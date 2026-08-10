# -*- coding: utf-8 -*-
"""Steam chat, embedded inside PuriPuly (beta, local only). NEVER ships.

Fully embedded: a headless browser helper (no window ever) reads/sends Steam
messages over a local socket by driving Steam's in-memory web-chat model, so it
works without any visible browser. This Flet panel renders the chat natively:
a collapsible friends list on the left, a chat pane on the right with avatars,
image embeds, and live translation both ways using the app's own translator.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import socket
from pathlib import Path

import flet as ft

# Exact values from the dashboard theme so the Steam tab matches the VRChat one.
_BG_MAIN = "#2e2f32"
_BG_SIDE = "#26272a"
_BG_INPUT = "#323336"
_BG_SEL = "#33343880"
_BORDER_INPUT = "#5b5c5f"
_DIVIDER = "#4b4c4f"
_TEXT_PRIMARY = "#f2f2f2"
_TEXT_FAINT = "#7f8084"
_SUB = "#8fa9c4"
_ACCENT = "#48a495"
_TOGGLE_ON = "#48a495"

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


def _avatar(url: str, size: int = 30) -> ft.Control:
    if url:
        return ft.Image(src=url, width=size, height=size, border_radius=size // 2,
                        fit=ft.ImageFit.COVER)
    return ft.Container(width=size, height=size, border_radius=size // 2, bgcolor="#3a3b3e")


class SteamBridgeView(ft.Container):
    def __init__(self) -> None:
        super().__init__(expand=True, bgcolor=_BG_MAIN, padding=0)
        # injected by the app: async translate(text, to_them:bool) -> str
        self.translate_message = None
        self._proc = None
        self._reader = None
        self._writer = None
        self._own = 0
        self._own_avatar = ""
        self._active = None            # account id of the open chat
        self._pending = None           # account id the user just picked
        self._names: dict[int, str] = {}
        self._avatars: dict[int, str] = {}
        self._friend_items: dict[int, ft.Container] = {}
        self._collapsed = False
        self._started = False
        self._prewarmed = False

        # ── left: collapsible friends list ───────────────────────────────────
        self._friends_list = ft.ListView(expand=True, spacing=2, padding=6)
        self._left_panel = ft.Container(width=220, bgcolor=_BG_SIDE,
                                        content=self._friends_list)
        self._left_divider = ft.VerticalDivider(width=1, color=_DIVIDER, thickness=1)

        # ── right: chat pane ─────────────────────────────────────────────────
        self._collapse_btn = ft.IconButton(
            ft.Icons.MENU_OPEN, icon_size=18, icon_color=_TEXT_FAINT,
            tooltip="Show/hide friends", on_click=self._toggle_panel,
            style=ft.ButtonStyle(overlay_color=ft.Colors.TRANSPARENT))
        self._chat_head = ft.Row([self._collapse_btn], spacing=8,
                                 vertical_alignment=ft.CrossAxisAlignment.CENTER)
        top_bar = ft.Container(content=self._chat_head,
                               padding=ft.padding.symmetric(horizontal=6, vertical=4),
                               bgcolor=_BG_MAIN)

        self._messages = ft.ListView(expand=True, spacing=10, padding=14, auto_scroll=True)
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
        """Spawn the helper at app startup (best effort, no page needed) so the
        Steam page is already loaded by the time the user opens the tab — kills
        the "late load". Skips if a helper is already listening."""
        if self._prewarmed:
            return
        self._prewarmed = True
        with contextlib.suppress(Exception):
            s = socket.socket()
            s.settimeout(0.25)
            try:
                s.connect((_HOST, _PORT))   # already running
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
            self._log(f"prewarm spawned pid={self._proc.pid}")

    def activate(self) -> None:
        if self._started:
            return
        self._started = True
        if self.page:
            self.page.run_task(self._connect)

    # ── rendering ────────────────────────────────────────────────────────────
    def _friend_row(self, acct: int, name: str, avatar: str) -> ft.Container:
        item = ft.Container(
            content=ft.Row([
                _avatar(avatar, 32),
                ft.Text(name or "Steam chat", size=13, color=_TEXT_PRIMARY,
                        max_lines=1, overflow=ft.TextOverflow.ELLIPSIS, expand=True),
            ], spacing=10, vertical_alignment=ft.CrossAxisAlignment.CENTER),
            padding=ft.padding.symmetric(horizontal=10, vertical=7),
            border_radius=8, ink=True, bgcolor=ft.Colors.TRANSPARENT,
            on_click=lambda e, a=acct: self.page.run_task(self._open, a))
        return item

    def _build_friends(self, items: list) -> None:
        self._friends_list.controls.clear()
        self._friend_items.clear()
        for i in items:
            acct = int(i["acct"])
            row = self._friend_row(acct, i.get("name", ""), i.get("avatar", ""))
            self._friend_items[acct] = row
            self._friends_list.controls.append(row)
        self._highlight(self._active)

    def _highlight(self, acct) -> None:
        for a, row in self._friend_items.items():
            row.bgcolor = _BG_SEL if a == acct else ft.Colors.TRANSPARENT

    def _set_chat_head(self, name: str, avatar: str) -> None:
        head = [self._collapse_btn]
        if name:
            head.append(_avatar(avatar, 26))
            head.append(ft.Text(name, size=14, weight=ft.FontWeight.BOLD, color=_TEXT_PRIMARY))
        self._chat_head.controls = head

    def _msg_row(self, *, avatar: str, name: str, name_color: str,
                 primary: str, secondary: str, images: list) -> ft.Control:
        col = ft.Column(spacing=3, tight=True, expand=True)
        col.controls.append(ft.Text(name, size=12, weight=ft.FontWeight.BOLD, color=name_color))
        if primary:
            col.controls.append(ft.Text(primary, size=14, color=_TEXT_PRIMARY, selectable=True))
        if secondary and secondary != primary:
            col.controls.append(ft.Text(secondary, size=11, color=_SUB, italic=True,
                                        selectable=True))
        for url in (images or []):
            col.controls.append(ft.Container(
                content=ft.Image(src=url, fit=ft.ImageFit.CONTAIN, width=260,
                                 border_radius=8),
                padding=ft.padding.only(top=3)))
        return ft.Row([_avatar(avatar), col], spacing=8,
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
        if m.get("from_me"):
            self._add(self._msg_row(avatar=m.get("avatar", "") or self._own_avatar,
                                    name="You", name_color=_TEXT_FAINT,
                                    primary=text, secondary="", images=images))
        else:
            english = await self._tr(text, False) if text else ""
            self._add(self._msg_row(avatar=m.get("avatar", ""),
                                    name=m.get("name", "Them"), name_color=_ACCENT,
                                    primary=english, secondary=text, images=images))

    def _set(self, text: str) -> None:
        # No status chrome — the tab just opens and populates naturally.
        self._log(f"status: {text}")

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

    # ── interactions ─────────────────────────────────────────────────────────
    def _toggle_panel(self, e=None) -> None:
        self._collapsed = not self._collapsed
        self._left_panel.visible = not self._collapsed
        self._left_divider.visible = not self._collapsed
        self._collapse_btn.icon = ft.Icons.MENU if self._collapsed else ft.Icons.MENU_OPEN
        if self.page:
            self.page.update()

    # ── helper process + socket ──────────────────────────────────────────────
    async def _connect(self) -> None:
        if self._writer is not None:
            return
        # Wait briefly for a prewarmed helper before spawning our own, so we do
        # not race two daemons onto the same port.
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
                self._log(f"spawned pid={self._proc.pid}")
            except Exception as exc:
                self._log(f"spawn FAILED: {type(exc).__name__}: {exc}")
                return
            for _ in range(60):
                if await self._try_open():
                    connected = True
                    break
                await asyncio.sleep(0.5)
        if not connected:
            self._log("connect FAILED after retries")
            return
        self._log("connected to helper socket")
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
        self._active = None
        self._pending = acct
        self._highlight(acct)
        self._set_chat_head(self._names.get(acct, ""), self._avatars.get(acct, ""))
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
        self._add(self._msg_row(avatar=self._own_avatar, name="You",
                                name_color=_TEXT_FAINT, primary=text, secondary=zh,
                                images=[]))
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
        if kind == "status":
            self._set("connected" if ev.get("signed_in") else str(ev.get("mode")))
        elif kind == "own":
            self._own = int(ev.get("acct", 0))
            self._own_avatar = ev.get("avatar", "") or self._own_avatar
        elif kind == "conversations":
            items = ev.get("items", [])
            self._names = {int(i["acct"]): i.get("name", "") for i in items}
            self._avatars = {int(i["acct"]): i.get("avatar", "") for i in items}
            self._build_friends(items)
            if self.page:
                self.page.update()
        elif kind == "history":
            if self._active_pending(ev.get("acct")):
                self._messages.controls.clear()
                for m in ev.get("messages", []):
                    await self._render_msg(m)
        elif kind == "opened":
            if ev.get("ok"):
                self._active = int(ev.get("acct", 0))
                self._entry.disabled = False
                self._highlight(self._active)
            if self.page:
                self.page.update()
        elif kind == "inbound":
            if int(ev.get("acct", 0)) != self._active:
                return
            await self._render_msg(ev.get("message", {}))

    def _active_pending(self, acct) -> bool:
        # history arrives right after the user picks a chat, before `opened`
        # sets self._active — accept it for the chat the user just picked.
        try:
            return int(acct) in (self._pending, self._active)
        except Exception:
            return True

    async def shutdown(self) -> None:
        with contextlib.suppress(Exception):
            if self._writer:
                self._writer.close()
        with contextlib.suppress(Exception):
            if self._proc and self._proc.returncode is None:
                self._proc.terminate()
