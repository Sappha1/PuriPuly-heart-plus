"""r332: the version label is a menu (Check for updates / What's new)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

LOCALES = ["en", "ja", "ko", "zh-CN"]


def _i18n(locale: str) -> dict:
    path = Path(f"src/puripuly_heart/data/i18n/{locale}.json")
    return json.loads(path.read_text(encoding="utf-8"))


@pytest.mark.parametrize("locale", LOCALES)
def test_title_menu_strings_exist_in_every_locale(locale: str) -> None:
    data = _i18n(locale)
    for key in (
        "dashboard.tooltip.title_menu",
        "dashboard.title_menu.check_updates",
        "dashboard.title_menu.changelog",
        "app.update_check.up_to_date",
        "app.update_check.available",
        "app.update_check.failed",
        "app.updated_dialog.hide_future",
    ):
        assert data.get(key), f"{locale} missing {key}"
    assert "{reason}" in data["app.update_check.failed"]


@pytest.mark.parametrize("locale", LOCALES)
def test_optout_label_names_what_it_hides(locale: str) -> None:
    """r332: 'Don't show this after updates' was vague — the label must name
    the thing it suppresses (what's new / 更新内容 / 新機能 / 새로운 기능)."""
    label = _i18n(locale)["app.updated_dialog.hide_future"]
    assert "this" not in label.lower()
    subject = {
        "en": "what's new",
        "ja": "新機能",
        "ko": "새로운 기능",
        "zh-CN": "更新内容",
    }[locale]
    assert subject in label


def test_changelog_menu_opens_the_full_view_not_a_dialog() -> None:
    """r333: 'Changelog' must navigate to the About page's full list of every
    build ("show the full change log like the one in settings"), not open the
    single-build notes dialog."""
    app_source = Path("src/puripuly_heart/ui/app.py").read_text(encoding="utf-8")
    start = app_source.index("def _title_menu_show_changelog")
    body = app_source[start:start + 700]
    # r335: opens the changelog WINDOW itself. Navigating to the info page only
    # got the user to the page the button lives on, not to the changelog.
    assert "_open_changelog_dialog()" in body
    assert "_on_nav_change(3)" not in body
    assert "open_update_notes_dialog" not in body


def test_dashboard_exposes_title_menu_handlers() -> None:
    """The menu must call out through the documented callback attributes."""
    source = Path("src/puripuly_heart/ui/views/dashboard.py").read_text(encoding="utf-8")
    assert "_on_title_menu_tap" in source
    assert "on_check_updates" in source
    assert "on_show_changelog" in source
    assert "self._sidebar_title_button" in source

    app_source = Path("src/puripuly_heart/ui/app.py").read_text(encoding="utf-8")
    assert "view_dashboard.on_check_updates" in app_source
    assert "view_dashboard.on_show_changelog" in app_source


def test_title_menu_lists_changelog_before_check_for_updates() -> None:
    """r334: reading what changed is the common reason to open this menu."""
    source = Path("src/puripuly_heart/ui/views/dashboard.py").read_text(encoding="utf-8")
    body = source[source.index("def _on_title_menu_tap") :]
    body = body[: body.index("def _invoke_optional")]
    assert body.index("title_menu.changelog") < body.index("title_menu.check_updates")
