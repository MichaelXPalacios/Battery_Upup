"""The daily calibration check, end to end: forecast log + realised prices -> window -> D1a's rules -> breaches.

    CalibrationMonitor(forecast_log, specs, price_source, forecaster=lambda: model)(now_utc)  ->  CalibrationOutcome | NoEvaluation

DESIGN.md "Monitoring policy": once a day (`config.CALIBRATION_EVAL_EVERY_S`), over the last `config.CALIBRATION_WINDOW_INTERVALS` SETTLED
intervals, join the forecasts the cycles logged with the prices that were realised, and ask `models.cv.calibration_check`:

    hit rate      is the share of realised prices at or below the issued P10 / P50 / P90 within `CALIBRATION_HIT_TOL` of 0.1 / 0.5 / 0.9?
    skill         is the P50's mean absolute error within `SKILL_DEGRADATION_MAX` of the 7-day profile median's on the same intervals?
    pinball       (alert only) is the pinball loss within `PINBALL_DEGRADATION_MAX` of what the model scored on its holdout?

A breach of the first two is an alert plus a retrain request (recorded, nothing trains); `CALIBRATION_HALT_AFTER` consecutive daily
breaches set the halt flag (`monitoring/halt_policy.py`). The pinball rule never counts toward a halt.

What is scored. Every logged (forecast, target interval) pair whose interval is settled: a target interval is covered by up to 96
forecasts, one per horizon, exactly like a row of the holdout table the false-breach rate was measured on. Only forecasts made by the
model version that is loaded now: after a retrain and a restart the evidence starts afresh, because last week's forecasts say nothing
about a new model. A window with fewer than `CALIBRATION_MIN_COVERAGE` of its intervals settled is not evaluated (no data is not a
breach and not a pass; the count of consecutive breaches neither grows nor resets).

Realised prices come from D0's `data.prices.price_source`, which is complete-or-nothing for a window, so the window is read one UTC day
at a time and a day with a gap in the lake is left out instead of losing the week.

State. The date of the last evaluation and the streak of consecutive breaches are kept in `calibration-state.json` next to the forecast
log, so a restart neither forgets a breach nor evaluates twice in a day (two restarts must not make "two consecutive daily breaches"),
and every evaluation is appended to `calibration-evaluations.jsonl` for the operator.
"""
from __future__ import annotations

import json
import logging
import math
import os
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional, Sequence

import numpy as np
import pandas as pd

from .. import config
from . import checks as C
from .forecast_log import ANCHOR_COLUMN, STEP, ForecastLog

log = logging.getLogger("dispatch.monitoring")

PRICE_LAG = pd.Timedelta(hours=1)               # ASSUMPTION: a price is final this long after its interval (same as monitoring/providers.py)
RETRY_AFTER_NO_DATA_S = 3600.0                  # ASSUMPTION: after an attempt with too little settled data, look again in an hour, not every pass
STATE_FILE, EVALUATIONS_FILE = "calibration-state.json", "calibration-evaluations.jsonl"
RT = "rt_usd_per_mwh"


@dataclass
class NoEvaluation:
    """The check did not run this pass: why. Not a breach, and it does not reset the streak either."""
    reason: str


@dataclass
class AssetCalibration:
    asset_id: str
    settlement_point: str
    model_version: str
    window_start: pd.Timestamp
    window_end: pd.Timestamp
    result: C.CalibrationResult


@dataclass
class CalibrationOutcome:
    evaluated_utc: pd.Timestamp
    model_version: str
    assets: list = field(default_factory=list)          # AssetCalibration, one per asset that had enough settled forecasts
    skipped: dict = field(default_factory=dict)         # asset_id -> why it was not evaluated
    breaches: list = field(default_factory=list)        # checks.Breach: halt-eligible ones, and alert-only ones (pinball)
    error: str = ""                                     # the evaluation itself failed; that is a doubt and counts as one breach for the day

    @property
    def breach(self) -> bool:
        """A HALT-ELIGIBLE rule tripped for some asset."""
        return any(b.halts for b in self.breaches)

    def as_dict(self) -> dict:
        return {"evaluated_utc": self.evaluated_utc, "model_version": self.model_version, "breach": self.breach, "error": self.error,
                "assets": {a.asset_id: {"window_start": a.window_start, "window_end": a.window_end, **a.result.as_dict()} for a in self.assets},
                "skipped": dict(self.skipped)}


class CalibrationMonitor:
    """Callable `provider(now_utc)` for `MonitorRunner(calibration_provider=...)`."""

    def __init__(self, forecast_log: ForecastLog, specs: Sequence, price_source: Callable, *, forecaster: Callable[[], object] = lambda: None,
                 directory: Optional[Path] = None, window_intervals: Optional[int] = None, every_s: Optional[float] = None,
                 retry_s: float = RETRY_AFTER_NO_DATA_S, lag: pd.Timedelta = PRICE_LAG, min_coverage: float = C.CALIBRATION_MIN_COVERAGE,
                 min_rows: int = C.CALIBRATION_MIN_ROWS, hit_tol=None, skill_max=None, pinball_max=None):
        self.log, self.specs, self.price_source, self.forecaster = forecast_log, list(specs), price_source, forecaster
        self.dir = Path(directory) if directory is not None else forecast_log.dir
        self.dir.mkdir(parents=True, exist_ok=True)
        self.window_intervals = int(config.CALIBRATION_WINDOW_INTERVALS if window_intervals is None else window_intervals)
        self.every_s = float(config.CALIBRATION_EVAL_EVERY_S if every_s is None else every_s)
        self.retry_s, self.lag, self.min_coverage, self.min_rows = float(retry_s), lag, float(min_coverage), int(min_rows)
        self.rules = dict(hit_tol=hit_tol, skill_max=skill_max, pinball_max=pinball_max)
        self._lock = threading.Lock()
        self.state = self._load_state()
        self.last_outcome: Optional[CalibrationOutcome] = None
        self.last_skip: str = ""

    # ---- persistence
    @property
    def state_path(self) -> Path:
        return self.dir / STATE_FILE

    def _load_state(self) -> dict:
        try:
            st = json.loads(self.state_path.read_text(encoding="utf-8"))
            return {"last_eval_utc": st.get("last_eval_utc"), "last_attempt_utc": st.get("last_attempt_utc"),
                    "model_version": st.get("model_version"), "streak": int(st.get("streak", 0))}
        except (OSError, ValueError):
            return {"last_eval_utc": None, "last_attempt_utc": None, "model_version": None, "streak": 0}

    def _save_state(self) -> None:
        tmp = self.state_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.state, sort_keys=True), encoding="utf-8")
        os.replace(tmp, self.state_path)

    @property
    def streak(self) -> int:
        """Consecutive daily breaches as of the last evaluation (what the halt policy restores after a restart)."""
        return int(self.state.get("streak", 0))

    def _append_evaluation(self, out: CalibrationOutcome) -> None:
        try:
            with open(self.dir / EVALUATIONS_FILE, "ab") as f:
                f.write((json.dumps(_plain(out.as_dict()), separators=(",", ":"), allow_nan=False) + "\n").encode("utf-8"))
        except Exception as exc:                      # a history file is for the operator; it must not break the check
            log.warning("calibration evaluation history not written: %s", exc)

    def sync_model(self, version: str) -> int:
        """Tell the monitor which model version is serving. A different version than the one the saved state was made under starts with a
        clean slate (no streak, no schedule): last week's breaches say nothing about a new model. Returns the streak to restore in the
        halt policy."""
        version = str(version)
        if self.state.get("model_version") not in (None, version):
            self.state.update(last_eval_utc=None, last_attempt_utc=None, streak=0, tried_reason="")
        if self.state.get("model_version") != version:
            self.state["model_version"] = version
            self._save_state()
        return self.streak

    # ---- the evaluation
    def due(self, now: pd.Timestamp) -> Optional[str]:
        """None if an evaluation is due now, else why not."""
        last, tried = self.state.get("last_eval_utc"), self.state.get("last_attempt_utc")
        if last is not None:
            wait = self.every_s - (now - pd.Timestamp(last)).total_seconds()
            if wait > 0:
                return f"next daily evaluation in {wait / 3600:.1f} h (last {pd.Timestamp(last):%Y-%m-%d %H:%M}Z)"
        if tried is not None:
            wait = self.retry_s - (now - pd.Timestamp(tried)).total_seconds()
            if wait > 0 and self.state.get("tried_reason"):
                return f"{self.state['tried_reason']}; looking again in {wait / 60:.0f} min"
        return None

    def __call__(self, now_utc) -> "CalibrationOutcome | NoEvaluation":
        now = pd.Timestamp(now_utc)
        now = now.tz_localize("UTC") if now.tzinfo is None else now.tz_convert("UTC")
        with self._lock:
            fc = self.forecaster()
            if fc is None:
                return self._skip("no model is loaded")
            version = str(getattr(fc, "version", "") or "")
            self.sync_model(version)
            wait = self.due(now)
            if wait is not None:
                return self._skip(wait)
            try:
                out = self._evaluate(now, fc, version)
            except Exception as exc:                  # a check that cannot run is a doubt, and a daily check doubts once a day, not once a pass
                log.warning("calibration evaluation failed: %s: %s", type(exc).__name__, exc)
                msg = f"{type(exc).__name__}: {exc}"[:300]
                out = CalibrationOutcome(evaluated_utc=now, model_version=version, error=msg,
                                         breaches=[C.Breach(C.CHECK_CALIBRATION, "monitor", math.nan, 0.0,
                                                            f"the calibration evaluation failed: {msg}", severity="error", halts=True)])
            if isinstance(out, NoEvaluation):
                self.state.update(last_attempt_utc=now.isoformat(), tried_reason=out.reason)
                self._save_state()
                self.last_skip = out.reason
                return out
            self.state.update(last_eval_utc=now.isoformat(), last_attempt_utc=now.isoformat(), tried_reason="",
                              streak=self.streak + 1 if out.breach else 0)
            self._save_state()
            self._append_evaluation(out)
            self.last_outcome, self.last_skip = out, ""
            return out

    def _skip(self, reason: str) -> NoEvaluation:
        self.last_skip = reason
        return NoEvaluation(reason)

    def _evaluate(self, now: pd.Timestamp, fc, version: str) -> "CalibrationOutcome | NoEvaluation":
        end = (now - self.lag).floor("15min")
        start = end - self.window_intervals * STEP
        out = CalibrationOutcome(evaluated_utc=now, model_version=version)
        for spec in self.specs:
            frame = self.log.frame(start, end, asset_id=spec.asset_id, model_version=version)
            if frame.empty:
                out.skipped[spec.asset_id] = f"no forecasts of model {version} are logged for [{start:%m-%d %H:%M}, {end:%m-%d %H:%M})"
                continue
            y = self.realised(spec, start, end)
            frame["interval_start_utc"] = pd.DatetimeIndex(frame["interval_start_utc"]).as_unit("ns")      # one resolution on both sides of the join
            frame = frame.assign(y=frame["interval_start_utc"].map(y)).dropna(subset=["y", "rt_p10", "rt_p50", "rt_p90"])
            n_int = int(frame["interval_start_utc"].nunique())
            if len(frame) < self.min_rows or n_int < self.min_coverage * self.window_intervals:
                out.skipped[spec.asset_id] = (f"only {n_int} of the window's {self.window_intervals} intervals have a settled forecast of model "
                                              f"{version} ({len(frame)} rows); at least {self.min_coverage:.0%} of the window is needed")
                continue
            prof = frame[ANCHOR_COLUMN].to_numpy("float64")
            prof = prof if np.isfinite(prof).any() else None
            res = C.evaluate_calibration(frame["y"].to_numpy(), frame[["rt_p10", "rt_p50", "rt_p90"]].to_numpy(), horizon=frame["horizon"].to_numpy(),
                                         profile_p50=prof, reference=_reference(fc, spec.settlement_point), n_intervals=n_int,
                                         window_intervals=self.window_intervals, min_rows=self.min_rows, **self.rules)
            out.assets.append(AssetCalibration(spec.asset_id, spec.settlement_point, version, start, end, res))
            out.breaches.extend(C.calibration_breaches(res, spec.asset_id))
        if not out.assets:
            return NoEvaluation("; ".join(f"{k}: {v}" for k, v in out.skipped.items()) or "no assets")
        return out

    def realised(self, spec, start: pd.Timestamp, end: pd.Timestamp) -> pd.Series:
        """Realised RT prices for [start, end), one UTC day at a time. A day the lake cannot price completely is left out (LookupError,
        D0's `PricesNotPublished`); any other failure is a failure of the check and propagates."""
        parts = []
        day = start.floor("D")
        while day < end:
            c0, c1 = max(day, start), min(day + pd.Timedelta(days=1), end)
            if c1 > c0:
                try:
                    p = self.price_source(spec, c0, c1)
                    parts.append(p[RT])
                except LookupError as exc:
                    log.info("calibration: no realised prices for %s [%s, %s): %s", spec.asset_id, c0, c1, str(exc)[:160])
            day += pd.Timedelta(days=1)
        if not parts:
            return pd.Series(dtype="float64")
        s = pd.concat(parts)
        s.index = pd.DatetimeIndex(s.index).tz_convert("UTC").as_unit("ns")
        return s[~s.index.duplicated(keep="last")]


def _reference(fc, sp: str) -> Optional[dict]:
    try:
        hm = getattr(fc, "holdout_metrics", None)
        return (hm or {}).get(sp) or None
    except Exception:
        return None


def _plain(obj):
    """JSON-safe copy: timestamps as ISO text, NaN as null."""
    if isinstance(obj, dict):
        return {str(k): _plain(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_plain(v) for v in obj]
    if isinstance(obj, pd.Timestamp):
        return obj.isoformat()
    if isinstance(obj, (np.floating, float)):
        return None if not math.isfinite(float(obj)) else float(obj)
    if isinstance(obj, (np.integer,)):
        return int(obj)
    if isinstance(obj, (np.bool_,)):
        return bool(obj)
    return obj


# ================================================================================================ replay over a holdout table
def replay_windows(table: pd.DataFrame, *, reference: Optional[dict] = None, every: str = "1D", window: Optional[pd.Timedelta] = None,
                   first: Optional[pd.Timestamp] = None, profile_col: str = "profile_median_7d_p50", hit_tol=None, skill_max=None,
                   pinball_max=None, min_share: float = 0.8) -> pd.DataFrame:
    """Run the calibration check the way the monitor does, over a table of forecasts for a period nobody was watching, one evaluation per
    `every`, each over the `window` of target intervals that ended at that evaluation. On a period the model never saw, a breach is a
    false breach, so `breach.mean()` is the false-breach rate.

    `table` is D1a's holdout table (`registry.holdout_forecasts(version, sp)`): index = target interval start, columns `y`, `rt_p10`,
    `rt_p50`, `rt_p90`, `horizon` and `profile_col`. Windows with fewer than `min_share` of the median row count are left out (the first
    and last days of a table). Returns one row per evaluation: eval_utc, breach, tripped, n_rows, hit_p10/50/90, skill_ratio, pinball_ratio,
    pinball_alert."""
    window = pd.Timedelta(days=7) if window is None else pd.Timedelta(window)
    t = table.sort_index()
    idx = pd.DatetimeIndex(t.index)
    y, q = t["y"].to_numpy(), t[["rt_p10", "rt_p50", "rt_p90"]].to_numpy()
    hz = t["horizon"].to_numpy() if "horizon" in t.columns else None
    prof = t[profile_col].to_numpy() if profile_col in t.columns else None
    step = pd.Timedelta(every)
    ends = pd.date_range((first if first is not None else idx.min().ceil("D") + window), idx.max() + step, freq=step)
    spans = [(int(idx.searchsorted(e - window, "left")), int(idx.searchsorted(e, "left"))) for e in ends]
    sizes = np.array([b - a for a, b in spans])
    keep = sizes >= min_share * np.median(sizes)
    rows = []
    for e, (a, b), k in zip(ends, spans, keep):
        if not k:
            continue
        r = C.evaluate_calibration(y[a:b], q[a:b], horizon=None if hz is None else hz[a:b], profile_p50=None if prof is None else prof[a:b],
                                   reference=reference, n_intervals=int(pd.Index(idx[a:b]).nunique()),
                                   window_intervals=int(window / STEP), hit_tol=hit_tol, skill_max=skill_max, pinball_max=pinball_max)
        rows.append({"eval_utc": e, "breach": r.breach, "tripped": ",".join(r.tripped), "n_rows": r.n_rows,
                     "hit_p10": r.hit.get("p10"), "hit_p50": r.hit.get("p50"), "hit_p90": r.hit.get("p90"),
                     "skill_ratio": r.skill_ratio, "pinball_ratio": r.pinball_ratio, "pinball_alert": r.pinball_alert})
    return pd.DataFrame(rows)


def halt_episodes(breach_flags: Sequence[bool], halt_after: Optional[int] = None) -> int:
    """How many separate halts a run of daily evaluations would have caused: the number of runs of consecutive breaches that reach
    `halt_after` (default config.CALIBRATION_HALT_AFTER). A single breach followed by a clean day is only an alert and a retrain
    request. (A run that is still going after an operator resumes would halt again at once; it is counted once, as one episode.)"""
    n = int(config.CALIBRATION_HALT_AFTER if halt_after is None else halt_after)
    runs, streak = 0, 0
    for flag in list(breach_flags) + [False]:
        if flag:
            streak += 1
        else:
            runs += 1 if streak >= n else 0
            streak = 0
    return runs
