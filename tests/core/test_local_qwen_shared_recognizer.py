"""r386: the local speech model loads once, is shared, and refuses politely.

A new user's machine (15.7GB RAM, ~2-3GB free) froze to a spinner on every
launch: self and peer each constructed their own recognizer — measured at
1142 MB apiece — CONCURRENTLY, demanding ~2.3GB at once. The machine paged
itself into a freeze the load never returned from, and not one error line was
written in nine attempts.

Measured before the fix was written: construction releases the GIL (the freeze
was never Python-side), construction is config-identical for both channels, and
one shared instance decoding for two channels concurrently is safe (2 threads x
6 rounds, zero crashes, zero divergence — upstream's own server pattern).
"""
from __future__ import annotations

import asyncio
import threading
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]

import puripuly_heart.providers.stt.local_qwen_sherpa as mod
from puripuly_heart.providers.stt.local_qwen_sherpa import (
    LocalQwenLowMemoryError,
    LocalQwenSherpaLoadError,
    LocalQwenSherpaSTTBackend,
    _reset_shared_local_qwen_recognizers,
    _shared_recognizer,
)


@pytest.fixture(autouse=True)
def _clean_cache():
    _reset_shared_local_qwen_recognizers()
    yield
    _reset_shared_local_qwen_recognizers()


def test_same_config_shares_one_instance() -> None:
    calls: list[int] = []

    def factory():
        calls.append(1)
        return object()

    key = ("model", 4, 128, "cpu")
    first = _shared_recognizer(key, factory)
    second = _shared_recognizer(key, factory)
    assert first is second
    assert len(calls) == 1, "both channels built their own 1.1GB instance again"


def test_different_config_builds_separately() -> None:
    made: list[object] = []

    def factory():
        made.append(object())
        return made[-1]

    a = _shared_recognizer(("model", 4, 128, "cpu"), factory)
    b = _shared_recognizer(("model", 8, 128, "cpu"), factory)
    assert a is not b
    assert len(made) == 2


def test_concurrent_builds_are_serialized_and_deduplicated() -> None:
    """The reported failure shape: both channels' loads starting 1ms apart.
    Whichever thread wins builds; the loser must WAIT and reuse — never build
    a second instance alongside."""
    started = threading.Event()
    release = threading.Event()
    calls: list[int] = []

    def slow_factory():
        calls.append(1)
        started.set()
        release.wait(timeout=5)
        return object()

    key = ("model", 4, 128, "cpu")
    results: list[object] = []

    def worker():
        results.append(_shared_recognizer(key, slow_factory))

    t1 = threading.Thread(target=worker)
    t2 = threading.Thread(target=worker)
    t1.start()
    started.wait(timeout=5)
    t2.start()  # arrives while the first build is mid-flight
    release.set()
    t1.join(timeout=5)
    t2.join(timeout=5)

    assert len(calls) == 1, "a second load ran while the first was in flight"
    assert results[0] is results[1]


def test_low_memory_refuses_with_numbers(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(mod, "_available_memory_mb", lambda: 900)
    with pytest.raises(LocalQwenLowMemoryError) as excinfo:
        mod._require_memory_for_model_load()
    err = excinfo.value
    assert err.available_mb == 900
    assert err.needed_mb == mod.LOCAL_QWEN_MODEL_LOAD_ESTIMATE_MB
    # It must still be catchable by the generic load-error handling…
    assert isinstance(err, LocalQwenSherpaLoadError)


def test_memory_probe_failure_never_blocks_the_load(monkeypatch) -> None:
    """The check is a diagnostic. If it cannot measure, it must stand aside."""
    monkeypatch.setattr(mod, "_available_memory_mb", lambda: None)
    mod._require_memory_for_model_load()  # must not raise


def test_cached_instance_skips_the_memory_gate(monkeypatch) -> None:
    """Once loaded, attaching to the shared instance costs nothing — a scarce-
    memory machine must not be refused the model it already has in RAM."""
    key = ("model", 4, 128, "cpu")
    _shared_recognizer(key, lambda: object())  # warm the cache
    monkeypatch.setattr(mod, "_available_memory_mb", lambda: 100)
    again = _shared_recognizer(key, lambda: pytest.fail("rebuilt despite cache"))
    assert again is not None


def test_backend_close_does_not_evict_the_shared_instance(monkeypatch) -> None:
    """Backends are recreated on trivial settings churn (r331/r333/r345); the
    whole point of the cache is surviving that."""
    key = ("model", 4, 128, "cpu")
    instance = _shared_recognizer(key, lambda: object())

    backend = LocalQwenSherpaSTTBackend(
        model_dir=Path("model"), sample_rate_hz=16000, stream_label="self"
    )
    asyncio.run(backend.close())
    assert mod._shared_recognizer_cached(key) is instance


def test_warmup_propagates_the_low_memory_refusal() -> None:
    """The review's blocker: the session retry loop swallowed EVERY exception,
    retried a memory refusal twice more with backoff, and returned False — so
    warmup() succeeded silently, the mic stayed green, and the localized
    handler downstream was dead code. Warmup must RAISE the typed error, after
    exactly one attempt."""
    from puripuly_heart.core.stt.controller import ManagedSTTProvider

    attempts: list[int] = []

    class RefusingBackend:
        async def open_session(self):
            attempts.append(1)
            raise LocalQwenLowMemoryError(available_mb=900, needed_mb=1200)

    provider = ManagedSTTProvider(backend=RefusingBackend(), sample_rate_hz=16000)

    async def run() -> None:
        with pytest.raises(LocalQwenLowMemoryError) as excinfo:
            await provider.warmup()
        assert excinfo.value.available_mb == 900

    asyncio.run(run())
    assert len(attempts) == 1, (
        f"a non-retryable refusal was retried {len(attempts)} times — the "
        "backoff loop is swallowing it again"
    )


def test_retryable_failures_still_do_not_raise_from_warmup() -> None:
    """Ordinary connection failures keep the old contract: retried, reported
    as an event, warmup returns quietly. Only non-retryable errors escape."""
    from puripuly_heart.core.stt.controller import ManagedSTTProvider

    attempts: list[int] = []

    class FlakyBackend:
        async def open_session(self):
            attempts.append(1)
            raise RuntimeError("transient network sadness")

    provider = ManagedSTTProvider(
        backend=FlakyBackend(),
        sample_rate_hz=16000,
        connect_attempts=2,
        connect_retry_base_s=0.001,
        connect_retry_max_s=0.001,
    )
    asyncio.run(provider.warmup())  # must NOT raise
    assert len(attempts) == 2, "retryable failures should still use every attempt"


def test_controller_low_memory_path_does_not_redownload() -> None:
    """The generic "invalid" handler re-downloads the 900MB model. For a
    machine whose problem is RAM, that is exactly wrong — the refusal must be
    caught separately, before the generic catch, and never start a download."""
    text = (_ROOT / "src/puripuly_heart/ui/controller.py").read_text(encoding="utf-8")
    low = text.index("except LocalQwenLowMemoryError")
    generic = text.index(
        "except (LocalSTTManifestInvalidError, LocalQwenSherpaLoadError)"
    )
    assert low < generic, (
        "the low-memory catch sits after the generic one, so it is dead code "
        "and low-memory machines re-download the model"
    )
    block = text[low:generic]
    assert "_start_local_stt_download" not in block
    assert "stt.local.low_memory" in block, "the localized message is not used"


@pytest.mark.parametrize("locale", ["en", "zh-CN", "ja", "ko"])
def test_low_memory_message_is_localized(locale: str) -> None:
    import json

    data = json.loads(
        (_ROOT / f"src/puripuly_heart/data/i18n/{locale}.json").read_text(encoding="utf-8")
    )
    message = data.get("stt.local.low_memory", "")
    assert "{needed}" in message and "{available}" in message, (
        f"{locale}: the refusal message is missing its numbers"
    )
