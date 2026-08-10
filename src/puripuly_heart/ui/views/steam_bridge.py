# -*- coding: utf-8 -*-
"""Steam chat tab (beta, local only). NEVER ships — beta/steam-bridge branch.

A conversation panel inside PuriPuly that bridges a Steam friend chat: their
messages arrive translated; you type and it is translated and sent back. The
actual Steam browser runs in a separate helper process (Playwright, in an
isolated env), exactly like the overlay/OCR helpers — this view only talks to it
over a localhost socket and does the translation with the app's own free Bing
engine (no API key).

Styled to feel like Steam's own dark chat: a friends column on the left, flat
message rows on the right, an input at the bottom.
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from uuid import uuid4

import flet as ft

# ── where the browser helper lives (beta: the isolated env in the scratchpad) ──
# This is a local-only beta path; the feature is not shipped, so a fixed dev
# path is acceptable until it graduates.
_BRIDGE_ROOT = Path(
    r"C:\Users\Owner\AppData\Local\Temp\claude\E--Programming-Claude"
    r"\6d59879c-8d48-4d69-98ef-fc5f025d4ef6\scratchpad"
)
_DAEMON_PY = _BRIDGE_ROOT / "steam_bridge" / "daemon.py"
_DAEMON_PYTHON = _BRIDGE_ROOT / "steamprobe-venv" / "Scripts" / "python.exe"
_HOST, _PORT = "127.0.0.1", 8791

# Beta language pair: you speak English, they speak Chinese.
_MY_LANG = "en"
_THEIR_LANG = "zh-CN"

# Steam-ish dark palette.
_BG = "#1b2838"
_PANEL = "#171a21"
_SIDE = "#1e2b3c"
_ROW_HOVER = "#2a3f5a"
_ACCENT = "#66c0f4"
_NAME = "#66c0f4"
_SUB = "#8fa9c4"
_INPUT = "#316282"


class SteamBridgeView(ft.Container):
    """A single self-contained control the app drops into its view map."""

    def __init__(self) -> None:
        super().__init__(expand=True, bgcolor=_BG, padding=0)
        self._translator = None
        self._proc: asyncio.subprocess.Process | None = None
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._own_name = ""
        self._active: str | None = None
        self._started = False

        self._status = ft.Text("Steam not connected", size=12, color=_SUB)
        self._connect_btn = ft.ElevatedButton(
            "Connect Steam", icon=ft.Icons.LINK, bgcolor=_INPUT, color="white",
            on_click=lambda e: self.page.run_task(self._connect))
        self._login_btn = ft.TextButton(
            "Sign in to Steam", icon=ft.Icons.LOGIN, visible=False,
            on_click=lambda e: self.page.run_task(self._login))
        self._convos = ft.ListView(expand=True, spacing=2, padding=6)
        self._messages = ft.ListView(expand=True, spacing=6, padding=14, auto_scroll=True)
        self._header = ft.Text("Pick a conversation", size=15, weight=ft.FontWeight.BOLD,
                               color="white")
        self._entry = ft.TextField(
            hint_text="Type in English, press Enter…", expand=True, disabled=True,
            border_color=_INPUT, focused_border_color=_ACCENT, color="white",
            bgcolor=_PANEL, on_submit=lambda e: self.page.run_task(self._send))

        left = ft.Container(
            width=220, bgcolor=_SIDE,
            content=ft.Column([
                ft.Container(
                    padding=10,
                    content=ft.Column([
                        ft.Text("STEAM", size=13, weight=ft.FontWeight.BOLD, color=_ACCENT),
                        self._status, self._connect_btn, self._login_btn,
                    ], spacing=6, tight=True)),
                ft.Divider(height=1, color=_PANEL),
                self._convos,
            ], spacing=0, expand=True))

        right = ft.Column([
            ft.Container(padding=12, bgcolor=_PANEL, content=self._header),
            ft.Container(content=self._messages, expand=True),
            ft.Container(padding=10, bgcolor=_PANEL, content=ft.Row([
                self._entry,
                ft.IconButton(ft.Icons.SEND_ROUNDED, icon_color=_ACCENT,
                              on_click=lambda e: self.page.run_task(self._send))])),
        ], spacing=0, expand=True)

        self.content = ft.Row([left, right], spacing=0, expand=True)

    # Called by the app the first time the Steam tab is opened.
    def activate(self) -> None:
        if self._started:
            return
        self._started = True

    # ── message rendering (Steam-style flat rows) ───────────────────────────
    def _row(self, *, name: str, name_color: str, primary: str, secondary: str) -> ft.Control:
        col = ft.Column(spacing=1, tight=True, expand=True)
        col.controls.append(ft.Text(name, size=12, weight=ft.FontWeight.BOLD, color=name_color))
        col.controls.append(ft.Text(primary, size=14, color="white", selectable=True))
        if secondary:
            col.controls.append(ft.Text(secondary, size=11, color=_SUB, italic=True,
                                        selectable=True))
        return ft.Container(padding=ft.padding.symmetric(vertical=2, horizontal=4), content=col)

    def _add(self, control: ft.Control) -> None:
        self._messages.controls.append(control)
        if self.page:
            self.page.update()

    def _set_status(self, text: str) -> None:
        self._status.value = text
        if self.page:
            self.page.update()

    async def _translate(self, text: str, target: str, source: str = "") -> str:
        if self._translator is None:
            from puripuly_heart.providers.llm.free_web import FreeWebTranslationProvider
            self._translator = FreeWebTranslationProvider("bing")
        try:
            result = await self._translator.translate(
                utterance_id=uuid4(), text=text, system_prompt="",
                source_language=source, target_language=target, context="")
            return getattr(result, "text", text) or text
        except Exception as exc:
            return f"[translation failed: {type(exc).__name__}]"

    # ── talking to the helper process ───────────────────────────────────────
    async def _connect(self) -> None:
        if self._writer is not None:
            return
        self._set_status("starting Steam helper…")
        try:
            self._proc = await asyncio.create_subprocess_exec(
                str(_DAEMON_PYTHON), str(_DAEMON_PY),
                stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
        except Exception as exc:
            self._set_status(f"could not start helper: {exc}")
            return
        # give the daemon a moment to bind the port
        for _ in range(30):
            try:
                self._reader, self._writer = await asyncio.open_connection(_HOST, _PORT)
                break
            except Exception:
                await asyncio.sleep(0.5)
        if self._writer is None:
            self._set_status("helper did not respond")
            return
        self.page.run_task(self._read_loop)

    async def _login(self) -> None:
        await self._cmd({"cmd": "login"})

    async def _cmd(self, obj: dict) -> None:
        if self._writer is None:
            return
        try:
            self._writer.write((json.dumps(obj, ensure_ascii=False) + "\n").encode("utf-8"))
            await self._writer.drain()
        except Exception:
            pass

    async def _open(self, name: str) -> None:
        self._active = None
        self._header.value = name
        self._messages.controls.clear()
        self._entry.disabled = True
        self._set_status(f"opening {name}…")
        await self._cmd({"cmd": "open", "name": name})

    async def _send(self) -> None:
        text = (self._entry.value or "").strip()
        if not text or not self._active:
            return
        self._entry.value = ""
        if self.page:
            self.page.update()
        zh = await self._translate(text, _THEIR_LANG, _MY_LANG)
        self._add(self._row(name="You", name_color=_SUB, primary=text, secondary=zh))
        await self._cmd({"cmd": "send", "text": zh})

    async def _read_loop(self) -> None:
        assert self._reader is not None
        try:
            async for raw in self._reader:
                try:
                    ev = json.loads(raw.decode("utf-8").strip() or "{}")
                except Exception:
                    continue
                await self._handle(ev)
        except Exception:
            self._set_status("helper connection lost")

    async def _handle(self, ev: dict) -> None:
        kind = ev.get("ev")
        if kind == "status":
            signed = ev.get("signed_in")
            mode = ev.get("mode")
            self._set_status("Steam connected" if signed else f"Steam: {mode}")
            self._login_btn.visible = not signed and mode != "login"
            if self.page:
                self.page.update()
        elif kind == "own_name":
            self._own_name = ev.get("name", "")
        elif kind == "conversations":
            self._convos.controls = [
                ft.Container(
                    padding=ft.padding.symmetric(vertical=8, horizontal=10),
                    border_radius=4, ink=True,
                    on_click=lambda e, n=n: self.page.run_task(self._open, n),
                    content=ft.Text(n, size=13, color="white", no_wrap=True,
                                    overflow=ft.TextOverflow.ELLIPSIS))
                for n in ev.get("names", [])]
            if self.page:
                self.page.update()
        elif kind == "opened":
            if ev.get("ok"):
                self._active = ev.get("name")
                self._entry.disabled = False
                self._set_status(f"bridging {self._active} — sending is live")
            else:
                self._set_status("could not open that conversation")
            if self.page:
                self.page.update()
        elif kind == "inbound":
            speaker = ev.get("speaker", "")
            original = ev.get("text", "")
            english = await self._translate(original, _MY_LANG)
            self._add(self._row(name=speaker or "Them", name_color=_NAME,
                                primary=english, secondary=original))

    async def shutdown(self) -> None:
        try:
            if self._writer:
                self._writer.close()
        except Exception:
            pass
        try:
            if self._proc and self._proc.returncode is None:
                self._proc.terminate()
        except Exception:
            pass
