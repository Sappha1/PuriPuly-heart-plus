from __future__ import annotations

from puripuly_heart.config.settings import AppSettings
from puripuly_heart.providers.llm.openrouter import OpenRouterKeyMetadata

MANAGED_EFFECTIVE_EXHAUSTION_USD = 0.0007


def is_effectively_exhausted(metadata: OpenRouterKeyMetadata | None) -> bool:
    return bool(
        metadata is not None
        and metadata.remaining_usd is not None
        and metadata.remaining_usd <= MANAGED_EFFECTIVE_EXHAUSTION_USD
    )


def store_managed_entitlement_snapshot(
    settings: AppSettings,
    *,
    managed_credential_ref: str | None,
    expires_at: str | None,
) -> None:
    existing_ref = settings.managed_identity.active_managed_credential_ref
    normalized_ref = (
        (managed_credential_ref or "").strip()
        or existing_ref
        or (expires_at or "").strip()
        or settings.managed_identity.installation_id
        or "managed-entitlement"
    )
    if settings.managed_identity.active_managed_credential_ref != normalized_ref:
        settings.managed_identity.founder_letter_seen_credential_ref = None
    settings.managed_identity.active_managed_credential_ref = normalized_ref
    settings.managed_identity.active_managed_expires_at = (expires_at or "").strip() or None


def should_auto_show_founder_letter(
    settings: AppSettings,
    metadata: OpenRouterKeyMetadata | None,
) -> bool:
    active_ref = settings.managed_identity.active_managed_credential_ref
    return bool(
        active_ref
        and is_effectively_exhausted(metadata)
        and settings.managed_identity.founder_letter_seen_credential_ref != active_ref
    )


def mark_founder_letter_shown(settings: AppSettings) -> None:
    settings.managed_identity.founder_letter_seen_credential_ref = (
        settings.managed_identity.active_managed_credential_ref
    )
