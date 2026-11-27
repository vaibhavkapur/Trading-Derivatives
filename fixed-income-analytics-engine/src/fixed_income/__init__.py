"""Fixed Income Analytics Engine.

Auditable bond cash-flow scheduling, yield curve bootstrapping, bond pricing,
rate sensitivities (duration, convexity, DV01, key-rate DV01), and curve shift
scenario attribution.
"""

from .conventions import (
    BusinessDayConvention,
    Calendar,
    Compounding,
    Currency,
    DayCountConvention,
    InterpolationMethod,
    StubType,
)
from .errors import (
    CalibrationError,
    ConfigurationError,
    ConvergenceError,
    FixedIncomeError,
    ScheduleError,
    UnsupportedConventionError,
    ValidationError,
)

__version__ = "0.1.0"
SCHEMA_VERSION = "1.0.0"

__all__ = [
    "__version__",
    "SCHEMA_VERSION",
    "BusinessDayConvention",
    "Calendar",
    "CalibrationError",
    "Compounding",
    "ConfigurationError",
    "ConvergenceError",
    "Currency",
    "DayCountConvention",
    "FixedIncomeError",
    "InterpolationMethod",
    "ScheduleError",
    "StubType",
    "UnsupportedConventionError",
    "ValidationError",
]
