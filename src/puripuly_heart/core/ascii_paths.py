"""Paths the native model loaders can actually open.

r391: sherpa-onnx reads its model files through narrow (byte) paths on Windows
and cannot open anything outside the system codepage. Measured against the real
model: a directory whose name contains Chinese characters fails in native code —

    qwen-asr-tokenizer.cc:InitFromContents:1114
    Failed to read vocab.json from: ...\\vrc翻译\\tokenizer

— and takes the process down with it. No Python exception is raised, so nothing
is logged, no error dialog appears, and the app simply vanishes mid-load. The
same model directory reached through an ASCII path loads in under five seconds.

This is not avoidable by the user: the model lives under %LOCALAPPDATA%, which
contains their Windows account name. Anyone whose account name is not ASCII —
which is ordinary in China, Japan, Korea and Russia — can never load the local
speech model, and the app gives them no way to find out why.

The fix asks Windows for an equivalent ASCII path rather than moving anything:

  1. Already ASCII — the overwhelmingly common case — returned untouched.
  2. The 8.3 short name (C:\\Users\\ABC~1\\...), which Windows maintains for
     exactly this class of problem. Costs nothing and copies nothing.
  3. A junction from an ASCII directory, when 8.3 names are disabled on the
     volume (some enterprise images turn them off).

If none of those work the caller gets a typed error naming the path, which is
infinitely better than a silent process death.
"""

from __future__ import annotations

import hashlib
import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)


class NonAsciiModelPathError(RuntimeError):
    """No ASCII route to a model file could be produced on this machine."""

    def __init__(self, path: Path) -> None:
        self.path = path
        super().__init__(
            "The local speech model is stored under a folder whose name contains "
            f"characters the speech engine cannot read ({path}). Windows short "
            "names are unavailable on this drive, so there is no way to reach it."
        )


def _short_path(text: str) -> str:
    """The Windows 8.3 short name, or "" when unavailable."""
    try:
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.windll.kernel32
        kernel32.GetShortPathNameW.argtypes = [
            wintypes.LPCWSTR,
            wintypes.LPWSTR,
            wintypes.DWORD,
        ]
        kernel32.GetShortPathNameW.restype = wintypes.DWORD
        buffer = ctypes.create_unicode_buffer(32768)
        written = kernel32.GetShortPathNameW(text, buffer, len(buffer))
        if not written:
            return ""
        return buffer.value
    except Exception:
        return ""


def _ascii_junction(directory: Path) -> Path | None:
    """A junction to `directory` from somewhere ASCII, created once and reused.

    C:\\Users\\Public is physically named that on every Windows locale (only its
    display name is translated) and is writable without elevation, so it is a
    dependable ASCII anchor when 8.3 names are switched off.
    """
    try:
        import _winapi

        public = os.environ.get("PUBLIC") or r"C:\Users\Public"
        if not str(public).isascii():
            return None
        digest = hashlib.sha256(str(directory).encode("utf-8")).hexdigest()[:16]
        root = Path(public) / "PuriPulyHeart" / "ascii-paths"
        link = root / digest
        if link.is_dir():
            return link
        root.mkdir(parents=True, exist_ok=True)
        _winapi.CreateJunction(str(directory), str(link))
        logger.info(
            "[AsciiPath] created an ASCII junction for a model directory the "
            "speech engine cannot open directly"
        )
        return link if link.is_dir() else None
    except Exception:
        logger.debug("[AsciiPath] junction fallback failed", exc_info=True)
        return None


def ascii_safe_path(path: Path) -> Path:
    """An equivalent path containing only ASCII, for handing to native loaders.

    Never raises for an already-ASCII path, which is the only case the vast
    majority of installs ever hit.
    """
    text = str(path)
    if text.isascii():
        return path

    short = _short_path(text)
    if short and short.isascii() and Path(short).exists():
        logger.info(
            "[AsciiPath] using a Windows short name for a model path the speech "
            "engine cannot read directly"
        )
        return Path(short)

    # Files: an ASCII parent plus an ASCII filename is still an ASCII path.
    if path.is_file():
        if path.name.isascii():
            parent = _ascii_junction(path.parent)
            if parent is not None:
                candidate = parent / path.name
                if candidate.exists():
                    return candidate
        raise NonAsciiModelPathError(path)

    junction = _ascii_junction(path)
    if junction is not None:
        return junction

    raise NonAsciiModelPathError(path)
