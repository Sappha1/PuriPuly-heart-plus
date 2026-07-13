"""Lightweight English spell-checking for the chat send box.

The dictionary (pyspellchecker) loads in a background thread so typing is
never blocked; until it is warm, checks return no errors. Only Latin word
tokens are checked — CJK has no 'spelling', numbers/URLs/@mentions/#tags
and a user 'personal dictionary' are skipped, matching browser behaviour.
"""

from __future__ import annotations

import logging
import os
import re
import threading

logger = logging.getLogger(__name__)

_SC = None  # SpellChecker instance once the dictionary is loaded
_LOADING = [False]
_LOCK = threading.Lock()

# Words the user added ("Add to dictionary") — persisted; plus a session
# ignore list ("Ignore once").
_USER_DICT: set[str] = set()
_SESSION_IGNORE: set[str] = set()
_USER_DICT_PATH = os.path.join(os.path.expanduser("~"), "AppData", "Local",
                               "puripuly-heart", "user_dictionary.txt")

# A word token: a letter run with internal apostrophes (don't, it's).
_WORD_RE = re.compile(r"[A-Za-z][A-Za-z'’]*")
_URL_RE = re.compile(r"https?://\S+|www\.\S+")


def _load_user_dict() -> None:
    try:
        with open(_USER_DICT_PATH, encoding="utf-8") as fh:
            for line in fh:
                w = line.strip().lower()
                if w:
                    _USER_DICT.add(w)
    except OSError:
        pass


def _do_load() -> None:
    global _SC
    try:
        from spellchecker import SpellChecker

        sc = SpellChecker(language="en", distance=2)
        _load_user_dict()
        with _LOCK:
            _SC = sc
        logger.info("[Spell] dictionary loaded (%d words, %d user)",
                    len(sc.word_frequency.dictionary), len(_USER_DICT))
    except Exception as exc:
        logger.warning("[Spell] failed to load dictionary: %s", exc)
    finally:
        _LOADING[0] = False


def warm() -> None:
    """Start loading the dictionary in the background (idempotent)."""
    with _LOCK:
        if _SC is not None or _LOADING[0]:
            return
        _LOADING[0] = True
    threading.Thread(target=_do_load, daemon=True).start()


def is_ready() -> bool:
    return _SC is not None


def _skip_token(word: str, text: str, start: int) -> bool:
    """True if this token should NOT be spell-checked."""
    if len(word) < 3:
        return True  # 1-2 letter words: too noisy (a, an, ok, hi, im…)
    if word.isupper() and len(word) <= 5:
        return True  # acronym/initialism (LOL, VRC, GG)
    if any(ch.isdigit() for ch in word):
        return True
    if start > 0 and text[start - 1] in "@#/\\.":
        return True  # @mention / #tag / path / file.ext fragment
    low = word.lower().replace("’", "'")
    return low in _USER_DICT or low in _SESSION_IGNORE


def misspelled(text: str) -> list[tuple[str, int, int]]:
    """List of (word, start, end) misspelled tokens. Empty until the
    dictionary is warm, or when the text is mostly non-Latin (CJK)."""
    sc = _SC
    if sc is None or not text:
        return []
    # URL spans are off-limits (their 'words' aren't English).
    url_spans = [(m.start(), m.end()) for m in _URL_RE.finditer(text)]

    def _in_url(i: int) -> bool:
        return any(a <= i < b for a, b in url_spans)

    out: list[tuple[str, int, int]] = []
    words_to_check: list[tuple[str, int, int]] = []
    for m in _WORD_RE.finditer(text):
        w, s, e = m.group(0), m.start(), m.end()
        if _in_url(s) or _skip_token(w, text, s):
            continue
        words_to_check.append((w, s, e))
    if not words_to_check:
        return []
    try:
        unknown = sc.unknown(
            [w.lower().replace("’", "'") for w, _s, _e in words_to_check])
    except Exception:
        return []
    for w, s, e in words_to_check:
        if w.lower().replace("’", "'") in unknown:
            out.append((w, s, e))
    return out


def suggestions(word: str, limit: int = 6) -> list[str]:
    """Best corrections for a word, most likely first."""
    sc = _SC
    if sc is None:
        return []
    low = word.lower().replace("’", "'")
    try:
        best = sc.correction(low)
        cands = sc.candidates(low) or set()
    except Exception:
        return []
    ordered: list[str] = []
    if best and best != low:
        ordered.append(best)
    # Remaining candidates by corpus frequency (most common first).
    rest = sorted((c for c in cands if c != best),
                  key=lambda c: -sc.word_frequency[c])
    for c in rest:
        if c not in ordered:
            ordered.append(c)
    # Preserve the typed word's capitalisation on the suggestions.
    if word[:1].isupper():
        ordered = [s.capitalize() if s.islower() else s for s in ordered]
    return ordered[:limit]


def add_word(word: str) -> None:
    """Add to the persistent personal dictionary."""
    low = word.lower().replace("’", "'")
    if not low:
        return
    _USER_DICT.add(low)
    sc = _SC
    if sc is not None:
        try:
            sc.word_frequency.add(low)
        except Exception:
            pass
    try:
        os.makedirs(os.path.dirname(_USER_DICT_PATH), exist_ok=True)
        with open(_USER_DICT_PATH, "a", encoding="utf-8") as fh:
            fh.write(low + "\n")
    except OSError as exc:
        logger.debug("[Spell] could not persist user word: %s", exc)


def ignore_once(word: str) -> None:
    """Ignore for this session only (not persisted)."""
    _SESSION_IGNORE.add(word.lower().replace("’", "'"))
