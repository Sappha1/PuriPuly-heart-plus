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
    body = _body(DASHBOARD, "def _open_speaker_name_dialog", 11000)
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


def test_a_recognised_line_is_clickable_without_a_session_cluster() -> None:
    """r339: match() returns cluster_id -1 for a pure voiceprint hit, so those
    lines had on_tap=None — the name was shown with no way to rename it."""
    body = _body(DASHBOARD, "on_tap=(", 700)
    assert "speaker_cluster_id >= 0 or speaker_name" in body
    assert "known_name=known" in body


def test_clusterless_named_tags_are_registered_for_relabelling() -> None:
    """They were keyed nowhere, so the r338 rename silently skipped them."""
    assert "_speaker_tag_controls_by_name" in DASHBOARD
    relabel = _body(DASHBOARD, "def _retro_label_speaker_tags", 2600)
    assert "_speaker_tag_controls_by_name" in relabel


def test_enrolling_still_requires_a_real_cluster() -> None:
    """Renaming works from a name alone; enrolling a voiceprint does not —
    there is no centroid to store for cluster -1."""
    body = _body(DASHBOARD, "def _open_speaker_name_dialog", 11000)
    assert "callable(enroll) and cluster_id >= 0" in body


def test_the_dialog_takes_the_name_from_the_rendered_line() -> None:
    body = _body(DASHBOARD, "def _open_speaker_name_dialog", 1200)
    assert "known_name: str = \"\"" in body
    assert "if not known_name and callable(lookup)" in body


def test_dialog_offers_scope_and_warns_before_merging() -> None:
    """r341: renaming one misidentified line must not silently sweep the real
    person, and typing an existing name must not silently merge two people."""
    body = _body(DASHBOARD, "def _open_speaker_name_dialog", 11000)
    assert "scope_all" in body and "scope_one" in body
    assert "merge_warning" in body
    assert "merge_button" in body
    # "only this speaker" detaches BEFORE enrolling, so the named person's
    # voiceprints are never part of the correction
    assert body.index("on_detach_speaker_cluster") < body.index(
        "on_enroll_speaker"
    )


def test_dialog_states_the_blast_radius() -> None:
    body = _body(DASHBOARD, "def _open_speaker_name_dialog", 11000)
    assert "_messages_showing_name" in body


def test_manager_requires_a_second_save_to_merge() -> None:
    body = _body(SETTINGS, "def _voice_row", 3200)
    assert "on_voice_name_taken" in body
    assert "pending_merge" in body
    assert "merge_confirm" in body


def test_manager_offers_undo() -> None:
    body = _body(SETTINGS, "def _on_saved_voices_click", 6500)
    assert "on_can_undo_voice_edit" in body
    assert "on_undo_voice_edit" in body


def test_app_wires_the_r341_guards() -> None:
    for attr in (
        "view_dashboard.on_speaker_variant_count",
        "view_dashboard.on_detach_speaker_cluster",
        "view_settings.on_voice_name_taken",
        "view_settings.on_can_undo_voice_edit",
        "view_settings.on_undo_voice_edit",
    ):
        assert attr in APP, attr


def test_controller_snapshots_before_destructive_edits() -> None:
    rename = _body(CONTROLLER, "def rename_speaker", 900)
    forget = _body(CONTROLLER, "def forget_speaker", 700)
    assert "_capture_speaker_snapshot" in rename
    assert "_capture_speaker_snapshot" in forget
    # slots=True: the field must be DECLARED or assignment crashes at runtime
    assert "_speaker_undo_snapshot: object | None = None" in CONTROLLER


def test_dialog_options_wrap_instead_of_clipping() -> None:
    """r342: single-line radio labels were cut off at the dialog edge; each
    option is now a short label plus a wrapping grey description."""
    body = _body(DASHBOARD, "def _open_speaker_name_dialog", 11000)
    assert "_scope_option" in body
    assert "scope_all_desc" in body and "scope_one_desc" in body
    assert body.count("no_wrap=False") >= 2


def test_dialog_offers_a_screenshot_only_relabel() -> None:
    """r342: "only this message" repaints exactly the clicked line and saves
    nothing — no enroll, no rename, no registry writes."""
    body = _body(DASHBOARD, "def _open_speaker_name_dialog", 11000)
    assert "scope_message" in body
    message_branch = body[body.index('if scope.visible and scope.value == "message"'):]
    message_branch = message_branch[: message_branch.index("return") + 6]
    assert "tag_control.value = name" in message_branch
    assert "on_enroll_speaker" not in message_branch
    assert "on_rename_speaker" not in message_branch
    # and it can never present as a merge
    assert 'scope.value == "message"' in _body(
        DASHBOARD, "def _refresh_merge_state", 900
    )


def test_clicked_tag_is_passed_into_the_dialog() -> None:
    assert "tag_control=tag" in DASHBOARD


def test_scope_strings_are_translated_everywhere() -> None:
    from pathlib import Path

    for locale in LOCALES:
        data = json.loads(
            Path(f"src/puripuly_heart/data/i18n/{locale}.json").read_text(
                encoding="utf-8"
            )
        )
        for key in (
            "dashboard.speaker_name_dialog.scope_all",
            "dashboard.speaker_name_dialog.scope_all_desc",
            "dashboard.speaker_name_dialog.scope_one",
            "dashboard.speaker_name_dialog.scope_one_desc",
            "dashboard.speaker_name_dialog.scope_message",
            "dashboard.speaker_name_dialog.scope_message_desc",
        ):
            assert data.get(key), f"{locale}: {key}"
        assert "{name}" in data["dashboard.speaker_name_dialog.scope_one"]
        assert "{count}" in data["dashboard.speaker_name_dialog.scope_all_desc"]


def test_dialog_links_to_the_voice_manager() -> None:
    """r345: "like a link to manage the names in the prompt"."""
    body = _body(DASHBOARD, "def _open_speaker_name_dialog", 13000)
    assert "manage_link" in body
    assert "on_open_voice_manager" in body
    assert "view_dashboard.on_open_voice_manager" in APP
    # the manager can open on a page the settings view is not mounted on
    assert "_on_saved_voices_click(page=self.page)" in APP
    assert "def _on_saved_voices_click(self, e=None, *, page=None)" in SETTINGS


def test_dialog_offers_clearing_the_name() -> None:
    body = _body(DASHBOARD, "def _open_speaker_name_dialog", 13000)
    assert "scope_clear" in body
    clear_branch = body[body.index('if scope.visible and scope.value == "clear"'):]
    clear_branch = clear_branch[: clear_branch.index("return") + 6]
    assert "on_detach_speaker_cluster" in clear_branch
    assert "dashboard.speaker_n" in clear_branch
    assert "on_enroll_speaker" not in clear_branch
    assert "on_rename_speaker" not in clear_branch
    # clearing must run BEFORE the typed-name early-outs
    assert body.index('scope.value == "clear"') < body.index(
        "if not name or name == known_name"
    )


def test_deleting_a_voice_clears_the_rendered_log() -> None:
    """r345: "deleting the name should clear it too"."""
    handler = _body(APP, "def _on_forget_saved_voice", 900)
    # cluster list captured BEFORE forget() clears the session map
    assert handler.index("speaker_clusters_for_name") < handler.index(
        "forget_speaker"
    )
    assert "clear_speaker_name_tags" in handler
    assert "def clear_speaker_name_tags" in DASHBOARD
