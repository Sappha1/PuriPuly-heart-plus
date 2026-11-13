"""A missing VR headset is reported as hmd_not_found, not the generic OpenVR failure.

The overlay's own pre-check already says hmd_not_found, but when OpenVR's init is
what finds no headset (desktop VRChat with SteamVR installed) the overlay reports
openvr_init_failed right after an error line naming VRInitError_Init_HmdNotFound.
That reason's message gave desktop players no hint to switch Overlay mode.
"""
from __future__ import annotations

import asyncio
import json

import pytest

from puripuly_heart.core.overlay import process as op


def _reason_after(lines: list[str]) -> str:
    async def main() -> str:
        proc = object.__new__(op._AsyncioOverlayProcess)
        proc._events = asyncio.Queue()
        proc._reader_tasks = []
        proc._diagnostics = None
        proc._logging_mode = "basic"
        proc._hmd_not_found_seen = False
        reader = asyncio.StreamReader()
        reader.feed_data("".join(line + "\n" for line in lines).encode("utf-8"))
        reader.feed_eof()
        await proc._read_stream(reader, "stderr")
        return (await proc.next_event())["failure_reason"]

    return asyncio.run(main())


def _event(reason: str) -> str:
    return "EVENT " + json.dumps({"type": "startup_error", "failure_reason": reason})


def test_openvr_init_finding_no_headset_reports_hmd_not_found():
    assert _reason_after([
        "[overlay][ERROR] openvr init failed: VR_InitInternal failed: "
        "VRInitError_Init_HmdNotFound",
        _event("openvr_init_failed"),
    ]) == "hmd_not_found"


@pytest.mark.parametrize("line", [
    "[overlay][ERROR] openvr init failed: VR_InitInternal failed: "
    "VRInitError_Init_PathRegistryNotFound",
    "[overlay][INFO] manifest_loaded",
])
def test_other_openvr_failures_keep_their_reason(line):
    assert _reason_after([line, _event("openvr_init_failed")]) == "openvr_init_failed"


def test_the_message_points_desktop_players_at_overlay_mode():
    data = json.loads(
        (op.Path(op.__file__).resolve().parents[2] / "data" / "i18n" / "en.json")
        .read_text(encoding="utf-8"))
    msg = data["settings.overlay.failure.hmd_not_found"]
    assert data["settings.overlay.caption_location"] in msg
    assert data["settings.overlay.target.desktop"] in msg
