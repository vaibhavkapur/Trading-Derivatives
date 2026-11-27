"""Typed domain and operational exceptions for the derivatives pricing engine."""

from __future__ import annotations

from typing import Any, Optional


class PricingEngineError(Exception):
    """Base exception for all pricing engine errors."""

    error_code: str = "PRICING_ENGINE_ERROR"

    def __init__(self, message: str, details: Optional[dict[str, Any]] = None) -> None:
        super().__init__(message)
        self.message = message
        self.details = details or {}

    def to_dict(self) -> dict[str, Any]:
        return {
            "error_code": self.error_code,
            "message": self.message,
            "details": self.details,
        }


class ValidationError(PricingEngineError):
    """Raised when contract, market, or pricing request inputs violate schema or domain rules."""

    error_code: str = "VALIDATION_ERROR"


class UnsupportedFeatureError(PricingEngineError):
    """Raised when an exercise style, instrument feature, or method combination is unsupported."""

    error_code: str = "UNSUPPORTED_FEATURE"


class NumericalStabilityError(PricingEngineError):
    """Raised when a numerical solver detects instability or fails to converge."""

    error_code: str = "NUMERICAL_STABILITY_ERROR"


class ConfigurationError(PricingEngineError):
    """Raised when required configuration is missing, unreadable, or malformed."""

    error_code: str = "CONFIGURATION_ERROR"
