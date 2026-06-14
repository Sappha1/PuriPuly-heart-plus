from __future__ import annotations

import puripuly_heart.core.stt.custom_vocab as custom_vocab_module
from puripuly_heart.config.settings import AppSettings
from puripuly_heart.core.stt.custom_vocab import get_effective_custom_terms


def test_get_effective_custom_terms_uses_seeded_defaults_for_fresh_settings() -> None:
    settings = AppSettings()

    assert get_effective_custom_terms(settings, "ko") == ["아이리", "시나노"]
    assert get_effective_custom_terms(settings, "en") == ["airi", "shinano"]
    assert get_effective_custom_terms(settings, "zh-CN") == ["airi", "shinano"]


def test_get_effective_custom_terms_reads_current_language_bucket_only() -> None:
    settings = AppSettings()
    settings.stt.custom_vocabulary_enabled = True
    settings.stt.custom_terms = {
        "ko": ["Puripuly", "VRChat"],
        "en": ["Soniox", "OSC"],
    }

    assert get_effective_custom_terms(settings, "ko") == ["Puripuly", "VRChat"]


def test_get_effective_custom_terms_preserves_first_occurrence_order_when_deduping() -> None:
    settings = AppSettings()
    settings.stt.custom_vocabulary_enabled = True
    settings.stt.custom_terms = {
        "ko": ["VRChat", "Puripuly", "VRChat", "OSC", "Puripuly", "Soniox"],
    }

    assert get_effective_custom_terms(settings, "ko") == ["VRChat", "Puripuly", "OSC", "Soniox"]


def test_get_effective_custom_terms_trims_whitespace_and_drops_empty_values() -> None:
    settings = AppSettings()
    settings.stt.custom_vocabulary_enabled = True
    settings.stt.custom_terms = {
        "ko": ["  Puripuly  ", "", "   ", "\tVRChat\t", "\nSoniox\n", "OSC"],
    }

    assert get_effective_custom_terms(settings, "ko") == ["Puripuly", "VRChat", "Soniox", "OSC"]


def test_get_effective_custom_terms_is_stable_and_respects_disabled_flag() -> None:
    settings = AppSettings()
    settings.stt.custom_vocabulary_enabled = True
    settings.stt.custom_terms = {
        "ko": ["  VRChat  ", "Puripuly", "VRChat", "  ", "OSC"],
        "en": ["Ignored"],
    }

    first = get_effective_custom_terms(settings, "ko")
    second = get_effective_custom_terms(settings, "ko")

    assert first == ["VRChat", "Puripuly", "OSC"]
    assert second == first
    assert second is not first

    settings.stt.custom_vocabulary_enabled = False

    assert get_effective_custom_terms(settings, "ko") == []


def test_get_effective_custom_terms_caps_to_100_terms() -> None:
    settings = AppSettings()
    settings.stt.custom_vocabulary_enabled = True
    settings.stt.custom_terms = {
        "ko": [f"term-{i:03d}" for i in range(120)],
    }

    effective_terms = get_effective_custom_terms(settings, "ko")

    assert len(effective_terms) == 100
    assert effective_terms[0] == "term-000"
    assert effective_terms[-1] == "term-099"


def test_get_effective_local_qwen_hotwords_uses_smaller_cap_and_sanitizes_commas() -> None:
    settings = AppSettings()
    settings.stt.custom_vocabulary_enabled = True
    settings.stt.custom_terms = {
        "ko": [
            " Puripuly ",
            "VRChat, Japan",
            "VRChat   Japan",
            *[f"term-{i:02d}" for i in range(20)],
        ],
    }

    assert hasattr(custom_vocab_module, "LOCAL_QWEN_MAX_HOTWORDS")
    assert hasattr(custom_vocab_module, "get_effective_local_qwen_hotwords")

    hotwords = custom_vocab_module.get_effective_local_qwen_hotwords(settings, "ko")

    assert hotwords[:2] == ["Puripuly", "VRChat Japan"]
    assert len(hotwords) == custom_vocab_module.LOCAL_QWEN_MAX_HOTWORDS
    assert hotwords[-1] == f"term-{custom_vocab_module.LOCAL_QWEN_MAX_HOTWORDS - 3:02d}"
