"""The monitoring checks of PRD FR11, as pure functions that return `Breach` objects.

    stale data        catalog.freshness against DATA_FRESHNESS_TARGET_S plus a per-report allowance. Only the reports the model
                      cannot work without can HALT (HALT_ELIGIBLE_REPORTS); a stale diagnostic report is an alert.
    feature drift     population stability index per feature against the reference frame stored with the model
                      (an in-house PSI; Evidently's KS drift test is an optional second opinion). ALERT ONLY
                      (config.DRIFT_PSI_HALTS = False, DESIGN.md "Monitoring policy"): the metric and the alert stay, it never halts.
    capture decay     rolling paper revenue / perfect-foresight revenue against a baseline
    slippage (Gate 3) paper-ledger replay against a backtest over the same period
    calibration       once a day, over the last CALIBRATION_WINDOW_INTERVALS settled intervals: the hit rate of the issued
                      P10/P50/P90 and the P50 error against the 7-day profile median (D1a's `models.cv.calibration_check`).
                      One breach is an alert plus a retrain request; CALIBRATION_HALT_AFTER (2) consecutive daily breaches halt.
                      The pinball-against-holdout rule is alert-only (PINBALL_DEGRADATION_MAX).

None of these touches Redis, the clock or the network. `monitoring/runner.py` feeds them and `monitoring/halt_policy.py`
decides what repeated breaches do. A check that cannot run (an input is missing) is not a breach; the runner reports it
as "skipped" and the cycle's own feature-age gate stays the hard stop.

Gate 3 (PRD phase 2: "Paper matches backtest"). Method:
  1. Pick a period of history with realised prices. Run the backtest (D1b's walk-forward harness, or any function that
     returns revenue per interval) with the registered model.
  2. Replay the same decision times through `service.cycle.run_cycle(asset, t, deps)` with `now_utc=t`, features loaded
     point in time before each call, into a fresh paper ledger (`service.cycle.replay_cycles` does the loop).
  3. `gate3(ledger, spec, prices, backtest, start, end)` replays the ledger file at the realised prices and compares its
     revenue with the backtest's: slippage = |paper - backtest| / max(|backtest|, floor). Pass = slippage <= SLIPPAGE_TOL
     (config, 5%) and the ledger replay is internally consistent (SOC adds up, every limit holds).
Same model, same prices, same code path: slippage is 0 up to float noise. Anything that makes paper differ (a guard
rejection, a stale feature, a skipped cycle, a different accounting) shows up as slippage.
"""
from __future__ import annotations

import math
import threading
from dataclasses import dataclass, field
from typing import Iterable, Mapping, Optional, Union

import numpy as np
import pandas as pd

from .. import config
from ..config import DATA_FRESHNESS_TARGET_S, DRIFT_PSI_ALERT, SLIPPAGE_TOL

CHECK_STALE, CHECK_DRIFT, CHECK_CAPTURE, CHECK_SLIPPAGE = "stale_data", "feature_drift", "capture_decay", "slippage"
CHECK_CALIBRATION = "calibration"
CHECKS = (CHECK_STALE, CHECK_DRIFT, CHECK_CAPTURE, CHECK_SLIPPAGE, CHECK_CALIBRATION)

# ---- stale data: per-report allowances (named constants; label each)
# Only the LIVE inputs of the platform are watched: the reports the online features are built from (D0's INGEST_IDS), by
# D0's catalog id (NP code) and by MIS reportTypeId. Anything else that `catalog.freshness` returns (yearly archives, static
# lists, the reports adopted once from the research folder and never polled again) is not watched, because it is allowed to
# be old; watching it would halt dispatch for nothing. The cycle's own gate (feature age) sees only the NEWEST input, so a
# single stale report behind fresh ones is exactly what this check exists to catch.
REPORT_CADENCE_S = {
    "NP6-905-CD": 900, "12301": 900,          # REAL: RT settlement point prices, one file per 15-minute interval
    "NP6-331-CD": 900, "24898": 900,          # REAL: RT clearing prices for capacity, 15-minute settlement interval
    "NP6-322-CD": 300, "13114": 300,          # REAL: SCED system lambda, one file per SCED run
    "NP6-86-CD": 3600, "12302": 3600,         # REAL: SCED shadow prices, hourly files
    "NP3-565-CD": 3600, "14837": 3600,        # REAL: seven-day load forecast by model and weather zone, hourly issues
    "NP4-190-CD": 86400, "12331": 86400,      # REAL: DAM settlement point prices, once a day
    "NP4-188-CD": 86400, "12329": 86400,      # REAL: DAM clearing prices for capacity, once a day
}
# DERIVED (D0, notes/d0.md section 7, 3 to 10 Oct 2026, applied by I1): ERCOT's own postings are sometimes late. On 8 Oct something stalled
# for ~20 minutes: the 15-minute price files came 1,285 s apart (cadence 900), SCED lambda 905 s apart (300). The allowance is cadence +
# this + DATA_FRESHNESS_TARGET_S, so one missed posting passes and two breach. The day-ahead reports kept 4 h: the longest gap seen was
# 25 h 2 min against an allowance of 28 h 5 min, so a missed day still breaches.
REPORT_EXTRA_ALLOWANCE_S = {
    "NP6-905-CD": 900, "12301": 900,          # allowance 2,100 s
    "NP6-331-CD": 900, "24898": 900,          # allowance 2,100 s
    "NP6-322-CD": 900, "13114": 900,          # allowance 1,500 s
    "NP6-86-CD": 3600, "12302": 3600,         # allowance 7,500 s
    "NP3-565-CD": 3600, "14837": 3600,        # allowance 7,500 s
    "NP4-190-CD": 4 * 3600, "12331": 4 * 3600, "NP4-188-CD": 4 * 3600, "12329": 4 * 3600,    # allowance 101,100 s
}
MONITORED_REPORTS = frozenset(REPORT_CADENCE_S)
# Which stale reports can HALT dispatch (lead decision, 10 Oct 2026, on D0's evidence): the RT price feed (NP6-905-CD), which every
# feature and the cycle's age gate stand on, and the day-ahead price report (NP4-190-CD), which posts on a once-a-day schedule whose
# longest gap (25 h 2 min) sits well inside its allowance, so a breach means a day was really missed. The rest (RT AS prices, SCED
# lambda, SCED shadow prices, the load forecast) are diagnostics or NaN-tolerant optional inputs: stale means an alert, not a halt.
# NP4-188-CD (day-ahead AS prices) is what D1a's model reads (19.6% of its gain), so it halts too (lead, 11 Oct): the rule is
# "a report the served model reads can halt". NP4-190-CD stays because it posts with NP4-188-CD and flags a missed DAM day.
HALT_ELIGIBLE_REPORTS = frozenset({"NP6-905-CD", "12301", "NP4-190-CD", "12331", "NP4-188-CD", "12329"})

# ---- drift
PSI_BINS = 10                                 # ASSUMPTION: decile bins of the reference, the usual PSI setup
PSI_EPS = 1e-4                                # ASSUMPTION: share floor so an empty bin cannot give log(0)
DRIFT_MIN_ROWS = 96                           # ASSUMPTION: fewer served rows than one day of intervals is too few to compare
DRIFT_WINDOW_ROWS = 96 * 12                   # ASSUMPTION: the newest ~12 cycles' feature rows are the "current" sample

# ---- capture rate and slippage
CAPTURE_DECAY_TOL = 0.2                       # ASSUMPTION: breach when capture falls more than 20% (relative) below its baseline
SLIPPAGE_DENOM_FLOOR_USD = 1.0                # ASSUMPTION: below $1 of backtest revenue the ratio says nothing; use $1


@dataclass(frozen=True)
class Breach:
    check: str                 # one of CHECKS (or "<check>" with severity "error" when the check itself failed)
    subject: str               # report id, feature name or asset id
    value: float
    threshold: float
    message: str
    severity: str = "breach"   # "breach" or "error" (the check could not run)
    halts: bool = True         # False: an alert only. The halt policy records it and the metrics show it, but it never counts toward a halt


def check_halts(check: str) -> bool:
    """Can a breach of this check count toward an automatic halt? Feature PSI cannot unless config.DRIFT_PSI_HALTS is switched on
    (DESIGN.md "Monitoring policy": it flags nearly every feature on in-distribution data). Read at call time, so a test can patch it."""
    return check != CHECK_DRIFT or bool(config.DRIFT_PSI_HALTS)


# ================================================================================================ stale data
def report_allowance_s(report_id: str) -> float:
    """Age at which a monitored report counts as stale: its publication cadence + any normal lateness + the PRD's
    publish-to-store target (config.DATA_FRESHNESS_TARGET_S). KeyError for a report that is not monitored."""
    rid = str(report_id)
    return float(REPORT_CADENCE_S[rid] + REPORT_EXTRA_ALLOWANCE_S.get(rid, 0) + DATA_FRESHNESS_TARGET_S)


def stale_data_breaches(ages: Mapping[str, float], *, allowances: Optional[Mapping[str, float]] = None) -> list:
    """A Breach for every monitored report whose age (from `catalog.freshness`) exceeds its allowance. A report that is not in
    MONITORED_REPORTS (and has no entry in `allowances`) is ignored. An age that is NaN or not a number counts as stale."""
    out = []
    for report, age in ages.items():
        if str(report) not in MONITORED_REPORTS and report not in (allowances or {}):
            continue
        limit = float((allowances or {}).get(report, report_allowance_s(report) if str(report) in MONITORED_REPORTS else math.inf))
        try:
            age = float(age)
        except (TypeError, ValueError):
            age = math.nan
        if not math.isfinite(age) or age > limit:
            halts = str(report) in HALT_ELIGIBLE_REPORTS or str(report) not in MONITORED_REPORTS      # an explicitly allowanced report was asked for
            out.append(Breach(CHECK_STALE, str(report), age, limit,
                              f"{report} is {age:.0f} s old, allowed {limit:.0f} s" + ("" if halts else " (alert only)"), halts=halts))
    return out


def allowances_for(ages: Iterable[str]) -> dict:
    return {r: report_allowance_s(r) for r in ages if str(r) in MONITORED_REPORTS}


# ================================================================================================ feature drift
def psi(reference, current, *, bins: int = PSI_BINS, eps: float = PSI_EPS) -> float:
    """Population stability index of `current` against `reference` for one feature.

    Numeric: bin edges are the reference's quantiles (open at both ends); missing values form their own bin. Anything
    else is treated as categories. PSI = sum (a_i - e_i) ln(a_i / e_i) with shares floored at `eps`. Rule of thumb:
    < 0.1 stable, 0.1 to 0.25 moderate, > 0.25 large; DRIFT_PSI_ALERT (0.2) is the alert line.
    """
    ref, cur = pd.Series(reference), pd.Series(current)
    if len(ref) == 0 or len(cur) == 0:
        return math.nan
    numeric = pd.api.types.is_numeric_dtype(ref) and not pd.api.types.is_bool_dtype(ref)
    if numeric:
        r = pd.to_numeric(ref, errors="coerce").to_numpy(dtype=float, na_value=np.nan)
        c = pd.to_numeric(cur, errors="coerce").to_numpy(dtype=float, na_value=np.nan)
        r_nan, c_nan = np.isnan(r), np.isnan(c)
        r, c = r[~r_nan], c[~c_nan]
        e_shares, a_shares = [], []
        if len(r) == 0:
            e_shares, a_shares = [0.0], [0.0]
        else:
            edges = np.unique(np.quantile(r, np.linspace(0.0, 1.0, bins + 1)))
            if len(edges) <= 2:                                  # (nearly) constant reference: "equals it" vs "does not"
                v = edges[0]
                e_shares = [float(np.mean(r == v)), float(np.mean(r != v))]
                a_shares = [float(np.mean(c == v)) if len(c) else 0.0, float(np.mean(c != v)) if len(c) else 0.0]
            else:
                cuts = edges[1:-1]
                e_counts = np.bincount(np.searchsorted(cuts, r, side="right"), minlength=len(edges) - 1)
                a_counts = np.bincount(np.searchsorted(cuts, c, side="right"), minlength=len(edges) - 1)
                e_shares, a_shares = list(e_counts / len(r)), list(a_counts / max(len(c), 1))
            e_shares = [s * len(r) / len(ref) for s in e_shares]    # shares of ALL rows, so the NaN bin below adds up
            a_shares = [s * len(c) / len(cur) for s in a_shares]
        e_shares.append(float(r_nan.mean()))
        a_shares.append(float(c_nan.mean()))
    else:
        cats = pd.concat([ref.astype(str), cur.astype(str)]).unique()
        e = ref.astype(str).value_counts(normalize=True).reindex(cats).fillna(0.0)
        a = cur.astype(str).value_counts(normalize=True).reindex(cats).fillna(0.0)
        e_shares, a_shares = list(e.to_numpy()), list(a.to_numpy())
    e_arr = np.clip(np.asarray(e_shares, float), eps, None)
    a_arr = np.clip(np.asarray(a_shares, float), eps, None)
    return float(np.sum((a_arr - e_arr) * np.log(a_arr / e_arr)))


def feature_psi(reference: pd.DataFrame, current: pd.DataFrame, features: Optional[Iterable[str]] = None) -> dict:
    """PSI for every feature the two frames share (numeric, boolean or categorical columns; timestamps are skipped)."""
    cols = list(features) if features is not None else [c for c in reference.columns if c in current.columns]
    out = {}
    for c in cols:
        if c not in reference.columns or c not in current.columns:
            continue
        if pd.api.types.is_datetime64_any_dtype(reference[c]) or pd.api.types.is_timedelta64_dtype(reference[c]):
            continue
        out[str(c)] = psi(reference[c], current[c])
    return out


def drift_breaches(psis: Mapping[str, float], *, threshold: float = DRIFT_PSI_ALERT, halts: Optional[bool] = None) -> list:
    """A Breach per feature whose PSI is above the alert line. `halts` defaults to config.DRIFT_PSI_HALTS (False): an alert only."""
    halts = check_halts(CHECK_DRIFT) if halts is None else bool(halts)
    return [Breach(CHECK_DRIFT, f, v, threshold, f"PSI of {f} is {v:.3f}, alert at {threshold}" + ("" if halts else " (alert only)"), halts=halts)
            for f, v in psis.items() if math.isfinite(v) and v > threshold]


def evidently_drift(reference: pd.DataFrame, current: pd.DataFrame) -> Optional[dict]:
    """Optional second opinion from Evidently's per-column drift test. Returns {column: {"method", "value", "threshold",
    "drifted"}}, or None when Evidently is not installed or fails. Evidently picks the test by sample size (a KS p-value for
    small samples, a normalised Wasserstein distance for large ones), so `drifted` is judged by the method: a p-value below its
    threshold, a distance above it. Importing Evidently takes ~10 s, so call this rarely."""
    try:
        from evidently import Report
        from evidently.presets import DataDriftPreset
        cols = [c for c in reference.columns if c in current.columns and pd.api.types.is_numeric_dtype(reference[c])]
        snap = Report([DataDriftPreset()]).run(current[cols], reference[cols])
        out = {}
        for m in snap.dict().get("metrics", []):
            cfg = m.get("config", {})
            if cfg.get("type", "").endswith("ValueDrift") and "column" in cfg:
                method, thr, value = str(cfg.get("method", "")), float(cfg.get("threshold", math.nan)), float(m["value"])
                is_p_value = "p_value" in method or "p-value" in method
                out[str(cfg["column"])] = {"method": method, "value": value, "threshold": thr,
                                           "drifted": value < thr if is_p_value else value > thr}
        return out
    except Exception:
        return None


class FeatureWindow:
    """The newest served feature rows, kept so the drift check has a "current" sample. Thread safe, bounded."""

    def __init__(self, max_rows: int = DRIFT_WINDOW_ROWS):
        self.max_rows = int(max_rows)
        self._frames: list = []
        self._rows = 0
        self._lock = threading.Lock()

    def add(self, df: pd.DataFrame) -> None:
        if df is None or len(df) == 0:
            return
        with self._lock:
            self._frames.append(df.reset_index(drop=True))
            self._rows += len(df)
            while self._rows - len(self._frames[0]) >= self.max_rows and len(self._frames) > 1:
                self._rows -= len(self._frames.pop(0))

    def frame(self) -> Optional[pd.DataFrame]:
        with self._lock:
            if not self._frames:
                return None
            return pd.concat(self._frames, ignore_index=True)

    def __len__(self) -> int:
        return self._rows


# ================================================================================================ calibration
# The halt-eligible model-health check (DESIGN.md "Monitoring policy", lead decision 10 Oct 2026). Output calibration, not input
# drift: over the last CALIBRATION_WINDOW_INTERVALS settled intervals, did the issued P10/P50/P90 behave as quantiles, and does the
# P50 still beat the 7-day profile median? The rules are D1a's `models.cv.calibration_check`; the thresholds are in config.py with
# the evidence that picked them (2.1 to 2.5% false breaches per hub on the 2025-26 holdout, against 42 to 46% for the first guess).
CALIBRATION_MIN_ROWS = 96                       # ASSUMPTION: fewer settled forecast rows than one day of intervals says nothing
CALIBRATION_MIN_COVERAGE = 0.5                  # ASSUMPTION: at least this share of the window's intervals needs a settled forecast, because the
                                                #   false-breach rate was measured on full 7-day windows; a thin window is one regime and noisy


@dataclass
class CalibrationResult:
    """One evaluation of the calibration check over one window of one asset."""
    breach: bool                  # a HALT-ELIGIBLE rule tripped: the hit-rate rule or the skill rule
    enough_data: bool
    n_rows: int                   # settled (forecast, realised price) pairs scored
    n_intervals: int              # distinct settled target intervals among them
    window_intervals: int         # the window asked for (CALIBRATION_WINDOW_INTERVALS)
    hit: dict = field(default_factory=dict)             # share of realised prices at or below the issued P10 / P50 / P90
    hit_error: dict = field(default_factory=dict)       # |hit - 0.1 / 0.5 / 0.9|
    skill_ratio: Optional[float] = None                 # P50 MAE over the 7-day profile median's MAE on the same rows
    skill_checked: bool = False                         # False when the log carried no rt_prof7_med: the skill rule could not run
    pinball_ratio: Optional[float] = None               # window pinball over the model's holdout pinball (alert only)
    pinball_alert: bool = False
    tripped: list = field(default_factory=list)         # e.g. ["hit_p90", "skill"]
    reasons: list = field(default_factory=list)
    pinball_reasons: list = field(default_factory=list)
    thresholds: dict = field(default_factory=dict)

    @property
    def coverage(self) -> float:
        """Share of the window's intervals with at least one settled forecast."""
        return self.n_intervals / self.window_intervals if self.window_intervals else 0.0

    def as_dict(self) -> dict:
        return {"breach": self.breach, "enough_data": self.enough_data, "n_rows": self.n_rows, "n_intervals": self.n_intervals,
                "window_intervals": self.window_intervals, "coverage": round(self.coverage, 4), "hit": self.hit,
                "hit_error": self.hit_error, "skill_ratio": self.skill_ratio, "skill_checked": self.skill_checked,
                "pinball_ratio": self.pinball_ratio, "pinball_alert": self.pinball_alert, "tripped": list(self.tripped),
                "reasons": list(self.reasons), "pinball_reasons": list(self.pinball_reasons), "thresholds": self.thresholds}


def evaluate_calibration(y, q, *, horizon=None, profile_p50=None, reference: Optional[dict] = None, n_intervals: Optional[int] = None,
                         window_intervals: Optional[int] = None, min_rows: int = CALIBRATION_MIN_ROWS, hit_tol=None, skill_max=None,
                         pinball_max=None) -> CalibrationResult:
    """The calibration check over one window.

    `y` realised prices and `q` (n, 3) the issued P10/P50/P90, one row per scored forecast; `profile_p50` the 7-day profile median the
    model logged for the same intervals (feature `rt_prof7_med`); `reference` the model's holdout metrics (`Forecaster.holdout_metrics[sp]`),
    used only by the alert-only pinball rule. Thresholds default to config (CALIBRATION_HIT_TOL, SKILL_DEGRADATION_MAX,
    PINBALL_DEGRADATION_MAX); `False` switches a rule off.

    `breach` is the HALT-ELIGIBLE verdict: the hit-rate rule or the skill rule. The pinball rule is run separately and can only set
    `pinball_alert`: a 7-day pinball is heavy tailed (a spike week costs every forecaster), so it never counts toward a halt."""
    from ..models import cv                       # D1a's rules; imported here because it pulls pandas-heavy modules in
    window_intervals = int(config.CALIBRATION_WINDOW_INTERVALS if window_intervals is None else window_intervals)
    core = cv.calibration_check(y, q, horizon=horizon, profile_p50=profile_p50, hit_tol=hit_tol, skill_max=skill_max, pinball_max=False,
                                min_rows=min_rows)
    res = CalibrationResult(breach=bool(core["breach"]), enough_data=bool(core["enough_data"]), n_rows=int(core["n"]),
                            n_intervals=int(n_intervals if n_intervals is not None else core["n"]), window_intervals=window_intervals,
                            hit=dict(core["hit"]), hit_error=dict(core["hit_error"]), skill_ratio=core["skill_ratio"],
                            skill_checked=profile_p50 is not None and skill_max is not False and core["thresholds"]["skill_max"] is not None,
                            tripped=list(core["tripped"]), reasons=list(core["reasons"]), thresholds=dict(core["thresholds"]))
    if reference and res.enough_data:
        alert = cv.calibration_check(y, q, reference=reference, horizon=horizon, hit_tol=False, skill_max=False, pinball_max=pinball_max,
                                     min_rows=min_rows)
        res.pinball_ratio = alert["pinball_ratio"]
        res.pinball_alert = "pinball" in alert["tripped"]
        res.pinball_reasons = list(alert["reasons"])
        res.thresholds["pinball_max"] = alert["thresholds"]["pinball_max"]
    return res


def calibration_breaches(res: CalibrationResult, subject: str) -> list:
    """Breach objects for one evaluation: one that halts when a halt-eligible rule tripped, one alert-only for the pinball rule."""
    out = []
    if res.enough_data and res.breach:
        hit_tripped = [t for t in res.tripped if t.startswith("hit_")]
        if hit_tripped:
            worst = max(hit_tripped, key=lambda t: res.hit_error[t[4:]])
            value, threshold = res.hit_error[worst[4:]], res.thresholds["hit_tol"][worst[4:]]
        else:
            value, threshold = res.skill_ratio, 1.0 + res.thresholds["skill_max"]
        out.append(Breach(CHECK_CALIBRATION, subject, float(value), float(threshold), "; ".join(res.reasons)[:400], halts=True))
    if res.enough_data and res.pinball_alert:
        out.append(Breach(CHECK_CALIBRATION, f"{subject}:pinball", float(res.pinball_ratio), 1.0 + float(res.thresholds["pinball_max"]),
                          "; ".join(res.pinball_reasons)[:400] + " (alert only)", halts=False))
    return out


# ================================================================================================ capture rate
def capture_rate(paper_revenue_usd: float, pf_revenue_usd: float) -> float:
    """Paper revenue over perfect-foresight revenue for the same window. NaN when perfect foresight earned nothing."""
    if not math.isfinite(pf_revenue_usd) or pf_revenue_usd <= 0:
        return math.nan
    return float(paper_revenue_usd) / float(pf_revenue_usd)


def capture_decay_breach(rate: float, baseline: float, *, tol: float = CAPTURE_DECAY_TOL, subject: str = "fleet") -> Optional[Breach]:
    """Breach when the rolling capture rate is more than `tol` (relative) below the baseline. No data, no breach."""
    if not (math.isfinite(rate) and math.isfinite(baseline)) or baseline <= 0:
        return None
    floor = baseline * (1.0 - tol)
    if rate < floor:
        return Breach(CHECK_CAPTURE, subject, rate, floor,
                      f"capture rate {rate:.1%} is below {floor:.1%} ({tol:.0%} under the baseline {baseline:.1%})")
    return None


# ================================================================================================ slippage / Gate 3
def slippage_ratio(paper_revenue_usd: float, backtest_revenue_usd: float, *, floor_usd: float = SLIPPAGE_DENOM_FLOOR_USD) -> float:
    return abs(float(paper_revenue_usd) - float(backtest_revenue_usd)) / max(abs(float(backtest_revenue_usd)), floor_usd)


def slippage_breach(ratio: float, *, tol: float = SLIPPAGE_TOL, subject: str = "fleet") -> Optional[Breach]:
    if not math.isfinite(ratio):
        return Breach(CHECK_SLIPPAGE, subject, ratio, tol, "slippage could not be computed", severity="error")
    if ratio > tol:
        return Breach(CHECK_SLIPPAGE, subject, ratio, tol, f"paper differs from the backtest by {ratio:.1%}, tolerance {tol:.0%}")
    return None


@dataclass
class Gate3Result:
    passed: bool
    slippage_ratio: float
    paper_revenue_usd: float
    backtest_revenue_usd: float
    tol: float
    n_intervals: int
    replay_problems: list = field(default_factory=list)
    largest_gaps: Optional[pd.DataFrame] = None        # per-interval paper vs backtest, biggest differences first (when the backtest is per interval)
    breach: Optional[Breach] = None


def gate3(ledger, spec, prices: pd.DataFrame, backtest: Union[float, pd.Series, pd.DataFrame], *, start_utc=None,
          end_utc=None, tol: float = SLIPPAGE_TOL) -> Gate3Result:
    """Gate 3: replay the paper ledger over [start, end) at realised `prices` and compare with the backtest.

    `backtest` is the backtest's revenue for the same period as a number, or per interval (a Series, or a DataFrame
    with a `revenue_usd` column) in which case the intervals outside [start, end) are dropped and the biggest
    per-interval gaps are returned for diagnosis.
    """
    rep = ledger.replay(spec, prices, start_utc=start_utc, end_utc=end_utc)
    gaps = None
    if isinstance(backtest, (pd.Series, pd.DataFrame)):
        series = backtest["revenue_usd"] if isinstance(backtest, pd.DataFrame) else backtest
        if start_utc is not None:
            series = series[series.index >= pd.Timestamp(start_utc)]
        if end_utc is not None:
            series = series[series.index < pd.Timestamp(end_utc)]
        both = pd.DataFrame({"backtest_usd": series}).join(rep.frame["revenue_usd"].rename("paper_usd"), how="outer").fillna(0.0)
        both["gap_usd"] = (both["paper_usd"] - both["backtest_usd"]).abs()
        gaps = both.sort_values("gap_usd", ascending=False).head(10)
        backtest_total = float(series.sum())
    else:
        backtest_total = float(backtest)
    ratio = slippage_ratio(rep.revenue_usd, backtest_total)
    breach = slippage_breach(ratio, tol=tol)
    passed = breach is None and rep.ok
    return Gate3Result(passed=passed, slippage_ratio=ratio, paper_revenue_usd=rep.revenue_usd,
                       backtest_revenue_usd=backtest_total, tol=tol, n_intervals=rep.n_intervals,
                       replay_problems=list(rep.problems), largest_gaps=gaps, breach=breach)


def _registry_recorder():
    from ..registry import mlflow_registry          # D1a's module; imported only when a result is recorded
    return mlflow_registry.record_gate3


def record_gate3_result(result: Gate3Result, version, *, audit=None, recorder=None, by: str = "d2:monitor") -> dict:
    """Put a Gate 3 outcome where it is used: on the model version in the registry, as the tags `gate3.pass`, `gate3.utc`,
    `gate3.by` and `gate3.evidence` (D1a's `registry.record_gate3`; promotion to `live` requires `gate3.pass == "true"`), and in
    the audit log. A pass is recorded as true, a fail as false. Never raises: a registry that cannot be written is reported in
    the returned dict (`recorded` False, `error`) and in the audit record."""
    evidence = (f"slippage {result.slippage_ratio:.4f} (tolerance {result.tol}) over {result.n_intervals} intervals; paper "
                f"${result.paper_revenue_usd:,.2f} vs backtest ${result.backtest_revenue_usd:,.2f}; "
                f"{len(result.replay_problems)} ledger replay problems")
    out = {"version": str(version), "passed": bool(result.passed), "recorded": False, "error": "", "evidence": evidence}
    try:
        (recorder or _registry_recorder())(str(version), bool(result.passed), evidence=evidence, by=by)
        out["recorded"] = True
    except Exception as exc:
        out["error"] = f"{type(exc).__name__}: {exc}"[:300]
    if audit is not None:
        try:
            audit.append({"type": "gate3", "version": out["version"], "passed": out["passed"], "recorded": out["recorded"],
                          "error": out["error"], "evidence": evidence, "slippage_ratio": result.slippage_ratio,
                          "tolerance": result.tol, "paper_revenue_usd": result.paper_revenue_usd,
                          "backtest_revenue_usd": result.backtest_revenue_usd, "replay_problems": result.replay_problems[:10]})
        except Exception as exc:
            out["error"] = (out["error"] + f" audit: {type(exc).__name__}: {exc}").strip()[:300]
    return out
