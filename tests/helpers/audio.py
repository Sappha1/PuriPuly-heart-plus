from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from puripuly_heart.core.audio.format import AudioFrameF32


@dataclass(slots=True)
class FakeAudioSource:
    frames_list: list[AudioFrameF32]

    async def frames(self):
        for item in self.frames_list:
            yield item

    async def close(self) -> None:
        return


def make_frames(
    samples: np.ndarray, *, sample_rate_hz: int, splits: list[int]
) -> list[AudioFrameF32]:
    frames: list[AudioFrameF32] = []
    offset = 0
    for n in splits:
        frames.append(
            AudioFrameF32(
                samples=samples[offset : offset + n],
                sample_rate_hz=sample_rate_hz,
            )
        )
        offset += n
    return frames
