"""r636: Steam helper daemon performance/hygiene (steam-helper/steam_bridge/daemon.py).

Covers: the single tick_state evaluate per poll tick (every 3rd tick only
while no chat is open), the adaptive fetch cadence (2 ticks hot / 4 ticks
idle, snapping back on typing, send and open), the own_info / emote-relist
task off the poll loop (_own_loop cadence, every-round relist while the emote
list is still empty, failed own reads ignored, quiet over reload/quit,
crash-proof supervisor), the capped favorites boot retry, diag.log rotation at
512 KB, the hello line being the first thing a new client reads, and the
port-drain helper behind stop_helper_processes.

FRIENDS_JS slimming is page JS and cannot run here; the view-facing contract
is: acct, name, avatar, state, ingame, game, flags, fav, groups, last_chat,
unread and last_seen are always present; icon, extra, extra_full, nick, real
are omitted when empty, appid when 0, extra_full only when it differs from
extra. The app view reads every optional key with .get() defaults (its one
f["appid"] sits behind an f.get("appid") guard).
"""
from __future__ import annotations

import asyncio
import importlib.util
import json
import os
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]
_DAEMON = _REPO / "steam-helper" / "steam_bridge" / "daemon.py"


@pytest.fixture(scope="module")
def daemon_mod():
    spec = importlib.util.spec_from_file_location("pph_steam_daemon_poll_under_test", _DAEMON)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(autouse=True)
def _diag_to_tmp(daemon_mod, tmp_path, monkeypatch):
    # the real _diag stays in place (the rotation test needs it) but never
    # touches the live diag.log
    monkeypatch.setattr(daemon_mod, "_DIAG", tmp_path / "diag.log")
    monkeypatch.delenv("PPH_STEAM_RECON", raising=False)


class _StopPoll(Exception):
    """raised by the fake asyncio.sleep to end poll_loop after N ticks"""


class _FastAsyncio:
    """asyncio stand-in for the daemon module: every sleep returns at once
    (recorded), the poll tick counter ends the loop after `max_ticks`."""

    def __init__(self, max_ticks: int = 0):
        self.sleeps: list = []
        self.ticks = 0
        self.max_ticks = max_ticks

    def __getattr__(self, name):
        return getattr(asyncio, name)

    async def sleep(self, delay, *a, **k):
        self.sleeps.append(delay)
        if delay == 1.3:                      # the poll loop's tick sleep
            self.ticks += 1
            if self.max_ticks and self.ticks > self.max_ticks:
                raise _StopPoll()
        await asyncio.sleep(0)


class _TickSteam:
    """Fake SteamPage for poll_loop: records one tick_state per tick and the
    tick number of every active-chat fetch; the three legacy per-tick methods
    must never be called any more."""

    def __init__(self, typing_from_tick: int | None = None):
        self.tick_calls: list = []
        self.legacy_calls: list = []
        self.fetches: list = []
        self.typing_from_tick = typing_from_tick

    def is_dead(self):
        return False

    async def tick_state(self, acct, reactivate=False):
        n = len(self.tick_calls) + 1
        self.tick_calls.append((n, acct, reactivate))
        typing = self.typing_from_tick is not None and n >= self.typing_from_tick
        return {"typing": typing, "activity": {}, "reactivated": bool(reactivate)}

    async def is_typing(self, acct):
        self.legacy_calls.append("is_typing")
        return False

    async def chat_activity(self):
        self.legacy_calls.append("chat_activity")
        return {}

    async def reactivate(self, acct):
        self.legacy_calls.append("reactivate")

    async def list_friends(self):
        return []

    async def fetch_messages(self, acct, since):
        self.fetches.append((len(self.tick_calls), acct))
        return []


def _poll_daemon(daemon_mod, steam, *, hot: bool):
    d = daemon_mod.Daemon()
    d.steam = steam
    d.own, d.own_name = 1, "You"
    d.convos = {100: {"acct": 100, "name": "Alex", "avatar": ""}}
    d.active = 100
    d._started = True
    d.signed = True
    d.clients.add(object())                   # somebody is listening
    d._hot_at = time.monotonic() if hot else time.monotonic() - 3600.0
    d.emitted: list = []

    async def _emit(obj, only=None):
        d.emitted.append(obj)

    d.emit = _emit
    return d


async def _run_ticks(daemon_mod, monkeypatch, d, ticks: int) -> _FastAsyncio:
    clock = _FastAsyncio(max_ticks=ticks)
    monkeypatch.setattr(daemon_mod, "asyncio", clock)
    with pytest.raises(_StopPoll):
        await d.poll_loop()
    return clock


# ── one evaluate per tick + adaptive cadence ───────────────────────────────

async def test_poll_loop_reads_tick_state_once_per_tick(daemon_mod, monkeypatch):
    steam = _TickSteam()
    d = _poll_daemon(daemon_mod, steam, hot=True)
    await _run_ticks(daemon_mod, monkeypatch, d, 12)
    assert len(steam.tick_calls) == 12
    assert steam.legacy_calls == []
    # the keep-warm nudge rides along every 3rd tick, for the active chat
    assert [n for n, _a, react in steam.tick_calls if react] == [3, 6, 9, 12]
    assert all(a == 100 for _n, a, _r in steam.tick_calls)


async def test_fetch_every_2_ticks_while_hot(daemon_mod, monkeypatch):
    steam = _TickSteam()
    d = _poll_daemon(daemon_mod, steam, hot=True)     # inbound/send/open < 120 s ago
    await _run_ticks(daemon_mod, monkeypatch, d, 24)
    assert [t for t, _a in steam.fetches] == list(range(2, 25, 2))


async def test_fetch_every_4_ticks_when_idle(daemon_mod, monkeypatch):
    steam = _TickSteam()
    d = _poll_daemon(daemon_mod, steam, hot=False)
    await _run_ticks(daemon_mod, monkeypatch, d, 24)
    assert [t for t, _a in steam.fetches] == [4, 8, 12, 16, 20, 24]


async def test_fetch_every_2_ticks_while_typing(daemon_mod, monkeypatch):
    steam = _TickSteam(typing_from_tick=1)
    d = _poll_daemon(daemon_mod, steam, hot=False)
    await _run_ticks(daemon_mod, monkeypatch, d, 12)
    assert [t for t, _a in steam.fetches] == [2, 4, 6, 8, 10, 12]
    assert d.emitted[0]["ev"] == "typing" and d.emitted[0]["typing"] is True


async def test_cadence_snaps_back_when_typing_starts(daemon_mod, monkeypatch):
    steam = _TickSteam(typing_from_tick=13)
    d = _poll_daemon(daemon_mod, steam, hot=False)
    await _run_ticks(daemon_mod, monkeypatch, d, 24)
    # idle until tick 12, then fast from the very tick typing is first seen
    assert [t for t, _a in steam.fetches] == [4, 8, 12, 14, 16, 18, 20, 22, 24]


async def test_send_marks_the_chat_hot(daemon_mod):
    d = daemon_mod.Daemon()
    d.emitted: list = []

    async def _emit(obj, only=None):
        d.emitted.append(obj)

    class _S:
        async def send(self, acct, text):
            return True

        def is_dead(self):
            return False

    d.emit, d.steam = _emit, _S()
    d._hot_at = 0.0
    await d.do_send(100, "hi", "sid")
    assert time.monotonic() - d._hot_at < 5.0
    assert [e["ev"] for e in d.emitted] == ["sent"]


async def test_open_marks_the_chat_hot_before_the_history_emit(daemon_mod):
    d = daemon_mod.Daemon()
    d.own = 1
    d.emitted: list = []

    async def _emit(obj, only=None):
        # record what _hot_at read at the moment of each emit
        d.emitted.append((obj["ev"], d._hot_at))

    class _S:
        async def open_conversation(self, acct):
            return True

        async def read_messages(self, acct):
            return [{"from": acct, "text": "hi", "ts": 1, "ordinal": 0}]

        async def fetch_messages(self, acct, since):
            return []

    d.emit, d.steam = _emit, _S()
    d._hot_at = 0.0
    await d.do_open(100)
    assert [ev for ev, _ in d.emitted] == ["history", "opened"]
    hot_at_history = d.emitted[0][1]
    assert time.monotonic() - hot_at_history < 5.0    # already hot when history went out
    assert d.active == 100 and d._typing is False


async def test_no_open_chat_reads_tick_state_every_3rd_tick_only(daemon_mod, monkeypatch):
    # without an active chat nothing consumes the typing flag; only the
    # sweep's activity clocks (every 3rd tick) — the other ticks skip the read
    steam = _TickSteam()
    d = _poll_daemon(daemon_mod, steam, hot=False)
    d.active = None
    await _run_ticks(daemon_mod, monkeypatch, d, 12)
    assert len(steam.tick_calls) == 4                 # ticks 3, 6, 9, 12
    assert all(a == 0 and react is False for _n, a, react in steam.tick_calls)
    assert steam.fetches == [] and steam.legacy_calls == []


# ── own_info + emote relist off the poll task (_own_loop) ─────────────────

class _OwnClock(_FastAsyncio):
    """ends _own_loop after `max_rounds` of its 30 s sleep"""

    def __init__(self, max_rounds: int):
        super().__init__()
        self.rounds = 0
        self.max_rounds = max_rounds

    async def sleep(self, delay, *a, **k):
        if delay == 30.0:
            self.rounds += 1
            if self.rounds > self.max_rounds:
                raise _StopPoll()
        await super().sleep(delay, *a, **k)


class _OwnSteam:
    def __init__(self):
        self.own_calls = 0
        self.emote_calls: list = []
        self.sticker_calls = 0

    def is_dead(self):
        return False

    async def own_info(self):
        self.own_calls += 1
        return {"acct": 1, "name": "You", "avatar": "", "state": 1, "invites": 0}

    async def list_emoticons(self, wait=True):
        self.emote_calls.append(wait)
        return [{"name": "x", "app": ""}]

    async def list_stickers(self):
        self.sticker_calls += 1
        return []


def _own_daemon(daemon_mod, steam):
    d = daemon_mod.Daemon()
    d.steam = steam
    d.own, d.own_name = 1, "You"
    d._started, d.signed = True, True
    d.emoticons = [{"name": "x", "app": ""}]          # listed once at boot
    d.emitted: list = []

    async def _emit(obj, only=None):
        d.emitted.append(obj)

    d.emit = _emit
    return d


async def _run_own_rounds(daemon_mod, monkeypatch, d, rounds: int) -> _OwnClock:
    clock = _OwnClock(rounds)
    monkeypatch.setattr(daemon_mod, "asyncio", clock)
    with pytest.raises(_StopPoll):
        await d._own_loop()
    return clock


async def test_own_loop_refreshes_each_round_and_relists_every_20th(daemon_mod, monkeypatch):
    steam = _OwnSteam()
    d = _own_daemon(daemon_mod, steam)
    await _run_own_rounds(daemon_mod, monkeypatch, d, 40)
    assert steam.own_calls == 40                      # one own_info per 30 s round
    assert steam.emote_calls == [False, False]        # rounds 20 + 40, no settle loop
    assert steam.sticker_calls == 2
    # "own" once (first signature), never again while nothing changed
    assert [e["ev"] for e in d.emitted] == ["own"]


async def test_own_loop_is_quiet_over_reload_quit_and_before_sign_in(daemon_mod, monkeypatch):
    for attr, val in (("_reloading", True), ("_quitting", True), ("signed", False)):
        steam = _OwnSteam()
        d = _own_daemon(daemon_mod, steam)
        setattr(d, attr, val)
        await _run_own_rounds(daemon_mod, monkeypatch, d, 5)
        assert steam.own_calls == 0 and steam.emote_calls == [], attr
        assert d.emitted == [], attr


async def test_own_loop_relist_settles_only_while_emote_list_is_empty(daemon_mod, monkeypatch):
    steam = _OwnSteam()
    d = _own_daemon(daemon_mod, steam)
    d.emoticons = []
    await _run_own_rounds(daemon_mod, monkeypatch, d, 20)
    # empty at boot -> relisted on the very first round (settle loop on) and
    # filled; round 20 is the regular cadence with the settle loop off
    assert steam.emote_calls == [True, False]
    assert d.emoticons == [{"name": "x", "app": ""}]
    assert [e["ev"] for e in d.emitted] == ["own", "own"]   # refresh + RELIST push


class _LateEmoteSteam(_OwnSteam):
    """emote list stays empty for the first `empty_calls` list_emoticons calls"""

    def __init__(self, empty_calls: int):
        super().__init__()
        self.empty_calls = empty_calls

    async def list_emoticons(self, wait=True):
        self.emote_calls.append(wait)
        if len(self.emote_calls) <= self.empty_calls:
            return []
        return [{"name": "x", "app": ""}]


async def test_own_loop_relists_every_round_until_the_emote_list_arrives(daemon_mod, monkeypatch):
    steam = _LateEmoteSteam(empty_calls=2)
    d = _own_daemon(daemon_mod, steam)
    d.emoticons = []
    await _run_own_rounds(daemon_mod, monkeypatch, d, 20)
    # rounds 1, 2 empty (retry next round), round 3 fills, then back to round 20
    assert steam.emote_calls == [True, True, True, False]
    assert d.emoticons == [{"name": "x", "app": ""}]
    assert [e["ev"] for e in d.emitted] == ["own", "own"]   # refresh + ONE relist push


async def test_own_loop_empty_emote_retry_is_bounded(daemon_mod, monkeypatch):
    steam = _LateEmoteSteam(empty_calls=10 ** 6)      # an account with no emotes at all
    d = _own_daemon(daemon_mod, steam)
    d.emoticons = []
    await _run_own_rounds(daemon_mod, monkeypatch, d, 40)
    # rounds 1..10 every round, then only the regular 20 / 40 cadence
    assert steam.emote_calls == [True] * 12
    assert d.emoticons == []
    assert [e["ev"] for e in d.emitted] == ["own"]    # refresh only, nothing to relist


class _FlakyOwnSteam(_OwnSteam):
    """scripted own_info results, in order"""

    def __init__(self, results: list):
        super().__init__()
        self.results = list(results)

    async def own_info(self):
        self.own_calls += 1
        return self.results.pop(0)


_GOOD_AWAY_INGAME = {"acct": 1, "name": "You", "avatar": "", "state": 3,
                     "ingame": True, "game": "A game", "invites": 0}


async def test_refresh_own_ignores_a_failed_read(daemon_mod):
    steam = _FlakyOwnSteam([
        _GOOD_AWAY_INGAME,
        {},                                           # evaluate raised (page dead)
        {"acct": 0, "name": "", "avatar": "", "state": 0},   # the JS catch path
    ])
    d = _own_daemon(daemon_mod, steam)
    for _ in range(3):
        await d._refresh_own()
    assert steam.own_calls == 3
    assert [e["ev"] for e in d.emitted] == ["own"]    # only the real read published
    assert d.emitted[0]["state"] == 3 and d.emitted[0]["game"] == "A game"
    # the two failed reads left the last good state alone
    assert (d.own_state, d.own_ingame, d.own_game) == (3, True, "A game")


async def test_refresh_own_ignores_a_read_that_landed_under_a_reload(daemon_mod):
    steam = _FlakyOwnSteam([_GOOD_AWAY_INGAME,
                            {"acct": 1, "name": "You", "avatar": "", "state": 1, "invites": 0}])
    d = _own_daemon(daemon_mod, steam)
    await d._refresh_own()
    d._reloading = True                               # a page reload / recovery began
    await d._refresh_own()
    assert [e["ev"] for e in d.emitted] == ["own"]
    assert (d.own_state, d.own_ingame, d.own_game) == (3, True, "A game")


async def test_own_supervisor_restarts_after_a_crash(daemon_mod, monkeypatch):
    clock = _FastAsyncio()
    monkeypatch.setattr(daemon_mod, "asyncio", clock)
    d = daemon_mod.Daemon()
    runs: list = []

    async def _loop():
        runs.append(1)
        if len(runs) == 1:
            raise RuntimeError("boom")
        raise asyncio.CancelledError()

    d._own_loop = _loop
    with pytest.raises(asyncio.CancelledError):
        await d._own_supervisor()
    assert runs == [1, 1]                             # logged, slept, restarted
    assert 5.0 in clock.sleeps


# ── favorites boot retry capped ────────────────────────────────────────────

class _BootSteam:
    def __init__(self, fav_on_call: int | None):
        self.list_calls = 0
        self.fav_on_call = fav_on_call

    async def start(self, mode="hidden"):
        pass

    async def is_signed_in(self):
        return True

    async def own_info(self):
        return {"acct": 1, "name": "You", "avatar": ""}

    async def list_friends(self):
        self.list_calls += 1
        fav = self.fav_on_call is not None and self.list_calls >= self.fav_on_call
        return [{"acct": 100, "name": "Alex", "avatar": "", "fav": fav}]

    async def list_emoticons(self, wait=True):
        return []

    async def list_stickers(self):
        return []

    async def list_effects(self):
        return []

    async def preload_recent(self, n=20):
        pass


def _boot_daemon(daemon_mod, steam):
    d = daemon_mod.Daemon()
    d.steam = steam

    async def _emit(obj, only=None):
        pass

    d.emit = _emit
    return d


async def test_favorites_boot_retry_is_capped_to_one(daemon_mod, monkeypatch):
    clock = _FastAsyncio()
    monkeypatch.setattr(daemon_mod, "asyncio", clock)
    steam = _BootSteam(fav_on_call=None)       # favorites never arrive
    d = _boot_daemon(daemon_mod, steam)
    await d.ensure_started()
    await asyncio.sleep(0)                    # let the preload task finish
    assert d._started is True
    assert steam.list_calls == 2              # initial + ONE retry (was up to 7)
    assert clock.sleeps.count(1.0) == 1


async def test_favorites_present_first_time_means_no_retry(daemon_mod, monkeypatch):
    clock = _FastAsyncio()
    monkeypatch.setattr(daemon_mod, "asyncio", clock)
    steam = _BootSteam(fav_on_call=1)
    d = _boot_daemon(daemon_mod, steam)
    await d.ensure_started()
    await asyncio.sleep(0)
    assert steam.list_calls == 1
    assert 1.0 not in clock.sleeps


# ── diag.log rotation ──────────────────────────────────────────────────────

def test_diag_rotates_past_512kb_keeping_one_generation(daemon_mod, tmp_path):
    log = tmp_path / "diag.log"
    gen1 = tmp_path / "diag.log.1"
    log.write_text("A" * (512 * 1024 + 1), encoding="utf-8")
    daemon_mod._diag("first-after-rotate")
    assert gen1.exists() and gen1.read_text(encoding="utf-8").startswith("AAAA")
    lines = log.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1 and lines[0].endswith("first-after-rotate")
    # below the cap: appends, no rotation
    daemon_mod._diag("second")
    assert len(log.read_text(encoding="utf-8").splitlines()) == 2
    # a second rotation replaces the single kept generation
    with open(log, "a", encoding="utf-8") as f:
        f.write("B" * (512 * 1024 + 1))
    daemon_mod._diag("third")
    assert gen1.read_text(encoding="utf-8").endswith("B" * 8)
    assert len(log.read_text(encoding="utf-8").splitlines()) == 1


# ── hello line first on connect ────────────────────────────────────────────

class _FakeWriter:
    def __init__(self):
        self.buf = bytearray()

    def write(self, data):
        self.buf += data

    async def drain(self):
        pass

    def close(self):
        pass


class _FakeReader:
    def __aiter__(self):
        return self

    async def __anext__(self):
        raise StopAsyncIteration


async def test_hello_line_is_the_first_thing_a_client_reads(daemon_mod):
    d = daemon_mod.Daemon()
    d._started = True                         # no browser launch in tests
    d.signed = True
    w = _FakeWriter()
    await d.handle(_FakeReader(), w)
    lines = [json.loads(x) for x in bytes(w.buf).decode("utf-8").splitlines()]
    assert lines[0] == {"ev": "hello", "app": "puripuly-steam", "proto": 1,
                        "pid": os.getpid()}
    assert [x["ev"] for x in lines[1:]] == ["status", "own", "friends"]
    assert w not in d.clients                 # cleaned up on disconnect


# ── stop_helper_processes: port drain (the kill itself is never run here) ──

def test_wait_port_closed_returns_at_once_when_nothing_listens():
    from puripuly_heart.core.steam_module import _wait_port_closed

    probe = socket.socket()
    probe.bind(("127.0.0.1", 0))
    port = probe.getsockname()[1]
    probe.close()                             # free ephemeral port, nobody listens
    t0 = time.monotonic()
    assert _wait_port_closed(port, timeout_s=2.0) is True
    assert time.monotonic() - t0 < 1.0


def test_wait_port_closed_waits_for_listener_to_go_away():
    from puripuly_heart.core.steam_module import _wait_port_closed

    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(4)
    port = srv.getsockname()[1]
    threading.Timer(0.6, srv.close).start()
    t0 = time.monotonic()
    assert _wait_port_closed(port, timeout_s=5.0) is True
    assert 0.4 < time.monotonic() - t0 < 4.0


def test_wait_port_closed_gives_up_after_timeout():
    from puripuly_heart.core.steam_module import _wait_port_closed

    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(4)
    try:
        assert _wait_port_closed(srv.getsockname()[1], timeout_s=0.6) is False
    finally:
        srv.close()


# ── stop_helper_processes: process drain (psutil, read-only; nothing is
#    ever killed here — the children below exit on their own) ──────────────

_DRAIN_MARK = "pph-r636-drain-marker"


def _marker_child(seconds: float) -> subprocess.Popen:
    """a python*.exe whose command line carries the test marker (never one of
    the real helper patterns, so a live helper on this PC is not a match)"""
    return subprocess.Popen(
        [sys.executable, "-c", f"import time; time.sleep({seconds})", _DRAIN_MARK],
        creationflags=(0x08000000 if sys.platform.startswith("win") else 0))


def _wait_visible(alive, needles) -> None:
    for _ in range(60):
        if alive(needles):
            return
        time.sleep(0.05)
    pytest.fail("child never showed up in the process table")


def test_cmdline_matches_uses_the_kill_patterns_in_order():
    from puripuly_heart.core.steam_module import _cmdline_matches

    assert _cmdline_matches("pythonw3.11.exe x/steam_bridge/daemon.py")
    assert _cmdline_matches("msedge.exe --type=gpu-process --user-data-dir=y/steamprobe-profile")
    assert not _cmdline_matches("python.exe daemon.py steam_bridge")    # wrong order
    assert not _cmdline_matches("python.exe -m pytest tests/steam_helper")


def test_wait_helper_gone_returns_at_once_without_a_matching_process():
    from puripuly_heart.core.steam_module import _wait_helper_gone

    t0 = time.monotonic()
    assert _wait_helper_gone(timeout_s=3.0, needles=(("no-such-" + _DRAIN_MARK,),)) is True
    assert time.monotonic() - t0 < 2.5


def test_wait_helper_gone_waits_for_a_matching_python_process_to_exit():
    from puripuly_heart.core.steam_module import _helper_processes_alive, _wait_helper_gone

    needles = ((_DRAIN_MARK,),)
    child = _marker_child(1.0)
    try:
        _wait_visible(_helper_processes_alive, needles)
        t0 = time.monotonic()
        assert _wait_helper_gone(timeout_s=6.0, needles=needles) is True
        assert time.monotonic() - t0 < 5.0
        child.wait(timeout=2.0)                   # it really had exited
    finally:
        child.kill()
        child.wait()


def test_wait_helper_gone_gives_up_after_timeout():
    from puripuly_heart.core.steam_module import _helper_processes_alive, _wait_helper_gone

    needles = ((_DRAIN_MARK,),)
    child = _marker_child(30.0)
    try:
        _wait_visible(_helper_processes_alive, needles)
        assert _wait_helper_gone(timeout_s=0.6, needles=needles) is False
    finally:
        child.kill()
        child.wait()


# ── do_open: the server fetch no longer waits behind the local retry loop ──

class _OpenSteam:
    """fake SteamPage for do_open: scripted local array + server window,
    counts the local reads and records the fetch window"""

    def __init__(self, local, server, fetch_raises: bool = False):
        self.local, self.server = list(local), list(server)
        self.fetch_raises = fetch_raises
        self.reads = 0
        self.fetches: list = []

    async def open_conversation(self, acct):
        return True

    async def read_messages(self, acct):
        self.reads += 1
        return list(self.local)

    async def fetch_messages(self, acct, since):
        self.fetches.append((acct, since))
        if self.fetch_raises:
            raise RuntimeError("Target closed")
        return list(self.server)


def _msg(ts: int, frm: int, text: str, ordinal: int = 0) -> dict:
    return {"ts": ts, "from": frm, "text": text, "ordinal": ordinal}


async def _open(daemon_mod, monkeypatch, steam):
    clock = _FastAsyncio()
    monkeypatch.setattr(daemon_mod, "asyncio", clock)
    d = daemon_mod.Daemon()
    d.steam = steam
    d.own, d.own_name = 1, "You"
    d.convos = {100: {"acct": 100, "name": "Alex", "avatar": ""}}
    d.emitted: list = []

    async def _emit(obj, only=None):
        d.emitted.append(obj)

    d.emit = _emit
    await d.do_open(100)
    return d, clock


async def test_open_with_empty_local_array_fetches_the_server_at_once(daemon_mod, monkeypatch):
    server = [_msg(200, 100, "second"), _msg(100, 100, "first")]
    steam = _OpenSteam(local=[], server=server)
    d, clock = await _open(daemon_mod, monkeypatch, steam)
    assert steam.reads == 1                       # ONE local read, no retry loop
    assert 0.4 not in clock.sleeps                # the old 6 x 0.4 s wait is gone
    assert len(steam.fetches) == 1
    acct, since = steam.fetches[0]
    assert acct == 100 and abs(since - (int(time.time()) - 48 * 3600)) <= 2
    assert [e["ev"] for e in d.emitted] == ["history", "opened"]
    hist = d.emitted[0]["messages"]
    assert [m["text"] for m in hist] == ["first", "second"]     # sorted by ts
    assert d._last_ts == 200 and d._emitted[100] == 200 and d.seen_count == 2
    assert {(100, 100, 100, "first"), (100, 200, 100, "second")} <= d._seen_keys
    assert d.emitted[1] == {"ev": "opened", "acct": 100, "ok": True}


async def test_open_retries_the_local_read_only_when_the_server_is_empty_too(daemon_mod, monkeypatch):
    steam = _OpenSteam(local=[], server=[])
    d, clock = await _open(daemon_mod, monkeypatch, steam)
    assert steam.reads == 3                       # 1 + 2 retries (was 6)
    assert clock.sleeps.count(0.4) == 2
    assert len(steam.fetches) == 1
    assert [e["ev"] for e in d.emitted] == ["history", "opened"]
    assert d.emitted[0]["messages"] == [] and d._last_ts == 0


async def test_open_populated_local_array_reads_once_and_merges_the_server(daemon_mod, monkeypatch):
    local = [_msg(10, 100, "a"), _msg(20, 1, "b")]
    server = [_msg(20, 1, "b"), _msg(15, 100, "c")]   # b overlaps, c is new
    steam = _OpenSteam(local=local, server=server)
    d, clock = await _open(daemon_mod, monkeypatch, steam)
    assert steam.reads == 1 and 0.4 not in clock.sleeps
    hist = d.emitted[0]["messages"]
    assert [m["text"] for m in hist] == ["a", "c", "b"]        # deduped + sorted
    assert [m["from_me"] for m in hist] == [False, False, True]
    assert d._last_ts == 20 and d.seen_count == 3


async def test_open_failing_server_fetch_falls_back_to_the_local_retry(daemon_mod, monkeypatch):
    steam = _OpenSteam(local=[], server=[], fetch_raises=True)
    d, clock = await _open(daemon_mod, monkeypatch, steam)
    assert steam.reads == 3 and clock.sleeps.count(0.4) == 2
    assert [e["ev"] for e in d.emitted] == ["history", "opened"]
    assert d.emitted[1] == {"ev": "opened", "acct": 100, "ok": True}
