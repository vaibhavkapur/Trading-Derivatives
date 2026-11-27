"""Seeded stochastic spot path generators and historical bar loader."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import math
from pathlib import Path
from typing import List, Optional, Tuple

import numpy as np
import pandas as pd

from .contracts import JumpParams, MarketParams, OptionContract, PathConfig
from .errors import ValidationError


@dataclass(frozen=True)
class SpotPath:
    """Immutable discrete time-series representation of a single underlying spot path."""

    path_id: str
    model: str
    seed: Optional[int]
    times: np.ndarray  # shape (N+1,), year fractions from 0.0 to T
    spots: np.ndarray  # shape (N+1,), positive finite spot prices
    is_stale: np.ndarray  # shape (N+1,), bool flags
    staleness_seconds: np.ndarray  # shape (N+1,), float staleness durations
    gap_shocks: np.ndarray  # shape (N+1,), float deterministic/jump shock log/pct diagnostics
    timestamps_utc: Optional[Tuple[str, ...]] = None

    def __post_init__(self) -> None:
        if self.times.ndim != 1 or self.spots.ndim != 1:
            raise ValidationError("times and spots must be 1D arrays", field="SpotPath")
        n = len(self.times)
        if n < 2:
            raise ValidationError("SpotPath requires at least 2 points (t=0 and expiry)", field="SpotPath")
        if not (len(self.spots) == len(self.is_stale) == len(self.staleness_seconds) == len(self.gap_shocks) == n):
            raise ValidationError("All SpotPath arrays must share identical length", field="SpotPath")
        if not np.all(np.isfinite(self.times)) or np.any(np.diff(self.times) <= 0.0):
            raise ValidationError("SpotPath times must be finite and strictly increasing", field="SpotPath.times")
        if not np.all(np.isfinite(self.spots)) or np.any(self.spots <= 0.0):
            raise ValidationError("SpotPath spots must be finite and strictly positive", field="SpotPath.spots")

    @property
    def num_steps(self) -> int:
        return len(self.times) - 1

    @property
    def maturity_years(self) -> float:
        return float(self.times[-1] - self.times[0])


def _apply_deterministic_shocks(
    spots: np.ndarray,
    gap_shocks: np.ndarray,
    jump_params: Optional[JumpParams],
) -> None:
    if jump_params is None or not jump_params.deterministic_shocks:
        return
    for shock in jump_params.deterministic_shocks:
        if shock.step < len(spots):
            factor = 1.0 + shock.pct_return
            spots[shock.step :] *= factor
            gap_shocks[shock.step] += shock.pct_return


def generate_gbm_paths(
    market: MarketParams,
    contract: OptionContract,
    path_config: PathConfig,
) -> List[SpotPath]:
    """Generate reproducible Geometric Brownian Motion (GBM) spot paths using PCG64."""
    rng = np.random.default_rng(np.random.PCG64(path_config.seed))
    n_paths = path_config.num_paths
    n_steps = path_config.num_steps
    maturity = float(contract.maturity_years)
    dt = maturity / n_steps
    times = np.linspace(0.0, maturity, n_steps + 1, dtype=np.float64)

    mu = market.effective_drift
    sigma = market.effective_realized_volatility
    drift_term = (mu - 0.5 * sigma * sigma) * dt
    vol_term = sigma * math.sqrt(dt)

    normals = rng.standard_normal(size=(n_paths, n_steps))
    log_increments = drift_term + vol_term * normals
    log_spots = np.empty((n_paths, n_steps + 1), dtype=np.float64)
    log_spots[:, 0] = math.log(market.spot0)
    np.cumsum(log_increments, axis=1, out=log_spots[:, 1:])
    log_spots[:, 1:] += log_spots[:, 0:1]
    all_spots = np.exp(log_spots)

    paths: List[SpotPath] = []
    zeros_bool = np.zeros(n_steps + 1, dtype=bool)
    zeros_float = np.zeros(n_steps + 1, dtype=np.float64)

    for idx in range(n_paths):
        spots_i = all_spots[idx].copy()
        shocks_i = zeros_float.copy()
        _apply_deterministic_shocks(spots_i, shocks_i, path_config.jump_params)
        paths.append(
            SpotPath(
                path_id=f"gbm_s{path_config.seed}_p{idx:05d}",
                model="gbm",
                seed=path_config.seed,
                times=times.copy(),
                spots=spots_i,
                is_stale=zeros_bool.copy(),
                staleness_seconds=zeros_float.copy(),
                gap_shocks=shocks_i,
            )
        )
    return paths


def generate_merton_jump_paths(
    market: MarketParams,
    contract: OptionContract,
    path_config: PathConfig,
) -> List[SpotPath]:
    """Generate reproducible Merton jump-diffusion / stressed spot paths."""
    rng = np.random.default_rng(np.random.PCG64(path_config.seed))
    jp = path_config.jump_params or JumpParams()

    n_paths = path_config.num_paths
    n_steps = path_config.num_steps
    maturity = float(contract.maturity_years)
    dt = maturity / n_steps
    times = np.linspace(0.0, maturity, n_steps + 1, dtype=np.float64)

    mu = market.effective_drift
    sigma = market.effective_realized_volatility
    lam = jp.jump_intensity
    mu_j = jp.jump_mean
    sigma_j = jp.jump_std
    # Compensator k = E[e^Y - 1] so expected drift remains exp(mu * t)
    jump_compensator = math.exp(mu_j + 0.5 * sigma_j * sigma_j) - 1.0 if lam > 0.0 else 0.0

    drift_term = (mu - lam * jump_compensator - 0.5 * sigma * sigma) * dt
    vol_term = sigma * math.sqrt(dt)

    normals = rng.standard_normal(size=(n_paths, n_steps))
    if lam > 0.0:
        jump_counts = rng.poisson(lam=lam * dt, size=(n_paths, n_steps))
        jump_sum = np.zeros((n_paths, n_steps), dtype=np.float64)
        nonzero_mask = jump_counts > 0
        if np.any(nonzero_mask):
            counts_nz = jump_counts[nonzero_mask]
            # Sum of N i.i.d. Normal(mu_j, sigma_j^2) is Normal(N * mu_j, N * sigma_j^2)
            z_j = rng.standard_normal(size=counts_nz.shape)
            jump_sum[nonzero_mask] = counts_nz * mu_j + np.sqrt(counts_nz) * sigma_j * z_j
    else:
        jump_sum = np.zeros((n_paths, n_steps), dtype=np.float64)

    log_increments = drift_term + vol_term * normals + jump_sum
    log_spots = np.empty((n_paths, n_steps + 1), dtype=np.float64)
    log_spots[:, 0] = math.log(market.spot0)
    np.cumsum(log_increments, axis=1, out=log_spots[:, 1:])
    log_spots[:, 1:] += log_spots[:, 0:1]
    all_spots = np.exp(log_spots)

    paths: List[SpotPath] = []
    zeros_bool = np.zeros(n_steps + 1, dtype=bool)
    zeros_float = np.zeros(n_steps + 1, dtype=np.float64)

    for idx in range(n_paths):
        spots_i = all_spots[idx].copy()
        shocks_i = np.zeros(n_steps + 1, dtype=np.float64)
        shocks_i[1:] = np.expm1(jump_sum[idx])
        _apply_deterministic_shocks(spots_i, shocks_i, jp)
        paths.append(
            SpotPath(
                path_id=f"jump_s{path_config.seed}_p{idx:05d}",
                model="merton_jump",
                seed=path_config.seed,
                times=times.copy(),
                spots=spots_i,
                is_stale=zeros_bool.copy(),
                staleness_seconds=zeros_float.copy(),
                gap_shocks=shocks_i,
            )
        )
    return paths


def _parse_iso_utc(ts: str) -> datetime:
    cleaned = ts.strip()
    if cleaned.endswith("Z"):
        cleaned = cleaned[:-1] + "+00:00"
    dt = datetime.fromisoformat(cleaned)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def load_historical_bars(
    bars_path: str | Path,
    *,
    maturity_years: Optional[float] = None,
    max_staleness_seconds: float = 86400.0,
    day_count: str = "ACT/365F",
) -> SpotPath:
    """Load and validate a permitted historical/fixture bar CSV into a SpotPath.

    Required columns:
    - timestamp_utc (ISO-8601 UTC string)
    - spot (or close)

    Optional columns:
    - time_years (explicit year fraction from t=0)
    - is_stale (bool / 0 / 1 / true / false)
    - staleness_seconds (float >= 0)
    """
    path = Path(bars_path)
    if not path.exists():
        raise ValidationError(f"bars file does not exist: {path}", field="path.bars_path")

    df = pd.read_csv(path)
    if df.empty or len(df) < 2:
        raise ValidationError("bars CSV must contain at least 2 rows", field="path.bars_path")

    if "timestamp_utc" not in df.columns:
        raise ValidationError("bars CSV missing required column 'timestamp_utc'", field="timestamp_utc")

    price_col = "spot" if "spot" in df.columns else ("close" if "close" in df.columns else None)
    if price_col is None:
        raise ValidationError("bars CSV must contain 'spot' or 'close' column", field="spot")

    parsed_ts: List[datetime] = []
    raw_ts: List[str] = []
    for i, val in enumerate(df["timestamp_utc"]):
        if pd.isna(val):
            raise ValidationError(f"row {i}: missing timestamp_utc", field="timestamp_utc")
        s = str(val).strip()
        try:
            dt = _parse_iso_utc(s)
        except ValueError as exc:
            raise ValidationError(f"row {i}: invalid ISO timestamp {s!r}", field="timestamp_utc") from exc
        parsed_ts.append(dt)
        raw_ts.append(s)

    deltas_sec = np.array(
        [0.0] + [(parsed_ts[i] - parsed_ts[i - 1]).total_seconds() for i in range(1, len(parsed_ts))],
        dtype=np.float64,
    )
    if np.any(deltas_sec[1:] <= 0.0):
        raise ValidationError("timestamps in bars CSV must be strictly increasing", field="timestamp_utc")

    spots = pd.to_numeric(df[price_col], errors="coerce").to_numpy(dtype=np.float64)
    if not np.all(np.isfinite(spots)) or np.any(spots <= 0.0):
        raise ValidationError(f"all prices in '{price_col}' must be finite and > 0", field=price_col)

    if "time_years" in df.columns:
        times = pd.to_numeric(df["time_years"], errors="coerce").to_numpy(dtype=np.float64)
        if not np.all(np.isfinite(times)):
            raise ValidationError("time_years column contains non-numeric or NaN values", field="time_years")
    else:
        denom_days = 365.0 if day_count == "ACT/365F" else 252.0
        elapsed_sec = np.array([(ts - parsed_ts[0]).total_seconds() for ts in parsed_ts], dtype=np.float64)
        times = elapsed_sec / (denom_days * 86400.0)
        if maturity_years is not None and times[-1] > 0.0:
            # If caller did not specify time_years, preserve elapsed ACT/365F scale unless zero
            pass

    if "staleness_seconds" in df.columns:
        staleness = pd.to_numeric(df["staleness_seconds"], errors="coerce").fillna(0.0).to_numpy(dtype=np.float64)
        if np.any(staleness < 0.0):
            raise ValidationError("staleness_seconds must be >= 0", field="staleness_seconds")
    else:
        staleness = np.zeros(len(df), dtype=np.float64)

    if "is_stale" in df.columns:
        raw_stale = df["is_stale"].astype(str).str.strip().str.lower()
        is_stale = raw_stale.isin({"true", "1", "yes"}).to_numpy(dtype=bool)
    else:
        is_stale = np.zeros(len(df), dtype=bool)

    # Also mark stale if explicit staleness_seconds exceeds max_staleness_seconds
    is_stale = np.logical_or(is_stale, staleness > max_staleness_seconds)

    gap_shocks = np.zeros(len(df), dtype=np.float64)
    if "gap_shock" in df.columns:
        gap_shocks = pd.to_numeric(df["gap_shock"], errors="coerce").fillna(0.0).to_numpy(dtype=np.float64)

    return SpotPath(
        path_id=f"bars_{path.stem}",
        model="historical_bars",
        seed=None,
        times=times,
        spots=spots,
        is_stale=is_stale,
        staleness_seconds=staleness,
        gap_shocks=gap_shocks,
        timestamps_utc=tuple(raw_ts),
    )


def generate_paths(
    market: MarketParams,
    contract: OptionContract,
    path_config: PathConfig,
    *,
    base_dir: Optional[Path] = None,
    day_count: str = "ACT/365F",
) -> List[SpotPath]:
    """Dispatch path generation or historical bar loading from PathConfig."""
    if path_config.model == "gbm":
        return generate_gbm_paths(market, contract, path_config)
    if path_config.model == "merton_jump":
        return generate_merton_jump_paths(market, contract, path_config)
    if path_config.model == "historical_bars":
        assert path_config.bars_path is not None
        p = Path(path_config.bars_path)
        if not p.is_absolute() and base_dir is not None:
            p = base_dir / p
        return [
            load_historical_bars(
                p,
                maturity_years=contract.maturity_years,
                max_staleness_seconds=path_config.max_staleness_seconds,
                day_count=day_count,
            )
        ]
    raise ValidationError(f"Unsupported path model: {path_config.model!r}", field="path.model")
