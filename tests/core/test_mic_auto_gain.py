"""Mic auto-gain + quiet-segment hallucination rejection (r305)."""
from __future__ import annotations

import numpy as np
import pytest

from puripuly_heart.core.audio.auto_gain import AutoGainAudioSource, AutoGainState
from puripuly_heart.core.audio.format import AudioFrameF32


class _Src:
    def __init__(self, chunks):
        self._chunks = chunks
        self.closed = False
        self.resolved_device_name = "Mic"

    async def frames(self):
        for c in self._chunks:
            yield AudioFrameF32(samples=c, sample_rate_hz=16000, channels=1)

    async def close(self):
        self.closed = True


def _rms(a) -> float:
    return float(np.sqrt((np.asarray(a, dtype=np.float64) ** 2).mean()) + 1e-12)


async def _collect(chunks, enabled=True):
    src = AutoGainAudioSource(source=_Src(chunks), enabled=enabled)
    out = [f.samples async for f in src.frames()]
    await src.close()
    return np.concatenate(out) if out else np.zeros(0, dtype=np.float32)


@pytest.mark.asyncio
async def test_quiet_mic_is_boosted() -> None:
    t = np.arange(1600) / 16000
    quiet = (0.004 * np.sin(2 * np.pi * 200 * t)).astype(np.float32)
    out = await _collect([quiet.copy() for _ in range(40)])
    assert _rms(out[-1600 * 10:]) > _rms(quiet) * 3.0


@pytest.mark.asyncio
async def test_normal_level_untouched_and_disabled_passthrough() -> None:
    t = np.arange(1600) / 16000
    normal = (0.25 * np.sin(2 * np.pi * 200 * t)).astype(np.float32)
    out = await _collect([normal.copy() for _ in range(5)])
    assert abs(_rms(out) - _rms(normal)) / _rms(normal) < 0.05
    off = await _collect([(normal * 0.02).astype(np.float32) for _ in range(8)], enabled=False)
    assert abs(_rms(off) - _rms(normal * 0.02)) / _rms(normal * 0.02) < 0.05


@pytest.mark.asyncio
async def test_true_silence_is_not_amplified() -> None:
    silence = np.zeros(1600, dtype=np.float32)
    out = await _collect([silence.copy() for _ in range(10)])
    assert _rms(out) < 1e-6


@pytest.mark.asyncio
async def test_source_close_and_attr_passthrough() -> None:
    inner = _Src([])
    src = AutoGainAudioSource(source=inner)
    assert src.resolved_device_name == "Mic"
    await src.close()
    assert inner.closed


def test_quiet_segment_hallucination_is_dropped() -> None:
    import puripuly_heart.providers.stt.local_qwen_sherpa as mod

    class _Result:
        text = "虚构"
        ys_log_probs = [-1.6, -1.7]  # above the normal bar, below the quiet bar
        lang = "zh"

    class _Stream:
        result = _Result()
        def accept_waveform(self, sr, samples): pass

    class _Rec:
        def create_stream(self): return _Stream()
        def decode_stream(self, stream): pass

    backend = mod.LocalQwenSherpaSTTBackend.__new__(mod.LocalQwenSherpaSTTBackend)
    for name, value in (("denoise", False), ("language_hint", None), ("hotwords", ()),
                        ("min_avg_logprob", mod.LOCAL_QWEN_MIN_AVG_LOGPROB),
                        ("stream_label", "peer"), ("diagnostics_enabled", None)):
        object.__setattr__(backend, name, value)

    quiet = (np.random.default_rng(3).normal(0, 0.003, 16000)).astype(np.float32)
    assert backend._decode_f32_sync(_Rec(), quiet) == ""

    loud = (np.random.default_rng(4).normal(0, 0.08, 16000)).astype(np.float32)
    assert backend._decode_f32_sync(_Rec(), loud) == "虚构"
