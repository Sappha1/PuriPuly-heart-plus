from __future__ import annotations

import contextlib
import importlib
import queue
import threading
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from typing import Protocol

import janus
import numpy as np

from puripuly_heart.config.process_capture_platform import (
    ProcessCapturePlatformAvailability,
    get_process_capture_platform_availability,
)
from puripuly_heart.config.process_capture_resolution import ResolvedProcessCaptureIdentity
from puripuly_heart.core.audio.format import AudioFrameF32

PROCESS_CAPTURE_SAMPLE_RATE_HZ = 48000
PROCESS_CAPTURE_CHANNELS = 2


class ProcessAudioCaptureSetupError(RuntimeError):
    pass


class ProcessAudioCaptureUnavailableError(RuntimeError):
    pass


class ProcessAudioCapturePort(Protocol):
    def start(self) -> None: ...
    def close(self) -> None: ...


class ProcessAudioCaptureFactory(Protocol):
    def create(
        self,
        *,
        pid: int,
        on_data: Callable[[bytes, int], None],
    ) -> ProcessAudioCapturePort: ...


class ProcessIdentityWatchPort(Protocol):
    @property
    def identity_verified(self) -> bool: ...

    def close(self) -> None: ...


class ProcessIdentityWatcher(Protocol):
    def watch(
        self,
        identity: ResolvedProcessCaptureIdentity,
        on_terminal: Callable[[], None],
    ) -> ProcessIdentityWatchPort: ...


@dataclass(frozen=True, slots=True)
class ProcTapProcessAudioCaptureFactory:
    platform_availability: Callable[[], ProcessCapturePlatformAvailability] = (
        get_process_capture_platform_availability
    )

    def create(
        self,
        *,
        pid: int,
        on_data: Callable[[bytes, int], None],
    ) -> ProcessAudioCapturePort:
        if not self.platform_availability().available:
            raise ProcessAudioCaptureUnavailableError("process capture platform is unavailable")
        module = importlib.import_module("proctap")
        capture_type = getattr(module, "ProcessAudioCapture")
        capture = capture_type(pid, on_data=on_data)
        try:
            verify_proctap_process_specific(capture)
        except Exception as exc:
            with contextlib.suppress(Exception):
                capture.close()
            if isinstance(exc, ProcessAudioCaptureSetupError):
                raise
            raise ProcessAudioCaptureSetupError(
                "process capture mode could not be verified"
            ) from None
        return capture


def verify_proctap_process_specific(capture: object) -> bool:
    backend = getattr(capture, "_backend", None)
    native = getattr(backend, "_native", None)
    verifier = getattr(native, "is_process_specific", None)
    if not callable(verifier):
        raise ProcessAudioCaptureSetupError("process capture mode could not be verified")
    try:
        verified = verifier()
    except Exception:
        raise ProcessAudioCaptureSetupError("process capture mode could not be verified") from None
    if verified is not True:
        raise ProcessAudioCaptureSetupError("process capture mode could not be verified")
    return True


@dataclass(slots=True)
class ProcessAudioCaptureSource:
    identity: ResolvedProcessCaptureIdentity
    watcher: ProcessIdentityWatcher
    # r629: 64 frames was 0.64 s of audio (10 ms WASAPI packets). The consumer
    # runs on the event loop, so every stall longer than that — a local
    # speech decode, a UI update burst — DISCARDED the friend's audio: 300-900
    # dropped packets per minute during conversation, up to 9 s lost per
    # minute. ~10 s of buffer turns a stall into a moment of latency that
    # drains once the loop is free, instead of words that never existed;
    # when it does fill, the OLDEST packet is shed (see _on_data) so a
    # consumer that is chronically behind still hears the most recent audio.
    max_queue_frames: int = 1000
    capture_factory: ProcessAudioCaptureFactory = field(
        default_factory=ProcTapProcessAudioCaptureFactory
    )
    platform_availability: Callable[[], ProcessCapturePlatformAvailability] = (
        get_process_capture_platform_availability
    )

    _queue: janus.Queue[np.ndarray | None] = field(init=False, repr=False)
    _capture: ProcessAudioCapturePort | None = field(init=False, default=None, repr=False)
    _watch: ProcessIdentityWatchPort | None = field(init=False, default=None, repr=False)
    _closed: bool = field(init=False, default=False, repr=False)
    _terminal_reason: str | None = field(init=False, default=None, repr=False)
    _queue_drop_count: int = field(init=False, default=0, repr=False)
    _lock: threading.RLock = field(init=False, default_factory=threading.RLock, repr=False)

    def __post_init__(self) -> None:
        if self.max_queue_frames <= 0:
            raise ValueError("max_queue_frames must be > 0")
        self._queue = janus.Queue(maxsize=self.max_queue_frames)
        if not self.platform_availability().available:
            self._queue.close()
            raise ProcessAudioCaptureUnavailableError("process capture platform is unavailable")
        try:
            capture = self.capture_factory.create(pid=self.identity.pid, on_data=self._on_data)
            self._capture = capture
            watch = self.watcher.watch(self.identity, self._on_process_terminal)
            with self._lock:
                if self._terminal_reason is None and watch.identity_verified:
                    self._watch = watch
                    capture.start()
                    if self._terminal_reason is not None:
                        self._release_native_resources()
                else:
                    with contextlib.suppress(Exception):
                        watch.close()
                    if self._terminal_reason is None:
                        self._terminal_reason = "target_identity_mismatch"
                        self._signal_terminal()
                        raise ProcessAudioCaptureSetupError(
                            "resolved process identity could not be verified"
                        )
                    self._release_native_resources()
        except Exception as exc:
            self._release_native_resources()
            self._queue.close()
            if isinstance(exc, ProcessAudioCaptureUnavailableError):
                raise
            raise ProcessAudioCaptureSetupError("process audio capture setup failed") from exc

    @property
    def terminal_reason(self) -> str | None:
        return self._terminal_reason

    @property
    def queue_drop_count(self) -> int:
        return self._queue_drop_count

    @property
    def queue_depth(self) -> int:
        """Packets waiting for the consumer — a backlog gauge for the Pace log."""
        with contextlib.suppress(Exception):
            return int(self._queue.sync_q.qsize())
        return 0

    async def frames(self) -> AsyncIterator[AudioFrameF32]:
        while True:
            samples = await self._queue.async_q.get()
            if samples is None:
                await self.close()
                return
            yield AudioFrameF32(
                samples=samples,
                sample_rate_hz=PROCESS_CAPTURE_SAMPLE_RATE_HZ,
                channels=PROCESS_CAPTURE_CHANNELS,
            )

    async def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            if self._terminal_reason is None:
                self._terminal_reason = "closed"
            self._release_native_resources()
            self._signal_terminal()
        self._queue.close()
        with contextlib.suppress(Exception):
            await self._queue.wait_closed()

    def _on_data(self, data: bytes, frames: int) -> None:
        if self._closed or self._terminal_reason is not None:
            return
        samples = _decode_process_capture_frame(data, frames)
        if samples is None:
            self._signal_terminal_failure("source_failure")
            return
        try:
            self._queue.sync_q.put_nowait(samples)
        except queue.Full:
            # r630: shed the OLDEST packet, not the newest. A consumer that is
            # chronically slower than real time then stays at most one buffer
            # behind and always hears the most recent audio, instead of
            # serving a stale window while everything new is discarded.
            self._queue_drop_count += 1
            try:
                shed = self._queue.sync_q.get_nowait()
            except queue.Empty:
                return          # nothing to shed: this packet is the drop
            except Exception:
                return          # queue closed between Full and shed: drop quietly
            if shed is None:
                # r631: we popped the terminal sentinel that _signal_terminal
                # enqueued a moment ago. Put it back and drop THIS packet, or the
                # consumer never learns the target exited (silent hang, green
                # pill over a dead capture).
                with contextlib.suppress(Exception):
                    self._queue.sync_q.put_nowait(None)
                return
            with contextlib.suppress(Exception):
                self._queue.sync_q.put_nowait(samples)
        except Exception:
            self._signal_terminal_failure("source_failure")

    def _on_process_terminal(self) -> None:
        self._signal_terminal_failure("target_exited")

    def _signal_terminal_failure(self, reason: str) -> None:
        with self._lock:
            if self._terminal_reason is not None:
                return
            self._terminal_reason = reason
            self._signal_terminal()

    def _release_native_resources(self) -> None:
        watch, self._watch = self._watch, None
        capture, self._capture = self._capture, None
        if watch is not None:
            with contextlib.suppress(Exception):
                watch.close()
        if capture is not None:
            with contextlib.suppress(Exception):
                capture.close()

    def _signal_terminal(self) -> None:
        # r630: the sentinel used to queue BEHIND up to a full buffer of stale
        # audio, delaying FAULTED and the relatch by however long that took to
        # drain; nothing captured after a terminal event is worth decoding.
        with contextlib.suppress(Exception):
            while True:
                try:
                    self._queue.sync_q.get_nowait()
                except queue.Empty:
                    break
        try:
            self._queue.sync_q.put_nowait(None)
            return
        except queue.Full:
            with contextlib.suppress(queue.Empty):
                self._queue.sync_q.get_nowait()
            with contextlib.suppress(Exception):
                self._queue.sync_q.put_nowait(None)
        except Exception:
            return


def _decode_process_capture_frame(data: bytes, frames: int) -> np.ndarray | None:
    if isinstance(frames, bool) or not isinstance(frames, int) or frames == 0 or frames < -1:
        return None
    if not isinstance(data, bytes) or len(data) % (PROCESS_CAPTURE_CHANNELS * 4) != 0:
        return None
    derived_frames = len(data) // (PROCESS_CAPTURE_CHANNELS * 4)
    if derived_frames <= 0 or (frames != -1 and frames != derived_frames):
        return None
    return np.frombuffer(data, dtype="<f4").reshape((derived_frames, PROCESS_CAPTURE_CHANNELS))
