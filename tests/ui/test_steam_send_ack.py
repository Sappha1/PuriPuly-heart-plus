"""r635: Steam view - text-send acks, the dashboard unread flag and the
socket line limit.

The view is built for real (no page) so the code under test is what the app
runs. Nothing here opens the helper socket or touches the user's prefs and
cache files - both paths are pointed at a temp dir before construction.
"""
from __future__ import annotations

import asyncio

import flet as ft
import pytest

from puripuly_heart.ui.views import steam_bridge as sb

FAILED_LINE = sb._T("steam.img_failed", default="Failed to send")
FAILED_NOTICE = sb._T("steam.send_failed",
                      default="Message not delivered - Steam rejected it, try again")


def _texts(ctrl) -> list[str]:
    """Every non-empty ft.Text value under a control, depth-first."""
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


def _mine(text: str, ts: int) -> dict:
    return {"from_me": True, "name": "You", "avatar": "", "text": text,
            "emoticons": [], "images": [], "stickers": [], "_ts": ts}


def _live(text: str, ts: int, *, from_me: bool = True, name: str = "You") -> dict:
    """A message as the socket delivers it - what _render_live consumes."""
    return {"from_me": from_me, "name": name, "avatar": "", "text": text,
            "images": [], "stickers": [], "ts": ts}


def _render(view, text: str, ts: int, **kw) -> dict:
    """Render one live line the way _send does (no page: nothing flushes)."""
    return asyncio.run(view._render_live(_live(text, ts, **kw)))


def _col_lines(inner: ft.Column) -> list[str]:
    """The text lines of one block's column, top to bottom, header skipped."""
    return [t for c in inner.controls[1:] for t in reversed(_texts(c))]


class _Writer:
    """Stands in for the helper socket: _cmd writes, drains, then bumps."""
    def write(self, data: bytes) -> None:
        pass

    async def drain(self) -> None:
        pass


@pytest.fixture
def view(tmp_path, monkeypatch):
    monkeypatch.setattr(sb, "_PREFS_FILE", tmp_path / "view_prefs.json")
    monkeypatch.setattr(sb, "_CACHE_FILE", tmp_path / "tr_cache.json")
    v = sb.SteamBridgeView()
    v._notices = []
    v._notice = v._notices.append          # no page -> no SnackBar; record instead
    v._tr_outgoing = False                 # plain text bubbles, no translator needed
    v._friends = {1: {"acct": 1, "name": "Alex", "state": 1, "last_chat": 0},
                  2: {"acct": 2, "name": "Robin", "state": 1, "last_chat": 0}}
    return v


def test_try_open_raises_the_stream_line_limit(view, monkeypatch):
    seen: dict = {}

    async def fake_open(host, port, **kw):
        seen.update(kw)
        return object(), object()

    monkeypatch.setattr(sb.asyncio, "open_connection", fake_open)
    assert asyncio.run(view._try_open()) is True
    assert seen["limit"] == 16 * 1024 * 1024


def test_unread_flag_reaches_the_dashboard_once_per_change(view):
    pushed: list[bool] = []
    view.on_unread_change = pushed.append
    view._tabs = [1, 2]
    view._active = 1

    view._rebuild_tabs()
    assert pushed == [False]

    view._unread_live.add(2)
    view._rebuild_tabs()
    view._rebuild_tabs()                   # unchanged -> not pushed again
    assert pushed == [False, True]

    view._unread_live.discard(2)
    view._unread_live.add(1)               # the active chat never counts (no chip dot either)
    view._rebuild_tabs()
    assert pushed == [False, True, False]

    view._unread_live.add(2)
    view._dnd = True                       # DND suppresses the flag like the dots
    view._rebuild_tabs()
    assert pushed == [False, True, False]


def test_unread_callback_errors_never_break_the_tab_strip(view):
    def boom(flag):
        raise RuntimeError("dashboard gone")

    view.on_unread_change = boom
    view._tabs = [2]
    view._unread_live.add(2)
    view._rebuild_tabs()                   # must not raise
    assert view._unread_flag_pushed is True


def test_send_attaches_a_sid_and_remembers_the_bubble(view):
    view._active = 1
    view._send_fmt = "orig_only"
    view._entry.value = "hello"
    asyncio.run(view._send())              # no socket -> the command is queued

    sent = view._resend_queue[-1]
    assert sent["cmd"] == "send" and sent["text"] == "hello"
    sid = sent["sid"]
    assert sid in view._pending_txt
    b = view._pending_txt[sid]["b"]
    assert b["_send_sid"] == sid and b["text"] == "hello"
    assert view._pending_txt[sid]["col"] is not None


def test_pending_text_sends_are_bounded(view):
    for i in range(60):
        view._track_txt_send(_mine(str(i), i), 1)
    assert len(view._pending_txt) == 50


def test_failed_ack_marks_the_bubble_and_notices(view):
    view._active = 1
    col = view._ensure_msg_list(1)
    b = _render(view, "hi", 1000)          # a fresh block of mine, as _send renders it
    sid = view._track_txt_send(b, 1)
    assert b["_col"] is col.controls[-1].content.controls[1]

    asyncio.run(view._handle({"ev": "sent", "ok": False, "acct": 1, "sid": sid}))

    assert sid not in view._pending_txt
    assert b["_send_failed"] is True
    assert len(col.controls) == 1          # the block's Container was NOT rebuilt
    assert _col_lines(col.controls[-1].content.controls[1]) == ["hi", FAILED_LINE]
    assert view._notices == [FAILED_NOTICE]


def test_failed_ack_keeps_the_later_bubbles_of_the_same_block(view):
    # 'one', 'two', 'three' within _GROUP_GAP_S share ONE block (one
    # Container, one column). A late failure for 'one' must not rebuild that
    # Container: that wiped 'two' and 'three' off the screen and left their
    # pending entries pointing at a detached column, so a following failure
    # for 'two' changed nothing visible.
    view._active = 1
    col = view._ensure_msg_list(1)
    blocks = [_render(view, t, 1000 + i)
              for i, t in enumerate(("one", "two", "three"))]
    sids = [view._track_txt_send(b, 1) for b in blocks]
    assert len(col.controls) == 1
    inner = col.controls[0].content.controls[1]
    assert _col_lines(inner) == ["one", "two", "three"]

    asyncio.run(view._handle({"ev": "sent", "ok": False, "acct": 1, "sid": sids[0]}))
    assert len(col.controls) == 1
    assert col.controls[0].content.controls[1] is inner
    assert _col_lines(inner) == ["one", FAILED_LINE, "two", "three"]

    asyncio.run(view._handle({"ev": "sent", "ok": False, "acct": 1, "sid": sids[1]}))
    assert _col_lines(inner) == ["one", FAILED_LINE, "two", FAILED_LINE, "three"]
    assert blocks[0]["_send_failed"] is True and blocks[1]["_send_failed"] is True
    assert "_send_failed" not in blocks[2]
    assert view._notices == [FAILED_NOTICE, FAILED_NOTICE]


def test_failed_line_stays_in_my_column_when_a_friend_rendered_meanwhile(view):
    # _send tracks the sid only after _render_live returns, and _render_live
    # awaits the translator; a friend's line landing during that await moves
    # _last_block to THEIR block. The column is captured at render time, so
    # the failure still marks my bubble and never theirs.
    view._active = 1
    view._tr_incoming = False
    col = view._ensure_msg_list(1)
    _render(view, "earlier", 1000)
    mine = _render(view, "hello", 1010)                 # grouped under 'earlier'
    _render(view, "hey", 1011, from_me=False, name="Alex")
    assert view._last_block["col"] is col.controls[-1].content.controls[1]
    sid = view._track_txt_send(mine, 1)                 # "after the await"

    asyncio.run(view._handle({"ev": "sent", "ok": False, "acct": 1, "sid": sid}))

    my_col = col.controls[0].content.controls[1]
    their_col = col.controls[1].content.controls[1]
    assert _col_lines(my_col) == ["earlier", "hello", FAILED_LINE]
    assert FAILED_LINE not in _col_lines(their_col)
    assert view._notices == [FAILED_NOTICE]


def test_own_send_counts_as_seen_so_switching_away_flags_nothing(view, monkeypatch):
    # the send's last_chat bump lands after translate latency, past the
    # render's seen mark; that flagged the chat (chip dot, and via r635 the
    # dashboard tab) the moment another chat was opened
    pushed: list[bool] = []
    view.on_unread_change = pushed.append
    view._tabs = [1, 2]
    view._active = 1
    view._writer = _Writer()
    view._seen_chat_ts[1] = 1000                        # my bubble rendered at 1000
    monkeypatch.setattr(sb.time, "time", lambda: 1001.7)

    assert asyncio.run(view._cmd({"cmd": "send", "acct": 1, "text": "x", "sid": "s"}))
    assert view._friends[1]["last_chat"] == 1001
    assert view._seen_chat_ts[1] == 1001

    view._active = 2                                    # switched away
    view._rebuild_tabs()
    assert view._tab_unread(1) is False
    assert pushed == [False]


def test_ok_ack_drops_silently_and_old_helpers_only_notice(view):
    view._active = 1
    b = _mine("fine", 1)
    sid = view._track_txt_send(b, 1)

    asyncio.run(view._handle({"ev": "sent", "ok": True, "acct": 1, "sid": sid}))
    assert sid not in view._pending_txt
    assert "_send_failed" not in b
    assert view._notices == []

    other = _mine("still pending", 2)
    view._track_txt_send(other, 1)
    asyncio.run(view._handle({"ev": "sent", "ok": False}))   # pre-sid helper
    assert view._notices == [FAILED_NOTICE]
    assert "_send_failed" not in other     # nothing to attribute it to


def test_refresh_block_keeps_the_grouping_anchor_on_the_newer_block(view):
    view._active = 1
    col = view._ensure_msg_list(1)
    mine = _mine("mine", 1)
    theirs = {"from_me": False, "name": "Alex", "avatar": "", "text": "reply",
              "emoticons": [], "images": [], "stickers": [], "_ts": 2}
    col.controls.append(view._block_control(mine))
    col.controls.append(view._block_control(theirs))
    anchor = view._last_block

    mine["_send_failed"] = True
    assert view._refresh_block(mine) is True
    assert view._last_block is anchor      # an older block's rebuild is not an anchor

    assert view._refresh_block(theirs) is True
    assert view._last_block is not anchor  # the newest block's rebuild re-anchors
    assert view._last_block["col"] is col.controls[-1].content.controls[1]
