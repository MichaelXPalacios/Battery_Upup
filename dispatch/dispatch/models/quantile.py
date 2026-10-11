"""LightGBM quantile regressors for the ERCOT real-time settlement point price (PRD FR4).

DESIGN (justified in notes/d1a.md)
  * One model per settlement point and per quantile (alpha 0.1, 0.5, 0.9), so a bundle holds 3 boosters per point.
  * ONE model spans horizons 1..96 (15-minute intervals, 24 h). The horizon is a numeric feature, not a bucket:
    all horizons share the daily/weekly price shape, trees split on `horizon` where the spread widens, and there are 3
    boosters per point instead of 12. Horizon is derived from the target index and `issued_utc` unless the feature
    frame carries its own `horizon` column.
  * The target can be normalised: the booster learns (y - anchor) / scale, where the anchor is a price that is known at
    issue time (a seasonal profile) and the scale is a robust recent spread. Prices are heavy tailed and the level moves
    by years (Uri 2021, gas 2022, battery saturation 2024+); trees cannot extrapolate a level, but they can learn how
    far the price tends to sit from a known anchor in units of recent volatility. With weights scale**weight_pow the
    pinball loss in dollars is preserved (pinball is positively homogeneous).
  * Quantile crossing is repaired by sorting the three quantiles row by row, then clipping to the ERCOT price bounds.
  * Simple ancillary-service price views (`as_<product>_p50`): the DAM clearing price of the target hour when the
    feature frame has it (published the day before), else a recent median of the same local hour. AS prices have
    collapsed to about $1-3/MW-h, so a median is the right size of model.

Models only forecast. Nothing here produces a setpoint (DESIGN.md).
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Mapping, Optional, Sequence

import numpy as np
import pandas as pd

from .. import config
from ..contracts import ForecastFrame, QUANTILES

ALPHAS = {"p10": 0.1, "p50": 0.5, "p90": 0.9}
H_MAX = 96
STEP = pd.Timedelta(minutes=15)
PRICE_CAP_USD = float(config.ERCOT_PRICE_CAP)       # REAL: system-wide offer cap $5,000/MWh since 2022
PRICE_FLOOR_USD = -251.0                            # ASSUMPTION: lowest RT SPP seen in the archive is -$251.00 (2022)
BUNDLE_SCHEMA = 1


CALENDAR_COLUMNS = frozenset({"horizon", "hour_local", "qtr_hour", "dow", "month", "is_holiday", "is_weekend"})   # D0's names


def is_calendar_feature(name: str) -> bool:
    """Deterministic functions of the target time (ours `tgt_*`, `horizon`; D0's `hour_local`, `dow`, ...): they cannot drift
    in a market sense, and a window of a few days always differs from a reference that spans a year, so a drift reference
    leaves them out."""
    return str(name).startswith("tgt_") or str(name) in CALENDAR_COLUMNS


class ForecastError(RuntimeError):
    """Base class for refusals. The service treats any of them as 'no forecast, no dispatch'."""


class MissingFeaturesError(ForecastError):
    """A column the model needs is absent, or a required value is null."""


class LeakageError(ForecastError):
    """The features claim data published after the issue time."""


class UnknownSettlementPointError(ForecastError):
    """The model has no booster for this settlement point."""


# ------------------------------------------------------------------------------------------------ small helpers
def to_utc(ts, what: str = "timestamp") -> pd.Timestamp:
    t = pd.Timestamp(ts)
    if t.tzinfo is None:
        raise ValueError(f"{what} must be tz-aware (UTC), got {ts!r}")
    return t.tz_convert("UTC")


def fix_crossing(q: np.ndarray) -> np.ndarray:
    """Make p10 <= p50 <= p90 row by row by sorting the quantile axis (the standard monotone rearrangement)."""
    return np.sort(np.asarray(q, dtype=np.float64), axis=-1)


def clip_prices(q: np.ndarray) -> np.ndarray:
    return np.clip(q, PRICE_FLOOR_USD, PRICE_CAP_USD)


def horizon_steps(index: pd.DatetimeIndex, issued_utc: pd.Timestamp) -> np.ndarray:
    """Horizon of each target interval: 1 for the interval containing the issue time, 2 for the next, ..."""
    base = to_utc(issued_utc, "issued_utc").floor("15min")
    return np.asarray((index - base) // STEP + 1, dtype=np.int64)


def sha256_bytes(*chunks: bytes) -> str:
    h = hashlib.sha256()
    for c in chunks:
        h.update(c)
    return h.hexdigest()


def bundle_sha256(directory: Path) -> str:
    """Deterministic hash of a bundle directory: relative path and content hash of every file, sorted."""
    directory = Path(directory)
    rows = []
    for p in sorted(directory.rglob("*")):
        if p.is_file():
            rel = p.relative_to(directory).as_posix()
            rows.append((rel, hashlib.sha256(p.read_bytes()).hexdigest()))
    return sha256_bytes(json.dumps(rows).encode("utf-8"))


# ------------------------------------------------------------------------------------------------ model spec
@dataclass
class ModelSpec:
    """Everything needed to turn a feature frame into price quantiles, stored with the boosters."""
    feature_names: tuple
    anchor_col: Optional[str] = None            # price the booster predicts deviations from (None: raw $/MWh target)
    scale_cols: tuple = ()                      # () none | (a,) scale=a | (hi, lo) scale=hi-lo
    scale_floor: float = 2.0                    # $/MWh floor under the scale
    weight_pow: float = 0.0                     # training weight = scale ** weight_pow
    required_nonnull: tuple = ()                # features that must be present and non-null (else fail closed)
    horizon_max: int = H_MAX
    n_estimators: int = 400
    params: dict = field(default_factory=dict)  # LightGBM parameters (the record of what was trained)
    calibration: dict = field(default_factory=dict)   # quantile -> additive offset in normalised units (conformal)

    def anchor(self, X: pd.DataFrame) -> np.ndarray:
        if self.anchor_col is None:
            return np.zeros(len(X))
        return X[self.anchor_col].to_numpy(dtype=np.float64)

    def scale(self, X: pd.DataFrame) -> np.ndarray:
        if not self.scale_cols:
            return np.ones(len(X))
        if len(self.scale_cols) == 1:
            s = X[self.scale_cols[0]].to_numpy(dtype=np.float64)
        else:
            s = X[self.scale_cols[0]].to_numpy(dtype=np.float64) - X[self.scale_cols[1]].to_numpy(dtype=np.float64)
        return np.maximum(s, self.scale_floor)

    def needed_columns(self) -> list:
        cols = list(self.feature_names) + [c for c in self.scale_cols] + ([self.anchor_col] if self.anchor_col else [])
        return sorted(set(cols), key=cols.index)

    def to_json(self) -> dict:
        d = asdict(self)
        d["feature_names"] = list(self.feature_names)
        d["scale_cols"] = list(self.scale_cols)
        d["required_nonnull"] = list(self.required_nonnull)
        return d

    @classmethod
    def from_json(cls, d: Mapping) -> "ModelSpec":
        d = dict(d)
        for k in ("feature_names", "scale_cols", "required_nonnull"):
            d[k] = tuple(d.get(k, ()))
        return cls(**d)


def default_lgbm_params(num_threads: int = 8, seed: int = 7) -> dict:
    """Starting point; Optuna (train.py) moves num_leaves, learning_rate, min_data_in_leaf, regularisation."""
    return dict(learning_rate=0.05, num_leaves=63, min_data_in_leaf=200, feature_fraction=0.8, bagging_fraction=0.7,
                bagging_freq=1, lambda_l2=1.0, max_bin=127, num_threads=num_threads, seed=seed, verbose=-1)


# ------------------------------------------------------------------------------------------------ one settlement point
class SPModel:
    """Three boosters (p10, p50, p90) for one settlement point."""

    def __init__(self, spec: ModelSpec, boosters: Mapping[str, "object"], info: Optional[dict] = None,
                 reference: Optional[pd.DataFrame] = None):
        self.spec = spec
        self.boosters = dict(boosters)
        self.info = dict(info or {})
        self.reference = reference            # a sample of the feature rows the model was trained on (drift monitoring, FR11)

    def set_reference(self, X: pd.DataFrame, n: int = 3000, seed: int = 7) -> None:
        """Keep a random sample of `X`'s market-state feature columns (not the calendar ones): the yardstick D2's
        population-stability check compares the served features against. Same column names as the served frame."""
        cols = [c for c in self.spec.feature_names if c in X.columns and not is_calendar_feature(c)]
        take = X[cols] if len(X) <= n else X[cols].sample(n=n, random_state=seed)
        self.reference = take.astype("float32").reset_index(drop=True)

    # ---- training
    @classmethod
    def fit(cls, X: pd.DataFrame, y, spec: ModelSpec, *, num_threads: int = 8, info: Optional[dict] = None,
            sample_weight=None) -> "SPModel":
        import copy
        import lightgbm as lgb
        spec = copy.deepcopy(spec)                      # the model owns its spec (calibration is stored on it)
        y = np.asarray(y, dtype=np.float64)
        anchor, scale = spec.anchor(X), spec.scale(X)
        z = (y - anchor) / scale
        ok = np.isfinite(z) & np.isfinite(scale)
        w = scale ** spec.weight_pow if spec.weight_pow else np.ones(len(y))
        if sample_weight is not None:
            w = w * np.asarray(sample_weight, dtype=np.float64)
        w = w / w[ok].mean()
        feats = list(spec.feature_names)
        arr = X[feats].to_numpy(dtype=np.float32)
        params = dict(default_lgbm_params(num_threads), **spec.params)
        params["num_threads"] = num_threads
        ds_params = {k: params[k] for k in ("max_bin", "min_data_in_leaf", "verbose") if k in params}
        ds = lgb.Dataset(arr[ok], label=z[ok], weight=w[ok], feature_name=feats, params=ds_params)
        boosters = {}
        for q, alpha in ALPHAS.items():
            p = dict(params, objective="quantile", alpha=alpha)
            boosters[q] = lgb.train(p, ds, num_boost_round=int(spec.n_estimators))
        meta = dict(info or {})
        meta.update(rows=int(ok.sum()), n_features=len(feats), trained_with=f"lightgbm {lgb.__version__}")
        return cls(spec, boosters, meta)

    # ---- prediction
    def raw_quantiles(self, X: pd.DataFrame, *, num_threads: int = 1) -> np.ndarray:
        """Quantiles in $/MWh, unsorted and unclipped, shape (n, 3) in the order p10, p50, p90."""
        arr = X[list(self.spec.feature_names)].to_numpy(dtype=np.float32)
        z = np.column_stack([self.boosters[q].predict(arr, num_threads=num_threads) for q in QUANTILES])
        if self.spec.calibration:
            z = z + np.array([self.spec.calibration.get(q, 0.0) for q in QUANTILES])[None, :]
        return self.spec.anchor(X)[:, None] + self.spec.scale(X)[:, None] * z

    def quantiles(self, X: pd.DataFrame, *, num_threads: int = 1) -> np.ndarray:
        return clip_prices(fix_crossing(self.raw_quantiles(X, num_threads=num_threads)))

    def calibrate(self, X: pd.DataFrame, y, *, num_threads: int = 1, min_rows: int = 2000) -> dict:
        """Split-conformal offsets: shift each quantile so its hit rate on held-out rows equals its level."""
        y = np.asarray(y, dtype=np.float64)
        arr = X[list(self.spec.feature_names)].to_numpy(dtype=np.float32)
        anchor, scale = self.spec.anchor(X), self.spec.scale(X)
        z_true = (y - anchor) / scale
        ok = np.isfinite(z_true)
        if ok.sum() < min_rows:
            raise ValueError(f"need at least {min_rows} calibration rows, got {int(ok.sum())}")
        out = {}
        for q, alpha in ALPHAS.items():
            z_hat = self.boosters[q].predict(arr[ok], num_threads=num_threads)
            out[q] = float(np.quantile(z_true[ok] - z_hat, alpha))
        self.spec.calibration = out
        return out

    def importance(self) -> pd.DataFrame:
        cols = list(self.spec.feature_names)
        return pd.DataFrame({q: self.boosters[q].feature_importance("gain") for q in QUANTILES}, index=cols)

    # ---- persistence
    def save(self, directory: Path) -> None:
        directory = Path(directory)
        directory.mkdir(parents=True, exist_ok=True)
        for q, b in self.boosters.items():
            b.save_model(str(directory / f"{q}.txt"))
        (directory / "spec.json").write_text(json.dumps({"spec": self.spec.to_json(), "info": self.info}, indent=1,
                                                         default=_json_default), encoding="utf-8")
        if self.reference is not None:
            self.reference.to_parquet(directory / "reference.parquet")

    @classmethod
    def load(cls, directory: Path) -> "SPModel":
        import lightgbm as lgb
        directory = Path(directory)
        d = json.loads((directory / "spec.json").read_text(encoding="utf-8"))
        boosters = {q: lgb.Booster(model_file=str(directory / f"{q}.txt")) for q in QUANTILES}
        ref = directory / "reference.parquet"
        return cls(ModelSpec.from_json(d["spec"]), boosters, d.get("info", {}),
                   pd.read_parquet(ref) if ref.exists() else None)


def _json_default(o):
    if isinstance(o, (np.floating, np.integer)):
        return o.item()
    if isinstance(o, (pd.Timestamp,)):
        return o.isoformat()
    if isinstance(o, np.ndarray):
        return o.tolist()
    raise TypeError(f"not JSON serialisable: {type(o)}")


# ------------------------------------------------------------------------------------------------ AS price view
AS_PRODUCTS = tuple(p.lower() for p in config.AS_PRODUCTS)


def as_p50_col(product: str) -> str:
    return f"as_{product.lower()}_p50"


class ASPriceModel:
    """Median ancillary-service price per product by local hour of day: the 'seasonal / recent median' view.

    `table[product][hour]` is the median clearing price ($/MW-h) of that local hour over the last `window_days` days of
    history at fit time. At prediction time a feature frame may supply fresher numbers, in this order of preference:
      as_<product>_dam   the day-ahead clearing price of the target hour (public the day before), when known
                         (D0's `dam_as_<product>` column is read the same way)
      as_<product>_med14 the point-in-time recent median of the same local hour (D0's rt_v2 name: `dam_as_<product>_med14`)
    and only then the stored table, whose age is reported on the forecast (`attrs['as_source']`).
    """

    def __init__(self, table: Mapping[str, Mapping[int, float]], window_days: int = 14, fitted_to: Optional[str] = None):
        self.table = {p: {int(h): float(v) for h, v in t.items()} for p, t in table.items()}
        self.window_days = int(window_days)
        self.fitted_to = fitted_to

    @property
    def products(self) -> tuple:
        return tuple(self.table)

    @classmethod
    def fit(cls, history: pd.DataFrame, asof_utc, window_days: int = 14) -> "ASPriceModel":
        """`history`: wide frame on a UTC DatetimeIndex (interval start), columns = product names (any case), $/MW-h.
        Only rows strictly before `asof_utc` are used."""
        asof = to_utc(asof_utc, "asof_utc")
        h = history[history.index < asof]
        h = h[h.index >= asof - pd.Timedelta(days=window_days)]
        if h.empty:
            raise ValueError("no AS history inside the window")
        hour = h.index.tz_convert(config.TZ_MARKET).hour
        table = {}
        for c in h.columns:
            med = h[c].groupby(hour).median().dropna()
            if len(med):
                table[str(c).lower()] = {int(k): float(v) for k, v in med.items()}
        return cls(table, window_days, fitted_to=asof.isoformat())

    def predict(self, index: pd.DatetimeIndex, features: Optional[pd.DataFrame] = None) -> pd.DataFrame:
        hour = index.tz_convert(config.TZ_MARKET).hour.to_numpy()
        out, sources = {}, set()
        for p, t in self.table.items():
            fallback = np.array([t.get(int(h), np.nan) for h in hour])
            if np.isnan(fallback).any():                                  # an hour with no history: the product median
                fallback = np.where(np.isnan(fallback), float(np.median(list(t.values()))), fallback)
            vals = fallback.copy()
            src = np.full(len(index), "table", dtype=object)
            if features is not None:
                # later overrides earlier: the day-ahead price (ours `as_<p>_dam`, D0's `dam_as_<p>`) wins over the median
                for suffix, col in (("med14", f"as_{p}_med14"), ("med14", f"dam_as_{p}_med14"), ("dam", f"dam_as_{p}"),
                                    ("dam", f"as_{p}_dam")):
                    if col in features.columns:
                        v = features[col].to_numpy(dtype=np.float64)
                        m = np.isfinite(v)
                        vals = np.where(m, v, vals)
                        src = np.where(m, suffix, src)
            out[as_p50_col(p)] = np.maximum(vals, 0.0)
            sources.update(src.tolist())
        res = pd.DataFrame(out, index=index)
        res.attrs["as_source"] = sorted(sources)
        return res

    def to_json(self) -> dict:
        return {"table": self.table, "window_days": self.window_days, "fitted_to": self.fitted_to}

    @classmethod
    def from_json(cls, d: Mapping) -> "ASPriceModel":
        return cls(d["table"], d.get("window_days", 14), d.get("fitted_to"))


# ------------------------------------------------------------------------------------------------ the Forecaster
def _max_published(features: pd.DataFrame) -> pd.Timestamp:
    if "max_published_utc" in features.columns:
        v = features["max_published_utc"]
        if v.isna().any():
            raise MissingFeaturesError("max_published_utc has nulls: cannot prove the features are point in time")
        return to_utc(v.max(), "max_published_utc")
    mp = features.attrs.get("max_published_utc")
    if mp is None:
        raise MissingFeaturesError("features carry no max_published_utc (column or attrs): cannot prove they are "
                                   "point in time, so no forecast")
    return to_utc(mp, "max_published_utc")


def resolve_sp(features: pd.DataFrame, settlement_point: Optional[str], available: Sequence[str]) -> str:
    sp = settlement_point or features.attrs.get("settlement_point")
    if sp is None and "settlement_point" in features.columns:
        u = features["settlement_point"].dropna().unique()
        if len(u) == 1:
            sp = str(u[0])
    if sp is None and len(available) == 1:
        sp = next(iter(available))
    if sp is None:
        raise MissingFeaturesError("settlement point unknown: pass settlement_point=, set features.attrs"
                                   "['settlement_point'], or add a 'settlement_point' column")
    return str(sp)


class QuantileForecaster:
    """Implements `contracts.Forecaster` for RT price quantiles (and the simple AS view).

    `predict` fails closed: an unknown settlement point, a missing feature column, a null in a required feature, a
    horizon outside what was trained, or features published after `issued_utc` all raise a ForecastError and produce
    no forecast. Multi-point bundles hold one SPModel per settlement point."""
    kind = "lightgbm_quantile"

    def __init__(self, models: Mapping[str, SPModel], *, version: str = "unregistered", feature_set: str = "",
                 as_model: Optional[ASPriceModel] = None, meta: Optional[dict] = None):
        if not models:
            raise ValueError("at least one settlement point model is required")
        self.models = dict(models)
        self.version = version
        self.feature_set = feature_set
        self.as_model = as_model
        self.meta = dict(meta or {})
        self._served: dict = {}            # settlement points this instance has produced forecasts for, in order of first use

    @property
    def settlement_points(self) -> tuple:
        return tuple(self.models)

    def reference_for(self, settlement_point: str) -> Optional[pd.DataFrame]:
        """Sample of the training features of one settlement point, market-state columns only (None if the bundle has none)."""
        ref = self.models[settlement_point].reference
        if ref is None:
            return None
        return ref[[c for c in ref.columns if not is_calendar_feature(c)]]

    @property
    def reference_frame(self) -> Optional[pd.DataFrame]:
        """What D2's feature-drift monitor reads (`getattr(forecaster, "reference_frame", None)`): a few thousand rows of the
        training features, market-state columns only, named as in the served frame.

        A multi-point bundle holds one sample per point. The drift monitor asks without naming an asset, so this returns the
        samples of the points this forecaster has actually served (so a service that serves HB_NORTH is compared with
        HB_NORTH's own training sample), and all points stacked until it has served any.

        CAUTION (measured, notes/d1a.md section 7.1): these features are 24 h to 7 day rolling statistics, so a PSI test of a window of
        hours or days against a year-long sample flags nearly every feature even on in-distribution data (99% of features for a
        window of 12 consecutive decisions, 87-93% for 7 days). PSI against this frame is therefore alert-only (config.DRIFT_PSI_HALTS
        is False); the halt-eligible check is output calibration (`holdout_metrics`, notes/d1a.md section 7.2). Setting the
        environment variable DISPATCH_DRIFT_REFERENCE=off makes this property return None (the monitor then skips the check)."""
        import os
        if os.environ.get("DISPATCH_DRIFT_REFERENCE", "").strip().lower() in ("off", "0", "false", "no"):
            return None
        served = [sp for sp in self._served if sp in self.models]
        refs = [self.reference_for(sp) for sp in (served or self.models)]
        refs = [r for r in refs if r is not None]
        if not refs:
            return None
        return refs[0] if len(refs) == 1 else pd.concat(refs, ignore_index=True)

    @property
    def holdout_metrics(self) -> dict:
        """What D2's output-calibration monitor compares live forecasts with (DESIGN.md "Monitoring policy"): for each settlement
        point this bundle serves, the metrics of the frozen model on its holdout, as `cv.reference_metrics` builds them:

            {"HB_NORTH": {"pooled": {"n", "pinball_mean", "pinball_p10/p50/p90", "hit_p10/p50/p90", "coverage_p10_p90", "mae_p50",
                                     "bias_p50", "width_p10_p90"},
                          "by_horizon": {"h1-4": {...}, "h5-16": {...}, "h17-48": {...}, "h49-96": {...}},
                          "window": {"start", "end", "decision_every", "horizons"}}, ...}

        Empty when the bundle was not built by `train.release` (a test bundle, an old version). The same numbers are on the model
        version as the tag `holdout.metrics` (`Registry.holdout_metrics(version)` reads it without loading the bundle). A copy is returned."""
        import copy
        h = (self.meta or {}).get("holdout") or {}
        return copy.deepcopy({sp: h[sp] for sp in self.models if sp in h})

    def holdout_for(self, settlement_point: str, horizon=None) -> dict:
        """Reference numbers (pinball_mean, hit_p10/p50/p90, mae_p50) for one point and a mix of horizons: pooled when `horizon` is None,
        else weighted by the share of `horizon` (an array of 1..96 steps, one per scored forecast) in each horizon bucket. Raises
        KeyError when the bundle has no holdout metrics for the point."""
        from .cv import reference_for_horizons
        h = (self.meta or {}).get("holdout") or {}
        if settlement_point not in h or settlement_point not in self.models:
            raise KeyError(f"no holdout metrics for {settlement_point!r} in model {self.version}")
        return reference_for_horizons(h[settlement_point], horizon)

    # ---- Forecaster protocol
    def predict(self, features: pd.DataFrame, issued_utc, *, settlement_point: Optional[str] = None) -> ForecastFrame:
        issued = to_utc(issued_utc, "issued_utc")
        sp = resolve_sp(features, settlement_point, self.models)
        if sp not in self.models:
            raise UnknownSettlementPointError(f"model {self.version} has no booster for {sp!r}; has {list(self.models)}")
        m = self.models[sp]
        if len(features) == 0:
            raise MissingFeaturesError("empty feature frame")
        if not isinstance(features.index, pd.DatetimeIndex) or features.index.tz is None:
            raise MissingFeaturesError("features must be indexed by tz-aware UTC interval_start_utc")
        max_pub = _max_published(features)
        if max_pub > issued:
            raise LeakageError(f"features use data published {max_pub} after issue time {issued}")
        idx = features.index.tz_convert("UTC")
        X = features
        if "horizon" not in X.columns:
            X = X.assign(horizon=horizon_steps(idx, issued))
        hz = X["horizon"].to_numpy()
        if hz.min() < 1 or hz.max() > m.spec.horizon_max:
            raise MissingFeaturesError(f"horizon {int(hz.min())}..{int(hz.max())} outside the trained 1..{m.spec.horizon_max}")
        missing = [c for c in m.spec.needed_columns() if c not in X.columns]
        if missing:
            raise MissingFeaturesError(f"feature columns missing: {missing}")
        if m.spec.required_nonnull:
            bad = [c for c in m.spec.required_nonnull if X[c].isna().any()]
            if bad:
                raise MissingFeaturesError(f"required features null: {bad}")
        if not np.isfinite(m.spec.scale(X)).all() or not np.isfinite(m.spec.anchor(X)).all():
            raise MissingFeaturesError("anchor/scale features are not finite")
        q = m.quantiles(X)
        frame = pd.DataFrame({f"rt_{k}": q[:, i] for i, k in enumerate(QUANTILES)}, index=idx)
        if self.as_model is not None:
            as_df = self.as_model.predict(idx, X)
            frame = pd.concat([frame, as_df], axis=1)
            frame.attrs["as_source"] = as_df.attrs.get("as_source", [])
        frame.index.name = "interval_start_utc"
        self._served.setdefault(sp, True)
        return ForecastFrame(issued_utc=issued, settlement_point=sp, model_version=str(self.version), frame=frame,
                             max_feature_published_utc=max_pub)

    # ---- persistence (an MLflow bundle is exactly this directory)
    def save(self, directory: Path) -> Path:
        directory = Path(directory)
        directory.mkdir(parents=True, exist_ok=True)
        for sp, m in self.models.items():
            m.save(directory / "models" / sp)
        if self.as_model is not None:
            (directory / "as_model.json").write_text(json.dumps(self.as_model.to_json()), encoding="utf-8")
        (directory / "model.json").write_text(json.dumps({
            "schema": BUNDLE_SCHEMA, "kind": self.kind, "settlement_points": list(self.models),
            "feature_set": self.feature_set, "meta": self.meta}, indent=1, default=_json_default), encoding="utf-8")
        return directory

    @classmethod
    def load(cls, directory: Path, version: str = "unregistered") -> "QuantileForecaster":
        directory = Path(directory)
        d = json.loads((directory / "model.json").read_text(encoding="utf-8"))
        if d.get("kind") != cls.kind:
            raise ValueError(f"bundle kind {d.get('kind')!r} is not {cls.kind!r}")
        models = {sp: SPModel.load(directory / "models" / sp) for sp in d["settlement_points"]}
        asf = directory / "as_model.json"
        as_model = ASPriceModel.from_json(json.loads(asf.read_text(encoding="utf-8"))) if asf.exists() else None
        return cls(models, version=version, feature_set=d.get("feature_set", ""), as_model=as_model, meta=d.get("meta"))

    def subset(self, points: Sequence[str]) -> "QuantileForecaster":
        return QuantileForecaster({p: self.models[p] for p in points}, version=self.version, feature_set=self.feature_set,
                                  as_model=self.as_model, meta=self.meta)


# ------------------------------------------------------------------------------------------------ batch helper
def batch_predict(forecaster, X: pd.DataFrame, decision_utc, *, settlement_point: Optional[str] = None) -> pd.DataFrame:
    """Forecasts for many decisions at once, in long form: the shape the evaluation and the shadow run share.

    `X`, `decision_utc` are as returned by a `training_frame` (rows aligned). `forecaster.predict` is called once per
    decision, so every row passes the same leakage / missing-feature checks as a live call. The result is indexed like X
    with columns decision_utc, horizon (when X has it), rt_p10, rt_p50, rt_p90 and any as_<product>_p50."""
    dec = pd.DatetimeIndex(decision_utc)
    if dec.tz is None:
        raise ValueError("decision_utc must be tz-aware (UTC)")
    codes, uniq = pd.factorize(dec)
    order = np.argsort(codes, kind="stable")
    bounds = np.searchsorted(codes[order], np.arange(len(uniq) + 1))
    sp = settlement_point or X.attrs.get("settlement_point")
    parts = []
    for k, d in enumerate(uniq):
        rows = order[bounds[k]:bounds[k + 1]]
        sub = X.iloc[rows]
        if sp:
            sub.attrs["settlement_point"] = sp
        fc = forecaster.predict(sub, d, settlement_point=sp) if sp else forecaster.predict(sub, d)
        f = fc.frame.copy()
        f.insert(0, "decision_utc", d)
        f["_row"] = rows
        parts.append(f)
    out = pd.concat(parts).sort_values("_row", kind="stable")
    out.index = X.index[out.pop("_row").to_numpy()]
    if "horizon" in X.columns:
        out.insert(1, "horizon", X["horizon"].to_numpy())
    return out
