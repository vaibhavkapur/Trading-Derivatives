"""Discrete delta-hedging rebalancing policies."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

from .contracts import PolicyConfig
from .errors import ValidationError


@dataclass(frozen=True)
class RebalanceDecision:
    """Decision returned by a hedging policy at an intermediate simulation step."""

    rebalance: bool
    reason: str


class HedgePolicy(ABC):
    """Abstract base class for discrete rebalancing policies."""

    def __init__(self, config: PolicyConfig) -> None:
        self.config = config

    @property
    def name(self) -> str:
        return self.config.name

    @abstractmethod
    def decide(
        self,
        *,
        step: int,
        num_steps: int,
        current_unit_delta: float,
        target_unit_delta: float,
        current_shares: float,
        target_shares: float,
    ) -> RebalanceDecision:
        """Return whether to rebalance at `step` (0 < step < num_steps)."""


class StaticHedgePolicy(HedgePolicy):
    """Hedge once at inception (step 0) and hold underlying inventory until expiry."""

    def decide(
        self,
        *,
        step: int,
        num_steps: int,
        current_unit_delta: float,
        target_unit_delta: float,
        current_shares: float,
        target_shares: float,
    ) -> RebalanceDecision:
        return RebalanceDecision(rebalance=False, reason="static_hold")


class PeriodicHedgePolicy(HedgePolicy):
    """Rebalance on a fixed step cadence (`rebalance_every_n_steps`)."""

    def decide(
        self,
        *,
        step: int,
        num_steps: int,
        current_unit_delta: float,
        target_unit_delta: float,
        current_shares: float,
        target_shares: float,
    ) -> RebalanceDecision:
        if step % self.config.rebalance_every_n_steps != 0:
            return RebalanceDecision(rebalance=False, reason="off_schedule")
        trade_size = abs(target_shares - current_shares)
        if trade_size <= 1e-12 or trade_size < self.config.min_trade_shares:
            return RebalanceDecision(rebalance=False, reason="below_min_trade")
        return RebalanceDecision(rebalance=True, reason="periodic_schedule")


class ThresholdHedgePolicy(HedgePolicy):
    """Rebalance whenever absolute per-unit delta drift reaches `delta_band`."""

    def decide(
        self,
        *,
        step: int,
        num_steps: int,
        current_unit_delta: float,
        target_unit_delta: float,
        current_shares: float,
        target_shares: float,
    ) -> RebalanceDecision:
        delta_diff = abs(target_unit_delta - current_unit_delta)
        if delta_diff < self.config.delta_band:
            return RebalanceDecision(rebalance=False, reason="within_delta_band")
        trade_size = abs(target_shares - current_shares)
        if trade_size <= 1e-12 or trade_size < self.config.min_trade_shares:
            return RebalanceDecision(rebalance=False, reason="below_min_trade")
        return RebalanceDecision(rebalance=True, reason="delta_band_breach")


def build_policy(config: PolicyConfig) -> HedgePolicy:
    """Instantiate a concrete HedgePolicy from a validated PolicyConfig."""
    if config.policy_type == "static":
        return StaticHedgePolicy(config)
    if config.policy_type == "periodic":
        return PeriodicHedgePolicy(config)
    if config.policy_type == "threshold":
        return ThresholdHedgePolicy(config)
    raise ValidationError(f"Unsupported policy_type: {config.policy_type!r}", field="policy.policy_type")
