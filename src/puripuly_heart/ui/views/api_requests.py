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
        self.on_send_custom_request: Callable[[str, str], None] | None = None
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
        header = ft.Container(
            content=ft.Row(
                [self._title_text, ft.Container(expand=True)],
                alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
            padding=ft.padding.only(left=16, right=8, top=8, bottom=0),
        )

        self._feed_text = ft.Text(
            t("logs.api.empty"),
            size=14,
            font_family="Consolas",
            color=COLOR_ON_BACKGROUND,
            selectable=True,
        )
        self._feed_scroll = ft.Column(
            controls=[
                ft.Container(
                    content=self._feed_text,
                    padding=ft.padding.only(left=16, right=16, top=8, bottom=16),
                )
            ],
            expand=True,
            scroll=ft.ScrollMode.AUTO,
        )

        self._prompt_field = ft.TextField(
            label=t("logs.api.prompt"),
            multiline=True,
            min_lines=3,
            max_lines=8,
            text_size=12,
            border_radius=12,
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
                    self._prompt_field,
                    ft.Row(
                        [self._text_field, self._send_button],
                        vertical_alignment=ft.CrossAxisAlignment.END,
                        spacing=8,
                    ),
                ],
                spacing=8,
            ),
            padding=ft.padding.only(left=16, right=16, bottom=12),
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

    def _render(self) -> None:
        if not self._model:
            self._feed_text.value = t("logs.api.empty")
        else:
            blocks: list[str] = []
            for e in self._model:
                if e.get("stage") == "response":
                    blocks.append(
                        f"[{e.get('ts', '')}] ← {e.get('provider', '')} response:\n"
                        f"{e.get('text', '')}"
                    )
                    continue
                head = (
                    f"[{e.get('ts', '')}] → {e.get('provider', '')} · {e.get('stage', '')} · "
                    f"{e.get('source_language') or 'auto'} → {e.get('target_language', '')}"
                )
                body = [f"text: {e.get('text', '')}"]
                if e.get("prompt_sent", True):
                    body.append(f"context: {e.get('context') or '(none)'}")
                    body.append(f"system_prompt:\n{e.get('system_prompt') or '(none)'}")
                else:
                    body.append(t("logs.api.not_sent"))
                blocks.append(head + "\n" + "\n".join(body))
            self._feed_text.value = ("\n" + "─" * 60 + "\n").join(blocks)
        if self.page:
            try:
                self._feed_text.update()
            except Exception:
                pass

    async def scroll_to_bottom(self) -> None:
        if self._feed_scroll and self.page:
            result = self._feed_scroll.scroll_to(offset=-1, duration=0)
            import inspect as _inspect

            if _inspect.isawaitable(result):
                await result

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
        if callable(self.on_send_custom_request):
            self.on_send_custom_request(prompt, text)

    # ── Locale ───────────────────────────────────────────────────────────

    def apply_locale(self) -> None:
        self._title_text.value = t("api_view.title")
        self._prompt_field.label = t("logs.api.prompt")
        self._text_field.label = t("logs.api.text")
        self._send_button.tooltip = t("logs.api.send")
        if not self._model:
            self._feed_text.value = t("logs.api.empty")
        if self.page:
            self.update()
