"""Turn translation provider failures into a short line the user can act on.

Providers raise errors like ``OpenAI request failed (status=429, message=...)``.
The chat used to show only a generic "Translation failed" after a timeout;
this keeps the provider's own reason (no credits, rejected key, rate limit)
so the user knows what to fix.
"""

from __future__ import annotations

import re

from puripuly_heart.ui.i18n import t

_FAILED_RE = re.compile(
    r"(?P<provider>[A-Za-z][\w.-]*(?: [A-Z][\w.-]*)?) request failed "
    r"\(status=(?P<status>\d+), message=(?P<message>.*?)\)\s*(?:\[|$)",
    re.S,
)

_NO_CREDITS_MARKERS = (
    "insufficient_quota",
    "no credits",
    "exceeded your current quota",
    "insufficient balance",
    "quota exceeded",
    "billing_hard_limit",
    "credit balance is too low",
)
_BAD_KEY_MARKERS = (
    "incorrect api key",
    "invalid api key",
    "invalid_api_key",
    "api key not valid",
    "authentication",
    "unauthorized",
)


# free services without a key: an auth-style refusal means the service is
# blocking or down, never that the user's key is wrong
_KEYLESS_PROVIDERS = frozenset({"index-translate"})
_UNAVAILABLE_MARKERS = ("web page", "service unavailable", "<!doctype", "<html")


def classify_translation_error(text: str) -> tuple[str, str, int | None, str]:
    """(kind, provider, status, api_message); kind is one of no_credits,
    bad_key, rate_limited, unavailable, generic."""
    raw = (text or "").strip()
    provider = ""
    status: int | None = None
    message = raw
    match = _FAILED_RE.search(raw)
    if match:
        provider = match.group("provider").strip()
        status = int(match.group("status"))
        message = match.group("message").strip()
    lowered = f"{raw} {message}".lower()
    keyless = provider.lower() in _KEYLESS_PROVIDERS
    if status == 429 and keyless:
        return "rate_limited", provider, status, message
    if any(marker in lowered for marker in _UNAVAILABLE_MARKERS) or (
        keyless and (status in (401, 403, 404, 412) or (status or 0) >= 500)
    ):
        return "unavailable", provider, status, message
    if any(marker in lowered for marker in _NO_CREDITS_MARKERS) or status in (402, 456):
        return "no_credits", provider, status, message
    if status in (401, 403) or any(marker in lowered for marker in _BAD_KEY_MARKERS):
        return "bad_key", provider, status, message
    if status == 429 or "rate limit" in lowered:
        return "rate_limited", provider, status, message
    return "generic", provider, status, message


def friendly_translation_error(text: str) -> str:
    kind, provider, _status, message = classify_translation_error(text)
    name = provider or t("dashboard.chat.error.provider_fallback", default="the translator")
    if kind == "no_credits":
        return t("dashboard.chat.error.no_credits", provider=name)
    if kind == "bad_key":
        return t("dashboard.chat.error.bad_key", provider=name)
    if kind == "rate_limited":
        return t("dashboard.chat.error.rate_limited", provider=name)
    if kind == "unavailable":
        return t("dashboard.chat.error.unavailable", provider=name)
    short = " ".join((message or "").split())
    if len(short) > 140:
        short = short[:137] + "..."
    if not short:
        return t("dashboard.chat.translation_failed")
    return t("dashboard.chat.error.generic", message=short)


def is_account_problem(text: str) -> bool:
    """Errors that keep failing until the user fixes their account or key."""
    return classify_translation_error(text)[0] in ("no_credits", "bad_key")
