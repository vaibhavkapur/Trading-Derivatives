"""Thread-safe, version-aware Real-Time Greeks Engine orchestrator."""

from __future__ import annotations

import hashlib
import json
import logging
import random
import threading
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from typing import Any

from real_time_greeks_engine.aggregator import (
    IncrementalGreeksAggregator,
    full_recompute_aggregates,
)
from real_time_greeks_engine.cache import GreeksCacheKey, VersionAwareGreeksCache
from real_time_greeks_engine.config import EngineConfig
from real_time_greeks_engine.errors import (
    MissingMarketDataError,
    SnapshotVersionConflictError,
    StaleMarketDataError,
    ValidationError,
)
from real_time_greeks_engine.market_data import (
    MarketDataStore,
    TickIngestOutcome,
    interpolate_vol,
    interpolate_zero_rate,
    year_fraction,
)
from real_time_greeks_engine.models import (
    AggregatedGreeksBucket,
    AlertSeverity,
    BucketDiff,
    OptionPosition,
    PortfolioSnapshot,
    PortfolioTotals,
    PositionGreeksResult,
    PricingMethod,
    ReconciliationReport,
    ReliabilityFlag,
    ScenarioResult,
    ScenarioShock,
    SpotTick,
    StaleDataAlert,
    VolSurfaceSnapshot,
    YieldCurveSnapshot,
    ensure_utc,
)
from real_time_greeks_engine.pricing import (
    PriceCallable,
    compute_instrument_greeks,
    scale_unit_greeks_to_position,
)

logger = logging.getLogger("real_time_greeks_engine")

_AGG_NUMERIC_FIELDS = (
    "pv",
    "delta_shares",
    "dollar_delta",
    "gamma_shares",
    "dollar_gamma_1pct",
    "vega",
    "theta",
    "rho",
    "vanna",
    "charm",
    "volga",
)


def _percentile(sorted_vals: list[float], pct: float) -> float:
    if not sorted_vals:
        return 0.0
    if len(sorted_vals) == 1:
        return sorted_vals[0]
    rank = (pct / 100.0) * (len(sorted_vals) - 1)
    lo = int(rank)
    hi = min(lo + 1, len(sorted_vals) - 1)
    frac = rank - lo
    return sorted_vals[lo] + frac * (sorted_vals[hi] - sorted_vals[lo])


class RealTimeGreeksEngine:
    """Thread-safe real-time option Greeks calculation and incremental aggregation engine."""

    def __init__(
        self,
        config: EngineConfig | None = None,
        *,
        valuation_time_utc: datetime | None = None,
    ) -> None:
        self.config = config or EngineConfig()
        self._config_hash = self.config.config_hash()
        self._lock = threading.RLock()
        self._valuation_time_utc: datetime = (
            ensure_utc(valuation_time_utc)
            if valuation_time_utc is not None
            else datetime.now(timezone.utc)
        )
        self._market = MarketDataStore(self.config.staleness)
        self._cache = VersionAwareGreeksCache(
            max_entries=self.config.cache.max_entries,
            enabled=self.config.cache.enabled,
        )
        self._aggregator = IncrementalGreeksAggregator()
        self._positions: dict[str, OptionPosition] = {}
        self._positions_by_underlying: dict[str, set[str]] = {}
        self._positions_by_currency: dict[str, set[str]] = {}
        self._custom_pricers: dict[str, PriceCallable] = {}

        # Active alerts keyed by alert_id (staleness alerts refreshed on valuation; transient alerts retained bounded)
        self._staleness_alerts: dict[str, StaleDataAlert] = {}
        self._operational_alerts: deque[StaleDataAlert] = deque(maxlen=200)

        # Counters & latency telemetry
        self._snapshot_seq: int = 0
        self._counters: dict[str, int] = {
            "ticks_accepted": 0,
            "duplicate_ticks_ignored": 0,
            "out_of_order_ticks_ignored": 0,
            "curves_updated": 0,
            "vols_updated": 0,
            "positions_upserted": 0,
            "positions_removed": 0,
            "positions_recomputed": 0,
            "stale_freezes": 0,
            "snapshot_race_conflicts": 0,
            "reconciliations_run": 0,
            "scenarios_evaluated": 0,
        }
        self._update_latencies_ms: deque[float] = deque(
            maxlen=self.config.observability.latency_window_size
        )
        self._latest_snapshot: PortfolioSnapshot = self._build_snapshot_locked()

    def register_custom_pricer(self, instrument_id: str, price_fn: PriceCallable) -> None:
        """Register an instrument-specific callable pricer (forces bump-and-revalue fallback)."""
        with self._lock:
            self._custom_pricers[instrument_id] = price_fn

    def _check_expected_seq_locked(self, expected_snapshot_seq: int | None) -> None:
        if expected_snapshot_seq is not None and expected_snapshot_seq != self._snapshot_seq:
            self._counters["snapshot_race_conflicts"] += 1
            alert = StaleDataAlert(
                alert_id=f"RACE-{self._snapshot_seq}-{expected_snapshot_seq}",
                severity=AlertSeverity.WARNING,
                code="SNAPSHOT_RACE_PROTECTED",
                resource="engine:snapshot",
                message=(
                    f"Rejected concurrent write expecting snapshot_seq={expected_snapshot_seq}, "
                    f"current snapshot_seq={self._snapshot_seq}."
                ),
                detected_at_utc=self._valuation_time_utc,
            )
            self._operational_alerts.append(alert)
            raise SnapshotVersionConflictError(
                alert.message,
                details={
                    "expected_snapshot_seq": expected_snapshot_seq,
                    "current_snapshot_seq": self._snapshot_seq,
                },
            )

    def _compute_position_locked(
        self,
        position: OptionPosition,
        *,
        market: MarketDataStore | None = None,
        valuation_time_utc: datetime | None = None,
        use_cache: bool = True,
        allow_freeze: bool = True,
        method_override: PricingMethod | None = None,
    ) -> tuple[PositionGreeksResult, list[StaleDataAlert]]:
        mkt = market or self._market
        val_time = valuation_time_utc or self._valuation_time_utc
        inst = position.instrument

        alerts = mkt.check_staleness_for_position(
            underlying=inst.underlying,
            currency=inst.currency,
            valuation_time_utc=val_time,
        )

        if alerts and self.config.staleness.strict_raise_on_stale and allow_freeze:
            raise StaleMarketDataError(
                f"Stale market data for position {position.position_id}: {alerts[0].message}",
                details={"position_id": position.position_id, "alerts": [a.model_dump(mode="json") for a in alerts]},
            )

        if alerts and self.config.staleness.freeze_on_stale and allow_freeze:
            prev = self._aggregator.get_position_result(position.position_id)
            if prev is not None and prev.quantity == position.quantity and prev.contract_multiplier == inst.contract_multiplier:
                self._counters["stale_freezes"] += 1
                frozen_flags = list(
                    dict.fromkeys(
                        [f for f in prev.flags if f is not ReliabilityFlag.OK]
                        + [ReliabilityFlag.STALE_MARKET_DATA, ReliabilityFlag.FROZEN_SNAPSHOT]
                    )
                )
                frozen_result = prev.model_copy(
                    update={
                        "flags": frozen_flags,
                        "is_frozen": True,
                        "computed_at_utc": val_time,
                    }
                )
                return frozen_result, alerts

        spot_tick = mkt.get_spot(inst.underlying)
        curve = mkt.get_curve(inst.currency)
        vol_surface = mkt.get_vol_surface(inst.underlying)
        market_version_key = mkt.position_version_key(inst.underlying, inst.currency)
        custom_fn = self._custom_pricers.get(inst.instrument_id)
        chosen_method = (
            PricingMethod.BUMP_AND_REVALUE
            if custom_fn is not None
            else (method_override or PricingMethod(self.config.pricing.default_method))
        )

        cache_key = GreeksCacheKey(
            position_id=position.position_id,
            position_version=position.version,
            instrument_fingerprint=inst.fingerprint(),
            quantity=position.quantity,
            market_version_key=market_version_key,
            valuation_time_iso=val_time.isoformat(),
            pricing_method=chosen_method.value,
        )

        if use_cache and not alerts:
            cached = self._cache.get(cache_key)
            if cached is not None:
                return cached, []

        tte_years = year_fraction(val_time, inst.expiry_utc, inst.day_count)
        rate = interpolate_zero_rate(curve, max(tte_years, 0.0))
        div_yield = curve.dividend_yields.get(inst.underlying, 0.0)
        implied_vol = interpolate_vol(vol_surface, max(tte_years, 0.0), inst.strike)

        unit_greeks, actual_method, flags = compute_instrument_greeks(
            inst,
            spot=spot_tick.spot,
            time_to_expiry_years=tte_years,
            rate=rate,
            dividend_yield=div_yield,
            vol=implied_vol,
            pricing_config=self.config.pricing,
            method=chosen_method,
            custom_price_fn=custom_fn,
        )
        pos_greeks = scale_unit_greeks_to_position(
            unit_greeks,
            quantity=position.quantity,
            contract_multiplier=inst.contract_multiplier,
            spot=spot_tick.spot,
        )

        is_frozen = False
        if alerts and allow_freeze:
            self._counters["stale_freezes"] += 1
            is_frozen = self.config.staleness.freeze_on_stale
            flags = list(
                dict.fromkeys(
                    [f for f in flags if f is not ReliabilityFlag.OK]
                    + [ReliabilityFlag.STALE_MARKET_DATA]
                    + ([ReliabilityFlag.FROZEN_SNAPSHOT] if is_frozen else [])
                )
            )

        result = PositionGreeksResult(
            position_id=position.position_id,
            account_id=position.account_id,
            instrument_id=inst.instrument_id,
            underlying=inst.underlying,
            expiry_date=inst.expiry_date_str,
            currency=inst.currency,
            quantity=position.quantity,
            contract_multiplier=inst.contract_multiplier,
            spot=spot_tick.spot,
            rate=rate,
            dividend_yield=div_yield,
            implied_vol=implied_vol,
            time_to_expiry_years=tte_years,
            pricing_method=actual_method,
            unit_greeks=unit_greeks,
            position_greeks=pos_greeks,
            flags=flags,
            market_version_key=market_version_key,
            computed_at_utc=val_time,
            is_frozen=is_frozen,
        )

        if use_cache and not alerts:
            self._cache.put(cache_key, result)
        return result, alerts

    def _recompute_positions_locked(self, position_ids: list[str]) -> None:
        affected_underlyings: set[str] = set()
        affected_currencies: set[str] = set()
        for pid in position_ids:
            pos = self._positions.get(pid)
            if pos is None:
                continue
            affected_underlyings.add(pos.instrument.underlying)
            affected_currencies.add(pos.instrument.currency)

        # Clear stale alerts for resources being refreshed before re-evaluating
        for aid in list(self._staleness_alerts.keys()):
            alert = self._staleness_alerts[aid]
            res = alert.resource
            if any(res == f"spot:{u}" or res == f"vol:{u}" for u in affected_underlyings) or any(
                res == f"curve:{c}" for c in affected_currencies
            ):
                del self._staleness_alerts[aid]

        for pid in sorted(position_ids):
            pos = self._positions.get(pid)
            if pos is None:
                continue
            try:
                result, alerts = self._compute_position_locked(pos)
            except MissingMarketDataError:
                # Position stays registered; will be priced once market data arrives
                continue
            self._aggregator.upsert(result)
            self._counters["positions_recomputed"] += 1
            for alert in alerts:
                self._staleness_alerts[alert.alert_id] = alert

    def _build_snapshot_locked(self) -> PortfolioSnapshot:
        positions = self._aggregator.positions_snapshot()
        by_underlying = self._aggregator.by_underlying_snapshot()
        by_underlying_expiry = self._aggregator.by_underlying_expiry_snapshot()
        totals = self._aggregator.totals_snapshot()
        market_versions = self._market.market_versions_summary()
        alerts = sorted(
            list(self._staleness_alerts.values()) + list(self._operational_alerts),
            key=lambda a: (a.detected_at_utc, a.alert_id),
        )
        has_frozen = any(p.is_frozen for p in positions.values())

        # Deterministic logical snapshot_id based on canonical portfolio & market state
        digest_payload = {
            "valuation_time_utc": self._valuation_time_utc.isoformat(),
            "engine_config_hash": self._config_hash,
            "market_versions": market_versions,
            "totals": totals.model_dump(mode="json"),
            "position_ids": list(positions.keys()),
            "has_frozen": has_frozen,
        }
        canonical = json.dumps(digest_payload, sort_keys=True, separators=(",", ":"))
        snapshot_id = f"SNAP-{self._snapshot_seq:06d}-{hashlib.sha256(canonical.encode('utf-8')).hexdigest()[:12]}"

        return PortfolioSnapshot(
            snapshot_id=snapshot_id,
            snapshot_seq=self._snapshot_seq,
            valuation_time_utc=self._valuation_time_utc,
            engine_config_hash=self._config_hash,
            market_versions=market_versions,
            totals=totals,
            by_underlying=by_underlying,
            by_underlying_expiry=by_underlying_expiry,
            positions=positions,
            alerts=alerts,
            has_frozen_positions=has_frozen,
        )

    def _publish_snapshot_locked(self) -> PortfolioSnapshot:
        self._snapshot_seq += 1
        self._latest_snapshot = self._build_snapshot_locked()
        return self._latest_snapshot

    def set_valuation_time(
        self,
        valuation_time_utc: datetime,
        *,
        recompute_all: bool = True,
        expected_snapshot_seq: int | None = None,
    ) -> PortfolioSnapshot:
        """Advance or set valuation timestamp and optionally recompute/re-check staleness."""
        t0 = time.perf_counter()
        val_utc = ensure_utc(valuation_time_utc)
        with self._lock:
            self._check_expected_seq_locked(expected_snapshot_seq)
            self._valuation_time_utc = val_utc
            if recompute_all and self._positions:
                self._staleness_alerts.clear()
                self._recompute_positions_locked(list(self._positions.keys()))
            snap = self._publish_snapshot_locked()
            self._update_latencies_ms.append((time.perf_counter() - t0) * 1000.0)
            return snap

    def upsert_position(
        self,
        position: OptionPosition,
        *,
        expected_snapshot_seq: int | None = None,
    ) -> PortfolioSnapshot:
        """Add or update an option position (handling quantity sign flips and multiplier changes)."""
        t0 = time.perf_counter()
        with self._lock:
            self._check_expected_seq_locked(expected_snapshot_seq)
            prev_pos = self._positions.get(position.position_id)
            if prev_pos is not None:
                old_u = prev_pos.instrument.underlying
                old_c = prev_pos.instrument.currency
                self._positions_by_underlying.get(old_u, set()).discard(position.position_id)
                self._positions_by_currency.get(old_c, set()).discard(position.position_id)
                self._cache.invalidate_position(position.position_id)

            self._positions[position.position_id] = position
            self._positions_by_underlying.setdefault(position.instrument.underlying, set()).add(
                position.position_id
            )
            self._positions_by_currency.setdefault(position.instrument.currency, set()).add(
                position.position_id
            )
            self._counters["positions_upserted"] += 1

            # Validate instrument upfront even if market data is not yet loaded
            if position.instrument.exercise_style.value != "EUROPEAN" and (
                position.instrument.instrument_id not in self._custom_pricers
            ):
                # Roll back registration before raising
                del self._positions[position.position_id]
                self._positions_by_underlying[position.instrument.underlying].discard(
                    position.position_id
                )
                self._positions_by_currency[position.instrument.currency].discard(
                    position.position_id
                )
                if prev_pos is not None:
                    self._positions[prev_pos.position_id] = prev_pos
                    self._positions_by_underlying[prev_pos.instrument.underlying].add(
                        prev_pos.position_id
                    )
                    self._positions_by_currency[prev_pos.instrument.currency].add(
                        prev_pos.position_id
                    )
                compute_instrument_greeks(
                    position.instrument,
                    spot=100.0,
                    time_to_expiry_years=0.5,
                    rate=0.05,
                    dividend_yield=0.0,
                    vol=0.2,
                    pricing_config=self.config.pricing,
                )

            self._recompute_positions_locked([position.position_id])
            snap = self._publish_snapshot_locked()
            elapsed_ms = (time.perf_counter() - t0) * 1000.0
            self._update_latencies_ms.append(elapsed_ms)
            logger.info(
                "position_upserted",
                extra={
                    "position_id": position.position_id,
                    "quantity": position.quantity,
                    "multiplier": position.instrument.contract_multiplier,
                    "snapshot_seq": snap.snapshot_seq,
                    "elapsed_ms": round(elapsed_ms, 4),
                },
            )
            return snap

    def remove_position(
        self,
        position_id: str,
        *,
        expected_snapshot_seq: int | None = None,
    ) -> PortfolioSnapshot:
        """Remove a position and incrementally subtract its sensitivities."""
        t0 = time.perf_counter()
        with self._lock:
            self._check_expected_seq_locked(expected_snapshot_seq)
            prev_pos = self._positions.pop(position_id, None)
            if prev_pos is not None:
                self._positions_by_underlying.get(prev_pos.instrument.underlying, set()).discard(
                    position_id
                )
                self._positions_by_currency.get(prev_pos.instrument.currency, set()).discard(
                    position_id
                )
                self._aggregator.remove(position_id)
                self._cache.invalidate_position(position_id)
                self._counters["positions_removed"] += 1
            snap = self._publish_snapshot_locked()
            self._update_latencies_ms.append((time.perf_counter() - t0) * 1000.0)
            return snap

    def ingest_spot_tick(
        self,
        tick: SpotTick,
        *,
        advance_valuation_time: bool = False,
        expected_snapshot_seq: int | None = None,
    ) -> tuple[TickIngestOutcome, PortfolioSnapshot]:
        """Ingest a spot tick with dedup/sequence enforcement and incremental recompute."""
        t0 = time.perf_counter()
        with self._lock:
            self._check_expected_seq_locked(expected_snapshot_seq)
            if advance_valuation_time and tick.event_time_utc > self._valuation_time_utc:
                self._valuation_time_utc = tick.event_time_utc

            outcome = self._market.ingest_spot_tick(tick, self._valuation_time_utc)
            if not outcome.accepted:
                if outcome.reason == "DUPLICATE_TICK":
                    self._counters["duplicate_ticks_ignored"] += 1
                else:
                    self._counters["out_of_order_ticks_ignored"] += 1
                if outcome.alert is not None:
                    self._operational_alerts.append(outcome.alert)
                snap = self._publish_snapshot_locked()
                self._update_latencies_ms.append((time.perf_counter() - t0) * 1000.0)
                return outcome, snap

            self._counters["ticks_accepted"] += 1
            affected = list(self._positions_by_underlying.get(tick.underlying, ()))
            if affected:
                self._recompute_positions_locked(affected)
            snap = self._publish_snapshot_locked()
            self._update_latencies_ms.append((time.perf_counter() - t0) * 1000.0)
            return outcome, snap

    def upsert_yield_curve(
        self,
        curve: YieldCurveSnapshot,
        *,
        expected_snapshot_seq: int | None = None,
    ) -> PortfolioSnapshot:
        """Update yield curve version and incrementally recompute positions in that currency."""
        t0 = time.perf_counter()
        with self._lock:
            self._check_expected_seq_locked(expected_snapshot_seq)
            self._market.upsert_yield_curve(curve)
            self._counters["curves_updated"] += 1
            affected = list(self._positions_by_currency.get(curve.currency, ()))
            if affected:
                self._recompute_positions_locked(affected)
            snap = self._publish_snapshot_locked()
            self._update_latencies_ms.append((time.perf_counter() - t0) * 1000.0)
            return snap

    def upsert_vol_surface(
        self,
        surface: VolSurfaceSnapshot,
        *,
        expected_snapshot_seq: int | None = None,
    ) -> PortfolioSnapshot:
        """Update volatility surface version and incrementally recompute positions on that underlying."""
        t0 = time.perf_counter()
        with self._lock:
            self._check_expected_seq_locked(expected_snapshot_seq)
            self._market.upsert_vol_surface(surface)
            self._counters["vols_updated"] += 1
            affected = list(self._positions_by_underlying.get(surface.underlying, ()))
            if affected:
                self._recompute_positions_locked(affected)
            snap = self._publish_snapshot_locked()
            self._update_latencies_ms.append((time.perf_counter() - t0) * 1000.0)
            return snap

    def get_snapshot(self) -> PortfolioSnapshot:
        """Return the latest atomic, immutable PortfolioSnapshot."""
        return self._latest_snapshot

    def get_alerts(self) -> list[StaleDataAlert]:
        return list(self._latest_snapshot.alerts)

    def clear_operational_alerts(self) -> None:
        with self._lock:
            self._operational_alerts.clear()
            self._latest_snapshot = self._build_snapshot_locked()

    def reconcile(self) -> ReconciliationReport:
        """Compare incremental aggregates against a clean uncached full recompute from scratch."""
        with self._lock:
            self._counters["reconciliations_run"] += 1
            snap = self._latest_snapshot
            fresh_results: list[PositionGreeksResult] = []
            for pid, pos in sorted(self._positions.items()):
                live_res = snap.positions.get(pid)
                if live_res is None:
                    continue
                if live_res.is_frozen:
                    # Frozen positions intentionally preserve last reliable values; reconcile aggregation sum
                    fresh_results.append(live_res)
                else:
                    recomputed, _ = self._compute_position_locked(
                        pos,
                        use_cache=False,
                        allow_freeze=False,
                    )
                    fresh_results.append(recomputed)

            ref_totals, ref_by_u, ref_by_ue = full_recompute_aggregates(fresh_results)
            abs_tol = self.config.reconciliation.abs_tolerance
            rel_tol = self.config.reconciliation.rel_tolerance

            mismatches: list[BucketDiff] = []
            global_max_abs = 0.0
            global_max_rel = 0.0
            buckets_checked = 0

            def _compare_bucket_maps(
                label: str,
                inc_map: dict[str, AggregatedGreeksBucket],
                ref_map: dict[str, AggregatedGreeksBucket],
            ) -> None:
                nonlocal global_max_abs, global_max_rel, buckets_checked
                all_keys = sorted(set(inc_map.keys()) | set(ref_map.keys()))
                for key in all_keys:
                    buckets_checked += 1
                    inc_b = inc_map.get(key)
                    ref_b = ref_map.get(key)
                    if inc_b is None or ref_b is None:
                        global_max_abs = float("inf")
                        global_max_rel = float("inf")
                        mismatches.append(
                            BucketDiff(
                                bucket_key=f"{label}:{key}",
                                max_abs_diff=float("inf"),
                                max_rel_diff=float("inf"),
                                field_diffs={"missing_bucket": 1.0},
                            )
                        )
                        continue
                    field_diffs: dict[str, float] = {}
                    b_max_abs = 0.0
                    b_max_rel = 0.0
                    for fname in _AGG_NUMERIC_FIELDS:
                        iv = getattr(inc_b, fname)
                        rv = getattr(ref_b, fname)
                        ad = abs(iv - rv)
                        denom = max(abs(rv), 1.0)
                        rd = ad / denom
                        if ad > b_max_abs:
                            b_max_abs = ad
                        if rd > b_max_rel:
                            b_max_rel = rd
                        if ad > abs_tol and rd > rel_tol:
                            field_diffs[fname] = ad
                    if inc_b.position_count != ref_b.position_count:
                        field_diffs["position_count"] = float(abs(inc_b.position_count - ref_b.position_count))
                    global_max_abs = max(global_max_abs, b_max_abs)
                    global_max_rel = max(global_max_rel, b_max_rel)
                    if field_diffs:
                        mismatches.append(
                            BucketDiff(
                                bucket_key=f"{label}:{key}",
                                max_abs_diff=b_max_abs,
                                max_rel_diff=b_max_rel,
                                field_diffs=field_diffs,
                            )
                        )

            _compare_bucket_maps("underlying", snap.by_underlying, ref_by_u)
            _compare_bucket_maps("underlying_expiry", snap.by_underlying_expiry, ref_by_ue)

            # Check portfolio totals too
            buckets_checked += 1
            tot_field_diffs: dict[str, float] = {}
            t_max_abs = 0.0
            t_max_rel = 0.0
            for fname in ("pv", "dollar_delta", "dollar_gamma_1pct", "vega", "theta", "rho"):
                iv = getattr(snap.totals, fname)
                rv = getattr(ref_totals, fname)
                ad = abs(iv - rv)
                rd = ad / max(abs(rv), 1.0)
                t_max_abs = max(t_max_abs, ad)
                t_max_rel = max(t_max_rel, rd)
                if ad > abs_tol and rd > rel_tol:
                    tot_field_diffs[fname] = ad
            global_max_abs = max(global_max_abs, t_max_abs)
            global_max_rel = max(global_max_rel, t_max_rel)
            if tot_field_diffs:
                mismatches.append(
                    BucketDiff(
                        bucket_key="portfolio:TOTAL",
                        max_abs_diff=t_max_abs,
                        max_rel_diff=t_max_rel,
                        field_diffs=tot_field_diffs,
                    )
                )

            return ReconciliationReport(
                reconciliation_id=f"RECON-{snap.snapshot_seq:06d}-{self._counters['reconciliations_run']:04d}",
                snapshot_seq=snap.snapshot_seq,
                valuation_time_utc=snap.valuation_time_utc,
                passed=len(mismatches) == 0,
                abs_tolerance=abs_tol,
                rel_tolerance=rel_tol,
                max_abs_diff=global_max_abs,
                max_rel_diff=global_max_rel,
                total_buckets_checked=buckets_checked,
                mismatched_buckets=mismatches,
            )

    def run_scenarios(
        self,
        shocks: list[ScenarioShock],
        *,
        max_workers: int | None = None,
    ) -> list[ScenarioResult]:
        """Parallel scenario recomputation over an immutable point-in-time portfolio snapshot."""
        if not shocks:
            return []

        with self._lock:
            base_snap = self._latest_snapshot
            positions_copy = list(self._positions.values())
            market_copy = self._market.clone()
            base_val_time = self._valuation_time_utc
            custom_pricers_copy = dict(self._custom_pricers)

        base_pv = base_snap.totals.pv
        workers = max_workers or self.config.scenarios.max_workers

        def _eval_one(shock: ScenarioShock) -> ScenarioResult:
            t0 = time.perf_counter()
            shocked_market = market_copy.apply_shock(
                spot_shock_pct=shock.spot_shock_pct,
                per_underlying_spot_shock_pct={
                    k.upper(): v for k, v in shock.per_underlying_spot_shock_pct.items()
                },
                vol_shift_abs=shock.vol_shift_abs,
                rate_shift_bps=shock.rate_shift_bps,
                shock_tag=shock.scenario_id,
            )
            shock_val_time = base_val_time + timedelta(days=shock.time_step_days)
            results: list[PositionGreeksResult] = []
            for pos in positions_copy:
                inst = pos.instrument
                try:
                    spot_tick = shocked_market.get_spot(inst.underlying)
                    curve = shocked_market.get_curve(inst.currency)
                    vol_surf = shocked_market.get_vol_surface(inst.underlying)
                except MissingMarketDataError:
                    continue
                tte = year_fraction(shock_val_time, inst.expiry_utc, inst.day_count)
                rate = interpolate_zero_rate(curve, max(tte, 0.0))
                div_q = curve.dividend_yields.get(inst.underlying, 0.0)
                iv = interpolate_vol(vol_surf, max(tte, 0.0), inst.strike)
                custom_fn = custom_pricers_copy.get(inst.instrument_id)
                unit, method, flags = compute_instrument_greeks(
                    inst,
                    spot=spot_tick.spot,
                    time_to_expiry_years=tte,
                    rate=rate,
                    dividend_yield=div_q,
                    vol=iv,
                    pricing_config=self.config.pricing,
                    custom_price_fn=custom_fn,
                )
                pg = scale_unit_greeks_to_position(
                    unit,
                    quantity=pos.quantity,
                    contract_multiplier=inst.contract_multiplier,
                    spot=spot_tick.spot,
                )
                results.append(
                    PositionGreeksResult(
                        position_id=pos.position_id,
                        account_id=pos.account_id,
                        instrument_id=inst.instrument_id,
                        underlying=inst.underlying,
                        expiry_date=inst.expiry_date_str,
                        currency=inst.currency,
                        quantity=pos.quantity,
                        contract_multiplier=inst.contract_multiplier,
                        spot=spot_tick.spot,
                        rate=rate,
                        dividend_yield=div_q,
                        implied_vol=iv,
                        time_to_expiry_years=tte,
                        pricing_method=method,
                        unit_greeks=unit,
                        position_greeks=pg,
                        flags=flags,
                        market_version_key=shocked_market.position_version_key(
                            inst.underlying, inst.currency
                        ),
                        computed_at_utc=shock_val_time,
                        is_frozen=False,
                    )
                )

            totals, by_u, _ = full_recompute_aggregates(results)
            elapsed_ms = (time.perf_counter() - t0) * 1000.0
            return ScenarioResult(
                scenario_id=shock.scenario_id,
                shock=shock,
                base_pv=base_pv,
                scenario_pv=totals.pv,
                pnl=totals.pv - base_pv,
                totals=totals,
                by_underlying=by_u,
                elapsed_ms=round(elapsed_ms, 4),
            )

        if workers <= 1 or len(shocks) == 1:
            out = [_eval_one(s) for s in shocks]
        else:
            with ThreadPoolExecutor(max_workers=min(workers, len(shocks))) as pool:
                out = list(pool.map(_eval_one, shocks))

        with self._lock:
            self._counters["scenarios_evaluated"] += len(out)
        return out

    def generate_stochastic_scenarios(
        self,
        count: int,
        *,
        seed: int | None = None,
        spot_sigma: float = 0.05,
        vol_sigma: float = 0.03,
        rate_bps_sigma: float = 25.0,
    ) -> list[ScenarioShock]:
        """Generate reproducible fixed-seed stochastic stress shocks."""
        if count <= 0:
            raise ValidationError("count must be > 0")
        rng = random.Random(self.config.scenarios.default_seed if seed is None else seed)
        shocks: list[ScenarioShock] = []
        for idx in range(count):
            spot_shock = max(-0.5, min(0.5, rng.gauss(0.0, spot_sigma)))
            vol_shift = max(-0.2, min(0.5, rng.gauss(0.0, vol_sigma)))
            rate_bps = max(-500.0, min(500.0, rng.gauss(0.0, rate_bps_sigma)))
            shocks.append(
                ScenarioShock(
                    scenario_id=f"MC-{idx + 1:04d}",
                    spot_shock_pct=round(spot_shock, 6),
                    vol_shift_abs=round(vol_shift, 6),
                    rate_shift_bps=round(rate_bps, 4),
                    time_step_days=0.0,
                )
            )
        return shocks

    def replay_events(self, events: list[dict[str, Any]]) -> PortfolioSnapshot:
        """Deterministically replay a sequence of canonical events."""
        for idx, ev in enumerate(events):
            ev_type = ev.get("type")
            payload = ev.get("payload")
            if ev_type == "spot_tick":
                self.ingest_spot_tick(SpotTick.model_validate(payload))
            elif ev_type == "position_upsert":
                self.upsert_position(OptionPosition.model_validate(payload))
            elif ev_type == "position_remove":
                self.remove_position(str(payload["position_id"]))
            elif ev_type == "yield_curve":
                self.upsert_yield_curve(YieldCurveSnapshot.model_validate(payload))
            elif ev_type == "vol_surface":
                self.upsert_vol_surface(VolSurfaceSnapshot.model_validate(payload))
            elif ev_type == "valuation_time":
                self.set_valuation_time(datetime.fromisoformat(payload["valuation_time_utc"].replace("Z", "+00:00")))
            else:
                raise ValidationError(
                    f"Unknown replay event type at index {idx}: {ev_type}",
                    details={"index": idx, "event_type": ev_type},
                )
        return self.get_snapshot()

    def diagnostics(self) -> dict[str, Any]:
        """Return structured counters, cache metrics, and p50/p95/p99 update latencies."""
        with self._lock:
            latencies = sorted(self._update_latencies_ms)
            return {
                "engine_id": self.config.engine_id,
                "engine_config_hash": self._config_hash,
                "snapshot_seq": self._snapshot_seq,
                "active_positions": len(self._positions),
                "active_staleness_alerts": len(self._staleness_alerts),
                "operational_alerts": len(self._operational_alerts),
                "counters": dict(self._counters),
                "cache": self._cache.stats(),
                "update_latency_ms": {
                    "count": len(latencies),
                    "p50": round(_percentile(latencies, 50.0), 4),
                    "p95": round(_percentile(latencies, 95.0), 4),
                    "p99": round(_percentile(latencies, 99.0), 4),
                    "max": round(latencies[-1], 4) if latencies else 0.0,
                    "mean": round(sum(latencies) / len(latencies), 4) if latencies else 0.0,
                },
            }
