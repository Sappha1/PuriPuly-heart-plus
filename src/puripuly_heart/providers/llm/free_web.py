from __future__ import annotations

import asyncio
import logging
from uuid import UUID

from puripuly_heart.domain.models import Translation

logger = logging.getLogger(__name__)

# BCP-47 → translators library language code
_LANG_MAP: dict[str, str] = {
    "en": "en", "en-US": "en", "en-GB": "en",
    "ja": "ja",
    "zh": "zh", "zh-CN": "zh", "zh-TW": "zh-TW",
    "ko": "ko",
    "fr": "fr", "fr-FR": "fr",
    "de": "de", "de-DE": "de",
    "es": "es", "es-ES": "es", "es-MX": "es",
    "it": "it", "it-IT": "it",
    "pt": "pt", "pt-BR": "pt", "pt-PT": "pt",
    "ru": "ru",
    "ar": "ar",
    "nl": "nl",
    "pl": "pl",
    "sv": "sv",
    "tr": "tr",
    "vi": "vi",
    "th": "th",
    "id": "id",
    "uk": "uk",
    "cs": "cs",
    "da": "da",
    "fi": "fi",
    "hu": "hu",
    "ro": "ro",
    "sk": "sk",
    "el": "el",
    "bg": "bg",
    "hr": "hr",
    "lt": "lt",
    "lv": "lv",
    "et": "et",
    "sl": "sl",
    "nb": "no",
}


def _to_translator_lang(lang_code: str) -> str:
    normalized = lang_code.strip()
    if normalized in _LANG_MAP:
        return _LANG_MAP[normalized]
    base = normalized.split("-")[0].lower()
    for key, val in _LANG_MAP.items():
        if key.lower() == base:
            return val
    return base


class FreeWebTranslationProvider:
    """Google / Bing / Papago translation via the `translators` library (no API key)."""

    def __init__(self, translator: str) -> None:
        self._translator = translator  # "google", "bing", "papago"

    def _translate_sync(self, text: str, from_lang: str, to_lang: str) -> str:
        from translators import translate_text  # type: ignore

        try:
            result = translate_text(
                query_text=text,
                translator=self._translator,
                from_language=from_lang,
                to_language=to_lang,
            )
            return str(result).strip()
        except Exception as exc:
            logger.warning("[%s] translation failed: %s", self._translator, exc)
            return ""

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
        from_lang = _to_translator_lang(source_language) if source_language else "auto"
        to_lang = _to_translator_lang(target_language) if target_language else "en"
        logger.info(
            "[%s] translate %s->%s (%s->%s): %r",
            self._translator, source_language, target_language, from_lang, to_lang, text,
        )
        loop = asyncio.get_event_loop()
        try:
            translated = await asyncio.wait_for(
                loop.run_in_executor(None, self._translate_sync, text, from_lang, to_lang),
                timeout=10.0,
            )
        except asyncio.TimeoutError:
            logger.warning("[%s] translation timed out after 10s (service may be blocked)", self._translator)
            translated = ""
        logger.info("[%s] result: %r", self._translator, translated)
        return Translation(utterance_id=utterance_id, text=translated)

    async def close(self) -> None:
        pass
