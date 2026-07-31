"""Local speaker identification (r318).

Every peer utterance long enough to be reliable gets a 512-dim voiceprint
(ERes2Net ONNX via the main process's onnxruntime — NOT sherpa's private
runtime, whose older ORT cannot load this model class). Voiceprints are
matched against locally-enrolled named voices first, then against this
session's anonymous clusters ("Speaker 1", "Speaker 2", ...).

Everything stays on this machine: enrolled voiceprints live in a JSON file
next to settings.json and are never transmitted anywhere.

Spike numbers (2026-07-30, TTS voices, kaldi-native-fbank features):
same-speaker cosine ~0.76, different-speaker ~0.19-0.28 with one hard
cross-language female pair at 0.54 — hence NAMED_MATCH_THRESHOLD 0.60 and
CLUSTER_MATCH_THRESHOLD 0.52 leave margin on both sides. VoIP compression
narrows the gap in the wild; thresholds err toward "new speaker" because a
wrong name on a line is worse than an anonymous label.
"""
from __future__ import annotations

import json
import logging
import threading
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

logger = logging.getLogger(__name__)

EMBEDDING_DIM = 512
# Segments shorter than this produce unstable voiceprints — skip labeling.
MIN_UTTERANCE_SECONDS = 1.2
NAMED_MATCH_THRESHOLD = 0.60
CLUSTER_MATCH_THRESHOLD = 0.52
# Centroid update weight for a new sample joining a cluster/enrollment.
CENTROID_EMA_ALPHA = 0.15
MAX_SESSION_CLUSTERS = 12
# r330: one enrolled person can hold several voiceprint VARIANTS — the same
# voice through a Discord/OOPZ call and through VRChat's spatialized in-game
# audio lands in measurably different places (different codec, plus distance
# attenuation / direction / reverb). The old single centroid was averaged
# 50/50 on re-enrollment, drifting to a midpoint that matched NEITHER context
# and forcing the user to re-name the same person every session. Variants are
# matched independently (best wins); a re-name close to an existing variant
# refines it, a distant one becomes a new variant.
MAX_VARIANTS_PER_NAME = 4
# A re-enrollment at/above this similarity refines the nearest variant;
# below it, the new print is kept as a separate channel of the same voice.
VARIANT_MERGE_THRESHOLD = 0.70
# r338: the session cluster->name map makes a freshly named voice stick even
# when a later sample lands between the cluster and named thresholds. That
# convenience was handing enrolled names to STRANGERS: anyone within 0.52 of
# a named cluster inherited its name without ever passing the 0.60 named test.
# Inheriting now needs the sample to actually resemble THAT PERSON's stored
# voiceprints this much — below it the line falls back to "Speaker N", which
# is the documented trade (a wrong name is worse than an anonymous one).
STICKY_NAME_THRESHOLD = 0.55


def _normalize(vector: np.ndarray) -> np.ndarray:
    norm = float(np.linalg.norm(vector))
    if norm <= 0.0:
        return vector.astype(np.float32)
    return (vector / norm).astype(np.float32)


@dataclass(slots=True)
class SpeakerMatch:
    kind: str        # "named" | "cluster" | "none"
    label: str       # enrolled name, or "Speaker N", or ""
    cluster_id: int  # session cluster the sample joined (-1 for pure named hit)
    similarity: float


@dataclass(slots=True)
class _Cluster:
    cluster_id: int
    centroid: np.ndarray
    count: int = 1


class SpeakerRegistry:
    """Session clustering + persistent named enrollment. Not thread-safe by
    design intent (hub loop only), but guarded anyway — naming arrives from
    UI callbacks."""

    def __init__(self, store_path: Path) -> None:
        self._store_path = store_path
        self._lock = threading.Lock()
        # name -> list of unit-norm variant centroids (r330)
        self._named: dict[str, list[np.ndarray]] = {}
        self._named_counts: dict[str, int] = {}
        self._clusters: list[_Cluster] = []
        self._next_cluster_number = 1
        # Session map cluster_id -> enrolled name. Makes naming sticky for the
        # rest of the session even when a borderline sample re-matches its
        # cluster (>=0.52) but misses the stricter named threshold (>=0.60) —
        # without this, a just-named voice could keep showing "Speaker N".
        self._cluster_names: dict[int, str] = {}
        self._load()

    # ── persistence ──────────────────────────────────────────────────────

    def _load(self) -> None:
        try:
            data = json.loads(self._store_path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return
        except Exception:
            logger.warning("[SpeakerID] voices store unreadable — starting empty")
            return
        for entry in data.get("voices", []):
            name = str(entry.get("name") or "").strip()
            if not name:
                continue
            raw_variants = entry.get("variants")
            if not isinstance(raw_variants, list):
                # Legacy single-centroid store (r318-r329) — one variant.
                legacy = entry.get("centroid")
                raw_variants = [legacy] if isinstance(legacy, list) else []
            variants: list[np.ndarray] = []
            for raw in raw_variants:
                if not isinstance(raw, list):
                    continue
                arr = np.asarray(raw, dtype=np.float32)
                if arr.shape == (EMBEDDING_DIM,):
                    variants.append(_normalize(arr))
            if not variants:
                continue
            self._named[name] = variants[:MAX_VARIANTS_PER_NAME]
            self._named_counts[name] = int(entry.get("count", 1))

    def _save(self) -> None:
        payload = {
            "voices": [
                {
                    "name": name,
                    "variants": [
                        [round(float(x), 6) for x in variant] for variant in variants
                    ],
                    "count": self._named_counts.get(name, 1),
                }
                for name, variants in self._named.items()
            ]
        }
        try:
            self._store_path.parent.mkdir(parents=True, exist_ok=True)
            self._store_path.write_text(
                json.dumps(payload, ensure_ascii=False), encoding="utf-8"
            )
        except Exception:
            logger.warning("[SpeakerID] could not persist voices store", exc_info=True)

    # ── matching ─────────────────────────────────────────────────────────

    def match(self, embedding: np.ndarray) -> SpeakerMatch:
        vector = _normalize(np.asarray(embedding, dtype=np.float32))
        with self._lock:
            best_name, best_name_sim, best_variant_index = "", -1.0, -1
            for name, variants in self._named.items():
                for index, variant in enumerate(variants):
                    sim = float(np.dot(vector, variant))
                    if sim > best_name_sim:
                        best_name, best_name_sim, best_variant_index = name, sim, index

            best_cluster, best_cluster_sim = None, -1.0
            for cluster in self._clusters:
                sim = float(np.dot(vector, cluster.centroid))
                if sim > best_cluster_sim:
                    best_cluster, best_cluster_sim = cluster, sim

            if best_name and best_name_sim >= NAMED_MATCH_THRESHOLD:
                # Nudge ONLY the variant that matched, so a call-channel print
                # never drags the VRChat one (or vice versa) toward it.
                variants = self._named[best_name]
                variants[best_variant_index] = _normalize(
                    (1 - CENTROID_EMA_ALPHA) * variants[best_variant_index]
                    + CENTROID_EMA_ALPHA * vector
                )
                logger.debug(
                    "[SpeakerID] matched %r variant=%d similarity=%.3f",
                    best_name, best_variant_index, best_name_sim,
                )
                self._named_counts[best_name] = self._named_counts.get(best_name, 1) + 1
                cluster_id = -1
                if best_cluster is not None and best_cluster_sim >= CLUSTER_MATCH_THRESHOLD:
                    cluster_id = best_cluster.cluster_id
                    self._cluster_names.setdefault(cluster_id, best_name)
                return SpeakerMatch("named", best_name, cluster_id, best_name_sim)

            if best_cluster is not None and best_cluster_sim >= CLUSTER_MATCH_THRESHOLD:
                best_cluster.centroid = _normalize(
                    (1 - CENTROID_EMA_ALPHA) * best_cluster.centroid
                    + CENTROID_EMA_ALPHA * vector
                )
                best_cluster.count += 1
                session_name = self._cluster_names.get(best_cluster.cluster_id, "")
                # r338: only inherit the cluster's name when the voice really
                # does resemble that person — not merely the cluster they were
                # last seen in.
                if session_name and best_name == session_name and (
                    best_name_sim >= STICKY_NAME_THRESHOLD
                ):
                    return SpeakerMatch(
                        "named", session_name, best_cluster.cluster_id, best_cluster_sim
                    )
                if session_name:
                    logger.debug(
                        "[SpeakerID] withheld %r from cluster %d "
                        "(cluster sim=%.3f but name sim=%.3f < %.2f)",
                        session_name, best_cluster.cluster_id,
                        best_cluster_sim, best_name_sim, STICKY_NAME_THRESHOLD,
                    )
                return SpeakerMatch(
                    "cluster",
                    self._cluster_label(best_cluster.cluster_id),
                    best_cluster.cluster_id,
                    best_cluster_sim,
                )

            if len(self._clusters) >= MAX_SESSION_CLUSTERS:
                # Room is chaos — reuse the closest cluster rather than grow.
                if best_cluster is not None:
                    return SpeakerMatch(
                        "cluster",
                        self._cluster_label(best_cluster.cluster_id),
                        best_cluster.cluster_id,
                        best_cluster_sim,
                    )
                return SpeakerMatch("none", "", -1, 0.0)

            cluster = _Cluster(self._next_cluster_number, vector)
            self._next_cluster_number += 1
            self._clusters.append(cluster)
            return SpeakerMatch(
                "cluster", self._cluster_label(cluster.cluster_id), cluster.cluster_id, 1.0
            )

    @staticmethod
    def _cluster_label(cluster_id: int) -> str:
        return f"Speaker {cluster_id}"

    # ── enrollment (the naming UI calls these) ───────────────────────────

    def enroll_cluster(self, cluster_id: int, name: str) -> bool:
        """Name a session cluster; its centroid becomes (or merges into) the
        enrolled voice."""
        name = name.strip()
        if not name:
            return False
        with self._lock:
            cluster = next(
                (c for c in self._clusters if c.cluster_id == cluster_id), None
            )
            if cluster is None:
                return False
            if name in self._named:
                variants = self._named[name]
                sims = [float(np.dot(cluster.centroid, v)) for v in variants]
                nearest = int(np.argmax(sims)) if sims else -1
                if nearest >= 0 and sims[nearest] >= VARIANT_MERGE_THRESHOLD:
                    # Same channel as an existing print — refine it.
                    variants[nearest] = _normalize(
                        0.5 * variants[nearest] + 0.5 * cluster.centroid
                    )
                    logger.info(
                        "[SpeakerID] refined %r variant=%d (similarity=%.3f)",
                        name, nearest, sims[nearest],
                    )
                else:
                    # Distinct channel (e.g. VRChat vs a voice call) — keep it
                    # as its own variant instead of averaging them together.
                    variants.append(cluster.centroid.copy())
                    if len(variants) > MAX_VARIANTS_PER_NAME:
                        variants.pop(0)  # oldest out
                    logger.info(
                        "[SpeakerID] added a new voice variant for %r "
                        "(closest existing similarity=%.3f, variants=%d)",
                        name,
                        max(sims) if sims else float("nan"),
                        len(variants),
                    )
                self._named_counts[name] = self._named_counts.get(name, 1) + cluster.count
            else:
                self._named[name] = [cluster.centroid.copy()]
                self._named_counts[name] = cluster.count
            self._cluster_names[cluster_id] = name
            self._save()
            return True

    def has_name(self, name: str) -> bool:
        """Does an enrolled voice already use this name? (r341)

        The UI asks before saving, so a merge can be confirmed rather than
        discovered afterwards.
        """
        with self._lock:
            return name.strip() in self._named

    def snapshot(self) -> dict:
        """Copy of the enrolled state, for one level of undo (r341)."""
        with self._lock:
            return {
                "named": {
                    name: [variant.copy() for variant in variants]
                    for name, variants in self._named.items()
                },
                "counts": dict(self._named_counts),
                "cluster_names": dict(self._cluster_names),
            }

    def restore(self, snapshot: dict) -> bool:
        """Put back a snapshot() — undoing a merge, rename or deletion."""
        if not isinstance(snapshot, dict) or "named" not in snapshot:
            return False
        with self._lock:
            self._named = {
                name: [variant.copy() for variant in variants]
                for name, variants in snapshot["named"].items()
            }
            self._named_counts = dict(snapshot.get("counts", {}))
            self._cluster_names = dict(snapshot.get("cluster_names", {}))
            self._save()
            logger.info("[SpeakerID] restored a previous voices snapshot")
            return True

    def detach_cluster(self, cluster_id: int) -> str:
        """Unbind this session cluster from whatever name it carries (r341).

        For "this speaker is not that person": the cluster goes back to an
        anonymous label WITHOUT touching the named voice, so correcting a
        misidentified line cannot rewrite the real person's history. Returns
        the name it used to carry ("" if none).
        """
        with self._lock:
            previous = self._cluster_names.pop(int(cluster_id), "")
            if previous:
                logger.info(
                    "[SpeakerID] cluster %d detached from %r", cluster_id, previous
                )
            return previous

    def rename(self, old_name: str, new_name: str) -> bool:
        """Rename an enrolled voice, merging into an existing name if taken.

        r338: renaming is a NAME operation. Every session cluster mapped to
        the old name follows it, so the chat log can relabel all of them at
        once instead of only the entry that was clicked.
        """
        old_name = old_name.strip()
        new_name = new_name.strip()
        if not old_name or not new_name or old_name == new_name:
            return False
        with self._lock:
            if old_name not in self._named:
                return False
            moving = self._named.pop(old_name)
            moving_count = self._named_counts.pop(old_name, 1)
            if new_name in self._named:
                # Merging two names for one person: keep every distinct
                # voiceprint, drop near-duplicates so the variant budget is
                # not spent twice on the same channel.
                target = self._named[new_name]
                for variant in moving:
                    sims = [float(np.dot(variant, existing)) for existing in target]
                    if sims and max(sims) >= VARIANT_MERGE_THRESHOLD:
                        nearest = int(np.argmax(sims))
                        target[nearest] = _normalize(
                            0.5 * target[nearest] + 0.5 * variant
                        )
                    else:
                        target.append(variant)
                if len(target) > MAX_VARIANTS_PER_NAME:
                    # oldest out, same rule as enroll_cluster
                    del target[: len(target) - MAX_VARIANTS_PER_NAME]
                self._named_counts[new_name] = (
                    self._named_counts.get(new_name, 1) + moving_count
                )
            else:
                self._named[new_name] = moving
                self._named_counts[new_name] = moving_count
            for cluster_id, mapped in list(self._cluster_names.items()):
                if mapped == old_name:
                    self._cluster_names[cluster_id] = new_name
            self._save()
            logger.info("[SpeakerID] renamed %r -> %r", old_name, new_name)
            return True

    def clusters_for_name(self, name: str) -> list[int]:
        """Every session cluster currently carrying this name (r338)."""
        name = name.strip()
        if not name:
            return []
        with self._lock:
            return [
                cluster_id
                for cluster_id, mapped in self._cluster_names.items()
                if mapped == name
            ]

    def enrolled_summary(self) -> list[tuple[str, int, int]]:
        """(name, voiceprint variants, utterances heard) for the manager UI."""
        with self._lock:
            return [
                (name, len(variants), int(self._named_counts.get(name, 1)))
                for name, variants in sorted(self._named.items())
            ]

    def forget(self, name: str) -> bool:
        with self._lock:
            if name not in self._named:
                return False
            del self._named[name]
            self._named_counts.pop(name, None)
            # r338: drop the session mapping too. Without this the cluster
            # keeps the deleted name for the rest of the session and the very
            # next utterance re-labels with a voice the user just removed.
            for cluster_id, mapped in list(self._cluster_names.items()):
                if mapped == name:
                    del self._cluster_names[cluster_id]
            self._save()
            return True

    def enrolled_names(self) -> list[str]:
        with self._lock:
            return sorted(self._named)

    def variant_count(self, name: str) -> int:
        """How many distinct voiceprints are stored for this person (r330)."""
        with self._lock:
            return len(self._named.get(name, ()))

    def name_for_cluster(self, cluster_id: int) -> str:
        """The name this session's cluster was enrolled under, if any."""
        with self._lock:
            return self._cluster_names.get(cluster_id, "")

    def reset_session(self) -> None:
        with self._lock:
            self._clusters.clear()
            self._cluster_names.clear()
            self._next_cluster_number = 1


__all__ = [
    "CLUSTER_MATCH_THRESHOLD",
    "EMBEDDING_DIM",
    "MIN_UTTERANCE_SECONDS",
    "NAMED_MATCH_THRESHOLD",
    "SpeakerMatch",
    "SpeakerRegistry",
]
