"""Register/unregister PuriPulyHeart with SteamVR's application list.

"Start with SteamVR": writes a .vrmanifest next to the packaged main executable and
invokes the overlay exe's one-shot `--set-autolaunch` CLI, which registers the manifest
with a running SteamVR and sets the auto-launch flag. SteamVR then starts the app
automatically whenever SteamVR itself starts (i.e. alongside VRChat VR sessions).
"""

from __future__ import annotations

import asyncio
import json
import logging
import sys
from pathlib import Path

logger = logging.getLogger(__name__)

STEAMVR_APP_KEY = "com.puripuly.heartplus"
VRMANIFEST_NAME = "puripulyheart.vrmanifest"

# Overlay exe exit codes (see native/overlay autolaunch.rs)
_EXIT_OK = 0
_EXIT_STEAMVR_NOT_RUNNING = 3


def main_executable_path() -> Path | None:
    """The packaged app exe, or None when running from source (dev runs must not
    register python.exe with SteamVR)."""
    if not getattr(sys, "frozen", False):
        return None
    return Path(sys.executable).resolve()


def write_vrmanifest(main_exe: Path) -> Path:
    """Write (or refresh) the SteamVR application manifest next to the main exe."""
    manifest_path = main_exe.with_name(VRMANIFEST_NAME)
    manifest = {
        "source": "builtin",
        "applications": [
            {
                "app_key": STEAMVR_APP_KEY,
                "launch_type": "binary",
                "binary_path_windows": main_exe.name,
                "arguments": "",
                # Overlay-style app: doesn't occupy SteamVR's "running application"
                # slot, so it coexists with VRChat.
                "is_dashboard_overlay": True,
                "strings": {
                    "en_us": {
                        "name": "PuriPulyHeart+",
                        "description": "VRChat translation subtitles overlay",
                    }
                },
            }
        ],
    }
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8", newline="\n")
    return manifest_path


async def apply_steamvr_autolaunch(enabled: bool) -> tuple[bool, str]:
    """Apply the auto-launch registration. Returns (ok, reason) where reason is one of
    "" (ok), "dev_build", "overlay_exe_missing", "steamvr_not_running", or an error
    detail string."""
    main_exe = main_executable_path()
    if main_exe is None:
        return False, "dev_build"

    try:
        from puripuly_heart.core.overlay.process import OverlayProcessManager

        overlay_exe = OverlayProcessManager.resolve_default_executable()
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("[SteamVR autolaunch] overlay exe resolution failed: %s", exc)
        return False, "overlay_exe_missing"
    if not overlay_exe.exists():
        return False, "overlay_exe_missing"

    try:
        manifest_path = write_vrmanifest(main_exe)
    except Exception as exc:
        logger.warning("[SteamVR autolaunch] manifest write failed: %s", exc)
        return False, f"manifest_write_failed: {exc}"

    argv = [
        str(overlay_exe),
        "--set-autolaunch",
        str(manifest_path),
        STEAMVR_APP_KEY,
        "1" if enabled else "0",
    ]
    try:
        process = await asyncio.create_subprocess_exec(
            *argv,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=20.0)
    except Exception as exc:
        logger.warning("[SteamVR autolaunch] subprocess failed: %s", exc)
        return False, f"subprocess_failed: {exc}"

    exit_code = process.returncode
    detail = (stderr or b"").decode("utf-8", errors="replace").strip()
    logger.info(
        "[SteamVR autolaunch] set enabled=%s exit=%s detail=%r stdout=%r",
        enabled,
        exit_code,
        detail,
        (stdout or b"").decode("utf-8", errors="replace").strip(),
    )
    if exit_code == _EXIT_OK:
        return True, ""
    if exit_code == _EXIT_STEAMVR_NOT_RUNNING:
        return False, "steamvr_not_running"
    return False, detail or f"exit_{exit_code}"
