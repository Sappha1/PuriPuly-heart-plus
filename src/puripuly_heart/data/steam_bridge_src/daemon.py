# -*- coding: utf-8 -*-
"""Steam browser helper — the companion process the PuriPuly app controls.

Runs in the isolated venv (it has Playwright); the app runs from its own clean
venv and never imports Playwright. They speak newline-delimited JSON over a
localhost TCP socket — the same shape as the overlay/OCR helpers.

Reads/sends through Steam's in-memory web-chat model (see steam_page.py), which
works headless. Every message carries its real sender account id, so "me vs
them" is exact — no echo-guessing tracker needed. Steam BBCode is parsed here
into clean text + image URLs so the app renders embeds, not raw [img]/[url] tags.

The helper loads Steam immediately on launch (pre-warm) so switching to the tab
is instant, and pushes current state to every client that connects.

Protocol
  app -> daemon (one JSON object per line):
    {"cmd":"status"}                 ask for a status event
    {"cmd":"login"}                  open a VISIBLE window to sign in, then hide
    {"cmd":"list"}                   (re)send the conversation list
    {"cmd":"open","acct":123}        open that chat; load history; start bridging
    {"cmd":"send","acct":123,"text":"..."}   send text to that chat
  daemon -> app:
    {"ev":"status","signed_in":bool,"mode":"hidden|login|starting"}
    {"ev":"own","acct":123}
    {"ev":"conversations","items":[{"acct":123,"name":"...","avatar":"..."}]}
    {"ev":"opened","acct":123,"ok":bool}
    {"ev":"history","acct":123,"messages":[MSG,...]}
    {"ev":"inbound","acct":123,"message":MSG}
    {"ev":"sent","ok":bool}
    {"ev":"log","text":"..."}
  where MSG = {"from_me":bool,"text":"...","images":["url",...],
               "name":"...","avatar":"..."}
"""
from __future__ import annotations

import asyncio
import contextlib
import os
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from steam_page import SteamPage

PROFILE = str(Path(__file__).resolve().parent.parent / "steamprobe-profile")
HOST, PORT = "127.0.0.1", 8791

# Diagnostic log (append-only) so the live inbound path can be inspected WITHOUT
# a browser probe (which would fight the running app for the Steam profile).
import time
_DIAG = Path(__file__).resolve().parent / "diag.log"


def _diag(msg: str) -> None:
    try:
        with open(_DIAG, "a", encoding="utf-8") as f:
            f.write(time.strftime("%H:%M:%S ") + msg + "\n")
    except Exception:
        pass

# ── Steam BBCode → clean text + images + stickers ────────────────────────────
_URL_IN = re.compile(r"https?://[^\s\]\[]+")
_URLTAG_RE = re.compile(r"\[url=([^\]]+)\](.*?)\[/url\]", re.I | re.S)
_STICKER_RE = re.compile(r'\[sticker[^\]]*type="([^"]+)"[^\]]*\](?:\[/sticker\])?', re.I)
_IMGSRC_RE = re.compile(r'\[img\s+src=(?:"([^"]+)"|([^\s\]]+))[^\]]*\](?:\[/img\])?', re.I)
_IMG_RE = re.compile(r"\[img[^\]]*\](.*?)\[/img\]", re.I | re.S)
_OG_RE = re.compile(r"\[og\b([^\]]*)\](?:\[/og\])?", re.I)
_LOBBY_RE = re.compile(r"\[lobbyinvite[^\]]*\](?:\[/lobbyinvite\])?", re.I)
_EMOTICON_RE = re.compile(r"\[emoticon[^\]]*\]", re.I)
_TAG_RE = re.compile(r"\[/?[a-z][^\]]*\]", re.I)
# Bare image-host URLs in the TEXT (Steam includes the URL alongside the [img]
# tag when an image is pasted) — show only the picture, never the raw URL.
_IMGHOST_RE = re.compile(
    r"https?://(?:images\.steamusercontent\.com|steamuserimages-a\.akamaihd\.net)/\S+",
    re.I)
_STICKER_URL = "https://community.fastly.steamstatic.com/economy/sticker/{}/sticker.png"


def parse_bbcode(raw: str) -> tuple[str, list[str], list[str]]:
    """Return (display_text, image_urls, sticker_urls)."""
    images: list[str] = []
    stickers: list[str] = []
    text = raw or ""

    text = _STICKER_RE.sub(
        lambda m: (stickers.append(_STICKER_URL.format(m.group(1))) or " "), text)
    text = _IMGSRC_RE.sub(
        lambda m: (images.append(m.group(1) or m.group(2)) or " "), text)
    text = _IMG_RE.sub(
        lambda m: ((lambda u: images.append(u[-1]) if u else None)
                   (_URL_IN.findall(m.group(1) or "")) or " "), text)

    def _og(m: "re.Match") -> str:
        blk = m.group(1) or ""
        u = re.search(r'url="([^"]+)"', blk)
        img = re.search(r'img="([^"]+)"', blk)
        if img:
            images.append(img.group(1))
        return (u.group(1) + " ") if u else " "

    text = _OG_RE.sub(_og, text)
    # Server-fetched messages carry emotes as [emoticon]name[/emoticon] —
    # convert to the ː-token form the app's renderer understands (the CDN
    # serves ANY emote name; ownership belongs to the sender, not us).
    text = re.sub(r"\[emoticon\]([A-Za-z0-9_]+)\[/emoticon\]",
                  lambda m: "ː" + m.group(1) + "ː", text, flags=re.I)
    text = _LOBBY_RE.sub(" 🎮 game invite ", text)
    text = _URLTAG_RE.sub(lambda m: (m.group(2).strip() or m.group(1).strip()), text)
    text = _EMOTICON_RE.sub(" ", text)
    text = _TAG_RE.sub("", text)
    # Pasted-image messages carry the raw URL as text too — fold it into images
    # (dedup) and drop it from the text so only the picture renders.
    for u in _IMGHOST_RE.findall(text):
        u = u.rstrip(".,);")
        if u not in images:
            images.append(u)
    text = _IMGHOST_RE.sub(" ", text)
    text = re.sub(r"[ \t]*\n[ \t]*", " ", text)
    text = re.sub(r"\s{2,}", " ", text).strip()
    return text, images, stickers


class Daemon:
    def __init__(self) -> None:
        self.steam = SteamPage(PROFILE)
        self.own = 0
        self.own_avatar = ""
        self.own_name = ""
        self.own_state = 1
        self.own_invites = 0
        self.own_invisible = False
        self.own_ingame = False
        self.own_game = ""
        self.emoticons: list[str] = []
        self.stickers: list[str] = []
        self.effects: list[str] = []
        self.signed = False
        self.active: int | None = None
        self.seen_count = 0
        self._typing = False
        self._last_ts = 0                     # newest message time seen (server-fetch poll)
        self._seen_keys: set = set()          # (ts, from, text) already emitted, for dedup
        self._sent_pending: list[str] = []   # texts we sent, for echo dedup
        self.convos: dict[int, dict] = {}   # acct -> friend dict (name/avatar/status)
        self._last_sig = None               # signature of last-pushed friends list
        self.clients: set[asyncio.StreamWriter] = set()
        self._started = False
        self._start_lock: asyncio.Lock | None = None

    async def emit(self, obj: dict, only: asyncio.StreamWriter | None = None) -> None:
        line = (json.dumps(obj, ensure_ascii=False) + "\n").encode("utf-8")
        targets = [only] if only is not None else list(self.clients)
        for w in targets:
            try:
                w.write(line)
                await w.drain()
            except Exception:
                self.clients.discard(w)

    async def ensure_started(self, mode: str = "hidden") -> None:
        if self._started:
            return
        # Serialize: prewarm and the first client connect both call this — without
        # the lock, two Edge launches race on the same profile and one crashes.
        if self._start_lock is None:
            self._start_lock = asyncio.Lock()
        async with self._start_lock:
            if self._started:
                return
            await self.emit({"ev": "status", "signed_in": False, "mode": "starting"})
            await self.steam.start(mode=mode)
            self.signed = await self.steam.is_signed_in()
            await self._load_own()
            if self.signed:
                await self.refresh_list()
                # Favorites arrive a beat after the friends list — retry briefly so
                # the Favorites section isn't empty on first paint.
                for _ in range(6):
                    if any(v.get("fav") for v in self.convos.values()):
                        break
                    await asyncio.sleep(1.0)
                    await self.refresh_list()
                with contextlib.suppress(Exception):
                    self.emoticons = await self.steam.list_emoticons()
                    with contextlib.suppress(Exception):
                        self.stickers = await self.steam.list_stickers()
                        self.effects = await self.steam.list_effects()
                        _diag(f"STICKERS {len(self.stickers)} EMOTES {len(self.emoticons)}"
                              f" EFFECTS {self.effects}")
                    with contextlib.suppress(Exception):
                        import json as _json
                        _diag("PICKER-RECON " + _json.dumps(
                            await self.steam.dump_picker_recon())[:1800])
                # warm recent chats in the background so opening is instant
                asyncio.create_task(self.steam.preload_recent(20))
            self._started = True

    async def _load_own(self) -> None:
        # Own persona (name) loads a beat after sign-in — retry briefly.
        for _ in range(8):
            info = await self.steam.own_info()
            self.own = info.get("acct", 0) or self.own
            self.own_avatar = info.get("avatar", "") or self.own_avatar
            self.own_state = info.get("state", 1) or 1
            self.own_invites = info.get("invites", 0) or 0
            self.own_invisible = bool(info.get("invisible"))
            self.own_ingame = bool(info.get("ingame"))
            self.own_game = info.get("game", "") or ""
            nm = info.get("name", "")
            if nm:
                self.own_name = nm
                _diag(f"OWN-INITIAL {info}")
                return
            await asyncio.sleep(0.5)

    async def refresh_list(self) -> None:
        items = await self.steam.list_friends()   # full friends list + status
        self.convos = {i["acct"]: i for i in items}

    def _friends_sig(self):
        # top-6 recently-messaged order, so RECENT re-orders on new messages
        recent = tuple(sorted(self.convos, key=lambda a: -(self.convos[a].get("last_chat") or 0))[:6])
        return (recent, tuple(sorted(
            (a, v.get("state"), v.get("ingame"), v.get("game"), v.get("fav"),
             v.get("name"), v.get("unread"), tuple(v.get("groups") or ()))
            for a, v in self.convos.items())))

    async def push_state(self, only: asyncio.StreamWriter | None = None) -> None:
        """Send everything a (new) client needs: status, own id, friends list."""
        await self.emit({"ev": "status", "signed_in": self.signed, "mode": "hidden"}, only)
        await self.emit({"ev": "own", "acct": self.own, "avatar": self.own_avatar,
                         "name": self.own_name, "state": self.own_state,
                         "invites": self.own_invites, "invisible": self.own_invisible,
                         "ingame": self.own_ingame, "game": self.own_game,
                         "emoticons": self.emoticons, "stickers": self.stickers, "effects": self.effects}, only)
        await self.emit({"ev": "friends",
                         "items": list(self.convos.values())}, only)
        self._last_sig = self._friends_sig()

    async def do_login(self) -> None:
        if self.signed:
            await self.push_state()     # spam-clicked sign-in: already done
            return
        await self.emit({"ev": "login_progress", "stage": "opening"})
        await self.steam.restart(mode="login")
        await self.emit({"ev": "status", "signed_in": False, "mode": "login"})
        await self.emit({"ev": "login_progress", "stage": "waiting"})
        ok = await self.steam.wait_until_signed_in(timeout_s=300)
        if ok:
            await self.emit({"ev": "login_progress", "stage": "finishing"})
        await self.steam.restart(mode="hidden")
        self._started = True
        self.signed = ok
        await self._load_own()
        if ok:
            await self.refresh_list()
        await self.push_state()

    def _name(self, acct: int) -> str:
        c = self.convos.get(acct, {})
        return c.get("nick") or c.get("name", "") or "Them"

    def _avatar(self, acct: int) -> str:
        return self.convos.get(acct, {}).get("avatar", "")

    def _shape(self, m: dict) -> dict:
        """Raw store message -> the app-facing MSG shape (parsed + avatars)."""
        from_me = (m["from"] == self.own)
        text, images, stickers = parse_bbcode(m["text"])
        acct = self.active or 0
        return {
            "from_me": from_me,
            "text": text,
            "images": images,
            "stickers": stickers,
            "ts": int(m.get("ts") or 0),
            "name": (self.own_name or "You") if from_me else self._name(acct),
            "avatar": self.own_avatar if from_me else self._avatar(acct),
        }

    async def do_open(self, acct: int) -> None:
        self._typing = False
        ok = await self.steam.open_conversation(acct)
        # One-shot recon for image-send: dump the chat's file-upload internals to
        # diag.log so the upload flow can be implemented without a separate probe.
        if not getattr(self, "_dumped_upload", False):
            self._dumped_upload = True
            with contextlib.suppress(Exception):
                info = await self.steam.dump_upload_methods(acct)
                _diag("UPLOAD-RECON " + json.dumps(info, ensure_ascii=False)[:8000])
            with contextlib.suppress(Exception):
                rec = await self.steam.dump_own_recon()
                _diag("OWN-RECON " + json.dumps(rec, ensure_ascii=False)[:8000])
        # Always become active on the requested friend and ALWAYS emit a history
        # event for that acct — even if open_conversation() couldn't get-or-create
        # the chat (a friend you've never messaged, or a CM race). read_messages()
        # is acct-scoped and returns [] for a missing chat, so the app swaps to the
        # correct (possibly empty) history instead of leaving the PREVIOUS friend's
        # messages on screen (the "wrong chat under this tab" desync).
        self.active = acct
        msgs = []
        for _ in range(6):
            msgs = await self.steam.read_messages(acct)
            if msgs:
                break
            await asyncio.sleep(0.4)
        self.seen_count = len(msgs)
        # Seed the server-fetch dedup from the loaded history so the live poll only
        # emits messages that arrive AFTER this open.
        self._last_ts = max((m.get("ts") or 0 for m in msgs), default=0)
        self._seen_keys = {(m.get("ts"), m.get("from"), m.get("text")) for m in msgs}
        _diag(f"OPEN acct={acct} ok={ok} count={len(msgs)} last_ts={self._last_ts}")
        history = [self._shape(m) for m in msgs[-40:]]
        await self.emit({"ev": "history", "acct": acct, "messages": history})
        await self.emit({"ev": "opened", "acct": acct, "ok": ok})

    async def do_send(self, acct: int, text: str) -> None:
        # Remember what we sent so the poll can tell an app-send (already shown
        # optimistically) from a message the user sent on another device.
        self._sent_pending.append(text)
        if len(self._sent_pending) > 20:
            self._sent_pending.pop(0)
        ok = await self.steam.send(acct, text)
        await self.emit({"ev": "sent", "ok": ok})

    async def poll_loop(self) -> None:
        ticks = 0
        while True:
            await asyncio.sleep(1.3)
            ticks += 1
            # Refresh the friends list (status changes) every ~12s, but only
            # push when something actually changed, so the list doesn't rebuild
            # (and scroll-jump) needlessly.
            if self._started and self.signed and ticks % 46 == 23:
                # emote/sticker stores fill late and grow with purchases —
                # re-list occasionally and re-push own info when they change
                try:
                    em = await self.steam.list_emoticons()
                    st = await self.steam.list_stickers()
                    if ((em and em != self.emoticons)
                            or (st and st != self.stickers)):
                        self.emoticons = em or self.emoticons
                        self.stickers = st or self.stickers
                        _diag(f"RELIST emotes={len(self.emoticons)} stickers={len(self.stickers)}")
                        await self.emit({"ev": "own", "acct": self.own,
                                         "avatar": self.own_avatar,
                                         "name": self.own_name, "state": self.own_state,
                                         "invites": self.own_invites,
                                         "invisible": self.own_invisible,
                                         "ingame": self.own_ingame, "game": self.own_game,
                                         "emoticons": self.emoticons,
                                         "stickers": self.stickers, "effects": self.effects})
                except Exception:
                    pass
            if self._started and self.signed and ticks % 9 == 0:
                try:
                    await self.refresh_list()
                    sig = self._friends_sig()
                    if sig != self._last_sig:
                        self._last_sig = sig
                        await self.emit({"ev": "friends", "items": list(self.convos.values())})
                except Exception:
                    pass
            if self._started and self.signed and ticks % 9 == 0:
                with contextlib.suppress(Exception):
                    info = await self.steam.own_info()
                    sig = (info.get("state"), bool(info.get("invisible")),
                           bool(info.get("ingame")), info.get("game", ""),
                           info.get("invites", 0), info.get("name", ""))
                    if sig != getattr(self, "_own_sig", None):
                        self._own_sig = sig
                        _diag(f"OWN {info}")
                        self.own_state = info.get("state", 1) or 1
                        self.own_invisible = bool(info.get("invisible"))
                        self.own_ingame = bool(info.get("ingame"))
                        self.own_game = info.get("game", "") or ""
                        self.own_invites = info.get("invites", 0) or 0
                        if info.get("name"):
                            self.own_name = info["name"]
                        if info.get("avatar"):
                            self.own_avatar = info["avatar"]
                        await self.emit({"ev": "own", "acct": self.own,
                                         "avatar": self.own_avatar,
                                         "name": self.own_name, "state": self.own_state,
                                         "invites": self.own_invites,
                                         "invisible": self.own_invisible,
                                         "ingame": self.own_ingame, "game": self.own_game,
                                         "emoticons": self.emoticons, "stickers": self.stickers, "effects": self.effects})
            if not self.active:
                continue
            # keep the chat warm/foreground every ~4s (helps typing + mark-read).
            if ticks % 3 == 0:
                with contextlib.suppress(Exception):
                    await self.steam.reactivate(self.active)
            # typing indicator
            try:
                typing = await self.steam.is_typing(self.active)
                if typing != self._typing:
                    self._typing = typing
                    _diag(f"typing={typing} acct={self.active}")
                    await self.emit({"ev": "typing", "acct": self.active,
                                     "typing": typing, "name": self._name(self.active)})
            except Exception:
                pass
            # LIVE inbound = SERVER FETCH, not the local array. Steam does not append
            # new messages to this background session's array (push is withheld while
            # the user's real client is primary), but GetMessagesFromTimeRange pulls
            # them fresh from the server. Fetch a small trailing window (every ~2.6s
            # to stay light on the server) and emit any (ts,from,text) not shown yet.
            if ticks % 2 != 0:
                continue
            try:
                fresh = await self.steam.fetch_messages(self.active, self._last_ts - 3)
            except Exception as exc:
                _diag(f"fetch error: {exc}")
                fresh = []
            # Emit strictly in time order — same-second messages from both sides can
            # come back interleaved, which made replies render before the message
            # they answered (and mis-grouped blocks in the app).
            fresh.sort(key=lambda m: (m.get("ts") or 0, m.get("ordinal") or 0))
            for m in fresh:
                key = (m.get("ts"), m.get("from"), m.get("text"))
                if key in self._seen_keys:
                    continue
                self._seen_keys.add(key)
                self._last_ts = max(self._last_ts, m.get("ts") or 0)
                if m["from"] == self.own:
                    # An app-send we already showed optimistically → swallow one echo.
                    # A message the user sent from ANOTHER device → show it. Steam
                    # normalizes sent emote codes :name: to ːnameː (U+02D0), so
                    # normalize both sides before comparing or emote echoes leak.
                    raw = (m.get("text") or "").strip().replace("ː", ":").replace("ː", ":")
                    matched = next((s for s in self._sent_pending
                                    if s.strip().replace("ː", ":") == raw), None)
                    if matched is not None:
                        self._sent_pending.remove(matched)
                        continue
                _diag(f"INBOUND(fetch) from={m.get('from')} own={self.own} {(m.get('text') or '')[:30]!r}")
                if "[sticker" in (m.get("text") or ""):
                    _diag("RAWSTICKER " + repr((m.get("text") or "")[:400]))
                await self.emit({"ev": "inbound", "acct": self.active,
                                 "message": self._shape(m)})
                # The view clears its typing indicator when a message lands; if the
                # friend is STILL composing, our cached state would suppress the
                # re-emit — reset it so the next poll re-announces the typing.
                self._typing = False
            # keep the dedup set from growing without bound over a long session
            if len(self._seen_keys) > 800:
                cutoff = self._last_ts - 7200
                self._seen_keys = {k for k in self._seen_keys if (k[0] or 0) >= cutoff}

    async def handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        self.clients.add(writer)
        try:
            await self.ensure_started()
            await self.push_state(only=writer)   # bring this client up to date
            async for raw in reader:
                try:
                    obj = json.loads(raw.decode("utf-8").strip() or "{}")
                except Exception:
                    continue
                cmd = obj.get("cmd")
                if cmd == "status":
                    await self.push_state(only=writer)
                elif cmd == "list":
                    await self.refresh_list()
                    await self.push_state(only=writer)
                elif cmd == "login":
                    await self.do_login()
                elif cmd == "open":
                    await self.do_open(int(obj.get("acct", 0)))
                elif cmd == "send":
                    await self.do_send(int(obj.get("acct", 0)), obj.get("text", ""))
                elif cmd == "send_image":
                    acct = int(obj.get("acct", 0))
                    path = obj.get("path", "")
                    result = {"step": "read", "err": "no file"}
                    try:
                        import base64
                        data = Path(path).read_bytes()
                        ext = Path(path).suffix.lower().lstrip(".") or "png"
                        mime = {"jpg": "image/jpeg", "jpeg": "image/jpeg",
                                "gif": "image/gif", "webp": "image/webp"}.get(ext, "image/png")
                        b64 = base64.b64encode(data).decode("ascii")
                        result = await self.steam.send_image(
                            acct, b64, Path(path).name, mime,
                            spoiler=bool(obj.get("spoiler")))
                    except Exception as exc:
                        result = {"step": "daemon", "err": str(exc)}
                    _diag(f"SEND_IMAGE acct={acct} -> {result}")
                    await self.emit({"ev": "image_sent", "acct": acct,
                                     "ok": bool(result.get("ok")),
                                     "detail": result})
                elif cmd == "send_sticker" or cmd == "send_effect":
                    kind = "sticker" if cmd == "send_sticker" else "effect"
                    with contextlib.suppress(Exception):
                        res = await self.steam.send_sticker_or_effect(
                            int(obj.get("acct", 0)), str(obj.get("name", "")), kind)
                        _diag(f"STICKFX kind={kind} name={obj.get('name')!r} -> {res}")
                        if not (res or {}).get("ok"):
                            recon = await self.steam.dump_stickfx_methods()
                            import json as _json
                            _diag("STICKFX-RECON " + _json.dumps(recon)[:1800])
                elif cmd == "signout":
                    with contextlib.suppress(Exception):
                        await self.steam.sign_out()
                    self.signed = False
                    self.convos = {}
                    self.active = 0
                    await self.emit({"ev": "status", "signed_in": False,
                                     "mode": "hidden"})
                elif cmd == "quit":
                    # Module switched off: free ALL RAM (browser + this process).
                    _diag("QUIT by app request")
                    with contextlib.suppress(Exception):
                        await self.steam.close()
                    os._exit(0)
                elif cmd == "favorite":
                    with contextlib.suppress(Exception):
                        await self.steam.set_favorite(int(obj.get("acct", 0)), bool(obj.get("on")))
                        await self.refresh_list()
                        self._last_sig = self._friends_sig()
                        await self.emit({"ev": "friends", "items": list(self.convos.values())})
                elif cmd == "status":
                    with contextlib.suppress(Exception):
                        await self.steam.set_status(int(obj.get("state", 1)))
                        await self._load_own()
                        await self.emit({"ev": "own", "acct": self.own, "avatar": self.own_avatar,
                                         "name": self.own_name, "state": self.own_state,
                                         "invites": self.own_invites, "invisible": self.own_invisible,
                                         "emoticons": self.emoticons, "stickers": self.stickers, "effects": self.effects})
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
        # Pre-warm: load Steam now so the first tab open is instant.
        asyncio.create_task(self._prewarm())
        asyncio.create_task(self.poll_loop())
        async with server:
            await server.serve_forever()

    async def _prewarm(self) -> None:
        try:
            await self.ensure_started()
        except Exception:
            pass


if __name__ == "__main__":
    try:
        asyncio.run(Daemon().run())
    except KeyboardInterrupt:
        pass
