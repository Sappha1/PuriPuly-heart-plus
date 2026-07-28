"""Shared boost-only auto-gain for capture streams.

Quiet capture is the single most common cause of "the app doesn't hear me":
a low mic level (or low Windows volume on the loopback side) leaves the
recognizer with near-silence, which it either ignores or turns into stock
hallucinated phrases. This lifts quiet audio to a stable internal level
before detection/recognition. Playback is never touched.

Boost-only, capped, slewed, and silence-guarded — true silence keeps its
level so it stays identifiable as silence downstream.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import AsyncIterator

import numpy as np

from puripuly_heart.core.audio.format import AudioFrameF32
from puripuly_heart.core.audio.source import AudioSource

TARGET_RMS = 0.05          # ~ -26 dBFS
MAX_GAIN = 6.0             # ~ +15.5 dB
SILENCE_RMS = 0.0015       # ~ -56 dBFS: hold gain, never amplify the floor
EMA_ALPHA = 0.10
SLEW = 0.25                # max relative gain change per chunk


@dataclass(slots=True)
class AutoGainState:
    rms_ema: float = 0.0
    gain: float = 1.0

    def apply(self, chunk: np.ndarray) -> np.ndarray:
        rms = float(np.sqrt(np.mean(np.square(chunk), dtype=np.float64)))
        if rms < SILENCE_RMS:
            if self.gain != 1.0:
                return np.clip(chunk * self.gain, -1.0, 1.0).astype(np.float32)
            return chunk
        ema = self.rms_ema
        ema = rms if ema <= 0.0 else (1.0 - EMA_ALPHA) * ema + EMA_ALPHA * rms
        self.rms_ema = ema
        desired = min(MAX_GAIN, max(1.0, TARGET_RMS / max(ema, 1e-6)))
        low, high = self.gain * (1.0 - SLEW), self.gain * (1.0 + SLEW)
        self.gain = min(max(min(max(desired, low), high), 1.0), MAX_GAIN)
        if self.gain == 1.0:
            return chunk
        return np.clip(chunk * self.gain, -1.0, 1.0).astype(np.float32)


@dataclass(slots=True)
class AutoGainAudioSource(AudioSource):
    """Wraps an AudioSource, boosting quiet frames before they reach the VAD."""

    source: AudioSource
    enabled: bool = True
    _state: AutoGainState = field(default_factory=AutoGainState, init=False, repr=False)

    def __getattr__(self, name: str):  # diagnostics passthrough
        return getattr(self.source, name)

    async def frames(self) -> AsyncIterator[AudioFrameF32]:
        async for frame in self.source.frames():
            if not self.enabled:
                yield frame
                continue
            samples = np.asarray(frame.samples, dtype=np.float32).reshape(-1)
            boosted = self._state.apply(samples)
            if boosted is samples:
                yield frame
                continue
            yield AudioFrameF32(
                samples=boosted,
                sample_rate_hz=frame.sample_rate_hz,
                channels=frame.channels,
            )

    async def close(self) -> None:
        await self.source.close()
