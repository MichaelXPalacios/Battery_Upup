"""Baseline forecasters the LightGBM quantile model has to beat (PRD FR4; D1a brief).

  seasonal_naive_1d   the same 15-minute interval yesterday (falls back to two days ago when yesterday's price is not yet
                      public at the horizon, then to last week)
  seasonal_naive_7d   the same interval last week
  profile_median_7d   EXTRA, stronger than the two above: the median of the same time of day over the last 7 days. It is also
                      the anchor the LightGBM model predicts deviations from, so beating it shows the model adds skill
                      beyond a robust seasonal profile (seasonal-naive and the arena blend are mean/last-value based and
                      are hurt by spikes)
  arena_profile       the research arena's Forecaster (docs/research/arena/arena/algorithms.py, A2) point forecast:
                          f = 0.5 * History.profile(weekend, 14 days) + 0.3 * yesterday + 0.2 * the day a week ago
                      `History.profile` is the mean over the last 14 days of the same weekday/weekend type at the same
                      time of day, falling back to all 14 days when fewer than two such days exist. D0's builder (rt_v1)
                      emits exactly that quantity as `rt_prof14_dt` (as the retired interim builder did); tests/test_models.py
                      checks it against `arena.algorithms.History.profile` itself (through `dispatch.paths.use_arena()`).
                      NOT ported: the arena's swing rescale (`k = clip(typical / own, 1, 3)`). It sharpens the forecast so
                      the arena's dynamic programme sees full-size spreads; it makes the point forecast worse by MAE, and
                      it needs a whole forecast day while a decision sees a 24 h window. The arena's closed-loop `act`
                      (acting on each real price as it arrives) is a dispatch policy, not a forecast, and is compared
                      through `cv.decision_value`.

All three are `Forecaster`s (`.version`, `.predict(features, issued_utc) -> ForecastFrame`), so the registry can serve
them. They are driven by feature columns (so they work on any frame that carries them) and obey the same fail-closed and
leakage rules as the LightGBM model. A point forecast has no quantiles, so p10/p90 come from the empirical quantiles of
its own past errors: (y - point) / scale per horizon bucket, with scale the trailing 7-day p90 - p10 spread. That makes
the baselines honest about uncertainty: a baseline is not penalised for lacking a band it was never given.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Optional, Sequence

import numpy as np
import pandas as pd

from ..contracts import ForecastFrame, QUANTILES
from .quantile import (BUNDLE_SCHEMA, LeakageError, MissingFeaturesError, UnknownSettlementPointError,
                       _json_default, _max_published, clip_prices, fix_crossing, resolve_sp, to_utc, horizon_steps)

HORIZON_BUCKETS = ((1, 4), (5, 16), (17, 48), (49, 96))   # ASSUMPTION: 1 h, 4 h, 12 h, 24 h of lead time
BAND_LEVELS = (0.1, 0.9)
SCALE_COLS = ("rt_q90_672", "rt_q10_672")
SCALE_FLOOR = 2.0                                          # $/MWh floor under the scale

CHAIN_1D = ("rt_lag_1d", "rt_lag_2d", "rt_lag_7d", "rt_prof7_med")
CHAIN_7D = ("rt_lag_7d", "rt_lag_14d", "rt_prof7_med")
ARENA_WEIGHTS = (0.5, 0.3, 0.2)                            # arena Forecaster.forecast: profile, last day, week ago
ARENA_PROFILE_COL = "rt_prof14_dt"


def bucket_of(horizon: np.ndarray) -> np.ndarray:
    b = np.zeros(len(horizon), dtype=np.int64)
    for k, (lo, hi) in enumerate(HORIZON_BUCKETS):
        b[(horizon >= lo) & (horizon <= hi)] = k
    return b


def chain(X: pd.DataFrame, cols: Sequence[str]) -> np.ndarray:
    """First finite value along `cols`, row by row."""
    out = np.full(len(X), np.nan)
    for c in cols:
        if c not in X.columns:
            continue
        v = X[c].to_numpy(dtype=np.float64)
        out = np.where(np.isfinite(out), out, v)
    return out


@dataclass
class ResidualBands:
    """Empirical p10 / p90 of (y - point) / scale for each horizon bucket."""
    lo: tuple
    hi: tuple

    @classmethod
    def fit(cls, y, point, scale, horizon) -> "ResidualBands":
        y, point, scale, horizon = (np.asarray(a, dtype=np.float64) for a in (y, point, scale, horizon))
        z = (y - point) / scale
        ok = np.isfinite(z)
        b = bucket_of(horizon)
        lo, hi = [], []
        for k in range(len(HORIZON_BUCKETS)):
            zk = z[ok & (b == k)]
            if len(zk) < 50:
                zk = z[ok]
            lo.append(float(np.quantile(zk, BAND_LEVELS[0])))
            hi.append(float(np.quantile(zk, BAND_LEVELS[1])))
        return cls(tuple(lo), tuple(hi))

    def apply(self, point, scale, horizon):
        b = bucket_of(np.asarray(horizon))
        lo, hi = np.asarray(self.lo)[b], np.asarray(self.hi)[b]
        return point + lo * scale, point + hi * scale

    def to_json(self) -> dict:
        return {"lo": list(self.lo), "hi": list(self.hi)}

    @classmethod
    def from_json(cls, d: Mapping) -> "ResidualBands":
        return cls(tuple(d["lo"]), tuple(d["hi"]))


class BaselineForecaster:
    """Shared machinery: calibrated residual bands per settlement point and the Forecaster protocol."""
    kind = "baseline"
    name = "baseline"
    needs: tuple = ()                      # feature columns the point forecast reads

    def __init__(self, *, version: Optional[str] = None, bands: Optional[Mapping[str, ResidualBands]] = None,
                 feature_set: str = "", horizon_max: int = 96):
        self.version = version or f"baseline/{self.name}"
        self.bands = dict(bands or {})
        self.feature_set = feature_set
        self.horizon_max = horizon_max
        self.meta: dict = {}

    @property
    def settlement_points(self) -> tuple:
        return tuple(self.bands)

    # ---- to override
    def point(self, X: pd.DataFrame) -> np.ndarray:
        raise NotImplementedError

    def params(self) -> dict:
        return {}

    # ---- fitting
    @staticmethod
    def scale(X: pd.DataFrame) -> np.ndarray:
        return np.maximum(X[SCALE_COLS[0]].to_numpy(dtype=np.float64) - X[SCALE_COLS[1]].to_numpy(dtype=np.float64), SCALE_FLOOR)

    def fit(self, sp: str, X: pd.DataFrame, y) -> "BaselineForecaster":
        """Calibrate the p10 / p90 bands on past (training-window) errors for one settlement point."""
        pt = self.point(X)
        self.bands[sp] = ResidualBands.fit(np.asarray(y), pt, self.scale(X), X["horizon"].to_numpy())
        return self

    # ---- prediction
    def quantiles(self, X: pd.DataFrame, sp: str) -> np.ndarray:
        if sp not in self.bands:
            raise UnknownSettlementPointError(f"{self.version} was not fitted for {sp!r}; has {list(self.bands)}")
        pt = self.point(X)
        lo, hi = self.bands[sp].apply(pt, self.scale(X), X["horizon"].to_numpy())
        return clip_prices(fix_crossing(np.column_stack([lo, pt, hi])))

    def predict(self, features: pd.DataFrame, issued_utc, *, settlement_point: Optional[str] = None) -> ForecastFrame:
        issued = to_utc(issued_utc, "issued_utc")
        sp = resolve_sp(features, settlement_point, self.bands)
        if len(features) == 0:
            raise MissingFeaturesError("empty feature frame")
        if not isinstance(features.index, pd.DatetimeIndex) or features.index.tz is None:
            raise MissingFeaturesError("features must be indexed by tz-aware UTC interval_start_utc")
        max_pub = _max_published(features)
        if max_pub > issued:
            raise LeakageError(f"features use data published {max_pub} after issue time {issued}")
        idx = features.index.tz_convert("UTC")
        X = features if "horizon" in features.columns else features.assign(horizon=horizon_steps(idx, issued))
        if X["horizon"].min() < 1 or X["horizon"].max() > self.horizon_max:
            raise MissingFeaturesError(f"horizon outside 1..{self.horizon_max}")
        missing = [c for c in tuple(self.needs) + SCALE_COLS if c not in X.columns]
        if missing:
            raise MissingFeaturesError(f"feature columns missing: {missing}")
        if not np.isfinite(self.point(X)).all():
            raise MissingFeaturesError(f"{self.name}: point forecast is not finite for some rows (lag features missing)")
        q = self.quantiles(X, sp)
        frame = pd.DataFrame({f"rt_{k}": q[:, i] for i, k in enumerate(QUANTILES)}, index=idx)
        frame.index.name = "interval_start_utc"
        return ForecastFrame(issued_utc=issued, settlement_point=sp, model_version=str(self.version), frame=frame,
                             max_feature_published_utc=max_pub)

    # ---- persistence
    def save(self, directory: Path) -> Path:
        directory = Path(directory)
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "model.json").write_text(json.dumps({
            "schema": BUNDLE_SCHEMA, "kind": self.kind, "name": self.name, "params": self.params(),
            "settlement_points": list(self.bands), "feature_set": self.feature_set, "horizon_max": self.horizon_max,
            "bands": {sp: b.to_json() for sp, b in self.bands.items()}, "meta": self.meta}, indent=1,
            default=_json_default), encoding="utf-8")
        return directory

    @classmethod
    def load(cls, directory: Path, version: Optional[str] = None) -> "BaselineForecaster":
        d = json.loads((Path(directory) / "model.json").read_text(encoding="utf-8"))
        if d.get("kind") != "baseline":
            raise ValueError(f"bundle kind {d.get('kind')!r} is not a baseline")
        klass, _ = BASELINES[d["name"]]
        obj = klass(**d.get("params", {}), version=version, bands={sp: ResidualBands.from_json(b) for sp, b in d["bands"].items()},
                    feature_set=d.get("feature_set", ""), horizon_max=d.get("horizon_max", 96))
        obj.meta = d.get("meta", {})
        return obj


class SeasonalNaive(BaselineForecaster):
    """Same interval `days` days ago, as published at the issue time."""

    def __init__(self, days: int = 1, **kw):
        if days not in (1, 7):
            raise ValueError("days must be 1 or 7")
        self.days = days
        self.name = f"seasonal_naive_{days}d"
        self.needs = CHAIN_1D if days == 1 else CHAIN_7D
        super().__init__(**kw)

    def params(self) -> dict:
        return {"days": self.days}

    def point(self, X: pd.DataFrame) -> np.ndarray:
        return chain(X, self.needs)


class ProfileMedian(BaselineForecaster):
    """Median of the same time of day over the last 7 days (the model's own anchor)."""
    name = "profile_median_7d"
    needs = ("rt_prof7_med", "rt_lag_2d")

    def point(self, X: pd.DataFrame) -> np.ndarray:
        return chain(X, ("rt_prof7_med", "rt_lag_2d", "rt_lag_7d"))


class ArenaProfile(BaselineForecaster):
    """The arena Forecaster's recent-days blend: 0.5 profile(14 d, weekday type) + 0.3 yesterday + 0.2 last week."""
    name = "arena_profile"

    def __init__(self, weights: Sequence[float] = ARENA_WEIGHTS, **kw):
        self.weights = tuple(weights)
        self.needs = (ARENA_PROFILE_COL,) + CHAIN_1D[:2] + ("rt_lag_7d",)
        super().__init__(**kw)

    def params(self) -> dict:
        return {"weights": list(self.weights)}

    def point(self, X: pd.DataFrame) -> np.ndarray:
        prof = X[ARENA_PROFILE_COL].to_numpy(dtype=np.float64)
        yday = chain(X, ("rt_lag_1d", "rt_lag_2d"))                 # History.last(1): yesterday's price at this time
        week = chain(X, ("rt_lag_7d",))                              # History.week_ago()
        w = self.weights
        yday = np.where(np.isfinite(yday), yday, prof)
        week = np.where(np.isfinite(week), week, prof)
        return w[0] * prof + w[1] * yday + w[2] * week


BASELINES = {"seasonal_naive_1d": (SeasonalNaive, {"days": 1}),
             "seasonal_naive_7d": (SeasonalNaive, {"days": 7}),
             "arena_profile": (ArenaProfile, {}),
             "profile_median_7d": (ProfileMedian, {})}


def make_baseline(name: str, **kw) -> BaselineForecaster:
    klass, params = BASELINES[name]
    return klass(**params, **kw)


def fit_baselines(sp: str, X: pd.DataFrame, y, names: Sequence[str] = tuple(BASELINES)) -> dict:
    """Calibrated baselines for one settlement point from a training frame."""
    return {n: make_baseline(n).fit(sp, X, y) for n in names}
