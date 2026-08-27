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
# ugcids of image uploads we already committed: beginfileupload sometimes
# hands back the previous upload's slot, binding the new message to the wrong
# file (friend sees a bare filedownload link or an old picture). send_image
# rejects any begin response whose ugcid is in this list.
_SENT_UGC = Path(__file__).resolve().parent / "sent_ugcids.json"


def _load_sent_ugcids() -> list:
    try:
        import json as _j
        v = _j.loads(_SENT_UGC.read_text(encoding="utf-8"))
        return [str(x) for x in v][-50:] if isinstance(v, list) else []
    except Exception:
        return []


def _record_sent_ugcid(ugcid: str) -> list:
    ids = _load_sent_ugcids()
    if str(ugcid) and str(ugcid) not in ids:
        ids.append(str(ugcid))
        ids = ids[-50:]
        try:
            import json as _j
            _SENT_UGC.write_text(_j.dumps(ids), encoding="utf-8")
        except Exception:
            pass
    return ids


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
_ROOMFX_RE = re.compile(r'\[roomeffect[^\]]*type="([^"]+)"[^\]]*\](?:\[/roomeffect\])?', re.I)
_ROOMFX_ICON = {"balloons": "🎈", "confetti": "🎉",
                "firework": "🎆", "goldfetti": "🎊"}
_EMOTICON_RE = re.compile(r"\[emoticon[^\]]*\]", re.I)
_TAG_RE = re.compile(r"\[/?[a-z][^\]]*\]", re.I)
# Bare image-host URLs in the TEXT (Steam includes the URL alongside the [img]
# tag when an image is pasted) — show only the picture, never the raw URL.
_IMGHOST_RE = re.compile(
    r"https?://(?:images\.steamusercontent\.com|cdn\.steamusercontent\.com|steamuserimages-a\.akamaihd\.net)/\S+",
    re.I)
# r508: a shared file (mp4, zip, ...) also lives on cdn.steamusercontent.com —
# under /filedownload/ — and is NOT an image. Folding it into the image list
# stripped it from the text and the picture renderer silently failed, so the
# recipient never saw that a file arrived. Only fold real image URLs.
_FILE_URL_RE = re.compile(r"/filedownload/|\.(?:mp4|webm|mov|mkv|zip|rar|7z|pdf|txt|exe)(?:[?#]|$)", re.I)
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

    # a link preview ([og url="U" img=...]) rides ALONGSIDE the URL text in
    # the same message — re-emitting its url duplicated the link ("U U") and
    # broke the own-echo dedupe. Only emit it when the URL isn't already there.
    _og_free = _OG_RE.sub(" ", text)

    def _og(m: "re.Match") -> str:
        blk = m.group(1) or ""
        u = re.search(r'url="([^"]+)"', blk)
        img = re.search(r'img="([^"]+)"', blk)
        if img:
            images.append(img.group(1))
        if u and u.group(1) not in _og_free:
            return u.group(1) + " "
        return " "

    text = _OG_RE.sub(_og, text)
    text = text.replace("\\[", "\x13").replace("\\]", "\x14")
    # Server-fetched messages carry emotes as [emoticon]name[/emoticon] —
    # convert to the ː-token form the app's renderer understands (the CDN
    # serves ANY emote name; ownership belongs to the sender, not us).
    text = re.sub(r"\[emoticon\]([A-Za-z0-9_]+)\[/emoticon\]",
                  lambda m: "ː" + m.group(1) + "ː", text, flags=re.I)
    text = _ROOMFX_RE.sub(
        lambda m: f" {_ROOMFX_ICON.get(m.group(1).lower(), '✨')} {m.group(1)} ",
        text)
    text = _LOBBY_RE.sub(" 🎮 game invite ", text)
    text = _URLTAG_RE.sub(lambda m: (m.group(2).strip() or m.group(1).strip()), text)
    text = _EMOTICON_RE.sub(" ", text)
    text = re.sub(r"\[quote[^\]]*\]", "\x11q\x12", text, flags=re.I)
    text = re.sub(r"\[/quote\]", "\x11/q\x12", text, flags=re.I)
    text = re.sub(r"\[code[^\]]*\]", "\x11c\x12", text, flags=re.I)
    text = re.sub(r"\[/code\]", "\x11/c\x12", text, flags=re.I)
    text = _TAG_RE.sub("", text)
    # Pasted-image messages carry the raw URL as text too — fold it into images
    # (dedup) and drop it from the text so only the picture renders.
    for u in _IMGHOST_RE.findall(text):
        u = u.rstrip(".,);")
        if _FILE_URL_RE.search(u):
            continue            # a shared FILE: keep it in the text as a link
        if u not in images:
            images.append(u)
    text = _IMGHOST_RE.sub(lambda m: (m.group(0) if _FILE_URL_RE.search(m.group(0))
                                      else " "), text)
    text = re.sub(r"[ \t]{2,}", " ", text)
    text = re.sub(r" ?\n ?", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    # the same link repeated back-to-back is always preview residue
    text = re.sub(r"(https?://\S+)(?:\s+\1)+", r"\1", text)
    text = text.replace("\x13", "[").replace("\x14", "]")
    return text, images, stickers


class Daemon:
    def __init__(self) -> None:
        self.steam = SteamPage(PROFILE, proxy=os.environ.get("PPH_STEAM_PROXY", ""))
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
        self._fetch_err = None                # last fetch failure (dedup diag spam)
        self._last_recv: dict[int, int] = {}  # acct -> last m_rtLastMessageReceived seen
        self._recv_baselined = False          # first sweep only records, never emits
        self._seen_keys: set = set()          # (ts, from, text) already emitted, for dedup
        self._emitted: dict = {}              # acct -> (ts, ordinal) high-water mark: never re-emit at/below
        self._sent_pending: list[str] = []   # texts we sent, for echo dedup
        self._img_pending = 0                # app image-sends awaiting their echo
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

    async def _emit_own(self) -> None:
        await self.emit({"ev": "own", "acct": self.own, "avatar": self.own_avatar,
                         "name": self.own_name, "state": self.own_state,
                         "invites": self.own_invites, "invisible": self.own_invisible,
                         "emoticons": self.emoticons, "stickers": self.stickers,
                         "effects": self.effects})

    async def _own_refresh_later(self) -> None:
        for delay in (2.0, 6.0):
            await asyncio.sleep(delay)
            with contextlib.suppress(Exception):
                await self._load_own()
                await self._emit_own()

    async def _revive_friends(self) -> None:
        """Persona state 0 signs the WEB session out of the friends network,
        and setting a state again does NOT reconnect it (verified: st_self
        stays 0) — only a page reload re-establishes the session."""
        with contextlib.suppress(Exception):
            _diag("REVIVE friends session (page reload after persona 0)")
            await self.emit({"ev": "status", "signed_in": False, "mode": "starting"})
            self._reloading = True   # pause the poll loop over the reload
            try:
                await self.steam.reload()
                for _ in range(30):
                    await asyncio.sleep(1.0)
                    if await self.steam.is_signed_in():
                        break
            finally:
                self._reloading = False
            self.signed = await self.steam.is_signed_in()
            await self._load_own()
            if self.signed:
                await self.refresh_list()
            await self.push_state()
            await self._emit_own()
            if self.active:
                with contextlib.suppress(Exception):
                    await self.do_open(self.active)
            _diag(f"REVIVE done signed={self.signed}")

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
        # never clobber a populated list with an empty read — the page is
        # mid-reload (401 recovery) and an empty push would make the UI treat
        # every open tab as stale and wipe them
        if items or not self.convos:
            self.convos = {i["acct"]: i for i in items}

    def _friends_sig(self):
        # top-6 recently-messaged order, so RECENT re-orders on new messages
        recent = tuple(sorted(self.convos, key=lambda a: -(self.convos[a].get("last_chat") or 0))[:6])
        return (recent, tuple(sorted(
            (a, v.get("state"), v.get("ingame"), v.get("game"), v.get("fav"),
             v.get("name"), v.get("unread"), v.get("extra"),
             tuple(v.get("groups") or ()))
            for a, v in self.convos.items())))

    async def push_state(self, only: asyncio.StreamWriter | None = None) -> None:
        """Send everything a (new) client needs: status, own id, friends list."""
        _netblk = False
        if not self.signed:
            with contextlib.suppress(Exception):
                _netblk = bool(self.steam.net_blocked())
        await self.emit({"ev": "status", "signed_in": self.signed, "mode": "hidden",
                         "net_blocked": _netblk}, only)
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

    async def do_retry(self) -> None:
        """Blocked-network recovery: a full hidden-browser restart re-runs the
        page load, picking up a proxy/VPN the user turned on after the fact.
        No status is emitted until the outcome is known — the view keeps its
        'retrying' overlay instead of flashing 'Not signed in'."""
        if self.signed:
            await self.push_state()
            return
        with contextlib.suppress(Exception):
            await self.steam.restart(mode="hidden")
        self._started = True
        self.signed = await self.steam.is_signed_in()
        await self._load_own()
        if self.signed:
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
            "ord": int(m.get("ordinal") or 0),
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
        # AUGMENT with a live server fetch: while the real Steam client is
        # primary the local array gets NO friend messages (they only flow via
        # GetMessagesFromTimeRange), so the array alone is missing every friend
        # line since this page loaded — and everything from app-closed gaps.
        with contextlib.suppress(Exception):
            # Flat 48h window: even a FRESH page load's array lacks recent
            # friend lines, so anchoring to page-load time fetched nothing.
            # Dedup makes the overlap free; history is capped at 40 anyway.
            _since = int(time.time()) - 48 * 3600
            fresh = await self.steam.fetch_messages(acct, _since)
            if fresh:
                have = {(m.get("ts"), m.get("from"), m.get("text")) for m in msgs}
                add = [m for m in fresh
                       if (m.get("ts"), m.get("from"), m.get("text")) not in have]
                if add:
                    _diag(f"OPEN-AUGMENT acct={acct} +{len(add)} server msgs "
                          f"(since={_since})")
                    msgs = msgs + add
                    msgs.sort(key=lambda m: (m.get("ts") or 0,
                                             m.get("ordinal") or 0))
        self.seen_count = len(msgs)
        # Seed the server-fetch dedup from the loaded history so the live poll only
        # emits messages that arrive AFTER this open.
        self._last_ts = max((m.get("ts") or 0 for m in msgs), default=0)
        # ADDITIVE: replacing this set wiped the sweep's dedup keys, and since
        # Steam withholds push the swept message was absent from local history
        # — every open re-emitted it as a duplicate
        self._seen_keys |= {(m.get("ts"), m.get("from"), m.get("text")) for m in msgs}
        _mark = max((int(m.get("ts") or 0) for m in msgs), default=0)
        if _mark > self._emitted.get(acct, 0):
            self._emitted[acct] = _mark
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

    async def _sweep_deliver(self, acct: int, fresh: list, src: str) -> None:
        """Watermark/dedup/own-echo gate + emit for background-chat fetches —
        shared by the clock sweep and the round-robin verify fetch."""
        fresh.sort(key=lambda m: (m.get("ts") or 0, m.get("ordinal") or 0))
        for m in fresh:
            mk = int(m.get("ts") or 0)
            if mk < self._emitted.get(acct, 0):
                continue
            key = (m.get("ts"), m.get("from"), m.get("text"))
            if key in self._seen_keys:
                continue
            self._seen_keys.add(key)
            self._emitted[acct] = max(self._emitted.get(acct, 0), mk)
            if m.get("from") == self.own:
                raw_txt = (m.get("text") or "")
                if (self._img_pending > 0
                        and raw_txt.lstrip().startswith("[img")):
                    self._img_pending -= 1
                    continue
                def _canon_s(s: str) -> str:
                    s = (s or "").replace("ː", ":").replace("ˑ", ":")
                    s = re.sub(r"\[emoticon\]([A-Za-z0-9_]+)\[/emoticon\]",
                               r":\1:", s)
                    s = re.sub(r"\[url=[^\]]*\]", "", s)
                    s = s.replace("[/url]", "")
                    return " ".join(s.split())
                raw_c = _canon_s(raw_txt)
                matched = next((s for s in self._sent_pending
                                if _canon_s(s) == raw_c), None)
                if matched is not None:
                    self._sent_pending.remove(matched)
                    continue
            _diag(f"INBOUND({src}) acct={acct} from={m.get('from')}")
            await self.emit({"ev": "inbound", "acct": acct,
                             "message": self._shape(m)})

    async def poll_loop(self) -> None:
        ticks = 0
        while True:
            await asyncio.sleep(1.3)
            ticks += 1
            if getattr(self, "_reloading", False):
                # a deliberate page reload is in progress — every read would
                # see a half-booted page and emit bogus empty events
                continue
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
            if self._started and self.signed and ticks % 3 == 0:
                # r508: every ~4s (was ~12s) — tab chips showed a friend as
                # offline until the user clicked the tab and the open path
                # read the live persona. Only pushes when the signature moved.
                try:
                    await self.refresh_list()
                    if (not getattr(self, "_persona_dumped", False)
                            and self.convos):
                        # one-shot: dump one OFFLINE friend's fields so the
                        # last-seen property can be identified for real
                        self._persona_dumped = True
                        with contextlib.suppress(Exception):
                            _tg = next((a for a, i in self.convos.items()
                                        if i.get("ingame")), None) or next(
                                (a for a, i in self.convos.items()
                                 if not i.get("state")), None)
                            if _tg:
                                _diag("PERSONA-KEYS "
                                      + str(await self.steam.dump_persona_keys(_tg)))
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
            if not self.clients:
                continue    # nobody listening: do NOT fetch/emit — an emit to
                            # zero clients is LOST, but the watermark/seen sets
                            # would mark it delivered and the app could never
                            # get it after reconnecting. Frozen clocks make the
                            # first post-reconnect poll pull everything missed.
            # ALL-CHATS inbound sweep: a friend messaging a chat that is NOT
            # open in the app must still surface (tab + unread + cached line)
            # like real Steam. One cheap clock read; fetch only advanced chats.
            if self._started and self.signed and ticks % 3 == 0:
                with contextlib.suppress(Exception):
                    activity = await self.steam.chat_activity()
                    if activity and not self._recv_baselined:
                        self._recv_baselined = True
                        self._last_recv.update(activity)
                    elif activity:
                        changed = [a for a, ts in activity.items()
                                   if a != self.active
                                   and ts > self._last_recv.get(a, 0)][:3]
                        for acct in changed:
                            prev = self._last_recv.get(acct, 0)
                            self._last_recv[acct] = activity[acct]
                            fresh = await self.steam.fetch_messages(
                                acct, max(prev - 3, 1))
                            await self._sweep_deliver(acct, fresh, "sweep")
                        # brand-new chats appearing post-baseline start at
                        # their current clock (handled above via changed)
                        if self.active in activity:
                            # the active poll already streams this chat — keep
                            # its baseline current so tabbing away is quiet
                            self._last_recv[self.active] = activity[self.active]

            # CLOCK-FREEZE IMMUNITY: Steam can freeze m_rtLastMessageReceived
            # for background sessions — the clock sweep then goes blind (a
            # friend's message never surfaces). Verify ONE recent chat per
            # pass with a real server fetch; the watermark + dedup gate in
            # _sweep_deliver keeps it emit-only-new.
            if self._started and self.signed and self.clients and ticks % 3 == 1:
                with contextlib.suppress(Exception):
                    _rr = [a for a, v in sorted(
                               self.convos.items(),
                               key=lambda kv: -(kv[1].get("last_chat") or 0))
                           if a != self.active
                           and (v.get("last_chat") or 0) > time.time() - 3 * 86400][:12]
                    if _rr:
                        self._rr_i = (getattr(self, "_rr_i", -1) + 1) % len(_rr)
                        _acct = _rr[self._rr_i]
                        _wm = self._emitted.get(_acct, 0)
                        fresh = await self.steam.fetch_messages(
                            _acct, (_wm - 3) if _wm else int(time.time()) - 900)
                        await self._sweep_deliver(_acct, fresh, "rr")

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
            _poll_acct = self.active   # capture ONCE: an open landing mid-
            # iteration must never re-tag this batch with another chat's acct
            try:
                fresh = await self.steam.fetch_messages(_poll_acct, self._last_ts - 3)
                self._fetch_err = None
            except Exception as exc:
                if str(exc) != self._fetch_err:
                    self._fetch_err = str(exc)
                    _diag(f"fetch error: {exc}")
                fresh = []
            # Emit strictly in time order — same-second messages from both sides can
            # come back interleaved, which made replies render before the message
            # they answered (and mis-grouped blocks in the app).
            if self.active != _poll_acct:
                continue   # chat switched while fetching — discard; the next
                           # poll re-fetches under the new chat's own window
            fresh.sort(key=lambda m: (m.get("ts") or 0, m.get("ordinal") or 0))
            for m in fresh:
                mk = int(m.get("ts") or 0)
                if mk < self._emitted.get(_poll_acct, 0):
                    continue          # STRICTLY older than the high-water mark
                                      # — an OPEN reset the fetch window; never
                                      # replay. Same-second bursts pass (their
                                      # ordinals are unreliable) and dedup via
                                      # _seen_keys instead.
                key = (m.get("ts"), m.get("from"), m.get("text"))
                if key in self._seen_keys:
                    continue
                self._seen_keys.add(key)
                self._emitted[_poll_acct] = max(self._emitted.get(_poll_acct, 0), mk)
                self._last_ts = max(self._last_ts, m.get("ts") or 0)
                if (m["from"] == self.own and self._img_pending > 0
                        and (m.get("text") or "").lstrip().startswith("[img")):
                    # echo of an app image-send (already rendered optimistically)
                    self._img_pending -= 1
                    with contextlib.suppress(Exception):
                        await self.emit({"ev": "seen", "acct": self.active,
                                         "ts": int(m.get("ts") or 0)})
                    continue
                if m["from"] == self.own:
                    # An app-send we already showed optimistically → swallow one echo.
                    # A message the user sent from ANOTHER device → show it. Steam
                    # normalizes sent emote codes :name: to ːnameː (U+02D0), so
                    # normalize both sides before comparing or emote echoes leak.
                    def _canon(s: str) -> str:
                        s = (s or "").replace("ː", ":").replace("ˑ", ":")
                        s = re.sub(r"\[emoticon\]([A-Za-z0-9_]+)\[/emoticon\]",
                                   r":\1:", s)
                        # Steam wraps bare links on echo: [url=X]X[/url] — the
                        # app sent the RAW url, so unwrap before comparing or
                        # every link send bounces back as a phantom duplicate
                        s = re.sub(r"\[url=[^\]]*\]", "", s)
                        s = s.replace("[/url]", "")
                        return " ".join(s.split())
                    raw = _canon(m.get("text") or "")
                    matched = next((s for s in self._sent_pending
                                    if _canon(s) == raw), None)
                    if matched is not None:
                        self._sent_pending.remove(matched)
                        with contextlib.suppress(Exception):
                            await self.emit({"ev": "seen", "acct": self.active,
                                             "ts": int(m.get("ts") or 0)})
                        continue
                _diag(f"INBOUND(fetch) from={m.get('from')} own={self.own} {(m.get('text') or '')[:30]!r}")
                if "[sticker" in (m.get("text") or ""):
                    _diag("RAWSTICKER " + repr((m.get("text") or "")[:400]))
                await self.emit({"ev": "inbound", "acct": _poll_acct,
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
                if cmd == "status" and "state" not in obj:
                    await self.push_state(only=writer)
                elif cmd == "list":
                    await self.refresh_list()
                    await self.push_state(only=writer)
                elif cmd == "login":
                    await self.do_login()
                elif cmd == "retry":
                    await self.do_retry()
                elif cmd == "open":
                    try:
                        await self.ensure_started()
                        await self.do_open(int(obj.get("acct", 0)))
                    except Exception as exc:
                        _diag(f"OPEN-ERR acct={obj.get('acct')} "
                              f"{type(exc).__name__}: {exc}")
                        with contextlib.suppress(Exception):
                            await self.emit({"ev": "opened",
                                             "acct": int(obj.get("acct", 0)),
                                             "ok": False})
                elif cmd == "more_history":
                    acct = int(obj.get("acct", 0))
                    before = int(obj.get("before", 0) or 0)
                    older: list = []
                    with contextlib.suppress(Exception):
                        # walk back in 14-day windows until something turns
                        # up (Steam serves months of history server-side)
                        end = (before - 1) if before else int(time.time())
                        for _hop in range(9):
                            got = await self.steam.fetch_messages_range(
                                acct, end - 14 * 86400, end)
                            if got:
                                got.sort(key=lambda m: (m.get("ts") or 0,
                                                        m.get("ordinal") or 0))
                                older = got[-60:]
                                break
                            end -= 14 * 86400
                    _diag(f"MORE-HISTORY acct={acct} before={before} "
                          f"-> {len(older)}")
                    await self.emit({"ev": "history_older", "acct": acct,
                                     "before": before,
                                     "messages": [self._shape(m)
                                                  for m in older]})
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
                        used_ids = _load_sent_ugcids()
                        result = await self.steam.send_image(
                            acct, b64, Path(path).name, mime,
                            spoiler=bool(obj.get("spoiler")), used=used_ids)
                        if (not result.get("ok")
                                and (result.get("status") == 401
                                     or "Not Logged" in str(result.get("resp", "")))):
                            # The page's HTTP login cookies go stale after a
                            # few idle hours (chat rides the WebSocket and
                            # keeps working) — a reload refreshes them from
                            # the saved login. Reload, re-open, retry once.
                            _diag("SEND_IMAGE 401 stale web session -> reload + retry")
                            self._reloading = True   # pause the poll loop —
                            # its reads of the half-booted page emit bogus
                            # empty friends / blank own-info events
                            try:
                                await self.steam.reload()
                                for _ in range(20):
                                    await asyncio.sleep(1.0)
                                    if await self.steam.is_signed_in():
                                        break
                                with contextlib.suppress(Exception):
                                    await self.steam.open_conversation(acct)
                                result = await self.steam.send_image(
                                    acct, b64, Path(path).name, mime,
                                    spoiler=bool(obj.get("spoiler")), used=used_ids)
                            finally:
                                self._reloading = False
                        # Steam's beginfileupload intermittently answers
                        # HTTP 400 with "success":10 ("A server error
                        # occurred") — a transient server-side hiccup its
                        # own client retries through. Two short retries
                        # before declaring failure.
                        for _wait in (2.0, 5.0):
                            if result.get("ok"):
                                break
                            _st = result.get("status")
                            _rs = str(result.get("resp") or "")
                            if _st in (429, 500, 502, 503) or (
                                    _st == 400 and '"success":10' in _rs
                            ) or (_st is None
                                  and result.get("step") == "py"):
                                # step "py" with no status = the evaluate
                                # itself failed (connection blip / page
                                # navigation) — the most transient class
                                _diag(f"SEND_IMAGE transient {_st} -> retry in {_wait}s")
                                await asyncio.sleep(_wait)
                                result = await self.steam.send_image(
                                    acct, b64, Path(path).name, mime,
                                    spoiler=bool(obj.get("spoiler")), used=used_ids)
                            else:
                                break
                        # "success":10 SURVIVING the backoff retries means the
                        # page's upload session is wedged (chat keeps working;
                        # observed 2026-08-21 02:16-02:44, every begin failing
                        # for half an hour). A page reload rebuilds the session
                        # — the same recovery that fixes the 401 case.
                        if (not result.get("ok")
                                and result.get("status") == 400
                                and '"success":10' in str(result.get("resp") or "")):
                            _diag("SEND_IMAGE persistent success:10 -> reload + retry")
                            self._reloading = True
                            try:
                                await self.steam.reload()
                                for _ in range(20):
                                    await asyncio.sleep(1.0)
                                    if await self.steam.is_signed_in():
                                        break
                                with contextlib.suppress(Exception):
                                    await self.steam.open_conversation(acct)
                                result = await self.steam.send_image(
                                    acct, b64, Path(path).name, mime,
                                    spoiler=bool(obj.get("spoiler")), used=used_ids)
                            finally:
                                self._reloading = False
                    except Exception as exc:
                        result = {"step": "daemon", "err": str(exc)}
                    if result.get("ok"):
                        self._img_pending += 1   # swallow its echo in the poll
                        _record_sent_ugcid(str(result.get("ugcid", "")))
                    _diag(f"SEND_IMAGE acct={acct} -> {result}")
                    await self.emit({"ev": "image_sent", "acct": acct,
                                     "ok": bool(result.get("ok")),
                                     "sid": obj.get("sid"),
                                     "detail": result})
                elif cmd == "send_sticker" or cmd == "send_effect":
                    kind = "sticker" if cmd == "send_sticker" else "effect"
                    with contextlib.suppress(Exception):
                        res = await self.steam.send_sticker_or_effect(
                            int(obj.get("acct", 0)), str(obj.get("name", "")), kind)
                        _diag(f"STICKFX kind={kind} name={obj.get('name')!r} -> {res}")
                        if (res or {}).get("ok") and kind == "sticker":
                            nm = str(obj.get("name", ""))
                            self._sent_pending.append(
                                f'[sticker type="{nm}" limit="0"][/sticker]')
                            if len(self._sent_pending) > 20:
                                self._sent_pending.pop(0)
                            if not getattr(self, "_stick_reconned", False):
                                self._stick_reconned = True
                                with contextlib.suppress(Exception):
                                    import json as _json
                                    _diag("STICKSEND-RECON " + _json.dumps(
                                        await self.steam.dump_sticker_send_recon())[:3000])
                        if not (res or {}).get("ok"):
                            recon = await self.steam.dump_stickfx_methods()
                            import json as _json
                            _diag("STICKFX-RECON " + _json.dumps(recon)[:1800])
                elif cmd == "react":
                    with contextlib.suppress(Exception):
                        res = await self.steam.react(
                            int(obj.get("acct", 0)), int(obj.get("ts", 0)),
                            int(obj.get("ord", 0)), obj.get("name", ""),
                            int(obj.get("rtype", 1)))
                        _diag(f"REACT ts={obj.get('ts')} "
                              f"name={obj.get('name')} -> {res}")
                        await self.emit({"ev": "reacted",
                                         "ok": str(res).startswith("ok:")})
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
                        st_req = int(obj.get("state", 1))
                        res = await self.steam.set_status(st_req)
                        _diag(f"STATUS state={st_req} -> {res}")
                        if st_req == 0:
                            self._persona_offline = True
                        elif getattr(self, "_persona_offline", False):
                            self._persona_offline = False
                            asyncio.create_task(self._revive_friends())
                        asyncio.create_task(self._own_refresh_later())
        except (asyncio.IncompleteReadError, ConnectionResetError):
            pass
        finally:
            self.clients.discard(writer)
            try:
                writer.close()
            except Exception:
                pass

    async def run(self) -> None:
        # HARD singleton: on Windows, SO_REUSEADDR semantics let several
        # processes bind the same port — six daemons once coexisted, stealing
        # each other's connections (the app reconnect-looped forever). An
        # SO_EXCLUSIVEADDRUSE bind makes the second daemon exit immediately.
        import socket as _socket
        lsock = _socket.socket(_socket.AF_INET, _socket.SOCK_STREAM)
        with contextlib.suppress(Exception):
            lsock.setsockopt(_socket.SOL_SOCKET,
                             getattr(_socket, "SO_EXCLUSIVEADDRUSE", -1), 1)
        try:
            lsock.bind((HOST, PORT))
            lsock.listen(64)
        except OSError:
            _diag("SINGLETON exit: another daemon owns the port")
            return
        server = await asyncio.start_server(self.handle, sock=lsock)
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
