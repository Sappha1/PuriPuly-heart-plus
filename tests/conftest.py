from __future__ import annotations

import importlib
import sys
from pathlib import Path

SRC_PATH = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC_PATH))


import pytest

# r636: the suite used to run against the user's LIVE profile. default_main_log_file()
# falls back to user_config_dir() (%LOCALAPPDATA%/puripuly-heart/puripuly_heart.log),
# the main_gui OCR feed/bridge pollers and the dashboard derive their paths from
# expanduser("~")/AppData/Local, and core.vrchat_roster tails the real VRChat log dir.
# A pytest run holding the live log open made the real app die silently at startup.
# Everything below points that whole profile at a throwaway directory instead.
_SANDBOX_APP_DIR_NAME = "puripuly-heart"
_SANDBOX_LOCAL = ("AppData", "Local")
_SANDBOX_VRC_LOG = ("AppData", "LocalLow", "VRChat", "VRChat")
# Import-time path constants (computed from expanduser("~") when the module loads).
# Only re-pointed when the module is already imported - a module imported later picks
# the sandbox up from the environment on its own - so nothing here forces an import.
_SANDBOX_MODULE_CONSTANTS: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("puripuly_heart.core.vrchat_roster", "_LOG_DIR", _SANDBOX_VRC_LOG),
    ("puripuly_heart.ocr.manager", "_CONFIG_PATH",
     (*_SANDBOX_LOCAL, _SANDBOX_APP_DIR_NAME, "ocr_overlay_config.json")),
    ("puripuly_heart.ocr.overlay_proc", "_STATE_PATH",
     (*_SANDBOX_LOCAL, _SANDBOX_APP_DIR_NAME, "ocr_state.json")),
    ("puripuly_heart.ocr.overlay_proc", "_CONFIG_PATH",
     (*_SANDBOX_LOCAL, _SANDBOX_APP_DIR_NAME, "ocr_overlay_config.json")),
    ("puripuly_heart.ocr.overlay_proc", "_VRC_LOG_DIR", _SANDBOX_VRC_LOG),
    ("puripuly_heart.ocr.overlay_proc", "_FEED_PATH",
     (*_SANDBOX_LOCAL, _SANDBOX_APP_DIR_NAME, "ocr_feed.jsonl")),
    ("puripuly_heart.ocr.overlay_proc", "_PASTE_REQ",
     (*_SANDBOX_LOCAL, _SANDBOX_APP_DIR_NAME, "ocr_paste_req.jsonl")),
    ("puripuly_heart.ocr.overlay_proc", "_BR_REQ",
     (*_SANDBOX_LOCAL, _SANDBOX_APP_DIR_NAME, "ocr_xlat_req.jsonl")),
    ("puripuly_heart.ocr.overlay_proc", "_BR_RES",
     (*_SANDBOX_LOCAL, _SANDBOX_APP_DIR_NAME, "ocr_xlat_res.jsonl")),
    ("puripuly_heart.ocr.overlay_proc", "_SHOT_DIR", ("Desktop", "puripuly_ocr_shots")),
    ("puripuly_heart.ocr.overlay_proc", "_SHOT_TRIGGER",
     (*_SANDBOX_LOCAL, _SANDBOX_APP_DIR_NAME, "ocr_shot_trigger")),
)


def _sandbox_module(name: str, *, do_import: bool):
    """Return an already-imported module (optionally importing it); None when absent."""
    module = sys.modules.get(name)
    if module is None and do_import:
        try:
            module = importlib.import_module(name)
        except Exception:
            return None
    return module


def _apply_profile_sandbox(monkeypatch: pytest.MonkeyPatch, root: Path) -> Path:
    """Point every user-profile derived path at ``root`` for the life of ``monkeypatch``.

    Returns the sandboxed app dir (``root/AppData/Local/puripuly-heart``). Every patch is
    guarded: a missing module or attribute is skipped, never a collection error.
    """
    local = root.joinpath(*_SANDBOX_LOCAL)
    roaming = root / "AppData" / "Roaming"
    local.mkdir(parents=True, exist_ok=True)
    roaming.mkdir(parents=True, exist_ok=True)
    # ONE central lever: config.paths.user_config_dir() reads LOCALAPPDATA/APPDATA at
    # call time and every expanduser("~") / Path.home() site reads USERPROFILE (ntpath
    # consults it first). HOME only matters to non-stdlib code but costs nothing.
    monkeypatch.setenv("USERPROFILE", str(root))
    monkeypatch.setenv("HOME", str(root))
    monkeypatch.setenv("LOCALAPPDATA", str(local))
    monkeypatch.setenv("APPDATA", str(roaming))

    def _sandboxed_user_config_dir(*, app_dir_name: str = _SANDBOX_APP_DIR_NAME) -> Path:
        return local / app_dir_name

    # default_main_log_file() binds the name at import: patch the module-level symbol too.
    runtime_logging = _sandbox_module("puripuly_heart.core.runtime_logging", do_import=True)
    if runtime_logging is not None and hasattr(runtime_logging, "user_config_dir"):
        monkeypatch.setattr(runtime_logging, "user_config_dir", _sandboxed_user_config_dir)

    # The roster tailer is a 1s glob loop over the real VRChat log dir on a daemon thread
    # that nothing in a test ever joins; in the sandbox it would only ever see an empty
    # dir, so make start() a no-op as well (active_names() then reports "VRChat closed").
    roster = _sandbox_module("puripuly_heart.core.vrchat_roster", do_import=True)
    if roster is not None and hasattr(getattr(roster, "VRChatRoster", None), "start"):
        monkeypatch.setattr(roster.VRChatRoster, "start", lambda self: None)

    for module_name, attr, parts in _SANDBOX_MODULE_CONSTANTS:
        module = _sandbox_module(module_name, do_import=False)
        if module is not None and hasattr(module, attr):
            monkeypatch.setattr(module, attr, str(root.joinpath(*parts)))
    return local / _SANDBOX_APP_DIR_NAME


@pytest.fixture(autouse=True, scope="session")
def _session_profile_sandbox(tmp_path_factory: pytest.TempPathFactory):
    """Session-wide floor so module/class-scoped fixtures never see the live profile."""
    with pytest.MonkeyPatch.context() as mp:
        _apply_profile_sandbox(mp, tmp_path_factory.mktemp("profile-sandbox"))
        yield


@pytest.fixture(autouse=True)
def _profile_sandbox(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Per-test sandboxed profile; yields the sandboxed app dir for tests that want it."""
    return _apply_profile_sandbox(monkeypatch, tmp_path / "home")


@pytest.fixture(autouse=True)
def _reset_local_qwen_shared_recognizer_cache():
    """r386: the recognizer cache is module-level so both channels can share
    one 1.1GB instance. In tests that means the FIRST test's fake recognizer
    would be served to every later test with the same config key — 14
    pre-existing provider tests failed exactly that way. Reset around every
    test; no test file should have to know the cache exists."""
    try:
        from puripuly_heart.providers.stt.local_qwen_sherpa import (
            _reset_shared_local_qwen_recognizers,
        )
    except Exception:
        yield
        return
    _reset_shared_local_qwen_recognizers()
    yield
    _reset_shared_local_qwen_recognizers()
