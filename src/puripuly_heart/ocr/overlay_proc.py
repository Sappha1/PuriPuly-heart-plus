"""OCR detection overlay — standalone subprocess (prototype).

Detect-then-track for every-frame, zero-lag boxes on moving text:
  * a background DETECTION thread scans the screen a few times a second to find
    and refresh text boxes (slow, full-frame);
  * a fast TRACKING loop moves each box every delivered frame using optical flow
    on a SMALL PATCH around that box only — cost is independent of the 4K screen
    resolution, so it runs at the frame-delivery rate;
  * VELOCITY EXTRAPOLATION nudges each box one frame ahead by its recent motion,
    cancelling the ~1-frame capture latency so the outline sits on the text
    rather than trailing it.

Capture uses dxcam (DXGI desktop duplication) with an mss fallback. Detection
only — no translation.

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
_DETECT_MAX_SIDE = 1280   # detection working resolution (recall vs speed)
_DETECT_INTERVAL = 0.35   # seconds between full detections
_FLOW_MARGIN = 72         # px of context around a box for its flow patch
_EXTRAPOLATE = 1.0        # frames of motion to lead by (latency cancel)


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
    """Latest detection result handed from the detect thread to the tracker."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.boxes: list[TextBox] = []
        self.green: np.ndarray | None = None
        self.stamp = 0

    def publish(self, boxes: list[TextBox], green: np.ndarray) -> None:
        with self._lock:
            self.boxes, self.green, self.stamp = boxes, green, self.stamp + 1

    def take(self, last_stamp: int):
        with self._lock:
            if self.stamp == last_stamp:
                return None
            return self.stamp, list(self.boxes), self.green


def _detect_loop(cap: _Capture, anchors: _Anchors, stop: threading.Event) -> None:
    detector = TextDetector(max_side=_DETECT_MAX_SIDE)
    while not stop.is_set():
        t0 = time.monotonic()
        try:
            frame = cap.last()
            boxes = detector.detect(frame)  # returns full-res coords
            green = np.ascontiguousarray(frame[:, :, 1])  # for latency-advance
            anchors.publish(boxes, green)
        except Exception as exc:
            logger.debug("[OCR] detect error: %s", exc)
        stop.wait(max(0.0, _DETECT_INTERVAL - (time.monotonic() - t0)))


def _flow_boxes(prev_g: np.ndarray, cur_g: np.ndarray,
                boxes: list[TextBox]) -> list[tuple[TextBox, float, float]]:
    """Move each box by the median optical flow of points inside it, computed on
    a SMALL patch around the box (cost independent of screen resolution).
    Returns (moved_box, dx, dy) so callers can extrapolate."""
    import cv2

    H, W = cur_g.shape[:2]
    out: list[tuple[TextBox, float, float]] = []
    for b in boxes:
        x1 = max(0, b.x1 - _FLOW_MARGIN); y1 = max(0, b.y1 - _FLOW_MARGIN)
        x2 = min(W, b.x2 + _FLOW_MARGIN); y2 = min(H, b.y2 + _FLOW_MARGIN)
        if x2 - x1 < 8 or y2 - y1 < 8:
            out.append((b, 0.0, 0.0)); continue
        pp = np.ascontiguousarray(prev_g[y1:y2, x1:x2])
        cp = np.ascontiguousarray(cur_g[y1:y2, x1:x2])
        gx = np.linspace(b.x1 + 2, b.x2 - 2, 4) - x1
        gy = np.linspace(b.y1 + 2, b.y2 - 2, 3) - y1
        p0 = np.array([[x, y] for y in gy for x in gx], dtype=np.float32).reshape(-1, 1, 2)
        try:
            p1, st, _ = cv2.calcOpticalFlowPyrLK(pp, cp, p0, None,
                                                 winSize=(21, 21), maxLevel=2)
        except Exception:
            out.append((b, 0.0, 0.0)); continue
        d = (p1 - p0).reshape(-1, 2)
        good = st.reshape(-1) == 1
        if good.sum() >= 2:
            dx, dy = (float(v) for v in np.median(d[good], axis=0))
        else:
            dx = dy = 0.0
        out.append((TextBox(int(b.x1 + dx), int(b.y1 + dy),
                            int(b.x2 + dx), int(b.y2 + dy)), dx, dy))
    return out


def _track_loop(cap: _Capture, anchors: _Anchors, state: "_BoxState",
                stop: threading.Event) -> None:
    prev_frame: np.ndarray | None = None
    tracked: list[TextBox] = []
    last_stamp = 0
    while not stop.is_set():
        cur = cap.grab()
        if cur is None:
            time.sleep(0.002)  # screen unchanged — nothing to move
            continue
        cur_g = cur[:, :, 1]

        fresh = anchors.take(last_stamp)
        if fresh is not None:
            last_stamp, det_boxes, det_g = fresh
            if det_g is not None and det_boxes and det_g.shape == cur_g.shape:
                moved = _flow_boxes(det_g, cur_g, det_boxes)  # advance to now
            else:
                moved = [(b, 0.0, 0.0) for b in det_boxes]
        elif prev_frame is not None and tracked:
            moved = _flow_boxes(prev_frame[:, :, 1], cur_g, tracked)
        else:
            moved = [(b, 0.0, 0.0) for b in tracked]

        # Extrapolate one frame ahead by the just-measured velocity so the box
        # sits ON the moving text instead of one capture-frame behind.
        tracked = []
        disp: list[TextBox] = []
        for b, dx, dy in moved:
            tracked.append(b)
            ex, ey = int(dx * _EXTRAPOLATE), int(dy * _EXTRAPOLATE)
            disp.append(TextBox(b.x1 + ex + cap.left, b.y1 + ey + cap.top,
                                b.x2 + ex + cap.left, b.y2 + ey + cap.top))
        prev_frame = cur
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


def run(monitor_index: int = 1, fps: float = 0.0, max_side: int = _DETECT_MAX_SIDE) -> None:
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
    threading.Thread(target=_detect_loop, args=(cap, anchors, stop), daemon=True).start()
    threading.Thread(target=_track_loop, args=(cap, anchors, state, stop), daemon=True).start()

    def _redraw() -> None:
        canvas.delete("box")
        for b in state.get():
            canvas.create_rectangle(
                (b.x1 - left) * sx, (b.y1 - top) * sy,
                (b.x2 - left) * sx, (b.y2 - top) * sy,
                outline=_BOX_COLOR, width=_BOX_WIDTH, tags="box",
            )
        root.after(6, _redraw)  # ~160 fps redraw

    def _on_close() -> None:
        stop.set()
        root.destroy()

    root.protocol("WM_DELETE_WINDOW", _on_close)
    root.after(6, _redraw)
    try:
        root.mainloop()
    finally:
        stop.set()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--monitor", type=int, default=1)
    ap.add_argument("--fps", type=float, default=0.0)  # unused; tracks at frame rate
    ap.add_argument("--max-side", type=int, default=_DETECT_MAX_SIDE)
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO)
    run(monitor_index=args.monitor, fps=args.fps, max_side=args.max_side)


if __name__ == "__main__":
    main()
