"""Spectral noise gate for steady mic noise (r298)."""
from __future__ import annotations

import numpy as np

from puripuly_heart.core.audio.noise_gate import spectral_denoise


def _rms(a: np.ndarray) -> float:
    return float(np.sqrt((a.astype(np.float64) ** 2).mean()) + 1e-12)


def test_reduces_steady_noise_and_keeps_speech_shape() -> None:
    sr = 16000
    t = np.arange(sr * 3) / sr
    env = (np.sin(2 * np.pi * 2.5 * t) > 0).astype(np.float32)
    speech = (env * (0.35 * np.sin(2 * np.pi * 180 * t)
                     + 0.2 * np.sin(2 * np.pi * 360 * t))).astype(np.float32)
    rng = np.random.default_rng(7)
    fan = np.convolve(rng.normal(0, 1, speech.shape), np.ones(8) / 8,
                      mode="same").astype(np.float32) * 0.25
    noisy = speech + fan

    out = spectral_denoise(noisy, sr)

    assert out.dtype == np.float32 and len(out) == len(noisy)
    silence = env < 0.5
    assert _rms(noisy[silence]) / _rms(out[silence]) > 1.8  # noise clearly cut
    voiced = ~silence
    corr = float(np.corrcoef(out[voiced], speech[voiced])[0, 1])
    assert corr > 0.7  # speech survives


def test_short_segments_pass_through_unchanged() -> None:
    x = np.random.default_rng(0).normal(0, 0.1, 800).astype(np.float32)
    out = spectral_denoise(x, 16000)
    assert np.array_equal(out, x)


def test_backend_flag_applies_gate(monkeypatch) -> None:
    # The backend must route segments through the gate when denoise=True.
    import puripuly_heart.providers.stt.local_qwen_sherpa as mod

    calls = []

    def fake_denoise(samples, sr):
        calls.append(len(samples))
        return samples

    monkeypatch.setattr(
        "puripuly_heart.core.audio.noise_gate.spectral_denoise", fake_denoise
    )

    class _Result:
        text = "hello"
        ys_log_probs = None
        lang = "en"

    class _Stream:
        result = _Result()
        def accept_waveform(self, sr, samples): pass
    class _Recognizer:
        def create_stream(self): return _Stream()
        def decode_stream(self, stream): pass

    backend = mod.LocalQwenSherpaSTTBackend.__new__(mod.LocalQwenSherpaSTTBackend)
    object.__setattr__(backend, "denoise", True)
    object.__setattr__(backend, "language_hint", None)
    object.__setattr__(backend, "hotwords", ())
    object.__setattr__(backend, "min_avg_logprob", None)
    object.__setattr__(backend, "stream_label", "self")
    object.__setattr__(backend, "diagnostics_enabled", None)

    samples = np.zeros(16000, dtype=np.float32)
    text = backend._decode_f32_sync(_Recognizer(), samples)
    assert text == "hello"
    assert calls == [16000]
