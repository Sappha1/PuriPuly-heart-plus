"""RapidOCR text-region detector.

Wraps rapidocr-onnxruntime (which uses the onnxruntime we already ship for the
local Qwen speech model). Returns axis-aligned bounding boxes for every text
region found in a frame. Recognition text/score come along for free and are
kept on each box for later filtering — detection-first means we draw ALL boxes
now and prune false positives later.
"""

from __future__ import annotations

import logging
import os
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
            # RapidOCR otherwise re-caps detection to ~736px internally, so at 4K
            # small text is lost. Raise its limit to match our target resolution
            # so detection actually happens at max_side.
            try:
                self._engine.text_detector.preprocess_op[0].limit_side_len = (
                    self._max_side + 64
                )
            except Exception as exc:
                logger.debug("[OCR] could not raise det limit: %s", exc)
            self._try_gpu()

    def _try_gpu(self) -> None:
        """Rebuild the detection session on the GPU via DirectML when present.
        rapidocr 1.2.x only knows CUDA, so its CPU session is replaced by hand
        (onnxruntime-directml package). CPU inference measured ~1s per 1152px
        pass at 4K — the whole perceived correction lag; DML cuts the model
        run to a fraction (bench: 339ms → 145ms per pass on synthetic, more
        on real frames). FAIL-OPEN: any trouble keeps the stock CPU session."""
        try:
            import onnxruntime as ort

            if "DmlExecutionProvider" not in ort.get_available_providers():
                logger.info("[OCR] DirectML not available — detection on CPU")
                return
            infer = self._engine.text_detector.infer
            path = getattr(infer.session, "_model_path", None)
            if not path or not os.path.exists(path):
                logger.info("[OCR] det model path unknown — detection on CPU")
                return
            so = ort.SessionOptions()
            so.log_severity_level = 4
            so.graph_optimization_level = (
                ort.GraphOptimizationLevel.ORT_ENABLE_ALL
            )
            so.enable_mem_pattern = False  # required off for DML
            infer.session = ort.InferenceSession(
                path, sess_options=so,
                providers=[("DmlExecutionProvider", {"device_id": 0}),
                           "CPUExecutionProvider"])
            logger.info("[OCR] detection inference provider: %s",
                        infer.session.get_providers()[0])
        except Exception as exc:
            logger.warning("[OCR] DirectML init failed — CPU fallback: %s", exc)

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
            # INTER_AREA averages pixels when shrinking — it preserves thin
            # antialiased text that nearest-neighbour would drop, which is the
            # difference between catching and missing small on-screen lines.
            try:
                import cv2

                frame = cv2.resize(bgr, (new_w, new_h), interpolation=cv2.INTER_AREA)
            except Exception:
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

    def recognize(self, crops: list[np.ndarray]) -> list[tuple[str, float]]:
        """Read text from BGR crops (batch). Returns (text, score) per crop.
        FAIL-OPEN: engine trouble returns high-score empties so callers don't
        nuke everything on a transient error."""
        if not crops:
            return []
        try:
            self._ensure_engine()
            res, _elapse = self._engine.text_recognizer(crops)
            out: list[tuple[str, float]] = []
            for r in res:
                try:
                    out.append((str(r[0]), float(r[1])))
                except Exception:
                    out.append(("", 0.0))
            return out
        except Exception as exc:
            logger.debug("[OCR] recognize failed: %s", exc)
            return [("?", 1.0)] * len(crops)
