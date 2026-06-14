"""Settings selection modal — compact dark VRCT style."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Sequence

import flet as ft

from puripuly_heart.ui.theme import (
    COLOR_DIVIDER,
    COLOR_NEUTRAL,
    COLOR_ON_BACKGROUND,
    COLOR_PRIMARY,
    COLOR_SURFACE_TONAL,
)

_BG_MODAL = "#292a2d"
_BG_ITEM_SELECTED = "#1a3a36"


@dataclass
class OptionItem:
    """Option item for settings modal."""

    value: str
    label: str
    description: str = ""
    disabled: bool = False


class SettingsModal:
    """Modal dialog for settings selection — compact dark list."""

    def __init__(
        self,
        page: ft.Page,
        title: str,
        options: Sequence[OptionItem],
        on_select: Callable[[str], None],
        *,
        show_description: bool = False,
    ):
        self._page = page
        self._title = title
        self._options = options
        self._on_select = on_select
        self._show_description = show_description
        self._dialog: ft.AlertDialog | None = None

    def open(self, current: str) -> None:
        option_list = self._build_option_list(current)

        content_controls: list[ft.Control] = [
            ft.Text(
                self._title,
                size=13,
                weight=ft.FontWeight.W_600,
                color=COLOR_NEUTRAL,
            ),
            ft.Divider(height=1, color=COLOR_DIVIDER),
            option_list,
        ]

        modal_content = ft.Container(
            content=ft.Column(
                content_controls,
                spacing=8,
                horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
            ),
            width=340,
            height=480,
            padding=ft.padding.symmetric(horizontal=16, vertical=16),
            bgcolor=_BG_MODAL,
            border_radius=12,
            border=ft.border.all(1, ft.Colors.with_opacity(0.1, ft.Colors.WHITE)),
        )

        self._dialog = ft.AlertDialog(
            modal=False,
            content=modal_content,
            content_padding=0,
            bgcolor=ft.Colors.TRANSPARENT,
            surface_tint_color=ft.Colors.TRANSPARENT,
        )
        self._page.open(self._dialog)

    def _build_option_list(self, current: str) -> ft.ListView:
        items = []
        for option in self._options:
            is_selected = option.value == current and not option.disabled

            if self._show_description and option.description:
                content = ft.Column(
                    controls=[
                        ft.Text(
                            option.label,
                            size=14,
                            color=COLOR_PRIMARY if is_selected else COLOR_ON_BACKGROUND,
                            weight=ft.FontWeight.W_600 if is_selected else ft.FontWeight.NORMAL,
                        ),
                        ft.Text(
                            option.description,
                            size=12,
                            color=COLOR_NEUTRAL,
                        ),
                    ],
                    spacing=2,
                )
            else:
                content = ft.Row(
                    [
                        ft.Text(
                            option.label,
                            size=14,
                            color=COLOR_NEUTRAL if option.disabled else (
                                COLOR_PRIMARY if is_selected else COLOR_ON_BACKGROUND
                            ),
                            weight=ft.FontWeight.W_600 if is_selected else ft.FontWeight.NORMAL,
                            expand=True,
                        ),
                        ft.Icon(
                            ft.Icons.CHECK,
                            size=14,
                            color=COLOR_PRIMARY,
                            visible=is_selected,
                        ),
                    ],
                    spacing=8,
                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                )

            item = ft.Container(
                content=content,
                bgcolor=_BG_ITEM_SELECTED if is_selected else ft.Colors.TRANSPARENT,
                border_radius=6,
                padding=ft.padding.symmetric(horizontal=12, vertical=9),
                on_click=None if option.disabled else lambda e, val=option.value: self._select(val),
                on_hover=None if option.disabled else (
                    lambda e, is_sel=is_selected: self._on_item_hover(e, is_sel)
                ),
            )
            items.append(item)

        return ft.ListView(
            controls=items,
            expand=True,
            spacing=2,
            padding=ft.padding.only(right=4, bottom=8),
        )

    def _on_item_hover(self, e: ft.ControlEvent, is_selected: bool) -> None:
        if is_selected:
            return
        container = e.control
        container.bgcolor = COLOR_SURFACE_TONAL if e.data == "true" else ft.Colors.TRANSPARENT
        container.update()

    def _select(self, value: str) -> None:
        if self._dialog:
            self._page.close(self._dialog)
        self._on_select(value)
