"""Fakes for every interface D2 codes against, and a small synthetic market, so the whole service (cycle, API, monitoring)
runs and is tested without D0's online store, D1a's registry or D1b's LP.

    FakeOnlineStore   contracts.OnlineStore     features for a synthetic diurnal price curve, point in time on request
    FakeForecaster    contracts.Forecaster      P10/P50/P90 around that curve; knobs for failure, leakage, bad frames
    FakeOptimizer     contracts.Optimizer       a threshold heuristic that respects power and SOC; knobs for status, errors, overdrive
    FakeHalt          contracts.HaltFlag        in memory, with a switch that makes it fail
    RecordingQSE      execution.qse.QSEAdapter  records calls (the cycle must never make one)
    fake_attribute / fake_freshness             stand-ins for D1b's engine and D0's catalog

    make_world(tmp_path)     a CycleDeps with all of the above, real audit log and paper ledger on tmp_path
    make_app(tmp_path)       a FastAPI app on fakeredis, wired to the fakes
    make_demo_app()          the same on the real local Redis and the real DISPATCH_HOME, for the manual uvicorn check:
                             python -m uvicorn --app-dir tests fakes:make_demo_app --factory
The fakes are deliberately simple and deterministic; they are not the optimizer or the model.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import fakeredis
import numpy as np
import pandas as pd

from dispatch import units
from dispatch.config import TZ_MARKET
from dispatch.contracts import BatterySpec, ForecastFrame, Plan, as_award_col
from dispatch.execution.audit import AuditLog
from dispatch.execution.ledger import PaperLedger
from dispatch.guard.guard import project_soc_end
from dispatch.monitoring.metrics import Metrics
from dispatch.service.assets import AssetRegistry
from dispatch.service.cycle import CycleDeps
from dispatch.service.wiring import Wiring

NOW = pd.Timestamp("2026-10-10 17:07:00", tz="UTC")       # 12:07 CDT, inside the 17:00 interval: midday solar, prices low, so the fake optimizer charges
STEP = pd.Timedelta(minutes=15)
HORIZON = 96
SP = "HB_NORTH"
ASSET = "PILOT_HB_NORTH"


# ================================================================================================ the market
class Market:
    """A deterministic day: cheap around midday solar, expensive in the evening peak. Realised = forecast x a wiggle."""

    def __init__(self, noise: float = 0.10, seed: int = 7):
        self.noise, self.seed = noise, seed

    def forecast(self, ts) -> np.ndarray:
        idx = pd.DatetimeIndex(ts)
        local = idx.tz_convert(TZ_MARKET)
        h = local.hour.to_numpy() + local.minute.to_numpy() / 60.0
        return 42.0 + 70.0 * np.exp(-((h - 19.0) / 2.2) ** 2) - 30.0 * np.exp(-((h - 13.0) / 2.8) ** 2) + 8.0 * np.exp(-((h - 7.5) / 1.5) ** 2)

    def realised(self, ts) -> pd.DataFrame:
        """A PriceFrame (rt and the five AS prices) for the given 15-minute starts."""
        idx = pd.DatetimeIndex(ts)
        base = self.forecast(idx)
        wiggle = np.array([_unit(self.seed, int(t.value // 10**9)) for t in idx])
        rt = base * (1.0 + self.noise * (2 * wiggle - 1.0)) + 3.0 * (wiggle - 0.5)
        out = {"rt_usd_per_mwh": rt}
        for k, p in enumerate(("regup", "regdn", "rrs", "ecrs", "nspin")):
            out[f"as_{p}_usd_per_mw_h"] = 2.0 + 0.5 * k + 1.5 * wiggle
        return pd.DataFrame(out, index=pd.DatetimeIndex(idx, name="interval_start_utc"))


def _unit(seed: int, n: int) -> float:
    return int(hashlib.sha256(f"{seed}:{n}".encode()).hexdigest()[:8], 16) / 0xFFFFFFFF


def grid(start: pd.Timestamp, n: int = HORIZON) -> pd.DatetimeIndex:
    return pd.date_range(start.floor("15min"), periods=n, freq="15min", tz="UTC", name="interval_start_utc")


# ================================================================================================ online store
class FakeOnlineStore:
    """contracts.OnlineStore. `populate` writes the features a decision at `now` would see; `age_s` is how long ago the
    newest datum was published. Knobs: `error` makes every read raise, `empty` makes read_latest report nothing."""

    def __init__(self, market: Optional[Market] = None):
        self.market = market or Market()
        self.data: dict = {}
        self.error: Optional[Exception] = None
        self.reads = 0

    def populate(self, sp: str, now: pd.Timestamp, *, age_s: float = 120.0, horizon: int = HORIZON, publish_shift_s: float = 0.0) -> None:
        idx = grid(now, horizon)
        hint = self.market.forecast(idx)
        max_pub = now - pd.Timedelta(seconds=age_s) + pd.Timedelta(seconds=publish_shift_s)
        df = pd.DataFrame({"price_hint": hint, "hour": idx.tz_convert(TZ_MARKET).hour.to_numpy().astype(float),
                           "load_mw": 40000 + 9000 * np.sin(np.arange(horizon) / 15.0),
                           "asof_utc": now, "max_published_utc": max_pub}, index=idx)
        self.data[sp] = (df, now, max_pub)

    def write_latest(self, sp: str, df: pd.DataFrame) -> None:
        self.data[sp] = (df, df["asof_utc"].iloc[0], df["max_published_utc"].max())

    def read_latest(self, sp: str):
        self.reads += 1
        if self.error is not None:
            raise self.error
        if sp not in self.data:
            raise KeyError(sp)
        return self.data[sp]


class LiveFakeStore(FakeOnlineStore):
    """For the manual uvicorn check: every read builds features for the wall clock, as a healthy feed would."""

    def read_latest(self, sp: str):
        self.populate(sp, pd.Timestamp.now(tz="UTC"), age_s=90.0)
        return super().read_latest(sp)


# ================================================================================================ forecaster
class FakeForecaster:
    """contracts.Forecaster around the market curve. Knobs: fail (raise), leak_s (claims data published after the decision),
    nan (a hole in the P50), version, with_as (add AS P50 columns), settlement_point."""

    def __init__(self, version: str = "fake-v1", *, fail: Optional[Exception] = None, leak_s: float = 0.0, nan: bool = False,
                 with_as: bool = False, settlement_point: str = SP, reference: Optional[pd.DataFrame] = None):
        self.version, self.fail, self.leak_s, self.nan = version, fail, leak_s, nan
        self.with_as, self.settlement_point = with_as, settlement_point
        self.reference_frame = reference
        self.calls = []

    def predict(self, features: pd.DataFrame, issued_utc: pd.Timestamp) -> ForecastFrame:
        self.calls.append((features, issued_utc))
        if self.fail is not None:
            raise self.fail
        p50 = features["price_hint"].to_numpy(float).copy()
        if self.nan:
            p50[3] = np.nan
        cols = {"rt_p10": p50 * 0.8 - 5.0, "rt_p50": p50, "rt_p90": p50 * 1.3 + 10.0}
        if self.with_as:
            for k, p in enumerate(("regup", "regdn", "rrs", "ecrs", "nspin")):
                for q, f in (("p10", 0.7), ("p50", 1.0), ("p90", 1.5)):
                    cols[f"as_{p}_{q}"] = np.full(len(features), (2.0 + 0.5 * k) * f)
        frame = pd.DataFrame(cols, index=features.index)
        max_pub = features["max_published_utc"].max() if "max_published_utc" in features else issued_utc
        return ForecastFrame(issued_utc=issued_utc, settlement_point=self.settlement_point, model_version=self.version,
                             frame=frame, max_feature_published_utc=pd.Timestamp(max_pub) + pd.Timedelta(seconds=self.leak_s))


# ================================================================================================ optimizer
class FakeOptimizer:
    """contracts.Optimizer: charge in the cheap quarter of the horizon, discharge in the dear quarter, never leave the SOC window.

    Knobs: `status` (anything but "optimal" returns an empty plan with that status), `raises`, `overdrive` (multiplies the
    first interval's discharge, to break the power limit), `first` (force {"net_mw":.., "as": {..}} on the first interval),
    `calls` (every call's arguments)."""

    def __init__(self, *, status: str = "optimal", raises: Optional[Exception] = None, overdrive: float = 1.0,
                 first: Optional[dict] = None, quantiles=(0.3, 0.7)):
        self.status, self.raises, self.overdrive, self.first, self.q = status, raises, overdrive, first, quantiles
        self.calls = []

    def __call__(self, spec: BatterySpec, prices: pd.DataFrame, soc0_mwh: float, *, mode: str, allow_as: bool = True,
                 availability=None, fixed_as=None, terminal_soc_mwh=None) -> Plan:
        self.calls.append(dict(spec=spec, prices=prices, soc0=soc0_mwh, mode=mode))
        if self.raises is not None:
            raise self.raises
        rt = prices["rt_usd_per_mwh"].to_numpy(float)
        T = len(rt)
        c, d = np.zeros(T), np.zeros(T)
        if self.status == "optimal":
            lo, hi = np.quantile(rt, self.q)
            soc = soc0_mwh
            for t in range(T):
                if rt[t] <= lo and rt[t] / spec.rte + spec.wear_usd_per_mwh < hi:
                    c[t] = max(min(spec.power_mw, (spec.soc_hi_mwh - soc) / (spec.eta_charge * units.energy_mwh(1.0))), 0.0)
                elif rt[t] >= hi:
                    d[t] = max(min(spec.power_mw, (soc - spec.soc_min_mwh) * spec.eta_discharge / units.energy_mwh(1.0)), 0.0)
                soc = project_soc_end(spec, soc, d[t] - c[t])
            if self.first is not None:
                net = self.first.get("net_mw", d[0] - c[0])
                c[0], d[0] = max(-net, 0.0), max(net, 0.0)
            d[0] *= self.overdrive
        awards = {p: np.zeros(T) for p in spec.as_products}
        if self.first and self.first.get("as"):
            for p, v in self.first["as"].items():
                awards[p][0] = v
        soc_end = []
        s = soc0_mwh
        for t in range(T):
            s = project_soc_end(spec, s, d[t] - c[t])
            soc_end.append(s)
        frame = pd.DataFrame({"charge_mw": c, "discharge_mw": d, **{as_award_col(p): v for p, v in awards.items()},
                              "soc_end_mwh": soc_end}, index=prices.index)
        frame["energy_usd"] = units.energy_revenue_usd(rt, units.energy_mwh(d - c))
        frame["as_usd"] = 0.0
        frame["wear_usd"] = spec.wear_usd_per_mwh * units.energy_mwh(d)
        sha = hashlib.sha256(np.ascontiguousarray(rt).tobytes() + repr((spec.asset_id, round(soc0_mwh, 9), mode)).encode()).hexdigest()
        ok = self.status == "optimal"
        return Plan(asset_id=spec.asset_id, mode=mode, solver="fake-threshold", status=self.status,
                    objective_usd=float((frame["energy_usd"] - frame["wear_usd"]).sum()) if ok else float("nan"),
                    inputs_sha256=sha, frame=frame)


# ================================================================================================ halt, qse, engine, catalog
class FakeHalt:
    """contracts.HaltFlag in memory. `broken` makes is_set raise (to prove the cycle treats that as a halt)."""

    def __init__(self):
        self.halted, self.reason, self.set_by = False, "", ""
        self.broken = False
        self.history = []

    def is_set(self):
        if self.broken:
            raise ConnectionError("fake halt flag is down")
        return self.halted, self.reason

    def set(self, reason, set_by):
        self.halted, self.reason, self.set_by = True, reason, set_by
        self.history.append(("set", reason, set_by))

    def clear(self, reason, cleared_by):
        self.halted, self.reason = False, ""
        self.history.append(("clear", reason, cleared_by))


class RecordingQSE:
    """execution.qse.QSEAdapter that records what it is asked. The cycle must never ask."""

    def __init__(self):
        self.calls = []

    def submit(self, setpoint):
        self.calls.append(("submit", setpoint))
        return "ref"

    def cancel(self, reference):
        self.calls.append(("cancel", reference))

    def status(self, reference):
        self.calls.append(("status", reference))
        return "accepted"



def fake_attribute(spec, actual, prices, forecasts) -> pd.DataFrame:
    """Stand-in for D1b's engine: a small frame with the AttributionRow columns."""
    idx = pd.date_range("2026-10-01", periods=4, freq="15min", tz="UTC", name="interval_start_utc")
    gap = np.array([10.0, 20.0, 5.0, 15.0])
    return pd.DataFrame({"actual_usd": 1.0, "shadow_usd": 2.0, "pf_usd": 3.0, "gap_usd": gap, "forecast_usd": gap * 0.5,
                         "soc_usd": gap * 0.2, "as_usd": gap * 0.1, "availability_usd": gap * 0.18, "unexplained_usd": gap * 0.02},
                        index=idx)


def fake_attribution_inputs(spec, start, end):
    return object(), pd.DataFrame(), None


def fake_freshness(now_utc) -> dict:
    return {"12301": 120.0, "24898": 200.0, "12331": 3 * 3600.0}


# ================================================================================================ builders
@dataclass
class World:
    deps: CycleDeps
    store: FakeOnlineStore
    forecaster: FakeForecaster
    optimizer: FakeOptimizer
    halt: object
    ledger: PaperLedger
    audit: AuditLog
    metrics: Metrics
    spec: BatterySpec
    market: Market
    now: pd.Timestamp = NOW

    def populate(self, now: Optional[pd.Timestamp] = None, **kw) -> None:
        self.store.populate(self.spec.settlement_point, now or self.now, **kw)


def pilot_spec() -> BatterySpec:
    return AssetRegistry.load().get(ASSET).spec


def make_world(tmp_path, *, halt=None, fsync: bool = False, populate: bool = True, forecaster: Optional[FakeForecaster] = None,
               optimizer: Optional[FakeOptimizer] = None, now: pd.Timestamp = NOW, **deps_kw) -> World:
    market = Market()
    store = FakeOnlineStore(market)
    fc = forecaster if forecaster is not None else FakeForecaster()
    opt = optimizer or FakeOptimizer()
    metrics = Metrics()
    audit = AuditLog(Path(tmp_path) / "audit", fsync=fsync)
    ledger = PaperLedger(Path(tmp_path) / "ledger", fsync=fsync, clock=lambda: now)
    halt = halt if halt is not None else FakeHalt()
    assets = AssetRegistry.load()
    deps = CycleDeps(assets=assets, halt=halt, ledger=ledger, audit=audit, store=store, forecaster=fc,
                     optimizer=opt, metrics=metrics, clock=lambda: now, **deps_kw)
    w = World(deps=deps, store=store, forecaster=fc, optimizer=opt, halt=halt, ledger=ledger, audit=audit, metrics=metrics,
              spec=assets.get(ASSET).spec, market=market, now=now)
    if populate:
        w.populate()
    return w


def make_wiring(world: World, **over) -> Wiring:
    fns = dict(read_latest=world.store.read_latest, load_model=lambda alias: world.forecaster, solve=world.optimizer,
               attribute=None, freshness=fake_freshness, aliases=lambda: {"paper": world.forecaster.version})
    fns.update(over)
    return Wiring.of(**fns)


def make_app(tmp_path, *, world: Optional[World] = None, server=None, wiring: Optional[Wiring] = None, monitor_interval_s: float = 0.0,
             allow_replay: bool = True, now: pd.Timestamp = NOW, **kw):
    """(app, world, server). The app runs on fakeredis; `server.connected = False` simulates a Redis outage."""
    from dispatch.service.app import create_app
    world = world or make_world(tmp_path, now=now)
    server = server or fakeredis.FakeServer()
    redis_client = fakeredis.FakeRedis(server=server, decode_responses=True)
    app = create_app(wiring=wiring or make_wiring(world), redis_client=redis_client, assets=world.deps.assets,
                     ledger=world.ledger, audit=world.audit, metrics=world.metrics, monitor_interval_s=monitor_interval_s,
                     allow_replay=allow_replay, clock=lambda: now, **kw)
    return app, world, server


def make_demo_app():
    """uvicorn --factory entry point for the manual check: the real local Redis and DISPATCH_HOME, fake market and model."""
    from dispatch.service.app import create_app
    market = Market()
    store = LiveFakeStore(market)
    forecaster, optimizer = FakeForecaster("demo-fake-v1"), FakeOptimizer()
    wiring = Wiring.of(read_latest=store.read_latest, load_model=lambda alias: forecaster, solve=optimizer,
                       freshness=fake_freshness, aliases=lambda: {"paper": forecaster.version})
    return create_app(wiring=wiring, monitor_interval_s=0.0)
