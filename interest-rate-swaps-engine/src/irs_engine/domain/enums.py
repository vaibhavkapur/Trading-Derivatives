"""Canonical enumerations for IRS conventions, curves, schedules, and policies."""

from __future__ import annotations

from enum import Enum


class SwapDirection(str, Enum):
    """Payer pays fixed / receives floating; Receiver receives fixed / pays floating."""

    PAYER = "PAYER"
    RECEIVER = "RECEIVER"


class DayCountConvention(str, Enum):
    """Supported interest rate day-count conventions."""

    ACT_360 = "ACT/360"
    ACT_365F = "ACT/365F"
    THIRTY_360_US = "30/360"
    THIRTY_E_360 = "30E/360"
    ACT_ACT_ISDA = "ACT/ACT"


class BusinessDayConvention(str, Enum):
    """ISDA business day adjustment rules."""

    UNADJUSTED = "UNADJUSTED"
    FOLLOWING = "FOLLOWING"
    MODIFIED_FOLLOWING = "MODIFIED_FOLLOWING"
    PRECEDING = "PRECEDING"
    MODIFIED_PRECEDING = "MODIFIED_PRECEDING"


class Frequency(str, Enum):
    """Payment/reset coupon frequencies."""

    ANNUAL = "ANNUAL"
    SEMI_ANNUAL = "SEMI_ANNUAL"
    QUARTERLY = "QUARTERLY"
    MONTHLY = "MONTHLY"

    @property
    def months(self) -> int:
        return {
            Frequency.ANNUAL: 12,
            Frequency.SEMI_ANNUAL: 6,
            Frequency.QUARTERLY: 3,
            Frequency.MONTHLY: 1,
        }[self]

    @property
    def periods_per_year(self) -> int:
        return 12 // self.months


class StubType(str, Enum):
    """Schedule stub handling rules when tenor is not an integer multiple of frequency."""

    NONE = "NONE"
    FRONT_SHORT = "FRONT_SHORT"
    FRONT_LONG = "FRONT_LONG"
    BACK_SHORT = "BACK_SHORT"
    BACK_LONG = "BACK_LONG"


class CurveRole(str, Enum):
    """Explicit separation between discounting and forward-rate projection curves."""

    DISCOUNT = "DISCOUNT"
    FORWARD = "FORWARD"


class Compounding(str, Enum):
    """Zero-rate compounding basis on curve pillars."""

    CONTINUOUS = "CONTINUOUS"
    ANNUAL = "ANNUAL"
    SIMPLE = "SIMPLE"


class InterpolationMethod(str, Enum):
    """Curve interpolation method."""

    LINEAR_ZERO = "LINEAR_ZERO"
    LOG_LINEAR_DF = "LOG_LINEAR_DF"
    CUBIC_SPLINE_ZERO = "CUBIC_SPLINE_ZERO"


class FixingSource(str, Enum):
    """Provenance of each floating period's index rate."""

    HISTORICAL = "HISTORICAL"
    PROJECTED = "PROJECTED"
    FALLBACK_PROJECTED = "FALLBACK_PROJECTED"
    SETTLED = "SETTLED"


class FixingFallbackPolicy(str, Enum):
    """Behavior when a required historical fixing (fixing_date < valuation_date) is missing."""

    ERROR = "ERROR"
    PROJECT_FALLBACK = "PROJECT_FALLBACK"


class ValuationDateFixingPolicy(str, Enum):
    """Behavior when fixing_date == valuation_date and no historical fixing is provided."""

    ALLOW_PROJECT = "ALLOW_PROJECT"
    REQUIRE_HISTORICAL = "REQUIRE_HISTORICAL"


class FiniteDifferenceScheme(str, Enum):
    """Finite difference bump scheme for PV01 / KR01 sensitivities."""

    CENTRAL = "CENTRAL"
    FORWARD = "FORWARD"
