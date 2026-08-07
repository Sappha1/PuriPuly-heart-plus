"""r384: "A system." kept appearing mid-dictation.

From the live log, seven times across both channels:

    [peer] Transcript: 'This is a system.'  audio_ms=3252  rms=-25.8 peak=-7.1
    [self] Transcript: 'A system.'          audio_ms=1204  rms=-30.1 peak=-14.3
    [peer] Transcript: 'A system.'          audio_ms=1812  rms=-24.2 peak=-5.7

Short, quiet audio — the local speech model's stock output on near-silence. One
of them reached the translator and went out to the chatbox appended to a real
sentence.

The blocklist already carried a bare "system" for exactly this, but it is matched
against the RAW utterance, and the model does not emit a bare "system" — it emits
"A system." So the entry never fired once.
"""
from __future__ import annotations

import pytest

from puripuly_heart.core.stt.local_qwen_hallucination import (
    is_known_local_qwen_hallucination,
)


@pytest.mark.parametrize(
    "text",
    [
        "A system.",
        "This is a system.",
        "A System",          # capitalisation as the user saw it
        "  a system  ",
        "system",            # the original entry still fires
    ],
)
def test_stock_utterances_are_suppressed(text: str) -> None:
    assert is_known_local_qwen_hallucination(text) is True, (
        f"{text!r} reaches the chatbox and the overlay again"
    )


@pytest.mark.parametrize(
    "text",
    [
        "A system of equations is what I meant.",
        "This is a system I built last year.",
        "The system is down again.",
        "I like the system",
        "a systematic approach",
        "systems",
        "My system works fine, thanks",
        # Not observed from the model, and a person could plausibly say either
        # as a complete answer to a question — so they must stay speakable.
        "The system.",
        "It is a system.",
    ],
)
def test_real_speech_containing_the_word_survives(text: str) -> None:
    """Whole utterances only. Someone saying the word inside a sentence is
    talking, and suppressing that would be far worse than the hallucination."""
    assert is_known_local_qwen_hallucination(text) is False, (
        f"{text!r} — real speech — is being swallowed"
    )


def test_the_check_is_case_and_punctuation_insensitive() -> None:
    """The original entry failed because it demanded an exact raw match; a
    matcher that can only catch one spelling of a stock phrase catches none."""
    assert is_known_local_qwen_hallucination("A SYSTEM!") is True
    assert is_known_local_qwen_hallucination("a system...") is True
