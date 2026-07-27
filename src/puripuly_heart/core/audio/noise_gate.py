"""Dependency-free spectral noise gate for steady background noise.

Targets CONSTANT noise (fans, AC hum, PSU whine) mixed with speech: the
per-frequency noise floor is estimated from the quietest frames of the same
segment, then subtracted with a conservative gain floor so speech stays
intact. Pure numpy — the sherpa-onnx GTCRN denoiser would be preferable but
segfaults against the pinned onnxruntime 1.17.1 (needs ORT API 23).
"""
from __future__ import annotations

import numpy as np

_FRAME = 512
_HOP = 128
# How aggressively the noise floor is subtracted, and the minimum gain kept so
# speech harmonics never get fully zeroed (avoids "musical noise" artifacts).
_OVERSUBTRACT = 1.6
_GAIN_FLOOR = 0.08
# Percentile of per-bin magnitudes treated as the steady-noise floor. Steady
# noise is present in every frame, speech only in some — a low percentile
# isolates the noise even when the segment is mostly speech.
_NOISE_PERCENTILE = 20.0


def spectral_denoise(samples: np.ndarray, sample_rate_hz: int = 16000) -> np.ndarray:
    """Return a denoised copy of mono float32 samples (same length/dtype).

    Segments shorter than a few frames are returned unchanged.
    """
    _ = sample_rate_hz  # frame sizes are fine for 16k speech; kept for clarity
    x = np.asarray(samples, dtype=np.float32).reshape(-1)
    if x.size < _FRAME * 3:
        return x

    window = np.hanning(_FRAME).astype(np.float32)
    n_frames = 1 + (x.size - _FRAME) // _HOP
    idx = np.arange(_FRAME)[None, :] + _HOP * np.arange(n_frames)[:, None]
    frames = x[idx] * window[None, :]

    spec = np.fft.rfft(frames, axis=1)
    mag = np.abs(spec)

    noise_floor = np.percentile(mag, _NOISE_PERCENTILE, axis=0)[None, :]
    gain = (mag - _OVERSUBTRACT * noise_floor) / np.maximum(mag, 1e-9)
    np.clip(gain, _GAIN_FLOOR, 1.0, out=gain)

    cleaned = np.fft.irfft(spec * gain, n=_FRAME, axis=1).astype(np.float32)

    out = np.zeros(x.size, dtype=np.float32)
    norm = np.zeros(x.size, dtype=np.float32)
    win_sq = (window * window).astype(np.float32)
    for i in range(n_frames):
        start = i * _HOP
        out[start:start + _FRAME] += cleaned[i] * window
        norm[start:start + _FRAME] += win_sq
    np.divide(out, np.maximum(norm, 1e-6), out=out)

    # The tail beyond the last full frame keeps the original samples.
    tail_start = (n_frames - 1) * _HOP + _FRAME
    if tail_start < x.size:
        out[tail_start:] = x[tail_start:]
    return out
