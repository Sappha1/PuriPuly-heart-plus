"""Ctrl+Z right after sending puts the whole sent message back in the input.

Sending clears the message box programmatically. Flutter's text undo history
only takes a snapshot of a field about every 500 ms while typing, so its own
Ctrl+Z restores the last snapshot - the sent text minus the last few
characters typed before Enter. This remembers the exact sent text and, after
the native undo has run, replaces the partial value with the full message.

It never overwrites new typing: the restore only happens while the box is
empty or holds a leading part of the sent text, and only once per send.
"""

from __future__ import annotations

import asyncio
import contextlib
from typing import Any

# Native undo runs in the Flutter client; its value change reaches Python a
# few ms after the key event. Wait for it, then check again later in case a
# stale value change arrives after the restore.
_FIRST_CHECK_S = 0.12
_RECHECK_S = 0.25


class SentTextUndo:
    def __init__(self) -> None:
        self._text = ""

    @property
    def armed(self) -> bool:
        return bool(self._text)

    def remember(self, text: str) -> None:
        self._text = (text or "").strip()

    def forget(self) -> None:
        self._text = ""

    def restore_value(self, current: str | None) -> str | None:
        """The text to put back for the field's `current` value, or None.

        One-shot: once it has answered for a send it stays quiet until the
        next `remember`.
        """
        text = self._text
        if not text:
            return None
        value = (current or "").strip()
        if value == text:
            self._text = ""  # the native undo already got it right
            return None
        if text.startswith(value):
            self._text = ""
            return text
        return None  # something new was typed - leave it alone


async def restore_after_native_undo(field: Any, undo: SentTextUndo) -> bool:
    """Run on the UI loop after Ctrl+Z reached `field`'s view."""
    if not undo.armed:
        return False
    await asyncio.sleep(_FIRST_CHECK_S)
    restored = undo.restore_value(getattr(field, "value", ""))
    if restored is None:
        return False
    field.value = restored
    with contextlib.suppress(Exception):
        field.update()
    await asyncio.sleep(_RECHECK_S)
    late = (getattr(field, "value", "") or "").strip()
    if late != restored and restored.startswith(late):
        field.value = restored
        with contextlib.suppress(Exception):
            field.update()
    return True
