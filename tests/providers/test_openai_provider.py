from __future__ import annotations

import asyncio
from uuid import uuid4

import pytest

from puripuly_heart.providers.llm import openai_chat
from puripuly_heart.providers.llm.openai_chat import (
    USAGE,
    HttpxOpenAIClient,
    OpenAILLMProvider,
    OpenAIRequestError,
    OpenAIUsageTracker,
    model_profile,
)

PLACEHOLDER_KEY = "test-key-placeholder"


class FakeResponse:
    def __init__(
        self,
        *,
        status_code: int = 200,
        data: dict | None = None,
        text: str = "",
        headers: dict | None = None,
    ):
        self.status_code = status_code
        self.headers = headers or {}
        self._data = data if data is not None else {
            "choices": [{"message": {"content": "OK"}, "finish_reason": "stop"}]
        }
        self.text = text

    def json(self):
        return self._data


class ScriptedAsyncClient:
    """Returns the queued responses in order and records every request."""

    def __init__(self, responses: list[FakeResponse]):
        self._responses = list(responses)
        self.requests: list[dict] = []
        self.closed = False

    async def post(self, url, **kwargs):
        self.requests.append({"url": url, **kwargs})
        return self._responses.pop(0)

    async def get(self, url, **kwargs):
        self.requests.append({"url": url, **kwargs})
        return self._responses.pop(0)

    async def aclose(self):
        self.closed = True

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return None


def _client(model: str, responses: list[FakeResponse]) -> tuple[HttpxOpenAIClient, ScriptedAsyncClient]:
    client = HttpxOpenAIClient(api_key=PLACEHOLDER_KEY, model=model)
    fake = ScriptedAsyncClient(responses)
    client._client = fake  # type: ignore[assignment]
    return client, fake


@pytest.mark.asyncio
async def test_luna_request_uses_chat_completions_with_no_reasoning() -> None:
    client, fake = _client("gpt-6-luna", [FakeResponse()])

    result = await client.translate(
        text="hello",
        system_prompt="Translate {source_language} to {target_language}.",
        source_language="English",
        target_language="Korean",
    )

    assert result == "OK"
    request = fake.requests[0]
    assert request["url"] == "https://api.openai.com/v1/chat/completions"
    assert request["headers"]["Authorization"] == f"Bearer {PLACEHOLDER_KEY}"
    body = request["json"]
    assert body["model"] == "gpt-6-luna"
    assert body["reasoning_effort"] == "none"
    assert body["max_completion_tokens"] == model_profile("gpt-6-luna")[1]
    assert body["messages"][0] == {"role": "system", "content": "Translate English to Korean."}
    assert body["messages"][1]["role"] == "user"
    assert "hello" in body["messages"][1]["content"]
    assert "temperature" not in body


@pytest.mark.asyncio
async def test_sol_uses_lowest_supported_effort() -> None:
    client, fake = _client("gpt-6.1-sol", [FakeResponse()])

    await client.translate(
        text="hello", system_prompt="p", source_language="English", target_language="Japanese"
    )

    assert fake.requests[0]["json"]["reasoning_effort"] == "low"


@pytest.mark.asyncio
async def test_rejected_reasoning_effort_retries_once_without_it_and_remembers() -> None:
    rejection = FakeResponse(
        status_code=400,
        data={"error": {"message": "Unsupported value: 'reasoning_effort' does not support 'none'"}},
    )
    client, fake = _client("gpt-6-luna", [rejection, FakeResponse(), FakeResponse()])

    assert await client.translate(
        text="a", system_prompt="p", source_language="English", target_language="Korean"
    ) == "OK"
    assert "reasoning_effort" in fake.requests[0]["json"]
    assert "reasoning_effort" not in fake.requests[1]["json"]

    await client.translate(
        text="b", system_prompt="p", source_language="English", target_language="Korean"
    )
    assert len(fake.requests) == 3
    assert "reasoning_effort" not in fake.requests[2]["json"]


@pytest.mark.asyncio
async def test_other_errors_raise_with_the_api_message() -> None:
    client, _ = _client(
        "gpt-6-luna",
        [FakeResponse(status_code=401, data={"error": {"message": "Incorrect API key provided"}})],
    )

    with pytest.raises(RuntimeError, match="status=401.*Incorrect API key"):
        await client.translate(
            text="a", system_prompt="p", source_language="English", target_language="Korean"
        )


@pytest.mark.asyncio
async def test_output_cut_off_by_token_cap_raises() -> None:
    truncated = FakeResponse(
        data={"choices": [{"message": {"content": ""}, "finish_reason": "length"}]}
    )
    client, _ = _client("gpt-6.1-sol", [truncated])

    with pytest.raises(RuntimeError, match="cut off"):
        await client.translate(
            text="a", system_prompt="p", source_language="English", target_language="Korean"
        )


@pytest.mark.asyncio
async def test_provider_wraps_client_result_in_translation() -> None:
    class FakeClient:
        closed = False

        async def translate(self, **kwargs) -> str:
            self.kwargs = kwargs
            return "TRANSLATED"

        async def close(self) -> None:
            self.closed = True

    fake = FakeClient()
    provider = OpenAILLMProvider(api_key=PLACEHOLDER_KEY, client=fake)
    utterance_id = uuid4()

    result = await provider.translate(
        utterance_id=utterance_id,
        text="hi",
        system_prompt="p",
        source_language="English",
        target_language="Korean",
        context="earlier line",
    )

    assert result.utterance_id == utterance_id
    assert result.text == "TRANSLATED"
    assert fake.kwargs["context"] == "earlier line"


@pytest.mark.asyncio
async def test_verify_api_key_lists_models_without_spending_tokens(monkeypatch) -> None:
    fake = ScriptedAsyncClient([FakeResponse(status_code=200, data={"data": []})])
    monkeypatch.setattr(openai_chat.httpx, "AsyncClient", lambda **_: fake)

    assert await OpenAILLMProvider.verify_api_key(PLACEHOLDER_KEY) is True
    assert fake.requests[0]["url"] == "https://api.openai.com/v1/models"


@pytest.mark.asyncio
async def test_verify_api_key_rejects_empty_and_unauthorized(monkeypatch) -> None:
    assert await OpenAILLMProvider.verify_api_key("") is False

    fake = ScriptedAsyncClient([FakeResponse(status_code=401, data={})])
    monkeypatch.setattr(openai_chat.httpx, "AsyncClient", lambda **_: fake)
    assert await OpenAILLMProvider.verify_api_key(PLACEHOLDER_KEY) is False


@pytest.fixture(autouse=True)
def _fresh_usage():
    USAGE.reset()
    yield
    USAGE.reset()


NO_CREDITS = {
    "error": {
        "message": "You have no credits remaining. Add credits to continue using the API.",
        "type": "insufficient_quota",
        "code": "insufficient_quota",
    }
}


@pytest.mark.asyncio
async def test_no_credits_raises_a_typed_error_and_marks_usage() -> None:
    client, _ = _client("gpt-6-luna", [FakeResponse(status_code=429, data=NO_CREDITS)])

    with pytest.raises(OpenAIRequestError) as caught:
        await client.translate(
            text="a", system_prompt="p", source_language="English", target_language="Korean"
        )

    assert caught.value.status == 429
    assert caught.value.code == "insufficient_quota"
    assert caught.value.out_of_credits
    assert "no credits remaining" in str(caught.value)
    assert USAGE.snapshot().out_of_credits


@pytest.mark.asyncio
async def test_successful_reply_records_spend_and_rate_limits() -> None:
    reply = FakeResponse(
        data={
            "choices": [{"message": {"content": "OK"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 1_000, "completion_tokens": 200},
        },
        headers={
            "x-ratelimit-limit-tokens": "200000",
            "x-ratelimit-remaining-tokens": "150000",
            "x-ratelimit-limit-requests": "500",
            "x-ratelimit-remaining-requests": "499",
        },
    )
    client, _ = _client("gpt-6-luna", [reply])
    seen = []
    USAGE.add_listener(seen.append)
    try:
        await client.translate(
            text="a", system_prompt="p", source_language="English", target_language="Korean"
        )
    finally:
        USAGE.remove_listener(seen.append)

    snap = USAGE.snapshot()
    assert snap.requests == 1
    assert snap.input_tokens == 1_000 and snap.output_tokens == 200
    assert snap.spent_usd == pytest.approx((1_000 * 0.10 + 200 * 0.50) / 1_000_000)
    assert (snap.limit_tokens, snap.remaining_tokens) == (200_000, 150_000)
    assert len(seen) == 1


@pytest.mark.asyncio
async def test_balance_lookup_reads_credit_grants(monkeypatch) -> None:
    tracker = OpenAIUsageTracker()
    fake = ScriptedAsyncClient(
        [FakeResponse(data={"total_granted": 10.0, "total_used": 2.5, "total_available": 7.5})]
    )
    monkeypatch.setattr(openai_chat.httpx, "AsyncClient", lambda **_: fake)

    assert await tracker.refresh_balance(PLACEHOLDER_KEY) is True
    snap = tracker.snapshot()
    assert (snap.credit_available, snap.credit_granted) == (7.5, 10.0)
    assert fake.requests[0]["url"] == "https://api.openai.com/v1/dashboard/billing/credit_grants"


@pytest.mark.asyncio
async def test_balance_lookup_refused_turns_itself_off(monkeypatch) -> None:
    tracker = OpenAIUsageTracker()
    fake = ScriptedAsyncClient([FakeResponse(status_code=403), FakeResponse(status_code=404)])
    monkeypatch.setattr(openai_chat.httpx, "AsyncClient", lambda **_: fake)

    assert await tracker.refresh_balance(PLACEHOLDER_KEY) is False
    assert await tracker.refresh_balance(PLACEHOLDER_KEY) is False
    assert len(fake.requests) == 2  # the second call made no requests


@pytest.mark.asyncio
async def test_warmup_opens_the_connection_and_keeps_it_alive(monkeypatch) -> None:
    client = HttpxOpenAIClient(api_key=PLACEHOLDER_KEY, model="gpt-6-luna")
    fake = ScriptedAsyncClient([FakeResponse(data={"data": []}) for _ in range(3)])
    client._client = fake  # type: ignore[assignment]
    monkeypatch.setattr(HttpxOpenAIClient, "_WARM_INTERVAL_S", 0.02)

    await client.keep_warm()
    assert fake.requests[0]["url"] == "https://api.openai.com/v1/models"
    await asyncio.sleep(0.08)
    assert len(fake.requests) >= 2          # touched again while idle

    await client.close()
    assert client._warm_task is None


@pytest.mark.asyncio
async def test_provider_warmup_delegates_to_the_client() -> None:
    calls = []

    class _Client:
        async def keep_warm(self):
            calls.append("warm")

        async def close(self):
            pass

    provider = OpenAILLMProvider(api_key=PLACEHOLDER_KEY, client=_Client())
    await provider.warmup()
    assert calls == ["warm"]
