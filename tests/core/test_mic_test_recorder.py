from __future__ import annotations

import numpy as np
import pytest

from puripuly_heart.core.audio import mic_test_recorder
from puripuly_heart.core.audio.format import AudioFrameF32
from puripuly_heart.core.audio.mic_test_recorder import MicTestRecorder


def _frame(n: int, rate: int = 1000, channels: int = 1, value: float = 0.5) -> AudioFrameF32:
    shape = (n, channels) if channels > 1 else (n,)
    return AudioFrameF32(samples=np.full(shape, value, np.float32), sample_rate_hz=rate, channels=channels)


def test_records_exactly_the_requested_length_then_stops() -> None:
    rec = MicTestRecorder()
    rec.start(seconds=1.0)

    assert rec.add_frame(_frame(600)) is False
    assert rec.recording
    assert rec.add_frame(_frame(600)) is True  # 1200 > 1000: trimmed, done
    assert not rec.recording
    assert rec.add_frame(_frame(600)) is False  # ignored after the clip is full

    data, rate = rec.clip()
    assert rate == 1000 and len(data) == 1000
    assert rec.clip_seconds == pytest.approx(1.0)


def test_stereo_is_mixed_down_to_mono() -> None:
    rec = MicTestRecorder()
    rec.start(seconds=0.5)
    rec.add_frame(_frame(500, channels=2, value=0.25))

    data, _ = rec.clip()
    assert data.ndim == 1
    assert float(data[0]) == pytest.approx(0.25)


def test_no_clip_until_recording_finishes() -> None:
    rec = MicTestRecorder()
    assert rec.clip() is None
    rec.start(seconds=1.0)
    rec.add_frame(_frame(100))
    assert not rec.has_clip


@pytest.mark.asyncio
async def test_play_hands_the_clip_to_the_sound_device(monkeypatch) -> None:
    played = []

    class FakeSD:
        @staticmethod
        def play(data, rate):
            played.append((len(data), rate))

        @staticmethod
        def wait():
            return None

    monkeypatch.setitem(__import__("sys").modules, "sounddevice", FakeSD)
    rec = MicTestRecorder()
    rec.start(seconds=0.5)
    rec.add_frame(_frame(500))

    assert await rec.play() is True
    assert played == [(500, 1000)]
    assert mic_test_recorder.DEFAULT_RECORD_SECONDS == 5.0


def test_stop_ends_the_recording_early_and_keeps_the_audio() -> None:
    rec = MicTestRecorder()
    rec.start(seconds=5.0)
    rec.add_frame(_frame(1500))

    assert rec.stop() is True
    assert not rec.recording
    assert rec.clip_seconds == pytest.approx(1.5)
    assert rec.add_frame(_frame(500)) is False  # nothing more after Stop


def test_stop_before_any_audio_leaves_no_clip() -> None:
    rec = MicTestRecorder()
    rec.start(seconds=5.0)

    assert rec.stop() is False
    assert rec.clip() is None


def test_controller_stop_reports_the_captured_length() -> None:
    from types import SimpleNamespace

    from puripuly_heart.ui.controller import GuiController

    seen: list[float] = []
    rec = MicTestRecorder()
    rec.start(seconds=5.0)
    rec.add_frame(_frame(2000))
    fake = SimpleNamespace(_microphone_test_recorder=rec, _microphone_test_record_done=seen.append)

    assert GuiController.stop_microphone_test_recording(fake) is True
    assert seen == [pytest.approx(2.0)]
    assert fake._microphone_test_record_done is None
    assert GuiController.stop_microphone_test_recording(fake) is False  # already stopped


def test_dialog_swaps_record_for_stop_while_busy() -> None:
    from puripuly_heart.ui.components.microphone_test_dialog import MicrophoneTestDialog

    class _Page:
        def open(self, dialog):
            self.dialog = dialog

    dialog = MicrophoneTestDialog(_Page(), on_close=lambda: None, on_record=lambda: None,
                                  on_play=lambda: None, on_stop=lambda: None)
    dialog.open()
    dialog.set_recording()
    assert dialog._stop_btn.visible is True and dialog._record_btn.visible is False

    dialog.set_recorded(0.0)  # stopped before any audio arrived
    assert dialog._stop_btn.visible is False and dialog._record_btn.visible is True
    assert dialog._play_btn.disabled is True

    dialog.set_recording()
    dialog.set_recorded(2.0)
    assert dialog._play_btn.disabled is False
