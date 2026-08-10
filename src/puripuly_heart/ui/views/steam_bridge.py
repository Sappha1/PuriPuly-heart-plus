# -*- coding: utf-8 -*-
"""Steam chat, embedded inside PuriPuly (beta, local only). NEVER ships.

Drives Steam's in-memory web-chat model through a headless helper (no window),
rendered natively in Flet as a Steam-like client: the full friends list grouped
into Favorites / categories / In-Game / Online / Offline with status colors,
game names and VR/mobile/snooze badges; a chat pane that coalesces each person's
consecutive lines into one block, shows the translation with the original on
hover, renders stickers / images / clickable links, and lets you pick languages
right in the tab. Translations are cached; open is guarded so switching chats
never leaves a blank pane.
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
_BORDER_INPUT = "#5b5c5f"
_DIVIDER = "#4b4c4f"
_TEXT_PRIMARY = "#f2f2f2"
_TEXT_FAINT = "#7f8084"
_SUB = "#8fa9c4"
_ACCENT = "#48a495"
_TOGGLE_ON = "#48a495"
_LINK = "#6dc0e8"
_C_INGAME = "#a1cd5e"    # green: in a game
_C_ONLINE = "#6dc0e8"    # blue: online
_C_OFFLINE = "#7a7c80"   # gray: offline
_SECTION = "#8a8c90"

_FLAG_MOBILE = 0x200
_FLAG_VR = 0x800
_STEAMID64_BASE = 76561197960265728

_URL_RE = re.compile(r"(https?://[^\s]+)")

# Curated language set for the in-tab picker (code -> label).
_LANGS = [
    ("en", "English"), ("zh-CN", "Chinese (Simp.)"), ("zh-TW", "Chinese (Trad.)"),
    ("ja", "Japanese"), ("ko", "Korean"), ("es", "Spanish"), ("fr", "French"),
    ("de", "German"), ("ru", "Russian"), ("pt", "Portuguese"), ("it", "Italian"),
    ("id", "Indonesian"), ("vi", "Vietnamese"), ("th", "Thai"), ("ar", "Arabic"),
]
_EMOJIS = ["😀", "😂", "🥰", "😊", "😎", "😉", "😢", "😭", "😡", "👍", "👎", "🙏",
           "👋", "❤️", "💔", "🔥", "✨", "🎉", "😴", "🤔", "😳", "🥺", "😤", "💀"]


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


class SteamBridgeView(ft.Container):
    def __init__(self) -> None:
        super().__init__(expand=True, bgcolor=_BG_MAIN, padding=0)
        self.translate_message = None       # async (text, to_them) -> str, from app
        self.on_toggle_sidebar = None        # collapses the app's left sidebar
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
        self._tr_cache: dict[str, str] = {}
        self._started = False
        self._prewarmed = False

        # left: friends list
        self._friends_list = ft.ListView(expand=True, spacing=1, padding=6)
        self._left_panel = ft.Container(width=252, bgcolor=_BG_SIDE,
                                        content=self._friends_list)
        self._left_divider = ft.VerticalDivider(width=1, color=_DIVIDER, thickness=1)

        # right: chat pane
        self._collapse_btn = ft.IconButton(
            ft.Icons.CHEVRON_LEFT, icon_size=20, icon_color=_TEXT_FAINT,
            tooltip="Collapse the app sidebar for more room",
            on_click=lambda e: self._toggle_sidebar(),
            style=ft.ButtonStyle(overlay_color=ft.Colors.TRANSPARENT))
        self._chat_headinfo = ft.Container()   # clickable avatar+name (-> profile)
        self._from_dd = ft.Dropdown(
            value=self._src_lang, width=118, text_size=11, dense=True,
            border_color=_BORDER_INPUT, bgcolor=_BG_INPUT, color=_TEXT_PRIMARY,
            content_padding=6, tooltip="Your language",
            options=[ft.dropdown.Option(c, l) for c, l in _LANGS],
            on_change=lambda e: setattr(self, "_src_lang", e.control.value))
        self._to_dd = ft.Dropdown(
            value=self._tgt_lang, width=118, text_size=11, dense=True,
            border_color=_BORDER_INPUT, bgcolor=_BG_INPUT, color=_TEXT_PRIMARY,
            content_padding=6, tooltip="Their language",
            options=[ft.dropdown.Option(c, l) for c, l in _LANGS],
            on_change=lambda e: setattr(self, "_tgt_lang", e.control.value))
        top_bar = ft.Container(
            content=ft.Row([
                self._collapse_btn,
                self._chat_headinfo,
                ft.Container(expand=True),
                self._from_dd,
                ft.Icon(ft.Icons.ARROW_RIGHT_ALT, size=16, color=_TEXT_FAINT),
                self._to_dd,
            ], spacing=6, vertical_alignment=ft.CrossAxisAlignment.CENTER),
            padding=ft.padding.symmetric(horizontal=6, vertical=4), bgcolor=_BG_MAIN)

        self._messages = ft.ListView(expand=True, spacing=8, padding=14, auto_scroll=True)
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

        self._typing_text = ft.Text("", size=12, color=_TEXT_FAINT, italic=True)
        typing_row = ft.Container(content=self._typing_text,
                                  padding=ft.padding.only(left=16, top=2, bottom=0),
                                  height=18)

        chat_area = ft.Column([
            top_bar,
            ft.Divider(height=1, color=_DIVIDER, thickness=1),
            ft.Container(content=ft.SelectionArea(content=self._messages), expand=True),
            typing_row,
            ft.Divider(height=1, color=_DIVIDER, thickness=1),
            input_row,
        ], spacing=0, expand=True)

        self.content = ft.Row([
            self._left_panel, self._left_divider,
            ft.Container(content=chat_area, expand=True),
        ], spacing=0, expand=True, vertical_alignment=ft.CrossAxisAlignment.STRETCH)

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

    def _toggle_sidebar(self) -> None:
        if callable(self.on_toggle_sidebar):
            with contextlib.suppress(Exception):
                self.on_toggle_sidebar()

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
        clickable = ft.Container(
            content=ft.Row([
                _avatar(f.get("avatar", ""), 34),
                ft.Column([name_row, ft.Text(sub, size=11, color=sub_color, max_lines=1,
                                             overflow=ft.TextOverflow.ELLIPSIS)],
                          spacing=0, tight=True, expand=True),
            ], spacing=10, vertical_alignment=ft.CrossAxisAlignment.CENTER),
            expand=True, ink=True,
            on_click=lambda e, a=acct: self.page.run_task(self._open, a))
        menu_items = [
            ft.PopupMenuItem(text="Open chat",
                             on_click=lambda e, a=acct: self.page.run_task(self._open, a)),
            ft.PopupMenuItem(text="View Steam profile",
                             on_click=lambda e, a=acct: self._open_profile(a)),
        ]
        if ingame and f.get("appid"):
            menu_items.append(ft.PopupMenuItem(
                text="Game store page",
                on_click=lambda e, ap=f.get("appid"): self._launch(
                    f"https://store.steampowered.com/app/{ap}")))
        menu = ft.PopupMenuButton(icon=ft.Icons.MORE_VERT, icon_size=16,
                                  icon_color=_TEXT_FAINT, items=menu_items, tooltip="More")
        return ft.Container(
            content=ft.Row([clickable, menu], spacing=0,
                           vertical_alignment=ft.CrossAxisAlignment.CENTER),
            padding=ft.padding.only(left=8, right=0, top=5, bottom=5),
            border_radius=6, bgcolor=_BG_SEL if acct == self._active else ft.Colors.TRANSPARENT)

    def _section_header(self, label: str, n: int) -> ft.Control:
        return ft.Container(
            content=ft.Text(f"{label}  {n}", size=11, weight=ft.FontWeight.BOLD, color=_SECTION),
            padding=ft.padding.only(left=8, top=10, bottom=3))

    def _rebuild_friends(self) -> None:
        items = list(self._friends.values())

        def sk(f):
            return (0 if f.get("ingame") else (1 if f.get("state") else 2),
                    (f.get("name") or "").lower())

        favs, cats, ingame, online, offline = [], {}, [], [], []
        for f in items:
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

        def add_section(label, rows):
            if not rows:
                return
            self._friends_list.controls.append(self._section_header(label, len(rows)))
            for f in sorted(rows, key=sk):
                self._friends_list.controls.append(self._friend_row(f))

        add_section("FAVORITES", favs)
        for name in sorted(cats):
            add_section(name.upper(), cats[name])
        add_section("IN-GAME", ingame)
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

    # ── messages ─────────────────────────────────────────────────────────────
    async def _tr(self, text: str) -> str:
        """Translate into the reader's language, cached."""
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
            if blocks and blocks[-1]["from_me"] == fm:
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
            b["text"] = "\n".join(b["texts"])
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
        primary = b.get("primary", "")
        secondary = b.get("secondary", "")
        if primary:
            col.controls.append(ft.Text(
                spans=_spans(primary), size=14, selectable=True,
                tooltip=(secondary if secondary and secondary != primary else None)))
        for url in b.get("stickers", []):
            col.controls.append(ft.Image(src=url, width=120, height=120,
                                         fit=ft.ImageFit.CONTAIN))
        for url in b.get("images", []):
            col.controls.append(ft.Container(
                content=ft.Image(src=url, fit=ft.ImageFit.CONTAIN, width=260, border_radius=8),
                padding=ft.padding.only(top=2)))
        av = _avatar(b["avatar"])
        return ft.Row([av, col], spacing=8, vertical_alignment=ft.CrossAxisAlignment.START)

    async def _render_history(self, messages: list, seq: int) -> None:
        blocks = self._coalesce(messages)
        for b in blocks:
            b["secondary"] = b["text"]
            b["primary"] = await self._tr(b["text"]) if b["text"] else ""
            if seq != self._open_seq:        # a newer open superseded this render
                return
        if seq != self._open_seq:
            return
        self._messages.controls.clear()
        for b in blocks:
            self._messages.controls.append(self._block_control(b))
        if self.page:
            self.page.update()

    async def _append_message(self, m: dict) -> None:
        b = {"from_me": bool(m.get("from_me")), "name": m.get("name", ""),
             "avatar": m.get("avatar", ""), "text": m.get("text", ""),
             "images": m.get("images", []), "stickers": m.get("stickers", [])}
        b["secondary"] = b["text"]
        b["primary"] = await self._tr(b["text"]) if b["text"] else ""
        self._messages.controls.append(self._block_control(b))
        if self.page:
            self.page.update()

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
        self._typing_text.value = f"{name} is typing a message…" if name else ""
        if self.page:
            with contextlib.suppress(Exception):
                self._typing_text.update()

    async def _open(self, acct: int) -> None:
        self._open_seq += 1
        self._active = acct
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
        # translate my text into their language to send
        zh = text
        if callable(self.translate_message):
            with contextlib.suppress(Exception):
                r = await self.translate_message(text, True)
                if r:
                    zh = r
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

    async def _handle(self, ev: dict) -> None:
        kind = ev.get("ev")
        if kind == "own":
            self._own = int(ev.get("acct", 0))
            self._own_name = ev.get("name") or "You"
            self._own_avatar = ev.get("avatar", "") or self._own_avatar
            self._own_state = int(ev.get("state", 1) or 1)
        elif kind == "friends":
            self._friends = {int(i["acct"]): i for i in ev.get("items", [])}
            self._rebuild_friends()
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
                self._set_typing("")     # they sent it — stop "typing…"
                await self._append_message(ev.get("message", {}))

    async def shutdown(self) -> None:
        with contextlib.suppress(Exception):
            if self._writer:
                self._writer.close()
        with contextlib.suppress(Exception):
            if self._proc and self._proc.returncode is None:
                self._proc.terminate()
