from __future__ import annotations

import pytest

from puripuly_heart.core.osc.encoding import encode_message, encode_string


def test_encode_string_padding() -> None:
    encoded = encode_string("hi")
    assert encoded.endswith(b"\0")
    assert len(encoded) % 4 == 0

    encoded = encode_string("hey")
    assert encoded.endswith(b"\0")
    assert len(encoded) % 4 == 0


def test_encode_message_encodes_types() -> None:
    data = encode_message("/test", [True, False, 1, 1.5, "hi"])

    header = encode_string("/test") + encode_string(",TFifs")
    assert data.startswith(header)
    assert len(data) % 4 == 0


def test_encode_message_rejects_invalid_address() -> None:
    with pytest.raises(ValueError, match="OSC address"):
        encode_message("test", [])


def test_encode_message_rejects_invalid_arg_type() -> None:
    with pytest.raises(TypeError, match="Unsupported OSC arg type"):
        encode_message("/test", [object()])
