"""r388: the first caption after a lull gets the corrective pulse.

The r176 "smushed" compressed layout was fixed with a one-shot corrective armed
at boot, soft reveal and un-minimize — and consumed by the first caption. Every
LATER caption arriving into an idle locked window (the surface empties ~8s
after each turn) got nothing. The r385 diagnostic proved it live: the one-shot
consumed at 06:43:38, then ten unguarded empty->content edges in three
minutes, and the user saw the smush with their own eyes.

The pulse is the reveal path's 1px resize — imperceptible — and explicitly NOT
the +48px startup settle, which drops click-through and visibly jiggles: fine
once at boot, unacceptable at every conversational lull.

Win32/Flutter behaviour can't run headless, so these pin the shape of the fix
the way the r381 guard tests do.
"""
from __future__ import annotations

from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
SOURCE = _ROOT / "src/puripuly_heart/ui/desktop_overlay.py"


def _render_edge_block() -> str:
    text = SOURCE.read_text(encoding="utf-8")
    start = text.index('"[DesktopOverlay][Render] empty->content')
    return text[start : start + 1600]


def test_the_lull_edge_schedules_the_pulse() -> None:
    block = _render_edge_block()
    assert "_composite_nudge_after_reveal" in block, (
        "the empty->content edge is back to only logging; the first caption "
        "after every lull renders smushed again with nothing to correct it"
    )


def test_the_pulse_is_locked_mode_only() -> None:
    """In edit mode the window is interactive and repaints normally — pulsing
    there would fight the user's own drag/resize."""
    block = _render_edge_block()
    assert "_DESKTOP_INTERACTION_MODE_PASS_THROUGH" in block


def test_the_pulse_cannot_stack() -> None:
    block = _render_edge_block()
    assert "not self._composite_kick_in_flight" in block
    assert "self._composite_kick_in_flight = True" in block
    # and the pulse releases the flag when it finishes
    text = SOURCE.read_text(encoding="utf-8")
    nudge = text.index("async def _composite_nudge_after_reveal")
    body = text[nudge : text.index("\n    async def ", nudge + 10)]
    assert "self._composite_kick_in_flight = False" in body, (
        "the in-flight flag is never released; one pulse and the fix is dead "
        "for the rest of the session"
    )


def test_the_lull_edge_never_uses_the_startup_settle() -> None:
    """The +48px settle disables click-through and visibly jiggles the text —
    the file itself calls it that. Once at boot is fine; at every lull it
    would flash and steal clicks mid-game."""
    block = _render_edge_block()
    assert "_startup_relayout_after_settle" not in block


def test_the_one_shot_startup_arm_is_untouched() -> None:
    """r176/r210: the FIRST caption after boot/reveal/un-minimize must keep
    its dedicated corrective regardless of the new steady-state pulse."""
    text = SOURCE.read_text(encoding="utf-8")
    start = text.index("if self._startup_relayout_pending and plan.slots:")
    block = text[start : start + 400]
    assert "_startup_relayout_after_settle" in block
    assert "self._startup_relayout_pending = False" in block
