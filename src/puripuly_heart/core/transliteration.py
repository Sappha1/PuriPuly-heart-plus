"""Pinyin and romaji transliteration utilities."""

from __future__ import annotations

import re

_PINYIN_LETTER_RE = re.compile(r"[a-zA-Zāáǎàēéěèīíǐìōóǒòūúǔùǖǘǚǜü]", re.IGNORECASE)


def _join_pinyin(syllables: list[str]) -> str:
    """Join pinyin syllables with spaces, but no spaces around punctuation."""
    parts: list[str] = []
    for syl in syllables:
        if not syl:
            continue
        is_pinyin = bool(_PINYIN_LETTER_RE.search(syl))
        prev_is_pinyin = bool(parts and _PINYIN_LETTER_RE.search(parts[-1]))
        if parts and is_pinyin and prev_is_pinyin:
            parts.append(" ")
        parts.append(syl)
    return "".join(parts).strip()


def to_pinyin(text: str) -> str:
    """Convert Chinese text to pinyin with tone marks."""
    try:
        from pypinyin import lazy_pinyin, Style
        syllables = lazy_pinyin(text, style=Style.TONE)
        return _join_pinyin(syllables)
    except Exception:
        return ""


def to_romaji(text: str) -> str:
    """Convert Japanese text to Hepburn romaji with proper word spacing via MeCab/cutlet."""
    try:
        import cutlet
        ct = cutlet.Cutlet()
        return ct.romaji(text).strip()
    except Exception:
        pass
    # Fallback to pykakasi if cutlet unavailable
    try:
        from pykakasi import kakasi
        kks = kakasi()
        result = kks.convert(text)
        parts = []
        for item in result:
            orig = item.get("orig", "")
            hepburn = item.get("hepburn") or orig
            if not orig.strip():
                parts.append(orig)
            else:
                if parts and not parts[-1].endswith(" "):
                    parts.append(" ")
                parts.append(hepburn)
        return " ".join(parts).strip()
    except Exception:
        return ""


_KO_BASE = 0xAC00
_KO_INITIALS = [
    "g", "kk", "n", "d", "tt", "r", "m", "b", "pp",
    "s", "ss", "", "j", "jj", "ch", "k", "t", "p", "h",
]
_KO_VOWELS = [
    "a", "ae", "ya", "yae", "eo", "e", "yeo", "ye", "o",
    "wa", "wae", "oe", "yo", "u", "wo", "we", "wi", "yu",
    "eu", "ui", "i",
]
_KO_FINALS = [
    "",    # 0  (none)
    "k",   # 1  ㄱ
    "k",   # 2  ㄲ
    "k",   # 3  ㄳ
    "n",   # 4  ㄴ
    "n",   # 5  ㄵ
    "n",   # 6  ㄶ
    "t",   # 7  ㄷ
    "l",   # 8  ㄹ
    "k",   # 9  ㄺ
    "l",   # 10 ㄻ
    "l",   # 11 ㄼ
    "l",   # 12 ㄽ
    "l",   # 13 ㄾ
    "p",   # 14 ㄿ
    "l",   # 15 ㅀ
    "m",   # 16 ㅁ
    "p",   # 17 ㅂ
    "p",   # 18 ㅄ
    "t",   # 19 ㅅ
    "t",   # 20 ㅆ
    "ng",  # 21 ㅇ
    "t",   # 22 ㅈ
    "t",   # 23 ㅊ
    "k",   # 24 ㅋ
    "t",   # 25 ㅌ
    "p",   # 26 ㅍ
    "t",   # 27 ㅎ
]


def to_romaja(text: str) -> str:
    """Convert Korean Hangul text to Revised Romanization (RR)."""
    parts: list[str] = []
    for char in text:
        code = ord(char)
        if _KO_BASE <= code <= 0xD7A3:
            offset = code - _KO_BASE
            final_idx = offset % 28
            offset //= 28
            vowel_idx = offset % 21
            initial_idx = offset // 21
            parts.append(
                _KO_INITIALS[initial_idx]
                + _KO_VOWELS[vowel_idx]
                + _KO_FINALS[final_idx]
            )
        else:
            parts.append(char)
    return "".join(parts)


# ── Cyrillic → Latin ─────────────────────────────────────────────────────────
_CYR_MAP: dict[str, str] = {
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d",
    "е": "ye", "ё": "yo", "ж": "zh", "з": "z", "и": "i",
    "й": "y", "к": "k", "л": "l", "м": "m", "н": "n",
    "о": "o", "п": "p", "р": "r", "с": "s", "т": "t",
    "у": "u", "ф": "f", "х": "kh", "ц": "ts", "ч": "ch",
    "ш": "sh", "щ": "shch", "ъ": "", "ы": "y", "ь": "",
    "э": "e", "ю": "yu", "я": "ya",
    # Ukrainian extras
    "і": "i", "ї": "yi", "є": "ye", "ґ": "g",
    # Bulgarian extras
    "ъ": "a",
}
_CYR_MAP.update({c.upper(): v.capitalize() for c, v in _CYR_MAP.items()})


def to_latin_cyrillic(text: str) -> str:
    """Transliterate Cyrillic text (Russian/Ukrainian/Bulgarian) to Latin."""
    parts: list[str] = []
    for char in text:
        parts.append(_CYR_MAP.get(char, char))
    return "".join(parts)


# ── Greek → Latin ─────────────────────────────────────────────────────────────
_GR_MAP: dict[str, str] = {
    "α": "a", "β": "v", "γ": "g", "δ": "d", "ε": "e",
    "ζ": "z", "η": "i", "θ": "th", "ι": "i", "κ": "k",
    "λ": "l", "μ": "m", "ν": "n", "ξ": "x", "ο": "o",
    "π": "p", "ρ": "r", "σ": "s", "ς": "s", "τ": "t",
    "υ": "y", "φ": "f", "χ": "ch", "ψ": "ps", "ω": "o",
    # Accented vowels
    "ά": "a", "έ": "e", "ή": "i", "ί": "i", "ό": "o",
    "ύ": "y", "ώ": "o", "ϊ": "i", "ϋ": "y", "ΐ": "i", "ΰ": "y",
}
_GR_MAP.update({c.upper(): v.capitalize() for c, v in _GR_MAP.items()})
# uppercase single-letter overrides that capitalize() would mangle
for _lc, _uc in [("σ", "Σ"), ("ς", "ς")]:
    if _lc in _GR_MAP:
        _GR_MAP[_lc.upper()] = _GR_MAP[_lc].upper() if len(_GR_MAP[_lc]) == 1 else _GR_MAP[_lc].capitalize()


def to_latin_greek(text: str) -> str:
    """Transliterate Modern Greek text to Latin."""
    return "".join(_GR_MAP.get(c, c) for c in text)


# ── Arabic → Latin ────────────────────────────────────────────────────────────
_AR_MAP: dict[str, str] = {
    "ا": "a",   # ا alef
    "ب": "b",   # ب ba
    "ت": "t",   # ت ta
    "ث": "th",  # ث tha
    "ج": "j",   # ج jim
    "ح": "h",   # ح ha
    "خ": "kh",  # خ kha
    "د": "d",   # د dal
    "ذ": "dh",  # ذ dhal
    "ر": "r",   # ر ra
    "ز": "z",   # ز zayn
    "س": "s",   # س sin
    "ش": "sh",  # ش shin
    "ص": "s",   # ص sad
    "ض": "d",   # ض dad
    "ط": "t",   # ط ta
    "ظ": "z",   # ظ za
    "ع": "'",   # ع ayn
    "غ": "gh",  # غ ghayn
    "ف": "f",   # ف fa
    "ق": "q",   # ق qaf
    "ك": "k",   # ك kaf
    "ل": "l",   # ل lam
    "م": "m",   # م mim
    "ن": "n",   # ن nun
    "ه": "h",   # ه ha
    "و": "w",   # و waw
    "ي": "y",   # ي ya
    "ة": "t",   # ة ta marbuta
    "ى": "a",   # ى alef maqsura
    "ء": "'",   # ء hamza
    "أ": "a",   # أ alef with hamza above
    "إ": "i",   # إ alef with hamza below
    "آ": "aa",  # آ alef with madda
    "ؤ": "w",   # ؤ waw with hamza
    "ئ": "y",   # ئ ya with hamza
    # Diacritics (harakat)
    "َ": "a",   # fatha
    "ُ": "u",   # damma
    "ِ": "i",   # kasra
    "ّ": "",    # shadda (double) — skip
    "ْ": "",    # sukun — skip
    "ً": "an",  # tanwin fath
    "ٌ": "un",  # tanwin damm
    "ٍ": "in",  # tanwin kasr
    # Tatweel
    "ـ": "",
}


def to_latin_arabic(text: str) -> str:
    """Transliterate Arabic text to Latin (simplified)."""
    return "".join(_AR_MAP.get(c, c) for c in text)


# ── Devanagari (Hindi) → Latin ────────────────────────────────────────────────
_DEV_MAP: dict[str, str] = {
    # Independent vowels
    "अ": "a", "आ": "aa", "इ": "i", "ई": "ii",
    "उ": "u", "ऊ": "uu", "ऋ": "ri", "ऌ": "li",
    "ए": "e", "ऐ": "ai", "ओ": "o", "औ": "au",
    # Vowel signs (matras)
    "ा": "aa", "ि": "i", "ी": "ii",
    "ु": "u", "ू": "uu", "ृ": "ri",
    "े": "e", "ै": "ai", "ो": "o", "ौ": "au",
    "्": "",   # virama (halant) — removes inherent a
    # Consonants (inherent 'a' added unless followed by virama)
    "क": "ka", "ख": "kha", "ग": "ga", "घ": "gha", "ङ": "nga",
    "च": "ca", "छ": "cha", "ज": "ja", "झ": "jha", "ञ": "nya",
    "ट": "ta", "ठ": "tha", "ड": "da", "ढ": "dha", "ण": "na",
    "त": "ta", "थ": "tha", "द": "da", "ध": "dha", "न": "na",
    "प": "pa", "फ": "pha", "ब": "ba", "भ": "bha", "म": "ma",
    "य": "ya", "र": "ra", "ल": "la", "व": "va",
    "श": "sha", "ष": "sha", "स": "sa", "ह": "ha",
    # Anusvara / visarga / chandrabindu
    "ं": "m", "ः": "h", "ँ": "n",
    # Digits
    "०": "0", "१": "1", "२": "2", "३": "3", "४": "4",
    "५": "5", "६": "6", "७": "7", "८": "8", "९": "9",
}


def to_latin_hindi(text: str) -> str:
    """Transliterate Devanagari (Hindi) text to Latin (IAST-simplified)."""
    parts: list[str] = []
    i = 0
    chars = list(text)
    while i < len(chars):
        c = chars[i]
        mapped = _DEV_MAP.get(c)
        if mapped is None:
            parts.append(c)
            i += 1
            continue
        # If next char is virama, drop inherent 'a' from consonant
        if (
            i + 1 < len(chars)
            and chars[i + 1] == "्"
            and mapped.endswith("a")
        ):
            parts.append(mapped[:-1])
            i += 2  # consume consonant + virama
            continue
        parts.append(mapped)
        i += 1
    return "".join(parts)


# ── Thai → Latin ──────────────────────────────────────────────────────────────
_TH_MAP: dict[str, str] = {
    # Consonants (initial form)
    "ก": "k",  "ข": "kh", "ค": "kh", "ง": "ng",
    "จ": "ch", "ฉ": "ch", "ช": "ch", "ซ": "s",
    "ฌ": "ch", "ญ": "y",  "ฎ": "d",  "ฏ": "t",
    "ฐ": "th", "ฑ": "th", "ฒ": "th", "ณ": "n",
    "ด": "d",  "ต": "t",  "ถ": "th", "ท": "th",
    "ธ": "th", "น": "n",  "บ": "b",  "ป": "p",
    "ผ": "ph", "ฝ": "f",  "พ": "ph", "ฟ": "f",
    "ภ": "ph", "ม": "m",  "ย": "y",  "ร": "r",
    "ล": "l",  "ว": "w",  "ศ": "s",  "ษ": "s",
    "ส": "s",  "ห": "h",  "ฬ": "l",  "อ": "",
    "ฮ": "h",
    # Vowels (simplified)
    "ะ": "a",  "ั": "a",  "า": "aa", "ำ": "am",
    "ิ": "i",  "ี": "ii", "ึ": "ue", "ื": "uee",
    "ุ": "u",  "ู": "uu", "เ": "e",  "แ": "ae",
    "โ": "o",  "ใ": "ai", "ไ": "ai", "็": "ia",
    "่": "", "้": "", "๊": "", "๋": "",  # tone marks
    "์": "",   # thanthakat (silent)
    "ๅ": "ue",
    # Thai digits
    "๐": "0", "๑": "1", "๒": "2", "๓": "3", "๔": "4",
    "๕": "5", "๖": "6", "๗": "7", "๘": "8", "๙": "9",
}


def to_latin_thai(text: str) -> str:
    """Transliterate Thai text to Latin (RTGS-simplified)."""
    return "".join(_TH_MAP.get(c, c) for c in text)


# ── Script detection helpers ──────────────────────────────────────────────────
def _is_mostly_cjk(text: str) -> bool:
    cjk = sum(1 for c in text if "一" <= c <= "鿿" or "㐀" <= c <= "䶿")
    return cjk > len(text) * 0.3


def _is_mostly_japanese(text: str) -> bool:
    jp = sum(
        1 for c in text
        if "぀" <= c <= "ゟ"   # hiragana
        or "゠" <= c <= "ヿ"   # katakana
        or "一" <= c <= "鿿"   # kanji
    )
    return jp > len(text) * 0.2


def _is_mostly_korean(text: str) -> bool:
    ko = sum(1 for c in text if _KO_BASE <= ord(c) <= 0xD7A3)
    return ko > len(text) * 0.2


def _is_mostly_cyrillic(text: str) -> bool:
    cyr = sum(1 for c in text if "Ѐ" <= c <= "ӿ")
    return cyr > len(text) * 0.2


def _is_mostly_greek(text: str) -> bool:
    gr = sum(1 for c in text if "Ͱ" <= c <= "Ͽ" or "ἀ" <= c <= "῿")
    return gr > len(text) * 0.2


def _is_mostly_arabic(text: str) -> bool:
    ar = sum(1 for c in text if "؀" <= c <= "ۿ")
    return ar > len(text) * 0.2


def _is_mostly_devanagari(text: str) -> bool:
    dv = sum(1 for c in text if "ऀ" <= c <= "ॿ")
    return dv > len(text) * 0.2


def _is_mostly_thai(text: str) -> bool:
    th = sum(1 for c in text if "฀" <= c <= "๿")
    return th > len(text) * 0.2


def transliterate_for_language(
    text: str,
    language_code: str,
    *,
    show_pinyin: bool,
    show_romaji: bool,
    show_latin: bool = False,
) -> str:
    """Return transliteration line for text given display settings, or empty string."""
    if not text.strip():
        return ""
    lang = (language_code or "").lower().split("-")[0]
    if show_pinyin and lang in ("zh", "zh_cn", "zh_tw", "cmn") and _is_mostly_cjk(text):
        return to_pinyin(text)
    if show_romaji and lang in ("ja", "jpn") and _is_mostly_japanese(text):
        return to_romaji(text)
    if show_romaji and lang in ("ko", "kor") and _is_mostly_korean(text):
        return to_romaja(text)
    if show_latin:
        if lang in ("ru", "uk", "bg") and _is_mostly_cyrillic(text):
            return to_latin_cyrillic(text)
        if lang in ("el",) and _is_mostly_greek(text):
            return to_latin_greek(text)
        if lang in ("ar",) and _is_mostly_arabic(text):
            return to_latin_arabic(text)
        if lang in ("hi",) and _is_mostly_devanagari(text):
            return to_latin_hindi(text)
        if lang in ("th",) and _is_mostly_thai(text):
            return to_latin_thai(text)
    return ""
