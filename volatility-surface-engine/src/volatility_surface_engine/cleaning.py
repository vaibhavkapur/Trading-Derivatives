"""Quote cleaning, put-call parity forward estimation, and IV inversion pipeline."""

from __future__ import annotations

import math
from collections import Counter, defaultdict
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

from volatility_surface_engine.black76 import (
    black76_price_bounds,
    black76_vega,
    implied_volatility_black76,
)
from volatility_surface_engine.models import (
    CalibrationConfig,
    ExpiryMarketData,
    ForwardEstimate,
    ImpliedVolQuote,
    InputBoundsError,
    OptionChainSnapshot,
    OptionQuote,
    QuoteRejection,
)
from volatility_surface_engine.validation import validate_chain_snapshot


def _weighted_median(values: np.ndarray, weights: np.ndarray) -> float:
    """Compute a deterministic weighted median."""
    order = np.argsort(values)
    sorted_vals = values[order]
    sorted_w = weights[order]
    cum_w = np.cumsum(sorted_w)
    cutoff = 0.5 * cum_w[-1]
    idx = int(np.searchsorted(cum_w, cutoff))
    idx = min(max(idx, 0), len(sorted_vals) - 1)
    return float(sorted_vals[idx])


def estimate_forwards_put_call_parity(
    snapshot: OptionChainSnapshot,
    resolved_times: Dict[str, float],
    config: CalibrationConfig,
) -> Tuple[Dict[str, ForwardEstimate], List[str]]:
    """Estimate per-expiry forwards via bid-ask-weighted put-call parity and cross-check supplied forwards."""
    warnings: List[str] = []
    estimates: Dict[str, ForwardEstimate] = {}
    exp_map: Dict[str, ExpiryMarketData] = {e.expiry_timestamp: e for e in snapshot.expiries}

    # Group valid non-crossed quotes by (expiry_timestamp, strike)
    by_expiry_strike: Dict[str, Dict[float, Dict[str, OptionQuote]]] = defaultdict(lambda: defaultdict(dict))
    for q in snapshot.quotes:
        if not (math.isfinite(q.bid) and math.isfinite(q.ask) and math.isfinite(q.strike)):
            continue
        if q.strike <= 0.0 or q.bid > q.ask or q.ask <= 0.0:
            continue
        if q.bid < config.cleaning.min_bid or (q.bid == 0.0 and not config.cleaning.allow_zero_bid):
            continue
        by_expiry_strike[q.expiry_timestamp][q.strike][q.option_type] = q

    for exp_ts, exp_data in sorted(exp_map.items(), key=lambda kv: resolved_times[kv[0]]):
        t_years = resolved_times[exp_ts]
        df = exp_data.discount_factor
        supplied_f = exp_data.forward

        if config.forward_estimation.method == "supplied_only":
            estimates[exp_ts] = ForwardEstimate(
                expiry_timestamp=exp_ts,
                time_to_expiry_years=t_years,
                supplied_forward=supplied_f,
                estimated_forward=None,
                used_forward=supplied_f,
                discount_factor=df,
                parity_pairs_used=0,
                relative_difference=None,
                source="supplied",
                notes="Configured to use supplied forward directly.",
            )
            continue

        parity_forwards: List[float] = []
        parity_weights: List[float] = []
        for strike, pair in sorted(by_expiry_strike.get(exp_ts, {}).items()):
            if "call" in pair and "put" in pair:
                c_q = pair["call"]
                p_q = pair["put"]
                # Skip excessively wide spreads when estimating forward
                c_spread = c_q.ask - c_q.bid
                p_spread = p_q.ask - p_q.bid
                if c_q.mid <= 0.0 or p_q.mid <= 0.0:
                    continue
                if c_spread / c_q.mid > config.cleaning.max_relative_spread:
                    continue
                if p_spread / p_q.mid > config.cleaning.max_relative_spread:
                    continue
                f_hat = strike + (c_q.mid - p_q.mid) / df
                if math.isfinite(f_hat) and f_hat > 0.0:
                    total_spread = max(c_spread + p_spread, 1e-4)
                    parity_forwards.append(f_hat)
                    parity_weights.append(1.0 / (total_spread * total_spread))

        if len(parity_forwards) >= config.forward_estimation.min_parity_pairs:
            est_f = _weighted_median(np.asarray(parity_forwards, dtype=float), np.asarray(parity_weights, dtype=float))
            rel_diff = abs(est_f - supplied_f) / supplied_f
            notes = f"Estimated from {len(parity_forwards)} matched call/put parity pairs."
            if rel_diff > config.forward_estimation.max_forward_deviation_rel:
                warn_msg = (
                    f"Forward parity discrepancy for expiry {exp_ts}: supplied={supplied_f:.6f}, "
                    f"parity_est={est_f:.6f} (rel_diff={rel_diff:.4%})."
                )
                warnings.append(warn_msg)
                notes += " Exceeded deviation threshold."
            if config.forward_estimation.prefer_estimated_forward:
                used_f = est_f
                source = "parity_estimated"
            else:
                used_f = supplied_f
                source = "supplied_verified"
            estimates[exp_ts] = ForwardEstimate(
                expiry_timestamp=exp_ts,
                time_to_expiry_years=t_years,
                supplied_forward=supplied_f,
                estimated_forward=est_f,
                used_forward=used_f,
                discount_factor=df,
                parity_pairs_used=len(parity_forwards),
                relative_difference=rel_diff,
                source=source,
                notes=notes,
            )
        else:
            estimates[exp_ts] = ForwardEstimate(
                expiry_timestamp=exp_ts,
                time_to_expiry_years=t_years,
                supplied_forward=supplied_f,
                estimated_forward=None,
                used_forward=supplied_f,
                discount_factor=df,
                parity_pairs_used=len(parity_forwards),
                relative_difference=None,
                source="supplied_fallback",
                notes="Insufficient matched call/put pairs for parity forward estimation.",
            )

    return estimates, warnings


def clean_and_invert_quotes(
    snapshot: OptionChainSnapshot,
    config: CalibrationConfig,
) -> Tuple[List[ImpliedVolQuote], List[ForwardEstimate], List[QuoteRejection], Dict[str, int], List[str]]:
    """Validate snapshot, clean quotes, estimate forwards, and invert implied volatilities."""
    resolved_times = validate_chain_snapshot(snapshot)
    fwd_map, warnings = estimate_forwards_put_call_parity(snapshot, resolved_times, config)
    exp_map = {e.expiry_timestamp: e for e in snapshot.expiries}

    rejections: List[QuoteRejection] = []
    rejection_counts: Counter = Counter()

    def _reject(q: OptionQuote, reason: str, detail: str) -> None:
        rejections.append(
            QuoteRejection(
                quote_id=q.quote_id,
                expiry_timestamp=q.expiry_timestamp,
                strike=q.strike,
                option_type=q.option_type,
                reason=reason,
                detail=detail,
            )
        )
        rejection_counts[reason] += 1

    # Stage 1: basic market-hygiene filters
    preliminary: List[Tuple[OptionQuote, float, float, float]] = []
    for q in snapshot.quotes:
        t_years = resolved_times[q.expiry_timestamp]
        fwd = fwd_map[q.expiry_timestamp].used_forward
        df = exp_map[q.expiry_timestamp].discount_factor

        if not (math.isfinite(q.bid) and math.isfinite(q.ask) and math.isfinite(q.strike)):
            _reject(q, "non_finite_quote", "Non-finite bid, ask, or strike.")
            continue
        if q.strike <= 0.0:
            _reject(q, "invalid_strike", f"Strike must be positive, got {q.strike}.")
            continue
        if t_years < config.cleaning.min_time_to_expiry_years:
            _reject(
                q,
                "expired_or_short_expiry",
                f"T={t_years:.6f} < min_time_to_expiry_years={config.cleaning.min_time_to_expiry_years:.6f}.",
            )
            continue
        if q.bid > q.ask:
            _reject(q, "crossed_quote", f"Crossed market: bid={q.bid} > ask={q.ask}.")
            continue
        if q.bid < config.cleaning.min_bid or (q.bid == 0.0 and not config.cleaning.allow_zero_bid):
            _reject(q, "non_positive_bid", f"Bid {q.bid} violates min_bid/allow_zero_bid policy.")
            continue

        mid = q.mid
        if mid <= 0.0:
            _reject(q, "non_positive_mid", f"Mid price must be positive, got {mid}.")
            continue

        abs_spread = q.ask - q.bid
        rel_spread = abs_spread / mid
        if abs_spread > config.cleaning.max_absolute_spread:
            _reject(
                q,
                "wide_absolute_spread",
                f"Spread {abs_spread:.6f} > max_absolute_spread={config.cleaning.max_absolute_spread}.",
            )
            continue
        if rel_spread > config.cleaning.max_relative_spread:
            _reject(
                q,
                "wide_relative_spread",
                f"Relative spread {rel_spread:.4f} > max_relative_spread={config.cleaning.max_relative_spread}.",
            )
            continue

        moneyness = q.strike / fwd
        if not (config.cleaning.min_moneyness_ratio <= moneyness <= config.cleaning.max_moneyness_ratio):
            _reject(
                q,
                "extreme_moneyness",
                f"K/F={moneyness:.4f} outside [{config.cleaning.min_moneyness_ratio}, {config.cleaning.max_moneyness_ratio}].",
            )
            continue

        lower_b, upper_b = black76_price_bounds(fwd, q.strike, df, q.option_type)
        tol = config.cleaning.intrinsic_tolerance
        if mid <= lower_b + tol or mid >= upper_b - tol:
            _reject(
                q,
                "intrinsic_bound_violation",
                f"Mid price {mid:.6f} violates no-arbitrage bounds ({lower_b:.6f}, {upper_b:.6f}).",
            )
            continue

        preliminary.append((q, t_years, fwd, df))

    # Stage 2: optional OTM wing preference when both call & put exist at same (expiry, strike)
    if config.cleaning.use_otm_wings:
        grouped: Dict[Tuple[str, float], List[Tuple[OptionQuote, float, float, float]]] = defaultdict(list)
        for item in preliminary:
            q = item[0]
            grouped[(q.expiry_timestamp, q.strike)].append(item)

        selected: List[Tuple[OptionQuote, float, float, float]] = []
        for (_exp_ts, strike), items in sorted(grouped.items()):
            if len(items) == 1:
                selected.append(items[0])
                continue
            fwd = items[0][2]
            preferred_type = "put" if strike < fwd else "call"
            preferred_items = [it for it in items if it[0].option_type == preferred_type]
            if preferred_items:
                selected.append(preferred_items[0])
                for it in items:
                    if it[0].quote_id != preferred_items[0][0].quote_id:
                        _reject(
                            it[0],
                            "otm_preference_filtered",
                            f"Preferred OTM {preferred_type} over ITM {it[0].option_type} at strike {strike}.",
                        )
            else:
                selected.append(items[0])
    else:
        selected = preliminary

    # Stage 3: Black-76 implied volatility inversion, vega gate, and bid-ask weighting
    iv_quotes: List[ImpliedVolQuote] = []
    for q, t_years, fwd, df in selected:
        try:
            iv = implied_volatility_black76(
                price=q.mid,
                forward=fwd,
                strike=q.strike,
                time_to_expiry=t_years,
                discount_factor=df,
                option_type=q.option_type,
                vol_lower=config.iv_solver.vol_lower,
                vol_upper=config.iv_solver.vol_upper,
                tol=config.iv_solver.tol,
                max_iter=config.iv_solver.max_iter,
                min_vega=0.0,
            )
        except InputBoundsError as exc:
            _reject(q, "iv_inversion_failed", exc.message)
            continue

        vega = black76_vega(fwd, q.strike, t_years, iv, df)
        if vega < config.cleaning.min_vega:
            _reject(
                q,
                "near_zero_vega",
                f"Vega {vega:.6e} < min_vega={config.cleaning.min_vega:.6e} at IV={iv:.6f}.",
            )
            continue

        bid_iv: Optional[float] = None
        ask_iv: Optional[float] = None
        try:
            bid_iv = implied_volatility_black76(
                price=q.bid,
                forward=fwd,
                strike=q.strike,
                time_to_expiry=t_years,
                discount_factor=df,
                option_type=q.option_type,
                vol_lower=config.iv_solver.vol_lower,
                vol_upper=config.iv_solver.vol_upper,
                tol=config.iv_solver.tol,
                max_iter=config.iv_solver.max_iter,
            )
        except InputBoundsError:
            bid_iv = None

        try:
            ask_iv = implied_volatility_black76(
                price=q.ask,
                forward=fwd,
                strike=q.strike,
                time_to_expiry=t_years,
                discount_factor=df,
                option_type=q.option_type,
                vol_lower=config.iv_solver.vol_lower,
                vol_upper=config.iv_solver.vol_upper,
                tol=config.iv_solver.tol,
                max_iter=config.iv_solver.max_iter,
            )
        except InputBoundsError:
            ask_iv = None

        if bid_iv is not None and ask_iv is not None and ask_iv > bid_iv:
            iv_spread = ask_iv - bid_iv
        else:
            iv_spread = max((q.ask - q.bid) / max(vega, config.cleaning.min_vega), config.svi.spread_weight_floor)

        eff_spread = max(iv_spread, config.svi.spread_weight_floor)
        weight = 1.0 / (eff_spread * eff_spread)
        log_k = math.log(q.strike / fwd)

        iv_quotes.append(
            ImpliedVolQuote(
                quote_id=q.quote_id,
                expiry_timestamp=q.expiry_timestamp,
                time_to_expiry_years=t_years,
                strike=q.strike,
                option_type=q.option_type,
                bid=q.bid,
                ask=q.ask,
                mid=q.mid,
                forward=fwd,
                discount_factor=df,
                log_moneyness=log_k,
                implied_vol=iv,
                total_variance=iv * iv * t_years,
                bid_iv=bid_iv,
                ask_iv=ask_iv,
                iv_spread=iv_spread,
                vega=vega,
                weight=weight,
            )
        )

    iv_quotes.sort(key=lambda item: (item.time_to_expiry_years, item.log_moneyness, item.quote_id))
    forward_list = [fwd_map[e.expiry_timestamp] for e in sorted(snapshot.expiries, key=lambda x: resolved_times[x.expiry_timestamp])]
    return iv_quotes, forward_list, rejections, dict(sorted(rejection_counts.items())), warnings


def iv_quotes_to_polars(iv_quotes: List[ImpliedVolQuote]) -> Any:
    """Convert cleaned ImpliedVolQuote list to a Polars DataFrame (requires polars)."""
    try:
        import polars as pl
    except ImportError as exc:
        raise ImportError("Polars is not installed. Install with `pip install polars`.") from exc
    if not iv_quotes:
        return pl.DataFrame()
    return pl.DataFrame([q.to_dict() for q in iv_quotes])
