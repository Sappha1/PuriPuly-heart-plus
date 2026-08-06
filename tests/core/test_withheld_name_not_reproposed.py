"""r380: the name dialog re-proposed a name the matcher had just refused.

Reported: a chat line showing "Speaker 3" opened the naming dialog already
filled in with somebody's saved name — someone the line was NOT.

A session cluster records the name of whoever was matched inside it. When a
later voice joins that cluster but does NOT resemble the named person well
enough (STICKY_NAME_THRESHOLD), the matcher deliberately WITHHOLDS the name and
returns a plain cluster match, so the chat shows "Speaker N". That behaviour is
correct and stays.

What was wrong is that the dialog, seeing a line with no name, asked the
registry for the cluster's name — which is precisely the name just withheld. So
the fallback could only ever fire in the case where guessing is wrong.

It is not cosmetic: with the name pre-filled, Save merges this voice into that
person's stored identity. r341 added a warning for exactly that merge; here
there was none, because the dialog looked like it was confirming a name rather
than creating one.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np

from puripuly_heart.core.speaker_id import (
    EMBEDDING_DIM,
    STICKY_NAME_THRESHOLD,
    SpeakerRegistry,
)

SAVED_NAME = "Alex"          # never a real person's name in this repo


def _unit(seed: int) -> np.ndarray:
    rng = np.random.default_rng(seed)
    v = rng.normal(size=EMBEDDING_DIM).astype(np.float32)
    return v / np.linalg.norm(v)


def _blend(a: np.ndarray, b: np.ndarray, weight: float) -> np.ndarray:
    v = (1.0 - weight) * a + weight * b
    return (v / np.linalg.norm(v)).astype(np.float32)


def test_a_cluster_can_hold_a_name_the_chat_deliberately_does_not_show(
    tmp_path: Path,
) -> None:
    """The divergence the dialog was resurrecting. If this ever stops being
    true the fallback would have been harmless — so it is worth pinning."""
    registry = SpeakerRegistry(tmp_path / "voices.json")

    voice = _unit(1)
    first = registry.match(voice, seconds=4.0)
    assert first.cluster_id > 0
    assert registry.enroll_cluster(first.cluster_id, SAVED_NAME)

    # Someone else. Far enough that the matcher refuses to call them the named
    # person, yet the cluster still carries that name from the enrollment.
    other = _blend(voice, _unit(2), 0.72)
    result = registry.match(other, seconds=4.0)

    assert result.kind != "named", (
        "the second voice was accepted AS the named person, so this no longer "
        "exercises a withheld name — retune the blend"
    )
    assert result.label != SAVED_NAME, "the chat would have shown the wrong person"
    assert result.label.startswith("Speaker"), (
        f"expected an unnamed tag, got {result.label!r}"
    )
    assert registry.name_for_cluster(result.cluster_id) == SAVED_NAME, (
        "the cluster no longer carries the withheld name, so the scenario "
        "behind r380 has changed"
    )


def test_the_dialog_does_not_ask_the_registry_for_a_withheld_name() -> None:
    """The fix itself: a line rendered without a name must open an EMPTY field.

    Asserted against the source because the dialog needs a live Flet page to
    build, and the property that matters is that the cluster lookup is gone
    from this path entirely.
    """
    source = Path("src/puripuly_heart/ui/views/dashboard.py").read_text(encoding="utf-8")
    start = source.index("def _open_speaker_name_dialog")
    body = source[start : source.index("\n    def ", start + 10)]

    assert "on_speaker_name_lookup" not in body, (
        "the dialog is asking the registry for the cluster's name again — that "
        "name is the one the matcher refused to apply, and pre-filling it turns "
        "Save into a silent merge into someone else's identity"
    )
    assert "known_name = (known_name or \"\").strip()" in body, (
        "the dialog no longer takes its name from the rendered line"
    )


def test_the_sticky_threshold_is_still_what_withholds_the_name() -> None:
    """The comment in the fix names this constant; keep them honest together."""
    assert 0.0 < STICKY_NAME_THRESHOLD < 1.0
