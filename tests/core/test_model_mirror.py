"""r354: a user who cannot reach the model host still needs a recogniser.

Whisper's weights come through huggingface_hub, which is unreachable from
mainland China. That left one user with only the local Qwen recogniser -- and
on a CPU without VNNI its int8 arithmetic is unreliable, so the recogniser
their hardware can run and the recogniser they can download were disjoint.
"""
from __future__ import annotations

import os

import pytest

from puripuly_heart.core.model_mirror import (
    DEFAULT_MIRROR,
    configure_model_downloads,
)


@pytest.fixture(autouse=True)
def _clean_env():
    previous = os.environ.pop("HF_ENDPOINT", None)
    yield
    os.environ.pop("HF_ENDPOINT", None)
    if previous is not None:
        os.environ["HF_ENDPOINT"] = previous


def test_mirror_is_applied_when_asked() -> None:
    assert configure_model_downloads("mirror") == DEFAULT_MIRROR
    assert os.environ["HF_ENDPOINT"] == DEFAULT_MIRROR


def test_direct_leaves_the_environment_alone() -> None:
    assert configure_model_downloads("direct") == ""
    assert "HF_ENDPOINT" not in os.environ


def test_an_endpoint_the_user_set_is_never_overridden() -> None:
    """Someone who configured a host meant it -- including a corporate proxy
    or their own mirror."""
    os.environ["HF_ENDPOINT"] = "https://chosen.example"

    assert configure_model_downloads("mirror") == "https://chosen.example"
    assert os.environ["HF_ENDPOINT"] == "https://chosen.example"


def test_auto_follows_the_locale(monkeypatch) -> None:
    import puripuly_heart.core.model_mirror as mirror

    monkeypatch.setattr(mirror, "_looks_like_prc_locale", lambda: True)
    assert configure_model_downloads("auto") == DEFAULT_MIRROR

    os.environ.pop("HF_ENDPOINT", None)
    monkeypatch.setattr(mirror, "_looks_like_prc_locale", lambda: False)
    assert configure_model_downloads("auto") == ""


def test_a_broken_locale_probe_does_not_stop_startup(monkeypatch) -> None:
    """This runs before the app loads; it must never be the reason it doesn't."""
    import puripuly_heart.core.model_mirror as mirror

    def _boom() -> bool:
        raise RuntimeError("no locale")

    monkeypatch.setattr(mirror, "_looks_like_prc_locale", _boom)

    with pytest.raises(RuntimeError):
        configure_model_downloads("auto")

    # main() wraps the call, so verify the guard is really there rather than
    # trusting that it is.
    source = __import__("pathlib").Path(
        "src/puripuly_heart/main.py"
    ).read_text(encoding="utf-8")
    assert "configure_model_downloads(" in source
    assert "mirror setup skipped" in source, "startup is not guarded"
