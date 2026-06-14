from __future__ import annotations

from dataclasses import dataclass
from uuid import uuid4

import pytest

from puripuly_heart.config.prompts import _reset_prompt_cache_for_tests
from puripuly_heart.core.clock import FakeClock
from puripuly_heart.core.orchestrator.hub import ClientHub
from puripuly_heart.domain.models import Translation


@dataclass
class FakeOscQueue:
    messages: list = None

    def __post_init__(self) -> None:
        if self.messages is None:
            self.messages = []

    def enqueue(self, msg) -> None:
        self.messages.append(msg)

    def send_typing(self, on: bool) -> None:
        _ = on

    def process_due(self) -> None:
        return


@dataclass
class FakeLLMProvider:
    last_prompt: str | None = None

    async def translate(
        self,
        *,
        utterance_id,
        text: str,
        system_prompt: str,
        source_language: str,
        target_language: str,
        context: str = "",
    ) -> Translation:
        _ = (text, source_language, target_language, context)
        self.last_prompt = system_prompt
        return Translation(utterance_id=utterance_id, text="ok")

    async def close(self) -> None:
        return


@pytest.mark.asyncio
async def test_hub_substitutes_language_placeholders() -> None:
    fake_llm = FakeLLMProvider()
    hub = ClientHub(
        stt=None,
        llm=fake_llm,
        osc=FakeOscQueue(),
        clock=FakeClock(),
        source_language="ko",
        target_language="en",
        system_prompt="Translate ${sourceName} to ${targetName}.",
    )

    await hub._translate_and_enqueue(uuid4(), "hello")

    assert fake_llm.last_prompt is not None
    assert "${sourceName}" not in fake_llm.last_prompt
    assert "${targetName}" not in fake_llm.last_prompt
    assert "Korean" in fake_llm.last_prompt
    assert "English" in fake_llm.last_prompt


@pytest.mark.asyncio
async def test_hub_renders_dynamic_prompt_placeholders() -> None:
    fake_llm = FakeLLMProvider()
    hub = ClientHub(
        stt=None,
        llm=fake_llm,
        osc=FakeOscQueue(),
        clock=FakeClock(),
        source_language="ko",
        target_language="en",
        system_prompt=("${sourceName}|${targetName}|${targetLanguageRules}|${translationExamples}"),
    )

    await hub._translate_and_enqueue(uuid4(), "안녕")

    assert fake_llm.last_prompt is not None
    assert "Korean|English" in fake_llm.last_prompt
    assert "Use contractions" in fake_llm.last_prompt
    assert "Context Use Example" in fake_llm.last_prompt
    assert "${targetLanguageRules}" not in fake_llm.last_prompt
    assert "${translationExamples}" not in fake_llm.last_prompt


def test_hub_renders_peer_runtime_dynamic_prompt_placeholders() -> None:
    hub = ClientHub(
        stt=None,
        llm=FakeLLMProvider(),
        osc=FakeOscQueue(),
        clock=FakeClock(),
        source_language="ko",
        target_language="en",
        peer_translation_enabled=True,
        peer_source_language="en",
        peer_target_language="ja",
        system_prompt=("${sourceName}|${targetName}|${targetLanguageRules}|${translationExamples}"),
    )

    prompt, _, _ = hub._prepare_llm_request("hello", runtime=hub.peer_runtime)

    assert "English|Japanese" in prompt
    assert "Korean|English" not in prompt
    assert "タメ口" in prompt
    assert "Context Use Example" in prompt
    assert "${sourceName}" not in prompt
    assert "${targetName}" not in prompt
    assert "${targetLanguageRules}" not in prompt
    assert "${translationExamples}" not in prompt


@pytest.mark.asyncio
async def test_hub_renders_custom_prompt_without_dynamic_placeholders() -> None:
    fake_llm = FakeLLMProvider()
    hub = ClientHub(
        stt=None,
        llm=fake_llm,
        osc=FakeOscQueue(),
        clock=FakeClock(),
        source_language="ja",
        target_language="ko",
        system_prompt="Custom ${sourceName} to ${targetName} prompt.",
    )

    await hub._translate_and_enqueue(uuid4(), "こんにちは")

    assert fake_llm.last_prompt == "Custom Japanese to Korean prompt."


@pytest.mark.asyncio
async def test_hub_request_does_not_read_prompt_files_after_warmup(monkeypatch) -> None:
    _reset_prompt_cache_for_tests()
    fake_llm = FakeLLMProvider()
    hub = ClientHub(
        stt=None,
        llm=fake_llm,
        osc=FakeOscQueue(),
        clock=FakeClock(),
        source_language="en",
        target_language="ja",
        system_prompt="${sourceName}|${targetName}|${targetLanguageRules}",
    )

    def fail_read(_path):
        raise AssertionError("prompt files must not be read during hub request assembly")

    monkeypatch.setattr("puripuly_heart.config.prompts._read_prompt_text", fail_read)

    await hub._translate_and_enqueue(uuid4(), "hello")

    assert fake_llm.last_prompt is not None
    assert "English|Japanese" in fake_llm.last_prompt
    assert "タメ口" in fake_llm.last_prompt
