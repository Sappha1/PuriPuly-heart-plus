from __future__ import annotations

from dataclasses import dataclass

WINDOWS_WASAPI_HOST_API = "Windows WASAPI"
WINDOWS_WASAPI_COMPATIBILITY_HOST_API = "Windows WASAPI (Compatibility Mode)"
WINDOWS_DIRECTSOUND_HOST_API = "Windows DirectSound"
WINDOWS_MME_HOST_API = "MME"


@dataclass(frozen=True, slots=True)
class InputHostApiProfile:
    saved_value: str
    actual_host_api: str
    wasapi_auto_convert: bool = False
    wasapi_exclusive: bool = False


def normalize_input_host_api(value: str | None) -> InputHostApiProfile:
    saved_value = str(value or "").strip()
    if saved_value == WINDOWS_WASAPI_COMPATIBILITY_HOST_API:
        return InputHostApiProfile(
            saved_value=saved_value,
            actual_host_api=WINDOWS_WASAPI_HOST_API,
            wasapi_auto_convert=True,
            wasapi_exclusive=False,
        )
    return InputHostApiProfile(
        saved_value=saved_value,
        actual_host_api=saved_value,
        wasapi_auto_convert=False,
        wasapi_exclusive=False,
    )
