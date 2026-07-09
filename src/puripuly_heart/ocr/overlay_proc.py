"""OCR detection overlay — standalone subprocess (prototype).

Detect-then-track, tuned for per-frame updates that stay honest about what is
actually on screen:

  * DETECTION thread (own, smaller resolution for a faster refresh) finds text
    boxes ~3x/sec, geometry-filtered to text-plausible shapes. Results whose
    source frame no longer resembles the current view are discarded (a mid-pan
    detection is garbage).
  * TRACKING loop: every delivered frame, ONE downscale + ONE batched
    optical-flow call for all boxes (~6 ms flat). Box positions are floats that
    ALWAYS follow the text; a position-hysteresis anchor decides only what is
    RENDERED — still boxes draw rock-solid at their anchor, and ~2 px of real
    accumulated motion unfreezes them with an instant catch-up (no trail, no
    lost tracking, max ~2 frames of onset delay).
  * SCENE-CUT GUARD: a cheap whole-frame difference metric detects fast camera
    turns; all boxes are cleared immediately (no ghost outlines over new
    scenery) and an immediate re-detect is requested.
  * Boxes whose tracking points go unhealthy (text left the region) are
    dropped rather than left floating.

Capture is dxcam (DXGI, ~1-5 ms at 4K) with an mss fallback. Detection only —
no translation.

Run directly:
    python -m puripuly_heart.ocr.overlay_proc
"""

from __future__ import annotations

import argparse
import ctypes
import logging
import threading
import time
import tkinter as tk

import numpy as np

from puripuly_heart.ocr.detector import TextBox, TextDetector

logger = logging.getLogger(__name__)

_TRANSPARENT_KEY = "#010203"
_BOX_COLOR = "#ff2020"
_BOX_WIDTH = 1

_TRACK_SIDE = 1280        # tracking working resolution (longest side)
_DETECT_SIDE = 960        # detection resolution (smaller => faster refresh)
_DETECT_INTERVAL = 0.30   # seconds between detections (detect itself ~0.25 s)
_PTS_X, _PTS_Y = 4, 3     # flow sample grid per box

# Rendering hysteresis (track px). Float position always follows the text;
# the DRAWN box stays at its anchor until offset exceeds _UNFREEZE_PX, then
# snaps to the float and follows every frame until calm again.
_UNFREEZE_PX = 2.0
_STILL_SPEED_PX = 0.35    # per-frame |d| below this counts as calm
_STILL_FRAMES = 8         # calm frames required to re-freeze
_STILL_DECAY = 0.12       # pull float toward anchor while frozen (kills bias drift)
_LEAD_FRAMES = 1.2        # velocity lead (frames) to cancel capture latency

# Scene-change thresholds (mean abs gray diff, 0-255).
_SCENE_CUT = 22.0         # whip pan / teleport: clear boxes instantly
_DET_STALE = 16.0         # detection older than the scene: discard result

# Flow health: minimum fraction of good sample points to keep a box alive.
_MIN_OK_RATIO = 0.34


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
    """Hide our own boxes from screen capture (feedback-loop guard)."""
    WDA_EXCLUDEFROMCAPTURE = 0x00000011
    try:
        hwnd = ctypes.windll.user32.GetParent(root.winfo_id())
        ctypes.windll.user32.SetWindowDisplayAffinity(hwnd, WDA_EXCLUDEFROMCAPTURE)
    except Exception as exc:
        logger.debug("[OCR] exclude-from-capture failed: %s", exc)


class _Capture:
    """Full-screen grabber. Prefers dxcam (DXGI); falls back to mss."""

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
            f = self._cam.grab()
            if f is not None:
                self._last = f
                return f
            return None
        f = np.asarray(self._sct.grab(self._mon))[:, :, :3]
        self._last = f
        return f

    def last(self) -> np.ndarray:
        return self._last


class _Anchors:
    """Latest detection (track coords + track-res gray) for the tracker."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.boxes: list[TextBox] = []
        self.gray: np.ndarray | None = None
        self.stamp = 0

    def publish(self, boxes: list[TextBox], gray: np.ndarray) -> None:
        with self._lock:
            self.boxes, self.gray, self.stamp = boxes, gray, self.stamp + 1

    def take(self, last_stamp: int):
        with self._lock:
            if self.stamp == last_stamp:
                return None
            return self.stamp, list(self.boxes), self.gray


def _text_plausible(b: TextBox, det_w: int, det_h: int) -> bool:
    """Geometry filter: keep boxes shaped like lines of text, drop speckle and
    screen-sized slabs (false positives on foliage/edges while running)."""
    w = b.x2 - b.x1
    h = b.y2 - b.y1
    if w < 10 or h < 5:
        return False
    if h > 0.12 * det_h:          # taller than any plausible text line
        return False
    if w > 0.92 * det_w:          # near full-screen slab
        return False
    if w * h < 110:               # speckle
        return False
    if w / max(1.0, float(h)) < 0.55:  # skinnier-than-tall column, not a line
        return False
    return True


def _detect_loop(cap: _Capture, det_w: int, det_h: int, track_w: int,
                 track_h: int, anchors: _Anchors, wake: threading.Event,
                 stop: threading.Event) -> None:
    import cv2

    detector = TextDetector(max_side=max(det_w, det_h))
    sx = track_w / float(det_w)
    sy = track_h / float(det_h)
    while not stop.is_set():
        t0 = time.monotonic()
        try:
            frame = cap.last()
            det_bgr = cv2.resize(frame, (det_w, det_h), interpolation=cv2.INTER_AREA)
            raw = detector.detect(det_bgr)  # det-res in, det coords out
            boxes = [
                TextBox(int(b.x1 * sx), int(b.y1 * sy), int(b.x2 * sx), int(b.y2 * sy))
                for b in raw if _text_plausible(b, det_w, det_h)
            ]
            # Gray at TRACK resolution from the same frame, so the tracker can
            # advance these boxes to "now" with one flow step.
            gray = cv2.resize(cv2.extractChannel(frame, 1), (track_w, track_h),
                              interpolation=cv2.INTER_LINEAR)
            anchors.publish(boxes, gray)
        except Exception as exc:
            logger.debug("[OCR] detect error: %s", exc)
        remaining = max(0.0, _DETECT_INTERVAL - (time.monotonic() - t0))
        wake.wait(remaining)   # early wake on scene cut
        wake.clear()


class _Tracked:
    """Float box that always follows the text; anchor decides what is drawn."""

    __slots__ = ("x1", "y1", "x2", "y2", "ax", "ay", "vx", "vy",
                 "moving", "calm_frames")

    def __init__(self, b: TextBox) -> None:
        self.x1, self.y1 = float(b.x1), float(b.y1)
        self.x2, self.y2 = float(b.x2), float(b.y2)
        self.ax, self.ay = self.x1, self.y1   # anchor (render position offset base)
        self.vx = self.vy = 0.0
        self.moving = True        # start permissive; settles to still if calm
        self.calm_frames = 0

    def advance(self, dx: float, dy: float, dt: float) -> None:
        # Float position ALWAYS follows the text — tracking never pauses.
        self.x1 += dx; self.x2 += dx
        self.y1 += dy; self.y2 += dy
        if dt > 0:
            a = 0.4
            self.vx = (1 - a) * self.vx + a * (dx / dt)
            self.vy = (1 - a) * self.vy + a * (dy / dt)

        step = (dx * dx + dy * dy) ** 0.5
        if self.moving:
            # Anchor follows while moving; calm streak re-freezes.
            self.ax, self.ay = self.x1, self.y1
            if step < _STILL_SPEED_PX:
                self.calm_frames += 1
                if self.calm_frames >= _STILL_FRAMES:
                    self.moving = False
                    self.vx = self.vy = 0.0
            else:
                self.calm_frames = 0
        else:
            off = ((self.x1 - self.ax) ** 2 + (self.y1 - self.ay) ** 2) ** 0.5
            if off > _UNFREEZE_PX:
                # Real accumulated motion: unfreeze and catch up instantly.
                self.moving = True
                self.calm_frames = 0
                self.ax, self.ay = self.x1, self.y1
            else:
                # Frozen render; bleed float back toward anchor so sub-pixel
                # bias can never accumulate into a crawl.
                self.x1 = self.ax + (self.x1 - self.ax) * (1 - _STILL_DECAY)
                self.y1 = self.ay + (self.y1 - self.ay) * (1 - _STILL_DECAY)
                self.x2 = self.x1 + (self.x2 - self.x1)
                self.y2 = self.y1 + (self.y2 - self.y1)

    def display(self, dt: float) -> tuple[float, float, float, float]:
        w = self.x2 - self.x1
        h = self.y2 - self.y1
        if not self.moving:
            return self.ax, self.ay, self.ax + w, self.ay + h
        lead = _LEAD_FRAMES * dt
        ex, ey = self.vx * lead, self.vy * lead
        return self.x1 + ex, self.y1 + ey, self.x2 + ex, self.y2 + ey


def _batched_flow(prev_g: np.ndarray, cur_g: np.ndarray,
                  tracked: list[_Tracked]) -> list[tuple[float, float, float]]:
    """One pyrLK call for all boxes. Returns per-box (dx, dy, ok_ratio)."""
    import cv2

    if not tracked:
        return []
    H, W = cur_g.shape[:2]
    pts, spans = [], []
    for tr in tracked:
        gx = np.linspace(max(1.0, tr.x1 + 2), min(W - 2.0, tr.x2 - 2), _PTS_X)
        gy = np.linspace(max(1.0, tr.y1 + 2), min(H - 2.0, tr.y2 - 2), _PTS_Y)
        p = [[x, y] for y in gy for x in gx]
        spans.append((len(pts), len(pts) + len(p)))
        pts.extend(p)
    p0 = np.asarray(pts, dtype=np.float32).reshape(-1, 1, 2)
    try:
        p1, st, _ = cv2.calcOpticalFlowPyrLK(prev_g, cur_g, p0, None,
                                             winSize=(21, 21), maxLevel=3)
    except Exception:
        return [(0.0, 0.0, 0.0)] * len(tracked)
    d = (p1 - p0).reshape(-1, 2)
    ok = st.reshape(-1) == 1
    out: list[tuple[float, float, float]] = []
    for a, z in spans:
        good = ok[a:z]
        n = max(1, z - a)
        ratio = float(good.sum()) / n
        if good.sum() >= 2:
            dx, dy = np.median(d[a:z][good], axis=0)
            out.append((float(dx), float(dy), ratio))
        else:
            out.append((0.0, 0.0, ratio))
    return out


def _track_loop(cap: _Capture, track_w: int, track_h: int, inv_scale: float,
                anchors: _Anchors, wake: threading.Event, state: "_BoxState",
                stop: threading.Event) -> None:
    import cv2

    prev_g: np.ndarray | None = None
    tracked: list[_Tracked] = []
    last_stamp = 0
    last_t = time.monotonic()
    while not stop.is_set():
        frame = cap.grab()
        if frame is None:
            time.sleep(0.001)
            continue
        now = time.monotonic()
        dt = min(0.1, max(1e-4, now - last_t))
        last_t = now
        cur_g = cv2.resize(cv2.extractChannel(frame, 1), (track_w, track_h),
                           interpolation=cv2.INTER_LINEAR)

        # Scene-cut guard: a whip pan/teleport invalidates every box NOW.
        if prev_g is not None and tracked:
            diff = float(cv2.mean(cv2.absdiff(cur_g, prev_g))[0])
            if diff > _SCENE_CUT:
                tracked = []
                state.set([])
                wake.set()           # ask for an immediate re-detect
                prev_g = cur_g
                continue

        fresh = anchors.take(last_stamp)
        if fresh is not None:
            last_stamp, det_boxes, det_gray = fresh
            usable = det_gray is not None and det_gray.shape == cur_g.shape
            if usable:
                # Discard detections whose source frame no longer matches the
                # view (started before a pan finished — boxes would be wrong).
                stale = float(cv2.mean(cv2.absdiff(cur_g, det_gray))[0])
                usable = stale < _DET_STALE
            if usable:
                new_tracked = [_Tracked(b) for b in det_boxes]
                if new_tracked:
                    flows = _batched_flow(det_gray, cur_g, new_tracked)
                    kept = []
                    for tr, (dx, dy, ratio) in zip(new_tracked, flows):
                        if ratio >= _MIN_OK_RATIO:
                            tr.advance(dx, dy, dt)
                            kept.append(tr)
                    tracked = kept
                else:
                    tracked = []
        elif prev_g is not None and tracked:
            flows = _batched_flow(prev_g, cur_g, tracked)
            kept = []
            for tr, (dx, dy, ratio) in zip(tracked, flows):
                if ratio < _MIN_OK_RATIO:
                    continue          # text left the region — drop, don't float
                tr.advance(dx, dy, dt)
                kept.append(tr)
            tracked = kept

        prev_g = cur_g
        disp = []
        for tr in tracked:
            x1, y1, x2, y2 = tr.display(dt)
            disp.append((x1 * inv_scale + cap.left, y1 * inv_scale + cap.top,
                         x2 * inv_scale + cap.left, y2 * inv_scale + cap.top))
        state.set(disp)


class _BoxState:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._boxes: list[tuple[float, float, float, float]] = []
        self.version = 0

    def set(self, boxes: list[tuple[float, float, float, float]]) -> None:
        with self._lock:
            self._boxes = boxes
            self.version += 1

    def get(self) -> tuple[int, list[tuple[float, float, float, float]]]:
        with self._lock:
            return self.version, list(self._boxes)


def run(monitor_index: int = 1, fps: float = 0.0, max_side: int = _TRACK_SIDE) -> None:
    _set_dpi_aware()
    cap = _Capture()
    left, top, width, height = cap.left, cap.top, cap.width, cap.height

    t_scale = min(1.0, max_side / float(max(width, height)))
    track_w, track_h = max(1, int(width * t_scale)), max(1, int(height * t_scale))
    inv_scale = 1.0 / t_scale if t_scale else 1.0
    d_scale = min(1.0, _DETECT_SIDE / float(max(width, height)))
    det_w, det_h = max(1, int(width * d_scale)), max(1, int(height * d_scale))

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
    threading.Thread(target=_detect_loop,
                     args=(cap, det_w, det_h, track_w, track_h, anchors, wake, stop),
                     daemon=True).start()
    threading.Thread(target=_track_loop,
                     args=(cap, track_w, track_h, inv_scale, anchors, wake, state, stop),
                     daemon=True).start()

    pool: list[int] = []
    last_version = -1

    def _redraw() -> None:
        nonlocal last_version
        version, boxes = state.get()
        if version != last_version:
            last_version = version
            while len(pool) < len(boxes):
                pool.append(canvas.create_rectangle(
                    0, 0, 0, 0, outline=_BOX_COLOR, width=_BOX_WIDTH,
                    state="hidden"))
            for i, item in enumerate(pool):
                if i < len(boxes):
                    x1, y1, x2, y2 = boxes[i]
                    canvas.coords(item, (x1 - left) * sx, (y1 - top) * sy,
                                  (x2 - left) * sx, (y2 - top) * sy)
                    canvas.itemconfigure(item, state="normal")
                else:
                    canvas.itemconfigure(item, state="hidden")
        root.after(5, _redraw)

    def _on_close() -> None:
        stop.set()
        root.destroy()

    root.protocol("WM_DELETE_WINDOW", _on_close)
    root.after(5, _redraw)
    try:
        root.mainloop()
    finally:
        stop.set()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--monitor", type=int, default=1)
    ap.add_argument("--fps", type=float, default=0.0)  # tracks at frame rate
    ap.add_argument("--max-side", type=int, default=_TRACK_SIDE)
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO)
    run(monitor_index=args.monitor, fps=args.fps, max_side=args.max_side)


if __name__ == "__main__":
    main()
