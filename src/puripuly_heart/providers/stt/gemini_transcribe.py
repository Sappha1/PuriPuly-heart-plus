"""Gemini 3.5 Transcribe Live - cloud speech recognition over the Gemini Live API.

Free on Google's free tier with a Google AI Studio key (the same key the
Gemini translators use). Talks to the Live API WebSocket directly: the bundled
google-genai SDK predates the transcription fields this model needs.

Protocol (ai.google.dev/gemini-api/docs/live-api/live-transcribe):
- setup: model + TEXT modality + inputAudioTranscription {languageCodes,
  customVocabulary}; the server answers setupComplete.
- audio: realtimeInput.audio = base64 16 kHz mono PCM16, ~100 ms chunks.
- our VAD's speech end sends realtimeInput.audioStreamEnd, which makes the
  server finalize right away (its own VAD stays as a fallback).
- serverContent.interimInputTranscription = in-progress text (sent on as a
  non-final transcript, e.g. for the live chatbox preview);
  serverContent.inputTranscription = finalized text. Finals are collected and
  handed on as ONE final per speech end, so the controller's one-final-per-
  utterance pairing stays intact.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import json
import logging
import time
import unicodedata
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Sequence

from puripuly_heart.core.stt.backend import (
    STTBackend,
    STTBackendSession,
    STTBackendTranscriptEvent,
)

logger = logging.getLogger(__name__)

GEMINI_TRANSCRIBE_MODEL = "gemini-3.5-transcribe-live"
GEMINI_LIVE_ENDPOINT = (
    "wss://generativelanguage.googleapis.com/ws/"
    "google.ai.generativelanguage.v1beta.GenerativeService.BidiGenerateContent"
)
_MAX_LANGUAGE_CODES = 32
_MAX_VOCABULARY = 1000
_CHUNK_BYTES = 3200  # 100 ms of 16 kHz PCM16

_STOP = object()
_END_OF_SPEECH = object()


def gemini_transcribe_language_codes(*languages: str | None) -> list[str]:
    """BCP-47 hints for the given UI language codes; empty = auto-detect."""
    from puripuly_heart.providers.stt.google_stt import _to_google_lang

    codes: list[str] = []
    for language in languages:
        value = (language or "").strip()
        if not value or value.lower() in ("auto", "auto_detect", "auto-detect"):
            continue
        code = _to_google_lang(value)
        if code and code not in codes:
            codes.append(code)
    return codes[:_MAX_LANGUAGE_CODES]


def _is_cjk(char: str) -> bool:
    if not char:
        return False
    name = unicodedata.name(char, "")
    return any(tag in name for tag in ("CJK", "HIRAGANA", "KATAKANA", "HANGUL"))


def join_transcript_pieces(pieces: Sequence[str]) -> str:
    """Join finalized pieces: a space between words, none around CJK text."""
    out = ""
    for piece in pieces:
        text = piece.strip()
        if not text:
            continue
        if out and not (_is_cjk(out[-1]) or _is_cjk(text[0])):
            out += " "
        out += text
    return out


@dataclass(slots=True)
class GeminiTranscribeSTTBackend(STTBackend):
    api_key: str
    language_codes: Sequence[str] = ()
    custom_vocabulary: Sequence[str] = ()
    model: str = GEMINI_TRANSCRIBE_MODEL
    endpoint: str = GEMINI_LIVE_ENDPOINT
    sample_rate_hz: int = 16000
    connect_timeout_s: float = 8.0
    # after our speech end: wait this long at most for the server's final text
    finalize_timeout_s: float = 2.5
    # ...and hand it on once no further final piece arrived for this long
    settle_s: float = 0.3

    async def open_session(self) -> STTBackendSession:
        if not self.api_key:
            raise ValueError("api_key must be non-empty")
        if self.sample_rate_hz != 16000:
            raise ValueError("sample_rate_hz must be 16000")
        session = _GeminiTranscribeSession(
            api_key=self.api_key,
            model=self.model,
            endpoint=self.endpoint,
            sample_rate_hz=self.sample_rate_hz,
            language_codes=list(self.language_codes)[:_MAX_LANGUAGE_CODES],
            custom_vocabulary=[
                term for term in (str(t).strip() for t in self.custom_vocabulary) if term
            ][:_MAX_VOCABULARY],
            connect_timeout_s=self.connect_timeout_s,
            finalize_timeout_s=self.finalize_timeout_s,
            settle_s=self.settle_s,
        )
        await session.start()
        return session

    @staticmethod
    async def verify_api_key(api_key: str) -> bool:
        """The key is good when it can see the transcription model."""
        from puripuly_heart.providers.llm.gemini import GeminiLLMProvider

        return await GeminiLLMProvider.verify_api_key(api_key, model=GEMINI_TRANSCRIBE_MODEL)


@dataclass(slots=True)
class _GeminiTranscribeSession(STTBackendSession):
    api_key: str
    model: str
    endpoint: str
    sample_rate_hz: int
    language_codes: list[str]
    custom_vocabulary: list[str]
    connect_timeout_s: float
    finalize_timeout_s: float
    settle_s: float

    _ws: Any = field(init=False, default=None, repr=False)
    _events: asyncio.Queue = field(init=False, repr=False)
    _audio_q: asyncio.Queue = field(init=False, repr=False)
    _send_task: asyncio.Task | None = field(init=False, default=None, repr=False)
    _recv_task: asyncio.Task | None = field(init=False, default=None, repr=False)
    _finalize_task: asyncio.Task | None = field(init=False, default=None, repr=False)
    _setup_done: asyncio.Event = field(init=False, repr=False)
    _final_parts: list[str] = field(init=False, default_factory=list, repr=False)
    _interim: str = field(init=False, default="", repr=False)
    _last_partial: str = field(init=False, default="", repr=False)
    _finals_since_end: int = field(init=False, default=0, repr=False)
    _last_final_at: float = field(init=False, default=0.0, repr=False)
    _finished_flag: bool = field(init=False, default=False, repr=False)
    _closing: bool = field(init=False, default=False, repr=False)
    _ended: bool = field(init=False, default=False, repr=False)

    def __post_init__(self) -> None:
        self._events = asyncio.Queue()
        self._audio_q = asyncio.Queue()
        self._setup_done = asyncio.Event()

    def setup_message(self) -> dict[str, Any]:
        transcription: dict[str, Any] = {"languageCodes": list(self.language_codes)}
        if self.custom_vocabulary:
            transcription["customVocabulary"] = list(self.custom_vocabulary)
        return {
            "setup": {
                "model": f"models/{self.model}",
                "generationConfig": {"responseModalities": ["TEXT"]},
                "inputAudioTranscription": transcription,
            }
        }

    async def start(self) -> None:
        import websockets

        # the key goes in the query string as the Live API documents; the URL
        # is never logged
        url = f"{self.endpoint}?key={self.api_key}"
        logger.info(
            "[STT] Gemini Transcribe connecting (languages=%s, vocabulary=%s)",
            self.language_codes or "auto",
            len(self.custom_vocabulary),
        )
        started = time.monotonic()
        self._ws = await websockets.connect(
            url, ping_interval=20, open_timeout=self.connect_timeout_s, max_size=None
        )
        await self._ws.send(json.dumps(self.setup_message()))
        self._recv_task = asyncio.create_task(self._recv_loop())
        try:
            await asyncio.wait_for(self._setup_done.wait(), timeout=self.connect_timeout_s)
        except asyncio.TimeoutError:
            await self.close()
            raise RuntimeError("Gemini Transcribe did not confirm the session setup") from None
        if self._ended:
            reason = getattr(self._ws, "close_reason", "") or "no reason given"
            await self.close()
            raise RuntimeError(f"Gemini Transcribe closed the session during setup: {reason}")
        logger.info("[STT] Gemini Transcribe connected in %.2fs", time.monotonic() - started)
        self._send_task = asyncio.create_task(self._send_loop())

    # ── sending ────────────────────────────────────────────────────────────
    async def send_audio(self, pcm16le: bytes) -> None:
        if self._closing or self._ended or not pcm16le:
            return
        await self._audio_q.put(pcm16le)

    async def on_speech_end(self, *, trailing_silence_ms: int | None = None) -> None:
        _ = trailing_silence_ms
        if self._closing or self._ended:
            return
        await self._audio_q.put(_END_OF_SPEECH)

    async def _send_json(self, payload: dict[str, Any]) -> None:
        await self._ws.send(json.dumps(payload))

    async def _send_chunk(self, pcm: bytes) -> None:
        await self._send_json(
            {
                "realtimeInput": {
                    "audio": {
                        "data": base64.b64encode(pcm).decode("ascii"),
                        "mimeType": f"audio/pcm;rate={self.sample_rate_hz}",
                    }
                }
            }
        )

    async def _send_loop(self) -> None:
        pending = bytearray()
        try:
            while True:
                item = await self._audio_q.get()
                if item is _STOP:
                    return
                if item is _END_OF_SPEECH:
                    if pending:
                        await self._send_chunk(bytes(pending))
                        pending.clear()
                    await self._send_json({"realtimeInput": {"audioStreamEnd": True}})
                    self._begin_finalize()
                    continue
                pending.extend(item)
                while len(pending) >= _CHUNK_BYTES:
                    await self._send_chunk(bytes(pending[:_CHUNK_BYTES]))
                    del pending[:_CHUNK_BYTES]
                if pending and self._audio_q.empty():
                    # no more audio queued: do not sit on a partial chunk
                    await self._send_chunk(bytes(pending))
                    pending.clear()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            if not self._closing:
                logger.warning("[STT] Gemini Transcribe send failed: %s", exc)
                self._put(exc)

    # ── receiving ──────────────────────────────────────────────────────────
    async def _recv_loop(self) -> None:
        close_reason = ""
        try:
            async for message in self._ws:
                self.handle_message(message)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            close_reason = str(exc)
        finally:
            self._ended = True
            self._setup_done.set()
            if not self._closing:
                code = getattr(self._ws, "close_code", None)
                reason = getattr(self._ws, "close_reason", "") or close_reason
                # an unexpected close must surface so the controller reconnects
                self._put(
                    RuntimeError(
                        f"Gemini Transcribe session closed (code={code}, reason={reason or 'none'})"
                    )
                )
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
        if "setupComplete" in data:
            self._setup_done.set()
            return
        error = data.get("error")
        if error:
            detail = error.get("message") if isinstance(error, dict) else str(error)
            self._put(RuntimeError(f"Gemini Transcribe error: {detail}"))
            return
        if "goAway" in data:
            logger.info("[STT] Gemini Transcribe server asked to reconnect soon")
            return
        content = data.get("serverContent")
        if not isinstance(content, dict):
            return
        final = content.get("inputTranscription")
        if isinstance(final, dict):
            text = str(final.get("text") or "")
            if text.strip():
                self._final_parts.append(text)
                self._finals_since_end += 1
                self._last_final_at = time.monotonic()
            if final.get("finished"):
                self._finished_flag = True
            self._interim = ""
            self._emit_partial()
        interim = content.get("interimInputTranscription")
        if isinstance(interim, dict):
            self._interim = str(interim.get("text") or "")
            self._emit_partial()

    def _emit_partial(self) -> None:
        text = join_transcript_pieces([*self._final_parts, self._interim])
        if not text or text == self._last_partial:
            return
        self._last_partial = text
        self._put(STTBackendTranscriptEvent(text=text, is_final=False))

    # ── finalizing one utterance ───────────────────────────────────────────
    def _begin_finalize(self) -> None:
        task = self._finalize_task
        if task is not None and not task.done():
            return  # the running one picks up whatever arrives
        self._finals_since_end = 0
        self._finished_flag = False
        self._finalize_task = asyncio.create_task(self._finalize())

    async def _finalize(self) -> None:
        deadline = time.monotonic() + self.finalize_timeout_s
        while time.monotonic() < deadline and not self._ended:
            if self._finished_flag:
                break
            if self._finals_since_end and time.monotonic() - self._last_final_at >= self.settle_s:
                break
            await asyncio.sleep(0.05)
        text = join_transcript_pieces(self._final_parts) or self._interim.strip()
        self._final_parts.clear()
        self._interim = ""
        self._last_partial = ""
        if text:
            logger.info("[STT] Transcript: '%s' (final, gemini)", text)
            self._put(STTBackendTranscriptEvent(text=text, is_final=True))

    # ── lifecycle ──────────────────────────────────────────────────────────
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
        for task in (self._finalize_task, self._send_task):
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
