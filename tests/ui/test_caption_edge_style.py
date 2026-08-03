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


def _directions(shadows) -> set[tuple[int, int]]:
    """Which way each offset points, independent of how far it reaches."""
    def sign(v: float) -> int:
        return (v > 0) - (v < 0)

    return {(sign(s.offset[0]), sign(s.offset[1])) for s in shadows}


def test_outline_surrounds_the_glyph_on_every_side() -> None:
    """An outline that only covers some directions leaves the text bleeding
    into the background exactly where it is brightest."""
    shadows = overlay._caption_text_shadow(_FakeFt, "outline", font_size=41)
    covered = _directions(shadows)

    for dx in (-1, 0, 1):
        for dy in (-1, 0, 1):
            if (dx, dy) == (0, 0):
                continue
            assert (dx, dy) in covered, f"no outline at {(dx, dy)}"
    assert all(s.blur_radius == 0 for s in shadows), "a blurred outline is a shadow"
    assert all(
        s.color == overlay._CAPTION_OUTLINE_COLOR for s in shadows
    ), "the rim is not the outline colour"


def test_outline_thickness_scales_with_the_glyph() -> None:
    """r370: this is the assertion whose absence shipped the bug.

    The old test pinned the offsets at exactly +/-1 and passed happily, because
    it checked which DIRECTIONS the rim covered and never how far it reached.
    One pixel around a 56px glyph is not an outline — it reads as slightly
    bolder text, which is what it was reported as.
    """
    radii = {size: overlay._caption_outline_radius(size) for size in (20, 41, 50, 56)}

    assert radii[56] > radii[41] > radii[20], (
        f"the rim does not grow with the glyph: {radii}"
    )
    for size, radius in radii.items():
        share = radius / size
        assert 0.04 <= share <= 0.08, (
            f"at {size}px the rim is {share:.1%} of the glyph; a video player's "
            f"is around 6%, and outside this band it reads as either a hairline "
            f"or a blob"
        )
    # The concrete regression: the medium preset must not go back to a hairline.
    assert radii[41] >= 2.0, "a 41px caption is back to a sub-2px rim"


def test_outline_has_no_gaps_on_the_diagonals() -> None:
    """Eight points at a wide radius leave the corners open and the rim looks
    chewed; the inner ring at half radius is what closes them."""
    shadows = overlay._caption_text_shadow(_FakeFt, "outline", font_size=56)
    reaches = {round((s.offset[0] ** 2 + s.offset[1] ** 2) ** 0.5, 4) for s in shadows}

    assert len(reaches) >= 2, (
        "every offset sits at one distance — there is no inner ring to fill "
        "the diagonal gaps"
    )
    assert len(shadows) == 16, f"expected two rings of eight, got {len(shadows)}"


def test_outline_survives_a_missing_or_junk_size() -> None:
    """The lock-screen placeholder and any older caller pass no size at all."""
    for bad in (None, 0, -5, "big", float("nan")):
        radius = overlay._caption_outline_radius(bad)
        assert radius >= overlay._CAPTION_OUTLINE_MIN_RADIUS, bad
        assert overlay._caption_text_shadow(_FakeFt, "outline", font_size=bad)


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


def test_the_choice_survives_the_trip_to_the_overlay_process() -> None:
    """r365: the overlay draws in ANOTHER PROCESS, and everything that reaches
    it is named explicitly in a payload. r362/r363 saved the settings, wired
    the menu, and never added them to that payload — so every choice was
    stored correctly and stopped at the process boundary. Nothing changed on
    screen no matter what was picked.

    This walks the actual chain: payload -> parser -> visual state -> plan.
    """
    from puripuly_heart.ui.desktop_overlay import _parse_runtime_visual_state

    payload = {
        "text_scale": 1.0,
        "background_alpha": 0.4,
        "edge_style": "outline",
        "text_background_alpha": 0.8,
    }
    state = _parse_runtime_visual_state(payload)

    assert state is not None
    assert state.edge_style == "outline"
    assert state.text_background_alpha == 0.8


def test_an_older_payload_without_the_new_keys_still_parses() -> None:
    """A running overlay from a previous build must not be rejected outright —
    _parse_runtime_visual_state returns None on anything it dislikes, and None
    means the overlay ignores the whole update."""
    from puripuly_heart.ui.desktop_overlay import _parse_runtime_visual_state

    state = _parse_runtime_visual_state({"text_scale": 1.0, "background_alpha": 0.4})

    assert state is not None, "an older payload must not be rejected"
    assert state.edge_style == overlay.DEFAULT_CAPTION_EDGE_STYLE
    assert state.text_background_alpha == 0.0


def test_the_wire_format_carries_both_settings() -> None:
    """The VR path goes through OverlayPresentationCalibration, which also
    enumerates its fields by hand."""
    from puripuly_heart.core.overlay.protocol import OverlayPresentationCalibration

    sent = OverlayPresentationCalibration(
        edge_style="raised", text_background_alpha=0.5
    )
    restored = OverlayPresentationCalibration.from_dict(sent.to_dict())

    assert restored.edge_style == "raised"
    assert restored.text_background_alpha == 0.5


def test_a_style_change_is_a_reason_to_push_an_update() -> None:
    """The controller only sends a visual update when it decides something
    changed. Styling was not on that list, so even a correct payload would
    have sat unsent."""
    source = Path("src/puripuly_heart/ui/controller.py").read_text(encoding="utf-8")

    assert 'getattr(previous_visual, "edge_style", None)' in source
    assert 'getattr(previous_visual, "text_background_alpha", None)' in source
    assert '"edge_style": getattr(visual, "edge_style", "shadow")' in source


def test_the_settings_survive_a_restart() -> None:
    """r365b: the SAVE path enumerates fields by hand too. Both settings were
    being written out of existence on every restart, which no amount of live
    plumbing would have fixed."""
    import puripuly_heart.config.settings as settings_mod

    saved = DesktopFletOverlayVisualSettings(
        background_alpha=0.4, edge_style="outline", text_background_alpha=0.8
    )
    saved.validate()

    on_disk = settings_mod._desktop_flet_visual_to_dict(saved)
    assert "edge_style" in on_disk, "edge style is not written to disk"
    assert "text_background_alpha" in on_disk, "text background is not written to disk"

    restored = settings_mod._parse_desktop_flet_visual(on_disk)
    assert restored.edge_style == "outline"
    assert restored.text_background_alpha == 0.8


def test_every_payload_builder_carries_the_styling() -> None:
    """r365b: there are TWO apply_visual_config builders — one seeds a starting
    overlay, the other runs on a live settings change. The first fix patched
    only the seeding one, so nothing moved when the user changed anything.

    Counting them means a third builder cannot be added without this failing.
    """
    source = Path("src/puripuly_heart/ui/controller.py").read_text(encoding="utf-8")

    builders = source.count('"command": "apply_visual_config"')
    assert builders == 2, f"expected 2 payload builders, found {builders}"

    # every one of them has to name both settings
    assert source.count('"edge_style"') >= builders
    assert source.count('"text_background_alpha"') >= builders


def test_every_caption_text_style_carries_both_settings() -> None:
    """r367: a caption line showing a reading above its characters is built by
    a DIFFERENT function from a plain one. r363 gave the background to the
    plain builder only, so on any line with pinyin or romaji above it — most
    of them, for a Chinese or Japanese partner — the background could never
    appear however well the plumbing worked.

    Counting instead of spot-checking: every text style that draws a caption
    glyph must carry the edge shadow AND the background, or one of them is
    invisible on half the lines.
    """
    import re

    source = Path("src/puripuly_heart/ui/desktop_overlay.py").read_text(
        encoding="utf-8"
    )
    blocks = re.findall(r"ft\.TextStyle\((.*?)\n\s*\)", source, re.S)
    # Only styles that draw a real caption LINE. The lock-screen placeholder
    # also carries a shadow, but it takes the default constant rather than a
    # line and has no user text to sit behind.
    styled = [
        b for b in blocks
        if "_caption_text_shadow" in b and "getattr(line," in b
    ]

    assert styled, "no caption line styles found — has the renderer moved?"
    for block in styled:
        assert "_caption_text_background_color" in block, (
            "a caption text style has the edge shadow but no text background; "
            "one of the two will be invisible on those lines"
        )
        assert block.count("bgcolor=") == 1, "duplicated bgcolor in one style"


def test_the_applied_config_is_logged_without_a_debug_flag() -> None:
    """Diagnosing this took three wrong guesses because the only record went
    through _emit_detailed_log, which is silent unless detailed logging is on —
    so "no log line" looked like "never sent"."""
    source = Path("src/puripuly_heart/ui/desktop_overlay.py").read_text(
        encoding="utf-8"
    )
    assert "visual config applied: edge_style=%s" in source
    marker = source.index("visual config applied: edge_style=%s")
    preceding = source[max(0, marker - 400):marker]
    assert "logger.info(" in preceding, "the record is not an unconditional log"


def test_the_plan_actually_carries_the_chosen_styling() -> None:
    """r369: THE bug, found after six failed attempts.

    _validated_visual_state round-trips the state through a settings object
    and then reads the styling back OFF that object. It was constructed
    without these two fields, so whatever the user chose was silently replaced
    with defaults immediately before the plan was built — while every layer
    above it (menu, settings, payload, parser, widget) was demonstrably
    correct. That is why the values were observably arriving and observably
    not drawn.

    Asserting on the PLAN, which is what the renderer consumes, rather than on
    any single link in the chain.
    """
    import sys

    sys.path.insert(0, "tests/ui")
    from test_desktop_overlay_renderer import _block

    from puripuly_heart.core.overlay.protocol import OverlayPresentationSnapshot

    snapshot = OverlayPresentationSnapshot(
        blocks=[
            _block(
                "b1",
                channel="peer",
                block_variant="finalized",
                appearance_seq=1,
                primary_text="hello",
                secondary_text="bonjour",
                secondary_enabled=True,
            )
        ]
    )
    state = overlay.DesktopCaptionVisualState(
        edge_style="outline", text_background_alpha=0.8
    )

    plan = overlay.build_desktop_caption_plan(snapshot, visual_state=state)

    assert plan.edge_style == "outline", "the plan lost the edge style"
    assert plan.text_background_alpha == 0.8, "the plan lost the text background"

    assert plan.lines, "no lines to check"
    for line in plan.lines:
        assert line.edge_style == "outline", f"line {line.text!r} lost the edge style"
        assert line.text_background_alpha == 0.8, (
            f"line {line.text!r} lost the text background"
        )

    # and the slots the renderer walks, not only the flattened view
    for slot in plan.slots:
        for line in slot.lines:
            assert line.edge_style == "outline"
            assert line.text_background_alpha == 0.8


def test_every_dashboard_overlay_seeder_is_actually_called() -> None:
    """r370: a setter nobody calls is a control that lies about its own state.

    set_overlay_edge_style and set_overlay_text_background_alpha were both
    written when those controls were added, both correct, and neither was ever
    called from anywhere in src/ — the only references in the repo were two
    hasattr checks in tests, which is precisely why the suite stayed green.

    The popover therefore opened at 0% and the default edge style no matter
    what was saved, while the push to the overlay is gated on the value
    DIFFERING from what is saved. A gesture landing on the stored value was
    dropped in silence, so whether a drag did anything looked random.
    """
    import re

    dashboard = Path("src/puripuly_heart/ui/views/dashboard.py").read_text(
        encoding="utf-8"
    )
    seeders = sorted(set(re.findall(r"def (set_overlay_\w+)\(", dashboard)))
    assert seeders, "no overlay seeders found — has the dashboard moved?"

    unwired = []
    for name in seeders:
        callers = 0
        for path in Path("src").rglob("*.py"):
            text = path.read_text(encoding="utf-8", errors="ignore")
            callers += len(re.findall(rf"\b{name}\b", text))
            callers -= len(re.findall(rf"def\s+{name}\b", text))
        if callers == 0:
            unwired.append(name)

    assert not unwired, (
        "these dashboard seeders are never called, so the menu opens showing a "
        f"value that is not the one in settings: {unwired}"
    )


def test_the_popover_opens_on_the_saved_values() -> None:
    """The concrete symptom: the slider claimed 0% while 88% was stored."""
    from puripuly_heart.ui.views import dashboard as dash_module

    dash = dash_module.DashboardView.__new__(dash_module.DashboardView)
    dash._overlay_text_background_alpha = 0.0
    dash._overlay_edge_style = "shadow"

    dash.set_overlay_text_background_alpha(0.88)
    dash.set_overlay_edge_style("outline")

    assert dash._overlay_text_background_alpha == 0.88
    assert dash._overlay_edge_style == "outline"

    # and the seed is driven from the same settings object the gate compares to
    controller_src = Path("src/puripuly_heart/ui/controller.py").read_text(
        encoding="utf-8"
    )
    for name, field in (
        ("set_overlay_text_background_alpha", "text_background_alpha"),
        ("set_overlay_edge_style", "edge_style"),
    ):
        marker = controller_src.index(name)
        window = controller_src[marker : marker + 500]
        assert f"visual.{field}" in window, (
            f"{name} is called but not from settings.overlay.desktop_flet."
            f"visual.{field}, so the control can still disagree with the gate"
        )


def test_the_rim_is_round_rather_than_square() -> None:
    """Written as (±1, ±1) the four corners sit √2 = 1.41 units out while the
    sides sit at 1, so the rim is a square and every glyph corner gets a 41%
    thicker edge than its sides. At the old fixed 1px that was 0.4px and
    antialiasing hid it; at 3–4px it shows as lumpy, squared-off corners.
    """
    offsets = overlay._caption_outline_offsets(56)
    radius = overlay._caption_outline_radius(56)

    outer = [o for o in offsets if (o[0] ** 2 + o[1] ** 2) ** 0.5 > radius * 0.75]
    assert len(outer) == 8, f"expected eight points on the outer ring, got {len(outer)}"
    for dx, dy in outer:
        reach = (dx**2 + dy**2) ** 0.5
        assert abs(reach - radius) < 0.01, (
            f"({dx:.2f}, {dy:.2f}) reaches {reach:.2f} but the ring radius is "
            f"{radius:.2f} — the rim is square, not round"
        )


def test_the_outline_colour_is_pinned_to_black() -> None:
    """The only colour assertion in this file used to be
    `s.color == _CAPTION_OUTLINE_COLOR`, which asserts the builder used the
    constant and says nothing about what the constant IS. Change it to white
    and every test still passed — which is the exact shape of "the outline
    renders in the text colour" shipping green.
    """
    assert overlay._CAPTION_OUTLINE_COLOR == "#E6000000", (
        "the outline is no longer black"
    )
    rim = overlay._CAPTION_OUTLINE_COLOR.upper()
    for name in ("_DESKTOP_CAPTION_WHITE", "_DESKTOP_CAPTION_GOLD"):
        text_colour = getattr(overlay, name).upper().lstrip("#")
        assert text_colour not in rim, (
            f"the rim is drawn in {name} — it would read as bolder text, not an "
            f"outline"
        )
    # opaque enough to read over bright footage
    assert int(rim[1:3], 16) >= 0xC0, "the rim is too transparent to register"


def test_outline_reaches_every_caption_widget_not_just_the_plan() -> None:
    """No test ever built a caption WIDGET with edge_style='outline' — the one
    widget-level shadow test calls build_desktop_caption_plan with no
    visual_state, so it only ever exercised the default. The whole span between
    _caption_text_shadow and the control was unasserted, across all five
    TextStyle construction sites.
    """
    import sys

    sys.path.insert(0, "tests/ui")
    from test_desktop_overlay_renderer import _block

    from puripuly_heart.core.overlay.protocol import OverlayPresentationSnapshot

    snapshot = OverlayPresentationSnapshot(
        blocks=[
            _block(
                "b1",
                channel="peer",
                block_variant="finalized",
                appearance_seq=1,
                primary_text="今日はゆっくり話してくれてありがとう",
                secondary_text="Thanks for speaking slowly today.",
                secondary_enabled=True,
            )
        ]
    )
    state = overlay.DesktopCaptionVisualState(edge_style="outline")
    plan = overlay.build_desktop_caption_plan(snapshot, visual_state=state)
    surface = overlay.build_desktop_caption_surface(plan)

    shadowed = []

    def walk(node, depth=0):
        if node is None or depth > 40:
            return
        style = getattr(node, "style", None)
        if style is not None and getattr(style, "shadow", None):
            shadowed.append(style.shadow)
        for attr in ("content", "controls"):
            child = getattr(node, attr, None)
            if isinstance(child, (list, tuple)):
                for item in child:
                    walk(item, depth + 1)
            elif child is not None:
                walk(child, depth + 1)

    walk(surface)

    assert shadowed, "no shadowed text found in the caption surface"
    for shadow in shadowed:
        assert len(shadow) == 16, (
            f"a caption control got {len(shadow)} shadows, not the outline's 16 "
            f"— it is still drawing the default edge style"
        )
        for entry in shadow:
            assert entry.color == overlay._CAPTION_OUTLINE_COLOR
            assert entry.blur_radius == 0


def test_the_change_gate_drops_a_value_that_equals_what_is_stored() -> None:
    """r370: the gate was only ever tested by grepping controller.py for
    substrings — nothing called it, so its actual behaviour was unasserted,
    including the case that produced the report.

    This is the second half of "I had to move it somewhere and back": a change
    is pushed only when it DIFFERS from what is saved, so a gesture landing on
    the stored value produces no payload, no log line and no repaint. That is
    correct behaviour — but only once the control is seeded from the same
    settings, which is what the other half of r370 fixes. Pinning both here so
    they cannot drift apart again.
    """
    import copy

    from puripuly_heart.config.settings import AppSettings
    from puripuly_heart.ui.controller import OVERLAY_TARGET_DESKTOP, GuiController

    controller = GuiController.__new__(GuiController)
    controller._active_overlay_target = OVERLAY_TARGET_DESKTOP
    controller._overlay_bridge = object()

    previous = AppSettings()
    previous.ui.overlay_enabled = True
    previous.overlay.desktop_flet.visual.text_background_alpha = 0.4
    previous.overlay.desktop_flet.validate()

    # (a) landing on the stored value -> nothing is sent
    same = copy.deepcopy(previous)
    same.overlay.desktop_flet.visual.text_background_alpha = 0.4
    assert controller._prepare_desktop_runtime_settings_update(previous, same) == [], (
        "a no-change gesture produced a payload"
    )

    # (b) a real change -> exactly one visual config carrying the new value
    changed = copy.deepcopy(previous)
    changed.overlay.desktop_flet.visual.text_background_alpha = 0.8
    controls = controller._prepare_desktop_runtime_settings_update(previous, changed)

    visual = [c for c in controls if c.get("command") == "apply_visual_config"]
    assert len(visual) == 1, f"expected one visual config, got {controls}"
    assert visual[0]["text_background_alpha"] == 0.8
    assert "edge_style" in visual[0], "the payload lost the edge style"

    # (c) the same, for the edge style
    restyled = copy.deepcopy(previous)
    restyled.overlay.desktop_flet.visual.edge_style = "outline"
    controls = controller._prepare_desktop_runtime_settings_update(previous, restyled)
    visual = [c for c in controls if c.get("command") == "apply_visual_config"]
    assert len(visual) == 1
    assert visual[0]["edge_style"] == "outline"
