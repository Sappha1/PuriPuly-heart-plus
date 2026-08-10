# -*- coding: utf-8 -*-
"""Steam tab (beta, local only). NEVER ships — beta/steam-bridge branch.

The Steam chat is not rebuilt inside PuriPuly — a Flet app cannot host a live
browser. Instead this tab launches the REAL steamcommunity.com/chat in a window
PuriPuly controls, with translation injected into the page (a translated line
under each foreign message, and a 'type in your language' bar that sends
translated). See beta_local/steam_bridge/inject_daemon.py.

This view is just the launch/stop control, styled to match the app.
"""
from __future__ import annotations

import asyncio
import os
from pathlib import Path

import flet as ft

# App theme values (kept local so this beta view has no fragile deep imports).
_BG_MAIN = "#2e2f32"
_TEXT_PRIMARY = "#f2f2f2"
_TEXT_FAINT = "#7f8084"
COLOR_PRIMARY = "#48a495"

# beta: the helper lives in the isolated env in the scratchpad.
_BRIDGE_ROOT = Path(
    r"C:\Users\Owner\AppData\Local\Temp\claude\E--Programming-Claude"
    r"\6d59879c-8d48-4d69-98ef-fc5f025d4ef6\scratchpad"
)
_DAEMON_PY = _BRIDGE_ROOT / "steam_bridge" / "inject_daemon.py"
_VENV_SCRIPTS = _BRIDGE_ROOT / "steamprobe-venv" / "Scripts"
_DAEMON_PYTHON = (
    _VENV_SCRIPTS / "pythonw.exe" if (_VENV_SCRIPTS / "pythonw.exe").exists()
    else _VENV_SCRIPTS / "python.exe")
_CREATE_NO_WINDOW = 0x08000000


class SteamBridgeView(ft.Container):
    def __init__(self) -> None:
        super().__init__(expand=True, bgcolor=_BG_MAIN,
                         padding=ft.padding.symmetric(horizontal=24, vertical=20))
        # Injected by the app so the helper uses the user's configured languages.
        self.get_languages = None  # callable -> (my_lang, their_lang)
        self._proc: asyncio.subprocess.Process | None = None

        self._status = ft.Text("Steam chat is closed", size=13, color=_TEXT_FAINT)
        self._btn = ft.ElevatedButton(
            "Open Steam chat", icon=ft.Icons.CHAT_BUBBLE_OUTLINE,
            bgcolor=COLOR_PRIMARY, color="white",
            on_click=lambda e: self.page.run_task(self._toggle))
        self.content = ft.Column(
            [
                ft.Text("Steam", size=16, weight=ft.FontWeight.BOLD, color=_TEXT_PRIMARY),
                ft.Text("Opens your Steam chat with live translation woven in — "
                        "a translated line under each message you receive, and a bar to "
                        "type in your language and send it translated.",
                        size=12, color=_TEXT_FAINT),
                ft.Container(height=8),
                self._btn,
                self._status,
            ],
            spacing=8, tight=True)

    def activate(self) -> None:
        # Nothing eager — the window opens only when the button is pressed.
        pass

    def _set(self, text: str) -> None:
        self._status.value = text
        if self.page:
            self.page.update()

    async def _toggle(self) -> None:
        if self._proc is not None and self._proc.returncode is None:
            with __import__("contextlib").suppress(Exception):
                self._proc.terminate()
            self._proc = None
            self._btn.text = "Open Steam chat"
            self._set("Steam chat is closed")
            if self.page:
                self.page.update()
            return

        my, their = "en", "zh-CN"
        if callable(self.get_languages):
            with __import__("contextlib").suppress(Exception):
                my, their = self.get_languages()
        env = dict(os.environ, PP_MY_LANG=str(my or "en"),
                   PP_THEIR_LANG=str(their or "zh-CN"))
        self._set("opening Steam chat…")
        try:
            self._proc = await asyncio.create_subprocess_exec(
                str(_DAEMON_PYTHON), str(_DAEMON_PY),
                stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
                creationflags=_CREATE_NO_WINDOW, env=env)
        except Exception as exc:
            self._set(f"could not open: {exc}")
            return
        self._btn.text = "Close Steam chat"
        self._set(f"Steam chat open — translating your language ⇄ {their}")
        if self.page:
            self.page.update()

    async def shutdown(self) -> None:
        with __import__("contextlib").suppress(Exception):
            if self._proc and self._proc.returncode is None:
                self._proc.terminate()
