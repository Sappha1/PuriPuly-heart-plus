from __future__ import annotations

import sys
from types import SimpleNamespace

import numpy as np
import pytest

import puripuly_heart.core.audio.diagnostics as diagnostics_module
from puripuly_heart.core.audio.diagnostics import (
    EXPECTED_FAULT_SIGNATURES,
    AudioFaultProfile,
    AudioFrameMetrics,
    DiagnosticAudioSource,
    apply_audio_fault_profile,
    collect_pyaudiowpatch_snapshot_lines,
    compute_audio_frame_metrics,
    format_pyaudiowpatch_snapshot_lines,
    format_sounddevice_snapshot_lines,
)
from puripuly_heart.core.audio.format import AudioFrameF32


def test_compute_audio_frame_metrics_reports_channel_values() -> None:
    frame = AudioFrameF32(
        samples=np.array([[0.0, 1.0], [0.0, -1.0]], dtype=np.float32),
        sample_rate_hz=48000,
        channels=2,
    )

    metrics = compute_audio_frame_metrics(frame)

    assert isinstance(metrics, AudioFrameMetrics)
    assert metrics.samples == 4
    assert round(metrics.audio_ms, 3) == 0.042
    assert metrics.peak_db == 0.0
    assert metrics.channel_rms_db[0] <= -119.0
    assert metrics.channel_rms_db[1] == 0.0


def test_snapshot_formatters_mark_virtual_audio_devices() -> None:
    sounddevice_lines = format_sounddevice_snapshot_lines(
        hostapis=[
            {"name": "Windows WASAPI", "default_input_device": 2, "default_output_device": 5}
        ],
        devices=[
            {
                "name": "Steam Streaming Microphone",
                "hostapi": 0,
                "max_input_channels": 2,
                "max_output_channels": 0,
                "default_samplerate": 48000.0,
            },
            {
                "name": "Realtek Speakers",
                "hostapi": 0,
                "max_input_channels": 0,
                "max_output_channels": 2,
                "default_samplerate": 48000.0,
            },
        ],
    )
    loopback_lines = format_pyaudiowpatch_snapshot_lines(
        loopback_devices=[
            {
                "index": 10,
                "name": "Steam Streaming Speakers [Loopback]",
                "maxInputChannels": 2,
                "defaultSampleRate": 48000.0,
            }
        ],
        default_loopback={
            "index": 10,
            "name": "Steam Streaming Speakers [Loopback]",
            "maxInputChannels": 2,
            "defaultSampleRate": 48000.0,
        },
    )

    assert any("hostapi index=0 name='Windows WASAPI'" in line for line in sounddevice_lines)
    assert any(
        "Steam Streaming Microphone" in line and "virtual_hint=True" in line
        for line in sounddevice_lines
    )
    assert any("[AudioDiag][Snapshot][Loopback] default" in line for line in loopback_lines)
    assert any(
        "Steam Streaming Speakers" in line and "virtual_hint=True" in line
        for line in loopback_lines
    )


def test_pyaudiowpatch_snapshot_collector_reports_default_query_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakePyAudioManager:
        def get_loopback_device_info_generator(self):
            yield {
                "index": 10,
                "name": "Steam Streaming Speakers [Loopback]",
                "maxInputChannels": 2,
                "defaultSampleRate": 48000.0,
            }

        def get_default_wasapi_loopback(self):
            raise RuntimeError("default loopback unavailable")

        def terminate(self) -> None:
            return None

    fake_pyaudio = SimpleNamespace(PyAudio=FakePyAudioManager)
    monkeypatch.setitem(sys.modules, "pyaudiowpatch", fake_pyaudio)

    lines = collect_pyaudiowpatch_snapshot_lines()

    assert any(
        "[AudioDiag][Snapshot][Loopback] default_query_failed" in line
        and "default loopback unavailable" in line
        for line in lines
    )
    assert any("Steam Streaming Speakers" in line and "virtual_hint=True" in line for line in lines)


def test_apply_audio_fault_profiles_are_deterministic() -> None:
    frame = AudioFrameF32(
        samples=np.array([[1.0, 0.5], [-1.0, -0.5]], dtype=np.float32),
        sample_rate_hz=16000,
        channels=2,
    )

    muted = apply_audio_fault_profile(frame, AudioFaultProfile.CAPTURE_SILENT_FIRST_CHANNEL)
    attenuated = apply_audio_fault_profile(frame, AudioFaultProfile.CAPTURE_ATTENUATE_40DB)
    noisy = apply_audio_fault_profile(
        frame, AudioFaultProfile.CAPTURE_NEAR_SILENCE_NOISE, sequence_index=3
    )
    noisy_again = apply_audio_fault_profile(
        frame, AudioFaultProfile.CAPTURE_NEAR_SILENCE_NOISE, sequence_index=3
    )
    noisy_other = apply_audio_fault_profile(
        frame, AudioFaultProfile.CAPTURE_NEAR_SILENCE_NOISE, sequence_index=4
    )
    dropped = apply_audio_fault_profile(
        frame, AudioFaultProfile.CAPTURE_BUFFER_DROPOUTS, sequence_index=1
    )

    np.testing.assert_allclose(muted.samples[:, 0], np.array([0.0, 0.0], dtype=np.float32))
    np.testing.assert_allclose(muted.samples[:, 1], np.array([0.5, -0.5], dtype=np.float32))
    np.testing.assert_allclose(attenuated.samples, frame.samples * np.float32(0.01))
    assert float(np.max(np.abs(noisy.samples))) <= 0.004
    np.testing.assert_allclose(noisy.samples, noisy_again.samples)
    assert not np.array_equal(noisy.samples, noisy_other.samples)
    np.testing.assert_allclose(dropped.samples, np.zeros_like(frame.samples))
    assert "stt_input_low_snr_vad_pass" in EXPECTED_FAULT_SIGNATURES


class StubAudioSource:
    def __init__(self, frames: list[AudioFrameF32]) -> None:
        self._frames = frames
        self.closed = False

    async def frames(self):
        for frame in self._frames:
            yield frame

    async def close(self) -> None:
        self.closed = True


@pytest.mark.asyncio
async def test_diagnostic_audio_source_skips_metrics_when_not_detailed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    log_lines: list[str] = []
    frame = AudioFrameF32(
        samples=np.ones(16000, dtype=np.float32), sample_rate_hz=16000, channels=1
    )
    source = StubAudioSource([frame])
    monkeypatch.setattr(
        diagnostics_module,
        "compute_audio_frame_metrics",
        lambda _frame: (_ for _ in ()).throw(AssertionError("Basic mode must not compute metrics")),
    )
    wrapper = DiagnosticAudioSource(
        source=source,
        channel_label="self",
        is_detailed_enabled=lambda: False,
        log_detailed=log_lines.append,
    )

    frames = [frame async for frame in wrapper.frames()]

    assert len(frames) == 1
    assert frames[0] is frame
    assert log_lines == []


@pytest.mark.asyncio
async def test_diagnostic_audio_source_logs_rate_limited_metrics_when_detailed() -> None:
    log_lines: list[str] = []
    source = StubAudioSource(
        [
            AudioFrameF32(
                samples=np.ones(8000, dtype=np.float32), sample_rate_hz=16000, channels=1
            ),
            AudioFrameF32(
                samples=np.ones(8000, dtype=np.float32), sample_rate_hz=16000, channels=1
            ),
        ]
    )
    wrapper = DiagnosticAudioSource(
        source=source,
        channel_label="peer",
        is_detailed_enabled=lambda: True,
        log_detailed=log_lines.append,
        fault_profile_provider=lambda: AudioFaultProfile.CAPTURE_ATTENUATE_40DB,
        summary_interval_audio_ms=1000,
        extra_fields_provider=lambda: {"queue_drops": 2, "callback_statuses": 1},
    )

    frames = [frame async for frame in wrapper.frames()]

    assert len(frames) == 2
    assert np.allclose(frames[0].samples, np.full(8000, 0.01, dtype=np.float32))
    assert any("[AudioDiag][Capture][peer]" in line for line in log_lines)
    assert any("fault_profile=capture_attenuate_40db" in line for line in log_lines)
    assert any("queue_drops=2" in line and "callback_statuses=1" in line for line in log_lines)


@pytest.mark.asyncio
async def test_diagnostic_audio_source_metric_failure_still_yields_faulted_frame(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        diagnostics_module,
        "compute_audio_frame_metrics",
        lambda _frame: (_ for _ in ()).throw(RuntimeError("diagnostic metrics failed")),
    )
    frame = AudioFrameF32(
        samples=np.ones(16000, dtype=np.float32), sample_rate_hz=16000, channels=1
    )
    wrapper = DiagnosticAudioSource(
        source=StubAudioSource([frame]),
        channel_label="self",
        is_detailed_enabled=lambda: True,
        log_detailed=lambda _message: None,
        fault_profile=AudioFaultProfile.CAPTURE_ATTENUATE_40DB,
    )

    frames = [frame async for frame in wrapper.frames()]

    assert len(frames) == 1
    np.testing.assert_allclose(frames[0].samples, np.full(16000, 0.01, dtype=np.float32))


@pytest.mark.asyncio
async def test_diagnostic_audio_source_fault_provider_failure_yields_original_frame() -> None:
    frame = AudioFrameF32(
        samples=np.ones(16000, dtype=np.float32), sample_rate_hz=16000, channels=1
    )

    def fail_fault_profile():
        raise RuntimeError("fault profile unavailable")

    wrapper = DiagnosticAudioSource(
        source=StubAudioSource([frame]),
        channel_label="self",
        is_detailed_enabled=lambda: False,
        fault_profile_provider=fail_fault_profile,
    )

    frames = [frame async for frame in wrapper.frames()]

    assert len(frames) == 1
    assert frames[0] is frame


@pytest.mark.asyncio
async def test_diagnostic_audio_source_detailed_predicate_failure_yields_original_frame() -> None:
    frame = AudioFrameF32(
        samples=np.ones(16000, dtype=np.float32), sample_rate_hz=16000, channels=1
    )

    def fail_detailed_enabled():
        raise RuntimeError("detailed predicate failed")

    wrapper = DiagnosticAudioSource(
        source=StubAudioSource([frame]),
        channel_label="self",
        is_detailed_enabled=fail_detailed_enabled,
        log_detailed=lambda _message: (_ for _ in ()).throw(
            AssertionError("failed detailed predicate must not log")
        ),
    )

    frames = [frame async for frame in wrapper.frames()]

    assert len(frames) == 1
    assert frames[0] is frame


@pytest.mark.asyncio
async def test_diagnostic_audio_source_extra_field_failure_does_not_prevent_yield() -> None:
    log_lines: list[str] = []
    frame = AudioFrameF32(
        samples=np.ones(16000, dtype=np.float32), sample_rate_hz=16000, channels=1
    )

    def fail_extra_fields():
        raise RuntimeError("extra fields unavailable")

    wrapper = DiagnosticAudioSource(
        source=StubAudioSource([frame]),
        channel_label="self",
        is_detailed_enabled=lambda: True,
        log_detailed=log_lines.append,
        summary_interval_audio_ms=1000,
        extra_fields_provider=fail_extra_fields,
    )

    frames = [frame async for frame in wrapper.frames()]

    assert len(frames) == 1
    assert frames[0] is frame
    assert any("[AudioDiag][Capture][self]" in line for line in log_lines)


@pytest.mark.asyncio
async def test_diagnostic_audio_source_log_failure_does_not_prevent_yield() -> None:
    frame = AudioFrameF32(
        samples=np.ones(16000, dtype=np.float32), sample_rate_hz=16000, channels=1
    )

    def fail_log(_message: str) -> None:
        raise RuntimeError("diagnostic log unavailable")

    wrapper = DiagnosticAudioSource(
        source=StubAudioSource([frame]),
        channel_label="self",
        is_detailed_enabled=lambda: True,
        log_detailed=fail_log,
        summary_interval_audio_ms=1000,
    )

    frames = [frame async for frame in wrapper.frames()]

    assert len(frames) == 1
    assert frames[0] is frame
