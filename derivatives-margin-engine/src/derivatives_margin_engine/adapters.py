"""Independent runnable integration adapters for upstream pricing and Greeks feeds.

These adapters define explicit boundary contracts so neighboring projects in
Trading-Derivatives (e.g., derivatives-pricing-engine, real-time-greeks-engine,
options-hedging-simulator) can supply settlement prices or scenario shocks without
tight coupling or copied analytics implementations.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Mapping, Protocol, Sequence

from derivatives_margin_engine.models import (
    Position,
    Scenario,
    ScenarioSet,
    SettlementPrice,
    Shock,
    ShockType,
    SUPPORTED_SCHEMA_VERSION,
    TargetType,
    to_decimal,
)


class PricingFeedAdapter(Protocol):
    """Contract for external pricing engines supplying end-of-day settlement prices."""

    def get_settlement_prices(
        self,
        valuation_date: date,
        instrument_ids: Sequence[str],
        revision: int = 1,
    ) -> Sequence[SettlementPrice]:
        ...


class ScenarioFeedAdapter(Protocol):
    """Contract for external risk/Greeks engines supplying price shock scenarios."""

    def build_scenario_set(self, scenario_set_id: str) -> ScenarioSet:
        ...


@dataclass(frozen=True)
class StaticPricingAdapter:
    """Runnable in-memory adapter converting an external price dictionary to SettlementPrices."""

    quotes: Mapping[str, Mapping[str, str]]

    def get_settlement_prices(
        self,
        valuation_date: date,
        instrument_ids: Sequence[str],
        revision: int = 1,
    ) -> Sequence[SettlementPrice]:
        result = []
        for inst_id in sorted(set(instrument_ids)):
            if inst_id not in self.quotes:
                continue
            q = self.quotes[inst_id]
            result.append(
                SettlementPrice(
                    instrument_id=inst_id,
                    valuation_date=valuation_date,
                    price=to_decimal(q["price"], f"quotes.{inst_id}.price"),
                    currency=str(q["currency"]),
                    revision=revision,
                )
            )
        return tuple(result)


@dataclass(frozen=True)
class LinearizedGreeksScenarioAdapter:
    """Adapter translating linearized per-instrument delta/price shock tables into a ScenarioSet.

    Demonstrates how a future `real-time-greeks-engine` or `derivatives-pricing-engine`
    output table can feed `derivatives-margin-engine` through a clean adapter boundary.
    """

    shock_table: Mapping[str, Mapping[str, str]]
    shock_type: ShockType = ShockType.RELATIVE

    def build_scenario_set(self, scenario_set_id: str) -> ScenarioSet:
        scenarios = []
        for scn_id in sorted(self.shock_table.keys()):
            inst_map = self.shock_table[scn_id]
            shocks = tuple(
                Shock(
                    target_type=TargetType.INSTRUMENT,
                    target_id=inst_id,
                    shock_type=self.shock_type,
                    value=to_decimal(val, f"{scn_id}.{inst_id}"),
                )
                for inst_id, val in sorted(inst_map.items())
            )
            scenarios.append(
                Scenario(
                    scenario_id=scn_id,
                    description=f"Adapter-imported scenario {scn_id}",
                    shocks=shocks,
                )
            )
        return ScenarioSet(
            schema_version=SUPPORTED_SCHEMA_VERSION,
            scenario_set_id=scenario_set_id,
            description="Scenarios imported via LinearizedGreeksScenarioAdapter",
            scenarios=tuple(scenarios),
        )


def filter_positions_for_netting_set(
    positions: Sequence[Position], netting_set_id: str
) -> Sequence[Position]:
    """Helper boundary function for downstream portfolio adapters."""
    return tuple(p for p in positions if p.netting_set_id == netting_set_id)
