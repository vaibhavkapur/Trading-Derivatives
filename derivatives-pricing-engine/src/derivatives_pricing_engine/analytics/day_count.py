"""Explicit day-count fraction, discounting, and payoff conventions."""

from __future__ import annotations

import math
from datetime import datetime, timezone

from ..contracts import DayCountConvention, OptionType
from ..errors import UnsupportedFeatureError, ValidationError

SECONDS_PER_DAY = 86400.0


def year_fraction(
    start_utc: datetime,
    end_utc: datetime,
    convention: DayCountConvention = DayCountConvention.ACT_365F,
) -> float:
    """Compute non-negative year fraction between two UTC timestamps."""
    if start_utc.tzinfo is None or end_utc.tzinfo is None:
        raise ValidationError("Timestamps passed to year_fraction must be timezone-aware.")

    s_utc = start_utc.astimezone(timezone.utc)
    e_utc = end_utc.astimezone(timezone.utc)
    delta_seconds = (e_utc - s_utc).total_seconds()
    if delta_seconds < 0.0:
        raise ValidationError(
            "end_utc must be greater than or equal to start_utc.",
            details={"start_utc": s_utc.isoformat(), "end_utc": e_utc.isoformat()},
        )
    if delta_seconds == 0.0:
        return 0.0

    if convention == DayCountConvention.ACT_365F:
        return delta_seconds / (365.0 * SECONDS_PER_DAY)
    if convention == DayCountConvention.ACT_360:
        return delta_seconds / (360.0 * SECONDS_PER_DAY)
    if convention == DayCountConvention.ACT_365_25:
        return delta_seconds / (365.25 * SECONDS_PER_DAY)
    if convention == DayCountConvention.THIRTY_360:
        d1 = min(s_utc.day, 30)
        d2 = 30 if (e_utc.day == 31 and d1 == 30) else e_utc.day
        whole_days = 360 * (e_utc.year - s_utc.year) + 30 * (e_utc.month - s_utc.month) + (d2 - d1)
        sec1 = s_utc.hour * 3600.0 + s_utc.minute * 60.0 + s_utc.second + s_utc.microsecond / 1e6
        sec2 = e_utc.hour * 3600.0 + e_utc.minute * 60.0 + e_utc.second + e_utc.microsecond / 1e6
        frac_day = (sec2 - sec1) / SECONDS_PER_DAY
        return max(0.0, (whole_days + frac_day) / 360.0)

    raise UnsupportedFeatureError(
        f"Unsupported day count convention: {convention}",
        details={"convention": str(convention)},
    )


def discount_factor(rate: float, time_to_maturity: float) -> float:
    """Continuous-compounding discount factor exp(-r * T)."""
    if time_to_maturity < 0.0:
        raise ValidationError("time_to_maturity must be non-negative.")
    return math.exp(-rate * time_to_maturity)


def intrinsic_payoff(spot: float, strike: float, option_type: OptionType) -> float:
    """Immediate exercise intrinsic payoff for a vanilla call or put."""
    if option_type == OptionType.CALL:
        return max(spot - strike, 0.0)
    if option_type == OptionType.PUT:
        return max(strike - spot, 0.0)
    raise UnsupportedFeatureError(f"Unsupported option type: {option_type}")
