from __future__ import annotations

from typing import Protocol

from puripuly_heart.core.vad.gating import VadEvent


class VadEventSink(Protocol):
    async def handle_vad_event(self, event: VadEvent) -> None: ...
