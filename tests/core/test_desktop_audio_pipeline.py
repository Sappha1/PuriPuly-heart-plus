from __future__ import annotations

import numpy as np
import pytest

import puripuly_heart.core.audio.desktop_pipeline as desktop_pipeline_module
from puripuly_heart.core.audio.desktop_pipeline import DesktopPeerPipeline
from puripuly_heart.core.audio.format import AudioFrameF32


class StubDesktopAudioSource:
    def __init__(self, frames):
        self._frames = frames
        self.closed = False

    async def frames(self):
        for frame in self._frames:
            yield frame

    async def close(self) -> None:
        self.closed = True


@pytest.mark.asyncio
async def test_desktop_pipeline_outputs_16khz_vad_ready_frames():
    source = StubDesktopAudioSource(
        frames=[
            AudioFrameF32(
                sample_rate_hz=48000,
                samples=np.ones(4800, dtype=np.float32),
            )
        ]
    )
    pipeline = DesktopPeerPipeline(source=source)

    frames = [frame async for frame in pipeline.frames()]
    combined = np.concatenate([frame.samples for frame in frames])
    combined_pcm = b"".join(frame.deepgram_pcm16le for frame in frames)

    assert len(frames) >= 1
    assert all(frame.sample_rate_hz == 16000 for frame in frames)
    assert all(frame.samples.dtype == np.float32 for frame in frames)
    assert all(frame.samples.ndim == 1 for frame in frames)
    assert combined.shape == (1600,)
    assert len(combined_pcm) == 3200


@pytest.mark.asyncio
async def test_desktop_pipeline_downmixes_interleaved_multichannel_frames():
    source = StubDesktopAudioSource(
        frames=[
            AudioFrameF32(
                sample_rate_hz=16000,
                channels=2,
                samples=np.array([0.0, 1.0, 1.0, 0.0], dtype=np.float32),
            )
        ]
    )
    pipeline = DesktopPeerPipeline(source=source)

    frame = await pipeline.frames().__anext__()

    assert frame.sample_rate_hz == 16000
    assert np.allclose(frame.samples, np.array([0.5, 0.5], dtype=np.float32))


@pytest.mark.asyncio
async def test_desktop_pipeline_logs_post_resample_diagnostics_when_detailed() -> None:
    log_lines: list[str] = []
    source = StubDesktopAudioSource(
        frames=[
            AudioFrameF32(
                sample_rate_hz=16000,
                channels=1,
                samples=np.ones(16000, dtype=np.float32),
            )
        ]
    )
    pipeline = DesktopPeerPipeline(
        source=source,
        target_sample_rate_hz=16000,
        is_detailed_enabled=lambda: True,
        log_detailed=log_lines.append,
    )

    frames = [frame async for frame in pipeline.frames()]

    assert len(frames) == 1
    assert any("[AudioDiag][PeerPipeline]" in line for line in log_lines)
    assert any("source_rate=16000" in line and "target_rate=16000" in line for line in log_lines)


@pytest.mark.asyncio
async def test_desktop_pipeline_skips_metrics_when_not_detailed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        desktop_pipeline_module,
        "compute_audio_frame_metrics",
        lambda _frame: (_ for _ in ()).throw(
            AssertionError("Basic mode must not compute peer pipeline metrics")
        ),
    )
    source = StubDesktopAudioSource(
        frames=[
            AudioFrameF32(
                sample_rate_hz=16000,
                channels=1,
                samples=np.ones(16000, dtype=np.float32),
            )
        ]
    )
    pipeline = DesktopPeerPipeline(
        source=source,
        target_sample_rate_hz=16000,
        is_detailed_enabled=lambda: False,
        log_detailed=lambda _message: (_ for _ in ()).throw(
            AssertionError("Basic mode must not log peer AudioDiag")
        ),
    )

    frames = [frame async for frame in pipeline.frames()]

    assert len(frames) == 1


@pytest.mark.asyncio
async def test_desktop_pipeline_detailed_predicate_failure_still_yields_frames() -> None:
    def fail_detailed_enabled():
        raise RuntimeError("detailed predicate failed")

    source = StubDesktopAudioSource(
        frames=[
            AudioFrameF32(
                sample_rate_hz=16000,
                channels=1,
                samples=np.ones(16000, dtype=np.float32),
            )
        ]
    )
    pipeline = DesktopPeerPipeline(
        source=source,
        target_sample_rate_hz=16000,
        is_detailed_enabled=fail_detailed_enabled,
        log_detailed=lambda _message: (_ for _ in ()).throw(
            AssertionError("failed detailed predicate must not log")
        ),
    )

    frames = [frame async for frame in pipeline.frames()]

    assert len(frames) == 1
    np.testing.assert_allclose(frames[0].samples, np.ones(16000, dtype=np.float32))


@pytest.mark.asyncio
async def test_desktop_pipeline_metric_failure_still_yields_frames(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        desktop_pipeline_module,
        "compute_audio_frame_metrics",
        lambda _frame: (_ for _ in ()).throw(RuntimeError("peer metrics failed")),
    )
    source = StubDesktopAudioSource(
        frames=[
            AudioFrameF32(
                sample_rate_hz=16000,
                channels=1,
                samples=np.ones(16000, dtype=np.float32),
            )
        ]
    )
    pipeline = DesktopPeerPipeline(
        source=source,
        target_sample_rate_hz=16000,
        is_detailed_enabled=lambda: True,
        log_detailed=lambda _message: None,
    )

    frames = [frame async for frame in pipeline.frames()]

    assert len(frames) == 1
    np.testing.assert_allclose(frames[0].samples, np.ones(16000, dtype=np.float32))


@pytest.mark.asyncio
async def test_desktop_pipeline_log_failure_still_yields_frames() -> None:
    def fail_log(_message: str) -> None:
        raise RuntimeError("peer diagnostic log failed")

    source = StubDesktopAudioSource(
        frames=[
            AudioFrameF32(
                sample_rate_hz=16000,
                channels=1,
                samples=np.ones(16000, dtype=np.float32),
            )
        ]
    )
    pipeline = DesktopPeerPipeline(
        source=source,
        target_sample_rate_hz=16000,
        is_detailed_enabled=lambda: True,
        log_detailed=fail_log,
    )

    frames = [frame async for frame in pipeline.frames()]

    assert len(frames) == 1
    np.testing.assert_allclose(frames[0].samples, np.ones(16000, dtype=np.float32))


@pytest.mark.asyncio
async def test_desktop_pipeline_close_closes_underlying_source():
    source = StubDesktopAudioSource(frames=[])
    pipeline = DesktopPeerPipeline(source=source)

    await pipeline.close()

    assert source.closed is True
