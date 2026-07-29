"""Peer language filter only applies while translating (r309)."""
from __future__ import annotations

from uuid import uuid4

import pytest

from puripuly_heart.core.orchestrator.hub import ClientHub
from puripuly_heart.core.stt.controller import STTFinalEvent
from puripuly_heart.domain.events import UIEventType
from puripuly_heart.domain.models import Transcript
from tests.helpers.fakes import RecordingOscQueue


class _StubLLM:
    """Minimal stand-in: presence of an LLM is what enables translation."""

    async def translate(self, *a, **kw):  # pragma: no cover - not exercised
        return ""


async def _feed(hub: ClientHub, text: str) -> None:
    uid = uuid4()
    await hub._handle_stt_event(
        STTFinalEvent(
            utterance_id=uid,
            transcript=Transcript(utterance_id=uid, text=text, is_final=True,
                                  created_at=hub.clock.now(), channel="peer"),
        )
    )


def _seen(hub: ClientHub) -> list[str]:
    out = []
    while not hub.ui_events.empty():
        ev = hub.ui_events.get_nowait()
        if ev.type == UIEventType.TRANSCRIPT_FINAL:
            out.append(ev.payload.text)
    return out


@pytest.mark.asyncio
async def test_trans_off_shows_every_language() -> None:
    hub = ClientHub(stt=None, llm=None, osc=RecordingOscQueue(),
                    source_language="en", target_language="zh-CN")
    hub.translation_enabled = False          # TRANS off
    hub.peer_source_language = "zh-CN"       # pinned to Chinese
    await _feed(hub, "hello friends this is english")
    assert "hello friends this is english" in _seen(hub)


@pytest.mark.asyncio
async def test_translating_still_filters_wrong_language() -> None:
    hub = ClientHub(stt=None, llm=_StubLLM(), osc=RecordingOscQueue(),
                    source_language="en", target_language="zh-CN",
                    peer_translation_enabled=True)
    hub.translation_enabled = True
    hub.peer_source_language = "ko"          # expecting Korean
    await _feed(hub, "你好朋友我们去吃饭吧")     # clearly Chinese
    assert _seen(hub) == []                  # filtered, as before


@pytest.mark.asyncio
async def test_matching_language_passes_while_translating() -> None:
    hub = ClientHub(stt=None, llm=_StubLLM(), osc=RecordingOscQueue(),
                    source_language="en", target_language="zh-CN",
                    peer_translation_enabled=True)
    hub.translation_enabled = True
    hub.peer_source_language = "zh-CN"
    await _feed(hub, "你好朋友我们去吃饭吧")
    assert "你好朋友我们去吃饭吧" in _seen(hub)
