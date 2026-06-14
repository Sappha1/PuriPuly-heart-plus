from __future__ import annotations

import sys
from types import SimpleNamespace

import pytest

from puripuly_heart.providers.llm.qwen import DashScopeQwenClient


class DummyGeneration:
    last_call: dict | None = None

    @classmethod
    def call(cls, *_, **kwargs):
        cls.last_call = kwargs

        class Response:
            status_code = 200
            output = {"choices": [{"message": {"content": "OK"}}]}

        return Response()


@pytest.mark.asyncio
async def test_qwen_client_builds_prompt_with_context(monkeypatch) -> None:
    dummy = SimpleNamespace(api_key=None, base_http_api_url=None, Generation=DummyGeneration)
    monkeypatch.setitem(sys.modules, "dashscope", dummy)

    client = DashScopeQwenClient(api_key="key", model="qwen-plus")
    result = await client.translate(
        text="hello",
        system_prompt="PROMPT",
        source_language="ko",
        target_language="en",
        context='- "previous"',
    )

    assert result == "OK"
    assert DummyGeneration.last_call is not None
    messages = DummyGeneration.last_call.get("messages")
    assert messages[0]["role"] == "system"
    assert "PROMPT" in messages[0]["content"]
    assert messages[1]["role"] == "user"
    assert "<context>" in messages[1]["content"]
    assert "</context>" in messages[1]["content"]
    assert "<input>\nhello\n</input>" in messages[1]["content"]
    assert "Input: hello" not in messages[1]["content"]


@pytest.mark.asyncio
async def test_qwen_client_builds_prompt_without_context(monkeypatch) -> None:
    dummy = SimpleNamespace(api_key=None, base_http_api_url=None, Generation=DummyGeneration)
    monkeypatch.setitem(sys.modules, "dashscope", dummy)

    client = DashScopeQwenClient(api_key="key", model="qwen-plus")
    result = await client.translate(
        text="hello",
        system_prompt="PROMPT",
        source_language="ko",
        target_language="en",
        context="",
    )

    assert result == "OK"
    assert DummyGeneration.last_call is not None
    assert DummyGeneration.last_call.get("messages") == [
        {"role": "system", "content": "PROMPT"},
        {"role": "user", "content": "<input>\nhello\n</input>"},
    ]
