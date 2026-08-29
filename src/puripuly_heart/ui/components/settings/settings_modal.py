"""Settings selection modal — compact dark VRCT style."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Sequence

import flet as ft

from puripuly_heart.ui.i18n import t
from puripuly_heart.ui.theme import (
    COLOR_DIVIDER,
    COLOR_NEUTRAL,
    COLOR_ON_BACKGROUND,
    COLOR_PRIMARY,
    COLOR_SURFACE_TONAL,
)

_BG_MODAL = "#292a2d"
_BG_ITEM_SELECTED = "#1a3a36"
# r624: same search-field palette as the language selector
_TEXT_FAINT = "#7f8084"
_BORDER_INPUT = "#4b4c4f"
_FOCUSED_BORDER = "#48a495"
# short lists don't need a search bar — it would just be clutter
_SEARCH_MIN_OPTIONS = 8


@dataclass
class OptionItem:
    """Option item for settings modal."""

    value: str
    label: str
    description: str = ""
    disabled: bool = False
    # r609: optional leading image (e.g. an app's real exe icon); rows
    # without one render exactly as before
    icon_src: str | None = None
    # r610: optional Flet icon-name glyph when no image applies (devices,
    # the Auto row) so mixed lists stay aligned with no blank slots
    icon_name: str | None = None


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
        self._option_rows: list[tuple[str, ft.Container]] = []
        self._list_view: ft.ListView | None = None

    def open(self, current: str) -> None:
        self._option_rows = self._build_option_rows(current)
        self._list_view = ft.ListView(
            controls=[row for _, row in self._option_rows],
            expand=True,
            spacing=2,
            padding=ft.padding.only(right=4, bottom=8),
        )

        content_controls: list[ft.Control] = [
            ft.Text(
                self._title,
                size=13,
                weight=ft.FontWeight.W_600,
                color=COLOR_NEUTRAL,
            ),
        ]
        if len(self._option_rows) >= _SEARCH_MIN_OPTIONS:
            content_controls.append(ft.TextField(
                hint_text=t("settings.search_hint", default="Search..."),
                border=ft.InputBorder.OUTLINE,
                border_color=_BORDER_INPUT,
                focused_border_color=_FOCUSED_BORDER,
                bgcolor="#2a2b2e",
                color="#f2f2f2",
                hint_style=ft.TextStyle(color=_TEXT_FAINT, italic=True),
                text_size=13,
                height=40,
                content_padding=ft.padding.symmetric(horizontal=12, vertical=6),
                prefix_icon=ft.Icons.SEARCH,
                on_change=self._on_search_change,
                autofocus=True,
            ))
        content_controls.append(ft.Divider(height=1, color=COLOR_DIVIDER))
        content_controls.append(self._list_view)

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

    def _on_search_change(self, e) -> None:
        query = (e.control.value or "").lower().strip()
        if self._list_view is None:
            return
        if query:
            self._list_view.controls = [
                row for text, row in self._option_rows if query in text]
        else:
            self._list_view.controls = [row for _, row in self._option_rows]
        try:
            self._list_view.update()
        except Exception:
            pass

    def _build_option_rows(
        self, current: str
    ) -> list[tuple[str, ft.Container]]:
        """(search_text, row) pairs — search matches label + description."""
        items: list[tuple[str, ft.Container]] = []
        for option in self._options:
            # The currently-active option is highlighted even if it's temporarily
            # disabled (e.g. its API key needs re-verifying), so the user can always
            # see what's selected.
            is_selected = option.value == current

            _icon_ctrl = None
            if getattr(option, "icon_src", None):
                _icon_ctrl = ft.Container(
                    content=ft.Image(src=option.icon_src, width=18,
                                     height=18, fit=ft.ImageFit.CONTAIN),
                    width=20, height=20, alignment=ft.alignment.center)
            elif getattr(option, "icon_name", None):
                _icon_ctrl = ft.Container(
                    content=ft.Icon(option.icon_name, size=16,
                                    color=COLOR_NEUTRAL),
                    width=20, height=20, alignment=ft.alignment.center)
            if self._show_description and option.description:
                content = ft.Column(
                    controls=[
                        ft.Text(
                            option.label,
                            size=14,
                            color=COLOR_PRIMARY if is_selected else (
                                COLOR_NEUTRAL if option.disabled else COLOR_ON_BACKGROUND
                            ),
                            weight=ft.FontWeight.W_600 if is_selected else ft.FontWeight.NORMAL,
                        ),
                        ft.Text(
                            option.description,
                            size=12,
                            color=ft.Colors.with_opacity(0.5, COLOR_NEUTRAL) if option.disabled else COLOR_NEUTRAL,
                        ),
                    ],
                    spacing=2,
                )
            else:
                content = ft.Row(
                    [
                        *([_icon_ctrl] if _icon_ctrl is not None else []),
                        ft.Text(
                            option.label,
                            size=14,
                            color=COLOR_PRIMARY if is_selected else (
                                COLOR_NEUTRAL if option.disabled else COLOR_ON_BACKGROUND
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
            items.append(
                ((option.label + " " + option.description).lower(), item))

        return items

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
