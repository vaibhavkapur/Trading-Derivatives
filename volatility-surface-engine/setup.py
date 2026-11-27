"""Minimal setuptools shim for editable installs on older pip versions (e.g. pip 21.x)."""

from setuptools import find_packages, setup

setup(
    name="volatility-surface-engine",
    version="0.1.0",
    package_dir={"": "src"},
    packages=find_packages(where="src"),
    entry_points={"console_scripts": ["vse=volatility_surface_engine.cli:main"]},
)
