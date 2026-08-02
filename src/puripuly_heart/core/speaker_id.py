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

# r349: ERes2NetV2 emits 192-dim embeddings (the previous ERes2Net base
# emitted 512). Voiceprints are only comparable within one model, so the store
# is stamped with the model that wrote it and cleared when that changes.
EMBEDDING_DIM = 192
SPEAKER_MODEL_ID = "eres2netv2_zh_16k"
# Segments shorter than this produce unstable voiceprints — skip labeling.
MIN_UTTERANCE_SECONDS = 1.2
# r349: and segments shorter than THIS are still too unstable to be believed
# about somebody NEW. Measured on the current model, one speaker's own samples
# score 0.87 at 3s and 0.77 at 2s but scatter down to 0.44 at 1.2s, where they
# overlap with different-speaker scores entirely. A segment under this length
# may still be labeled by matching, but may not create a cluster or move one.
MIN_TRUSTED_SECONDS = 2.0
NAMED_MATCH_THRESHOLD = 0.60
# r351: was 0.52, BELOW the naming bar -- two people were merged into one
# identity at a score explicitly deemed too weak to share a name, and then the
# cluster handed its name to both. Measured on the current model, different
# speakers top out around 0.38 and the same speaker bottoms out around 0.78,
# so this sits in empty space and no longer undercuts naming.
CLUSTER_MATCH_THRESHOLD = 0.58
# r351: how far the winning name must beat the SECOND-BEST NAME. Without this
# the best of N wrong answers wins on an absolute bar alone, which is why
# accuracy fell apart as names were added rather than staying flat.
NAMED_MARGIN = 0.08
# Centroid update weight for a new sample joining a cluster/enrollment.
CENTROID_EMA_ALPHA = 0.15
# r351: was 12, which was a hard ceiling on how many people could be told apart
# in one session -- and going past it silently bound the wrong voiceprint to a
# name (see the overflow path in match). This counts everyone who has SPOKEN
# this session, not everyone enrolled, and a public instance holds far more
# than twelve.
MAX_SESSION_CLUSTERS = 64
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
# r350: how close a voice must be to a recorded rejection to count as the same
# rejection. Deliberately looser than the join bar — the user said "not this
# person" about a VOICE, and it has to keep meaning that as the voice drifts.
DENY_SIMILARITY = 0.55
# Rejections are cheap to keep and only accumulate when the user corrects
# something, but the store should not grow without bound.
MAX_DENIALS = 64
# r338: the session cluster->name map makes a freshly named voice stick even
# when a later sample lands between the cluster and named thresholds. That
# convenience was handing enrolled names to STRANGERS: anyone within 0.52 of
# a named cluster inherited its name without ever passing the 0.60 named test.
# Inheriting now needs the sample to actually resemble THAT PERSON's stored
# voiceprints this much — below it the line falls back to "Speaker N", which
# is the documented trade (a wrong name is worse than an anonymous one).
# Deliberately UNDER the naming bar: a genuine utterance of a couple of seconds
# in room noise measures around 0.57 against its own speaker, and this is what
# keeps that person's name on it instead of dropping them to "Speaker N" when
# the audio is worst. Safe only because the join bar above is now higher than
# it -- a stranger cannot reach the cluster this applies within. It also still
# requires the voice's best-matching name to BE this cluster's name.
STICKY_NAME_THRESHOLD = 0.55
# r344: two people in a clear voice chat reached "Speaker 8" — same-speaker
# similarity wobbles around the 0.52 join bar, and every miss spawned a new
# cluster. A miss still joins the nearest cluster when it is CLEARLY nearest:
# at least CLUSTER_SOFT_THRESHOLD, with the runner-up at least CLUSTER_MARGIN
# further away. Ambiguous samples keep spawning their own cluster, so two
# genuinely similar speakers are never glued together by this path.
CLUSTER_SOFT_THRESHOLD = 0.40
CLUSTER_MARGIN = 0.12
# And clusters whose centroids drift together heal into one: fragments of the
# same voice converge as the EMA accumulates evidence. Two clusters carrying
# different names are never consolidated.
CLUSTER_CONSOLIDATE_THRESHOLD = 0.62


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
        # Set by _load when a model change forced the store to be cleared, so
        # the UI can explain an unexpectedly empty list.
        self.reset_reason = ""
        # r350: (voiceprint, name) pairs the user has explicitly rejected —
        # "this speaker is NOT that person". Anchored to the print, not the
        # cluster id, because reset_session renumbers clusters every session.
        self._denied: list[tuple[np.ndarray, str]] = []
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
        stored_model = str(data.get("model") or "")
        if data.get("voices") and stored_model != SPEAKER_MODEL_ID:
            # r349: a different model wrote these. Their numbers mean nothing
            # to the current one, so keep nobody rather than everybody wrong.
            self.reset_reason = "model_changed"
            logger.warning(
                "[SpeakerID] saved voices were recorded by %r but this build "
                "uses %r — clearing them; people need naming again",
                stored_model or "an older build",
                SPEAKER_MODEL_ID,
            )
            return
        self._load_denials(data)
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

    def _load_denials(self, data: dict) -> None:
        """r350: restore the user's 'not that person' corrections."""
        for entry in data.get("rejected", []) or []:
            try:
                name = str(entry.get("name") or "")
                raw = np.asarray(entry.get("voiceprint") or [], dtype=np.float32)
            except Exception:
                continue
            if not name or raw.shape != (EMBEDDING_DIM,):
                continue
            self._denied.append((_normalize(raw), name))

    def _save(self) -> None:
        # r349: this store now belongs to the current model, so an empty
        # list from here on is the user's own doing, not the upgrade's. Left
        # latched, it blamed the upgrade for every voice they later deleted.
        self.reset_reason = ""
        payload = {
            # r349: stamped so a future model change can detect and clear
            # rather than silently comparing incompatible numbers.
            "model": SPEAKER_MODEL_ID,
            # r350: corrections the user made, so they survive a restart.
            "rejected": [
                {"name": denied_name, "voiceprint": [float(x) for x in print_]}
                for print_, denied_name in self._denied
            ],
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

    def match(self, embedding: np.ndarray, seconds: float = 0.0) -> SpeakerMatch:
        """Label a voiceprint. `seconds` is how much audio produced it; 0.0
        means the caller does not know, which is treated as trustworthy so
        existing callers keep their behaviour."""
        vector = _normalize(np.asarray(embedding, dtype=np.float32))
        trusted = seconds <= 0.0 or seconds >= MIN_TRUSTED_SECONDS
        with self._lock:
            # r351: score each PERSON once (their best variant), then rank
            # people. The old loop tracked only the single best variant
            # overall, so there was no way to ask the question that matters
            # once several people are enrolled: how much better is the winner
            # than the next candidate?
            ranked: list[tuple[float, str, int]] = []
            for name, variants in self._named.items():
                if not variants:
                    continue
                sims = [float(np.dot(vector, variant)) for variant in variants]
                index = int(np.argmax(sims))
                ranked.append((sims[index], name, index))
            ranked.sort(key=lambda row: row[0], reverse=True)

            best_name, best_name_sim, best_variant_index = "", -1.0, -1
            runner_up_sim = -1.0
            if ranked:
                best_name_sim, best_name, best_variant_index = ranked[0]
            if len(ranked) > 1:
                runner_up_sim = ranked[1][0]

            best_cluster, best_cluster_sim = None, -1.0
            second_cluster_sim = -1.0
            for cluster in self._clusters:
                sim = float(np.dot(vector, cluster.centroid))
                if sim > best_cluster_sim:
                    second_cluster_sim = best_cluster_sim
                    best_cluster, best_cluster_sim = cluster, sim
                elif sim > second_cluster_sim:
                    second_cluster_sim = sim

            joins_best = best_cluster is not None and (
                best_cluster_sim >= CLUSTER_MATCH_THRESHOLD
                or (
                    # r344 margin join: clearly nearest, just under the bar.
                    best_cluster_sim >= CLUSTER_SOFT_THRESHOLD
                    and best_cluster_sim - second_cluster_sim >= CLUSTER_MARGIN
                )
            )

            def _join_cluster() -> "_Cluster":
                """EMA the joined cluster and heal fragments; returns the
                surviving cluster (the joined one may be absorbed)."""
                if not trusted:
                    # r349: ride along, but don't steer. A scrap this short can
                    # sit far enough off the speaker to drag the centroid onto
                    # a neighbouring voice.
                    return best_cluster
                best_cluster.centroid = _normalize(
                    (1 - CENTROID_EMA_ALPHA) * best_cluster.centroid
                    + CENTROID_EMA_ALPHA * vector
                )
                best_cluster.count += 1
                return self._consolidate_locked(best_cluster) or best_cluster

            if best_name and self._denied_locked(vector, best_name, best_name_sim):
                # r350: the user has said this voice is not that person. Fall
                # through to clustering rather than re-applying the name.
                logger.debug(
                    "[SpeakerID] %r suppressed (similarity=%.3f): rejected "
                    "for this voice", best_name, best_name_sim,
                )
                best_name, best_name_sim, best_variant_index = "", -1.0, -1

            # r351: two enrolled people this close to one voice means the
            # evidence does not pick between them. Leaving the line unnamed is
            # recoverable; putting one person's name on another person's words
            # is what destroys a chat log.
            decisive = (
                runner_up_sim < 0.0
                or (best_name_sim - runner_up_sim) >= NAMED_MARGIN
            )
            if best_name and best_name_sim >= NAMED_MATCH_THRESHOLD and not decisive:
                logger.info(
                    "[SpeakerID] refusing to guess between %r (%.3f) and the "
                    "next closest (%.3f) - margin %.3f < %.2f",
                    best_name, best_name_sim, runner_up_sim,
                    best_name_sim - runner_up_sim, NAMED_MARGIN,
                )
                best_name, best_name_sim, best_variant_index = "", -1.0, -1

            if best_name and best_name_sim >= NAMED_MATCH_THRESHOLD:
                # Nudge ONLY the variant that matched, so a call-channel print
                # never drags the VRChat one (or vice versa) toward it.
                variants = self._named[best_name]
                # r351: only clean wins teach. A borderline match used to nudge
                # the stored print toward the new sample, so one wrong match
                # made the next wrong match likelier and the error compounded.
                if trusted and best_name_sim >= NAMED_MATCH_THRESHOLD + NAMED_MARGIN:
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
                if joins_best:
                    # r344: a named hit still feeds its cluster. Freezing the
                    # centroid on naming let it drift from the living voice —
                    # the root of one person fragmenting into "Speaker N"s.
                    survivor = _join_cluster()
                    cluster_id = survivor.cluster_id
                    self._cluster_names.setdefault(cluster_id, best_name)
                return SpeakerMatch("named", best_name, cluster_id, best_name_sim)

            if joins_best:
                survivor = _join_cluster()
                session_name = self._cluster_names.get(survivor.cluster_id, "")
                # r338: only inherit the cluster's name when the voice really
                # does resemble that person — not merely the cluster they were
                # last seen in.
                if session_name and best_name == session_name and (
                    best_name_sim >= STICKY_NAME_THRESHOLD
                ):
                    return SpeakerMatch(
                        "named", session_name, survivor.cluster_id, best_cluster_sim
                    )
                if session_name:
                    logger.debug(
                        "[SpeakerID] withheld %r from cluster %d "
                        "(cluster sim=%.3f but name sim=%.3f < %.2f)",
                        session_name, survivor.cluster_id,
                        best_cluster_sim, best_name_sim, STICKY_NAME_THRESHOLD,
                    )
                return SpeakerMatch(
                    "cluster",
                    self._cluster_label(survivor.cluster_id),
                    survivor.cluster_id,
                    best_cluster_sim,
                )

            if len(self._clusters) >= MAX_SESSION_CLUSTERS:
                # r351: only reuse the closest cluster if the voice ACTUALLY
                # belongs to it. This used to hand back the nearest cluster
                # unconditionally, so the 13th speaker in a room was given
                # somebody else's identity — and naming that line enrolled the
                # wrong person's voiceprint under the new name, permanently.
                if (
                    best_cluster is not None
                    and best_cluster_sim >= CLUSTER_MATCH_THRESHOLD
                ):
                    return SpeakerMatch(
                        "cluster",
                        self._cluster_label(best_cluster.cluster_id),
                        best_cluster.cluster_id,
                        best_cluster_sim,
                    )
                # Nobody here sounds like them. Make room by dropping the
                # cluster with the least evidence behind it (never a named
                # one — those the user has spoken for).
                evictable = [
                    c for c in self._clusters
                    if not self._cluster_names.get(c.cluster_id)
                ]
                if not evictable or not trusted:
                    return SpeakerMatch("none", "", -1, 0.0)
                victim = min(evictable, key=lambda c: c.count)
                self._clusters.remove(victim)
                self._cluster_names.pop(victim.cluster_id, None)
                logger.info(
                    "[SpeakerID] session full (%d): evicted unnamed cluster %d "
                    "(count=%d) for a new voice",
                    MAX_SESSION_CLUSTERS, victim.cluster_id, victim.count,
                )

            # r358: a short segment DOES get its own identity now. r349
            # returned nothing here, which kept the stored voiceprints safe but
            # collapsed every uncertain voice into one indistinguishable
            # "Unknown speaker" — and telling two unknown people apart is the
            # more useful half. What it still cannot do is teach: `trusted`
            # gates every centroid and variant update above, so a short segment
            # can carry a label without ever moving a saved print.
            cluster = _Cluster(self._next_cluster_number, vector)
            self._next_cluster_number += 1
            logger.info(
                "[SpeakerID] new session speaker: cluster %d (%.2fs%s)",
                cluster.cluster_id,
                seconds,
                "" if trusted else ", provisional — will not update saved voices",
            )
            self._clusters.append(cluster)
            return SpeakerMatch(
                "cluster", self._cluster_label(cluster.cluster_id), cluster.cluster_id, 1.0
            )

    def _consolidate_locked(self, moved: "_Cluster") -> "_Cluster | None":
        """Merge `moved` into another cluster it has drifted onto (r344).

        Fragments of one voice converge as EMA evidence accumulates; without
        this they linger for the whole session as separate "Speaker N"s.
        Caller holds the lock. Clusters carrying two DIFFERENT names never
        merge — that decision belongs to the user.
        """
        moved_name = self._cluster_names.get(moved.cluster_id, "")
        for other in self._clusters:
            if other.cluster_id == moved.cluster_id:
                continue
            other_name = self._cluster_names.get(other.cluster_id, "")
            if moved_name and other_name and moved_name != other_name:
                continue
            sim = float(np.dot(moved.centroid, other.centroid))
            if sim < CLUSTER_CONSOLIDATE_THRESHOLD:
                continue
            # r350: merging hands the survivor one of these names. If the user
            # has rejected that name for either voice, this merge is exactly
            # the correction they made being undone — the whole reason a
            # detached cluster used to drift straight back onto its old name.
            merged_name = other_name or moved_name
            if merged_name and (
                self._denied_locked(moved.centroid, merged_name)
                or self._denied_locked(other.centroid, merged_name)
            ):
                logger.info(
                    "[SpeakerID] not merging clusters %d and %d (sim=%.3f): "
                    "the user rejected that name for this voice",
                    moved.cluster_id, other.cluster_id, sim,
                )
                continue
            # Keep the older (smaller id) cluster — its label is the one the
            # user has been looking at longest.
            keep, drop = (
                (other, moved) if other.cluster_id < moved.cluster_id else (moved, other)
            )
            total = max(keep.count + drop.count, 1)
            keep.centroid = _normalize(
                (keep.count / total) * keep.centroid
                + (drop.count / total) * drop.centroid
            )
            keep.count = total
            self._clusters.remove(drop)
            drop_name = self._cluster_names.pop(drop.cluster_id, "")
            surviving_name = self._cluster_names.get(keep.cluster_id, "") or (
                drop_name or moved_name or other_name
            )
            if surviving_name:
                self._cluster_names[keep.cluster_id] = surviving_name
            logger.info(
                "[SpeakerID] consolidated cluster %d into %d (sim=%.3f)",
                drop.cluster_id, keep.cluster_id, sim,
            )
            return keep
        return None

    # ── "not the same person" memory (r350) ──────────────────────────────

    def _denied_locked(
        self, vector: np.ndarray, name: str, name_sim: float | None = None
    ) -> bool:
        """Has the user rejected this name for a voice like this one?

        The comparison is RELATIVE. An absolute bar also blocked the genuine
        person, because a correction is only ever needed when two voices are
        close — so rejecting the impostor cost the real person their name. The
        name is withheld only when the voice resembles the rejected print at
        least as much as it resembles that person's own enrolled prints.
        """
        if not name or not self._denied:
            return False
        rejected_sim = max(
            (
                float(np.dot(vector, print_))
                for print_, denied_name in self._denied
                if denied_name == name
            ),
            default=-1.0,
        )
        if rejected_sim < DENY_SIMILARITY:
            return False
        if name_sim is None:
            name_sim = max(
                (float(np.dot(vector, v)) for v in self._named.get(name, [])),
                default=-1.0,
            )
        return rejected_sim >= name_sim

    def _deny_locked(self, vector: np.ndarray, name: str) -> None:
        if not name:
            return
        self._denied.append((_normalize(vector.copy()), name))
        if len(self._denied) > MAX_DENIALS:
            del self._denied[: len(self._denied) - MAX_DENIALS]

    def _allow_locked(self, vector: np.ndarray, name: str) -> None:
        """The user just named this voice — that outranks any old rejection."""
        if not name:
            return
        self._denied = [
            (print_, denied_name)
            for print_, denied_name in self._denied
            if not (
                denied_name == name
                and float(np.dot(vector, print_)) >= DENY_SIMILARITY
            )
        ]

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
            # r350: the user naming this voice outranks any earlier rejection
            # of that name for it.
            self._allow_locked(cluster.centroid, name)
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
                # r350: deliberately does NOT record a rejection. A cluster
                # holds both voices by the time a correction is needed (joining
                # costs 0.52, being named costs 0.60), so its centroid is ~0.99
                # similar to the real person — rejecting it would strip the
                # name from the very person the user is protecting. Rejections
                # are recorded per message, via reject_utterance.
                logger.info(
                    "[SpeakerID] cluster %d detached from %r", cluster_id, previous
                )
            return previous

    def reject_utterance(self, embedding: np.ndarray, name: str) -> bool:
        """Record "the voice in THIS message is not that person" (r350).

        Anchored to the message's own voiceprint, which is the only thing fine
        enough to tell two people apart once they share a cluster. The name is
        then withheld from voices that look more like this print than like the
        person's enrolled ones, by every route: a named match, cluster
        consolidation, and session-name inheritance.
        """
        name = name.strip()
        if not name:
            return False
        try:
            vector = _normalize(np.asarray(embedding, dtype=np.float32))
        except Exception:
            return False
        if vector.shape != (EMBEDDING_DIM,):
            return False
        with self._lock:
            self._deny_locked(vector, name)
            # Unbind any session cluster this message's voice is sitting in, so
            # the label disappears from the log straight away instead of after
            # the next utterance.
            for cluster in self._clusters:
                if self._cluster_names.get(cluster.cluster_id) != name:
                    continue
                if float(np.dot(vector, cluster.centroid)) >= CLUSTER_MATCH_THRESHOLD:
                    self._cluster_names.pop(cluster.cluster_id, None)
            logger.info("[SpeakerID] recorded that a voice is NOT %r", name)
            self._save()
            return True

    def enroll_embedding(self, embedding: np.ndarray, name: str) -> bool:
        """Name a single message's voice directly (r350).

        An unidentified line has no cluster to enroll, so without this the user
        can see a message they know the speaker of and have no way to say so.
        The user is the authority, so this also clears any earlier rejection.
        """
        name = name.strip()
        if not name:
            return False
        try:
            vector = _normalize(np.asarray(embedding, dtype=np.float32))
        except Exception:
            return False
        if vector.shape != (EMBEDDING_DIM,):
            return False
        with self._lock:
            self._allow_locked(vector, name)
            variants = self._named.setdefault(name, [])
            sims = [float(np.dot(vector, v)) for v in variants]
            nearest = int(np.argmax(sims)) if sims else -1
            if nearest >= 0 and sims[nearest] >= VARIANT_MERGE_THRESHOLD:
                variants[nearest] = _normalize(
                    0.5 * variants[nearest] + 0.5 * vector
                )
            else:
                variants.append(vector.copy())
                if len(variants) > MAX_VARIANTS_PER_NAME:
                    variants.pop(0)
            self._named_counts[name] = self._named_counts.get(name, 1) + 1
            self._save()
            return True

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
    "MIN_TRUSTED_SECONDS",
    "SPEAKER_MODEL_ID",
    "NAMED_MATCH_THRESHOLD",
    "SpeakerMatch",
    "SpeakerRegistry",
]
