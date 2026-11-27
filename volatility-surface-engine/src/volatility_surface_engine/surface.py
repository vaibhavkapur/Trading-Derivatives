"""Volatility surface orchestration, interpolation, strict query bounds, snapshots, and evaluation."""

from __future__ import annotations

import hashlib
import json
import math
import platform
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

import numpy as np
import scipy

from volatility_surface_engine.arbitrage import check_surface_arbitrage, interpolate_slice_variance_and_derivs
from volatility_surface_engine.cleaning import clean_and_invert_quotes
from volatility_surface_engine.models import (
    ArbitrageViolationError,
    CalibrationConfig,
    CalibrationDiagnostics,
    CalibrationError,
    ExtrapolationError,
    ImpliedVolQuote,
    OptionChainSnapshot,
    SCHEMA_VERSION,
    SliceCalibrationResult,
    SurfaceQueryResult,
    SurfaceSnapshot,
)
from volatility_surface_engine.sabr import fit_sabr_slice, sabr_implied_vol_scalar
from volatility_surface_engine.svi import fit_svi_slice, svi_total_variance


def _collect_runtime_metadata() -> Dict[str, Any]:
    meta: Dict[str, Any] = {
        "python_version": sys.version.split()[0],
        "platform": platform.platform(),
        "numpy_version": np.__version__,
        "scipy_version": scipy.__version__,
    }
    try:
        import polars as pl

        meta["polars_version"] = pl.__version__
    except ImportError:
        meta["polars_version"] = None
    try:
        import plotly

        meta["plotly_version"] = plotly.__version__
    except ImportError:
        meta["plotly_version"] = None
    return meta


def _compute_deterministic_surface_id(
    input_snapshot_id: str,
    config_hash: str,
    slices: List[SliceCalibrationResult],
    is_valid: bool,
) -> str:
    """Derive a stable, reproducible surface identifier from canonical logical outputs."""
    canonical = {
        "input_snapshot_id": input_snapshot_id,
        "config_hash": config_hash,
        "is_valid": is_valid,
        "slices": [
            {
                "expiry_timestamp": s.expiry_timestamp,
                "t": round(s.time_to_expiry_years, 10),
                "forward": round(s.forward, 8),
                "svi": {k: round(v, 10) for k, v in s.svi_params.to_dict().items()},
            }
            for s in slices
        ],
    }
    digest = hashlib.sha256(json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
    return f"vse-{digest[:12]}"


class VolatilitySurface:
    """Calibrated implied-volatility surface supporting arbitrage-aware queries and versioned snapshots."""

    def __init__(self, snapshot: SurfaceSnapshot) -> None:
        if not snapshot.slices:
            raise CalibrationError("Cannot instantiate VolatilitySurface with zero calibrated slices.")
        self.snapshot = snapshot
        self._ordered_slices = sorted(snapshot.slices, key=lambda s: s.time_to_expiry_years)

    @property
    def surface_id(self) -> str:
        return self.snapshot.surface_id

    @property
    def is_valid(self) -> bool:
        return self.snapshot.is_valid

    @property
    def slices(self) -> List[SliceCalibrationResult]:
        return list(self._ordered_slices)

    def _bracketing_slices(self, expiry_years: float) -> Tuple[SliceCalibrationResult, SliceCalibrationResult, float]:
        """Locate bracketing slices and interpolation weight lambda in [0, 1]."""
        ordered = self._ordered_slices
        t_min = ordered[0].time_to_expiry_years
        t_max = ordered[-1].time_to_expiry_years

        if expiry_years <= t_min:
            return ordered[0], ordered[0], 0.0
        if expiry_years >= t_max:
            return ordered[-1], ordered[-1], 0.0

        for i in range(len(ordered) - 1):
            s0 = ordered[i]
            s1 = ordered[i + 1]
            if s0.time_to_expiry_years <= expiry_years <= s1.time_to_expiry_years:
                if abs(expiry_years - s0.time_to_expiry_years) <= 1e-12:
                    return s0, s0, 0.0
                if abs(expiry_years - s1.time_to_expiry_years) <= 1e-12:
                    return s1, s1, 0.0
                lam = (expiry_years - s0.time_to_expiry_years) / (s1.time_to_expiry_years - s0.time_to_expiry_years)
                return s0, s1, lam

        return ordered[-1], ordered[-1], 0.0

    def valid_k_bounds(self, expiry_years: float) -> Tuple[float, float]:
        """Return valid calibrated log-moneyness bounds [k_min(T), k_max(T)] for expiry_years."""
        s0, s1, _ = self._bracketing_slices(expiry_years)
        if s0 is s1:
            return s0.k_min, s0.k_max
        return max(s0.k_min, s1.k_min), min(s0.k_max, s1.k_max)

    def interpolated_forward(self, expiry_years: float) -> float:
        """Log-linear interpolated forward price F(T) across calibrated expiry slices."""
        s0, s1, lam = self._bracketing_slices(expiry_years)
        if s0 is s1:
            return s0.forward
        return math.exp((1.0 - lam) * math.log(s0.forward) + lam * math.log(s1.forward))

    def query(
        self,
        expiry_years: float,
        strike: Optional[float] = None,
        log_moneyness: Optional[float] = None,
        model: str = "svi",
        allow_extrapolation: bool = False,
        require_valid_surface: bool = False,
    ) -> SurfaceQueryResult:
        """Query implied volatility and total variance at (expiry_years, strike or log_moneyness).

        By default, rejects both strike/log-moneyness and maturity extrapolation with ExtrapolationError.
        """
        if not math.isfinite(expiry_years) or expiry_years <= 0.0:
            raise ExtrapolationError(f"Requested expiry_years must be finite and positive, got {expiry_years}.")
        if (strike is None) == (log_moneyness is None):
            raise ValueError("Provide exactly one of 'strike' or 'log_moneyness'.")
        model_norm = model.lower()
        if model_norm not in ("svi", "sabr"):
            raise ValueError(f"Unsupported model '{model}'; expected 'svi' or 'sabr'.")
        if require_valid_surface and not self.is_valid:
            raise ArbitrageViolationError(
                f"Surface '{self.surface_id}' is marked invalid due to static arbitrage violations.",
                violations=[v.to_dict() for v in self.snapshot.arbitrage_check.violations],
            )

        t_min = self._ordered_slices[0].time_to_expiry_years
        t_max = self._ordered_slices[-1].time_to_expiry_years
        t_extrap = (expiry_years < t_min - 1e-12) or (expiry_years > t_max + 1e-12)
        if t_extrap and not allow_extrapolation:
            raise ExtrapolationError(
                f"Requested expiry_years={expiry_years:.6f} lies outside calibrated maturity domain "
                f"[{t_min:.6f}, {t_max:.6f}]. Extrapolation is rejected by default.",
                details={"expiry_years": expiry_years, "t_min": t_min, "t_max": t_max, "axis": "maturity"},
            )

        fwd = self.interpolated_forward(expiry_years)
        if strike is not None:
            if not math.isfinite(strike) or strike <= 0.0:
                raise ExtrapolationError(f"Requested strike must be finite and positive, got {strike}.")
            k = math.log(strike / fwd)
            k_strike = float(strike)
        else:
            assert log_moneyness is not None
            if not math.isfinite(log_moneyness):
                raise ExtrapolationError(f"Requested log_moneyness must be finite, got {log_moneyness}.")
            k = float(log_moneyness)
            k_strike = fwd * math.exp(k)

        k_lo, k_hi = self.valid_k_bounds(expiry_years)
        k_extrap = (k < k_lo - 1e-12) or (k > k_hi + 1e-12)
        if k_extrap and not allow_extrapolation:
            raise ExtrapolationError(
                f"Requested log_moneyness={k:.6f} (K={k_strike:.4f}) lies outside calibrated strike domain "
                f"[{k_lo:.6f}, {k_hi:.6f}] at T={expiry_years:.6f}. Extrapolation is rejected by default.",
                details={
                    "expiry_years": expiry_years,
                    "log_moneyness": k,
                    "strike": k_strike,
                    "k_min": k_lo,
                    "k_max": k_hi,
                    "axis": "strike",
                },
            )

        s0, s1, lam = self._bracketing_slices(expiry_years)
        if model_norm == "svi":
            if not t_extrap:
                w_arr, _, _, _, _ = interpolate_slice_variance_and_derivs(
                    self._ordered_slices, np.array([k], dtype=float), expiry_years
                )
                w_val = float(w_arr[0])
            else:
                # If explicitly allowed, flat-vol scaling from nearest boundary slice
                w_edge = float(svi_total_variance(np.array([k], dtype=float), s0.svi_params)[0])
                w_val = w_edge * (expiry_years / s0.time_to_expiry_years)
            if w_val <= 0.0:
                raise CalibrationError(f"Non-positive total variance {w_val:.6e} evaluated at T={expiry_years}, k={k}.")
            iv_val = math.sqrt(w_val / expiry_years)
        else:
            if s0.sabr_params is None or s1.sabr_params is None:
                raise CalibrationError("SABR benchmark parameters are unavailable for this slice.")
            iv0 = sabr_implied_vol_scalar(s0.forward, s0.forward * math.exp(k), s0.time_to_expiry_years, s0.sabr_params)
            if s0 is s1:
                iv_val = iv0
                w_val = iv_val * iv_val * expiry_years
            else:
                iv1 = sabr_implied_vol_scalar(s1.forward, s1.forward * math.exp(k), s1.time_to_expiry_years, s1.sabr_params)
                w0 = iv0 * iv0 * s0.time_to_expiry_years
                w1 = iv1 * iv1 * s1.time_to_expiry_years
                w_val = (1.0 - lam) * w0 + lam * w1
                iv_val = math.sqrt(max(w_val, 1e-16) / expiry_years)

        return SurfaceQueryResult(
            underlying=self.snapshot.underlying,
            expiry_years=float(expiry_years),
            strike=k_strike,
            forward=fwd,
            log_moneyness=k,
            implied_vol=iv_val,
            total_variance=w_val,
            model=model_norm,
            extrapolated=bool(t_extrap or k_extrap),
            surface_id=self.surface_id,
            surface_valid=self.is_valid,
        )

    def sample_grid(
        self,
        k_points: int = 41,
        t_points: int = 15,
        model: str = "svi",
    ) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
        """Sample (k_grid, t_grid, iv_matrix[t_idx, k_idx]) over the common valid domain."""
        t_min, t_max, k_min, k_max = self.snapshot.domain_bounds()
        if k_max <= k_min:
            raise CalibrationError("No overlapping log-moneyness domain across slices to sample grid.")
        k_grid = np.linspace(k_min, k_max, k_points)
        if abs(t_max - t_min) <= 1e-12:
            t_grid = np.array([t_min], dtype=float)
        else:
            t_grid = np.linspace(t_min, t_max, t_points)
        iv_mat = np.zeros((len(t_grid), len(k_grid)), dtype=float)
        for ti, t_val in enumerate(t_grid):
            for ki, k_val in enumerate(k_grid):
                res = self.query(expiry_years=float(t_val), log_moneyness=float(k_val), model=model)
                iv_mat[ti, ki] = res.implied_vol
        return k_grid, t_grid, iv_mat

    def to_polars_slices(self) -> Any:
        """Export calibrated slice summary as a Polars DataFrame."""
        try:
            import polars as pl
        except ImportError as exc:
            raise ImportError("Polars is not installed.") from exc
        rows = []
        for s in self._ordered_slices:
            rows.append(
                {
                    "expiry_timestamp": s.expiry_timestamp,
                    "time_to_expiry_years": s.time_to_expiry_years,
                    "forward": s.forward,
                    "discount_factor": s.discount_factor,
                    "quote_count": s.quote_count,
                    "k_min": s.k_min,
                    "k_max": s.k_max,
                    "svi_a": s.svi_params.a,
                    "svi_b": s.svi_params.b,
                    "svi_rho": s.svi_params.rho,
                    "svi_m": s.svi_params.m,
                    "svi_sigma": s.svi_params.sigma,
                    "svi_weighted_rmse_iv": s.svi_weighted_rmse_iv,
                    "sabr_weighted_rmse_iv": s.sabr_weighted_rmse_iv,
                }
            )
        return pl.DataFrame(rows)


def build_surface(
    snapshot: OptionChainSnapshot,
    config: Optional[CalibrationConfig] = None,
) -> VolatilitySurface:
    """Execute the full deterministic surface calibration and static-arbitrage validation pipeline."""
    cfg = config if config is not None else CalibrationConfig()

    iv_quotes, forward_estimates, _rejections, rejection_counts, warnings = clean_and_invert_quotes(snapshot, cfg)
    if not iv_quotes:
        raise CalibrationError(
            "No valid option quotes remained after quote cleaning and implied-volatility inversion.",
            details={"rejection_counts": rejection_counts},
        )

    # Group cleaned IV quotes by expiry
    by_expiry: Dict[str, List[ImpliedVolQuote]] = defaultdict(list)
    for q in iv_quotes:
        by_expiry[q.expiry_timestamp].append(q)

    fwd_by_exp = {f.expiry_timestamp: f for f in forward_estimates}
    ordered_expiries = sorted(by_expiry.keys(), key=lambda ts: fwd_by_exp[ts].time_to_expiry_years)

    slices: List[SliceCalibrationResult] = []
    svi_w_sq_sum = 0.0
    svi_w_sum = 0.0
    sabr_w_sq_sum = 0.0
    sabr_w_sum = 0.0

    for slice_idx, exp_ts in enumerate(ordered_expiries):
        exp_quotes = sorted(by_expiry[exp_ts], key=lambda item: item.log_moneyness)
        if len(exp_quotes) < cfg.svi.min_slice_quotes:
            warnings.append(
                f"Skipped expiry {exp_ts}: only {len(exp_quotes)} cleaned quotes "
                f"(minimum {cfg.svi.min_slice_quotes} required for SVI)."
            )
            continue

        f_est = fwd_by_exp[exp_ts]
        t_years = f_est.time_to_expiry_years
        fwd = f_est.used_forward
        df = f_est.discount_factor

        k_arr = np.array([q.log_moneyness for q in exp_quotes], dtype=float)
        strikes_arr = np.array([q.strike for q in exp_quotes], dtype=float)
        iv_arr = np.array([q.implied_vol for q in exp_quotes], dtype=float)
        w_arr = np.array([q.weight for q in exp_quotes], dtype=float)

        slice_seed = cfg.seed + 1009 * slice_idx
        svi_params, svi_w_rmse, svi_u_rmse, svi_max_err, svi_conv, svi_nfev = fit_svi_slice(
            k_vals=k_arr,
            iv_vals=iv_arr,
            weights=w_arr,
            time_to_expiry=t_years,
            config=cfg.svi,
            seed=slice_seed,
        )

        sabr_params = None
        sabr_w_rmse = None
        sabr_u_rmse = None
        sabr_max_err = None
        sabr_conv = False
        sabr_nfev = 0
        if len(exp_quotes) >= cfg.sabr.min_slice_quotes:
            sabr_params, sabr_w_rmse, sabr_u_rmse, sabr_max_err, sabr_conv, sabr_nfev = fit_sabr_slice(
                forward=fwd,
                strikes=strikes_arr,
                iv_vals=iv_arr,
                weights=w_arr,
                time_to_expiry=t_years,
                config=cfg.sabr,
                seed=slice_seed + 503,
            )

        norm_w_slice = w_arr / np.sum(w_arr)
        svi_w_sq_sum += (svi_w_rmse ** 2) * float(np.sum(norm_w_slice))
        svi_w_sum += float(np.sum(norm_w_slice))
        if sabr_w_rmse is not None:
            sabr_w_sq_sum += (sabr_w_rmse ** 2) * float(np.sum(norm_w_slice))
            sabr_w_sum += float(np.sum(norm_w_slice))

        slices.append(
            SliceCalibrationResult(
                expiry_timestamp=exp_ts,
                time_to_expiry_years=t_years,
                forward=fwd,
                discount_factor=df,
                k_min=float(np.min(k_arr)),
                k_max=float(np.max(k_arr)),
                quote_count=len(exp_quotes),
                svi_params=svi_params,
                sabr_params=sabr_params,
                svi_weighted_rmse_iv=svi_w_rmse,
                svi_unweighted_rmse_iv=svi_u_rmse,
                svi_max_abs_err_iv=svi_max_err,
                sabr_weighted_rmse_iv=sabr_w_rmse,
                sabr_unweighted_rmse_iv=sabr_u_rmse,
                sabr_max_abs_err_iv=sabr_max_err,
                svi_converged=svi_conv,
                sabr_converged=sabr_conv,
                svi_nfev=svi_nfev,
                sabr_nfev=sabr_nfev,
                fitted_quotes=exp_quotes,
            )
        )

    if not slices:
        raise CalibrationError(
            "No expiry slice met minimum quote count requirements for surface calibration.",
            details={"warnings": warnings, "rejection_counts": rejection_counts},
        )

    arb_result = check_surface_arbitrage(slices, cfg.arbitrage)
    is_valid = arb_result.is_valid
    if not is_valid:
        warnings.append(
            f"Surface marked INVALID: {arb_result.butterfly_violation_count} butterfly and "
            f"{arb_result.calendar_violation_count} calendar arbitrage violations detected."
        )
        if cfg.arbitrage.reject_on_violation:
            raise ArbitrageViolationError(
                f"Static arbitrage violations detected ({arb_result.total_violations} total).",
                violations=[v.to_dict() for v in arb_result.violations],
            )

    overall_svi_rmse = math.sqrt(svi_w_sq_sum / max(svi_w_sum, 1e-12))
    overall_sabr_rmse = math.sqrt(sabr_w_sq_sum / sabr_w_sum) if sabr_w_sum > 0.0 else None

    config_hash = cfg.content_hash()
    surface_id = _compute_deterministic_surface_id(snapshot.snapshot_id, config_hash, slices, is_valid)

    diagnostics = CalibrationDiagnostics(
        total_input_quotes=len(snapshot.quotes),
        accepted_quotes=len(iv_quotes),
        rejected_quotes=len(snapshot.quotes) - len(iv_quotes),
        rejection_counts=rejection_counts,
        forward_estimates=forward_estimates,
        overall_svi_weighted_rmse_iv=overall_svi_rmse,
        overall_sabr_weighted_rmse_iv=overall_sabr_rmse,
        seed=cfg.seed,
        runtime_metadata=_collect_runtime_metadata(),
        warnings=warnings,
    )

    surf_snapshot = SurfaceSnapshot(
        surface_id=surface_id,
        schema_version=SCHEMA_VERSION,
        input_snapshot_id=snapshot.snapshot_id,
        underlying=snapshot.underlying,
        valuation_timestamp=snapshot.valuation_timestamp,
        conventions=snapshot.conventions,
        config_hash=config_hash,
        slices=slices,
        arbitrage_check=arb_result,
        is_valid=is_valid,
        diagnostics=diagnostics,
        interpolation_method=cfg.interpolation.method,
    )
    return VolatilitySurface(surf_snapshot)


def evaluate_heldout_quotes(
    surface: VolatilitySurface,
    heldout_quotes: List[ImpliedVolQuote],
    model: str = "svi",
) -> Dict[str, Any]:
    """Evaluate a calibrated surface against held-out ImpliedVolQuotes within its calibrated domain."""
    errors: List[float] = []
    weights: List[float] = []
    skipped_out_of_domain = 0
    per_quote: List[Dict[str, Any]] = []

    for q in heldout_quotes:
        try:
            res = surface.query(
                expiry_years=q.time_to_expiry_years,
                strike=q.strike,
                model=model,
                allow_extrapolation=False,
            )
        except ExtrapolationError:
            skipped_out_of_domain += 1
            continue
        err = res.implied_vol - q.implied_vol
        errors.append(err)
        weights.append(q.weight)
        per_quote.append(
            {
                "quote_id": q.quote_id,
                "expiry_timestamp": q.expiry_timestamp,
                "strike": q.strike,
                "observed_iv": q.implied_vol,
                "predicted_iv": res.implied_vol,
                "error_iv": err,
            }
        )

    if not errors:
        return {
            "model": model,
            "evaluated_count": 0,
            "skipped_out_of_domain": skipped_out_of_domain,
            "heldout_rmse_iv": None,
            "heldout_weighted_rmse_iv": None,
            "heldout_mae_iv": None,
            "heldout_max_abs_err_iv": None,
            "residuals": [],
        }

    err_arr = np.asarray(errors, dtype=float)
    w_arr = np.asarray(weights, dtype=float)
    w_norm = w_arr / np.sum(w_arr)
    return {
        "model": model,
        "evaluated_count": len(errors),
        "skipped_out_of_domain": skipped_out_of_domain,
        "heldout_rmse_iv": float(np.sqrt(np.mean(err_arr ** 2))),
        "heldout_weighted_rmse_iv": float(np.sqrt(np.sum(w_norm * (err_arr ** 2)))),
        "heldout_mae_iv": float(np.mean(np.abs(err_arr))),
        "heldout_max_abs_err_iv": float(np.max(np.abs(err_arr))),
        "residuals": per_quote,
    }


def save_surface_snapshot(surface: VolatilitySurface, path: Union[str, Path]) -> Path:
    """Persist a versioned SurfaceSnapshot to JSON."""
    out_path = Path(path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(surface.snapshot.to_dict(), indent=2, sort_keys=True), encoding="utf-8")
    return out_path


def load_surface_snapshot(path: Union[str, Path]) -> VolatilitySurface:
    """Load a versioned SurfaceSnapshot from JSON."""
    in_path = Path(path)
    data = json.loads(in_path.read_text(encoding="utf-8"))
    snap = SurfaceSnapshot.from_dict(data)
    return VolatilitySurface(snap)
