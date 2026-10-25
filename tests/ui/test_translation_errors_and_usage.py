from __future__ import annotations

import flet as ft
import pytest

from puripuly_heart.providers.llm.openai_chat import OpenAIRequestError, OpenAIUsageSnapshot
from puripuly_heart.ui.i18n import set_locale
from puripuly_heart.ui.translation_errors import (
    classify_translation_error,
    friendly_translation_error,
    is_account_problem,
)
from puripuly_heart.ui.usage_meter import openai_meter_view
from puripuly_heart.ui.views.dashboard import DashboardView


@pytest.fixture(autouse=True)
def _english():
    set_locale("en")
    yield


def _no_credits_text() -> str:
    return str(
        OpenAIRequestError(
            status=429,
            code="insufficient_quota",
            api_message="You have no credits remaining. Add credits to continue using the API "
            "at https://platform.openai.com/settings/organization/billing.",
        )
    )


def test_no_credits_is_reported_as_such() -> None:
    text = _no_credits_text()

    assert classify_translation_error(text)[:3] == ("no_credits", "OpenAI", 429)
    assert friendly_translation_error(text).startswith("No credits remaining on your OpenAI account")
    assert is_account_problem(text)


def test_rejected_key_and_rate_limit_are_distinct() -> None:
    bad_key = "OpenAI request failed (status=401, message=Incorrect API key provided)"
    limited = "OpenAI request failed (status=429, message=Rate limit reached for requests)"

    assert classify_translation_error(bad_key)[0] == "bad_key"
    assert "rejected the API key" in friendly_translation_error(bad_key)
    assert classify_translation_error(limited)[0] == "rate_limited"
    assert not is_account_problem(limited)


def test_other_providers_out_of_balance_count_too() -> None:
    deepseek = "DeepSeek request failed (status=402, message=Insufficient Balance)"

    assert classify_translation_error(deepseek)[:2] == ("no_credits", "DeepSeek")


def test_unknown_errors_keep_the_api_message() -> None:
    text = "OpenAI request failed (status=500, message=The server had an error)"

    assert friendly_translation_error(text) == "Translation failed: The server had an error"


def test_meter_prefers_balance_then_rate_limit() -> None:
    snap = OpenAIUsageSnapshot(
        requests=3, spent_usd=0.0021, limit_tokens=200_000, remaining_tokens=50_000
    )
    fraction, caption, tone, lines = openai_meter_view(snap)
    assert fraction == pytest.approx(0.25)
    assert caption == "$0.0021 used"
    assert tone == "ok"
    assert any("tokens/min" in line for line in lines)

    snap.credit_available, snap.credit_granted = 1.5, 10.0
    fraction, caption, tone, _ = openai_meter_view(snap)
    assert fraction == pytest.approx(0.15)
    assert caption == "$1.50 left"
    assert tone == "warning"


def test_meter_shows_no_credits_in_red() -> None:
    snap = OpenAIUsageSnapshot(out_of_credits=True)

    fraction, caption, tone, _ = openai_meter_view(snap)

    assert (fraction, caption, tone) == (0.0, "No credits", "error")


def test_meter_hidden_before_any_use() -> None:
    assert openai_meter_view(OpenAIUsageSnapshot())[:2] == (None, "")


def _dash_with_pending() -> tuple[DashboardView, ft.Column, ft.Text]:
    dash = DashboardView.__new__(DashboardView)
    pending_text = ft.Text("hello", italic=True)
    col = ft.Column([pending_text])
    dash._pending_sent_col = col
    dash._pending_sent_text = pending_text
    dash._pending_version = 0
    return dash, col, pending_text


def test_failed_typed_message_shows_the_reason_right_away() -> None:
    dash, col, pending_text = _dash_with_pending()

    assert dash.show_translation_error(_no_credits_text(), channel="self") is True

    assert dash._pending_sent_col is None  # the generic timeout line is cancelled
    assert dash._pending_version == 1
    assert col.controls[-1].value.startswith("No credits remaining")
    assert pending_text.color == "#888888"


def test_peer_failures_do_not_touch_the_typed_message() -> None:
    dash, col, _ = _dash_with_pending()

    assert dash.show_translation_error(_no_credits_text(), channel="peer") is False
    assert len(col.controls) == 1


def test_keyless_service_refusal_reads_as_unavailable_not_bad_key() -> None:
    text = (
        "Index-Translate request failed (status=403, "
        "message=service unavailable (answered with a web page))"
    )

    assert classify_translation_error(text)[0] == "unavailable"
    assert friendly_translation_error(text).startswith("Index-Translate is not reachable")
    assert is_account_problem(text) is False


def test_weekly_budget_drives_the_ring() -> None:
    from puripuly_heart.ui.usage_meter import openai_meter_details

    snap = OpenAIUsageSnapshot(requests=3, spent_usd=0.5, week_spent_usd=4.0, week_requests=40)

    view = openai_meter_details(snap, weekly_budget=5.0)

    assert view.fraction == pytest.approx(0.2)
    assert view.caption == "$1.00 left this week"
    assert any("Last 7 days: 40 requests" in line for line in view.lines)

    spent_out = openai_meter_details(
        OpenAIUsageSnapshot(week_spent_usd=6.0, week_requests=50), weekly_budget=5.0
    )
    assert spent_out.fraction == 0.0 and spent_out.tone == "error"


def test_without_budget_or_limits_there_is_no_percentage() -> None:
    from puripuly_heart.ui.usage_meter import openai_meter_details

    view = openai_meter_details(OpenAIUsageSnapshot(week_spent_usd=0.42, week_requests=9))

    assert view.fraction is None
    assert any("Last 7 days: 9 requests" in line for line in view.lines)


def test_spend_ledger_survives_a_restart_and_keeps_seven_days(tmp_path) -> None:
    from datetime import date, timedelta

    from puripuly_heart.providers.llm.openai_chat import OpenAIUsageTracker

    path = tmp_path / "openai_usage.json"
    today = date.today()
    old = (today - timedelta(days=9)).isoformat()
    path.write_text('{"days": {"%s": [9.0, 90]}}' % old, encoding="utf-8")

    tracker = OpenAIUsageTracker()
    tracker.attach_ledger(path)
    assert tracker.snapshot().week_requests == 0  # 9 days ago is outside the week
    tracker.record_response(
        model="gpt-6-luna",
        headers={},
        data={"usage": {"prompt_tokens": 1000, "completion_tokens": 500}},
    )

    reloaded = OpenAIUsageTracker()
    reloaded.attach_ledger(path)
    snap = reloaded.snapshot()
    assert snap.week_requests == 1
    assert snap.week_spent_usd > 0


def test_account_notice_can_be_dismissed_until_it_changes() -> None:
    dash = DashboardView.__new__(DashboardView)
    dash._translator_account_notice = None
    dash._translator_account_notice_dismissed = None
    dash._sync_notice = lambda: None

    dash.set_translator_account_notice("No credits remaining")
    assert dash._visible_account_notice() == "No credits remaining"

    dash.page = None  # no page: the dismiss applies directly
    dash._on_notice_dismiss_click()
    assert dash._visible_account_notice() is None

    dash.set_translator_account_notice("OpenAI rejected the API key")
    assert dash._visible_account_notice() == "OpenAI rejected the API key"

    dash.set_translator_account_notice(None)  # a translation succeeded
    assert dash._translator_account_notice_dismissed is None


def test_trans_row_ring_shows_the_percent_in_its_centre() -> None:
    from puripuly_heart.ui.views.dashboard import _ToggleRow

    row = _ToggleRow(ft.Icons.TRANSLATE, "TRANS", on_click=lambda _e: None)

    row.set_meter(0.4, "#ff0000")
    assert row._meter_box.visible is True
    assert row._meter.value == pytest.approx(0.4)
    assert row._meter.color == "#ff0000"
    assert row._meter_text.value == "40%"

    row.set_meter(None)
    assert row._meter_box.visible is False


def test_trans_row_ring_reads_dashes_until_a_number_exists() -> None:
    from puripuly_heart.ui.views.dashboard import _ToggleRow

    row = _ToggleRow(ft.Icons.TRANSLATE, "TRANS", on_click=lambda _e: None)

    row.set_meter(None, unknown=True)
    assert row._meter_box.visible is True
    assert row._meter_text.value == "--"
    assert row._meter.value == 0.0

    row.set_meter(0.0, "#cf4040")  # out of credits
    assert row._meter_text.value == "0%"
