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
_ENABLE_REGION_EVENT = "PuriPulyHeart_OCR_EnableRegion"
_RELOAD_PREFS_EVENT = "PuriPulyHeart_OCR_ReloadPrefs"
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
        # Target window ('' = whole screen). Migrates from the old
        # vrchat_only bool; any window title works (Roblox, browsers, ...).
        _wt = _p.get("window_title")
        self.window_title = (str(_wt) if _wt is not None
                             else ("VRChat" if self.vrchat_only else ""))
        # Pre-warm recognition: read text in the background while subtitles
        # are toggled off — instant Alt+T at the cost of CPU bursts.
        self.prewarm = bool(_p.get("prewarm", True))
        # Only box text that looks like a VRChat chat bubble / nameplate.
        self.bubbles_only = bool(_p.get("bubbles_only", True))
        # Hide boxes whose text is already in the user's language.
        self.foreign_only = bool(_p.get("foreign_only", True))
        # Hide boxes that are purely a player name / a pronoun-bio field.
        self.ignore_names = bool(_p.get("ignore_names", True))
        self.ignore_pronouns = bool(_p.get("ignore_pronouns", True))
        # Master OCR translation switch (off = raw recognized text, no calls).
        self.translate = bool(_p.get("translate", True))

    def _reload_live(self) -> None:
        """Push saved prefs into the RUNNING overlay — no restart needed."""
        if self.running:
            _fire_event(_RELOAD_PREFS_EVENT)

    def set_window_title(self, title: str) -> None:
        title = (title or "").strip()
        if title == self.window_title:
            return
        self.window_title = title
        save_ocr_pref("window_title", title)
        save_ocr_pref("vrchat_only", title.lower() == "vrchat")
        self.vrchat_only = title.lower() == "vrchat"
        logger.info("[OCR] target window -> %r", title or "whole screen")
        self._reload_live()

    @staticmethod
    def list_windows(limit: int = 10) -> list[str]:
        """Titles of visible top-level windows (for the target picker)."""
        titles: list[str] = []
        try:
            user32 = ctypes.windll.user32
            GWL_EXSTYLE = -20
            WS_EX_TOOLWINDOW = 0x00000080

            @ctypes.WINFUNCTYPE(ctypes.c_int, ctypes.c_void_p,
                                ctypes.c_void_p)
            def _cb(hwnd, _lp):
                try:
                    if not user32.IsWindowVisible(hwnd):
                        return True
                    if user32.GetWindowLongW(hwnd, GWL_EXSTYLE) & WS_EX_TOOLWINDOW:
                        return True
                    n = user32.GetWindowTextLengthW(hwnd)
                    if n <= 0:
                        return True
                    buf = ctypes.create_unicode_buffer(n + 1)
                    user32.GetWindowTextW(hwnd, buf, n + 1)
                    t = buf.value.strip()
                    if (t and t not in titles
                            and not t.startswith("PuriPulyHeart")):
                        titles.append(t)
                except Exception:
                    pass
                return True

            user32.EnumWindows(_cb, 0)
        except Exception:
            pass
        return titles[:limit]

    def set_ignore_pronouns(self, enabled: bool) -> None:
        enabled = bool(enabled)
        if enabled == self.ignore_pronouns:
            return
        self.ignore_pronouns = enabled
        save_ocr_pref("ignore_pronouns", enabled)
        self._reload_live()

    def set_translate(self, enabled: bool) -> None:
        enabled = bool(enabled)
        if enabled == self.translate:
            return
        self.translate = enabled
        save_ocr_pref("translate", enabled)
        self._reload_live()

    def set_ignore_names(self, enabled: bool) -> None:
        enabled = bool(enabled)
        if enabled == self.ignore_names:
            return
        self.ignore_names = enabled
        save_ocr_pref("ignore_names", enabled)
        self._reload_live()

    def set_foreign_only(self, enabled: bool) -> None:
        enabled = bool(enabled)
        if enabled == self.foreign_only:
            return
        self.foreign_only = enabled
        save_ocr_pref("foreign_only", enabled)
        self._reload_live()

    def set_prewarm(self, enabled: bool) -> None:
        enabled = bool(enabled)
        if enabled == self.prewarm:
            return
        self.prewarm = enabled
        save_ocr_pref("prewarm", enabled)
        self._reload_live()

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
        r = load_ocr_prefs().get("region")
        return isinstance(r, list) and len(r) == 4

    def region_enabled(self) -> bool:
        p = load_ocr_prefs()
        r = p.get("region")
        return (isinstance(r, list) and len(r) == 4
                and bool(p.get("region_enabled", True)))

    def toggle_region(self) -> None:
        """Checkbox behavior: flip the lock on/off, REMEMBERING the last
        dragged rectangle. Only falls back to drag-selection when no
        rectangle has ever been set."""
        logger.info("[OCR] toggle_region (enabled=%s, has=%s, running=%s)",
                    self.region_enabled(), self.has_region(), self.running)
        if self.region_enabled():
            if self.running:
                _fire_event(_CLEAR_REGION_EVENT)
            else:
                save_ocr_pref("region_enabled", False)
            return
        if self.has_region():
            if self.running:
                _fire_event(_ENABLE_REGION_EVENT)
            else:
                save_ocr_pref("region_enabled", True)
            return
        self.select_region()

    def select_region(self) -> None:
        """Always start a fresh drag (the 'Set region' menu item)."""
        logger.info("[OCR] select_region requested (running=%s)", self.running)
        if not self.running:
            self.start()
        _fire_event(_SELECT_REGION_EVENT)
        logger.info("[OCR] select_region event fired")

    def set_vrchat_only(self, enabled: bool) -> None:
        # Legacy shim: routes through the generalized window targeting.
        self.set_window_title("VRChat" if enabled else "")

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
                "--bubbles-only", "1" if self.bubbles_only else "0",
                "--foreign-only", "1" if self.foreign_only else "0",
                "--ignore-names", "1" if self.ignore_names else "0",
                "--ignore-pronouns", "1" if self.ignore_pronouns else "0",
                "--translate", "1" if self.translate else "0"]
        if self.window_title:
            args += ["--window", self.window_title]
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
        logger.info("[OCR] toggle(%s) (running=%s)", enabled, self.running)
        if enabled:
            return self.start()
        self.stop()
        return False
