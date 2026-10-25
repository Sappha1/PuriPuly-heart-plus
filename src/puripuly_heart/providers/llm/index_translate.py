"""Index-Translate 35B A3B - a free translation model, no API key.

Served by bilibili's public endpoint (OpenAI-compatible Chat Completions).
Text goes to bilibili's servers; nothing is sent to anyone else.

The request copies the model's own reference client: one user message in the
fixed Chinese instruction template (language names in Chinese), thinking off,
``Bearer none`` auth and the client's User-Agent. The app's general LLM
system prompt and conversation context are not sent - this model is trained
on the template alone and translates the context too when it is included.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import re
from dataclasses import dataclass, field
from uuid import UUID

import httpx

from puripuly_heart.core.language import get_language_info
from puripuly_heart.core.runtime_logging import SessionRuntimeLoggingService
from puripuly_heart.domain.models import Translation

logger = logging.getLogger(__name__)

INDEX_TRANSLATE_ENDPOINT = "https://index-translate.bilibili.com/v1/chat/completions"
# the public endpoint's own name for the model (the Hugging Face repo id is
# rejected there as an unknown model)
INDEX_TRANSLATE_MODEL = "Index-Translate-35B-A3B"
_MODEL_NAME_RE = re.compile(r"Index-Translate-[A-Za-z0-9.]+(?:-[A-Za-z0-9.]+)*")
INDEX_TRANSLATE_HEADERS = {
    "Authorization": "Bearer none",
    "User-Agent": "Index-Translate-Client/1.0",
}
_MAX_OUTPUT_TOKENS = 1024
_REASONING_MARKUP = re.compile(r"</?(?:analysis|reasoning)\b|<\|", re.IGNORECASE)
_THINK_BLOCK = re.compile(r"^.*</think>", re.IGNORECASE | re.S)

# language names as the model's template expects them (Chinese)
_ZH_LANGUAGE_NAMES = {
    "zh": "中文",
    "zh-cn": "简体中文",
    "zh-hans": "简体中文",
    "zh-tw": "繁体中文",
    "zh-hant": "繁体中文",
    "zh-hk": "繁体中文",
    "en": "英语",
    "ja": "日语",
    "ko": "韩语",
    "ru": "俄语",
    "uk": "乌克兰语",
    "es": "西班牙语",
    "fr": "法语",
    "de": "德语",
    "it": "意大利语",
    "pt": "葡萄牙语",
    "nl": "荷兰语",
    "pl": "波兰语",
    "sv": "瑞典语",
    "da": "丹麦语",
    "no": "挪威语",
    "nb": "挪威语",
    "fi": "芬兰语",
    "cs": "捷克语",
    "sk": "斯洛伐克语",
    "hu": "匈牙利语",
    "ro": "罗马尼亚语",
    "bg": "保加利亚语",
    "el": "希腊语",
    "tr": "土耳其语",
    "ar": "阿拉伯语",
    "he": "希伯来语",
    "fa": "波斯语",
    "hi": "印地语",
    "bn": "孟加拉语",
    "ur": "乌尔都语",
    "th": "泰语",
    "vi": "越南语",
    "id": "印尼语",
    "ms": "马来语",
    "tl": "菲律宾语",
    "fil": "菲律宾语",
    "km": "高棉语",
    "lo": "老挝语",
    "my": "缅甸语",
    "mn": "蒙古语",
    "kk": "哈萨克语",
}


class IndexTranslateError(RuntimeError):
    def __init__(self, *, status: int, api_message: str = "") -> None:
        self.status = status
        self.api_message = api_message
        super().__init__(
            f"Index-Translate request failed (status={status}, "
            f"message={api_message or 'unknown error'})"
        )


def language_name_for_prompt(code: str) -> str:
    """Chinese language name for the template; English name as a fallback."""
    raw = (code or "").strip()
    if not raw:
        return ""
    lowered = raw.lower()
    if lowered in _ZH_LANGUAGE_NAMES:
        return _ZH_LANGUAGE_NAMES[lowered]
    base = lowered.split("-")[0]
    if base in _ZH_LANGUAGE_NAMES:
        return _ZH_LANGUAGE_NAMES[base]
    info = get_language_info(raw)
    return info.name if info is not None else raw


def build_index_prompt(text: str, source_language: str, target_language: str) -> str:
    source = "" if (source_language or "").lower() in ("", "auto") else language_name_for_prompt(source_language)
    target = language_name_for_prompt(target_language)
    return (
        f"请将以下{source}文本翻译为{target}，直接输出翻译结果，不要进行任何解释。"
        f"\n\n{text}"
    )


def _looks_like_web_page(text: str) -> bool:
    head = (text or "").lstrip()[:200].lower()
    return head.startswith("<!doctype") or head.startswith("<html") or "<head" in head


def parse_index_reply(data: object) -> str:
    choices = data.get("choices") if isinstance(data, dict) else None
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
        raise IndexTranslateError(status=200, api_message="reply had no choices")
    choice = choices[0]
    message = choice.get("message") if isinstance(choice.get("message"), dict) else {}
    content = message.get("content")
    if isinstance(content, list):
        content = "\n".join(
            part.get("text", "") for part in content
            if isinstance(part, dict) and isinstance(part.get("text"), str)
        )
    text = content if isinstance(content, str) else ""
    # the reference client keeps only what follows a closing think tag
    if "</think>" in text.lower():
        text = _THINK_BLOCK.sub("", text)
    text = re.sub(r"^\s*<think>", "", text, flags=re.IGNORECASE).strip()
    if choice.get("finish_reason") == "length" and not text:
        raise IndexTranslateError(status=200, api_message="reply was cut off")
    if not text or _REASONING_MARKUP.search(text):
        raise IndexTranslateError(status=200, api_message="reply had no usable translation")
    return text


def _error_detail(response: httpx.Response) -> str:
    detail = ""
    with contextlib.suppress(Exception):
        data = response.json()
        err = data.get("error") if isinstance(data, dict) else None
        if isinstance(err, dict):
            detail = str(err.get("message") or "")
        elif err:
            detail = str(err)
        elif isinstance(data, dict) and data.get("message"):
            detail = str(data.get("message"))
        elif isinstance(data, dict) and data.get("detail"):
            raw = data.get("detail")
            if isinstance(raw, list):
                raw = "; ".join(
                    str(item.get("msg") if isinstance(item, dict) else item) for item in raw
                )
            detail = str(raw)
    if not detail:
        with contextlib.suppress(Exception):
            body = response.text
            if _looks_like_web_page(body):
                detail = "service unavailable (answered with a web page)"
            else:
                detail = " ".join(body.split())[:200]
    if response.status_code == 429:
        detail = detail or "rate limit reached"
    return detail


def _is_unknown_model(status: int, detail: str) -> bool:
    lowered = detail.lower()
    if status not in (400, 404, 422):
        return False
    if "未知模型" in detail or "不支持" in detail and "模型" in detail:
        return True
    return "model" in lowered and (
        "not found" in lowered or "does not exist" in lowered or "unknown" in lowered
        or "invalid" in lowered or "not exist" in lowered or "not supported" in lowered
    )


def _supported_model_from(detail: str, rejected: str) -> str | None:
    """Pick the 35B model from the list an unknown-model error names."""
    names = [n for n in _MODEL_NAME_RE.findall(detail or "") if n != rejected]
    for name in names:
        if "35B" in name and not name.endswith("-preview"):
            return name
    for name in names:
        if "35B" in name:
            return name
    return None


@dataclass(slots=True)
class IndexTranslateLLMProvider:
    endpoint: str = INDEX_TRANSLATE_ENDPOINT
    model: str = INDEX_TRANSLATE_MODEL
    timeout: float = 20.0
    runtime_logging: SessionRuntimeLoggingService | None = None
    _client: httpx.AsyncClient | None = field(init=False, default=None, repr=False)
    _lock: asyncio.Lock = field(init=False, default_factory=asyncio.Lock, repr=False)

    async def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            async with self._lock:
                if self._client is None:
                    self._client = httpx.AsyncClient(
                        timeout=self.timeout, headers=dict(INDEX_TRANSLATE_HEADERS)
                    )
        return self._client

    def _log(self, message: str, *, level: int = logging.INFO) -> None:
        if self.runtime_logging is not None:
            self.runtime_logging.emit_basic(message, level=level)
        else:
            logger.log(level, message)

    def _body(self, model: str, prompt: str) -> dict:
        return {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0,
            "max_tokens": _MAX_OUTPUT_TOKENS,
            "chat_template_kwargs": {"enable_thinking": False},
            "stream": False,
        }

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
        prompt = build_index_prompt(text, source_language, target_language)
        self._log(
            "[Basic][LLM] Index-Translate request %s -> %s: %r"
            % (source_language, target_language, text)
        )
        client = await self._http()
        response = await client.post(self.endpoint, json=self._body(self.model, prompt))
        if response.status_code != 200:
            detail = _error_detail(response)
            alternative = (
                _supported_model_from(detail, self.model)
                if _is_unknown_model(response.status_code, detail)
                else None
            )
            if alternative:
                # the server renamed the model: use the name it lists, and keep it
                retry = await client.post(self.endpoint, json=self._body(alternative, prompt))
                response = retry
                if retry.status_code == 200:
                    self.model = alternative
                else:
                    detail = _error_detail(retry)
            if response.status_code != 200:
                self._log(
                    "[Basic][LLM] Index-Translate request failed: status=%s message=%s"
                    % (response.status_code, detail or "unknown error"),
                    level=logging.ERROR,
                )
                raise IndexTranslateError(status=response.status_code, api_message=detail)
        try:
            data = response.json()
        except (json.JSONDecodeError, ValueError):
            detail = (
                "service unavailable (answered with a web page)"
                if _looks_like_web_page(response.text)
                else "reply was not JSON"
            )
            raise IndexTranslateError(status=response.status_code, api_message=detail) from None
        result = parse_index_reply(data)
        self._log("[Basic][LLM] Index-Translate response: %r" % result)
        return Translation(utterance_id=utterance_id, text=result)

    async def close(self) -> None:
        client, self._client = self._client, None
        if client is not None:
            await client.aclose()
