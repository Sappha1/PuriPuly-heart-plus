"""r655: Steam text sends report what Steam actually did, and recover a stale session.

Steam's FriendChat.SendChatMessage() never throws - a rejected send is marked
failed in its persisted send queue and the call returns normally - so the helper
reported every rejected send as delivered (one chat collected dozens of silent
'Generic' failures). The page now calls SendChatMessageInternal (Steam's
EChatMsgSendError: 0 ok, 1 Generic, 2 NotFriends, 3 NoChatPermissionInGroup,
4 RateLimitExceeded) and taps console.warn for the EResult Steam only logs there.
On Generic the daemon re-initialises the chat, then reloads the page, retrying
after each step - but only after checking the message did not land after all.
"""
from __future__ import annotations

import ast
import importlib.util
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

_SRC = Path(__file__).resolve().parents[2] / "src" / "puripuly_heart" / "data" / "steam_bridge_src"


@pytest.fixture(scope="module")
def daemon_mod(tmp_path_factory):
    # a COPY: the daemon writes diag.log next to its own file, and the src
    # data tree is bundled into the release
    d = tmp_path_factory.mktemp("daemon")
    for f in ("daemon.py", "steam_page.py"):
        shutil.copy2(_SRC / f, d / f)
    spec = importlib.util.spec_from_file_location("pph_steam_daemon_send_outcome", d / "daemon.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


# ── page JS under Node ──────────────────────────────────────────────────────

_NODE = shutil.which("node")


def _send_js() -> str:
    tree = ast.parse((_SRC / "steam_page.py").read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "send":
            for sub in ast.walk(node):
                if (isinstance(sub, ast.Call) and isinstance(sub.func, ast.Attribute)
                        and sub.func.attr in ("evaluate", "_ev") and isinstance(sub.args[0], ast.Constant)):
                    return sub.args[0].value
    raise AssertionError("send() evaluate body not found")


_HARNESS = r"""
const queueAdds = [];
function chat(acct, result) {
  return {
    m_unAccountIDFriend: acct, chat_partner: {is_friend: true},
    m_MessageSendQueue: {Add: () => { queueAdds.push(acct); return "id"; }},
    async SendChatMessage(t) { queueAdds.push(acct); },          // must NOT be used
    async SendChatMessageInternal(t) {
      if (result !== 0) console.warn(`Error sending message (Attempt #1). Got EResult 15`);
      return result;
    },
  };
}
const chats = [chat(100, 0), chat(200, 1), chat(300, 2)];
globalThis.window = {g_FriendsUIApp: {m_ChatStore: {m_FriendChatStore: {m_rgFriendChats: chats},
                                                    GetFriendChat: () => null}}};
console.warn = () => {};   // the page's own sink
const SEND = %(send)s;
(async () => {
  const r = {};
  r.ok = await SEND([100, "hi"]);
  r.generic = await SEND([200, "hi"]);
  r.notfriends = await SEND([300, "hi"]);
  r.nochat = await SEND([999, "hi"]);
  r.queueAdds = queueAdds;
  process.stdout.write(JSON.stringify(r));
})();
"""


@pytest.mark.skipif(_NODE is None, reason="node not installed")
def test_page_send_reports_steams_real_outcome(tmp_path):
    js = tmp_path / "h.js"
    js.write_text(_HARNESS % {"send": _send_js()}, encoding="utf-8")
    p = subprocess.run([_NODE, str(js)], capture_output=True, text=True, timeout=60)
    assert p.returncode == 0, p.stderr
    r = json.loads(p.stdout)
    assert r["ok"]["ok"] is True and r["ok"]["code"] == 0 and r["ok"]["warns"] == []
    assert r["generic"]["ok"] is False and r["generic"]["code"] == 1     # used to read "delivered"
    assert any("EResult 15" in w for w in r["generic"]["warns"])        # real cause captured
    assert r["notfriends"]["code"] == 2 and r["notfriends"]["ok"] is False
    assert r["nochat"]["code"] == -1
    assert r["queueAdds"] == []          # Steam's persisted send queue untouched


# ── daemon ──────────────────────────────────────────────────────────────────

class _Steam:
    def __init__(self, results, landed=False):
        self.results = list(results)
        self.landed = landed
        self.sent: list = []
        self.opens: list = []
        self.reloads = 0

    async def send(self, acct, text):
        self.sent.append(acct)
        return self.results.pop(0)

    async def fetch_messages(self, acct, since):
        return ([{"from": 1, "text": "hello there friend", "ts": 1, "ordinal": 0}]
                if self.landed else [])

    async def open_conversation(self, acct):
        self.opens.append(acct)
        return True

    async def reload(self):
        self.reloads += 1

    async def is_signed_in(self):
        return True

    def is_dead(self):
        return False


def _r(code):
    return {"ok": code == 0, "code": code, "warns": [], "is_friend": True}


def _daemon(mod, steam, monkeypatch):
    async def _no_sleep(_s):
        return None
    monkeypatch.setattr(mod.asyncio, "sleep", _no_sleep)
    d = mod.Daemon()
    d.steam, d.own, d.active = steam, 1, 100
    d.emitted: list = []

    async def _emit(obj, only=None):
        d.emitted.append(obj)

    d.emit = _emit
    return d


async def test_generic_failure_reopens_then_reloads_and_retries(daemon_mod, monkeypatch):
    steam = _Steam([_r(1), _r(1), _r(0)])
    d = _daemon(daemon_mod, steam, monkeypatch)
    await d.do_send(100, "hello there friend", "sid-1")
    assert steam.sent == [100, 100, 100]          # first, after-reopen, after-reload
    assert steam.reloads == 1 and 100 in steam.opens
    assert d._reloading is False                  # poll un-paused
    assert d.emitted[-1] == {"ev": "sent", "ok": True, "acct": 100, "sid": "sid-1"}


async def test_no_retry_when_the_message_landed_anyway(daemon_mod, monkeypatch):
    steam = _Steam([_r(1)], landed=True)
    d = _daemon(daemon_mod, steam, monkeypatch)
    await d.do_send(100, "hello there friend", "sid-2")
    assert steam.sent == [100]                    # never double-sends
    assert d.emitted[-1]["ok"] is True


async def test_persistent_failure_is_reported_with_a_reason(daemon_mod, monkeypatch):
    steam = _Steam([_r(1), _r(1), _r(1)])
    d = _daemon(daemon_mod, steam, monkeypatch)
    await d.do_send(100, "hello there friend", "sid-3")
    assert d.emitted[-1] == {"ev": "sent", "ok": False, "acct": 100, "sid": "sid-3",
                             "reason": "generic"}


async def test_not_friends_is_not_retried(daemon_mod, monkeypatch):
    steam = _Steam([_r(2)])
    d = _daemon(daemon_mod, steam, monkeypatch)
    await d.do_send(100, "hello there friend", "sid-4")
    assert steam.sent == [100] and steam.reloads == 0
    assert d.emitted[-1]["ok"] is False and d.emitted[-1]["reason"] == "not_friends"
