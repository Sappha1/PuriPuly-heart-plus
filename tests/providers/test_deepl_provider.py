from __future__ import annotations

import logging
from uuid import uuid4

import pytest

deepl = pytest.importorskip("deepl")
requests = pytest.importorskip("requests")

from puripuly_heart.providers.llm.deepl import DeepLTranslationProvider  # noqa: E402


class _Result:
    def __init__(self, text: str) -> None:
        self._text = text
        self.detected_source_lang = "JA"

    def __str__(self) -> str:
        return self._text


def _install_fake_translator(monkeypatch: pytest.MonkeyPatch, *, scripts: list[list[object]]):
    """Replace deepl.Translator with a recorder.

    ``scripts[i]`` is the outcome list for the i-th client built: a str is
    returned as the translation, an exception instance is raised. Outcomes
    past the end of a script translate as "ok".
    """
    instances: list[object] = []

    class FakeTranslator:
        def __init__(self, api_key: str, **kwargs) -> None:
            self.api_key = api_key
            self.kwargs = kwargs
            self.calls: list[tuple[str, str | None, str]] = []
            self.closed = False
            index = len(instances)
            self.outcomes = list(scripts[index]) if index < len(scripts) else []
            instances.append(self)

        def translate_text(self, text: str, *, source_lang, target_lang):
            self.calls.append((text, source_lang, target_lang))
            outcome = self.outcomes.pop(0) if self.outcomes else "ok"
            if isinstance(outcome, BaseException):
                raise outcome
            return _Result(str(outcome))

        def close(self) -> None:
            self.closed = True

    monkeypatch.setattr(deepl, "Translator", FakeTranslator)
    return instances


async def _translate(provider: DeepLTranslationProvider, text: str = "こんにちは") -> str:
    translation = await provider.translate(
        utterance_id=uuid4(),
        text=text,
        system_prompt="",
        source_language="ja",
        target_language="en",
    )
    return translation.text


@pytest.mark.asyncio
async def test_deepl_reuses_one_client_across_calls(monkeypatch: pytest.MonkeyPatch) -> None:
    instances = _install_fake_translator(monkeypatch, scripts=[["first", "second"]])
    provider = DeepLTranslationProvider(api_key="test-key-placeholder")

    assert await _translate(provider) == "first"
    assert await _translate(provider) == "second"

    assert len(instances) == 1
    assert len(instances[0].calls) == 2
    assert instances[0].calls[0] == ("こんにちは", "JA", "EN-US")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error",
    [
        deepl.AuthorizationException("bad key"),
        deepl.QuotaExceededException("quota"),
        deepl.DeepLException("bad target language"),
        ValueError("bad code"),
    ],
    ids=["auth", "quota", "deepl", "value"],
)
async def test_deepl_non_transport_errors_are_not_retried(
    monkeypatch: pytest.MonkeyPatch, error: Exception
) -> None:
    instances = _install_fake_translator(monkeypatch, scripts=[[error]])
    provider = DeepLTranslationProvider(api_key="test-key-placeholder")
    provider._client()  # a reused client is the only case that may retry

    with pytest.raises(type(error)):
        await _translate(provider)

    assert len(instances) == 1
    assert len(instances[0].calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error",
    [
        deepl.ConnectionException("connection reset"),
        requests.exceptions.ConnectionError("connection reset"),
        requests.exceptions.Timeout("read timed out"),
        OSError("socket closed"),
    ],
    ids=["deepl-connection", "requests-connection", "requests-timeout", "oserror"],
)
async def test_deepl_transport_error_on_reused_client_is_retried_once_with_a_fresh_client(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    error: Exception,
) -> None:
    instances = _install_fake_translator(monkeypatch, scripts=[["warm", error], ["retried"]])
    provider = DeepLTranslationProvider(api_key="test-key-placeholder")

    assert await _translate(provider) == "warm"
    with caplog.at_level(logging.INFO, logger="puripuly_heart.providers.llm.deepl"):
        assert await _translate(provider) == "retried"

    assert len(instances) == 2
    assert len(instances[0].calls) == 2
    assert len(instances[1].calls) == 1
    assert provider._translator is instances[1]
    assert any(
        "retrying once" in record.getMessage() and str(error) in record.getMessage()
        for record in caplog.records
    )


@pytest.mark.asyncio
async def test_deepl_transport_error_on_a_fresh_client_is_not_retried(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    instances = _install_fake_translator(
        monkeypatch, scripts=[[deepl.ConnectionException("down")]]
    )
    provider = DeepLTranslationProvider(api_key="test-key-placeholder")

    with pytest.raises(deepl.ConnectionException):
        await _translate(provider)

    assert len(instances) == 1
    assert len(instances[0].calls) == 1


@pytest.mark.asyncio
async def test_deepl_retry_failure_propagates(monkeypatch: pytest.MonkeyPatch) -> None:
    instances = _install_fake_translator(
        monkeypatch,
        scripts=[["warm", deepl.ConnectionException("reset")], [OSError("still down")]],
    )
    provider = DeepLTranslationProvider(api_key="test-key-placeholder")
    assert await _translate(provider) == "warm"

    with pytest.raises(OSError):
        await _translate(provider)

    assert len(instances) == 2


@pytest.mark.asyncio
async def test_deepl_close_drops_the_client(monkeypatch: pytest.MonkeyPatch) -> None:
    instances = _install_fake_translator(monkeypatch, scripts=[["one"], ["two"]])
    provider = DeepLTranslationProvider(api_key="test-key-placeholder")
    assert await _translate(provider) == "one"

    await provider.close()

    assert instances[0].closed is True
    assert provider._translator is None
    await provider.close()  # idempotent

    # the next request builds a fresh client rather than touching the closed one
    assert await _translate(provider) == "two"
    assert len(instances) == 2
    assert len(instances[0].calls) == 1


@pytest.mark.asyncio
async def test_deepl_close_survives_a_client_without_close(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    instances = _install_fake_translator(monkeypatch, scripts=[["one"]])
    provider = DeepLTranslationProvider(api_key="test-key-placeholder")
    assert await _translate(provider) == "one"
    del type(instances[0]).close

    await provider.close()

    assert provider._translator is None
