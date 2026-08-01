"""r350: "this is not that person" has to survive the next thing they say.

Correcting a wrong name used to stick only until the voice spoke again: the
label was popped and nothing was recorded, so consolidation drifted the
anonymous cluster back onto the named one and it inherited the rejected name.

The correction is anchored to the voiceprint of the MESSAGE the user clicked,
not to the cluster. By the time a correction is needed the two voices already
share a cluster — joining costs 0.52 while being named costs 0.60, so the
lookalike is inside the cluster before it is ever labelled, and the centroid
is ~0.99 similar to the genuine person. A cluster-level rejection would strip
the name from the person the user was trying to protect; the last two tests
here pin that down.
"""
from __future__ import annotations

import json

import numpy as np

from puripuly_heart.core.speaker_id import (
    EMBEDDING_DIM,
    MIN_TRUSTED_SECONDS,
    SpeakerRegistry,
)

LONG = MIN_TRUSTED_SECONDS + 0.5


def _voice(seed: int) -> np.ndarray:
    rng = np.random.default_rng(seed)
    vector = rng.normal(size=EMBEDDING_DIM).astype(np.float32)
    return vector / float(np.linalg.norm(vector))


def _voice_at(base: np.ndarray, similarity: float, seed: int) -> np.ndarray:
    """Two people who genuinely sound alike — the only case a correction is
    ever needed for, and the case that used to collapse."""
    rng = np.random.default_rng(seed)
    noise = rng.normal(size=EMBEDDING_DIM).astype(np.float32)
    orthogonal = noise - float(np.dot(noise, base)) * base
    orthogonal /= float(np.linalg.norm(orthogonal))
    vector = similarity * base + float(np.sqrt(1.0 - similarity**2)) * orthogonal
    return (vector / float(np.linalg.norm(vector))).astype(np.float32)


def _registry(tmp_path) -> SpeakerRegistry:
    return SpeakerRegistry(tmp_path / "voices.json")


def _named_alex(tmp_path):
    """A registry with Alex enrolled, plus a second person who sounds close."""
    registry = _registry(tmp_path)
    alex = _voice(1)
    other = _voice_at(alex, 0.66, 2)
    registry.enroll_cluster(registry.match(alex, LONG).cluster_id, "Alex")
    return registry, alex, other


def test_a_rejected_name_does_not_come_back_when_the_voice_speaks_again(tmp_path):
    """The reported bug, end to end."""
    registry, _alex, other = _named_alex(tmp_path)

    mislabelled = registry.match(other, LONG)
    assert mislabelled.label == "Alex", "expected the misidentification to reproduce"

    assert registry.reject_utterance(other, "Alex")

    for _ in range(8):
        again = registry.match(other, LONG)
        assert again.label != "Alex", "the rejected name crawled back"
        assert registry.name_for_cluster(again.cluster_id) != "Alex"


def test_the_real_person_keeps_their_name(tmp_path):
    """A rejection is about the rejected VOICE only. Rejecting a lookalike must
    not cost the genuine person the name they were given."""
    registry, alex, other = _named_alex(tmp_path)

    registry.reject_utterance(other, "Alex")

    assert registry.match(alex, LONG).label == "Alex"


def test_the_label_disappears_from_the_log_immediately(tmp_path):
    """The user should not have to wait for the next utterance to see their
    correction take effect."""
    registry, _alex, other = _named_alex(tmp_path)
    mislabelled = registry.match(other, LONG)

    registry.reject_utterance(other, "Alex")

    assert registry.name_for_cluster(mislabelled.cluster_id) != "Alex"


def test_naming_the_voice_overrides_an_earlier_rejection(tmp_path):
    """The user is the authority in both directions."""
    registry, _alex, other = _named_alex(tmp_path)
    registry.reject_utterance(other, "Alex")
    assert registry.match(other, LONG).label != "Alex"

    assert registry.enroll_embedding(other, "Alex")

    assert registry.match(other, LONG).label == "Alex"


def test_an_unidentified_message_can_be_named_directly(tmp_path):
    """An unidentified line has no cluster, so without this the user can see a
    message they know the speaker of and have no way to say so."""
    registry = _registry(tmp_path)
    voice = _voice(70)

    assert registry.enroll_embedding(voice, "Robin")

    assert registry.has_name("Robin")
    assert registry.match(voice, LONG).label == "Robin"


def test_a_correction_survives_a_restart(tmp_path):
    registry, alex, other = _named_alex(tmp_path)
    registry.reject_utterance(other, "Alex")

    saved = json.loads((tmp_path / "voices.json").read_text(encoding="utf-8"))
    assert saved["rejected"], "the correction was never written to disk"

    reloaded = SpeakerRegistry(tmp_path / "voices.json")

    for _ in range(5):
        assert reloaded.match(other, LONG).label != "Alex"
    assert reloaded.match(alex, LONG).label == "Alex"


def test_a_correction_survives_a_session_reset(tmp_path):
    """reset_session renumbers clusters, which is why a rejection is keyed to
    the voiceprint rather than the cluster id."""
    registry, _alex, other = _named_alex(tmp_path)
    registry.reject_utterance(other, "Alex")

    registry.reset_session()

    for _ in range(5):
        assert registry.match(other, LONG).label != "Alex"


def test_the_two_stay_apart_while_both_keep_talking(tmp_path):
    """The route the bug actually travelled: the pair take turns, the clusters
    converge, and the survivor hands its name to whoever is left.

    Note what is NOT asserted: that they end up in different clusters. Voices
    0.66 apart sit far inside the 0.52 join bar, so clustering cannot separate
    them and the two share one cluster throughout. The guarantee that matters
    is the one the user asked for — the corrected voice never wears the name
    again, while the real person keeps it.
    """
    registry, alex, other = _named_alex(tmp_path)
    registry.reject_utterance(other, "Alex")

    for _ in range(10):
        assert registry.match(other, LONG).label != "Alex"
        assert registry.match(alex, LONG).label == "Alex"
