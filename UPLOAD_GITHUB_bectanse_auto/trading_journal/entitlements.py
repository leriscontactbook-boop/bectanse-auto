"""Single entitlement engine for Academy-included and standalone Journal access."""

from __future__ import annotations

import os
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone


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
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def academy_membership_state(member: dict | None, *, now: datetime | None = None) -> tuple[bool, datetime | None]:
    member = member or {}
    if not member or bool(member.get("admin_suspended")):
        return False, None
    if str(member.get("access_level") or "member").lower() in {"explorer", "demo"}:
        return False, None
    now = now or datetime.now(timezone.utc)
    period_end = _as_utc(member.get("billing_current_period_end") or member.get("date_fin"))
    grace_days = max(0, int(os.environ.get("JOURNAL_ACADEMY_GRACE_DAYS", "7")))
    grace_until = period_end + timedelta(days=grace_days) if period_end else None
    if period_end and now > grace_until:
        return False, grace_until
    billing_status = str(member.get("billing_status") or "legacy").lower()
    status_allows_access = billing_status in {"active", "trialing", "legacy"} or (
        billing_status == "canceled" and bool(period_end and now <= period_end)
    )
    normal_access = bool(member.get("actif", False)) and status_allows_access
    grace_access = bool(period_end and period_end < now <= grace_until)
    return normal_access or grace_access, grace_until if grace_access else None


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
        academy_plan = normalize_plan(os.environ.get("ACADEMY_JOURNAL_PLAN", "JOURNAL_PRO"))
        return _with_source(academy_plan, "ACADEMY_INCLUDED", grace_until)

    subscription = subscription or {}
    subscription_status = str(subscription.get("subscription_status") or "").lower()
    period_end = _as_utc(subscription.get("current_period_end"))
    standalone_active = subscription_status in {"active", "trialing"}
    if period_end and period_end <= now:
        standalone_active = False
    if standalone_active:
        return _with_source(subscription.get("plan") or "JOURNAL_PRO", "JOURNAL_SUBSCRIPTION")

    active_grants = []
    for grant in grants or []:
        valid_from = _as_utc(grant.get("valid_from"))
        valid_until = _as_utc(grant.get("valid_until"))
        if str(grant.get("status") or "").upper() != "ACTIVE":
            continue
        if valid_from and valid_from > now:
            continue
        if valid_until and valid_until <= now:
            continue
        active_grants.append(grant)
    if active_grants:
        source = "ADMIN" if any(str(g.get("source")).upper() == "ADMIN" for g in active_grants) else "PROMO"
        return _with_source("JOURNAL_PRO", source)

    return PLAN_RULES["NONE"]


def can_add_trading_account(entitlements: Entitlements, current_count: int) -> bool:
    return entitlements.allowed and current_count < entitlements.max_accounts


def can_access_advanced_analytics(entitlements: Entitlements) -> bool:
    return entitlements.allowed and entitlements.advanced_analytics


def can_export(entitlements: Entitlements) -> bool:
    return entitlements.allowed and entitlements.export


def get_historical_limit(entitlements: Entitlements) -> int | None:
    return entitlements.historical_days
