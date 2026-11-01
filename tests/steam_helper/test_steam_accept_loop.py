"""r668: one failed accept must not leave the Steam helper unreachable.

asyncio's Windows (proactor) server closes its listening socket for good the
first time an accept fails (a client that drops mid-connect). The helper kept
running with nothing listening: the app could not reconnect, so no messages
arrived and sends queued forever, and a fresh helper could not take the port.
The daemon now runs its own accept loop.
"""
from __future__ import annotations

import asyncio
import importlib.util
import shutil
import socket
import sys
from pathlib import Path

import pytest

_SRC = Path(__file__).resolve().parents[2] / "src" / "puripuly_heart" / "data" / "steam_bridge_src"


@pytest.fixture(scope="module")
def daemon_mod(tmp_path_factory):
    # a COPY: the daemon writes diag.log next to its own file
    d = tmp_path_factory.mktemp("daemon")
    for f in ("daemon.py", "steam_page.py"):
        shutil.copy2(_SRC / f, d / f)
    spec = importlib.util.spec_from_file_location("pph_steam_daemon_accept", d / "daemon.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def _listener() -> socket.socket:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    s.listen(8)
    return s


def _daemon(daemon_mod, handled: asyncio.Queue):
    d = object.__new__(daemon_mod.Daemon)
    d._conn_tasks = set()

    async def handle(reader, writer):
        await handled.put(await reader.readline())
        writer.close()

    d.handle = handle
    return d


def test_a_failed_accept_keeps_the_helper_listening(daemon_mod):
    async def main():
        handled: asyncio.Queue = asyncio.Queue()
        d = _daemon(daemon_mod, handled)
        lsock = _listener()
        loop = asyncio.get_running_loop()
        real = loop.sock_accept
        calls = {"n": 0}

        async def flaky(sock):
            calls["n"] += 1
            if calls["n"] == 1:
                # what a client dropping mid-connect looks like on Windows
                raise ConnectionAbortedError(64, "The specified network name "
                                                 "is no longer available")
            return await real(sock)

        loop.sock_accept = flaky
        served = asyncio.create_task(d._serve(lsock))
        try:
            for line in (b"first\n", b"second\n"):
                _r, w = await asyncio.open_connection(*lsock.getsockname())
                w.write(line)
                await w.drain()
                assert await asyncio.wait_for(handled.get(), 10) == line
                w.close()
            assert lsock.fileno() != -1
            assert calls["n"] >= 3
        finally:
            served.cancel()
            lsock.close()

    asyncio.run(main())


def test_a_listener_that_is_really_gone_ends_the_helper(daemon_mod):
    async def main():
        d = _daemon(daemon_mod, asyncio.Queue())
        lost = []

        async def listener_lost():
            lost.append(True)

        d._listener_lost = listener_lost
        lsock = _listener()
        lsock.close()
        await asyncio.wait_for(d._serve(lsock), 10)
        assert lost == [True]

    asyncio.run(main())


def test_run_serves_through_the_accept_loop():
    text = (_SRC / "daemon.py").read_text(encoding="utf-8")
    run = text[text.index("    async def run(self)"):text.index("    async def _serve(")]
    assert "await self._serve(lsock)" in run
    assert "start_server" not in run
