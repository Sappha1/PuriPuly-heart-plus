"""Startup system snapshot for support logs.

Users share their puripuly_heart.log when something breaks, but the log never
recorded what machine it came from — every hardware/OS question (weak CPU
causing loopback queue drops, low RAM, Smart App Control blocking child exes,
China timezone) needed a back-and-forth with the user. Collect it once at
startup, in a background thread because the GPU query shells out to CIM and
can take ~1s.

Deliberately hardware/OS only: no usernames, hostnames, or network identity —
these lines are designed to be safe in logs users paste publicly.
"""

from __future__ import annotations

import ctypes
import logging
import os
import platform
import subprocess
import sys
import threading
import winreg

logger = logging.getLogger(__name__)

_CREATE_NO_WINDOW = 0x08000000


def _cpu_name() -> str:
    try:
        with winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE,
            r"HARDWARE\DESCRIPTION\System\CentralProcessor\0",
        ) as key:
            return str(winreg.QueryValueEx(key, "ProcessorNameString")[0]).strip()
    except OSError:
        return platform.processor() or "unknown"


class _MemoryStatusEx(ctypes.Structure):
    _fields_ = [
        ("dwLength", ctypes.c_uint32),
        ("dwMemoryLoad", ctypes.c_uint32),
        ("ullTotalPhys", ctypes.c_uint64),
        ("ullAvailPhys", ctypes.c_uint64),
        ("ullTotalPageFile", ctypes.c_uint64),
        ("ullAvailPageFile", ctypes.c_uint64),
        ("ullTotalVirtual", ctypes.c_uint64),
        ("ullAvailVirtual", ctypes.c_uint64),
        ("ullAvailExtendedVirtual", ctypes.c_uint64),
    ]


def _memory_gb() -> tuple[float, float]:
    status = _MemoryStatusEx()
    status.dwLength = ctypes.sizeof(_MemoryStatusEx)
    if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
        return (0.0, 0.0)
    gib = 1024.0**3
    return (status.ullTotalPhys / gib, status.ullAvailPhys / gib)


def _gpus() -> str:
    # AdapterRAM is a 32-bit WMI field that caps/wraps at 4 GB on most drivers,
    # so report the GPU names (which answer the support question) and treat the
    # VRAM number as a lower bound.
    try:
        completed = subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-NonInteractive",
                "-Command",
                "(Get-CimInstance Win32_VideoController | ForEach-Object {"
                " '{0} ({1:N1}GB+)' -f $_.Name, ($_.AdapterRAM / 1GB) }) -join '; '",
            ],
            capture_output=True,
            text=True,
            timeout=15,
            creationflags=_CREATE_NO_WINDOW,
        )
        gpus = (completed.stdout or "").strip()
        return gpus or "unknown"
    except Exception:
        return "unknown"


def _smart_app_control_state() -> str:
    # 0=Off, 1=Enforce, 2=Evaluation. Enforce mode blocks our unsigned child
    # exes (OCR overlay) and installers with WinError 5 and CANNOT be
    # whitelisted per-app — knowing this up front short-circuits an entire
    # class of "Error 5: access denied" support threads.
    try:
        with winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE,
            r"SYSTEM\CurrentControlSet\Control\CI\Policy",
        ) as key:
            value = int(winreg.QueryValueEx(key, "VerifiedAndReputablePolicyState")[0])
        return {0: "off", 1: "enforce", 2: "evaluation"}.get(value, f"unknown({value})")
    except OSError:
        return "unavailable"


def _windows_build() -> str:
    try:
        with winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE,
            r"SOFTWARE\Microsoft\Windows NT\CurrentVersion",
        ) as key:
            product = str(winreg.QueryValueEx(key, "ProductName")[0])
            display = str(winreg.QueryValueEx(key, "DisplayVersion")[0])
            build = str(winreg.QueryValueEx(key, "CurrentBuildNumber")[0])
            try:
                ubr = int(winreg.QueryValueEx(key, "UBR")[0])
                build = f"{build}.{ubr}"
            except OSError:
                pass
        # ProductName still says "Windows 10" on Windows 11 (Microsoft never
        # updated the registry value); build >= 22000 is the real marker.
        try:
            if int(build.split(".")[0]) >= 22000:
                product = product.replace("Windows 10", "Windows 11")
        except ValueError:
            pass
        return f"{product} {display} (build {build})"
    except OSError:
        return platform.platform()


def _timezone_name() -> str:
    try:
        completed = subprocess.run(
            ["tzutil", "/g"],
            capture_output=True,
            text=True,
            timeout=5,
            creationflags=_CREATE_NO_WINDOW,
        )
        return (completed.stdout or "").strip() or "unknown"
    except Exception:
        return "unknown"


def log_system_info_async() -> None:
    """Log one [SysInfo] block from a daemon thread; never blocks startup."""

    def _collect() -> None:
        try:
            total_gb, avail_gb = _memory_gb()
            logger.info(
                "[SysInfo] os=%s | cpu=%s cores=%s | ram_total=%.1fGB ram_available=%.1fGB"
                " | gpu=%s | smart_app_control=%s | timezone=%s | frozen=%s exe=%s",
                _windows_build(),
                _cpu_name(),
                os.cpu_count(),
                total_gb,
                avail_gb,
                _gpus(),
                _smart_app_control_state(),
                _timezone_name(),
                getattr(sys, "frozen", False),
                sys.executable,
            )
        except Exception as exc:
            logger.warning("[SysInfo] collection failed: %s", exc)

    threading.Thread(target=_collect, name="sysinfo", daemon=True).start()
