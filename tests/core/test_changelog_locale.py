"""r324: the changelog renders in the UI language where translated."""
from __future__ import annotations

from puripuly_heart.core.changelog import (
    changelog_sections,
    current_build_notes_localized,
)


def _has_cjk(text: str) -> bool:
    return any("一" <= ch <= "鿿" or "぀" <= ch <= "ヿ" or "가" <= ch <= "힯" for ch in text)


def test_english_default_structure() -> None:
    sections = changelog_sections("en-US")
    assert sections
    assert sections[0][1]  # newest section has bullets


def test_zh_sections_are_translated_with_same_structure() -> None:
    english = changelog_sections("en-US")
    chinese = changelog_sections("zh-CN")
    assert len(english) == len(chinese)          # merge preserves section order
    assert _has_cjk(chinese[0][1][0])            # newest bullet is Chinese
    assert not _has_cjk(english[0][1][0].replace("怪", "").replace("来了", "").replace("变回", "").replace("。", ""))


def test_ja_ko_newest_sections_translated() -> None:
    for locale in ("ja-JP", "ko-KR"):
        sections = changelog_sections(locale)
        assert _has_cjk(sections[0][1][0]) or any(
            "가" <= ch <= "힯" for ch in sections[0][1][0]
        )


def test_old_sections_fall_back_to_english() -> None:
    english = changelog_sections("en-US")
    chinese = changelog_sections("zh-CN")
    # find a section old enough to have no translation (pre-r298)
    for (en_title, en_bullets), (zh_title, zh_bullets) in zip(english, chinese):
        tag = en_title.split(" ")[0]
        if tag.startswith("r") and tag[1:].isdigit() and int(tag[1:]) < 298:
            assert zh_bullets == en_bullets       # untranslated => English
            break
    else:
        raise AssertionError("no pre-r298 section found to test fallback")


def test_dialog_notes_localized() -> None:
    zh_notes = current_build_notes_localized("zh-CN")
    en_notes = current_build_notes_localized("en-US")
    assert zh_notes and en_notes
    assert zh_notes[0] != en_notes[0]
    assert _has_cjk(zh_notes[0])


def test_unknown_locale_falls_back_to_english() -> None:
    assert changelog_sections("fr-FR") == changelog_sections("en")
