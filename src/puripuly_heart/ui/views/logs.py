"""System logs view with real-time log display and folder access."""

import asyncio
import inspect
import logging
import subprocess
import sys
import time
from collections import deque
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable

import flet as ft

from puripuly_heart.ui.components.glow import GLOW_CARD, create_glow_stack
from puripuly_heart.ui.fonts import font_for_language
from puripuly_heart.ui.i18n import get_locale, source_label, t
from puripuly_heart.ui.theme import (
    COLOR_NEUTRAL,
    COLOR_ON_BACKGROUND,
    COLOR_PRIMARY,
    COLOR_SURFACE,
    get_card_shadow,
)

MAX_LOG_ENTRIES = 4000
CLEANUP_BATCH = 500
MAX_CONVERSATION_RECORDS = 1000
_UPDATE_INTERVAL = 0.2  # 200ms throttling
_BASIC_MODE = "basic"
_DETAILED_MODE = "detailed"


def _get_log_dir() -> Path:
    """Get the directory where log files are stored."""
    from puripuly_heart.config.paths import user_config_dir

    return user_config_dir()


def _format_conversation_timestamp(origin_wall_clock_ms: int | None) -> str:
    timestamp_s = origin_wall_clock_ms / 1000.0 if origin_wall_clock_ms is not None else time.time()
    return datetime.fromtimestamp(timestamp_s).strftime("%H:%M:%S")


class FletLogHandler(logging.Handler):
    """Custom log handler that forwards logs to a LogsView."""

    def __init__(self, logs_view: "LogsView"):
        super().__init__()
        self.logs_view = logs_view
        self.setFormatter(
            logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")
        )

    def emit(self, record: logging.LogRecord) -> None:
        try:
            msg = self.format(record)
            append_log_threadsafe = getattr(self.logs_view, "append_log_threadsafe", None)
            if callable(append_log_threadsafe):
                append_log_threadsafe(msg)
            else:
                self.logs_view.append_log(msg)
        except Exception:
            pass


class LiveLogViewModel:
    """Keeps the merged live-log buffer shared by app and overlay sources."""

    def __init__(
        self,
        *,
        max_entries: int = MAX_LOG_ENTRIES,
        cleanup_batch: int = CLEANUP_BATCH,
    ) -> None:
        self._max_entries = max_entries
        self._cleanup_batch = cleanup_batch
        self._visible_lines: list[str] = []
        self._cleanup_count = 0

    @property
    def visible_lines(self) -> list[str]:
        return self._visible_lines

    @property
    def cleanup_count(self) -> int:
        return self._cleanup_count

    def append(self, record: str) -> None:
        self._visible_lines.append(record)
        if len(self._visible_lines) > self._max_entries + self._cleanup_batch:
            del self._visible_lines[: self._cleanup_batch]
            self._cleanup_count += 1


@dataclass(frozen=True, slots=True)
class ConversationRecord:
    timestamp_label: str
    source: str
    channel: str
    source_text: str
    translated_text: str


class ConversationViewModel:
    """Keeps session-only original/translation pairs for the Logs conversation view."""

    def __init__(
        self,
        *,
        max_entries: int = MAX_CONVERSATION_RECORDS,
        cleanup_batch: int = CLEANUP_BATCH,
    ) -> None:
        self._max_entries = max_entries
        self._cleanup_batch = cleanup_batch
        self._records: list[ConversationRecord] = []
        self._cleanup_count = 0

    @property
    def records(self) -> list[ConversationRecord]:
        return self._records

    @property
    def cleanup_count(self) -> int:
        return self._cleanup_count

    def append(self, record: ConversationRecord) -> None:
        self._records.append(record)
        if len(self._records) > self._max_entries + self._cleanup_batch:
            del self._records[: self._cleanup_batch]
            self._cleanup_count += 1

    def render(self) -> str:
        if not self._records:
            return t("logs.conversation.empty")

        return "\n\n".join(
            f"[{record.timestamp_label}] {source_label(record.source)}\n"
            f"{record.source_text}\n"
            f"{record.translated_text}"
            for record in self._records
        )


class _LogListProxy:
    """Compatibility proxy for tests expecting a list-style log view."""

    def __init__(self, view: "LogsView") -> None:
        self._view = view

    @property
    def controls(self) -> list[ft.Text]:
        return [ft.Text(entry) for entry in self._view._model.visible_lines]


class LogsView(ft.Column):
    """System logs view with VR-optimized display and folder access."""

    def __init__(self):
        super().__init__(expand=True, spacing=16)

        self.on_mode_change: Callable[[str], None] | None = None

        self._handler: FletLogHandler | None = None
        self._title_text: ft.Text | None = None
        self._mode_button: ft.TextButton | None = None
        self._log_text: ft.Text | None = None
        self._log_scroll: ft.Column | None = None
        self._folder_button: ft.TextButton | None = None
        self._header_button_row: ft.Row | None = None
        self._conversation_button: ft.TextButton | None = None
        self._showing_conversation = False
        self._conversation_model = ConversationViewModel()
        self._runtime_logging_mode = _BASIC_MODE

        # API Requests view: structured captures of outbound translation
        # requests (from hub API_REQUEST events) + a composer to hand-send a
        # custom prompt/text to the active provider.
        self._api_button: ft.TextButton | None = None
        self._showing_api_requests = False
        self._api_request_model: deque[dict] = deque(maxlen=50)
        self._api_prompt_field: ft.TextField | None = None
        self._api_text_field: ft.TextField | None = None
        self._api_composer: ft.Container | None = None
        self.on_send_custom_request: Callable[[str, str], None] | None = None

        # Log buffer and throttling state
        self._model = LiveLogViewModel()
        self._log_buffer = self._model.visible_lines
        self._last_update: float = 0.0
        self._pending_update: bool = False
        self._rendered_line_count: int = 0
        self._last_cleanup_count: int = 0
        self.log_list = _LogListProxy(self)

        self._build_ui()

    def _get_button_style(self, font_family: str) -> ft.ButtonStyle:
        """Create a complete ButtonStyle with the specified font."""
        return ft.ButtonStyle(
            color={
                ft.ControlState.HOVERED: COLOR_PRIMARY,
                ft.ControlState.DEFAULT: COLOR_NEUTRAL,
            },
            icon_color={
                ft.ControlState.HOVERED: COLOR_PRIMARY,
                ft.ControlState.DEFAULT: COLOR_NEUTRAL,
            },
            text_style=ft.TextStyle(
                size=20,
                font_family=font_family,
            ),
            overlay_color=ft.Colors.TRANSPARENT,
            animation_duration=0,
        )

    def _build_ui(self):
        """Build the logs view UI."""
        font_family = font_for_language(get_locale())

        # Title (styled like About page section headers)
        self._title_text = ft.Text(
            t("logs.title"),
            size=28,
            weight=ft.FontWeight.BOLD,
            color=COLOR_NEUTRAL,
        )

        # Folder open button (brown, hover -> primary)
        self._folder_button = ft.TextButton(
            text=t("logs.open_folder"),
            icon=ft.Icons.FOLDER_OPEN,
            style=self._get_button_style(font_family),
            on_click=self._open_log_folder,
        )
        self._mode_button = ft.TextButton(
            text=self._mode_button_label(),
            icon=ft.Icons.ARTICLE,
            style=self._get_button_style(font_family),
            on_click=self._on_mode_button_click,
        )
        self._conversation_button = ft.TextButton(
            text=self._conversation_button_label(),
            icon=ft.Icons.CHAT_BUBBLE_OUTLINE,
            style=self._get_button_style(font_family),
            on_click=self._on_conversation_button_click,
        )
        self._api_button = ft.TextButton(
            text=self._api_button_label(),
            icon=ft.Icons.SWAP_VERT,
            style=self._get_button_style(font_family),
            on_click=self._on_api_button_click,
        )

        # Header rows
        self._header_button_row = ft.Row(
            controls=[self._folder_button, self._mode_button,
                      self._conversation_button, self._api_button],
            spacing=4,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        )
        header = ft.Container(
            content=ft.Row(
                controls=[
                    self._title_text,
                    ft.Container(expand=True),
                    self._header_button_row,
                ],
                alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
            padding=ft.padding.only(left=16, right=8, top=8, bottom=0),
        )

        # Single selectable text for all logs (enables multi-line drag selection)
        self._log_text = ft.Text(
            "",
            size=16,
            font_family="Consolas",
            color=COLOR_ON_BACKGROUND,
            selectable=True,
        )

        # Scrollable container for log text
        self._log_scroll = ft.Column(
            controls=[
                ft.Container(
                    content=self._log_text,
                    padding=ft.padding.only(left=16, right=16, top=8, bottom=16),
                )
            ],
            expand=True,
            scroll=ft.ScrollMode.AUTO,
        )

        # API Requests composer: edit the prompt/text and hand-send it to the
        # active provider. Visible only in the API Requests view.
        self._api_prompt_field = ft.TextField(
            label=t("logs.api.prompt"),
            multiline=True,
            min_lines=3,
            max_lines=8,
            text_size=12,
            border_radius=12,
        )
        self._api_text_field = ft.TextField(
            label=t("logs.api.text"),
            multiline=True,
            min_lines=1,
            max_lines=3,
            text_size=13,
            border_radius=12,
        )
        self._api_send_button = ft.TextButton(
            text=t("logs.api.send"),
            icon=ft.Icons.SEND,
            style=self._get_button_style(font_family),
            on_click=self._on_api_send_click,
        )
        self._api_composer = ft.Container(
            content=ft.Column(
                [
                    self._api_prompt_field,
                    self._api_text_field,
                    ft.Row([ft.Container(expand=True), self._api_send_button]),
                ],
                spacing=8,
            ),
            padding=ft.padding.only(left=16, right=16, bottom=12),
            visible=False,
        )

        # Card content
        card_content = ft.Column(
            controls=[header, self._log_scroll, self._api_composer],
            spacing=0,
            expand=True,
        )

        # Wrap in glow stack
        content_with_glow = create_glow_stack(
            ft.Container(content=card_content, expand=True),
            config=GLOW_CARD,
        )

        # Outer card container
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

    def attach_log_handler(self) -> None:
        """Attach this view as a logging handler to capture app logs."""
        if self._handler is not None:
            return
        self._handler = FletLogHandler(self)
        logging.getLogger().addHandler(self._handler)

    def append_log(self, record: str):
        """Append a log entry with throttled updates."""
        self._model.append(record)

        # Throttled update
        now = time.time()
        if now - self._last_update >= _UPDATE_INTERVAL:
            self._flush_logs()
        else:
            self._pending_update = True

    def append_log_threadsafe(self, record: str) -> None:
        """Append a log entry from any thread without mutating Flet state off-loop."""
        page = self.page
        loop = getattr(page, "loop", None) if page is not None else None
        if loop is None:
            self.append_log(record)
            return

        try:
            running_loop = asyncio.get_running_loop()
        except RuntimeError:
            running_loop = None

        if running_loop is loop:
            self.append_log(record)
            return

        if self._schedule_log_append(record):
            return
        self._buffer_pending_log(record)

    @property
    def conversation_records(self) -> tuple[ConversationRecord, ...]:
        return tuple(self._conversation_model.records)

    def append_conversation_record(
        self,
        *,
        source: str,
        channel: str,
        source_text: str,
        translated_text: str,
        origin_wall_clock_ms: int | None = None,
    ) -> None:
        cleaned_source = source_text.strip()
        cleaned_translation = translated_text.strip()
        if not cleaned_source or not cleaned_translation:
            return

        self._conversation_model.append(
            ConversationRecord(
                timestamp_label=_format_conversation_timestamp(origin_wall_clock_ms),
                source=source.strip() or "Mic",
                channel=channel,
                source_text=cleaned_source,
                translated_text=cleaned_translation,
            )
        )
        if self._showing_conversation:
            self._render_conversation_text()
            if self.page and self._log_text is not None:
                self._log_text.update()

    def _schedule_log_append(self, record: str) -> bool:
        page = self.page
        loop = getattr(page, "loop", None) if page is not None else None
        if loop is None:
            return False

        try:
            running_loop = asyncio.get_running_loop()
        except RuntimeError:
            running_loop = None

        if running_loop is loop:
            return False

        coroutine = self._append_log_on_page_loop(record)
        try:
            asyncio.run_coroutine_threadsafe(coroutine, loop)
        except RuntimeError:
            coroutine.close()
            return False
        return True

    async def _append_log_on_page_loop(self, record: str) -> None:
        self.append_log(record)

    def _buffer_pending_log(self, record: str) -> None:
        self._model.append(record)
        self._pending_update = True

    def _flush_logs(self):
        """Flush pending logs to the UI."""
        if self._log_text is None:
            return

        if self._showing_conversation:
            self._rendered_line_count = len(self._log_buffer)
            self._last_cleanup_count = self._model.cleanup_count
            self._last_update = time.time()
            self._pending_update = False
            return

        cleanup_changed = self._model.cleanup_count != self._last_cleanup_count
        rendered_ahead = self._rendered_line_count > len(self._log_buffer)

        if cleanup_changed or rendered_ahead:
            self._rebuild_visible_text()
        else:
            new_lines = self._log_buffer[self._rendered_line_count :]
            self._append_visible_text(new_lines)

        self._last_update = time.time()
        self._pending_update = False

        if self.page:
            self._log_text.update()

    def _rebuild_visible_text(self) -> None:
        assert self._log_text is not None
        self._log_text.value = "\n".join(self._log_buffer)
        self._rendered_line_count = len(self._log_buffer)
        self._last_cleanup_count = self._model.cleanup_count

    def _append_visible_text(self, new_lines: list[str]) -> None:
        assert self._log_text is not None
        if not new_lines:
            return
        addition = "\n".join(new_lines)
        if self._log_text.value:
            self._log_text.value = f"{self._log_text.value}\n{addition}"
        else:
            self._log_text.value = addition
        self._rendered_line_count = len(self._log_buffer)
        self._last_cleanup_count = self._model.cleanup_count

    def _render_conversation_text(self) -> None:
        assert self._log_text is not None
        self._log_text.value = self._conversation_model.render()

    def _conversation_button_label(self) -> str:
        key = "logs.conversation.hide" if self._showing_conversation else "logs.conversation.show"
        return t(key)

    def _on_conversation_button_click(self, _e: ft.ControlEvent | object) -> None:
        self._showing_conversation = not self._showing_conversation
        if self._showing_conversation:
            self._set_api_view(False)
        if self._conversation_button is not None:
            self._conversation_button.text = self._conversation_button_label()
        if self._showing_conversation:
            self._render_conversation_text()
        else:
            self._rebuild_visible_text()
        if self.page:
            self.update()

    # --- API Requests view ---

    def append_api_request(self, entry: dict) -> None:
        """Structured capture of an outbound request (bridge: API_REQUEST)."""
        record = dict(entry)
        record.setdefault("ts", time.strftime("%H:%M:%S"))
        self._api_request_model.append(record)
        if self._showing_api_requests:
            self._render_api_requests_text()
            if self.page and self._log_text is not None:
                self._log_text.update()

    def append_api_response(self, provider: str, text: str) -> None:
        """Result (or error) of a hand-sent composer request."""
        self._api_request_model.append({
            "ts": time.strftime("%H:%M:%S"),
            "provider": provider,
            "stage": "response",
            "text": text,
        })
        if self._showing_api_requests:
            self._render_api_requests_text()
            if self.page and self._log_text is not None:
                self._log_text.update()

    def _render_api_requests_text(self) -> None:
        assert self._log_text is not None
        if not self._api_request_model:
            self._log_text.value = t("logs.api.empty")
            return
        blocks: list[str] = []
        for e in self._api_request_model:
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
        self._log_text.value = ("\n" + "─" * 60 + "\n").join(blocks)

    def _api_button_label(self) -> str:
        key = "logs.api.hide" if self._showing_api_requests else "logs.api.show"
        return t(key)

    def _set_api_view(self, showing: bool) -> None:
        self._showing_api_requests = showing
        if self._api_button is not None:
            self._api_button.text = self._api_button_label()
        if self._api_composer is not None:
            self._api_composer.visible = showing

    def _on_api_button_click(self, _e: ft.ControlEvent | object) -> None:
        entering = not self._showing_api_requests
        self._set_api_view(entering)
        if entering:
            self._showing_conversation = False
            if self._conversation_button is not None:
                self._conversation_button.text = self._conversation_button_label()
            # Prefill the composer from the newest captured LLM request so the
            # user can tweak the real prompt rather than start from scratch.
            if self._api_prompt_field is not None and not self._api_prompt_field.value:
                for e in reversed(self._api_request_model):
                    if e.get("prompt_sent") and e.get("system_prompt"):
                        self._api_prompt_field.value = e["system_prompt"]
                        break
            self._render_api_requests_text()
        else:
            self._rebuild_visible_text()
        if self.page:
            self.update()

    def _on_api_send_click(self, _e: ft.ControlEvent | object) -> None:
        prompt = (self._api_prompt_field.value or "") if self._api_prompt_field else ""
        text = (self._api_text_field.value or "") if self._api_text_field else ""
        if not text.strip():
            return
        if callable(self.on_send_custom_request):
            self.on_send_custom_request(prompt, text)

    def apply_locale(self) -> None:
        """Refresh UI text when locale changes."""
        font_family = font_for_language(get_locale())
        if self._title_text:
            self._title_text.value = t("logs.title")
        if self._folder_button:
            self._folder_button.text = t("logs.open_folder")
            self._folder_button.style = self._get_button_style(font_family)
        if self._mode_button:
            self._mode_button.text = self._mode_button_label()
            self._mode_button.style = self._get_button_style(font_family)
        if self._conversation_button:
            self._conversation_button.text = self._conversation_button_label()
            self._conversation_button.style = self._get_button_style(font_family)
        if self._api_button:
            self._api_button.text = self._api_button_label()
            self._api_button.style = self._get_button_style(font_family)
        if self._api_prompt_field:
            self._api_prompt_field.label = t("logs.api.prompt")
        if self._api_text_field:
            self._api_text_field.label = t("logs.api.text")
        if self._api_send_button:
            self._api_send_button.text = t("logs.api.send")
            self._api_send_button.style = self._get_button_style(font_family)
        if self._showing_conversation and self._log_text is not None:
            self._render_conversation_text()
        if self._showing_api_requests and self._log_text is not None:
            self._render_api_requests_text()
        # Only update if added to page
        if self.page:
            self.update()

    @property
    def runtime_logging_mode(self) -> str:
        return self._runtime_logging_mode

    def set_runtime_logging_mode(self, mode: str) -> None:
        self._runtime_logging_mode = self._normalize_mode(mode)
        if self._mode_button is not None:
            self._mode_button.text = self._mode_button_label()
        if self.page:
            self.update()

    def _normalize_mode(self, mode: str) -> str:
        normalized = str(getattr(mode, "value", mode)).lower()
        if normalized not in {_BASIC_MODE, _DETAILED_MODE}:
            raise ValueError(f"Unsupported runtime logging mode: {mode}")
        return normalized

    def _mode_button_label(self) -> str:
        return t(f"logs.mode.{self._runtime_logging_mode}")

    def _on_mode_button_click(self, _e: ft.ControlEvent | object) -> None:
        next_mode = _DETAILED_MODE if self._runtime_logging_mode == _BASIC_MODE else _BASIC_MODE
        self.set_runtime_logging_mode(next_mode)
        if callable(self.on_mode_change):
            self.on_mode_change(next_mode)

    async def scroll_to_bottom(self) -> None:
        """Scroll to the latest log entry."""
        if self._log_scroll and self.page:
            if self._pending_update:
                self._flush_logs()
            result = self._log_scroll.scroll_to(offset=-1, duration=0)
            if inspect.isawaitable(result):
                await result

    def _open_log_folder(self, _):
        """Open the log folder in the system file explorer."""
        log_dir = _get_log_dir()
        if sys.platform == "win32":
            subprocess.Popen(["explorer", str(log_dir)])
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(log_dir)])
        else:
            subprocess.Popen(["xdg-open", str(log_dir)])
