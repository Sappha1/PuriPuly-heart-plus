"""r341: destructive voice edits are previewable, refusable, and undoable.

A silent merge collapsed two people into one name and averaged their
voiceprints together — unrecoverable, because nothing kept the prior state
and nothing warned. These pin the three guards at the registry level.
"""
from __future__ import annotations

import numpy as np

from puripuly_heart.core.speaker_id import EMBEDDING_DIM, SpeakerRegistry


def _voice(seed: int) -> np.ndarray:
    rng = np.random.default_rng(seed)
    vector = rng.normal(size=EMBEDDING_DIM).astype(np.float32)
    return vector / float(np.linalg.norm(vector))


def _registry(tmp_path) -> SpeakerRegistry:
    return SpeakerRegistry(tmp_path / "voices.json")


def _enroll(registry: SpeakerRegistry, seed: int, name: str) -> int:
    match = registry.match(_voice(seed))
    assert registry.enroll_cluster(match.cluster_id, name) is True
    return match.cluster_id


def test_has_name_answers_before_a_merge_happens(tmp_path) -> None:
    registry = _registry(tmp_path)
    _enroll(registry, 1, "Alex")

    assert registry.has_name("Alex") is True
    assert registry.has_name(" Alex ") is True  # the UI passes raw field text
    assert registry.has_name("Robin") is False


def test_snapshot_restores_a_merge(tmp_path) -> None:
    """The exact shape of tonight's data loss, undone."""
    registry = _registry(tmp_path)
    _enroll(registry, 11, "Alex")
    _enroll(registry, 12, "Robin")
    before = registry.snapshot()

    registry.rename("Robin", "Alex")  # the destructive merge
    assert registry.enrolled_names() == ["Alex"]
    assert registry.variant_count("Alex") == 2

    assert registry.restore(before) is True

    assert registry.enrolled_names() == ["Alex", "Robin"]
    assert registry.variant_count("Alex") == 1
    assert registry.variant_count("Robin") == 1


def test_restore_survives_a_reload(tmp_path) -> None:
    registry = _registry(tmp_path)
    _enroll(registry, 21, "Alex")
    before = registry.snapshot()
    registry.forget("Alex")

    registry.restore(before)

    assert _registry(tmp_path).enrolled_names() == ["Alex"]


def test_snapshot_is_a_copy_not_a_view(tmp_path) -> None:
    """Later edits must not corrupt the undo state they'd be undone by."""
    registry = _registry(tmp_path)
    _enroll(registry, 31, "Alex")
    before = registry.snapshot()

    registry.rename("Alex", "Robin")
    registry.restore(before)

    assert registry.enrolled_names() == ["Alex"]


def test_detach_frees_the_cluster_without_touching_the_person(tmp_path) -> None:
    """"This speaker is not that person" — the correction that must never
    rewrite the real person's history."""
    registry = _registry(tmp_path)
    cluster_id = _enroll(registry, 41, "Alex")

    previous = registry.detach_cluster(cluster_id)

    assert previous == "Alex"
    assert registry.name_for_cluster(cluster_id) == ""
    # the enrolled person is untouched
    assert registry.enrolled_names() == ["Alex"]
    assert registry.variant_count("Alex") == 1
    # and the freed cluster can be enrolled as someone else
    assert registry.enroll_cluster(cluster_id, "Robin") is True
    assert registry.enrolled_names() == ["Alex", "Robin"]


def test_detach_of_an_unnamed_cluster_is_a_noop(tmp_path) -> None:
    registry = _registry(tmp_path)
    match = registry.match(_voice(51))

    assert registry.detach_cluster(match.cluster_id) == ""
    assert registry.detach_cluster(9999) == ""


def test_restore_rejects_garbage(tmp_path) -> None:
    registry = _registry(tmp_path)
    _enroll(registry, 61, "Alex")

    assert registry.restore({}) is False
    assert registry.restore(None) is False  # type: ignore[arg-type]
    assert registry.enrolled_names() == ["Alex"]
