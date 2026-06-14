from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any

from pythonosc.dispatcher import Dispatcher
from pythonosc.osc_server import AsyncIOOSCUDPServer

logger = logging.getLogger(__name__)

VRC_OSC_RECEIVER_HOST = "127.0.0.1"
VRC_OSC_RECEIVER_PORT = 9001
VRC_OSC_MUTE_ADDRESS = "/avatar/parameters/MuteSelf"


@dataclass(slots=True)
class VrcMicState:
    muted: bool | None = None

    def update(self, muted: bool) -> bool:
        if self.muted == muted:
            return False
        self.muted = muted
        return True

    def reset(self) -> None:
        self.muted = None


class VrcOscReceiver:
    def __init__(
        self,
        state: VrcMicState,
        *,
        host: str = VRC_OSC_RECEIVER_HOST,
        port: int = VRC_OSC_RECEIVER_PORT,
        mute_delay_s: float = 0.4,
    ) -> None:
        self.state = state
        self.host = host
        self.port = port
        self.mute_delay_s = mute_delay_s
        self.transport = None
        self._mute_task: asyncio.Task[None] | None = None

    def mute_handler(self, address: str, *args: Any) -> None:
        _ = address
        if not args:
            return
        is_muted = bool(args[0])

        if self._mute_task is not None and not self._mute_task.done():
            self._mute_task.cancel()

        loop = asyncio.get_running_loop()
        self._mute_task = loop.create_task(self._apply_mute_state(is_muted))

    async def _apply_mute_state(self, is_muted: bool) -> None:
        try:
            if is_muted:
                await asyncio.sleep(self.mute_delay_s)

            if self.state.update(is_muted):
                logger.info("[OSC Receiver] VRChat mic muted state applied: %s", is_muted)
        except asyncio.CancelledError:
            raise

    async def start(self) -> None:
        if self.transport is not None:
            return

        # A restarted receiver must wait for a fresh VRChat mute edge.
        self.state.reset()

        dispatcher = Dispatcher()
        dispatcher.map(VRC_OSC_MUTE_ADDRESS, self.mute_handler)

        loop = asyncio.get_running_loop()
        try:
            server = AsyncIOOSCUDPServer((self.host, self.port), dispatcher, loop)
            self.transport, _ = await server.create_serve_endpoint()
        except OSError:
            logger.exception(
                "[OSC Receiver] Failed to start AsyncIOOSCUDPServer on %s:%s",
                self.host,
                self.port,
            )
            raise

        logger.info(
            "[OSC Receiver] Listening on %s:%s for VRChat parameters",
            self.host,
            self.port,
        )

    def stop(self) -> None:
        if self._mute_task is not None and not self._mute_task.done():
            self._mute_task.cancel()
        self._mute_task = None

        if self.transport is not None:
            self.transport.close()
            self.transport = None
            logger.info("[OSC Receiver] Stopped listening.")
