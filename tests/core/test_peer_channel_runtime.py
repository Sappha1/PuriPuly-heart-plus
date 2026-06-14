from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from puripuly_heart.app.wiring import ResolvedPeerSTTConfig
from puripuly_heart.config.settings import STTProviderName
from puripuly_heart.core.clock import FakeClock
from puripuly_heart.core.runtime.peer_channel import (
    PeerChannelRuntime,
    PeerChannelRuntimeState,
    PeerRuntimeConfig,
)


@dataclass(slots=True)
class DummyManagedSTT:
    name: str = "peer"
    warmup_calls: int = 0
    close_calls: int = 0

    async def warmup(self) -> None:
        self.warmup_calls += 1

    async def close(self) -> None:
        self.close_calls += 1


@dataclass(slots=True)
class DummySource:
    close_calls: int = 0

    async def close(self) -> None:
        self.close_calls += 1


@dataclass(slots=True)
class BlockingWarmupSTT:
    name: str = "peer"
    warmup_calls: int = 0
    close_calls: int = 0
    warmup_started: asyncio.Event = field(default_factory=asyncio.Event)
    warmup_release: asyncio.Event = field(default_factory=asyncio.Event)

    async def warmup(self) -> None:
        self.warmup_calls += 1
        self.warmup_started.set()
        await self.warmup_release.wait()

    async def close(self) -> None:
        self.close_calls += 1


@dataclass(slots=True)
class FailureAwareSTT:
    name: str = "peer"
    warmup_calls: int = 0
    close_calls: int = 0
    on_terminal_failure: object | None = None

    async def warmup(self) -> None:
        self.warmup_calls += 1

    async def close(self) -> None:
        self.close_calls += 1

    async def trigger_failure(self, exc: Exception) -> None:
        assert self.on_terminal_failure is not None
        await self.on_terminal_failure(exc)


class DummyHub:
    def __init__(self) -> None:
        self.peer_stt = None
        self.replace_peer_stt_calls: list[object | None] = []
        self.peer_events: list[object] = []

    async def replace_peer_stt_provider(self, stt: object | None) -> None:
        self.replace_peer_stt_calls.append(stt)
        self.peer_stt = stt

    async def handle_peer_vad_event(self, event: object) -> None:
        self.peer_events.append(event)


class StagedAttachHub(DummyHub):
    def __init__(self) -> None:
        super().__init__()
        self.first_attach_started = asyncio.Event()
        self.first_attach_release = asyncio.Event()
        self._attach_calls = 0

    async def replace_peer_stt_provider(self, stt: object | None) -> None:
        self._attach_calls += 1
        self.replace_peer_stt_calls.append(stt)
        self.peer_stt = stt
        if self._attach_calls == 1 and stt is not None:
            self.first_attach_started.set()
            await self.first_attach_release.wait()


async def fake_run_audio_loop(**_kwargs) -> None:
    await asyncio.Event().wait()


def make_peer_runtime_config(output_device: str = "Headphones (Loopback)") -> PeerRuntimeConfig:
    backend = ResolvedPeerSTTConfig(
        provider=STTProviderName.DEEPGRAM,
        source_language="ko",
        sample_rate_hz=16000,
        keyterms=("아이리", "시나노"),
        deepgram_model="nova-3",
    )
    provider_signature = (
        backend.provider,
        backend.source_language,
        backend.deepgram_model,
        backend.keyterms,
    )
    return PeerRuntimeConfig(
        backend=backend,
        output_device=output_device,
        vad_threshold=0.6,
        vad_hangover_ms=900,
        vad_pre_roll_ms=500,
        provider_signature=provider_signature,
        runtime_signature=(
            backend.source_language,
            output_device,
            0.6,
            900,
            500,
            provider_signature,
        ),
    )


@pytest.mark.asyncio
async def test_apply_policy_is_idempotent_for_same_runtime_signature() -> None:
    hub = DummyHub()
    created: list[str] = []

    runtime = PeerChannelRuntime(
        hub=hub,
        clock=FakeClock(),
        stt_factory=lambda config, on_terminal_failure: created.append("stt") or DummyManagedSTT(),
        source_factory=lambda config: DummySource(),
        vad_factory=lambda config, model_path: "peer-vad",
        vad_model_resolver=lambda: Path("vad.onnx"),
        run_audio_loop=fake_run_audio_loop,
    )
    config = make_peer_runtime_config()

    await runtime.apply_policy(config=config, desired_active=True)
    await runtime.apply_policy(config=config, desired_active=True)

    assert created == ["stt"]
    assert len(hub.replace_peer_stt_calls) == 1
    assert runtime.state == PeerChannelRuntimeState.RUNNING


@pytest.mark.asyncio
async def test_same_signature_reapply_still_auto_recovers_late_terminal_failure() -> None:
    hub = DummyHub()
    created: list[FailureAwareSTT] = []

    def stt_factory(config, on_terminal_failure):
        _ = config
        stt = FailureAwareSTT()
        stt.on_terminal_failure = on_terminal_failure
        created.append(stt)
        return stt

    runtime = PeerChannelRuntime(
        hub=hub,
        clock=FakeClock(),
        stt_factory=stt_factory,
        source_factory=lambda config: DummySource(),
        vad_factory=lambda config, model_path: "peer-vad",
        vad_model_resolver=lambda: Path("vad.onnx"),
        run_audio_loop=fake_run_audio_loop,
    )
    config = make_peer_runtime_config()

    await runtime.apply_policy(config=config, desired_active=True)
    await runtime.apply_policy(config=config, desired_active=True)
    await created[0].trigger_failure(RuntimeError("peer session closed"))

    assert runtime.state == PeerChannelRuntimeState.RUNNING
    assert len(created) == 1
    assert hub.peer_stt is created[0]


@pytest.mark.asyncio
async def test_stale_generation_teardown_does_not_detach_newer_peer_provider() -> None:
    hub = StagedAttachHub()
    created: list[FailureAwareSTT] = []

    def stt_factory(config, on_terminal_failure):
        _ = on_terminal_failure
        stt = FailureAwareSTT(name=config.output_device)
        created.append(stt)
        return stt

    runtime = PeerChannelRuntime(
        hub=hub,
        clock=FakeClock(),
        stt_factory=stt_factory,
        source_factory=lambda config: DummySource(),
        vad_factory=lambda config, model_path: "peer-vad",
        vad_model_resolver=lambda: Path("vad.onnx"),
        run_audio_loop=fake_run_audio_loop,
    )
    first = make_peer_runtime_config(output_device="first-device")
    second = make_peer_runtime_config(output_device="second-device")

    first_task = asyncio.create_task(runtime.apply_policy(config=first, desired_active=True))
    await hub.first_attach_started.wait()
    second_task = asyncio.create_task(runtime.apply_policy(config=second, desired_active=True))
    await second_task
    hub.first_attach_release.set()
    await first_task

    assert hub.peer_stt is not None
    assert hub.peer_stt.name == "second-device"
    assert runtime.current_signature == second.runtime_signature


@pytest.mark.asyncio
async def test_warmup_does_not_interleave_with_reconfigure() -> None:
    hub = DummyHub()
    warmup_provider = BlockingWarmupSTT(name="first-device")
    created: list[BlockingWarmupSTT] = []
    reconfigure_started = asyncio.Event()

    def stt_factory(config, on_terminal_failure):
        _ = on_terminal_failure
        if config.output_device == "first-device":
            created.append(warmup_provider)
            return warmup_provider
        reconfigure_started.set()
        stt = BlockingWarmupSTT(name=config.output_device)
        created.append(stt)
        return stt

    runtime = PeerChannelRuntime(
        hub=hub,
        clock=FakeClock(),
        stt_factory=stt_factory,
        source_factory=lambda config: DummySource(),
        vad_factory=lambda config, model_path: "peer-vad",
        vad_model_resolver=lambda: Path("vad.onnx"),
        run_audio_loop=fake_run_audio_loop,
    )
    first = make_peer_runtime_config(output_device="first-device")
    second = make_peer_runtime_config(output_device="second-device")

    await runtime.apply_policy(config=first, desired_active=True)
    warmup_task = asyncio.create_task(runtime.warmup())
    await warmup_provider.warmup_started.wait()

    reconfigure_task = asyncio.create_task(runtime.apply_policy(config=second, desired_active=True))
    await asyncio.sleep(0)

    assert not reconfigure_started.is_set()

    warmup_provider.warmup_release.set()
    await warmup_task
    await reconfigure_task

    assert hub.peer_stt is not None
    assert hub.peer_stt.name == "second-device"


@pytest.mark.asyncio
async def test_warmup_during_running_state_does_not_build_a_second_peer_session() -> None:
    hub = DummyHub()
    stt = DummyManagedSTT()
    runtime = PeerChannelRuntime(
        hub=hub,
        clock=FakeClock(),
        stt_factory=lambda config, on_terminal_failure: stt,
        source_factory=lambda config: DummySource(),
        vad_factory=lambda config, model_path: "peer-vad",
        vad_model_resolver=lambda: Path("vad.onnx"),
        run_audio_loop=fake_run_audio_loop,
    )

    await runtime.apply_policy(config=make_peer_runtime_config(), desired_active=True)
    await runtime.warmup()
    await runtime.warmup()

    assert stt.warmup_calls == 2
    assert len(hub.replace_peer_stt_calls) == 1


@pytest.mark.asyncio
async def test_apply_policy_drops_superseded_in_flight_start_before_attach() -> None:
    hub = DummyHub()
    first_release = asyncio.Event()
    second_release = asyncio.Event()

    async def delayed_stt_factory(config: PeerRuntimeConfig, on_terminal_failure):
        _ = on_terminal_failure
        if config.output_device == "first-device":
            await first_release.wait()
        else:
            await second_release.wait()
        return DummyManagedSTT(name=config.output_device)

    runtime = PeerChannelRuntime(
        hub=hub,
        clock=FakeClock(),
        stt_factory=delayed_stt_factory,
        source_factory=lambda config: DummySource(),
        vad_factory=lambda config, model_path: "peer-vad",
        vad_model_resolver=lambda: Path("vad.onnx"),
        run_audio_loop=fake_run_audio_loop,
    )

    first = make_peer_runtime_config(output_device="first-device")
    second = make_peer_runtime_config(output_device="second-device")

    first_task = asyncio.create_task(runtime.apply_policy(config=first, desired_active=True))
    second_task = asyncio.create_task(runtime.apply_policy(config=second, desired_active=True))
    second_release.set()
    await second_task
    first_release.set()
    await first_task

    assert hub.peer_stt is not None
    assert hub.peer_stt.name == "second-device"
    assert runtime.current_signature == second.runtime_signature


@pytest.mark.asyncio
async def test_source_open_failure_transitions_faulted_and_detaches() -> None:
    hub = DummyHub()
    runtime = PeerChannelRuntime(
        hub=hub,
        clock=FakeClock(),
        stt_factory=lambda config, on_terminal_failure: DummyManagedSTT(),
        source_factory=lambda config: (_ for _ in ()).throw(RuntimeError("loopback open failed")),
        vad_factory=lambda config, model_path: "peer-vad",
        vad_model_resolver=lambda: Path("vad.onnx"),
        run_audio_loop=fake_run_audio_loop,
    )

    await runtime.apply_policy(config=make_peer_runtime_config(), desired_active=True)

    assert runtime.state == PeerChannelRuntimeState.FAULTED
    assert hub.replace_peer_stt_calls[-1] is None


@pytest.mark.asyncio
async def test_provider_factory_failure_transitions_faulted_without_attach() -> None:
    hub = DummyHub()
    runtime = PeerChannelRuntime(
        hub=hub,
        clock=FakeClock(),
        stt_factory=lambda config, on_terminal_failure: (_ for _ in ()).throw(
            RuntimeError("backend build failed")
        ),
        source_factory=lambda config: DummySource(),
        vad_factory=lambda config, model_path: "peer-vad",
        vad_model_resolver=lambda: Path("vad.onnx"),
        run_audio_loop=fake_run_audio_loop,
    )

    await runtime.apply_policy(config=make_peer_runtime_config(), desired_active=True)

    assert runtime.state == PeerChannelRuntimeState.FAULTED
    assert hub.replace_peer_stt_calls == []


@pytest.mark.asyncio
async def test_loop_crash_detaches_and_moves_runtime_to_faulted() -> None:
    hub = DummyHub()

    async def failing_run_audio_loop(**kwargs):
        _ = kwargs
        raise RuntimeError("loop crashed")

    runtime = PeerChannelRuntime(
        hub=hub,
        clock=FakeClock(),
        stt_factory=lambda config, on_terminal_failure: DummyManagedSTT(),
        source_factory=lambda config: DummySource(),
        vad_factory=lambda config, model_path: "peer-vad",
        vad_model_resolver=lambda: Path("vad.onnx"),
        run_audio_loop=failing_run_audio_loop,
    )

    await runtime.apply_policy(config=make_peer_runtime_config(), desired_active=True)
    await asyncio.sleep(0)

    assert runtime.state == PeerChannelRuntimeState.FAULTED
    assert hub.replace_peer_stt_calls[-1] is None


@pytest.mark.asyncio
async def test_terminal_managed_stt_failure_auto_recovers_without_policy_reapply() -> None:
    hub = DummyHub()
    created: list[FailureAwareSTT] = []

    def stt_factory(config, on_terminal_failure):
        _ = config
        stt = FailureAwareSTT()
        stt.on_terminal_failure = on_terminal_failure
        created.append(stt)
        return stt

    runtime = PeerChannelRuntime(
        hub=hub,
        clock=FakeClock(),
        stt_factory=stt_factory,
        source_factory=lambda config: DummySource(),
        vad_factory=lambda config, model_path: "peer-vad",
        vad_model_resolver=lambda: Path("vad.onnx"),
        run_audio_loop=fake_run_audio_loop,
    )
    config = make_peer_runtime_config()

    await runtime.apply_policy(config=config, desired_active=True)
    await created[0].trigger_failure(RuntimeError("peer session closed"))

    assert runtime.state == PeerChannelRuntimeState.RUNNING
    assert len(created) == 1
    assert hub.peer_stt is created[0]


@pytest.mark.asyncio
async def test_late_stt_failure_after_audio_loop_fault_does_not_recover() -> None:
    hub = DummyHub()
    created: list[FailureAwareSTT] = []

    def stt_factory(config, on_terminal_failure):
        _ = config
        stt = FailureAwareSTT()
        stt.on_terminal_failure = on_terminal_failure
        created.append(stt)
        return stt

    async def failing_run_audio_loop(**kwargs):
        _ = kwargs
        raise RuntimeError("loop crashed")

    runtime = PeerChannelRuntime(
        hub=hub,
        clock=FakeClock(),
        stt_factory=stt_factory,
        source_factory=lambda config: DummySource(),
        vad_factory=lambda config, model_path: "peer-vad",
        vad_model_resolver=lambda: Path("vad.onnx"),
        run_audio_loop=failing_run_audio_loop,
    )

    await runtime.apply_policy(config=make_peer_runtime_config(), desired_active=True)
    await asyncio.sleep(0)
    await created[0].trigger_failure(RuntimeError("late peer session closed"))

    assert runtime.state == PeerChannelRuntimeState.FAULTED
    assert len(created) == 1
    assert hub.peer_stt is None


@pytest.mark.asyncio
async def test_close_detaches_provider_and_cancels_running_loop() -> None:
    hub = DummyHub()
    runtime = PeerChannelRuntime(
        hub=hub,
        clock=FakeClock(),
        stt_factory=lambda config, on_terminal_failure: DummyManagedSTT(),
        source_factory=lambda config: DummySource(),
        vad_factory=lambda config, model_path: "peer-vad",
        vad_model_resolver=lambda: Path("vad.onnx"),
        run_audio_loop=fake_run_audio_loop,
    )
    await runtime.apply_policy(config=make_peer_runtime_config(), desired_active=True)

    await runtime.close()

    assert hub.replace_peer_stt_calls[-1] is None
    assert runtime.state == PeerChannelRuntimeState.STOPPED
