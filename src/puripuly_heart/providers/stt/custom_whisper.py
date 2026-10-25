"""Your own speech server: any OpenAI-compatible /audio/transcriptions endpoint.

Works with a local faster-whisper / whisper.cpp / LocalAI server or a hosted
one (OpenAI, Groq, ...). Each utterance (start of speech to our VAD's speech
end) is sent as one 16 kHz mono WAV; the reply text becomes one final
transcript. The API key is optional - local servers usually need none.
"""

from __future__ import annotations

import asyncio
import io
import logging
import wave
from dataclasses import dataclass, field
from typing import AsyncIterator

import httpx

from puripuly_heart.core.stt.backend import (
    STTBackend,
    STTBackendSession,
    STTBackendTranscriptEvent,
)

logger = logging.getLogger(__name__)

DEFAULT_CUSTOM_STT_BASE_URL = "http://127.0.0.1:8000/v1"
DEFAULT_CUSTOM_STT_MODEL = "whisper-1"
_MIN_AUDIO_MS = 250


def transcription_url(base_url: str) -> str:
    base = (base_url or DEFAULT_CUSTOM_STT_BASE_URL).strip().rstrip("/")
    if base.endswith("/audio/transcriptions"):
        return base
    return f"{base}/audio/transcriptions"


def pcm16_to_wav(pcm16le: bytes, sample_rate_hz: int) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate_hz)
        wav.writeframes(pcm16le)
    return buf.getvalue()


@dataclass(slots=True)
class CustomWhisperSTTBackend(STTBackend):
    base_url: str = DEFAULT_CUSTOM_STT_BASE_URL
    model: str = DEFAULT_CUSTOM_STT_MODEL
    api_key: str = ""
    language: str = ""
    prompt_terms: tuple[str, ...] = ()
    sample_rate_hz: int = 16000
    timeout_s: float = 30.0

    async def open_session(self) -> STTBackendSession:
        return _CustomWhisperSession(backend=self)

    async def transcribe(self, pcm16le: bytes) -> str:
        headers = {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}
        data = {"model": self.model or DEFAULT_CUSTOM_STT_MODEL, "response_format": "json"}
        language = (self.language or "").split("-")[0].lower()
        if language and language not in ("auto",):
            data["language"] = language
        if self.prompt_terms:
            data["prompt"] = ", ".join(self.prompt_terms[:50])
        files = {"file": ("speech.wav", pcm16_to_wav(pcm16le, self.sample_rate_hz), "audio/wav")}
        async with httpx.AsyncClient(timeout=self.timeout_s) as client:
            response = await client.post(
                transcription_url(self.base_url), headers=headers, data=data, files=files
            )
        if response.status_code != 200:
            detail = ""
            try:
                body = response.json()
                err = body.get("error") if isinstance(body, dict) else None
                detail = (err.get("message") if isinstance(err, dict) else str(err or "")) or ""
            except Exception:
                detail = response.text[:200]
            raise RuntimeError(
                f"Speech server request failed (status={response.status_code}, "
                f"message={detail or 'unknown error'})"
            )
        try:
            body = response.json()
            text = body.get("text") if isinstance(body, dict) else ""
        except ValueError:
            text = response.text  # response_format=text servers
        return str(text or "").strip()

    @staticmethod
    async def verify_server(base_url: str, api_key: str = "") -> bool:
        """Reachable and answering (any HTTP reply below 500 counts)."""
        headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        root = (base_url or DEFAULT_CUSTOM_STT_BASE_URL).strip().rstrip("/")
        try:
            async with httpx.AsyncClient(timeout=8.0) as client:
                response = await client.get(f"{root}/models", headers=headers)
                return response.status_code < 500 and response.status_code not in (401, 403)
        except Exception:
            return False


@dataclass(slots=True)
class _CustomWhisperSession(STTBackendSession):
    backend: CustomWhisperSTTBackend
    _buffer: bytearray = field(init=False, default_factory=bytearray, repr=False)
    _events: asyncio.Queue = field(init=False, default_factory=asyncio.Queue, repr=False)
    _closed: bool = field(init=False, default=False, repr=False)

    async def send_audio(self, pcm16le: bytes) -> None:
        if not self._closed and pcm16le:
            self._buffer.extend(pcm16le)

    async def on_speech_end(self, *, trailing_silence_ms: int | None = None) -> None:
        _ = trailing_silence_ms
        if self._closed:
            return
        pcm = bytes(self._buffer)
        self._buffer.clear()
        audio_ms = len(pcm) / 2 / self.backend.sample_rate_hz * 1000
        text = ""
        if audio_ms >= _MIN_AUDIO_MS:
            try:
                text = await self.backend.transcribe(pcm)
            except Exception as exc:
                # one failed request must not end the session: say why in the
                # log and answer this speech end with an empty final
                logger.warning("[STT] speech server failed: %s", exc)
                text = ""
        if text:
            logger.info("[STT] Transcript: '%s' (final, custom server)", text)
        # always exactly one final per speech end (empty = nothing heard), so
        # the controller's utterance pairing stays in step
        await self._events.put(
            STTBackendTranscriptEvent(text=text, is_final=True, audio_ms=audio_ms)
        )

    async def stop(self) -> None:
        await self.close()

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._buffer.clear()
        await self._events.put(None)

    async def events(self) -> AsyncIterator[STTBackendTranscriptEvent]:
        while True:
            event = await self._events.get()
            if event is None:
                break
            if isinstance(event, BaseException):
                raise event
            yield event
