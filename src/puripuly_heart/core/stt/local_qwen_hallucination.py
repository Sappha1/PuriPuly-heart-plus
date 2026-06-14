from __future__ import annotations

KNOWN_LOCAL_QWEN_HALLUCINATIONS = frozenset({"leşme", "acia"})


def is_known_local_qwen_hallucination(text: str) -> bool:
    return text.strip() in KNOWN_LOCAL_QWEN_HALLUCINATIONS


__all__ = ["KNOWN_LOCAL_QWEN_HALLUCINATIONS", "is_known_local_qwen_hallucination"]
