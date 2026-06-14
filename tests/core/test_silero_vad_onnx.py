from __future__ import annotations

import sys
from types import ModuleType
from typing import Any

import numpy as np
import pytest

from puripuly_heart.core.vad.silero import SileroVadOnnx


class _NodeArg:
    def __init__(self, name: str, *, type: str, shape: list[int | None]):
        self.name = name
        self.type = type
        self.shape = shape


class _FakeSessionOptions:
    def __init__(self):
        self.intra_op_num_threads = 0
        self.inter_op_num_threads = 0
        self.graph_optimization_level = None


class _FakeGraphOptimizationLevel:
    ORT_ENABLE_ALL = 99


def _copy_feed(feed: dict[str, object]) -> dict[str, object]:
    copied: dict[str, object] = {}
    for key, value in feed.items():
        if isinstance(value, np.ndarray):
            copied[key] = np.array(value, copy=True)
        else:
            copied[key] = value
    return copied


class _FakeLegacySession:
    def __init__(self, _path: str, _sess_options=None, *, providers: list[str]):
        self.providers = providers
        self.calls: list[dict[str, object]] = []

        self._inputs = [
            _NodeArg("input", type="tensor(float)", shape=[1, None]),
            _NodeArg("sr", type="tensor(int64)", shape=[1]),
            _NodeArg("h", type="tensor(float)", shape=[2, 1, 64]),
            _NodeArg("c", type="tensor(float)", shape=[2, 1, 64]),
        ]
        self._outputs = [
            _NodeArg("output", type="tensor(float)", shape=[1, 1]),
            _NodeArg("hn", type="tensor(float)", shape=[2, 1, 64]),
            _NodeArg("cn", type="tensor(float)", shape=[2, 1, 64]),
        ]

    def get_inputs(self):
        return list(self._inputs)

    def get_outputs(self):
        return list(self._outputs)

    def run(self, _output_names, feed: dict[str, object]):
        self.calls.append(_copy_feed(feed))

        h = np.asarray(feed["h"], dtype=np.float32)
        c = np.asarray(feed["c"], dtype=np.float32)
        call_n = len(self.calls)

        if call_n == 1:
            assert np.all(h == 0.0)
            assert np.all(c == 0.0)
            prob = 0.7
        elif call_n == 2:
            assert np.all(h == 1.0)
            assert np.all(c == 1.0)
            prob = 0.2
        else:
            assert np.all(h == 0.0)
            assert np.all(c == 0.0)
            prob = 0.5

        hn = h + 1.0
        cn = c + 1.0
        return [
            np.asarray([[prob]], dtype=np.float32),
            hn,
            cn,
        ]


class _FakeStateSession:
    def __init__(self, _path: str, _sess_options=None, *, providers: list[str]):
        self.providers = providers
        self.calls: list[dict[str, object]] = []

        self._inputs = [
            _NodeArg("input", type="tensor(float)", shape=[None, None]),
            _NodeArg("state", type="tensor(float)", shape=[2, None, 128]),
            _NodeArg("sr", type="tensor(int64)", shape=[]),
        ]
        self._outputs = [
            _NodeArg("output", type="tensor(float)", shape=[None, 1]),
            _NodeArg("stateN", type="tensor(float)", shape=[None, None, None]),
        ]

    def get_inputs(self):
        return list(self._inputs)

    def get_outputs(self):
        return list(self._outputs)

    def run(self, _output_names, feed: dict[str, object]):
        self.calls.append(_copy_feed(feed))

        state = np.asarray(feed["state"], dtype=np.float32)
        call_n = len(self.calls)

        if call_n == 1:
            assert np.all(state == 0.0)
            prob = 0.6
        elif call_n == 2:
            assert np.all(state == 1.0)
            prob = 0.3
        else:
            assert np.all(state == 0.0)
            prob = 0.8

        return [
            np.asarray([[prob]], dtype=np.float32),
            state + 1.0,
        ]


class _RecordingStateSession(_FakeStateSession):
    def run(self, _output_names, feed: dict[str, object]):
        self.calls.append(_copy_feed(feed))
        state = np.asarray(feed["state"], dtype=np.float32)
        return [
            np.asarray([[0.4]], dtype=np.float32),
            state + 1.0,
        ]


def _install_fake_ort(monkeypatch: pytest.MonkeyPatch, session_type: type[Any]) -> None:
    fake_ort = ModuleType("onnxruntime")
    fake_ort.InferenceSession = session_type
    fake_ort.SessionOptions = _FakeSessionOptions
    fake_ort.GraphOptimizationLevel = _FakeGraphOptimizationLevel
    monkeypatch.setitem(sys.modules, "onnxruntime", fake_ort)


def test_silero_vad_onnx_legacy_state_tracks_context_and_reset(tmp_path, monkeypatch):
    _install_fake_ort(monkeypatch, _FakeLegacySession)

    model_path = tmp_path / "silero.onnx"
    model_path.write_bytes(b"")

    vad = SileroVadOnnx(model_path=model_path)

    chunk1 = np.arange(512, dtype=np.float32)
    chunk2 = np.arange(512, dtype=np.float32) + 1000.0

    p1 = vad.speech_probability(chunk1, sample_rate_hz=16000)
    p2 = vad.speech_probability(chunk2, sample_rate_hz=16000)
    assert p1 == pytest.approx(0.7)
    assert p2 == pytest.approx(0.2)

    calls = vad._session.calls
    assert len(calls) == 2
    np.testing.assert_array_equal(
        calls[0]["input"],
        np.concatenate([np.zeros((64,), dtype=np.float32), chunk1], dtype=np.float32).reshape(
            1, -1
        ),
    )
    np.testing.assert_array_equal(
        calls[1]["input"],
        np.concatenate([chunk1[-64:], chunk2], dtype=np.float32).reshape(1, -1),
    )

    vad.reset()
    p3 = vad.speech_probability(chunk1, sample_rate_hz=16000)
    assert p3 == pytest.approx(0.5)

    np.testing.assert_array_equal(
        calls[2]["input"],
        np.concatenate([np.zeros((64,), dtype=np.float32), chunk1], dtype=np.float32).reshape(
            1, -1
        ),
    )


def test_silero_vad_onnx_state_model_tracks_context_and_reset(tmp_path, monkeypatch):
    _install_fake_ort(monkeypatch, _FakeStateSession)

    model_path = tmp_path / "silero.onnx"
    model_path.write_bytes(b"")

    vad = SileroVadOnnx(model_path=model_path)

    chunk1 = np.arange(512, dtype=np.float32)
    chunk2 = np.arange(512, dtype=np.float32) + 1000.0

    p1 = vad.speech_probability(chunk1, sample_rate_hz=16000)
    p2 = vad.speech_probability(chunk2, sample_rate_hz=16000)
    assert p1 == pytest.approx(0.6)
    assert p2 == pytest.approx(0.3)

    calls = vad._session.calls
    assert len(calls) == 2
    assert np.asarray(calls[0]["sr"]).shape == ()
    np.testing.assert_array_equal(
        calls[0]["input"],
        np.concatenate([np.zeros((64,), dtype=np.float32), chunk1], dtype=np.float32).reshape(
            1, -1
        ),
    )
    np.testing.assert_array_equal(
        calls[1]["input"],
        np.concatenate([chunk1[-64:], chunk2], dtype=np.float32).reshape(1, -1),
    )

    vad.reset()
    p3 = vad.speech_probability(chunk1, sample_rate_hz=16000)
    assert p3 == pytest.approx(0.8)
    np.testing.assert_array_equal(
        calls[2]["input"],
        np.concatenate([np.zeros((64,), dtype=np.float32), chunk1], dtype=np.float32).reshape(
            1, -1
        ),
    )


def test_silero_vad_onnx_state_model_validates_chunk_size_and_resets_on_sample_rate_change(
    tmp_path, monkeypatch
):
    _install_fake_ort(monkeypatch, _RecordingStateSession)

    model_path = tmp_path / "silero.onnx"
    model_path.write_bytes(b"")

    vad = SileroVadOnnx(model_path=model_path)

    with pytest.raises(ValueError, match="512"):
        vad.speech_probability(np.zeros((511,), dtype=np.float32), sample_rate_hz=16000)

    vad.speech_probability(np.arange(512, dtype=np.float32), sample_rate_hz=16000)
    vad.speech_probability(np.arange(256, dtype=np.float32), sample_rate_hz=8000)

    calls = vad._session.calls
    assert len(calls) == 2
    np.testing.assert_array_equal(
        calls[1]["input"],
        np.concatenate(
            [np.zeros((32,), dtype=np.float32), np.arange(256, dtype=np.float32)],
            dtype=np.float32,
        ).reshape(1, -1),
    )
