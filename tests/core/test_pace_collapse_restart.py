"""Pace-collapse watchdog: starved peer audio requests a capture restart.

Incident (2026-08-11 20:44): a wedged native STT decode blocked the peer
pipeline for 6 minutes. The [Audio][peer] Pace ratio collapsed to 0.01 and
NEVER recovered — the capture queue sat wedged full of stale audio. The pace
report already measures exactly this; now consecutive collapsed reports (with
the capture queue actually dropping frames) fire on_pace_collapse, which the
controller wires to ResilientDesktopLoopbackSource.request_reopen.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

import puripuly_heart.core.audio.diagnostics as diagnostics_module
from puripuly_heart.core.audio.diagnostics import DiagnosticAudioSource
from puripuly_heart.core.audio.format import AudioFrameF32

_ROOT = Path(__file__).resolve().parents[2]


class FakeClock:
    def __init__(self) -> None:
        self.now = 100.0

    def __call__(self) -> float:
        return self.now


class ScriptedSource:
    def __init__(self) -> None:
        import asyncio

        self._queue: "asyncio.Queue" = asyncio.Queue()

    def feed(self, frame: AudioFrameF32 | None) -> None:
        self._queue.put_nowait(frame)

    async def frames(self):
        while True:
            item = await self._queue.get()
            if item is None:
                return
            yield item

    async def close(self) -> None:
        pass


def _tiny_frame() -> AudioFrameF32:
    # 1ms of audio — over a 61s report interval the ratio is ~0.00003.
    return AudioFrameF32(
        samples=np.zeros(48, dtype=np.float32), sample_rate_hz=48000, channels=1
    )


def _healthy_frame() -> AudioFrameF32:
    # 61s of audio at rate 1000 — over a 61s interval the ratio is ~1.0.
    return AudioFrameF32(
        samples=np.zeros(61_000, dtype=np.float32), sample_rate_hz=1000, channels=1
    )


def _build(monkeypatch: pytest.MonkeyPatch, *, on_pace_collapse, fields: dict):
    clock = FakeClock()
    # Replace the module's `time` binding only — patching time.monotonic
    # globally would also warp the event loop's clock.
    from types import SimpleNamespace

    monkeypatch.setattr(diagnostics_module, "time", SimpleNamespace(monotonic=clock))
    inner = ScriptedSource()
    logs: list[str] = []
    source = DiagnosticAudioSource(
        source=inner,
        channel_label="peer",
        is_detailed_enabled=lambda: False,
        log_basic=logs.append,
        extra_fields_provider=lambda: dict(fields),
        on_pace_collapse=on_pace_collapse,
    )
    return clock, inner, source, logs


async def _report(clock: FakeClock, inner: ScriptedSource, it, frame=None) -> None:
    """Advance one pace interval and deliver a frame so a report fires."""
    clock.now += 61.0
    inner.feed(frame if frame is not None else _tiny_frame())
    await it.__anext__()


@pytest.mark.asyncio
async def test_consecutive_low_reports_with_growing_drops_fire_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fields = {"queue_drops": 0, "callback_statuses": 0}
    fired: list[float] = []
    clock, inner, source, logs = _build(monkeypatch, on_pace_collapse=fired.append, fields=fields)
    it = source.frames().__aiter__()

    inner.feed(_tiny_frame())  # first frame only starts the wall clock
    await it.__anext__()

    fields["queue_drops"] = 10
    await _report(clock, inner, it)  # report 1: low, but only sets the drops baseline
    assert fired == []
    fields["queue_drops"] = 20
    await _report(clock, inner, it)  # report 2: low + drops grew -> streak 1
    assert fired == []
    fields["queue_drops"] = 30
    await _report(clock, inner, it)  # report 3: low + drops grew -> streak 2 -> fire
    assert len(fired) == 1
    assert fired[0] < 0.2
    assert any("Pace collapsed" in m for m in logs)

    # Throttled: the very next low report must NOT fire again immediately.
    fields["queue_drops"] = 40
    await _report(clock, inner, it)
    assert len(fired) == 1


@pytest.mark.asyncio
async def test_quiet_spell_without_queue_drops_never_fires(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Nothing playing also yields tiny ratios (wall time accrues while no
    audio exists) — but never drops frames. No drops growth, no restart."""
    fields = {"queue_drops": 10, "callback_statuses": 0}
    fired: list[float] = []
    clock, inner, source, _logs = _build(monkeypatch, on_pace_collapse=fired.append, fields=fields)
    it = source.frames().__aiter__()

    inner.feed(_tiny_frame())
    await it.__anext__()
    for _ in range(4):
        await _report(clock, inner, it)  # low ratio, drops frozen at 10
    assert fired == []


@pytest.mark.asyncio
async def test_healthy_ratio_resets_the_streak(monkeypatch: pytest.MonkeyPatch) -> None:
    fields = {"queue_drops": 0, "callback_statuses": 0}
    fired: list[float] = []
    clock, inner, source, _logs = _build(monkeypatch, on_pace_collapse=fired.append, fields=fields)
    it = source.frames().__aiter__()

    inner.feed(_tiny_frame())
    await it.__anext__()

    fields["queue_drops"] = 10
    await _report(clock, inner, it)  # baseline
    fields["queue_drops"] = 20
    await _report(clock, inner, it)  # streak 1
    await _report(clock, inner, it, frame=_healthy_frame())  # healthy -> reset
    fields["queue_drops"] = 30
    await _report(clock, inner, it)  # streak 1 again (not 2)
    assert fired == []
    fields["queue_drops"] = 40
    await _report(clock, inner, it)  # streak 2 -> fire
    assert len(fired) == 1


@pytest.mark.asyncio
async def test_drop_counter_reset_does_not_count_as_growth(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A capture reopen recreates the inner source and its counter restarts at
    zero — a smaller value must re-baseline, not read as new drops."""
    fields = {"queue_drops": 50, "callback_statuses": 0}
    fired: list[float] = []
    clock, inner, source, _logs = _build(monkeypatch, on_pace_collapse=fired.append, fields=fields)
    it = source.frames().__aiter__()

    inner.feed(_tiny_frame())
    await it.__anext__()

    await _report(clock, inner, it)  # baseline 50
    fields["queue_drops"] = 5
    await _report(clock, inner, it)  # counter reset -> re-baseline, no streak
    fields["queue_drops"] = 5
    await _report(clock, inner, it)  # flat -> no streak
    assert fired == []


@pytest.mark.asyncio
async def test_missing_drop_counter_falls_back_to_ratio_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No queue_drops field (no extra_fields wiring): the gate must stand
    aside rather than silently disable the recovery."""
    fields: dict = {}
    fired: list[float] = []
    clock, inner, source, _logs = _build(monkeypatch, on_pace_collapse=fired.append, fields=fields)
    it = source.frames().__aiter__()

    inner.feed(_tiny_frame())
    await it.__anext__()
    await _report(clock, inner, it)  # streak 1
    assert fired == []
    await _report(clock, inner, it)  # streak 2 -> fire
    assert len(fired) == 1


@pytest.mark.asyncio
async def test_no_callback_means_no_collapse_logging(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The self channel has no capture-restart machinery — without a callback
    the watchdog stays fully inert (no misleading 'requesting restart' log)."""
    fields = {"queue_drops": 0, "callback_statuses": 0}
    clock, inner, source, logs = _build(monkeypatch, on_pace_collapse=None, fields=fields)
    it = source.frames().__aiter__()

    inner.feed(_tiny_frame())
    await it.__anext__()
    for drops in (10, 20, 30, 40):
        fields["queue_drops"] = drops
        await _report(clock, inner, it)
    assert not any("Pace collapsed" in m for m in logs)


def test_controller_wires_peer_pace_collapse_to_capture_reopen() -> None:
    """The watchdog is only useful if the peer factory actually connects it to
    the resilient source's reopen machinery."""
    text = (_ROOT / "src/puripuly_heart/ui/controller.py").read_text(encoding="utf-8")
    start = text.index("def _create_peer_audio_source_from_runtime_config")
    end = text.index("DesktopPeerPipeline(", start)
    block = text[start:end]
    assert "on_pace_collapse" in block, "peer factory no longer wires the pace watchdog"
    assert "request_reopen" in block, "pace collapse no longer restarts peer capture"
