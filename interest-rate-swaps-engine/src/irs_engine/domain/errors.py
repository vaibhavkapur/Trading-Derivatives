"""Structured domain error hierarchy with stable machine-readable error codes."""

from __future__ import annotations

from typing import Any


class IRSEngineError(Exception):
    """Base exception for all Interest Rate Swaps Engine domain errors."""

    error_code: str = "IRS_ERROR"
    http_status: int = 400

    def __init__(
        self,
        message: str,
        *,
        error_code: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        if error_code is not None:
            self.error_code = error_code
        self.message = message
        self.details = details or {}

    def to_dict(self) -> dict[str, Any]:
        return {
            "error_code": self.error_code,
            "message": self.message,
            "details": self.details,
        }


class ValidationError(IRSEngineError):
    """Raised when input trade terms, dates, or schemas violate domain invariants."""

    error_code = "VALIDATION_ERROR"
    http_status = 422


class UnsupportedConventionError(IRSEngineError):
    """Raised when an unsupported currency, convention, or feature is requested."""

    error_code = "UNSUPPORTED_CONVENTION"
    http_status = 422


class CalendarError(IRSEngineError):
    """Raised when a requested calendar is unknown or malformed."""

    error_code = "CALENDAR_ERROR"
    http_status = 422


class ScheduleError(IRSEngineError):
    """Raised when payment or reset schedules cannot be constructed validly."""

    error_code = "SCHEDULE_ERROR"
    http_status = 422


class CurveError(IRSEngineError):
    """Raised when curve snapshots are malformed, non-monotonic, or mismatched."""

    error_code = "CURVE_ERROR"
    http_status = 422


class MissingFixingError(IRSEngineError):
    """Raised when a required historical fixing is absent from fixing history."""

    error_code = "MISSING_FIXING"
    http_status = 422


class ConfigurationError(IRSEngineError):
    """Raised when required configuration files or parameters are missing/invalid."""

    error_code = "CONFIGURATION_ERROR"
    http_status = 500
