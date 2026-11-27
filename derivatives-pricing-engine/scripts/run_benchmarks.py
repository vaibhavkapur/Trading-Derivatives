#!/usr/bin/env python3
"""Run convergence and Monte Carlo CI coverage benchmarks and write versioned JSON reports."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from derivatives_pricing_engine.cli import main

if __name__ == "__main__":
    default_dir = str(ROOT / "reports")
    args = sys.argv[1:] if len(sys.argv) > 1 else ["benchmark", "--output-dir", default_dir]
    raise SystemExit(main(args))
