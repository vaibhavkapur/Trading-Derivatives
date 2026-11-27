"""Explicit market conventions, calendars, and pricing enums."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from enum import Enum
from typing import FrozenSet, Iterable, Mapping

from .errors import UnsupportedConventionError, ValidationError


class Currency(str, Enum):
    """Supported settlement and pricing currencies."""

    USD = "USD"
    EUR = "EUR"
    GBP = "GBP"
    JPY = "JPY"


class DayCountConvention(str, Enum):
    """Explicit day-count fraction conventions."""

    ACT_360 = "ACT/360"
    ACT_365F = "ACT/365F"
    ACT_ACT_ICMA = "ACT/ACT_ICMA"
    THIRTY_360_US = "30/360_US"
    THIRTY_E_360 = "30E/360"


class BusinessDayConvention(str, Enum):
    """Business-day adjustment rules."""

    UNADJUSTED = "UNADJUSTED"
    FOLLOWING = "FOLLOWING"
    MODIFIED_FOLLOWING = "MODIFIED_FOLLOWING"
    PRECEDING = "PRECEDING"


class Compounding(str, Enum):
    """Interest compounding conventions."""

    SIMPLE = "SIMPLE"
    ANNUAL = "ANNUAL"
    SEMI_ANNUAL = "SEMI_ANNUAL"
    QUARTERLY = "QUARTERLY"
    MONTHLY = "MONTHLY"
    CONTINUOUS = "CONTINUOUS"

    @property
    def periods_per_year(self) -> int:
        mapping = {
            Compounding.ANNUAL: 1,
            Compounding.SEMI_ANNUAL: 2,
            Compounding.QUARTERLY: 4,
            Compounding.MONTHLY: 12,
        }
        if self not in mapping:
            raise UnsupportedConventionError(
                f"Compounding {self.value} does not have discrete periods per year",
                details={"compounding": self.value},
            )
        return mapping[self]


class InterpolationMethod(str, Enum):
    """Yield curve interpolation methods."""

    LOG_LINEAR_DF = "LOG_LINEAR_DF"
    LINEAR_ZERO = "LINEAR_ZERO"


class StubType(str, Enum):
    """Coupon schedule stub placement."""

    NONE = "NONE"
    SHORT_FIRST = "SHORT_FIRST"
    LONG_FIRST = "LONG_FIRST"
    SHORT_LAST = "SHORT_LAST"
    LONG_LAST = "LONG_LAST"


def parse_enum(enum_cls, value: str, field_name: str):
    """Parse an enum by value or name with a structured UnsupportedConventionError."""
    if isinstance(value, enum_cls):
        return value
    if not isinstance(value, str):
        raise ValidationError(
            f"Field '{field_name}' must be a string, got {type(value).__name__}",
            details={"field": field_name, "value": repr(value)},
        )
    for member in enum_cls:
        if member.value == value or member.name == value:
            return member
    raise UnsupportedConventionError(
        f"Unsupported {field_name}: '{value}'",
        details={
            "field": field_name,
            "value": value,
            "supported": [m.value for m in enum_cls],
        },
    )


@dataclass(frozen=True)
class Calendar:
    """Explicit holiday calendar for business-day adjustments.

    Uses Saturday/Sunday weekends plus an explicit set of holiday dates.
    All built-in calendars are synthetic deterministic calendars for offline
    research and testing; they are not authoritative exchange schedules.
    """

    name: str
    holidays: FrozenSet[date] = field(default_factory=frozenset)
    weekend_days: FrozenSet[int] = field(default_factory=lambda: frozenset({5, 6}))

    def is_business_day(self, d: date) -> bool:
        return d.weekday() not in self.weekend_days and d not in self.holidays

    def adjust(self, d: date, convention: BusinessDayConvention) -> date:
        if convention == BusinessDayConvention.UNADJUSTED:
            return d
        if self.is_business_day(d):
            return d
        if convention == BusinessDayConvention.FOLLOWING:
            cur = d
            while not self.is_business_day(cur):
                cur += timedelta(days=1)
            return cur
        if convention == BusinessDayConvention.PRECEDING:
            cur = d
            while not self.is_business_day(cur):
                cur -= timedelta(days=1)
            return cur
        if convention == BusinessDayConvention.MODIFIED_FOLLOWING:
            cur = d
            while not self.is_business_day(cur):
                cur += timedelta(days=1)
            if cur.month != d.month:
                cur = d
                while not self.is_business_day(cur):
                    cur -= timedelta(days=1)
            return cur
        raise UnsupportedConventionError(
            f"Unsupported business day convention: {convention}",
            details={"convention": str(convention)},
        )

    def add_business_days(self, d: date, n_days: int) -> date:
        step = 1 if n_days >= 0 else -1
        remaining = abs(n_days)
        cur = d
        while remaining > 0:
            cur += timedelta(days=step)
            if self.is_business_day(cur):
                remaining -= 1
        return cur

    @classmethod
    def from_iso_dates(cls, name: str, iso_dates: Iterable[str]) -> "Calendar":
        parsed = set()
        for raw in iso_dates:
            try:
                parsed.add(date.fromisoformat(raw))
            except ValueError as exc:
                raise ValidationError(
                    f"Invalid holiday date '{raw}' in calendar '{name}'",
                    details={"calendar": name, "date": raw},
                ) from exc
        return cls(name=name, holidays=frozenset(parsed))


# Deterministic synthetic calendars for offline fixtures and demos.
SYNTHETIC_CALENDARS: Mapping[str, Calendar] = {
    "NONE": Calendar(name="NONE", holidays=frozenset(), weekend_days=frozenset()),
    "WEEKEND_ONLY": Calendar(name="WEEKEND_ONLY", holidays=frozenset()),
    "US_SYNTHETIC": Calendar.from_iso_dates(
        "US_SYNTHETIC",
        [
            "2024-01-01", "2024-01-15", "2024-02-19", "2024-05-27", "2024-06-19",
            "2024-07-04", "2024-09-02", "2024-10-14", "2024-11-11", "2024-11-28", "2024-12-25",
            "2025-01-01", "2025-01-20", "2025-02-17", "2025-05-26", "2025-06-19",
            "2025-07-04", "2025-09-01", "2025-10-13", "2025-11-11", "2025-11-27", "2025-12-25",
            "2026-01-01", "2026-01-19", "2026-02-16", "2026-05-25", "2026-06-19",
            "2026-07-03", "2026-09-07", "2026-10-12", "2026-11-11", "2026-11-26", "2026-12-25",
            "2027-01-01", "2027-01-18", "2027-02-15", "2027-05-31", "2027-06-18",
            "2027-07-05", "2027-09-06", "2027-10-11", "2027-11-11", "2027-11-25", "2027-12-24",
            "2028-01-01", "2028-07-04", "2028-12-25",
            "2029-01-01", "2029-07-04", "2029-12-25",
            "2030-01-01", "2030-07-04", "2030-12-25",
            "2031-01-01", "2031-07-04", "2031-12-25",
            "2032-01-01", "2032-07-05", "2032-12-24",
            "2033-01-01", "2033-07-04", "2033-12-26",
            "2034-01-02", "2034-07-04", "2034-12-25",
            "2035-01-01", "2035-07-04", "2035-12-25",
        ],
    ),
    "TARGET_SYNTHETIC": Calendar.from_iso_dates(
        "TARGET_SYNTHETIC",
        [
            "2024-01-01", "2024-03-29", "2024-04-01", "2024-05-01", "2024-12-25", "2024-12-26",
            "2025-01-01", "2025-04-18", "2025-04-21", "2025-05-01", "2025-12-25", "2025-12-26",
            "2026-01-01", "2026-04-03", "2026-04-06", "2026-05-01", "2026-12-25", "2026-12-26",
            "2027-01-01", "2027-03-26", "2027-03-29", "2027-05-01", "2027-12-25", "2027-12-26",
            "2028-01-01", "2028-05-01", "2028-12-25", "2028-12-26",
            "2029-01-01", "2029-05-01", "2029-12-25", "2029-12-26",
            "2030-01-01", "2030-05-01", "2030-12-25", "2030-12-26",
        ],
    ),
}


def resolve_calendar(name: str, extra_holidays: Iterable[str] = ()) -> Calendar:
    """Resolve a named synthetic calendar, optionally merging custom holidays."""
    if name not in SYNTHETIC_CALENDARS:
        raise UnsupportedConventionError(
            f"Unsupported calendar: '{name}'",
            details={"calendar": name, "supported": sorted(SYNTHETIC_CALENDARS.keys())},
        )
    base = SYNTHETIC_CALENDARS[name]
    if not extra_holidays:
        return base
    extra = Calendar.from_iso_dates(name, extra_holidays)
    return Calendar(
        name=f"{base.name}+custom",
        holidays=base.holidays | extra.holidays,
        weekend_days=base.weekend_days,
    )
