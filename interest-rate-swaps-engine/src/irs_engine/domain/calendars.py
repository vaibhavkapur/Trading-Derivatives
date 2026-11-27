"""Holiday calendars and ISDA business day adjustment rules."""

from __future__ import annotations

import calendar as py_calendar
import json
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

from irs_engine.domain.enums import BusinessDayConvention
from irs_engine.domain.errors import CalendarError


@dataclass(frozen=True)
class BusinessCalendar:
    """Deterministic business day calendar."""

    name: str
    weekends: frozenset[int]  # 0=Monday .. 6=Sunday
    holidays: frozenset[date]

    def is_business_day(self, d: date) -> bool:
        return d.weekday() not in self.weekends and d not in self.holidays

    def adjust(self, d: date, convention: BusinessDayConvention) -> date:
        if convention == BusinessDayConvention.UNADJUSTED:
            return d

        if convention == BusinessDayConvention.FOLLOWING:
            curr = d
            while not self.is_business_day(curr):
                curr += timedelta(days=1)
            return curr

        if convention == BusinessDayConvention.PRECEDING:
            curr = d
            while not self.is_business_day(curr):
                curr -= timedelta(days=1)
            return curr

        if convention == BusinessDayConvention.MODIFIED_FOLLOWING:
            adj = self.adjust(d, BusinessDayConvention.FOLLOWING)
            if adj.month != d.month:
                return self.adjust(d, BusinessDayConvention.PRECEDING)
            return adj

        if convention == BusinessDayConvention.MODIFIED_PRECEDING:
            adj = self.adjust(d, BusinessDayConvention.PRECEDING)
            if adj.month != d.month:
                return self.adjust(d, BusinessDayConvention.FOLLOWING)
            return adj

        raise CalendarError(
            f"Unsupported business day convention: {convention}",
            details={"convention": str(convention)},
        )

    def add_business_days(self, d: date, n_days: int) -> date:
        """Shift date by signed number of business days."""
        if n_days == 0:
            return d
        step = 1 if n_days > 0 else -1
        remaining = abs(n_days)
        curr = d
        while remaining > 0:
            curr += timedelta(days=step)
            if self.is_business_day(curr):
                remaining -= 1
        return curr


def last_day_of_month(year: int, month: int) -> date:
    return date(year, month, py_calendar.monthrange(year, month)[1])


def is_end_of_month(d: date) -> bool:
    return d.day == py_calendar.monthrange(d.year, d.month)[1]


def add_months(d: date, months: int, *, end_of_month: bool = False) -> date:
    """Add (or subtract) calendar months with optional EOM roll preservation."""
    total_months = (d.year * 12 + (d.month - 1)) + months
    target_year, target_month_zero = divmod(total_months, 12)
    target_month = target_month_zero + 1
    max_day = py_calendar.monthrange(target_year, target_month)[1]
    if end_of_month:
        target_day = max_day
    else:
        target_day = min(d.day, max_day)
    return date(target_year, target_month, target_day)


class CalendarRegistry:
    """Registry of named holiday calendars loaded from config or built-in defaults."""

    def __init__(self, calendars: dict[str, BusinessCalendar] | None = None) -> None:
        self._calendars: dict[str, BusinessCalendar] = dict(calendars or {})
        # Always ensure NONE and WEEKEND_ONLY exist
        self._calendars.setdefault(
            "NONE",
            BusinessCalendar(name="NONE", weekends=frozenset(), holidays=frozenset()),
        )
        self._calendars.setdefault(
            "WEEKEND_ONLY",
            BusinessCalendar(name="WEEKEND_ONLY", weekends=frozenset({5, 6}), holidays=frozenset()),
        )

    @classmethod
    def from_json_file(cls, path: Path) -> CalendarRegistry:
        if not path.exists():
            raise CalendarError(
                f"Calendar config file not found: {path}",
                details={"path": str(path)},
            )
        raw = json.loads(path.read_text(encoding="utf-8"))
        cal_map: dict[str, BusinessCalendar] = {}
        for name, spec in raw.get("calendars", {}).items():
            weekends = frozenset(int(w) for w in spec.get("weekends", []))
            holidays = frozenset(date.fromisoformat(h) for h in spec.get("holidays", []))
            cal_map[name] = BusinessCalendar(name=name, weekends=weekends, holidays=holidays)
        return cls(cal_map)

    @classmethod
    def default(cls) -> CalendarRegistry:
        default_path = Path(__file__).resolve().parents[3] / "config" / "calendars.json"
        if default_path.exists():
            return cls.from_json_file(default_path)
        return cls()

    def get(self, name: str) -> BusinessCalendar:
        if name not in self._calendars:
            raise CalendarError(
                f"Unknown calendar '{name}'. Available calendars: {sorted(self._calendars.keys())}",
                details={"calendar": name, "available": sorted(self._calendars.keys())},
            )
        return self._calendars[name]

    def list_names(self) -> list[str]:
        return sorted(self._calendars.keys())
