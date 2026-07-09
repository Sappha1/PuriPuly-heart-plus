"""OCR detection overlay — standalone subprocess (prototype).

Detect-then-track, tuned for TRUE per-frame updates on moving text:

  * DETECTION thread: full text detection a few times a second (slow path).
  * TRACKING loop: every delivered frame, ONE downscale of the new frame and
    ONE batched optical-flow call covering all boxes — cost is flat (~6 ms)
    no matter how many boxes are on screen, so tracking runs at the monitor's
    frame-delivery rate.
  * Boxes live in float coordinates with a per-box velocity estimate:
      - speed below a px/SECOND threshold  -> hard-frozen at an anchor
        (kills sub-pixel drift on static screens, cannot accumulate);
      - real motion -> float-precision movement every frame, plus a velocity
        lead of ~1 frame to cancel capture latency, so the outline rides ON
        the moving text instead of trailing it.
    A per-frame pixel deadzone is deliberately NOT used: at high fps real
    motion is <2 px/frame and a distance deadzone would clip it (the earlier
    "boxes float then snap" bug).

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

_WORK_SIDE = 1280         # working resolution for tracking + detection
_DETECT_INTERVAL = 0.35   # seconds between full detections
_PTS_X, _PTS_Y = 4, 3     # flow sample grid per box

# Stillness hysteresis in FULL-RES px/sec. Below LO long enough -> frozen at
# anchor; above HI -> moving. Walking-avatar bubbles are hundreds of px/sec.
_SPEED_STILL = 20.0
_SPEED_MOVE = 45.0
_STILL_FRAMES = 6         # consecutive slow frames before freezing
_LEAD_FRAMES = 1.2        # velocity lead, in frames, to cancel capture latency


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
        """Newest frame in BGR, or None if the screen hasn't changed (dxcam)."""
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


def _work_gray(frame: np.ndarray, work_w: int, work_h: int) -> np.ndarray:
    """Green channel downscaled to work resolution (one cheap resize/frame)."""
    import cv2

    g = cv2.extractChannel(frame, 1)
    return cv2.resize(g, (work_w, work_h), interpolation=cv2.INTER_LINEAR)


class _Anchors:
    """Latest detection (work coords + matching work gray) for the tracker."""

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


def _detect_loop(cap: _Capture, work_w: int, work_h: int,
                 anchors: _Anchors, stop: threading.Event) -> None:
    import cv2

    detector = TextDetector(max_side=max(work_w, work_h))
    while not stop.is_set():
        t0 = time.monotonic()
        try:
            frame = cap.last()
            work_bgr = cv2.resize(frame, (work_w, work_h),
                                  interpolation=cv2.INTER_AREA)
            boxes = detector.detect(work_bgr)  # work-res in, work coords out
            gray = cv2.extractChannel(work_bgr, 1)
            anchors.publish(boxes, gray)
        except Exception as exc:
            logger.debug("[OCR] detect error: %s", exc)
        stop.wait(max(0.0, _DETECT_INTERVAL - (time.monotonic() - t0)))


class _Tracked:
    """One box tracked in float work-coords with velocity + stillness state."""

    __slots__ = ("x1", "y1", "x2", "y2", "ax1", "ay1", "ax2", "ay2",
                 "vx", "vy", "slow_frames", "moving")

    def __init__(self, b: TextBox) -> None:
        self.x1, self.y1, self.x2, self.y2 = float(b.x1), float(b.y1), float(b.x2), float(b.y2)
        self.ax1, self.ay1, self.ax2, self.ay2 = self.x1, self.y1, self.x2, self.y2
        self.vx = self.vy = 0.0
        self.slow_frames = 0
        self.moving = True  # assume motion until proven still (never clip real motion)

    def advance(self, dx: float, dy: float, dt: float, inv_scale: float) -> None:
        self.x1 += dx; self.x2 += dx
        self.y1 += dy; self.y2 += dy
        if dt > 0:
            # EMA velocity in work px/sec.
            a = 0.35
            self.vx = (1 - a) * self.vx + a * (dx / dt)
            self.vy = (1 - a) * self.vy + a * (dy / dt)
        speed_full = ((self.vx ** 2 + self.vy ** 2) ** 0.5) * inv_scale
        if self.moving:
            if speed_full < _SPEED_STILL:
                self.slow_frames += 1
                if self.slow_frames >= _STILL_FRAMES:
                    self.moving = False
                    self.ax1, self.ay1 = self.x1, self.y1
                    self.ax2, self.ay2 = self.x2, self.y2
            else:
                self.slow_frames = 0
        else:
            if speed_full > _SPEED_MOVE:
                self.moving = True
                self.slow_frames = 0
            else:
                # Frozen: pin floats to the anchor so noise can't accumulate.
                self.x1, self.y1 = self.ax1, self.ay1
                self.x2, self.y2 = self.ax2, self.ay2
                self.vx *= 0.8
                self.vy *= 0.8

    def display(self, dt: float) -> tuple[float, float, float, float]:
        if not self.moving:
            return self.ax1, self.ay1, self.ax2, self.ay2
        lead = _LEAD_FRAMES * dt
        ex, ey = self.vx * lead, self.vy * lead
        return self.x1 + ex, self.y1 + ey, self.x2 + ex, self.y2 + ey


def _batched_flow(prev_g: np.ndarray, cur_g: np.ndarray,
                  tracked: list[_Tracked]) -> list[tuple[float, float]]:
    """One pyrLK call for every box's sample grid. Returns per-box (dx, dy)."""
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
        return [(0.0, 0.0)] * len(tracked)
    d = (p1 - p0).reshape(-1, 2)
    ok = st.reshape(-1) == 1
    out: list[tuple[float, float]] = []
    for a, z in spans:
        good = ok[a:z]
        if good.sum() >= 2:
            dx, dy = np.median(d[a:z][good], axis=0)
            out.append((float(dx), float(dy)))
        else:
            out.append((0.0, 0.0))
    return out


def _track_loop(cap: _Capture, work_w: int, work_h: int, inv_scale: float,
                anchors: _Anchors, state: "_BoxState",
                stop: threading.Event) -> None:
    prev_g: np.ndarray | None = None
    tracked: list[_Tracked] = []
    last_stamp = 0
    last_t = time.monotonic()
    while not stop.is_set():
        frame = cap.grab()
        if frame is None:
            time.sleep(0.001)  # screen unchanged
            continue
        now = time.monotonic()
        dt = min(0.1, max(1e-4, now - last_t))
        last_t = now
        cur_g = _work_gray(frame, work_w, work_h)

        fresh = anchors.take(last_stamp)
        if fresh is not None:
            last_stamp, det_boxes, det_gray = fresh
            new_tracked = [_Tracked(b) for b in det_boxes]
            # Advance the (slightly old) detections to the current frame.
            if det_gray is not None and new_tracked and det_gray.shape == cur_g.shape:
                for tr, (dx, dy) in zip(new_tracked,
                                        _batched_flow(det_gray, cur_g, new_tracked)):
                    tr.advance(dx, dy, dt, inv_scale)
            tracked = new_tracked
        elif prev_g is not None and tracked:
            for tr, (dx, dy) in zip(tracked, _batched_flow(prev_g, cur_g, tracked)):
                tr.advance(dx, dy, dt, inv_scale)

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


def run(monitor_index: int = 1, fps: float = 0.0, max_side: int = _WORK_SIDE) -> None:
    _set_dpi_aware()
    cap = _Capture()
    left, top, width, height = cap.left, cap.top, cap.width, cap.height

    scale = min(1.0, max_side / float(max(width, height)))
    work_w, work_h = max(1, int(width * scale)), max(1, int(height * scale))
    inv_scale = 1.0 / scale if scale else 1.0

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
    stop = threading.Event()
    threading.Thread(target=_detect_loop,
                     args=(cap, work_w, work_h, anchors, stop), daemon=True).start()
    threading.Thread(target=_track_loop,
                     args=(cap, work_w, work_h, inv_scale, anchors, state, stop),
                     daemon=True).start()

    # Reuse canvas rectangles (coords update) instead of delete/create — far
    # cheaper at high redraw rates and avoids flicker.
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
        root.after(5, _redraw)  # ~200 Hz poll; only repaints on change

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
    ap.add_argument("--max-side", type=int, default=_WORK_SIDE)
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO)
    run(monitor_index=args.monitor, fps=args.fps, max_side=args.max_side)


if __name__ == "__main__":
    main()
