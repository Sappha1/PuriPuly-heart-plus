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
    venv = root / "steamprobe-venv"
    for cand in (venv / "Scripts" / "pythonw.exe", venv / "pythonw.exe",
                 venv / "Scripts" / "python.exe", venv / "python.exe"):
        if cand.exists():
            return cand
    return venv / "Scripts" / "pythonw.exe"


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


def _wait_port_closed(port: int = 8791, timeout_s: float = 5.0) -> bool:
    """r636: True once nothing accepts connections on 127.0.0.1:port (polled,
    bounded) — a killed daemon takes a moment to release its files, and
    uninstall's rmtree used to race that."""
    import socket
    import time

    deadline = time.monotonic() + timeout_s
    while True:
        try:
            s = socket.create_connection(("127.0.0.1", int(port)), timeout=0.3)
        except OSError:
            return True
        try:
            s.close()
        except Exception:
            pass
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.25)


# r636: the command-line patterns stop_helper_processes kills on, as ordered
# parts (mirrors '*steam_bridge*daemon.py*' / '*steamprobe-profile*'); the
# drain below polls the same set
_HELPER_NEEDLES = (("steamprobe-profile",), ("steam_bridge", "daemon.py"))


def _cmdline_matches(cl: str, needles=_HELPER_NEEDLES) -> bool:
    for parts in needles:
        pos = 0
        for part in parts:
            pos = cl.find(part, pos)
            if pos < 0:
                break
            pos += len(part)
        else:
            return True
    return False


def _helper_processes_alive(needles=_HELPER_NEEDLES) -> bool:
    """r636: True while any python*/msedge/chrome process (this one excluded)
    still carries one of `needles` on its command line. Edge's renderer /
    GPU / network children inherit --user-data-dir, so the whole browser
    tree counts — the port drain alone only proved the DAEMON was gone while
    Edge could still hold Default/* open. psutil missing = 'gone' (never
    block an uninstall on the check itself)."""
    try:
        import psutil
    except Exception:
        return False
    me = os.getpid()
    for p in psutil.process_iter(["pid", "name", "cmdline"]):
        try:
            if p.info["pid"] == me:
                continue
            name = (p.info["name"] or "").lower()
            if not (name.startswith("python") or name in ("msedge.exe", "chrome.exe")):
                continue
            if _cmdline_matches(" ".join(p.info["cmdline"] or []).lower(), needles):
                return True
        except Exception:
            continue
    return False


def _wait_helper_gone(timeout_s: float = 5.0, needles=_HELPER_NEEDLES) -> bool:
    """r636: poll (bounded) until no helper process is left; False on timeout."""
    import time

    deadline = time.monotonic() + timeout_s
    while _helper_processes_alive(needles):
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.25)
    return True


def stop_helper_processes() -> None:
    """Kill the daemon + its browser, matched by command line (never broad)."""
    # r636: the venv's pythonw.exe is only a launcher when the system Python
    # is the Store build — the process that really runs daemon.py is named
    # pythonw3.11.exe, which the exact-name list missed (uninstall then
    # raced a live daemon). Any python*.exe / Edge / Chrome whose command
    # line names the helper; never this PowerShell itself (its own command
    # line carries the patterns).
    ps = (
        "Get-CimInstance Win32_Process | Where-Object { "
        "$_.ProcessId -ne $PID -and "
        "($_.Name -like 'python*' -or $_.Name -in @('msedge.exe','chrome.exe')) -and ( "
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
    _wait_port_closed()   # r636: let the dying daemon release its files first
    _wait_helper_gone()   # r636: ...and the Edge tree its profile handles


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


_EMBED_URLS = (
    "https://www.python.org/ftp/python/3.11.9/python-3.11.9-embed-amd64.zip",
    "https://mirrors.huaweicloud.com/python/3.11.9/python-3.11.9-embed-amd64.zip",
)
_GETPIP_URLS = (
    "https://bootstrap.pypa.io/pip/get-pip.py",
    "https://mirrors.aliyun.com/pypi/get-pip.py",
)


def _download_first(urls, dest: Path) -> None:
    import urllib.request
    last = None
    for url in urls:
        try:
            with urllib.request.urlopen(url, timeout=60) as resp:
                dest.write_bytes(resp.read())
            return
        except Exception as exc:
            last = exc
    raise RuntimeError(f"download: {last}")


def _provision_embedded_runtime(venv_dir: Path) -> Path:
    """Extract the official embeddable Python into venv_dir and give it pip.
    Returns its python.exe. Raises RuntimeError on failure."""
    import zipfile

    venv_dir.mkdir(parents=True, exist_ok=True)
    zip_path = venv_dir / "_embed.zip"
    _download_first(_EMBED_URLS, zip_path)
    with zipfile.ZipFile(zip_path) as zf:
        zf.extractall(venv_dir)
    zip_path.unlink(missing_ok=True)
    # enable site-packages (the embeddable ships with site disabled)
    for pth in venv_dir.glob("python*._pth"):
        pth.write_text("python311.zip\n.\nLib\nLib\\site-packages\nimport site\n",
                       encoding="utf-8")
    getpip = venv_dir / "get-pip.py"
    _download_first(_GETPIP_URLS, getpip)
    vpy = venv_dir / "python.exe"
    r = subprocess.run([str(vpy), str(getpip), "--no-warn-script-location"],
                       capture_output=True, timeout=600,
                       creationflags=_CREATE_NO_WINDOW)
    getpip.unlink(missing_ok=True)
    if r.returncode != 0:
        raise RuntimeError("get-pip: " + (r.stderr or b"").decode(errors="replace")[:200])
    return vpy


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

    src = _bundled_bridge_src()
    if not (src / "daemon.py").exists():
        raise RuntimeError("no-sources")

    root = installed_helper_root()
    root.mkdir(parents=True, exist_ok=True)
    venv_dir = root / "steamprobe-venv"

    py = find_system_python()
    if py is not None:
        _p("env")
        r = subprocess.run(py + ["-m", "venv", str(venv_dir)],
                           capture_output=True, timeout=180,
                           creationflags=_CREATE_NO_WINDOW)
        if r.returncode != 0:
            raise RuntimeError("venv: " + (r.stderr or b"").decode(errors="replace")[:200])
        vpy = venv_dir / "Scripts" / "python.exe"
    else:
        # No Python on this PC: provision the official embeddable runtime
        # (~11 MB, no admin, no installer) — mirrors first for regions where
        # python.org is slow or blocked.
        _p("runtime")
        vpy = _provision_embedded_runtime(venv_dir)

    _p("deps")
    ok = False
    # pypi.org first, then Chinese mirrors — SSL resets on the default index
    # are routine behind the GFW, and one mirror alone is not always enough
    for extra in ([],
                  ["-i", "https://pypi.tuna.tsinghua.edu.cn/simple"],
                  ["-i", "https://mirrors.aliyun.com/pypi/simple/"],
                  ["-i", "https://mirrors.ustc.edu.cn/pypi/simple/"]):
        r = subprocess.run([str(vpy), "-m", "pip", "install", "--quiet",
                            "--timeout", "30", "--retries", "2",
                            "playwright"] + extra,
                           capture_output=True, timeout=600,
                           creationflags=_CREATE_NO_WINDOW)
        if r.returncode == 0:
            ok = True
            break
    if not ok:
        raise RuntimeError("pip (all indexes): "
                           + (r.stderr or b"").decode(errors="replace")[:200])

    _p("files")
    bridge = root / "steam_bridge"
    bridge.mkdir(parents=True, exist_ok=True)
    for f in src.glob("*.py"):
        shutil.copy2(f, bridge / f.name)
    (root / "steamprobe-profile").mkdir(exist_ok=True)

    _p("done")
    return root
