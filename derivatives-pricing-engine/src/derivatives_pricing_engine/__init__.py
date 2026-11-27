"""Derivatives Pricing Engine public package API."""

from __future__ import annotations

from .config import EngineConfig, load_config
from .contracts import (
    CalendarConvention,
    DayCountConvention,
    ExerciseStyle,
    MarketSnapshot,
    OptionContract,
    OptionType,
    PricingMethod,
    PricingRequest,
    PricingResult,
    RateCompounding,
)
from .errors import (
    ConfigurationError,
    NumericalStabilityError,
    PricingEngineError,
    UnsupportedFeatureError,
    ValidationError,
)
from .service import EngineMetrics, PricingEngine
from .version import CONFIG_VERSION, ENGINE_VERSION, SCHEMA_VERSION

__all__ = [
    "CONFIG_VERSION",
    "CalendarConvention",
    "ConfigurationError",
    "DayCountConvention",
    "ENGINE_VERSION",
    "EngineConfig",
    "EngineMetrics",
    "ExerciseStyle",
    "MarketSnapshot",
    "NumericalStabilityError",
    "OptionContract",
    "OptionType",
    "PricingEngine",
    "PricingEngineError",
    "PricingMethod",
    "PricingRequest",
    "PricingResult",
    "RateCompounding",
    "SCHEMA_VERSION",
    "UnsupportedFeatureError",
    "ValidationError",
    "load_config",
]
