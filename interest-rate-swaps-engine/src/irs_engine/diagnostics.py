"""Structured JSON logging, diagnostic counters, deterministic IDs, and runtime metadata."""

from __future__ import annotations

import dataclasses
import hashlib
import json
import logging
import platform
import sys
from collections import Counter
from datetime import date, datetime, timezone
from enum import Enum
from typing import Any

from irs_engine import __schema_version__, __version__


def _canonical_default(obj: Any) -> Any:
    if isinstance(obj, Enum):
        return obj.value
    if isinstance(obj, (date, datetime)):
        return obj.isoformat()
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        return dataclasses.asdict(obj)
    raise TypeError(f"Object of type {type(obj).__name__} is not JSON serializable")


def canonical_json(payload: Any) -> str:
    """Serialize any domain object or dict into deterministic canonical JSON."""
    return json.dumps(
        payload,
        default=_canonical_default,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    )


def compute_valuation_id(
    *,
    trade: Any,
    discount_snapshot: Any,
    forward_snapshot: Any,
    fixing_history: Any,
    config: Any,
) -> str:
    """Compute a stable SHA-256 prefix identifier from canonical valuation inputs."""
    digest = hashlib.sha256(
        canonical_json(
            {
                "engine_version": __version__,
                "schema_version": __schema_version__,
                "trade": trade,
                "discount_curve": discount_snapshot,
                "forward_curve": forward_snapshot,
                "fixing_history": fixing_history,
                "config": config,
            }
        ).encode("utf-8")
    ).hexdigest()
    return f"val_{digest[:16]}"


class StructuredJsonFormatter(logging.Formatter):
    """Emit structured single-line JSON logs for operational observability."""

    def format(self, record: logging.LogRecord) -> str:
        event: dict[str, Any] = {
            "timestamp_utc": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        extra_fields = getattr(record, "structured_fields", None)
        if isinstance(extra_fields, dict):
            event.update(extra_fields)
        return json.dumps(event, default=_canonical_default, sort_keys=True)


def get_logger(name: str = "irs_engine", level: str = "INFO") -> logging.Logger:
    logger = logging.getLogger(name)
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(StructuredJsonFormatter())
        logger.addHandler(handler)
    logger.setLevel(getattr(logging, level.upper(), logging.INFO))
    logger.propagate = False
    return logger


class DiagnosticCounters:
    """Thread-safe-style lightweight diagnostic counter store for engine operations."""

    def __init__(self) -> None:
        self._counts: Counter[str] = Counter()

    def inc(self, key: str, amount: int = 1) -> None:
        self._counts[key] += amount

    def snapshot(self) -> dict[str, int]:
        return dict(sorted(self._counts.items()))

    def reset(self) -> None:
        self._counts.clear()


ENGINE_COUNTERS = DiagnosticCounters()


def collect_runtime_metadata() -> dict[str, Any]:
    """Capture reproducible runtime metadata (excluding secrets or host-sensitive paths)."""
    try:
        import importlib.metadata as importlib_meta

        pkg_versions = {}
        for pkg in ("fastapi", "pydantic", "uvicorn", "pytest", "httpx", "QuantLib"):
            try:
                pkg_versions[pkg] = importlib_meta.version(pkg)
            except importlib_meta.PackageNotFoundError:
                pkg_versions[pkg] = "not_installed"
    except Exception:
        pkg_versions = {}

    return {
        "engine_version": __version__,
        "schema_version": __schema_version__,
        "python_version": sys.version.split()[0],
        "platform": platform.platform(),
        "machine": platform.machine(),
        "packages": pkg_versions,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
    }
