from __future__ import annotations

from puripuly_heart.config.settings import MAX_CUSTOM_VOCAB_TERMS, AppSettings

LOCAL_QWEN_MAX_HOTWORDS = 12


def _raw_terms_for_language(settings: AppSettings, source_language: str) -> list[str]:
    if source_language in settings.stt.custom_terms:
        return settings.stt.custom_terms[source_language]
    base_language = source_language.split("-")[0].lower()
    return settings.stt.custom_terms.get(base_language, [])


def get_effective_custom_terms(settings: AppSettings, source_language: str) -> list[str]:
    if not settings.stt.custom_vocabulary_enabled:
        return []

    raw_terms = _raw_terms_for_language(settings, source_language)
    effective_terms: list[str] = []
    seen_terms: set[str] = set()
    for term in raw_terms:
        normalized_term = term.strip()
        if not normalized_term or normalized_term in seen_terms:
            continue
        if len(effective_terms) >= MAX_CUSTOM_VOCAB_TERMS:
            break
        seen_terms.add(normalized_term)
        effective_terms.append(normalized_term)
    return effective_terms


def _normalize_local_qwen_hotword(term: str) -> str:
    return " ".join(term.replace(",", " ").split())


def get_effective_local_qwen_hotwords(settings: AppSettings, source_language: str) -> list[str]:
    if not settings.stt.custom_vocabulary_enabled:
        return []

    raw_terms = _raw_terms_for_language(settings, source_language)
    effective_terms: list[str] = []
    seen_terms: set[str] = set()
    for term in raw_terms:
        normalized_term = _normalize_local_qwen_hotword(term)
        if not normalized_term or normalized_term in seen_terms:
            continue
        if len(effective_terms) >= LOCAL_QWEN_MAX_HOTWORDS:
            break
        seen_terms.add(normalized_term)
        effective_terms.append(normalized_term)
    return effective_terms
