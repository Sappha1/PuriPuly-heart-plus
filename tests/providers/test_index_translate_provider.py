from __future__ import annotations

from uuid import uuid4

import pytest

from puripuly_heart.providers.llm.index_translate import (
    INDEX_TRANSLATE_ENDPOINT,
    INDEX_TRANSLATE_HEADERS,
    INDEX_TRANSLATE_MODEL,
    IndexTranslateError,
    IndexTranslateLLMProvider,
    build_index_prompt,
    parse_index_reply,
)


class _Response:
    def __init__(self, status: int = 200, data: dict | None = None, text: str = "") -> None:
        self.status_code = status
        self._data = data
        self.text = text

    def json(self):
        if self._data is None:
            raise ValueError("not json")
        return self._data


class _Client:
    def __init__(self, *responses: _Response) -> None:
        self.responses = list(responses)
        self.requests: list = []

    async def post(self, url, **kwargs):
        self.requests.append((url, kwargs))
        return self.responses.pop(0) if len(self.responses) > 1 else self.responses[0]

    async def aclose(self):
        pass


def _ok(text: str) -> _Response:
    return _Response(data={"choices": [{"message": {"role": "assistant", "content": text},
                                        "finish_reason": "stop"}]})


@pytest.mark.asyncio
async def test_request_follows_the_reference_client_template() -> None:
    provider = IndexTranslateLLMProvider()
    client = _Client(_ok("おはよう"))
    provider._client = client  # type: ignore[assignment]

    result = await provider.translate(
        utterance_id=uuid4(), text="good morning",
        system_prompt="a long general prompt that this model does not get",
        source_language="en", target_language="ja", context="earlier lines",
    )

    assert result.text == "おはよう"
    url, kwargs = client.requests[0]
    assert url == INDEX_TRANSLATE_ENDPOINT
    body = kwargs["json"]
    assert body["model"] == INDEX_TRANSLATE_MODEL
    assert body["chat_template_kwargs"] == {"enable_thinking": False}
    assert body["temperature"] == 0
    assert body["messages"] == [
        {"role": "user",
         "content": "请将以下英语文本翻译为日语，直接输出翻译结果，不要进行任何解释。\n\ngood morning"}
    ]


def test_client_headers_carry_no_real_credentials() -> None:
    assert INDEX_TRANSLATE_HEADERS["Authorization"] == "Bearer none"
    assert "User-Agent" in INDEX_TRANSLATE_HEADERS


def test_prompt_omits_unknown_source_and_maps_chinese_variants() -> None:
    assert build_index_prompt("hi", "", "zh-CN").startswith("请将以下文本翻译为简体中文，")
    assert build_index_prompt("hi", "auto", "zh-TW").startswith("请将以下文本翻译为繁体中文，")
    assert "韩语" in build_index_prompt("hi", "en", "ko")


def test_default_model_is_the_public_endpoint_name() -> None:
    assert INDEX_TRANSLATE_MODEL == "Index-Translate-35B-A3B"


@pytest.mark.asyncio
async def test_unknown_model_retries_with_the_name_the_server_lists() -> None:
    # the endpoint answers an unknown model in Chinese with the models it serves
    rejected = _Response(400, {"detail": (
        "未知模型 'Old-Name'，公开支持模型 本模型: Index-Translate-2B, "
        "Index-Translate-9B, Index-Translate-35B-A3B-v2")})
    provider = IndexTranslateLLMProvider(model="Old-Name")
    client = _Client(rejected, _ok("hola"))
    provider._client = client  # type: ignore[assignment]

    result = await provider.translate(utterance_id=uuid4(), text="hi", system_prompt="",
                                      source_language="en", target_language="es")

    assert result.text == "hola"
    assert [req[1]["json"]["model"] for req in client.requests] == [
        "Old-Name", "Index-Translate-35B-A3B-v2"]
    assert provider.model == "Index-Translate-35B-A3B-v2"


@pytest.mark.asyncio
async def test_detail_errors_are_read_as_plain_text() -> None:
    provider = IndexTranslateLLMProvider()
    provider._client = _Client(_Response(503, {"detail": "服务繁忙"}))  # type: ignore[assignment]

    with pytest.raises(IndexTranslateError) as err:
        await provider.translate(utterance_id=uuid4(), text="hi", system_prompt="",
                                 source_language="en", target_language="es")

    assert "message=服务繁忙" in str(err.value) and "{" not in str(err.value)


@pytest.mark.asyncio
async def test_http_errors_carry_status_and_message() -> None:
    provider = IndexTranslateLLMProvider()
    provider._client = _Client(_Response(429, {"error": {"message": "too many requests"}}))  # type: ignore[assignment]

    with pytest.raises(IndexTranslateError, match="status=429.*too many requests"):
        await provider.translate(utterance_id=uuid4(), text="hi", system_prompt="p",
                                 source_language="en", target_language="ko")


@pytest.mark.asyncio
async def test_a_web_page_answer_becomes_a_short_unavailable_message() -> None:
    page = '<!DOCTYPE html> <html lang="zh-cn"> <head> <meta charset="utf-8"> </head></html>'
    for status in (403, 200):
        provider = IndexTranslateLLMProvider()
        provider._client = _Client(_Response(status, None, text=page))  # type: ignore[assignment]

        with pytest.raises(IndexTranslateError) as err:
            await provider.translate(utterance_id=uuid4(), text="hi", system_prompt="",
                                     source_language="en", target_language="ko")

        assert "<" not in str(err.value)
        assert "web page" in str(err.value)


def test_think_block_is_dropped_and_empty_reply_is_rejected() -> None:
    assert parse_index_reply({"choices": [{"message": {"content": "<think>hmm</think> hi"},
                                           "finish_reason": "stop"}]}) == "hi"
    with pytest.raises(IndexTranslateError):
        parse_index_reply({"choices": [{"message": {"content": "  "}, "finish_reason": "stop"}]})
    with pytest.raises(IndexTranslateError):
        parse_index_reply({})


def test_settings_roundtrip_and_wiring() -> None:
    from puripuly_heart.app.wiring import create_llm_provider
    from puripuly_heart.config.settings import (
        AppSettings,
        LLMProviderName,
        TranslationConnection,
        TranslationModel,
        from_dict,
        to_dict,
    )
    from puripuly_heart.core.storage.secrets import InMemorySecretStore

    data = to_dict(AppSettings())
    data["translation"] = {"model": "index_translate", "connection": "free_web"}
    loaded = from_dict(data)

    assert loaded.translation.model == TranslationModel.INDEX_TRANSLATE
    assert loaded.translation.connection == TranslationConnection.FREE_WEB
    assert loaded.provider.llm == LLMProviderName.INDEX_TRANSLATE

    provider = create_llm_provider(loaded, secrets=InMemorySecretStore())
    assert isinstance(provider.inner, IndexTranslateLLMProvider)
