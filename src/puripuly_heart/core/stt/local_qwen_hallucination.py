from __future__ import annotations

import re
from collections import Counter

KNOWN_LOCAL_QWEN_HALLUCINATIONS = frozenset({"leşme", "acia", "system"})

# A "stuck" STT loop repeats a short unit many times ("什么?什么?什么?..."). Require a
# high repeat count + dominance so ordinary repetition ("no no no") is NOT suppressed.
_REPETITION_MIN_UNITS = 8
_REPETITION_SPLIT_RE = re.compile(r"[\s,.。、!！?？;；:：…·]+")


def is_repetition_loop(text: str) -> bool:
    """Detect a degenerate STT repetition loop — a common Whisper/Qwen failure on
    silence/noise where the model emits the same short phrase dozens of times. Such
    output is meaningless and spams the chatbox/overlay, so it should be suppressed."""
    s = text.strip()
    if len(s) < 12:
        return False
    units = [u for u in _REPETITION_SPLIT_RE.split(s) if u]
    if len(units) >= _REPETITION_MIN_UNITS:
        unit, count = Counter(units).most_common(1)[0]
        if len(unit) <= 16 and count / len(units) >= 0.7:
            return True
    # Unseparated case: the whole string is one short substring repeated back-to-back.
    n = len(s)
    for unit_len in range(1, min(16, n // _REPETITION_MIN_UNITS) + 1):
        reps = n // unit_len
        if reps >= _REPETITION_MIN_UNITS and s[: unit_len * reps] == s[:unit_len] * reps:
            if unit_len * reps >= n * 0.85:
                return True
    return False

# Prefixes that indicate the model hallucinated a structured/code output
_HALLUCINATION_PREFIXES = ("```", "{", "[{", "[\n{")

# Substrings indicating the model hallucinated a refusal / meta-commentary instead of transcribing
_HALLUCINATION_SUBSTRINGS = (
    "I'm sorry, but I cannot",
    "I cannot provide",
    "I am unable to",
    "I can't provide",
    "I apologize, but",
    "As an AI",
    "As a language model",
)

# Single characters that are meaningless to send (punctuation/whitespace only)
_TRIVIAL_CHARS = frozenset(".。,，!！?？;；:：、…")


def is_known_local_qwen_hallucination(text: str) -> bool:
    stripped = text.strip()
    if stripped in KNOWN_LOCAL_QWEN_HALLUCINATIONS:
        return True
    # Single-char or pure punctuation output
    if len(stripped) <= 1:
        return True
    # Markdown code block or JSON structure hallucination
    for prefix in _HALLUCINATION_PREFIXES:
        if stripped.startswith(prefix):
            return True
    # AI refusal / meta-commentary hallucination (model confused STT with chat)
    for sub in _HALLUCINATION_SUBSTRINGS:
        if sub in stripped:
            return True
    return False


__all__ = [
    "KNOWN_LOCAL_QWEN_HALLUCINATIONS",
    "is_known_local_qwen_hallucination",
    "is_repetition_loop",
]
