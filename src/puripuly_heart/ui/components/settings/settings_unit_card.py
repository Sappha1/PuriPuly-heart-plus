from __future__ import annotations

from collections.abc import Sequence

import flet as ft

from puripuly_heart.ui.theme import COLOR_DIVIDER, COLOR_NEUTRAL, COLOR_ON_BACKGROUND


class SettingsUnitCard(ft.Container):
    """Compact horizontal settings row: label on left, value/action on right."""

    DEFAULT_HEIGHT = 52

    def __init__(
        self,
        *,
        title: ft.Control,
        value: ft.Control,
        extra_controls: Sequence[ft.Control] = (),
        height: float | int | None = DEFAULT_HEIGHT,
    ) -> None:
        main_row = ft.Row(
            [
                ft.Container(content=title, expand=True),
                ft.Container(
                    content=value,
                    width=180,
                    alignment=ft.alignment.center_right,
                    clip_behavior=ft.ClipBehavior.HARD_EDGE,
                ),
            ],
            spacing=12,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        )

        if extra_controls:
            inner = ft.Column(
                [main_row, *extra_controls],
                spacing=4,
                tight=True,
            )
            resolved_height = None
        else:
            inner = main_row
            resolved_height = height

        super().__init__(
            content=inner,
            padding=ft.padding.symmetric(horizontal=16, vertical=0),
            height=resolved_height,
            expand=True,
            border=ft.border.only(bottom=ft.BorderSide(1, COLOR_DIVIDER)),
            bgcolor=ft.Colors.TRANSPARENT,
        )
