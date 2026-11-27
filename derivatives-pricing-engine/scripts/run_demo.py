#!/usr/bin/env python3
"""Run the reproducible end-to-end derivatives pricing engine demonstration."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from derivatives_pricing_engine.cli import main

if __name__ == "__main__":
    default_out = str(ROOT / "reports" / "baseline_demo.json")
    args = sys.argv[1:] if len(sys.argv) > 1 else ["demo", "--output", default_out]
    raise SystemExit(main(args))
