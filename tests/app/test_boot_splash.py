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


def test_set_alpha_is_a_noop_without_pyi_splash(monkeypatch):
    # r651: no pyi_splash (source run) -> silently does nothing, never raises.
    monkeypatch.setattr(boot_splash, "_pyi", lambda: None)
    boot_splash.set_alpha(85)
    boot_splash.set_alpha("nonsense")


class _FakePyi:
    def __init__(self):
        self.commands = []

    def _send_command(self, cmd, args=None):
        self.commands.append((cmd, list(args or [])))


def test_set_alpha_clamps_and_formats(monkeypatch):
    # r651: percent -> fraction, clamped to [0.20, 1.0], 3-decimal string.
    fake = _FakePyi()
    monkeypatch.setattr(boot_splash, "_pyi", lambda: fake)
    boot_splash.set_alpha(85)
    boot_splash.set_alpha(5)      # below floor -> 0.200
    boot_splash.set_alpha(500)    # above ceiling -> 1.000
    assert fake.commands == [
        ("set_alpha", ["0.850"]),
        ("set_alpha", ["0.200"]),
        ("set_alpha", ["1.000"]),
    ]


def test_set_alpha_ignores_bad_input(monkeypatch):
    fake = _FakePyi()
    monkeypatch.setattr(boot_splash, "_pyi", lambda: fake)
    boot_splash.set_alpha("nonsense")  # float() fails -> no command, no raise
    assert fake.commands == []


def test_reveal_is_a_noop_without_pyi_splash(monkeypatch):
    # r652: source run -> no pyi_splash -> silently does nothing, never raises.
    monkeypatch.setattr(boot_splash, "_pyi", lambda: None)
    boot_splash.reveal()


def test_reveal_sends_the_reveal_command(monkeypatch):
    # r652: the splash is baked withdrawn; reveal() deiconifies it (on = shown).
    fake = _FakePyi()
    monkeypatch.setattr(boot_splash, "_pyi", lambda: fake)
    boot_splash.reveal()
    assert fake.commands == [("reveal", [])]
