"""Configuration schemas and deterministic loader for the Real-Time Greeks Engine."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, ValidationError as PydanticValidationError

from real_time_greeks_engine.errors import ConfigurationError


class StalenessConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    spot_ttl_seconds: float = Field(default=5.0, gt=0.0)
    curve_ttl_seconds: float = Field(default=300.0, gt=0.0)
    vol_ttl_seconds: float = Field(default=60.0, gt=0.0)
    freeze_on_stale: bool = True
    strict_raise_on_stale: bool = False


class PricingConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    default_method: str = Field(default="ANALYTIC_BSM")
    fallback_to_bump_and_revalue: bool = True
    min_reliable_tte_days: float = Field(default=1.0, ge=0.0)
    min_sigma_sqrt_t: float = Field(default=0.001, gt=0.0)
    extreme_moneyness_log_threshold: float = Field(default=0.75, gt=0.0)
    spot_rel_bump: float = Field(default=1e-4, gt=0.0, lt=0.1)
    vol_abs_bump: float = Field(default=1e-4, gt=0.0, lt=0.1)
    rate_abs_bump: float = Field(default=1e-4, gt=0.0, lt=0.1)
    time_days_bump: float = Field(default=1e-2, gt=0.0, lt=1.0)


class CacheConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    max_entries: int = Field(default=50_000, ge=1)
    enabled: bool = True


class ReconciliationConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    abs_tolerance: float = Field(default=1e-8, gt=0.0)
    rel_tolerance: float = Field(default=1e-9, gt=0.0)


class ScenarioConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    max_workers: int = Field(default=4, ge=1, le=64)
    default_seed: int = Field(default=42, ge=0)


class ObservabilityConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    latency_window_size: int = Field(default=10_000, ge=10)
    log_level: str = Field(default="INFO")


class EngineConfig(BaseModel):
    """Top-level immutable configuration for RealTimeGreeksEngine."""

    model_config = ConfigDict(frozen=True)

    engine_id: str = Field(default="rt-greeks-engine-v1", min_length=1)
    base_currency: str = Field(default="USD", min_length=3, max_length=3)
    staleness: StalenessConfig = Field(default_factory=StalenessConfig)
    pricing: PricingConfig = Field(default_factory=PricingConfig)
    cache: CacheConfig = Field(default_factory=CacheConfig)
    reconciliation: ReconciliationConfig = Field(default_factory=ReconciliationConfig)
    scenarios: ScenarioConfig = Field(default_factory=ScenarioConfig)
    observability: ObservabilityConfig = Field(default_factory=ObservabilityConfig)

    def config_hash(self) -> str:
        """Stable SHA-256 prefix over canonical JSON representation."""
        canonical = json.dumps(self.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]


def load_config(path: str | Path) -> EngineConfig:
    """Load and validate EngineConfig from a JSON file; fails loudly on missing/invalid config."""
    cfg_path = Path(path)
    if not cfg_path.exists():
        raise ConfigurationError(
            f"Configuration file not found: {cfg_path}",
            details={"path": str(cfg_path)},
        )
    if not cfg_path.is_file():
        raise ConfigurationError(
            f"Configuration path is not a file: {cfg_path}",
            details={"path": str(cfg_path)},
        )
    try:
        raw = json.loads(cfg_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ConfigurationError(
            f"Invalid JSON in configuration file {cfg_path}: {exc}",
            details={"path": str(cfg_path)},
        ) from exc

    if not isinstance(raw, dict):
        raise ConfigurationError(
            "Configuration root must be a JSON object",
            details={"path": str(cfg_path)},
        )
    try:
        cfg = EngineConfig.model_validate(raw)
    except PydanticValidationError as exc:
        raise ConfigurationError(
            f"Configuration validation failed: {exc}",
            details={"path": str(cfg_path), "errors": exc.errors()},
        ) from exc

    if cfg.pricing.default_method not in {"ANALYTIC_BSM", "BUMP_AND_REVALUE"}:
        raise ConfigurationError(
            f"Unsupported default pricing method: {cfg.pricing.default_method}",
            details={"default_method": cfg.pricing.default_method},
        )
    return cfg
