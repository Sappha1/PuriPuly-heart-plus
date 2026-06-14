from __future__ import annotations

import importlib
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from puripuly_heart.core.audio.format import mixdown_to_mono_f32, reshape_audio_samples_f32

NOOP_SAMPLE_RATE_HZ = 16000


def _import_soxr() -> Any:
    return importlib.import_module("soxr")


@dataclass(slots=True)
class MonoFirstStreamingResampler:
    input_sample_rate_hz: int
    output_sample_rate_hz: int = NOOP_SAMPLE_RATE_HZ
    input_channels: int = 1
    _stream: Any | None = field(init=False, default=None, repr=False)
    _flushed: bool = field(init=False, default=False, repr=False)

    def __post_init__(self) -> None:
        if self.input_sample_rate_hz <= 0 or self.output_sample_rate_hz <= 0:
            raise ValueError("sample rates must be > 0")
        if self.input_channels <= 0:
            raise ValueError("input_channels must be > 0")
        if self._uses_noop_path:
            return
        self._stream = _import_soxr().ResampleStream(
            self.input_sample_rate_hz,
            self.output_sample_rate_hz,
            1,
            dtype="float32",
            quality="MQ",
        )

    @property
    def _uses_noop_path(self) -> bool:
        return (
            self.input_sample_rate_hz == NOOP_SAMPLE_RATE_HZ
            and self.output_sample_rate_hz == NOOP_SAMPLE_RATE_HZ
        )

    def resample_chunk(self, samples: np.ndarray, *, last: bool = False) -> np.ndarray:
        if self._flushed:
            raise RuntimeError("stream has already been flushed")

        mono = self._prepare_mono_chunk(samples)
        if self._uses_noop_path:
            output = mono
        else:
            if self._stream is None:
                raise RuntimeError("soxr stream is unavailable")
            output = np.asarray(self._stream.resample_chunk(mono, last=last), dtype=np.float32)

        if last:
            self._flushed = True
        return output

    def flush(self) -> np.ndarray:
        return self.resample_chunk(np.empty((0,), dtype=np.float32), last=True)

    def _prepare_mono_chunk(self, samples: np.ndarray) -> np.ndarray:
        samples = np.asarray(samples, dtype=np.float32)
        if samples.size == 0:
            return np.empty((0,), dtype=np.float32)

        reshaped = reshape_audio_samples_f32(samples, channels=self.input_channels)
        if reshaped.ndim == 2 and reshaped.shape[1] != self.input_channels:
            raise ValueError("2D samples channel count must match input_channels")
        return mixdown_to_mono_f32(reshaped)


__all__ = ["MonoFirstStreamingResampler", "NOOP_SAMPLE_RATE_HZ"]
