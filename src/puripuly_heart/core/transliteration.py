"""Pinyin and romaji transliteration utilities."""

from __future__ import annotations

import re

# Digits pass through lazy_pinyin unchanged (e.g. "1" in "第1回合" stays "1"). They
# must still be treated as their own word-like token — not glued to the neighboring
# syllable like punctuation is — so each CJK char keeps a 1:1 slot for ruby alignment.
_PINYIN_WORDLIKE_RE = re.compile(
    r"[a-zA-Z0-9āáǎàēéěèīíǐìōóǒòūúǔùǖǘǚǜü]", re.IGNORECASE
)


def _join_pinyin(syllables: list[str]) -> str:
    """Join pinyin syllables with spaces, but no spaces around punctuation."""
    parts: list[str] = []
    for syl in syllables:
        if not syl:
            continue
        is_wordlike = bool(_PINYIN_WORDLIKE_RE.search(syl))
        prev_is_wordlike = bool(parts and _PINYIN_WORDLIKE_RE.search(parts[-1]))
        if parts and is_wordlike and prev_is_wordlike:
            parts.append(" ")
        parts.append(syl)
    return "".join(parts).strip()


def to_pinyin(text: str) -> str:
    """Convert Chinese text to pinyin with tone marks (one syllable per character,
    space-separated: 朋友 -> "péng yǒu")."""
    try:
        from pypinyin import lazy_pinyin, Style
        syllables = lazy_pinyin(text, style=Style.TONE)
        return _join_pinyin(syllables)
    except Exception:
        return ""


# Syllables starting with these (after the first in a word) need a leading apostrophe
# to stay unambiguous when glued together — standard pinyin rule (西安 -> "Xī'ān").
_PINYIN_VOWEL_START = "aoeāáǎàēéěèōóǒò"


def _join_word_syllables(syllables: list[str]) -> str:
    """Glue the pinyin syllables of ONE word into a single token (no inner spaces),
    inserting an apostrophe before a vowel-initial syllable for readability."""
    out = ""
    for syl in syllables:
        if not syl:
            continue
        if out and out[-1].isalpha() and syl[0].lower() in _PINYIN_VOWEL_START:
            out += "'"
        out += syl
    return out


def to_pinyin_grouped(text: str) -> str:
    """Pinyin with syllables grouped into WORDS (朋友 -> "péngyǒu"), using jieba word
    segmentation. Falls back to per-syllable pinyin if jieba/pypinyin is unavailable."""
    try:
        import jieba
        from pypinyin import lazy_pinyin, Style

        tokens: list[str] = []
        for word in jieba.cut(text):
            if not word:
                continue
            joined = _join_word_syllables(lazy_pinyin(word, style=Style.TONE))
            if joined:
                tokens.append(joined)
        return _join_pinyin(tokens)
    except Exception:
        return to_pinyin(text)


# App-wide toggle (set from settings) controlling whether pinyin shown in-app/overlay
# and sent to the chatbox is grouped into words (péngyǒu) or per-syllable (péng yǒu).
_PINYIN_WORD_GROUPING = True


def set_pinyin_word_grouping(enabled: bool) -> None:
    global _PINYIN_WORD_GROUPING
    _PINYIN_WORD_GROUPING = bool(enabled)


def pinyin_word_grouping_enabled() -> bool:
    return _PINYIN_WORD_GROUPING


def _pinyin_for_setting(text: str) -> str:
    return to_pinyin_grouped(text) if _PINYIN_WORD_GROUPING else to_pinyin(text)


_CUTLET_INSTANCE = None

# Language-name stems that the bundled (small) unidic-lite dictionary tokenizes
# as "<stem> 語", producing romaji like "Nihon go" instead of "Nihongo". We merge
# the trailing standalone "go" (語, "language") back onto the stem in post-processing.
_JP_LANG_STEMS = (
    "nihon", "chuugoku", "kankoku", "furansu", "doitsu", "supein", "roshia",
    "itaria", "porutogaru", "arabia", "betonamu", "tai", "indoneshia",
    "mareeshia", "firipin", "toruko", "girisha", "oranda", "suweeden",
    "eigo",  # harmless no-op; kept for clarity (英語 already merges)
)
_JP_LANG_GO_RE = re.compile(
    r"\b(" + "|".join(_JP_LANG_STEMS) + r")\s+go\b", re.IGNORECASE
)


def _merge_jp_language_compounds(s: str) -> str:
    """Merge '<language-stem> go' → '<language-stem>go' (e.g. 'Nihon go' → 'Nihongo')."""
    return _JP_LANG_GO_RE.sub(lambda m: m.group(1) + "go", s)


def _get_cutlet():
    """Return a cached Cutlet instance configured for phonetic romaji.

    use_foreign_spelling defaults to True, which spells katakana loanwords with
    their original foreign spelling (テスト→"Test", ハロー→"Hello") instead of
    phonetic romaji. We want phonetic readings (テスト→"tesuto"), so disable it.
    The 日本 exception fixes the dictionary's "Nippon" reading to "Nihon".
    Caching avoids reloading the MeCab dictionary on every call.
    """
    global _CUTLET_INSTANCE
    if _CUTLET_INSTANCE is None:
        import cutlet
        ct = cutlet.Cutlet()
        ct.use_foreign_spelling = False
        ct.add_exception("日本", "nihon")
        _CUTLET_INSTANCE = ct
    return _CUTLET_INSTANCE


def warmup() -> None:
    """Pre-load the heavy transliteration backends so the FIRST live utterance doesn't
    pay their one-time init cost. cutlet/MeCab dictionary loading is ~150ms on the first
    Japanese romaji call (afterwards it's ~0.05ms); pypinyin lazy-loads its dictionary on
    the first Chinese call. Safe to call from a background thread; failures are ignored."""
    try:
        _get_cutlet().romaji("日本")
    except Exception:
        pass
    try:
        from pypinyin import lazy_pinyin
        lazy_pinyin("你好")
    except Exception:
        pass
    try:
        # jieba lazy-loads its ~5MB dictionary on the first cut (~1s); warm it so the
        # first word-grouped pinyin line isn't slow.
        import jieba
        list(jieba.cut("你好世界"))
    except Exception:
        pass
    try:
        # pykakasi backs per-character romaji; warm its dictionary on the first convert.
        from pykakasi import kakasi
        kakasi().convert("日本")
    except Exception:
        pass


def to_romaji(text: str) -> str:
    """Convert Japanese text to Hepburn romaji with proper word spacing via MeCab/cutlet."""
    try:
        ct = _get_cutlet()
        return _merge_jp_language_compounds(ct.romaji(text).strip())
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


# Hepburn romaji for each hiragana mora — the PHONETIC value (は -> "ha", not the
# particle "wa"), used for the per-character romaji mode (ungrouped), analogous to
# per-syllable pinyin. Katakana is normalized to hiragana first; kanji is resolved to
# its in-context kana reading by pykakasi before this table is applied.
_HIRA_ROMAJI = {
    "あ": "a", "い": "i", "う": "u", "え": "e", "お": "o",
    "か": "ka", "き": "ki", "く": "ku", "け": "ke", "こ": "ko",
    "が": "ga", "ぎ": "gi", "ぐ": "gu", "げ": "ge", "ご": "go",
    "さ": "sa", "し": "shi", "す": "su", "せ": "se", "そ": "so",
    "ざ": "za", "じ": "ji", "ず": "zu", "ぜ": "ze", "ぞ": "zo",
    "た": "ta", "ち": "chi", "つ": "tsu", "て": "te", "と": "to",
    "だ": "da", "ぢ": "ji", "づ": "zu", "で": "de", "ど": "do",
    "な": "na", "に": "ni", "ぬ": "nu", "ね": "ne", "の": "no",
    "は": "ha", "ひ": "hi", "ふ": "fu", "へ": "he", "ほ": "ho",
    "ば": "ba", "び": "bi", "ぶ": "bu", "べ": "be", "ぼ": "bo",
    "ぱ": "pa", "ぴ": "pi", "ぷ": "pu", "ぺ": "pe", "ぽ": "po",
    "ま": "ma", "み": "mi", "む": "mu", "め": "me", "も": "mo",
    "や": "ya", "ゆ": "yu", "よ": "yo",
    "ら": "ra", "り": "ri", "る": "ru", "れ": "re", "ろ": "ro",
    "わ": "wa", "ゐ": "i", "ゑ": "e", "を": "o", "ん": "n", "ゔ": "vu",
    "ぁ": "a", "ぃ": "i", "ぅ": "u", "ぇ": "e", "ぉ": "o",
    "ゃ": "ya", "ゅ": "yu", "ょ": "yo", "ゎ": "wa",
    "きゃ": "kya", "きゅ": "kyu", "きょ": "kyo", "ぎゃ": "gya", "ぎゅ": "gyu", "ぎょ": "gyo",
    "しゃ": "sha", "しゅ": "shu", "しょ": "sho", "じゃ": "ja", "じゅ": "ju", "じょ": "jo",
    "ちゃ": "cha", "ちゅ": "chu", "ちょ": "cho", "ぢゃ": "ja", "ぢゅ": "ju", "ぢょ": "jo",
    "にゃ": "nya", "にゅ": "nyu", "にょ": "nyo", "ひゃ": "hya", "ひゅ": "hyu", "ひょ": "hyo",
    "びゃ": "bya", "びゅ": "byu", "びょ": "byo", "ぴゃ": "pya", "ぴゅ": "pyu", "ぴょ": "pyo",
    "みゃ": "mya", "みゅ": "myu", "みょ": "myo", "りゃ": "rya", "りゅ": "ryu", "りょ": "ryo",
}
_HIRA_SMALL_Y = frozenset("ゃゅょ")


def _katakana_to_hiragana(text: str) -> str:
    out = []
    for c in text:
        o = ord(c)
        out.append(chr(o - 0x60) if 0x30A1 <= o <= 0x30F6 else c)
    return "".join(out)


def _hira_to_mora_romaji(hira: str) -> list[str]:
    out: list[str] = []
    i, n = 0, len(hira)
    pending_sokuon = False
    while i < n:
        ch = hira[i]
        # youon (きょ etc.) is one mora
        if i + 1 < n and hira[i + 1] in _HIRA_SMALL_Y and (ch + hira[i + 1]) in _HIRA_ROMAJI:
            rom, i = _HIRA_ROMAJI[ch + hira[i + 1]], i + 2
        elif ch in ("っ", "ッ"):  # sokuon — doubles the next consonant
            pending_sokuon, i = True, i + 1
            continue
        elif ch in ("ー", "～") and out:  # long-vowel mark extends the previous mora
            if out[-1] and out[-1][-1] in "aiueo":
                out[-1] += out[-1][-1]
            i += 1
            continue
        elif ch in _HIRA_ROMAJI:
            rom, i = _HIRA_ROMAJI[ch], i + 1
        else:  # punctuation / latin / digit — pass through as its own token
            out.append(ch)
            i += 1
            continue
        if pending_sokuon:
            pending_sokuon = False
            if rom.startswith("ch"):
                rom = "t" + rom
            elif rom[:1].isalpha():
                rom = rom[0] + rom
        out.append(rom)
    return out


def to_romaji_per_char(text: str) -> str:
    """Romaji split per kana/mora (UNGROUPED): 東京に行きます -> "to u kyo u ni i ki ma su".
    pykakasi resolves kanji to their in-context kana reading, then a Hepburn mora table
    romanizes each mora. Falls back to grouped romaji if pykakasi is unavailable."""
    try:
        from pykakasi import kakasi
        hira = "".join(it.get("hira", "") for it in kakasi().convert(text))
        if not hira.strip():
            return to_romaji(text)
        mora = _hira_to_mora_romaji(_katakana_to_hiragana(hira))
        return " ".join(m for m in mora if m).strip()
    except Exception:
        return to_romaji(text)


def _romaji_for_setting(text: str) -> str:
    return to_romaji(text) if _PINYIN_WORD_GROUPING else to_romaji_per_char(text)


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
            return _pinyin_for_setting(text)
        if show_romaji and _is_mostly_japanese(text):
            return _romaji_for_setting(text)
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
        return _pinyin_for_setting(text)
    if show_romaji and lang in ("ja", "jpn") and _is_mostly_japanese(text):
        return _romaji_for_setting(text)
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
