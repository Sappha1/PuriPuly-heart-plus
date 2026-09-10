"""r636: the overlay renderer stopped rebuilding the whole page per snapshot.

The presenter republishes the SAME frame every 100 ms for about two seconds per
turn, with a load-bearing refresh nonce so revision dedup cannot coalesce it
(core/overlay/state.py tick_peer_presentation_refresh; the nonce reaches this
renderer on block.session_scope). Before r636 every one of those republishes ran
page.clean() + page.add() + page.update() - a full teardown and rebuild of the
caption tree, which is per-character ft.Text controls for CJK ruby, up to twenty
times per turn.

r636 keeps a pixel signature of the plan. When nothing that affects pixels moved,
the page tree is left alone and a dedicated 1x1 transparent container is flipped
and updated on its own, which still submits a fresh Flutter frame - the half of
the r197-r208 "static frame silently dropped by the Windows compositor" fix that
the nonce republishes actually exist for.

The second half of this file covers the [Topmost] buried/re-sorted log limiter,
which was per (window description, 60 s) and so was reset every tick whenever the
burying window alternated between two classes (2,847 lines/day measured).
"""
from __future__ import annotations

import logging
from typing import Any

import flet as ft
import pytest

from puripuly_heart.core.overlay.protocol import (
    OverlayPresentationBlock,
    OverlayPresentationSnapshot,
)
from puripuly_heart.ui import desktop_overlay

WINDOW_BOUNDS: dict[str, int | float] = {"x": 320, "y": 720, "width": 1344, "height": 336}


class SpyWindow:
    def __init__(self) -> None:
        self.minimized = False
        self.visible = False
        self.ignore_mouse_events: bool | None = None
        self.left: int | float = WINDOW_BOUNDS["x"]
        self.top: int | float = WINDOW_BOUNDS["y"]
        self.width: int | float = WINDOW_BOUNDS["width"]
        self.height: int | float = WINDOW_BOUNDS["height"]


def _mount(control: object, page: object) -> None:
    """Mimic Flet: everything reachable from an added control gets `page` set.

    Without this the nudge is unmounted, and the renderer deliberately falls back
    to the full rebuild rather than dropping a frame.
    """
    try:
        control.page = page  # type: ignore[attr-defined]
    except Exception:
        pass
    child = getattr(control, "content", None)
    if child is not None:
        _mount(child, page)
    children = getattr(control, "controls", None)
    if isinstance(children, list | tuple):
        for item in children:
            _mount(item, page)


class SpyPage:
    """Records the three calls r636 is about: clean, add, and page.update()."""

    def __init__(self) -> None:
        self.window = SpyWindow()
        self.controls: list[Any] = []
        self.clean_calls = 0
        self.add_calls = 0
        self.page_update_calls = 0
        self.control_updates: list[Any] = []

    def add(self, *controls: Any) -> None:
        self.add_calls += 1
        self.controls.extend(controls)
        for control in controls:
            _mount(control, self)

    def clean(self) -> None:
        self.clean_calls += 1
        self.controls.clear()

    def update(self, *controls: Any) -> None:
        if controls:
            self.control_updates.extend(controls)
            return
        self.page_update_calls += 1

    def reset(self) -> None:
        self.clean_calls = 0
        self.add_calls = 0
        self.page_update_calls = 0
        self.control_updates.clear()


def _snapshot(
    *,
    revision: int,
    text: str,
    nonce: int,
) -> OverlayPresentationSnapshot:
    """A peer caption carrying the refresh nonce exactly where the presenter puts
    it: appended to session_scope (see _peer_session_scope_with_presentation_refresh).
    """
    block = OverlayPresentationBlock(
        id="peer-block",
        occupant_key="peer:turn-1",
        appearance_seq=10,
        channel="peer",
        block_variant="finalized",
        primary_text=text,
        secondary_text="",
        secondary_enabled=False,
        primary_language="en",
        session_scope=f"turn-1|peer_presentation_refresh={nonce}",
    )
    return OverlayPresentationSnapshot(revision=revision, blocks=[block])


def _renderer(page: SpyPage) -> desktop_overlay.FletDesktopRendererWindow:
    window = desktop_overlay.FletDesktopRendererWindow(locale="en")
    window._page = page  # noqa: SLF001 - render without starting the Flet app loop
    window._interaction_mode = "pass_through"  # noqa: SLF001 - the locked hot path
    window._current_window_bounds = dict(WINDOW_BOUNDS)  # noqa: SLF001 - deterministic plan
    # Both of these one-shot relayout kicks schedule page tasks; they are not
    # what this file is about and there is no event loop here.
    window._startup_relayout_pending = False  # noqa: SLF001 - see above
    window._content_surface_rendered = True  # noqa: SLF001 - see above
    return window


def _render(
    window: desktop_overlay.FletDesktopRendererWindow,
    snapshot: OverlayPresentationSnapshot,
) -> None:
    window._snapshot = snapshot  # noqa: SLF001 - dispatch_snapshot without the bridge
    window._render_page()  # noqa: SLF001 - the function under test


def _nudge_of(window: desktop_overlay.FletDesktopRendererWindow) -> Any:
    return window._render_nudge_ctrl  # noqa: SLF001 - the control the skip path pulses


def test_a_nonce_only_republish_takes_the_nudge_path() -> None:
    page = SpyPage()
    window = _renderer(page)

    _render(window, _snapshot(revision=7, text="hello there", nonce=1))
    assert page.add_calls == 1
    nudge = _nudge_of(window)
    assert nudge is not None
    root = page.controls[0]
    page.reset()

    # Same caption, next 100 ms republish: revision AND the load-bearing nonce
    # both moved, nothing that affects a pixel did.
    _render(window, _snapshot(revision=8, text="hello there", nonce=2))

    assert (page.clean_calls, page.add_calls) == (0, 0), (
        "a nonce-only republish rebuilt the caption tree again"
    )
    assert page.controls == [root], "the mounted root was replaced"
    assert page.control_updates == [nudge], (
        "the compositor nudge was not the only control updated"
    )


def test_the_nudge_alternates_so_every_republish_submits_a_frame() -> None:
    page = SpyPage()
    window = _renderer(page)
    _render(window, _snapshot(revision=1, text="hello there", nonce=1))
    nudge = _nudge_of(window)
    assert nudge.opacity == desktop_overlay._DESKTOP_RENDER_NUDGE_OPACITY_OFF

    seen: list[float] = []
    for step in range(2, 6):
        _render(window, _snapshot(revision=step, text="hello there", nonce=step))
        seen.append(nudge.opacity)

    assert seen == [
        desktop_overlay._DESKTOP_RENDER_NUDGE_OPACITY_ON,
        desktop_overlay._DESKTOP_RENDER_NUDGE_OPACITY_OFF,
        desktop_overlay._DESKTOP_RENDER_NUDGE_OPACITY_ON,
        desktop_overlay._DESKTOP_RENDER_NUDGE_OPACITY_OFF,
    ], "a repeated republish left the nudge untouched, so no frame was submitted"


def test_the_nudge_cannot_move_the_caption_layout() -> None:
    """r197-r208: the layout of this window is the fragile part. The nudge is a
    POSITIONED stack child, so Flutter sizes the stack from the content alone."""
    page = SpyPage()
    window = _renderer(page)
    _render(window, _snapshot(revision=1, text="hello there", nonce=1))

    root = page.controls[0]
    stack = root.content
    assert isinstance(stack, ft.Stack)
    nudge = _nudge_of(window)
    assert stack.controls[-1] is nudge
    assert (nudge.left, nudge.top) == (0, 0)
    assert nudge.width == desktop_overlay._DESKTOP_RENDER_NUDGE_SIZE
    assert nudge.height == desktop_overlay._DESKTOP_RENDER_NUDGE_SIZE


def test_a_text_change_still_rebuilds_the_page() -> None:
    page = SpyPage()
    window = _renderer(page)
    _render(window, _snapshot(revision=1, text="hello there", nonce=1))
    page.reset()

    _render(window, _snapshot(revision=2, text="something else", nonce=2))

    assert (page.clean_calls, page.add_calls) == (1, 1), (
        "a caption text change no longer reaches the screen"
    )
    assert page.control_updates == []


def test_a_visual_config_change_still_rebuilds_the_page() -> None:
    page = SpyPage()
    window = _renderer(page)
    _render(window, _snapshot(revision=1, text="hello there", nonce=1))
    page.reset()

    window._visual_state = desktop_overlay.DesktopCaptionVisualState(  # noqa: SLF001
        background_alpha=0.55,
    )
    _render(window, _snapshot(revision=2, text="hello there", nonce=2))

    assert (page.clean_calls, page.add_calls) == (1, 1), (
        "a background alpha change no longer reaches the screen"
    )


def test_a_click_through_change_still_rebuilds_the_page() -> None:
    """window.ignore_mouse_events is a PAGE attribute in Flet 0.28, and the nudge
    path never flushes those - so the chrome value is part of the signature."""
    page = SpyPage()
    window = _renderer(page)
    _render(window, _snapshot(revision=1, text="hello there", nonce=1))
    assert page.window.ignore_mouse_events is True
    page.reset()

    window._relayout_in_progress = True  # noqa: SLF001 - keeps the window interactive
    _render(window, _snapshot(revision=2, text="hello there", nonce=2))

    assert (page.clean_calls, page.add_calls) == (1, 1)
    assert page.window.ignore_mouse_events is False


def test_the_trailing_page_update_is_gone() -> None:
    """page.add() is itself a full page update in Flet 0.28 (Page.add -> __update),
    so the trailing page.update() was a second whole-tree diff and round trip."""
    page = SpyPage()
    window = _renderer(page)

    _render(window, _snapshot(revision=1, text="hello there", nonce=1))
    _render(window, _snapshot(revision=2, text="something else", nonce=2))

    assert page.add_calls == 2
    assert page.page_update_calls == 0


def test_full_redraw_env_flag_restores_the_old_rebuild(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    page = SpyPage()
    window = _renderer(page)
    _render(window, _snapshot(revision=1, text="hello there", nonce=1))
    page.reset()

    monkeypatch.setenv(desktop_overlay._DESKTOP_FULL_REDRAW_ENV, "1")
    _render(window, _snapshot(revision=2, text="hello there", nonce=2))

    assert (page.clean_calls, page.add_calls) == (1, 1)
    assert page.control_updates == []


def test_the_soft_hidden_state_also_skips_repeat_rebuilds() -> None:
    page = SpyPage()
    window = _renderer(page)
    window._suppress_content = True  # noqa: SLF001 - overlay toggled off
    _render(window, _snapshot(revision=1, text="hello there", nonce=1))
    page.reset()

    _render(window, _snapshot(revision=2, text="hello there", nonce=2))

    assert (page.clean_calls, page.add_calls) == (0, 0)
    assert page.control_updates == [_nudge_of(window)]


# ---------------------------------------------------------------------------
# [Topmost] buried / re-sorted log limiter
# ---------------------------------------------------------------------------

FIRST_CLASS = "GameFullscreenWindowClass (pid 4321)"
SECOND_CLASS = "OverlayHelperWindowClass (pid 8765)"


def _topmost_lines(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [
        record.getMessage()
        for record in caplog.records
        if "[Topmost]" in record.getMessage()
    ]


def test_alternating_burying_windows_cannot_defeat_the_limiter(
    caplog: pytest.LogCaptureFixture,
) -> None:
    window = desktop_overlay.FletDesktopRendererWindow(locale="en")
    caplog.set_level(logging.INFO, logger=desktop_overlay.__name__)

    clock = 0.0
    window._log_topmost_event("buried", FIRST_CLASS, now=clock)  # noqa: SLF001
    window._log_topmost_event("resorted", FIRST_CLASS, now=clock)  # noqa: SLF001
    window._log_topmost_event("buried", SECOND_CLASS, now=clock)  # noqa: SLF001
    window._log_topmost_event("resorted", SECOND_CLASS, now=clock)  # noqa: SLF001
    first_sightings = list(_topmost_lines(caplog))
    assert len(first_sightings) == 4, (
        "a new burying app must still be identifiable once per session"
    )

    # One tick per second for the next minute, alternating between the two
    # classes - the exact pattern that used to reset the per-description limiter.
    for step in range(1, 60):
        clock = float(step)
        desc = FIRST_CLASS if step % 2 else SECOND_CLASS
        window._log_topmost_event("buried", desc, now=clock)  # noqa: SLF001
        window._log_topmost_event("resorted", desc, now=clock)  # noqa: SLF001

    assert _topmost_lines(caplog) == first_sightings, (
        "alternating window classes still defeat the 60 s limiter"
    )

    window._log_topmost_event("buried", FIRST_CLASS, now=120.0)  # noqa: SLF001
    emitted = _topmost_lines(caplog)
    assert len(emitted) == len(first_sightings) + 1
    assert emitted[-1] == (
        f"[DesktopOverlay][Topmost] buried under {FIRST_CLASS}; re-sorting "
        "(118 similar suppressed)"
    ), "the swallowed lines are not accounted for on the next one that gets through"


def test_the_limiter_window_is_shared_by_both_lines(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The pair used to carry two independent timestamps, so a window that stayed
    on top kept writing both lines every tick."""
    window = desktop_overlay.FletDesktopRendererWindow(locale="en")
    caplog.set_level(logging.INFO, logger=desktop_overlay.__name__)

    window._log_topmost_event("buried", FIRST_CLASS, now=0.0)  # noqa: SLF001
    window._log_topmost_event("resorted", FIRST_CLASS, now=0.0)  # noqa: SLF001
    caplog.clear()

    for step in range(1, 40):
        window._log_topmost_event("buried", FIRST_CLASS, now=float(step))  # noqa: SLF001
        window._log_topmost_event("resorted", FIRST_CLASS, now=float(step))  # noqa: SLF001

    assert _topmost_lines(caplog) == []

    window._log_topmost_event("resorted", FIRST_CLASS, now=61.0)  # noqa: SLF001
    assert len(_topmost_lines(caplog)) == 1


def test_note_buried_by_still_routes_through_the_shared_limiter() -> None:
    source = desktop_overlay.__file__
    with open(source, encoding="utf-8") as handle:
        text = handle.read()
    assert "_topmost_buried_logged" not in text, (
        "the per-description limiter is back; alternating windows will defeat it"
    )
    assert "_topmost_resorted_logged" not in text
    assert text.count("_log_topmost_event(") >= 3
