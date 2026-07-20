from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass, field
from typing import ClassVar
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


# DeepL reports detected sources as uppercase ISO codes ("JA", "ZH", "EN").
# Map back to the app's codes so downstream romanization/labels know what the
# speech ACTUALLY was when the user runs voice auto-detection.
def _from_deepl_detected(code: str | None) -> str | None:
    if not code:
        return None
    c = str(code).strip().upper()
    return {"ZH": "zh-CN"}.get(c, c.lower()) or None


@dataclass(slots=True)
class DeepLTranslationProvider:
    # DeepL's API takes only text + language pair. The hub still hands every
    # provider a system_prompt/context; this flag tells the request inspector
    # they are NOT sent to the server.
    USES_SYSTEM_PROMPT: ClassVar[bool] = False

    api_key: str
    _executor: object = field(init=False, default=None, repr=False)

    def _translate_sync(self, text: str, source_lang: str | None,
                        target_lang: str) -> tuple[str, str | None]:
        import deepl  # type: ignore

        translator = deepl.Translator(self.api_key)
        result = translator.translate_text(
            text,
            source_lang=source_lang,
            target_lang=target_lang,
        )
        detected = getattr(result, "detected_source_lang", None)
        return str(result), detected


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
        translated, detected = await loop.run_in_executor(
            None,
            self._translate_sync,
            text,
            source_lang,
            target_lang,
        )
        translated = translated.strip()
        # DeepL's LLM-backed models occasionally hallucinate a language
        # label onto the end of the output ("…我是自由的Simplified Chinese
        # (Mainland)" was returned verbatim by the API). Trim it.
        # Only when the label sits DIRECTLY after a CJK character — a real
        # English translation ending in the word "Chinese" must survive.
        _trim = re.sub(
            r"(?<=[一-鿿])\s*(?:Simplified|Traditional)?\s*Chinese"
            r"(?:\s*\([^)]{0,30}\))?\s*$",
            "", translated, flags=re.IGNORECASE)
        if _trim != translated and _trim:
            logger.warning("[DeepL] trimmed hallucinated language label: %r",
                           translated[len(_trim):])
            translated = _trim.strip()
        detected_code = _from_deepl_detected(detected)
        logger.info("[DeepL] result: %r (detected=%s)", translated, detected_code)
        return Translation(utterance_id=utterance_id, text=translated,
                           source_language=detected_code)

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
