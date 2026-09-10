"""r639: the early-reveal gate. finish() reliably reveals a window only when
one has actually been parked (it is in _parked). parked_count() lets the app
wait for that precondition before revealing early, which is the fix for the
r636 invisible-window bug (an early finish() that ran before any window was
parked restored nothing yet latched its done-flag).
"""
from __future__ import annotations

import pytest

boot_stealth = pytest.importorskip("puripuly_heart.boot_stealth")


@pytest.fixture(autouse=True)
def _clean():
    boot_stealth._finished = False
    boot_stealth._stop = None
    boot_stealth._parked.clear()
    yield
    boot_stealth._finished = False
    boot_stealth._stop = None
    boot_stealth._parked.clear()


def test_parked_count_starts_zero_and_tracks_parks():
    assert boot_stealth.parked_count() == 0
    boot_stealth._parked.add(111)
    boot_stealth._parked.add(222)
    assert boot_stealth.parked_count() == 2


def test_parked_count_is_the_early_reveal_precondition():
    # Before any window is parked, revealing would restore nothing - the gate
    # must hold. After a park, the gate opens.
    assert boot_stealth.parked_count() == 0
    boot_stealth._parked.add(999)          # the watchdog parked a window
    assert boot_stealth.parked_count() >= 1


class _FakeU32:
    def __init__(self, moved):
        self._moved = moved

    def GetWindowRect(self, hwnd, ptr):
        r = ptr._obj
        r.left = r.top = boot_stealth._OFFSCREEN
        r.right = boot_stealth._OFFSCREEN + 800
        r.bottom = boot_stealth._OFFSCREEN + 600
        return True

    def SystemParametersInfoW(self, _a, _b, ptr, _d):
        a = ptr._obj
        a.left = a.top = 0
        a.right, a.bottom = 1920, 1080
        return True

    def SetWindowPos(self, hwnd, _z, x, y, _w, _h, _f):
        self._moved.append((int(hwnd), x, y))
        return True

    def ShowWindow(self, hwnd, cmd):
        self._moved.append((int(hwnd), "show", cmd))
        return True


def test_finish_restores_a_parked_window_and_latches(monkeypatch):
    moved = []
    u32 = _FakeU32(moved)
    monkeypatch.setattr(boot_stealth, "_each_flutter_window", lambda fn: fn(u32, 555))
    boot_stealth._parked.add(555)

    boot_stealth.finish()

    assert (555, 560, 240) in moved, "a parked window is re-centred on finish()"
    assert (555, "show", 5) in moved, "and shown"
    assert boot_stealth._finished is True
    assert boot_stealth._stop is None


def test_finish_is_idempotent(monkeypatch):
    calls = []
    monkeypatch.setattr(boot_stealth, "_each_flutter_window",
                        lambda fn: calls.append(1))
    boot_stealth._parked.add(7)
    boot_stealth.finish()
    boot_stealth.finish()
    assert calls == [1], "a second finish() is a no-op (the post-start fallback)"
