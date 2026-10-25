"""Main-app side of the floating chat window.

The floating chat is a separate small always-on-top window process (like the
Steam pop-out). It talks to the main app over a loopback-only TCP socket with
newline-delimited JSON:

- client -> {"type": "hello", "token": ...}  (wrong token: dropped)
- server -> {"type": "history", "entries": [...]} then one
            {"type": "entry", ...} per new chat line
- client -> {"type": "submit", "text": ...}  sent exactly like typing in the
            main chat box

Only chat lines (sent / received, text) cross the socket - never settings or
keys.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import secrets
from collections import deque
from collections.abc import Callable

logger = logging.getLogger(__name__)

_HISTORY = 60
_MAX_LINE = 64 * 1024


class FloatingChatHub:
    def __init__(self, on_submit: Callable[[str], None]) -> None:
        self._on_submit = on_submit
        self.token = secrets.token_urlsafe(18)
        self._history: deque[dict] = deque(maxlen=_HISTORY)
        self._writers: set[asyncio.StreamWriter] = set()
        self._server: asyncio.base_events.Server | None = None
        self.port: int | None = None

    @property
    def running(self) -> bool:
        return self._server is not None

    async def start(self) -> int:
        if self._server is not None and self.port is not None:
            return self.port
        self._server = await asyncio.start_server(
            self._handle, host="127.0.0.1", port=0, limit=_MAX_LINE
        )
        self.port = int(self._server.sockets[0].getsockname()[1])
        logger.info("[FloatingChat] listening on loopback port %s", self.port)
        return self.port

    async def stop(self) -> None:
        server, self._server = self._server, None
        for writer in list(self._writers):
            with contextlib.suppress(Exception):
                writer.close()
        self._writers.clear()
        if server is not None:
            server.close()
            with contextlib.suppress(Exception):
                await server.wait_closed()

    @property
    def client_count(self) -> int:
        return len(self._writers)

    def publish(
        self,
        *,
        channel: str,
        source_text: str,
        translated_text: str,
        time: str = "",
        speaker: str = "",
    ) -> None:
        """Called by the dashboard for every new chat line (page loop)."""
        if channel not in ("self", "peer"):
            return
        entry = {
            "type": "entry",
            "channel": channel,
            "source": source_text or "",
            "translation": translated_text or "",
            "time": time,
            "speaker": speaker or "",
        }
        self._history.append(entry)
        self._broadcast(entry)

    def _broadcast(self, payload: dict) -> None:
        line = (json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8")
        for writer in list(self._writers):
            try:
                writer.write(line)
            except Exception:
                self._writers.discard(writer)

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            hello = await asyncio.wait_for(reader.readline(), timeout=5.0)
            message = json.loads(hello.decode("utf-8") or "{}")
            if not (
                isinstance(message, dict)
                and message.get("type") == "hello"
                and secrets.compare_digest(str(message.get("token", "")), self.token)
            ):
                return
            self._writers.add(writer)
            writer.write(
                (
                    json.dumps({"type": "history", "entries": list(self._history)}, ensure_ascii=False)
                    + "\n"
                ).encode("utf-8")
            )
            await writer.drain()
            while True:
                raw = await reader.readline()
                if not raw:
                    return
                try:
                    message = json.loads(raw.decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError):
                    continue
                if not isinstance(message, dict) or message.get("type") != "submit":
                    continue
                text = str(message.get("text") or "").strip()
                if text:
                    with contextlib.suppress(Exception):
                        self._on_submit(text)
        except (asyncio.TimeoutError, ConnectionError, json.JSONDecodeError, UnicodeDecodeError):
            return
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.debug("[FloatingChat] client handler error", exc_info=True)
        finally:
            self._writers.discard(writer)
            with contextlib.suppress(Exception):
                writer.close()
