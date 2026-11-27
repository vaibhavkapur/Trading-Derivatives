"""Shared pytest fixtures for Interest Rate Swaps Engine."""

from __future__ import annotations

from pathlib import Path

import pytest

from irs_engine.domain.calendars import CalendarRegistry

PROJECT_ROOT = Path(__file__).resolve().parents[1]
FIXTURES_DIR = PROJECT_ROOT / "data" / "fixtures"
CONFIG_DIR = PROJECT_ROOT / "config"


@pytest.fixture(scope="session")
def calendar_registry() -> CalendarRegistry:
    return CalendarRegistry.from_json_file(CONFIG_DIR / "calendars.json")


@pytest.fixture(scope="session")
def fixtures_dir() -> Path:
    return FIXTURES_DIR


@pytest.fixture(scope="session")
def config_dir() -> Path:
    return CONFIG_DIR
