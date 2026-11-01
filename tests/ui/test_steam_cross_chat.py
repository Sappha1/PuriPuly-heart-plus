"""r668: a message sent in one Steam chat must never land in another chat's log.

Opening a chat with no cached history kept the view's block list pointing at
the PREVIOUS chat's list (the empty history reply for a chat with no messages
is ignored while that list is non-empty), so a send in the new chat was
written into the previous chat's cache and showed there on every reopen. The
snapshot kept it across restarts, and "Reload chat history" carried it back.

The view is built for real (no page); prefs and cache paths point at a temp dir.
"""
from __future__ import annotations

import asyncio
import json

import flet as ft
import pytest

from puripuly_heart.ui.views import steam_bridge as sb

_T0 = 1_791_000_000


def _texts(ctrl) -> list[str]:
    out: list[str] = []
    stack = [ctrl]
    while stack:
        c = stack.pop()
        if isinstance(c, ft.Text):
            if c.value:
                out.append(c.value)
            out.extend(s.text for s in (c.spans or []) if s.text)
        for attr in ("content", "controls"):
            v = getattr(c, attr, None)
            if isinstance(v, list):
                stack.extend(v)
            elif isinstance(v, ft.Control):
                stack.append(v)
    return out


def _msg(text: str, ts: int, *, from_me: bool = False, name: str = "Alex") -> dict:
    return {"from_me": from_me, "name": "You" if from_me else name, "avatar": "",
            "text": text, "images": [], "stickers": [], "ts": ts, "ord": 0}


@pytest.fixture
def view(tmp_path, monkeypatch):
    monkeypatch.setattr(sb, "_PREFS_FILE", tmp_path / "view_prefs.json")
    monkeypatch.setattr(sb, "_CACHE_FILE", tmp_path / "tr_cache.json")
    v = sb.SteamBridgeView()
    v._notices = []
    v._notice = v._notices.append
    v._tr_outgoing = False
    v._send_fmt = "orig_only"
    v._friends = {1: {"acct": 1, "name": "Alex", "state": 1, "last_chat": 0},
                  2: {"acct": 2, "name": "Robin", "state": 1, "last_chat": 0}}
    return v


def _open(view, acct: int, history: list | None = None) -> None:
    asyncio.run(view._open(acct))
    if history is not None:
        asyncio.run(view._handle({"ev": "history", "acct": acct, "messages": history}))


def _send(view, text: str) -> None:
    view._entry.value = text
    asyncio.run(view._send())


def _cache_texts(view, acct: int) -> list[str]:
    return [b.get("text") for b in view._chat_cache.get(acct) or []]


_ALEX = [_msg("hi", _T0), _msg("how are you", _T0 + 5)]


@pytest.mark.parametrize("history_first", [True, False],
                         ids=["empty-history-then-send", "send-before-history"])
def test_a_send_in_a_new_chat_stays_in_that_chat(view, history_first):
    _open(view, 1, _ALEX)
    assert _cache_texts(view, 1) == ["hi", "how are you"]

    # a friend never messaged: the helper answers the open with no history
    if history_first:
        _open(view, 2, [])
        _send(view, "first message to robin")
    else:
        _open(view, 2)
        _send(view, "first message to robin")
        asyncio.run(view._handle({"ev": "history", "acct": 2, "messages": []}))

    assert _cache_texts(view, 1) == ["hi", "how are you"]
    assert "first message to robin" in _cache_texts(view, 2)
    assert view._resend_queue[-1]["acct"] == 2

    _open(view, 1)        # back to the first chat: its log is untouched
    assert "first message to robin" not in _texts(view._messages)
    assert [b.get("text") for b in view._hist_blocks] == ["hi", "how are you"]


def test_an_empty_new_chat_says_so_instead_of_loading_forever(view):
    _open(view, 1, _ALEX)
    _open(view, 2, [])
    shown = _texts(view._messages)
    assert sb._T("steam.no_messages", default="No messages here yet") in shown
    assert sb._T("steam.loading_history", default="Loading chat history") not in shown


def _stray(text: str, ts: int, *, sent_to: int) -> dict:
    return {"from_me": True, "name": "You", "avatar": "", "text": text,
            "emoticons": [], "images": [], "stickers": [], "_ts": ts,
            "_send_sid": "x" * 32, "_send_acct": sent_to}


def test_reopening_a_chat_drops_a_stray_from_another_chat(view):
    _open(view, 1, _ALEX)
    _open(view, 2, [])
    # what the old bug left behind: chat 1's list holds a send made in chat 2
    view._chat_cache[1].append(_stray("meant for robin", _T0 + 60, sent_to=2))

    _open(view, 1)

    assert _cache_texts(view, 1) == ["hi", "how are you"]
    assert "meant for robin" not in _texts(view._messages)


def test_reload_drops_the_stray_instead_of_carrying_it_back(view):
    _open(view, 1, _ALEX)
    view._chat_cache[1].append(_stray("meant for robin", _T0 + 60, sent_to=2))
    # the reload button / a reconnect re-sends "open" without _open: the
    # history arrives for the active chat directly
    asyncio.run(view._handle({"ev": "history", "acct": 1, "messages": _ALEX}))

    assert _cache_texts(view, 1) == ["hi", "how are you"]
    assert "meant for robin" not in _texts(view._messages)


def test_own_sends_in_the_right_chat_are_kept(view):
    _open(view, 1, _ALEX)
    _send(view, "fine thanks")
    asyncio.run(view._handle({"ev": "history", "acct": 1, "messages": _ALEX}))
    assert "fine thanks" in _cache_texts(view, 1)


def test_the_snapshot_seed_drops_strays(view, tmp_path):
    snap = {"chats": {
        "1": [{"from_me": False, "name": "Alex", "text": "hi", "_ts": _T0},
              _stray("meant for robin", _T0 + 60, sent_to=2)],
        "2": [_stray("hello robin", _T0 + 61, sent_to=2)],
    }, "friends": [], "seen": {}}
    (tmp_path / "ui_snapshot.json").write_text(json.dumps(snap), encoding="utf-8")

    view._paint_snapshot(prefetch_only=False)

    assert _cache_texts(view, 1) == ["hi"]
    assert _cache_texts(view, 2) == ["hello robin"]
