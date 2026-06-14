from __future__ import annotations

from pathlib import Path

from puripuly_heart.config import paths


def test_user_config_dir_uses_xdg(monkeypatch, tmp_path):
    monkeypatch.setattr(paths.sys, "platform", "linux")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))

    assert paths.user_config_dir(app_dir_name="app") == tmp_path / "app"


def test_default_paths_use_user_config_dir(monkeypatch, tmp_path):
    def _fake_user_config_dir(*, app_dir_name: str = paths.APP_DIR_NAME) -> Path:
        return tmp_path / app_dir_name

    monkeypatch.setattr(paths, "user_config_dir", _fake_user_config_dir)

    assert paths.default_settings_path() == tmp_path / paths.APP_DIR_NAME / paths.SETTINGS_FILENAME
    assert (
        paths.default_vad_model_path() == tmp_path / paths.APP_DIR_NAME / paths.VAD_MODEL_FILENAME
    )


def test_user_config_dir_windows_fallback(monkeypatch):
    monkeypatch.setattr(paths.sys, "platform", "win32")
    monkeypatch.delenv("LOCALAPPDATA", raising=False)
    monkeypatch.delenv("APPDATA", raising=False)

    expected = Path.home() / "AppData" / "Local" / "app"
    assert paths.user_config_dir(app_dir_name="app") == expected


def test_user_config_dir_darwin(monkeypatch):
    monkeypatch.setattr(paths.sys, "platform", "darwin")

    expected = Path.home() / "Library" / "Application Support" / "app"
    assert paths.user_config_dir(app_dir_name="app") == expected


def test_user_config_dir_linux_fallback(monkeypatch):
    monkeypatch.setattr(paths.sys, "platform", "linux")
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)

    expected = Path.home() / ".config" / "app"
    assert paths.user_config_dir(app_dir_name="app") == expected
