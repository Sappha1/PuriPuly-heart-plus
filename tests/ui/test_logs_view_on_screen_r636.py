"""r636: the Logs view stops shipping its whole text buffer while off screen.

Before this, every 200 ms flush rebuilt up to 4,500 lines (~710 KB) and pushed
them to the Flutter client even when another tab was showing - the view's
.page stays set after a view swap, so the existing update guard never caught
it. Off screen the view now buffers; set_on_screen(True) catches up in one go.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import PropertyMock, patch

import pytest

from puripuly_heart.ui.views import logs as logs_module
from puripuly_heart.ui.views.logs import CLEANUP_BATCH, MAX_LOG_ENTRIES, LogsView


class _UpdateSpy:
    """Counts pushes to the client and the bytes each one would carry."""

    def __init__(self) -> None:
        self.calls = 0
        self.pushed_chars = 0

    def install(self, view: LogsView):
        spy = self

        def fake_update(text_control) -> None:
            spy.calls += 1
            spy.pushed_chars += len(text_control.value or "")

        return patch.object(type(view._log_text), "update", fake_update)


@pytest.fixture
def view_with_page():
    view = LogsView()
    spy = _UpdateSpy()
    with patch.object(
        type(view), "page", new_callable=PropertyMock, return_value=object()
    ), spy.install(view):
        yield view, spy


def _append(view: LogsView, count: int, *, start: int = 0) -> None:
    # bypass the 200 ms throttle: flush on every line, like a busy session does
    for i in range(start, start + count):
        view._last_update = 0.0
        view.append_log(f"log {i}")


def test_default_is_on_screen_so_existing_behaviour_holds(view_with_page) -> None:
    view, spy = view_with_page

    assert view._on_screen is True
    _append(view, 3)

    assert spy.calls == 3
    assert view._log_text.value == "log 0\nlog 1\nlog 2"


def test_off_screen_flushes_never_push(view_with_page) -> None:
    view, spy = view_with_page
    view.set_on_screen(False)

    _append(view, 200)

    assert spy.calls == 0
    assert view._log_text.value == ""
    # the lines are kept, just not rendered
    assert len(view._model.visible_lines) == 200
    assert view._pending_update is True


def test_set_on_screen_true_flushes_once_and_catches_up(view_with_page) -> None:
    view, spy = view_with_page
    view.set_on_screen(False)
    _append(view, 200)

    view.set_on_screen(True)

    assert spy.calls == 1
    assert view._log_text.value.splitlines()[0] == "log 0"
    assert view._log_text.value.splitlines()[-1] == "log 199"
    assert view._rendered_line_count == 200
    assert view._pending_update is False

    # a repeat call is a no-op, and normal flushing resumes
    view.set_on_screen(True)
    assert spy.calls == 1
    _append(view, 1, start=200)
    assert spy.calls == 2


def test_going_off_screen_and_back_pushes_one_payload_not_one_per_line(
    view_with_page,
) -> None:
    view, spy = view_with_page
    view.set_on_screen(False)
    _append(view, 500)
    view.set_on_screen(True)
    single_payload = spy.pushed_chars

    assert spy.calls == 1
    # the same 500 lines flushed on screen re-send the whole text every time
    on_screen = _UpdateSpy()
    other = LogsView()
    with patch.object(
        type(other), "page", new_callable=PropertyMock, return_value=object()
    ), on_screen.install(other):
        _append(other, 500)

    assert on_screen.calls == 500
    assert on_screen.pushed_chars > single_payload * 100


def test_cleanup_semantics_survive_an_off_screen_stretch(view_with_page) -> None:
    view, spy = view_with_page
    view.set_on_screen(False)

    _append(view, MAX_LOG_ENTRIES + CLEANUP_BATCH + 1)
    view.set_on_screen(True)

    assert spy.calls == 1
    assert view._model.cleanup_count == 1
    assert len(view._model.visible_lines) == MAX_LOG_ENTRIES + 1
    # the rebuild path ran, so the oldest CLEANUP_BATCH lines are gone
    assert view._log_text.value.splitlines()[0] == f"log {CLEANUP_BATCH}"
    assert view._last_cleanup_count == 1
    assert view._rendered_line_count == len(view._model.visible_lines)


def test_conversation_view_does_not_push_while_off_screen(view_with_page) -> None:
    view, spy = view_with_page
    # the toggle calls Column.update(), which needs a real mount - not what
    # this test is about
    with patch.object(type(view), "update", lambda _self: None):
        view._on_conversation_button_click(SimpleNamespace())
    spy.calls = 0
    view.set_on_screen(False)

    with patch.object(logs_module, "_format_conversation_timestamp", return_value="18:06:12"):
        view.append_conversation_record(
            source="Mic",
            channel="self",
            source_text="original",
            translated_text="translated",
        )

    assert spy.calls == 0
    # the text is still rendered locally, it just isn't shipped
    assert "translated" in view._log_text.value

    view.set_on_screen(True)
    assert spy.calls == 1
    assert "translated" in view._log_text.value


def test_max_log_entries_bounds_the_rendered_payload() -> None:
    # r636: 4000 + 500 headroom meant a 710 KB text on every on-screen flush
    assert MAX_LOG_ENTRIES == 1500
    assert CLEANUP_BATCH == 500
