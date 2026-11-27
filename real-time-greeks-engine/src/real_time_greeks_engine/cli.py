"""Command-line interface for demo, replay, benchmark, and API server."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import uvicorn

from real_time_greeks_engine.adapters import JsonFixtureAdapter
from real_time_greeks_engine.api import create_app
from real_time_greeks_engine.config import load_config
from real_time_greeks_engine.engine import RealTimeGreeksEngine


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="greeks-engine", description="Real-Time Greeks Engine CLI")
    sub = parser.add_subparsers(dest="command", required=True)

    serve_p = sub.add_parser("serve", help="Start FastAPI server and reconciliation dashboard")
    serve_p.add_argument("--config", default="config/default.json")
    serve_p.add_argument("--fixture", default="data/fixtures/valid_portfolio.json")
    serve_p.add_argument("--host", default="127.0.0.1")
    serve_p.add_argument("--port", type=int, default=8000)

    demo_p = sub.add_parser("demo", help="Run deterministic offline demo and print snapshot + reconciliation")
    demo_p.add_argument("--config", default="config/default.json")
    demo_p.add_argument("--fixture", default="data/fixtures/valid_portfolio.json")
    demo_p.add_argument("--replay", default="data/fixtures/replay_events.json")

    args = parser.parse_args(argv)
    cfg = load_config(args.config)
    engine = RealTimeGreeksEngine(cfg)
    if args.fixture and Path(args.fixture).exists():
        JsonFixtureAdapter(args.fixture).load_into(engine)

    if args.command == "serve":
        app = create_app(engine)
        uvicorn.run(app, host=args.host, port=args.port)
        return 0

    if args.command == "demo":
        if args.replay and Path(args.replay).exists():
            events = json.loads(Path(args.replay).read_text(encoding="utf-8")).get("events", [])
            engine.replay_events(events)
        snap = engine.get_snapshot()
        recon = engine.reconcile()
        diag = engine.diagnostics()
        print(
            json.dumps(
                {
                    "snapshot_id": snap.snapshot_id,
                    "totals": snap.totals.model_dump(mode="json"),
                    "by_underlying": {k: v.model_dump(mode="json") for k, v in snap.by_underlying.items()},
                    "reconciliation": recon.model_dump(mode="json"),
                    "diagnostics": diag,
                },
                indent=2,
            )
        )
        return 0 if recon.passed else 1

    return 1


if __name__ == "__main__":
    raise SystemExit(main())
