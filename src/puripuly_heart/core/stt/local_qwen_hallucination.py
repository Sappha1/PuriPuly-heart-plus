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
    # Instruction-corpus regurgitation on noise (Anhui user, r310 log): bare
    # prompts the model was trained on, emitted verbatim from mic noise.
    "虚构一个故事", "合并成一个句子", "格力空调", "格力",
    # r508: the Chinese-side twin of the "System." hallucination — a friend's
    # chatbox posted "系统。/ System." from noise. Whole-utterance only; a real
    # sentence containing 系统 has other words and never reduces to this.
    "系统", "系统。",
})

# Stock terms for the normalized checks below: junk if the text minus digits
# and punctuation is exactly one of these, or that term repeated back-to-back
# ("的答案是：100。", "格力空调，格力空调。").
_STOCK_TERMS = (
    "的答案是", "的答案", "虚构一个故事", "虚构人物", "虚构", "格力空调", "格力",
    "合并成一个句子", "我可以不因为", "系统",
)
_PUNCT_DIGIT_STRIP_RE = re.compile(r"[\s\d.。,，!！?？;；:：、…·（）()\-—‘’“”'\"#*]+")

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
    # Long-unit loop: one clause repeated many times with separators. The unit
    # cap of 16 above missed "这个角色的身高和体重的比是1.75:60" x8 (19 chars,
    # dominance 0.67) — a whole story-template loop shipped to VRChat (r312).
    if len(units) >= 6:
        unit, count = Counter(units).most_common(1)[0]
        if 17 <= len(unit) <= 40 and count >= 4 and count / len(units) >= 0.5:
            return True
    # Recursive-phrase loop ("小明的爸爸是小明的爸爸，小明的爸爸的爸爸是小小明
    # 的爸爸…"): the units all differ so unit counting fails, but one short
    # gram recurs absurdly often. Whitespace-free grams only — English prose
    # legitimately repeats "the"; CJK recursion is what this targets (r312).
    if len(s) >= 60:
        compact = s
        for gram_len in (3, 4, 5, 6):
            best = 0
            for i in range(len(compact) - gram_len + 1):
                gram = compact[i : i + gram_len]
                if not gram.strip() or any(ch.isspace() for ch in gram):
                    continue
                count = compact.count(gram)
                if count > best:
                    best = count
            if best >= 8 and best * gram_len >= len(compact) * 0.25:
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
# r312: "# 2" (space after #) and "3)" / "4、" list styles — a Chinese user's
# mic emitted "# 2".."# 27" as one utterance and the missing \s* let it ship.
_NUMBER_LINE_RE = re.compile(r"^#?\s*\d{1,4}[.。)）、]?$")


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


def _english_stock_residue(text: str) -> str:
    """Lowercased, stripped of punctuation, whitespace collapsed.

    r384: the set below is matched against the RAW utterance, so the bare
    "system" entry never caught what the model actually emits — "A system.",
    "This is a system." — which is what a user kept seeing appear mid-dictation.
    """
    lowered = _PUNCT_DIGIT_STRIP_RE.sub(" ", text.strip().lower())
    return " ".join(lowered.split())


# Whole utterances only. These are what Qwen produces from quiet or short audio
# (measured at -30 dB over 1.2s); a real sentence containing "a system" has other
# words around it and so never reduces to exactly one of these.
# Only forms actually OBSERVED from this model. "the system" and "it is a system"
# were in this set briefly on my guess alone and came back out: a person can say
# "The system." as a complete answer to a question, and swallowing real speech is
# a worse failure than letting one stray line through.
_ENGLISH_STOCK_UTTERANCES = frozenset({
    "system", "a system", "this is a system",
})

# r387: forms a person could plausibly say as a complete answer, so they are
# NOT blocked outright — only when the audio segment is in the noise band.
# Measured: every noise emission of these sat in a 1140-1972ms segment (five
# at exactly 1140.0ms, the VAD minimum), while real speech containing the
# word ran 3444ms. A deliberate lone "The system." at talking pace lands in a
# longer segment and passes; unknown duration always passes.
_ENGLISH_STOCK_UTTERANCES_NOISE_BAND = frozenset({
    "the system", "it is a system",
})
NOISE_BAND_MAX_AUDIO_MS = 2000.0


def is_known_local_qwen_hallucination(
    text: str, *, audio_ms: float | None = None
) -> bool:
    stripped = text.strip()
    if stripped in KNOWN_LOCAL_QWEN_HALLUCINATIONS:
        return True
    residue = _english_stock_residue(stripped)
    if residue in _ENGLISH_STOCK_UTTERANCES:
        return True
    # r387: the borderline forms need the noise band as a second witness —
    # whole-line alone is not enough, because a person can say them.
    if (
        residue in _ENGLISH_STOCK_UTTERANCES_NOISE_BAND
        and audio_ms is not None
        and 0.0 < audio_ms < NOISE_BAND_MAX_AUDIO_MS
    ):
        return True
    # Normalized stock-term checks (r312): drop digits/punctuation, then junk
    # if what remains is exactly a stock term or that term repeated ("的答案是：
    # 100。", "格力空调，格力空调。"). Real sentences EMBED these fragments in
    # other words, so their residue never equals the bare term.
    residue = _PUNCT_DIGIT_STRIP_RE.sub("", stripped)
    if residue:
        for term in _STOCK_TERMS:
            if residue == term:
                return True
            if (
                len(residue) > len(term)
                and len(residue) % len(term) == 0
                and residue == term * (len(residue) // len(term))
            ):
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


# ── Filler / grunt detection (provider-agnostic) ───────────────────────────
# "hmm", "uh...", "嗯嗯", "うーん", "음..." — non-lexical sounds that waste
# translator tokens and spam the chat log. Whole-utterance match ONLY: a
# transcript is filler when EVERY token/character of it is filler, so one
# real word anywhere keeps the line. Meaningful acknowledgements ("yes",
# "はい", "네", "好") deliberately do NOT count as filler.

_FILLER_STRIP_RE = re.compile(
    "[\\s.。,，!！?？;；:：、…·~〜～\\-—–_'\"“”‘’"
    "「」『』()（）\\[\\]♪ー]+")

_LATIN_FILLER_TOKEN_RE = re.compile(
    r"^(?:h+m+|m+h+m*|m{2,}|u+h+m*|u+m+|e+r+m*|e+h+|a+h+|o+h+|o+o+|huh+|"
    r"mhm+|(?:ha){2,}h*|(?:he){2,}h*|(?:ho){2,}h*|ugh+|hmph+|tsk+|pf+t*|"
    r"whew+|phew+|uhhuh|aha+)$")

_CJK_FILLER_CHARS = frozenset(
    # zh interjections / laughter / sighs
    "嗯呃啊哦噢喔唔哈嘿呵哎唉欸诶咦哟唷呦嚯呀嗷噗唏咳呜嘤"
    # ja kana grunts ("い" is excluded so はい/yes always passes; the
    # elongation mark ー is stripped by _FILLER_STRIP_RE)
    "あぁうぅえぇおぉんっはへほふ"
    "アァウゥエェオォンッハヘホフ"
    # ko grunts / laughter
    "음응어아으흠하허헤호후흐엇앗"
)


def is_filler_utterance(text: str) -> bool:
    """True when the WHOLE utterance is non-lexical filler (grunts,
    hesitation sounds, bare laughter) in any supported script."""
    stripped = _FILLER_STRIP_RE.sub(" ", (text or "").lower()).strip()
    if not stripped:
        return False   # empty / punctuation-only is handled elsewhere
    for token in stripped.split(" "):
        if not token:
            continue
        if token.isascii():
            if not _LATIN_FILLER_TOKEN_RE.fullmatch(token):
                return False
        elif not all(ch in _CJK_FILLER_CHARS for ch in token):
            return False
    return True


__all__ = [
    "KNOWN_LOCAL_QWEN_HALLUCINATIONS",
    "is_filler_utterance",
    "is_known_local_qwen_hallucination",
    "is_repetition_loop",
]
