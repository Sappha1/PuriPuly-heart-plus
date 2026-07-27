"""Peer loopback auto-gain (r299)."""
from __future__ import annotations

import numpy as np
import pytest

from puripuly_heart.core.audio.desktop_pipeline import DesktopPeerPipeline
from puripuly_heart.core.audio.format import AudioFrameF32


class _ChunkSource:
    def __init__(self, chunks):
        self._chunks = chunks

    async def frames(self):
        for c in self._chunks:
            yield AudioFrameF32(samples=c, sample_rate_hz=16000, channels=1)

    async def close(self):
        pass


def _rms(a: np.ndarray) -> float:
    return float(np.sqrt((a.astype(np.float64) ** 2).mean()) + 1e-12)


async def _run(chunks, auto_gain=True):
    pipe = DesktopPeerPipeline(source=_ChunkSource(chunks), auto_gain=auto_gain)
    out = []
    async for frame in pipe.frames():
        out.append(frame.samples)
    return np.concatenate(out) if out else np.zeros(0, dtype=np.float32)


@pytest.mark.asyncio
async def test_quiet_audio_boosted_toward_target() -> None:
    t = np.arange(16000) / 16000
    quiet = (0.005 * np.sin(2 * np.pi * 300 * t)).astype(np.float32)  # ~ -49 dBFS
    chunks = [quiet.copy() for _ in range(12)]
    out = await _run(chunks, auto_gain=True)
    # The tail chunks should be clearly louder than the input (slewed ramp-up).
    tail = out[-16000 * 3:]
    assert _rms(tail) > _rms(quiet) * 3.0


@pytest.mark.asyncio
async def test_loud_audio_untouched() -> None:
    t = np.arange(16000) / 16000
    loud = (0.3 * np.sin(2 * np.pi * 300 * t)).astype(np.float32)
    chunks = [loud.copy() for _ in range(4)]
    out = await _run(chunks, auto_gain=True)
    assert abs(_rms(out) - _rms(loud)) / _rms(loud) < 0.05  # gain stays 1.0


@pytest.mark.asyncio
async def test_silence_not_amplified_from_cold_start() -> None:
    silence = np.zeros(16000, dtype=np.float32)
    out = await _run([silence.copy() for _ in range(4)], auto_gain=True)
    assert _rms(out) < 1e-6


@pytest.mark.asyncio
async def test_disabled_flag_passthrough() -> None:
    t = np.arange(16000) / 16000
    quiet = (0.005 * np.sin(2 * np.pi * 300 * t)).astype(np.float32)
    out = await _run([quiet.copy() for _ in range(6)], auto_gain=False)
    assert abs(_rms(out) - _rms(quiet)) / _rms(quiet) < 0.05
