# -*- coding: utf-8 -*-
"""Steam chat, embedded inside PuriPuly (beta, local only). NEVER ships.

Fully embedded: a headless browser helper (no window ever) reads/sends Steam
messages over a local socket; this Flet panel renders the chat natively inside
the app. Auto-connects on open (you are already signed in), renders your
conversations with avatars, translates incoming messages into your language and
your typed messages into theirs using the app's own translator.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import os
from pathlib import Path

import flet as ft

_BG_MAIN = "#2e2f32"
_BG_SIDE = "#26272a"
_BG_INPUT = "#323336"
_TEXT_PRIMARY = "#f2f2f2"
_TEXT_FAINT = "#9a9b9f"
_SUB = "#8fa9c4"
_ACCENT = "#48a495"
_ROW_SEL = "#33454f"

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
        self._own = ""
        self._active = None
        self._avatars: dict[str, str] = {}
        self._started = False

        self._status = ft.Text("connecting to Steam…", size=11, color=_TEXT_FAINT)
        self._convos = ft.ListView(expand=True, spacing=1, padding=6)
        self._messages = ft.ListView(expand=True, spacing=8, padding=14, auto_scroll=True)
        self._header = ft.Text("", size=14, weight=ft.FontWeight.BOLD, color=_TEXT_PRIMARY)
        self._entry = ft.TextField(
            hint_text="Type — it sends translated", expand=True, disabled=True,
            border_color=_BG_INPUT, focused_border_color=_ACCENT, color=_TEXT_PRIMARY,
            bgcolor=_BG_INPUT, text_size=13, content_padding=10,
            on_submit=lambda e: self.page.run_task(self._send))

        left = ft.Container(
            width=210, bgcolor=_BG_SIDE,
            content=ft.Column([
                ft.Container(padding=8, content=self._status),
                ft.Divider(height=1, color="#333"),
                self._convos,
            ], spacing=0, expand=True))
        right = ft.Column([
            ft.Container(padding=10, content=ft.Row([self._header], spacing=8)),
            ft.Divider(height=1, color="#333"),
            ft.Container(content=self._messages, expand=True),
            ft.Container(padding=8, content=ft.Row([
                self._entry,
                ft.IconButton(ft.Icons.SEND_ROUNDED, icon_color=_ACCENT,
                              on_click=lambda e: self.page.run_task(self._send))])),
        ], spacing=0, expand=True)
        self.content = ft.Row([left, right], spacing=0, expand=True)

    def activate(self) -> None:
        if self._started:
            return
        self._started = True
        if self.page:
            self.page.run_task(self._connect)

    # ── rendering ────────────────────────────────────────────────────────────
    def _msg_row(self, *, avatar: str, name: str, name_color: str,
                 primary: str, secondary: str) -> ft.Control:
        col = ft.Column(spacing=1, tight=True, expand=True)
        col.controls.append(ft.Text(name, size=12, weight=ft.FontWeight.BOLD, color=name_color))
        col.controls.append(ft.Text(primary, size=14, color=_TEXT_PRIMARY, selectable=True))
        if secondary and secondary != primary:
            col.controls.append(ft.Text(secondary, size=11, color=_SUB, italic=True,
                                        selectable=True))
        return ft.Row([_avatar(avatar), col], spacing=8,
                      vertical_alignment=ft.CrossAxisAlignment.START)

    def _add(self, control: ft.Control) -> None:
        self._messages.controls.append(control)
        if self.page:
            self.page.update()

    def _set(self, text: str) -> None:
        self._status.value = text
        if self.page:
            self.page.update()

    async def _tr(self, text: str, to_them: bool) -> str:
        if callable(self.translate_message):
            with contextlib.suppress(Exception):
                out = await self.translate_message(text, to_them)
                if out:
                    return out
        return text

    # ── helper process + socket ──────────────────────────────────────────────
    async def _connect(self) -> None:
        if self._writer is not None:
            return
        try:
            self._proc = await asyncio.create_subprocess_exec(
                str(_DAEMON_PYTHON), str(_DAEMON_PY),
                stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
                creationflags=_CREATE_NO_WINDOW)
        except Exception:
            self._proc = None  # a daemon may already be running; try to connect anyway
        for _ in range(40):
            try:
                self._reader, self._writer = await asyncio.open_connection(_HOST, _PORT)
                break
            except Exception:
                await asyncio.sleep(0.5)
        if self._writer is None:
            self._set("could not reach the Steam helper")
            return
        self.page.run_task(self._read_loop)

    async def _cmd(self, obj: dict) -> None:
        if self._writer is None:
            return
        with contextlib.suppress(Exception):
            self._writer.write((json.dumps(obj, ensure_ascii=False) + "\n").encode("utf-8"))
            await self._writer.drain()

    async def _open(self, name: str) -> None:
        self._active = None
        self._header.value = name
        self._messages.controls.clear()
        self._entry.disabled = True
        self._set(f"opening {name}…")
        await self._cmd({"cmd": "open", "name": name})

    async def _send(self) -> None:
        text = (self._entry.value or "").strip()
        if not text or not self._active:
            return
        self._entry.value = ""
        if self.page:
            self.page.update()
        zh = await self._tr(text, True)
        self._add(self._msg_row(avatar="", name="You", name_color=_TEXT_FAINT,
                                primary=text, secondary=zh))
        await self._cmd({"cmd": "send", "text": zh})

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
            self._set("Steam connected" if ev.get("signed_in") else f"Steam: {ev.get('mode')}")
        elif kind == "own_name":
            self._own = ev.get("name", "")
        elif kind == "conversations":
            items = ev.get("items", [])
            self._avatars = {i["name"]: i.get("avatar", "") for i in items}
            self._convos.controls = [
                ft.Container(
                    padding=ft.padding.symmetric(vertical=6, horizontal=8),
                    border_radius=4, ink=True,
                    on_click=lambda e, n=i["name"]: self.page.run_task(self._open, n),
                    content=ft.Row([
                        _avatar(i.get("avatar", ""), 26),
                        ft.Text(i["name"], size=12, color=_TEXT_PRIMARY, no_wrap=True,
                                overflow=ft.TextOverflow.ELLIPSIS, expand=True),
                    ], spacing=8))
                for i in items]
            self._set("")
            if self.page:
                self.page.update()
        elif kind == "opened":
            if ev.get("ok"):
                self._active = ev.get("name")
                self._entry.disabled = False
                self._set("")
            else:
                self._set("could not open that chat")
            if self.page:
                self.page.update()
        elif kind == "inbound":
            speaker = ev.get("speaker", "") or "Them"
            original = ev.get("text", "")
            english = await self._tr(original, False)
            self._add(self._msg_row(avatar=ev.get("avatar", ""), name=speaker,
                                    name_color=_ACCENT, primary=english, secondary=original))

    async def shutdown(self) -> None:
        with contextlib.suppress(Exception):
            if self._writer:
                self._writer.close()
        with contextlib.suppress(Exception):
            if self._proc and self._proc.returncode is None:
                self._proc.terminate()
