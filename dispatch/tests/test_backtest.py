"""Gate 3 backtest (dispatch/optimizer/backtest.py): `backtest_fn(spec, start_utc, end_utc) -> revenue_usd` over
`rolling.run_shadow(step_intervals=1)`. The properties that matter: it IS one run_shadow with a decision at every interval
start, the memo never changes an answer, failures idle and are counted, bad windows raise, and it plugs into D2's
`slippage_provider` and `PaperLedger` so that paper and backtest agree to the cent when nothing interferes."""
import numpy as np
import pandas as pd
import pytest

from dispatch.contracts import BatterySpec, Setpoint, as_award_col
from dispatch.optimizer import lp, rolling
from dispatch.optimizer.backtest import (Backtest, BacktestError, frame_price_source, ledger_soc0, make_backtest_fn)

SPEC = BatterySpec("bt1", "HB_NORTH", 10.0, 25.0, rte=0.88, wear_usd_per_mwh=10.0)
SOC0 = 8.0
HORIZON = 32
N = 24                                                  # decisions per test window: 6 hours
STEP = pd.Timedelta(minutes=15)


def make_prices(days=4, seed=21):
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2026-07-01 05:00", periods=96 * days, freq="15min", tz="UTC", name="interval_start_utc")
    t = np.arange(len(idx))
    rt = 30 + 35 * np.sin(2 * np.pi * (t % 96) / 96 - 1.4) + rng.normal(0, 8, len(idx))
    rt[rng.integers(0, len(idx), 6)] += rng.uniform(150, 500, 6)
    df = pd.DataFrame({"rt_usd_per_mwh": rt}, index=idx)
    for k, m in (("regup", 3.0), ("regdn", 2.0), ("rrs", 2.5), ("ecrs", 2.0), ("nspin", 1.0)):
        df[f"as_{k}_usd_per_mw_h"] = np.abs(rng.normal(m, m / 2, len(idx)))
    return df


PRICES = make_prices()
W0 = PRICES.index[96 * 2 + 36]                          # 14:00 UTC on day three: a 6 h window that charges, discharges and sells AS
W1 = W0 + N * STEP


def forecaster(version=None):
    return rolling.SeasonalNaiveForecaster(PRICES, horizon_intervals=HORIZON, version=version)


def backtest(fc=None, **kw):
    kw.setdefault("soc0", lambda spec, start: SOC0)
    return Backtest(frame_price_source(PRICES), fc or forecaster(), **kw)


def reference(start=W0, end=W1, soc0=SOC0, fc=None, **kw):
    """One run_shadow over the window with a decision at every interval start: what the backtest must equal."""
    return rolling.run_shadow(SPEC, PRICES.loc[start:end - STEP], fc or forecaster(), soc0_mwh=soc0, start_utc=start,
                              end_utc=end, step_intervals=1, **kw)


@pytest.fixture(scope="module")
def ref():
    return reference()


# ------------------------------------------------------------------------------------------------ it is run_shadow(step=1)
def test_the_backtest_equals_one_run_shadow_with_a_decision_at_every_interval_start(ref):
    bt = backtest()
    res = bt.run(SPEC, W0, W1)
    assert res.n_solves == N and res.n_cached == 0
    pd.testing.assert_frame_equal(res.frame, ref.frame, check_exact=True, check_freq=False)
    assert bt(SPEC, W0, W1) == pytest.approx(ref.revenue_usd, abs=1e-9) and ref.revenue_usd != 0.0       # the second call is the memo's
    assert (res.frame["decision_utc"].to_numpy() == res.frame.index.to_numpy()).all(), "decisions are made at the interval starts"
    assert res.soc_end_mwh == pytest.approx(ref.soc_end_mwh, abs=1e-12)
    assert res.frame["soc_end_mwh"].between(SPEC.soc_min_mwh - 1e-9, SPEC.soc_hi_mwh + 1e-9).all()
    assert (res.frame["discharge_mw"] > 0).any() and (res.frame["charge_mw"] > 0).any(), "the window actually trades"
    assert res.frame[[as_award_col(p) for p in SPEC.as_products]].to_numpy().sum() > 0, "and sells ancillary services"


def test_the_first_call_solves_every_decision_and_reports_it():
    bt = backtest()
    res = bt.run(SPEC, W0, W1)
    assert (res.n_solves, res.n_cached, res.n_failed) == (N, 0, 0)
    assert bt.last["solved"] == N and bt.last["memo_hits"] == 0 and bt.last["intervals"] == N and bt.memo_size == N
    assert bt.last["revenue_usd"] == res.revenue_usd and bt.last["forecast_version"] == "seasonal-naive-1d"


def test_the_no_memo_path_gives_the_same_answer_as_the_memo_path(ref):
    cold = backtest(memo=False)
    assert cold.run(SPEC, W0, W1).revenue_usd == pytest.approx(ref.revenue_usd, abs=1e-9)
    assert cold.run(SPEC, W0, W1).n_solves == N and cold.memo_size == 0


def test_make_backtest_fn_returns_the_callable_the_service_takes(ref):
    fn = make_backtest_fn(frame_price_source(PRICES), forecaster(), soc0=lambda spec, t: SOC0)
    assert callable(fn) and fn(SPEC, W0, W1) == pytest.approx(ref.revenue_usd, abs=1e-9)
    assert fn(SPEC, W0.tz_convert("America/Chicago"), W1.tz_convert("America/Chicago")) == pytest.approx(ref.revenue_usd, abs=1e-9)


# ------------------------------------------------------------------------------------------------ the memo
def test_a_repeated_call_and_a_window_one_interval_later_cost_almost_nothing(ref):
    bt = backtest()
    first = bt.run(SPEC, W0, W1)
    again = bt.run(SPEC, W0, W1)
    assert again.n_solves == 0 and again.n_cached == N
    pd.testing.assert_frame_equal(again.frame, first.frame, check_exact=True, check_freq=False)
    # the monitor's next window starts at the paper SOC one interval later; when paper follows the backtest, that SOC is the backtest's
    soc1 = float(first.frame["soc_end_mwh"].iloc[0])
    slid = bt.run(SPEC, W0 + STEP, W1 + STEP, soc0_mwh=soc1)
    assert slid.n_solves == 1 and slid.n_cached == N - 1
    fresh = reference(W0 + STEP, W1 + STEP, soc0=soc1)
    assert slid.revenue_usd == pytest.approx(fresh.revenue_usd, rel=1e-9, abs=1e-6)
    pd.testing.assert_frame_equal(slid.frame.drop(columns="soc_end_mwh"), fresh.frame.drop(columns="soc_end_mwh"),
                                  check_exact=False, atol=1e-6, rtol=1e-9, check_freq=False)


def test_the_memo_is_not_fooled_by_a_different_state_of_charge():
    bt = backtest()
    bt.run(SPEC, W0, W1)
    other = bt.run(SPEC, W0, W1, soc0_mwh=SOC0 + 3.7)
    assert other.n_solves >= 1, "a different starting state is a different decision"
    fresh = reference(soc0=SOC0 + 3.7)
    assert other.revenue_usd == pytest.approx(fresh.revenue_usd, rel=1e-9, abs=1e-6)


def test_the_memo_is_keyed_by_forecast_version():
    fc = forecaster("model-a")
    bt = backtest(fc)
    assert bt.run(SPEC, W0, W1).n_solves == N
    fc.version = "model-b"                                # a registry swap: same object, new model
    assert bt.run(SPEC, W0, W1).n_solves == N
    fc.version = "model-a"
    assert bt.run(SPEC, W0, W1).n_solves == 0


def test_the_memo_is_bounded_and_clearable():
    bt = backtest(memo_entries=10)
    bt.run(SPEC, W0, W1)
    assert bt.memo_size == 10
    bt.clear()
    assert bt.memo_size == 0 and bt.run(SPEC, W0, W1).n_solves == N


def test_a_different_spec_does_not_share_the_memo():
    other = BatterySpec("bt1", "HB_NORTH", 10.0, 25.0, rte=0.80, wear_usd_per_mwh=10.0)
    bt = backtest()
    bt.run(SPEC, W0, W1)
    assert bt.run(other, W0, W1).n_solves == N


def test_revised_realised_prices_change_the_valuation_not_the_decisions():
    revised = PRICES.copy()
    revised["rt_usd_per_mwh"] = revised["rt_usd_per_mwh"] + 5.0
    bt = Backtest(frame_price_source(PRICES), forecaster(), soc0=lambda s, t: SOC0)
    first = bt.run(SPEC, W0, W1)
    bt.price_source = frame_price_source(revised)
    second = bt.run(SPEC, W0, W1)
    assert second.n_solves == 0, "a decision never sees realised prices"
    net = (first.frame["discharge_mw"] - first.frame["charge_mw"]).to_numpy()
    assert second.revenue_usd - first.revenue_usd == pytest.approx(5.0 * net.sum() * 0.25, abs=1e-6)


# ------------------------------------------------------------------------------------------------ failures and bad input
class Flaky:
    """A forecaster that fails at chosen issue times."""
    def __init__(self, inner, bad):
        self.inner, self.bad, self.version = inner, set(bad), inner.version

    def __call__(self, issued):
        if pd.Timestamp(issued) in self.bad:
            raise ConnectionError("feature store unavailable")
        return self.inner(issued)


def test_a_failed_decision_idles_is_counted_and_is_not_remembered():
    bad = [W0 + k * STEP for k in (10, 11, 12)]
    bt = backtest(Flaky(forecaster(), bad))
    res = bt.run(SPEC, W0, W1)
    assert res.n_failed == 3 and len(res.failures) == 3 and "ConnectionError" in res.failures[0][1]
    rows = res.frame.loc[bad]
    assert not rows["plan_ok"].any() and (rows[["charge_mw", "discharge_mw"]] == 0).all().all()
    assert (rows[[as_award_col(p) for p in SPEC.as_products]] == 0).all().all(), "fail closed: no energy, no awards"
    assert rows["soc_end_mwh"].nunique() == 1, "the state of charge holds while idle"
    assert bt.last["failed"] == 3
    # the feed comes back: the failed steps are solved now, the answer is the clean run's, nothing idle was frozen in the memo
    bt.forecast_fn = Flaky(forecaster(), [])
    again = bt.run(SPEC, W0, W1)
    assert again.n_failed == 0 and again.revenue_usd == pytest.approx(reference().revenue_usd, abs=1e-6)


def test_too_many_failed_decisions_raise_when_asked_to():
    bad = [W0 + k * STEP for k in range(0, 6)]
    assert backtest(Flaky(forecaster(), bad)).run(SPEC, W0, W1).n_failed == 6                       # default: report, do not raise
    with pytest.raises(BacktestError, match="6 of 24"):
        backtest(Flaky(forecaster(), bad), max_failed_share=0.10).run(SPEC, W0, W1)


def test_missing_prices_nan_prices_and_unaligned_windows_raise():
    holes = PRICES.drop(PRICES.index[96 * 2 + 40])                      # an interval inside the window
    with pytest.raises(BacktestError, match="missing for 1 of 24"):
        Backtest(frame_price_source(holes), forecaster(), soc0=lambda s, t: SOC0).run(SPEC, W0, W1)
    nan = PRICES.copy()
    nan.iloc[96 * 2 + 37, 0] = np.nan
    with pytest.raises(BacktestError, match="NaN"):
        Backtest(frame_price_source(nan), forecaster(), soc0=lambda s, t: SOC0).run(SPEC, W0, W1)
    with pytest.raises(BacktestError, match="no realised prices"):
        Backtest(frame_price_source(PRICES.iloc[0:0]), forecaster(), soc0=lambda s, t: SOC0).run(SPEC, W0, W1)
    bt = backtest()
    with pytest.raises(ValueError, match="15-minute grid"):
        bt.run(SPEC, W0 + pd.Timedelta(minutes=5), W1)
    with pytest.raises(ValueError, match="empty window"):
        bt.run(SPEC, W1, W0)


def test_a_price_source_that_raises_propagates():
    def source(spec, start, end):
        raise LookupError("realised prices are not there yet")
    with pytest.raises(LookupError):
        Backtest(source, forecaster(), soc0=lambda s, t: SOC0)(SPEC, W0, W1)


def test_the_starting_state_is_checked_and_defaults_to_mid_window():
    bt = Backtest(frame_price_source(PRICES), forecaster())
    bt.run(SPEC, W0, W0 + 4 * STEP)
    assert bt.last["soc0_mwh"] == pytest.approx(0.5 * (SPEC.soc_min_mwh + SPEC.soc_hi_mwh))
    with pytest.raises(BacktestError, match="outside"):
        bt.run(SPEC, W0, W1, soc0_mwh=SPEC.energy_mwh + 1.0)
    with pytest.raises(BacktestError, match="outside"):
        bt.run(SPEC, W0, W1, soc0_mwh=float("nan"))
    bt.run(SPEC, W0, W0 + 4 * STEP, soc0_mwh=SPEC.soc_hi_mwh + 1e-8)
    assert bt.last["soc0_mwh"] == SPEC.soc_hi_mwh, "float dust is clipped"


def test_a_fleet_mapping_picks_the_forecaster_by_asset():
    other = BatterySpec("bt2", "HB_NORTH", 10.0, 25.0, rte=0.88, wear_usd_per_mwh=10.0)
    fn = Backtest(frame_price_source(PRICES), {"bt1": forecaster(), "bt2": forecaster("other")}, soc0=lambda s, t: SOC0)
    a, b = fn(SPEC, W0, W0 + 8 * STEP), fn(other, W0, W0 + 8 * STEP)
    assert a == pytest.approx(b, abs=1e-9) and fn.last["forecast_version"] == "other"
    with pytest.raises(BacktestError, match="no forecast function"):
        fn(BatterySpec("zz", "HB_NORTH", 10.0, 25.0), W0, W0 + 8 * STEP)


def test_unknown_options_are_rejected_at_construction():
    with pytest.raises(TypeError, match="unknown run_shadow options"):
        Backtest(frame_price_source(PRICES), forecaster(), step_intervals=4)
    with pytest.raises(TypeError, match="unknown run_shadow options"):
        Backtest(frame_price_source(PRICES), forecaster(), nonsense=True)


# ------------------------------------------------------------------------------------------------ options pass through
def test_scalar_options_reach_the_solver_and_the_answer_equals_run_shadow():
    for kw in ({"allow_as": False}, {"terminal_soc_mwh": 10.0}, {"charge_limit_mw": 8.0}):
        got = backtest(**kw).run(SPEC, W0, W0 + 12 * STEP)
        want = reference(W0, W0 + 12 * STEP, **kw)
        assert got.revenue_usd == pytest.approx(want.revenue_usd, abs=1e-9), kw
        pd.testing.assert_frame_equal(got.frame, want.frame, check_exact=True, check_freq=False)
    no_as = backtest(allow_as=False).run(SPEC, W0, W1)
    assert no_as.frame[[as_award_col(p) for p in SPEC.as_products]].to_numpy().sum() == 0


def test_derate_series_take_the_whole_window_path_and_match_run_shadow():
    avail = pd.Series(1.0, index=PRICES.index)
    avail.loc[W0 + 6 * STEP: W0 + 14 * STEP] = 0.4
    bt = backtest(availability=avail)
    got = bt.run(SPEC, W0, W1)
    want = reference(availability=avail)
    pd.testing.assert_frame_equal(got.frame, want.frame, check_exact=True, check_freq=False)
    assert bt.memo_size == 0, "lookahead over a derate needs the whole window in one run; the memo is off"
    assert got.frame.loc[W0 + 6 * STEP: W0 + 14 * STEP, "discharge_mw"].max() <= 0.4 * SPEC.power_mw + 1e-9


# ------------------------------------------------------------------------------------------------ D2's ledger and provider
def record_frame(ledger, frame, model="bt-test"):
    """Execute the backtest's own decisions through D2's paper ledger, as the cycle would."""
    products = list(SPEC.as_products)
    for ts, row in frame.iterrows():
        sp = Setpoint(SPEC.asset_id, ts, float(row["discharge_mw"] - row["charge_mw"]),
                      {p: float(row[as_award_col(p)]) for p in products if row[as_award_col(p)] > 0},
                      f"plan-{ts.isoformat()}", model, row["decision_utc"])
        ledger.record(SPEC, sp)


def test_paper_and_backtest_agree_to_the_cent_through_the_real_ledger_and_the_slippage_provider(tmp_path):
    from dispatch.execution.ledger import PaperLedger
    from dispatch.monitoring.providers import slippage_provider
    ledger = PaperLedger(tmp_path, fsync=False)
    assert ledger.init_asset(SPEC, SOC0)
    bt = Backtest(frame_price_source(PRICES), forecaster(), soc0=ledger_soc0(ledger))
    first = bt.run(SPEC, W0, W1)
    record_frame(ledger, first.frame)                       # the paper cycle executes what the backtest decided
    rep = ledger.replay(SPEC, PRICES, start_utc=W0, end_utc=W1)
    assert rep.ok, rep.problems[:3]
    assert rep.revenue_usd == pytest.approx(first.revenue_usd, abs=1e-6)
    provider = slippage_provider(ledger, [SPEC], frame_price_source(PRICES), bt, span=N * STEP, lag=pd.Timedelta(0))
    inputs = provider(W1)                                   # period(now) = [now - span, now) with no price lag
    assert inputs is not None
    assert inputs.paper_revenue_usd == pytest.approx(inputs.backtest_revenue_usd, abs=1e-6)
    assert bt.last["start_utc"] == W0 and bt.last["end_utc"] == W1
    assert bt.last["soc0_mwh"] == pytest.approx(SOC0), "the window starts from the ledger's state of charge"
    # the window one interval later: the ledger's SOC is the paper's, the memo covers all but the newest decision
    record_frame(ledger, bt.run(SPEC, W1, W1 + STEP).frame)
    nxt = provider(W1 + STEP)
    assert nxt is not None and bt.last["solved"] <= 2 and bt.last["memo_hits"] >= N - 2, bt.last
    assert nxt.paper_revenue_usd == pytest.approx(nxt.backtest_revenue_usd, abs=1e-6)


def test_gate3_fails_when_paper_departs_from_the_backtest(tmp_path):
    from dispatch.execution.ledger import PaperLedger
    from dispatch.monitoring.checks import SLIPPAGE_TOL, slippage_ratio
    ledger = PaperLedger(tmp_path, fsync=False)
    ledger.init_asset(SPEC, SOC0)
    bt = Backtest(frame_price_source(PRICES), forecaster(), soc0=ledger_soc0(ledger))
    frame = bt.run(SPEC, W0, W1).frame.copy()
    frame["charge_mw"] = 0.0
    frame["discharge_mw"] = 0.0                              # a guard that rejected everything: the battery idled
    for p in SPEC.as_products:
        frame[as_award_col(p)] = 0.0
    record_frame(ledger, frame)
    rep = ledger.replay(SPEC, PRICES, start_utc=W0, end_utc=W1)
    assert slippage_ratio(rep.revenue_usd, bt(SPEC, W0, W1)) > SLIPPAGE_TOL


def test_a_window_that_starts_before_the_ledger_is_clipped_to_the_first_executed_interval(tmp_path):
    """The monitor asks for the last seven days from day one of paper trading; paper has hours, not days."""
    from dispatch.execution.ledger import PaperLedger
    from dispatch.monitoring.providers import slippage_provider
    late = W0 + 6 * STEP
    ledger = PaperLedger(tmp_path, fsync=False)
    ledger.init_asset(SPEC, SOC0)
    paper = Backtest(frame_price_source(PRICES), forecaster(), soc0=lambda s, t: SOC0).run(SPEC, late, W1)
    record_frame(ledger, paper.frame)                           # the paper service started at `late`
    bt = Backtest(frame_price_source(PRICES), forecaster(), ledger=ledger)
    inputs = slippage_provider(ledger, [SPEC], frame_price_source(PRICES), bt, span=N * STEP, lag=pd.Timedelta(0))(W1)
    assert bt.last["start_utc"] == late and bt.last["intervals"] == N - 6
    assert inputs.paper_revenue_usd == pytest.approx(inputs.backtest_revenue_usd, abs=1e-6) and inputs.backtest_revenue_usd != 0.0
    unclipped = Backtest(frame_price_source(PRICES), forecaster(), ledger=ledger, clip_to_ledger=False)
    assert unclipped(SPEC, W0, W1) != pytest.approx(inputs.backtest_revenue_usd, abs=1e-6), "seven days of backtest against two hours of paper"


def test_intervals_the_ledger_did_not_execute_inside_the_window_still_count_as_slippage(tmp_path):
    """An outage or a setpoint the guard rejected leaves no entry; the backtest decided there, so paper is short. Not masked."""
    from dispatch.execution.ledger import PaperLedger
    from dispatch.monitoring.checks import slippage_ratio
    ledger = PaperLedger(tmp_path, fsync=False)
    ledger.init_asset(SPEC, SOC0)
    full = Backtest(frame_price_source(PRICES), forecaster(), soc0=lambda s, t: SOC0).run(SPEC, W0, W1)
    bt = Backtest(frame_price_source(PRICES), forecaster(), ledger=ledger)
    missed = full.frame.iloc[-3:]
    record_frame(ledger, full.frame.iloc[:-3])                  # the last three decisions never reached the ledger
    paper = ledger.replay(SPEC, PRICES, start_utc=W0, end_utc=W1).revenue_usd
    assert paper == pytest.approx(full.revenue_usd - missed["revenue_usd"].sum(), abs=1e-6)
    assert bt(SPEC, W0, W1) == pytest.approx(full.revenue_usd, abs=1e-9)
    assert slippage_ratio(paper, bt(SPEC, W0, W1)) == pytest.approx(abs(missed["revenue_usd"].sum()) / abs(full.revenue_usd), rel=1e-9)


def test_a_ledger_with_nothing_in_the_window_raises(tmp_path):
    from dispatch.execution.ledger import PaperLedger
    ledger = PaperLedger(tmp_path, fsync=False)
    ledger.init_asset(SPEC, SOC0)
    with pytest.raises(BacktestError, match="no executed interval"):
        Backtest(frame_price_source(PRICES), forecaster(), ledger=ledger)(SPEC, W0, W1)
    with pytest.raises(BacktestError, match="cannot read the paper ledger"):
        Backtest(frame_price_source(PRICES), forecaster(), ledger=ledger)(BatterySpec("nope", "HB_NORTH", 10.0, 25.0), W0, W1)


def test_ledger_soc0_reads_the_state_at_the_window_start(tmp_path):
    from dispatch.execution.ledger import PaperLedger
    ledger = PaperLedger(tmp_path, fsync=False)
    ledger.init_asset(SPEC, 12.5)
    assert ledger_soc0(ledger)(SPEC, W0) == 12.5


def test_the_backtest_uses_the_planner_the_cycle_uses():
    """Same function, same arguments: the first decision of the backtest equals `lp.solve(mode="forecast")` on the P50 forecast."""
    fc = forecaster()
    res = backtest(fc).run(SPEC, W0, W0 + STEP)
    plan = lp.solve(SPEC, fc(W0).price_view("p50"), SOC0, mode="forecast")
    assert plan.ok
    row, first = res.frame.iloc[0], plan.frame.iloc[0]
    assert row["charge_mw"] == pytest.approx(first["charge_mw"], abs=1e-9) and row["discharge_mw"] == pytest.approx(first["discharge_mw"], abs=1e-9)
    assert row["soc_end_mwh"] == pytest.approx(first["soc_end_mwh"], abs=1e-9)
