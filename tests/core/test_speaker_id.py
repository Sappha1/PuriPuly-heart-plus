"""SpeakerRegistry (r318): session clustering + named enrollment, all local."""
from __future__ import annotations

import numpy as np
import pytest

from puripuly_heart.core.speaker_id import (
    EMBEDDING_DIM,
    SpeakerRegistry,
)


def _voice(seed: int) -> np.ndarray:
    """A stable synthetic voiceprint direction."""
    rng = np.random.default_rng(seed)
    v = rng.normal(0, 1, EMBEDDING_DIM).astype(np.float32)
    return v / np.linalg.norm(v)


def _near(base: np.ndarray, seed: int, wobble: float = 0.25) -> np.ndarray:
    """Same voice, different utterance: high cosine to base (~0.97 at 0.25)."""
    rng = np.random.default_rng(seed)
    v = base + wobble * rng.normal(0, 1, EMBEDDING_DIM).astype(np.float32) / np.sqrt(EMBEDDING_DIM)
    return v / np.linalg.norm(v)


@pytest.fixture()
def registry(tmp_path):
    return SpeakerRegistry(tmp_path / "voices.json")


def test_distinct_voices_get_distinct_speaker_labels(registry) -> None:
    a, b = _voice(1), _voice(2)  # random 512-dim: cosine ~0 (clearly different)
    m1 = registry.match(a)
    m2 = registry.match(b)
    m3 = registry.match(_near(a, 3))
    assert m1.label == "Speaker 1"
    assert m2.label == "Speaker 2"
    assert m3.label == "Speaker 1"          # same voice rejoins its cluster
    assert m3.kind == "cluster"


def test_naming_a_cluster_persists_and_matches_next_session(registry, tmp_path) -> None:
    a = _voice(7)
    match = registry.match(a)
    assert registry.enroll_cluster(match.cluster_id, "Rio")
    assert registry.enrolled_names() == ["Rio"]

    fresh = SpeakerRegistry(tmp_path / "voices.json")   # "next session"
    again = fresh.match(_near(a, 8))
    assert again.kind == "named"
    assert again.label == "Rio"


def test_forget_removes_the_voice(registry, tmp_path) -> None:
    a = _voice(11)
    registry.enroll_cluster(registry.match(a).cluster_id, "Momo")
    assert registry.forget("Momo")
    assert registry.enrolled_names() == []
    fresh = SpeakerRegistry(tmp_path / "voices.json")
    assert fresh.match(_near(a, 12)).kind == "cluster"  # anonymous again


def test_enroll_requires_real_cluster_and_name(registry) -> None:
    assert not registry.enroll_cluster(999, "Ghost")
    match = registry.match(_voice(20))
    assert not registry.enroll_cluster(match.cluster_id, "   ")


def test_cluster_cap_reuses_nearest_instead_of_growing(registry) -> None:
    for seed in range(40):                   # far more voices than the cap
        registry.match(_voice(100 + seed))
    labels = {registry.match(_voice(100 + s)).cluster_id for s in range(40)}
    assert len(labels) <= 12


def test_session_reset_clears_clusters_not_names(registry) -> None:
    a = _voice(30)
    registry.enroll_cluster(registry.match(a).cluster_id, "Kai")
    registry.reset_session()
    m = registry.match(_near(a, 31))
    assert m.kind == "named" and m.label == "Kai"
    b = registry.match(_voice(32))
    assert b.label == "Speaker 1"            # numbering restarted


def test_corrupt_store_starts_empty(tmp_path) -> None:
    path = tmp_path / "voices.json"
    path.write_text("{not json", encoding="utf-8")
    registry = SpeakerRegistry(path)
    assert registry.enrolled_names() == []


# ── end-to-end: embedding -> hub match -> UIEvent payload (r318) ──────────

@pytest.mark.asyncio
async def test_peer_transcript_with_embedding_gets_labeled(tmp_path) -> None:
    from uuid import uuid4

    from puripuly_heart.core.orchestrator.hub import ClientHub
    from puripuly_heart.core.stt.controller import STTFinalEvent
    from puripuly_heart.domain.events import UIEventType
    from puripuly_heart.domain.models import Transcript
    from tests.helpers.fakes import RecordingOscQueue

    hub = ClientHub(stt=None, llm=None, osc=RecordingOscQueue(),
                    source_language="en", target_language="zh-CN")
    hub.translation_enabled = False   # r309: no filtering, straight to chat
    hub.speaker_registry = SpeakerRegistry(tmp_path / "voices.json")

    voice = _voice(50)

    async def _feed(text: str, embedding) -> None:
        uid = uuid4()
        await hub._handle_stt_event(
            STTFinalEvent(
                utterance_id=uid,
                transcript=Transcript(
                    utterance_id=uid, text=text, is_final=True,
                    created_at=hub.clock.now(), channel="peer",
                    speaker_embedding=tuple(float(x) for x in embedding),
                ),
            )
        )

    await _feed("hello there", voice)
    await _feed("second line", _near(voice, 51))
    await _feed("someone else", _voice(60))

    labels = []
    while not hub.ui_events.empty():
        ev = hub.ui_events.get_nowait()
        if ev.type == UIEventType.TRANSLATION_SKIPPED:
            labels.append(ev.payload.speaker_cluster_id)
    assert labels == [1, 1, 2]        # same voice sticks, new voice increments


@pytest.mark.asyncio
async def test_peer_transcript_without_embedding_stays_unlabeled(tmp_path) -> None:
    from uuid import uuid4

    from puripuly_heart.core.orchestrator.hub import ClientHub
    from puripuly_heart.core.stt.controller import STTFinalEvent
    from puripuly_heart.domain.events import UIEventType
    from puripuly_heart.domain.models import Transcript
    from tests.helpers.fakes import RecordingOscQueue

    hub = ClientHub(stt=None, llm=None, osc=RecordingOscQueue(),
                    source_language="en", target_language="zh-CN")
    hub.translation_enabled = False
    hub.speaker_registry = SpeakerRegistry(tmp_path / "voices.json")

    uid = uuid4()
    await hub._handle_stt_event(
        STTFinalEvent(
            utterance_id=uid,
            transcript=Transcript(uid, "no embedding here", True,
                                  created_at=hub.clock.now(), channel="peer"),
        )
    )
    ev = hub.ui_events.get_nowait()
    while ev.type != UIEventType.TRANSLATION_SKIPPED:
        ev = hub.ui_events.get_nowait()
    assert ev.payload.speaker_cluster_id == -1
    assert ev.payload.speaker_name == ""


# ── r321: naming must be sticky and recallable ────────────────────────────

def test_named_cluster_stays_named_in_threshold_gap(registry) -> None:
    """A sample matching its cluster (>=0.52) but missing the named bar
    (>=0.60) must STILL show the enrolled name for the session."""
    import numpy as np
    from puripuly_heart.core.speaker_id import (
        CLUSTER_MATCH_THRESHOLD,
        NAMED_MATCH_THRESHOLD,
    )

    base = _voice(70)
    match = registry.match(base)
    registry.enroll_cluster(match.cluster_id, "Robin")

    # craft a vector with cosine ~0.56 to the centroid: in the gap
    rng = np.random.default_rng(71)
    ortho = rng.normal(0, 1, base.shape[0]).astype(np.float32)
    ortho -= float(np.dot(ortho, base)) * base
    ortho /= np.linalg.norm(ortho)
    target = 0.56
    gap_vector = target * base + float(np.sqrt(1 - target**2)) * ortho
    sim = float(np.dot(gap_vector, base))
    assert CLUSTER_MATCH_THRESHOLD < sim < NAMED_MATCH_THRESHOLD

    result = registry.match(gap_vector)
    assert result.kind == "named"
    assert result.label == "Robin"


def test_name_for_cluster_lookup_and_reset(registry) -> None:
    m = registry.match(_voice(80))
    assert registry.name_for_cluster(m.cluster_id) == ""
    registry.enroll_cluster(m.cluster_id, "Momo")
    assert registry.name_for_cluster(m.cluster_id) == "Momo"
    registry.reset_session()
    assert registry.name_for_cluster(m.cluster_id) == ""   # session map cleared
    # but the enrollment survives: a close voice is greeted by name
    again = registry.match(_near(_voice(80), 81))
    assert again.kind == "named" and again.label == "Momo"


# ── r330: multiple voiceprint variants per person ─────────────────────────

def _channel_shifted(base: np.ndarray, seed: int, similarity: float) -> np.ndarray:
    """Same person heard through a different channel: a vector at a chosen
    cosine similarity to `base` (VRChat vs a voice call measures like this)."""
    rng = np.random.default_rng(seed)
    ortho = rng.normal(0, 1, base.shape[0]).astype(np.float32)
    ortho -= float(np.dot(ortho, base)) * base
    ortho /= np.linalg.norm(ortho)
    vector = similarity * base + float(np.sqrt(1 - similarity**2)) * ortho
    return (vector / np.linalg.norm(vector)).astype(np.float32)


def test_second_channel_becomes_a_new_variant_not_an_average(registry) -> None:
    call_voice = _voice(200)
    vr_voice = _channel_shifted(call_voice, 201, 0.45)   # clearly another channel

    registry.enroll_cluster(registry.match(call_voice).cluster_id, "Robin")
    assert registry.variant_count("Robin") == 1
    registry.reset_session()

    # She shows up in VRChat: not recognized, user names her again.
    vr_match = registry.match(vr_voice)
    assert vr_match.kind == "cluster"
    registry.enroll_cluster(vr_match.cluster_id, "Robin")
    assert registry.variant_count("Robin") == 2          # kept, not blended

    # BOTH channels are now recognized in a fresh session.
    registry.reset_session()
    assert registry.match(_near(call_voice, 202)).label == "Robin"
    registry.reset_session()
    assert registry.match(_near(vr_voice, 203)).label == "Robin"


def test_same_channel_refines_instead_of_multiplying(registry) -> None:
    voice = _voice(210)
    registry.enroll_cluster(registry.match(voice).cluster_id, "Momo")
    registry.reset_session()
    registry.enroll_cluster(registry.match(_near(voice, 211)).cluster_id, "Momo")
    assert registry.variant_count("Momo") == 1          # refined, not duplicated


def test_variants_are_capped(registry) -> None:
    base = _voice(220)
    for index in range(8):
        registry.reset_session()
        far = _channel_shifted(base, 230 + index, 0.30)
        registry.enroll_cluster(registry.match(far).cluster_id, "Kai")
    assert registry.variant_count("Kai") <= 4


def test_legacy_single_centroid_store_still_loads(tmp_path) -> None:
    """r318-r329 wrote {"centroid": [...]}; those users must not lose names.

    Stamped with the current model on purpose: this is about reading the old
    single-centroid SHAPE, not about carrying voiceprints across a model
    change. An unstamped store is cleared by design since r349 — see
    tests/test_speaker_model_upgrade.py.
    """
    import json

    from puripuly_heart.core.speaker_id import SPEAKER_MODEL_ID

    voice = _voice(240)
    path = tmp_path / "voices.json"
    path.write_text(
        json.dumps(
            {
                "model": SPEAKER_MODEL_ID,
                "voices": [
                    {"name": "Rio", "centroid": [float(x) for x in voice], "count": 3}
                ],
            }
        ),
        encoding="utf-8",
    )
    registry = SpeakerRegistry(path)
    assert registry.enrolled_names() == ["Rio"]
    assert registry.variant_count("Rio") == 1
    assert registry.match(_near(voice, 241)).label == "Rio"


def test_variants_round_trip_through_the_store(registry, tmp_path) -> None:
    call_voice = _voice(250)
    vr_voice = _channel_shifted(call_voice, 251, 0.40)
    registry.enroll_cluster(registry.match(call_voice).cluster_id, "Robin")
    registry.reset_session()
    registry.enroll_cluster(registry.match(vr_voice).cluster_id, "Robin")

    reloaded = SpeakerRegistry(tmp_path / "voices.json")
    assert reloaded.variant_count("Robin") == 2
    assert reloaded.match(_near(vr_voice, 252)).label == "Robin"
    assert reloaded.match(_near(call_voice, 253)).label == "Robin"
