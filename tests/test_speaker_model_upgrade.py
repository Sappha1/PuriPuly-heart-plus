"""r349: a bigger voiceprint model, and a length gate in front of it.

Two separate protections live here.

The model swap changed the voiceprint size (512 -> 192 numbers). Prints from
the old model are not merely less accurate under the new one, they are
meaningless, so the store records which model wrote it and refuses to load
anybody else's numbers.

The length gate exists because measurement showed the model is unreliable on
short audio: one speaker's own samples score ~0.87 against each other at 3
seconds but scatter to 0.44 at 1.2 seconds, overlapping the different-speaker
range entirely. A segment that short may still be labeled by matching an
existing voice, but must never mint a new speaker or move a stored print.
"""
from __future__ import annotations

import json

import numpy as np

from puripuly_heart.core.speaker_id import (
    EMBEDDING_DIM,
    MIN_TRUSTED_SECONDS,
    SPEAKER_MODEL_ID,
    SpeakerRegistry,
)

LONG = MIN_TRUSTED_SECONDS + 0.5
SHORT = MIN_TRUSTED_SECONDS - 0.5


def _voice(seed: int) -> np.ndarray:
    rng = np.random.default_rng(seed)
    vector = rng.normal(size=EMBEDDING_DIM).astype(np.float32)
    return vector / float(np.linalg.norm(vector))


def _registry(tmp_path) -> SpeakerRegistry:
    return SpeakerRegistry(tmp_path / "voices.json")


# ── the model swap ───────────────────────────────────────────────────────


def test_voices_saved_by_another_model_are_dropped_not_compared(tmp_path) -> None:
    store = tmp_path / "voices.json"
    store.write_text(
        json.dumps(
            {
                "model": "eres2net_base_zh_16k",
                "voices": [
                    {"name": "Alex", "variants": [[0.1] * 512], "count": 9}
                ],
            }
        ),
        encoding="utf-8",
    )

    registry = SpeakerRegistry(store)

    assert registry.enrolled_summary() == []
    assert registry.reset_reason == "model_changed"


def test_a_store_with_no_model_recorded_predates_the_stamp(tmp_path) -> None:
    """Anything written before r349 came from the old model by definition."""
    store = tmp_path / "voices.json"
    store.write_text(
        json.dumps({"voices": [{"name": "Robin", "variants": [[0.1] * 512]}]}),
        encoding="utf-8",
    )

    registry = SpeakerRegistry(store)

    assert registry.enrolled_summary() == []
    assert registry.reset_reason == "model_changed"


def test_voices_this_model_wrote_survive_a_reload(tmp_path) -> None:
    registry = _registry(tmp_path)
    opened = registry.match(_voice(1), LONG)
    assert registry.enroll_cluster(opened.cluster_id, "Alex")

    reloaded = SpeakerRegistry(tmp_path / "voices.json")

    assert reloaded.has_name("Alex")
    assert reloaded.reset_reason == ""
    saved = json.loads((tmp_path / "voices.json").read_text(encoding="utf-8"))
    assert saved["model"] == SPEAKER_MODEL_ID


def test_an_empty_store_is_not_reported_as_a_model_reset(tmp_path) -> None:
    (tmp_path / "voices.json").write_text(json.dumps({"voices": []}), encoding="utf-8")

    assert SpeakerRegistry(tmp_path / "voices.json").reset_reason == ""


# ── the length gate ──────────────────────────────────────────────────────


def test_a_short_segment_never_opens_a_new_speaker(tmp_path) -> None:
    """The fragmentation engine: a scrap too short to identify anyone failed
    to match its own speaker and was minted as "Speaker N" instead."""
    registry = _registry(tmp_path)

    match = registry.match(_voice(1), SHORT)

    assert match.kind == "none"
    assert match.cluster_id == -1


def test_a_long_segment_still_opens_one(tmp_path) -> None:
    registry = _registry(tmp_path)

    assert registry.match(_voice(1), LONG).kind == "cluster"


def test_a_caller_that_reports_no_duration_is_trusted(tmp_path) -> None:
    """Duration is optional so existing callers keep working."""
    registry = _registry(tmp_path)

    assert registry.match(_voice(1)).kind == "cluster"


def test_a_short_segment_can_still_be_recognised(tmp_path) -> None:
    """Matching is the safe direction — the gate must not cost people their
    name on every brief reply."""
    registry = _registry(tmp_path)
    voice = _voice(1)
    opened = registry.match(voice, LONG)
    assert registry.enroll_cluster(opened.cluster_id, "Alex")

    match = registry.match(voice, SHORT)

    assert match.kind == "named"
    assert match.label == "Alex"


def test_a_short_segment_does_not_move_a_stored_voiceprint(tmp_path) -> None:
    """A bad vector used to be averaged into whichever print it landed
    nearest, walking that person's identity toward somebody else."""
    registry = _registry(tmp_path)
    voice = _voice(1)
    opened = registry.match(voice, LONG)
    assert registry.enroll_cluster(opened.cluster_id, "Alex")
    before = np.array(registry.snapshot()["named"]["Alex"][0], dtype=np.float32)

    for seed in range(2, 12):
        drifting = 0.75 * voice + 0.25 * _voice(seed)
        registry.match(drifting / np.linalg.norm(drifting), SHORT)

    after = np.array(registry.snapshot()["named"]["Alex"][0], dtype=np.float32)
    assert np.allclose(before, after)


def test_long_segments_are_still_allowed_to_adapt_a_voiceprint(tmp_path) -> None:
    """The gate must not freeze enrollment — a voice drifts across a session
    and the print has to follow it."""
    registry = _registry(tmp_path)
    voice = _voice(1)
    opened = registry.match(voice, LONG)
    assert registry.enroll_cluster(opened.cluster_id, "Alex")
    before = np.array(registry.snapshot()["named"]["Alex"][0], dtype=np.float32)

    drifting = 0.9 * voice + 0.1 * _voice(99)
    registry.match(drifting / np.linalg.norm(drifting), LONG)

    after = np.array(registry.snapshot()["named"]["Alex"][0], dtype=np.float32)
    assert not np.allclose(before, after)
