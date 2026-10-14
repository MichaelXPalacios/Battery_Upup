"""Late binding of the modules other agents own (DESIGN.md, "Interfaces each owner implements").

The service must come up and answer /health even when D0, D1a or D1b have not delivered a module yet, or when one of them
fails to import (a missing wheel, a broken sqlite file). So nothing here is imported at package import time. `Wiring.from_modules()`
imports each target when the service starts and records, per target, whether it worked and why not. A target that is missing
or raises while importing becomes None; the cycle then fails closed with a reason that names it, and /health shows the same.

    name              target                                           signature expected
    read_latest       dispatch.features.online.read_latest             (sp) -> (df, asof_utc, max_published_utc)          D0
    load_model        dispatch.registry.mlflow_registry.load           (alias) -> Forecaster (.version, .predict)         D1a
    solve             dispatch.optimizer.lp.solve                      (spec, prices, soc0, *, mode, ...) -> Plan         D1b
    attribute         dispatch.attribution.engine.attribute            (spec, actual, prices, forecasts) -> DataFrame     D1b
    freshness         dispatch.data.catalog.freshness                  (now_utc) -> {report_id: age_s}                    D0
    aliases           dispatch.registry.mlflow_registry.aliases        () -> {alias: version}                             D1a
                      (not in DESIGN.md's list; same module as `load`. A model whose alias was cleared or moved is a stale model.)
    price_source      dispatch.data.prices.price_source                (spec, start, end) -> PriceFrame, complete or raises  D0
    make_forecast_fn  dispatch.service.wiring.pit_forecast_fn          (forecaster, sp) -> forecast_fn(issued_utc)        D1a over D0 (below)
    make_backtest_fn  dispatch.optimizer.backtest.make_backtest_fn     (price_source, forecast_fn, *, ledger) -> backtest_fn   D1b
    adapt_features    dispatch.features.compat.for_forecaster          (frame, forecaster) -> frame the model reads       D0

The last four are OPTIONAL: without the first three the service dispatches as before, and capture rate, Gate 3 slippage, the daily calibration
check and /attribution stay unavailable (skipped / 501) and say why; without `adapt_features` a model is fed the frame as D0 stored it. A failure to
bind them does not make /health "degraded" by itself.

`make_forecast_fn` is D1a's `models.replay.make_forecast_fn` over D0's point-in-time builder, whatever the model's feature set: `adapt_features`
renames D0's `rt_v1` frame for a model trained on D1a's interim names (registered version 1, kept as a rollback) and leaves a model trained on
rt_v1 names (version 2 on) alone (see `pit_forecast_fn`).

Tests build `Wiring.of(read_latest=fake, ...)` instead and never touch the real modules.
"""
from __future__ import annotations

import importlib
import logging
import threading
from dataclasses import dataclass, field
from typing import Callable, Optional

import pandas as pd

log = logging.getLogger("dispatch.service")

TARGETS = {
    "read_latest": ("dispatch.features.online", "read_latest"),
    "load_model": ("dispatch.registry.mlflow_registry", "load"),
    "solve": ("dispatch.optimizer.lp", "solve"),
    "attribute": ("dispatch.attribution.engine", "attribute"),
    "freshness": ("dispatch.data.catalog", "freshness"),
    "aliases": ("dispatch.registry.mlflow_registry", "aliases"),     # {alias: version}: lets the service notice a demoted or replaced model
    "price_source": ("dispatch.data.prices", "price_source"),
    "make_forecast_fn": ("dispatch.service.wiring", "pit_forecast_fn"),
    "make_backtest_fn": ("dispatch.optimizer.backtest", "make_backtest_fn"),
    "adapt_features": ("dispatch.features.compat", "for_forecaster"),
}
OPTIONAL_TARGETS = frozenset({"attribute", "price_source", "make_forecast_fn", "make_backtest_fn", "adapt_features"})   # their absence is not "degraded"


@dataclass
class ModuleStatus:
    name: str
    target: str
    ok: bool
    detail: str = ""

    def as_dict(self) -> dict:
        return {"ok": self.ok, "target": self.target, "detail": self.detail}


@dataclass
class Wiring:
    read_latest: Optional[Callable] = None
    load_model: Optional[Callable] = None
    solve: Optional[Callable] = None
    attribute: Optional[Callable] = None
    freshness: Optional[Callable] = None
    aliases: Optional[Callable] = None
    price_source: Optional[Callable] = None
    make_forecast_fn: Optional[Callable] = None
    make_backtest_fn: Optional[Callable] = None
    adapt_features: Optional[Callable] = None
    status: dict = field(default_factory=dict)       # name -> ModuleStatus

    @classmethod
    def from_modules(cls) -> "Wiring":
        """Import the real modules one by one. Any failure is recorded, never raised."""
        w = cls()
        for name, (mod_name, attr) in TARGETS.items():
            target = f"{mod_name}.{attr}"
            try:
                fn = getattr(importlib.import_module(mod_name), attr)
                if not callable(fn):
                    raise TypeError(f"{target} is not callable")
                setattr(w, name, fn)
                w.status[name] = ModuleStatus(name, target, True)
            except Exception as exc:                 # ImportError, AttributeError, or anything the module does while importing
                setattr(w, name, None)
                w.status[name] = ModuleStatus(name, target, False, f"{type(exc).__name__}: {exc}"[:300])
                log.warning("wiring: %s unavailable (%s)", target, w.status[name].detail)
        return w

    @classmethod
    def of(cls, **fns) -> "Wiring":
        """Injected functions (tests, demos). Names not given are unavailable."""
        w = cls()
        for name, (mod_name, attr) in TARGETS.items():
            fn = fns.get(name)
            setattr(w, name, fn)
            w.status[name] = ModuleStatus(name, f"{mod_name}.{attr}", fn is not None,
                                          "injected" if fn is not None else "not provided")
        return w

    def why_not(self, name: str) -> str:
        st = self.status.get(name)
        return "" if st is None or st.ok else st.detail or "unavailable"

    def report(self) -> dict:
        return {n: s.as_dict() for n, s in self.status.items()}


class ModuleOnlineStore:
    """Adapts D0's module-level functions to the OnlineStore protocol.

    D0's `online.read_latest(sp)` builds a new Redis client, hence a new TCP connection, on every call. With `client` the connection is kept
    (redis-py pools it) and handed to D0's function, which takes `client=` for exactly this."""

    def __init__(self, read_latest: Callable, write_latest: Optional[Callable] = None, client=None):
        self._read, self._write, self._client = read_latest, write_latest, client

    def read_latest(self, sp: str):
        return self._read(sp) if self._client is None else self._read(sp, client=self._client)

    def write_latest(self, sp: str, df: pd.DataFrame) -> None:
        if self._write is None:
            raise NotImplementedError("this service handle only reads the online store")
        self._write(sp, df)


def pit_forecast_fn(forecaster, settlement_point: str, *, horizon: int = 96) -> Callable:
    """`forecast_fn(issued_utc) -> ForecastFrame` for one settlement point: D0's point-in-time features at the issue time, renamed for the
    model if it was trained on D1a's interim names (D0's `compat.for_forecaster`), then D1a's `forecaster.predict`. It is D1a's
    `models.replay.make_forecast_fn` with that features function, so it keeps the guard against asking a model about the period it was trained on.

    The shadow optimizer, the Gate 3 backtest and /attribution use it: the same model and the same feature code as a live cycle, which reads the
    latest of the same frames from the online store."""
    from ..features import build
    from ..models.replay import make_forecast_fn
    try:
        from ..features.compat import for_forecaster
    except ImportError:                                     # D0's shim is not there: the frame is fed as built
        def for_forecaster(df, _forecaster):
            return df

    def features(sp: str, asof: pd.Timestamp, horizon: int = horizon) -> pd.DataFrame:
        return for_forecaster(build.point_in_time(sp, asof, horizon), forecaster)
    return make_forecast_fn(forecaster, settlement_point, features_fn=features, horizon=horizon)


class LazyBacktest:
    """`backtest_fn(spec, start, end) -> revenue_usd` (D1b's `Backtest`) built when the first call finds a model loaded.

    The service comes up before a model is necessarily loaded (it retries the registry), and a `Backtest` needs the model's point-in-time
    forecast function. This builds it on first use, from whatever `get_forecaster()` holds then, and rebuilds it if the model object changes
    (a restart gets a new process; this covers a test that swaps the model). With no model loaded it raises LookupError, which
    `monitoring.providers.slippage_provider` reads as "no data" and skips, not as a breach: nothing dispatches without a model anyway.

    NEVER call it from a request handler: a cold week is ~670 decisions, ~90 s (D1b, notes/d1b.md section 5.1); it runs on the monitor's
    own thread, and its memo makes every later call about 0.05 s."""

    def __init__(self, get_forecaster: Callable, make_forecast_fn: Callable, make_backtest_fn: Callable, price_source: Callable, ledger, specs):
        self._get, self._make_fc, self._make_bt = get_forecaster, make_forecast_fn, make_backtest_fn
        self._price_source, self._ledger, self._specs = price_source, ledger, list(specs)
        self._built_for = None
        self._bt = None
        self._lock = threading.Lock()

    def _backtest(self):
        fc = self._get()
        if fc is None:
            raise LookupError("no model is loaded: the backtest needs the served model's forecasts")
        with self._lock:
            if self._bt is None or self._built_for is not fc:
                fns = {}
                for spec in self._specs:
                    fn = self._make_fc(fc, spec.settlement_point)
                    fn.version = str(fc.version)               # the backtest's memo is keyed by the model version
                    fns[spec.asset_id] = fn
                self._bt = self._make_bt(self._price_source, fns, ledger=self._ledger)
                self._built_for = fc
            return self._bt

    def __call__(self, spec, start_utc, end_utc, **kw) -> float:
        return self._backtest()(spec, start_utc, end_utc, **kw)


_DEFAULT = {"deps": None}


def _features_client(w: "Wiring"):
    """One long-lived client for D0's real `online.read_latest` (RESP2, 3 s timeouts, IPv4 host: see `state.normalize_redis_url`). None for anything
    else, which manages its own transport (a fake, a file store)."""
    try:
        from .. import config
        from ..features import online
        from .state import normalize_redis_url
        if w.read_latest is not online.read_latest:              # only D0's module-level function takes `client=`; a bound method of a store does not
            return None
        return online.connect(normalize_redis_url(config.REDIS_URL))
    except Exception as exc:                                     # never fatal: without it the function connects for itself, slowly
        log.warning("shared online-store client not built: %s: %s", type(exc).__name__, exc)
        return None


def build_deps(*, wiring: Optional[Wiring] = None, redis_client=None, assets=None, ledger=None, audit=None, metrics=None,
               halt=None, feature_window=None, clock=None, forecast_log=None):
    """CycleDeps from a Wiring (default: the real modules). The model is NOT loaded here; the app does that at startup."""
    from ..execution.audit import AuditLog
    from ..execution.ledger import PaperLedger
    from ..monitoring.checks import FeatureWindow
    from ..monitoring.metrics import Metrics
    from .assets import AssetRegistry
    from .cycle import CycleDeps
    from .state import RedisHaltFlag
    w = wiring or Wiring.from_modules()
    kw = {"clock": clock} if clock is not None else {}
    return CycleDeps(
        assets=assets or AssetRegistry.load(),
        halt=halt or RedisHaltFlag(redis_client),
        ledger=ledger or PaperLedger(),
        audit=audit or AuditLog(),
        store=ModuleOnlineStore(w.read_latest, client=_features_client(w)) if w.read_latest is not None else None,
        optimizer=w.solve,
        metrics=metrics or Metrics(),
        feature_window=feature_window or FeatureWindow(),
        forecast_log=forecast_log,
        adapt_features=w.adapt_features,
        unavailable={n: w.why_not(n) for n in ("read_latest", "solve", "load_model") if w.why_not(n)},
        **kw)


def default_deps():
    """Process-wide deps for callers that do not build their own (`run_cycle(asset, now)` without deps). The model is loaded
    for the alias in DISPATCH_MODEL_ALIAS (default "paper"); if none holds it the cycle fails closed with `no_model`."""
    import os
    if _DEFAULT["deps"] is None:
        from ..monitoring.forecast_log import ForecastLog
        w = Wiring.from_modules()
        deps = build_deps(wiring=w, forecast_log=ForecastLog())
        alias = os.environ.get("DISPATCH_MODEL_ALIAS", "paper")
        if w.load_model is not None:
            try:
                deps.forecaster = w.load_model(alias)
            except Exception as exc:
                deps.unavailable["model"] = f"no model holds alias {alias!r}: {type(exc).__name__}: {exc}"[:300]
        else:
            deps.unavailable["model"] = w.why_not("load_model")
        _DEFAULT["deps"] = deps
    return _DEFAULT["deps"]


def reset_default_deps() -> None:
    _DEFAULT["deps"] = None
