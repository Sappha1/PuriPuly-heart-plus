"""r317: launches land on the newest build; metadata survives blocked GitHub."""
from __future__ import annotations

from puripuly_heart.core.updater import (
    GITHUB_REPO,
    RELEASE_TAG,
    _remote_build_from_version_payload,
)
from puripuly_heart.ui.update_flow import (
    LAUNCH_AUTO_APPLY_WINDOW_S,
    should_auto_apply_on_launch,
)


def test_version_payload_synthesizes_asset_urls() -> None:
    remote = _remote_build_from_version_payload(
        {"build": 317, "tag": "r317", "date": "2026-07-30", "notes": ["a", "b"]}
    )
    assert remote is not None
    assert remote.build == 317 and remote.tag == "r317"
    assert remote.notes == ("a", "b") and remote.date == "2026-07-30"
    assert remote.zip_url == (
        f"https://github.com/{GITHUB_REPO}/releases/download/"
        f"{RELEASE_TAG}/updater-payload-internal.zip"
    )
    assert remote.zip_size == 0  # unknown via mirror — tooltip omits MB


def test_version_payload_rejects_garbage() -> None:
    assert _remote_build_from_version_payload(None) is None
    assert _remote_build_from_version_payload([]) is None
    assert _remote_build_from_version_payload({}) is None
    assert _remote_build_from_version_payload({"build": "x"}) is None
    assert _remote_build_from_version_payload({"build": 0}) is None


def test_auto_apply_window() -> None:
    assert should_auto_apply_on_launch(0.0)
    assert should_auto_apply_on_launch(LAUNCH_AUTO_APPLY_WINDOW_S - 1)
    assert not should_auto_apply_on_launch(LAUNCH_AUTO_APPLY_WINDOW_S + 1)
    assert not should_auto_apply_on_launch(-5.0)


def test_current_build_top_notes_reads_latest_section() -> None:
    from puripuly_heart.core.updater import current_build_top_notes

    summary = current_build_top_notes()
    assert summary                                  # non-empty
    assert not summary.startswith("#")              # no headers leaked
    assert len(summary) <= 220


def test_last_run_build_roundtrip() -> None:
    from puripuly_heart.config.settings import (
        from_dict,
        new_settings_for_first_run,
        to_dict,
    )

    settings = new_settings_for_first_run("en-US")
    assert settings.ui.last_run_build == 0
    settings.ui.last_run_build = 322
    loaded = from_dict(to_dict(settings))
    assert loaded.ui.last_run_build == 322
