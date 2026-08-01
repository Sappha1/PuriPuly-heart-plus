"""r346: the peer language filter trusts the audio, not the transcript.

The reported case: peer speech filtered to Chinese, a friend spoke ENGLISH,
and the line passed anyway — because the Chinese-hinted recognizer covertly
translated the English into Han characters, which is all the old text-level
filter could see. The recognizer's own detected language now rides along and
wins.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from puripuly_heart.core.orchestrator.hub import ClientHub
from puripuly_heart.providers.stt.local_qwen_sherpa import LocalQwenSherpaSTTBackend


def _filter(text: str, *, detected: str | None, targets: list[str]) -> bool:
    state = SimpleNamespace(
        filter_peer_by_target_languages=True,
        target_language=targets[0],
        extra_target_languages=targets[1:],
    )
    return ClientHub._peer_text_passes_language_filter(
        state, text, detected_language=detected
    )


def test_english_audio_is_dropped_even_when_transcribed_as_chinese() -> None:
    """The exact screenshot: '切换到我的耳phone。' from English speech."""
    assert _filter("切换到我的耳phone。喂。", detected="en", targets=["zh"]) is False


def test_chinese_audio_passes() -> None:
    assert _filter("你好，今天怎么样？", detected="zh", targets=["zh"]) is True


def test_language_aliases_are_normalized() -> None:
    assert _filter("你好", detected="cmn", targets=["zh"]) is True
    assert _filter("你好", detected="zh", targets=["cmn"]) is True
    assert _filter("こんにちは", detected="jpn", targets=["ja"]) is True


def test_audio_language_beats_the_script_in_both_directions() -> None:
    # Chinese speech that the model happened to romanize: script check would
    # fail it, the audio language saves it.
    assert _filter("ni hao ma", detected="zh", targets=["zh"]) is True


def test_no_detected_language_falls_back_to_the_script_heuristic() -> None:
    assert _filter("你好", detected=None, targets=["zh"]) is True
    assert _filter("hello there", detected=None, targets=["zh"]) is False


def test_audio_filtering_works_for_non_cjk_targets_too() -> None:
    """The script heuristic passes everything for non-CJK targets (it cannot
    tell French from English by characters) — the audio language can."""
    assert _filter("bonjour tout le monde", detected="fr", targets=["fr"]) is True
    assert _filter("hello everyone", detected="en", targets=["fr"]) is False


def test_filter_off_passes_everything() -> None:
    state = SimpleNamespace(
        filter_peer_by_target_languages=False,
        target_language="zh",
        extra_target_languages=[],
    )
    assert ClientHub._peer_text_passes_language_filter(
        state, "hello", detected_language="en"
    ) is True


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("en", "en"),
        ("<|zh|>", "zh"),
        (" EN-us ", "en"),
        ("auto", None),
        ("", None),
        (None, None),
        ("unknown", None),
    ],
)
def test_detected_language_normalization(raw, expected) -> None:
    assert LocalQwenSherpaSTTBackend._normalize_detected_language(raw) == expected
