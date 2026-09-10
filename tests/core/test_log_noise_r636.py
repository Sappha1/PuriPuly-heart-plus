"""r636 log-noise / silent-failure fixes.

Covers the three non-UI parts of the batch:
  * VAD candidate "[VAD][TEST]" lines are INFO only while detailed
    diagnostics are on (6,606 INFO records a day otherwise).
  * The hub names extra-target translation/transliteration failures that used
    to be swallowed by `except Exception: pass`, throttled per target.
  * The VRChat mute-state callback no longer fails in silence.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from uuid import UUID, uuid4

import pytest

from puripuly_heart.core.clock import FakeClock
from puripuly_heart.core.orchestrator.hub import ClientHub
from puripuly_heart.core.osc.receiver import VrcMicState
from puripuly_heart.core.vad.gating import create_peer_vad_gating
from puripuly_heart.domain.models import Translation
from tests.helpers.fakes import RecordingOscQueue
from tests.helpers.vad import SequenceVadEngine, chunk_samples

_GATING_LOGGER = "puripuly_heart.core.vad.gating"
_HUB_LOGGER = "puripuly_heart.core.orchestrator.hub"
_RECEIVER_LOGGER = "puripuly_heart.core.osc.receiver"


# --------------------------------------------------------------------------
# VAD candidate lines
# --------------------------------------------------------------------------
def _run_peer_candidate(*, detailed: bool | None) -> None:
    """Start a peer candidate and let it die -> "start" + "dropped" lines."""
    kwargs = {} if detailed is None else {"diagnostics_enabled": lambda: detailed}
    gating = create_peer_vad_gating(
        SequenceVadEngine(probs=[0.9, 0.0, 0.0]),
        sample_rate_hz=16000,
        ring_buffer_ms=64,
        hangover_ms=64,
        **kwargs,
    )
    for i in range(3):
        gating.process_chunk(chunk_samples(float(i), n=gating.chunk_samples))


def _candidate_lines(caplog: pytest.LogCaptureFixture, *, level: int) -> list[str]:
    return [
        record.getMessage()
        for record in caplog.records
        if record.levelno == level and "[VAD][TEST]" in record.getMessage()
    ]


def test_peer_candidate_lines_are_not_info_when_diagnostics_off(caplog) -> None:
    with caplog.at_level(logging.INFO, logger=_GATING_LOGGER):
        _run_peer_candidate(detailed=False)

    assert _candidate_lines(caplog, level=logging.INFO) == []


def test_peer_candidate_lines_are_not_info_without_a_detail_flag(caplog) -> None:
    # headless runs wire no flag at all - still quiet at INFO
    with caplog.at_level(logging.INFO, logger=_GATING_LOGGER):
        _run_peer_candidate(detailed=None)

    assert _candidate_lines(caplog, level=logging.INFO) == []


def test_peer_candidate_lines_stay_available_at_debug(caplog) -> None:
    with caplog.at_level(logging.DEBUG, logger=_GATING_LOGGER):
        _run_peer_candidate(detailed=False)

    debug_lines = _candidate_lines(caplog, level=logging.DEBUG)
    assert any("candidate start" in line for line in debug_lines)
    assert any("candidate dropped" in line for line in debug_lines)


def test_peer_candidate_drop_facts_are_info_when_diagnostics_on(caplog) -> None:
    with caplog.at_level(logging.INFO, logger=_GATING_LOGGER):
        _run_peer_candidate(detailed=True)

    info_lines = _candidate_lines(caplog, level=logging.INFO)
    assert any("Peer candidate start" in line for line in info_lines)
    dropped = [line for line in info_lines if "candidate dropped" in line]
    assert len(dropped) == 1
    # r629/r630 diagnostics must survive: this is what missed speech is chased with
    assert "hits=" in dropped[0]
    assert "prob=" in dropped[0]
    assert "min_prob=" in dropped[0]


# --------------------------------------------------------------------------
# Hub extra-target failures
# --------------------------------------------------------------------------
@dataclass(slots=True)
class _ExtraTargetFailingLLM:
    failing_targets: tuple[str, ...]
    calls: list[str] = field(default_factory=list)

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
        _ = (system_prompt, source_language, context)
        self.calls.append(target_language)
        if target_language in self.failing_targets:
            raise RuntimeError("provider is down")
        return Translation(utterance_id=utterance_id, text=f"T:{text}")

    async def close(self) -> None:
        return


def _make_hub(llm: _ExtraTargetFailingLLM, clock: FakeClock, extras: list[str]) -> ClientHub:
    return ClientHub(
        stt=None,
        llm=llm,
        osc=RecordingOscQueue(),
        clock=clock,
        target_language="en",
        extra_target_languages=extras,
    )


def _warnings(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [
        record.getMessage()
        for record in caplog.records
        if record.levelno == logging.WARNING and "extra target translation" in record.getMessage()
    ]


@pytest.mark.asyncio
async def test_extra_target_translation_failure_is_logged_once_per_minute(caplog) -> None:
    clock = FakeClock(100.0)
    llm = _ExtraTargetFailingLLM(failing_targets=("ko",))
    hub = _make_hub(llm, clock, ["ko"])

    with caplog.at_level(logging.WARNING, logger=_HUB_LOGGER):
        await hub._translate_and_enqueue(uuid4(), "hello")
        first = list(_warnings(caplog))

        # same failure again inside the window: no second line
        await hub._translate_and_enqueue(uuid4(), "hello again")
        throttled = list(_warnings(caplog))

        clock.advance(61.0)
        await hub._translate_and_enqueue(uuid4(), "hello later")
        after_window = list(_warnings(caplog))

    assert len(first) == 1
    assert "ko" in first[0]
    assert "RuntimeError" in first[0]
    assert "provider is down" in first[0]
    assert len(throttled) == 1
    assert len(after_window) == 2


@pytest.mark.asyncio
async def test_extra_target_failures_are_throttled_per_target(caplog) -> None:
    clock = FakeClock(10.0)
    llm = _ExtraTargetFailingLLM(failing_targets=("ko", "ja"))
    hub = _make_hub(llm, clock, ["ko", "ja"])

    with caplog.at_level(logging.WARNING, logger=_HUB_LOGGER):
        await hub._translate_and_enqueue(uuid4(), "hello")
        lines = list(_warnings(caplog))

    # one line per failing target, not one shared throttle slot
    assert len(lines) == 2
    assert any("'ko'" in line for line in lines)
    assert any("'ja'" in line for line in lines)


@pytest.mark.asyncio
async def test_extra_target_success_logs_nothing(caplog) -> None:
    clock = FakeClock()
    llm = _ExtraTargetFailingLLM(failing_targets=())
    hub = _make_hub(llm, clock, ["ko"])

    with caplog.at_level(logging.WARNING, logger=_HUB_LOGGER):
        await hub._translate_and_enqueue(uuid4(), "hello")

    assert _warnings(caplog) == []
    assert "ko" in llm.calls


def test_target_failure_warning_names_stage_and_target(caplog) -> None:
    clock = FakeClock(5.0)
    hub = _make_hub(_ExtraTargetFailingLLM(failing_targets=()), clock, [])

    with caplog.at_level(logging.WARNING, logger=_HUB_LOGGER):
        hub._warn_target_failure("chatbox transliteration", "ja", ValueError("boom"))
        hub._warn_target_failure("chatbox transliteration", "ja", ValueError("boom"))
        # a different stage for the same target keeps its own slot
        hub._warn_target_failure("extra target transliteration", "ja", ValueError("boom"))

    messages = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert len(messages) == 2
    assert "chatbox transliteration failed for target 'ja'" in messages[0]
    assert "ValueError: boom" in messages[0]
    assert "extra target transliteration failed for target 'ja'" in messages[1]


# --------------------------------------------------------------------------
# OSC receiver callback
# --------------------------------------------------------------------------
def test_mute_state_callback_exception_is_logged(caplog) -> None:
    def boom(_muted: bool | None) -> None:
        raise RuntimeError("gate is gone")

    state = VrcMicState(on_state_changed=boom)

    with caplog.at_level(logging.ERROR, logger=_RECEIVER_LOGGER):
        changed = state.update(True)

    # the state still flips - only the notification failed
    assert changed is True
    assert state.muted is True
    records = [r for r in caplog.records if r.levelno == logging.ERROR]
    assert len(records) == 1
    assert "callback failed" in records[0].getMessage()
    assert records[0].exc_info is not None


def test_mute_state_reset_callback_exception_is_logged(caplog) -> None:
    def boom(_muted: bool | None) -> None:
        raise RuntimeError("gate is gone")

    state = VrcMicState(muted=True, on_state_changed=boom)

    with caplog.at_level(logging.ERROR, logger=_RECEIVER_LOGGER):
        state.reset()

    assert state.muted is None
    records = [r for r in caplog.records if r.levelno == logging.ERROR]
    assert len(records) == 1
    assert "reset callback failed" in records[0].getMessage()
    assert records[0].exc_info is not None
