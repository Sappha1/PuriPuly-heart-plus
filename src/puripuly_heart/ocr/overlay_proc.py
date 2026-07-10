"""OCR detection overlay — standalone subprocess (prototype).

Detect-then-track with global camera-motion compensation and an extrapolating
renderer, aimed at perceptual 1:1 realtime:

  * DETECTION thread (960px, ~0.25 s) finds text boxes ~3x/sec.
  * TRACKING loop: every delivered frame, ONE downscale + ONE batched
    optical-flow call covering a whole-frame grid (camera motion) and every
    box. Camera motion moves frozen boxes' anchors too (world-locked text
    glides with any pan speed); freezing only suppresses per-box residual
    noise. Detection refreshes SOFT-MERGE into existing boxes (blended
    correction, one-miss tolerance) instead of snapping/flickering.
  * RENDERER (~250 Hz): extrapolates each box along its measured velocity
    for the time since its last tracker update plus a small lead — motion is
    continuous between capture frames (which arrive only at monitor refresh;
    nothing can SEE faster than the screen updates) and sits on the text
    rather than trailing it. Frozen boxes have zero velocity: rock solid.
  * Scene-cut guard only fires on true discontinuities.
  * PrintScreen saves a composited PNG (frame + boxes) to
    Desktop/puripuly_ocr_shots/ — polled in a dedicated thread so it can
    NEVER take down the render loop.

Run directly:
    python -m puripuly_heart.ocr.overlay_proc
"""

from __future__ import annotations

import argparse
import ctypes
import logging
import os
import threading
import time
import tkinter as tk

import numpy as np

from puripuly_heart.ocr.detector import TextBox, TextDetector

logger = logging.getLogger(__name__)

_TRANSPARENT_KEY = "#010203"
_BOX_COLOR = "#ff2020"
_BOX_WIDTH = 1

_TRACK_SIDE = 1280
_DETECT_SIDE = 960
_DETECT_INTERVAL = 0.30
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

# Soft-merge of detection refreshes into live boxes.
_MERGE_IOU = 0.35
_MERGE_BLEND = 0.45       # fraction pulled toward the fresh detection
_MAX_MISSES = 1           # detection passes a live box may miss before dropping

# Renderer extrapolation: time since last tracker update + this lead.
_RENDER_LEAD_S = 0.010
_RENDER_EXTRAP_CAP_S = 0.06

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
    w = b.x2 - b.x1
    h = b.y2 - b.y1
    if w < 10 or h < 5:
        return False
    if h > 0.12 * det_h:
        return False
    if w > 0.92 * det_w:
        return False
    if w * h < 110:
        return False
    if w / max(1.0, float(h)) < 0.55:
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
            raw = detector.detect(det_bgr)
            boxes = [
                TextBox(int(b.x1 * sx), int(b.y1 * sy), int(b.x2 * sx), int(b.y2 * sy))
                for b in raw if _text_plausible(b, det_w, det_h)
            ]
            gray = cv2.resize(cv2.extractChannel(frame, 1), (track_w, track_h),
                              interpolation=cv2.INTER_LINEAR)
            anchors.publish(boxes, gray)
        except Exception as exc:
            logger.debug("[OCR] detect error: %s", exc)
        remaining = max(0.0, _DETECT_INTERVAL - (time.monotonic() - t0))
        wake.wait(remaining)
        wake.clear()


class _Tracked:
    __slots__ = ("x1", "y1", "x2", "y2", "ax", "ay", "vx", "vy",
                 "moving", "calm_frames", "miss")

    def __init__(self, b: TextBox) -> None:
        self.x1, self.y1 = float(b.x1), float(b.y1)
        self.x2, self.y2 = float(b.x2), float(b.y2)
        self.ax, self.ay = self.x1, self.y1
        self.vx = self.vy = 0.0
        self.moving = True
        self.calm_frames = 0
        self.miss = 0

    def advance(self, dx: float, dy: float, dt: float,
                gx: float, gy: float) -> None:
        self.x1 += dx; self.x2 += dx
        self.y1 += dy; self.y2 += dy
        if dt > 0:
            a = 0.4
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
            else:
                w = self.x2 - self.x1
                h = self.y2 - self.y1
                self.x1 = self.ax + (self.x1 - self.ax) * (1 - _STILL_DECAY)
                self.y1 = self.ay + (self.y1 - self.ay) * (1 - _STILL_DECAY)
                self.x2 = self.x1 + w
                self.y2 = self.y1 + h

    def blend_toward(self, b: "_Tracked") -> None:
        """Soft-merge a fresh detection: pull position/size partway instead of
        snapping. Keeps identity, velocity and freeze state."""
        k = _MERGE_BLEND
        self.x1 += (b.x1 - self.x1) * k
        self.y1 += (b.y1 - self.y1) * k
        self.x2 += (b.x2 - self.x2) * k
        self.y2 += (b.y2 - self.y2) * k
        if not self.moving:
            # Correct the anchor too, so a frozen box absorbs the fix.
            self.ax, self.ay = self.x1, self.y1

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


def _iou(a: tuple[float, float, float, float],
         b: tuple[float, float, float, float]) -> float:
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
              grid: np.ndarray, win: int = 21, levels: int = 3):
    import cv2

    H, W = cur_g.shape[:2]
    pts: list[list[float]] = [p.tolist() for p in grid]
    n_grid = len(pts)
    spans = []
    for tr in tracked:
        gx = np.linspace(max(1.0, tr.x1 + 2), min(W - 2.0, tr.x2 - 2), _PTS_X)
        gy = np.linspace(max(1.0, tr.y1 + 2), min(H - 2.0, tr.y2 - 2), _PTS_Y)
        p = [[x, y] for y in gy for x in gx]
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
    """Rendered boxes: screen-coord rects + velocities (px/s) + a timestamp,
    so the renderer can extrapolate between tracker updates."""

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


def _track_loop(cap: _Capture, track_w: int, track_h: int, inv_scale: float,
                anchors: _Anchors, wake: threading.Event, state: _BoxState,
                stop: threading.Event) -> None:
    import cv2

    grid = _make_grid(track_w, track_h)
    prev_g: np.ndarray | None = None
    tracked: list[_Tracked] = []
    last_stamp = 0
    last_t = time.monotonic()
    gx_ema = gy_ema = 0.0
    while not stop.is_set():
        try:
            frame = cap.grab()
            if frame is None:
                time.sleep(0.001)
                continue
            now = time.monotonic()
            dt = min(0.1, max(1e-4, now - last_t))
            last_t = now
            cur_g = cv2.resize(cv2.extractChannel(frame, 1), (track_w, track_h),
                               interpolation=cv2.INTER_LINEAR)
            if prev_g is None:
                prev_g = cur_g
                continue

            flows, (gx, gy, grid_ratio) = _flow_all(prev_g, cur_g, tracked, grid)
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
                kept.append(tr)
            tracked = kept

            fresh = anchors.take(last_stamp)
            if fresh is not None:
                last_stamp, det_boxes, det_gray = fresh
                usable = det_gray is not None and det_gray.shape == cur_g.shape
                if usable:
                    stale = float(cv2.mean(cv2.absdiff(cur_g, det_gray))[0])
                    usable = stale < _DET_STALE
                if usable:
                    fresh_tracked: list[_Tracked] = []
                    if det_boxes:
                        cands = [_Tracked(b) for b in det_boxes]
                        adv, _g = _flow_all(det_gray, cur_g, cands, grid,
                                            win=25, levels=4)
                        for tr, (dx, dy, ratio) in zip(cands, adv):
                            if ratio >= _MIN_OK_RATIO:
                                tr.advance(dx, dy, dt, agx, agy)
                                fresh_tracked.append(tr)
                    # SOFT-MERGE: blend matches into live boxes (no snapping),
                    # adopt genuinely new ones, tolerate one missed pass.
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
                            tr.blend_toward(fresh_tracked[best])
                            tr.miss = 0
                            merged.append(tr)
                        else:
                            tr.miss += 1
                            if tr.miss <= _MAX_MISSES:
                                merged.append(tr)
                    for i, nb in enumerate(fresh_tracked):
                        if not used[i]:
                            merged.append(nb)
                    tracked = merged

            prev_g = cur_g
            items = []
            for tr in tracked:
                x1, y1, x2, y2 = tr.rect()
                vx, vy = tr.velocity()
                items.append((x1 * inv_scale + cap.left, y1 * inv_scale + cap.top,
                              x2 * inv_scale + cap.left, y2 * inv_scale + cap.top,
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
            x1, y1, x2, y2 = it[0], it[1], it[2], it[3]
            cv2.rectangle(frame,
                          (int(x1 - cap.left), int(y1 - cap.top)),
                          (int(x2 - cap.left), int(y2 - cap.top)),
                          (0, 0, 255), 2)
        path = os.path.join(_SHOT_DIR, time.strftime("shot_%H%M%S.png"))
        cv2.imwrite(path, frame)
        logger.info("[OCR] debug shot saved: %s", path)
    except Exception as exc:
        logger.debug("[OCR] debug shot failed: %s", exc)


def _prtscn_loop(cap: _Capture, state: _BoxState, stop: threading.Event) -> None:
    """PrintScreen poller in its own thread — completely isolated from the Tk
    render loop so a screenshot can never take the overlay down."""
    was_down = False
    while not stop.is_set():
        try:
            down = bool(ctypes.windll.user32.GetAsyncKeyState(_VK_SNAPSHOT) & 0x8000)
            if down and not was_down:
                _v, _s, boxes = state.get()
                _save_debug_shot(cap, boxes)
            was_down = down
        except Exception:
            pass
        time.sleep(0.03)


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
    threading.Thread(target=_prtscn_loop, args=(cap, state, stop),
                     daemon=True).start()

    pool: list[int] = []

    def _redraw() -> None:
        # NOTHING may escape this callback: an uncaught exception would break
        # the after() chain and freeze the overlay (the PrintScreen incident).
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
                    x1, y1, x2, y2, vx, vy = items[i]
                    ex, ey = vx * ext, vy * ext
                    canvas.coords(item,
                                  (x1 + ex - left) * sx, (y1 + ey - top) * sy,
                                  (x2 + ex - left) * sx, (y2 + ey - top) * sy)
                    canvas.itemconfigure(item, state="normal")
                else:
                    canvas.itemconfigure(item, state="hidden")
        except Exception as exc:
            logger.debug("[OCR] redraw error: %s", exc)
        finally:
            root.after(4, _redraw)  # ~250 Hz; extrapolation keeps motion smooth

    def _on_close() -> None:
        stop.set()
        root.destroy()

    root.protocol("WM_DELETE_WINDOW", _on_close)
    root.after(4, _redraw)
    try:
        root.mainloop()
    finally:
        stop.set()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--monitor", type=int, default=1)
    ap.add_argument("--fps", type=float, default=0.0)
    ap.add_argument("--max-side", type=int, default=_TRACK_SIDE)
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO)
    run(monitor_index=args.monitor, fps=args.fps, max_side=args.max_side)


if __name__ == "__main__":
    main()
