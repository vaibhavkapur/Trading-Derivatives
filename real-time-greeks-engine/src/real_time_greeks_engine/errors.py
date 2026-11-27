"""Domain exceptions for the Real-Time Greeks Engine."""

from __future__ import annotations


class GreeksEngineError(Exception):
    """Base exception for all Real-Time Greeks Engine domain errors."""

    error_code: str = "GREEKS_ENGINE_ERROR"

    def __init__(self, message: str, *, details: dict | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.details = details or {}


class ValidationError(GreeksEngineError):
    """Raised when input schemas, numeric bounds, or timestamps are invalid."""

    error_code = "VALIDATION_ERROR"


class UnsupportedInstrumentError(GreeksEngineError):
    """Raised when an instrument style, day count, or pricing feature is unsupported."""

    error_code = "UNSUPPORTED_INSTRUMENT"


class MissingMarketDataError(GreeksEngineError):
    """Raised when required spot, curve, or volatility surface data is absent."""

    error_code = "MISSING_MARKET_DATA"


class StaleMarketDataError(GreeksEngineError):
    """Raised in strict mode when market data exceeds its staleness TTL."""

    error_code = "STALE_MARKET_DATA"


class SnapshotVersionConflictError(GreeksEngineError):
    """Raised when an optimistic concurrency check detects a snapshot version race."""

    error_code = "SNAPSHOT_VERSION_CONFLICT"


class ConfigurationError(GreeksEngineError):
    """Raised when engine configuration is missing, malformed, or out of range."""

    error_code = "CONFIGURATION_ERROR"
