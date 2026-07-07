"""Auto-update checker for GitHub Releases.

Checks for new releases on GitHub and provides update information.
Network failures are handled gracefully without affecting the main application.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import httpx

from puripuly_heart import GITHUB_REPO, __version__

logger = logging.getLogger(__name__)

GITHUB_API_URL = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"
GITHUB_RELEASES_URL = f"https://github.com/{GITHUB_REPO}/releases/latest"


@dataclass(slots=True)
class UpdateInfo:
    """Information about an available update."""

    version: str
    download_url: str
    release_notes: str


def _parse_version(version_str: str) -> tuple[int, ...]:
    """Parse version string like '0.1.0' or 'v0.1.0' into tuple of ints."""
    clean = version_str.lstrip("v")
    parts = clean.split(".")
    result = []
    for part in parts:
        # Handle pre-release versions like '0.1.0-beta'
        num_part = part.split("-")[0]
        try:
            result.append(int(num_part))
        except ValueError:
            result.append(0)
    return tuple(result)


def _is_newer(remote: str, current: str) -> bool:
    """Check if remote version is newer than current."""
    return _parse_version(remote) > _parse_version(current)


async def check_for_update() -> UpdateInfo | None:
    """Check GitHub for a newer release.

    Returns UpdateInfo if a new version is available, None otherwise.
    Network errors are silently ignored (returns None).
    """
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(
                GITHUB_API_URL,
                headers={"Accept": "application/vnd.github.v3+json"},
                follow_redirects=True,
            )

            if resp.status_code != 200:
                logger.debug(f"GitHub API returned {resp.status_code}")
                return None

            data: dict[str, Any] = resp.json()
            latest_version = data.get("tag_name", "").lstrip("v")

            if not latest_version:
                return None

            if not _is_newer(latest_version, __version__):
                logger.debug(f"Current version {__version__} is up to date")
                return None

            # Find installer download URL
            download_url = GITHUB_RELEASES_URL
            for asset in data.get("assets", []):
                name = asset.get("name", "")
                if name.endswith(".exe") or name.endswith(".zip"):
                    download_url = asset.get("browser_download_url", download_url)
                    break

            logger.info(f"New version available: {latest_version}")
            return UpdateInfo(
                version=latest_version,
                download_url=download_url,
                release_notes=data.get("body", ""),
            )

    except httpx.TimeoutException:
        logger.debug("Update check timed out")
        return None
    except Exception as exc:
        logger.debug(f"Update check failed: {exc}")
        return None


# ── In-app self-update ────────────────────────────────────────────────────────
# The release is a rolling asset on a fixed tag (v2.1.2), so the tag never
# changes between builds. Updates are keyed on the BUILD number instead: each
# release uploads a small version.json asset ({"build": 213, "tag": "r213"})
# next to PuriPulyHeart.zip, compared against the running build (_BUILD_TAG).

import json
import os
import subprocess
import sys
import zipfile
from pathlib import Path
from typing import Callable

# Release asset names. The legacy name is no longer uploaded (retired at r228
# while the updater was days old); the fallback read stays as a harmless safety
# in case an asset is ever published under the old name again.
ZIP_ASSET_NAME = "PuriPulyHeartPlus.zip"
LEGACY_ZIP_ASSET_NAME = "PuriPulyHeart.zip"
VERSION_ASSET_NAME = "version.json"


def current_build_number() -> int:
    """The running build number parsed from the dashboard build tag ("r213" -> 213)."""
    try:
        from puripuly_heart.ui.views.dashboard import _BUILD_TAG

        return int(str(_BUILD_TAG).lstrip("rR"))
    except Exception:
        return 0


def is_self_update_supported() -> bool:
    """Self-update only works for the packaged app (a source run has no install dir)."""
    return bool(getattr(sys, "frozen", False))


@dataclass(slots=True)
class RemoteBuild:
    build: int  # -1 when the release has no version.json yet (can't compare)
    tag: str
    zip_url: str
    zip_size: int
    # Short user-facing changelog bullets shipped in version.json ("notes").
    notes: tuple = ()


async def fetch_remote_build() -> RemoteBuild | None:
    """Fetch the latest release's build number + zip asset. None on network failure
    or when the release has no zip asset."""
    try:
        async with httpx.AsyncClient(timeout=10.0, follow_redirects=True) as client:
            resp = await client.get(
                GITHUB_API_URL, headers={"Accept": "application/vnd.github.v3+json"}
            )
            if resp.status_code != 200:
                return None
            data: dict[str, Any] = resp.json()
            zip_url = ""
            zip_size = 0
            legacy_zip_url = ""
            legacy_zip_size = 0
            version_url = ""
            for asset in data.get("assets", []):
                name = asset.get("name", "")
                if name == ZIP_ASSET_NAME:
                    zip_url = asset.get("browser_download_url", "")
                    zip_size = int(asset.get("size", 0) or 0)
                elif name == LEGACY_ZIP_ASSET_NAME:
                    legacy_zip_url = asset.get("browser_download_url", "")
                    legacy_zip_size = int(asset.get("size", 0) or 0)
                elif name == VERSION_ASSET_NAME:
                    version_url = asset.get("browser_download_url", "")
            if not zip_url:
                zip_url, zip_size = legacy_zip_url, legacy_zip_size
            if not zip_url:
                return None
            build = -1
            tag = ""
            notes: tuple = ()
            if version_url:
                v_resp = await client.get(version_url)
                if v_resp.status_code == 200:
                    try:
                        # lstrip BOM: a version.json written by PowerShell carries one
                        # and json.loads rejects it.
                        v_data = json.loads(v_resp.text.lstrip("﻿"))
                        build = int(v_data.get("build", -1))
                        tag = str(v_data.get("tag", ""))
                        raw_notes = v_data.get("notes", [])
                        if isinstance(raw_notes, list):
                            notes = tuple(
                                str(n).strip() for n in raw_notes if str(n).strip()
                            )[:8]
                    except Exception:
                        pass
            return RemoteBuild(
                build=build, tag=tag, zip_url=zip_url, zip_size=zip_size, notes=notes
            )
    except Exception as exc:
        logger.debug(f"fetch_remote_build failed: {exc}")
        return None


def update_staging_dir() -> Path:
    from puripuly_heart.config.paths import default_settings_path

    return default_settings_path().parent / "update"


def sweep_leftover_update_files() -> None:
    """Best-effort removal of a previous update's leftovers (the helper can't delete
    its own log file). Called when the Updates card is built at app start."""
    import shutil

    with_dir = update_staging_dir()
    try:
        if with_dir.exists():
            shutil.rmtree(with_dir, ignore_errors=True)
    except Exception:
        pass


async def download_update_zip(
    url: str,
    dest: Path,
    total_size: int,
    progress: Callable[[float], None] | None = None,
) -> None:
    """Stream the release zip to dest, reporting progress as a 0.0-1.0 fraction."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    done = 0
    async with httpx.AsyncClient(timeout=None, follow_redirects=True) as client:
        async with client.stream("GET", url) as resp:
            resp.raise_for_status()
            total = int(resp.headers.get("content-length", total_size) or total_size or 0)
            with open(dest, "wb") as fh:
                async for chunk in resp.aiter_bytes(chunk_size=1 << 18):
                    fh.write(chunk)
                    done += len(chunk)
                    if progress is not None and total > 0:
                        progress(min(1.0, done / total))


def extract_update_zip(zip_path: Path, stage_dir: Path) -> Path:
    """Extract the zip into stage_dir. Returns the extracted app root
    (stage_dir/PuriPulyHeart). Blocking — run via asyncio.to_thread."""
    import shutil

    if stage_dir.exists():
        shutil.rmtree(stage_dir, ignore_errors=True)
    stage_dir.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path) as zf:
        zf.extractall(stage_dir)
    app_root = stage_dir / "PuriPulyHeart"
    if not app_root.exists():
        # Zip without the top-level folder — treat the stage itself as the root.
        app_root = stage_dir
    return app_root


_SWAP_HELPER_PS1 = r"""
param([int]$AppPid, [string]$Stage, [string]$Dest, [string]$ExeName, [string]$Cleanup)
# Wait for the app to exit fully.
while (Get-Process -Id $AppPid -ErrorAction SilentlyContinue) { Start-Sleep -Milliseconds 300 }
# Nothing from the install dir may still be running (overlay/flet helpers).
Get-Process -ErrorAction SilentlyContinue | Where-Object { $_.Path -like "$Dest*" } |
    Stop-Process -Force -ErrorAction SilentlyContinue
Start-Sleep -Milliseconds 700
# Mirror the staged build over the install dir (retries ride out slow handle release).
robocopy "$Stage" "$Dest" /MIR /R:30 /W:1 | Out-Null
Start-Process -FilePath (Join-Path $Dest $ExeName)
Start-Sleep -Milliseconds 500
if ($Cleanup -and (Test-Path $Cleanup)) { Remove-Item $Cleanup -Recurse -Force -ErrorAction SilentlyContinue }
Remove-Item $MyInvocation.MyCommand.Path -Force -ErrorAction SilentlyContinue
"""


def launch_swap_helper(staged_app_root: Path) -> None:
    """Spawn the detached helper that swaps the install dir once this process exits,
    relaunches the app, and cleans the staging area. Caller must exit promptly."""
    install_dir = Path(sys.executable).resolve().parent
    exe_name = Path(sys.executable).name
    helper_path = update_staging_dir() / "apply_update.ps1"
    helper_path.parent.mkdir(parents=True, exist_ok=True)
    helper_path.write_text(_SWAP_HELPER_PS1, encoding="utf-8-sig")
    argv = [
        "powershell",
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(helper_path),
        "-AppPid",
        str(os.getpid()),
        "-Stage",
        str(staged_app_root),
        "-Dest",
        str(install_dir),
        "-ExeName",
        exe_name,
        "-Cleanup",
        str(update_staging_dir()),  # removes the zip, the stage, and the helper
    ]
    # CREATE_NO_WINDOW, not DETACHED_PROCESS: a fully detached PowerShell has no
    # console at all and console tools inside the script (robocopy) die with invalid
    # std handles — the swap silently never ran. NO_WINDOW provides a hidden console.
    # BREAKAWAY escapes the app's job object so the helper survives the app's exit.
    # Both verified live: NO_WINDOW|BREAKAWAY swapped and relaunched successfully.
    no_window = 0x08000000  # CREATE_NO_WINDOW
    breakaway = 0x01000000  # CREATE_BREAKAWAY_FROM_JOB
    helper_log = open(update_staging_dir() / "helper.log", "w", encoding="utf-8")
    try:
        subprocess.Popen(
            argv, creationflags=no_window | breakaway,
            stdout=helper_log, stderr=subprocess.STDOUT,
        )
    except OSError:
        subprocess.Popen(
            argv, creationflags=no_window,
            stdout=helper_log, stderr=subprocess.STDOUT,
        )
    logger.info(
        "[Updater] swap helper launched: stage=%s dest=%s", staged_app_root, install_dir
    )
