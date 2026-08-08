"""r387: borderline stock phrases need the noise band as a second witness.

The user's constraint: the word must stay speakable — so "The system." /
"It is a system." are not blocked outright (a person can say either as a
complete answer). But the live log showed four bare 'The system.' lines
reaching chat as noise in one day, two of them on the build that narrowed
the list.

The same log carries the discriminator. Every noise emission sat in a
1140-1972ms segment — five at exactly 1140.0ms, the VAD's minimum commit
length — while the one real sentence containing the word ran 3444ms. So the
borderline forms are suppressed only when whole-line AND the segment is under
NOISE_BAND_MAX_AUDIO_MS. Unknown duration always passes: the conservative
side of every ambiguity is "let speech through".
"""
from __future__ import annotations

from uuid import uuid4

import pytest

from puripuly_heart.core.stt.backend import STTBackendTranscriptEvent
from puripuly_heart.core.stt.local_qwen_hallucination import (
    NOISE_BAND_MAX_AUDIO_MS,
    is_known_local_qwen_hallucination,
)


@pytest.mark.parametrize(
    ("text", "audio_ms"),
    [
        ("The system.", 1140.0),   # the exact observed noise emissions
        ("The system.", 1300.0),
        ("the system", 1972.0),
        ("It is a system.", 1500.0),
        ("THE SYSTEM!", 1140.0),   # case/punctuation never rescue noise
    ],
)
def test_borderline_forms_in_the_noise_band_are_suppressed(text, audio_ms) -> None:
    assert is_known_local_qwen_hallucination(text, audio_ms=audio_ms) is True


@pytest.mark.parametrize(
    ("text", "audio_ms"),
    [
        ("The system.", 3444.0),         # deliberate speech: longer segment
        ("The system.", NOISE_BAND_MAX_AUDIO_MS),  # boundary is exclusive
        ("The system.", None),           # unknown duration: never suppress
        ("The system.", 0.0),            # degenerate duration: never suppress
        ("It is a system.", 2500.0),
        # Embedded uses are untouchable regardless of duration — the observed
        # real sentence, at the exact noise-band length.
        ("what do you mean about the system?", 1140.0),
        ("The system is down", 1140.0),
    ],
)
def test_speech_is_never_suppressed(text, audio_ms) -> None:
    assert is_known_local_qwen_hallucination(text, audio_ms=audio_ms) is False


def test_the_unconditional_forms_ignore_duration() -> None:
    """"A system." / "System." are observed stock noise and stay blocked as
    whole lines whatever the duration — r387 must not loosen r384."""
    assert is_known_local_qwen_hallucination("A system.", audio_ms=None) is True
    assert is_known_local_qwen_hallucination("System.", audio_ms=99999.0) is True


def test_the_event_carries_its_audio_duration() -> None:
    """The sherpa session knows the decoded duration when it builds the final
    event; it must travel on the event or the gate can never fire. Default is
    None so cloud providers are untouched."""
    event = STTBackendTranscriptEvent(text="x", is_final=True, audio_ms=1140.0)
    assert event.audio_ms == 1140.0
    assert STTBackendTranscriptEvent(text="x", is_final=True).audio_ms is None


@pytest.mark.asyncio
async def test_provider_suppresses_a_noise_band_final_end_to_end() -> None:
    """Through ManagedSTTProvider: a 'The system.' final in the noise band is
    suppressed; the identical text with a speech-length duration passes."""
    from puripuly_heart.config.settings import STTProviderName
    from puripuly_heart.core.stt.controller import ManagedSTTProvider, STTFinalEvent
    from tests.core.test_stt_controller import EventOnlySession, FakeBackend, _next_event

    async def run(audio_ms: float) -> tuple[object | None, list[object]]:
        notifications: list[object] = []
        provider = ManagedSTTProvider(
            backend=FakeBackend(),
            sample_rate_hz=16000,
            stt_provider_name=STTProviderName.LOCAL_QWEN,
            on_final_transcript_suppressed=notifications.append,
        )
        provider._pending_final_utterance_ids.append(uuid4())
        await provider._consume_session_events(
            EventOnlySession(
                [
                    STTBackendTranscriptEvent(
                        text="The system.", is_final=True, audio_ms=audio_ms
                    )
                ]
            )
        )
        if notifications:
            return None, notifications
        event = await _next_event(provider.events())
        return event, notifications

    event, notifications = await run(1140.0)
    assert event is None and len(notifications) == 1, (
        "a noise-band 'The system.' reached the chat"
    )

    event, notifications = await run(3444.0)
    assert notifications == [] and isinstance(event, STTFinalEvent), (
        "a speech-length 'The system.' was swallowed"
    )
    assert event.transcript.text == "The system."
