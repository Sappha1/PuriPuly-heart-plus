from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from uuid import UUID

from puripuly_heart.domain.models import Translation

logger = logging.getLogger(__name__)

# Maps BCP-47-like codes used internally to DeepL source language codes.
# DeepL source codes are 2-letter; None means auto-detect.
_SOURCE_LANG_MAP: dict[str, str | None] = {
    "en": "EN",
    "ja": "JA",
    "ko": "KO",
    "zh-CN": "ZH",
    "zh-TW": "ZH",
    "zh": "ZH",
    "de": "DE",
    "fr": "FR",
    "es": "ES",
    "it": "IT",
    "pt": "PT",
    "pt-BR": "PT",
    "ru": "RU",
    "nl": "NL",
    "pl": "PL",
    "cs": "CS",
    "da": "DA",
    "fi": "FI",
    "hu": "HU",
    "nb": "NB",
    "ro": "RO",
    "sk": "SK",
    "sv": "SV",
    "tr": "TR",
    "uk": "UK",
    "bg": "BG",
    "el": "EL",
    "et": "ET",
    "lv": "LV",
    "lt": "LT",
    "sl": "SL",
    "id": "ID",
}

# Maps BCP-47-like codes to DeepL target language codes.
# Target codes can be more specific (EN-US vs EN-GB).
_TARGET_LANG_MAP: dict[str, str] = {
    "en": "EN-US",
    "en-US": "EN-US",
    "en-GB": "EN-GB",
    "ja": "JA",
    "ko": "KO",
    "zh-CN": "ZH-HANS",
    "zh-TW": "ZH-HANT",
    "zh": "ZH-HANS",
    "de": "DE",
    "fr": "FR",
    "es": "ES",
    "it": "IT",
    "pt": "PT-BR",
    "pt-BR": "PT-BR",
    "pt-PT": "PT-PT",
    "ru": "RU",
    "nl": "NL",
    "pl": "PL",
    "cs": "CS",
    "da": "DA",
    "fi": "FI",
    "hu": "HU",
    "nb": "NB",
    "ro": "RO",
    "sk": "SK",
    "sv": "SV",
    "tr": "TR",
    "uk": "UK",
    "bg": "BG",
    "el": "EL",
    "et": "ET",
    "lv": "LV",
    "lt": "LT",
    "sl": "SL",
    "id": "ID",
}


def _to_deepl_source(lang_code: str) -> str | None:
    normalized = lang_code.strip()
    if normalized in _SOURCE_LANG_MAP:
        return _SOURCE_LANG_MAP[normalized]
    # Try base language (e.g. "zh-Hant" -> "zh")
    base = normalized.split("-")[0].lower()
    for key, val in _SOURCE_LANG_MAP.items():
        if key.lower() == base:
            return val
    return None  # auto-detect


def _to_deepl_target(lang_code: str) -> str:
    normalized = lang_code.strip()
    if normalized in _TARGET_LANG_MAP:
        return _TARGET_LANG_MAP[normalized]
    base = normalized.split("-")[0].lower()
    for key, val in _TARGET_LANG_MAP.items():
        if key.lower() == base:
            return val
    # Fallback: uppercase the code and hope DeepL accepts it
    return normalized.upper()


@dataclass(slots=True)
class DeepLTranslationProvider:
    api_key: str
    _executor: object = field(init=False, default=None, repr=False)

    def _translate_sync(self, text: str, source_lang: str | None, target_lang: str) -> str:
        import deepl  # type: ignore

        translator = deepl.Translator(self.api_key)
        result = translator.translate_text(
            text,
            source_lang=source_lang,
            target_lang=target_lang,
        )
        return str(result)

    async def translate(
        self,
        *,
        utterance_id: UUID,
        text: str,
        system_prompt: str,
        source_language: str,
        target_language: str,
        context: str = "",
    ) -> Translation:
        source_lang = _to_deepl_source(source_language)
        target_lang = _to_deepl_target(target_language)
        logger.info(
            "[DeepL] translate %s -> %s (%s -> %s): %r",
            source_language, target_language, source_lang, target_lang, text,
        )
        loop = asyncio.get_event_loop()
        translated = await loop.run_in_executor(
            None,
            self._translate_sync,
            text,
            source_lang,
            target_lang,
        )
        translated = translated.strip()
        logger.info("[DeepL] result: %r", translated)
        return Translation(utterance_id=utterance_id, text=translated)

    async def warmup(self) -> None:
        pass

    async def close(self) -> None:
        pass

    @staticmethod
    async def verify_api_key(api_key: str) -> bool:
        if not api_key:
            return False
        try:
            import deepl  # type: ignore

            loop = asyncio.get_event_loop()

            def _check() -> bool:
                translator = deepl.Translator(api_key)
                usage = translator.get_usage()
                return usage is not None

            return await loop.run_in_executor(None, _check)
        except Exception:
            return False

    @staticmethod
    async def fetch_usage(api_key: str) -> tuple[int, int] | None:
        """Return (characters_used, characters_limit) or None on error."""
        if not api_key:
            return None
        try:
            import deepl  # type: ignore

            loop = asyncio.get_event_loop()

            def _get() -> tuple[int, int] | None:
                translator = deepl.Translator(api_key)
                usage = translator.get_usage()
                if usage is None:
                    return None
                char = usage.character
                if char is None:
                    return None
                return int(char.count), int(char.limit)

            return await loop.run_in_executor(None, _get)
        except Exception:
            return None
