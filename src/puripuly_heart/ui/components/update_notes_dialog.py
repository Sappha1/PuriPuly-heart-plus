"""Compact "what's new" dialog shown once after a self-update (r329).

Deliberately NOT the warm document dialog: that one is 600px wide with large
body text, a redundant top-right X plus a bottom text button, and is sized
for long advisories — as an update note it filled most of the window ("way
too huge"). This is a narrow card: small muted header, the changelog bullets
at reading size, one Close button. Nothing else.
"""
from __future__ import annotations

from collections.abc import Sequence

import flet as ft

from puripuly_heart.ui.theme import (
    COLOR_DIVIDER,
    COLOR_NEUTRAL,
    COLOR_ON_BACKGROUND,
    COLOR_PRIMARY,
    COLOR_SURFACE,
)

DIALOG_WIDTH = 400
MAX_BODY_HEIGHT = 300
BULLET_TEXT_SIZE = 12
HEADER_TEXT_SIZE = 12


def open_update_notes_dialog(
    page: ft.Page,
    *,
    header: str,
    bullets: Sequence[str],
    close_label: str,
) -> ft.AlertDialog:
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
                ft.Container(content=body, height=min(MAX_BODY_HEIGHT, 44 + 34 * len(bullet_rows))),
                ft.Row(
                    controls=[
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
                    alignment=ft.MainAxisAlignment.END,
                    spacing=0,
                ),
            ],
            spacing=6,
            tight=True,
            horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
        ),
    )

    dialog = ft.AlertDialog(
        modal=True,
        content=card,
        content_padding=0,
        bgcolor=ft.Colors.TRANSPARENT,
        surface_tint_color=ft.Colors.TRANSPARENT,
    )
    page.open(dialog)
    return dialog


__all__ = ["DIALOG_WIDTH", "open_update_notes_dialog"]
