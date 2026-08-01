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


def _mark_of_the_web_zone(path: str) -> int | None:
    """ZoneId from the file's Zone.Identifier ADS (3 = downloaded from the
    internet), or None when absent/unreadable."""
    try:
        with open(f"{path}:Zone.Identifier", encoding="utf-8", errors="ignore") as fh:
            for line in fh:
                if line.strip().lower().startswith("zoneid="):
                    return int(line.strip().split("=", 1)[1])
    except (OSError, ValueError):
        pass
    return None


def diagnose_blocked_executable(exe_path: str) -> str:
    """Explain a WinError 5/4551 launch refusal of exe_path in support-log terms.

    Windows' CreateProcess failure message is a bare "Access is denied" with no
    file name and no cause, so callers that catch it must name the exe they
    passed in and this figures out the likely blocker: quarantined-and-deleted,
    Smart App Control, Mark-of-the-Web, or a generic AV/permissions block.
    """
    if not os.path.exists(exe_path):
        return (
            f"{exe_path} no longer exists — an antivirus likely quarantined or "
            "deleted it; restore it from the AV's protection history and add an "
            "exclusion for the app folder"
        )
    notes = []
    if _smart_app_control_state() == "enforce":
        notes.append(
            "Smart App Control is ON — it blocks unsigned apps with 'access "
            "denied', ignores antivirus exclusions, and has no per-app "
            "whitelist; it can only be switched off in Windows Security > "
            "App & browser control > Smart App Control settings"
        )
    zone = _mark_of_the_web_zone(exe_path)
    if zone is not None and zone >= 3:
        notes.append(
            "the file is marked as downloaded from the internet "
            "(Mark-of-the-Web) — right-click the file > Properties > Unblock, "
            "or unblock the downloaded zip before extracting it"
        )
    if not notes:
        notes.append(
            "the file exists but Windows refused to execute it — most likely an "
            "antivirus block (check the antivirus's protection history) or "
            "missing folder permissions"
        )
    return f"{exe_path}: " + "; ".join(notes)


# r353: families carrying VNNI in some form. Intel picked it up with Ice Lake
# (AVX-512 form) and Alder Lake (256-bit AVX-VNNI); AMD with Zen 4. Everything
# older runs the int8 recogniser on an emulated path that can saturate.
_VNNI_INTEL_HINTS = (
    "i3-1", "i5-1", "i7-1", "i9-1",       # 10th gen and later mobile/desktop
    "ultra",                                # Core Ultra
    "xeon",                                 # server parts, mostly Ice Lake+
)
_NO_VNNI_AMD_HINTS = (
    "ryzen 3 1", "ryzen 5 1", "ryzen 7 1", "ryzen 9 1",   # Zen 1
    "ryzen 3 2", "ryzen 5 2", "ryzen 7 2", "ryzen 9 2",   # Zen+
    "ryzen 3 3", "ryzen 5 3", "ryzen 7 3", "ryzen 9 3",   # Zen 2
    "ryzen 3 4", "ryzen 5 4", "ryzen 7 4", "ryzen 9 4",   # Zen 2 APU
    "ryzen 3 5", "ryzen 5 5", "ryzen 7 5", "ryzen 9 5",   # Zen 3
)


def cpu_int8_support() -> str:
    """Can this CPU run the quantized ASR model exactly? (r353)

    Returns "vnni" (exact), "emulated" (int8 matmuls run on a path that can
    saturate, and the recogniser may emit fluent nonsense), or "unknown".
    """
    try:
        import numpy as np

        features = getattr(np._core._multiarray_umath, "__cpu_features__", {})
        if features.get("AVX512VNNI"):
            return "vnni"
    except Exception:
        pass
    name = ""
    try:
        name = _cpu_name().lower()
    except Exception:
        return "unknown"
    if not name:
        return "unknown"
    if any(hint in name for hint in _NO_VNNI_AMD_HINTS):
        return "emulated"
    if "ryzen" in name:
        # Zen 4 and later carry AVX-VNNI; numpy cannot see it, so anything not
        # matched above is left unclaimed rather than guessed.
        return "unknown"
    if any(hint in name for hint in _VNNI_INTEL_HINTS):
        return "vnni"
    return "unknown"


def log_system_info_async() -> None:
    """Log one [SysInfo] block from a daemon thread; never blocks startup."""

    def _collect() -> None:
        try:
            total_gb, avail_gb = _memory_gb()
            logger.info(
                "[SysInfo] os=%s | cpu=%s cores=%s int8=%s | ram_total=%.1fGB"
                " ram_available=%.1fGB | gpu=%s | smart_app_control=%s"
                " | timezone=%s | frozen=%s exe=%s",
                _windows_build(),
                _cpu_name(),
                os.cpu_count(),
                cpu_int8_support(),
                total_gb,
                avail_gb,
                _gpus(),
                _smart_app_control_state(),
                _timezone_name(),
                getattr(sys, "frozen", False),
                sys.executable,
            )
            if cpu_int8_support() == "emulated":
                logger.warning(
                    "[SysInfo] this CPU has no VNNI, so the quantized speech "
                    "model's int8 matmuls run on an emulated path that can "
                    "saturate. If transcription returns confident nonsense on "
                    "every utterance regardless of language, suspect this "
                    "first -- the model loads and runs normally either way."
                )
        except Exception as exc:
            logger.warning("[SysInfo] collection failed: %s", exc)

    threading.Thread(target=_collect, name="sysinfo", daemon=True).start()
