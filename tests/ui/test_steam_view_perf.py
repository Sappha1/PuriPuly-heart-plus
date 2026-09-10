"""r636: Steam view render cost - targeted updates, the friends-list build
gate, the per-chat column cap, the debounced UI snapshot, friend lines that
paint before their translation, and the helper's hello handshake.

The view is built for real (no page) so the code under test is what the app
runs. Nothing here opens the helper socket or touches the user's prefs and
cache files - both paths are pointed at a temp dir before construction.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time

import flet as ft
import pytest

from puripuly_heart.ui.views import steam_bridge as sb

# a fixed local noon: every timestamp in a test stays on one calendar day
_NOON = int(time.mktime((2024, 1, 15, 12, 0, 0, 0, 0, -1)))
_DAY = 24 * 3600


def _friend(acct: int, **kw) -> dict:
    f = {"acct": acct, "name": f"Friend {acct}", "state": 1, "last_chat": 0}
    f.update(kw)
    return f


def _live(text: str, ts: int, *, from_me: bool = False, name: str = "Alex") -> dict:
    """A message as the socket delivers it - what _render_live consumes."""
    return {"from_me": from_me, "name": name, "avatar": "", "text": text,
            "images": [], "stickers": [], "ts": ts}


def _blocks(col: ft.Column) -> list:
    """The block Containers of one chat column (day separators, pills skipped)."""
    return [c for c in col.controls if isinstance(getattr(c, "data", None), dict)]


def _dayseps(col: ft.Column) -> list:
    return [c for c in col.controls if getattr(c, "data", None) == "daysep"]


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
    v._tr_incoming = False
    v._state_mode = ""                     # no state overlay to dismiss on first paint
    return v


def _count_rebuilds(view) -> list:
    calls: list = []
    orig = view._rebuild_friends

    def wrapped(**kw):
        r = orig(**kw)
        calls.append(r)
        return r

    view._rebuild_friends = wrapped
    return calls


# ── friends list build gate ──────────────────────────────────────────────────
def test_identical_friends_build_once(view):
    view._friends = {a: _friend(a, state=a % 3, last_chat=1000 + a) for a in range(1, 31)}
    view._friends[4]["fav"] = True
    view._friends[7]["unread"] = 2

    assert view._rebuild_friends() is True
    first = list(view._friends_list.controls)
    assert len(first) > 30                       # headers + rows

    assert view._rebuild_friends() is False      # same rows -> no rebuild
    assert view._rebuild_friends() is False
    assert len(view._friends_list.controls) == len(first)
    assert all(a is b for a, b in zip(view._friends_list.controls, first))

    view._friends[10]["state"] = 0               # a real change (online -> offline) rebuilds
    assert view._rebuild_friends() is True
    assert not all(a is b for a, b in zip(view._friends_list.controls, first))
    assert view._rebuild_friends(force=True) is True


def test_same_friends_push_twice_builds_no_rows_the_second_time(view):
    items = [_friend(a, state=1, avatar=f"https://example.invalid/{a}.jpg")
             for a in range(1, 41)]
    built: list = []
    orig_row = view._friend_row

    def counting_row(f, **kw):
        built.append(int(f["acct"]))
        return orig_row(f, **kw)

    view._friend_row = counting_row

    asyncio.run(view._handle({"ev": "friends", "items": [dict(i) for i in items]}))
    n_first = len(built)
    assert n_first == 40
    asyncio.run(view._handle({"ev": "friends", "items": [dict(i) for i in items]}))
    assert len(built) == n_first                 # second identical push: zero rows built

    # the daemon may omit empty fields (icon/extra/nick/real/appid) - a
    # push that only drops them is still the same list
    slim = [{k: v for k, v in i.items() if v not in ("", 0, None) or k in ("acct", "last_chat")}
            for i in items]
    asyncio.run(view._handle({"ev": "friends", "items": slim}))
    assert len(built) == n_first


def test_friend_row_tolerates_omitted_fields(view):
    view._active = 3
    f = {"acct": 3, "name": "Friend 3", "state": 2, "ingame": True, "unread": 1}
    view._friends = {3: f}
    assert view._friend_row(f) is not None       # no icon/extra/game/nick/real/appid
    assert view._tab_chip(3) is not None
    assert view._rail_row(f) is not None
    assert view._rebuild_friends() is True


def test_active_chat_only_matters_while_a_section_is_collapsed(view):
    view._friends = {a: _friend(a) for a in range(1, 11)}
    view._active = 1
    assert view._rebuild_friends() is True
    view._active = 2                             # a tab switch, nothing collapsed
    assert view._rebuild_friends() is False
    view._collapsed_sections.add("ONLINE")       # now the active row stays visible
    assert view._rebuild_friends() is True
    view._active = 3
    assert view._rebuild_friends() is True


# ── RECENT ordering gate on the hot paths ────────────────────────────────────
def test_inbound_to_the_top_recent_chat_rebuilds_nothing(view):
    view._friends = {a: _friend(a, last_chat=1000 * a) for a in range(1, 7)}
    view._rebuild_friends()
    assert view._chat_order(view._friend_items())[1] == (6, 5, 4, 3)
    calls = _count_rebuilds(view)
    view._active = 99                            # the inbound chat is a background tab

    # already on top of RECENT: the bump changes no ordering -> no rebuild
    asyncio.run(view._handle({"ev": "inbound", "acct": 6,
                              "message": _live("hi", 999_999, name="Friend 6")}))
    assert calls == []
    assert view._friends[6]["last_chat"] == 999_999

    # a friend outside RECENT moves in -> exactly one rebuild
    asyncio.run(view._handle({"ev": "inbound", "acct": 1,
                              "message": _live("hey", 1_000_000, name="Friend 1")}))
    assert calls == [True]
    assert view._chat_order(view._friend_items())[1] == (1, 6, 5, 4)


def test_own_send_bump_rebuilds_only_when_recent_reorders(view, monkeypatch):
    view._friends = {a: _friend(a, last_chat=1000 * a) for a in range(1, 7)}
    view._rebuild_friends()
    calls = _count_rebuilds(view)
    view._writer = _Writer()
    monkeypatch.setattr(sb.time, "time", lambda: 2_000_000.0)

    assert asyncio.run(view._cmd({"cmd": "send", "acct": 6, "text": "x", "sid": "a"}))
    assert calls == []                           # 6 was already first
    assert asyncio.run(view._cmd({"cmd": "send", "acct": 2, "text": "y", "sid": "b"}))
    assert calls == [True]                       # 2 entered RECENT


def test_unread_membership_not_order_drives_the_signature(view):
    # the UNREAD MESSAGES section sorts by status + name, so two unread
    # friends swapping last_chat order is not a visible change
    view._friends = {1: _friend(1, unread=1, last_chat=10),
                     2: _friend(2, unread=1, last_chat=20),
                     3: _friend(3, last_chat=5)}
    before = view._chat_order(view._friend_items())
    view._friends[1]["last_chat"] = 30
    assert view._chat_order(view._friend_items()) == before


# ── per-chat column cap ───────────────────────────────────────────────────────
def _fill(view, col, n: int, *, start_ts: int, step: int = 10) -> list:
    """n live friend lines, each its own block: the sender alternates with
    the timestamp's parity, so consecutive lines never group (same-sender
    lines within _GROUP_GAP_S share one block control)."""
    out = []
    for i in range(n):
        ts = start_ts + i * step
        name = "Alex" if (ts // step) % 2 == 0 else "Robin"
        out.append(asyncio.run(view._render_live(_live(f"line {i}", ts, name=name))))
    return out


def test_live_append_caps_the_column_and_cleans_the_registries(view):
    view._active = 1
    col = view._ensure_msg_list(1)
    view._chat_cache[1] = view._hist_blocks       # as _render_history leaves it
    blocks = _fill(view, col, 199, start_ts=_NOON)
    assert len(_blocks(col)) == 199

    view._pending_txt["t0"] = {"b": blocks[0], "col": blocks[0]["_col"],
                               "anchor": None, "acct": 1}
    view._pending_imgs["i1"] = blocks[1]
    view._pending_txt["tk"] = {"b": blocks[150], "col": blocks[150]["_col"],
                               "anchor": None, "acct": 1}
    view._react_target = blocks[2]

    more = _fill(view, col, 4, start_ts=_NOON + 199 * 10)   # blocks 200, 201, 202, 203
    assert len(_blocks(col)) == 200
    assert _blocks(col)[-1].data is more[-1]
    assert _blocks(col)[0].data is blocks[3]      # the three oldest went
    assert len(view._hist_blocks) == 200
    assert view._chat_cache[1] is view._hist_blocks
    assert all(b.get("_trimmed") for b in blocks[:3])
    assert not any(b.get("_trimmed") for b in blocks[3:])
    assert "t0" not in view._pending_txt and "i1" not in view._pending_imgs
    assert "tk" in view._pending_txt
    assert view._react_target is None
    assert view._last_block["col"] is more[-1]["_col"]


def test_grouped_lines_of_a_trimmed_block_go_with_it(view):
    view._active = 1
    col = view._ensure_msg_list(1)
    first = asyncio.run(view._render_live(_live("a", _NOON, name="Alex")))
    grouped = asyncio.run(view._render_live(_live("b", _NOON + 5, name="Alex")))
    assert len(_blocks(col)) == 1 and grouped["_col"] is first["_col"]
    _fill(view, col, 200, start_ts=_NOON + 1000)   # 201 blocks -> the first one goes
    assert len(_blocks(col)) == 200
    assert first not in view._hist_blocks and grouped not in view._hist_blocks
    assert grouped.get("_trimmed") is True


def test_reading_up_the_active_chat_defers_the_trim(view):
    view._active = 1
    col = view._ensure_msg_list(1)
    _fill(view, col, 200, start_ts=_NOON)
    view._following = False                        # scrolled up, reading
    _fill(view, col, 2, start_ts=_NOON + 200 * 10)
    assert len(_blocks(col)) == 202                # the viewport never moved
    view._following = True
    _fill(view, col, 1, start_ts=_NOON + 202 * 10)
    assert len(_blocks(col)) == 200                # caught up on the next append


def test_trim_keeps_the_fresh_day_separator_and_drops_orphans(view):
    view._active = 1
    col = view._ensure_msg_list(1)
    d0, d1, d2, d3 = (_NOON - 2 * _DAY, _NOON - _DAY, _NOON, _NOON + _DAY)
    _fill(view, col, 1, start_ts=d0)
    _fill(view, col, 1, start_ts=d1)
    view._following = False                        # reading up: the trim is deferred
    _fill(view, col, 199, start_ts=d2)             # 201 blocks, 2 separators
    assert len(_blocks(col)) == 201 and len(_dayseps(col)) == 2

    view._following = True
    new = _fill(view, col, 1, start_ts=d3)[0]      # a new day on an over-full column
    assert len(_blocks(col)) == 200
    seps = _dayseps(col)
    assert len(seps) == 2                          # d1's orphan went, d2 + d3 stay
    assert col.controls[0] is seps[0]              # d2 labels the surviving front
    assert col.controls[-2] is seps[1]             # d3 right above the new block
    assert col.controls[-1].data is new


def test_load_more_inserts_are_never_capped(view):
    view._active = 1
    col = view._ensure_msg_list(1)
    _fill(view, col, 200, start_ts=_NOON)
    older = asyncio.run(view._render_live(_live("old", _NOON - 3600, name="Robin")))
    assert len(_blocks(col)) == 201
    assert _blocks(col)[0].data is older


# ── snapshot: dirty flag + debounced writer, flushed on teardown ─────────────
def _count_writes(view) -> list:
    writes: list = []
    view._write_snapshot_file = writes.append
    return writes


def test_snapshot_writes_are_debounced_and_teardown_flushes(view):
    view._friends = {1: _friend(1), 2: _friend(2)}
    view._SNAP_MIN_GAP_S = 0.3
    writes = _count_writes(view)

    async def main():
        view._save_snapshot()
        view._save_snapshot()
        view._save_snapshot()
        await asyncio.sleep(0.05)
        assert len(writes) == 1                    # one write for the burst
        view._save_snapshot()                      # dirty again inside the gap
        await asyncio.sleep(0.05)
        assert len(writes) == 1                    # held by the debounce
        assert view._snap_dirty is True
        await view.shutdown()                      # teardown bypasses it
        assert len(writes) == 2
        assert view._snap_dirty is False
        await view._flush_snapshot()               # nothing dirty -> no write
        assert len(writes) == 2

    asyncio.run(main())
    snap = json.loads(writes[-1])
    assert set(snap) == {"seen", "chats", "friends", "own"}   # format unchanged
    assert [f["acct"] for f in snap["friends"]] == [1, 2]


def test_snapshot_writer_wakes_after_the_gap(view):
    view._SNAP_MIN_GAP_S = 0.15
    writes = _count_writes(view)

    async def main():
        view._save_snapshot()
        await asyncio.sleep(0.02)
        view._save_snapshot()
        await asyncio.sleep(0.02)
        assert len(writes) == 1
        await asyncio.sleep(0.25)
        assert len(writes) == 2                    # the deferred write landed
        assert view._snap_dirty is False

    asyncio.run(main())


def test_deactivate_and_module_off_schedule_a_flush(view):
    scheduled: list = []

    class _Page:
        def run_task(self, fn, *a):
            scheduled.append(getattr(fn, "__name__", str(fn)))

    view.page = _Page()
    view._snap_dirty = True
    view.deactivate()
    assert "_flush_snapshot" in scheduled
    view._state_mode = "popped"
    view.on_popout_restore = lambda: None
    view._state_action()
    assert scheduled.count("_flush_snapshot") == 2
    view.page = None


def test_snapshot_without_a_loop_writes_synchronously(view):
    writes = _count_writes(view)
    view._save_snapshot()
    assert len(writes) == 1 and view._snap_dirty is False


# ── friend lines paint before their translation ─────────────────────────────
def test_friend_line_renders_before_a_slow_translation(view):
    view._active = 1
    col = view._ensure_msg_list(1)
    view._tr_incoming = True
    view._show_pinyin = False
    view._needs_tr = lambda text: True
    gate = asyncio.Event()
    calls: list = []

    async def slow_tr(text, is_own):
        calls.append(text)
        await gate.wait()
        return "TR " + text

    view.translate_message = slow_tr

    async def main():
        b = await asyncio.wait_for(view._render_live(_live("nihao", _NOON)), 0.5)
        assert _blocks(col)[-1].data is b          # on screen before the translator answers
        assert not b["_ctrl"].spans                # the translation line is still empty
        assert len(view._tr_tasks) == 1
        await asyncio.sleep(0)                     # the task starts
        assert calls == ["nihao"]
        gate.set()
        await asyncio.gather(*view._tr_tasks)
        assert b["_ctrl"].visible is True
        assert [s.text for s in b["_ctrl"].spans] == ["TR nihao"]
        assert view._tr_tasks == set()

    asyncio.run(main())


def test_own_line_still_awaits_its_translation_inline(view):
    view._active = 1
    view._ensure_msg_list(1)
    view._tr_outgoing = True
    view._show_pinyin = False
    view._needs_tr = lambda text: True
    gate = asyncio.Event()

    async def slow_tr(text, is_own):
        await gate.wait()
        return "TR " + text

    view.translate_message = slow_tr

    async def main():
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(view._render_live(_live("mine", _NOON, from_me=True,
                                                           name="You")), 0.1)
        assert view._tr_tasks == set()             # nothing was backgrounded
        gate.set()

    asyncio.run(main())


def test_trimmed_block_translation_stands_down(view):
    view._active = 1
    col = view._ensure_msg_list(1)
    view._tr_incoming = True
    view._show_pinyin = False
    view._needs_tr = lambda text: True
    gate = asyncio.Event()

    async def slow_tr(text, is_own):
        await gate.wait()
        return "TR " + text

    view.translate_message = slow_tr

    async def main():
        b = await view._render_live(_live("first", _NOON - 3 * 3600))
        await asyncio.sleep(0)
        view.translate_message = None              # the rest render untranslated
        for i in range(200):                       # alternate senders: 200 new blocks
            await view._render_live(_live(f"l{i}", _NOON + i * 10,
                                          name="Robin" if i % 2 else "Alex"))
        assert b.get("_trimmed") is True and b not in view._hist_blocks
        gate.set()
        await asyncio.gather(*view._tr_tasks)
        assert not b["_ctrl"].spans                # never painted into a dead control
        assert len(_blocks(col)) == 200

    asyncio.run(main())


def test_ctrl_update_only_touches_mounted_controls():
    class _Ctrl:
        page = None
        calls = 0

        def update(self):
            type(self).calls += 1

    c = _Ctrl()
    sb.SteamBridgeView._ctrl_update(c)
    assert _Ctrl.calls == 0                        # detached: no update pushed
    c.page = object()
    sb.SteamBridgeView._ctrl_update(c)
    assert _Ctrl.calls == 1


def test_live_append_flushes_only_its_column(view):
    class _Page:
        def __init__(self):
            self.targets: list = []

        def update(self, *ctrls):
            self.targets.append(ctrls)

        def run_task(self, fn, *a):
            pass

    view._active = 1
    col = view._ensure_msg_list(1)
    pg = _Page()
    view.page = pg
    col.page = pg                                  # the column is mounted
    b = asyncio.run(view._render_live(_live("x", _NOON)))
    # following: ONE column walk (scroll_to's own update carries the new
    # block) - never a bare page.update(), never a second flush
    assert pg.targets == [(col,)]

    # reading up: a grouped same-sender line flushes just that block's line column
    view._following = False
    inner = b["_col"]
    inner.page = pg                                # mounted by the flush above
    pg.targets.clear()
    b2 = asyncio.run(view._render_live(_live("y", _NOON + 5)))
    assert b2["_col"] is inner and len(_blocks(col)) == 1
    assert pg.targets == [(inner,)]
    pg.targets.clear()
    asyncio.run(view._render_live(_live("z", _NOON + 6, name="Robin")))   # a new block
    assert pg.targets == [(col,)]
    view.page = None
    col.page = None
    inner.page = None


# ── protocol tolerance: hello handshake ──────────────────────────────────────
def test_hello_is_ignored_and_logged_once(view, caplog):
    with caplog.at_level(logging.DEBUG, logger="puripuly_heart.steam_view"):
        asyncio.run(view._handle({"ev": "hello", "app": "puripuly-steam",
                                  "proto": 1, "pid": 4242}))
        asyncio.run(view._handle({"ev": "hello", "app": "puripuly-steam",
                                  "proto": 1, "pid": 4242}))
    assert view._hello_seen is True
    assert view._tabs == [] and view._friends == {} and view._notices == []
    hellos = [r for r in caplog.records if "helper hello" in r.getMessage()]
    assert len(hellos) == 1 and hellos[0].levelno == logging.DEBUG


def test_missing_hello_warns_once_and_keeps_working(view, monkeypatch, caplog):
    async def _nosleep(*a, **k):
        return None

    monkeypatch.setattr(sb.asyncio, "sleep", _nosleep)
    view._reader = object()                        # the connection is up
    with caplog.at_level(logging.WARNING, logger="puripuly_heart.steam_view"):
        asyncio.run(view._hello_watch())
        asyncio.run(view._hello_watch())
    warns = [r for r in caplog.records if "sent no hello" in r.getMessage()]
    assert len(warns) == 1 and warns[0].levelno == logging.WARNING
    assert "8791" in warns[0].getMessage()

    caplog.clear()
    view._nohello_warned = False
    view._hello_seen = True                        # a newer helper said hello
    with caplog.at_level(logging.WARNING, logger="puripuly_heart.steam_view"):
        asyncio.run(view._hello_watch())
    assert not [r for r in caplog.records if "sent no hello" in r.getMessage()]


def test_read_loop_logs_an_oversized_line_loudly(view, caplog):
    class _Reader:
        def __aiter__(self):
            return self

        async def __anext__(self):
            raise ValueError("Separator is not found, and chunk exceed the limit")

    view._reader = _Reader()
    view._started = False                          # no reconnect loop afterwards
    with caplog.at_level(logging.ERROR, logger="puripuly_heart.steam_view"):
        asyncio.run(view._read_loop())
    errs = [r for r in caplog.records if "exceeded the stream limit" in r.getMessage()]
    assert len(errs) == 1 and errs[0].exc_info is not None


# ── instant tab open / reopen (item 8) ──────────────────────────────────────
class _LoopPage:
    """A page for the open/anchor flows: run_task schedules on the running
    loop, updates are recorded (never sent), nothing else is needed."""
    def __init__(self):
        self.updates: list = []
        self.loop = None
        self.overlay: list = []

    def run_task(self, fn, *a):
        return asyncio.get_running_loop().create_task(fn(*a))

    def update(self, *ctrls):
        self.updates.append(ctrls)


def _hist(n: int, *, start: int = _NOON - 3600, step: int = 60, name: str = "Alex") -> list:
    """n history messages one minute apart - one block each once coalesced."""
    return [_live(f"h{i}", start + i * step, name=name) for i in range(n)]


def _count_repaints(view) -> list:
    calls: list = []
    orig = view._render_history_body

    async def wrapped(blocks, keep, seq):
        calls.append(len(blocks))
        return await orig(blocks, keep, seq)

    view._render_history_body = wrapped
    return calls


async def _open_with_history(view, acct: int, msgs: list):
    await view._open(acct)
    await view._handle({"ev": "history", "acct": acct, "messages": msgs})
    await asyncio.sleep(0.15)                  # past the first anchor step
    return view._ensure_msg_list(acct)


def _evict(view, acct: int) -> None:
    """Drop a parked column the way the LRU eviction does (forces the
    cache-rebuild path on the next open)."""
    ent = view._msg_lists.pop(acct)
    view._messages_stack.controls.remove(ent[0])
    view._parked_at.pop(acct, None)


async def _wait_visible(col, limit: float = 1.5) -> float:
    t0 = time.perf_counter()
    while col.opacity != 1 and time.perf_counter() - t0 < limit:
        await asyncio.sleep(0.005)
    return time.perf_counter() - t0


def test_reopen_paints_from_cache_and_lifts_the_curtain_after_the_first_pin(view):
    view.page = _LoopPage()
    repaints = _count_repaints(view)

    async def main():
        col = await _open_with_history(view, 1, _hist(5))
        assert repaints == [5] and col.opacity == 1
        view._close_tab(1)
        _evict(view, 1)
        await view._open(1)
        col2 = view._ensure_msg_list(1)
        assert col2 is not col and len(_blocks(col2)) == 5
        assert col2.opacity == 0                # curtain down at the open
        assert col2.controls[0].data == "loadmore"
        waited = await _wait_visible(col2)
        assert col2.opacity == 1
        # r635 kept the curtain for the whole settle loop (>= 0.7 s here);
        # it lifts one frame after the first pin now
        assert waited < 0.4
        assert view._render_fp[1] == view._hist_fp(view._chat_cache[1])
        assert repaints == [5]                  # the reopen never repainted

    asyncio.run(main())
    view.page = None


def test_identical_history_after_a_cache_rebuild_fp_skips(view):
    view.page = _LoopPage()
    repaints = _count_repaints(view)
    msgs = _hist(6)

    async def main():
        await _open_with_history(view, 1, msgs)
        view._close_tab(1)
        _evict(view, 1)
        await view._open(1)                     # cache rebuild
        col = view._ensure_msg_list(1)
        ctrls = list(col.controls)
        await view._handle({"ev": "history", "acct": 1, "messages": msgs})
        assert repaints == [6]                  # only the very first render
        assert len(col.controls) == len(ctrls)  # not cleared, not rebuilt
        assert all(a is b for a, b in zip(col.controls, ctrls))

    asyncio.run(main())
    view.page = None


def test_trailing_only_history_appends_instead_of_repainting(view):
    view.page = _LoopPage()
    repaints = _count_repaints(view)
    msgs = _hist(5)
    newer = [_live("later 1", _NOON + 10, name="Robin"),
             _live("later 2", _NOON + 400, name="Alex")]

    async def main():
        await _open_with_history(view, 1, msgs)
        view._close_tab(1)
        _evict(view, 1)
        await view._open(1)
        col = view._ensure_msg_list(1)
        old = _blocks(col)
        await view._handle({"ev": "history", "acct": 1, "messages": msgs + newer})
        assert repaints == [5]
        blocks = _blocks(col)
        assert len(blocks) == 7
        assert all(a is b for a, b in zip(blocks, old))     # the 5 kept their controls
        assert [b.data["text"] for b in blocks[5:]] == ["later 1", "later 2"]
        assert len(view._hist_blocks) == 7
        assert view._chat_cache[1] is view._hist_blocks
        assert view._render_fp[1] == view._hist_fp(view._hist_blocks)
        # and the same history again is a no-op
        await view._handle({"ev": "history", "acct": 1, "messages": msgs + newer})
        assert repaints == [5] and len(_blocks(col)) == 7

    asyncio.run(main())
    view.page = None


def test_a_different_middle_repaints(view):
    view.page = _LoopPage()
    repaints = _count_repaints(view)
    msgs = _hist(5)

    async def main():
        await _open_with_history(view, 1, msgs)
        view._close_tab(1)
        _evict(view, 1)
        await view._open(1)
        edited = [dict(m) for m in msgs]
        edited[2]["text"] = "edited"
        await view._handle({"ev": "history", "acct": 1, "messages": edited})
        assert repaints == [5, 5]
        assert [b.data["text"] for b in _blocks(view._ensure_msg_list(1))][2] == "edited"
        # fewer blocks: also a repaint (the oldest goes - a dropped NEWEST
        # block would be carried back from the cache, as before r636)
        fewer = msgs[1:]
        await view._handle({"ev": "history", "acct": 1, "messages": fewer})
        assert repaints == [5, 5, 4]

    asyncio.run(main())
    view.page = None


def test_live_appends_extend_the_fingerprint_so_a_reconnect_history_fp_skips(view):
    view.page = _LoopPage()
    repaints = _count_repaints(view)
    msgs = _hist(4)
    live = [_live("now 1", _NOON + 5, name="Robin"), _live("now 2", _NOON + 300, name="Alex")]

    async def main():
        col = await _open_with_history(view, 1, msgs)
        for m in live:
            await view._handle({"ev": "inbound", "acct": 1, "message": m})
        assert len(_blocks(col)) == 6
        assert view._render_fp[1] == view._hist_fp(view._hist_blocks)
        ctrls = list(col.controls)
        # the helper reconnects and re-sends the history, now with both lines
        await view._handle({"ev": "history", "acct": 1, "messages": msgs + live})
        assert repaints == [4]
        assert len(col.controls) == len(ctrls)
        assert all(a is b for a, b in zip(col.controls, ctrls))

    asyncio.run(main())
    view.page = None


def test_closed_tab_column_stays_parked_and_reopens_in_place(view):
    view.page = _LoopPage()
    repaints = _count_repaints(view)

    async def main():
        col = await _open_with_history(view, 1, _hist(5))
        wrap = view._msg_lists[1][0]
        view._close_tab(1)
        assert 1 not in view._tabs and view._active is None
        assert view._msg_lists[1][1] is col and wrap in view._messages_stack.controls
        assert 1 in view._parked_at
        blocks = _blocks(col)
        await view._open(1)
        assert view._ensure_msg_list(1) is col
        assert col.opacity == 0                 # a closed tab forgot its spot: re-pin
        assert len(_blocks(col)) == 5
        assert all(a is b for a, b in zip(_blocks(col), blocks))
        assert 1 not in view._parked_at
        waited = await _wait_visible(col)
        assert col.opacity == 1 and waited < 0.4
        assert repaints == [5]

    asyncio.run(main())
    view.page = None


def test_parked_reopen_appends_the_lines_swept_in_while_closed(view):
    view.page = _LoopPage()
    repaints = _count_repaints(view)
    msgs = _hist(5)
    swept = [_live("while away 1", _NOON + 10, name="Robin"),
             _live("while away 2", _NOON + 500, name="Alex")]

    async def main():
        col = await _open_with_history(view, 1, msgs)
        old = _blocks(col)
        view._close_tab(1)
        for m in swept:
            await view._handle({"ev": "inbound", "acct": 1, "message": m})
        assert len(view._chat_cache[1]) == 7 and len(_blocks(col)) == 5
        assert 1 in view._tabs                  # surfaced as a background tab
        await view._open(1)
        blocks = _blocks(col)
        assert len(blocks) == 7
        assert all(a is b for a, b in zip(blocks, old))
        assert [b.data["text"] for b in blocks[5:]] == ["while away 1", "while away 2"]
        assert len(view._hist_blocks) == 7 and view._chat_cache[1] is view._hist_blocks
        assert repaints == [5]
        await view._handle({"ev": "history", "acct": 1, "messages": msgs + swept})
        assert repaints == [5]                  # already on screen: fp-skip

    asyncio.run(main())
    view.page = None


def test_parked_reopen_rebuilds_when_the_sweep_trimmed_the_cache(view):
    view.page = _LoopPage()

    async def main():
        col = await _open_with_history(view, 1, _hist(5))
        old = _blocks(col)
        view._close_tab(1)
        for i in range(60):                     # past 60 the sweep trims to 40 (+4 after)
            await view._handle({"ev": "inbound", "acct": 1,
                                "message": _live(f"s{i}", _NOON + i * 100,
                                                 name="Robin" if i % 2 else "Alex")})
        assert len(view._chat_cache[1]) == 44
        assert view._unrendered_tail(col, view._chat_cache[1]) is None
        await view._open(1)
        assert view._ensure_msg_list(1) is col
        blocks = _blocks(col)
        assert len(blocks) == 44
        assert not any(b in old for b in blocks)
        assert [b.data for b in blocks] == view._chat_cache[1]
        assert view._render_fp[1] == view._hist_fp(view._chat_cache[1])

    asyncio.run(main())
    view.page = None


def test_parked_columns_are_evicted_lru_beyond_six(view):
    view.page = _LoopPage()

    async def main():
        for a in range(1, 9):
            await _open_with_history(view, a, _hist(1, name=f"Friend {a}"))
        wraps = {a: view._msg_lists[a][0] for a in range(1, 9)}
        for a in range(1, 9):
            view._close_tab(a)
        parked = [a for a in view._msg_lists if a not in view._tabs]
        assert sorted(parked) == [3, 4, 5, 6, 7, 8]
        assert 1 not in view._msg_lists and 2 not in view._msg_lists
        assert wraps[1] not in view._messages_stack.controls
        assert wraps[8] in view._messages_stack.controls
        assert set(view._parked_at) == {3, 4, 5, 6, 7, 8}

    asyncio.run(main())
    view.page = None


def test_open_flushes_the_column_stack_not_the_page(view):
    view.page = pg = _LoopPage()
    view._messages_stack.page = pg            # the Stack is mounted...
    view._entry.page = pg
    col = view._ensure_msg_list(1)
    col.page = pg                             # ...and so is the chat's column
    view._chat_cache[1] = view._coalesce(_hist(3))

    async def main():
        await view._open(1)
        await asyncio.sleep(0.2)              # anchor + translation repair ran

    asyncio.run(main())
    assert () not in pg.updates               # never a bare page walk
    assert (view._messages_stack,) in pg.updates
    assert (view._entry,) in pg.updates
    assert (col,) in pg.updates               # the curtain lift / column flushes
    for c in (view._messages_stack, view._entry, col):
        c.page = None
    view.page = None
