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
        self._named: dict[str, np.ndarray] = {}
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
            vector = entry.get("centroid")
            if not name or not isinstance(vector, list):
                continue
            arr = np.asarray(vector, dtype=np.float32)
            if arr.shape != (EMBEDDING_DIM,):
                continue
            self._named[name] = _normalize(arr)
            self._named_counts[name] = int(entry.get("count", 1))

    def _save(self) -> None:
        payload = {
            "voices": [
                {
                    "name": name,
                    "centroid": [round(float(x), 6) for x in centroid],
                    "count": self._named_counts.get(name, 1),
                }
                for name, centroid in self._named.items()
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
            best_name, best_name_sim = "", -1.0
            for name, centroid in self._named.items():
                sim = float(np.dot(vector, centroid))
                if sim > best_name_sim:
                    best_name, best_name_sim = name, sim

            best_cluster, best_cluster_sim = None, -1.0
            for cluster in self._clusters:
                sim = float(np.dot(vector, cluster.centroid))
                if sim > best_cluster_sim:
                    best_cluster, best_cluster_sim = cluster, sim

            if best_name and best_name_sim >= NAMED_MATCH_THRESHOLD:
                # Nudge the enrolled centroid toward the fresh sample so a
                # voice tracked across sessions keeps up with mic changes.
                self._named[best_name] = _normalize(
                    (1 - CENTROID_EMA_ALPHA) * self._named[best_name]
                    + CENTROID_EMA_ALPHA * vector
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
                if session_name:
                    return SpeakerMatch(
                        "named", session_name, best_cluster.cluster_id, best_cluster_sim
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
                self._named[name] = _normalize(
                    0.5 * self._named[name] + 0.5 * cluster.centroid
                )
                self._named_counts[name] = self._named_counts.get(name, 1) + cluster.count
            else:
                self._named[name] = cluster.centroid.copy()
                self._named_counts[name] = cluster.count
            self._cluster_names[cluster_id] = name
            self._save()
            return True

    def forget(self, name: str) -> bool:
        with self._lock:
            if name not in self._named:
                return False
            del self._named[name]
            self._named_counts.pop(name, None)
            self._save()
            return True

    def enrolled_names(self) -> list[str]:
        with self._lock:
            return sorted(self._named)

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
