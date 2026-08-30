"""Process-wide shutdown state + a force-exit watchdog (r626).

Closing the app while a peer utterance was mid-decode could freeze the whole
process until force-kill (observed live 2026-08-28: "UI Event Bridge
cancelled" logged at window close, then nothing — the process sat wedged).
The decode itself already runs on an abandoned daemon thread, but shutdown
still had two ways to hang behind it:

  * teardown paths that finalize the in-flight utterance (a full native
    decode awaited inline, up to the 30s decode timeout — or forever once
    the loop's timers stop firing), and
  * interpreter finalization, which destroys the sherpa/onnxruntime native
    objects while the abandoned decode thread is still executing inside
    them; the native destructor then blocks WITH THE GIL HELD, freezing
    every remaining Python thread at once.

This module is the coordination point: the UI marks shutdown the moment the
window disconnects, latency-sensitive native work (local STT decode/build)
checks the flag and stands down, and a watchdog guarantees the process
actually dies even if some native call never returns. The watchdog is a
plain daemon thread — it cannot fire through a GIL held by a wedged native
destructor, which is exactly why the frozen app also skips interpreter
finalization entirely (see main._exit_without_finalization).
"""
from __future__ import annotations

import logging
import os
import threading
import time

logger = logging.getLogger(__name__)

_shutting_down = threading.Event()
_watchdog_armed = threading.Lock()


def _hard_exit_allowed() -> bool:
    """Same contract as main._exit_without_finalization: hard exits are for
    frozen builds only, and PURIPULY_NO_HARD_EXIT=1 opts out for exit-path
    diagnosis (pdb in a teardown finally, profiling slow finalization)."""
    import sys

    return bool(getattr(sys, "frozen", False)) and not os.environ.get(
        "PURIPULY_NO_HARD_EXIT")


def _quit_steam_helper() -> None:
    """The watchdog exit never reaches main's post-ft.app _stop_steam_helper
    hook — send the helper daemon its quit here too, or a forced exit
    orphans the daemon + its hidden browser (the exact background-process
    leak the hook exists to prevent). Mirrors main._stop_steam_helper."""
    import socket

    try:
        s = socket.create_connection(("127.0.0.1", 8791), timeout=0.5)
        try:
            s.sendall(b'{"cmd": "quit"}\n')
        finally:
            s.close()
    except Exception:
        pass


def _drain_file_log_queue(deadline_s: float = 2.0) -> None:
    """Best-effort, BOUNDED wait for the file-log QueueListener to write
    pending records (QueueHandler.flush is a no-op — without this the final
    '[Shutdown] force-exiting' line is usually lost to os._exit). Bounded
    polling, never queue.join(): the watchdog must stay unblockable even if
    the listener thread is wedged."""
    import queue as _queue
    from logging.handlers import QueueHandler

    try:
        queues = [
            h.queue for h in logging.getLogger().handlers
            if isinstance(h, QueueHandler)
            and isinstance(getattr(h, "queue", None), _queue.Queue)
        ]
        end = time.monotonic() + deadline_s
        while time.monotonic() < end and any(
                q.unfinished_tasks for q in queues):
            time.sleep(0.05)
    except Exception:
        pass


def begin_shutdown(reason: str = "") -> None:
    """Mark the process as shutting down. Idempotent."""
    if _shutting_down.is_set():
        return
    _shutting_down.set()
    logger.info("[Shutdown] begin%s", f": {reason}" if reason else "")


def is_shutting_down() -> bool:
    return _shutting_down.is_set()


def arm_exit_watchdog(timeout_s: float = 15.0, *, exit_code: int = 1) -> None:
    """Force the process down if it is still alive timeout_s after arming.

    Armed once, at the moment the UI window disconnects — from then on the
    only legitimate outcome is process exit, so a process still breathing
    after the timeout is wedged (native teardown, executor join, a decode
    that never returned) and os._exit is strictly better than a zombie the
    user has to hunt down in Task Manager.
    """
    if not _watchdog_armed.acquire(blocking=False):
        return  # already armed

    def _watch() -> None:
        time.sleep(timeout_s)
        if not _hard_exit_allowed():
            logger.error(
                "[Shutdown] still alive %.0fs after window close — hard exit "
                "disabled (source run or PURIPULY_NO_HARD_EXIT), leaving the "
                "process to finish on its own",
                timeout_s,
            )
            return
        logger.error(
            "[Shutdown] still alive %.0fs after window close — force-exiting "
            "(a native call is likely wedged; abandoning it)",
            timeout_s,
        )
        _quit_steam_helper()
        _drain_file_log_queue()
        for handler in logging.getLogger().handlers:
            try:
                handler.flush()
            except Exception:
                pass
        os._exit(exit_code)

    threading.Thread(target=_watch, name="shutdown-watchdog", daemon=True).start()


def _reset_for_tests() -> None:
    """Test hook: clear the flag (the watchdog lock is deliberately left
    alone — a second arm in-process must stay a no-op)."""
    _shutting_down.clear()
