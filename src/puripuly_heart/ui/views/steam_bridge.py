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
import unicodedata
from pathlib import Path

import flet as ft


def _search_key(name: str) -> str:
    """name (lowercased) + its romanization, so 'aba' matches 阿巴阿巴 (ā bā…)."""
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
_SECTION = "#8a8c90"

_FLAG_MOBILE = 0x200
_FLAG_VR = 0x800
_STEAMID64_BASE = 76561197960265728
_URL_RE = re.compile(r"(https?://[^\s]+)")
# CJK ideographs, Japanese kana, Korean hangul — used to decide if a message is
# in a different script than the reader (so we don't translate English->English).
_CJK_RE = re.compile(r"[㐀-鿿぀-ヿ가-힣]")
_CJK_LANGS = {"zh-CN", "zh-TW", "ja", "ko"}
_EMOTE_RE = re.compile(r":([a-zA-Z][a-zA-Z0-9_]{1,}):")


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
_BRIDGE_ROOT = Path(r"C:\Users\Owner\Desktop\PuriPuly-heart-2.1.2\steam-helper")
_DAEMON_PY = _BRIDGE_ROOT / "steam_bridge" / "daemon.py"
_CACHE_FILE = _BRIDGE_ROOT / "steam_bridge" / "tr_cache.json"
_PREFS_FILE = _BRIDGE_ROOT / "steam_bridge" / "view_prefs.json"
_VENV_SCRIPTS = _BRIDGE_ROOT / "steamprobe-venv" / "Scripts"
_DAEMON_PYTHON = (
    _VENV_SCRIPTS / "pythonw.exe" if (_VENV_SCRIPTS / "pythonw.exe").exists()
    else _VENV_SCRIPTS / "python.exe")
_CREATE_NO_WINDOW = 0x08000000
_HOST, _PORT = "127.0.0.1", 8791

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
        self._show_pinyin = True     # seeded from the app's chat pinyin setting
        self._emoticons: list[str] = []
        self._active = None
        self._open_seq = 0
        self._last_block = None       # last rendered msg block, for same-sender coalescing
        self._tr_incoming = True      # translate their messages (Steam-tab setting)
        self._tr_outgoing = True      # translate my messages before sending
        self._pend: dict | None = None   # short buffer: rapid same-sender lines → ONE block
        self._pend_task = None
        self._hist_blocks: list = []  # blocks currently rendered (for retranslate)
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
            hint_text="Search friends", dense=True, height=30,
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
        self._from_lbl = ft.Text(_LANG_LABEL.get(self._src_lang, "English"),
                                 size=12, color=_TEXT_PRIMARY, weight=ft.FontWeight.W_500)
        self._to_lbl = ft.Text(_LANG_LABEL.get(self._tgt_lang, "中文(简)"),
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
        self._tab_strip = ft.Row([], spacing=6, scroll=ft.ScrollMode.AUTO, expand=True)
        settings_btn = ft.IconButton(
            ft.Icons.SETTINGS_OUTLINED, icon_size=17, icon_color=_TEXT_FAINT,
            tooltip="Steam chat settings",
            on_click=lambda e: self._toggle_settings(),
            style=ft.ButtonStyle(overlay_color=ft.Colors.TRANSPARENT))
        top_bar = ft.Container(
            content=ft.Row([self._tab_strip, settings_btn], spacing=8,
                           vertical_alignment=ft.CrossAxisAlignment.CENTER),
            padding=ft.padding.only(left=8, right=12, top=6, bottom=4), bgcolor=_BG_MAIN)
        self._tab_bar = ft.Container(visible=False)   # kept: _rebuild_tabs toggles it

        self._messages = ft.ListView(expand=True, spacing=10, padding=14, auto_scroll=True,
                                     on_scroll=self._on_msg_scroll)
        self._max_scroll = 0.0
        self._jump_btn = ft.Container(
            visible=False, right=14, bottom=12, width=34, height=34, border_radius=17,
            bgcolor="#3a3b3e", border=ft.border.all(1, "#55565a"),
            alignment=ft.alignment.center, tooltip="Jump to latest",
            content=ft.Icon(ft.Icons.KEYBOARD_DOUBLE_ARROW_DOWN_ROUNDED, size=18, color="#e8e8e8"),
            on_click=lambda e: self._jump_to_latest())
        self._typing_text = ft.Text("", size=12, color=_TEXT_FAINT)
        typing_row = ft.Container(content=self._typing_text, height=20,
                                  padding=ft.padding.only(left=14),
                                  alignment=ft.alignment.center_left)
        self._entry = ft.TextField(
            hint_text="Type message to send", disabled=True,
            border=ft.InputBorder.OUTLINE, border_color=_BORDER_INPUT,
            focused_border_color=_TOGGLE_ON, text_size=13, color=_TEXT_PRIMARY,
            hint_style=ft.TextStyle(color=_TEXT_FAINT, italic=True),
            expand=True, multiline=True, min_lines=2, max_lines=4, shift_enter=True,
            bgcolor=_BG_INPUT, border_radius=8,
            content_padding=ft.padding.symmetric(horizontal=12, vertical=8),
            on_submit=lambda e: self.page.run_task(self._send))
        emoji_btn = ft.IconButton(
            ft.Icons.EMOJI_EMOTIONS_OUTLINED, icon_size=20, icon_color=_TEXT_FAINT,
            tooltip="Emoji & emoticons", on_click=lambda e: self._toggle_emoji(),
            style=ft.ButtonStyle(overlay_color=ft.Colors.TRANSPARENT))
        input_row = ft.Container(
            content=ft.Row([
                ft.GestureDetector(
                    content=self._entry, expand=True,
                    on_secondary_tap_down=lambda e: self._show_input_menu(e)),
                ft.IconButton(ft.Icons.SEND_ROUNDED, icon_size=18, icon_color=_TOGGLE_ON,
                              tooltip="Send", on_click=lambda e: self.page.run_task(self._send),
                              style=ft.ButtonStyle(overlay_color=ft.Colors.TRANSPARENT,
                                                   padding=ft.padding.all(8))),
                emoji_btn,
                ft.IconButton(ft.Icons.ATTACH_FILE, icon_size=18, icon_color=_TEXT_FAINT,
                              tooltip="Attach (coming soon)",
                              on_click=lambda e: self._not_yet("Attachments")),
                ft.IconButton(ft.Icons.MIC_NONE, icon_size=18, icon_color=_TEXT_FAINT,
                              tooltip="Voice message (coming soon)",
                              on_click=lambda e: self._not_yet("Voice messages")),
            ], spacing=4, vertical_alignment=ft.CrossAxisAlignment.CENTER),
            padding=ft.padding.symmetric(horizontal=8, vertical=6), bgcolor=_BG_MAIN)

        chat_area = ft.Column([
            top_bar,
            ft.Divider(height=1, color=_DIVIDER, thickness=1),
            ft.Container(content=ft.Stack([
                ft.SelectionArea(content=self._messages), self._jump_btn,
            ], expand=True), expand=True),
            typing_row,
            ft.Divider(height=1, color=_DIVIDER, thickness=1),
            input_row,
        ], spacing=0, expand=True)

        main_row = ft.Row([
            self._left_panel,
            ft.VerticalDivider(width=1, color=_DIVIDER, thickness=1),
            ft.Container(content=chat_area, expand=True),
        ], spacing=0, expand=True, vertical_alignment=ft.CrossAxisAlignment.STRETCH)

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
        self._emoji_panel = ft.Container(
            visible=False, bgcolor=_BG_MENU, border_radius=10,
            border=ft.border.all(1, "#4b4c4f"), width=320, height=340,
            right=8, bottom=64, padding=8,
            shadow=ft.BoxShadow(blur_radius=16, color="#99000000", offset=ft.Offset(0, 4)))
        self._emoji_backdrop = ft.GestureDetector(
            visible=False, on_tap=lambda e: self._toggle_emoji(False),
            content=ft.Container(expand=True, bgcolor=ft.Colors.TRANSPARENT))
        # Steam-tab settings panel (gear in the top bar) + input right-click menu.
        self._settings_panel = ft.Container(
            visible=False, bgcolor=_BG_MENU, border_radius=10,
            border=ft.border.all(1, "#4b4c4f"), width=280,
            right=8, top=44, padding=12,
            shadow=ft.BoxShadow(blur_radius=16, color="#99000000", offset=ft.Offset(0, 4)))
        self._settings_backdrop = ft.GestureDetector(
            visible=False, on_tap=lambda e: self._toggle_settings(False),
            content=ft.Container(expand=True, bgcolor=ft.Colors.TRANSPARENT))
        self._input_menu = ft.Container(
            visible=False, bgcolor=_BG_MENU, border_radius=8,
            border=ft.border.all(1, "#4b4c4f"), padding=4, width=170,
            left=300, bottom=70,
            shadow=ft.BoxShadow(blur_radius=14, color="#88000000", offset=ft.Offset(0, 4)))
        self.content = ft.Stack([main_row, self._loading,
                                  self._emoji_backdrop, self._emoji_panel,
                                  self._ctx_backdrop, self._ctx_menu,
                                  self._settings_backdrop, self._settings_panel,
                                  self._input_menu], expand=True)
        self._load_cache()
        self._load_prefs()
        self._update_own_header()

    def _lang_button(self, which: str) -> ft.Control:
        lbl = self._from_lbl if which == "from" else self._to_lbl

        def make_item(code, label):
            def h(e):
                if which == "from":
                    self._src_lang = code
                else:
                    self._tgt_lang = code
                lbl.value = _LANG_LABEL.get(code, code)
                with contextlib.suppress(Exception):
                    lbl.update()
            return ft.PopupMenuItem(text=label, on_click=h)

        return ft.PopupMenuButton(
            content=ft.Row([lbl, ft.Icon(ft.Icons.ARROW_DROP_DOWN, size=14, color=_TEXT_FAINT)],
                           spacing=0, tight=True),
            tooltip=("Your language" if which == "from" else "Their language"),
            items=[make_item(c, l) for c, l in _LANGS])

    def set_languages(self, src: str, tgt: str) -> None:
        if src:
            self._src_lang = src
            self._from_lbl.value = _LANG_LABEL.get(src, src)
        if tgt:
            self._tgt_lang = tgt
            self._to_lbl.value = _LANG_LABEL.get(tgt, tgt)

    # ── lifecycle ────────────────────────────────────────────────────────────
    def prewarm(self) -> None:
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
        if self._started:
            return
        self._started = True
        if self.page:
            self.page.run_task(self._connect)

    def _insert_emoji(self, em: str) -> None:
        self._entry.value = (self._entry.value or "") + em
        if self.page:
            self._entry.update()

    def _insert_emoticon(self, name: str) -> None:
        # Steam emoticons are sent as :name: and render on both ends.
        self._entry.value = (self._entry.value or "") + f":{name}: "
        if self.page:
            self._entry.update()
        self._toggle_emoji(False)

    def _emoticon_url(self, name: str) -> str:
        return f"https://community.fastly.steamstatic.com/economy/emoticon/{name}"

    def _build_emoji_panel(self) -> None:
        def cell(control, on_click, tip=None):
            return ft.Container(content=control, on_click=on_click, ink=True, border_radius=6,
                                padding=3, width=36, height=36, tooltip=tip,
                                alignment=ft.alignment.center)
        sections = [ft.Text("EMOJI", size=11, weight=ft.FontWeight.BOLD, color=_SECTION),
                    ft.Row([cell(ft.Text(e, size=20), (lambda ev, em=e: self._insert_emoji(em)))
                            for e in _EMOJIS], wrap=True, spacing=2, run_spacing=2)]
        if self._emoticons:
            sections.append(ft.Text("MY EMOTICONS", size=11, weight=ft.FontWeight.BOLD,
                                    color=_SECTION))
            sections.append(ft.Row([
                cell(ft.Image(src=self._emoticon_url(n), width=26, height=26,
                              fit=ft.ImageFit.CONTAIN),
                     (lambda ev, nm=n: self._insert_emoticon(nm)), tip=f":{n}:")
                for n in self._emoticons], wrap=True, spacing=2, run_spacing=2))
        else:
            sections.append(ft.Text("Your Steam emoticons load with your account…",
                                    size=11, color=_TEXT_FAINT))
        self._emoji_panel.content = ft.Column(sections, spacing=6, tight=True,
                                              scroll=ft.ScrollMode.AUTO)

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
        def row(label, value, cb):
            return ft.Row([
                ft.Text(label, size=13, color=_TEXT_PRIMARY, expand=True),
                ft.Switch(value=value, active_color=_TOGGLE_ON, scale=0.8,
                          on_change=cb),
            ], vertical_alignment=ft.CrossAxisAlignment.CENTER)

        def btn(label, cb, tip=None):
            return ft.Container(
                content=ft.Text(label, size=13, color=_TEXT_PRIMARY),
                padding=ft.padding.symmetric(horizontal=8, vertical=7),
                border_radius=6, ink=True, on_click=cb, tooltip=tip)

        self._settings_panel.content = ft.Column([
            ft.Text("STEAM CHAT SETTINGS", size=11, weight=ft.FontWeight.BOLD,
                    color=_SECTION),
            row("Show pinyin / romaji", self._show_pinyin, self._set_pinyin),
            row("Translate their messages", self._tr_incoming, self._set_tr_incoming),
            row("Translate my messages", self._tr_outgoing, self._set_tr_outgoing),
            ft.Divider(height=1, color="#4b4c4f"),
            btn("Clean up / re-render chat", lambda e: self._rerender_chat(),
                tip="Re-group and re-render this chat's history"),
            btn("Retranslate history…", lambda e: self._retranslate_prompt(),
                tip="Redo translations (e.g. after changing language)"),
        ], spacing=8, tight=True)

    def _set_pinyin(self, e) -> None:
        self._show_pinyin = bool(e.control.value)
        self._pinyin_from_prefs = True
        self._save_prefs()
        self._rerender_chat()

    def _set_tr_incoming(self, e) -> None:
        self._tr_incoming = bool(e.control.value)
        self._save_prefs()
        self._rerender_chat()

    def _set_tr_outgoing(self, e) -> None:
        self._tr_outgoing = bool(e.control.value)
        self._save_prefs()

    def _rerender_chat(self) -> None:
        # Re-request history from the helper — re-groups (time-gap coalesce) and
        # re-renders; translations come from cache, so no DeepL is burned.
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
                self.page.open(ft.SnackBar(ft.Text("Nothing here needs retranslating.")))
            return
        dlg = ft.AlertDialog(
            modal=True,
            title=ft.Text("Retranslate history?", size=15),
            content=ft.Text(
                f"This will re-send ≈{chars} characters to your translator "
                f"(DeepL bills per character), replacing the cached translations "
                f"for this chat.", size=13),
            actions=[
                ft.TextButton("Cancel", on_click=lambda e: self.page.close(dlg)),
                ft.TextButton("Retranslate",
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
        self._ctx_menu.left = max(4.0, gx - 8)
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
        out = []
        flags = int(f.get("flags", 0))
        if int(f.get("state", 0)) == 4:
            out.append(ft.Text("💤", size=11))
        if flags & _FLAG_VR:
            out.append(ft.Icon(ft.Icons.VIEW_IN_AR, size=13, color=_C_INGAME))
        if flags & _FLAG_MOBILE:
            out.append(ft.Icon(ft.Icons.SMARTPHONE, size=12, color=_TEXT_FAINT))
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
        # name + caret; opens a Steam-style status menu
        opts = [("Online", 1), ("Away", 3), ("Invisible", 7), ("Offline", 0)]
        return ft.PopupMenuButton(
            content=ft.Row([
                ft.Text(self._own_name or "Me", size=14, weight=ft.FontWeight.BOLD,
                        color=_TEXT_PRIMARY, max_lines=1, overflow=ft.TextOverflow.ELLIPSIS),
                ft.Icon(ft.Icons.ARROW_DROP_DOWN, size=16, color=_TEXT_FAINT),
            ], spacing=0, tight=True),
            tooltip="Set status",
            items=[ft.PopupMenuItem(
                       height=32,
                       content=ft.Text(label, size=13, color=_TEXT_PRIMARY),
                       on_click=lambda e, s=st: self.page.run_task(self._set_status, s))
                   for label, st in opts]
            + [ft.PopupMenuItem(height=1),
               ft.PopupMenuItem(height=32,
                                content=ft.Text("View my Steam profile", size=13, color=_TEXT_PRIMARY),
                                on_click=lambda e: self._open_profile(self._own))])

    def _update_own_header(self) -> None:
        if self._own_invisible:
            status_row = ft.Row([ft.Icon(ft.Icons.VISIBILITY_OFF, size=13, color=_TEXT_FAINT),
                                 ft.Text("Invisible", size=11, color=_TEXT_FAINT)],
                                spacing=4, tight=True)
        else:
            status_row = ft.Text(_STATE_LABEL.get(self._own_state, "Online"), size=11,
                                 color=_name_color(self._own_state, False))
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
        await self._cmd({"cmd": "status", "state": int(state)})

    def _friend_row(self, f: dict, *, lead_icon: str = "") -> ft.Control:
        acct = int(f["acct"])
        state, ingame = int(f.get("state", 0)), bool(f.get("ingame"))
        if ingame:
            sub, sub_color = (f.get("game") or "In-Game"), _C_INGAME
        elif state:
            sub, sub_color = _STATE_LABEL.get(state, "Online"), _TEXT_FAINT
        else:
            sub, sub_color = "Offline", _TEXT_FAINT
        name_row = ft.Row([
            ft.Text(f.get("name") or "Steam friend", size=13, weight=ft.FontWeight.W_500,
                    color=_name_color(state, ingame), max_lines=1,
                    overflow=ft.TextOverflow.ELLIPSIS),
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
        return ft.Container(
            content=gd, padding=ft.padding.symmetric(horizontal=8, vertical=4),
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
        return ft.Container(
            content=ft.Row([
                ft.Icon(ft.Icons.CHEVRON_RIGHT if collapsed else ft.Icons.EXPAND_MORE,
                        size=14, color=color),
                ft.Text(f"{label}  {n}" if n else label, size=11,
                        weight=ft.FontWeight.BOLD, color=color),
            ], spacing=1, tight=True, vertical_alignment=ft.CrossAxisAlignment.CENTER),
            padding=ft.padding.only(left=4, top=10, bottom=3), ink=True,
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
        header = ft.Container(
            content=ft.Row([
                ft.Icon(ft.Icons.CHEVRON_RIGHT if collapsed else ft.Icons.EXPAND_MORE,
                        size=14, color=_C_INGAME),
                self._game_icon(icon, 22),
                ft.Text(f"{game}  {len(members)}", size=12, weight=ft.FontWeight.BOLD,
                        color=_C_INGAME),
            ], spacing=6, tight=True, vertical_alignment=ft.CrossAxisAlignment.CENTER),
            padding=ft.padding.only(left=4, top=10, bottom=3), ink=True,
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
        active = (acct == self._active)
        state, ingame = int(f.get("state", 0)), bool(f.get("ingame"))
        sub = (f.get("game") or "In-Game") if ingame else _STATE_LABEL.get(state, "Offline")
        chip = ft.Container(
            content=ft.Row([
                _avatar(f.get("avatar", ""), 26),
                ft.Column([
                    ft.Text(f.get("name") or "Chat", size=13,
                            color=_TEXT_PRIMARY if active else _TEXT_FAINT,
                            max_lines=1, overflow=ft.TextOverflow.ELLIPSIS,
                            weight=ft.FontWeight.BOLD if active else ft.FontWeight.W_500),
                    ft.Text(sub, size=10, color=_name_color(state, ingame) if active else _TEXT_FAINT,
                            max_lines=1, overflow=ft.TextOverflow.ELLIPSIS),
                ], spacing=0, tight=True),
                ft.IconButton(ft.Icons.CLOSE, icon_size=13, icon_color=_TEXT_FAINT,
                              tooltip="Close", width=20, height=20,
                              on_click=lambda e, a=acct: self._close_tab(a),
                              style=ft.ButtonStyle(padding=ft.padding.all(0))),
            ], spacing=8, tight=True, vertical_alignment=ft.CrossAxisAlignment.CENTER),
            padding=ft.padding.only(left=8, right=4, top=4, bottom=4),
            border_radius=8, bgcolor=_BG_SEL if active else _BG_MENU,
            on_click=lambda e, a=acct: self.page.run_task(self._open, a))
        # Right-click (mouse 3) anywhere on the tab closes it, like a browser tab.
        return ft.GestureDetector(
            content=chip,
            on_secondary_tap_down=lambda e, a=acct: self._close_tab(a))

    def _rebuild_tabs(self) -> None:
        self._tab_strip.controls = [self._tab_chip(a) for a in self._tabs]

    def _close_tab(self, acct: int) -> None:
        if acct in self._tabs:
            self._tabs.remove(acct)
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
        self._rebuild_tabs()
        if self.page:
            self.page.update()

    # ── messages ─────────────────────────────────────────────────────────────
    def _on_msg_scroll(self, e) -> None:
        with contextlib.suppress(Exception):
            self._max_scroll = e.max_scroll_extent or 0.0
            want = e.pixels < (e.max_scroll_extent or 0) - 90
            # While scrolled up, STOP following new messages (no forced jumps to the
            # end); resume following when back at the bottom or via the jump button.
            self._messages.auto_scroll = not want
            if self._jump_btn.visible != want:
                self._jump_btn.visible = want
                self._jump_btn.update()

    def _jump_to_latest(self) -> None:
        with contextlib.suppress(Exception):
            self._messages.auto_scroll = True
            self._messages.scroll_to(offset=self._max_scroll or 1000000, duration=200)
            self._jump_btn.visible = False
            self._jump_btn.update()

    def _romanize(self, text: str) -> str:
        """Pinyin (Chinese) / romaji (Japanese) of the original — matches the
        VRChat tab's reading line. Empty if not romanizable or not applicable."""
        if not text:
            return ""
        with contextlib.suppress(Exception):
            if re.search(r"[぀-ヿ]", text):        # kana -> Japanese
                from puripuly_heart.core.transliteration import to_romaji
                return to_romaji(text) or ""
            if re.search(r"[㐀-鿿]", text):        # Han ideographs -> Chinese
                from puripuly_heart.core.transliteration import to_pinyin_grouped
                return to_pinyin_grouped(text) or ""
        return ""

    def _needs_tr(self, text: str) -> bool:
        """True only when the text is in a different script than the reader —
        so we never translate English->English (which wastes DeepL and reads odd)."""
        if not text:
            return False
        has_cjk = bool(_CJK_RE.search(text))
        return (not has_cjk) if self._src_lang in _CJK_LANGS else has_cjk

    def _load_cache(self) -> None:
        with contextlib.suppress(Exception):
            if _CACHE_FILE.exists():
                self._tr_cache = json.loads(_CACHE_FILE.read_text(encoding="utf-8"))

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

    def _save_prefs(self) -> None:
        with contextlib.suppress(Exception):
            _PREFS_FILE.write_text(json.dumps({
                "show_pinyin": self._show_pinyin,
                "tr_incoming": self._tr_incoming,
                "tr_outgoing": self._tr_outgoing,
            }), encoding="utf-8")

    def _save_cache(self) -> None:
        with contextlib.suppress(Exception):
            if len(self._tr_cache) > 8000:
                self._tr_cache = dict(list(self._tr_cache.items())[-6000:])
            _CACHE_FILE.write_text(json.dumps(self._tr_cache, ensure_ascii=False),
                                   encoding="utf-8")

    def _tr_key(self, text: str) -> str:
        # Cache is keyed by TARGET language, else switching languages would serve
        # stale translations (and "retranslate" could never work).
        return f"{self._src_lang}|{text}"

    async def _tr(self, text: str, *, force: bool = False) -> str:
        if not text or not self._needs_tr(text):
            return text                       # already in my language — no DeepL
        key = self._tr_key(text)
        if not force and key in self._tr_cache:
            return self._tr_cache[key]        # cached from this or a past session
        if not force and text in self._tr_cache:   # legacy un-keyed entry
            return self._tr_cache[text]
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

    # Merge only rapid-fire lines: same sender AND within this many seconds AND the
    # merged text stays modest. Steam-style grouping — messages minutes apart must
    # NOT collapse into one mega-block (they'd also translate as one blob).
    _MERGE_GAP_S = 120
    _MERGE_MAX_CHARS = 280

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
        translated = self._needs_tr(orig) and (b.get("from_me") or self._tr_incoming)
        if orig and translated:
            # Matches the VRChat tab: pinyin/romaji (top), original in GRAY, then
            # the translation as the prominent line.
            noml = _URL_RE.sub("", orig).strip()   # don't romanize/translate the URL
            if self._show_pinyin:
                roman = self._romanize(noml)
                if roman:
                    out.append(ft.Text(roman, size=12.5, italic=True,
                                       color=_ACCENT, selectable=True))
            out.append(ft.Text(spans=_spans(orig, "#9aa0a6"), size=14, selectable=True))
            tr_ctrl = ft.Text("", size=14, weight=ft.FontWeight.W_500,
                              color=_TEXT_PRIMARY, selectable=True)
            b["_ctrl"] = tr_ctrl                    # the translation line, filled below
            out.append(tr_ctrl)
        elif orig:
            out.append(ft.Text(spans=_spans(orig), size=14, selectable=True))
        if b.get("emoticons"):
            out.append(ft.Row(
                [ft.Image(src=self._emoticon_url(n), width=28, height=28, fit=ft.ImageFit.CONTAIN)
                 for n in b["emoticons"]], spacing=3, wrap=True))
        for url in b.get("stickers", []):
            out.append(ft.Image(src=url, width=120, height=120, fit=ft.ImageFit.CONTAIN))
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
        self._last_block = {"from_me": b["from_me"], "name": b.get("name") or "", "col": col}
        return ft.Row([_avatar(b["avatar"]), col], spacing=8,
                      vertical_alignment=ft.CrossAxisAlignment.START)

    def _image_control(self, url: str) -> ft.Control:
        # Show ONLY the image (never the raw steamusercontent URL as text). Left-
        # click opens it full size; right-click copies the link, for when it's
        # wanted.
        img = ft.Image(src=url, fit=ft.ImageFit.CONTAIN, width=260, border_radius=8)
        return ft.Container(
            content=ft.GestureDetector(
                content=img, mouse_cursor=ft.MouseCursor.CLICK,
                on_tap=lambda e, u=url: self.page and self.page.launch_url(u),
                on_secondary_tap_down=lambda e, u=url: self._copy_link(u)),
            padding=ft.padding.only(top=2))

    def _copy_link(self, url: str) -> None:
        if not self.page:
            return
        with contextlib.suppress(Exception):
            self.page.set_clipboard(url)
            self.page.open(ft.SnackBar(ft.Text("Link copied"), duration=1400))

    async def _translate_block(self, b: dict, seq: int, *, force: bool = False) -> None:
        orig = b.get("text", "")
        tc = b.get("_ctrl")
        if not orig or tc is None:
            return
        if not b.get("from_me") and not self._tr_incoming:
            return                                  # incoming translation turned off
        noml = _URL_RE.sub("", orig).strip()       # translate the text, not the URL
        tr = await self._tr(noml, force=force)
        if seq != self._open_seq or not tr or tr == noml:
            return
        tc.spans = _spans(tr)
        with contextlib.suppress(Exception):
            tc.update()

    async def _render_history(self, messages: list, seq: int) -> None:
        blocks = self._coalesce(messages)
        if seq != self._open_seq:
            return
        self._pend = None                  # drop any half-buffered live lines
        self._messages.controls.clear()
        self._messages.auto_scroll = True  # fresh chat follows the newest message
        self._last_block = None            # fresh chat — don't coalesce across it
        self._hist_blocks = blocks
        for b in blocks:
            self._messages.controls.append(self._block_control(b))
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
        if self._pend and self._pend["key"] == key \
                and sum(len(t) for t in self._pend["texts"]) < self._MERGE_MAX_CHARS:
            self._pend["texts"].append(m.get("text", "") or "")
        else:
            await self._flush_pend()
            self._pend = {"key": key, "name": m.get("name", ""),
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
            "images": [], "stickers": [],
        })

    async def _render_live(self, m: dict) -> None:
        text, emos = _extract_emoticons((m.get("text", "") or "").strip())
        b = {"from_me": bool(m.get("from_me")), "name": m.get("name", ""),
             "avatar": m.get("avatar", ""), "text": text, "emoticons": emos,
             "images": m.get("images", []), "stickers": m.get("stickers", [])}
        lb = self._last_block
        if (lb and lb["from_me"] == b["from_me"] and lb["name"] == (b.get("name") or "")):
            # Same sender as the previous block → append the new lines to it instead
            # of starting a new avatar+name block (Steam-/VRChat-tab style grouping).
            with contextlib.suppress(Exception):
                lb["col"].controls.extend(self._message_body_controls(b))
        else:
            self._messages.controls.append(self._block_control(b))
        self._hist_blocks.append(b)
        if self.page:
            self.page.update()
        await self._translate_block(b, self._open_seq)

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
            self.page.run_task(self._read_loop)

    async def _try_open(self) -> bool:
        try:
            self._reader, self._writer = await asyncio.open_connection(_HOST, _PORT)
            return True
        except Exception:
            return False

    async def _cmd(self, obj: dict) -> None:
        if self._writer is None:
            return
        with contextlib.suppress(Exception):
            self._writer.write((json.dumps(obj, ensure_ascii=False) + "\n").encode("utf-8"))
            await self._writer.drain()

    def _set_typing(self, name: str) -> None:
        self._typing_text.value = f"{name} is typing…" if name else ""
        if self.page:
            with contextlib.suppress(Exception):
                self._typing_text.update()

    async def _open(self, acct: int) -> None:
        self._hide_ctx()
        self._open_seq += 1
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
        if self.page:
            self.page.update()
        await self._cmd({"cmd": "open", "acct": acct})

    async def _send(self) -> None:
        text = (self._entry.value or "").strip()
        if not text or not self._active:
            return
        self._entry.value = ""
        if self.page:
            self._entry.update()
        # Pull out :emoticon: shortcodes BEFORE translating — Steam renders them on
        # both ends, but a translator would mangle ":cyanheart:" into "cyanheart".
        clean, emos = _extract_emoticons(text)
        zh = clean
        if clean and self._tr_outgoing and callable(self.translate_message):
            with contextlib.suppress(Exception):
                r = await self.translate_message(clean, True)
                if r:
                    zh = r
        codes = " ".join(f":{n}:" for n in emos)
        out = (f"{zh} {codes}".strip() if zh else codes)
        if clean and zh != clean:
            self._tr_cache[self._tr_key(zh)] = clean   # own echo renders instantly
        await self._append_message({"from_me": True, "name": self._own_name,
                                    "avatar": self._own_avatar, "text": out})
        await self._cmd({"cmd": "send", "acct": self._active, "text": out})

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
            await asyncio.sleep(1.0)
            await self._connect()
            if self._active is not None:
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
            if ev.get("emoticons"):
                self._emoticons = list(ev.get("emoticons"))
            self._update_own_header()
        elif kind == "friends":
            self._friends = {int(i["acct"]): i for i in ev.get("items", [])}
            self._got_friends = True
            for a, i in self._friends.items():
                if a not in self._search_index:
                    self._search_index[a] = _search_key(i.get("name", ""))
            self._hide_loading()
            self._rebuild_friends()
            self._rebuild_tabs()
            if self._active in self._friends:
                self._set_chat_head(self._friends[self._active])
            if self.page:
                self.page.update()
        elif kind == "history":
            if int(ev.get("acct", 0)) == self._active:
                await self._render_history(ev.get("messages", []), self._open_seq)
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

    async def shutdown(self) -> None:
        with contextlib.suppress(Exception):
            if self._writer:
                self._writer.close()
        with contextlib.suppress(Exception):
            if self._proc and self._proc.returncode is None:
                self._proc.terminate()
