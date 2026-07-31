"""Which provider honours which setting.

r336. Several settings only do anything for some providers, and the app never
said so: with the DEFAULT translator (Google Translate) the whole system
prompt and the context toggle are dropped at the wire, and custom vocabulary
is ignored by every speech engine except three. A user could write a persona,
turn context on, add hotwords, and change nothing at all.

The runtime authority is ``USES_SYSTEM_PROMPT`` on the provider classes; this
module mirrors it as a settings-level predicate so the UI can grey a control
out before a request is ever made. ``tests/core/test_provider_capabilities.py``
asserts the two never disagree.
"""
from __future__ import annotations

from puripuly_heart.config.settings import LLMProviderName, STTProviderName

# DeepL's API and the free web engines accept only text + a language pair.
# There is nowhere to put a system prompt or conversation context.
PROMPTLESS_TRANSLATORS: frozenset[LLMProviderName] = frozenset(
    {
        LLMProviderName.DEEPL,
        LLMProviderName.GOOGLE_TRANSLATE,
        LLMProviderName.BING,
        LLMProviderName.PAPAGO,
    }
)

# Engines that accept a term list. Local Qwen takes them as hotwords (capped
# lower than the cloud engines — see LOCAL_QWEN_MAX_HOTWORDS).
CUSTOM_VOCABULARY_ENGINES: frozenset[STTProviderName] = frozenset(
    {
        STTProviderName.DEEPGRAM,
        STTProviderName.LOCAL_QWEN,
        STTProviderName.SONIOX,
    }
)


def translator_uses_system_prompt(provider: LLMProviderName | str | None) -> bool:
    """True when the system prompt and context reach the translation server."""
    if provider is None:
        return True
    try:
        resolved = LLMProviderName(provider)
    except ValueError:
        return True  # unknown provider: assume it does, never grey out wrongly
    return resolved not in PROMPTLESS_TRANSLATORS


def stt_uses_custom_vocabulary(provider: STTProviderName | str | None) -> bool:
    """True when the custom term list is actually sent to the speech engine."""
    if provider is None:
        return True
    try:
        resolved = STTProviderName(provider)
    except ValueError:
        return True
    return resolved in CUSTOM_VOCABULARY_ENGINES


def provider_display_key(provider: LLMProviderName | STTProviderName | str) -> str:
    """i18n key holding the provider's human name, e.g. 'provider.deepl'."""
    value = getattr(provider, "value", provider)
    return f"provider.{value}"
