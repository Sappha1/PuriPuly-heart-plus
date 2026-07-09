"""RapidOCR text-region detector.

Wraps rapidocr-onnxruntime (which uses the onnxruntime we already ship for the
local Qwen speech model). Returns axis-aligned bounding boxes for every text
region found in a frame. Recognition text/score come along for free and are
kept on each box for later filtering — detection-first means we draw ALL boxes
now and prune false positives later.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import numpy as np

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class TextBox:
    # Axis-aligned bounds in ORIGINAL frame pixels.
    x1: int
    y1: int
    x2: int
    y2: int
    text: str = ""
    score: float = 0.0


class TextDetector:
    """Lazy-loaded RapidOCR wrapper. Downscales large frames for speed and maps
    the returned boxes back to original-frame coordinates."""

    def __init__(self, max_side: int = 960) -> None:
        self._max_side = max_side
        self._engine = None

    def _ensure_engine(self) -> None:
        if self._engine is None:
            from rapidocr_onnxruntime import RapidOCR

            # Defaults run on CPU. First call downloads/loads the bundled models.
            self._engine = RapidOCR()

    def detect(self, bgr: np.ndarray) -> list[TextBox]:
        """Detect text regions in a BGR frame. Never raises — returns [] on any
        failure so the capture loop keeps running."""
        try:
            self._ensure_engine()
        except Exception as exc:  # engine/model load problem
            logger.warning("[OCR] engine load failed: %s", exc)
            return []

        h, w = bgr.shape[:2]
        scale = 1.0
        frame = bgr
        longest = max(h, w)
        if longest > self._max_side:
            scale = self._max_side / float(longest)
            new_w, new_h = max(1, int(w * scale)), max(1, int(h * scale))
            # Cheap nearest-neighbour resize without pulling in cv2 here.
            ys = (np.arange(new_h) / scale).astype(np.int32).clip(0, h - 1)
            xs = (np.arange(new_w) / scale).astype(np.int32).clip(0, w - 1)
            frame = bgr[ys][:, xs]

        # DET-ONLY. The high-level engine call also runs recognition on every
        # crop (~4 s/frame with many text regions); text_detector returns just
        # the boxes at ~9 fps. Recognition (text/score) is added back later,
        # only on stable boxes, when we move to translation.
        try:
            det_boxes, _elapse = self._engine.text_detector(frame)
        except Exception as exc:
            logger.debug("[OCR] detect call failed: %s", exc)
            return []

        boxes: list[TextBox] = []
        for quad in det_boxes if det_boxes is not None else []:
            try:
                pts = np.asarray(quad, dtype=float)
                xs_, ys_ = pts[:, 0] / scale, pts[:, 1] / scale
                boxes.append(
                    TextBox(
                        x1=int(xs_.min()), y1=int(ys_.min()),
                        x2=int(xs_.max()), y2=int(ys_.max()),
                    )
                )
            except Exception:
                continue
        return boxes
