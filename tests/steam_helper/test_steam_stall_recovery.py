"""r656: a hung page call must not freeze the Steam helper.

The helper's web session can half-die: a Steam request's promise never settles
and page.evaluate never returns. With no deadline that blocked the daemon's poll
loop forever (no inbound, presence frozen to offline, chat opens ignored, zero
CPU, nothing logged). Every page call now has a deadline; a timeout bumps
SteamPage.stall_count and the poll loop reloads the page.
"""
from __future__ import annotations

import asyncio
import importlib.util
import re
import shutil
import sys
from pathlib import Path

import pytest

_SRC = Path(__file__).resolve().parents[2] / "src" / "puripuly_heart" / "data" / "steam_bridge_src"


@pytest.fixture(scope="module")
def mods(tmp_path_factory):
    # COPIES: the daemon writes diag.log next to its own file, and the src data
    # tree is bundled into the release
    d = tmp_path_factory.mktemp("helper")
    for f in ("daemon.py", "steam_page.py"):
        shutil.copy2(_SRC / f, d / f)
    out = {}
    for name in ("steam_page", "daemon"):
        spec = importlib.util.spec_from_file_location(f"pph_stall_{name}", d / f"{name}.py")
        m = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = m
        sys.path.insert(0, str(d))
        try:
            spec.loader.exec_module(m)
        finally:
            sys.path.remove(str(d))
        out[name] = m
    return out


def test_every_page_call_goes_through_the_deadline_wrapper():
    src = (_SRC / "steam_page.py").read_text(encoding="utf-8")
    assert src.count("self._page.evaluate(") == 1            # only inside _ev
    assert "asyncio.wait_for(self._page.evaluate(" in src
    assert len(re.findall(r"self\._ev\(", src)) >= 36


class _HungPage:
    async def evaluate(self, js, *args):
        await asyncio.Event().wait()                         # a promise that never settles


class _OkPage:
    async def evaluate(self, js, *args):
        return {"ok": True}


async def test_ev_times_out_and_counts_a_stall(mods):
    sp = mods["steam_page"].SteamPage.__new__(mods["steam_page"].SteamPage)
    sp._page = _HungPage()
    with pytest.raises(RuntimeError, match="timed out"):
        await sp._ev("() => 1", timeout=0.05)
    assert sp.stall_count == 1
    sp._page = _OkPage()
    assert await sp._ev("() => 1", timeout=1.0) == {"ok": True}
    assert sp.stall_count == 1


class _Steam:
    def __init__(self, reload_raises=False):
        self.reload_raises = reload_raises
        self.reloads = 0
        self.opens: list = []
        self.stall_count = 3

    async def reload(self):
        self.reloads += 1
        if self.reload_raises:
            raise RuntimeError("page call timed out after 30s")

    async def is_signed_in(self):
        return True

    async def open_conversation(self, acct):
        self.opens.append(acct)
        return True

    def is_dead(self):
        return False


def _daemon(mod, steam, monkeypatch):
    async def _no_sleep(_s):
        return None
    monkeypatch.setattr(mod.asyncio, "sleep", _no_sleep)
    d = mod.Daemon()
    d.steam, d.active = steam, 100
    d.calls: list = []

    async def _refresh():
        d.calls.append("refresh")

    async def _push(only=None):
        d.calls.append("push")

    async def _recover(why):
        d.calls.append(f"recover:{why}")
        return True

    d.refresh_list, d.push_state, d._recover_browser = _refresh, _push, _recover
    return d


async def test_stall_reloads_reopens_and_repushes(mods, monkeypatch):
    steam = _Steam()
    d = _daemon(mods["daemon"], steam, monkeypatch)
    await d._recover_stall()
    assert steam.reloads == 1 and steam.opens == [100]
    assert d.calls == ["refresh", "push"]                    # presence re-sent to the app
    assert d._reloading is False and d._seen_stalls == 3
    await d._recover_stall()                                 # within a minute: no reload storm
    assert steam.reloads == 1


async def test_failed_reload_escalates_to_browser_restart(mods, monkeypatch):
    steam = _Steam(reload_raises=True)
    d = _daemon(mods["daemon"], steam, monkeypatch)
    await d._recover_stall()
    assert d.calls == ["recover:stall"]
    assert d._reloading is False


class _HungCtx:
    async def close(self):
        await asyncio.Event().wait()


class _Pw:
    stopped = False

    async def stop(self):
        _Pw.stopped = True


async def test_close_does_not_hang_on_a_wedged_browser(mods, monkeypatch):
    sp_mod = mods["steam_page"]
    sp = sp_mod.SteamPage.__new__(sp_mod.SteamPage)
    sp._ctx, sp._pw = _HungCtx(), _Pw()
    killed: list = []

    async def _kill():
        killed.append(True)

    sp._kill_profile_browser = _kill
    real_wait_for = asyncio.wait_for

    async def _fast_wait_for(aw, timeout):
        return await real_wait_for(aw, min(timeout, 0.05))

    monkeypatch.setattr(sp_mod.asyncio, "wait_for", _fast_wait_for)
    await real_wait_for(sp.close(), 5.0)        # returns instead of hanging
    assert killed == [True] and _Pw.stopped
