from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from uuid import UUID

from .models import ChannelId, Transcript


def _validate_channel(channel: str) -> None:
    if channel not in ("self", "peer"):
        raise ValueError(f"invalid channel: {channel!r}")


class STTSessionState(str, Enum):
    CONNECTING = "CONNECTING"
    DISCONNECTED = "DISCONNECTED"
    STREAMING = "STREAMING"
    DRAINING = "DRAINING"


class STTEventType(str, Enum):
    PARTIAL = "STT_PARTIAL"
    FINAL = "STT_FINAL"
    ERROR = "STT_ERROR"
    SESSION_STATE = "STT_SESSION_STATE"


@dataclass(frozen=True, slots=True)
class STTPartialEvent:
    utterance_id: UUID
    transcript: Transcript
    type: STTEventType = STTEventType.PARTIAL

    def __post_init__(self) -> None:
        if self.transcript.is_final:
            raise ValueError("STTPartialEvent requires transcript.is_final == False")

    @property
    def channel(self) -> ChannelId:
        return self.transcript.channel


@dataclass(frozen=True, slots=True)
class STTFinalEvent:
    utterance_id: UUID
    transcript: Transcript
    type: STTEventType = STTEventType.FINAL

    def __post_init__(self) -> None:
        if not self.transcript.is_final:
            raise ValueError("STTFinalEvent requires transcript.is_final == True")

    @property
    def channel(self) -> ChannelId:
        return self.transcript.channel


@dataclass(frozen=True, slots=True)
class STTErrorEvent:
    message: str
    utterance_id: UUID | None = None
    channel: ChannelId = "self"
    runtime_log_handled: bool = False
    type: STTEventType = STTEventType.ERROR

    def __post_init__(self) -> None:
        _validate_channel(self.channel)


@dataclass(frozen=True, slots=True)
class STTSessionStateEvent:
    state: STTSessionState
    utterance_id: None = None
    channel: ChannelId = "self"
    type: STTEventType = STTEventType.SESSION_STATE

    def __post_init__(self) -> None:
        _validate_channel(self.channel)


STTEvent = STTPartialEvent | STTFinalEvent | STTErrorEvent | STTSessionStateEvent


class UIEventType(str, Enum):
    SESSION_STATE_CHANGED = "SESSION_STATE_CHANGED"
    TRANSCRIPT_PARTIAL = "TRANSCRIPT_PARTIAL"
    TRANSCRIPT_FINAL = "TRANSCRIPT_FINAL"
    TRANSLATION_DONE = "TRANSLATION_DONE"
    EXTRA_TRANSLATIONS_DONE = "EXTRA_TRANSLATIONS_DONE"
    OSC_SENT = "OSC_SENT"
    ERROR = "ERROR"


@dataclass(frozen=True, slots=True)
class UIEvent:
    type: UIEventType
    utterance_id: UUID | None = None
    payload: object | None = None
    source: str | None = None
    channel: ChannelId | None = None
    runtime_log_handled: bool = False

    def __post_init__(self) -> None:
        resolved_channel = self.channel
        if resolved_channel is None:
            payload_channel = getattr(self.payload, "channel", None)
            if payload_channel is not None:
                _validate_channel(payload_channel)
                resolved_channel = payload_channel
            else:
                resolved_channel = "self"
        else:
            _validate_channel(resolved_channel)

        object.__setattr__(self, "channel", resolved_channel)
