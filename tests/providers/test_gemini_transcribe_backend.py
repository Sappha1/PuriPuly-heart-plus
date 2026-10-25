from __future__ import annotations

import asyncio
import base64
import json
import sys
from types import SimpleNamespace

import pytest

from puripuly_heart.providers.stt.gemini_transcribe import (
    GeminiTranscribeSTTBackend,
    _GeminiTranscribeSession,
    gemini_transcribe_language_codes,
    join_transcript_pieces,
)

PLACEHOLDER_KEY = "placeholder-key"


class FakeWebSocket:
    """Answers setup, records what the session sends, plays back server messages."""

    def __init__(self, *, setup_ok: bool = True) -> None:
        self.sent: list[dict] = []
        self.incoming: asyncio.Queue = asyncio.Queue()
        self.close_code = None
        self.close_reason = ""
        self.closed = False
        if setup_ok:
            self.incoming.put_nowait(json.dumps({"setupComplete": {}}))
        else:
            self.close_code, self.close_reason = 1008, "API key not valid"
            self.incoming.put_nowait(None)

    async def send(self, payload: str) -> None:
        self.sent.append(json.loads(payload))

    def push(self, payload: dict) -> None:
        self.incoming.put_nowait(json.dumps(payload))

    def __aiter__(self):
        return self

    async def __anext__(self):
        item = await self.incoming.get()
        if item is None:
            raise StopAsyncIteration
        return item

    async def close(self) -> None:
        self.closed = True
        self.incoming.put_nowait(None)


@pytest.fixture
def fake_ws(monkeypatch):
    holder: dict = {}

    async def connect(url, **kwargs):
        holder["url"] = url
        holder["kwargs"] = kwargs
        holder["ws"] = holder.get("next") or FakeWebSocket()
        return holder["ws"]

    monkeypatch.setitem(sys.modules, "websockets", SimpleNamespace(connect=connect))
    return holder


def _backend(**kwargs) -> GeminiTranscribeSTTBackend:
    return GeminiTranscribeSTTBackend(
        api_key=PLACEHOLDER_KEY, settle_s=0.05, finalize_timeout_s=0.5, **kwargs
    )


async def _next_event(session, timeout: float = 1.0):
    return await asyncio.wait_for(session._events.get(), timeout)


def test_language_codes_map_to_bcp47_and_skip_auto() -> None:
    codes = gemini_transcribe_language_codes("en", "", "auto", "ja", "en")
    assert codes == ["en-US", "ja-JP"]
    assert gemini_transcribe_language_codes(None) == []


def test_pieces_join_with_spaces_except_around_cjk() -> None:
    assert join_transcript_pieces(["hello", " world "]) == "hello world"
    assert join_transcript_pieces(["你好", "世界"]) == "你好世界"
    assert join_transcript_pieces(["ok", "", "  "]) == "ok"


@pytest.mark.asyncio
async def test_setup_sends_model_languages_and_vocabulary(fake_ws) -> None:
    session = await _backend(language_codes=["ko-KR"], custom_vocabulary=["PuriPuly", " "]).open_session()
    try:
        setup = fake_ws["ws"].sent[0]["setup"]
        assert setup["model"] == "models/gemini-3.5-transcribe-live"
        assert setup["generationConfig"] == {"responseModalities": ["TEXT"]}
        assert setup["inputAudioTranscription"] == {
            "languageCodes": ["ko-KR"],
            "customVocabulary": ["PuriPuly"],
        }
        assert fake_ws["url"].endswith(f"?key={PLACEHOLDER_KEY}")
    finally:
        await session.close()


@pytest.mark.asyncio
async def test_a_rejected_key_fails_the_open_with_the_reason(fake_ws) -> None:
    fake_ws["next"] = FakeWebSocket(setup_ok=False)

    with pytest.raises(RuntimeError, match="API key not valid"):
        await _backend().open_session()


@pytest.mark.asyncio
async def test_audio_goes_out_as_base64_pcm_and_speech_end_closes_the_stream(fake_ws) -> None:
    session = await _backend().open_session()
    try:
        await session.send_audio(b"\x01\x00" * 1600)  # exactly one 100 ms chunk
        await session.on_speech_end()
        await asyncio.sleep(0.05)
        sent = fake_ws["ws"].sent[1:]
        audio = sent[0]["realtimeInput"]["audio"]
        assert audio["mimeType"] == "audio/pcm;rate=16000"
        assert base64.b64decode(audio["data"]) == b"\x01\x00" * 1600
        assert sent[-1] == {"realtimeInput": {"audioStreamEnd": True}}
    finally:
        await session.close()


@pytest.mark.asyncio
async def test_interim_text_is_a_partial_and_finals_become_one_final(fake_ws) -> None:
    session = await _backend().open_session()
    ws = fake_ws["ws"]
    try:
        ws.push({"serverContent": {"interimInputTranscription": {"text": "hello"}}})
        partial = await _next_event(session)
        assert (partial.text, partial.is_final) == ("hello", False)

        ws.push({"serverContent": {"inputTranscription": {"text": "hello there"}}})
        ws.push({"serverContent": {"interimInputTranscription": {"text": "my friend"}}})
        partials = [await _next_event(session), await _next_event(session)]
        assert [p.text for p in partials] == ["hello there", "hello there my friend"]

        await session.on_speech_end()
        await asyncio.sleep(0.02)
        ws.push({"serverContent": {"inputTranscription": {"text": "my friend"}}})
        events = []
        while True:
            event = await _next_event(session)
            events.append(event)
            if event.is_final:
                break
        assert events[-1].text == "hello there my friend"
    finally:
        await session.close()


@pytest.mark.asyncio
async def test_without_a_server_final_the_last_interim_is_used(fake_ws) -> None:
    session = await _backend().open_session()
    ws = fake_ws["ws"]
    try:
        ws.push({"serverContent": {"interimInputTranscription": {"text": "almost done"}}})
        await _next_event(session)
        await session.on_speech_end()
        final = await _next_event(session)
        assert (final.text, final.is_final) == ("almost done", True)
    finally:
        await session.close()


@pytest.mark.asyncio
async def test_an_unexpected_server_close_surfaces_as_an_error(fake_ws) -> None:
    session = await _backend().open_session()
    ws = fake_ws["ws"]
    ws.close_code, ws.close_reason = 1011, "internal"
    ws.incoming.put_nowait(None)

    with pytest.raises(RuntimeError, match="closed"):
        async for _ in session.events():
            pass
    await session.close()


@pytest.mark.asyncio
async def test_verify_checks_the_key_can_see_the_model(monkeypatch) -> None:
    from puripuly_heart.providers.llm import gemini

    calls = []

    async def fake_verify(api_key, *, model):
        calls.append((api_key, model))
        return True

    monkeypatch.setattr(gemini.GeminiLLMProvider, "verify_api_key", staticmethod(fake_verify))

    assert await GeminiTranscribeSTTBackend.verify_api_key(PLACEHOLDER_KEY) is True
    assert calls == [(PLACEHOLDER_KEY, "gemini-3.5-transcribe-live")]


def test_session_setup_omits_empty_vocabulary() -> None:
    session = _GeminiTranscribeSession(
        api_key=PLACEHOLDER_KEY,
        model="gemini-3.5-transcribe-live",
        endpoint="wss://example.invalid",
        sample_rate_hz=16000,
        language_codes=[],
        custom_vocabulary=[],
        connect_timeout_s=1.0,
        finalize_timeout_s=1.0,
        settle_s=0.1,
    )
    assert session.setup_message()["setup"]["inputAudioTranscription"] == {"languageCodes": []}
