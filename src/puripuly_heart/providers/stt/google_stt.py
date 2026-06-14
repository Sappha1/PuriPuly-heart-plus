from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import AsyncIterator

from puripuly_heart.core.stt.backend import STTBackend, STTBackendSession, STTBackendTranscriptEvent

logger = logging.getLogger(__name__)

# BCP-47 → Google STT locale tag
_GOOGLE_LANG_MAP: dict[str, str] = {
    "en": "en-US", "en-US": "en-US", "en-GB": "en-GB",
    "ja": "ja-JP",
    "zh": "zh-CN", "zh-CN": "zh-CN", "zh-TW": "zh-TW",
    "ko": "ko-KR",
    "fr": "fr-FR",
    "de": "de-DE",
    "es": "es-ES",
    "it": "it-IT",
    "pt": "pt-BR", "pt-BR": "pt-BR", "pt-PT": "pt-PT",
    "ru": "ru-RU",
    "nl": "nl-NL",
    "pl": "pl-PL",
    "sv": "sv-SE",
    "tr": "tr-TR",
    "vi": "vi-VN",
    "th": "th-TH",
    "id": "id-ID",
    "uk": "uk-UA",
    "ar": "ar-SA",
    "cs": "cs-CZ",
    "da": "da-DK",
    "fi": "fi-FI",
    "hu": "hu-HU",
    "ro": "ro-RO",
    "sk": "sk-SK",
    "el": "el-GR",
    "bg": "bg-BG",
    "hr": "hr-HR",
    "lt": "lt-LT",
    "lv": "lv-LV",
    "et": "et-EE",
    "nb": "nb-NO",
}


def _to_google_lang(lang_code: str) -> str:
    normalized = lang_code.strip()
    if normalized in _GOOGLE_LANG_MAP:
        return _GOOGLE_LANG_MAP[normalized]
    base = normalized.split("-")[0].lower()
    for key, val in _GOOGLE_LANG_MAP.items():
        if key.lower() == base:
            return val
    return normalized


@dataclass
class _GoogleSTTSession:
    language: str
    sample_rate_hz: int = 16000
    _audio_chunks: list[bytes] = field(default_factory=list)
    _events_queue: asyncio.Queue = field(default_factory=asyncio.Queue)
    _speech_ended: bool = False
    _closed: bool = False

    async def send_audio(self, pcm16le: bytes) -> None:
        self._audio_chunks.append(pcm16le)

    async def on_speech_end(self, *, trailing_silence_ms: int | None = None) -> None:
        raw = b"".join(self._audio_chunks)
        loop = asyncio.get_event_loop()
        text = await loop.run_in_executor(None, self._transcribe_sync, raw)
        if text:
            await self._events_queue.put(STTBackendTranscriptEvent(text=text, is_final=True))
        self._speech_ended = True

    def _transcribe_sync(self, raw_pcm: bytes) -> str:
        try:
            import speech_recognition as sr  # type: ignore

            r = sr.Recognizer()
            audio = sr.AudioData(raw_pcm, self.sample_rate_hz, 2)  # 16-bit = 2 bytes/sample
            text = r.recognize_google(audio, language=self.language)
            logger.info("[GoogleSTT] %r -> %r", self.language, text)
            return str(text).strip()
        except Exception as exc:
            logger.warning("[GoogleSTT] transcription failed: %s", exc)
            return ""

    async def stop(self) -> None:
        self._speech_ended = True

    async def close(self) -> None:
        self._closed = True

    async def events(self) -> AsyncIterator[STTBackendTranscriptEvent]:
        while not self._speech_ended or not self._events_queue.empty():
            try:
                event = self._events_queue.get_nowait()
                yield event
            except asyncio.QueueEmpty:
                if self._speech_ended:
                    break
                await asyncio.sleep(0.05)


@dataclass
class GoogleSTTBackend:
    language: str
    sample_rate_hz: int = 16000

    async def open_session(self) -> STTBackendSession:
        return _GoogleSTTSession(
            language=self.language,
            sample_rate_hz=self.sample_rate_hz,
        )
