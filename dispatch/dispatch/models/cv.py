"""Walk-forward purged validation, forecast metrics and the decision-value hook (PRD FR4 / FR6, success metric "capture").

CV DESIGN
  Rows are (decision, horizon) pairs. A row's *decision time* is when its features were frozen; its *label interval* is
  `interval_start_utc .. + 15 min`, up to 24 h after the decision. Folds go forward in time (expanding training window)
  because that is the only order production ever sees. For a validation window [V0, V1):
    * validation rows: decisions in [V0, V1)
    * training rows:   decisions before V0 - purge - embargo, AND label end <= V0 - embargo
  The purge (default 24 h, i.e. the 96-interval forecast horizon) removes training rows whose label period reaches into
  the validation window, which would otherwise teach the model prices it is later scored on. The embargo (default 24 h)
  adds a further quiet gap against serial correlation of price regimes. Features of validation rows look back at
  training-period prices; that is legitimate (production has them too) and is not purged.

METRICS (per quantile and pooled): pinball loss; P10-P90 coverage (target 80%); P50 MAE; error on spike intervals; and
  the same by horizon bucket. DECISION VALUE: `decision_value` turns forecasts into dispatch money with a planner and
  reports capture = backtest revenue / perfect-foresight revenue.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional, Sequence

import numpy as np
import pandas as pd

from .. import config
from ..contracts import BatterySpec, Plan, QUANTILES, RT
from ..units import usd_per_kw_yr
from .baselines import HORIZON_BUCKETS, bucket_of

STEP = pd.Timedelta(minutes=15)
LEVELS = {"p10": 0.1, "p50": 0.5, "p90": 0.9}
NOMINAL_COVERAGE = 0.8
SPIKE_USD = 150.0              # ASSUMPTION: an interval at or above $150/MWh counts as a spike (about the 99th percentile of 2025-26)
DEFAULT_PURGE = pd.Timedelta(hours=24)       # >= the forecast horizon (96 x 15 min)
DEFAULT_EMBARGO = pd.Timedelta(hours=24)


def _utc(t) -> pd.Timestamp:
    t = pd.Timestamp(t)
    return t.tz_localize("UTC") if t.tzinfo is None else t.tz_convert("UTC")


# ================================================================================================ metrics
def pinball(y, q, alpha: float) -> np.ndarray:
    """Pinball (quantile) loss of forecast q at level alpha, row by row."""
    d = np.asarray(y, dtype=np.float64) - np.asarray(q, dtype=np.float64)
    return np.maximum(alpha * d, (alpha - 1.0) * d)


def quantile_metrics(y, q, *, spike_usd: float = SPIKE_USD) -> dict:
    """Metrics for forecasts q of shape (n, 3) = p10, p50, p90 against realised prices y."""
    y = np.asarray(y, dtype=np.float64)
    q = np.asarray(q, dtype=np.float64)
    ok = np.isfinite(y) & np.isfinite(q).all(axis=1)
    y, q = y[ok], q[ok]
    n = len(y)
    out = {"n": int(n)}
    if n == 0:
        return out
    pl = {k: float(pinball(y, q[:, i], LEVELS[k]).mean()) for i, k in enumerate(QUANTILES)}
    out.update({f"pinball_{k}": v for k, v in pl.items()})
    out["pinball_mean"] = float(np.mean(list(pl.values())))
    out["coverage_p10_p90"] = float(np.mean((y >= q[:, 0]) & (y <= q[:, 2])))
    for i, k in enumerate(QUANTILES):
        out[f"hit_{k}"] = float(np.mean(y <= q[:, i]))               # calibration: should equal the level
    out["mae_p50"] = float(np.abs(y - q[:, 1]).mean())
    out["bias_p50"] = float((q[:, 1] - y).mean())
    out["width_p10_p90"] = float((q[:, 2] - q[:, 0]).mean())
    sp = y >= spike_usd
    out["spike_rate"] = float(sp.mean())
    if sp.any():
        out["spike_n"] = int(sp.sum())
        out["spike_mae_p50"] = float(np.abs(y[sp] - q[sp, 1]).mean())
        out["spike_bias_p50"] = float((q[sp, 1] - y[sp]).mean())
        out["spike_pinball_p90"] = float(pinball(y[sp], q[sp, 2], 0.9).mean())
        out["spike_cover_p90"] = float(np.mean(y[sp] <= q[sp, 2]))
    return out


def metrics_by_horizon(y, q, horizon, *, spike_usd: float = SPIKE_USD) -> pd.DataFrame:
    """Metrics in each horizon bucket (1-4, 5-16, 17-48, 49-96 intervals ahead)."""
    y, q, horizon = np.asarray(y), np.asarray(q), np.asarray(horizon)
    b = bucket_of(horizon)
    rows = {}
    for k, (lo, hi) in enumerate(HORIZON_BUCKETS):
        m = b == k
        if m.any():
            rows[f"h{lo}-{hi}"] = quantile_metrics(y[m], q[m], spike_usd=spike_usd)
    return pd.DataFrame(rows).T


def evaluate_frame(df: pd.DataFrame, y_col: str = "y", *, spike_usd: float = SPIKE_USD) -> dict:
    """Metrics for a long forecast frame with columns y, rt_p10, rt_p50, rt_p90."""
    return quantile_metrics(df[y_col].to_numpy(), df[[f"rt_{k}" for k in QUANTILES]].to_numpy(), spike_usd=spike_usd)


def relative_gain(model: float, baseline: float) -> float:
    """(baseline - model) / baseline: positive means the model is better (lower loss)."""
    return float((baseline - model) / baseline) if baseline else float("nan")


# ================================================================================================ monitoring reference
REFERENCE_KEYS = ("n", "pinball_mean", "pinball_p10", "pinball_p50", "pinball_p90", "hit_p10", "hit_p50", "hit_p90",
                  "coverage_p10_p90", "mae_p50", "bias_p50", "width_p10_p90")
BUCKET_KEYS = ("n", "pinball_mean", "hit_p10", "hit_p50", "hit_p90", "mae_p50")      # kept small: a version tag holds 4900 chars
_BUCKET_LABEL = tuple(f"h{lo}-{hi}" for lo, hi in HORIZON_BUCKETS)


def _pick(m: dict, keys: Sequence[str], nd: int = 5) -> dict:
    return {k: (int(m[k]) if k == "n" else round(float(m[k]), nd)) for k in keys if k in m}


def reference_metrics(y, q, horizon=None, *, window: Optional[dict] = None) -> dict:
    """What a live monitor compares itself with: one settlement point's metrics on the model's holdout.

    Returns {"pooled": REFERENCE_KEYS of `quantile_metrics` over every row, "by_horizon": {"h1-4": BUCKET_KEYS, ...} (when
    `horizon` is given), "window": `window`}. Rows are (decision, horizon) pairs exactly as in the walk-forward holdout table,
    so a monitor that scores every issued (decision, horizon) forecast compares like with like; one that scores only the freshest
    forecast of each interval should use `reference_for_horizons` with the horizons of the rows it scores."""
    y, q = np.asarray(y, dtype=np.float64), np.asarray(q, dtype=np.float64)
    out = {"pooled": _pick(quantile_metrics(y, q), REFERENCE_KEYS)}
    if horizon is not None:
        bh = metrics_by_horizon(y, q, horizon)
        out["by_horizon"] = {str(k): _pick(row.dropna().to_dict(), BUCKET_KEYS) for k, row in bh.iterrows()}
    if window:
        out["window"] = dict(window)
    return out


def reference_for_horizons(reference: dict, horizon=None) -> dict:
    """The reference numbers for a mix of horizons: the pooled numbers when `horizon` is None, otherwise the means of the
    horizon-bucket numbers weighted by the share of `horizon` in each bucket (pinball, hit rates and MAE are means over rows, so
    the weighted mean is exactly the holdout value for that mix of horizons)."""
    if horizon is None or not reference.get("by_horizon"):
        return {k: v for k, v in (reference.get("pooled") or reference).items() if k in REFERENCE_KEYS}
    b = bucket_of(np.asarray(horizon))
    share = np.bincount(b, minlength=len(HORIZON_BUCKETS)).astype(float)
    share /= share.sum()
    out = {}
    for k in BUCKET_KEYS:
        if k == "n":
            continue
        num = den = 0.0
        for i, lab in enumerate(_BUCKET_LABEL):
            row = reference["by_horizon"].get(lab)
            if share[i] > 0 and row is not None and k in row:
                num += share[i] * row[k]
                den += share[i]
        if den > 0:
            out[k] = num / den
    return out


def _rule(value, default):
    """A rule's threshold: None takes the config default (None if config has none), False switches the rule off."""
    if value is False:
        return None
    return default if value is None else float(value)


def calibration_check(y, q, *, reference: Optional[dict] = None, horizon=None, profile_p50=None, hit_tol: Optional[float] = None,
                      pinball_max: Optional[float] = None, skill_max: Optional[float] = None, min_rows: int = 96) -> dict:
    """The output-calibration health check of DESIGN.md "Monitoring policy", over one window of settled intervals.

    `y` are realised prices and `q` (n, 3) the P10/P50/P90 issued for those intervals (one row per scored forecast). A breach is
      * hit: any |share of y at or below the issued P10/P50/P90 - 0.1/0.5/0.9| > `hit_tol` (default config.CALIBRATION_HIT_TOL; a
        float, or a dict {"p10": , "p50": , "p90": } for one tolerance per quantile);
      * pinball: window pinball_mean > (1 + `pinball_max`) x the model's holdout pinball_mean (default
        config.PINBALL_DEGRADATION_MAX; needs `reference`, from `reference_metrics` / `Forecaster.holdout_metrics[sp]`, and
        `horizon` per row when the window mixes horizons so the reference is matched);
      * skill: window P50 MAE > (1 + `skill_max`) x the MAE of `profile_p50`, the 7-day profile median (the model's feature
        `rt_prof7_med`) on the same intervals (default config.SKILL_DEGRADATION_MAX if the lead defines it, else off).
    Each threshold left as None takes its config default; pass False to switch that rule off.
    Fewer than `min_rows` finite rows is "not enough data": breach False, enough_data False (the caller decides what that means)."""
    if isinstance(hit_tol, dict):                     # one tolerance per quantile: {"p10": 0.15, "p50": 0.25, "p90": 0.15}
        hit_tol = {k: _rule(hit_tol.get(k), getattr(config, "CALIBRATION_HIT_TOL", None)) for k in LEVELS}
    else:
        hit_tol = {k: _rule(hit_tol, getattr(config, "CALIBRATION_HIT_TOL", None)) for k in LEVELS}
    pinball_max = _rule(pinball_max, getattr(config, "PINBALL_DEGRADATION_MAX", None))
    skill_max = _rule(skill_max, getattr(config, "SKILL_DEGRADATION_MAX", None))
    y, q = np.asarray(y, dtype=np.float64), np.asarray(q, dtype=np.float64)
    prof = None if profile_p50 is None else np.asarray(profile_p50, dtype=np.float64)
    ok = np.isfinite(y) & np.isfinite(q).all(axis=1)
    if prof is not None:
        ok &= np.isfinite(prof)
    hz = None if horizon is None else np.asarray(horizon)[ok]
    y, q = y[ok], q[ok]
    prof = None if prof is None else prof[ok]
    res = {"breach": False, "enough_data": bool(len(y) >= min_rows), "reasons": [], "tripped": [], "n": int(len(y)), "hit": {}, "hit_error": {},
           "pinball_mean": None, "pinball_ratio": None, "skill_ratio": None,
           "thresholds": {"hit_tol": dict(hit_tol), "pinball_max": pinball_max, "skill_max": skill_max}}
    if not res["enough_data"]:
        return res
    m = quantile_metrics(y, q)
    res["metrics"] = m
    res["pinball_mean"] = m["pinball_mean"]
    for k, level in LEVELS.items():
        res["hit"][k] = m[f"hit_{k}"]
        res["hit_error"][k] = abs(m[f"hit_{k}"] - level)
        if hit_tol[k] is not None and res["hit_error"][k] > hit_tol[k]:
            res["tripped"].append(f"hit_{k}")
            res["reasons"].append(f"hit rate below {k.upper()} is {m[f'hit_{k}']:.3f}, nominal {level}, off by "
                                  f"{res['hit_error'][k]:.3f} > {hit_tol[k]}")
    if reference and pinball_max is not None:
        ref = reference_for_horizons(reference, hz).get("pinball_mean")
        if ref:
            res["pinball_ratio"] = m["pinball_mean"] / ref
            if res["pinball_ratio"] > 1 + pinball_max:
                res["tripped"].append("pinball")
                res["reasons"].append(f"pinball {m['pinball_mean']:.3f} is {res['pinball_ratio']:.2f} x the holdout {ref:.3f}"
                                      f" (limit {1 + pinball_max:.2f} x)")
    if prof is not None and skill_max is not None:
        base = float(np.abs(y - prof).mean())
        if base > 0:
            res["skill_ratio"] = m["mae_p50"] / base
            if res["skill_ratio"] > 1 + skill_max:
                res["tripped"].append("skill")
                res["reasons"].append(f"P50 MAE {m['mae_p50']:.2f} is {res['skill_ratio']:.2f} x the 7-day profile median's {base:.2f}"
                                      f" (limit {1 + skill_max:.2f} x)")
    res["breach"] = bool(res["reasons"])
    return res


def false_breach_rate(table: pd.DataFrame, *, reference: Optional[dict] = None, window: str = "7D", step: str = "4h",
                      profile_col: Optional[str] = "profile_median_7d_p50", min_share: float = 0.8, **check) -> dict:
    """Slide `calibration_check` over a forecast table of a period the model never saw and report how often it breaches.

    `table` is a walk-forward holdout table (index = target interval start, columns y, rt_p10/p50/p90 and, for the pinball and
    skill rules, horizon and `profile_col`); each window scores the rows whose target interval is in [end - window, end), the
    window end advancing by `step`. Windows with fewer than `min_share` of the median row count are skipped. On an in-distribution
    period every breach is a false one, so `breach_rate` is the false-breach rate; `episodes` counts runs of consecutive
    breaching windows, i.e. how many separate halts a monitor would have raised."""
    t = table.sort_index()
    idx = t.index
    qcols = [f"rt_{k}" for k in QUANTILES]
    y, q = t["y"].to_numpy(), t[qcols].to_numpy()
    hz = t["horizon"].to_numpy() if "horizon" in t.columns else None
    prof = t[profile_col].to_numpy() if profile_col and profile_col in t.columns else None
    w, s = pd.Timedelta(window), pd.Timedelta(step)
    ends = pd.date_range(idx.min() + w, idx.max() + s, freq=s)
    spans = [(int(idx.searchsorted(e - w, "left")), int(idx.searchsorted(e, "left"))) for e in ends]
    sizes = np.array([b - a for a, b in spans])
    keep = sizes >= min_share * np.median(sizes)
    flags, reasons = [], {}
    for (a, b), k in zip(spans, keep):
        if not k:
            continue
        r = calibration_check(y[a:b], q[a:b], reference=reference, horizon=None if hz is None else hz[a:b],
                              profile_p50=None if prof is None else prof[a:b], **check)
        flags.append(r["breach"])
        for key in r["tripped"]:
            reasons[key] = reasons.get(key, 0) + 1
    f = np.asarray(flags, dtype=bool)
    n = int(len(f))
    return {"windows": n, "breach_rate": float(f.mean()) if n else float("nan"),
            "episodes": int((f[1:] & ~f[:-1]).sum() + (1 if n and f[0] else 0)),
            "by_reason": {k: v / n for k, v in reasons.items()} if n else {}}


# ================================================================================================ folds
@dataclass(frozen=True)
class Fold:
    index: int
    train_start: pd.Timestamp
    val_start: pd.Timestamp
    val_end: pd.Timestamp
    purge: pd.Timedelta = DEFAULT_PURGE
    embargo: pd.Timedelta = DEFAULT_EMBARGO

    @property
    def train_decision_end(self) -> pd.Timestamp:
        """Training decisions must be strictly before this."""
        return self.val_start - self.purge - self.embargo

    @property
    def label_cutoff(self) -> pd.Timestamp:
        """Training label intervals must END at or before this."""
        return self.val_start - self.embargo

    def describe(self) -> str:
        return (f"fold {self.index}: train decisions [{self.train_start:%Y-%m-%d}, {self.train_decision_end:%Y-%m-%d %H:%M}) "
                f"-> validate [{self.val_start:%Y-%m-%d}, {self.val_end:%Y-%m-%d})")


def walk_forward_folds(val_start, val_end, *, span="90D", train_start="2019-01-01", purge=DEFAULT_PURGE,
                       embargo=DEFAULT_EMBARGO, min_train="365D") -> list:
    """Consecutive validation windows of length `span` covering [val_start, val_end), each trained on everything from
    `train_start` up to the purge gap. The purge must cover the forecast horizon (24 h)."""
    purge, embargo, span = pd.Timedelta(purge), pd.Timedelta(embargo), pd.Timedelta(span)
    if purge < DEFAULT_PURGE:
        raise ValueError(f"purge {purge} is shorter than the 24 h forecast horizon: validation labels would leak")
    v0, v1, t0 = _utc(val_start), _utc(val_end), _utc(train_start)
    folds, k, cur = [], 0, v0
    while cur < v1:
        end = min(cur + span, v1)
        if cur - purge - embargo - t0 < pd.Timedelta(min_train):
            raise ValueError(f"fold {k} has less than {min_train} of training data before {cur}")
        folds.append(Fold(k, t0, cur, end, purge, embargo))
        cur, k = end, k + 1
    return folds


def fold_masks(fold: Fold, decision_utc, interval_start_utc):
    """Boolean (train, validation) masks over rows. Both inputs are tz-aware, one entry per row."""
    d = pd.DatetimeIndex(decision_utc)
    label_end = pd.DatetimeIndex(interval_start_utc) + STEP
    train = (d >= fold.train_start) & (d < fold.train_decision_end) & (label_end <= fold.label_cutoff)
    val = (d >= fold.val_start) & (d < fold.val_end)
    return np.asarray(train), np.asarray(val)


def assert_no_overlap(fold: Fold, decision_utc, interval_start_utc) -> dict:
    """Prove a fold's train and validation rows are separated by at least the purge gap; raise if not."""
    tr, va = fold_masks(fold, decision_utc, interval_start_utc)
    d = pd.DatetimeIndex(decision_utc)
    s = pd.DatetimeIndex(interval_start_utc)
    if (tr & va).any():
        raise AssertionError("a row is in both train and validation")
    if not tr.any() or not va.any():
        raise AssertionError(f"{fold.describe()}: empty train or validation")
    last_train_label_end = (s[tr] + STEP).max()
    first_val_decision = d[va].min()
    last_train_decision = d[tr].max()
    if last_train_label_end > fold.val_start - fold.embargo:
        raise AssertionError("a training label ends inside the embargo / validation window")
    if first_val_decision - last_train_decision < fold.purge + fold.embargo:
        raise AssertionError("train and validation decisions are closer than purge + embargo")
    return {"gap_decision": first_val_decision - last_train_decision, "gap_label": first_val_decision - last_train_label_end}


@dataclass
class CVResult:
    folds: list
    per_fold: pd.DataFrame            # one row per fold: the quantile_metrics
    pooled: dict                      # metrics over all validation rows
    oof: pd.DataFrame                 # long out-of-fold frame: decision_utc, horizon, y, rt_p10/p50/p90 (+ fold)


def cross_validate(fit_predict: Callable[[np.ndarray, np.ndarray], np.ndarray], y, decision_utc, interval_start_utc,
                   horizon, folds: Sequence[Fold], *, spike_usd: float = SPIKE_USD, check: bool = True) -> CVResult:
    """Run `fit_predict(train_idx, val_idx) -> (n_val, 3) quantiles` on every fold; collect honest out-of-fold metrics.

    The callable owns the data (it closes over the feature matrix) and must fit on `train_idx` only."""
    y = np.asarray(y, dtype=np.float64)
    d = pd.DatetimeIndex(decision_utc)
    s = pd.DatetimeIndex(interval_start_utc)
    horizon = np.asarray(horizon)
    rows, oofs = [], []
    for f in folds:
        if check:
            assert_no_overlap(f, d, s)
        tr, va = fold_masks(f, d, s)
        tr_i, va_i = np.where(tr)[0], np.where(va)[0]
        q = np.asarray(fit_predict(tr_i, va_i), dtype=np.float64)
        m = quantile_metrics(y[va_i], q, spike_usd=spike_usd)
        m.update(fold=f.index, val_start=f.val_start, n_train=int(len(tr_i)))
        rows.append(m)
        o = pd.DataFrame({"decision_utc": d[va_i], "horizon": horizon[va_i], "y": y[va_i], "fold": f.index,
                          **{f"rt_{k}": q[:, i] for i, k in enumerate(QUANTILES)}}, index=s[va_i])
        oofs.append(o)
    oof = pd.concat(oofs)
    pooled = evaluate_frame(oof, spike_usd=spike_usd)
    return CVResult(list(folds), pd.DataFrame(rows).set_index("fold"), pooled, oof)


# ================================================================================================ decision value
# CAPTURE RATE (PRD success metric): backtest revenue / perfect-foresight revenue.
# Protocol: rolling horizon. At each decision the planner sees the forecast's P50 over the next 96 intervals and the
# battery's state of charge, produces a plan, and the first `execute_steps` intervals of that plan are executed and
# SETTLED AT THE REALISED PRICE; the state of charge carries into the next decision. The perfect-foresight run uses the
# same planner, cadence and settlement but plans on the realised prices. Same code path for both, only the price view
# differs (DESIGN.md: "the same function runs both modes").
#
# PLANNER. The target is `dispatch.optimizer.lp.solve` (D1b). Until it exists this module falls back on the research
# arena's dynamic programme (`arena.battery.value_det` for the value function, `act_on_value` to roll it forward on the
# forecast prices, and `oracle_days` as an independent daily ceiling). The fallback is energy only, per MW of inverter
# power, with the arena's discretisation (0.125 MWh state units, losses on the charge side). `DecisionValue.planner`
# says which one ran.
@dataclass
class DecisionValue:
    planner: str
    capture: float
    revenue_usd: float
    pf_usd: float
    n_decisions: int
    days: float
    usd_per_kw_yr: float
    pf_usd_per_kw_yr: float
    oracle_days_usd: Optional[float] = None
    daily: Optional[pd.DataFrame] = None
    notes: tuple = ()

    def as_dict(self) -> dict:
        return {k: v for k, v in self.__dict__.items() if k not in ("daily", "notes")}


def default_battery(settlement_point: str = "HB_NORTH") -> BatterySpec:
    """The arena's grid-scale battery (1 MW / 2 MWh, 85% round trip, $20/MWh wear, no reserve) so results line up."""
    return BatterySpec("decision-value", settlement_point, power_mw=1.0, energy_mwh=2.0, rte=config.DEFAULT_RTE,
                       loss_side="charge", wear_usd_per_mwh=config.DEFAULT_WEAR_USD_PER_MWH)


def _arena_spec(spec: BatterySpec):
    """Per-MW arena battery equivalent to `spec` (the arena works in 0.125 MWh units per MW of power)."""
    from types import SimpleNamespace
    from .. import paths
    paths.use_arena()
    from arena import config as AC
    unit = AC.U
    e_units = int(round(spec.energy_mwh / spec.power_mw / unit))
    r_units = int(round(spec.soc_min_mwh / spec.power_mw / unit))
    hi_units = int(round(spec.soc_hi_mwh / spec.power_mw / unit))
    return SimpleNamespace(E=hi_units, R=r_units, S=hi_units + 1, rte=spec.rte, deg=spec.wear_usd_per_mwh,
                           cap=10 ** 6, key="dispatch", unit=unit, e_units=e_units)


def arena_dp_plan(spec: BatterySpec, prices: pd.DataFrame, soc0_mwh: float, *, mode: str = "forecast", **_ignored) -> Plan:
    """Planner with the `Optimizer` signature built on the arena's dynamic programme (fallback until D1b's LP exists).

    Solves value-to-go on `prices` (a PriceFrame: rt_usd_per_mwh on the 15-minute index), then rolls the best actions
    forward on those same prices. Energy only; `soc0_mwh` is rounded to the arena's 0.125 MWh/MW grid."""
    from .. import paths
    paths.use_arena()
    from arena.battery import act_on_value, feasible, value_det
    sp_ = _arena_spec(spec)
    p = prices[RT].to_numpy(dtype=np.float64)
    n = len(p)
    unit = sp_.unit
    # horizon-end water value: a quarter-quantile price over the window, as the arena's Forecaster does
    w_end = np.array([max(float(np.quantile(p, 0.25)), 0.0) / spec.rte])
    # the arena DP is hard-wired to 96 intervals per day (config.NI); pad / trim the window to that length
    from arena import config as AC
    F = np.empty((1, AC.NI))
    F[0, :min(n, AC.NI)] = p[:AC.NI]
    if n < AC.NI:
        F[0, n:] = p[-1]
    V = value_det(F, sp_, w_end)
    soc = np.array([int(np.clip(round(soc0_mwh / spec.power_mw / unit), sp_.R, sp_.E))])
    disc = np.zeros(1, int)
    acts = np.zeros(n, int)
    soc_end = np.zeros(n)
    res = np.full(1, sp_.R)
    for t in range(min(n, AC.NI)):
        a = act_on_value(V[:, t + 1], np.array([F[0, t]]), soc, disc, sp_, res)
        a = feasible(a, soc, disc, sp_, np.where(a < 0, sp_.R, res))
        acts[t] = a[0]
        soc = soc - a
        soc_end[t] = soc[0] * unit * spec.power_mw
    dt = config.DT_RT_H
    k = spec.power_mw
    discharge_mw = np.where(acts > 0, acts, 0) * unit / dt * k
    charge_mw = np.where(acts < 0, -acts, 0) * unit / dt / spec.rte * k       # grid side: stored energy / rte
    energy = (discharge_mw - charge_mw) * p * dt
    wear = spec.wear_usd_per_mwh * discharge_mw * dt
    frame = pd.DataFrame({"charge_mw": charge_mw, "discharge_mw": discharge_mw, "soc_end_mwh": soc_end,
                          "energy_usd": energy, "as_usd": 0.0, "wear_usd": wear}, index=prices.index[:n])
    return Plan(asset_id=spec.asset_id, mode=mode, solver="arena-dp", status="optimal",
                objective_usd=float((energy - wear).sum()), inputs_sha256="", frame=frame)


def default_planner():
    """(name, callable): D1b's LP when it exists, else the arena DP. The callable has the `Optimizer` signature."""
    try:
        from ..optimizer import lp
        fn = getattr(lp, "solve")
        return "lp.solve", fn
    except (ImportError, AttributeError):
        return "arena-dp (lp.solve not available yet)", arena_dp_plan


def _settle(plan_frame: pd.DataFrame, realised: np.ndarray, spec: BatterySpec, steps: int) -> float:
    """Dollars of the first `steps` plan rows at realised prices: sold - bought - wear (energy only)."""
    dt = config.DT_RT_H
    f = plan_frame.iloc[:steps]
    net = f["discharge_mw"].to_numpy() - f["charge_mw"].to_numpy()
    wear = spec.wear_usd_per_mwh * f["discharge_mw"].to_numpy() * dt
    return float((net * realised[:steps] * dt - wear).sum())


def _prepare(forecasts: pd.DataFrame, prices: pd.Series, quantile: str, horizon: int, execute_steps: Optional[int]):
    """Decisions (evenly spaced, each with a full window), their forecast and realised price vectors."""
    col = f"rt_{quantile}"
    if col not in forecasts.columns:
        raise KeyError(f"forecast frame has no {col!r}")
    f = forecasts[["decision_utc", "horizon", col]].copy()
    f["interval_start_utc"] = f.index
    decisions = pd.DatetimeIndex(f["decision_utc"].unique()).sort_values()
    if len(decisions) < 2:
        raise ValueError("need at least two decisions")
    gaps = np.asarray((decisions[1:] - decisions[:-1]) // STEP)
    steps = int(execute_steps or np.median(gaps))
    if steps < 1:
        raise ValueError("execute_steps must be >= 1")
    if (gaps != steps).any():                      # thin out to decisions that are at least `steps` apart
        keep = [decisions[0]]
        for d in decisions[1:]:
            if int((d - keep[-1]) // STEP) >= steps:
                keep.append(d)
        decisions = pd.DatetimeIndex(keep)
    real = prices.sort_index()
    by_dec = {d: g for d, g in f.groupby("decision_utc")}
    used, F, Rl, idxs = [], [], [], []
    for d in decisions:
        g = by_dec[d].sort_values("horizon").head(horizon)
        idx = pd.DatetimeIndex(g["interval_start_utc"])
        if len(idx) < max(steps, 1) or idx[0] != d.floor("15min"):
            continue
        realised = real.reindex(idx).to_numpy(dtype=np.float64)
        if np.isnan(realised[:steps]).any():
            continue
        fc = g[col].to_numpy(dtype=np.float64)
        used.append(d)
        F.append(fc)
        Rl.append(pd.Series(realised).ffill().to_numpy())            # a gap in the realised window repeats the last price, so the
        idxs.append(idx)                                              # perfect-foresight run never depends on the forecaster
    return real, pd.DatetimeIndex(used), F, Rl, idxs, steps


def _arena_roll(V: np.ndarray, prices: np.ndarray, soc: int, sp_, steps: int):
    """The arena's act_on_value + feasible for one lane, in scalar Python: roll `steps` intervals on value table V
    (97 x S) at `prices`. Returns (actions in units, final state of charge in units)."""
    unit = sp_.unit
    rte, deg, E, R = sp_.rte, sp_.deg, sp_.E, sp_.R
    acts = np.zeros(steps, dtype=np.int64)
    for t in range(steps):
        p = float(prices[t])
        best, ba = -1e300, 0
        for a in (-2, -1, 0, 1, 2):
            s2 = soc - a
            if s2 > E or s2 < 0 or (a > 0 and s2 < R):
                continue
            if a < 0:
                r = -p * (-a * unit) / rte
            else:
                r = (p - deg) * (a * unit)
            val = r + V[t + 1, s2]
            if val > best + 1e-9:
                best, ba = val, a
        acts[t] = ba
        soc -= ba
    return acts, soc


def _arena_money(acts: np.ndarray, realised: np.ndarray, sp_, spec: BatterySpec) -> float:
    """Settle arena actions at realised prices, per the Plan convention: grid-side charge MW = stored energy / rte."""
    dt = config.DT_RT_H
    dis = np.where(acts > 0, acts, 0) * sp_.unit / dt * spec.power_mw
    chg = np.where(acts < 0, -acts, 0) * sp_.unit / dt / spec.rte * spec.power_mw
    return float(((dis - chg) * realised * dt - spec.wear_usd_per_mwh * dis * dt).sum())


def _decision_value_arena(F, Rl, steps, spec, soc0_frac):
    """Batched arena planner: one value_det call per mode for ALL decisions (lanes), then a scalar roll per decision.
    Numerically the same as calling `arena_dp_plan` decision by decision (tests/test_models.py checks it)."""
    from .. import paths
    paths.use_arena()
    from arena import config as AC
    from arena.battery import value_det
    sp_ = _arena_spec(spec)
    n = len(F)
    Fm = np.vstack([np.pad(f[:AC.NI], (0, max(0, AC.NI - len(f))), mode="edge") for f in F])
    Rm = np.vstack([np.pad(r[:AC.NI], (0, max(0, AC.NI - len(r))), mode="edge") for r in Rl])
    chunk = 1500
    soc_init = int(round((spec.soc_min_mwh + soc0_frac * (spec.soc_hi_mwh - spec.soc_min_mwh)) / spec.power_mw / sp_.unit))
    soc_fc = soc_pf = soc_init
    money_fc, money_pf = np.zeros(n), np.zeros(n)
    for lo in range(0, n, chunk):
        hi = min(lo + chunk, n)
        wf = np.maximum(np.quantile(Fm[lo:hi], 0.25, axis=1), 0.0) / spec.rte
        wr = np.maximum(np.quantile(Rm[lo:hi], 0.25, axis=1), 0.0) / spec.rte
        Vf = value_det(Fm[lo:hi], sp_, wf)
        Vr = value_det(Rm[lo:hi], sp_, wr)
        for i in range(lo, hi):
            a_fc, soc_fc = _arena_roll(Vf[i - lo], Fm[i], soc_fc, sp_, steps)
            a_pf, soc_pf = _arena_roll(Vr[i - lo], Rm[i], soc_pf, sp_, steps)
            money_fc[i] = _arena_money(a_fc, Rm[i, :steps], sp_, spec)
            money_pf[i] = _arena_money(a_pf, Rm[i, :steps], sp_, spec)
    to_mwh = sp_.unit * spec.power_mw
    return money_fc, money_pf, soc_fc * to_mwh, soc_pf * to_mwh, soc_init * to_mwh


def decision_value(fn_plan: Optional[Callable] = None, *, forecasts: pd.DataFrame, prices: pd.Series,
                   spec: Optional[BatterySpec] = None, execute_steps: Optional[int] = None, quantile: str = "p50",
                   horizon: int = 96, soc0_frac: float = 0.0, with_oracle: bool = True,
                   inventory_usd_per_mwh: Optional[float] = None, pf_cache: Optional[dict] = None) -> DecisionValue:
    """Capture rate of a forecaster under a planner.

    forecasts  long frame indexed by `interval_start_utc` with columns decision_utc, horizon and rt_<quantile>
               (a batch_predict / out-of-fold frame). Decisions must be evenly spaced; `execute_steps` defaults to that
               spacing in 15-minute intervals.
    prices     realised RT price ($/MWh) on the 15-minute UTC index.
    fn_plan    `Optimizer`-shaped callable (spec, prices, soc0_mwh, *, mode, allow_as=...) -> Plan. None picks
               `default_planner()`. Plans are made on the forecast ('forecast') or the realised price ('pf'). When the
               planner is the arena fallback a batched implementation with identical numbers is used.
    pf_cache   a dict shared across calls on the same decisions, prices and battery: the perfect-foresight run does not depend on
               the forecaster, so an LP planner solves it once instead of once per forecaster.
    inventory_usd_per_mwh  value of stored energy left in the battery at the end (and put in at the start): a rolling run
               ends mid-cycle, and without this a plan that bought energy for tomorrow looks like a loss. None = the
               median realised price over the scored period less the wear charge, floored at zero (same for both runs)."""
    name, planner = ("custom", fn_plan) if fn_plan is not None else default_planner()
    if fn_plan is arena_dp_plan:
        name = "arena-dp"
    elif fn_plan is not None and str(getattr(fn_plan, "__module__", "")).startswith("dispatch.optimizer"):
        name = "lp.solve"
    spec = spec or default_battery()
    real, decisions, F, Rl, idxs, steps = _prepare(forecasts, prices, quantile, horizon, execute_steps)
    if len(decisions) == 0:
        raise ValueError("no decision could be scored (check horizon alignment and price coverage)")
    if planner is arena_dp_plan:
        m_fc, m_pf, soc_fc, soc_pf, soc_init = _decision_value_arena(F, Rl, steps, spec, soc0_frac)
    else:
        soc_fc = soc_pf = soc_init = spec.soc_min_mwh + soc0_frac * (spec.soc_hi_mwh - spec.soc_min_mwh)
        m_fc, m_pf = np.zeros(len(decisions)), np.zeros(len(decisions))
        key = (name, decisions.as_unit("ns").asi8.tobytes(), steps, horizon, soc0_frac, spec.asset_id, spec.power_mw,
               spec.energy_mwh, spec.soc_min_mwh, spec.soc_hi_mwh, spec.rte, spec.loss_side, spec.wear_usd_per_mwh,
               hash(np.concatenate(Rl).tobytes()) if pf_cache is not None else 0)
        cached = pf_cache.get(key) if pf_cache is not None else None
        if cached is not None:
            m_pf, soc_pf = cached
        for i, (d, fc, rl, idx) in enumerate(zip(decisions, F, Rl, idxs)):
            plan_fc = planner(spec, pd.DataFrame({RT: fc}, index=idx), soc_fc, mode="forecast", allow_as=False)
            if not getattr(plan_fc, "ok", True):
                raise RuntimeError(f"planner returned a non-optimal plan at {d}")
            m_fc[i] = _settle(plan_fc.frame, rl, spec, steps)
            soc_fc = float(plan_fc.frame["soc_end_mwh"].iloc[steps - 1])
            if cached is None:
                plan_pf = planner(spec, pd.DataFrame({RT: rl}, index=idx), soc_pf, mode="pf", allow_as=False)
                if not getattr(plan_pf, "ok", True):
                    raise RuntimeError(f"planner returned a non-optimal plan at {d}")
                m_pf[i] = _settle(plan_pf.frame, rl, spec, steps)
                soc_pf = float(plan_pf.frame["soc_end_mwh"].iloc[steps - 1])
        if cached is None and pf_cache is not None:
            pf_cache[key] = (m_pf.copy(), soc_pf)
    day = decisions.tz_convert(config.TZ_MARKET).date
    dd = pd.DataFrame({"revenue_usd": m_fc, "pf_usd": m_pf}).groupby(pd.Index(day, name="date")).sum()
    dd.index = pd.DatetimeIndex(dd.index, name="date")
    if inventory_usd_per_mwh is None:
        span = real[(real.index >= decisions[0]) & (real.index < decisions[-1] + (horizon + steps) * STEP)]
        inventory_usd_per_mwh = max(float(np.nanmedian(span.to_numpy())) - spec.wear_usd_per_mwh, 0.0) if len(span) else 0.0
    rev = float(m_fc.sum()) + inventory_usd_per_mwh * (soc_fc - soc_init)
    pf = float(m_pf.sum()) + inventory_usd_per_mwh * (soc_pf - soc_init)
    n_used = len(decisions)
    days = n_used * steps * 0.25 / 24.0
    years = max(days / 365.25, 1e-9)
    oracle = _oracle_days_total(real, decisions, steps, spec) if (with_oracle and name.startswith("arena")) else None
    notes = (f"inventory valued at ${inventory_usd_per_mwh:.2f}/MWh at the end of the run",)
    if name.startswith("arena"):
        notes += ("planner: arena DP fallback (D1b lp.solve not available); energy only; per-MW arena discretisation",)
    return DecisionValue(planner=name, capture=rev / pf if pf else float("nan"), revenue_usd=rev, pf_usd=pf,
                         n_decisions=n_used, days=days, usd_per_kw_yr=float(usd_per_kw_yr(rev, spec.power_mw, years)),
                         pf_usd_per_kw_yr=float(usd_per_kw_yr(pf, spec.power_mw, years)), oracle_days_usd=oracle,
                         daily=dd, notes=notes)


def _oracle_days_total(real: pd.Series, decisions, steps: int, spec: BatterySpec) -> Optional[float]:
    """Arena `oracle_days` ceiling summed over the local days covered: an independent perfect-foresight reference.

    Each local day starts at the reserve and may end anywhere, so it is a looser ceiling than the rolling PF run."""
    from .. import paths
    paths.use_arena()
    from arena.battery import oracle_days
    sp_ = _arena_spec(spec)
    sp_.cap = int(round((sp_.E - sp_.R) * 4.0))                      # the arena's physical 4 cycles/day sanity cap
    loc = real.index.tz_convert(config.TZ_MARKET)
    days = pd.Series(real.to_numpy(), index=loc).groupby(loc.date)
    first = pd.Timestamp(decisions[0]).tz_convert(config.TZ_MARKET).date()
    last = (pd.Timestamp(decisions[-1]) + steps * STEP).tz_convert(config.TZ_MARKET).date()
    rows = []
    for day, g in days:
        if day < first or day > last or len(g) != 96:
            continue
        rows.append(g.to_numpy())
    if not rows:
        return None
    return float(oracle_days(np.array(rows), sp_).sum() * spec.power_mw)
