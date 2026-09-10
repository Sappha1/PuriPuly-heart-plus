"""Single-instance guard (r647).

A named Windows mutex keeps auto-launch (SteamVR / VRChat launch options) from
spawning a duplicate app that would fight over the OSC port and settings file.

This lives in its OWN module - like ``boot_stealth`` - because the frozen entry
runs ``main`` as ``__main__``, so a handle stored there is invisible to
``import puripuly_heart.main`` (a second module instance). Both the entry and
the shutdown path import THIS module, so they share one handle: the entry
acquires it, and ``begin_shutdown`` releases it the instant the window closes.

Releasing at shutdown-BEGIN (not at process exit) is what makes both launch
cases feel right with NO wait:
  * genuine duplicate (app really running) -> mutex held -> reject instantly,
    so a re-launch does not sit on the boot splash;
  * close-then-relaunch -> the closing instance already released the mutex, so
    the new one acquires immediately instead of being rejected and flashing
    the splash.
"""
from __future__ import annotations

import sys

# Distinct name so this OCR-prototype build can run alongside a release install
# (which holds "...SingleInstance").
_MUTEX_NAME = "PuriPulyHeartPlus.SingleInstance.OCRProto"
_ERROR_ALREADY_EXISTS = 183

_handle = 0
_released = False


def acquire() -> bool:
    """True if this is the only instance (holds the mutex). False if another
    instance already owns it - reject immediately, no waiting."""
    global _handle
    if sys.platform != "win32":
        return True
    try:
        import ctypes

        handle = ctypes.windll.kernel32.CreateMutexW(None, False, _MUTEX_NAME)
        if not handle:
            return True  # can't tell — don't block startup
        _handle = handle
        return ctypes.windll.kernel32.GetLastError() != _ERROR_ALREADY_EXISTS
    except Exception:
        return True


def release() -> None:
    """Release the mutex NOW (called from begin_shutdown) so a quick relaunch
    can acquire it without waiting for this process to fully exit. Idempotent;
    a no-op when we never owned it. Windows also frees it on process exit, so a
    hard kill is covered too."""
    global _handle, _released
    if _released or not _handle:
        return
    _released = True
    try:
        import ctypes

        ctypes.windll.kernel32.CloseHandle(_handle)
    except Exception:
        pass
    _handle = 0
