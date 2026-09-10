"""r643: boot_splash percentage rendering + no-op behavior in source runs."""
from __future__ import annotations

import pytest

boot_splash = pytest.importorskip("puripuly_heart.boot_splash")


def test_render_pct_is_a_short_percentage():
    assert boot_splash.render_pct(0) == "0%"
    assert boot_splash.render_pct(45) == "45%"
    assert boot_splash.render_pct(100) == "100%"


def test_render_pct_clamps():
    assert boot_splash.render_pct(-5) == "0%"
    assert boot_splash.render_pct(250) == "100%"


def test_start_progress_is_a_noop_without_pyi_splash(monkeypatch):
    monkeypatch.setattr(boot_splash, "_pyi", lambda: None)
    boot_splash._thread = None
    boot_splash.start_progress()
    assert boot_splash._thread is None, "no animation thread without pyi_splash"


def test_close_is_a_noop_and_idempotent(monkeypatch):
    monkeypatch.setattr(boot_splash, "_pyi", lambda: None)
    boot_splash._closed = False
    boot_splash.close()
    boot_splash.close()
    assert boot_splash._closed is True
