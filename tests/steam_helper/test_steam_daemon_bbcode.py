"""r635: unit tests for the Steam helper daemon (steam-helper/steam_bridge/daemon.py).

The daemon is a separate 3.11-compatible process; its module imports nothing
from Playwright at import time, so it is loaded straight from its path here.
Covers: [video] BBCode -> link line, account-scoped dedup keys, _shape(acct),
the "sent" event shape (acct + sid) and the zombie-browser recovery guards.
"""
from __future__ import annotations

import asyncio
import importlib.util
import sys
import time
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]
_DAEMON = _REPO / "steam-helper" / "steam_bridge" / "daemon.py"

_VID = "https://example.test/clip.mp4"
# an image-host URL WITHOUT a file extension: _IMGHOST_RE folds it into the
# image list unless the video placeholder keeps it out of reach
_CDN_VID = "https://images.steamusercontent.com/ugc/1234/ABCD/"
_IMG = "https://images.steamusercontent.com/ugc/1/2/"


@pytest.fixture(scope="module")
def daemon_mod():
    spec = importlib.util.spec_from_file_location("pph_steam_daemon_under_test", _DAEMON)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    mod._diag = lambda *_a, **_k: None   # never append to the live diag.log
    return mod


# ── parse_bbcode: [video] tags ──────────────────────────────────────────────

def test_video_quoted_src_becomes_link_line(daemon_mod):
    text, images, stickers = daemon_mod.parse_bbcode(
        f'[video src="{_VID}" width="1280" height="720"][/video]')
    assert text == _VID
    assert images == [] and stickers == []


def test_video_bare_src_becomes_link_line(daemon_mod):
    text, images, _ = daemon_mod.parse_bbcode(f"[video src={_VID} width=640]")
    assert text == _VID
    assert images == []


def test_video_src_after_other_attrs(daemon_mod):
    text, images, _ = daemon_mod.parse_bbcode(f'[video width="640" src="{_VID}"]')
    assert text == _VID
    assert images == []


def test_video_on_steamusercontent_is_not_folded_into_images(daemon_mod):
    text, images, _ = daemon_mod.parse_bbcode(
        f'[video src="{_CDN_VID}" width="1920" height="1080"][/video]')
    assert text == _CDN_VID
    assert images == []


def test_video_with_close_tag_keeps_surrounding_text(daemon_mod):
    text, images, _ = daemon_mod.parse_bbcode(f'look [video src="{_VID}"][/video] wow')
    assert text == f"look {_VID} wow"
    assert images == []


def test_video_url_alongside_tag_is_not_duplicated(daemon_mod):
    text, _, _ = daemon_mod.parse_bbcode(f'{_VID} [video src="{_VID}"][/video]')
    assert text == _VID


def test_video_without_src_reads_literal_video(daemon_mod):
    assert daemon_mod.parse_bbcode('[video width="1"][/video]')[0] == "[video]"
    assert daemon_mod.parse_bbcode("[video]")[0] == "[video]"


def test_video_empty_quoted_src_reads_literal_video(daemon_mod):
    # an empty quoted src must not fall through to the bare branch (which
    # captured the two quote characters as the "URL")
    text, images, _ = daemon_mod.parse_bbcode('[video src=""][/video]')
    assert text == "[video]"
    assert images == []


def test_video_cdn_url_alongside_tag_is_not_folded_into_images(daemon_mod):
    # pasted-media form: the extension-less CDN URL rides along as bare text
    # next to the tag -> it is the video, not a picture
    text, images, _ = daemon_mod.parse_bbcode(
        f'{_CDN_VID} [video src="{_CDN_VID}"][/video]')
    assert text == _CDN_VID
    assert images == []
    # ... while a genuine image URL next to a video still folds into images
    text, images, _ = daemon_mod.parse_bbcode(f'{_IMG} [video src="{_CDN_VID}"]')
    assert text == _CDN_VID
    assert images == [_IMG]


def test_img_src_regression_still_yields_images_and_empty_text(daemon_mod):
    text, images, _ = daemon_mod.parse_bbcode(f'[img src="{_IMG}"][/img]')
    assert text == "" and images == [_IMG]
    # pasted-image form: the URL rides along as text too -> folded, no dup
    text, images, _ = daemon_mod.parse_bbcode(f'{_IMG} [img src="{_IMG}"][/img]')
    assert text == "" and images == [_IMG]


# ── Daemon: acct-scoped shaping / dedup / send event ───────────────────────

class _FakeSteam:
    def __init__(self, send_results=(True,), dead=False):
        self._send_results = list(send_results)
        self.dead = dead
        self.sent: list = []
        self.restarts = 0

    async def send(self, acct, text):
        self.sent.append((acct, text))
        return self._send_results.pop(0) if self._send_results else True

    def is_dead(self):
        return self.dead

    async def restart(self, mode):
        self.restarts += 1
        self.dead = False

    async def is_signed_in(self):
        return True

    async def own_info(self):
        return {"acct": 1, "name": "You", "avatar": ""}

    async def list_friends(self):
        return []

    async def open_conversation(self, acct):
        return True

    def net_blocked(self):
        return False


def _daemon(daemon_mod, steam=None):
    d = daemon_mod.Daemon()
    d.steam = steam or _FakeSteam()
    d.own, d.own_name = 1, "You"
    d.convos = {100: {"acct": 100, "name": "Alex", "avatar": "av-alex"},
                200: {"acct": 200, "name": "Robin", "avatar": "av-robin"}}
    d.active = 100
    d.emitted: list = []

    async def _emit(obj, only=None):
        d.emitted.append(obj)

    d.emit = _emit
    return d


def test_shape_resolves_name_from_given_acct_not_active(daemon_mod):
    d = _daemon(daemon_mod)
    m = {"from": 2, "text": "x", "ts": 5, "ordinal": 1}
    assert d._shape(m, 200)["name"] == "Robin"
    assert d._shape(m, 200)["avatar"] == "av-robin"
    assert d._shape(m)["name"] == "Alex"            # default = active chat


async def test_sweep_deliver_keys_are_account_scoped(daemon_mod):
    d = _daemon(daemon_mod)
    m = {"from": 2, "text": "x", "ts": 10, "ordinal": 0}
    await d._sweep_deliver(200, [dict(m)], "rr")
    assert [e["acct"] for e in d.emitted] == [200]
    assert d.emitted[0]["message"]["name"] == "Robin"
    assert (200, 10, 2, "x") in d._seen_keys
    # the same message re-fetched for that chat is deduped ...
    await d._sweep_deliver(200, [dict(m)], "rr")
    assert len(d.emitted) == 1
    # ... but an identical (ts, from, text) in ANOTHER chat is its own message
    await d._sweep_deliver(300, [dict(m)], "sweep")
    assert [e["acct"] for e in d.emitted] == [200, 300]


async def test_do_send_emits_acct_and_sid(daemon_mod):
    d = _daemon(daemon_mod)
    await d.do_send(100, "hello", "sid-1")
    assert d.emitted[-1] == {"ev": "sent", "ok": True, "acct": 100, "sid": "sid-1"}
    await d.do_send(100, "hello")          # older app builds send no sid
    assert d.emitted[-1]["sid"] is None


async def test_do_send_recovers_dead_browser_and_retries_once(daemon_mod):
    steam = _FakeSteam(send_results=(False, True), dead=True)
    d = _daemon(daemon_mod, steam)
    calls: list = []

    async def _recover(why):
        calls.append(why)
        steam.dead = False
        return True

    d._recover_browser = _recover
    await d.do_send(100, "hello", "sid-2")
    assert calls == ["send"]
    assert len(steam.sent) == 2
    assert d.emitted[-1]["ok"] is True and d.emitted[-1]["sid"] == "sid-2"


async def test_do_send_waits_for_inflight_recovery_then_retries(daemon_mod):
    # a send landing DURING a poll-initiated recovery: is_dead() reads False
    # over the deliberate restart, so the gate must key on _recovering too
    steam = _FakeSteam(send_results=(False, True), dead=False)
    d = _daemon(daemon_mod, steam)
    d._recovering = True
    calls: list = []

    async def _recover(why):
        calls.append(why)
        return True

    d._recover_browser = _recover

    async def _finish_recovery():
        await asyncio.sleep(0.3)
        d._recovering = False

    asyncio.get_running_loop().create_task(_finish_recovery())
    await d.do_send(100, "hello", "sid-3")
    assert calls == []                      # waited for the running one
    assert len(steam.sent) == 2
    assert d.emitted[-1] == {"ev": "sent", "ok": True, "acct": 100, "sid": "sid-3"}


async def test_login_and_retry_wait_for_inflight_recovery(daemon_mod):
    # neither may call steam.restart() while a recovery owns the browser
    for fn in ("do_login", "do_retry"):
        steam = _FakeSteam()
        d = _daemon(daemon_mod, steam)
        d._recovering = True

        async def _finish_recovery(d=d):
            await asyncio.sleep(0.3)
            d.signed = True
            d._recovering = False

        asyncio.get_running_loop().create_task(_finish_recovery())
        await getattr(d, fn)()
        assert steam.restarts == 0, fn
        assert d.emitted and d.emitted[0]["ev"] == "status", fn


async def test_do_retry_owns_browser_over_its_restart(daemon_mod):
    d = _daemon(daemon_mod)
    d.signed = False
    busy_during_restart: list = []

    async def _restart(mode):
        busy_during_restart.append(d._login_busy)
        # a recovery attempted mid-restart must be refused
        busy_during_restart.append(await d._recover_browser("poll"))

    d.steam.restart = _restart
    await d.do_retry()
    assert busy_during_restart == [True, False]
    assert d._login_busy is False


async def test_recover_browser_backs_off_while_launch_keeps_failing(daemon_mod):
    steam = _FakeSteam(dead=True)

    async def _restart_fails(mode):
        steam.restarts += 1          # page never comes up: stays dead

    async def _signed():
        return not steam.dead

    steam.restart, steam.is_signed_in = _restart_fails, _signed
    d = _daemon(daemon_mod, steam)
    d._started = True
    assert d._recover_backoff == 30.0
    assert await d._recover_browser("poll") is False
    assert steam.restarts == 1 and d._recover_backoff == 60.0
    # inside the (doubled) window: refused, no relaunch
    d._recovered_at -= 45.0
    assert await d._recover_browser("poll") is False
    assert steam.restarts == 1
    d._recovered_at -= 20.0
    assert await d._recover_browser("poll") is False
    assert steam.restarts == 2 and d._recover_backoff == 120.0
    # a launch that comes back resets the interval
    async def _restart_ok(mode):
        steam.restarts += 1
        steam.dead = False

    steam.restart = _restart_ok
    d._recovered_at = None
    assert await d._recover_browser("poll") is True
    assert d._recover_backoff == 30.0


async def test_recover_browser_is_rate_limited_and_guarded(daemon_mod):
    steam = _FakeSteam(dead=True)
    d = _daemon(daemon_mod, steam)
    d._recovered_at = time.monotonic()
    assert await d._recover_browser("poll") is False
    assert steam.restarts == 0
    d._recovered_at = None
    d._login_busy = True
    assert await d._recover_browser("poll") is False
    d._login_busy = False
    d._quitting = True
    assert await d._recover_browser("poll") is False
    assert steam.restarts == 0


async def test_recover_browser_restarts_and_reopens_active(daemon_mod):
    steam = _FakeSteam(dead=True)
    d = _daemon(daemon_mod, steam)
    d._started = True
    opened: list = []

    async def _open(acct):
        opened.append(acct)
        return True

    steam.open_conversation = _open
    assert await d._recover_browser("fetch") is True
    assert steam.restarts == 1
    assert opened == [100]
    assert d._reloading is False and d._recovering is False
    assert d._recovered_at is not None
    assert d.emitted and d.emitted[0]["ev"] == "status"


# ── SteamPage: is_dead / close independence ────────────────────────────────

def test_steam_page_is_dead_flags(daemon_mod):
    sp = daemon_mod.SteamPage("nonexistent-profile-dir")
    assert sp.is_dead() is True          # never started
    sp._restarting = True
    assert sp.is_dead() is False         # deliberate teardown in flight
    sp._restarting = False

    class _Page:
        def is_closed(self):
            return False

    sp._page = _Page()
    assert sp.is_dead() is False
    sp._dead = True
    assert sp.is_dead() is True


async def test_steam_page_close_stops_driver_even_if_context_close_fails(daemon_mod):
    sp = daemon_mod.SteamPage("nonexistent-profile-dir")
    stopped: list = []

    class _Ctx:
        async def close(self):
            raise RuntimeError("Target page, context or browser has been closed")

    class _Pw:
        async def stop(self):
            stopped.append(True)

    sp._ctx, sp._pw = _Ctx(), _Pw()
    await sp.close()
    assert stopped == [True]
