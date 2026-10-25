"""Live chatbox preview: SELF partial transcripts are translated and shown in
the VRChat chatbox while the user is still talking; the final replaces them."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from uuid import uuid4

import numpy as np
import pytest

from puripuly_heart.core.clock import FakeClock
from puripuly_heart.core.orchestrator.hub import ClientHub
from puripuly_heart.core.osc.chatbox_paginator import ChatboxPaginator
from puripuly_heart.domain.models import OSCMessage, Transcript, Translation
from puripuly_heart.providers.stt.local_qwen_sherpa import _LocalQwenSherpaSession


# ── paginator ────────────────────────────────────────────────────────────────


@dataclass
class RecordingSender:
    sent: list[str] = field(default_factory=list)

    def send_chatbox(self, text: str) -> None:
        self.sent.append(text)

    def send_typing(self, is_typing: bool) -> None:
        pass


def _paginator(clock: FakeClock) -> tuple[ChatboxPaginator, RecordingSender]:
    sender = RecordingSender()
    return ChatboxPaginator(sender=sender, clock=clock, max_chars=20, page_interval_s=3.0), sender


def test_preview_is_sent_immediately_as_one_page_keeping_the_newest_words() -> None:
    clock = FakeClock()
    pag, sender = _paginator(clock)

    assert pag.send_preview("one two three four five six seven") is True

    assert len(sender.sent) == 1
    assert len(sender.sent[0]) <= 20
    assert sender.sent[0].startswith("...")
    assert sender.sent[0].endswith("seven")


def test_preview_never_jumps_a_message_that_is_still_paginating() -> None:
    clock = FakeClock()
    pag, sender = _paginator(clock)
    pag.enqueue(OSCMessage(uuid4(), "a" * 30, created_at=0.0))  # 2 pages, 1 waiting

    assert pag.send_preview("preview") is False
    assert sender.sent == ["a" * 20]


def test_a_finished_message_right_after_a_preview_waits_out_the_gap() -> None:
    clock = FakeClock()
    pag, sender = _paginator(clock)
    pag.send_preview("hello wor")
    clock.advance(0.3)

    pag.enqueue(OSCMessage(uuid4(), "hello world", created_at=0.3))
    assert sender.sent == ["hello wor"]  # held: VRChat would drop it

    clock.advance(1.0)
    pag.process_due()
    assert sender.sent == ["hello wor"]

    clock.advance(0.3)  # 1.6 s after the preview
    pag.process_due()
    assert sender.sent == ["hello wor", "hello world"]


# ── local speech session: rolling partial decodes ────────────────────────────


class FakeBackend:
    sample_rate_hz = 16000
    stream_label = "self"
    speaker_embedder = None
    diagnostics_enabled = None
    partial_interval_s = 1.0
    partial_min_audio_s = 1.0

    def __init__(self, wanted: bool = True) -> None:
        self.wanted = wanted
        self.decodes: list[tuple[int, bool]] = []
        self.release = asyncio.Event()
        self.release.set()

    def partial_results_wanted(self) -> bool:
        return self.wanted

    async def decode_f32(self, samples, *, partial: bool = False):
        self.decodes.append((int(samples.size), partial))
        await self.release.wait()
        return ("partial text" if partial else "final text"), "en"


def _second() -> np.ndarray:
    return np.full(16000, 0.1, np.float32)


async def _drain(session, n: int) -> list:
    out = []
    for _ in range(n):
        out.append(await asyncio.wait_for(session._events.get(), 1.0))
    return out


@pytest.mark.asyncio
async def test_long_speech_produces_a_partial_before_it_ends() -> None:
    backend = FakeBackend()
    session = _LocalQwenSherpaSession(backend=backend)

    await session.send_audio_f32(_second()[:8000])
    assert backend.decodes == []  # under the minimum length
    await session.send_audio_f32(_second()[:8000])
    await asyncio.sleep(0)
    await session._partial_task

    (event,) = await _drain(session, 1)
    assert event.is_final is False
    assert event.text == "partial text"
    assert backend.decodes == [(16000, True)]


@pytest.mark.asyncio
async def test_a_partial_finishing_after_speech_end_is_dropped() -> None:
    backend = FakeBackend()
    backend.release.clear()
    session = _LocalQwenSherpaSession(backend=backend)
    await session.send_audio_f32(_second())
    partial_task = session._partial_task
    assert partial_task is not None

    end = asyncio.create_task(session.on_speech_end())
    await asyncio.sleep(0)
    backend.release.set()
    await end
    await partial_task

    events = await _drain(session, 1)
    assert [e.is_final for e in events] == [True]
    assert session._events.empty()


@pytest.mark.asyncio
async def test_no_partials_when_the_preview_is_off() -> None:
    backend = FakeBackend(wanted=False)
    session = _LocalQwenSherpaSession(backend=backend)

    await session.send_audio_f32(_second())
    await session.send_audio_f32(_second())

    assert session._partial_task is None
    assert backend.decodes == []


# ── hub ──────────────────────────────────────────────────────────────────────


@dataclass
class FakeLLM:
    calls: list[str] = field(default_factory=list)

    async def translate(self, *, utterance_id, text, system_prompt, source_language,
                        target_language, context=""):
        self.calls.append(text)
        return Translation(utterance_id=utterance_id, text=f"T({text})")

    async def close(self) -> None:
        pass


@dataclass
class PreviewOsc:
    previews: list[str] = field(default_factory=list)
    messages: list = field(default_factory=list)
    accept: bool = True

    def enqueue(self, msg) -> None:
        self.messages.append(msg)

    def send_preview(self, text: str) -> bool:
        if self.accept:
            self.previews.append(text)
        return self.accept

    def send_typing(self, on: bool) -> None:
        pass

    def process_due(self) -> None:
        pass


def _hub(**kwargs) -> tuple[ClientHub, FakeLLM, PreviewOsc, FakeClock]:
    llm, osc, clock = FakeLLM(), PreviewOsc(), FakeClock(_now=100.0)
    hub = ClientHub(
        stt=None,
        llm=llm,
        osc=osc,
        clock=clock,
        source_language="en",
        target_language="ko",
        chatbox_include_source=False,
        **kwargs,
    )
    hub.translation_enabled = True
    return hub, llm, osc, clock


def _partial(utterance_id, text: str) -> Transcript:
    return Transcript(utterance_id=utterance_id, text=text, is_final=False, created_at=0.0,
                      channel="self")


@pytest.mark.asyncio
async def test_partial_is_translated_and_shown_with_a_marker() -> None:
    hub, llm, osc, _ = _hub()
    uid = uuid4()

    hub._maybe_preview_self_partial(_partial(uid, "hello there"))
    await hub._chatbox_previews[uid].task

    assert llm.calls == ["hello there"]
    assert osc.previews == ["T(hello there) ..."]
    assert osc.messages == []  # nothing queued as a finished message


@pytest.mark.asyncio
async def test_previews_are_spaced_out_and_only_the_newest_text_is_sent() -> None:
    hub, llm, osc, clock = _hub()
    uid = uuid4()
    hub._maybe_preview_self_partial(_partial(uid, "hello there"))
    await hub._chatbox_previews[uid].task

    hub._maybe_preview_self_partial(_partial(uid, "hello there my"))
    hub._maybe_preview_self_partial(_partial(uid, "hello there my friend"))
    task = hub._chatbox_previews[uid].task
    await asyncio.sleep(0.01)
    assert len(osc.previews) == 1  # waiting out the interval

    clock.advance(hub._PREVIEW_MIN_INTERVAL_S)
    await asyncio.wait_for(task, 2.0)
    assert llm.calls == ["hello there", "hello there my friend"]
    assert osc.previews[-1] == "T(hello there my friend) ..."


@pytest.mark.asyncio
async def test_the_final_closes_the_preview_and_drops_a_late_result() -> None:
    hub, llm, osc, clock = _hub()
    uid = uuid4()
    hub._last_preview_send_at = clock.now()  # next preview has to wait
    hub._maybe_preview_self_partial(_partial(uid, "hello there"))
    task = hub._chatbox_previews[uid].task

    hub._close_chatbox_preview(uid)
    await asyncio.gather(task, return_exceptions=True)

    assert uid not in hub._chatbox_previews
    assert osc.previews == []
    assert llm.calls == []


@pytest.mark.asyncio
async def test_no_preview_when_off_or_translation_off_or_too_short() -> None:
    hub, llm, osc, _ = _hub(chatbox_live_preview=False)
    hub._maybe_preview_self_partial(_partial(uuid4(), "hello there"))

    hub2, llm2, _, _ = _hub()
    hub2.translation_enabled = False
    hub2._maybe_preview_self_partial(_partial(uuid4(), "hello there"))

    hub3, llm3, _, _ = _hub()
    hub3._maybe_preview_self_partial(_partial(uuid4(), "ok"))

    await asyncio.sleep(0)
    assert hub._chatbox_previews == {} and hub2._chatbox_previews == {}
    assert hub3._chatbox_previews == {}
    assert llm.calls == llm2.calls == llm3.calls == []
