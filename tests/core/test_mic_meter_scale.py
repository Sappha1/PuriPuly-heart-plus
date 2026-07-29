"""Mic-test meter shows a dB scale, not raw amplitude (r314).

The old meter displayed min(1.0, peak) as percent, putting 100% at hard
digital clipping — a user with a hot mic (wind complaints from friends)
read "6%", and every healthy mic looked broken."""
from __future__ import annotations

import numpy as np

from puripuly_heart.ui.controller import GuiController


class _Frame:
    def __init__(self, samples):
        self.samples = samples


def _pct(peak: float) -> int:
    frame = _Frame(np.array([peak], dtype=np.float32))
    return round(GuiController._microphone_test_meter_level_from_frame(frame) * 100)


def test_normal_speech_reads_mid_range() -> None:
    assert 45 <= _pct(0.06) <= 60      # -24 dBFS peaks — the reported "6%" mic


def test_loud_speech_reads_high() -> None:
    assert 75 <= _pct(0.3) <= 92


def test_clipping_reads_full() -> None:
    assert _pct(1.0) == 100


def test_very_quiet_reads_low_but_alive() -> None:
    assert 5 <= _pct(0.006) <= 20      # -44 dBFS — the Anhui-level signal


def test_silence_reads_zero() -> None:
    assert _pct(0.0) == 0
    frame = _Frame(np.zeros(0, dtype=np.float32))
    assert GuiController._microphone_test_meter_level_from_frame(frame) == 0.0
