"""Lightweight detection of whether SteamVR is currently running.

Used to auto-resolve the overlay target: if the stored preference is the SteamVR
overlay but SteamVR isn't running, the overlay falls back to the desktop overlay
instead of failing with ``steamvr_not_running``. Detection is a cheap process-name
scan (no new dependency, no OpenVR init) so it can be polled.
"""

from __future__ import annotations

import logging
import sys

logger = logging.getLogger(__name__)

# SteamVR's core processes. vrmonitor/vrserver run whenever SteamVR is up.
_STEAMVR_PROCESS_NAMES = frozenset(
    {"vrmonitor.exe", "vrserver.exe", "vrcompositor.exe"}
)


def _running_process_names_windows() -> set[str]:
    """Return lowercased names of running processes via the Toolhelp snapshot API."""

    import ctypes
    from ctypes import wintypes

    TH32CS_SNAPPROCESS = 0x00000002
    INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value

    class PROCESSENTRY32(ctypes.Structure):
        _fields_ = [
            ("dwSize", wintypes.DWORD),
            ("cntUsage", wintypes.DWORD),
            ("th32ProcessID", wintypes.DWORD),
            ("th32DefaultHeapID", ctypes.POINTER(ctypes.c_ulong)),
            ("th32ModuleID", wintypes.DWORD),
            ("cntThreads", wintypes.DWORD),
            ("th32ParentProcessID", wintypes.DWORD),
            ("pcPriClassBase", ctypes.c_long),
            ("dwFlags", wintypes.DWORD),
            ("szExeFile", ctypes.c_char * 260),
        ]

    kernel32 = ctypes.windll.kernel32
    snapshot = kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if snapshot == INVALID_HANDLE_VALUE:
        return set()

    names: set[str] = set()
    try:
        entry = PROCESSENTRY32()
        entry.dwSize = ctypes.sizeof(PROCESSENTRY32)
        if not kernel32.Process32First(snapshot, ctypes.byref(entry)):
            return names
        while True:
            try:
                names.add(entry.szExeFile.decode("utf-8", "ignore").lower())
            except Exception:
                pass
            if not kernel32.Process32Next(snapshot, ctypes.byref(entry)):
                break
    finally:
        kernel32.CloseHandle(snapshot)
    return names


def is_steamvr_running() -> bool:
    """Best-effort check for a live SteamVR runtime. Defaults to False on error.

    Returning False on any failure means the overlay falls back to the desktop
    target rather than attempting a SteamVR launch that would fail anyway.
    """

    if not sys.platform.startswith("win"):
        return False
    try:
        running = _running_process_names_windows()
    except Exception:
        logger.debug("SteamVR detection failed", exc_info=True)
        return False
    return any(name in running for name in _STEAMVR_PROCESS_NAMES)
