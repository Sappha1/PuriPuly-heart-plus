"""r317: caption lines never end in a 1-2 word orphan.

Near-fits (<=25% overflow) shrink the font up to 20% and stay on one line;
genuinely long lines get balanced breaks instead of greedy wrap; CJK lines
never start with closing punctuation."""
from __future__ import annotations

import pytest

from puripuly_heart.ui.desktop_overlay import (
    _caption_break_candidates,
    _estimated_caption_line_width,
    _fit_caption_text,
)

# The screenshot case: this line missed one-line fit by ~10% and greedy wrap
# left "shots." alone on line two.
_SCREENSHOT_EN = "Still, I didn't see anyone, so I killed one. Oh, wait—no, I just fired two shots."
_SCREENSHOT_ZH = "依旧是没看见人，就打死一个。哎，不对，就打打了两枪。"


def test_fitting_leaves_short_text_alone() -> None:
    assert _fit_caption_text("hello", 20, 1000.0, 3) == ("hello", 20)


def test_near_fit_shrinks_to_one_line_instead_of_orphan() -> None:
    size = 20
    width = _estimated_caption_line_width(_SCREENSHOT_EN, size)
    avail = width / 1.10  # missed the fit by 10%
    text, fitted = _fit_caption_text(_SCREENSHOT_EN, size, avail, 3)
    assert text == _SCREENSHOT_EN                    # no break inserted
    assert 16 <= fitted < size                       # shrunk, floored at 80%
    assert _estimated_caption_line_width(text, fitted) <= avail  # now fits


def test_long_text_breaks_balanced_not_greedy() -> None:
    size = 20
    width = _estimated_caption_line_width(_SCREENSHOT_EN, size)
    avail = width / 1.8  # genuinely needs two lines
    text, fitted = _fit_caption_text(_SCREENSHOT_EN, size, avail, 3)
    assert fitted == size
    lines = text.split(chr(10))
    assert len(lines) == 2
    first, second = (
        _estimated_caption_line_width(line, size) for line in lines
    )
    assert first <= avail and second <= avail        # both fit
    assert second >= first * 0.4                     # no orphan tail
    assert len(lines[1].split()) >= 3                # never 1-2 words alone


def test_cjk_lines_never_start_with_closing_punctuation() -> None:
    size = 20
    width = _estimated_caption_line_width(_SCREENSHOT_ZH, size)
    text, _ = _fit_caption_text(_SCREENSHOT_ZH, size, width / 1.9, 3)
    for line in text.split(chr(10)):
        assert line and line[0] not in "。，、！？：；）」』”…"


def test_unbreakable_text_passes_through() -> None:
    blob = "Supercalifragilisticexpialidocious" * 3
    text, size = _fit_caption_text(blob, 20, 100.0, 3)
    assert text == blob and size == 20


def test_break_candidates_respect_cjk_punctuation() -> None:
    text = "打死一个。哎不对"
    candidates = _caption_break_candidates(text)
    idx_of_period = text.index("。")
    assert idx_of_period not in candidates           # no break BEFORE 。
    assert (idx_of_period + 1) in candidates         # break after it is fine


def test_multiline_input_untouched() -> None:
    text = "line one" + chr(10) + "line two"
    assert _fit_caption_text(text, 20, 10.0, 3) == (text, 20)
