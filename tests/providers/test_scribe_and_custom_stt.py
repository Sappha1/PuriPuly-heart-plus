from __future__ import annotations

import asyncio
import base64
import io
import json
import sys
import wave
from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse

import pytest

from puripuly_heart.providers.stt import custom_whisper
from puripuly_heart.providers.stt.custom_whisper import (
    CustomWhisperSTTBackend,
    pcm16_to_wav,
    transcription_url,
)
from puripuly_heart.providers.stt.elevenlabs_scribe import (
    ElevenLabsScribeSTTBackend,
    scribe_language_code,
    scribe_url,
)

KEY = "placeholder-key"


# ── ElevenLabs Scribe ─────────────────────────────────────────────────────────


def test_url_uses_manual_commits_language_and_keyterms() -> None:
    url = scribe_url(language_code=scribe_language_code("ja-JP"), keyterms=["PuriPuly", " "])
    q = parse_qs(urlparse(url).query)
    assert q["model_id"] == ["scribe_v2_realtime"]
    assert q["commit_strategy"] == ["manual"]
    assert q["audio_format"] == ["pcm_16000"]
    assert q["language_code"] == ["ja"]
    assert q["keyterms"] == ["PuriPuly"]
    assert "language_code" not in parse_qs(urlparse(scribe_url()).query)


class _WS:
    def __init__(self) -> None:
        self.sent: list[dict] = []
        self.incoming: asyncio.Queue = asyncio.Queue()
        self.incoming.put_nowait(json.dumps({"message_type": "session_started", "session_id": "s"}))
        self.close_code = None
        self.close_reason = ""

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
        self.incoming.put_nowait(None)


@pytest.fixture
def scribe_ws(monkeypatch):
    holder: dict = {}

    async def connect(url, **kwargs):
        holder.update(url=url, kwargs=kwargs, ws=_WS())
        return holder["ws"]

    monkeypatch.setitem(sys.modules, "websockets", SimpleNamespace(connect=connect))
    return holder


async def _next(session, timeout=1.0):
    return await asyncio.wait_for(session._events.get(), timeout)


@pytest.mark.asyncio
async def test_scribe_key_header_partials_and_one_final_per_commit(scribe_ws) -> None:
    session = await ElevenLabsScribeSTTBackend(api_key=KEY, language="en").open_session()
    ws = scribe_ws["ws"]
    try:
        assert scribe_ws["kwargs"]["additional_headers"] == {"xi-api-key": KEY}

        await session.send_audio(b"\x00\x01" * 1600)
        await session.on_speech_end()
        await asyncio.sleep(0.05)
        chunks = [m for m in ws.sent if m["message_type"] == "input_audio_chunk"]
        assert base64.b64decode(chunks[0]["audio_base_64"]) == b"\x00\x01" * 1600
        assert chunks[-1]["commit"] is True

        ws.push({"message_type": "partial_transcript", "text": "hello"})
        partial = await _next(session)
        assert (partial.text, partial.is_final) == ("hello", False)

        ws.push({"message_type": "committed_transcript", "text": "hello there"})
        final = await _next(session)
        assert (final.text, final.is_final) == ("hello there", True)
    finally:
        await session.close()


@pytest.mark.asyncio
async def test_scribe_errors_surface(scribe_ws) -> None:
    session = await ElevenLabsScribeSTTBackend(api_key=KEY).open_session()
    scribe_ws["ws"].push({"message_type": "quota_exceeded", "error": "out of credits"})
    try:
        with pytest.raises(RuntimeError, match="quota_exceeded"):
            async for _ in session.events():
                pass
    finally:
        await session.close()


# ── your own speech server ───────────────────────────────────────────────────


def test_transcription_url_and_wav() -> None:
    assert transcription_url("http://127.0.0.1:8000/v1/") == "http://127.0.0.1:8000/v1/audio/transcriptions"
    assert transcription_url("https://x/v1/audio/transcriptions") == "https://x/v1/audio/transcriptions"
    with wave.open(io.BytesIO(pcm16_to_wav(b"\x00\x00" * 160, 16000))) as w:
        assert (w.getnchannels(), w.getframerate(), w.getnframes()) == (1, 16000, 160)


class _Resp:
    def __init__(self, status=200, data=None, text=""):
        self.status_code = status
        self._data = data or {}
        self.text = text

    def json(self):
        return self._data


@pytest.mark.asyncio
async def test_each_utterance_is_one_request_and_one_final(monkeypatch) -> None:
    posts: list = []

    class _Client:
        def __init__(self, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, **kwargs):
            posts.append((url, kwargs))
            return _Resp(data={"text": " good morning "})

    monkeypatch.setattr(custom_whisper.httpx, "AsyncClient", _Client)
    backend = CustomWhisperSTTBackend(base_url="http://127.0.0.1:8000/v1", model="small",
                                      language="en-US", prompt_terms=("PuriPuly",))
    session = await backend.open_session()

    await session.send_audio(b"\x00\x00" * 16000)  # 1 s
    await session.on_speech_end()
    final = await asyncio.wait_for(session._events.get(), 1.0)

    assert (final.text, final.is_final) == ("good morning", True)
    url, kwargs = posts[0]
    assert url.endswith("/audio/transcriptions")
    assert "headers" in kwargs and kwargs["headers"] == {}  # no key: no auth header
    assert kwargs["data"]["model"] == "small"
    assert kwargs["data"]["language"] == "en"
    assert kwargs["data"]["prompt"] == "PuriPuly"


@pytest.mark.asyncio
async def test_short_or_failed_utterances_still_answer_with_an_empty_final(monkeypatch) -> None:
    class _Client:
        def __init__(self, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, **kwargs):
            return _Resp(status=500, data={"error": {"message": "boom"}})

    monkeypatch.setattr(custom_whisper.httpx, "AsyncClient", _Client)
    session = await CustomWhisperSTTBackend().open_session()

    await session.send_audio(b"\x00\x00" * 100)  # too short to send
    await session.on_speech_end()
    await session.send_audio(b"\x00\x00" * 16000)
    await session.on_speech_end()  # server error

    a = await asyncio.wait_for(session._events.get(), 1.0)
    b = await asyncio.wait_for(session._events.get(), 1.0)
    assert (a.text, a.is_final, b.text, b.is_final) == ("", True, "", True)


def test_settings_and_wiring_for_both() -> None:
    from puripuly_heart.app.wiring import create_stt_backend
    from puripuly_heart.config.settings import AppSettings, STTProviderName, from_dict, to_dict
    from puripuly_heart.core.storage.secrets import InMemorySecretStore

    data = to_dict(AppSettings())
    data["custom_stt"] = {"base_url": "http://10.0.0.5:9000/v1", "model": "large-v3"}
    loaded = from_dict(data)
    assert (loaded.custom_stt.base_url, loaded.custom_stt.model) == ("http://10.0.0.5:9000/v1", "large-v3")

    loaded.provider.stt = STTProviderName.CUSTOM_STT
    backend = create_stt_backend(loaded, secrets=InMemorySecretStore())
    assert isinstance(backend, CustomWhisperSTTBackend)
    assert backend.base_url == "http://10.0.0.5:9000/v1" and backend.api_key == ""

    secrets = InMemorySecretStore()
    secrets.set("elevenlabs_api_key", KEY)
    loaded.provider.stt = STTProviderName.ELEVENLABS_SCRIBE
    assert isinstance(create_stt_backend(loaded, secrets=secrets), ElevenLabsScribeSTTBackend)
