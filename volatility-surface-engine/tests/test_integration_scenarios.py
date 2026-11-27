"""Integration tests: oracle recovery, held-out strike fit, snapshots/replay, viz, and CLI."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from volatility_surface_engine import (
    OptionChainSnapshot,
    build_surface,
    clean_and_invert_quotes,
    evaluate_heldout_quotes,
    export_surface_html,
    load_chain_snapshot,
    load_config,
    load_surface_snapshot,
    save_surface_snapshot,
)
from volatility_surface_engine.cli import main as cli_main

PROJECT_ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = PROJECT_ROOT / "config" / "default_config.json"
FIXTURES_DIR = PROJECT_ROOT / "data" / "fixtures"


def test_reference_oracle_smile_and_interpolation_recovery() -> None:
    cfg = load_config(CONFIG_PATH)
    chain = load_chain_snapshot(FIXTURES_DIR / "valid_chain_reference.json")
    expected = json.loads((FIXTURES_DIR / "expected_reference.json").read_text(encoding="utf-8"))

    surface = build_surface(chain, cfg)
    assert surface.is_valid
    assert surface.snapshot.diagnostics.overall_svi_weighted_rmse_iv <= expected["tolerances"]["max_weighted_rmse_iv"]

    for oq in expected["oracle_queries"]:
        res = surface.query(expiry_years=oq["expiry_years"], log_moneyness=oq["log_moneyness"], model="svi")
        assert abs(res.implied_vol - oq["expected_implied_vol"]) <= expected["tolerances"]["max_iv_recovery_abs_err"]


def test_heldout_strike_fit_svi_and_sabr() -> None:
    cfg = load_config(CONFIG_PATH)
    bundle = json.loads((FIXTURES_DIR / "heldout_smile_chain.json").read_text(encoding="utf-8"))
    train_chain = OptionChainSnapshot.from_dict(bundle["train_snapshot"])
    test_chain = OptionChainSnapshot.from_dict(bundle["heldout_snapshot"])

    surface = build_surface(train_chain, cfg)
    assert surface.is_valid

    test_iv_quotes, _, _, _, _ = clean_and_invert_quotes(test_chain, cfg)
    svi_eval = evaluate_heldout_quotes(surface, test_iv_quotes, model="svi")
    sabr_eval = evaluate_heldout_quotes(surface, test_iv_quotes, model="sabr")

    assert svi_eval["evaluated_count"] == 16  # 4 expiries * 4 held-out interior strikes
    assert svi_eval["skipped_out_of_domain"] == 0
    assert svi_eval["heldout_rmse_iv"] < 0.002  # < 20 bps on noisy 8 bps synthetic data
    assert sabr_eval["heldout_rmse_iv"] < 0.005  # SABR benchmark fits closely as well


def test_versioned_snapshot_roundtrip_replay_and_exports(tmp_path: Path) -> None:
    cfg = load_config(CONFIG_PATH)
    chain = load_chain_snapshot(FIXTURES_DIR / "valid_chain_reference.json")
    surface1 = build_surface(chain, cfg)
    surface2 = build_surface(chain, cfg)

    assert surface1.surface_id == surface2.surface_id
    assert surface1.snapshot.to_dict() == surface2.snapshot.to_dict()

    snap_file = tmp_path / "snapshot.json"
    save_surface_snapshot(surface1, snap_file)
    reloaded = load_surface_snapshot(snap_file)
    assert reloaded.surface_id == surface1.surface_id
    assert reloaded.is_valid == surface1.is_valid

    q1 = surface1.query(expiry_years=135.0 / 365.0, strike=98.0)
    q2 = reloaded.query(expiry_years=135.0 / 365.0, strike=98.0)
    assert abs(q1.implied_vol - q2.implied_vol) < 1e-12

    # Polars slice summary export
    pl = pytest.importorskip("polars")
    slices_df = reloaded.to_polars_slices()
    assert isinstance(slices_df, pl.DataFrame)
    assert slices_df.height == 4

    # Plotly HTML export
    pytest.importorskip("plotly")
    html_file = export_surface_html(reloaded, tmp_path / "surface.html")
    assert html_file.exists()
    assert html_file.stat().st_size > 1000


def test_cli_end_to_end_and_error_handling(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    snap_out = tmp_path / "cli_snapshot.json"
    ref_in = str(FIXTURES_DIR / "valid_chain_reference.json")

    # validate
    rc = cli_main(["validate", "--input", ref_in])
    assert rc == 0
    val_out = json.loads(capsys.readouterr().out)
    assert val_out["status"] == "valid"

    # calibrate
    rc = cli_main(["calibrate", "--input", ref_in, "--config", str(CONFIG_PATH), "--output", str(snap_out)])
    assert rc == 0
    cal_out = json.loads(capsys.readouterr().out)
    assert cal_out["is_valid"] is True
    assert cal_out["butterfly_violations"] == 0
    assert cal_out["calendar_violations"] == 0
    assert snap_out.exists()

    # query in-domain
    rc = cli_main(["query", "--snapshot", str(snap_out), "--expiry-years", "0.25", "--log-moneyness", "0.0"])
    assert rc == 0
    q_out = json.loads(capsys.readouterr().out)
    assert q_out["extrapolated"] is False

    # query extrapolation rejected with non-zero exit code
    rc = cli_main(["query", "--snapshot", str(snap_out), "--expiry-years", "3.0", "--log-moneyness", "0.0"])
    assert rc == 2
    err_out = json.loads(capsys.readouterr().err)
    assert err_out["error"] == "EXTRAPOLATION_REJECTED"

    # replay determinism check
    rc = cli_main(["replay", "--input", ref_in, "--snapshot", str(snap_out), "--config", str(CONFIG_PATH)])
    assert rc == 0
    rep_out = json.loads(capsys.readouterr().out)
    assert rep_out["replay_match"] is True

    # missing config fails cleanly with exit code 2
    rc = cli_main(["calibrate", "--input", ref_in, "--config", str(tmp_path / "missing.json")])
    assert rc == 2
    err_cfg = json.loads(capsys.readouterr().err)
    assert err_cfg["error"] == "CONFIGURATION_ERROR"
