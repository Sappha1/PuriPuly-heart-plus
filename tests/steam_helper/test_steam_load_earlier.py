"""Steam tab "Load earlier messages": only strictly older history comes back.

The page fetch used to return the chat's cached recent messages along with the
requested window, so every press appended the newest message again, and a chat
with no older history never reached "No earlier messages".
"""

from __future__ import annotations

import ast
import asyncio
import json
import shutil
import subprocess
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]
_SRC = _REPO / "src" / "puripuly_heart" / "data" / "steam_bridge_src"
_NOW = 1_791_481_941
_NODE = shutil.which("node")


def _range_body() -> str:
    tree = ast.parse((_SRC / "steam_page.py").read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "fetch_messages_range":
            for sub in ast.walk(node):
                if (isinstance(sub, ast.Call) and isinstance(sub.func, ast.Attribute)
                        and sub.func.attr in ("evaluate", "_ev") and sub.args
                        and isinstance(sub.args[0], ast.Constant)):
                    return sub.args[0].value
    raise AssertionError("fetch_messages_range body not found")


_HARNESS = r"""
const NOW = %(now)d;
const recent = {unAccountID: 100, strMessageInternal: "hello", rtTimestamp: NOW - 10, unOrdinal: 0};
const old = {unAccountID: 100, strMessageInternal: "older", rtTimestamp: NOW - 900, unOrdinal: 0};
// the client returns its cached recent message whatever window is asked for
const chat = {m_unAccountIDFriend: 100,
  async GetMessagesFromTimeRange(s, e) { return {messages: [old, recent]}; }};
globalThis.window = {g_FriendsUIApp: {m_ChatStore: {m_FriendChatStore: {m_rgFriendChats: [chat]}, GetFriendChat: () => chat}}};
const RANGE = %(range)s;
(async () => {
  console.log(JSON.stringify(await RANGE([100, NOW - 1200, NOW - 100])));
})();
"""


@pytest.mark.skipif(_NODE is None, reason="node not installed")
def test_range_fetch_drops_messages_outside_the_window(tmp_path):
    js = tmp_path / "range.js"
    js.write_text(_HARNESS % {"now": _NOW, "range": _range_body()}, encoding="utf-8")
    p = subprocess.run([_NODE, str(js)], capture_output=True, text=True, timeout=60)
    assert p.returncode == 0, p.stderr
    texts = [m["text"] for m in json.loads(p.stdout)]
    assert texts == ["older"]


def test_helper_asks_for_the_windows_in_parallel_batches():
    text = (_SRC / "daemon.py").read_text(encoding="utf-8")
    start = text.index('elif cmd == "more_history":')
    block = text[start:text.index('elif cmd == "send":', start)]
    assert "asyncio.gather(" in block
    assert "<= end" in block  # nothing at or after the oldest shown message


def _view():
    from puripuly_heart.ui.views import steam_bridge as sb

    v = object.__new__(sb.SteamBridgeView)
    v._active = 100
    v._loadmore_busy = {100: True}
    v._hist_blocks = [{"from_me": True, "text": "hello", "_ts": _NOW - 10, "_ord": 0}]
    v._rendered: list = []

    class _Label:
        value = ""

        def update(self):
            pass

    label = _Label()
    v._pill_lbl = lambda acct: label

    async def _render_live(m, **_kw):
        v._rendered.append(m)

    v._render_live = _render_live
    return sb, v, label


def test_a_reply_repeating_the_shown_message_adds_nothing():
    sb, v, label = _view()

    asyncio.run(sb.SteamBridgeView._handle(v, {
        "ev": "history_older", "acct": 100,
        "messages": [{"from_me": True, "text": "hello", "ts": _NOW - 10, "ord": 0}],
    }))

    assert v._rendered == []
    assert label.value == "No earlier messages"
    assert 100 not in v._loadmore_busy


def test_strictly_older_messages_still_load():
    sb, v, label = _view()

    asyncio.run(sb.SteamBridgeView._handle(v, {
        "ev": "history_older", "acct": 100,
        "messages": [
            {"from_me": False, "text": "yo", "ts": _NOW - 3600, "ord": 0},
            {"from_me": True, "text": "hello", "ts": _NOW - 10, "ord": 0},
        ],
    }))

    assert [m["text"] for m in v._rendered] == ["yo"]
    assert label.value == "Load earlier messages"
