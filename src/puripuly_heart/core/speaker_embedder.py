"""Voiceprint extraction for local speaker identification (r318).

Runs the bundled ERes2Net speaker-embedding ONNX (38MB, 3D-Speaker) on the
MAIN process's onnxruntime — deliberately NOT through sherpa-onnx, whose
private runtime DLLs are pinned to an older ORT that cannot load this model
class (same wall that blocked the GTCRN denoiser in r298). Features come
from kaldi-native-fbank, the exact extractor the model was trained against;
a hand-rolled numpy fbank was measured 2026-07-30 and halves the
same/different-speaker separation (0.88/0.69 vs 0.76/0.2-0.3).

~30ms per utterance on CPU. Called from STT worker threads only.
"""
from __future__ import annotations

import logging
import threading
from importlib import resources
from pathlib import Path

import numpy as np

from puripuly_heart.core.speaker_id import EMBEDDING_DIM, MIN_UTTERANCE_SECONDS

logger = logging.getLogger(__name__)

_MODEL_RESOURCE = "data/models/speaker/eres2net_base_zh_16k.onnx"
SAMPLE_RATE_HZ = 16000


def _bundled_model_path() -> Path:
    return Path(str(resources.files("puripuly_heart") / _MODEL_RESOURCE))


class SpeakerEmbedder:
    """Lazy-loading, thread-safe voiceprint extractor. embed() returns a
    unit-norm float32 vector, or None when the segment is too short or the
    model is unavailable (missing file, ORT failure) — callers degrade to
    unlabeled captions, never crash."""

    def __init__(self, model_path: Path | None = None) -> None:
        self._model_path = model_path or _bundled_model_path()
        self._lock = threading.Lock()
        self._session = None
        self._input_name = ""
        self._failed = False

    def _ensure_session(self) -> bool:
        if self._session is not None:
            return True
        if self._failed:
            return False
        with self._lock:
            if self._session is not None:
                return True
            if self._failed:
                return False
            try:
                import onnxruntime as ort

                session = ort.InferenceSession(
                    str(self._model_path), providers=["CPUExecutionProvider"]
                )
                self._input_name = session.get_inputs()[0].name
                self._session = session
                logger.info("[SpeakerID] embedding model loaded")
                return True
            except Exception:
                self._failed = True
                logger.warning(
                    "[SpeakerID] embedding model unavailable — captions stay "
                    "unlabeled",
                    exc_info=True,
                )
                return False

    def _fbank(self, samples: np.ndarray) -> np.ndarray | None:
        try:
            import kaldi_native_fbank as knf
        except Exception:
            if not self._failed:
                self._failed = True
                logger.warning("[SpeakerID] kaldi-native-fbank missing")
            return None
        opts = knf.FbankOptions()
        opts.frame_opts.samp_freq = SAMPLE_RATE_HZ
        opts.frame_opts.dither = 0.0
        opts.frame_opts.snip_edges = True
        opts.mel_opts.num_bins = 80
        extractor = knf.OnlineFbank(opts)
        extractor.accept_waveform(SAMPLE_RATE_HZ, (samples * 32768.0).tolist())
        extractor.input_finished()
        frames = extractor.num_frames_ready
        if frames <= 0:
            return None
        feats = np.array(
            [extractor.get_frame(i) for i in range(frames)], dtype=np.float32
        )
        feats -= feats.mean(axis=0, keepdims=True)
        return feats

    def embed(self, samples: np.ndarray) -> np.ndarray | None:
        samples = np.asarray(samples, dtype=np.float32).reshape(-1)
        if samples.size < int(MIN_UTTERANCE_SECONDS * SAMPLE_RATE_HZ):
            return None
        if not self._ensure_session():
            return None
        try:
            feats = self._fbank(samples)
            if feats is None:
                return None
            out = self._session.run(  # type: ignore[union-attr]
                None, {self._input_name: feats[None, :, :]}
            )[0][0]
            vector = np.asarray(out, dtype=np.float32)
            if vector.shape != (EMBEDDING_DIM,):
                return None
            norm = float(np.linalg.norm(vector))
            if norm <= 0.0:
                return None
            return vector / norm
        except Exception:
            logger.warning("[SpeakerID] embedding failed for a segment", exc_info=True)
            return None


__all__ = ["SAMPLE_RATE_HZ", "SpeakerEmbedder"]
