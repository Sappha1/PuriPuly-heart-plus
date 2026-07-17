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

# Packaged builds ship the OCR overlay as its own frozen app (built from
# ocr_overlay.spec and copied to <app dir>\ocr\PuriPulyHeartOCR\) because it
# needs libraries the main bundle excludes (rapidocr/mss/opencv/tkinter).
# The dev-venv path below is the fallback for THIS dev machine only: running
# a packaged build from the repo (scratchpad test builds) hot-swaps the
# overlay from source instead of the bundled exe.
_DEV_REPO = r"C:\Users\Owner\Desktop\PuriPuly-heart-2.1.2"
_DEV_VENV_PY = os.path.join(_DEV_REPO, ".venv", "Scripts", "python.exe")
_DEV_SRC = os.path.join(_DEV_REPO, "src")
_CREATE_NO_WINDOW = 0x08000000


def _packaged_ocr_exe() -> str:
    return os.path.join(os.path.dirname(sys.executable),
                        "ocr", "PuriPulyHeartOCR", "PuriPulyHeartOCR.exe")


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
        # Free web engine used for OCR translation (never the user's paid
        # API quota): bing (default) / google / papago.
        self.xlat_service = str(_p.get("xlat_service", "bing"))

    def set_xlat_service(self, service: str) -> None:
        service = (service or "bing").lower()
        if service == self.xlat_service:
            return
        self.xlat_service = service
        save_ocr_pref("xlat_service", service)
        self._reload_live()

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
        # LIVE like every other flag — the legacy stop()/start() here
        # reset the scan toggle state on every bubbles flip.
        self._reload_live()

    def set_style(self, key: str, value) -> None:
        """Generic persisted style/behavior pref (ocr_format, ocr_place,
        ocr_outline, ocr_bg, ocr_bg_alpha, ocr_text, scan_mode, scan_bind).
        Applies live to the running overlay."""
        save_ocr_pref(key, value)
        logger.info("[OCR] style %s -> %r", key, value)
        self._reload_live()

    def _region_entry(self) -> dict | None:
        p = load_ocr_prefs()
        regs = p.get("regions")
        key = self.window_title or ""
        if isinstance(regs, dict) and isinstance(regs.get(key), dict):
            return regs[key]
        # Legacy single-region fallback (pre per-window storage).
        if key == "" and isinstance(p.get("region"), list):
            return {"rect": p.get("region"),
                    "on": bool(p.get("region_enabled", True))}
        return None

    def has_region(self) -> bool:
        e = self._region_entry()
        return bool(e and isinstance(e.get("rect"), list))

    def region_enabled(self) -> bool:
        e = self._region_entry()
        return bool(e and isinstance(e.get("rect"), list)
                    and e.get("on", True))

    def toggle_region(self) -> None:
        """Checkbox behavior: flip the lock on/off, REMEMBERING the last
        dragged rectangle. Only falls back to drag-selection when no
        rectangle has ever been set."""
        logger.info("[OCR] toggle_region (enabled=%s, has=%s, running=%s)",
                    self.region_enabled(), self.has_region(), self.running)
        def _set_on(flag: bool) -> None:
            p = load_ocr_prefs()
            regs = p.get("regions") if isinstance(p.get("regions"), dict) else {}
            key = self.window_title or ""
            e = regs.get(key)
            if not isinstance(e, dict) and key == "" and p.get("region"):
                e = {"rect": p.get("region")}
            if isinstance(e, dict):
                e["on"] = flag
                regs[key] = e
                save_ocr_pref("regions", regs)

        if self.region_enabled():
            if self.running:
                _fire_event(_CLEAR_REGION_EVENT)
            else:
                _set_on(False)
            return
        if self.has_region():
            if self.running:
                _fire_event(_ENABLE_REGION_EVENT)
            else:
                _set_on(True)
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
        flags = ["--fps", str(self._fps), "--monitor", str(self._monitor),
                 "--parent-pid", str(os.getpid()),
                 "--prewarm", "1" if self.prewarm else "0",
                 "--bubbles-only", "1" if self.bubbles_only else "0",
                 "--foreign-only", "1" if self.foreign_only else "0",
                 "--ignore-names", "1" if self.ignore_names else "0",
                 "--ignore-pronouns", "1" if self.ignore_pronouns else "0",
                 "--translate", "1" if self.translate else "0"]
        if self.window_title:
            flags += ["--window", self.window_title]
        env = dict(os.environ)
        if getattr(sys, "frozen", False):
            packaged = _packaged_ocr_exe()
            if os.path.exists(packaged):
                # Normal install: the bundled OCR overlay app.
                cmd = [packaged, *flags]
            elif os.path.exists(_DEV_VENV_PY):
                # Dev machine running a repo test build: hot-swap from source.
                env["PYTHONPATH"] = _DEV_SRC
                cmd = [_DEV_VENV_PY, "-m",
                       "puripuly_heart.ocr.overlay_proc", *flags]
            else:
                logger.warning(
                    "[OCR] no bundled overlay at %s and no dev venv", packaged)
                return False
        else:
            env.setdefault("PYTHONPATH", _DEV_SRC)
            cmd = [sys.executable, "-m",
                   "puripuly_heart.ocr.overlay_proc", *flags]
        try:
            self._proc = subprocess.Popen(
                cmd, env=env, creationflags=_CREATE_NO_WINDOW,
            )
            logger.info("[OCR] overlay subprocess started (pid=%s)", self._proc.pid)
            return True
        except Exception as exc:
            # WinError 5/4551 = Windows refused to execute the exe (AV, Smart
            # App Control, MotW...). The OS message names no file and no cause,
            # so diagnose it here — this line is what support logs live on.
            if getattr(exc, "winerror", None) in (5, 4551):
                from puripuly_heart.core.system_info import diagnose_blocked_executable

                logger.warning(
                    "[OCR] Windows blocked the overlay exe (WinError %s) — %s",
                    exc.winerror,
                    diagnose_blocked_executable(cmd[0]),
                )
            else:
                logger.warning(
                    "[OCR] failed to start overlay (exe=%s): %s", cmd[0], exc)
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
