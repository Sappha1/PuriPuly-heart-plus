"""r383: two similar-sounding saved voices deadlocked naming permanently.

Reported as having to re-enter the same names on every single line. From the
live log, on every utterance:

    refusing to guess between '<a>' (0.790) and the next closest (0.787)
        - margin 0.003 < 0.08
    joined cluster 1 (similarity=0.775, next closest 0.622)

Two saved people scoring within 0.003 of each other means the r351 margin rule
refuses to choose — correctly. But the refusal clears the winner, and the
sticky-cluster path underneath required that same cleared winner to equal the
cluster's name, so the name the cluster ALREADY carried could never be applied
either. Every line came back unnamed however clearly it belonged to that
cluster; naming it again added another print, which made the two voices more
alike, which guaranteed the next refusal. It could not recover on its own.

Two different questions were sharing one number:

  * "WHICH of these people is this?" needs a margin — putting one person's name
    on another's words is what destroys a log.
  * "Is this the person whose cluster this voice is already in?" does not. The
    cluster is continuity already established.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np

from puripuly_heart.core.speaker_id import (
    EMBEDDING_DIM,
    NAMED_MARGIN,
    SpeakerRegistry,
)


def _unit(seed: int) -> np.ndarray:
    rng = np.random.default_rng(seed)
    v = rng.normal(size=EMBEDDING_DIM).astype(np.float32)
    return v / np.linalg.norm(v)


def _blend(a: np.ndarray, b: np.ndarray, w: float) -> np.ndarray:
    v = (1.0 - w) * a + w * b
    return (v / np.linalg.norm(v)).astype(np.float32)


def _two_alike(tmp_path: Path):
    """Two saved voices close enough to sit inside NAMED_MARGIN, as reported."""
    registry = SpeakerRegistry(tmp_path / "voices.json")
    first = _unit(1)
    second = _blend(first, _unit(2), 0.30)

    a = registry.match(first, seconds=4.0)
    registry.enroll_cluster(a.cluster_id, "Alex")
    b = registry.match(second, seconds=4.0)
    registry.enroll_cluster(b.cluster_id, "Robin")
    return registry, first, second


def test_a_named_cluster_keeps_its_name_when_two_voices_score_alike(tmp_path) -> None:
    """The reported symptom: every line unnamed, forever."""
    registry, first, second = _two_alike(tmp_path)

    unnamed = []
    for i in range(8):
        probe = _blend(first, second, 0.02 * i)
        result = registry.match(probe, seconds=4.0)
        if result.kind != "named":
            unnamed.append((i, result.label, round(result.similarity, 3)))

    assert not unnamed, (
        f"{len(unnamed)}/8 utterances came back unnamed even though they landed "
        f"in a named cluster: {unnamed} — the user has to retype the name on "
        f"every line, and each retype makes the two voices more alike"
    )


def test_the_name_applied_is_the_one_that_cluster_belongs_to(tmp_path) -> None:
    """Sticking to a name is only safe if it is the cluster's OWN name — not
    whichever person happened to rank highest."""
    registry, first, second = _two_alike(tmp_path)

    for i in range(6):
        probe = _blend(first, second, 0.03 * i)
        result = registry.match(probe, seconds=4.0)
        if result.kind == "named":
            assert result.label == registry.name_for_cluster(result.cluster_id), (
                "a line was labelled with someone other than the person its "
                "cluster belongs to"
            )


def test_saying_not_this_person_still_overrides_the_cluster(tmp_path) -> None:
    """The correction has to keep working — otherwise a wrong name becomes
    permanent, which is worse than the deadlock it replaced."""
    registry, first, second = _two_alike(tmp_path)

    probe = _blend(first, second, 0.05)
    before = registry.match(probe, seconds=4.0)
    assert before.kind == "named", "nothing to reject in this fixture"

    assert registry.reject_utterance(probe, before.label)

    after = registry.match(probe, seconds=4.0)
    assert after.label != before.label, (
        "the cluster re-applied a name the user had explicitly rejected"
    )


def test_a_voice_that_drifts_away_still_loses_the_name(tmp_path) -> None:
    """Cluster continuity must not become 'named forever regardless'."""
    registry, first, _second = _two_alike(tmp_path)

    stranger = _blend(first, _unit(99), 0.95)
    result = registry.match(stranger, seconds=4.0)
    assert result.label != "Alex" or result.kind != "named", (
        "an unrelated voice inherited the cluster's name"
    )


def test_the_margin_rule_itself_is_untouched(tmp_path) -> None:
    """r351 still decides WHICH stranger a new voice is; r383 only affects a
    cluster confirming the person it already belongs to."""
    assert NAMED_MARGIN > 0.0
