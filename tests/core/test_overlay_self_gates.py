"""r384: the overlay's "show my own text" / "show my own voice" toggles did nothing.

Confirmed against the live config before any code was touched:

    ui.self_in_overlay  = False
    ui.typed_in_overlay = True      <- ON
    overlay.show_self   = False

Typed messages were switched on and still never reached the overlay.

Two gates guard the self channel. The hub decides per utterance and gets it
right; the presenter then re-checked overlay.show_self and dropped the whole
self channel, typed and spoken alike, because it cannot tell them apart. With
voice off and text on — the state above — the hub passed the message and the
presenter threw it away, and no amount of toggling could help: the dashboard
toggle wrote only the flag the presenter ignores.
"""
from __future__ import annotations

import pytest

from puripuly_heart.config.settings import effective_overlay_show_self


class _Ui:
    def __init__(self, self_in_overlay: bool, typed_in_overlay: bool) -> None:
        self.self_in_overlay = self_in_overlay
        self.typed_in_overlay = typed_in_overlay


class _Settings:
    def __init__(self, self_in_overlay: bool, typed_in_overlay: bool) -> None:
        self.ui = _Ui(self_in_overlay, typed_in_overlay)


@pytest.mark.parametrize(
    ("voice", "typed", "expected"),
    [
        (False, False, False),  # nothing of mine wanted — presenter blocks the channel
        (False, True, True),    # THE REPORTED STATE: text on, so the channel must open
        (True, False, True),
        (True, True, True),
    ],
)
def test_the_presenter_gate_opens_for_either_kind(voice, typed, expected) -> None:
    """The presenter's gate is the coarse question it can actually answer.

    Which KIND of self content to show is the hub's decision — it is the only
    one that knows whether an utterance was typed or spoken.
    """
    assert effective_overlay_show_self(_Settings(voice, typed)) is expected


def test_typed_only_is_not_blocked_by_the_voice_setting() -> None:
    """The exact regression, stated on its own so it cannot be parametrized away."""
    settings = _Settings(self_in_overlay=False, typed_in_overlay=True)
    assert effective_overlay_show_self(settings) is True, (
        "typed messages are gated on the SPOKEN setting again — with voice off "
        "and text on, nothing the user types reaches the overlay"
    )


def test_the_presenter_is_not_configured_from_show_self_directly() -> None:
    """overlay.show_self is "show my own SPOKEN messages"; feeding it to the
    presenter applies it to the whole channel, which is the bug."""
    from pathlib import Path

    controller = Path("src/puripuly_heart/ui/controller.py").read_text(encoding="utf-8")
    assert "show_self=self.settings.overlay.show_self" not in controller
    assert "show_self=settings.overlay.show_self" not in controller
    assert controller.count("show_self=effective_overlay_show_self(") == 2, (
        "a presenter configuration site is back to reading overlay.show_self raw"
    )


def test_the_dashboard_voice_toggle_writes_both_stores() -> None:
    """r334 locked ui.self_in_overlay and overlay.show_self together — the
    Settings card honours that, the dashboard toggle did not. Since the load-time
    reconcile ANDs the pair, writing one of them is silently undone at restart."""
    from pathlib import Path

    app = Path("src/puripuly_heart/ui/app.py").read_text(encoding="utf-8")
    start = app.index("def _on_dashboard_self_in_overlay_toggle")
    handler = app[start : app.index("def _on_dashboard_typed_in_overlay_toggle", start)]
    assert "_s.ui.self_in_overlay = value" in handler
    assert "_s.overlay.show_self = value" in handler, (
        "the dashboard voice toggle writes only the hub flag again; the "
        "presenter keeps the old value and the restart reconcile reverts it"
    )


def test_both_dashboard_toggles_refresh_the_live_presenter() -> None:
    """A correct value that never reaches the running presenter is still a
    toggle that does nothing for the session the user flipped it in."""
    from pathlib import Path

    app = Path("src/puripuly_heart/ui/app.py").read_text(encoding="utf-8")
    for name in (
        "_on_dashboard_self_in_overlay_toggle",
        "_on_dashboard_typed_in_overlay_toggle",
    ):
        start = app.index(f"def {name}")
        handler = app[start : start + 1400]
        body = handler[: handler.index("\n    def ", 10)]
        assert "_refresh_overlay_self_gate()" in body, (
            f"{name} no longer pushes the change to the live presenter"
        )
