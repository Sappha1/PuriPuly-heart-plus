"""OCR detection overlay — standalone subprocess (prototype).

Detect-then-track architecture so boxes stay glued to moving text with no
perceptible delay:
  * a background DETECTION thread scans the screen a few times a second to find
    and refresh text boxes (slow);
  * a fast TRACKING loop moves each existing box every frame via optical flow,
    following the pixels it sits on (cheap), so walking avatars' bubbles keep
    their outline in real time.

Capture uses dxcam (DXGI desktop duplication, ~5 ms for 4K) with an mss
fallback. Detection only — no translation.

Run directly:
    python -m puripuly_heart.ocr.overlay_proc --fps 30
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
_WORK_MAX = 1280          # tracking/detection working resolution (longest side)
_DETECT_INTERVAL = 0.4    # seconds between full detections


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
    """Hide our own boxes from screen capture so the detector never sees them
    (would otherwise feed back and make boxes crawl). Windows 10 2004+."""
    WDA_EXCLUDEFROMCAPTURE = 0x00000011
    try:
        hwnd = ctypes.windll.user32.GetParent(root.winfo_id())
        ctypes.windll.user32.SetWindowDisplayAffinity(hwnd, WDA_EXCLUDEFROMCAPTURE)
    except Exception as exc:
        logger.debug("[OCR] exclude-from-capture failed: %s", exc)


# ── Capture backends ────────────────────────────────────────────────────────

class _Capture:
    """Full-screen grabber. Prefers dxcam (DXGI, ~5 ms); falls back to mss."""

    def __init__(self) -> None:
        self._cam = None
        self._sct = None
        self._mon = None
        self.width = 0
        self.height = 0
        self.left = 0
        self.top = 0
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
            logger.info("[OCR] capture backend: dxcam %dx%d", self.width, self.height)
        except Exception as exc:
            logger.warning("[OCR] dxcam unavailable (%s); using mss", exc)
            import mss

            self._sct = mss.mss()
            self._mon = self._sct.monitors[1]
            self.width = self._mon["width"]
            self.height = self._mon["height"]
            self.left = self._mon["left"]
            self.top = self._mon["top"]
            self._last = np.asarray(self._sct.grab(self._mon))[:, :, :3]

    def grab(self) -> np.ndarray | None:
        """Latest frame in BGR, or None if nothing changed (dxcam only)."""
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


def _to_work_gray(bgr: np.ndarray, work_max: int) -> tuple[np.ndarray, float]:
    """Single-channel, downscaled gray for optical flow. Uses the green channel
    as a luminance proxy (skips a full cvtColor). Returns (gray, scale) where
    scale maps original -> work pixels."""
    import cv2

    h, w = bgr.shape[:2]
    scale = work_max / float(max(h, w)) if max(h, w) > work_max else 1.0
    g = bgr[:, :, 1]  # green plane view — cheap luminance proxy
    if scale < 1.0:
        g = cv2.resize(g, (max(1, int(w * scale)), max(1, int(h * scale))),
                       interpolation=cv2.INTER_AREA)
    return np.ascontiguousarray(g), scale


# ── Detection thread: finds/refreshes boxes (in WORK coords) ────────────────

class _Anchors:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.boxes: list[TextBox] = []
        self.gray: np.ndarray | None = None
        self.stamp = 0

    def publish(self, boxes: list[TextBox], gray: np.ndarray) -> None:
        with self._lock:
            self.boxes = boxes
            self.gray = gray
            self.stamp += 1

    def take(self, last_stamp: int):
        with self._lock:
            if self.stamp == last_stamp:
                return None
            return self.stamp, list(self.boxes), self.gray


def _detect_loop(cap: _Capture, work_max: int, anchors: _Anchors,
                 stop: threading.Event) -> None:
    detector = TextDetector(max_side=work_max)
    while not stop.is_set():
        t0 = time.monotonic()
        try:
            frame = cap.last()
            gray, scale = _to_work_gray(frame, work_max)
            # Detect on the work-resolution BGR (build it once from the frame).
            import cv2
            h, w = frame.shape[:2]
            if scale < 1.0:
                work_bgr = cv2.resize(frame, (int(w * scale), int(h * scale)),
                                      interpolation=cv2.INTER_AREA)
            else:
                work_bgr = frame
            boxes = detector.detect(work_bgr)  # already work-res, no re-scale
            anchors.publish(boxes, gray)
        except Exception as exc:
            logger.debug("[OCR] detect error: %s", exc)
        stop.wait(max(0.0, _DETECT_INTERVAL - (time.monotonic() - t0)))


# ── Tracking: move boxes by optical flow every frame ────────────────────────

def _flow_boxes(prev_gray: np.ndarray, cur_gray: np.ndarray,
                boxes: list[TextBox]) -> list[TextBox]:
    """Translate each box by the median optical-flow of sample points inside it."""
    if not boxes:
        return boxes
    import cv2

    pts = []
    spans = []
    for b in boxes:
        gx = np.linspace(b.x1 + 2, b.x2 - 2, 4)
        gy = np.linspace(b.y1 + 2, b.y2 - 2, 3)
        p = np.array([[x, y] for y in gy for x in gx], dtype=np.float32)
        spans.append((len(pts), len(pts) + len(p)))
        pts.extend(p.tolist())
    if not pts:
        return boxes
    p0 = np.array(pts, dtype=np.float32).reshape(-1, 1, 2)
    p1, st, _ = cv2.calcOpticalFlowPyrLK(prev_gray, cur_gray, p0, None,
                                         winSize=(21, 21), maxLevel=2)
    out: list[TextBox] = []
    for b, (a, z) in zip(boxes, spans):
        d = (p1[a:z] - p0[a:z]).reshape(-1, 2)
        good = st[a:z].reshape(-1) == 1
        if good.sum() >= 2:
            dx, dy = np.median(d[good], axis=0)
        else:
            dx = dy = 0.0
        out.append(TextBox(int(b.x1 + dx), int(b.y1 + dy),
                           int(b.x2 + dx), int(b.y2 + dy)))
    return out


def _track_loop(cap: _Capture, work_max: int, anchors: _Anchors,
                state: "_BoxState", stop: threading.Event) -> None:
    tracked: list[TextBox] = []
    prev_gray: np.ndarray | None = None
    last_stamp = 0
    while not stop.is_set():
        frame = cap.grab()
        if frame is None:
            time.sleep(0.004)  # nothing changed on screen
            continue
        cur_gray, scale = _to_work_gray(frame, work_max)

        fresh = anchors.take(last_stamp)
        if fresh is not None:
            last_stamp, det_boxes, det_gray = fresh
            # Advance the fresh (slightly old) boxes to NOW via one flow step.
            if det_gray is not None and prev_gray is not None and det_boxes:
                tracked = _flow_boxes(det_gray, cur_gray, det_boxes)
            else:
                tracked = det_boxes
        elif prev_gray is not None and tracked:
            tracked = _flow_boxes(prev_gray, cur_gray, tracked)

        prev_gray = cur_gray
        # WORK coords -> screen coords.
        inv = 1.0 / scale if scale else 1.0
        disp = [TextBox(int(b.x1 * inv) + cap.left, int(b.y1 * inv) + cap.top,
                        int(b.x2 * inv) + cap.left, int(b.y2 * inv) + cap.top)
                for b in tracked]
        state.set(disp)


class _BoxState:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._boxes: list[TextBox] = []

    def set(self, boxes: list[TextBox]) -> None:
        with self._lock:
            self._boxes = boxes

    def get(self) -> list[TextBox]:
        with self._lock:
            return list(self._boxes)


def run(monitor_index: int = 1, fps: float = 30.0, max_side: int = _WORK_MAX) -> None:
    _set_dpi_aware()
    cap = _Capture()
    left, top, width, height = cap.left, cap.top, cap.width, cap.height

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
    threading.Thread(target=_detect_loop, args=(cap, max_side, anchors, stop),
                     daemon=True).start()
    threading.Thread(target=_track_loop, args=(cap, max_side, anchors, state, stop),
                     daemon=True).start()

    def _redraw() -> None:
        canvas.delete("box")
        for b in state.get():
            canvas.create_rectangle(
                (b.x1 - left) * sx, (b.y1 - top) * sy,
                (b.x2 - left) * sx, (b.y2 - top) * sy,
                outline=_BOX_COLOR, width=_BOX_WIDTH, tags="box",
            )
        root.after(16, _redraw)  # ~60 fps redraw

    def _on_close() -> None:
        stop.set()
        root.destroy()

    root.protocol("WM_DELETE_WINDOW", _on_close)
    root.after(16, _redraw)
    try:
        root.mainloop()
    finally:
        stop.set()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--monitor", type=int, default=1)
    ap.add_argument("--fps", type=float, default=30.0)
    ap.add_argument("--max-side", type=int, default=_WORK_MAX)
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO)
    run(monitor_index=args.monitor, fps=args.fps, max_side=args.max_side)


if __name__ == "__main__":
    main()
