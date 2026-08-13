"""Decode-timeout watchdog: abandon a wedged native decode, rebuild, recover.

Incident (2026-08-11 20:44): with a GPU-heavy game running, a DirectML decode
of a 4.2s peer clip sat inside onnxruntime for 6+ minutes. decode_start was
the last STT log line; the peer pipeline starved behind the await and the
whole UI ghosted. The native call cannot be cancelled or joined from Python,
so the only sane recovery is: stop listening after a deadline, poison the
recognizer so nothing touches it again, and build a fresh instance on the
next utterance while the hung thread is left to its fate.
"""
from __future__ import annotations

import asyncio
import threading
import time
from types import SimpleNamespace

import numpy as np
import pytest

from puripuly_heart.providers.stt import local_qwen_sherpa as local_qwen_module
from puripuly_heart.providers.stt.local_qwen_sherpa import (
    LocalQwenSherpaDecodeTimeoutError,
    LocalQwenSherpaInferenceError,
    LocalQwenSherpaSTTBackend,
)


class _Stream:
    def __init__(self, text: str) -> None:
        self.result = SimpleNamespace(text=text)

    def accept_waveform(self, sample_rate: int, samples) -> None:
        _ = (sample_rate, samples)


class HangingRecognizer:
    """decode_stream blocks until `release` is set — the wedged-native shape."""

    def __init__(self, release: threading.Event) -> None:
        self.release = release
        self.decodes = 0

    def create_stream(self) -> _Stream:
        return _Stream("from the wedged instance")

    def decode_stream(self, stream: _Stream) -> None:
        _ = stream
        self.decodes += 1
        self.release.wait(timeout=10)


class HealthyRecognizer:
    def __init__(self, text: str = "recovered") -> None:
        self.text = text
        self.decodes = 0

    def create_stream(self) -> _Stream:
        return _Stream(self.text)

    def decode_stream(self, stream: _Stream) -> None:
        _ = stream
        self.decodes += 1


def _patch_runtime(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        local_qwen_module, "validate_local_stt_runtime_ready", lambda *a, **k: None
    )
    monkeypatch.setattr(local_qwen_module, "_available_memory_mb", lambda: 99_999)


def _patch_factory(monkeypatch: pytest.MonkeyPatch, instances: list[object]) -> list[object]:
    """Serve `instances` in order; returns the list of instances actually built."""
    built: list[object] = []

    def factory(**_kwargs):
        item = instances[len(built)]
        built.append(item)
        return item

    monkeypatch.setattr(local_qwen_module, "create_local_qwen_sherpa_recognizer", factory)
    return built


def _samples(ms: int = 100) -> np.ndarray:
    return np.zeros(int(16000 * ms / 1000), dtype=np.float32)


@pytest.mark.asyncio
async def test_decode_timeout_raises_typed_error_poisons_and_evicts(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    release = threading.Event()
    recognizer = HangingRecognizer(release)
    _patch_runtime(monkeypatch)
    _patch_factory(monkeypatch, [recognizer])
    backend = LocalQwenSherpaSTTBackend(
        model_dir=tmp_path / "model", stream_label="peer", decode_timeout_s=0.2
    )

    started = time.monotonic()
    with pytest.raises(LocalQwenSherpaDecodeTimeoutError):
        await backend.decode_f32(_samples())
    assert time.monotonic() - started < 5.0, "the timeout did not bound the wait"

    # The wedged instance must never be handed out again — to either channel.
    assert local_qwen_module._recognizer_is_poisoned(recognizer)
    assert backend._recognizer is None
    assert local_qwen_module._shared_recognizer_cached(backend._recognizer_cache_key()) is None
    # …and it is still an inference error for generic handling.
    assert issubclass(LocalQwenSherpaDecodeTimeoutError, LocalQwenSherpaInferenceError)
    release.set()


@pytest.mark.asyncio
async def test_next_utterance_rebuilds_while_the_old_thread_still_hangs(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    """The whole point: recovery must NOT wait for (or join) the hung thread."""
    release = threading.Event()
    _patch_runtime(monkeypatch)
    built = _patch_factory(
        monkeypatch, [HangingRecognizer(release), HealthyRecognizer("recovered")]
    )
    backend = LocalQwenSherpaSTTBackend(
        model_dir=tmp_path / "model", stream_label="peer", decode_timeout_s=0.2
    )

    with pytest.raises(LocalQwenSherpaDecodeTimeoutError):
        await backend.decode_f32(_samples())

    # Old thread is STILL wedged (release never set) — the rebuild+decode
    # must complete anyway, on a fresh instance.
    text, _lang = await asyncio.wait_for(backend.decode_f32(_samples()), timeout=5.0)
    assert text == "recovered"
    assert len(built) == 2
    release.set()


@pytest.mark.asyncio
async def test_late_result_from_the_abandoned_thread_is_discarded(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    """When the native call eventually returns minutes later, its transcript
    belongs to nobody: the future was cancelled, so delivery is dropped."""
    release = threading.Event()
    _patch_runtime(monkeypatch)
    _patch_factory(monkeypatch, [HangingRecognizer(release), HealthyRecognizer("recovered")])
    backend = LocalQwenSherpaSTTBackend(
        model_dir=tmp_path / "model", stream_label="peer", decode_timeout_s=0.2
    )

    with pytest.raises(LocalQwenSherpaDecodeTimeoutError):
        await backend.decode_f32(_samples())

    release.set()  # the wedge clears AFTER abandonment
    await asyncio.sleep(0.05)  # let the late delivery hit the cancelled future

    text, _lang = await asyncio.wait_for(backend.decode_f32(_samples()), timeout=5.0)
    assert text == "recovered", "the stale result leaked into a later decode"


@pytest.mark.asyncio
async def test_other_channel_sheds_the_poisoned_shared_instance(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    """self and peer share ONE instance (r386). A timeout on peer poisons the
    instance itself, so self — which still holds its own reference — must
    also rebuild instead of decoding into the same wedge."""
    release = threading.Event()
    _patch_runtime(monkeypatch)
    built = _patch_factory(
        monkeypatch, [HangingRecognizer(release), HealthyRecognizer("recovered")]
    )
    model_dir = tmp_path / "model"
    peer = LocalQwenSherpaSTTBackend(
        model_dir=model_dir, stream_label="peer", decode_timeout_s=0.2
    )
    self_backend = LocalQwenSherpaSTTBackend(
        model_dir=model_dir, stream_label="self", decode_timeout_s=0.2
    )
    await peer.warmup()
    await self_backend.warmup()
    assert peer._recognizer is self_backend._recognizer

    with pytest.raises(LocalQwenSherpaDecodeTimeoutError):
        await peer.decode_f32(_samples())

    text, _lang = await asyncio.wait_for(self_backend.decode_f32(_samples()), timeout=5.0)
    assert text == "recovered"
    assert len(built) == 2, "self decoded on the poisoned instance"
    release.set()


@pytest.mark.asyncio
async def test_decode_errors_still_wrap_as_inference_error_without_poisoning(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    class ExplodingRecognizer(HealthyRecognizer):
        def decode_stream(self, stream: _Stream) -> None:
            raise RuntimeError("decode failed")

    recognizer = ExplodingRecognizer()
    _patch_runtime(monkeypatch)
    _patch_factory(monkeypatch, [recognizer])
    backend = LocalQwenSherpaSTTBackend(model_dir=tmp_path / "model", decode_timeout_s=0.2)

    with pytest.raises(LocalQwenSherpaInferenceError, match="decode failed"):
        await backend.decode_f32(_samples())
    assert not local_qwen_module._recognizer_is_poisoned(recognizer)
    assert (
        local_qwen_module._shared_recognizer_cached(backend._recognizer_cache_key())
        is recognizer
    ), "an ordinary decode error must not throw away the 1.1GB instance"


@pytest.mark.asyncio
async def test_nonpositive_timeout_disables_the_watchdog(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    _patch_runtime(monkeypatch)
    _patch_factory(monkeypatch, [HealthyRecognizer("recovered")])
    backend = LocalQwenSherpaSTTBackend(model_dir=tmp_path / "model", decode_timeout_s=0.0)
    text, _lang = await backend.decode_f32(_samples())
    assert text == "recovered"


@pytest.mark.asyncio
async def test_session_surfaces_the_timeout_through_events(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    """on_speech_end must not swallow the timeout: it flows out of events()
    so ManagedSTTProvider's terminal-failure path tears the session down."""
    release = threading.Event()
    _patch_runtime(monkeypatch)
    _patch_factory(monkeypatch, [HangingRecognizer(release)])
    backend = LocalQwenSherpaSTTBackend(
        model_dir=tmp_path / "model", stream_label="peer", decode_timeout_s=0.2
    )
    session = await backend.open_session()
    await session.send_audio_f32(_samples())
    await session.on_speech_end()

    gen = session.events()
    with pytest.raises(LocalQwenSherpaDecodeTimeoutError):
        await gen.__anext__()
    release.set()


@pytest.mark.asyncio
async def test_managed_provider_recovers_on_the_next_utterance(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    """End to end at the provider layer: utterance 1 times out (session torn
    down, terminal-failure callback fired), utterance 2 transcribes on the
    rebuilt recognizer — no restart, no manual toggle."""
    from uuid import uuid4

    from puripuly_heart.core.stt.controller import ManagedSTTProvider
    from puripuly_heart.core.vad.gating import SpeechEnd, SpeechStart
    from puripuly_heart.domain.events import STTFinalEvent, STTSessionState

    release = threading.Event()
    _patch_runtime(monkeypatch)
    built = _patch_factory(
        monkeypatch, [HangingRecognizer(release), HealthyRecognizer("recovered")]
    )
    backend = LocalQwenSherpaSTTBackend(
        model_dir=tmp_path / "model", stream_label="peer", decode_timeout_s=0.2
    )
    failures: list[Exception] = []
    provider = ManagedSTTProvider(
        backend=backend,
        sample_rate_hz=16000,
        channel="peer",
        on_terminal_failure=failures.append,
    )

    chunk = _samples(20)
    first = uuid4()
    await provider.handle_vad_event(
        SpeechStart(utterance_id=first, pre_roll=chunk, chunk=chunk)
    )
    await provider.handle_vad_event(SpeechEnd(utterance_id=first))

    deadline = time.monotonic() + 5.0
    while provider.state is not STTSessionState.DISCONNECTED:
        assert time.monotonic() < deadline, "session never tore down after the timeout"
        await asyncio.sleep(0.01)
    assert failures and isinstance(failures[0], LocalQwenSherpaDecodeTimeoutError)

    second = uuid4()
    await provider.handle_vad_event(
        SpeechStart(utterance_id=second, pre_roll=chunk, chunk=chunk)
    )
    await provider.handle_vad_event(SpeechEnd(utterance_id=second))

    events = provider.events()
    while True:
        event = await asyncio.wait_for(events.__anext__(), timeout=5.0)
        if isinstance(event, STTFinalEvent):
            break
    assert event.transcript.text == "recovered"
    assert event.utterance_id == second
    assert len(built) == 2
    release.set()
    await provider.close()
