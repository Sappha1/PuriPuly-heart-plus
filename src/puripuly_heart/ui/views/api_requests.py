"""API Requests view — its own tab (not part of the Logs page).

Live, wire-accurate feed of outbound translation requests (API_REQUEST events
from the hub) plus a composer to hand-send an edited system prompt / text to
the ACTIVE provider and see the raw response. Entries state per provider what
actually goes over the wire — DeepL/free-web receive only text + languages.
"""

from __future__ import annotations

import time
from collections import deque
from typing import Callable

import flet as ft

from puripuly_heart.ui.components.glow import GLOW_CARD, create_glow_stack
from puripuly_heart.ui.fonts import font_for_language
from puripuly_heart.ui.i18n import get_locale, t
from puripuly_heart.ui.theme import (
    COLOR_NEUTRAL,
    COLOR_ON_BACKGROUND,
    COLOR_PRIMARY,
    COLOR_SURFACE,
    get_card_shadow,
)

_MAX_ENTRIES = 50


class ApiRequestsView(ft.Column):
    def __init__(self) -> None:
        super().__init__(expand=True, spacing=16)
        self.on_send_custom_request: Callable[[str, str, bool], None] | None = None
        self._model: deque[dict] = deque(maxlen=_MAX_ENTRIES)
        self._build_ui()

    # ── UI ───────────────────────────────────────────────────────────────

    def _button_style(self, font_family: str) -> ft.ButtonStyle:
        return ft.ButtonStyle(
            color={
                ft.ControlState.HOVERED: COLOR_PRIMARY,
                ft.ControlState.DEFAULT: COLOR_NEUTRAL,
            },
            text_style=ft.TextStyle(size=16, font_family=font_family),
            overlay_color=ft.Colors.TRANSPARENT,
        )

    def _build_ui(self) -> None:
        font_family = font_for_language(get_locale())

        self._title_text = ft.Text(
            t("api_view.title"),
            size=28,
            weight=ft.FontWeight.BOLD,
            color=COLOR_NEUTRAL,
        )
        self._clear_button = ft.TextButton(
            text=t("dashboard.clear"),
            icon=ft.Icons.CLEAR_ALL,
            style=self._button_style(font_family),
            on_click=self._on_clear_click,
        )
        # In the header next to Clear (short label; the tooltip explains) —
        # it sat awkwardly inside the composer. OFF by default: prompt
        # experiments shouldn't spam the in-game chatbox unless asked to.
        self._push_vrchat_checkbox = ft.Checkbox(
            label=t("logs.api.push_vrchat.short"),
            tooltip=t("logs.api.push_vrchat"),
            value=False,
        )
        header = ft.Container(
            content=ft.Row(
                [self._title_text, ft.Container(expand=True),
                 self._push_vrchat_checkbox, self._clear_button],
                alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
                spacing=12,
            ),
            padding=ft.padding.only(left=16, right=8, top=8, bottom=0),
        )

        self._empty_text = ft.Text(
            t("logs.api.empty"),
            size=13,
            color=COLOR_NEUTRAL,
            italic=True,
        )
        self._feed_scroll = ft.Column(
            controls=[ft.Container(content=self._empty_text,
                                   padding=ft.padding.only(left=16, right=16, top=8))],
            expand=True,
            scroll=ft.ScrollMode.AUTO,
            spacing=8,
        )

        # Collapsed by default — the big prompt box is in the way unless the
        # user is actively editing it. The header row toggles it open.
        self._prompt_field = ft.TextField(
            label=t("logs.api.prompt"),
            multiline=True,
            min_lines=3,
            max_lines=8,
            text_size=12,
            border_radius=12,
            visible=False,
        )
        self._prompt_toggle_icon = ft.Icon(
            ft.Icons.EXPAND_MORE, size=16, color=COLOR_NEUTRAL)
        self._prompt_toggle_label = ft.Text(
            t("logs.api.prompt"), size=12, weight=ft.FontWeight.W_600,
            color=COLOR_NEUTRAL)
        self._prompt_toggle = ft.Container(
            content=ft.Row(
                [self._prompt_toggle_icon, self._prompt_toggle_label],
                spacing=6,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
            padding=ft.padding.symmetric(horizontal=4, vertical=4),
            border_radius=6,
            on_click=self._on_prompt_toggle,
        )
        self._text_field = ft.TextField(
            label=t("logs.api.text"),
            multiline=True,
            min_lines=1,
            max_lines=3,
            text_size=13,
            border_radius=12,
            expand=True,
            # Same behavior as the dashboard chat box: Enter sends,
            # Shift+Enter inserts a newline.
            shift_enter=True,
            on_submit=self._on_send_click,
        )
        self._send_button = ft.IconButton(
            ft.Icons.SEND_ROUNDED,
            tooltip=t("logs.api.send"),
            on_click=self._on_send_click,
        )
        composer = ft.Container(
            content=ft.Column(
                [
                    self._prompt_toggle,
                    self._prompt_field,
                    ft.Row(
                        [self._text_field, self._send_button],
                        vertical_alignment=ft.CrossAxisAlignment.END,
                        spacing=8,
                    ),
                ],
                spacing=10,
            ),
            padding=ft.padding.only(left=16, right=16, top=14, bottom=12),
            border=ft.border.only(top=ft.BorderSide(1, "#3a3b3f")),
        )

        card_content = ft.Column(
            controls=[header, self._feed_scroll, composer],
            spacing=0,
            expand=True,
        )
        content_with_glow = create_glow_stack(
            ft.Container(content=card_content, expand=True),
            config=GLOW_CARD,
        )
        card = ft.Container(
            content=content_with_glow,
            bgcolor=COLOR_SURFACE,
            border_radius=16,
            border=ft.border.all(1, ft.Colors.with_opacity(0.4, ft.Colors.WHITE)),
            expand=True,
            clip_behavior=ft.ClipBehavior.HARD_EDGE,
            shadow=get_card_shadow(),
        )
        self.controls = [card]

    # ── Data ─────────────────────────────────────────────────────────────

    def append_api_request(self, entry: dict) -> None:
        record = dict(entry)
        record.setdefault("ts", time.strftime("%H:%M:%S"))
        self._model.append(record)
        # Prefill the composer with the newest real LLM prompt so the user
        # tweaks the live prompt rather than typing one from scratch.
        if (
            self._prompt_field is not None
            and not self._prompt_field.value
            and record.get("prompt_sent")
            and record.get("system_prompt")
        ):
            self._prompt_field.value = record["system_prompt"]
            if self.page:
                try:
                    self._prompt_field.update()
                except Exception:
                    pass
        self._render()

    def append_api_response(self, provider: str, text: str) -> None:
        self._model.append({
            "ts": time.strftime("%H:%M:%S"),
            "provider": provider,
            "stage": "response",
            "text": text,
        })
        self._render()

    def _entry_control(self, e: dict) -> ft.Control:
        _TIME = "#6e7175"
        _OUT = COLOR_PRIMARY          # teal: outbound request
        _IN = "#6ab7e8"               # light blue: provider response
        _MUTED = "#9a9da1"
        _FAINT = "#6e7175"
        rows: list[ft.Control] = []
        if e.get("stage") == "response":
            rows.append(ft.Row([
                ft.Text(e.get("ts", ""), size=11, color=_TIME),
                ft.Text("← " + str(e.get("provider", "")), size=12,
                        weight=ft.FontWeight.W_700, color=_IN),
            ], spacing=8))
            rows.append(ft.Text(str(e.get("text", "")), size=14,
                                color=COLOR_ON_BACKGROUND, selectable=True))
            border_color = _IN
        else:
            langs = f"{e.get('source_language') or 'auto'} → {e.get('target_language', '')}"
            rows.append(ft.Row([
                ft.Text(e.get("ts", ""), size=11, color=_TIME),
                ft.Text("→ " + str(e.get("provider", "")), size=12,
                        weight=ft.FontWeight.W_700, color=_OUT),
                ft.Text(str(e.get("stage", "")), size=11, color=_MUTED),
                ft.Text(langs, size=11, color=_MUTED),
            ], spacing=8, wrap=True))
            rows.append(ft.Text(str(e.get("text", "")), size=14,
                                color=COLOR_ON_BACKGROUND, selectable=True))
            if e.get("prompt_sent", True):
                ctx = e.get("context") or ""
                if ctx:
                    rows.append(ft.Text("context: " + ctx, size=12, color=_MUTED,
                                        selectable=True))
                prompt = e.get("system_prompt") or ""
                if prompt:
                    rows.append(ft.Container(
                        content=ft.Text(prompt, size=11, color=_FAINT, selectable=True),
                        padding=ft.padding.only(left=8, top=2),
                        border=ft.border.only(left=ft.BorderSide(2, "#3a3b3f")),
                    ))
            else:
                rows.append(ft.Text(t("logs.api.not_sent"), size=11, color=_FAINT,
                                    italic=True))
            border_color = _OUT
        return ft.Container(
            content=ft.Column(rows, spacing=4),
            padding=ft.padding.symmetric(horizontal=12, vertical=8),
            margin=ft.margin.only(left=16, right=16),
            border_radius=10,
            bgcolor="#2e2f33",
            border=ft.border.only(left=ft.BorderSide(3, border_color)),
        )

    def _render(self) -> None:
        if not self._model:
            self._feed_scroll.controls = [ft.Container(
                content=self._empty_text,
                padding=ft.padding.only(left=16, right=16, top=8))]
        else:
            self._feed_scroll.controls = [self._entry_control(e) for e in self._model]
        if self.page:
            try:
                self._feed_scroll.update()
            except Exception:
                pass

    async def scroll_to_bottom(self) -> None:
        if self._feed_scroll and self.page:
            result = self._feed_scroll.scroll_to(offset=-1, duration=0)
            import inspect as _inspect

            if _inspect.isawaitable(result):
                await result

    def _on_prompt_toggle(self, _e: ft.ControlEvent | object) -> None:
        self._prompt_field.visible = not self._prompt_field.visible
        self._prompt_toggle_icon.name = (
            ft.Icons.EXPAND_LESS if self._prompt_field.visible else ft.Icons.EXPAND_MORE)
        if self.page:
            try:
                self.update()
            except Exception:
                pass

    def _on_clear_click(self, _e: ft.ControlEvent | object) -> None:
        self._model.clear()
        self._render()

    def set_prompt_if_empty(self, prompt: str) -> None:
        """Prefill the composer with the app's active prompt (called when the
        tab opens) — only when the user hasn't typed their own yet."""
        if not prompt or (self._prompt_field.value or "").strip():
            return
        self._prompt_field.value = prompt
        if self.page:
            try:
                self._prompt_field.update()
            except Exception:
                pass

    # ── Composer ─────────────────────────────────────────────────────────

    def _on_send_click(self, _e: ft.ControlEvent | object) -> None:
        prompt = self._prompt_field.value or ""
        text = self._text_field.value or ""
        if not text.strip():
            return
        # Clear on send, like the dashboard chat box (the prompt stays).
        self._text_field.value = ""
        if self.page:
            try:
                self._text_field.update()
            except Exception:
                pass
        if callable(self.on_send_custom_request):
            self.on_send_custom_request(
                prompt, text, bool(self._push_vrchat_checkbox.value))

    # ── Locale ───────────────────────────────────────────────────────────

    def apply_locale(self) -> None:
        self._title_text.value = t("api_view.title")
        self._clear_button.text = t("dashboard.clear")
        self._clear_button.style = self._button_style(font_for_language(get_locale()))
        self._prompt_field.label = t("logs.api.prompt")
        self._prompt_toggle_label.value = t("logs.api.prompt")
        self._text_field.label = t("logs.api.text")
        self._send_button.tooltip = t("logs.api.send")
        self._push_vrchat_checkbox.label = t("logs.api.push_vrchat.short")
        self._push_vrchat_checkbox.tooltip = t("logs.api.push_vrchat")
        self._empty_text.value = t("logs.api.empty")
        if self._model:
            self._render()
        if self.page:
            self.update()
