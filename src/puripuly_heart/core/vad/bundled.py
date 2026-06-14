from __future__ import annotations

import shutil
from importlib import resources
from pathlib import Path

from puripuly_heart.config.paths import default_vad_model_path

SILERO_VAD_VERSION = "6.2.1"
SILERO_VAD_RESOURCE_RELATIVE_PATH = "data/vad/silero_vad.onnx"
SILERO_VAD_RESOURCE_SHA256 = "1a153a22f4509e292a94e67d6f9b85e8deb25b4988682b7e174c65279d8788e3"


def bundled_silero_vad_onnx_path() -> resources.abc.Traversable:
    return resources.files("puripuly_heart").joinpath(SILERO_VAD_RESOURCE_RELATIVE_PATH)


def ensure_silero_vad_onnx(*, target_path: Path | None = None) -> Path:
    target_path = target_path or default_vad_model_path()

    try:
        if target_path.exists() and target_path.stat().st_size > 0:
            return target_path
    except OSError:
        pass

    bundled = bundled_silero_vad_onnx_path()
    if not bundled.is_file():
        raise FileNotFoundError(
            f"Bundled Silero VAD model missing: {SILERO_VAD_RESOURCE_RELATIVE_PATH}"
        )

    target_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = target_path.with_suffix(target_path.suffix + ".tmp")
    with bundled.open("rb") as src, tmp.open("wb") as dst:
        shutil.copyfileobj(src, dst)
    tmp.replace(target_path)
    return target_path
