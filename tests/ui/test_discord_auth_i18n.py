from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

REQUIRED_DISCORD_AUTH_KEYS = [
    "discord_auth.body",
    "discord_auth.continue",
    "discord_auth.close",
    "discord_auth.reopen_browser",
    "discord_auth.cancel",
    "discord_auth.waiting_body",
    "discord_auth.callback_received_body",
    "discord_auth.success",
    "discord_auth.referral_id.label",
    "discord_auth.referral_reward_applied",
    "discord_auth.error.email_unverified",
    "discord_auth.error.account_too_new",
    "discord_auth.error.lifetime_used",
    "discord_auth.error.hardware_duplicate",
    "discord_auth.error.daily_cap",
    "discord_auth.error.expired",
    "discord_auth.error.loopback_unavailable",
    "discord_auth.error.retry",
    "debug_preview.discord_auth",
]


_EXPECTED_EXACT_STRINGS = {
    "en": {
        "discord_auth.body": "PuriPuly gives new users a free usage allowance.\nThat's about 600–700 translated utterances.\nYou'll receive it right after Discord verification.\n\nWe don't keep personal information.\nWe only check the minimum information needed for verification.\n\nIf you received a Pass ID from a friend, enter it here.\nYou and your friend can each get 200 extra translations.",
        "discord_auth.success": "Discord verification is complete.",
    },
    "ko": {
        "discord_auth.body": "PuriPuly는 신규 사용자에게 무료 사용량을 제공해요.\n발화 기준 약 600~700회를 번역할 수 있어요.\nDiscord 인증 후 바로 발급돼요.\n\n개인 정보는 보관하지 않아요.\n인증에 필요한 최소 정보만 확인해요.\n\n친구에게 받은 Pass ID가 있으면 입력해 주세요.\n친구와 같이 200회 추가 사용량을 받을 수 있어요.",
        "discord_auth.success": "Discord 인증이 완료되었어요.",
    },
    "ja": {
        "discord_auth.body": "PuriPulyでは新規ユーザー向けに無料利用枠をご用意しています。\n発話ベースで約600〜700回翻訳できます。\nDiscord認証後、すぐに付与されます。\n\n個人情報は保存しません。\n認証に必要な最小限の情報だけを確認します。\n\n友だちから受け取った Pass ID があれば入力してください。\n友だちと一緒に追加で200回分の利用枠を受け取れます。",
        "discord_auth.success": "Discord認証が完了しました。",
    },
    "zh-CN": {
        "discord_auth.body": "PuriPuly 会为新用户提供免费使用额度。\n按发言计算，可翻译约 600–700 次。\n完成 Discord 认证后会立即发放。\n\n我们不会保存个人信息。\n只会确认认证所需的最低限度信息。\n\n如果你有朋友给你的 Pass ID，请在这里输入。\n你和朋友可以一起获得额外 200 次使用额度。",
        "discord_auth.success": "Discord 认证已完成。",
    },
}

_FORBIDDEN_DISCORD_AUTH_COPY_PATTERNS = {
    "currency or dollar amounts": re.compile(r"(?:\$|USD|usd|dollars?|달러|원|円|엔|美元|美金)"),
    "referral terminology": re.compile(r"Referral", re.IGNORECASE),
}

_EXPECTED_TALK_TOGETHER_PASS_STRINGS = {
    "en": {
        "discord_auth.referral_id.label": "Pass ID",
        "discord_auth.referral_reward_applied": "You and your friend got 200 extra uses.",
    },
    "ko": {
        "discord_auth.referral_id.label": "Pass ID",
        "discord_auth.referral_reward_applied": "친구와 함께 200회 추가 사용량을 받았어요.",
    },
    "ja": {
        "discord_auth.referral_id.label": "Pass ID",
        "discord_auth.referral_reward_applied": "友だちと一緒に200回分の追加使用量を受け取りました。",
    },
    "zh-CN": {
        "discord_auth.referral_id.label": "Pass ID",
        "discord_auth.referral_reward_applied": "你和朋友已获得 200 次额外使用量。",
    },
}


def _load_bundle(locale: str) -> dict[str, str]:
    i18n_path = (
        Path(__file__).resolve().parents[2]
        / "src"
        / "puripuly_heart"
        / "data"
        / "i18n"
        / f"{locale}.json"
    )
    return json.loads(i18n_path.read_text(encoding="utf-8"))


@pytest.mark.parametrize("locale", ["en", "ko", "zh-CN", "ja"])
def test_discord_auth_i18n_keys_exist_and_are_not_empty(locale: str) -> None:
    bundle = _load_bundle(locale)

    missing = [key for key in REQUIRED_DISCORD_AUTH_KEYS if key not in bundle]
    empty = [key for key in REQUIRED_DISCORD_AUTH_KEYS if bundle.get(key) == ""]

    assert missing == []
    assert empty == []


@pytest.mark.parametrize("locale", ["en", "ko", "zh-CN", "ja"])
def test_discord_auth_i18n_uses_planned_title_body_and_success_copy(
    locale: str,
) -> None:
    bundle = _load_bundle(locale)

    for key, expected_value in _EXPECTED_EXACT_STRINGS[locale].items():
        assert bundle[key] == expected_value


@pytest.mark.parametrize("locale", ["en", "ko", "zh-CN", "ja"])
def test_discord_auth_copy_uses_pass_terms_without_referral_or_currency(locale: str) -> None:
    bundle = _load_bundle(locale)
    checked_copy = "\n".join(
        [
            bundle["discord_auth.body"],
            bundle["discord_auth.referral_id.label"],
            bundle["discord_auth.referral_reward_applied"],
        ]
    )

    violations = {
        label: pattern.pattern
        for label, pattern in _FORBIDDEN_DISCORD_AUTH_COPY_PATTERNS.items()
        if pattern.search(checked_copy)
    }

    assert violations == {}


@pytest.mark.parametrize("locale", ["en", "ko", "zh-CN", "ja"])
def test_discord_auth_talk_together_pass_i18n_uses_planned_copy(locale: str) -> None:
    bundle = _load_bundle(locale)

    for key, expected_value in _EXPECTED_TALK_TOGETHER_PASS_STRINGS[locale].items():
        assert bundle[key] == expected_value
