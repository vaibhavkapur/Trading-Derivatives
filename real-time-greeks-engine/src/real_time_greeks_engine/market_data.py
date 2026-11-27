"""Pure domain market data interpolation, tick deduplication, and staleness evaluation."""

from __future__ import annotations

import bisect
from dataclasses import dataclass
from datetime import datetime

from real_time_greeks_engine.config import StalenessConfig
from real_time_greeks_engine.errors import MissingMarketDataError
from real_time_greeks_engine.models import (
    AlertSeverity,
    DayCountConvention,
    SpotTick,
    StaleDataAlert,
    VolSurfaceSnapshot,
    YieldCurveSnapshot,
)


def year_fraction(start_utc: datetime, end_utc: datetime, day_count: DayCountConvention) -> float:
    """Compute exact year fraction between valuation time and expiry using instrument convention."""
    delta_seconds = (end_utc - start_utc).total_seconds()
    return delta_seconds / (day_count.days_per_year * 86400.0)


def interpolate_zero_rate(curve: YieldCurveSnapshot, tenor_years: float) -> float:
    """Piecewise linear interpolation on continuous zero rates with flat boundary extrapolation."""
    tenors = [p.tenor_years for p in curve.points]
    rates = [p.zero_rate for p in curve.points]
    if tenor_years <= tenors[0]:
        return rates[0]
    if tenor_years >= tenors[-1]:
        return rates[-1]
    idx = bisect.bisect_right(tenors, tenor_years) - 1
    t0, t1 = tenors[idx], tenors[idx + 1]
    r0, r1 = rates[idx], rates[idx + 1]
    weight = (tenor_years - t0) / (t1 - t0)
    return r0 + weight * (r1 - r0)


def interpolate_vol(surface: VolSurfaceSnapshot, time_to_expiry_years: float, strike: float) -> float:
    """Deterministic 2D bilinear interpolation on (expiry_years, strike) with flat boundary clamping."""
    expiries = surface.expiries_years
    strikes = surface.strikes
    vols = surface.vols

    def _bracket(axis: list[float], val: float) -> tuple[int, int, float]:
        if val <= axis[0]:
            return 0, 0, 0.0
        if val >= axis[-1]:
            last = len(axis) - 1
            return last, last, 0.0
        i0 = bisect.bisect_right(axis, val) - 1
        i1 = i0 + 1
        w = (val - axis[i0]) / (axis[i1] - axis[i0])
        return i0, i1, w

    e0, e1, we = _bracket(expiries, time_to_expiry_years)
    k0, k1, wk = _bracket(strikes, strike)

    v00 = vols[e0][k0]
    v01 = vols[e0][k1]
    v10 = vols[e1][k0]
    v11 = vols[e1][k1]

    v0 = v00 + wk * (v01 - v00)
    v1 = v10 + wk * (v11 - v10)
    return v0 + we * (v1 - v0)


@dataclass(frozen=True)
class TickIngestOutcome:
    accepted: bool
    reason: str
    alert: StaleDataAlert | None = None


class MarketDataStore:
    """Versioned in-memory market state with tick deduplication and staleness checks."""

    def __init__(self, staleness_config: StalenessConfig) -> None:
        self._staleness = staleness_config
        self._spots: dict[str, SpotTick] = {}
        self._seen_tick_ids: set[str] = set()
        self._curves: dict[str, YieldCurveSnapshot] = {}
        self._vols: dict[str, VolSurfaceSnapshot] = {}

    def clone(self) -> MarketDataStore:
        """Create an independent shallow-copied snapshot for scenario evaluation."""
        cloned = MarketDataStore(self._staleness)
        cloned._spots = dict(self._spots)
        cloned._seen_tick_ids = set(self._seen_tick_ids)
        cloned._curves = dict(self._curves)
        cloned._vols = dict(self._vols)
        return cloned

    def ingest_spot_tick(self, tick: SpotTick, detected_at_utc: datetime) -> TickIngestOutcome:
        """Accept a spot tick only if not duplicate and strictly advancing sequence_no."""
        if tick.tick_id in self._seen_tick_ids:
            alert = StaleDataAlert(
                alert_id=f"DUP-{tick.underlying}-{tick.tick_id}",
                severity=AlertSeverity.WARNING,
                code="DUPLICATE_TICK",
                resource=f"spot:{tick.underlying}",
                message=f"Ignored duplicate tick_id={tick.tick_id} (seq={tick.sequence_no}) for {tick.underlying}.",
                detected_at_utc=detected_at_utc,
            )
            return TickIngestOutcome(accepted=False, reason="DUPLICATE_TICK", alert=alert)

        prev = self._spots.get(tick.underlying)
        if prev is not None and tick.sequence_no <= prev.sequence_no:
            code = "DUPLICATE_TICK" if tick.sequence_no == prev.sequence_no else "OUT_OF_ORDER_TICK"
            alert = StaleDataAlert(
                alert_id=f"SEQ-{tick.underlying}-{tick.sequence_no}",
                severity=AlertSeverity.WARNING,
                code=code,
                resource=f"spot:{tick.underlying}",
                message=(
                    f"Ignored {code.lower()} for {tick.underlying}: "
                    f"received seq={tick.sequence_no} <= latest seq={prev.sequence_no}."
                ),
                detected_at_utc=detected_at_utc,
            )
            return TickIngestOutcome(accepted=False, reason=code, alert=alert)

        self._spots[tick.underlying] = tick
        self._seen_tick_ids.add(tick.tick_id)
        return TickIngestOutcome(accepted=True, reason="ACCEPTED")

    def upsert_yield_curve(self, curve: YieldCurveSnapshot) -> None:
        self._curves[curve.currency] = curve

    def upsert_vol_surface(self, surface: VolSurfaceSnapshot) -> None:
        self._vols[surface.underlying] = surface

    def get_spot(self, underlying: str) -> SpotTick:
        key = underlying.upper()
        if key not in self._spots:
            raise MissingMarketDataError(
                f"No spot tick available for underlying {key}",
                details={"underlying": key},
            )
        return self._spots[key]

    def get_curve(self, currency: str) -> YieldCurveSnapshot:
        key = currency.upper()
        if key not in self._curves:
            raise MissingMarketDataError(
                f"No yield curve available for currency {key}",
                details={"currency": key},
            )
        return self._curves[key]

    def get_vol_surface(self, underlying: str) -> VolSurfaceSnapshot:
        key = underlying.upper()
        if key not in self._vols:
            raise MissingMarketDataError(
                f"No vol surface available for underlying {key}",
                details={"underlying": key},
            )
        return self._vols[key]

    def check_staleness_for_position(
        self,
        *,
        underlying: str,
        currency: str,
        valuation_time_utc: datetime,
    ) -> list[StaleDataAlert]:
        """Evaluate spot, curve, and vol surface ages against configured/snapshot TTLs."""
        alerts: list[StaleDataAlert] = []
        spot = self.get_spot(underlying)
        curve = self.get_curve(currency)
        vol = self.get_vol_surface(underlying)

        spot_age = (valuation_time_utc - spot.event_time_utc).total_seconds()
        spot_ttl = self._staleness.spot_ttl_seconds
        if spot_age > spot_ttl:
            alerts.append(
                StaleDataAlert(
                    alert_id=f"STALE-SPOT-{underlying}-{spot.sequence_no}",
                    severity=AlertSeverity.CRITICAL,
                    code="STALE_SPOT",
                    resource=f"spot:{underlying}",
                    age_seconds=round(spot_age, 4),
                    max_age_seconds=spot_ttl,
                    message=(
                        f"Spot for {underlying} (seq={spot.sequence_no}) is stale: "
                        f"age={spot_age:.2f}s > ttl={spot_ttl:.2f}s."
                    ),
                    detected_at_utc=valuation_time_utc,
                )
            )

        curve_age = (valuation_time_utc - curve.as_of_utc).total_seconds()
        curve_ttl = curve.ttl_seconds if curve.ttl_seconds is not None else self._staleness.curve_ttl_seconds
        if curve_age > curve_ttl:
            alerts.append(
                StaleDataAlert(
                    alert_id=f"STALE-CURVE-{currency}-{curve.curve_version}",
                    severity=AlertSeverity.CRITICAL,
                    code="STALE_CURVE",
                    resource=f"curve:{currency}",
                    age_seconds=round(curve_age, 4),
                    max_age_seconds=curve_ttl,
                    message=(
                        f"Yield curve {curve.curve_version} ({currency}) is stale: "
                        f"age={curve_age:.2f}s > ttl={curve_ttl:.2f}s."
                    ),
                    detected_at_utc=valuation_time_utc,
                )
            )

        vol_age = (valuation_time_utc - vol.as_of_utc).total_seconds()
        vol_ttl = vol.ttl_seconds if vol.ttl_seconds is not None else self._staleness.vol_ttl_seconds
        if vol_age > vol_ttl:
            alerts.append(
                StaleDataAlert(
                    alert_id=f"STALE-VOL-{underlying}-{vol.vol_version}",
                    severity=AlertSeverity.CRITICAL,
                    code="STALE_VOL_SURFACE",
                    resource=f"vol:{underlying}",
                    age_seconds=round(vol_age, 4),
                    max_age_seconds=vol_ttl,
                    message=(
                        f"Vol surface {vol.vol_version} ({underlying}) is stale: "
                        f"age={vol_age:.2f}s > ttl={vol_ttl:.2f}s."
                    ),
                    detected_at_utc=valuation_time_utc,
                )
            )

        return alerts

    def market_versions_summary(self) -> dict[str, dict[str, str | int | float]]:
        return {
            "spots": {
                u: {"tick_id": t.tick_id, "sequence_no": t.sequence_no, "spot": t.spot}
                for u, t in sorted(self._spots.items())
            },
            "curves": {c: cv.curve_version for c, cv in sorted(self._curves.items())},
            "vol_surfaces": {u: vs.vol_version for u, vs in sorted(self._vols.items())},
        }

    def position_version_key(self, underlying: str, currency: str) -> str:
        spot = self.get_spot(underlying)
        curve = self.get_curve(currency)
        vol = self.get_vol_surface(underlying)
        return f"S:{spot.sequence_no}:{spot.spot:.8f}|C:{curve.curve_version}|V:{vol.vol_version}"

    def apply_shock(
        self,
        *,
        spot_shock_pct: float,
        per_underlying_spot_shock_pct: dict[str, float],
        vol_shift_abs: float,
        rate_shift_bps: float,
        shock_tag: str,
    ) -> MarketDataStore:
        """Return a new MarketDataStore with shocked spots, curves, and vol surfaces."""
        shocked = MarketDataStore(self._staleness)
        shocked._seen_tick_ids = set(self._seen_tick_ids)

        for u, tick in self._spots.items():
            pct = per_underlying_spot_shock_pct.get(u, spot_shock_pct)
            new_spot = max(tick.spot * (1.0 + pct), 1e-6)
            shocked._spots[u] = tick.model_copy(
                update={
                    "tick_id": f"{tick.tick_id}#{shock_tag}",
                    "spot": new_spot,
                    "bid": None,
                    "ask": None,
                }
            )

        rate_shift = rate_shift_bps * 1e-4
        for c, curve in self._curves.items():
            shifted_points = [
                p.model_copy(update={"zero_rate": p.zero_rate + rate_shift})
                for p in curve.points
            ]
            shocked._curves[c] = curve.model_copy(
                update={
                    "curve_version": f"{curve.curve_version}#{shock_tag}",
                    "points": shifted_points,
                }
            )

        for u, surf in self._vols.items():
            shifted_vols = [
                [min(max(v + vol_shift_abs, 1e-4), 5.0) for v in row]
                for row in surf.vols
            ]
            shocked._vols[u] = surf.model_copy(
                update={
                    "vol_version": f"{surf.vol_version}#{shock_tag}",
                    "vols": shifted_vols,
                }
            )
        return shocked
