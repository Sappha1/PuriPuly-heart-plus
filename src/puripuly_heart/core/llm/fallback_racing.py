from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import time
from collections.abc import Awaitable
from dataclasses import dataclass, field
from uuid import UUID, uuid4

from puripuly_heart.core.llm.provider import LLMProvider
from puripuly_heart.core.runtime_logging import SessionRuntimeLoggingService
from puripuly_heart.domain.models import Translation

_PERSISTED_FALLBACK_PREFIX = "[Persisted][Fallback] "


@dataclass(slots=True)
class _BranchOutcome:
    result: Translation | None = None
    error: Exception | None = None
    elapsed_ms: int | None = None

    @property
    def resolved(self) -> bool:
        return self.result is not None or self.error is not None


@dataclass(slots=True)
class FallbackRacingLLMProvider(LLMProvider):
    primary: LLMProvider
    fallback: LLMProvider
    fallback_timeout_ms: int = 2000
    loser_grace_ms: int = 50
    runtime_logging: SessionRuntimeLoggingService | None = None
    _inflight_tasks: set[asyncio.Task[object]] = field(
        init=False,
        default_factory=set,
        repr=False,
    )
    _state_lock: asyncio.Lock = field(init=False, default_factory=asyncio.Lock, repr=False)
    _close_lock: asyncio.Lock = field(init=False, default_factory=asyncio.Lock, repr=False)
    _closed: bool = field(init=False, default=False, repr=False)
    _providers_closed: bool = field(init=False, default=False, repr=False)

    def __post_init__(self) -> None:
        self.fallback_timeout_ms = max(0, int(self.fallback_timeout_ms))
        self.loser_grace_ms = max(0, int(self.loser_grace_ms))

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
        params = {
            "utterance_id": utterance_id,
            "text": text,
            "system_prompt": system_prompt,
            "source_language": source_language,
            "target_language": target_language,
            "context": context,
        }
        race_id = uuid4().hex
        started_at = time.monotonic()
        primary_outcome = _BranchOutcome()
        fallback_outcome = _BranchOutcome()
        fallback_triggered = False
        dual_bill_candidate = False
        winner: str | None = None
        winner_wait_ms: int | None = None

        primary_task = await self._create_tracked_task(self.primary.translate(**params))
        timeout_task = await self._create_tracked_task(
            asyncio.sleep(self.fallback_timeout_ms / 1000.0)
        )
        fallback_task: asyncio.Task[object] | None = None

        try:
            while not fallback_triggered:
                done, _ = await asyncio.wait(
                    {task for task in (primary_task, timeout_task) if task is not None},
                    return_when=asyncio.FIRST_COMPLETED,
                )

                if primary_task in done:
                    await self._capture_outcome(
                        task=primary_task,
                        outcome=primary_outcome,
                        started_at=started_at,
                    )
                    await self._cancel_task(timeout_task)
                    timeout_task = None

                    if primary_outcome.result is not None:
                        self._emit_event(
                            race_id=race_id,
                            utterance_id=utterance_id,
                            event="primary_completed",
                            primary_elapsed_ms=primary_outcome.elapsed_ms,
                            fallback_elapsed_ms=None,
                            fallback_triggered=False,
                            winner="primary",
                            returned_source="primary",
                            total_user_wait_ms=primary_outcome.elapsed_ms,
                            primary_error=None,
                            fallback_error=None,
                            fallback_unusable=False,
                            dual_bill_candidate=False,
                        )
                        return primary_outcome.result

                    fallback_triggered = True
                    fallback_task = await self._create_tracked_task(
                        self.fallback.translate(**params)
                    )
                    await asyncio.sleep(0)
                    self._emit_event(
                        race_id=race_id,
                        utterance_id=utterance_id,
                        event="fallback_triggered",
                        trigger_reason="primary_error",
                        primary_elapsed_ms=primary_outcome.elapsed_ms,
                        fallback_elapsed_ms=None,
                        fallback_triggered=True,
                        winner=None,
                        returned_source=None,
                        total_user_wait_ms=None,
                        primary_error=self._format_error(primary_outcome.error),
                        fallback_error=None,
                        fallback_unusable=False,
                        dual_bill_candidate=False,
                    )
                    break

                if timeout_task in done:
                    timeout_task = None
                    fallback_triggered = True
                    dual_bill_candidate = not primary_outcome.resolved
                    self._emit_basic_fallback_triggered()
                    fallback_task = await self._create_tracked_task(
                        self.fallback.translate(**params)
                    )
                    await asyncio.sleep(0)
                    self._emit_event(
                        race_id=race_id,
                        utterance_id=utterance_id,
                        event="fallback_triggered",
                        trigger_reason="timeout",
                        primary_elapsed_ms=self._elapsed_ms(started_at),
                        fallback_elapsed_ms=None,
                        fallback_triggered=True,
                        winner=None,
                        returned_source=None,
                        total_user_wait_ms=None,
                        primary_error=None,
                        fallback_error=None,
                        fallback_unusable=False,
                        dual_bill_candidate=dual_bill_candidate,
                    )
                    break

            if fallback_task is None:
                raise RuntimeError("fallback race ended without starting fallback")

            while winner is None and not (
                primary_outcome.error is not None and fallback_outcome.error is not None
            ):
                waiters = {
                    task
                    for task, outcome in (
                        (primary_task, primary_outcome),
                        (fallback_task, fallback_outcome),
                    )
                    if task is not None and not outcome.resolved
                }
                if not waiters:
                    break

                done, _ = await asyncio.wait(waiters, return_when=asyncio.FIRST_COMPLETED)

                if primary_task in done and not primary_outcome.resolved:
                    await self._capture_outcome(
                        task=primary_task,
                        outcome=primary_outcome,
                        started_at=started_at,
                    )
                    if primary_outcome.result is not None and winner is None:
                        winner = "primary"
                        winner_wait_ms = primary_outcome.elapsed_ms

                if fallback_task in done and not fallback_outcome.resolved:
                    await self._capture_outcome(
                        task=fallback_task,
                        outcome=fallback_outcome,
                        started_at=started_at,
                    )
                    if fallback_outcome.result is not None and winner is None:
                        winner = "fallback"
                        winner_wait_ms = fallback_outcome.elapsed_ms

            if winner is None:
                if primary_outcome.error is None or fallback_outcome.error is None:
                    raise RuntimeError("fallback race ended without a successful result")
                combined_message = (
                    f"primary failed: {self._format_error(primary_outcome.error)}; "
                    f"fallback failed: {self._format_error(fallback_outcome.error)}"
                )
                self._emit_event(
                    race_id=race_id,
                    utterance_id=utterance_id,
                    event="race_failed",
                    primary_elapsed_ms=primary_outcome.elapsed_ms,
                    fallback_elapsed_ms=fallback_outcome.elapsed_ms,
                    fallback_triggered=True,
                    winner=None,
                    returned_source=None,
                    total_user_wait_ms=self._elapsed_ms(started_at),
                    primary_error=self._format_error(primary_outcome.error),
                    fallback_error=self._format_error(fallback_outcome.error),
                    fallback_unusable=False,
                    dual_bill_candidate=dual_bill_candidate,
                )
                raise self._race_failure_error(
                    primary_outcome.error, fallback_outcome.error, combined_message
                )

            await self._allow_loser_grace(
                started_at=started_at,
                primary_task=primary_task,
                primary_outcome=primary_outcome,
                fallback_task=fallback_task,
                fallback_outcome=fallback_outcome,
            )

            returned = primary_outcome.result if winner == "primary" else fallback_outcome.result
            if returned is None:
                raise RuntimeError("fallback race winner did not produce a translation")

            fallback_unusable = winner == "primary" and fallback_outcome.error is not None
            event = "fallback_unusable" if fallback_unusable else "race_finished"
            self._emit_event(
                race_id=race_id,
                utterance_id=utterance_id,
                event=event,
                primary_elapsed_ms=primary_outcome.elapsed_ms,
                fallback_elapsed_ms=fallback_outcome.elapsed_ms,
                fallback_triggered=True,
                winner=winner,
                returned_source=winner,
                total_user_wait_ms=winner_wait_ms,
                primary_error=self._format_error(primary_outcome.error),
                fallback_error=self._format_error(fallback_outcome.error),
                fallback_unusable=fallback_unusable,
                dual_bill_candidate=dual_bill_candidate,
            )
            return returned
        finally:
            await self._cancel_task(timeout_task)
            await self._cancel_task(primary_task)
            await self._cancel_task(fallback_task)

    async def close(self) -> None:
        async with self._close_lock:
            async with self._state_lock:
                self._closed = True
                inflight_tasks = list(self._inflight_tasks)
            for task in inflight_tasks:
                task.cancel()
            if inflight_tasks:
                await asyncio.gather(*inflight_tasks, return_exceptions=True)
            if self._providers_closed:
                return
            await self.primary.close()
            if self.fallback is not self.primary:
                await self.fallback.close()
            self._providers_closed = True

    async def _allow_loser_grace(
        self,
        *,
        started_at: float,
        primary_task: asyncio.Task[object] | None,
        primary_outcome: _BranchOutcome,
        fallback_task: asyncio.Task[object] | None,
        fallback_outcome: _BranchOutcome,
    ) -> None:
        pending = [
            task
            for task, outcome in (
                (primary_task, primary_outcome),
                (fallback_task, fallback_outcome),
            )
            if task is not None and not outcome.resolved
        ]
        if not pending:
            return

        if self.loser_grace_ms > 0:
            done, _ = await asyncio.wait(pending, timeout=self.loser_grace_ms / 1000.0)
            if primary_task in done and not primary_outcome.resolved:
                await self._capture_outcome(
                    task=primary_task,
                    outcome=primary_outcome,
                    started_at=started_at,
                )
            if fallback_task in done and not fallback_outcome.resolved:
                await self._capture_outcome(
                    task=fallback_task,
                    outcome=fallback_outcome,
                    started_at=started_at,
                )

        for task, outcome in (
            (primary_task, primary_outcome),
            (fallback_task, fallback_outcome),
        ):
            if task is None or outcome.resolved:
                continue
            outcome.elapsed_ms = outcome.elapsed_ms or self._elapsed_ms(started_at)
            await self._cancel_task(task)

    async def _capture_outcome(
        self,
        *,
        task: asyncio.Task[object],
        outcome: _BranchOutcome,
        started_at: float,
    ) -> None:
        if outcome.resolved:
            return
        if task.cancelled():
            raise asyncio.CancelledError
        exc = task.exception()
        outcome.elapsed_ms = self._elapsed_ms(started_at)
        if exc is None:
            outcome.result = task.result()
            return
        outcome.error = exc

    async def _create_tracked_task(self, awaitable: Awaitable[object]) -> asyncio.Task[object]:
        task = asyncio.create_task(awaitable)
        async with self._state_lock:
            if self._closed:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await task
                raise asyncio.CancelledError
            self._inflight_tasks.add(task)
        task.add_done_callback(self._inflight_tasks.discard)
        return task

    async def _cancel_task(self, task: asyncio.Task[object] | None) -> None:
        if task is None:
            return
        if task.done():
            self._consume_task_result(task)
            return
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await task

    def _emit_basic_fallback_triggered(self) -> None:
        if self.runtime_logging is None:
            return
        emit = getattr(self.runtime_logging, "emit_basic", None)
        if not callable(emit):
            return
        with contextlib.suppress(Exception):
            emit(f"Fallback triggered after {self.fallback_timeout_ms} ms", level=logging.INFO)

    def _emit_event(
        self,
        *,
        race_id: str,
        utterance_id: UUID,
        event: str,
        primary_elapsed_ms: int | None,
        fallback_elapsed_ms: int | None,
        fallback_triggered: bool,
        winner: str | None,
        returned_source: str | None,
        total_user_wait_ms: int | None,
        primary_error: str | None,
        fallback_error: str | None,
        fallback_unusable: bool,
        dual_bill_candidate: bool,
        trigger_reason: str | None = None,
    ) -> None:
        if self.runtime_logging is None:
            return
        emit = getattr(self.runtime_logging, "emit_persisted", None)
        if not callable(emit):
            return

        primary_model, primary_credential_source = self._provider_identity(self.primary)
        fallback_model, fallback_credential_source = self._provider_identity(self.fallback)
        payload = {
            "race_id": race_id,
            "utterance_id": str(utterance_id),
            "event": event,
            "primary_model": primary_model,
            "fallback_model": fallback_model,
            "primary_credential_source": primary_credential_source,
            "fallback_credential_source": fallback_credential_source,
            "primary_elapsed_ms": primary_elapsed_ms,
            "fallback_elapsed_ms": fallback_elapsed_ms,
            "fallback_triggered": fallback_triggered,
            "winner": winner,
            "returned_source": returned_source,
            "total_user_wait_ms": total_user_wait_ms,
            "primary_error": primary_error,
            "fallback_error": fallback_error,
            "fallback_unusable": fallback_unusable,
            "dual_bill_candidate": dual_bill_candidate,
        }
        if trigger_reason is not None:
            payload["trigger_reason"] = trigger_reason

        with contextlib.suppress(Exception):
            emit(
                _PERSISTED_FALLBACK_PREFIX
                + json.dumps(payload, ensure_ascii=False, sort_keys=True),
                level=logging.INFO,
            )

    @staticmethod
    def _elapsed_ms(started_at: float) -> int:
        return max(0, int(round((time.monotonic() - started_at) * 1000)))

    @classmethod
    def _provider_identity(cls, provider: object) -> tuple[str | None, str | None]:
        seen: set[int] = set()
        pending: list[object] = [provider]
        resolved_model: str | None = None
        resolved_source: str | None = None
        saw_openrouter = False

        while pending:
            current = pending.pop(0)
            if current is None or id(current) in seen:
                continue
            seen.add(id(current))

            node_model, node_source, node_is_openrouter = cls._identity_from_node(current)
            if resolved_model is None and node_model is not None:
                resolved_model = node_model
            if resolved_source is None and node_source is not None:
                resolved_source = node_source
            saw_openrouter = saw_openrouter or node_is_openrouter

            if resolved_model is not None and resolved_source is not None:
                return resolved_model, resolved_source

            for attr_name in ("_delegate", "inner"):
                wrapped = getattr(current, attr_name, None)
                if wrapped is not None and id(wrapped) not in seen:
                    pending.append(wrapped)

        if resolved_source is None and saw_openrouter:
            resolved_source = "openrouter"
        return resolved_model, resolved_source

    @classmethod
    def _identity_from_node(cls, provider: object) -> tuple[str | None, str | None, bool]:
        direct_model = cls._stringify_metadata(getattr(provider, "model", None))
        direct_source = cls._stringify_metadata(
            getattr(
                provider,
                "selected_source",
                getattr(provider, "credential_source", None),
            )
        )
        settings_model, settings_source = cls._settings_identity(
            getattr(provider, "settings", None)
        )
        release_model, release_source = cls._settings_identity(
            getattr(getattr(provider, "release_service", None), "settings", None)
        )

        model = direct_model or settings_model or release_model
        source = direct_source or settings_source or release_source
        is_openrouter = cls._is_openrouter_provider(provider) or any(
            value is not None
            for value in (release_model, release_source, settings_model, settings_source)
        )
        return model, source, is_openrouter

    @classmethod
    def _settings_identity(cls, settings: object | None) -> tuple[str | None, str | None]:
        if settings is None:
            return None, None
        openrouter_settings = getattr(settings, "openrouter", settings)
        model = cls._stringify_metadata(getattr(openrouter_settings, "llm_model", None))
        source = cls._stringify_metadata(getattr(openrouter_settings, "selected_source", None))
        return model, source

    @staticmethod
    def _is_openrouter_provider(provider: object) -> bool:
        provider_type = type(provider)
        haystack = f"{provider_type.__module__}.{provider_type.__name__}".lower()
        return "openrouter" in haystack

    @staticmethod
    def _stringify_metadata(value: object | None) -> str | None:
        if value is None:
            return None
        raw = getattr(value, "value", value)
        return str(raw)

    @staticmethod
    def _format_error(error: Exception | None) -> str | None:
        if error is None:
            return None
        message = str(error)
        if not message:
            return type(error).__name__
        return f"{type(error).__name__}: {message}"

    @staticmethod
    def _race_failure_error(
        primary_error: Exception, fallback_error: Exception, combined_message: str
    ) -> Exception:
        """Pick what to raise when both branches of the race fail.

        Provider errors that carry a `message_key` (e.g. ManagedOpenRouterUserFacingError)
        are meant to be shown to the user as-is — re-raise that instance instead of
        flattening both sides into a generic combined string, or a specific failure
        like "managed quota exhausted" gets lost as soon as a fallback is configured.
        """
        for error in (fallback_error, primary_error):
            if hasattr(error, "message_key"):
                return error
        return RuntimeError(combined_message)

    @staticmethod
    def _consume_task_result(task: asyncio.Task[object]) -> None:
        with contextlib.suppress(asyncio.CancelledError, Exception):
            task.result()
