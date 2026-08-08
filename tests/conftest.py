from __future__ import annotations

import sys
from pathlib import Path

SRC_PATH = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC_PATH))


import pytest


@pytest.fixture(autouse=True)
def _reset_local_qwen_shared_recognizer_cache():
    """r386: the recognizer cache is module-level so both channels can share
    one 1.1GB instance. In tests that means the FIRST test's fake recognizer
    would be served to every later test with the same config key — 14
    pre-existing provider tests failed exactly that way. Reset around every
    test; no test file should have to know the cache exists."""
    try:
        from puripuly_heart.providers.stt.local_qwen_sherpa import (
            _reset_shared_local_qwen_recognizers,
        )
    except Exception:
        yield
        return
    _reset_shared_local_qwen_recognizers()
    yield
    _reset_shared_local_qwen_recognizers()
