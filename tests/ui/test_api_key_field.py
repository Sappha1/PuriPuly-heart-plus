from __future__ import annotations

import asyncio

import pytest

pytest.importorskip("flet")

from puripuly_heart.ui.components.settings.api_key_field import ApiKeyField


@pytest.mark.asyncio
async def test_api_key_field_default_status_mode_verifies_saved_value() -> None:
    saved: list[tuple[str, str]] = []
    verified: list[tuple[str, str]] = []

    async def verify(provider: str, key: str) -> tuple[bool, str]:
        verified.append((provider, key))
        return True, "ok"

    field = ApiKeyField(
        "settings.api_keys.openrouter",
        "openrouter_api_key",
        "openrouter",
        on_verify=verify,
        on_save=lambda key, value: saved.append((key, value)),
    )

    field._text_field.value = "provider-secret"
    field._handle_change(None)
    field._handle_blur(None)
    await field._run_verification()

    assert len(field.controls) == 2
    assert saved == [("openrouter_api_key", "provider-secret")]
    assert verified == [("openrouter", "provider-secret")]
    assert field._current_status == "success"
    assert field._last_verified_hash == field._get_key_hash("provider-secret")


@pytest.mark.asyncio
async def test_api_key_field_default_status_mode_verifies_unchanged_loaded_value() -> None:
    saved: list[tuple[str, str]] = []
    verified: list[tuple[str, str]] = []

    async def verify(provider: str, key: str) -> tuple[bool, str]:
        verified.append((provider, key))
        return True, "ok"

    field = ApiKeyField(
        "settings.api_keys.openrouter",
        "openrouter_api_key",
        "openrouter",
        on_verify=verify,
        on_save=lambda key, value: saved.append((key, value)),
    )

    field.value = "loaded-secret"
    field._last_verified_hash = ""
    field._handle_blur(None)
    await field._run_verification()

    assert saved == []
    assert verified == [("openrouter", "loaded-secret")]
    assert field._current_status == "success"
    assert field._last_verified_hash == field._get_key_hash("loaded-secret")


@pytest.mark.asyncio
async def test_api_key_field_verifies_latest_edit_after_blur_during_inflight_verification() -> None:
    saved: list[tuple[str, str]] = []
    verified: list[tuple[str, str]] = []
    first_started = asyncio.Event()
    release_first = asyncio.Event()

    async def verify(provider: str, key: str) -> tuple[bool, str]:
        verified.append((provider, key))
        if key == "first-secret":
            first_started.set()
            await release_first.wait()
        return True, "ok"

    field = ApiKeyField(
        "settings.api_keys.openrouter",
        "openrouter_api_key",
        "openrouter",
        on_verify=verify,
        on_save=lambda key, value: saved.append((key, value)),
    )

    field._text_field.value = "first-secret"
    field._handle_change(None)
    field._handle_blur(None)
    verification_task = asyncio.create_task(field._run_verification())
    await first_started.wait()

    field._text_field.value = "second-secret"
    field._handle_change(None)
    field._handle_blur(None)

    release_first.set()
    await verification_task

    assert saved == [
        ("openrouter_api_key", "first-secret"),
        ("openrouter_api_key", "second-secret"),
    ]
    assert verified == [
        ("openrouter", "first-secret"),
        ("openrouter", "second-secret"),
    ]
    assert field._current_status == "success"
    assert field._last_verified_hash == field._get_key_hash("second-secret")


def test_api_key_field_can_hide_status_and_skip_verification() -> None:
    saved: list[tuple[str, str]] = []
    verified: list[tuple[str, str]] = []

    async def verify(provider: str, key: str) -> tuple[bool, str]:
        verified.append((provider, key))
        return True, "ok"

    field = ApiKeyField(
        "settings.local_llm.api_key",
        "local_llm_api_key",
        "local_llm",
        on_verify=verify,
        on_save=lambda key, value: saved.append((key, value)),
        show_status=False,
    )

    field._text_field.value = "local-secret"
    field._handle_change(None)
    field._handle_blur(None)

    assert len(field.controls) == 1
    assert saved == [("local_llm_api_key", "local-secret")]
    assert verified == []
    assert not hasattr(field, "_pending_key")


def test_api_key_field_does_not_save_unchanged_loaded_value() -> None:
    saved: list[tuple[str, str]] = []
    field = ApiKeyField(
        "settings.local_llm.api_key",
        "local_llm_api_key",
        "local_llm",
        on_save=lambda key, value: saved.append((key, value)),
        show_status=False,
    )

    field.value = "loaded-secret"
    field._handle_blur(None)

    assert saved == []
