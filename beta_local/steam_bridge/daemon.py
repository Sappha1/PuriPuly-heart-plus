# -*- coding: utf-8 -*-
"""Steam browser helper — the companion process the PuriPuly app controls.

Runs in the isolated venv (it has Playwright); the app runs from its own clean
venv and never imports Playwright. They speak newline-delimited JSON over a
localhost TCP socket — the same shape as the overlay/OCR helpers, and needing no
extra dependency on either side (stdlib asyncio only).

Protocol
  app -> daemon (one JSON object per line):
    {"cmd":"status"}                 ask for a status event
    {"cmd":"login"}                  open a VISIBLE window to sign in, then hide
    {"cmd":"list"}                   (re)send the conversation list
    {"cmd":"open","name":"..."}      open that conversation; start bridging it
    {"cmd":"send","text":"..."}      type + send text to the open conversation
  daemon -> app:
    {"ev":"status","signed_in":bool,"mode":"hidden|login|starting"}
    {"ev":"conversations","names":[...]}
    {"ev":"own_name","name":"..."}
    {"ev":"opened","name":"...","ok":bool}
    {"ev":"inbound","speaker":"...","text":"..."}     a NEW message from them
    {"ev":"sent","ok":bool}
    {"ev":"log","text":"..."}                         diagnostics

Echo suppression and new-message detection (the tracker) live here, because they
need own_name and note_sent — the app only translates and displays.
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from steam_page import SteamPage
from tracker import MessageTracker, Msg

PROFILE = str(Path(__file__).resolve().parent.parent / "steamprobe-profile")
HOST, PORT = "127.0.0.1", 8791


class Daemon:
    def __init__(self) -> None:
        self.steam = SteamPage(PROFILE)
        self.tracker = MessageTracker()
        self.conversation: str | None = None
        self.clients: set[asyncio.StreamWriter] = set()
        self._started = False

    async def emit(self, obj: dict) -> None:
        line = (json.dumps(obj, ensure_ascii=False) + "\n").encode("utf-8")
        for w in list(self.clients):
            try:
                w.write(line)
                await w.drain()
            except Exception:
                self.clients.discard(w)

    async def ensure_started(self, mode: str = "hidden") -> None:
        if self._started:
            return
        await self.emit({"ev": "status", "signed_in": False, "mode": "starting"})
        await self.steam.start(mode=mode)
        self._started = True
        signed = await self.steam.is_signed_in()
        await self.emit({"ev": "status", "signed_in": signed, "mode": mode})
        if signed:
            await self.push_list()

    async def push_list(self) -> None:
        names = await self.steam.list_conversations()
        await self.emit({"ev": "conversations", "names": names})
        own = await self.steam.own_name()
        if own:
            await self.emit({"ev": "own_name", "name": own})

    async def do_login(self) -> None:
        # Relaunch visible so the user can sign in, then go hidden again.
        await self.steam.restart(mode="login")
        await self.emit({"ev": "status", "signed_in": False, "mode": "login"})
        ok = await self.steam.wait_until_signed_in(timeout_s=300)
        await self.steam.restart(mode="hidden")
        self._started = True
        await self.emit({"ev": "status", "signed_in": ok, "mode": "hidden"})
        if ok:
            await self.push_list()

    async def do_open(self, name: str) -> None:
        self.conversation = None
        ok = await self.steam.open_conversation(name)
        if ok:
            self.tracker = MessageTracker(own_name=await self.steam.own_name())
            primed = await self.steam.read_messages()
            self.tracker.poll([Msg(s, t) for s, t in primed])   # silence history
            self.conversation = name
        await self.emit({"ev": "opened", "name": name, "ok": ok})

    async def do_send(self, text: str) -> None:
        self.tracker.note_sent(text)
        ok = await self.steam.send(text)
        await self.emit({"ev": "sent", "ok": ok})

    async def poll_loop(self) -> None:
        while True:
            await asyncio.sleep(1.3)
            if not self.conversation:
                continue
            try:
                await self.steam.poke()
                snapshot = await self.steam.read_messages()
            except Exception as exc:
                await self.emit({"ev": "log", "text": f"read error: {exc}"})
                continue
            for msg in self.tracker.poll([Msg(s, t) for s, t in snapshot]):
                await self.emit({"ev": "inbound", "speaker": msg.speaker, "text": msg.text})

    async def handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        self.clients.add(writer)
        try:
            await self.ensure_started()
            async for raw in reader:
                try:
                    obj = json.loads(raw.decode("utf-8").strip() or "{}")
                except Exception:
                    continue
                cmd = obj.get("cmd")
                if cmd == "status":
                    await self.emit({"ev": "status",
                                     "signed_in": await self.steam.is_signed_in(),
                                     "mode": getattr(self.steam, "_mode", "hidden")})
                elif cmd == "list":
                    await self.push_list()
                elif cmd == "login":
                    await self.do_login()
                elif cmd == "open":
                    await self.do_open(obj.get("name", ""))
                elif cmd == "send":
                    await self.do_send(obj.get("text", ""))
        except (asyncio.IncompleteReadError, ConnectionResetError):
            pass
        finally:
            self.clients.discard(writer)
            try:
                writer.close()
            except Exception:
                pass

    async def run(self) -> None:
        server = await asyncio.start_server(self.handle, HOST, PORT)
        asyncio.create_task(self.poll_loop())
        async with server:
            await server.serve_forever()


if __name__ == "__main__":
    try:
        asyncio.run(Daemon().run())
    except KeyboardInterrupt:
        pass
