"""Canonical input and output data contracts with strict validation."""

from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional, Union

from .errors import UnsupportedFeatureError, ValidationError
from .version import ENGINE_VERSION, SCHEMA_VERSION

_ISO_CURRENCY_RE = re.compile(r"^[A-Z]{3}$")


class OptionType(str, Enum):
    CALL = "call"
    PUT = "put"


class ExerciseStyle(str, Enum):
    EUROPEAN = "european"
    AMERICAN = "american"


class DayCountConvention(str, Enum):
    ACT_365F = "ACT/365F"
    ACT_360 = "ACT/360"
    ACT_365_25 = "ACT/365.25"
    THIRTY_360 = "30/360"


class CalendarConvention(str, Enum):
    NONE_365 = "NONE_365"
    US_NYSE = "US_NYSE"
    TARGET = "TARGET"


class PricingMethod(str, Enum):
    BLACK_SCHOLES = "black_scholes"
    BINOMIAL = "binomial"
    MONTE_CARLO = "monte_carlo"
    PDE_FD = "pde_fd"


class RateCompounding(str, Enum):
    CONTINUOUS = "continuous"


def parse_utc_timestamp(value: Union[str, datetime], field_name: str) -> datetime:
    """Parse an ISO-8601 timezone-aware timestamp and normalize to UTC."""
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, str):
        text = value.strip()
        if not text:
            raise ValidationError(
                f"'{field_name}' must be a non-empty ISO-8601 UTC timestamp.",
                details={"field": field_name},
            )
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        try:
            dt = datetime.fromisoformat(text)
        except ValueError as exc:
            raise ValidationError(
                f"'{field_name}' is not a valid ISO-8601 timestamp: {value!r}",
                details={"field": field_name, "value": value},
            ) from exc
    else:
        raise ValidationError(
            f"'{field_name}' must be an ISO-8601 string or timezone-aware datetime.",
            details={"field": field_name, "type": type(value).__name__},
        )

    if dt.tzinfo is None or dt.utcoffset() is None:
        raise ValidationError(
            f"'{field_name}' must include an explicit timezone offset or 'Z' suffix.",
            details={"field": field_name, "value": str(value)},
        )

    return dt.astimezone(timezone.utc)


def format_utc_timestamp(dt: datetime) -> str:
    """Format a timezone-aware datetime as canonical ISO-8601 UTC string."""
    utc_dt = dt.astimezone(timezone.utc)
    return utc_dt.isoformat().replace("+00:00", "Z")


def _require_finite_float(value: Any, field_name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValidationError(
            f"'{field_name}' must be a finite numeric value.",
            details={"field": field_name, "value": value},
        )
    fval = float(value)
    if not math.isfinite(fval):
        raise ValidationError(
            f"'{field_name}' must be finite (got {value}).",
            details={"field": field_name, "value": str(value)},
        )
    return fval


def _parse_enum(enum_cls: type[Enum], value: Any, field_name: str, unsupported_as_feature: bool = False) -> Any:
    if isinstance(value, enum_cls):
        return value
    if not isinstance(value, str):
        raise ValidationError(
            f"'{field_name}' must be a string matching {[e.value for e in enum_cls]}.",
            details={"field": field_name, "value": value},
        )
    normalized = value.strip()
    for member in enum_cls:
        if normalized == member.value or normalized.lower() == member.value.lower():
            return member
    err_cls = UnsupportedFeatureError if unsupported_as_feature else ValidationError
    raise err_cls(
        f"Unsupported {field_name}: '{value}'. Supported values: {[e.value for e in enum_cls]}.",
        details={"field": field_name, "value": value, "supported": [e.value for e in enum_cls]},
    )


@dataclass(frozen=True)
class OptionContract:
    """Vanilla European or American equity option contract specification."""

    contract_id: str
    underlying: str
    currency: str
    option_type: OptionType
    exercise_style: ExerciseStyle
    strike: float
    expiry_timestamp: datetime
    contract_multiplier: float = 1.0
    day_count: DayCountConvention = DayCountConvention.ACT_365F
    calendar: CalendarConvention = CalendarConvention.NONE_365

    def __post_init__(self) -> None:
        if not isinstance(self.contract_id, str) or not self.contract_id.strip():
            raise ValidationError("'contract_id' must be a non-empty string.", details={"field": "contract_id"})
        if not isinstance(self.underlying, str) or not self.underlying.strip():
            raise ValidationError("'underlying' must be a non-empty string.", details={"field": "underlying"})
        if not isinstance(self.currency, str) or not _ISO_CURRENCY_RE.match(self.currency):
            raise ValidationError(
                "'currency' must be a 3-letter uppercase ISO-4217 code (e.g., 'USD').",
                details={"field": "currency", "value": self.currency},
            )

        option_type = _parse_enum(OptionType, self.option_type, "option_type", unsupported_as_feature=True)
        exercise_style = _parse_enum(ExerciseStyle, self.exercise_style, "exercise_style", unsupported_as_feature=True)
        day_count = _parse_enum(DayCountConvention, self.day_count, "day_count", unsupported_as_feature=True)
        calendar = _parse_enum(CalendarConvention, self.calendar, "calendar", unsupported_as_feature=True)

        strike = _require_finite_float(self.strike, "strike")
        if strike <= 0.0:
            raise ValidationError("'strike' must be strictly positive (> 0).", details={"field": "strike", "value": strike})

        multiplier = _require_finite_float(self.contract_multiplier, "contract_multiplier")
        if multiplier <= 0.0:
            raise ValidationError(
                "'contract_multiplier' must be strictly positive (> 0).",
                details={"field": "contract_multiplier", "value": multiplier},
            )

        expiry_utc = parse_utc_timestamp(self.expiry_timestamp, "expiry_timestamp")

        object.__setattr__(self, "option_type", option_type)
        object.__setattr__(self, "exercise_style", exercise_style)
        object.__setattr__(self, "day_count", day_count)
        object.__setattr__(self, "calendar", calendar)
        object.__setattr__(self, "strike", strike)
        object.__setattr__(self, "contract_multiplier", multiplier)
        object.__setattr__(self, "expiry_timestamp", expiry_utc)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "OptionContract":
        if not isinstance(data, dict):
            raise ValidationError("Contract payload must be a JSON object.")
        required = ("contract_id", "underlying", "currency", "option_type", "exercise_style", "strike", "expiry_timestamp")
        missing = [k for k in required if k not in data]
        if missing:
            raise ValidationError(f"Contract missing required fields: {missing}", details={"missing": missing})
        return cls(
            contract_id=data["contract_id"],
            underlying=data["underlying"],
            currency=data["currency"],
            option_type=data["option_type"],
            exercise_style=data["exercise_style"],
            strike=data["strike"],
            expiry_timestamp=data["expiry_timestamp"],
            contract_multiplier=data.get("contract_multiplier", 1.0),
            day_count=data.get("day_count", DayCountConvention.ACT_365F.value),
            calendar=data.get("calendar", CalendarConvention.NONE_365.value),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "contract_id": self.contract_id,
            "underlying": self.underlying,
            "currency": self.currency,
            "option_type": self.option_type.value,
            "exercise_style": self.exercise_style.value,
            "strike": self.strike,
            "expiry_timestamp": format_utc_timestamp(self.expiry_timestamp),
            "contract_multiplier": self.contract_multiplier,
            "day_count": self.day_count.value,
            "calendar": self.calendar.value,
        }


@dataclass(frozen=True)
class MarketSnapshot:
    """Point-in-time flat-parameter synthetic market snapshot."""

    snapshot_id: str
    valuation_timestamp: datetime
    spot: float
    risk_free_rate: float
    dividend_yield: float
    volatility: float
    rate_compounding: RateCompounding = RateCompounding.CONTINUOUS

    def __post_init__(self) -> None:
        if not isinstance(self.snapshot_id, str) or not self.snapshot_id.strip():
            raise ValidationError("'snapshot_id' must be a non-empty string.", details={"field": "snapshot_id"})

        val_utc = parse_utc_timestamp(self.valuation_timestamp, "valuation_timestamp")
        spot = _require_finite_float(self.spot, "spot")
        if spot <= 0.0:
            raise ValidationError("'spot' must be strictly positive (> 0).", details={"field": "spot", "value": spot})

        rate = _require_finite_float(self.risk_free_rate, "risk_free_rate")
        if rate < -0.5 or rate > 2.0:
            raise ValidationError(
                "'risk_free_rate' is outside supported bounds [-0.5, 2.0].",
                details={"field": "risk_free_rate", "value": rate},
            )

        div_yield = _require_finite_float(self.dividend_yield, "dividend_yield")
        if div_yield < 0.0 or div_yield > 2.0:
            raise ValidationError(
                "'dividend_yield' must be in [0.0, 2.0].",
                details={"field": "dividend_yield", "value": div_yield},
            )

        vol = _require_finite_float(self.volatility, "volatility")
        if vol < 0.0 or vol > 10.0:
            raise ValidationError(
                "'volatility' must be in [0.0, 10.0].",
                details={"field": "volatility", "value": vol},
            )

        compounding = _parse_enum(RateCompounding, self.rate_compounding, "rate_compounding", unsupported_as_feature=True)

        object.__setattr__(self, "valuation_timestamp", val_utc)
        object.__setattr__(self, "spot", spot)
        object.__setattr__(self, "risk_free_rate", rate)
        object.__setattr__(self, "dividend_yield", div_yield)
        object.__setattr__(self, "volatility", vol)
        object.__setattr__(self, "rate_compounding", compounding)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "MarketSnapshot":
        if not isinstance(data, dict):
            raise ValidationError("Market snapshot payload must be a JSON object.")
        required = ("snapshot_id", "valuation_timestamp", "spot", "risk_free_rate", "dividend_yield", "volatility")
        missing = [k for k in required if k not in data]
        if missing:
            raise ValidationError(f"Market snapshot missing required fields: {missing}", details={"missing": missing})
        return cls(
            snapshot_id=data["snapshot_id"],
            valuation_timestamp=data["valuation_timestamp"],
            spot=data["spot"],
            risk_free_rate=data["risk_free_rate"],
            dividend_yield=data["dividend_yield"],
            volatility=data["volatility"],
            rate_compounding=data.get("rate_compounding", RateCompounding.CONTINUOUS.value),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "snapshot_id": self.snapshot_id,
            "valuation_timestamp": format_utc_timestamp(self.valuation_timestamp),
            "spot": self.spot,
            "risk_free_rate": self.risk_free_rate,
            "dividend_yield": self.dividend_yield,
            "volatility": self.volatility,
            "rate_compounding": self.rate_compounding.value,
        }


@dataclass(frozen=True)
class PricingRequest:
    """Versioned pricing request combining contract, market snapshot, and numerical method."""

    contract: OptionContract
    market: MarketSnapshot
    method: PricingMethod
    method_params: dict[str, Any] = field(default_factory=dict)
    request_id: str = ""
    schema_version: str = SCHEMA_VERSION

    def __post_init__(self) -> None:
        if not isinstance(self.contract, OptionContract):
            raise ValidationError("'contract' must be an OptionContract instance.")
        if not isinstance(self.market, MarketSnapshot):
            raise ValidationError("'market' must be a MarketSnapshot instance.")
        method = _parse_enum(PricingMethod, self.method, "method", unsupported_as_feature=True)
        if not isinstance(self.method_params, dict):
            raise ValidationError("'method_params' must be a dictionary.")
        if self.schema_version != SCHEMA_VERSION:
            raise ValidationError(
                f"Unsupported schema_version '{self.schema_version}', expected '{SCHEMA_VERSION}'.",
                details={"actual": self.schema_version, "expected": SCHEMA_VERSION},
            )
        if self.contract.expiry_timestamp < self.market.valuation_timestamp:
            raise ValidationError(
                "Option contract has already expired (expiry_timestamp < valuation_timestamp).",
                details={
                    "expiry_timestamp": format_utc_timestamp(self.contract.expiry_timestamp),
                    "valuation_timestamp": format_utc_timestamp(self.market.valuation_timestamp),
                },
            )

        object.__setattr__(self, "method", method)
        object.__setattr__(self, "method_params", dict(self.method_params))

        if not self.request_id:
            req_id = compute_stable_id("req", self._canonical_payload())
            object.__setattr__(self, "request_id", req_id)

    def _canonical_payload(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "contract": self.contract.to_dict(),
            "market": self.market.to_dict(),
            "method": self.method.value,
            "method_params": self.method_params,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "PricingRequest":
        if not isinstance(data, dict):
            raise ValidationError("PricingRequest payload must be a JSON object.")
        required = ("contract", "market", "method")
        missing = [k for k in required if k not in data]
        if missing:
            raise ValidationError(f"PricingRequest missing required fields: {missing}", details={"missing": missing})
        return cls(
            contract=OptionContract.from_dict(data["contract"]),
            market=MarketSnapshot.from_dict(data["market"]),
            method=data["method"],
            method_params=data.get("method_params", {}),
            request_id=data.get("request_id", ""),
            schema_version=data.get("schema_version", SCHEMA_VERSION),
        )

    def to_dict(self) -> dict[str, Any]:
        payload = self._canonical_payload()
        payload["request_id"] = self.request_id
        return payload


@dataclass(frozen=True)
class PricingResult:
    """Canonical versioned pricing response."""

    result_id: str
    request_id: str
    schema_version: str
    engine_version: str
    contract_id: str
    snapshot_id: str
    method: str
    currency: str
    unit_price: float
    total_price: float
    intrinsic_value: float
    time_value: float
    time_to_maturity: float
    discount_factor: float
    greeks: dict[str, Optional[float]]
    diagnostics: dict[str, Any]
    metadata: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "result_id": self.result_id,
            "request_id": self.request_id,
            "schema_version": self.schema_version,
            "engine_version": self.engine_version,
            "contract_id": self.contract_id,
            "snapshot_id": self.snapshot_id,
            "method": self.method,
            "currency": self.currency,
            "unit_price": self.unit_price,
            "total_price": self.total_price,
            "intrinsic_value": self.intrinsic_value,
            "time_value": self.time_value,
            "time_to_maturity": self.time_to_maturity,
            "discount_factor": self.discount_factor,
            "greeks": self.greeks,
            "diagnostics": self.diagnostics,
            "metadata": self.metadata,
        }


def compute_stable_id(prefix: str, payload: dict[str, Any]) -> str:
    """Compute a deterministic content-addressed identifier from canonical JSON."""
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]
    return f"{prefix}-{digest}"


def compute_result_id(request: PricingRequest, effective_params: dict[str, Any]) -> str:
    """Compute a deterministic result identifier stable across reruns of identical inputs."""
    payload = {
        "schema_version": SCHEMA_VERSION,
        "engine_version": ENGINE_VERSION,
        "contract": request.contract.to_dict(),
        "market": request.market.to_dict(),
        "method": request.method.value,
        "effective_params": effective_params,
    }
    return compute_stable_id("res", payload)
