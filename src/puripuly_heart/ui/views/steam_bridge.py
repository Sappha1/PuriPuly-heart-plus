# -*- coding: utf-8 -*-
"""Steam chat, embedded inside PuriPuly (beta, local only). NEVER ships.

Native Flet rendering of Steam's in-memory web-chat model (driven by a headless
helper): searchable friends list grouped Recent / Favorites / categories /
In-Game / Online / Offline with status colors, game names and VR/mobile/snooze
badges; chat tabs; a pane that coalesces each person's lines into flowing text,
shows the message immediately and swaps in the translation as it arrives; right-
click a friend for options; compact in-tab language picker.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import re
import socket
import time
import unicodedata
from pathlib import Path

import flet as ft


def _search_key(name: str) -> str:
    """name (lowercased) + its romanization, so 'xiaoming' matches 小明 (xiǎo míng)."""
    base = (name or "").lower()
    roman = ""
    with contextlib.suppress(Exception):
        from puripuly_heart.core.transliteration import to_pinyin
        py = to_pinyin(name)
        if py and py != name:
            nfd = unicodedata.normalize("NFD", py)
            roman = "".join(c for c in nfd if not unicodedata.combining(c)).replace(" ", "").lower()
    return f"{base} {roman}".strip()

_BG_MAIN = "#2e2f32"
_BG_SIDE = "#26272a"
_BG_INPUT = "#323336"
_BG_SEL = "#3a4a55"
_BG_MENU = "#2a2b2e"
_BORDER_INPUT = "#5b5c5f"
_DIVIDER = "#4b4c4f"
_TEXT_PRIMARY = "#f2f2f2"
_TEXT_FAINT = "#7f8084"
_SUB = "#8fa9c4"
_ACCENT = "#48a495"
_TOGGLE_ON = "#48a495"
_LINK = "#6dc0e8"
_C_INGAME = "#a1cd5e"
_C_ONLINE = "#6dc0e8"
_C_OFFLINE = "#7a7c80"
_C_AWAY = "#54748a"      # Steam's faded away-blue (name + icons all dim together)
_SECTION = "#8a8c90"

_FLAG_MOBILE = 0x200
_FLAG_VR = 0x800
_STEAMID64_BASE = 76561197960265728
_URL_RE = re.compile(r"(https?://[^\s]+)")
# CJK ideographs, Japanese kana, Korean hangul — used to decide if a message is
# in a different script than the reader (so we don't translate English->English).
_CJK_RE = re.compile(r"[㐀-鿿぀-ヿ가-힣]")
_CJK_LANGS = {"zh-CN", "zh-TW", "ja", "ko"}
from puripuly_heart.ui.i18n import t as _T


def _send_fmt_labels() -> dict:
    return {
        "orig_trans": _T("steam.fmt_orig_trans", default="Original + Translation"),
        "orig_read_trans": _T("steam.fmt_orig_read_trans", default="Original + Pinyin + Translation"),
        "read_trans": _T("steam.fmt_read_trans", default="Pinyin + Translation"),
        "read_only": _T("steam.fmt_read_only", default="Pinyin Only"),
        "trans_only": _T("steam.fmt_trans_only", default="Translation Only"),
    }


import logging

_vlog = logging.getLogger("puripuly_heart.steam_view")

_EMOTE_RE = re.compile(r"[:ː]([a-zA-Z][a-zA-Z0-9_]{1,})[:ː]")


# Steam room effects are slash commands the server renders as full-chat
# effects on both ends.
_ROOM_EFFECTS = [("balloons", "🎈"), ("confetti", "🎉"),
                 ("fireworks", "🎆"), ("goldfetti", "🎊")]
# (instance attr self._effects overrides with the store's real names)


def _norm_named(raw) -> tuple[list, dict]:
    """Helper lists arrive as ["name"] (old) or [{"name","app"}] (r446) —
    normalize to (names, {name: game})."""
    names, meta = [], {}
    for it in raw or []:
        if isinstance(it, dict):
            n, app = it.get("name"), it.get("app") or ""
        else:
            n, app = str(it), ""
        if n and n not in meta:
            names.append(n)
            meta[n] = app
    return names, meta


def _disp_name(f: dict) -> str:
    """Steam behavior: the nickname (when set) is the shown name."""
    return f.get("nick") or f.get("name") or ""


def _est_text_w(s: str, size: float = 13.0) -> int:
    """Rough pixel width: CJK ~1em, latin ~0.55em — good enough to decide
    whether a row's name will ellipsize (tooltips only then)."""
    return int(sum(size if ord(ch) > 0x2E80 else size * 0.55 for ch in (s or "")))


def _extract_emoticons(text: str) -> tuple[str, list[str]]:
    """Pull Steam emoticon tokens (:name:) out of the text so they can render as
    images; returns (text_without_tokens, [names])."""
    names = _EMOTE_RE.findall(text or "")
    if not names:
        return text or "", []
    return _EMOTE_RE.sub("", text or "").strip(), names

_LANGS = [
    ("en", "English"), ("zh-CN", "中文(简)"), ("zh-TW", "中文(繁)"), ("ja", "日本語"),
    ("ko", "한국어"), ("es", "Español"), ("fr", "Français"), ("de", "Deutsch"),
    ("ru", "Русский"), ("pt", "Português"), ("it", "Italiano"), ("id", "Indonesia"),
    ("vi", "Tiếng Việt"), ("th", "ไทย"), ("ar", "العربية"),
]
_LANG_LABEL = dict(_LANGS)
_EMOJIS = ["😀", "😂", "🥰", "😊", "😎", "😉", "😢", "😭", "😡", "👍", "👎", "🙏",
           "👋", "❤️", "💔", "🔥", "✨", "🎉", "😴", "🤔", "😳", "🥺", "😤", "💀"]

# Persistent home for the Steam helper (daemon code + Playwright venv + Edge login
# profile), next to the app so it survives a temp wipe. NOTE: must NOT live under
# AppData\Local — the Claude Store-app sandbox redirects new AppData\Local folders
# into its private container, so files written there aren't visible to this (normal)
# app. The Desktop project dir is a real, shared path.
def _resolve_bridge_root() -> Path:
    from puripuly_heart.core.steam_module import helper_root

    return helper_root()


_BRIDGE_ROOT = _resolve_bridge_root()
_DAEMON_PY = _BRIDGE_ROOT / "steam_bridge" / "daemon.py"
_CACHE_FILE = _BRIDGE_ROOT / "steam_bridge" / "tr_cache.json"
_PREFS_FILE = _BRIDGE_ROOT / "steam_bridge" / "view_prefs.json"
def _find_daemon_python() -> Path:
    venv = _BRIDGE_ROOT / "steamprobe-venv"
    for cand in (venv / "Scripts" / "pythonw.exe", venv / "pythonw.exe",
                 venv / "Scripts" / "python.exe", venv / "python.exe"):
        if cand.exists():
            return cand
    return venv / "Scripts" / "pythonw.exe"


_DAEMON_PYTHON = _find_daemon_python()
_CREATE_NO_WINDOW = 0x08000000


def steam_module_installed() -> bool:
    """The Steam bridge is an optional module (like OCR): the tab only exists
    when the helper (daemon + its Playwright venv) is present on disk."""
    try:
        return _DAEMON_PY.exists() and _DAEMON_PYTHON.exists()
    except Exception:
        return False
_HOST, _PORT = "127.0.0.1", 8791

_SEND_FMT_LABEL = {
    "orig_trans": "Original + Translation",
    "orig_read_trans": "Original + Pinyin + Translation",
    "read_trans": "Pinyin + Translation",
    "read_only": "Pinyin Only",
    "trans_only": "Translation Only",
}

def _state_labels() -> dict:
    return {
        0: _T("steam.state_offline", default="Offline"),
        1: _T("steam.state_online", default="Online"),
        3: _T("steam.state_away", default="Away"),
        4: _T("steam.state_snooze", default="Snooze"),
        7: _T("steam.state_invisible", default="Invisible"),
    }


_STATE_LABEL = {0: "Offline", 1: "Online", 2: "Busy", 3: "Away", 4: "Snooze",
                5: "Looking to Trade", 6: "Looking to Play"}


def _avatar(url: str, size: int = 30) -> ft.Control:
    if url:
        return ft.Image(src=url, width=size, height=size, border_radius=6,
                        fit=ft.ImageFit.COVER)
    return ft.Container(width=size, height=size, border_radius=6, bgcolor="#3a3b3e")


def _name_color(state: int, ingame: bool) -> str:
    if ingame:
        return _C_INGAME
    if state in (3, 4):        # away / snooze — faded like real Steam
        return _C_AWAY
    return _C_ONLINE if state else _C_OFFLINE


def _spans(text: str, base_color: str = _TEXT_PRIMARY) -> list:
    out = []
    for part in _URL_RE.split(text or ""):
        if not part:
            continue
        if _URL_RE.fullmatch(part):
            out.append(ft.TextSpan(
                part, ft.TextStyle(color=_LINK, decoration=ft.TextDecoration.UNDERLINE),
                url=part))
        else:
            out.append(ft.TextSpan(part, ft.TextStyle(color=base_color)))
    return out or [ft.TextSpan("", ft.TextStyle(color=base_color))]


class SteamBridgeView(ft.Container):
    def __init__(self) -> None:
        super().__init__(expand=True, bgcolor=_BG_MAIN, padding=0)
        self.translate_message = None
        self.on_toggle_sidebar = None
        self.on_modal_change = None    # dashboard arms the side-column catcher
        self._src_lang = "en"
        self._tgt_lang = "zh-CN"
        self._proc = None
        self._reader = None
        self._writer = None
        self._own = 0
        self._own_name = "You"
        self._own_avatar = ""
        self._own_state = 1
        self._own_invites = 0
        self._own_invisible = False
        self._own_ingame = False
        self._own_game = ""
        self._pending_img: str | None = None   # queued image file to send
        self._show_pinyin = True     # seeded from the app's chat pinyin setting
        self._emoticons: list[str] = []
        self._active = None
        self._open_seq = 0
        self._last_block = None       # last rendered msg block, for same-sender coalescing
        self._tr_incoming = True      # translate their messages (Steam-tab setting)
        self._tr_outgoing = True      # translate my messages before sending
        self._show_original = True    # show the original line above the translation
        # what the FRIEND receives — same five formats as the Chat tab chatbox
        self._send_fmt = "trans_only"
        self._tr_provider = "default"  # "default" = app's translator (e.g. DeepL); "bing" = free
        self._fmt_expanded = False    # "Send as" pill expands/collapses its radio list
        self._read_zh = True          # per-language reading lines (like the Chat tab)
        self._read_ja = True
        self._read_ko = True
        self._read_latin = True
        self._pinyin_grouped = True   # grouped words vs per-syllable (Chat-tab option)
        # Injected by app.py: SettingsModal card picker + current-model label +
        # whether the picked model bills a personal API key.
        self.open_translator_picker = None
        self.translator_label = None
        self.translator_is_paid = None
        self.translator_value = None   # injected: resolved model value (cache key)
        self._resend_queue: list = []  # sends attempted while the helper was down
        self.on_module_state = None    # dashboard: grey the Steam chip when off
        self.on_popout = None          # app: open the tab in its own window
        self._is_popout = False        # standalone window: no module screens
        self._popped_out = False       # main window: the tab lives in a pop-out
        self._pref_tabs: list = []
        self._dnd = False              # Do Not Disturb: no unread dots
        self._pref_active = 0
        self._live_since_open: list = []   # messages rendered before history lands
        self.on_popout_restore = None  # app: close the pop-out window
        self._stickers: list = []
        self._emote_meta: dict = {}    # emote name -> source game name
        self._sticker_meta: dict = {}
        self._chat_cache: dict = {}    # acct -> last-rendered history blocks
        self._scroll_pos: dict = {}    # acct -> scroll px (open tabs keep position)
        self._was_following: dict = {} # acct -> was at the bottom when last viewed
        self._render_fp: dict = {}     # acct -> fingerprint of last rendered history
        self._module_on = True         # module toggle: off = helper not running, no RAM
        self._pend: dict | None = None   # short buffer: rapid same-sender lines → ONE block
        self._pend_task = None
        self._hist_blocks: list = []  # blocks currently rendered (for retranslate)
        self._seen_chat_ts: dict[int, int] = {}  # acct -> newest msg ts we've shown
        self._friends: dict[int, dict] = {}
        self._search_index: dict[int, str] = {}
        self._tabs: list[int] = []
        self._expanded_games: set[str] = set()
        self._collapsed_sections: set[str] = set()
        self._filter = ""
        self._tr_cache: dict[str, str] = {}
        self._tr_dirty = 0
        self._started = False
        self._prewarmed = False
        self._got_friends = False

        # left: own profile header + search + friends list (favorites are a
        # named section at the top of the list, Steam-style — see _rebuild_friends)
        self._own_header = ft.Container(
            padding=ft.padding.only(left=10, right=10, top=6, bottom=4))
        # Borderless field inside a fixed-height box — Flet's TextField ignores
        # `height` when it has a prefix icon, so build the box ourselves.
        self._search = ft.TextField(
            hint_text=_T("steam.search", default="Search friends"), dense=True, height=30,
            text_size=13, color=_TEXT_PRIMARY, expand=True,
            border=ft.InputBorder.NONE, bgcolor=ft.Colors.TRANSPARENT,
            text_vertical_align=ft.VerticalAlignment.CENTER,
            hint_style=ft.TextStyle(color=_TEXT_FAINT, size=13),
            content_padding=ft.padding.symmetric(horizontal=0, vertical=0),
            on_change=lambda e: self._on_search(e.control.value))
        search_box = ft.Container(
            content=ft.Row([ft.Icon(ft.Icons.SEARCH, size=16, color=_TEXT_FAINT), self._search],
                           spacing=6, vertical_alignment=ft.CrossAxisAlignment.CENTER),
            bgcolor=_BG_INPUT, border_radius=6, height=32,
            padding=ft.padding.only(left=8, right=8))
        self._friends_list = ft.ListView(
            expand=True, spacing=1,
            padding=ft.padding.only(left=4, right=10, top=6, bottom=6))
        self._left_panel = ft.Container(
            width=230, bgcolor=_BG_SIDE,
            content=ft.Column([
                self._own_header,
                ft.Container(content=search_box,
                             padding=ft.padding.only(left=8, right=8, top=0, bottom=4)),
                self._friends_list,
            ], spacing=0, expand=True))

        # right: chat pane
        self._chat_headinfo = ft.Container()
        self._from_lbl = ft.Text(self._lang_name(self._src_lang),
                                 size=12, color=_TEXT_PRIMARY, weight=ft.FontWeight.W_500)
        self._to_lbl = ft.Text(self._lang_name(self._tgt_lang),
                               size=12, color=_TEXT_PRIMARY, weight=ft.FontWeight.W_500)
        # The language selector is hoisted OUT into the dashboard's tab bar (far
        # right, same row as Chat/Steam). The dashboard reads self.lang_bar and mounts
        # it there; kept as an attribute so set_languages() still updates the labels.
        self.lang_bar = ft.Row([
            self._lang_button("from"),
            ft.Icon(ft.Icons.ARROW_RIGHT_ALT, size=15, color=_TEXT_FAINT),
            self._lang_button("to"),
        ], spacing=4, tight=True)
        # The tabs ARE the header — each tab shows the friend's name + status, the
        # active one highlighted. No separate (redundant) person header.
        self._tab_strip = ft.Row([], spacing=6, scroll=ft.ScrollMode.HIDDEN, expand=True)
        settings_btn = ft.IconButton(
            ft.Icons.SETTINGS_OUTLINED, icon_size=17, icon_color=_TEXT_FAINT,
            tooltip="Steam chat settings", visible=False,   # lives in the app header now
            on_click=lambda e: self._toggle_settings(),
            style=ft.ButtonStyle(overlay_color=ft.Colors.TRANSPARENT))
        popout_btn = ft.IconButton(
            ft.Icons.OPEN_IN_NEW, icon_size=16, icon_color=_TEXT_FAINT,
            tooltip=_T("steam.popout_tip", default="Open in its own window"),
            visible=False,             # lives in the app header row now
            on_click=lambda e: (self.on_popout() if callable(self.on_popout) else None),
            style=ft.ButtonStyle(overlay_color=ft.Colors.TRANSPARENT))
        top_bar = ft.Container(
            content=ft.Row([
                ft.Container(content=self._tab_strip, expand=True,
                             padding=ft.padding.only(bottom=6)),
                popout_btn, settings_btn,
            ], spacing=8,
                           vertical_alignment=ft.CrossAxisAlignment.CENTER),
            padding=ft.padding.only(left=8, right=12, top=6, bottom=4), bgcolor=_BG_MAIN)
        self._tab_bar = ft.Container(visible=False)   # kept: _rebuild_tabs toggles it

        # auto_scroll is deliberately OFF forever: Flutter re-applies it on ANY
        # repaint (window redraws, PrintScreen, etc.), yanking the list to the end.
        # Following is done by explicit scroll_to commands instead (_scroll_to_end).
        self._messages = ft.ListView(expand=True, spacing=10, padding=14, auto_scroll=False,
                                     on_scroll=self._on_msg_scroll)
        self._following = True
        self._max_scroll = 0.0
        # Centered horizontally at the bottom, matching the VRChat chat tab.
        self._jump_btn = ft.Container(
            visible=False, left=0, right=0, bottom=12, alignment=ft.alignment.center,
            content=ft.Container(
                width=34, height=34, border_radius=17,
                bgcolor="#3a3b3e", border=ft.border.all(1, "#55565a"),
                alignment=ft.alignment.center, tooltip="Jump to latest",
                content=ft.Icon(ft.Icons.KEYBOARD_DOUBLE_ARROW_DOWN_ROUNDED,
                                size=18, color="#e8e8e8"),
                on_click=lambda e: self._jump_to_latest()))
        self._typing_text = ft.Text("", size=12, color=_TEXT_FAINT)
        typing_row = ft.Container(content=self._typing_text, height=20,
                                  padding=ft.padding.only(left=14),
                                  alignment=ft.alignment.center_left)
        self._entry = ft.TextField(
            hint_text=_T("steam.input_hint", default="Type message to send"), disabled=True,
            border=ft.InputBorder.OUTLINE, border_color=_BORDER_INPUT,
            focused_border_color=_TOGGLE_ON, text_size=13, color=_TEXT_PRIMARY,
            hint_style=ft.TextStyle(color=_TEXT_FAINT, italic=True),
            expand=True, multiline=True, min_lines=2, max_lines=4, shift_enter=True,
            bgcolor=_BG_INPUT, border_radius=8,
            content_padding=ft.padding.symmetric(horizontal=12, vertical=8),
            on_change=self._on_entry_change,
            on_submit=lambda e: self.page.run_task(self._send))
        self._char_count = ft.Text("", size=10.5, color=_TEXT_FAINT, visible=False)
        emoji_btn = ft.IconButton(
            ft.Icons.EMOJI_EMOTIONS_OUTLINED, icon_size=20, icon_color=_TEXT_FAINT,
            tooltip="Emoji & emoticons", on_click=lambda e: self._toggle_emoji(),
            style=ft.ButtonStyle(overlay_color=ft.Colors.TRANSPARENT))
        input_row = ft.Container(
            content=ft.Row([
                ft.GestureDetector(
                    content=self._entry, expand=True,
                    on_secondary_tap_down=lambda e: self._show_input_menu(e)),
                ft.Column([
                    self._char_count,
                    ft.IconButton(ft.Icons.SEND_ROUNDED, icon_size=18, icon_color=_TOGGLE_ON,
                                  tooltip="Send", on_click=lambda e: self.page.run_task(self._send),
                                  style=ft.ButtonStyle(overlay_color=ft.Colors.TRANSPARENT,
                                                       padding=ft.padding.all(8))),
                ], spacing=0, tight=True,
                   horizontal_alignment=ft.CrossAxisAlignment.END),
                emoji_btn,
                ft.IconButton(ft.Icons.ATTACH_FILE, icon_size=18, icon_color=_TEXT_FAINT,
                              tooltip="Send an image",
                              on_click=lambda e: self._pick_image()),
                ft.IconButton(ft.Icons.MIC_NONE, icon_size=18, icon_color=_TEXT_FAINT,
                              tooltip="Voice message (coming soon)",
                              on_click=lambda e: self._not_yet("Voice messages")),
            ], spacing=4, vertical_alignment=ft.CrossAxisAlignment.CENTER),
            padding=ft.padding.symmetric(horizontal=8, vertical=6), bgcolor=_BG_MAIN)

        # queued image attachment (paste or picker) — shown above the input
        self._attach_chip = ft.Container(visible=False, bgcolor=_BG_MAIN,
                                         padding=ft.padding.only(left=10, top=4, bottom=2))
        chat_area = ft.Column([
            top_bar,
            ft.Divider(height=1, color=_DIVIDER, thickness=1),
            ft.Container(content=ft.Stack([
                ft.SelectionArea(content=self._messages), self._jump_btn,
            ], expand=True), expand=True),
            typing_row,
            ft.Divider(height=1, color=_DIVIDER, thickness=1),
            self._attach_chip,
            input_row,
        ], spacing=0, expand=True)
        self._file_picker = ft.FilePicker(on_result=self._on_pick_file)

        self._main_divider = ft.VerticalDivider(width=1, color=_DIVIDER, thickness=1)
        self._main_row = main_row = ft.Row([
            self._left_panel,
            self._main_divider,
            ft.Container(content=chat_area, expand=True),
        ], spacing=0, expand=True, vertical_alignment=ft.CrossAxisAlignment.STRETCH)
        self._ctx_offset_x = 0   # set when the friends column moves out of this view

        # No spinner/loading screen — show a skeleton of the friends list instead,
        # so a cold start reads as "content loading in", not a loading screen.
        self._friends_list.controls = self._skeleton_rows()
        self._loading = ft.Container(visible=False)   # kept only so _hide_loading is a no-op

        # right-click context menu (cursor-positioned) + a backdrop so any click
        # dismisses it. The backdrop closes on left OR right click.
        # Backdrop covers only the chat area (x>=253), NOT the friends list — so
        # right-clicking a different friend while the menu is open switches it in
        # one click, while a click in the chat area still dismisses the menu.
        self._ctx_backdrop = ft.Container(
            visible=False, left=231, top=0, right=0, bottom=0,
            content=ft.GestureDetector(
                on_tap=lambda e: self._hide_ctx(),
                on_secondary_tap_down=lambda e: self._hide_ctx(),
                content=ft.Container(expand=True, bgcolor=ft.Colors.TRANSPARENT)))
        self._ctx_menu = ft.Container(visible=False, bgcolor=_BG_MENU, border_radius=8,
                                      border=ft.border.all(1, "#4b4c4f"), padding=4,
                                      width=210, left=60, top=60,
                                      shadow=ft.BoxShadow(blur_radius=14, color="#88000000",
                                                          offset=ft.Offset(0, 4)))
        # emoji / emoticon picker panel (anchored bottom-right, over the input)
        self._picker_tab = "emotes"
        self._picker_query = ""
        self._recent_picks: list = []      # [kind, value] — "u" emoji / "e" emote / "s" sticker
        self._picker_grid = ft.Container(expand=True)
        self._picker_search = ft.TextField(
            hint_text=_T("steam.search_hint", default="Search..."),
            dense=True, height=36, text_size=12.5, border_radius=8,
            border_color="#4b4c4f", focused_border_color=_TOGGLE_ON,
            content_padding=ft.padding.symmetric(horizontal=10, vertical=6),
            on_change=self._on_picker_search)
        self._emoji_panel = ft.Container(
            visible=False, bgcolor=_BG_MENU, border_radius=10,
            border=ft.border.all(1, "#4b4c4f"), width=352, height=430,
            right=8, bottom=64, padding=8,
            shadow=ft.BoxShadow(blur_radius=16, color="#99000000", offset=ft.Offset(0, 4)))
        self._emoji_backdrop = ft.GestureDetector(
            visible=False, on_tap=lambda e: self._toggle_emoji(False),
            content=ft.Container(expand=True, bgcolor=ft.Colors.TRANSPARENT))
        # Steam-tab settings panel (gear in the top bar) + input right-click menu.
        self._settings_panel = ft.Container(
            visible=False, bgcolor=_BG_MENU, border_radius=10,
            border=ft.border.all(1, "#4b4c4f"), width=320,
            right=8, top=44, padding=8,
            shadow=ft.BoxShadow(blur_radius=16, color="#99000000", offset=ft.Offset(0, 4)))
        self._settings_backdrop = ft.GestureDetector(
            visible=False, on_tap=lambda e: self._toggle_settings(False),
            content=ft.Container(expand=True, bgcolor=ft.Colors.TRANSPARENT))
        self._input_menu = ft.Container(
            visible=False, bgcolor=_BG_MENU, border_radius=8,
            border=ft.border.all(1, "#4b4c4f"), padding=4, width=170,
            left=300, bottom=70,
            shadow=ft.BoxShadow(blur_radius=14, color="#88000000", offset=ft.Offset(0, 4)))
        self._state_mode = ""
        self._state_icon = ft.Icon(ft.Icons.POWER_SETTINGS_NEW, size=42, color=_TEXT_FAINT)
        self._state_title = ft.Text("", size=16, weight=ft.FontWeight.W_600,
                                    color=_TEXT_PRIMARY)
        self._state_caption = ft.Text("", size=12.5, color=_TEXT_FAINT, width=400,
                                      text_align=ft.TextAlign.CENTER)
        self._state_btn_text = ft.Text("", size=13, weight=ft.FontWeight.W_600,
                                       color="#ffffff")
        self._state_prog = ft.ProgressRing(width=18, height=18, stroke_width=2,
                                           color=_TOGGLE_ON, visible=False)
        self._state_btn = ft.Container(
            content=self._state_btn_text, bgcolor="#2f89bd", border_radius=6,
            padding=ft.padding.symmetric(horizontal=18, vertical=9), ink=True,
            on_click=lambda e: self._state_action())
        self._state_btn2 = ft.Container(
            visible=False,
            content=ft.Text(_T("steam.sign_out", default="Sign out of Steam"),
                            size=12.5, color=_TEXT_FAINT),
            border=ft.border.all(1, "#55565a"), border_radius=6,
            padding=ft.padding.symmetric(horizontal=14, vertical=7), ink=True,
            on_click=lambda e: self._signout_prompt())
        self._state_overlay = ft.Container(
            visible=False, expand=True, bgcolor=_BG_MAIN,
            alignment=ft.alignment.center,
            content=ft.Column([self._state_icon, self._state_title,
                               self._state_caption, self._state_prog,
                               ft.Container(height=6), self._state_btn,
                               self._state_btn2],
                              spacing=10, tight=True,
                              horizontal_alignment=ft.CrossAxisAlignment.CENTER))
        self._hover_token = None
        self._hover_card = ft.Container(
            visible=False, left=12, top=76, bgcolor="#1e1f22",
            border=ft.border.all(1, "#4b4c4f"), border_radius=8, padding=8,
            shadow=ft.BoxShadow(blur_radius=14, color="#88000000",
                                offset=ft.Offset(0, 4)))
        self._viewer = ft.Container(visible=False, left=0, top=0, right=0,
                                    bottom=0)
        # the image lightbox lives in page.overlay so its scrim covers the
        # WHOLE window — sidebar, header bar and tab strip live outside this
        # view's Stack and could never be covered from in here
        self._viewer_overlay = ft.Container(visible=False, expand=True)
        self.content = ft.Stack([main_row, self._loading,
                                  self._emoji_backdrop, self._emoji_panel,
                                  self._ctx_backdrop, self._ctx_menu,
                                  self._input_menu, self._hover_card,
                                  self._state_overlay,
                                  self._settings_backdrop, self._settings_panel,
                                  self._viewer], expand=True)
        self._load_cache()
        self._load_prefs()
        self._update_own_header()

    def detach_left_panel(self) -> ft.Control:
        """Hand the friends column to the dashboard so it can occupy the sidebar
        slot (full window height under a sidebar-style brand header — true Chat-tab
        parity). Removes it and the divider from this view's own row and shifts the
        context-menu X math by the width the dashboard slot occupies."""
        with contextlib.suppress(ValueError):
            self._main_row.controls.remove(self._left_panel)
        with contextlib.suppress(ValueError):
            self._main_row.controls.remove(self._main_divider)
        self._left_panel.width = None      # fill the dashboard slot's width
        self._left_panel.expand = True     # and its full height
        self._ctx_backdrop.left = 0        # friends rows are outside this Stack now
        self._ctx_offset_x = 220
        return self._left_panel

    def _lang_name(self, code: str) -> str:
        """Language label in the app's UI language (same names as the Chat tab),
        falling back to the native name."""
        with contextlib.suppress(Exception):
            from puripuly_heart.ui.i18n import t
            s = t(f"language.{code}")
            if s and s != f"language.{code}":
                return s
        return _LANG_LABEL.get(code, code)

    def _pick_lang(self, which: str, code: str) -> None:
        """Set a chat language. Applies to FUTURE messages immediately; history
        changes only via Retranslate (so no surprise DeepL spend)."""
        if which == "from":
            self._src_lang = code
        else:
            self._tgt_lang = code
        lbl = self._from_lbl if which == "from" else self._to_lbl
        lbl.value = self._lang_name(code)
        with contextlib.suppress(Exception):
            lbl.update()
        # keep the settings panel's language rows in sync if it's open
        if self._settings_panel.visible:
            self._build_settings_panel()
            with contextlib.suppress(Exception):
                self._settings_panel.update()
        self._notice(_T("steam.applies_new", default="Applies to new messages — Retranslate history converts this chat."))

    def _notice(self, msg: str) -> None:
        if self.page:
            with contextlib.suppress(Exception):
                self.page.open(ft.SnackBar(ft.Text(msg)))

    def _lang_button(self, which: str) -> ft.Control:
        lbl = self._from_lbl if which == "from" else self._to_lbl
        return ft.PopupMenuButton(
            content=ft.Row([lbl, ft.Icon(ft.Icons.ARROW_DROP_DOWN, size=14, color=_TEXT_FAINT)],
                           spacing=0, tight=True),
            tooltip=("Your language" if which == "from" else "Their language"),
            items=[ft.PopupMenuItem(text=self._lang_name(c), on_click=(lambda e, c=c, w=which: self._pick_lang(w, c)))
                   for c, l in _LANGS])

    def set_languages(self, src: str, tgt: str) -> None:
        if src:
            self._src_lang = src
            self._from_lbl.value = _LANG_LABEL.get(src, src)
        if tgt:
            self._tgt_lang = tgt
            self._to_lbl.value = _LANG_LABEL.get(tgt, tgt)

    # ── lifecycle ────────────────────────────────────────────────────────────
    def prewarm(self) -> None:
        if not getattr(self, "_module_on", True):
            return                      # module off: keep RAM free, spawn nothing
        if self._prewarmed:
            return
        self._prewarmed = True
        with contextlib.suppress(Exception):
            s = socket.socket(); s.settimeout(0.25)
            try:
                s.connect((_HOST, _PORT)); s.close(); return
            except Exception:
                s.close()
        with contextlib.suppress(Exception):
            import subprocess
            self._proc = subprocess.Popen(
                [str(_DAEMON_PYTHON), str(_DAEMON_PY)],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                creationflags=_CREATE_NO_WINDOW)

    def activate(self) -> None:
        # the FilePicker must live in page.overlay to open dialogs
        if self.page and self._file_picker not in self.page.overlay:
            with contextlib.suppress(Exception):
                self.page.overlay.append(self._file_picker)
                self.page.update()
        if not self._module_on:
            self._show_state_overlay("off")
            return
        if self._popped_out and not self._is_popout:
            self._show_state_overlay("popped")
            return
        if self._started:
            _vlog.info("[SteamView] activate: started=1 got_friends=%s mode=%r",
                       self._got_friends, self._state_mode)
            if not self._got_friends:
                self._show_state_overlay("connecting")
            elif self._active is None and not self._state_overlay.visible:
                self._show_state_overlay("idle")
            return
        self._started = True
        self._paint_snapshot()
        _vlog.info("[SteamView] activate cold: friends=%d got=%s page=%s",
                   len(self._friends), self._got_friends, self.page is not None)
        if not self._got_friends:
            self._show_state_overlay("connecting")
        if self.page:
            self.page.run_task(self._connect)

    def _show_state_overlay(self, mode: str) -> None:
        _vlog.info("[SteamView] overlay -> %r (page=%s)", mode, self.page is not None)
        if self._is_popout and mode in ("idle", "off", "popped"):
            return                      # module control lives in the main app
        self._state_mode = mode
        self._state_btn2.visible = (mode == "idle")
        if mode == "connecting":
            self._state_icon.name = ft.Icons.CLOUD_SYNC_OUTLINED
            self._state_icon.color = _TEXT_FAINT
            self._state_title.value = _T("steam.connecting_title",
                                         default="Connecting to Steam")
            self._state_caption.value = _T(
                "steam.connecting_caption",
                default="Right after the app starts this can take a little "
                        "while — your chats appear as soon as it's ready.")
            self._state_prog.visible = True
            self._state_btn.visible = False
        elif mode == "popped":
            self._state_icon.name = ft.Icons.OPEN_IN_NEW
            self._state_icon.color = _TOGGLE_ON
            self._state_title.value = _T("steam.popped_title",
                                         default="Opened in its own window")
            self._state_caption.value = _T(
                "steam.popped_caption",
                default="Close that window — or use the button below — to bring "
                        "the chat back here.")
            self._state_btn_text.value = _T("steam.popped_btn", default="Bring it back")
        elif mode == "idle":
            self._state_icon.name = ft.Icons.POWER_SETTINGS_NEW
            self._state_icon.color = _TOGGLE_ON
            self._state_title.value = _T("steam.idle_title", default="Steam Chat is running")
            self._state_caption.value = _T(
                "steam.idle_caption",
                default="Pick a friend from the list to start chatting. Turning "
                        "the module off frees all of its RAM.")
            self._state_btn_text.value = _T("steam.turn_off", default="Turn off")
        elif mode == "off":
            self._state_icon.name = ft.Icons.POWER_SETTINGS_NEW
            self._state_icon.color = _TEXT_FAINT
            self._state_title.value = _T("steam.off_title", default="Steam Chat is turned off")
            self._state_caption.value = _T(
                "steam.off_caption",
                default="The Steam helper and its hidden browser are not running, so this module uses no RAM.")
            self._state_btn_text.value = _T("steam.off_btn", default="Turn on")
        else:
            self._state_icon.name = ft.Icons.LOGIN
            self._state_title.value = _T("steam.signin_title", default="Not signed in to Steam")
            self._state_caption.value = _T(
                "steam.signin_caption",
                default="Signing in opens a Steam window — log in there and it closes by itself.")
            self._state_btn_text.value = _T("steam.signin_btn", default="Sign in to Steam")
        self._state_overlay.visible = True
        if self.page:
            with contextlib.suppress(Exception):
                self.page.update()

    def _hide_state_overlay(self) -> None:
        self._state_btn.visible = True
        self._state_prog.visible = False
        if self._state_overlay.visible:
            self._state_overlay.visible = False
            if self.page:
                with contextlib.suppress(Exception):
                    self.page.update()

    def _state_action(self) -> None:
        if self._state_mode == "popped":
            if callable(self.on_popout_restore):
                self.on_popout_restore()
            return
        if self._state_mode == "idle":
            self._module_off()
            return
        if self._state_mode == "off":
            self._module_on = True
            self._save_prefs()
            if callable(self.on_module_state):
                with contextlib.suppress(Exception):
                    self.on_module_state(True)
            self._hide_state_overlay()
            self._started = False
            self._prewarmed = False
            self._writer = None
            self._reader = None
            self.activate()
        else:
            if self._state_btn.disabled:
                return                      # a sign-in is already running
            self._state_btn.disabled = True
            self._state_caption.value = _T(
                "steam.signin_wait",
                default="A Steam sign-in window is opening — log in there. This can take a moment…")
            with contextlib.suppress(Exception):
                self._state_caption.update()
                self._state_btn.update()
            if self.page:
                self.page.run_task(self._cmd, {"cmd": "login"})

    def _signout_prompt(self) -> None:
        dlg = ft.AlertDialog(
            modal=True,
            title=ft.Text(_T("steam.signout_q", default="Sign out of Steam?"), size=15),
            content=ft.Text(_T(
                "steam.signout_body",
                default="This disconnects the Steam tab and clears the saved login. Signing back in opens a Steam window."), size=13),
            actions=[
                ft.TextButton(_T("steam.cancel", default="Cancel"), on_click=lambda e: self.page.close(dlg)),
                ft.TextButton(_T("steam.sign_out", default="Sign out"), on_click=lambda e: (
                    self.page.close(dlg),
                    self._toggle_settings(False),
                    self.page.run_task(self._cmd, {"cmd": "signout"}),
                    self._show_state_overlay("signedout"))),
            ])
        with contextlib.suppress(Exception):
            self.page.open(dlg)

    def _module_off(self) -> None:
        self._module_on = False
        self._save_prefs()
        if callable(self.on_module_state):
            with contextlib.suppress(Exception):
                self.on_module_state(False)
        self._toggle_settings(False)

        async def _shutdown() -> None:
            with contextlib.suppress(Exception):
                await self._cmd({"cmd": "quit"})
            with contextlib.suppress(Exception):
                if self._writer is not None:
                    self._writer.close()
            self._writer = None
            self._reader = None
            self._started = False
            self._prewarmed = False

        if self.page:
            self.page.run_task(_shutdown)
        self._show_state_overlay("off")

    _MAX_MSG = 5000   # Steam's chat message limit (approx; server truncates)

    def _on_entry_change(self, e) -> None:
        # hard cap without max_length (its counter reserves an ugly empty line)
        if len(self._entry.value or "") > self._MAX_MSG:
            self._entry.value = (self._entry.value or "")[: self._MAX_MSG]
            with contextlib.suppress(Exception):
                self._entry.update()
        n = len(self._entry.value or "")
        show = n > int(self._MAX_MSG * 0.8)
        self._char_count.value = f"{n} / {self._MAX_MSG}"
        self._char_count.color = "#d96a6a" if n > self._MAX_MSG else _TEXT_FAINT
        if show != self._char_count.visible:
            self._char_count.visible = show
        with contextlib.suppress(Exception):
            self._char_count.update()

    def _insert_emoji(self, em: str) -> None:
        self._push_recent("u", em)
        self._entry.value = (self._entry.value or "") + em
        if self.page:
            self._entry.update()

    def _sticker_url(self, name: str) -> str:
        return ("https://community.fastly.steamstatic.com/economy/sticker/"
                f"{name}/sticker.png")

    def _send_effect(self, name: str) -> None:
        if not self._active:
            return
        self._toggle_emoji(False)
        acct = self._active
        if self.page:
            self.page.run_task(self._cmd, {"cmd": "send_effect", "acct": acct,
                                           "name": name})

    def _send_sticker(self, name: str) -> None:
        # Stickers send immediately on click, like real Steam.
        if not self._active:
            return
        self._push_recent("s", name)
        self._toggle_emoji(False)
        acct = self._active
        url = self._sticker_url(name)

        async def _go() -> None:
            await self._render_live({
                "from_me": True, "name": self._own_name, "avatar": self._own_avatar,
                "text": "", "images": [], "stickers": [url],
                "ts": int(time.time())})
            await self._cmd({"cmd": "send_sticker", "acct": acct, "name": name})

        if self.page:
            self.page.run_task(_go)

    def _insert_emoticon(self, name: str) -> None:
        self._push_recent("e", name)
        # Steam emoticons are sent as :name: and render on both ends.
        # The panel STAYS OPEN so several can be clicked in a row (emoji parity).
        self._entry.value = (self._entry.value or "") + f":{name}: "
        if self.page:
            self._entry.update()

    _FX_ICONS = {"balloons": "🎈", "confetti": "🎉", "firework": "🎆",
                 "fireworks": "🎆", "goldfetti": "🎊"}

    def _effect_of(self, b: dict):
        """(icon, effect_name) when this message is a room-effect line the
        helper rendered as 'icon name' — else None."""
        t = (b.get("text") or "").strip()
        parts = t.split()
        if len(parts) == 2 and parts[1].lower() in self._FX_ICONS                 and parts[0] in self._FX_ICONS.values():
            return parts[0], parts[1]
        return None

    def _play_effect_burst(self, icon: str) -> None:
        """A light in-app approximation of Steam's fullscreen effect."""
        row = ft.Row([ft.Text(icon, size=44) for _ in range(7)],
                     alignment=ft.MainAxisAlignment.SPACE_AROUND)
        burst = ft.Container(content=ft.Column([row, row], spacing=60),
                             alignment=ft.alignment.center, expand=True,
                             opacity=1.0, animate_opacity=900)
        self._viewer.content = burst      # reuse the topmost overlay slot
        self._viewer.visible = True

        async def _fade() -> None:
            await asyncio.sleep(0.35)
            burst.opacity = 0.0
            with contextlib.suppress(Exception):
                burst.update()
            await asyncio.sleep(1.0)
            self._viewer.visible = False
            with contextlib.suppress(Exception):
                self.page.update()

        if self.page:
            with contextlib.suppress(Exception):
                self.page.update()
            self.page.run_task(_fade)

    def _show_emote_card(self, kind: str, name: str,
                         gx: float, gy: float) -> None:
        # Steam-style hover card: compact, next to the hovered emote.
        url = self._emoticon_url(name) if kind == "e" else self._sticker_url(name)
        game = (self._emote_meta if kind == "e" else self._sticker_meta).get(name, "")
        size = 44 if kind == "e" else 72
        self._hover_card.content = ft.Row([
            ft.Image(src=url, width=size, height=size, fit=ft.ImageFit.CONTAIN),
            ft.Column([
                ft.Text(f":{name}:" if kind == "e" else name, size=13,
                        weight=ft.FontWeight.W_600, color=_TEXT_PRIMARY),
                *([ft.Text(game, size=11, color=_TEXT_FAINT)] if game else []),
            ], spacing=1, tight=True, alignment=ft.MainAxisAlignment.CENTER),
        ], spacing=8, tight=True)
        # page-global -> view-stack coords (the stack sits right of the app
        # sidebar and below the header in the main window; near 0,0 popped out)
        dx, dy = (10, 44) if self._is_popout else (232, 92)
        self._hover_card.left = max(6, gx - dx + 34)
        self._hover_card.top = max(6, gy - dy - 38)
        self._hover_card.on_click = lambda e: self._hide_emote_card()
        self._hover_card.visible = True
        with contextlib.suppress(Exception):
            self._hover_card.update()
        # safety net: a missed hover-exit (card overlapping the cell swallows
        # it) must never strand the card on screen
        self._hover_show_tok = tok = object()

        async def _auto_hide() -> None:
            await asyncio.sleep(3.5)
            if getattr(self, "_hover_show_tok", None) is tok:
                self._hide_emote_card()

        if self.page:
            self.page.run_task(_auto_hide)

    def _hide_emote_card(self, _e=None) -> None:
        self._hover_token = None
        self._hover_armed = None
        if self._hover_card.visible:
            self._hover_card.visible = False
            with contextlib.suppress(Exception):
                self._hover_card.update()

    def _on_emote_hover(self, e, kind: str, name: str) -> None:
        gx = float(getattr(e, "global_x", 0) or 0)
        gy = float(getattr(e, "global_y", 0) or 0)
        # GestureDetector streams hover events; remember the freshest position
        # but only arm ONE delayed show per entry
        self._hover_pos = (gx, gy)
        if getattr(self, "_hover_armed", None) == (kind, name):
            return
        self._hover_armed = (kind, name)
        self._hover_token = tok = object()

        async def _delayed() -> None:
            await asyncio.sleep(0.5)
            if self._hover_token is tok:
                px, py = getattr(self, "_hover_pos", (gx, gy))
                self._show_emote_card(kind, name, px, py)

        if self.page:
            self.page.run_task(_delayed)

    def _emote_hoverable(self, control, kind: str, name: str) -> ft.Control:
        return ft.GestureDetector(
            content=control,
            on_hover=lambda e, k=kind, n=name: self._on_emote_hover(e, k, n),
            on_exit=lambda e: self._hide_emote_card())

    def _emoticon_url(self, name: str) -> str:
        return f"https://community.fastly.steamstatic.com/economy/emoticon/{name}"

    def _push_recent(self, kind: str, value: str) -> None:
        item = [kind, value]
        self._recent_picks = ([item] +
                              [x for x in self._recent_picks if x != item])[:24]
        self._save_prefs()

    def _on_picker_search(self, e) -> None:
        self._picker_query = (e.control.value or "").strip().lower()
        self._fill_picker_grid()
        with contextlib.suppress(Exception):
            self._picker_grid.update()

    def _set_picker_tab(self, tab: str) -> None:
        self._picker_tab = tab
        self._build_emoji_panel()
        with contextlib.suppress(Exception):
            self._emoji_panel.update()

    def _picker_cell(self, control, on_click, tip=None, big=False):
        side = 60 if big else 36
        return ft.Container(content=control, on_click=on_click, ink=True,
                            border_radius=6, padding=3, width=side, height=side,
                            tooltip=tip, alignment=ft.alignment.center)

    def _fill_picker_grid(self) -> None:
        q = self._picker_query
        tab = self._picker_tab
        cells = []
        if tab == "recent":
            for kind, val in self._recent_picks:
                if kind == "u":
                    cells.append(self._picker_cell(
                        ft.Text(val, size=20),
                        (lambda ev, em=val: self._insert_emoji(em))))
                elif kind == "e":
                    cells.append(self._picker_cell(
                        ft.Image(src=self._emoticon_url(val), width=26, height=26,
                                 fit=ft.ImageFit.CONTAIN),
                        (lambda ev, nm=val: self._insert_emoticon(nm)), tip=f":{val}:"))
                elif kind == "s":
                    cells.append(self._picker_cell(
                        ft.Image(src=self._sticker_url(val), width=52, height=52,
                                 fit=ft.ImageFit.CONTAIN),
                        (lambda ev, nm=val: self._send_sticker(nm)), tip=val, big=True))
            if not cells:
                self._picker_grid.content = ft.Container(
                    content=ft.Text(_T("steam.no_recent",
                                       default="Things you use appear here"),
                                    size=12, color=_TEXT_FAINT),
                    alignment=ft.alignment.center, padding=20)
                return
        elif tab == "emoji":
            cells = [self._picker_cell(ft.Text(e, size=20),
                                       (lambda ev, em=e: self._insert_emoji(em)))
                     for e in _EMOJIS]
        elif tab == "emotes":
            names = [n for n in self._emoticons if q in n.lower()] if q else self._emoticons
            cells = [self._picker_cell(
                self._emote_hoverable(
                    ft.Image(src=self._emoticon_url(n), width=26, height=26,
                             fit=ft.ImageFit.CONTAIN), "e", n),
                (lambda ev, nm=n: self._insert_emoticon(nm)))
                for n in names]
        elif tab == "effects":
            effects = getattr(self, "_effects", None) or _ROOM_EFFECTS
            cells = [self._picker_cell(
                ft.Text(icon, size=24),
                (lambda ev, nm=name: self._send_effect(nm)), tip=f"/{name}",
                big=True)
                for name, icon in effects]
        else:
            names = [n for n in self._stickers if q in n.lower()] if q else self._stickers
            cells = [self._picker_cell(
                self._emote_hoverable(
                    ft.Image(src=self._sticker_url(n), width=52, height=52,
                             fit=ft.ImageFit.CONTAIN), "s", n),
                (lambda ev, nm=n: self._send_sticker(nm)), big=True)
                for n in names]
        hdr_key = {"recent": "steam.hdr_recent", "emoji": "steam.hdr_emoji",
                   "emotes": "steam.hdr_emoticons", "stickers": "steam.hdr_stickers",
                   "effects": "steam.hdr_effects"}[tab]
        hdr_default = {"recent": "RECENT", "emoji": "EMOJI",
                       "emotes": "EMOTICONS", "stickers": "STICKERS",
                       "effects": "ROOM EFFECTS"}[tab]
        self._picker_grid.content = ft.Column(
            [ft.Text(_T(hdr_key, default=hdr_default), size=11,
                     weight=ft.FontWeight.BOLD, color=_SECTION),
             ft.Row(cells, wrap=True, spacing=2, run_spacing=2)],
            spacing=6, scroll=ft.ScrollMode.AUTO, expand=True)

    def _build_emoji_panel(self) -> None:
        def tab_btn(tab, icon, tip):
            active = self._picker_tab == tab
            return ft.Container(
                content=ft.Icon(icon, size=19,
                                color=_TOGGLE_ON if active else _TEXT_FAINT),
                border=ft.border.only(bottom=ft.BorderSide(
                    2, _TOGGLE_ON if active else ft.Colors.TRANSPARENT)),
                padding=ft.padding.symmetric(horizontal=14, vertical=6),
                ink=True, tooltip=tip,
                on_click=lambda e, t=tab: self._set_picker_tab(t))

        tabs = ft.Row([
            tab_btn("recent", ft.Icons.SCHEDULE,
                    _T("steam.tab_recent", default="Recent")),
            tab_btn("emoji", ft.Icons.EMOJI_EMOTIONS_OUTLINED,
                    _T("steam.tab_emoji", default="Emoji")),
            tab_btn("emotes", ft.Icons.SENTIMENT_SATISFIED_ALT,
                    _T("steam.tab_emoticons", default="Emoticons")),
            tab_btn("stickers", ft.Icons.NOTE_OUTLINED,
                    _T("steam.tab_stickers", default="Stickers")),
            tab_btn("effects", ft.Icons.AUTO_AWESOME,
                    _T("steam.tab_effects", default="Room Effects")),
        ], spacing=0, alignment=ft.MainAxisAlignment.CENTER)
        self._fill_picker_grid()
        show_search = self._picker_tab in ("emotes", "stickers")
        self._picker_search.visible = show_search
        self._emoji_panel.content = ft.Column(
            [tabs, ft.Divider(height=1, color="#4b4c4f"),
             self._picker_grid, self._picker_search],
            spacing=6, tight=True)

    def _toggle_emoji(self, show=None) -> None:
        show = (not self._emoji_panel.visible) if show is None else show
        if show:
            self._build_emoji_panel()
        self._emoji_panel.visible = show
        self._emoji_backdrop.visible = show
        if self.page:
            self.page.update()

    # ── Steam-tab settings (gear) ────────────────────────────────────────────
    def _toggle_settings(self, show=None) -> None:
        show = (not self._settings_panel.visible) if show is None else show
        if show:
            self._build_settings_panel()
        self._settings_panel.visible = show
        self._settings_backdrop.visible = show
        if self.page:
            self.page.update()

    def _build_settings_panel(self) -> None:
        # Mirrors the Chat tab's Output Format controls exactly: label + info
        # icon + bordered On/Off pill on the right for booleans, clickable format
        # pill that expands a radio list, teal checkboxes for the reading
        # languages, dense 30-32px rows.
        def info(tip):
            return ft.Icon(ft.Icons.INFO_OUTLINE, size=13, color=_TEXT_FAINT,
                           tooltip=tip)

        def onoff(val, cb):
            return ft.Container(
                content=ft.Text("On" if val else "Off", size=12,
                                weight=ft.FontWeight.W_600,
                                color=_TOGGLE_ON if val else _TEXT_FAINT),
                border=ft.border.all(1, _TOGGLE_ON if val else "#55565a"),
                border_radius=8, width=46, height=28, ink=True,
                alignment=ft.alignment.center,
                on_click=lambda e, v=not val: cb(v))

        def toggle_row(label, tip, val, cb):
            return ft.Container(
                height=34, padding=ft.padding.symmetric(horizontal=6),
                content=ft.Row([
                    ft.Text(label, size=13, color=_TEXT_PRIMARY),
                    info(tip),
                    ft.Container(expand=True),
                    onoff(val, cb),
                ], spacing=4, vertical_alignment=ft.CrossAxisAlignment.CENTER))

        def pill_row(label, tip, text, on_click):
            return ft.Container(
                height=34, padding=ft.padding.symmetric(horizontal=6),
                content=ft.Row([
                    ft.Text(label, size=13, color=_TEXT_PRIMARY),
                    info(tip),
                    ft.Container(expand=True),
                    ft.Container(
                        content=ft.Text(text, size=12.5, color=_TOGGLE_ON,
                                        weight=ft.FontWeight.W_600),
                        border=ft.border.all(1, _TOGGLE_ON), border_radius=8,
                        ink=True, padding=ft.padding.symmetric(horizontal=10, vertical=4),
                        on_click=on_click),
                ], spacing=4, vertical_alignment=ft.CrossAxisAlignment.CENTER))

        def menu_row(label, tip, current, items):
            return ft.Container(
                height=32, padding=ft.padding.symmetric(horizontal=6),
                content=ft.Row([
                    ft.Text(label, size=13, color=_TEXT_PRIMARY),
                    info(tip),
                    ft.Container(expand=True),
                    ft.PopupMenuButton(
                        content=ft.Row([
                            ft.Text(current, size=13, color=_TOGGLE_ON,
                                    weight=ft.FontWeight.W_500),
                            ft.Icon(ft.Icons.ARROW_DROP_DOWN, size=14, color=_TEXT_FAINT),
                        ], spacing=0, tight=True),
                        items=items),
                ], spacing=4, vertical_alignment=ft.CrossAxisAlignment.CENTER))

        def check_row(label, val, cb):
            return ft.Container(
                content=ft.Row([
                    ft.Icon(ft.Icons.CHECK_BOX if val else ft.Icons.CHECK_BOX_OUTLINE_BLANK,
                            size=17, color=_TOGGLE_ON if val else _TEXT_FAINT),
                    ft.Text(label, size=13, color=_TEXT_PRIMARY),
                ], spacing=8, vertical_alignment=ft.CrossAxisAlignment.CENTER),
                height=27, border_radius=6, ink=True,
                padding=ft.padding.only(left=20, right=6),
                on_click=lambda e, v=not val: cb(v))

        def radio_row(label, selected, on_pick):
            return ft.Container(
                content=ft.Row([
                    ft.Icon(ft.Icons.RADIO_BUTTON_CHECKED if selected
                            else ft.Icons.RADIO_BUTTON_UNCHECKED,
                            size=16, color=_TOGGLE_ON if selected else _TEXT_FAINT),
                    ft.Text(label, size=13, color=_TEXT_PRIMARY),
                ], spacing=8, vertical_alignment=ft.CrossAxisAlignment.CENTER),
                height=28, border_radius=6, ink=True,
                padding=ft.padding.only(left=14, right=6),
                on_click=on_pick)

        def btn(label, on_click, tip=None):
            return ft.Container(
                content=ft.Text(label, size=13, color=_TEXT_PRIMARY),
                height=30, border_radius=6, ink=True, tooltip=tip,
                padding=ft.padding.only(left=6, top=6), on_click=on_click)

        lang_items = lambda which: [ft.PopupMenuItem(
            text=self._lang_name(c),
            on_click=(lambda e, c=c, w=which: self._pick_lang(w, c)))
            for c, _l in _LANGS]

        self._settings_panel.content = ft.Column([
            ft.Row([
                ft.Icon(ft.Icons.TUNE, size=17, color=_TEXT_FAINT),
                ft.Text(_T("steam.settings_title", default="Steam Chat Settings"), size=14, weight=ft.FontWeight.W_600,
                        color=_TEXT_PRIMARY),
            ], spacing=8, vertical_alignment=ft.CrossAxisAlignment.CENTER),
            menu_row(_T("steam.my_language", default="My language"), _T("steam.tip_my_language", default="Their messages are translated into this"),
                     self._lang_name(self._src_lang), lang_items("from")),
            menu_row(_T("steam.their_language", default="Their language"), _T("steam.tip_their_language", default="Your messages are translated into this"),
                     self._lang_name(self._tgt_lang), lang_items("to")),
            pill_row(_T("steam.translator", default="Translator"), _T("steam.tip_translator", default="Which model translates this chat"),
                     self._translator_pill_label(),
                     lambda e: (self.open_translator_picker()
                                if callable(self.open_translator_picker) else None)),
            ft.Divider(height=1, color="#4b4c4f"),
            pill_row(_T("steam.send_to_them", default="Send to them"), _T("steam.tip_send", default="What your friend receives on Steam"),
                     _send_fmt_labels()[self._send_fmt],
                     lambda e: self._toggle_fmt_expanded()),
            *([radio_row(lbl, m == self._send_fmt,
                         (lambda e, m=m: self._set_send_fmt(m)))
               for m, lbl in _send_fmt_labels().items()] if self._fmt_expanded else []),
            ft.Divider(height=1, color="#4b4c4f"),
            toggle_row(_T("steam.show_pinyin", default="Show Pinyin"), _T("steam.tip_show_pinyin", default="Reading line above originals in this chat"),
                       self._show_pinyin, self._set_pinyin),
            *([check_row(lbl, val, cb)
               for lbl, val, cb in (
                   (_T("steam.reading_zh", default="Chinese pinyin"), self._read_zh, self._set_read_zh),
                   (_T("steam.reading_ja", default="Japanese romaji"), self._read_ja, self._set_read_ja),
                   (_T("steam.reading_ko", default="Korean romaja"), self._read_ko, self._set_read_ko),
                   (_T("steam.reading_latin", default="Other languages (Latin)"), self._read_latin, self._set_read_latin),
               )] if self._show_pinyin else []),
            toggle_row(_T("steam.grouped_pinyin", default="Grouped Pinyin"), _T("steam.tip_grouped", default="Whole words instead of per-syllable"),
                       self._pinyin_grouped, self._set_pinyin_grouped),
            toggle_row(_T("steam.show_original", default="Show original text"), _T("steam.tip_show_original", default="The untranslated line above the translation"),
                       self._show_original, self._set_show_original),
            toggle_row(_T("steam.translate_mine", default="Translate my messages"), _T("steam.tip_translate_mine", default="Off shows your originals only — no pinyin or translation lines"),
                       self._tr_outgoing, self._set_tr_outgoing),
            toggle_row(_T("steam.translate_theirs", default="Translate their messages"), _T("steam.tip_translate_theirs", default="Off shows their originals only"),
                       self._tr_incoming, self._set_tr_incoming),
            ft.Divider(height=1, color="#4b4c4f"),
            btn(_T("steam.reload_history", default="Reload chat history"), lambda e: self._reload_chat(),
                tip=_T("steam.tip_reload", default="Re-fetch this chat from Steam and redraw it with the current settings.")),
            btn(_T("steam.retranslate", default="Retranslate history"), lambda e: self._retranslate_prompt(),
                tip=_T("steam.tip_retranslate", default="Redo translations")),
        ], spacing=1, tight=True)
        # Never taller than the window: cap + scroll (the "menu is cut off"
        # bug). Sum the REAL control heights — a rough rows*33 estimate set a
        # too-tall fixed height and left blank padding at the bottom.
        controls = self._settings_panel.content.controls
        est = sum((getattr(c, "height", None) or 22) for c in controls)
        est += max(0, len(controls) - 1) * 1 + 20   # column spacing + padding
        avail = int(getattr(self.page, "height", 0) or 760) - 110
        if avail > 220 and est > avail:
            self._settings_panel.content.scroll = ft.ScrollMode.AUTO
            self._settings_panel.height = avail
            self._settings_panel.padding = ft.padding.only(
                left=8, top=8, bottom=8, right=22)
        else:
            self._settings_panel.content.scroll = None
            self._settings_panel.height = None
            self._settings_panel.padding = 8


    def _set_pinyin(self, v) -> None:
        self._show_pinyin = bool(v)
        self._pinyin_from_prefs = True
        self._save_prefs()
        self._rerender_chat()

        if self._settings_panel.visible:
            self._build_settings_panel()
            with contextlib.suppress(Exception):
                self._settings_panel.update()
    def _set_show_original(self, v) -> None:
        self._show_original = bool(v)
        self._save_prefs()
        self._rerender_chat()

        if self._settings_panel.visible:
            self._build_settings_panel()
            with contextlib.suppress(Exception):
                self._settings_panel.update()
    def _translator_pill_label(self) -> str:
        cb = self.translator_label
        if callable(cb):
            with contextlib.suppress(Exception):
                return str(cb())
        return self._tr_provider or "Bing"

    def _set_tr_provider(self, prov: str) -> None:
        self._tr_provider = prov
        self._save_prefs()
        self._notice("Applies to new messages — Retranslate history converts this chat.")
        if self._settings_panel.visible:
            self._build_settings_panel()
            with contextlib.suppress(Exception):
                self._settings_panel.update()

    def _toggle_fmt_expanded(self) -> None:
        self._fmt_expanded = not self._fmt_expanded
        if self._settings_panel.visible:
            self._build_settings_panel()
            with contextlib.suppress(Exception):
                self._settings_panel.update()

    def _set_read_flag(self, name: str, v) -> None:
        setattr(self, name, bool(v))
        self._save_prefs()
        self._rerender_chat()
        if self._settings_panel.visible:
            self._build_settings_panel()
            with contextlib.suppress(Exception):
                self._settings_panel.update()

    def _set_pinyin_grouped(self, v) -> None:
        self._set_read_flag("_pinyin_grouped", v)

    def _set_read_zh(self, v) -> None:
        self._set_read_flag("_read_zh", v)

    def _set_read_ja(self, v) -> None:
        self._set_read_flag("_read_ja", v)

    def _set_read_ko(self, v) -> None:
        self._set_read_flag("_read_ko", v)

    def _set_read_latin(self, v) -> None:
        self._set_read_flag("_read_latin", v)

    def _set_send_fmt(self, fmt: str) -> None:
        self._send_fmt = fmt
        self._save_prefs()
        if self._settings_panel.visible:
            self._build_settings_panel()
            with contextlib.suppress(Exception):
                self._settings_panel.update()

    def _set_tr_incoming(self, v) -> None:
        self._tr_incoming = bool(v)
        self._save_prefs()
        self._rerender_chat()

        if self._settings_panel.visible:
            self._build_settings_panel()
            with contextlib.suppress(Exception):
                self._settings_panel.update()
    def _set_tr_outgoing(self, e) -> None:
        # accepts a plain bool (settings pill) or a switch event
        ctrl = getattr(e, "control", None)
        self._tr_outgoing = bool(ctrl.value) if ctrl is not None else bool(e)
        self._save_prefs()
        self._rerender_chat()   # so already-shown own messages reflect the toggle
        if self._settings_panel.visible:
            self._build_settings_panel()
            with contextlib.suppress(Exception):
                self._settings_panel.update()

    def _rerender_chat(self) -> None:
        """Restyle IN PLACE from the blocks already in memory — used by the display
        toggles (pinyin / originals / translations). No helper round-trip, so the
        chat doesn't visibly unload and repost anything; order can't change."""
        if not self._active or not self._hist_blocks:
            return
        was_following = self._following
        blocks = list(self._hist_blocks)
        self._messages.controls.clear()
        self._last_block = None
        for b in blocks:
            b.pop("_ctrl", None)               # rebuilt by the body builder below
            self._messages.controls.append(self._block_control(b))
        if self.page:
            with contextlib.suppress(Exception):
                self.page.update()
            if was_following:
                # the rebuild fires on_scroll events that can flip _following off
                # mid-flight (the fast-toggle bug) — restore and re-anchor AFTER
                # the client re-lays-out the changed content
                self.page.run_task(self._anchor_end)
            self.page.run_task(self._fill_translations, blocks, self._open_seq)

    async def _fill_translations(self, blocks: list, seq: int) -> None:
        # cache hits are instant; only genuinely new text would call the API
        await asyncio.gather(*(self._translate_block(b, seq) for b in blocks))
        self._save_cache()

    def _reload_chat(self) -> None:
        # Full reload from the helper (re-reads + re-groups server history) —
        # only the explicit "Clean up / re-render chat" button does this.
        if self._active:
            with contextlib.suppress(Exception):
                self.page.run_task(self._cmd, {"cmd": "open", "acct": self._active})

    def _retranslate_prompt(self) -> None:
        chars = 0
        for b in self._hist_blocks:
            t = _URL_RE.sub("", b.get("text", "") or "").strip()
            if t and self._needs_tr(t):
                chars += len(t)
        if not chars:
            with contextlib.suppress(Exception):
                self.page.open(ft.SnackBar(ft.Text(_T("steam.retr_none", default="Nothing here needs retranslating."))))
            return
        paid = True
        cb = getattr(self, "translator_is_paid", None)
        if callable(cb):
            with contextlib.suppress(Exception):
                paid = bool(cb())
        if not paid:
            # free model — nothing to warn about, just do it
            with contextlib.suppress(Exception):
                self.page.open(ft.SnackBar(ft.Text(_T("steam.retr_free", default="Retranslating…"))))
            self.page.run_task(self._retranslate_all)
            return
        dlg = ft.AlertDialog(
            modal=True,
            title=ft.Text(_T("steam.retr_q", default="Retranslate history?"), size=15),
            content=ft.Text(_T(
                "steam.retr_body", chars=chars,
                default=f"This will re-send ≈{chars} characters to your API "
                        f"translator, replacing the cached translations for this "
                        f"chat. Tip: pick a free model (e.g. Bing) under "
                        f"Translator to do this at no cost."), size=13),
            actions=[
                ft.TextButton(_T("steam.cancel", default="Cancel"), on_click=lambda e: self.page.close(dlg)),
                ft.TextButton(_T("steam.retranslate", default="Retranslate"),
                              on_click=lambda e: (self.page.close(dlg),
                                                  self.page.run_task(self._retranslate_all))),
            ])
        with contextlib.suppress(Exception):
            self.page.open(dlg)

    async def _retranslate_all(self) -> None:
        seq = self._open_seq
        await asyncio.gather(*(self._translate_block(b, seq, force=True)
                               for b in list(self._hist_blocks)))
        self._save_cache()
        with contextlib.suppress(Exception):
            self.page.open(ft.SnackBar(ft.Text("History retranslated."), duration=1600))

    # ── input right-click menu (paste/copy/cut/clear) ────────────────────────
    def _show_input_menu(self, e) -> None:
        def item(label, cb):
            def handler(ev):
                self._hide_input_menu()
                cb()
            return ft.Container(content=ft.Text(label, size=13, color=_TEXT_PRIMARY),
                                padding=ft.padding.symmetric(horizontal=10, vertical=7),
                                border_radius=6, ink=True, on_click=handler)

        def paste():
            with contextlib.suppress(Exception):
                clip = self.page.get_clipboard()
                if clip:
                    self._entry.value = (self._entry.value or "") + clip
                    self._entry.update()

        def copy_all():
            with contextlib.suppress(Exception):
                self.page.set_clipboard(self._entry.value or "")

        def cut_all():
            copy_all()
            self._entry.value = ""
            with contextlib.suppress(Exception):
                self._entry.update()

        def clear():
            self._entry.value = ""
            with contextlib.suppress(Exception):
                self._entry.update()

        self._input_menu.content = ft.Column([
            item("Paste", paste),
            item("Copy all", copy_all),
            item("Cut all", cut_all),
            item("Clear", clear),
        ], spacing=1, tight=True)
        gx = float(getattr(e, "global_x", 0) or 300)
        self._input_menu.left = max(240.0, gx - 8)
        self._input_menu.visible = True
        self._ctx_backdrop.visible = True    # reuse the chat-area backdrop to dismiss
        if self.page:
            self.page.update()

    def _hide_input_menu(self) -> None:
        if self._input_menu.visible:
            self._input_menu.visible = False
            self._ctx_backdrop.visible = False
            if self.page:
                self.page.update()

    # ── image send: paste (Ctrl+V), file picker, attachment chip ─────────────
    def paste_image(self) -> bool:
        """Called on Ctrl+V (from the app's keyboard handler) while the Steam tab
        is active. If the clipboard holds an IMAGE, queue it as an attachment and
        return True; text clipboards return False so normal paste proceeds."""
        try:
            from PIL import ImageGrab
            grab = ImageGrab.grabclipboard()
        except Exception:
            return False
        path = None
        try:
            if grab is None:
                return False
            outbox = _BRIDGE_ROOT / "steam_bridge" / "outbox"
            outbox.mkdir(parents=True, exist_ok=True)
            if isinstance(grab, list):     # copied file(s) — take the first image
                for p in grab:
                    if str(p).lower().endswith((".png", ".jpg", ".jpeg", ".gif", ".webp")):
                        path = str(p)
                        break
                if not path:
                    return False
            else:                          # raw bitmap from the clipboard
                sub = outbox / str(int(time.time()))
                sub.mkdir(parents=True, exist_ok=True)
                path = str(sub / "image.png")   # Steam names pasted uploads image.png
                grab.save(path, "PNG")
        except Exception:
            return False
        self._attach_image(path)
        return True

    def _pick_image(self) -> None:
        with contextlib.suppress(Exception):
            self._file_picker.pick_files(
                dialog_title="Send an image",
                allow_multiple=False,
                allowed_extensions=["png", "jpg", "jpeg", "gif", "webp"])

    def _on_pick_file(self, e) -> None:
        with contextlib.suppress(Exception):
            if e.files:
                self._attach_image(e.files[0].path)

    def _attach_image(self, path: str) -> None:
        # Steam-style upload dialog: preview, filename, Upload, Tag as Spoiler.
        name = Path(path).name
        spoiler = ft.Checkbox(label=_T("steam.spoiler", default="Tag as Spoiler"), value=False,
                              label_style=ft.TextStyle(size=13, color=_TEXT_FAINT))
        dlg = ft.AlertDialog(
            modal=False, bgcolor=_BG_MENU,
            content=ft.Column([
                ft.Image(src=path, width=380, height=280, fit=ft.ImageFit.CONTAIN,
                         border_radius=6),
                ft.Text(f"'{name}'", size=13, color=_TEXT_FAINT,
                        text_align=ft.TextAlign.CENTER),
                ft.ElevatedButton(
                    _T("steam.upload", default="Upload"),
                    bgcolor="#3d6dcc", color="#ffffff", width=380,
                    on_click=lambda e: self.page.run_task(
                        self._do_upload, path, bool(spoiler.value), dlg)),
                spoiler,
            ], tight=True, spacing=10,
               horizontal_alignment=ft.CrossAxisAlignment.CENTER),
        )
        with contextlib.suppress(Exception):
            self.page.open(dlg)

    async def _do_upload(self, path: str, spoiler: bool, dlg) -> None:
        with contextlib.suppress(Exception):
            self.page.close(dlg)
        if not self._active:
            return
        await self._render_live({"from_me": True, "name": self._own_name,
                                 "avatar": self._own_avatar, "text": "",
                                 "images": [path], "stickers": []})
        await self._cmd({"cmd": "send_image", "acct": self._active,
                         "path": path, "spoiler": bool(spoiler)})

    def _not_yet(self, label: str) -> None:
        with contextlib.suppress(Exception):
            self.page.open(ft.SnackBar(ft.Text(f"{label} aren't wired up yet in the beta.")))

    def _launch(self, url: str) -> None:
        with contextlib.suppress(Exception):
            self.page.launch_url(url)

    def _open_profile(self, acct: int) -> None:
        if acct:
            self._launch(f"https://steamcommunity.com/profiles/{acct + _STEAMID64_BASE}")

    def _on_search(self, q: str) -> None:
        self._filter = (q or "").strip().lower()
        self._rebuild_friends()
        if self.page:
            with contextlib.suppress(Exception):
                self._friends_list.update()

    # ── right-click context menu ─────────────────────────────────────────────
    def _menu_actions(self, f: dict) -> list:
        acct = int(f["acct"])
        sid = acct + _STEAMID64_BASE
        acts = [("Open chat", lambda: self.page.run_task(self._open, acct)),
                ("View Steam profile", lambda: self._open_profile(acct))]
        if f.get("fav"):
            acts.append(("Remove from Favorites",
                         lambda: self.page.run_task(self._set_favorite, acct, False)))
        else:
            acts.append(("Add to Favorites",
                         lambda: self.page.run_task(self._set_favorite, acct, True)))
        if f.get("ingame") and f.get("appid"):
            ap = f["appid"]
            acts.append(("Game store page",
                         lambda: self._launch(f"https://store.steampowered.com/app/{ap}")))
            acts.append(("Community hub",
                         lambda: self._launch(f"https://steamcommunity.com/app/{ap}")))
        acts.append(("Copy profile link",
                     lambda: self._copy(f"https://steamcommunity.com/profiles/{sid}")))
        return acts

    async def _set_favorite(self, acct: int, on: bool) -> None:
        await self._cmd({"cmd": "favorite", "acct": int(acct), "on": bool(on)})

    def _copy(self, text: str) -> None:
        with contextlib.suppress(Exception):
            self.page.set_clipboard(text)

    def _show_ctx(self, e, f: dict) -> None:
        rows = []
        for label, cb in self._menu_actions(f):
            def make(cb):
                def handler(ev):
                    self._hide_ctx()
                    cb()
                return handler
            rows.append(ft.Container(
                content=ft.Text(label, size=13, color=_TEXT_PRIMARY),
                padding=ft.padding.symmetric(horizontal=10, vertical=7),
                border_radius=6, ink=True, on_click=make(cb)))
        self._ctx_menu.content = ft.Column(rows, spacing=1, tight=True)
        gx = float(getattr(e, "global_x", 0) or getattr(e, "local_x", 0) or 60)
        gy = float(getattr(e, "global_y", 0) or getattr(e, "local_y", 0) or 60)
        self._ctx_menu.left = max(4.0, gx - self._ctx_offset_x - 8)
        self._ctx_menu.top = max(40.0, gy - 44)
        self._ctx_menu.visible = True
        self._ctx_backdrop.visible = True
        if self.page:
            self.page.update()

    def _hide_ctx(self) -> None:
        if self._ctx_menu.visible or self._ctx_backdrop.visible or self._input_menu.visible:
            self._ctx_menu.visible = False
            self._ctx_backdrop.visible = False
            self._input_menu.visible = False
            if self.page:
                self.page.update()

    # ── friends list ─────────────────────────────────────────────────────────
    def _status_badges(self, f: dict) -> list:
        # every badge matches the name's status color, like real Steam (green
        # in-game, blue online, FADED blue away/snooze)
        out = []
        flags = int(f.get("flags", 0))
        state, ingame = int(f.get("state", 0)), bool(f.get("ingame"))
        col = _name_color(state, ingame)
        if state in (3, 4):                      # away / snooze — Steam's zZZ
            out.append(ft.Text("zᶻᶻ", size=10, weight=ft.FontWeight.BOLD, color=col))
        if flags & _FLAG_VR:                      # small VR pill like real Steam
            out.append(ft.Container(
                content=ft.Text("VR", size=7.5, weight=ft.FontWeight.BOLD, color="#1b1c1e"),
                bgcolor=col, border_radius=2,
                padding=ft.padding.only(left=2, right=2, top=0, bottom=0)))
        if flags & _FLAG_MOBILE:
            out.append(ft.Icon(ft.Icons.SMARTPHONE, size=12, color=col))
        return out

    def _unread_badge(self, count: int) -> ft.Control:
        return ft.Container(
            content=ft.Text(str(count), size=11, weight=ft.FontWeight.BOLD, color="#1b1c1e"),
            bgcolor="#e0b400", border_radius=4,
            padding=ft.padding.symmetric(horizontal=6, vertical=1),
            shadow=ft.BoxShadow(blur_radius=10, spread_radius=1, color="#99e0b400"))

    def _game_icon(self, url: str, size: int = 20) -> ft.Control:
        if url:
            return ft.Image(src=url, width=size, height=size, border_radius=4, fit=ft.ImageFit.COVER)
        return ft.Container(width=size, height=size, border_radius=4, bgcolor="#3a3b3e")

    def _status_menu(self) -> ft.Control:
        # name + caret; opens the Steam status menu (labels/descriptions match
        # the real client, localized)
        def item(label, desc, cb, checked=None):
            lines = [ft.Row([
                *([ft.Icon(ft.Icons.CHECK_BOX if checked
                           else ft.Icons.CHECK_BOX_OUTLINE_BLANK,
                           size=15, color=_TOGGLE_ON if checked else _TEXT_FAINT)]
                  if checked is not None else []),
                ft.Text(label, size=13, color=_TEXT_PRIMARY),
            ], spacing=6, tight=True)]
            if desc:
                lines.append(ft.Text(desc, size=10.5, color=_TEXT_FAINT))
            return ft.PopupMenuItem(
                height=46 if desc else 32,
                content=ft.Column(lines, spacing=1, tight=True),
                on_click=cb)

        def st(s):
            return lambda e: self.page.run_task(self._set_status, s)

        return ft.PopupMenuButton(
            content=ft.Row([
                ft.Text(self._own_name or "Me", size=14, weight=ft.FontWeight.BOLD,
                        color=_C_INGAME if self._own_ingame
                        else _name_color(self._own_state, False),
                        max_lines=1, overflow=ft.TextOverflow.ELLIPSIS),
                ft.Icon(ft.Icons.ARROW_DROP_DOWN, size=16, color=_TEXT_FAINT),
            ], spacing=0, tight=True),
            tooltip=_T("steam.set_status", default="Set status"),
            items=[
                item(_T("steam.status_online", default="Online"), "", st(1)),
                item(_T("steam.status_away", default="Away"), "", st(3)),
                item(_T("steam.status_invisible", default="Invisible"),
                     _T("steam.status_invisible_desc",
                        default="Appear offline, but you can still chat"), st(7)),
                item(_T("steam.status_offline", default="Offline"),
                     _T("steam.status_offline_desc",
                        default="Sign out of Friends & Chat"), st(0)),
                ft.PopupMenuItem(height=1),
                item(_T("steam.dnd", default="Do Not Disturb"),
                     _T("steam.dnd_desc", default="Disables all chat notifications"),
                     lambda e: self._toggle_dnd(), checked=self._dnd),
                ft.PopupMenuItem(height=1),
                item(_T("steam.edit_profile", default="Edit Profile Name"), "",
                     lambda e: self._open_profile_edit()),
                item(_T("steam.view_profile", default="View my Steam profile"), "",
                     lambda e: self._open_profile(self._own)),
            ])

    def _toggle_dnd(self) -> None:
        self._dnd = not self._dnd
        self._save_prefs()
        self._rebuild_tabs()          # DND suppresses the unread dots
        self._update_own_header()
        if self.page:
            with contextlib.suppress(Exception):
                self.page.update()

    def _open_profile_edit(self) -> None:
        if self._own:
            self._launch("https://steamcommunity.com/profiles/"
                         f"{self._own + _STEAMID64_BASE}/edit/info")

    def _update_own_header(self) -> None:
        # Matches real Steam: in-game → green game name (name goes green via
        # _status_menu using _name_color); invisible → crossed eye BEFORE the
        # status, kept even while in-game (green name + eye + game).
        if self._own_ingame:
            parts = []
            if self._own_invisible:
                parts.append(ft.Icon(ft.Icons.VISIBILITY_OFF, size=13, color=_C_INGAME))
            parts.append(ft.Text(self._own_game or "In-Game", size=11, color=_C_INGAME,
                                 max_lines=1, overflow=ft.TextOverflow.ELLIPSIS))
            if self._own_state in (0, 3, 4):
                parts.append(ft.Text(_state_labels().get(self._own_state, ""),
                                     size=11, color=_C_AWAY))
            status_row = ft.Row(parts, spacing=4, tight=True)
        elif self._own_invisible:
            status_row = ft.Row([ft.Icon(ft.Icons.VISIBILITY_OFF, size=13, color=_TEXT_FAINT),
                                 ft.Text(_T("steam.state_invisible", default="Invisible"), size=11, color=_TEXT_FAINT)],
                                spacing=4, tight=True)
        else:
            status_row = ft.Text(
                _state_labels().get(self._own_state,
                                    _state_labels()[0] if not self._own_state
                                    else _state_labels()[1]),
                size=11, color=_name_color(self._own_state, False))
        children = [
            ft.GestureDetector(content=_avatar(self._own_avatar, 32),
                               on_tap=lambda e: self._open_profile(self._own)),
            ft.Column([self._status_menu(), status_row], spacing=0, tight=True, expand=True),
        ]
        if self._own_invites > 0:
            children.append(ft.Container(
                content=ft.Row([
                    ft.Icon(ft.Icons.PERSON_ADD_ALT_1, size=17, color="#d96a6a"),
                    ft.Text(str(self._own_invites), size=12, weight=ft.FontWeight.BOLD,
                            color="#d96a6a"),
                ], spacing=3, tight=True),
                tooltip=f"{self._own_invites} friend requests (opens Steam)", ink=True,
                border_radius=6, padding=ft.padding.all(4),
                on_click=lambda e: self._launch(
                    f"https://steamcommunity.com/profiles/{self._own + _STEAMID64_BASE}/friends/pending")))
        self._own_header.content = ft.Row(
            children, spacing=8, vertical_alignment=ft.CrossAxisAlignment.CENTER)
        if self.page:
            with contextlib.suppress(Exception):
                self._own_header.update()

    async def _set_status(self, state: int) -> None:
        self._own_state = int(state)
        self._own_invisible = (int(state) == 7)
        self._update_own_header()
        if self.page:
            with contextlib.suppress(Exception):
                self.page.update()
        await self._cmd({"cmd": "status", "state": int(state)})

    def _friend_row(self, f: dict, *, lead_icon: str = "") -> ft.Control:
        acct = int(f["acct"])
        state, ingame = int(f.get("state", 0)), bool(f.get("ingame"))
        if ingame:
            sub, sub_color = (f.get("game") or "In-Game"), _C_INGAME
        elif state:
            sub, sub_color = _state_labels().get(state, "Online"), _TEXT_FAINT
        else:
            sub, sub_color = "Offline", _TEXT_FAINT
        name_row = ft.Row([
            ft.Text(_disp_name(f) or "Steam friend", size=13, weight=ft.FontWeight.W_500,
                    color=_name_color(state, ingame), max_lines=1,
                    overflow=ft.TextOverflow.ELLIPSIS),
            *([ft.Container(
                    content=ft.Text("*", size=15, color=_TEXT_FAINT),
                    margin=ft.margin.only(left=-2, bottom=5),
                    tooltip=f.get("real") or f.get("name") or "")]
              if f.get("nick") else []),
            *self._status_badges(f),
        ], spacing=4, tight=True)
        left = []
        if lead_icon:
            left.append(self._game_icon(lead_icon, 22))
        left.append(_avatar(f.get("avatar", ""), 30))
        children = left + [
            ft.Column([name_row, ft.Text(sub, size=11, color=sub_color, max_lines=1,
                                         overflow=ft.TextOverflow.ELLIPSIS)],
                      spacing=0, tight=True, expand=True),
        ]
        if int(f.get("unread", 0)) > 0:
            children.append(self._unread_badge(int(f["unread"])))
        body = ft.Row(children, spacing=8, vertical_alignment=ft.CrossAxisAlignment.CENTER)
        gd = ft.GestureDetector(
            content=body, expand=True,
            on_tap=lambda e, a=acct: self.page.run_task(self._open, a),
            on_secondary_tap_down=lambda e, fr=f: self._show_ctx(e, fr))
        # Highlight on HOVER (like the real Steam friends list) rather than pinning a
        # permanent highlight on the open chat's friend.
        disp = _disp_name(f)
        return ft.Container(
            content=gd, padding=ft.padding.symmetric(horizontal=8, vertical=4),
            # tooltip ONLY when the name will actually ellipsize (the always-on
            # instant tooltips annoyed on every hover)
            tooltip=(disp if _est_text_w(disp) > 132 else None),
            border_radius=6, bgcolor=ft.Colors.TRANSPARENT, on_hover=self._row_hover)

    def _row_hover(self, e) -> None:
        e.control.bgcolor = "#34363b" if e.data == "true" else ft.Colors.TRANSPARENT
        with contextlib.suppress(Exception):
            e.control.update()

    def _skeleton_rows(self) -> list:
        sk = "#3234384d"
        rows = []
        for _ in range(10):
            rows.append(ft.Container(
                content=ft.Row([
                    ft.Container(width=32, height=32, border_radius=6, bgcolor=sk),
                    ft.Column([
                        ft.Container(width=110, height=11, border_radius=4, bgcolor=sk),
                        ft.Container(width=68, height=9, border_radius=4, bgcolor="#2c2d3033"),
                    ], spacing=5, tight=True),
                ], spacing=10, vertical_alignment=ft.CrossAxisAlignment.CENTER),
                padding=ft.padding.symmetric(horizontal=8, vertical=7)))
        return rows

    def _section_header(self, label: str, n: int, color: str = _SECTION,
                        collapsed: bool = False) -> ft.Control:
        # Steam parity: the chevron only appears while hovering the header,
        # and the member count only while the section is collapsed.
        chev = ft.Icon(ft.Icons.CHEVRON_RIGHT if collapsed else ft.Icons.EXPAND_MORE,
                       size=14, color=color, opacity=0, animate_opacity=120)

        def _hov(e, c=chev):
            c.opacity = 1 if e.data == "true" else 0
            with contextlib.suppress(Exception):
                c.update()

        return ft.Container(
            content=ft.Row([
                chev,
                ft.Text(f"{label}  {n}" if (collapsed and n) else label, size=11,
                        weight=ft.FontWeight.BOLD, color=color),
            ], spacing=1, tight=True, vertical_alignment=ft.CrossAxisAlignment.CENTER),
            padding=ft.padding.only(left=4, top=10, bottom=3), ink=True,
            on_hover=_hov,
            on_click=lambda e, l=label: self._toggle_section(l))

    def _toggle_section(self, label: str) -> None:
        if label in self._collapsed_sections:
            self._collapsed_sections.discard(label)
        else:
            self._collapsed_sections.add(label)
        self._rebuild_friends()
        if self.page:
            with contextlib.suppress(Exception):
                self._friends_list.update()

    def _toggle_game(self, game: str) -> None:
        if game in self._expanded_games:
            self._expanded_games.discard(game)
        else:
            self._expanded_games.add(game)
        self._rebuild_friends()
        if self.page:
            with contextlib.suppress(Exception):
                self._friends_list.update()

    def _game_group(self, game: str, members: list) -> ft.Control:
        icon = next((f.get("icon") for f in members if f.get("icon")), "")
        collapsed = game in self._collapsed_sections
        _chev = ft.Icon(ft.Icons.CHEVRON_RIGHT if collapsed else ft.Icons.EXPAND_MORE,
                        size=14, color=_C_INGAME, opacity=0, animate_opacity=120)

        def _ghov(e, c=_chev):
            c.opacity = 1 if e.data == "true" else 0
            with contextlib.suppress(Exception):
                c.update()

        header = ft.Container(
            content=ft.Row([
                _chev,
                self._game_icon(icon, 22),
                ft.Text(f"{game}  {len(members)}", size=12, weight=ft.FontWeight.BOLD,
                        color=_C_INGAME),
            ], spacing=6, tight=True, vertical_alignment=ft.CrossAxisAlignment.CENTER),
            padding=ft.padding.only(left=4, top=10, bottom=3), ink=True,
            on_hover=_ghov,
            on_click=lambda e, g=game: self._toggle_section(g))
        if collapsed:
            active_rows = [self._friend_row(f) for f in members if int(f["acct"]) == self._active]
            if not active_rows:
                return header
            box = ft.Container(content=ft.Column(active_rows, spacing=1, tight=True),
                               margin=ft.margin.only(left=18), padding=ft.padding.only(left=8),
                               border=ft.border.only(left=ft.BorderSide(1.5, "#54555a")))
            return ft.Column([header, box], spacing=0, tight=True)
        expanded = game in self._expanded_games
        shown = members if expanded else members[:2]
        rows = [self._friend_row(f) for f in shown]
        rest = len(members) - len(shown)
        if rest > 0:
            rows.append(ft.Container(
                content=ft.Row([
                    ft.Text(f"+{rest}", size=12, weight=ft.FontWeight.BOLD, color=_C_INGAME),
                    ft.Text(f"more playing", size=12, color=_TEXT_FAINT,
                            max_lines=1, overflow=ft.TextOverflow.ELLIPSIS),
                ], spacing=8, tight=True),
                padding=ft.padding.symmetric(horizontal=8, vertical=4), ink=True,
                on_click=lambda e, g=game: self._toggle_game(g)))
        elif expanded and len(members) > 2:
            rows.append(ft.Container(
                content=ft.Text("Show less", size=12, color=_TEXT_FAINT),
                padding=ft.padding.symmetric(horizontal=8, vertical=4), ink=True,
                on_click=lambda e, g=game: self._toggle_game(g)))
        members_box = ft.Container(
            content=ft.Column(rows, spacing=1, tight=True),
            margin=ft.margin.only(left=18), padding=ft.padding.only(left=8),
            border=ft.border.only(left=ft.BorderSide(1.5, "#54555a")))
        return ft.Column([header, members_box], spacing=0, tight=True)

    def _rebuild_friends(self) -> None:
        items = list(self._friends.values())
        if self._filter:
            q = self._filter
            items = [f for f in items
                     if q in self._search_index.get(int(f["acct"]), (f.get("name") or "").lower())]

        def sk(f):
            return (0 if f.get("ingame") else (1 if f.get("state") else 2),
                    (f.get("name") or "").lower())

        assigned: set[int] = set()
        # Favorites get their own pinned section at the very top (Steam-style),
        # with full name + status rows — so they're taken out of every other
        # section here to avoid duplication.
        favs = sorted((f for f in items if f.get("fav")), key=sk)
        assigned |= {int(f["acct"]) for f in favs}
        unread = sorted((f for f in items
                         if int(f.get("unread", 0)) > 0 and int(f["acct"]) not in assigned),
                        key=lambda f: -(f.get("last_chat") or 0))
        assigned |= {int(f["acct"]) for f in unread}
        recent = [f for f in sorted((f for f in items if f.get("last_chat")),
                                    key=lambda f: -(f.get("last_chat") or 0))
                  if int(f["acct"]) not in assigned][:4]
        assigned |= {int(f["acct"]) for f in recent}

        # Favorites are pinned in their own top section (assigned above), so the
        # remaining friends fall into their normal status/game sections.
        cats, ingame, online, offline = {}, [], [], []
        for f in items:
            if int(f["acct"]) in assigned and not self._filter:
                continue
            if f.get("groups"):
                cats.setdefault(f["groups"][0], []).append(f)
            elif f.get("ingame"):
                ingame.append(f)
            elif f.get("state"):
                online.append(f)
            else:
                offline.append(f)

        self._friends_list.controls.clear()
        C = self._friends_list.controls

        def add_section(label, rows, presorted=False, color=_SECTION):
            if not rows:
                return
            collapsed = label in self._collapsed_sections
            C.append(self._section_header(label, len(rows), color, collapsed))
            for f in (rows if presorted else sorted(rows, key=sk)):
                # keep the active chat's friend visible even when collapsed
                if not collapsed or int(f["acct"]) == self._active:
                    C.append(self._friend_row(f))

        if not self._filter:
            if favs:
                _fc = "FAVORITES" in self._collapsed_sections
                C.append(self._section_header("FAVORITES", len(favs), _ACCENT, _fc))
                for f in favs:
                    if not _fc or int(f["acct"]) == self._active:
                        C.append(self._friend_row(f))
            if unread:
                _uc = "UNREAD MESSAGES" in self._collapsed_sections
                C.append(self._section_header("UNREAD MESSAGES", 0, "#e0b400", _uc))
                for f in sorted(unread, key=sk):
                    if not _uc or int(f["acct"]) == self._active:
                        C.append(self._friend_row(f))
            add_section("RECENT", recent, presorted=True)
        for name in sorted(cats):
            add_section(name.upper(), cats[name])
        # In-game: 2+ friends in a game -> its own group; solo games -> "Other Games".
        # Groups sorted by how many friends are in each game (most first), like Steam.
        by_game: dict[str, list] = {}
        for f in ingame:
            by_game.setdefault(f.get("game") or "In-Game", []).append(f)
        others = []
        multi = [(g, r) for g, r in by_game.items() if len(r) >= 2]
        multi.sort(key=lambda gr: (-len(gr[1]), gr[0].lower()))
        for game, rows in multi:
            C.append(self._game_group(game, sorted(rows, key=sk)))
        for game, rows in by_game.items():
            if len(rows) < 2:
                others += rows
        if others:
            _oc = "OTHER GAMES" in self._collapsed_sections
            C.append(self._section_header("OTHER GAMES", len(others), _SECTION, _oc))
            for f in sorted(others, key=sk):
                if not _oc or int(f["acct"]) == self._active:
                    C.append(self._friend_row(f, lead_icon=f.get("icon", "")))
        add_section("ONLINE", online)
        add_section("OFFLINE", offline)

    def _set_chat_head(self, f: dict | None) -> None:
        # The tabs now serve as the header (see _tab_chip); nothing to do here.
        return

    # ── chat tabs (act as the header) ────────────────────────────────────────
    def _tab_chip(self, acct: int) -> ft.Control:
        f = self._friends.get(acct, {})
        if not f and acct and acct == self._own:
            # chat with yourself (saved messages) — show your own identity
            f = {"name": self._own_name, "avatar": self._own_avatar,
                 "state": self._own_state, "ingame": self._own_ingame,
                 "game": self._own_game}
        active = (acct == self._active)
        state, ingame = int(f.get("state", 0)), bool(f.get("ingame"))
        sub = (f.get("game") or "In-Game") if ingame else _state_labels().get(state, "Offline")
        # Unread dot: a background tab whose chat has a newer message than we've
        # shown gets Steam's amber dot (cleared by opening the tab).
        unread = (not active and not self._dnd
                  and int(f.get("last_chat") or 0) > self._seen_chat_ts.get(acct, 1 << 62))
        row_items = [
            _avatar(f.get("avatar", ""), 26),
            ft.Column([
                ft.Row([
                    ft.Text(_disp_name(f) or "Chat", size=13,
                            color=_TEXT_PRIMARY if active else _TEXT_FAINT,
                            max_lines=1, overflow=ft.TextOverflow.ELLIPSIS),
                    *([ft.Container(
                            content=ft.Text("*", size=13, color=_TEXT_FAINT),
                            margin=ft.margin.only(left=-1, bottom=4),
                            tooltip=f.get("real") or f.get("name") or "")]
                      if f.get("nick") else []),
                ], spacing=2, tight=True),
                ft.Text(sub, size=10, color=_name_color(state, ingame) if active else _TEXT_FAINT,
                        max_lines=1, overflow=ft.TextOverflow.ELLIPSIS),
            ], spacing=0, tight=True),
        ]
        if unread:
            row_items.append(ft.Container(width=8, height=8, border_radius=4,
                                          bgcolor="#e0b400"))
        row_items.append(
            ft.IconButton(ft.Icons.CLOSE, icon_size=13, icon_color=_TEXT_FAINT,
                          tooltip="Close", width=20, height=20,
                          on_click=lambda e, a=acct: self._close_tab(a),
                          style=ft.ButtonStyle(padding=ft.padding.all(0))))
        chip = ft.Container(
            content=ft.Row(row_items, spacing=8, tight=True,
                           vertical_alignment=ft.CrossAxisAlignment.CENTER),
            padding=ft.padding.only(left=8, right=4, top=4, bottom=4),
            border_radius=8, bgcolor=_BG_SEL if active else _BG_MENU,
            on_click=lambda e, a=acct: self.page.run_task(self._open, a))
        # Right-click opens a small menu (Close tab / Close all), like Steam.
        return ft.GestureDetector(
            content=chip,
            on_secondary_tap_down=lambda e, a=acct: self._show_tab_menu(e, a))

    def _rebuild_tabs(self) -> None:
        self._tab_strip.controls = [self._tab_chip(a) for a in self._tabs]

    def _show_tab_menu(self, e, acct: int) -> None:
        rows = []
        for label, cb in (
            (_T("steam.close_tab", default="Close tab"),
             lambda a=acct: self._close_tab(a)),
            (_T("steam.close_tabs_right", default="Close tabs to the right"),
             lambda a=acct: self._close_tabs_right(a)),
            (_T("steam.close_all_tabs", default="Close all tabs"),
             self._close_all_tabs),
        ):
            def make(cb):
                def handler(ev):
                    self._hide_ctx()
                    cb()
                return handler
            rows.append(ft.Container(
                content=ft.Text(label, size=13, color=_TEXT_PRIMARY),
                padding=ft.padding.symmetric(horizontal=10, vertical=7),
                border_radius=6, ink=True, on_click=make(cb)))
        self._ctx_menu.content = ft.Column(rows, spacing=1, tight=True)
        gx = float(getattr(e, "global_x", 0) or getattr(e, "local_x", 0) or 60)
        gy = float(getattr(e, "global_y", 0) or getattr(e, "local_y", 0) or 60)
        self._ctx_menu.left = max(4.0, gx - self._ctx_offset_x - 8)
        self._ctx_menu.top = max(40.0, gy - 44)
        self._ctx_menu.visible = True
        self._ctx_backdrop.visible = True
        if self.page:
            self.page.update()

    async def _warm_tabs(self, active_acct: int) -> None:
        """Prefetch history for every restored background tab: the daemon
        streams each one and non-active histories land in _chat_cache, so the
        first click on any tab paints instantly. Ends by re-syncing the
        active chat (its identical history skips the repaint)."""
        await asyncio.sleep(2.0)
        warm = [a for a in self._tabs if a != active_acct]
        for a in warm:
            if not self._writer:
                return
            await self._cmd({"cmd": "open", "acct": a})
            await asyncio.sleep(0.8)
        if warm and self._active is not None and self._writer:
            await self._cmd({"cmd": "open", "acct": self._active})

    def _close_tabs_right(self, acct: int) -> None:
        if acct not in self._tabs:
            return
        idx = self._tabs.index(acct)
        removed = self._tabs[idx + 1:]
        if not removed:
            return
        self._tabs = self._tabs[:idx + 1]
        for a in removed:
            self._scroll_pos.pop(a, None)
            self._was_following.pop(a, None)
            self._render_fp.pop(a, None)
        if self._active in removed and self.page:
            self.page.run_task(self._open, acct)
        self._rebuild_tabs()
        self._save_prefs()
        if self.page:
            self.page.update()

    def _close_all_tabs(self) -> None:
        self._tabs = []
        self._scroll_pos.clear()
        self._was_following.clear()
        self._render_fp.clear()
        self._messages.controls.clear()
        self._last_block = None
        self._hist_blocks = []
        self._active = None
        self._set_chat_head(None)
        self._entry.disabled = True
        if self._module_on:
            self._show_state_overlay("idle")
        self._rebuild_tabs()
        self._save_prefs()
        if self.page:
            self.page.update()

    def _close_tab(self, acct: int) -> None:
        if acct in self._tabs:
            self._tabs.remove(acct)
        # a closed tab forgets its position — reopening jumps to the newest
        self._scroll_pos.pop(acct, None)
        self._was_following.pop(acct, None)
        if acct == self._active:
            # Clear the closed chat's content IMMEDIATELY — don't leave it on screen
            # while the next chat's history loads.
            self._messages.controls.clear()
            self._last_block = None
            self._hist_blocks = []
            if self._tabs:
                self.page.run_task(self._open, self._tabs[-1])
                return
            self._active = None
            self._set_chat_head(None)
            self._entry.disabled = True
            if self._module_on:
                self._show_state_overlay("idle")
        self._rebuild_tabs()
        self._save_prefs()
        if self.page:
            self.page.update()

    # ── messages ─────────────────────────────────────────────────────────────
    def _on_msg_scroll(self, e) -> None:
        with contextlib.suppress(Exception):
            self._max_scroll = e.max_scroll_extent or 0.0
            want = ((e.max_scroll_extent or 0) > 80
                    and e.pixels < (e.max_scroll_extent or 0) - 40)
            # While scrolled up, stop following new messages; resume at the bottom
            # or via the jump button. Scrolling only ever happens via _scroll_to_end.
            self._following = not want
            if self._active is not None:
                self._scroll_pos[self._active] = float(e.pixels or 0)
                self._was_following[self._active] = self._following
            if self._jump_btn.visible != want:
                self._jump_btn.visible = want
                self._jump_btn.update()

    def _scroll_to_end(self, duration: int = 80) -> None:
        with contextlib.suppress(Exception):
            self._messages.scroll_to(offset=-1, duration=max(1, duration))

    async def _anchor_end(self, pos: float | None = None) -> None:
        # After a rebuild the client needs a beat to lay out the new content —
        # scrolling immediately lands on stale geometry (the toggle-pushes-view-
        # off-the-end bug). Anchor twice across a short delay to be sure.
        # pos=None jumps to the end (fresh chat); a px value restores a kept
        # position (open tabs keep their spot, like real Steam). duration=1 and
        # the pre-anchor opacity=0 mean the scroll is never visible.
        try:
            for delay in (0.05, 0.2):
                await asyncio.sleep(delay)
                self._following = pos is None
                with contextlib.suppress(Exception):
                    self._messages.scroll_to(
                        offset=(-1 if pos is None else pos), duration=1)
        finally:
            if self._messages.opacity != 1:
                self._messages.opacity = 1
                with contextlib.suppress(Exception):
                    self._messages.update()

    def _jump_to_latest(self) -> None:
        with contextlib.suppress(Exception):
            self._following = True
            self._scroll_to_end(200)
            self._jump_btn.visible = False
            self._jump_btn.update()

    def _romanize(self, text: str) -> str:
        """Pinyin (Chinese) / romaji (Japanese) of the original — matches the
        VRChat tab's reading line. Empty if not romanizable or not applicable."""
        if not text:
            return ""
        with contextlib.suppress(Exception):
            if re.search(r"[぀-ヿ]", text):        # kana -> Japanese
                if not self._read_ja:
                    return ""
                from puripuly_heart.core.transliteration import to_romaji
                return to_romaji(text) or ""
            if re.search(r"[㐀-鿿]", text):        # Han ideographs -> Chinese
                if not self._read_zh:
                    return ""
                if self._pinyin_grouped:
                    from puripuly_heart.core.transliteration import to_pinyin_grouped
                    return to_pinyin_grouped(text) or ""
                from puripuly_heart.core.transliteration import to_pinyin
                return to_pinyin(text) or ""
            if re.search(r"[가-힣]", text):        # Hangul -> Korean romaja
                if not self._read_ko:
                    return ""
                from puripuly_heart.core.transliteration import to_romaja
                return to_romaja(text) or ""
            if self._read_latin:                   # other scripts -> Latin
                from puripuly_heart.core import transliteration as _tl
                for pat, fn in ((r"[а-яА-Я]", "to_latin_cyrillic"),
                                (r"[α-ωΑ-Ω]", "to_latin_greek"),
                                (r"[؀-ۿ]", "to_latin_arabic"),
                                (r"[ऀ-ॿ]", "to_latin_hindi"),
                                (r"[฀-๿]", "to_latin_thai")):
                    if re.search(pat, text) and hasattr(_tl, fn):
                        return getattr(_tl, fn)(text) or ""
        return ""

    def _needs_tr(self, text: str) -> bool:
        """True only when the text is in a different script than the reader —
        so we never translate English->English (which wastes DeepL and reads odd)."""
        if not text:
            return False
        has_cjk = bool(_CJK_RE.search(text))
        return (not has_cjk) if self._src_lang in _CJK_LANGS else has_cjk

    def _purge_emote_cache(self) -> None:
        """Drop cache entries polluted with emote artifacts (bare emote names from
        pre-fix translations, or Steam's U+02D0 colon) so old ghosts stop
        resurfacing in history."""
        if not self._emoticons:
            return
        names = {n.lower() for n in self._emoticons}
        def bad(txt: str) -> bool:
            t = (txt or "").lower()
            if "ː" in t or "ː" in t:
                return True
            return any(re.search(rf"(?<![a-z0-9_]){re.escape(n)}(?![a-z0-9_])", t)
                       for n in names)
        dirty = [k for k, v in self._tr_cache.items() if bad(k) or bad(str(v))]
        for k in dirty:
            self._tr_cache.pop(k, None)
        if dirty:
            self._tr_dirty = 999           # force save
            self._save_cache()

    def _load_cache(self) -> None:
        with contextlib.suppress(Exception):
            if _CACHE_FILE.exists():
                self._tr_cache = json.loads(_CACHE_FILE.read_text(encoding="utf-8"))

    # UI snapshot: last-known friends list + own header, painted INSTANTLY on
    # activate while the helper (headless browser) is still booting — the live
    # "friends"/"own" events replace it seamlessly when they arrive.
    def _save_snapshot(self) -> None:
        with contextlib.suppress(Exception):
            _ok = (str, int, float, bool, list, dict, type(None))
            chats = {}
            for _a in list(dict.fromkeys(
                    list(self._tabs) + list(self._chat_cache.keys())))[:16]:
                _blks = self._chat_cache.get(_a)
                if _blks:
                    chats[str(_a)] = [
                        {k: v for k, v in b.items()
                         if k not in ("_ctrl", "_out_ctrl")
                         and isinstance(v, _ok)}
                        for b in _blks[-40:]]
            snap = {"seen": {str(k): v for k, v in self._seen_chat_ts.items()},
                    "chats": chats,
                    "friends": list(self._friends.values()),
                    "own": {"acct": self._own, "name": self._own_name,
                            "avatar": self._own_avatar, "state": self._own_state,
                            "invites": self._own_invites,
                            "invisible": self._own_invisible,
                            "ingame": self._own_ingame, "game": self._own_game}}
            _CACHE_FILE.with_name("ui_snapshot.json").write_text(
                json.dumps(snap, ensure_ascii=False), encoding="utf-8")

    def _paint_snapshot(self) -> None:
        if not self._chat_cache:
            # seed the chat cache from disk so restored tabs paint instantly
            with contextlib.suppress(Exception):
                _f0 = _CACHE_FILE.with_name("ui_snapshot.json")
                if _f0.exists():
                    _snap0 = json.loads(_f0.read_text(encoding="utf-8"))
                    for _k, _blks in (_snap0.get("chats") or {}).items():
                        with contextlib.suppress(Exception):
                            self._chat_cache.setdefault(int(_k), list(_blks))
        if self._got_friends or self._friends:
            return
        with contextlib.suppress(Exception):
            f = _CACHE_FILE.with_name("ui_snapshot.json")
            if not f.exists():
                return
            snap = json.loads(f.read_text(encoding="utf-8"))
            for k, v in (snap.get("seen") or {}).items():
                with contextlib.suppress(Exception):
                    self._seen_chat_ts.setdefault(int(k), int(v))
            items = snap.get("friends") or []
            if items:
                self._friends = {int(i["acct"]): i for i in items}
                for a, i in self._friends.items():
                    if a not in self._search_index:
                        self._search_index[a] = _search_key(i.get("name", ""))
                self._rebuild_friends()
            own = snap.get("own") or {}
            if own.get("acct"):
                self._own = int(own.get("acct", 0))
                self._own_name = own.get("name") or "You"
                self._own_avatar = own.get("avatar", "") or ""
                self._own_state = int(own.get("state", 1) or 1)
                self._own_invites = int(own.get("invites", 0) or 0)
                self._own_invisible = bool(own.get("invisible"))
                self._own_ingame = bool(own.get("ingame"))
                self._own_game = own.get("game", "") or ""
                self._update_own_header()

    # Steam-tab-only preferences (independent of the app's translation settings).
    def _load_prefs(self) -> None:
        with contextlib.suppress(Exception):
            if _PREFS_FILE.exists():
                p = json.loads(_PREFS_FILE.read_text(encoding="utf-8"))
                if "show_pinyin" in p:
                    self._show_pinyin = bool(p["show_pinyin"])
                    self._pinyin_from_prefs = True   # app.py must not overwrite it
                self._tr_incoming = bool(p.get("tr_incoming", True))
                self._tr_outgoing = bool(p.get("tr_outgoing", True))
                self._show_original = bool(p.get("show_original", True))
                self._send_fmt = p.get("send_fmt", "")
                if not self._send_fmt:      # migrate the short-lived r422 pref
                    self._send_fmt = {"tr_only": "trans_only", "both": "orig_trans",
                                      "orig_only": "orig_trans"}.get(
                                          p.get("send_mode", ""), "trans_only")
                if self._send_fmt not in _SEND_FMT_LABEL:
                    self._send_fmt = "trans_only"
                self._tr_provider = p.get("tr_provider", "") or ""
                if self._tr_provider == "default":
                    self._tr_provider = ""   # follow the app's configured model
                self._read_zh = bool(p.get("read_zh", True))
                self._read_ja = bool(p.get("read_ja", True))
                self._read_ko = bool(p.get("read_ko", True))
                self._read_latin = bool(p.get("read_latin", True))
                self._pinyin_grouped = bool(p.get("pinyin_grouped", True))
                self._module_on = bool(p.get("module_on", True))
                rp = p.get("recent_picks") or []
                self._recent_picks = [list(x) for x in rp if isinstance(x, (list, tuple))
                                      and len(x) == 2][:24]
                self._pref_tabs = list(dict.fromkeys(
                    int(a) for a in (p.get("open_tabs") or [])))[:8]
                self._pref_active = int(p.get("active_tab") or 0)
                self._dnd = bool(p.get("dnd", False))
                self._tr_outgoing = bool(p.get("tr_mine", True))

    def _save_prefs(self) -> None:
        with contextlib.suppress(Exception):
            _PREFS_FILE.write_text(json.dumps({
                "show_pinyin": self._show_pinyin,
                "tr_incoming": self._tr_incoming,
                "tr_mine": self._tr_outgoing,
                "dnd": self._dnd,
                "tr_outgoing": self._tr_outgoing,
                "show_original": self._show_original,
                "send_fmt": self._send_fmt,
                "tr_provider": self._tr_provider,
                "read_zh": self._read_zh, "read_ja": self._read_ja,
                "read_ko": self._read_ko, "read_latin": self._read_latin,
                "pinyin_grouped": self._pinyin_grouped,
                "module_on": self._module_on,
                "recent_picks": self._recent_picks[:24],
                "open_tabs": list(self._tabs),
                "active_tab": self._active or 0,
            }), encoding="utf-8")

    def _save_cache(self) -> None:
        with contextlib.suppress(Exception):
            if len(self._tr_cache) > 8000:
                self._tr_cache = dict(list(self._tr_cache.items())[-6000:])
            _CACHE_FILE.write_text(json.dumps(self._tr_cache, ensure_ascii=False),
                                   encoding="utf-8")

    def _tr_model_value(self) -> str:
        cb = getattr(self, "translator_value", None)
        if callable(cb):
            with contextlib.suppress(Exception):
                return str(cb() or "app")
        return self._tr_provider or "app"

    def _tr_key(self, text: str) -> str:
        # Keyed by translator model AND target language: paid results (DeepL
        # etc.) live in their own namespace, so trying Bing then switching back
        # re-uses every cached DeepL line instead of re-billing it.
        return f"{self._tr_model_value()}|{self._src_lang}|{text}"

    async def _tr(self, text: str, *, force: bool = False) -> str:
        if not text or not self._needs_tr(text):
            return text                       # already in my language — no DeepL
        key = self._tr_key(text)
        if not force and key in self._tr_cache:
            return self._tr_cache[key]        # cached from this or a past session
        if not force and not self._tr_provider:
            # pre-r428 entries had no model prefix; they belong to the app's
            # default translator — migrate them forward on first hit
            legacy = f"{self._src_lang}|{text}"
            if legacy in self._tr_cache:
                out = self._tr_cache[legacy]
                self._tr_cache[key] = out
                return out
        # NOTE: legacy un-keyed cache entries are deliberately NOT read — they date
        # from before emote codes were stripped pre-translation, so many contain
        # mangled emote names ("meatytears") baked into the text.
        out = text
        if callable(self.translate_message):
            with contextlib.suppress(Exception):
                r = await self.translate_message(text, False)
                if r:
                    out = r
        self._tr_cache[key] = out
        self._tr_dirty += 1
        if self._tr_dirty >= 8:               # persist so restarts don't re-burn DeepL
            self._tr_dirty = 0
            self._save_cache()
        return out

    # FUSE (join into one text + one translation) only true rapid-fire bursts:
    # same sender, within _MERGE_GAP_S seconds, and the merged text stays modest.
    # GROUP (share one name/avatar header, but keep each message's own lines) up
    # to _GROUP_GAP_S — matches how the live view renders, so switching tabs does
    # NOT reshape what was already on screen into paragraphs.
    # NO text fusion, ever (user: separate messages must stay separate) — each
    # message keeps its own pinyin/original/translation lines and translates on
    # its own. Grouping only shares the name/avatar header within _GROUP_GAP_S.
    _MERGE_GAP_S = 0
    _MERGE_MAX_CHARS = 0
    _GROUP_GAP_S = 120

    def _coalesce(self, messages: list) -> list:
        blocks = []
        for m in messages:
            fm = bool(m.get("from_me"))
            ts = int(m.get("ts") or 0)
            joinable = (
                blocks
                and blocks[-1]["from_me"] == fm
                and not blocks[-1]["stickers"] and not blocks[-1]["images"]
                and not m.get("images") and not m.get("stickers")
                and (not ts or not blocks[-1]["_ts"]
                     or ts - blocks[-1]["_ts"] <= self._MERGE_GAP_S)
                and sum(len(t) for t in blocks[-1]["texts"]) + len(m.get("text") or "")
                    <= self._MERGE_MAX_CHARS
            )
            if joinable:
                b = blocks[-1]
            else:
                b = {"from_me": fm, "name": m.get("name", ""), "avatar": m.get("avatar", ""),
                     "texts": [], "images": [], "stickers": [], "_ts": ts}
                blocks.append(b)
            if m.get("text"):
                b["texts"].append(m["text"])
            b["images"] += m.get("images", [])
            b["stickers"] += m.get("stickers", [])
            if ts:
                b["_ts"] = ts
        for b in blocks:
            joined = " ".join(t.strip() for t in b["texts"] if t.strip())
            b["text"], b["emoticons"] = _extract_emoticons(joined)
        return blocks

    def _message_body_controls(self, b: dict) -> list:
        """The lines for ONE message (no name/avatar): pinyin → original (gray) →
        translation, then emotes/stickers/images. Shared by the first message in a
        block and by same-sender messages coalesced into it."""
        out: list = []
        orig = b.get("text", "")
        if self._effect_of(b):
            orig = ""          # effect messages render only the banner below
        translated = self._needs_tr(orig) and (
            self._tr_outgoing if b.get("from_me") else self._tr_incoming)
        if orig and translated:
            # Matches the VRChat tab: pinyin/romaji (top), original in GRAY, then
            # the translation as the prominent line.
            noml = _URL_RE.sub("", orig).strip()   # don't romanize/translate the URL
            if self._show_original and self._show_pinyin:
                roman = self._romanize(noml)
                if roman:
                    out.append(ft.Text(roman, size=12.5, italic=True,
                                       color=_ACCENT))
            if self._show_original:
                out.append(ft.Text(spans=_spans(orig, "#9aa0a6"), size=14))
            else:
                # translation-only mode: show the original UNTIL the translation
                # arrives, then swap it out (handled in _translate_block)
                pass
            tr_ctrl = ft.Text("", size=14, weight=ft.FontWeight.W_500,
                              color=_TEXT_PRIMARY)
            if not self._show_original:
                # translation-only mode: show the original (gray) as a placeholder
                # until the translation replaces it
                tr_ctrl.spans = _spans(orig, "#9aa0a6")
            b["_ctrl"] = tr_ctrl                    # the translation line, filled below
            out.append(tr_ctrl)
        elif orig:
            out.append(ft.Text(spans=_spans(orig), size=14))
        if b.get("_out_pending") or b.get("_out_sent"):
            # own message: the translation that was (or will be) SENT — the
            # value lives in the block so re-renders can't lose it
            out_ctrl = ft.Text(b.get("_out_sent") or "", size=13, color=_ACCENT)
            b["_out_ctrl"] = out_ctrl
            out.append(out_ctrl)
        fx = self._effect_of(b)
        if fx:
            icon, fx_name = fx
            out.append(ft.Container(
                content=ft.Row([
                    ft.Text(icon, size=22),
                    ft.Text(_T("steam.used_effect", name=b.get("name") or "",
                               effect=fx_name,
                               default=f"{b.get('name') or ''} used {fx_name}!"),
                            size=13, color=_TEXT_PRIMARY, expand=True),
                    ft.Container(
                        content=ft.Text(_T("steam.replay_effect",
                                           default="Replay effect"),
                                        size=12.5, weight=ft.FontWeight.W_600,
                                        color="#ffffff"),
                        bgcolor="#2f6fed", border_radius=6, ink=True,
                        padding=ft.padding.symmetric(horizontal=14, vertical=8),
                        on_click=lambda e, i=icon: self._play_effect_burst(i)),
                ], spacing=10, vertical_alignment=ft.CrossAxisAlignment.CENTER),
                bgcolor="#26282c", border_radius=8, padding=12,
                margin=ft.margin.only(top=4, bottom=2)))
            return out
        if b.get("emoticons"):
            out.append(ft.Row(
                [self._emote_hoverable(
                    ft.Image(src=self._emoticon_url(n), width=28, height=28,
                             fit=ft.ImageFit.CONTAIN), "e", n)
                 for n in b["emoticons"]], spacing=3, wrap=True))
        for url in b.get("stickers", []):
            sname = ""
            with contextlib.suppress(Exception):
                import urllib.parse
                sname = urllib.parse.unquote(url.split("/sticker/")[1].split("/")[0])
            img = ft.Image(src=url, width=120, height=120, fit=ft.ImageFit.CONTAIN)
            out.append(self._emote_hoverable(img, "s", sname) if sname else img)
        for url in b.get("images", []):
            out.append(self._image_control(url))
        return out

    def _block_control(self, b: dict) -> ft.Control:
        if b["from_me"]:
            name_color = _name_color(self._own_state, False)
            name = b.get("name") or self._own_name
        else:
            f = self._friends.get(self._active or 0, {})
            name_color = _name_color(int(f.get("state", 1)), bool(f.get("ingame")))
            name = b.get("name") or "Them"
        col = ft.Column(spacing=1, tight=True, expand=True)
        col.controls.append(ft.Text(name, size=12, weight=ft.FontWeight.BOLD, color=name_color))
        col.controls.extend(self._message_body_controls(b))
        # remember this block so the next same-sender message can append to it
        # (ts gates the grouping — messages long apart get their own header)
        self._last_block = {"from_me": b["from_me"], "name": b.get("name") or "",
                            "col": col, "ts": int(b.get("_ts") or 0)}
        return ft.Container(
            content=ft.Row([_avatar(b["avatar"]), col], spacing=8,
                           vertical_alignment=ft.CrossAxisAlignment.START),
            border_radius=6, bgcolor=ft.Colors.TRANSPARENT,
            padding=ft.padding.symmetric(horizontal=6, vertical=3),
            on_hover=self._msg_hover)

    def _msg_hover(self, e) -> None:
        e.control.bgcolor = "#363a41" if e.data == "true" else ft.Colors.TRANSPARENT
        with contextlib.suppress(Exception):
            e.control.update()

    def _image_control(self, url: str) -> ft.Control:
        # Show ONLY the image (never the raw steamusercontent URL as text). Left-
        # click opens it full size; right-click copies the link, for when it's
        # wanted.
        img = ft.Image(src=url, fit=ft.ImageFit.CONTAIN, width=440, border_radius=8)
        return ft.Container(
            content=ft.GestureDetector(
                content=img, mouse_cursor=ft.MouseCursor.CLICK,
                on_tap=lambda e, u=url: self._show_image_viewer(u),
                on_secondary_tap_down=lambda e, u=url: self._copy_link(u)),
            padding=ft.padding.only(top=2))

    def _show_image_viewer(self, url: str) -> None:
        # Lightbox, not a dialog: an AlertDialog's (invisible) surface swallows
        # clicks in a large rectangle around the image, which made closing feel
        # broken. Here ONLY the image and its toolbar capture clicks — a tap
        # anywhere else closes — and the buttons are visible pills.
        def pill(icon, label, on_click, accent=False):
            return ft.Container(
                content=ft.Row([
                    ft.Icon(icon, size=15,
                            color=_TOGGLE_ON if accent else _TEXT_PRIMARY),
                    ft.Text(label, size=12.5,
                            color=_TOGGLE_ON if accent else _TEXT_PRIMARY),
                ], spacing=6, tight=True),
                bgcolor="#2b2c30", border=ft.border.all(1, "#4b4c4f"),
                border_radius=8, padding=ft.padding.symmetric(horizontal=12, vertical=8),
                ink=True, on_click=on_click)

        pills = [pill(ft.Icons.COPY, _T("steam.copy_image", default="Copy image"),
                      lambda e, u=url: self.page.run_task(self._copy_image, u))]
        if url.startswith("http"):
            pills += [pill(ft.Icons.OPEN_IN_BROWSER,
                           _T("steam.open_browser", default="Open in browser"),
                           lambda e, u=url: self.page.launch_url(u)),
                      pill(ft.Icons.LINK, _T("steam.copy_link", default="Copy link"),
                           lambda e, u=url: self._copy_link(u))]
        pills.append(pill(ft.Icons.CLOSE, _T("steam.close", default="Close"),
                          lambda e: self._close_viewer(), accent=True))
        inner = ft.Column([
            ft.GestureDetector(
                on_tap=lambda e: None,      # clicks on the image don't close
                content=ft.Image(src=url, width=820, height=520,
                                 fit=ft.ImageFit.CONTAIN)),
            ft.Row(pills, spacing=8, alignment=ft.MainAxisAlignment.CENTER),
        ], spacing=10, tight=True,
           horizontal_alignment=ft.CrossAxisAlignment.CENTER)
        self._viewer_overlay.content = ft.GestureDetector(
            on_tap=lambda e: self._close_viewer(),
            content=ft.Container(bgcolor="#77000000", expand=True,
                                 alignment=ft.alignment.center, content=inner))
        if self.page and self._viewer_overlay not in self.page.overlay:
            self.page.overlay.append(self._viewer_overlay)
        self._viewer_overlay.visible = True
        if callable(getattr(self, "on_modal_change", None)):
            with contextlib.suppress(Exception):
                self.on_modal_change(True)
        if self.page:
            with contextlib.suppress(Exception):
                self.page.update()

    def _close_viewer(self) -> None:
        self._viewer.visible = False
        self._viewer_overlay.visible = False
        if callable(getattr(self, "on_modal_change", None)):
            with contextlib.suppress(Exception):
                self.on_modal_change(False)
        if self.page:
            with contextlib.suppress(Exception):
                self.page.update()

    async def _copy_image(self, url: str) -> None:
        """Put the actual image on the Windows clipboard (not just its link)."""
        import subprocess as _sp
        import tempfile
        try:
            path = url
            if url.startswith("http"):
                import httpx
                async with httpx.AsyncClient(timeout=20.0, follow_redirects=True) as c:
                    r = await c.get(url)
                    r.raise_for_status()
                path = str(Path(tempfile.gettempdir()) / "pp_copied_image.png")
                Path(path).write_bytes(r.content)
            ps = ("Add-Type -AssemblyName System.Windows.Forms; "
                  "Add-Type -AssemblyName System.Drawing; "
                  f"$img=[System.Drawing.Image]::FromFile('{path}'); "
                  "[System.Windows.Forms.Clipboard]::SetImage($img); $img.Dispose()")
            r2 = await asyncio.to_thread(
                _sp.run, ["powershell", "-STA", "-NoProfile", "-Command", ps],
                capture_output=True, timeout=30, creationflags=_CREATE_NO_WINDOW)
            if r2.returncode == 0:
                self._notice(_T("steam.image_copied", default="Image copied"))
            else:
                self._notice(_T("steam.copy_failed", default="Copy failed"))
        except Exception:
            self._notice(_T("steam.copy_failed", default="Copy failed"))

    def _copy_link(self, url: str) -> None:
        if not self.page:
            return
        with contextlib.suppress(Exception):
            self.page.set_clipboard(url)
            self.page.open(ft.SnackBar(ft.Text(_T("steam.link_copied", default="Link copied")), duration=1400))

    async def _translate_block(self, b: dict, seq: int, *, force: bool = False) -> None:
        orig = b.get("text", "")
        tc = b.get("_ctrl")
        if not orig or tc is None:
            return
        if b.pop("_tr_prefilled", False):
            return                          # already painted from the cache
        if not b.get("from_me") and not self._tr_incoming:
            return                                  # incoming translation turned off
        if b.get("from_me") and not self._tr_outgoing:
            return                                  # own-message translation turned off
        noml = _URL_RE.sub("", orig).strip()       # translate the text, not the URL
        tr = await self._tr(noml, force=force)
        if seq != self._open_seq or not tr or tr == noml:
            return
        self._apply_tr_spans(b, tc, tr)
        with contextlib.suppress(Exception):
            tc.update()

    def _apply_tr_spans(self, b: dict, tc, tr: str) -> None:
        if b.get("from_me") and self._show_pinyin:
            # own messages: the reading belongs to the TRANSLATION (the
            # original is usually not romanizable at all)
            roman = self._romanize(tr)
            if roman:
                tc.spans = [ft.TextSpan(
                    roman + "\n",
                    ft.TextStyle(size=12.5, italic=True, color=_ACCENT),
                )] + _spans(tr)
                return
        tc.spans = _spans(tr)

    def _prefill_translations(self, blocks: list) -> None:
        """Apply CACHE-HIT translations before the first paint — the
        after-paint trickle (one control update per block) was visible as a
        flicker on freshly opened chats."""
        for b in blocks:
            tc = b.get("_ctrl")
            orig = b.get("text", "")
            if tc is None or not orig:
                continue
            if not (self._tr_outgoing if b.get("from_me") else self._tr_incoming):
                continue
            noml = _URL_RE.sub("", orig).strip()
            if not noml or not self._needs_tr(noml):
                continue
            tr = self._tr_cache.get(self._tr_key(noml))
            if tr and tr != noml:
                self._apply_tr_spans(b, tc, tr)
                b["_tr_prefilled"] = True

    async def _render_history(self, messages: list, seq: int) -> None:
        blocks = self._coalesce(messages)
        if seq != self._open_seq:
            return
        # Steam's own last-chat clock can run ahead of the newest HISTORY entry
        # (own sends, filtered items) — without marking live renders too, a
        # background tab grew a phantom amber dot right after you talked in it.
        # Messages sent/received WHILE the history was still loading are not in
        # the server snapshot yet — merge them so the render can't wipe them
        # (an empty snapshot used to replace a just-sent message with
        # "No messages here yet").
        if self._live_since_open:
            have = {(int(b.get("_ts") or 0), b.get("text") or "") for b in blocks}
            extra = [m for m in self._live_since_open
                     if (int(m.get("ts") or 0), m.get("text") or "") not in have]
            if extra:
                blocks = blocks + self._coalesce(extra)
        _acct_now = self._active or 0
        _fp = tuple((int(b.get("_ts") or 0), b.get("from"), b.get("text") or "",
                     len(b.get("images") or []), len(b.get("stickers") or []),
                     len(b.get("emoticons") or [])) for b in blocks)
        if (_fp == self._render_fp.get(_acct_now)
                and self._hist_blocks and self._messages.controls):
            # identical to what's on screen — repainting would flicker; just
            # refresh the bookkeeping
            if blocks:
                self._seen_chat_ts[_acct_now] = max(
                    self._seen_chat_ts.get(_acct_now, 0),
                    max(int(b.get("_ts") or 0) for b in blocks))
            self._rebuild_tabs()
            if self.page:
                self.page.update()
            return
        self._render_fp[_acct_now] = _fp
        self._pend = None                  # drop any half-buffered live lines
        _keep_pos = (None if self._was_following.get(_acct_now, True)
                     else self._scroll_pos.get(_acct_now))
        self._messages.opacity = 0         # hidden until anchored — no visible scroll
        self._messages.controls.clear()
        self._following = _keep_pos is None
        self._last_block = None            # fresh chat — don't coalesce across it
        self._hist_blocks = blocks
        self._chat_cache[self._active or 0] = blocks
        # OLD messages are never grouped — each gets its own avatar+name block.
        for b in blocks:
            self._messages.controls.append(self._block_control(b))
        if not blocks:
            self._messages.controls.append(ft.Container(
                data="empty",
                content=ft.Text(_T("steam.no_messages",
                                   default="No messages here yet"),
                                size=12.5, color=_TEXT_FAINT),
                alignment=ft.alignment.center,
                padding=ft.padding.only(top=40)))
        if blocks:
            self._seen_chat_ts[self._active or 0] = max(
                self._seen_chat_ts.get(self._active or 0, 0),
                max(int(b.get("_ts") or 0) for b in blocks))
        self._prefill_translations(blocks)
        self._rebuild_tabs()               # clears this tab's unread dot
        if self.page:
            self.page.run_task(self._anchor_end, _keep_pos)
        if self.page:
            self.page.update()
        # translate all blocks at once (cached ones are instant) instead of
        # one-by-one — much faster, and skips English->English entirely
        await asyncio.gather(*(self._translate_block(b, seq) for b in blocks))
        self._save_cache()

    async def _append_message(self, m: dict) -> None:
        # Buffer rapid same-sender TEXT lines for ~1s so a burst renders (and
        # translates) as ONE combined message instead of one block per line.
        # Media messages render immediately (never combined).
        if m.get("images") or m.get("stickers"):
            await self._flush_pend()
            await self._render_live(m)
            return
        key = (bool(m.get("from_me")), m.get("name", "") or "")
        ts = int(m.get("ts") or 0) or int(time.time())
        if self._pend and self._pend["key"] == key \
                and ts - self._pend["ts"] <= self._MERGE_GAP_S \
                and sum(len(t) for t in self._pend["texts"]) < self._MERGE_MAX_CHARS:
            self._pend["texts"].append(m.get("text", "") or "")
            self._pend["ts"] = ts
        else:
            await self._flush_pend()
            self._pend = {"key": key, "name": m.get("name", ""), "ts": ts,
                          "avatar": m.get("avatar", ""), "texts": [m.get("text", "") or ""]}
        if self._pend_task:
            with contextlib.suppress(Exception):
                self._pend_task.cancel()
        self._pend_task = asyncio.create_task(self._pend_flush_later())

    async def _pend_flush_later(self) -> None:
        with contextlib.suppress(asyncio.CancelledError):
            await asyncio.sleep(1.0)
            await self._flush_pend()

    async def _flush_pend(self) -> None:
        p, self._pend = self._pend, None
        if self._pend_task:
            self._pend_task = None
        if not p:
            return
        await self._render_live({
            "from_me": p["key"][0], "name": p["name"], "avatar": p["avatar"],
            "text": " ".join(t for t in p["texts"] if t),
            "images": [], "stickers": [], "ts": p.get("ts") or 0,
        })

    def _mark_seen(self, acct: int, ts: int) -> None:
        if acct and ts:
            self._seen_chat_ts[acct] = max(self._seen_chat_ts.get(acct, 0), int(ts))

    async def _render_live(self, m: dict) -> None:
        # the first live message replaces the "No messages here yet" note
        if any(getattr(c, "data", None) == "empty" for c in self._messages.controls):
            self._messages.controls = [
                c for c in self._messages.controls
                if getattr(c, "data", None) != "empty"]
        self._mark_seen(self._active or 0, int(m.get("ts") or 0))
        self._live_since_open = (self._live_since_open + [dict(m)])[-30:]
        text, emos = _extract_emoticons((m.get("text", "") or "").strip())
        ts = int(m.get("ts") or 0) or int(time.time())
        b = {"from_me": bool(m.get("from_me")), "name": m.get("name", ""),
             "avatar": m.get("avatar", ""), "text": text, "emoticons": emos,
             "images": m.get("images", []), "stickers": m.get("stickers", []),
             "_ts": ts, "_out_pending": bool(m.get("_out_pending"))}
        lb = self._last_block
        if (lb and lb["from_me"] == b["from_me"] and lb["name"] == (b.get("name") or "")
                and (not lb.get("ts") or ts - lb["ts"] <= self._GROUP_GAP_S)):
            # Same sender, recent → share the name header; the message KEEPS its own
            # lines (no text fusion).
            with contextlib.suppress(Exception):
                lb["col"].controls.extend(self._message_body_controls(b))
            lb["ts"] = ts
        else:
            self._messages.controls.append(self._block_control(b))
        self._hist_blocks.append(b)
        self._seen_chat_ts[self._active or 0] = max(
            self._seen_chat_ts.get(self._active or 0, 0), ts)
        if self.page:
            self.page.update()
            if self._following:
                self._scroll_to_end()
        await self._translate_block(b, self._open_seq)
        return b

    # ── helper process + socket ──────────────────────────────────────────────
    async def _connect(self) -> None:
        if self._writer is not None:
            return
        connected = False
        for _ in range(6):
            if await self._try_open():
                connected = True
                break
            await asyncio.sleep(0.5)
        if not connected:
            # Spawn guard: during a slow cold boot every retry used to spawn
            # ANOTHER daemon (a farm of six once fought over the profile).
            now = time.monotonic()
            if now - getattr(self, "_last_spawn", 0.0) > 30.0:
                self._last_spawn = now
                import subprocess
                with contextlib.suppress(Exception):
                    self._proc = subprocess.Popen(
                        [str(_DAEMON_PYTHON), str(_DAEMON_PY)],
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                        creationflags=_CREATE_NO_WINDOW)
            for _ in range(60):
                if await self._try_open():
                    connected = True
                    break
                await asyncio.sleep(0.5)
        if connected:
            self._reconnect_delay = 1.0
            self.page.run_task(self._read_loop)

    async def _try_open(self) -> bool:
        try:
            self._reader, self._writer = await asyncio.open_connection(_HOST, _PORT)
            return True
        except Exception:
            return False

    async def _cmd(self, obj: dict) -> bool:
        if self._writer is None:
            self._queue_if_send(obj)
            return False
        try:
            self._writer.write((json.dumps(obj, ensure_ascii=False) + "\n").encode("utf-8"))
            await self._writer.drain()
            return True
        except Exception:
            self._writer = None
            self._queue_if_send(obj)
            return False

    def _queue_if_send(self, obj: dict) -> None:
        """A send hit a dead helper socket (deploy/crash mid-session). Queue it
        for automatic redelivery after the read-loop's self-heal reconnects —
        the write provably never reached Steam, so a resend cannot duplicate."""
        if obj.get("cmd") not in ("send", "send_image"):
            return
        self._resend_queue.append(obj)
        self._notice(_T("steam.not_delivered",
                        default="Connection to Steam was down — your message "
                                "will be sent automatically once it reconnects."))

    def _set_typing(self, name: str) -> None:
        self._typing_text.value = (
            _T("steam.typing", name=name, default=f"{name} is typing…") if name else "")
        if self.page:
            with contextlib.suppress(Exception):
                self._typing_text.update()

    async def _open(self, acct: int) -> None:
        if self._popped_out and not self._is_popout:
            self._show_state_overlay("popped")
            return
        self._hide_ctx()
        self._open_seq += 1
        self._live_since_open = []
        self._active = acct
        if acct not in self._tabs:
            self._tabs.append(acct)
        self._rebuild_tabs()
        self._set_typing("")
        self._set_chat_head(self._friends.get(acct))
        self._rebuild_friends()
        # Don't clear here — keep the previous messages visible until the new
        # chat's history arrives (then _render_history swaps atomically), so a
        # fast switch doesn't flash blank.
        self._entry.disabled = False   # let them type right away, don't wait for load
        # ALWAYS swap the pane on a tab switch — keeping the previous chat
        # visible read as "wrong chat shown". Cached chats paint instantly and
        # the fresh fetch replaces them silently; uncached ones show a loading
        # state (never a blank wall, and never someone else's messages).
        if self._state_mode == "idle":
            self._hide_state_overlay()
        self._pend = None
        self._last_block = None
        self._messages.controls.clear()
        cached = self._chat_cache.get(acct)
        if cached:
            self._hist_blocks = cached
            _keep_pos = (None if self._was_following.get(acct, True)
                         else self._scroll_pos.get(acct))
            self._following = _keep_pos is None
            self._messages.opacity = 0     # hidden until anchored
            for b in cached:
                b.pop("_ctrl", None)
                self._messages.controls.append(self._block_control(b))
            self._prefill_translations(cached)
            if self.page:
                self.page.run_task(self._anchor_end, _keep_pos)
                self.page.run_task(self._fill_translations, cached, self._open_seq)
        else:
            self._messages.controls.append(ft.Container(
                content=ft.Row([
                    ft.ProgressRing(width=16, height=16, stroke_width=2,
                                    color=_ACCENT),
                    ft.Text(_T("steam.loading_history",
                               default="Loading chat history"),
                            size=12.5, color=_TEXT_FAINT),
                ], spacing=10, alignment=ft.MainAxisAlignment.CENTER),
                padding=ft.padding.only(top=40)))
        if self.page:
            self.page.update()
        self._save_prefs()
        await self._cmd({"cmd": "open", "acct": acct})

    async def _send(self) -> None:
        text = (self._entry.value or "").strip()
        if not self._active or not text:
            return
        if len(text) > self._MAX_MSG:
            self._notice(_T("steam.too_long", n=str(len(text)),
                            max=str(self._MAX_MSG),
                            default=f"Message is too long for Steam "
                                    f"({len(text)}/{self._MAX_MSG})"))
            return
        self._entry.value = ""
        if self.page:
            self._entry.update()
        # Same behavior as the main chat tab: the ORIGINAL goes out (and renders)
        # IMMEDIATELY — never gated on DeepL latency — and the translation follows
        # as its own message the moment it's ready, shown under the original.
        clean, emos = _extract_emoticons(text)
        codes = " ".join(f":{n}:" for n in emos)
        orig_out = (f"{clean} {codes}".strip() if clean else codes)
        acct = self._active
        fmt = self._send_fmt if clean else "orig_only"   # emote-only: send as-is
        # ALWAYS render my message instantly on my end; the pending accent line
        # fills with what actually got sent once the composed message goes out.
        b = await self._render_live({
            "from_me": True, "name": self._own_name, "avatar": self._own_avatar,
            "text": orig_out, "images": [], "stickers": [],
            "ts": int(time.time()),
            "_out_pending": fmt != "orig_only"})
        if fmt == "orig_only":
            await self._cmd({"cmd": "send", "acct": acct, "text": orig_out})
            return
        tr_src = _URL_RE.sub("", clean).strip()
        urls = " ".join(_URL_RE.findall(clean))
        if not tr_src:
            # nothing but links — one message only, no near-identical
            # "translation" duplicate (_needs_tr judges INCOMING direction,
            # so it must not gate outgoing text)
            if b is not None:
                b.pop("_out_pending", None)
            await self._cmd({"cmd": "send", "acct": acct, "text": orig_out})
            return
        zh = None
        if callable(self.translate_message):
            with contextlib.suppress(Exception):
                zh = await self.translate_message(tr_src, True)
        zh = (zh or "").strip()
        if not zh or zh.lower() == tr_src.lower():
            # translation failed or came back unchanged — send once, as-is
            if b is not None:
                b.pop("_out_pending", None)
            await self._cmd({"cmd": "send", "acct": acct, "text": orig_out})
            return
        self._tr_cache[self._tr_key(zh)] = tr_src         # echo renders instantly
        reading = self._romanize(zh) if "read" in fmt else ""
        lines = []
        if fmt.startswith("orig"):
            lines.append(orig_out)
        if reading:
            lines.append(reading)
        if fmt != "read_only":
            lines.append(zh if fmt.startswith("orig")
                         else (zh + (" " + codes if codes else "")).strip())
        out2 = "\n".join(l for l in lines if l).strip() or orig_out
        if urls and not fmt.startswith("orig") and urls not in out2:
            out2 = f"{out2}\n{urls}".strip()   # links ride along untranslated
        await self._cmd({"cmd": "send", "acct": acct, "text": out2})
        sent_extra = "\n".join(l for l in lines if l and l != orig_out)
        if b is not None and sent_extra:
            b["_out_sent"] = sent_extra        # survives any re-render
        ctrl = (b or {}).get("_out_ctrl")
        if ctrl is not None and sent_extra:
            ctrl.value = sent_extra                        # what was sent to them
            with contextlib.suppress(Exception):
                ctrl.update()

    async def _read_loop(self) -> None:
        try:
            async for raw in self._reader:
                try:
                    ev = json.loads(raw.decode("utf-8").strip() or "{}")
                except Exception:
                    continue
                await self._handle(ev)
        except Exception:
            pass
        # Connection closed (daemon died/restarted) — self-heal so the tab does
        # not go dead/blank. Reconnect (respawns the helper if needed).
        self._reader = None
        self._writer = None
        if self._started:
            # backoff so a flapping helper can't drive a 1s reopen storm
            delay = getattr(self, "_reconnect_delay", 1.0)
            self._reconnect_delay = min(delay * 2, 15.0)
            await asyncio.sleep(delay)
            await self._connect()
            if self._writer is not None and self._active is not None:
                await self._cmd({"cmd": "open", "acct": self._active})

    def _hide_loading(self) -> None:
        if self._loading.visible:
            self._loading.visible = False
            if self.page:
                with contextlib.suppress(Exception):
                    self._loading.update()

    async def _handle(self, ev: dict) -> None:
        kind = ev.get("ev")
        if kind == "own":
            self._own = int(ev.get("acct", 0))
            self._own_name = ev.get("name") or "You"
            self._own_avatar = ev.get("avatar", "") or self._own_avatar
            self._own_state = int(ev.get("state", 1) or 1)
            self._own_invites = int(ev.get("invites", 0) or 0)
            self._own_invisible = bool(ev.get("invisible"))
            self._own_ingame = bool(ev.get("ingame"))
            self._own_game = ev.get("game", "") or ""
            if ev.get("emoticons"):
                self._emoticons, self._emote_meta = _norm_named(ev.get("emoticons"))
                self._purge_emote_cache()
            if ev.get("stickers") is not None:
                self._stickers, self._sticker_meta = _norm_named(ev.get("stickers"))
            if ev.get("effects"):
                names = [str(n) for n in ev.get("effects")]
                icons = {"balloons": "🎈", "confetti": "🎉",
                         "firework": "🎆", "fireworks": "🎆", "goldfetti": "🎊"}
                self._effects = [(n, icons.get(n, "✨")) for n in names]
            self._update_own_header()
            self._save_snapshot()
        elif kind == "login_progress":
            stage = ev.get("stage", "")
            cap = {
                "opening": _T("steam.login_opening",
                              default="Opening the Steam sign-in window"),
                "waiting": _T("steam.login_waiting",
                              default="Waiting for you to log in in the Steam window"),
                "finishing": _T("steam.login_finishing",
                                default="Signed in — loading your friends"),
            }.get(stage)
            if cap and self._state_overlay.visible:
                self._state_prog.visible = True
                self._state_caption.value = cap
                with contextlib.suppress(Exception):
                    self._state_caption.update()
                    self._state_prog.update()
        elif kind == "status":
            if ev.get("signed_in") and self._resend_queue:
                q, self._resend_queue = self._resend_queue, []
                ok_all = True
                for obj in q:
                    ok_all = await self._cmd(obj) and ok_all
                if ok_all:
                    self._notice(_T("steam.redelivered",
                                    default="Reconnected — your queued messages were sent."))
            self._state_prog.visible = False
            with contextlib.suppress(Exception):
                self._state_prog.update()
            self._state_btn.disabled = False
            with contextlib.suppress(Exception):
                self._state_btn.update()
            if ev.get("signed_in"):
                self._hide_state_overlay()
                if not self._tabs and self._pref_tabs:
                    # restore the workspace (pop-out windows + app restarts)
                    self._tabs = list(dict.fromkeys(self._pref_tabs))
                    act = (self._pref_active
                           if self._pref_active in self._tabs else self._tabs[-1])
                    self._rebuild_tabs()
                    self.page.run_task(self._open, act)
                    self.page.run_task(self._warm_tabs, act)
                elif self._active is None and self._module_on:
                    self._show_state_overlay("idle")
            elif self._module_on and ev.get("mode") != "login":
                self._show_state_overlay("signedout")
        elif kind == "seen":
            _sa, _sts = int(ev.get("acct") or 0), int(ev.get("ts") or 0)
            if _sa and _sts and _sts > self._seen_chat_ts.get(_sa, 0):
                self._seen_chat_ts[_sa] = _sts
                self._rebuild_tabs()
                if self.page:
                    with contextlib.suppress(Exception):
                        self.page.update()
        elif kind == "friends":
            if not (ev.get("items") or []) and not self._got_friends:
                return          # pre-signin empty push — keep Connecting up
            if self._state_mode == "connecting":
                self._hide_state_overlay()
                if self._active is None and self._module_on:
                    self._show_state_overlay("idle")
            self._friends = {int(i["acct"]): i for i in ev.get("items", [])}
            self._got_friends = True
            stale = [a for a in self._tabs
                     if a not in self._friends and a != self._own]
            if stale:
                self._tabs = [a for a in self._tabs if a not in stale]
                for a in stale:
                    self._scroll_pos.pop(a, None)
                    self._was_following.pop(a, None)
                    self._render_fp.pop(a, None)
                if self._active in stale:
                    self._active = None
                    self._messages.controls.clear()
                    self._hist_blocks = []
                    if self._tabs and self.page:
                        self.page.run_task(self._open, self._tabs[-1])
                    elif self._module_on:
                        self._show_state_overlay("idle")
                self._save_prefs()
            for a, i in self._friends.items():
                if a not in self._search_index:
                    self._search_index[a] = _search_key(i.get("name", ""))
            self._hide_loading()
            self._rebuild_friends()
            self._rebuild_tabs()
            self._save_snapshot()
            if self._active in self._friends:
                self._set_chat_head(self._friends[self._active])
            if self.page:
                self.page.update()
        elif kind == "history":
            h_acct = int(ev.get("acct", 0))
            if h_acct == self._active:
                await self._render_history(ev.get("messages", []), self._open_seq)
            else:
                # late history for a chat we already left — cache it so the next
                # switch there paints instantly
                with contextlib.suppress(Exception):
                    self._chat_cache[h_acct] = self._coalesce(ev.get("messages", []))
        elif kind == "opened":
            # Do NOT set self._active here — it's already set by _open() to what the
            # user last clicked. A late "opened" from a chat they switched AWAY from
            # would otherwise hijack the active chat and blank the history.
            if ev.get("ok") and int(ev.get("acct", 0)) == self._active:
                self._entry.disabled = False
                if self.page:
                    self.page.update()
        elif kind == "typing":
            if int(ev.get("acct", 0)) == self._active:
                self._set_typing(ev.get("name", "") if ev.get("typing") else "")
        elif kind == "inbound":
            if int(ev.get("acct", 0)) == self._active:
                self._set_typing("")
                await self._append_message(ev.get("message", {}))
        elif kind == "image_sent":
            if not ev.get("ok"):
                d = ev.get("detail") or {}
                with contextlib.suppress(Exception):
                    self.page.open(ft.SnackBar(ft.Text(
                        f"Image failed to send (step: {d.get('step', '?')}"
                        f"{', ' + str(d.get('status')) if d.get('status') else ''})"),
                        duration=4000))

    async def shutdown(self) -> None:
        with contextlib.suppress(Exception):
            if self._writer:
                self._writer.close()
        with contextlib.suppress(Exception):
            if self._proc and self._proc.returncode is None:
                self._proc.terminate()
