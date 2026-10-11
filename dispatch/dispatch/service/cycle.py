"""One dispatch cycle (PRD: POST /signal "features, forecast, optimize, guard, dispatch").

    run_cycle(asset_id, now_utc, deps) -> CycleResult

The same function serves a live request, a replay (`now_utc` is the decision time) and the Gate 3 harness, so a backtest, a
shadow run and a live cycle cannot disagree about the math. It never raises: whatever goes wrong, the result is a
non-dispatch status with machine-readable reasons, and an audit record says why.

Steps (each failure stops the cycle; the status and reasons are in brackets)
  1. halt flag                 the Redis flag, read first                                 [halted: halted]
  2. asset and ledger          unknown asset, ledger cannot start                         [error: unknown_asset, soc_unavailable]
  3. latest features           online store read; missing, older than MAX_FEATURE_AGE_S,
                               published after the decision time                          [stale: features_missing, features_stale]
                                                                                          [error: online_store_unavailable, feature_read_failed,
                                                                                                  features_invalid, features_from_future]
  4. forecast                  deps.forecaster.predict(features, now); leakage check      [error: no_model, forecast_failed, forecast_invalid, forecast_leak]
                               The frame is first passed through deps.adapt_features(frame, forecaster) when there is one: D0's
                               `features.compat.for_forecaster` renames rt_v1 columns to the names a model trained on D1a's interim
                               builder reads, and does nothing for a model trained on rt_v1 names. A `ForecastError` (a column missing,
                               features from the future, an unknown settlement point) or any other exception is no dispatch.
                               then the issued P10/P50/P90 and rt_prof7_med go to the forecast log (the calibration check's evidence;
                               a failed write is counted and never stops the cycle)
  5. optimize                  deps.optimizer(spec, P50 view, soc0, mode="forecast")      [error: optimizer_unavailable, optimizer_failed,
                                                                                                  plan_not_optimal, plan_invalid]
  6. setpoint                  the first interval of the plan
  7. guard                     the halt flag is read AGAIN here, so a kill that arrives
                               mid-cycle wins; the verdict is written to the audit log
                               BEFORE anything executes                                   [rejected | halted: the guard's reason codes]
                                                                                          [error: guard_error, audit_failed, deadline_exceeded]
  8. paper execution           PaperLedger.record (which re-checks the limits itself)     [error: ledger_refused]
  9. audit and metrics         `setpoint` and `cycle` records; histogram, counters, gauges

Latency is measured with a monotonic clock around the whole cycle and exported as dispatch_cycle_seconds; the PRD target is
p99 < 30 s (config.CYCLE_P99_TARGET_S). A cycle that has already run longer than CYCLE_DEADLINE_S does not execute.

Reuse of resilience/: one cycle per asset at a time (a per-asset lock, as a lease gives one worker one transformer group),
and the ledger's "newer interval, later issue time" rule plays the part of the device's (epoch, seq) fencing.
"""
from __future__ import annotations

import hashlib
import logging
import math
import threading
import time
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Callable, Iterable, Optional

import pandas as pd

from ..config import MAX_FEATURE_AGE_S
from ..contracts import (CycleResult, Forecaster, GuardDecision, GuardState, HaltFlag, OnlineStore, Optimizer, Plan,
                         Setpoint, as_award_col)
from ..execution.audit import AuditError, AuditLog, to_jsonable
from ..execution.ledger import LedgerError, PaperLedger
from ..guard.guard import check as guard_check
from ..monitoring.checks import FeatureWindow
from ..monitoring.forecast_log import ForecastLog
from ..monitoring.metrics import Metrics
from .assets import AssetRegistry, UnknownAsset

log = logging.getLogger("dispatch.cycle")

# ---- named constants
FEATURE_FUTURE_TOLERANCE_S = 60.0     # ASSUMPTION: clock skew allowed between a publish time and our clock before data counts as from the future
CYCLE_DEADLINE_S = 120.0              # ASSUMPTION: a cycle that has run two minutes (inside one SCED interval) does not execute; the PRD target is p99 < 30 s
RT_QUANTILE_FOR_COST = "rt_p90"       # the forecast column that prices the energy a setpoint would buy (conservative side)

# ---- reason codes (the guard's own codes are in dispatch.guard.guard.REASON_CODES)
HALTED = "halted"
UNKNOWN_ASSET = "unknown_asset"
CYCLE_IN_PROGRESS = "cycle_in_progress"
ONLINE_STORE_UNAVAILABLE = "online_store_unavailable"
FEATURES_MISSING = "features_missing"
FEATURES_STALE = "features_stale"
FEATURES_FROM_FUTURE = "features_from_future"
FEATURE_READ_FAILED = "feature_read_failed"
FEATURES_INVALID = "features_invalid"
NO_MODEL = "no_model"
MODEL_STALE = "model_stale"
FORECAST_FAILED = "forecast_failed"
FORECAST_INVALID = "forecast_invalid"
FORECAST_LEAK = "forecast_leak"
OPTIMIZER_UNAVAILABLE = "optimizer_unavailable"
OPTIMIZER_FAILED = "optimizer_failed"
PLAN_NOT_OPTIMAL = "plan_not_optimal"
PLAN_INVALID = "plan_invalid"
SOC_UNAVAILABLE = "soc_unavailable"
GUARD_ERROR = "guard_error"
AUDIT_FAILED = "audit_failed"
LEDGER_REFUSED = "ledger_refused"
DEADLINE_EXCEEDED = "deadline_exceeded"
INVALID_TIME = "invalid_time"
EXCEPTION = "exception"

STATUSES = ("dispatched", "rejected", "halted", "stale", "error")


def utc_now() -> pd.Timestamp:
    return pd.Timestamp.now(tz="UTC")


@dataclass
class ServiceCycleResult(CycleResult):
    """contracts.CycleResult plus what the API and the audit log want to show."""
    latency_s: float = 0.0
    plan_id: str = ""
    detail: dict = field(default_factory=dict)
    proposed: Optional[Setpoint] = None     # what the plan proposed, also when the guard rejected it


@dataclass
class CycleDeps:
    """Everything a cycle needs. A dependency that could not be bound is None; the cycle then fails closed with a reason."""
    assets: AssetRegistry
    halt: HaltFlag
    ledger: PaperLedger
    audit: AuditLog
    store: Optional[OnlineStore] = None
    forecaster: Optional[Forecaster] = None
    optimizer: Optional[Optimizer] = None
    guard: Callable[..., GuardDecision] = guard_check
    metrics: Optional[Metrics] = None
    feature_window: Optional[FeatureWindow] = None
    forecast_log: Optional[ForecastLog] = None          # every cycle's issued quantiles, for the daily calibration check
    adapt_features: Optional[Callable] = None           # (features frame, forecaster) -> the frame that forecaster reads (D0's compat.for_forecaster)
    max_feature_age_s: float = MAX_FEATURE_AGE_S
    deadline_s: float = CYCLE_DEADLINE_S
    clock: Callable[[], pd.Timestamp] = utc_now
    perf: Callable[[], float] = time.perf_counter
    model_check: Optional[Callable[[], Optional[str]]] = None   # () -> None while the loaded model is still the one the alias names, else why not
    unavailable: dict = field(default_factory=dict)      # dependency name -> why it is None (shown in results and /health)
    last_results: dict = field(default_factory=dict)     # asset_id -> last ServiceCycleResult
    _locks: dict = field(default_factory=dict)
    _locks_guard: threading.Lock = field(default_factory=threading.Lock)

    def lock_for(self, asset_id: str) -> threading.Lock:
        with self._locks_guard:
            return self._locks.setdefault(asset_id, threading.Lock())


class _Stop(Exception):
    """Ends a cycle early with a non-dispatch outcome."""

    def __init__(self, status: str, reasons: tuple, detail: Optional[dict] = None):
        super().__init__(status, reasons)
        self.status, self.reasons, self.detail = status, tuple(reasons), detail or {}


def run_cycle(asset_id: str, now_utc: Optional[pd.Timestamp] = None, deps: Optional[CycleDeps] = None) -> ServiceCycleResult:
    """Run one cycle for `asset_id` at decision time `now_utc` (default: now). Never raises."""
    if deps is None:
        from .wiring import default_deps        # lazy: binding the real modules is slow and must not happen on import
        deps = default_deps()
    cyc = _Cycle(str(asset_id), now_utc, deps)
    lock = deps.lock_for(cyc.asset_id)
    if not lock.acquire(blocking=False):
        return cyc.finish("error", (CYCLE_IN_PROGRESS,), {"hint": "another cycle for this asset is still running"})
    try:
        return cyc.run()
    finally:
        lock.release()


def replay_cycles(asset_id: str, decision_times: Iterable[pd.Timestamp], deps: CycleDeps, *,
                  prepare: Optional[Callable[[pd.Timestamp], None]] = None) -> list:
    """Gate 3 / replay driver: for each decision time, `prepare(t)` loads the point-in-time features for t into the online
    store, then the live cycle runs with now_utc=t. Returns the results in order."""
    out = []
    for t in decision_times:
        if prepare is not None:
            prepare(t)
        out.append(run_cycle(asset_id, t, deps))
    return out


class _Cycle:
    def __init__(self, asset_id: str, now_utc, deps: CycleDeps):
        self.asset_id, self.deps = asset_id, deps
        self.t0 = deps.perf()
        self.bad_time = None
        try:
            ts = pd.Timestamp(now_utc) if now_utc is not None else deps.clock()
            if ts is pd.NaT or ts.tzinfo is None:
                raise ValueError("now_utc must be a timezone-aware timestamp")
            self.now = ts.tz_convert("UTC")
        except Exception as exc:
            self.bad_time = f"{type(exc).__name__}: {exc}"
            self.now = deps.clock()
        self.events: list = []
        self.hashes: dict = {}
        self.last_hash = ""
        self.model_version = ""
        self.feature_age_s: Optional[float] = None
        self.plan_id = ""
        self.setpoint: Optional[Setpoint] = None          # what executed (set only after the ledger accepted it)
        self.proposed: Optional[Setpoint] = None          # what the plan proposed
        self._signal_ev: dict = {}
        self.decision: Optional[GuardDecision] = None
        self.halted_seen: Optional[bool] = None
        self.guard_rejected: tuple = ()
        self.detail: dict = {}

    # ------------------------------------------------------------------------------------------ the steps
    def run(self) -> ServiceCycleResult:
        try:
            if self.bad_time:
                raise _Stop("error", (INVALID_TIME,), {"error": self.bad_time})
            self._halt_check()
            cfg = self._asset()
            df, max_pub = self._features(cfg)
            ff, view = self._forecast(cfg, df, max_pub)
            plan, soc0, soc_age = self._optimize(cfg, view)
            sp = self._setpoint(cfg, plan, ff)
            self._guard_and_execute(cfg, ff, plan, sp, soc0, soc_age)
        except _Stop as stop:
            return self.finish(stop.status, stop.reasons, stop.detail)
        except Exception as exc:                         # nothing may escape: an unexpected error is a non-dispatch outcome
            log.exception("cycle for %s failed", self.asset_id)
            self.events.append({"type": "error", "asset_id": self.asset_id, "where": "run_cycle",
                                "error": f"{type(exc).__name__}: {exc}"[:500]})
            return self.finish("error", (EXCEPTION,), {"error": f"{type(exc).__name__}: {exc}"[:300]})
        return self.finish("dispatched", (), {})

    def _halt_check(self) -> None:
        try:
            halted, reason = self.deps.halt.is_set()
        except Exception as exc:                         # a halt flag that cannot answer is a halt
            halted, reason = True, f"halt flag unreadable: {type(exc).__name__}"
        self.halted_seen = bool(halted)
        if halted:
            raise _Stop("halted", (HALTED,), {"halt_reason": reason})

    def _asset(self):
        try:
            cfg = self.deps.assets.get(self.asset_id)
        except UnknownAsset:
            raise _Stop("error", (UNKNOWN_ASSET,), {"known": self.deps.assets.ids()}) from None
        try:
            if not self.deps.ledger.has_asset(self.asset_id):
                self.deps.ledger.init_asset(cfg.spec, cfg.initial_soc_mwh)
        except LedgerError as exc:
            raise _Stop("error", (SOC_UNAVAILABLE,), {"error": str(exc)}) from exc
        return cfg

    def _features(self, cfg):
        d = self.deps
        if d.store is None:
            raise _Stop("error", (ONLINE_STORE_UNAVAILABLE,), {"why": d.unavailable.get("read_latest", "not wired")})
        try:
            df, asof, max_pub = d.store.read_latest(cfg.spec.settlement_point)
        except Exception as exc:
            if _is_missing(exc):                          # no features is a data problem (stale), not a platform error
                raise _Stop("stale", (FEATURES_MISSING,), {"error": f"{type(exc).__name__}: {exc}"[:300]}) from exc
            raise _Stop("error", (FEATURE_READ_FAILED,), {"error": f"{type(exc).__name__}: {exc}"[:300]}) from exc
        if (df is None or not isinstance(df, pd.DataFrame) or len(df) == 0 or asof is None or max_pub is None
                or pd.isna(asof) or pd.isna(max_pub)):
            raise _Stop("stale", (FEATURES_MISSING,), {"settlement_point": cfg.spec.settlement_point})
        try:
            max_pub = pd.Timestamp(max_pub).tz_convert("UTC")
            asof = pd.Timestamp(asof).tz_convert("UTC")
        except Exception as exc:
            raise _Stop("error", (FEATURES_INVALID,), {"error": f"timestamps are not tz-aware: {exc}"[:200]}) from exc
        if not isinstance(df.index, pd.DatetimeIndex) or df.index.tz is None:
            raise _Stop("error", (FEATURES_INVALID,), {"error": "feature index must be tz-aware interval_start_utc"})
        age = (self.now - max_pub).total_seconds()
        self.feature_age_s = age
        if age < -FEATURE_FUTURE_TOLERANCE_S or (asof - self.now).total_seconds() > FEATURE_FUTURE_TOLERANCE_S:
            raise _Stop("error", (FEATURES_FROM_FUTURE,),
                        {"max_published_utc": max_pub, "asof_utc": asof, "decision_utc": self.now})
        if age > d.max_feature_age_s:
            raise _Stop("stale", (FEATURES_STALE,), {"feature_age_s": age, "max_feature_age_s": d.max_feature_age_s,
                                                     "max_published_utc": max_pub})
        self.features_sha256 = _frame_hash(df)
        return df, max_pub

    def _forecast(self, cfg, df, max_pub):
        d = self.deps
        if d.forecaster is None:
            raise _Stop("error", (NO_MODEL,), {"why": d.unavailable.get("model", "no model holds the alias")})
        if d.model_check is not None:                     # a model whose alias was cleared or moved is a stale model: no dispatch
            try:
                stale = d.model_check()
            except Exception:                             # an unanswerable check keeps the last answer inside model_check itself
                stale = None
            if stale:
                raise _Stop("error", (MODEL_STALE,), {"why": stale})
        try:
            df.attrs["settlement_point"] = cfg.spec.settlement_point    # QuantileForecaster.resolve_sp reads it from here
            feats = d.adapt_features(df, d.forecaster) if d.adapt_features is not None else df
            feats.attrs["settlement_point"] = cfg.spec.settlement_point
            if d.feature_window is not None:                            # what the model was fed: the drift check compares it with the model's reference
                d.feature_window.add(feats)
            ff = d.forecaster.predict(feats, self.now)
        except Exception as exc:
            raise _Stop("error", (FORECAST_FAILED,), {"error": f"{type(exc).__name__}: {exc}"[:300]}) from exc
        try:
            self.model_version = str(ff.model_version)
            frame = ff.frame
            if not isinstance(frame, pd.DataFrame) or len(frame) == 0:
                raise ValueError("empty forecast frame")
            if not isinstance(frame.index, pd.DatetimeIndex) or frame.index.tz is None:
                raise ValueError("forecast index must be tz-aware")
            if not self.model_version.strip():
                raise ValueError("forecast carries no model_version")
            if ff.settlement_point != cfg.spec.settlement_point:
                raise ValueError(f"forecast is for {ff.settlement_point}, asset settles at {cfg.spec.settlement_point}")
            view = ff.price_view("p50")
            if "rt_usd_per_mwh" not in view.columns or view["rt_usd_per_mwh"].isna().any():
                raise ValueError("forecast has no complete rt_p50 column")
            fmax = pd.Timestamp(ff.max_feature_published_utc)
            leak_s = None if pd.isna(fmax) else (fmax.tz_convert("UTC") - self.now).total_seconds()
        except Exception as exc:
            raise _Stop("error", (FORECAST_INVALID,), {"error": f"{type(exc).__name__}: {exc}"[:300]}) from exc
        if leak_s is not None and leak_s > FEATURE_FUTURE_TOLERANCE_S:
            raise _Stop("error", (FORECAST_LEAK,), {"max_feature_published_utc": fmax, "decision_utc": self.now})
        self._log_forecast(cfg, ff, df)
        return ff, view

    def _log_forecast(self, cfg, ff, df) -> None:
        """DESIGN.md "Monitoring policy": keep what was forecast, so it can be judged against the realised prices. Never raises."""
        d = self.deps
        if d.forecast_log is None:
            return
        ok = d.forecast_log.log_cycle(asset_id=self.asset_id, settlement_point=cfg.spec.settlement_point, model_version=self.model_version,
                                      issued_utc=self.now, frame=ff.frame, features=df)
        if not ok and d.metrics is not None:
            try:
                d.metrics.forecast_log_error()
            except Exception:
                pass

    def _optimize(self, cfg, view):
        d = self.deps
        if d.optimizer is None:
            raise _Stop("error", (OPTIMIZER_UNAVAILABLE,), {"why": d.unavailable.get("solve", "not wired")})
        first = view.index[0]
        try:
            soc0, soc_age = d.ledger.soc_reading(self.asset_id, first)
        except Exception as exc:
            raise _Stop("error", (SOC_UNAVAILABLE,), {"error": f"{type(exc).__name__}: {exc}"[:300]}) from exc
        try:
            plan = d.optimizer(cfg.spec, view, soc0, mode="forecast")
        except Exception as exc:
            raise _Stop("error", (OPTIMIZER_FAILED,), {"error": f"{type(exc).__name__}: {exc}"[:300]}) from exc
        self._signal_ev = self._signal_event(cfg, plan, None, soc0)
        self.events.append(self._signal_ev)
        if not getattr(plan, "ok", False):
            raise _Stop("error", (PLAN_NOT_OPTIMAL,), {"plan_status": str(getattr(plan, "status", "?")),
                                                      "solver": str(getattr(plan, "solver", "?"))})
        return plan, soc0, soc_age

    def _setpoint(self, cfg, plan: Plan, ff) -> Setpoint:
        spec = cfg.spec
        try:
            frame = plan.frame
            first = frame.index[0]
            row = frame.iloc[0]
            if first != ff.frame.index[0]:
                raise ValueError(f"plan starts at {first}, forecast at {ff.frame.index[0]}")
            net = float(row["discharge_mw"]) - float(row["charge_mw"])       # + discharge (injection), - charge
            as_mw = {p: float(row[as_award_col(p)]) for p in spec.as_products if as_award_col(p) in frame.columns}
            plan_id = (plan.inputs_sha256 or _digest(str(row.to_dict())))[:16]
            sp = Setpoint(asset_id=spec.asset_id, interval_start_utc=first, net_mw=net, as_mw=as_mw, plan_id=plan_id,
                          model_version=self.model_version, issued_utc=self.now)
        except Exception as exc:
            raise _Stop("error", (PLAN_INVALID,), {"error": f"{type(exc).__name__}: {exc}"[:300]}) from exc
        self.proposed, self.plan_id = sp, plan_id
        self._signal_ev["proposed"] = sp                                  # the signal record now carries the proposal
        return sp

    def _guard_and_execute(self, cfg, ff, plan, sp: Setpoint, soc0: float, soc_age: float) -> None:
        d, spec = self.deps, cfg.spec
        if d.perf() - self.t0 > d.deadline_s:
            raise _Stop("error", (DEADLINE_EXCEEDED,), {"elapsed_s": d.perf() - self.t0, "deadline_s": d.deadline_s})
        try:                                                              # read the flag again: a kill during the cycle wins
            halted, halt_reason = d.halt.is_set()
        except Exception as exc:
            halted, halt_reason = True, f"halt flag unreadable: {type(exc).__name__}"
        self.halted_seen = bool(halted)
        ref_price = _ref_price(ff)
        try:
            exposure = d.ledger.preview(spec, sp, ref_price).cost_basis_end_usd
        except Exception as exc:
            raise _Stop("error", (SOC_UNAVAILABLE,), {"error": f"{type(exc).__name__}: {exc}"[:300]}) from exc
        state = GuardState(soc_mwh=soc0, soc_age_s=soc_age, halted=bool(halted), halt_reason=halt_reason,
                           exposure_usd=exposure, exposure_cap_usd=cfg.exposure_cap_usd)
        try:
            decision = d.guard(spec, sp, state, now_utc=self.now)
        except Exception as exc:                                          # a guard error means no dispatch
            raise _Stop("error", (GUARD_ERROR,), {"error": f"{type(exc).__name__}: {exc}"[:300]}) from exc
        if halted and decision.accepted:                                  # only a faulty injected guard gets here; the cycle still stops
            self.events.append({"type": "error", "asset_id": self.asset_id, "where": "guard",
                                "error": "the guard accepted a setpoint while the halt flag was set"})
            raise _Stop("halted", (HALTED, GUARD_ERROR), {"halt_reason": halt_reason})
        self.decision = decision
        self.events.append({"type": "guard_decision", "asset_id": self.asset_id, "accepted": bool(decision.accepted),
                            "reasons": list(decision.reasons), "setpoint": sp, "state": state, "plan_id": self.plan_id,
                            "checked_utc": decision.checked_utc})
        self._flush_or_stop()                                             # write ahead: the verdict is durable before anything executes
        if not decision.accepted:
            self.guard_rejected = tuple(decision.reasons)
            raise _Stop("halted" if HALTED in decision.reasons else "rejected", tuple(decision.reasons),
                        {"halt_reason": halt_reason} if halted else {})
        exec_sp = decision.setpoint if decision.setpoint is not None else sp
        try:
            entry = d.ledger.record(spec, exec_sp, ref_price_usd_per_mwh=ref_price, audit_hash=self.hashes.get("guard_decision", ""))
        except LedgerError as exc:
            raise _Stop("error", (LEDGER_REFUSED,), {"ledger_code": exc.code, "error": str(exc)[:300]}) from exc
        self.events.append({"type": "setpoint", "asset_id": self.asset_id, "setpoint": exec_sp, "executor": "paper_ledger",
                            "ledger_seq": entry.seq, "soc_start_mwh": entry.soc_start_mwh, "soc_end_mwh": entry.soc_end_mwh,
                            "cost_basis_end_usd": entry.cost_basis_end_usd, "guard_audit_hash": self.hashes.get("guard_decision", "")})
        self.setpoint = exec_sp

    # ------------------------------------------------------------------------------------------ audit and result
    def _signal_event(self, cfg, plan, proposed, soc0) -> dict:
        return {"type": "signal", "asset_id": self.asset_id, "now_utc": self.now, "model_version": self.model_version,
                "feature_age_s": self.feature_age_s, "features_sha256": getattr(self, "features_sha256", None),
                "soc0_mwh": soc0, "proposed": proposed,
                "plan": {"status": str(getattr(plan, "status", "?")), "solver": str(getattr(plan, "solver", "?")),
                         "mode": str(getattr(plan, "mode", "?")), "objective_usd": getattr(plan, "objective_usd", None),
                         "inputs_sha256": getattr(plan, "inputs_sha256", "")}}

    def _flush(self) -> None:
        while self.events:
            ev = self.events[0]
            h = self.deps.audit.append(ev)
            self.events.pop(0)
            self.hashes[ev["type"]] = h
            self.last_hash = h

    def _flush_or_stop(self) -> None:
        try:
            self._flush()
        except (AuditError, ValueError, OSError) as exc:
            self._audit_failed(exc)
            raise _Stop("error", (AUDIT_FAILED,), {"error": f"{type(exc).__name__}: {exc}"[:300]}) from exc

    def _audit_failed(self, exc) -> None:
        log.error("audit append failed in cycle for %s: %s", self.asset_id, exc)
        if self.deps.metrics is not None:
            try:
                self.deps.metrics.audit_error()
            except Exception:
                pass

    def finish(self, status: str, reasons: tuple, detail: dict) -> ServiceCycleResult:
        d = self.deps
        elapsed = max(d.perf() - self.t0, 0.0)
        detail = dict(detail)
        self.events.append({"type": "cycle", "asset_id": self.asset_id, "now_utc": self.now, "status": status,
                            "reasons": list(reasons), "detail": detail, "latency_s": elapsed,
                            "model_version": self.model_version, "feature_age_s": self.feature_age_s, "plan_id": self.plan_id})
        try:
            self._flush()
        except Exception as exc:                          # best effort: the decision stands, the audit gap is reported
            self._audit_failed(exc)
            detail["audit_error"] = f"{type(exc).__name__}: {exc}"[:200]
        res = ServiceCycleResult(
            asset_id=self.asset_id, started_utc=self.now, finished_utc=self.now + timedelta(seconds=elapsed), status=status,
            setpoint=self.setpoint if status == "dispatched" else None,
            decision=self.decision, reasons=tuple(reasons), model_version=self.model_version,
            feature_age_s=self.feature_age_s, audit_hash=self.last_hash, latency_s=elapsed, plan_id=self.plan_id,
            detail=detail, proposed=self.proposed)
        m = d.metrics
        if m is not None:
            try:
                m.observe_cycle(status, elapsed, asset_id=self.asset_id, feature_age_s=self.feature_age_s)
                if self.guard_rejected:
                    m.guard_rejected(self.guard_rejected)
                if self.halted_seen is not None:
                    m.set_halted(self.halted_seen)
            except Exception:                              # metrics never change an outcome
                log.exception("metrics update failed")
        d.last_results[self.asset_id] = res
        return res


# ---------------------------------------------------------------------------------------------- helpers
def _ref_price(ff) -> Optional[float]:
    """The price the setpoint's charging energy is costed at: the P90 RT forecast of the first interval, if there is one."""
    try:
        v = float(ff.frame[RT_QUANTILE_FOR_COST].iloc[0])
        return v if math.isfinite(v) else None
    except Exception:
        return None


def _is_missing(exc: BaseException) -> bool:
    """True for "there are no features": a LookupError or FileNotFoundError, or D0's FeaturesMissing (matched by name,
    because it derives from RuntimeError, and this module does not import D0's)."""
    return isinstance(exc, (LookupError, FileNotFoundError)) or any(c.__name__ == "FeaturesMissing" for c in type(exc).__mro__)


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def _frame_hash(df: pd.DataFrame) -> Optional[str]:
    try:
        return hashlib.sha256(pd.util.hash_pandas_object(df, index=True).to_numpy().tobytes()).hexdigest()
    except Exception:
        return None


def cycle_to_json(res: ServiceCycleResult) -> dict:
    """The API / log view of a result."""
    return to_jsonable({
        "asset_id": res.asset_id, "status": res.status, "reasons": list(res.reasons), "model_version": res.model_version,
        "feature_age_s": res.feature_age_s, "started_utc": res.started_utc, "finished_utc": res.finished_utc,
        "latency_s": res.latency_s, "plan_id": res.plan_id, "setpoint": res.setpoint, "proposed_setpoint": res.proposed,
        "guard": None if res.decision is None else {"accepted": res.decision.accepted, "reasons": list(res.decision.reasons),
                                                    "checked_utc": res.decision.checked_utc},
        "audit_hash": res.audit_hash, "detail": res.detail})
