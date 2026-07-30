"""Locale-aware changelog access (r324).

The canonical changelog stays English (data/CHANGELOG.md — it also feeds the
GitHub release notes). Translated siblings (CHANGELOG.zh-CN.md / .ja.md /
.ko.md) carry the SAME "## rNNN — date" section structure for the sections
that have been translated; anything missing falls back to the English section,
so partially-translated history degrades gracefully instead of hiding changes.

Both the What's New panel and the post-update dialog read through here with
the CURRENT UI locale, so switching language re-renders the changelog in that
language on next open.
"""
from __future__ import annotations

import re
from importlib import resources

_SECTION_TAG_RE = re.compile(r"\br(\d{2,4})\b")

_LOCALE_FILES = {
    "zh": "CHANGELOG.zh-CN.md",
    "ja": "CHANGELOG.ja.md",
    "ko": "CHANGELOG.ko.md",
}


def _read_data_file(name: str) -> str:
    try:
        return (
            resources.files("puripuly_heart.data").joinpath(name).read_text(
                encoding="utf-8"
            )
        )
    except Exception:
        return ""


def _parse_sections(text: str) -> list[tuple[str, list[str]]]:
    """Parse '## heading' + '- bullet' structure into ordered pairs."""
    entries: list[tuple[str, list[str]]] = []
    title: str | None = None
    bullets: list[str] = []
    for line in text.splitlines():
        if line.startswith("## "):
            if title is not None:
                entries.append((title, bullets))
            title, bullets = line[3:].strip(), []
        elif line.startswith("- ") and title is not None:
            bullets.append(line[2:].strip())
    if title is not None:
        entries.append((title, bullets))
    return entries


def _section_tag(heading: str) -> str:
    match = _SECTION_TAG_RE.search(heading)
    return match.group(1) if match else ""


def changelog_sections(locale: str | None) -> list[tuple[str, list[str]]]:
    """English section order, with translated sections substituted when the
    locale has them."""
    english = _parse_sections(_read_data_file("CHANGELOG.md"))
    root = (locale or "en").split("-")[0].split("_")[0].lower()
    filename = _LOCALE_FILES.get(root)
    if not filename:
        return english
    localized = {
        _section_tag(title): (title, bullets)
        for title, bullets in _parse_sections(_read_data_file(filename))
        if _section_tag(title)
    }
    if not localized:
        return english
    merged: list[tuple[str, list[str]]] = []
    for title, bullets in english:
        tag = _section_tag(title)
        if tag and tag in localized and localized[tag][1]:
            merged.append(localized[tag])
        else:
            merged.append((title, bullets))
    return merged


def current_build_notes_localized(
    locale: str | None, max_bullets: int = 6
) -> list[str]:
    """Bullets of the newest section, in the given locale when translated."""
    sections = changelog_sections(locale)
    if not sections:
        return []
    return sections[0][1][:max_bullets]


__all__ = ["changelog_sections", "current_build_notes_localized"]
