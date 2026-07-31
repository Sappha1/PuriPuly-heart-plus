"""r335: a settings label must say what the setting DOES.

The user's complaint was concrete: "it would be nice if people didn't have to
highlight a tooltip to find out what it does because of bad naming." These
tests pin the renamed labels and block the two habits that caused it —
undefined jargon, and bare nouns that name a concept instead of an effect.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

LOCALES = ["en", "ja", "ko", "zh-CN"]

# Labels that were jargon or a bare noun before r335, with what they became.
RENAMED = {
    "settings.vrc_mic_intercept": "Sync mute with VRChat",
    "settings.ptt_mute_sync": "Push-to-talk support",
    "settings.live_preview": "Live preview",
    "settings.chatbox_send_peer": "Send their speech to the VRChat chatbox",
    "settings.separate_text_translation": "Separate 'Text Translation' box on the dashboard",
    "settings.audio_host_api": "Audio host",
    "settings.section.self_vad_sensitivity": "Mic sensitivity",
    "settings.section.peer_vad_sensitivity": "Their voice sensitivity",
    "settings.mic_auto_gain": "Mic auto-gain",
    "settings.mic_denoise": "Mic noise suppression",
    "settings.peer_auto_gain": "Their volume auto-gain",
    "settings.overlay.show_peer_original": "Show their original text",
    "settings.overlay.single_turn_mode": "Show one message at a time",
    "settings.overlay.calibration.offset_x": "Horizontal position",
    "settings.overlay.calibration.offset_y": "Vertical position",
    "settings.filter_peer_by_target_languages": "Only translate my target languages",
}

# Terms no user of a VRChat translator is expected to know. "API"/"OpenRouter"
# survive on the API tab, where the audience is deliberately technical.
# r336: "sensitivity", "auto-gain", "noise suppression" and "audio host" are
# what Discord/OBS/Audacity call these — plain-English rewrites of them read
# as amateur. Only terms with no consumer-facing equivalent stay banned.
JARGON = ("VAD", "Loopback", "Intercept", "Single Turn", "Offset X", "Offset Y")


def _i18n(locale: str) -> dict:
    return json.loads(
        Path(f"src/puripuly_heart/data/i18n/{locale}.json").read_text(encoding="utf-8")
    )


def test_english_labels_state_the_effect() -> None:
    data = _i18n("en")
    for key, expected in RENAMED.items():
        assert data.get(key) == expected, f"{key} drifted from its r335 wording"


@pytest.mark.parametrize("locale", LOCALES)
def test_every_renamed_label_is_translated(locale: str) -> None:
    data = _i18n(locale)
    english = _i18n("en")
    for key in RENAMED:
        value = data.get(key)
        assert value, f"{locale}: {key} missing"
        if locale != "en":
            assert value != english[key], f"{locale}: {key} left in English"


def test_no_unexplained_jargon_in_settings_labels() -> None:
    """Grandfathered leftovers are listed explicitly, so adding new jargon fails."""
    data = _i18n("en")
    allowed = {
        # API tab — technical by design.
        "settings.local_llm.connection",
        "settings.log_api_requests",
        "settings.section.api_keys",
    }
    offenders = [
        f"{key} = {value!r}"
        for key, value in data.items()
        if key.startswith("settings.")
        and key not in allowed
        and isinstance(value, str)
        and not key.endswith((".tooltip", ".hint", ".description"))
        and any(term in value for term in JARGON)
    ]
    assert not offenders, "settings labels contain jargon: " + "; ".join(offenders)
