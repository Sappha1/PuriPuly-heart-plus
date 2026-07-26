"""Throttled, language-aware peer-filter notice (r286)."""
from __future__ import annotations

import pytest

from puripuly_heart.core.clock import FakeClock
from puripuly_heart.core.orchestrator.hub import ClientHub
from puripuly_heart.domain.events import UIEventType
from tests.helpers.fakes import RecordingOscQueue


def _drain_error_events(hub: ClientHub) -> list[str]:
    out = []
    while not hub.ui_events.empty():
        ev = hub.ui_events.get_nowait()
        if ev.type == UIEventType.ERROR:
            out.append(str(ev.payload))
    return out


@pytest.mark.asyncio
async def test_filter_notice_repeats_after_throttle_and_names_language() -> None:
    clock = FakeClock(_now=1000.0)
    hub = ClientHub(stt=None, llm=None, osc=RecordingOscQueue(), clock=clock)
    hub.peer_source_language = "zh-CN"

    await hub._maybe_notify_peer_language_filtered("You hear that boomer.")
    first = _drain_error_events(hub)
    assert len(first) == 1
    assert "English" in first[0]  # names the dropped language

    # Within the throttle window: silent.
    clock._now += 60.0
    await hub._maybe_notify_peer_language_filtered("Another English line.")
    assert _drain_error_events(hub) == []

    # After the window: repeats (one-time-per-session was not enough).
    clock._now += 300.0
    await hub._maybe_notify_peer_language_filtered("Third English line.")
    assert len(_drain_error_events(hub)) == 1
