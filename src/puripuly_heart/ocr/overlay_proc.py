"""OCR detection overlay — standalone subprocess (prototype).

Launched by the dashboard OCR toggle (see ``manager.py``). Captures the primary
monitor, runs text detection on a worker thread, and draws a thin red outline
around every detected text region on a transparent, click-through, always-on-top
window that follows the text in real time. Detection only — no translation.

Run directly for testing:
    python -m puripuly_heart.ocr.overlay_proc --fps 4
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

# Canvas background + window transparent-color key. A drab olive that is very
# unlikely to be produced by our own 1px red outlines, so it keys out cleanly.
_TRANSPARENT_KEY = "#010203"
_BOX_COLOR = "#ff2020"
_BOX_WIDTH = 1


def _set_dpi_aware() -> None:
    """Make capture pixels and Tk geometry share one (physical) coordinate
    space, so boxes land on the text under display scaling."""
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)  # per-monitor v2
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


def _make_click_through(root: tk.Tk) -> None:
    """Add WS_EX_LAYERED | WS_EX_TRANSPARENT so the whole overlay passes every
    mouse event to VRChat beneath it, and TOOLWINDOW to keep it out of Alt-Tab."""
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


class _BoxState:
    """Latest detected boxes, handed from the worker thread to the Tk thread."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._boxes: list[TextBox] = []

    def set(self, boxes: list[TextBox]) -> None:
        with self._lock:
            self._boxes = boxes

    def get(self) -> list[TextBox]:
        with self._lock:
            return list(self._boxes)


def _capture_loop(state: _BoxState, monitor_index: int, fps: float,
                  max_side: int, stop: threading.Event) -> None:
    import mss

    detector = TextDetector(max_side=max_side)
    period = 1.0 / max(0.5, fps)
    with mss.mss() as sct:
        mons = sct.monitors
        idx = monitor_index if 0 < monitor_index < len(mons) else 1
        mon = mons[idx]
        left, top = mon["left"], mon["top"]
        while not stop.is_set():
            t0 = time.monotonic()
            try:
                shot = sct.grab(mon)
                # BGRA -> BGR
                frame = np.asarray(shot)[:, :, :3]
                boxes = detector.detect(frame)
                # Shift into absolute screen coords for the canvas.
                for b in boxes:
                    b.x1 += left; b.x2 += left
                    b.y1 += top; b.y2 += top
                state.set(boxes)
            except Exception as exc:
                logger.debug("[OCR] capture/detect error: %s", exc)
            dt = time.monotonic() - t0
            stop.wait(max(0.0, period - dt))


def run(monitor_index: int = 1, fps: float = 4.0, max_side: int = 960) -> None:
    _set_dpi_aware()

    # Size the overlay to the target monitor via mss geometry.
    import mss

    with mss.mss() as sct:
        mons = sct.monitors
        idx = monitor_index if 0 < monitor_index < len(mons) else 1
        mon = mons[idx]
    left, top, width, height = mon["left"], mon["top"], mon["width"], mon["height"]

    root = tk.Tk()
    root.overrideredirect(True)
    root.attributes("-topmost", True)
    root.geometry(f"{width}x{height}+{left}+{top}")
    root.configure(bg=_TRANSPARENT_KEY)
    try:
        root.attributes("-transparentcolor", _TRANSPARENT_KEY)
    except Exception:
        pass

    canvas = tk.Canvas(root, bg=_TRANSPARENT_KEY, highlightthickness=0,
                       width=width, height=height)
    canvas.pack(fill="both", expand=True)
    root.update_idletasks()
    _make_click_through(root)

    state = _BoxState()
    stop = threading.Event()
    worker = threading.Thread(
        target=_capture_loop,
        args=(state, monitor_index, fps, max_side, stop),
        daemon=True,
    )
    worker.start()

    def _redraw() -> None:
        canvas.delete("box")
        for b in state.get():
            # Canvas origin is the monitor origin, so subtract it back out.
            canvas.create_rectangle(
                b.x1 - left, b.y1 - top, b.x2 - left, b.y2 - top,
                outline=_BOX_COLOR, width=_BOX_WIDTH, tags="box",
            )
        root.after(40, _redraw)

    def _on_close() -> None:
        stop.set()
        root.destroy()

    root.protocol("WM_DELETE_WINDOW", _on_close)
    root.after(40, _redraw)
    try:
        root.mainloop()
    finally:
        stop.set()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--monitor", type=int, default=1)
    ap.add_argument("--fps", type=float, default=4.0)
    ap.add_argument("--max-side", type=int, default=960)
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO)
    run(monitor_index=args.monitor, fps=args.fps, max_side=args.max_side)


if __name__ == "__main__":
    main()
