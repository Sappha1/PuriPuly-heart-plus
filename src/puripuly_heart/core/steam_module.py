r"""Optional Steam Chat module: the helper (daemon + Playwright venv + browser
profile) that powers the Steam tab. Mirrors ocr_module.py's shape.

Resolution order (steam_bridge.py resolves the same way):
  1. dev helper (source machine)   <project>\steam-helper
  2. installed module              %LOCALAPPDATA%\puripuly-heart\modules\steam\steam-helper

Install bootstraps a fresh venv with the SYSTEM Python (the frozen app's
runtime cannot create venvs), pip-installs Playwright, and writes the bridge
sources bundled under puripuly_heart/data/steam_bridge_src/. The browser is
Edge (preinstalled on Windows 10/11) with a Chrome fallback — no browser
download needed. Sign-in happens on first use of the Steam tab.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Callable

_CREATE_NO_WINDOW = 0x08000000 if sys.platform.startswith("win") else 0

def _project_dev_root() -> Path:
    """The source checkout's steam-helper (dev machines only). Frozen dev
    builds run from <project>\\dist-dev\\PuriPulyHeart; source runs resolve
    relative to this file. End-user installs simply won't have it."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent.parent.parent / "steam-helper"
    return Path(__file__).resolve().parents[3] / "steam-helper"


_DEV_ROOT = _project_dev_root()


def modules_steam_root() -> Path:
    from puripuly_heart.config.paths import user_config_dir

    return user_config_dir() / "modules" / "steam"


def installed_helper_root() -> Path:
    return modules_steam_root() / "steam-helper"


def helper_root() -> Path:
    """The active helper location: dev copy wins when present."""
    if _DEV_ROOT.exists():
        return _DEV_ROOT
    return installed_helper_root()


def _venv_python(root: Path) -> Path:
    return root / "steamprobe-venv" / "Scripts" / "pythonw.exe"


def module_ready() -> bool:
    root = helper_root()
    return ((root / "steam_bridge" / "daemon.py").exists()
            and _venv_python(root).exists())


def size_bytes() -> int:
    total = 0
    root = helper_root()
    try:
        for f in root.rglob("*"):
            if f.is_file():
                total += f.stat().st_size
    except Exception:
        pass
    return total


def stop_helper_processes() -> None:
    """Kill the daemon + its browser, matched by command line (never broad)."""
    ps = (
        "Get-CimInstance Win32_Process | Where-Object { "
        "$_.Name -in @('pythonw.exe','python.exe','msedge.exe','chrome.exe') -and ( "
        "$_.CommandLine -like '*steam_bridge*daemon.py*' -or "
        "$_.CommandLine -like '*steamprobe-profile*' ) } | "
        "ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }"
    )
    try:
        subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                       capture_output=True, timeout=20,
                       creationflags=_CREATE_NO_WINDOW)
    except Exception:
        pass


def uninstall() -> int:
    """Stop the helper and delete it (dev copy or installed module, whichever
    is active). Returns bytes freed. Also removes the saved Steam sign-in
    (it lives in the helper's browser profile)."""
    stop_helper_processes()
    root = helper_root()
    freed = size_bytes()
    shutil.rmtree(root, ignore_errors=True)
    return freed


def find_system_python() -> list[str] | None:
    """An argv prefix for a real system Python (the frozen runtime can't make
    venvs). Prefers the py launcher."""
    py = shutil.which("py")
    if py:
        try:
            r = subprocess.run([py, "-3", "--version"], capture_output=True,
                               timeout=10, creationflags=_CREATE_NO_WINDOW)
            if r.returncode == 0:
                return [py, "-3"]
        except Exception:
            pass
    for name in ("python", "python3"):
        exe = shutil.which(name)
        if exe and "puripulyheart" not in exe.lower():
            try:
                r = subprocess.run([exe, "--version"], capture_output=True,
                                   timeout=10, creationflags=_CREATE_NO_WINDOW)
                if r.returncode == 0:
                    return [exe]
            except Exception:
                continue
    return None


def _bundled_bridge_src() -> Path:
    return Path(__file__).resolve().parent.parent / "data" / "steam_bridge_src"


def install(progress: Callable[[str], None] | None = None) -> Path:
    """Synchronous install (run via asyncio.to_thread). Stages reported via
    progress('env'|'deps'|'files'|'done'). Raises RuntimeError with a
    user-showable reason on failure."""
    def _p(stage: str) -> None:
        if callable(progress):
            try:
                progress(stage)
            except Exception:
                pass

    py = find_system_python()
    if py is None:
        raise RuntimeError("no-python")
    src = _bundled_bridge_src()
    if not (src / "daemon.py").exists():
        raise RuntimeError("no-sources")

    root = installed_helper_root()
    root.mkdir(parents=True, exist_ok=True)
    venv_dir = root / "steamprobe-venv"

    _p("env")
    r = subprocess.run(py + ["-m", "venv", str(venv_dir)],
                       capture_output=True, timeout=180,
                       creationflags=_CREATE_NO_WINDOW)
    if r.returncode != 0:
        raise RuntimeError("venv: " + (r.stderr or b"").decode(errors="replace")[:200])

    _p("deps")
    vpy = venv_dir / "Scripts" / "python.exe"
    r = subprocess.run([str(vpy), "-m", "pip", "install", "--quiet", "playwright"],
                       capture_output=True, timeout=900,
                       creationflags=_CREATE_NO_WINDOW)
    if r.returncode != 0:
        raise RuntimeError("pip: " + (r.stderr or b"").decode(errors="replace")[:200])

    _p("files")
    bridge = root / "steam_bridge"
    bridge.mkdir(parents=True, exist_ok=True)
    for f in src.glob("*.py"):
        shutil.copy2(f, bridge / f.name)
    (root / "steamprobe-profile").mkdir(exist_ok=True)

    _p("done")
    return root
