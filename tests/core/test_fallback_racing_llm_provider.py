from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest

from puripuly_heart.config.settings import (
    AppSettings,
    OpenRouterCredentialSource,
    OpenRouterLLMModel,
    OpenRouterSelectionAlias,
    OpenRouterSettings,
)
from puripuly_heart.core.llm import FallbackRacingLLMProvider
from puripuly_heart.core.llm.provider import LLMProvider
from puripuly_heart.core.managed_openrouter_release import (
    ManagedOpenRouterLLMProvider,
    ManagedOpenRouterReleaseBehavior,
    ManagedOpenRouterReleaseResult,
)
from puripuly_heart.domain.models import Translation
from puripuly_heart.providers.llm.openrouter import OpenRouterLLMProvider

_PERSISTED_FALLBACK_PREFIX = "[Persisted][Fallback] "


def _translation_kwargs(*, utterance_id: UUID) -> dict[str, object]:
    return {
        "utterance_id": utterance_id,
        "text": "안녕",
        "system_prompt": "PROMPT",
        "source_language": "ko",
        "target_language": "en",
        "context": "ctx",
    }


@dataclass(slots=True)
class _PersistedRuntimeLogging:
    basic_messages: list[tuple[int, str]] = field(default_factory=list)
    persisted_messages: list[tuple[int, str]] = field(default_factory=list)

    def emit_basic(self, message: str, *, level: int = logging.INFO) -> None:
        self.basic_messages.append((level, message))

    def emit_persisted(self, message: str, *, level: int = logging.INFO) -> None:
        self.persisted_messages.append((level, message))


def _persisted_payloads(runtime_logging: _PersistedRuntimeLogging) -> list[dict[str, object]]:
    payloads: list[dict[str, object]] = []
    for _, message in runtime_logging.persisted_messages:
        assert message.startswith(_PERSISTED_FALLBACK_PREFIX)
        payloads.append(json.loads(message[len(_PERSISTED_FALLBACK_PREFIX) :]))
    return payloads


def _payload_for_event(
    runtime_logging: _PersistedRuntimeLogging,
    *,
    event: str,
) -> dict[str, object]:
    return next(
        payload for payload in _persisted_payloads(runtime_logging) if payload["event"] == event
    )


async def _wait_until(predicate: Callable[[], bool], *, timeout: float = 0.2) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    while not predicate():
        if asyncio.get_running_loop().time() >= deadline:
            raise TimeoutError("condition was not met before timeout")
        await asyncio.sleep(0.001)


@dataclass(slots=True)
class FakeLLM(LLMProvider):
    translated_text: str = "translated"
    delay_s: float = 0.0
    gate: asyncio.Event | None = None
    error: Exception | None = None
    model: str | None = None
    selected_source: str | None = None
    translate_calls: list[dict[str, object]] = field(default_factory=list)
    close_calls: int = 0
    translate_started: asyncio.Event = field(default_factory=asyncio.Event, repr=False)
    translate_cancelled: asyncio.Event = field(default_factory=asyncio.Event, repr=False)
    translate_finished: asyncio.Event = field(default_factory=asyncio.Event, repr=False)

    async def translate(
        self,
        *,
        utterance_id: UUID,
        text: str,
        system_prompt: str,
        source_language: str,
        target_language: str,
        context: str = "",
    ) -> Translation:
        self.translate_calls.append(
            {
                "utterance_id": utterance_id,
                "text": text,
                "system_prompt": system_prompt,
                "source_language": source_language,
                "target_language": target_language,
                "context": context,
            }
        )
        self.translate_started.set()
        try:
            if self.gate is not None:
                await self.gate.wait()
            if self.delay_s:
                await asyncio.sleep(self.delay_s)
            if self.error is not None:
                raise self.error
            translation = Translation(
                utterance_id=utterance_id,
                text=self.translated_text,
                source_text=text,
                source_language=source_language,
                target_language=target_language,
            )
            self.translate_finished.set()
            return translation
        except asyncio.CancelledError:
            self.translate_cancelled.set()
            raise

    async def close(self) -> None:
        self.close_calls += 1


@dataclass(slots=True)
class _OpenRouterDelegateShapeLLM(LLMProvider):
    model: str
    translated_text: str = "translated"
    close_calls: int = 0

    async def translate(
        self,
        *,
        utterance_id: UUID,
        text: str,
        system_prompt: str,
        source_language: str,
        target_language: str,
        context: str = "",
    ) -> Translation:
        _ = system_prompt, context
        return Translation(
            utterance_id=utterance_id,
            text=self.translated_text,
            source_text=text,
            source_language=source_language,
            target_language=target_language,
        )

    async def close(self) -> None:
        self.close_calls += 1


@dataclass(slots=True)
class _EquivalentLazyWrapperLLM(LLMProvider):
    factory: Callable[[], LLMProvider]
    _delegate: LLMProvider | None = field(init=False, default=None, repr=False)
    _delegate_lock: asyncio.Lock = field(init=False, default_factory=asyncio.Lock, repr=False)

    async def _ensure_delegate(self) -> LLMProvider:
        if self._delegate is not None:
            return self._delegate

        async with self._delegate_lock:
            if self._delegate is None:
                self._delegate = self.factory()
            return self._delegate

    async def translate(
        self,
        *,
        utterance_id: UUID,
        text: str,
        system_prompt: str,
        source_language: str,
        target_language: str,
        context: str = "",
    ) -> Translation:
        delegate = await self._ensure_delegate()
        return await delegate.translate(
            utterance_id=utterance_id,
            text=text,
            system_prompt=system_prompt,
            source_language=source_language,
            target_language=target_language,
            context=context,
        )

    async def close(self) -> None:
        if self._delegate is not None:
            await self._delegate.close()


def _managed_openrouter_settings(
    *,
    model: OpenRouterLLMModel = OpenRouterLLMModel.GEMMA_4_26B_A4B_IT,
    source: OpenRouterCredentialSource = OpenRouterCredentialSource.MANAGED,
    alias: OpenRouterSelectionAlias = OpenRouterSelectionAlias.GEMMA4_MANAGED,
) -> AppSettings:
    return AppSettings(
        openrouter=OpenRouterSettings(
            llm_model=model,
            selected_source=source,
            selection_alias=alias,
        )
    )


@dataclass(slots=True)
class _ManagedReleaseServiceStub:
    settings: AppSettings
    api_key: str = "managed-openrouter-key"

    async def ensure_key_for_llm_start(self) -> ManagedOpenRouterReleaseResult:
        return ManagedOpenRouterReleaseResult(
            behavior=ManagedOpenRouterReleaseBehavior.READY,
            message_key="managed_release.ready",
            api_key=self.api_key,
        )


def test_provider_identity_recovers_managed_wrapper_settings_before_delegate_init() -> None:
    provider = ManagedOpenRouterLLMProvider(
        release_service=SimpleNamespace(settings=_managed_openrouter_settings()),
        delegate_factory=lambda api_key: FakeLLM(translated_text=api_key),
    )

    identity = FallbackRacingLLMProvider._provider_identity(provider)

    assert identity == (
        OpenRouterLLMModel.GEMMA_4_26B_A4B_IT.value,
        OpenRouterCredentialSource.MANAGED.value,
    )


@pytest.mark.asyncio
async def test_provider_identity_prefers_delegate_model_after_managed_delegate_init() -> None:
    provider = ManagedOpenRouterLLMProvider(
        release_service=_ManagedReleaseServiceStub(settings=_managed_openrouter_settings()),
        delegate_factory=lambda api_key: FakeLLM(
            translated_text=api_key,
            model=OpenRouterLLMModel.QWEN_35_FLASH_02_23.value,
            selected_source="openrouter",
        ),
    )

    await provider.translate(**_translation_kwargs(utterance_id=uuid4()))

    identity = FallbackRacingLLMProvider._provider_identity(provider)

    assert identity == (
        OpenRouterLLMModel.QWEN_35_FLASH_02_23.value,
        "openrouter",
    )

    await provider.close()


@pytest.mark.asyncio
async def test_provider_identity_preserves_wrapper_source_for_openrouter_delegate_shape() -> None:
    provider = ManagedOpenRouterLLMProvider(
        release_service=_ManagedReleaseServiceStub(settings=_managed_openrouter_settings()),
        delegate_factory=lambda api_key: _OpenRouterDelegateShapeLLM(
            model=OpenRouterLLMModel.QWEN_35_FLASH_02_23.value,
            translated_text=api_key,
        ),
    )

    await provider.translate(**_translation_kwargs(utterance_id=uuid4()))

    identity = FallbackRacingLLMProvider._provider_identity(provider)

    assert identity == (
        OpenRouterLLMModel.QWEN_35_FLASH_02_23.value,
        OpenRouterCredentialSource.MANAGED.value,
    )

    await provider.close()


def test_provider_identity_reports_openrouter_source_for_direct_openrouter_provider() -> None:
    provider = OpenRouterLLMProvider(
        api_key="openrouter-key",
        model=OpenRouterLLMModel.QWEN_35_FLASH_02_23.value,
    )

    identity = FallbackRacingLLMProvider._provider_identity(provider)

    assert identity == (OpenRouterLLMModel.QWEN_35_FLASH_02_23.value, "openrouter")


@pytest.mark.asyncio
async def test_fallback_racer_persists_managed_wrapper_identity_from_release_service_settings() -> (
    None
):
    primary = ManagedOpenRouterLLMProvider(
        release_service=_ManagedReleaseServiceStub(settings=_managed_openrouter_settings()),
        delegate_factory=lambda api_key: FakeLLM(translated_text=f"primary:{api_key}"),
    )
    fallback = FakeLLM(
        translated_text="fallback",
        model=OpenRouterLLMModel.DEEPSEEK_V4_FLASH.value,
        selected_source=OpenRouterCredentialSource.MANAGED.value,
    )
    runtime_logging = _PersistedRuntimeLogging()
    provider = FallbackRacingLLMProvider(
        primary=primary,
        fallback=fallback,
        runtime_logging=runtime_logging,
    )

    provider._emit_event(
        race_id="race-1",
        utterance_id=uuid4(),
        event="primary_completed",
        primary_elapsed_ms=10,
        fallback_elapsed_ms=None,
        fallback_triggered=False,
        winner="primary",
        returned_source="primary",
        total_user_wait_ms=10,
        primary_error=None,
        fallback_error=None,
        fallback_unusable=False,
        dual_bill_candidate=False,
    )

    payload = _payload_for_event(runtime_logging, event="primary_completed")
    assert payload["primary_model"] == OpenRouterLLMModel.GEMMA_4_26B_A4B_IT.value
    assert payload["primary_credential_source"] == OpenRouterCredentialSource.MANAGED.value
    assert payload["fallback_model"] == OpenRouterLLMModel.DEEPSEEK_V4_FLASH.value
    assert payload["fallback_credential_source"] == OpenRouterCredentialSource.MANAGED.value

    await provider.close()


@pytest.mark.asyncio
async def test_fallback_racer_persisted_event_prefers_delegate_model_over_release_settings() -> (
    None
):
    primary = ManagedOpenRouterLLMProvider(
        release_service=_ManagedReleaseServiceStub(settings=_managed_openrouter_settings()),
        delegate_factory=lambda api_key: FakeLLM(
            translated_text=f"primary:{api_key}",
            model=OpenRouterLLMModel.QWEN_35_FLASH_02_23.value,
            selected_source="openrouter",
        ),
    )
    fallback = FakeLLM(
        translated_text="fallback",
        model=OpenRouterLLMModel.DEEPSEEK_V4_FLASH.value,
        selected_source=OpenRouterCredentialSource.MANAGED.value,
    )
    runtime_logging = _PersistedRuntimeLogging()
    provider = FallbackRacingLLMProvider(
        primary=primary,
        fallback=fallback,
        runtime_logging=runtime_logging,
    )

    await primary.translate(**_translation_kwargs(utterance_id=uuid4()))

    provider._emit_event(
        race_id="race-1",
        utterance_id=uuid4(),
        event="primary_completed",
        primary_elapsed_ms=10,
        fallback_elapsed_ms=None,
        fallback_triggered=False,
        winner="primary",
        returned_source="primary",
        total_user_wait_ms=10,
        primary_error=None,
        fallback_error=None,
        fallback_unusable=False,
        dual_bill_candidate=False,
    )

    payload = _payload_for_event(runtime_logging, event="primary_completed")
    assert payload["primary_model"] == OpenRouterLLMModel.QWEN_35_FLASH_02_23.value
    assert payload["primary_credential_source"] == "openrouter"
    assert payload["fallback_model"] == OpenRouterLLMModel.DEEPSEEK_V4_FLASH.value
    assert payload["fallback_credential_source"] == OpenRouterCredentialSource.MANAGED.value

    await provider.close()


@pytest.mark.asyncio
async def test_fallback_racer_lazy_wrapper_populates_fallback_trigger_identity() -> None:
    primary = FakeLLM(gate=asyncio.Event())
    fallback = _EquivalentLazyWrapperLLM(
        factory=lambda: ManagedOpenRouterLLMProvider(
            release_service=_ManagedReleaseServiceStub(
                settings=_managed_openrouter_settings(
                    model=OpenRouterLLMModel.QWEN_35_FLASH_02_23,
                    alias=OpenRouterSelectionAlias.QWEN35_FLASH_MANAGED,
                )
            ),
            delegate_factory=lambda api_key: FakeLLM(translated_text=f"fallback:{api_key}"),
        )
    )
    runtime_logging = _PersistedRuntimeLogging()
    provider = FallbackRacingLLMProvider(
        primary=primary,
        fallback=fallback,
        fallback_timeout_ms=10,
        runtime_logging=runtime_logging,
    )
    translate_task = asyncio.create_task(
        provider.translate(**_translation_kwargs(utterance_id=uuid4()))
    )

    try:
        await _wait_until(
            lambda: any(
                payload["event"] == "fallback_triggered"
                for payload in _persisted_payloads(runtime_logging)
            )
        )

        payload = _payload_for_event(runtime_logging, event="fallback_triggered")
        assert payload["fallback_model"] == OpenRouterLLMModel.QWEN_35_FLASH_02_23.value
        assert payload["fallback_credential_source"] == OpenRouterCredentialSource.MANAGED.value
    finally:
        translate_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await translate_task
        await provider.close()


@pytest.mark.asyncio
async def test_fallback_racer_starts_primary_immediately_and_returns_primary_before_timeout() -> (
    None
):
    primary_gate = asyncio.Event()
    primary = FakeLLM(translated_text="primary", gate=primary_gate)
    fallback = FakeLLM(translated_text="fallback")
    provider = FallbackRacingLLMProvider(
        primary=primary,
        fallback=fallback,
        fallback_timeout_ms=50,
    )
    utterance_id = uuid4()

    translate_task = asyncio.create_task(
        provider.translate(**_translation_kwargs(utterance_id=utterance_id))
    )
    await asyncio.wait_for(primary.translate_started.wait(), timeout=0.2)

    assert primary.translate_calls == [
        {
            "utterance_id": utterance_id,
            "text": "안녕",
            "system_prompt": "PROMPT",
            "source_language": "ko",
            "target_language": "en",
            "context": "ctx",
        }
    ]
    assert fallback.translate_calls == []

    primary_gate.set()
    result = await asyncio.wait_for(translate_task, timeout=0.2)

    assert result.text == "primary"
    assert fallback.translate_calls == []

    await provider.close()


@pytest.mark.asyncio
async def test_fallback_racer_persists_fast_primary_success_event() -> None:
    primary = FakeLLM(
        translated_text="primary",
        model="google/gemma-4-26b-a4b-it",
        selected_source="byok",
    )
    fallback = FakeLLM(
        translated_text="fallback",
        model="google/gemini-2.5-flash-lite",
        selected_source="byok",
    )
    runtime_logging = _PersistedRuntimeLogging()
    provider = FallbackRacingLLMProvider(
        primary=primary,
        fallback=fallback,
        fallback_timeout_ms=50,
        runtime_logging=runtime_logging,
    )
    utterance_id = uuid4()

    result = await provider.translate(**_translation_kwargs(utterance_id=utterance_id))

    payload = _payload_for_event(runtime_logging, event="primary_completed")
    assert result.text == "primary"
    assert payload["race_id"]
    assert payload["utterance_id"] == str(utterance_id)
    assert payload["winner"] == "primary"
    assert payload["returned_source"] == "primary"
    assert payload["primary_model"] == "google/gemma-4-26b-a4b-it"
    assert payload["fallback_model"] == "google/gemini-2.5-flash-lite"
    assert payload["primary_credential_source"] == "byok"
    assert payload["fallback_credential_source"] == "byok"
    assert isinstance(payload["primary_elapsed_ms"], int)
    assert payload["fallback_elapsed_ms"] is None
    assert payload["fallback_triggered"] is False
    assert isinstance(payload["total_user_wait_ms"], int)
    assert payload["primary_error"] is None
    assert payload["fallback_error"] is None
    assert payload["fallback_unusable"] is False
    assert payload["dual_bill_candidate"] is False
    assert runtime_logging.basic_messages == []

    await provider.close()


@pytest.mark.asyncio
async def test_fallback_racer_returns_fallback_after_timeout_and_emits_persisted_events() -> None:
    primary = FakeLLM(
        translated_text="primary",
        gate=asyncio.Event(),
        model="google/gemma-4-26b-a4b-it",
        selected_source="managed",
    )
    fallback = FakeLLM(
        translated_text="fallback",
        model="google/gemini-2.5-flash-lite",
        selected_source="managed",
    )
    runtime_logging = _PersistedRuntimeLogging()
    provider = FallbackRacingLLMProvider(
        primary=primary,
        fallback=fallback,
        fallback_timeout_ms=10,
        runtime_logging=runtime_logging,
    )
    utterance_id = uuid4()

    result = await asyncio.wait_for(
        provider.translate(**_translation_kwargs(utterance_id=utterance_id)), timeout=0.2
    )

    assert result.text == "fallback"
    assert primary.translate_started.is_set()
    assert fallback.translate_started.is_set()
    assert primary.translate_cancelled.is_set()
    fallback_triggered = _payload_for_event(runtime_logging, event="fallback_triggered")
    race_finished = _payload_for_event(runtime_logging, event="race_finished")
    assert fallback_triggered["race_id"] == race_finished["race_id"]
    assert fallback_triggered["utterance_id"] == str(utterance_id)
    assert fallback_triggered["primary_model"] == "google/gemma-4-26b-a4b-it"
    assert fallback_triggered["fallback_model"] == "google/gemini-2.5-flash-lite"
    assert fallback_triggered["primary_credential_source"] == "managed"
    assert fallback_triggered["fallback_credential_source"] == "managed"
    assert isinstance(fallback_triggered["primary_elapsed_ms"], int)
    assert fallback_triggered["fallback_triggered"] is True
    assert fallback_triggered["dual_bill_candidate"] is True
    assert race_finished["winner"] == "fallback"
    assert race_finished["returned_source"] == "fallback"
    assert isinstance(race_finished["primary_elapsed_ms"], int)
    assert isinstance(race_finished["fallback_elapsed_ms"], int)
    assert isinstance(race_finished["total_user_wait_ms"], int)
    assert race_finished["primary_error"] is None
    assert race_finished["fallback_error"] is None
    assert race_finished["fallback_unusable"] is False
    assert runtime_logging.basic_messages == [(logging.INFO, "Fallback triggered after 10 ms")]

    await provider.close()


@pytest.mark.asyncio
async def test_fallback_racer_returns_fallback_after_primary_exception() -> None:
    primary = FakeLLM(
        error=RuntimeError("primary failed"),
        model="google/gemma-4-26b-a4b-it",
        selected_source="managed",
    )
    fallback = FakeLLM(
        translated_text="fallback",
        model="google/gemini-2.5-flash-lite",
        selected_source="managed",
    )
    runtime_logging = _PersistedRuntimeLogging()
    provider = FallbackRacingLLMProvider(
        primary=primary,
        fallback=fallback,
        fallback_timeout_ms=100,
        runtime_logging=runtime_logging,
    )
    utterance_id = uuid4()

    result = await asyncio.wait_for(
        provider.translate(**_translation_kwargs(utterance_id=utterance_id)), timeout=0.2
    )

    assert result.text == "fallback"
    assert primary.translate_started.is_set()
    assert fallback.translate_started.is_set()
    fallback_triggered = _payload_for_event(runtime_logging, event="fallback_triggered")
    race_finished = _payload_for_event(runtime_logging, event="race_finished")
    assert fallback_triggered["primary_error"] == "RuntimeError: primary failed"
    assert fallback_triggered["fallback_triggered"] is True
    assert race_finished["winner"] == "fallback"
    assert race_finished["returned_source"] == "fallback"
    assert race_finished["primary_error"] == "RuntimeError: primary failed"
    assert race_finished["fallback_error"] is None
    assert runtime_logging.basic_messages == []

    await provider.close()


@pytest.mark.asyncio
async def test_fallback_racer_can_still_return_primary_after_fallback_starts() -> None:
    primary_gate = asyncio.Event()
    primary = FakeLLM(translated_text="primary", gate=primary_gate)
    fallback = FakeLLM(translated_text="fallback", delay_s=0.01)
    runtime_logging = _PersistedRuntimeLogging()
    provider = FallbackRacingLLMProvider(
        primary=primary,
        fallback=fallback,
        fallback_timeout_ms=1,
        loser_grace_ms=50,
        runtime_logging=runtime_logging,
    )
    translate_task = asyncio.create_task(
        provider.translate(**_translation_kwargs(utterance_id=uuid4()))
    )

    await asyncio.wait_for(primary.translate_started.wait(), timeout=0.2)
    await asyncio.wait_for(fallback.translate_started.wait(), timeout=0.2)
    primary_gate.set()

    result = await asyncio.wait_for(translate_task, timeout=0.3)

    assert result.text == "primary"
    assert fallback.translate_started.is_set()
    assert fallback.translate_finished.is_set()
    race_finished = _payload_for_event(runtime_logging, event="race_finished")
    assert race_finished["winner"] == "primary"
    assert race_finished["returned_source"] == "primary"
    assert race_finished["fallback_triggered"] is True
    assert isinstance(race_finished["primary_elapsed_ms"], int)
    assert isinstance(race_finished["fallback_elapsed_ms"], int)
    assert race_finished["fallback_unusable"] is False

    await provider.close()


@pytest.mark.asyncio
async def test_fallback_racer_persists_unusable_fallback_when_primary_still_succeeds() -> None:
    primary_gate = asyncio.Event()
    primary = FakeLLM(translated_text="primary", gate=primary_gate)
    fallback = FakeLLM(error=RuntimeError("fallback unavailable"), delay_s=0.0)
    runtime_logging = _PersistedRuntimeLogging()
    provider = FallbackRacingLLMProvider(
        primary=primary,
        fallback=fallback,
        fallback_timeout_ms=1,
        runtime_logging=runtime_logging,
    )
    translate_task = asyncio.create_task(
        provider.translate(**_translation_kwargs(utterance_id=uuid4()))
    )

    await asyncio.wait_for(primary.translate_started.wait(), timeout=0.2)
    await asyncio.wait_for(fallback.translate_started.wait(), timeout=0.2)
    primary_gate.set()

    result = await asyncio.wait_for(translate_task, timeout=0.3)

    payload = _payload_for_event(runtime_logging, event="fallback_unusable")
    assert result.text == "primary"
    assert payload["winner"] == "primary"
    assert payload["returned_source"] == "primary"
    assert payload["fallback_triggered"] is True
    assert payload["fallback_unusable"] is True
    assert payload["primary_error"] is None
    assert payload["fallback_error"] == "RuntimeError: fallback unavailable"

    await provider.close()


@pytest.mark.asyncio
async def test_fallback_racer_close_cancels_inflight_branches() -> None:
    primary = FakeLLM(gate=asyncio.Event())
    fallback = FakeLLM(gate=asyncio.Event())
    provider = FallbackRacingLLMProvider(
        primary=primary,
        fallback=fallback,
        fallback_timeout_ms=10,
    )

    translate_task = asyncio.create_task(
        provider.translate(**_translation_kwargs(utterance_id=uuid4()))
    )
    await asyncio.wait_for(primary.translate_started.wait(), timeout=0.2)
    await asyncio.wait_for(fallback.translate_started.wait(), timeout=0.2)

    await provider.close()

    with pytest.raises(asyncio.CancelledError):
        await translate_task
    assert primary.translate_cancelled.is_set()
    assert fallback.translate_cancelled.is_set()
    assert primary.close_calls == 1
    assert fallback.close_calls == 1


@pytest.mark.asyncio
async def test_fallback_racer_caller_cancel_cancels_inflight_branches_without_close() -> None:
    primary = FakeLLM(gate=asyncio.Event())
    fallback = FakeLLM(gate=asyncio.Event())
    provider = FallbackRacingLLMProvider(
        primary=primary,
        fallback=fallback,
        fallback_timeout_ms=10,
    )

    translate_task = asyncio.create_task(
        provider.translate(**_translation_kwargs(utterance_id=uuid4()))
    )

    try:
        await asyncio.wait_for(primary.translate_started.wait(), timeout=0.2)
        await asyncio.wait_for(fallback.translate_started.wait(), timeout=0.2)

        translate_task.cancel()

        with pytest.raises(asyncio.CancelledError):
            await translate_task

        await asyncio.wait_for(primary.translate_cancelled.wait(), timeout=0.2)
        await asyncio.wait_for(fallback.translate_cancelled.wait(), timeout=0.2)
        assert provider._inflight_tasks == set()
        assert primary.close_calls == 0
        assert fallback.close_calls == 0
    finally:
        await provider.close()


@pytest.mark.asyncio
async def test_fallback_racer_waits_for_fallback_when_primary_fails_after_fallback_has_started() -> (
    None
):
    primary_gate = asyncio.Event()
    primary = FakeLLM(error=RuntimeError("primary boom"), gate=primary_gate)
    fallback = FakeLLM(translated_text="fallback", delay_s=0.01)
    runtime_logging = _PersistedRuntimeLogging()
    provider = FallbackRacingLLMProvider(
        primary=primary,
        fallback=fallback,
        fallback_timeout_ms=1,
        runtime_logging=runtime_logging,
    )
    translate_task = asyncio.create_task(
        provider.translate(**_translation_kwargs(utterance_id=uuid4()))
    )

    await asyncio.wait_for(primary.translate_started.wait(), timeout=0.2)
    await asyncio.wait_for(fallback.translate_started.wait(), timeout=0.2)
    primary_gate.set()

    result = await asyncio.wait_for(translate_task, timeout=0.3)

    payload = _payload_for_event(runtime_logging, event="race_finished")
    assert result.text == "fallback"
    assert payload["winner"] == "fallback"
    assert payload["returned_source"] == "fallback"
    assert payload["primary_error"] == "RuntimeError: primary boom"
    assert payload["fallback_error"] is None
    assert payload["fallback_triggered"] is True

    await provider.close()


@pytest.mark.asyncio
async def test_fallback_racer_preserves_both_errors_when_both_branches_fail() -> None:
    primary_gate = asyncio.Event()
    primary = FakeLLM(error=RuntimeError("primary boom"), gate=primary_gate)
    fallback = FakeLLM(error=RuntimeError("fallback boom"), delay_s=0.0)
    runtime_logging = _PersistedRuntimeLogging()
    provider = FallbackRacingLLMProvider(
        primary=primary,
        fallback=fallback,
        fallback_timeout_ms=1,
        runtime_logging=runtime_logging,
    )
    translate_task = asyncio.create_task(
        provider.translate(**_translation_kwargs(utterance_id=uuid4()))
    )

    await asyncio.wait_for(primary.translate_started.wait(), timeout=0.2)
    await asyncio.wait_for(fallback.translate_started.wait(), timeout=0.2)
    primary_gate.set()

    with pytest.raises(
        RuntimeError,
        match="primary failed: RuntimeError: primary boom; fallback failed: RuntimeError: fallback boom",
    ):
        await asyncio.wait_for(translate_task, timeout=0.3)

    payload = _payload_for_event(runtime_logging, event="race_failed")
    assert payload["winner"] is None
    assert payload["returned_source"] is None
    assert payload["primary_error"] == "RuntimeError: primary boom"
    assert payload["fallback_error"] == "RuntimeError: fallback boom"
    assert payload["fallback_triggered"] is True
    assert payload["dual_bill_candidate"] is True

    await provider.close()
