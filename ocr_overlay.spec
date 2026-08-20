# ruff: noqa: F821
# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for the realtime OCR overlay (PuriPulyHeartOCR.exe).

The OCR overlay runs as a SEPARATE frozen app: it needs tkinter (excluded
from the main app), rapidocr + onnxruntime-directml, opencv, and mss —
none of which belong in the main bundle. The release packaging copies the
resulting dist/PuriPulyHeartOCR folder into dist/PuriPulyHeart/ocr/ so the
manager can launch it next to the main exe.

Build:
    pyinstaller ocr_overlay.spec
"""

import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs

src_path = Path("src").resolve()
sys.path.insert(0, str(src_path))
sys.path.insert(0, str(Path("scripts").resolve()))

from puripuly_heart import __version__
from pyi_version_resource import build_version_resource

block_cipher = None

datas = (
    # include_py_files: rapidocr imports its engine modules (ch_ppocr_v3_det
    # etc.) DYNAMICALLY from its package dir via sys.path — they must exist
    # as real .py files next to the models, or the frozen app resolves the
    # data-only directory as an empty namespace package and detection dies
    # with "module 'ch_ppocr_v3_det' has no attribute 'TextDetector'".
    collect_data_files("rapidocr_onnxruntime", include_py_files=True)
    + collect_data_files("jieba")               # dict.txt for word-grouped pinyin
    + collect_data_files("pypinyin")
    # wordninja is a lone module; its frequency list lives in a sibling
    # data dir it resolves relative to its own file
    + [(str(Path(SPECPATH) / ".venv/Lib/site-packages/wordninja/wordninja_words.txt.gz"),
        "wordninja")]
)

# onnxruntime-directml: DirectML.dll + providers ride along here
runtime_binaries = collect_dynamic_libs("onnxruntime")

hiddenimports = [
    "puripuly_heart.ocr.detector",
    "rapidocr_onnxruntime",
    "wordninja",
    # deps of rapidocr's dynamically-imported engine modules — invisible to
    # static analysis because the importing .py files ship as data
    "pyclipper",
    "shapely",
    "shapely.geometry",
    "yaml",
    "six",
    "onnxruntime",
    "cv2",
    "mss",
    "PIL",
    "PIL.Image",
    "PIL.ImageDraw",
    "PIL.ImageFont",
    "pypinyin",
    "jieba",
    # free web engines (bing/google/papago) translate inside the overlay
    "translators",
    "translators.server",
    "numpy._core._multiarray_umath",
]

a = Analysis(
    [str(src_path / "puripuly_heart" / "ocr" / "overlay_proc.py")],
    pathex=[str(src_path)],
    binaries=runtime_binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "unittest",
        "pydoc",
        "doctest",
        "flet",
        "flet_desktop",
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="PuriPulyHeartOCR",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,  # UPX packing is the top AV false-positive trigger (esp. 360/Tencent in China)
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    contents_directory="_internal",
    icon=str(src_path / "puripuly_heart" / "data" / "icons" / "icon.ico"),
    version=build_version_resource(
        version=__version__,
        name="PuriPulyHeartOCR",
        description="PuriPulyHeart+ realtime OCR overlay",
    ),
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="PuriPulyHeartOCR",
)
