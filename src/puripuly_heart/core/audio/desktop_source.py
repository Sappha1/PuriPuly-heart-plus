from __future__ import annotations

import asyncio
import contextlib
import logging
import platform
import queue
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Sequence

import janus
import numpy as np

from puripuly_heart.core.audio.format import AudioFrameF32

logger = logging.getLogger(__name__)

_CALLBACK_WARNING_MIN_INTERVAL_S = 1.0


@dataclass(frozen=True, slots=True)
class DesktopLoopbackDevice:
    index: int
    name: str
    channels: int
    sample_rate_hz: int


@dataclass(frozen=True, slots=True)
class DesktopLoopbackDeviceResolution:
    device: DesktopLoopbackDevice | None
    used_default_fallback: bool


@dataclass(slots=True)
class DesktopLoopbackDeviceResolver:
    devices: Sequence[DesktopLoopbackDevice | str]
    default_device: DesktopLoopbackDevice | str | None = None

    def resolve(self, *, saved_device_name: str) -> DesktopLoopbackDevice | str | None:
        return self.resolve_with_metadata(saved_device_name=saved_device_name).device

    def resolve_with_metadata(self, *, saved_device_name: str) -> DesktopLoopbackDeviceResolution:
        if saved_device_name:
            for device in self.devices:
                if self._device_name(device) == saved_device_name:
                    return DesktopLoopbackDeviceResolution(
                        device=device, used_default_fallback=False
                    )

        return DesktopLoopbackDeviceResolution(
            device=self.default_device,
            used_default_fallback=bool(saved_device_name and self.default_device is not None),
        )

    @staticmethod
    def _device_name(device: DesktopLoopbackDevice | str) -> str:
        if isinstance(device, DesktopLoopbackDevice):
            return device.name
        return str(device)


@dataclass(slots=True)
class DesktopLoopbackAudioSource:
    device_name: str = ""
    frames_per_buffer: int = 1024
    # ~21ms per 1024-sample frame at 48kHz: 64 frames was only ~1.4s of buffer,
    # so the 7-9s local model load (and CPU inference bursts on weak machines)
    # overflowed the queue and dropped audio — shredded chunks then transcribed
    # as hallucination garbage. 512 frames ≈ 11s (~4MB) rides out the stall.
    max_queue_frames: int = 512

    _queue: janus.Queue[np.ndarray | None] = field(init=False, repr=False)
    _stream: object = field(init=False, repr=False)
    _manager: object = field(init=False, repr=False)
    _closed: bool = field(init=False, default=False)
    _actual_sample_rate_hz: int = field(init=False, repr=False)
    _resolved_device: DesktopLoopbackDevice = field(init=False, repr=False)
    _used_default_fallback: bool = field(init=False, default=False, repr=False)
    _callback_status_count: int = field(init=False, default=0, repr=False)
    _queue_drop_count: int = field(init=False, default=0, repr=False)
    _last_callback_status: object | None = field(init=False, default=None, repr=False)
    _last_reported_callback_status_count: int = field(init=False, default=0, repr=False)
    _last_reported_queue_drop_count: int = field(init=False, default=0, repr=False)
    _last_callback_warning_monotonic_s: float = field(init=False, default=float("-inf"), repr=False)

    def __post_init__(self) -> None:
        if self.frames_per_buffer <= 0:
            raise ValueError("frames_per_buffer must be > 0")
        if self.max_queue_frames <= 0:
            raise ValueError("max_queue_frames must be > 0")

        pyaudio = _import_pyaudiowpatch()
        self._queue = janus.Queue(maxsize=self.max_queue_frames)

        manager = pyaudio.PyAudio()
        try:
            devices = _enumerate_loopback_devices(manager)
            default_device = _get_default_loopback_device(manager)
            resolution = DesktopLoopbackDeviceResolver(
                devices=devices, default_device=default_device
            ).resolve_with_metadata(saved_device_name=self.device_name)
            resolved = resolution.device
            if not isinstance(resolved, DesktopLoopbackDevice):
                raise RuntimeError("No Windows loopback output device is available")
            if resolution.used_default_fallback:
                logger.warning(
                    "Saved desktop loopback device unavailable, falling back to default output "
                    "loopback (saved=%r, resolved=%r)",
                    self.device_name,
                    resolved.name,
                )

            self._resolved_device = resolved
            self._actual_sample_rate_hz = resolved.sample_rate_hz
            self._used_default_fallback = resolution.used_default_fallback
            self._manager = manager

            continue_flag = getattr(pyaudio, "paContinue", 0)
            float32_format = getattr(pyaudio, "paFloat32")

            def _callback(in_data, _frame_count, _time_info, status_flags):
                if self._closed:
                    return (None, continue_flag)
                if status_flags:
                    self._callback_status_count += 1
                    self._last_callback_status = status_flags
                if in_data:
                    try:
                        samples = np.frombuffer(in_data, dtype=np.float32).copy()
                        self._queue.sync_q.put_nowait(samples)
                    except queue.Full:
                        self._queue_drop_count += 1
                        return (None, continue_flag)
                return (None, continue_flag)

            stream = manager.open(
                format=float32_format,
                channels=resolved.channels,
                rate=resolved.sample_rate_hz,
                input=True,
                input_device_index=resolved.index,
                frames_per_buffer=self.frames_per_buffer,
                stream_callback=_callback,
            )
            stream.start_stream()
            self._stream = stream
        except Exception:
            with contextlib.suppress(Exception):
                manager.terminate()
            raise

    @property
    def resolved_device_name(self) -> str:
        return self._resolved_device.name

    @property
    def resolved_device_index(self) -> int:
        return self._resolved_device.index

    @property
    def resolved_channels(self) -> int:
        return self._resolved_device.channels

    @property
    def actual_sample_rate_hz(self) -> int:
        return self._actual_sample_rate_hz

    @property
    def used_default_fallback(self) -> bool:
        return self._used_default_fallback

    @property
    def callback_status_count(self) -> int:
        return self._callback_status_count

    @property
    def queue_drop_count(self) -> int:
        return self._queue_drop_count

    @property
    def last_callback_status(self) -> object | None:
        return self._last_callback_status

    def stream_is_active(self) -> bool | None:
        """True/False from PortAudio, or None when the stream can't be queried."""
        with contextlib.suppress(Exception):
            return bool(self._stream.is_active())
        return None

    async def frames(self) -> AsyncIterator[AudioFrameF32]:
        while True:
            item = await self._queue.async_q.get()
            if item is None:
                return
            self._report_callback_warnings_from_consumer()
            yield AudioFrameF32(
                samples=item,
                sample_rate_hz=self._actual_sample_rate_hz,
                channels=self._resolved_device.channels,
            )

    def _report_callback_warnings_from_consumer(self) -> None:
        callback_status_count = self._callback_status_count
        queue_drop_count = self._queue_drop_count
        status_new_count = callback_status_count - self._last_reported_callback_status_count
        drop_new_count = queue_drop_count - self._last_reported_queue_drop_count
        if status_new_count <= 0 and drop_new_count <= 0:
            return

        now = time.monotonic()
        if now - self._last_callback_warning_monotonic_s < _CALLBACK_WARNING_MIN_INTERVAL_S:
            return

        self._last_callback_warning_monotonic_s = now
        self._last_reported_callback_status_count = callback_status_count
        self._last_reported_queue_drop_count = queue_drop_count
        with contextlib.suppress(Exception):
            logger.warning(
                "Desktop loopback audio callback status/drop observed: "
                "callback status count=%s callback status new=%s "
                "last_status=%s queue drop count=%s queue drop new=%s",
                callback_status_count,
                max(0, status_new_count),
                self._last_callback_status,
                queue_drop_count,
                max(0, drop_new_count),
            )

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True

        with contextlib.suppress(Exception):
            self._stream.stop_stream()
        with contextlib.suppress(Exception):
            self._stream.close()
        with contextlib.suppress(Exception):
            self._manager.terminate()

        try:
            self._queue.sync_q.put_nowait(None)
        except Exception:
            pass

        self._queue.close()
        with contextlib.suppress(Exception):
            await self._queue.wait_closed()


@dataclass(frozen=True, slots=True)
class DesktopLoopbackProbe:
    """Fresh snapshot of the loopback devices Windows currently exposes."""

    devices: tuple[DesktopLoopbackDevice, ...]
    default_device: DesktopLoopbackDevice | None


def _probe_loopback_devices() -> DesktopLoopbackProbe:
    # PortAudio snapshots the device list at Pa_Initialize, so a long-lived
    # manager never sees hot-plug changes — a fresh PyAudio() per probe does.
    pyaudio = _import_pyaudiowpatch()
    manager = pyaudio.PyAudio()
    try:
        devices = tuple(_enumerate_loopback_devices(manager))
        default_device = _get_default_loopback_device(manager)
    finally:
        with contextlib.suppress(Exception):
            manager.terminate()
    return DesktopLoopbackProbe(devices=devices, default_device=default_device)


@dataclass(slots=True)
class ResilientDesktopLoopbackSource:
    """DesktopLoopbackAudioSource with a starvation watchdog and auto-reopen.

    WASAPI loopback dies without an error when its endpoint is invalidated
    (headphones unplugged, driver power event, endpoint re-created): the
    capture callback simply stops firing and the pipeline starves forever.
    This wrapper notices "no frames for starvation_timeout_s", verifies the
    stream is actually dead (loopback also goes quiet while nothing renders,
    which is NOT a failure), and re-opens capture on whatever device is live.
    """

    device_name: str = ""
    starvation_timeout_s: float = 10.0
    reopen_backoff_s: tuple[float, ...] = (10.0, 20.0, 30.0)
    fallback_recheck_interval_s: float = 30.0
    log_basic: Callable[[str], object] | None = None
    log_detailed: Callable[[str], object] | None = None
    source_factory: Callable[[str], Any] = DesktopLoopbackAudioSource
    probe_devices: Callable[[], DesktopLoopbackProbe] = _probe_loopback_devices

    _inner: Any = field(init=False, default=None, repr=False)
    _closed: bool = field(init=False, default=False)
    _closed_event: asyncio.Event = field(init=False, repr=False)
    _idle_logged: bool = field(init=False, default=False)
    _last_fallback_recheck_monotonic_s: float = field(
        init=False, default=float("-inf"), repr=False
    )

    def __post_init__(self) -> None:
        self._closed_event = asyncio.Event()
        # Eager first open, same semantics as the raw source: enabling peer
        # with no loopback device at all still fails loudly right away.
        self._inner = self.source_factory(self.device_name)

    # Diagnostics passthrough (DiagnosticAudioSource and the controller read these).
    @property
    def resolved_device_name(self) -> str:
        return getattr(self._inner, "resolved_device_name", "")

    @property
    def resolved_device_index(self) -> int:
        return getattr(self._inner, "resolved_device_index", -1)

    @property
    def resolved_channels(self) -> int:
        return getattr(self._inner, "resolved_channels", 1)

    @property
    def actual_sample_rate_hz(self) -> int:
        return getattr(self._inner, "actual_sample_rate_hz", 48000)

    @property
    def used_default_fallback(self) -> bool:
        return bool(getattr(self._inner, "used_default_fallback", False))

    @property
    def callback_status_count(self) -> int:
        return getattr(self._inner, "callback_status_count", 0)

    @property
    def queue_drop_count(self) -> int:
        return getattr(self._inner, "queue_drop_count", 0)

    @property
    def last_callback_status(self) -> object | None:
        return getattr(self._inner, "last_callback_status", None)

    def _emit_basic(self, message: str) -> None:
        with contextlib.suppress(Exception):
            if self.log_basic is not None:
                self.log_basic(message)
                return
        logger.info(message)

    def _emit_detailed(self, message: str) -> None:
        with contextlib.suppress(Exception):
            if self.log_detailed is not None:
                self.log_detailed(message)
                return
        logger.debug(message)

    async def frames(self) -> AsyncIterator[AudioFrameF32]:
        while not self._closed:
            inner = self._inner
            if inner is None:
                inner = await self._reopen_with_backoff()
                if inner is None:
                    return
            iterator = inner.frames().__aiter__()
            next_task: asyncio.Task | None = None
            reopen_reason: str | None = None
            try:
                while True:
                    if next_task is None:
                        next_task = asyncio.ensure_future(iterator.__anext__())
                    # asyncio.wait does NOT cancel on timeout — the generator
                    # stays suspended and we can keep waiting on the same task
                    # (wait_for would cancel it and kill the generator).
                    done, _ = await asyncio.wait(
                        {next_task}, timeout=self.starvation_timeout_s
                    )
                    if not done:
                        if self._closed:
                            return
                        reopen_reason = await self._starved_health_reason(inner)
                        if reopen_reason is None:
                            if not self._idle_logged:
                                self._idle_logged = True
                                self._emit_detailed(
                                    "[PeerAudio] No loopback frames for "
                                    f"{self.starvation_timeout_s:.0f}s but device "
                                    f"'{self.resolved_device_name}' looks healthy — "
                                    "nothing is playing; still listening"
                                )
                            continue
                        break
                    task, next_task = next_task, None
                    try:
                        frame = task.result()
                    except StopAsyncIteration:
                        if self._closed:
                            return
                        reopen_reason = "capture stream ended unexpectedly"
                        break
                    except Exception as exc:
                        if self._closed:
                            return
                        reopen_reason = f"capture stream failed: {exc}"
                        break
                    self._idle_logged = False
                    yield frame
                    if await self._saved_device_returned():
                        reopen_reason = (
                            f"saved output device '{self.device_name}' is available again"
                        )
                        break
            finally:
                if next_task is not None:
                    next_task.cancel()
                    with contextlib.suppress(BaseException):
                        await next_task
            if self._closed:
                return
            self._emit_basic(
                f"[PeerAudio] Reopening desktop capture: {reopen_reason} "
                f"(was device='{self.resolved_device_name}')"
            )
            await self._close_inner()

    async def _starved_health_reason(self, inner: Any) -> str | None:
        """Return why the stream should be reopened, or None if it's just idle."""
        stream_active = None
        with contextlib.suppress(Exception):
            stream_active = inner.stream_is_active()
        if stream_active is False:
            return "stream reports inactive"

        try:
            probe = await asyncio.to_thread(self.probe_devices)
        except Exception:
            # Can't verify — don't churn the stream on a probe failure.
            return None

        open_name = str(getattr(inner, "resolved_device_name", "") or "")
        current = next((d for d in probe.devices if d.name == open_name), None)
        if current is None:
            return f"device '{open_name}' disappeared"
        if current.index != int(getattr(inner, "resolved_device_index", -1)):
            return f"device '{open_name}' endpoint was re-created"
        if not self.device_name or bool(getattr(inner, "used_default_fallback", False)):
            default_name = probe.default_device.name if probe.default_device else ""
            if default_name and default_name != open_name:
                return f"default output changed to '{default_name}'"
        return None

    async def _saved_device_returned(self) -> bool:
        """While running on a fallback device, watch for the saved one to return."""
        if self._closed or not self.device_name:
            return False
        inner = self._inner
        if inner is None or not bool(getattr(inner, "used_default_fallback", False)):
            return False
        now = time.monotonic()
        if now - self._last_fallback_recheck_monotonic_s < self.fallback_recheck_interval_s:
            return False
        self._last_fallback_recheck_monotonic_s = now
        try:
            probe = await asyncio.to_thread(self.probe_devices)
        except Exception:
            return False
        return any(d.name == self.device_name for d in probe.devices)

    async def _reopen_with_backoff(self) -> Any | None:
        attempt = 0
        while not self._closed:
            try:
                # Must run ON the loop: the source's janus.Queue binds to the
                # running loop at creation and raises in a worker thread. The
                # ~100-300ms PyAudio init only happens on rare reopens.
                inner = self.source_factory(self.device_name)
            except Exception as exc:
                delay = self.reopen_backoff_s[
                    min(attempt, len(self.reopen_backoff_s) - 1)
                ]
                if attempt == 0:
                    self._emit_basic(
                        f"[PeerAudio] Could not reopen desktop capture ({exc}) — "
                        f"retrying every {delay:.0f}s until an output device is back"
                    )
                else:
                    self._emit_detailed(
                        f"[PeerAudio] Capture reopen attempt {attempt + 1} failed: {exc}"
                    )
                attempt += 1
                with contextlib.suppress(asyncio.TimeoutError, TimeoutError):
                    await asyncio.wait_for(self._closed_event.wait(), timeout=delay)
                continue
            self._inner = inner
            self._emit_basic(
                "[PeerAudio] Capture reconnected: "
                f"device='{getattr(inner, 'resolved_device_name', '')}' "
                f"rate={getattr(inner, 'actual_sample_rate_hz', 0)}Hz "
                f"fallback={bool(getattr(inner, 'used_default_fallback', False))}"
            )
            return inner
        return None

    async def _close_inner(self) -> None:
        inner, self._inner = self._inner, None
        if inner is not None:
            with contextlib.suppress(Exception):
                await inner.close()

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._closed_event.set()
        await self._close_inner()


def _import_pyaudiowpatch() -> Any:
    if platform.system() != "Windows":
        raise RuntimeError("Desktop loopback capture requires Windows")

    import pyaudiowpatch as pyaudio  # type: ignore

    return pyaudio


def _enumerate_loopback_devices(manager: Any) -> list[DesktopLoopbackDevice]:
    return [_coerce_device_info(info) for info in manager.get_loopback_device_info_generator()]


def _get_default_loopback_device(manager: Any) -> DesktopLoopbackDevice | None:
    with contextlib.suppress(Exception):
        return _coerce_device_info(manager.get_default_wasapi_loopback())

    with contextlib.suppress(Exception):
        default_output = manager.get_default_wasapi_device(deviceType="output")
        if default_output is None:
            return None
        analogue = manager.get_wasapi_loopback_analogue_by_dict(default_output)
        return _coerce_device_info(analogue)

    return None


def _coerce_device_info(info: Any) -> DesktopLoopbackDevice:
    if not isinstance(info, dict):
        raise TypeError("loopback device info must be a dictionary")

    index = int(info.get("index", -1))
    name = str(info.get("name", "") or "")
    channels = int(
        info.get(
            "maxInputChannels",
            info.get(
                "max_input_channels",
                info.get("maxOutputChannels", info.get("max_output_channels", 0)),
            ),
        )
        or 0
    )
    channels = max(channels, 1)
    sample_rate_raw = info.get("defaultSampleRate", info.get("default_sample_rate", 48000.0))
    sample_rate_hz = int(round(float(sample_rate_raw or 48000.0)))

    if index < 0 or not name:
        raise ValueError("loopback device info is missing a usable index or name")

    return DesktopLoopbackDevice(
        index=index,
        name=name,
        channels=channels,
        sample_rate_hz=sample_rate_hz,
    )
