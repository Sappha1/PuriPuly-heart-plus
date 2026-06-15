from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

import numpy as np


class MuteState(Protocol):
    muted: bool | None


@dataclass(slots=True)
class VrcMicAudioGate:
    state: MuteState
    enabled: bool = True
    receiver_active: bool = False

    def set_enabled(self, enabled: bool) -> None:
        self.enabled = enabled

    def set_receiver_active(self, active: bool) -> None:
        self.receiver_active = active

    def reset(self) -> None:
        pass

    def process_chunk(self, chunk: np.ndarray) -> np.ndarray:
        if not self.enabled:
            return chunk
        if not self.receiver_active:
            return chunk
        muted = getattr(self.state, "muted", None)
        if muted is False:
            return chunk
        # muted is True or None — block audio.
        # None means VRChat hasn't sent its state yet; assume blocked until confirmed unmuted.
        return np.zeros_like(chunk)
