"""r636: chat entries are isolated Containers.

Every appended transcript used to run the chat Column's diff walk over the
whole capped log (~1,800 controls for 200 entries, 18-19 ms per append, twice
with extra-language lines) to discover that nothing inside the old entries had
changed. Flet 0.28 skips the children of a control whose is_isolated() is
True, so the entries are _IsolatedEntry now and the walk stops at each one.

The price is a contract: anything changed INSIDE an entry after it was added
(extra-language lines, the pending->final rewrite, find highlights) must push
its own update - a list-level update no longer reaches it. These pin the
isolation, the update targets of every such path, the targeted tab-flip
updates, and the typed clipboard bindings (page-less, nothing launched, the
real clipboard untouched).
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ft = pytest.importorskip("flet")

from puripuly_heart.ui.views import dashboard as dashboard_module
from tests.ui.test_dashboard_view_branches import _make_dashboard


class _PageSpy:
    """Stands in for the page: Control.update() forwards to page.update(ctrl),
    and _update_together batches several controls into one page.update."""

    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def update(self, *controls) -> None:
        self.calls.append(tuple(controls))

    @property
    def updated(self) -> list:
        return [c for call in self.calls for c in call]


def _prime(root):
    """Replay Flet's add pass so build_update_commands has a baseline to diff
    against (what a mounted tree carries after page.add)."""
    index = {"page": None}
    added: list = []
    root._build_add_commands(index=index, added_controls=added)
    for k, ctrl in enumerate(added):
        ctrl._Control__uid = f"u{k}"
        index[ctrl._Control__uid] = ctrl
    return index


def _append_peer(view, source_text: str, translated_text: str):
    view.append_chat_entry(
        channel="peer", source="Mic", source_text=source_text,
        translated_text=translated_text, src_lang_hint="en",
    )
    return view._chat_list_view.controls[-1]


def test_finalized_and_pending_entries_are_isolated(monkeypatch: pytest.MonkeyPatch) -> None:
    view = _make_dashboard(monkeypatch)
    entry = _append_peer(view, "hello there", "bonjour")
    assert isinstance(entry, dashboard_module._IsolatedEntry)
    assert entry.is_isolated() is True
    # only the entry boundary is isolated - the list still diffs its children
    # (new/evicted entries) and the entry's own column still diffs its lines
    assert view._chat_list_view.is_isolated() is False
    assert entry.content.is_isolated() is False

    view.on_send_message = lambda *args: None
    view._on_submit("typed line")
    pending = view._chat_list_view.controls[-1]
    assert pending is not entry
    assert pending.is_isolated() is True
    assert view._pending_sent_col is pending.content


def test_the_list_walk_stops_at_an_entry_but_the_entry_walk_does_not(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The mechanism itself, on Flet's own diff: a value changed inside a
    mounted entry is invisible to the chat Column's walk and emitted by the
    entry's walk - which is why every in-entry mutation must update itself."""
    view = _make_dashboard(monkeypatch)
    lv = view._chat_list_view
    entry = _append_peer(view, "first line", "premiere ligne")
    index = _prime(lv)
    text = next(c for c in entry.content.controls if getattr(c, "value", None) == "premiere ligne")
    text.value = "changed inside"

    cmds: list = []
    lv.build_update_commands(index, cmds, [], [])
    assert all(cmd.attrs.get("value") != "changed inside" for cmd in cmds)

    cmds = []
    entry.build_update_commands(index, cmds, [], [])
    assert any(cmd.name == "set" and cmd.attrs.get("value") == "changed inside" for cmd in cmds)


def test_append_extra_chat_lines_updates_the_entry_column_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    view = _make_dashboard(monkeypatch)
    entry = _append_peer(view, "hello there", "bonjour")
    col = entry.content
    assert view._last_chat_content_col is col
    before = len(col.controls)
    calls: list[str] = []
    monkeypatch.setattr(col, "update", lambda: calls.append("col"))
    monkeypatch.setattr(view._chat_list_view, "update", lambda: calls.append("list"))

    view.append_extra_chat_lines([("en", "extra english line"), ("de", "   ")])

    added = col.controls[before:]
    assert added, "the extra line was not appended to the entry"
    assert added[-1].value == "extra english line"
    assert added[-1].color == dashboard_module._TEXT_PRIMARY
    assert len(added) == 1, "a blank pair must not add a line"
    # the entry's own column pushed the change; the list walk cannot see it
    assert calls == ["col"]


def test_pending_echo_is_finalized_in_place_and_pushes_its_own_column(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    view = _make_dashboard(monkeypatch)
    view.on_send_message = lambda *args: None
    view._on_submit("typed line")
    pending = view._chat_list_view.controls[-1]
    col = pending.content
    assert view._pending_sent_col is col
    n_entries = len(view._chat_list_view.controls)

    calls: list[str] = []
    col.page = object()  # mounted, as far as the finalize check is concerned
    monkeypatch.setattr(col, "update", lambda: calls.append("col"))
    monkeypatch.setattr(view._chat_list_view, "update", lambda: calls.append("list"))

    view.append_chat_entry(
        channel="self", source="You", source_text="typed line",
        translated_text="ligne tapee", src_lang_hint="en",
    )

    # the same entry, rewritten in place - no new list child
    assert len(view._chat_list_view.controls) == n_entries
    assert view._chat_list_view.controls[-1] is pending
    assert view._pending_sent_col is None
    assert isinstance(col.controls[0], ft.Row), "the header row leads the finalized column"
    values = [getattr(c, "value", None) for c in col.controls]
    assert "ligne tapee" in values
    assert calls == ["col"]


def test_find_highlight_reaches_inside_an_isolated_entry(monkeypatch: pytest.MonkeyPatch) -> None:
    view = _make_dashboard(monkeypatch)
    _append_peer(view, "nothing here", "rien ici")
    entry = _append_peer(view, "needle in the hay", "aiguille")
    spy = _PageSpy()
    entry.page = spy
    view._find_visible = True

    view._run_find("needle")

    assert view._find_matches and view._find_matches[0][0] is entry
    text = view._find_matches[0][1]
    assert text.value == "" and text.spans, "the match was not turned into highlight spans"
    # the highlighted entry pushed itself (one page.update for the dirty set)
    assert spy.calls == [(entry,)]
    assert view._find_dirty_entries == {}
    assert id(entry) in view._find_lit_entries

    spy.calls.clear()
    view._run_find("")
    assert text.value == "needle in the hay" and text.spans == []
    # clearing a highlight is a change inside the entry too
    assert spy.calls == [(entry,)]
    assert view._find_lit_entries == {}


def test_chat_clear_drops_the_highlight_bookkeeping(monkeypatch: pytest.MonkeyPatch) -> None:
    view = _make_dashboard(monkeypatch)
    monkeypatch.setattr(view, "_clear_ocr_paste_temp", lambda: None)
    entry = _append_peer(view, "needle in the hay", "aiguille")
    spy = _PageSpy()
    entry.page = spy
    view._find_visible = True
    view._run_find("needle")
    assert id(entry) in view._find_lit_entries
    spy.calls.clear()

    view._on_chat_clear(None)

    assert view._chat_list_view.controls == []
    assert view._find_lit_entries == {} and view._find_dirty_entries == {}
    assert view._find_matches == []
    # a cleared entry is never pushed again
    assert spy.calls == []


def test_update_together_batches_mounted_controls_into_one_page_update(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    view = _make_dashboard(monkeypatch)
    spy = _PageSpy()
    a, b, unmounted = ft.Container(), ft.Container(), ft.Container()
    a.page = spy
    b.page = spy

    view._update_together(None, a, unmounted, b)
    assert spy.calls == [(a, b)]

    spy.calls.clear()
    view._update_together(None, unmounted)
    assert spy.calls == []


def test_tab_flips_update_their_own_containers_not_the_dashboard(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    view = _make_dashboard(monkeypatch)
    calls: list = []
    monkeypatch.setattr(view, "update", lambda: calls.append("dashboard"))
    monkeypatch.setattr(view, "_update_together", lambda *c: calls.append(("together", c)))
    monkeypatch.setattr(view, "_safe_update", lambda c: calls.append(("safe", c)))

    # the very first select may insert the Steam sidebar slot into the Row
    # (that one legitimately needs the Row) - the steady state is what matters
    view._select_chat_tab_now("vrc")
    calls.clear()
    view._select_chat_tab_now("vrc")
    assert "dashboard" not in calls
    together = [c for c in calls if c[0] == "together"]
    assert len(together) == 1
    targets = together[0][1]
    assert targets[:3] == (view._chat_header_row, view._chat_stack, view._app_sidebar)
    assert len(targets) == 4
    assert view._vrc_wrap.opacity == 1 and view._steam_wrap.opacity == 0

    calls.clear()
    view._set_steam_tab_hidden(True)
    assert calls == [("safe", view._chat_header_row)]
    assert view._tab_steam.visible is False
    assert view._steam_tabs_wrap.visible is False


@pytest.mark.skipif(sys.platform != "win32", reason="Win32 clipboard bindings")
def test_clipboard_bindings_declare_64_bit_safe_types() -> None:
    """The untyped ctypes.windll path truncated the HGLOBAL and the GlobalLock
    pointer to 32 bits (GlobalLock returned NULL, memmove raised after the
    clipboard had already been emptied). Only the binding shape is checked -
    nothing here opens the real clipboard."""
    import ctypes
    from ctypes import wintypes

    k32, u32 = dashboard_module._typed_clipboard()
    assert k32.GlobalAlloc.restype is wintypes.HGLOBAL
    assert list(k32.GlobalAlloc.argtypes) == [wintypes.UINT, ctypes.c_size_t]
    assert k32.GlobalLock.restype is ctypes.c_void_p
    assert list(k32.GlobalLock.argtypes) == [wintypes.HGLOBAL]
    assert k32.GlobalUnlock.restype is wintypes.BOOL
    assert list(k32.GlobalUnlock.argtypes) == [wintypes.HGLOBAL]
    assert k32.GlobalFree.restype is wintypes.HGLOBAL
    assert list(k32.GlobalFree.argtypes) == [wintypes.HGLOBAL]
    assert u32.OpenClipboard.restype is wintypes.BOOL
    assert list(u32.OpenClipboard.argtypes) == [wintypes.HWND]
    assert u32.EmptyClipboard.restype is wintypes.BOOL
    assert u32.SetClipboardData.restype is wintypes.HANDLE
    assert list(u32.SetClipboardData.argtypes) == [wintypes.UINT, wintypes.HANDLE]
    assert u32.CloseClipboard.restype is wintypes.BOOL
    # private handles, so the declarations can't leak into (or be clobbered
    # by) the shared untyped ctypes.windll objects
    assert k32 is not ctypes.windll.kernel32
    assert u32 is not ctypes.windll.user32
    assert dashboard_module._typed_clipboard() == (k32, u32)


def test_clipboard_copy_builds_the_block_before_touching_the_clipboard() -> None:
    """Order of operations is the other half of the fix: the DIB block is
    allocated, locked and filled BEFORE OpenClipboard/EmptyClipboard, so a
    failure can no longer leave the clipboard empty; a refused
    SetClipboardData frees the block instead of leaking it; and a failure is
    logged rather than silently handed to the PowerShell fallback."""
    src = Path(dashboard_module.__file__).read_text(encoding="utf-8")
    start = src.index("def _copy_chat_image")
    body = src[start:src.index("\n    def ", start + 1)]
    assert "ctypes.windll" not in body, "the copy is back on the untyped shared bindings"
    assert "_typed_clipboard()" in body
    assert (
        body.index("GlobalAlloc(")
        < body.index("GlobalLock(")
        < body.index("memmove(")
        < body.index("OpenClipboard(")
        < body.index("EmptyClipboard()")
        < body.index("SetClipboardData(8, h)")
    )
    after_set = body[body.index("SetClipboardData(8, h)"):]
    assert "GlobalFree(h)" in after_set[:after_set.index("finally:")]
    assert "logger.warning(" in body
    assert "get_last_error()" in body
