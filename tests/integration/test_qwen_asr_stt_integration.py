from __future__ import annotations

import os

import pytest

from tests.integration.helpers import (
    drain_and_close,
    get_qwen_asr_endpoint,
    integration_mark,
    open_session,
    require_env,
    require_module,
    stream_silence,
)

pytestmark = integration_mark()


@pytest.mark.asyncio
async def test_qwen_asr_realtime_streaming_smoke():
    api_key = require_env("ALIBABA_API_KEY")

    require_module(
        "dashscope",
        reason="dashscope is required for this integration test; install project dependencies.",
    )

    from puripuly_heart.providers.stt.qwen_asr import QwenASRRealtimeSTTBackend

    backend = QwenASRRealtimeSTTBackend(
        api_key=api_key,
        model=os.getenv("QWEN_ASR_MODEL", "qwen3-asr-flash-realtime"),
        endpoint=get_qwen_asr_endpoint(),
        language=os.getenv("QWEN_ASR_LANGUAGE", "ko"),
        sample_rate_hz=int(os.getenv("QWEN_ASR_SAMPLE_RATE", "16000")),
    )

    session = await open_session(backend)

    # Send a short silence stream just to validate connectivity/stream lifecycle.
    await stream_silence(session)

    await session.stop()
    await drain_and_close(session)
