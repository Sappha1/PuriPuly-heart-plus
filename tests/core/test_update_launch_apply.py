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


def test_warm_dialog_accepts_single_button() -> None:
    """r328: the update dialog (and r311 advisory) have only Close; the old
    two-label requirement made open_warm_document_dialog raise on EVERY
    launch announcement."""
    from puripuly_heart.ui.components.warm_document_dialog import (
        open_warm_document_dialog,
    )

    class _FakePage:
        def __init__(self):
            self.opened = None

        def open(self, dialog):
            self.opened = dialog

        def close(self, dialog):
            pass

        def update(self):
            pass

    page = _FakePage()
    result = open_warm_document_dialog(
        page,
        body_paragraphs=["Updated to r328", "•  a change"],
        primary_label="Close",
        primary_action=lambda: None,
    )
    assert result.dialog is not None
    assert page.opened is result.dialog


def test_compact_update_dialog_shape() -> None:
    """r329: the announcement dialog must stay small — the 600px warm
    document dialog filled the window for a handful of bullets."""
    import flet as ft

    from puripuly_heart.ui.components.update_notes_dialog import (
        DIALOG_WIDTH,
        MAX_BODY_HEIGHT,
        open_update_notes_dialog,
    )

    class _FakePage:
        def __init__(self):
            self.opened = None

        def open(self, dialog):
            self.opened = dialog

        def close(self, dialog):
            self.opened = None

    assert DIALOG_WIDTH <= 420          # compact, not 600
    page = _FakePage()
    dialog = open_update_notes_dialog(
        page,
        header="What's new in r329",
        bullets=["first change", "second change"],
        close_label="Close",
    )
    card = dialog.content
    assert card.width == DIALOG_WIDTH
    column = card.content
    header_text, _divider, body_container, action_row = column.controls
    assert header_text.value == "What's new in r329"
    assert "✨" not in header_text.value          # no extravagant title
    assert body_container.height <= MAX_BODY_HEIGHT
    assert len(body_container.content.controls) == 2
    # exactly one action, right-aligned, and it closes
    assert dialog.modal is False                 # r331: click-outside closes
    action_row.controls[-1].on_click(None)       # Close is the last control
    assert page.opened is None


def test_long_bullet_gets_enough_height_to_read() -> None:
    # r331: r329 assumed one line per bullet and clipped a long entry
    # mid-sentence ("...failed to match in the other place" was cut off).
    from puripuly_heart.ui.components.update_notes_dialog import (
        MAX_BODY_HEIGHT,
        _estimated_body_height,
    )

    long_bullet = (
        "A named voice can now be recognized across different apps: the same "
        "person sounds measurably different through a voice call versus "
        "VRChat in-game audio (different codec, plus distance and room "
        "effects), so one saved voiceprint often failed to match in the other "
        "place and you had to name them again every session."
    )
    short = _estimated_body_height(["short"])
    long_height = _estimated_body_height([long_bullet])
    assert long_height > short * 4               # scales with wrapped lines
    assert long_height <= MAX_BODY_HEIGHT

    # CJK counted double-width so a Chinese entry is not underestimated
    chinese = "已命名的声音现在可以跨应用被识别：同一个人通过语音通话听起来有明显差异"
    assert _estimated_body_height([chinese]) > _estimated_body_height(
        ["x" * len(chinese)]
    )


def test_dialog_optout_checkbox_reports_choice() -> None:
    # r331: the opt-out lives IN the dialog, not only in Settings.
    from puripuly_heart.ui.components.update_notes_dialog import (
        open_update_notes_dialog,
    )

    class _Page:
        def __init__(self):
            self.opened = None

        def open(self, dialog):
            self.opened = dialog

        def close(self, dialog):
            self.opened = None

    captured = []
    page = _Page()
    dialog = open_update_notes_dialog(
        page,
        header="What is new in r331",
        bullets=["a change"],
        close_label="Close",
        hide_future_label="Do not show this after updates",
        on_hide_future_changed=captured.append,
    )
    action_row = dialog.content.content.controls[3]
    checkbox = action_row.controls[0]
    assert checkbox.label == "Do not show this after updates"

    class _Event:
        control = checkbox

    checkbox.value = True
    checkbox.on_change(_Event())
    assert captured == [True]


def test_show_update_notes_setting_roundtrip() -> None:
    from puripuly_heart.config.settings import (
        from_dict,
        new_settings_for_first_run,
        to_dict,
    )

    settings = new_settings_for_first_run("en-US")
    assert settings.ui.show_update_notes_on_launch is True
    settings.ui.show_update_notes_on_launch = False
    assert from_dict(to_dict(settings)).ui.show_update_notes_on_launch is False
