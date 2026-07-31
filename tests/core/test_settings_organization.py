"""r334: Settings tabs are organized by what each setting affects, and the
duplicated 'my messages in the overlay' switch is a single setting again."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

LOCALES = ["en", "ja", "ko", "zh-CN"]
SOURCE = Path("src/puripuly_heart/ui/views/settings.py").read_text(encoding="utf-8")


def _i18n(locale: str) -> dict:
    return json.loads(
        Path(f"src/puripuly_heart/data/i18n/{locale}.json").read_text(encoding="utf-8")
    )


def test_tab_order_includes_the_new_tabs() -> None:
    from puripuly_heart.ui.views.settings import _SETTINGS_SUBTAB_ORDER

    # r336: Prompt dissolved — the system prompt moved under the API tab's
    # model pickers and custom vocabulary under Audio's speech engine, so each
    # sits with the choice that decides whether it does anything.
    assert _SETTINGS_SUBTAB_ORDER == (
        "general", "audio", "vrchat", "api", "overlay",
    )


@pytest.mark.parametrize("locale", LOCALES)
def test_every_tab_and_section_label_is_translated(locale: str) -> None:
    from puripuly_heart.ui.views.settings import _SETTINGS_SUBTAB_ORDER

    data = _i18n(locale)
    for key in _SETTINGS_SUBTAB_ORDER:
        assert data.get(f"settings.subtab.{key}"), f"{locale}: subtab.{key}"
    for key in (
        "settings.section.updates",
        "settings.section.audio_devices",
        "settings.section.voice_detection",
        "settings.section.voice_processing",
        "settings.section.vrchat",
    ):
        assert data.get(key), f"{locale}: {key}"


def test_audio_cards_left_the_general_catch_all() -> None:
    """The audio cards must live in the Audio tab's rows, not General's."""
    audio_block = SOURCE[SOURCE.index("audio_devices_row = ft.Column") : SOURCE.index('"vrchat": [vrchat_row]')]
    for card in (
        "host_api_card", "mic_audio_card", "loopback_audio_card",
        "microphone_test_card", "self._self_vad_card", "self._peer_vad_card",
        "self._mic_auto_gain_card", "self._mic_denoise_card",
        "self._auto_gain_card", "self._speaker_id_card",
    ):
        assert card in audio_block, f"{card} missing from the Audio tab"


def test_vrchat_cards_are_grouped() -> None:
    block = SOURCE[SOURCE.index("vrchat_row = ft.Column") : SOURCE.index("vrchat_row = ft.Column") + 900]
    for card in (
        "vrc_mic_card", "ptt_mute_sync_card", "live_preview_card",
        "chatbox_send_peer_card", "steamvr_autolaunch_card",
    ):
        assert card in block, f"{card} missing from the VRChat tab"


def test_self_in_overlay_duplicate_card_is_gone() -> None:
    """r334: General had 'Show My Messages in Overlay' (ui.self_in_overlay)
    while Overlay had 'Show self' (overlay.show_self) — two stores, one
    behaviour. Only the Overlay card may remain, and it writes both."""
    # the General row no longer references the duplicate card
    general_rows = SOURCE[SOURCE.index('"general": [') : SOURCE.index('"audio": [')]
    assert "self_in_overlay_card" not in general_rows

    handler = SOURCE[SOURCE.index("def _on_overlay_show_self_click") :][:900]
    assert "self._settings.overlay.show_self" in handler
    assert "self._settings.ui.self_in_overlay" in handler


def test_settings_load_reconciles_the_two_stores(tmp_path) -> None:
    """Effective behaviour was the AND of both flags; migration must preserve
    what the user actually experienced, then keep them locked."""
    from puripuly_heart.config.settings import (
        load_settings,
        new_settings_for_first_run,
        save_settings,
    )

    for ui_value, overlay_value, expected in (
        (True, True, True),
        (True, False, False),   # presenter hid it -> user saw nothing
        (False, True, False),   # hub gated it -> user saw nothing
        (False, False, False),
    ):
        settings = new_settings_for_first_run("en-US")
        settings.ui.self_in_overlay = ui_value
        settings.overlay.show_self = overlay_value
        path = tmp_path / f"settings_{ui_value}_{overlay_value}.json"
        save_settings(path, settings)

        loaded = load_settings(path)
        assert loaded.ui.self_in_overlay is expected
        assert loaded.overlay.show_self is expected
