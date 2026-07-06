"""Shared self-update state machine.

Single source of truth for update state so the dashboard sidebar button and the
About page Updates card can never disagree about whether a check/download is in
flight. Views register a listener and re-render themselves from the flow's
public fields on every notification.

States: idle → checking → (uptodate | available | error)
        available → downloading → (ready | error)
        ready → restarting (terminal — the swap helper takes over)
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from pathlib import Path
from typing import Callable

logger = logging.getLogger(__name__)

# Dev knob: pretend the release feed offers local+1 so the sidebar button can be
# exercised without publishing a newer release. launch_restart() stays gated on
# sys.frozen regardless, so a source run can never robocopy over its own venv.
_FAKE_UPDATE_ENV = "PPH_FAKE_UPDATE"


def dev_fake_update_enabled() -> bool:
    return bool(os.environ.get(_FAKE_UPDATE_ENV))


def _friendly_network_reason(exc: Exception) -> str:
    """Map transport errors to a human explanation — GitHub's release CDN is
    blocked in some regions (e.g. China), and 'ConnectTimeout(...)' tells the
    user nothing about why the update isn't happening."""
    msg = str(exc)
    lowered = msg.lower()
    if any(s in lowered for s in (
        "timeout", "timed out", "connecterror", "connect error", "getaddrinfo",
        "connection refused", "connection reset", "unreachable", "10060", "10054",
        "ssl", "temporary failure in name resolution",
    )):
        return "can't reach GitHub (network blocked or offline)"
    if "404" in lowered or "not found" in lowered:
        return "release file missing on GitHub"
    status = getattr(getattr(exc, "response", None), "status_code", None)
    if status:
        return f"GitHub returned HTTP {status}"
    return msg[:120] if msg else "unknown error"


class UpdateFlow:
    def __init__(self) -> None:
        self.state: str = "idle"
        self.progress: float = 0.0
        self.status_text: str = ""
        self.remote = None  # core.updater.RemoteBuild | None
        self.comparable: bool = True  # False when the release has no version.json
        self.staged_root: Path | None = None
        self.last_error: str = ""  # last failed download reason (retry hint)
        # Optional UI notice hook: (kind, detail). Wired by the app to snackbars.
        self.on_notice: Callable[[str, str], None] | None = None
        self._listeners: list[Callable[[], None]] = []

    # ── Observation ──────────────────────────────────────────────────────────

    def add_listener(self, cb: Callable[[], None]) -> None:
        if cb not in self._listeners:
            self._listeners.append(cb)

    def _notify(self) -> None:
        for cb in list(self._listeners):
            try:
                cb()
            except Exception:
                logger.debug("[UpdateFlow] listener failed", exc_info=True)

    def _set(self, state: str | None = None, *, progress: float | None = None,
             status: str | None = None) -> None:
        if state is not None:
            self.state = state
        if progress is not None:
            self.progress = progress
        if status is not None:
            self.status_text = status
        self._notify()

    # ── Derived UI helpers ───────────────────────────────────────────────────

    def sidebar_visible(self) -> bool:
        """The sidebar button only appears for a real, comparable update (or once
        a download/staged build is in flight — even one started from About)."""
        if self.state in ("downloading", "ready", "restarting"):
            return True
        return self.state == "available" and self.comparable

    def sidebar_tooltip(self) -> str:
        if self.state == "available":
            tag = ""
            size_mb = 0.0
            if self.remote is not None:
                tag = self.remote.tag or (f"r{self.remote.build}" if self.remote.build > 0 else "")
                size_mb = (self.remote.zip_size or 0) / (1024 * 1024)
            base = f"Update available: {tag}" if tag else "Update available"
            if self.last_error:
                return f"{base} — last attempt failed: {self.last_error}. Click to retry."
            suffix = "click to download & restart"
            return f"{base} ({size_mb:.0f} MB) — {suffix}" if size_mb else f"{base} — {suffix}"
        if self.state == "downloading":
            return f"Downloading update… {int(self.progress * 100)}%"
        if self.state in ("ready", "restarting"):
            return "Update ready — click to restart and apply"
        return ""

    # ── Actions ──────────────────────────────────────────────────────────────

    async def check_silently(self) -> None:
        await self.check(silent=True)

    async def check(self, silent: bool = False) -> None:
        from puripuly_heart.core.updater import (
            RemoteBuild,
            current_build_number,
            fetch_remote_build,
            is_self_update_supported,
        )

        if self.state in ("checking", "downloading", "ready", "restarting"):
            return
        if silent and not (is_self_update_supported() or dev_fake_update_enabled()):
            return  # source run: no install dir to update, skip the network hit
        self._set("checking", status="Checking for updates…")
        remote = await fetch_remote_build()
        local = current_build_number()
        if dev_fake_update_enabled():
            remote = RemoteBuild(
                build=local + 1,
                tag=f"r{local + 1} (fake)",
                zip_url=remote.zip_url if remote else "",
                zip_size=remote.zip_size if remote else 0,
            )
        if remote is None:
            if silent:
                self._set("idle", status="")
            else:
                self._set("error",
                          status="Couldn't reach GitHub — check your connection and try again.")
            return
        self.remote = remote
        self.comparable = remote.build > 0
        if not self.comparable:
            # Release predates version.json — can't tell if it's newer. Offer a
            # manual re-download from About, but never badge the sidebar with it.
            if silent:
                self._set("uptodate", status=f"Up to date (r{local}).")
            else:
                self._set("available",
                          status="Latest build number unknown — you can re-download the newest package.")
            return
        if remote.build <= local:
            self._set("uptodate", status=f"Up to date (r{local}).")
            return
        size_mb = (remote.zip_size or 0) / (1024 * 1024)
        self._set("available",
                  status=f"Update available: {remote.tag or 'r' + str(remote.build)} "
                         f"({size_mb:.0f} MB download).")
        logger.info("[UpdateFlow] update available: local=r%s remote=%s", local, remote.tag)

    async def download(self) -> None:
        from puripuly_heart.core.updater import (
            download_update_zip,
            extract_update_zip,
            update_staging_dir,
        )

        if self.state != "available" or self.remote is None:
            return
        remote = self.remote
        self.last_error = ""
        self._set("downloading", progress=0.0, status="Downloading update… 0%")
        zip_path = update_staging_dir() / "PuriPulyHeart.zip"
        last = {"t": 0.0}

        def _progress(frac: float) -> None:
            now = time.monotonic()
            if now - last["t"] >= 0.15 or frac >= 1.0:
                last["t"] = now
                self._set(progress=frac, status=f"Downloading update… {int(frac * 100)}%")

        try:
            await download_update_zip(remote.zip_url, zip_path, remote.zip_size, _progress)
            self._set(progress=1.0, status="Unpacking…")
            staged = await asyncio.to_thread(
                extract_update_zip, zip_path, update_staging_dir() / "stage"
            )
        except Exception as exc:
            reason = _friendly_network_reason(exc)
            logger.warning("[UpdateFlow] download failed: %s (%s)", reason, exc)
            self.last_error = reason
            # Back to "available", NOT "error": the sidebar button must stay
            # visible for a retry (previously it silently vanished on failure),
            # and the tooltip/About card explain what went wrong.
            self._set("available", progress=0.0,
                      status=f"Update failed: {reason}. Click to retry.")
            if self.on_notice is not None:
                try:
                    self.on_notice("download_failed", reason)
                except Exception:
                    pass
            return
        self.staged_root = staged
        self._set("ready",
                  status="Update downloaded. The app will close, apply the update, "
                         "and reopen automatically.")
        logger.info("[UpdateFlow] update staged at %s", staged)

    def launch_restart(self) -> bool:
        """Spawn the swap helper. Returns True when the caller should close the
        window; the helper waits for this process to exit before swapping."""
        from puripuly_heart.core.updater import is_self_update_supported, launch_swap_helper

        if self.state != "ready" or self.staged_root is None:
            return False
        if not is_self_update_supported():
            logger.info("[UpdateFlow] restart ignored — not a packaged build")
            return False
        try:
            launch_swap_helper(self.staged_root)
        except Exception as exc:
            self._set("error", status=f"Couldn't start the updater: {exc}")
            return False
        self._set("restarting", status="Restarting…")
        return True


_flow: UpdateFlow | None = None


def get_update_flow() -> UpdateFlow:
    global _flow
    if _flow is None:
        _flow = UpdateFlow()
    return _flow
