from __future__ import annotations

import numpy as np
import pytest

from puripuly_heart.core.audio.ring_buffer import RingBufferF32


def test_ring_buffer_rejects_non_positive_capacity() -> None:
    with pytest.raises(ValueError, match="capacity_samples"):
        RingBufferF32(capacity_samples=0)


def test_ring_buffer_wraps_and_returns_last_samples() -> None:
    buf = RingBufferF32(capacity_samples=5)
    buf.append(np.array([1, 2, 3], dtype=np.float32))
    buf.append(np.array([4, 5, 6], dtype=np.float32))

    out = buf.get_last_samples(5)
    np.testing.assert_allclose(out, np.array([2, 3, 4, 5, 6], dtype=np.float32))


def test_ring_buffer_overwrite_when_input_exceeds_capacity() -> None:
    buf = RingBufferF32(capacity_samples=4)
    buf.append(np.array([1, 2, 3, 4, 5, 6], dtype=np.float32))

    out = buf.get_last_samples(4)
    np.testing.assert_allclose(out, np.array([3, 4, 5, 6], dtype=np.float32))


def test_ring_buffer_get_last_samples_handles_zero_and_clear() -> None:
    buf = RingBufferF32(capacity_samples=3)
    buf.append(np.array([1, 2], dtype=np.float32))

    out = buf.get_last_samples(0)
    assert out.size == 0

    buf.clear()
    out = buf.get_last_samples(2)
    np.testing.assert_allclose(out, np.array([], dtype=np.float32))
