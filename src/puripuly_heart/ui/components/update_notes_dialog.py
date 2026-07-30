"""Compact "what's new" dialog shown once after a self-update (r329+).

Deliberately NOT the warm document dialog: that one is 600px wide with large
body text and is sized for long advisories — as an update note it filled most
of the window. This is a narrow card: small muted header with the release
date, the changelog bullets, one Close button and the opt-out.

r333: the card now HUGS its content. r331 always applied an estimated height,
which overshot on short entries and left dead space under the last bullet;
the estimate is now only used to decide whether a scroll cap is needed at all.
"""
from __future__ import annotations

from collections.abc import Callable, Sequence

import flet as ft

from puripuly_heart.ui.theme import (
    COLOR_DIVIDER,
    COLOR_NEUTRAL,
    COLOR_ON_BACKGROUND,
    COLOR_PRIMARY,
    COLOR_SURFACE,
)

DIALOG_WIDTH = 400
MAX_BODY_HEIGHT = 320
BULLET_TEXT_SIZE = 12
HEADER_TEXT_SIZE = 12
# Height estimation follows the WRAPPED text (r331): ~6.2px average glyph at
# 12px, minus padding and the bullet gutter. Used only to detect overflow.
_USABLE_TEXT_WIDTH = DIALOG_WIDTH - (16 * 2) - 22
_AVG_CHAR_WIDTH = 6.2
_LINE_HEIGHT = 17
_BULLET_GAP = 8


def _estimated_body_height(bullets: Sequence[str]) -> int:
    chars_per_line = max(1, int(_USABLE_TEXT_WIDTH / _AVG_CHAR_WIDTH))
    total = 0
    for bullet in bullets:
        # CJK glyphs are ~2x the latin average — count them double so a
        # Chinese changelog entry isn't underestimated.
        weighted = sum(2 if ord(ch) > 0x2E7F else 1 for ch in bullet)
        lines = max(1, -(-weighted // chars_per_line))  # ceil
        total += lines * _LINE_HEIGHT
    total += _BULLET_GAP * max(0, len(bullets) - 1)
    return total + 4


def open_update_notes_dialog(
    page: ft.Page,
    *,
    header: str,
    bullets: Sequence[str],
    close_label: str,
    release_date: str = "",
    hide_future_label: str = "",
    on_hide_future_changed: Callable[[bool], None] | None = None,
) -> ft.AlertDialog:
    """The opt-out lives IN the dialog (r331) — the moment a user decides they
    don't want this popup is the moment it is on screen."""
    dialog: ft.AlertDialog | None = None

    def _close(_=None) -> None:
        if dialog is not None:
            page.close(dialog)

    bullet_rows: list[ft.Control] = []
    for bullet in bullets:
        bullet_rows.append(
            ft.Row(
                controls=[
                    ft.Text(
                        "•",
                        size=BULLET_TEXT_SIZE,
                        color=COLOR_PRIMARY,
                        weight=ft.FontWeight.BOLD,
                    ),
                    ft.Text(
                        bullet,
                        size=BULLET_TEXT_SIZE,
                        color=COLOR_ON_BACKGROUND,
                        selectable=True,
                        expand=True,
                    ),
                ],
                spacing=8,
                vertical_alignment=ft.CrossAxisAlignment.START,
            )
        )

    overflows = _estimated_body_height(bullets) > MAX_BODY_HEIGHT
    body = ft.Column(
        controls=bullet_rows,
        spacing=_BULLET_GAP,
        tight=True,
        scroll=ft.ScrollMode.AUTO if overflows else None,
    )
    # Only clamp when the content would exceed the cap; otherwise let the card
    # hug the text so there is no dead space under the last bullet.
    body_container = (
        ft.Container(content=body, height=MAX_BODY_HEIGHT)
        if overflows
        else ft.Container(content=body)
    )

    header_row = ft.Row(
        controls=[
            ft.Text(
                header,
                size=HEADER_TEXT_SIZE,
                color=COLOR_NEUTRAL,
                weight=ft.FontWeight.W_600,
                expand=True,
            ),
            *(
                [
                    ft.Text(
                        release_date,
                        size=11,
                        color=COLOR_NEUTRAL,
                        opacity=0.75,
                    )
                ]
                if release_date
                else []
            ),
        ],
        spacing=8,
        vertical_alignment=ft.CrossAxisAlignment.CENTER,
    )

    footer_controls: list[ft.Control] = []
    if hide_future_label and on_hide_future_changed is not None:
        footer_controls.append(
            ft.Checkbox(
                label=hide_future_label,
                value=False,
                on_change=lambda e: on_hide_future_changed(bool(e.control.value)),
                label_style=ft.TextStyle(size=11, color=COLOR_NEUTRAL),
                check_color=COLOR_SURFACE,
                active_color=COLOR_PRIMARY,
                splash_radius=0,
                expand=True,
            )
        )
    else:
        footer_controls.append(ft.Container(expand=True))
    footer_controls.append(
        ft.TextButton(
            close_label,
            on_click=_close,
            style=ft.ButtonStyle(
                color={
                    ft.ControlState.DEFAULT: COLOR_NEUTRAL,
                    ft.ControlState.HOVERED: COLOR_PRIMARY,
                },
                overlay_color=ft.Colors.TRANSPARENT,
                padding=ft.padding.symmetric(horizontal=8, vertical=4),
                text_style=ft.TextStyle(size=12, weight=ft.FontWeight.W_600),
            ),
        )
    )

    card = ft.Container(
        width=DIALOG_WIDTH,
        padding=ft.padding.symmetric(horizontal=16, vertical=12),
        bgcolor=COLOR_SURFACE,
        border_radius=10,
        border=ft.border.all(1, ft.Colors.with_opacity(0.30, COLOR_DIVIDER)),
        content=ft.Column(
            controls=[
                header_row,
                ft.Divider(
                    height=10,
                    thickness=1,
                    color=ft.Colors.with_opacity(0.25, COLOR_DIVIDER),
                ),
                body_container,
                ft.Row(
                    controls=footer_controls,
                    alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                    spacing=0,
                ),
            ],
            spacing=4,
            tight=True,
            horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
        ),
    )

    dialog = ft.AlertDialog(
        # Clicking outside dismisses it — informational, not a trap (r331).
        modal=False,
        content=card,
        content_padding=0,
        bgcolor=ft.Colors.TRANSPARENT,
        surface_tint_color=ft.Colors.TRANSPARENT,
    )
    page.open(dialog)
    return dialog


__all__ = ["DIALOG_WIDTH", "MAX_BODY_HEIGHT", "open_update_notes_dialog"]
