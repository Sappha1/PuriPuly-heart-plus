"""r343: the audited hardcoded-English surfaces are keyed and translated.

A four-way audit found 59 UI strings that stayed English with the locale set
to Chinese — the whole About page, dashboard modal titles and tooltips, the
OCR color menu, and a handful of settings tooltips. These pins hold the fix:
every new key exists in all four locales, and the audited literals are gone
from the source.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

LOCALES = ["en", "ja", "ko", "zh-CN"]

NEW_KEYS = (
    "settings.overlay.calibration.distance.tooltip",
    "settings.overlay.calibration.offset_x.tooltip",
    "settings.overlay.calibration.offset_y.tooltip",
    "settings.overlay.desktop.size.option.micro",
    "settings.verify.unavailable",
    "dashboard.tooltip.settings",
    "dashboard.tooltip.expand_sidebar",
    "dashboard.ocr.tooltip",
    "settings_modal.free",
    "dashboard.modal.translator",
    "dashboard.modal.mic_stt",
    "dashboard.modal.peer_stt",
    "dashboard.tooltip.stt_row",
    "dashboard.tooltip.peer_row",
    "dashboard.color.red",
    "dashboard.color.black",
    "update.toolbar.unpackaged",
    "update.changelog.unavailable",
    "dashboard.no_provider_active",
    "logs.api.context_prefix",
    "about.subtitle",
    "about.fork_description",
    "about.providers_title",
    "about.providers_intro",
    "about.acc.high",
    "about.api.free_web",
    "about.provider.qwen_cloud",
    "about.providers_footnote",
)

# Audited literals that must never come back verbatim.
BANISHED = {
    "src/puripuly_heart/ui/views/settings.py": (
        '"How far (in metres)',
        'return "Micro"',
        '"Verification not available"',
    ),
    "src/puripuly_heart/ui/views/dashboard.py": (
        '"Expand sidebar" if collapsed',
        '"Translator",',
        '"Mic (STT)"',
        '"Peer Voice (STT)"',
        'system="Pinyin"',
        '("#ff2020", "Red")',
        'desc = "(free)"',
    ),
    "src/puripuly_heart/ui/views/about.py": (
        '"About this fork"',
        '"Original project by"',
        '"Speech & translation providers"',
        '"Provider", "Accuracy", "API"',
        '"and you!"',
    ),
    "src/puripuly_heart/ui/app.py": (
        '"Available in the packaged app only"',
        '"Changelog unavailable."',
        '"No translation provider is active."',
    ),
}


@pytest.mark.parametrize("locale", LOCALES)
def test_every_r343_key_exists(locale: str) -> None:
    data = json.loads(
        Path(f"src/puripuly_heart/data/i18n/{locale}.json").read_text(encoding="utf-8")
    )
    for key in NEW_KEYS:
        assert data.get(key), f"{locale}: {key}"


def test_non_english_locales_actually_translate() -> None:
    """Keys whose zh value merely repeats the English defeat the point.
    Proper nouns and '(STT)'-style suffixes are the only overlap allowed."""
    en = json.loads(Path("src/puripuly_heart/data/i18n/en.json").read_text(encoding="utf-8"))
    zh = json.loads(Path("src/puripuly_heart/data/i18n/zh-CN.json").read_text(encoding="utf-8"))
    identical = [k for k in NEW_KEYS if zh.get(k) == en.get(k) and k != "about.col.api"]
    assert not identical, f"zh-CN untranslated: {identical}"


@pytest.mark.parametrize("file", sorted(BANISHED))
def test_audited_literals_are_gone(file: str) -> None:
    source = Path(file).read_text(encoding="utf-8")
    for literal in BANISHED[file]:
        assert literal not in source, f"{file} still contains {literal!r}"


def test_provider_tables_evaluate_t_at_build_time() -> None:
    """Class-constant tables froze English at import; the builders must call
    t() so apply_locale rebuilds pick up the active language."""
    source = Path("src/puripuly_heart/ui/views/about.py").read_text(encoding="utf-8")
    assert "_STT_PROVIDER_ROWS = [" not in source
    assert "def _stt_provider_rows" in source
    assert "def _translation_provider_rows" in source
    assert 't("about.acc.high")' in source
