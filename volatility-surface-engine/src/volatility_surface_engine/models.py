"""Core domain models, data contracts, configuration schemas, and exceptions."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional, Tuple


SCHEMA_VERSION = "1.0.0"


# ---------------------------------------------------------------------------
# Exception Hierarchy
# ---------------------------------------------------------------------------


class VSEError(Exception):
    """Base exception for all Volatility Surface Engine errors."""

    def __init__(self, message: str, code: str = "VSE_ERROR", details: Optional[Dict[str, Any]] = None) -> None:
        super().__init__(message)
        self.message = message
        self.code = code
        self.details = details or {}


class ConfigurationError(VSEError):
    """Raised when configuration is missing, malformed, or out of valid bounds."""

    def __init__(self, message: str, details: Optional[Dict[str, Any]] = None) -> None:
        super().__init__(message, code="CONFIGURATION_ERROR", details=details)


class ValidationError(VSEError):
    """Raised when input market data violates schema or domain contracts."""

    def __init__(self, message: str, field_name: Optional[str] = None, details: Optional[Dict[str, Any]] = None) -> None:
        merged = dict(details or {})
        if field_name is not None:
            merged["field"] = field_name
        super().__init__(message, code="VALIDATION_ERROR", details=merged)
        self.field_name = field_name


class InputBoundsError(VSEError):
    """Raised when option price, vega, or domain bounds prevent valid IV estimation."""

    def __init__(self, message: str, details: Optional[Dict[str, Any]] = None) -> None:
        super().__init__(message, code="INPUT_BOUNDS_ERROR", details=details)


class CalibrationError(VSEError):
    """Raised when slice or surface calibration cannot proceed or fails."""

    def __init__(self, message: str, details: Optional[Dict[str, Any]] = None) -> None:
        super().__init__(message, code="CALIBRATION_ERROR", details=details)


class ArbitrageViolationError(VSEError):
    """Raised when static arbitrage violations are detected in strict mode."""

    def __init__(self, message: str, violations: Optional[List[Dict[str, Any]]] = None) -> None:
        super().__init__(message, code="ARBITRAGE_VIOLATION", details={"violations": violations or []})
        self.violations = violations or []


class ExtrapolationError(VSEError):
    """Raised when a query falls outside the calibrated strike or maturity domain."""

    def __init__(self, message: str, details: Optional[Dict[str, Any]] = None) -> None:
        super().__init__(message, code="EXTRAPOLATION_REJECTED", details=details)


# ---------------------------------------------------------------------------
# Pricing Conventions & Input Contracts
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PricingConventions:
    """Explicit conventions governing units, currency, calendar, and exercise style."""

    currency: str = "USD"
    day_count: str = "ACT/365F"
    exercise_style: str = "european"
    price_unit: str = "currency_per_unit"
    vol_unit: str = "annualized_decimal"
    time_unit: str = "year_fraction"

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "PricingConventions":
        return cls(
            currency=str(data.get("currency", "USD")),
            day_count=str(data.get("day_count", "ACT/365F")),
            exercise_style=str(data.get("exercise_style", "european")),
            price_unit=str(data.get("price_unit", "currency_per_unit")),
            vol_unit=str(data.get("vol_unit", "annualized_decimal")),
            time_unit=str(data.get("time_unit", "year_fraction")),
        )


@dataclass(frozen=True)
class ExpiryMarketData:
    """Per-expiry market parameters: expiry timestamp, forward, and discount factor."""

    expiry_timestamp: str
    forward: float
    discount_factor: float
    time_to_expiry_years: Optional[float] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ExpiryMarketData":
        t_val = data.get("time_to_expiry_years")
        return cls(
            expiry_timestamp=str(data["expiry_timestamp"]),
            forward=float(data["forward"]),
            discount_factor=float(data["discount_factor"]),
            time_to_expiry_years=None if t_val is None else float(t_val),
        )


@dataclass(frozen=True)
class OptionQuote:
    """Raw timestamped option quote."""

    quote_id: str
    expiry_timestamp: str
    strike: float
    option_type: str  # "call" or "put"
    bid: float
    ask: float
    volume: Optional[int] = None
    open_interest: Optional[int] = None

    @property
    def mid(self) -> float:
        return 0.5 * (self.bid + self.ask)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "OptionQuote":
        return cls(
            quote_id=str(data["quote_id"]),
            expiry_timestamp=str(data["expiry_timestamp"]),
            strike=float(data["strike"]),
            option_type=str(data["option_type"]).lower(),
            bid=float(data["bid"]),
            ask=float(data["ask"]),
            volume=None if data.get("volume") is None else int(data["volume"]),
            open_interest=None if data.get("open_interest") is None else int(data["open_interest"]),
        )


@dataclass(frozen=True)
class OptionChainSnapshot:
    """Canonical input snapshot containing timestamped option chain and market curves."""

    snapshot_id: str
    underlying: str
    valuation_timestamp: str
    spot: float
    expiries: List[ExpiryMarketData]
    quotes: List[OptionQuote]
    conventions: PricingConventions = field(default_factory=PricingConventions)
    schema_version: str = SCHEMA_VERSION

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "snapshot_id": self.snapshot_id,
            "underlying": self.underlying,
            "valuation_timestamp": self.valuation_timestamp,
            "spot": self.spot,
            "conventions": self.conventions.to_dict(),
            "expiries": [e.to_dict() for e in self.expiries],
            "quotes": [q.to_dict() for q in self.quotes],
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "OptionChainSnapshot":
        if not isinstance(data, dict):
            raise ValidationError("Option chain snapshot must be a JSON object.")
        required = ("snapshot_id", "underlying", "valuation_timestamp", "spot", "expiries", "quotes")
        for req in required:
            if req not in data:
                raise ValidationError(f"Missing required field '{req}' in option chain snapshot.", field_name=req)
        conventions = PricingConventions.from_dict(data.get("conventions", {}))
        expiries = [ExpiryMarketData.from_dict(e) for e in data["expiries"]]
        quotes = [OptionQuote.from_dict(q) for q in data["quotes"]]
        return cls(
            schema_version=str(data.get("schema_version", SCHEMA_VERSION)),
            snapshot_id=str(data["snapshot_id"]),
            underlying=str(data["underlying"]),
            valuation_timestamp=str(data["valuation_timestamp"]),
            spot=float(data["spot"]),
            conventions=conventions,
            expiries=expiries,
            quotes=quotes,
        )


# ---------------------------------------------------------------------------
# Configuration Contracts
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CleaningConfig:
    min_bid: float = 0.0
    allow_zero_bid: bool = False
    max_relative_spread: float = 0.35
    max_absolute_spread: float = 25.0
    min_time_to_expiry_years: float = 1.0 / 365.0
    min_vega: float = 1e-4
    min_moneyness_ratio: float = 0.4
    max_moneyness_ratio: float = 2.5
    intrinsic_tolerance: float = 1e-8
    use_otm_wings: bool = True


@dataclass(frozen=True)
class ForwardEstimationConfig:
    method: str = "parity_weighted_median"
    max_forward_deviation_rel: float = 0.05
    min_parity_pairs: int = 1
    prefer_estimated_forward: bool = False


@dataclass(frozen=True)
class IVSolverConfig:
    vol_lower: float = 1e-5
    vol_upper: float = 5.0
    tol: float = 1e-10
    max_iter: int = 100


@dataclass(frozen=True)
class SVIConfig:
    num_restarts: int = 8
    max_nfev: int = 1500
    butterfly_penalty_weight: float = 500.0
    min_slice_quotes: int = 5
    spread_weight_floor: float = 1e-4


@dataclass(frozen=True)
class SABRConfig:
    beta: float = 0.5
    num_restarts: int = 6
    max_nfev: int = 1000
    min_slice_quotes: int = 4


@dataclass(frozen=True)
class ArbitrageConfig:
    k_grid_points: int = 81
    t_grid_points: int = 25
    butterfly_density_tol: float = -1e-6
    butterfly_call_convexity_tol: float = -1e-6
    calendar_variance_tol: float = -1e-8
    reject_on_violation: bool = False


@dataclass(frozen=True)
class InterpolationConfig:
    method: str = "piecewise_linear_total_variance"
    allow_extrapolation_by_default: bool = False
    domain_padding_k: float = 0.0


@dataclass(frozen=True)
class CalibrationConfig:
    schema_version: str = SCHEMA_VERSION
    seed: int = 42
    conventions: PricingConventions = field(default_factory=PricingConventions)
    cleaning: CleaningConfig = field(default_factory=CleaningConfig)
    forward_estimation: ForwardEstimationConfig = field(default_factory=ForwardEstimationConfig)
    iv_solver: IVSolverConfig = field(default_factory=IVSolverConfig)
    svi: SVIConfig = field(default_factory=SVIConfig)
    sabr: SABRConfig = field(default_factory=SABRConfig)
    arbitrage: ArbitrageConfig = field(default_factory=ArbitrageConfig)
    interpolation: InterpolationConfig = field(default_factory=InterpolationConfig)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def content_hash(self) -> str:
        payload = json.dumps(self.to_dict(), sort_keys=True, separators=(",", ":")).encode("utf-8")
        return hashlib.sha256(payload).hexdigest()[:16]


# ---------------------------------------------------------------------------
# Intermediate & Output Contracts
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class QuoteRejection:
    quote_id: str
    expiry_timestamp: str
    strike: float
    option_type: str
    reason: str
    detail: str

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ForwardEstimate:
    expiry_timestamp: str
    time_to_expiry_years: float
    supplied_forward: float
    estimated_forward: Optional[float]
    used_forward: float
    discount_factor: float
    parity_pairs_used: int
    relative_difference: Optional[float]
    source: str
    notes: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ForwardEstimate":
        return cls(**data)


@dataclass(frozen=True)
class ImpliedVolQuote:
    quote_id: str
    expiry_timestamp: str
    time_to_expiry_years: float
    strike: float
    option_type: str
    bid: float
    ask: float
    mid: float
    forward: float
    discount_factor: float
    log_moneyness: float
    implied_vol: float
    total_variance: float
    bid_iv: Optional[float]
    ask_iv: Optional[float]
    iv_spread: float
    vega: float
    weight: float

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ImpliedVolQuote":
        return cls(**data)


@dataclass(frozen=True)
class SVIParameters:
    """Raw SVI total-variance parameterization: w(k) = a + b * (rho*(k-m) + sqrt((k-m)^2 + sigma^2))."""

    a: float
    b: float
    rho: float
    m: float
    sigma: float

    def to_dict(self) -> Dict[str, float]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "SVIParameters":
        return cls(
            a=float(data["a"]),
            b=float(data["b"]),
            rho=float(data["rho"]),
            m=float(data["m"]),
            sigma=float(data["sigma"]),
        )


@dataclass(frozen=True)
class SABRParameters:
    """Hagan (2002) SABR parameters."""

    alpha: float
    beta: float
    rho: float
    nu: float

    def to_dict(self) -> Dict[str, float]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "SABRParameters":
        return cls(
            alpha=float(data["alpha"]),
            beta=float(data["beta"]),
            rho=float(data["rho"]),
            nu=float(data["nu"]),
        )


@dataclass(frozen=True)
class SliceCalibrationResult:
    expiry_timestamp: str
    time_to_expiry_years: float
    forward: float
    discount_factor: float
    k_min: float
    k_max: float
    quote_count: int
    svi_params: SVIParameters
    sabr_params: Optional[SABRParameters]
    svi_weighted_rmse_iv: float
    svi_unweighted_rmse_iv: float
    svi_max_abs_err_iv: float
    sabr_weighted_rmse_iv: Optional[float]
    sabr_unweighted_rmse_iv: Optional[float]
    sabr_max_abs_err_iv: Optional[float]
    svi_converged: bool
    sabr_converged: bool
    svi_nfev: int
    sabr_nfev: int
    fitted_quotes: List[ImpliedVolQuote] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "expiry_timestamp": self.expiry_timestamp,
            "time_to_expiry_years": self.time_to_expiry_years,
            "forward": self.forward,
            "discount_factor": self.discount_factor,
            "k_min": self.k_min,
            "k_max": self.k_max,
            "quote_count": self.quote_count,
            "svi_params": self.svi_params.to_dict(),
            "sabr_params": None if self.sabr_params is None else self.sabr_params.to_dict(),
            "svi_weighted_rmse_iv": self.svi_weighted_rmse_iv,
            "svi_unweighted_rmse_iv": self.svi_unweighted_rmse_iv,
            "svi_max_abs_err_iv": self.svi_max_abs_err_iv,
            "sabr_weighted_rmse_iv": self.sabr_weighted_rmse_iv,
            "sabr_unweighted_rmse_iv": self.sabr_unweighted_rmse_iv,
            "sabr_max_abs_err_iv": self.sabr_max_abs_err_iv,
            "svi_converged": self.svi_converged,
            "sabr_converged": self.sabr_converged,
            "svi_nfev": self.svi_nfev,
            "sabr_nfev": self.sabr_nfev,
            "fitted_quotes": [q.to_dict() for q in self.fitted_quotes],
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "SliceCalibrationResult":
        return cls(
            expiry_timestamp=str(data["expiry_timestamp"]),
            time_to_expiry_years=float(data["time_to_expiry_years"]),
            forward=float(data["forward"]),
            discount_factor=float(data["discount_factor"]),
            k_min=float(data["k_min"]),
            k_max=float(data["k_max"]),
            quote_count=int(data["quote_count"]),
            svi_params=SVIParameters.from_dict(data["svi_params"]),
            sabr_params=None if data.get("sabr_params") is None else SABRParameters.from_dict(data["sabr_params"]),
            svi_weighted_rmse_iv=float(data["svi_weighted_rmse_iv"]),
            svi_unweighted_rmse_iv=float(data["svi_unweighted_rmse_iv"]),
            svi_max_abs_err_iv=float(data["svi_max_abs_err_iv"]),
            sabr_weighted_rmse_iv=None
            if data.get("sabr_weighted_rmse_iv") is None
            else float(data["sabr_weighted_rmse_iv"]),
            sabr_unweighted_rmse_iv=None
            if data.get("sabr_unweighted_rmse_iv") is None
            else float(data["sabr_unweighted_rmse_iv"]),
            sabr_max_abs_err_iv=None
            if data.get("sabr_max_abs_err_iv") is None
            else float(data["sabr_max_abs_err_iv"]),
            svi_converged=bool(data["svi_converged"]),
            sabr_converged=bool(data["sabr_converged"]),
            svi_nfev=int(data["svi_nfev"]),
            sabr_nfev=int(data["sabr_nfev"]),
            fitted_quotes=[ImpliedVolQuote.from_dict(q) for q in data.get("fitted_quotes", [])],
        )


@dataclass(frozen=True)
class ArbitrageViolation:
    kind: str  # "butterfly_density", "butterfly_call_convexity", "calendar_variance"
    time_to_expiry_years: float
    k: float
    magnitude: float
    threshold: float
    detail: str
    expiry_timestamp: Optional[str] = None
    strike: Optional[float] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ArbitrageViolation":
        return cls(**data)


@dataclass(frozen=True)
class ArbitrageCheckResult:
    is_valid: bool
    butterfly_violation_count: int
    calendar_violation_count: int
    min_butterfly_density: float
    min_call_convexity: float
    min_calendar_variance_diff: float
    k_grid_points: int
    t_grid_points: int
    violations: List[ArbitrageViolation] = field(default_factory=list)

    @property
    def total_violations(self) -> int:
        return self.butterfly_violation_count + self.calendar_violation_count

    def to_dict(self) -> Dict[str, Any]:
        return {
            "is_valid": self.is_valid,
            "butterfly_violation_count": self.butterfly_violation_count,
            "calendar_violation_count": self.calendar_violation_count,
            "total_violations": self.total_violations,
            "min_butterfly_density": self.min_butterfly_density,
            "min_call_convexity": self.min_call_convexity,
            "min_calendar_variance_diff": self.min_calendar_variance_diff,
            "k_grid_points": self.k_grid_points,
            "t_grid_points": self.t_grid_points,
            "violations": [v.to_dict() for v in self.violations],
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ArbitrageCheckResult":
        return cls(
            is_valid=bool(data["is_valid"]),
            butterfly_violation_count=int(data["butterfly_violation_count"]),
            calendar_violation_count=int(data["calendar_violation_count"]),
            min_butterfly_density=float(data["min_butterfly_density"]),
            min_call_convexity=float(data["min_call_convexity"]),
            min_calendar_variance_diff=float(data["min_calendar_variance_diff"]),
            k_grid_points=int(data["k_grid_points"]),
            t_grid_points=int(data["t_grid_points"]),
            violations=[ArbitrageViolation.from_dict(v) for v in data.get("violations", [])],
        )


@dataclass(frozen=True)
class CalibrationDiagnostics:
    total_input_quotes: int
    accepted_quotes: int
    rejected_quotes: int
    rejection_counts: Dict[str, int]
    forward_estimates: List[ForwardEstimate]
    overall_svi_weighted_rmse_iv: float
    overall_sabr_weighted_rmse_iv: Optional[float]
    seed: int
    runtime_metadata: Dict[str, Any]
    warnings: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "total_input_quotes": self.total_input_quotes,
            "accepted_quotes": self.accepted_quotes,
            "rejected_quotes": self.rejected_quotes,
            "rejection_counts": dict(self.rejection_counts),
            "forward_estimates": [f.to_dict() for f in self.forward_estimates],
            "overall_svi_weighted_rmse_iv": self.overall_svi_weighted_rmse_iv,
            "overall_sabr_weighted_rmse_iv": self.overall_sabr_weighted_rmse_iv,
            "seed": self.seed,
            "runtime_metadata": dict(self.runtime_metadata),
            "warnings": list(self.warnings),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "CalibrationDiagnostics":
        return cls(
            total_input_quotes=int(data["total_input_quotes"]),
            accepted_quotes=int(data["accepted_quotes"]),
            rejected_quotes=int(data["rejected_quotes"]),
            rejection_counts={str(k): int(v) for k, v in data.get("rejection_counts", {}).items()},
            forward_estimates=[ForwardEstimate.from_dict(f) for f in data.get("forward_estimates", [])],
            overall_svi_weighted_rmse_iv=float(data["overall_svi_weighted_rmse_iv"]),
            overall_sabr_weighted_rmse_iv=None
            if data.get("overall_sabr_weighted_rmse_iv") is None
            else float(data["overall_sabr_weighted_rmse_iv"]),
            seed=int(data["seed"]),
            runtime_metadata=dict(data.get("runtime_metadata", {})),
            warnings=[str(w) for w in data.get("warnings", [])],
        )


@dataclass(frozen=True)
class SurfaceQueryResult:
    underlying: str
    expiry_years: float
    strike: float
    forward: float
    log_moneyness: float
    implied_vol: float
    total_variance: float
    model: str
    extrapolated: bool
    surface_id: str
    surface_valid: bool

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class SurfaceSnapshot:
    surface_id: str
    schema_version: str
    input_snapshot_id: str
    underlying: str
    valuation_timestamp: str
    conventions: PricingConventions
    config_hash: str
    slices: List[SliceCalibrationResult]
    arbitrage_check: ArbitrageCheckResult
    is_valid: bool
    diagnostics: CalibrationDiagnostics
    interpolation_method: str = "piecewise_linear_total_variance"

    def domain_bounds(self) -> Tuple[float, float, float, float]:
        """Return (t_min, t_max, common_k_min, common_k_max) across calibrated slices."""
        t_min = min(s.time_to_expiry_years for s in self.slices)
        t_max = max(s.time_to_expiry_years for s in self.slices)
        k_min = max(s.k_min for s in self.slices)
        k_max = min(s.k_max for s in self.slices)
        return (t_min, t_max, k_min, k_max)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "surface_id": self.surface_id,
            "schema_version": self.schema_version,
            "input_snapshot_id": self.input_snapshot_id,
            "underlying": self.underlying,
            "valuation_timestamp": self.valuation_timestamp,
            "conventions": self.conventions.to_dict(),
            "config_hash": self.config_hash,
            "interpolation_method": self.interpolation_method,
            "is_valid": self.is_valid,
            "slices": [s.to_dict() for s in self.slices],
            "arbitrage_check": self.arbitrage_check.to_dict(),
            "diagnostics": self.diagnostics.to_dict(),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "SurfaceSnapshot":
        return cls(
            surface_id=str(data["surface_id"]),
            schema_version=str(data["schema_version"]),
            input_snapshot_id=str(data["input_snapshot_id"]),
            underlying=str(data["underlying"]),
            valuation_timestamp=str(data["valuation_timestamp"]),
            conventions=PricingConventions.from_dict(data["conventions"]),
            config_hash=str(data["config_hash"]),
            interpolation_method=str(data.get("interpolation_method", "piecewise_linear_total_variance")),
            is_valid=bool(data["is_valid"]),
            slices=[SliceCalibrationResult.from_dict(s) for s in data["slices"]],
            arbitrage_check=ArbitrageCheckResult.from_dict(data["arbitrage_check"]),
            diagnostics=CalibrationDiagnostics.from_dict(data["diagnostics"]),
        )
