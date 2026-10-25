"""What the usage ring shows for an OpenAI translator.

Best available signal first: "no credits" overrides everything; then the
user's own weekly budget (this week's spend comes from a small on-disk
ledger); then the prepaid credit balance (only some keys may read it);
otherwise the per-minute token limit OpenAI reports on every reply. The
headline is what the ring's click popup leads with.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from puripuly_heart.ui.i18n import t


def _money(amount: float) -> str:
    if amount >= 1:
        return f"{amount:,.2f}"
    if amount >= 0.01:
        return f"{amount:.3f}"
    return f"{amount:.4f}"


def _percent(fraction: float) -> str:
    return f"{round(max(0.0, min(1.0, fraction)) * 100)}"


@dataclass(slots=True)
class MeterView:
    fraction: float | None = None
    caption: str = ""
    tone: str = "ok"
    headline: str = ""
    lines: list[str] = field(default_factory=list)


def openai_meter_details(snap, weekly_budget: float = 0.0) -> MeterView:
    lines: list[str] = []
    budget = weekly_budget if weekly_budget and weekly_budget > 0 else 0.0
    week_spent = float(getattr(snap, "week_spent_usd", 0.0) or 0.0)
    week_requests = int(getattr(snap, "week_requests", 0) or 0)

    if budget:
        lines.append(
            t(
                "dashboard.usage.tooltip.budget",
                left=_money(max(0.0, budget - week_spent)),
                budget=_money(budget),
            )
        )
    if week_requests:
        lines.append(
            t(
                "dashboard.usage.tooltip.week",
                requests=str(week_requests),
                spent=_money(week_spent),
            )
        )
    if snap.credit_available is not None:
        if snap.credit_granted:
            lines.append(
                t(
                    "dashboard.usage.tooltip.credits",
                    available=_money(snap.credit_available),
                    granted=_money(snap.credit_granted),
                )
            )
        else:
            lines.append(t("dashboard.usage.tooltip.credits_only", available=_money(snap.credit_available)))
    if snap.requests:
        lines.append(
            t(
                "dashboard.usage.tooltip.session",
                requests=str(snap.requests),
                spent=_money(snap.spent_usd),
            )
        )
    if snap.limit_tokens and snap.remaining_tokens is not None:
        lines.append(
            t(
                "dashboard.usage.tooltip.rate",
                remaining=f"{snap.remaining_tokens:,}",
                limit=f"{snap.limit_tokens:,}",
            )
        )
    if snap.credit_available is None and (snap.requests or snap.out_of_credits):
        lines.append(t("dashboard.usage.tooltip.no_balance"))

    if snap.out_of_credits:
        return MeterView(1.0, t("dashboard.usage.no_credits"), "error",
                         t("dashboard.usage.headline.no_credits"), lines)

    if budget:
        left = max(0.0, budget - week_spent)
        fraction = left / budget
        tone = "error" if left <= 0 else ("warning" if fraction < 0.2 else "ok")
        return MeterView(
            fraction,
            t("dashboard.usage.week_left", amount=_money(left)),
            tone,
            t("dashboard.usage.headline.week", percent=_percent(fraction)),
            lines,
        )

    if snap.credit_available is not None:
        available = max(0.0, snap.credit_available)
        if snap.credit_granted and snap.credit_granted > 0:
            fraction = available / snap.credit_granted
        else:
            fraction = 1.0 if available > 0 else 0.0
        tone = "warning" if fraction < 0.2 or available < 1 else "ok"
        if available <= 0:
            tone = "error"
        return MeterView(
            fraction,
            t("dashboard.usage.left", amount=_money(available)),
            tone,
            t("dashboard.usage.headline.credits", percent=_percent(fraction)),
            lines,
        )

    if snap.limit_tokens and snap.remaining_tokens is not None:
        fraction = snap.remaining_tokens / snap.limit_tokens
        tone = "warning" if fraction < 0.15 else "ok"
        caption = t("dashboard.usage.used", amount=_money(snap.spent_usd)) if snap.requests else ""
        headline = (
            t("dashboard.usage.headline.week_spent", amount=_money(week_spent))
            if week_requests
            else t("dashboard.usage.headline.rate", percent=_percent(fraction))
        )
        return MeterView(fraction, caption, tone, headline, lines)

    if snap.requests or week_requests:
        return MeterView(
            None,
            t("dashboard.usage.used", amount=_money(snap.spent_usd)) if snap.requests else "",
            "ok",
            t("dashboard.usage.headline.week_spent", amount=_money(week_spent)),
            lines,
        )
    return MeterView(None, "", "ok", "", lines)


def openai_meter_view(snap, weekly_budget: float = 0.0) -> tuple[float | None, str, str, list[str]]:
    """(fraction_left or None, caption, tone, tooltip_lines)."""
    view = openai_meter_details(snap, weekly_budget)
    return view.fraction, view.caption, view.tone, view.lines
