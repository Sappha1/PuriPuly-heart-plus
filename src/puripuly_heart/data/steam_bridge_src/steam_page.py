# -*- coding: utf-8 -*-
"""Async wrapper around a hidden Edge running Steam's web chat. The bridge's Steam half.

Reads/sends by driving Steam's in-memory web-chat model (window.g_FriendsUIApp),
which works headless — Steam only *renders* messages in a visible window, but the
WebSocket + JS data model run regardless of paint. See the store map below.

The store is MobX: Observable Map/Set are NOT `instanceof Map/Set`, so we iterate
with `.forEach(...)` (never spread / Object.keys, which expose MobX internals).

    g_FriendsUIApp.m_FriendStore
        .m_setFriendAccountIDs           ObservableSet of ALL friend account ids
        .GetFriend(acct).m_persona        m_strPlayerName / m_ePersonaState /
                                          m_unGamePlayedAppID / m_strGameExtraInfo
        .GetFriend(acct).avatar_url_medium
        .m_FavoritesStore.BIsFavorited(acct)
        .m_FriendGroupStore.m_mapGroups   custom categories (m_strName, m_rgAccountIDMembers)
    g_FriendsUIApp.m_ChatStore.m_FriendChatStore
        .m_rgFriendChats[]  one per 1:1 chat (m_unAccountIDFriend, OnActivate(),
                            LoadMoreHistory(), m_rgChatMessages[], SendChatMessage())
"""
from __future__ import annotations

import contextlib
import logging

logger = logging.getLogger("steam_page")

# Ready once the FRIENDS list is received (chats load later / may be empty).
STORE_READY_JS = r"""
() => {
  const a = window.g_FriendsUIApp;
  if (!a || !a.m_FriendStore) return false;
  return a.m_FriendStore.m_bReceivedFriendsList === true;
}
"""

OWN_ACCOUNT_JS = r"""
() => { try { return window.g_FriendsUIApp.m_FriendStore.m_self.m_unAccountID || 0; }
        catch (e) { return 0; } }
"""

OWN_INFO_JS = r"""
async () => {
  const fs = window.g_FriendsUIApp.m_FriendStore;
  const self = fs.m_self;
  const url = o => (o && (o.avatar_url_medium
      || (o.m_persona && o.m_persona.avatar_url_medium))) || "";
  try {
    let av = url(self);
    if (!av && fs.GetFriend) av = url(fs.GetFriend(self.m_unAccountID));
    const name = (self.m_persona && self.m_persona.m_strPlayerName)
        || self.m_strPlayerNameNormalized || "";
    let invites = 0;
    try { const s = fs.m_setIncomingInviteAccountIDs; if (s && s.forEach) s.forEach(() => invites++); } catch (e) {}
    let invisible = false;
    try { invisible = fs.BIsInvisibleMode ? !!fs.BIsInvisibleMode() : false; } catch (e) {}
    // Own in-game/presence: the WEB session's own m_eUserPersonaState reflects
    // THIS session, not the desktop client — the friends-store persona for our
    // own account carries the real presence + game the desktop client reports.
    let appid = 0, game = "", state = 0;
    try {
      const pf = ((fs.GetFriend && fs.GetFriend(self.m_unAccountID)) || {}).m_persona;
      const p = pf || self.m_persona;
      if (p) {
        state = p.m_ePersonaState || 0;
        appid = p.m_unGamePlayedAppID || 0;
        game = p.m_strGameExtraInfo || "";
        if (appid && !game) {
          try { const ov = window.g_FriendsUIApp.m_AppInfoStore.GetAppInfo(appid);
                if (ov) game = ov.m_strName || ""; } catch (e) {}
        }
      }
    } catch (e) {}
    if (!state) state = fs.m_eUserPersonaState || 0;
    if (!invisible && state === 7) invisible = true;
    // INVIS-RECON: candidate sources for the DESKTOP client's invisible mode
    // (the r428 lesson says the in-page state is session-scoped — record every
    // candidate so diag shows which one flips when the user goes invisible).
    let st_self = 0, st_fp = 0, binvis = false, pub = "err";
    try { st_self = (self.m_persona && self.m_persona.m_ePersonaState) || 0; } catch (e) {}
    try { st_fp = (((fs.GetFriend && fs.GetFriend(self.m_unAccountID)) || {}).m_persona || {}).m_ePersonaState || 0; } catch (e) {}
    try { binvis = fs.BIsInvisibleMode ? !!fs.BIsInvisibleMode() : false; } catch (e) {}
    try {
      const sid64 = (76561197960265728n + BigInt(self.m_unAccountID || 0)).toString();
      const r2 = await fetch(
          "https://steamcommunity.com/profiles/" + sid64 + "/?xml=1&cb=" + Date.now(),
          {credentials: "omit", cache: "no-store"});
      if (r2.ok) {
        const t2 = await r2.text();
        pub = ((t2.match(/<onlineState>([^<]+)<\/onlineState>/) || [])[1]) || "err";
      }
    } catch (e) {}
    // We ARE this account's logged-in session, so when the PUBLIC profile
    // reads "offline" the account is invisible (a signed-in public profile
    // otherwise reads online/in-game). Caveat: a fully-private profile also
    // reads offline; recon 03:22 confirmed this user's profile is public.
    if (pub === "offline") invisible = true;
    // OWN-RECON ground truth: every in-page model only knows THIS web session
    // (state=1, no game, even while the desktop client is in VRChat). The
    // miniprofile endpoint is server-side truth for the DESKTOP client.
    try {
      const r = await fetch(
          "https://steamcommunity.com/miniprofile/" + (self.m_unAccountID || 0)
              + "/json?cb=a" + Date.now(),
          {credentials: "include", cache: "no-store"});
      if (r.ok) {
        const mini = await r.json();
        const ig = mini && mini.in_game;
        if (ig && (ig.name || ig.is_non_steam)) {
          game = ig.name || game || "a game";
          if (!appid) appid = 1;        // truthy -> ingame
          if (!state) state = 1;
        } else {
          appid = 0; game = "";
        }
      }
    } catch (e) {}
    return {acct: self.m_unAccountID || 0, name, avatar: av || "",
            state, invites, invisible,
            ingame: !!appid, game,
            st_self, st_fp, binvis, pub};
  } catch (e) { return {acct: 0, name: "", avatar: "", state: 0}; }
}
"""

# The FULL friends list with status. state: 0 offline,1 online,2 busy,3 away,
# 4 snooze,5 trade,6 play. flags bits: 0x200 mobile, 0x800 VR.
FRIENDS_JS = r"""
async () => {
  const a = window.g_FriendsUIApp, fs = a.m_FriendStore, ai = a.m_AppInfoStore;
  const forEach = (o, f) => { try { o && o.forEach(f); } catch (e) {} };
  const nameOf = f => {
    const p = f && f.m_persona;
    return (f && f.m_strNickname) || (p && p.m_strPlayerName)
        || (f && f.m_strPlayerNameNormalized) || "";
  };
  const avatarOf = f => {
    try { return f.avatar_url_medium || (f.m_persona && f.m_persona.avatar_url_medium) || ""; }
    catch (e) { return ""; }
  };
  const ids = []; forEach(fs.m_setFriendAccountIDs, x => ids.push(x));
  // resolve game names: ensure app info for every in-game appid first
  const appids = new Set();
  for (const acct of ids) {
    const p = fs.GetFriend(acct) && fs.GetFriend(acct).m_persona;
    if (p && p.m_unGamePlayedAppID) appids.add(p.m_unGamePlayedAppID);
  }
  try { if (ai && ai.EnsureAppInfoForAppIDs && appids.size) await ai.EnsureAppInfoForAppIDs([...appids]); } catch (e) {}
  const appName = appid => {
    if (!appid) return "";
    try { const ov = ai.GetAppInfo(appid); if (ov) return ov.m_strName || ov.name || ""; } catch (e) {}
    return "";
  };
  const appIcon = appid => {
    if (!appid) return "";
    try {
      const ov = ai.GetAppInfo(appid);
      if (ov && ov.m_strIconURL)
        return "https://cdn.cloudflare.steamstatic.com/steamcommunity/public/images/apps/"
             + appid + "/" + ov.m_strIconURL + ".jpg";
    } catch (e) {}
    return "";
  };
  const groupOf = {};
  forEach(fs.m_FriendGroupStore.m_mapGroups, g => {
    if (!g) return;
    let nm; try { nm = g.m_strName; } catch (e) {}
    let mem; try { mem = g.m_rgAccountIDMembers; } catch (e) {}
    forEach(mem, acct => { (groupOf[acct] = groupOf[acct] || []).push(nm || "Group"); });
  });
  // last-message time + unread count per friend
  const lastChat = {}, unread = {};
  try {
    for (const c of (a.m_ChatStore.m_FriendChatStore.m_rgFriendChats || [])) {
      lastChat[c.m_unAccountIDFriend] = c.m_rtLastMessageReceived || 0;
      unread[c.m_unAccountIDFriend] = c.m_cUnreadChatMessages || 0;
    }
  } catch (e) {}
  const out = [];
  for (const acct of ids) {
    const f = fs.GetFriend(acct); if (!f) continue;
    const p = f.m_persona;
    const state = p ? (p.m_ePersonaState || 0) : 0;
    const appid = p ? (p.m_unGamePlayedAppID || 0) : 0;
    const ingame = !!appid;
    let game = "";
    if (ingame) game = appName(appid) || (p.m_strGameExtraInfo || "") || "In-Game";
    let fav = false; try { fav = !!fs.m_FavoritesStore.BIsFavorited(acct); } catch (e) {}
    let nick = "";
    try { nick = f.m_strNickname || (p && p.m_strNickname) || ""; } catch (e) {}
    let extra = "";
    try {
      // the friend object exposes current_game_rich_presence — the SAME
      // localized, composed line the real Steam UI renders (resolves the
      // steam_display token with map/score params). Raw map "status" is a
      // different, unlocalized string — only a fallback.
      if (ingame) {
        try { extra = String(f.current_game_rich_presence || ""); }
        catch (e) {}
        if (!extra || extra[0] === "#") {
          const m = p && p.m_mapRichPresence;
          let st = "";
          try { st = (m && m.get && m.get("status")) || ""; } catch (e) {}
          if (st && st[0] !== "#" && st !== game) extra = st; else extra = "";
        }
        if (extra === game) extra = "";
      }
    } catch (e) {}
    // the composed line often omits the live score — it sits in a separate
    // map value ("[ 13 : 5 ]" shaped); carry the full form separately so
    // the UI can show it in a tooltip without breaking the row fit
    let extraFull = extra;
    try {
      if (ingame && extra && extra.indexOf("[") === -1) {
        const m2 = p && p.m_mapRichPresence;
        let sc = "";
        if (m2 && m2.forEach)
          m2.forEach((v) => {
            try {
              if (!sc && typeof v === "string") {
                const mm = v.match(/\[\s*\d+\s*:\s*\d+\s*\]/);
                if (mm) sc = mm[0];
              }
            } catch (e) {}
          });
        if (sc) extraFull = extra + " " + sc;
      }
    } catch (e) {}
    let lastSeen = 0;
    try {
      // field names vary across FriendsUI builds — scan for any plausible
      // RTime32 property instead of guessing one name
      const scan = (o) => {
        let best = 0;
        for (const k in o) {
          try {
            if (/last.*(seen|online)/i.test(k) && typeof o[k] === "number"
                && o[k] > 1000000000 && o[k] < 4102444800 && o[k] > best)
              best = o[k];
          } catch (e) {}
        }
        return best;
      };
      lastSeen = (p ? scan(p) : 0) || scan(f) || 0;
    } catch (e) {}
    out.push({ acct, name: nameOf(f), avatar: avatarOf(f), state, ingame, game,
               appid, icon: ingame ? appIcon(appid) : "",
               flags: p ? (p.m_unPersonaStateFlags || 0) : 0,
               nick, real: (p && p.m_strPlayerName) || "",
               fav, groups: groupOf[acct] || [], last_chat: lastChat[acct] || 0,
               unread: unread[acct] || 0, last_seen: lastSeen,
               extra, extra_full: extraFull });
  }
  return out;
}
"""


class SteamPage:
    def __init__(self, profile_dir: str, proxy: str = ""):
        self._profile = profile_dir
        proxy = (proxy or "").strip()
        if proxy and "://" not in proxy:
            proxy = "http://" + proxy
        self._proxy = proxy
        self._net_blocked = False   # steamcommunity.com unreachable (blocked network)
        self._loaded_at = 0         # goto success time: the local history array
                                    # stops receiving FRIEND messages from here on
                                    # (Steam withholds push while the real client
                                    # is primary) — do_open fetches the gap
        self._pw = None
        self._ctx = None
        self._page = None
        self._own = 0

    async def start(self, mode: str = "hidden") -> None:
        from playwright.async_api import async_playwright

        self._mode = mode
        args = [
            "--disable-blink-features=AutomationControlled",
            "--disable-background-timer-throttling",
            "--disable-renderer-backgrounding",
            "--disable-backgrounding-occluded-windows",
            # steamcommunity.com holds notification permission in this
            # profile — without this the hidden browser surfaces Steam's
            # "friend is playing..." web pushes as real Windows toasts
            "--disable-notifications",
        ]
        hidden = (mode == "hidden")
        if hidden:
            # New headless (Chrome/Edge) behaves like a real browser — old headless
            # throttles background timers and the CM WebSocket, so LIVE incoming
            # messages never arrive. --headless=new keeps the socket fully alive.
            args = args + ["--headless=new"]
        self._pw = await async_playwright().start()
        # Edge is preinstalled on Windows 10/11; fall back to Chrome for the
        # rare machine without it (the login MUST happen inside THIS persistent
        # profile — an external/default browser's session is unreachable).
        self._ctx = await self._launch_ctx_with_fallback(
            user_data_dir=self._profile,
            channel="msedge",
            headless=False,   # hidden mode is windowless via --headless=new; login = visible
            proxy=({"server": self._proxy} if self._proxy else None),
            args=args,
        )
        # CRITICAL for live messages: --headless=new reports the page as HIDDEN
        # (document.visibilityState==='hidden'), so Steam treats the tab as a
        # background tab and throttles + eventually DROPS the CM WebSocket that
        # delivers live chat — incoming messages stop arriving and sends silently
        # fail. Spoof the page as permanently visible+focused BEFORE Steam's code
        # runs (init script runs on every document) so the socket stays hot.
        with contextlib.suppress(Exception):
            await self._ctx.add_init_script(
                r"""
                try {
                  const vis = (o, prop, val) => {
                    try { Object.defineProperty(o, prop, { configurable: true, get: () => val }); }
                    catch (e) {}
                  };
                  vis(document, 'visibilityState', 'visible');
                  vis(document, 'hidden', false);
                  vis(document, 'webkitVisibilityState', 'visible');
                  vis(document, 'webkitHidden', false);
                  document.hasFocus = () => true;
                  // swallow the app's own 'hidden' visibilitychange if one ever fires
                  document.addEventListener('visibilitychange', (e) => {
                    if (document.visibilityState !== 'visible') e.stopImmediatePropagation();
                  }, true);
                } catch (e) {}
                """)
        self._page = self._ctx.pages[0] if self._ctx.pages else await self._ctx.new_page()
        self._net_blocked = False
        try:
            await self._page.goto("https://steamcommunity.com/chat", timeout=60000)
        except Exception:
            # DNS-poisoned / connection reset / timeout: steamcommunity.com is
            # blocked on this network (e.g. mainland China without a covering
            # proxy). Flag it so the app says "unreachable", not "signed out".
            self._net_blocked = True
            return
        import time as _t
        self._loaded_at = int(_t.time())
        for _ in range(40 if mode == "hidden" else 6):
            try:
                if await self._page.evaluate(STORE_READY_JS):
                    self._own = await self._page.evaluate(OWN_ACCOUNT_JS) or 0
                    return
            except Exception:
                pass
            await self._page.wait_for_timeout(1000)
        with contextlib.suppress(Exception):
            u = self._page.url or ""
            if u.startswith("chrome-error") or u == "about:blank":
                self._net_blocked = True   # error page committed, not a login page


    async def _launch_ctx_with_fallback(self, *args, **kwargs):
        try:
            return await self._pw.chromium.launch_persistent_context(*args, **kwargs)
        except Exception:
            if kwargs.get("channel") == "msedge":
                kwargs["channel"] = "chrome"
                return await self._pw.chromium.launch_persistent_context(*args, **kwargs)
            raise

    async def restart(self, mode: str) -> None:
        await self.close()
        self._pw = self._ctx = self._page = None
        await self.start(mode=mode)

    def net_blocked(self) -> bool:
        return self._net_blocked

    def loaded_at(self) -> int:
        return self._loaded_at

    async def wait_until_signed_in(self, timeout_s: int = 300) -> bool:
        import time as _t
        end = _t.monotonic() + timeout_s
        while _t.monotonic() < end:
            try:
                if await self._page.evaluate(STORE_READY_JS):
                    self._own = await self._page.evaluate(OWN_ACCOUNT_JS) or 0
                    return True
                await self._page.wait_for_timeout(1500)
            except Exception:
                return False
        return False

    async def is_signed_in(self) -> bool:
        try:
            return bool(await self._page.evaluate(STORE_READY_JS))
        except Exception:
            return False

    async def own_account_id(self) -> int:
        if not self._own:
            try:
                self._own = await self._page.evaluate(OWN_ACCOUNT_JS) or 0
            except Exception:
                self._own = 0
        return self._own

    async def own_info(self) -> dict:
        try:
            return await self._page.evaluate(OWN_INFO_JS) or {}
        except Exception:
            return {}

    async def list_friends(self) -> list[dict]:
        """The FULL friends list with per-friend status."""
        try:
            return await self._page.evaluate(FRIENDS_JS) or []
        except Exception:
            return []

    async def dump_persona_keys(self, acct: int) -> str:
        """Diagnostic: every scalar property (name+value) on one friend's
        objects — used once to identify the real last-seen field name."""
        try:
            return await self._page.evaluate(
                r"""(acct) => {
                  const f = window.g_FriendsUIApp.m_FriendStore.GetFriend(acct);
                  const p = f && f.m_persona;
                  const rep = (o) => {
                    const r = {};
                    for (const k in o) {
                      try {
                        const v = o[k];
                        if (typeof v === "number" || typeof v === "string"
                            || typeof v === "boolean") r[k] = v;
                        else if (v && typeof v === "object")
                          r[k] = "<" + (v.constructor ? v.constructor.name
                                        : "obj") + ">";
                      } catch (e) {}
                    }
                    return r;
                  };
                  let rp = {};
                  try {
                    const m = p && p.m_mapRichPresence;
                    const ent = {};
                    if (m && m.forEach)
                      m.forEach((v, k) => { ent[String(k)] = String(v); });
                    rp.map = ent;
                    rp.protoP = Object.getOwnPropertyNames(
                      Object.getPrototypeOf(p || {}));
                    rp.protoF = Object.getOwnPropertyNames(
                      Object.getPrototypeOf(f || {}));
                  } catch (e) { rp.err = String(e); }
                  return JSON.stringify({f: rep(f || {}), p: rep(p || {}),
                                         rp});
                }""", acct)
        except Exception as exc:
            return f"dump failed: {exc}"

    async def preload_recent(self, n: int = 20) -> None:
        """Warm history for the n most-recent chats so opening them is instant."""
        try:
            await self._page.evaluate(
                r"""async (n) => {
                  const a = window.g_FriendsUIApp;
                  const rg = a.m_ChatStore.m_FriendChatStore.m_rgFriendChats || [];
                  const sorted = [...rg].sort(
                    (x, y) => (y.m_rtLastMessageReceived || 0) - (x.m_rtLastMessageReceived || 0)
                  ).slice(0, n);
                  for (const c of sorted) {
                    try {
                      if ((!c.m_rgChatMessages || !c.m_rgChatMessages.length) && c.LoadMoreHistory)
                        await c.LoadMoreHistory();
                    } catch (e) {}
                  }
                  return true;
                }""",
                n,
            )
        except Exception:
            pass

    async def open_conversation(self, acct: int) -> bool:
        """Activate the friend chat (GetFriendChat get-or-creates it for friends
        you have never messaged) and pull its history from the CM."""
        try:
            ok = await self._page.evaluate(
                r"""async (acct) => {
                  const a = window.g_FriendsUIApp;
                  const cs = a.m_ChatStore, fcs = cs.m_FriendChatStore;
                  let c = (fcs.m_rgFriendChats || []).find(x => x.m_unAccountIDFriend === acct);
                  if (!c) { try { c = cs.GetFriendChat(acct); } catch (e) {} }
                  if (!c) { try { c = fcs.GetFriendChat(acct); } catch (e) {} }
                  if (!c) return false;
                  try { c.OnActivate && c.OnActivate(); } catch (e) {}
                  // CRITICAL: Steam streams live message BODIES only to a chat that
                  // has a registered VIEW (this is what opening a chat in the real UI
                  // does). Without it the session is told a message arrived
                  // (m_rtLastMessageReceived updates) but never appends it. Register
                  // one view + pull the session from the server so new messages flow.
                  try { if (c.AddChatView) c.AddChatView(); } catch (e) {}
                  try { if (c.InitMessageSessionFromServer) await c.InitMessageSessionFromServer(); } catch (e) {}
                  // History arrives from the CM a beat after LoadMoreHistory —
                  // wait for it to populate so the chat isn't blank.
                  try {
                    for (let i = 0; i < 10 && (!c.m_rgChatMessages || c.m_rgChatMessages.length === 0); i++) {
                      if (c.LoadMoreHistory) await c.LoadMoreHistory();
                      await new Promise(r => setTimeout(r, 300));
                    }
                  } catch (e) {}
                  return true;
                }""",
                acct,
            )
            return bool(ok)
        except Exception as exc:
            logger.warning("open_conversation(%r) failed: %s", acct, exc)
            return False

    async def read_messages(self, acct: int) -> list[dict]:
        try:
            return await self._page.evaluate(
                r"""(acct) => {
                  const a = window.g_FriendsUIApp;
                  const cs = a.m_ChatStore, fcs = cs.m_FriendChatStore;
                  let c = (fcs.m_rgFriendChats || []).find(x => x.m_unAccountIDFriend === acct);
                  if (!c) { try { c = cs.GetFriendChat(acct); } catch (e) {} }
                  if (!c || !Array.isArray(c.m_rgChatMessages)) return [];
                  const out = [];
                  for (const m of c.m_rgChatMessages) {
                    if (m.eDeleteState) continue;
                    if (m.m_bNoUserContent) continue;
                    const t = (m.strMessageInternal || "").trim();
                    if (!t) continue;
                    out.push({from: m.unAccountID || 0, text: t,
                              ordinal: m.unOrdinal || 0, ts: m.rtTimestamp || 0});
                  }
                  return out;
                }""",
                acct,
            ) or []
        except Exception:
            return []

    async def fetch_messages(self, acct: int, since_ts: int) -> list[dict]:
        """LIVE inbound via SERVER FETCH (not the local array / push, which Steam
        withholds from this background session while the user's real client is
        primary). GetMessagesFromTimeRange(start,end) returns {messages,...} pulled
        fresh from the server — the reliable way to see new messages. Same message
        shape as read_messages."""
        return await self._page.evaluate(
                r"""async (args) => {
                  const [acct, since] = args;
                  const a = window.g_FriendsUIApp;
                  const cs = a.m_ChatStore, fcs = cs.m_FriendChatStore;
                  let c = (fcs.m_rgFriendChats || []).find(x => x.m_unAccountIDFriend === acct);
                  if (!c) { try { c = cs.GetFriendChat(acct); } catch (e) {} }
                  if (!c) throw new Error("nochat");
                  if (!c.GetMessagesFromTimeRange) throw new Error("norange");
                  const now = Math.floor(Date.now() / 1000) + 5;
                  const start = since > 0 ? since : now - 905;
                  const r = await c.GetMessagesFromTimeRange(start, now);
                  const msgs = (r && r.messages) ? r.messages : (Array.isArray(r) ? r : []);
                  const out = [];
                  for (const m of msgs) {
                    if (m.eDeleteState) continue;
                    if (m.m_bNoUserContent) continue;
                    const t = (m.strMessageInternal || "").trim();
                    if (!t) continue;
                    out.push({from: m.unAccountID || 0, text: t,
                              ordinal: m.unOrdinal || 0, ts: m.rtTimestamp || 0});
                  }
                  return out;
                }""",
                [acct, int(since_ts)],
            ) or []

    async def fetch_messages_range(self, acct: int, start: int,
                                   end: int) -> list[dict]:
        """Server fetch of an arbitrary [start, end] window — used to page
        BACK through history older than what the app already shows. Same
        shape as fetch_messages."""
        return await self._page.evaluate(
                r"""async (args) => {
                  const [acct, start, end] = args;
                  const a = window.g_FriendsUIApp;
                  const cs = a.m_ChatStore, fcs = cs.m_FriendChatStore;
                  let c = (fcs.m_rgFriendChats || []).find(x => x.m_unAccountIDFriend === acct);
                  if (!c) { try { c = cs.GetFriendChat(acct); } catch (e) {} }
                  if (!c) throw new Error("nochat");
                  if (!c.GetMessagesFromTimeRange) throw new Error("norange");
                  const r = await c.GetMessagesFromTimeRange(start, end);
                  const msgs = (r && r.messages) ? r.messages : (Array.isArray(r) ? r : []);
                  const out = [];
                  for (const m of msgs) {
                    if (m.eDeleteState) continue;
                    if (m.m_bNoUserContent) continue;
                    const t = (m.strMessageInternal || "").trim();
                    if (!t) continue;
                    out.push({from: m.unAccountID || 0, text: t,
                              ordinal: m.unOrdinal || 0, ts: m.rtTimestamp || 0});
                  }
                  return out;
                }""",
                [acct, int(start), int(end)],
            ) or []

    async def chat_activity(self) -> dict:
        """acct -> m_rtLastMessageReceived for every 1:1 chat. One cheap read;
        drives the all-chats inbound sweep (messages must surface even for
        chats that are not open in the app)."""
        try:
            rows = await self._page.evaluate(
                r"""() => {
                  const a = window.g_FriendsUIApp;
                  const out = [];
                  (a.m_ChatStore.m_FriendChatStore.m_rgFriendChats || []).forEach(c => {
                    out.push([c.m_unAccountIDFriend || 0, c.m_rtLastMessageReceived || 0]);
                  });
                  return out;
                }""")
            return {int(k): int(v) for k, v in (rows or []) if k}
        except Exception:
            return {}

    async def is_typing(self, acct: int) -> bool:
        try:
            return bool(await self._page.evaluate(
                r"""(acct) => {
                  const a = window.g_FriendsUIApp;
                  const c = (a.m_ChatStore.m_FriendChatStore.m_rgFriendChats || [])
                      .find(x => x.m_unAccountIDFriend === acct);
                  return c ? !!c.m_bFriendIsTyping : false;
                }""", acct))
        except Exception:
            return False

    async def send(self, acct: int, text: str) -> bool:
        try:
            return bool(await self._page.evaluate(
                r"""async (args) => {
                  const [acct, text] = args;
                  const a = window.g_FriendsUIApp;
                  const cs = a.m_ChatStore, fcs = cs.m_FriendChatStore;
                  let c = (fcs.m_rgFriendChats || []).find(x => x.m_unAccountIDFriend === acct);
                  if (!c) { try { c = cs.GetFriendChat(acct); } catch (e) {} }
                  if (!c || !c.SendChatMessage) return false;
                  try { await c.SendChatMessage(text); return true; } catch (e) { return false; }
                }""",
                [acct, text],
            ))
        except Exception as exc:
            logger.warning("send failed: %s", exc)
            return False

    async def list_emoticons(self) -> list[str]:
        """The user's owned emoticon names (rendered as economy images by the app)."""
        try:
            return await self._page.evaluate(
                r"""async () => {
                  const es = window.g_FriendsUIApp.m_ChatStore.m_EmoticonStore;
                  try { if (es.RequestEmoticonList) await es.RequestEmoticonList(); } catch (e) {}
                  const ai = window.g_FriendsUIApp.m_AppInfoStore;
                  const appName = id => {
                    try { const ov = ai.GetAppInfo(id);
                          return (ov && (ov.m_strName || ov.name)) || ""; }
                    catch (e) { return ""; }
                  };
                  // m_rgEmoticons is the full owned list (501 observed); the
                  // search API caps at ~25. Items carry name + appid.
                  let arr = [];
                  for (let i = 0; i < 10; i++) {
                    arr = es.m_rgEmoticons || [];
                    if (arr.length > 25) break;
                    await new Promise(r => setTimeout(r, 400));
                  }
                  const seen = new Set(), out = [];
                  for (const e of arr) {
                    const n = e && (e.name || e.strName);
                    if (!n || seen.has(n)) continue;
                    seen.add(n);
                    out.push({ name: n, app: appName(e.appid || 0) });
                  }
                  return out;
                }""") or []
        except Exception:
            return []

    async def list_stickers(self) -> list[str]:
        """Owned sticker names (rendered from the economy CDN; sent as
        [sticker type="name"] BBCode — the same shape incoming stickers use)."""
        try:
            return await self._page.evaluate(
                r"""() => {
                  // PICKER-RECON: stickers live ON the EmoticonStore as
                  // m_rgStickers — items carry name + appid.
                  try {
                    const es = window.g_FriendsUIApp.m_ChatStore.m_EmoticonStore;
                    const ai = window.g_FriendsUIApp.m_AppInfoStore;
                    const appName = id => {
                      try { const ov = ai.GetAppInfo(id);
                            return (ov && (ov.m_strName || ov.name)) || ""; }
                      catch (e) { return ""; }
                    };
                    const arr = es.m_rgStickers || [];
                    const seen = new Set(), out = [];
                    for (const x of arr) {
                      const n = x && (x.name || x.strName);
                      if (!n || seen.has(n)) continue;
                      seen.add(n);
                      out.push({ name: n, app: appName(x.appid || 0) });
                    }
                    return out;
                  } catch (e) { return []; }
                }""") or []
        except Exception:
            return []

    async def send_sticker_or_effect(self, acct: int, name: str,
                                     kind: str) -> dict:
        """Send a sticker or room effect the way the real client does: probe
        the chat object and stores for a dedicated send method; fall back to
        the wire text. Returns {ok, how} for diag."""
        try:
            return await self._page.evaluate(
                r"""async (args) => {
                  const [acct, name, kind] = args;
                  const a = window.g_FriendsUIApp, cs = a.m_ChatStore;
                  let c = (cs.m_FriendChatStore.m_rgFriendChats || [])
                            .find(x => x.m_unAccountIDFriend === acct);
                  if (!c) { try { c = cs.GetFriendChat(acct); } catch (e) {} }
                  if (!c) return { ok: false, how: 'no chat' };
                  const es = cs.m_EmoticonStore;
                  const protoNames = o => {
                    const out = new Set();
                    let p = o;
                    while (p && p !== Object.prototype) {
                      for (const k of Object.getOwnPropertyNames(p)) out.add(k);
                      p = Object.getPrototypeOf(p);
                    }
                    return [...out];
                  };
                  // The client sends SLASH COMMANDS; the server converts
                  // them into validated sticker/effect BBCode. Sending the
                  // BBCode directly skips validation -> invisible line.
                  try {
                    const text = (kind === 'sticker' ? '/sticker ' : '/roomeffect ') + name;
                    if (c.SendChatMessage) { c.SendChatMessage(text); return { ok: true, how: 'slash' }; }
                  } catch (e) {}
                  return { ok: false, how: 'none' };
                }""", [acct, name, kind]) or {"ok": False, "how": "eval"}
        except Exception as exc:
            return {"ok": False, "how": str(exc)[:120]}

    async def dump_sticker_send_recon(self) -> dict:
        """Find the REAL sticker send path: every method on the chat object /
        chat store whose SOURCE mentions sticker (name-matching found nothing;
        the exact wire text sent as plain chat renders invisible, so the
        client must attach sticker data via a dedicated call)."""
        try:
            return await self._page.evaluate(
                r"""() => {
                  const out = {};
                  const a = window.g_FriendsUIApp, cs = a.m_ChatStore;
                  const c = (cs.m_FriendChatStore.m_rgFriendChats || [])[0];
                  const grab = (host, label) => {
                    if (!host) return;
                    let p = host, depth = 0;
                    while (p && p !== Object.prototype && depth < 6) {
                      for (const k of Object.getOwnPropertyNames(p)) {
                        try {
                          const v = host[k];
                          if (typeof v !== 'function') continue;
                          const src = String(v);
                          if (/sticker/i.test(src) && src.length < 4000)
                            out[label + '.' + k] = src.slice(0, 700);
                        } catch (e) {}
                      }
                      p = Object.getPrototypeOf(p); depth++;
                    }
                  };
                  grab(c, 'chat');
                  grab(cs, 'chatstore');
                  grab(cs.m_FriendChatStore, 'fcs');
                  return out;
                }""") or {}
        except Exception as exc:
            return {"err": str(exc)}

    async def dump_stickfx_methods(self) -> dict:
        """Recon: source of every sticker/effect-ish method on the chat object
        and EmoticonStore, so the send path can be read instead of guessed."""
        try:
            return await self._page.evaluate(
                r"""() => {
                  const out = {};
                  const a = window.g_FriendsUIApp, cs = a.m_ChatStore;
                  const c = (cs.m_FriendChatStore.m_rgFriendChats || [])[0];
                  const es = cs.m_EmoticonStore;
                  const grab = (host, label) => {
                    if (!host) return;
                    let p = host;
                    while (p && p !== Object.prototype) {
                      for (const k of Object.getOwnPropertyNames(p)) {
                        if (!/sticker|effect/i.test(k)) continue;
                        try {
                          const v = host[k];
                          if (typeof v === 'function')
                            out[label + '.' + k] = String(v).slice(0, 300);
                        } catch (e) {}
                      }
                      p = Object.getPrototypeOf(p);
                    }
                  };
                  grab(c, 'chat'); grab(es, 'store');
                  return out;
                }""") or {}
        except Exception as exc:
            return {"err": str(exc)}

    async def list_effects(self) -> list[str]:
        """Room-effect names from the EmoticonStore's m_rgEffects (4 observed)."""
        try:
            return await self._page.evaluate(
                r"""() => {
                  try {
                    const es = window.g_FriendsUIApp.m_ChatStore.m_EmoticonStore;
                    const arr = es.m_rgEffects || [];
                    return arr.map(x => x && (x.name || x.strName || (x.effect && x.effect.name)))
                              .filter(Boolean);
                  } catch (e) { return []; }
                }""") or []
        except Exception:
            return []

    async def dump_picker_recon(self) -> dict:
        """One-shot: emote/sticker store shapes, for data-driven fixes."""
        try:
            return await self._page.evaluate(
                r"""() => {
                  const out = {};
                  const size = x => {
                    try {
                      if (!x) return null;
                      if (x instanceof Map) return 'Map(' + x.size + ')';
                      if (Array.isArray(x)) return 'Arr(' + x.length + ')';
                      if (typeof x === 'object') return 'obj';
                      return typeof x;
                    } catch (e) { return '?'; }
                  };
                  try {
                    const es = window.g_FriendsUIApp.m_ChatStore.m_EmoticonStore;
                    out.emoticon_store = {};
                    for (const k of Object.keys(es)) out.emoticon_store[k] = size(es[k]);
                  } catch (e) { out.emoticon_store = String(e).slice(0, 80); }
                  try {
                    const es = window.g_FriendsUIApp.m_ChatStore.m_EmoticonStore;
                    const item = o => {
                      const r = {};
                      try { for (const k of Object.keys(o || {}))
                              r[k] = String(o[k]).slice(0, 40); } catch (e) {}
                      return r;
                    };
                    out.first_sticker = item((es.m_rgStickers || [])[0]);
                    out.effects = (es.m_rgEffects || []).map(x => item(x));
                  } catch (e) {}
                  return out;
                }""") or {}
        except Exception as exc:
            return {"err": str(exc)}

    async def set_favorite(self, acct: int, on: bool) -> bool:
        try:
            return bool(await self._page.evaluate(
                r"""async (args) => {
                  const [acct, on] = args;
                  const fav = window.g_FriendsUIApp.m_FriendStore.m_FavoritesStore;
                  try {
                    if (on) fav.AddToFavorites(acct); else fav.RemoveFromFavorites(acct);
                    if (fav.SaveFavorites) await fav.SaveFavorites();
                    return true;
                  } catch (e) { return false; }
                }""", [acct, on]))
        except Exception:
            return False

    async def set_status(self, state: int) -> str:
        try:
            res = await self._page.evaluate(
                r"""async (state) => {
                  const app = window.g_FriendsUIApp;
                  const targets = [app.m_FriendStore, app, app.m_ChatStore];
                  const names = ['SetUserPersonaState', 'ChangeUserPersonaState',
                                 'SetPersonaState', 'SetPersonaOnlineState',
                                 'ChangeStatus', 'SetOnlineStatus'];
                  for (const t of targets) {
                    if (!t) continue;
                    for (const fn of names) {
                      try {
                        if (typeof t[fn] === 'function') {
                          await t[fn](state);
                          return 'ok:' + fn;
                        }
                      } catch (e) {}
                    }
                  }
                  // nothing matched — report every persona/state-ish method so
                  // the diag log shows what the real call is
                  const found = [];
                  for (const t of targets) {
                    if (!t) continue;
                    let o = t;
                    while (o && o !== Object.prototype) {
                      for (const k of Object.getOwnPropertyNames(o)) {
                        try {
                          if (typeof t[k] === 'function' &&
                              /persona|online|status|state/i.test(k) &&
                              !found.includes(k)) found.push(k);
                        } catch (e) {}
                      }
                      o = Object.getPrototypeOf(o);
                    }
                  }
                  return 'none:' + found.slice(0, 40).join(',');
                }""", state)
            return str(res)
        except Exception as exc:
            return f"err:{exc}"

    async def react(self, acct: int, ts: int, ordinal: int, name: str,
                    rtype: int = 1) -> str:
        """Add an emoticon reaction to a message. Probes the reaction API and
        returns 'ok:<method>' or 'none:<recon dump>' for the daemon's diag."""
        try:
            res = await self._page.evaluate(
                r"""async (a) => {
                  const app = window.g_FriendsUIApp, cs = app.m_ChatStore;
                  let c = (cs.m_FriendChatStore.m_rgFriendChats || [])
                      .find(x => x.m_unAccountIDFriend === a.acct);
                  if (!c) { try { c = cs.GetFriendChat(a.acct); } catch (e) {} }
                  if (!c) return 'none:no-chat';
                  const names = ['SendMessageReaction', 'UpdateMessageReaction',
                                 'SetMessageReaction', 'ReactToMessage',
                                 'AddReaction'];
                  const errs = [];
                  for (const fn of names) {
                    if (typeof c[fn] !== 'function') continue;
                    try {
                      await c[fn](a.ts, a.ord, a.rtype, a.name, true);
                      return 'ok:' + fn;
                    } catch (e) { errs.push(fn + ':' + e); }
                  }
                  const found = [];
                  let o = c;
                  while (o && o !== Object.prototype) {
                    for (const k of Object.getOwnPropertyNames(o)) {
                      try {
                        if (typeof c[k] === 'function' && /react/i.test(k)
                            && !found.includes(k)) found.push(k);
                      } catch (e) {}
                    }
                    o = Object.getPrototypeOf(o);
                  }
                  let mf = [];
                  try {
                    const m = (c.m_rgChatMessages || [])[0];
                    if (m) for (const k in m) if (/react/i.test(k)) mf.push(k);
                  } catch (e) {}
                  return 'none:' + found.slice(0, 30).join(',') +
                         '|errs:' + errs.join(';') + '|msg:' + mf.join(',');
                }""", {"acct": acct, "ts": ts, "ord": ordinal, "name": name,
                       "rtype": int(rtype)})
            return str(res)
        except Exception as exc:
            return f"err:{exc}"

    async def reactivate(self, acct: int) -> None:
        """Nudge Steam to keep pushing live messages for the open chat to this
        (headless) page — OnActivate + a focus event."""
        try:
            await self._page.evaluate(
                r"""(acct) => {
                  const a = window.g_FriendsUIApp, cs = a.m_ChatStore;
                  let c = (cs.m_FriendChatStore.m_rgFriendChats || []).find(x => x.m_unAccountIDFriend === acct);
                  if (!c) { try { c = cs.GetFriendChat(acct); } catch (e) {} }
                  try { if (c && c.OnActivate) c.OnActivate(); } catch (e) {}
                  // Re-assert foreground so Steam never parks the CM socket.
                  try { window.dispatchEvent(new Event('focus')); } catch (e) {}
                  try { document.dispatchEvent(new Event('visibilitychange')); } catch (e) {}
                  return true;
                }""", acct)
        except Exception:
            pass

    async def chat_debug(self, acct: int) -> dict:
        """Diagnostic: why aren't live messages appended in this headless session?
        Dumps unread/msg counts, last-recv time, connection state, and every method
        on the chat object so we can find a 'fetch newer messages' call."""
        try:
            return await self._page.evaluate(
                r"""async (acct) => {
                  const a = window.g_FriendsUIApp;
                  const cs = a.m_ChatStore, fcs = cs.m_FriendChatStore;
                  let c = (fcs.m_rgFriendChats||[]).find(x=>x.m_unAccountIDFriend===acct);
                  if (!c) return { none: true };
                  const names = new Set();
                  try { let p = Object.getPrototypeOf(c);
                        while (p && p !== Object.prototype) {
                          Object.getOwnPropertyNames(p).forEach(n => { try { if (typeof c[n] === 'function') names.add(n); } catch(e){} });
                          p = Object.getPrototypeOf(p);
                        } } catch(e){}
                  const num = v => (typeof v === 'number' ? v : null);
                  // any instance property mentioning "view" (find the view counter)
                  let views = {};
                  try { Object.getOwnPropertyNames(c).forEach(k => {
                    if (/view/i.test(k)) { const v = c[k];
                      views[k] = (typeof v === 'number' || typeof v === 'boolean') ? v
                               : (v && typeof v.size === 'number') ? ('set:'+v.size)
                               : (Array.isArray(v)) ? ('arr:'+v.length) : typeof v; }
                  }); } catch(e){}
                  // Does a SERVER FETCH return the messages the local array is missing?
                  let fetch = {};
                  try {
                    const m = c.GetMostRecentChatMsg && c.GetMostRecentChatMsg();
                    fetch.mostRecent = m ? (m.strMessageInternal || m.m_strMessage || '').slice(0, 20) : null;
                  } catch (e) { fetch.mrErr = '' + e; }
                  try {
                    if (c.GetMessagesFromTimeRange) {
                      const now = Math.floor(Date.now() / 1000);
                      let r = null;
                      try { r = await c.GetMessagesFromTimeRange(now - 900, now); } catch (e1) {
                        try { r = await c.GetMessagesFromTimeRange(now - 900, now, 30); } catch (e2) { fetch.rangeErr = '' + e2; }
                      }
                      const msgs = r && r.messages ? r.messages : (Array.isArray(r) ? r : []);
                      fetch.rangeLen = msgs.length;
                      try {
                        if (msgs.length) {
                          const last = msgs[msgs.length - 1];
                          fetch.msgKeys = Object.keys(last).slice(0, 16);
                          fetch.msgSample = JSON.stringify(last).slice(0, 300);
                        }
                      } catch (e) { fetch.msgErr = '' + e; }
                    }
                  } catch (e) { fetch.rangeErr = '' + e; }
                  let conn = {};
                  try {
                    const ci = cs.m_CMInterface || a.m_CMInterface;
                    conn.hasCM = !!ci;
                    for (const m of ['BConnected','BIsConnected','BConnectedToServer','GetConnectionState']) {
                      try { if (ci && typeof ci[m] === 'function') conn[m] = ci[m](); } catch(e){}
                    }
                    conn.appOnline = (typeof a.BConnectedToServer === 'function') ? a.BConnectedToServer() : undefined;
                    conn.eState = num(a.m_eConnectionState);
                  } catch(e){}
                  return {
                    unread: num(c.m_cUnreadChatMessages),
                    msgCount: (c.m_rgChatMessages||[]).length,
                    lastRecv: num(c.m_rtLastMessageReceived),
                    lastRead: num(c.m_rtLastMessageRead),
                    activeTS: num(c.m_rtActive),
                    isActive: c.m_bIsActive,
                    typing: c.m_bFriendIsTyping,
                    methods: [...names].sort(),
                    views,
                    fetch,
                    conn,
                  };
                }""", acct) or {}
        except Exception as exc:
            return {"error": str(exc)}

    async def send_image(self, acct: int, b64: str, fname: str, mime: str,
                         spoiler: bool = False, used: list | None = None) -> dict:
        """Upload an image to Steam UGC and deliver it to the friend, reproducing
        the web client's flow (recon r415): POST chat/beginfileupload/ (sha1, size,
        dims) -> PUT the bytes to the returned host with the returned headers ->
        POST chat/commitfileupload/ (+friend_steamid via
        PopulateCommitFileUploadFormData) — commit delivers the message.

        `used` lists ugcids of PREVIOUS committed uploads: beginfileupload has
        been observed handing back the prior upload's ugcid (every other send),
        which makes the delivered message reference the wrong file — the friend
        then sees a bare filedownload link, or an earlier picture, instead of
        the image. Any begin response whose ugcid is already used is re-asked."""
        try:
            return await self._page.evaluate(
                r"""async (args) => {
                  const [acct, b64, fname, mime, spoiler, used] = args;
                  const a = window.g_FriendsUIApp, cs = a.m_ChatStore;
                  let c = (cs.m_FriendChatStore.m_rgFriendChats||[]).find(x=>x.m_unAccountIDFriend===acct);
                  if (!c) { try { c = cs.GetFriendChat(acct); } catch(e){} }
                  if (!c) return { step:'chat', err:'no chat' };
                  const bytes = Uint8Array.from(atob(b64), ch => ch.charCodeAt(0));
                  const digest = await crypto.subtle.digest('SHA-1', bytes);
                  const sha = [...new Uint8Array(digest)].map(b=>b.toString(16).padStart(2,'0')).join('');
                  let w = 0, h = 0;
                  try { const bmp = await createImageBitmap(new Blob([bytes], {type: mime}));
                        w = bmp.width; h = bmp.height; bmp.close && bmp.close(); }
                  catch (e) { return { step:'decode', err: ''+e }; }
                  const sessionid = (document.cookie.match(/sessionid=([^;]+)/)||[])[1] || '';
                  const mk = () => {
                    const fd = new FormData();
                    fd.append('sessionid', sessionid);
                    fd.append('l', 'english');
                    fd.append('file_size', String(bytes.length));
                    fd.append('file_name', fname);
                    fd.append('file_sha', sha);
                    fd.append('file_image_width', String(w));
                    fd.append('file_image_height', String(h));
                    fd.append('file_type', mime);
                    return fd;
                  };
                  const usedSet = new Set((used || []).map(String));
                  const pickIds = (raw) =>
                      [...raw.matchAll(/"ugcid"\s*:\s*"?(\d+)"?/g)].map(m => m[1]);
                  let r1 = null, j1 = null, raw1 = '', beginTries = 0;
                  // beginfileupload has been seen returning the PREVIOUS
                  // upload's ugcid — commit then binds the message to the
                  // wrong file. Re-ask until the slot is one we never used.
                  while (beginTries < 3) {
                    beginTries++;
                    r1 = await fetch(c.GetBeginFileUploadURL(),
                                     {method:'POST', body: mk(), credentials:'include'});
                    j1 = null; raw1 = '';
                    try { raw1 = await r1.text(); j1 = JSON.parse(raw1); } catch(e){}
                    if (!j1 || j1.success !== 1)
                      return { step:'begin', status: r1.status, resp: JSON.stringify(j1).slice(0,300) };
                    const idsHere = pickIds(raw1);
                    if (idsHere.some(i => !usedSet.has(i))) break;
                    await new Promise(rs => setTimeout(rs, 350));
                  }
                  const res = j1.result || j1;
                  const allIds = pickIds(raw1);
                  let idIdx = allIds.findIndex(i => !usedSet.has(i));
                  if (idIdx < 0) idIdx = 0;
                  const chosenUgc = allIds[idIdx] || String(res.ugcid);
                  // timestamp/hmac must belong to the SAME slot: when the body
                  // carries several entries, take the field at the same index
                  const pickAt = (re) => {
                    const ms = [...raw1.matchAll(re)].map(m => m[1]);
                    return ms[idIdx] !== undefined ? ms[idIdx] : (ms[0] || '');
                  };
                  const putURL = (res.use_https ? 'https://' : 'http://') + res.url_host + res.url_path;
                  const hdrs = {};
                  (res.request_headers||[]).forEach(x => { hdrs[x.name] = x.value; });
                  const r2 = await fetch(putURL, {method:'PUT', headers: hdrs, body: bytes});
                  if (!(r2.status >= 200 && r2.status < 300))
                    return { step:'put', status: r2.status };
                  const fd2 = mk();
                  fd2.append('success', '1');
                  // ugcid is a 64-bit id: JSON.parse mangles it above 2^53
                  // (the commit-400 bug) — digits come from the raw body, and
                  // chosenUgc skips slots we already committed in the past.
                  fd2.append('ugcid', chosenUgc);
                  fd2.append('timestamp',
                      pickAt(/"timestamp"\s*:\s*"?(\d+)"?/g) || String(res.timestamp));
                  // The begin response signs the upload: its hmac MUST be echoed
                  // in the commit or Steam rejects it as a bad request.
                  const hmac = pickAt(/"hmac"\s*:\s*"([^"]+)"/g) || res.hmac || '';
                  if (hmac) fd2.append('hmac', String(hmac));
                  try { c.PopulateCommitFileUploadFormData(fd2, {bSpoiler: !!spoiler}, {}); } catch(e){}
                  // Populate derives friend_steamid from this.accountid_partner,
                  // which our store-plucked chat object may not carry -> a base/
                  // garbage steamid -> commit 400. Set it ourselves from acct.
                  fd2.set('friend_steamid', (76561197960265728n + BigInt(acct)).toString());
                  if (fd2.get('spoiler') === null) fd2.append('spoiler', spoiler ? '1' : '0');
                  const dbg = { partner: String(c.accountid_partner),
                                beginKeys: Object.keys(res).join(','),
                                beginTries: beginTries, ids: allIds.join('|'),
                                raw: raw1.slice(0, 300) };
                  try { for (const [k, v] of fd2.entries())
                          dbg[k] = (typeof v === 'string') ? String(v).slice(0, 60) : '<file>'; } catch (e) {}
                  const r3 = await fetch(c.GetCommitFileUploadURL(),
                                         {method:'POST', body: fd2, credentials:'include'});
                  let j3 = null; try { j3 = await r3.json(); } catch(e){}
                  if (!j3 || j3.success !== 1)
                    return { step:'commit', status: r3.status,
                             resp: JSON.stringify(j3).slice(0,300),
                             sent: dbg };
                  // r508: report the REAL 64-bit id (JSON.parse mangles it above
                  // 2^53 -- two different uploads logged the 'same' ugcid)
                  return { ok: true, ugcid: chosenUgc, beginTries: beginTries,
                           ids: allIds.join('|'),
                           mime: mime, w: w, h: h, bytes: bytes.length };
                }""",
                [acct, b64, fname, mime, bool(spoiler), list(used or [])],
            ) or {"step": "eval", "err": "no result"}
        except Exception as exc:
            return {"step": "py", "err": str(exc)}

    async def dump_own_recon(self) -> dict:
        """Recon: every candidate source for OWN presence (state/game/invisible).
        The friends-store persona reports Online/no-game, so find where the truth
        lives: full persona scalars + the miniprofile endpoint (hover cards)."""
        try:
            return await self._page.evaluate(
                r"""async () => {
                  const a = window.g_FriendsUIApp, fs = a.m_FriendStore;
                  const own = fs.m_self ? fs.m_self.m_unAccountID : 0;
                  const scal = o => { const r = {};
                    try { for (const k of Object.keys(o)) { const v = o[k];
                      if (v === null || ['number','string','boolean'].includes(typeof v)) r[k] = v; } }
                    catch(e){} return r; };
                  const out = { own };
                  try { out.selfPersona = scal(fs.m_self.m_persona || {}); } catch(e){}
                  try { out.friendPersona = scal(((fs.GetFriend && fs.GetFriend(own)) || {}).m_persona || {}); } catch(e){}
                  try { out.eUserState = fs.m_eUserPersonaState; } catch(e){}
                  try { out.invisible = fs.BIsInvisibleMode ? fs.BIsInvisibleMode() : null; } catch(e){}
                  try {
                    const r = await fetch('https://steamcommunity.com/miniprofile/' + own + '/json',
                                          {credentials: 'include'});
                    out.miniStatus = r.status;
                    try { out.mini = await r.json(); } catch(e) { out.miniErr = 'json ' + e; }
                  } catch (e) { out.miniErr = '' + e; }
                  return out;
                }""") or {}
        except Exception as exc:
            return {"error": str(exc)}

    async def dump_upload_methods(self, acct: int) -> dict:
        """Recon for image-send: the source of the chat's file-upload helpers, so
        the Begin→PUT→Commit flow can be reproduced from page JS."""
        try:
            return await self._page.evaluate(
                r"""(acct) => {
                  const a = window.g_FriendsUIApp, cs = a.m_ChatStore;
                  let c = (cs.m_FriendChatStore.m_rgFriendChats||[]).find(x=>x.m_unAccountIDFriend===acct);
                  if (!c) { try { c = cs.GetFriendChat(acct); } catch(e){} }
                  if (!c) return { none: true };
                  const out = {};
                  for (const n of ['GetBeginFileUploadURL','GetCommitFileUploadURL',
                                   'PopulateCommitFileUploadFormData','GetMaxFileSizeMB',
                                   'LogFileUploadMessage']) {
                    try { out[n] = ('' + c[n]).slice(0, 1400); } catch (e) { out[n] = 'ERR ' + e; }
                  }
                  try { out.maxMB = c.GetMaxFileSizeMB ? c.GetMaxFileSizeMB() : null; } catch (e) {}
                  try {
                    out.beginURL = c.GetBeginFileUploadURL ? c.GetBeginFileUploadURL() : null;
                    out.commitURL = c.GetCommitFileUploadURL ? c.GetCommitFileUploadURL() : null;
                  } catch (e) { out.urlErr = '' + e; }
                  return out;
                }""", acct) or {}
        except Exception as exc:
            return {"error": str(exc)}

    async def poke(self) -> None:
        try:
            await self._page.evaluate("() => { try { window.dispatchEvent(new Event('focus')); } catch(e){} }")
        except Exception:
            pass

    async def reload(self) -> None:
        """Plain page reload, login kept — used to revive the friends session
        after persona state 0 (Sign out of Friends & Chat)."""
        with contextlib.suppress(Exception):
            await self._page.reload()

    async def sign_out(self) -> None:
        """Clear the persistent profile's Steam login (cookies + storage) so the
        next start lands on the signed-out community page."""
        with contextlib.suppress(Exception):
            await self._ctx.clear_cookies()
        with contextlib.suppress(Exception):
            await self._page.evaluate(
                "() => { try { localStorage.clear(); sessionStorage.clear(); } "
                "catch (e) {} }")
        with contextlib.suppress(Exception):
            await self._page.reload()

    async def close(self) -> None:
        try:
            if self._ctx:
                await self._ctx.close()
            if self._pw:
                await self._pw.stop()
        except Exception:
            pass
