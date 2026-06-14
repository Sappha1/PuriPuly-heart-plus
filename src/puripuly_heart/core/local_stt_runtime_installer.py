from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
import shutil
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Awaitable, Callable, Literal
from uuid import uuid4

import httpx

from puripuly_heart.core.local_stt_assets import (
    InstalledLocalSTTManifest,
    LocalSTTAssetError,
    LocalSTTAssetManifest,
    default_local_stt_model_root,
    default_local_stt_source_for_locale,
    inspect_local_stt_install_state,
    load_local_stt_asset_manifest,
    validate_local_stt_install,
)

RuntimeLocalSTTStatus = Literal["downloading", "ready", "download_failed"]


@dataclass(slots=True, frozen=True)
class RuntimeLocalSTTStatusUpdate:
    status: RuntimeLocalSTTStatus
    percent: int | None = None


StatusCallback = Callable[[RuntimeLocalSTTStatusUpdate], Awaitable[None] | None]


class LocalSTTRuntimeInstallError(LocalSTTAssetError):
    """Raised when runtime local STT provisioning fails."""


class LocalSTTRuntimeInstallCancelled(LocalSTTAssetError):
    """Raised when runtime local STT provisioning is cancelled."""


async def _emit_status(
    on_status: StatusCallback | None,
    status: RuntimeLocalSTTStatus,
    *,
    percent: int | None = None,
) -> None:
    if on_status is None:
        return
    result = on_status(RuntimeLocalSTTStatusUpdate(status=status, percent=percent))
    if inspect.isawaitable(result):
        await result


class _DownloadProgress:
    def __init__(self, total_bytes: int) -> None:
        self._total_bytes = max(total_bytes, 0)
        self._downloaded_bytes = 0
        self._lock = threading.Lock()

    def add(self, size_bytes: int) -> None:
        if size_bytes <= 0:
            return
        with self._lock:
            self._downloaded_bytes += size_bytes

    def percent(self) -> int:
        if self._total_bytes <= 0:
            return 0
        with self._lock:
            downloaded_bytes = self._downloaded_bytes
        return min(99, int(downloaded_bytes * 100 / self._total_bytes))


def _source_order(
    manifest: LocalSTTAssetManifest,
    *,
    preferred_source: str | None,
    locale: str | None,
) -> tuple[str, ...]:
    selected = preferred_source or default_local_stt_source_for_locale(locale)
    names: list[str] = []
    if selected in manifest.sources:
        names.append(selected)
    for name in manifest.sources:
        if name not in names:
            names.append(name)
    return tuple(names[:2])


def _raise_if_cancelled(cancel_event: threading.Event | None) -> None:
    if cancel_event is not None and cancel_event.is_set():
        raise LocalSTTRuntimeInstallCancelled("runtime local STT install cancelled")


def _download_source_into_staging(
    *,
    source_name: str,
    staging_dir: Path,
    manifest: LocalSTTAssetManifest,
    cancel_event: threading.Event | None = None,
    progress: _DownloadProgress | None = None,
) -> InstalledLocalSTTManifest:
    try:
        _raise_if_cancelled(cancel_event)
        source = manifest.sources[source_name]
        with httpx.Client(timeout=30.0, follow_redirects=True) as client:
            for asset in manifest.files:
                _raise_if_cancelled(cancel_event)
                asset_path = staging_dir / asset.relative_path
                asset_path.parent.mkdir(parents=True, exist_ok=True)
                digest = hashlib.sha256()
                size_bytes = 0
                url = source.download_url_template.format(
                    path=asset.remote_path_for_source(source_name)
                )
                with client.stream("GET", url) as response:
                    response.raise_for_status()
                    with asset_path.open("wb") as handle:
                        for chunk in response.iter_bytes():
                            _raise_if_cancelled(cancel_event)
                            if not chunk:
                                continue
                            handle.write(chunk)
                            digest.update(chunk)
                            size_bytes += len(chunk)
                            if progress is not None:
                                progress.add(len(chunk))
                if digest.hexdigest() != asset.sha256:
                    raise LocalSTTRuntimeInstallError(
                        f"checksum mismatch for required model file: {asset.relative_path}"
                    )
                if asset.size_bytes is not None and size_bytes != asset.size_bytes:
                    raise LocalSTTRuntimeInstallError(
                        f"size mismatch for required model file: {asset.relative_path}"
                    )

        installed = InstalledLocalSTTManifest(
            manifest_version=manifest.installed_manifest_version,
            model_id=manifest.model_id,
            engine=manifest.engine,
            install_dirname=manifest.install_dirname,
            selected_source=source_name,
            selected_revision=source.revision,
        )
        (staging_dir / manifest.installed_manifest_filename).write_text(
            json.dumps(installed.to_dict(), indent=2),
            encoding="utf-8",
        )
        validate_local_stt_install(staging_dir, manifest=manifest)
        return installed
    except LocalSTTRuntimeInstallCancelled:
        shutil.rmtree(staging_dir, ignore_errors=True)
        raise


def _promote_staging_install(
    *,
    staging_dir: Path,
    install_dir: Path,
    cancel_event: threading.Event | None = None,
) -> None:
    _raise_if_cancelled(cancel_event)
    backup_dir = install_dir.with_name(f"{install_dir.name}.backup")
    install_dir.parent.mkdir(parents=True, exist_ok=True)
    shutil.rmtree(backup_dir, ignore_errors=True)

    had_existing_install = install_dir.exists()
    if had_existing_install:
        install_dir.rename(backup_dir)

    try:
        staging_dir.rename(install_dir)
    except Exception:
        if install_dir.exists():
            shutil.rmtree(install_dir, ignore_errors=True)
        if had_existing_install and backup_dir.exists():
            backup_dir.rename(install_dir)
        raise
    else:
        if backup_dir.exists():
            shutil.rmtree(backup_dir, ignore_errors=True)


async def ensure_local_stt_installed(
    *,
    preferred_source: str | None = None,
    locale: str | None = None,
    model_root: Path | None = None,
    manifest: LocalSTTAssetManifest | None = None,
    on_status: StatusCallback | None = None,
    cancel_event: threading.Event | None = None,
) -> InstalledLocalSTTManifest:
    resolved_manifest = manifest or load_local_stt_asset_manifest()
    resolved_root = model_root or default_local_stt_model_root()
    install_dir = resolved_root / resolved_manifest.install_dirname
    total_bytes = sum(asset.size_bytes or 0 for asset in resolved_manifest.files)

    _raise_if_cancelled(cancel_event)
    state = inspect_local_stt_install_state(install_dir, manifest=resolved_manifest)
    if state.status == "ready" and state.installed_manifest is not None:
        try:
            return await asyncio.to_thread(
                validate_local_stt_install,
                install_dir,
                manifest=resolved_manifest,
            )
        except LocalSTTAssetError:
            # Cheap runtime inspection is allowed to say "ready" without checksums.
            # Repair/download should only skip when the full install contract passes.
            pass

    _raise_if_cancelled(cancel_event)
    failures: list[str] = []
    last_progress_percent: int | None = None

    for source_name in _source_order(
        resolved_manifest,
        preferred_source=preferred_source,
        locale=locale,
    ):
        _raise_if_cancelled(cancel_event)
        staging_dir = resolved_root / f"{resolved_manifest.install_dirname}.staging-{uuid4().hex}"
        shutil.rmtree(staging_dir, ignore_errors=True)
        progress = _DownloadProgress(total_bytes)
        try:
            current_percent = 0 if last_progress_percent is None else last_progress_percent
            if current_percent != last_progress_percent:
                last_progress_percent = current_percent
                await _emit_status(on_status, "downloading", percent=current_percent)

            download_task = asyncio.create_task(
                asyncio.to_thread(
                    _download_source_into_staging,
                    source_name=source_name,
                    staging_dir=staging_dir,
                    manifest=resolved_manifest,
                    cancel_event=cancel_event,
                    progress=progress,
                )
            )
            while not download_task.done():
                _raise_if_cancelled(cancel_event)
                current_percent = max(last_progress_percent or 0, progress.percent())
                if current_percent != last_progress_percent:
                    last_progress_percent = current_percent
                    await _emit_status(on_status, "downloading", percent=current_percent)
                await asyncio.sleep(0.05)

            installed = await download_task
            current_percent = max(last_progress_percent or 0, progress.percent())
            if current_percent != last_progress_percent:
                last_progress_percent = current_percent
                await _emit_status(on_status, "downloading", percent=current_percent)
            await asyncio.to_thread(
                _promote_staging_install,
                staging_dir=staging_dir,
                install_dir=install_dir,
                cancel_event=cancel_event,
            )
            await _emit_status(on_status, "ready", percent=None)
            return installed
        except LocalSTTRuntimeInstallCancelled:
            shutil.rmtree(staging_dir, ignore_errors=True)
            raise
        except Exception as exc:
            failures.append(f"{source_name}: {exc}")
            shutil.rmtree(staging_dir, ignore_errors=True)

    await _emit_status(on_status, "download_failed", percent=None)
    raise LocalSTTRuntimeInstallError("; ".join(failures) or "runtime local STT install failed")
