# -*- coding: utf-8 -*-
"""Async wrapper around a hidden Edge showing Steam chat. The bridge's Steam half.

Everything the UI needs, and nothing it does not: list conversations, open one,
read the visible messages (speaker + text), read our own display name, and send.
Uses the persistent profile from the probe, so the login is already there.

Selectors are the STABLE ones the probe confirmed carry no build hash:
    .friend / .quickAccessFriend   conversation rows in the friends list
    .ChatMessageBlock              one message block
    .speakerName                   who said it
    .msg                           the message text
Only Steam's *styling* classes carry the build hash (messages_*_<hash>); these
structural ones do not, which is what makes this survivable across updates.
"""
from __future__ import annotations

import logging

logger = logging.getLogger("steam_page")

READ_MESSAGES_JS = r"""
() => {
  const clsOf = (el) => (typeof el.className === "string" ? el.className : "");
  return Array.from(document.querySelectorAll(".ChatMessageBlock")).slice(-25).map(b => {
    const sp = b.querySelector(".speakerName");
    const texts = Array.from(b.querySelectorAll(".msg"))
      .map(m => (m.innerText || "").trim()).filter(Boolean);
    return {speaker: sp ? (sp.innerText || "").trim() : "", texts};
  });
}
"""

READ_CONVERSATIONS_JS = r"""
() => {
  const clsOf = (el) => (typeof el.className === "string" ? el.className : "");
  const seen = new Set();
  const out = [];
  for (const el of document.querySelectorAll(".friend.quickAccessFriend, .friend")) {
    const name = (el.innerText || "").trim().split("\n")[0].trim();
    if (name && !seen.has(name)) { seen.add(name); out.push(name); }
  }
  return out.slice(0, 30);
}
"""

# Our own persona name — used as one of the two echo guards.
READ_OWN_NAME_JS = r"""
() => {
  const el = document.querySelector(".currentUserContainer .playerName, "
    + ".ChatRoomListHeader .playerName, .personaname, .playerName");
  return el ? (el.innerText || "").trim() : "";
}
"""


class SteamPage:
    def __init__(self, profile_dir: str):
        self._profile = profile_dir
        self._pw = None
        self._ctx = None
        self._page = None

    async def start(self) -> None:
        from playwright.async_api import async_playwright

        self._pw = await async_playwright().start()
        self._ctx = await self._pw.chromium.launch_persistent_context(
            user_data_dir=self._profile,
            channel="msedge",
            headless=True,                     # hidden — the whole point
            args=["--disable-blink-features=AutomationControlled"],
        )
        self._page = self._ctx.pages[0] if self._ctx.pages else await self._ctx.new_page()
        await self._page.goto("https://steamcommunity.com/chat", timeout=60000)
        # The chat is a heavy SPA; the friends list appears a few seconds after
        # navigation. Wait for it (verified: headless renders it in ~2-6s) so the
        # first reads are not empty.
        for _ in range(20):
            try:
                n = await self._page.evaluate(
                    "() => document.querySelectorAll('.friend').length")
                if n:
                    return
            except Exception:
                pass
            await self._page.wait_for_timeout(1000)

    async def is_signed_in(self) -> bool:
        try:
            txt = await self._page.evaluate(
                "() => document.body ? document.body.innerText : ''")
            # The auth screen is short and mentions signing in; a real chat page
            # has the friends list and is long.
            convos = await self.list_conversations()
            return bool(convos) or len(txt) > 6000
        except Exception:
            return False

    async def own_name(self) -> str:
        try:
            return await self._page.evaluate(READ_OWN_NAME_JS) or ""
        except Exception:
            return ""

    async def list_conversations(self) -> list[str]:
        try:
            return await self._page.evaluate(READ_CONVERSATIONS_JS) or []
        except Exception:
            return []

    async def open_conversation(self, name: str) -> bool:
        """Click the friends-list row whose name matches, opening that chat."""
        try:
            row = self._page.locator(".friend", has_text=name).first
            await row.click(timeout=5000)
            await self._page.wait_for_timeout(600)
            return True
        except Exception as exc:
            logger.warning("open_conversation(%r) failed: %s", name, exc)
            return False

    async def read_messages(self) -> list[tuple[str, str]]:
        """Flat (speaker, text) list of the visible conversation, in order."""
        try:
            blocks = await self._page.evaluate(READ_MESSAGES_JS) or []
        except Exception:
            return []
        out: list[tuple[str, str]] = []
        for b in blocks:
            speaker = b.get("speaker", "")
            for t in b.get("texts", []):
                out.append((speaker, t))
        return out

    async def send(self, text: str) -> bool:
        try:
            sel = "textarea:not([class*=authcode]), [contenteditable='true']:not([class*=authcode])"
            box = self._page.locator(sel).first
            await box.click(timeout=5000)
            await box.type(text, delay=8)
            await self._page.keyboard.press("Enter")
            return True
        except Exception as exc:
            logger.warning("send failed: %s", exc)
            return False

    async def close(self) -> None:
        try:
            if self._ctx:
                await self._ctx.close()
            if self._pw:
                await self._pw.stop()
        except Exception:
            pass
