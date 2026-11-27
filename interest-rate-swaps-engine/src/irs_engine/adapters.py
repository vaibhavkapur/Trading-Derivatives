"""Typed integration ports and standalone adapters for Trading-Derivatives siblings."""

from __future__ import annotations

from datetime import date
from typing import Protocol

from irs_engine.domain.calendars import CalendarRegistry
from irs_engine.domain.enums import CurveRole
from irs_engine.domain.errors import CurveError
from irs_engine.domain.models import (
    CurveSnapshot,
    FixingHistory,
    SensitivityResult,
    SwapTrade,
    SwapValuationResult,
    ValuationConfig,
)
from irs_engine.domain.pricer import price_swap
from irs_engine.domain.sensitivities import compute_sensitivities


class CurveProviderPort(Protocol):
    """Port interface for external curve providers (e.g., fixed-income-analytics-engine)."""

    def get_curve(
        self,
        *,
        currency: str,
        role: CurveRole,
        valuation_date: date,
        index_name: str | None = None,
    ) -> CurveSnapshot: ...


class SwapValuationPort(Protocol):
    """Port interface exposed to downstream risk/margin engines (e.g., derivatives-margin-engine)."""

    def value_swap(
        self,
        trade: SwapTrade,
        valuation_date: date,
        fixing_history: FixingHistory | None = None,
    ) -> SwapValuationResult: ...

    def compute_risk(
        self,
        trade: SwapTrade,
        valuation_date: date,
        fixing_history: FixingHistory | None = None,
    ) -> SensitivityResult: ...


class InMemoryCurveProvider:
    """Deterministic in-memory curve repository adapter."""

    def __init__(self, snapshots: list[CurveSnapshot] | tuple[CurveSnapshot, ...]) -> None:
        self._snapshots = tuple(snapshots)

    def get_curve(
        self,
        *,
        currency: str,
        role: CurveRole,
        valuation_date: date,
        index_name: str | None = None,
    ) -> CurveSnapshot:
        for snap in self._snapshots:
            if (
                snap.currency == currency
                and snap.role == role
                and snap.valuation_date == valuation_date
                and (index_name is None or snap.index_name == index_name)
            ):
                return snap
        raise CurveError(
            f"No matching curve found for currency={currency}, role={role.value}, "
            f"valuation_date={valuation_date.isoformat()}, index_name={index_name}.",
            details={
                "currency": currency,
                "role": role.value,
                "valuation_date": valuation_date.isoformat(),
                "index_name": index_name,
            },
        )


class IRSEngineServiceAdapter:
    """Standalone runnable adapter implementing SwapValuationPort via a CurveProviderPort."""

    def __init__(
        self,
        curve_provider: CurveProviderPort,
        *,
        calendar_registry: CalendarRegistry | None = None,
        config: ValuationConfig | None = None,
    ) -> None:
        self._curves = curve_provider
        self._calendars = calendar_registry or CalendarRegistry.default()
        self._config = config or ValuationConfig()

    def value_swap(
        self,
        trade: SwapTrade,
        valuation_date: date,
        fixing_history: FixingHistory | None = None,
    ) -> SwapValuationResult:
        disc = self._curves.get_curve(
            currency=trade.currency,
            role=CurveRole.DISCOUNT,
            valuation_date=valuation_date,
        )
        fwd = self._curves.get_curve(
            currency=trade.currency,
            role=CurveRole.FORWARD,
            valuation_date=valuation_date,
            index_name=trade.floating_leg.index_name,
        )
        return price_swap(
            trade,
            disc,
            fwd,
            fixing_history=fixing_history,
            calendar_registry=self._calendars,
            config=self._config,
        )

    def compute_risk(
        self,
        trade: SwapTrade,
        valuation_date: date,
        fixing_history: FixingHistory | None = None,
    ) -> SensitivityResult:
        disc = self._curves.get_curve(
            currency=trade.currency,
            role=CurveRole.DISCOUNT,
            valuation_date=valuation_date,
        )
        fwd = self._curves.get_curve(
            currency=trade.currency,
            role=CurveRole.FORWARD,
            valuation_date=valuation_date,
            index_name=trade.floating_leg.index_name,
        )
        return compute_sensitivities(
            trade,
            disc,
            fwd,
            fixing_history=fixing_history,
            calendar_registry=self._calendars,
            config=self._config,
        )
