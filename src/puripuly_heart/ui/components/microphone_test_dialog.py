from __future__ import annotations

from collections.abc import Callable

import flet as ft

from puripuly_heart.ui.components.glow import create_glow_stack
from puripuly_heart.ui.fonts import font_for_language
from puripuly_heart.ui.i18n import get_locale, t
from puripuly_heart.ui.theme import (
    COLOR_DIVIDER,
    COLOR_NEUTRAL_DARK,
    COLOR_PRIMARY,
    COLOR_SURFACE,
    get_card_shadow,
)

_DIALOG_WIDTH = 450
_DIALOG_HEIGHT = 500
_CONTENT_SIZE = 386
_PERCENT_TEXT_SIZE = 96
_FAILURE_TEXT_SIZE = 24
_HINT_TEXT_SIZE = 28


def _clamp_level(value: float) -> float:
    level = max(0.0, min(1.0, float(value)))
    if level <= 1e-6:
        return 0.0
    return level


def _level_percent(value: float) -> int:
    return int(round(_clamp_level(value) * 100))


class MicrophoneTestDialog:
    """Minimal microphone-test modal with a large live percentage readout."""

    def __init__(
        self,
        page: ft.Page,
        *,
        on_close: Callable[[], None],
        on_record: Callable[[], None] | None = None,
        on_play: Callable[[], None] | None = None,
        on_stop: Callable[[], None] | None = None,
    ) -> None:
        self._page = page
        self._on_close = on_close
        self._on_record = on_record
        self._on_play = on_play
        self._on_stop = on_stop
        self._record_btn: ft.TextButton | None = None
        self._play_btn: ft.TextButton | None = None
        self._stop_btn: ft.TextButton | None = None
        self._clip_text: ft.Text | None = None
        self._clip_status = ""
        self._has_clip = False
        self._busy = False
        self._dialog: ft.AlertDialog | None = None
        self._level_text: ft.Text | None = None
        self._hint_text: ft.Text | None = None
        self._level = 0.0
        self._failed = False
        self._is_open = False
        self._close_notified = False

    @property
    def dialog(self) -> ft.AlertDialog | None:
        return self._dialog

    @property
    def is_open(self) -> bool:
        return self._is_open

    def open(self) -> None:
        if self._is_open:
            return
        self._close_notified = False
        self._dialog = self._build_dialog()
        self._is_open = True
        self._page.open(self._dialog)

    def close(self, *, notify: bool = False) -> None:
        dialog = self._dialog
        if dialog is None:
            self._close_notified = True
            return
        was_open = self._is_open
        self._is_open = False
        if notify:
            self._notify_close_once()
        else:
            self._close_notified = True
        if was_open:
            close = getattr(self._page, "close", None)
            if callable(close):
                close(dialog)

    def reset(self) -> None:
        self._level = 0.0
        self._failed = False
        self._clip_status = ""
        self._has_clip = False
        self._busy = False
        self._sync_text()
        self._sync_clip_controls()

    # ── record / play back ──────────────────────────────────────────────
    def set_recording(self) -> None:
        self._busy = True
        self._clip_status = t("settings.microphone_test.recording")
        self._sync_clip_controls()

    def set_recorded(self, seconds: float) -> None:
        self._busy = False
        if seconds <= 0:
            # stopped before anything was captured
            self._clip_status = ""
            self._sync_clip_controls()
            return
        self._has_clip = True
        self._clip_status = t("settings.microphone_test.recorded", seconds=f"{seconds:.1f}")
        self._sync_clip_controls()

    def set_playing(self, playing: bool) -> None:
        self._busy = playing
        self._clip_status = t("settings.microphone_test.playing") if playing else (
            t("settings.microphone_test.played") if self._has_clip else ""
        )
        self._sync_clip_controls()

    def _sync_clip_controls(self) -> None:
        # while recording or playing, Stop takes the place of Record
        if self._record_btn is not None:
            self._record_btn.disabled = self._busy or self._failed
            self._record_btn.visible = not (self._busy and self._stop_btn is not None)
        if self._stop_btn is not None:
            self._stop_btn.visible = self._busy
        if self._play_btn is not None:
            self._play_btn.disabled = self._busy or not self._has_clip
        if self._clip_text is not None:
            self._clip_text.value = self._clip_status
        for ctrl in (self._record_btn, self._stop_btn, self._play_btn, self._clip_text):
            if ctrl is None or getattr(ctrl, "page", None) is None:
                continue
            try:
                ctrl.update()
            except AssertionError as exc:
                if "Control must be added" not in str(exc):
                    raise

    def set_level(self, value: float) -> None:
        self._level = _clamp_level(value)
        self._failed = False
        self._sync_text()

    def show_failure(self) -> None:
        self._level = 0.0
        self._failed = True
        self._sync_text()

    def _build_dialog(self) -> ft.AlertDialog:
        self._level_text = ft.Text(
            self._text_value(),
            size=self._text_size(),
            weight=ft.FontWeight.BOLD,
            color=self._text_color(),
            text_align=ft.TextAlign.CENTER,
            font_family=font_for_language(get_locale()),
            semantics_label=t("settings.microphone_test.level_label"),
        )
        self._hint_text = ft.Text(
            t("settings.microphone_test.host_api_hint"),
            size=_HINT_TEXT_SIZE,
            color=COLOR_NEUTRAL_DARK,
            text_align=ft.TextAlign.CENTER,
            font_family=font_for_language(get_locale()),
        )

        font = font_for_language(get_locale())
        self._record_btn = ft.TextButton(
            text=t("settings.microphone_test.record"),
            icon=ft.Icons.FIBER_MANUAL_RECORD,
            icon_color="#d65a5a",
            on_click=lambda _e: self._on_record() if callable(self._on_record) else None,
            disabled=self._busy or self._failed,
        )
        self._stop_btn = ft.TextButton(
            text=t("settings.microphone_test.stop"),
            icon=ft.Icons.STOP_ROUNDED,
            icon_color="#d65a5a",
            on_click=lambda _e: self._on_stop() if callable(self._on_stop) else None,
            visible=self._busy and callable(self._on_stop),
        )
        if not callable(self._on_stop):
            self._stop_btn = None
        self._play_btn = ft.TextButton(
            text=t("settings.microphone_test.play"),
            icon=ft.Icons.PLAY_ARROW_ROUNDED,
            on_click=lambda _e: self._on_play() if callable(self._on_play) else None,
            disabled=self._busy or not self._has_clip,
        )
        self._clip_text = ft.Text(
            self._clip_status,
            size=13,
            color=COLOR_NEUTRAL_DARK,
            text_align=ft.TextAlign.CENTER,
            font_family=font,
        )
        clip_controls = ft.Column(
            [
                ft.Row(
                    [c for c in (self._record_btn, self._stop_btn, self._play_btn) if c is not None],
                    alignment=ft.MainAxisAlignment.CENTER,
                    spacing=8,
                ),
                self._clip_text,
            ],
            spacing=2,
            horizontal_alignment=ft.CrossAxisAlignment.CENTER,
            visible=callable(self._on_record),
        )

        modal_content = ft.Container(
            width=_DIALOG_WIDTH,
            height=_DIALOG_HEIGHT,
            padding=28,
            bgcolor=COLOR_SURFACE,
            border_radius=30,
            border=ft.border.all(1, ft.Colors.with_opacity(0.35, COLOR_DIVIDER)),
            shadow=get_card_shadow(),
            alignment=ft.alignment.center,
            content=ft.Column(
                controls=[
                    ft.Container(
                        width=_CONTENT_SIZE,
                        content=self._level_text,
                        alignment=ft.alignment.center,
                        bgcolor=ft.Colors.TRANSPARENT,
                        expand=True,
                    ),
                    clip_controls,
                    self._hint_text,
                ],
                spacing=12,
                alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                tight=False,
            ),
        )

        return ft.AlertDialog(
            modal=False,
            content=create_glow_stack(modal_content),
            content_padding=0,
            bgcolor=ft.Colors.TRANSPARENT,
            surface_tint_color=ft.Colors.TRANSPARENT,
            semantics_label=t("settings.microphone_test"),
            on_dismiss=self._handle_dismiss,
        )

    def _text_value(self) -> str:
        if self._failed:
            return t("settings.microphone_test.start_failed")
        return f"{_level_percent(self._level)}%"

    def _text_size(self) -> int:
        return _FAILURE_TEXT_SIZE if self._failed else _PERCENT_TEXT_SIZE

    def _text_color(self) -> str:
        return COLOR_NEUTRAL_DARK if self._failed else COLOR_PRIMARY

    def _sync_text(self) -> None:
        if self._level_text is None:
            return
        self._level_text.value = self._text_value()
        self._level_text.size = self._text_size()
        self._level_text.color = self._text_color()
        if getattr(self._level_text, "page", None) is None:
            return
        try:
            self._level_text.update()
        except AssertionError as exc:
            if "Control must be added" not in str(exc):
                raise

    def _handle_dismiss(self, _event) -> None:  # noqa: ANN001
        if not self._is_open:
            return
        self._is_open = False
        self._notify_close_once()

    def _notify_close_once(self) -> None:
        if self._close_notified:
            return
        self._close_notified = True
        self._on_close()
