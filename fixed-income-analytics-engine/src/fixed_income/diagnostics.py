"""Structured JSON diagnostics, counters, runtime metadata, and replay verification."""

from __future__ import annotations

import json
import platform
import sys
from dataclasses import dataclass, field
from typing import Any, Dict, List

from . import SCHEMA_VERSION, __version__
from .models import stable_id, utc_now_iso


@dataclass
class DiagnosticLog:
    """In-memory structured JSON event recorder and diagnostic counter."""

    run_label: str
    events: List[Dict[str, Any]] = field(default_factory=list)
    counters: Dict[str, int] = field(default_factory=dict)

    def increment(self, counter_name: str, amount: int = 1) -> None:
        self.counters[counter_name] = self.counters.get(counter_name, 0) + amount

    def emit(self, level: str, event: str, **fields: Any) -> None:
        record = {
            "timestamp_utc": utc_now_iso(),
            "level": level.upper(),
            "run_label": self.run_label,
            "event": event,
            "fields": fields,
        }
        self.events.append(record)
        self.increment(f"events.{level.lower()}")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "run_label": self.run_label,
            "counters": dict(sorted(self.counters.items())),
            "event_count": len(self.events),
            "events": list(self.events),
        }

    def to_jsonl(self) -> str:
        return "\n".join(json.dumps(e, sort_keys=True, default=str) for e in self.events)


def runtime_metadata() -> Dict[str, Any]:
    """Capture deterministic environment metadata (no secrets or user paths)."""
    optional_pkgs: Dict[str, str] = {}
    for pkg in ("numpy", "scipy", "QuantLib", "pytest"):
        try:
            mod = __import__(pkg)
            optional_pkgs[pkg] = getattr(mod, "__version__", "installed")
        except ImportError:
            optional_pkgs[pkg] = "not_installed"

    return {
        "engine_version": __version__,
        "schema_version": SCHEMA_VERSION,
        "python_version": sys.version.split()[0],
        "platform": platform.platform(),
        "optional_packages": optional_pkgs,
        "generated_at_utc": utc_now_iso(),
    }


def compute_logical_fingerprint(payload: Dict[str, Any]) -> str:
    """Compute a stable logical fingerprint ignoring volatile wall-clock metadata."""
    cleaned = _strip_volatile_keys(payload)
    return stable_id("logical", cleaned)


def _strip_volatile_keys(obj: Any) -> Any:
    volatile = {"generated_at_utc", "timestamp_utc", "runtime_metadata", "diagnostics"}
    if isinstance(obj, dict):
        return {k: _strip_volatile_keys(v) for k, v in obj.items() if k not in volatile}
    if isinstance(obj, list):
        return [_strip_volatile_keys(item) for item in obj]
    return obj
