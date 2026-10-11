"""Integration: the pieces four builders delivered, wired together and run as one system.

Real, not faked: D0's online store (on fakeredis, the same code that talks to the platform Redis), a real LightGBM quantile model trained on
synthetic prices and REGISTERED AND PROMOTED in a temp MLflow registry (D1a), D1b's LP and attribution engine and Gate 3 backtest, D2's guard,
cycle, paper ledger, audit log, halt flag, API and monitor, and the forecast log and calibration check. Only the market is synthetic: the
prices, and the ERCOT feed, which is replaced by writing point-in-time features into the store before each cycle, exactly as
`flows.prepare_signal` does with the real feed.

  dispatch       a signal runs the whole chain, leaves a complete audit trail and a forecast log line, and the paper ledger agrees
  kill / resume  a kill halts the next cycle, a resume brings it back
  attribution    after a few cycles with realised prices, /attribution returns the engine's rows for the paper asset
  calibration    two bad daily evaluations halt dispatch; one only alerts and asks for a retrain; PSI never halts
  Gate 3         the lazily wired D1b backtest reproduces the paper ledger's revenue
  fail closed    a forecast that cannot be made, or no model, is no dispatch with a reason
  registry       the shared `mlflow_registry` fixture serves a promoted model through the module-level `registry.load`
  audit          the hash chain verifies at the end of every scenario
"""
import json

import fakeredis
import pandas as pd
import pytest
from fastapi.testclient import TestClient

from dispatch.attribution import engine
from dispatch.config import AS_PRODUCTS
from dispatch.execution.audit import AuditLog
from dispatch.execution.ledger import PaperLedger
from dispatch.features import online
from dispatch.models import interim_features as interim
from dispatch.models import replay
from dispatch.models import synthetic
from dispatch.optimizer import backtest as backtest_mod
from dispatch.optimizer import lp
from dispatch.registry import mlflow_registry as R
from dispatch.monitoring.metrics import Metrics
from dispatch.service.app import create_app
from dispatch.service.assets import AssetRegistry
from dispatch.service.wiring import Wiring

SP = "HB_NORTH"
ASSET = "PILOT_HB_NORTH"
SNAP = {"kind": "interim_files", "sha256": "ab" * 32, "n_items": 2}


# ================================================================================================ one model, trained and registered once
@pytest.fixture(scope="module")
def tiny():
    """(forecaster, X, y, decision_utc, history): a real two-second LightGBM model on synthetic prices."""
    return synthetic.fit_tiny_forecaster(SP, n_estimators=20, days=100)


def holdout_payload():
    """Holdout metrics in the schema `promote` reads: the tiny model beats every baseline by more than the paper thresholds."""
    m = {"pinball_mean": 8.5, "mae_p50": 11.0, "coverage_p10_p90": 0.79, "n": 1000}
    base = {"seasonal_naive_1d": {"pinball_mean": 10.0, "mae_p50": 14.0}, "seasonal_naive_7d": {"pinball_mean": 11.0, "mae_p50": 15.0},
            "arena_profile": {"pinball_mean": 9.5, "mae_p50": 13.0}, "profile_median_7d": {"pinball_mean": 9.2, "mae_p50": 12.8}}
    return R.holdout_metrics_payload(SP, m, base)


def register_and_promote(reg, model, aliases=("research", "paper")) -> str:
    run_id = reg.log_run({"model": "tiny"}, holdout_payload(), data_snapshot=SNAP, feature_set="interim_d1a/v1", settlement_points=[SP],
                         name="integration")
    version = reg.register(model, run_id)
    for alias in aliases:
        reg.promote(version, alias, by="integration-test")
    return version


@pytest.fixture(scope="module")
def served(tmp_path_factory, tiny):
    """A temp registry whose `paper` alias holds the tiny model. Read-only for the tests that use it."""
    d = tmp_path_factory.mktemp("mlflow")
    reg = R.Registry(tracking_uri="sqlite:///" + (d / "mlflow.db").as_posix(), artifact_root=d / "artifacts", cache_dir=d / "cache")
    return reg, register_and_promote(reg, tiny[0])


# ================================================================================================ the market and the stack
class Bias:
    """Added to the realised prices: 0 is the market the model was trained on, 80 puts every price far above its P90."""
    value = 0.0


class Clock:
    def __init__(self, now):
        self.now = now

    def __call__(self):
        return self.now


class Stack:
    """The service on fakeredis with the real modules wired in, plus the helpers a scenario needs."""

    def __init__(self, tmp_path, reg, tiny, *, with_backtest=False, alias="paper", load_model=None):
        self.model, _X, _y, _d, self.hist = tiny
        self.bias = Bias()
        self.server = fakeredis.FakeServer()
        self.store = online.OnlineStore(fakeredis.FakeRedis(server=self.server))
        self.t0 = self.hist.end - pd.Timedelta(days=6)                                  # the first decision time of the scenarios
        self.clock = Clock(self.t0)
        self.ledger = PaperLedger(tmp_path / "ledger", fsync=False, clock=self.clock)
        self.audit = AuditLog(tmp_path / "audit", fsync=False)
        self.metrics = Metrics()
        series = self.hist.series()

        def price_source(spec, start, end):
            idx = pd.date_range(start, end, freq="15min", inclusive="left", name="interval_start_utc")
            rt = series.reindex(idx)
            if len(idx) == 0 or rt.isna().any():
                raise LookupError("realised prices are not complete for that window")
            frame = pd.DataFrame({"rt_usd_per_mwh": rt.to_numpy() + self.bias.value}, index=idx)
            for p in AS_PRODUCTS:
                frame[f"as_{p.lower()}_usd_per_mw_h"] = 3.0
            return frame
        self.price_source = price_source
        features = lambda sp, asof, horizon=96: interim.point_in_time(sp, asof, horizon, history=self.hist)
        wiring = Wiring.of(read_latest=self.store.read_latest, load_model=load_model or reg.load, solve=lp.solve, attribute=engine.attribute,
                           freshness=lambda now: {"12301": 100.0, "24898": 100.0, "12331": 3600.0}, aliases=reg.aliases,
                           price_source=price_source,
                           make_forecast_fn=lambda fc, sp: replay.make_forecast_fn(fc, sp, features_fn=features),
                           make_backtest_fn=backtest_mod.make_backtest_fn if with_backtest else None)
        self.app = create_app(wiring=wiring, redis_client=fakeredis.FakeRedis(server=self.server, decode_responses=True),
                              assets=AssetRegistry.load(), ledger=self.ledger, audit=self.audit, metrics=self.metrics, model_alias=alias,
                              monitor_interval_s=0.0, allow_replay=True, clock=self.clock)
        self.client = None

    def __enter__(self):
        self.client = TestClient(self.app).__enter__()
        self.state = self.app.state.dispatch
        return self

    def __exit__(self, *exc):
        self.client.__exit__(*exc)

    # ---- the feed, one cycle at a time
    def decision_time(self, k, every=pd.Timedelta(minutes=15)):
        return self.t0 + every * k + pd.Timedelta(minutes=7)

    def publish(self, asof) -> None:
        """What `flows.prepare_signal` does right before a signal: point-in-time features at the decision time into the online store."""
        feats = interim.point_in_time(SP, asof, 96, history=self.hist)
        feats["asof_utc"] = asof
        self.store.write_latest(SP, feats)

    def signal(self, asof, *, publish=True) -> dict:
        if publish:
            self.publish(asof)
        self.clock.now = asof
        r = self.client.post("/signal", json={"asset_id": ASSET, "now_utc": asof.isoformat()})
        assert r.status_code in (200, 503), r.text
        return r.json()

    def records(self, *types):
        return list(self.audit.read(types=types or None))


@pytest.fixture()
def stack(tmp_path, served, tiny):
    reg, _version = served
    with Stack(tmp_path, reg, tiny) as s:
        yield s


# ================================================================================================ a cycle dispatches
def test_a_signal_runs_the_whole_chain_and_the_paper_ledger_agrees(stack):
    s = stack
    assert s.state.model_version() == "1" and s.state.deps.forecaster.alias == "paper", "the promoted model was loaded from the registry"
    h = s.client.get("/health").json()
    assert h["status"] == "ok" and h["model_alias"] == "paper" and h["wiring"]["solve"]["ok"] and h["wiring"]["price_source"]["ok"]
    asof = s.decision_time(0)
    body = s.signal(asof)
    assert body["status"] == "dispatched", body
    assert body["model_version"] == "1" and body["feature_age_s"] < 20 * 60 and body["guard"]["accepted"] is True
    assert body["setpoint"]["interval_start_utc"].startswith(asof.floor("15min").strftime("%Y-%m-%dT%H:%M")) and body["latency_s"] < 30
    # the audit trail of the cycle, in order, and the chain verifies
    assert [r["type"] for r in s.records()] == ["startup", "model_load", "signal", "guard_decision", "setpoint", "cycle"]
    sig, dec = s.records("signal")[0], s.records("guard_decision")[0]
    assert sig["plan"]["status"] == "optimal" and sig["plan"]["solver"].startswith(("HIGHS", "CLARABEL")) and dec["accepted"] is True
    # the paper ledger holds exactly the setpoint the cycle reported, with the state of charge carried on from the asset's initial SOC
    (entry,) = s.ledger.entries(ASSET)
    assert entry.net_mw == pytest.approx(body["setpoint"]["net_mw"]) and entry.soc_start_mwh == pytest.approx(10.0)
    assert 0.0 <= entry.soc_end_mwh <= 20.0
    # the forecast log keeps what was forecast, with the model's own profile median for the calibration check
    f = s.state.forecast_log.frame(asof.floor("15min"), asof.floor("15min") + pd.Timedelta(days=1), asset_id=ASSET)
    assert len(f) == 96 and (f["model_version"] == "1").all() and f["rt_prof7_med"].notna().all()
    assert (f["rt_p10"] <= f["rt_p50"]).all() and (f["rt_p50"] <= f["rt_p90"]).all()
    assert s.audit.verify() == (True, None)
    # six more cycles: SOC carries from cycle to cycle and everything dispatches
    results = [s.signal(s.decision_time(k))["status"] for k in range(1, 7)]
    assert results == ["dispatched"] * 6
    entries = s.ledger.entries(ASSET)
    assert len(entries) == 7 and all(entries[i + 1].soc_start_mwh == pytest.approx(entries[i].soc_end_mwh) for i in range(6))
    assert s.audit.verify() == (True, None)
    assert s.metrics.registry.get_sample_value("dispatch_cycles_total", {"status": "dispatched"}) == 7


# ================================================================================================ kill and resume
def test_a_kill_halts_the_next_cycle_and_a_resume_brings_it_back(stack):
    s = stack
    assert s.signal(s.decision_time(0))["status"] == "dispatched"
    r = s.client.post("/kill", json={"reason": "integration test: the feed looks wrong", "set_by": "i1"})
    assert r.status_code == 200 and r.json()["halted"] is True and r.json()["persisted"] is True
    halted = s.signal(s.decision_time(1))
    assert halted["status"] == "halted" and halted["reasons"] == ["halted"] and halted["setpoint"] is None
    assert halted["detail"]["halt_reason"] == "integration test: the feed looks wrong"
    assert s.client.get("/health").json()["status"] == "halted" and len(s.ledger.entries(ASSET)) == 1, "nothing executed while halted"
    # the key itself, as `redis-cli` sees it
    raw = fakeredis.FakeRedis(server=s.server, decode_responses=True).get("dispatch:halt")
    assert json.loads(raw)["set_by"] == "i1"
    r = s.client.post("/resume", json={"reason": "feed repaired", "cleared_by": "i1"})
    assert r.status_code == 200 and r.json()["was_halted"] is True, r.text
    assert s.signal(s.decision_time(2))["status"] == "dispatched"
    assert [x["type"] for x in s.records("kill", "resume")] == ["kill", "resume"]
    assert s.audit.verify() == (True, None)


# ================================================================================================ attribution
def test_attribution_returns_the_engines_rows_for_the_paper_asset_after_a_few_cycles(stack):
    s = stack
    for k in range(6):
        assert s.signal(s.decision_time(k))["status"] == "dispatched"
    s.clock.now = s.decision_time(5) + pd.Timedelta(hours=3)                    # the prices of those intervals are final by now
    start, end = s.t0.floor("15min"), s.t0.floor("15min") + pd.Timedelta(hours=1, minutes=30)
    r = s.client.get("/attribution", params={"asset_id": ASSET, "start": start.isoformat(), "end": end.isoformat()})
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["kind"] == "paper" and out["source"] == "paper-ledger" and out["n_intervals"] == 6 and out["shadow"] is True, "a short window runs the shadow"
    assert out["window"]["start"].startswith(start.strftime("%Y-%m-%dT%H:%M")) and out["closure"] in (0.0, None)
    rows = out["rows"]
    assert len(rows) == 6 and {"interval_start_utc", "actual_usd", "shadow_usd", "pf_usd", "gap_usd", "forecast_usd", "soc_usd", "as_usd",
                               "availability_usd", "unexplained_usd"} <= set(rows[0])
    for row in rows:                                                            # the components add up to the gap, interval by interval
        parts = row["forecast_usd"] + row["soc_usd"] + row["as_usd"] + row["availability_usd"] + row["unexplained_usd"]
        assert parts == pytest.approx(row["gap_usd"], abs=1e-6)
    assert sum(r_["pf_usd"] for r_ in rows) >= sum(r_["actual_usd"] for r_ in rows) - 1e-6, "perfect foresight is never worse than what was done"
    assert out["summary"]["closure_basis"] == "revaluation"
    # revenue of the ledger, valued by D2's own accounting, equals the engine's `actual`
    prices = s.price_source(None, start, end)
    spec = AssetRegistry.load().get(ASSET).spec
    assert sum(r_["actual_usd"] for r_ in rows) == pytest.approx(s.ledger.revenue_usd(spec, prices, start_utc=start, end_utc=end), abs=1e-6)
    # a window that starts before the first executed interval is clipped to it (before that there was no paper asset), a window nobody traded
    # in is "no data", and the public pilot is not a paper asset
    wide = s.client.get("/attribution", params={"asset_id": ASSET, "start": (start - pd.Timedelta(days=4)).isoformat(), "end": end.isoformat()})
    assert wide.status_code == 200 and wide.json()["n_intervals"] == 6 and wide.json()["window"]["start"].startswith(start.strftime("%Y-%m-%dT%H:%M"))
    assert wide.json()["shadow"] is True, "after the clip the window is short, so the shadow optimizer runs"
    nothing = s.client.get("/attribution", params={"asset_id": ASSET, "start": (start - pd.Timedelta(days=9)).isoformat(),
                                                   "end": (start - pd.Timedelta(days=8)).isoformat()})
    assert nothing.status_code == 404 and nothing.json()["reason"] == "no_data"
    assert s.client.post("/signal", json={"asset_id": "PAD1_ESS_ESR1"}).status_code == 404, "a disclosure asset never runs a cycle"
    assert s.audit.verify() == (True, None)


# ================================================================================================ calibration
def run_days(s, n_cycles=20):
    """Decisions every 4 hours (a day and a half of the log is enough for half the 7-day window to be settled)."""
    every = pd.Timedelta(hours=4)
    for k in range(n_cycles):
        assert s.signal(s.decision_time(k, every))["status"] == "dispatched"
    return s.decision_time(n_cycles - 1, every)


def test_two_bad_daily_calibration_evaluations_halt_dispatch_and_one_only_alerts(stack):
    s = stack
    last = run_days(s)
    day1 = last + pd.Timedelta(hours=26)                                       # every target interval is settled by then
    s.bias.value = 80.0                                                        # the market moves: every price lands far above the issued P90
    r1 = s.state.monitor.run_once(day1)
    assert r1.status["calibration"] == "breach", r1.status
    assert not r1.outcome.halted_now and s.client.get("/health").json()["halted"] is False, "one bad day is an alert and a retrain request"
    assert r1.outcome.retrain_requested and [x["type"] for x in s.records("retrain_request")] == ["retrain_request"]
    assert s.records("retrain_request")[0]["trains"] is False and s.records("retrain_request")[0]["checks"] == ["calibration"]
    # /attribution over the days the ledger spans is longer than SHADOW_MAX_DAYS: D1b asked for forecast_fn=None, so no shadow run
    s.clock.now = day1
    span = s.client.get("/attribution", params={"asset_id": ASSET, "start": (s.t0 - pd.Timedelta(days=1)).isoformat(), "end": day1.isoformat()})
    assert span.status_code == 200 and span.json()["shadow"] is False and span.json()["n_intervals"] > 3 * 96, span.text[:300]
    assert span.json()["summary"]["closure_basis"] == "revaluation" and span.json()["closure"] in (0.0, None)
    cal = r1.extra["calibration"]["assets"][ASSET]
    assert cal["breach"] and cal["n_intervals"] >= 336 and cal["hit_error"]["p90"] > 0.25 and cal["skill_checked"] is True
    # the same pass: feature PSI flags features (alert only) and the capture check ran on the real LP; neither halts, neither errored
    assert r1.status["feature_drift"] in ("alert", "ok") and not r1.status["capture_decay"].startswith("error"), r1.status
    assert not r1.status["stale_data"].startswith("error") and r1.extra["capture_rate"] > 0
    # a minute-by-minute monitor does not count the same day twice
    for minutes in (1, 2, 60):
        again = s.state.monitor.run_once(day1 + pd.Timedelta(minutes=minutes))
        assert again.status["calibration"].startswith("skipped: next daily evaluation") and not again.outcome.halted_now
    assert s.signal(s.decision_time(30, pd.Timedelta(hours=4)))["status"] == "dispatched", "still dispatching after the first breach"
    # the second consecutive daily breach sets the halt flag
    r2 = s.state.monitor.run_once(day1 + pd.Timedelta(days=1, minutes=5))
    assert r2.status["calibration"] == "breach" and r2.outcome.halted_now, r2.status
    reason = s.client.get("/health").json()["halt"]["reason"]
    assert reason.startswith("auto-halt after 2 consecutive breaching checks: calibration x2"), reason
    halted = s.signal(s.decision_time(31, pd.Timedelta(hours=4)))
    assert halted["status"] == "halted" and "calibration x2" in halted["detail"]["halt_reason"]
    assert [x["set_by"] for x in s.records("halt")] == ["monitor:halt_policy"]
    gauges = s.metrics.registry
    assert gauges.get_sample_value("dispatch_calibration_breach", {"asset": ASSET}) == 1 and gauges.get_sample_value("dispatch_halted") == 1
    assert gauges.get_sample_value("dispatch_monitor_halt_threshold", {"check": "calibration"}) == 2
    # an operator resumes: dispatch carries on (the monitor's passes in between do not set the flag again), but the streak is not forgotten
    assert s.client.post("/resume", json={"reason": "looked at it", "cleared_by": "i1"}).status_code == 200
    quiet = s.state.monitor.run_once(day1 + pd.Timedelta(days=1, minutes=10))
    assert quiet.status["calibration"].startswith("skipped: next daily evaluation") and not quiet.outcome.halted_now
    assert s.client.get("/health").json()["halted"] is False
    assert s.signal(s.decision_time(32, pd.Timedelta(hours=4)))["status"] == "dispatched", "resumed means dispatching"
    # ... so the next daily evaluation, still bad, halts again
    r3 = s.state.monitor.run_once(day1 + pd.Timedelta(days=2, minutes=10))
    assert r3.status["calibration"] == "breach" and r3.outcome.halted_now
    assert s.state.calibration.streak == 3 and s.state.policy.counts["calibration"] == 3
    assert s.audit.verify() == (True, None)


def test_feature_psi_flags_but_never_halts_inside_the_running_service(stack):
    s = stack
    run_days(s, n_cycles=8)
    ran = [s.state.monitor.run_once(s.decision_time(40, pd.Timedelta(hours=4)) + pd.Timedelta(minutes=k)) for k in range(6)]
    assert all(r.status["feature_drift"] in ("alert", "ok") for r in ran), [r.status["feature_drift"] for r in ran]
    assert not any(r.outcome.halted_now for r in ran) and s.client.get("/health").json()["halted"] is False
    assert s.state.policy.counts["feature_drift"] == 0
    psi = {x.labels["feature"]: x.value for fam in s.metrics.registry.collect() for x in fam.samples if x.name == "dispatch_feature_drift_psi"}
    assert psi, "the PSI metric is exported (it feeds the Grafana panel and the alert)"


# ================================================================================================ Gate 3 through the lazily wired D1b backtest
def test_the_lazily_wired_backtest_reproduces_the_paper_ledger(tmp_path, served, tiny):
    reg, _ = served
    with Stack(tmp_path, reg, tiny, with_backtest=True) as s:
        assert s.state.backtest_fn is not None and s.state.monitor.slippage_provider is not None
        for k in range(8):
            assert s.signal(s.decision_time(k))["status"] == "dispatched"
        spec = AssetRegistry.load().get(ASSET).spec
        start, end = s.t0.floor("15min"), s.t0.floor("15min") + pd.Timedelta(hours=2)
        paper = s.ledger.revenue_usd(spec, s.price_source(spec, start, end), start_utc=start, end_utc=end)
        bt = s.state.backtest_fn(spec, start, end)                              # built on first use, with the loaded model's forecasts
        assert bt == pytest.approx(paper, abs=1e-4), f"paper ${paper:.4f} against backtest ${bt:.4f}"
        assert abs(paper - bt) / max(abs(bt), 1.0) < 0.05, "Gate 3: slippage under SLIPPAGE_TOL"
        again = s.state.backtest_fn(spec, start, end)
        assert again == bt and s.state.backtest_fn._bt.last["memo_hits"] == 8, "the second call is eight lookups"
        # the monitor's slippage check reads the same numbers once the window has settled
        s.clock.now = s.t0.floor("15min") + pd.Timedelta(hours=3)                # the window then ends at t0 + 2 h, the last interval the ledger holds
        rep = s.state.monitor.run_once(s.clock.now)
        assert rep.status["slippage"] == "ok" and rep.extra["slippage_ratio"] < 0.05
        assert s.audit.verify() == (True, None)


# ================================================================================================ fail closed
def test_a_forecast_that_cannot_be_made_is_no_dispatch_with_a_reason(stack):
    s = stack
    asof = s.decision_time(0)
    s.publish(asof)
    feats, a, mp = s.store.read_latest(SP)
    broken = feats.drop(columns=["rt_prof7_med"])                              # the anchor of the model: MissingFeaturesError, a ForecastError
    broken["asof_utc"] = a
    s.store.write_latest(SP, broken)
    s.clock.now = asof
    body = s.client.post("/signal", json={"asset_id": ASSET, "now_utc": asof.isoformat()}).json()
    assert body["status"] == "error" and body["reasons"] == ["forecast_failed"], body
    assert "MissingFeaturesError" in body["detail"]["error"] and body["setpoint"] is None
    assert s.ledger.entries(ASSET) == [] and s.state.forecast_log.errors == 0
    assert s.signal(s.decision_time(1))["status"] == "dispatched", "the next cycle with good features recovers"
    assert s.audit.verify() == (True, None)


def test_no_model_holding_the_alias_is_no_dispatch_with_a_reason(tmp_path, served, tiny):
    reg, _ = served
    empty = R.Registry(tracking_uri="sqlite:///" + (tmp_path / "empty" / "mlflow.db").as_posix(), artifact_root=tmp_path / "empty" / "artifacts")
    with Stack(tmp_path, empty, tiny) as s:
        assert s.state.deps.forecaster is None and s.client.get("/health").json()["status"] == "degraded"
        body = s.signal(s.decision_time(0))
        assert body["status"] == "error" and body["reasons"] == ["no_model"] and "NoModelError" in body["detail"]["why"], body
        assert s.ledger.entries(ASSET) == []
        assert s.audit.verify() == (True, None)


# ================================================================================================ the shared registry fixture
def test_the_shared_registry_fixture_serves_a_promoted_model_through_the_module_level_api(tmp_path, mlflow_registry, tiny):
    """conftest.mlflow_registry: a temp store that `registry.load / promote / aliases` follow, so the service's default wiring finds it."""
    from dispatch import registry
    assert registry.aliases() == {} and not R.default().is_default_store
    version = register_and_promote(mlflow_registry, tiny[0])
    assert registry.aliases() == {"research": version, "paper": version}
    with Stack(tmp_path, mlflow_registry, tiny, load_model=registry.load) as s:       # the module-level function, not the bound method
        assert s.state.model_version() == version
        assert s.signal(s.decision_time(0))["status"] == "dispatched"


# ================================================================================================ the paper loop (ops/run_paper_loop.py)
def load_loop():
    import importlib.util
    from pathlib import Path
    path = Path(__file__).resolve().parents[1] / "ops" / "run_paper_loop.py"
    spec = importlib.util.spec_from_file_location("run_paper_loop", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_the_paper_loop_prepares_the_features_then_posts_a_signal_and_the_service_dispatches(tmp_path, served, tiny, monkeypatch):
    """`ops/run_paper_loop.py` against the running service: prepare_signal (replaced here by the same point-in-time write the real one ends
    with) and then POST /signal, once per settlement point and once per asset."""
    from dispatch.data import flows
    loop = load_loop()
    reg, _ = served
    with Stack(tmp_path, reg, tiny) as s:
        calls = []

        def fake_prepare(sps, *, now_utc=None, ingest=True, **kw):
            calls.append(("prepare", tuple(sps), now_utc, ingest))
            s.publish(now_utc)
            return {"online": {sp: {"rows": 96} for sp in sps}, "elapsed_s": 0.1}
        monkeypatch.setattr(flows, "prepare_signal", fake_prepare)
        assets = loop.load_assets(None)
        assert assets == [(ASSET, SP)]
        t = s.decision_time(0)
        s.clock.now = t
        rows = loop.one_cycle(s.client, "http://testserver", assets, now_utc=t, ingest=False)
        assert calls == [("prepare", (SP,), t, False)]
        (row,) = rows
        assert row["http"] == 200 and row["status"] == "dispatched" and row["asset"] == ASSET and row["settlement_point"] == SP
        assert row["model_version"] == "1" and row["server_latency_s"] > 0 and row["signal_s"] >= row["server_latency_s"] and row["prepare_errors"] is None
        assert len(s.ledger.entries(ASSET)) == 1
        summary = loop.summarize(rows)
        assert summary["cycles"] == 1 and summary["statuses"] == {"dispatched": 1} and summary["server_latency_s"]["p99"] == row["server_latency_s"]
        # a service that is down is reported, not raised
        import httpx
        dead = httpx.Client(transport=httpx.MockTransport(lambda req: (_ for _ in ()).throw(httpx.ConnectError("refused"))))
        (gone,) = loop.one_cycle(dead, "http://127.0.0.1:9", assets, now_utc=t, ingest=False)
        assert gone["status"] == "unreachable" and gone["http"] is None
        # a feed that failed to refresh is not fatal for the loop: it says so and lets the service's age gate decide
        monkeypatch.setattr(flows, "prepare_signal", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("ERCOT unreachable")))
        (late,) = loop.one_cycle(s.client, "http://testserver", assets, now_utc=s.decision_time(1), ingest=False)
        assert late["prepare_errors"]["prepare_signal"].startswith("RuntimeError") and late["status"] in ("dispatched", "stale", "error")
        assert s.audit.verify() == (True, None)


def test_the_paper_loop_fires_at_the_start_of_each_interval_and_summarises_latency():
    loop = load_loop()
    t = pd.Timestamp("2026-10-10 18:07:30", tz="UTC")
    assert loop.DEFAULT_OFFSET_S == 15.0, "at the start of the interval, like the backtest and the training grid"
    assert loop.next_trigger(t, 900, 15) == pd.Timestamp("2026-10-10 18:15:15", tz="UTC")
    assert loop.next_trigger(pd.Timestamp("2026-10-10 18:00:10", tz="UTC"), 900, 15) == pd.Timestamp("2026-10-10 18:00:15", tz="UTC")
    assert loop.next_trigger(pd.Timestamp("2026-10-10 18:00:15", tz="UTC"), 900, 15) == pd.Timestamp("2026-10-10 18:15:15", tz="UTC"), "strictly after now"
    assert loop.next_trigger(t, 900, 240) == pd.Timestamp("2026-10-10 18:19:00", tz="UTC")
    rows = [{"status": "dispatched", "server_latency_s": x, "signal_s": x + 0.01} for x in (0.1, 0.2, 0.3, 0.4, 5.0)] + [{"status": "stale"}]
    out = loop.summarize(rows)
    assert out["cycles"] == 6 and out["statuses"] == {"dispatched": 5, "stale": 1}
    assert out["server_latency_s"]["p50"] == 0.3 and out["server_latency_s"]["max"] == 5.0


# ================================================================================================ serving a model trained on other column names
def test_the_cycle_feeds_the_model_through_the_feature_adapter_and_a_failing_adapter_is_no_dispatch(tmp_path):
    """D0's `compat.for_forecaster` renames rt_v1 columns for a model trained on D1a's interim names. The cycle calls whatever adapter it is given
    (`deps.adapt_features`) before `predict`, gives the drift check the frame the model was fed, and treats a failing adapter as no dispatch."""
    from fakes import ASSET as A, NOW, make_world
    from dispatch.service.cycle import run_cycle
    w = make_world(tmp_path)
    df, asof, mp = w.store.data[SP]
    w.store.data[SP] = (df.rename(columns={"price_hint": "hint_in_d0_names"}), asof, mp)                 # the online store holds the other names
    res = run_cycle(A, NOW, w.deps)
    assert res.status == "error" and res.reasons == ("forecast_failed",) and "KeyError" in res.detail["error"], "the model cannot read the stored frame"
    seen = []

    def rename_back(frame, forecaster):
        seen.append(forecaster.version)
        return frame.rename(columns={"hint_in_d0_names": "price_hint"})
    from dispatch.monitoring.checks import FeatureWindow
    w.deps.adapt_features, w.deps.feature_window = rename_back, FeatureWindow()
    w.populate(NOW + pd.Timedelta(minutes=15))                  # the store holds the model's own names again: the adapter leaves such a frame alone
    w.store.data[SP] = (lambda d, a, m: (d.rename(columns={"price_hint": "hint_in_d0_names"}), a, m))(*w.store.data[SP])
    ok = run_cycle(A, NOW + pd.Timedelta(minutes=15), w.deps)
    assert ok.status == "dispatched" and seen == [w.forecaster.version]
    assert "price_hint" in w.deps.feature_window.frame().columns, "the drift window holds what the model was fed"
    df2, asof2, mp2 = w.store.data[SP]
    w.store.data[SP] = (df2.rename(columns={"price_hint": "hint_in_d0_names"}), asof2, mp2)
    w.deps.adapt_features = lambda frame, forecaster: (_ for _ in ()).throw(RuntimeError("compat shim broke"))
    bad = run_cycle(A, NOW + pd.Timedelta(minutes=30), w.deps)
    assert bad.status == "error" and bad.reasons == ("forecast_failed",) and "compat shim broke" in bad.detail["error"]
    assert len(w.ledger.entries(A)) == 1, "only the cycle with a working adapter executed"
    assert w.audit.verify() == (True, None)


def test_the_point_in_time_forecast_function_builds_d0s_features_and_renames_them_for_the_model(monkeypatch):
    """`wiring.pit_forecast_fn` (what the shadow optimizer, the Gate 3 backtest and /attribution forecast with) is D1a's make_forecast_fn over D0's
    point-in-time builder and D0's compat shim, so a model trained on interim names is asked about the present from rt_v1 frames."""
    from dispatch.features import build
    from dispatch.models.quantile import ForecastError
    from dispatch.service.wiring import pit_forecast_fn
    asked = {}

    def fake_point_in_time(sp, asof, horizon=96, **kw):
        asked.update(sp=sp, asof=asof, horizon=horizon)
        idx = pd.date_range(asof.floor("15min"), periods=3, freq="15min", name="interval_start_utc")
        return pd.DataFrame({"rt_mean_1h": [1.0, 2.0, 3.0], "hour_local": [10, 10, 10], "qtr_hour": [0, 1, 2], "horizon": [1, 2, 3],
                             "max_published_utc": asof - pd.Timedelta(minutes=3), "settlement_point": sp}, index=idx)
    monkeypatch.setattr(build, "point_in_time", fake_point_in_time)

    class InterimModel:
        feature_set, version, models = "interim_d1a/v1", "9", {}
        meta = {}

        def predict(self, feats, issued, *, settlement_point=None):
            self.got = feats
            raise ForecastError("stop here: the columns are what this test looks at")
    m = InterimModel()
    fn = pit_forecast_fn(m, "HB_NORTH")
    with pytest.raises(ForecastError):
        fn(pd.Timestamp("2026-10-10 18:07", tz="UTC"))
    assert asked["sp"] == "HB_NORTH" and asked["horizon"] == 96 and asked["asof"] == pd.Timestamp("2026-10-10 18:07", tz="UTC")
    assert "rt_mean_4" in m.got.columns and list(m.got["rt_mean_4"]) == [1.0, 2.0, 3.0], "D0's rt_mean_1h under the interim name"
    assert m.got["tgt_hod"].tolist() == [10.0, 10.25, 10.5] and "rt_mean_1h" in m.got.columns, "and D0's own names stay"
    m2 = InterimModel()
    m2.feature_set = "rt_v1/1.1.0"
    with pytest.raises(ForecastError):
        pit_forecast_fn(m2, "HB_NORTH")(pd.Timestamp("2026-10-10 18:07", tz="UTC"))
    assert "rt_mean_4" not in m2.got.columns, "a model trained on D0's names is fed D0's frame untouched"


# ================================================================================================ the cost of "localhost" (found in the first live profile)
def test_localhost_becomes_127_0_0_1_so_a_new_connection_does_not_wait_for_the_ipv6_refusal():
    """On this Windows machine `localhost` resolves to ::1 first; the platform's Redis listens on 127.0.0.1 only, so every new connection waited
    about 2.0 s (measured 2.011 s against 0.001 s) and two of the 2.2 s of the first real cycle were that wait."""
    from dispatch.service.state import make_redis_client, normalize_redis_url
    assert normalize_redis_url("redis://localhost:6390/0") == "redis://127.0.0.1:6390/0"
    assert normalize_redis_url("redis://:pw@localhost:6379/2") == "redis://:pw@127.0.0.1:6379/2"
    assert normalize_redis_url("redis://user:pw@LOCALHOST/1") == "redis://user:pw@127.0.0.1/1"
    for unchanged in ("redis://127.0.0.1:6390/0", "redis://redis:6379/0", "redis://cache.internal:6380/2", "redis://[::1]:6390/0", "not a url"):
        assert normalize_redis_url(unchanged) == unchanged
    kw = make_redis_client("redis://localhost:6390/0").connection_pool.connection_kwargs
    assert kw["host"] == "127.0.0.1" and kw["port"] == 6390 and kw["protocol"] == 2


def test_the_real_online_store_function_gets_one_long_lived_client_and_fakes_keep_their_own_transport():
    from dispatch.features import online
    from dispatch.service.wiring import ModuleOnlineStore, Wiring, _features_client, build_deps
    got = {}

    def fake_read(sp, *, client=None):
        got["client"] = client
        return "frame"
    assert ModuleOnlineStore(fake_read).read_latest("A") == "frame" and got["client"] is None
    sentinel = object()
    assert ModuleOnlineStore(fake_read, client=sentinel).read_latest("A") == "frame" and got["client"] is sentinel
    assert _features_client(Wiring.of(read_latest=lambda sp: None)) is None, "a fake manages its own transport"
    bound = online.OnlineStore(fakeredis.FakeRedis()).read_latest
    assert _features_client(Wiring.of(read_latest=bound)) is None, "a store's bound method (same module as D0's function) has its own client and takes no `client=`"
    real = _features_client(Wiring.of(read_latest=online.read_latest))
    assert real is not None and real.connection_pool.connection_kwargs["host"] == "127.0.0.1" and real.connection_pool.connection_kwargs["protocol"] == 2
    store = build_deps(wiring=Wiring.of(read_latest=online.read_latest, solve=lambda *a, **k: None), redis_client=fakeredis.FakeRedis(),
                       halt=object(), ledger=object(), audit=object()).store
    assert isinstance(store, ModuleOnlineStore) and store._client is not None, "the service reads the online store through one pooled connection"
