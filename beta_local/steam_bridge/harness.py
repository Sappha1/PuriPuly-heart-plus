# -*- coding: utf-8 -*-
"""Steam Translation Bridge — local beta harness.

A single window that is the whole loop, end to end:

  * a hidden Edge (no window) stays signed in to Steam chat, via the saved
    profile — you never see or touch it;
  * pick a conversation from the buttons across the top;
  * whatever your friend says arrives here, translated to English;
  * type in English, press Enter, and it is translated and sent to them in
    their language — for real, in the actual Steam chat.

This is deliberately a standalone toy, not part of the main app: it lives in
its own isolated environment so it cannot bloat or break your real build. It
proves the design so you can feel it before it becomes real dashboard tabs.

Translation uses the same keyless Bing endpoint the app calls, so no API key is
needed. Sending is REAL — the banner says so.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

import flet as ft
import httpx

from steam_page import SteamPage
from tracker import MessageTracker, Msg

PROFILE = str(Path(__file__).resolve().parent.parent / "steamprobe-profile")

# Who speaks what. Your side / their side. (A dropdown could expose these; for
# the beta they are just constants — English you, Chinese them.)
MY_LANG = "en"
THEIR_LANG = "zh-Hans"

_EDGE_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36 Edg/131.0.0.0"
)
_EDGE_URL = "https://edge.microsoft.com/translate/translatetext"

C_BG = "#1b2838"       # steam-ish
C_PANEL = "#2a3f5a"
C_IN = "#33475b"
C_OUT = "#1a6dd0"
C_SUB = "#8fa9c4"


async def bing_translate(client: httpx.AsyncClient, text: str, to: str, frm: str = "") -> str:
    params = {"to": to, "isEnterpriseClient": "false"}
    if frm:
        params["from"] = frm
    try:
        r = await client.post(_EDGE_URL, params=params, json=[text],
                              headers={"User-Agent": _EDGE_UA}, timeout=8.0)
        r.raise_for_status()
        return str(r.json()[0]["translations"][0]["text"]).strip()
    except Exception as exc:
        return f"[translation failed: {type(exc).__name__}]"


async def main(page: ft.Page) -> None:
    page.title = "Steam Translation Bridge (beta)"
    page.bgcolor = C_BG
    page.padding = 0
    page.window.width = 620
    page.window.height = 780

    steam = SteamPage(PROFILE)
    http = httpx.AsyncClient()
    tracker = MessageTracker()
    state = {"conversation": None, "ready": False}

    status = ft.Text("Starting hidden Steam browser…", color=C_SUB, size=12)
    convo_row = ft.Row(wrap=True, spacing=6, run_spacing=6)
    messages = ft.ListView(expand=True, spacing=8, padding=12, auto_scroll=True)
    entry = ft.TextField(
        hint_text="Type in English, press Enter to send…",
        expand=True, border_color=C_PANEL, focused_border_color=C_OUT,
        color="white", bgcolor=C_PANEL, disabled=True,
    )

    def bubble(*, mine: bool, primary: str, secondary: str) -> ft.Control:
        col = ft.Column(spacing=2, tight=True, horizontal_alignment=(
            ft.CrossAxisAlignment.END if mine else ft.CrossAxisAlignment.START))
        col.controls.append(ft.Text(primary, color="white", size=15, selectable=True))
        if secondary:
            col.controls.append(ft.Text(secondary, color=C_SUB, size=11, italic=True,
                                        selectable=True))
        return ft.Row(
            alignment=(ft.MainAxisAlignment.END if mine else ft.MainAxisAlignment.START),
            controls=[ft.Container(
                content=col, padding=ft.padding.symmetric(horizontal=12, vertical=8),
                bgcolor=(C_OUT if mine else C_IN), border_radius=12,
                width=440)])

    def add(control: ft.Control) -> None:
        messages.controls.append(control)
        page.update()

    async def select_conversation(name: str) -> None:
        state["conversation"] = None                 # pause polling during switch
        status.value = f"opening chat with {name}…"
        page.update()
        ok = await steam.open_conversation(name)
        if not ok:
            status.value = f"could not open {name}"
            page.update()
            return
        # fresh tracker for the new conversation; prime it on the next poll so
        # existing scrollback is not dumped as "new".
        nonlocal tracker
        tracker = MessageTracker(own_name=await steam.own_name())
        primed = await steam.read_messages()
        tracker.poll([Msg(s, t) for s, t in primed])   # prime silently
        messages.controls.clear()
        add(ft.Text(f"— now bridging your chat with {name} —", color=C_SUB, size=12))
        entry.disabled = False
        state["conversation"] = name
        status.value = f"bridging {name} — sending is LIVE"
        page.update()

    def convo_button(name: str) -> ft.Control:
        return ft.ElevatedButton(
            text=name, height=32, bgcolor=C_PANEL, color="white",
            on_click=lambda e, n=name: page.run_task(select_conversation, n))

    async def on_send(_e=None) -> None:
        text = (entry.value or "").strip()
        if not text or not state["conversation"]:
            return
        entry.value = ""
        page.update()
        translated = await bing_translate(http, text, THEIR_LANG, MY_LANG)
        add(bubble(mine=True, primary=text, secondary=translated))
        tracker.note_sent(translated)
        ok = await steam.send(translated)
        if not ok:
            add(ft.Text("  (send failed — is the conversation still open?)", color="#e06c6c",
                        size=11))

    entry.on_submit = on_send

    async def poll_loop() -> None:
        while True:
            await asyncio.sleep(1.3)
            if not state["conversation"]:
                continue
            try:
                snapshot = await steam.read_messages()
            except Exception:
                continue
            new = tracker.poll([Msg(s, t) for s, t in snapshot])
            for msg in new:
                translated = await bing_translate(http, msg.text, MY_LANG)
                add(bubble(mine=False, primary=translated, secondary=f"{msg.speaker}: {msg.text}"))

    async def boot() -> None:
        try:
            await steam.start()
        except Exception as exc:
            status.value = f"could not start Steam browser: {exc}"
            page.update()
            return
        if not await steam.is_signed_in():
            status.value = ("not signed in — run TEST-Steam-Send once to log in, "
                            "then reopen this")
            page.update()
            return
        convos = await steam.list_conversations()
        convo_row.controls = [convo_button(n) for n in convos]
        status.value = "pick a conversation above to start bridging"
        state["ready"] = True
        page.update()
        await poll_loop()

    page.add(
        ft.Container(
            bgcolor=C_PANEL, padding=12,
            content=ft.Column([
                ft.Text("Steam Translation Bridge", color="white", size=18,
                        weight=ft.FontWeight.BOLD),
                status,
                ft.Container(
                    content=ft.Text("Sending is REAL — messages go to your actual "
                                    "Steam friend.", color="#ffd27f", size=11),
                    padding=ft.padding.only(top=2)),
                ft.Container(content=convo_row, padding=ft.padding.only(top=8)),
            ], spacing=4, tight=True)),
        ft.Container(content=messages, expand=True),
        ft.Container(
            bgcolor=C_PANEL, padding=10,
            content=ft.Row([entry, ft.IconButton(
                icon=ft.Icons.SEND_ROUNDED, icon_color=C_OUT,
                on_click=lambda e: page.run_task(on_send))])),
    )

    async def on_close(_e) -> None:
        await steam.close()

    page.on_disconnect = on_close
    page.run_task(boot)


if __name__ == "__main__":
    ft.app(target=main)
