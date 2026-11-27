"""Configuration loader and validator."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Union

from .errors import ConfigurationError
from .version import CONFIG_VERSION

DEFAULT_CONFIG_PATH = Path(__file__).resolve().parents[2] / "config" / "default.json"


@dataclass(frozen=True)
class EngineConfig:
    """Validated runtime configuration for the pricing engine."""

    config_version: str
    engine: dict[str, Any]
    methods: dict[str, dict[str, Any]]
    benchmarks: dict[str, Any]
    source_path: str

    def method_defaults(self, method: str) -> dict[str, Any]:
        if method not in self.methods:
            raise ConfigurationError(
                f"No default configuration section found for pricing method '{method}'.",
                details={"method": method, "available_methods": sorted(self.methods.keys())},
            )
        return dict(self.methods[method])


def load_config(path: Union[str, Path, None] = None) -> EngineConfig:
    """Load and validate an engine configuration JSON file."""
    cfg_path = Path(path) if path is not None else DEFAULT_CONFIG_PATH
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
    except (OSError, json.JSONDecodeError) as exc:
        raise ConfigurationError(
            f"Failed to parse configuration file '{cfg_path}': {exc}",
            details={"path": str(cfg_path)},
        ) from exc

    if not isinstance(raw, dict):
        raise ConfigurationError(
            "Configuration root must be a JSON object.",
            details={"path": str(cfg_path)},
        )

    required_keys = ("config_version", "engine", "methods", "benchmarks")
    missing = [k for k in required_keys if k not in raw]
    if missing:
        raise ConfigurationError(
            f"Configuration file missing required sections: {missing}",
            details={"path": str(cfg_path), "missing_keys": missing},
        )

    if raw["config_version"] != CONFIG_VERSION:
        raise ConfigurationError(
            f"Unsupported config_version '{raw['config_version']}', expected '{CONFIG_VERSION}'.",
            details={"actual": raw["config_version"], "expected": CONFIG_VERSION},
        )

    if not isinstance(raw["methods"], dict) or not isinstance(raw["engine"], dict):
        raise ConfigurationError(
            "'engine' and 'methods' configuration sections must be JSON objects.",
            details={"path": str(cfg_path)},
        )

    return EngineConfig(
        config_version=raw["config_version"],
        engine=raw["engine"],
        methods=raw["methods"],
        benchmarks=raw["benchmarks"],
        source_path=str(cfg_path),
    )
