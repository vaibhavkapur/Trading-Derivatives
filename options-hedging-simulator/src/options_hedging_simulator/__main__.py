"""Module entrypoint for `python -m options_hedging_simulator`."""

from __future__ import annotations

from .cli import main

if __name__ == "__main__":
    raise SystemExit(main())
