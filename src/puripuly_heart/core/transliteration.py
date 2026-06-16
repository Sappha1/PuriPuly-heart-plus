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
# Consonant set: letters that map to consonant sounds (not vowel markers)
_AR_CONSONANTS = frozenset("بتثجحخدذرزسشصضطظعغفقكلمنهةء")
# Long-vowel letters (ا ى are always vowels; و ي context-dependent)
_AR_LONG_VOWEL_ALWAYS = frozenset("اىآأإ")
# Base consonant map (و and ي handled separately based on context)
_AR_BASE_MAP: dict[str, str] = {
    "ا": "a",   # alef — long vowel
    "ب": "b",
    "ت": "t",
    "ث": "th",
    "ج": "j",
    "ح": "h",
    "خ": "kh",
    "د": "d",
    "ذ": "dh",
    "ر": "r",
    "ز": "z",
    "س": "s",
    "ش": "sh",
    "ص": "s",
    "ض": "d",
    "ط": "t",
    "ظ": "z",
    "ع": "",    # ayn — silent in casual romanization
    "غ": "gh",
    "ف": "f",
    "ق": "q",
    "ك": "k",
    "ل": "l",
    "م": "m",
    "ن": "n",
    "ه": "h",
    "ة": "a",   # ta marbuta — usually pronounced as trailing "a"
    "ى": "a",   # alef maqsura
    "ء": "",    # hamza — silent
    "أ": "a",   # alef with hamza above
    "إ": "i",   # alef with hamza below
    "آ": "aa",  # alef with madda
    "ؤ": "u",   # waw with hamza — vowel "u"
    "ئ": "i",   # ya with hamza — vowel "i"
    # Diacritics (harakat) — when present they give exact vowels
    "َ": "a",   # fatha
    "ُ": "u",   # damma
    "ِ": "i",   # kasra
    "ّ": "",    # shadda — gemination, skip
    "ْ": "",    # sukun — skip
    "ً": "an",  # tanwin fath
    "ٌ": "un",  # tanwin damm
    "ٍ": "in",  # tanwin kasr
    "ـ": "",    # tatweel
}

# Common Arabic words → correct romanization (avoids guessing vowels)
_AR_WORD_DICT: dict[str, str] = {
    "الله": "Allah",
    "أكبر": "Akbar",
    "اكبر": "Akbar",
    "الحمد": "Alhamd",
    "لله": "lillah",
    "الرحمن": "al-Rahman",
    "الرحيم": "al-Rahim",
    "بسم": "Bismi",
    "السلام": "al-Salam",
    "سلام": "Salam",
    "عليكم": "alaykum",
    "وعليكم": "wa-alaykum",
    "شكرا": "shukran",
    "شكراً": "shukran",
    "مرحبا": "marhaba",
    "مرحباً": "marhaban",
    "نعم": "na'am",
    "لا": "la",
    "أنا": "ana",
    "أنت": "anta",
    "هو": "huwa",
    "هي": "hiya",
    "نحن": "nahnu",
    "أهلا": "ahlan",
    "أهلاً": "ahlan",
    "وسهلا": "wa-sahlan",
    "حبيبي": "habibi",
    "حبيبتي": "habibti",
    "يلا": "yalla",
    "يالله": "yallah",
    "إنشاء": "insha",
    "الله": "Allah",
    "ماشاء": "masha",
    "ماشاءالله": "masha'Allah",
    "سبحان": "subhan",
    "سبحانالله": "subhanAllah",
    "فلبيني": "Filipini",
    "فلبينية": "Filipiniya",
    "فلبين": "Filipin",
    "عربي": "Arabi",
    "عربية": "Arabiya",
    "ياباني": "Yabani",
    "صيني": "Sini",
    "كوري": "Kuri",
    "إنجليزي": "Ingleezi",
    "مرحبا": "marhaba",
    "كيف": "kayf",
    "حالك": "halak",
    "بخير": "bikhair",
    "جيد": "jayyid",
    "ممتاز": "mumtaz",
    "شيء": "shay",
    "كثير": "katheer",
    "قليل": "qaleel",
    "كبير": "kabeer",
    "صغير": "sagheer",
    "جميل": "jameel",
    "حلو": "hilu",
    "اسمي": "ismi",
    "اسمك": "ismak",
}

# Characters that are inherently vowel-like (produce a vowel sound)
_AR_VOWEL_CHARS = frozenset("ًٌٍَُِ")  # harakat
_AR_VOWEL_OUTPUT_CHARS = frozenset("aeiouAEIOU")


def _ar_word_to_latin(word: str) -> str:
    """Convert a single Arabic word to Latin with context-sensitive ي/و handling."""
    # Check word dictionary first
    if word in _AR_WORD_DICT:
        return _AR_WORD_DICT[word]

    parts: list[str] = []
    has_harakat = any(c in _AR_VOWEL_CHARS for c in word)

    chars = list(word)
    i = 0
    while i < len(chars):
        c = chars[i]
        # Handle و (waw): vowel "u" after a consonant, consonant "w" otherwise
        if c == "و":
            prev_output = parts[-1] if parts else ""
            prev_ends_consonant = prev_output and prev_output[-1] not in _AR_VOWEL_OUTPUT_CHARS
            parts.append("u" if prev_ends_consonant else "w")
            i += 1
            continue
        # Handle ي (ya): vowel "i" after a consonant, consonant "y" otherwise
        if c == "ي":
            prev_output = parts[-1] if parts else ""
            prev_ends_consonant = prev_output and prev_output[-1] not in _AR_VOWEL_OUTPUT_CHARS
            parts.append("i" if prev_ends_consonant else "y")
            i += 1
            continue
        mapped = _AR_BASE_MAP.get(c, c)
        parts.append(mapped)
        i += 1

    result = "".join(parts)

    # If no harakat in the original text, insert 'a' between consecutive consonants
    # to make the result more readable (rough approximation of inherent short vowels)
    if not has_harakat:
        result = _insert_arabic_short_vowels(result)

    return result


def _insert_arabic_short_vowels(text: str) -> str:
    """Insert 'a' between adjacent consonant sounds to improve readability."""
    _vowels = set("aeiouAEIOU")
    out: list[str] = []
    i = 0
    while i < len(text):
        out.append(text[i])
        # If current char is a consonant and next is also a consonant (no space/vowel between)
        if (
            i + 1 < len(text)
            and text[i] not in _vowels
            and text[i] not in (" ", "-", "'", "")
            and text[i + 1] not in _vowels
            and text[i + 1] not in (" ", "-", "'", "")
            and text[i].isalpha()
            and text[i + 1].isalpha()
        ):
            out.append("a")
        i += 1
    return "".join(out)


def to_latin_arabic(text: str) -> str:
    """Transliterate Arabic text to readable Latin, with vowel reconstruction."""
    words = text.split()
    result_words: list[str] = []
    for word in words:
        # Strip leading/trailing punctuation for lookup, re-attach after
        stripped = word.strip("،.؟!,?!;:")
        punct_pre = word[: len(word) - len(word.lstrip("،.؟!,?!;:"))]
        punct_post = word[len(stripped) + len(punct_pre):]
        romanized = _ar_word_to_latin(stripped) if stripped else ""
        result_words.append(punct_pre + romanized + punct_post)
    return " ".join(result_words)


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

    # Auto-detect script from text content when language is unknown ("" = Auto Detect)
    if not lang:
        if show_pinyin and _is_mostly_cjk(text) and not _is_mostly_japanese(text):
            return to_pinyin(text)
        if show_romaji and _is_mostly_japanese(text):
            return to_romaji(text)
        if show_romaji and _is_mostly_korean(text):
            return to_romaja(text)
        if show_latin:
            if _is_mostly_cyrillic(text):
                return to_latin_cyrillic(text)
            if _is_mostly_greek(text):
                return to_latin_greek(text)
            if _is_mostly_arabic(text):
                return to_latin_arabic(text)
            if _is_mostly_devanagari(text):
                return to_latin_hindi(text)
            if _is_mostly_thai(text):
                return to_latin_thai(text)
        return ""

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
