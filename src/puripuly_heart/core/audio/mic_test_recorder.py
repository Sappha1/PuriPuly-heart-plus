"""Short record-and-play-back clip for the microphone test.

The level meter shows that the microphone hears something; playing a few
seconds back lets the user hear what it actually picks up (volume, noise,
the wrong device, a muffled headset).
"""

from __future__ import annotations

import asyncio

import numpy as np

DEFAULT_RECORD_SECONDS = 5.0


class MicTestRecorder:
    def __init__(self) -> None:
        self._chunks: list[np.ndarray] = []
        self._rate: int | None = None
        self._target_samples = 0
        self._collected = 0
        self._recording = False

    @property
    def recording(self) -> bool:
        return self._recording

    @property
    def has_clip(self) -> bool:
        return not self._recording and self._collected > 0 and self._rate is not None

    @property
    def clip_seconds(self) -> float:
        if not self._rate:
            return 0.0
        return self._collected / float(self._rate)

    def start(self, seconds: float = DEFAULT_RECORD_SECONDS) -> None:
        self._chunks = []
        self._rate = None
        self._collected = 0
        self._target_samples = 0
        self._seconds = max(0.5, float(seconds))
        self._recording = True

    def cancel(self) -> None:
        self._recording = False

    def stop(self) -> bool:
        """End the recording now, keeping what was captured; True if any."""
        self._recording = False
        return self.has_clip

    def add_frame(self, frame) -> bool:
        """Feed one captured frame; True when it completed the recording."""
        if not self._recording:
            return False
        samples = np.asarray(frame.samples, dtype=np.float32)
        if samples.ndim == 2:
            samples = samples.mean(axis=1) if samples.shape[1] > 1 else samples[:, 0]
        rate = int(frame.sample_rate_hz)
        if self._rate is None:
            self._rate = rate
            self._target_samples = int(self._seconds * rate)
        elif rate != self._rate:
            return False  # device changed mid-clip; ignore the odd frame
        remaining = self._target_samples - self._collected
        if remaining <= 0:
            self._recording = False
            return True
        chunk = samples[:remaining].copy()
        self._chunks.append(chunk)
        self._collected += len(chunk)
        if self._collected >= self._target_samples:
            self._recording = False
            return True
        return False

    def clip(self) -> tuple[np.ndarray, int] | None:
        if not self.has_clip:
            return None
        data = np.concatenate(self._chunks) if self._chunks else np.zeros(0, np.float32)
        return np.clip(data, -1.0, 1.0), int(self._rate or 0)

    async def play(self) -> bool:
        clip = self.clip()
        if clip is None:
            return False
        data, rate = clip

        def _play() -> None:
            import sounddevice as sd

            sd.play(data, rate)
            sd.wait()

        await asyncio.to_thread(_play)
        return True

    @staticmethod
    def stop_playback() -> None:
        import sounddevice as sd

        sd.stop()
