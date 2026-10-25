from __future__ import annotations

import logging
import textwrap
from dataclasses import dataclass

from puripuly_heart.core.clock import Clock
from puripuly_heart.core.osc.sender import OscSender
from puripuly_heart.core.runtime_logging import SessionRuntimeLoggingService
from puripuly_heart.domain.models import OSCMessage

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class ChatboxPaginator:
    sender: OscSender
    clock: Clock
    max_chars: int = 144
    page_interval_s: float = 3.0
    runtime_logging: SessionRuntimeLoggingService | None = None
    _pending_pages: list[str] | None = None
    _pending_messages: list[OSCMessage] | None = None
    _next_page_at: float = 0.0
    # VRChat drops chatbox updates that arrive too close together. A queued
    # message right after a live preview waits this long so it is not lost.
    preview_gap_s: float = 1.5
    _last_preview_at: float | None = None

    def __post_init__(self) -> None:
        if self.max_chars <= 0:
            raise ValueError("max_chars must be > 0")
        if self.page_interval_s <= 0:
            raise ValueError("page_interval_s must be > 0")
        self._pending_pages = []
        self._pending_messages = []

    def enqueue(self, message: OSCMessage) -> None:
        if self._is_paginating() or self._preview_hold_active():
            self._pending_messages.append(message)
            return
        self._start_message(message)

    def _preview_hold_active(self) -> bool:
        if self._last_preview_at is None:
            return False
        return self.clock.now() - self._last_preview_at < self.preview_gap_s

    def is_busy(self) -> bool:
        """Pages or messages still waiting - a preview must not jump them."""
        return self._is_paginating() or bool(self._pending_messages)

    def send_preview(self, text: str) -> bool:
        """Show an in-progress translation now (one page, never queued).

        Skipped while a real message is paginating or waiting, so a preview
        never interrupts or delays finished text.
        """
        text = text.strip()
        if not text or self.is_busy():
            return False
        if len(text) > self.max_chars:
            # keep the newest words: the tail is what is being said right now
            text = "..." + text[-(self.max_chars - 3):].lstrip()
        sent = self._send_page(mode="preview", text=text, remaining_parts=0)
        if sent:
            self._last_preview_at = self.clock.now()
        return sent

    def process_due(self) -> None:
        if not self._is_paginating():
            if not self._preview_hold_active():
                self._drain_pending_messages()
            return

        now = self.clock.now()
        if now < self._next_page_at:
            return

        page = self._pending_pages.pop(0)
        remaining_parts = len(self._pending_pages)
        self._send_page(mode="queued", text=page, remaining_parts=remaining_parts)

        if self._pending_pages:
            self._next_page_at = now + self.page_interval_s
            return

        self._next_page_at = 0.0
        self._drain_pending_messages()

    def send_immediate(self, text: str) -> bool:
        """Send a single chatbox packet immediately without changing pagination state."""
        text = text.strip()
        if not text:
            return False
        return self._send_page(mode="immediate", text=text, remaining_parts=0)

    def send_typing(self, is_typing: bool) -> None:
        """Forward typing indicator to the OSC sender."""
        try:
            self.sender.send_typing(is_typing)
        except OSError as exc:
            self._emit_basic(
                f"[Basic][OSC] typing status=failed error={exc}", level=logging.WARNING
            )

    def _is_paginating(self) -> bool:
        return bool(self._pending_pages)

    def _start_message(self, message: OSCMessage) -> None:
        text = message.text.strip()
        if not text:
            return

        parts = self._split_text(text)
        head = parts[0]
        tail = parts[1:]
        self._send_page(mode="queued", text=head, remaining_parts=len(tail))

        if tail:
            self._pending_pages.extend(tail)
            self._next_page_at = self.clock.now() + self.page_interval_s

    def _drain_pending_messages(self) -> None:
        while self._pending_messages and not self._is_paginating():
            next_message = self._pending_messages.pop(0)
            self._start_message(next_message)

    def _split_text(self, text: str) -> list[str]:
        if len(text) <= self.max_chars:
            return [text]
        return textwrap.wrap(
            text,
            width=self.max_chars,
            break_long_words=True,
            break_on_hyphens=False,
        )

    def _send_page(self, *, mode: str, text: str, remaining_parts: int) -> bool:
        self._emit_send_attempt(mode=mode, text=text, remaining_parts=remaining_parts)
        try:
            self.sender.send_chatbox(text)
        except OSError as exc:
            self._emit_send_failure(mode=mode, exc=exc)
            return False
        self._emit_send_delivered(mode=mode, text=text, remaining_parts=remaining_parts)
        return True

    def _emit_send_attempt(self, *, mode: str, text: str, remaining_parts: int) -> None:
        self._emit_detailed(
            f"[Detailed][OSC] send mode={mode} status=attempt chars={len(text)} "
            f"remaining_parts={remaining_parts} text={text!r}"
        )

    def _emit_send_delivered(self, *, mode: str, text: str, remaining_parts: int) -> None:
        self._emit_basic(
            f"[Basic][OSC] send mode={mode} status=delivered chars={len(text)} "
            f"remaining_parts={remaining_parts}"
        )

    def _emit_send_failure(self, *, mode: str, exc: OSError) -> None:
        self._emit_basic(
            f"[Basic][OSC] send mode={mode} status=failed error={exc}",
            level=logging.WARNING,
        )

    def _emit_basic(self, message: str, *, level: int = logging.INFO) -> None:
        if self.runtime_logging is not None:
            self.runtime_logging.emit_basic(message, level=level)
            return
        logger.log(level, message)

    def _emit_detailed(self, message: str, *, level: int = logging.INFO) -> None:
        if self.runtime_logging is not None:
            self.runtime_logging.emit_detailed(message, level=level)
            return
        logger.debug(message)
