"""r626: shutdown must abandon local-STT work, never wait on it.

Closing the app mid-peer-decode froze the whole process until force-kill
(2026-08-28 log): teardown finalized the in-flight utterance (a full native
decode awaited inline) and interpreter finalization then destroyed the
sherpa/onnxruntime objects the abandoned decode thread was still inside.
These tests pin the stand-down behavior of every layer that fix touched.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from pathlib import Path
from uuid import uuid4

import numpy as np
import pytest

from puripuly_heart.core import shutdown
from puripuly_heart.core.stt.controller import ManagedSTTProvider
from puripuly_heart.providers.stt.local_qwen_sherpa import (
    LocalQwenSherpaLoadError,
    LocalQwenSherpaSTTBackend,
)


@pytest.fixture(autouse=True)
def _clean_shutdown_flag():
    shutdown._reset_for_tests()
    yield
    shutdown._reset_for_tests()


def test_begin_shutdown_sets_flag_idempotently() -> None:
    assert not shutdown.is_shutting_down()
    shutdown.begin_shutdown("test")
    shutdown.begin_shutdown("test again")
    assert shutdown.is_shutting_down()


async def test_local_decode_stands_down_during_shutdown(tmp_path: Path) -> None:
    backend = LocalQwenSherpaSTTBackend(model_dir=tmp_path)
    shutdown.begin_shutdown("test")
    # Must return empty WITHOUT touching asset validation or the recognizer
    # cache — there is no model under tmp_path, so reaching either would
    # raise, not return.
    text, lang = await backend.decode_f32(np.zeros(1600, dtype=np.float32))
    assert text == ""
    assert lang is None


async def test_recognizer_load_refused_during_shutdown(tmp_path: Path) -> None:
    backend = LocalQwenSherpaSTTBackend(model_dir=tmp_path)
    shutdown.begin_shutdown("test")
    with pytest.raises(LocalQwenSherpaLoadError, match="shutting down"):
        await backend.warmup()


@dataclass(slots=True)
class _RecordingSession:
    calls: list[str] = field(default_factory=list)

    async def on_speech_end(self, *, trailing_silence_ms: int | None = None) -> None:
        self.calls.append("on_speech_end")

    async def stop(self) -> None:
        self.calls.append("stop")

    async def close(self) -> None:
        self.calls.append("close")


@dataclass(slots=True)
class _NullBackend:
    async def open_session(self) -> object:  # pragma: no cover - never reached
        raise AssertionError("drain tests never open sessions")

    async def close(self) -> None:
        pass


async def _drain(provider: ManagedSTTProvider, session: _RecordingSession) -> None:
    consumer = asyncio.create_task(asyncio.sleep(0))
    await provider._drain_and_close(session, consumer, allow_finalize=True)


async def test_drain_finalizes_pending_utterance_normally() -> None:
    provider = ManagedSTTProvider(backend=_NullBackend(), sample_rate_hz=16000)
    provider._active_utterance_id = uuid4()
    session = _RecordingSession()
    await _drain(provider, session)
    assert "on_speech_end" in session.calls


async def test_drain_skips_finalize_during_shutdown() -> None:
    provider = ManagedSTTProvider(backend=_NullBackend(), sample_rate_hz=16000)
    provider._active_utterance_id = uuid4()
    session = _RecordingSession()
    shutdown.begin_shutdown("test")
    await _drain(provider, session)
    # The doomed utterance is abandoned, but the session still stops cleanly.
    assert "on_speech_end" not in session.calls
    assert "stop" in session.calls
    assert "close" in session.calls


def test_page_disconnect_handler_is_async() -> None:
    """flet 0.28.3 dispatches SYNC page.on_disconnect handlers via
    page.run_thread onto the pool executor that FletSocketServer.close()
    has already shut down when disconnect fires — a sync handler silently
    never runs, disabling this entire shutdown path. Only an async handler
    (awaited inline by EventHandler) actually executes."""
    from pathlib import Path

    app_py = (Path(__file__).resolve().parents[2] / "src" / "puripuly_heart"
              / "ui" / "app.py").read_text(encoding="utf-8")
    assert "async def _on_page_disconnect" in app_py
