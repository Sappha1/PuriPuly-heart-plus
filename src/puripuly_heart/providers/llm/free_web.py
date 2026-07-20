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


# The Edge browser's internal translation endpoint: no auth, no scraping, one
# POST, and — critically — reachable from mainland China (Edge operates there
# officially). The old `translators`-library bing path scraped bing.com pages:
# cn.bing.com now 301s to www.bing.com (blocked in China) and the scrape chain
# breaks with "'NoneType' object has no attribute 'xpath'" whenever Microsoft
# churns the page. Every major Chinese OSS translator uses this endpoint.
_EDGE_TRANSLATE_URL = "https://edge.microsoft.com/translate/translatetext"
_EDGE_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36 Edg/131.0.0.0"
)


def _to_edge_lang(code: str) -> str:
    """Microsoft Translator BCP-47: zh keeps its script tag, others drop region."""
    low = (code or "").strip().lower()
    if not low or low == "auto":
        return ""
    if low in ("zh", "zh-cn", "zh-hans", "zh-sg"):
        return "zh-Hans"
    if low in ("zh-tw", "zh-hk", "zh-mo", "zh-hant"):
        return "zh-Hant"
    return low.split("-")[0]


def edge_bing_translate_sync(text: str, from_lang: str, to_lang: str) -> str:
    """Translate via edge.microsoft.com (the app's 'Bing' engine). Empty/auto
    from_lang omits the `from` param → server-side language detection."""
    import requests

    params = {"to": _to_edge_lang(to_lang) or "en", "isEnterpriseClient": "false"}
    src = _to_edge_lang(from_lang)
    if src:
        params["from"] = src
    resp = requests.post(
        _EDGE_TRANSLATE_URL,
        params=params,
        json=[text],
        headers={"User-Agent": _EDGE_UA},
        timeout=8.0,
    )
    resp.raise_for_status()
    data = resp.json()
    return str(data[0]["translations"][0]["text"]).strip()


class FreeWebTranslationProvider:
    """Google / Bing / Papago translation via free web endpoints (no API key).
    'bing' uses the Edge translate endpoint; the rest go through `translators`."""

    # Free web engines receive only text + language pair — the hub-provided
    # system_prompt/context never reach the server (request inspector flag).
    USES_SYSTEM_PROMPT = False

    def __init__(self, translator: str) -> None:
        self._translator = translator  # "google", "bing", "papago"

    def _translate_sync(self, text: str, from_lang: str, to_lang: str) -> str:
        if self._translator == "bing":
            return edge_bing_translate_sync(text, from_lang, to_lang)
        # Must precede the translators import: pins the region so the lib skips
        # its geo-detection calls (blocked/hanging in China) for google/papago.
        from puripuly_heart.core.translators_region import ensure_translators_region

        ensure_translators_region()
        from translators import translate_text  # type: ignore

        # Let exceptions propagate so the orchestrator can surface a visible
        # error (UIEventType.ERROR) instead of silently showing nothing. The
        # `translators` library raises e.g. "'NoneType' object has no attribute
        # 'xpath'" when a free web endpoint is regionally blocked or its page
        # structure changed; the user needs to see that, not an empty result.
        result = translate_text(
            query_text=text,
            translator=self._translator,
            from_language=from_lang,
            to_language=to_lang,
        )
        return str(result).strip()

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
        # Same-language: nothing to translate. Return the original text instead of
        # asking the web translator (which errors with "from and to should not be same").
        if from_lang == to_lang:
            return Translation(utterance_id=utterance_id, text=text)
        loop = asyncio.get_event_loop()
        try:
            translated = await asyncio.wait_for(
                loop.run_in_executor(None, self._translate_sync, text, from_lang, to_lang),
                timeout=10.0,
            )
        except asyncio.TimeoutError:
            # One retry: free web endpoints (esp. through the GFW) often stall
            # transiently and answer promptly on a fresh request.
            logger.warning(
                "[%s] translation timed out after 10s — retrying once", self._translator)
            try:
                translated = await asyncio.wait_for(
                    loop.run_in_executor(None, self._translate_sync, text, from_lang, to_lang),
                    timeout=10.0,
                )
            except asyncio.TimeoutError as exc:
                logger.warning(
                    "[%s] translation timed out twice (service may be blocked)", self._translator)
                raise RuntimeError(
                    f"{self._translator} translation timed out twice (service may be blocked)"
                ) from exc
        except Exception as exc:
            logger.warning("[%s] translation failed: %s", self._translator, exc)
            raise RuntimeError(f"{self._translator} translation failed: {exc}") from exc
        if not translated:
            raise RuntimeError(
                f"{self._translator} returned an empty translation (service may be blocked or rate-limited)"
            )
        logger.info("[%s] result: %r", self._translator, translated)
        return Translation(utterance_id=utterance_id, text=translated)

    async def close(self) -> None:
        pass
