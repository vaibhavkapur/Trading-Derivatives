"""Failure, boundary, staleness, duplicate-tick, snapshot-race, and config validation tests."""

from __future__ import annotations

import json
import threading
from datetime import timedelta
from pathlib import Path

import pytest

from real_time_greeks_engine.adapters import JsonFixtureAdapter
from real_time_greeks_engine.config import EngineConfig, StalenessConfig, load_config
from real_time_greeks_engine.engine import RealTimeGreeksEngine
from real_time_greeks_engine.errors import (
    ConfigurationError,
    SnapshotVersionConflictError,
    StaleMarketDataError,
)
from real_time_greeks_engine.models import (
    ReliabilityFlag,
    SpotTick,
    VolSurfaceSnapshot,
)

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = ROOT / "data" / "fixtures"


def test_stale_vol_surface_freezes_snapshot_and_recovers_on_refresh() -> None:
    """Required failure scenario: stale surface raises alert, freezes Greeks, and recovers cleanly."""
    cfg = load_config(ROOT / "config" / "default.json")
    engine = RealTimeGreeksEngine(cfg)
    base_snap = JsonFixtureAdapter(FIXTURES / "valid_portfolio.json").load_into(engine)
    orig_acme_pv = base_snap.positions["POS-ACME-1Y-C100"].position_greeks.pv

    boundary_raw = json.loads((FIXTURES / "invalid_and_boundary.json").read_text(encoding="utf-8"))
    stale_vol = VolSurfaceSnapshot.model_validate(boundary_raw["stale_vol_surface"])

    # Ingesting stale ACME vol surface freezes ACME positions at last reliable values and raises alert
    stale_snap = engine.upsert_vol_surface(stale_vol)
    assert stale_snap.has_frozen_positions is True
    acme_res = stale_snap.positions["POS-ACME-1Y-C100"]
    assert acme_res.is_frozen is True
    assert ReliabilityFlag.STALE_MARKET_DATA in acme_res.flags
    assert ReliabilityFlag.FROZEN_SNAPSHOT in acme_res.flags
    assert acme_res.position_greeks.pv == pytest.approx(orig_acme_pv, abs=1e-12)
    assert any(a.code == "STALE_VOL_SURFACE" and a.resource == "vol:ACME" for a in stale_snap.alerts)
    assert engine.reconcile().passed is True

    # Refresh with fresh vol surface -> unfreezes and clears stale vol alert
    fresh_vol = stale_vol.model_copy(
        update={
            "vol_version": "VOL-ACME-FRESH-002",
            "as_of_utc": base_snap.valuation_time_utc,
        }
    )
    recovered_snap = engine.upsert_vol_surface(fresh_vol)
    assert recovered_snap.positions["POS-ACME-1Y-C100"].is_frozen is False
    assert recovered_snap.has_frozen_positions is False
    assert not any(a.code == "STALE_VOL_SURFACE" for a in recovered_snap.alerts)
    assert engine.reconcile().passed is True


def test_strict_raise_on_stale_fails_loudly() -> None:
    cfg = EngineConfig(staleness=StalenessConfig(spot_ttl_seconds=5.0, strict_raise_on_stale=True))
    engine = RealTimeGreeksEngine(cfg)
    snap = JsonFixtureAdapter(FIXTURES / "valid_portfolio.json").load_into(engine)

    with pytest.raises(StaleMarketDataError):
        engine.set_valuation_time(snap.valuation_time_utc + timedelta(seconds=30))


def test_duplicate_and_out_of_order_ticks_ignored_without_state_corruption() -> None:
    """Required failure scenario: duplicate tick_id, duplicate seq, and out-of-order seq are rejected."""
    engine = RealTimeGreeksEngine(load_config(ROOT / "config" / "default.json"))
    base_snap = JsonFixtureAdapter(FIXTURES / "valid_portfolio.json").load_into(engine)
    vt = base_snap.valuation_time_utc

    # Advance to seq=5 at spot=105.0
    t5 = SpotTick(
        tick_id="TICK-ACME-0005",
        underlying="ACME",
        spot=105.0,
        sequence_no=5,
        event_time_utc=vt,
        received_time_utc=vt,
    )
    o5, s5 = engine.ingest_spot_tick(t5)
    assert o5.accepted is True
    assert s5.positions["POS-ACME-1Y-C100"].spot == 105.0

    # Duplicate tick_id with higher seq still rejected
    dup_id = t5.model_copy(update={"spot": 999.0, "sequence_no": 6})
    o_dup, s_dup = engine.ingest_spot_tick(dup_id)
    assert o_dup.accepted is False
    assert o_dup.reason == "DUPLICATE_TICK"
    assert s_dup.positions["POS-ACME-1Y-C100"].spot == 105.0

    # Out-of-order lower sequence number rejected
    ooo = SpotTick(
        tick_id="TICK-ACME-0003",
        underlying="ACME",
        spot=88.0,
        sequence_no=3,
        event_time_utc=vt,
        received_time_utc=vt,
    )
    o_ooo, s_ooo = engine.ingest_spot_tick(ooo)
    assert o_ooo.accepted is False
    assert o_ooo.reason == "OUT_OF_ORDER_TICK"
    assert s_ooo.positions["POS-ACME-1Y-C100"].spot == 105.0

    diag = engine.diagnostics()
    assert diag["counters"]["duplicate_ticks_ignored"] == 1
    assert diag["counters"]["out_of_order_ticks_ignored"] == 1
    assert engine.reconcile().passed is True


def test_snapshot_race_optimistic_conflict_and_multithreaded_consistency() -> None:
    """Required failure scenario: optimistic seq check rejects races; concurrent readers always see atomic snapshots."""
    engine = RealTimeGreeksEngine(load_config(ROOT / "config" / "default.json"))
    base_snap = JsonFixtureAdapter(FIXTURES / "valid_portfolio.json").load_into(engine)
    vt = base_snap.valuation_time_utc

    # Optimistic concurrency race check
    stale_seq = base_snap.snapshot_seq - 1
    tick = SpotTick(
        tick_id="RACE-TICK-1",
        underlying="ACME",
        spot=101.0,
        sequence_no=2,
        event_time_utc=vt,
        received_time_utc=vt,
    )
    with pytest.raises(SnapshotVersionConflictError):
        engine.ingest_spot_tick(tick, expected_snapshot_seq=stale_seq)
    assert engine.diagnostics()["counters"]["snapshot_race_conflicts"] == 1

    # Multi-threaded concurrent writers + readers verifying snapshot atomicity
    errors: list[Exception] = []
    stop_flag = threading.Event()

    def _writer() -> None:
        try:
            for seq in range(2, 120):
                engine.ingest_spot_tick(
                    SpotTick(
                        tick_id=f"MT-ACME-{seq}",
                        underlying="ACME",
                        spot=100.0 + (seq % 10) * 0.5,
                        sequence_no=seq,
                        event_time_utc=vt,
                        received_time_utc=vt,
                    )
                )
        except Exception as exc:  # pragma: no cover
            errors.append(exc)
        finally:
            stop_flag.set()

    def _reader() -> None:
        last_seq = 0
        try:
            while not stop_flag.is_set():
                snap = engine.get_snapshot()
                assert snap.snapshot_seq >= last_seq
                last_seq = snap.snapshot_seq
                sum_pos_pv = sum(p.position_greeks.pv for p in snap.positions.values())
                sum_u_pv = sum(b.pv for b in snap.by_underlying.values())
                assert abs(sum_pos_pv - snap.totals.pv) < 1e-7
                assert abs(sum_u_pv - snap.totals.pv) < 1e-7
        except Exception as exc:  # pragma: no cover
            errors.append(exc)

    w_thread = threading.Thread(target=_writer)
    r_threads = [threading.Thread(target=_reader) for _ in range(3)]
    for rt in r_threads:
        rt.start()
    w_thread.start()
    w_thread.join()
    for rt in r_threads:
        rt.join()

    assert errors == []
    assert engine.reconcile().passed is True


def test_missing_and_invalid_configuration_fails_loudly(tmp_path: Path) -> None:
    """Operational review: missing or invalid config raises ConfigurationError (no silent fallback)."""
    with pytest.raises(ConfigurationError):
        load_config(tmp_path / "does_not_exist.json")

    bad_json = tmp_path / "bad.json"
    bad_json.write_text("{not valid json", encoding="utf-8")
    with pytest.raises(ConfigurationError):
        load_config(bad_json)

    bad_method = tmp_path / "bad_method.json"
    bad_method.write_text(json.dumps({"pricing": {"default_method": "MAGIC_TREE"}}), encoding="utf-8")
    with pytest.raises(ConfigurationError):
        load_config(bad_method)
