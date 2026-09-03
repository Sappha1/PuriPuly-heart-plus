"""r381: the always-on-top guard could never find its own window.

Reported as the overlay being switched on, captions flowing, and nothing on
screen. Measured against the running app: the window was visible, correctly
sized and placed, click-through, and still carrying WS_EX_TOPMOST — but sat at
z-position 8, underneath a full-screen game, while Discord's overlay held
position 1.

The guard that exists for exactly this runs every four seconds and located the
window with `FindWindowW(None, "PuriPuly Overlay")`. That call returns 0 for
this window: EnumWindows finds it (title 16 plain ASCII characters, class
FLUTTER_RUNNER_WIN32_WINDOW) while FindWindowW with that exact string, and with
the class name, both return 0. So `if hwnd:` was false every time.

One SetWindowPos(HWND_TOPMOST) then restored it from position 8 to 1 — the call
was never the problem, the handle was.

Win32 z-order cannot be exercised headlessly, so these assert the properties
that were actually wrong.
"""
from __future__ import annotations

import re
from pathlib import Path

SOURCE = Path("src/puripuly_heart/ui/desktop_overlay.py")


def _guard_source() -> str:
    text = SOURCE.read_text(encoding="utf-8")
    start = text.index("def _resolve_native_hwnd")
    end = text.index("async def _topmost_guard_loop", start)
    return text[start:end]


def test_the_window_is_not_looked_up_by_title() -> None:
    """FindWindowW returns 0 for this window — verified live. A title lookup
    also breaks the moment the title is renamed or localized."""
    # The name still appears in the comment explaining why it was removed, so
    # match the CALL rather than the mention.
    assert "user32.FindWindowW(" not in SOURCE.read_text(encoding="utf-8"), (
        "the overlay is locating its window by title again; that call returns "
        "0 for this window, which silently disables the always-on-top guard"
    )


def test_the_handle_comes_from_this_process() -> None:
    guard = _guard_source()
    assert "GetCurrentProcessId" in guard, (
        "the handle is no longer resolved from the overlay's OWN process, so "
        "it depends on matching something about the window again"
    )
    assert "EnumWindows" in guard
    assert "IsWindowVisible" in guard, "an invisible window would be picked up"


def test_the_guard_reports_when_it_cannot_find_a_window() -> None:
    """The whole failure was silent: a guard that runs but does nothing looks
    exactly like a guard that works."""
    guard = _guard_source()
    assert "could not resolve" in guard, (
        "a failure to resolve the handle is unlogged again, so the guard can "
        "go back to doing nothing without anyone noticing"
    )


def test_the_guard_still_runs_on_a_timer() -> None:
    text = SOURCE.read_text(encoding="utf-8")
    assert "_topmost_guard_loop" in text
    loop = text[text.index("async def _topmost_guard_loop"):][:4000]
    assert "_reassert_native_topmost" in loop
    sleep = re.search(r"sleep\(([\d.]+)\)", loop)
    assert sleep and float(sleep.group(1)) <= 10.0, (
        "the self-heal interval got long enough to be useless mid-conversation"
    )


def test_topmost_is_asserted_with_the_right_constants() -> None:
    guard = _guard_source()
    # HWND_TOPMOST is -1; 0x13 = SWP_NOMOVE|SWP_NOSIZE|SWP_NOACTIVATE, so the
    # call cannot move, resize or steal focus from the game underneath.
    assert "SetWindowPos(hwnd, -1, 0, 0, 0, 0, 0x13)" in guard, (
        "the topmost re-assert changed shape; it must not move, resize or "
        "activate the window"
    )
