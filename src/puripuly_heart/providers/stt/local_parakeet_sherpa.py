from __future__ import annotations

import importlib
from dataclasses import dataclass, field
from pathlib import Path

from puripuly_heart.core.local_qwen_runtime import (
    LocalQwenRuntimeBootstrapError,
    ensure_local_qwen_windows_runtime,
)
from puripuly_heart.core.local_stt_assets import (
    LOCAL_PARAKEET_JA_MODEL_ID,
    LOCAL_PARAKEET_V3_MODEL_ID,
)
from puripuly_heart.providers.stt.local_qwen_sherpa import (
    LOCAL_QWEN_RECOGNIZER_SAMPLE_RATE_HZ,
    LocalQwenLowMemoryError,
    LocalQwenSherpaLoadError,
    LocalQwenSherpaSTTBackend,
    _shared_recognizer,
)


class LocalParakeetSherpaLoadError(LocalQwenSherpaLoadError):
    """Subclass so every existing local-model load-error surface applies."""


class _LocalParakeetSherpaImportError(ImportError):
    pass


def _recognizer_class() -> tuple[object, type]:
    ensure_local_qwen_windows_runtime()
    try:
        import sherpa_onnx

        recognizer_module = importlib.import_module("sherpa_onnx.offline_recognizer")
    except ImportError as exc:
        raise _LocalParakeetSherpaImportError from exc
    return sherpa_onnx, getattr(recognizer_module, "_Recognizer")


def create_local_parakeet_v3_sherpa_recognizer(
    *,
    model_dir: Path,
    num_threads: int,
    sample_rate_hz: int = LOCAL_QWEN_RECOGNIZER_SAMPLE_RATE_HZ,
    feature_dim: int = 80,
    provider: str = "cpu",
) -> object:
    if sample_rate_hz != LOCAL_QWEN_RECOGNIZER_SAMPLE_RATE_HZ:
        raise ValueError(f"sample_rate_hz must be {LOCAL_QWEN_RECOGNIZER_SAMPLE_RATE_HZ}")
    # Same non-ASCII-profile crash as the Qwen loader (r391): sherpa reads these
    # with narrow paths and dies in native code without a Python exception.
    from puripuly_heart.core.ascii_paths import ascii_safe_path

    model_dir = ascii_safe_path(Path(model_dir))
    sherpa_onnx, recognizer_cls = _recognizer_class()
    transducer_config = sherpa_onnx.OfflineTransducerModelConfig(
        encoder_filename=str(model_dir / "encoder.int8.onnx"),
        decoder_filename=str(model_dir / "decoder.int8.onnx"),
        joiner_filename=str(model_dir / "joiner.int8.onnx"),
    )
    model_config = sherpa_onnx.OfflineModelConfig(
        transducer=transducer_config,
        tokens=str(model_dir / "tokens.txt"),
        num_threads=num_threads,
        debug=False,
        provider=provider,
        model_type="nemo_transducer",
    )
    feat_config = sherpa_onnx.FeatureExtractorConfig(
        sampling_rate=sample_rate_hz,
        feature_dim=feature_dim,
    )
    recognizer_config = sherpa_onnx.OfflineRecognizerConfig(
        feat_config=feat_config,
        model_config=model_config,
        decoding_method="greedy_search",
    )
    return recognizer_cls(recognizer_config)


def create_local_parakeet_japanese_sherpa_recognizer(
    *,
    model_dir: Path,
    num_threads: int,
    sample_rate_hz: int = LOCAL_QWEN_RECOGNIZER_SAMPLE_RATE_HZ,
    feature_dim: int = 80,
    provider: str = "cpu",
) -> object:
    if sample_rate_hz != LOCAL_QWEN_RECOGNIZER_SAMPLE_RATE_HZ:
        raise ValueError(f"sample_rate_hz must be {LOCAL_QWEN_RECOGNIZER_SAMPLE_RATE_HZ}")
    from puripuly_heart.core.ascii_paths import ascii_safe_path

    model_dir = ascii_safe_path(Path(model_dir))
    sherpa_onnx, recognizer_cls = _recognizer_class()
    nemo_config = sherpa_onnx.OfflineNemoEncDecCtcModelConfig(
        model=str(model_dir / "model.int8.onnx"),
    )
    model_config = sherpa_onnx.OfflineModelConfig(
        nemo_ctc=nemo_config,
        tokens=str(model_dir / "tokens.txt"),
        num_threads=num_threads,
        debug=False,
        provider=provider,
    )
    feat_config = sherpa_onnx.FeatureExtractorConfig(
        sampling_rate=sample_rate_hz,
        feature_dim=feature_dim,
    )
    recognizer_config = sherpa_onnx.OfflineRecognizerConfig(
        feat_config=feat_config,
        model_config=model_config,
        decoding_method="greedy_search",
    )
    return recognizer_cls(recognizer_config)


@dataclass(slots=True)
class LocalParakeetV3SherpaSTTBackend(LocalQwenSherpaSTTBackend):
    feature_dim: int = field(default=80, init=False)
    asset_model_id: str = field(default=LOCAL_PARAKEET_V3_MODEL_ID, init=False)

    def _create_recognizer(self) -> object:
        try:
            return _shared_recognizer(
                self._recognizer_cache_key(),
                lambda: create_local_parakeet_v3_sherpa_recognizer(
                    model_dir=self.model_dir,
                    num_threads=self.num_threads,
                    sample_rate_hz=LOCAL_QWEN_RECOGNIZER_SAMPLE_RATE_HZ,
                    feature_dim=self.feature_dim,
                    provider=self.provider,
                ),
            )
        except LocalQwenLowMemoryError:
            raise
        except LocalQwenRuntimeBootstrapError as exc:
            raise LocalParakeetSherpaLoadError(str(exc)) from exc
        except _LocalParakeetSherpaImportError as exc:
            raise LocalParakeetSherpaLoadError(
                "failed to import sherpa_onnx"
            ) from exc.__cause__
        except Exception as exc:
            raise LocalParakeetSherpaLoadError(str(exc)) from exc


@dataclass(slots=True)
class LocalParakeetJapaneseSherpaSTTBackend(LocalQwenSherpaSTTBackend):
    feature_dim: int = field(default=80, init=False)
    asset_model_id: str = field(default=LOCAL_PARAKEET_JA_MODEL_ID, init=False)

    def _create_recognizer(self) -> object:
        try:
            return _shared_recognizer(
                self._recognizer_cache_key(),
                lambda: create_local_parakeet_japanese_sherpa_recognizer(
                    model_dir=self.model_dir,
                    num_threads=self.num_threads,
                    sample_rate_hz=LOCAL_QWEN_RECOGNIZER_SAMPLE_RATE_HZ,
                    feature_dim=self.feature_dim,
                    provider=self.provider,
                ),
            )
        except LocalQwenLowMemoryError:
            raise
        except LocalQwenRuntimeBootstrapError as exc:
            raise LocalParakeetSherpaLoadError(str(exc)) from exc
        except _LocalParakeetSherpaImportError as exc:
            raise LocalParakeetSherpaLoadError(
                "failed to import sherpa_onnx"
            ) from exc.__cause__
        except Exception as exc:
            raise LocalParakeetSherpaLoadError(str(exc)) from exc


# provider-enum value -> (backend class, bundled-manifest model id); used by
# app wiring so both construction sites stay one lookup.
LOCAL_PARAKEET_BACKENDS: dict[str, tuple[type, str]] = {
    "local_parakeet_v3": (LocalParakeetV3SherpaSTTBackend, LOCAL_PARAKEET_V3_MODEL_ID),
    "local_parakeet_ja": (LocalParakeetJapaneseSherpaSTTBackend, LOCAL_PARAKEET_JA_MODEL_ID),
}
