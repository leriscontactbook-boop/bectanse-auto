"""Single entitlement engine for Academy-included and standalone Journal access."""

from __future__ import annotations

import os
from dataclasses import asdict, dataclass
from datetime import datetime, timezone


FEATURE_KEYS = (
    "journal.basic",
    "journal.calendar",
    "journal.analytics",
    "journal.advanced_analytics",
    "journal.multi_account",
    "journal.export",
    "journal.priority_sync",
    "coach.daily",
    "coach.weekly",
    "coach.monthly",
    "coach.advanced_patterns",
    "coach.ai_explanations",
)


@dataclass(frozen=True)
class Entitlements:
    allowed: bool
    source: str
    plan: str
    max_accounts: int
    historical_days: int | None
    basic: bool
    calendar: bool
    analytics: bool
    advanced_analytics: bool
    multi_account: bool
    export: bool
    priority_sync: bool
    coach_daily: bool
    coach_weekly: bool
    coach_monthly: bool
    coach_advanced_patterns: bool
    coach_ai_explanations: bool
    grace_until: datetime | None = None

    def as_dict(self) -> dict:
        result = asdict(self)
        result["grace_until"] = (
            self.grace_until.isoformat() if self.grace_until else None
        )
        result["features"] = {
            "journal.basic": self.basic,
            "journal.calendar": self.calendar,
            "journal.analytics": self.analytics,
            "journal.advanced_analytics": self.advanced_analytics,
            "journal.multi_account": self.multi_account,
            "journal.export": self.export,
            "journal.priority_sync": self.priority_sync,
            "coach.daily": self.coach_daily,
            "coach.weekly": self.coach_weekly,
            "coach.monthly": self.coach_monthly,
            "coach.advanced_patterns": self.coach_advanced_patterns,
            "coach.ai_explanations": self.coach_ai_explanations,
        }
        return result


PLAN_RULES = {
    "NONE": Entitlements(False, "NONE", "NONE", 0, 0, False, False, False,
                         False, False, False, False, False, False, False, False, False),
    "JOURNAL_PRO": Entitlements(True, "JOURNAL_SUBSCRIPTION", "JOURNAL_PRO", 1,
                                None, True, True, True, False, False, False, False,
                                True, True, False, False, False),
    "JOURNAL_ELITE": Entitlements(True, "JOURNAL_SUBSCRIPTION", "JOURNAL_ELITE", 10,
                                  None, True, True, True, True, True, True, True,
                                  True, True, True, True, True),
}


def normalize_plan(plan: str) -> str:
    aliases = {"PRO": "JOURNAL_PRO", "ELITE": "JOURNAL_ELITE"}
    normalized = aliases.get(str(plan or "").upper(), str(plan or "").upper())
    return normalized if normalized in PLAN_RULES and normalized != "NONE" else "JOURNAL_PRO"


def _as_utc(value) -> datetime | None:
    if not value:
        return None
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    if not isinstance(value, datetime):
        return None
    # Naive member dates come from a PostgreSQL TIMESTAMP column and follow the
    # runtime's local clock; convert that clock explicitly before comparing it.
    return value.astimezone(timezone.utc)


def academy_membership_state(member: dict | None, *, now: datetime | None = None) -> tuple[bool, datetime | None]:
    member = member or {}
    if (not member or bool(member.get("admin_suspended")) or
            not bool(member.get("actif", False))):
        return False, None
    if str(member.get("access_level") or "member").lower() in {"explorer", "demo"}:
        return False, None
    now = now or datetime.now(timezone.utc)
    period_end = _as_utc(member.get("billing_current_period_end") or member.get("date_fin"))
    billing_status = str(member.get("billing_status") or "legacy").lower()
    status_allows_access = billing_status in {"active", "trialing", "legacy"}
    # Fail closed: a paid access without a known future end date is not valid.
    return bool(status_allows_access and period_end and period_end > now), None


def standalone_subscription_state(
    subscription: dict | None, *, now: datetime | None = None,
) -> bool:
    subscription = subscription or {}
    now = now or datetime.now(timezone.utc)
    period_end = _as_utc(subscription.get("current_period_end"))
    status = str(subscription.get("subscription_status") or "").lower()
    return bool(status in {"active", "trialing"} and period_end and period_end > now)


def _with_source(plan: str, source: str, grace_until: datetime | None = None) -> Entitlements:
    base = PLAN_RULES[normalize_plan(plan)]
    return Entitlements(
        allowed=True,
        source=source,
        plan="ACADEMY_INCLUDED" if source == "ACADEMY_INCLUDED" else base.plan,
        max_accounts=base.max_accounts,
        historical_days=base.historical_days,
        basic=base.basic,
        calendar=base.calendar,
        analytics=base.analytics,
        advanced_analytics=base.advanced_analytics,
        multi_account=base.multi_account,
        export=base.export,
        priority_sync=base.priority_sync,
        coach_daily=True if source == "ACADEMY_INCLUDED" else base.coach_daily,
        coach_weekly=True if source == "ACADEMY_INCLUDED" else base.coach_weekly,
        coach_monthly=True if source == "ACADEMY_INCLUDED" else base.coach_monthly,
        coach_advanced_patterns=True if source == "ACADEMY_INCLUDED" else base.coach_advanced_patterns,
        coach_ai_explanations=True if source == "ACADEMY_INCLUDED" else base.coach_ai_explanations,
        grace_until=grace_until,
    )


def resolve_entitlements(
    subscription: dict | None,
    member: dict | None = None,
    grants: list[dict] | None = None,
    *,
    now: datetime | None = None,
) -> Entitlements:
    """Resolve one effective Journal access without duplicating authentication."""
    now = now or datetime.now(timezone.utc)
    academy_active, grace_until = academy_membership_state(member, now=now)
    if academy_active:
        academy_plan = normalize_plan(os.environ.get("ACADEMY_JOURNAL_PLAN", "JOURNAL_ELITE"))
        return _with_source(academy_plan, "ACADEMY_INCLUDED", grace_until)

    subscription = subscription or {}
    if standalone_subscription_state(subscription, now=now):
        return _with_source(subscription.get("plan") or "JOURNAL_PRO", "JOURNAL_SUBSCRIPTION")

    # Feature grants never replace payment. They remain available in the schema
    # for future feature-level overrides, but base Journal access always
    # requires a valid Academy or standalone subscription.
    return PLAN_RULES["NONE"]


def can_add_trading_account(entitlements: Entitlements, current_count: int) -> bool:
    return entitlements.allowed and current_count < entitlements.max_accounts


def can_access_advanced_analytics(entitlements: Entitlements) -> bool:
    return entitlements.allowed and entitlements.advanced_analytics


def can_export(entitlements: Entitlements) -> bool:
    return entitlements.allowed and entitlements.export


def get_historical_limit(entitlements: Entitlements) -> int | None:
    return entitlements.historical_days
