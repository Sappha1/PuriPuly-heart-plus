from __future__ import annotations

import asyncio
import io
import logging
import struct
from dataclasses import dataclass, field
from typing import AsyncIterator

from puripuly_heart.core.stt.backend import STTBackend, STTBackendSession, STTBackendTranscriptEvent

logger = logging.getLogger(__name__)

WHISPER_MODELS = (
    "tiny",
    "base",
    "small",
    "medium",
    "large-v1",
    "large-v2",
    "large-v3",
    "large-v3-turbo-int8",
    "large-v3-turbo",
)

WHISPER_MODEL_SIZES = {
    "tiny": "74.5 MB",
    "base": "141 MB",
    "small": "463 MB",
    "medium": "1.42 GB",
    "large-v1": "2.87 GB",
    "large-v2": "2.87 GB",
    "large-v3": "2.87 GB",
    "large-v3-turbo-int8": "794 MB",
    "large-v3-turbo": "1.58 GB",
}

# Maps our BCP-47 codes to Whisper language identifiers (ISO 639-1)
_WHISPER_LANG_MAP: dict[str, str] = {
    "en": "en", "en-US": "en", "en-GB": "en",
    "ja": "ja",
    "zh": "zh", "zh-CN": "zh", "zh-TW": "zh",
    "ko": "ko",
    "fr": "fr",
    "de": "de",
    "es": "es",
    "it": "it",
    "pt": "pt", "pt-BR": "pt", "pt-PT": "pt",
    "ru": "ru",
    "nl": "nl",
    "pl": "pl",
    "sv": "sv",
    "tr": "tr",
    "vi": "vi",
    "th": "th",
    "id": "id",
    "uk": "uk",
    "ar": "ar",
    "cs": "cs",
    "da": "da",
    "fi": "fi",
    "hu": "hu",
    "ro": "ro",
    "sk": "sk",
    "el": "el",
    "bg": "bg",
    "hr": "hr",
    "lt": "lt",
    "lv": "lv",
    "et": "et",
    "nb": "no",
}


def _to_whisper_lang(lang_code: str) -> str | None:
    normalized = lang_code.strip()
    if normalized in _WHISPER_LANG_MAP:
        return _WHISPER_LANG_MAP[normalized]
    base = normalized.split("-")[0].lower()
    for key, val in _WHISPER_LANG_MAP.items():
        if key.lower() == base:
            return val
    return None


def _pcm16le_to_wav(raw_pcm: bytes, sample_rate: int) -> bytes:
    """Wrap raw PCM16LE bytes in a minimal WAV container."""
    num_channels = 1
    bits_per_sample = 16
    byte_rate = sample_rate * num_channels * bits_per_sample // 8
    block_align = num_channels * bits_per_sample // 8
    data_size = len(raw_pcm)
    header = struct.pack(
        "<4sI4s4sIHHIIHH4sI",
        b"RIFF", 36 + data_size, b"WAVE",
        b"fmt ", 16, 1, num_channels, sample_rate,
        byte_rate, block_align, bits_per_sample,
        b"data", data_size,
    )
    return header + raw_pcm


# Module-level model cache: {(model_name, device): WhisperModel}
_MODEL_CACHE: dict[tuple[str, str], object] = {}
_MODEL_LOCK = asyncio.Lock()


async def _get_or_load_model(model_name: str, device: str = "cpu"):
    key = (model_name, device)
    if key in _MODEL_CACHE:
        return _MODEL_CACHE[key]
    async with _MODEL_LOCK:
        if key in _MODEL_CACHE:
            return _MODEL_CACHE[key]
        from faster_whisper import WhisperModel  # type: ignore

        logger.info("[Whisper] loading model %r on %r (first use — may download)", model_name, device)
        # Map our model names to faster-whisper model ids
        _MODEL_ID_MAP = {
            "large-v3-turbo-int8": "Zoont/faster-whisper-large-v3-turbo-int8-ct2",
            "large-v3-turbo": "deepdml/faster-whisper-large-v3-turbo-ct2",
        }
        model_id = _MODEL_ID_MAP.get(model_name, model_name)
        loop = asyncio.get_event_loop()
        model = await loop.run_in_executor(
            None, lambda: WhisperModel(model_id, device=device, compute_type="int8")
        )
        _MODEL_CACHE[key] = model
        return model


@dataclass
class _WhisperSTTSession:
    model_name: str
    language: str | None
    sample_rate_hz: int = 16000
    device: str = "cpu"
    _audio_chunks: list[bytes] = field(default_factory=list)
    _events_queue: asyncio.Queue = field(default_factory=asyncio.Queue)
    _speech_ended: bool = False
    _inflight: int = 0  # number of transcriptions in progress

    async def send_audio(self, pcm16le: bytes) -> None:
        self._audio_chunks.append(pcm16le)

    async def on_speech_end(self, *, trailing_silence_ms: int | None = None) -> None:
        # Snapshot and clear the chunks so each utterance is independent
        raw = b"".join(self._audio_chunks)
        self._audio_chunks.clear()
        if not raw:
            return
        wav_bytes = _pcm16le_to_wav(raw, self.sample_rate_hz)
        loop = asyncio.get_event_loop()
        self._inflight += 1
        try:
            text = await loop.run_in_executor(None, self._transcribe_sync, wav_bytes)
        finally:
            self._inflight -= 1
        if text:
            await self._events_queue.put(STTBackendTranscriptEvent(text=text, is_final=True))

    def _transcribe_sync(self, wav_bytes: bytes) -> str:
        try:
            import numpy as np
            from faster_whisper import WhisperModel  # type: ignore

            # Load model (from cache if already loaded)
            key = (self.model_name, self.device)
            model = _MODEL_CACHE.get(key)
            if model is None:
                _MODEL_ID_MAP = {
                    "large-v3-turbo-int8": "Zoont/faster-whisper-large-v3-turbo-int8-ct2",
                    "large-v3-turbo": "deepdml/faster-whisper-large-v3-turbo-ct2",
                }
                model_id = _MODEL_ID_MAP.get(self.model_name, self.model_name)
                model = WhisperModel(model_id, device=self.device, compute_type="int8")
                _MODEL_CACHE[key] = model

            # Convert WAV → float32 samples via numpy
            pcm_bytes = wav_bytes[44:]  # strip 44-byte WAV header
            samples = np.frombuffer(pcm_bytes, dtype=np.int16).astype(np.float32) / 32768.0

            segments, _ = model.transcribe(
                samples,
                language=self.language,
                beam_size=5,
                temperature=0.0,
                without_timestamps=True,
                task="transcribe",
            )
            text = " ".join(seg.text for seg in segments).strip()
            logger.info("[Whisper] %r -> %r", self.language, text)
            return text
        except Exception as exc:
            logger.warning("[Whisper] transcription failed: %s", exc)
            return ""

    async def stop(self) -> None:
        # Wait for any in-flight transcription to finish so its result can be yielded
        while self._inflight > 0:
            await asyncio.sleep(0.05)
        self._speech_ended = True

    async def close(self) -> None:
        self._speech_ended = True

    async def events(self) -> AsyncIterator[STTBackendTranscriptEvent]:
        while not self._speech_ended or not self._events_queue.empty() or self._inflight > 0:
            try:
                event = self._events_queue.get_nowait()
                yield event
            except asyncio.QueueEmpty:
                if self._speech_ended and self._inflight == 0:
                    break
                await asyncio.sleep(0.05)


@dataclass
class WhisperSTTBackend:
    model_name: str
    language: str | None
    sample_rate_hz: int = 16000
    device: str = "cpu"

    async def open_session(self) -> STTBackendSession:
        return _WhisperSTTSession(
            model_name=self.model_name,
            language=self.language,
            sample_rate_hz=self.sample_rate_hz,
            device=self.device,
        )
