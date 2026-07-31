"""r338: the rename has to reach the rendered chat entries, and the saved
voices must be editable without waiting for that person to speak again.

These are source-level contracts: the pieces live in three files (registry,
controller, two views) and the bug was a missing hop between them, not a bad
algorithm.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

DASHBOARD = Path("src/puripuly_heart/ui/views/dashboard.py").read_text(encoding="utf-8")
SETTINGS = Path("src/puripuly_heart/ui/views/settings.py").read_text(encoding="utf-8")
APP = Path("src/puripuly_heart/ui/app.py").read_text(encoding="utf-8")
CONTROLLER = Path("src/puripuly_heart/ui/controller.py").read_text(encoding="utf-8")

LOCALES = ["en", "ja", "ko", "zh-CN"]


def _body(source: str, marker: str, length: int = 1800) -> str:
    return source[source.index(marker) : source.index(marker) + length]


def test_relabelling_collects_every_cluster_of_the_person() -> None:
    body = _body(DASHBOARD, "def _retro_label_speaker_tags")
    # it must ask which clusters share the name rather than trusting the one
    # cluster_id it was handed
    assert "on_speaker_clusters_for_name" in body
    assert "also_named" in body


def test_naming_a_known_voice_renames_instead_of_re_enrolling() -> None:
    body = _body(DASHBOARD, "def _open_speaker_name_dialog", 3000)
    assert "on_rename_speaker" in body
    # the old name is passed through so its other clusters follow
    assert "also_named=known_name" in body


def test_app_wires_rename_and_cluster_lookup() -> None:
    assert "view_dashboard.on_rename_speaker" in APP
    assert "view_dashboard.on_speaker_clusters_for_name" in APP
    assert "view_settings.on_list_saved_voices" in APP
    assert "view_settings.on_rename_saved_voice" in APP
    assert "view_settings.on_forget_saved_voice" in APP


def test_settings_rename_also_relabels_the_log() -> None:
    """Renaming from Settings must repaint entries already on screen."""
    body = _body(APP, "def _on_rename_saved_voice")
    assert "rename_speaker" in body
    assert "_retro_label_speaker_tags" in body


def test_controller_exposes_the_registry_operations() -> None:
    for method in (
        "def rename_speaker",
        "def forget_speaker",
        "def enrolled_speakers",
        "def speaker_clusters_for_name",
    ):
        assert method in CONTROLLER, method


def test_saved_voices_card_is_mounted_next_to_speaker_identification() -> None:
    assert "self._saved_voices_card," in SETTINGS
    row = _body(SETTINGS, "audio_processing_row = ft.Column", 400)
    assert "self._speaker_id_card," in row
    assert "self._saved_voices_card," in row


def test_manager_offers_rename_and_remove() -> None:
    body = _body(SETTINGS, "def _on_saved_voices_click", 4000)
    assert "on_rename_saved_voice" in body
    assert "on_forget_saved_voice" in body
    assert "on_list_saved_voices" in body


@pytest.mark.parametrize("locale", LOCALES)
def test_saved_voices_strings_are_translated(locale: str) -> None:
    data = json.loads(
        Path(f"src/puripuly_heart/data/i18n/{locale}.json").read_text(encoding="utf-8")
    )
    for key in (
        "settings.saved_voices",
        "settings.saved_voices.manage",
        "settings.saved_voices.tooltip",
        "settings.saved_voices.detail",
        "settings.saved_voices.remove",
        "settings.saved_voices.empty",
    ):
        assert data.get(key), f"{locale}: {key}"
    assert "{variants}" in data["settings.saved_voices.detail"]
    assert "{heard}" in data["settings.saved_voices.detail"]
