"""r391: the local speech model could never load under a non-ASCII path.

A user in China could not get the mic working across seventeen hours and dozens
of launches. The log showed the load starting and then simply nothing — no
transcript, no error, no session, ever. Memory was never the problem: 5.7-8.5 GB
was free at every attempt.

Reproduced exactly, against the real model, by pointing the loader at the same
files through a directory named with Chinese characters:

    qwen-asr-tokenizer.cc:InitFromContents:1114
    Failed to read vocab.json from: ...\\vrc翻译\\tokenizer

sherpa-onnx reads model files with narrow paths and dies in native code, taking
the process down without raising anything Python can catch — which is why the
app vanished and the log stayed silent. The identical directory reached through
an ASCII path loads in five seconds.

Nobody can work around this: the model lives under %LOCALAPPDATA%, which
contains the Windows account name. A non-ASCII account name — ordinary across
China, Japan, Korea and Russia — meant the local model could never work.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from puripuly_heart.core.ascii_paths import (
    NonAsciiModelPathError,
    ascii_safe_path,
)


def test_an_ascii_path_is_returned_untouched() -> None:
    """The common case must not acquire short names, junctions, or any other
    surprise — the returned path is what ends up in logs and error messages."""
    path = Path(r"C:\Users\Owner\AppData\Local\puripuly-heart\models")
    assert ascii_safe_path(path) == path


def test_ascii_paths_that_do_not_exist_are_still_returned() -> None:
    """Resolution must not depend on the file being there — callers construct
    paths before the download finishes."""
    path = Path(r"C:\definitely\not\here\model")
    assert ascii_safe_path(path) == path


@pytest.mark.skipif(os.name != "nt", reason="Windows path semantics")
def test_a_non_ascii_directory_resolves_to_an_ascii_path(tmp_path: Path) -> None:
    """The whole bug in one assertion."""
    directory = tmp_path / "vrc翻译"
    directory.mkdir()
    (directory / "marker.txt").write_text("x", encoding="utf-8")

    resolved = ascii_safe_path(directory)

    assert str(resolved).isascii(), f"still non-ASCII: {resolved}"
    assert resolved.is_dir(), "the ASCII path does not point at a real directory"
    assert (resolved / "marker.txt").read_text(encoding="utf-8") == "x", (
        "the ASCII path points somewhere else — it must be the same directory"
    )


@pytest.mark.skipif(os.name != "nt", reason="Windows path semantics")
def test_the_resolved_path_survives_joining(tmp_path: Path) -> None:
    """The loader joins model filenames onto the directory, so the ASCII-ness
    has to hold for the children too — those are the paths sherpa opens."""
    directory = tmp_path / "语音模型"
    (directory / "tokenizer").mkdir(parents=True)
    (directory / "tokenizer" / "vocab.json").write_text("{}", encoding="utf-8")

    resolved = ascii_safe_path(directory)
    child = resolved / "tokenizer" / "vocab.json"

    assert str(child).isascii()
    assert child.read_text(encoding="utf-8") == "{}"


@pytest.mark.skipif(os.name != "nt", reason="Windows path semantics")
def test_a_non_ascii_file_resolves_to_an_ascii_path(tmp_path: Path) -> None:
    directory = tmp_path / "モデル"
    directory.mkdir()
    model = directory / "encoder.onnx"
    model.write_bytes(b"\x00\x01")

    resolved = ascii_safe_path(model)
    assert str(resolved).isascii()
    assert resolved.read_bytes() == b"\x00\x01"


def test_the_failure_is_typed_and_names_the_path() -> None:
    """When no ASCII route exists the caller must get something it can explain
    to the user — the alternative, historically, was the process dying."""
    error = NonAsciiModelPathError(Path(r"C:\Users\阿巴\models"))
    assert "阿巴" in str(error)
    assert isinstance(error, RuntimeError)


def test_the_loader_resolves_the_model_directory() -> None:
    """Structural: the recognizer factory must run the model dir through the
    helper BEFORE handing paths to sherpa, or the process dies as before."""
    source = Path("src/puripuly_heart/providers/stt/local_qwen_sherpa.py").read_text(
        encoding="utf-8"
    )
    start = source.index("def create_local_qwen_sherpa_recognizer")
    body = source[start : source.index("conv_frontend=str(model_dir", start)]
    assert "ascii_safe_path" in body, (
        "model paths reach sherpa unresolved again; a non-ASCII account name "
        "kills the process with no error anywhere"
    )
