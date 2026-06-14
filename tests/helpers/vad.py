from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass(slots=True)
class SequenceVadEngine:
    probs: list[float]
    idx: int = 0

    def speech_probability(self, samples: np.ndarray, *, sample_rate_hz: int) -> float:
        _ = samples
        _ = sample_rate_hz
        prob = self.probs[self.idx]
        self.idx = min(self.idx + 1, len(self.probs) - 1)
        return prob

    def reset(self) -> None:
        return


def chunk_samples(value: float, *, n: int) -> np.ndarray:
    return np.full((n,), value, dtype=np.float32)
