"""Typed domain error hierarchy with explicit machine-readable error codes."""

from typing import Any, Dict, Optional


class MarginEngineError(Exception):
    """Base exception for all derivatives margin engine errors."""

    def __init__(
        self,
        code: str,
        message: str,
        details: Optional[Dict[str, Any]] = None,
    ) -> None:
        super().__init__(f"[{code}] {message}")
        self.code = code
        self.message = message
        self.details: Dict[str, Any] = details or {}

    def to_dict(self) -> Dict[str, Any]:
        return {
            "error_type": self.__class__.__name__,
            "code": self.code,
            "message": self.message,
            "details": self.details,
        }


class ValidationError(MarginEngineError):
    """Raised when input data, configuration, or schema contracts are violated."""


class DuplicateCollateralError(ValidationError):
    """Raised when a collateral lot is double-counted or allocated twice."""

    def __init__(self, lot_id: str, netting_sets: Any) -> None:
        super().__init__(
            code="DUPLICATE_COLLATERAL_LOT",
            message=f"Collateral lot '{lot_id}' cannot be double-counted across allocations: {netting_sets}",
            details={"lot_id": lot_id, "netting_sets": list(netting_sets)},
        )


class DuplicateMarginCallError(MarginEngineError):
    """Raised when attempting to issue or persist a duplicate margin call."""

    def __init__(self, call_id: str, idempotency_key: str) -> None:
        super().__init__(
            code="DUPLICATE_MARGIN_CALL",
            message=f"Margin call '{call_id}' already exists for key '{idempotency_key}'",
            details={"call_id": call_id, "idempotency_key": idempotency_key},
        )


class SettlementCorrectionError(MarginEngineError):
    """Raised when a settlement price revision/correction violates revision ordering or state."""


class ReconciliationError(MarginEngineError):
    """Raised when journal reconciliation checks fail."""
