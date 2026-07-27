"""ResilientDesktopLoopbackSource: starvation watchdog + auto-reopen.

Regression (r281): unplugging the headphones invalidated the WASAPI loopback
endpoint and the capture callback silently stopped firing — peer stayed deaf
for 14 hours with zero log lines until an app restart (2026-07-24).
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

import numpy as np
import pytest

from puripuly_heart.core.audio.desktop_pipeline import DesktopPeerPipeline
from puripuly_heart.core.audio.desktop_source import (
    DesktopLoopbackDevice,
    DesktopLoopbackProbe,
    ResilientDesktopLoopbackSource,
)
from puripuly_heart.core.audio.format import AudioFrameF32


def _frame(rate: int = 48000, channels: int = 1) -> AudioFrameF32:
    return AudioFrameF32(
        samples=np.zeros(64 * channels, dtype=np.float32),
        sample_rate_hz=rate,
        channels=channels,
    )


@dataclass(slots=True)
class FakeInnerSource:
    resolved_device_name: str = "Speakers"
    resolved_device_index: int = 3
    resolved_channels: int = 1
    actual_sample_rate_hz: int = 48000
    used_default_fallback: bool = False
    stream_active: bool | None = True
    closed: bool = False
    _queue: asyncio.Queue = field(default_factory=asyncio.Queue)

    def feed(self, item: AudioFrameF32 | None) -> None:
        self._queue.put_nowait(item)

    def stream_is_active(self) -> bool | None:
        return self.stream_active

    async def frames(self):
        while True:
            item = await self._queue.get()
            if item is None:
                return
            yield item

    async def close(self) -> None:
        self.closed = True


def _probe(*devices: DesktopLoopbackDevice, default: DesktopLoopbackDevice | None = None):
    snapshot = DesktopLoopbackProbe(devices=tuple(devices), default_device=default)
    return lambda: snapshot


_SPEAKERS = DesktopLoopbackDevice(index=3, name="Speakers", channels=1, sample_rate_hz=48000)
_HEADPHONES = DesktopLoopbackDevice(index=7, name="Headphones", channels=2, sample_rate_hz=44100)


def _make_source(factory_sources, probe, **kwargs):
    created: list[FakeInnerSource] = []

    def factory(device_name: str):
        result = factory_sources[len(created)]
        if isinstance(result, Exception):
            created.append(None)  # count the attempt
            raise result
        created.append(result)
        return result

    source = ResilientDesktopLoopbackSource(
        device_name=kwargs.pop("device_name", ""),
        starvation_timeout_s=kwargs.pop("starvation_timeout_s", 0.05),
        reopen_backoff_s=kwargs.pop("reopen_backoff_s", (0.05,)),
        fallback_recheck_interval_s=kwargs.pop("fallback_recheck_interval_s", 3600.0),
        source_factory=factory,
        probe_devices=probe,
        **kwargs,
    )
    return source, created


@pytest.mark.asyncio
async def test_starved_dead_device_reopens_and_frames_continue() -> None:
    first, second = FakeInnerSource(), FakeInnerSource()
    logs: list[str] = []
    # Probe: the open device is GONE (unplugged) — must reopen.
    source, created = _make_source(
        [first, second], _probe(_HEADPHONES, default=_HEADPHONES), log_basic=logs.append
    )
    first.feed(_frame())
    second.feed(_frame())

    it = source.frames().__aiter__()
    assert (await asyncio.wait_for(it.__anext__(), 1.0)) is not None
    # No more frames from `first` → starvation → probe says device disappeared.
    assert (await asyncio.wait_for(it.__anext__(), 2.0)) is not None
    assert len(created) == 2
    assert first.closed
    assert any("Reopening desktop capture" in m and "disappeared" in m for m in logs)
    assert any("Capture reconnected" in m for m in logs)
    await source.close()


@pytest.mark.asyncio
async def test_starved_but_healthy_device_does_not_reopen() -> None:
    first = FakeInnerSource()
    # Probe: same device, same index, stream active, not fallback → just idle.
    source, created = _make_source([first], _probe(_SPEAKERS, default=_SPEAKERS))
    first.feed(_frame())

    it = source.frames().__aiter__()
    await asyncio.wait_for(it.__anext__(), 1.0)
    late = asyncio.ensure_future(it.__anext__())
    await asyncio.sleep(0.2)  # several starvation timeouts of silence
    assert len(created) == 1  # never reopened
    first.feed(_frame())  # audio comes back on the same healthy stream
    assert (await asyncio.wait_for(late, 1.0)) is not None
    await source.close()


@pytest.mark.asyncio
async def test_stream_end_reopens() -> None:
    first, second = FakeInnerSource(), FakeInnerSource()
    logs: list[str] = []
    source, created = _make_source(
        [first, second], _probe(_SPEAKERS, default=_SPEAKERS), log_basic=logs.append
    )
    first.feed(_frame())
    first.feed(None)  # inner stream ends unexpectedly
    second.feed(_frame())

    it = source.frames().__aiter__()
    await asyncio.wait_for(it.__anext__(), 1.0)
    assert (await asyncio.wait_for(it.__anext__(), 2.0)) is not None
    assert len(created) == 2
    assert any("ended unexpectedly" in m for m in logs)
    await source.close()


@pytest.mark.asyncio
async def test_reopen_failure_retries_with_backoff_then_succeeds() -> None:
    first, second = FakeInnerSource(), FakeInnerSource()
    logs: list[str] = []
    source, created = _make_source(
        [first, RuntimeError("no device"), second],
        _probe(default=None),  # nothing plugged in at all
        log_basic=logs.append,
    )
    first.feed(_frame())
    first.feed(None)
    second.feed(_frame())

    it = source.frames().__aiter__()
    await asyncio.wait_for(it.__anext__(), 1.0)
    assert (await asyncio.wait_for(it.__anext__(), 3.0)) is not None
    assert len(created) == 3  # first + failed attempt + success
    assert any("Could not reopen desktop capture" in m for m in logs)
    await source.close()


@pytest.mark.asyncio
async def test_switches_back_when_saved_device_returns() -> None:
    fallback = FakeInnerSource(
        resolved_device_name="Speakers", used_default_fallback=True
    )
    saved = FakeInnerSource(
        resolved_device_name="Headphones",
        resolved_device_index=7,
        actual_sample_rate_hz=44100,
    )
    logs: list[str] = []
    source, created = _make_source(
        [fallback, saved],
        _probe(_SPEAKERS, _HEADPHONES, default=_SPEAKERS),  # saved device is back
        device_name="Headphones",
        fallback_recheck_interval_s=0.0,
        log_basic=logs.append,
    )
    fallback.feed(_frame())
    saved.feed(_frame())

    it = source.frames().__aiter__()
    await asyncio.wait_for(it.__anext__(), 1.0)  # triggers post-frame recheck
    assert (await asyncio.wait_for(it.__anext__(), 2.0)) is not None
    assert len(created) == 2
    assert fallback.closed
    assert any("available again" in m for m in logs)
    await source.close()


@pytest.mark.asyncio
async def test_close_during_starvation_exits_promptly() -> None:
    first = FakeInnerSource()
    source, _ = _make_source([first], _probe(_SPEAKERS, default=_SPEAKERS))
    first.feed(_frame())

    it = source.frames().__aiter__()
    await asyncio.wait_for(it.__anext__(), 1.0)
    pending = asyncio.ensure_future(it.__anext__())
    await asyncio.sleep(0.02)
    await source.close()
    with pytest.raises((StopAsyncIteration, asyncio.CancelledError)):
        await asyncio.wait_for(pending, 1.0)
    assert first.closed


@pytest.mark.asyncio
async def test_pipeline_rebuilds_resampler_on_format_change() -> None:
    class TwoFormatSource:
        async def frames(self):
            for _ in range(3):
                yield _frame(rate=48000, channels=1)
            for _ in range(3):
                yield _frame(rate=44100, channels=2)

        async def close(self) -> None:
            pass

    pipeline = DesktopPeerPipeline(source=TwoFormatSource(), target_sample_rate_hz=16000)
    outputs = [frame async for frame in pipeline.frames()]
    assert outputs, "pipeline must keep producing across a format change"
    assert all(f.sample_rate_hz == 16000 for f in outputs)


@pytest.mark.asyncio
async def test_silent_default_capture_follows_audio_to_other_endpoint() -> None:
    # SteamVR case: default-bound capture sits on the silent headphones while
    # sound plays on the HMD endpoint — the watchdog must follow the audio.
    on_headphones = FakeInnerSource(resolved_device_name="Headphones", resolved_device_index=7)
    on_hmd = FakeInnerSource(resolved_device_name="Index HMD", resolved_device_index=9)
    hmd = DesktopLoopbackDevice(index=9, name="Index HMD", channels=2, sample_rate_hz=48000)
    headphones = DesktopLoopbackDevice(index=7, name="Headphones", channels=2, sample_rate_hz=48000)
    logs: list[str] = []
    probed: list[str] = []

    def activity(device):
        probed.append(device.name)
        return 0.2 if device.name == "Index HMD" else 0.0

    source, created = _make_source(
        [on_headphones, on_hmd],
        _probe(headphones, hmd, default=headphones),
        device_name="",
        log_basic=logs.append,
    )
    source.probe_activity = activity
    source.audio_seek_interval_s = 0.0
    on_headphones.feed(_frame())
    on_hmd.feed(_frame())

    it = source.frames().__aiter__()
    await asyncio.wait_for(it.__anext__(), 1.0)
    # Starvation on the (present, "healthy") default -> seek -> HMD has audio.
    assert (await asyncio.wait_for(it.__anext__(), 3.0)) is not None
    assert len(created) == 2
    assert created[1] is on_hmd
    assert "Index HMD" in probed
    assert any("audio is playing on 'Index HMD'" in m for m in logs)
    await source.close()


@pytest.mark.asyncio
async def test_pinned_device_never_audio_seeks() -> None:
    pinned = FakeInnerSource(resolved_device_name="Speakers", resolved_device_index=3)
    other = DesktopLoopbackDevice(index=9, name="Index HMD", channels=2, sample_rate_hz=48000)
    probed: list[str] = []

    source, created = _make_source(
        [pinned], _probe(_SPEAKERS, other, default=_SPEAKERS), device_name="Speakers"
    )
    source.probe_activity = lambda d: probed.append(d.name) or 1.0
    source.audio_seek_interval_s = 0.0
    pinned.feed(_frame())

    it = source.frames().__aiter__()
    await asyncio.wait_for(it.__anext__(), 1.0)
    late = asyncio.ensure_future(it.__anext__())
    await asyncio.sleep(0.25)  # several starvation ticks
    assert probed == []       # pinned: no seeking
    assert len(created) == 1
    pinned.feed(_frame())
    await asyncio.wait_for(late, 1.0)
    await source.close()
