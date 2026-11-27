"""I/O utilities, runtime environment provenance, and replay envelopes."""

from __future__ import annotations

import json
import platform
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Union

import numpy as np
import scipy

from .errors import ValidationError
from .version import ENGINE_VERSION, SCHEMA_VERSION


def collect_runtime_environment() -> dict[str, str]:
    """Capture runtime environment versions for reproducible audit artifacts."""
    return {
        "engine_version": ENGINE_VERSION,
        "schema_version": SCHEMA_VERSION,
        "python_version": platform.python_version(),
        "python_implementation": platform.python_implementation(),
        "numpy_version": np.__version__,
        "scipy_version": scipy.__version__,
        "platform": platform.platform(),
        "machine": platform.machine(),
    }


def read_json(path: Union[str, Path]) -> Any:
    """Read and parse a JSON file with explicit error reporting."""
    file_path = Path(path)
    if not file_path.exists():
        raise ValidationError(f"Input JSON file does not exist: {file_path}", details={"path": str(file_path)})
    try:
        return json.loads(file_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValidationError(f"Failed to read JSON file '{file_path}': {exc}", details={"path": str(file_path)}) from exc


def write_json(path: Union[str, Path], payload: Any) -> Path:
    """Write formatted UTF-8 JSON to disk, creating parent directories if needed."""
    file_path = Path(path)
    file_path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    file_path.write_text(text, encoding="utf-8")
    return file_path


def utc_now_iso() -> str:
    """Return current UTC timestamp in canonical ISO-8601 Z format."""
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
