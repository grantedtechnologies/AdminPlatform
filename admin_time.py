"""Admin wall-clock times ↔ naive UTC for MySQL DATETIME columns.

Form inputs are America/New_York (overridable via ADMIN_TIMEZONE). The main
app compares pilot windows with UTC_TIMESTAMP() / datetime.now(timezone.utc),
so we store naive UTC.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone
from functools import lru_cache
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


DEFAULT_TIMEZONE = "America/New_York"


class InvalidLocalTime(ValueError):
    """Nonexistent or ambiguous local wall time."""


@lru_cache(maxsize=1)
def admin_zone() -> ZoneInfo:
    name = (os.getenv("ADMIN_TIMEZONE") or DEFAULT_TIMEZONE).strip() or DEFAULT_TIMEZONE
    try:
        return ZoneInfo(name)
    except ZoneInfoNotFoundError as exc:
        raise InvalidLocalTime(f"Unknown timezone {name!r}.") from exc


def timezone_label() -> str:
    """Short label for UI (ET covers EST/EDT)."""
    key = (os.getenv("ADMIN_TIMEZONE") or DEFAULT_TIMEZONE).strip() or DEFAULT_TIMEZONE
    if key in ("America/New_York", "US/Eastern"):
        return "ET"
    return key


def local_form_to_utc_naive(value: str) -> datetime:
    """Parse a datetime-local string as admin-local wall time → naive UTC."""
    try:
        naive = datetime.fromisoformat(value)
    except ValueError as exc:
        raise InvalidLocalTime("Start and end must be valid dates.") from exc
    if naive.tzinfo is not None:
        raise InvalidLocalTime("Start and end must be local times without a zone suffix.")

    tz = admin_zone()
    fold0 = naive.replace(tzinfo=tz, fold=0)
    fold1 = naive.replace(tzinfo=tz, fold=1)
    utc0 = fold0.astimezone(timezone.utc)
    utc1 = fold1.astimezone(timezone.utc)
    if utc0 != utc1:
        raise InvalidLocalTime(
            "That local time is ambiguous during the daylight-saving fall-back. "
            "Pick a time one hour earlier or later."
        )
    roundtrip = utc0.astimezone(tz).replace(tzinfo=None)
    if roundtrip.replace(microsecond=0) != naive.replace(microsecond=0):
        raise InvalidLocalTime(
            "That local time does not exist during the daylight-saving spring-forward. "
            "Pick a valid time."
        )
    return utc0.replace(tzinfo=None)


def utc_naive_to_local_form(value: datetime | None) -> str:
    """Naive UTC from MySQL → datetime-local string in admin timezone."""
    if not value:
        return ""
    if value.tzinfo is not None:
        utc = value.astimezone(timezone.utc)
    else:
        utc = value.replace(tzinfo=timezone.utc)
    local = utc.astimezone(admin_zone())
    return local.strftime("%Y-%m-%dT%H:%M")


def utc_naive_to_display(value: datetime | None) -> str:
    """Human-readable admin-local time with zone label."""
    if not value:
        return "—"
    if value.tzinfo is not None:
        utc = value.astimezone(timezone.utc)
    else:
        utc = value.replace(tzinfo=timezone.utc)
    local = utc.astimezone(admin_zone())
    return f"{local.strftime('%Y-%m-%d %H:%M')} {timezone_label()}"


def to_unix(value: datetime) -> int:
    """Naive UTC DATETIME → unix timestamp for Stripe."""
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return int(value.astimezone(timezone.utc).timestamp())


def months_rounded_up(start: datetime, end: datetime) -> int:
    """Whole months from start→end, rounded up; minimum 1."""
    if end <= start:
        return 1

    def add_months(dt: datetime, months: int) -> datetime:
        year = dt.year + (dt.month - 1 + months) // 12
        month = (dt.month - 1 + months) % 12 + 1
        # Clamp day for short months
        from calendar import monthrange
        day = min(dt.day, monthrange(year, month)[1])
        return dt.replace(year=year, month=month, day=day)

    months = (end.year - start.year) * 12 + (end.month - start.month)
    if add_months(start, months) < end:
        months += 1
    return max(1, months)
