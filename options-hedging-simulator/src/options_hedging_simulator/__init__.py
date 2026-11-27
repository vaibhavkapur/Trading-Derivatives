"""Options Hedging Simulator public package interface."""

from __future__ import annotations

from .analytics import (
    ConvergencePoint,
    ConvergenceReport,
    PolicyMetrics,
    batch_paths_dataframe,
    comparison_dataframe,
    run_zero_cost_convergence,
    summarize_batch,
)
from .contracts import (
    ComparisonConfig,
    DeterministicShock,
    JumpParams,
    MarketParams,
    OptionContract,
    PathConfig,
    PolicyConfig,
    PricingConventions,
    SCHEMA_VERSION,
    SimulationConfig,
    TransactionCostConfig,
)
from .errors import (
    ConfigurationError,
    HedgingSimulatorError,
    LedgerImbalanceError,
    ReplayMismatchError,
    StalePriceError,
    UnsupportedContractError,
    ValidationError,
)
from .io import load_comparison_config, load_simulation_config, verify_replay
from .ledger import FillRecord, LedgerEntry, SelfFinancingLedger
from .paths import SpotPath, generate_gbm_paths, generate_merton_jump_paths, generate_paths, load_historical_bars
from .policies import HedgePolicy, PeriodicHedgePolicy, StaticHedgePolicy, ThresholdHedgePolicy, build_policy
from .pricing import BlackScholesPricer, OptionQuote, PricingModel, finite_difference_greeks
from .simulator import (
    BatchSimulationResult,
    PathDiagnostics,
    PathSimulationResult,
    compare_policies,
    simulate_batch,
    simulate_path,
)

__all__ = [
    "SCHEMA_VERSION",
    "BatchSimulationResult",
    "BlackScholesPricer",
    "ComparisonConfig",
    "ConfigurationError",
    "ConvergencePoint",
    "ConvergenceReport",
    "DeterministicShock",
    "FillRecord",
    "HedgePolicy",
    "HedgingSimulatorError",
    "JumpParams",
    "LedgerEntry",
    "LedgerImbalanceError",
    "MarketParams",
    "OptionContract",
    "OptionQuote",
    "PathConfig",
    "PathDiagnostics",
    "PathSimulationResult",
    "PeriodicHedgePolicy",
    "PolicyConfig",
    "PolicyMetrics",
    "PricingConventions",
    "PricingModel",
    "ReplayMismatchError",
    "SelfFinancingLedger",
    "SimulationConfig",
    "SpotPath",
    "StalePriceError",
    "StaticHedgePolicy",
    "ThresholdHedgePolicy",
    "TransactionCostConfig",
    "UnsupportedContractError",
    "ValidationError",
    "batch_paths_dataframe",
    "build_policy",
    "compare_policies",
    "comparison_dataframe",
    "finite_difference_greeks",
    "generate_gbm_paths",
    "generate_merton_jump_paths",
    "generate_paths",
    "load_comparison_config",
    "load_historical_bars",
    "load_simulation_config",
    "run_zero_cost_convergence",
    "simulate_batch",
    "simulate_path",
    "summarize_batch",
    "verify_replay",
]
