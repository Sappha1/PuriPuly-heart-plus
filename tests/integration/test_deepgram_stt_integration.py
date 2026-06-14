from __future__ import annotations

import os

import pytest

from tests.integration.helpers import (
    drain_and_close,
    integration_mark,
    open_session,
    require_env,
    require_module,
    stream_silence,
)

pytestmark = integration_mark()


@pytest.mark.asyncio
async def test_deepgram_realtime_streaming_smoke():
    api_key = require_env("DEEPGRAM_API_KEY")

    require_module(
        "websocket",
        reason=(
            "websocket-client is required for this integration test; install with pip install websocket-client"
        ),
    )

    from puripuly_heart.providers.stt.deepgram import DeepgramRealtimeSTTBackend

    backend = DeepgramRealtimeSTTBackend(
        api_key=api_key,
        model=os.getenv("DEEPGRAM_STT_MODEL", "nova-3"),
        language=os.getenv("DEEPGRAM_STT_LANGUAGE", "ko"),
        sample_rate_hz=int(os.getenv("DEEPGRAM_STT_SAMPLE_RATE", "16000")),
    )

    session = await open_session(backend)

    # Send a short silence stream just to validate connectivity/stream lifecycle.
    await stream_silence(session)

    await session.stop()
    await drain_and_close(session)
