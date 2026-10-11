"""The dispatch service (PRD "Service" table and FR8 to FR11).

    POST /signal       run one cycle: features, forecast, optimize, guard, paper dispatch          body {asset_id, now_utc?}
    POST /kill         set the Redis halt flag; the guard rejects every setpoint until it is cleared   body {reason, set_by}
    POST /resume       clear the halt flag                                                         body {reason, cleared_by}
    GET  /health       model version, feature age, last cycle status, halt state, Redis, wiring per module
    GET  /metrics      Prometheus text
    GET  /attribution  attribution for an asset and a date range (501 with a reason while the engine is unavailable)

/resume is an addition to the PRD table: a kill switch needs a way back, and it is better as an audited API call than as a
bare `redis-cli DEL`. It refuses while Redis is unreachable or the audit chain does not verify.

HTTP codes. /signal answers 200 for dispatched, rejected, halted and stale (the system did what it is meant to do: decide), and
503 for error (it could not complete a cycle). The body is always the cycle result; callers must read `status`, and only
`"dispatched"` means a setpoint executed. 404 unknown asset, 401 bad token, 403 replay not allowed, 409 resume refused.

Wiring of the real modules (I1, 10 Oct 2026). With no arguments, `create_app()` binds D0's `data.prices.price_source` (realised prices),
D1b's `optimizer.backtest.make_backtest_fn` and D1a's `models.replay.make_forecast_fn`, and from them builds
  - the capture-rate and Gate 3 slippage providers (the backtest is built lazily on the monitor thread, never in a request handler: the
    first call after a start takes ~90 s),
  - the daily calibration check, fed by the forecast log every cycle writes (monitoring/forecast_log.py),
  - /attribution for paper assets (actual dispatch = the paper ledger) and for the public pilot assets listed under `disclosure_assets` in
    the assets file (actual dispatch = D0's `esr_sced`). The shadow optimizer runs only for windows up to SHADOW_MAX_DAYS.

Startup (lifespan): connect Redis, bind the other agents' modules (a missing one is reported, not fatal), load the model for
DISPATCH_MODEL_ALIAS (default "paper"). If no model holds the alias the service starts anyway and /signal fails closed with
`no_model`; it retries the load at most once a minute while it has none. A running model is never swapped: a restart reloads it.

Environment (secrets come from the environment only, and are never logged)
  DISPATCH_MODEL_ALIAS         registry alias to serve (paper | live; a research model may not dispatch), default paper
  DISPATCH_REDIS_URL           Redis, default config.REDIS_URL = redis://localhost:6390/0, the platform's own instance (scripts/redis_dispatch.sh);
                               :6379 belongs to another application. Credentials in the URL are redacted in /health
  DISPATCH_HOME                runtime directory (audit log, ledger, MLflow) and the lake the prices and features come from
  DISPATCH_RUNTIME_DIR         if set, the audit log, the paper ledger and the forecast log go to <dir>/audit, <dir>/ledger and <dir>/forecasts
                               instead of DISPATCH_HOME/runtime. For replays against the real lake that must not enter the live paper ledger.
  DISPATCH_ASSETS_FILE         assets JSON, default ops/assets.example.json
  DISPATCH_API_TOKEN           if set, /signal /kill /resume /attribution need "Authorization: Bearer <token>"
  DISPATCH_ALLOW_REPLAY        1 lets /signal take a now_utc far from the server clock (replays; use a separate DISPATCH_HOME)
  DISPATCH_MONITOR_INTERVAL_S  seconds between monitoring passes, default 60, 0 turns the monitor off
"""
from __future__ import annotations

import hashlib
import json
import logging
import math
import os
import secrets
import sys
import threading
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

import pandas as pd
from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse, Response
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from .. import config
from ..execution.audit import AuditError, AuditLog, to_jsonable
from ..execution.ledger import PaperLedger
from ..monitoring import checks as monitor_checks
from ..monitoring.calibration import CalibrationMonitor
from ..monitoring.forecast_log import ForecastLog
from ..monitoring.halt_policy import HaltPolicy
from ..monitoring.metrics import Metrics
from ..monitoring.providers import PRICE_LAG, capture_provider, slippage_provider
from ..monitoring.runner import MonitorRunner
from .assets import AssetRegistry
from .cycle import CycleDeps, cycle_to_json, run_cycle
from .disclosure import disclosure_inputs, load_disclosure_assets
from .reconcile import reconcile
from .state import RedisHaltFlag, make_redis_client, redact_url
from .wiring import OPTIONAL_TARGETS, LazyBacktest, Wiring, build_deps

log = logging.getLogger("dispatch.service")

# ---- named constants
REPLAY_TOLERANCE_S = 600.0            # ASSUMPTION: a now_utc further than this from the server clock is a replay, not a live call
MODEL_RETRY_S = 60.0                  # ASSUMPTION: while no model is loaded, try the registry again at most this often
MODEL_CHECK_S = 30.0                  # ASSUMPTION: how often the loaded model is compared with what its alias names
DEFAULT_MONITOR_INTERVAL_S = 60.0     # ASSUMPTION: one monitoring pass a minute (the feeds move every 5 to 15 minutes)
MAX_ATTRIBUTION_DAYS = 93             # ASSUMPTION: one quarter per request keeps the response and the engine call bounded
DISPATCH_ALIASES = tuple(a for a in config.STAGE_ALIASES if a != "research")      # DESIGN.md: no model with the live or paper alias, no dispatch
SHADOW_MAX_DAYS = 3                   # D1b measured the shadow run at ~2.5 s per day (five products): longer requests skip it (forecast_fn=None)


def _env_flag(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in ("1", "true", "yes", "on")


@dataclass
class Settings:
    model_alias: str = "paper"
    api_token: Optional[str] = None
    allow_replay: bool = False
    monitor_interval_s: float = DEFAULT_MONITOR_INTERVAL_S
    use_evidently: bool = False

    @classmethod
    def from_env(cls, **override) -> "Settings":
        s = cls(model_alias=os.environ.get("DISPATCH_MODEL_ALIAS", "paper"),
                api_token=os.environ.get("DISPATCH_API_TOKEN") or None,
                allow_replay=_env_flag("DISPATCH_ALLOW_REPLAY"),
                monitor_interval_s=float(os.environ.get("DISPATCH_MONITOR_INTERVAL_S", DEFAULT_MONITOR_INTERVAL_S)),
                use_evidently=_env_flag("DISPATCH_USE_EVIDENTLY"))
        for k, v in override.items():
            if v is not None:
                setattr(s, k, v)
        if s.model_alias not in DISPATCH_ALIASES:
            raise ValueError(f"DISPATCH_MODEL_ALIAS must be one of {DISPATCH_ALIASES} (a research model is not approved to dispatch, "
                             f"even on paper), got {s.model_alias!r}")
        return s


# ---- request bodies
class SignalRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    asset_id: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_.\-]+$")
    now_utc: Optional[AwareDatetime] = Field(default=None, description="Decision time, for replays. Default: the server clock.")


class KillRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reason: str = Field(min_length=1, max_length=500)
    set_by: str = Field(min_length=1, max_length=100)


class ResumeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reason: str = Field(min_length=1, max_length=500)
    cleared_by: str = Field(min_length=1, max_length=100)


# ---- application state
class AppState:
    """Everything the endpoints share. Built in the lifespan hook, held in app.state.dispatch."""

    def __init__(self, settings: Settings, *, wiring: Optional[Wiring] = None, redis_client=None, assets=None, ledger=None,
                 audit=None, metrics=None, clock: Optional[Callable] = None, attribution_inputs=None, monitor_providers=None,
                 price_source=None, backtest_fn=None, capture_baseline=None, forecast_fn=None, forecast_log=None,
                 disclosure_assets=None, clip_attribution: Optional[bool] = None):
        self.settings = settings
        self.clock = clock or (lambda: pd.Timestamp.now(tz="UTC"))
        self.wiring = wiring or Wiring.from_modules()
        self.redis = redis_client if redis_client is not None else make_redis_client()
        self.metrics = metrics or Metrics()
        self._anchor_key = None
        self.halt = RedisHaltFlag(self.redis, clock=self.clock)
        self.audit = audit if audit is not None else AuditLog(_runtime_dir("audit"))
        self._anchor_key = f"dispatch:audit:head:{hashlib.sha1(str(self.audit.dir).encode()).hexdigest()[:10]}"
        if not self.audit.has_anchor:
            self.audit.set_anchor(self._write_anchor)
        # the forecast log lives next to the audit log (runtime/forecasts), so a test that injects an audit directory keeps it there too
        self.forecast_log = forecast_log if forecast_log is not None else ForecastLog(Path(self.audit.dir).parent / "forecasts")
        self.deps: CycleDeps = build_deps(wiring=self.wiring, assets=assets or AssetRegistry.load(), halt=self.halt,
                                          ledger=ledger or PaperLedger(_runtime_dir("ledger")), audit=self.audit, metrics=self.metrics,
                                          clock=self.clock, forecast_log=self.forecast_log)
        specs = [self.deps.assets.get(a).spec for a in self.deps.assets.ids()]
        # D0's realised prices; D1b's backtest over D1a's point-in-time forecast function (built lazily, once a model is loaded)
        lake_prices = price_source is None and self.wiring.price_source is not None
        price_source = price_source if price_source is not None else self.wiring.price_source
        self.price_source = price_source
        self.clip_attribution = lake_prices if clip_attribution is None else bool(clip_attribution)
        if (backtest_fn is None and price_source is not None and self.wiring.make_backtest_fn is not None
                and self.wiring.make_forecast_fn is not None):
            backtest_fn = LazyBacktest(lambda: self.deps.forecaster, self.wiring.make_forecast_fn, self.wiring.make_backtest_fn,
                                       price_source, self.deps.ledger, specs)
        self.backtest_fn = backtest_fn
        self.forecast_fn = forecast_fn
        self.disclosure_assets = dict(disclosure_assets) if disclosure_assets is not None else _safe_disclosure()
        self.attribution_inputs = attribution_inputs
        if self.attribution_inputs is None and price_source is not None:
            self.attribution_inputs = self._ledger_inputs(price_source, forecast_fn)
        self.policy = HaltPolicy(self.halt, self.audit, metrics=self.metrics, clock=self.clock)
        providers = dict(monitor_providers or {})
        if price_source is not None and self.wiring.solve is not None:      # realised prices make capture rate and Gate 3 measurable
            providers.setdefault("capture", capture_provider(self.deps.ledger, specs, price_source, self.wiring.solve,
                                                             baseline=capture_baseline))
            if backtest_fn is not None:
                providers.setdefault("slippage", slippage_provider(self.deps.ledger, specs, price_source, backtest_fn))
        self.calibration: Optional[CalibrationMonitor] = None
        if price_source is not None:                                       # the daily calibration check needs realised prices and the forecast log
            self.calibration = CalibrationMonitor(self.forecast_log, specs, price_source, forecaster=lambda: self.deps.forecaster)
            providers.setdefault("calibration", self.calibration)
        self.monitor = MonitorRunner(
            policy=self.policy, metrics=self.metrics, clock=self.clock, freshness=self.wiring.freshness,
            feature_window=self.deps.feature_window,
            reference_provider=lambda: getattr(self.deps.forecaster, "reference_frame", None),
            capture_provider=providers.get("capture"), slippage_provider=providers.get("slippage"),
            calibration_provider=providers.get("calibration"), use_evidently=settings.use_evidently)
        self.model_error = ""
        self._model_block: Optional[str] = None
        self._alias_checked: Optional[float] = None
        self.alias_error = ""
        self._alias_lock = threading.Lock()
        self.deps.model_check = self.model_status
        self.orphans: list = []
        self.redis_ok = False
        self._model_lock = threading.Lock()
        self._model_tried: Optional[float] = None
        self._last_model_audit = None

    def _shadow_forecast_fn(self, spec, start, end, forecast_fn):
        """The forecast function for the attribution engine's shadow optimizer. D1b asked for None on long windows: the shadow run costs
        about 2.5 s a day (five products), so it only runs for windows up to SHADOW_MAX_DAYS, and only with a model loaded."""
        if forecast_fn is not None:
            return forecast_fn
        if (end - start) > pd.Timedelta(days=SHADOW_MAX_DAYS) or self.deps.forecaster is None:
            return None
        try:
            make = self.wiring.make_forecast_fn
            if make is None:
                from ..models.replay import make_forecast_fn as make
            return make(self.deps.forecaster, spec.settlement_point)         # D0's point-in-time features, adapted to the model (wiring.pit_forecast_fn)
        except Exception as exc:
            log.info("no forecast_fn for the attribution shadow run: %s", exc)
            return None

    def _ledger_inputs(self, price_source, forecast_fn):
        """The default /attribution inputs for a paper asset: what it did (the ledger), realised prices, and the forecast function.

        With the lake's price source (`clip_attribution`) the window is clipped to what can be judged: it starts at the first executed
        interval (before that there was no paper asset, and "idle" would count the whole market as missed) and ends at the last interval
        whose prices are final (PRICE_LAG ago). Without it the window is used as asked."""
        def inputs(spec, start, end):
            ledger = self.deps.ledger
            if not ledger.has_asset(spec.asset_id):
                raise LookupError(f"no paper dispatch for {spec.asset_id} between {start} and {end}")
            if self.clip_attribution:
                rows = ledger.entries(spec.asset_id, start_utc=start, end_utc=end)
                if rows:
                    start = max(start, pd.Timestamp(rows[0].interval_start_utc).tz_convert("UTC"))
                end = min(end, (self.clock() - PRICE_LAG).floor("15min"))
                if end <= start:
                    raise LookupError(f"no paper dispatch for {spec.asset_id} has settled yet in that window (prices are final {PRICE_LAG} after an interval)")
            actual = ledger.as_actual(spec, start_utc=start, end_utc=end)
            if actual is None or len(actual.frame) == 0:
                raise LookupError(f"no paper dispatch for {spec.asset_id} between {start} and {end}")
            return actual, price_source(spec, start, end), self._shadow_forecast_fn(spec, start, end, forecast_fn)
        return inputs

    def disclosure_inputs(self, asset, start, end):
        """/attribution inputs for a public pilot asset: D0's `esr_sced` telemetry, the hub price proxy, D1b's day-ahead position."""
        if self.price_source is None:
            raise LookupError("no realised-price source is wired into the service")
        return disclosure_inputs(asset, start, end, price_source=self.price_source,
                                 forecast_fn=self._shadow_forecast_fn(asset.spec, start, end, self.forecast_fn))

    # -- audit anchor (tail truncation is invisible to a hash chain, so the head is also kept in Redis)
    def _write_anchor(self, seq: int, h: str) -> None:
        if self._anchor_key is not None:
            self.redis.set(self._anchor_key, json.dumps({"seq": seq, "hash": h}))

    def read_anchor(self) -> Optional[tuple]:
        try:
            raw = self.redis.get(self._anchor_key)
            if raw is None:
                return None
            d = json.loads(raw)
            return int(d["seq"]), str(d["hash"])
        except Exception:
            return None

    # -- lifecycle
    def startup(self) -> None:
        self.redis_ok = self.halt.ping()
        info = self.halt.info()
        self.metrics.set_halted(info["halted"])
        self.metrics.set_redis_writable(info["writable"])
        if self.redis_ok and not info["writable"]:
            log.error("Redis answers reads but refuses writes (out of memory?): a kill set by hand would fail; POST /kill still halts")
        self._audit({"type": "startup", "model_alias": self.settings.model_alias, "redis": redact_url(config.REDIS_URL),
                     "redis_ok": self.redis_ok, "redis_writable": info["writable"], "wiring": self.wiring.report(), "assets": self.deps.assets.ids(),
                     "halted": info["halted"], "halt_reason": info["reason"],
                     "auth": "bearer token" if self.settings.api_token else "none (set DISPATCH_API_TOKEN)",
                     "replay_allowed": self.settings.allow_replay})
        if not self.settings.api_token:
            log.warning("DISPATCH_API_TOKEN is not set: /kill, /resume and /signal are open to anyone who can reach the port")
        self.orphans = reconcile(self.audit, self.deps.ledger)
        self.ensure_model(force=True)
        self._warm_up()
        if self.settings.monitor_interval_s > 0:
            self.monitor.start(self.settings.monitor_interval_s)

    def _warm_up(self) -> None:
        """D1b: the first LP solve in a process takes seconds (it imports cvxpy and HiGHS), a later one about 30 ms. Pay it at start, not in
        the first cycle. Whatever `wiring.solve` is, if its module has a `warmup(spec)` it is called; a failure only means a slow first cycle."""
        solve = self.wiring.solve
        warm = getattr(sys.modules.get(getattr(solve, "__module__", None) or ""), "warmup", None) if solve is not None else None
        if not callable(warm) or not self.deps.assets.ids():
            return
        try:
            seconds = warm(self.deps.assets.get(self.deps.assets.ids()[0]).spec)
            log.info("optimizer warmed up in %.1f s", seconds)
        except Exception as exc:
            log.warning("optimizer warm-up failed (the first cycle will be slow): %s: %s", type(exc).__name__, exc)

    def shutdown(self) -> None:
        self.monitor.stop()
        try:
            self.redis.close()
        except Exception:
            pass

    def ensure_model(self, force: bool = False) -> None:
        """Load the model for the configured alias if none is loaded. Never raises. Never replaces a loaded model."""
        with self._model_lock:
            if self.deps.forecaster is not None:
                return
            now = time.monotonic()
            if not force and self._model_tried is not None and now - self._model_tried < MODEL_RETRY_S:
                return
            self._model_tried = now
            alias = self.settings.model_alias
            if self.wiring.load_model is None:
                self._model_failed(f"the registry loader is unavailable: {self.wiring.why_not('load_model')}")
                return
            try:
                fc = self.wiring.load_model(alias)
                version = str(fc.version)
                if not version.strip() or not callable(getattr(fc, "predict", None)):
                    raise ValueError("the loaded object has no version or no predict()")
            except Exception as exc:
                self._model_failed(f"no model holds alias {alias!r}: {type(exc).__name__}: {exc}"[:300])
                return
            self.deps.forecaster = fc
            self.deps.unavailable.pop("model", None)
            self.model_error = ""
            self.metrics.set_model(version, alias)
            if self.calibration is not None:               # a restart keeps a breach streak; a NEW model version starts with none
                self.policy.restore({monitor_checks.CHECK_CALIBRATION: self.calibration.sync_model(version)})
            self._audit({"type": "model_load", "ok": True, "alias": alias, "version": version})
            log.info("model %s loaded for alias %s", version, alias)

    def model_status(self) -> Optional[str]:
        """None while the loaded model is still the version its alias names; otherwise why not. A model that was demoted
        (alias cleared) or replaced (alias moved) is a stale model and nothing dispatches with it. A restart loads the new
        one. Asked at most every MODEL_CHECK_S; a registry that cannot be read keeps the last answer."""
        fc = self.deps.forecaster
        if fc is None or self.wiring.aliases is None:
            return None
        now = time.monotonic()
        with self._alias_lock:
            if self._alias_checked is not None and now - self._alias_checked < MODEL_CHECK_S:
                return self._model_block
            self._alias_checked = now
            alias, loaded = self.settings.model_alias, str(fc.version)
            try:
                current = self.wiring.aliases().get(alias)
                self.alias_error = ""
            except Exception as exc:
                self.alias_error = f"{type(exc).__name__}: {exc}"[:200]
                return self._model_block
            if current is None:
                block = f"alias {alias!r} no longer names any version (it was cleared); this service still runs {loaded}"
            elif str(current) != loaded:
                block = f"alias {alias!r} now names version {current}; this service runs {loaded}. Restart it to load the new one"
            else:
                block = None
            if block != self._model_block:
                self._model_block = block
                self._audit({"type": "model_load", "ok": block is None, "alias": alias, "version": loaded,
                             "stale": block, "note": "alias check"})
                (log.error if block else log.info)("model check: %s", block or "the loaded model is current again")
            return block

    def _model_failed(self, why: str) -> None:
        self.model_error = why
        self.deps.unavailable["model"] = why
        self.metrics.set_model(None, self.settings.model_alias)
        if why != self._last_model_audit:                  # one record per distinct failure, not one a minute
            self._last_model_audit = why
            self._audit({"type": "model_load", "ok": False, "alias": self.settings.model_alias, "error": why})
        log.warning("%s", why)

    def _audit(self, event: dict) -> Optional[str]:
        try:
            return self.audit.append(event)
        except (AuditError, ValueError, OSError) as exc:
            self.metrics.audit_error()
            log.error("audit append failed: %s", exc)
            return None

    # -- views
    def model_version(self) -> Optional[str]:
        fc = self.deps.forecaster
        return None if fc is None else str(getattr(fc, "version", "") or "") or None

    def health(self) -> dict:
        self.model_status()                                # refreshes model_stale (rate limited), so /health is not older than the last cycle
        info = self.halt.info()
        last = {a: r for a, r in self.deps.last_results.items()}
        newest = max(last.values(), key=lambda r: r.started_utc) if last else None
        try:
            head_seq, head_hash = self.audit.head()
            anchor = self.read_anchor()
            audit = {"head_seq": head_seq, "head_hash": head_hash[:16], "ok": True, "anchor_seq": anchor[0] if anchor else None}
            if anchor and (anchor[0] > head_seq):
                audit["ok"] = False
                audit["problem"] = "the log is behind its anchor (tail truncated?)"
        except AuditError as exc:
            audit = {"ok": False, "problem": str(exc)[:200]}
        wiring = self.wiring.report()
        degraded = (not all(w["ok"] for k, w in wiring.items() if k not in OPTIONAL_TARGETS) or self.deps.forecaster is None
                    or self._model_block is not None or not audit["ok"] or (info["redis_ok"] and not info["writable"]))
        return to_jsonable({
            "status": "halted" if info["halted"] else "degraded" if degraded else "ok",
            "model_version": self.model_version(),
            "model_alias": self.settings.model_alias,
            "model_error": self.model_error or None,
            "model_stale": self._model_block, "model_alias_check_error": self.alias_error or None,
            "feature_age_s": None if newest is None else newest.feature_age_s,
            "last_cycle_status": None if newest is None else newest.status,
            "last_cycle": None if newest is None else {"asset_id": newest.asset_id, "status": newest.status,
                                                       "reasons": list(newest.reasons), "started_utc": newest.started_utc,
                                                       "latency_s": newest.latency_s},
            "halted": info["halted"], "halt": {k: info[k] for k in ("reason", "set_by", "set_utc", "persisted")},
            "redis_ok": info["redis_ok"], "redis_writable": info["writable"], "redis": redact_url(config.REDIS_URL),
            "wiring": wiring, "audit": audit,
            "assets": self.deps.assets.ids(),
            "disclosure_assets": sorted(self.disclosure_assets),
            "calibration": None if self.calibration is None else {
                "streak": self.calibration.streak, "last_skip": self.calibration.last_skip or None,
                "last_evaluation": None if self.calibration.last_outcome is None else self.calibration.last_outcome.as_dict()},
            "forecast_log": {"errors": self.forecast_log.errors, "last_error": self.forecast_log.last_error or None},
            "unexecuted_decisions_at_startup": len(self.orphans),
            "monitoring": {"policy": self.policy.status(),
                           "last_pass": None if self.monitor.last_report is None else {
                               "ran_utc": self.monitor.last_report.ran_utc, "status": self.monitor.last_report.status}},
            "auth": "bearer token" if self.settings.api_token else "none",
            "checked_utc": self.clock()})


def _bearer_ok(authorization: Optional[str], token: str) -> bool:
    scheme, _, supplied = (authorization or "").partition(" ")
    return scheme.lower() == "bearer" and secrets.compare_digest(supplied.encode(), token.encode())


def public_health(h: dict) -> dict:
    """What /health shows a caller without the token (a Docker healthcheck, a load balancer): the state of the service, without
    file paths, error texts, who halted it or why."""
    out = {k: h[k] for k in ("status", "model_version", "model_alias", "model_stale", "feature_age_s", "last_cycle_status", "halted",
                              "redis_ok", "redis_writable", "assets", "auth", "checked_utc") if k in h}
    out["wiring"] = {k: {"ok": v["ok"]} for k, v in h["wiring"].items()}
    out["audit"] = {"ok": h["audit"]["ok"]}
    out["detail"] = "send the bearer token for halt reasons, error texts and wiring detail"
    return out


def get_state(request: Request) -> AppState:
    st = getattr(request.app.state, "dispatch", None)
    if st is None:
        raise HTTPException(503, "the service has not finished starting")
    return st


def require_token(request: Request, authorization: Optional[str] = Header(default=None)) -> None:
    token = get_state(request).settings.api_token
    if not token:
        return
    if not _bearer_ok(authorization, token):
        raise HTTPException(401, "missing or wrong bearer token", headers={"WWW-Authenticate": "Bearer"})


# ---- the application
def create_app(*, wiring: Optional[Wiring] = None, redis_client=None, assets=None, ledger=None, audit=None, metrics=None,
               model_alias: Optional[str] = None, monitor_interval_s: Optional[float] = None, api_token: Optional[str] = None,
               allow_replay: Optional[bool] = None, clock: Optional[Callable] = None, attribution_inputs=None,
               monitor_providers: Optional[dict] = None, price_source=None, backtest_fn=None, capture_baseline=None,
               forecast_fn=None, forecast_log=None, disclosure_assets=None, clip_attribution: Optional[bool] = None) -> FastAPI:
    """Build the app. Every argument is optional; tests pass fakes, the real service passes nothing and reads the environment.

    price_source(spec, start, end) -> PriceFrame and backtest_fn(spec, start, end) -> revenue_usd are what the data lake and
    D1b's harness provide for /attribution, the capture-rate check, the Gate 3 slippage check and the daily calibration check. When
    they are not passed they are bound from the real modules (D0's `data.prices.price_source`, D1b's `make_backtest_fn` over D1a's
    `make_forecast_fn`); when those cannot be bound the checks stay unavailable (skipped, 501) and everything else works.
    `forecast_log` is where each cycle's issued quantiles go (default: runtime/forecasts next to the audit log); `disclosure_assets`
    {asset_id: DisclosureAsset} are the public pilots /attribution can answer for (default: the assets file's `disclosure_assets`);
    `clip_attribution` clips a paper asset's window to its first executed and last settled interval (default: on with the lake's prices)."""
    settings = Settings.from_env(model_alias=model_alias, monitor_interval_s=monitor_interval_s, api_token=api_token,
                                 allow_replay=allow_replay)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        def build():                                    # imports the other agents' modules, reads files: blocking, so off the event loop
            return AppState(settings, wiring=wiring, redis_client=redis_client, assets=assets, ledger=ledger, audit=audit,
                            metrics=metrics, clock=clock, attribution_inputs=attribution_inputs,
                            monitor_providers=monitor_providers, price_source=price_source, backtest_fn=backtest_fn,
                            capture_baseline=capture_baseline, forecast_fn=forecast_fn, forecast_log=forecast_log,
                            disclosure_assets=disclosure_assets, clip_attribution=clip_attribution)
        state = await run_in_threadpool(build)
        await run_in_threadpool(state.startup)          # Redis ping, model load
        app.state.dispatch = state
        try:
            yield
        finally:
            await run_in_threadpool(state.shutdown)

    app = FastAPI(title="ERCOT battery dispatch service", version="0.1.0", lifespan=lifespan,
                  description="Paper dispatch behind a deterministic risk guard, a Redis kill switch and an append-only audit log.")

    @app.post("/signal")
    def signal(req: SignalRequest, state: AppState = Depends(get_state), _: None = Depends(require_token)):
        if req.asset_id not in state.deps.assets:
            raise HTTPException(404, f"unknown asset {req.asset_id!r}; known: {state.deps.assets.ids()}")
        now = None
        if req.now_utc is not None:
            now = pd.Timestamp(req.now_utc).tz_convert("UTC")
            if abs((now - state.clock()).total_seconds()) > REPLAY_TOLERANCE_S and not state.settings.allow_replay:
                raise HTTPException(403, "now_utc is far from the server clock: replays need DISPATCH_ALLOW_REPLAY=1 "
                                         "(and a separate DISPATCH_HOME, so replays do not enter the live paper ledger)")
        state.ensure_model()
        res = run_cycle(req.asset_id, now, state.deps)
        return JSONResponse(cycle_to_json(res), status_code=503 if res.status == "error" else 200)

    @app.post("/kill")
    def kill(req: KillRequest, state: AppState = Depends(get_state), _: None = Depends(require_token)):
        state.halt.set(req.reason, req.set_by)           # first: stop dispatch. Everything after this is bookkeeping.
        state.metrics.set_halted(True)
        info = state.halt.info()
        h = state._audit({"type": "kill", "reason": req.reason, "set_by": req.set_by, "persisted": info["persisted"]})
        out = {"halted": True, "reason": req.reason, "set_by": req.set_by, "set_utc": info["set_utc"],
               "persisted": info["persisted"], "audit_hash": h}
        if not info["persisted"]:
            out["warning"] = "Redis refused the flag; this process holds the kill and dispatch is halted, but it will not survive a restart"
        if h is None:
            out["warning"] = (out.get("warning", "") + " The kill could not be written to the audit log.").strip()
        return out

    @app.post("/resume")
    def resume(req: ResumeRequest, state: AppState = Depends(get_state), _: None = Depends(require_token)):
        rep = state.audit.verify_report(expected_head=state.read_anchor())
        if not rep.ok:
            raise HTTPException(409, {"reason": "audit_chain_broken", "first_bad_line": rep.first_bad_line, "file": rep.file,
                                      "problem": rep.reason, "hint": "dispatch does not resume while the audit log cannot be trusted"})
        was_halted, _reason = state.halt.is_set()
        try:
            state.halt.clear(req.reason, req.cleared_by)
        except Exception as exc:
            raise HTTPException(503, f"cannot resume: {exc}") from exc
        state.metrics.set_halted(False)
        h = state._audit({"type": "resume", "reason": req.reason, "cleared_by": req.cleared_by, "was_halted": bool(was_halted),
                          "breach_counts_kept": state.policy.status()["consecutive"]})
        return {"halted": False, "was_halted": bool(was_halted), "cleared_by": req.cleared_by, "audit_hash": h,
                "note": "monitoring breach counts are not reset: if the cause is still there the monitor halts again"}

    @app.get("/health")
    def health(request: Request, state: AppState = Depends(get_state), authorization: Optional[str] = Header(default=None)):
        h = state.health()
        if state.settings.api_token and not _bearer_ok(authorization, state.settings.api_token):
            h = public_health(h)                           # probes get the state, not the paths and error texts
        return JSONResponse(h)

    @app.get("/metrics")
    def metrics_endpoint(state: AppState = Depends(get_state)):
        halted, _ = state.halt.is_set()
        state.metrics.set_halted(halted)
        state.metrics.set_redis_writable(state.halt.writable())
        body, content_type = state.metrics.render()
        return Response(content=body, headers={"Content-Type": content_type})

    @app.get("/attribution")
    def attribution(asset_id: str = Query(min_length=1, max_length=64), start: str = Query(), end: str = Query(),
                    state: AppState = Depends(get_state), _: None = Depends(require_token)):
        pilot = state.disclosure_assets.get(asset_id)
        if asset_id not in state.deps.assets and pilot is None:
            raise HTTPException(404, f"unknown asset {asset_id!r}")
        if state.wiring.attribute is None:
            return JSONResponse({"reason": "attribution_engine_unavailable", "detail": state.wiring.why_not("attribute"),
                                 "target": "dispatch.attribution.engine.attribute"}, status_code=501)
        if pilot is None and state.attribution_inputs is None:
            return JSONResponse({"reason": "attribution_inputs_unavailable",
                                 "detail": "no provider of realised prices and forecasts is wired into the service"}, status_code=501)
        try:
            t0, t1 = _utc_ts(start).floor("15min"), _utc_ts(end).floor("15min")        # the engine and the price source work on whole 15-minute intervals
        except (ValueError, TypeError) as exc:
            raise HTTPException(422, f"start and end must be ISO dates or datetimes: {exc}") from exc
        if not t0 < t1 or (t1 - t0) > pd.Timedelta(days=MAX_ATTRIBUTION_DAYS):
            raise HTTPException(422, f"need start < end and at most {MAX_ATTRIBUTION_DAYS} days")
        spec = pilot.spec if pilot is not None else state.deps.assets.get(asset_id).spec
        try:
            got = state.disclosure_inputs(pilot, t0, t1) if pilot is not None else state.attribution_inputs(spec, t0, t1)
            actual, prices, forecast_fn_used, *extra = got
            df = state.wiring.attribute(spec, actual, prices, forecast_fn_used, **(extra[0] if extra else {}))
        except LookupError as exc:
            return JSONResponse({"reason": "no_data", "detail": str(exc)[:300]}, status_code=404)
        except Exception as exc:
            if type(exc).__name__ == "InSampleError":                 # D1a: the model was trained on that period; refusing is the point
                return JSONResponse({"reason": "in_sample_forecasts", "detail": str(exc)[:300]}, status_code=422)
            return JSONResponse({"reason": "attribution_failed", "detail": f"{type(exc).__name__}: {exc}"[:300]}, status_code=502)
        gap = float(df["gap_usd"].sum()) if "gap_usd" in df else float("nan")
        unexplained = float(df["unexplained_usd"].sum()) if "unexplained_usd" in df else float("nan")
        closure = abs(unexplained) / abs(gap) if gap and math.isfinite(gap) and math.isfinite(unexplained) else None
        rows = df.to_dict(orient="records") if "interval_start_utc" in df.columns else df.reset_index().to_dict(orient="records")
        window = None
        if "interval_start_utc" in df.columns and len(df):
            window = {"start": pd.Timestamp(df["interval_start_utc"].min()), "end": pd.Timestamp(df["interval_start_utc"].max()) + pd.Timedelta(minutes=15)}
        return JSONResponse(to_jsonable({"asset_id": asset_id, "kind": "disclosure" if pilot is not None else "paper",
                                         "source": getattr(actual, "source", None), "start": t0, "end": t1, "window": window,
                                         "n_intervals": int(len(df)), "closure": closure, "shadow": forecast_fn_used is not None,
                                         "notes": list(getattr(actual, "notes", ()) or ()), "summary": df.attrs.get("summary"), "rows": rows}))

    return app


def _runtime_dir(sub: str) -> Optional[Path]:
    """<DISPATCH_RUNTIME_DIR>/<sub>, or None (the default under DISPATCH_HOME/runtime) when the variable is not set."""
    root = os.environ.get("DISPATCH_RUNTIME_DIR", "").strip()
    return Path(root) / sub if root else None


def _safe_disclosure() -> dict:
    """The assets file's public pilots; an unreadable or invalid list is logged and means none (paper dispatch does not depend on it)."""
    try:
        return load_disclosure_assets()
    except Exception as exc:
        log.warning("disclosure assets not loaded: %s: %s", type(exc).__name__, exc)
        return {}


def _utc_ts(text: str) -> pd.Timestamp:
    ts = pd.Timestamp(text)
    return ts.tz_localize("UTC") if ts.tzinfo is None else ts.tz_convert("UTC")
