"""Reusable adapter boundaries for integrating market feeds, fixtures, and snapshot sinks."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Protocol, runtime_checkable

from real_time_greeks_engine.engine import RealTimeGreeksEngine
from real_time_greeks_engine.errors import ValidationError
from real_time_greeks_engine.models import (
    OptionPosition,
    PortfolioSnapshot,
    SpotTick,
    VolSurfaceSnapshot,
    YieldCurveSnapshot,
)


@runtime_checkable
class PortfolioFixtureAdapter(Protocol):
    """Protocol for loading versioned positions and market data into the engine."""

    def load_into(self, engine: RealTimeGreeksEngine) -> PortfolioSnapshot: ...


@runtime_checkable
class SnapshotPublisherAdapter(Protocol):
    """Protocol for publishing immutable portfolio snapshots to downstream consumers."""

    def publish(self, snapshot: PortfolioSnapshot) -> None: ...


class JsonFixtureAdapter:
    """Offline file adapter that hydrates a RealTimeGreeksEngine from a canonical JSON fixture."""

    def __init__(self, fixture_path: str | Path) -> None:
        self.fixture_path = Path(fixture_path)

    def load_into(self, engine: RealTimeGreeksEngine) -> PortfolioSnapshot:
        if not self.fixture_path.exists():
            raise ValidationError(f"Fixture file not found: {self.fixture_path}")
        raw = json.loads(self.fixture_path.read_text(encoding="utf-8"))
        val_time = datetime.fromisoformat(raw["valuation_time_utc"].replace("Z", "+00:00"))
        engine.set_valuation_time(val_time, recompute_all=False)

        for curve_raw in raw.get("yield_curves", []):
            engine.upsert_yield_curve(YieldCurveSnapshot.model_validate(curve_raw))

        for vol_raw in raw.get("vol_surfaces", []):
            engine.upsert_vol_surface(VolSurfaceSnapshot.model_validate(vol_raw))

        for tick_raw in raw.get("spot_ticks", []):
            engine.ingest_spot_tick(SpotTick.model_validate(tick_raw))

        for pos_raw in raw.get("positions", []):
            engine.upsert_position(OptionPosition.model_validate(pos_raw))

        return engine.get_snapshot()


class InMemorySnapshotPublisher:
    """In-memory snapshot sink for testing, replay auditing, and offline integration."""

    def __init__(self) -> None:
        self.published: list[PortfolioSnapshot] = []

    def publish(self, snapshot: PortfolioSnapshot) -> None:
        self.published.append(snapshot)


class JsonFileSnapshotPublisher:
    """Writes versioned JSON snapshots to a local directory for auditing and offline replay."""

    def __init__(self, output_dir: str | Path) -> None:
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def publish(self, snapshot: PortfolioSnapshot) -> None:
        out_path = self.output_dir / f"{snapshot.snapshot_id}.json"
        out_path.write_text(
            json.dumps(snapshot.model_dump(mode="json"), indent=2, sort_keys=True),
            encoding="utf-8",
        )
