from __future__ import annotations

import asyncio
import threading
import contextlib
import importlib
import logging
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import AsyncIterator, Callable

import numpy as np

from puripuly_heart.core.audio.diagnostics import compute_audio_frame_metrics
from puripuly_heart.core.audio.format import AudioFrameF32, pcm16le_bytes_to_float32
from puripuly_heart.core.local_qwen_runtime import (
    LocalQwenRuntimeBootstrapError,
    ensure_local_qwen_windows_runtime,
)
from puripuly_heart.core.local_stt_assets import (
    LOCAL_STT_MODEL_ID,
    load_local_stt_asset_manifest,
    validate_local_stt_runtime_ready,
)
from puripuly_heart.core.stt.backend import (
    STTBackend,
    STTBackendSession,
    STTBackendTranscriptEvent,
)
from puripuly_heart.core.stt.local_qwen_hallucination import (
    is_known_local_qwen_hallucination,
)

DEFAULT_SHERPA_NUM_THREADS = 3
LOCAL_QWEN_RECOGNIZER_SAMPLE_RATE_HZ = 16000
_KNOWN_HALLUCINATION_LOG_REDACTION = "<known-local-qwen-hallucination>"
# Mean per-token log-prob below which a transcript is treated as garbage the model
# hallucinated from noise/silence. Deliberately lenient so confident real speech is
# never dropped; actual avg_logprob values are logged so this can be tightened from
# real-world logs if needed.
LOCAL_QWEN_MIN_AVG_LOGPROB = -2.3
# Segments this quiet (post gain/denoise) are near-silence: the model reliably
# invents stock phrases from them ("虚构", "的答案是"), and those inventions
# score just above the normal confidence bar. Hold quiet audio to a stricter
# bar instead of blocklisting individual phrases (r305).
LOCAL_QWEN_QUIET_SEGMENT_RMS = 0.0075          # ~ -42 dBFS
LOCAL_QWEN_QUIET_MIN_AVG_LOGPROB = -1.0
# A native decode that has not returned after this long is treated as wedged,
# not slow. Observed 2026-08-11: a DirectML inference call on a 4.2s clip sat
# inside onnxruntime for 6+ minutes while a game saturated the GPU — the
# utterance never produced a transcript and the whole peer pipeline starved
# behind it. Normal decodes run well under 1x real-time even on CPU, so 30s is
# far outside anything a healthy recognizer does.
LOCAL_QWEN_DECODE_TIMEOUT_S = 30.0
logger = logging.getLogger(__name__)


def _mean_log_prob(ys_log_probs: object) -> float | None:
    """Return the mean of the model's per-token log-probs, or None if unavailable.

    Defensive against shape: ``ys_log_probs`` may be absent, a flat sequence of
    floats, or a nested sequence (per-token lists). Non-numeric / empty inputs
    yield None so the confidence filter simply no-ops.
    """

    if not ys_log_probs:
        return None
    flat: list[float] = []
    try:
        for entry in ys_log_probs:
            if isinstance(entry, (list, tuple, np.ndarray)):
                flat.extend(float(v) for v in np.asarray(entry).reshape(-1))
            else:
                flat.append(float(entry))
    except (TypeError, ValueError):
        return None
    if not flat:
        return None
    return sum(flat) / len(flat)


class LocalQwenSherpaLoadError(RuntimeError):
    """Raised when the local sherpa recognizer cannot be initialized."""


class LocalQwenSherpaInferenceError(RuntimeError):
    """Raised when local sherpa inference fails for an utterance."""


class LocalQwenSherpaDecodeTimeoutError(LocalQwenSherpaInferenceError):
    """A native decode call did not return within the timeout.

    The hung thread cannot be cancelled or joined — the wedge is inside a
    native onnxruntime call. The recognizer it was decoding on is poisoned
    (never used again) and a fresh instance is built on the next utterance.
    """


class LocalQwenLowMemoryError(LocalQwenSherpaLoadError):
    """The machine does not have enough free memory to load the model.

    r386: raised INSTEAD of attempting the load. On a machine with ~2-3GB free
    the construction demand pages the whole system to a crawl — the app freezes
    to a spinner, no error is ever logged, and the load can outlive the user's
    patience by minutes. A refusal with numbers is strictly better.

    Deliberately NOT the generic load error alone: the generic handler treats
    load failures as corrupt-install and re-downloads the 900MB model, which is
    exactly wrong when the problem is RAM.
    """

    def __init__(self, *, available_mb: int, needed_mb: int) -> None:
        self.available_mb = int(available_mb)
        self.needed_mb = int(needed_mb)
        # Read by the session retry loop (core/stt/controller.py) as a duck-
        # typed attribute — retrying a memory refusal with backoff is pointless
        # and, worse, swallows the typed error the UI needs to localize.
        self.non_retryable = True
        super().__init__(
            f"Not enough free memory to load the local speech model: "
            f"~{needed_mb} MB needed, {available_mb} MB available. Close other "
            f"programs (games, emulators, browsers) and toggle MIC again, or "
            f"choose a cloud recognizer in Settings."
        )


class _LocalQwenSherpaImportError(ImportError):
    """Internal sentinel for sherpa_onnx import failures."""


def _log_prefix(stream_label: str | None) -> str:
    prefix = "[STT][local_qwen]"
    if stream_label:
        return f"{prefix}[{stream_label}]"
    return prefix


def _audio_diag_prefix(stream_label: str | None) -> str:
    prefix = "[AudioDiag][local_qwen]"
    if stream_label:
        return f"{prefix}[{stream_label}]"
    return prefix


def _transcript_text_for_log(text: str) -> str:
    if is_known_local_qwen_hallucination(text):
        return _KNOWN_HALLUCINATION_LOG_REDACTION
    return text


_SPECIAL_TOKEN_RE = re.compile(r"<\|[^|<>]{1,32}\|>")
# Decode repetition loops: a single char repeated 5+ times, or a short unit
# repeated 4+ times ('堵' x80 after a legit prefix flooded the whole overlay).
_CHAR_RUN_RE = re.compile(r"(.)\1{4,}")
_UNIT_LOOP_RE = re.compile(r"(.{2,6}?)\1{3,}")


def _strip_asr_meta_wrapper(text: str) -> str:
    """The Qwen3 ASR model occasionally leaks its internal prompt format instead
    of a bare transcript ('system\\nlanguage Chinese<asr_text>是河南的吗？' —
    captured live while decoding Chinese speech under an Indonesian language
    setting). The real speech follows the last <asr_text> marker — salvage it
    and drop the meta prefix."""
    if "<asr_text>" in text:
        text = text.rsplit("<asr_text>", 1)[1]
    text = text.replace("</asr_text>", "").strip()
    # Raw SPECIAL TOKENS also leak: '罗曼达。<|endoftext|>Human Rights Watch
    # （人权观察）是全球最大的人权组织…' was emitted verbatim on ambiguous audio.
    # Everything from the first special token onward is training-data
    # continuation, not speech — truncate (an all-garbage segment becomes
    # empty and the caller's `if text:` gate drops it).
    m = _SPECIAL_TOKEN_RE.search(text)
    if m:
        logger.warning("[STT][local_qwen] special-token leak truncated: %r",
                       text[m.start():m.start() + 60])
        text = text[:m.start()].strip()
    # Collapse decode loops instead of dropping the segment: the sentence
    # before the loop is real speech ('不是啊，怎么一直' + 堵 x80 -> keep the
    # sentence with a natural 3x stutter). _looks_repetitive only catches
    # WHOLLY repetitive strings, so a legit prefix slipped these through.
    collapsed = _CHAR_RUN_RE.sub(lambda mm: mm.group(1) * 3, text)
    collapsed = _UNIT_LOOP_RE.sub(lambda mm: mm.group(1) * 2, collapsed)
    if collapsed != text:
        logger.warning("[STT][local_qwen] repetition loop collapsed: %d -> %d chars",
                       len(text), len(collapsed))
        text = collapsed
    return text


def _looks_repetitive(text: str) -> bool:
    stripped = text.strip()
    if len(stripped) < 6:
        return False
    for unit_len in range(1, (len(stripped) // 2) + 1):
        if len(stripped) % unit_len == 0 and stripped == stripped[:unit_len] * (
            len(stripped) // unit_len
        ):
            return len(stripped) // unit_len >= 3
    if len(stripped) < 12:
        return False
    return len(set(stripped)) <= max(4, len(stripped) // 8)


def _looks_script_mismatched(text: str, language_hint: str | None) -> bool:
    if not text or language_hint != "Korean":
        return False
    cjk = sum("\u4e00" <= ch <= "\u9fff" for ch in text)
    latin = sum("a" <= ch.lower() <= "z" for ch in text)
    return cjk >= 3 or latin >= max(5, len(text) // 2)


def _pcm16le_duration_ms(pcm16le_size_bytes: int, sample_rate_hz: int) -> float:
    if pcm16le_size_bytes <= 0:
        return 0.0
    return _sample_count_duration_ms(pcm16le_size_bytes // 2, sample_rate_hz)


def _sample_count_duration_ms(sample_count: int, sample_rate_hz: int) -> float:
    if sample_count <= 0 or sample_rate_hz <= 0:
        return 0.0
    return sample_count * 1000.0 / float(sample_rate_hz)


def create_local_qwen_sherpa_recognizer(
    *,
    model_dir: Path,
    num_threads: int,
    sample_rate_hz: int = LOCAL_QWEN_RECOGNIZER_SAMPLE_RATE_HZ,
    feature_dim: int = 128,
    provider: str = "cpu",
) -> object:
    if sample_rate_hz != LOCAL_QWEN_RECOGNIZER_SAMPLE_RATE_HZ:
        raise ValueError(f"sample_rate_hz must be {LOCAL_QWEN_RECOGNIZER_SAMPLE_RATE_HZ}")
    # r391: sherpa reads these with narrow paths and dies in native code — no
    # Python exception, no log line, the process just disappears — when the
    # directory contains characters outside the system codepage. Anyone whose
    # Windows account name is not ASCII hits this every single time, because
    # the model lives under %LOCALAPPDATA%.
    from puripuly_heart.core.ascii_paths import ascii_safe_path

    model_dir = ascii_safe_path(Path(model_dir))
    ensure_local_qwen_windows_runtime()
    try:
        import sherpa_onnx

        recognizer_module = importlib.import_module("sherpa_onnx.offline_recognizer")
    except ImportError as exc:
        raise _LocalQwenSherpaImportError from exc

    qwen3_config = sherpa_onnx.OfflineQwen3ASRModelConfig(
        conv_frontend=str(model_dir / "conv_frontend.onnx"),
        encoder=str(model_dir / "encoder.int8.onnx"),
        decoder=str(model_dir / "decoder.int8.onnx"),
        tokenizer=str(model_dir / "tokenizer"),
        max_total_len=512,
        max_new_tokens=128,
        temperature=1e-6,
        top_p=0.8,
        seed=42,
    )
    model_config = sherpa_onnx.OfflineModelConfig(
        qwen3_asr=qwen3_config,
        num_threads=num_threads,
        debug=False,
        provider=provider,
    )
    feat_config = sherpa_onnx.FeatureExtractorConfig(
        sampling_rate=sample_rate_hz,
        feature_dim=feature_dim,
    )
    recognizer_config = sherpa_onnx.OfflineRecognizerConfig(
        feat_config=feat_config,
        model_config=model_config,
        decoding_method="greedy_search",
    )
    recognizer_cls = getattr(recognizer_module, "_Recognizer")
    return recognizer_cls(recognizer_config)


# ── shared recognizer cache (r386) ───────────────────────────────────────────
# One recognizer instance costs ~1.15GB of RSS (measured). Construction is
# config-identical for both channels — language hints, denoise and confidence
# filtering are decode-time concerns — so self and peer share ONE instance,
# keyed on the only things construction actually consumes. Concurrent decodes
# on a shared instance are safe: verified empirically (2 threads x 6 rounds,
# zero crashes, zero divergence) and it is upstream's own server pattern.
#
# Instances are retained for the process lifetime on purpose. Backends are
# recreated on trivial settings churn (the r331/r333/r345 reload bugs), so a
# cache tied to backend lifetime would guarantee pointless 1.15GB reloads.
_SHARED_RECOGNIZERS: dict[tuple, object] = {}
_SHARED_RECOGNIZERS_LOCK = threading.Lock()
# Serializes CONSTRUCTION globally. Two concurrent 1.15GB loads demand ~2.3GB
# at once — measured to page a 16GB machine with an emulator resident into a
# freeze the load never returns from.
_RECOGNIZER_BUILD_LOCK = threading.Lock()

# Measured instance cost (1142 MB RSS growth) plus working headroom. Below the
# floor the load refuses with numbers instead of thrashing the whole machine.
LOCAL_QWEN_MODEL_LOAD_ESTIMATE_MB = 1200
LOCAL_QWEN_MODEL_LOAD_FLOOR_MB = 1400


def _available_memory_mb() -> int | None:
    try:
        import psutil

        return int(psutil.virtual_memory().available / (1024 * 1024))
    except Exception:
        return None  # diagnostics must never block the load themselves


# Set on a recognizer whose decode timed out. An attribute (not an id() set)
# so the mark travels with the instance itself: BOTH channels share one
# instance, and the peer backend poisoning it must also stop the self backend
# — which holds its own reference — from ever decoding on it again.
_POISONED_ATTR = "_puripuly_poisoned_by_decode_timeout"


def _recognizer_is_poisoned(recognizer: object) -> bool:
    return bool(getattr(recognizer, _POISONED_ATTR, False))


def _mark_recognizer_poisoned(recognizer: object) -> None:
    with contextlib.suppress(Exception):
        setattr(recognizer, _POISONED_ATTR, True)


def _shared_recognizer_cached(key: tuple) -> object | None:
    with _SHARED_RECOGNIZERS_LOCK:
        recognizer = _SHARED_RECOGNIZERS.get(key)
        if recognizer is not None and _recognizer_is_poisoned(recognizer):
            # A decode on this instance hung inside native code; it must never
            # be handed out again. Evict so the next lookup rebuilds fresh.
            del _SHARED_RECOGNIZERS[key]
            return None
        return recognizer


def _reset_shared_local_qwen_recognizers() -> None:
    """Test hook: drop every cached instance."""
    with _SHARED_RECOGNIZERS_LOCK:
        _SHARED_RECOGNIZERS.clear()


def _require_memory_for_model_load() -> None:
    available = _available_memory_mb()
    if available is None:
        return
    if available < LOCAL_QWEN_MODEL_LOAD_FLOOR_MB:
        logger.warning(
            "[STT][local_qwen] refusing model load: %d MB available, ~%d MB needed",
            available,
            LOCAL_QWEN_MODEL_LOAD_ESTIMATE_MB,
        )
        raise LocalQwenLowMemoryError(
            available_mb=available, needed_mb=LOCAL_QWEN_MODEL_LOAD_ESTIMATE_MB
        )
    if available < LOCAL_QWEN_MODEL_LOAD_ESTIMATE_MB + 1000:
        logger.warning(
            "[STT][local_qwen] low memory for model load: %d MB available — "
            "the load will work but may be slow while the system pages",
            available,
        )


def _shared_recognizer(key: tuple, factory) -> object:
    existing = _shared_recognizer_cached(key)
    if existing is not None:
        return existing
    # Bounded, not blind: a build that never returns (the AV-interference hang
    # the 180s outer timeout exists for) would otherwise hold this lock
    # forever — every retry would then wait out the full 180s to fail with the
    # SAME misleading message, stranding one more worker thread each time.
    # 170s keeps this under the outer timeout so the caller sees THIS message.
    if not _RECOGNIZER_BUILD_LOCK.acquire(timeout=170.0):
        raise LocalQwenSherpaLoadError(
            "another speech-model load is still in progress — wait a moment, "
            "then toggle MIC off and on to retry"
        )
    try:
        existing = _shared_recognizer_cached(key)
        if existing is not None:
            # The other channel built it while this one waited — 0 MB, 0 s.
            logger.info("[STT][local_qwen] reusing shared recognizer instance")
            return existing
        # Re-check under the lock: a build that just finished ahead of this
        # one has already consumed its ~1.15GB.
        _require_memory_for_model_load()
        recognizer = factory()
        with _SHARED_RECOGNIZERS_LOCK:
            _SHARED_RECOGNIZERS[key] = recognizer
        return recognizer
    finally:
        _RECOGNIZER_BUILD_LOCK.release()


@dataclass(slots=True)
class LocalQwenSherpaSTTBackend(STTBackend):
    model_dir: Path
    sample_rate_hz: int = 16000
    num_threads: int = DEFAULT_SHERPA_NUM_THREADS
    feature_dim: int = 128
    provider: str = "cpu"
    stream_label: str | None = None
    language_hint: str | None = None
    hotwords: tuple[str, ...] = ()
    # Mean per-token log-prob below which a transcript is dropped as garbage. None
    # disables the confidence filter entirely (no transcripts dropped on confidence).
    min_avg_logprob: float | None = LOCAL_QWEN_MIN_AVG_LOGPROB
    # Abandon a native decode that has not returned after this long and poison
    # the recognizer (see LocalQwenSherpaDecodeTimeoutError). <= 0 disables.
    decode_timeout_s: float = LOCAL_QWEN_DECODE_TIMEOUT_S
    # Spectral noise gate for steady background noise (fans/AC). Applied to
    # each segment before decoding; opt-in (settings.stt.mic_denoise).
    denoise: bool = False
    # r318: shared SpeakerEmbedder (core/speaker_embedder.py) — set on the
    # PEER backend when speaker identification is enabled; None otherwise.
    speaker_embedder: object | None = None
    diagnostics_enabled: Callable[[], bool] | None = None
    on_model_loading: object = None  # Callable[[str], None] — fired (with channel "self"|"peer") before model init
    on_model_loaded: object = None   # Callable[[str], None] — fired (with channel "self"|"peer") after model init
    # Which bundled manifest validates model_dir (Parakeet subclasses override).
    asset_model_id: str = LOCAL_STT_MODEL_ID
    _recognizer: object | None = field(init=False, default=None, repr=False)
    _load_lock: asyncio.Lock = field(init=False, repr=False)
    _decode_lock: asyncio.Lock = field(init=False, repr=False)

    def __post_init__(self) -> None:
        if self.sample_rate_hz != LOCAL_QWEN_RECOGNIZER_SAMPLE_RATE_HZ:
            raise ValueError(f"sample_rate_hz must be {LOCAL_QWEN_RECOGNIZER_SAMPLE_RATE_HZ}")
        if self.num_threads <= 0:
            raise ValueError("num_threads must be > 0")
        self._load_lock = asyncio.Lock()
        self._decode_lock = asyncio.Lock()

    async def open_session(self) -> STTBackendSession:
        await self._ensure_recognizer()
        return _LocalQwenSherpaSession(backend=self)

    async def warmup(self) -> None:
        # Idempotent: constructing the sherpa recognizer is the 7-9s cost
        # normally paid at the FIRST utterance ("green light but not truly
        # ready"). Warming at enable time moves that load to startup.
        await self._ensure_recognizer()

    async def close(self) -> None:
        # Drops this backend's reference only. The shared instance stays cached
        # (r386): backends are recreated on trivial settings churn, and tying
        # the 1.15GB model to backend lifetime is how the r331/r333/r345
        # reload bugs kept coming back.
        self._recognizer = None

    @property
    def _crash_sentinel_path(self) -> Path:
        label = self.stream_label or "self"
        return self.model_dir.parent / f".stt_load_sentinel_{label}"

    async def _ensure_recognizer(self) -> object:
        recognizer = self._recognizer
        if recognizer is not None:
            if not _recognizer_is_poisoned(recognizer):
                return recognizer
            # Poisoned by a timed-out decode — possibly on the OTHER channel's
            # backend, since the instance is shared. Drop the reference and
            # fall through to a fresh build.
            self._recognizer = None

        async with self._load_lock:
            recognizer = self._recognizer
            if recognizer is not None:
                if not _recognizer_is_poisoned(recognizer):
                    return recognizer
                self._recognizer = None
                logger.warning(
                    "%s discarding poisoned recognizer after a decode timeout — "
                    "building a fresh instance",
                    _log_prefix(self.stream_label),
                )

            # r386: refuse now if the machine cannot fit the load — but only
            # when a build will actually happen; picking up the shared
            # instance costs nothing however scarce memory is. While the OTHER
            # channel's build is in flight it has transiently consumed ~1GB,
            # so measuring now would refuse a caller whose answer is seconds
            # away and free — skip; the in-lock check inside _shared_recognizer
            # still guards the case where that build fails.
            if (
                _shared_recognizer_cached(self._recognizer_cache_key()) is None
                and not _RECOGNIZER_BUILD_LOCK.locked()
            ):
                _require_memory_for_model_load()

            # Check sentinel BEFORE any DLL loading. The crash from AV/memory issues
            # happens inside validate_local_stt_runtime_ready → ensure_local_qwen_windows_runtime,
            # so the sentinel must be written first or it will never survive the crash.
            sentinel = self._crash_sentinel_path
            if sentinel.exists():
                try:
                    sentinel.unlink(missing_ok=True)
                except Exception:
                    pass
                raise LocalQwenSherpaLoadError(
                    "Speech model crashed the app during last load — antivirus may be "
                    "blocking it. Try whitelisting the app folder, then toggle MIC off "
                    "and on to retry."
                )

            # Write sentinel now, before DLL loading begins. If the process hard-crashes
            # (e.g. AV kills it during DLL init), this file survives and we detect it
            # on the next run instead of crashing again silently.
            try:
                sentinel.write_text("loading", encoding="utf-8")
            except Exception:
                pass

            # Outer try/finally ensures the sentinel is always cleaned up, including
            # on CancelledError (BaseException), which bypasses except Exception blocks.
            try:
                await asyncio.to_thread(self._validate_runtime_assets)

                if callable(self.on_model_loading):
                    try:
                        self.on_model_loading(self.stream_label or "self")
                    except Exception:
                        pass

                try:
                    self._recognizer = await asyncio.wait_for(
                        asyncio.to_thread(self._create_recognizer),
                        timeout=180.0,
                    )
                except asyncio.TimeoutError:
                    raise LocalQwenSherpaLoadError(
                        "Speech model took too long to load — antivirus may be blocking it. "
                        "Try whitelisting the app folder, then toggle MIC off and on to retry."
                    )
            finally:
                try:
                    sentinel.unlink(missing_ok=True)
                except Exception:
                    pass
                if callable(self.on_model_loaded):
                    try:
                        self.on_model_loaded(self.stream_label or "self")
                    except Exception:
                        pass
            return self._recognizer

    def _validate_runtime_assets(self) -> None:
        validate_local_stt_runtime_ready(
            self.model_dir,
            manifest=load_local_stt_asset_manifest(self.asset_model_id),
        )

    def _recognizer_cache_key(self) -> tuple:
        return (
            str(Path(self.model_dir).resolve()),
            int(self.num_threads),
            int(self.feature_dim),
            str(self.provider),
        )

    def _create_recognizer(self) -> object:
        try:
            return _shared_recognizer(
                self._recognizer_cache_key(),
                lambda: create_local_qwen_sherpa_recognizer(
                    model_dir=self.model_dir,
                    num_threads=self.num_threads,
                    sample_rate_hz=LOCAL_QWEN_RECOGNIZER_SAMPLE_RATE_HZ,
                    feature_dim=self.feature_dim,
                    provider=self.provider,
                ),
            )
        except LocalQwenLowMemoryError:
            raise
        except LocalQwenRuntimeBootstrapError as exc:
            raise LocalQwenSherpaLoadError(str(exc)) from exc
        except _LocalQwenSherpaImportError as exc:
            raise LocalQwenSherpaLoadError("failed to import sherpa_onnx") from exc.__cause__
        except Exception as exc:
            raise LocalQwenSherpaLoadError(str(exc)) from exc

    async def decode_pcm16le(self, pcm16le: bytes) -> str:
        return await self.decode_f32(pcm16le_bytes_to_float32(pcm16le))  # -> (text, lang)

    async def decode_f32(self, samples_f32: np.ndarray) -> tuple[str, str | None]:
        recognizer = await self._ensure_recognizer()
        async with self._decode_lock:
            if _recognizer_is_poisoned(recognizer):
                # Poisoned while this call waited on the decode lock. Never
                # touch the wedged instance; the next utterance rebuilds.
                raise LocalQwenSherpaInferenceError(
                    "recognizer was poisoned by a timed-out decode; a fresh "
                    "instance will be built on the next utterance"
                )
            timeout_s = self.decode_timeout_s
            try:
                if timeout_s > 0:
                    return await asyncio.wait_for(
                        self._decode_in_abandonable_thread(recognizer, samples_f32),
                        timeout=timeout_s,
                    )
                return await self._decode_in_abandonable_thread(recognizer, samples_f32)
            except (asyncio.TimeoutError, TimeoutError):
                self._poison_recognizer(recognizer)
                audio_ms = _sample_count_duration_ms(
                    int(np.asarray(samples_f32).size), LOCAL_QWEN_RECOGNIZER_SAMPLE_RATE_HZ
                )
                logger.error(
                    "%s decode TIMED OUT after %.0fs (audio_ms=%.0f) — abandoning "
                    "the hung inference thread and poisoning the recognizer; a "
                    "fresh instance will be built on the next utterance",
                    _log_prefix(self.stream_label),
                    timeout_s,
                    audio_ms,
                )
                raise LocalQwenSherpaDecodeTimeoutError(
                    f"local speech decode did not return within {timeout_s:.0f}s "
                    f"(audio_ms={audio_ms:.0f}) — the GPU is likely saturated by "
                    "another program; the speech model will be rebuilt on the "
                    "next utterance"
                ) from None
            except Exception as exc:
                raise LocalQwenSherpaInferenceError(str(exc)) from exc

    async def _decode_in_abandonable_thread(
        self, recognizer: object, samples_f32: np.ndarray
    ) -> tuple[str, str | None]:
        """Run the native decode on a dedicated daemon thread that is safe to
        abandon.

        NOT asyncio.to_thread: that borrows a worker from the loop's shared
        executor, and a decode that never returns (the wedge is inside a native
        DirectML call, uncancellable from Python) would hold that worker
        forever — hang by hang starving every other to_thread user in the
        process. A dedicated thread is disposable: on timeout the caller just
        stops listening (the future is cancelled, so a late result is dropped
        by the done() guard) and the thread is never joined.
        """
        loop = asyncio.get_running_loop()
        future: asyncio.Future = loop.create_future()

        def _deliver(result: object = None, exc: BaseException | None = None) -> None:
            def _resolve() -> None:
                if future.done():
                    return  # timed out — this result belongs to an abandoned decode
                if exc is not None:
                    future.set_exception(exc)
                else:
                    future.set_result(result)

            # RuntimeError: the loop already closed (app shutdown) — nobody
            # is listening and there is nothing left to deliver to.
            with contextlib.suppress(RuntimeError):
                loop.call_soon_threadsafe(_resolve)

        def _worker() -> None:
            try:
                _deliver(result=self._decode_f32_sync(recognizer, samples_f32))
            except BaseException as exc:
                _deliver(exc=exc)

        threading.Thread(
            target=_worker,
            name=f"local-qwen-decode-{self.stream_label or 'self'}",
            daemon=True,
        ).start()
        return await future

    def _poison_recognizer(self, recognizer: object) -> None:
        """Retire a wedged recognizer so nothing ever decodes on it again.

        The mark rides on the instance (both channels share it), this
        backend's reference is dropped, and the shared cache entry is evicted
        — the next utterance's _ensure_recognizer builds a fresh instance.
        The old one stays pinned by the hung thread's frame until (if ever)
        the native call returns; that memory is the price of never joining.
        """
        _mark_recognizer_poisoned(recognizer)
        self._recognizer = None
        key = self._recognizer_cache_key()
        with _SHARED_RECOGNIZERS_LOCK:
            if _SHARED_RECOGNIZERS.get(key) is recognizer:
                del _SHARED_RECOGNIZERS[key]

    @staticmethod
    def _normalize_detected_language(raw: object) -> str | None:
        """sherpa reports e.g. 'zh', 'en', sometimes token-wrapped '<|en|>'.
        Normalized to a bare lowercase root; None when it says nothing."""
        text = str(raw or "").strip().strip("<|>").strip().lower()
        if not text or text in ("auto", "unk", "unknown"):
            return None
        return text.split("-")[0]

    def _decode_f32_sync(
        self, recognizer: object, samples_f32: np.ndarray
    ) -> tuple[str, str | None]:
        samples = np.asarray(samples_f32, dtype=np.float32).reshape(-1).copy()
        stream = recognizer.create_stream()
        set_option = getattr(stream, "set_option", None)
        if callable(set_option):
            if self.language_hint:
                set_option("language", self.language_hint)
            if self.hotwords:
                set_option("hotwords", ",".join(self.hotwords))
        np.clip(samples, -1.0, 1.0, out=samples)
        if self.denoise:
            try:
                from puripuly_heart.core.audio.noise_gate import spectral_denoise

                samples = spectral_denoise(samples, LOCAL_QWEN_RECOGNIZER_SAMPLE_RATE_HZ)
            except Exception:
                pass  # never let cleanup break recognition
        stream.accept_waveform(LOCAL_QWEN_RECOGNIZER_SAMPLE_RATE_HZ, samples)
        recognizer.decode_stream(stream)
        result = getattr(stream, "result", None)
        text = _strip_asr_meta_wrapper(str(getattr(result, "text", "")).strip())

        # Confidence-based garbage filter (free, model-native). The Qwen3 ASR model
        # exposes per-token log-probs in `ys_log_probs`; very low average confidence
        # is a strong signal that the model hallucinated text from noise/silence
        # (e.g. mis-hearing quiet English as garbage Chinese). We compute the mean
        # log-prob, log it so the threshold can be calibrated from real logs, and
        # drop the transcript when it falls below LOCAL_QWEN_MIN_AVG_LOGPROB.
        avg_logprob = _mean_log_prob(getattr(result, "ys_log_probs", None))
        segment_rms = float(np.sqrt(np.mean(np.square(samples), dtype=np.float64)))
        if (
            text
            and avg_logprob is not None
            and segment_rms < LOCAL_QWEN_QUIET_SEGMENT_RMS
            and avg_logprob < LOCAL_QWEN_QUIET_MIN_AVG_LOGPROB
        ):
            logger.info(
                "%s dropped quiet-segment hallucination rms=%.5f avg_logprob=%.3f text=%r",
                _audio_diag_prefix(self.stream_label),
                segment_rms,
                avg_logprob,
                text,
            )
            return "", None
        detected_lang = getattr(result, "lang", None)
        if text and (avg_logprob is not None or detected_lang):
            logger.info(
                "%s decoded lang=%r avg_logprob=%s hint=%r text=%r",
                _audio_diag_prefix(self.stream_label),
                detected_lang,
                "n/a" if avg_logprob is None else f"{avg_logprob:.3f}",
                self.language_hint,
                text[:60],
            )
        threshold = self.min_avg_logprob
        if (
            text
            and threshold is not None
            and avg_logprob is not None
            and avg_logprob < threshold
        ):
            logger.info(
                "%s dropped low-confidence transcript avg_logprob=%.3f (< %.3f) text=%r",
                _audio_diag_prefix(self.stream_label),
                avg_logprob,
                threshold,
                text[:60],
            )
            return "", self._normalize_detected_language(detected_lang)
        return text, self._normalize_detected_language(detected_lang)


@dataclass(slots=True)
class _LocalQwenSherpaSession(STTBackendSession):
    backend: LocalQwenSherpaSTTBackend
    _buffer_f32: list[np.ndarray] = field(init=False, repr=False)
    _events: asyncio.Queue[STTBackendTranscriptEvent | BaseException | None] = field(
        init=False,
        repr=False,
    )
    _closed: bool = field(init=False, default=False, repr=False)
    _closed_event_enqueued: bool = field(init=False, default=False, repr=False)
    _utterances: int = field(init=False, default=0, repr=False)
    _total_audio_ms: float = field(init=False, default=0.0, repr=False)
    _total_inference_ms: float = field(init=False, default=0.0, repr=False)
    _total_rtf: float = field(init=False, default=0.0, repr=False)
    _summary_logged: bool = field(init=False, default=False, repr=False)

    def __post_init__(self) -> None:
        self._buffer_f32 = []
        self._events = asyncio.Queue()

    async def send_audio(self, pcm16le: bytes) -> None:
        if self._closed:
            return
        await self.send_audio_f32(pcm16le_bytes_to_float32(pcm16le))

    async def send_audio_f32(self, samples_f32: np.ndarray) -> None:
        if self._closed:
            return
        samples = np.asarray(samples_f32, dtype=np.float32).reshape(-1)
        if samples.size == 0:
            return
        self._buffer_f32.append(samples.copy())

    async def on_speech_end(self, *, trailing_silence_ms: int | None = None) -> None:
        _ = trailing_silence_ms
        if self._closed or not self._buffer_f32:
            return

        samples_f32 = np.concatenate(self._buffer_f32)
        self._buffer_f32.clear()
        audio_ms = _sample_count_duration_ms(samples_f32.size, self.backend.sample_rate_hz)
        diag_enabled = self._diagnostics_enabled()
        # r313: always log decode_start — one line per utterance with rms/peak
        # is the difference between diagnosing a user's junk-transcript log in
        # one pass and guessing (two logs arrived without it this week).
        self._log_decode_start_diagnostics(samples_f32)

        try:
            started_at = time.perf_counter()
            text, detected_language = await self.backend.decode_f32(samples_f32)
            inference_ms = (time.perf_counter() - started_at) * 1000.0
        except Exception as exc:
            await self._events.put(exc)
            return

        rtf = inference_ms / audio_ms if audio_ms > 0 else 0.0
        self._utterances += 1
        self._total_audio_ms += audio_ms
        self._total_inference_ms += inference_ms
        self._total_rtf += rtf

        if diag_enabled:
            self._log_decode_done_diagnostics(
                audio_ms=audio_ms,
                inference_ms=inference_ms,
                rtf=rtf,
                text=text,
            )

        if text:
            logger.info(
                "%s Transcript: '%s' (final, audio_ms=%.1f, inference_ms=%.1f, rtf=%.3f)",
                _log_prefix(self.backend.stream_label),
                _transcript_text_for_log(text),
                audio_ms,
                inference_ms,
                rtf,
            )
            speaker_embedding: tuple[float, ...] | None = None
            speaker_seconds = 0.0
            embedder = getattr(self.backend, "speaker_embedder", None)
            if embedder is not None and not self._too_faint_to_identify(samples_f32):
                try:
                    vector = await asyncio.to_thread(embedder.embed, samples_f32)
                    if vector is not None:
                        speaker_embedding = tuple(float(x) for x in vector)
                        # r349: same rate the decode diagnostics use, rather
                        # than a second hardcoded copy of it.
                        speaker_seconds = _sample_count_duration_ms(
                            samples_f32.size, self.backend.sample_rate_hz
                        ) / 1000.0
                except Exception:
                    logger.debug("speaker embedding failed", exc_info=True)
            await self._events.put(
                STTBackendTranscriptEvent(
                    text=text,
                    is_final=True,
                    speaker_embedding=speaker_embedding,
                    speaker_seconds=speaker_seconds,
                    detected_language=detected_language,
                    audio_ms=audio_ms,
                )
            )

    async def stop(self) -> None:
        self._log_summary_once()
        await self.close()

    async def close(self) -> None:
        self._log_summary_once()
        self._closed = True
        self._buffer_f32.clear()
        if self._closed_event_enqueued:
            return
        self._closed_event_enqueued = True
        await self._events.put(None)

    async def events(self) -> AsyncIterator[STTBackendTranscriptEvent]:
        while True:
            event = await self._events.get()
            if event is None:
                break
            if isinstance(event, BaseException):
                raise event
            yield event

    def _diagnostics_enabled(self) -> bool:
        diagnostics_enabled = self.backend.diagnostics_enabled
        if diagnostics_enabled is None:
            return False
        with contextlib.suppress(Exception):
            return bool(diagnostics_enabled())
        return False

    # r360: a voiceprint is only as good as the audio under it. These bound
    # the clearly-unusable, not the merely-quiet: utterances that clustered
    # correctly measured -26 to -41 dB with 4-32% zeros, while the one that
    # merged two different people measured -53.2 dB with 42% zeros.
    _SPEAKER_ID_MIN_RMS_DB = -48.0
    _SPEAKER_ID_MAX_ZERO_RATIO = 0.40

    def _too_faint_to_identify(self, samples_f32: np.ndarray) -> bool:
        """Is this segment too quiet or too full of dropouts to be anybody?

        Returns False on any failure to measure — a missing metric must never
        silently disable speaker identification.
        """
        try:
            metrics = compute_audio_frame_metrics(
                AudioFrameF32(
                    samples=samples_f32,
                    sample_rate_hz=self.backend.sample_rate_hz,
                    channels=1,
                )
            )
        except Exception:
            return False
        faint = metrics.rms_db < self._SPEAKER_ID_MIN_RMS_DB
        gappy = metrics.zero_ratio > self._SPEAKER_ID_MAX_ZERO_RATIO
        if faint or gappy:
            logger.info(
                "%s no voiceprint: audio too %s to identify anyone "
                "(rms=%.1fdB zero_ratio=%.3f)",
                _audio_diag_prefix(self.backend.stream_label),
                "faint" if faint else "broken up",
                metrics.rms_db,
                metrics.zero_ratio,
            )
            return True
        return False

    def _log_decode_start_diagnostics(self, samples_f32: np.ndarray) -> None:
        with contextlib.suppress(Exception):
            metrics = compute_audio_frame_metrics(
                AudioFrameF32(
                    samples=samples_f32,
                    sample_rate_hz=self.backend.sample_rate_hz,
                    channels=1,
                )
            )
            logger.info(
                "%s decode_start audio_ms=%.1f rms_db=%.1f peak_db=%.1f zero_ratio=%.3f language_hint=%r",
                _audio_diag_prefix(self.backend.stream_label),
                metrics.audio_ms,
                metrics.rms_db,
                metrics.peak_db,
                metrics.zero_ratio,
                self.backend.language_hint,
            )

    def _log_decode_done_diagnostics(
        self,
        *,
        audio_ms: float,
        inference_ms: float,
        rtf: float,
        text: str,
    ) -> None:
        with contextlib.suppress(Exception):
            logger.info(
                "%s decode_done audio_ms=%.1f inference_ms=%.1f rtf=%.3f text_len=%s empty_result=%s suspicious_repetition=%s suspicious_script=%s",
                _audio_diag_prefix(self.backend.stream_label),
                audio_ms,
                inference_ms,
                rtf,
                len(text),
                not bool(text),
                _looks_repetitive(text),
                _looks_script_mismatched(text, self.backend.language_hint),
            )

    def _log_summary_once(self) -> None:
        if self._summary_logged or self._utterances == 0:
            return
        self._summary_logged = True
        weighted_total_rtf = (
            self._total_inference_ms / self._total_audio_ms if self._total_audio_ms > 0 else 0.0
        )
        mean_rtf = self._total_rtf / self._utterances if self._utterances > 0 else 0.0
        logger.info(
            "%s Session summary: utterances=%s total_audio_ms=%.1f total_inference_ms=%.1f weighted_total_rtf=%.3f mean_rtf=%.3f",
            _log_prefix(self.backend.stream_label),
            self._utterances,
            self._total_audio_ms,
            self._total_inference_ms,
            weighted_total_rtf,
            mean_rtf,
        )
