"""Compact "what's new" dialog shown once after a self-update (r329).

Deliberately NOT the warm document dialog: that one is 600px wide with large
body text, a redundant top-right X plus a bottom text button, and is sized
for long advisories — as an update note it filled most of the window ("way
too huge"). This is a narrow card: small muted header, the changelog bullets
at reading size, one Close button. Nothing else.
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
# r331: height must follow the WRAPPED text. The r329 estimate assumed one
# line per bullet (44 + 34*n) and clipped a long entry mid-sentence. Derive
# it from the usable text width instead: ~6.2px average glyph at 12px, minus
# padding (18*2), the bullet glyph and its gap.
_USABLE_TEXT_WIDTH = DIALOG_WIDTH - (18 * 2) - 22
_AVG_CHAR_WIDTH = 6.2
_LINE_HEIGHT = 17
_BULLET_GAP = 10


def _estimated_body_height(bullets: "Sequence[str]") -> int:
    chars_per_line = max(1, int(_USABLE_TEXT_WIDTH / _AVG_CHAR_WIDTH))
    total = 0
    for bullet in bullets:
        # CJK glyphs are ~2x wider than the latin average — count them double
        # so a Chinese changelog entry isn't underestimated.
        weighted = sum(2 if ord(ch) > 0x2E7F else 1 for ch in bullet)
        lines = max(1, -(-weighted // chars_per_line))  # ceil
        total += lines * _LINE_HEIGHT
    total += _BULLET_GAP * max(0, len(bullets) - 1)
    return min(MAX_BODY_HEIGHT, total + 8)


def open_update_notes_dialog(
    page: ft.Page,
    *,
    header: str,
    bullets: Sequence[str],
    close_label: str,
    hide_future_label: str = "",
    on_hide_future_changed: Callable[[bool], None] | None = None,
) -> ft.AlertDialog:
    """r331: the opt-out lives IN the dialog — the moment a user decides they
    don't want this popup is the moment it is on screen, not later in
    Settings (the Settings card exists too, and both write the same flag)."""
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

    body = ft.Column(
        controls=bullet_rows,
        spacing=10,
        tight=True,
        scroll=ft.ScrollMode.AUTO,
    )

    card = ft.Container(
        width=DIALOG_WIDTH,
        padding=ft.padding.symmetric(horizontal=18, vertical=14),
        bgcolor=COLOR_SURFACE,
        border_radius=10,
        border=ft.border.all(1, ft.Colors.with_opacity(0.30, COLOR_DIVIDER)),
        content=ft.Column(
            controls=[
                ft.Text(
                    header,
                    size=HEADER_TEXT_SIZE,
                    color=COLOR_NEUTRAL,
                    weight=ft.FontWeight.W_600,
                ),
                ft.Divider(height=12, thickness=1, color=ft.Colors.with_opacity(0.25, COLOR_DIVIDER)),
                ft.Container(content=body, height=_estimated_body_height(bullets)),
                ft.Row(
                    controls=[
                        *(
                            [
                                ft.Checkbox(
                                    label=hide_future_label,
                                    value=False,
                                    on_change=lambda e: (
                                        on_hide_future_changed(bool(e.control.value))
                                        if on_hide_future_changed is not None
                                        else None
                                    ),
                                    label_style=ft.TextStyle(
                                        size=11, color=COLOR_NEUTRAL
                                    ),
                                    check_color=COLOR_SURFACE,
                                    active_color=COLOR_PRIMARY,
                                    splash_radius=0,
                                    expand=True,
                                )
                            ]
                            if hide_future_label and on_hide_future_changed is not None
                            else [ft.Container(expand=True)]
                        ),
                        ft.TextButton(
                            close_label,
                            on_click=_close,
                            style=ft.ButtonStyle(
                                color={
                                    ft.ControlState.DEFAULT: COLOR_NEUTRAL,
                                    ft.ControlState.HOVERED: COLOR_PRIMARY,
                                },
                                overlay_color=ft.Colors.TRANSPARENT,
                                padding=ft.padding.symmetric(horizontal=10, vertical=6),
                                text_style=ft.TextStyle(
                                    size=12, weight=ft.FontWeight.W_600
                                ),
                            ),
                        )
                    ],
                    alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                    spacing=0,
                ),
            ],
            spacing=6,
            tight=True,
            horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
        ),
    )

    dialog = ft.AlertDialog(
        # r331 (user request): clicking outside dismisses it — this is an
        # informational note, not something to trap the user in.
        modal=False,
        content=card,
        content_padding=0,
        bgcolor=ft.Colors.TRANSPARENT,
        surface_tint_color=ft.Colors.TRANSPARENT,
    )
    page.open(dialog)
    return dialog


__all__ = ["DIALOG_WIDTH", "open_update_notes_dialog"]
