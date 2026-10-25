"""Installs from an older accepted model revision keep working and are
updated in place: only the changed file is downloaded, swapped in when not in
use, and the installed revision is recorded afterwards."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from puripuly_heart.core import local_stt_assets as assets
from puripuly_heart.core import local_stt_runtime_installer as installer
from puripuly_heart.core.local_stt_assets import (
    LocalSTTAssetManifest,
    apply_pending_local_stt_repairs,
    inspect_local_stt_install_state,
    load_local_stt_asset_manifest,
    repair_file_path,
)

OLD_REV = "old-revision"
NEW_REV = "new-revision"
SAME = b"unchanged file"
OLD_DECODER = b"old decoder bytes"
NEW_DECODER = b"corrected decoder bytes!"


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _manifest() -> LocalSTTAssetManifest:
    return LocalSTTAssetManifest.from_dict(
        {
            "manifest_version": 1,
            "installed_manifest_version": 1,
            "model_id": "test-model",
            "engine": "sherpa-onnx",
            "upstream_repo": "example/test",
            "install_dirname": "test-model",
            "sources": {
                "huggingface": {
                    "name": "huggingface",
                    "revision": NEW_REV,
                    "download_url_template": "https://example.invalid/{path}",
                    "accepted_revisions": [OLD_REV],
                }
            },
            "files": [
                {"relative_path": "same.onnx", "sha256": _sha(SAME), "size_bytes": len(SAME)},
                {
                    "relative_path": "decoder.onnx",
                    "sha256": _sha(NEW_DECODER),
                    "size_bytes": len(NEW_DECODER),
                    "legacy_sha256": [_sha(OLD_DECODER)],
                },
            ],
        }
    )


def _install(tmp_path: Path, revision: str = OLD_REV) -> Path:
    model_dir = tmp_path / "test-model"
    model_dir.mkdir()
    (model_dir / "same.onnx").write_bytes(SAME)
    (model_dir / "decoder.onnx").write_bytes(OLD_DECODER)
    (model_dir / "installed-manifest.json").write_text(
        json.dumps(
            {
                "manifest_version": 1,
                "model_id": "test-model",
                "engine": "sherpa-onnx",
                "install_dirname": "test-model",
                "selected_source": "huggingface",
                "selected_revision": revision,
            }
        ),
        encoding="utf-8",
    )
    return model_dir


def _revision(model_dir: Path) -> str:
    return json.loads((model_dir / "installed-manifest.json").read_text())["selected_revision"]


def test_an_accepted_older_revision_is_still_ready(tmp_path) -> None:
    model_dir = _install(tmp_path)

    state = inspect_local_stt_install_state(model_dir, manifest=_manifest())

    assert state.status == "ready"
    # full checksum validation accepts the superseded decoder too
    assert assets.validate_local_stt_install(model_dir, manifest=_manifest())


def test_an_unknown_revision_is_still_invalid(tmp_path) -> None:
    model_dir = _install(tmp_path, revision="something-else")

    assert inspect_local_stt_install_state(model_dir, manifest=_manifest()).status == "invalid"


@pytest.mark.asyncio
async def test_repair_downloads_only_the_changed_file(tmp_path, monkeypatch) -> None:
    model_dir = _install(tmp_path)
    downloaded: list[str] = []
    announced: list[int] = []

    def fake_download(*, asset, source_name, manifest, target, cancel_event=None):
        downloaded.append(asset.relative_path)
        target.write_bytes(NEW_DECODER)

    monkeypatch.setattr(installer, "_download_repair_file", fake_download)

    result = await installer.repair_outdated_local_stt_install(
        model_dir=model_dir, manifest=_manifest(), on_start=announced.append
    )

    assert result == "updated"
    assert downloaded == ["decoder.onnx"]
    assert announced == [len(NEW_DECODER)]
    assert (model_dir / "decoder.onnx").read_bytes() == NEW_DECODER
    assert _revision(model_dir) == NEW_REV
    assert not repair_file_path(model_dir / "decoder.onnx").exists()


@pytest.mark.asyncio
async def test_a_file_in_use_is_swapped_on_the_next_check(tmp_path, monkeypatch) -> None:
    model_dir = _install(tmp_path)

    def fake_download(*, asset, source_name, manifest, target, cancel_event=None):
        target.write_bytes(NEW_DECODER)

    real_replace = assets.os.replace

    def locked_replace(src, dst):
        if str(dst).endswith("decoder.onnx"):
            raise PermissionError("in use")
        return real_replace(src, dst)

    monkeypatch.setattr(installer, "_download_repair_file", fake_download)
    monkeypatch.setattr(assets.os, "replace", locked_replace)

    result = await installer.repair_outdated_local_stt_install(
        model_dir=model_dir, manifest=_manifest()
    )

    assert result == "pending"
    assert (model_dir / "decoder.onnx").read_bytes() == OLD_DECODER  # still serving
    assert _revision(model_dir) == OLD_REV

    monkeypatch.setattr(assets.os, "replace", real_replace)
    # next start: inspecting the install applies the pending swap
    state = inspect_local_stt_install_state(model_dir, manifest=_manifest())

    assert state.status == "ready"
    assert (model_dir / "decoder.onnx").read_bytes() == NEW_DECODER
    assert _revision(model_dir) == NEW_REV


@pytest.mark.asyncio
async def test_a_current_install_is_left_alone(tmp_path, monkeypatch) -> None:
    model_dir = _install(tmp_path, revision=NEW_REV)
    (model_dir / "decoder.onnx").write_bytes(NEW_DECODER)
    monkeypatch.setattr(
        installer, "_download_repair_file", lambda **_: pytest.fail("nothing to download")
    )

    assert (
        await installer.repair_outdated_local_stt_install(model_dir=model_dir, manifest=_manifest())
        == "current"
    )


def test_a_truncated_replacement_is_discarded(tmp_path) -> None:
    model_dir = _install(tmp_path)
    repair_file_path(model_dir / "decoder.onnx").write_bytes(b"short")

    assert apply_pending_local_stt_repairs(model_dir, manifest=_manifest()) is False
    assert not repair_file_path(model_dir / "decoder.onnx").exists()
    assert (model_dir / "decoder.onnx").read_bytes() == OLD_DECODER


def test_bundled_qwen_manifest_pins_the_corrected_decoder() -> None:
    manifest = load_local_stt_asset_manifest("qwen3-asr-0.6b-int8-sherpa")
    decoder = next(a for a in manifest.files if a.relative_path == "decoder.int8.onnx")

    assert decoder.sha256 == "4f6885be5959ae26af3089d38ee7972c5fafbeeb1cf8d5e76eab6d8b61ca5771"
    assert decoder.size_bytes == 755914231
    # every source serves the same corrected file now
    for name in manifest.sources:
        assert decoder.sha256_for_source(name) == decoder.sha256
    assert manifest.sources["huggingface"].revision == "68818b2313fe77bd06f6a7c5068ff3ef59d02b8a"
    assert "2cc50d1abfe4d4f2df8d71f536d108bb40f943d2" in manifest.sources["huggingface"].accepted_revisions
