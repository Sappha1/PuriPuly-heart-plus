"""r385: a typed message already in the target language never reached the overlay.

The overlay holds a finalized transcript until its translation arrives, so the
caption doesn't flash the untranslated line first. The hold is armed by
`_overlay_translation_will_follow` — "is there an LLM and is translation on" —
and every release site lives on the translation path.

But `submit_text` skips translation on a condition that check knows nothing
about: text already in the target language. It then takes the no-translation
branch while the transcript has already been parked, and no release site is on
that branch. The entry stays in `_pending_overlay_transcripts` forever: the text
reaches the VRChat chatbox and the caption never appears at all.

Unlike the r384 report, toggling the overlay does not recover this one — there is
nothing left to re-render.
"""
from __future__ import annotations

import re
from pathlib import Path

HUB = Path("src/puripuly_heart/core/orchestrator/hub.py")


def _source() -> str:
    return HUB.read_text(encoding="utf-8")


def test_the_skip_branch_releases_the_hold() -> None:
    text = _source()
    start = text.index('stage="typed"')
    branch = text[start : text.index("await self._ensure_translation", start)]
    assert "_release_pending_overlay_transcript" in branch, (
        "submit_text skips translation for text already in the target language "
        "but leaves the transcript parked; the caption is then never emitted"
    )


def test_the_release_helper_emits_and_closes() -> None:
    """Half a release is still a stuck caption: the transcript has to be emitted
    AND the utterance closed, because `_handle_transcript` skips the close on the
    same will-translate condition that armed the hold."""
    text = _source()
    start = text.index("async def _release_pending_overlay_transcript")
    body = text[start : text.index("async def _emit_final_transcript_to_overlay", start)]
    assert "_pending_overlay_transcripts.pop" in body
    assert "transcript_final" in body, "the held transcript is never emitted"
    assert "_emit_overlay_utterance_closed" in body, (
        "the utterance is never closed, so the caption cannot finalize"
    )


def test_the_release_respects_the_self_overlay_gate() -> None:
    """It must not become a way to put my own messages on the overlay when I have
    switched them off — the r384 gate still decides."""
    text = _source()
    start = text.index("async def _release_pending_overlay_transcript")
    body = text[start : text.index("async def _emit_final_transcript_to_overlay", start)]
    assert "_overlay_flag_for_utterance" in body, (
        "the release bypasses the typed/spoken overlay gate"
    )


def test_the_release_is_safe_when_nothing_is_parked() -> None:
    """It runs on every skipped typed message, most of which were never held."""
    text = _source()
    start = text.index("async def _release_pending_overlay_transcript")
    body = text[start : text.index("async def _emit_final_transcript_to_overlay", start)]
    assert re.search(r"if transcript is None", body), (
        "a skipped message with nothing parked must return quietly"
    )


def test_every_park_still_has_a_release() -> None:
    """Structural guard: if a new park site appears without a release, this bug
    comes back in a new place."""
    text = _source()
    parks = text.count("self._pending_overlay_transcripts[")
    releases = text.count("self._pending_overlay_transcripts.pop(")
    assert releases >= parks, (
        f"{parks} place(s) park a transcript but only {releases} release it"
    )
