"""Canonical input/output contracts, validation, and configuration hashing."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from decimal import Decimal
import hashlib
import json
import math
from typing import Any, Dict, List, Optional, Tuple

from .errors import ConfigurationError, UnsupportedContractError, ValidationError

SCHEMA_VERSION = "1.0.0"

SUPPORTED_CURRENCIES: Dict[str, int] = {
    "USD": 2,
    "EUR": 2,
    "GBP": 2,
    "JPY": 0,
}

SUPPORTED_DAY_COUNTS = frozenset({"ACT/365F", "BUS/252"})
SUPPORTED_COMPOUNDING = frozenset({"continuous", "simple"})
SUPPORTED_OPTION_TYPES = frozenset({"call", "put"})
SUPPORTED_EXERCISE_STYLES = frozenset({"european"})
SUPPORTED_SETTLEMENT_TYPES = frozenset({"cash", "physical"})
SUPPORTED_POSITION_SIDES = frozenset({"short", "long"})
SUPPORTED_PATH_MODELS = frozenset({"gbm", "merton_jump", "historical_bars"})
SUPPORTED_STALE_POLICIES = frozenset({"skip", "error", "use_last"})
SUPPORTED_POLICY_TYPES = frozenset({"static", "periodic", "threshold"})


def _check_finite(value: float, name: str) -> None:
    if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(float(value)):
        raise ValidationError(f"must be a finite number, got {value!r}", field=name)


def _check_positive(value: float, name: str) -> None:
    _check_finite(value, name)
    if float(value) <= 0.0:
        raise ValidationError(f"must be > 0, got {value!r}", field=name)


def _check_non_negative(value: float, name: str) -> None:
    _check_finite(value, name)
    if float(value) < 0.0:
        raise ValidationError(f"must be >= 0, got {value!r}", field=name)


@dataclass(frozen=True)
class PricingConventions:
    """Explicit pricing, currency, calendar, and valuation conventions."""

    day_count: str = "ACT/365F"
    compounding: str = "continuous"
    valuation_time_utc: str = "16:00:00Z"
    currency: str = "USD"
    currency_decimals: Optional[int] = None
    steps_per_year: int = 252

    def __post_init__(self) -> None:
        if self.currency not in SUPPORTED_CURRENCIES:
            raise UnsupportedContractError(
                f"currency {self.currency!r} is not supported; supported: {sorted(SUPPORTED_CURRENCIES)}",
                field="conventions.currency",
            )
        expected_decimals = SUPPORTED_CURRENCIES[self.currency]
        if self.currency_decimals is None:
            object.__setattr__(self, "currency_decimals", expected_decimals)
        elif self.currency_decimals != expected_decimals:
            raise ValidationError(
                f"currency {self.currency} requires {expected_decimals} decimals, got {self.currency_decimals}",
                field="conventions.currency_decimals",
            )
        if self.day_count not in SUPPORTED_DAY_COUNTS:
            raise UnsupportedContractError(
                f"day_count {self.day_count!r} is not supported; supported: {sorted(SUPPORTED_DAY_COUNTS)}",
                field="conventions.day_count",
            )
        if self.compounding not in SUPPORTED_COMPOUNDING:
            raise UnsupportedContractError(
                f"compounding {self.compounding!r} is not supported; supported: {sorted(SUPPORTED_COMPOUNDING)}",
                field="conventions.compounding",
            )
        if not isinstance(self.steps_per_year, int) or isinstance(self.steps_per_year, bool) or self.steps_per_year <= 0:
            raise ValidationError(
                f"steps_per_year must be a positive integer, got {self.steps_per_year!r}",
                field="conventions.steps_per_year",
            )
        if not isinstance(self.valuation_time_utc, str) or not self.valuation_time_utc.strip():
            raise ValidationError("valuation_time_utc must be a non-empty string", field="conventions.valuation_time_utc")

    @property
    def currency_quantum(self) -> Decimal:
        decimals = self.currency_decimals if self.currency_decimals is not None else SUPPORTED_CURRENCIES[self.currency]
        return Decimal(10) ** (-decimals)


@dataclass(frozen=True)
class OptionContract:
    """Canonical European option contract specification."""

    contract_id: str
    underlying: str
    option_type: str
    strike: float
    maturity_years: float
    contracts: int = 1
    multiplier: int = 100
    position_side: str = "short"
    exercise_style: str = "european"
    settlement_type: str = "cash"
    currency: str = "USD"

    def __post_init__(self) -> None:
        if not isinstance(self.contract_id, str) or not self.contract_id.strip():
            raise ValidationError("contract_id must be a non-empty string", field="contract.contract_id")
        if not isinstance(self.underlying, str) or not self.underlying.strip():
            raise ValidationError("underlying must be a non-empty string", field="contract.underlying")

        opt_type = str(self.option_type).lower()
        if opt_type not in SUPPORTED_OPTION_TYPES:
            raise UnsupportedContractError(
                f"option_type {self.option_type!r} is unsupported; expected one of {sorted(SUPPORTED_OPTION_TYPES)}",
                field="contract.option_type",
            )
        object.__setattr__(self, "option_type", opt_type)

        style = str(self.exercise_style).lower()
        if style not in SUPPORTED_EXERCISE_STYLES:
            raise UnsupportedContractError(
                f"exercise_style {self.exercise_style!r} is unsupported; only 'european' is implemented",
                field="contract.exercise_style",
            )
        object.__setattr__(self, "exercise_style", style)

        settlement = str(self.settlement_type).lower()
        if settlement not in SUPPORTED_SETTLEMENT_TYPES:
            raise UnsupportedContractError(
                f"settlement_type {self.settlement_type!r} is unsupported; expected {sorted(SUPPORTED_SETTLEMENT_TYPES)}",
                field="contract.settlement_type",
            )
        object.__setattr__(self, "settlement_type", settlement)

        side = str(self.position_side).lower()
        if side not in SUPPORTED_POSITION_SIDES:
            raise ValidationError(
                f"position_side {self.position_side!r} must be 'short' or 'long'",
                field="contract.position_side",
            )
        object.__setattr__(self, "position_side", side)

        if self.currency not in SUPPORTED_CURRENCIES:
            raise UnsupportedContractError(
                f"currency {self.currency!r} is not supported; supported: {sorted(SUPPORTED_CURRENCIES)}",
                field="contract.currency",
            )

        _check_positive(self.strike, "contract.strike")
        _check_positive(self.maturity_years, "contract.maturity_years")

        if not isinstance(self.contracts, int) or isinstance(self.contracts, bool) or self.contracts <= 0:
            raise ValidationError(f"contracts must be a positive integer, got {self.contracts!r}", field="contract.contracts")
        if not isinstance(self.multiplier, int) or isinstance(self.multiplier, bool) or self.multiplier <= 0:
            raise ValidationError(f"multiplier must be a positive integer, got {self.multiplier!r}", field="contract.multiplier")

    @property
    def total_units(self) -> int:
        """Total underlying share equivalents covered by the option contract."""
        return self.contracts * self.multiplier

    @property
    def option_sign(self) -> int:
        """+1 for a long option position, -1 for a short option position."""
        return 1 if self.position_side == "long" else -1


@dataclass(frozen=True)
class MarketParams:
    """Market inputs for pricing, hedging, and stochastic path generation."""

    spot0: float
    risk_free_rate: float
    hedge_volatility: float
    dividend_yield: float = 0.0
    realized_volatility: Optional[float] = None
    drift: Optional[float] = None

    def __post_init__(self) -> None:
        _check_positive(self.spot0, "market.spot0")
        _check_finite(self.risk_free_rate, "market.risk_free_rate")
        if abs(self.risk_free_rate) > 1.0:
            raise ValidationError(
                f"risk_free_rate {self.risk_free_rate!r} outside plausible annualized bounds [-1.0, 1.0]",
                field="market.risk_free_rate",
            )
        _check_positive(self.hedge_volatility, "market.hedge_volatility")
        if self.hedge_volatility > 5.0:
            raise ValidationError(
                f"hedge_volatility {self.hedge_volatility!r} exceeds maximum supported bound 5.0 (500%)",
                field="market.hedge_volatility",
            )
        _check_non_negative(self.dividend_yield, "market.dividend_yield")
        if self.dividend_yield > 1.0:
            raise ValidationError(
                f"dividend_yield {self.dividend_yield!r} exceeds maximum supported bound 1.0",
                field="market.dividend_yield",
            )
        if self.realized_volatility is not None:
            _check_positive(self.realized_volatility, "market.realized_volatility")
            if self.realized_volatility > 5.0:
                raise ValidationError(
                    f"realized_volatility {self.realized_volatility!r} exceeds maximum supported bound 5.0",
                    field="market.realized_volatility",
                )
        if self.drift is not None:
            _check_finite(self.drift, "market.drift")

    @property
    def effective_realized_volatility(self) -> float:
        return self.hedge_volatility if self.realized_volatility is None else float(self.realized_volatility)

    @property
    def effective_drift(self) -> float:
        return (self.risk_free_rate - self.dividend_yield) if self.drift is None else float(self.drift)


@dataclass(frozen=True)
class TransactionCostConfig:
    """Transaction cost, financing spread, and execution granularity assumptions."""

    half_spread_bps: float = 0.0
    fee_per_share: float = 0.0
    fee_per_trade: float = 0.0
    min_fee_per_trade: float = 0.0
    settlement_spread_bps: Optional[float] = None
    cash_borrow_spread_bps: float = 0.0
    share_lot_size: float = 0.0

    def __post_init__(self) -> None:
        _check_non_negative(self.half_spread_bps, "costs.half_spread_bps")
        if self.half_spread_bps >= 10000.0:
            raise ValidationError("half_spread_bps must be < 10000 bps (100%)", field="costs.half_spread_bps")
        _check_non_negative(self.fee_per_share, "costs.fee_per_share")
        _check_non_negative(self.fee_per_trade, "costs.fee_per_trade")
        _check_non_negative(self.min_fee_per_trade, "costs.min_fee_per_trade")
        if self.settlement_spread_bps is not None:
            _check_non_negative(self.settlement_spread_bps, "costs.settlement_spread_bps")
            if self.settlement_spread_bps >= 10000.0:
                raise ValidationError(
                    "settlement_spread_bps must be < 10000 bps (100%)",
                    field="costs.settlement_spread_bps",
                )
        _check_non_negative(self.cash_borrow_spread_bps, "costs.cash_borrow_spread_bps")
        _check_non_negative(self.share_lot_size, "costs.share_lot_size")

    @property
    def effective_settlement_spread_bps(self) -> float:
        return self.half_spread_bps if self.settlement_spread_bps is None else float(self.settlement_spread_bps)


@dataclass(frozen=True)
class DeterministicShock:
    """Explicit gap shock applied at a specific step index."""

    step: int
    pct_return: float

    def __post_init__(self) -> None:
        if not isinstance(self.step, int) or isinstance(self.step, bool) or self.step <= 0:
            raise ValidationError(f"shock step must be an integer > 0, got {self.step!r}", field="jump_params.deterministic_shocks.step")
        _check_finite(self.pct_return, "jump_params.deterministic_shocks.pct_return")
        if self.pct_return <= -0.99:
            raise ValidationError(
                f"pct_return must be > -0.99 to keep spot positive, got {self.pct_return!r}",
                field="jump_params.deterministic_shocks.pct_return",
            )


@dataclass(frozen=True)
class JumpParams:
    """Merton jump-diffusion and deterministic gap-shock stress parameters."""

    jump_intensity: float = 0.0
    jump_mean: float = 0.0
    jump_std: float = 0.0
    deterministic_shocks: Tuple[DeterministicShock, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        _check_non_negative(self.jump_intensity, "jump_params.jump_intensity")
        _check_finite(self.jump_mean, "jump_params.jump_mean")
        _check_non_negative(self.jump_std, "jump_params.jump_std")
        shocks = tuple(
            s if isinstance(s, DeterministicShock) else DeterministicShock(**s)
            for s in self.deterministic_shocks
        )
        object.__setattr__(self, "deterministic_shocks", shocks)


@dataclass(frozen=True)
class PathConfig:
    """Path generation or historical bar loading configuration."""

    model: str = "gbm"
    num_paths: int = 100
    num_steps: int = 63
    seed: int = 42
    jump_params: Optional[JumpParams] = None
    bars_path: Optional[str] = None
    stale_price_policy: str = "skip"
    max_staleness_seconds: float = 86400.0

    def __post_init__(self) -> None:
        model = str(self.model).lower()
        if model not in SUPPORTED_PATH_MODELS:
            raise ValidationError(
                f"path model {self.model!r} unsupported; expected {sorted(SUPPORTED_PATH_MODELS)}",
                field="path.model",
            )
        object.__setattr__(self, "model", model)

        if not isinstance(self.num_paths, int) or isinstance(self.num_paths, bool) or self.num_paths <= 0:
            raise ValidationError(f"num_paths must be a positive integer, got {self.num_paths!r}", field="path.num_paths")
        if not isinstance(self.num_steps, int) or isinstance(self.num_steps, bool) or self.num_steps <= 0:
            raise ValidationError(f"num_steps must be a positive integer, got {self.num_steps!r}", field="path.num_steps")
        if not isinstance(self.seed, int) or isinstance(self.seed, bool) or self.seed < 0:
            raise ValidationError(f"seed must be a non-negative integer, got {self.seed!r}", field="path.seed")

        stale_policy = str(self.stale_price_policy).lower()
        if stale_policy not in SUPPORTED_STALE_POLICIES:
            raise ValidationError(
                f"stale_price_policy {self.stale_price_policy!r} unsupported; expected {sorted(SUPPORTED_STALE_POLICIES)}",
                field="path.stale_price_policy",
            )
        object.__setattr__(self, "stale_price_policy", stale_policy)
        _check_positive(self.max_staleness_seconds, "path.max_staleness_seconds")

        if self.model == "historical_bars" and not self.bars_path:
            raise ValidationError("bars_path is required when path.model == 'historical_bars'", field="path.bars_path")
        if self.jump_params is not None and isinstance(self.jump_params, dict):
            object.__setattr__(self, "jump_params", JumpParams(**self.jump_params))
        if self.jump_params is not None:
            for shock in self.jump_params.deterministic_shocks:
                if shock.step > self.num_steps:
                    raise ValidationError(
                        f"deterministic shock step {shock.step} exceeds num_steps {self.num_steps}",
                        field="path.jump_params.deterministic_shocks",
                    )


@dataclass(frozen=True)
class PolicyConfig:
    """Discrete delta hedging policy specification."""

    name: str
    policy_type: str
    rebalance_every_n_steps: int = 1
    delta_band: float = 0.05
    min_trade_shares: float = 0.0

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not self.name.strip():
            raise ValidationError("policy name must be a non-empty string", field="policy.name")
        ptype = str(self.policy_type).lower()
        if ptype not in SUPPORTED_POLICY_TYPES:
            raise ValidationError(
                f"policy_type {self.policy_type!r} unsupported; expected {sorted(SUPPORTED_POLICY_TYPES)}",
                field="policy.policy_type",
            )
        object.__setattr__(self, "policy_type", ptype)

        if not isinstance(self.rebalance_every_n_steps, int) or isinstance(self.rebalance_every_n_steps, bool) or self.rebalance_every_n_steps <= 0:
            raise ValidationError(
                f"rebalance_every_n_steps must be an integer >= 1, got {self.rebalance_every_n_steps!r}",
                field="policy.rebalance_every_n_steps",
            )
        _check_positive(self.delta_band, "policy.delta_band")
        _check_non_negative(self.min_trade_shares, "policy.min_trade_shares")


@dataclass(frozen=True)
class SimulationConfig:
    """Top-level versioned configuration for a simulation run."""

    scenario_name: str
    contract: OptionContract
    market: MarketParams
    costs: TransactionCostConfig
    path: PathConfig
    policy: PolicyConfig
    conventions: PricingConventions = field(default_factory=PricingConventions)
    schema_version: str = SCHEMA_VERSION

    def __post_init__(self) -> None:
        if not isinstance(self.scenario_name, str) or not self.scenario_name.strip():
            raise ValidationError("scenario_name must be a non-empty string", field="scenario_name")
        if self.schema_version != SCHEMA_VERSION:
            raise ValidationError(
                f"unsupported schema_version {self.schema_version!r}; expected {SCHEMA_VERSION!r}",
                field="schema_version",
            )
        if self.contract.currency != self.conventions.currency:
            raise ValidationError(
                f"contract.currency ({self.contract.currency}) != conventions.currency ({self.conventions.currency})",
                field="contract.currency",
            )

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def canonical_json(self) -> str:
        return json.dumps(self.to_dict(), sort_keys=True, separators=(",", ":"))

    def config_hash(self) -> str:
        return hashlib.sha256(self.canonical_json().encode("utf-8")).hexdigest()[:16]

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "SimulationConfig":
        if not isinstance(data, dict):
            raise ConfigurationError("SimulationConfig expects a JSON object / dict.")
        required_keys = {"scenario_name", "contract", "market", "costs", "path", "policy"}
        missing = sorted(required_keys - set(data.keys()))
        if missing:
            raise ConfigurationError(f"Missing required config sections: {', '.join(missing)}")
        try:
            contract = OptionContract(**data["contract"])
            market = MarketParams(**data["market"])
            costs = TransactionCostConfig(**data["costs"])
            path_raw = dict(data["path"])
            if path_raw.get("jump_params") is not None:
                jp = dict(path_raw["jump_params"])
                if "deterministic_shocks" in jp and jp["deterministic_shocks"] is not None:
                    jp["deterministic_shocks"] = tuple(
                        DeterministicShock(**s) if isinstance(s, dict) else s
                        for s in jp["deterministic_shocks"]
                    )
                path_raw["jump_params"] = JumpParams(**jp)
            path = PathConfig(**path_raw)
            policy = PolicyConfig(**data["policy"])
            conventions = PricingConventions(**data.get("conventions", {"currency": contract.currency}))
            schema_version = data.get("schema_version", SCHEMA_VERSION)
            return cls(
                scenario_name=data["scenario_name"],
                contract=contract,
                market=market,
                costs=costs,
                path=path,
                policy=policy,
                conventions=conventions,
                schema_version=schema_version,
            )
        except (ValidationError, ConfigurationError):
            raise
        except TypeError as exc:
            raise ConfigurationError(f"Invalid configuration field or type: {exc}") from exc


@dataclass(frozen=True)
class ComparisonConfig:
    """Configuration for multi-policy comparison across shared seeded paths."""

    scenario_name: str
    contract: OptionContract
    market: MarketParams
    costs: TransactionCostConfig
    path: PathConfig
    policies: Tuple[PolicyConfig, ...]
    conventions: PricingConventions = field(default_factory=PricingConventions)
    schema_version: str = SCHEMA_VERSION

    def __post_init__(self) -> None:
        if not self.policies:
            raise ValidationError("policies must contain at least one PolicyConfig", field="policies")
        names = [p.name for p in self.policies]
        if len(set(names)) != len(names):
            raise ValidationError("policy names must be unique", field="policies")
        if self.contract.currency != self.conventions.currency:
            raise ValidationError(
                f"contract.currency ({self.contract.currency}) != conventions.currency ({self.conventions.currency})",
                field="contract.currency",
            )

    def to_simulation_configs(self) -> List[SimulationConfig]:
        return [
            SimulationConfig(
                scenario_name=f"{self.scenario_name}__{p.name}",
                contract=self.contract,
                market=self.market,
                costs=self.costs,
                path=self.path,
                policy=p,
                conventions=self.conventions,
                schema_version=self.schema_version,
            )
            for p in self.policies
        ]

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def config_hash(self) -> str:
        canonical = json.dumps(self.to_dict(), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ComparisonConfig":
        if not isinstance(data, dict):
            raise ConfigurationError("ComparisonConfig expects a JSON object / dict.")
        required_keys = {"scenario_name", "contract", "market", "costs", "path", "policies"}
        missing = sorted(required_keys - set(data.keys()))
        if missing:
            raise ConfigurationError(f"Missing required comparison config sections: {', '.join(missing)}")
        try:
            contract = OptionContract(**data["contract"])
            market = MarketParams(**data["market"])
            costs = TransactionCostConfig(**data["costs"])
            path_raw = dict(data["path"])
            if path_raw.get("jump_params") is not None:
                jp = dict(path_raw["jump_params"])
                if "deterministic_shocks" in jp and jp["deterministic_shocks"] is not None:
                    jp["deterministic_shocks"] = tuple(
                        DeterministicShock(**s) if isinstance(s, dict) else s
                        for s in jp["deterministic_shocks"]
                    )
                path_raw["jump_params"] = JumpParams(**jp)
            path = PathConfig(**path_raw)
            policies = tuple(PolicyConfig(**p) for p in data["policies"])
            conventions = PricingConventions(**data.get("conventions", {"currency": contract.currency}))
            return cls(
                scenario_name=data["scenario_name"],
                contract=contract,
                market=market,
                costs=costs,
                path=path,
                policies=policies,
                conventions=conventions,
                schema_version=data.get("schema_version", SCHEMA_VERSION),
            )
        except (ValidationError, ConfigurationError):
            raise
        except TypeError as exc:
            raise ConfigurationError(f"Invalid comparison configuration field: {exc}") from exc
