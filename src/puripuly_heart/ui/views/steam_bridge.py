# -*- coding: utf-8 -*-
"""Steam chat, embedded inside PuriPuly (beta, local only). NEVER ships.

Drives Steam's in-memory web-chat model through a headless helper (no window),
and renders it natively in Flet as a Steam-like client: the full friends list
grouped into Favorites / your categories / In-Game / Online / Offline with status
colors and game names, a chat pane with grouped messages, avatars, image embeds,
clickable links, and live two-way translation via the app's own translator.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import re
import socket
from pathlib import Path

import flet as ft

# Dashboard theme values so the tab matches the rest of the app.
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
# Steam-style status colors.
_C_INGAME = "#a1cd5e"   # green: in a game
_C_ONLINE = "#6dc0e8"   # blue: online
_C_OFFLINE = "#7a7c80"  # gray: offline
_SECTION = "#8a8c90"

_URL_RE = re.compile(r"(https?://[^\s]+)")

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


def _spans(text: str, base_color: str) -> list:
    """Split text into TextSpans, making URLs clickable."""
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
        # injected by the app
        self.translate_message = None       # async (text, to_them) -> str
        self.on_toggle_sidebar = None        # collapses the app's left sidebar
        self._proc = None
        self._reader = None
        self._writer = None
        self._own = 0
        self._own_avatar = ""
        self._active = None
        self._pending = None
        self._friends: dict[int, dict] = {}
        self._last_key = None                # message grouping (sender continuity)
        self._started = False
        self._prewarmed = False

        # ── left: Steam-style friends list ───────────────────────────────────
        self._friends_list = ft.ListView(expand=True, spacing=1, padding=6)
        self._left_panel = ft.Container(width=248, bgcolor=_BG_SIDE,
                                        content=self._friends_list)
        self._left_divider = ft.VerticalDivider(width=1, color=_DIVIDER, thickness=1)

        # ── right: chat pane ─────────────────────────────────────────────────
        self._collapse_btn = ft.IconButton(
            ft.Icons.CHEVRON_LEFT, icon_size=20, icon_color=_TEXT_FAINT,
            tooltip="Collapse the app sidebar for more room",
            on_click=lambda e: self._toggle_sidebar(),
            style=ft.ButtonStyle(overlay_color=ft.Colors.TRANSPARENT))
        self._chat_head = ft.Row([self._collapse_btn], spacing=8,
                                 vertical_alignment=ft.CrossAxisAlignment.CENTER)
        top_bar = ft.Container(content=self._chat_head,
                               padding=ft.padding.symmetric(horizontal=6, vertical=4),
                               bgcolor=_BG_MAIN)

        self._messages = ft.ListView(expand=True, spacing=2, padding=14, auto_scroll=True)
        self._entry = ft.TextField(
            hint_text="Type message to send", disabled=True,
            border=ft.InputBorder.OUTLINE, border_color=_BORDER_INPUT,
            focused_border_color=_TOGGLE_ON, text_size=13, color=_TEXT_PRIMARY,
            hint_style=ft.TextStyle(color=_TEXT_FAINT, italic=True),
            expand=True, multiline=True, min_lines=2, max_lines=4, shift_enter=True,
            bgcolor=_BG_INPUT, border_radius=8,
            content_padding=ft.padding.symmetric(horizontal=12, vertical=8),
            on_submit=lambda e: self.page.run_task(self._send))
        input_row = ft.Container(
            content=ft.Row([
                self._entry,
                ft.IconButton(ft.Icons.SEND_ROUNDED, icon_size=18, icon_color=_TOGGLE_ON,
                              on_click=lambda e: self.page.run_task(self._send),
                              style=ft.ButtonStyle(overlay_color=ft.Colors.TRANSPARENT,
                                                   padding=ft.padding.all(8))),
            ], spacing=6, vertical_alignment=ft.CrossAxisAlignment.CENTER),
            padding=ft.padding.symmetric(horizontal=8, vertical=6), bgcolor=_BG_MAIN)

        chat_area = ft.Column([
            top_bar,
            ft.Divider(height=1, color=_DIVIDER, thickness=1),
            ft.Container(content=self._messages, expand=True),
            ft.Divider(height=1, color=_DIVIDER, thickness=1),
            input_row,
        ], spacing=0, expand=True)

        self.content = ft.Row([
            self._left_panel,
            self._left_divider,
            ft.Container(content=chat_area, expand=True),
        ], spacing=0, expand=True, vertical_alignment=ft.CrossAxisAlignment.STRETCH)

    # ── lifecycle ────────────────────────────────────────────────────────────
    def prewarm(self) -> None:
        if self._prewarmed:
            return
        self._prewarmed = True
        with contextlib.suppress(Exception):
            s = socket.socket()
            s.settimeout(0.25)
            try:
                s.connect((_HOST, _PORT))
                s.close()
                return
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

    # ── friends list ─────────────────────────────────────────────────────────
    def _friend_row(self, f: dict) -> ft.Control:
        acct = int(f["acct"])
        state = int(f.get("state", 0))
        ingame = bool(f.get("ingame"))
        selected = (acct == self._active)
        if ingame:
            sub, sub_color = (f.get("game") or "In-Game"), _C_INGAME
        elif state:
            sub, sub_color = _STATE_LABEL.get(state, "Online"), _TEXT_FAINT
        else:
            sub, sub_color = "Offline", _TEXT_FAINT
        return ft.Container(
            content=ft.Row([
                _avatar(f.get("avatar", ""), 34),
                ft.Column([
                    ft.Text(f.get("name") or "Steam friend", size=13,
                            weight=ft.FontWeight.W_500,
                            color=_name_color(state, ingame),
                            max_lines=1, overflow=ft.TextOverflow.ELLIPSIS),
                    ft.Text(sub, size=11, color=sub_color,
                            max_lines=1, overflow=ft.TextOverflow.ELLIPSIS),
                ], spacing=0, tight=True, expand=True),
            ], spacing=10, vertical_alignment=ft.CrossAxisAlignment.CENTER),
            padding=ft.padding.symmetric(horizontal=8, vertical=5),
            border_radius=6, ink=True,
            bgcolor=_BG_SEL if selected else ft.Colors.TRANSPARENT,
            on_click=lambda e, a=acct: self.page.run_task(self._open, a))

    def _section_header(self, label: str, n: int) -> ft.Control:
        return ft.Container(
            content=ft.Text(f"{label}  {n}", size=11, weight=ft.FontWeight.BOLD,
                            color=_SECTION),
            padding=ft.padding.only(left=8, top=10, bottom=3))

    def _rebuild_friends(self) -> None:
        items = list(self._friends.values())
        # Sort key within a section: in-game, then online, then name.
        def sk(f):
            return (0 if f.get("ingame") else (1 if f.get("state") else 2),
                    (f.get("name") or "").lower())
        # Single-assignment into sections by priority.
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
        head = [self._collapse_btn]
        if f:
            state, ingame = int(f.get("state", 0)), bool(f.get("ingame"))
            head.append(_avatar(f.get("avatar", ""), 26))
            col = [ft.Text(f.get("name") or "Steam friend", size=14,
                           weight=ft.FontWeight.BOLD, color=_TEXT_PRIMARY)]
            if ingame:
                col.append(ft.Text(f.get("game") or "In-Game", size=11, color=_C_INGAME))
            else:
                col.append(ft.Text(_STATE_LABEL.get(state, "Offline"), size=11,
                                   color=(_C_ONLINE if state else _C_OFFLINE)))
            head.append(ft.Column(col, spacing=0, tight=True))
        self._chat_head.controls = head

    # ── messages ─────────────────────────────────────────────────────────────
    def _msg_block(self, *, avatar: str, name: str, name_color: str, grouped: bool,
                   primary: str, secondary: str, images: list) -> ft.Control:
        col = ft.Column(spacing=2, tight=True, expand=True)
        if not grouped:
            col.controls.append(ft.Text(name, size=12, weight=ft.FontWeight.BOLD,
                                        color=name_color))
        if primary:
            col.controls.append(ft.Text(spans=_spans(primary, _TEXT_PRIMARY), size=14,
                                        selectable=True))
        if secondary and secondary != primary:
            col.controls.append(ft.Text(spans=_spans(secondary, _SUB), size=11,
                                        selectable=True, italic=True))
        for url in (images or []):
            col.controls.append(ft.Container(
                content=ft.Image(src=url, fit=ft.ImageFit.CONTAIN, width=260,
                                 border_radius=8),
                padding=ft.padding.only(top=3)))
        av = ft.Container(width=30) if grouped else _avatar(avatar)
        return ft.Row([av, col], spacing=8,
                      vertical_alignment=ft.CrossAxisAlignment.START)

    def _add(self, control: ft.Control) -> None:
        self._messages.controls.append(control)
        if self.page:
            self.page.update()

    async def _render_msg(self, m: dict) -> None:
        text = m.get("text", "")
        images = m.get("images", [])
        if not text and not images:
            return
        from_me = bool(m.get("from_me"))
        key = "me" if from_me else ("them:" + str(m.get("name", "")))
        grouped = (key == self._last_key)
        self._last_key = key
        if from_me:
            self._add(self._msg_block(avatar=m.get("avatar", "") or self._own_avatar,
                                      name="You", name_color=_TEXT_FAINT, grouped=grouped,
                                      primary=text, secondary="", images=images))
        else:
            english = await self._tr(text, False) if text else ""
            self._add(self._msg_block(avatar=m.get("avatar", ""),
                                      name=m.get("name", "Them"), name_color=_ACCENT,
                                      grouped=grouped, primary=english, secondary=text,
                                      images=images))

    async def _tr(self, text: str, to_them: bool) -> str:
        if callable(self.translate_message):
            with contextlib.suppress(Exception):
                out = await self.translate_message(text, to_them)
                if out:
                    return out
        return text

    def _log(self, msg: str) -> None:
        with contextlib.suppress(Exception):
            p = _BRIDGE_ROOT / "steam_bridge" / "view_debug.log"
            with open(p, "a", encoding="utf-8") as h:
                h.write(msg + "\n")

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
            try:
                self._proc = subprocess.Popen(
                    [str(_DAEMON_PYTHON), str(_DAEMON_PY)],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    creationflags=_CREATE_NO_WINDOW)
            except Exception as exc:
                self._log(f"spawn FAILED: {exc}")
                return
            for _ in range(60):
                if await self._try_open():
                    connected = True
                    break
                await asyncio.sleep(0.5)
        if not connected:
            return
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

    async def _open(self, acct: int) -> None:
        self._active = acct           # highlight immediately
        self._pending = acct
        self._last_key = None
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
            self.page.update()
        zh = await self._tr(text, True)
        self._last_key = None
        self._add(self._msg_block(avatar=self._own_avatar, name="You",
                                  name_color=_TEXT_FAINT, grouped=False,
                                  primary=text, secondary=zh, images=[]))
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
            self._own_avatar = ev.get("avatar", "") or self._own_avatar
        elif kind == "friends":
            self._friends = {int(i["acct"]): i for i in ev.get("items", [])}
            self._rebuild_friends()
            if self._active in self._friends:
                self._set_chat_head(self._friends[self._active])
            if self.page:
                self.page.update()
        elif kind == "history":
            if int(ev.get("acct", 0)) in (self._pending, self._active):
                self._messages.controls.clear()
                self._last_key = None
                for m in ev.get("messages", []):
                    await self._render_msg(m)
        elif kind == "opened":
            if ev.get("ok"):
                self._active = int(ev.get("acct", 0))
                self._entry.disabled = False
            if self.page:
                self.page.update()
        elif kind == "inbound":
            if int(ev.get("acct", 0)) != self._active:
                return
            await self._render_msg(ev.get("message", {}))

    async def shutdown(self) -> None:
        with contextlib.suppress(Exception):
            if self._writer:
                self._writer.close()
        with contextlib.suppress(Exception):
            if self._proc and self._proc.returncode is None:
                self._proc.terminate()
