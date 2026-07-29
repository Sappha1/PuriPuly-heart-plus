from __future__ import annotations

import re
from collections import Counter

KNOWN_LOCAL_QWEN_HALLUCINATIONS = frozenset({
    "leşme", "acia", "system",
    # Qwen's stock Chinese filler on near-silence/noise. Observed dozens of
    # times across two users' logs on ENGLISH-only audio (both channels, with
    # and without a language hint). Matched as a WHOLE utterance only — these
    # are bare fragments ("'s answer", "fictional"), never a real sentence.
    "的答案", "的答案是", "虚构", "虚构人物", "我可以不因为",
})

# A "stuck" STT loop repeats a short unit many times ("什么?什么?什么?..."). Require a
# high repeat count + dominance so ordinary repetition ("no no no") is NOT suppressed.
_REPETITION_MIN_UNITS = 8
# NOTE: ，(U+FF0C, the standard Chinese comma) was originally missing here, which let
# "等等，等等，等等，…" walls parse as ONE unit and evade detection entirely.
_REPETITION_SPLIT_RE = re.compile(r"[\s,，.。、!！?？;；:：…·]+")


# A contiguous run of one short unit this many times, covering this much of the
# text, is a degenerate loop — well above ordinary emphasis ("no no no no").
_RUN_MIN_REPS = 12
_RUN_MIN_COVERAGE = 0.6


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
    # Run ANYWHERE in the string: the model often emits a few plausible chars and
    # then locks into a loop ("這不看一" + "怪"x180). The prefix defeated both checks
    # above, so a message of hundreds of identical characters reached the chatbox.
    for unit_len in range(1, 5):
        i = 0
        while i + unit_len <= n:
            unit = s[i : i + unit_len]
            if not unit.strip():
                i += 1
                continue
            reps = 1
            j = i + unit_len
            while j + unit_len <= n and s[j : j + unit_len] == unit:
                reps += 1
                j += unit_len
            if reps >= _RUN_MIN_REPS and (reps * unit_len) >= n * _RUN_MIN_COVERAGE:
                return True
            i = max(j - unit_len, i) + 1
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

# HTML/XML-ish markup fragment ('<col="" title=""'). Speech transcripts never
# contain markup — its presence means the model hallucinated document/subtitle
# structure (captured live on noise/music through desktop loopback).
_MARKUP_LINE_RE = re.compile(r"<[a-zA-Z]+[=>\"]")
# Bare numbered-list line ("#1", "12.", "3"). Walls of these are OCR/subtitle
# hallucinations, not speech.
_NUMBER_LINE_RE = re.compile(r"^#?\d{1,4}\.?$")


def _is_multiline_garbage(stripped: str) -> bool:
    """Real STT output is a single line of prose. Multi-line output made of
    markup, number walls, or one-token-per-line words (e.g.
    'acia\\n<col="" title=""\\n#1\\n#2\\n...' captured live) is a hallucination."""
    lines = [ln.strip() for ln in stripped.splitlines() if ln.strip()]
    if len(lines) < 3:
        return False
    if any(_MARKUP_LINE_RE.search(ln) for ln in lines):
        return True
    number_lines = sum(1 for ln in lines if _NUMBER_LINE_RE.match(ln))
    if number_lines >= 3 and number_lines / len(lines) >= 0.5:
        return True
    single_token_lines = sum(1 for ln in lines if len(ln.split()) == 1)
    if len(lines) >= 6 and single_token_lines / len(lines) >= 0.8:
        return True
    return False


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
    if _is_multiline_garbage(stripped):
        return True
    return False


__all__ = [
    "KNOWN_LOCAL_QWEN_HALLUCINATIONS",
    "is_known_local_qwen_hallucination",
    "is_repetition_loop",
]
