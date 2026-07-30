"""r315: the overlay wedge of 2026-07-29 must self-heal.

A silent bridge-reader death left the overlay headless for 90+ minutes:
window on screen, websocket ESTABLISHED, zero captions rendered, zero log
lines, supervisor reporting "connected". Three guards now make that state
impossible to sustain: bounded broadcast sends, an inbound-traffic watchdog,
and fatal (not silent) handling of cancelled runtime tasks."""
from __future__ import annotations

import asyncio
import time

import pytest

from puripuly_heart.core.overlay.bridge import OverlayBridge
from puripuly_heart.core.overlay.presenter import (
    OverlayPresentationCalibration,
    OverlayPresentationSnapshot,
)
from puripuly_heart.ui.desktop_overlay import (
    BRIDGE_TRAFFIC_STALL_S,
    DesktopOverlayRenderer,
    _RUNTIME_FAILURE_EXIT_CODE,
    _RuntimeOutcome,
)


class _WedgedConnection:
    """send() parks forever — the live failure's app-side symptom."""

    def __init__(self) -> None:
        self.closed = False

    async def send(self, message: str) -> None:
        await asyncio.Event().wait()

    async def close(self) -> None:
        self.closed = True


class _HealthyConnection:
    def __init__(self) -> None:
        self.sent: list[str] = []

    async def send(self, message: str) -> None:
        self.sent.append(message)

    async def close(self) -> None:
        pass


def _bridge(**kwargs) -> OverlayBridge:
    return OverlayBridge(
        session_token="t",
        initial_snapshot=OverlayPresentationSnapshot(
            revision=0, calibration=OverlayPresentationCalibration(), blocks=[]
        ),
        **kwargs,
    )


@pytest.mark.asyncio
async def test_broadcast_survives_wedged_connection() -> None:
    bridge = _bridge(broadcast_send_timeout_s=0.05)
    wedged, healthy = _WedgedConnection(), _HealthyConnection()
    bridge._authenticated_connections.add(wedged)  # type: ignore[arg-type]
    bridge._authenticated_connections.add(healthy)  # type: ignore[arg-type]

    await asyncio.wait_for(
        bridge._broadcast_json({"type": "heartbeat"}), timeout=2.0
    )

    assert wedged not in bridge._authenticated_connections
    assert wedged.closed
    assert healthy in bridge._authenticated_connections
    assert healthy.sent  # the healthy consumer still got the message


class _FakeLifecycleSink:
    def __init__(self) -> None:
        self.events: list[dict] = []

    async def emit(self, event: dict) -> None:
        self.events.append(event)


def _bare_renderer() -> DesktopOverlayRenderer:
    renderer = DesktopOverlayRenderer.__new__(DesktopOverlayRenderer)
    renderer._shutdown_event = asyncio.Event()
    renderer._websocket = None
    renderer.lifecycle_sink = _FakeLifecycleSink()
    return renderer


@pytest.mark.asyncio
async def test_watchdog_restarts_on_stalled_bridge_traffic() -> None:
    renderer = _bare_renderer()
    renderer._last_bridge_inbound_monotonic = (
        time.monotonic() - BRIDGE_TRAFFIC_STALL_S - 1.0
    )

    outcome = await asyncio.wait_for(renderer._heartbeat_loop(), timeout=3.0)

    assert isinstance(outcome, _RuntimeOutcome)
    assert outcome.exit_code == _RUNTIME_FAILURE_EXIT_CODE
    assert any(
        e.get("failure_reason") == "bridge_traffic_stalled"
        for e in renderer.lifecycle_sink.events
    )


@pytest.mark.asyncio
async def test_watchdog_quiet_while_traffic_fresh_and_exits_on_shutdown() -> None:
    renderer = _bare_renderer()
    renderer._last_bridge_inbound_monotonic = time.monotonic()

    task = asyncio.ensure_future(renderer._heartbeat_loop())
    await asyncio.sleep(1.5)
    assert not task.done()  # fresh traffic: no restart
    renderer._shutdown_event.set()
    assert (await asyncio.wait_for(task, timeout=3.0)) is None


@pytest.mark.asyncio
async def test_cancelled_runtime_task_is_fatal_outside_shutdown() -> None:
    renderer = _bare_renderer()

    async def _forever() -> None:
        await asyncio.Event().wait()

    task = asyncio.ensure_future(_forever())
    renderer._tasks = {task}
    task.cancel()

    outcome = await asyncio.wait_for(
        renderer._wait_for_runtime_outcome(), timeout=3.0
    )
    assert outcome.exit_code == _RUNTIME_FAILURE_EXIT_CODE
    assert any(
        e.get("failure_reason") == "runtime_task_cancelled"
        for e in renderer.lifecycle_sink.events
    )


@pytest.mark.asyncio
async def test_cancelled_task_during_shutdown_stays_graceful() -> None:
    renderer = _bare_renderer()
    renderer._shutdown_event.set()

    async def _forever() -> None:
        await asyncio.Event().wait()

    task = asyncio.ensure_future(_forever())
    renderer._tasks = {task}
    task.cancel()

    outcome = await asyncio.wait_for(
        renderer._wait_for_runtime_outcome(), timeout=3.0
    )
    assert outcome.exit_code != _RUNTIME_FAILURE_EXIT_CODE
