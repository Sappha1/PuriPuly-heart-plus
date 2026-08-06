"""r377: labels that stayed English because nobody wired them to apply_locale.

Reported from a Chinese UI showing English for "Show what's new after updates",
"Mic auto-gain", "Mic noise suppression", "Their volume auto-gain", "Speaker
identification", "Manage" and "Find in chat".

None of it was a missing translation — every one of those keys exists and is
translated in all four locales. The views are BUILT before the saved locale is
applied, so every label starts life in English and only the ones apply_locale
touches get corrected. apply_locale works off hand-maintained lists, so a row
nobody remembered to add stays English for the whole session.

That is not a bug a person can be trusted to avoid by being careful, which is
why these tests enumerate the controls from the source rather than listing the
ones somebody happened to think of.
"""
from __future__ import annotations

import io
import json
import re
from pathlib import Path

SETTINGS = Path("src/puripuly_heart/ui/views/settings.py")
DASHBOARD = Path("src/puripuly_heart/ui/views/dashboard.py")
I18N = Path("src/puripuly_heart/data/i18n")
LOCALES = ("en", "zh-CN", "ja", "ko")

# Controls apply_locale refreshes by re-running the setter that owns them,
# rather than by assigning a key. They hold device NAMES or live state, so
# forcing them to a key would replace the user's chosen microphone with the
# word "Default". Each must name the setter, and that setter must be called.
INDIRECT = {
    "_desktop_overlay_status_title": "_sync_desktop_overlay_status_control",
    "_mic_audio_text": "_sync_general_audio_card_texts",
    "_audio_host_api_text": "_sync_general_audio_card_texts",
    "_loopback_audio_text": "_sync_general_audio_card_texts",
    "_managed_key_referral_helper_text": "_sync_managed_key_referral_row_value",
    "_managed_key_referral_id_value": "_sync_managed_key_referral_row_value",
    "_openrouter_fallback_helper_text": "_sync_openrouter_fallback_card",
    "_request_format_helper": "_sync_prompt_tab_copy",
}


def _split_settings() -> tuple[str, str]:
    source = SETTINGS.read_text(encoding="utf-8")
    at = source.index("def apply_locale")
    return source[:at], source[at:]


def _locale(code: str) -> dict:
    return json.load(io.open(I18N / f"{code}.json", encoding="utf-8"))


def test_every_settings_title_is_re_translated() -> None:
    build, refresh = _split_settings()
    # EVERY Text built from a key, not just the ones named "*title*" — the
    # helper lines under a row go stale the same way and are just as visible.
    titles = re.findall(
        r'self\.(_\w+)\s*=\s*ft\.Text\(\s*\n?\s*t\("([^"]+)"\)', build
    )
    assert len(titles) > 40, "the label-building pattern moved; recount it"

    stranded = []
    for attr, _key in titles:
        if attr in refresh:
            continue
        setter = INDIRECT.get(attr)
        if setter and f"self.{setter}()" in refresh:
            continue
        stranded.append(attr)

    assert not stranded, (
        f"these settings titles are never re-translated, so they stay in the "
        f"language the view was built in — English — however the UI language is "
        f"set: {stranded}"
    )


def test_every_settings_value_label_is_re_translated() -> None:
    build, refresh = _split_settings()
    values = re.findall(
        r'self\.(_\w+_text)\s*=\s*self\._build_clickable_text\(\s*\n?\s*t\("([^"]+)"\)',
        build,
    )
    assert len(values) > 15, "the value-building pattern moved; recount it"

    stranded = []
    for attr, _key in values:
        if attr in refresh:
            continue
        setter = INDIRECT.get(attr)
        if setter and f"self.{setter}()" in refresh:
            continue
        stranded.append(attr)

    assert not stranded, (
        f"these On/Off value labels are never re-translated — they are only set "
        f"when the value CHANGES, and switching language is not a value change: "
        f"{stranded}"
    )


def test_the_indirect_setters_are_actually_called() -> None:
    """The allowance above is only honest if apply_locale really calls them."""
    _build, refresh = _split_settings()
    for attr, setter in INDIRECT.items():
        assert f"self.{setter}()" in refresh, (
            f"{attr} is excused because {setter} supposedly refreshes it, but "
            f"apply_locale never calls {setter}"
        )


def test_the_find_bar_follows_the_ui_language() -> None:
    """r377: it was built with the dashboard, which happens before the saved
    locale is applied, so its placeholder read "Find in chat" on a Chinese UI."""
    source = DASHBOARD.read_text(encoding="utf-8")
    at = source.index("def apply_locale")
    refresh = source[at:]

    assert "_find_field" in refresh, "the find placeholder never re-translates"
    assert "_find_buttons" in refresh, "the find button tooltips never re-translate"
    assert "self._find_buttons.append(" in source[:at], (
        "the find buttons are not recorded, so apply_locale has nothing to walk"
    )


def test_every_locale_has_every_key() -> None:
    en = _locale("en")
    for code in LOCALES[1:]:
        missing = sorted(set(en) - set(_locale(code)))
        assert not missing, f"{code} is missing {len(missing)} keys: {missing[:8]}"


def test_the_strings_from_the_report_are_translated_everywhere() -> None:
    """The exact labels seen in English on a Chinese UI. Their keys existed and
    were translated all along — the wiring was the fault — so this guards the
    other direction: that nobody later deletes the translations."""
    keys = (
        "settings.update_notes",
        "settings.speaker_id",
        "settings.mic_auto_gain",
        "settings.mic_denoise",
        "settings.peer_auto_gain",
        "settings.saved_voices.manage",
        "dashboard.find.hint",
        "dashboard.find.prev",
        "dashboard.find.next",
        "dashboard.find.close",
        "common.on",
        "common.off",
    )
    en = _locale("en")
    for code in ("zh-CN", "ja", "ko"):
        data = _locale(code)
        untranslated = [k for k in keys if data.get(k, "").strip() == en[k].strip()]
        assert not untranslated, (
            f"{code} still shows the English text for: {untranslated}"
        )


def test_untranslated_values_are_only_proper_nouns() -> None:
    """A value identical to English is usually fine — DeepL, VRChat, API, VR and
    "Gemma 4 26B A4B" are the same in every language. This fails only when a
    real SENTENCE goes untranslated, which is what a missing translation
    actually looks like.

    Product and person names live in namespaces of their own, and a sentence is
    detected by the English function words a name never contains.
    """
    NAME_NAMESPACES = ("provider.", "about.special_thanks.", "settings.audio_host_api.option.")
    FUNCTION_WORDS = {
        "the", "a", "an", "to", "is", "are", "when", "your", "you", "and",
        "for", "of", "in", "with", "this", "that", "it", "on", "off", "not",
    }
    en = _locale("en")
    for code in ("zh-CN", "ja", "ko"):
        data = _locale(code)
        suspicious = [
            k
            for k, v in en.items()
            if data.get(k) == v
            and str(v).strip()
            and not k.startswith(NAME_NAMESPACES)
            and FUNCTION_WORDS & {w.strip(".,:;!?").lower() for w in str(v).split()}
        ]
        assert not suspicious, (
            f"{code} leaves these multi-word strings in English: {suspicious}"
        )
