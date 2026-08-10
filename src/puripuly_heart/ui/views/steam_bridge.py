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
from pathlib import Path

import flet as ft

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

_LANGS = [
    ("en", "English"), ("zh-CN", "中文(简)"), ("zh-TW", "中文(繁)"), ("ja", "日本語"),
    ("ko", "한국어"), ("es", "Español"), ("fr", "Français"), ("de", "Deutsch"),
    ("ru", "Русский"), ("pt", "Português"), ("it", "Italiano"), ("id", "Indonesia"),
    ("vi", "Tiếng Việt"), ("th", "ไทย"), ("ar", "العربية"),
]
_LANG_LABEL = dict(_LANGS)
_EMOJIS = ["😀", "😂", "🥰", "😊", "😎", "😉", "😢", "😭", "😡", "👍", "👎", "🙏",
           "👋", "❤️", "💔", "🔥", "✨", "🎉", "😴", "🤔", "😳", "🥺", "😤", "💀"]

_BRIDGE_ROOT = Path(
    r"C:\Users\Owner\AppData\Local\Temp\claude\E--Programming-Claude"
    r"\6d59879c-8d48-4d69-98ef-fc5f025d4ef6\scratchpad"
)
_DAEMON_PY = _BRIDGE_ROOT / "steam_bridge" / "daemon.py"
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


def _spans(text: str) -> list:
    out = []
    for part in _URL_RE.split(text or ""):
        if not part:
            continue
        if _URL_RE.fullmatch(part):
            out.append(ft.TextSpan(
                part, ft.TextStyle(color=_LINK, decoration=ft.TextDecoration.UNDERLINE),
                url=part))
        else:
            out.append(ft.TextSpan(part, ft.TextStyle(color=_TEXT_PRIMARY)))
    return out or [ft.TextSpan("", ft.TextStyle(color=_TEXT_PRIMARY))]


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
        self._active = None
        self._open_seq = 0
        self._friends: dict[int, dict] = {}
        self._tabs: list[int] = []
        self._filter = ""
        self._tr_cache: dict[str, str] = {}
        self._started = False
        self._prewarmed = False
        self._got_friends = False

        # left: search + friends list
        self._search = ft.TextField(
            hint_text="Search friends", prefix_icon=ft.Icons.SEARCH, dense=True,
            text_size=12, color=_TEXT_PRIMARY, border=ft.InputBorder.NONE,
            bgcolor=_BG_INPUT, border_radius=8, hint_style=ft.TextStyle(color=_TEXT_FAINT),
            content_padding=ft.padding.symmetric(horizontal=8, vertical=6),
            on_change=lambda e: self._on_search(e.control.value))
        self._friends_list = ft.ListView(expand=True, spacing=1, padding=6)
        self._left_panel = ft.Container(
            width=252, bgcolor=_BG_SIDE,
            content=ft.Column([
                ft.Container(content=self._search,
                             padding=ft.padding.only(left=8, right=8, top=8, bottom=4)),
                self._friends_list,
            ], spacing=0, expand=True))

        # right: chat pane
        self._chat_headinfo = ft.Container()
        self._from_lbl = ft.Text(_LANG_LABEL.get(self._src_lang, "English"),
                                 size=12, color=_TEXT_PRIMARY, weight=ft.FontWeight.W_500)
        self._to_lbl = ft.Text(_LANG_LABEL.get(self._tgt_lang, "中文(简)"),
                               size=12, color=_TEXT_PRIMARY, weight=ft.FontWeight.W_500)
        lang_box = ft.Row([
            self._lang_button("from"),
            ft.Icon(ft.Icons.ARROW_RIGHT_ALT, size=15, color=_TEXT_FAINT),
            self._lang_button("to"),
        ], spacing=4, tight=True)
        top_bar = ft.Container(
            content=ft.Row([self._chat_headinfo, ft.Container(expand=True), lang_box],
                           spacing=6, vertical_alignment=ft.CrossAxisAlignment.CENTER),
            padding=ft.padding.symmetric(horizontal=12, vertical=6), bgcolor=_BG_MAIN)

        self._tab_strip = ft.Row([], spacing=4, scroll=ft.ScrollMode.AUTO)
        self._tab_bar = ft.Container(content=self._tab_strip, visible=False,
                                     padding=ft.padding.only(left=8, right=8, top=4, bottom=2),
                                     bgcolor=_BG_MAIN)

        self._messages = ft.ListView(expand=True, spacing=10, padding=14, auto_scroll=True)
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
        emoji_btn = ft.PopupMenuButton(
            icon=ft.Icons.EMOJI_EMOTIONS_OUTLINED, icon_size=20, icon_color=_TEXT_FAINT,
            tooltip="Emoji", menu_position=ft.PopupMenuPosition.OVER,
            items=[ft.PopupMenuItem(text=e, on_click=lambda ev, em=e: self._insert_emoji(em))
                   for e in _EMOJIS])
        input_row = ft.Container(
            content=ft.Row([
                self._entry,
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
            self._tab_bar,
            ft.Container(content=ft.SelectionArea(content=self._messages), expand=True),
            typing_row,
            ft.Divider(height=1, color=_DIVIDER, thickness=1),
            input_row,
        ], spacing=0, expand=True)

        main_row = ft.Row([
            self._left_panel,
            ft.VerticalDivider(width=1, color=_DIVIDER, thickness=1),
            ft.Container(content=chat_area, expand=True),
        ], spacing=0, expand=True, vertical_alignment=ft.CrossAxisAlignment.STRETCH)

        # never-blank loading overlay
        self._loading = ft.Container(
            expand=True, bgcolor=_BG_MAIN, alignment=ft.alignment.center, visible=True,
            content=ft.Column([
                ft.ProgressRing(width=34, height=34, stroke_width=3, color=_ACCENT),
                ft.Text("Loading your Steam friends…", size=13, color=_TEXT_FAINT),
            ], spacing=14, horizontal_alignment=ft.CrossAxisAlignment.CENTER,
               alignment=ft.MainAxisAlignment.CENTER))

        # right-click context menu (cursor-positioned)
        self._ctx_menu = ft.Container(visible=False, bgcolor=_BG_MENU, border_radius=8,
                                      border=ft.border.all(1, "#4b4c4f"), padding=4,
                                      width=190, left=60, top=60,
                                      shadow=ft.BoxShadow(blur_radius=14, color="#88000000",
                                                          offset=ft.Offset(0, 4)))
        self.content = ft.Stack([main_row, self._loading, self._ctx_menu], expand=True)

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
        acts = [("Open chat", lambda: self.page.run_task(self._open, acct)),
                ("View Steam profile", lambda: self._open_profile(acct))]
        if f.get("ingame") and f.get("appid"):
            ap = f["appid"]
            acts.append(("Game store page",
                         lambda: self._launch(f"https://store.steampowered.com/app/{ap}")))
        return acts

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
        if self.page:
            self.page.update()

    def _hide_ctx(self) -> None:
        if self._ctx_menu.visible:
            self._ctx_menu.visible = False
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

    def _friend_row(self, f: dict) -> ft.Control:
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
        body = ft.Row([
            _avatar(f.get("avatar", ""), 34),
            ft.Column([name_row, ft.Text(sub, size=11, color=sub_color, max_lines=1,
                                         overflow=ft.TextOverflow.ELLIPSIS)],
                      spacing=0, tight=True, expand=True),
        ], spacing=10, vertical_alignment=ft.CrossAxisAlignment.CENTER)
        gd = ft.GestureDetector(
            content=body, expand=True,
            on_tap=lambda e, a=acct: self.page.run_task(self._open, a),
            on_secondary_tap_down=lambda e, fr=f: self._show_ctx(e, fr))
        return ft.Container(
            content=gd,
            padding=ft.padding.symmetric(horizontal=8, vertical=5),
            border_radius=6, bgcolor=_BG_SEL if acct == self._active else ft.Colors.TRANSPARENT)

    def _section_header(self, label: str, n: int) -> ft.Control:
        return ft.Container(
            content=ft.Text(f"{label}  {n}", size=11, weight=ft.FontWeight.BOLD, color=_SECTION),
            padding=ft.padding.only(left=8, top=10, bottom=3))

    def _game_header(self, game: str, n: int, appid: int) -> ft.Control:
        row = []
        if appid:
            row.append(ft.Image(
                src=f"https://cdn.cloudflare.steamstatic.com/steam/apps/{appid}/capsule_sm_120.jpg",
                height=20, width=44, fit=ft.ImageFit.COVER, border_radius=3))
        row.append(ft.Text(f"{game}  {n}", size=11, weight=ft.FontWeight.BOLD, color=_C_INGAME))
        return ft.Container(
            content=ft.Row(row, spacing=6, tight=True,
                           vertical_alignment=ft.CrossAxisAlignment.CENTER),
            padding=ft.padding.only(left=8, top=10, bottom=3))

    def _rebuild_friends(self) -> None:
        items = list(self._friends.values())
        if self._filter:
            items = [f for f in items if self._filter in (f.get("name") or "").lower()]

        def sk(f):
            return (0 if f.get("ingame") else (1 if f.get("state") else 2),
                    (f.get("name") or "").lower())

        recent = sorted((f for f in items if f.get("last_chat")),
                        key=lambda f: -(f.get("last_chat") or 0))[:6]
        recent_ids = {int(f["acct"]) for f in recent}

        favs, cats, ingame, online, offline = [], {}, [], [], []
        for f in items:
            if int(f["acct"]) in recent_ids and not self._filter:
                continue
            if f.get("fav"):
                favs.append(f)
            elif f.get("groups"):
                cats.setdefault(f["groups"][0], []).append(f)
            elif f.get("ingame"):
                ingame.append(f)
            elif f.get("state"):
                online.append(f)
            else:
                offline.append(f)
        self._friends_list.controls.clear()

        def add_section(label, rows, presorted=False):
            if not rows:
                return
            self._friends_list.controls.append(self._section_header(label, len(rows)))
            for f in (rows if presorted else sorted(rows, key=sk)):
                self._friends_list.controls.append(self._friend_row(f))

        if not self._filter:
            add_section("RECENT", recent, presorted=True)
        add_section("FAVORITES", favs)
        for name in sorted(cats):
            add_section(name.upper(), cats[name])
        # In-game friends grouped by the game they're playing (Steam-style).
        by_game: dict[str, list] = {}
        for f in ingame:
            by_game.setdefault(f.get("game") or "In-Game", []).append(f)
        for game in sorted(by_game):
            rows = by_game[game]
            gappid = next((f.get("appid") for f in rows if f.get("appid")), 0)
            self._friends_list.controls.append(self._game_header(game, len(rows), gappid))
            for f in sorted(rows, key=sk):
                self._friends_list.controls.append(self._friend_row(f))
        add_section("ONLINE", online)
        add_section("OFFLINE", offline)

    def _set_chat_head(self, f: dict | None) -> None:
        if not f:
            self._chat_headinfo.content = None
            self._chat_headinfo.on_click = None
            return
        state, ingame = int(f.get("state", 0)), bool(f.get("ingame"))
        sub = (f.get("game") or "In-Game") if ingame else _STATE_LABEL.get(state, "Offline")
        self._chat_headinfo.content = ft.Row([
            _avatar(f.get("avatar", ""), 30),
            ft.Column([
                ft.Row([ft.Text(f.get("name") or "Steam friend", size=14,
                                weight=ft.FontWeight.BOLD, color=_TEXT_PRIMARY),
                        *self._status_badges(f)], spacing=4, tight=True),
                ft.Text(sub, size=11, color=_name_color(state, ingame)),
            ], spacing=0, tight=True),
        ], spacing=8, vertical_alignment=ft.CrossAxisAlignment.CENTER)
        acct = int(f["acct"])
        self._chat_headinfo.on_click = lambda e, a=acct: self._open_profile(a)
        self._chat_headinfo.tooltip = "View Steam profile"

    # ── chat tabs ────────────────────────────────────────────────────────────
    def _tab_chip(self, acct: int) -> ft.Control:
        f = self._friends.get(acct, {})
        active = (acct == self._active)
        name = (f.get("name") or "Chat")
        return ft.Container(
            content=ft.Row([
                _avatar(f.get("avatar", ""), 18),
                ft.Text(name, size=12, color=_TEXT_PRIMARY if active else _TEXT_FAINT,
                        max_lines=1, overflow=ft.TextOverflow.ELLIPSIS,
                        weight=ft.FontWeight.W_500 if active else ft.FontWeight.NORMAL),
                ft.IconButton(ft.Icons.CLOSE, icon_size=13, icon_color=_TEXT_FAINT,
                              tooltip="Close", width=22, height=22,
                              on_click=lambda e, a=acct: self._close_tab(a),
                              style=ft.ButtonStyle(padding=ft.padding.all(0))),
            ], spacing=6, tight=True, vertical_alignment=ft.CrossAxisAlignment.CENTER),
            padding=ft.padding.only(left=8, right=2, top=3, bottom=3),
            border_radius=6, bgcolor=_BG_SEL if active else _BG_MENU,
            on_click=lambda e, a=acct: self.page.run_task(self._open, a))

    def _rebuild_tabs(self) -> None:
        self._tab_strip.controls = [self._tab_chip(a) for a in self._tabs]
        self._tab_bar.visible = bool(self._tabs)

    def _close_tab(self, acct: int) -> None:
        if acct in self._tabs:
            self._tabs.remove(acct)
        if acct == self._active:
            if self._tabs:
                self.page.run_task(self._open, self._tabs[-1])
                return
            self._active = None
            self._set_chat_head(None)
            self._messages.controls.clear()
            self._entry.disabled = True
        self._rebuild_tabs()
        if self.page:
            self.page.update()

    # ── messages ─────────────────────────────────────────────────────────────
    async def _tr(self, text: str) -> str:
        if not text:
            return ""
        if text in self._tr_cache:
            return self._tr_cache[text]
        out = text
        if callable(self.translate_message):
            with contextlib.suppress(Exception):
                r = await self.translate_message(text, False)
                if r:
                    out = r
        self._tr_cache[text] = out
        return out

    def _coalesce(self, messages: list) -> list:
        blocks = []
        for m in messages:
            fm = bool(m.get("from_me"))
            if blocks and blocks[-1]["from_me"] == fm and not blocks[-1]["stickers"] \
                    and not blocks[-1]["images"]:
                b = blocks[-1]
            else:
                b = {"from_me": fm, "name": m.get("name", ""), "avatar": m.get("avatar", ""),
                     "texts": [], "images": [], "stickers": []}
                blocks.append(b)
            if m.get("text"):
                b["texts"].append(m["text"])
            b["images"] += m.get("images", [])
            b["stickers"] += m.get("stickers", [])
        for b in blocks:
            b["text"] = " ".join(t.strip() for t in b["texts"] if t.strip())
        return blocks

    def _block_control(self, b: dict) -> ft.Control:
        if b["from_me"]:
            name_color = _name_color(self._own_state, False)
            name = b.get("name") or self._own_name
        else:
            f = self._friends.get(self._active or 0, {})
            name_color = _name_color(int(f.get("state", 1)), bool(f.get("ingame")))
            name = b.get("name") or "Them"
        col = ft.Column(spacing=2, tight=True, expand=True)
        col.controls.append(ft.Text(name, size=12, weight=ft.FontWeight.BOLD, color=name_color))
        text_ctrl = ft.Text(spans=_spans(b.get("text", "")), size=14, selectable=True)
        b["_ctrl"] = text_ctrl
        if b.get("text"):
            col.controls.append(text_ctrl)
        for url in b.get("stickers", []):
            col.controls.append(ft.Image(src=url, width=120, height=120, fit=ft.ImageFit.CONTAIN))
        for url in b.get("images", []):
            col.controls.append(ft.Container(
                content=ft.Image(src=url, fit=ft.ImageFit.CONTAIN, width=260, border_radius=8),
                padding=ft.padding.only(top=2)))
        return ft.Row([_avatar(b["avatar"]), col], spacing=8,
                      vertical_alignment=ft.CrossAxisAlignment.START)

    async def _translate_block(self, b: dict, seq: int) -> None:
        if not b.get("text"):
            return
        tr = await self._tr(b["text"])
        if seq != self._open_seq or tr == b["text"]:
            return
        tc = b.get("_ctrl")
        if tc is not None:
            tc.spans = _spans(tr)
            tc.tooltip = b["text"]
            with contextlib.suppress(Exception):
                tc.update()

    async def _render_history(self, messages: list, seq: int) -> None:
        blocks = self._coalesce(messages)
        if seq != self._open_seq:
            return
        self._messages.controls.clear()
        for b in blocks:
            self._messages.controls.append(self._block_control(b))
        if self.page:
            self.page.update()
        for b in blocks:
            if seq != self._open_seq:
                return
            await self._translate_block(b, seq)

    async def _append_message(self, m: dict) -> None:
        b = {"from_me": bool(m.get("from_me")), "name": m.get("name", ""),
             "avatar": m.get("avatar", ""), "text": (m.get("text", "") or "").strip(),
             "images": m.get("images", []), "stickers": m.get("stickers", [])}
        self._messages.controls.append(self._block_control(b))
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
        self._messages.controls.clear()
        self._entry.disabled = True
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
        zh = text
        if callable(self.translate_message):
            with contextlib.suppress(Exception):
                r = await self.translate_message(text, True)
                if r:
                    zh = r
        self._tr_cache[zh] = text
        await self._append_message({"from_me": True, "name": self._own_name,
                                    "avatar": self._own_avatar, "text": zh})
        await self._cmd({"cmd": "send", "acct": self._active, "text": zh})

    async def _read_loop(self) -> None:
        with contextlib.suppress(Exception):
            async for raw in self._reader:
                try:
                    ev = json.loads(raw.decode("utf-8").strip() or "{}")
                except Exception:
                    continue
                await self._handle(ev)

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
        elif kind == "friends":
            self._friends = {int(i["acct"]): i for i in ev.get("items", [])}
            self._got_friends = True
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
            if ev.get("ok"):
                self._active = int(ev.get("acct", 0))
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
