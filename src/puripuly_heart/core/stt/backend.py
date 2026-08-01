from __future__ import annotations

from dataclasses import dataclass
from typing import AsyncIterator, Protocol, runtime_checkable

import numpy as np


@dataclass(frozen=True, slots=True)
class STTBackendTranscriptEvent:
    text: str
    is_final: bool
    # r318: unit-norm voiceprint of the segment (tuple for hashability under
    # frozen slots), None when speaker ID is off / segment too short.
    speaker_embedding: tuple[float, ...] | None = None
    # r349: how much audio produced that voiceprint. Short segments are
    # matchable but not trustworthy enough to enroll somebody new.
    speaker_seconds: float = 0.0
    # r346: what language the recognizer says the AUDIO was. A language-hinted
    # LLM recognizer covertly translates out-of-language speech, so the
    # transcript's script cannot be trusted for language filtering.
    detected_language: str | None = None


class STTBackendSession(Protocol):
    async def send_audio(self, pcm16le: bytes) -> None: ...
    async def on_speech_end(
        self, *, trailing_silence_ms: int | None = None
    ) -> None: ...  # Backend-specific end-of-speech handling
    async def stop(self) -> None: ...
    async def close(self) -> None: ...
    async def events(self) -> AsyncIterator[STTBackendTranscriptEvent]: ...


@runtime_checkable
class STTBackendFloat32Session(Protocol):
    async def send_audio_f32(self, samples_f32: np.ndarray) -> None: ...


class STTBackend(Protocol):
    async def open_session(self) -> STTBackendSession: ...
