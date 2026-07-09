"""Launch/stop the OCR detection overlay subprocess (prototype).

Mirrors the VR overlay's process model: the toggle starts a separate process so
OCR work (capture + detection + its own Tk event loop) never blocks the main
Flet app, and killing the process is a clean, complete "off".
"""

from __future__ import annotations

import contextlib
import logging
import subprocess
import sys

logger = logging.getLogger(__name__)


class OcrOverlayManager:
    def __init__(self, *, fps: float = 4.0, monitor: int = 1) -> None:
        self._proc: subprocess.Popen | None = None
        self._fps = fps
        self._monitor = monitor

    @property
    def running(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def start(self) -> bool:
        if self.running:
            return True
        # Prototype: launched from source via the venv interpreter. (Frozen
        # builds would need a bundled launcher — deferred until this ships.)
        if getattr(sys, "frozen", False):
            logger.warning("[OCR] overlay not available in packaged build yet")
            return False
        try:
            self._proc = subprocess.Popen(
                [sys.executable, "-m", "puripuly_heart.ocr.overlay_proc",
                 "--fps", str(self._fps), "--monitor", str(self._monitor)],
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
