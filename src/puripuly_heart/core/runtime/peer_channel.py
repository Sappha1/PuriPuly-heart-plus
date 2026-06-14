from __future__ import annotations

import asyncio
import inspect
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Awaitable, Callable, Protocol

from puripuly_heart.app.wiring import ResolvedPeerSTTConfig
from puripuly_heart.core.clock import Clock
from puripuly_heart.core.orchestrator.hub import ClientHub


class PeerChannelRuntimeState(str, Enum):
    STOPPED = "stopped"
    STARTING = "starting"
    RUNNING = "running"
    STOPPING = "stopping"
    FAULTED = "faulted"


@dataclass(frozen=True, slots=True)
class PeerRuntimeConfig:
    backend: ResolvedPeerSTTConfig
    output_device: str
    vad_threshold: float
    vad_hangover_ms: int
    vad_pre_roll_ms: int
    provider_signature: tuple[object, ...]
    runtime_signature: tuple[object, ...]


class SpeechChannelRuntime(Protocol):
    @property
    def state(self) -> PeerChannelRuntimeState: ...

    @property
    def current_signature(self) -> object | None: ...

    async def apply_policy(self, *, config: PeerRuntimeConfig, desired_active: bool) -> None: ...
    async def warmup(self) -> None: ...
    async def close(self) -> None: ...


@dataclass(slots=True)
class _PeerHubVadSink:
    hub: ClientHub

    async def handle_vad_event(self, event) -> None:  # noqa: ANN001
        await self.hub.handle_peer_vad_event(event)


class PeerChannelRuntime:
    def __init__(
        self,
        *,
        hub: ClientHub,
        clock: Clock,
        stt_factory: Callable[
            [PeerRuntimeConfig, Callable[[Exception], Awaitable[None]]],
            Awaitable[object] | object,
        ],
        source_factory: Callable[[PeerRuntimeConfig], object],
        vad_factory: Callable[[PeerRuntimeConfig, Path], object],
        vad_model_resolver: Callable[[], Path],
        run_audio_loop: Callable[..., Awaitable[None]],
    ) -> None:
        self.hub = hub
        self.clock = clock
        self._stt_factory = stt_factory
        self._source_factory = source_factory
        self._vad_factory = vad_factory
        self._vad_model_resolver = vad_model_resolver
        self._run_audio_loop = run_audio_loop

        self._config: PeerRuntimeConfig | None = None
        self._stt: object | None = None
        self._audio_source: object | None = None
        self._vad: object | None = None
        self._loop_task: asyncio.Task[None] | None = None
        self._signature: tuple[object, ...] | None = None
        self._state = PeerChannelRuntimeState.STOPPED
        self._generation = 0
        self._desired_active = False
        self._lock = asyncio.Lock()

    @property
    def state(self) -> PeerChannelRuntimeState:
        return self._state

    @property
    def current_signature(self) -> object | None:
        return self._signature

    async def apply_policy(self, *, config: PeerRuntimeConfig, desired_active: bool) -> None:
        async with self._lock:
            if (
                desired_active
                and self._desired_active
                and self._state == PeerChannelRuntimeState.RUNNING
                and self._signature == config.runtime_signature
            ):
                self._config = config
                return
            self._generation += 1
            generation = self._generation
            self._config = config
            self._desired_active = desired_active
            if not desired_active:
                self._state = PeerChannelRuntimeState.STOPPING
            elif (
                self._signature == config.runtime_signature
                and self._state == PeerChannelRuntimeState.RUNNING
            ):
                return
            else:
                self._state = PeerChannelRuntimeState.STARTING

        if not desired_active:
            await self._teardown_resources(target_state=PeerChannelRuntimeState.STOPPED)
            return

        await self._start_generation(generation, config)

    async def warmup(self) -> None:
        async with self._lock:
            stt = self._stt
            if (
                self._desired_active
                and stt is not None
                and self._state == PeerChannelRuntimeState.RUNNING
                and hasattr(stt, "warmup")
            ):
                await stt.warmup()

    async def close(self) -> None:
        async with self._lock:
            self._generation += 1
            self._desired_active = False
            self._state = PeerChannelRuntimeState.STOPPING
        await self._teardown_resources(target_state=PeerChannelRuntimeState.STOPPED)

    async def _start_generation(self, generation: int, config: PeerRuntimeConfig) -> None:
        try:
            stt = self._stt_factory(
                config,
                lambda exc, *, _generation=generation: self._on_terminal_stt_failure(
                    exc, generation=_generation
                ),
            )
            if inspect.isawaitable(stt):
                stt = await stt
        except Exception:
            await self._mark_faulted_if_current(generation, detach_provider=False)
            return

        if self._is_superseded(generation):
            await self._close_if_possible(stt)
            return

        source = None
        try:
            source = self._source_factory(config)
            model_path = self._vad_model_resolver()
            vad = self._vad_factory(config, model_path)
        except Exception:
            await self._close_if_possible(source)
            await self._close_if_possible(stt)
            await self._mark_faulted_if_current(generation, detach_provider=True)
            return

        if self._is_superseded(generation):
            await self._close_if_possible(source)
            await self._close_if_possible(stt)
            return

        loop_to_cancel = None
        source_to_close = None
        old_stt = None
        async with self._lock:
            if self._is_superseded(generation):
                pass
            else:
                loop_to_cancel = self._loop_task
                source_to_close = self._audio_source
                old_stt = self._stt
                self._loop_task = None
                self._audio_source = None
                self._vad = None
                self._stt = None
        if self._is_superseded(generation):
            await self._close_if_possible(source)
            await self._close_if_possible(stt)
            return

        await self._cancel_loop(loop_to_cancel)
        await self._close_if_possible(source_to_close)
        if old_stt is not None:
            await self.hub.replace_peer_stt_provider(None)

        await self.hub.replace_peer_stt_provider(stt)
        if self._is_superseded(generation):
            await self._close_if_possible(source)
            await self._close_peer_provider_if_current(stt)
            return

        loop_task = asyncio.create_task(
            self._run_peer_loop_guarded(
                source=source,
                vad=vad,
                target_sample_rate_hz=config.backend.sample_rate_hz,
                generation=generation,
            )
        )
        async with self._lock:
            if self._is_superseded(generation):
                loop_task.cancel()
            else:
                self._stt = stt
                self._audio_source = source
                self._vad = vad
                self._loop_task = loop_task
                self._signature = config.runtime_signature
                self._state = PeerChannelRuntimeState.RUNNING

        if self._is_superseded(generation):
            await asyncio.gather(loop_task, return_exceptions=True)
            await self._close_peer_provider_if_current(stt)
            await self._close_if_possible(source)

    async def _run_peer_loop_guarded(
        self,
        *,
        source: object,
        vad: object,
        target_sample_rate_hz: int,
        generation: int,
    ) -> None:
        try:
            await self._run_audio_loop(
                source=source,
                vad=vad,
                sink=_PeerHubVadSink(hub=self.hub),
                target_sample_rate_hz=target_sample_rate_hz,
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            await self._on_runtime_failure(exc, generation=generation)

    async def _on_runtime_failure(self, exc: Exception, *, generation: int) -> None:
        _ = exc
        await self._mark_faulted_if_current(generation, detach_provider=True)

    async def _on_terminal_stt_failure(
        self,
        exc: Exception,
        *,
        generation: int | None = None,
    ) -> None:
        _ = exc
        target_generation = self._generation if generation is None else generation
        async with self._lock:
            if self._is_superseded(target_generation):
                return
            if (
                self._desired_active
                and self._state == PeerChannelRuntimeState.RUNNING
                and self._stt is not None
            ):
                return
        await self._mark_faulted_if_current(target_generation, detach_provider=True)

    async def _mark_faulted_if_current(self, generation: int, *, detach_provider: bool) -> None:
        if self._is_superseded(generation):
            return
        await self._teardown_resources(target_state=PeerChannelRuntimeState.FAULTED)
        if not detach_provider:
            return
        if getattr(self.hub, "peer_stt", None) is None:
            await self.hub.replace_peer_stt_provider(None)

    async def _teardown_resources(self, *, target_state: PeerChannelRuntimeState) -> None:
        async with self._lock:
            loop_task = self._loop_task
            source = self._audio_source
            stt = self._stt
            self._loop_task = None
            self._audio_source = None
            self._vad = None
            self._stt = None
            self._signature = None

        await self._cancel_loop(loop_task)
        await self._close_if_possible(source)
        await self._close_peer_provider_if_current(stt)

        async with self._lock:
            if not self._desired_active and target_state == PeerChannelRuntimeState.FAULTED:
                self._state = PeerChannelRuntimeState.STOPPED
            else:
                self._state = target_state

    async def _cancel_loop(self, loop_task: asyncio.Task[None] | None) -> None:
        if loop_task is None:
            return
        if loop_task is asyncio.current_task():
            return
        loop_task.cancel()
        await asyncio.gather(loop_task, return_exceptions=True)

    async def _close_if_possible(self, resource: object | None) -> None:
        if resource is None or not hasattr(resource, "close"):
            return
        result = resource.close()
        if inspect.isawaitable(result):
            await result

    async def _close_peer_provider_if_current(self, stt: object | None) -> None:
        if stt is None:
            return
        if getattr(self.hub, "peer_stt", None) is stt:
            await self.hub.replace_peer_stt_provider(None)
            return
        await self._close_if_possible(stt)

    def _is_superseded(self, generation: int) -> bool:
        return generation != self._generation or not self._desired_active
