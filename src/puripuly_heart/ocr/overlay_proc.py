"""OCR detection overlay — standalone subprocess (prototype).

Detect-then-track with global camera-motion compensation, an extrapolating
renderer, appearance validation, and (default) VRChat-window scoping:

  * WINDOW TARGETING: with --window "VRChat" (the default launch), capture,
    detection and boxes are restricted to the VRChat window's client area,
    and everything hides whenever VRChat isn't the foreground window — no
    boxing of other apps. Without --window it runs on the whole monitor.
  * DETECTION thread (960px eq., ~0.25 s) finds text boxes ~3x/sec.
  * TRACKING loop: per delivered frame, ONE downscale + ONE batched optical
    flow call (whole-frame grid = camera motion + all boxes). Camera motion
    moves frozen boxes' anchors (world-locked text glides at any pan speed);
    freezing only suppresses residual noise. Detection refreshes SOFT-MERGE
    into live boxes.
  * APPEARANCE SIGNATURES: each box fingerprints the pixels it covers; if the
    content under a box vanishes (e.g. the escape menu closed), the box is
    dropped within a few frames instead of waiting for the next detection.
  * RENDERER (~250 Hz) extrapolates along per-box velocity so motion is
    continuous between capture frames and never trails.
  * PrintScreen saves a composited PNG to Desktop/puripuly_ocr_shots/ from an
    isolated thread.

Run directly:
    python -m puripuly_heart.ocr.overlay_proc --window VRChat
"""

from __future__ import annotations

import argparse
import ctypes
import logging
import os
import threading
import time
import tkinter as tk
from ctypes import wintypes

import numpy as np

from puripuly_heart.ocr.detector import TextBox, TextDetector

logger = logging.getLogger(__name__)

_TRANSPARENT_KEY = "#010203"
_BOX_COLOR = "#ff2020"
_BOX_WIDTH = 1

# SUBTITLE stage (recognition, no translation yet): Alt+T toggles in-place
# subtitles — each box fills and shows the text the LOCAL RapidOCR recognizer
# read from it (free, no API). Validates recognition accuracy before the
# translation stage swaps these strings for translated ones. Change the
# letter without a rebuild via
# %LOCALAPPDATA%\puripuly-heart\ocr_overlay_config.json:
#   {"translate_key": "T"}    (bind is Alt+<letter>)
_PILL_BG = "#14161a"
_PILL_TEXT = "#ffffff"

# ── Subtitle appearance / activation (all live-configurable) ──
_FMT = ["trans_only"]  # orig_trans|orig_pinyin_trans|pinyin_trans|pinyin_only|trans_only
_PLACE = ["cover"]  # cover (fill the box) | above (stack above the text)
_C_OUTLINE = ["#ff2020"]
_C_BG = ["#14161a"]
_BG_ALPHA = [100]  # 100/75/50/25 via stipple, 0 = no backdrop (shadow text)
_C_TEXT = ["#ffffff"]
_C_PY = "#5ba8a0"  # pinyin lines — same teal as the chat-log transliteration
_FONT_FIX = [0]  # subtitle font px; 0 = auto (fit box), -1 = match original
_SZ_PY = [0]  # pinyin line px override (0 = same as the main font size)
_SZ_TR = [0]  # translation line px override (0 = inherit)
_SZ_PRO = [0]  # pronoun-set boxes px override (0 = inherit)
_C_OR = [""]  # original line color ('' inherit text color, 'auto', #hex)
_C_TR = [""]  # translation line color ('' inherit, 'auto', #hex)
_C_PYO = [""]  # pinyin line color ('' = the default teal, 'auto', #hex)
_C_PRO = [""]  # pronoun-set box color ('' inherit, 'auto', #hex)
_SCAN_MODE = ["hold"]  # hold | toggle — gates ALL scanning/drawing
_SCAN_VK = [ord("E")]  # legacy single-key path (combo below preferred)
_SCAN_ACTIVE = [False]
_REGION_BORDER = [True]  # show the dashed region rectangle
_TGT_FG = [True]  # live "target window is foreground" (track loop updates)
_FLASH = [0.0]  # show the on-screen scan-state flash until this monotonic
_FLASH_TXT = [""]
_STATE_PATH = os.path.join(os.path.expanduser("~"), "AppData", "Local",
                           "puripuly-heart", "ocr_state.json")


def _write_state() -> None:
    """Tiny state file the app reads to show live scan status in the OCR
    menu — written on every scan on/off transition."""
    try:
        import json

        with open(_STATE_PATH, "w", encoding="utf-8") as fh:
            json.dump({"scan": bool(_SCAN_ACTIVE[0]),
                       "mode": _SCAN_MODE[0],
                       "fg": bool(_TGT_FG[0]),
                       "ts": time.time()}, fh)
    except Exception:
        pass


# Named non-alphanumeric bind keys: numpad + extra mouse buttons.
# GetAsyncKeyState reads mouse VKs exactly like keyboard ones.
_BIND_VK = {
    "MOUSE3": 0x04, "MOUSE4": 0x05, "MOUSE5": 0x06,
    "NUM0": 0x60, "NUM1": 0x61, "NUM2": 0x62, "NUM3": 0x63, "NUM4": 0x64,
    "NUM5": 0x65, "NUM6": 0x66, "NUM7": 0x67, "NUM8": 0x68, "NUM9": 0x69,
    "NUMMUL": 0x6A, "NUMADD": 0x6B, "NUMSUB": 0x6D, "NUMDEC": 0x6E,
    "NUMDIV": 0x6F,
    "SPACE": 0x20, "TAB": 0x09, "ENTER": 0x0D, "BACKSPACE": 0x08,
    "UP": 0x26, "DOWN": 0x28, "LEFT": 0x25, "RIGHT": 0x27,
    "HOME": 0x24, "END": 0x23, "PGUP": 0x21, "PGDN": 0x22,
    "INS": 0x2D, "DEL": 0x2E, "PAUSE": 0x13, "SCROLL": 0x91,
    "SEMI": 0xBA, "EQUALS": 0xBB, "COMMA": 0xBC, "MINUS": 0xBD,
    "PERIOD": 0xBE, "SLASH": 0xBF, "GRAVE": 0xC0, "LBRACKET": 0xDB,
    "BACKSLASH": 0xDC, "RBRACKET": 0xDD, "QUOTE": 0xDE,
}
for _fi in range(13, 25):  # F13-F24 (F1-F12 parse via the regex below)
    _BIND_VK[f"F{_fi}"] = 0x70 + _fi - 1


def _parse_bind(s: str):
    """'ALT+E' / 'CTRL+SHIFT+F5' / 'MOUSE4' / 'NUM5' -> (modifier VKs,
    key VK). Empty/invalid -> None = no bind = scanning always active."""
    s = (s or "").strip().upper()
    if not s:
        return None
    mods: list[int] = []
    key = None
    for part in s.split("+"):
        p = part.strip()
        if p == "CTRL":
            mods.append(0x11)
        elif p == "ALT":
            mods.append(0x12)
        elif p == "SHIFT":
            mods.append(0x10)
        elif p in _BIND_VK:
            key = _BIND_VK[p]
        elif len(p) == 1 and p.isalnum():
            key = ord(p)
        elif _re.fullmatch(r"F([1-9]|1[0-2])", p):
            key = 0x70 + int(p[1:]) - 1
    return (tuple(mods), key) if key is not None else None


_SCAN_COMBO: list = [_parse_bind("E")]  # legacy single-bind (migrated)
# TWO independent binds: hold = scan while the combo is down; toggle = a
# tap flips persistent scanning. Scanning is active when EITHER says so.
# Empty bind disables that mechanism; both empty = always scanning.
_HOLD_COMBO: list = [_parse_bind("E")]
_TOG_COMBO: list = [None]
_TOG_STATE = [False]


def _fmt_lines(text: str, xlat: str, pinyin: str) -> list[tuple[str, str]]:
    """Ordered subtitle lines as (text, kind) with kind in py|orig|trans.
    Pinyin always on top."""
    f = _FMT[0]
    orig, tr, py = text or "", xlat or "", pinyin or ""
    if f == "orig_trans":
        lines = [(orig, "orig"), (tr, "trans")]
    elif f == "orig_pinyin_trans":
        lines = [(py, "py"), (orig, "orig"), (tr, "trans")]
    elif f == "pinyin_trans":
        lines = [(py or orig, "py" if py else "orig"), (tr, "trans")]
    elif f == "pinyin_only":
        lines = [(py or orig, "py" if py else "orig")]
    else:
        lines = [(tr or orig, "trans")]
    return [(ln, k) for ln, k in lines if ln and ln.strip()]


def _line_px(kind: str, base: int, pron: int) -> int:
    """Per-line font px: pronoun-box override beats line-kind overrides,
    which beat the main size."""
    if pron > 0:
        return pron
    if kind == "py" and _SZ_PY[0] > 0:
        return _SZ_PY[0]
    if kind == "trans" and _SZ_TR[0] > 0:
        return _SZ_TR[0]
    return base


def _line_color(kind: str, base: str, tcol: str, pron: str) -> str:
    """Per-line color. pron (pronoun-box override) beats line-kind
    overrides, which beat the main text color. 'auto' = the color sampled
    from the original on-screen glyphs; '' = inherit."""
    v = pron or (_C_PYO[0] if kind == "py"
                 else _C_TR[0] if kind == "trans" else _C_OR[0])
    if not v:
        return _C_PY if kind == "py" else base
    if v == "auto":
        return tcol or "#ffffff"
    return v


_PY_TONE = [True]  # tone marks (nǐ hǎo) — matches the chat log's pinyin
_PY_GROUP = [True]  # jieba word grouping (nǐhǎo ma vs nǐ hǎo ma)
_JIEBA_OK = [False]  # set once the ~1s dictionary load finished (warm thread)


def _pinyin_of(text: str) -> str:
    try:
        if not any("一" <= c <= "鿿" for c in text):
            return ""
        from pypinyin import Style, lazy_pinyin

        style = Style.TONE if _PY_TONE[0] else Style.NORMAL
        if _PY_GROUP[0] and _JIEBA_OK[0]:
            # Word-grouped like the chat log: 朋友 -> péngyǒu. Only once
            # jieba is warm — its first cut loads a 5MB dict and this runs
            # under the rec claim lock.
            try:
                import jieba

                toks = ["".join(lazy_pinyin(w, style=style))
                        for w in jieba.cut(text) if w.strip()]
                return " ".join(tk for tk in toks if tk)
            except Exception:
                pass
        return " ".join(lazy_pinyin(text, style=style))
    except Exception:
        pass
    return ""


def _warm_pinyin() -> None:
    """Background: load pypinyin + jieba dictionaries so the first real
    subtitle doesn't stall the track loop."""
    try:
        from pypinyin import Style, lazy_pinyin

        lazy_pinyin("你好", style=Style.TONE)
    except Exception:
        pass
    try:
        import jieba

        list(jieba.cut("你好世界"))
        _JIEBA_OK[0] = True
    except Exception:
        pass
_DEFAULT_TRANSLATE_KEY = "T"
_VK_MENU = 0x12  # Alt
_CONFIG_PATH = os.path.join(os.path.expanduser("~"), "AppData", "Local",
                            "puripuly-heart", "ocr_overlay_config.json")
_TRANSLATE_VK = [ord(_DEFAULT_TRANSLATE_KEY)]
_SUBTITLE_ON = [False]

# Recognition handoff between the track loop (owns the boxes) and the rec
# worker (runs the model): uid -> full-res rect in, uid -> text out.
_REC_LOCK = threading.Lock()
_REC_REQ: dict[int, tuple[int, int, int, int]] = {}
_REC_OUT: dict[int, str] = {}
_REC_BATCH = 12
_REC_WORKERS = 3  # parallel CPU rec: session.run releases the GIL
# Pre-warm: recognize in the background while subtitles are OFF so Alt+T is
# instant. Costs bursts of CPU while new text is on screen; the app's OCR
# right-click menu exposes it (--prewarm 0 disables: recognition then only
# runs while subtitle mode is on).
_PREWARM = [True]
# Bubble filter (--bubbles-only): keep only boxes that look like VRChat chat
# bubbles / nameplates — a text line sitting on a UNIFORM semi-opaque pill
# with strong text contrast. World text (signs, posters, HUD junk) sits on
# varied pixels and fails the ring test; the square mute icon fails aspect.
_BUBBLES_ONLY = [False]
_BUBBLE_RING_STD = 24.0
_BUBBLE_CONTRAST = 40.0
_BUBBLE_MIN_ASPECT = 1.2
_BUBBLE_WHY = [""]  # last rejection reason (throttled diagnostics)
# VRChat pill color gate: bubbles/nameplates are mid-brightness with a
# neutral-to-BLUE tint. Excludes the solid panels that beat the uniformity
# test — near-black leaderboards, near-white screens, warm wood/cream walls.
_BUBBLE_LUM_MIN = 36.0   # post-processed (darkened) worlds tint pills low
_BUBBLE_LUM_MAX = 215.0
_BUBBLE_WARM_MAX = 8.0   # red may exceed blue by at most this
_BUBBLE_SPREAD_MAX = 72.0  # channel spread cap: pills are desaturated
# (world post-processing tints the whole scene — gates carry headroom)
# Reborn-box text inheritance: the detector blinks on borderline text (tiny
# chips like "..."), killing and re-creating its box every second or two —
# each rebirth used to visibly reset to pending and re-recognize. A box born
# where a just-read box lived inherits that text instantly.
_TEXT_CACHE_TTL = 12.0

# ── Translation stage ──
# Alt+T shows TRANSLATED text. Cost model: each unique string is translated
# exactly ONCE per session (cache below), requests only fire while subtitle
# mode is ON, text already in the target language never leaves the machine,
# and the provider is the FREE web translator — the user's DeepL quota is
# never touched by OCR. Target language comes from the app's settings
# (languages.source_language — same as peer speech translations).
_XLAT_LOCK = threading.Lock()
_XLAT_CACHE: dict[str, str] = {}
_XLAT_PENDING: list[str] = []
_XLAT_QUEUED: set[str] = set()
_XLAT_TRIES: dict[str, int] = {}
_XLAT_TARGET = ["en"]
_XLAT_SVC = ["bing"]
_XLAT_WORKERS = 2
# Master translation switch (debug aid): off = Alt+T shows the RAW recognized
# text, nothing is queued, no network traffic, no chat-feed entries.
_XLAT_ENABLED = [True]
# Foreign-only: once recognition shows a box's text is already in the user's
# language, hide the box entirely — only text the user can't read gets boxed
# (and translated). Detection itself is language-blind, so an own-language
# box is visible for the ~1s until its text is first read.
_FOREIGN_ONLY = [True]


# Ignore player names & pronouns: VRChat logs 'OnPlayerJoined <name>' to its
# output log (the same source VRCX reads). A box whose ENTIRE text is a known
# player name or a pronoun set is neither shown nor translated. A message
# that merely CONTAINS a name ("how are you doing <name>") is unaffected —
# only whole-box matches drop.
_IGNORE_NAMES = [True]
_IGNORE_PRONOUNS = [True]
_NAMES_LOCK = threading.Lock()
_PLAYER_NAMES: set[str] = set()
_NAMES_VER = [0]  # bumped when the roster grows (cached verdicts refresh)
_VRC_LOG_DIR = os.path.join(os.path.expanduser("~"), "AppData", "LocalLow",
                            "VRChat", "VRChat")
_PRONOUN_TOKENS = {
    "he", "him", "his", "she", "her", "hers", "they", "them", "theirs",
    "it", "its", "any", "all", "ask", "none", "fae", "faer", "xe", "xem",
    "ze", "zir", "hir", "ey", "em", "pronouns"}


def _norm_name(s: str) -> str:
    """Punctuation- and width-blind: '是伊花哦~' must match the logged
    '是伊花哦～' (full-width tilde) and any OCR punctuation wobble. Only
    letters/digits survive, full-width folded to half-width."""
    import unicodedata

    s = unicodedata.normalize("NFKC", s).casefold()
    return "".join(c for c in s if c.isalnum())


def _is_pronoun_text(text: str) -> bool:
    toks = [w for w in _re.split(r"[\s/|,&+·~]+", text.strip().lower()) if w]
    return bool(toks) and all(t in _PRONOUN_TOKENS for t in toks)


def _looks_truncated_bio(t: str) -> bool:
    """VRChat machine-truncates long bio/pronoun fields with a trailing
    ellipsis; chat bubbles are never cut that way. A SHORT text ending in a
    truncation mark is nameplate bio ('microwave ov…', 'INTP /…')."""
    if not (t.endswith("...") or t.endswith("..") or t.endswith("…")):
        return False
    core = _norm_name(t.rstrip(".…"))
    return 0 < len(core) <= 16


def _ignore_active() -> bool:
    return _IGNORE_NAMES[0] or _IGNORE_PRONOUNS[0]


def _is_roster_name(text: str) -> bool:
    """Whole-box match against the player roster (exact/fragment/fuzzy),
    independent of the ignore toggles — also anchors nameplate-chrome
    geometry when only the pronoun filter is on."""
    n = _norm_name(text.strip())
    if not n:
        return False
    with _NAMES_LOCK:
        if n in _PLAYER_NAMES:
            return True
        names = list(_PLAYER_NAMES)
    # FRAGMENT: the detector splits long mixed-script nameplates into pieces
    # ('AL1S__（劳kei联结）' -> 'AL1S__' + '（劳kei联结）'). A box whose whole
    # text is a contiguous piece of a roster name is that name's fragment —
    # real sentences are never substrings of a display name. CJK carries far
    # more per-glyph information, so 2 Han glyphs ('冰陈' of '冰陈oVo')
    # already identify a fragment; Latin needs 4+.
    han = sum("一" <= c <= "鿿" for c in n)
    if len(n) >= 4:
        for cand in names:
            if n in cand:
                return True
    elif han >= 2:
        # 2 Han glyphs only count at the START or END of a roster name
        # (nameplate splits break there) — mid-name matching killed real
        # 2-glyph chat messages that happened to occur inside a name.
        for cand in names:
            if cand.startswith(n) or cand.endswith(n):
                return True
    # FUZZY: OCR misreads stylized glyphs (especially CJK in names), so an
    # exact roster match is brittle. A whole-box string ~75% similar to a
    # known player, at comparable length, is that player.
    if len(n) >= 4:
        import difflib

        for cand in names:
            # 0.70 admits two misread glyphs in a 7-char name (measured:
            # OCR read 娧渶hargo for 婲淉hargo = 0.714) while the length
            # guard and whole-box scope keep real sentences out.
            if (abs(len(cand) - len(n)) <= 2
                    and difflib.SequenceMatcher(None, n, cand).ratio()
                    >= 0.70):
                return True
    return False


def _is_ignored_name(text: str) -> bool:
    t = text.strip()
    if not t:
        return False
    # Pronoun sets and truncated bio fields — their own toggle.
    if _IGNORE_PRONOUNS[0] and (_is_pronoun_text(t)
                                or _looks_truncated_bio(t)):
        return True
    return _IGNORE_NAMES[0] and _is_roster_name(t)


def _players_loop(stop: threading.Event) -> None:
    """Tail VRChat's output log for OnPlayerJoined lines (VRCX's technique)
    to keep the roster of names to ignore. Reads the whole newest log first
    (players who joined before OCR started), then follows appended lines."""
    import glob

    cur_path = None
    fh = None
    join_re = _re.compile(r"OnPlayerJoined\s+(.+?)(?:\s+\(usr_[^)]*\))?\s*$")
    while not stop.is_set():
        try:
            logs = glob.glob(os.path.join(_VRC_LOG_DIR, "output_log_*.txt"))
            if logs:
                newest = max(logs, key=os.path.getmtime)
                if newest != cur_path:
                    if fh is not None:
                        with contextlib_suppress():
                            fh.close()
                    cur_path = newest
                    fh = open(newest, "r", encoding="utf-8", errors="ignore")
                    logger.info("[OCR] tailing VRChat log for player names: %s",
                                os.path.basename(newest))
                if fh is not None:
                    added = 0
                    for line in fh.read().splitlines():
                        if "OnPlayerJoined" not in line:
                            continue
                        m = join_re.search(line)
                        if m:
                            name = m.group(1).strip()
                            if 0 < len(name) <= 64:
                                with _NAMES_LOCK:
                                    _PLAYER_NAMES.add(_norm_name(name))
                                added += 1
                    if added:
                        _NAMES_VER[0] += 1
                        with _NAMES_LOCK:
                            total = len(_PLAYER_NAMES)
                        logger.info("[OCR] player roster +%d (known: %d)",
                                    added, total)
        except Exception as exc:
            logger.debug("[OCR] player log tail error: %s", exc)
        # Fast poll: a joining player's nameplate can be detected and
        # recognized within ~1s — the roster must win that race.
        stop.wait(0.5)


# Translate-icon glyph (文A and variants — Roblox/VRChat stamp it on chat
# lines). Whole-box match of these signatures in a SQUARE-ish box is the
# icon, never a message; a real letter 'a' lives in wider boxes with more
# characters around it, so plain text is untouched.
_XLAT_ICON_SIGS = {"文a", "a文", "あa", "aあ", "文", "あ", "文字a"}


def _is_translate_icon(text: str, w: float, h: float) -> bool:
    if h <= 0 or w > 1.8 * h:
        return False  # icons are squarish; text lines are wide
    n = _norm_name(text)
    return bool(n) and len(n) <= 3 and n in _XLAT_ICON_SIGS


def _is_own_language(text: str, tgt: str) -> bool:
    """Script-level check: is this text already readable for the target
    language? Rough by design — it gates cosmetics and API-call skips."""
    han = any("一" <= c <= "鿿" for c in text)
    kana = any("぀" <= c <= "ヿ" for c in text)
    hangul = any("가" <= c <= "힯" for c in text)
    cyr = any("Ѐ" <= c <= "ӿ" for c in text)
    t = (tgt or "en").lower()
    if t.startswith("zh"):
        return han and not kana
    if t.startswith("ja"):
        return kana or han
    if t.startswith("ko"):
        return hangul
    return not (han or kana or hangul or cyr)  # latin-script targets
# Feed for the app's chat panel: each completed translation is appended here
# once; the app tails it and logs 'Received OCR' entries.
_FEED_PATH = os.path.join(os.path.expanduser("~"), "AppData", "Local",
                          "puripuly-heart", "ocr_feed.jsonl")


def _load_translation_prefs() -> None:
    try:
        import json

        p = os.path.join(os.path.expanduser("~"), "AppData", "Local",
                         "puripuly-heart", "settings.json")
        with open(p, encoding="utf-8") as fh:
            data = json.load(fh)
        lang = str(data.get("languages", {}).get("source_language") or "en")
        _XLAT_TARGET[0] = lang
        logger.info("[OCR] translation target: %s", lang)
    except Exception as exc:
        logger.debug("[OCR] settings read failed (target stays en): %s", exc)


def _xlat_loop(stop: threading.Event) -> None:
    """Translation worker: unique strings only, free web provider, results
    cached for the session. Uses the app's own language-code mapping."""
    try:
        from translators import translate_text
    except Exception as exc:
        logger.warning("[OCR] translators lib unavailable: %s", exc)
        return
    try:
        from puripuly_heart.providers.llm.free_web import _to_translator_lang
    except Exception:
        def _to_translator_lang(code: str) -> str:  # type: ignore
            return code
    while not stop.is_set():
        with _XLAT_LOCK:
            text = _XLAT_PENDING.pop(0) if _XLAT_PENDING else None
        if text is None:
            time.sleep(0.1)
            continue
        tgt = _to_translator_lang(_XLAT_TARGET[0]) or "en"
        try:
            out = str(translate_text(
                query_text=text, translator=_XLAT_SVC[0],
                from_language="auto", to_language=tgt)).strip()
            with _XLAT_LOCK:
                _XLAT_CACHE[text] = out or text
                _XLAT_QUEUED.discard(text)
            if out and out.strip() != text.strip():
                try:
                    import json as _json

                    with open(_FEED_PATH, "a", encoding="utf-8") as fh:
                        fh.write(_json.dumps(
                            {"src": text, "dst": out},
                            ensure_ascii=False) + "\n")
                except Exception:
                    pass
        except Exception as exc:
            logger.debug("[OCR] translate failed (%r): %s", text[:40], exc)
            with _XLAT_LOCK:
                n = _XLAT_TRIES.get(text, 0) + 1
                _XLAT_TRIES[text] = n
                if n >= 2:
                    _XLAT_CACHE[text] = text  # give up — show the original
                _XLAT_QUEUED.discard(text)
            time.sleep(1.0)

# Region lock: OCR restricted to a user-dragged rectangle (config-persisted).
# The app signals these named events; selection happens in-overlay (the
# window temporarily becomes clickable and dims while the user drags).
_SELECT_REGION_EVENT = "PuriPulyHeart_OCR_SelectRegion"
_CLEAR_REGION_EVENT = "PuriPulyHeart_OCR_ClearRegion"  # disables, keeps rect
_ENABLE_REGION_EVENT = "PuriPulyHeart_OCR_EnableRegion"  # re-arm saved rect
# LIVE settings: the app saves prefs to the config file and fires this event;
# the running overlay re-reads and applies them in place — no restart, no
# model reload, instant effect.
_RELOAD_PREFS_EVENT = "PuriPulyHeart_OCR_ReloadPrefs"


def _apply_prefs(cfg: dict) -> None:
    _PREWARM[0] = bool(cfg.get("prewarm", _PREWARM[0]))
    _BUBBLES_ONLY[0] = bool(cfg.get("bubbles_only", _BUBBLES_ONLY[0]))
    _FOREIGN_ONLY[0] = bool(cfg.get("foreign_only", _FOREIGN_ONLY[0]))
    _IGNORE_NAMES[0] = bool(cfg.get("ignore_names", _IGNORE_NAMES[0]))
    _IGNORE_PRONOUNS[0] = bool(cfg.get("ignore_pronouns",
                                       _IGNORE_PRONOUNS[0]))
    _XLAT_ENABLED[0] = bool(cfg.get("translate", _XLAT_ENABLED[0]))
    svc = str(cfg.get("xlat_service", _XLAT_SVC[0]) or "bing").lower()
    if svc in ("bing", "google", "papago"):
        _XLAT_SVC[0] = svc
    fmt = str(cfg.get("ocr_format", _FMT[0]))
    if fmt in ("orig_trans", "orig_pinyin_trans", "pinyin_trans",
               "pinyin_only", "trans_only"):
        _FMT[0] = fmt
    place = str(cfg.get("ocr_place", _PLACE[0]))
    if place in ("cover", "above"):
        _PLACE[0] = place
    for key, ref in (("ocr_outline", _C_OUTLINE), ("ocr_bg", _C_BG),
                     ("ocr_text", _C_TEXT)):
        v = str(cfg.get(key, ref[0]) or "")
        if _re.fullmatch(r"#[0-9a-fA-F]{6}", v) \
                or (key == "ocr_text" and v == "auto"):
            ref[0] = v
    try:
        a = int(cfg.get("ocr_bg_alpha", _BG_ALPHA[0]))
        if a in (0, 25, 50, 75, 100):
            _BG_ALPHA[0] = a
    except Exception:
        pass
    mode = str(cfg.get("scan_mode", _SCAN_MODE[0]))
    if mode in ("hold", "toggle"):
        _SCAN_MODE[0] = mode
    if "scan_bind_toggle" in cfg:
        # New dual-bind config: the two keys are independent.
        _HOLD_COMBO[0] = _parse_bind(str(cfg.get("scan_bind") or ""))
        _TOG_COMBO[0] = _parse_bind(str(cfg.get("scan_bind_toggle") or ""))
        if (_HOLD_COMBO[0] is not None
                and _HOLD_COMBO[0] == _TOG_COMBO[0]):
            # Identical binds are nonsensical — toggle wins, hold drops
            # (the UI prevents this; guards hand-edited configs).
            _HOLD_COMBO[0] = None
    elif "scan_bind" in cfg:
        # Legacy single bind: scan_mode decides which slot it fills.
        cbb = _parse_bind(str(cfg.get("scan_bind") or ""))
        if _SCAN_MODE[0] == "toggle":
            _TOG_COMBO[0], _HOLD_COMBO[0] = cbb, None
        else:
            _HOLD_COMBO[0], _TOG_COMBO[0] = cbb, None
    if "scan_bind" in cfg:
        _SCAN_COMBO[0] = _parse_bind(str(cfg.get("scan_bind") or ""))
    if "ocr_region_border" in cfg:
        _REGION_BORDER[0] = bool(int(cfg.get("ocr_region_border") or 0)) \
            if str(cfg.get("ocr_region_border")).isdigit() \
            else bool(cfg.get("ocr_region_border"))
    try:
        fp = int(cfg.get("ocr_font_px", _FONT_FIX[0]))
        if -1 <= fp <= 72:  # -1 = match the original text's size per box
            _FONT_FIX[0] = fp
    except Exception:
        pass
    for key, ref in (("ocr_size_pinyin", _SZ_PY), ("ocr_size_trans", _SZ_TR),
                     ("ocr_size_pronoun", _SZ_PRO)):
        try:
            v = int(cfg.get(key, ref[0]))
            if 0 <= v <= 72:
                ref[0] = v
        except Exception:
            pass
    for key, ref in (("ocr_color_orig", _C_OR), ("ocr_color_trans", _C_TR),
                     ("ocr_color_pinyin", _C_PYO),
                     ("ocr_color_pronoun", _C_PRO)):
        if key in cfg:
            v = str(cfg.get(key) or "")
            if v in ("", "auto") or _re.fullmatch(r"#[0-9a-fA-F]{6}", v):
                ref[0] = v
    for key, ref in (("ocr_pinyin_tone", _PY_TONE),
                     ("ocr_pinyin_group", _PY_GROUP)):
        if key in cfg:
            try:
                ref[0] = bool(int(cfg.get(key))) \
                    if str(cfg.get(key)).isdigit() else bool(cfg.get(key))
            except Exception:
                pass
    logger.info("[OCR] prefs applied live: prewarm=%d bubbles=%d foreign=%d "
                "names=%d pronouns=%d translate=%d",
                _PREWARM[0], _BUBBLES_ONLY[0], _FOREIGN_ONLY[0],
                _IGNORE_NAMES[0], _IGNORE_PRONOUNS[0], _XLAT_ENABLED[0])
_SELECT_REQ = [False]


def _load_config() -> dict:
    try:
        import json

        with open(_CONFIG_PATH, encoding="utf-8") as fh:
            cfg = json.load(fh)
        return cfg if isinstance(cfg, dict) else {}
    except Exception:
        return {}


def _save_config_key(key: str, value) -> None:
    try:
        import json

        cfg = _load_config()
        cfg[key] = value
        os.makedirs(os.path.dirname(_CONFIG_PATH), exist_ok=True)
        with open(_CONFIG_PATH, "w", encoding="utf-8") as fh:
            json.dump(cfg, fh)
    except Exception as exc:
        logger.debug("[OCR] config save failed: %s", exc)


_PIL_FONTS: dict[int, object] = {}


def _pil_font(px: int):
    f = _PIL_FONTS.get(px)
    if f is None:
        from PIL import ImageFont

        for name in ("msyh.ttc", "meiryo.ttc", "msgothic.ttc", "segoeui.ttf"):
            try:
                f = ImageFont.truetype(
                    os.path.join(os.environ.get("WINDIR", r"C:\Windows"),
                                 "Fonts", name), px)
                break
            except Exception:
                continue
        if f is None:
            from PIL import ImageFont as _IF

            f = _IF.load_default()
        _PIL_FONTS[px] = f
    return f

_TRACK_SIDE = 1280
_DETECT_SIDE = 960
_DETECT_INTERVAL = 0.05  # near back-to-back: correction latency = pass time
_PTS_X, _PTS_Y = 4, 3
_GRID_X, _GRID_Y = 8, 5

_UNFREEZE_PX = 2.0
_STILL_SPEED_PX = 0.35
_STILL_FRAMES = 8
_STILL_DECAY = 0.12

_GLOBAL_MOVE_PX = 0.04

_CUT_DIFF_HARD = 70.0
_CUT_DIFF_SOFT = 38.0
_CUT_GRID_RATIO = 0.5

_DET_STALE = 45.0
_MIN_OK_RATIO = 0.34

# SCROLL/PAN PROTECTION. Text lines look alike, so optical flow over a large
# content shift aliases: it lands a box on the WRONG line (or worse) with a
# confident score — the "boxes snap to places that never had text" bug when
# scrolling. Two gates:
#  * A detection pass whose content has shifted more than this (track px)
#    since it was captured may only VOUCH for existing boxes (keep them
#    alive) — it may not move them and may not add new ones.
_DET_VOUCH_SHIFT = 12.0
# Below this mean-abs-diff the screen is pixel-identical to the pass — the
# pass is EXACT and is applied fully, no flow opinion consulted. (Flow
# confidence is chronically low on dark/flat pages; gating on it alone froze
# every correction — boxes sat misaligned forever.)
_DET_TRUST_DIFF = 7.0
#  * A per-frame box motion that disagrees with the global motion by more
#    than this is an aliasing jump, not real motion (real relative motion
#    between consecutive frames is tiny) — the box follows the world instead.
_FLOW_JUMP_PX = 18.0

# Merge/miss tolerance. The detector is nondeterministic on borderline text —
# it can find a line on one pass and miss it on the next, or alternate the
# box extents. A strict match + fast miss-delete made such boxes blink at the
# detection cadence (~0.5s). Boxes now survive several missed passes (their
# content is still verified text-like every frame by texture/appearance) and
# match more loosely so extent wobble updates the box instead of replacing it.
_MERGE_IOU = 0.25
_MERGE_BLEND = 0.60
# PERMISSIVE PROFILE (user-chosen): the high-recall pre-filter pipeline
# "detected everything" best. The recognition gate and content-kill checks
# cost too much recall/latency, so they are OFF; detection publishes every
# text-shaped region and tracking keeps it glued. Rec stays available for
# the future translation stage.
_REC_GATE_ENABLED = False
_TEXTURE_KILL_ENABLED = False
_SIG_KILL_ENABLED = False

_REC_MIN_SCORE = 0.50
_REC_MAX_BOXES = 25          # rec budget per detection pass
import re as _re
_REC_TEXTY = _re.compile(r"[0-9A-Za-z一-鿿぀-ヿ가-힯]")

# Early-version persistence profile: garbage dies within 1-2 passes. The
# generous 6/3 tolerance (anti-blink overcorrection) let stale/junk boxes
# linger for many seconds after view changes.
_MAX_MISSES_CONFIRMED = 2
_MAX_MISSES_UNPROVEN = 1
# When a still-camera detection corroborates <34% of current boxes, the view
# changed (app switch, teleport): drop every uncorroborated box immediately.
_COHERENCE_MIN = 0.34
_CONFIRMED_AT = 2

# Appearance signature: sample grid inside each box; if the mean abs gray
# difference vs the remembered fingerprint exceeds this for a few consecutive
# frames, the content is gone (menu closed / bubble expired) — drop the box.
_SIG_X, _SIG_Y = 6, 2
_SIG_DIFF = 42.0
_SIG_BAD_FRAMES = 4
# A box detection re-confirmed this recently is REAL text — never sig-kill it
# (stops the kill/re-detect flash loop on high-contrast text).
_SIG_CONFIRM_GRACE_S = 0.7

# The game renders its own mouse cursor INTO the frame; pixels under it really
# change. Flow points within this radius (track px) of the cursor are skipped
# and content checks are suspended for boxes the cursor is touching.
_CURSOR_RADIUS = 18.0

# Focus-loss debounce. Foreground can blip for milliseconds (the app's own
# always-on-top subtitle overlay re-asserting bounds, OS transitions); reacting
# instantly nuked all boxes and forced a ~0.5s rebuild — visible flashing on
# perfectly static text. Only a SUSTAINED focus change hides the boxes.
_FG_GRACE_S = 0.6

# A small box centered on the cursor IS the cursor: the pointer glyph passes
# the text-shape filter and (being fully inside the cursor radius) even tracks
# its motion. Boxes this small, this close to the pointer, are never kept.
_CURSOR_BOX_DIST = 22.0
_CURSOR_BOX_W = 42.0
_CURSOR_BOX_H = 28.0


def _is_cursor_box(x1: float, y1: float, x2: float, y2: float,
                   cursor: tuple[float, float] | None) -> bool:
    if cursor is None:
        return False
    if (x2 - x1) > _CURSOR_BOX_W or (y2 - y1) > _CURSOR_BOX_H:
        return False
    cx, cy = (x1 + x2) / 2, (y1 + y2) / 2
    return ((cx - cursor[0]) ** 2 + (cy - cursor[1]) ** 2
            <= _CURSOR_BOX_DIST * _CURSOR_BOX_DIST)

# Texture check: text has strong local contrast. A box whose content's std
# stays below this is sitting on a featureless surface (floor/wall a ghost
# drifted onto) — kill it. Applies even during motion, since flat is flat.
_TEX_MIN_STD = 10.0
_TEX_BAD_FRAMES = 4

# FLAT-KILL (always on, unlike the sparse-sample texture check): full-patch
# std below this = solid color; no text of any faintness measures this low.
# Kills scroll ghosts sitting on bare background DURING the scroll (~0.1s)
# instead of waiting for the next trusted detection pass.
_FLAT_KILL_STD = 6.0
_FLAT_BAD_FRAMES = 3

_RENDER_LEAD_S = 0.016
_RENDER_EXTRAP_CAP_S = 0.06

# Capture watchdog: recreate the DXGI camera if no frame arrives this long
# (PrintScreen/snipping tools can invalidate desktop duplication silently).
_CAPTURE_STALL_S = 3.0

_VK_SNAPSHOT = 0x2C
_SHOT_DIR = os.path.join(os.path.expanduser("~"), "Desktop", "puripuly_ocr_shots")


def _hwnd_exe(hwnd: int) -> str:
    """Basename of the process image owning hwnd (lowercase), '' on failure.
    Lets window targeting require the real vrchat.exe, not just the title."""
    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    try:
        pid = wintypes.DWORD(0)
        ctypes.windll.user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        h = ctypes.windll.kernel32.OpenProcess(
            PROCESS_QUERY_LIMITED_INFORMATION, False, pid.value)
        if not h:
            return ""
        try:
            buf = ctypes.create_unicode_buffer(4096)
            size = wintypes.DWORD(4096)
            if ctypes.windll.kernel32.QueryFullProcessImageNameW(
                    h, 0, buf, ctypes.byref(size)):
                return os.path.basename(buf.value).lower()
        finally:
            ctypes.windll.kernel32.CloseHandle(h)
    except Exception:
        pass
    return ""


def _set_dpi_aware() -> None:
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass


def _make_click_through(root: tk.Tk) -> None:
    GWL_EXSTYLE = -20
    WS_EX_LAYERED = 0x00080000
    WS_EX_TRANSPARENT = 0x00000020
    WS_EX_TOOLWINDOW = 0x00000080
    try:
        hwnd = ctypes.windll.user32.GetParent(root.winfo_id())
        styles = ctypes.windll.user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
        ctypes.windll.user32.SetWindowLongW(
            hwnd, GWL_EXSTYLE,
            styles | WS_EX_LAYERED | WS_EX_TRANSPARENT | WS_EX_TOOLWINDOW,
        )
    except Exception as exc:
        logger.debug("[OCR] click-through setup failed: %s", exc)


def _exclude_from_capture(root: tk.Tk) -> None:
    WDA_EXCLUDEFROMCAPTURE = 0x00000011
    try:
        hwnd = ctypes.windll.user32.GetParent(root.winfo_id())
        ctypes.windll.user32.SetWindowDisplayAffinity(hwnd, WDA_EXCLUDEFROMCAPTURE)
    except Exception as exc:
        logger.debug("[OCR] exclude-from-capture failed: %s", exc)


_PARENT_PID = [0]  # the PuriPuly app process (set from --parent-pid)
_APP_PIDS: list = [frozenset(), 0.0]  # cached [pids, stamp]
_OCCL_DBG: list = [[]]  # "window title#pid" of current occluders (logging)


def _app_pids() -> frozenset:
    """PIDs of the parent app and its children (flet renderer hosts the
    visible windows), EXCLUDING this overlay process and its launcher
    chain — our own fullscreen tk window must never blank the capture."""
    now = time.monotonic()
    if _APP_PIDS[0] and now - _APP_PIDS[1] < 5.0:
        return _APP_PIDS[0]
    root = _PARENT_PID[0]
    if not root:
        return frozenset()
    pids: set[int] = {root}
    try:
        k32 = ctypes.windll.kernel32

        class _PE32(ctypes.Structure):
            _fields_ = [("dwSize", wintypes.DWORD),
                        ("cntUsage", wintypes.DWORD),
                        ("th32ProcessID", wintypes.DWORD),
                        ("th32DefaultHeapID", ctypes.POINTER(ctypes.c_ulong)),
                        ("th32ModuleID", wintypes.DWORD),
                        ("cntThreads", wintypes.DWORD),
                        ("th32ParentProcessID", wintypes.DWORD),
                        ("pcPriClassBase", ctypes.c_long),
                        ("dwFlags", wintypes.DWORD),
                        ("szExeFile", ctypes.c_wchar * 260)]

        snap = k32.CreateToolhelp32Snapshot(0x2, 0)  # TH32CS_SNAPPROCESS
        ppid: dict[int, int] = {}
        if snap and snap != -1:
            pe = _PE32()
            pe.dwSize = ctypes.sizeof(_PE32)
            if k32.Process32FirstW(snap, ctypes.byref(pe)):
                while True:
                    ppid[int(pe.th32ProcessID)] = int(pe.th32ParentProcessID)
                    if not k32.Process32NextW(snap, ctypes.byref(pe)):
                        break
            k32.CloseHandle(snap)
        # our own launcher chain up to (not including) the app root —
        # never blank our own fullscreen tk overlay
        mine: set[int] = set()
        p = os.getpid()
        while p and p != root and p not in mine and len(mine) < 32:
            mine.add(p)
            p = ppid.get(p, 0)
        children: dict[int, list[int]] = {}
        for c, par in ppid.items():
            children.setdefault(par, []).append(c)
        queue = [root]
        while queue:
            cur = queue.pop()
            for c in children.get(cur, ()):
                if c not in pids and c not in mine:
                    pids.add(c)
                    queue.append(c)
        pids -= mine
    except Exception:
        pass
    result = frozenset(pids - {os.getpid()})
    _APP_PIDS[0], _APP_PIDS[1] = result, now
    return result


class _Capture:
    def __init__(self) -> None:
        self._cam = None
        self._sct = None
        self._mon = None
        self.width = self.height = 0
        self.left = self.top = 0
        try:
            import dxcam

            self._cam = dxcam.create(output_color="BGR")
            frame = None
            for _ in range(10):
                frame = self._cam.grab()
                if frame is not None:
                    break
                time.sleep(0.01)
            if frame is None:
                raise RuntimeError("dxcam produced no frame")
            self.height, self.width = frame.shape[:2]
            self._last = frame
            self._last_ok = time.monotonic()
            logger.info("[OCR] capture: dxcam %dx%d", self.width, self.height)
        except Exception as exc:
            logger.warning("[OCR] dxcam unavailable (%s); using mss", exc)
            import mss

            self._sct = mss.mss()
            self._mon = self._sct.monitors[1]
            self.width, self.height = self._mon["width"], self._mon["height"]
            self.left, self.top = self._mon["left"], self._mon["top"]
            self._last = np.asarray(self._sct.grab(self._mon))[:, :, :3]

    def grab(self) -> np.ndarray | None:
        if self._cam is not None:
            try:
                f = self._cam.grab()
            except Exception as exc:
                logger.warning("[OCR] dxcam grab failed (%s); reinitializing", exc)
                self._reinit()
                return None
            if f is not None:
                self._last = f
                self._last_ok = time.monotonic()
                return f
            # Watchdog: PrintScreen/snipping tools can silently invalidate the
            # DXGI duplication session — grab() then returns None forever even
            # though the screen is changing. Recreate the camera after a stall.
            if time.monotonic() - getattr(self, "_last_ok", 0.0) > _CAPTURE_STALL_S:
                logger.info("[OCR] capture stalled %.1fs — recreating camera",
                            _CAPTURE_STALL_S)
                self._reinit()
            return None
        if self._sct is None:
            # between failed dxcam reinit attempts: no camera yet
            if time.monotonic() - getattr(self, "_last_ok", 0.0) > _CAPTURE_STALL_S:
                self._reinit()
            return None
        f = np.asarray(self._sct.grab(self._mon))[:, :, :3]
        self._last = f
        self._last_ok = time.monotonic()
        return f

    def _reinit(self) -> None:
        self._last_ok = time.monotonic()
        self._reinits = getattr(self, "_reinits", 0) + 1
        # RELEASE, don't just del: dxcam's factory caches camera instances
        # and only drops RELEASED ones — del alone made create() hand back
        # the same dead duplicator, so the stall watchdog "recovered" into
        # the very camera that was broken (scan death until OCR restart).
        try:
            if self._cam is not None:
                self._cam.release()
        except Exception as exc:
            logger.debug("[OCR] dxcam release failed: %s", exc)
        self._cam = None
        if self._reinits > 4:
            # dxcam keeps dying — permanent mss fallback beats a dead feed
            logger.warning("[OCR] dxcam died %d times — falling back to mss",
                           self._reinits)
            try:
                import mss

                self._sct = mss.mss()
                self._mon = self._sct.monitors[1]
                self._last = np.asarray(
                    self._sct.grab(self._mon))[:, :, :3]
                logger.info("[OCR] capture: mss fallback active")
            except Exception as exc:
                logger.warning("[OCR] mss fallback failed: %s", exc)
            return
        try:
            import dxcam

            self._cam = dxcam.create(output_color="BGR")
            f = self._cam.grab() if self._cam else None
            logger.info("[OCR] dxcam recreated (attempt %d, first frame: %s)",
                        self._reinits, "ok" if f is not None else "none yet")
        except Exception as exc:
            logger.warning("[OCR] dxcam recreate failed: %s", exc)

    def last(self) -> np.ndarray:
        return self._last


class _Target:
    """Tracks the capture region: a specific window's client area (VRChat
    mode) or the whole monitor (global mode). epoch increments whenever the
    region changes so both loops can resynchronize. Also computes OCCLUSIONS:
    regions of the target covered by other windows (chat apps, file explorer,
    the translator itself) — those pixels belong to other apps and must never
    be scanned or boxed."""

    def __init__(self, title: str | None, cap: _Capture) -> None:
        self._title = title or None
        # Title alone is spoofable; for the game we demand the actual exe.
        self._exe = "vrchat.exe" if (title or "").lower() == "vrchat" else ""
        self._cap = cap
        self._lock = threading.Lock()
        self._rect: tuple[int, int, int, int] | None = (0, 0, cap.width, cap.height)
        self._fg = True
        self._fg_title = ""
        self._epoch = 0
        self._warned = False
        self._occl: list[tuple[int, int, int, int]] = []
        # Region lock (whole-screen mode only): restrict OCR to a
        # user-dragged rectangle, persisted in the config file.
        # Regions are PER TARGET WINDOW: {'': whole-screen, 'VRChat': ...}.
        # Each entry: {'rect': [x1,y1,x2,y2], 'on': bool}. Legacy single
        # 'region' key migrates into the '' slot.
        self._regions: dict[str, dict] = {}
        try:
            cfg = _load_config()
            regs = cfg.get("regions")
            if isinstance(regs, dict):
                for k, e in regs.items():
                    r = (e or {}).get("rect")
                    if (isinstance(r, list) and len(r) == 4
                            and r[2] - r[0] >= 64 and r[3] - r[1] >= 64):
                        self._regions[str(k)] = {
                            "rect": [int(v) for v in r],
                            "on": bool((e or {}).get("on", True))}
            elif isinstance(cfg.get("region"), list):
                r = cfg["region"]
                if len(r) == 4 and r[2] - r[0] >= 64 and r[3] - r[1] >= 64:
                    entry = {"rect": [int(v) for v in r],
                             "on": bool(cfg.get("region_enabled", True))}
                    # Legacy single region: seed BOTH the whole-screen slot
                    # and the configured target window's slot, so the old
                    # rectangle keeps working after per-window migration.
                    self._regions[""] = dict(entry)
                    wt = str(cfg.get("window_title") or "") or (title or "")
                    if wt:
                        self._regions[wt] = dict(entry)
            if self._regions:
                logger.info("[OCR] regions restored: %s",
                            {k: v["on"] for k, v in self._regions.items()})
        except Exception:
            pass
        if self._title:
            self._rect = None  # resolved by poll()

    def _rkey(self) -> str:
        return self._title or ""

    def _save_regions(self) -> None:
        _save_config_key("regions", self._regions)

    def _active_region(self) -> tuple[int, int, int, int] | None:
        e = self._regions.get(self._rkey())
        if e and e.get("on") and e.get("rect"):
            r = e["rect"]
            return (r[0], r[1], r[2], r[3])
        return None

    def set_title(self, title: str | None) -> None:
        """Live retarget: switch between whole-screen (None/empty) and any
        window title without restarting the overlay. The epoch bump from the
        next poll resynchronizes both loops onto the new region."""
        title = (title or "").strip() or None
        with self._lock:
            if title == self._title:
                return
            self._title = title
            self._exe = ("vrchat.exe"
                         if (title or "").lower() == "vrchat" else "")
            self._warned = False
            if title:
                self._rect = None  # resolved by the next poll
        logger.info("[OCR] target switched to %r",
                    title or "whole screen")
        self.poll()

    def set_region(self, rect: tuple[int, int, int, int] | None) -> None:
        """A fresh drag: store the rect for the CURRENT target and arm it."""
        if rect is None:
            self.set_region_enabled(False)
            return
        with self._lock:
            key = self._rkey()
            self._regions[key] = {"rect": [int(v) for v in rect], "on": True}
            self._save_regions()
        logger.info("[OCR] region set for %r: %s", key or "whole screen",
                    rect)
        self.poll()

    def set_region_enabled(self, enabled: bool) -> None:
        """Toggle the CURRENT target's lock, keeping its rectangle."""
        with self._lock:
            e = self._regions.get(self._rkey())
            if e is None:
                return  # nothing saved for this target
            e["on"] = bool(enabled)
            self._save_regions()
        logger.info("[OCR] region lock for %r %s (rect kept)",
                    self._rkey() or "whole screen",
                    "enabled" if enabled else "disabled")
        self.poll()

    def region(self) -> tuple[int, int, int, int] | None:
        with self._lock:
            return self._active_region()

    @staticmethod
    def _occlusions_for(hwnd: int, sx1: int, sy1: int, sx2: int, sy2: int
                        ) -> list[tuple[int, int, int, int]]:
        """Screen rects of visible windows ABOVE hwnd that overlap it, in
        target-local coordinates. EnumWindows yields top-to-bottom z-order."""
        user32 = ctypes.windll.user32
        GWL_EXSTYLE = -20
        WS_EX_TRANSPARENT = 0x00000020
        WS_EX_LAYERED = 0x00080000
        DWMWA_CLOAKED = 14
        order: list[int] = []

        @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        def _cb(hw, _lp):
            order.append(int(hw))
            return True

        user32.EnumWindows(_cb, 0)
        occl: list[tuple[int, int, int, int]] = []
        dbg: list[str] = []

        def _note(hw, wpid):
            buf = ctypes.create_unicode_buffer(48)
            user32.GetWindowTextW(hw, buf, 48)
            dbg.append("%s#%d" % (buf.value or "?", wpid))

        for hw in order:
            if hw == hwnd:
                break  # everything after is beneath the target
            try:
                if not user32.IsWindowVisible(hw):
                    continue
                wpid = wintypes.DWORD(0)
                user32.GetWindowThreadProcessId(hw, ctypes.byref(wpid))
                if wpid.value and wpid.value in _app_pids():
                    # The app's OWN windows (chat overlay floating over the
                    # game): always blank their rect so OCR never reads our
                    # own subtitles/chat back — even though they're layered
                    # click-through windows the skips below would pass.
                    r = wintypes.RECT()
                    user32.GetWindowRect(hw, ctypes.byref(r))
                    ix1, iy1 = max(sx1, r.left), max(sy1, r.top)
                    ix2, iy2 = min(sx2, r.right), min(sy2, r.bottom)
                    if ix2 - ix1 > 8 and iy2 - iy1 > 8:
                        occl.append((ix1 - sx1, iy1 - sy1,
                                     ix2 - sx1, iy2 - sy1))
                        _note(hw, wpid.value)
                    continue
                exs = user32.GetWindowLongW(hw, GWL_EXSTYLE)
                if exs & WS_EX_TRANSPARENT:
                    continue  # click-through overlays (incl. our own boxes)
                if exs & WS_EX_LAYERED:
                    # Toasts and game-overlay hosts (Discord/Steam popups,
                    # notification banners) are layered windows; several
                    # keep a screen-sized 'visible' host alive after the
                    # toast, which blanked the ENTIRE scan region and made
                    # OCR 'randomly die'. Real occluding app windows are
                    # not layered.
                    continue
                cloaked = wintypes.DWORD(0)
                try:
                    ctypes.windll.dwmapi.DwmGetWindowAttribute(
                        hw, DWMWA_CLOAKED, ctypes.byref(cloaked),
                        ctypes.sizeof(cloaked))
                except Exception:
                    pass
                if cloaked.value:
                    continue  # invisible UWP shells report visible otherwise
                r = wintypes.RECT()
                user32.GetWindowRect(hw, ctypes.byref(r))
                ix1, iy1 = max(sx1, r.left), max(sy1, r.top)
                ix2, iy2 = min(sx2, r.right), min(sy2, r.bottom)
                if ix2 - ix1 > 8 and iy2 - iy1 > 8:
                    occl.append((ix1 - sx1, iy1 - sy1, ix2 - sx1, iy2 - sy1))
                    _note(hw, wpid.value)
                if len(occl) >= 12:
                    break
            except Exception:
                continue
        _OCCL_DBG[0] = dbg
        return occl

    def _find_window(self, title: str) -> int:
        """Largest VISIBLE window with this exact title (and, when set, the
        required exe). FindWindowW returns the first title match, which can
        be one of the game's hidden helper windows — that intermittently
        blanked VRChat-only mode."""
        user32 = ctypes.windll.user32
        need_exe = self._exe
        matches: list[tuple[int, int]] = []

        @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        def _cb(hwnd, _lp):
            try:
                if not user32.IsWindowVisible(hwnd):
                    return True
                n = user32.GetWindowTextLengthW(hwnd)
                if n <= 0:
                    return True
                buf = ctypes.create_unicode_buffer(n + 1)
                user32.GetWindowTextW(hwnd, buf, n + 1)
                if buf.value == title:
                    if need_exe and _hwnd_exe(hwnd) != need_exe:
                        return True
                    r = wintypes.RECT()
                    user32.GetClientRect(hwnd, ctypes.byref(r))
                    matches.append((int(r.right) * int(r.bottom), int(hwnd)))
            except Exception:
                pass
            return True

        user32.EnumWindows(_cb, 0)
        return max(matches)[1] if matches else 0

    def poll(self) -> None:
        if not self._title:
            # Whole-screen mode: the "window" is the monitor, or the locked
            # region when one is set. Epoch bumps resync both loops.
            with self._lock:
                r = self._active_region()
                rect_new = r if r else (0, 0, self._cap.width,
                                        self._cap.height)
                rect_new = (max(0, rect_new[0]), max(0, rect_new[1]),
                            min(self._cap.width, rect_new[2]),
                            min(self._cap.height, rect_new[3]))
                if rect_new != self._rect:
                    self._rect = rect_new
                    self._epoch += 1
                self._fg = True
                self._occl = []
            return
        user32 = ctypes.windll.user32
        rect_new: tuple[int, int, int, int] | None = None
        occl_new: list[tuple[int, int, int, int]] = []
        fg = False
        try:
            # FOREGROUND-FIRST: with multiple same-titled windows (the user
            # runs TWO VRChat instances — identical 1920x1080 windows!), size
            # cannot identify the one being played. The focused one can.
            hwnd = 0
            fgw = user32.GetForegroundWindow()
            if fgw and user32.IsWindowVisible(fgw):
                n = user32.GetWindowTextLengthW(fgw)
                if n > 0:
                    b = ctypes.create_unicode_buffer(n + 1)
                    user32.GetWindowTextW(fgw, b, n + 1)
                    if b.value == self._title and (
                            not self._exe or _hwnd_exe(fgw) == self._exe):
                        hwnd = fgw
            if not hwnd:
                hwnd = self._find_window(self._title)
            if hwnd:
                # DWM extended frame bounds: PHYSICAL screen pixels, immune to
                # DPI virtualization. GetClientRect/ClientToScreen return the
                # game's LOGICAL coords when it runs windowed under display
                # scaling (measured: 1920x1080 reported for a screen-filling
                # window) — the scan then covered a corner of the game and
                # every box landed offset.
                x1 = y1 = x2 = y2 = 0
                got = False
                try:
                    eb = wintypes.RECT()
                    DWMWA_EXTENDED_FRAME_BOUNDS = 9
                    if ctypes.windll.dwmapi.DwmGetWindowAttribute(
                            hwnd, DWMWA_EXTENDED_FRAME_BOUNDS,
                            ctypes.byref(eb), ctypes.sizeof(eb)) == 0:
                        x1 = eb.left - self._cap.left
                        y1 = eb.top - self._cap.top
                        x2 = eb.right - self._cap.left
                        y2 = eb.bottom - self._cap.top
                        got = True
                except Exception:
                    pass
                if not got:
                    r = wintypes.RECT()
                    user32.GetClientRect(hwnd, ctypes.byref(r))
                    pt = wintypes.POINT(0, 0)
                    user32.ClientToScreen(hwnd, ctypes.byref(pt))
                    x1 = pt.x - self._cap.left
                    y1 = pt.y - self._cap.top
                    x2, y2 = x1 + r.right, y1 + r.bottom
                x1 = max(0, min(self._cap.width, x1))
                y1 = max(0, min(self._cap.height, y1))
                x2 = max(0, min(self._cap.width, x2))
                y2 = max(0, min(self._cap.height, y2))
                # Region lock applies in window mode too: scan only where
                # the VRChat window and the user's rectangle agree (it was
                # silently ignored here — dragging "did nothing").
                with self._lock:
                    r = self._active_region()
                if r is not None:
                    ix1, iy1 = max(x1, r[0]), max(y1, r[1])
                    ix2, iy2 = min(x2, r[2]), min(y2, r[3])
                    if ix2 - ix1 >= 64 and iy2 - iy1 >= 64:
                        x1, y1, x2, y2 = ix1, iy1, ix2, iy2
                if x2 - x1 >= 64 and y2 - y1 >= 64:
                    rect_new = (x1, y1, x2, y2)
                    occl_new = self._occlusions_for(
                        hwnd,
                        x1 + self._cap.left, y1 + self._cap.top,
                        x2 + self._cap.left, y2 + self._cap.top)
                else:
                    occl_new = []
                fgw = user32.GetForegroundWindow()
                fg = fgw == hwnd
                fg_title = ""
                if not fg and fgw:
                    m = user32.GetWindowTextLengthW(fgw)
                    if m > 0:
                        b2 = ctypes.create_unicode_buffer(m + 1)
                        user32.GetWindowTextW(fgw, b2, m + 1)
                        fg_title = b2.value
                self._fg_title = fg_title
                # NOTE: no "friendly focus" for our own app. With the app in
                # front, the scanned region contains the app's UI — whose
                # blinking caret/spinners are REAL appearing/disappearing text
                # to the detector, i.e. permanently flashing boxes. VRChat
                # focused = scan; anything else focused = idle. (Short blips
                # are still absorbed by the debounce in the track loop.)
        except Exception as exc:
            logger.debug("[OCR] window poll failed: %s", exc)
        with self._lock:
            if rect_new != self._rect:
                self._rect = rect_new
                self._epoch += 1
            self._fg = fg
            new_occl = occl_new if rect_new is not None else []
            if len(new_occl) != len(self._occl):
                logger.info("[OCR] occlusions: %d -> %d %s from %s",
                            len(self._occl), len(new_occl), new_occl[:4],
                            _OCCL_DBG[0][:4])
            self._occl = new_occl
        if rect_new is None and not self._warned:
            self._warned = True
            logger.info("[OCR] window '%s' not found — boxes hidden until it appears",
                        self._title)
        elif rect_new is not None:
            self._warned = False

    def get(self) -> tuple[tuple[int, int, int, int] | None, bool, int]:
        with self._lock:
            return self._rect, self._fg, self._epoch

    def fg_title(self) -> str:
        with self._lock:
            return self._fg_title

    def occlusions(self) -> list[tuple[int, int, int, int]]:
        with self._lock:
            return list(self._occl)


def _target_loop(target: _Target, stop: threading.Event) -> None:
    while not stop.is_set():
        target.poll()
        stop.wait(0.4)


class _Anchors:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.boxes: list[TextBox] = []
        self.gray: np.ndarray | None = None
        self.epoch = -1
        self.stamp = 0

    def publish(self, boxes: list[TextBox], gray: np.ndarray, epoch: int) -> None:
        with self._lock:
            self.boxes, self.gray, self.epoch = boxes, gray, epoch
            self.stamp += 1

    def take(self, last_stamp: int):
        with self._lock:
            if self.stamp == last_stamp:
                return None
            return self.stamp, list(self.boxes), self.gray, self.epoch


def _text_plausible(b: TextBox, det_w: int, det_h: int) -> bool:
    # Minimal filter (permissive profile): reject only specks and
    # screen-sized slabs; everything else gets its chance.
    w = b.x2 - b.x1
    h = b.y2 - b.y1
    if w < 8 or h < 5:
        return False
    if w * h < 80:
        return False
    if h > 0.5 * det_h and w > 0.9 * det_w:
        return False
    return True


def _looks_like_bubble(bgr: np.ndarray, b: TextBox) -> bool:
    """Chat bubble test: at least ONE side of the text line is uniform pill
    fill, and the text contrasts strongly against that fill. Sides only —
    in a multi-line bubble the pixels above/below a line are OTHER TEXT
    (demanding a fully uniform ring rejected every middle line), and a
    nameplate's profile icon ruins one side, so one clean side suffices."""
    H, W = bgr.shape[:2]
    bw, bh = b.x2 - b.x1, b.y2 - b.y1
    if bh <= 0 or bw < _BUBBLE_MIN_ASPECT * bh:
        _BUBBLE_WHY[0] = "aspect"
        return False
    y1, y2 = max(0, b.y1), min(H, b.y2)
    if y2 - y1 < 3:
        _BUBBLE_WHY[0] = "tiny"
        return False
    ring_lum = None
    why = "no-uniform-side"
    # Probe a NARROW strip first, then a wider one: tight-padded bubbles
    # (big text, small pill margin) fail the wide strip because it crosses
    # the pill edge onto the world — the narrow strip stays inside.
    for m in (max(3, bh // 4), max(4, bh // 2)):
        for sx1, sx2 in ((max(0, b.x1 - m), b.x1),
                         (b.x2, min(W, b.x2 + m))):
            if sx2 - sx1 < 3:
                continue
            strip = bgr[y1:y2, sx1:sx2].astype(np.float32).reshape(-1, 3)
            if strip.shape[0] < 12:
                continue
            if float(strip.std(axis=0).max()) > _BUBBLE_RING_STD:
                continue
            mbgr = strip.mean(axis=0)  # B, G, R
            lum = float(mbgr.mean())
            if not (_BUBBLE_LUM_MIN <= lum <= _BUBBLE_LUM_MAX):
                why = f"lum={lum:.0f}"  # near-black panel / white screen
                continue
            if float(mbgr[2] - mbgr[0]) > _BUBBLE_WARM_MAX:
                why = "warm"  # wood, cream, paper — not a VRChat pill
                continue
            if float(mbgr.max() - mbgr.min()) > _BUBBLE_SPREAD_MAX:
                why = f"sat={mbgr.max() - mbgr.min():.0f}"
                continue
            ring_lum = lum
            break
        if ring_lum is not None:
            break
    if ring_lum is None:
        _BUBBLE_WHY[0] = why
        return False  # neither side sits on a solid pill fill
    inner = bgr[y1:y2, max(0, b.x1):min(W, b.x2)].astype(np.float32)
    if inner.size < 48:
        return False
    lum = inner.mean(axis=2)
    bright = float(np.percentile(lum, 92))
    dark = float(np.percentile(lum, 8))
    ok = (bright - ring_lum > _BUBBLE_CONTRAST
          or ring_lum - dark > _BUBBLE_CONTRAST)
    if not ok:
        _BUBBLE_WHY[0] = "low-contrast"
    return ok


def _glyph_color(crop: np.ndarray) -> str:
    """Dominant color of the glyph pixels (#rrggbb, '' when too flat to
    tell). Glyphs are the ~20% of pixels whose luminance deviates most
    from the crop median, so it works for bright-on-dark and dark-on-
    bright alike — a gold nameplate yields gold, not white."""
    try:
        small = crop if crop.shape[0] * crop.shape[1] <= 24000 \
            else crop[::2, ::2]
        f = small.reshape(-1, 3).astype(np.float32)
        lum = f @ np.array([0.114, 0.587, 0.299], np.float32)  # BGR order
        med = float(np.median(lum))
        dev = np.abs(lum - med)
        thr = float(np.percentile(dev, 80))
        if thr < 14:
            return ""  # flat crop: no confident glyph/background split
        sel = f[dev >= thr]
        b = int(np.median(sel[:, 0]))
        g = int(np.median(sel[:, 1]))
        r = int(np.median(sel[:, 2]))
        # Near-black text would vanish against the dark pill bg — lift it.
        if r + g + b < 120:
            return "#e8e8e8"
        return "#%02x%02x%02x" % (r, g, b)
    except Exception:
        return ""


def _rec_loop(cap: _Capture, detector: TextDetector,
              stop: threading.Event) -> None:
    """Recognition worker: reads the requested boxes' text with the LOCAL
    RapidOCR recognizer — free, no API. Crops come from the full-res frame
    so glyphs are sharp. Runs even while subtitles are toggled OFF (pre-warm)
    so Alt+T shows text instantly instead of a wall of pending markers.
    Several workers run in parallel — the claim below pops entries under the
    lock so no two workers read the same box."""
    while not stop.is_set():
        with _REC_LOCK:
            batch = list(_REC_REQ.items())[:_REC_BATCH]
            for uid, _r in batch:
                _REC_REQ.pop(uid, None)
        if not batch:
            time.sleep(0.05)
            continue
        try:
            frame = cap.last()
            H, W = frame.shape[:2]
            crops, uids, origins = [], [], []
            for uid, (x1, y1, x2, y2) in batch:
                x1p, y1p = max(0, x1 - 6), max(0, y1 - 4)
                x2p, y2p = min(W, x2 + 6), min(H, y2 + 4)
                if x2p - x1p < 8 or y2p - y1p < 6:
                    with _REC_LOCK:
                        _REC_OUT[uid] = ("", None, "")
                    continue
                crops.append(np.ascontiguousarray(frame[y1p:y2p, x1p:x2p]))
                uids.append(uid)
                origins.append((x1p, y1p))
            if crops:
                res = detector.recognize(crops)
                with _REC_LOCK:
                    for uid, crop, (ox, oy), (text, _score) in zip(
                            uids, crops, origins, res):
                        # EDGE REFINEMENT: detection is quantized to ~3px
                        # steps (1152px pass on a 4K screen). The full-res
                        # crop shows exactly where the glyphs start and end —
                        # snap the box edges to them. Bounded by the crop
                        # margin, so a busy background can only nudge edges
                        # a few px, never relocate the box.
                        rect = None
                        try:
                            g = crop[:, :, 1].astype(np.float32)
                            kc = np.flatnonzero(g.std(axis=0) > 6.0)
                            kr = np.flatnonzero(g.std(axis=1) > 6.0)
                            if kc.size and kr.size:
                                rect = (ox + int(kc[0]) - 1,
                                        oy + int(kr[0]) - 1,
                                        ox + int(kc[-1]) + 2,
                                        oy + int(kr[-1]) + 2)
                        except Exception:
                            rect = None
                        _REC_OUT[uid] = ((text or "").strip(), rect,
                                         _glyph_color(crop))
        except Exception as exc:
            logger.debug("[OCR] rec loop error: %s", exc)
            time.sleep(0.1)


def _detect_loop(cap: _Capture, target: _Target, anchors: _Anchors,
                 wake: threading.Event, stop: threading.Event,
                 detector: TextDetector) -> None:
    import cv2

    det_pass = [0]
    last_epoch = -1
    while not stop.is_set():
        t0 = time.monotonic()
        try:
            rect, fg, epoch = target.get()
            if epoch != last_epoch:
                last_epoch = epoch
                logger.info("[OCR] target region: %s (epoch %d)", rect, epoch)
            if rect is None or not fg or not _SCAN_ACTIVE[0]:
                if _SCAN_ACTIVE[0]:
                    # The user is HOLDING the scan key and nothing happens —
                    # say why, throttled (this is the "scan died" diagnosis).
                    lastw = getattr(_detect_loop, "_why_at", 0.0)
                    if time.monotonic() - lastw > 5.0:
                        _detect_loop._why_at = time.monotonic()
                        logger.info(
                            "[OCR] scan held but blocked: rect=%s fg=%s "
                            "occl=%s", rect, fg, _OCCL_DBG[0][:3])
                wake.wait(0.2)
                wake.clear()
                continue
            x1, y1, x2, y2 = rect
            frame = cap.last()[y1:y2, x1:x2]
            ch, cw = frame.shape[:2]
            longest = max(cw, ch)
            # Adaptive: floor 960, cap 1152 for 4K (~0.5s/pass; 1280 measured
            # ~1s — the settle sluggishness the user felt).
            target_side = min(1152, max(_DETECT_SIDE, int(longest * 0.30)))
            d_scale = min(1.0, target_side / float(longest))
            det_w, det_h = max(1, int(cw * d_scale)), max(1, int(ch * d_scale))
            det_bgr = cv2.resize(frame, (det_w, det_h), interpolation=cv2.INTER_AREA)
            # Blank regions covered by other windows so their text is never
            # even detected (they aren't VRChat content).
            for ox1, oy1, ox2, oy2 in target.occlusions():
                dx1 = max(0, int(ox1 * d_scale)); dy1 = max(0, int(oy1 * d_scale))
                dx2 = min(det_w, int(ox2 * d_scale)); dy2 = min(det_h, int(oy2 * d_scale))
                if dx2 > dx1 and dy2 > dy1:
                    det_bgr[dy1:dy2, dx1:dx2] = 0
            raw = detector.detect(det_bgr)
            shaped = [b for b in raw if _text_plausible(b, det_w, det_h)]
            if _BUBBLES_ONLY[0]:
                kept = [b for b in shaped
                        if _looks_like_bubble(det_bgr, b)]
                ndrop = len(shaped) - len(kept)
                if ndrop and time.monotonic() - getattr(
                        _detect_loop, "_bub_at", 0.0) > 5.0:
                    _detect_loop._bub_at = time.monotonic()
                    logger.info("[OCR] bubbles-only dropped %d box(es), "
                                "last reason: %s", ndrop, _BUBBLE_WHY[0])
                shaped = kept
            # RECOGNITION GATE: actually READ each candidate; only regions
            # producing legible characters at decent confidence earn a box.
            # Crops come from the FULL-RES frame (not the downscaled detection
            # image) so the gate judges sharp pixels — at whole-screen scope a
            # 4K frame squeezed to 960px made every crop unreadable and the
            # gate rejected nearly all real text.
            if _REC_GATE_ENABLED:
                inv_d = 1.0 / d_scale if d_scale else 1.0
                crops, keep = [], []
                for b in shaped[:_REC_MAX_BOXES]:
                    fx1 = max(0, int(b.x1 * inv_d) - 3)
                    fy1 = max(0, int(b.y1 * inv_d) - 3)
                    fx2 = min(cw, int(b.x2 * inv_d) + 3)
                    fy2 = min(ch, int(b.y2 * inv_d) + 3)
                    if fx2 - fx1 < 12 or fy2 - fy1 < 8:
                        continue
                    crops.append(np.ascontiguousarray(frame[fy1:fy2, fx1:fx2]))
                    keep.append(b)
                verified = [
                    b for b, (text, score) in zip(keep, detector.recognize(crops))
                    if score >= _REC_MIN_SCORE and _REC_TEXTY.search(text)
                ]
            else:
                verified = shaped  # permissive: every text-shaped region
            det_pass[0] += 1
            if det_pass[0] % 10 == 1:
                logger.info("[OCR] det: raw=%d shaped=%d verified=%d region=%dx%d "
                            "det_side=%d pass_ms=%.0f",
                            len(raw), len(shaped), len(verified), cw, ch,
                            max(det_w, det_h),
                            (time.monotonic() - t0) * 1000)
            t_scale = min(1.0, _TRACK_SIDE / float(max(cw, ch)))
            track_w, track_h = max(1, int(cw * t_scale)), max(1, int(ch * t_scale))
            sx = track_w / float(det_w)
            sy = track_h / float(det_h)
            boxes = [
                TextBox(round(b.x1 * sx), round(b.y1 * sy),
                        round(b.x2 * sx), round(b.y2 * sy))
                for b in verified
            ]
            gray = cv2.resize(cv2.extractChannel(frame, 1), (track_w, track_h),
                              interpolation=cv2.INTER_LINEAR)
            # The recognition gate adds ~0.3-0.6s inside this pass, so the
            # boxes describe an OLD frame; merging them dragged well-tracked
            # boxes off their text (the alignment regression). Re-advance the
            # boxes to the CURRENT frame before publishing so the tracker
            # always merges toward fresh positions.
            if boxes:
                now_crop = cap.last()[y1:y2, x1:x2]
                if now_crop.shape[:2] == (ch, cw):
                    now_gray = cv2.resize(
                        np.ascontiguousarray(now_crop[:, :, 1]),
                        (track_w, track_h), interpolation=cv2.INTER_LINEAR)
                    tmp = [_Tracked(b) for b in boxes]
                    adv, (agx, agy, agr) = _flow_all(
                        gray, now_gray, tmp, _make_grid(track_w, track_h),
                        win=25, levels=4)
                    fresh_boxes = []
                    for tb, (adx, ady, ar) in zip(tmp, adv):
                        if ar < _MIN_OK_RATIO:
                            continue
                        # Advance that disagrees with the global shift is
                        # line-aliased flow (similar-looking lines) — the box
                        # would land somewhere text never was. Drop it; the
                        # next pass re-detects it at the true position.
                        if (agr >= _MIN_OK_RATIO
                                and ((adx - agx) ** 2 + (ady - agy) ** 2) ** 0.5
                                > _FLOW_JUMP_PX):
                            continue
                        # Landing-spot check: the pixels where the box lands
                        # must still look like the pixels it was detected on
                        # (a stalled advance otherwise publishes a box onto
                        # the area its text already scrolled away from).
                        s0 = _grid_sample(gray, tb.x1, tb.y1, tb.x2, tb.y2)
                        s1 = _grid_sample(now_gray, tb.x1 + adx, tb.y1 + ady,
                                          tb.x2 + adx, tb.y2 + ady)
                        if float(np.mean(np.abs(s1 - s0))) > _SIG_DIFF:
                            continue
                        fresh_boxes.append(TextBox(
                            int(tb.x1 + adx), int(tb.y1 + ady),
                            int(tb.x2 + adx), int(tb.y2 + ady)))
                    boxes = fresh_boxes
                    gray = now_gray
            anchors.publish(boxes, gray, epoch)
        except Exception as exc:
            # WARNING, not debug: a broken detect loop looks like "boxes never
            # appear" — it must be visible in the log file.
            logger.warning("[OCR] detect error: %s", exc)
        remaining = max(0.0, _DETECT_INTERVAL - (time.monotonic() - t0))
        wake.wait(remaining)
        wake.clear()


_NEXT_UID = [0]


class _Tracked:
    __slots__ = ("x1", "y1", "x2", "y2", "ax", "ay", "vx", "vy",
                 "moving", "calm_frames", "miss", "sig", "sig_bad", "tex_bad",
                 "last_confirm", "confirms", "jump_dx", "jump_dy", "jump_n",
                 "uid", "text", "refined", "xlat", "namever", "namehit",
                 "rosterhit", "text_at", "pinyin", "color")

    def __init__(self, b: TextBox) -> None:
        self.x1, self.y1 = float(b.x1), float(b.y1)
        self.x2, self.y2 = float(b.x2), float(b.y2)
        self.ax, self.ay = self.x1, self.y1
        self.vx = self.vy = 0.0
        self.moving = True
        self.calm_frames = 0
        self.miss = 0
        self.sig: np.ndarray | None = None
        self.sig_bad = 0
        self.tex_bad = 0
        self.last_confirm = time.monotonic()
        self.confirms = 1  # detections that have vouched for this box
        self.jump_dx = self.jump_dy = 0.0  # pending unconfirmed motion jump
        self.jump_n = 0
        _NEXT_UID[0] += 1
        self.uid = _NEXT_UID[0]  # persistent identity keys recognition
        self.text = ""  # what the local recognizer read (subtitle mode)
        self.refined = False  # edges snapped to full-res glyph extents
        self.xlat = ""  # translated text (subtitle mode shows this)
        self.namever = -1  # roster version the namehit verdict was cached at
        self.namehit = False  # cached "this box is a player name" verdict
        self.rosterhit = False  # roster match regardless of ignore toggles
        self.text_at = 0.0  # when recognition first delivered the text
        self.pinyin = ""  # transliteration of Han text (display formats)
        self.color = ""  # dominant glyph color (#rrggbb; '' = unknown)

    def advance(self, dx: float, dy: float, dt: float,
                gx: float, gy: float) -> None:
        self.x1 += dx; self.x2 += dx
        self.y1 += dy; self.y2 += dy
        if dt > 0:
            a = 0.55  # responsive velocity for the extrapolating renderer
            self.vx = (1 - a) * self.vx + a * (dx / dt)
            self.vy = (1 - a) * self.vy + a * (dy / dt)

        if self.moving:
            self.ax, self.ay = self.x1, self.y1
            step = ((dx - gx) ** 2 + (dy - gy) ** 2) ** 0.5
            if step < _STILL_SPEED_PX:
                self.calm_frames += 1
                if self.calm_frames >= _STILL_FRAMES:
                    self.moving = False
                    self.vx = self.vy = 0.0
                    # Keep the CREATION-time fingerprint: a box that drifted
                    # off its text during motion must fail against the real
                    # text baseline and die, not adopt whatever it landed on.
            else:
                self.calm_frames = 0
        else:
            self.ax += gx
            self.ay += gy
            off = ((self.x1 - self.ax) ** 2 + (self.y1 - self.ay) ** 2) ** 0.5
            if off > _UNFREEZE_PX:
                self.moving = True
                self.calm_frames = 0
                self.ax, self.ay = self.x1, self.y1
                self.sig_bad = 0
            else:
                w = self.x2 - self.x1
                h = self.y2 - self.y1
                self.x1 = self.ax + (self.x1 - self.ax) * (1 - _STILL_DECAY)
                self.y1 = self.ay + (self.y1 - self.ay) * (1 - _STILL_DECAY)
                self.x2 = self.x1 + w
                self.y2 = self.y1 + h

    def blend_toward(self, b: "_Tracked", k: float = _MERGE_BLEND) -> None:
        self.x1 += (b.x1 - self.x1) * k
        self.y1 += (b.y1 - self.y1) * k
        self.x2 += (b.x2 - self.x2) * k
        self.y2 += (b.y2 - self.y2) * k
        if not self.moving:
            self.ax, self.ay = self.x1, self.y1
        self.sig = None  # re-fingerprint at the corrected position
        self.sig_bad = 0
        self.last_confirm = time.monotonic()
        self.confirms = min(10, self.confirms + 1)

    def content_changed(self, gray: np.ndarray) -> bool:
        """One-shot: do the pixels under the box still match the fingerprint
        from when detection last confirmed it? No hysteresis, no adaptation.
        Lets a trusted pass fast-kill boxes whose content is GONE while boxes
        on unchanged pixels keep the anti-blink miss tolerance."""
        if self.sig is None:
            return False
        try:
            return float(np.mean(np.abs(self._sample(gray) - self.sig))) > _SIG_DIFF
        except Exception:
            return False

    def confirm_only(self) -> None:
        """Detection vouches this box still exists, but its positions are
        motion-stale (scroll/pan during the pass): refresh liveness only,
        keep our own tracked position untouched."""
        self.sig_bad = 0
        self.last_confirm = time.monotonic()
        self.confirms = min(10, self.confirms + 1)

    def rect(self) -> tuple[float, float, float, float]:
        if not self.moving:
            w = self.x2 - self.x1
            h = self.y2 - self.y1
            return self.ax, self.ay, self.ax + w, self.ay + h
        return self.x1, self.y1, self.x2, self.y2

    def velocity(self) -> tuple[float, float]:
        if not self.moving:
            return 0.0, 0.0
        return self.vx, self.vy

    def _sample(self, gray: np.ndarray) -> np.ndarray:
        x1, y1, x2, y2 = self.rect()
        return _grid_sample(gray, x1, y1, x2, y2)

    def check_signature(self, gray: np.ndarray) -> bool:
        """Fingerprint the pixels under the box; returns False when the content
        has been gone for _SIG_BAD_FRAMES frames (drop the box)."""
        sample = self._sample(gray)
        if self.sig is None:
            self.sig = sample
            self.sig_bad = 0
            return True
        diff = float(np.mean(np.abs(sample - self.sig)))
        if diff > _SIG_DIFF:
            self.sig_bad += 1
            if self.sig_bad >= _SIG_BAD_FRAMES:
                return False
        else:
            self.sig_bad = 0
            self.sig = 0.9 * self.sig + 0.1 * sample
        return True

    def check_texture(self, gray: np.ndarray) -> bool:
        """Text has contrast. A box whose content is flat for a few frames is a
        ghost sitting on floor/wall — drop it (works even during motion)."""
        std = float(self._sample(gray).std())
        if std < _TEX_MIN_STD:
            self.tex_bad += 1
            if self.tex_bad >= _TEX_BAD_FRAMES:
                return False
        else:
            self.tex_bad = 0
        return True


def _grid_sample(gray: np.ndarray, x1: float, y1: float, x2: float, y2: float
                 ) -> np.ndarray:
    H, W = gray.shape[:2]
    xs = np.clip(np.linspace(x1 + 2, x2 - 2, _SIG_X).astype(np.int32), 0, W - 1)
    ys = np.clip(np.linspace(y1 + 1, y2 - 1, _SIG_Y).astype(np.int32), 0, H - 1)
    return gray[np.ix_(ys, xs)].astype(np.float32).ravel()


def _iou(a, b) -> float:
    ix1, iy1 = max(a[0], b[0]), max(a[1], b[1])
    ix2, iy2 = min(a[2], b[2]), min(a[3], b[3])
    iw, ih = max(0.0, ix2 - ix1), max(0.0, iy2 - iy1)
    inter = iw * ih
    if inter <= 0:
        return 0.0
    area_a = (a[2] - a[0]) * (a[3] - a[1])
    area_b = (b[2] - b[0]) * (b[3] - b[1])
    return inter / max(1.0, area_a + area_b - inter)


def _make_grid(w: int, h: int) -> np.ndarray:
    xs = np.linspace(w * 0.08, w * 0.92, _GRID_X)
    ys = np.linspace(h * 0.10, h * 0.90, _GRID_Y)
    return np.array([[x, y] for y in ys for x in xs], dtype=np.float32)


def _flow_all(prev_g: np.ndarray, cur_g: np.ndarray, tracked: list["_Tracked"],
              grid: np.ndarray, win: int = 21, levels: int = 3,
              cursor: tuple[float, float] | None = None):
    import cv2

    H, W = cur_g.shape[:2]
    pts: list[list[float]] = [p.tolist() for p in grid]
    n_grid = len(pts)
    spans = []
    r2 = _CURSOR_RADIUS * _CURSOR_RADIUS
    for tr in tracked:
        gx = np.linspace(max(1.0, tr.x1 + 2), min(W - 2.0, tr.x2 - 2), _PTS_X)
        gy = np.linspace(max(1.0, tr.y1 + 2), min(H - 2.0, tr.y2 - 2), _PTS_Y)
        p = [[x, y] for y in gy for x in gx]
        if cursor is not None:
            cx, cy = cursor
            filtered = [q for q in p
                        if (q[0] - cx) ** 2 + (q[1] - cy) ** 2 > r2]
            if len(filtered) >= 4:
                p = filtered  # drop cursor-contaminated points
        spans.append((len(pts), len(pts) + len(p)))
        pts.extend(p)
    p0 = np.asarray(pts, dtype=np.float32).reshape(-1, 1, 2)
    try:
        p1, st, _ = cv2.calcOpticalFlowPyrLK(
            prev_g, cur_g, p0, None, winSize=(win, win), maxLevel=levels)
    except Exception:
        return [(0.0, 0.0, 0.0)] * len(tracked), (0.0, 0.0, 0.0)
    d = (p1 - p0).reshape(-1, 2)
    ok = st.reshape(-1) == 1

    g_ok = ok[:n_grid]
    if g_ok.sum() >= 4:
        gxy = np.median(d[:n_grid][g_ok], axis=0)
        g = (float(gxy[0]), float(gxy[1]), float(g_ok.sum()) / n_grid)
    else:
        g = (0.0, 0.0, float(g_ok.sum()) / max(1, n_grid))

    out: list[tuple[float, float, float]] = []
    for a, z in spans:
        good = ok[a:z]
        n = max(1, z - a)
        if good.sum() >= 2:
            dx, dy = np.median(d[a:z][good], axis=0)
            out.append((float(dx), float(dy), float(good.sum()) / n))
        else:
            out.append((0.0, 0.0, float(good.sum()) / n))
    return out, g


class _BoxState:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._items: list[tuple[float, float, float, float, float, float]] = []
        self._stamp = time.monotonic()
        self.version = 0

    def set(self, items) -> None:
        with self._lock:
            self._items = items
            self._stamp = time.monotonic()
            self.version += 1

    def get(self):
        with self._lock:
            return self.version, self._stamp, list(self._items)


def _track_loop(cap: _Capture, target: _Target, anchors: _Anchors,
                wake: threading.Event, state: _BoxState,
                stop: threading.Event) -> None:
    import cv2

    prev_g: np.ndarray | None = None
    tracked: list[_Tracked] = []
    text_cache: dict[tuple[int, int], tuple[str, float, float, float]] = {}
    last_stamp = 0
    last_t = time.monotonic()
    gx_ema = gy_ema = 0.0
    cur_epoch = -1
    hb_t = time.monotonic()
    hb_frames = 0
    away_since: float | None = None
    away_logged = False
    track_w = track_h = 0
    inv_scale = 1.0
    off_x = off_y = 0
    grid = _make_grid(8, 5)
    while not stop.is_set():
        try:
            # Heartbeat: with a windowless subprocess this log line is the only
            # way to see whether tracking is alive and at what rate.
            now_hb = time.monotonic()
            if now_hb - hb_t >= 5.0:
                logger.info("[OCR] heartbeat: %.0f fps, boxes=%d, fg=%s",
                            hb_frames / max(1e-6, now_hb - hb_t), len(tracked),
                            target.get()[1])
                hb_t, hb_frames = now_hb, 0
            rect, fg, epoch = target.get()
            _TGT_FG[0] = bool(fg) and rect is not None
            if rect is None or not _SCAN_ACTIVE[0]:
                if tracked or prev_g is not None:
                    tracked = []
                    prev_g = None
                    state.set([])
                time.sleep(0.05)
                continue
            # Focus-loss debounce: ignore sub-_FG_GRACE_S blips entirely (the
            # app's own overlay re-asserting topmost, OS transitions). Only a
            # sustained focus change hides the boxes; returning re-detects
            # immediately instead of waiting out the cycle.
            if not fg:
                if away_since is None:
                    away_since = time.monotonic()
                if time.monotonic() - away_since >= _FG_GRACE_S:
                    if not away_logged:
                        away_logged = True
                        logger.info("[OCR] hidden: focus lost >%.1fs to %r",
                                    _FG_GRACE_S, target.fg_title())
                    if tracked or prev_g is not None:
                        tracked = []
                        prev_g = None
                        state.set([])
                    time.sleep(0.05)
                    continue
                # inside grace: keep tracking straight through the blip
            else:
                if away_logged:
                    logger.info("[OCR] resumed: focus back on target")
                    # Discard any detection made before/during the hide — it
                    # describes the OLD view and briefly painted misplaced
                    # boxes on tab-back. A fresh scan is requested instead.
                    stale_fresh = anchors.take(last_stamp)
                    if stale_fresh is not None:
                        last_stamp = stale_fresh[0]
                    wake.set()  # repopulate without waiting for the next cycle
                away_since = None
                away_logged = False
            if epoch != cur_epoch:
                cur_epoch = epoch
                x1, y1, x2, y2 = rect
                cw, ch = x2 - x1, y2 - y1
                t_scale = min(1.0, _TRACK_SIDE / float(max(cw, ch)))
                track_w, track_h = max(1, int(cw * t_scale)), max(1, int(ch * t_scale))
                inv_scale = 1.0 / t_scale if t_scale else 1.0
                off_x, off_y = x1, y1
                grid = _make_grid(track_w, track_h)
                prev_g = None
                tracked = []
                state.set([])

            frame = cap.grab()
            if frame is None:
                time.sleep(0.001)
                continue
            hb_frames += 1
            now = time.monotonic()
            dt = min(0.1, max(1e-4, now - last_t))
            last_t = now
            x1, y1, x2, y2 = rect
            # Copy ONLY the green plane of the crop (8 MB at 4K, not the 24 MB
            # BGR copy — that hidden copy was costing real frame rate).
            crop_g = frame[y1:y2, x1:x2, 1]
            if crop_g.size == 0:
                time.sleep(0.01)
                continue
            cur_g = cv2.resize(np.ascontiguousarray(crop_g),
                               (track_w, track_h), interpolation=cv2.INTER_LINEAR)
            # Blank pixels covered by OTHER windows (chat apps, explorer, the
            # translator): they are not VRChat content and must not be scanned,
            # tracked, or boxed. Masked-out boxes die via the checks below.
            occl = target.occlusions()
            t_s = 1.0 / inv_scale if inv_scale else 1.0
            occl_wk = []
            for ox1, oy1, ox2, oy2 in occl:
                wx1 = max(0, int(ox1 * t_s)); wy1 = max(0, int(oy1 * t_s))
                wx2 = min(track_w, int(ox2 * t_s)); wy2 = min(track_h, int(oy2 * t_s))
                if wx2 > wx1 and wy2 > wy1:
                    cur_g[wy1:wy2, wx1:wx2] = 0
                    occl_wk.append((wx1, wy1, wx2, wy2))
            if prev_g is None or prev_g.shape != cur_g.shape:
                prev_g = cur_g
                continue

            # In-game cursor position in track coords (game draws its own
            # pointer into the frame; OS position matches in desktop mode).
            cursor_wk: tuple[float, float] | None = None
            try:
                cpt = wintypes.POINT()
                ctypes.windll.user32.GetCursorPos(ctypes.byref(cpt))
                scale = 1.0 / inv_scale if inv_scale else 1.0
                cursor_wk = ((cpt.x - cap.left - off_x) * scale,
                             (cpt.y - cap.top - off_y) * scale)
            except Exception:
                pass

            flows, (gx, gy, grid_ratio) = _flow_all(prev_g, cur_g, tracked, grid,
                                                    cursor=cursor_wk)
            gx_ema = 0.7 * gx_ema + 0.3 * gx
            gy_ema = 0.7 * gy_ema + 0.3 * gy
            camera_moving = (gx_ema ** 2 + gy_ema ** 2) ** 0.5 > _GLOBAL_MOVE_PX
            agx, agy = (gx, gy) if camera_moving else (0.0, 0.0)

            if tracked:
                diff = float(cv2.mean(cv2.absdiff(cur_g, prev_g))[0])
                if diff > _CUT_DIFF_HARD or (diff > _CUT_DIFF_SOFT
                                             and grid_ratio < _CUT_GRID_RATIO):
                    tracked = []
                    text_cache.clear()  # view changed — texts are stale
                    state.set([])
                    wake.set()
                    prev_g = cur_g
                    continue

            kept: list[_Tracked] = []
            for tr, (dx, dy, ratio) in zip(tracked, flows):
                # Reference for "plausible motion" is the box's OWN momentum,
                # NOT the whole-screen median: the median becomes the pane's
                # scroll when a large window scrolls, and overriding static
                # boxes with it dragged sidebar/header boxes onto empty space.
                pvx, pvy = tr.vx * dt, tr.vy * dt
                if ratio < _MIN_OK_RATIO:
                    if not (camera_moving and grid_ratio >= _MIN_OK_RATIO):
                        continue
                    # Points lost mid-motion: coast one frame on momentum —
                    # detection settles the box's fate at the next pass.
                    dx, dy = pvx, pvy
                elif ((dx - pvx) ** 2 + (dy - pvy) ** 2) ** 0.5 > _FLOW_JUMP_PX:
                    # Sudden large motion: line-aliasing OR a genuine scroll
                    # start — indistinguishable in a single frame. Hold the
                    # box on its momentum for ONE frame; if the next frame
                    # repeats the same jump it is real motion (aliasing does
                    # not repeat consistently) — accept it and repay the held
                    # frame so the box catches up exactly.
                    jdx, jdy = dx - pvx, dy - pvy
                    if (tr.jump_n > 0
                            and ((jdx - tr.jump_dx) ** 2
                                 + (jdy - tr.jump_dy) ** 2) ** 0.5
                            < _FLOW_JUMP_PX):
                        dx += tr.jump_dx
                        dy += tr.jump_dy
                        tr.jump_n = 0
                    else:
                        tr.jump_dx, tr.jump_dy = jdx, jdy
                        tr.jump_n = 1
                        dx, dy = pvx, pvy
                else:
                    tr.jump_n = 0
                tr.advance(dx, dy, dt, agx, agy)
                # Content checks are suspended while the cursor touches the
                # box — the game-drawn pointer legitimately changes the pixels.
                cursor_on_box = False
                if cursor_wk is not None:
                    cx, cy = cursor_wk
                    m = _CURSOR_RADIUS
                    cursor_on_box = (tr.x1 - m <= cx <= tr.x2 + m
                                     and tr.y1 - m <= cy <= tr.y2 + m)
                # Texture check ALWAYS: a box on featureless content (floor,
                # wall) is a ghost regardless of motion — flow reports OK on
                # flat surfaces, so this is the only thing that catches them.
                if _is_cursor_box(tr.x1, tr.y1, tr.x2, tr.y2, cursor_wk):
                    continue  # tiny box riding the pointer — it IS the cursor
                bcx, bcy = (tr.x1 + tr.x2) / 2, (tr.y1 + tr.y2) / 2
                if any(ox1 <= bcx <= ox2 and oy1 <= bcy <= oy2
                       for ox1, oy1, ox2, oy2 in occl_wk):
                    continue  # box sits on another window's area — drop
                # FLAT-KILL: a solid-color patch cannot be text (text needs
                # contrast). When scrolled content leaves a box behind, flow
                # stalls at "no motion" on the bare background and the box
                # would sit on grey until the next trusted pass — this removes
                # it DURING the scroll instead (~3 frames).
                if not cursor_on_box:
                    fx1, fy1 = int(max(0.0, tr.x1)), int(max(0.0, tr.y1))
                    fx2 = int(min(float(track_w), tr.x2))
                    fy2 = int(min(float(track_h), tr.y2))
                    if fx2 - fx1 >= 4 and fy2 - fy1 >= 2:
                        patch = cur_g[fy1:fy2, fx1:fx2]
                        kc = np.flatnonzero(patch.std(axis=0) > _FLAT_KILL_STD)
                        kr = np.flatnonzero(patch.std(axis=1) > _FLAT_KILL_STD)
                        if kc.size == 0 or kr.size == 0:
                            tr.tex_bad += 1
                            if tr.tex_bad >= _FLAT_BAD_FRAMES:
                                continue
                        else:
                            tr.tex_bad = 0
                            # LIVE TRIM: a ghost that PARTIALLY overlaps text
                            # (wide box, text sliver at one edge) beats the
                            # flat-kill. Shrink it every frame to the extent
                            # that has contrast, so it hugs whatever text it
                            # actually covers instead of stretching across
                            # empty background. Only substantial shrinks are
                            # applied — settled boxes never jitter.
                            w, h = fx2 - fx1, fy2 - fy1
                            nx1 = fx1 + max(0, int(kc[0]) - 2)
                            nx2 = fx1 + min(w, int(kc[-1]) + 3)
                            ny1 = fy1 + max(0, int(kr[0]) - 1)
                            ny2 = fy1 + min(h, int(kr[-1]) + 2)
                            if ((nx2 - nx1) < 0.85 * w
                                    or (ny2 - ny1) < 0.8 * h):
                                tr.x1, tr.y1 = float(nx1), float(ny1)
                                tr.x2, tr.y2 = float(nx2), float(ny2)
                                if not tr.moving:
                                    tr.ax, tr.ay = tr.x1, tr.y1
                                tr.sig = None  # content basis changed
                if (_TEXTURE_KILL_ENABLED and not cursor_on_box
                        and not tr.check_texture(cur_g)):
                    continue
                # Appearance check ONLY for frozen boxes under a still camera
                # (the menu-close case), and only when detection hasn't
                # re-confirmed the box recently — a repeatedly-confirmed box is
                # real text; sig-killing it caused an on/off flash loop.
                if (_SIG_KILL_ENABLED and not cursor_on_box
                        and not camera_moving and not tr.moving
                        and (now - tr.last_confirm) > _SIG_CONFIRM_GRACE_S
                        and not tr.check_signature(cur_g)):
                    continue
                kept.append(tr)
            tracked = kept

            fresh = anchors.take(last_stamp)
            if fresh is not None:
                last_stamp, det_boxes, det_gray, det_epoch = fresh
                usable = (det_epoch == cur_epoch and det_gray is not None
                          and det_gray.shape == cur_g.shape)
                if usable:
                    stale = float(cv2.mean(cv2.absdiff(cur_g, det_gray))[0])
                    usable = stale < _DET_STALE
                    if not usable:
                        # This pass is unusable (view moved on) — request a
                        # fresh one NOW so ghosts don't outlive the movement.
                        wake.set()
                if usable:
                    fresh_tracked: list[_Tracked] = []
                    vouch_only = False
                    if det_boxes:
                        cands = [_Tracked(b) for b in det_boxes]
                        adv, (mgx, mgy, mgr) = _flow_all(det_gray, cur_g,
                                                         cands, grid,
                                                         win=25, levels=4)
                        # Screen unchanged since the pass => the pass is exact:
                        # trust it fully. Otherwise, a big content shift
                        # (scroll/pan mid-pass) or an unreliable bridge flow
                        # means positions can't be trusted — vouch only.
                        det_shift = (mgx * mgx + mgy * mgy) ** 0.5
                        vouch_only = (stale >= _DET_TRUST_DIFF
                                      and (det_shift > _DET_VOUCH_SHIFT
                                           or mgr < _MIN_OK_RATIO))
                        for tr, (dx, dy, ratio) in zip(cands, adv):
                            if ratio < _MIN_OK_RATIO:
                                continue
                            if (mgr >= _MIN_OK_RATIO
                                    and ((dx - mgx) ** 2 + (dy - mgy) ** 2)
                                    ** 0.5 > _FLOW_JUMP_PX):
                                continue  # line-aliased advance — wrong spot
                            dox1, doy1 = tr.x1, tr.y1  # det-time position
                            dox2, doy2 = tr.x2, tr.y2
                            tr.advance(dx, dy, dt, agx, agy)
                            # LOCAL staleness: the pixels at the box's landing
                            # spot must still look like the pixels it was
                            # detected on. A mid-scroll box whose flow stalled
                            # lands on the VACATED (bare) area and can't match
                            # the text it came from — drop it here instead of
                            # painting a ghost on the background.
                            s_det = _grid_sample(det_gray, dox1, doy1,
                                                 dox2, doy2)
                            s_now = _grid_sample(cur_g, tr.x1, tr.y1,
                                                 tr.x2, tr.y2)
                            if float(np.mean(np.abs(s_now - s_det))) > _SIG_DIFF:
                                continue
                            if _is_cursor_box(tr.x1, tr.y1, tr.x2, tr.y2,
                                              cursor_wk):
                                continue  # never adopt the pointer glyph
                            ncx = (tr.x1 + tr.x2) / 2
                            ncy = (tr.y1 + tr.y2) / 2
                            if any(ox1 <= ncx <= ox2 and oy1 <= ncy <= oy2
                                   for ox1, oy1, ox2, oy2 in occl_wk):
                                continue  # on another window's area
                            # Baseline fingerprint NOW, while the content
                            # under the box is verified text.
                            tr.check_signature(cur_g)
                            fresh_tracked.append(tr)
                    merged: list[_Tracked] = []
                    used = [False] * len(fresh_tracked)
                    n_prior = len(tracked)
                    n_matched = 0
                    for tr in tracked:
                        best, best_iou = -1, _MERGE_IOU
                        for i, nb in enumerate(fresh_tracked):
                            if used[i]:
                                continue
                            iou = _iou(tr.rect(), nb.rect())
                            if iou > best_iou:
                                best, best_iou = i, iou
                        if best >= 0:
                            used[best] = True
                            if vouch_only:
                                # Motion-stale pass: liveness only — never
                                # drag a well-tracked box toward stale coords.
                                tr.confirm_only()
                            elif tr.refined and _iou(
                                    tr.rect(),
                                    fresh_tracked[best].rect()) > 0.55:
                                # Edges were snapped to full-res glyph
                                # extents; a re-detection within quantization
                                # slop must not drag them back to its coarse
                                # 1152px grid.
                                tr.confirm_only()
                            else:
                                # Still camera => detections are exact: snap
                                # EXACTLY (k=1) so one pass fully aligns.
                                # Softer during motion (some staleness).
                                tr.blend_toward(fresh_tracked[best],
                                                0.55 if camera_moving else 1.0)
                                # blend cleared the fingerprint — retake it at
                                # the corrected position NOW so the fast-kill
                                # below has a fresh baseline to judge against.
                                tr.check_signature(cur_g)
                            tr.miss = 0
                            n_matched += 1
                            merged.append(tr)
                        elif vouch_only:
                            # The pass was compromised — a no-match proves
                            # nothing. No miss penalty.
                            merged.append(tr)
                        else:
                            # FAST-KILL (settle speed): a trusted pass didn't
                            # corroborate this box AND the pixels under it no
                            # longer match its last-confirmed fingerprint —
                            # the text is really gone; don't ride out the miss
                            # tolerance. Unchanged pixels = detector flake on
                            # borderline text: keep the anti-blink tolerance.
                            cursor_on = False
                            if cursor_wk is not None:
                                ccx, ccy = cursor_wk
                                m = _CURSOR_RADIUS
                                cursor_on = (tr.x1 - m <= ccx <= tr.x2 + m
                                             and tr.y1 - m <= ccy <= tr.y2 + m)
                            if (stale < _DET_TRUST_DIFF and not cursor_on
                                    and tr.content_changed(cur_g)):
                                continue
                            tr.miss += 1
                            allowed = (_MAX_MISSES_CONFIRMED
                                       if tr.confirms >= _CONFIRMED_AT
                                       else _MAX_MISSES_UNPROVEN)
                            if tr.miss <= allowed:
                                merged.append(tr)
                    if not vouch_only:
                        # New boxes only from trustworthy passes — motion-stale
                        # ones put boxes where text never existed ("snapping").
                        for i, nb in enumerate(fresh_tracked):
                            if not used[i]:
                                merged.append(nb)
                    # Coherence: a still-camera detection that corroborates
                    # almost none of the current boxes means the VIEW CHANGED
                    # (app switch) — keep only what it confirmed + the fresh
                    # set, instead of letting stale boxes ride out misses
                    # ("existing boxes fling all over" after tab-back).
                    if (not vouch_only and not camera_moving and n_prior >= 4
                            and n_matched / n_prior < _COHERENCE_MIN):
                        merged = ([tr for tr in merged if tr.miss == 0])
                        text_cache.clear()  # view changed — texts are stale
                    tracked = merged
                    if vouch_only:
                        wake.set()  # ask for a cleaner pass immediately

            prev_g = cur_g
            # Recognition bookkeeping runs ALWAYS (pre-warm): text is read in
            # the background while subtitles are off, so Alt+T shows results
            # instantly. Claim finished reads, request text for stable boxes
            # that have none, drop orphaned entries.
            with _REC_LOCK:
                live = set()
                pending = len(_REC_REQ)
                for tr in tracked:
                    live.add(tr.uid)
                    # Center-keyed (rebirth extents wobble a few px per pass,
                    # which made exact-corner keys miss — the "text keeps
                    # regenerating" complaint; centers are far more stable).
                    ckey = (int(tr.x1 + tr.x2) // 16,
                            int(tr.y1 + tr.y2) // 16)
                    if not tr.text:
                        hit = text_cache.get(ckey)
                        if (hit is not None
                                and now - hit[1] < _TEXT_CACHE_TTL
                                and abs((tr.x2 - tr.x1) - hit[2])
                                < 0.3 * hit[2] + 8):
                            tr.text = hit[0]  # reborn box inherits its text
                            tr.text_at = 0.0  # shown before — no first-hold
                            tr.pinyin = _pinyin_of(tr.text)
                            if len(hit) > 4 and hit[4]:
                                tr.color = hit[4]
                    if tr.uid in _REC_OUT:
                        text, rrect, tcol = _REC_OUT.pop(tr.uid)
                        tr.text = text or "-"
                        tr.text_at = now
                        tr.pinyin = _pinyin_of(tr.text)
                        if tcol:
                            tr.color = tcol
                        # Apply the full-res edge refinement when the box is
                        # settled and the correction is within detection's
                        # quantization slop (a large delta means the box
                        # moved since the crop — stale, don't apply).
                        if rrect is not None and not tr.moving:
                            nx1 = (rrect[0] - off_x) / inv_scale
                            ny1 = (rrect[1] - off_y) / inv_scale
                            nx2 = (rrect[2] - off_x) / inv_scale
                            ny2 = (rrect[3] - off_y) / inv_scale
                            if (abs(nx1 - tr.x1) <= 5 and abs(ny1 - tr.y1) <= 4
                                    and abs(nx2 - tr.x2) <= 5
                                    and abs(ny2 - tr.y2) <= 4):
                                tr.x1, tr.y1, tr.x2, tr.y2 = nx1, ny1, nx2, ny2
                                tr.ax, tr.ay = nx1, ny1
                                tr.refined = True
                    if tr.text and tr.text != "-":
                        text_cache[ckey] = (tr.text, now, tr.x2 - tr.x1,
                                            tr.y2 - tr.y1, tr.color)
                        # Translation: once per unique string per session.
                        if not tr.xlat and _XLAT_ENABLED[0]:
                            norm = tr.text.strip()
                            if (_is_own_language(norm, _XLAT_TARGET[0])
                                    or _is_ignored_name(norm)
                                    or _is_translate_icon(
                                        norm, tr.x2 - tr.x1,
                                        tr.y2 - tr.y1)):
                                tr.xlat = norm  # readable/ignored — no call
                            else:
                                with _XLAT_LOCK:
                                    hit = _XLAT_CACHE.get(norm)
                                    if hit is not None:
                                        tr.xlat = hit
                                    elif (norm
                                          and norm not in _XLAT_QUEUED):
                                        _XLAT_QUEUED.add(norm)
                                        _XLAT_PENDING.append(norm)
                    elif ((_PREWARM[0] or _SCAN_ACTIVE[0] or _FOREIGN_ONLY[0]
                           or _ignore_active()) and not tr.text
                          and tr.confirms >= 1
                          and tr.uid not in _REC_REQ and pending < 48):
                        # Content filters NEED text to classify — they imply
                        # recognition even with pre-warm off (otherwise
                        # foreign-only + prewarm-off = every box invisible
                        # forever, waiting for a read that never comes).
                        bx1, by1, bx2, by2 = tr.rect()
                        _REC_REQ[tr.uid] = (
                            int(bx1 * inv_scale + off_x),
                            int(by1 * inv_scale + off_y),
                            int(bx2 * inv_scale + off_x),
                            int(by2 * inv_scale + off_y))
                        pending += 1
                if len(text_cache) > 500:
                    cutoff = now - _TEXT_CACHE_TTL
                    for k in [k for k, v in text_cache.items()
                              if v[1] < cutoff]:
                        del text_cache[k]
                for k in list(_REC_OUT):
                    if k not in live:
                        del _REC_OUT[k]
                for k in list(_REC_REQ):
                    if k not in live:
                        del _REC_REQ[k]
            # Pass 1 — suppressed-name rects. Anything sitting directly
            # BENEATH a suppressed name is nameplate chrome (status line,
            # pronouns, bio) and is suppressed with it, whatever it says —
            # content matching can't catch "INTP /<name>&…" bios, geometry can.
            name_rects: list[tuple[float, float, float, float]] = []
            if _ignore_active():
                for tr in tracked:
                    if tr.text and tr.text != "-":
                        if tr.namever != _NAMES_VER[0]:
                            _t1 = tr.text.strip()
                            tr.namehit = _is_ignored_name(_t1)
                            # Roster anchors work even when the name itself
                            # is displayed (pronoun filter on, names off) —
                            # the status/pronoun pill under it still drops.
                            tr.rosterhit = tr.namehit or _is_roster_name(_t1)
                            tr.namever = _NAMES_VER[0]
                        if tr.rosterhit:
                            name_rects.append(tr.rect())

            def _under_name(bx1: float, by1: float, bx2: float,
                            by2: float) -> bool:
                # Real status/pronoun pills hug their name (VRChat places
                # them under the plate, often shifted right), are no taller
                # than the name line and no wider than the plate. The size
                # guards keep chat text that merely passes below someone's
                # nameplate in a crowd alive — a generous window here read
                # as "OCR stopped scanning things".
                bh = by2 - by1
                bw = bx2 - bx1
                for ax1, ay1, ax2, ay2 in name_rects:
                    ah = max(1.0, ay2 - ay1)
                    aw = max(1.0, ax2 - ax1)
                    if not (-0.3 * ah <= by1 - ay2 <= 0.9 * ah):
                        continue
                    if bh > 1.05 * ah or bw > 1.5 * aw:
                        continue  # as big as the name line: not chrome
                    ov = min(bx2, ax2) - max(bx1, ax1)
                    if ov <= 0:
                        continue
                    coff = abs((bx1 + bx2) - (ax1 + ax2)) / 2.0
                    if coff <= 0.6 * max(aw, bw):
                        return True
                return False

            items = []
            for tr in tracked:
                # Hide (still tracked, never re-recognized): boxes whose text
                # is already in the user's language (foreign-only mode), and
                # boxes that are purely a player name or pronoun set.
                if tr.text and tr.text != "-":
                    _t = tr.text.strip()
                    if _is_translate_icon(_t, tr.x2 - tr.x1, tr.y2 - tr.y1):
                        continue  # UI chrome, never a message
                    if (_FOREIGN_ONLY[0]
                            and _is_own_language(_t, _XLAT_TARGET[0])):
                        continue
                    if _ignore_active() and tr.namehit:
                        continue  # verdict cached in pass 1
                    if (_IGNORE_NAMES[0] and len(_t) <= 24
                            and now - tr.text_at < 0.8):
                        # SHORT texts hold one roster beat before first
                        # display: a just-joined player's name can be read
                        # before their OnPlayerJoined line is parsed — give
                        # the roster the chance to veto.
                        continue
                elif not tr.text and (_FOREIGN_ONLY[0] or _ignore_active()):
                    # Content filters active + text not yet read: draw NOTHING
                    # until recognition classifies the box. Drawing first and
                    # erasing later flashed a red outline on every nameplate
                    # for the ~1s classification gap.
                    continue
                bx1, by1, bx2, by2 = tr.rect()
                _txt = (tr.text or "").strip()
                if (name_rects
                        and len(_txt) <= 20
                        and sum("一" <= c <= "鿿" for c in _txt) < 8
                        and _under_name(bx1, by1, bx2, by2)):
                    # Nameplate chrome under a name. Long recognized text is
                    # immune — status/pronoun pills are always short.
                    continue
                vx, vy = tr.velocity()
                items.append((
                    (bx1 * inv_scale + off_x) + cap.left,
                    (by1 * inv_scale + off_y) + cap.top,
                    (bx2 * inv_scale + off_x) + cap.left,
                    (by2 * inv_scale + off_y) + cap.top,
                    vx * inv_scale, vy * inv_scale, tr.uid,
                    tr.text, tr.xlat, tr.pinyin, tr.color))
            state.set(items)
        except Exception as exc:
            logger.debug("[OCR] track iteration error: %s", exc)
            time.sleep(0.01)


def _hex_rgb(h: str) -> tuple[int, int, int]:
    try:
        return (int(h[1:3], 16), int(h[3:5], 16), int(h[5:7], 16))
    except Exception:
        return (255, 255, 255)


def _save_debug_shot(cap: _Capture, boxes, pills: bool = False) -> None:
    import cv2

    try:
        os.makedirs(_SHOT_DIR, exist_ok=True)
        frame = cap.last().copy()
        texted = []
        oc = _hex_rgb(_C_OUTLINE[0])
        oc_bgr = (oc[2], oc[1], oc[0])
        for it in boxes:
            bx1, by1, bx2, by2 = it[0], it[1], it[2], it[3]
            x1, y1 = int(bx1 - cap.left), int(by1 - cap.top)
            x2, y2 = int(bx2 - cap.left), int(by2 - cap.top)
            if pills and len(it) > 9:
                lines = _fmt_lines(it[7], it[8], it[9]) or [("...", "trans")]
                texted.append((x1, y1, x2, y2, lines[:3],
                               it[10] if len(it) > 10 else ""))
            else:
                cv2.rectangle(frame, (x1, y1), (x2, y2), oc_bgr, 2)
        if texted:
            # PIL pass, mirroring the LIVE renderer exactly — same pill
            # rect math, colors, opacity and pinyin tint, so what lands in
            # the shots folder is what the user saw on screen. (cv2's
            # built-in font is ASCII-only; a TrueType draws every script.)
            from PIL import Image, ImageDraw

            img = Image.fromarray(frame[:, :, ::-1]).convert("RGBA")
            ovl = Image.new("RGBA", img.size, (0, 0, 0, 0))
            od = ImageDraw.Draw(ovl)
            bg = _hex_rgb(_C_BG[0])
            pyc = _hex_rgb(_C_PY)
            alpha = int(255 * _BG_ALPHA[0] / 100)
            for x1, y1, x2, y2, lines, bcol in texted:
                base_c = (bcol or "#ffffff") \
                    if _C_TEXT[0] == "auto" else _C_TEXT[0]
                od.rectangle([x1, y1, x2, y2], outline=oc + (255,), width=1)
                n = len(lines)
                fix = _FONT_FIX[0]
                if fix > 0:
                    px = fix
                elif fix == -1:
                    px = max(10, min(64, int((y2 - y1) * 0.82)))
                elif _PLACE[0] == "cover":
                    px = max(9, min(46, int((y2 - y1) / n * 0.72)))
                else:
                    px = max(10, min(30, int((y2 - y1) * 0.55)))
                txt0 = lines[0][0] if lines else ""
                is_pron = _is_pronoun_text(txt0)
                pron = _SZ_PRO[0] if (_SZ_PRO[0] > 0 and is_pron) else 0
                pron_c = _C_PRO[0] if is_pron else ""
                pxs = [_line_px(k, px, pron) for _ln, k in lines]
                cols = [_hex_rgb(_line_color(k, base_c, bcol, pron_c))
                        for _ln, k in lines]
                fonts = [_pil_font(p) for p in pxs]
                try:
                    lws = [od.textlength(ln, font=fonts[j])
                           for j, (ln, _k) in enumerate(lines)]
                except Exception:
                    lws = [x2 - x1] * n
                widest = max(lws)
                grow = fix != 0 or any(p != px for p in pxs)
                if grow:
                    block = sum(p + 5 for p in pxs) + 4
                    bcx = (x1 + x2) / 2.0
                    if widest + 6 > x2 - x1:
                        x1 = bcx - (widest + 6) / 2.0
                        x2 = bcx + (widest + 6) / 2.0
                    if _PLACE[0] == "cover":
                        bcy = (y1 + y2) / 2.0
                        half = max(block, y2 - y1 + 2) / 2.0
                        ry1, ry2 = bcy - half, bcy + half
                    else:
                        ry2 = y1 - 3
                        ry1 = ry2 - block
                    line_x = [bcx - w / 2.0 for w in lws]
                    yc = ry1 + ((ry2 - ry1) - block) / 2.0 + 2
                    centers = []
                    for p in pxs:
                        centers.append(yc + (p + 5) / 2.0)
                        yc += p + 5
                else:
                    if _PLACE[0] == "cover":
                        ry1, ry2 = y1 - 1, y2 + 1
                    else:
                        block = n * (px + 5) + 4
                        ry2 = y1 - 3
                        ry1 = ry2 - block
                    bw = x2 - x1
                    if widest > bw - 4 and bw > 24 and widest > 0:
                        px = max(8, int(px * (bw - 4) / widest))
                        fonts = [_pil_font(px)] * n
                    line_x = [x1 + 2] * n
                    row_h = (ry2 - ry1) / n
                    centers = [ry1 + row_h * (j + 0.5) for j in range(n)]
                if _BG_ALPHA[0] > 0:
                    od.rectangle([x1 - 1, ry1, x2 + 1, ry2],
                                 fill=bg + (alpha,), outline=oc + (255,),
                                 width=1)
                for j, (ln, kind) in enumerate(lines):
                    cy = centers[j]
                    if _BG_ALPHA[0] == 0:
                        od.text((line_x[j] + 1, cy + 1), ln, font=fonts[j],
                                fill=(0, 0, 0, 255), anchor="lm")
                    od.text((line_x[j], cy), ln, font=fonts[j],
                            fill=cols[j] + (255,), anchor="lm")
            img = Image.alpha_composite(img, ovl).convert("RGB")
            frame = np.asarray(img)[:, :, ::-1].copy()
        path = os.path.join(_SHOT_DIR, time.strftime("shot_%H%M%S.png"))
        cv2.imwrite(path, frame)
        logger.info("[OCR] debug shot saved: %s (pills=%s)", path, pills)
    except Exception as exc:
        logger.debug("[OCR] debug shot failed: %s", exc)


_SHOT_TRIGGER = os.path.join(os.path.expanduser("~"), "AppData", "Local",
                             "puripuly-heart", "ocr_shot_trigger")


def _prtscn_loop(cap: _Capture, state: _BoxState, stop: threading.Event) -> None:
    """Composite triggers: the PrintScreen key (user's muscle memory) OR a
    trigger FILE — the file path lets tooling request a composite silently,
    without touching the keyboard (the user's ShareX intercepts PrtScn with a
    focus-stealing capture UI)."""
    was_down = False
    was_combo = False
    was_scan = False
    user32 = ctypes.windll.user32
    while not stop.is_set():
        try:
            down = bool(user32.GetAsyncKeyState(_VK_SNAPSHOT) & 0x8000)
            fire = down and not was_down
            was_down = down
            # Scan activation: hold = active while the recorded combo is
            # down; toggle = press flips; no bind = always active. Gates
            # ALL scanning and drawing (subtitles follow the scan — there
            # is no separate subtitle key anymore).
            # ONLY while the target window is focused: the bind is a bare
            # letter for most users, and typing in Discord/a browser was
            # silently flipping the toggle ("PrintScreen killed OCR" was
            # really the e's in the chat message they typed after it).
            def _combo_down(cbv) -> bool:
                mods, keyvk = cbv
                return (all(user32.GetAsyncKeyState(m) & 0x8000
                            for m in mods)
                        and bool(user32.GetAsyncKeyState(keyvk) & 0x8000))

            hold_cb, tog_cb = _HOLD_COMBO[0], _TOG_COMBO[0]
            prev_active = _SCAN_ACTIVE[0]
            # Binds count when the TARGET GAME is focused OR our own app
            # is (so the toggle is testable from the menu with the Status
            # row visible). Presses in Discord/browsers stay ignored —
            # typing there was silently flipping the toggle.
            fgw2 = user32.GetForegroundWindow()
            fpid = wintypes.DWORD(0)
            user32.GetWindowThreadProcessId(fgw2, ctypes.byref(fpid))
            bind_ok = _TGT_FG[0] or (fpid.value and
                                     fpid.value in _app_pids())
            if hold_cb is None and tog_cb is None:
                _SCAN_ACTIVE[0] = True
                was_scan = False
            elif not bind_ok:
                # Presses in other apps are ignored; the toggle state
                # persists across alt-tabs, the hold contribution drops.
                was_scan = False
                _SCAN_ACTIVE[0] = _TOG_STATE[0] if tog_cb is not None \
                    else False
            else:
                tog_down = _combo_down(tog_cb) if tog_cb else False
                hold_down = _combo_down(hold_cb) if hold_cb else False
                if tog_down:
                    # Toggle takes precedence: ALT+E must not also read
                    # as the E hold-bind being down.
                    hold_down = False
                if tog_cb is not None:
                    if tog_down and not was_scan:
                        _TOG_STATE[0] = not _TOG_STATE[0]
                        logger.info("[OCR] scan toggle %s",
                                    "ON" if _TOG_STATE[0] else "OFF")
                        _FLASH_TXT[0] = ("OCR ON" if _TOG_STATE[0]
                                         else "OCR OFF")
                        _FLASH[0] = time.monotonic() + 1.6
                    was_scan = tog_down
                _SCAN_ACTIVE[0] = _TOG_STATE[0] or hold_down
            noww = time.monotonic()
            if (_SCAN_ACTIVE[0] != prev_active
                    or noww - getattr(_prtscn_loop, "_state_at", 0.0) > 2.0):
                _prtscn_loop._state_at = noww
                _write_state()
            if not fire and os.path.exists(_SHOT_TRIGGER):
                with contextlib_suppress():
                    os.remove(_SHOT_TRIGGER)
                fire = True
            if fire:
                _v, _s, boxes = state.get()
                _save_debug_shot(cap, boxes, pills=_SCAN_ACTIVE[0])
        except Exception:
            pass
        time.sleep(0.03)


class contextlib_suppress:
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return True


def run(monitor_index: int = 1, fps: float = 0.0, max_side: int = _TRACK_SIDE,
        window_title: str | None = "VRChat") -> None:
    _set_dpi_aware()
    cap = _Capture()
    left, top, width, height = cap.left, cap.top, cap.width, cap.height

    target = _Target(window_title, cap)
    target.poll()

    root = tk.Tk()
    root.overrideredirect(True)
    root.attributes("-topmost", True)
    root.update_idletasks()
    tk_w, tk_h = root.winfo_screenwidth(), root.winfo_screenheight()
    sx = tk_w / float(width) if width else 1.0
    sy = tk_h / float(height) if height else 1.0
    win_w, win_h = int(round(width * sx)), int(round(height * sy))
    win_x, win_y = int(round(left * sx)), int(round(top * sy))
    root.geometry(f"{win_w}x{win_h}+{win_x}+{win_y}")
    root.configure(bg=_TRANSPARENT_KEY)
    try:
        root.attributes("-transparentcolor", _TRANSPARENT_KEY)
    except Exception:
        pass
    canvas = tk.Canvas(root, bg=_TRANSPARENT_KEY, highlightthickness=0,
                       width=win_w, height=win_h)
    canvas.pack(fill="both", expand=True)
    root.update_idletasks()
    _make_click_through(root)
    _exclude_from_capture(root)

    # ── Region selection (drag a rectangle; OCR locks to it) ──
    sel = {"active": False, "start": None}

    def _set_click_through(enable: bool) -> None:
        GWL_EXSTYLE = -20
        WS_EX_TRANSPARENT = 0x00000020
        try:
            hwnd = ctypes.windll.user32.GetParent(root.winfo_id())
            st = ctypes.windll.user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
            st = (st | WS_EX_TRANSPARENT) if enable else (st & ~WS_EX_TRANSPARENT)
            ctypes.windll.user32.SetWindowLongW(hwnd, GWL_EXSTYLE, st)
        except Exception:
            pass

    def _begin_select() -> None:
        if sel["active"]:
            return
        sel["active"] = True
        sel["start"] = None
        _set_click_through(False)
        # A dimmed SOLID background: transparent-key pixels never receive
        # clicks, so the veil is what makes the drag land on us.
        canvas.configure(bg="#161616")
        root.attributes("-alpha", 0.35)
        # Screen-center crosshair: a reference point for aiming the region
        # (e.g. around the game's own crosshair).
        cxp, cyp = win_w / 2, win_h / 2
        canvas.create_line(cxp - 20, cyp, cxp + 20, cyp,
                           fill=_BOX_COLOR, width=2, tags="selaid")
        canvas.create_line(cxp, cyp - 20, cxp, cyp + 20,
                           fill=_BOX_COLOR, width=2, tags="selaid")
        canvas.create_oval(cxp - 4, cyp - 4, cxp + 4, cyp + 4,
                           fill="#ffffff", outline=_BOX_COLOR,
                           tags="selaid")
        logger.info("[OCR] region selection active — drag to set, Esc cancels")
        with contextlib_suppress():
            root.focus_force()

    def _end_select(region) -> None:
        sel["active"] = False
        sel["start"] = None
        canvas.delete("selrect")
        canvas.delete("selaid")
        canvas.configure(bg=_TRANSPARENT_KEY)
        root.attributes("-alpha", 1.0)
        _set_click_through(True)
        if region is not None:
            target.set_region(region)
        else:
            logger.info("[OCR] region selection cancelled")

    def _sel_press(ev) -> None:
        if sel["active"]:
            sel["start"] = (ev.x, ev.y)

    def _sel_drag(ev) -> None:
        if not sel["active"] or sel["start"] is None:
            return
        canvas.delete("selrect")
        x0, y0 = sel["start"]
        canvas.create_rectangle(x0, y0, ev.x, ev.y, outline=_BOX_COLOR,
                                width=2, tags="selrect")

    def _sel_release(ev) -> None:
        if not sel["active"] or sel["start"] is None:
            return
        x0, y0 = sel["start"]
        rx1 = int(min(x0, ev.x) / sx)
        ry1 = int(min(y0, ev.y) / sy)
        rx2 = int(max(x0, ev.x) / sx)
        ry2 = int(max(y0, ev.y) / sy)
        # A tiny drag is a mis-click, not a region — cancel.
        _end_select((rx1, ry1, rx2, ry2)
                    if rx2 - rx1 >= 64 and ry2 - ry1 >= 64 else None)

    canvas.bind("<ButtonPress-1>", _sel_press)
    canvas.bind("<B1-Motion>", _sel_drag)
    canvas.bind("<ButtonRelease-1>", _sel_release)
    root.bind("<Escape>",
              lambda _e: _end_select(None) if sel["active"] else None)

    state = _BoxState()
    anchors = _Anchors()
    wake = threading.Event()
    stop = threading.Event()
    # max_side only raises the engine's internal cap; the working resolution
    # is chosen per detection pass. Shared with the rec worker (subtitles).
    detector = TextDetector(max_side=1664)
    threading.Thread(target=_target_loop, args=(target, stop), daemon=True).start()
    threading.Thread(target=_detect_loop,
                     args=(cap, target, anchors, wake, stop, detector),
                     daemon=True).start()
    for _w in range(_REC_WORKERS):
        threading.Thread(target=_rec_loop, args=(cap, detector, stop),
                         daemon=True).start()
    threading.Thread(target=_track_loop,
                     args=(cap, target, anchors, wake, state, stop),
                     daemon=True).start()
    threading.Thread(target=_prtscn_loop, args=(cap, state, stop),
                     daemon=True).start()
    threading.Thread(target=_warm_pinyin, daemon=True).start()
    threading.Thread(target=_wait_event_loop,
                     args=(_SELECT_REGION_EVENT,
                           lambda: _SELECT_REQ.__setitem__(0, True)),
                     daemon=True).start()
    threading.Thread(target=_wait_event_loop,
                     args=(_CLEAR_REGION_EVENT,
                           lambda: target.set_region_enabled(False)),
                     daemon=True).start()
    threading.Thread(target=_wait_event_loop,
                     args=(_ENABLE_REGION_EVENT,
                           lambda: target.set_region_enabled(True)),
                     daemon=True).start()

    def _reload_prefs() -> None:
        cfg = _load_config()
        _apply_prefs(cfg)
        if "window_title" in cfg:
            target.set_title(str(cfg.get("window_title") or "") or None)

    threading.Thread(target=_wait_event_loop,
                     args=(_RELOAD_PREFS_EVENT, _reload_prefs),
                     daemon=True).start()
    for _w in range(_XLAT_WORKERS):
        threading.Thread(target=_xlat_loop, args=(stop,),
                         daemon=True).start()
    threading.Thread(target=_players_loop, args=(stop,),
                     daemon=True).start()

    # Hold-to-translate bind: overridable without a rebuild via the config
    # file (a real settings-page bind picker comes with the app-side stage).
    try:
        import json as _json

        with open(_CONFIG_PATH, encoding="utf-8") as _fh:
            _k = str(_json.load(_fh).get("translate_key",
                                         _DEFAULT_TRANSLATE_KEY))
        _k = (_k.strip().upper() or _DEFAULT_TRANSLATE_KEY)[0]
        if _k.isalnum():
            _TRANSLATE_VK[0] = ord(_k)
    except Exception:
        pass
    logger.info("[OCR] hold-to-translate placeholder bind: %s",
                chr(_TRANSLATE_VK[0]))
    import tkinter.font as tkfont

    _font_cache: dict[int, tkfont.Font] = {}

    def _font_px(px: int) -> tkfont.Font:
        f = _font_cache.get(px)
        if f is None:
            f = tkfont.Font(family="Segoe UI", size=-px)  # negative = pixels
            _font_cache[px] = f
        return f

    pool: list[int] = []
    pill_pool: list[tuple[int, int]] = []
    pill_meta: list[tuple[str, int]] = []
    region_border = canvas.create_rectangle(
        0, 0, 0, 0, outline="#909090", width=1, dash=(5, 5), state="hidden")

    def _redraw() -> None:
        try:
            if _SELECT_REQ[0]:
                _SELECT_REQ[0] = False
                _begin_select()
            rgn = target.region()
            if rgn is not None and not sel["active"] and _REGION_BORDER[0]:
                canvas.coords(region_border, rgn[0] * sx, rgn[1] * sy,
                              rgn[2] * sx, rgn[3] * sy)
                canvas.itemconfigure(region_border, state="normal")
            else:
                canvas.itemconfigure(region_border, state="hidden")
            # Scan-state flash: brief "OCR ON/OFF" pill top-center so a
            # toggle is visible in-game (state changes used to be silent —
            # a flipped-off toggle read as "OCR died").
            fl = getattr(_redraw, "_flash", None)
            if fl is None:
                fl = (canvas.create_rectangle(
                          0, 0, 0, 0, fill=_PILL_BG, outline="#3a3b3f",
                          width=1, state="hidden"),
                      canvas.create_text(
                          0, 0, text="", fill="#ffffff", anchor="c",
                          state="hidden", font=_font_px(26)))
                _redraw._flash = fl
            if time.monotonic() < _FLASH[0]:
                fcx = (canvas.winfo_width() or 800) / 2.0
                canvas.coords(fl[0], fcx - 120, 36, fcx + 120, 92)
                canvas.coords(fl[1], fcx, 64)
                canvas.itemconfigure(fl[1], text=_FLASH_TXT[0],
                                     state="normal")
                canvas.itemconfigure(fl[0], state="normal")
                canvas.tag_raise(fl[0])
                canvas.tag_raise(fl[1])
            else:
                canvas.itemconfigure(fl[0], state="hidden")
                canvas.itemconfigure(fl[1], state="hidden")
            _version, stamp, items = state.get()
            age = time.monotonic() - stamp
            ext = min(age, _RENDER_EXTRAP_CAP_S) + _RENDER_LEAD_S
            held = True  # subtitles follow the scan gate; items only exist
            # while scanning is active, so visible box = show its format.
            while len(pool) < len(items):
                pool.append(canvas.create_rectangle(
                    0, 0, 0, 0, outline=_BOX_COLOR, width=_BOX_WIDTH,
                    state="hidden"))
            while len(pill_pool) < len(items):
                r = canvas.create_rectangle(
                    0, 0, 0, 0, fill=_PILL_BG, outline=_BOX_COLOR,
                    width=_BOX_WIDTH, state="hidden")
                shs = [canvas.create_text(0, 0, text="", fill="#000000",
                                          anchor="w", state="hidden")
                       for _ in range(3)]
                txs = [canvas.create_text(0, 0, text="", fill=_PILL_TEXT,
                                          anchor="w", state="hidden")
                       for _ in range(3)]
                pill_pool.append((r, shs, txs))
                pill_meta.append(("", 0))
            # Live style: recolor pooled items when the config changed.
            style_now = (_C_OUTLINE[0], _C_BG[0], _C_TEXT[0], _BG_ALPHA[0])
            if getattr(_redraw, "_style", None) != style_now:
                _redraw._style = style_now
                stip = {100: "", 75: "gray75", 50: "gray50",
                        25: "gray25"}.get(_BG_ALPHA[0], "")
                for bx in pool:
                    canvas.itemconfigure(bx, outline=_C_OUTLINE[0])
                for r, shs, txs in pill_pool:
                    canvas.itemconfigure(
                        r, outline=_C_OUTLINE[0],
                        fill="" if _BG_ALPHA[0] == 0 else _C_BG[0],
                        stipple=stip)
                # Text fills are per-line (pinyin keeps its own color) —
                # invalidate the metas so the next pass re-applies them.
                for i in range(len(pill_meta)):
                    pill_meta[i] = ("", 0)
            for i, item in enumerate(pool):
                if i < len(items):
                    bx1, by1, bx2, by2, vx, vy = items[i][:6]
                    ex, ey = vx * ext, vy * ext
                    canvas.coords(item,
                                  (bx1 + ex - left) * sx, (by1 + ey - top) * sy,
                                  (bx2 + ex - left) * sx, (by2 + ey - top) * sy)
                    canvas.itemconfigure(item, state="normal")
                else:
                    canvas.itemconfigure(item, state="hidden")
            for i, (rid, shs, txs) in enumerate(pill_pool):
                shown = False
                if held and i < len(items):
                    (bx1, by1, bx2, by2, vx, vy, uid, text, xlat, py,
                     tcol) = items[i]
                    base = (tcol or "#ffffff") if _C_TEXT[0] == "auto" \
                        else _C_TEXT[0]
                    lines = _fmt_lines(text, xlat, py) or [("...", "trans")]
                    lines = lines[:3]
                    ex, ey = vx * ext, vy * ext
                    x1 = (bx1 + ex - left) * sx
                    y1 = (by1 + ey - top) * sy
                    x2 = (bx2 + ex - left) * sx
                    y2 = (by2 + ey - top) * sy
                    n = len(lines)
                    # Main size: fixed px, match-original (-1: derived from
                    # THIS box's text height, so a 50px nameplate renders at
                    # ~50px and a smaller pronoun field stays smaller), or
                    # auto (fit the box).
                    fix = _FONT_FIX[0]
                    if fix > 0:
                        px_h = fix
                    elif fix == -1:
                        px_h = max(10, min(64, int((y2 - y1) * 0.82)))
                    elif _PLACE[0] == "cover":
                        px_h = max(9, min(46, int((y2 - y1) / n * 0.72)))
                    else:
                        px_h = max(10, min(30, int((y2 - y1) * 0.55)))
                    is_pron = bool(text) and _is_pronoun_text(text)
                    pron = _SZ_PRO[0] if (_SZ_PRO[0] > 0 and is_pron) else 0
                    pron_c = _C_PRO[0] if is_pron else ""
                    pxs = [_line_px(k, px_h, pron) for _ln, k in lines]
                    cols = [_line_color(k, base, tcol, pron_c)
                            for _ln, k in lines]
                    fonts = [_font_px(p) for p in pxs]
                    lws = [fonts[j].measure(ln)
                           for j, (ln, _k) in enumerate(lines)]
                    widest = max(lws)
                    grow = fix != 0 or any(p != px_h for p in pxs)
                    if grow:
                        # Sized text never shrinks: the pill grows AROUND
                        # the box center and each line is centered in it.
                        block = sum(p + 5 for p in pxs) + 4
                        bcx = (x1 + x2) / 2.0
                        if widest + 6 > x2 - x1:
                            x1 = bcx - (widest + 6) / 2.0
                            x2 = bcx + (widest + 6) / 2.0
                        if _PLACE[0] == "cover":
                            bcy = (y1 + y2) / 2.0
                            half = max(block, y2 - y1 + 2) / 2.0
                            ry1, ry2 = bcy - half, bcy + half
                        else:
                            ry2 = y1 - 3
                            ry1 = ry2 - block
                        line_x = [bcx - w / 2.0 for w in lws]
                        yc = ry1 + ((ry2 - ry1) - block) / 2.0 + 2
                        centers = []
                        for p in pxs:
                            centers.append(yc + (p + 5) / 2.0)
                            yc += p + 5
                    else:
                        # Legacy auto: uniform font, shrink to fit width.
                        if _PLACE[0] == "cover":
                            ry1, ry2 = y1 - 1, y2 + 1
                        else:
                            block = n * (px_h + 5) + 4
                            ry2 = y1 - 3
                            ry1 = ry2 - block
                        bw = x2 - x1
                        if widest > bw - 4 and bw > 24:
                            px_h = max(8, int(px_h * (bw - 4)
                                              / max(1, widest)))
                            pxs = [px_h] * n
                            fonts = [_font_px(px_h)] * n
                        line_x = None
                        row_h = (ry2 - ry1) / n
                        centers = [ry1 + row_h * (j + 0.5) for j in range(n)]
                    key = ("|".join(("»" if k == "py" else "") + ln
                                    for ln, k in lines), tuple(pxs),
                           tuple(cols))
                    if pill_meta[i] != key:
                        pill_meta[i] = key
                        for j in range(3):
                            ln, kind = lines[j] if j < n else ("", "trans")
                            fj = fonts[j] if j < n else fonts[0]
                            canvas.itemconfigure(
                                txs[j], text=ln, font=fj,
                                fill=cols[j] if j < n else base)
                            canvas.itemconfigure(shs[j], text=ln, font=fj)
                    if _BG_ALPHA[0] > 0:
                        canvas.coords(rid, x1 - 1, ry1, x2 + 1, ry2)
                        canvas.itemconfigure(rid, state="normal")
                    else:
                        canvas.itemconfigure(rid, state="hidden")
                    for j in range(3):
                        if j < n:
                            cy = centers[j]
                            lx = line_x[j] if line_x else x1 + 2
                            if _BG_ALPHA[0] == 0:
                                # No backdrop: black shadow keeps the text
                                # readable over any scene.
                                canvas.coords(shs[j], lx + 1, cy + 1)
                                canvas.itemconfigure(shs[j], state="normal")
                            else:
                                canvas.itemconfigure(shs[j], state="hidden")
                            canvas.coords(txs[j], lx, cy)
                            canvas.itemconfigure(txs[j], state="normal")
                        else:
                            canvas.itemconfigure(txs[j], state="hidden")
                            canvas.itemconfigure(shs[j], state="hidden")
                    shown = True
                if not shown:
                    canvas.itemconfigure(rid, state="hidden")
                    for j in range(3):
                        canvas.itemconfigure(txs[j], state="hidden")
                        canvas.itemconfigure(shs[j], state="hidden")
        except Exception as exc:
            logger.debug("[OCR] redraw error: %s", exc)
        finally:
            root.after(4, _redraw)

    def _on_close() -> None:
        stop.set()
        root.destroy()

    root.protocol("WM_DELETE_WINDOW", _on_close)
    root.after(4, _redraw)
    try:
        root.mainloop()
    finally:
        stop.set()


def _wait_event_loop(name: str, cb) -> None:
    """Fire cb() every time the named auto-reset event is signaled."""
    INFINITE = 0xFFFFFFFF
    try:
        kernel32 = ctypes.windll.kernel32
        evt = kernel32.CreateEventW(None, False, False, name)
        while True:
            kernel32.WaitForSingleObject(evt, INFINITE)
            try:
                cb()
            except Exception:
                pass
    except Exception:
        pass


def _parent_watch_loop(parent_pid: int) -> None:
    """Exit the INSTANT the parent app dies. Holds a HANDLE to the exact
    process object (immune to Windows PID reuse — polling by pid could latch
    onto an unrelated process that recycled the number) and blocks on it, so
    there is no polling window either. If the parent can't be opened at all,
    it is already gone — exit immediately."""
    SYNCHRONIZE = 0x00100000
    INFINITE = 0xFFFFFFFF
    kernel32 = ctypes.windll.kernel32
    try:
        handle = kernel32.OpenProcess(SYNCHRONIZE, False, parent_pid)
        if handle:
            kernel32.WaitForSingleObject(handle, INFINITE)  # returns on death
    except Exception:
        pass
    logger.info("[OCR] parent app (pid %s) exited — shutting down", parent_pid)
    os._exit(0)


_TAKEOVER_EVENT = "PuriPulyHeart_OCR_Takeover"
_INSTANCE_MUTEX = "PuriPulyHeart_OCR_Overlay"
_SHUTDOWN_EVENT = "PuriPulyHeart_OCR_Shutdown"


def _shutdown_listener() -> None:
    """Exit when the app signals OCR-off. The manager's process handle can't
    reach a hot-swapped replacement (toggle-off killed nothing after a swap);
    a named event reaches EVERY overlay generation."""
    INFINITE = 0xFFFFFFFF
    try:
        kernel32 = ctypes.windll.kernel32
        evt = kernel32.CreateEventW(None, True, False, _SHUTDOWN_EVENT)
        kernel32.WaitForSingleObject(evt, INFINITE)
        logger.info("[OCR] shutdown signaled by the app — exiting")
    except Exception:
        return
    os._exit(0)


def _takeover_listener(evt: int) -> None:
    """Exit when a NEWER overlay signals takeover — the fresh code wins."""
    INFINITE = 0xFFFFFFFF
    try:
        ctypes.windll.kernel32.WaitForSingleObject(evt, INFINITE)
        logger.info("[OCR] takeover signaled by a newer instance — exiting")
    except Exception:
        pass
    os._exit(0)


def _source_watch_loop() -> None:
    """Hot-swap on source change: when this file or detector.py is modified
    (a fix landed), spawn a replacement of ourselves with the same arguments
    and let its takeover handshake retire us. Removes the re-toggle burden
    that repeatedly left stale code running while new fixes sat unused."""
    import subprocess
    import sys as _sys

    watch = [__file__.rstrip("co"),
             os.path.join(os.path.dirname(__file__), "detector.py")]
    last = {}
    for p in watch:
        try:
            last[p] = os.path.getmtime(p)
        except Exception:
            last[p] = 0.0
    while True:
        time.sleep(2.0)
        changed = False
        for p in watch:
            try:
                m = os.path.getmtime(p)
            except Exception:
                continue
            if m != last[p]:
                last[p] = m
                changed = True
        if changed:
            time.sleep(1.5)  # let the write settle
            logger.info("[OCR] source changed — hot-swapping to new code")
            try:
                subprocess.Popen([_sys.executable, "-m",
                                  "puripuly_heart.ocr.overlay_proc",
                                  *_sys.argv[1:]],
                                 creationflags=0x08000000)
            except Exception as exc:
                logger.warning("[OCR] hot-swap spawn failed: %s", exc)
            # The new instance's takeover event will terminate us.


def _acquire_single_instance() -> bool:
    """One OCR overlay at a time (two DXGI capture sessions randomly kill each
    other) — but NEVER let a stale instance block a new one: the new overlay
    signals takeover, the old one exits, then the mutex is acquired. Without
    this, an orphan holding the mutex silently rejected every re-toggle and
    kept ancient code drawing on screen."""
    ERROR_ALREADY_EXISTS = 183
    try:
        kernel32 = ctypes.windll.kernel32
        # Manual-reset event: any old instance's listener wakes and exits.
        evt = kernel32.CreateEventW(None, True, False, _TAKEOVER_EVENT)
        kernel32.SetEvent(evt)
        time.sleep(0.7)  # give the old instance time to leave
        kernel32.ResetEvent(evt)
        for _ in range(10):
            kernel32.CreateMutexW(None, False, _INSTANCE_MUTEX)
            if kernel32.GetLastError() != ERROR_ALREADY_EXISTS:
                threading.Thread(target=_takeover_listener, args=(evt,),
                                 daemon=True).start()
                return True
            time.sleep(0.3)
        logger.warning("[OCR] could not take over from the running instance")
        return False
    except Exception:
        return True


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--monitor", type=int, default=1)
    ap.add_argument("--fps", type=float, default=0.0)
    ap.add_argument("--max-side", type=int, default=_TRACK_SIDE)
    ap.add_argument("--window", type=str, default="",
                    help="restrict to this window title (e.g. VRChat); empty = whole screen")
    ap.add_argument("--parent-pid", type=int, default=0,
                    help="exit when this process dies (no orphan overlays)")
    ap.add_argument("--prewarm", type=int, default=1,
                    help="1 = recognize in background while subtitles are off"
                         " (instant Alt+T, bursts of CPU); 0 = recognize only"
                         " while subtitle mode is on")
    ap.add_argument("--bubbles-only", type=int, default=0,
                    help="1 = only box text that looks like a VRChat chat"
                         " bubble/nameplate (uniform pill + contrast)")
    ap.add_argument("--foreign-only", type=int, default=1,
                    help="1 = hide boxes whose recognized text is already in"
                         " the user's language")
    ap.add_argument("--ignore-names", type=int, default=1,
                    help="1 = hide boxes that are purely a player name"
                         " (VRChat log roster)")
    ap.add_argument("--ignore-pronouns", type=int, default=1,
                    help="1 = hide boxes that are pronoun sets or truncated"
                         " bio fields")
    ap.add_argument("--translate", type=int, default=1,
                    help="0 = subtitle mode shows raw recognized text only"
                         " (no translation calls; debugging aid)")
    args = ap.parse_args()
    _XLAT_ENABLED[0] = bool(args.translate)
    _PREWARM[0] = bool(args.prewarm)
    _BUBBLES_ONLY[0] = bool(args.bubbles_only)
    _FOREIGN_ONLY[0] = bool(args.foreign_only)
    _IGNORE_NAMES[0] = bool(args.ignore_names)
    _IGNORE_PRONOUNS[0] = bool(args.ignore_pronouns)
    # Config file is the persisted truth (live toggles write it) — apply it
    # over the CLI defaults at startup too, including the target window.
    _startup_cfg = _load_config()
    _apply_prefs(_startup_cfg)
    if "window_title" in _startup_cfg:
        args.window = str(_startup_cfg.get("window_title") or "")
    _load_translation_prefs()
    # Log to a file: the subprocess runs windowless, so stderr goes nowhere.
    log_path = os.path.join(os.path.expanduser("~"), "AppData", "Local",
                            "puripuly-heart", "ocr_overlay.log")
    handlers = [logging.StreamHandler()]
    try:
        os.makedirs(os.path.dirname(log_path), exist_ok=True)
        # APPEND, not truncate: takeover spawns run concurrently for a
        # moment and mode="w" let the newcomer wipe the active log.
        try:
            if os.path.getsize(log_path) > 5 * 1024 * 1024:
                os.remove(log_path)
        except OSError:
            pass
        handlers.append(logging.FileHandler(log_path, mode="a",
                                            encoding="utf-8"))
    except Exception:
        pass
    logging.basicConfig(level=logging.INFO, handlers=handlers,
                        format="%(asctime)s %(levelname)s %(message)s")
    logger.info("[OCR] ===== overlay boot pid=%d =====", os.getpid())
    if not _acquire_single_instance():
        logger.warning("[OCR] another OCR overlay is already running — exiting")
        return
    if args.parent_pid:
        _PARENT_PID[0] = int(args.parent_pid)
        threading.Thread(target=_parent_watch_loop, args=(args.parent_pid,),
                         daemon=True).start()
    threading.Thread(target=_source_watch_loop, daemon=True).start()
    threading.Thread(target=_shutdown_listener, daemon=True).start()
    logger.info("[OCR] starting: window=%r parent=%s prewarm=%d bubbles=%d "
                "foreign=%d names=%d pronouns=%d translate=%d",
                args.window or None, args.parent_pid or "none",
                _PREWARM[0], _BUBBLES_ONLY[0], _FOREIGN_ONLY[0],
                _IGNORE_NAMES[0], _IGNORE_PRONOUNS[0], _XLAT_ENABLED[0])
    run(monitor_index=args.monitor, fps=args.fps, max_side=args.max_side,
        window_title=args.window or None)


if __name__ == "__main__":
    main()
