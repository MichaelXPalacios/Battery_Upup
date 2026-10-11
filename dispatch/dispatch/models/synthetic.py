"""Synthetic prices, features and forecasters for tests (D1a). Nothing here touches ERCOT data or the real MLflow store.

`ConstantForecaster` is a deterministic `contracts.Forecaster` other agents can use as a fake in their own tests:
    from dispatch.models.synthetic import ConstantForecaster
"""
from __future__ import annotations

from typing import Optional

import numpy as np
import pandas as pd

from ..contracts import ForecastFrame
from . import interim_features as F
from .quantile import ModelSpec, QuantileForecaster, SPModel, ASPriceModel, to_utc, _max_published


def synthetic_prices(start="2024-01-07", days: int = 150, seed: int = 0, level: float = 30.0,
                     spike_rate: float = 0.004, amplitude: float = 1.0) -> pd.Series:
    """15-minute UTC prices with a daily shape (morning ramp, midday solar dip, evening peak), a weekend dip, an
    autocorrelated daily level, lognormal noise and rare spikes. Starts away from a DST change on purpose."""
    rng = np.random.default_rng(seed)
    idx = pd.date_range(pd.Timestamp(start, tz="UTC"), periods=days * 96, freq="15min", name="interval_start_utc")
    loc = idx.tz_convert("America/Chicago")
    hod = loc.hour.to_numpy() + loc.minute.to_numpy() / 60.0
    dow = loc.dayofweek.to_numpy()
    shape = (6.0 * np.exp(-0.5 * ((hod - 19.5) / 1.8) ** 2) + 3.0 * np.exp(-0.5 * ((hod - 7.5) / 1.5) ** 2)
             - 7.0 * np.exp(-0.5 * ((hod - 13.0) / 2.5) ** 2))
    day_level = np.repeat(level * np.exp(np.cumsum(rng.normal(0, 0.05, days)) * 0.3), 96)
    weekend = np.where(dow >= 5, 0.85, 1.0)
    noise = rng.lognormal(0.0, 0.12, len(idx))
    p = (day_level + amplitude * shape * day_level / level) * weekend * noise
    spikes = rng.random(len(idx)) < spike_rate
    p = np.where(spikes, p + rng.exponential(250.0, len(idx)), p)
    return pd.Series(np.round(p, 2), index=idx, name="rt_usd_per_mwh")


def synthetic_history(sp: str = "HB_TEST", **kw) -> F.PriceHistory:
    s = synthetic_prices(**kw)
    return F.PriceHistory.from_series(sp, s, sources=[{"name": "synthetic", "bytes": len(s), "sha256": "0" * 64}])


def synthetic_training(sp: str = "HB_TEST", days: int = 150, every: str = "4h", seed: int = 0):
    """(X, y, decision_utc, history) from the interim builder on synthetic prices."""
    h = synthetic_history(sp, days=days, seed=seed)
    start = h.ts(F.MIN_HISTORY_STEPS + 8)
    X, y, d = F.training_frame(sp, start, h.end - pd.Timedelta(days=1), every, history=h)
    return X, y, d, h


def hetero_regression(n: int = 20000, seed: int = 0):
    """y = signal(x) + sigma(x) * noise with sigma growing in `vol` and `horizon`: for quantile-coverage tests.

    Returns (features frame indexed by 15-minute UTC timestamps, y). Columns: x1, x2, vol, horizon."""
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2025-02-01", periods=n, freq="15min", tz="UTC", name="interval_start_utc")
    x1 = rng.uniform(0, 1, n)
    x2 = rng.normal(0, 1, n)
    vol = rng.uniform(1.0, 5.0, n)
    horizon = rng.integers(1, 97, n)
    sigma = vol * (1.0 + horizon / 96.0)
    y = 20.0 + 15.0 * x1 + 5.0 * x2 + sigma * rng.normal(0, 1, n)
    X = pd.DataFrame({"x1": x1, "x2": x2, "vol": vol, "horizon": horizon}, index=idx)
    return X, pd.Series(y, index=idx, name="y")


def fit_tiny_forecaster(sp: str = "HB_TEST", *, n_estimators: int = 40, days: int = 150, seed: int = 0,
                        with_as: bool = True, normalised: bool = True) -> tuple:
    """A small real QuantileForecaster on synthetic interim features. Returns (forecaster, X, y, decision_utc, history)."""
    X, y, d, h = synthetic_training(sp, days=days, seed=seed)
    feats = tuple(c for c in F.RT_FEATURES)
    spec = ModelSpec(feature_names=feats, anchor_col="rt_prof7_med" if normalised else None,
                     scale_cols=("rt_q90_672", "rt_q10_672") if normalised else (), weight_pow=0.5 if normalised else 0.0,
                     required_nonnull=("rt_last", "rt_mean_96"), n_estimators=n_estimators,
                     params=dict(num_leaves=15, min_data_in_leaf=50, max_bin=63))
    m = SPModel.fit(X, y.to_numpy(), spec, num_threads=4, info={"train_rows": len(X)})
    m.set_reference(X, n=500)
    as_model = None
    if with_as:
        loc = h.series().index
        hist_as = pd.DataFrame({p: 1.0 + 0.2 * np.sin(np.arange(len(loc)) / 40.0) + (i * 0.1)
                                for i, p in enumerate(["regup", "regdn", "rrs", "ecrs", "nspin"])}, index=loc)
        as_model = ASPriceModel.fit(hist_as, h.end)
    fc = QuantileForecaster({sp: m}, feature_set=f"{F.FEATURE_SET}/{F.FEATURE_VERSION}", as_model=as_model,
                            meta={"synthetic": True})
    return fc, X, y, d, h


class ConstantForecaster:
    """A trivially predictable Forecaster for other agents' tests: p50 = `level` $/MWh, p10/p90 = level -/+ `spread`.

    It applies the same fail-closed checks as the real model (tz-aware index, max_published_utc <= issued_utc)."""
    kind = "constant"

    def __init__(self, level: float = 30.0, spread: float = 10.0, version: str = "constant-1", settlement_point: str = "HB_NORTH"):
        self.level, self.spread, self.version, self.settlement_point = level, spread, version, settlement_point

    def predict(self, features: pd.DataFrame, issued_utc, *, settlement_point: Optional[str] = None) -> ForecastFrame:
        issued = to_utc(issued_utc, "issued_utc")
        max_pub = _max_published(features)
        if max_pub > issued:
            raise ValueError("features published after the issue time")
        idx = features.index.tz_convert("UTC")
        frame = pd.DataFrame({"rt_p10": self.level - self.spread, "rt_p50": self.level, "rt_p90": self.level + self.spread},
                             index=idx)
        return ForecastFrame(issued_utc=issued, settlement_point=settlement_point or self.settlement_point,
                             model_version=self.version, frame=frame, max_feature_published_utc=max_pub)
