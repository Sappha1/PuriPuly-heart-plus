"""OpenAI (ChatGPT) translation provider - the user's own API key from
platform.openai.com, over the Chat Completions API.

Current OpenAI models are reasoning models: reasoning tokens count toward the
output cap and the default effort is "medium", which is slow for live
translation. Each model therefore gets the lowest effort it accepts
(`gpt-6-luna` supports "none"; `gpt-6.1-sol` starts at "low") and a cap that
leaves room for that reasoning. If the API rejects the effort value (HTTP 400
naming reasoning_effort), the request is retried once without it and the
choice is remembered for the session.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import time
from datetime import date, timedelta
from pathlib import Path
from dataclasses import dataclass, field
from typing import Protocol
from uuid import UUID

import httpx

from puripuly_heart.core.runtime_logging import SessionRuntimeLoggingService
from puripuly_heart.domain.models import Translation
from puripuly_heart.providers.llm.messages import build_translation_user_message

logger = logging.getLogger(__name__)

OPENAI_BASE_URL = "https://api.openai.com/v1"
DEFAULT_OPENAI_MODEL = "gpt-6-luna"

# model -> (reasoning_effort, max_completion_tokens)
_MODEL_PROFILES: dict[str, tuple[str | None, int]] = {
    "gpt-6-luna": ("none", 1000),
    "gpt-6.1-sol": ("low", 4000),
}
_DEFAULT_PROFILE: tuple[str | None, int] = ("low", 4000)


def model_profile(model: str) -> tuple[str | None, int]:
    return _MODEL_PROFILES.get(model, _DEFAULT_PROFILE)


# USD per 1M tokens (input, output) - only used for the on-screen spend estimate
_MODEL_PRICES: dict[str, tuple[float, float]] = {
    "gpt-6-luna": (0.10, 0.50),
    "gpt-6.1-sol": (2.0, 10.0),
}


class OpenAIRequestError(RuntimeError):
    """A failed OpenAI call, keeping the API's own status, code and message."""

    def __init__(self, *, status: int, code: str = "", api_message: str = "") -> None:
        self.status = status
        self.code = code
        self.api_message = api_message
        super().__init__(
            f"OpenAI request failed (status={status}, message={api_message or 'unknown error'})"
        )

    @property
    def out_of_credits(self) -> bool:
        return is_out_of_credits(self.code, self.api_message)


def is_out_of_credits(code: str, message: str) -> bool:
    text = f"{code} {message}".lower()
    return (
        "insufficient_quota" in text
        or "no credits" in text
        or "exceeded your current quota" in text
        or "billing_hard_limit" in text
    )


@dataclass(slots=True)
class OpenAIUsageSnapshot:
    model: str = ""
    requests: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    spent_usd: float = 0.0
    # per-minute rate limits from the x-ratelimit-* headers of the last reply
    limit_tokens: int | None = None
    remaining_tokens: int | None = None
    limit_requests: int | None = None
    remaining_requests: int | None = None
    # prepaid credit balance, when the account exposes it to this key
    credit_available: float | None = None
    credit_granted: float | None = None
    out_of_credits: bool = False
    last_error: str = ""
    # rolling last 7 days (today included), from the on-disk ledger
    week_spent_usd: float = 0.0
    week_requests: int = 0


class OpenAIUsageTracker:
    """Session-wide OpenAI usage, fed by every request and read by the UI."""

    def __init__(self) -> None:
        self._snap = OpenAIUsageSnapshot()
        self._listeners: list = []
        self._balance_unsupported = False
        self._ledger_path: Path | None = None
        # {"YYYY-MM-DD": [usd, requests]}
        self._days: dict[str, list] = {}

    def attach_ledger(self, path: Path | str) -> None:
        """Load (and from now on keep) the per-day spend ledger at ``path``."""
        self._ledger_path = Path(path)
        days: dict[str, list] = {}
        with contextlib.suppress(Exception):
            raw = json.loads(self._ledger_path.read_text(encoding="utf-8"))
            for day, entry in (raw.get("days") or {}).items():
                date.fromisoformat(day)
                days[day] = [float(entry[0]), int(entry[1])]
        self._days = days
        self._prune_days()
        self._refresh_week()

    def _prune_days(self, today: date | None = None) -> None:
        cutoff = (today or date.today()) - timedelta(days=13)
        for day in list(self._days):
            try:
                keep = date.fromisoformat(day) >= cutoff
            except ValueError:
                keep = False
            if not keep:
                del self._days[day]

    def _refresh_week(self, today: date | None = None) -> None:
        today = today or date.today()
        start = today - timedelta(days=6)
        spent, requests = 0.0, 0
        for day, (usd, count) in self._days.items():
            with contextlib.suppress(ValueError):
                if start <= date.fromisoformat(day) <= today:
                    spent += usd
                    requests += count
        self._snap.week_spent_usd = spent
        self._snap.week_requests = requests

    def _add_to_ledger(self, usd: float, today: date | None = None) -> None:
        today = today or date.today()
        entry = self._days.setdefault(today.isoformat(), [0.0, 0])
        entry[0] += usd
        entry[1] += 1
        self._prune_days(today)
        self._refresh_week(today)
        if self._ledger_path is None:
            return
        with contextlib.suppress(Exception):
            self._ledger_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._ledger_path.with_suffix(".tmp")
            tmp.write_text(json.dumps({"days": self._days}), encoding="utf-8")
            os.replace(tmp, self._ledger_path)

    def snapshot(self) -> OpenAIUsageSnapshot:
        snap = self._snap
        return OpenAIUsageSnapshot(**{name: getattr(snap, name) for name in snap.__slots__})

    def add_listener(self, callback) -> None:
        if callback not in self._listeners:
            self._listeners.append(callback)

    def remove_listener(self, callback) -> None:
        with contextlib.suppress(ValueError):
            self._listeners.remove(callback)

    def _notify(self) -> None:
        snap = self.snapshot()
        for callback in list(self._listeners):
            with contextlib.suppress(Exception):
                callback(snap)

    def record_response(self, *, model: str, headers: object, data: object) -> None:
        snap = self._snap
        snap.model = model
        snap.requests += 1
        snap.out_of_credits = False
        snap.last_error = ""
        usage = data.get("usage") if isinstance(data, dict) else None
        cost = 0.0
        if isinstance(usage, dict):
            prompt = int(usage.get("prompt_tokens") or 0)
            completion = int(usage.get("completion_tokens") or 0)
            snap.input_tokens += prompt
            snap.output_tokens += completion
            price_in, price_out = _MODEL_PRICES.get(model, (0.0, 0.0))
            cost = (prompt * price_in + completion * price_out) / 1_000_000
            snap.spent_usd += cost
        self._add_to_ledger(cost)
        self._read_rate_limits(headers)
        self._notify()

    def record_error(self, *, model: str, headers: object, error: OpenAIRequestError) -> None:
        snap = self._snap
        snap.model = model
        snap.last_error = error.api_message or str(error)
        if error.out_of_credits:
            snap.out_of_credits = True
        self._read_rate_limits(headers)
        self._notify()

    def record_balance(self, available: float | None, granted: float | None) -> None:
        self._snap.credit_available = available
        self._snap.credit_granted = granted
        if available is not None and available > 0:
            self._snap.out_of_credits = False
        self._notify()

    def _read_rate_limits(self, headers: object) -> None:
        get = getattr(headers, "get", None)
        if not callable(get):
            return

        def _int(name: str) -> int | None:
            with contextlib.suppress(Exception):
                value = get(name)
                if value is not None and str(value).strip():
                    return int(float(str(value).strip()))
            return None

        snap = self._snap
        for attr, header in (
            ("limit_tokens", "x-ratelimit-limit-tokens"),
            ("remaining_tokens", "x-ratelimit-remaining-tokens"),
            ("limit_requests", "x-ratelimit-limit-requests"),
            ("remaining_requests", "x-ratelimit-remaining-requests"),
        ):
            value = _int(header)
            if value is not None:
                setattr(snap, attr, value)

    async def refresh_balance(self, api_key: str, base_url: str = OPENAI_BASE_URL) -> bool:
        """Ask OpenAI for the prepaid credit balance.

        There is no documented balance endpoint; the billing summary below works
        for some keys and answers 401/403/404 for others (project keys). One
        refusal turns the lookup off for the session - the meter then shows
        spend and rate limits only.
        """
        if self._balance_unsupported or not api_key:
            return False
        root = base_url.rstrip("/")
        if root.endswith("/v1"):
            root = root[:-3]
        urls = (
            f"{root}/v1/dashboard/billing/credit_grants",
            f"{root}/dashboard/billing/credit_grants",
        )
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                for url in urls:
                    response = await client.get(
                        url, headers={"Authorization": f"Bearer {api_key}"}
                    )
                    if response.status_code != 200:
                        continue
                    data = response.json()
                    available = data.get("total_available") if isinstance(data, dict) else None
                    granted = data.get("total_granted") if isinstance(data, dict) else None
                    if isinstance(available, (int, float)):
                        self.record_balance(
                            float(available),
                            float(granted) if isinstance(granted, (int, float)) else None,
                        )
                        return True
        except Exception:
            return False
        self._balance_unsupported = True
        return False

    def reset(self) -> None:
        self._snap = OpenAIUsageSnapshot()
        self._balance_unsupported = False
        self._notify()


USAGE = OpenAIUsageTracker()


def _emit(
    runtime_logging: SessionRuntimeLoggingService | None, message: str, *, level: int = logging.INFO
) -> None:
    if runtime_logging is not None:
        if level >= logging.ERROR:
            runtime_logging.emit_basic(message, level=level)
        else:
            runtime_logging.emit_basic(message)
        return
    logger.log(level, message)


def _build_system_prompt(*, system_prompt: str, source_language: str, target_language: str) -> str:
    return (
        system_prompt.format(source_language=source_language, target_language=target_language)
        if "{source_language}" in system_prompt
        else system_prompt
    )


def _extract_message_content(content: object) -> str:
    if isinstance(content, str):
        result = content.strip()
        if result:
            return result
        raise RuntimeError("OpenAI response contained empty message content")
    if isinstance(content, list):
        parts = [
            item["text"].strip()
            for item in content
            if isinstance(item, dict) and isinstance(item.get("text"), str) and item["text"].strip()
        ]
        if parts:
            return "\n".join(parts)
    raise RuntimeError("OpenAI response did not contain message content")


def _extract_error_message(data: object) -> str:
    if not isinstance(data, dict):
        return ""
    error = data.get("error")
    if isinstance(error, dict):
        nested = error.get("message")
        if isinstance(nested, str) and nested.strip():
            return nested.strip()
    if isinstance(error, str) and error.strip():
        return error.strip()
    message = data.get("message")
    if isinstance(message, str) and message.strip():
        return message.strip()
    return ""


def _extract_error_code(data: object) -> str:
    error = data.get("error") if isinstance(data, dict) else None
    if isinstance(error, dict):
        for key in ("code", "type"):
            value = error.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    return ""


def _finish_reason(data: object) -> str:
    if isinstance(data, dict):
        choices = data.get("choices")
        if isinstance(choices, list) and choices and isinstance(choices[0], dict):
            reason = choices[0].get("finish_reason")
            if isinstance(reason, str):
                return reason
    return ""


class OpenAIClient(Protocol):
    async def translate(
        self,
        *,
        text: str,
        system_prompt: str,
        source_language: str,
        target_language: str,
        context: str = "",
    ) -> str: ...

    async def close(self) -> None: ...


@dataclass(slots=True)
class OpenAILLMProvider:
    api_key: str
    base_url: str = OPENAI_BASE_URL
    model: str = DEFAULT_OPENAI_MODEL
    timeout: float = 30.0
    runtime_logging: SessionRuntimeLoggingService | None = None
    client: OpenAIClient | None = None
    _internal_client: OpenAIClient | None = field(init=False, default=None, repr=False)

    def _get_client(self) -> OpenAIClient:
        if self.client is not None:
            return self.client
        if self._internal_client is None:
            self._internal_client = HttpxOpenAIClient(
                api_key=self.api_key,
                model=self.model,
                base_url=self.base_url,
                timeout=self.timeout,
                runtime_logging=self.runtime_logging,
            )
        return self._internal_client

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
        translated = await self._get_client().translate(
            text=text,
            system_prompt=system_prompt,
            source_language=source_language,
            target_language=target_language,
            context=context,
        )
        return Translation(utterance_id=utterance_id, text=translated)

    async def warmup(self) -> None:
        """Open the HTTPS connection now and keep it open while idle, so the
        first translation after turning translation on skips the TLS setup."""
        client = self._get_client()
        warm = getattr(client, "keep_warm", None)
        if callable(warm):
            await warm()

    async def close(self) -> None:
        if self._internal_client is not None:
            await self._internal_client.close()
            self._internal_client = None

    @staticmethod
    async def verify_api_key(api_key: str, base_url: str = OPENAI_BASE_URL) -> bool:
        """A key is valid when it can list models - no tokens are spent."""
        if not api_key:
            return False
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.get(
                    f"{base_url}/models",
                    headers={"Authorization": f"Bearer {api_key}"},
                )
                return response.status_code == 200
        except Exception:
            return False


@dataclass(slots=True)
class HttpxOpenAIClient:
    api_key: str
    model: str
    base_url: str = OPENAI_BASE_URL
    timeout: float = 30.0
    runtime_logging: SessionRuntimeLoggingService | None = None
    _client: httpx.AsyncClient | None = field(init=False, default=None, repr=False)
    _client_lock: asyncio.Lock = field(init=False, default_factory=asyncio.Lock, repr=False)
    _send_reasoning: bool = field(init=False, default=True, repr=False)
    _warm_task: asyncio.Task | None = field(init=False, default=None, repr=False)
    _last_used: float = field(init=False, default=0.0, repr=False)

    # an idle pooled connection is dropped after this (httpx default: 5 s)
    _KEEPALIVE_EXPIRY_S = 120.0
    # while idle, touch the connection this often so it never expires
    _WARM_INTERVAL_S = 60.0

    async def _get_http_client(self) -> httpx.AsyncClient:
        if self._client is not None:
            return self._client
        async with self._client_lock:
            if self._client is None:
                self._client = httpx.AsyncClient(
                    timeout=self.timeout,
                    limits=httpx.Limits(keepalive_expiry=self._KEEPALIVE_EXPIRY_S),
                )
            return self._client

    async def _touch(self) -> None:
        """A free request (lists models, no tokens) that opens or refreshes
        the pooled HTTPS connection."""
        client = await self._get_http_client()
        with contextlib.suppress(Exception):
            await client.get(
                f"{self.base_url}/models",
                headers={"Authorization": f"Bearer {self.api_key}"},
            )
        self._last_used = time.monotonic()

    async def keep_warm(self) -> None:
        await self._touch()
        task = self._warm_task
        if task is None or task.done():
            self._warm_task = asyncio.create_task(self._warm_loop())

    async def _warm_loop(self) -> None:
        try:
            while self._client is not None:
                await asyncio.sleep(self._WARM_INTERVAL_S)
                if time.monotonic() - self._last_used >= self._WARM_INTERVAL_S - 5:
                    await self._touch()
        except asyncio.CancelledError:
            pass

    def _build_request_body(
        self,
        *,
        text: str,
        system_prompt: str,
        source_language: str,
        target_language: str,
        context: str,
    ) -> dict[str, object]:
        effort, cap = model_profile(self.model)
        body: dict[str, object] = {
            "model": self.model,
            "messages": [
                {
                    "role": "system",
                    "content": _build_system_prompt(
                        system_prompt=system_prompt,
                        source_language=source_language,
                        target_language=target_language,
                    ),
                },
                {"role": "user", "content": build_translation_user_message(text=text, context=context)},
            ],
            "max_completion_tokens": cap,
        }
        if effort and self._send_reasoning:
            body["reasoning_effort"] = effort
        return body

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}

    async def translate(
        self,
        *,
        text: str,
        system_prompt: str,
        source_language: str,
        target_language: str,
        context: str = "",
    ) -> str:
        _emit(
            self.runtime_logging,
            "[Basic][LLM] OpenAI request [translate][context=%s] %s -> %s: %r"
            % ("yes" if context else "no", source_language, target_language, text),
        )
        client = await self._get_http_client()
        for attempt in range(2):
            body = self._build_request_body(
                text=text,
                system_prompt=system_prompt,
                source_language=source_language,
                target_language=target_language,
                context=context,
            )
            response = await client.post(
                f"{self.base_url}/chat/completions", headers=self._headers(), json=body
            )
            if response.status_code == 200:
                break
            error_message = ""
            error_code = ""
            with contextlib.suppress(Exception):
                error_data = response.json()
                error_message = _extract_error_message(error_data)
                error_code = _extract_error_code(error_data)
            if not error_message:
                with contextlib.suppress(Exception):
                    error_message = response.text[:200]
            if (
                attempt == 0
                and response.status_code == 400
                and "reasoning_effort" in body
                and "reasoning" in error_message.lower()
            ):
                # this model rejects the effort value - use its default from now on
                self._send_reasoning = False
                _emit(
                    self.runtime_logging,
                    "[Basic][LLM] OpenAI model %s rejected reasoning_effort=%s; "
                    "retrying with the model default" % (self.model, body["reasoning_effort"]),
                    level=logging.WARNING,
                )
                continue
            _emit(
                self.runtime_logging,
                "[Basic][LLM] OpenAI request failed [translate]: status=%s message=%s"
                % (response.status_code, error_message or "unknown error"),
                level=logging.ERROR,
            )
            error = OpenAIRequestError(
                status=response.status_code, code=error_code, api_message=error_message
            )
            USAGE.record_error(model=self.model, headers=response.headers, error=error)
            raise error

        self._last_used = time.monotonic()
        data = response.json()
        USAGE.record_response(model=self.model, headers=response.headers, data=data)
        choices = data.get("choices", []) if isinstance(data, dict) else []
        if not choices:
            raise RuntimeError("OpenAI response did not contain choices")
        message = choices[0].get("message", {}) if isinstance(choices[0], dict) else {}
        content = message.get("content") if isinstance(message, dict) else None
        if _finish_reason(data) == "length" and not (isinstance(content, str) and content.strip()):
            raise RuntimeError("OpenAI response was cut off by the output token limit")
        result = _extract_message_content(content)
        _emit(self.runtime_logging, "[Basic][LLM] OpenAI response [translate]: %r" % result)
        return result

    async def close(self) -> None:
        task, self._warm_task = self._warm_task, None
        if task is not None and not task.done():
            task.cancel()
        async with self._client_lock:
            client = self._client
            self._client = None
        if client is not None:
            await client.aclose()
