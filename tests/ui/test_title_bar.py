from __future__ import annotations

from types import SimpleNamespace

import pytest

pytest.importorskip("flet")

from puripuly_heart.ui.components.title_bar import TitleBar
from puripuly_heart.ui.theme import COLOR_NEUTRAL, COLOR_NEUTRAL_DARK


class DummyWindow:
    def __init__(self) -> None:
        self.minimized = False
        self.maximized = False
        self.closed = False

    def close(self) -> None:
        self.closed = True


class DummyPage:
    def __init__(self) -> None:
        self.window = DummyWindow()
        self.updated = 0

    def update(self) -> None:
        self.updated += 1


def test_title_bar_window_controls_and_hover(monkeypatch: pytest.MonkeyPatch) -> None:
    page = DummyPage()
    bar = TitleBar(page)
    monkeypatch.setattr(type(bar._title_text), "update", lambda self: None)

    bar._minimize(None)
    assert page.window.minimized is True

    bar._maximize(None)
    assert page.window.maximized is True
    bar._maximize(None)
    assert page.window.maximized is False

    bar._close(None)
    assert page.window.closed is True
    assert page.updated >= 3

    icon = SimpleNamespace(color=COLOR_NEUTRAL, update=lambda: None)
    bar._on_btn_hover(SimpleNamespace(control=SimpleNamespace(content=icon), data="true"))
    assert icon.color == COLOR_NEUTRAL_DARK
    bar._on_btn_hover(SimpleNamespace(control=SimpleNamespace(content=icon), data="false"))
    assert icon.color == COLOR_NEUTRAL

    close_container = SimpleNamespace(
        content=SimpleNamespace(color=COLOR_NEUTRAL, update=lambda: None),
        bgcolor=None,
        update=lambda: None,
    )
    bar._on_close_hover(SimpleNamespace(control=close_container, data="true"))
    assert close_container.content.color != COLOR_NEUTRAL
    bar._on_close_hover(SimpleNamespace(control=close_container, data="false"))
    assert close_container.content.color == COLOR_NEUTRAL


def test_title_bar_set_title_updates_text(monkeypatch: pytest.MonkeyPatch) -> None:
    bar = TitleBar(DummyPage())
    monkeypatch.setattr(type(bar._title_text), "update", lambda self: None)
    bar.set_title("New Title")
    assert bar._title_text.value == "New Title"
