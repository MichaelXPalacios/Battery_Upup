"""Prometheus metrics for the dispatch service (PRD FR11, and the Grafana dashboard / alert rules in ops/).

One `Metrics` object owns one `CollectorRegistry`, so tests (and a service restart in the same process) never hit
"duplicated timeseries". The names below are a contract with ops/alerts.yml and ops/grafana/dashboards/dispatch.json;
tests/test_monitoring.py parses both files and fails if either references a metric that is not defined here.

Required by the task
  dispatch_cycle_seconds            histogram   wall time of one POST /signal cycle
  dispatch_cycles_total{status}     counter     dispatched | rejected | halted | stale | error
  dispatch_guard_rejections_total{reason}  counter   one increment per reason code on a rejected setpoint
  dispatch_halted                   gauge       1 while the halt flag is set (or Redis cannot be read)
  dispatch_data_age_seconds{report} gauge       from data.catalog.freshness
  dispatch_feature_drift_psi{feature} gauge     population stability index against the model's reference frame
  dispatch_capture_rate             gauge       rolling paper revenue / perfect-foresight revenue
  dispatch_slippage_ratio           gauge       |paper - backtest| / backtest over the replayed period (Gate 3)
  dispatch_model_info{version,alias} gauge      1 for the loaded model

Added because the alerts need them
  dispatch_data_age_allowance_seconds{report}  the age at which a report counts as stale (cadence + slack)
  dispatch_capture_baseline          the capture rate the decay check compares against
  dispatch_feature_age_seconds{asset}   age of the features the last cycle used
  dispatch_monitor_breaches_total{check}   breaches seen, by check
  dispatch_monitor_consecutive_breaches{check}   the count the auto-halt policy keeps (3 halts)
  dispatch_audit_errors_total        audit appends that failed
  dispatch_redis_writable            1 if Redis accepts writes; 0 means a kill set with redis-cli would fail (POST /kill still works)

Added for the calibration check (DESIGN.md "Monitoring policy") and the forecast log
  dispatch_monitor_halt_threshold{check}          consecutive breaching passes that halt that check (3; calibration 2)
  dispatch_calibration_hit_error{asset,quantile}  |share of realised prices at or below the issued P10/P50/P90 - 0.1/0.5/0.9|, last evaluation
  dispatch_calibration_skill_ratio{asset}         P50 MAE over the 7-day profile median's MAE on the same intervals (breach above 1.10)
  dispatch_calibration_pinball_ratio{asset}       pinball over the model's holdout pinball (alert only above 1.25)
  dispatch_calibration_breach{asset}              1 if the last evaluation breached a halt-eligible rule
  dispatch_calibration_window_intervals{asset}    settled intervals the last evaluation scored
  dispatch_calibration_last_evaluation_timestamp_seconds   unix time of the last evaluation (the start of the service until the first)
  dispatch_forecast_log_errors_total              forecast-log writes that failed (the calibration check starves without the log)
"""
from __future__ import annotations

import math
import time
from typing import Iterable, Mapping, Optional

from prometheus_client import CONTENT_TYPE_LATEST, CollectorRegistry, Counter, Gauge, Histogram, generate_latest

from ..guard.guard import REASON_CODES

CYCLE_BUCKETS = (0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 20.0, 30.0, 45.0, 60.0, 120.0)   # 30 s is the PRD target
CYCLE_STATUSES = ("dispatched", "rejected", "halted", "stale", "error")
MONITOR_CHECKS = ("stale_data", "feature_drift", "capture_decay", "slippage", "calibration")
METRIC_NAMES = (
    "dispatch_cycle_seconds", "dispatch_cycles_total", "dispatch_guard_rejections_total", "dispatch_halted",
    "dispatch_data_age_seconds", "dispatch_feature_drift_psi", "dispatch_capture_rate", "dispatch_slippage_ratio",
    "dispatch_model_info", "dispatch_data_age_allowance_seconds", "dispatch_capture_baseline",
    "dispatch_feature_age_seconds", "dispatch_monitor_breaches_total", "dispatch_monitor_consecutive_breaches",
    "dispatch_audit_errors_total", "dispatch_redis_writable", "dispatch_monitor_halt_threshold", "dispatch_calibration_hit_error",
    "dispatch_calibration_skill_ratio", "dispatch_calibration_pinball_ratio", "dispatch_calibration_breach",
    "dispatch_calibration_window_intervals", "dispatch_calibration_last_evaluation_timestamp_seconds",
    "dispatch_forecast_log_errors_total",
)


class Metrics:
    def __init__(self, registry: Optional[CollectorRegistry] = None):
        self.registry = registry or CollectorRegistry()
        r = self.registry
        self.cycle_seconds = Histogram("dispatch_cycle_seconds", "Wall time of one dispatch cycle (POST /signal).",
                                       buckets=CYCLE_BUCKETS, registry=r)
        self.cycles_total = Counter("dispatch_cycles_total", "Dispatch cycles by outcome.", ["status"], registry=r)
        self.guard_rejections_total = Counter("dispatch_guard_rejections_total",
                                              "Guard rejections, one per reason code.", ["reason"], registry=r)
        self.halted = Gauge("dispatch_halted", "1 while dispatch is halted (halt flag set or Redis unreachable).", registry=r)
        self.data_age_seconds = Gauge("dispatch_data_age_seconds", "Seconds since the newest file of a report.",
                                      ["report"], registry=r)
        self.data_age_allowance_seconds = Gauge("dispatch_data_age_allowance_seconds",
                                                "Age at which a report is considered stale.", ["report"], registry=r)
        self.feature_drift_psi = Gauge("dispatch_feature_drift_psi", "PSI of a served feature against the model's reference.",
                                       ["feature"], registry=r)
        self.capture_rate = Gauge("dispatch_capture_rate", "Rolling paper revenue / perfect-foresight revenue.", registry=r)
        self.capture_baseline = Gauge("dispatch_capture_baseline", "Baseline capture rate for the decay check.", registry=r)
        self.slippage_ratio = Gauge("dispatch_slippage_ratio", "|paper - backtest| / backtest revenue (Gate 3).", registry=r)
        self.model_info = Gauge("dispatch_model_info", "The model the service dispatches with.", ["version", "alias"], registry=r)
        self.feature_age_seconds = Gauge("dispatch_feature_age_seconds", "Age of the features the last cycle used.",
                                         ["asset"], registry=r)
        self.monitor_breaches_total = Counter("dispatch_monitor_breaches_total", "Monitoring breaches by check.",
                                              ["check"], registry=r)
        self.monitor_consecutive = Gauge("dispatch_monitor_consecutive_breaches",
                                         "Consecutive breaching evaluations per check (HALT_AFTER_BREACHES halts).",
                                         ["check"], registry=r)
        self.audit_errors_total = Counter("dispatch_audit_errors_total", "Audit appends that failed.", registry=r)
        self.redis_writable = Gauge("dispatch_redis_writable", "1 if Redis accepts writes (a full Redis refuses a kill set by hand).",
                                    registry=r)
        self.monitor_halt_threshold = Gauge("dispatch_monitor_halt_threshold",
                                            "Consecutive breaching evaluations of a check that set the halt flag.", ["check"], registry=r)
        self.calibration_hit_error = Gauge("dispatch_calibration_hit_error",
                                           "|hit rate below the issued quantile - its level|, last calibration evaluation.",
                                           ["asset", "quantile"], registry=r)
        self.calibration_skill_ratio = Gauge("dispatch_calibration_skill_ratio",
                                             "P50 MAE over the 7-day profile median's MAE on the same intervals, last evaluation.",
                                             ["asset"], registry=r)
        self.calibration_pinball_ratio = Gauge("dispatch_calibration_pinball_ratio",
                                               "Window pinball over the model's holdout pinball (alert only), last evaluation.",
                                               ["asset"], registry=r)
        self.calibration_breach = Gauge("dispatch_calibration_breach", "1 if the last calibration evaluation breached a halt-eligible rule.",
                                        ["asset"], registry=r)
        self.calibration_window_intervals = Gauge("dispatch_calibration_window_intervals",
                                                  "Settled intervals the last calibration evaluation scored.", ["asset"], registry=r)
        self.calibration_last_evaluation = Gauge("dispatch_calibration_last_evaluation_timestamp_seconds",
                                                 "Unix time of the last calibration evaluation (service start until the first).", registry=r)
        self.forecast_log_errors_total = Counter("dispatch_forecast_log_errors_total", "Forecast-log writes that failed.", registry=r)
        # series that must exist from the first scrape, so rate() and absent() alerts behave
        for s in CYCLE_STATUSES:
            self.cycles_total.labels(s)
        for reason in REASON_CODES:
            self.guard_rejections_total.labels(reason)
        for c in MONITOR_CHECKS:
            self.monitor_breaches_total.labels(c)
            self.monitor_consecutive.labels(c).set(0)
        self.halted.set(0)
        self.redis_writable.set(1)
        self.calibration_last_evaluation.set(time.time())
        for g in (self.capture_rate, self.capture_baseline, self.slippage_ratio):
            g.set(math.nan)                       # unknown until a check has run; NaN fails every alert comparison

    # -- cycle
    def observe_cycle(self, status: str, latency_s: float, *, asset_id: Optional[str] = None,
                      feature_age_s: Optional[float] = None) -> None:
        self.cycle_seconds.observe(max(float(latency_s), 0.0))
        self.cycles_total.labels(status if status in CYCLE_STATUSES else "error").inc()
        if asset_id is not None and feature_age_s is not None and math.isfinite(feature_age_s):
            self.feature_age_seconds.labels(asset_id).set(feature_age_s)

    def guard_rejected(self, reasons: Iterable[str]) -> None:
        for reason in reasons:
            self.guard_rejections_total.labels(reason if reason in REASON_CODES else "other").inc()

    def set_halted(self, halted: bool) -> None:
        self.halted.set(1 if halted else 0)

    def set_model(self, version: Optional[str], alias: str) -> None:
        self.model_info.clear()
        if version:
            self.model_info.labels(version, alias).set(1)

    def audit_error(self) -> None:
        self.audit_errors_total.inc()

    def set_redis_writable(self, ok: bool) -> None:
        self.redis_writable.set(1 if ok else 0)

    # -- monitoring
    def set_data_age(self, ages: Mapping[str, float], allowances: Optional[Mapping[str, float]] = None) -> None:
        self.data_age_seconds.clear()
        self.data_age_allowance_seconds.clear()
        for report, age in ages.items():
            self.data_age_seconds.labels(str(report)).set(float(age))
            if allowances and report in allowances:
                self.data_age_allowance_seconds.labels(str(report)).set(float(allowances[report]))

    def set_drift(self, psi: Mapping[str, float]) -> None:
        self.feature_drift_psi.clear()
        for feature, value in psi.items():
            self.feature_drift_psi.labels(str(feature)).set(float(value))

    def set_capture(self, rate: float, baseline: Optional[float] = None) -> None:
        self.capture_rate.set(float(rate))
        if baseline is not None:
            self.capture_baseline.set(float(baseline))

    def set_slippage(self, ratio: float) -> None:
        self.slippage_ratio.set(float(ratio))

    def breach(self, check: str) -> None:
        self.monitor_breaches_total.labels(check).inc()

    def set_consecutive(self, check: str, n: int) -> None:
        self.monitor_consecutive.labels(check).set(n)

    def set_halt_threshold(self, check: str, n: int) -> None:
        self.monitor_halt_threshold.labels(check).set(n)

    def set_calibration(self, asset: str, *, hit_error: Mapping[str, float], skill_ratio: Optional[float], pinball_ratio: Optional[float],
                        breach: bool, intervals: int) -> None:
        """The numbers of one asset's last calibration evaluation. A ratio that could not be computed is NaN (fails every comparison)."""
        for q, v in hit_error.items():
            self.calibration_hit_error.labels(asset, q).set(float(v))
        self.calibration_skill_ratio.labels(asset).set(math.nan if skill_ratio is None else float(skill_ratio))
        self.calibration_pinball_ratio.labels(asset).set(math.nan if pinball_ratio is None else float(pinball_ratio))
        self.calibration_breach.labels(asset).set(1 if breach else 0)
        self.calibration_window_intervals.labels(asset).set(int(intervals))

    def mark_calibration_evaluated(self, unix_s: Optional[float] = None) -> None:
        self.calibration_last_evaluation.set(time.time() if unix_s is None else float(unix_s))

    def forecast_log_error(self, n: int = 1) -> None:
        self.forecast_log_errors_total.inc(n)

    # -- exposition
    def render(self) -> tuple:
        """(body, content_type) for GET /metrics."""
        return generate_latest(self.registry), CONTENT_TYPE_LATEST
