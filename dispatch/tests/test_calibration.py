"""The calibration check and the monitoring policy it belongs to (DESIGN.md "Monitoring policy", lead decision of 10 Oct 2026).

  rules         a breach for each rule: the hit rate below P10, P50 and P90, and the P50's skill against the 7-day profile median;
                the pinball rule can only alert
  policy        one daily breach is an alert plus a retrain request (recorded, nothing trains); two consecutive daily breaches halt;
                a clean day in between resets; feature PSI never halts, however long it flags
  forecast log  every cycle's issued quantiles and rt_prof7_med, joined later with the realised prices
  monitor       the daily check end to end on a real forecast log: schedule, settled intervals only, the loaded model only, a restart
  holdout       a replay over D1a's holdout forecasts (registry.holdout_forecasts): the false-breach rate per hub is under 5%, and the
                same rules still see a $10/MWh bias

The thresholds come from config.py; the test of the rules reads them from there, so a change of a threshold is a visible change here.
"""
import json

import fakeredis
import numpy as np
import pandas as pd
import pytest

from fakes import ASSET, FakeHalt, make_world, pilot_spec
from dispatch import config
from dispatch.execution.audit import AuditLog
from dispatch.monitoring import checks as C
from dispatch.monitoring.calibration import (CalibrationMonitor, CalibrationOutcome, NoEvaluation, halt_episodes, replay_windows)
from dispatch.monitoring.forecast_log import ForecastLog
from dispatch.monitoring.halt_policy import HaltPolicy, RETRAIN_CHECKS
from dispatch.monitoring.metrics import Metrics
from dispatch.monitoring.runner import MonitorRunner
from dispatch.service.state import RedisHaltFlag

# ================================================================================================ the rules, on exact windows
N = 1000
Y = np.linspace(0.0, 100.0, N)                        # realised prices spread evenly over [0, 100]: an exact quantile q has hit rate q / 100
EXACT = np.column_stack([np.full(N, 10.0), np.full(N, 50.0), np.full(N, 90.0)])   # the true P10 / P50 / P90 of that distribution
WORSE_PROFILE = np.full(N, 25.0)                      # a profile median whose MAE (31.25) is above the exact P50's (25)


def check(q=EXACT, y=Y, profile=WORSE_PROFILE, **kw):
    return C.evaluate_calibration(y, q, profile_p50=profile, **kw)


def test_thresholds_are_the_ones_the_lead_set_after_d1as_false_breach_study():
    assert config.CALIBRATION_HIT_TOL == 0.25 and config.SKILL_DEGRADATION_MAX == 0.10 and config.PINBALL_DEGRADATION_MAX == 0.25
    assert config.CALIBRATION_WINDOW_INTERVALS == 7 * 96 and config.CALIBRATION_EVAL_EVERY_S == 24 * 3600 and config.CALIBRATION_HALT_AFTER == 2
    assert config.DRIFT_PSI_HALTS is False and config.HALT_AFTER_BREACHES == 3


def test_a_calibrated_window_that_beats_the_profile_median_is_clean():
    r = check()
    assert r.enough_data and not r.breach and r.tripped == [] and r.reasons == [] and not r.pinball_alert
    assert r.hit == pytest.approx({"p10": 0.1, "p50": 0.5, "p90": 0.9}, abs=2e-3)
    assert r.skill_checked and r.skill_ratio == pytest.approx(0.8, abs=0.01)             # 25 / 31.25


@pytest.mark.parametrize("quantile,column", [("p10", 0), ("p50", 1), ("p90", 2)])
def test_the_hit_rate_rule_trips_on_each_quantile(quantile, column):
    q = EXACT.copy()
    q[:, column] += 30.0 if quantile != "p90" else -30.0          # a hit rate 0.30 away from nominal (a P90 of 60 covers 0.6, not 0.9)
    r = check(q=q, profile=None)
    assert r.breach and r.tripped == [f"hit_{quantile}"] and r.hit_error[quantile] == pytest.approx(0.30, abs=2e-3)
    assert f"hit rate below {quantile.upper()}" in r.reasons[0]
    (b,) = C.calibration_breaches(r, "A")
    assert b.check == "calibration" and b.halts and b.value == pytest.approx(0.30, abs=2e-3) and b.threshold == config.CALIBRATION_HIT_TOL


def test_the_hit_tolerance_is_exactly_the_configured_one():
    inside, outside = EXACT.copy(), EXACT.copy()
    inside[:, 0] += 100 * (config.CALIBRATION_HIT_TOL - 0.01)       # off by 0.24: still fine
    outside[:, 0] += 100 * (config.CALIBRATION_HIT_TOL + 0.01)      # off by 0.26: a breach
    assert not check(q=inside, profile=None).breach
    assert check(q=outside, profile=None).breach
    assert check(q=outside, profile=None, hit_tol=0.5).breach is False, "a caller can loosen a rule"
    assert check(q=outside, profile=None, hit_tol=False).breach is False, "and switch it off"


def test_the_skill_rule_trips_when_the_p50_loses_to_the_7_day_profile_median():
    r = check(profile=Y + 1.0)                                    # a profile median that is nearly perfect: the model's P50 (MAE 25) is far worse
    assert r.breach and r.tripped == ["skill"] and r.skill_ratio == pytest.approx(25.0, rel=0.05)
    assert r.hit_error["p10"] < 0.01, "only the skill rule tripped"
    (b,) = C.calibration_breaches(r, "A")
    assert b.halts and b.threshold == pytest.approx(1 + config.SKILL_DEGRADATION_MAX) and b.value == pytest.approx(r.skill_ratio)
    # the limit is the configured 10%: a P50 that is 5% worse than the profile is fine, 15% worse is not
    def profile_with_mae(ratio):                                   # a profile median that is wrong by exactly 25 / ratio everywhere, up and down
        return Y + np.where(np.arange(N) % 2 == 0, 1.0, -1.0) * (25.0 / ratio)
    assert not check(profile=profile_with_mae(1.05)).breach
    assert check(profile=profile_with_mae(1.15)).tripped == ["skill"]
    assert check(profile=Y + 1.0, skill_max=False).breach is False, "switched off"


def test_without_the_logged_profile_median_the_skill_rule_cannot_run_and_says_so():
    r = check(profile=None)
    assert not r.skill_checked and r.skill_ratio is None and not r.breach


def test_pinball_against_the_holdout_can_only_alert():
    ref = {"pooled": {"pinball_mean": 5.0}}                       # the exact quantiles score 7.17 on this window: 1.43 x the holdout
    r = check(reference=ref)
    assert r.pinball_alert and r.pinball_ratio == pytest.approx(7.1667 / 5.0, rel=0.01) and not r.breach and r.tripped == []
    (b,) = C.calibration_breaches(r, "A")
    assert b.subject == "A:pinball" and not b.halts and b.threshold == pytest.approx(1 + config.PINBALL_DEGRADATION_MAX)
    assert not check(reference={"pooled": {"pinball_mean": 7.0}}).pinball_alert, "inside the 25% allowance"
    # even next to a real breach the pinball alert stays a separate, non-halting Breach
    q = EXACT.copy()
    q[:, 0] += 30.0
    both = C.calibration_breaches(check(q=q, reference=ref), "A")
    assert sorted(b.halts for b in both) == [False, True]


def test_too_few_settled_forecasts_is_neither_a_breach_nor_a_pass():
    r = C.evaluate_calibration(Y[:50], EXACT[:50] + 40.0, profile_p50=WORSE_PROFILE[:50])
    assert not r.enough_data and not r.breach and C.calibration_breaches(r, "A") == []
    nan = EXACT.copy()
    nan[:, 1] = np.nan
    assert not check(q=nan).enough_data, "rows with a missing quantile are not evidence"


# ================================================================================================ the halt policy
@pytest.fixture()
def policy_kit(tmp_path):
    server = fakeredis.FakeServer()
    halt = RedisHaltFlag(fakeredis.FakeRedis(server=server, decode_responses=True), clock=lambda: pd.Timestamp("2026-10-10 18:00", tz="UTC"))
    audit = AuditLog(tmp_path / "audit", fsync=False)
    metrics = Metrics()
    retrains = []
    return HaltPolicy(halt, audit, metrics=metrics, on_retrain=retrains.append), halt, audit, metrics, retrains


def bad_day():
    return [C.Breach(C.CHECK_CALIBRATION, "PILOT", 0.4, 0.25, "hit rate below P90 is 0.500, nominal 0.9, off by 0.400 > 0.25")]


def test_one_daily_breach_is_an_alert_and_a_retrain_request_not_a_halt(policy_kit):
    policy, halt, audit, metrics, retrains = policy_kit
    out = policy.evaluate(bad_day(), ["calibration"])
    assert not out.halted_now and halt.is_set() == (False, "") and policy.counts["calibration"] == 1
    assert out.retrain_requested and len(retrains) == 1 and retrains[0].checks == ("calibration",) and "calibration" in RETRAIN_CHECKS
    recs = list(audit.read())
    assert [r["type"] for r in recs] == ["retrain_request", "breach"] or [r["type"] for r in recs] == ["breach", "retrain_request"]
    retrain = next(r for r in recs if r["type"] == "retrain_request")
    assert retrain["trains"] is False, "it records a request; nothing trains"
    assert metrics.registry.get_sample_value("dispatch_monitor_consecutive_breaches", {"check": "calibration"}) == 1
    assert metrics.registry.get_sample_value("dispatch_monitor_halt_threshold", {"check": "calibration"}) == 2
    assert metrics.registry.get_sample_value("dispatch_monitor_halt_threshold", {"check": "stale_data"}) == 3


def test_two_consecutive_daily_breaches_set_the_halt_flag(policy_kit, tmp_path):
    policy, halt, audit, metrics, retrains = policy_kit
    policy.evaluate(bad_day(), ["calibration"])
    out = policy.evaluate(bad_day(), ["calibration"])
    assert out.halted_now and out.tripped == ["calibration"] and len(retrains) == 1, "one retrain request per streak"
    halted, reason = halt.is_set()
    assert halted and reason.startswith("auto-halt after 2 consecutive breaching checks: calibration x2")
    h = next(r for r in audit.read(types=("halt",)))
    assert h["set_by"] == "monitor:halt_policy" and h["checks"] == ["calibration"] and h["thresholds"] == {"calibration": 2}
    # and the guard then refuses everything, through the real cycle
    from dispatch.service.cycle import run_cycle
    from fakes import NOW
    w = make_world(tmp_path / "w", halt=halt)
    res = run_cycle(ASSET, NOW, w.deps)
    assert res.status == "halted" and "calibration x2" in res.detail["halt_reason"]
    assert audit.verify() == (True, None)


def test_a_clean_day_between_two_breaches_resets_the_streak(policy_kit):
    policy, halt, *_ = policy_kit
    policy.evaluate(bad_day(), ["calibration"])
    policy.evaluate([], ["calibration"])
    assert policy.counts["calibration"] == 0
    policy.evaluate(bad_day(), ["calibration"])
    assert halt.is_set() == (False, ""), "bad, good, bad is not two in a row"
    policy.evaluate(bad_day(), ["calibration"])
    assert halt.is_set()[0] is True


def test_a_resume_after_a_calibration_halt_holds_until_the_next_daily_evaluation(policy_kit):
    """DESIGN.md: two daily breaches set the halt flag, "which an operator clears with /resume". The streak is kept (the next bad day halts again),
    but the passes in between do not re-set the flag: the check is not run in them."""
    policy, halt, audit, metrics, retrains = policy_kit
    policy.evaluate(bad_day(), ["calibration"])
    assert policy.evaluate(bad_day(), ["calibration"]).halted_now and halt.is_set()[0]
    halt.clear("operator looked at it", "ops")                                  # POST /resume
    for _ in range(100):                                                        # the minute-by-minute passes of the same day
        out = policy.evaluate([], ["stale_data", "feature_drift", "capture_decay", "slippage"])
    assert halt.is_set() == (False, "") and not out.halted_now and policy.counts["calibration"] == 2, "kept, but not acted on without an evaluation"
    assert policy.evaluate(bad_day(), ["calibration"]).halted_now, "the next daily evaluation, still bad, halts again"
    halt.clear("again", "ops")
    assert not policy.evaluate([], ["calibration"]).halted_now and policy.counts["calibration"] == 0, "and a clean one ends the streak"


def test_a_per_pass_check_that_still_breaches_halts_again_at_the_very_next_pass_after_a_resume(policy_kit):
    policy, halt, *_ = policy_kit
    for _ in range(3):
        policy.evaluate([C.Breach("stale_data", "12301", 5000.0, 2100.0, "stale")], ["stale_data"])
    assert halt.is_set()[0] is True
    halt.clear("resumed too early", "ops")
    out = policy.evaluate([C.Breach("stale_data", "12301", 5000.0, 2100.0, "stale")], ["stale_data"])
    assert out.halted_now and halt.is_set()[0] is True, "the cause is still there: documented, and the point"


def test_a_pass_that_did_not_evaluate_neither_counts_nor_resets(policy_kit):
    policy, halt, *_ = policy_kit
    policy.evaluate(bad_day(), ["calibration"])
    for _ in range(50):                                            # the minute-by-minute passes between two daily evaluations
        policy.evaluate([], ["stale_data"])                       # calibration is not in checks_run
    assert policy.counts["calibration"] == 1
    policy.evaluate(bad_day(), ["calibration"])
    assert halt.is_set()[0] is True


def test_the_other_checks_keep_their_threshold_of_three_and_an_explicit_threshold_overrides_all(policy_kit, tmp_path):
    policy, *_ = policy_kit
    assert policy.threshold == 3 and policy.thresholds == {**{c: 3 for c in C.CHECKS}, "calibration": 2}
    one = HaltPolicy(FakeHalt(), AuditLog(tmp_path / "b", fsync=False), threshold=1)
    assert set(one.thresholds.values()) == {1}
    custom = HaltPolicy(FakeHalt(), AuditLog(tmp_path / "c", fsync=False), thresholds={"calibration": 4})
    assert custom.thresholds["calibration"] == 4 and custom.thresholds["slippage"] == 3
    with pytest.raises(ValueError):
        HaltPolicy(FakeHalt(), AuditLog(tmp_path / "d", fsync=False), thresholds={"calibration": 0})


def test_a_restart_restores_the_streak(policy_kit):
    policy, halt, *_ = policy_kit
    policy.restore({"calibration": 1})
    policy.evaluate(bad_day(), ["calibration"])
    assert halt.is_set()[0] is True, "yesterday's breach, restored from the state file, plus today's"


def test_feature_psi_never_halts_however_long_it_flags(policy_kit):
    """D1a measured 87 to 100% of the model's features above PSI 0.2 on in-distribution windows, so PSI is an alert, full stop."""
    policy, halt, audit, metrics, retrains = policy_kit
    flagged = {f"f{i}": 5.0 for i in range(50)}
    for _ in range(1000):
        out = policy.evaluate(C.drift_breaches(flagged), ["feature_drift"])
    assert not out.halted_now and out.alerts == ["feature_drift"] and policy.counts["feature_drift"] == 0 and halt.is_set() == (False, "")
    assert all(not b.halts for b in C.drift_breaches(flagged))
    assert retrains == [], "and it asks for no retrain either"
    # a Breach that CLAIMS to halt is still not counted for a drift check: the policy enforces the rule itself
    for _ in range(10):
        policy.evaluate([C.Breach(C.CHECK_DRIFT, "f0", 5.0, 0.2, "claims to halt", halts=True)], ["feature_drift"])
    assert halt.is_set() == (False, "") and policy.counts["feature_drift"] == 0
    assert metrics.registry.get_sample_value("dispatch_monitor_breaches_total", {"check": "feature_drift"}) == 1010, "every pass is still counted for the dashboards"
    audited = [r for r in audit.read(types=("breach",))]
    assert len(audited) <= 3 and all(r["halts"] is False for r in audited), "the audit log is not flooded"


# ================================================================================================ the forecast log
def frame_for(start, n=96, base=40.0, spread=12.0):
    t = pd.Timestamp(start)
    t = t.tz_localize("UTC") if t.tzinfo is None else t.tz_convert("UTC")
    idx = pd.date_range(t.floor("15min"), periods=n, freq="15min", name="interval_start_utc")
    b = base + 10.0 * np.sin(np.arange(n) / 9.0)
    return pd.DataFrame({"rt_p10": b - spread, "rt_p50": b, "rt_p90": b + spread, "as_regup_p50": 2.0}, index=idx)


def test_the_forecast_log_round_trips_quantiles_horizons_and_the_profile_median(tmp_path):
    log = ForecastLog(tmp_path / "fc")
    fr = frame_for("2026-10-10 17:07")
    feats = pd.DataFrame({"rt_prof7_med": np.arange(96) + 100.0, "x": 1.0}, index=fr.index)
    assert log.log_cycle(asset_id="A", settlement_point="HB_NORTH", model_version="7", issued_utc=pd.Timestamp("2026-10-10 17:07", tz="UTC"),
                         frame=fr, features=feats)
    f = log.frame("2026-10-10 17:00", "2026-10-10 19:00", asset_id="A")
    assert len(f) == 8 and list(f["horizon"]) == list(range(1, 9)), "horizon 1 is the interval that contains the issue time"
    assert list(f.columns) == ["asset_id", "settlement_point", "model_version", "issued_utc", "interval_start_utc", "horizon", "rt_p10", "rt_p50",
                               "rt_p90", "rt_prof7_med"]
    assert f["rt_p50"].to_numpy() == pytest.approx(fr["rt_p50"].to_numpy()[:8], abs=1e-4)
    assert f["rt_p10"].to_numpy() == pytest.approx(fr["rt_p10"].to_numpy()[:8], abs=1e-4) and f["rt_prof7_med"].tolist() == list(np.arange(8) + 100.0)
    assert (f["model_version"] == "7").all() and (f["issued_utc"] == pd.Timestamp("2026-10-10 17:07", tz="UTC")).all()
    path = log.path_for("2026-10-10 17:07")
    assert path.name == "forecasts-20261010.jsonl" and len(path.read_text(encoding="utf-8").splitlines()) == 1
    assert log.frame("2026-10-10 17:00", "2026-10-10 19:00", model_version="8").empty and log.frame("2026-10-10 17:00", "2026-10-10 19:00", asset_id="B").empty


def test_a_cycle_logged_twice_counts_once_and_a_torn_line_is_skipped_not_fatal(tmp_path):
    log = ForecastLog(tmp_path / "fc")
    t = pd.Timestamp("2026-10-10 17:07", tz="UTC")
    log.log_cycle(asset_id="A", settlement_point="S", model_version="1", issued_utc=t, frame=frame_for(t, base=40.0))
    log.log_cycle(asset_id="A", settlement_point="S", model_version="1", issued_utc=t, frame=frame_for(t, base=50.0))      # a retry: the last line wins
    with open(log.path_for(t), "ab") as f:
        f.write(b'{"schema":1,"asset_id":"A","issued_utc":"2026-10-10T17:22:00Z","n":96,"p10":[1,')                          # a crash mid-write
    f = log.frame(t.floor("15min"), t.floor("15min") + pd.Timedelta(hours=1), asset_id="A")
    assert len(f) == 4 and f["rt_p50"].iloc[0] == pytest.approx(50.0, abs=1e-6)
    assert log.errors >= 1 and "unreadable" in log.last_error


def test_a_record_appended_after_a_torn_line_is_not_glued_onto_it(tmp_path):
    """A crash in the middle of a write leaves a last line without a newline; the next cycle's record must start on a line of its own."""
    log = ForecastLog(tmp_path / "fc")
    t = pd.Timestamp("2026-10-10 17:07", tz="UTC")
    log.log_cycle(asset_id="A", settlement_point="S", model_version="1", issued_utc=t, frame=frame_for(t))
    with open(log.path_for(t), "ab") as f:
        f.write(b'{"schema":1,"asset_id":"A","issued_utc":"2026-10-10T17:22:00Z","n":96,"p10":[1,')
    t2 = t + pd.Timedelta(minutes=30)
    assert log.log_cycle(asset_id="A", settlement_point="S", model_version="1", issued_utc=t2, frame=frame_for(t2, base=60.0))
    lines = log.path_for(t).read_bytes().split(b"\n")
    assert len(lines) == 4 and lines[-1] == b"", "first record, the torn fragment, the new record, a final newline"
    f = log.frame(t2.floor("15min"), t2.floor("15min") + pd.Timedelta(hours=1), asset_id="A")
    new = f[f["issued_utc"] == t2]
    assert len(new) == 4 and new["rt_p50"].iloc[0] == pytest.approx(60.0, abs=1e-6), "the record after the torn fragment is intact"


def test_a_forecast_log_that_cannot_be_written_is_counted_and_never_raises(tmp_path):
    log = ForecastLog(tmp_path / "fc")
    ok = log.log_cycle(asset_id="A", settlement_point="S", model_version="1", issued_utc=pd.Timestamp("2026-10-10 17:07", tz="UTC"),
                       frame=pd.DataFrame({"rt_p50": [1.0]}, index=pd.DatetimeIndex(["2026-10-10 17:00"], tz="UTC")))     # no p10 / p90
    assert ok is False and log.errors == 1 and "rt_p10" in log.last_error


def test_the_cycle_writes_its_forecast_to_the_log_and_a_log_failure_never_stops_it(tmp_path):
    from dispatch.service.cycle import run_cycle
    from fakes import NOW
    w = make_world(tmp_path / "w")
    w.deps.forecast_log = ForecastLog(tmp_path / "fc")
    assert run_cycle(ASSET, NOW, w.deps).status == "dispatched"
    f = w.deps.forecast_log.frame(NOW.floor("15min"), NOW.floor("15min") + pd.Timedelta(days=1), asset_id=ASSET)
    assert len(f) == 96 and (f["model_version"] == w.forecaster.version).all() and f["rt_prof7_med"].isna().all(), "the fake features have no rt_prof7_med"
    boom = ForecastLog(tmp_path / "fc2")
    boom.append = lambda rec: (_ for _ in ()).throw(OSError("disk full"))
    w.deps.forecast_log = boom
    w.populate(NOW + pd.Timedelta(minutes=15))
    res = run_cycle(ASSET, NOW + pd.Timedelta(minutes=15), w.deps)
    assert res.status == "dispatched" and boom.errors == 1
    assert w.metrics.registry.get_sample_value("dispatch_forecast_log_errors_total") == 1


# ================================================================================================ the daily check on a real log
BASE_T0 = pd.Timestamp("2026-10-01 00:07", tz="UTC")
NOW_EVAL = pd.Timestamp("2026-10-06 02:00", tz="UTC")


def slots(ts) -> np.ndarray:
    """Whole 15-minute slots since a fixed origin: the same integer whatever resolution the index happens to have."""
    return np.asarray((pd.DatetimeIndex(ts) - pd.Timestamp("2026-01-01", tz="UTC")) / pd.Timedelta(minutes=15)).astype(np.int64)


def base_price(ts) -> np.ndarray:
    return 40.0 + 10.0 * np.sin((slots(ts) % 96) / 9.0)


def fill_log(log, version="1", cycles=24, every=pd.Timedelta(hours=4), profile_offset=5.0):
    """Cycles issued every 4 hours from BASE_T0: each logs 96 intervals of quantiles around the same curve the price source uses."""
    for k in range(cycles):
        t = BASE_T0 + k * every
        idx = pd.date_range(t.floor("15min"), periods=96, freq="15min", tz="UTC", name="interval_start_utc")
        b = base_price(idx)
        fr = pd.DataFrame({"rt_p10": b - 12.82, "rt_p50": b, "rt_p90": b + 12.82}, index=idx)
        feats = pd.DataFrame({"rt_prof7_med": b + profile_offset}, index=idx)
        assert log.log_cycle(asset_id=ASSET, settlement_point="HB_NORTH", model_version=version, issued_utc=t, frame=fr, features=feats)


class Prices:
    """A price source: the curve plus an error e(ts) from a low-discrepancy sequence with the spread the quantiles claim (N(0, 10)),
    shifted by `bias`. bias 0 is a calibrated model; bias 40 puts every price above the issued P90."""

    def __init__(self, bias=0.0, missing_days=()):
        self.bias, self.missing_days, self.calls = bias, set(missing_days), 0

    def __call__(self, spec, start, end):
        self.calls += 1
        idx = pd.date_range(start, end, freq="15min", inclusive="left", name="interval_start_utc")
        if any(d in self.missing_days for d in {t.strftime("%Y-%m-%d") for t in idx}):
            raise LookupError("realised prices are not complete for that day")
        u = (slots(idx) * 0.6180339887498949) % 1.0                # a low-discrepancy sequence: the hit rates come out at their levels
        from statistics import NormalDist
        e = np.array([NormalDist(0, 10).inv_cdf(min(max(x, 1e-6), 1 - 1e-6)) for x in u])
        return pd.DataFrame({"rt_usd_per_mwh": base_price(idx) + e + self.bias}, index=idx)


class Model:
    version = "1"
    holdout_metrics = {}


def monitor(tmp_path, prices, *, version="1", **kw):
    log = ForecastLog(tmp_path / "fc")
    mon = CalibrationMonitor(log, [pilot_spec()], prices, forecaster=lambda: Model(), **kw)
    return log, mon


def test_a_calibrated_model_passes_the_daily_check(tmp_path):
    log, mon = monitor(tmp_path, Prices(bias=0.0))
    fill_log(log)
    out = mon(NOW_EVAL)
    assert isinstance(out, CalibrationOutcome) and not out.breach and out.breaches == []
    (a,) = out.assets
    r = a.result
    assert r.enough_data and r.n_intervals >= 0.5 * config.CALIBRATION_WINDOW_INTERVALS and r.window_intervals == 672 and r.skill_checked
    assert (a.window_end - a.window_start) == pd.Timedelta(days=7) and a.window_end == pd.Timestamp("2026-10-06 01:00", tz="UTC"), "ends one price lag ago"
    assert r.hit["p10"] == pytest.approx(0.10, abs=0.05) and r.hit["p90"] == pytest.approx(0.90, abs=0.05) and r.skill_ratio < 1.0


def test_a_biased_model_breaches_and_the_check_runs_once_a_day(tmp_path):
    log, mon = monitor(tmp_path, Prices(bias=40.0))
    fill_log(log)
    out = mon(NOW_EVAL)
    assert out.breach and out.breaches[0].halts and "hit rate" in out.breaches[0].message and mon.streak == 1
    soon = mon(NOW_EVAL + pd.Timedelta(hours=5))
    assert isinstance(soon, NoEvaluation) and "next daily evaluation" in soon.reason and mon.streak == 1, "not again within the day"
    again = mon(NOW_EVAL + pd.Timedelta(hours=24, minutes=1))
    assert isinstance(again, CalibrationOutcome) and again.breach and mon.streak == 2
    hist = [json.loads(x) for x in (mon.dir / "calibration-evaluations.jsonl").read_text(encoding="utf-8").splitlines()]
    assert len(hist) == 2 and hist[0]["breach"] is True and hist[0]["assets"][ASSET]["tripped"]


def test_the_check_scores_only_the_loaded_model_and_starts_afresh_for_a_new_one(tmp_path):
    log, mon = monitor(tmp_path, Prices(bias=40.0))
    fill_log(log, version="1")
    assert mon(NOW_EVAL).breach and mon.streak == 1
    mon.forecaster = lambda: type("M2", (), {"version": "2", "holdout_metrics": {}})()
    out = mon(NOW_EVAL + pd.Timedelta(days=1, minutes=1))
    assert isinstance(out, NoEvaluation) and "model 2" in out.reason, "version 1's forecasts say nothing about version 2"
    assert mon.streak == 0, "a new model starts with no breaches against it"
    assert mon.sync_model("2") == 0


def test_a_restart_remembers_the_schedule_and_the_streak(tmp_path):
    log, mon = monitor(tmp_path, Prices(bias=40.0))
    fill_log(log)
    mon(NOW_EVAL)
    log2, mon2 = monitor(tmp_path, Prices(bias=40.0))              # the same directory, a new process
    assert mon2.streak == 1 and mon2.sync_model("1") == 1
    out = mon2(NOW_EVAL + pd.Timedelta(hours=3))
    assert isinstance(out, NoEvaluation), "a restart in the afternoon does not evaluate a second time that day"
    assert mon2.sync_model("9") == 0, "but a different model version does start afresh"


def test_days_without_realised_prices_are_left_out_and_a_thin_window_is_not_judged(tmp_path):
    log, mon = monitor(tmp_path, Prices(bias=0.0, missing_days=("2026-10-02",)))
    fill_log(log)
    out = mon(NOW_EVAL)
    assert isinstance(out, CalibrationOutcome) and out.assets[0].result.n_intervals < 464 - 90, "a day of prices is missing: left out, not fatal"
    log3, mon3 = monitor(tmp_path / "thin", Prices(bias=40.0))
    fill_log(log3, cycles=6)                                       # one day of forecasts: 96 + 24 intervals, a fifth of the window
    thin = mon3(NOW_EVAL)
    assert isinstance(thin, NoEvaluation) and "at least 50% of the window is needed" in thin.reason
    assert mon3.streak == 0
    assert "looking again" in mon3(NOW_EVAL + pd.Timedelta(minutes=5)).reason, "an hour's pause before it tries again, not every pass"
    log4, mon4 = monitor(tmp_path / "empty", Prices())
    assert isinstance(mon4(NOW_EVAL), NoEvaluation)


def test_an_evaluation_that_crashes_counts_once_a_day_not_once_a_pass(tmp_path):
    def broken(spec, start, end):
        raise ConnectionError("lake unreachable")
    log, mon = monitor(tmp_path, broken)
    fill_log(log)
    out = mon(NOW_EVAL)
    assert out.error.startswith("ConnectionError") and out.breach and out.breaches[0].severity == "error"
    assert isinstance(mon(NOW_EVAL + pd.Timedelta(minutes=1)), NoEvaluation), "the next pass a minute later does not count it again"


def test_without_a_model_the_check_does_not_run(tmp_path):
    log = ForecastLog(tmp_path / "fc")
    mon = CalibrationMonitor(log, [pilot_spec()], Prices(), forecaster=lambda: None)
    assert mon(NOW_EVAL).reason == "no model is loaded"


def test_the_runner_turns_two_bad_days_into_an_alert_then_a_halt(tmp_path):
    server = fakeredis.FakeServer()
    halt = RedisHaltFlag(fakeredis.FakeRedis(server=server, decode_responses=True))
    audit = AuditLog(tmp_path / "audit", fsync=False)
    metrics = Metrics()
    policy = HaltPolicy(halt, audit, metrics=metrics)
    log, mon = monitor(tmp_path, Prices(bias=40.0))
    fill_log(log)
    runner = MonitorRunner(policy=policy, metrics=metrics, calibration_provider=mon)
    assert runner.run_once(NOW_EVAL - pd.Timedelta(days=30)).status["calibration"].startswith("skipped"), "a month earlier nothing was settled yet"
    mon.state.update(last_eval_utc=None, last_attempt_utc=None, tried_reason="")
    r1 = runner.run_once(NOW_EVAL)
    assert r1.status["calibration"] == "breach" and not r1.outcome.halted_now and halt.is_set() == (False, "")
    assert r1.outcome.retrain_requested and any(x["type"] == "retrain_request" for x in audit.read())
    assert metrics.registry.get_sample_value("dispatch_calibration_breach", {"asset": ASSET}) == 1
    assert metrics.registry.get_sample_value("dispatch_calibration_hit_error", {"asset": ASSET, "quantile": "p90"}) > config.CALIBRATION_HIT_TOL
    assert metrics.registry.get_sample_value("dispatch_calibration_last_evaluation_timestamp_seconds") == NOW_EVAL.timestamp()
    for minutes in (1, 2, 3, 600):                                 # the minute-by-minute passes of the same day change nothing
        rr = runner.run_once(NOW_EVAL + pd.Timedelta(minutes=minutes))
        assert rr.status["calibration"].startswith("skipped: next daily evaluation") and not rr.outcome.halted_now
    assert policy.counts["calibration"] == 1 and halt.is_set() == (False, "")
    r2 = runner.run_once(NOW_EVAL + pd.Timedelta(days=1, minutes=1))
    assert r2.status["calibration"] == "breach" and r2.outcome.halted_now
    halted, reason = halt.is_set()
    assert halted and "calibration x2" in reason and policy.counts["calibration"] == 2
    assert audit.verify() == (True, None)
    # a calibrated model on the same log would not have done any of this
    log_ok, mon_ok = monitor(tmp_path / "ok", Prices(bias=0.0))
    fill_log(log_ok)
    runner_ok = MonitorRunner(policy=HaltPolicy(FakeHalt(), AuditLog(tmp_path / "a2", fsync=False)), calibration_provider=mon_ok)
    assert runner_ok.run_once(NOW_EVAL).status["calibration"] == "ok"


def test_a_pinball_alert_next_to_a_clean_check_is_reported_and_never_halts(tmp_path):
    class RefModel(Model):
        holdout_metrics = {"HB_NORTH": {"pooled": {"pinball_mean": 1.0}}}       # the model scored a pinball of 1.0 on its holdout
    log = ForecastLog(tmp_path / "fc")
    mon = CalibrationMonitor(log, [pilot_spec()], Prices(bias=0.0), forecaster=lambda: RefModel())
    fill_log(log)
    halt = FakeHalt()
    runner = MonitorRunner(policy=HaltPolicy(halt, AuditLog(tmp_path / "a", fsync=False)), calibration_provider=mon)
    r = runner.run_once(NOW_EVAL)
    assert r.status["calibration"] == "alert" and [b.subject for b in r.breaches] == [f"{ASSET}:pinball"] and not r.breaches[0].halts
    assert r.extra["calibration"]["assets"][ASSET]["pinball_alert"] is True
    for d in range(1, 6):
        r = runner.run_once(NOW_EVAL + pd.Timedelta(days=d, minutes=1))
        assert not r.outcome.halted_now
    assert halt.is_set() == (False, "") and runner.policy.counts["calibration"] == 0


# ================================================================================================ replay over D1a's holdout forecasts
HUBS = ("HB_NORTH", "HB_WEST", "HB_HOUSTON", "HB_SOUTH")
FALSE_BREACH_BAR = 0.05                    # DESIGN.md: the false-breach rate on in-distribution windows must be under 5%
REPORT = {}


def real_registry_version():
    """(registry, version holding `research`) from the REAL store, read only; None if there is none on this machine."""
    try:
        from dispatch.registry import mlflow_registry as R
        reg = R.default()
        if not reg.exists:
            return None
        v = reg.aliases().get("research")
        return (reg, v) if v else None
    except Exception:
        return None


@pytest.fixture(scope="module")
def holdout():
    found = real_registry_version()
    if found is None:
        pytest.skip("no MLflow registry with a `research` model on this machine")
    reg, version = found
    try:
        reg.holdout_forecasts(version, HUBS[0])
    except Exception as exc:
        pytest.skip(f"the registry has no holdout forecasts for version {version}: {exc}")
    return reg, version


@pytest.mark.parametrize("hub", HUBS)
def test_the_false_breach_rate_on_the_holdout_forecasts_is_under_5_percent(holdout, hub):
    """D1a's walk-forward holdout (1 Jan 2025 to 26 Sep 2026, Winter Storm Fern included): the model never saw it and no real fault was
    injected, so every breach is a false one. Evaluated the way the monitor does: once a day over a 7-day window, config thresholds."""
    reg, version = holdout
    table = reg.holdout_forecasts(version, hub)
    ref = reg.holdout_metrics(version).get(hub)
    res = replay_windows(table, reference=ref)
    rate = float(res["breach"].mean())
    halts = halt_episodes(res["breach"].tolist())
    episodes = halt_episodes(res["breach"].tolist(), halt_after=1)
    REPORT[hub] = {"evaluations": len(res), "false_breach_rate": rate, "breach_episodes": episodes, "halt_episodes": halts,
                   "pinball_alert_rate": float(res["pinball_alert"].mean())}
    print(f"\n{hub}: {len(res)} daily evaluations, false-breach rate {rate:.2%}, {episodes} breach episodes, {halts} would have halted, "
          f"pinball alert rate {REPORT[hub]['pinball_alert_rate']:.1%}")
    assert len(res) > 500, "a full 21-month replay, not a stub"
    assert rate < FALSE_BREACH_BAR, f"{hub}: {rate:.2%} of in-distribution windows breach (bar {FALSE_BREACH_BAR:.0%})"
    assert halts <= episodes


@pytest.mark.parametrize("hub", ("HB_NORTH", "HB_HOUSTON"))
def test_the_same_rules_still_see_a_10_dollar_bias_on_the_holdout(holdout, hub):
    """The rules are not blind: add $10/MWh (about three quarters of the P50 MAE) to the realised prices and most windows breach
    (D1a measured 65 to 95% across the hubs)."""
    reg, version = holdout
    table = reg.holdout_forecasts(version, hub)
    biased = table.assign(y=table["y"] + 10.0)
    res = replay_windows(biased, reference=reg.holdout_metrics(version).get(hub))
    rate = float(res["breach"].mean())
    print(f"\n{hub}: +$10/MWh bias breaches {rate:.0%} of windows")
    assert rate > 0.5
    assert halt_episodes(res["breach"].tolist()) >= 1, "and it would have halted, which the unbiased replay (mostly) does not"


def test_halt_episodes_counts_runs_of_consecutive_breaches():
    assert halt_episodes([False, True, False, True, False]) == 0, "isolated breaches are only alerts"
    assert halt_episodes([True, True]) == 1 and halt_episodes([True, True, True, True, False, True, True]) == 2
    assert halt_episodes([True, True, True], halt_after=3) == 1 and halt_episodes([True, True], halt_after=3) == 0
    assert halt_episodes([]) == 0


def test_replay_windows_slides_one_evaluation_a_day_over_the_table():
    idx = pd.date_range("2026-01-01", periods=96 * 20, freq="15min", tz="UTC", name="interval_start_utc")
    t = pd.DataFrame({"y": 50.0, "rt_p10": 50.0, "rt_p50": 50.0, "rt_p90": 50.0, "horizon": 1, "profile_median_7d_p50": 40.0}, index=idx)
    res = replay_windows(t)
    assert res["eval_utc"].diff().dropna().eq(pd.Timedelta(days=1)).all() and len(res) >= 10
    assert (res["n_rows"] > 0).all()
    assert res["breach"].all(), "y at or below the issued P10 / P50 / P90 for every row: hit rates of 1.0"
