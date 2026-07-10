"""OCR detection overlay — standalone subprocess (prototype).

Detect-then-track with global camera-motion compensation, an extrapolating
renderer, appearance validation, and (default) VRChat-window scoping:

  * WINDOW TARGETING: with --window "VRChat" (the default launch), capture,
    detection and boxes are restricted to the VRChat window's client area,
    and everything hides whenever VRChat isn't the foreground window — no
    boxing of other apps. Without --window it runs on the whole monitor.
  * DETECTION thread (960px eq., ~0.25 s) finds text boxes ~3x/sec.
  * TRACKING loop: per delivered frame, ONE downscale + ONE batched optical
    flow call (whole-frame grid = camera motion + all boxes). Camera motion
    moves frozen boxes' anchors (world-locked text glides at any pan speed);
    freezing only suppresses residual noise. Detection refreshes SOFT-MERGE
    into live boxes.
  * APPEARANCE SIGNATURES: each box fingerprints the pixels it covers; if the
    content under a box vanishes (e.g. the escape menu closed), the box is
    dropped within a few frames instead of waiting for the next detection.
  * RENDERER (~250 Hz) extrapolates along per-box velocity so motion is
    continuous between capture frames and never trails.
  * PrintScreen saves a composited PNG to Desktop/puripuly_ocr_shots/ from an
    isolated thread.

Run directly:
    python -m puripuly_heart.ocr.overlay_proc --window VRChat
"""

from __future__ import annotations

import argparse
import ctypes
import logging
import os
import threading
import time
import tkinter as tk
from ctypes import wintypes

import numpy as np

from puripuly_heart.ocr.detector import TextBox, TextDetector

logger = logging.getLogger(__name__)

_TRANSPARENT_KEY = "#010203"
_BOX_COLOR = "#ff2020"
_BOX_WIDTH = 1

_TRACK_SIDE = 1280
_DETECT_SIDE = 960
_DETECT_INTERVAL = 0.22  # detect itself ~0.25s; effectively back-to-back
_PTS_X, _PTS_Y = 4, 3
_GRID_X, _GRID_Y = 8, 5

_UNFREEZE_PX = 2.0
_STILL_SPEED_PX = 0.35
_STILL_FRAMES = 8
_STILL_DECAY = 0.12

_GLOBAL_MOVE_PX = 0.04

_CUT_DIFF_HARD = 70.0
_CUT_DIFF_SOFT = 38.0
_CUT_GRID_RATIO = 0.5

_DET_STALE = 45.0
_MIN_OK_RATIO = 0.34

# Merge/miss tolerance. The detector is nondeterministic on borderline text —
# it can find a line on one pass and miss it on the next, or alternate the
# box extents. A strict match + fast miss-delete made such boxes blink at the
# detection cadence (~0.5s). Boxes now survive several missed passes (their
# content is still verified text-like every frame by texture/appearance) and
# match more loosely so extent wobble updates the box instead of replacing it.
_MERGE_IOU = 0.25
_MERGE_BLEND = 0.60
# PERMISSIVE PROFILE (user-chosen): the high-recall pre-filter pipeline
# "detected everything" best. The recognition gate and content-kill checks
# cost too much recall/latency, so they are OFF; detection publishes every
# text-shaped region and tracking keeps it glued. Rec stays available for
# the future translation stage.
_REC_GATE_ENABLED = False
_TEXTURE_KILL_ENABLED = False
_SIG_KILL_ENABLED = False

_REC_MIN_SCORE = 0.50
_REC_MAX_BOXES = 25          # rec budget per detection pass
import re as _re
_REC_TEXTY = _re.compile(r"[0-9A-Za-z一-鿿぀-ヿ가-힯]")

# Persistence: every displayed box has already passed recognition, so err on
# the side of KEEPING it — flicker hurts more than a briefly stale box.
_MAX_MISSES_CONFIRMED = 6
_MAX_MISSES_UNPROVEN = 3
_CONFIRMED_AT = 2

# Appearance signature: sample grid inside each box; if the mean abs gray
# difference vs the remembered fingerprint exceeds this for a few consecutive
# frames, the content is gone (menu closed / bubble expired) — drop the box.
_SIG_X, _SIG_Y = 6, 2
_SIG_DIFF = 42.0
_SIG_BAD_FRAMES = 4
# A box detection re-confirmed this recently is REAL text — never sig-kill it
# (stops the kill/re-detect flash loop on high-contrast text).
_SIG_CONFIRM_GRACE_S = 0.7

# The game renders its own mouse cursor INTO the frame; pixels under it really
# change. Flow points within this radius (track px) of the cursor are skipped
# and content checks are suspended for boxes the cursor is touching.
_CURSOR_RADIUS = 18.0

# Focus-loss debounce. Foreground can blip for milliseconds (the app's own
# always-on-top subtitle overlay re-asserting bounds, OS transitions); reacting
# instantly nuked all boxes and forced a ~0.5s rebuild — visible flashing on
# perfectly static text. Only a SUSTAINED focus change hides the boxes.
_FG_GRACE_S = 0.6

# A small box centered on the cursor IS the cursor: the pointer glyph passes
# the text-shape filter and (being fully inside the cursor radius) even tracks
# its motion. Boxes this small, this close to the pointer, are never kept.
_CURSOR_BOX_DIST = 22.0
_CURSOR_BOX_W = 42.0
_CURSOR_BOX_H = 28.0


def _is_cursor_box(x1: float, y1: float, x2: float, y2: float,
                   cursor: tuple[float, float] | None) -> bool:
    if cursor is None:
        return False
    if (x2 - x1) > _CURSOR_BOX_W or (y2 - y1) > _CURSOR_BOX_H:
        return False
    cx, cy = (x1 + x2) / 2, (y1 + y2) / 2
    return ((cx - cursor[0]) ** 2 + (cy - cursor[1]) ** 2
            <= _CURSOR_BOX_DIST * _CURSOR_BOX_DIST)

# Texture check: text has strong local contrast. A box whose content's std
# stays below this is sitting on a featureless surface (floor/wall a ghost
# drifted onto) — kill it. Applies even during motion, since flat is flat.
_TEX_MIN_STD = 10.0
_TEX_BAD_FRAMES = 4

_RENDER_LEAD_S = 0.016
_RENDER_EXTRAP_CAP_S = 0.06

# Capture watchdog: recreate the DXGI camera if no frame arrives this long
# (PrintScreen/snipping tools can invalidate desktop duplication silently).
_CAPTURE_STALL_S = 3.0

_VK_SNAPSHOT = 0x2C
_SHOT_DIR = os.path.join(os.path.expanduser("~"), "Desktop", "puripuly_ocr_shots")


def _set_dpi_aware() -> None:
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass


def _make_click_through(root: tk.Tk) -> None:
    GWL_EXSTYLE = -20
    WS_EX_LAYERED = 0x00080000
    WS_EX_TRANSPARENT = 0x00000020
    WS_EX_TOOLWINDOW = 0x00000080
    try:
        hwnd = ctypes.windll.user32.GetParent(root.winfo_id())
        styles = ctypes.windll.user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
        ctypes.windll.user32.SetWindowLongW(
            hwnd, GWL_EXSTYLE,
            styles | WS_EX_LAYERED | WS_EX_TRANSPARENT | WS_EX_TOOLWINDOW,
        )
    except Exception as exc:
        logger.debug("[OCR] click-through setup failed: %s", exc)


def _exclude_from_capture(root: tk.Tk) -> None:
    WDA_EXCLUDEFROMCAPTURE = 0x00000011
    try:
        hwnd = ctypes.windll.user32.GetParent(root.winfo_id())
        ctypes.windll.user32.SetWindowDisplayAffinity(hwnd, WDA_EXCLUDEFROMCAPTURE)
    except Exception as exc:
        logger.debug("[OCR] exclude-from-capture failed: %s", exc)


class _Capture:
    def __init__(self) -> None:
        self._cam = None
        self._sct = None
        self._mon = None
        self.width = self.height = 0
        self.left = self.top = 0
        try:
            import dxcam

            self._cam = dxcam.create(output_color="BGR")
            frame = None
            for _ in range(10):
                frame = self._cam.grab()
                if frame is not None:
                    break
                time.sleep(0.01)
            if frame is None:
                raise RuntimeError("dxcam produced no frame")
            self.height, self.width = frame.shape[:2]
            self._last = frame
            self._last_ok = time.monotonic()
            logger.info("[OCR] capture: dxcam %dx%d", self.width, self.height)
        except Exception as exc:
            logger.warning("[OCR] dxcam unavailable (%s); using mss", exc)
            import mss

            self._sct = mss.mss()
            self._mon = self._sct.monitors[1]
            self.width, self.height = self._mon["width"], self._mon["height"]
            self.left, self.top = self._mon["left"], self._mon["top"]
            self._last = np.asarray(self._sct.grab(self._mon))[:, :, :3]

    def grab(self) -> np.ndarray | None:
        if self._cam is not None:
            try:
                f = self._cam.grab()
            except Exception as exc:
                logger.warning("[OCR] dxcam grab failed (%s); reinitializing", exc)
                self._reinit()
                return None
            if f is not None:
                self._last = f
                self._last_ok = time.monotonic()
                return f
            # Watchdog: PrintScreen/snipping tools can silently invalidate the
            # DXGI duplication session — grab() then returns None forever even
            # though the screen is changing. Recreate the camera after a stall.
            if time.monotonic() - getattr(self, "_last_ok", 0.0) > _CAPTURE_STALL_S:
                logger.info("[OCR] capture stalled %.1fs — recreating camera",
                            _CAPTURE_STALL_S)
                self._reinit()
            return None
        f = np.asarray(self._sct.grab(self._mon))[:, :, :3]
        self._last = f
        return f

    def _reinit(self) -> None:
        self._last_ok = time.monotonic()
        try:
            del self._cam
        except Exception:
            pass
        self._cam = None
        try:
            import dxcam

            self._cam = dxcam.create(output_color="BGR")
        except Exception as exc:
            logger.warning("[OCR] dxcam recreate failed: %s", exc)

    def last(self) -> np.ndarray:
        return self._last


class _Target:
    """Tracks the capture region: a specific window's client area (VRChat
    mode) or the whole monitor (global mode). epoch increments whenever the
    region changes so both loops can resynchronize. Also computes OCCLUSIONS:
    regions of the target covered by other windows (chat apps, file explorer,
    the translator itself) — those pixels belong to other apps and must never
    be scanned or boxed."""

    def __init__(self, title: str | None, cap: _Capture) -> None:
        self._title = title or None
        self._cap = cap
        self._lock = threading.Lock()
        self._rect: tuple[int, int, int, int] | None = (0, 0, cap.width, cap.height)
        self._fg = True
        self._fg_title = ""
        self._epoch = 0
        self._warned = False
        self._occl: list[tuple[int, int, int, int]] = []
        if self._title:
            self._rect = None  # resolved by poll()

    @staticmethod
    def _occlusions_for(hwnd: int, sx1: int, sy1: int, sx2: int, sy2: int
                        ) -> list[tuple[int, int, int, int]]:
        """Screen rects of visible windows ABOVE hwnd that overlap it, in
        target-local coordinates. EnumWindows yields top-to-bottom z-order."""
        user32 = ctypes.windll.user32
        GWL_EXSTYLE = -20
        WS_EX_TRANSPARENT = 0x00000020
        DWMWA_CLOAKED = 14
        order: list[int] = []

        @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        def _cb(hw, _lp):
            order.append(int(hw))
            return True

        user32.EnumWindows(_cb, 0)
        occl: list[tuple[int, int, int, int]] = []
        for hw in order:
            if hw == hwnd:
                break  # everything after is beneath the target
            try:
                if not user32.IsWindowVisible(hw):
                    continue
                if user32.GetWindowLongW(hw, GWL_EXSTYLE) & WS_EX_TRANSPARENT:
                    continue  # click-through overlays (incl. our own boxes)
                cloaked = wintypes.DWORD(0)
                try:
                    ctypes.windll.dwmapi.DwmGetWindowAttribute(
                        hw, DWMWA_CLOAKED, ctypes.byref(cloaked),
                        ctypes.sizeof(cloaked))
                except Exception:
                    pass
                if cloaked.value:
                    continue  # invisible UWP shells report visible otherwise
                r = wintypes.RECT()
                user32.GetWindowRect(hw, ctypes.byref(r))
                ix1, iy1 = max(sx1, r.left), max(sy1, r.top)
                ix2, iy2 = min(sx2, r.right), min(sy2, r.bottom)
                if ix2 - ix1 > 8 and iy2 - iy1 > 8:
                    occl.append((ix1 - sx1, iy1 - sy1, ix2 - sx1, iy2 - sy1))
                if len(occl) >= 12:
                    break
            except Exception:
                continue
        return occl

    @staticmethod
    def _find_window(title: str) -> int:
        """Largest VISIBLE window with this exact title. FindWindowW returns
        the first title match, which can be one of the game's hidden helper
        windows — that intermittently blanked VRChat-only mode."""
        user32 = ctypes.windll.user32
        matches: list[tuple[int, int]] = []

        @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        def _cb(hwnd, _lp):
            try:
                if not user32.IsWindowVisible(hwnd):
                    return True
                n = user32.GetWindowTextLengthW(hwnd)
                if n <= 0:
                    return True
                buf = ctypes.create_unicode_buffer(n + 1)
                user32.GetWindowTextW(hwnd, buf, n + 1)
                if buf.value == title:
                    r = wintypes.RECT()
                    user32.GetClientRect(hwnd, ctypes.byref(r))
                    matches.append((int(r.right) * int(r.bottom), int(hwnd)))
            except Exception:
                pass
            return True

        user32.EnumWindows(_cb, 0)
        return max(matches)[1] if matches else 0

    def poll(self) -> None:
        if not self._title:
            return
        user32 = ctypes.windll.user32
        rect_new: tuple[int, int, int, int] | None = None
        occl_new: list[tuple[int, int, int, int]] = []
        fg = False
        try:
            # FOREGROUND-FIRST: with multiple same-titled windows (the user
            # runs TWO VRChat instances — identical 1920x1080 windows!), size
            # cannot identify the one being played. The focused one can.
            hwnd = 0
            fgw = user32.GetForegroundWindow()
            if fgw and user32.IsWindowVisible(fgw):
                n = user32.GetWindowTextLengthW(fgw)
                if n > 0:
                    b = ctypes.create_unicode_buffer(n + 1)
                    user32.GetWindowTextW(fgw, b, n + 1)
                    if b.value == self._title:
                        hwnd = fgw
            if not hwnd:
                hwnd = self._find_window(self._title)
            if hwnd:
                # DWM extended frame bounds: PHYSICAL screen pixels, immune to
                # DPI virtualization. GetClientRect/ClientToScreen return the
                # game's LOGICAL coords when it runs windowed under display
                # scaling (measured: 1920x1080 reported for a screen-filling
                # window) — the scan then covered a corner of the game and
                # every box landed offset.
                x1 = y1 = x2 = y2 = 0
                got = False
                try:
                    eb = wintypes.RECT()
                    DWMWA_EXTENDED_FRAME_BOUNDS = 9
                    if ctypes.windll.dwmapi.DwmGetWindowAttribute(
                            hwnd, DWMWA_EXTENDED_FRAME_BOUNDS,
                            ctypes.byref(eb), ctypes.sizeof(eb)) == 0:
                        x1 = eb.left - self._cap.left
                        y1 = eb.top - self._cap.top
                        x2 = eb.right - self._cap.left
                        y2 = eb.bottom - self._cap.top
                        got = True
                except Exception:
                    pass
                if not got:
                    r = wintypes.RECT()
                    user32.GetClientRect(hwnd, ctypes.byref(r))
                    pt = wintypes.POINT(0, 0)
                    user32.ClientToScreen(hwnd, ctypes.byref(pt))
                    x1 = pt.x - self._cap.left
                    y1 = pt.y - self._cap.top
                    x2, y2 = x1 + r.right, y1 + r.bottom
                x1 = max(0, min(self._cap.width, x1))
                y1 = max(0, min(self._cap.height, y1))
                x2 = max(0, min(self._cap.width, x2))
                y2 = max(0, min(self._cap.height, y2))
                if x2 - x1 >= 64 and y2 - y1 >= 64:
                    rect_new = (x1, y1, x2, y2)
                    occl_new = self._occlusions_for(
                        hwnd,
                        x1 + self._cap.left, y1 + self._cap.top,
                        x2 + self._cap.left, y2 + self._cap.top)
                else:
                    occl_new = []
                fgw = user32.GetForegroundWindow()
                fg = fgw == hwnd
                fg_title = ""
                if not fg and fgw:
                    m = user32.GetWindowTextLengthW(fgw)
                    if m > 0:
                        b2 = ctypes.create_unicode_buffer(m + 1)
                        user32.GetWindowTextW(fgw, b2, m + 1)
                        fg_title = b2.value
                self._fg_title = fg_title
                # NOTE: no "friendly focus" for our own app. With the app in
                # front, the scanned region contains the app's UI — whose
                # blinking caret/spinners are REAL appearing/disappearing text
                # to the detector, i.e. permanently flashing boxes. VRChat
                # focused = scan; anything else focused = idle. (Short blips
                # are still absorbed by the debounce in the track loop.)
        except Exception as exc:
            logger.debug("[OCR] window poll failed: %s", exc)
        with self._lock:
            if rect_new != self._rect:
                self._rect = rect_new
                self._epoch += 1
            self._fg = fg
            self._occl = occl_new if rect_new is not None else []
        if rect_new is None and not self._warned:
            self._warned = True
            logger.info("[OCR] window '%s' not found — boxes hidden until it appears",
                        self._title)
        elif rect_new is not None:
            self._warned = False

    def get(self) -> tuple[tuple[int, int, int, int] | None, bool, int]:
        with self._lock:
            return self._rect, self._fg, self._epoch

    def fg_title(self) -> str:
        with self._lock:
            return self._fg_title

    def occlusions(self) -> list[tuple[int, int, int, int]]:
        with self._lock:
            return list(self._occl)


def _target_loop(target: _Target, stop: threading.Event) -> None:
    while not stop.is_set():
        target.poll()
        stop.wait(0.4)


class _Anchors:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.boxes: list[TextBox] = []
        self.gray: np.ndarray | None = None
        self.epoch = -1
        self.stamp = 0

    def publish(self, boxes: list[TextBox], gray: np.ndarray, epoch: int) -> None:
        with self._lock:
            self.boxes, self.gray, self.epoch = boxes, gray, epoch
            self.stamp += 1

    def take(self, last_stamp: int):
        with self._lock:
            if self.stamp == last_stamp:
                return None
            return self.stamp, list(self.boxes), self.gray, self.epoch


def _text_plausible(b: TextBox, det_w: int, det_h: int) -> bool:
    # Minimal filter (permissive profile): reject only specks and
    # screen-sized slabs; everything else gets its chance.
    w = b.x2 - b.x1
    h = b.y2 - b.y1
    if w < 8 or h < 5:
        return False
    if w * h < 80:
        return False
    if h > 0.5 * det_h and w > 0.9 * det_w:
        return False
    return True


def _detect_loop(cap: _Capture, target: _Target, anchors: _Anchors,
                 wake: threading.Event, stop: threading.Event) -> None:
    import cv2

    # max_side here only raises the engine's internal cap; the actual working
    # resolution is chosen per-pass below, scaled to the region (a fixed 960
    # turned 4K text into ~4px shards — fragmented boxes, missed lines,
    # pass-to-pass flashing; the accurate ~5pm build ran 1280).
    detector = TextDetector(max_side=1664)
    det_pass = [0]
    last_epoch = -1
    while not stop.is_set():
        t0 = time.monotonic()
        try:
            rect, fg, epoch = target.get()
            if epoch != last_epoch:
                last_epoch = epoch
                logger.info("[OCR] target region: %s (epoch %d)", rect, epoch)
            if rect is None or not fg:
                wake.wait(0.2)
                wake.clear()
                continue
            x1, y1, x2, y2 = rect
            frame = cap.last()[y1:y2, x1:x2]
            ch, cw = frame.shape[:2]
            longest = max(cw, ch)
            # Adaptive: ~0.42x of the region, floor 960, cap 1280 (the proven
            # ~5pm resolution; 1600 measured 1-3s/pass over 4K — too slow).
            target_side = min(1280, max(_DETECT_SIDE, int(longest * 0.42)))
            d_scale = min(1.0, target_side / float(longest))
            det_w, det_h = max(1, int(cw * d_scale)), max(1, int(ch * d_scale))
            det_bgr = cv2.resize(frame, (det_w, det_h), interpolation=cv2.INTER_AREA)
            # Blank regions covered by other windows so their text is never
            # even detected (they aren't VRChat content).
            for ox1, oy1, ox2, oy2 in target.occlusions():
                dx1 = max(0, int(ox1 * d_scale)); dy1 = max(0, int(oy1 * d_scale))
                dx2 = min(det_w, int(ox2 * d_scale)); dy2 = min(det_h, int(oy2 * d_scale))
                if dx2 > dx1 and dy2 > dy1:
                    det_bgr[dy1:dy2, dx1:dx2] = 0
            raw = detector.detect(det_bgr)
            shaped = [b for b in raw if _text_plausible(b, det_w, det_h)]
            # RECOGNITION GATE: actually READ each candidate; only regions
            # producing legible characters at decent confidence earn a box.
            # Crops come from the FULL-RES frame (not the downscaled detection
            # image) so the gate judges sharp pixels — at whole-screen scope a
            # 4K frame squeezed to 960px made every crop unreadable and the
            # gate rejected nearly all real text.
            if _REC_GATE_ENABLED:
                inv_d = 1.0 / d_scale if d_scale else 1.0
                crops, keep = [], []
                for b in shaped[:_REC_MAX_BOXES]:
                    fx1 = max(0, int(b.x1 * inv_d) - 3)
                    fy1 = max(0, int(b.y1 * inv_d) - 3)
                    fx2 = min(cw, int(b.x2 * inv_d) + 3)
                    fy2 = min(ch, int(b.y2 * inv_d) + 3)
                    if fx2 - fx1 < 12 or fy2 - fy1 < 8:
                        continue
                    crops.append(np.ascontiguousarray(frame[fy1:fy2, fx1:fx2]))
                    keep.append(b)
                verified = [
                    b for b, (text, score) in zip(keep, detector.recognize(crops))
                    if score >= _REC_MIN_SCORE and _REC_TEXTY.search(text)
                ]
            else:
                verified = shaped  # permissive: every text-shaped region
            det_pass[0] += 1
            if det_pass[0] % 10 == 1:
                logger.info("[OCR] det: raw=%d shaped=%d verified=%d region=%dx%d "
                            "det_side=%d pass_ms=%.0f",
                            len(raw), len(shaped), len(verified), cw, ch,
                            max(det_w, det_h),
                            (time.monotonic() - t0) * 1000)
            t_scale = min(1.0, _TRACK_SIDE / float(max(cw, ch)))
            track_w, track_h = max(1, int(cw * t_scale)), max(1, int(ch * t_scale))
            sx = track_w / float(det_w)
            sy = track_h / float(det_h)
            boxes = [
                TextBox(int(b.x1 * sx), int(b.y1 * sy), int(b.x2 * sx), int(b.y2 * sy))
                for b in verified
            ]
            gray = cv2.resize(cv2.extractChannel(frame, 1), (track_w, track_h),
                              interpolation=cv2.INTER_LINEAR)
            # The recognition gate adds ~0.3-0.6s inside this pass, so the
            # boxes describe an OLD frame; merging them dragged well-tracked
            # boxes off their text (the alignment regression). Re-advance the
            # boxes to the CURRENT frame before publishing so the tracker
            # always merges toward fresh positions.
            if boxes:
                now_crop = cap.last()[y1:y2, x1:x2]
                if now_crop.shape[:2] == (ch, cw):
                    now_gray = cv2.resize(
                        np.ascontiguousarray(now_crop[:, :, 1]),
                        (track_w, track_h), interpolation=cv2.INTER_LINEAR)
                    tmp = [_Tracked(b) for b in boxes]
                    adv, _g = _flow_all(gray, now_gray, tmp,
                                        _make_grid(track_w, track_h),
                                        win=25, levels=4)
                    fresh_boxes = []
                    for tb, (adx, ady, ar) in zip(tmp, adv):
                        if ar >= _MIN_OK_RATIO:
                            fresh_boxes.append(TextBox(
                                int(tb.x1 + adx), int(tb.y1 + ady),
                                int(tb.x2 + adx), int(tb.y2 + ady)))
                    boxes = fresh_boxes
                    gray = now_gray
            anchors.publish(boxes, gray, epoch)
        except Exception as exc:
            # WARNING, not debug: a broken detect loop looks like "boxes never
            # appear" — it must be visible in the log file.
            logger.warning("[OCR] detect error: %s", exc)
        remaining = max(0.0, _DETECT_INTERVAL - (time.monotonic() - t0))
        wake.wait(remaining)
        wake.clear()


class _Tracked:
    __slots__ = ("x1", "y1", "x2", "y2", "ax", "ay", "vx", "vy",
                 "moving", "calm_frames", "miss", "sig", "sig_bad", "tex_bad",
                 "last_confirm", "confirms")

    def __init__(self, b: TextBox) -> None:
        self.x1, self.y1 = float(b.x1), float(b.y1)
        self.x2, self.y2 = float(b.x2), float(b.y2)
        self.ax, self.ay = self.x1, self.y1
        self.vx = self.vy = 0.0
        self.moving = True
        self.calm_frames = 0
        self.miss = 0
        self.sig: np.ndarray | None = None
        self.sig_bad = 0
        self.tex_bad = 0
        self.last_confirm = time.monotonic()
        self.confirms = 1  # detections that have vouched for this box

    def advance(self, dx: float, dy: float, dt: float,
                gx: float, gy: float) -> None:
        self.x1 += dx; self.x2 += dx
        self.y1 += dy; self.y2 += dy
        if dt > 0:
            a = 0.55  # responsive velocity for the extrapolating renderer
            self.vx = (1 - a) * self.vx + a * (dx / dt)
            self.vy = (1 - a) * self.vy + a * (dy / dt)

        if self.moving:
            self.ax, self.ay = self.x1, self.y1
            step = ((dx - gx) ** 2 + (dy - gy) ** 2) ** 0.5
            if step < _STILL_SPEED_PX:
                self.calm_frames += 1
                if self.calm_frames >= _STILL_FRAMES:
                    self.moving = False
                    self.vx = self.vy = 0.0
                    # Keep the CREATION-time fingerprint: a box that drifted
                    # off its text during motion must fail against the real
                    # text baseline and die, not adopt whatever it landed on.
            else:
                self.calm_frames = 0
        else:
            self.ax += gx
            self.ay += gy
            off = ((self.x1 - self.ax) ** 2 + (self.y1 - self.ay) ** 2) ** 0.5
            if off > _UNFREEZE_PX:
                self.moving = True
                self.calm_frames = 0
                self.ax, self.ay = self.x1, self.y1
                self.sig_bad = 0
            else:
                w = self.x2 - self.x1
                h = self.y2 - self.y1
                self.x1 = self.ax + (self.x1 - self.ax) * (1 - _STILL_DECAY)
                self.y1 = self.ay + (self.y1 - self.ay) * (1 - _STILL_DECAY)
                self.x2 = self.x1 + w
                self.y2 = self.y1 + h

    def blend_toward(self, b: "_Tracked", k: float = _MERGE_BLEND) -> None:
        self.x1 += (b.x1 - self.x1) * k
        self.y1 += (b.y1 - self.y1) * k
        self.x2 += (b.x2 - self.x2) * k
        self.y2 += (b.y2 - self.y2) * k
        if not self.moving:
            self.ax, self.ay = self.x1, self.y1
        self.sig = None  # re-fingerprint at the corrected position
        self.sig_bad = 0
        self.last_confirm = time.monotonic()
        self.confirms = min(10, self.confirms + 1)

    def rect(self) -> tuple[float, float, float, float]:
        if not self.moving:
            w = self.x2 - self.x1
            h = self.y2 - self.y1
            return self.ax, self.ay, self.ax + w, self.ay + h
        return self.x1, self.y1, self.x2, self.y2

    def velocity(self) -> tuple[float, float]:
        if not self.moving:
            return 0.0, 0.0
        return self.vx, self.vy

    def _sample(self, gray: np.ndarray) -> np.ndarray:
        H, W = gray.shape[:2]
        x1, y1, x2, y2 = self.rect()
        xs = np.clip(np.linspace(x1 + 2, x2 - 2, _SIG_X).astype(np.int32), 0, W - 1)
        ys = np.clip(np.linspace(y1 + 1, y2 - 1, _SIG_Y).astype(np.int32), 0, H - 1)
        return gray[np.ix_(ys, xs)].astype(np.float32).ravel()

    def check_signature(self, gray: np.ndarray) -> bool:
        """Fingerprint the pixels under the box; returns False when the content
        has been gone for _SIG_BAD_FRAMES frames (drop the box)."""
        sample = self._sample(gray)
        if self.sig is None:
            self.sig = sample
            self.sig_bad = 0
            return True
        diff = float(np.mean(np.abs(sample - self.sig)))
        if diff > _SIG_DIFF:
            self.sig_bad += 1
            if self.sig_bad >= _SIG_BAD_FRAMES:
                return False
        else:
            self.sig_bad = 0
            self.sig = 0.9 * self.sig + 0.1 * sample
        return True

    def check_texture(self, gray: np.ndarray) -> bool:
        """Text has contrast. A box whose content is flat for a few frames is a
        ghost sitting on floor/wall — drop it (works even during motion)."""
        std = float(self._sample(gray).std())
        if std < _TEX_MIN_STD:
            self.tex_bad += 1
            if self.tex_bad >= _TEX_BAD_FRAMES:
                return False
        else:
            self.tex_bad = 0
        return True


def _iou(a, b) -> float:
    ix1, iy1 = max(a[0], b[0]), max(a[1], b[1])
    ix2, iy2 = min(a[2], b[2]), min(a[3], b[3])
    iw, ih = max(0.0, ix2 - ix1), max(0.0, iy2 - iy1)
    inter = iw * ih
    if inter <= 0:
        return 0.0
    area_a = (a[2] - a[0]) * (a[3] - a[1])
    area_b = (b[2] - b[0]) * (b[3] - b[1])
    return inter / max(1.0, area_a + area_b - inter)


def _make_grid(w: int, h: int) -> np.ndarray:
    xs = np.linspace(w * 0.08, w * 0.92, _GRID_X)
    ys = np.linspace(h * 0.10, h * 0.90, _GRID_Y)
    return np.array([[x, y] for y in ys for x in xs], dtype=np.float32)


def _flow_all(prev_g: np.ndarray, cur_g: np.ndarray, tracked: list["_Tracked"],
              grid: np.ndarray, win: int = 21, levels: int = 3,
              cursor: tuple[float, float] | None = None):
    import cv2

    H, W = cur_g.shape[:2]
    pts: list[list[float]] = [p.tolist() for p in grid]
    n_grid = len(pts)
    spans = []
    r2 = _CURSOR_RADIUS * _CURSOR_RADIUS
    for tr in tracked:
        gx = np.linspace(max(1.0, tr.x1 + 2), min(W - 2.0, tr.x2 - 2), _PTS_X)
        gy = np.linspace(max(1.0, tr.y1 + 2), min(H - 2.0, tr.y2 - 2), _PTS_Y)
        p = [[x, y] for y in gy for x in gx]
        if cursor is not None:
            cx, cy = cursor
            filtered = [q for q in p
                        if (q[0] - cx) ** 2 + (q[1] - cy) ** 2 > r2]
            if len(filtered) >= 4:
                p = filtered  # drop cursor-contaminated points
        spans.append((len(pts), len(pts) + len(p)))
        pts.extend(p)
    p0 = np.asarray(pts, dtype=np.float32).reshape(-1, 1, 2)
    try:
        p1, st, _ = cv2.calcOpticalFlowPyrLK(
            prev_g, cur_g, p0, None, winSize=(win, win), maxLevel=levels)
    except Exception:
        return [(0.0, 0.0, 0.0)] * len(tracked), (0.0, 0.0, 0.0)
    d = (p1 - p0).reshape(-1, 2)
    ok = st.reshape(-1) == 1

    g_ok = ok[:n_grid]
    if g_ok.sum() >= 4:
        gxy = np.median(d[:n_grid][g_ok], axis=0)
        g = (float(gxy[0]), float(gxy[1]), float(g_ok.sum()) / n_grid)
    else:
        g = (0.0, 0.0, float(g_ok.sum()) / max(1, n_grid))

    out: list[tuple[float, float, float]] = []
    for a, z in spans:
        good = ok[a:z]
        n = max(1, z - a)
        if good.sum() >= 2:
            dx, dy = np.median(d[a:z][good], axis=0)
            out.append((float(dx), float(dy), float(good.sum()) / n))
        else:
            out.append((0.0, 0.0, float(good.sum()) / n))
    return out, g


class _BoxState:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._items: list[tuple[float, float, float, float, float, float]] = []
        self._stamp = time.monotonic()
        self.version = 0

    def set(self, items) -> None:
        with self._lock:
            self._items = items
            self._stamp = time.monotonic()
            self.version += 1

    def get(self):
        with self._lock:
            return self.version, self._stamp, list(self._items)


def _track_loop(cap: _Capture, target: _Target, anchors: _Anchors,
                wake: threading.Event, state: _BoxState,
                stop: threading.Event) -> None:
    import cv2

    prev_g: np.ndarray | None = None
    tracked: list[_Tracked] = []
    last_stamp = 0
    last_t = time.monotonic()
    gx_ema = gy_ema = 0.0
    cur_epoch = -1
    hb_t = time.monotonic()
    hb_frames = 0
    away_since: float | None = None
    away_logged = False
    track_w = track_h = 0
    inv_scale = 1.0
    off_x = off_y = 0
    grid = _make_grid(8, 5)
    while not stop.is_set():
        try:
            # Heartbeat: with a windowless subprocess this log line is the only
            # way to see whether tracking is alive and at what rate.
            now_hb = time.monotonic()
            if now_hb - hb_t >= 5.0:
                logger.info("[OCR] heartbeat: %.0f fps, boxes=%d, fg=%s",
                            hb_frames / max(1e-6, now_hb - hb_t), len(tracked),
                            target.get()[1])
                hb_t, hb_frames = now_hb, 0
            rect, fg, epoch = target.get()
            if rect is None:
                if tracked or prev_g is not None:
                    tracked = []
                    prev_g = None
                    state.set([])
                time.sleep(0.05)
                continue
            # Focus-loss debounce: ignore sub-_FG_GRACE_S blips entirely (the
            # app's own overlay re-asserting topmost, OS transitions). Only a
            # sustained focus change hides the boxes; returning re-detects
            # immediately instead of waiting out the cycle.
            if not fg:
                if away_since is None:
                    away_since = time.monotonic()
                if time.monotonic() - away_since >= _FG_GRACE_S:
                    if not away_logged:
                        away_logged = True
                        logger.info("[OCR] hidden: focus lost >%.1fs to %r",
                                    _FG_GRACE_S, target.fg_title())
                    if tracked or prev_g is not None:
                        tracked = []
                        prev_g = None
                        state.set([])
                    time.sleep(0.05)
                    continue
                # inside grace: keep tracking straight through the blip
            else:
                if away_logged:
                    logger.info("[OCR] resumed: focus back on target")
                    # Discard any detection made before/during the hide — it
                    # describes the OLD view and briefly painted misplaced
                    # boxes on tab-back. A fresh scan is requested instead.
                    stale_fresh = anchors.take(last_stamp)
                    if stale_fresh is not None:
                        last_stamp = stale_fresh[0]
                    wake.set()  # repopulate without waiting for the next cycle
                away_since = None
                away_logged = False
            if epoch != cur_epoch:
                cur_epoch = epoch
                x1, y1, x2, y2 = rect
                cw, ch = x2 - x1, y2 - y1
                t_scale = min(1.0, _TRACK_SIDE / float(max(cw, ch)))
                track_w, track_h = max(1, int(cw * t_scale)), max(1, int(ch * t_scale))
                inv_scale = 1.0 / t_scale if t_scale else 1.0
                off_x, off_y = x1, y1
                grid = _make_grid(track_w, track_h)
                prev_g = None
                tracked = []
                state.set([])

            frame = cap.grab()
            if frame is None:
                time.sleep(0.001)
                continue
            hb_frames += 1
            now = time.monotonic()
            dt = min(0.1, max(1e-4, now - last_t))
            last_t = now
            x1, y1, x2, y2 = rect
            # Copy ONLY the green plane of the crop (8 MB at 4K, not the 24 MB
            # BGR copy — that hidden copy was costing real frame rate).
            crop_g = frame[y1:y2, x1:x2, 1]
            if crop_g.size == 0:
                time.sleep(0.01)
                continue
            cur_g = cv2.resize(np.ascontiguousarray(crop_g),
                               (track_w, track_h), interpolation=cv2.INTER_LINEAR)
            # Blank pixels covered by OTHER windows (chat apps, explorer, the
            # translator): they are not VRChat content and must not be scanned,
            # tracked, or boxed. Masked-out boxes die via the checks below.
            occl = target.occlusions()
            t_s = 1.0 / inv_scale if inv_scale else 1.0
            occl_wk = []
            for ox1, oy1, ox2, oy2 in occl:
                wx1 = max(0, int(ox1 * t_s)); wy1 = max(0, int(oy1 * t_s))
                wx2 = min(track_w, int(ox2 * t_s)); wy2 = min(track_h, int(oy2 * t_s))
                if wx2 > wx1 and wy2 > wy1:
                    cur_g[wy1:wy2, wx1:wx2] = 0
                    occl_wk.append((wx1, wy1, wx2, wy2))
            if prev_g is None or prev_g.shape != cur_g.shape:
                prev_g = cur_g
                continue

            # In-game cursor position in track coords (game draws its own
            # pointer into the frame; OS position matches in desktop mode).
            cursor_wk: tuple[float, float] | None = None
            try:
                cpt = wintypes.POINT()
                ctypes.windll.user32.GetCursorPos(ctypes.byref(cpt))
                scale = 1.0 / inv_scale if inv_scale else 1.0
                cursor_wk = ((cpt.x - cap.left - off_x) * scale,
                             (cpt.y - cap.top - off_y) * scale)
            except Exception:
                pass

            flows, (gx, gy, grid_ratio) = _flow_all(prev_g, cur_g, tracked, grid,
                                                    cursor=cursor_wk)
            gx_ema = 0.7 * gx_ema + 0.3 * gx
            gy_ema = 0.7 * gy_ema + 0.3 * gy
            camera_moving = (gx_ema ** 2 + gy_ema ** 2) ** 0.5 > _GLOBAL_MOVE_PX
            agx, agy = (gx, gy) if camera_moving else (0.0, 0.0)

            if tracked:
                diff = float(cv2.mean(cv2.absdiff(cur_g, prev_g))[0])
                if diff > _CUT_DIFF_HARD or (diff > _CUT_DIFF_SOFT
                                             and grid_ratio < _CUT_GRID_RATIO):
                    tracked = []
                    state.set([])
                    wake.set()
                    prev_g = cur_g
                    continue

            kept: list[_Tracked] = []
            for tr, (dx, dy, ratio) in zip(tracked, flows):
                if ratio < _MIN_OK_RATIO:
                    continue
                tr.advance(dx, dy, dt, agx, agy)
                # Content checks are suspended while the cursor touches the
                # box — the game-drawn pointer legitimately changes the pixels.
                cursor_on_box = False
                if cursor_wk is not None:
                    cx, cy = cursor_wk
                    m = _CURSOR_RADIUS
                    cursor_on_box = (tr.x1 - m <= cx <= tr.x2 + m
                                     and tr.y1 - m <= cy <= tr.y2 + m)
                # Texture check ALWAYS: a box on featureless content (floor,
                # wall) is a ghost regardless of motion — flow reports OK on
                # flat surfaces, so this is the only thing that catches them.
                if _is_cursor_box(tr.x1, tr.y1, tr.x2, tr.y2, cursor_wk):
                    continue  # tiny box riding the pointer — it IS the cursor
                bcx, bcy = (tr.x1 + tr.x2) / 2, (tr.y1 + tr.y2) / 2
                if any(ox1 <= bcx <= ox2 and oy1 <= bcy <= oy2
                       for ox1, oy1, ox2, oy2 in occl_wk):
                    continue  # box sits on another window's area — drop
                if (_TEXTURE_KILL_ENABLED and not cursor_on_box
                        and not tr.check_texture(cur_g)):
                    continue
                # Appearance check ONLY for frozen boxes under a still camera
                # (the menu-close case), and only when detection hasn't
                # re-confirmed the box recently — a repeatedly-confirmed box is
                # real text; sig-killing it caused an on/off flash loop.
                if (_SIG_KILL_ENABLED and not cursor_on_box
                        and not camera_moving and not tr.moving
                        and (now - tr.last_confirm) > _SIG_CONFIRM_GRACE_S
                        and not tr.check_signature(cur_g)):
                    continue
                kept.append(tr)
            tracked = kept

            fresh = anchors.take(last_stamp)
            if fresh is not None:
                last_stamp, det_boxes, det_gray, det_epoch = fresh
                usable = (det_epoch == cur_epoch and det_gray is not None
                          and det_gray.shape == cur_g.shape)
                if usable:
                    stale = float(cv2.mean(cv2.absdiff(cur_g, det_gray))[0])
                    usable = stale < _DET_STALE
                    if not usable:
                        # This pass is unusable (view moved on) — request a
                        # fresh one NOW so ghosts don't outlive the movement.
                        wake.set()
                if usable:
                    fresh_tracked: list[_Tracked] = []
                    if det_boxes:
                        cands = [_Tracked(b) for b in det_boxes]
                        adv, _g = _flow_all(det_gray, cur_g, cands, grid,
                                            win=25, levels=4)
                        for tr, (dx, dy, ratio) in zip(cands, adv):
                            if ratio >= _MIN_OK_RATIO:
                                tr.advance(dx, dy, dt, agx, agy)
                                if _is_cursor_box(tr.x1, tr.y1, tr.x2, tr.y2,
                                                  cursor_wk):
                                    continue  # never adopt the pointer glyph
                                ncx = (tr.x1 + tr.x2) / 2
                                ncy = (tr.y1 + tr.y2) / 2
                                if any(ox1 <= ncx <= ox2 and oy1 <= ncy <= oy2
                                       for ox1, oy1, ox2, oy2 in occl_wk):
                                    continue  # on another window's area
                                # Baseline fingerprint NOW, while the content
                                # under the box is verified text.
                                tr.check_signature(cur_g)
                                fresh_tracked.append(tr)
                    merged: list[_Tracked] = []
                    used = [False] * len(fresh_tracked)
                    for tr in tracked:
                        best, best_iou = -1, _MERGE_IOU
                        for i, nb in enumerate(fresh_tracked):
                            if used[i]:
                                continue
                            iou = _iou(tr.rect(), nb.rect())
                            if iou > best_iou:
                                best, best_iou = i, iou
                        if best >= 0:
                            used[best] = True
                            # Still camera => detections are exact: snap ~fully
                            # so alignment settles in 1-2 passes (was ~5 passes
                            # / 15s of visible creep). Softer during motion
                            # where detections carry staleness.
                            tr.blend_toward(fresh_tracked[best],
                                            0.55 if camera_moving else 0.95)
                            tr.miss = 0
                            merged.append(tr)
                        else:
                            tr.miss += 1
                            allowed = (_MAX_MISSES_CONFIRMED
                                       if tr.confirms >= _CONFIRMED_AT
                                       else _MAX_MISSES_UNPROVEN)
                            if tr.miss <= allowed:
                                merged.append(tr)
                    for i, nb in enumerate(fresh_tracked):
                        if not used[i]:
                            merged.append(nb)
                    tracked = merged

            prev_g = cur_g
            items = []
            for tr in tracked:
                bx1, by1, bx2, by2 = tr.rect()
                vx, vy = tr.velocity()
                items.append((
                    (bx1 * inv_scale + off_x) + cap.left,
                    (by1 * inv_scale + off_y) + cap.top,
                    (bx2 * inv_scale + off_x) + cap.left,
                    (by2 * inv_scale + off_y) + cap.top,
                    vx * inv_scale, vy * inv_scale))
            state.set(items)
        except Exception as exc:
            logger.debug("[OCR] track iteration error: %s", exc)
            time.sleep(0.01)


def _save_debug_shot(cap: _Capture, boxes) -> None:
    import cv2

    try:
        os.makedirs(_SHOT_DIR, exist_ok=True)
        frame = cap.last().copy()
        for it in boxes:
            bx1, by1, bx2, by2 = it[0], it[1], it[2], it[3]
            cv2.rectangle(frame,
                          (int(bx1 - cap.left), int(by1 - cap.top)),
                          (int(bx2 - cap.left), int(by2 - cap.top)),
                          (0, 0, 255), 2)
        path = os.path.join(_SHOT_DIR, time.strftime("shot_%H%M%S.png"))
        cv2.imwrite(path, frame)
        logger.info("[OCR] debug shot saved: %s", path)
    except Exception as exc:
        logger.debug("[OCR] debug shot failed: %s", exc)


_SHOT_TRIGGER = os.path.join(os.path.expanduser("~"), "AppData", "Local",
                             "puripuly-heart", "ocr_shot_trigger")


def _prtscn_loop(cap: _Capture, state: _BoxState, stop: threading.Event) -> None:
    """Composite triggers: the PrintScreen key (user's muscle memory) OR a
    trigger FILE — the file path lets tooling request a composite silently,
    without touching the keyboard (the user's ShareX intercepts PrtScn with a
    focus-stealing capture UI)."""
    was_down = False
    while not stop.is_set():
        try:
            down = bool(ctypes.windll.user32.GetAsyncKeyState(_VK_SNAPSHOT) & 0x8000)
            fire = down and not was_down
            was_down = down
            if not fire and os.path.exists(_SHOT_TRIGGER):
                with contextlib_suppress():
                    os.remove(_SHOT_TRIGGER)
                fire = True
            if fire:
                _v, _s, boxes = state.get()
                _save_debug_shot(cap, boxes)
        except Exception:
            pass
        time.sleep(0.03)


class contextlib_suppress:
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return True


def run(monitor_index: int = 1, fps: float = 0.0, max_side: int = _TRACK_SIDE,
        window_title: str | None = "VRChat") -> None:
    _set_dpi_aware()
    cap = _Capture()
    left, top, width, height = cap.left, cap.top, cap.width, cap.height

    target = _Target(window_title, cap)
    target.poll()

    root = tk.Tk()
    root.overrideredirect(True)
    root.attributes("-topmost", True)
    root.update_idletasks()
    tk_w, tk_h = root.winfo_screenwidth(), root.winfo_screenheight()
    sx = tk_w / float(width) if width else 1.0
    sy = tk_h / float(height) if height else 1.0
    win_w, win_h = int(round(width * sx)), int(round(height * sy))
    win_x, win_y = int(round(left * sx)), int(round(top * sy))
    root.geometry(f"{win_w}x{win_h}+{win_x}+{win_y}")
    root.configure(bg=_TRANSPARENT_KEY)
    try:
        root.attributes("-transparentcolor", _TRANSPARENT_KEY)
    except Exception:
        pass
    canvas = tk.Canvas(root, bg=_TRANSPARENT_KEY, highlightthickness=0,
                       width=win_w, height=win_h)
    canvas.pack(fill="both", expand=True)
    root.update_idletasks()
    _make_click_through(root)
    _exclude_from_capture(root)

    state = _BoxState()
    anchors = _Anchors()
    wake = threading.Event()
    stop = threading.Event()
    threading.Thread(target=_target_loop, args=(target, stop), daemon=True).start()
    threading.Thread(target=_detect_loop,
                     args=(cap, target, anchors, wake, stop), daemon=True).start()
    threading.Thread(target=_track_loop,
                     args=(cap, target, anchors, wake, state, stop),
                     daemon=True).start()
    threading.Thread(target=_prtscn_loop, args=(cap, state, stop),
                     daemon=True).start()

    pool: list[int] = []

    def _redraw() -> None:
        try:
            _version, stamp, items = state.get()
            age = time.monotonic() - stamp
            ext = min(age, _RENDER_EXTRAP_CAP_S) + _RENDER_LEAD_S
            while len(pool) < len(items):
                pool.append(canvas.create_rectangle(
                    0, 0, 0, 0, outline=_BOX_COLOR, width=_BOX_WIDTH,
                    state="hidden"))
            for i, item in enumerate(pool):
                if i < len(items):
                    bx1, by1, bx2, by2, vx, vy = items[i]
                    ex, ey = vx * ext, vy * ext
                    canvas.coords(item,
                                  (bx1 + ex - left) * sx, (by1 + ey - top) * sy,
                                  (bx2 + ex - left) * sx, (by2 + ey - top) * sy)
                    canvas.itemconfigure(item, state="normal")
                else:
                    canvas.itemconfigure(item, state="hidden")
        except Exception as exc:
            logger.debug("[OCR] redraw error: %s", exc)
        finally:
            root.after(4, _redraw)

    def _on_close() -> None:
        stop.set()
        root.destroy()

    root.protocol("WM_DELETE_WINDOW", _on_close)
    root.after(4, _redraw)
    try:
        root.mainloop()
    finally:
        stop.set()


def _parent_watch_loop(parent_pid: int) -> None:
    """Exit the INSTANT the parent app dies. Holds a HANDLE to the exact
    process object (immune to Windows PID reuse — polling by pid could latch
    onto an unrelated process that recycled the number) and blocks on it, so
    there is no polling window either. If the parent can't be opened at all,
    it is already gone — exit immediately."""
    SYNCHRONIZE = 0x00100000
    INFINITE = 0xFFFFFFFF
    kernel32 = ctypes.windll.kernel32
    try:
        handle = kernel32.OpenProcess(SYNCHRONIZE, False, parent_pid)
        if handle:
            kernel32.WaitForSingleObject(handle, INFINITE)  # returns on death
    except Exception:
        pass
    logger.info("[OCR] parent app (pid %s) exited — shutting down", parent_pid)
    os._exit(0)


_TAKEOVER_EVENT = "PuriPulyHeart_OCR_Takeover"
_INSTANCE_MUTEX = "PuriPulyHeart_OCR_Overlay"


def _takeover_listener(evt: int) -> None:
    """Exit when a NEWER overlay signals takeover — the fresh code wins."""
    INFINITE = 0xFFFFFFFF
    try:
        ctypes.windll.kernel32.WaitForSingleObject(evt, INFINITE)
        logger.info("[OCR] takeover signaled by a newer instance — exiting")
    except Exception:
        pass
    os._exit(0)


def _source_watch_loop() -> None:
    """Hot-swap on source change: when this file or detector.py is modified
    (a fix landed), spawn a replacement of ourselves with the same arguments
    and let its takeover handshake retire us. Removes the re-toggle burden
    that repeatedly left stale code running while new fixes sat unused."""
    import subprocess
    import sys as _sys

    watch = [__file__.rstrip("co"),
             os.path.join(os.path.dirname(__file__), "detector.py")]
    last = {}
    for p in watch:
        try:
            last[p] = os.path.getmtime(p)
        except Exception:
            last[p] = 0.0
    while True:
        time.sleep(2.0)
        changed = False
        for p in watch:
            try:
                m = os.path.getmtime(p)
            except Exception:
                continue
            if m != last[p]:
                last[p] = m
                changed = True
        if changed:
            time.sleep(1.5)  # let the write settle
            logger.info("[OCR] source changed — hot-swapping to new code")
            try:
                subprocess.Popen([_sys.executable, "-m",
                                  "puripuly_heart.ocr.overlay_proc",
                                  *_sys.argv[1:]],
                                 creationflags=0x08000000)
            except Exception as exc:
                logger.warning("[OCR] hot-swap spawn failed: %s", exc)
            # The new instance's takeover event will terminate us.


def _acquire_single_instance() -> bool:
    """One OCR overlay at a time (two DXGI capture sessions randomly kill each
    other) — but NEVER let a stale instance block a new one: the new overlay
    signals takeover, the old one exits, then the mutex is acquired. Without
    this, an orphan holding the mutex silently rejected every re-toggle and
    kept ancient code drawing on screen."""
    ERROR_ALREADY_EXISTS = 183
    try:
        kernel32 = ctypes.windll.kernel32
        # Manual-reset event: any old instance's listener wakes and exits.
        evt = kernel32.CreateEventW(None, True, False, _TAKEOVER_EVENT)
        kernel32.SetEvent(evt)
        time.sleep(0.7)  # give the old instance time to leave
        kernel32.ResetEvent(evt)
        for _ in range(10):
            kernel32.CreateMutexW(None, False, _INSTANCE_MUTEX)
            if kernel32.GetLastError() != ERROR_ALREADY_EXISTS:
                threading.Thread(target=_takeover_listener, args=(evt,),
                                 daemon=True).start()
                return True
            time.sleep(0.3)
        logger.warning("[OCR] could not take over from the running instance")
        return False
    except Exception:
        return True


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--monitor", type=int, default=1)
    ap.add_argument("--fps", type=float, default=0.0)
    ap.add_argument("--max-side", type=int, default=_TRACK_SIDE)
    ap.add_argument("--window", type=str, default="",
                    help="restrict to this window title (e.g. VRChat); empty = whole screen")
    ap.add_argument("--parent-pid", type=int, default=0,
                    help="exit when this process dies (no orphan overlays)")
    args = ap.parse_args()
    # Log to a file: the subprocess runs windowless, so stderr goes nowhere.
    log_path = os.path.join(os.path.expanduser("~"), "AppData", "Local",
                            "puripuly-heart", "ocr_overlay.log")
    handlers = [logging.StreamHandler()]
    try:
        os.makedirs(os.path.dirname(log_path), exist_ok=True)
        handlers.append(logging.FileHandler(log_path, mode="w", encoding="utf-8"))
    except Exception:
        pass
    logging.basicConfig(level=logging.INFO, handlers=handlers,
                        format="%(asctime)s %(levelname)s %(message)s")
    if not _acquire_single_instance():
        logger.warning("[OCR] another OCR overlay is already running — exiting")
        return
    if args.parent_pid:
        threading.Thread(target=_parent_watch_loop, args=(args.parent_pid,),
                         daemon=True).start()
    threading.Thread(target=_source_watch_loop, daemon=True).start()
    logger.info("[OCR] starting: window=%r parent=%s",
                args.window or None, args.parent_pid or "none")
    run(monitor_index=args.monitor, fps=args.fps, max_side=args.max_side,
        window_title=args.window or None)


if __name__ == "__main__":
    main()
