"""ElevenLabs Scribe v2 Realtime speech recognition (your own ElevenLabs key).

WebSocket wss://api.elevenlabs.io/v1/speech-to-text/realtime, key in the
xi-api-key header. Manual commits: audio streams as input_audio_chunk
messages and our VAD's speech end sends a chunk with commit=true, so each
utterance ends in exactly one committed transcript (one final event).
partial_transcript messages become non-final transcripts (live preview).
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Sequence
from urllib.parse import urlencode

import httpx

from puripuly_heart.core.stt.backend import (
    STTBackend,
    STTBackendSession,
    STTBackendTranscriptEvent,
)

logger = logging.getLogger(__name__)

SCRIBE_ENDPOINT = "wss://api.elevenlabs.io/v1/speech-to-text/realtime"
SCRIBE_MODEL = "scribe_v2_realtime"
_CHUNK_BYTES = 3200  # 100 ms at 16 kHz PCM16
_ERROR_TYPES = frozenset({
    "error", "auth_error", "quota_exceeded", "commit_throttled", "unaccepted_terms",
    "rate_limited", "queue_overflow", "resource_exhausted", "session_time_limit_exceeded",
    "input_error", "invalid_request", "chunk_size_exceeded", "transcriber_error",
})
_STOP = object()
_COMMIT = object()


def scribe_language_code(language: str | None) -> str:
    """ISO 639-1 base code ('' = auto-detect)."""
    value = (language or "").strip()
    if not value or value.lower() in ("auto", "auto_detect", "auto-detect"):
        return ""
    return value.split("-")[0].lower()


def scribe_url(
    *,
    endpoint: str = SCRIBE_ENDPOINT,
    model: str = SCRIBE_MODEL,
    language_code: str = "",
    keyterms: Sequence[str] = (),
    sample_rate_hz: int = 16000,
) -> str:
    params: list[tuple[str, str]] = [
        ("model_id", model),
        ("audio_format", f"pcm_{sample_rate_hz}"),
        ("commit_strategy", "manual"),
    ]
    if language_code:
        params.append(("language_code", language_code))
    for term in list(keyterms)[:100]:
        term = str(term).strip()
        if term:
            params.append(("keyterms", term))
    return f"{endpoint}?{urlencode(params)}"


@dataclass(slots=True)
class ElevenLabsScribeSTTBackend(STTBackend):
    api_key: str
    language: str = ""
    keyterms: Sequence[str] = ()
    endpoint: str = SCRIBE_ENDPOINT
    model: str = SCRIBE_MODEL
    sample_rate_hz: int = 16000
    connect_timeout_s: float = 8.0
    commit_timeout_s: float = 3.0

    async def open_session(self) -> STTBackendSession:
        if not self.api_key:
            raise ValueError("api_key must be non-empty")
        session = _ScribeSession(
            api_key=self.api_key,
            url=scribe_url(
                endpoint=self.endpoint,
                model=self.model,
                language_code=scribe_language_code(self.language),
                keyterms=self.keyterms,
                sample_rate_hz=self.sample_rate_hz,
            ),
            sample_rate_hz=self.sample_rate_hz,
            connect_timeout_s=self.connect_timeout_s,
            commit_timeout_s=self.commit_timeout_s,
        )
        await session.start()
        return session

    @staticmethod
    async def verify_api_key(api_key: str) -> bool:
        if not api_key:
            return False
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.get(
                    "https://api.elevenlabs.io/v1/user", headers={"xi-api-key": api_key}
                )
                return response.status_code == 200
        except Exception:
            return False


@dataclass(slots=True)
class _ScribeSession(STTBackendSession):
    api_key: str
    url: str
    sample_rate_hz: int
    connect_timeout_s: float
    commit_timeout_s: float

    _ws: Any = field(init=False, default=None, repr=False)
    _events: asyncio.Queue = field(init=False, repr=False)
    _audio_q: asyncio.Queue = field(init=False, repr=False)
    _send_task: asyncio.Task | None = field(init=False, default=None, repr=False)
    _recv_task: asyncio.Task | None = field(init=False, default=None, repr=False)
    _started: asyncio.Event = field(init=False, repr=False)
    _committed: list[str] = field(init=False, default_factory=list, repr=False)
    _partial: str = field(init=False, default="", repr=False)
    _pending_commits: int = field(init=False, default=0, repr=False)
    _commit_deadline: float = field(init=False, default=0.0, repr=False)
    _timer: asyncio.Task | None = field(init=False, default=None, repr=False)
    _closing: bool = field(init=False, default=False, repr=False)
    _ended: bool = field(init=False, default=False, repr=False)

    def __post_init__(self) -> None:
        self._events = asyncio.Queue()
        self._audio_q = asyncio.Queue()
        self._started = asyncio.Event()

    async def start(self) -> None:
        import websockets

        self._ws = await websockets.connect(
            self.url,
            open_timeout=self.connect_timeout_s,
            additional_headers={"xi-api-key": self.api_key},
            max_size=None,
        )
        self._recv_task = asyncio.create_task(self._recv_loop())
        with contextlib.suppress(asyncio.TimeoutError):
            # session_started arrives right away; do not fail hard if a server
            # version skips it - audio still works
            await asyncio.wait_for(self._started.wait(), timeout=self.connect_timeout_s)
        if self._ended:
            reason = getattr(self._ws, "close_reason", "") or "no reason given"
            await self.close()
            raise RuntimeError(f"ElevenLabs Scribe closed the session: {reason}")
        self._send_task = asyncio.create_task(self._send_loop())
        logger.info("[STT] ElevenLabs Scribe connected")

    async def send_audio(self, pcm16le: bytes) -> None:
        if not self._closing and not self._ended and pcm16le:
            await self._audio_q.put(pcm16le)

    async def on_speech_end(self, *, trailing_silence_ms: int | None = None) -> None:
        _ = trailing_silence_ms
        if not self._closing and not self._ended:
            await self._audio_q.put(_COMMIT)

    async def _send_chunk(self, pcm: bytes, *, commit: bool = False) -> None:
        await self._ws.send(json.dumps({
            "message_type": "input_audio_chunk",
            "audio_base_64": base64.b64encode(pcm).decode("ascii"),
            "commit": commit,
            "sample_rate": self.sample_rate_hz,
        }))

    async def _send_loop(self) -> None:
        pending = bytearray()
        try:
            while True:
                item = await self._audio_q.get()
                if item is _STOP:
                    return
                if item is _COMMIT:
                    # a commit needs a chunk to ride on; 20 ms of silence if empty
                    tail = bytes(pending) or bytes(int(self.sample_rate_hz * 0.02) * 2)
                    pending.clear()
                    await self._send_chunk(tail, commit=True)
                    self._pending_commits += 1
                    self._commit_deadline = time.monotonic() + self.commit_timeout_s
                    if self._timer is None or self._timer.done():
                        self._timer = asyncio.create_task(self._commit_watchdog())
                    continue
                pending.extend(item)
                while len(pending) >= _CHUNK_BYTES:
                    await self._send_chunk(bytes(pending[:_CHUNK_BYTES]))
                    del pending[:_CHUNK_BYTES]
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            if not self._closing:
                self._put(exc)

    async def _commit_watchdog(self) -> None:
        """If the server never answers a commit, hand on what we heard."""
        try:
            while self._pending_commits > 0 and not self._ended:
                await asyncio.sleep(0.1)
                if time.monotonic() >= self._commit_deadline:
                    self._pending_commits = 0
                    self._emit_final(fallback=True)
        except asyncio.CancelledError:
            pass

    async def _recv_loop(self) -> None:
        try:
            async for message in self._ws:
                self.handle_message(message)
        except asyncio.CancelledError:
            raise
        except Exception:
            pass
        finally:
            self._ended = True
            self._started.set()
            if not self._closing:
                code = getattr(self._ws, "close_code", None)
                reason = getattr(self._ws, "close_reason", "") or "none"
                self._put(RuntimeError(
                    f"ElevenLabs Scribe session closed (code={code}, reason={reason})"
                ))
            self._put(None)

    def handle_message(self, message: str | bytes) -> None:
        if isinstance(message, bytes):
            message = message.decode("utf-8", errors="ignore")
        try:
            data = json.loads(message)
        except json.JSONDecodeError:
            return
        if not isinstance(data, dict):
            return
        kind = str(data.get("message_type") or "")
        if kind == "session_started":
            self._started.set()
        elif kind == "partial_transcript":
            text = str(data.get("text") or "").strip()
            if text and text != self._partial:
                self._partial = text
                joined = " ".join([*self._committed, text]).strip()
                self._put(STTBackendTranscriptEvent(text=joined, is_final=False))
        elif kind == "committed_transcript":
            text = str(data.get("text") or "").strip()
            if text:
                self._committed.append(text)
            self._partial = ""
            if self._pending_commits > 0:
                self._pending_commits -= 1
                self._emit_final()
        elif kind in _ERROR_TYPES:
            detail = data.get("error") or data.get("message") or kind
            if kind == "insufficient_audio_activity":
                return
            self._put(RuntimeError(f"ElevenLabs Scribe {kind}: {detail}"))

    def _emit_final(self, *, fallback: bool = False) -> None:
        text = " ".join(self._committed).strip()
        if not text and fallback:
            text = self._partial
        self._committed.clear()
        self._partial = ""
        if text:
            logger.info("[STT] Transcript: '%s' (final, scribe)", text)
            self._put(STTBackendTranscriptEvent(text=text, is_final=True))

    def _put(self, event: object) -> None:
        self._events.put_nowait(event)

    async def stop(self) -> None:
        await self.close()

    async def close(self) -> None:
        if self._closing:
            return
        self._closing = True
        with contextlib.suppress(Exception):
            self._audio_q.put_nowait(_STOP)
        for task in (self._timer, self._send_task):
            if task is not None and not task.done():
                task.cancel()
        if self._ws is not None:
            with contextlib.suppress(Exception):
                await asyncio.wait_for(self._ws.close(), timeout=3.0)
        task = self._recv_task
        if task is not None and not task.done():
            task.cancel()
            with contextlib.suppress(BaseException):
                await task
        if not self._ended:
            self._ended = True
            self._put(None)

    async def events(self) -> AsyncIterator[STTBackendTranscriptEvent]:
        while True:
            event = await self._events.get()
            if event is None:
                break
            if isinstance(event, BaseException):
                raise event
            yield event
