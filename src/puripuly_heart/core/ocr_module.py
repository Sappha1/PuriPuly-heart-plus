r"""Optional OCR module: download-on-demand install of the frozen OCR overlay.

The OCR overlay app (~150MB compressed, 40% of the install) became an optional
module in r277. Resolution order at launch time (ocr/manager.py):

  1. installer-bundled copy         <app>\ocr\PuriPulyHeartOCR\
  2. downloaded module (this file)  %LOCALAPPDATA%\puripuly-heart\modules\ocr\PuriPulyHeartOCR\
  3. dev venv (source machine only)

The downloaded module lives OUTSIDE the app dir, so app updates never touch
it; the updater's swap also preserves an installer-bundled ocr\ dir when
applying slim update zips. Each module carries a module.json {"build": N} for
the compatibility handshake.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import zipfile
from pathlib import Path
from typing import Callable

logger = logging.getLogger(__name__)

OCR_MODULE_ASSET_NAME = "PuriPulyHeartOCR-module.zip"
# Oldest module build this app can drive. Bump when the app<->overlay contract
# (CLI flags, config file schema, feed format) changes incompatibly.
REQUIRED_MODULE_BUILD = 277


def modules_ocr_root() -> Path:
    from puripuly_heart.config.paths import user_config_dir

    return user_config_dir() / "modules" / "ocr"


def installed_module_dir() -> Path:
    return modules_ocr_root() / "PuriPulyHeartOCR"


def installed_module_exe() -> Path:
    return installed_module_dir() / "PuriPulyHeartOCR.exe"


def installed_module_build() -> int:
    try:
        with open(installed_module_dir() / "module.json", encoding="utf-8") as fh:
            return int(json.load(fh).get("build", 0))
    except Exception:
        return 0


def module_ready() -> bool:
    return (
        installed_module_exe().exists()
        and installed_module_build() >= REQUIRED_MODULE_BUILD
    )


async def fetch_module_asset() -> tuple[str, int] | None:
    """(download_url, size_bytes) of the module zip on the latest release,
    or None when unavailable/offline."""
    import httpx

    from puripuly_heart.core.updater import GITHUB_API_URL

    try:
        async with httpx.AsyncClient(timeout=10.0, follow_redirects=True) as client:
            resp = await client.get(
                GITHUB_API_URL, headers={"Accept": "application/vnd.github.v3+json"}
            )
            if resp.status_code != 200:
                return None
            for asset in resp.json().get("assets", []):
                if asset.get("name", "") == OCR_MODULE_ASSET_NAME:
                    url = asset.get("browser_download_url", "")
                    if url:
                        return url, int(asset.get("size", 0) or 0)
    except Exception as exc:
        logger.warning("[OCRModule] asset lookup failed: %s", exc)
    return None


async def download_and_install_module(
    progress: Callable[[float], None] | None = None,
) -> Path:
    """Download the module zip and install it under the modules dir.
    Returns the installed exe path. Raises on failure (caller shows the
    manual-fallback instructions)."""
    from puripuly_heart.core.updater import download_update_zip

    asset = await fetch_module_asset()
    if asset is None:
        raise RuntimeError(
            "Could not reach the release page to download the OCR module"
        )
    url, size = asset
    root = modules_ocr_root()
    root.mkdir(parents=True, exist_ok=True)
    zip_path = root / "_module_download.zip"
    logger.info("[OCRModule] downloading %s (%s bytes)", url, size)
    await download_update_zip(url, zip_path, size, progress)

    import asyncio

    def _install() -> Path:
        tmp_dir = root / "_extract_tmp"
        shutil.rmtree(tmp_dir, ignore_errors=True)
        tmp_dir.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(zip_path) as zf:
            zf.extractall(tmp_dir)
        src = tmp_dir / "PuriPulyHeartOCR"
        if not src.exists():
            # Zip without the top-level folder — treat tmp as the module root.
            src = tmp_dir
        target = installed_module_dir()
        shutil.rmtree(target, ignore_errors=True)
        os.replace(src, target)
        shutil.rmtree(tmp_dir, ignore_errors=True)
        zip_path.unlink(missing_ok=True)
        exe = installed_module_exe()
        if not exe.exists():
            raise RuntimeError("Downloaded OCR module is missing its executable")
        logger.info(
            "[OCRModule] installed build %s at %s", installed_module_build(), target
        )
        return exe

    return await asyncio.to_thread(_install)
