from __future__ import annotations

import contextlib
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum
from typing import AsyncIterator

import numpy as np

from puripuly_heart.core.audio.format import AudioFrameF32, reshape_audio_samples_f32
from puripuly_heart.core.audio.source import AudioSource

_VIRTUAL_AUDIO_KEYWORDS = (
    "steam",
    "oculus",
    "meta",
    "quest",
    "virtual",
    "voicemeeter",
    "vb-cable",
    "sonar",
    "nvidia broadcast",
)


class AudioFaultProfile(StrEnum):
    NONE = "none"
    CAPTURE_SILENT_FIRST_CHANNEL = "capture_silent_first_channel"
    CAPTURE_ATTENUATE_40DB = "capture_attenuate_40db"
    CAPTURE_NEAR_SILENCE_NOISE = "capture_near_silence_noise"
    CAPTURE_BUFFER_DROPOUTS = "capture_buffer_dropouts"
    STT_INPUT_LOW_SNR_VAD_PASS = "stt_input_low_snr_vad_pass"


EXPECTED_FAULT_SIGNATURES = {
    "capture_silent_first_channel": (
        "Capture logs show one muted channel; VAD may miss speech after mono mixdown."
    ),
    "capture_attenuate_40db": (
        "Capture RMS and VAD-input RMS drop sharply; VAD may miss or local_qwen may decode "
        "garbage."
    ),
    "capture_near_silence_noise": (
        "Capture RMS is very low with near-silence noise; VAD should usually stay idle."
    ),
    "capture_buffer_dropouts": (
        "Capture logs alternate normal and zeroed chunks; VAD/STT chunk counts reveal dropout "
        "sensitivity."
    ),
    "stt_input_low_snr_vad_pass": (
        "VAD can start from real capture while STT-input and local_qwen logs show injected "
        "low-SNR audio."
    ),
}


@dataclass(frozen=True, slots=True)
class AudioFrameMetrics:
    samples: int
    audio_ms: float
    rms_db: float
    peak_db: float
    zero_ratio: float
    channel_rms_db: tuple[float, ...] = ()
    channel_peak_db: tuple[float, ...] = ()


def _safe_db(value: float) -> float:
    if value <= 0.0:
        return -120.0
    return round(float(20.0 * np.log10(max(value, 1e-6))), 1)


def compute_audio_frame_metrics(frame: AudioFrameF32) -> AudioFrameMetrics:
    samples = np.asarray(frame.samples, dtype=np.float32)
    if samples.size == 0:
        return AudioFrameMetrics(
            samples=0,
            audio_ms=0.0,
            rms_db=-120.0,
            peak_db=-120.0,
            zero_ratio=1.0,
        )

    reshaped = reshape_audio_samples_f32(samples, channels=frame.channels)
    sample_frames = int(reshaped.shape[0])
    audio_ms = (
        sample_frames * 1000.0 / float(frame.sample_rate_hz) if frame.sample_rate_hz > 0 else 0.0
    )
    rms = float(np.sqrt(np.mean(np.square(samples))))
    peak = float(np.max(np.abs(samples)))
    zero_ratio = float(np.mean(np.abs(samples) < 1e-6))

    channel_rms: list[float] = []
    channel_peak: list[float] = []
    channels = (
        (reshaped,)
        if reshaped.ndim == 1
        else tuple(reshaped[:, idx] for idx in range(reshaped.shape[1]))
    )
    for channel in channels:
        channel_rms.append(
            _safe_db(float(np.sqrt(np.mean(np.square(channel)))) if channel.size else 0.0)
        )
        channel_peak.append(_safe_db(float(np.max(np.abs(channel))) if channel.size else 0.0))

    return AudioFrameMetrics(
        samples=int(samples.size),
        audio_ms=audio_ms,
        rms_db=_safe_db(rms),
        peak_db=_safe_db(peak),
        zero_ratio=round(zero_ratio, 3),
        channel_rms_db=tuple(channel_rms),
        channel_peak_db=tuple(channel_peak),
    )


def _virtual_hint(name: str) -> bool:
    lowered = name.lower()
    return any(keyword in lowered for keyword in _VIRTUAL_AUDIO_KEYWORDS)


def format_sounddevice_snapshot_lines(*, hostapis: list[dict], devices: list[dict]) -> list[str]:
    lines = ["[AudioDiag][Snapshot][SoundDevice] environment"]
    for index, hostapi in enumerate(hostapis):
        lines.append(
            f"[AudioDiag][Snapshot][SoundDevice] hostapi index={index} "
            f"name={str(hostapi.get('name', ''))!r} "
            f"default_input={hostapi.get('default_input_device')} "
            f"default_output={hostapi.get('default_output_device')}"
        )
    for index, device in enumerate(devices):
        name = str(device.get("name", "") or "")
        lines.append(
            f"[AudioDiag][Snapshot][SoundDevice] device index={index} name={name!r} "
            f"hostapi={device.get('hostapi')} "
            f"max_input_channels={device.get('max_input_channels')} "
            f"max_output_channels={device.get('max_output_channels')} "
            f"default_samplerate={device.get('default_samplerate')} "
            f"virtual_hint={_virtual_hint(name)}"
        )
    return lines


def format_pyaudiowpatch_snapshot_lines(
    *, loopback_devices: list[dict], default_loopback: dict | None
) -> list[str]:
    lines = ["[AudioDiag][Snapshot][Loopback] environment"]
    if default_loopback is not None:
        lines.append(
            f"[AudioDiag][Snapshot][Loopback] default "
            f"name={str(default_loopback.get('name', ''))!r} "
            f"index={default_loopback.get('index')}"
        )
    for item in loopback_devices:
        name = str(item.get("name", "") or "")
        channels = item.get(
            "maxInputChannels",
            item.get(
                "max_input_channels", item.get("maxOutputChannels", item.get("max_output_channels"))
            ),
        )
        rate = item.get("defaultSampleRate", item.get("default_sample_rate"))
        lines.append(
            f"[AudioDiag][Snapshot][Loopback] device index={item.get('index')} "
            f"name={name!r} channels={channels} default_samplerate={rate} "
            f"virtual_hint={_virtual_hint(name)}"
        )
    return lines


def collect_sounddevice_snapshot_lines() -> list[str]:
    try:
        import sounddevice as sd

        return format_sounddevice_snapshot_lines(
            hostapis=list(sd.query_hostapis()), devices=list(sd.query_devices())
        )
    except Exception as exc:
        return [f"[AudioDiag][Snapshot][SoundDevice] query_failed error={exc}"]


def collect_pyaudiowpatch_snapshot_lines() -> list[str]:
    try:
        import pyaudiowpatch as pyaudio

        manager = pyaudio.PyAudio()
        try:
            devices = list(manager.get_loopback_device_info_generator())
            default_query_error: str | None = None
            try:
                default_loopback = manager.get_default_wasapi_loopback()
            except Exception as exc:
                default_loopback = None
                default_query_error = str(exc)
            lines = format_pyaudiowpatch_snapshot_lines(
                loopback_devices=devices, default_loopback=default_loopback
            )
            if default_query_error is not None:
                lines.insert(
                    1,
                    "[AudioDiag][Snapshot][Loopback] "
                    f"default_query_failed error={default_query_error}",
                )
            return lines
        finally:
            manager.terminate()
    except Exception as exc:
        return [f"[AudioDiag][Snapshot][Loopback] query_failed error={exc}"]


def normalize_audio_fault_profile(profile: AudioFaultProfile | str | None) -> AudioFaultProfile:
    if profile is None:
        return AudioFaultProfile.NONE
    return AudioFaultProfile(profile)


def apply_audio_fault_profile(
    frame: AudioFrameF32,
    profile: AudioFaultProfile | str | None,
    *,
    sequence_index: int = 0,
) -> AudioFrameF32:
    resolved = normalize_audio_fault_profile(profile)
    if resolved in (AudioFaultProfile.NONE, AudioFaultProfile.STT_INPUT_LOW_SNR_VAD_PASS):
        return frame

    samples = np.asarray(frame.samples, dtype=np.float32).copy()
    if resolved is AudioFaultProfile.CAPTURE_SILENT_FIRST_CHANNEL:
        reshaped = reshape_audio_samples_f32(samples, channels=frame.channels).copy()
        if reshaped.ndim == 2:
            reshaped[:, 0] = 0.0
            return AudioFrameF32(
                samples=reshaped, sample_rate_hz=frame.sample_rate_hz, channels=frame.channels
            )
        return AudioFrameF32(
            samples=np.zeros_like(samples),
            sample_rate_hz=frame.sample_rate_hz,
            channels=frame.channels,
        )

    if resolved is AudioFaultProfile.CAPTURE_ATTENUATE_40DB:
        return AudioFrameF32(
            samples=samples * np.float32(0.01),
            sample_rate_hz=frame.sample_rate_hz,
            channels=frame.channels,
        )

    if resolved is AudioFaultProfile.CAPTURE_NEAR_SILENCE_NOISE:
        flat = np.arange(samples.size, dtype=np.float32) + np.float32(sequence_index * 17)
        noise = np.sin(flat * np.float32(12.9898)) * np.float32(0.003)
        return AudioFrameF32(
            samples=noise.reshape(samples.shape).astype(np.float32),
            sample_rate_hz=frame.sample_rate_hz,
            channels=frame.channels,
        )

    if resolved is AudioFaultProfile.CAPTURE_BUFFER_DROPOUTS:
        if sequence_index % 2 == 1:
            samples.fill(0.0)
        return AudioFrameF32(
            samples=samples, sample_rate_hz=frame.sample_rate_hz, channels=frame.channels
        )

    raise AssertionError(f"Unhandled audio fault profile: {resolved}")


@dataclass(slots=True)
class DiagnosticAudioSource(AudioSource):
    source: AudioSource
    channel_label: str
    is_detailed_enabled: Callable[[], bool]
    log_detailed: Callable[[str], object] | None = None
    fault_profile: AudioFaultProfile | str = AudioFaultProfile.NONE
    fault_profile_provider: Callable[[], AudioFaultProfile | str | None] | None = None
    summary_interval_audio_ms: int = 1000
    extra_fields_provider: Callable[[], dict[str, object]] | None = None
    # Always-on plumbing health (r313): one open banner + a pace line every
    # 60s at BASIC level, so a plain log answers "which device, and is the
    # audio clock sane" without the user enabling detailed diagnostics. A
    # pace ratio far from 1.0 means the device delivers a different sample
    # rate than it claims (pitch-shifted capture -> STT junk).
    log_basic: Callable[[str], object] | None = None
    pace_interval_s: float = 60.0
    # Consumer-starvation recovery (2026-08-11): a wedged native STT decode
    # blocked the peer pipeline for 6 minutes; the pace ratio collapsed to
    # 0.01 and never came back on its own — the capture queue was full of
    # stale audio nobody would ever catch up on. When the ratio stays under
    # pace_collapse_ratio for pace_collapse_reports consecutive reports (and
    # the capture queue is actually dropping frames, see
    # _maybe_report_pace_collapse), on_pace_collapse fires so the owner can
    # restart the capture session.
    on_pace_collapse: Callable[[float], object] | None = None
    pace_collapse_ratio: float = 0.2
    pace_collapse_reports: int = 2
    _accumulated_audio_ms: float = field(init=False, default=0.0)
    _sequence_index: int = field(init=False, default=0)
    _open_banner_logged: bool = field(init=False, default=False)
    _pace_audio_s: float = field(init=False, default=0.0)
    _pace_wall_start_s: float = field(init=False, default=0.0)
    _pace_low_streak: int = field(init=False, default=0)
    _pace_last_queue_drops: int | None = field(init=False, default=None)

    def _current_fault_profile(self) -> AudioFaultProfile:
        if self.fault_profile_provider is not None:
            return normalize_audio_fault_profile(self.fault_profile_provider())
        return normalize_audio_fault_profile(self.fault_profile)

    def _extra_fields_safe(self) -> dict[str, object]:
        with contextlib.suppress(Exception):
            if self.extra_fields_provider is not None:
                return dict(self.extra_fields_provider())
        return {}

    def _log_basic_safe(self, message: str) -> None:
        if self.log_basic is None:
            return
        with contextlib.suppress(Exception):
            self.log_basic(message)

    def _maybe_log_open_banner(self) -> None:
        if self._open_banner_logged:
            return
        self._open_banner_logged = True
        fields = self._extra_fields_safe()
        self._log_basic_safe(
            f"[Audio][{self.channel_label}] Capture opened: "
            f"device={fields.get('resolved_device_name')!r} "
            f"idx={fields.get('resolved_device_index')} "
            f"ch={fields.get('resolved_channels')} "
            f"rate={fields.get('actual_sample_rate_hz')} "
            f"default_fallback={fields.get('used_default_fallback')}"
        )

    def _track_pace(self, frame: AudioFrameF32) -> None:
        with contextlib.suppress(Exception):
            channels = max(1, int(getattr(frame, "channels", 1) or 1))
            rate = max(1, int(frame.sample_rate_hz))
            self._pace_audio_s += frame.samples.size / (rate * channels)
            now = time.monotonic()
            if self._pace_wall_start_s == 0.0:
                self._pace_wall_start_s = now
                return
            wall = now - self._pace_wall_start_s
            if wall < self.pace_interval_s:
                return
            ratio = self._pace_audio_s / wall if wall > 0 else 0.0
            fields = self._extra_fields_safe()
            pace_line = (
                f"[Audio][{self.channel_label}] Pace: audio_s={self._pace_audio_s:.1f} "
                f"wall_s={wall:.1f} ratio={ratio:.2f} "
                f"queue_drops={fields.get('queue_drops')} "
                f"callback_statuses={fields.get('callback_statuses')}"
            )
            if fields.get("queue_depth") is not None:
                # r629: backlog gauge — how far behind real time the consumer is
                pace_line += f" queue_depth={fields['queue_depth']}"
            self._log_basic_safe(pace_line)
            self._maybe_report_pace_collapse(ratio, fields)
            self._pace_audio_s = 0.0
            self._pace_wall_start_s = now

    def _maybe_report_pace_collapse(self, ratio: float, fields: dict[str, object]) -> None:
        """Fire on_pace_collapse when the pipeline is consuming far less audio
        than the device delivers, sustained across consecutive pace reports.

        A collapsed ratio alone is not proof: pace reports only fire when
        frames arrive, so a long quiet spell (nothing playing) makes the first
        report after it look collapsed too — wall time accrued while no audio
        existed to consume. Real consumer starvation necessarily overflows the
        capture queue, so when the queue_drops counter is available the streak
        only counts intervals where it GREW. A quiet-spell interval neither
        confirms nor refutes starvation, so it holds the streak; only a
        healthy ratio resets it.
        """
        if self.on_pace_collapse is None:
            return
        if ratio >= self.pace_collapse_ratio:
            self._pace_low_streak = 0
            return
        drops: int | None = None
        with contextlib.suppress(TypeError, ValueError):
            raw = fields.get("queue_drops")
            if raw is not None:
                drops = int(raw)  # type: ignore[arg-type]
        if drops is not None:
            previous = self._pace_last_queue_drops
            self._pace_last_queue_drops = drops
            # `previous is None`: no baseline yet. `drops < previous`: the
            # counter reset (capture was reopened). Neither confirms drops.
            if previous is None or drops <= previous:
                return
        self._pace_low_streak += 1
        if self._pace_low_streak < max(1, int(self.pace_collapse_reports)):
            return
        self._pace_low_streak = 0
        self._log_basic_safe(
            f"[Audio][{self.channel_label}] Pace collapsed: ratio={ratio:.2f} "
            f"(< {self.pace_collapse_ratio:.2f}) across "
            f"{max(1, int(self.pace_collapse_reports))} consecutive reports with "
            "growing queue drops — requesting capture restart"
        )
        with contextlib.suppress(Exception):
            self.on_pace_collapse(ratio)

    async def frames(self) -> AsyncIterator[AudioFrameF32]:
        async for frame in self.source.frames():
            self._maybe_log_open_banner()
            self._track_pace(frame)
            profile = self._safe_current_fault_profile()
            detailed_enabled = self._safe_detailed_enabled()
            if profile is AudioFaultProfile.NONE and not detailed_enabled:
                yield frame
                continue

            output = self._safe_apply_audio_fault_profile(
                frame,
                profile,
                sequence_index=self._sequence_index,
            )
            self._sequence_index += 1
            if not detailed_enabled:
                yield output
                continue

            self._safe_maybe_log_capture_diagnostics(output=output, profile=profile)
            yield output

    def _safe_current_fault_profile(self) -> AudioFaultProfile:
        with contextlib.suppress(Exception):
            return self._current_fault_profile()
        return AudioFaultProfile.NONE

    def _safe_detailed_enabled(self) -> bool:
        with contextlib.suppress(Exception):
            return bool(self.is_detailed_enabled())
        return False

    def _safe_apply_audio_fault_profile(
        self,
        frame: AudioFrameF32,
        profile: AudioFaultProfile,
        *,
        sequence_index: int,
    ) -> AudioFrameF32:
        with contextlib.suppress(Exception):
            return apply_audio_fault_profile(frame, profile, sequence_index=sequence_index)
        return frame

    def _safe_extra_fields(self) -> str:
        if self.extra_fields_provider is None:
            return ""
        with contextlib.suppress(Exception):
            fields = self.extra_fields_provider()
            return " " + " ".join(f"{key}={value}" for key, value in sorted(fields.items()))
        return ""

    def _safe_maybe_log_capture_diagnostics(
        self,
        *,
        output: AudioFrameF32,
        profile: AudioFaultProfile,
    ) -> None:
        with contextlib.suppress(Exception):
            metrics = compute_audio_frame_metrics(output)
            self._accumulated_audio_ms += metrics.audio_ms
            if (
                self.log_detailed is None
                or self._accumulated_audio_ms < self.summary_interval_audio_ms
            ):
                return
            self._accumulated_audio_ms = 0.0
            extra = self._safe_extra_fields()
            self.log_detailed(
                f"[AudioDiag][Capture][{self.channel_label}] "
                f"rate_hz={output.sample_rate_hz} channels={output.channels} "
                f"fault_profile={profile.value} samples={metrics.samples} "
                f"audio_ms={metrics.audio_ms:.1f} rms_db={metrics.rms_db:.1f} "
                f"peak_db={metrics.peak_db:.1f} zero_ratio={metrics.zero_ratio:.3f} "
                f"channel_rms_db={metrics.channel_rms_db} "
                f"channel_peak_db={metrics.channel_peak_db}{extra}"
            )

    async def close(self) -> None:
        await self.source.close()
