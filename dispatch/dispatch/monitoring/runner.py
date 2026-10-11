"""Runs the FR11 checks on a schedule and hands the result to the halt policy.

`MonitorRunner.run_once()` is one monitoring pass: for each check whose inputs are available it computes the breaches,
updates the Prometheus gauges, and then calls `HaltPolicy.evaluate` so that repeated breaches halt dispatch.

Inputs are providers, not hard dependencies, because they come from modules owned by other agents:
  freshness            data.catalog.freshness(now_utc) -> {report_id: age_s}                       (D0)
  feature_window       the rows the cycle served (service/cycle.py adds them)                       (this package)
  reference_provider   () -> the reference frame stored with the model                              (D1a: Forecaster.reference_frame)
  capture_provider     (now) -> CaptureInputs(paper_usd, pf_usd, baseline)                          (needs realised prices + optimizer)
  slippage_provider    (now) -> SlippageInputs(paper_usd, backtest_usd)                             (needs realised prices + a backtest)
  calibration_provider (now) -> CalibrationOutcome | NoEvaluation    (monitoring/calibration.py: once a day, from the forecast log)
A check whose provider is missing, or whose provider returns None, is "skipped": no breach, no reset of its count. A check
that raises is an "error" breach, because a check that cannot run is a doubt, and doubt means no dispatch (unless it is an
alert-only check: feature PSI cannot halt, so a failing PSI check cannot either).

Status of a check in a pass: "ok", "breach" (a halt-eligible breach: it counts toward the halt), "alert" (only alert-only breaches: shown
and recorded, never counted), "skipped: why", or "error: what".
"""
from __future__ import annotations

import logging
import math
import threading
from dataclasses import dataclass, field
from typing import Callable, Mapping, Optional

import pandas as pd

from ..config import DRIFT_PSI_ALERT, SLIPPAGE_TOL
from . import checks as C
from .calibration import CalibrationOutcome, NoEvaluation
from .halt_policy import HaltPolicy, PolicyOutcome
from .metrics import Metrics

log = logging.getLogger("dispatch.monitoring")


@dataclass(frozen=True)
class CaptureInputs:
    paper_revenue_usd: float
    pf_revenue_usd: float
    baseline: float


@dataclass(frozen=True)
class SlippageInputs:
    paper_revenue_usd: float
    backtest_revenue_usd: float


@dataclass
class MonitorReport:
    ran_utc: pd.Timestamp
    status: dict = field(default_factory=dict)         # check -> "ok" | "breach" | "skipped: <why>" | "error: <what>"
    breaches: list = field(default_factory=list)
    outcome: Optional[PolicyOutcome] = None
    extra: dict = field(default_factory=dict)          # e.g. Evidently p-values, the capture rate


class MonitorRunner:
    def __init__(self, *, policy: HaltPolicy, metrics: Optional[Metrics] = None,
                 clock: Callable[[], pd.Timestamp] = lambda: pd.Timestamp.now(tz="UTC"),
                 freshness: Optional[Callable[[pd.Timestamp], Mapping]] = None,
                 feature_window: Optional[C.FeatureWindow] = None,
                 reference_provider: Optional[Callable[[], Optional[pd.DataFrame]]] = None,
                 capture_provider: Optional[Callable[[pd.Timestamp], Optional[CaptureInputs]]] = None,
                 slippage_provider: Optional[Callable[[pd.Timestamp], Optional[SlippageInputs]]] = None,
                 calibration_provider: Optional[Callable[[pd.Timestamp], "CalibrationOutcome | NoEvaluation | None"]] = None,
                 use_evidently: bool = False, drift_threshold: float = DRIFT_PSI_ALERT,
                 capture_tol: float = C.CAPTURE_DECAY_TOL, slippage_tol: float = SLIPPAGE_TOL):
        self.policy, self.metrics, self._clock = policy, metrics, clock
        self.freshness, self.feature_window, self.reference_provider = freshness, feature_window, reference_provider
        self.capture_provider, self.slippage_provider, self.calibration_provider = capture_provider, slippage_provider, calibration_provider
        self.use_evidently, self.drift_threshold = use_evidently, drift_threshold
        self.capture_tol, self.slippage_tol = capture_tol, slippage_tol
        self.last_report: Optional[MonitorReport] = None
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()

    # -- one pass
    def run_once(self, now_utc: Optional[pd.Timestamp] = None) -> MonitorReport:
        with self._lock:
            now = pd.Timestamp(now_utc) if now_utc is not None else self._clock()
            rep = MonitorReport(ran_utc=now)
            for check, fn in ((C.CHECK_STALE, self._stale), (C.CHECK_DRIFT, self._drift),
                              (C.CHECK_CAPTURE, self._capture), (C.CHECK_SLIPPAGE, self._slippage),
                              (C.CHECK_CALIBRATION, self._calibration)):
                try:
                    status, found = fn(now, rep)
                except Exception as exc:                    # a check that cannot run is a doubt (but only a check that can halt can doubt)
                    log.warning("monitor check %s failed: %s", check, exc)
                    status = f"error: {type(exc).__name__}: {exc}"[:200]
                    found = [C.Breach(check, "monitor", math.nan, 0.0, f"the {check} check failed: {type(exc).__name__}: {exc}"[:300],
                                      severity="error", halts=C.check_halts(check))]
                rep.status[check] = status
                rep.breaches.extend(found)
            ran = [c for c, s in rep.status.items() if not s.startswith("skipped")]
            rep.outcome = self.policy.evaluate(rep.breaches, ran)
            self.last_report = rep
            return rep

    def _stale(self, now, rep):
        if self.freshness is None:
            return "skipped: no freshness provider", []
        ages = {str(k): v for k, v in dict(self.freshness(now)).items()}
        monitored = {k: v for k, v in ages.items() if k in C.MONITORED_REPORTS}
        allow = C.allowances_for(monitored)
        if self.metrics is not None:
            self.metrics.set_data_age({k: v for k, v in monitored.items() if _num(v)}, allow)
        found = C.stale_data_breaches(monitored, allowances=allow)
        return _status(found), found

    def _drift(self, now, rep):
        if self.reference_provider is None or self.feature_window is None:
            return "skipped: no reference frame or feature window", []
        ref, cur = self.reference_provider(), self.feature_window.frame()
        if ref is None:
            return "skipped: the loaded model has no reference frame", []
        if cur is None or len(cur) < C.DRIFT_MIN_ROWS:
            return f"skipped: only {0 if cur is None else len(cur)} served rows (need {C.DRIFT_MIN_ROWS})", []
        psis = C.feature_psi(ref, cur)
        if self.metrics is not None:
            self.metrics.set_drift(psis)
        if self.use_evidently:
            rep.extra["evidently_p_values"] = C.evidently_drift(ref, cur)
        found = C.drift_breaches(psis, threshold=self.drift_threshold)
        return _status(found), found

    def _capture(self, now, rep):
        if self.capture_provider is None:
            return "skipped: no capture provider", []
        inp = self.capture_provider(now)
        if inp is None:
            return "skipped: no capture data for the window", []
        rate = C.capture_rate(inp.paper_revenue_usd, inp.pf_revenue_usd)
        rep.extra["capture_rate"] = rate
        if self.metrics is not None:
            self.metrics.set_capture(rate, inp.baseline)
        b = C.capture_decay_breach(rate, inp.baseline, tol=self.capture_tol)
        return ("breach" if b else "ok"), ([b] if b else [])

    def _slippage(self, now, rep):
        if self.slippage_provider is None:
            return "skipped: no slippage provider", []
        inp = self.slippage_provider(now)
        if inp is None:
            return "skipped: no replayed period to compare", []
        ratio = C.slippage_ratio(inp.paper_revenue_usd, inp.backtest_revenue_usd)
        rep.extra["slippage_ratio"] = ratio
        if self.metrics is not None and math.isfinite(ratio):
            self.metrics.set_slippage(ratio)
        b = C.slippage_breach(ratio, tol=self.slippage_tol)
        return ("breach" if b else "ok"), ([b] if b else [])

    def _calibration(self, now, rep):
        if self.calibration_provider is None:
            return "skipped: no calibration provider", []
        out = self.calibration_provider(now)
        if out is None:
            return "skipped: no calibration data", []
        if isinstance(out, NoEvaluation):
            return f"skipped: {out.reason}", []
        rep.extra["calibration"] = out.as_dict()
        if self.metrics is not None:
            for a in out.assets:
                r = a.result
                self.metrics.set_calibration(a.asset_id, hit_error=r.hit_error, skill_ratio=r.skill_ratio, pinball_ratio=r.pinball_ratio,
                                             breach=r.breach, intervals=r.n_intervals)
            self.metrics.mark_calibration_evaluated(out.evaluated_utc.timestamp())
        found = list(out.breaches)
        if not any(b.halts for b in found):
            return ("alert" if found else "ok"), found
        return "breach", found

    # -- scheduling
    def start(self, interval_s: float) -> None:
        if interval_s <= 0 or (self._thread is not None and self._thread.is_alive()):
            return
        self._stop.clear()

        def loop():
            while not self._stop.wait(interval_s):
                try:
                    self.run_once()
                except Exception:                           # the monitor never dies
                    log.exception("monitoring pass failed")

        self._thread = threading.Thread(target=loop, name="dispatch-monitor", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5)
            self._thread = None


def _status(found: list) -> str:
    """"breach" if any breach can halt, "alert" if there are only alert-only ones, "ok" if none."""
    if any(b.halts for b in found):
        return "breach"
    return "alert" if found else "ok"


def _num(v) -> bool:
    try:
        return math.isfinite(float(v))
    except (TypeError, ValueError):
        return False
