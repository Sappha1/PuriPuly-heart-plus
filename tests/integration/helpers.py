from __future__ import annotations

import asyncio
import os
import time
import wave
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pytest

from puripuly_heart.config.settings import QwenRegion, QwenSettings

INTEGRATION_ENV = "INTEGRATION"
EVENT_POLL_TIMEOUT_S = float(os.getenv("INTEGRATION_EVENT_POLL_TIMEOUT_S", "0.1"))
WARMUP_DELAY_S = float(os.getenv("INTEGRATION_WARMUP_S", "0.5"))
CHUNK_DELAY_S = float(os.getenv("INTEGRATION_CHUNK_DELAY_S", "0.05"))
RESULT_TIMEOUT_S = float(os.getenv("INTEGRATION_RESULT_TIMEOUT_S", "30"))
OSC_TIMEOUT_S = float(os.getenv("INTEGRATION_OSC_TIMEOUT_S", "15"))
ITERATION_DELAY_S = float(os.getenv("INTEGRATION_ITERATION_DELAY_S", "1.0"))
OPEN_SESSION_TIMEOUT_S = float(os.getenv("INTEGRATION_OPEN_TIMEOUT_S", "15"))


def integration_mark():
    return pytest.mark.skipif(
        os.getenv(INTEGRATION_ENV) != "1",
        reason="set INTEGRATION=1 to run integration tests",
    )


def require_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        pytest.skip(f"missing env var {name}")
    return value


def require_module(module: str, *, reason: str) -> None:
    try:
        __import__(module)
    except ModuleNotFoundError as exc:  # pragma: no cover
        raise RuntimeError(reason) from exc


def resolve_test_audio_path(
    *, env_var: str = "TEST_AUDIO_PATH", filename: str = "test_speech.wav"
) -> Path:
    audio_env = os.getenv(env_var)
    if audio_env:
        return Path(audio_env)
    return Path(__file__).resolve().parents[2] / ".test_audio" / filename


def load_audio_wav(path: str | Path) -> tuple[np.ndarray, int]:
    with wave.open(str(path), "rb") as f:
        sample_rate = f.getframerate()
        n_frames = f.getnframes()
        audio_data = f.readframes(n_frames)

    samples_int16 = np.frombuffer(audio_data, dtype=np.int16)
    samples_f32 = samples_int16.astype(np.float32) / 32768.0
    return samples_f32, sample_rate


def chunk_audio(
    samples: np.ndarray, *, sample_rate_hz: int, chunk_ms: int | None = None
) -> tuple[list[np.ndarray], int]:
    if chunk_ms is None:
        chunk_ms = int(os.getenv("INTEGRATION_CHUNK_MS", "100"))
    chunk_samples = int(sample_rate_hz * (chunk_ms / 1000.0))
    if chunk_samples <= 0:
        raise ValueError("chunk size must be positive")
    chunks = [samples[i : i + chunk_samples] for i in range(0, len(samples), chunk_samples)]
    return chunks, chunk_samples


def qwen_settings_from_env() -> QwenSettings:
    region_raw = os.getenv("QWEN_REGION", QwenRegion.BEIJING.value).lower()
    try:
        region = QwenRegion(region_raw)
    except ValueError:
        region = QwenRegion.BEIJING
    return QwenSettings(region=region)


def get_qwen_asr_endpoint() -> str:
    return os.getenv("QWEN_ASR_ENDPOINT", qwen_settings_from_env().get_asr_endpoint())


def get_qwen_base_url() -> str:
    return os.getenv("QWEN_BASE_URL", qwen_settings_from_env().get_llm_base_url())


@dataclass(slots=True)
class MockOscSender:
    messages: list[str] = field(default_factory=list)
    typing_states: list[bool] = field(default_factory=list)

    def send_chatbox(self, text: str) -> None:
        self.messages.append(text)

    def send_typing(self, is_typing: bool) -> None:
        self.typing_states.append(is_typing)


class SimpleClock:
    def now(self) -> float:
        return time.time()


async def next_ui_event(queue: asyncio.Queue, *, timeout_s: float | None = None):
    timeout = EVENT_POLL_TIMEOUT_S if timeout_s is None else timeout_s
    try:
        return await asyncio.wait_for(queue.get(), timeout=timeout)
    except asyncio.TimeoutError:
        return None


async def wait_for_event(event: asyncio.Event, *, timeout_s: float | None = None) -> bool:
    timeout = RESULT_TIMEOUT_S if timeout_s is None else timeout_s
    try:
        await asyncio.wait_for(event.wait(), timeout=timeout)
        return True
    except asyncio.TimeoutError:
        return False


async def send_vad_events(
    hub,
    utterance_id,
    chunks: list[np.ndarray],
    *,
    chunk_delay_s: float | None = None,
) -> None:
    if not chunks:
        return
    from puripuly_heart.core.vad.gating import SpeechChunk, SpeechEnd, SpeechStart

    delay = CHUNK_DELAY_S if chunk_delay_s is None else chunk_delay_s
    pre_roll = np.zeros(len(chunks[0]), dtype=np.float32)
    await hub.handle_vad_event(SpeechStart(utterance_id, pre_roll=pre_roll, chunk=chunks[0]))
    await asyncio.sleep(delay)
    for chunk in chunks[1:]:
        await hub.handle_vad_event(SpeechChunk(utterance_id, chunk=chunk))
        await asyncio.sleep(delay)
    await hub.handle_vad_event(SpeechEnd(utterance_id))


async def stream_silence(
    session, *, frames: int = 10, frame_bytes: int = 1024, delay_s: float = 0.032
) -> None:
    silence = b"\0" * frame_bytes
    for _ in range(frames):
        await session.send_audio(silence)
        await asyncio.sleep(delay_s)


async def drain_and_close(
    session, *, drain_timeout_s: float = 30.0, close_timeout_s: float = 5.0
) -> None:
    async def _drain():
        async for _ in session.events():
            pass

    await asyncio.wait_for(_drain(), timeout=drain_timeout_s)
    await asyncio.wait_for(session.close(), timeout=close_timeout_s)


async def open_session(backend, *, timeout_s: float | None = None):
    timeout = OPEN_SESSION_TIMEOUT_S if timeout_s is None else timeout_s
    return await asyncio.wait_for(backend.open_session(), timeout=timeout)
