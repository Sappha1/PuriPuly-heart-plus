"""Keep the flet boot window invisible until the app is ready.

The bundled flet desktop client ignores FLET_APP_HIDDEN (verified live: its
window self-shows ~1.4s after spawn), and merely SW_HIDE-ing the window loses
a race every time the client re-shows itself during boot (visible blips). So
the watchdog parks the window OFF-SCREEN — position survives show/hide
cycles, so even a re-shown window appears at -32000 and can never flash —
and finish() moves it to the center of the work area and shows it.

This lives in its own module because the frozen entry runs main.py as
__main__: state stored there is invisible to `import puripuly_heart.main`
(a second module instance), which is exactly how r455 ended up with an
armed watchdog nobody could disarm. Import this module from anywhere and
you get the same instance.

Scope: only windows of class FLUTTER_RUNNER_WIN32_WINDOW owned by flet.exe
processes whose PARENT is this process — other running copies of the app
are never touched. If finish() is never called (startup crash), the
watchdog restores the window itself at its deadline: visible late is
acceptable, invisible forever is not.
"""
from __future__ import annotations

import contextlib
import ctypes
import ctypes.wintypes as wt
import os
import threading

_OFFSCREEN = -32000
_SWP_FLAGS = 0x1 | 0x4 | 0x10          # NOSIZE | NOZORDER | NOACTIVATE
_stop: threading.Event | None = None
_finished = False
_parked: set[int] = set()


def _child_flet_pids() -> set[int]:
    pids: set[int] = set()
    k32 = ctypes.windll.kernel32

    class PROCESSENTRY32W(ctypes.Structure):
        _fields_ = [("dwSize", wt.DWORD), ("cntUsage", wt.DWORD),
                    ("th32ProcessID", wt.DWORD),
                    ("th32DefaultHeapID", ctypes.POINTER(ctypes.c_ulong)),
                    ("th32ModuleID", wt.DWORD), ("cntThreads", wt.DWORD),
                    ("th32ParentProcessID", wt.DWORD),
                    ("pcPriClassBase", ctypes.c_long), ("dwFlags", wt.DWORD),
                    ("szExeFile", ctypes.c_wchar * 260)]

    snap = k32.CreateToolhelp32Snapshot(0x2, 0)
    if snap in (0, -1):
        return pids
    try:
        entry = PROCESSENTRY32W()
        entry.dwSize = ctypes.sizeof(PROCESSENTRY32W)
        me = os.getpid()
        ok = k32.Process32FirstW(snap, ctypes.byref(entry))
        while ok:
            if (entry.th32ParentProcessID == me
                    and entry.szExeFile.lower() == "flet.exe"):
                pids.add(int(entry.th32ProcessID))
            ok = k32.Process32NextW(snap, ctypes.byref(entry))
    finally:
        k32.CloseHandle(snap)
    return pids


def _each_flutter_window(fn) -> None:
    """fn(u32, hwnd) for each top-level FLUTTER_RUNNER_WIN32_WINDOW of our
    child flet.exe processes."""
    targets = _child_flet_pids()
    if not targets:
        return
    u32 = ctypes.windll.user32
    proto = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
    buf = ctypes.create_unicode_buffer(64)

    def _cb(hwnd, _l):
        pid = wt.DWORD()
        u32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value in targets:
            u32.GetClassNameW(hwnd, buf, 64)
            if buf.value == "FLUTTER_RUNNER_WIN32_WINDOW":
                fn(u32, hwnd)
        return True

    u32.EnumWindows(proto(_cb), 0)


def _park(u32, hwnd) -> None:
    _parked.add(int(hwnd))
    u32.SetWindowPos(hwnd, 0, _OFFSCREEN, _OFFSCREEN, 0, 0, _SWP_FLAGS)
    if u32.IsWindowVisible(hwnd):
        u32.ShowWindow(hwnd, 0)        # SW_HIDE (no taskbar button either)


def _restore(u32, hwnd) -> None:
    if int(hwnd) not in _parked:
        return
    rect = wt.RECT()
    u32.GetWindowRect(hwnd, ctypes.byref(rect))
    w, h = rect.right - rect.left, rect.bottom - rect.top
    area = wt.RECT()
    u32.SystemParametersInfoW(0x30, 0, ctypes.byref(area), 0)  # SPI_GETWORKAREA
    x = area.left + max(0, (area.right - area.left - w) // 2)
    y = area.top + max(0, (area.bottom - area.top - h) // 2)
    u32.SetWindowPos(hwnd, 0, x, y, 0, 0, _SWP_FLAGS)
    u32.ShowWindow(hwnd, 5)            # SW_SHOW


def start() -> None:
    """Arm the watchdog. Idempotent; call before ft.app()."""
    global _stop
    if _stop is not None or _finished:
        return
    _stop = threading.Event()
    ev = _stop

    def _watch() -> None:
        import time as _t
        deadline = _t.monotonic() + 30
        while not ev.is_set():
            if _t.monotonic() >= deadline:
                # finish() never came (startup crashed?) — never leave the
                # window invisible
                with contextlib.suppress(Exception):
                    _each_flutter_window(_restore)
                return
            with contextlib.suppress(Exception):
                _each_flutter_window(_park)
            _t.sleep(0.01)

    threading.Thread(target=_watch, daemon=True, name="boot-stealth").start()


def finish() -> None:
    """Disarm the watchdog and show the window centered on the work area.
    Idempotent — later calls (e.g. the safety backstop) are no-ops."""
    global _stop, _finished
    if _finished:
        return
    _finished = True
    ev, _stop = _stop, None
    if ev is not None:
        ev.set()
        import time
        time.sleep(0.12)               # watchdog exit + pending size commands
    with contextlib.suppress(Exception):
        _each_flutter_window(_restore)
