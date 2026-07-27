from __future__ import annotations

import contextlib
import logging
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field

import numpy as np

from puripuly_heart.core.audio.diagnostics import compute_audio_frame_metrics
from puripuly_heart.core.audio.format import AudioFrameF32, float32_to_pcm16le_bytes
from puripuly_heart.core.audio.source import AudioSource
from puripuly_heart.core.audio.streaming_resampler import MonoFirstStreamingResampler

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class DesktopPeerAudioFrame:
    samples: np.ndarray
    sample_rate_hz: int
    deepgram_pcm16le: bytes
    channels: int = 1


# Auto-gain: boost quiet loopback audio to a stable level before VAD/STT.
# Loopback captures POST-Windows-volume, so a user listening at 15% volume
# feeds the detector near-silence and songs fragment into one-word segments.
# Boost-only (never attenuates), capped, slewed, and silence-guarded.
_AGC_TARGET_RMS = 0.05          # ~ -26 dBFS
_AGC_MAX_GAIN = 6.0             # ~ +15.5 dB cap
_AGC_SILENCE_RMS = 0.0015       # below this: hold gain, don't chase noise
_AGC_EMA_ALPHA = 0.10           # smoothing for the level estimate
_AGC_SLEW = 0.25                # max relative gain change per chunk


@dataclass(slots=True)
class DesktopPeerPipeline:
    source: AudioSource
    target_sample_rate_hz: int = 16000
    auto_gain: bool = True
    is_detailed_enabled: Callable[[], bool] | None = None
    log_detailed: Callable[[str], object] | None = None
    _logged_formats: set[tuple[int, int]] = field(default_factory=set, init=False, repr=False)
    _diag_accumulated_audio_ms: float = field(default=0.0, init=False, repr=False)
    _agc_rms_ema: float = field(default=0.0, init=False, repr=False)
    _agc_gain: float = field(default=1.0, init=False, repr=False)

    async def frames(self) -> AsyncIterator[DesktopPeerAudioFrame]:
        resampler: MonoFirstStreamingResampler | None = None
        source_format: tuple[int, int] | None = None

        async for frame in self.source.frames():
            format_key = (frame.sample_rate_hz, frame.channels)
            if format_key not in self._logged_formats:
                self._logged_formats.add(format_key)
                logger.info(
                    "Desktop peer audio format: source_rate=%sHz source_channels=%s -> target_rate=%sHz",
                    frame.sample_rate_hz,
                    frame.channels,
                    self.target_sample_rate_hz,
                )

            frame_format = (frame.sample_rate_hz, frame.channels)
            if source_format is not None and frame_format != source_format:
                # The watchdog can reopen capture on a different device
                # (unplug/replug, default switch) whose rate/channels differ —
                # rebuild the resampler instead of killing the pipeline.
                logger.info(
                    "Desktop peer audio format changed: %sHz/%sch -> %sHz/%sch; "
                    "rebuilding resampler",
                    source_format[0],
                    source_format[1],
                    frame.sample_rate_hz,
                    frame.channels,
                )
                assert resampler is not None
                tail = resampler.flush()
                if tail.size:
                    yield self._build_output_frame(tail.reshape(-1))
                source_format = None
                resampler = None
            if source_format is None:
                source_format = frame_format
                resampler = MonoFirstStreamingResampler(
                    input_sample_rate_hz=frame.sample_rate_hz,
                    output_sample_rate_hz=self.target_sample_rate_hz,
                    input_channels=frame.channels,
                )

            assert resampler is not None
            normalized = resampler.resample_chunk(frame.samples)
            self._maybe_log_peer_diagnostics(
                source_rate=frame.sample_rate_hz,
                source_channels=frame.channels,
                normalized=normalized,
            )
            if normalized.size:
                chunk = normalized.reshape(-1)
                if self.auto_gain:
                    chunk = self._apply_auto_gain(chunk)
                yield self._build_output_frame(chunk)

        if resampler is None:
            return

        tail = resampler.flush()
        if tail.size:
            yield self._build_output_frame(tail.reshape(-1))

    def _apply_auto_gain(self, chunk: np.ndarray) -> np.ndarray:
        rms = float(np.sqrt(np.mean(np.square(chunk), dtype=np.float64)))
        if rms < _AGC_SILENCE_RMS:
            # Near-silence: keep the current gain, don't amplify the noise floor.
            if self._agc_gain != 1.0:
                return np.clip(chunk * self._agc_gain, -1.0, 1.0).astype(np.float32)
            return chunk
        ema = self._agc_rms_ema
        ema = rms if ema <= 0.0 else (1.0 - _AGC_EMA_ALPHA) * ema + _AGC_EMA_ALPHA * rms
        self._agc_rms_ema = ema
        desired = min(_AGC_MAX_GAIN, max(1.0, _AGC_TARGET_RMS / max(ema, 1e-6)))
        gain = self._agc_gain
        low, high = gain * (1.0 - _AGC_SLEW), gain * (1.0 + _AGC_SLEW)
        gain = min(max(desired, low), high)
        self._agc_gain = min(max(gain, 1.0), _AGC_MAX_GAIN)
        if self._agc_gain == 1.0:
            return chunk
        return np.clip(chunk * self._agc_gain, -1.0, 1.0).astype(np.float32)

    async def close(self) -> None:
        await self.source.close()

    def _maybe_log_peer_diagnostics(
        self,
        *,
        source_rate: int,
        source_channels: int,
        normalized: np.ndarray,
    ) -> None:
        if self.is_detailed_enabled is None or self.log_detailed is None:
            return
        detailed_enabled = False
        with contextlib.suppress(Exception):
            detailed_enabled = bool(self.is_detailed_enabled())
        if not detailed_enabled:
            return

        with contextlib.suppress(Exception):
            frame = AudioFrameF32(
                samples=normalized.reshape(-1),
                sample_rate_hz=self.target_sample_rate_hz,
                channels=1,
            )
            metrics = compute_audio_frame_metrics(frame)
            self._diag_accumulated_audio_ms += metrics.audio_ms
            if self._diag_accumulated_audio_ms < 1000.0:
                return

            self._diag_accumulated_audio_ms = 0.0
            self.log_detailed(
                f"[AudioDiag][PeerPipeline] source_rate={source_rate} "
                f"source_channels={source_channels} target_rate={self.target_sample_rate_hz} "
                f"samples={metrics.samples} audio_ms={metrics.audio_ms:.1f} "
                f"rms_db={metrics.rms_db:.1f} peak_db={metrics.peak_db:.1f} "
                f"zero_ratio={metrics.zero_ratio:.3f}"
            )

    def _build_output_frame(self, samples: np.ndarray) -> DesktopPeerAudioFrame:
        return DesktopPeerAudioFrame(
            samples=samples,
            sample_rate_hz=self.target_sample_rate_hz,
            channels=1,
            deepgram_pcm16le=float32_to_pcm16le_bytes(samples),
        )
