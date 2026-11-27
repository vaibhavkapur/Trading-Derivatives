"""Incremental O(1) portfolio Greeks aggregator by underlying and underlying+expiry."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field

from real_time_greeks_engine.models import (
    AggregatedGreeksBucket,
    PortfolioTotals,
    PositionGreeksResult,
    ReliabilityFlag,
)

_UNRELIABLE_FLAGS = frozenset(
    {
        ReliabilityFlag.NEAR_EXPIRY,
        ReliabilityFlag.LOW_VOL,
        ReliabilityFlag.EXTREME_MONEYNESS,
        ReliabilityFlag.STALE_MARKET_DATA,
        ReliabilityFlag.FROZEN_SNAPSHOT,
    }
)


def _is_unreliable(result: PositionGreeksResult) -> bool:
    return any(f in _UNRELIABLE_FLAGS for f in result.flags)


@dataclass
class _MutableBucket:
    bucket_key: str
    underlying: str
    expiry_date: str | None
    currency: str
    position_count: int = 0
    pv: float = 0.0
    delta_shares: float = 0.0
    dollar_delta: float = 0.0
    gamma_shares: float = 0.0
    dollar_gamma_1pct: float = 0.0
    vega: float = 0.0
    theta: float = 0.0
    rho: float = 0.0
    vanna: float = 0.0
    charm: float = 0.0
    volga: float = 0.0
    unreliable_count: int = 0
    frozen_count: int = 0
    flag_counts: Counter[ReliabilityFlag] = field(default_factory=Counter)

    def apply(self, result: PositionGreeksResult, sign: int) -> None:
        pg = result.position_greeks
        self.position_count += sign
        self.pv += sign * pg.pv
        self.delta_shares += sign * pg.delta_shares
        self.dollar_delta += sign * pg.dollar_delta
        self.gamma_shares += sign * pg.gamma_shares
        self.dollar_gamma_1pct += sign * pg.dollar_gamma_1pct
        self.vega += sign * pg.vega
        self.theta += sign * pg.theta
        self.rho += sign * pg.rho
        self.vanna += sign * pg.vanna
        self.charm += sign * pg.charm
        self.volga += sign * pg.volga
        if _is_unreliable(result):
            self.unreliable_count += sign
        if result.is_frozen:
            self.frozen_count += sign
        for fl in result.flags:
            if fl is ReliabilityFlag.OK:
                continue
            self.flag_counts[fl] += sign
            if self.flag_counts[fl] <= 0:
                del self.flag_counts[fl]

    def to_model(self) -> AggregatedGreeksBucket:
        active_flags = sorted(self.flag_counts.keys(), key=lambda f: f.value)
        if not active_flags:
            active_flags = [ReliabilityFlag.OK]
        return AggregatedGreeksBucket(
            bucket_key=self.bucket_key,
            underlying=self.underlying,
            expiry_date=self.expiry_date,
            currency=self.currency,
            position_count=self.position_count,
            pv=self.pv,
            delta_shares=self.delta_shares,
            dollar_delta=self.dollar_delta,
            gamma_shares=self.gamma_shares,
            dollar_gamma_1pct=self.dollar_gamma_1pct,
            vega=self.vega,
            theta=self.theta,
            rho=self.rho,
            vanna=self.vanna,
            charm=self.charm,
            volga=self.volga,
            unreliable_count=self.unreliable_count,
            frozen_count=self.frozen_count,
            flags=active_flags,
        )


class IncrementalGreeksAggregator:
    """Maintains incremental aggregates by underlying, by (underlying, expiry), and portfolio total."""

    def __init__(self) -> None:
        self._positions: dict[str, PositionGreeksResult] = {}
        self._by_underlying: dict[str, _MutableBucket] = {}
        self._by_underlying_expiry: dict[str, _MutableBucket] = {}
        self._total_count: int = 0
        self._total_pv: float = 0.0
        self._total_dollar_delta: float = 0.0
        self._total_dollar_gamma_1pct: float = 0.0
        self._total_vega: float = 0.0
        self._total_theta: float = 0.0
        self._total_rho: float = 0.0
        self._total_unreliable: int = 0
        self._total_frozen: int = 0

    @staticmethod
    def expiry_bucket_key(underlying: str, expiry_date: str) -> str:
        return f"{underlying}|{expiry_date}"

    def _apply_totals(self, result: PositionGreeksResult, sign: int) -> None:
        pg = result.position_greeks
        self._total_count += sign
        self._total_pv += sign * pg.pv
        self._total_dollar_delta += sign * pg.dollar_delta
        self._total_dollar_gamma_1pct += sign * pg.dollar_gamma_1pct
        self._total_vega += sign * pg.vega
        self._total_theta += sign * pg.theta
        self._total_rho += sign * pg.rho
        if _is_unreliable(result):
            self._total_unreliable += sign
        if result.is_frozen:
            self._total_frozen += sign

    def _remove_contribution(self, prev: PositionGreeksResult) -> None:
        u_key = prev.underlying
        ue_key = self.expiry_bucket_key(prev.underlying, prev.expiry_date)

        u_bucket = self._by_underlying[u_key]
        u_bucket.apply(prev, -1)
        if u_bucket.position_count <= 0:
            del self._by_underlying[u_key]

        ue_bucket = self._by_underlying_expiry[ue_key]
        ue_bucket.apply(prev, -1)
        if ue_bucket.position_count <= 0:
            del self._by_underlying_expiry[ue_key]

        self._apply_totals(prev, -1)

    def _add_contribution(self, curr: PositionGreeksResult) -> None:
        u_key = curr.underlying
        ue_key = self.expiry_bucket_key(curr.underlying, curr.expiry_date)

        if u_key not in self._by_underlying:
            self._by_underlying[u_key] = _MutableBucket(
                bucket_key=u_key,
                underlying=curr.underlying,
                expiry_date=None,
                currency=curr.currency,
            )
        self._by_underlying[u_key].apply(curr, +1)

        if ue_key not in self._by_underlying_expiry:
            self._by_underlying_expiry[ue_key] = _MutableBucket(
                bucket_key=ue_key,
                underlying=curr.underlying,
                expiry_date=curr.expiry_date,
                currency=curr.currency,
            )
        self._by_underlying_expiry[ue_key].apply(curr, +1)

        self._apply_totals(curr, +1)

    def upsert(self, result: PositionGreeksResult) -> None:
        """Incrementally update aggregates for a single position in O(1)."""
        prev = self._positions.get(result.position_id)
        if prev is not None:
            self._remove_contribution(prev)
        self._positions[result.position_id] = result
        self._add_contribution(result)

    def remove(self, position_id: str) -> PositionGreeksResult | None:
        """Remove a position and subtract its contribution in O(1)."""
        prev = self._positions.pop(position_id, None)
        if prev is not None:
            self._remove_contribution(prev)
        return prev

    def get_position_result(self, position_id: str) -> PositionGreeksResult | None:
        return self._positions.get(position_id)

    def positions_snapshot(self) -> dict[str, PositionGreeksResult]:
        return {k: self._positions[k] for k in sorted(self._positions.keys())}

    def by_underlying_snapshot(self) -> dict[str, AggregatedGreeksBucket]:
        return {k: self._by_underlying[k].to_model() for k in sorted(self._by_underlying.keys())}

    def by_underlying_expiry_snapshot(self) -> dict[str, AggregatedGreeksBucket]:
        return {
            k: self._by_underlying_expiry[k].to_model()
            for k in sorted(self._by_underlying_expiry.keys())
        }

    def totals_snapshot(self) -> PortfolioTotals:
        if self._total_count == 0:
            return PortfolioTotals(
                position_count=0,
                pv=0.0,
                dollar_delta=0.0,
                dollar_gamma_1pct=0.0,
                vega=0.0,
                theta=0.0,
                rho=0.0,
                unreliable_count=0,
                frozen_count=0,
            )
        return PortfolioTotals(
            position_count=self._total_count,
            pv=self._total_pv,
            dollar_delta=self._total_dollar_delta,
            dollar_gamma_1pct=self._total_dollar_gamma_1pct,
            vega=self._total_vega,
            theta=self._total_theta,
            rho=self._total_rho,
            unreliable_count=self._total_unreliable,
            frozen_count=self._total_frozen,
        )


def full_recompute_aggregates(
    results: list[PositionGreeksResult],
) -> tuple[PortfolioTotals, dict[str, AggregatedGreeksBucket], dict[str, AggregatedGreeksBucket]]:
    """Compute portfolio totals and buckets completely from scratch (oracle for reconciliation)."""
    fresh = IncrementalGreeksAggregator()
    for res in results:
        fresh.upsert(res)
    return (
        fresh.totals_snapshot(),
        fresh.by_underlying_snapshot(),
        fresh.by_underlying_expiry_snapshot(),
    )
