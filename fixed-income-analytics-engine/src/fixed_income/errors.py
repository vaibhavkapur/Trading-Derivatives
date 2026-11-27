"""Domain exception hierarchy for the Fixed Income Analytics Engine."""

from __future__ import annotations

from typing import Any, Dict, Optional


class FixedIncomeError(Exception):
    """Base error for all fixed-income analytics failures."""

    code: str = "FI_ERROR"

    def __init__(self, message: str, *, details: Optional[Dict[str, Any]] = None) -> None:
        super().__init__(message)
        self.message = message
        self.details: Dict[str, Any] = details or {}

    def to_dict(self) -> Dict[str, Any]:
        return {
            "error_code": self.code,
            "error_type": self.__class__.__name__,
            "message": self.message,
            "details": self.details,
        }


class ValidationError(FixedIncomeError):
    """Raised when input data violates schema or financial invariants."""

    code = "FI_VALIDATION_ERROR"


class UnsupportedConventionError(FixedIncomeError):
    """Raised when a requested market convention or method is unsupported."""

    code = "FI_UNSUPPORTED_CONVENTION"


class ConfigurationError(FixedIncomeError):
    """Raised when configuration is missing, unreadable, or malformed."""

    code = "FI_CONFIGURATION_ERROR"


class ScheduleError(FixedIncomeError):
    """Raised when a cash-flow schedule cannot be constructed cleanly."""

    code = "FI_SCHEDULE_ERROR"


class CalibrationError(FixedIncomeError):
    """Raised when yield curve bootstrapping fails or violates invariants."""

    code = "FI_CALIBRATION_ERROR"


class ConvergenceError(FixedIncomeError):
    """Raised when an iterative numerical solver fails to converge."""

    code = "FI_CONVERGENCE_ERROR"
