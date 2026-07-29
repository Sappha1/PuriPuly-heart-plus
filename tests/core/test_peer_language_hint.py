"""The peer channel must never force a decode language (r310).

Measured on the shipped Qwen model with SHORT segments — what the VAD
actually emits (long clean clips transcribe correctly whatever the hint,
which is how r308 slipped through):

    EN speech 1.3s, hint=None    -> "Hear me."               (transcribed)
    EN speech 1.3s, hint=Chinese -> "听我。"                   (TRANSLATED)
    JA speech 1.3s, hint=English -> "The way of the king."    (TRANSLATED)

Forcing a hint turns the recognizer into a translator, so foreign speech
comes out looking like the expected language and the peer language filter
can no longer tell it apart.
"""
from __future__ import annotations

import pytest

from puripuly_heart.app.wiring import resolve_peer_stt_config
from puripuly_heart.config.settings import STTProviderName, new_settings_for_first_run


def _cfg(peer_language: str, *, auto_detect: bool = False):
    settings = new_settings_for_first_run("en-US")
    settings.provider.peer_stt = STTProviderName.LOCAL_QWEN
    settings.languages.peer_source_language = peer_language
    settings.languages.auto_detect_peer_voice = auto_detect
    return resolve_peer_stt_config(settings)


@pytest.mark.parametrize("peer_language", ["en", "zh-CN", "ja", "ko", ""])
def test_peer_channel_never_forces_a_language(peer_language: str) -> None:
    assert _cfg(peer_language).language_hint is None


def test_auto_detect_also_hint_free() -> None:
    assert _cfg("zh-CN", auto_detect=True).language_hint is None


def test_pinned_language_still_reaches_the_filter() -> None:
    # The pin keeps its OTHER job: telling the app what to expect (used by
    # the source-language filter while translating).
    assert _cfg("ja").source_language == "ja"
