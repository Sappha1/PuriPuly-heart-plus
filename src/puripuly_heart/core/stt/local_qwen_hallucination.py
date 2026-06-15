from __future__ import annotations

KNOWN_LOCAL_QWEN_HALLUCINATIONS = frozenset({"leşme", "acia", "system"})

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


__all__ = ["KNOWN_LOCAL_QWEN_HALLUCINATIONS", "is_known_local_qwen_hallucination"]
