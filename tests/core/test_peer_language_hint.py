"""Peer STT language hint policy (r308).

Measured against the shipped Qwen model: hinting does NOT make it translate
(English audio + hint="Chinese" returned English text), while hint-free LID
mis-fires on low-SNR audio and invents fluent Chinese from English speech.
So a PINNED peer language is passed through as a hint; Auto Detect is not.
"""
from __future__ import annotations

from puripuly_heart.app.wiring import resolve_peer_stt_config
from puripuly_heart.config.settings import new_settings_for_first_run
from puripuly_heart.config.settings import STTProviderName


def _hint(peer_language: str, *, auto_detect: bool = False):
    settings = new_settings_for_first_run("en-US")
    settings.provider.peer_stt = STTProviderName.LOCAL_QWEN
    settings.languages.peer_source_language = peer_language
    settings.languages.auto_detect_peer_voice = auto_detect
    return resolve_peer_stt_config(settings).language_hint


def test_pinned_peer_language_is_hinted() -> None:
    assert _hint("en") == "English"
    assert _hint("zh-CN") == "Chinese"
    assert _hint("ja") == "Japanese"


def test_auto_detect_stays_hint_free() -> None:
    assert _hint("zh-CN", auto_detect=True) is None
    assert _hint("") is None


def test_unknown_language_gets_no_hint() -> None:
    assert _hint("xx-YY") is None
