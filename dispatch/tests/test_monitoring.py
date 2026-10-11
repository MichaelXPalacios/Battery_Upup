"""Monitoring (FR11) and Gate 3.

  checks       stale data, feature drift (PSI), capture-rate decay, slippage: each breach type, and the PSI maths
  halt policy  three consecutive breaches set the halt flag by themselves (two daily ones for calibration); the retrain hook only
               records; an alert-only breach (feature PSI, a diagnostic report gone stale) never counts
  runner       the passes that feed the policy, with providers that are missing, failing or breaching
  Gate 3       "paper matches backtest": the same prices and the same model give slippage 0 and pass; an injected
               difference fails
  ops          alerts.yml, the Grafana dashboard, prometheus.yml and docker-compose.yml are valid and refer only to
               metrics the service really exports
"""
import dataclasses
import json
import math
import re
import time
from pathlib import Path

import fakeredis
import numpy as np
import pandas as pd
import pytest
import yaml
from hypothesis import HealthCheck, given, settings, strategies as st

from fakes import ASSET, NOW, SP, FakeForecaster, FakeHalt, FakeOnlineStore, Market, make_world, pilot_spec
from dispatch import units
from dispatch.config import (DATA_FRESHNESS_TARGET_S, DRIFT_PSI_ALERT, HALT_AFTER_BREACHES, HALT_KEY, PINBALL_DEGRADATION_MAX, SLIPPAGE_TOL)
from dispatch.execution.audit import AuditLog
from dispatch.guard.guard import project_soc_end
from dispatch.monitoring import checks as C
from dispatch.monitoring.checks import Breach, FeatureWindow, gate3, record_gate3_result
from dispatch.monitoring.halt_policy import HaltPolicy, RETRAIN_CHECKS
from dispatch.monitoring.metrics import METRIC_NAMES, Metrics
from dispatch.monitoring.providers import PRICE_LAG, capture_provider, period, slippage_provider
from dispatch.monitoring.runner import CaptureInputs, MonitorRunner, SlippageInputs
from dispatch.service.assets import AssetRegistry
from dispatch.service.cycle import replay_cycles
from dispatch.service.state import RedisHaltFlag

OPS = Path(__file__).resolve().parents[1] / "ops"


def breach(check, subject="x", value=1.0, threshold=0.5):
    return Breach(check, subject, value, threshold, f"{check} {subject}")


# ================================================================================================ stale data
def test_stale_data_uses_the_freshness_target_plus_a_per_report_allowance():
    """D0 measured ERCOT's own late postings (notes/d0.md section 7): one missed posting must pass, two must breach."""
    assert DATA_FRESHNESS_TARGET_S == 300
    assert C.report_allowance_s("12301") == 900 + 900 + DATA_FRESHNESS_TARGET_S == 2100      # RT SPP, every 15 minutes; 1,285 s seen between files
    assert C.report_allowance_s("NP6-905-CD") == C.report_allowance_s("12301")
    assert {r: C.report_allowance_s(r) for r in ("NP6-331-CD", "NP6-322-CD", "NP6-86-CD", "NP3-565-CD")} == {
        "NP6-331-CD": 2100, "NP6-322-CD": 1500, "NP6-86-CD": 7500, "NP3-565-CD": 7500}       # D0's table
    assert C.report_allowance_s("12331") == 86400 + 4 * 3600 + DATA_FRESHNESS_TARGET_S == 101100   # DAM, once a day, may post late
    with pytest.raises(KeyError):
        C.report_allowance_s("NP6-785-ER")                                             # a yearly archive: not watched
    for table in (C.REPORT_CADENCE_S, C.REPORT_EXTRA_ALLOWANCE_S):
        assert all(isinstance(v, (int, float)) and v >= 0 for v in table.values())


def test_only_the_live_inputs_are_watched_and_they_are_reports_d0_knows():
    """Archives, static lists and the reports adopted once from research are allowed to be old: watching them would halt for nothing."""
    reports = pytest.importorskip("dispatch.data.reports")
    watched = {reports.canonical_id(r) for r in C.MONITORED_REPORTS}
    assert watched <= set(reports.REPORTS), watched - set(reports.REPORTS)
    assert not [r.report_id for r in reports.REPORTS.values() if (r.archive or not r.ingest) and r.report_id in watched]
    assert {"NP6-905-CD", "NP4-190-CD", "NP4-188-CD", "NP6-331-CD"} <= watched
    ages = {r: 10**9 for r in reports.REPORTS if r not in watched}                    # everything unwatched, a billion seconds old
    assert C.stale_data_breaches(ages) == []
    assert all(reports.canonical_id(k) == reports.canonical_id(str(reports.get(k).rtid)) for k in C.REPORT_CADENCE_S if k in reports.REPORTS)


def test_stale_data_breach_per_report():
    ages = {"12301": 1300.0, "24898": 2200.0, "12331": 20 * 3600.0}      # 1,285 s is the longest gap D0 saw between two 15-minute files
    found = C.stale_data_breaches(ages)
    assert [b.subject for b in found] == ["24898"] and found[0].check == "stale_data"
    assert found[0].value == 2200.0 and found[0].threshold == 2100.0
    assert C.stale_data_breaches({"12301": 2100.0}) == [], "exactly at the allowance is not a breach"
    assert C.stale_data_breaches({"12301": 2100.1})


def test_only_the_reports_the_served_model_reads_can_halt_for_staleness():
    """Lead decisions (10-11 Oct 2026, on D0's evidence and I1's finding): NP6-905-CD, NP4-188-CD (what the model reads) and
    NP4-190-CD (posts with it) halt; the other live reports alert only."""
    assert C.HALT_ELIGIBLE_REPORTS == {"NP6-905-CD", "12301", "NP4-190-CD", "12331", "NP4-188-CD", "12329"}
    assert C.HALT_ELIGIBLE_REPORTS <= C.MONITORED_REPORTS
    old = {r: 10**7 for r in C.MONITORED_REPORTS}
    by = {b.subject: b.halts for b in C.stale_data_breaches(old)}
    assert {r for r, halts in by.items() if halts} == set(C.HALT_ELIGIBLE_REPORTS)
    assert not any(by[r] for r in ("NP6-331-CD", "NP6-322-CD", "NP6-86-CD", "NP3-565-CD", "24898", "13114", "12302", "14837"))
    assert all("(alert only)" in b.message for b in C.stale_data_breaches(old) if not b.halts)
    assert all(b.halts for b in C.stale_data_breaches({"anything": 10**7}, allowances={"anything": 5.0})), "a report asked for by name can halt"


def test_stale_data_nan_or_unreadable_age_counts_as_stale_and_archives_are_ignored():
    assert len(C.stale_data_breaches({"12301": float("nan"), "24898": None})) == 2
    assert C.stale_data_breaches({"13091": 10**9, "25569": 10**9, "HIST-DAM-MCPC": 10**9, "NP6-785-ER": 10**9}) == [], "yearly archives never refresh"
    assert C.stale_data_breaches({"12301": 5000.0}, allowances={"12301": 6000.0}) == []


# ================================================================================================ drift
RNG = np.random.default_rng(11)
REF = pd.DataFrame({"price": RNG.normal(50, 10, 4000), "load": RNG.normal(40000, 3000, 4000),
                    "flag": RNG.integers(0, 2, 4000), "zone": RNG.choice(["N", "S", "H"], 4000, p=[0.5, 0.3, 0.2])})


def sample_like(ref, n=600, seed=3, **shift):
    r = np.random.default_rng(seed)
    cur = pd.DataFrame({"price": r.normal(50, 10, n), "load": r.normal(40000, 3000, n), "flag": r.integers(0, 2, n),
                        "zone": r.choice(["N", "S", "H"], n, p=[0.5, 0.3, 0.2])})
    for k, f in shift.items():
        cur[k] = f(cur[k])
    return cur


def test_psi_is_small_for_the_same_distribution_and_large_for_a_shift():
    same = C.feature_psi(REF, sample_like(REF))
    assert set(same) == {"price", "load", "flag", "zone"} and max(same.values()) < 0.1
    shifted = C.feature_psi(REF, sample_like(REF, price=lambda s: s + 10, zone=lambda s: s.map({"N": "H", "S": "H", "H": "H"})))
    assert shifted["price"] > DRIFT_PSI_ALERT and shifted["zone"] > DRIFT_PSI_ALERT
    assert shifted["load"] < 0.1 and shifted["flag"] < 0.1, "untouched features stay quiet"
    found = C.drift_breaches(shifted)
    assert {b.subject for b in found} == {"price", "zone"} and all(b.check == "feature_drift" and b.threshold == DRIFT_PSI_ALERT for b in found)


def test_psi_grows_with_the_size_of_the_shift():
    vals = [C.psi(REF["price"], sample_like(REF, n=2000, price=lambda s, k=k: s + k)["price"]) for k in (0, 2, 5, 10, 25)]
    assert vals == sorted(vals) and vals[0] < 0.05 and vals[-1] > 1.0


def test_psi_of_a_sample_with_itself_is_exactly_zero():
    assert C.psi(REF["price"], REF["price"]) == 0.0
    assert C.psi(REF["zone"], REF["zone"]) == 0.0


def test_psi_handles_constant_features_missing_values_and_unseen_categories():
    const = pd.Series([1.0] * 500)
    assert C.psi(const, pd.Series([1.0] * 100)) == 0.0
    assert C.psi(const, pd.Series([2.0] * 100)) > DRIFT_PSI_ALERT
    ref = pd.Series(np.r_[np.random.default_rng(1).normal(0, 1, 900), [np.nan] * 100])
    assert C.psi(ref, pd.Series(np.r_[np.random.default_rng(2).normal(0, 1, 900), [np.nan] * 100])) < 0.1
    assert C.psi(ref, pd.Series(np.random.default_rng(2).normal(0, 1, 1000))) > DRIFT_PSI_ALERT, "a feed that stopped sending values is drift"
    assert C.psi(pd.Series(["a", "b"] * 50), pd.Series(["a", "c"] * 50)) > DRIFT_PSI_ALERT
    assert math.isnan(C.psi(pd.Series([], dtype=float), pd.Series([1.0])))
    assert C.psi(pd.Series([np.nan] * 5), pd.Series([np.nan] * 5)) == 0.0
    nullable = pd.Series([1.0, 2.0, pd.NA, 4.0] * 50, dtype="Float64")
    assert C.psi(nullable, nullable) == 0.0


def test_feature_psi_skips_timestamps_and_columns_only_one_side_has():
    ref = REF.assign(ts=pd.Timestamp("2026-01-01", tz="UTC"), only_ref=1.0)
    cur = sample_like(REF).assign(ts=pd.Timestamp("2026-10-01", tz="UTC"), only_cur=2.0)
    assert set(C.feature_psi(ref, cur)) == {"price", "load", "flag", "zone"}
    assert set(C.feature_psi(ref, cur, features=["price", "nope"])) == {"price"}


@settings(max_examples=60, deadline=None, database=None, suppress_health_check=list(HealthCheck))
@given(st.lists(st.floats(-1e6, 1e6, allow_nan=False, allow_subnormal=False), min_size=20, max_size=120),
       st.lists(st.floats(-1e6, 1e6, allow_nan=False, allow_subnormal=False), min_size=20, max_size=120),
       st.sampled_from([0.25, 0.5, 2.0, 4.0]))
def test_psi_is_non_negative_and_ignores_rescaling(a, b, scale):
    ref, cur = pd.Series(a), pd.Series(b)
    p = C.psi(ref, cur)
    assert p >= -1e-12
    assert C.psi(ref * scale, cur * scale) == pytest.approx(p, abs=1e-9)


def test_feature_window_is_bounded_and_keeps_the_newest_rows():
    win = FeatureWindow(max_rows=300)
    assert win.frame() is None
    for k in range(10):
        win.add(pd.DataFrame({"x": np.full(96, float(k))}))
    f = win.frame()
    assert 300 <= len(f) < 300 + 96 and f["x"].iloc[-1] == 9.0 and f["x"].min() > 0.0
    win.add(None)
    win.add(pd.DataFrame())


def test_evidently_second_opinion_agrees_with_psi_when_it_can_be_imported():
    cur = sample_like(REF, n=600, price=lambda s: s + 12)
    verdict = C.evidently_drift(REF[["price", "load"]], cur[["price", "load"]])
    if verdict is None:
        pytest.skip("evidently could not be imported here (it is optional)")
    assert verdict["price"]["drifted"] is True and verdict["load"]["drifted"] is False
    assert C.feature_psi(REF, cur)["price"] > DRIFT_PSI_ALERT
    small = C.evidently_drift(REF[["price"]].head(400), sample_like(REF, n=200, price=lambda s: s + 12)[["price"]])
    assert small["price"]["drifted"] is True            # a small sample uses a KS p-value instead of a distance


# ================================================================================================ capture and slippage
def test_capture_rate_and_its_decay_breach():
    assert C.capture_rate(560.0, 1000.0) == pytest.approx(0.56)
    assert math.isnan(C.capture_rate(5.0, 0.0)) and math.isnan(C.capture_rate(5.0, -3.0))
    assert C.capture_decay_breach(0.56, 0.56) is None
    assert C.capture_decay_breach(0.46, 0.56) is None                         # 18% under: inside the 20% tolerance
    b = C.capture_decay_breach(0.40, 0.56)
    assert b.check == "capture_decay" and b.value == 0.40 and b.threshold == pytest.approx(0.56 * 0.8)
    assert C.capture_decay_breach(float("nan"), 0.56) is None and C.capture_decay_breach(0.3, float("nan")) is None
    assert C.capture_decay_breach(0.3, 0.0) is None


def test_slippage_ratio_and_breach():
    assert C.slippage_ratio(105.0, 100.0) == pytest.approx(0.05)
    assert C.slippage_breach(C.slippage_ratio(105.0, 100.0)) is None, "exactly at the tolerance passes"
    b = C.slippage_breach(C.slippage_ratio(106.0, 100.0))
    assert b.check == "slippage" and b.threshold == SLIPPAGE_TOL and b.value == pytest.approx(0.06)
    assert C.slippage_ratio(10.0, 0.0) == pytest.approx(10.0 / C.SLIPPAGE_DENOM_FLOOR_USD), "a zero backtest does not divide by zero"
    assert C.slippage_breach(float("nan")).severity == "error"


# ================================================================================================ the halt policy
@pytest.fixture()
def policy_kit(tmp_path):
    server = fakeredis.FakeServer()
    halt = RedisHaltFlag(fakeredis.FakeRedis(server=server, decode_responses=True), clock=lambda: pd.Timestamp("2026-10-10 18:00", tz="UTC"))
    audit = AuditLog(tmp_path / "audit", fsync=False)
    metrics = Metrics()
    retrains = []
    policy = HaltPolicy(halt, audit, metrics=metrics, on_retrain=retrains.append)
    return policy, halt, audit, metrics, retrains, server


def test_three_consecutive_breaches_set_the_halt_flag(policy_kit):
    policy, halt, audit, metrics, _, server = policy_kit
    assert HALT_AFTER_BREACHES == 3 and policy.threshold == 3
    for n in (1, 2):
        out = policy.evaluate([breach("stale_data", "12301")], ["stale_data"])
        assert not out.halted_now and halt.is_set() == (False, "") and policy.counts["stale_data"] == n
    out = policy.evaluate([breach("stale_data", "12301")], ["stale_data"])
    assert out.halted_now and out.tripped == ["stale_data"]
    halted, reason = halt.is_set()
    assert halted and reason.startswith("auto-halt after 3 consecutive breaching checks") and "stale_data x3" in reason
    raw = json.loads(fakeredis.FakeRedis(server=server, decode_responses=True).get(HALT_KEY))
    assert raw["set_by"] == "monitor:halt_policy" and raw["reason"] == reason
    # the audit trail: three breach records, then the halt with its reason
    recs = list(audit.read())
    assert [r["type"] for r in recs] == ["breach", "breach", "breach", "halt"]
    assert [r["consecutive"] for r in recs[:3]] == [1, 2, 3]
    h = recs[-1]
    assert h["source"] == "monitor" and h["reason"] == reason and h["checks"] == ["stale_data"] and h["set_by"] == "monitor:halt_policy"
    assert audit.verify() == (True, None)
    assert metrics.registry.get_sample_value("dispatch_halted") == 1
    assert metrics.registry.get_sample_value("dispatch_monitor_consecutive_breaches", {"check": "stale_data"}) == 3
    assert metrics.registry.get_sample_value("dispatch_monitor_breaches_total", {"check": "stale_data"}) == 3


def test_a_long_breach_does_not_flood_the_audit_log(policy_kit):
    policy, halt, audit, metrics, *_ = policy_kit
    for _ in range(65):
        policy.evaluate([breach("stale_data", "12301")], ["stale_data"])
    counts = [r["consecutive"] for r in audit.read(types=("breach",))]
    assert counts == [1, 2, 3, 30, 60], counts
    assert metrics.registry.get_sample_value("dispatch_monitor_breaches_total", {"check": "stale_data"}) == 65, "the metric counts every pass"
    assert len(list(audit.read(types=("halt",)))) == 1


def test_the_guard_then_rejects_everything_after_an_automatic_halt(policy_kit, tmp_path):
    policy, halt, *_ = policy_kit
    for _ in range(3):
        policy.evaluate([breach("slippage", "fleet")], ["slippage"])
    w = make_world(tmp_path / "w", halt=halt)
    from dispatch.service.cycle import run_cycle
    res = run_cycle(ASSET, NOW, w.deps)
    assert res.status == "halted" and "auto-halt" in res.detail["halt_reason"]


def test_a_clean_pass_resets_the_count(policy_kit):
    policy, halt, *_ = policy_kit
    policy.evaluate([breach("feature_drift")], ["feature_drift"])
    policy.evaluate([breach("feature_drift")], ["feature_drift"])
    policy.evaluate([], ["feature_drift"])
    assert policy.counts["feature_drift"] == 0
    policy.evaluate([breach("feature_drift")], ["feature_drift"])
    policy.evaluate([breach("feature_drift")], ["feature_drift"])
    assert halt.is_set() == (False, "")


def test_a_check_that_did_not_run_neither_counts_nor_resets(policy_kit):
    policy, halt, *_ = policy_kit
    policy.evaluate([breach("capture_decay")], ["capture_decay"])
    policy.evaluate([], ["stale_data"])                        # capture_decay was skipped this pass
    policy.evaluate([breach("capture_decay")], ["capture_decay"])
    assert policy.counts["capture_decay"] == 2
    policy.evaluate([breach("capture_decay")], ["capture_decay"])
    assert halt.is_set()[0] is True


def test_counts_are_per_check(policy_kit):
    policy, halt, *_ = policy_kit
    for check in ("stale_data", "feature_drift", "stale_data", "slippage", "feature_drift", "capture_decay"):
        policy.evaluate([breach(check)], [check])
    assert halt.is_set() == (False, ""), "two of three in two different checks is not three in a row"


def test_an_existing_halt_is_not_overwritten_and_not_repeated(policy_kit):
    policy, halt, audit, *_ = policy_kit
    halt.set("operator kill", "alice")
    for _ in range(4):
        policy.evaluate([breach("stale_data")], ["stale_data"])
    assert halt.is_set() == (True, "operator kill")
    assert [r["type"] for r in audit.read(types=("halt",))] == []


def test_the_retrain_hook_only_records(policy_kit):
    policy, halt, audit, _, retrains, _ = policy_kit
    policy.evaluate([breach("stale_data")], ["stale_data"])
    assert retrains == [], "stale data is not something a retrain fixes"
    assert set(RETRAIN_CHECKS) == {"calibration", "capture_decay"}, "PSI no longer asks for a retrain: it flags nearly every window"
    out = policy.evaluate([breach("capture_decay", "fleet")], ["capture_decay"])
    assert out.retrain_requested and len(retrains) == 1 and retrains[0].checks == ("capture_decay",)
    policy.evaluate([breach("capture_decay", "fleet")], ["capture_decay"])
    assert len(retrains) == 1, "one request per streak, not one per pass"
    rec = [r for r in audit.read() if r["type"] == "retrain_request"]
    assert len(rec) == 1 and rec[0]["trains"] is False and "capture_decay" in rec[0]["checks"]
    assert policy.retrain_requests == retrains
    policy.evaluate([], ["capture_decay"])
    policy.evaluate([breach("capture_decay")], ["capture_decay"])
    assert len(retrains) == 2, "a new streak asks again"
    manual = policy.request_retrain("operator asked")
    assert manual.reason == "operator asked" and len(policy.retrain_requests) == 3
    assert halt.is_set() == (False, "")


def test_the_retrain_hook_failing_does_not_stop_monitoring(tmp_path):
    def broken(req):
        raise RuntimeError("scheduler down")
    p = HaltPolicy(FakeHalt(), AuditLog(tmp_path / "a", fsync=False), on_retrain=broken)
    assert p.evaluate([breach("capture_decay")], ["capture_decay"]).retrain_requested


def test_a_failing_audit_log_cannot_prevent_a_halt(tmp_path):
    audit = AuditLog(tmp_path / "a", fsync=False)
    audit.append = lambda e: (_ for _ in ()).throw(OSError("disk full"))
    halt, metrics = FakeHalt(), Metrics()
    p = HaltPolicy(halt, audit, metrics=metrics)
    for _ in range(3):
        p.evaluate([breach("stale_data")], ["stale_data"])
    assert halt.is_set()[0] is True and metrics.registry.get_sample_value("dispatch_audit_errors_total") >= 4


def test_a_halt_flag_that_cannot_be_set_is_reported_not_raised(tmp_path):
    class Dead(FakeHalt):
        def set(self, reason, set_by):
            raise ConnectionError("redis down")
    audit = AuditLog(tmp_path / "a", fsync=False)
    p = HaltPolicy(Dead(), audit)
    for _ in range(3):
        out = p.evaluate([breach("stale_data")], ["stale_data"])
    assert not out.halted_now
    assert any(r["type"] == "error" and r["where"] == "halt_policy.set" for r in audit.read())


def test_the_threshold_is_configurable_and_validated(tmp_path):
    halt = FakeHalt()
    p = HaltPolicy(halt, AuditLog(tmp_path / "a", fsync=False), threshold=1)
    assert p.evaluate([breach("slippage")], ["slippage"]).halted_now and halt.is_set()[0]
    with pytest.raises(ValueError):
        HaltPolicy(FakeHalt(), AuditLog(tmp_path / "b"), threshold=0)


# ================================================================================================ the runner
def make_runner(tmp_path, **kw):
    server = fakeredis.FakeServer()
    halt = RedisHaltFlag(fakeredis.FakeRedis(server=server, decode_responses=True))
    audit = AuditLog(tmp_path / "audit", fsync=False)
    metrics = Metrics()
    policy = HaltPolicy(halt, audit, metrics=metrics)
    runner = MonitorRunner(policy=policy, metrics=metrics, clock=lambda: NOW, **kw)
    return runner, halt, audit, metrics


def test_a_stale_price_feed_halts_dispatch_after_three_passes(tmp_path):
    ages = {"12301": 5000.0, "24898": 200.0}
    runner, halt, audit, metrics = make_runner(tmp_path, freshness=lambda now: ages)
    r1 = runner.run_once()
    assert r1.status["stale_data"] == "breach" and r1.status["feature_drift"].startswith("skipped")
    assert [b.subject for b in r1.breaches] == ["12301"]
    assert metrics.registry.get_sample_value("dispatch_data_age_seconds", {"report": "12301"}) == 5000.0
    assert metrics.registry.get_sample_value("dispatch_data_age_allowance_seconds", {"report": "12301"}) == 2100
    runner.run_once()
    assert halt.is_set() == (False, "")
    r3 = runner.run_once()
    assert r3.outcome.halted_now and halt.is_set()[0] is True and "12301" in halt.is_set()[1]
    ages["12301"] = 100.0                                     # the feed recovers: the count resets, the flag stays until /resume
    r4 = runner.run_once()
    assert r4.status["stale_data"] == "ok" and runner.policy.counts["stale_data"] == 0 and halt.is_set()[0] is True


def test_a_stale_diagnostic_report_alerts_but_never_halts(tmp_path):
    """NP6-331-CD (RT ancillary prices) is not a feature input: stale means an alert, however long it lasts."""
    ages = {"12301": 120.0, "24898": 10**6}
    runner, halt, audit, metrics = make_runner(tmp_path, freshness=lambda now: ages)
    for _ in range(10):
        r = runner.run_once()
    assert r.status["stale_data"] == "alert" and [b.subject for b in r.breaches] == ["24898"] and not r.breaches[0].halts
    assert halt.is_set() == (False, "") and runner.policy.counts["stale_data"] == 0
    assert metrics.registry.get_sample_value("dispatch_data_age_seconds", {"report": "24898"}) == 10**6, "the metric and the Prometheus alert stay"
    assert metrics.registry.get_sample_value("dispatch_monitor_breaches_total", {"check": "stale_data"}) == 10
    recs = list(audit.read(types=("breach",)))
    assert len(recs) == 1 and recs[0]["halts"] is False and recs[0]["consecutive"] == 0, "audited when the streak starts, not every pass"
    ages["12301"] = 99999.0                                   # a halting report going stale next to it still halts
    for _ in range(3):
        r = runner.run_once()
    assert r.outcome.halted_now and "12301" in halt.is_set()[1]


def test_drift_is_checked_against_the_reference_stored_with_the_model(tmp_path):
    window = FeatureWindow()
    reference = REF[["price", "load"]]
    runner, halt, audit, metrics = make_runner(tmp_path, feature_window=window, reference_provider=lambda: reference)
    assert runner.run_once().status["feature_drift"].startswith("skipped: only 0 served rows")
    window.add(sample_like(REF, n=300)[["price", "load"]])
    r = runner.run_once()
    assert r.status["feature_drift"] == "ok"
    assert metrics.registry.get_sample_value("dispatch_feature_drift_psi", {"feature": "price"}) < 0.1
    window.add(sample_like(REF, n=2000, price=lambda s: s + 20)[["price", "load"]])
    r = runner.run_once()
    assert r.status["feature_drift"] == "alert" and r.breaches[0].subject == "price" and not r.breaches[0].halts
    assert metrics.registry.get_sample_value("dispatch_feature_drift_psi", {"feature": "price"}) > DRIFT_PSI_ALERT, "the metric and its alert stay"
    for _ in range(10):
        r = runner.run_once()
    assert r.status["feature_drift"] == "alert" and not r.outcome.halted_now and halt.is_set() == (False, "")
    assert runner.policy.counts["feature_drift"] == 0, "feature PSI never counts toward a halt (DESIGN.md Monitoring policy)"
    assert not any(rec["type"] == "retrain_request" for rec in audit.read()), "and no longer asks for a retrain: it flags nearly every window"


def test_feature_psi_halts_only_if_the_config_switch_is_turned_on(tmp_path, monkeypatch):
    """config.DRIFT_PSI_HALTS is the one switch: False (the lead's decision) makes PSI an alert; True restores the old behaviour."""
    monkeypatch.setattr("dispatch.config.DRIFT_PSI_HALTS", True)
    window = FeatureWindow()
    window.add(sample_like(REF, n=2000, price=lambda s: s + 20)[["price", "load"]])
    runner, halt, audit, metrics = make_runner(tmp_path, feature_window=window, reference_provider=lambda: REF[["price", "load"]])
    for _ in range(3):
        r = runner.run_once()
    assert r.status["feature_drift"] == "breach" and r.outcome.halted_now and "feature_drift x3" in halt.is_set()[1]


def test_a_model_without_a_reference_frame_skips_the_drift_check(tmp_path):
    runner, *_ = make_runner(tmp_path, feature_window=FeatureWindow(), reference_provider=lambda: None)
    assert runner.run_once().status["feature_drift"] == "skipped: the loaded model has no reference frame"


def test_capture_decay_and_slippage_providers_feed_the_gauges(tmp_path):
    state = {"cap": CaptureInputs(560.0, 1000.0, 0.56), "slip": SlippageInputs(101.0, 100.0)}
    runner, halt, audit, metrics = make_runner(tmp_path, capture_provider=lambda now: state["cap"],
                                               slippage_provider=lambda now: state["slip"])
    r = runner.run_once()
    assert r.status["capture_decay"] == "ok" and r.status["slippage"] == "ok"
    assert metrics.registry.get_sample_value("dispatch_capture_rate") == pytest.approx(0.56)
    assert metrics.registry.get_sample_value("dispatch_capture_baseline") == pytest.approx(0.56)
    assert metrics.registry.get_sample_value("dispatch_slippage_ratio") == pytest.approx(0.01)
    state["cap"], state["slip"] = CaptureInputs(300.0, 1000.0, 0.56), SlippageInputs(120.0, 100.0)
    r = runner.run_once()
    assert r.status["capture_decay"] == "breach" and r.status["slippage"] == "breach"
    assert {b.check for b in r.breaches} == {"capture_decay", "slippage"}
    state["cap"], state["slip"] = None, None
    assert runner.run_once().status["slippage"].startswith("skipped")


def test_a_check_that_raises_is_a_breach_not_a_crash(tmp_path):
    def boom(now):
        raise ConnectionError("catalog unreachable")
    runner, halt, *_ = make_runner(tmp_path, freshness=boom)
    for _ in range(3):
        r = runner.run_once()
    assert r.status["stale_data"].startswith("error: ConnectionError") and r.breaches[0].severity == "error"
    assert halt.is_set()[0] is True, "a check that cannot run is a doubt, and doubt means no dispatch"


def test_the_monitor_thread_runs_and_stops(tmp_path):
    seen = []
    runner, *_ = make_runner(tmp_path, freshness=lambda now: seen.append(1) or {"12301": 1.0})
    runner.start(0.05)
    deadline = time.time() + 5
    while len(seen) < 3 and time.time() < deadline:
        time.sleep(0.02)
    runner.stop()
    n = len(seen)
    assert n >= 3
    time.sleep(0.2)
    assert len(seen) == n, "stopped means stopped"
    runner.start(0)                                           # interval 0 = off
    assert runner._thread is None


# ================================================================================================ providers and the registry
def realised_source(world):
    return lambda spec, start, end: world.market.realised(pd.date_range(start, end, freq="15min", inclusive="left"))


def test_the_period_ends_one_price_lag_ago_on_the_grid():
    start, end = period(pd.Timestamp("2026-10-11 06:20", tz="UTC"), pd.Timedelta(days=1))
    assert end == pd.Timestamp("2026-10-11 05:15", tz="UTC") and start == end - pd.Timedelta(days=1) and PRICE_LAG == pd.Timedelta(hours=1)


def test_the_capture_provider_divides_paper_revenue_by_perfect_foresight(tmp_path):
    lp = pytest.importorskip("dispatch.optimizer.lp")
    w, _ = paper_run(tmp_path)
    now = intervals(DECISIONS)[-1] + pd.Timedelta(minutes=15) + PRICE_LAG
    span = pd.Timedelta(days=2)
    src = realised_source(w)
    prov = capture_provider(w.ledger, [w.spec], src, lp.solve, baseline=0.5, span=span)
    inp = prov(now)
    start, end = period(now, span)
    assert inp.paper_revenue_usd == pytest.approx(w.ledger.revenue_usd(w.spec, src(w.spec, start, end), start_utc=start, end_utc=end))
    assert inp.pf_revenue_usd >= inp.paper_revenue_usd > 0 and 0 < C.capture_rate(inp.paper_revenue_usd, inp.pf_revenue_usd) <= 1
    assert inp.baseline == 0.5 and math.isnan(capture_provider(w.ledger, [w.spec], src, lp.solve, span=span)(now).baseline)
    assert prov(now - pd.Timedelta(days=30)) is None, "no dispatch in that window: no data"
    assert capture_provider(w.ledger, [w.spec], lambda *a: 1 / 0, lp.solve, span=span)(now) is None, "prices not there yet: skipped, not a breach"


def test_the_slippage_provider_is_gate3_as_a_rolling_check(tmp_path, backtest):
    w, _ = paper_run(tmp_path)
    now = intervals(DECISIONS)[-1] + pd.Timedelta(minutes=15) + PRICE_LAG
    src = realised_source(w)
    total = float(backtest["revenue_usd"].sum())
    prov = slippage_provider(w.ledger, [w.spec], src, lambda spec, s, e: total, span=pd.Timedelta(days=1))
    inp = prov(now)
    assert C.slippage_ratio(inp.paper_revenue_usd, inp.backtest_revenue_usd) < 1e-9
    off = slippage_provider(w.ledger, [w.spec], src, lambda spec, s, e: total * 0.8, span=pd.Timedelta(days=1))(now)
    assert C.slippage_breach(C.slippage_ratio(off.paper_revenue_usd, off.backtest_revenue_usd)) is not None
    assert slippage_provider(w.ledger, [w.spec], lambda *a: 1 / 0, lambda *a: total, span=pd.Timedelta(days=1))(now) is None
    assert prov(now - pd.Timedelta(days=30)) is None
    p = w.ledger.path(ASSET)                                   # a hand-edited ledger is an error, which the runner counts as a breach
    lines = [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines()]
    lines[3]["soc_end_mwh"] += 1.0
    p.write_text("\n".join(json.dumps(x, sort_keys=True) for x in lines) + "\n", encoding="utf-8")
    with pytest.raises(RuntimeError, match="does not replay cleanly"):
        prov(now)
    runner, halt, *_ = make_runner(tmp_path / "mon", slippage_provider=prov)
    for _ in range(3):
        runner.run_once()
    assert halt.is_set()[0] is True


def test_gate3_outcomes_are_recorded_on_the_model_version_and_in_the_audit_log(tmp_path, backtest):
    w, _ = paper_run(tmp_path)
    good = run_gate(w, backtest)
    bad = run_gate(w, float(backtest["revenue_usd"].sum()) * 2)
    calls = []
    audit = AuditLog(tmp_path / "audit", fsync=False)
    out = record_gate3_result(good, "7", audit=audit, recorder=lambda *a, **kw: calls.append((a, kw)))
    assert out["recorded"] is True and out["passed"] is True and calls[0][0] == ("7", True)
    assert calls[0][1]["by"] == "d2:monitor" and "slippage 0.0000" in calls[0][1]["evidence"]
    record_gate3_result(bad, "7", audit=audit, recorder=lambda *a, **kw: calls.append((a, kw)))
    assert calls[1][0] == ("7", False)
    rec = list(audit.read(types=("gate3",)))
    assert [r["passed"] for r in rec] == [True, False] and rec[1]["slippage_ratio"] > SLIPPAGE_TOL and rec[0]["tolerance"] == SLIPPAGE_TOL

    def refuses(*a, **kw):
        raise RuntimeError("registry is read-only under pytest")
    failed = record_gate3_result(good, "7", audit=audit, recorder=refuses)
    assert failed["recorded"] is False and "read-only" in failed["error"] and list(audit.read(types=("gate3",)))[-1]["recorded"] is False


def test_the_default_recorder_is_d1as_registry_function():
    registry = pytest.importorskip("dispatch.registry.mlflow_registry")
    assert C._registry_recorder() is registry.record_gate3


# ================================================================================================ Gate 3
DECISIONS = [pd.Timestamp("2026-10-10 14:07", tz="UTC") + pd.Timedelta(minutes=15 * k) for k in range(60)]   # 09:07 to 23:52 CDT


def intervals(times):
    return pd.DatetimeIndex([t.floor("15min") for t in times], name="interval_start_utc")


def run_backtest(w, times) -> pd.DataFrame:
    """Stand-in for the backtest harness (D1b's walk-forward): the same forecaster and optimizer on point-in-time features,
    the plan's first interval each time, SOC carried forward, revenue at realised prices through dispatch.units. It does not
    use the cycle, the guard, the ledger or the audit log."""
    spec, soc, rows = w.spec, 10.0, []
    realised = w.market.realised(intervals(times))
    for t, (ts, px) in zip(times, realised.iterrows()):
        w.populate(t)
        df, _, _ = w.store.read_latest(SP)
        view = w.forecaster.predict(df, t).price_view("p50")
        plan = w.optimizer(spec, view, soc, mode="forecast")
        row = plan.frame.iloc[0]
        net = float(row["discharge_mw"] - row["charge_mw"])
        energy = units.interval_energy_revenue_usd(px["rt_usd_per_mwh"], net)
        wear = units.energy_revenue_usd(spec.wear_usd_per_mwh, units.energy_mwh(max(net, 0.0)))
        rows.append({"interval_start_utc": ts, "net_mw": net, "revenue_usd": energy - wear})
        soc = project_soc_end(spec, soc, net)
    return pd.DataFrame(rows).set_index("interval_start_utc")


@pytest.fixture(scope="module")
def backtest(tmp_path_factory):
    w = make_world(tmp_path_factory.mktemp("backtest"))
    return run_backtest(w, DECISIONS)


def paper_run(tmp_path, *, prepare=None, mutate=None):
    w = make_world(tmp_path)
    if mutate:
        mutate(w)
    results = replay_cycles(ASSET, DECISIONS, w.deps, prepare=prepare or (lambda t: w.populate(t)))
    return w, results


def run_gate(w, backtest):
    idx = intervals(DECISIONS)
    prices = w.market.realised(idx)
    return gate3(w.ledger, w.spec, prices, backtest, start_utc=idx[0], end_utc=idx[-1] + pd.Timedelta(minutes=15))


def test_the_backtest_actually_trades(backtest):
    """A Gate 3 test on a backtest that never moves would pass for nothing."""
    assert (backtest["net_mw"] < 0).sum() >= 4 and (backtest["net_mw"] > 0).sum() >= 4
    assert backtest["revenue_usd"].sum() > 100.0


def test_gate3_paper_replay_matches_the_backtest_with_slippage_zero(tmp_path, backtest):
    w, results = paper_run(tmp_path)
    assert [r.status for r in results] == ["dispatched"] * len(DECISIONS)
    g = run_gate(w, backtest)
    assert g.passed and g.replay_problems == [] and g.breach is None
    assert g.slippage_ratio < 1e-9, g
    assert g.paper_revenue_usd == pytest.approx(g.backtest_revenue_usd) and g.n_intervals == len(DECISIONS)
    # per interval too, not just in total
    paper = w.ledger.settle(w.spec, w.market.realised(intervals(DECISIONS)))
    assert paper["net_mw"].to_numpy() == pytest.approx(backtest["net_mw"].to_numpy())
    assert paper["revenue_usd"].to_numpy() == pytest.approx(backtest["revenue_usd"].to_numpy())
    # the number alone works as well as the per-interval frame
    assert gate3(w.ledger, w.spec, w.market.realised(intervals(DECISIONS)), float(backtest["revenue_usd"].sum())).passed


def test_gate3_tolerates_float_noise_but_not_the_tolerance(tmp_path, backtest):
    w, _ = paper_run(tmp_path)
    total = float(backtest["revenue_usd"].sum())
    prices = w.market.realised(intervals(DECISIONS))
    assert gate3(w.ledger, w.spec, prices, total * (1 + 1e-9)).passed
    assert gate3(w.ledger, w.spec, prices, total / (1 + SLIPPAGE_TOL * 0.9)).passed
    off = gate3(w.ledger, w.spec, prices, total / (1 + SLIPPAGE_TOL * 1.2))
    assert not off.passed and off.breach.check == "slippage" and off.slippage_ratio == pytest.approx(0.06, rel=0.05)


def stale_every_fifth(w):
    return lambda t: w.populate(t, age_s=3600.0 if DECISIONS.index(t) % 5 == 4 else 120.0)


def test_gate3_fails_when_cycles_are_skipped_for_stale_data(tmp_path, backtest):
    holder = {}

    def mutate(w):
        holder["w"] = w
    w = make_world(tmp_path)
    results = replay_cycles(ASSET, DECISIONS, w.deps, prepare=stale_every_fifth(w))
    skipped = [r for r in results if r.status == "stale"]
    assert len(skipped) == len(DECISIONS) // 5
    g = run_gate(w, backtest)
    assert not g.passed and g.slippage_ratio > SLIPPAGE_TOL and g.breach is not None
    assert g.largest_gaps is not None and g.largest_gaps["gap_usd"].iloc[0] > 0, "the report says which intervals differ"


def test_gate3_fails_when_the_guard_interferes(tmp_path, backtest):
    def tight_cap(w):
        cfg = w.deps.assets.get(ASSET)
        w.deps.assets = AssetRegistry([dataclasses.replace(cfg, exposure_cap_usd=1.0)])
    w, results = paper_run(tmp_path, mutate=tight_cap)
    assert any(r.status == "rejected" and "exposure_cap" in r.reasons for r in results)
    g = run_gate(w, backtest)
    assert not g.passed and g.slippage_ratio > SLIPPAGE_TOL


def test_gate3_fails_when_the_asset_is_derated(tmp_path, backtest):
    def derate(w):
        cfg = w.deps.assets.get(ASSET)
        w.spec = dataclasses.replace(cfg.spec, power_mw=7.5)
        w.deps.assets = AssetRegistry([dataclasses.replace(cfg, spec=w.spec)])
    w, results = paper_run(tmp_path, mutate=derate)
    assert all(r.status == "dispatched" for r in results)
    g = run_gate(w, backtest)
    assert not g.passed and g.slippage_ratio > SLIPPAGE_TOL


def test_gate3_fails_when_the_ledger_was_edited(tmp_path, backtest):
    w, _ = paper_run(tmp_path)
    p = w.ledger.path(ASSET)
    lines = [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines()]
    for rec in lines:
        if rec["type"] == "setpoint" and rec["net_mw"] < 0:
            rec["net_mw"] *= 0.5                                     # half the charging, SOC fields left as they were
    p.write_text("\n".join(json.dumps(x, sort_keys=True) for x in lines) + "\n", encoding="utf-8")
    g = run_gate(w, backtest)
    assert not g.passed and g.replay_problems, "the replay notices that the stored SOC no longer adds up"


def test_a_failing_gate3_trips_the_halt_policy_through_the_runner(tmp_path, backtest):
    w, _ = paper_run(tmp_path / "paper", mutate=lambda w: setattr(w.deps, "assets", AssetRegistry(
        [dataclasses.replace(w.deps.assets.get(ASSET), exposure_cap_usd=1.0)])))
    idx = intervals(DECISIONS)
    prices = w.market.realised(idx)

    def provider(now):
        g = gate3(w.ledger, w.spec, prices, backtest)
        return SlippageInputs(g.paper_revenue_usd, g.backtest_revenue_usd)
    runner, halt, audit, metrics = make_runner(tmp_path / "mon", slippage_provider=provider)
    for _ in range(3):
        runner.run_once()
    assert halt.is_set()[0] is True and "slippage" in halt.is_set()[1]
    assert metrics.registry.get_sample_value("dispatch_slippage_ratio") > SLIPPAGE_TOL


# ---- Gate 3 with D1b's real LP and D1b's real shadow run (skipped until they exist)
LP_TIMES = [pd.Timestamp("2026-10-10 14:00", tz="UTC") + pd.Timedelta(minutes=15 * k) for k in range(60)]     # interval starts, 09:00 to 23:45 CDT


@pytest.fixture(scope="module")
def d1b_shadow():
    """D1b's receding-horizon shadow run, re-planning every interval: the backtest the paper cycle has to match."""
    lp = pytest.importorskip("dispatch.optimizer.lp")
    rolling = pytest.importorskip("dispatch.optimizer.rolling")
    idx = pd.DatetimeIndex(LP_TIMES, name="interval_start_utc")
    market, model = Market(), FakeForecaster("fake-as-v1", with_as=True)
    realised = market.realised(idx)

    def forecast_fn(issued):
        store = FakeOnlineStore(market)
        store.populate(SP, issued)
        return model.predict(store.read_latest(SP)[0], issued)
    spec = pilot_spec()
    res = rolling.run_shadow(spec, realised, forecast_fn, soc0_mwh=10.0, start_utc=idx[0], end_utc=idx[-1] + pd.Timedelta(minutes=15),
                             step_intervals=1)
    assert not res.failures
    return lp, realised, res


def paper_run_with_real_lp(tmp_path, lp, mutate=None):
    w = make_world(tmp_path, forecaster=FakeForecaster("fake-as-v1", with_as=True), optimizer=lp.solve)
    if mutate:
        mutate(w)
    return w, replay_cycles(ASSET, LP_TIMES, w.deps, prepare=lambda t: w.populate(t))


def test_gate3_the_paper_cycle_matches_d1bs_shadow_run_to_the_cent(tmp_path, d1b_shadow):
    """The real LP in the cycle, the guard on every plan (ancillary awards included), the paper ledger, then Gate 3 against
    D1b's own backtest harness. Same model, same prices, same code path: slippage 0."""
    lp, realised, shadow = d1b_shadow
    w, results = paper_run_with_real_lp(tmp_path, lp)
    assert [r.status for r in results] == ["dispatched"] * len(LP_TIMES), [(r.started_utc, r.reasons) for r in results if r.status != "dispatched"][:3]
    awards = w.ledger.as_actual(w.spec).frame.filter(like="as_").sum().sum()
    assert awards > 0 and (shadow.frame["discharge_mw"] > 0).any() and (shadow.frame["charge_mw"] > 0).any(), "the plans trade and sell ancillary services"
    g = gate3(w.ledger, w.spec, realised, shadow.frame, start_utc=realised.index[0], end_utc=realised.index[-1] + pd.Timedelta(minutes=15))
    assert g.passed and g.replay_problems == [] and g.slippage_ratio < 1e-9, g
    assert g.paper_revenue_usd == pytest.approx(shadow.revenue_usd, rel=1e-9) and g.paper_revenue_usd > 100
    paper = w.ledger.settle(w.spec, realised)
    assert paper["revenue_usd"].to_numpy() == pytest.approx(shadow.frame["revenue_usd"].to_numpy())
    assert paper["soc_end_mwh"].to_numpy() == pytest.approx(shadow.frame["soc_end_mwh"].to_numpy(), abs=1e-6)


def test_gate3_fails_against_d1bs_shadow_run_when_the_guard_interferes(tmp_path, d1b_shadow):
    lp, realised, shadow = d1b_shadow

    def tight_cap(w):
        w.deps.assets = AssetRegistry([dataclasses.replace(w.deps.assets.get(ASSET), exposure_cap_usd=1.0)])
    w, results = paper_run_with_real_lp(tmp_path, lp, tight_cap)
    assert any(r.status == "rejected" and "exposure_cap" in r.reasons for r in results)
    g = gate3(w.ledger, w.spec, realised, shadow.frame, start_utc=realised.index[0], end_utc=realised.index[-1] + pd.Timedelta(minutes=15))
    assert not g.passed and g.slippage_ratio > SLIPPAGE_TOL and g.largest_gaps["gap_usd"].iloc[0] > 0


# ================================================================================================ ops files
def load_yaml(path):
    return yaml.safe_load(Path(path).read_text(encoding="utf-8"))


def exported_names():
    """Every sample name the service can expose: a registry with one child of every labelled metric."""
    m = Metrics()
    m.observe_cycle("dispatched", 0.1, asset_id="A", feature_age_s=1.0)
    m.set_data_age({"r": 1.0}, {"r": 2.0})
    m.set_drift({"f": 0.1})
    m.set_model("v", "paper")
    m.set_capture(0.5, 0.6)
    m.set_slippage(0.01)
    m.audit_error()
    m.set_halt_threshold("calibration", 2)
    m.set_calibration("A", hit_error={"p10": 0.02, "p50": 0.03, "p90": 0.04}, skill_ratio=0.9, pinball_ratio=0.95, breach=False, intervals=672)
    m.mark_calibration_evaluated(1.0)
    m.forecast_log_error()
    return {s.name for fam in m.registry.collect() for s in fam.samples}


def promql_names(expr):
    return set(re.findall(r"\bdispatch_[a-z_]+\b", expr))


def test_metric_names_listed_in_the_module_are_the_ones_exported():
    names = exported_names()
    for n in METRIC_NAMES:
        assert n in names or n + "_bucket" in names, n
    required = {"dispatch_cycle_seconds_bucket", "dispatch_cycles_total", "dispatch_guard_rejections_total", "dispatch_halted",
                "dispatch_data_age_seconds", "dispatch_feature_drift_psi", "dispatch_capture_rate", "dispatch_slippage_ratio",
                "dispatch_model_info"}
    assert required <= names


def test_alert_rules_cover_the_required_conditions_and_use_real_metrics():
    doc = load_yaml(OPS / "alerts.yml")
    rules = [r for g in doc["groups"] for r in g["rules"]]
    by_name = {r["alert"]: r for r in rules}
    for need in ("DispatchStaleData", "DispatchFeatureDrift", "DispatchCaptureRateDecay", "DispatchSlippageOverTolerance",
                 "DispatchHalted", "DispatchGuardRejectionRate", "DispatchCycleLatencyP99", "DispatchRedisReadOnly",
                 "DispatchCalibrationBreach", "DispatchCalibrationPinball", "DispatchCalibrationNotRunning", "DispatchForecastLogWriteFailures"):
        assert need in by_name, need
    names = exported_names()
    for r in rules:
        assert {"alert", "expr", "labels", "annotations"} <= set(r) and r["labels"]["severity"] in ("warning", "critical")
        assert r["annotations"]["summary"] and r["annotations"]["description"]
        for metric in promql_names(r["expr"]):
            assert metric in names, f"{r['alert']} uses {metric}, which the service does not export"
    # thresholds that mirror a constant
    assert "> 30" in by_name["DispatchCycleLatencyP99"]["expr"]
    assert f"> {DRIFT_PSI_ALERT}" in by_name["DispatchFeatureDrift"]["expr"]
    assert f"> {SLIPPAGE_TOL}" in by_name["DispatchSlippageOverTolerance"]["expr"]
    assert f"* dispatch_capture_baseline" in by_name["DispatchCaptureRateDecay"]["expr"] and f"{1 - C.CAPTURE_DECAY_TOL:g}" in by_name["DispatchCaptureRateDecay"]["expr"]
    assert f"> {1 + PINBALL_DEGRADATION_MAX:g}" in by_name["DispatchCalibrationPinball"]["expr"]
    assert "dispatch_calibration_breach == 1" in by_name["DispatchCalibrationBreach"]["expr"]
    assert "dispatch_monitor_halt_threshold" in by_name["DispatchAutoHaltApproaching"]["expr"], "calibration halts at 2, the rest at 3"
    assert "ALERT ONLY" in by_name["DispatchFeatureDrift"]["annotations"]["description"]
    assert by_name["DispatchHalted"]["labels"]["severity"] == "critical"
    # balanced brackets: the cheapest syntax check without promtool
    for r in rules:
        for open_, close in ("()", "[]", "{}"):
            assert r["expr"].count(open_) == r["expr"].count(close), r["alert"]


def test_the_dashboard_has_a_panel_for_every_alert_and_only_real_metrics():
    dash = json.loads((OPS / "grafana" / "dashboards" / "dispatch.json").read_text(encoding="utf-8"))
    assert dash["uid"] == "dispatch-ops" and dash["schemaVersion"] >= 36
    names = exported_names()
    exprs, titles, ids = [], [], []
    for p in dash["panels"]:
        titles.append(p["title"].lower())
        ids.append(p["id"])
        assert p["datasource"]["uid"] == "prometheus" and p["targets"]
        for t in p["targets"]:
            exprs.append(t["expr"])
            assert promql_names(t["expr"]) <= names, (p["title"], promql_names(t["expr"]) - names)
    assert len(set(ids)) == len(ids)
    text = " ".join(titles)
    for word in ("dispatch state", "cycles by status", "latency", "guard rejections", "rejection rate", "data age", "drift",
                 "capture", "slippage", "feature age", "audit", "redis writable", "calibration: hit-rate", "calibration: skill"):
        assert word in text, word
    used = " ".join(exprs)
    for metric in ("dispatch_halted", "dispatch_cycles_total", "dispatch_cycle_seconds_bucket", "dispatch_guard_rejections_total",
                   "dispatch_data_age_seconds", "dispatch_feature_drift_psi", "dispatch_capture_rate", "dispatch_slippage_ratio",
                   "dispatch_model_info"):
        assert metric in used, metric
    # panels do not overlap
    boxes = [(p["gridPos"]["x"], p["gridPos"]["y"], p["gridPos"]["w"], p["gridPos"]["h"]) for p in dash["panels"]]
    for i, a in enumerate(boxes):
        assert a[0] + a[2] <= 24
        for b in boxes[i + 1:]:
            assert a[0] + a[2] <= b[0] or b[0] + b[2] <= a[0] or a[1] + a[3] <= b[1] or b[1] + b[3] <= a[1], (a, b)


def test_prometheus_provisioning_and_compose_files_are_consistent():
    prom = load_yaml(OPS / "prometheus.yml")
    assert prom["rule_files"] == ["/etc/prometheus/alerts.yml"]
    job = prom["scrape_configs"][0]
    assert job["job_name"] == "dispatch" and job["metrics_path"] == "/metrics" and job["static_configs"][0]["targets"] == ["dispatch:8000"]
    ds = load_yaml(OPS / "grafana" / "provisioning" / "datasources" / "prometheus.yml")["datasources"][0]
    assert ds["uid"] == "prometheus" and ds["type"] == "prometheus" and ds["url"] == "http://prometheus:9090"
    prov = load_yaml(OPS / "grafana" / "provisioning" / "dashboards" / "dispatch.yml")["providers"][0]
    assert prov["options"]["path"] == "/var/lib/grafana/dashboards"
    compose = load_yaml(OPS / "docker-compose.yml")
    services = compose["services"]
    assert {"redis", "dispatch", "mlflow", "prometheus", "grafana"} <= set(services)
    for name, svc in services.items():
        for port in svc.get("ports", []):
            assert str(port).startswith("127.0.0.1:"), f"{name} publishes {port} beyond localhost"
        for vol in svc.get("volumes", []):
            host = str(vol).split(":")[0]
            if host.startswith("./"):
                assert (OPS / host).exists(), f"{name} mounts {host}, which does not exist"
    assert (OPS / services["dispatch"]["build"]["dockerfile"].replace("ops/", "")).exists()
    env = services["dispatch"]["environment"]
    assert env["DISPATCH_REDIS_URL"] == "redis://redis:6379/0" and env["MLFLOW_TRACKING_URI"].startswith("sqlite:////data/mlflow")
    assert "DISPATCH_API_TOKEN" in env and str(env["DISPATCH_API_TOKEN"]).startswith("${DISPATCH_API_TOKEN:?")
    mlflow_cmd = " ".join(services["mlflow"]["command"])
    assert "sqlite:////data/mlflow/mlflow.db" in mlflow_cmd, "the MLflow UI reads the same sqlite file as the registry"
    assert services["mlflow"]["volumes"] == services["dispatch"]["volumes"]
    text = (OPS / "docker-compose.yml").read_text(encoding="utf-8")
    assert re.search(r"GF_SECURITY_ADMIN_PASSWORD:\s*\$\{", text), "no literal password in the compose file"


def test_the_example_assets_file_is_the_one_the_service_loads():
    from dispatch.service import assets as A
    assert A.DEFAULT_ASSETS_FILE == OPS / "assets.example.json"
    doc = json.loads((OPS / "assets.example.json").read_text(encoding="utf-8"))
    assert [a["asset_id"] for a in doc["assets"]] == [ASSET] and all(a["mode"] == "paper" for a in doc["assets"])
