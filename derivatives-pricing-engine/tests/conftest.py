"""Shared pytest configuration and fixture paths."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from derivatives_pricing_engine import PricingEngine, load_config


@pytest.fixture(scope="session")
def project_root() -> Path:
    return PROJECT_ROOT


@pytest.fixture(scope="session")
def fixtures_dir() -> Path:
    return PROJECT_ROOT / "data" / "fixtures"


@pytest.fixture()
def engine() -> PricingEngine:
    return PricingEngine(load_config(PROJECT_ROOT / "config" / "default.json"))
