from __future__ import annotations

import asyncio
import sys
import types

import pytest

from puripuly_heart.core.stt.backend import STTBackendTranscriptEvent
from puripuly_heart.providers.stt import qwen_asr as qwen_asr_module
from puripuly_heart.providers.stt.qwen_asr import _COMMIT, _STOP, _QwenASRSession
from tests.helpers.fakes import TargetThread


def _make_session() -> _QwenASRSession:
    return _QwenASRSession(
        api_key="k",
        model="m",
        language="en",
        endpoint="wss://example",
        sample_rate_hz=16000,
        connect_timeout_s=5.0,
    )


@pytest.mark.asyncio
async def test_qwen_asr_session_on_speech_end_enqueues_commit():
    session = _make_session()

    await session.on_speech_end(trailing_silence_ms=200)
    commit = session._audio_q.get_nowait()
    assert commit is _COMMIT

    await session.on_speech_end(trailing_silence_ms=0)
    silence = session._audio_q.get_nowait()
    commit = session._audio_q.get_nowait()

    assert isinstance(silence, bytes)
    assert commit is _COMMIT


@pytest.mark.asyncio
async def test_qwen_asr_session_send_audio_and_stop() -> None:
    session = _make_session()

    await session.send_audio(b"abc")
    assert session._audio_q.get_nowait() == b"abc"

    await session.stop()
    assert session._stopped is True
    assert session._audio_q.get_nowait() is _STOP


@pytest.mark.asyncio
async def test_qwen_asr_session_reports_error(monkeypatch) -> None:
    session = _make_session()
    session._loop = asyncio.get_running_loop()

    err = RuntimeError("boom")
    session._report_error(err)
    await asyncio.sleep(0)

    event = await session._events.get()
    assert event is err
    assert session._error_reported is True
    assert session._connect_error is err
    assert session._connected.is_set() is True


@pytest.mark.asyncio
async def test_qwen_asr_session_events_yield_and_raise() -> None:
    session = _make_session()

    session._events.put_nowait(STTBackendTranscriptEvent(text="hi", is_final=True))
    session._events.put_nowait(None)

    gen = session.events()
    event = await gen.__anext__()
    assert event.text == "hi"
    with pytest.raises(StopAsyncIteration):
        await gen.__anext__()

    session._events.put_nowait(RuntimeError("boom"))
    gen = session.events()
    with pytest.raises(RuntimeError, match="boom"):
        await gen.__anext__()


@pytest.mark.asyncio
async def test_qwen_asr_session_start_success(monkeypatch) -> None:
    session = _make_session()

    def fake_run_sync():
        session._connected.set()

    async def fake_to_thread(func, *args, **kwargs):
        return func(*args, **kwargs)

    monkeypatch.setattr(asyncio, "to_thread", fake_to_thread)
    monkeypatch.setattr(qwen_asr_module.threading, "Thread", TargetThread)
    monkeypatch.setattr(session, "_run_sync", fake_run_sync)

    await session.start()
    assert session._connected.is_set() is True


@pytest.mark.asyncio
async def test_qwen_asr_session_start_failure(monkeypatch) -> None:
    session = _make_session()

    def fake_run_sync():
        session._connect_error = RuntimeError("fail")
        session._connected.set()

    async def fake_to_thread(func, *args, **kwargs):
        return func(*args, **kwargs)

    monkeypatch.setattr(asyncio, "to_thread", fake_to_thread)
    monkeypatch.setattr(qwen_asr_module.threading, "Thread", TargetThread)
    monkeypatch.setattr(session, "_run_sync", fake_run_sync)

    with pytest.raises(RuntimeError, match="fail"):
        await session.start()


@pytest.mark.asyncio
async def test_qwen_asr_session_signal_stop_is_safe() -> None:
    session = _make_session()
    session._signal_stop()
    assert session._audio_q.get_nowait() is _STOP


@pytest.mark.asyncio
async def test_qwen_asr_session_report_error_only_once() -> None:
    session = _make_session()
    session._loop = asyncio.get_running_loop()

    err = RuntimeError("boom")
    session._report_error(err)
    session._report_error(RuntimeError("second"))
    await asyncio.sleep(0)

    assert session._error_reported is True
    assert await session._events.get() is err
    assert session._events.empty()


@pytest.mark.asyncio
async def test_qwen_asr_session_run_sync_processes_audio_commit_and_final_event(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _make_session()
    session._loop = asyncio.get_running_loop()
    session._connect_started_at = 1.0

    append_calls: list[str] = []
    commit_calls = 0
    closed = False
    latest_dashscope: dict[str, object] = {}

    class FakeOmniRealtimeCallback:
        pass

    class FakeMultiModality:
        TEXT = "text"

    class FakeTranscriptionParams:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    class FakeConversation:
        def __init__(self, model: str, url: str, callback):
            _ = (model, url)
            self.callback = callback

        def connect(self):
            self.callback.on_open()

        def update_session(self, **kwargs):
            _ = kwargs
            self.callback.on_event({"type": "session.created", "session": {"id": "sid"}})

        def append_audio(self, audio_b64: str):
            append_calls.append(audio_b64)
            self.callback.on_event(
                {
                    "type": "conversation.item.input_audio_transcription.text",
                    "text": "t",
                    "stash": "",
                }
            )

        def commit(self):
            nonlocal commit_calls
            commit_calls += 1
            self.callback.on_event({"type": "input_audio_buffer.committed"})
            self.callback.on_event(
                {
                    "type": "conversation.item.input_audio_transcription.completed",
                    "transcript": "final transcript",
                }
            )

        def close(self):
            nonlocal closed
            closed = True

    dashscope_pkg = types.ModuleType("dashscope")
    dashscope_pkg.api_key = None
    latest_dashscope["pkg"] = dashscope_pkg
    qwen_omni_pkg = types.ModuleType("dashscope.audio.qwen_omni")
    qwen_omni_pkg.MultiModality = FakeMultiModality
    qwen_omni_pkg.OmniRealtimeCallback = FakeOmniRealtimeCallback
    qwen_omni_pkg.OmniRealtimeConversation = FakeConversation
    omni_rt_pkg = types.ModuleType("dashscope.audio.qwen_omni.omni_realtime")
    omni_rt_pkg.TranscriptionParams = FakeTranscriptionParams

    monkeypatch.setitem(sys.modules, "dashscope", dashscope_pkg)
    monkeypatch.setitem(sys.modules, "dashscope.audio", types.ModuleType("dashscope.audio"))
    monkeypatch.setitem(sys.modules, "dashscope.audio.qwen_omni", qwen_omni_pkg)
    monkeypatch.setitem(sys.modules, "dashscope.audio.qwen_omni.omni_realtime", omni_rt_pkg)

    session._audio_q.put_nowait(b"pcm")
    session._audio_q.put_nowait(_COMMIT)
    session._audio_q.put_nowait(_COMMIT)
    session._audio_q.put_nowait(_STOP)
    session._run_sync()
    await asyncio.sleep(0)

    first = await session._events.get()
    assert isinstance(first, STTBackendTranscriptEvent)
    assert first.text == "final transcript"
    assert latest_dashscope["pkg"].api_key == "k"
    assert append_calls
    assert commit_calls == 2
    assert session._connected.is_set() is True
    assert closed is True

    tail: list[object] = []
    while not session._events.empty():
        tail.append(session._events.get_nowait())
    assert None in tail
