from __future__ import annotations

import argparse
import asyncio
import contextlib
import inspect
import json
import logging
import math
import os
import re
import subprocess
import sys
import time
import traceback
from collections.abc import Awaitable, Callable
from concurrent.futures import Future as ConcurrentFuture
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import urlsplit

import websockets
from websockets.exceptions import ConnectionClosed

from puripuly_heart.config.settings import (
    DESKTOP_FLET_DEFAULT_BACKGROUND_ALPHA,
    DESKTOP_FLET_DEFAULT_HEIGHT,
    DESKTOP_FLET_DEFAULT_SIZE_PRESET,
    DESKTOP_FLET_DEFAULT_TEXT_SCALE,
    DESKTOP_FLET_DEFAULT_WIDTH,
    DESKTOP_FLET_MAX_BACKGROUND_ALPHA,
    DESKTOP_FLET_MAX_OUTLINE_WIDTH,
    DESKTOP_FLET_MAX_TEXT_SCALE,
    DESKTOP_FLET_MIN_BACKGROUND_ALPHA,
    DESKTOP_FLET_MIN_HEIGHT,
    DESKTOP_FLET_MIN_OUTLINE_WIDTH,
    DESKTOP_FLET_MIN_TEXT_SCALE,
    DESKTOP_FLET_MIN_WIDTH,
    DESKTOP_FLET_SIZE_PRESET_DISPLAY_ORDER,
    DESKTOP_FLET_SIZE_PRESET_ORDER,
    DESKTOP_FLET_SIZE_PRESETS,
    DesktopFletOverlayVisualSettings,
)
from puripuly_heart.core.overlay.manifest import (
    OVERLAY_CONTRACT_VERSION,
    OverlayLaunchManifest,
    normalize_overlay_logging_mode,
)
from puripuly_heart.core.overlay.protocol import (
    OverlayPresentationBlock,
    OverlayPresentationSnapshot,
)
from puripuly_heart.ui.fonts import assets_dir
from puripuly_heart.ui.i18n import t_for_locale

logger = logging.getLogger(__name__)

_LOOPBACK_BRIDGE_HOSTS = {"127.0.0.1", "::1"}
_SENSITIVE_EVENT_KEYS = {
    "accesstoken",
    "apikey",
    "authorization",
    "authorizationheader",
    "bearer",
    "secret",
    "sessiontoken",
    "token",
}
_STARTUP_FAILURE_EXIT_CODE = 1
_RUNTIME_FAILURE_EXIT_CODE = 1
_SUCCESS_EXIT_CODE = 0
_REQUIRED_MANIFEST_STRING_FIELDS = {
    "app_version",
    "bridge_url",
    "locale",
    "log_dir",
    "log_level",
    "logging_mode",
    "overlay_instance_id",
    "session_token",
}
_REQUIRED_MANIFEST_INT_FIELDS = {"contract_version", "parent_pid", "startup_deadline_ms"}
_DESKTOP_CAPTION_WHITE = "#FFFFFF"
_DESKTOP_CAPTION_GOLD = "#FFD700"
_DESKTOP_CAPTION_PEER_SOURCE = "#c8b4ff"  # light purple for peer source/pinyin lines
# Your own (self/typed) lines get their own colored scheme so they read like the
# peer captions instead of plain white: a light blue source + mint translation,
# distinct from the peer's purple/gold so you can still tell who said what.
_DESKTOP_CAPTION_SELF_SOURCE = "#a9d6ff"  # light blue for self source/typed lines
_DESKTOP_CAPTION_SELF_TRANSLATION = "#6fe9cf"  # mint for self translation lines
_DESKTOP_CAPTION_LATIN_FONT_FAMILY = "Noto Sans"
_DESKTOP_CAPTION_CJK_FONT_FAMILY = "Noto Sans CJK JP"
_DESKTOP_CAPTION_CJK_LANGUAGE_PRIMARY_SUBTAGS = frozenset(
    {"ko", "kor", "ja", "jpn", "zh", "zho", "chi", "cmn", "yue"}
)
_DESKTOP_CAPTION_BACKGROUND_RGB = "000000"
_DESKTOP_CAPTION_TRANSPARENT = "transparent"
_DESKTOP_CAPTION_MAX_VISIBLE_SLOTS = 2
_DESKTOP_CAPTION_MAX_VISIBLE_LINES = 16
_DESKTOP_CAPTION_PRIMARY_MAX_LINES = 6
_DESKTOP_CAPTION_SECONDARY_MAX_LINES = 6
_DESKTOP_CAPTION_LINE_HEIGHT = 1.24
_DESKTOP_CAPTION_PRIMARY_REGION_ALIGNMENT_Y = -0.5
_DESKTOP_CAPTION_TEXT_STACK_ALIGNMENT_Y = -0.08
_DESKTOP_CAPTION_MIN_DYNAMIC_CARD_WIDTH = 320.0
_DESKTOP_CAPTION_DYNAMIC_WIDTH_SAFETY = 24.0
_DESKTOP_CAPTION_CJK_WIDTH_EM = 1.0
_DESKTOP_CAPTION_LATIN_WIDE_WIDTH_EM = 0.62
_DESKTOP_CAPTION_LATIN_NARROW_WIDTH_EM = 0.42
_DESKTOP_CAPTION_SPACE_WIDTH_EM = 0.32
_DESKTOP_CAPTION_PUNCT_WIDTH_EM = 0.38
_DESKTOP_CAPTION_EMOJI_WIDTH_EM = 1.15
_DESKTOP_CAPTION_CONTACT_SHADOW_COLOR = "#C0000000"
_DESKTOP_CAPTION_CONTACT_SHADOW_OFFSET = (0, 1)
_DESKTOP_CAPTION_CONTACT_SHADOW_BLUR = 1.0
_DESKTOP_CAPTION_AMBIENT_SHADOW_COLOR = "#66000000"
_DESKTOP_CAPTION_AMBIENT_SHADOW_OFFSET = (0, 0)
_DESKTOP_CAPTION_AMBIENT_SHADOW_BLUR = 3.0
_DESKTOP_CAPTION_OVERFLOW_STRATEGY = (
    "two-turn-slots:presenter-selected-blocks,primary-two-lines,secondary-one-line"
)
_DESKTOP_INTERACTION_MODE_EDIT = "edit"
_DESKTOP_INTERACTION_MODE_PASS_THROUGH = "pass_through"
_DESKTOP_INTERACTION_MODES = {
    _DESKTOP_INTERACTION_MODE_EDIT,
    _DESKTOP_INTERACTION_MODE_PASS_THROUGH,
}
_DESKTOP_WINDOW_BOUNDS_EVENT_NAMES = {"MOVE", "MOVED", "RESIZE", "RESIZED"}
_INITIAL_RUNTIME_CONTROL_DRAIN_TIMEOUT_S = 0.05
_PROGRAMMATIC_BOUNDS_ECHO_SUPPRESSION_S = 0.25
_PROGRAMMATIC_BOUNDS_ECHO_TOLERANCE_PX = 2.0
_DESKTOP_PREVIEW_BACKGROUND_ALPHA_PRESETS = (0.35, 0.5, 0.6, 0.8)
_DESKTOP_PREVIEW_DEFAULT_BACKGROUND_ALPHA = DESKTOP_FLET_DEFAULT_BACKGROUND_ALPHA
_DESKTOP_PREVIEW_DEFAULT_BACKGROUND_SURFACE_ID = "bright"
_DESKTOP_PREVIEW_STAGE_WIDTH = 1180
_DESKTOP_PREVIEW_STAGE_HEIGHT = 420
_DESKTOP_PREVIEW_BACKGROUND_SURFACE_DATA = (
    ("bright", "settings.overlay.desktop.preview.background_surface.bright", "#FFFFFF"),
    ("dark", "settings.overlay.desktop.preview.background_surface.dark", "#111827"),
    ("busy", "settings.overlay.desktop.preview.background_surface.busy", "#1F2937"),
)
_DESKTOP_EMPTY_LOCK_ACTION_I18N_KEY = "settings.overlay.desktop.empty_state.action.lock"
_DESKTOP_EMPTY_LOCK_ACTION_DEFAULT_LABEL = "Lock"
_DESKTOP_EMPTY_LOCK_ACTION_DEFAULT_COLOR = "#FFF8F4"
_DESKTOP_EMPTY_LOCK_ACTION_FOCUS_COLOR = "#FF6B6B"
# Teal accent used to make "edit mode" obvious (matches the app's primary accent).
_DESKTOP_EDIT_ACCENT_COLOR = "#3dd7c0"
_DESKTOP_EDIT_HINT_I18N_KEY = "settings.overlay.desktop.empty_state.hint"
_DESKTOP_OVERLAY_ACTIVE_BANNER_I18N_KEY = "settings.overlay.desktop.active_banner"
# Timed from when the window is REVEALED (not app start), so the visible duration is
# consistent regardless of how long startup took. Held at full opacity, then faded out
# via Flet's native opacity animation (smooth ~60fps, not a stepped re-render).
_DESKTOP_ACTIVE_BANNER_VISIBLE_SECONDS = 0.8
_DESKTOP_ACTIVE_BANNER_FADE_SECONDS = 0.45
_DESKTOP_EMPTY_LOCK_ACTION_MIN_HIT_TARGET = 44
_DESKTOP_EMPTY_LOCK_ACTION_HORIZONTAL_PADDING = 28
_DESKTOP_EMPTY_LOCK_ACTION_VERTICAL_PADDING = 12
_DESKTOP_EMPTY_LOCK_ACTION_TEXT_WIDTH_SAFETY = 24


def _desktop_caption_color_for_channel(channel: str) -> str:
    if channel == "peer":
        return _DESKTOP_CAPTION_GOLD
    return _DESKTOP_CAPTION_WHITE


@dataclass(frozen=True, slots=True)
class DesktopCaptionMappingRule:
    snapshot_field: str
    block_type: str
    role: str
    slot: str
    promoted: bool
    color: str
    priority: str
    truncation: str


# Reviewable snapshot mapping table required before renderer coding.
# Current contract inspected in core.overlay.protocol/state:
# OverlayPresentationSnapshot(revision, calibration, blocks[]), where blocks[]
# contains OverlayPresentationBlock(channel self|peer, block_variant
# active_self|active_peer|finalized, primary_text, secondary_text,
# secondary_enabled, appearance_seq). Desktop visual sizing is owned by repaired
# desktop visual settings/runtime controls, so snapshot.calibration is not mapped
# to desktop caption visual state.
DESKTOP_CAPTION_MAPPING_TABLE: tuple[DesktopCaptionMappingRule, ...] = (
    DesktopCaptionMappingRule(
        snapshot_field="blocks[]",
        block_type="active_self/self",
        role="active_self_source",
        slot="primary",
        promoted=False,
        color=_DESKTOP_CAPTION_WHITE,
        priority="100 newest active/interim source",
        truncation="max 2 lines; retained before secondary and finalized lines",
    ),
    DesktopCaptionMappingRule(
        snapshot_field="blocks[]",
        block_type="active_self/self",
        role="active_self_translation",
        slot="secondary",
        promoted=False,
        color=_desktop_caption_color_for_channel("self"),
        priority="85 active/interim secondary",
        truncation="max 1 line; drops before active primary",
    ),
    DesktopCaptionMappingRule(
        snapshot_field="blocks[]",
        block_type="active_peer/peer",
        role="active_peer_source",
        slot="primary",
        promoted=True,
        color=_desktop_caption_color_for_channel("peer"),
        priority="95 newest active/interim peer source",
        truncation="max 2 lines; retained before finalized secondary lines",
    ),
    DesktopCaptionMappingRule(
        snapshot_field="blocks[]",
        block_type="finalized/peer translated",
        role="peer_translation",
        slot="primary",
        promoted=False,
        color=_DESKTOP_CAPTION_GOLD,
        priority="90 peer translated primary; newer appearance wins ties",
        truncation="max 2 lines; outranks older finalized source/self lines",
    ),
    DesktopCaptionMappingRule(
        snapshot_field="blocks[]",
        block_type="finalized/peer translated",
        role="peer_source_original",
        slot="secondary",
        promoted=False,
        color=_desktop_caption_color_for_channel("peer"),
        priority="70 peer original/source secondary",
        truncation="max 1 line; drops before peer translated primary",
    ),
    DesktopCaptionMappingRule(
        snapshot_field="blocks[]",
        block_type="finalized/peer source-only",
        role="peer_source_original",
        slot="primary",
        promoted=True,
        color=_desktop_caption_color_for_channel("peer"),
        priority="60 peer source-only finalized",
        truncation="max 2 lines; drops before active and translated primary lines",
    ),
    DesktopCaptionMappingRule(
        snapshot_field="blocks[]",
        block_type="finalized/self",
        role="self_source",
        slot="primary",
        promoted=False,
        color=_DESKTOP_CAPTION_WHITE,
        priority="65 self/source finalized; newer appearance wins ties",
        truncation="max 2 lines; older finalized drops first",
    ),
    DesktopCaptionMappingRule(
        snapshot_field="blocks[]",
        block_type="finalized/self",
        role="self_translation",
        slot="secondary",
        promoted=False,
        color=_desktop_caption_color_for_channel("self"),
        priority="50 self translation secondary",
        truncation="max 1 line; drops before finalized primary lines",
    ),
    DesktopCaptionMappingRule(
        snapshot_field="blocks[]",
        block_type="finalized/self secondary-only",
        role="self_translation",
        slot="primary",
        promoted=True,
        color=_desktop_caption_color_for_channel("self"),
        priority="55 self translation secondary-only promoted primary",
        truncation="max 2 lines; drops before active and peer translated primary lines",
    ),
    DesktopCaptionMappingRule(
        snapshot_field="calibration",
        block_type="all",
        role="desktop_visual_ignored",
        slot="none",
        promoted=False,
        color="none",
        priority="not rendered",
        truncation="desktop caption visual state comes from repaired desktop visual config",
    ),
    DesktopCaptionMappingRule(
        snapshot_field="blocks[]",
        block_type="none/edit",
        role="edit_no_caption_empty_card",
        slot="none",
        promoted=False,
        color="none",
        priority="0 edit-mode empty caption surface",
        truncation="renders empty caption card with centered lock text action",
    ),
    DesktopCaptionMappingRule(
        snapshot_field="blocks[]",
        block_type="none/pass_through",
        role="pass_through_no_caption",
        slot="none",
        promoted=False,
        color="none",
        priority="not rendered",
        truncation="renders no text and no background",
    ),
)


@dataclass(frozen=True, slots=True)
class DesktopCaptionSizePreset:
    id: str
    window_width: int
    window_height: int
    primary_font_size: int
    secondary_font_size: int
    padding_horizontal: int
    padding_vertical: int
    border_radius: int
    slot_gap: int


_DESKTOP_CAPTION_SIZE_PRESETS: dict[str, DesktopCaptionSizePreset] = {
    "tiny":   DesktopCaptionSizePreset("tiny",   640,  160, 20, 20, 10, 2,  10, 4),
    "xsmall": DesktopCaptionSizePreset("xsmall", 960,  240, 29, 29, 14, 6,  12, 6),
    "small":  DesktopCaptionSizePreset("small",  1152, 288, 35, 35, 18, 8,  14, 8),
    "medium": DesktopCaptionSizePreset("medium", 1344, 336, 41, 41, 22, 10, 16, 10),
    "large":  DesktopCaptionSizePreset("large",  1600, 400, 50, 50, 26, 12, 18, 12),
    "xlarge": DesktopCaptionSizePreset("xlarge", 1792, 448, 56, 56, 30, 14, 20, 14),
}


@dataclass(frozen=True, slots=True)
class DesktopCaptionVisualState:
    text_scale: float = DESKTOP_FLET_DEFAULT_TEXT_SCALE
    background_alpha: float = DESKTOP_FLET_DEFAULT_BACKGROUND_ALPHA
    outline_width: float | None = None
    # Whether romanization (pinyin/romaji) is shown — used so the edit-mode SAMPLE
    # matches the user's actual overlay romanization setting.
    show_romanization: bool = True
    # Whether single-turn mode is active — controls how many slots the sample shows.
    single_turn_mode: bool = True
    # Display toggles mirrored from presenter — used to make the sample match live output.
    show_translation: bool = True
    show_peer_original: bool = True
    # The peer's spoken language and the language their speech is translated INTO
    # (the user's reading language). Used only so the edit-mode SAMPLE caption reflects
    # the user's actual dashboard language choices instead of a fixed zh→en string.
    peer_source_language: str = ""
    peer_target_language: str = ""


@dataclass(frozen=True, slots=True)
class DesktopCaptionLine:
    text: str
    role: str
    slot: str
    color: str
    priority: int
    block_id: str
    channel: str
    block_variant: str
    appearance_seq: int
    max_lines: int
    font_size: int
    font_family: str | None
    line_height: float = _DESKTOP_CAPTION_LINE_HEIGHT
    weight: str = "semibold"
    promoted: bool = False
    active: bool = False


@dataclass(frozen=True, slots=True)
class DesktopCaptionSlot:
    block_id: str
    occupant_key: str
    channel: str
    block_variant: str
    appearance_seq: int
    lines: tuple[DesktopCaptionLine, ...]
    secondary_enabled: bool
    card_width: float = 0.0
    card_text_width: float = 0.0
    active: bool = False


@dataclass(frozen=True, slots=True)
class DesktopCaptionPlan:
    slots: tuple[DesktopCaptionSlot, ...]
    lines: tuple[DesktopCaptionLine, ...]
    size_preset: str
    window_width: int
    window_height: int
    text_width: int
    primary_font_size: int
    secondary_font_size: int
    outline_width: float
    padding_horizontal: int
    padding_vertical: int
    slot_gap: int
    slot_height: float
    primary_region_height: float
    secondary_region_height: float
    border_radius: int
    background_alpha: float
    background_color: str
    surface_visible: bool
    full_window_background_visible: bool
    no_scrollbars: bool = True
    max_visible_lines: int = _DESKTOP_CAPTION_MAX_VISIBLE_LINES
    max_visible_slots: int = _DESKTOP_CAPTION_MAX_VISIBLE_SLOTS
    secondary_line_max_lines: int = _DESKTOP_CAPTION_SECONDARY_MAX_LINES
    overflow_strategy: str = _DESKTOP_CAPTION_OVERFLOW_STRATEGY


@dataclass(frozen=True, slots=True)
class DesktopOverlayPreviewFixture:
    id: str
    label: str
    i18n_key: str
    snapshot: OverlayPresentationSnapshot
    coverage_tags: frozenset[str]


@dataclass(frozen=True, slots=True)
class DesktopOverlayPreviewSizePreset:
    id: str
    label: str
    i18n_key: str
    window_width: int
    window_height: int
    primary_font_size: int
    secondary_font_size: int
    padding_horizontal: int
    padding_vertical: int
    border_radius: int


@dataclass(frozen=True, slots=True)
class DesktopOverlayPreviewBackgroundSurface:
    id: str
    label: str
    i18n_key: str
    bgcolor: str


@dataclass(frozen=True, slots=True)
class DesktopOverlayPreviewLabels:
    fixture: str
    size_preset: str
    background_alpha: str
    background_surface: str


@dataclass(frozen=True, slots=True)
class DesktopOverlayPreviewCatalog:
    fixtures: tuple[DesktopOverlayPreviewFixture, ...]
    background_surfaces: tuple[DesktopOverlayPreviewBackgroundSurface, ...]
    size_presets: tuple[DesktopOverlayPreviewSizePreset, ...]
    background_alpha_presets: tuple[float, ...]
    labels: DesktopOverlayPreviewLabels


@dataclass(frozen=True, slots=True)
class DesktopOverlayPreviewFixtureDataSource:
    source_kind: str
    module: str
    package_data_globs: tuple[str, ...] = ()
    hiddenimports: tuple[str, ...] = ()


_DESKTOP_PREVIEW_SECRET_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "bearer_token",
        re.compile(r"\bBearer\s+[A-Za-z0-9._~+/=-]{8,}\b", re.IGNORECASE),
    ),
    (
        "api_key",
        re.compile(r"\b(?:sk|rk|pk)-(?:live|prod|test)?-?[A-Za-z0-9_-]{12,}\b", re.IGNORECASE),
    ),
)


def build_desktop_caption_plan(
    snapshot: OverlayPresentationSnapshot,
    *,
    window_width: int | float = DESKTOP_FLET_DEFAULT_WIDTH,
    window_height: int | float = DESKTOP_FLET_DEFAULT_HEIGHT,
    visual_state: DesktopCaptionVisualState | None = None,
    interaction_mode: str = "pass_through",
    locale: str | None = None,
) -> DesktopCaptionPlan:
    """Map the current overlay snapshot contract into a deterministic caption plan."""

    width = _positive_int_or_default(window_width, DESKTOP_FLET_DEFAULT_WIDTH)
    height = _positive_int_or_default(window_height, DESKTOP_FLET_DEFAULT_HEIGHT)
    visual = _validated_visual_state(visual_state)
    preset = _desktop_caption_size_preset_for_dimensions(width, height)
    primary_font_size = preset.primary_font_size
    secondary_font_size = preset.secondary_font_size
    outline_width = 0.0
    _ = locale

    candidate_slots = _caption_slots_for_snapshot(
        snapshot,
        primary_font_size=primary_font_size,
        secondary_font_size=secondary_font_size,
    )
    # Two-turn disabled — always one caption slot (single-turn).
    max_slots = 1  # was: 1 if visual.single_turn_mode else _DESKTOP_CAPTION_MAX_VISIBLE_SLOTS
    slots = tuple(
        _caption_slot_with_dynamic_width(
            slot,
            padding_horizontal=preset.padding_horizontal,
            max_card_width=width,
        )
        for slot in candidate_slots[:max_slots]
    )
    lines = tuple(line for slot in slots for line in slot.lines)

    full_window_background_visible = interaction_mode == _DESKTOP_INTERACTION_MODE_EDIT
    surface_visible = bool(slots) or full_window_background_visible
    background_alpha = 0.0
    if surface_visible:
        background_alpha = visual.background_alpha
    n_active_slots = max(1, min(len(slots), _DESKTOP_CAPTION_MAX_VISIBLE_SLOTS))
    slot_height = max(
        1.0,
        (float(height) - preset.slot_gap) / n_active_slots,
    )
    primary_region_height = (
        primary_font_size * _DESKTOP_CAPTION_LINE_HEIGHT * _DESKTOP_CAPTION_PRIMARY_MAX_LINES
    )
    secondary_region_height = (
        secondary_font_size * _DESKTOP_CAPTION_LINE_HEIGHT * _DESKTOP_CAPTION_SECONDARY_MAX_LINES
    )
    return DesktopCaptionPlan(
        slots=slots,
        lines=lines,
        size_preset=preset.id,
        window_width=width,
        window_height=height,
        text_width=max(1, width - (preset.padding_horizontal * 2)),
        primary_font_size=primary_font_size,
        secondary_font_size=secondary_font_size,
        outline_width=outline_width,
        padding_horizontal=preset.padding_horizontal,
        padding_vertical=preset.padding_vertical,
        slot_gap=preset.slot_gap,
        slot_height=slot_height,
        primary_region_height=primary_region_height,
        secondary_region_height=secondary_region_height,
        border_radius=preset.border_radius,
        background_alpha=background_alpha,
        background_color=_caption_background_color(background_alpha),
        surface_visible=surface_visible,
        full_window_background_visible=full_window_background_visible,
    )


def desktop_empty_lock_action_label(locale: str | None) -> str:
    return t_for_locale(
        locale,
        _DESKTOP_EMPTY_LOCK_ACTION_I18N_KEY,
        default=_DESKTOP_EMPTY_LOCK_ACTION_DEFAULT_LABEL,
    )


def _desktop_empty_lock_action_font_size(plan: DesktopCaptionPlan) -> int:
    return max(_DESKTOP_EMPTY_LOCK_ACTION_MIN_HIT_TARGET, plan.primary_font_size)


def _desktop_empty_lock_action_width(label: str, font_size: int) -> float:
    return max(
        _DESKTOP_EMPTY_LOCK_ACTION_MIN_HIT_TARGET,
        _estimated_caption_line_width(label, font_size)
        + (_DESKTOP_EMPTY_LOCK_ACTION_HORIZONTAL_PADDING * 2)
        + _DESKTOP_EMPTY_LOCK_ACTION_TEXT_WIDTH_SAFETY,
    )


def build_desktop_empty_lock_action(
    plan: DesktopCaptionPlan,
    *,
    label: str,
    on_click: Callable[[object], object] | None,
) -> Any:
    """Build the bounded text-only lock action shown in empty moving mode."""

    import flet as ft

    font_size = _desktop_empty_lock_action_font_size(plan)
    text_style = ft.TextStyle(
        size=font_size,
        height=1.0,
        weight=ft.FontWeight.BOLD,
        font_family=_desktop_caption_font_family_for_text(label),
        shadow=_caption_text_shadow(ft),
        decoration=None,
    )
    return ft.TextButton(
        text=label,
        tooltip=label,
        on_click=on_click,
        width=_desktop_empty_lock_action_width(label, font_size),
        height=max(
            _DESKTOP_EMPTY_LOCK_ACTION_MIN_HIT_TARGET,
            font_size + (_DESKTOP_EMPTY_LOCK_ACTION_VERTICAL_PADDING * 2),
        ),
        style=ft.ButtonStyle(
            color={
                ft.ControlState.DEFAULT: _DESKTOP_EMPTY_LOCK_ACTION_DEFAULT_COLOR,
                ft.ControlState.HOVERED: _DESKTOP_EMPTY_LOCK_ACTION_FOCUS_COLOR,
                ft.ControlState.FOCUSED: _DESKTOP_EMPTY_LOCK_ACTION_FOCUS_COLOR,
            },
            bgcolor=ft.Colors.TRANSPARENT,
            overlay_color=ft.Colors.TRANSPARENT,
            elevation=0,
            padding=ft.padding.symmetric(
                horizontal=_DESKTOP_EMPTY_LOCK_ACTION_HORIZONTAL_PADDING,
                vertical=_DESKTOP_EMPTY_LOCK_ACTION_VERTICAL_PADDING,
            ),
            text_style=text_style,
            mouse_cursor=ft.MouseCursor.CLICK,
            animation_duration=0,
        ),
    )


def build_desktop_caption_surface(plan: DesktopCaptionPlan) -> Any:
    """Build no-outline fixed-slot Flet caption controls from a caption plan."""

    import flet as ft

    stack_controls: list[Any] = []
    if plan.full_window_background_visible:
        stack_controls.append(
            ft.Container(
                bgcolor=plan.background_color,
                border_radius=plan.border_radius,
                alignment=ft.alignment.center,
                left=0,
                top=0,
                right=0,
                bottom=0,
            )
        )
    slot_controls = [_build_flet_caption_slot(ft, plan, slot) for slot in plan.slots]
    if slot_controls:
        slot_stack_height = (plan.slot_height * len(slot_controls)) + (
            plan.slot_gap * max(0, len(slot_controls) - 1)
        )
        stack_controls.append(
            ft.Column(
                controls=slot_controls,
                horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                alignment=ft.MainAxisAlignment.CENTER,
                spacing=plan.slot_gap,
                tight=True,
                width=plan.window_width,
                height=slot_stack_height,
            )
        )
    return ft.Container(
        content=ft.Stack(
            controls=stack_controls,
            width=plan.window_width,
            height=plan.window_height,
        ),
        width=plan.window_width,
        height=plan.window_height,
        bgcolor=ft.Colors.TRANSPARENT,
        border_radius=plan.border_radius,
        alignment=ft.alignment.center,
        visible=plan.surface_visible,
    )


def build_desktop_transparent_sizing_host(plan: DesktopCaptionPlan) -> Any:
    """Build a transparent, layout-stable host for locked empty runtime state."""

    import flet as ft

    return ft.Container(
        width=plan.window_width,
        height=plan.window_height,
        bgcolor=ft.Colors.TRANSPARENT,
        alignment=ft.alignment.center,
    )


def build_desktop_overlay_preview_catalog(
    *,
    locale: str | None = None,
) -> DesktopOverlayPreviewCatalog:
    """Return local-only desktop overlay preview fixtures and visual presets."""

    def text(key: str) -> str:
        return t_for_locale(locale, key)

    fixtures = tuple(
        DesktopOverlayPreviewFixture(
            id=fixture_id,
            i18n_key=i18n_key,
            label=text(i18n_key),
            snapshot=snapshot,
            coverage_tags=frozenset(coverage_tags),
        )
        for fixture_id, i18n_key, snapshot, coverage_tags in _desktop_preview_fixture_data()
    )
    size_presets = tuple(
        _preview_size_preset(preset_id, locale=locale)
        for preset_id in DESKTOP_FLET_SIZE_PRESET_DISPLAY_ORDER
    )
    background_surfaces = tuple(
        DesktopOverlayPreviewBackgroundSurface(
            id=surface_id,
            i18n_key=i18n_key,
            label=text(i18n_key),
            bgcolor=bgcolor,
        )
        for surface_id, i18n_key, bgcolor in _DESKTOP_PREVIEW_BACKGROUND_SURFACE_DATA
    )
    labels = DesktopOverlayPreviewLabels(
        fixture=text("settings.overlay.desktop.preview.fixture"),
        size_preset=text("settings.overlay.desktop.size.title"),
        background_alpha=text("settings.overlay.desktop.preview.background_alpha"),
        background_surface=text("settings.overlay.desktop.preview.background_surface"),
    )
    return DesktopOverlayPreviewCatalog(
        fixtures=fixtures,
        background_surfaces=background_surfaces,
        size_presets=size_presets,
        background_alpha_presets=_DESKTOP_PREVIEW_BACKGROUND_ALPHA_PRESETS,
        labels=labels,
    )


def _preview_size_preset(
    preset_id: str,
    *,
    locale: str | None,
) -> DesktopOverlayPreviewSizePreset:
    preset = _DESKTOP_CAPTION_SIZE_PRESETS[preset_id]
    i18n_key = f"settings.overlay.desktop.size.option.{preset_id}"
    return DesktopOverlayPreviewSizePreset(
        id=preset.id,
        label=t_for_locale(locale, i18n_key),
        i18n_key=i18n_key,
        window_width=preset.window_width,
        window_height=preset.window_height,
        primary_font_size=preset.primary_font_size,
        secondary_font_size=preset.secondary_font_size,
        padding_horizontal=preset.padding_horizontal,
        padding_vertical=preset.padding_vertical,
        border_radius=preset.border_radius,
    )


def preview_fixture_secret_findings(
    catalog: DesktopOverlayPreviewCatalog | None = None,
) -> tuple[str, ...]:
    """Return redacted diagnostics for credential-like preview fixture content."""

    catalog = catalog or build_desktop_overlay_preview_catalog(locale="en")
    findings: list[str] = []
    for fixture in catalog.fixtures:
        fixture_identifier = _safe_preview_fixture_identifier(fixture.id)
        for field_path, value in _iter_preview_guard_strings(
            _preview_fixture_guard_payload(fixture)
        ):
            for pattern_name, pattern in _DESKTOP_PREVIEW_SECRET_PATTERNS:
                if pattern.search(value):
                    findings.append(
                        f"fixture {fixture_identifier} field {field_path} matched {pattern_name}"
                    )
    for field_path, value in _iter_preview_guard_strings(
        _preview_catalog_control_guard_payload(catalog)
    ):
        for pattern_name, pattern in _DESKTOP_PREVIEW_SECRET_PATTERNS:
            if pattern.search(value):
                findings.append(f"preview catalog field {field_path} matched {pattern_name}")
    return tuple(findings)


def desktop_overlay_preview_fixture_data_sources() -> tuple[
    DesktopOverlayPreviewFixtureDataSource,
    ...,
]:
    """Describe preview fixture data sources for packaging readiness checks."""

    return (
        DesktopOverlayPreviewFixtureDataSource(
            source_kind="embedded_python_module",
            module=__name__,
        ),
    )


def _preview_fixture_guard_payload(fixture: DesktopOverlayPreviewFixture) -> dict[str, object]:
    return {
        "id": fixture.id,
        "label": fixture.label,
        "i18n_key": fixture.i18n_key,
        "coverage_tags": tuple(sorted(fixture.coverage_tags)),
        "snapshot": fixture.snapshot.to_dict(),
    }


def _preview_catalog_control_guard_payload(
    catalog: DesktopOverlayPreviewCatalog,
) -> dict[str, object]:
    return {
        "background_surfaces": tuple(
            {
                "id": surface.id,
                "label": surface.label,
                "i18n_key": surface.i18n_key,
                "bgcolor": surface.bgcolor,
            }
            for surface in catalog.background_surfaces
        ),
        "size_presets": tuple(
            {
                "id": preset.id,
                "label": preset.label,
                "i18n_key": preset.i18n_key,
                "window_width": preset.window_width,
                "window_height": preset.window_height,
                "primary_font_size": preset.primary_font_size,
                "secondary_font_size": preset.secondary_font_size,
                "padding_horizontal": preset.padding_horizontal,
                "padding_vertical": preset.padding_vertical,
                "border_radius": preset.border_radius,
            }
            for preset in catalog.size_presets
        ),
        "background_alpha_presets": tuple(catalog.background_alpha_presets),
        "labels": {
            "fixture": catalog.labels.fixture,
            "size_preset": catalog.labels.size_preset,
            "background_alpha": catalog.labels.background_alpha,
            "background_surface": catalog.labels.background_surface,
        },
    }


def _iter_preview_guard_strings(value: object, path: str = "") -> tuple[tuple[str, str], ...]:
    strings: list[tuple[str, str]] = []
    if isinstance(value, str):
        strings.append((path, value))
    elif isinstance(value, dict):
        for key, item in value.items():
            key_path = str(key) if not path else f"{path}.{key}"
            strings.extend(_iter_preview_guard_strings(item, key_path))
    elif isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            strings.extend(_iter_preview_guard_strings(item, f"{path}[{index}]"))
    return tuple(strings)


def _safe_preview_fixture_identifier(fixture_id: str) -> str:
    for _, pattern in _DESKTOP_PREVIEW_SECRET_PATTERNS:
        if pattern.search(fixture_id):
            return "<redacted-fixture-id>"
    return fixture_id


def _desktop_preview_fixture_data() -> tuple[
    tuple[str, str, OverlayPresentationSnapshot, frozenset[str]],
    ...,
]:
    return (
        (
            "korean_long_wrap",
            "settings.overlay.desktop.preview.fixture.korean_long_wrap",
            OverlayPresentationSnapshot(
                revision=1,
                blocks=[
                    _preview_block(
                        "preview-ko-long-active-self",
                        channel="self",
                        block_variant="active_self",
                        appearance_seq=10,
                        primary_text=(
                            "긴 문장 미리보기입니다. 한국어 자막이 화면 너비에 맞춰 "
                            "자연스럽게 줄바꿈되는지 확인하기 위해 일부러 길게 작성했습니다. "
                            "밝은 배경에서도 반투명 자막 카드가 읽기 쉬운지 살펴보세요."
                        ),
                        secondary_text=(
                            "This long Korean sample checks wrapping, source color, "
                            "and the secondary translation line."
                        ),
                        secondary_enabled=True,
                    )
                ],
            ),
            frozenset({"ko", "en", "self", "primary", "secondary", "active", "long_wrap"}),
        ),
        (
            "japanese_peer_finalized",
            "settings.overlay.desktop.preview.fixture.japanese_peer_finalized",
            OverlayPresentationSnapshot(
                revision=2,
                blocks=[
                    _preview_block(
                        "preview-ja-peer-finalized",
                        channel="peer",
                        block_variant="finalized",
                        appearance_seq=20,
                        primary_text="今日はゆっくり話してくれてありがとう。字幕カードも見やすいです。",
                        secondary_text="Thanks for speaking slowly today. The caption card is easy to read.",
                        secondary_enabled=True,
                    )
                ],
            ),
            frozenset({"ja", "en", "peer", "primary", "secondary", "finalized"}),
        ),
        (
            "chinese_self_finalized",
            "settings.overlay.desktop.preview.fixture.chinese_self_finalized",
            OverlayPresentationSnapshot(
                revision=3,
                blocks=[
                    _preview_block(
                        "preview-zh-self-finalized",
                        channel="self",
                        block_variant="finalized",
                        appearance_seq=30,
                        primary_text="我这边的桌面字幕会保持居中，并且在深色背景上也要清晰。",
                        secondary_text="My desktop captions stay centered and readable on dark backgrounds.",
                        secondary_enabled=True,
                    )
                ],
            ),
            frozenset({"zh-CN", "en", "self", "primary", "secondary", "finalized"}),
        ),
        (
            "english_active_peer",
            "settings.overlay.desktop.preview.fixture.english_active_peer",
            OverlayPresentationSnapshot(
                revision=4,
                blocks=[
                    _preview_block(
                        "preview-en-active-peer",
                        channel="peer",
                        block_variant="active_peer",
                        appearance_seq=40,
                        primary_text="",
                        secondary_text="Live peer captions are arriving right now...",
                        secondary_enabled=True,
                    )
                ],
            ),
            frozenset({"en", "peer", "primary", "active"}),
        ),
        (
            "mixed_script_emoji",
            "settings.overlay.desktop.preview.fixture.mixed_script_emoji",
            OverlayPresentationSnapshot(
                revision=5,
                blocks=[
                    _preview_block(
                        "preview-mixed-emoji-peer",
                        channel="peer",
                        block_variant="finalized",
                        appearance_seq=50,
                        primary_text="今日は PuriPuly Heart 좋아요 你好 😊✨",
                        secondary_text="Mixed source: hello, 안녕, こんにちは, 你好 🎮",
                        secondary_enabled=True,
                    )
                ],
            ),
            frozenset(
                {
                    "mixed_script",
                    "emoji",
                    "en",
                    "ko",
                    "ja",
                    "zh-CN",
                    "peer",
                    "primary",
                    "secondary",
                    "finalized",
                }
            ),
        ),
        (
            "no_captions",
            "settings.overlay.desktop.preview.fixture.no_captions",
            OverlayPresentationSnapshot(revision=6, blocks=[]),
            frozenset({"no_caption", "edit_placeholder", "pass_through_transparent"}),
        ),
    )


def _preview_block(
    block_id: str,
    *,
    channel: str,
    block_variant: str,
    appearance_seq: int,
    primary_text: str,
    secondary_text: str,
    secondary_enabled: bool,
) -> OverlayPresentationBlock:
    return OverlayPresentationBlock(
        id=block_id,
        occupant_key=f"preview:{channel}:{block_id}",
        appearance_seq=appearance_seq,
        channel=channel,  # type: ignore[arg-type]
        block_variant=block_variant,  # type: ignore[arg-type]
        primary_text=primary_text,
        secondary_text=secondary_text,
        secondary_enabled=secondary_enabled,
    )


def _caption_slots_for_snapshot(
    snapshot: OverlayPresentationSnapshot,
    *,
    primary_font_size: int,
    secondary_font_size: int,
) -> tuple[DesktopCaptionSlot, ...]:
    slots: list[DesktopCaptionSlot] = []
    for block in sorted(snapshot.blocks, key=lambda item: (item.appearance_seq, item.occupant_key)):
        lines = _caption_lines_for_block(
            block,
            primary_font_size=primary_font_size,
            secondary_font_size=secondary_font_size,
        )
        if not lines:
            continue
        slots.append(
            DesktopCaptionSlot(
                block_id=block.id,
                occupant_key=block.occupant_key,
                channel=block.channel,
                block_variant=block.block_variant,
                appearance_seq=block.appearance_seq,
                lines=lines,
                secondary_enabled=block.secondary_enabled,
                active=block.block_variant in {"active_self", "active_peer"},
            )
        )
    return tuple(slots)


def _caption_lines_for_snapshot(
    snapshot: OverlayPresentationSnapshot,
    *,
    primary_font_size: int,
    secondary_font_size: int,
) -> tuple[DesktopCaptionLine, ...]:
    return tuple(
        line
        for slot in _caption_slots_for_snapshot(
            snapshot,
            primary_font_size=primary_font_size,
            secondary_font_size=secondary_font_size,
        )
        for line in slot.lines
    )


def _caption_lines_for_block(
    block: OverlayPresentationBlock,
    *,
    primary_font_size: int,
    secondary_font_size: int,
) -> tuple[DesktopCaptionLine, ...]:
    primary_text = block.primary_text.strip()
    secondary_text = block.secondary_text.strip()
    if not primary_text and not secondary_text:
        return ()
    # If translation equals original (e.g. "." → "."), suppress duplicate secondary line.
    if primary_text and secondary_text and primary_text == secondary_text:
        secondary_text = ""

    if block.block_variant == "active_self":
        return _self_active_lines(
            block,
            primary_text=primary_text,
            secondary_text=secondary_text,
            primary_font_size=primary_font_size,
            secondary_font_size=secondary_font_size,
        )
    if block.block_variant == "active_peer":
        return _peer_active_lines(
            block,
            primary_text=primary_text,
            secondary_text=secondary_text,
            primary_font_size=primary_font_size,
            secondary_font_size=secondary_font_size,
        )
    if block.channel == "peer":
        return _peer_finalized_lines(
            block,
            primary_text=primary_text,
            secondary_text=secondary_text,
            primary_font_size=primary_font_size,
            secondary_font_size=secondary_font_size,
        )
    return _self_finalized_lines(
        block,
        primary_text=primary_text,
        secondary_text=secondary_text,
        primary_font_size=primary_font_size,
        secondary_font_size=secondary_font_size,
    )


def _self_active_lines(
    block: OverlayPresentationBlock,
    *,
    primary_text: str,
    secondary_text: str,
    primary_font_size: int,
    secondary_font_size: int,
) -> tuple[DesktopCaptionLine, ...]:
    lines: list[DesktopCaptionLine] = []
    if primary_text:
        lines.append(
            _caption_line(
                block,
                text=primary_text,
                role="active_self_source",
                slot="primary",
                priority=100,
                max_lines=_DESKTOP_CAPTION_PRIMARY_MAX_LINES,
                font_size=primary_font_size,
                language=block.primary_language,
                color=_DESKTOP_CAPTION_SELF_SOURCE,
                active=True,
            )
        )
    if secondary_text and block.secondary_enabled:
        lines.append(
            _caption_line(
                block,
                text=secondary_text,
                role="active_self_translation",
                slot="secondary",
                priority=85,
                max_lines=_DESKTOP_CAPTION_SECONDARY_MAX_LINES,
                font_size=secondary_font_size,
                language=block.secondary_language,
                color=_DESKTOP_CAPTION_SELF_TRANSLATION,
                active=True,
            )
        )
    return tuple(lines)


def _peer_active_lines(
    block: OverlayPresentationBlock,
    *,
    primary_text: str,
    secondary_text: str,
    primary_font_size: int,
    secondary_font_size: int,
) -> tuple[DesktopCaptionLine, ...]:
    readable_text = primary_text or (secondary_text if block.secondary_enabled else "")
    if not readable_text:
        return ()
    promoted = not primary_text and bool(secondary_text) and block.secondary_enabled
    return (
        _caption_line(
            block,
            text=readable_text,
            role="active_peer_source",
            slot="primary" if not promoted else "primary",
            priority=95,
            max_lines=_DESKTOP_CAPTION_PRIMARY_MAX_LINES,
            font_size=primary_font_size if promoted else secondary_font_size,
            language=block.secondary_language if promoted else block.primary_language,
            promoted=promoted,
            active=True,
        ),
    )


def _peer_finalized_lines(
    block: OverlayPresentationBlock,
    *,
    primary_text: str,
    secondary_text: str,
    primary_font_size: int,
    secondary_font_size: int,
) -> tuple[DesktopCaptionLine, ...]:
    # Layout: source (Chinese + pinyin) on TOP, translation (English) on BOTTOM.
    # The "secondary" slot renders after "primary" in _slot_lines_with_reserved_regions,
    # so we flip the slot assignments: source → primary (top), translation → secondary (bottom).
    if secondary_text and block.secondary_enabled and primary_text:
        # Both source and translation available: source on top (primary slot, purple),
        # translation below (secondary slot, gold).
        return (
            _caption_line(
                block,
                text=secondary_text,
                role="peer_source_original",
                slot="primary",
                priority=90,
                max_lines=_DESKTOP_CAPTION_PRIMARY_MAX_LINES,
                font_size=primary_font_size,
                language=block.secondary_language,
                color=_DESKTOP_CAPTION_PEER_SOURCE,
            ),
            _caption_line(
                block,
                text=primary_text,
                role="peer_translation",
                slot="secondary",
                priority=70,
                max_lines=_DESKTOP_CAPTION_SECONDARY_MAX_LINES,
                font_size=secondary_font_size,
                language=block.primary_language,
                color=_DESKTOP_CAPTION_GOLD,
            ),
        )
    if primary_text:
        return (
            _caption_line(
                block,
                text=primary_text,
                role="peer_translation",
                slot="primary",
                priority=90,
                max_lines=_DESKTOP_CAPTION_PRIMARY_MAX_LINES,
                font_size=primary_font_size,
                language=block.primary_language,
                color=_DESKTOP_CAPTION_GOLD,
            ),
        )
    if secondary_text and block.secondary_enabled:
        return (
            _caption_line(
                block,
                text=secondary_text,
                role="peer_source_original",
                slot="primary",
                priority=60,
                max_lines=_DESKTOP_CAPTION_PRIMARY_MAX_LINES,
                font_size=primary_font_size,
                language=block.secondary_language,
                promoted=True,
                color=_DESKTOP_CAPTION_PEER_SOURCE,
            ),
        )
    return ()


def _self_finalized_lines(
    block: OverlayPresentationBlock,
    *,
    primary_text: str,
    secondary_text: str,
    primary_font_size: int,
    secondary_font_size: int,
) -> tuple[DesktopCaptionLine, ...]:
    lines: list[DesktopCaptionLine] = []
    if primary_text:
        lines.append(
            _caption_line(
                block,
                text=primary_text,
                role="self_source",
                slot="primary",
                priority=65,
                max_lines=_DESKTOP_CAPTION_PRIMARY_MAX_LINES,
                font_size=primary_font_size,
                language=block.primary_language,
                color=_DESKTOP_CAPTION_SELF_SOURCE,
            )
        )
        if secondary_text and block.secondary_enabled:
            lines.append(
                _caption_line(
                    block,
                    text=secondary_text,
                    role="self_translation",
                    slot="secondary",
                    priority=50,
                    max_lines=_DESKTOP_CAPTION_SECONDARY_MAX_LINES,
                    font_size=secondary_font_size,
                    language=block.secondary_language,
                    color=_DESKTOP_CAPTION_SELF_TRANSLATION,
                )
            )
        return tuple(lines)
    if secondary_text and block.secondary_enabled:
        return (
            _caption_line(
                block,
                text=secondary_text,
                role="self_translation",
                slot="primary",
                priority=55,
                max_lines=_DESKTOP_CAPTION_PRIMARY_MAX_LINES,
                font_size=primary_font_size,
                language=block.secondary_language,
                color=_DESKTOP_CAPTION_SELF_TRANSLATION,
                promoted=True,
            ),
        )
    return ()


def _caption_line(
    block: OverlayPresentationBlock,
    *,
    text: str,
    role: str,
    slot: str,
    priority: int,
    max_lines: int,
    font_size: int,
    language: str | None = None,
    promoted: bool = False,
    active: bool = False,
    color: str | None = None,
) -> DesktopCaptionLine:
    uses_cjk_font_policy = _desktop_caption_uses_cjk_font_policy(text, language)
    return DesktopCaptionLine(
        text=text,
        role=role,
        slot=slot,
        color=color if color is not None else _desktop_caption_color_for_channel(block.channel),
        priority=priority,
        block_id=block.id,
        channel=block.channel,
        block_variant=block.block_variant,
        appearance_seq=block.appearance_seq,
        max_lines=max_lines,
        font_size=font_size,
        font_family=(
            _DESKTOP_CAPTION_CJK_FONT_FAMILY
            if uses_cjk_font_policy
            else _DESKTOP_CAPTION_LATIN_FONT_FAMILY
        ),
        weight="medium" if uses_cjk_font_policy else "semibold",
        promoted=promoted,
        active=active,
    )


def _caption_slot_with_dynamic_width(
    slot: DesktopCaptionSlot,
    *,
    padding_horizontal: int,
    max_card_width: int,
) -> DesktopCaptionSlot:
    max_width = max(1.0, float(max_card_width))
    minimum_width = min(_DESKTOP_CAPTION_MIN_DYNAMIC_CARD_WIDTH, max_width)
    estimated_text_width = max(
        (_estimated_caption_line_width(line.text, line.font_size) for line in slot.lines),
        default=0.0,
    )
    estimated_card_width = (
        estimated_text_width
        + (float(padding_horizontal) * 2)
        + _DESKTOP_CAPTION_DYNAMIC_WIDTH_SAFETY
    )
    card_width = min(max_width, max(minimum_width, estimated_card_width))
    card_text_width = max(1.0, card_width - (float(padding_horizontal) * 2))
    return replace(slot, card_width=card_width, card_text_width=card_text_width)


def _caption_card_width_memory_key(slot: DesktopCaptionSlot) -> tuple[str, str, int]:
    return (slot.block_id, slot.occupant_key, slot.appearance_seq)


def _caption_width_key_label(key: tuple[str, str, int]) -> str:
    return f"{key[0]}/{key[1]}/{key[2]}"


def _desktop_snapshot_rows_summary(snapshot: OverlayPresentationSnapshot) -> str:
    return "; ".join(
        _desktop_snapshot_block_summary(index, block) for index, block in enumerate(snapshot.blocks)
    )


def _desktop_snapshot_block_summary(
    index: int,
    block: OverlayPresentationBlock,
) -> str:
    secondary_len = len(block.secondary_text) if block.secondary_enabled else 0
    return (
        f"idx={index} "
        f"id={block.id} "
        f"occupant_key={block.occupant_key} "
        f"appearance_seq={block.appearance_seq} "
        f"channel={block.channel} "
        f"variant={block.block_variant} "
        f"primary_len={len(block.primary_text)} "
        f"secondary_len={secondary_len} "
        f"secondary_enabled={block.secondary_enabled} "
        f"update_id={_optional_log_value(block.update_id)} "
        f"origin_wall_clock_ms={_optional_log_value(block.origin_wall_clock_ms)} "
        f"session_scope={_optional_log_value(block.session_scope)}"
    )


def _optional_log_value(value: object | None) -> str:
    if value is None:
        return "none"
    return str(value)


def _estimated_caption_line_width(text: str, font_size: int) -> float:
    return sum(_estimated_caption_char_width(char, font_size) for char in text)


def _estimated_caption_char_width(char: str, font_size: int) -> float:
    codepoint = ord(char)
    if char.isspace():
        return font_size * _DESKTOP_CAPTION_SPACE_WIDTH_EM
    if _is_caption_emoji_or_symbol(codepoint):
        return font_size * _DESKTOP_CAPTION_EMOJI_WIDTH_EM
    if _is_caption_cjk_or_hangul(codepoint):
        return font_size * _DESKTOP_CAPTION_CJK_WIDTH_EM
    if char in ".,;:!?'\"-–—()[]{}·…":
        return font_size * _DESKTOP_CAPTION_PUNCT_WIDTH_EM
    if char in "ilI|":
        return font_size * _DESKTOP_CAPTION_LATIN_NARROW_WIDTH_EM
    if char.isascii():
        return font_size * _DESKTOP_CAPTION_LATIN_WIDE_WIDTH_EM
    return font_size * _DESKTOP_CAPTION_CJK_WIDTH_EM


def _is_caption_cjk_or_hangul(codepoint: int) -> bool:
    return (
        0x1100 <= codepoint <= 0x11FF
        or 0x3040 <= codepoint <= 0x30FF
        or 0x3400 <= codepoint <= 0x4DBF
        or 0x4E00 <= codepoint <= 0x9FFF
        or 0xAC00 <= codepoint <= 0xD7AF
        or 0xF900 <= codepoint <= 0xFAFF
    )


def _is_caption_emoji_or_symbol(codepoint: int) -> bool:
    return 0x1F000 <= codepoint <= 0x1FAFF


def _desktop_caption_char_is_cjk(char: str) -> bool:
    return _is_caption_cjk_or_hangul(ord(char))


def _desktop_caption_font_family_for_text(text: str, language: str | None = None) -> str:
    if _desktop_caption_uses_cjk_font_policy(text, language):
        return _DESKTOP_CAPTION_CJK_FONT_FAMILY
    return _DESKTOP_CAPTION_LATIN_FONT_FAMILY


def _desktop_caption_uses_cjk_font_policy(text: str, language: str | None = None) -> bool:
    return _desktop_caption_language_is_cjk(language) or _desktop_caption_text_contains_cjk(text)


def _desktop_caption_language_is_cjk(language: str | None) -> bool:
    primary_subtag = _desktop_caption_language_primary_subtag(language)
    return primary_subtag in _DESKTOP_CAPTION_CJK_LANGUAGE_PRIMARY_SUBTAGS


def _desktop_caption_language_primary_subtag(language: str | None) -> str | None:
    if language is None:
        return None
    normalized = language.strip().replace("_", "-").lower()
    if not normalized:
        return None
    return next((part for part in normalized.split("-") if part), None)


def _desktop_caption_text_contains_cjk(text: str) -> bool:
    return any(_desktop_caption_char_is_cjk(char) for char in text)


def _select_visible_caption_lines(
    candidates: tuple[DesktopCaptionLine, ...],
) -> tuple[DesktopCaptionLine, ...]:
    selected: list[DesktopCaptionLine] = []
    used_lines = 0
    for line in sorted(
        candidates,
        key=lambda item: (item.priority, item.appearance_seq, -_slot_order(item.slot), item.text),
        reverse=True,
    ):
        if used_lines + line.max_lines > _DESKTOP_CAPTION_MAX_VISIBLE_LINES:
            continue
        selected.append(line)
        used_lines += line.max_lines
        if used_lines >= _DESKTOP_CAPTION_MAX_VISIBLE_LINES:
            break
    return tuple(sorted(selected, key=lambda item: (item.appearance_seq, _slot_order(item.slot))))


def _slot_order(slot: str) -> int:
    if slot in {"primary", "primary_promoted", "primary_placeholder"}:
        return 0
    return 1


def _validated_visual_state(
    visual_state: DesktopCaptionVisualState | None,
) -> DesktopCaptionVisualState:
    source = visual_state or DesktopCaptionVisualState()
    settings = DesktopFletOverlayVisualSettings(
        text_scale=source.text_scale,
        background_alpha=source.background_alpha,
        outline_width=source.outline_width,
    )
    settings.validate()
    return DesktopCaptionVisualState(
        text_scale=settings.text_scale,
        background_alpha=settings.background_alpha,
        outline_width=settings.outline_width,
        show_romanization=source.show_romanization,
        single_turn_mode=source.single_turn_mode,
        show_translation=source.show_translation,
        show_peer_original=source.show_peer_original,
        peer_source_language=source.peer_source_language,
        peer_target_language=source.peer_target_language,
    )


# Representative one-line sample messages per language, used so the edit-mode SAMPLE
# caption reflects the user's actual language choices ("as if I would hear it in game").
# Each entry: (text, romanization_or_None). Keyed by the app's language codes.
_DESKTOP_SAMPLE_SENTENCES: dict[str, tuple[str, str | None]] = {
    "en": ("This is a sample message", None),
    "zh-CN": ("这是一条示例消息", "zhè shì yī tiáo shìlì xiāoxī"),
    "zh-TW": ("這是一條範例訊息", "zhè shì yī tiáo fànlì xùnxí"),
    "ja": ("これはサンプルメッセージです", "kore wa sanpuru messēji desu"),
    "ko": ("이것은 샘플 메시지입니다", "igeoseun saempeul mesijiimnida"),
    "es": ("Este es un mensaje de ejemplo", None),
    "fr": ("Ceci est un message d'exemple", None),
    "de": ("Dies ist eine Beispielnachricht", None),
    "ru": ("Это пример сообщения", "Eto primer soobshcheniya"),
    "pt": ("Esta é uma mensagem de exemplo", None),
    "it": ("Questo è un messaggio di esempio", None),
}


def _desktop_sample_sentence_for(language: str | None) -> tuple[str, str | None]:
    """Return (text, romanization) for a sample caption in ``language``, falling back
    to the base language (e.g. zh for zh-XX) and finally English."""
    if language:
        if language in _DESKTOP_SAMPLE_SENTENCES:
            return _DESKTOP_SAMPLE_SENTENCES[language]
        base = language.split("-")[0]
        for code, value in _DESKTOP_SAMPLE_SENTENCES.items():
            if code.split("-")[0] == base:
                return value
    return _DESKTOP_SAMPLE_SENTENCES["en"]


def _desktop_caption_size_preset_for_dimensions(
    width: int,
    height: int,
) -> DesktopCaptionSizePreset:
    # Matched on width only: two-turn mode doubles the actual window height (two
    # stacked slots) while keeping the same font/padding metrics as single-turn,
    # so height no longer uniquely identifies the preset.
    _ = height
    for preset_id in DESKTOP_FLET_SIZE_PRESET_ORDER:
        preset = _DESKTOP_CAPTION_SIZE_PRESETS[preset_id]
        settings_dimensions = DESKTOP_FLET_SIZE_PRESETS[preset_id]
        if (preset.window_width, preset.window_height) != settings_dimensions:
            raise RuntimeError("desktop caption preset dimensions diverged from settings")
        if width == preset.window_width:
            return preset
    return _DESKTOP_CAPTION_SIZE_PRESETS[DESKTOP_FLET_DEFAULT_SIZE_PRESET]


def _caption_background_color(background_alpha: float) -> str:
    if background_alpha <= 0:
        return _DESKTOP_CAPTION_TRANSPARENT
    alpha = int(round(_clamp(background_alpha, 0.0, 1.0) * 255))
    return f"#{alpha:02X}{_DESKTOP_CAPTION_BACKGROUND_RGB}"


def _background_transparency_label_for_alpha(background_alpha: float) -> str:
    opacity = _clamp(background_alpha, 0.0, 1.0)
    return f"{int(round(opacity * 100))}%"


def _positive_int_or_default(value: int | float, default: int) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        return default
    if value <= 0:
        return default
    return int(round(value))


def _clamp(value: float, minimum: float, maximum: float) -> float:
    return min(maximum, max(minimum, value))


def _build_flet_caption_slot(ft: Any, plan: DesktopCaptionPlan, slot: DesktopCaptionSlot) -> Any:
    if plan.full_window_background_visible:
        card_text_width = plan.text_width
        card_width = plan.window_width
    else:
        card_text_width = slot.card_text_width or plan.text_width
        card_width = slot.card_width or plan.window_width
    slot_lines = _slot_lines_with_reserved_regions(
        slot,
        secondary_font_size=plan.secondary_font_size,
        font_family=slot.lines[0].font_family if slot.lines else None,
    )
    has_secondary_region = any(line.slot == "secondary" for line in slot_lines)
    line_controls = [
        _build_flet_caption_line(
            ft,
            plan,
            line,
            text_width=card_text_width,
            center_primary_region=not has_secondary_region,
        )
        for line in slot_lines
    ]
    column = ft.Column(
        controls=line_controls,
        horizontal_alignment=ft.CrossAxisAlignment.CENTER,
        spacing=0,
        tight=True,
        scroll=None,
    )
    text_layer = ft.Container(
        content=column,
        width=card_text_width,
        bgcolor=ft.Colors.TRANSPARENT,
    )
    inner_card = ft.Container(
        content=text_layer,
        width=card_width,
        bgcolor=(
            ft.Colors.TRANSPARENT if plan.full_window_background_visible else plan.background_color
        ),
        border_radius=plan.border_radius,
        padding=ft.padding.symmetric(
            horizontal=plan.padding_horizontal,
            vertical=plan.padding_vertical,
        ),
    )
    return ft.Container(
        content=inner_card,
        width=plan.window_width,
        height=plan.slot_height,
        bgcolor=ft.Colors.TRANSPARENT,
        alignment=ft.alignment.center,
        clip_behavior=ft.ClipBehavior.HARD_EDGE,
    )


def _build_flet_caption_line(
    ft: Any,
    plan: DesktopCaptionPlan,
    line: DesktopCaptionLine,
    *,
    text_width: float,
    center_primary_region: bool = False,
) -> Any:
    if _is_ruby_line(line):
        content = _build_ruby_content(ft, line, text_width)
    else:
        content = _build_flet_text(ft, line, text_width)
    return ft.Container(
        content=content,
        width=text_width,
        bgcolor=ft.Colors.TRANSPARENT,
    )


def _is_ruby_line(line: DesktopCaptionLine) -> bool:
    """True when line.text is 'romanization\\nCJK_text' — suitable for per-char ruby layout."""
    if "\n" not in line.text:
        return False
    roman, cjk = line.text.split("\n", 1)
    roman = roman.strip()
    cjk = cjk.strip()
    if not roman or not cjk:
        return False
    if _desktop_caption_text_contains_cjk(roman):
        return False
    return _desktop_caption_text_contains_cjk(cjk)


def _build_ruby_content(ft: Any, line: DesktopCaptionLine, text_width: float) -> Any:
    """Render 'romanization\\nCJK' as ruby text.

    Chinese: pinyin has exactly 1 syllable per CJK char, so we stack each syllable
    above its character. Non-CJK chars (digits, punctuation) are rendered without
    a ruby syllable but share the same vertical alignment.

    Japanese / other (romaji doesn't map 1:1): render romaji as a block above the
    full text at a readable size (not tiny), since fitting it per-char isn't possible.
    """
    roman, cjk = line.text.split("\n", 1)
    roman = roman.strip()
    cjk = cjk.strip()

    ruby_size = max(9, int(line.font_size * 0.50))
    syllables = roman.split()
    cjk_chars = list(cjk)
    cjk_only = [c for c in cjk_chars if _desktop_caption_char_is_cjk(c)]
    # Only count letter-bearing tokens as pinyin syllables so that trailing
    # digits/punctuation (e.g. "3" in "cè shì 3") don't break the alignment.
    alpha_syllables = [s for s in syllables if any(c.isalpha() for c in s)]

    if len(alpha_syllables) == len(cjk_only):
        # Per-character stacking (Chinese and similar scripts)
        char_cols: list[Any] = []
        syl_idx = 0
        for char in cjk_chars:
            if _desktop_caption_char_is_cjk(char):
                syl = alpha_syllables[syl_idx]
                syl_idx += 1
            else:
                syl = " "  # non-breaking space placeholder keeps column height uniform
            char_cols.append(
                ft.Column(
                    controls=[
                        ft.Text(
                            syl,
                            size=ruby_size,
                            color=line.color,
                            font_family=_DESKTOP_CAPTION_LATIN_FONT_FAMILY,
                            text_align=ft.TextAlign.CENTER,
                            style=ft.TextStyle(
                                size=ruby_size,
                                height=1.1,
                                shadow=_caption_text_shadow(ft),
                            ),
                        ),
                        ft.Text(
                            char,
                            size=line.font_size,
                            color=line.color,
                            font_family=(
                                _DESKTOP_CAPTION_CJK_FONT_FAMILY
                                if _desktop_caption_char_is_cjk(char)
                                else _DESKTOP_CAPTION_LATIN_FONT_FAMILY
                            ),
                            text_align=ft.TextAlign.CENTER,
                            style=ft.TextStyle(
                                size=line.font_size,
                                height=line.line_height,
                                weight=_flet_font_weight(ft, line.weight),
                                shadow=_caption_text_shadow(ft),
                            ),
                        ),
                    ],
                    horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                    spacing=0,
                    tight=True,
                )
            )
        return ft.Container(
            content=ft.Row(
                char_cols,
                wrap=True,
                alignment=ft.MainAxisAlignment.CENTER,
                spacing=2,
                run_spacing=2,
            ),
            width=text_width,
            alignment=ft.alignment.center,
        )
    else:
        # Per-word fallback (Japanese romaji etc.): use a readable size — the romaji
        # is shown as a full block above the text, not squeezed over individual chars.
        block_roman_size = max(int(line.font_size * 0.75), 14)
        return ft.Column(
            controls=[
                ft.Text(
                    roman,
                    size=block_roman_size,
                    color=line.color,
                    font_family=_DESKTOP_CAPTION_LATIN_FONT_FAMILY,
                    text_align=ft.TextAlign.CENTER,
                    width=text_width,
                    style=ft.TextStyle(
                        size=block_roman_size,
                        height=1.15,
                        shadow=_caption_text_shadow(ft),
                    ),
                ),
                ft.Text(
                    cjk,
                    size=line.font_size,
                    color=line.color,
                    font_family=line.font_family,
                    text_align=ft.TextAlign.CENTER,
                    width=text_width,
                    max_lines=line.max_lines,
                    overflow=ft.TextOverflow.ELLIPSIS,
                    style=ft.TextStyle(
                        size=line.font_size,
                        height=line.line_height,
                        weight=_flet_font_weight(ft, line.weight),
                        font_family=line.font_family,
                        shadow=_caption_text_shadow(ft),
                    ),
                ),
            ],
            horizontal_alignment=ft.CrossAxisAlignment.CENTER,
            spacing=0,
            tight=True,
        )


def _caption_line_region_alignment(
    ft: Any,
    line: DesktopCaptionLine,
    *,
    center_primary_region: bool = False,
) -> Any:
    if line.slot == "primary":
        if center_primary_region:
            return ft.alignment.center
        return ft.Alignment(0, _DESKTOP_CAPTION_PRIMARY_REGION_ALIGNMENT_Y)
    return ft.alignment.center


def _slot_lines_with_reserved_regions(
    slot: DesktopCaptionSlot,
    *,
    secondary_font_size: int,
    font_family: str | None,
) -> tuple[DesktopCaptionLine, ...]:
    primary_lines = tuple(line for line in slot.lines if line.slot == "primary")
    secondary_lines = tuple(line for line in slot.lines if line.slot == "secondary")
    if secondary_lines:
        return (*primary_lines, secondary_lines[0])
    if not _slot_should_reserve_empty_secondary_region(slot, primary_lines):
        return primary_lines
    return (
        *primary_lines,
        DesktopCaptionLine(
            text="",
            role="reserved_secondary",
            slot="secondary",
            color=_DESKTOP_CAPTION_WHITE,
            priority=0,
            block_id=slot.block_id,
            channel=slot.channel,
            block_variant=slot.block_variant,
            appearance_seq=slot.appearance_seq,
            max_lines=_DESKTOP_CAPTION_SECONDARY_MAX_LINES,
            font_size=secondary_font_size,
            font_family=font_family,
        ),
    )


def _slot_should_reserve_empty_secondary_region(
    slot: DesktopCaptionSlot,
    primary_lines: tuple[DesktopCaptionLine, ...],
) -> bool:
    if not slot.secondary_enabled:
        return False
    return any(not line.promoted for line in primary_lines)


def _build_flet_text(
    ft: Any,
    line: DesktopCaptionLine,
    text_width: float,
) -> Any:
    return ft.Text(
        value=line.text,
        width=text_width,
        text_align=ft.TextAlign.CENTER,
        font_family=line.font_family,
        size=line.font_size,
        weight=_flet_font_weight(ft, line.weight),
        max_lines=line.max_lines,
        overflow=ft.TextOverflow.ELLIPSIS,
        no_wrap=False,
        color=line.color,
        style=ft.TextStyle(
            size=line.font_size,
            height=line.line_height,
            weight=_flet_font_weight(ft, line.weight),
            font_family=line.font_family,
            shadow=_caption_text_shadow(ft),
            foreground=None,
        ),
    )


def _caption_text_shadow(ft: Any) -> list[Any]:
    return [
        ft.BoxShadow(
            color=_DESKTOP_CAPTION_CONTACT_SHADOW_COLOR,
            offset=_DESKTOP_CAPTION_CONTACT_SHADOW_OFFSET,
            blur_radius=_DESKTOP_CAPTION_CONTACT_SHADOW_BLUR,
        ),
        ft.BoxShadow(
            color=_DESKTOP_CAPTION_AMBIENT_SHADOW_COLOR,
            offset=_DESKTOP_CAPTION_AMBIENT_SHADOW_OFFSET,
            blur_radius=_DESKTOP_CAPTION_AMBIENT_SHADOW_BLUR,
        ),
    ]


def _flet_font_weight(ft: Any, weight: str) -> Any:
    if weight == "semibold":
        return ft.FontWeight.W_600
    if weight == "medium":
        return ft.FontWeight.W_500
    if weight == "bold":
        return ft.FontWeight.BOLD
    return None


class DesktopOverlayStartupError(Exception):
    def __init__(self, failure_reason: str, message: str) -> None:
        super().__init__(message)
        self.failure_reason = failure_reason


class LifecycleSink(Protocol):
    async def emit(self, event: dict[str, object]) -> None: ...


class RendererWindow(Protocol):
    async def start(self, initial_snapshot: OverlayPresentationSnapshot) -> None: ...
    async def run_until_closed(self) -> None: ...
    async def close(self) -> None: ...
    async def dispatch_snapshot(self, snapshot: OverlayPresentationSnapshot) -> None: ...
    async def dispatch_runtime_control(self, payload: dict[str, object]) -> None: ...


class ParentMonitor(Protocol):
    async def wait_for_parent_exit(self, stop_event: asyncio.Event) -> None: ...


@dataclass(frozen=True, slots=True)
class _RuntimeOutcome:
    exit_code: int


@dataclass(frozen=True, slots=True)
class _ProgrammaticBoundsEchoSuppression:
    signature: tuple[float, float, float, float]
    expires_at: float


@dataclass(frozen=True, slots=True)
class _DesktopRenderTrace:
    content_kind: str
    surface_visible: bool
    slot_count: int
    line_count: int
    window_width: int
    window_height: int
    background_alpha: float


class StdoutLifecycleSink:
    async def emit(self, event: dict[str, object]) -> None:
        safe_event = _redact_event(event)
        if safe_event.get("type") == "overlay_event":
            return
        stream = (
            sys.stderr
            if safe_event.get("type") in {"startup_error", "runtime_error"}
            else sys.stdout
        )
        print(json.dumps(safe_event, sort_keys=True), file=stream, flush=True)


class HeadlessRendererWindow:
    """Minimal window lifecycle boundary until the Flet window implementation lands."""

    def __init__(self) -> None:
        self._closed = asyncio.Event()

    async def start(self, initial_snapshot: OverlayPresentationSnapshot) -> None:
        _ = initial_snapshot

    async def run_until_closed(self) -> None:
        await self._closed.wait()

    async def close(self) -> None:
        self._closed.set()

    async def dispatch_snapshot(self, snapshot: OverlayPresentationSnapshot) -> None:
        _ = snapshot

    async def dispatch_runtime_control(self, payload: dict[str, object]) -> None:
        _ = payload


type FletAppRunner = Callable[[Callable[[Any], object]], Awaitable[None]]
type OverlayEventSink = Callable[[dict[str, object]], Awaitable[None]]
type PreviewAppRunner = Callable[[Callable[[Any], object]], object]


async def _default_flet_app_runner(target: Callable[[Any], object]) -> None:
    import flet as ft

    with _patch_flet_view_hidden_launcher():
        await ft.app_async(
            target=target,
            view=ft.AppView.FLET_APP_HIDDEN,
            assets_dir=str(assets_dir()),
        )


@contextlib.contextmanager
def _patch_flet_view_hidden_launcher():
    import flet_desktop

    original = flet_desktop.open_flet_view_async
    flet_desktop.open_flet_view_async = _open_flet_view_hidden_without_startup_flash
    try:
        yield
    finally:
        flet_desktop.open_flet_view_async = original


async def _open_flet_view_hidden_without_startup_flash(
    page_url: str,
    assets_dir: str | None,
    hidden: bool,
) -> tuple[asyncio.subprocess.Process, str | None]:
    import flet_desktop

    args, flet_env, pid_file = flet_desktop.__locate_and_unpack_flet_view(
        page_url,
        assets_dir,
        hidden,
    )
    kwargs: dict[str, object] = {"env": flet_env}
    if os.name == "nt":
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startupinfo.wShowWindow = subprocess.SW_HIDE
        kwargs["startupinfo"] = startupinfo
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW

    return (
        await asyncio.create_subprocess_exec(args[0], *args[1:], **kwargs),
        pid_file,
    )


def _default_preview_app_runner(target: Callable[[Any], object]) -> None:
    import flet as ft

    ft.app(target=target)


_REAL_DEFAULT_PREVIEW_APP_RUNNER = _default_preview_app_runner


class FletDesktopRendererWindow:
    """Flet 0.28.3 transparent desktop overlay window boundary.

    The renderer remains persistence-free: this class only applies runtime
    controls to the Flet page/window and emits renderer-originated overlay
    events for the parent/controller to decide whether and how to persist.
    """

    def __init__(
        self,
        *,
        app_runner: FletAppRunner | None = None,
        event_sink: OverlayEventSink | None = None,
        locale: str | None = None,
        logging_mode: str = "basic",
        bounds_debounce_s: float = 0.15,
        startup_timeout_s: float = 5.0,
        preview_catalog: DesktopOverlayPreviewCatalog | None = None,
    ) -> None:
        self._app_runner = app_runner or _default_flet_app_runner
        self._event_sink = event_sink
        self._locale = locale
        self._logging_mode = normalize_overlay_logging_mode(logging_mode)
        self._bounds_debounce_s = max(0.0, float(bounds_debounce_s))
        self._startup_timeout_s = max(0.1, float(startup_timeout_s))
        self._preview_catalog = preview_catalog
        self._preview_fixture_id = preview_catalog.fixtures[0].id if preview_catalog else None
        self._preview_background_surface_id = _DESKTOP_PREVIEW_DEFAULT_BACKGROUND_SURFACE_ID
        self._preview_background_alpha = _DESKTOP_PREVIEW_DEFAULT_BACKGROUND_ALPHA
        self._preview_size_preset_id = DESKTOP_FLET_DEFAULT_SIZE_PRESET
        self._snapshot = OverlayPresentationSnapshot()
        self._visual_state = DesktopCaptionVisualState()
        self._interaction_mode = _DESKTOP_INTERACTION_MODE_EDIT
        self._startup_visual_state: DesktopCaptionVisualState | None = None
        self._startup_window_bounds: dict[str, int | float] | None = None
        # Authoritative current window size (from the bounds we set), used to size the
        # edit chrome — page.window.width/height lag at first render, which made the
        # chrome use the wrong (default) size for small presets until a manual resize.
        self._current_window_bounds: dict[str, int | float] | None = None
        self._page: Any | None = None
        self._page_ready = asyncio.Event()
        self._closed = asyncio.Event()
        self._app_task: asyncio.Task[None] | None = None
        self._page_start_error: BaseException | None = None
        self._bounds_sample_task: asyncio.Task[None] | None = None
        self._scheduled_callback_tasks: set[asyncio.Future[Any] | ConcurrentFuture[Any]] = set()
        self._programmatic_bounds_echo_suppression: _ProgrammaticBoundsEchoSuppression | None = None
        self._last_reported_bounds: tuple[float, float, float, float] | None = None
        self._caption_card_width_floor_by_block: dict[tuple[str, str, int], float] = {}
        self._last_render_trace: _DesktopRenderTrace | None = None
        # When we have a startup size to apply, the window stays hidden until the
        # native OS window has actually been resized to that size (see
        # _reassert_startup_bounds). Without this, the window is shown immediately
        # at Flet's unrelated native default size while the content inside is laid
        # out for the configured preset, which looks like a "squished and cut in
        # half" window for the brief window before the resize lands.
        self._pending_startup_reveal: bool = False
        # Bumped every time a new bounds-apply retry loop starts (startup or live
        # runtime control). A running loop checks this before each window mutation
        # and bails out if a newer request has superseded it — without this, the
        # startup retry loop and a live size-preset-change retry loop can race and
        # interleave their jiggle/settle writes, leaving the native window stuck
        # mid-jiggle (oversized) while content is laid out for the real size.
        self._bounds_apply_generation: int = 0
        # Set when a live resize is applied while the overlay is LOCKED (pass-through).
        # A pass-through window is layered/click-through, and when it's also in the
        # background (e.g. the Settings tab is focused) Windows does not reliably
        # commit a programmatic native resize — Flet's window.width/height model
        # updates but the real OS frame keeps its old size. So when we return to EDIT
        # mode (leaving Settings auto-unlocks), we re-assert the bounds to force the
        # native window to actually adopt the size the content is now laid out for.
        # Without this, changing the size in Settings looks broken until a full
        # overlay restart — even though every logged dimension reads "correct".
        self._needs_bounds_reassert_on_edit: bool = False
        # One-time "overlay active" banner shown when the overlay first turns on WHILE
        # LOCKED — so the user can confirm it's alive even with background transparency
        # at 0. Rendered through the caption path (not page.overlay, which didn't
        # paint) and ONLY in pass-through mode, so it never sits on top of edit-mode
        # sample text. _active_banner_until is a monotonic deadline.
        self._startup_active_banner_shown: bool = False
        self._active_banner_until: float | None = None
        self._active_banner_opacity: float = 1.0
        self._active_banner_ctrl: Any = None

    def prime_startup_runtime_controls(
        self,
        payloads: tuple[dict[str, object], ...],
    ) -> tuple[dict[str, object], ...]:
        """Apply startup controls that must affect the first Flet page render.

        Returns controls that were not consumed during priming and still need
        normal runtime dispatch after the Flet page exists.
        """

        self._startup_visual_state = None
        self._startup_window_bounds = None
        self._startup_interaction_mode = None
        residual: list[dict[str, object]] = []
        for payload in payloads:
            command = payload.get("command")
            if command is None and "logging_mode" in payload:
                if self._set_logging_mode(payload.get("logging_mode")):
                    continue
                residual.append(payload)
                continue
            if command == "set_interaction_mode":
                # Capture (don't drop) the launch interaction mode so the overlay
                # honors locked-at-startup instead of always starting in edit mode,
                # and so an early lock toggle isn't lost during startup drain.
                mode = payload.get("mode")
                if mode in _DESKTOP_INTERACTION_MODES:
                    self._startup_interaction_mode = mode
                continue
            if command == "apply_visual_config":
                visual_state = _parse_runtime_visual_state(payload)
                if visual_state is not None:
                    self._startup_visual_state = visual_state
                else:
                    residual.append(payload)
                continue
            if command == "apply_window_bounds":
                bounds = _parse_runtime_window_bounds(payload)
                if bounds is not None:
                    self._startup_window_bounds = bounds
                    # Consume it: _reassert_startup_bounds owns the startup resize and
                    # retries it robustly (the native window opens oversized and Flet's
                    # model already holds the target, so it jiggles to force a real
                    # native resize). Do NOT also leave it in residual — that spawns a
                    # SECOND, concurrent live-resize loop that interleaves its jiggle
                    # writes with the startup loop's, leaving the native window stuck
                    # mid-jiggle while content renders at the real size (the "squished /
                    # sample text gone / improperly rescaled" bug).
                    continue
                # Unparseable bounds: fall through and let normal dispatch log+reject it.
                residual.append(payload)
                continue
            residual.append(payload)
        return tuple(residual)

    async def start(self, initial_snapshot: OverlayPresentationSnapshot) -> None:
        if self._preview_catalog is not None:
            self._snapshot = self._preview_selected_fixture().snapshot
            self._visual_state = self._preview_visual_state()
        else:
            self._snapshot = initial_snapshot
            self._visual_state = self._startup_visual_state or DesktopCaptionVisualState()
        self._page_ready.clear()
        self._closed.clear()
        self._page_start_error = None
        self._interaction_mode = (
            self._startup_interaction_mode or _DESKTOP_INTERACTION_MODE_EDIT
        )
        # The one-time "overlay active" banner is armed when the window is REVEALED
        # (see _reveal_window_if_supported), not here, so its short visible duration is
        # measured from when it actually appears rather than from app start.
        self._startup_active_banner_shown = False
        self._active_banner_until = None
        self._active_banner_opacity = 1.0
        if self._app_task is None or self._app_task.done():
            self._app_task = asyncio.create_task(self._app_runner(self._handle_page))

        ready_task = asyncio.create_task(self._page_ready.wait())
        try:
            done, _pending = await asyncio.wait(
                {ready_task, self._app_task},
                timeout=self._startup_timeout_s,
                return_when=asyncio.FIRST_COMPLETED,
            )
            if ready_task not in done:
                if self._app_task in done:
                    await self._app_task
                raise RuntimeError("desktop overlay Flet page was not created")
            if self._page_start_error is not None:
                raise RuntimeError(
                    "desktop overlay Flet page configuration failed"
                ) from self._page_start_error
        finally:
            if not ready_task.done():
                ready_task.cancel()
            await asyncio.gather(ready_task, return_exceptions=True)

        # Re-assert the configured window size shortly AFTER the page settles. Setting
        # window.width/height during initial config is unreliable (Flet can keep its
        # default, oversized window) — this delayed re-apply is what reliably makes a
        # small preset stick on first launch, the same effect as changing the size
        # later in settings.
        if self._startup_window_bounds is not None:
            self._run_page_task(self._reassert_startup_bounds)

    async def _reassert_startup_bounds(self) -> None:
        bounds = self._startup_window_bounds
        if not bounds:
            return
        # Claim this retry loop's generation. If a live runtime-control resize
        # (_apply_window_bounds_with_retries) starts while we're still retrying,
        # it bumps the counter and we bail at the next checkpoint instead of
        # racing it — see the comment on _bounds_apply_generation in __init__.
        self._bounds_apply_generation += 1
        my_generation = self._bounds_apply_generation
        # The native window opens oversized; Flet's model already holds the configured
        # size, so re-setting the SAME value is a no-op and the native window never
        # resizes. We jiggle to a slightly different size to force a real native resize,
        # then set the real bounds. A single attempt is timing-flaky (the native view
        # may not be ready) — on this machine the resize only reliably lands on the
        # LATER attempts (~1s+), so the full schedule below is load-bearing. Do NOT
        # shorten it: a brief 3-attempt/~0.85s version left the window revealed at the
        # wrong (small) height because the resize hadn't committed yet. Flet's
        # window.width/height is its own model value, not a real OS readback, so we
        # cannot detect "already correct" and must just retry on the proven cadence.
        for attempt_index, attempt_delay in enumerate((0.12, 0.25, 0.5, 1.0, 1.6), start=1):
            try:
                await asyncio.sleep(attempt_delay)
            except asyncio.CancelledError:
                return
            if self._closed.is_set() or self._page is None:
                if self._pending_startup_reveal:
                    logger.warning(
                        "[DesktopOverlay][Startup] overlay closed before startup resize "
                        "could be confirmed (attempt=%s)",
                        attempt_index,
                    )
                return
            if self._bounds_apply_generation != my_generation:
                logger.info(
                    "[DesktopOverlay][Startup] startup resize superseded by a newer "
                    "bounds request (attempt=%s)",
                    attempt_index,
                )
                if self._pending_startup_reveal:
                    self._reveal_window_if_supported(force=True)
                return
            page = self._page
            window = getattr(page, "window", None)
            if window is None:
                logger.warning(
                    "[DesktopOverlay][Startup] page.window unavailable during startup "
                    "resize attempt=%s",
                    attempt_index,
                )
                return
            try:
                window.width = float(bounds["width"]) + 16
                window.height = float(bounds["height"]) + 16
                update = getattr(page, "update", None)
                if callable(update):
                    update()
            except Exception:
                logger.warning(
                    "[DesktopOverlay][Startup] jiggle resize raised on attempt=%s",
                    attempt_index, exc_info=True,
                )
            try:
                await asyncio.sleep(0.08)
            except asyncio.CancelledError:
                return
            if self._closed.is_set() or self._page is None:
                return
            if self._bounds_apply_generation != my_generation:
                logger.info(
                    "[DesktopOverlay][Startup] startup resize superseded by a newer "
                    "bounds request before settle (attempt=%s)",
                    attempt_index,
                )
                if self._pending_startup_reveal:
                    self._reveal_window_if_supported(force=True)
                return
            self._apply_window_bounds(dict(bounds))
            logger.info(
                "[DesktopOverlay][Startup] startup resize attempt=%s applied "
                "width=%s height=%s native_width=%s native_height=%s",
                attempt_index, bounds["width"], bounds["height"],
                getattr(window, "width", None), getattr(window, "height", None),
            )
            if self._pending_startup_reveal:
                self._reveal_window_if_supported(force=True)
        if self._pending_startup_reveal:
            # Defensive fallback: every attempt above raised/was skipped without
            # ever clearing the flag. Reveal anyway rather than leaving the
            # overlay invisible forever (the user reported exactly this symptom).
            logger.warning(
                "[DesktopOverlay][Startup] startup resize never confirmed after all "
                "retries; revealing window anyway to avoid a permanently invisible overlay"
            )
            self._reveal_window_if_supported(force=True)

    async def run_until_closed(self) -> None:
        task = self._app_task
        if task is None:
            await self._closed.wait()
            return
        try:
            await task
        finally:
            self._closed.set()

    async def close(self) -> None:
        self._closed.set()
        page = self._page
        if page is not None:
            page.window.on_event = None
        await self._cancel_scheduled_callback_tasks()
        await self._cancel_bounds_sample()

        if page is not None:
            window = page.window
            try:
                window.close()
            except Exception:
                destroy = getattr(window, "destroy", None)
                if callable(destroy):
                    with contextlib.suppress(Exception):
                        destroy()

        task = self._app_task
        if task is not None and not task.done():
            try:
                await asyncio.wait_for(asyncio.shield(task), timeout=1.0)
            except TimeoutError:
                if page is not None:
                    destroy = getattr(page.window, "destroy", None)
                    if callable(destroy):
                        with contextlib.suppress(Exception):
                            destroy()
                    try:
                        await asyncio.wait_for(asyncio.shield(task), timeout=0.5)
                    except TimeoutError:
                        task.cancel()
                        await asyncio.gather(task, return_exceptions=True)
            except asyncio.CancelledError:
                raise
            except Exception:
                pass

    async def dispatch_snapshot(self, snapshot: OverlayPresentationSnapshot) -> None:
        self._emit_detailed_log(
            f"snapshot_update revision={snapshot.revision} blocks={len(snapshot.blocks)} "
            f"rows=[{_desktop_snapshot_rows_summary(snapshot)}]"
        )
        self._snapshot = snapshot
        self._render_page()

    async def dispatch_runtime_control(self, payload: dict[str, object]) -> None:
        if "logging_mode" in payload and payload.get("command") is None:
            self._set_logging_mode(payload.get("logging_mode"))
            return
        command = payload.get("command")
        if command == "set_interaction_mode":
            mode = payload.get("mode")
            if not isinstance(mode, str) or mode not in _DESKTOP_INTERACTION_MODES:
                logger.warning("[DesktopOverlay] Ignoring invalid interaction mode control")
                return
            await self._set_interaction_mode(mode, emit_event=True)
            return
        if command == "apply_window_bounds":
            bounds = _parse_runtime_window_bounds(payload)
            if bounds is None:
                logger.warning("[DesktopOverlay] Ignoring invalid window bounds control")
                return
            self._emit_detailed_log(
                "runtime_control command=apply_window_bounds "
                f"x={bounds['x']} y={bounds['y']} width={bounds['width']} "
                f"height={bounds['height']}"
            )
            await self._cancel_bounds_sample()
            await self._apply_window_bounds_with_retries(bounds)
            return
        if command == "apply_visual_config":
            visual_state = _parse_runtime_visual_state(payload)
            if visual_state is None:
                logger.warning("[DesktopOverlay] Ignoring invalid visual config control")
                return
            self._visual_state = visual_state
            self._emit_detailed_log(
                "runtime_control command=apply_visual_config "
                f"text_scale={visual_state.text_scale} "
                f"background_alpha={visual_state.background_alpha} "
                f"outline_width={visual_state.outline_width}"
            )
            self._render_page()
            return
        logger.warning("[DesktopOverlay] Ignoring unsupported desktop runtime control: %r", command)

    def _handle_page(self, page: Any) -> None:
        self._page = page
        try:
            self._configure_base_window(page)
            # Flush window-size changes to Flutter before rendering so the
            # window is never shown at the wrong initial size.
            if hasattr(page, "update"):
                page.update()
            self._render_page()
            self._page_ready.set()
        except Exception as exc:
            self._page_start_error = exc
            self._page_ready.set()
            raise

    def _configure_base_window(self, page: Any) -> None:
        import flet as ft

        window = page.window
        page.title = t_for_locale(
            self._locale,
            "desktop_overlay.window.title",
            default="PuriPuly Overlay",
        )
        window.icon = "icons/icon.ico"
        window.frameless = True
        window.always_on_top = True
        window.shadow = False
        window.skip_task_bar = False
        window.resizable = False
        window.maximizable = False
        window.bgcolor = ft.Colors.TRANSPARENT
        window.ignore_mouse_events = (
            self._interaction_mode == _DESKTOP_INTERACTION_MODE_PASS_THROUGH
        )
        if self._preview_catalog is not None:
            size_preset = self._preview_selected_size_preset()
            window.width = max(
                size_preset.window_width,
                _DESKTOP_PREVIEW_STAGE_WIDTH,
            )
            window.height = max(size_preset.window_height, _DESKTOP_PREVIEW_STAGE_HEIGHT)
        elif self._startup_window_bounds is not None:
            bounds = self._startup_window_bounds
            window.left = bounds["x"]
            window.top = bounds["y"]
            window.width = bounds["width"]
            window.height = bounds["height"]
            self._current_window_bounds = dict(bounds)
            self._programmatic_bounds_echo_suppression = _ProgrammaticBoundsEchoSuppression(
                signature=_bounds_signature(bounds),
                expires_at=time.monotonic() + _PROGRAMMATIC_BOUNDS_ECHO_SUPPRESSION_S,
            )
            # Keep the window hidden until _reassert_startup_bounds confirms the
            # native OS window has actually been resized to this size. Revealing
            # immediately would show the wrong-sized native frame for a beat.
            self._pending_startup_reveal = True
            # The startup resize can silently fail to commit to the native frame on
            # some machines (Flet reports the right size but the OS window stays
            # wrong). Arm a forced re-assert for the first time the user enters edit
            # mode so it self-corrects instead of needing a manual preset change.
            self._needs_bounds_reassert_on_edit = True
            if hasattr(window, "visible"):
                window.visible = False
            logger.info(
                "[DesktopOverlay][WindowConfig] startup_bounds width=%s height=%s x=%s y=%s "
                "(window hidden until resize confirmed)",
                bounds["width"], bounds["height"], bounds["x"], bounds["y"],
            )
        else:
            # Always set explicit dimensions when no saved bounds — Flet's
            # default window size is unpredictable and can match the main window.
            window.width = DESKTOP_FLET_DEFAULT_WIDTH
            window.height = DESKTOP_FLET_DEFAULT_HEIGHT
            self._current_window_bounds = {
                "x": getattr(window, "left", 0) or 0,
                "y": getattr(window, "top", 0) or 0,
                "width": DESKTOP_FLET_DEFAULT_WIDTH,
                "height": DESKTOP_FLET_DEFAULT_HEIGHT,
            }
            logger.info(
                "[DesktopOverlay][WindowConfig] NO startup_bounds -> default width=%s height=%s",
                DESKTOP_FLET_DEFAULT_WIDTH, DESKTOP_FLET_DEFAULT_HEIGHT,
            )
        window.on_event = self._on_window_event
        if hasattr(window, "min_width"):
            window.min_width = DESKTOP_FLET_MIN_WIDTH
        if hasattr(window, "min_height"):
            window.min_height = DESKTOP_FLET_MIN_HEIGHT
        if self._preview_catalog is not None:
            page.on_keyboard_event = self._on_preview_keyboard_event
        page.bgcolor = ft.Colors.TRANSPARENT
        if hasattr(page, "padding"):
            page.padding = 0
        if hasattr(page, "spacing"):
            page.spacing = 0
        # Register bundled CJK font so Chinese/Japanese render correctly
        try:
            from puripuly_heart.ui.fonts import fonts_dir as _fonts_dir
            _cjk_ttc = _fonts_dir() / "NotoSansCJK-Medium.ttc"
            if _cjk_ttc.is_file():
                _existing = dict(page.fonts or {})
                _existing[_DESKTOP_CAPTION_CJK_FONT_FAMILY] = f"/fonts/NotoSansCJK-Medium.ttc"
                page.fonts = _existing
        except Exception:
            pass

    def _on_empty_lock_action_click(self, _event: object | None = None) -> None:
        self._run_page_task(self._lock_from_empty_action)

    def _build_sample_overlay_snapshot(self) -> OverlayPresentationSnapshot:
        """A representative caption snapshot for edit mode. Reflects the user's
        current display settings (show_translation, show_peer_original, show_romanization,
        single_turn_mode) so the band accurately represents what live captions look like."""

        vs = self._visual_state
        show_romanization = bool(getattr(vs, "show_romanization", True))
        single_turn = bool(getattr(vs, "single_turn_mode", True))
        show_translation = bool(getattr(vs, "show_translation", True))
        show_peer_original = bool(getattr(vs, "show_peer_original", True))

        # Reflect the user's actual dashboard language choices: the peer speaks
        # `peer_source_language` and we translate INTO `peer_target_language` (their
        # reading language). Falls back to a zh→en sample when languages aren't known.
        source_lang = getattr(vs, "peer_source_language", "") or "zh-CN"
        reading_lang = getattr(vs, "peer_target_language", "") or "en"
        source_text, source_roman = _desktop_sample_sentence_for(source_lang)
        reading_text, _reading_roman = _desktop_sample_sentence_for(reading_lang)

        # Build peer secondary (source + optional romanization)
        if show_peer_original:
            peer_secondary = (
                f"{source_roman}\n{source_text}"
                if (show_romanization and source_roman)
                else source_text
            )
        else:
            peer_secondary = ""

        peer_block = OverlayPresentationBlock(
            id="__edit_sample_peer__",
            occupant_key="__edit_sample_peer__",
            appearance_seq=0,
            channel="peer",
            block_variant="finalized",
            primary_text=reading_text if show_translation else "",
            secondary_text=peer_secondary,
            secondary_enabled=show_peer_original,
            primary_language=reading_lang,
            secondary_language=source_lang,
        )
        if single_turn:
            return OverlayPresentationSnapshot(revision=0, blocks=[peer_block])

        # Two-turn: self block. primary_text = what user said (Japanese source + optional romaji),
        # secondary_text = English translation shown to peer.
        ja_original = "これはサンプルメッセージです"
        ja_romaji = "kore wa sanpuru messēji desu"
        self_primary = f"{ja_romaji}\n{ja_original}" if show_romanization else ja_original
        self_secondary = "This is a sample message" if show_translation else ""

        self_block = OverlayPresentationBlock(
            id="__edit_sample_self__",
            occupant_key="__edit_sample_self__",
            appearance_seq=1,
            channel="self",
            block_variant="finalized",
            primary_text=self_primary,
            secondary_text=self_secondary,
            secondary_enabled=show_translation,
            primary_language="ja",
            secondary_language="en",
        )
        return OverlayPresentationSnapshot(revision=0, blocks=[peer_block, self_block])

    def _build_desktop_edit_hint(self, ft: Any, plan: Any) -> Any:
        """The move instructions shown pinned in edit mode."""

        return ft.Text(
            t_for_locale(self._locale, _DESKTOP_EDIT_HINT_I18N_KEY),
            size=max(11, int(plan.secondary_font_size * 0.9)),
            color=_DESKTOP_EDIT_ACCENT_COLOR,
            weight=ft.FontWeight.W_600,
            text_align=ft.TextAlign.CENTER,
            no_wrap=False,
        )

    async def _lock_from_empty_action(self) -> None:
        await self._set_interaction_mode(
            _DESKTOP_INTERACTION_MODE_PASS_THROUGH,
            emit_event=True,
        )

    def _render_page(self) -> None:
        page = self._page
        if page is None:
            return
        import flet as ft

        if self._preview_catalog is not None:
            root = self._build_preview_root(ft)
            if hasattr(page, "clean"):
                page.clean()
            else:
                page.controls.clear()
            page.add(root)
            self._apply_interaction_window_chrome()
            self._reveal_window_if_supported()
            page.update()
            return

        _cur = self._current_window_bounds
        plan_window_width = (
            _cur["width"] if _cur else _page_window_number(page, "width", DESKTOP_FLET_DEFAULT_WIDTH)
        )
        plan_window_height = (
            _cur["height"] if _cur else _page_window_number(page, "height", DESKTOP_FLET_DEFAULT_HEIGHT)
        )
        raw_plan = build_desktop_caption_plan(
            self._snapshot,
            window_width=plan_window_width,
            window_height=plan_window_height,
            visual_state=self._visual_state,
            interaction_mode=self._interaction_mode,
            locale=self._locale,
        )
        previous_width_floors = dict(self._caption_card_width_floor_by_block)
        plan = self._plan_with_grow_only_caption_card_widths(raw_plan)
        self._emit_caption_width_diagnostics(raw_plan, plan, previous_width_floors)
        caption_surface = build_desktop_caption_surface(plan)
        if self._interaction_mode == _DESKTOP_INTERACTION_MODE_EDIT:
            # Size the chrome EXPLICITLY to the real window (cur_bounds is correct at
            # startup; page.window.width/height lag the first render and made small
            # presets render as a thin strip until a manual resize).
            cur_bounds = self._current_window_bounds
            if cur_bounds:
                win_w = cur_bounds["width"]
                win_h = cur_bounds["height"]
            else:
                win_w = _page_window_number(page, "width", plan.window_width)
                win_h = _page_window_number(page, "height", plan.window_height)

            # Render the body through the REAL caption path so text sits exactly where
            # live subtitles land. When there's no live caption, substitute a sample
            # snapshot (same layout) so the user positions against real placement.
            if plan.lines:
                body_surface = caption_surface
                body_plan = plan
            else:
                sample_plan = self._plan_with_grow_only_caption_card_widths(
                    build_desktop_caption_plan(
                        self._build_sample_overlay_snapshot(),
                        window_width=win_w,
                        window_height=win_h,
                        visual_state=self._visual_state,
                        interaction_mode=self._interaction_mode,
                        locale=self._locale,
                    )
                )
                body_surface = build_desktop_caption_surface(sample_plan)
                body_plan = sample_plan

            logger.info(
                "[DesktopOverlay][EditChrome] plan_window=%sx%s chrome=%sx%s "
                "page_window=%sx%s slots=%s lines=%s",
                plan.window_width, plan.window_height, win_w, win_h,
                _page_window_number(page, "width", -1),
                _page_window_number(page, "height", -1),
                len(plan.slots), len(plan.lines),
            )

            # Band always covers the full overlay window so the teal border never
            # shrinks to hug content — it always shows the complete caption zone.
            band_h = int(win_h)
            band_top = 0.0

            lock_label = desktop_empty_lock_action_label(self._locale)
            lock_action = ft.Container(
                content=ft.Icon(ft.Icons.LOCK_OPEN, size=20, color=_DESKTOP_EDIT_ACCENT_COLOR),
                on_click=self._on_empty_lock_action_click,
                tooltip=lock_label,
                padding=6,
                border_radius=6,
                bgcolor="#1d1f24c0",
            )
            stack_children = [
                ft.Container(left=0, top=0, right=0, bottom=0, bgcolor=ft.Colors.TRANSPARENT),
                ft.Container(
                    left=0, right=0, top=band_top, height=band_h,
                    bgcolor="#1d1f24d8",
                    border_radius=plan.border_radius,
                    clip_behavior=ft.ClipBehavior.HARD_EDGE,
                ),
                ft.Container(
                    left=0, top=0, right=0, bottom=0,
                    alignment=ft.alignment.center,
                    content=body_surface,
                ),
                ft.Container(
                    left=0, right=0, top=band_top, height=band_h,
                    bgcolor=ft.Colors.TRANSPARENT,
                    border=ft.border.all(2, _DESKTOP_EDIT_ACCENT_COLOR),
                    border_radius=plan.border_radius,
                ),
                ft.Container(top=band_top + 6, right=8, content=lock_action),
            ]
            chrome = ft.Container(
                width=win_w,
                height=win_h,
                content=ft.Stack(stack_children, width=win_w, height=win_h),
            )
            drag_area = ft.WindowDragArea(
                content=chrome,
                maximizable=False,
            )
            content_kind = "drag_area"
            content = drag_area
        elif self._active_banner_visible():
            # One-time "overlay active" confirmation while locked (armed in start()).
            # Drawn over whatever the locked content would be so it's visible even at
            # 0 background transparency.
            content_kind = "active_banner"
            content = self._build_active_banner_control(ft, plan)
            logger.info("[DesktopOverlay][Banner] rendering active banner (locked)")
        else:
            if plan.surface_visible:
                content_kind = "caption_surface"
                content = caption_surface
            else:
                content_kind = "transparent_host"
                content = build_desktop_transparent_sizing_host(plan)
        self._emit_detailed_log(
            "render "
            f"revision={self._snapshot.revision} "
            f"blocks={len(self._snapshot.blocks)} "
            f"interaction_mode={self._interaction_mode} "
            f"surface_visible={plan.surface_visible} "
            f"line_count={len(plan.lines)} "
            f"content_kind={content_kind} "
            f"window={plan.window_width}x{plan.window_height} "
            f"background_alpha={plan.background_alpha}"
        )
        self._emit_render_transition(
            _DesktopRenderTrace(
                content_kind=content_kind,
                surface_visible=plan.surface_visible,
                slot_count=len(plan.slots),
                line_count=len(plan.lines),
                window_width=plan.window_width,
                window_height=plan.window_height,
                background_alpha=plan.background_alpha,
            )
        )
        _edit = self._interaction_mode == _DESKTOP_INTERACTION_MODE_EDIT
        # The active banner uses the same window-filling layout as the edit chrome
        # (expand + top-left) so it reliably gets real bounds and paints.
        _window_filling = _edit or content_kind == "active_banner"
        root = ft.Container(
            content=content,
            padding=0,
            bgcolor=ft.Colors.TRANSPARENT,
            # Edit chrome is explicitly window-sized, so pin it to the top-left origin
            # rather than centering (centering a window-sized box is a no-op but can
            # interact badly with expand); captions keep centering.
            alignment=ft.alignment.top_left if _window_filling else ft.alignment.center,
            expand=_window_filling,
        )

        if hasattr(page, "clean"):
            page.clean()
        else:
            page.controls.clear()
        page.add(root)
        self._apply_interaction_window_chrome()
        page.update()
        self._reveal_window_if_supported()

    def _apply_interaction_window_chrome(self) -> None:
        page = self._page
        if page is None:
            return
        locked = self._interaction_mode == _DESKTOP_INTERACTION_MODE_PASS_THROUGH
        window = page.window
        # While the one-time "overlay active" banner is showing, keep the window
        # interactive (NOT click-through). A fully click-through transparent layered
        # window (WS_EX_TRANSPARENT) does not reliably composite freshly-rendered
        # content on Windows — which is why the banner rendered (per logs) but stayed
        # invisible while locked, yet the edit chrome (interactive) shows fine. The
        # banner is brief and only at startup, so dropping click-through is harmless.
        window.ignore_mouse_events = locked and not self._active_banner_visible()

    def _reveal_window_if_supported(self, *, force: bool = False) -> None:
        if self._pending_startup_reveal and not force:
            # Held hidden until the startup resize lands; see _reassert_startup_bounds.
            return
        page = self._page
        if page is None:
            return
        window = page.window
        if hasattr(window, "visible"):
            window.visible = True
        if self._pending_startup_reveal:
            self._pending_startup_reveal = False
            logger.info(
                "[DesktopOverlay][Startup] window revealed after confirmed resize "
                "width=%s height=%s",
                getattr(window, "width", None), getattr(window, "height", None),
            )
            self._arm_active_banner_on_reveal()

    def _arm_active_banner_on_reveal(self) -> None:
        """Arm the one-time "overlay active" banner now that the window is actually
        visible. Only while LOCKED (pass-through) — in edit mode the chrome already
        shows it's active. Timed from here so the visible duration is short and
        consistent. The fade task drives opacity down and restores click-through."""
        if self._startup_active_banner_shown:
            return
        if self._interaction_mode != _DESKTOP_INTERACTION_MODE_PASS_THROUGH:
            return
        self._startup_active_banner_shown = True
        self._active_banner_opacity = 1.0
        total = _DESKTOP_ACTIVE_BANNER_VISIBLE_SECONDS + _DESKTOP_ACTIVE_BANNER_FADE_SECONDS
        self._active_banner_until = time.monotonic() + total
        logger.info("[DesktopOverlay][Banner] armed on reveal total=%.1fs", total)
        self._render_page()
        self._run_page_task(self._run_active_banner_fade)

    async def _run_active_banner_fade(self) -> None:
        try:
            await asyncio.sleep(_DESKTOP_ACTIVE_BANNER_VISIBLE_SECONDS)
            if self._closed.is_set() or self._page is None:
                return
            # Trigger Flet's native opacity animation by flipping the banner control's
            # opacity to 0 and updating just that control (smooth, GPU-driven) rather
            # than re-rendering the page per step.
            self._active_banner_opacity = 0.0
            ctrl = self._active_banner_ctrl
            if ctrl is not None:
                try:
                    ctrl.opacity = 0.0
                    if getattr(ctrl, "page", None) is not None:
                        ctrl.update()
                except Exception:
                    pass
            await asyncio.sleep(_DESKTOP_ACTIVE_BANNER_FADE_SECONDS + 0.1)
        except asyncio.CancelledError:
            return
        if self._closed.is_set() or self._page is None:
            return
        # Done: drop the banner and restore click-through (via _render_page chrome).
        self._active_banner_until = None
        self._active_banner_opacity = 1.0
        self._active_banner_ctrl = None
        self._render_page()

    def _active_banner_visible(self) -> bool:
        deadline = self._active_banner_until
        return (
            deadline is not None
            and time.monotonic() < deadline
            and self._interaction_mode == _DESKTOP_INTERACTION_MODE_PASS_THROUGH
        )

    def _build_active_banner_control(self, ft: Any, plan: Any) -> Any:
        # One full-window band: the dim background is bounded by the teal outline (the
        # outline hugs the whole caption zone, same bounds as the edit-mode sample), with
        # the confirmation text centered inside it.
        win_w = float(getattr(plan, "window_width", 0) or 0)
        win_h = float(getattr(plan, "window_height", 0) or 0)
        border_radius = getattr(plan, "border_radius", 12)
        ctrl = ft.Container(
            width=win_w or None,
            height=win_h or None,
            opacity=max(0.0, min(1.0, self._active_banner_opacity)),
            # Native opacity animation so the fade-out is smooth; _run_active_banner_fade
            # just flips opacity to 0 and Flutter animates it over this duration.
            animate_opacity=int(_DESKTOP_ACTIVE_BANNER_FADE_SECONDS * 1000),
            bgcolor="#0b0d10e0",
            border=ft.border.all(2, _DESKTOP_EDIT_ACCENT_COLOR),
            border_radius=border_radius,
            alignment=ft.alignment.center,
            content=ft.Text(
                t_for_locale(self._locale, _DESKTOP_OVERLAY_ACTIVE_BANNER_I18N_KEY),
                size=max(18, int(getattr(plan, "primary_font_size", 24) * 0.95)),
                weight=ft.FontWeight.W_700,
                color=_DESKTOP_EDIT_ACCENT_COLOR,
                text_align=ft.TextAlign.CENTER,
                no_wrap=True,
            ),
        )
        self._active_banner_ctrl = ctrl
        return ctrl

    def hide_window_safely(self) -> None:
        """Best-effort hide of the native window.

        Used when the overlay runtime is failing so a broken/half-rendered window
        isn't left stuck on screen (the user reported a "black bar I couldn't get
        rid of" after a crash). Fully guarded — never raises.
        """
        page = self._page
        if page is None:
            return
        try:
            window = page.window
            if hasattr(window, "visible"):
                window.visible = False
            window_update = getattr(window, "update", None)
            if callable(window_update):
                window_update()
            page_update = getattr(page, "update", None)
            if callable(page_update):
                page_update()
        except Exception:
            pass

    def _build_preview_root(self, ft: Any) -> Any:
        preview_plan = self._current_preview_caption_plan()
        caption_surface = build_desktop_caption_surface(preview_plan)
        if preview_plan.full_window_background_visible and not preview_plan.slots:
            caption_surface = ft.Stack(
                controls=[
                    caption_surface,
                    build_desktop_empty_lock_action(
                        preview_plan,
                        label=desktop_empty_lock_action_label(self._locale),
                        on_click=self._on_empty_lock_action_click,
                    ),
                ],
                width=preview_plan.window_width,
                height=preview_plan.window_height,
                alignment=ft.alignment.center,
            )
        return ft.Container(
            content=ft.Column(
                controls=[
                    self._build_preview_controls(ft),
                    self._build_preview_surface_backdrop(ft, caption_surface),
                ],
                spacing=12,
                horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                scroll=ft.ScrollMode.AUTO,
            ),
            padding=16,
            bgcolor="#101827",
            alignment=ft.alignment.center,
        )

    def _build_preview_controls(self, ft: Any) -> Any:
        catalog = self._preview_catalog
        if catalog is None:
            return ft.Container()
        labels = catalog.labels
        return ft.Column(
            controls=[
                self._build_preview_button_group(
                    ft,
                    labels.fixture,
                    [
                        (fixture.id, fixture.label, fixture.id == self._preview_fixture_id)
                        for fixture in catalog.fixtures
                    ],
                    self._set_preview_fixture,
                ),
                self._build_preview_button_group(
                    ft,
                    labels.size_preset,
                    [
                        (preset.id, preset.label, preset.id == self._preview_size_preset_id)
                        for preset in catalog.size_presets
                    ],
                    self._set_preview_size_preset,
                ),
                self._build_preview_button_group(
                    ft,
                    labels.background_alpha,
                    [
                        (
                            str(value),
                            _background_transparency_label_for_alpha(value),
                            value == self._preview_background_alpha,
                        )
                        for value in catalog.background_alpha_presets
                    ],
                    lambda value: self._set_preview_background_alpha(float(value)),
                ),
                self._build_preview_button_group(
                    ft,
                    labels.background_surface,
                    [
                        (
                            surface.id,
                            surface.label,
                            surface.id == self._preview_background_surface_id,
                        )
                        for surface in catalog.background_surfaces
                    ],
                    self._set_preview_background_surface,
                ),
            ],
            spacing=6,
            horizontal_alignment=ft.CrossAxisAlignment.CENTER,
            tight=True,
        )

    def _build_preview_button_group(
        self,
        ft: Any,
        label: str,
        items: list[tuple[str, str, bool]],
        on_select: Callable[[str], None],
    ) -> Any:
        return ft.Column(
            controls=[
                ft.Text(label, size=12, weight=ft.FontWeight.BOLD, color="#FFE7D6"),
                ft.Row(
                    controls=[
                        ft.ElevatedButton(
                            text=text,
                            on_click=lambda _event, selected=value: self._select_preview(
                                selected,
                                on_select,
                            ),
                            disabled=selected,
                        )
                        for value, text, selected in items
                    ],
                    spacing=6,
                    alignment=ft.MainAxisAlignment.CENTER,
                    wrap=True,
                ),
            ],
            spacing=4,
            horizontal_alignment=ft.CrossAxisAlignment.CENTER,
            tight=True,
        )

    def _build_preview_surface_backdrop(self, ft: Any, caption_surface: Any) -> Any:
        surface = self._preview_selected_background_surface()
        size_preset = self._preview_selected_size_preset()
        controls: list[Any] = []
        if surface.id == "busy":
            controls.append(self._build_preview_busy_background(ft, size_preset))
        controls.append(caption_surface)
        content: Any = caption_surface
        if len(controls) > 1:
            content = ft.Stack(
                controls=controls,
                width=size_preset.window_width,
                height=size_preset.window_height,
                alignment=ft.alignment.center,
            )
        return ft.Container(
            content=content,
            width=size_preset.window_width,
            height=size_preset.window_height,
            bgcolor=surface.bgcolor,
            padding=24,
            border_radius=20,
            alignment=ft.alignment.center,
        )

    def _build_preview_busy_background(
        self,
        ft: Any,
        size_preset: DesktopOverlayPreviewSizePreset,
    ) -> Any:
        colors = (
            "#475569",
            "#7C3AED",
            "#0EA5E9",
            "#F97316",
            "#22C55E",
            "#334155",
        )
        rows = []
        for row_index in range(5):
            rows.append(
                ft.Row(
                    controls=[
                        ft.Container(
                            width=140 + (column_index % 3) * 46,
                            height=54 + ((row_index + column_index) % 2) * 22,
                            bgcolor=colors[(row_index + column_index) % len(colors)],
                            border_radius=14,
                            opacity=0.72,
                        )
                        for column_index in range(5)
                    ],
                    spacing=12,
                    alignment=ft.MainAxisAlignment.CENTER,
                )
            )
        return ft.Container(
            content=ft.Column(
                controls=rows,
                spacing=12,
                horizontal_alignment=ft.CrossAxisAlignment.CENTER,
            ),
            width=size_preset.window_width,
            height=size_preset.window_height,
            alignment=ft.alignment.center,
        )

    def _select_preview(self, value: str, on_select: Callable[[str], None]) -> None:
        on_select(value)
        self._render_page()

    def _set_preview_fixture(self, fixture_id: str) -> None:
        catalog = self._preview_catalog
        if catalog is None or not any(fixture.id == fixture_id for fixture in catalog.fixtures):
            return
        self._preview_fixture_id = fixture_id
        self._snapshot = self._preview_selected_fixture().snapshot

    def _set_preview_background_alpha(self, value: float) -> None:
        catalog = self._preview_catalog
        if catalog is None or value not in catalog.background_alpha_presets:
            return
        self._preview_background_alpha = value
        self._visual_state = self._preview_visual_state()

    def _set_preview_size_preset(self, preset_id: str) -> None:
        catalog = self._preview_catalog
        if catalog is None or not any(preset.id == preset_id for preset in catalog.size_presets):
            return
        self._preview_size_preset_id = preset_id
        self._apply_preview_window_size()
        self._visual_state = self._preview_visual_state()

    def _set_preview_background_surface(self, surface_id: str) -> None:
        catalog = self._preview_catalog
        if catalog is None or not any(
            surface.id == surface_id for surface in catalog.background_surfaces
        ):
            return
        self._preview_background_surface_id = surface_id

    def _preview_selected_fixture(self) -> DesktopOverlayPreviewFixture:
        catalog = self._preview_catalog
        assert catalog is not None
        for fixture in catalog.fixtures:
            if fixture.id == self._preview_fixture_id:
                return fixture
        return catalog.fixtures[0]

    def _preview_selected_background_surface(self) -> DesktopOverlayPreviewBackgroundSurface:
        catalog = self._preview_catalog
        assert catalog is not None
        for surface in catalog.background_surfaces:
            if surface.id == self._preview_background_surface_id:
                return surface
        return catalog.background_surfaces[0]

    def _preview_selected_size_preset(self) -> DesktopOverlayPreviewSizePreset:
        catalog = self._preview_catalog
        assert catalog is not None
        for preset in catalog.size_presets:
            if preset.id == self._preview_size_preset_id:
                return preset
        return catalog.size_presets[1]

    def _apply_preview_window_size(self) -> None:
        page = self._page
        if page is None or self._preview_catalog is None:
            return
        preset = self._preview_selected_size_preset()
        page.window.width = preset.window_width
        page.window.height = preset.window_height

    def _current_preview_caption_plan(self) -> DesktopCaptionPlan:
        preset = self._preview_selected_size_preset()
        plan = build_desktop_caption_plan(
            self._preview_selected_fixture().snapshot,
            window_width=preset.window_width,
            window_height=preset.window_height,
            visual_state=self._preview_visual_state(),
            interaction_mode=self._interaction_mode,
            locale=self._locale,
        )
        return self._plan_with_grow_only_caption_card_widths(plan)

    def _plan_with_grow_only_caption_card_widths(
        self,
        plan: DesktopCaptionPlan,
    ) -> DesktopCaptionPlan:
        if not plan.slots:
            self._caption_card_width_floor_by_block.clear()
            return plan
        if plan.full_window_background_visible:
            return plan

        active_keys = {_caption_card_width_memory_key(slot) for slot in plan.slots}
        for key in tuple(self._caption_card_width_floor_by_block):
            if key not in active_keys:
                del self._caption_card_width_floor_by_block[key]

        grown_slots: list[DesktopCaptionSlot] = []
        for slot in plan.slots:
            key = _caption_card_width_memory_key(slot)
            previous_width = self._caption_card_width_floor_by_block.get(key, 0.0)
            card_width = _clamp(max(slot.card_width, previous_width), 1.0, float(plan.window_width))
            self._caption_card_width_floor_by_block[key] = card_width
            grown_slots.append(
                replace(
                    slot,
                    card_width=card_width,
                    card_text_width=max(1.0, card_width - (plan.padding_horizontal * 2)),
                )
            )
        return replace(
            plan,
            slots=tuple(grown_slots),
            lines=tuple(line for slot in grown_slots for line in slot.lines),
        )

    def _emit_caption_width_diagnostics(
        self,
        raw_plan: DesktopCaptionPlan,
        applied_plan: DesktopCaptionPlan,
        previous_width_floors: dict[tuple[str, str, int], float],
    ) -> None:
        if self._logging_mode != "detailed":
            return
        raw_slots_by_key = {_caption_card_width_memory_key(slot): slot for slot in raw_plan.slots}
        for slot_index, slot in enumerate(applied_plan.slots):
            key = _caption_card_width_memory_key(slot)
            raw_slot = raw_slots_by_key.get(key)
            if raw_slot is None:
                continue
            previous_floor = previous_width_floors.get(key, 0.0)
            floor_hit = slot.card_width > raw_slot.card_width + 0.01
            self._emit_detailed_log(
                "render_width "
                f"revision={self._snapshot.revision} "
                f"slot={slot_index} "
                f"key={_caption_width_key_label(key)} "
                f"raw_card_width={raw_slot.card_width:.1f} "
                f"applied_card_width={slot.card_width:.1f} "
                f"raw_text_width={raw_slot.card_text_width:.1f} "
                f"applied_text_width={slot.card_text_width:.1f} "
                f"previous_floor={previous_floor:.1f} "
                f"floor_hit={floor_hit} "
                f"line_count={len(slot.lines)} "
                f"primary_len={sum(len(line.text) for line in slot.lines if line.slot == 'primary')} "
                f"secondary_len={sum(len(line.text) for line in slot.lines if line.slot == 'secondary')}"
            )

    def _emit_render_transition(self, trace: _DesktopRenderTrace) -> None:
        previous = self._last_render_trace
        self._last_render_trace = trace
        if previous is None:
            return
        self._emit_detailed_log(
            "render_transition "
            f"revision={self._snapshot.revision} "
            f"content_kind {previous.content_kind}->{trace.content_kind} "
            f"surface_visible {previous.surface_visible}->{trace.surface_visible} "
            f"slot_count {previous.slot_count}->{trace.slot_count} "
            f"line_count {previous.line_count}->{trace.line_count} "
            f"window {previous.window_width}x{previous.window_height}->"
            f"{trace.window_width}x{trace.window_height} "
            f"background_alpha {previous.background_alpha:.3f}->{trace.background_alpha:.3f}"
        )

    def _preview_visual_state(self) -> DesktopCaptionVisualState:
        return DesktopCaptionVisualState(
            background_alpha=self._preview_background_alpha,
        )

    def _on_preview_keyboard_event(self, event: object) -> None:
        key = str(getattr(event, "key", "")).lower()
        if key not in {"e", "escape"}:
            return
        self._run_page_task(self._return_preview_to_edit_mode)

    async def _return_preview_to_edit_mode(self) -> None:
        await self._set_interaction_mode(_DESKTOP_INTERACTION_MODE_EDIT, emit_event=True)

    async def _set_interaction_mode(self, mode: str, *, emit_event: bool) -> None:
        if mode not in _DESKTOP_INTERACTION_MODES:
            return
        if mode == self._interaction_mode:
            return
        previous_mode = self._interaction_mode
        self._interaction_mode = mode
        render_started_at = time.monotonic()
        self._render_page()
        render_elapsed_ms = (time.monotonic() - render_started_at) * 1000.0
        logger.info(
            "[DesktopOverlay][Lock] interaction_mode %s->%s render_elapsed_ms=%.1f",
            previous_mode, mode, render_elapsed_ms,
        )
        if (
            mode == _DESKTOP_INTERACTION_MODE_EDIT
            and self._needs_bounds_reassert_on_edit
            and self._current_window_bounds is not None
        ):
            # A size change landed while we were locked (e.g. in Settings); the
            # native window may still be the old size. Now that the window is
            # interactive again (the render above cleared ignore_mouse_events),
            # force the size to actually commit so the edit chrome isn't laid out
            # for a size the OS frame never adopted.
            #
            # Schedule the SAME retried apply the dashboard resize uses
            # (_apply_window_bounds_with_retries) — a single apply isn't always
            # enough to make the frame stick; the dashboard does two and that's the
            # combination known to work. Schedule it as a proper coroutine METHOD
            # (never a lambda — Flet's page.run_task rejects non-coroutine-functions,
            # which is what crashed the overlay last time).
            self._needs_bounds_reassert_on_edit = False
            logger.info(
                "[DesktopOverlay][Resize] scheduling bounds re-assert on edit entry "
                "width=%s height=%s (resize had landed while locked)",
                self._current_window_bounds["width"],
                self._current_window_bounds["height"],
            )
            self._run_page_task(self._reassert_bounds_after_unlock)
        if emit_event:
            await self._emit_overlay_event({"event": "interaction_mode_changed", "mode": mode})

    async def _reassert_bounds_after_unlock(self) -> None:
        """Re-commit the window size after returning to edit mode.

        Runs in its own page task (decoupled from the lock-toggle dispatch) and is
        fully guarded, so a failure here can never tear the overlay down. Uses the
        retried apply path — the same one the live dashboard resize uses, which is
        the combination that actually makes the native frame adopt the new size.
        """
        bounds = self._current_window_bounds
        if bounds is None:
            return
        try:
            # force_resize: we're re-applying the SAME size Flet already holds, so a
            # plain set would be a no-op. Force a real native resize so a startup
            # resize that never actually committed gets corrected on first edit entry
            # (otherwise the window stays the wrong size until a manual preset change).
            await self._apply_window_bounds_with_retries(dict(bounds), force_resize=True)
        except Exception:
            logger.warning(
                "[DesktopOverlay][Resize] re-assert after unlock failed; "
                "overlay kept alive",
                exc_info=True,
            )

    def _apply_window_bounds(self, bounds: dict[str, int | float]) -> None:
        page = self._page
        if page is None:
            return
        if self._interaction_mode == _DESKTOP_INTERACTION_MODE_PASS_THROUGH:
            # Resizing a backgrounded pass-through window may not commit natively;
            # remember to re-assert once we're interactive again. See the flag's
            # definition in __init__.
            self._needs_bounds_reassert_on_edit = True
        if _page_window_size_differs_from_bounds(page, bounds):
            self._caption_card_width_floor_by_block.clear()
        self._emit_detailed_log(
            "apply_window_bounds "
            f"x={bounds['x']} y={bounds['y']} width={bounds['width']} "
            f"height={bounds['height']}"
        )
        self._apply_window_bounds_without_rerender(bounds)
        self._programmatic_bounds_echo_suppression = _ProgrammaticBoundsEchoSuppression(
            signature=_bounds_signature(bounds),
            expires_at=time.monotonic() + _PROGRAMMATIC_BOUNDS_ECHO_SUPPRESSION_S,
        )
        logger.info(
            "[DesktopOverlay][ApplyBounds] width=%s height=%s x=%s y=%s",
            bounds["width"], bounds["height"], bounds["x"], bounds["y"],
        )
        # Force the window size change to commit immediately. Just setting
        # window.width/height + page.update() can fail to take until the next
        # content change (e.g. a caption) — explicitly pushing the window object
        # makes the resize stick without waiting for subtitles.
        page = self._page
        if page is not None:
            window_update = getattr(getattr(page, "window", None), "update", None)
            if callable(window_update):
                try:
                    window_update()
                except Exception:
                    pass
        self._render_page()

    async def _apply_window_bounds_with_retries(
        self, bounds: dict[str, int | float], *, force_resize: bool = False
    ) -> None:
        # Apply the new bounds, then re-apply once more after a short delay. The
        # single re-apply guards against Flet occasionally deferring the very first
        # programmatic resize of a frameless window until the next frame. Each
        # re-apply is the same target size, so a stray render landing between them
        # sees the correct size either way.
        #
        # Claim this loop's generation so it supersedes any in-flight startup resize
        # loop (_reassert_startup_bounds): the user changed the size, so the startup
        # target is stale and that loop must stop touching the window. Without this,
        # the two loops race and interleave their writes.
        self._bounds_apply_generation += 1
        my_generation = self._bounds_apply_generation
        # force_resize=True: we're re-applying a size Flet already believes it has
        # (recovering from a startup resize that never committed natively). A plain
        # re-apply is a no-op, and a 1px nudge gets coalesced away by the OS. So first
        # resize to a CLEARLY different size and let it land, then settle to the real
        # target — the final apply is then a genuine change the native window honors.
        # This is exactly why the user's manual "pick another preset, then back" fixes
        # it; we just do it automatically.
        if force_resize and not (self._closed.is_set() or self._page is None):
            perturbed = dict(bounds)
            perturbed["width"] = max(1.0, float(bounds["width"]) + 48)
            perturbed["height"] = max(1.0, float(bounds["height"]) + 48)
            self._apply_window_bounds(perturbed)
            logger.info(
                "[DesktopOverlay][Resize] force-resize perturb width=%s height=%s",
                perturbed["width"], perturbed["height"],
            )
            try:
                await asyncio.sleep(0.12)
            except asyncio.CancelledError:
                return
            if self._bounds_apply_generation != my_generation:
                return
        for attempt_index, attempt_delay in enumerate((0.0, 0.2), start=1):
            if attempt_delay:
                try:
                    await asyncio.sleep(attempt_delay)
                except asyncio.CancelledError:
                    return
            if self._closed.is_set() or self._page is None:
                return
            if self._bounds_apply_generation != my_generation:
                logger.info(
                    "[DesktopOverlay][Resize] live resize superseded by a newer "
                    "bounds request (attempt=%s)",
                    attempt_index,
                )
                return
            self._apply_window_bounds(dict(bounds))
            logger.info(
                "[DesktopOverlay][Resize] live resize attempt=%s applied width=%s height=%s "
                "force_resize=%s",
                attempt_index, bounds["width"], bounds["height"], force_resize,
            )

    def _apply_window_bounds_without_rerender(self, bounds: dict[str, int | float]) -> None:
        page = self._page
        if page is None:
            return
        window = page.window
        # Non-resizable frameless windows can ignore a programmatic resize until some
        # other frame (a caption) forces it. Briefly allowing resize while applying
        # the size makes the native window accept it immediately.
        had_resizable = getattr(window, "resizable", None)
        try:
            window.resizable = True
        except Exception:
            pass
        # Set the target size DIRECTLY. A live preset change is a genuine size change,
        # so the native window accepts it. Forcing a native resize when re-applying a
        # size Flet already holds is handled one level up in
        # _apply_window_bounds_with_retries (it perturbs through a clearly different
        # size first) — NOT with a tiny jiggle here, which the OS coalesces away.
        window.left = bounds["x"]
        window.top = bounds["y"]
        window.width = bounds["width"]
        window.height = bounds["height"]
        if had_resizable is not None:
            try:
                window.resizable = had_resizable
            except Exception:
                pass
        self._current_window_bounds = dict(bounds)

    def _on_window_event(self, event: object) -> None:
        if self._closed.is_set():
            return
        if not _is_window_bounds_event(event):
            return
        self._emit_detailed_log(
            f"window_event type={getattr(event, 'type', getattr(event, 'data', None))} "
            f"interaction_mode={self._interaction_mode}"
        )
        if self._interaction_mode != _DESKTOP_INTERACTION_MODE_EDIT:
            self._emit_detailed_log(
                "bounds_sample dropped reason=event_interaction_mode "
                f"interaction_mode={self._interaction_mode}"
            )
            return
        self._run_page_task(self._schedule_bounds_sample)

    async def _schedule_bounds_sample(self) -> None:
        if self._closed.is_set():
            return
        await self._cancel_bounds_sample()
        if self._closed.is_set():
            return
        self._emit_detailed_log(
            f"bounds_sample scheduled interaction_mode={self._interaction_mode}"
        )
        self._bounds_sample_task = asyncio.create_task(self._emit_debounced_bounds_sample())

    async def _cancel_bounds_sample(self) -> None:
        task = self._bounds_sample_task
        self._bounds_sample_task = None
        if task is not None and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    async def _emit_debounced_bounds_sample(self) -> None:
        if self._closed.is_set():
            return
        if self._bounds_debounce_s > 0:
            await asyncio.sleep(self._bounds_debounce_s)
        if self._closed.is_set():
            return
        bounds = _sample_page_window_bounds(self._page)
        if bounds is None:
            self._emit_detailed_log("bounds_sample dropped reason=no_bounds")
            return
        signature = _bounds_signature(bounds)
        if self._is_programmatic_bounds_echo(signature):
            self._emit_detailed_log(
                "bounds_sample dropped reason=programmatic_echo "
                f"x={bounds['x']} y={bounds['y']} width={bounds['width']} "
                f"height={bounds['height']}"
            )
            return
        if self._interaction_mode != _DESKTOP_INTERACTION_MODE_EDIT:
            self._emit_detailed_log(
                "bounds_sample dropped reason=interaction_mode "
                f"interaction_mode={self._interaction_mode} "
                f"x={bounds['x']} y={bounds['y']} width={bounds['width']} "
                f"height={bounds['height']}"
            )
            return
        if signature == self._last_reported_bounds:
            self._emit_detailed_log(
                "bounds_sample dropped reason=unchanged "
                f"x={bounds['x']} y={bounds['y']} width={bounds['width']} "
                f"height={bounds['height']}"
            )
            return
        self._programmatic_bounds_echo_suppression = None
        self._last_reported_bounds = signature
        # The user just moved/resized the window themselves. If the startup
        # re-assert loop is still running (it re-applies the saved position for a
        # few seconds after launch), bump the generation so it bails — otherwise it
        # keeps snapping the window back to its origin while the user is dragging it.
        self._bounds_apply_generation += 1
        self._emit_detailed_log(
            "bounds_sample emitted source=user persist=True "
            f"x={bounds['x']} y={bounds['y']} width={bounds['width']} "
            f"height={bounds['height']}"
        )
        await self._emit_overlay_event(
            {
                "event": "window_bounds_changed",
                "source": "user",
                "persist": True,
                **bounds,
            }
        )

    def _run_page_task(self, func: Callable[[], Awaitable[None]]) -> None:
        if self._closed.is_set():
            return
        page = self._page
        if page is not None:
            run_task = getattr(page, "run_task", None)
            if callable(run_task):
                self._track_scheduled_callback_task(run_task(func))
                return
        self._track_scheduled_callback_task(asyncio.create_task(func()))

    async def _emit_overlay_event(self, payload: dict[str, object]) -> None:
        if self._closed.is_set():
            return
        if self._event_sink is None:
            return
        await self._event_sink({"type": "overlay_event", "payload": payload})

    def _set_logging_mode(self, mode: object) -> bool:
        try:
            normalized_mode = normalize_overlay_logging_mode(mode)
        except Exception:
            return False
        self._logging_mode = normalized_mode
        self._emit_detailed_log(f"logging_mode mode={normalized_mode}")
        return True

    def _emit_detailed_log(self, message: str) -> None:
        if self._logging_mode != "detailed":
            return
        print(f"[DesktopOverlay][Detail] {message}", flush=True)

    def _is_programmatic_bounds_echo(
        self,
        signature: tuple[float, float, float, float],
    ) -> bool:
        suppression = self._programmatic_bounds_echo_suppression
        if suppression is None:
            return False
        if time.monotonic() > suppression.expires_at:
            self._programmatic_bounds_echo_suppression = None
            return False
        return _bounds_signatures_close(signature, suppression.signature)

    def _track_scheduled_callback_task(self, task: object) -> None:
        if not isinstance(task, (asyncio.Future, ConcurrentFuture)):
            return
        self._scheduled_callback_tasks.add(task)
        task.add_done_callback(self._scheduled_callback_tasks.discard)

    async def _cancel_scheduled_callback_tasks(self) -> None:
        tasks = tuple(self._scheduled_callback_tasks)
        self._scheduled_callback_tasks.clear()
        if not tasks:
            return
        current_task = asyncio.current_task()
        awaitables: list[asyncio.Future[Any]] = []
        for task in tasks:
            if task is current_task:
                continue
            task.cancel()
            if isinstance(task, asyncio.Future):
                awaitables.append(task)
            else:
                awaitables.append(asyncio.wrap_future(task))
        if awaitables:
            await asyncio.gather(*awaitables, return_exceptions=True)


def _page_window_number(page: Any, field_name: str, default: int) -> int | float:
    return getattr(page.window, field_name, default) or default


def _page_window_size_differs_from_bounds(
    page: Any,
    bounds: dict[str, int | float],
) -> bool:
    window = page.window
    current_width = _finite_non_bool_number(getattr(window, "width", None))
    current_height = _finite_non_bool_number(getattr(window, "height", None))
    if current_width is None or current_height is None:
        return True
    width_changed = float(current_width) != float(bounds["width"])
    height_changed = float(current_height) != float(bounds["height"])
    return width_changed or height_changed


def _parse_runtime_window_bounds(
    payload: dict[str, object],
) -> dict[str, int | float] | None:
    x = _finite_non_bool_number(payload.get("x"))
    y = _finite_non_bool_number(payload.get("y"))
    width = _finite_non_bool_number(payload.get("width"))
    height = _finite_non_bool_number(payload.get("height"))
    if x is None or y is None or width is None or height is None:
        return None
    if width < DESKTOP_FLET_MIN_WIDTH or height < DESKTOP_FLET_MIN_HEIGHT:
        return None
    return {"x": x, "y": y, "width": width, "height": height}


def _parse_runtime_visual_state(payload: dict[str, object]) -> DesktopCaptionVisualState | None:
    text_scale = _finite_non_bool_number(payload.get("text_scale"))
    background_alpha = _finite_non_bool_number(payload.get("background_alpha"))
    outline_width_raw = payload.get("outline_width")
    if text_scale is None or background_alpha is None:
        return None
    if not DESKTOP_FLET_MIN_TEXT_SCALE <= text_scale <= DESKTOP_FLET_MAX_TEXT_SCALE:
        return None
    if (
        not DESKTOP_FLET_MIN_BACKGROUND_ALPHA
        <= background_alpha
        <= DESKTOP_FLET_MAX_BACKGROUND_ALPHA
    ):
        return None
    outline_width: float | None = None
    if outline_width_raw is not None:
        outline_number = _finite_non_bool_number(outline_width_raw)
        if outline_number is None:
            return None
        if not DESKTOP_FLET_MIN_OUTLINE_WIDTH <= outline_number <= DESKTOP_FLET_MAX_OUTLINE_WIDTH:
            return None
        outline_width = float(outline_number)
    show_romanization_raw = payload.get("show_romanization", True)
    single_turn_raw = payload.get("single_turn_mode", True)
    show_translation_raw = payload.get("show_translation", True)
    show_peer_original_raw = payload.get("show_peer_original", True)
    peer_source_language_raw = payload.get("peer_source_language", "")
    peer_target_language_raw = payload.get("peer_target_language", "")
    return DesktopCaptionVisualState(
        text_scale=float(text_scale),
        background_alpha=float(background_alpha),
        outline_width=outline_width,
        show_romanization=bool(show_romanization_raw),
        single_turn_mode=bool(single_turn_raw),
        show_translation=bool(show_translation_raw),
        show_peer_original=bool(show_peer_original_raw),
        peer_source_language=str(peer_source_language_raw or ""),
        peer_target_language=str(peer_target_language_raw or ""),
    )


def _sample_page_window_bounds(page: Any | None) -> dict[str, int | float] | None:
    if page is None:
        return None
    window = page.window
    bounds = {
        "x": _finite_non_bool_number(getattr(window, "left", None)),
        "y": _finite_non_bool_number(getattr(window, "top", None)),
        "width": _finite_non_bool_number(getattr(window, "width", None)),
        "height": _finite_non_bool_number(getattr(window, "height", None)),
    }
    if any(value is None for value in bounds.values()):
        return None
    typed_bounds = {key: value for key, value in bounds.items() if value is not None}
    if (
        typed_bounds["x"] == 0
        and typed_bounds["y"] == 0
        and typed_bounds["width"] == 0
        and typed_bounds["height"] == 0
    ):
        return None
    if typed_bounds["width"] <= 0 or typed_bounds["height"] <= 0:
        return None
    return typed_bounds


def _is_window_bounds_event(event: object) -> bool:
    event_type = getattr(event, "type", None)
    if event_type is None:
        event_type = getattr(event, "data", None)
    event_name = getattr(event_type, "name", None)
    if event_name is None:
        event_name = getattr(event_type, "value", None)
    if event_name is None:
        event_name = str(event_type)
    return str(event_name).split(".")[-1].upper() in _DESKTOP_WINDOW_BOUNDS_EVENT_NAMES


def _bounds_signature(bounds: dict[str, int | float]) -> tuple[float, float, float, float]:
    return (
        float(bounds["x"]),
        float(bounds["y"]),
        float(bounds["width"]),
        float(bounds["height"]),
    )


def _bounds_signatures_close(
    first: tuple[float, float, float, float],
    second: tuple[float, float, float, float],
) -> bool:
    return all(
        abs(left - right) <= _PROGRAMMATIC_BOUNDS_ECHO_TOLERANCE_PX
        for left, right in zip(first, second, strict=True)
    )


def _finite_non_bool_number(value: object) -> int | float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if not math.isfinite(value):
        return None
    return value


@dataclass(slots=True)
class PollingParentMonitor:
    parent_pid: int
    poll_interval_s: float = 1.0

    async def wait_for_parent_exit(self, stop_event: asyncio.Event) -> None:
        while not stop_event.is_set():
            if not self._pid_exists(self.parent_pid):
                return
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=self.poll_interval_s)
            except TimeoutError:
                continue

    @staticmethod
    def _pid_exists(parent_pid: int) -> bool:
        if parent_pid <= 0:
            return False
        try:
            os.kill(parent_pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        except OSError:
            return True
        return True


@dataclass(slots=True)
class BridgeDisconnectParentMonitor:
    """Windows-safe fallback when no parent handle can be opened.

    The bridge connection is owned by the parent process; if the parent exits, the
    bridge reader reports the disconnect. This monitor intentionally performs no
    PID probing so Windows fallback cannot signal or terminate the parent.
    """

    parent_pid: int

    async def wait_for_parent_exit(self, stop_event: asyncio.Event) -> None:
        _ = self.parent_pid
        await stop_event.wait()


@dataclass(slots=True)
class WindowsParentHandleMonitor:
    handle: object
    poll_interval_s: float = 0.25
    wait_handle_signaled: Callable[[object], bool] | None = None
    close_handle: Callable[[object], None] | None = None
    _closed: bool = field(init=False, default=False)

    async def wait_for_parent_exit(self, stop_event: asyncio.Event) -> None:
        wait_handle_signaled = self.wait_handle_signaled or _default_windows_handle_signaled
        try:
            while not stop_event.is_set():
                if await asyncio.to_thread(wait_handle_signaled, self.handle):
                    return
                try:
                    await asyncio.wait_for(stop_event.wait(), timeout=self.poll_interval_s)
                except TimeoutError:
                    continue
        finally:
            await asyncio.to_thread(self.close)

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        close_handle = self.close_handle or _default_close_windows_handle
        close_handle(self.handle)


def _default_open_windows_parent_handle(parent_pid: int) -> object | None:
    if os.name != "nt" or parent_pid <= 0:
        return None
    try:
        import ctypes

        synchronize = 0x00100000
        handle = ctypes.windll.kernel32.OpenProcess(synchronize, False, int(parent_pid))
    except Exception:
        return None
    if not handle:
        return None
    return int(handle)


def _default_windows_handle_signaled(handle: object) -> bool:
    if os.name != "nt":
        return False
    try:
        import ctypes

        wait_object_0 = 0x00000000
        result = ctypes.windll.kernel32.WaitForSingleObject(int(handle), 0)
    except Exception:
        return False
    return result == wait_object_0


def _default_close_windows_handle(handle: object) -> None:
    if os.name != "nt":
        return
    with contextlib.suppress(Exception):
        import ctypes

        ctypes.windll.kernel32.CloseHandle(int(handle))


def create_parent_monitor(
    parent_pid: int,
    *,
    is_windows: bool | None = None,
    open_windows_handle: Callable[[int], object | None] | None = None,
) -> ParentMonitor:
    windows = os.name == "nt" if is_windows is None else is_windows
    if windows:
        opener = open_windows_handle or _default_open_windows_parent_handle
        handle = opener(parent_pid)
        if handle is not None:
            return WindowsParentHandleMonitor(handle=handle)
        logger.warning(
            "[DesktopOverlay] Unable to open parent process handle; "
            "relying on bridge disconnect for parent-loss detection"
        )
        return BridgeDisconnectParentMonitor(parent_pid=parent_pid)
    return PollingParentMonitor(parent_pid=parent_pid)


def validate_desktop_bridge_url(bridge_url: str) -> str:
    try:
        parsed = urlsplit(bridge_url)
        port = parsed.port
    except ValueError as exc:
        raise ValueError("desktop overlay bridge_url is invalid") from exc

    if parsed.scheme != "ws":
        raise ValueError("desktop overlay bridge_url must use ws")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("desktop overlay bridge_url must not include credentials")
    if parsed.hostname not in _LOOPBACK_BRIDGE_HOSTS:
        raise ValueError("desktop overlay bridge_url must be loopback-only")
    if port is None or port <= 0:
        raise ValueError("desktop overlay bridge_url must include a positive port")
    return bridge_url


def load_renderer_manifest(config_path: Path) -> OverlayLaunchManifest:
    try:
        payload = json.loads(config_path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise DesktopOverlayStartupError(
            "manifest_invalid",
            "desktop overlay launch manifest is invalid",
        ) from exc
    if not isinstance(payload, dict):
        raise DesktopOverlayStartupError(
            "manifest_invalid",
            "desktop overlay launch manifest is invalid",
        )

    try:
        _validate_manifest_payload_shape(payload)
        manifest = OverlayLaunchManifest.from_dict(payload)
        _validate_runtime_manifest(manifest)
    except DesktopOverlayStartupError:
        raise
    except Exception as exc:
        raise DesktopOverlayStartupError(
            "manifest_invalid",
            "desktop overlay launch manifest is invalid",
        ) from exc
    return manifest


class DesktopOverlayRenderer:
    def __init__(
        self,
        manifest: OverlayLaunchManifest,
        *,
        window: RendererWindow | None = None,
        lifecycle_sink: LifecycleSink | None = None,
        parent_monitor: ParentMonitor | None = None,
    ) -> None:
        self.manifest = manifest
        self.lifecycle_sink = lifecycle_sink or StdoutLifecycleSink()
        self.window = window or FletDesktopRendererWindow(
            event_sink=self._emit_lifecycle,
            locale=manifest.locale,
            logging_mode=manifest.logging_mode,
        )
        self.parent_monitor = parent_monitor or create_parent_monitor(manifest.parent_pid)
        self._shutdown_event = asyncio.Event()
        self._shutdown_lock = asyncio.Lock()
        self._shutdown_complete = False
        self._websocket: Any | None = None
        self._tasks: set[asyncio.Task[_RuntimeOutcome | None]] = set()
        self._ui_queue: asyncio.Queue[tuple[str, object]] = asyncio.Queue()
        self._startup_pending_messages: asyncio.Queue[object] = asyncio.Queue()

    @property
    def is_shutdown(self) -> bool:
        return self._shutdown_complete

    async def run(self) -> int:
        unexpected_startup_failure_reason = "renderer_init_failed"
        try:
            _validate_runtime_manifest(self.manifest)
            unexpected_startup_failure_reason = "bridge_auth_failed"
            websocket = await self._connect_bridge()
            self._websocket = websocket
            await websocket.send(
                json.dumps({"type": "auth", "session_token": self.manifest.session_token})
            )
            unexpected_startup_failure_reason = "renderer_init_failed"
            initial_snapshot, initial_runtime_controls = (
                await self._receive_initial_snapshot_and_runtime_controls(websocket)
            )
            unexpected_startup_failure_reason = "window_configuration_failed"
            prime_startup_runtime_controls = getattr(
                self.window,
                "prime_startup_runtime_controls",
                None,
            )
            startup_runtime_controls_to_dispatch = initial_runtime_controls
            if callable(prime_startup_runtime_controls):
                startup_runtime_controls_to_dispatch = prime_startup_runtime_controls(
                    initial_runtime_controls
                )
            await self.window.start(initial_snapshot)
            for payload in startup_runtime_controls_to_dispatch:
                await self.window.dispatch_runtime_control(payload)
            unexpected_startup_failure_reason = "renderer_init_failed"
            self._start_runtime_tasks(websocket)
            await self._emit_lifecycle({"type": "overlay_ready"})
            outcome = await self._wait_for_runtime_outcome()
            return outcome.exit_code
        except DesktopOverlayStartupError as exc:
            await self._emit_lifecycle(
                {"type": "startup_error", "failure_reason": exc.failure_reason}
            )
            return _STARTUP_FAILURE_EXIT_CODE
        except Exception as exc:
            safe_exception_message = _redact_renderer_startup_exception_text(
                str(exc),
                self.manifest,
            )
            safe_exception_traceback = _redact_renderer_startup_exception_text(
                "".join(traceback.format_exception(type(exc), exc, exc.__traceback__)),
                self.manifest,
            )
            logger.warning(
                "[DesktopOverlay] Renderer startup failed: "
                "exception_type=%s exception_message=%s exception_traceback=%s",
                type(exc).__name__,
                safe_exception_message,
                safe_exception_traceback,
            )
            await self._emit_lifecycle(
                {"type": "startup_error", "failure_reason": unexpected_startup_failure_reason}
            )
            return _STARTUP_FAILURE_EXIT_CODE
        finally:
            await self.shutdown()

    async def shutdown(self) -> None:
        async with self._shutdown_lock:
            if self._shutdown_complete:
                return
            self._shutdown_event.set()

            websocket = self._websocket
            self._websocket = None
            if websocket is not None:
                with contextlib.suppress(Exception):
                    await websocket.close()

            with contextlib.suppress(Exception):
                await self.window.close()

            current_task = asyncio.current_task()
            pending_tasks = [
                task for task in self._tasks if task is not current_task and not task.done()
            ]
            for task in pending_tasks:
                task.cancel()
            if pending_tasks:
                await asyncio.gather(*pending_tasks, return_exceptions=True)
            if self._tasks:
                await asyncio.gather(*self._tasks, return_exceptions=True)
            self._tasks.clear()
            await _close_parent_monitor(self.parent_monitor)
            self._shutdown_complete = True

    async def _connect_bridge(self) -> Any:
        timeout_s = max(0.1, self.manifest.startup_deadline_ms / 1000.0)
        try:
            return await asyncio.wait_for(
                websockets.connect(self.manifest.bridge_url, ping_interval=None),
                timeout=timeout_s,
            )
        except Exception as exc:
            raise DesktopOverlayStartupError(
                "bridge_auth_failed",
                "desktop overlay bridge authentication failed",
            ) from exc

    async def _receive_initial_snapshot_and_runtime_controls(
        self,
        websocket: Any,
    ) -> tuple[OverlayPresentationSnapshot, tuple[dict[str, object], ...]]:
        snapshot = await self._receive_initial_snapshot(websocket)
        runtime_controls = await self._drain_startup_runtime_controls(websocket)
        return snapshot, runtime_controls

    async def _receive_initial_snapshot(self, websocket: Any) -> OverlayPresentationSnapshot:
        timeout_s = max(0.1, self.manifest.startup_deadline_ms / 1000.0)
        try:
            raw_message = await asyncio.wait_for(websocket.recv(), timeout=timeout_s)
            message = _load_bridge_message(raw_message)
        except DesktopOverlayStartupError:
            raise
        except Exception as exc:
            raise DesktopOverlayStartupError(
                "renderer_init_failed",
                "desktop overlay initial snapshot is invalid",
            ) from exc

        message_type = message.get("type")
        if message_type == "auth_error":
            raise DesktopOverlayStartupError(
                "bridge_auth_failed",
                "desktop overlay bridge authentication failed",
            )
        if message_type != "snapshot":
            raise DesktopOverlayStartupError(
                "renderer_init_failed",
                "desktop overlay initial snapshot is invalid",
            )
        try:
            return _parse_snapshot_message(message)
        except Exception as exc:
            raise DesktopOverlayStartupError(
                "renderer_init_failed",
                "desktop overlay initial snapshot is invalid",
            ) from exc

    async def _drain_startup_runtime_controls(
        self,
        websocket: Any,
    ) -> tuple[dict[str, object], ...]:
        controls: list[dict[str, object]] = []
        deadline = asyncio.get_running_loop().time() + _INITIAL_RUNTIME_CONTROL_DRAIN_TIMEOUT_S
        while True:
            timeout_s = max(0.0, deadline - asyncio.get_running_loop().time())
            if timeout_s <= 0:
                break
            try:
                raw_message = await asyncio.wait_for(websocket.recv(), timeout=timeout_s)
            except TimeoutError:
                break
            except Exception:
                break
            try:
                message = _load_bridge_message(raw_message)
            except ValueError:
                await self._startup_pending_messages.put(raw_message)
                continue
            if message.get("type") != "runtime_control":
                await self._startup_pending_messages.put(raw_message)
                continue
            payload = _parse_runtime_control_payload(message)
            if payload is None:
                raise DesktopOverlayStartupError(
                    "runtime_control_invalid",
                    "desktop overlay initial runtime control is invalid",
                )
            controls.append(payload)
        return tuple(controls)

    def _start_runtime_tasks(self, websocket: Any) -> None:
        self._tasks = {
            asyncio.create_task(self._bridge_reader_loop(websocket)),
            asyncio.create_task(self._parent_monitor_loop()),
            asyncio.create_task(self._window_loop()),
            asyncio.create_task(self._ui_update_loop()),
            asyncio.create_task(self._heartbeat_loop()),
        }

    async def _wait_for_runtime_outcome(self) -> _RuntimeOutcome:
        while self._tasks:
            done, _pending = await asyncio.wait(
                self._tasks,
                return_when=asyncio.FIRST_COMPLETED,
            )
            for task in done:
                self._tasks.discard(task)
                try:
                    result = task.result()
                except asyncio.CancelledError:
                    continue
                except Exception as exc:
                    logger.warning(
                        "[DesktopOverlay] Runtime task failed: exception_type=%s",
                        type(exc).__name__,
                    )
                    await self._emit_runtime_error("runtime_crashed")
                    return _RuntimeOutcome(_RUNTIME_FAILURE_EXIT_CODE)
                if isinstance(result, _RuntimeOutcome):
                    return result
            if self._shutdown_event.is_set():
                return _RuntimeOutcome(_SUCCESS_EXIT_CODE)
        return _RuntimeOutcome(_SUCCESS_EXIT_CODE)

    async def _bridge_reader_loop(self, websocket: Any) -> _RuntimeOutcome:
        try:
            while not self._startup_pending_messages.empty():
                raw_message = await self._startup_pending_messages.get()
                outcome = await self._handle_bridge_message(raw_message)
                if outcome is not None:
                    return outcome
            async for raw_message in websocket:
                outcome = await self._handle_bridge_message(raw_message)
                if outcome is not None:
                    return outcome
        except ConnectionClosed:
            if self._shutdown_event.is_set():
                return _RuntimeOutcome(_SUCCESS_EXIT_CODE)
            await self._emit_runtime_error("runtime_disconnected")
            return _RuntimeOutcome(_RUNTIME_FAILURE_EXIT_CODE)
        if self._shutdown_event.is_set():
            return _RuntimeOutcome(_SUCCESS_EXIT_CODE)
        await self._emit_runtime_error("runtime_disconnected")
        return _RuntimeOutcome(_RUNTIME_FAILURE_EXIT_CODE)

    async def _handle_bridge_message(self, raw_message: object) -> _RuntimeOutcome | None:
        try:
            message = _load_bridge_message(raw_message)
        except ValueError:
            logger.warning("[DesktopOverlay] Ignoring malformed bridge message")
            return None

        message_type = message.get("type")
        if message_type == "heartbeat":
            return None
        if message_type == "shutdown":
            return _RuntimeOutcome(_SUCCESS_EXIT_CODE)
        if message_type == "snapshot":
            try:
                snapshot = _parse_snapshot_message(message)
            except Exception:
                logger.warning("[DesktopOverlay] Ignoring malformed snapshot update")
                return None
            await self._ui_queue.put(("snapshot", snapshot))
            return None
        if message_type == "runtime_control":
            payload = _parse_runtime_control_payload(message)
            if payload is None:
                await self._emit_runtime_error("runtime_control_invalid")
                return _RuntimeOutcome(_RUNTIME_FAILURE_EXIT_CODE)
            await self._ui_queue.put(("runtime_control", payload))
            return None
        logger.warning(
            "[DesktopOverlay] Ignoring unsupported bridge message type: %r", message_type
        )
        return None

    async def _ui_update_loop(self) -> _RuntimeOutcome | None:
        while not self._shutdown_event.is_set():
            queue_task = asyncio.create_task(self._ui_queue.get())
            stop_task = asyncio.create_task(self._shutdown_event.wait())
            try:
                done, _pending = await asyncio.wait(
                    {queue_task, stop_task},
                    return_when=asyncio.FIRST_COMPLETED,
                )
                if stop_task in done:
                    return None
                kind, payload = queue_task.result()
            finally:
                for task in (queue_task, stop_task):
                    if not task.done():
                        task.cancel()
                await asyncio.gather(queue_task, stop_task, return_exceptions=True)

            try:
                if kind == "snapshot" and isinstance(payload, OverlayPresentationSnapshot):
                    await self.window.dispatch_snapshot(payload)
                elif kind == "runtime_control" and isinstance(payload, dict):
                    await self.window.dispatch_runtime_control(payload)
            except Exception:
                # Hide the window before reporting failure so a half-rendered
                # overlay can't sit on screen until the user notices and toggles
                # it off.
                with contextlib.suppress(Exception):
                    self.window.hide_window_safely()
                await self._emit_runtime_error("window_configuration_failed")
                return _RuntimeOutcome(_RUNTIME_FAILURE_EXIT_CODE)
        return None

    async def _parent_monitor_loop(self) -> _RuntimeOutcome | None:
        try:
            await self.parent_monitor.wait_for_parent_exit(self._shutdown_event)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning(
                "[DesktopOverlay] Parent monitor failed: exception_type=%s",
                type(exc).__name__,
            )
            return None
        if self._shutdown_event.is_set():
            return None
        await self._emit_runtime_error("runtime_disconnected")
        return _RuntimeOutcome(_RUNTIME_FAILURE_EXIT_CODE)

    async def _window_loop(self) -> _RuntimeOutcome | None:
        try:
            await self.window.run_until_closed()
        except asyncio.CancelledError:
            raise
        except Exception:
            await self._emit_runtime_error("window_configuration_failed")
            return _RuntimeOutcome(_RUNTIME_FAILURE_EXIT_CODE)
        if self._shutdown_event.is_set():
            return None
        return _RuntimeOutcome(_SUCCESS_EXIT_CODE)

    async def _heartbeat_loop(self) -> None:
        while not self._shutdown_event.is_set():
            try:
                await asyncio.wait_for(self._shutdown_event.wait(), timeout=1.0)
            except TimeoutError:
                continue

    async def _emit_runtime_error(self, failure_reason: str) -> None:
        await self._emit_lifecycle({"type": "runtime_error", "failure_reason": failure_reason})

    async def _emit_lifecycle(self, event: dict[str, object]) -> None:
        safe_event = _redact_event(event)
        websocket = self._websocket
        if websocket is not None:
            with contextlib.suppress(Exception):
                await websocket.send(json.dumps(safe_event))
        await self.lifecycle_sink.emit(safe_event)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="puripuly-heart desktop-overlay")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument(
        "--config",
        type=Path,
        help="Path to overlay launch manifest JSON",
    )
    mode.add_argument(
        "--preview",
        action="store_true",
        help="Run a local desktop overlay preview",
    )
    return parser


def run_renderer(config_path: Path) -> int:
    return asyncio.run(_run_renderer_async(config_path))


async def _run_renderer_async(config_path: Path) -> int:
    sink = StdoutLifecycleSink()
    try:
        manifest = load_renderer_manifest(config_path)
    except DesktopOverlayStartupError as exc:
        await sink.emit({"type": "startup_error", "failure_reason": exc.failure_reason})
        return _STARTUP_FAILURE_EXIT_CODE
    renderer = DesktopOverlayRenderer(manifest, lifecycle_sink=sink)
    return await renderer.run()


def run_preview(
    *,
    app_runner: PreviewAppRunner | None = None,
    locale: str | None = None,
) -> int:
    catalog = build_desktop_overlay_preview_catalog(locale=locale)
    secret_findings = preview_fixture_secret_findings(catalog)
    if secret_findings:
        for finding in secret_findings:
            logger.error("Unsafe desktop overlay preview fixture data: %s", finding)
        return _STARTUP_FAILURE_EXIT_CODE
    runner = app_runner or _default_preview_app_runner
    return asyncio.run(
        _run_preview_async(
            catalog=catalog,
            app_runner=runner,
            locale=locale,
            allow_no_page=app_runner is not None or runner is not _REAL_DEFAULT_PREVIEW_APP_RUNNER,
        )
    )


async def _run_preview_async(
    *,
    catalog: DesktopOverlayPreviewCatalog,
    app_runner: PreviewAppRunner,
    locale: str | None,
    allow_no_page: bool,
) -> int:
    async def preview_app_runner(target: Callable[[Any], object]) -> None:
        result = app_runner(target)
        if inspect.isawaitable(result):
            await result

    async def preview_event_sink(event: dict[str, object]) -> None:
        logger.debug("Desktop overlay preview event: %r", _redact_event(event))

    window = FletDesktopRendererWindow(
        app_runner=preview_app_runner,
        event_sink=preview_event_sink,
        locale=locale,
        preview_catalog=catalog,
    )
    try:
        try:
            await window.start(catalog.fixtures[0].snapshot)
        except RuntimeError:
            if allow_no_page and window._page is None:
                return _SUCCESS_EXIT_CODE
            raise
        await window.run_until_closed()
    finally:
        await window.close()
    return _SUCCESS_EXIT_CODE


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.preview:
        return run_preview()
    return run_renderer(args.config)


def _validate_runtime_manifest(manifest: OverlayLaunchManifest) -> None:
    if manifest.contract_version != OVERLAY_CONTRACT_VERSION:
        raise DesktopOverlayStartupError(
            "contract_mismatch",
            "desktop overlay contract version is not supported",
        )
    try:
        validate_desktop_bridge_url(manifest.bridge_url)
    except ValueError as exc:
        raise DesktopOverlayStartupError(
            "manifest_invalid",
            "desktop overlay launch manifest is invalid",
        ) from exc
    if not manifest.session_token:
        raise DesktopOverlayStartupError(
            "manifest_invalid",
            "desktop overlay launch manifest is invalid",
        )
    if manifest.parent_pid <= 0 or manifest.startup_deadline_ms <= 0:
        raise DesktopOverlayStartupError(
            "manifest_invalid",
            "desktop overlay launch manifest is invalid",
        )
    if not manifest.log_dir or not manifest.log_level or not manifest.locale:
        raise DesktopOverlayStartupError(
            "manifest_invalid",
            "desktop overlay launch manifest is invalid",
        )


def _validate_manifest_payload_shape(payload: dict[object, object]) -> None:
    for field_name in _REQUIRED_MANIFEST_STRING_FIELDS:
        value = payload.get(field_name)
        if not isinstance(value, str) or not value:
            raise DesktopOverlayStartupError(
                "manifest_invalid",
                "desktop overlay launch manifest is invalid",
            )
    for field_name in _REQUIRED_MANIFEST_INT_FIELDS:
        value = payload.get(field_name)
        if not isinstance(value, int) or isinstance(value, bool):
            raise DesktopOverlayStartupError(
                "manifest_invalid",
                "desktop overlay launch manifest is invalid",
            )


def _load_bridge_message(raw_message: object) -> dict[str, object]:
    if not isinstance(raw_message, str):
        raise ValueError("desktop overlay bridge message must be text JSON")
    payload = json.loads(raw_message)
    if not isinstance(payload, dict):
        raise ValueError("desktop overlay bridge message must decode to an object")
    return payload


def _parse_snapshot_message(message: dict[str, object]) -> OverlayPresentationSnapshot:
    payload = message.get("payload")
    if not isinstance(payload, dict):
        raise ValueError("desktop overlay snapshot payload must be an object")
    return OverlayPresentationSnapshot.from_dict(payload)


def _parse_runtime_control_payload(message: dict[str, object]) -> dict[str, object] | None:
    payload = message.get("payload")
    if not isinstance(payload, dict):
        return None
    if "logging_mode" in payload:
        if set(payload) != {"logging_mode"} or not isinstance(payload.get("logging_mode"), str):
            return None
        return dict(payload)
    command = payload.get("command")
    if not isinstance(command, str) or not command:
        return None
    return dict(payload)


def _redact_renderer_startup_exception_text(
    text: str,
    manifest: OverlayLaunchManifest,
) -> str:
    redacted = text
    if manifest.session_token:
        redacted = redacted.replace(manifest.session_token, "<redacted>")
    for _, pattern in _DESKTOP_PREVIEW_SECRET_PATTERNS:
        redacted = pattern.sub("<redacted>", redacted)
    return redacted


def _redact_event(event: dict[str, object]) -> dict[str, object]:
    redacted = _redact_value(event)
    if isinstance(redacted, dict):
        return redacted
    return {"type": "runtime_error", "failure_reason": "unknown"}


def _redact_value(value: object) -> object:
    if isinstance(value, dict):
        result: dict[str, object] = {}
        for key, item in value.items():
            key_text = str(key)
            if _is_sensitive_event_key(key_text):
                result[key_text] = "<redacted>"
            else:
                result[key_text] = _redact_value(item)
        return result
    if isinstance(value, list):
        return [_redact_value(item) for item in value]
    return value


def _is_sensitive_event_key(key: str) -> bool:
    normalized = re.sub(r"[^a-z0-9]", "", key.lower())
    return normalized in _SENSITIVE_EVENT_KEYS


async def _close_parent_monitor(parent_monitor: ParentMonitor) -> None:
    close = getattr(parent_monitor, "close", None)
    if not callable(close):
        return
    result = close()
    if asyncio.iscoroutine(result):
        await result


if __name__ == "__main__":
    raise SystemExit(main())
