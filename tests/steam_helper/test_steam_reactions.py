"""Emoji/sticker reactions on Steam messages reach the app: the page readers
include them, the daemon re-reads the open chat for reaction changes, and the
view shows them under the message with counts."""

from __future__ import annotations

import ast
import importlib.util
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]
_SRC = _REPO / "src" / "puripuly_heart" / "data" / "steam_bridge_src"
_NOW = 1_791_481_941


@pytest.fixture(scope="module")
def daemon_mod(tmp_path_factory):
    d = tmp_path_factory.mktemp("daemon_rx")
    for name in ("daemon.py", "steam_page.py"):
        shutil.copy2(_SRC / name, d / name)
    spec = importlib.util.spec_from_file_location("pph_steam_daemon_rx", d / "daemon.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def test_shape_carries_reactions(daemon_mod):
    d = object.__new__(daemon_mod.Daemon) if hasattr(daemon_mod, "Daemon") else None
    cls = next(v for v in vars(daemon_mod).values()
               if isinstance(v, type) and hasattr(v, "_shape_rx"))
    rx = cls._shape_rx([
        {"t": 1, "r": "steamthumbsup", "n": 2, "me": True},
        {"t": 1, "r": "gone", "n": 0},
        {"t": 2, "r": "Sticker_Cat", "n": 1, "me": False},
        "junk",
    ])
    assert rx == [
        {"type": 1, "name": "steamthumbsup", "count": 2, "mine": True},
        {"type": 2, "name": "Sticker_Cat", "count": 1, "mine": False},
    ]
    _ = d


class _FakeSteam:
    def __init__(self, batches):
        self.batches = list(batches)

    async def fetch_messages_range(self, acct, start, end):
        return self.batches.pop(0)


def _daemon(daemon_mod, batches):
    cls = next(v for v in vars(daemon_mod).values()
               if isinstance(v, type) and hasattr(v, "_sync_reactions"))
    d = object.__new__(cls)
    d.steam = _FakeSteam(batches)
    d.active = 100
    d.own = 1
    d._rx_sig = {}
    d.emitted = []

    async def emit(obj, only=None):
        d.emitted.append(obj)

    d.emit = emit
    return d


@pytest.mark.asyncio
async def test_sync_sends_the_full_set_first_then_only_changes(daemon_mod):
    msg = {"from": 1, "text": "hi", "ordinal": 0, "ts": _NOW - 30}
    thumbs = [{"t": 1, "r": "steamthumbsup", "n": 1, "me": False}]
    d = _daemon(daemon_mod, [
        [dict(msg, rx=thumbs)],
        [dict(msg, rx=thumbs)],                                     # unchanged
        [dict(msg, rx=[dict(thumbs[0], n=2)])],                     # a second reactor
        [dict(msg, rx=[])],                                         # all removed
    ])

    await d._sync_reactions(100)
    assert d.emitted[-1]["items"] == [{"ts": _NOW - 30, "ord": 0, "reactions": [
        {"type": 1, "name": "steamthumbsup", "count": 1, "mine": False}],
        "from_me": True, "text": "hi"}]

    await d._sync_reactions(100)
    assert len(d.emitted) == 1                                      # nothing new

    await d._sync_reactions(100)
    assert d.emitted[-1]["items"][0]["reactions"][0]["count"] == 2

    await d._sync_reactions(100)
    assert d.emitted[-1]["items"] == [{"ts": _NOW - 30, "ord": 0, "reactions": [],
                                        "from_me": True, "text": "hi"}]


# ── page JS under Node ───────────────────────────────────────────────────────

_NODE = shutil.which("node")


def _evaluate_bodies(path: Path) -> dict:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    out = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef) and node.name in (
                "read_messages", "fetch_messages", "fetch_messages_range"):
            for sub in ast.walk(node):
                if (isinstance(sub, ast.Call) and isinstance(sub.func, ast.Attribute)
                        and sub.func.attr in ("evaluate", "_ev") and sub.args
                        and isinstance(sub.args[0], ast.Constant)):
                    out[node.name] = sub.args[0].value
                    break
    return out


_HARNESS = r"""
const NOW = %(now)d;
Date.now = () => NOW * 1000;
const msg = {unAccountID: 100, strMessageInternal: "hello", rtTimestamp: NOW - 10, unOrdinal: 0,
  m_rgReactions: [
    {eReactionType: 1, strReaction: "steamthumbsup", cReactors: 2, bUserReacted: true},
    {eReactionType: 1, strReaction: "removed", cReactors: 0, bUserReacted: false}]};
const chat = {m_unAccountIDFriend: 100, m_rgChatMessages: [msg],
  async GetMessagesFromTimeRange(e, t) { return {messages: [msg]}; }};
globalThis.window = {g_FriendsUIApp: {m_ChatStore: {m_FriendChatStore: {m_rgFriendChats: [chat]}, GetFriendChat: () => chat}}};
const READ = %(read)s, FETCH = %(fetch)s, RANGE = %(range)s;
(async () => {
  console.log(JSON.stringify({read: await READ(100), poll: await FETCH([100, NOW - 60]),
                              range: await RANGE([100, NOW - 600, NOW])}));
})();
"""


@pytest.mark.skipif(_NODE is None, reason="node not installed")
def test_every_page_reader_includes_live_reactions(tmp_path):
    b = _evaluate_bodies(_SRC / "steam_page.py")
    js = tmp_path / "rx.js"
    js.write_text(_HARNESS % {"now": _NOW, "read": b["read_messages"],
                              "fetch": b["fetch_messages"], "range": b["fetch_messages_range"]},
                  encoding="utf-8")
    p = subprocess.run([_NODE, str(js)], capture_output=True, text=True, timeout=60)
    assert p.returncode == 0, p.stderr
    r = json.loads(p.stdout)
    for key in ("read", "poll", "range"):
        assert r[key][0]["rx"] == [{"t": 1, "r": "steamthumbsup", "n": 2, "me": True}], key


# ── app view ─────────────────────────────────────────────────────────────────


def _view():
    from puripuly_heart.ui.views import steam_bridge as sb

    v = object.__new__(sb.SteamBridgeView)
    v._active = 100
    v._chat_cache = {}
    v._hist_blocks = []
    v._rx_unmatched = {}
    return sb, v


def test_reactions_from_the_helper_land_on_the_matching_message():
    sb, v = _view()
    blocks = sb.SteamBridgeView._coalesce(v, [
        {"from_me": True, "name": "You", "text": "hi", "ts": _NOW - 30, "ord": 0},
        {"from_me": False, "name": "Alex", "text": "yo", "ts": _NOW - 20, "ord": 0},
    ])
    v._hist_blocks = blocks

    changed = sb.SteamBridgeView._apply_reactions(v, 100, [
        {"ts": _NOW - 30, "ord": 0,
         "reactions": [{"type": 1, "name": "steamthumbsup", "count": 1, "mine": False}]}])

    assert changed is True
    assert blocks[0]["_rx"][0]["name"] == "steamthumbsup"
    assert "_rx" not in blocks[1]
    # a second identical update changes nothing
    assert sb.SteamBridgeView._apply_reactions(v, 100, [
        {"ts": _NOW - 30, "ord": 0, "reactions": blocks[0]["_rx"]}]) is False


def test_history_messages_bring_their_reactions_and_chips_render():
    sb, v = _view()
    (block,) = sb.SteamBridgeView._coalesce(v, [{
        "from_me": True, "name": "You", "text": "hi", "ts": _NOW, "ord": 0,
        "reactions": [{"type": 1, "name": "steamthumbsup", "count": 3, "mine": True},
                      {"type": 2, "name": "Cat_Sticker", "count": 1, "mine": False}]}])

    chips = sb.SteamBridgeView._reaction_chips(v, block)

    assert len(chips) == 2
    count_texts = [c.value for c in chips[0].content.controls if hasattr(c, "value")]
    assert count_texts == ["3"]
    assert "sticker" in chips[1].content.controls[0].src


def test_a_reaction_sent_from_here_shows_until_steam_confirms_it():
    sb, v = _view()
    block = {"_ts": _NOW, "_ord": 0, "_reacts": [":steamhappy:"]}

    merged = sb.SteamBridgeView._merged_reactions(v, block)
    assert merged == [{"type": 1, "name": ":steamhappy:", "count": 1, "mine": True}]

    block["_rx"] = [{"type": 1, "name": "steamhappy", "count": 1, "mine": True}]
    assert len(sb.SteamBridgeView._merged_reactions(v, block)) == 1  # no duplicate


_THUMBS = [{"type": 1, "name": "steamthumbsup", "count": 1, "mine": False}]


def test_a_message_sent_from_here_matches_despite_its_local_send_time():
    # the app stores its own sends with the local clock; Steam's timestamp for
    # the same message can be a few seconds later
    sb, v = _view()
    mine = {"from_me": True, "text": "?", "emoticons": [], "_ts": _NOW - 3, "_ord": 0}
    theirs = {"from_me": False, "text": "yo", "emoticons": [], "_ts": _NOW - 1, "_ord": 0}
    v._hist_blocks = [mine, theirs]

    changed = sb.SteamBridgeView._apply_reactions(v, 100, [
        {"ts": _NOW, "ord": 4, "from_me": True, "text": "?", "reactions": _THUMBS}])

    assert changed is True
    assert mine["_rx"] == _THUMBS and "_rx" not in theirs
    assert v._rx_unmatched == {}


def test_text_picks_the_right_one_of_two_close_messages():
    sb, v = _view()
    first = {"from_me": True, "text": "hello there", "emoticons": [], "_ts": _NOW - 4, "_ord": 0}
    second = {"from_me": True, "text": "how are you", "emoticons": [], "_ts": _NOW - 2, "_ord": 0}
    v._hist_blocks = [first, second]

    sb.SteamBridgeView._apply_reactions(v, 100, [
        {"ts": _NOW - 1, "ord": 0, "from_me": True, "text": "hello there", "reactions": _THUMBS}])

    assert first.get("_rx") == _THUMBS and "_rx" not in second


def test_an_update_for_a_message_not_shown_yet_waits_and_applies_later():
    sb, v = _view()
    v._hist_blocks = []
    v.page = None
    rerendered = []
    v._rerender_chat = lambda: rerendered.append(True)

    assert sb.SteamBridgeView._apply_reactions(v, 100, [
        {"ts": _NOW, "ord": 0, "from_me": False, "text": "late", "reactions": _THUMBS}]) is False
    assert len(v._rx_unmatched[100]) == 1

    late = {"from_me": False, "text": "late", "emoticons": [], "_ts": _NOW, "_ord": 0}
    v._hist_blocks.append(late)
    sb.SteamBridgeView._retry_unmatched_reactions(v)

    assert late["_rx"] == _THUMBS
    assert rerendered == [True]
    assert 100 not in v._rx_unmatched


def test_far_away_messages_are_never_matched():
    sb, v = _view()
    old = {"from_me": True, "text": "?", "emoticons": [], "_ts": _NOW - 60, "_ord": 0}
    v._hist_blocks = [old]

    assert sb.SteamBridgeView._apply_reactions(v, 100, [
        {"ts": _NOW, "ord": 0, "from_me": True, "text": "?", "reactions": _THUMBS}]) is False
    assert "_rx" not in old
