"""The dispatch cycle and the FastAPI service, on fakes (tests/fakes.py).

  cycle     run_cycle on every success and every fail-closed path, with its audit trail and its metrics
  API       /signal, /kill, /resume, /health, /metrics, /attribution through the TestClient on fakeredis
  latency   200 cycles with fakes: p50 and p99 against the PRD target (p99 < 30 s)

Fail closed: when data, model or guard is in doubt, nothing dispatches. Each parametrised case below makes one thing
wrong and checks the same five facts: no setpoint, nothing on the ledger, a `cycle` audit record that says why, an intact
audit chain, and a counter that moved.
"""
import dataclasses
import json
import math
import time

import fakeredis
import redis
import pandas as pd
import pytest
from fastapi.testclient import TestClient
from prometheus_client.parser import text_string_to_metric_families

import fakes
from fakes import (ASSET, NOW, SP, FakeForecaster, RecordingQSE, fake_attribute, fake_attribution_inputs, make_app, make_wiring,
                   make_world)
from dispatch.config import CYCLE_P99_TARGET_S, MAX_FEATURE_AGE_S
from dispatch.contracts import GuardDecision
from dispatch.execution.audit import AuditError
from dispatch.execution.ledger import PaperLedger
from dispatch.service import app as app_module
from dispatch.service import cycle as C
from dispatch.service.assets import AssetRegistry, parse_asset
from dispatch.service.cycle import ServiceCycleResult, replay_cycles, run_cycle
from dispatch.service.reconcile import reconcile, unexecuted_decisions
from dispatch.service.state import HaltUnavailable, RedisHaltFlag, make_redis_client, redact_url
from dispatch.service.wiring import Wiring


# ================================================================================================ helpers
def sample(metrics, name, **labels):
    return metrics.registry.get_sample_value(name, labels or None)


def audit_records(w, *types):
    return [r for r in w.audit.read() if not types or r["type"] in types]


def ledger_lines(w):
    return w.ledger.entries(ASSET, final_only=False) if w.ledger.has_asset(ASSET) else []


def assert_no_dispatch(w, res, status, reason):
    assert isinstance(res, ServiceCycleResult)
    assert res.status == status and reason in res.reasons, (res.status, res.reasons, res.detail)
    assert res.setpoint is None, "a non-dispatch outcome carries no setpoint"
    assert ledger_lines(w) == [], "nothing was executed"
    last = audit_records(w)[-1]
    assert last["type"] == "cycle" and last["status"] == status and reason in last["reasons"]
    assert last["hash"] == res.audit_hash
    assert w.audit.verify() == (True, None)
    assert sample(w.metrics, "dispatch_cycles_total", status=status) >= 1


# ================================================================================================ the cycle: success
def test_a_healthy_cycle_dispatches_and_leaves_a_complete_trail(tmp_path):
    w = make_world(tmp_path)
    res = run_cycle(ASSET, NOW, w.deps)
    assert res.status == "dispatched" and res.reasons == ()
    sp = res.setpoint
    assert sp.asset_id == ASSET and sp.interval_start_utc == NOW.floor("15min") and sp.model_version == "fake-v1"
    assert sp.issued_utc == NOW and sp.net_mw < 0, "midday prices are low: the fake optimizer charges"
    assert abs(sp.net_mw) <= w.spec.power_mw
    assert res.decision.accepted and res.decision.setpoint is sp
    assert res.feature_age_s == pytest.approx(120.0) and res.model_version == "fake-v1"
    # the paper ledger executed it and tracks the SOC
    (entry,) = w.ledger.entries(ASSET)
    assert entry.net_mw == sp.net_mw and entry.soc_start_mwh == 10.0
    assert entry.soc_end_mwh == pytest.approx(10.0 + w.spec.rte * abs(sp.net_mw) * 0.25)
    assert w.ledger.soc_at(ASSET, NOW.floor("15min") + pd.Timedelta(minutes=15)) == pytest.approx(entry.soc_end_mwh)
    # the trail: signal, guard decision (written before execution), executed setpoint, cycle summary
    assert [r["type"] for r in audit_records(w)] == ["signal", "guard_decision", "setpoint", "cycle"]
    sig, dec, exe, cyc = audit_records(w)
    assert sig["plan"]["status"] == "optimal" and sig["proposed"]["net_mw"] == sp.net_mw and sig["features_sha256"]
    assert dec["accepted"] is True and dec["state"]["soc_mwh"] == 10.0 and dec["state"]["halted"] is False
    assert exe["executor"] == "paper_ledger" and exe["guard_audit_hash"] == dec["hash"] and entry.audit_hash == dec["hash"]
    assert cyc["status"] == "dispatched" and res.audit_hash == cyc["hash"]
    assert w.audit.verify() == (True, None)
    # metrics
    assert sample(w.metrics, "dispatch_cycles_total", status="dispatched") == 1
    assert sample(w.metrics, "dispatch_cycle_seconds_count") == 1
    assert sample(w.metrics, "dispatch_feature_age_seconds", asset=ASSET) == pytest.approx(120.0)


def test_the_optimizer_gets_the_p50_view_the_ledger_soc_and_forecast_mode(tmp_path):
    w = make_world(tmp_path)
    run_cycle(ASSET, NOW, w.deps)
    (call,) = w.optimizer.calls
    assert call["mode"] == "forecast" and call["soc0"] == 10.0 and call["spec"] is w.spec
    pred_features, issued = w.forecaster.calls[0]
    assert issued == NOW and len(pred_features) == fakes.HORIZON
    p50 = w.forecaster.predict(pred_features, NOW).price_view("p50")
    pd.testing.assert_frame_equal(call["prices"], p50)


def test_state_of_charge_carries_from_cycle_to_cycle(tmp_path):
    w = make_world(tmp_path)
    r1 = run_cycle(ASSET, NOW, w.deps)
    now2 = NOW + pd.Timedelta(minutes=15)
    w.populate(now2)
    r2 = run_cycle(ASSET, now2, w.deps)
    assert r2.status == "dispatched"
    e1, e2 = w.ledger.entries(ASSET)
    assert e2.soc_start_mwh == pytest.approx(e1.soc_end_mwh), "the second cycle starts from the SOC the first one left"
    assert r2.setpoint.interval_start_utc == r1.setpoint.interval_start_utc + pd.Timedelta(minutes=15)


def test_a_second_cycle_in_the_same_interval_supersedes_the_first(tmp_path):
    w = make_world(tmp_path)
    run_cycle(ASSET, NOW, w.deps)
    later = NOW + pd.Timedelta(minutes=5)
    w.populate(later)
    res = run_cycle(ASSET, later, w.deps)
    assert res.status == "dispatched"
    assert len(w.ledger.entries(ASSET, final_only=False)) == 2 and len(w.ledger.entries(ASSET)) == 1
    assert w.ledger.entries(ASSET)[0].soc_start_mwh == 10.0, "the revision starts from the SOC before the interval, not after the first try"


def test_the_same_decision_time_twice_is_idempotent(tmp_path):
    w = make_world(tmp_path)
    a = run_cycle(ASSET, NOW, w.deps)
    lines = w.ledger.path(ASSET).read_bytes()
    b = run_cycle(ASSET, NOW, w.deps)
    assert a.status == b.status == "dispatched" and a.setpoint == b.setpoint
    assert w.ledger.path(ASSET).read_bytes() == lines, "no second ledger line"


def test_the_cycle_never_touches_a_qse_adapter(tmp_path):
    qse = RecordingQSE()
    w = make_world(tmp_path)
    run_cycle(ASSET, NOW, w.deps)
    assert qse.calls == []                                                  # (there is nothing in deps that could call it)
    assert not hasattr(w.deps, "qse")


# ================================================================================================ the cycle: fail closed
def s_stale(w):
    w.populate(age_s=3600.0)


def s_just_stale(w):
    w.populate(age_s=MAX_FEATURE_AGE_S + 1)


def s_missing(w):
    w.store.data.clear()


def s_empty(w):
    w.store.data[SP] = (pd.DataFrame(), NOW, NOW)


def s_read_error(w):
    w.store.error = RuntimeError("redis timed out")


def s_future(w):
    w.populate(age_s=-3600.0)


def s_no_store(w):
    w.deps.store = None


def s_no_model(w):
    w.deps.forecaster = None


def s_forecast_fail(w):
    w.forecaster.fail = RuntimeError("model exploded")


def s_forecast_nan(w):
    w.forecaster.nan = True


def s_forecast_leak(w):
    w.forecaster.leak_s = 3600.0


def s_wrong_sp(w):
    w.forecaster.settlement_point = "HB_SOUTH"


def s_no_version(w):
    w.forecaster.version = ""


def s_no_optimizer(w):
    w.deps.optimizer = None


def s_optimizer_error(w):
    w.optimizer.raises = RuntimeError("solver crashed")


def s_not_optimal(w):
    w.optimizer.status = "infeasible"


def s_guard_power(w):
    w.optimizer.first = {"net_mw": 15.0}


def s_guard_soc(w):
    w.ledger.init_asset(w.spec, 19.9)                                       # nearly full ...
    w.optimizer.first = {"net_mw": -10.0}                                   # ... and told to charge hard


def s_guard_as(w):
    w.optimizer.first = {"net_mw": 8.0, "as": {"REGUP": 6.0}}               # 8 + 6 > 10 MW


def s_halted(w):
    w.halt.set("operator kill", "ops")


def s_halt_unreadable(w):
    w.halt.broken = True


FAIL_CLOSED = [
    ("features older than the limit", s_stale, "stale", C.FEATURES_STALE),
    ("features one second over the limit", s_just_stale, "stale", C.FEATURES_STALE),
    ("no features at all", s_missing, "stale", C.FEATURES_MISSING),
    ("an empty feature frame", s_empty, "stale", C.FEATURES_MISSING),
    ("the online store raises", s_read_error, "error", C.FEATURE_READ_FAILED),
    ("features published after the decision time", s_future, "error", C.FEATURES_FROM_FUTURE),
    ("the online store is not wired", s_no_store, "error", C.ONLINE_STORE_UNAVAILABLE),
    ("no model holds the alias", s_no_model, "error", C.NO_MODEL),
    ("the forecaster raises", s_forecast_fail, "error", C.FORECAST_FAILED),
    ("a hole in the P50", s_forecast_nan, "error", C.FORECAST_INVALID),
    ("a forecast built on data from the future", s_forecast_leak, "error", C.FORECAST_LEAK),
    ("a forecast for another settlement point", s_wrong_sp, "error", C.FORECAST_INVALID),
    ("a forecast without a model version", s_no_version, "error", C.FORECAST_INVALID),
    ("the optimizer is not wired", s_no_optimizer, "error", C.OPTIMIZER_UNAVAILABLE),
    ("the optimizer raises", s_optimizer_error, "error", C.OPTIMIZER_FAILED),
    ("a plan that is not optimal", s_not_optimal, "error", C.PLAN_NOT_OPTIMAL),
    ("the guard rejects: power", s_guard_power, "rejected", "power_limit"),
    ("the guard rejects: state of charge", s_guard_soc, "rejected", "soc_above_max"),
    ("the guard rejects: ancillary service over the power limit", s_guard_as, "rejected", "as_up_power"),
    ("the halt flag is set", s_halted, "halted", C.HALTED),
    ("the halt flag cannot be read", s_halt_unreadable, "halted", C.HALTED),
]


@pytest.mark.parametrize("name, setup, status, reason", FAIL_CLOSED, ids=[c[0] for c in FAIL_CLOSED])
def test_fail_closed(tmp_path, name, setup, status, reason):
    w = make_world(tmp_path)
    setup(w)
    res = run_cycle(ASSET, NOW, w.deps)
    assert_no_dispatch(w, res, status, reason)


def test_a_stale_cycle_reports_the_age_it_saw(tmp_path):
    w = make_world(tmp_path)
    w.populate(age_s=3600.0)
    res = run_cycle(ASSET, NOW, w.deps)
    assert res.feature_age_s == pytest.approx(3600.0) and res.detail["max_feature_age_s"] == MAX_FEATURE_AGE_S
    assert w.forecaster.calls == [] and w.optimizer.calls == [], "nothing downstream ran on stale data"


def test_features_exactly_at_the_age_limit_are_still_usable(tmp_path):
    w = make_world(tmp_path)
    w.populate(age_s=MAX_FEATURE_AGE_S)
    assert run_cycle(ASSET, NOW, w.deps).status == "dispatched"


def test_a_halted_cycle_does_no_work_at_all(tmp_path):
    w = make_world(tmp_path)
    w.halt.set("kill", "ops")
    res = run_cycle(ASSET, NOW, w.deps)
    assert res.status == "halted" and res.detail["halt_reason"] == "kill"
    assert w.store.reads == 0 and w.forecaster.calls == [] and w.optimizer.calls == []
    assert sample(w.metrics, "dispatch_halted") == 1


def test_a_kill_that_arrives_mid_cycle_wins(tmp_path):
    """The flag is read again right before the guard, so a kill during the solve stops the setpoint."""
    w = make_world(tmp_path)
    inner = w.optimizer

    def kill_while_solving(*a, **kw):
        plan = inner(*a, **kw)
        w.halt.set("kill during the solve", "ops")
        return plan
    w.deps.optimizer = kill_while_solving
    res = run_cycle(ASSET, NOW, w.deps)
    assert res.status == "halted" and "halted" in res.reasons
    assert w.ledger.entries(ASSET) == []
    dec = audit_records(w, "guard_decision")[0]
    assert dec["accepted"] is False and "halted" in dec["reasons"] and dec["state"]["halted"] is True


def test_a_guard_that_wrongly_accepts_while_halted_still_cannot_dispatch(tmp_path):
    w = make_world(tmp_path)
    inner = w.optimizer

    def kill_while_solving(*a, **kw):
        plan = inner(*a, **kw)
        w.halt.set("kill", "ops")
        return plan
    w.deps.optimizer = kill_while_solving
    w.deps.guard = lambda spec, sp, state, *, now_utc: GuardDecision(True, sp, (), now_utc)
    res = run_cycle(ASSET, NOW, w.deps)
    assert res.status == "halted" and w.ledger.entries(ASSET) == []
    assert audit_records(w, "error")[0]["where"] == "guard"


def test_the_guard_verdict_is_on_disk_before_the_ledger_is_written(tmp_path):
    w = make_world(tmp_path)
    seen = {}
    real_record = w.ledger.record

    def spy(spec, setpoint, **kw):
        seen["audit_types_at_execution"] = [r["type"] for r in w.audit.read()]
        return real_record(spec, setpoint, **kw)
    w.ledger.record = spy
    run_cycle(ASSET, NOW, w.deps)
    assert seen["audit_types_at_execution"] == ["signal", "guard_decision"]


def test_if_the_audit_log_cannot_be_written_nothing_executes(tmp_path):
    w = make_world(tmp_path)

    def broken(event):
        raise AuditError("disk full")
    w.audit.append = broken
    res = run_cycle(ASSET, NOW, w.deps)
    assert res.status == "error" and C.AUDIT_FAILED in res.reasons and res.setpoint is None
    assert w.ledger.entries(ASSET) == []
    assert sample(w.metrics, "dispatch_audit_errors_total") >= 1


def test_a_ledger_that_refuses_is_an_error_and_the_decision_stays_on_record(tmp_path):
    w = make_world(tmp_path)
    later = NOW + pd.Timedelta(minutes=30)
    w.populate(later)
    assert run_cycle(ASSET, later, w.deps).status == "dispatched"
    w.populate(NOW)
    res = run_cycle(ASSET, NOW, w.deps)                                     # an interval older than the newest on the ledger
    assert res.status == "error" and C.LEDGER_REFUSED in res.reasons and res.detail["ledger_code"] == "stale_interval"
    assert audit_records(w, "guard_decision")[-1]["accepted"] is True       # the verdict was written first, as designed
    assert len(w.ledger.entries(ASSET)) == 1


def test_a_guard_that_raises_is_an_error_not_a_dispatch(tmp_path):
    w = make_world(tmp_path)

    def boom(*a, **kw):
        raise RuntimeError("guard bug")
    w.deps.guard = boom
    assert_no_dispatch(w, run_cycle(ASSET, NOW, w.deps), "error", C.GUARD_ERROR)


def test_unknown_asset_and_bad_time(tmp_path):
    w = make_world(tmp_path)
    assert_no_dispatch(w, run_cycle("NOPE", NOW, w.deps), "error", C.UNKNOWN_ASSET)
    res = run_cycle(ASSET, pd.Timestamp("2026-10-10 17:07"), w.deps)       # no time zone
    assert res.status == "error" and C.INVALID_TIME in res.reasons


def test_one_cycle_per_asset_at_a_time(tmp_path):
    w = make_world(tmp_path)
    lock = w.deps.lock_for(ASSET)
    assert lock.acquire(blocking=False)
    try:
        res = run_cycle(ASSET, NOW, w.deps)
    finally:
        lock.release()
    assert res.status == "error" and C.CYCLE_IN_PROGRESS in res.reasons and ledger_lines(w) == []
    assert run_cycle(ASSET, NOW, w.deps).status == "dispatched"             # and it works again once the lock is free


def test_a_cycle_that_overran_its_deadline_does_not_execute(tmp_path):
    w = make_world(tmp_path, deadline_s=-1.0)
    assert_no_dispatch(w, run_cycle(ASSET, NOW, w.deps), "error", C.DEADLINE_EXCEEDED)


def test_an_unexpected_exception_anywhere_is_an_error_result(tmp_path):
    w = make_world(tmp_path)
    w.deps.metrics = None

    class Weird:                                                            # a forecast whose price_view explodes
        version = "x"

        def predict(self, f, t):
            raise MemoryError("out of memory")
    w.deps.forecaster = Weird()
    res = run_cycle(ASSET, NOW, w.deps)
    assert res.status == "error" and res.reasons == (C.FORECAST_FAILED,)
    # something no step expects: the ledger blows up in a way it never should
    w2 = make_world(tmp_path / "second")
    w2.ledger.has_asset = lambda asset_id: 1 / 0
    res2 = run_cycle(ASSET, NOW, w2.deps)
    assert res2.status == "error" and res2.reasons == (C.EXCEPTION,) and res2.setpoint is None
    assert audit_records(w2, "error")[0]["where"] == "run_cycle"


def test_the_audit_trail_names_the_reason_for_every_failure(tmp_path):
    for i, (name, setup, status, reason) in enumerate(FAIL_CLOSED):
        w = make_world(tmp_path / f"w{i}")
        setup(w)
        res = run_cycle(ASSET, NOW, w.deps)
        cyc = [r for r in w.audit.read() if r["type"] == "cycle"]
        assert len(cyc) == 1 and cyc[0]["status"] == res.status == status, name
        assert reason in cyc[0]["reasons"], name


def test_replay_runs_the_same_cycle_over_a_period(tmp_path):
    w = make_world(tmp_path)
    times = [NOW + pd.Timedelta(minutes=15 * k) for k in range(8)]
    results = replay_cycles(ASSET, times, w.deps, prepare=lambda t: w.populate(t))
    assert [r.status for r in results] == ["dispatched"] * 8
    assert [r.started_utc for r in results] == times
    assert len(w.ledger.entries(ASSET)) == 8


# ================================================================================================ the halt flag in Redis
def flag(server=None):
    server = server or fakeredis.FakeServer()
    return RedisHaltFlag(fakeredis.FakeRedis(server=server, decode_responses=True),
                         clock=lambda: pd.Timestamp("2026-10-10 17:00", tz="UTC")), server


def test_the_flag_is_one_json_key():
    f, server = flag()
    assert f.is_set() == (False, "")
    f.set("price spike", "alice")
    raw = fakeredis.FakeRedis(server=server, decode_responses=True).get("dispatch:halt")
    assert json.loads(raw) == {"reason": "price spike", "set_by": "alice", "set_utc": "2026-10-10T17:00:00Z"}
    assert f.is_set() == (True, "price spike")
    f.clear("fixed", "bob")
    assert f.is_set() == (False, "") and fakeredis.FakeRedis(server=server).get("dispatch:halt") is None


def test_a_key_set_by_hand_halts_and_deleting_it_resumes():
    f, server = flag()
    r = fakeredis.FakeRedis(server=server, decode_responses=True)
    r.set("dispatch:halt", json.dumps({"reason": "manual", "set_by": "ops", "set_utc": "2026-10-10T17:00:00Z"}))
    assert f.is_set() == (True, "manual")
    r.set("dispatch:halt", "not json")
    assert f.is_set() == (True, "halt flag present but unreadable")         # a value that cannot be read is still a halt
    r.delete("dispatch:halt")
    assert f.is_set() == (False, "")


def test_redis_unreachable_is_reported_as_halted():
    f, server = flag()
    server.connected = False
    halted, reason = f.is_set()
    assert halted and reason.startswith("redis_unreachable")
    assert f.info()["halted"] is True and f.info()["redis_ok"] is False and f.ping() is False
    with pytest.raises(HaltUnavailable):
        f.clear("x", "y")                                                   # cannot resume while Redis is down
    server.connected = True
    assert f.is_set() == (False, "")


def test_a_kill_made_while_redis_is_down_is_not_lost():
    f, server = flag()
    server.connected = False
    f.set("kill during outage", "ops")
    assert f.last_set_persisted is False
    server.connected = True
    assert f.is_set() == (True, "kill during outage"), "the latch survives the outage ..."
    raw = fakeredis.FakeRedis(server=server, decode_responses=True).get("dispatch:halt")
    assert json.loads(raw)["reason"] == "kill during outage", "... and is written back to Redis"
    fakeredis.FakeRedis(server=server).delete("dispatch:halt")
    assert f.is_set() == (False, ""), "once written back it is ordinary Redis state: DEL resumes"


def test_the_redis_client_speaks_resp2_because_the_local_redis_is_3_0():
    """redis-py 8 opens with HELLO (RESP3); Redis 3.0.504, the local one, answers "unknown command 'HELLO'"."""
    client = make_redis_client("redis://:pw@localhost:6379/0")           # builds the client without connecting
    kw = client.connection_pool.connection_kwargs
    assert kw["protocol"] == 2 and kw["decode_responses"] is True and 0 < kw["socket_timeout"] <= 2


class FullRedis(fakeredis.FakeRedis):
    """A Redis at its maxmemory with noeviction: it answers reads and DEL and refuses SET (the local one does, right now)."""

    def set(self, *a, **kw):
        raise redis.exceptions.OutOfMemoryError("command not allowed when used memory > 'maxmemory'.")


def test_a_redis_that_refuses_writes_is_reported_and_a_kill_is_still_held():
    server = fakeredis.FakeServer()
    f = RedisHaltFlag(FullRedis(server=server, decode_responses=True), clock=lambda: pd.Timestamp("2026-10-10 17:00", tz="UTC"))
    assert f.is_set() == (False, "") and f.writable() is False and f.info()["writable"] is False
    f.set("kill while Redis is full", "ops")
    assert f.last_set_persisted is False and f.is_set() == (True, "kill while Redis is full")
    assert f.info()["persisted"] is False
    f.clear("done", "ops")                                               # DEL is allowed on a full Redis
    assert f.is_set() == (False, "")
    ok, _ = flag()
    assert ok.writable() is True and ok.info()["writable"] is True


def test_the_probe_is_cached_and_leaves_no_key_behind():
    f, server = flag()
    assert f.writable() is True
    assert fakeredis.FakeRedis(server=server).get("dispatch:halt:probe") is None
    server.connected = False
    assert f.writable() is True, "cached for the probe interval"
    assert f.writable(max_age_s=0) is False


def test_redis_urls_are_redacted():
    assert redact_url("redis://user:hunter2@cache.internal:6380/2") == "redis://cache.internal:6380/2"
    assert "hunter2" not in redact_url("redis://:hunter2@localhost")


def test_the_cycle_uses_the_real_flag_class_too(tmp_path):
    f, server = flag()
    w = make_world(tmp_path, halt=f)
    assert run_cycle(ASSET, NOW, w.deps).status == "dispatched"
    f.set("kill", "ops")
    assert run_cycle(ASSET, NOW + pd.Timedelta(minutes=15), w.deps).status == "halted"
    f.clear("ok", "ops")
    w.populate(NOW + pd.Timedelta(minutes=15))
    assert run_cycle(ASSET, NOW + pd.Timedelta(minutes=15), w.deps).status == "dispatched"
    server.connected = False
    res = run_cycle(ASSET, NOW + pd.Timedelta(minutes=30), w.deps)
    assert res.status == "halted" and res.detail["halt_reason"].startswith("redis_unreachable")


# ================================================================================================ the real stack
def test_the_cycle_on_the_real_forecaster_online_store_and_lp(tmp_path):
    """No fake of D0, D1a or D1b: D1a's LightGBM QuantileForecaster (a tiny one, fitted on synthetic prices), D0's Redis online
    store (on fakeredis), D1b's LP, then this package's cycle, guard, paper ledger and audit log. It skips until they exist."""
    synthetic = pytest.importorskip("dispatch.models.synthetic")
    online = pytest.importorskip("dispatch.features.online")
    lp = pytest.importorskip("dispatch.optimizer.lp")
    interim = pytest.importorskip("dispatch.models.interim_features")
    pytest.importorskip("lightgbm")
    model, _X, _y, _d, hist = synthetic.fit_tiny_forecaster("HB_NORTH", n_estimators=20, days=100)
    store = online.OnlineStore(fakeredis.FakeRedis(server=fakeredis.FakeServer()))
    w = make_world(tmp_path, forecaster=model, optimizer=lp.solve, populate=False)
    w.deps.store = store
    asof0 = hist.end - pd.Timedelta(days=2) + pd.Timedelta(minutes=7)
    results = []
    for k in range(6):
        asof = asof0 + pd.Timedelta(minutes=15 * k)
        feats = interim.point_in_time("HB_NORTH", asof, 96, history=hist)
        feats["asof_utc"] = asof
        store.write_latest("HB_NORTH", feats)
        if k == 0:
            stored0 = store.read_latest("HB_NORTH")[0].copy()                    # exactly what the first cycle reads
        results.append(run_cycle(ASSET, asof, w.deps))
    assert [r.status for r in results] == ["dispatched"] * 6, [(r.reasons, r.detail) for r in results if r.status != "dispatched"]
    first = results[0]
    assert first.setpoint.interval_start_utc == asof0.floor("15min") and first.model_version == model.version
    assert first.feature_age_s == pytest.approx((asof0 - asof0.floor("15min")).total_seconds()) and first.feature_age_s < MAX_FEATURE_AGE_S
    # the setpoint is exactly what D1b's own helper extracts from the same plan
    entries = w.ledger.entries(ASSET)
    assert len(entries) == 6 and all(entries[i + 1].soc_start_mwh == pytest.approx(entries[i].soc_end_mwh) for i in range(5))
    assert all(0.0 <= e.soc_end_mwh <= w.spec.energy_mwh for e in entries)
    again = lp.solve(w.spec, model.predict(stored0, asof0).price_view("p50"), 10.0, mode="forecast")
    expected = lp.first_setpoint(again, model_version=model.version, issued_utc=asof0)
    assert first.setpoint == expected, (first.setpoint, expected)
    assert w.audit.verify() == (True, None) and len(audit_records(w, "cycle")) == 6


# ================================================================================================ a crash between the verdict and the ledger
def test_a_crash_between_the_audit_verdict_and_the_ledger_write_is_noticed_at_restart(tmp_path):
    w = make_world(tmp_path)
    w.ledger.record = lambda *a, **kw: (_ for _ in ()).throw(SystemExit("power cut"))
    with pytest.raises(SystemExit):
        run_cycle(ASSET, NOW, w.deps)                                       # the process dies after the verdict is on disk
    assert [r["type"] for r in audit_records(w)] == ["signal", "guard_decision"]
    assert ledger_lines(w) == []
    fresh = PaperLedger(w.ledger.dir)                                       # the restarted process
    found = unexecuted_decisions(w.audit, fresh)
    assert len(found) == 1 and found[0]["asset_id"] == ASSET and found[0]["guard_decision_hash"] == audit_records(w, "guard_decision")[0]["hash"]
    assert reconcile(w.audit, fresh) == found
    err = audit_records(w, "error")[-1]
    assert err["where"] == "startup.reconcile" and err["guard_decision_hash"] == found[0]["guard_decision_hash"]
    assert reconcile(w.audit, fresh) == [], "reported once, not at every restart"
    assert w.audit.verify() == (True, None)


def test_a_decision_that_did_execute_is_not_an_orphan(tmp_path):
    w = make_world(tmp_path)
    run_cycle(ASSET, NOW, w.deps)
    assert unexecuted_decisions(w.audit, w.ledger) == []
    # the crash came after the ledger write but before the `setpoint` audit record: the ledger line answers the decision
    w2 = make_world(tmp_path / "second")
    real = w2.audit.append

    def die_on_setpoint(event):
        if event["type"] == "setpoint":
            raise SystemExit("power cut")
        return real(event)
    w2.audit.append = die_on_setpoint
    with pytest.raises(SystemExit):
        run_cycle(ASSET, NOW, w2.deps)
    assert len(w2.ledger.entries(ASSET)) == 1
    assert unexecuted_decisions(w2.audit, w2.ledger) == []


def test_the_service_reports_unexecuted_decisions_at_startup(tmp_path):
    w = make_world(tmp_path / "w")
    w.ledger.record = lambda *a, **kw: (_ for _ in ()).throw(SystemExit("power cut"))
    with pytest.raises(SystemExit):
        run_cycle(ASSET, NOW, w.deps)
    app, _, _ = make_app(tmp_path / "a", world=dataclasses.replace(w, ledger=PaperLedger(w.ledger.dir, clock=lambda: NOW)))
    with TestClient(app) as client:
        assert client.get("/health").json()["unexecuted_decisions_at_startup"] == 1


# ================================================================================================ assets
def test_only_a_paper_or_live_model_may_dispatch(monkeypatch):
    for ok in ("paper", "live"):
        monkeypatch.setenv("DISPATCH_MODEL_ALIAS", ok)
        assert app_module.Settings.from_env().model_alias == ok
    monkeypatch.setenv("DISPATCH_MODEL_ALIAS", "research")
    with pytest.raises(ValueError, match="research model is not approved"):
        app_module.Settings.from_env()
    monkeypatch.delenv("DISPATCH_MODEL_ALIAS")
    assert app_module.Settings.from_env().model_alias == "paper", "the default"


def test_the_pilot_asset_is_in_the_example_file():
    reg = AssetRegistry.load()
    cfg = reg.get(ASSET)
    s = cfg.spec
    assert (s.settlement_point, s.power_mw, s.energy_mwh, s.rte, s.loss_side) == ("HB_NORTH", 10.0, 20.0, 0.85, "charge")
    assert cfg.mode == "paper" and cfg.initial_soc_mwh == 10.0 and 0 < cfg.exposure_cap_usd < math.inf
    assert reg.ids() == [ASSET] and ASSET in reg and "X" not in reg


def test_only_paper_assets_load():
    raw = {"asset_id": "LIVE1", "mode": "live", "settlement_point": "HB_NORTH", "power_mw": 1.0, "energy_mwh": 2.0}
    with pytest.raises(ValueError, match="paper only"):
        parse_asset(raw)
    with pytest.raises(ValueError, match="unknown keys"):
        parse_asset({**raw, "mode": "paper", "colour": "red"})
    with pytest.raises(ValueError, match="initial_soc"):
        parse_asset({**raw, "mode": "paper", "initial_soc_mwh": 5.0})


# ================================================================================================ the API
@pytest.fixture()
def api(tmp_path):
    app, world, server = make_app(tmp_path)
    with TestClient(app) as client:
        yield client, world, server


def post_signal(client, **body):
    return client.post("/signal", json={"asset_id": ASSET, **body})


def test_signal_dispatches(api):
    client, w, _ = api
    r = post_signal(client)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "dispatched" and body["reasons"] == [] and body["asset_id"] == ASSET
    assert body["setpoint"]["net_mw"] < 0 and body["setpoint"]["model_version"] == "fake-v1"
    assert body["guard"]["accepted"] is True and body["feature_age_s"] == pytest.approx(120.0)
    assert body["latency_s"] >= 0 and body["audit_hash"] == w.audit.head()[1]
    assert len(w.ledger.entries(ASSET)) == 1


def test_signal_takes_a_decision_time_for_replays(api):
    client, w, _ = api
    later = NOW + pd.Timedelta(minutes=30)
    w.populate(later)
    body = post_signal(client, now_utc=later.isoformat()).json()
    assert body["status"] == "dispatched" and body["started_utc"].startswith("2026-10-10T17:37")
    assert body["setpoint"]["interval_start_utc"] == "2026-10-10T17:30:00Z"


def test_kill_then_signal_returns_halted(api):
    client, w, server = api
    k = client.post("/kill", json={"reason": "price spike on HB_NORTH", "set_by": "alice"})
    assert k.status_code == 200 and k.json()["halted"] is True and k.json()["persisted"] is True
    raw = fakeredis.FakeRedis(server=server, decode_responses=True).get("dispatch:halt")
    assert set(json.loads(raw)) == {"reason", "set_by", "set_utc"} and json.loads(raw)["set_by"] == "alice"
    r = post_signal(client)
    assert r.status_code == 200 and r.json()["status"] == "halted"
    assert r.json()["detail"]["halt_reason"] == "price spike on HB_NORTH"
    assert ledger_lines(w) == []
    assert w.optimizer.calls == [], "a halted service does not even solve"
    kills = audit_records(w, "kill")
    assert len(kills) == 1 and kills[0]["set_by"] == "alice" and kills[0]["reason"] == "price spike on HB_NORTH"
    h = client.get("/health").json()
    assert h["status"] == "halted" and h["halted"] is True and h["halt"]["set_by"] == "alice"
    assert "dispatch_halted 1.0" in client.get("/metrics").text


def test_resume_clears_the_flag_and_dispatch_continues(api):
    client, w, server = api
    client.post("/kill", json={"reason": "r", "set_by": "alice"})
    r = client.post("/resume", json={"reason": "spike over, checked the feeds", "cleared_by": "bob"})
    assert r.status_code == 200 and r.json()["halted"] is False and r.json()["was_halted"] is True
    assert fakeredis.FakeRedis(server=server).get("dispatch:halt") is None
    assert post_signal(client).json()["status"] == "dispatched"
    resumes = audit_records(w, "resume")
    assert len(resumes) == 1 and resumes[0]["cleared_by"] == "bob" and resumes[0]["was_halted"] is True
    assert "dispatch_halted 0.0" in client.get("/metrics").text


def test_resume_is_refused_while_the_audit_chain_is_broken(api):
    client, w, _ = api
    post_signal(client)
    client.post("/kill", json={"reason": "r", "set_by": "alice"})
    f = w.audit.files()[0]
    lines = f.read_bytes().split(b"\n")
    rec = json.loads(lines[1])
    rec["accepted"] = False
    lines[1] = json.dumps(rec, sort_keys=True, separators=(",", ":")).encode()
    f.write_bytes(b"\n".join(lines))
    r = client.post("/resume", json={"reason": "x", "cleared_by": "bob"})
    assert r.status_code == 409 and r.json()["detail"]["reason"] == "audit_chain_broken" and r.json()["detail"]["first_bad_line"] == 2
    assert client.get("/health").json()["halted"] is True, "it stays halted"


def test_resume_is_refused_when_the_audit_tail_was_cut_off(api):
    client, w, _ = api
    post_signal(client)
    client.post("/kill", json={"reason": "r", "set_by": "alice"})
    f = w.audit.files()[0]
    f.write_bytes(b"\n".join(f.read_bytes().split(b"\n")[:-2]) + b"\n")     # drop the last record: the chain alone cannot tell
    assert w.audit.verify() == (True, None)
    r = client.post("/resume", json={"reason": "x", "cleared_by": "bob"})
    assert r.status_code == 409 and "truncated" in r.json()["detail"]["problem"]


def test_kill_and_resume_validate_their_bodies(api):
    client, _, _ = api
    assert client.post("/kill", json={"reason": "", "set_by": "a"}).status_code == 422
    assert client.post("/kill", json={"reason": "r"}).status_code == 422
    assert client.post("/kill", json={"reason": "r", "set_by": "a", "force": True}).status_code == 422
    assert client.post("/resume", json={"reason": "r"}).status_code == 422
    assert client.post("/signal", json={"asset_id": "../etc"}).status_code == 422
    assert client.post("/signal", json={"asset_id": ASSET, "now_utc": "2026-10-10T17:07:00"}).status_code == 422   # no time zone
    assert client.post("/signal", json={"asset_id": "NOPE"}).status_code == 404


def test_health_has_the_fields_an_operator_needs(api):
    client, w, _ = api
    h0 = client.get("/health").json()
    assert h0["model_version"] == "fake-v1" and h0["model_alias"] == "paper"
    assert h0["feature_age_s"] is None and h0["last_cycle_status"] is None, "nothing has run yet"
    post_signal(client)
    h = client.get("/health").json()
    assert h["status"] == "ok" and h["halted"] is False and h["redis_ok"] is True and h["redis_writable"] is True
    assert h["feature_age_s"] == pytest.approx(120.0) and h["last_cycle_status"] == "dispatched"
    assert h["last_cycle"]["asset_id"] == ASSET and h["last_cycle"]["latency_s"] >= 0
    assert set(h["wiring"]) == {"read_latest", "load_model", "solve", "attribute", "freshness", "aliases", "price_source", "make_forecast_fn",
                                "make_backtest_fn", "adapt_features"}
    assert h["wiring"]["price_source"]["ok"] is False and h["status"] == "ok", "the lake's price source is optional: its absence is not degraded"
    assert h["disclosure_assets"] == ["PAD1_ESS_ESR1"] and h["forecast_log"]["errors"] == 0 and h["calibration"] is None
    assert h["wiring"]["solve"] == {"ok": True, "target": "dispatch.optimizer.lp.solve", "detail": "injected"}
    assert h["wiring"]["attribute"]["ok"] is False
    assert h["audit"]["ok"] is True and h["audit"]["head_seq"] == w.audit.head()[0]
    assert h["assets"] == [ASSET] and h["auth"] == "none"
    assert "hunter" not in json.dumps(h)


def test_health_reports_a_rejected_cycle_and_never_raises_without_data(api):
    client, w, _ = api
    w.optimizer.first = {"net_mw": 15.0}
    post_signal(client)
    assert client.get("/health").json()["last_cycle_status"] == "rejected"


def test_metrics_is_prometheus_text_with_every_series_the_dashboard_needs(api):
    client, w, _ = api
    post_signal(client)                                                     # dispatched
    w.optimizer.first = {"net_mw": 15.0}
    later = NOW + pd.Timedelta(minutes=15)
    w.populate(later)
    assert post_signal(client, now_utc=later.isoformat()).json()["status"] == "rejected"
    client.app.state.dispatch.monitor.run_once()                            # fills the freshness gauges
    r = client.get("/metrics")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/plain")
    families = list(text_string_to_metric_families(r.text))                 # raises if the text is not valid exposition format
    names = {s.name for f in families for s in f.samples}
    for need in ("dispatch_cycle_seconds_bucket", "dispatch_cycle_seconds_sum", "dispatch_cycle_seconds_count",
                 "dispatch_cycles_total", "dispatch_guard_rejections_total", "dispatch_halted", "dispatch_data_age_seconds",
                 "dispatch_capture_rate", "dispatch_slippage_ratio", "dispatch_model_info",
                 "dispatch_data_age_allowance_seconds", "dispatch_monitor_consecutive_breaches"):
        assert need in names, need
    val = {(s.name, tuple(sorted(s.labels.items()))): s.value for f in families for s in f.samples}
    assert val[("dispatch_cycles_total", (("status", "dispatched"),))] == 1.0
    assert val[("dispatch_cycles_total", (("status", "rejected"),))] == 1.0
    assert val[("dispatch_guard_rejections_total", (("reason", "power_limit"),))] == 1.0
    assert val[("dispatch_model_info", (("alias", "paper"), ("version", "fake-v1")))] == 1.0
    assert val[("dispatch_data_age_seconds", (("report", "12301"),))] == 120.0
    assert val[("dispatch_cycle_seconds_bucket", (("le", "30.0"),))] == 2.0, "a 30 s bucket exists, for the p99 alert"
    assert math.isnan(val[("dispatch_capture_rate", ())]), "unknown until a check has run"


def test_attribution_is_501_with_a_reason_when_the_engine_is_not_wired(api):
    client, _, _ = api
    r = client.get("/attribution", params={"asset_id": ASSET, "start": "2026-10-01", "end": "2026-10-02"})
    assert r.status_code == 501 and r.json()["reason"] == "attribution_engine_unavailable"
    assert r.json()["target"] == "dispatch.attribution.engine.attribute" and r.json()["detail"] == "not provided"


def test_attribution_needs_its_inputs_too(tmp_path):
    base = make_world(tmp_path / "w")
    app, w, _ = make_app(tmp_path / "a", world=base, wiring=make_wiring(base, attribute=fake_attribute))
    with TestClient(app) as client:
        r = client.get("/attribution", params={"asset_id": ASSET, "start": "2026-10-01", "end": "2026-10-02"})
        assert r.status_code == 501 and r.json()["reason"] == "attribution_inputs_unavailable"


def test_attribution_defaults_to_the_paper_ledger_and_a_price_source(tmp_path):
    seen = {}

    def engine(spec, actual, prices, forecast_fn=None, **kw):
        seen.update(spec=spec, actual=actual, prices=prices, forecast_fn=forecast_fn)
        return fake_attribute(spec, actual, prices, forecast_fn)
    base = make_world(tmp_path / "w")
    marker = lambda decision: None
    app, w, _ = make_app(tmp_path / "a", world=base, wiring=make_wiring(base, attribute=engine),
                         price_source=lambda spec, s, e: base.market.realised(pd.date_range(s, e, freq="15min", inclusive="left")),
                         forecast_fn=marker)
    with TestClient(app) as client:
        q = {"asset_id": ASSET, "start": "2026-10-10", "end": "2026-10-11"}
        empty = client.get("/attribution", params=q)
        assert empty.status_code == 404 and empty.json()["reason"] == "no_data"
        assert post_signal(client).json()["status"] == "dispatched"
        r = client.get("/attribution", params=q)
        assert r.status_code == 200, r.text
        assert seen["actual"].source == "paper-ledger" and list(seen["actual"].frame["net_mw"]) == [-10.0]
        assert seen["prices"].index[0] == pd.Timestamp("2026-10-10 00:00", tz="UTC") and len(seen["prices"]) == 96
        assert seen["forecast_fn"] is marker and seen["spec"].asset_id == ASSET


def r0_shadow(client, q):
    r = client.get("/attribution", params=q)
    assert r.status_code == 200, r.text
    return r.json()["shadow"]


def test_attribution_forecasts_with_the_loaded_model_by_default_and_reports_in_sample_refusals(tmp_path):
    replay = pytest.importorskip("dispatch.models.replay")
    seen = {}

    class InSampleError(RuntimeError):
        pass

    def engine(spec, actual, prices, forecast_fn=None, **kw):
        seen["forecast_fn"] = forecast_fn
        if seen.get("raise"):
            raise InSampleError("the model was trained on data before 2025-01-01")
        return fake_attribute(spec, actual, prices, forecast_fn)
    base = make_world(tmp_path / "w")
    app, w, _ = make_app(tmp_path / "a", world=base, wiring=make_wiring(base, attribute=engine),
                         price_source=lambda spec, s, e: base.market.realised(pd.date_range(s, e, freq="15min", inclusive="left")))
    with TestClient(app) as client:
        post_signal(client)
        q = {"asset_id": ASSET, "start": "2026-10-10", "end": "2026-10-11"}
        assert client.get("/attribution", params=q).status_code == 200
        assert callable(seen["forecast_fn"]), "built with D1a's make_forecast_fn from the loaded model"
        assert seen["forecast_fn"].__module__ == replay.__name__
        assert r0_shadow(client, q) is True
        long_q = {"asset_id": ASSET, "start": "2026-09-20", "end": "2026-10-11"}      # three weeks: the shadow would take minutes
        assert r0_shadow(client, long_q) is False and seen["forecast_fn"] is None
        seen["raise"] = True
        r = client.get("/attribution", params=q)
        assert r.status_code == 422 and r.json()["reason"] == "in_sample_forecasts"


def test_the_monitor_gets_capture_and_slippage_providers_when_it_has_prices_and_a_backtest(tmp_path):
    base = make_world(tmp_path / "w")
    src = lambda spec, s, e: base.market.realised(pd.date_range(s, e, freq="15min", inclusive="left"))
    app, w, _ = make_app(tmp_path / "a", world=base, price_source=src, backtest_fn=lambda spec, s, e: 0.0, capture_baseline=0.55)
    with TestClient(app) as client:
        mon = client.app.state.dispatch.monitor
        assert mon.capture_provider is not None and mon.slippage_provider is not None
        report = mon.run_once()
        assert report.status["capture_decay"].startswith("skipped") and report.status["slippage"].startswith("skipped")   # nothing executed yet
    app2, _, _ = make_app(tmp_path / "b", world=make_world(tmp_path / "w2"))
    with TestClient(app2) as client:
        assert client.app.state.dispatch.monitor.capture_provider is None, "no price source, no providers"


def test_attribution_returns_the_engines_rows_and_the_closure(tmp_path):
    base = make_world(tmp_path / "w")
    app, w, _ = make_app(tmp_path / "a", world=base, wiring=make_wiring(base, attribute=fake_attribute),
                         attribution_inputs=fake_attribution_inputs)
    with TestClient(app) as client:
        r = client.get("/attribution", params={"asset_id": ASSET, "start": "2026-10-01", "end": "2026-10-03"})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["n_intervals"] == 4 and body["closure"] == pytest.approx(0.02)
        assert body["rows"][0]["interval_start_utc"] == "2026-10-01T00:00:00Z" and body["rows"][0]["gap_usd"] == 10.0
        assert client.get("/attribution", params={"asset_id": ASSET, "start": "2026-10-03", "end": "2026-10-01"}).status_code == 422
        assert client.get("/attribution", params={"asset_id": ASSET, "start": "2026-01-01", "end": "2026-10-01"}).status_code == 422
        assert client.get("/attribution", params={"asset_id": "NOPE", "start": "2026-10-01", "end": "2026-10-02"}).status_code == 404


# ---- fail closed, through the API
def test_api_stale_features_return_stale(api):
    client, w, _ = api
    w.populate(age_s=3600)
    r = post_signal(client)
    assert r.status_code == 200 and r.json()["status"] == "stale" and r.json()["reasons"] == ["features_stale"]
    assert r.json()["feature_age_s"] == pytest.approx(3600.0) and r.json()["setpoint"] is None


def test_api_no_model_starts_the_service_and_fails_closed(tmp_path):
    base = make_world(tmp_path / "w")

    def no_model(alias):
        raise LookupError(f"no model version holds alias {alias!r}")
    app, w, _ = make_app(tmp_path / "a", world=base, wiring=make_wiring(base, load_model=no_model))
    with TestClient(app) as client:
        h = client.get("/health").json()                                    # the service is up
        assert h["model_version"] is None and h["status"] == "degraded" and "paper" in h["model_error"]
        r = post_signal(client)
        assert r.status_code == 503 and r.json()["status"] == "error" and r.json()["reasons"] == ["no_model"]
        assert "paper" in r.json()["detail"]["why"]
        assert ledger_lines(base) == []
        loads = audit_records(base, "model_load")
        assert len(loads) == 1 and loads[0]["ok"] is False, "one record per distinct failure, not one per request"


def test_api_model_is_picked_up_later_but_never_swapped(tmp_path, monkeypatch):
    monkeypatch.setattr(app_module, "MODEL_RETRY_S", 0.0)
    base = make_world(tmp_path / "w")
    calls = {"n": 0}
    v1, v2 = FakeForecaster("v1"), FakeForecaster("v2")

    def registry(alias):
        calls["n"] += 1
        if calls["n"] <= 2:                                                # at startup and on the first request: nothing promoted
            raise LookupError("nothing promoted yet")
        return v1 if calls["n"] == 3 else v2
    app, w, _ = make_app(tmp_path / "a", world=base, wiring=make_wiring(base, load_model=registry, aliases=lambda: {"paper": "v1"}))
    with TestClient(app) as client:
        assert post_signal(client).json()["reasons"] == ["no_model"]
        ok = post_signal(client)                                           # a model was promoted meanwhile: the retry finds it
        assert ok.json()["status"] == "dispatched" and ok.json()["model_version"] == "v1"
        base.populate(NOW + pd.Timedelta(minutes=15))
        again = post_signal(client, now_utc=(NOW + pd.Timedelta(minutes=15)).isoformat())
        assert again.json()["model_version"] == "v1" and calls["n"] == 3, "a loaded model is only replaced by a restart"
        assert client.get("/health").json()["model_version"] == "v1"


def test_api_a_model_whose_alias_was_cleared_or_moved_is_stale_and_nothing_dispatches(tmp_path, monkeypatch):
    monkeypatch.setattr(app_module, "MODEL_CHECK_S", 0.0)
    base = make_world(tmp_path / "w")
    held = {"paper": "fake-v1"}
    app, w, _ = make_app(tmp_path / "a", world=base, wiring=make_wiring(base, aliases=lambda: dict(held)))
    with TestClient(app) as client:
        assert post_signal(client).json()["status"] == "dispatched"
        assert client.get("/health").json()["model_stale"] is None
        held.pop("paper")                                                  # D1a: clear_alias() pulls a model out of service
        r = post_signal(client)
        assert r.status_code == 503 and r.json()["reasons"] == ["model_stale"] and "cleared" in r.json()["detail"]["why"]
        h = client.get("/health").json()
        assert h["status"] == "degraded" and "cleared" in h["model_stale"]
        held["paper"] = "fake-v2"                                          # promoted over: also stale until a restart loads it
        base.populate(NOW + pd.Timedelta(minutes=15))
        r = post_signal(client, now_utc=(NOW + pd.Timedelta(minutes=15)).isoformat())
        assert r.json()["reasons"] == ["model_stale"] and "Restart" in r.json()["detail"]["why"]
        held["paper"] = "fake-v1"                                          # rolled back: current again, no restart needed
        r = post_signal(client, now_utc=(NOW + pd.Timedelta(minutes=15)).isoformat())
        assert r.json()["status"] == "dispatched"
        loads = [x for x in audit_records(base, "model_load") if x.get("note") == "alias check"]
        assert [x["ok"] for x in loads] == [False, False, True] and loads[0]["stale"] and loads[2]["stale"] is None


def test_api_an_unreadable_registry_keeps_the_last_answer_and_says_so(tmp_path, monkeypatch):
    monkeypatch.setattr(app_module, "MODEL_CHECK_S", 0.0)
    base = make_world(tmp_path / "w")
    state = {"broken": False}

    def aliases():
        if state["broken"]:
            raise OSError("database is locked")
        return {"paper": "fake-v1"}
    app, w, _ = make_app(tmp_path / "a", world=base, wiring=make_wiring(base, aliases=aliases))
    with TestClient(app) as client:
        assert post_signal(client).json()["status"] == "dispatched"
        state["broken"] = True
        assert post_signal(client, now_utc=(NOW + pd.Timedelta(seconds=30)).isoformat()).json()["status"] == "dispatched"
        assert "database is locked" in client.get("/health").json()["model_alias_check_error"]


def test_cycle_a_model_check_that_answers_stale_stops_the_cycle(tmp_path):
    w = make_world(tmp_path, model_check=lambda: "alias paper was cleared")
    assert_no_dispatch(w, run_cycle(ASSET, NOW, w.deps), "error", C.MODEL_STALE)
    w2 = make_world(tmp_path / "ok", model_check=lambda: None)
    assert run_cycle(ASSET, NOW, w2.deps).status == "dispatched"
    w3 = make_world(tmp_path / "broken", model_check=lambda: 1 / 0)
    assert run_cycle(ASSET, NOW, w3.deps).status == "dispatched", "a check that cannot answer does not by itself stop dispatch"


def test_api_non_optimal_plan_returns_503_error(api):
    client, w, _ = api
    w.optimizer.status = "infeasible"
    r = post_signal(client)
    assert r.status_code == 503 and r.json()["reasons"] == ["plan_not_optimal"] and r.json()["detail"]["plan_status"] == "infeasible"
    assert audit_records(w, "signal")[0]["plan"]["status"] == "infeasible"


def test_api_guard_rejection_returns_rejected_with_the_proposal(api):
    client, w, _ = api
    w.optimizer.first = {"net_mw": 15.0}
    r = post_signal(client)
    assert r.status_code == 200 and r.json()["status"] == "rejected" and r.json()["reasons"] == ["power_limit"]
    assert r.json()["setpoint"] is None and r.json()["proposed_setpoint"]["net_mw"] == 15.0
    assert r.json()["guard"] == {"accepted": False, "reasons": ["power_limit"], "checked_utc": r.json()["guard"]["checked_utc"]}
    assert sample(w.metrics, "dispatch_guard_rejections_total", reason="power_limit") == 1


def test_api_redis_outage_halts_everything_and_the_service_stays_up(api):
    client, w, server = api
    assert post_signal(client).json()["status"] == "dispatched"
    server.connected = False
    r = post_signal(client, now_utc=(NOW + pd.Timedelta(minutes=15)).isoformat())
    assert r.status_code == 200 and r.json()["status"] == "halted"
    assert r.json()["detail"]["halt_reason"].startswith("redis_unreachable")
    h = client.get("/health")
    assert h.status_code == 200 and h.json()["redis_ok"] is False and h.json()["status"] == "halted"
    assert client.get("/metrics").status_code == 200 and "dispatch_halted 1.0" in client.get("/metrics").text
    assert client.post("/resume", json={"reason": "x", "cleared_by": "bob"}).status_code == 503, "cannot resume blind"
    server.connected = True
    w.populate(NOW + pd.Timedelta(minutes=15))
    assert post_signal(client, now_utc=(NOW + pd.Timedelta(minutes=15)).isoformat()).json()["status"] == "dispatched"


def test_api_a_full_redis_is_degraded_but_kill_and_resume_still_work(tmp_path):
    base = make_world(tmp_path / "w")
    server = fakeredis.FakeServer()
    app, w, _ = make_app(tmp_path / "a", world=base, server=server)
    app_state_redis = FullRedis(server=server, decode_responses=True)
    with TestClient(app) as client:
        client.app.state.dispatch.halt._client = app_state_redis        # Redis fills up while the service runs
        client.app.state.dispatch.halt._probe = None
        h = client.get("/health").json()
        assert h["redis_ok"] is True and h["redis_writable"] is False and h["status"] == "degraded"
        assert "dispatch_redis_writable 0.0" in client.get("/metrics").text
        k = client.post("/kill", json={"reason": "feed looks wrong", "set_by": "alice"})
        assert k.status_code == 200 and k.json()["persisted"] is False and "survive a restart" in k.json()["warning"]
        assert post_signal(client).json()["status"] == "halted", "the kill holds even though Redis would not store it"
        assert client.post("/resume", json={"reason": "ok", "cleared_by": "bob"}).status_code == 200
        assert post_signal(client).json()["status"] == "dispatched"


def test_api_a_kill_during_a_redis_outage_survives_it(api):
    client, w, server = api
    server.connected = False
    k = client.post("/kill", json={"reason": "feed looks wrong", "set_by": "alice"})
    assert k.status_code == 200 and k.json()["persisted"] is False and "survive a restart" in k.json()["warning"]
    server.connected = True
    assert post_signal(client).json()["status"] == "halted", "Redis is back and empty, the kill still holds"
    assert client.post("/resume", json={"reason": "ok", "cleared_by": "bob"}).status_code == 200
    assert post_signal(client).json()["status"] == "dispatched"


def test_api_modules_that_are_not_there_do_not_stop_the_service(tmp_path):
    base = make_world(tmp_path / "w")
    app, w, _ = make_app(tmp_path / "a", world=base, wiring=Wiring.of())
    with TestClient(app) as client:
        h = client.get("/health").json()
        assert h["status"] == "degraded" and not any(m["ok"] for m in h["wiring"].values())
        assert h["wiring"]["solve"]["target"] == "dispatch.optimizer.lp.solve"
        r = post_signal(client)
        assert r.status_code == 503 and r.json()["reasons"] == ["online_store_unavailable"]
        assert client.get("/metrics").status_code == 200
        assert client.post("/kill", json={"reason": "r", "set_by": "a"}).status_code == 200


def test_wiring_survives_a_target_that_is_missing_or_raises_on_import(tmp_path, monkeypatch):
    (tmp_path / "boom_mod.py").write_text("raise RuntimeError('wheel missing')\n", encoding="utf-8")
    (tmp_path / "ok_mod.py").write_text("def fn(*a): return 'ok'\nnot_callable = 3\n", encoding="utf-8")
    monkeypatch.syspath_prepend(str(tmp_path))
    from dispatch.service import wiring as W
    monkeypatch.setattr(W, "TARGETS", {"read_latest": ("boom_mod", "fn"), "load_model": ("no_such_module_xyz", "load"),
                                       "solve": ("ok_mod", "fn"), "attribute": ("ok_mod", "missing_attr"),
                                       "freshness": ("ok_mod", "not_callable")})
    w = W.Wiring.from_modules()
    assert w.solve is not None and w.status["solve"].ok
    assert w.read_latest is None and "RuntimeError: wheel missing" in w.status["read_latest"].detail
    assert w.load_model is None and "ModuleNotFoundError" in w.status["load_model"].detail
    assert w.attribute is None and "AttributeError" in w.status["attribute"].detail
    assert w.freshness is None and "not callable" in w.status["freshness"].detail


def test_the_real_modules_bind_or_report_why_not():
    """Whatever D0, D1a and D1b have delivered so far, binding never raises and says what is missing."""
    w = Wiring.from_modules()
    assert set(w.status) == {"read_latest", "load_model", "solve", "attribute", "freshness", "aliases", "price_source", "make_forecast_fn",
                             "make_backtest_fn", "adapt_features"}
    for name, st in w.status.items():
        assert st.ok == (getattr(w, name) is not None)
        assert st.ok or st.detail


# ---- security
def test_a_bearer_token_protects_the_mutating_endpoints(tmp_path):
    app, w, _ = make_app(tmp_path, api_token="s3cret-token")
    with TestClient(app) as client:
        assert client.get("/health").status_code == 200 and client.get("/metrics").status_code == 200   # probes stay open
        assert client.get("/health").json()["auth"] == "bearer token"
        body = {"asset_id": ASSET}
        for path, payload in (("/signal", body), ("/kill", {"reason": "r", "set_by": "a"}),
                              ("/resume", {"reason": "r", "cleared_by": "a"})):
            assert client.post(path, json=payload).status_code == 401
            assert client.post(path, json=payload, headers={"Authorization": "Bearer nope"}).status_code == 401
            assert client.post(path, json=payload, headers={"Authorization": "s3cret-token"}).status_code == 401   # no "Bearer"
            assert client.post(path, json=payload, headers={"Authorization": "Bearer s3cret-token-and-more"}).status_code == 401
        assert client.get("/attribution", params={"asset_id": ASSET, "start": "2026-10-01", "end": "2026-10-02"}).status_code == 401
        good = {"Authorization": "Bearer s3cret-token"}
        pub = client.get("/health").json()                              # a probe without the token sees the state, not the details
        assert pub["status"] == "ok" and all(set(m) == {"ok"} for m in pub["wiring"].values())
        assert "halt" not in pub and "model_error" not in pub and "redis" not in pub and set(pub["audit"]) == {"ok"}
        full = client.get("/health", headers=good).json()
        assert full["wiring"]["solve"]["detail"] == "injected" and "halt" in full and "redis" in full
        assert client.post("/signal", json=body, headers=good).json()["status"] == "dispatched"
        assert client.post("/kill", json={"reason": "r", "set_by": "a"}, headers=good).status_code == 200
        assert "s3cret" not in client.get("/health").text and "s3cret" not in json.dumps(list(w.audit.read()))


def test_replays_need_permission_and_stay_out_of_the_live_ledger(tmp_path):
    app, w, _ = make_app(tmp_path, allow_replay=False)
    with TestClient(app) as client:
        assert post_signal(client, now_utc=(NOW + pd.Timedelta(days=1)).isoformat()).status_code == 403
        assert post_signal(client).status_code == 200
        w.populate(NOW + pd.Timedelta(seconds=90))
        assert post_signal(client, now_utc=(NOW + pd.Timedelta(seconds=90)).isoformat()).status_code == 200   # within the tolerance
        assert len(w.ledger.entries(ASSET, final_only=False)) == 2


def test_the_service_starts_without_a_token_but_says_so(tmp_path, caplog):
    app, w, _ = make_app(tmp_path)
    with caplog.at_level("WARNING", logger="dispatch.service"), TestClient(app):
        pass
    assert any("DISPATCH_API_TOKEN is not set" in m for m in caplog.messages)
    startup = audit_records(w, "startup")[0]
    assert startup["redis_ok"] is True and startup["assets"] == [ASSET] and "none" in startup["auth"]


# ================================================================================================ latency
def pctl(xs, q):
    xs = sorted(xs)
    return xs[min(len(xs) - 1, math.ceil(q * len(xs)) - 1)]


def test_latency_of_200_cycles(tmp_path, capsys):
    """The PRD target is p99 < 30 s per cycle. With fakes the cycle is milliseconds; the number worth watching is how much
    of it is the platform itself: audit fsyncs, ledger writes, guard, metrics."""
    w = make_world(tmp_path, fsync=True)                                    # durable writes, as in production
    lat, statuses = [], []
    t = NOW
    for _ in range(200):
        w.populate(t)
        t0 = time.perf_counter()
        res = run_cycle(ASSET, t, w.deps)
        lat.append(time.perf_counter() - t0)
        statuses.append(res.status)
        t += pd.Timedelta(minutes=5)
    p50, p99, worst = pctl(lat, 0.5), pctl(lat, 0.99), max(lat)
    with capsys.disabled():
        print(f"\nLATENCY 200 cycles (fakes, fsync on): p50 {p50 * 1e3:.1f} ms, p99 {p99 * 1e3:.1f} ms, max {worst * 1e3:.1f} ms "
              f"(target p99 < {CYCLE_P99_TARGET_S:.0f} s); statuses {dict(pd.Series(statuses).value_counts())}")
    assert statuses.count("dispatched") == 200
    assert p99 < CYCLE_P99_TARGET_S and p99 < 1.0
    assert sample(w.metrics, "dispatch_cycle_seconds_count") == 200
    assert w.audit.verify() == (True, None) and w.audit.head()[0] == 200 * 4 - 1
    assert w.ledger.replay(w.spec, w.market.realised(pd.DatetimeIndex([e.interval_start_utc for e in w.ledger.entries(ASSET)]))).ok


def test_latency_through_the_http_layer(tmp_path, capsys):
    app, w, _ = make_app(tmp_path)
    lat = []
    with TestClient(app) as client:
        t = NOW
        for _ in range(100):
            w.populate(t)
            t0 = time.perf_counter()
            r = client.post("/signal", json={"asset_id": ASSET, "now_utc": t.isoformat()})
            lat.append(time.perf_counter() - t0)
            assert r.json()["status"] == "dispatched"
            t += pd.Timedelta(minutes=5)
    with capsys.disabled():
        print(f"\nLATENCY 100 POST /signal (TestClient, fakes): p50 {pctl(lat, 0.5) * 1e3:.1f} ms, p99 {pctl(lat, 0.99) * 1e3:.1f} ms")
    assert pctl(lat, 0.99) < CYCLE_P99_TARGET_S
