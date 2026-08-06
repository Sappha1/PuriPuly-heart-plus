"""r378: an explicit peer language must reach the local recogniser.

Reported as an English-speaking friend appearing in Chinese characters with
translation off. Nothing was translating — the peer channel decodes with no
language hint, so the model's own detection chose Chinese and wrote English
speech down in Chinese.

Measured on the shipped model over synthesised English clips:

    hint=None     0/5 Chinese     "Oh, I see. Right, so."
    hint=English  0/5 Chinese
    hint=Chinese  3/5 Chinese     "哦，我 see， right， so."

The last line is the reported symptom verbatim. Hint-free therefore stays the
default (r310: a WRONG hint turns this model into a translator), but discarding
an EXPLICIT pin as well left the setting doing nothing at all.
"""
from __future__ import annotations

from puripuly_heart.app import wiring
from puripuly_heart.config.settings import AppSettings


def _settings(*, auto_detect: bool, peer_language: str) -> AppSettings:
    s = AppSettings()
    s.provider.peer_stt = wiring.STTProviderName.LOCAL_QWEN
    s.languages.source_language = "en"
    s.languages.peer_source_language = peer_language
    s.languages.auto_detect_peer_voice = auto_detect
    return s


def test_auto_detect_still_decodes_hint_free() -> None:
    """r310's protection, unchanged: with Auto Detect on, a wrong hint would
    turn the recogniser into a translator and make foreign speech LOOK like the
    expected language."""
    resolved = wiring.resolve_peer_stt_config(_settings(auto_detect=True, peer_language="zh-CN"))
    assert resolved.language_hint is None


def test_an_explicit_pin_reaches_the_recognizer() -> None:
    """r378: this was silently discarded, so choosing a peer language did
    nothing whatsoever for the local model."""
    resolved = wiring.resolve_peer_stt_config(_settings(auto_detect=False, peer_language="en"))
    assert resolved.language_hint == "English", (
        "pinning the peer language still does not reach the model, so someone "
        "whose partner is transcribed into the wrong language has no way out"
    )

    resolved = wiring.resolve_peer_stt_config(_settings(auto_detect=False, peer_language="zh-CN"))
    assert resolved.language_hint == "Chinese"


def test_the_pin_is_the_peer_language_not_your_own() -> None:
    """effective_peer_source falls back to YOUR language when auto-detect is on;
    the hint must come from what the PARTNER speaks, or pinning would feed the
    recogniser the wrong language entirely."""
    s = _settings(auto_detect=False, peer_language="ja")
    s.languages.source_language = "en"
    assert wiring.resolve_peer_stt_config(s).language_hint == "Japanese"


def test_a_pin_the_model_does_not_know_is_dropped_rather_than_forced() -> None:
    s = _settings(auto_detect=False, peer_language="xx-YY")
    assert wiring.resolve_peer_stt_config(s).language_hint is None
