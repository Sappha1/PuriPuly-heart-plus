# ruff: noqa: F821
# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec file for PuriPuly <3.

Direct Windows PyInstaller packaging (executable-only / manual installer packaging):
    This direct path is not the release-complete compliance-packaging path and requires the staged overlay executable at build/overlay/PuriPulyHeartOverlay.exe plus the vendored OpenVR bundle under third_party/openvr/ (enforced below).
    pwsh -File scripts/ci/prepare-soxr-release-inputs.ps1
    pyinstaller build.spec
    ISCC installer.iss

Full release-complete compliance packaging requires scripts/ci/prepare-soxr-release-inputs.ps1 before scripts/ci/build-release-artifacts.ps1:
    pwsh -File scripts/ci/prepare-soxr-release-inputs.ps1
    pwsh -File scripts/ci/build-release-artifacts.ps1 -AppVersion <version> -InnoSetupVersion <version>

Output:
    dist/PuriPulyHeart/  (folder with all files)
"""

import json
import sys
from pathlib import Path

from PyInstaller.utils.hooks import (collect_data_files,
                                     collect_dynamic_libs,
                                     collect_submodules)

# Add src to path for imports
src_path = Path("src").resolve()
sys.path.insert(0, str(src_path))

# The local STT model manifest MUST ship — its absence crashes the app at
# launch (r299 shipped without it after a cleanup deleted data/models).
_stt_manifest = Path("src/puripuly_heart/data/models/qwen3-asr-0.6b-int8-sherpa.manifest.json").resolve()
if not _stt_manifest.exists():
    raise SystemExit(f"STT model manifest missing from source tree: {_stt_manifest}")

# r318: the bundled speaker-embedding model must ship too — captions silently
# lose speaker tags without it (embedder degrades to None, no crash, but a
# build without the file is a broken release).
# r355: 248-byte probe that tells us whether this machine's int8 arithmetic
# is trustworthy. Tiny, but without it the check silently degrades to guessing
# from the CPU name.
_int8_probe = Path("src/puripuly_heart/data/models/int8_saturation_probe.onnx").resolve()
if not _int8_probe.exists():
    raise SystemExit(f"int8 probe missing from source tree: {_int8_probe}")

_speaker_model = Path("src/puripuly_heart/data/models/speaker/eres2netv2_zh_16k.onnx").resolve()
if not _speaker_model.exists():
    raise SystemExit(f"Speaker embedding model missing from source tree: {_speaker_model}")

overlay_staged_path = Path("build").resolve() / "overlay" / "PuriPulyHeartOverlay.exe"
if not overlay_staged_path.exists():
    raise SystemExit(
        "Staged overlay executable not found at "
        f"{overlay_staged_path}. Build and stage the Rust overlay before PyInstaller packaging."
    )

from puripuly_heart import __version__
from puripuly_heart.core.local_qwen_runtime import LOCAL_QWEN_PACKAGED_RUNTIME_RELATIVE_DIR
from puripuly_heart.core.overlay.openvr_vendor import collect_vendored_openvr_runtime_binaries

sys.path.insert(0, str(Path("scripts").resolve()))
from pyi_version_resource import build_version_resource

block_cipher = None
SOXR_RELEASE_INPUTS_MANIFEST_PATH = Path("build/soxr-release-inputs/manifest.json").resolve()
SOXR_PACKAGED_RUNTIME_RELATIVE_DIR = Path("soxr")
NOTO_CJK_SOURCE_FONT_PATH = src_path / "puripuly_heart" / "data" / "fonts" / "NotoSansCJK-Medium.ttc"
NOTO_CJK_PROVENANCE_DIR = Path("third_party/noto-sans-cjk").resolve()
NOTO_CJK_PACKAGED_PROVENANCE_RELATIVE_DIR = Path("third_party/noto-sans-cjk")

if not NOTO_CJK_SOURCE_FONT_PATH.is_file():
    raise SystemExit(f"Noto Sans CJK Medium TTC not found: {NOTO_CJK_SOURCE_FONT_PATH}")


def get_prepared_soxr_runtime_paths() -> tuple[Path, Path]:
    if not SOXR_RELEASE_INPUTS_MANIFEST_PATH.is_file():
        raise SystemExit(
            "Staged soxr release inputs manifest not found at "
            f"{SOXR_RELEASE_INPUTS_MANIFEST_PATH}. "
            "Run scripts/ci/prepare-soxr-release-inputs.ps1 before PyInstaller packaging."
        )

    manifest = json.loads(SOXR_RELEASE_INPUTS_MANIFEST_PATH.read_text(encoding="utf-8-sig"))
    runtime_manifest = manifest["runtime"]
    packaged_relative_dir = Path(runtime_manifest["packaged_relative_dir"])
    if packaged_relative_dir.as_posix() != SOXR_PACKAGED_RUNTIME_RELATIVE_DIR.as_posix():
        raise SystemExit(
            "Prepared soxr runtime packaged layout mismatch: expected "
            f"{SOXR_PACKAGED_RUNTIME_RELATIVE_DIR.as_posix()}, got "
            f"{packaged_relative_dir.as_posix()}"
        )

    extension_path = Path(runtime_manifest["extension_path"]).resolve()
    sibling_dll_path = Path(runtime_manifest["dll_path"]).resolve()
    expected_runtime_names = {"soxr_ext.pyd", "soxr.dll"}
    actual_runtime_names = {extension_path.name.lower(), sibling_dll_path.name.lower()}
    if actual_runtime_names != expected_runtime_names:
        raise SystemExit(
            "Prepared soxr runtime inputs must contain exactly soxr_ext.pyd and soxr.dll; "
            f"got {sorted(actual_runtime_names)}"
        )

    for runtime_path in (extension_path, sibling_dll_path):
        if not runtime_path.is_file():
            raise SystemExit(f"Prepared soxr runtime file not found: {runtime_path}")

    return extension_path, sibling_dll_path


def collect_staged_soxr_runtime_binaries() -> list[tuple[str, str]]:
    extension_path, sibling_dll_path = get_prepared_soxr_runtime_paths()

    return [
        (str(extension_path), SOXR_PACKAGED_RUNTIME_RELATIVE_DIR.as_posix()),
        (str(sibling_dll_path), SOXR_PACKAGED_RUNTIME_RELATIVE_DIR.as_posix()),
    ]


def normalize_soxr_runtime_binaries(binaries):
    binaries[:] = [
        binary
        for binary in binaries
        if not _is_root_level_auto_collected_soxr_dll(binary)
    ]


def _is_root_level_auto_collected_soxr_dll(binary) -> bool:
    destination_name, _source_path, _typecode = binary
    normalized_destination_name = destination_name.replace("\\", "/")
    return normalized_destination_name == "soxr.dll"

# Collect data files
datas = [
    # Project license text for packaged/installed distributions
    ("LICENSE", "."),
    # VAD model and data files
    (str(src_path / "puripuly_heart" / "data"), "puripuly_heart/data"),
    # Prompt templates
    ("prompts", "prompts"),
    # Native VR Subtitle Overlay executable
    (str(overlay_staged_path), "."),
    # Native VR Subtitle Overlay distribution provenance.
    # The TTC itself is included by the packaged puripuly_heart/data tree above.
    (str(NOTO_CJK_PROVENANCE_DIR / "OFL.txt"), NOTO_CJK_PACKAGED_PROVENANCE_RELATIVE_DIR.as_posix()),
    (str(NOTO_CJK_PROVENANCE_DIR / "README.md"), NOTO_CJK_PACKAGED_PROVENANCE_RELATIVE_DIR.as_posix()),
    (str(NOTO_CJK_PROVENANCE_DIR / "SHA256SUMS.txt"), NOTO_CJK_PACKAGED_PROVENANCE_RELATIVE_DIR.as_posix()),
] + collect_data_files("flet_desktop") + collect_data_files("pykakasi") + collect_data_files("unidic_lite") + collect_data_files("cutlet") + collect_data_files("langdetect") + collect_data_files("jieba")

runtime_binaries = collect_dynamic_libs(
    "onnxruntime", destdir=LOCAL_QWEN_PACKAGED_RUNTIME_RELATIVE_DIR.as_posix()
)
runtime_binaries += collect_dynamic_libs("cryptography")
runtime_binaries += collect_staged_soxr_runtime_binaries()
# r481: per-app audio capture — proctap ships a compiled _native .pyd that
# static analysis misses (imported via importlib inside the capture factory)
runtime_binaries += collect_dynamic_libs("proctap")
runtime_binaries += collect_vendored_openvr_runtime_binaries()

# Hidden imports for dynamic imports
hiddenimports = [
    "PIL.ImageGrab",       # beta/steam-bridge: clipboard-image paste (lazy import)
    "pygments",            # r476: code-block syntax highlighting (lazy import)
    "kaldi_native_fbank",  # r318 speaker-ID features (imported lazily)
    "puripuly_heart.providers.stt.deepgram",
    "puripuly_heart.providers.stt.qwen_asr",
    "puripuly_heart.providers.stt.soniox",
    "puripuly_heart.providers.llm.gemini",
    "puripuly_heart.providers.llm.qwen",
    "puripuly_heart.providers.llm.qwen_async",
    "google.genai",
    "dashscope",
    "deepgram",
    "websockets",
    "flet",
    "flet_desktop",
    "httpx",
    "keyring.backends.Windows",
    "onnxruntime",
    # r617: Windows-cert-store TLS for model/update downloads behind VPN/proxy
    # MITM roots. Imported lazily inside try/except — a build that drops it
    # silently regresses every mainland-China install.
    "truststore",
    # NumPy's C-extension is required before the packaged CLI can even boot.
    "numpy._core._multiarray_umath",
    "soxr",
    "sounddevice",
    # cryptography Rust native extension — missed by PyInstaller's static analysis
    "cryptography.hazmat.bindings._rust",
    "cryptography",
    "deepl",
    "puripuly_heart.providers.llm.deepl",
    "pypinyin",
    "jieba",
    "pykakasi",
    "cutlet",
    "fugashi",
    "unidic_lite",
    "puripuly_heart.core.transliteration",
    "puripuly_heart.providers.llm.free_web",
    "puripuly_heart.providers.stt.google_stt",
    "puripuly_heart.providers.stt.whisper_stt",
    "translators",
    "translators.server",
    "speech_recognition",
    "faster_whisper",
    "ctranslate2",
    "langdetect",
    # r481: per-app audio capture (lazy importlib inside the capture factory)
    "proctap",
    "proctap._native",
    "proctap.backends.windows",
    "psutil",
    "puripuly_heart.core.audio.process_source",
    "puripuly_heart.core.audio.process_identity",
    "puripuly_heart.config.process_capture_platform",
    "puripuly_heart.config.process_capture_resolution",
    "puripuly_heart.config.process_capture_target",
]
hiddenimports += collect_submodules("pygments.lexers")

a = Analysis(
    [str(src_path / "puripuly_heart" / "main.py")],
    pathex=[str(src_path)],
    binaries=runtime_binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "soxr.soxr_ext",
        "tkinter",
        "unittest",
        "pydoc",
        "doctest",
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

normalize_soxr_runtime_binaries(a.binaries)

# r654: the whole src/puripuly_heart/data tree is bundled, so anything a local
# test or helper run leaves in it (the Steam daemon writes diag.log next to
# itself; imports leave __pycache__) would ship with the release. Keep run
# artifacts out of the bundle regardless of what is on disk.
def _is_run_artifact(dest: str) -> bool:
    d = dest.replace("\\", "/")
    return d.startswith("puripuly_heart/data/") and (
        "/__pycache__/" in d or d.endswith(".log") or d.endswith(".pyc"))


a.datas = [e for e in a.datas if not _is_run_artifact(e[0])]

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

# r652: boot-splash transparency + "off = never shown". PyInstaller's splash IPC
# only knows `update_text`, and the bootloader draws the splash before Python
# runs, so we patch the generated tcl (the template strings Splash().generate_script
# concatenates) to:
#   1) start the window WITHDRAWN (hidden). The app deiconifies it (a `reveal`
#      IPC command) ONLY when the setting is on, so a disabled splash never
#      flashes - it is simply never shown, instead of opening then closing.
#   2) add a `set_alpha` IPC command for live window opacity, plus an 85% baked
#      default. boot_splash.set_alpha applies the user's Settings > Updates value
#      before revealing.
#   3) drop the Windows `-transparentcolor magenta` colour-key (we have no
#      transparent corners - the image is a solid rectangle) because combining it
#      with `-alpha` on a layered window suppressed the opacity in r651. Alpha
#      alone reliably renders a translucent window.
# showing/hiding rides on withdraw/deiconify (works regardless of alpha support);
# opacity rides on `-alpha` (best effort). Defensive: patch only when the anchors
# are present, else the splash degrades to a plain opaque one rather than failing.
import PyInstaller.building.splash_templates as _spl_tmpl
from PyInstaller.compat import is_win as _spl_is_win

_SPLASH_DEFAULT_ALPHA = 0.85  # matches settings.ui.boot_splash_opacity default (85)

_ipc_handlers = (
    '        # set_alpha(<0..1>) (r652): live whole-window opacity\n'
    '        if {[string match "set_alpha*" $cmd]} {\n'
    '            set first [expr {[string first "(" $cmd] + 1}]\n'
    '            set last [expr {[string last ")" $cmd] - 1}]\n'
    '            set _a [string range $cmd $first $last]\n'
    '            if {[string is double -strict $_a]} {\n'
    '                catch {wm attributes . -alpha $_a}\n'
    '            }\n'
    '        }\n'
    '        # reveal (r652): show the splash (baked withdrawn so "off" never flashes)\n'
    '        if {[string match "reveal*" $cmd]} {\n'
    '            catch {wm deiconify .}\n'
    '            catch {raise .}\n'
    '            catch {wm attributes . -topmost 1}\n'
    '        }\n'
    '        # Implement other procedures here'
)
if "# Implement other procedures here" in _spl_tmpl.ipc_script and "set_alpha" not in _spl_tmpl.ipc_script:
    _spl_tmpl.ipc_script = _spl_tmpl.ipc_script.replace(
        "        # Implement other procedures here", _ipc_handlers, 1
    )
else:
    print("build.spec WARNING: splash ipc_script anchor missing - reveal/set_alpha NOT injected")

# Bake the default opacity + start hidden. Appended to the window-setup template
# so the bootloader applies them before Python connects.
_bake = "\nwm attributes . -alpha %.3f\nwm withdraw .\n" % _SPLASH_DEFAULT_ALPHA
for _pw_name in ("position_window_on_top", "position_window"):
    _pw = getattr(_spl_tmpl, _pw_name, None)
    if isinstance(_pw, str) and "wm withdraw" not in _pw:
        setattr(_spl_tmpl, _pw_name, _pw + _bake)

# Drop the Windows magenta colour-key so `-alpha` renders (no transparent corners
# needed); keep the canvas background dark so no light sliver shows.
if _spl_is_win and "transparentcolor" in _spl_tmpl.transparent_setup:
    _spl_tmpl.transparent_setup = '\n.root.canvas configure -background "#202225"\n'

# r639: boot splash shown by the bootloader within a few hundred ms of launch
# (before Python finishes importing), closed by the app the moment the main
# window reveals (ui/app.py _close_boot_splash). Covers the ~4 s blank gap the
# user saw. Regenerate the art with build/make_splash.py.
splash = Splash(
    str(src_path / "puripuly_heart" / "data" / "icons" / "splash.png"),
    binaries=a.binaries,
    datas=a.datas,
    # r643: a live percentage (boot_splash animates it). Kept SHORT and using
    # the default splash font (no custom family - r641's "Consolas" fell back to
    # a wide font, which looked poor and threw the centring off). A short string
    # centres reliably: text_pos is the left edge (anchor sw), ~centred for "NN%".
    # r652: repositioned for the minimal 160x96 card.
    text_pos=(67, 88),
    text_size=10,
    text_color="#c9cbce",
    text_default="0%",
    always_on_top=True,
)

exe = EXE(
    pyz,
    a.scripts,
    splash,
    [],
    exclude_binaries=True,
    name="PuriPulyHeart",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,  # Windowed application (no terminal)
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    contents_directory="_internal",
    icon=str(src_path / "puripuly_heart" / "data" / "icons" / "icon.ico"),
    version=build_version_resource(
        version=__version__,
        name="PuriPulyHeart",
        description="PuriPulyHeart+ — VRChat live translation",
    ),
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    splash.binaries,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="PuriPulyHeart",
)
