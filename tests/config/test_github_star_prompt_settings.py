from __future__ import annotations

import json

import pytest

from puripuly_heart.config.settings import (
    AppSettings,
    _migrate_settings_dict,
    from_dict,
    load_settings,
    to_dict,
)

PROMPT_UI_DEFAULTS = {
    "github_star_prompt_clicked": False,
    "github_star_prompt_last_shown_at": None,
    "github_star_prompt_show_count": 0,
    "github_star_prompt_translation_success_observed": False,
    "github_star_prompt_eligible_launch_count": 0,
}


def _github_star_prompt_ui_payload(settings: AppSettings) -> dict[str, object]:
    return {
        "github_star_prompt_clicked": settings.ui.github_star_prompt_clicked,
        "github_star_prompt_last_shown_at": settings.ui.github_star_prompt_last_shown_at,
        "github_star_prompt_show_count": settings.ui.github_star_prompt_show_count,
        "github_star_prompt_translation_success_observed": (
            settings.ui.github_star_prompt_translation_success_observed
        ),
        "github_star_prompt_eligible_launch_count": getattr(
            settings.ui,
            "github_star_prompt_eligible_launch_count",
            None,
        ),
    }


def test_github_star_prompt_state_defaults_for_existing_settings(tmp_path) -> None:
    path = tmp_path / "settings.json"
    existing = to_dict(AppSettings())
    for key in PROMPT_UI_DEFAULTS:
        existing["ui"].pop(key, None)
    path.write_text(json.dumps(existing, ensure_ascii=False, indent=2), encoding="utf-8")

    loaded = load_settings(path)

    assert _github_star_prompt_ui_payload(loaded) == PROMPT_UI_DEFAULTS
    serialized_ui = to_dict(loaded)["ui"]
    assert {key: serialized_ui.get(key) for key in PROMPT_UI_DEFAULTS} == PROMPT_UI_DEFAULTS


def test_github_star_prompt_state_round_trips_through_ui_settings() -> None:
    persisted_timestamp = "2026-05-24T12:34:56Z"
    raw = to_dict(AppSettings())
    raw["ui"].update(
        {
            "github_star_prompt_clicked": True,
            "github_star_prompt_last_shown_at": persisted_timestamp,
            "github_star_prompt_show_count": 3,
            "github_star_prompt_translation_success_observed": True,
            "github_star_prompt_eligible_launch_count": 2,
        }
    )

    restored = from_dict(raw)
    serialized = to_dict(restored)

    assert restored.ui.github_star_prompt_clicked is True
    assert restored.ui.github_star_prompt_last_shown_at == persisted_timestamp
    assert restored.ui.github_star_prompt_show_count == 3
    assert restored.ui.github_star_prompt_translation_success_observed is True
    assert getattr(restored.ui, "github_star_prompt_eligible_launch_count", None) == 2
    assert serialized["ui"]["github_star_prompt_clicked"] is True
    assert serialized["ui"]["github_star_prompt_last_shown_at"] == persisted_timestamp
    assert serialized["ui"]["github_star_prompt_show_count"] == 3
    assert serialized["ui"]["github_star_prompt_translation_success_observed"] is True
    assert serialized["ui"].get("github_star_prompt_eligible_launch_count") == 2


@pytest.mark.parametrize(
    "raw_last_shown_at",
    ["not-a-timestamp", "", "2026-05-24T12:34:56", 123],
)
def test_github_star_prompt_invalid_last_shown_at_is_treated_as_never_shown(
    raw_last_shown_at: object,
) -> None:
    settings = from_dict({"ui": {"github_star_prompt_last_shown_at": raw_last_shown_at}})

    assert settings.ui.github_star_prompt_last_shown_at is None
    assert to_dict(settings)["ui"]["github_star_prompt_last_shown_at"] is None


@pytest.mark.parametrize("raw_show_count", [-1, -10])
def test_github_star_prompt_negative_show_count_is_treated_as_zero(
    raw_show_count: int,
) -> None:
    settings = from_dict({"ui": {"github_star_prompt_show_count": raw_show_count}})

    assert settings.ui.github_star_prompt_show_count == 0
    assert to_dict(settings)["ui"]["github_star_prompt_show_count"] == 0


@pytest.mark.parametrize("raw_show_count", [False, 1.0])
def test_github_star_prompt_show_count_migration_normalizes_non_integer_values(
    raw_show_count: object,
) -> None:
    raw = to_dict(AppSettings())
    raw["ui"]["github_star_prompt_show_count"] = raw_show_count

    migrated, changed = _migrate_settings_dict(raw)

    assert changed is True
    assert migrated["ui"]["github_star_prompt_show_count"] == 0
    assert type(migrated["ui"]["github_star_prompt_show_count"]) is int


@pytest.mark.parametrize("raw_show_count", [False, 1.0])
def test_github_star_prompt_show_count_load_normalizes_non_integer_values_on_disk(
    tmp_path,
    raw_show_count: object,
) -> None:
    path = tmp_path / "settings.json"
    raw = to_dict(AppSettings())
    raw["ui"]["github_star_prompt_show_count"] = raw_show_count
    path.write_text(json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8")

    loaded = load_settings(path)
    persisted = json.loads(path.read_text(encoding="utf-8"))

    assert loaded.ui.github_star_prompt_show_count == 0
    assert persisted["ui"]["github_star_prompt_show_count"] == 0
    assert type(persisted["ui"]["github_star_prompt_show_count"]) is int


@pytest.mark.parametrize("raw_launch_count", [-1, -10])
def test_github_star_prompt_negative_eligible_launch_count_is_treated_as_zero(
    raw_launch_count: int,
) -> None:
    settings = from_dict({"ui": {"github_star_prompt_eligible_launch_count": raw_launch_count}})

    assert getattr(settings.ui, "github_star_prompt_eligible_launch_count", None) == 0
    assert to_dict(settings)["ui"].get("github_star_prompt_eligible_launch_count") == 0


@pytest.mark.parametrize("raw_launch_count", [False, 1.0])
def test_github_star_prompt_eligible_launch_count_migration_normalizes_non_integer_values(
    raw_launch_count: object,
) -> None:
    raw = to_dict(AppSettings())
    raw["ui"]["github_star_prompt_eligible_launch_count"] = raw_launch_count

    migrated, changed = _migrate_settings_dict(raw)

    assert changed is True
    assert migrated["ui"].get("github_star_prompt_eligible_launch_count") == 0
    assert type(migrated["ui"].get("github_star_prompt_eligible_launch_count")) is int


def test_github_star_prompt_validation_sanitizes_invalid_prompt_state() -> None:
    settings = AppSettings()
    settings.ui.github_star_prompt_last_shown_at = "not-a-timestamp"
    settings.ui.github_star_prompt_show_count = -7
    if hasattr(settings.ui, "github_star_prompt_eligible_launch_count"):
        settings.ui.github_star_prompt_eligible_launch_count = -7

    settings.validate()

    assert settings.ui.github_star_prompt_last_shown_at is None
    assert settings.ui.github_star_prompt_show_count == 0
    assert getattr(settings.ui, "github_star_prompt_eligible_launch_count", None) == 0
