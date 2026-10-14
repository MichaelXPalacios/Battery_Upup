import os
import sys
import tempfile
from pathlib import Path

import pytest

PROJECT = Path(__file__).resolve().parents[1]
if str(PROJECT) not in sys.path:
    sys.path.insert(0, str(PROJECT))


@pytest.fixture()
def dispatch_home(tmp_path, monkeypatch):
    """Point DISPATCH_HOME at a temp dir and reload path-dependent modules, so tests never touch the real lake."""
    monkeypatch.setenv("DISPATCH_HOME", str(tmp_path / "home"))
    import importlib
    import dispatch.paths as p
    importlib.reload(p)
    p.ensure()
    yield p
    try:                                   # release lake file handles before the temp dir is removed (Windows locks)
        from dispatch.data import lake
        lake.close_all()
    except Exception:
        pass
    monkeypatch.delenv("DISPATCH_HOME", raising=False)
    importlib.reload(p)


@pytest.fixture()
def mlflow_registry(tmp_path):
    """A fresh MLflow store and model registry on a temp sqlite file, for any test that registers, promotes or loads a model.

    Added by I1 (10 Oct 2026), the only change to this file, at D1a's request (notes/d1a.md section 11): `config.MLFLOW_TRACKING_URI` is read
    when `dispatch.config` is imported, so the `dispatch_home` fixture does not redirect it, and a test that called the module-level
    `registry.load / promote / register` would read the REAL store (writes to it are refused under pytest). This fixture builds a `Registry` on a
    temp URI and also makes the module-level functions follow it (`registry.configure`), then puts the previous default back, so
    `wiring.load_model` (which is `registry.load`) serves the model a test registered here. It returns the `Registry`.
    """
    from dispatch.registry import mlflow_registry as R
    d = tmp_path / "mlflow"
    previous = R._default
    reg = R.configure("sqlite:///" + (d / "mlflow.db").as_posix(), artifact_root=d / "artifacts", cache_dir=d / "cache")
    try:
        yield reg
    finally:
        R._default = previous
