"""r389: Alibaba workspace keys (sk-ws-…) and their private endpoints.

The Singapore Model Studio console now issues workspace keys with a
per-workspace endpoint (https://{WorkspaceId}.{region}.maas.aliyuncs.com).
Those keys 401 on the shared regional endpoint the app used for everything —
which reads as "my key is broken" when the key is fine. A real user hit
exactly this within a day of buying access.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from puripuly_heart.config.settings import (
    QwenRegion,
    QwenSettings,
    normalize_qwen_workspace_endpoint,
)

_ROOT = Path(__file__).resolve().parents[2]
WORKSPACE = "https://ws-example0000.ap-southeast-1.maas.aliyuncs.com"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        # exactly what the console displays
        (WORKSPACE + "/compatible-mode/v1", WORKSPACE),
        # docs sometimes show the bare origin; users add slashes and spaces
        (WORKSPACE, WORKSPACE),
        (WORKSPACE + "/", WORKSPACE),
        ("  " + WORKSPACE + "/api/v1  ", WORKSPACE),
        ("", ""),
        ("   ", ""),
    ],
)
def test_endpoint_normalizes_to_the_origin(raw: str, expected: str) -> None:
    assert normalize_qwen_workspace_endpoint(raw) == expected


def test_workspace_endpoint_overrides_the_regional_url() -> None:
    settings = QwenSettings(
        region=QwenRegion.SINGAPORE,
        workspace_endpoint=WORKSPACE + "/compatible-mode/v1",
    )
    settings.validate()
    base = settings.get_llm_base_url()
    assert base == WORKSPACE + "/api/v1"
    # every consumer converts the /api/v1 suffix — the round trip must land on
    # the exact URL the console displays
    assert base.replace("/api/v1", "/compatible-mode/v1") == (
        WORKSPACE + "/compatible-mode/v1"
    )


def test_empty_endpoint_keeps_the_regional_urls() -> None:
    assert (
        QwenSettings(region=QwenRegion.SINGAPORE).get_llm_base_url()
        == "https://dashscope-intl.aliyuncs.com/api/v1"
    )
    assert (
        QwenSettings(region=QwenRegion.BEIJING).get_llm_base_url()
        == "https://dashscope.aliyuncs.com/api/v1"
    )


def test_the_asr_websocket_is_not_redirected() -> None:
    """The workspace scheme is documented for the HTTP APIs only; guessing a
    wss:// shape would fail in a worse place than a clear regional 401."""
    settings = QwenSettings(
        region=QwenRegion.SINGAPORE, workspace_endpoint=WORKSPACE
    )
    assert settings.get_asr_endpoint() == (
        "wss://dashscope-intl.aliyuncs.com/api-ws/v1/realtime"
    )


def test_non_https_endpoint_is_rejected() -> None:
    settings = QwenSettings(workspace_endpoint="http://ws-x.maas.aliyuncs.com")
    with pytest.raises(ValueError):
        settings.validate()


def test_endpoint_survives_a_settings_round_trip() -> None:
    import json

    from puripuly_heart.config.settings import AppSettings, from_dict, to_dict

    settings = AppSettings()
    settings.qwen.workspace_endpoint = WORKSPACE + "/compatible-mode/v1"
    data = json.loads(json.dumps(to_dict(settings)))
    restored = from_dict(data)
    assert restored.qwen.workspace_endpoint == WORKSPACE + "/compatible-mode/v1"


def test_verification_uses_the_workspace_endpoint() -> None:
    """The settings verify buttons must probe where the key actually works."""
    text = (_ROOT / "src/puripuly_heart/ui/controller.py").read_text(encoding="utf-8")
    for branch in ('provider == "alibaba_beijing"', 'provider == "alibaba_singapore"'):
        start = text.index(branch)
        block = text[start : start + 400]
        assert "_qwen_verify_base_url" in block, (
            f"{branch} probes a hardcoded regional URL again — workspace keys "
            "will 401 at verify even when correctly configured"
        )


def test_a_workspace_key_without_endpoint_fails_with_the_specific_message() -> None:
    text = (_ROOT / "src/puripuly_heart/ui/controller.py").read_text(encoding="utf-8")
    start = text.index("async def _verify_qwen_key_with_model_fallback")
    guard = text.index('"qwen_workspace_endpoint_missing"', start)
    first_probe = text.index("_verify_qwen_llm_api_key", start)
    assert guard < first_probe, (
        "the sk-ws- guard runs after the probe, so the user still sees a bare "
        "401 instead of being told to paste the endpoint"
    )
    field = (
        _ROOT / "src/puripuly_heart/ui/components/settings/api_key_field.py"
    ).read_text(encoding="utf-8")
    assert "error.qwen_workspace_endpoint_missing" in field


@pytest.mark.parametrize("locale", ["en", "zh-CN", "ja", "ko"])
def test_the_field_and_error_are_localized(locale: str) -> None:
    import json

    data = json.loads(
        (_ROOT / f"src/puripuly_heart/data/i18n/{locale}.json").read_text(
            encoding="utf-8"
        )
    )
    assert data.get("settings.qwen_workspace_endpoint")
    assert data.get("settings.qwen_workspace_endpoint.hint")
    assert data.get("error.qwen_workspace_endpoint_missing")


# ── r390: the field only appears when it applies ─────────────────────────────


def _visibility(key_value: str, saved_endpoint: str, key_row_visible: bool = True) -> bool:
    """Drive the real visibility rule with stand-ins for the flet widgets."""
    from puripuly_heart.config.settings import AppSettings
    from puripuly_heart.ui.views.settings import SettingsView

    class _Field:
        visible = False
        page = None

        def update(self) -> None:  # pragma: no cover - never has a page here
            pass

    class _Key:
        def __init__(self) -> None:
            self.value = key_value
            self.visible = key_row_visible

    view = object.__new__(SettingsView)
    view._qwen_workspace_endpoint_field = _Field()
    view._alibaba_key_singapore = _Key()
    settings = AppSettings()
    settings.qwen.workspace_endpoint = saved_endpoint
    view._settings = settings
    SettingsView._sync_qwen_workspace_endpoint_visibility(view)
    return view._qwen_workspace_endpoint_field.visible


def test_hidden_for_an_ordinary_key() -> None:
    """The whole point of r390: people with classic keys should never be shown
    a box about a key format they do not have."""
    assert _visibility("sk-abcdef0123", "") is False


def test_hidden_when_nothing_is_entered() -> None:
    assert _visibility("", "") is False


def test_shown_while_a_workspace_key_is_typed() -> None:
    assert _visibility("sk-ws-abcdef", "") is True


def test_shown_when_an_endpoint_is_already_saved() -> None:
    """Otherwise a saved endpoint becomes uneditable and unclearable."""
    assert _visibility("", WORKSPACE) is True


def test_hidden_when_the_singapore_row_itself_is_hidden() -> None:
    """No Qwen provider selected — the endpoint must not outlive its key row."""
    assert _visibility("sk-ws-abcdef", "", key_row_visible=False) is False


def test_a_partial_prefix_does_not_reveal_it() -> None:
    assert _visibility("sk-w", "") is False


def test_the_key_field_reports_edits_without_exposing_the_key() -> None:
    """The callback exists so the owner can react to WHAT KIND of key is being
    typed; it must not hand the key value around."""
    import inspect

    from puripuly_heart.ui.components.settings.api_key_field import ApiKeyField

    signature = inspect.signature(ApiKeyField.__init__)
    assert "on_value_change" in signature.parameters
    source = inspect.getsource(ApiKeyField)
    assert "self._on_value_change()" in source, "the callback is never fired"
    assert "self._on_value_change(self" not in source
    assert "_on_value_change(val" not in source, "the key value is being passed out"
