"""r379: a long settings label pushed its On/Off button off the row.

"Separate 'Text Translation' box on the dashboard" ran past the edge and its
value button was not visible, so the setting could be read but not changed.

The row is Row([title(expand=True), value(width=180)]), which looks safe. What
breaks it is the title: _info_title builds Row([text, icon], tight=True), and a
tight Row sizes to its CONTENT — so a label wider than the space available
overflows rather than shrinking, straight over the value column.

Fixed at the layout level rather than by shortening one string, because every
setting in the app uses this row and the translations are routinely longer than
the English.
"""
from __future__ import annotations

import flet as ft

from puripuly_heart.ui.components.settings.settings_unit_card import SettingsUnitCard
from puripuly_heart.ui.views.settings import SettingsView

LONG = "Separate 'Text Translation' box on the dashboard and then some more"


def _title_row(text: ft.Text) -> ft.Row:
    view = SettingsView.__new__(SettingsView)
    return SettingsView._info_title(view, text, "tip", _register=False)


def test_a_long_label_is_allowed_to_shrink() -> None:
    text = ft.Text(LONG, size=13)
    _title_row(text)

    assert text.expand_loose is True, (
        "the label cannot give way, so a long one overflows the row and covers "
        "the value button"
    )
    assert text.max_lines == 2, "an unbounded label would grow the row instead"
    assert text.overflow == ft.TextOverflow.ELLIPSIS


def test_a_label_that_already_expands_is_left_alone() -> None:
    """Some rows set their own sizing; this must not fight them."""
    text = ft.Text("already handled", size=13, expand=True)
    _title_row(text)
    # expand_loose defaults to False rather than None, so assert it was not
    # turned ON — an expanding label already fills the space it is given.
    assert text.expand_loose is not True, "overrode a label that manages its own width"
    assert text.expand, "the label's own sizing was clobbered"


def test_the_value_column_keeps_its_width_whatever_the_label_says() -> None:
    """The button must never be squeezed out — that is the reported symptom."""
    card = SettingsUnitCard(
        title=_title_row(ft.Text(LONG, size=13)),
        value=ft.Text("On"),
    )
    row = card.content
    title_slot, value_slot = row.controls

    assert value_slot.width == 180, "the value column lost its reserved width"
    assert title_slot.expand is True, "the label column no longer yields space"


def test_the_reported_label_is_not_absurdly_long_in_any_language() -> None:
    """The layout fix is the real one, but a label that needs two lines in every
    language is still a bad label."""
    import io
    import json
    from pathlib import Path

    key = "settings.separate_text_translation"
    for code in ("en", "zh-CN", "ja", "ko"):
        data = json.load(
            io.open(Path("src/puripuly_heart/data/i18n") / f"{code}.json", encoding="utf-8")
        )
        value = data[key]
        assert len(value) <= 40, (
            f"{code} label is {len(value)} characters: {value!r} — long enough to "
            f"need the ellipsis on every screen"
        )
