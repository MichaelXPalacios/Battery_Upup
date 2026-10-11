"""Model bundles: the directory a registered model version carries (what MLflow stores as artifacts/bundle).

A bundle is a plain directory (`model.json` plus LightGBM text files / JSON), never a pickle, so a registered version can
be inspected, diffed and hashed, and loading it cannot execute code. `bundle_sha256` is the integrity hash the registry
records at registration and checks at load.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from .baselines import BaselineForecaster
from .quantile import QuantileForecaster, bundle_sha256

__all__ = ["load_forecaster", "save_forecaster", "bundle_sha256", "bundle_kind"]


def bundle_kind(directory: Path) -> str:
    return json.loads((Path(directory) / "model.json").read_text(encoding="utf-8"))["kind"]


def load_forecaster(directory: Path, version: Optional[str] = None):
    """Rebuild the Forecaster stored in `directory`."""
    kind = bundle_kind(directory)
    if kind == QuantileForecaster.kind:
        return QuantileForecaster.load(directory, version or "unregistered")
    if kind == BaselineForecaster.kind:
        return BaselineForecaster.load(directory, version)
    raise ValueError(f"unknown model kind {kind!r} in {directory}")


def save_forecaster(forecaster, directory: Path) -> str:
    """Write the bundle and return its sha256."""
    forecaster.save(Path(directory))
    return bundle_sha256(Path(directory))
