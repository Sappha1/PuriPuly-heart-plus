"""Auto-detect 'ignore my language' (r295)."""
from __future__ import annotations

from uuid import uuid4

import pytest

from puripuly_heart.core.orchestrator.hub import ClientHub
from puripuly_heart.core.stt.controller import STTFinalEvent
from puripuly_heart.domain.events import UIEventType
from puripuly_heart.domain.models import Transcript
from tests.helpers.fakes import RecordingOscQueue


async def _feed_peer_final(hub: ClientHub, text: str) -> None:
    uid = uuid4()
    await hub._handle_stt_event(
        STTFinalEvent(
            utterance_id=uid,
            transcript=Transcript(
                utterance_id=uid, text=text, is_final=True,
                created_at=hub.clock.now(), channel="peer",
            ),
        )
    )


def _texts_reaching_ui(hub: ClientHub) -> list[str]:
    out = []
    while not hub.ui_events.empty():
        ev = hub.ui_events.get_nowait()
        if ev.type == UIEventType.TRANSCRIPT_FINAL:
            out.append(ev.payload.text)
    return out


def _make_hub(**kw) -> ClientHub:
    hub = ClientHub(stt=None, llm=None, osc=RecordingOscQueue(),
                    source_language="en", target_language="zh-CN")
    hub.translation_enabled = False
    hub.peer_source_language = ""  # auto-detect
    for k, v in kw.items():
        setattr(hub, k, v)
    return hub


@pytest.mark.asyncio
async def test_ignore_own_drops_own_language_keeps_others() -> None:
    hub = _make_hub(auto_detect_ignore_own=True)
    await _feed_peer_final(hub, "my own english voice")
    await _feed_peer_final(hub, "你好朋友")
    texts = _texts_reaching_ui(hub)
    assert "my own english voice" not in texts
    assert "你好朋友" in texts


@pytest.mark.asyncio
async def test_ignore_own_off_keeps_everything() -> None:
    hub = _make_hub(auto_detect_ignore_own=False)
    await _feed_peer_final(hub, "my own english voice")
    assert "my own english voice" in _texts_reaching_ui(hub)


@pytest.mark.asyncio
async def test_ignore_own_inert_with_pinned_peer_language() -> None:
    hub = _make_hub(auto_detect_ignore_own=True)
    hub.peer_source_language = "en"  # pinned, not auto-detect
    await _feed_peer_final(hub, "pinned english passes")
    assert "pinned english passes" in _texts_reaching_ui(hub)
