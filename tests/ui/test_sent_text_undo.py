from __future__ import annotations

import asyncio

import pytest

from puripuly_heart.ui import sent_text_undo
from puripuly_heart.ui.sent_text_undo import SentTextUndo, restore_after_native_undo


class FakeField:
    def __init__(self, value: str = "") -> None:
        self.value = value
        self.updates = 0

    def update(self) -> None:
        self.updates += 1


def test_partial_native_undo_is_completed_once() -> None:
    undo = SentTextUndo()
    undo.remember("see you tomorrow")

    # Flutter's snapshot lost the last two characters
    assert undo.restore_value("see you tomorr") == "see you tomorrow"
    # one-shot: a second Ctrl+Z is left to the native undo history
    assert undo.restore_value("") is None


def test_empty_box_gets_the_sent_text_back() -> None:
    undo = SentTextUndo()
    undo.remember("  hello  ")

    assert undo.restore_value("") == "hello"


def test_new_typing_is_never_overwritten() -> None:
    undo = SentTextUndo()
    undo.remember("hello")

    assert undo.restore_value("something else") is None
    assert undo.armed


def test_exact_native_undo_needs_no_restore() -> None:
    undo = SentTextUndo()
    undo.remember("hello")

    assert undo.restore_value("hello") is None
    assert not undo.armed


def test_nothing_remembered_does_nothing() -> None:
    assert SentTextUndo().restore_value("") is None


@pytest.fixture
def fast_timers(monkeypatch):
    monkeypatch.setattr(sent_text_undo, "_FIRST_CHECK_S", 0)
    monkeypatch.setattr(sent_text_undo, "_RECHECK_S", 0)


@pytest.mark.asyncio
async def test_restore_replaces_the_partial_value(fast_timers) -> None:
    undo = SentTextUndo()
    undo.remember("good morning")
    field = FakeField("good morni")

    assert await restore_after_native_undo(field, undo) is True
    assert field.value == "good morning"
    assert field.updates == 1


@pytest.mark.asyncio
async def test_restore_reapplies_after_a_late_stale_value(monkeypatch) -> None:
    monkeypatch.setattr(sent_text_undo, "_FIRST_CHECK_S", 0)
    monkeypatch.setattr(sent_text_undo, "_RECHECK_S", 0.01)
    undo = SentTextUndo()
    undo.remember("good morning")
    field = FakeField("")

    task = asyncio.create_task(restore_after_native_undo(field, undo))
    await asyncio.sleep(0.001)
    field.value = "good mor"  # the native undo's change arrives after the restore
    assert await task is True
    assert field.value == "good morning"


@pytest.mark.asyncio
async def test_restore_leaves_new_text_alone(fast_timers) -> None:
    undo = SentTextUndo()
    undo.remember("good morning")
    field = FakeField("unrelated")

    assert await restore_after_native_undo(field, undo) is False
    assert field.value == "unrelated"
    assert field.updates == 0


# ── wiring: the app shortcut and the dashboard ──────────────────────────────

from types import SimpleNamespace  # noqa: E402

import flet as ft  # noqa: E402

from puripuly_heart.ui.app import TranslatorApp  # noqa: E402
from puripuly_heart.ui.views.dashboard import DashboardView  # noqa: E402


def _app_with(dash) -> TranslatorApp:
    app = TranslatorApp.__new__(TranslatorApp)
    app.view_dashboard = dash
    app._inner_content = ft.Container(content=dash)
    app.content_area = ft.Container(content=ft.Column([ft.Container(), app._inner_content]))
    return app


def _key(key: str, **mods):
    base = {"ctrl": False, "shift": False, "alt": False, "meta": False}
    base.update(mods)
    return SimpleNamespace(key=key, **base)


def test_ctrl_z_on_the_dashboard_asks_it_to_restore() -> None:
    calls: list[str] = []
    app = _app_with(SimpleNamespace(undo_last_send=lambda: calls.append("undo")))

    app._on_keyboard_event(_key("Z", ctrl=True))
    app._on_keyboard_event(_key("z"))
    app._on_keyboard_event(_key("Z", ctrl=True, shift=True))

    assert calls == ["undo"]


class _Page:
    def __init__(self) -> None:
        self.tasks: list[tuple] = []

    def run_task(self, fn, *args):
        self.tasks.append((fn, args))


def _dash(chat_tab: str = "vrc", focused: bool = True) -> DashboardView:
    dash = DashboardView.__new__(DashboardView)
    dash._chat_tab = chat_tab
    dash._message_input_focused = focused
    dash._sent_undo = SentTextUndo()
    dash._msg_input = ft.TextField()
    dash._pending_paste = []
    dash._on_submit = lambda text: None
    dash._steam_view = SimpleNamespace(undo_last_send=lambda: None)
    return dash


def test_sending_from_the_chat_box_arms_the_restore(monkeypatch) -> None:
    dash = _dash()
    monkeypatch.setattr(DashboardView, "page", property(lambda self: _Page()), raising=False)
    dash._msg_input.value = "hello there"
    dash._on_send_btn_click(None)

    assert dash._msg_input.value == ""
    assert dash._sent_undo.restore_value("hello th") == "hello there"


def test_undo_schedules_the_restore_only_while_the_box_is_focused(monkeypatch) -> None:
    page = _Page()
    monkeypatch.setattr(DashboardView, "page", property(lambda self: page), raising=False)

    dash = _dash(focused=False)
    dash._sent_undo.remember("hi")
    dash.undo_last_send()
    assert page.tasks == []

    dash._message_input_focused = True
    dash.undo_last_send()
    assert page.tasks == [(restore_after_native_undo, (dash._msg_input, dash._sent_undo))]


def test_undo_on_the_steam_tab_goes_to_the_steam_box(monkeypatch) -> None:
    calls: list[str] = []
    dash = _dash(chat_tab="steam")
    dash._steam_view = SimpleNamespace(undo_last_send=lambda: calls.append("steam"))

    dash.undo_last_send()

    assert calls == ["steam"]
