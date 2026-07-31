"""r338: a name is one identity, however many clusters carry it.

The user enrolled the same person ("Robin") four times — the same voice through
a call and in-game clusters separately, which is what MAX_VARIANTS_PER_NAME
exists for. Renaming from one chat entry relabelled only the cluster clicked,
so three log entries kept the old name.
"""
from __future__ import annotations

import numpy as np
import pytest

from puripuly_heart.core.speaker_id import (
    EMBEDDING_DIM,
    MAX_VARIANTS_PER_NAME,
    SpeakerRegistry,
)


def _voice(seed: int) -> np.ndarray:
    rng = np.random.default_rng(seed)
    vector = rng.normal(size=EMBEDDING_DIM).astype(np.float32)
    return vector / float(np.linalg.norm(vector))


def _registry(tmp_path) -> SpeakerRegistry:
    return SpeakerRegistry(tmp_path / "voices.json")


def _enroll_new_cluster(registry: SpeakerRegistry, seed: int, name: str) -> int:
    """Feed a distinct voice so it forms its own cluster, then name it."""
    match = registry.match(_voice(seed))
    assert registry.enroll_cluster(match.cluster_id, name) is True
    return match.cluster_id


def test_rename_moves_every_cluster_of_that_person(tmp_path) -> None:
    registry = _registry(tmp_path)
    clusters = [_enroll_new_cluster(registry, seed, "Robin") for seed in (1, 2, 3, 4)]
    assert len({*clusters}) == 4, "seeds must produce distinct clusters"

    assert sorted(registry.clusters_for_name("Robin")) == sorted(clusters)

    assert registry.rename("Robin", "Sarah") is True

    # every cluster follows the person, not just the one that was clicked
    assert sorted(registry.clusters_for_name("Sarah")) == sorted(clusters)
    assert registry.clusters_for_name("Robin") == []
    for cluster_id in clusters:
        assert registry.name_for_cluster(cluster_id) == "Sarah"
    assert registry.enrolled_names() == ["Sarah"]


def test_rename_survives_a_reload(tmp_path) -> None:
    registry = _registry(tmp_path)
    _enroll_new_cluster(registry, 11, "Robin")
    registry.rename("Robin", "Sarah")

    reloaded = _registry(tmp_path)

    assert reloaded.enrolled_names() == ["Sarah"]


def test_rename_into_an_existing_name_merges_the_voiceprints(tmp_path) -> None:
    registry = _registry(tmp_path)
    _enroll_new_cluster(registry, 21, "Robin")
    _enroll_new_cluster(registry, 22, "Sarah")

    assert registry.rename("Robin", "Sarah") is True

    assert registry.enrolled_names() == ["Sarah"]
    # both prints are kept — they are the same person on two channels
    assert registry.variant_count("Sarah") == 2


def test_merge_respects_the_variant_budget(tmp_path) -> None:
    registry = _registry(tmp_path)
    for seed in range(30, 30 + MAX_VARIANTS_PER_NAME):
        _enroll_new_cluster(registry, seed, "Robin")
    for seed in range(50, 50 + MAX_VARIANTS_PER_NAME):
        _enroll_new_cluster(registry, seed, "Sarah")

    registry.rename("Robin", "Sarah")

    assert registry.variant_count("Sarah") == MAX_VARIANTS_PER_NAME


@pytest.mark.parametrize(
    ("old", "new"),
    [("", "Sarah"), ("Robin", ""), ("Robin", "Robin"), ("Ghost", "Sarah")],
)
def test_rename_rejects_nonsense(tmp_path, old: str, new: str) -> None:
    registry = _registry(tmp_path)
    _enroll_new_cluster(registry, 41, "Robin")

    assert registry.rename(old, new) is False
    assert registry.enrolled_names() == ["Robin"]


def test_forget_clears_the_session_mapping(tmp_path) -> None:
    """Otherwise the cluster keeps the deleted name and the next utterance
    re-labels with a voice the user just removed."""
    registry = _registry(tmp_path)
    cluster_id = _enroll_new_cluster(registry, 51, "Robin")

    assert registry.forget("Robin") is True

    assert registry.enrolled_names() == []
    assert registry.name_for_cluster(cluster_id) == ""
    assert registry.clusters_for_name("Robin") == []


def test_enrolled_summary_reports_variants_and_counts(tmp_path) -> None:
    registry = _registry(tmp_path)
    _enroll_new_cluster(registry, 61, "Robin")
    _enroll_new_cluster(registry, 62, "Robin")
    _enroll_new_cluster(registry, 63, "Alex")

    summary = dict((name, variants) for name, variants, _heard in registry.enrolled_summary())

    assert summary == {"Alex": 1, "Robin": 2}
    assert [name for name, _v, _h in registry.enrolled_summary()] == ["Alex", "Robin"]


def _voice_at(base: np.ndarray, similarity: float, seed: int) -> np.ndarray:
    """A unit vector whose cosine to `base` is exactly `similarity`.

    Blend weights are NOT similarities here: two random unit vectors in 512
    dimensions are near-orthogonal, so mixing 62% of a voice in lands at
    cosine 0.86, not 0.62. Build it from an orthogonal component instead.
    """
    rng = np.random.default_rng(seed)
    noise = rng.normal(size=EMBEDDING_DIM).astype(np.float32)
    orthogonal = noise - float(np.dot(noise, base)) * base
    orthogonal /= float(np.linalg.norm(orthogonal))
    vector = similarity * base + float(np.sqrt(1.0 - similarity**2)) * orthogonal
    return (vector / float(np.linalg.norm(vector))).astype(np.float32)


def test_a_stranger_near_a_named_cluster_is_not_given_that_name(tmp_path) -> None:
    """r338: the user's report — a different speaker tagged "Robin".

    A voice that fails the 0.60 named test used to inherit an enrolled name
    just by landing within 0.52 of a cluster that had once been named. With
    one person enrolled from several clusters that is several traps.
    """
    from puripuly_heart.core.speaker_id import (
        CLUSTER_MATCH_THRESHOLD,
        NAMED_MATCH_THRESHOLD,
        STICKY_NAME_THRESHOLD,
    )

    registry = _registry(tmp_path)
    robin = _voice(101)
    cluster_id = registry.match(robin).cluster_id
    assert registry.enroll_cluster(cluster_id, "Robin") is True

    # Someone else who lands in the risky band: close enough to join Robin's
    # cluster (>= 0.52), nowhere near close enough to BE Robin (< 0.60), and
    # below the bar for inheriting the name (< 0.55).
    stranger = _voice_at(robin, 0.535, seed=202)
    similarity = float(np.dot(stranger, robin))
    assert CLUSTER_MATCH_THRESHOLD <= similarity < NAMED_MATCH_THRESHOLD, similarity
    assert similarity < STICKY_NAME_THRESHOLD, similarity

    match = registry.match(stranger)

    assert match.label != "Robin", "a stranger inherited an enrolled name"
    assert match.kind == "cluster"
    assert match.label.startswith("Speaker ")


def test_the_named_voice_still_keeps_its_name_across_borderline_samples(tmp_path) -> None:
    """The stickiness exists so a just-named voice does not flip back to
    "Speaker N" on a slightly noisy sample — that must still hold."""
    from puripuly_heart.core.speaker_id import NAMED_MATCH_THRESHOLD

    registry = _registry(tmp_path)
    robin = _voice(303)
    cluster_id = registry.match(robin).cluster_id
    registry.enroll_cluster(cluster_id, "Robin")

    # Same person, a bit degraded: under the named bar, but still clearly
    # them — this is the case the stickiness was added for.
    from puripuly_heart.core.speaker_id import STICKY_NAME_THRESHOLD

    noisy = _voice_at(robin, 0.57, seed=404)
    similarity = float(np.dot(noisy, robin))
    assert STICKY_NAME_THRESHOLD <= similarity < NAMED_MATCH_THRESHOLD, similarity

    match = registry.match(noisy)

    assert match.label == "Robin"
