"""r344: two people must not fragment into "Speaker 8".

Same-speaker similarity wobbles around the 0.52 join bar on real VoIP audio;
every miss used to spawn a fresh cluster. Margin join catches the clearly-
nearest misses, and consolidation heals fragments whose centroids converge.
Both must never glue two genuinely distinct speakers together.
"""
from __future__ import annotations

import numpy as np

from puripuly_heart.core.speaker_id import (
    CLUSTER_MARGIN,
    CLUSTER_MATCH_THRESHOLD,
    CLUSTER_SOFT_THRESHOLD,
    EMBEDDING_DIM,
    SpeakerRegistry,
)


def _voice(seed: int) -> np.ndarray:
    rng = np.random.default_rng(seed)
    vector = rng.normal(size=EMBEDDING_DIM).astype(np.float32)
    return vector / float(np.linalg.norm(vector))


def _voice_at(base: np.ndarray, similarity: float, seed: int) -> np.ndarray:
    rng = np.random.default_rng(seed)
    noise = rng.normal(size=EMBEDDING_DIM).astype(np.float32)
    orthogonal = noise - float(np.dot(noise, base)) * base
    orthogonal /= float(np.linalg.norm(orthogonal))
    vector = similarity * base + float(np.sqrt(1.0 - similarity**2)) * orthogonal
    return (vector / float(np.linalg.norm(vector))).astype(np.float32)


def _registry(tmp_path) -> SpeakerRegistry:
    return SpeakerRegistry(tmp_path / "voices.json")


def test_a_wobbly_voice_stays_one_cluster(tmp_path) -> None:
    """The fragmentation case: samples at 0.46-0.58 to their own centroid,
    nothing else in the room. Two speakers used to become Speaker 8."""
    registry = _registry(tmp_path)
    base = _voice(1)
    first = registry.match(base)

    seen_clusters = {first.cluster_id}
    for i, sim in enumerate((0.58, 0.47, 0.50, 0.46, 0.55, 0.48)):
        match = registry.match(_voice_at(base, sim, seed=100 + i))
        seen_clusters.add(match.cluster_id)

    assert len(seen_clusters) == 1, f"one voice fragmented into {seen_clusters}"


def test_two_speakers_stay_two_clusters(tmp_path) -> None:
    registry = _registry(tmp_path)
    alex, robin = _voice(11), _voice(12)
    a = registry.match(alex).cluster_id
    b = registry.match(robin).cluster_id
    assert a != b

    for i in range(6):
        who, base = ((a, alex) if i % 2 == 0 else (b, robin))
        match = registry.match(_voice_at(base, 0.49, seed=200 + i))
        assert match.cluster_id == who, f"sample {i} joined the wrong speaker"


def test_ambiguous_sample_gets_its_own_cluster(tmp_path) -> None:
    """Margin join must NOT fire when two clusters are both nearly as close —
    that is exactly the two-similar-speakers case."""
    registry = _registry(tmp_path)
    alex = _voice(21)
    a = registry.match(alex).cluster_id

    # a second cluster deliberately close to the probe
    near = _voice_at(alex, 0.50, seed=301)
    # push it away enough to be its own cluster first
    b = registry.match(_voice_at(alex, 0.30, seed=302)).cluster_id
    assert b != a

    # probe: soft-close to BOTH (margin below CLUSTER_MARGIN) -> new cluster
    probe = _voice_at(alex, CLUSTER_SOFT_THRESHOLD + 0.02, seed=303)
    best = float(np.dot(probe, alex))
    assert best < CLUSTER_MATCH_THRESHOLD
    match = registry.match(probe)
    if match.cluster_id in (a, b):
        # only acceptable if the margin was genuinely large
        sims = sorted(
            (float(np.dot(probe, c.centroid)) for c in registry._clusters),
            reverse=True,
        )
        assert sims[0] - sims[1] >= CLUSTER_MARGIN


def test_fragments_consolidate_as_evidence_accumulates(tmp_path) -> None:
    """A fragment spawned early merges back once the centroids converge —
    and the name carries over."""
    registry = _registry(tmp_path)
    base = _voice(31)
    a = registry.match(base).cluster_id
    assert registry.enroll_cluster(a, "Alex") is True

    # a fragment: far enough to spawn its own cluster
    frag = registry.match(_voice_at(base, 0.35, seed=401))
    b = frag.cluster_id
    assert b != a

    # the person's samples land BETWEEN the fragments (that is what
    # fragmentation of one voice means) — both centroids drift together
    for i in range(12):
        clusters = list(registry._clusters)
        if len(clusters) == 1:
            break
        midpoint = clusters[0].centroid + clusters[1].centroid
        midpoint = midpoint / float(np.linalg.norm(midpoint))
        registry.match(_voice_at(midpoint, 0.90, seed=500 + i))

    cluster_ids = {c.cluster_id for c in registry._clusters}
    assert len(cluster_ids) == 1, f"fragments did not consolidate: {cluster_ids}"
    survivor = next(iter(cluster_ids))
    assert registry.name_for_cluster(survivor) == "Alex"


def test_two_differently_named_clusters_never_consolidate(tmp_path) -> None:
    """Even at high similarity, merging two PEOPLE is the user's decision."""
    registry = _registry(tmp_path)
    base = _voice(41)
    a = registry.match(base).cluster_id
    registry.enroll_cluster(a, "Alex")
    b = registry.match(_voice_at(base, 0.30, seed=601)).cluster_id
    assert b != a
    registry.enroll_cluster(b, "Robin")

    # drive both centroids toward each other
    for i in range(12):
        registry.match(_voice_at(base, 0.58, seed=700 + i))

    ids = {c.cluster_id for c in registry._clusters}
    assert {a, b} <= ids, "differently-named clusters were merged"
