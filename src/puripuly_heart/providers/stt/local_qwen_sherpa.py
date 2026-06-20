from __future__ import annotations

import asyncio
import contextlib
import importlib
import logging
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
    diagnostics_enabled: Callable[[], bool] | None = None
    on_model_loading: object = None  # Callable[[], None] — fired just before blocking model init
    on_model_loaded: object = None   # Callable[[], None] — fired after model init completes
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

    async def close(self) -> None:
        self._recognizer = None

    @property
    def _crash_sentinel_path(self) -> Path:
        label = self.stream_label or "self"
        return self.model_dir.parent / f".stt_load_sentinel_{label}"

    async def _ensure_recognizer(self) -> object:
        if self._recognizer is not None:
            return self._recognizer

        async with self._load_lock:
            if self._recognizer is not None:
                return self._recognizer

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
                await asyncio.to_thread(validate_local_stt_runtime_ready, self.model_dir)

                if callable(self.on_model_loading):
                    try:
                        self.on_model_loading()
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
                        self.on_model_loaded()
                    except Exception:
                        pass
            return self._recognizer

    def _create_recognizer(self) -> object:
        try:
            return create_local_qwen_sherpa_recognizer(
                model_dir=self.model_dir,
                num_threads=self.num_threads,
                sample_rate_hz=LOCAL_QWEN_RECOGNIZER_SAMPLE_RATE_HZ,
                feature_dim=self.feature_dim,
                provider=self.provider,
            )
        except LocalQwenRuntimeBootstrapError as exc:
            raise LocalQwenSherpaLoadError(str(exc)) from exc
        except _LocalQwenSherpaImportError as exc:
            raise LocalQwenSherpaLoadError("failed to import sherpa_onnx") from exc.__cause__
        except Exception as exc:
            raise LocalQwenSherpaLoadError(str(exc)) from exc

    async def decode_pcm16le(self, pcm16le: bytes) -> str:
        return await self.decode_f32(pcm16le_bytes_to_float32(pcm16le))

    async def decode_f32(self, samples_f32: np.ndarray) -> str:
        recognizer = await self._ensure_recognizer()
        async with self._decode_lock:
            try:
                return await asyncio.to_thread(
                    self._decode_f32_sync,
                    recognizer,
                    samples_f32,
                )
            except Exception as exc:
                raise LocalQwenSherpaInferenceError(str(exc)) from exc

    def _decode_f32_sync(self, recognizer: object, samples_f32: np.ndarray) -> str:
        samples = np.asarray(samples_f32, dtype=np.float32).reshape(-1).copy()
        stream = recognizer.create_stream()
        set_option = getattr(stream, "set_option", None)
        if callable(set_option):
            if self.language_hint:
                set_option("language", self.language_hint)
            if self.hotwords:
                set_option("hotwords", ",".join(self.hotwords))
        np.clip(samples, -1.0, 1.0, out=samples)
        stream.accept_waveform(LOCAL_QWEN_RECOGNIZER_SAMPLE_RATE_HZ, samples)
        recognizer.decode_stream(stream)
        result = getattr(stream, "result", None)
        text = str(getattr(result, "text", "")).strip()

        # Confidence-based garbage filter (free, model-native). The Qwen3 ASR model
        # exposes per-token log-probs in `ys_log_probs`; very low average confidence
        # is a strong signal that the model hallucinated text from noise/silence
        # (e.g. mis-hearing quiet English as garbage Chinese). We compute the mean
        # log-prob, log it so the threshold can be calibrated from real logs, and
        # drop the transcript when it falls below LOCAL_QWEN_MIN_AVG_LOGPROB.
        avg_logprob = _mean_log_prob(getattr(result, "ys_log_probs", None))
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
            return ""
        return text


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
        if diag_enabled:
            self._log_decode_start_diagnostics(samples_f32)

        try:
            started_at = time.perf_counter()
            text = await self.backend.decode_f32(samples_f32)
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
            await self._events.put(STTBackendTranscriptEvent(text=text, is_final=True))

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
