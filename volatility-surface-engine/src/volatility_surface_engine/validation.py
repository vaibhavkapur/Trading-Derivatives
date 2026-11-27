"""Input schema validation, day-count timestamp conversion, and config loading."""

from __future__ import annotations

import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Union

from volatility_surface_engine.models import (
    ArbitrageConfig,
    CalibrationConfig,
    CleaningConfig,
    ConfigurationError,
    ForwardEstimationConfig,
    InterpolationConfig,
    IVSolverConfig,
    OptionChainSnapshot,
    PricingConventions,
    SABRConfig,
    SCHEMA_VERSION,
    SVIConfig,
    ValidationError,
)

SUPPORTED_DAY_COUNTS = {"ACT/365F": 365.0, "ACT/365.25": 365.25, "ACT/360": 360.0}
SUPPORTED_EXERCISE_STYLES = {"european"}
SUPPORTED_OPTION_TYPES = {"call", "put"}
SUPPORTED_FORWARD_METHODS = {"parity_weighted_median", "supplied_only"}
SUPPORTED_INTERPOLATION_METHODS = {"piecewise_linear_total_variance"}


def parse_iso_timestamp(ts: str, field_name: str = "timestamp") -> datetime:
    """Parse an ISO-8601 timestamp string into a timezone-aware UTC datetime."""
    if not isinstance(ts, str) or not ts.strip():
        raise ValidationError(f"Timestamp '{field_name}' must be a non-empty ISO-8601 string.", field_name=field_name)
    normalized = ts.strip()
    if normalized.endswith("Z"):
        normalized = normalized[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise ValidationError(
            f"Invalid ISO-8601 timestamp for '{field_name}': {ts!r}.",
            field_name=field_name,
        ) from exc
    if dt.tzinfo is None:
        raise ValidationError(
            f"Timestamp '{field_name}' must include an explicit timezone offset or 'Z' (UTC): {ts!r}.",
            field_name=field_name,
        )
    return dt.astimezone(timezone.utc)


def compute_year_fraction(valuation_ts: str, expiry_ts: str, day_count: str = "ACT/365F") -> float:
    """Compute year fraction between valuation and expiry timestamps using explicit day-count."""
    if day_count not in SUPPORTED_DAY_COUNTS:
        raise ValidationError(
            f"Unsupported day_count convention '{day_count}'. Supported: {sorted(SUPPORTED_DAY_COUNTS)}.",
            field_name="conventions.day_count",
        )
    t0 = parse_iso_timestamp(valuation_ts, "valuation_timestamp")
    t1 = parse_iso_timestamp(expiry_ts, "expiry_timestamp")
    delta_seconds = (t1 - t0).total_seconds()
    denom_days = SUPPORTED_DAY_COUNTS[day_count]
    return delta_seconds / (denom_days * 86400.0)


def validate_conventions(conventions: PricingConventions) -> None:
    """Validate explicit pricing conventions."""
    if not conventions.currency or len(conventions.currency.strip()) != 3:
        raise ValidationError(
            f"Currency must be a 3-letter ISO code, got {conventions.currency!r}.",
            field_name="conventions.currency",
        )
    if conventions.day_count not in SUPPORTED_DAY_COUNTS:
        raise ValidationError(
            f"Unsupported day_count '{conventions.day_count}'. Supported: {sorted(SUPPORTED_DAY_COUNTS)}.",
            field_name="conventions.day_count",
        )
    if conventions.exercise_style.lower() not in SUPPORTED_EXERCISE_STYLES:
        raise ValidationError(
            f"Unsupported exercise_style '{conventions.exercise_style}'. Only European options are supported.",
            field_name="conventions.exercise_style",
        )


def validate_config(raw: Dict[str, Any]) -> CalibrationConfig:
    """Validate and construct a typed CalibrationConfig from a dictionary."""
    if not isinstance(raw, dict):
        raise ConfigurationError("Configuration must be a JSON object.")

    schema_version = str(raw.get("schema_version", SCHEMA_VERSION))
    if schema_version != SCHEMA_VERSION:
        raise ConfigurationError(
            f"Unsupported config schema_version '{schema_version}'; expected '{SCHEMA_VERSION}'."
        )

    seed = raw.get("seed", 42)
    if not isinstance(seed, int) or isinstance(seed, bool) or seed < 0:
        raise ConfigurationError(f"Config 'seed' must be a non-negative integer, got {seed!r}.")

    try:
        conventions = PricingConventions.from_dict(raw.get("conventions", {}))
        validate_conventions(conventions)
    except ValidationError as exc:
        raise ConfigurationError(f"Invalid conventions in config: {exc.message}", details=exc.details) from exc

    def _build_section(cls_type: Any, key: str) -> Any:
        section = raw.get(key, {})
        if not isinstance(section, dict):
            raise ConfigurationError(f"Config section '{key}' must be an object.")
        allowed = set(cls_type.__dataclass_fields__.keys())
        unknown = set(section.keys()) - allowed
        if unknown:
            raise ConfigurationError(f"Unknown keys in config section '{key}': {sorted(unknown)}.")
        try:
            return cls_type(**section)
        except TypeError as exc:
            raise ConfigurationError(f"Invalid parameters in config section '{key}': {exc}") from exc

    cleaning = _build_section(CleaningConfig, "cleaning")
    fwd = _build_section(ForwardEstimationConfig, "forward_estimation")
    iv_solver = _build_section(IVSolverConfig, "iv_solver")
    svi = _build_section(SVIConfig, "svi")
    sabr = _build_section(SABRConfig, "sabr")
    arb = _build_section(ArbitrageConfig, "arbitrage")
    interp = _build_section(InterpolationConfig, "interpolation")

    # Cross-field domain checks
    if cleaning.max_relative_spread <= 0:
        raise ConfigurationError("cleaning.max_relative_spread must be positive.")
    if cleaning.max_absolute_spread <= 0:
        raise ConfigurationError("cleaning.max_absolute_spread must be positive.")
    if cleaning.min_time_to_expiry_years <= 0:
        raise ConfigurationError("cleaning.min_time_to_expiry_years must be positive.")
    if cleaning.min_vega < 0:
        raise ConfigurationError("cleaning.min_vega must be non-negative.")
    if not (0 < cleaning.min_moneyness_ratio < cleaning.max_moneyness_ratio):
        raise ConfigurationError("cleaning moneyness ratios must satisfy 0 < min < max.")

    if fwd.method not in SUPPORTED_FORWARD_METHODS:
        raise ConfigurationError(
            f"Unsupported forward_estimation.method '{fwd.method}'. Supported: {sorted(SUPPORTED_FORWARD_METHODS)}."
        )
    if fwd.max_forward_deviation_rel <= 0:
        raise ConfigurationError("forward_estimation.max_forward_deviation_rel must be positive.")

    if not (0 < iv_solver.vol_lower < iv_solver.vol_upper):
        raise ConfigurationError("iv_solver bounds must satisfy 0 < vol_lower < vol_upper.")
    if iv_solver.tol <= 0 or iv_solver.max_iter < 10:
        raise ConfigurationError("iv_solver.tol must be positive and max_iter >= 10.")

    if svi.num_restarts < 1 or svi.min_slice_quotes < 3 or svi.spread_weight_floor <= 0:
        raise ConfigurationError("Invalid SVI calibration configuration bounds.")
    if not (0.0 <= sabr.beta <= 1.0) or sabr.num_restarts < 1 or sabr.min_slice_quotes < 3:
        raise ConfigurationError("Invalid SABR calibration configuration bounds (0 <= beta <= 1).")
    if arb.k_grid_points < 11 or arb.t_grid_points < 5:
        raise ConfigurationError("Arbitrage grid must have k_grid_points >= 11 and t_grid_points >= 5.")
    if interp.method not in SUPPORTED_INTERPOLATION_METHODS:
        raise ConfigurationError(
            f"Unsupported interpolation.method '{interp.method}'. Supported: {sorted(SUPPORTED_INTERPOLATION_METHODS)}."
        )

    return CalibrationConfig(
        schema_version=schema_version,
        seed=seed,
        conventions=conventions,
        cleaning=cleaning,
        forward_estimation=fwd,
        iv_solver=iv_solver,
        svi=svi,
        sabr=sabr,
        arbitrage=arb,
        interpolation=interp,
    )


def load_config(path: Union[str, Path]) -> CalibrationConfig:
    """Load and validate a JSON configuration file."""
    cfg_path = Path(path)
    if not cfg_path.exists():
        raise ConfigurationError(f"Configuration file not found: {cfg_path}")
    try:
        raw = json.loads(cfg_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ConfigurationError(f"Malformed JSON in configuration file {cfg_path}: {exc}") from exc
    return validate_config(raw)


def validate_chain_snapshot(snapshot: OptionChainSnapshot) -> Dict[str, float]:
    """Validate an OptionChainSnapshot against schema and domain contracts.

    Returns a mapping from expiry_timestamp -> resolved positive time_to_expiry_years.
    Note: Quote-level market anomalies (crossed spreads, wide spreads, near-zero vega)
    are handled in the quote-cleaning stage rather than failing schema validation, while
    structural/schema errors (missing fields, unknown option type, unknown expiry,
    non-positive forward/discount factor) fail fast here.
    """
    if snapshot.schema_version != SCHEMA_VERSION:
        raise ValidationError(
            f"Unsupported snapshot schema_version '{snapshot.schema_version}'; expected '{SCHEMA_VERSION}'.",
            field_name="schema_version",
        )
    if not snapshot.snapshot_id.strip():
        raise ValidationError("snapshot_id must be non-empty.", field_name="snapshot_id")
    if not snapshot.underlying.strip():
        raise ValidationError("underlying must be non-empty.", field_name="underlying")
    if not math.isfinite(snapshot.spot) or snapshot.spot <= 0.0:
        raise ValidationError(f"spot must be finite and positive, got {snapshot.spot}.", field_name="spot")

    validate_conventions(snapshot.conventions)
    parse_iso_timestamp(snapshot.valuation_timestamp, "valuation_timestamp")

    if not snapshot.expiries:
        raise ValidationError("Option chain snapshot must contain at least one expiry.", field_name="expiries")
    if not snapshot.quotes:
        raise ValidationError("Option chain snapshot must contain at least one quote.", field_name="quotes")

    resolved_times: Dict[str, float] = {}
    for idx, exp in enumerate(snapshot.expiries):
        if exp.expiry_timestamp in resolved_times:
            raise ValidationError(
                f"Duplicate expiry_timestamp '{exp.expiry_timestamp}' in expiries.",
                field_name=f"expiries[{idx}].expiry_timestamp",
            )
        computed_t = compute_year_fraction(
            snapshot.valuation_timestamp, exp.expiry_timestamp, snapshot.conventions.day_count
        )
        if exp.time_to_expiry_years is not None:
            if not math.isfinite(exp.time_to_expiry_years):
                raise ValidationError(
                    f"Non-finite time_to_expiry_years for expiry '{exp.expiry_timestamp}'.",
                    field_name=f"expiries[{idx}].time_to_expiry_years",
                )
            # Ensure supplied T is consistent with timestamps within 1 day tolerance
            if abs(exp.time_to_expiry_years - computed_t) > (1.0 / 365.0) + 1e-6:
                raise ValidationError(
                    f"Supplied time_to_expiry_years ({exp.time_to_expiry_years:.6f}) disagrees with "
                    f"timestamps ({computed_t:.6f}) for expiry '{exp.expiry_timestamp}'.",
                    field_name=f"expiries[{idx}].time_to_expiry_years",
                )
            t_val = exp.time_to_expiry_years
        else:
            t_val = computed_t

        if not math.isfinite(exp.forward) or exp.forward <= 0.0:
            raise ValidationError(
                f"Forward must be finite and positive for expiry '{exp.expiry_timestamp}', got {exp.forward}.",
                field_name=f"expiries[{idx}].forward",
            )
        if not math.isfinite(exp.discount_factor) or not (0.0 < exp.discount_factor <= 1.5):
            raise ValidationError(
                f"Discount factor must be in (0, 1.5] for expiry '{exp.expiry_timestamp}', got {exp.discount_factor}.",
                field_name=f"expiries[{idx}].discount_factor",
            )
        resolved_times[exp.expiry_timestamp] = t_val

    seen_ids = set()
    for idx, q in enumerate(snapshot.quotes):
        if not q.quote_id.strip():
            raise ValidationError(f"Empty quote_id at quotes[{idx}].", field_name=f"quotes[{idx}].quote_id")
        if q.quote_id in seen_ids:
            raise ValidationError(
                f"Duplicate quote_id '{q.quote_id}' at quotes[{idx}].",
                field_name=f"quotes[{idx}].quote_id",
            )
        seen_ids.add(q.quote_id)
        if q.option_type not in SUPPORTED_OPTION_TYPES:
            raise ValidationError(
                f"Unsupported option_type '{q.option_type}' for quote '{q.quote_id}'. Expected 'call' or 'put'.",
                field_name=f"quotes[{idx}].option_type",
            )
        if q.expiry_timestamp not in resolved_times:
            raise ValidationError(
                f"Quote '{q.quote_id}' references unknown expiry_timestamp '{q.expiry_timestamp}'.",
                field_name=f"quotes[{idx}].expiry_timestamp",
            )

    return resolved_times


def load_chain_snapshot(path: Union[str, Path]) -> OptionChainSnapshot:
    """Load an OptionChainSnapshot from a JSON file and validate it."""
    snap_path = Path(path)
    if not snap_path.exists():
        raise ValidationError(f"Input option chain file not found: {snap_path}", field_name="path")
    try:
        raw = json.loads(snap_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValidationError(f"Malformed JSON in option chain file {snap_path}: {exc}", field_name="path") from exc
    snapshot = OptionChainSnapshot.from_dict(raw)
    validate_chain_snapshot(snapshot)
    return snapshot
