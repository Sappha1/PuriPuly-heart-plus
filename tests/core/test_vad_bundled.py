from __future__ import annotations

import hashlib

from puripuly_heart.core.vad.bundled import (
    SILERO_VAD_RESOURCE_SHA256,
    bundled_silero_vad_onnx_path,
    ensure_silero_vad_onnx,
)


def test_ensure_silero_vad_onnx_copies_file(tmp_path):
    target = tmp_path / "silero.onnx"

    path = ensure_silero_vad_onnx(target_path=target)
    assert path == target
    assert path.exists()
    assert path.stat().st_size > 0

    same = ensure_silero_vad_onnx(target_path=target)
    assert same == target


def test_bundled_silero_vad_sha256_matches_constant():
    bundled = bundled_silero_vad_onnx_path()

    with bundled.open("rb") as fh:
        digest = hashlib.file_digest(fh, "sha256").hexdigest()

    assert digest == SILERO_VAD_RESOURCE_SHA256
