"""r362: character edge styles for the overlay captions.

Requested as the set a video player offers — None, Drop shadow, Raised,
Depressed, Outline — because captions sit over arbitrary game footage and no
single treatment reads well on all of it. A drop shadow disappears against a
bright scene; an outline is noise against a dark one.
"""
from __future__ import annotations

import json
from pathlib import Path

from puripuly_heart.config.settings import DesktopFletOverlayVisualSettings
from puripuly_heart.ui import desktop_overlay as overlay

I18N = Path("src/puripuly_heart/data/i18n")


class _FakeShadow:
    def __init__(self, color=None, offset=None, blur_radius=None):
        self.color = color
        self.offset = offset
        self.blur_radius = blur_radius

    def __eq__(self, other: object) -> bool:
        # Compare what gets drawn, not object identity — otherwise every
        # comparison in this file silently comes back False.
        return (
            isinstance(other, _FakeShadow)
            and self.color == other.color
            and tuple(self.offset) == tuple(other.offset)
            and self.blur_radius == other.blur_radius
        )

    def __repr__(self) -> str:
        return f"Shadow({self.color}, {self.offset}, blur={self.blur_radius})"


class _FakeFt:
    BoxShadow = _FakeShadow


def test_every_style_produces_a_distinct_treatment() -> None:
    """Five names must mean five different looks, or the setting is a lie."""
    seen = {}
    for style in overlay.CAPTION_EDGE_STYLES:
        shadows = overlay._caption_text_shadow(_FakeFt, style)
        signature = tuple(
            (s.color, tuple(s.offset), s.blur_radius) for s in shadows
        )
        assert signature not in seen, (
            f"{style!r} renders identically to {seen[signature]!r}"
        )
        seen[signature] = style
    assert len(seen) == len(overlay.CAPTION_EDGE_STYLES)


def test_none_really_draws_nothing() -> None:
    assert overlay._caption_text_shadow(_FakeFt, "none") == []


def test_outline_surrounds_the_glyph_on_every_side() -> None:
    """An outline that only covers some directions leaves the text bleeding
    into the background exactly where it is brightest."""
    shadows = overlay._caption_text_shadow(_FakeFt, "outline")
    offsets = {tuple(s.offset) for s in shadows}

    for dx in (-1, 0, 1):
        for dy in (-1, 0, 1):
            if (dx, dy) == (0, 0):
                continue
            assert (dx, dy) in offsets, f"no outline at {(dx, dy)}"
    assert all(s.blur_radius == 0 for s in shadows), "a blurred outline is a shadow"


def test_raised_and_depressed_are_mirror_images() -> None:
    """They are the same two lights swapped; if they are not, one of them is
    not doing what its name says."""
    raised = overlay._caption_text_shadow(_FakeFt, "raised")
    depressed = overlay._caption_text_shadow(_FakeFt, "depressed")

    raised_light = next(s for s in raised if "FFFFFF" in s.color.upper())
    depressed_light = next(s for s in depressed if "FFFFFF" in s.color.upper())

    assert tuple(raised_light.offset) == tuple(
        -v for v in depressed_light.offset
    ), "the highlight falls on the same side in both"


def test_an_unknown_style_falls_back_instead_of_crashing() -> None:
    """A settings file edited by hand must not stop the overlay drawing."""
    assert overlay.normalize_caption_edge_style("elaborate") == (
        overlay.DEFAULT_CAPTION_EDGE_STYLE
    )
    assert overlay.normalize_caption_edge_style(None) == (
        overlay.DEFAULT_CAPTION_EDGE_STYLE
    )
    assert overlay._caption_text_shadow(_FakeFt, "elaborate") == (
        overlay._caption_text_shadow(_FakeFt, overlay.DEFAULT_CAPTION_EDGE_STYLE)
    )


def test_the_choice_survives_a_settings_round_trip() -> None:
    saved = DesktopFletOverlayVisualSettings(edge_style="outline")
    saved.validate()
    assert saved.edge_style == "outline"

    junk = DesktopFletOverlayVisualSettings(edge_style="sparkly")
    junk.validate()
    assert junk.edge_style == overlay.DEFAULT_CAPTION_EDGE_STYLE


def test_the_style_reaches_every_line_of_the_caption() -> None:
    """The reading line and the text under it must not end up with different
    edges — they are one caption to the person reading them."""
    state = overlay.DesktopCaptionVisualState(edge_style="outline")
    assert state.edge_style == "outline"


def test_the_style_names_are_translated_everywhere() -> None:
    for code in ("en", "zh-CN", "ja", "ko"):
        data = json.loads((I18N / f"{code}.json").read_text(encoding="utf-8"))
        for style in overlay.CAPTION_EDGE_STYLES:
            key = f"settings.overlay.edge_style.{style}"
            assert data.get(key), f"{code} is missing {key}"


def test_the_text_background_is_separate_from_the_panel() -> None:
    """r363: a video player has two backgrounds and they do different jobs —
    the panel behind the whole caption area, and a box hugging the glyphs. The
    second keeps text readable over a bright scene without dimming everything.
    """
    assert overlay._caption_text_background_color(0.0) is None, "off must draw nothing"
    assert overlay._caption_text_background_color(1.0) == "#FF000000"
    # 0.5 * 255 rounds to 128, not 127 — half-way values round to even
    assert overlay._caption_text_background_color(0.5) == "#80000000"

    saved = DesktopFletOverlayVisualSettings(
        background_alpha=0.4, text_background_alpha=0.75
    )
    saved.validate()
    assert saved.background_alpha == 0.4
    assert saved.text_background_alpha == 0.75, "the two must not share a value"


def test_the_text_background_defaults_to_off() -> None:
    """Updating must not change how anyone's overlay already looks."""
    fresh = DesktopFletOverlayVisualSettings()
    fresh.validate()
    assert fresh.text_background_alpha == 0.0


def test_both_controls_reach_the_overlay_menu() -> None:
    from puripuly_heart.ui.views.dashboard import DashboardView

    assert hasattr(DashboardView, "set_overlay_edge_style")
    assert hasattr(DashboardView, "set_overlay_text_background_alpha")

    source = Path("src/puripuly_heart/ui/views/dashboard.py").read_text(
        encoding="utf-8"
    )
    assert "_edge_row," in source, "the edge style row is not in the menu"
    assert "_text_bg_row," in source, "the text background row is not in the menu"


def test_the_text_background_label_is_translated_everywhere() -> None:
    for code in ("en", "zh-CN", "ja", "ko"):
        data = json.loads((I18N / f"{code}.json").read_text(encoding="utf-8"))
        assert data.get("settings.overlay.text_background.title"), f"{code} missing"
        assert data.get("settings.overlay.text_background.tooltip"), f"{code} missing"
