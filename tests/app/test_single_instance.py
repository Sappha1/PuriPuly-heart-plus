"""r647: single-instance guard - release is idempotent/no-op-safe, and the
begin_shutdown hook releases it."""
from __future__ import annotations

import pytest

single_instance = pytest.importorskip("puripuly_heart.single_instance")


def test_release_before_acquire_is_a_noop():
    single_instance._handle = 0
    single_instance._released = False
    single_instance.release()   # nothing owned - must not raise
    assert single_instance._released is False   # nothing to release


def test_release_is_idempotent(monkeypatch):
    closed = []

    class _K32:
        @staticmethod
        def CloseHandle(h):
            closed.append(h)
            return True

    class _Windll:
        kernel32 = _K32()

    monkeypatch.setattr(single_instance, "_handle", 4242)
    monkeypatch.setattr(single_instance, "_released", False)
    import ctypes
    monkeypatch.setattr(ctypes, "windll", _Windll(), raising=False)

    single_instance.release()
    single_instance.release()
    assert closed == [4242], "the handle is closed exactly once"
    assert single_instance._handle == 0


def test_begin_shutdown_releases_the_instance_lock(monkeypatch):
    from puripuly_heart.core import shutdown

    called = []
    monkeypatch.setattr(single_instance, "release", lambda: called.append(1))
    # reset the shutdown latch so begin_shutdown runs its body
    shutdown._shutting_down.clear()
    try:
        shutdown.begin_shutdown("test")
        assert called == [1], "begin_shutdown must release the single-instance lock"
    finally:
        shutdown._shutting_down.clear()
