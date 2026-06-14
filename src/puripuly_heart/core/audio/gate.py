from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Callable, Protocol

import numpy as np


class MuteState(Protocol):
    muted: bool | None


@dataclass(slots=True)
class VrcMicAudioGate:
    state: MuteState
    enabled: bool = True
    receiver_active: bool = False
    initial_sync_grace_s: float = 1.0
    monotonic: Callable[[], float] = time.monotonic
    _sync_deadline: float | None = field(default=None, init=False)

    def set_enabled(self, enabled: bool) -> None:
        self.enabled = enabled
        self._reset_sync_deadline()

    def set_receiver_active(self, active: bool) -> None:
        self.receiver_active = active
        self._reset_sync_deadline()

    def reset(self) -> None:
        self._reset_sync_deadline()

    def process_chunk(self, chunk: np.ndarray) -> np.ndarray:
        if not self.enabled:
            return chunk

        if not self.receiver_active:
            return chunk

        muted = getattr(self.state, "muted", None)
        if muted is True:
            return np.zeros_like(chunk)
        if muted is False:
            self._sync_deadline = None
            return chunk

        if self._sync_deadline is not None and self.monotonic() < self._sync_deadline:
            return np.zeros_like(chunk)

        self._sync_deadline = None
        return chunk

    def _reset_sync_deadline(self) -> None:
        muted = getattr(self.state, "muted", None)
        if (
            self.enabled
            and self.receiver_active
            and muted is None
            and self.initial_sync_grace_s > 0
        ):
            self._sync_deadline = self.monotonic() + self.initial_sync_grace_s
            return
        self._sync_deadline = None
