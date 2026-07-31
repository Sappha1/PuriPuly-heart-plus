"""r336: the UI's idea of "this provider ignores that setting" must match what
the provider classes actually do at the wire.

If these ever disagree, the app either greys out a working setting or accepts
input that is silently dropped — the second is what prompted this work.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from puripuly_heart.config.settings import LLMProviderName, STTProviderName
from puripuly_heart.core.provider_capabilities import (
    provider_display_key,
    stt_uses_custom_vocabulary,
    translator_uses_system_prompt,
)

LOCALES = ["en", "ja", "ko", "zh-CN"]


def _i18n(locale: str) -> dict:
    return json.loads(
        Path(f"src/puripuly_heart/data/i18n/{locale}.json").read_text(encoding="utf-8")
    )


@pytest.mark.parametrize(
    ("provider", "expected"),
    [
        (LLMProviderName.GEMINI, True),
        (LLMProviderName.OPENROUTER, True),
        (LLMProviderName.QWEN, True),
        (LLMProviderName.DEEPSEEK, True),
        (LLMProviderName.LOCAL_LLM, True),
        (LLMProviderName.DEEPL, False),
        (LLMProviderName.GOOGLE_TRANSLATE, False),
        (LLMProviderName.BING, False),
        (LLMProviderName.PAPAGO, False),
    ],
)
def test_translator_prompt_support(provider: LLMProviderName, expected: bool) -> None:
    assert translator_uses_system_prompt(provider) is expected


def test_predicate_matches_the_provider_classes() -> None:
    """USES_SYSTEM_PROMPT on the client classes is the runtime authority."""
    from puripuly_heart.providers.llm.deepl import DeepLTranslationProvider
    from puripuly_heart.providers.llm.free_web import FreeWebTranslationProvider

    for cls, provider in (
        (DeepLTranslationProvider, LLMProviderName.DEEPL),
        (FreeWebTranslationProvider, LLMProviderName.GOOGLE_TRANSLATE),
    ):
        assert getattr(cls, "USES_SYSTEM_PROMPT") is False
        assert translator_uses_system_prompt(provider) is False


def test_the_default_translator_is_one_that_drops_prompts() -> None:
    """Documents WHY this exists: the shipped default ignores the prompt."""
    from puripuly_heart.config.settings import ProviderSettings

    assert translator_uses_system_prompt(ProviderSettings().llm) is False


@pytest.mark.parametrize(
    ("provider", "expected"),
    [
        (STTProviderName.DEEPGRAM, True),
        (STTProviderName.SONIOX, True),
        (STTProviderName.LOCAL_QWEN, True),
        (STTProviderName.QWEN_ASR, False),
    ],
)
def test_stt_vocabulary_support(provider: STTProviderName, expected: bool) -> None:
    assert stt_uses_custom_vocabulary(provider) is expected


def test_vocabulary_predicate_matches_the_controller() -> None:
    """The controller decides whether terms are actually sent; same list."""
    source = Path("src/puripuly_heart/ui/controller.py").read_text(encoding="utf-8")
    start = source.index("def _stt_provider_applies_custom_vocabulary")
    body = source[start : source.index("def _stt_provider_requires_secret")]
    for provider in STTProviderName:
        in_controller = f"STTProviderName.{provider.name}," in body
        assert in_controller is stt_uses_custom_vocabulary(provider), provider


def test_unknown_provider_is_never_greyed_out() -> None:
    assert translator_uses_system_prompt("something-new") is True
    assert stt_uses_custom_vocabulary("something-new") is True
    assert translator_uses_system_prompt(None) is True


@pytest.mark.parametrize("locale", LOCALES)
def test_notice_strings_and_provider_names_exist(locale: str) -> None:
    data = _i18n(locale)
    for key in (
        "settings.capability.prompt_unsupported",
        "settings.capability.vocabulary_unsupported",
        "settings.section.prompt_intro",
    ):
        assert data.get(key), f"{locale}: {key}"
    assert "{provider}" in data["settings.capability.prompt_unsupported"]
    assert "{provider}" in data["settings.capability.vocabulary_unsupported"]
    # Every provider the notice can name must have a display string.
    for provider in (*LLMProviderName, *STTProviderName):
        key = provider_display_key(provider)
        assert data.get(key), f"{locale}: missing {key}"
