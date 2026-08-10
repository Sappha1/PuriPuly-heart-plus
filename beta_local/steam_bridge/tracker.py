# -*- coding: utf-8 -*-
"""Pure message-tracking logic for the Steam bridge — no browser, fully testable.

The browser side hands this a fresh snapshot of the visible conversation on each
poll: an ordered list of (speaker, text) pairs read from the stable Steam DOM
classes (ChatMessageBlock / speakerName / msg — the ones the probe confirmed
carry no build hash). This decides which of them are genuinely NEW inbound
messages worth translating and showing, and — critically — never lets the
bridge's own sent messages come back around as if the friend had said them.

Two independent guards against that echo loop, either sufficient alone:
  1. own_name: messages whose speaker is us are never inbound.
  2. a recent-sent buffer: a message matching something we just sent is
     swallowed once, even if we could not identify our own name.

Deliberately conservative: on the very first snapshot it emits nothing (the
existing scrollback is history, not new arrivals), and it finds new messages as
the tail past the previous snapshot's overlap, so ordinary repeats ("ok" / "ok")
are handled correctly rather than deduped away.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass


@dataclass(frozen=True)
class Msg:
    speaker: str
    text: str


class MessageTracker:
    def __init__(self, own_name: str = "", sent_memory: int = 20) -> None:
        # own_name may be empty if we could not read it; the sent-buffer still
        # protects against echo in that case.
        self._own = (own_name or "").strip()
        self._prev: list[Msg] = []
        self._primed = False
        self._recent_sent: deque[str] = deque(maxlen=sent_memory)

    def set_own_name(self, name: str) -> None:
        self._own = (name or "").strip()

    def note_sent(self, text: str) -> None:
        """Record a message we just sent, so its DOM echo is not re-emitted."""
        t = (text or "").strip()
        if t:
            self._recent_sent.append(t)

    def _is_own(self, msg: Msg) -> bool:
        if self._own and msg.speaker.strip() == self._own:
            return True
        return False

    def _consume_echo(self, msg: Msg) -> bool:
        """True (and consumes one) if this looks like our own just-sent text."""
        t = msg.text.strip()
        if t in self._recent_sent:
            self._recent_sent.remove(t)  # consume a single occurrence
            return True
        return False

    @staticmethod
    def _tail_after_overlap(prev: list[Msg], current: list[Msg]) -> list[Msg]:
        """Everything in `current` after the point that `prev` last reached.

        Steam only keeps the last N blocks in the DOM, so `current` is a sliding
        window. The new arrivals are the suffix of `current` beyond where `prev`
        ended. We locate prev's final element inside current and take what
        follows; if prev has scrolled entirely out of view, the whole snapshot is
        treated as new (bounded by the window size, so at worst one burst).
        """
        if not prev:
            return list(current)
        # Largest k where the last k of prev equal the first k of current: the
        # maximal overlap, i.e. the fewest "new" messages consistent with the
        # snapshots. This is what makes consecutive identical messages
        # ("ok"/"ok") come out right, where anchor-searching cannot.
        limit = min(len(prev), len(current))
        for k in range(limit, 0, -1):
            if prev[len(prev) - k :] == current[:k]:
                return list(current[k:])
        return list(current)

    def poll(self, current: list[Msg]) -> list[Msg]:
        """Given the current visible messages, return new inbound ones to show."""
        if not self._primed:
            self._primed = True
            self._prev = list(current)
            return []

        fresh = self._tail_after_overlap(self._prev, current)
        self._prev = list(current)

        out: list[Msg] = []
        for msg in fresh:
            if not msg.text.strip():
                continue
            if self._is_own(msg):
                continue
            if self._consume_echo(msg):
                continue
            out.append(msg)
        return out
