"""r320: ruby (pinyin-over-hanzi) captions get the same orphan control as
plain lines — r317 only covered _build_flet_text, and the ruby paths still
greedy-wrapped ("...变回 / 来了。" observed live on the overlay)."""
from __future__ import annotations

import flet as ft

from puripuly_heart.ui.desktop_overlay import (
    DesktopCaptionLine,
    _build_ruby_content,
    _estimated_caption_line_width,
)


def _line(text: str, font_size: int = 24) -> DesktopCaptionLine:
    return DesktopCaptionLine(
        text=text,
        role="peer_original",
        slot="primary",
        color="#ffffff",
        priority=1,
        block_id="b",
        channel="peer",
        block_variant="peer",
        appearance_seq=1,
        max_lines=3,
        font_size=font_size,
        font_family="x",
        weight="medium",
    )


_ZH = "依旧是没看见人就打死一个哎不对就打打了两枪"
_PINYIN_PER_CHAR = " ".join(["yi"] * len(_ZH))          # 1:1 → per-char branch
_PINYIN_GROUPED = "yijiu shi mei kanjian ren jiu dasi"  # !=1:1 → block branch


def test_per_char_ruby_splits_into_balanced_rows_when_long() -> None:
    line = _line(_PINYIN_PER_CHAR + "\n" + _ZH)
    wide = _estimated_caption_line_width(_ZH, 24) + 2 * len(_ZH)
    container = _build_ruby_content(ft, line, wide / 1.9)   # needs 2 rows
    content = container.content
    assert isinstance(content, ft.Column)
    rows = content.controls
    assert len(rows) == 2
    counts = [len(r.controls) for r in rows]
    assert min(counts) >= max(counts) * 0.5                 # balanced, no orphan


def test_per_char_ruby_shrinks_for_near_fit() -> None:
    line = _line(_PINYIN_PER_CHAR + "\n" + _ZH)
    wide = _estimated_caption_line_width(_ZH, 24) + 2 * len(_ZH)
    container = _build_ruby_content(ft, line, wide / 1.1)   # barely over
    assert isinstance(container.content, ft.Row)            # single row kept
    first_char_text = container.content.controls[0].controls[1]
    assert first_char_text.size < 24                        # font shrank


def test_per_char_ruby_untouched_when_it_fits() -> None:
    line = _line(_PINYIN_PER_CHAR + "\n" + _ZH)
    container = _build_ruby_content(ft, line, 100000.0)
    assert isinstance(container.content, ft.Row)
    assert container.content.controls[0].controls[1].size == 24


def test_block_ruby_lines_get_fitted() -> None:
    line = _line(_PINYIN_GROUPED + "\n" + _ZH)
    wide = _estimated_caption_line_width(_ZH, 24)
    column = _build_ruby_content(ft, line, wide / 1.9)      # needs 2 lines
    cjk_text = column.controls[1]
    assert chr(10) in cjk_text.value                        # balanced break inserted
    halves = cjk_text.value.split(chr(10))
    assert len(halves[1]) >= len(halves[0]) * 0.4           # no orphan tail
