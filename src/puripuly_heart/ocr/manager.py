"""Launch/stop the OCR detection overlay subprocess (prototype).

Mirrors the VR overlay's process model: the toggle starts a separate process so
OCR work (capture + detection + its own Tk event loop) never blocks the main
Flet app, and killing the process is a clean, complete "off".
"""

from __future__ import annotations

import contextlib
import logging
import os
import subprocess
import sys

logger = logging.getLogger(__name__)

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
        # Default: only the VRChat window is scanned/boxed (hidden when VRChat
        # isn't focused). Disabled => whole-screen OCR.
        self.vrchat_only = True

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
        args = ["-m", "puripuly_heart.ocr.overlay_proc",
                "--fps", str(self._fps), "--monitor", str(self._monitor),
                "--parent-pid", str(os.getpid())]
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
        proc = self._proc
        self._proc = None
        if proc is None:
            return
        with contextlib.suppress(Exception):
            if proc.poll() is None:
                proc.terminate()
        logger.info("[OCR] overlay subprocess stopped")

    def toggle(self, enabled: bool) -> bool:
        if enabled:
            return self.start()
        self.stop()
        return False
