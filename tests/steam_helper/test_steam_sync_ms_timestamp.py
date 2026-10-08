"""r654: Steam chats out of sync with the real client (blank chat, only own
messages, friend replies never arriving).

Root cause: Steam's InitQueuedMessages() restores a FAILED queued send as a
local echo with eErrorSendingObservable set and rtTimestamp in MILLISECONDS.
The helper read rtTimestamp as seconds, so that one phantom message

* made the live poll's range start a ms value -> GetMessagesFromTimeRange's
  fixed32 rtime32_start_time >= 2^32 -> jspb "Assertion failed" on EVERY poll,
  so the friend's messages were never fetched (logged once, then silent);
* pushed the delivery watermark to ms, so real messages were dropped as
  already delivered;
* made the app's datetime.fromtimestamp / time.localtime raise OSError while
  rendering the history, painting the chat blank (own sends still appeared via
  the separate live path).

Covered here: the page JS (run under Node against a mock reproducing jspb's
asserts), the daemon's normalization layer, and the app view's formatters.
"""
from __future__ import annotations

import ast
import importlib.util
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]
_SRC = _REPO / "src" / "puripuly_heart" / "data" / "steam_bridge_src"
_NOW = 1_791_481_941
_MS = (_NOW - 3600) * 1000          # a failed send's restored time, in ms


# ── daemon ──────────────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def daemon_mod(tmp_path_factory):
    # load a COPY: the daemon writes diag.log next to its own file, and the
    # src data tree is bundled into the release
    d = tmp_path_factory.mktemp("daemon")
    for _f in ("daemon.py", "steam_page.py"):
        shutil.copy2(_SRC / _f, d / _f)
    spec = importlib.util.spec_from_file_location("pph_steam_daemon_ms_ts", d / "daemon.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def test_norm_ts(daemon_mod):
    n = daemon_mod._norm_ts
    assert n(_NOW) == _NOW
    assert n(_MS) == _NOW - 3600              # ms -> s
    assert n(None) == 0 and n("x") == 0 and n(-5) == 0
    assert n(str(_NOW)) == _NOW


class _OpenSteam:
    def __init__(self, local, server):
        self.local, self.server = list(local), list(server)
        self.fetches: list = []

    async def open_conversation(self, acct):
        return True

    async def read_messages(self, acct):
        return list(self.local)

    async def fetch_messages(self, acct, since):
        self.fetches.append(since)
        return list(self.server)


async def test_open_with_a_ms_message_keeps_every_watermark_in_seconds(daemon_mod):
    # defense layer: even if a ms time slips past the page filter, the poll
    # window / watermarks / shaped history must stay valid seconds
    local = [
        {"from": 1, "text": "own ok", "ts": _NOW - 60, "ordinal": 0},
        {"from": 1, "text": "own phantom", "ts": _MS, "ordinal": 0},
    ]
    server = [{"from": 100, "text": "friend reply", "ts": _NOW - 10, "ordinal": 0}]
    d = daemon_mod.Daemon()
    d.steam = _OpenSteam(local, server)
    d.own, d.own_name = 1, "You"
    d.convos = {100: {"acct": 100, "name": "Alex", "avatar": ""}}
    d.emitted: list = []

    async def _emit(obj, only=None):
        d.emitted.append(obj)

    d.emit = _emit
    await d.do_open(100)
    assert d._last_ts < 4_294_967_296                  # poll range start is valid fixed32
    assert d._last_ts - 3 < 4_294_967_296
    assert d._emitted[100] < 4_294_967_296             # watermark not poisoned
    hist = next(e for e in d.emitted if e["ev"] == "history")["messages"]
    assert all(0 < m["ts"] < 4_294_967_296 for m in hist)
    # a real later message (seconds) is NOT below the watermark any more
    assert _NOW + 30 > d._emitted[100]


# ── page JS (the primary fix) under Node ────────────────────────────────────

_NODE = shutil.which("node")


def _evaluate_bodies(path: Path) -> dict:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    out = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef) and node.name in (
                "read_messages", "fetch_messages", "fetch_messages_range"):
            for sub in ast.walk(node):
                if (isinstance(sub, ast.Call) and isinstance(sub.func, ast.Attribute)
                        and sub.func.attr == "evaluate" and sub.args
                        and isinstance(sub.args[0], ast.Constant)):
                    out[node.name] = sub.args[0].value
                    break
    return out


_HARNESS = r"""
const NOW = %(now)d;
Date.now = () => NOW * 1000;
function fixed32(v){ if (v == null) return; if (!(0 <= v && v < 4294967296)) throw new Error("Assertion failed"); if (v != Math.floor(v)) throw new Error("Assertion failed"); }
function uint32(v){ if (v == null) return; if (!(0 <= v && v < 4294967296)) throw new Error("Assertion failed"); }
const calls = [];
const server = [{unAccountID: 100, strMessageInternal: "friend reply", rtTimestamp: NOW - 10, unOrdinal: 0}];
const chat = {
  m_unAccountIDFriend: 100,
  m_rgChatMessages: [
    {unAccountID: 1, strMessageInternal: "own ok", rtTimestamp: NOW - 60, unOrdinal: 0},
    {unAccountID: 1, strMessageInternal: "own phantom", rtTimestamp: %(ms)d, unOrdinal: 0, eErrorSendingObservable: 2},
  ],
  // Steam: (rtime32_start_time fixed32, start_ordinal, time_last, ordinal_last, count)
  async GetMessagesFromTimeRange(e, t, n, s){ calls.push(e); fixed32(e); uint32(t); uint32(n); uint32(s);
    return {messages: server.filter(m => m.rtTimestamp >= e)}; },
};
globalThis.window = {g_FriendsUIApp: {m_ChatStore: {m_FriendChatStore: {m_rgFriendChats: [chat]}, GetFriendChat: () => chat}}};
const READ = %(read)s, FETCH = %(fetch)s, RANGE = %(range)s;
(async () => {
  const r = {read: await READ(100)};
  try { r.poll = await FETCH([100, %(ms)d - 3]); } catch (e) { r.poll = "THREW " + e.message; }
  try { r.range = await RANGE([100, %(ms)d, NOW * 1000]); } catch (e) { r.range = "THREW " + e.message; }
  r.calls = calls;
  console.log(JSON.stringify(r));
})();
"""


@pytest.mark.skipif(_NODE is None, reason="node not installed")
def test_page_js_skips_failed_sends_and_never_sends_an_invalid_fixed32(tmp_path):
    b = _evaluate_bodies(_SRC / "steam_page.py")
    assert set(b) == {"read_messages", "fetch_messages", "fetch_messages_range"}
    js = tmp_path / "h.js"
    js.write_text(_HARNESS % {"now": _NOW, "ms": _MS, "read": b["read_messages"],
                              "fetch": b["fetch_messages"], "range": b["fetch_messages_range"]},
                  encoding="utf-8")
    p = subprocess.run([_NODE, str(js)], capture_output=True, text=True, timeout=60)
    assert p.returncode == 0, p.stderr
    r = json.loads(p.stdout)
    assert [m["text"] for m in r["read"]] == ["own ok"]          # phantom dropped
    assert all(m["ts"] < 4_294_967_296 for m in r["read"])
    assert isinstance(r["poll"], list), r["poll"]                 # no jspb assert
    assert [m["text"] for m in r["poll"]] == ["friend reply"]     # friend reply arrives
    assert isinstance(r["range"], list), r["range"]
    assert all(0 <= c < 4_294_967_296 and c == int(c) for c in r["calls"])


# ── app view ────────────────────────────────────────────────────────────────

def test_view_sec_and_formatters_survive_ms_times():
    from puripuly_heart.ui.views import steam_bridge as sb

    assert sb._sec(_MS) == _NOW - 3600 and sb._sec(_NOW) == _NOW
    assert sb._sec(None) == 0 and sb._sec("junk") == 0
    v = object.__new__(sb.SteamBridgeView)                 # formatters need no state
    assert sb.SteamBridgeView._fmt_ts(v, _MS) == sb.SteamBridgeView._fmt_ts(v, _NOW - 3600)
    assert sb.SteamBridgeView._fmt_ts(v, 0) == ""
    sb.SteamBridgeView._day_sep(v, _MS)                    # used to raise OSError [Errno 22]


def test_view_coalesce_normalizes_ms_to_seconds():
    from puripuly_heart.ui.views import steam_bridge as sb

    v = object.__new__(sb.SteamBridgeView)
    blocks = sb.SteamBridgeView._coalesce(v, [
        {"from_me": True, "name": "You", "text": "a", "ts": _NOW - 60},
        {"from_me": True, "name": "You", "text": "b", "ts": _MS},
    ])
    assert all(0 < b["_ts"] < 4_294_967_296 for b in blocks)
    for b in blocks:                                       # the render path's day key
        time.localtime(b["_ts"])
