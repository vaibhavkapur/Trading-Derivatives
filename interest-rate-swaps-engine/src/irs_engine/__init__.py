"""Interest Rate Swaps Engine — dual-curve vanilla IRS valuation, risk, and scenario library."""

from irs_engine.domain.enums import (
    BusinessDayConvention,
    Compounding,
    CurveRole,
    DayCountConvention,
    FiniteDifferenceScheme,
    FixingFallbackPolicy,
    FixingSource,
    Frequency,
    InterpolationMethod,
    StubType,
    SwapDirection,
    ValuationDateFixingPolicy,
)
from irs_engine.domain.errors import (
    CalendarError,
    ConfigurationError,
    CurveError,
    IRSEngineError,
    MissingFixingError,
    ScheduleError,
    UnsupportedConventionError,
    ValidationError,
)

__version__ = "0.1.0"
__schema_version__ = "1.0.0"

__all__ = [
    "__version__",
    "__schema_version__",
    "BusinessDayConvention",
    "Compounding",
    "CurveRole",
    "DayCountConvention",
    "FiniteDifferenceScheme",
    "FixingFallbackPolicy",
    "FixingSource",
    "Frequency",
    "InterpolationMethod",
    "StubType",
    "SwapDirection",
    "ValuationDateFixingPolicy",
    "CalendarError",
    "ConfigurationError",
    "CurveError",
    "IRSEngineError",
    "MissingFixingError",
    "ScheduleError",
    "UnsupportedConventionError",
    "ValidationError",
]
