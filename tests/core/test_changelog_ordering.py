"""The changelog files must stay newest-first, in every language.

`changelog_sections` parses the file top to bottom and preserves that order, so
the What's New panel and the post-update dialog show whatever happens to be
first. An r384 section was once inserted before r382 — which sits BELOW r383 —
and landed in the middle of the list, where the newest build's notes are the
third thing a reader sees and the panel leads with the previous build.

Anchoring an insert on a neighbouring heading is easy to get wrong and produces
no error, so assert the property instead of trusting the edit.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

DATA = Path("src/puripuly_heart/data")
FILES = [
    "CHANGELOG.md",
    "CHANGELOG.zh-CN.md",
    "CHANGELOG.ja.md",
    "CHANGELOG.ko.md",
]
SECTION = re.compile(r"^## r(\d+) — ", re.MULTILINE)


def _builds(name: str) -> list[int]:
    return [int(m.group(1)) for m in SECTION.finditer((DATA / name).read_text(encoding="utf-8"))]


@pytest.mark.parametrize("name", FILES)
def test_sections_are_newest_first(name: str) -> None:
    builds = _builds(name)
    assert builds, f"{name} has no '## rNNN — ' sections at all"
    out_of_order = [
        (builds[i], builds[i + 1])
        for i in range(len(builds) - 1)
        if builds[i] <= builds[i + 1]
    ]
    assert not out_of_order, (
        f"{name} is not newest-first: {out_of_order}. The newest build's notes "
        f"must be the first section — that is what the What's New panel shows"
    )


@pytest.mark.parametrize("name", FILES)
def test_no_build_appears_twice(name: str) -> None:
    builds = _builds(name)
    dupes = sorted({b for b in builds if builds.count(b) > 1})
    assert not dupes, f"{name} lists {dupes} more than once"


def test_every_language_leads_with_the_same_build() -> None:
    """A translated file that is missing the newest section falls back to English
    for it — but only if the section is absent, not if it is buried."""
    heads = {name: _builds(name)[0] for name in FILES}
    assert len(set(heads.values())) == 1, (
        f"the languages disagree about the newest build: {heads}"
    )
