"""r352: an unidentified peer line must still be nameable.

r349 made the recogniser decline to invent a speaker for short audio, which is
only a good trade if the user can correct it afterwards. Before this, such a
line rendered as a plain "Received" header with nothing to click, so the one
person who actually knew who spoke had no way to tell the app.

The voiceprint has to survive the whole way from the hub to the chip for that
to work, so these follow it along the chain rather than testing the end alone.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from puripuly_heart.core.speaker_id import EMBEDDING_DIM, SpeakerRegistry

I18N = Path("src/puripuly_heart/data/i18n")


def _voice(seed: int) -> np.ndarray:
    rng = np.random.default_rng(seed)
    vector = rng.normal(size=EMBEDDING_DIM).astype(np.float32)
    return vector / float(np.linalg.norm(vector))


def test_the_hub_hands_the_voiceprint_to_the_ui() -> None:
    """It was computed, used for matching, then dropped when the outgoing
    transcript was built — which is why a correction had nothing finer than
    the cluster to attach to."""
    from puripuly_heart.domain.models import Transcript

    assert "speaker_embedding" in Transcript.__dataclass_fields__
    assert "speaker_seconds" in Transcript.__dataclass_fields__

    source = Path("src/puripuly_heart/core/orchestrator/hub.py").read_text(
        encoding="utf-8"
    )
    assert "speaker_embedding=transcript.speaker_embedding" in source, (
        "the hub drops the voiceprint again"
    )


def test_the_event_bridge_carries_it_through() -> None:
    import inspect

    from puripuly_heart.ui.event_bridge import UIEventBridge

    signature = inspect.signature(UIEventBridge._append_chat_entry)
    assert "speaker_embedding" in signature.parameters


def test_the_chat_entry_accepts_it() -> None:
    import inspect

    from puripuly_heart.ui.views.dashboard import DashboardView

    signature = inspect.signature(DashboardView.append_chat_entry)
    assert "speaker_embedding" in signature.parameters


def test_the_naming_dialog_accepts_a_voiceprint() -> None:
    import inspect

    from puripuly_heart.ui.views.dashboard import DashboardView

    signature = inspect.signature(DashboardView._open_speaker_name_dialog)
    assert "embedding" in signature.parameters


def test_a_line_with_only_a_voiceprint_earns_a_chip() -> None:
    """The render condition itself: no name, no cluster, but a voiceprint."""
    source = Path("src/puripuly_heart/ui/views/dashboard.py").read_text(
        encoding="utf-8"
    )
    assert "speaker_embedding is not None" in source, (
        "the chip still requires a name or a cluster"
    )


def test_naming_a_bare_voiceprint_enrolls_it(tmp_path) -> None:
    registry = SpeakerRegistry(tmp_path / "voices.json")
    voice = _voice(7)

    assert registry.enroll_embedding(voice, "Robin")

    assert registry.has_name("Robin")
    assert registry.match(voice).label == "Robin"


def test_the_unidentified_label_is_translated_everywhere() -> None:
    """A tag the user is meant to click must not be the one English word left
    on a Chinese screen."""
    for code in ("en", "zh-CN", "ja", "ko"):
        data = json.loads((I18N / f"{code}.json").read_text(encoding="utf-8"))
        assert data.get("dashboard.speaker_unknown"), f"{code} is missing it"

    english = json.loads((I18N / "en.json").read_text(encoding="utf-8"))
    for code in ("zh-CN", "ja", "ko"):
        data = json.loads((I18N / f"{code}.json").read_text(encoding="utf-8"))
        assert (
            data["dashboard.speaker_unknown"]
            != english["dashboard.speaker_unknown"]
        ), f"{code} still shows the English string"
