from __future__ import annotations

from types import SimpleNamespace

import pytest

pytest.importorskip("flet")

from puripuly_heart.ui.components.settings import api_key_field as api_key_field_module
from puripuly_heart.ui.views import settings as settings_view
from tests.helpers.flet_page import DummyPage, attach_dummy_page


def test_api_key_field_uses_legacy_icon_name_api(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeIcon:
        def __init__(self, *, name, color, size, tooltip):
            self.name = name
            self.color = color
            self.size = size
            self.tooltip = tooltip
            self.page = None

        def update(self) -> None:
            return None

    class FakeIconButton:
        def __init__(self, **kwargs):
            self.icon = kwargs.get("icon")

    class FakeTextField:
        def __init__(self, **kwargs):
            self.value = ""
            self.password = kwargs.get("password", False)
            self.page = None

        def update(self) -> None:
            return None

    class FakeRow:
        def __init__(self, *, controls, vertical_alignment):
            self.controls = controls
            self.vertical_alignment = vertical_alignment

    monkeypatch.setattr(api_key_field_module.ft, "Icon", FakeIcon)
    monkeypatch.setattr(api_key_field_module.ft, "IconButton", FakeIconButton)
    monkeypatch.setattr(api_key_field_module.ft, "TextField", FakeTextField)
    monkeypatch.setattr(api_key_field_module.ft, "Row", FakeRow)

    field = api_key_field_module.ApiKeyField(
        "settings.deepgram_api_key",
        "deepgram_api_key",
        "deepgram",
    )
    field._set_status("success")

    assert field._status_icon.name == api_key_field_module.icons.CHECK_CIRCLE_ROUNDED


def test_make_text_button_uses_legacy_text_argument(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, object] = {}

    class FakeTextButton:
        def __init__(self, *, text, **kwargs):
            seen["text"] = text
            seen["kwargs"] = kwargs
            self.text = text

    monkeypatch.setattr(settings_view.ft, "TextButton", FakeTextButton)

    button = settings_view._make_text_button("Gemma 4", style="style")

    assert seen["text"] == "Gemma 4"
    assert seen["kwargs"] == {"style": "style"}
    assert button.text == "Gemma 4"


def test_set_text_button_label_uses_legacy_text_property_only() -> None:
    class FakeButton:
        __slots__ = ("text",)

        def __init__(self) -> None:
            self.text = ""

    button = FakeButton()

    settings_view._set_text_button_label(button, "Managed")

    assert button.text == "Managed"


def test_make_overlay_anchor_dropdown_uses_legacy_on_change(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: dict[str, object] = {}

    class FakeOption:
        def __init__(self, *, key, text):
            self.key = key
            self.text = text

    class FakeDropdown:
        def __init__(self, **kwargs):
            seen.update(kwargs)

    monkeypatch.setattr(settings_view.ft.dropdown, "Option", FakeOption)
    monkeypatch.setattr(settings_view.ft, "Dropdown", FakeDropdown)

    on_change = SimpleNamespace()
    settings_view._make_overlay_anchor_dropdown("center", on_change)

    assert seen["value"] == "center"
    assert seen["on_change"] is on_change
    assert "on_select" not in seen
    assert len(seen["options"]) == len(settings_view.OVERLAY_CALIBRATION_ANCHORS)


class _RaisingPageControl:
    @property
    def page(self):
        raise RuntimeError("not attached")


def test_attach_dummy_page_replaces_unreadable_page_property(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    control = _RaisingPageControl()

    page = attach_dummy_page(monkeypatch, control)

    assert control.page is page
    assert bool(control.page) is True


def test_attach_dummy_page_only_changes_target_control_instance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    control = _RaisingPageControl()
    other_control = _RaisingPageControl()

    page = attach_dummy_page(monkeypatch, control)

    assert control.page is page
    with pytest.raises(RuntimeError, match="not attached"):
        _ = other_control.page


def test_attach_dummy_page_uses_explicit_dummy_page(monkeypatch: pytest.MonkeyPatch) -> None:
    control = _RaisingPageControl()
    page = DummyPage()

    returned = attach_dummy_page(monkeypatch, control, page)
    returned.open("dialog")
    returned.close("dialog")

    assert returned is page
    assert control.page is page
    assert page.opened == ["dialog"]
    assert page.closed == ["dialog"]
