"""Train, tune, evaluate and register the RT price quantile models (PRD FR4, FR6).

PIPELINE (one settlement point at a time; `release` then bundles the points that pass into one registry version)

  1. data     features and labels from D0's point-in-time builder, `features.build.training_frame` (feature set `rt_v1`),
              under D0's own column names, so the model is trained on, and later served from, exactly what the leakage tests
              of D0 guard. (Version 1 was trained on D1a's interim builder, retired on 11 Oct 2026; `features/compat.py` still
              serves it.) Training decisions 2019-01-01 .. 2024-12-31; the holdout is 2025-01-01 to the latest data.
  2. tuning   Optuna (TPE, median pruner) on purged walk-forward folds inside 2019-2024. The holdout is never seen.
  3. CV       a purged walk-forward report over 2022-2024 with the chosen parameters, against the baselines.
  4. fit      all of 2019-2024 (purged against the holdout), then an optional split-conformal offset from the last
              `calib_days` of that window, so P10-P90 covers about 80%.
  5. holdout  2025 .. latest: pooled and by horizon / month / stress window, against four baselines, with the capture
              rate of the dispatch plan each forecast produces (`cv.decision_value`).
  6. MLflow   every step above is a tracked run: params, metrics, data snapshot, code version, feature set; Optuna trials
              are child runs; the holdout forecasts are attached as a table for D1b's shadow run.

Hardware note: the machine is shared and CPU-bound. Fits use a thinned frame (decisions every 8-12 h, every 3rd-4th horizon):
neighbouring horizons of one decision are almost the same row, so the information lost is small. `num_threads` defaults to 1,
and parallelism comes from running one process per settlement point: on a saturated machine a 2-thread LightGBM fit of 40k rows
took 235 s against 9 s for the same fit on 1 thread (OpenMP threads wait on each other when cores are taken), and a first
attempt with 4 threads per process managed 7 Optuna trials in 22 minutes.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import time
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from typing import Callable, Optional, Sequence

import numpy as np
import pandas as pd

from .. import config, paths
from ..contracts import QUANTILES, RT
from ..features import sets as D0
from . import baselines as B
from . import cv as CV
from .quantile import ASPriceModel, LeakageError, ModelSpec, QuantileForecaster, SPModel

log = logging.getLogger("dispatch.models.train")

SETTLEMENT_POINTS = ("HB_NORTH", "HB_WEST", "HB_HOUSTON", "HB_SOUTH")
ANCHOR_COL = "rt_prof7_med"                       # price the booster predicts deviations from: a robust 7-day profile
SCALES = {                                        # unit of the deviation; Optuna picks between them
    "price": ("rt_q90_672", "rt_q10_672"),        # 7-day P90 - P10 of the price itself (tracks the price regime)
    "resid": ("res_q90_672", "res_q10_672"),      # 7-day P90 - P10 of price minus its profile (tracks surprise only)
}
SCALE_COLS = SCALES["price"]
CORE_NONNULL = ("rt_last", "rt_mean_24h", "rt_prof7_med", "res_q90_672", "res_q10_672")
STRESS_WINDOWS = {                                # read off the data: HB_NORTH day-mean price $318 on 25 Jan, max $1,597 on 28 Jan
    "winter_storm_fern": ("2026-01-23", "2026-02-03"),
}
BASELINE_NAMES = tuple(B.BASELINES)               # seasonal_naive_1d, seasonal_naive_7d, arena_profile, profile_median_7d


# ================================================================================================ configuration
@dataclass
class TrainConfig:
    sps: tuple = SETTLEMENT_POINTS
    train_start: str = "2019-01-01"
    holdout_start: str = "2025-01-01"
    holdout_end: Optional[str] = None             # None: the day before the history ends
    purge: str = "24h"                            # >= the 96-interval horizon
    embargo: str = "24h"
    # frames. Fit, CV and tuning frames are thinned for compute (a 3x bigger fit frame was not better: notes/d1a.md);
    # the holdout is evaluated on every horizon.
    fit_every: str = "8h"
    fit_horizon_stride: int = 3
    cv_every: str = "12h"
    cv_horizon_stride: int = 4
    tune_horizon_stride: int = 8                  # Optuna sees every 8th horizon of the CV frame (a multiple of cv_horizon_stride)
    eval_every: str = "4h"
    eval_offset: str = "2h"                       # holdout decisions fall between the training decision grid
    # validation
    cv_start: str = "2022-01-01"                  # first validation window of the walk-forward report
    cv_span: str = "274D"                         # 2022-01-01 .. 2025-01-01 is 1096 days: four folds exactly
    cv_min_train: str = "200D"
    tune_folds: int = 1                           # the latest N folds drive the Optuna objective
    n_trials: int = 20
    tune_timeout_s: Optional[float] = 1500.0
    seed: int = 7
    num_threads: int = 1
    # model
    feature_names: Optional[tuple] = None         # None: price/calendar/residual features (+ AS if include_as)
    include_as: bool = True                       # day-ahead AS price features
    anchor_col: Optional[str] = ANCHOR_COL
    scale_cols: tuple = SCALE_COLS
    calib_days: int = 120                         # split-conformal slice at the end of training (0: none)
    refit_after_calibration: bool = False
    params: Optional[dict] = None                 # skip tuning and use these (LightGBM params + n_estimators + weight_pow)
    source: str = "d0"                            # the only source: D0's point-in-time builder (the interim builder was retired)
    # test hooks (a synthetic lake stands in for D0's real one; see models/synthetic.py). None: read D0's lake.
    frames_provider: Optional[Callable] = None    # (sp, start, end, every, offset) -> (X, y, decision_utc), as features.build.training_frame
    prices_provider: Optional[Callable] = None    # sp -> pd.Series of realised RT prices on the 15-minute UTC grid
    as_provider: Optional[Callable] = None        # () -> wide DataFrame of day-ahead AS prices (hourly UTC index, product columns)
    work_dir: Optional[str] = None
    registry_uri: Optional[str] = None
    eval_decisions_for_value: str = "8h"          # decision-value evaluation cadence (a multiple of eval_every)
    value_planner: str = "auto"                   # auto: D1b's lp.solve when it exists, else the arena DP | arena | lp

    def __post_init__(self):
        if self.source != "d0":
            raise ValueError(f"source={self.source!r}: D1a's interim feature builder was retired (11 Oct 2026); the only source is 'd0'")

    def to_params(self) -> dict:
        d = asdict(self)
        d.pop("frames_provider"), d.pop("prices_provider"), d.pop("as_provider")
        return d


# ================================================================================================ frames
@dataclass
class Frame:
    X: pd.DataFrame
    y: np.ndarray
    decision: pd.DatetimeIndex
    source: str

    @property
    def target(self) -> pd.DatetimeIndex:
        return pd.DatetimeIndex(self.X.index)

    @property
    def horizon(self) -> np.ndarray:
        return self.X["horizon"].to_numpy()

    def __len__(self) -> int:
        return len(self.y)

    def take(self, mask) -> "Frame":
        m = np.asarray(mask)
        X = self.X[m]
        X.attrs.update(self.X.attrs)
        return Frame(X, self.y[m], self.decision[m], self.source)


FEATURE_SET_LABEL = f"{D0.FEATURE_SET}/{D0.VERSION}"        # what a bundle records as `feature_set` (rt_v1/1.1.0)

# The model's inputs, under D0's own column names (`features/sets.py`, rt_v1 1.1.0). It is the list version 1 was trained on
# (D1a's interim builder), rename for rename, with one change: the interim `tgt_hod` (the hour plus the quarter hour / 4, one number)
# is `hour_local` and `qtr_hour` here, two integers, because that is how rt_v1 carries it. `compat.INTERIM_TO_D0` is the map between
# the two spellings and `tests/test_rt_v1_1_equivalence.py` holds both lists to the frozen interim outputs.
RT_FEATURES = (
    "rt_last", "rt_mean_1h", "rt_mean_16", "rt_mean_24h", "rt_mean_7d", "rt_std_24h", "rt_std_7d", "rt_min_96", "rt_max_96",
    "rt_q10_672", "rt_q50_672", "rt_q90_672", "rt_spread_96", "rt_frac_hi_96", "rt_frac_hi_672", "rt_frac_neg_96", "rt_frac_neg_672",
    "res_last", "res_mean_4", "res_mean_16", "res_mean_96", "res_q10_96", "res_q50_96", "res_q90_96", "res_q10_672", "res_q50_672",
    "res_q90_672", "res_mad_672",
    "horizon", "hour_local", "qtr_hour", "dow", "is_weekend", "is_holiday", "doy_sin", "doy_cos", "month",
    "rt_lag_1d", "rt_lag_2d", "rt_lag_3d", "rt_lag_7d", "rt_lag_14d",
    "rt_prof7_mean", "rt_prof7_med", "rt_prof7_min", "rt_prof7_max", "rt_prof7_std", "rt_prof14_dt",
)
AS_MODEL_PRODUCTS = ("regup", "regdn", "rrs", "nspin")           # ECRS only exists since 2023-06: not an RT-model input (the AS view uses it)
AS_FEATURES = tuple(f"dam_as_{p}{sfx}" for p in AS_MODEL_PRODUCTS for sfx in ("", "_med14", "_rel"))
MODEL_FEATURES = RT_FEATURES + AS_FEATURES                       # 60 columns


def default_feature_names(cfg: TrainConfig) -> tuple:
    if cfg.feature_names is not None:
        return tuple(cfg.feature_names)
    return RT_FEATURES + (AS_FEATURES if cfg.include_as else ())


def _utc(ts) -> pd.Timestamp:
    """A pandas UTC Timestamp. A naive input (e.g. the string '2024-01-01') is read as UTC, the platform's one clock."""
    t = pd.Timestamp(ts)
    return t.tz_localize("UTC") if t.tzinfo is None else t.tz_convert("UTC")


def _d0_training_frame(cfg: TrainConfig, sp, start, end, every, offset="0h"):
    """D0's `features.build.training_frame` -> (X, y, decision) with X indexed by the target interval, or raises."""
    if cfg.frames_provider is not None:
        X, y, d = cfg.frames_provider(sp, start, end, every, offset)
    else:
        from ..features import build as d0
        X, y, d = d0.training_frame(sp, start, end, every, offset=offset)
    if "interval_start_utc" in X.columns and not isinstance(X.index, pd.DatetimeIndex):
        X = X.set_index(pd.DatetimeIndex(X["interval_start_utc"]).rename("interval_start_utc"), drop=False)   # D0's layout
    return X, np.asarray(y, dtype=float), pd.DatetimeIndex(d)


def check_columns(cfg: TrainConfig, sp: str, needed: Sequence[str]) -> None:
    """Fail before an hour of tuning if D0's frame cannot supply a column the model needs (a feature-set change, a lake that cannot build
    the window). One small frame per settlement point; nothing is silently replaced."""
    X, _, _ = _d0_training_frame(cfg, sp, _utc("2024-06-03"), _utc("2024-06-04"), "4h")
    missing = [c for c in needed if c not in X.columns]
    if missing:
        raise KeyError(f"{sp}: D0's frame lacks {len(missing)} columns the model needs (e.g. {missing[:4]}); "
                       f"features/sets.py is at {FEATURE_SET_LABEL}")


def build_frame(cfg: TrainConfig, sp: str, start, end, every: str, *, offset: str = "0h", stride: int = 1) -> Frame:
    """Training / evaluation rows for decisions in [start, end) from D0's builder; leakage-checked row by row.
    `offset` shifts the decision grid (D0's grid is epoch-aligned: multiples of `every` from 1970-01-01 plus the offset)."""
    start, end = _utc(start), _utc(end)
    X, y, dec = _d0_training_frame(cfg, sp, start, end, every, offset)
    if stride > 1:
        keep = ((X["horizon"].to_numpy() - 1) % stride) == 0
        X, y, dec = X[keep], y[keep], dec[keep]
    if "max_published_utc" in X.columns:
        late = X["max_published_utc"].to_numpy() > dec.to_numpy()
        if late.any():
            raise LeakageError(f"{int(late.sum())} rows use data published after their decision time")
    X.attrs["settlement_point"] = sp
    return Frame(X, y, dec, FEATURE_SET_LABEL)


# ------------------------------------------------------------------------------------------------ realised prices and AS prices
def realised_prices(cfg: TrainConfig, sp: str, lo, hi) -> pd.Series:
    """Realised RT SPP on the 15-minute UTC grid [lo, hi) from D0's lake (NaN where there is none yet): the same prices `training_frame`
    uses as labels (the REAL source before the archive, then the latest published, so ERCOT's corrections count). It feeds the capture
    rate of the holdout plan and tells where the history ends."""
    lo, hi = _utc(lo).floor("15min"), _utc(hi).ceil("15min")
    if cfg.prices_provider is not None:
        s = cfg.prices_provider(sp)
        return s[(s.index >= lo) & (s.index < hi)]
    from ..data.reader import read_prices
    df = read_prices("rt_spp", "rt_spp_hist", sp, lo, hi - pd.Timedelta(minutes=15), has_type=True)
    idx = pd.date_range(lo, hi, freq="15min", inclusive="left", name="interval_start_utc")
    if df.empty:
        return pd.Series(np.nan, index=idx, name=RT)
    df = df.sort_values(["interval_start_utc", "prio", "published_utc"], ascending=[True, True, False], kind="stable")
    df = df.drop_duplicates("interval_start_utc", keep="first")
    got = pd.Series(df["price_usd_per_mwh"].to_numpy(dtype="float64"), index=pd.DatetimeIndex(df["interval_start_utc"]).tz_convert("UTC").as_unit("ns"))
    out = got.reindex(idx.as_unit("ns"))
    out.index = idx
    return out.rename(RT)


def history_end(prices: pd.Series) -> pd.Timestamp:
    """End of the newest interval that has a price."""
    ok = prices.dropna()
    if ok.empty:
        raise ValueError("no realised prices")
    return ok.index[-1] + pd.Timedelta(minutes=15)


def day_ahead_as_frame(cfg: TrainConfig, asof, days: int = 30) -> Optional[pd.DataFrame]:
    """Day-ahead AS clearing prices of the last `days` before `asof`, wide (UTC hour start x product), from D0's lake. The AS view
    (`ASPriceModel`) is a median of the same local hour over 14 days; this is what it is fitted to. None if the lake has none."""
    if cfg.as_provider is not None:
        return cfg.as_provider()
    from ..data.reader import read_as
    asof = _utc(asof)
    df = read_as("dam_as", "dam_as_hist", asof - pd.Timedelta(days=days), asof)
    if df.empty:
        return None
    df = df[df["published_utc"] <= asof].sort_values(["interval_start_utc", "product", "prio", "published_utc"],
                                                    ascending=[True, True, True, False], kind="stable")
    df = df.drop_duplicates(["interval_start_utc", "product"], keep="first")
    wide = df.pivot(index="interval_start_utc", columns="product", values="mcpc_usd_per_mw_h").sort_index()
    wide.columns = [str(c).lower() for c in wide.columns]
    wide.index = pd.DatetimeIndex(wide.index).tz_convert("UTC")
    return wide


# ================================================================================================ specs and search
def make_spec(cfg: TrainConfig, feature_names: Sequence[str], chosen: dict) -> ModelSpec:
    """ModelSpec from the tuned choices: LightGBM params, n_estimators, weight_pow and which scale to normalise by."""
    scale = SCALES[chosen["scale"]] if chosen.get("scale") else tuple(cfg.scale_cols)
    spec = ModelSpec(feature_names=tuple(feature_names), anchor_col=cfg.anchor_col, scale_cols=tuple(scale),
                     weight_pow=float(chosen.get("weight_pow", 0.0)), n_estimators=int(chosen["n_estimators"]),
                     params=dict(chosen["params"]))
    spec.required_nonnull = tuple(c for c in CORE_NONNULL if c in spec.needed_columns())
    return spec


DEFAULT_PARAMS = dict(learning_rate=0.08, num_leaves=31, min_data_in_leaf=300, feature_fraction=0.8, bagging_fraction=0.7,
                      bagging_freq=1, lambda_l2=5.0, max_bin=63)
DEFAULT_TRIAL = dict(DEFAULT_PARAMS, n_estimators=260, weight_pow=0.0, scale="price")


def rescale_leaf(chosen: dict, ratio: float, cap: int = 4000) -> dict:
    """Parameters tuned on a thin frame, moved to a bigger one: min_data_in_leaf grows with the number of rows (a leaf keeps
    the same share of the data), everything else is kept."""
    out = dict(chosen, params=dict(chosen["params"]))
    if "min_data_in_leaf" in out["params"]:
        out["params"]["min_data_in_leaf"] = int(min(cap, max(20, round(out["params"]["min_data_in_leaf"] * ratio))))
    return out


def suggest(trial, scales: Sequence[str] = tuple(SCALES)) -> dict:
    """Optuna search space -> {params, n_estimators, weight_pow, scale}."""
    params = dict(
        learning_rate=trial.suggest_float("learning_rate", 0.04, 0.2, log=True),
        num_leaves=trial.suggest_int("num_leaves", 15, 95, log=True),
        min_data_in_leaf=trial.suggest_int("min_data_in_leaf", 80, 1200, log=True),
        feature_fraction=trial.suggest_float("feature_fraction", 0.5, 1.0),
        bagging_fraction=trial.suggest_float("bagging_fraction", 0.5, 1.0),
        lambda_l2=trial.suggest_float("lambda_l2", 0.5, 50.0, log=True),
        bagging_freq=1, max_bin=63)
    n_est = trial.suggest_int("n_estimators", 100, 320, step=20)
    wp = trial.suggest_categorical("weight_pow", [0.0, 0.5])
    scale = trial.suggest_categorical("scale", list(scales))
    return dict(params=params, n_estimators=n_est, weight_pow=wp, scale=scale)


def _fold_arrays(frame: Frame, folds: Sequence[CV.Fold]):
    return [CV.fold_masks(f, frame.decision, frame.target) for f in folds]


def tune(cfg: TrainConfig, frame: Frame, folds: Sequence[CV.Fold], feature_names: Sequence[str]):
    """Optuna study on the latest `cfg.tune_folds` folds of `frame`. Returns (best params dict, trials DataFrame)."""
    import optuna
    optuna.logging.set_verbosity(optuna.logging.WARNING)
    use = list(folds)[-cfg.tune_folds:]
    masks = _fold_arrays(frame, use)

    scales = [k for k, cols in SCALES.items() if all(c in frame.X.columns for c in cols)]

    def objective(trial) -> float:
        spec = make_spec(cfg, feature_names, suggest(trial, scales))
        losses = []
        for k, (tr, va) in enumerate(masks):
            m = SPModel.fit(frame.X[tr], frame.y[tr], spec, num_threads=cfg.num_threads)
            q = m.quantiles(frame.X[va])
            met = CV.quantile_metrics(frame.y[va], q)
            losses.append(met["pinball_mean"])
            trial.report(float(np.mean(losses)), k)
            if trial.should_prune():
                raise optuna.TrialPruned()
        return float(np.mean(losses))

    study = optuna.create_study(direction="minimize", sampler=optuna.samplers.TPESampler(seed=cfg.seed, n_startup_trials=6),
                                pruner=optuna.pruners.MedianPruner(n_startup_trials=4, n_warmup_steps=0))
    first = {k: v for k, v in DEFAULT_TRIAL.items() if k not in ("max_bin", "bagging_freq")}
    first["scale"] = first["scale"] if first["scale"] in scales else scales[0]
    study.enqueue_trial(first)
    t0 = time.time()
    def progress(study_, trial_):
        v = trial_.value if trial_.value is not None else float("nan")
        log.info("optuna trial %d %s value=%.4f best=%.4f (%.0fs)", trial_.number, trial_.state.name, v,
                 study_.best_value if any(t.state.name == "COMPLETE" for t in study_.trials) else float("nan"), time.time() - t0)

    study.optimize(objective, n_trials=cfg.n_trials, timeout=cfg.tune_timeout_s, gc_after_trial=True, callbacks=[progress])
    trials = study.trials_dataframe(attrs=("number", "value", "state", "params", "duration"))
    complete = [t for t in study.trials if t.state.name == "COMPLETE"]
    if not complete:
        raise RuntimeError("Optuna finished no trial; raise tune_timeout_s or lower the data size")
    best = study.best_trial
    p = dict(best.params)
    n_est, wp, scale = int(p.pop("n_estimators")), float(p.pop("weight_pow")), p.pop("scale")
    p.update(bagging_freq=1, max_bin=63)
    info = {"best_value": float(best.value), "best_trial": best.number, "n_trials": len(study.trials),
            "n_complete": len(complete), "n_pruned": sum(t.state.name == "PRUNED" for t in study.trials),
            "seconds": round(time.time() - t0, 1)}
    return dict(params=p, n_estimators=n_est, weight_pow=wp, scale=scale), trials, info


# ================================================================================================ evaluation
def baseline_quantiles(baselines: dict, X: pd.DataFrame, sp: str) -> dict:
    return {n: b.quantiles(X, sp) for n, b in baselines.items()}


def walk_forward_report(cfg: TrainConfig, frame: Frame, folds: Sequence[CV.Fold], spec: ModelSpec, sp: str) -> dict:
    """Purged walk-forward: the model and every baseline are refit on each fold's training rows and scored on its
    validation rows. Returns per-fold tables and pooled metrics for the model and for each baseline."""
    sets = {"model": []}
    sets.update({n: [] for n in BASELINE_NAMES})
    oof = {n: [] for n in sets}
    for f in folds:
        CV.assert_no_overlap(f, frame.decision, frame.target)
        tr, va = CV.fold_masks(f, frame.decision, frame.target)
        Xtr, ytr, Xva, yva = frame.X[tr], frame.y[tr], frame.X[va], frame.y[va]
        m = SPModel.fit(Xtr, ytr, spec, num_threads=cfg.num_threads)
        qs = {"model": m.quantiles(Xva)}
        for n in BASELINE_NAMES:
            qs[n] = B.make_baseline(n).fit(sp, Xtr, ytr).quantiles(Xva, sp)
        log.info("%s: CV %s | model pinball %.3f vs profile_median %.3f", sp, f.describe(),
                 CV.quantile_metrics(yva, qs["model"])["pinball_mean"], CV.quantile_metrics(yva, qs["profile_median_7d"])["pinball_mean"])
        for n, q in qs.items():
            met = CV.quantile_metrics(yva, q)
            met.update(fold=f.index, val_start=f.val_start, n_train=int(tr.sum()))
            sets[n].append(met)
            oof[n].append(pd.DataFrame(q, columns=[f"rt_{k}" for k in QUANTILES]).assign(y=yva))
    out = {}
    for n in sets:
        per = pd.DataFrame(sets[n]).set_index("fold")
        pooled_df = pd.concat(oof[n])
        out[n] = {"per_fold": per, "pooled": CV.evaluate_frame(pooled_df)}
    return out


def summarize_gain(table: dict, key: str = "pinball_mean") -> dict:
    """Relative gain of the model over each baseline on a metric (positive: model better)."""
    m = table["model"][key]
    return {n: CV.relative_gain(m, table[n][key]) for n in table if n != "model"}


def predict_all(model: SPModel, baselines: dict, frame: Frame, sp: str) -> dict:
    out = {"model": model.quantiles(frame.X)}
    out.update(baseline_quantiles(baselines, frame.X, sp))
    return out


def pick_planner(kind: str = "auto"):
    """(name, callable) for decision_value: 'arena' forces the arena DP, 'lp' requires D1b's lp.solve, 'auto' takes the LP if it
    exists."""
    if kind == "arena":
        return "arena-dp", CV.arena_dp_plan
    name, fn = CV.default_planner()
    if kind == "lp" and name != "lp.solve":
        raise RuntimeError("value_planner='lp' but dispatch.optimizer.lp.solve is not available")
    return name, fn


def holdout_report(model: SPModel, baselines: dict, frame: Frame, sp: str, *, price_series: Optional[pd.Series],
                   value_every: str = "8h", with_value: bool = True, planner: str = "auto") -> dict:
    """Everything the notes and the registry gates need from the holdout frame."""
    qs = predict_all(model, baselines, frame, sp)
    y = frame.y
    pooled = {n: CV.quantile_metrics(y, q) for n, q in qs.items()}
    by_h = {n: CV.metrics_by_horizon(y, q, frame.horizon) for n, q in qs.items()}
    month = pd.PeriodIndex(frame.target.tz_convert(config.TZ_MARKET).tz_localize(None), freq="M")
    monthly = {}
    for n, q in qs.items():
        rows = {}
        for per in month.unique():
            mk = np.asarray(month == per)
            rows[str(per)] = CV.quantile_metrics(y[mk], q[mk])
        monthly[n] = pd.DataFrame(rows).T
    stress = {}
    for name, (a, b) in STRESS_WINDOWS.items():
        t = frame.target.tz_convert(config.TZ_MARKET)
        mk = np.asarray((t >= pd.Timestamp(a, tz=config.TZ_MARKET)) & (t < pd.Timestamp(b, tz=config.TZ_MARKET)))
        if mk.sum() > 100:
            stress[name] = {n: CV.quantile_metrics(y[mk], q[mk]) for n, q in qs.items()}
    value = {}
    if with_value and price_series is not None:
        every = pd.Timedelta(value_every)
        base = pd.DatetimeIndex(frame.decision)
        keep = np.asarray(((base - base.min()) % every) == pd.Timedelta(0))
        long = pd.DataFrame({"decision_utc": frame.decision[keep], "horizon": frame.horizon[keep]}, index=frame.target[keep])
        _, fn = pick_planner(planner)
        pf_cache: dict = {}
        for n, q in qs.items():
            f = long.assign(rt_p50=q[keep, 1])
            try:
                dv = CV.decision_value(fn, forecasts=f, prices=price_series, pf_cache=pf_cache)
                value[n] = dv.as_dict() | {"notes": list(dv.notes)}
            except Exception as e:                       # decision value must not kill a training run
                value[n] = {"error": f"{type(e).__name__}: {e}"}
    return {"pooled": pooled, "by_horizon": by_h, "monthly": monthly, "stress": stress, "value": value, "q": qs}


def holdout_table(frame: Frame, qs: dict, baselines: Sequence[str]) -> pd.DataFrame:
    """The long forecast table attached to the run: model quantiles plus each baseline's P50 (for D1b's shadow run)."""
    df = pd.DataFrame({"decision_utc": frame.decision, "horizon": frame.horizon.astype("int16"), "y": frame.y,
                       **{f"rt_{k}": qs["model"][:, i].astype("float32") for i, k in enumerate(QUANTILES)}},
                      index=frame.target)
    if "max_published_utc" in frame.X.columns:
        df["max_published_utc"] = frame.X["max_published_utc"].to_numpy()
    for n in baselines:
        df[f"{n}_p50"] = qs[n][:, 1].astype("float32")
    return df


# ================================================================================================ one settlement point
@dataclass
class SPResult:
    sp: str
    model: SPModel
    baselines: dict
    chosen: dict
    holdout: dict
    cv: dict
    optuna_info: dict
    trials: Optional[pd.DataFrame]
    source: str
    data_snapshot: dict
    run_id: str = ""
    timings: dict = field(default_factory=dict)
    forecasts: Optional[pd.DataFrame] = None
    passes: bool = False
    gains: dict = field(default_factory=dict)


def run_sp(cfg: TrainConfig, sp: str) -> SPResult:
    """Tune, validate, fit and evaluate one settlement point on the holdout. Pure computation: nothing here needs MLflow, so a
    tracking failure can never lose a result (`log_result` records it afterwards)."""
    t_all = time.time()
    timings: dict = {}
    names = default_feature_names(cfg)                  # D0's own column names: the model is trained on, and served from, rt_v1
    extra = tuple(c for cols in SCALES.values() for c in cols) + ((cfg.anchor_col,) if cfg.anchor_col else ())
    hold0 = _utc(cfg.holdout_start)
    train0 = _utc(cfg.train_start)
    prices = realised_prices(cfg, sp, hold0 - pd.Timedelta(days=2), pd.Timestamp.now(tz="UTC") + pd.Timedelta(days=1))
    hold1 = _utc(cfg.holdout_end) if cfg.holdout_end else (history_end(prices) - pd.Timedelta(days=1))
    purge, embargo = pd.Timedelta(cfg.purge), pd.Timedelta(cfg.embargo)

    # ---- frames
    t0 = time.time()
    check_columns(cfg, sp, tuple(names) + extra)
    cv_frame = build_frame(cfg, sp, train0, hold0, cfg.cv_every, stride=cfg.cv_horizon_stride)
    fit_frame = build_frame(cfg, sp, train0, hold0, cfg.fit_every, stride=cfg.fit_horizon_stride)
    eval_frame = build_frame(cfg, sp, hold0, hold1, cfg.eval_every, offset=cfg.eval_offset)
    timings["frames_s"] = round(time.time() - t0, 1)
    log.info("%s: cv %d rows, fit %d rows, holdout %d rows (%s)", sp, len(cv_frame), len(fit_frame), len(eval_frame), fit_frame.source)
    # the price series the holdout plan is settled at must be the labels the frames carry
    seen = prices.reindex(eval_frame.target).to_numpy()
    if not np.allclose(seen, eval_frame.y, rtol=0, atol=1e-9, equal_nan=False):
        bad = int((~np.isclose(seen, eval_frame.y, rtol=0, atol=1e-9)).sum())
        raise ValueError(f"{sp}: {bad} holdout labels differ from the realised price series used for the dispatch value")

    from ..registry import mlflow_registry as R
    snapshot = R.catalog_snapshot()                             # D0's lake: a hash of the catalog lines the run could read

    # ---- folds inside the training window (the holdout is never used for tuning)
    folds = CV.walk_forward_folds(cfg.cv_start, hold0, span=cfg.cv_span, train_start=cfg.train_start, purge=cfg.purge,
                                  embargo=cfg.embargo, min_train=cfg.cv_min_train)

    # ---- tune
    t0 = time.time()
    if cfg.params is not None:
        chosen = dict(params=dict(cfg.params.get("params", {})), n_estimators=int(cfg.params.get("n_estimators", 250)),
                      weight_pow=float(cfg.params.get("weight_pow", 0.0)), scale=cfg.params.get("scale"))
        trials, opt_info = None, {"skipped": True}
    else:
        assert cfg.tune_horizon_stride % cfg.cv_horizon_stride == 0, "tune stride must be a multiple of the CV stride"
        tune_frame = cv_frame.take(((cv_frame.horizon - 1) % cfg.tune_horizon_stride) == 0)
        tuned, trials, opt_info = tune(cfg, tune_frame, folds, names)
        opt_info["tune_rows"] = len(tune_frame)
        chosen = dict(tuned, tuned_on_rows=len(tune_frame))
    timings["tune_s"] = round(time.time() - t0, 1)
    log.info("%s: tuned in %.0fs -> %s", sp, timings["tune_s"], {k: v for k, v in chosen.items() if k != "params"})
    base_rows = chosen.get("tuned_on_rows")                       # None: parameters were given, not tuned: used as they are
    spec_cv = make_spec(cfg, names, rescale_leaf(chosen, len(cv_frame) / base_rows) if base_rows else chosen)
    spec = make_spec(cfg, names, rescale_leaf(chosen, len(fit_frame) / base_rows) if base_rows else chosen)

    # ---- purged walk-forward report with the chosen parameters
    t0 = time.time()
    cvrep = walk_forward_report(cfg, cv_frame, folds, spec_cv, sp)
    timings["cv_s"] = round(time.time() - t0, 1)

    # ---- final fit on the whole training window (purged against the holdout), optional conformal offset
    t0 = time.time()
    model, calib = final_fit(cfg, fit_frame, spec, hold0, sp)
    timings["fit_s"] = round(time.time() - t0, 1)
    log.info("%s: final fit in %.0fs, calibration %s", sp, timings["fit_s"], {k: round(v, 3) for k, v in calib.items() if k != "calibration_rows"})
    ref_rows = np.asarray((fit_frame.decision >= hold0 - pd.Timedelta(days=365) - purge - embargo)
                          & (fit_frame.decision < hold0 - purge - embargo))
    model.set_reference(fit_frame.X[ref_rows] if ref_rows.sum() > 500 else fit_frame.X)   # last training year: drift yardstick
    baselines = {n: B.make_baseline(n, feature_set=fit_frame.source).fit(sp, *_train_rows(fit_frame, hold0, purge, embargo))
                 for n in BASELINE_NAMES}

    # ---- holdout
    t0 = time.time()
    rep = holdout_report(model, baselines, eval_frame, sp, price_series=prices, value_every=cfg.eval_decisions_for_value,
                         planner=cfg.value_planner)
    timings["holdout_s"] = round(time.time() - t0, 1)
    log.info("%s: holdout pinball %s", sp, {n: round(m["pinball_mean"], 3) for n, m in rep["pooled"].items()})
    gains_p = summarize_gain(rep["pooled"], "pinball_mean")
    gains_m = summarize_gain(rep["pooled"], "mae_p50")
    passes = all(g > 0 for g in gains_p.values()) and all(g > 0 for g in gains_m.values())
    consistency = serving_consistency(model, eval_frame, sp, rep["q"]["model"])
    timings["total_s"] = round(time.time() - t_all, 1)
    return SPResult(sp, model, baselines, chosen, rep, cvrep, opt_info, trials, fit_frame.source, snapshot, timings=timings,
                    forecasts=holdout_table(eval_frame, rep["q"], BASELINE_NAMES), passes=passes,
                    gains={"pinball": gains_p, "mae": gains_m, "calibration": calib, "serving_consistency": consistency})


def log_result(registry, cfg: TrainConfig, res: SPResult) -> str:
    """Record one settlement point's run in MLflow (params, metrics, tables, Optuna trials as child runs); sets res.run_id."""
    with registry.start_run(f"train_{res.sp}", data_snapshot=res.data_snapshot, feature_set=res.source,
                            settlement_points=[res.sp], tags={"stage": "train_sp", "sp": res.sp}) as run:
        res.run_id = run.run_id
        log_sp(run, cfg, res)
        if res.trials is not None:
            log_trials(registry, run.run_id, res.sp, res.trials, res.data_snapshot, res.source)
    return res.run_id


def _train_rows(frame: Frame, hold0: pd.Timestamp, purge: pd.Timedelta, embargo: pd.Timedelta):
    """(X, y) of the training rows allowed before the holdout: decisions before hold0 - purge - embargo."""
    fold = CV.Fold(0, frame.decision.min(), hold0, hold0 + pd.Timedelta(days=1), purge, embargo)
    tr, _ = CV.fold_masks(fold, frame.decision, frame.target)
    return frame.X[tr], frame.y[tr]


def final_fit(cfg: TrainConfig, frame: Frame, spec: ModelSpec, hold0: pd.Timestamp, sp: str):
    """Fit on everything before the holdout (purged). With calib_days > 0 the last slice is held out from the boosters and
    used for split-conformal offsets (a fixed shift per quantile in normalised units)."""
    purge, embargo = pd.Timedelta(cfg.purge), pd.Timedelta(cfg.embargo)
    calib = {}
    if cfg.calib_days > 0:
        c0 = hold0 - pd.Timedelta(days=cfg.calib_days)
        fit_fold = CV.Fold(0, frame.decision.min(), c0, hold0, purge, embargo)
        tr, _ = CV.fold_masks(fit_fold, frame.decision, frame.target)
        cal_rows = ((frame.decision >= c0) & (frame.decision < hold0 - purge - embargo)
                    & (frame.target + CV.STEP <= hold0 - embargo))
        model = SPModel.fit(frame.X[tr], frame.y[tr], spec, num_threads=cfg.num_threads,
                            info={"fit_rows": int(tr.sum()), "calibration_days": cfg.calib_days})
        cx, cy = frame.X[np.asarray(cal_rows)], frame.y[np.asarray(cal_rows)]
        calib = model.calibrate(cx, cy, num_threads=cfg.num_threads, min_rows=500)
        calib["calibration_rows"] = int(len(cy))
        if cfg.refit_after_calibration:
            full = CV.Fold(0, frame.decision.min(), hold0, hold0 + pd.Timedelta(days=1), purge, embargo)
            tr2, _ = CV.fold_masks(full, frame.decision, frame.target)
            m2 = SPModel.fit(frame.X[tr2], frame.y[tr2], replace(spec, calibration=dict(model.spec.calibration)),
                             num_threads=cfg.num_threads, info={"fit_rows": int(tr2.sum()), "calibration_days": cfg.calib_days})
            model = m2
        return model, calib
    full = CV.Fold(0, frame.decision.min(), hold0, hold0 + pd.Timedelta(days=1), purge, embargo)
    tr, _ = CV.fold_masks(full, frame.decision, frame.target)
    model = SPModel.fit(frame.X[tr], frame.y[tr], spec, num_threads=cfg.num_threads, info={"fit_rows": int(tr.sum())})
    return model, calib


def serving_consistency(model: SPModel, frame: Frame, sp: str, fast_q: np.ndarray, n_decisions: int = 12) -> float:
    """Max |difference| between the vectorised path used for evaluation and the real `Forecaster.predict` path
    (leakage checks, derived horizon, DataFrame plumbing) on a sample of decisions."""
    fc = QuantileForecaster({sp: model}, feature_set=frame.source)
    uniq = pd.DatetimeIndex(frame.decision).unique()
    pick = uniq[np.linspace(0, len(uniq) - 1, min(n_decisions, len(uniq))).astype(int)]
    worst = 0.0
    for d in pick:
        m = np.asarray(frame.decision == d)
        sub = frame.X[m]
        sub.attrs["settlement_point"] = sp
        out = fc.predict(sub.drop(columns=["horizon"]), d).frame[[f"rt_{k}" for k in QUANTILES]].to_numpy()
        worst = max(worst, float(np.abs(out - fast_q[m]).max()))
    return worst


# ================================================================================================ tracking
def log_trials(registry, parent_run_id: str, sp: str, trials: pd.DataFrame, snapshot: dict, source: str) -> None:
    for _, t in trials.iterrows():
        with registry.start_run(f"trial_{sp}_{int(t['number']):03d}", parent=parent_run_id, tags={"stage": "optuna_trial", "sp": sp},
                                feature_set=source, settlement_points=[sp]) as r:
            r.log_params({k.replace("params_", ""): v for k, v in t.items() if str(k).startswith("params_") and pd.notna(v)})
            if pd.notna(t.get("value")):
                r.log_metrics({"cv_pinball_mean": float(t["value"])})
            r.set_tags({"optuna.state": str(t["state"])})


def log_sp(run, cfg: TrainConfig, res: SPResult) -> None:
    from ..registry import mlflow_registry as R
    sp = res.sp
    run.log_params({"config": {k: v for k, v in cfg.to_params().items() if k not in ("params",)}, "chosen": res.chosen,
                    "frame_source": res.source, "optuna": res.optuna_info})
    base = {n: res.holdout["pooled"][n] for n in BASELINE_NAMES}
    metrics = R.holdout_metrics_payload(sp, res.holdout["pooled"]["model"], base)
    metrics["cv"] = {"model": res.cv["model"]["pooled"], **{n: res.cv[n]["pooled"] for n in BASELINE_NAMES}}
    metrics["gain"] = {"pinball": res.gains["pinball"], "mae": res.gains["mae"]}
    metrics["timing"] = res.timings
    metrics["serving_consistency_max_abs_diff"] = res.gains["serving_consistency"]
    if res.gains["calibration"]:
        metrics["calibration"] = res.gains["calibration"]
    for n, v in res.holdout["value"].items():
        if "capture" in v:
            metrics.setdefault("capture", {})[n] = v["capture"]
            metrics.setdefault("usd_per_kw_yr", {})[n] = v["usd_per_kw_yr"]
    run.log_metrics(metrics)
    for name in ("model", *BASELINE_NAMES):
        run.log_table(res.holdout["by_horizon"][name], f"holdout_by_horizon_{name}", "tables")
        run.log_table(res.holdout["monthly"][name], f"holdout_monthly_{name}", "tables")
        run.log_table(res.cv[name]["per_fold"].drop(columns=["val_start"]), f"cv_per_fold_{name}", "tables")
    run.log_dict({k: v for k, v in res.holdout["stress"].items()}, "stress_windows.json")
    run.log_dict(res.holdout["value"], "decision_value.json")
    run.log_table(res.forecasts, "holdout_forecasts", "tables")
    imp = res.model.importance()
    run.log_table(imp.rename_axis("feature").reset_index(), "feature_importance_gain", "tables")
    if res.trials is not None:
        run.log_table(res.trials.astype({"state": str}).drop(columns=["duration"], errors="ignore"), "optuna_trials", "tables")
    run.set_tags({"frame_source": res.source, "passes_baselines": str(res.passes).lower()})


# ================================================================================================ checkpoints and release
def work_dir(cfg: TrainConfig, tag: str = "latest") -> Path:
    base = Path(cfg.work_dir) if cfg.work_dir else paths.MLFLOW / "work"
    return base / tag


def save_checkpoint(cfg: TrainConfig, res: SPResult, tag: str = "latest") -> Path:
    d = work_dir(cfg, tag) / res.sp
    d.mkdir(parents=True, exist_ok=True)
    res.model.save(d / "model")
    for n, b in res.baselines.items():
        b.save(d / "baselines" / n)
    summary = {"sp": res.sp, "run_id": res.run_id, "source": res.source, "chosen": res.chosen, "passes": res.passes,
               "gains": res.gains, "optuna": res.optuna_info, "timings": res.timings,
               "pooled": res.holdout["pooled"], "value": res.holdout["value"], "snapshot": res.data_snapshot,
               "stress": res.holdout["stress"], "cv_pooled": {n: res.cv[n]["pooled"] for n in res.cv}}
    (d / "result.json").write_text(json.dumps(summary, indent=1, default=_jd), encoding="utf-8")
    if res.forecasts is not None:
        res.forecasts.to_parquet(d / "holdout_forecasts.parquet")
    return d


def _jd(o):
    if isinstance(o, (np.floating, np.integer)):
        return o.item()
    if isinstance(o, (pd.Timestamp,)):
        return o.isoformat()
    if isinstance(o, (pd.Timedelta,)):
        return str(o)
    if isinstance(o, (np.ndarray,)):
        return o.tolist()
    return str(o)


def load_checkpoint(cfg: TrainConfig, sp: str, tag: str = "latest"):
    d = work_dir(cfg, tag) / sp
    summary = json.loads((d / "result.json").read_text(encoding="utf-8"))
    model = SPModel.load(d / "model")
    baselines = {n: B.BaselineForecaster.load(d / "baselines" / n) for n in BASELINE_NAMES}
    return summary, model, baselines


def holdout_reference(cfg: TrainConfig, tag: str, sp: str, summary: Optional[dict] = None) -> dict:
    """The monitor's reference numbers for one point (`cv.reference_metrics`) from the checkpointed holdout forecast table, which is
    the table D1b replays: pooled over every (decision, horizon) row and by horizon bucket. {} if the checkpoint has no table.
    Cross-checked against the pooled metrics in result.json (the table stores quantiles as float32, so equal to about 1e-4)."""
    f = work_dir(cfg, tag) / sp / "holdout_forecasts.parquet"
    if not f.exists():
        return {}
    tab = pd.read_parquet(f)
    ref = CV.reference_metrics(tab["y"].to_numpy(), tab[[f"rt_{k}" for k in QUANTILES]].to_numpy(), tab["horizon"].to_numpy(),
                               window={"start": tab.index.min().isoformat(), "end": tab.index.max().isoformat(),
                                       "decision_every": cfg.eval_every, "horizons": "1-96", "rows": int(len(tab))})
    if summary is not None:
        want = summary["pooled"]["model"]["pinball_mean"]
        got = ref["pooled"]["pinball_mean"]
        if abs(got - want) > 1e-3 * max(1.0, abs(want)):
            raise ValueError(f"{sp}: holdout table pinball {got:.5f} does not match result.json {want:.5f}; checkpoint is inconsistent")
    return ref


def release(cfg: TrainConfig, registry, *, tag: str = "latest", promote: Optional[str] = "research", by: str = "d1a",
            sps: Optional[Sequence[str]] = None) -> dict:
    """Bundle the checkpointed settlement points that beat every baseline into one registry version and try to promote it.

    Every point's holdout metrics go on the release run in the schema `promote` reads; baselines are registered as their own
    versions (kind=baseline) so they can be served too, but only the model competes for the aliases."""
    from ..registry import mlflow_registry as R
    sps = tuple(sps or cfg.sps)
    loaded = {sp: load_checkpoint(cfg, sp, tag) for sp in sps}
    passing = [sp for sp, (s, _, _) in loaded.items() if s["passes"]]
    failing = [sp for sp in sps if sp not in passing]
    report = {"passing": passing, "failing": failing, "version": None, "promoted": None, "refusal": None}
    if not passing:
        return report
    sources = {s["source"] for s, _, _ in loaded.values()}
    source = sources.pop() if len(sources) == 1 else "mixed"
    snap = {"kind": "lake_catalog",
            "sha256": hashlib.sha256(json.dumps({sp: loaded[sp][0]["snapshot"].get("sha256") for sp in passing},
                                                  sort_keys=True).encode()).hexdigest(),
            "n_items": len(passing), "per_sp": {sp: loaded[sp][0]["snapshot"] for sp in passing}}
    as_model = None
    if cfg.include_as:
        asof = pd.Timestamp.now(tz="UTC").floor("h")
        try:
            as_frame = day_ahead_as_frame(cfg, asof)
            as_model = ASPriceModel.fit(as_frame, asof) if as_frame is not None else None
        except ValueError:
            as_model = None
    holdout = {sp: holdout_reference(cfg, tag, sp, loaded[sp][0]) for sp in passing}
    fc = QuantileForecaster({sp: loaded[sp][1] for sp in passing}, feature_set=source, as_model=as_model,
                            meta={"trained_utc": pd.Timestamp.now(tz="UTC").isoformat(),
                                  "train_window": f"{cfg.train_start}..{cfg.holdout_start}", "holdout_start": cfg.holdout_start,
                                  "anchor": cfg.anchor_col, "scale": list(cfg.scale_cols),
                                  "feature_set": source, "publish_times": "D0 measured (features/sets.py, data/assumptions.py)",
                                  "chosen": {sp: loaded[sp][0]["chosen"] for sp in passing},
                                  "holdout": {sp: h for sp, h in holdout.items() if h}})
    payloads = [R.holdout_metrics_payload(sp, loaded[sp][0]["pooled"]["model"], {n: loaded[sp][0]["pooled"][n] for n in BASELINE_NAMES})
                for sp in passing]
    metrics = R.merge_payloads(*payloads)
    metrics["value"] = {sp: {n: v.get("capture") for n, v in loaded[sp][0]["value"].items()} for sp in passing}
    with registry.start_run(f"release_{pd.Timestamp.now(tz='UTC'):%Y%m%d_%H%M}", data_snapshot=snap, feature_set=source,
                            settlement_points=passing, tags={"stage": "release", "child_runs_by_sp": json.dumps({sp: loaded[sp][0]["run_id"] for sp in passing})}) as r:
        r.log_params({"passing": passing, "failing": failing, "chosen": {sp: loaded[sp][0]["chosen"] for sp in passing}})
        r.log_metrics(metrics)
        r.log_dict({sp: loaded[sp][0] for sp in sps}, "release_summary.json")
        run_id = r.run_id
    version = registry.register(fc, run_id, description=f"RT price quantiles for {', '.join(passing)}; holdout {cfg.holdout_start}..",
                                tags={"holdout": f"{cfg.holdout_start}.."})
    report["version"] = version
    for n in BASELINE_NAMES:
        b = B.make_baseline(n, feature_set=source)
        b.bands = {sp: loaded[sp][2][n].bands[sp] for sp in passing}
        rid = registry.log_run({"model": n}, {}, data_snapshot=snap, feature_set=source, settlement_points=passing, name=f"baseline_{n}")
        registry.register(b, rid, description=f"baseline {n}")
    if promote:
        from ..registry.mlflow_registry import PromotionRefused
        try:
            registry.promote(version, promote, by=by, note="train.release")
            report["promoted"] = promote
        except PromotionRefused as e:
            report["refusal"] = e.reasons
    return report


# ================================================================================================ D0 availability
def check_d0(sp: str = "HB_NORTH", timeout_s: int = 900) -> dict:
    """Is D0's builder usable for training? Imports it, runs D0's leakage test, and builds a small frame for 2024 (the
    training window needs 2019-2024 history in D0's lake). Returns a dict for the notes; never raises."""
    import subprocess
    import sys
    out = {"importable": False, "leakage_test": None, "leakage_output": "", "frame_rows": 0, "frame_error": None,
           "has_model_columns": None}
    try:
        from ..features import build as d0  # noqa: F401
        out["importable"] = hasattr(d0, "training_frame")
    except Exception as e:
        out["frame_error"] = f"import: {type(e).__name__}: {e}"
        return out
    test = paths.PROJECT_DIR / "tests" / "test_leakage.py"
    if test.exists():
        r = subprocess.run([sys.executable, "-m", "pytest", "-q", str(test)], cwd=str(paths.PROJECT_DIR), capture_output=True,
                           text=True, timeout=timeout_s)
        out["leakage_test"] = r.returncode == 0
        out["leakage_output"] = (r.stdout or "")[-400:]
    else:
        out["leakage_test"] = None
    try:
        X, y, d = _d0_training_frame(TrainConfig(), sp, _utc("2024-06-03"), _utc("2024-06-10"), "4h")
        out["frame_rows"] = int(len(X))
        out["has_model_columns"] = all(c in X.columns for c in (ANCHOR_COL, *SCALES["price"]))
        out["columns"] = [c for c in X.columns][:60]
    except Exception as e:
        out["frame_error"] = f"{type(e).__name__}: {str(e)[:200]}"
    return out


# ================================================================================================ CLI
def _registry(cfg: TrainConfig):
    from ..registry.mlflow_registry import Registry
    return Registry(cfg.registry_uri) if cfg.registry_uri else Registry()


def format_table(results: Sequence[SPResult]) -> str:
    """Markdown metrics table: pinball, coverage and P50 MAE per settlement point against each baseline."""
    rows = ["| point | forecaster | pinball | P10-P90 coverage | P50 MAE | spike MAE | capture |", "|---|---|---:|---:|---:|---:|---:|"]
    for r in results:
        for n in ("model",) + BASELINE_NAMES:
            m = r.holdout["pooled"][n]
            v = r.holdout["value"].get(n, {})
            cap = f"{v['capture']:.3f}" if "capture" in v else "n/a"
            rows.append(f"| {r.sp} | {'LightGBM' if n == 'model' else n} | {m['pinball_mean']:.3f} | {m['coverage_p10_p90']:.3f} | "
                        f"{m['mae_p50']:.2f} | {m.get('spike_mae_p50', float('nan')):.1f} | {cap} |")
    return "\n".join(rows)


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("command", choices=["sp", "release", "all", "d0check"])
    ap.add_argument("--sps", default=",".join(SETTLEMENT_POINTS))
    ap.add_argument("--trials", type=int, default=20)
    ap.add_argument("--threads", type=int, default=1)
    ap.add_argument("--timeout", type=float, default=1200.0)
    ap.add_argument("--tag", default="latest")
    ap.add_argument("--no-as", action="store_true")
    ap.add_argument("--promote", default="research")
    ap.add_argument("--source", default="d0", choices=["d0"], help="D0's point-in-time builder is the only source (the interim builder was retired)")
    ap.add_argument("--holdout-end", default=None, help="end of the holdout decisions (UTC); default: one day before the realised prices end")
    ap.add_argument("--registry-uri", default=None)
    ap.add_argument("--params-json", default=None, help="skip tuning: JSON with params / n_estimators / weight_pow")
    ap.add_argument("--calib-days", type=int, default=120)
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    cfg = TrainConfig(sps=tuple(a.sps.split(",")), n_trials=a.trials, num_threads=a.threads, tune_timeout_s=a.timeout,
                      include_as=not a.no_as, source=a.source, registry_uri=a.registry_uri, calib_days=a.calib_days, holdout_end=a.holdout_end,
                      params=json.loads(Path(a.params_json).read_text(encoding="utf-8")) if a.params_json else None)
    if a.command == "d0check":
        print(json.dumps(check_d0(cfg.sps[0]), indent=1, default=_jd))
        return 0
    reg = _registry(cfg)
    results = []
    if a.command in ("sp", "all"):
        for sp in cfg.sps:
            res = run_sp(cfg, sp)
            save_checkpoint(cfg, res, a.tag)                      # the result is safe on disk before any tracking call
            log_result(reg, cfg, res)
            save_checkpoint(cfg, res, a.tag)                      # again, now carrying the MLflow run id
            results.append(res)
            log.info("%s done in %.0fs: passes=%s gains(pinball)=%s", sp, res.timings["total_s"], res.passes,
                     {k: round(v, 3) for k, v in res.gains["pinball"].items()})
        print(format_table(results))
    if a.command in ("release", "all"):
        rep = release(cfg, reg, tag=a.tag, promote=a.promote or None)
        print(json.dumps(rep, indent=1, default=_jd))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
