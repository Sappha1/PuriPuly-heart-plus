"""Language selection modal component — compact dark VRCT style."""

import logging
from dataclasses import dataclass
from typing import Callable, Sequence

import flet as ft

from puripuly_heart.ui.i18n import language_name, t
from puripuly_heart.ui.theme import (
    COLOR_DIVIDER,
    COLOR_NEUTRAL,
    COLOR_ON_BACKGROUND,
    COLOR_PRIMARY,
    COLOR_SURFACE,
    COLOR_SURFACE_TONAL,
)

logger = logging.getLogger(__name__)

_BG_MODAL = "#292a2d"
_BG_ITEM = "#3a3b3e"
_BG_ITEM_SELECTED = "#1a3a36"
_TOGGLE_ON = "#1a3a36"
_TOGGLE_OFF = "#3a3b3e"
_TEXT_FAINT = "#7f8084"
_BORDER_INPUT = "#4b4c4f"
_FOCUSED_BORDER = "#48a495"


@dataclass
class LanguageModalToggle:
    """A quick-toggle checkbox shown at the top of the language modal."""
    label: str
    value: bool
    on_change: Callable[[bool], None]


class LanguageModal:
    """Modal dialog for language selection with compact dark list style."""

    def __init__(
        self,
        page: ft.Page,
        languages: Sequence[tuple[str, str]],
        on_select: Callable[[str], None],
        *,
        toggles: list[LanguageModalToggle] | None = None,
    ):
        self._page = page
        self._languages = languages
        self._on_select = on_select
        self._toggles = toggles or []
        self._dialog: ft.AlertDialog | None = None
        self._all_lang_items: list[tuple[str, ft.Container]] = []
        self._lang_list_view: ft.ListView | None = None
        self._recent_row: ft.Control | None = None
        self._recent_header: ft.Text | None = None
        self._recent_divider: ft.Divider | None = None
        self._all_header: ft.Text | None = None

    def open(self, current: str, recent: list[str]) -> None:
        # Build all language items once (name_lower, widget)
        self._all_lang_items = self._build_all_lang_items(current)
        self._lang_list_view = ft.ListView(
            controls=[item for _, item in self._all_lang_items],
            expand=True,
            spacing=2,
            padding=ft.padding.only(right=4, bottom=8),
        )

        search_field = ft.TextField(
            hint_text=t("language_modal.search_hint"),
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
        )

        content_controls: list[ft.Control] = [search_field]

        # Quick toggles
        if self._toggles:
            content_controls.append(self._build_toggles())
            content_controls.append(ft.Divider(height=1, color=COLOR_DIVIDER))

        # Recent section
        self._recent_header = None
        self._recent_row = None
        self._recent_divider = None
        if recent:
            self._recent_header = ft.Text(
                t("language_modal.recent"),
                size=11,
                weight=ft.FontWeight.W_600,
                color=COLOR_NEUTRAL,
            )
            self._recent_row = self._build_recent_chips(recent, current)
            self._recent_divider = ft.Divider(height=1, color=COLOR_DIVIDER)
            content_controls.append(self._recent_header)
            content_controls.append(self._recent_row)
            content_controls.append(self._recent_divider)

        self._all_header = ft.Text(
            t("language_modal.all_languages"),
            size=11,
            weight=ft.FontWeight.W_600,
            color=COLOR_NEUTRAL,
        )
        content_controls.append(self._all_header)
        content_controls.append(self._lang_list_view)

        modal_content = ft.Container(
            content=ft.Column(
                content_controls,
                spacing=8,
                horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
            ),
            width=360,
            height=520,
            padding=ft.padding.symmetric(horizontal=20, vertical=16),
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
        if query:
            filtered = [item for name, item in self._all_lang_items if query in name]
            # Hide recent section while searching
            for ctrl in (self._recent_header, self._recent_row, self._recent_divider):
                if ctrl is not None:
                    ctrl.visible = False
                    try:
                        ctrl.update()
                    except Exception:
                        pass
        else:
            filtered = [item for _, item in self._all_lang_items]
            for ctrl in (self._recent_header, self._recent_row, self._recent_divider):
                if ctrl is not None:
                    ctrl.visible = True
                    try:
                        ctrl.update()
                    except Exception:
                        pass
        if self._lang_list_view is not None:
            self._lang_list_view.controls = filtered
            try:
                self._lang_list_view.update()
            except Exception:
                pass

    def _build_chip(self, toggle: "LanguageModalToggle") -> ft.Container:
        chip_text = ft.Text(
            toggle.label,
            size=12,
            color=COLOR_PRIMARY if toggle.value else COLOR_ON_BACKGROUND,
            weight=ft.FontWeight.W_600 if toggle.value else ft.FontWeight.NORMAL,
            no_wrap=True,
            expand=True,
        )
        return ft.Container(
            content=ft.Row(
                [
                    ft.Icon(
                        ft.Icons.CHECK_BOX if toggle.value else ft.Icons.CHECK_BOX_OUTLINE_BLANK,
                        size=14,
                        color=COLOR_PRIMARY if toggle.value else COLOR_ON_BACKGROUND,
                    ),
                    chip_text,
                ],
                spacing=6,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
                tight=True,
            ),
            bgcolor=_TOGGLE_ON if toggle.value else _TOGGLE_OFF,
            border_radius=8,
            border=ft.border.all(1, COLOR_PRIMARY if toggle.value else COLOR_DIVIDER),
            padding=ft.padding.symmetric(horizontal=10, vertical=6),
            expand=True,
            on_click=lambda e, t=toggle, ct=chip_text: self._on_toggle_click(e, t, ct),
        )

    def _build_toggles(self) -> ft.Control:
        # Render as paired rows: [Show X] [Send X] per transliteration type
        rows: list[ft.Control] = []
        for i in range(0, len(self._toggles), 2):
            pair = self._toggles[i:i + 2]
            rows.append(ft.Row(
                controls=[self._build_chip(t) for t in pair],
                spacing=8,
            ))
        return ft.Column(controls=rows, spacing=6)

    def _on_toggle_click(self, e, toggle: LanguageModalToggle, label_text: ft.Text) -> None:
        toggle.value = not toggle.value
        chip = e.control
        chip.bgcolor = _TOGGLE_ON if toggle.value else _TOGGLE_OFF
        chip.border = ft.border.all(1, COLOR_PRIMARY if toggle.value else COLOR_DIVIDER)
        icon = chip.content.controls[0]
        icon.name = ft.Icons.CHECK_BOX if toggle.value else ft.Icons.CHECK_BOX_OUTLINE_BLANK
        icon.color = COLOR_PRIMARY if toggle.value else COLOR_ON_BACKGROUND
        label_text.color = COLOR_PRIMARY if toggle.value else COLOR_ON_BACKGROUND
        label_text.weight = ft.FontWeight.W_600 if toggle.value else ft.FontWeight.NORMAL
        chip.update()
        toggle.on_change(toggle.value)

    def _build_recent_chips(self, recent: list[str], current: str) -> ft.Control:
        chips = []
        for lang_code in recent[:6]:
            is_current = lang_code == current
            chip = ft.Container(
                content=ft.Text(
                    language_name(lang_code),
                    size=13,
                    weight=ft.FontWeight.W_600 if is_current else ft.FontWeight.NORMAL,
                    color=COLOR_PRIMARY if is_current else COLOR_ON_BACKGROUND,
                    text_align=ft.TextAlign.CENTER,
                    no_wrap=True,
                    overflow=ft.TextOverflow.ELLIPSIS,
                ),
                bgcolor=_BG_ITEM_SELECTED if is_current else _BG_ITEM,
                border_radius=8,
                border=ft.border.all(1, COLOR_PRIMARY if is_current else COLOR_DIVIDER),
                padding=ft.padding.symmetric(horizontal=12, vertical=8),
                alignment=ft.alignment.center,
                on_click=lambda e, code=lang_code: self._select(code),
                on_hover=lambda e, is_sel=is_current: self._on_chip_hover(e, is_sel),
            )
            chips.append(chip)
        return ft.Row(controls=chips, wrap=True, spacing=8, run_spacing=6)

    def _build_all_lang_items(self, current: str) -> list[tuple[str, ft.Container]]:
        """Returns list of (name_lower, widget) for search filtering."""
        result = []
        for code, _name in self._languages:
            name = language_name(code)
            is_selected = code == current
            item = ft.Container(
                content=ft.Row(
                    [
                        ft.Text(
                            name,
                            size=14,
                            color=COLOR_PRIMARY if is_selected else COLOR_ON_BACKGROUND,
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
                ),
                bgcolor=_BG_ITEM_SELECTED if is_selected else ft.Colors.TRANSPARENT,
                border_radius=6,
                padding=ft.padding.symmetric(horizontal=12, vertical=9),
                on_click=lambda e, sel=code: self._select(sel),
                on_hover=lambda e, is_sel=is_selected: self._on_item_hover(e, is_sel),
            )
            result.append((name.lower(), item))
        return result

    def _on_chip_hover(self, e: ft.ControlEvent, is_selected: bool) -> None:
        if is_selected:
            return
        container = e.control
        container.bgcolor = COLOR_SURFACE_TONAL if e.data == "true" else _BG_ITEM
        container.update()

    def _on_item_hover(self, e: ft.ControlEvent, is_selected: bool) -> None:
        if is_selected:
            return
        container = e.control
        container.bgcolor = COLOR_SURFACE_TONAL if e.data == "true" else ft.Colors.TRANSPARENT
        container.update()

    def _select(self, name: str) -> None:
        logger.info("[LanguageModal] Selection requested: %s", name)
        if self._dialog:
            self._page.close(self._dialog)
        self._on_select(name)
