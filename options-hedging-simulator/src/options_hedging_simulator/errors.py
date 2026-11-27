"""Domain exception hierarchy for options_hedging_simulator."""

from __future__ import annotations

from typing import Optional


class HedgingSimulatorError(Exception):
    """Base error for all options hedging simulator failures."""


class ValidationError(HedgingSimulatorError):
    """Raised when an input contract, parameter, or fixture violates schema rules."""

    def __init__(
        self,
        message: str,
        *,
        field: Optional[str] = None,
        code: str = "VALIDATION_ERROR",
    ) -> None:
        self.field = field
        self.code = code
        prefix = f"[{code}]"
        if field:
            prefix = f"{prefix} {field}:"
        super().__init__(f"{prefix} {message}")


class UnsupportedContractError(ValidationError):
    """Raised when a contract specifies an unsupported option style, currency, or convention."""

    def __init__(self, message: str, *, field: Optional[str] = None) -> None:
        super().__init__(message, field=field, code="UNSUPPORTED_CONTRACT")


class ConfigurationError(HedgingSimulatorError):
    """Raised when configuration files are missing, malformed, or inconsistent."""


class StalePriceError(HedgingSimulatorError):
    """Raised when stale market data is encountered under strict staleness policy."""

    def __init__(self, message: str, *, step: int, staleness_seconds: float) -> None:
        self.step = step
        self.staleness_seconds = staleness_seconds
        super().__init__(message)


class LedgerImbalanceError(HedgingSimulatorError):
    """Raised when the self-financing cash/inventory accounting identity is violated."""


class ReplayMismatchError(HedgingSimulatorError):
    """Raised when replaying a seeded simulation produces non-identical fills or outputs."""
