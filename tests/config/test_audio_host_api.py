from __future__ import annotations

from puripuly_heart.config.audio_host_api import (
    WINDOWS_DIRECTSOUND_HOST_API,
    WINDOWS_WASAPI_COMPATIBILITY_HOST_API,
    WINDOWS_WASAPI_HOST_API,
    normalize_input_host_api,
)


def test_normal_wasapi_profile_uses_plain_wasapi() -> None:
    profile = normalize_input_host_api(WINDOWS_WASAPI_HOST_API)

    assert profile.saved_value == WINDOWS_WASAPI_HOST_API
    assert profile.actual_host_api == WINDOWS_WASAPI_HOST_API
    assert profile.wasapi_auto_convert is False
    assert profile.wasapi_exclusive is False


def test_wasapi_compatibility_profile_maps_to_wasapi_with_auto_convert() -> None:
    profile = normalize_input_host_api(WINDOWS_WASAPI_COMPATIBILITY_HOST_API)

    assert profile.saved_value == WINDOWS_WASAPI_COMPATIBILITY_HOST_API
    assert profile.actual_host_api == WINDOWS_WASAPI_HOST_API
    assert profile.wasapi_auto_convert is True
    assert profile.wasapi_exclusive is False


def test_directsound_profile_remains_directsound_without_wasapi_flags() -> None:
    profile = normalize_input_host_api(WINDOWS_DIRECTSOUND_HOST_API)

    assert profile.saved_value == WINDOWS_DIRECTSOUND_HOST_API
    assert profile.actual_host_api == WINDOWS_DIRECTSOUND_HOST_API
    assert profile.wasapi_auto_convert is False
    assert profile.wasapi_exclusive is False


def test_blank_profile_remains_blank_default() -> None:
    profile = normalize_input_host_api("  ")

    assert profile.saved_value == ""
    assert profile.actual_host_api == ""
    assert profile.wasapi_auto_convert is False
    assert profile.wasapi_exclusive is False


def test_unknown_profile_preserves_canonical_raw_value() -> None:
    profile = normalize_input_host_api("Custom Host API")

    assert profile.saved_value == "Custom Host API"
    assert profile.actual_host_api == "Custom Host API"
    assert profile.wasapi_auto_convert is False
    assert profile.wasapi_exclusive is False
