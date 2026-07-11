"""Launch/stop the OCR detection overlay subprocess (prototype).

Mirrors the VR overlay's process model: the toggle starts a separate process so
OCR work (capture + detection + its own Tk event loop) never blocks the main
Flet app, and killing the process is a clean, complete "off".
"""

from __future__ import annotations

import contextlib
import ctypes
import logging
import os
import subprocess
import sys

logger = logging.getLogger(__name__)

# Named event that tells EVERY overlay generation to exit. The stored process
# handle can't reach a hot-swapped replacement, so toggle-off must broadcast.
_SHUTDOWN_EVENT = "PuriPulyHeart_OCR_Shutdown"
# Region lock control (handled by the overlay): start drag-selection / clear.
_SELECT_REGION_EVENT = "PuriPulyHeart_OCR_SelectRegion"
_CLEAR_REGION_EVENT = "PuriPulyHeart_OCR_ClearRegion"
_CONFIG_PATH = os.path.join(os.path.expanduser("~"), "AppData", "Local",
                            "puripuly-heart", "ocr_overlay_config.json")


def _shutdown_event(set_it: bool) -> None:
    with contextlib.suppress(Exception):
        kernel32 = ctypes.windll.kernel32
        evt = kernel32.CreateEventW(None, True, False, _SHUTDOWN_EVENT)
        if set_it:
            kernel32.SetEvent(evt)
        else:
            kernel32.ResetEvent(evt)


def load_ocr_prefs() -> dict:
    """OCR menu preferences, persisted in the overlay config file so they
    survive app restarts. Shared by the manager and the dashboard menu."""
    try:
        import json

        with open(_CONFIG_PATH, encoding="utf-8") as fh:
            cfg = json.load(fh)
        return cfg if isinstance(cfg, dict) else {}
    except Exception:
        return {}


def save_ocr_pref(key: str, value) -> None:
    with contextlib.suppress(Exception):
        import json

        cfg = load_ocr_prefs()
        cfg[key] = value
        os.makedirs(os.path.dirname(_CONFIG_PATH), exist_ok=True)
        with open(_CONFIG_PATH, "w", encoding="utf-8") as fh:
            json.dump(cfg, fh)


def _fire_event(name: str) -> None:
    with contextlib.suppress(Exception):
        kernel32 = ctypes.windll.kernel32
        evt = kernel32.CreateEventW(None, False, False, name)
        kernel32.SetEvent(evt)

# PROTOTYPE LOCAL BUILD ONLY. The packaged app doesn't bundle the OCR libraries
# (rapidocr/mss/opencv/tkinter), so when running frozen we launch the overlay
# through the source venv that DOES have them. These paths are this dev machine's
# repo; a real shippable OCR feature would bundle the deps instead.
_DEV_REPO = r"C:\Users\Owner\Desktop\PuriPuly-heart-2.1.2"
_DEV_VENV_PY = os.path.join(_DEV_REPO, ".venv", "Scripts", "python.exe")
_DEV_SRC = os.path.join(_DEV_REPO, "src")
_CREATE_NO_WINDOW = 0x08000000


class OcrOverlayManager:
    def __init__(self, *, fps: float = 10.0, monitor: int = 1) -> None:
        self._proc: subprocess.Popen | None = None
        self._fps = fps
        self._monitor = monitor
        # Defaults per user preference: scan only the focused VRChat window,
        # and only bubble-shaped text — persisted across restarts.
        _p = load_ocr_prefs()
        self.vrchat_only = bool(_p.get("vrchat_only", True))
        # Pre-warm recognition: read text in the background while subtitles
        # are toggled off — instant Alt+T at the cost of CPU bursts.
        self.prewarm = bool(_p.get("prewarm", True))
        # Only box text that looks like a VRChat chat bubble / nameplate.
        self.bubbles_only = bool(_p.get("bubbles_only", True))

    def set_prewarm(self, enabled: bool) -> None:
        enabled = bool(enabled)
        if enabled == self.prewarm:
            return
        self.prewarm = enabled
        save_ocr_pref("prewarm", enabled)
        if self.running:
            self.stop()
            self.start()

    def set_bubbles_only(self, enabled: bool) -> None:
        enabled = bool(enabled)
        if enabled == self.bubbles_only:
            return
        self.bubbles_only = enabled
        save_ocr_pref("bubbles_only", enabled)
        if self.running:
            self.stop()
            self.start()

    def has_region(self) -> bool:
        try:
            import json

            with open(_CONFIG_PATH, encoding="utf-8") as fh:
                r = json.load(fh).get("region")
            return isinstance(r, list) and len(r) == 4
        except Exception:
            return False

    def toggle_region(self) -> None:
        """No region set: start drag-selection in the overlay (starting it if
        needed). Region set: clear back to whole screen."""
        if self.has_region():
            if self.running:
                _fire_event(_CLEAR_REGION_EVENT)
            else:
                with contextlib.suppress(Exception):
                    import json

                    with open(_CONFIG_PATH, encoding="utf-8") as fh:
                        cfg = json.load(fh)
                    cfg["region"] = None
                    with open(_CONFIG_PATH, "w", encoding="utf-8") as fh:
                        json.dump(cfg, fh)
            return
        if not self.running:
            self.start()
        _fire_event(_SELECT_REGION_EVENT)

    def set_vrchat_only(self, enabled: bool) -> None:
        enabled = bool(enabled)
        if enabled == self.vrchat_only:
            return
        self.vrchat_only = enabled
        if self.running:
            # Apply immediately: relaunch the overlay with the new scope.
            self.stop()
            self.start()

    @property
    def running(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def start(self) -> bool:
        if self.running:
            return True
        _shutdown_event(False)  # clear any previous OFF signal before spawning
        args = ["-m", "puripuly_heart.ocr.overlay_proc",
                "--fps", str(self._fps), "--monitor", str(self._monitor),
                "--parent-pid", str(os.getpid()),
                "--prewarm", "1" if self.prewarm else "0",
                "--bubbles-only", "1" if self.bubbles_only else "0"]
        if self.vrchat_only:
            args += ["--window", "VRChat"]
        env = dict(os.environ)
        if getattr(sys, "frozen", False):
            # Packaged app: shell out to the source venv that has the OCR libs.
            python = _DEV_VENV_PY
            env["PYTHONPATH"] = _DEV_SRC
            if not os.path.exists(python):
                logger.warning("[OCR] dev venv not found at %s", python)
                return False
        else:
            python = sys.executable  # running from source
            env.setdefault("PYTHONPATH", _DEV_SRC)
        try:
            self._proc = subprocess.Popen(
                [python, *args], env=env, creationflags=_CREATE_NO_WINDOW,
            )
            logger.info("[OCR] overlay subprocess started (pid=%s)", self._proc.pid)
            return True
        except Exception as exc:
            logger.warning("[OCR] failed to start overlay: %s", exc)
            self._proc = None
            return False

    def stop(self) -> None:
        # Broadcast OFF to every overlay generation (a hot-swapped replacement
        # isn't our child, so the handle alone can't stop it).
        _shutdown_event(True)
        proc = self._proc
        self._proc = None
        if proc is not None:
            with contextlib.suppress(Exception):
                if proc.poll() is None:
                    proc.terminate()
        logger.info("[OCR] overlay stop signaled")

    def toggle(self, enabled: bool) -> bool:
        if enabled:
            return self.start()
        self.stop()
        return False
