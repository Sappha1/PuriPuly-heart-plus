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


def test_current_build_notes_returns_full_bullets() -> None:
    from puripuly_heart.core.updater import current_build_notes

    bullets = current_build_notes()
    assert bullets                                   # latest section has bullets
    assert all(not b.startswith("#") for b in bullets)
    assert all(len(b) > 10 for b in bullets)         # real sentences, untruncated


def test_load_or_init_settings_sets_fresh_flag(tmp_path) -> None:
    """r326: r325 crashed EVERY launch because settings_created_fresh was
    assigned on a slotted GuiController without a declaration. Exercise the
    real code path against the real class."""
    from puripuly_heart.ui.controller import GuiController

    controller = GuiController.__new__(GuiController)

    fresh_path = tmp_path / "settings.json"
    settings = controller._load_or_init_settings(fresh_path)
    assert settings is not None
    assert controller.settings_created_fresh is True

    again = controller._load_or_init_settings(fresh_path)  # file exists now
    assert again is not None
    assert controller.settings_created_fresh is False


def test_update_machinery_not_nested_under_dead_guard() -> None:
    """r327 tripwire: r317-r325 anchored the launch check, retry ladder, and
    the post-update dialog INSIDE `if not _OCR_PROTO_NO_UPDATES:` (hardcoded
    True) — every one of those features shipped as dead code, and 'auto
    update' only ever worked via the manual Settings button. Fail loudly if
    anything update-related ever moves back under that guard."""
    import ast
    from pathlib import Path

    source = Path("src/puripuly_heart/ui/app.py").read_text(encoding="utf-8")
    tree = ast.parse(source)

    guarded_code = []
    for node in ast.walk(tree):
        if isinstance(node, ast.If):
            test_src = ast.dump(node.test)
            if "_OCR_PROTO_NO_UPDATES" in test_src:
                for child in node.body:
                    guarded_code.append(ast.unparse(child))
    assert guarded_code, "guard vanished — update this test's premise"
    guarded_text = "\n".join(guarded_code)
    for forbidden in (
        "last_run_build",
        "_periodic_update_check",
        "check_silently",
        "updated_dialog",
    ):
        assert forbidden not in guarded_text, (
            f"'{forbidden}' is nested under _OCR_PROTO_NO_UPDATES again — "
            "that branch is dead code (guard is hardcoded True)"
        )
