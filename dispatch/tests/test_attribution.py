"""FR7 tests (Gate 2): the attribution engine on synthetic assets whose answer is known, plus the 60-day disclosure
readers on synthetic zips. The real case study is checked at the end against the saved report summary."""
import io
import json
import zipfile

import numpy as np
import pandas as pd
import pytest

from dispatch import paths
from dispatch.attribution import disclosure as D
from dispatch.attribution import engine
from dispatch.config import ATTRIBUTION_CLOSURE_MAX
from dispatch.contracts import ATTRIBUTION_COLUMNS, ActualDispatch, BatterySpec, as_award_col, as_price_col
from dispatch.optimizer import lp, rolling

DAYS = 4
SOC0 = 6.0


def make_prices(days=DAYS, seed=11):
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2026-07-01 05:00", periods=96 * days, freq="15min", tz="UTC")      # starts at 00:00 CPT
    t = np.arange(len(idx))
    rt = 28 + 30 * np.sin(2 * np.pi * (t % 96) / 96 - 1.4) + rng.normal(0, 7, len(idx))
    rt[rng.integers(0, len(idx), 5)] += rng.uniform(150, 600, 5)
    df = pd.DataFrame({"rt_usd_per_mwh": rt}, index=idx)
    for k, m in (("regup", 3.0), ("regdn", 2.0), ("rrs", 2.5), ("ecrs", 2.0), ("nspin", 1.0)):
        df[f"as_{k}_usd_per_mw_h"] = np.abs(rng.normal(m, m / 2, len(idx)))
    return df


SPEC = BatterySpec("syn", "HB_NORTH", 10.0, 25.0, rte=0.88, wear_usd_per_mwh=10.0)


def to_actual(plan, avail=None, drop=None):
    f = plan.frame
    d = pd.DataFrame({"net_mw": f.discharge_mw - f.charge_mw, "soc_mwh": f.soc_end_mwh})
    for k in SPEC.as_products:
        d[as_award_col(k)] = f[as_award_col(k)]
    d["available_mw"] = SPEC.power_mw * (1.0 if avail is None else np.asarray(avail))
    if drop is not None:
        d.loc[drop, ["net_mw", "soc_mwh"] + [as_award_col(k) for k in SPEC.as_products]] = np.nan
    return ActualDispatch("syn", "synthetic", d)


def with_history(prices, days=2, seed=3):
    """The prices preceded by `days` of earlier prices, so a seasonal-naive forecaster has something to look back on."""
    past = make_prices(days, seed)
    past.index = prices.index[:96 * days] - pd.Timedelta(days=days)
    return pd.concat([past, prices])


def actual_from_frame(f, source):
    return ActualDispatch("syn", source, pd.DataFrame(
        {"net_mw": f.discharge_mw - f.charge_mw, "soc_mwh": f.soc_end_mwh, "available_mw": 10.0,
         **{as_award_col(k): f[as_award_col(k)] for k in SPEC.as_products}}, index=f.index))


@pytest.fixture(scope="module")
def prices():
    return make_prices()


@pytest.fixture(scope="module")
def pf_plan(prices):
    return lp.solve(SPEC, prices, SOC0, mode="pf")


def components(r):
    return r.summary["components_usd"]


# ---------------------------------------------------------------------------------------------------- the contract
def test_frame_has_the_attribution_columns_and_components_sum_to_the_gap_exactly(prices):
    rng = np.random.default_rng(1)
    idx = prices.index
    # an imperfect actual: the dispatch of a seasonal-naive forecaster, with no AS awards in the frame below
    fc = rolling.SeasonalNaiveForecaster(with_history(prices, 2, 5), horizon_intervals=96)
    f = rolling.run_shadow(SPEC, prices, fc, soc0_mwh=SOC0, step_intervals=8).frame
    actual = ActualDispatch("syn", "noisy", pd.DataFrame({"net_mw": f.discharge_mw - f.charge_mw, "soc_mwh": f.soc_end_mwh,
                                                         "available_mw": 10.0}, index=f.index))
    df = engine.attribute(SPEC, actual, prices, None, position_usd=pd.Series(rng.normal(0, 5, len(idx)), index=idx))
    assert list(df.columns) == ["interval_start_utc"] + list(ATTRIBUTION_COLUMNS)
    assert len(df) == len(prices) and str(df["interval_start_utc"].dt.tz) == "UTC"
    comp = df[["forecast_usd", "soc_usd", "as_usd", "availability_usd", "unexplained_usd"]].sum(axis=1)
    assert np.abs(df["gap_usd"] - comp).max() < 1e-9                         # exact, interval by interval
    assert np.allclose(df["gap_usd"], df["pf_usd"] - df["actual_usd"], atol=1e-9)
    s = df.attrs["summary"]
    assert s["identity_max_abs_error"] < 1e-9 and s["stage_monotone"]
    assert s["closure"] == pytest.approx(abs(df["unexplained_usd"].sum()) / abs(df["gap_usd"].sum()))


def test_actual_equal_to_perfect_foresight_has_no_gap(prices, pf_plan):
    r = engine.run_attribution(SPEC, to_actual(pf_plan), prices, None)
    f = r.frame
    assert abs(f["gap_usd"].sum()) < 1e-6 * r.summary["pf_usd"]
    assert f[["forecast_usd", "soc_usd", "as_usd", "availability_usd", "unexplained_usd"]].abs().to_numpy().max() < 1e-6
    assert r.summary["capture_actual"] == pytest.approx(1.0, abs=1e-9)


def test_idle_asset_gap_equals_perfect_foresight(prices):
    idle = ActualDispatch("syn", "idle", pd.DataFrame({"net_mw": 0.0, "soc_mwh": SOC0, "available_mw": 10.0},
                                                      index=prices.index))
    r = engine.run_attribution(SPEC, idle, prices, None)
    f = r.frame
    assert f["gap_usd"].sum() == pytest.approx(f["pf_usd"].sum())
    assert f["actual_usd"].abs().max() == 0.0
    assert np.abs(f["gap_usd"] - f[["forecast_usd", "soc_usd", "as_usd", "availability_usd"]].sum(axis=1)).max() < 1e-9
    assert r.summary["closure"] == 0.0


def test_injected_availability_loss_lands_in_the_availability_component(prices):
    t = np.arange(len(prices))
    avail = np.where((t % 96 >= 56) & (t % 96 < 80), 0.4, 1.0)                  # 40% power for six hours a day
    best_under_derate = lp.solve(SPEC, prices, SOC0, mode="pf", availability=pd.Series(avail, index=prices.index))
    r = engine.run_attribution(SPEC, to_actual(best_under_derate, avail), prices, None)
    c, gap = components(r), r.summary["gap_usd"]
    assert gap > 100
    assert c["availability_usd"] == pytest.approx(gap, rel=1e-6)
    for k in ("forecast_usd", "soc_usd", "as_usd", "unexplained_usd"):
        assert abs(c[k]) < 1e-5 * gap


def test_injected_energy_derate_lands_in_the_availability_component(prices):
    cap = pd.Series(12.0, index=prices.index)                                   # reported max SOC cut from 25 to 12 MWh
    best = lp.solve(SPEC, prices, SOC0, mode="pf", soc_max_series=cap)
    act = to_actual(best)
    act.frame["soc_max_mwh"] = 12.0
    r = engine.run_attribution(SPEC, act, prices, None)
    c, gap = components(r), r.summary["gap_usd"]
    assert gap > 100 and c["availability_usd"] == pytest.approx(gap, rel=1e-6)


def test_injected_award_difference_lands_in_the_as_component(prices):
    no_as = lp.solve(SPEC, prices, SOC0, mode="pf", allow_as=False)             # optimal dispatch, but sells no AS
    r = engine.run_attribution(SPEC, to_actual(no_as), prices, None)
    c, gap = components(r), r.summary["gap_usd"]
    assert gap > 100 and c["as_usd"] == pytest.approx(gap, rel=1e-6)
    for k in ("forecast_usd", "soc_usd", "availability_usd", "unexplained_usd"):
        assert abs(c[k]) < 1e-5 * gap
    # half the optimal awards, energy re-optimised around them: still all AS
    pf = lp.solve(SPEC, prices, SOC0, mode="pf")
    half = pf.frame[[as_award_col(k) for k in SPEC.as_products]] * 0.5
    again = lp.solve(SPEC, prices, SOC0, mode="pf", fixed_as=half)
    r2 = engine.run_attribution(SPEC, to_actual(again), prices, None)
    assert r2.summary["gap_usd"] > 10 and components(r2)["as_usd"] == pytest.approx(r2.summary["gap_usd"], rel=1e-6)


def test_a_bad_dispatch_with_the_right_awards_is_a_forecast_or_decision_cost(prices, pf_plan):
    # keep the optimal awards but never charge: revenue is lost in the dispatch, not in awards or availability
    f = pf_plan.frame
    act = ActualDispatch("syn", "inert", pd.DataFrame({"net_mw": 0.0, "soc_mwh": SOC0, "available_mw": 10.0,
                                                       **{as_award_col(k): f[as_award_col(k)] for k in SPEC.as_products}},
                                                      index=f.index))
    r = engine.run_attribution(SPEC, act, prices, None)
    c = components(r)
    assert c["forecast_usd"] > 0 and abs(c["availability_usd"]) < 1e-6 and abs(c["unexplained_usd"]) < 1e-9
    # the awards were optimal, so most of the loss is the energy side; the AS term is only the knock-on of fixing them
    assert c["forecast_usd"] > abs(c["as_usd"])


def test_soc_position_is_attributed_to_the_soc_component():
    prices = make_prices(2, 21)
    idx = prices.index
    day1, day2 = idx[:96], idx[96:]
    # day 1: optimal, but the battery is forced to end it nearly empty (as if a poor decision drained it at midnight)
    low_end = pd.Series(np.where(np.arange(96) >= 94, 1.0, 25.0), index=day1)
    p1 = lp.solve(SPEC, prices.loc[day1], SOC0, mode="pf", soc_max_series=low_end)
    # day 2: optimal from that low starting SOC
    p2 = lp.solve(SPEC, prices.loc[day2], float(p1.frame.soc_end_mwh.iloc[-1]), mode="pf")
    both = pd.concat([p1.frame, p2.frame])
    act = ActualDispatch("syn", "two-day", pd.DataFrame(
        {"net_mw": both.discharge_mw - both.charge_mw, "soc_mwh": both.soc_end_mwh, "available_mw": 10.0,
         **{as_award_col(k): both[as_award_col(k)] for k in SPEC.as_products}}, index=both.index))
    r = engine.run_attribution(SPEC, act, prices, None)
    fr = r.frame.set_index("interval_start_utc")
    d2 = fr.loc[day2]
    assert fr["soc_usd"].sum() > 1.0, "the drained start of day 2 should cost something"
    assert d2["soc_usd"].sum() > 1.0, "and it should show up on day 2"
    assert abs(d2["forecast_usd"].sum()) < 1e-4 * max(1.0, d2["pf_usd"].sum()), "day 2 itself was played optimally"


def test_data_gaps_are_unexplained_and_everything_still_sums(prices, pf_plan):
    drop = prices.index[100:130]
    r = engine.run_attribution(SPEC, to_actual(pf_plan, drop=drop), prices, None)
    f = r.frame.set_index("interval_start_utc")
    assert r.summary["coverage"] == pytest.approx(1 - 30 / len(prices))
    assert np.allclose(f.loc[drop, "unexplained_usd"], f.loc[drop, "pf_usd"])
    assert (f.loc[drop, ["forecast_usd", "soc_usd", "as_usd", "availability_usd"]] == 0).all().all()
    assert np.abs(f["gap_usd"] - f[["forecast_usd", "soc_usd", "as_usd", "availability_usd", "unexplained_usd"]].sum(axis=1)).max() < 1e-9
    assert r.summary["identity_max_abs_error"] < 1e-9
    assert r.summary["closure"] > 0.9            # all of this (tiny) gap IS the data gap: closure honestly reports it


def test_closure_is_below_five_percent_on_clean_synthetic_data(prices):
    rng = np.random.default_rng(5)
    fc = rolling.SeasonalNaiveForecaster(with_history(prices, 2, 7), horizon_intervals=96)
    f = rolling.run_shadow(SPEC, prices, fc, soc0_mwh=SOC0, step_intervals=4).frame
    actual = actual_from_frame(f, "forecast-driven")
    # (1) no reported revenue: the revaluation IS the actual, closure is 0 by construction
    r0 = engine.run_attribution(SPEC, actual, prices, None)
    assert r0.summary["closure_basis"] == "revaluation" and r0.summary["closure"] < ATTRIBUTION_CLOSURE_MAX
    # (2) an owner export that differs from the revaluation by ~1% noise: the unexplained term picks it up
    reval = r0.frame.set_index("interval_start_utc")["actual_usd"]
    reported = reval + rng.normal(0, 0.01, len(reval)) * reval.abs().mean() * 3
    r1 = engine.run_attribution(SPEC, actual, prices, None, reported_usd=reported)
    assert r1.summary["closure_basis"] == "reported"
    assert 0 < r1.summary["closure"] < ATTRIBUTION_CLOSURE_MAX
    assert r1.summary["gap_usd"] == pytest.approx(r0.summary["pf_usd"] - reported.sum())


def test_day_ahead_positions_are_part_of_actual_revenue_and_go_to_forecast(prices, pf_plan):
    pos = pd.Series(0.0, index=prices.index)
    pos.iloc[50:60] = 7.0
    base = engine.run_attribution(SPEC, to_actual(pf_plan), prices, None)
    withpos = engine.run_attribution(SPEC, to_actual(pf_plan), prices, None, position_usd=pos)
    assert withpos.summary["actual_usd"] == pytest.approx(base.summary["actual_usd"] + 70.0)
    assert withpos.summary["components_usd"]["forecast_usd"] == pytest.approx(
        base.summary["components_usd"]["forecast_usd"] - 70.0)
    assert withpos.summary["identity_max_abs_error"] < 1e-9


def test_shapley_orderings_sum_to_the_same_total_and_report_the_spread(prices):
    t = np.arange(len(prices))
    avail = np.where((t % 96 >= 56) & (t % 96 < 80), 0.4, 1.0)
    # actual: derated and with awards fixed at half of what is optimal -> several components are non-zero
    half = lp.solve(SPEC, prices, SOC0, mode="pf").frame[[as_award_col(k) for k in SPEC.as_products]] * 0.5
    plan = lp.solve(SPEC, prices, SOC0, mode="pf", availability=pd.Series(avail, index=prices.index), fixed_as=half)
    r = engine.run_attribution(SPEC, to_actual(plan, avail), prices, None, shapley=True)
    sh, seq = r.summary["shapley_total_usd"], r.summary["sequential_total_usd"]
    assert sum(sh.values()) == pytest.approx(sum(seq.values()), rel=1e-6)       # same r0 - r3, split differently
    assert r.summary["order_spread_usd"] >= 0
    three = ["availability_usd", "as_usd", "soc_usd"]
    per_interval = r.shapley[three].sum(axis=1).to_numpy() - r.frame[three].sum(axis=1).to_numpy()
    assert np.abs(per_interval).max() < 1e-6                                      # equal interval by interval too


def test_shadow_is_reported_alongside_and_never_beats_perfect_foresight(prices, pf_plan):
    fc = rolling.SeasonalNaiveForecaster(with_history(prices, 2, 8), horizon_intervals=96)
    idle = ActualDispatch("syn", "idle", pd.DataFrame({"net_mw": 0.0, "soc_mwh": SOC0, "available_mw": 10.0},
                                                      index=prices.index))
    r = engine.run_attribution(SPEC, idle, prices, fc, shadow_step_intervals=4)
    s = r.summary
    assert 0 < s["shadow_usd"] <= s["pf_usd"] + 1e-6 and 0 < s["capture_shadow"] <= 1
    assert s["shadow_recovers_share_of_gap"] == pytest.approx(s["shadow_usd"] / s["pf_usd"])   # actual is zero
    assert not r.frame["shadow_usd"].isna().any()
    top = engine.explain_top(r, 5)
    assert len(top) == 5 and top["gap_usd"].is_monotonic_decreasing and top["explanation"].str.len().min() > 20


def test_missing_prices_fail_closed(prices, pf_plan):
    bad = prices.copy()
    bad.iloc[5, 0] = np.nan
    with pytest.raises(ValueError):
        engine.run_attribution(SPEC, to_actual(pf_plan), bad, None)


# ---------------------------------------------------------------------------------------------------- 60-day disclosure readers
SCED_HEADER = ['SCED Time Stamp', 'Repeated Hour Flag', 'QSE', 'DME', 'Resource Name', 'Resource Type', 'Output Schedule',
               'HSL', 'HDL', 'LSL', 'LDL', 'Telemetered Resource Status', 'Base Point', 'Telemetered Net Output',
               'State of Charge', 'Minimum SOC', 'Maximum SOC', 'AS Awards NSPIN', 'AS Awards RRSFFR',
               'AS Awards RRSPFR', 'AS Awards RRSUFR', 'AS Awards ECRS', 'AS Awards REGUP', 'AS Awards REGDN']


def sced_csv(rows):
    out = io.StringIO()
    out.write(",".join(f'"{h}"' for h in SCED_HEADER) + "\n")
    for r in rows:
        d = {h: "" for h in SCED_HEADER}
        d.update({'Repeated Hour Flag': 'N', 'QSE': 'Q', 'DME': 'D', 'Resource Name': 'TEST_ESR1', 'Resource Type': 'ESR',
                  'Telemetered Resource Status': 'ON', 'HSL': '50', 'LSL': '-50', 'HDL': '50', 'LDL': '-50',
                  'Output Schedule': '0', 'Base Point': '0', 'Minimum SOC': '0', 'Maximum SOC': '100'})
        d.update(r)
        out.write(",".join(f'"{d[h]}"' for h in SCED_HEADER) + "\n")
    return out.getvalue()


def to_rows(rows):
    """Raw SCED csv rows -> the converted ESR frame the readers produce (without writing a zip)."""
    raw = pd.read_csv(io.StringIO(sced_csv(rows)), dtype=str, keep_default_na=False, usecols=list(D.SCED_ESR_COLS))
    return D._convert_sced(raw.rename(columns=D.SCED_ESR_COLS))


def make_zip(tmp_path, rows):
    p = tmp_path / "ext.00013052.0000000000000000.20261010.055240368.60_Day_SCED_Disclosure.zip"
    with zipfile.ZipFile(p, "w") as z:
        z.writestr("60d_ESR_Data_in_SCED-10-OCT-26.csv", sced_csv(rows))
        z.writestr("60d_SCED_Gen_Resource_Data-10-OCT-26.csv", "not,an,esr\n1,2,3\n")
    return p


def runs(start="07/01/2026 00:00:10", n=9, net=0.0, soc=50.0, **kw):
    t0 = pd.Timestamp(pd.to_datetime(start, format="%m/%d/%Y %H:%M:%S"))
    rows = []
    for i in range(n):
        ts = (t0 + pd.Timedelta(minutes=5 * i)).strftime("%m/%d/%Y %H:%M:%S")
        row = {'SCED Time Stamp': ts, 'Telemetered Net Output': str(net[i] if hasattr(net, "__len__") else net),
               'State of Charge': str(soc[i] if hasattr(soc, "__len__") else soc)}
        row.update({k: str(v[i] if hasattr(v, "__len__") and not isinstance(v, str) else v) for k, v in kw.items()})
        rows.append(row)
    return rows


def test_reader_converts_central_time_to_utc_and_maps_rrs_awards(tmp_path):
    z = make_zip(tmp_path, runs(n=3, **{'AS Awards RRSPFR': 5, 'AS Awards RRSFFR': 2, 'AS Awards ECRS': 1}))
    df = D.read_esr_sced(z)
    assert len(df) == 3 and df["resource"].eq("TEST_ESR1").all()
    assert df["ts_utc"].iloc[0] == pd.Timestamp("2026-07-01 05:00:10", tz="UTC")           # CDT = UTC-5
    assert df["aw_rrspfr"].iloc[0] == 5 and df["aw_regup"].iloc[0] == 0                       # blank award -> 0
    assert df["soc_mwh"].dtype.kind == "f" and df["hsl_mw"].iloc[0] == 50


def test_repeated_hour_flag_resolves_the_fall_back_hour():
    raw = pd.DataFrame({"ts_local": ["11/01/2026 01:30:00", "11/01/2026 01:30:00"], "rep": ["N", "Y"],
                        "resource": ["R", "R"], "status": ["ON", "ON"], "hsl_mw": ["50", "50"], "lsl_mw": ["-50", "-50"],
                        "hdl_mw": ["", ""], "ldl_mw": ["", ""], "base_point_mw": ["0", "0"], "net_mw": ["0", "0"],
                        "soc_mwh": ["1", "1"], "min_soc_mwh": ["0", "0"], "max_soc_mwh": ["9", "9"],
                        "aw_regup": ["", ""], "aw_regdn": ["", ""], "aw_rrspfr": ["", ""], "aw_rrsffr": ["", ""],
                        "aw_rrsufr": ["", ""], "aw_ecrs": ["", ""], "aw_nspin": ["", ""]})
    out = D._convert_sced(raw)
    assert list(out["ts_utc"]) == [pd.Timestamp("2026-11-01 06:30:00", tz="UTC"), pd.Timestamp("2026-11-01 07:30:00", tz="UTC")]


def test_aggregation_time_weights_runs_and_separates_derates_from_soc_limits(tmp_path):
    n = 9                                                                        # 45 minutes of runs = three 15-min intervals
    net = [10, 20, 30, 0, 0, 0, -10, -10, -10]
    soc = [50, 50, 50, 50, 50, 50, 50, 50, 50]
    hsl = [50, 50, 50, 30, 30, 30, 50, 50, 50]                                   # derate to 30 MW in interval 2 (SOC is high)
    rows = runs(n=n, net=net, soc=soc, **{'HSL': hsl, 'AS Awards REGUP': [4] * 3 + [0] * 6})
    df = to_rows(rows)
    idx = pd.date_range("2026-07-01 05:00", periods=4, freq="15min", tz="UTC")
    agg = D.aggregate_runs(df, idx, nameplate_mw=50.0, nameplate_charge_mw=50.0, eta_c=0.9, eta_d=1.0, aux_mw=0.5)
    assert agg["net_mw"].iloc[0] == pytest.approx(20.0 + 0.5, abs=0.3)           # mean of 10, 20, 30 plus the aux draw
    assert agg["net_telemetered_mw"].iloc[0] == pytest.approx(20.0, abs=0.3)
    assert agg["as_regup_mw"].iloc[0] == pytest.approx(4.0, abs=0.2) and agg["as_regup_mw"].iloc[1] == pytest.approx(0.0, abs=0.3)
    assert agg["available_mw"].iloc[1] == pytest.approx(30.0, abs=0.5)            # a real derate: SOC head is 50 MWh
    assert agg["available_mw"].iloc[0] == pytest.approx(50.0, abs=0.05)
    assert np.isnan(agg["net_mw"].iloc[3])                                       # no runs there: a data gap, not a zero
    # the same HSL cut with the SOC at its floor is the SOC constraint, not a derate
    rows2 = runs(n=n, net=0, soc=0.2, **{'HSL': [0] * n})
    df2 = to_rows(rows2)
    agg2 = D.aggregate_runs(df2, idx, 50.0, 50.0, 0.9, 1.0)
    assert agg2["available_mw"].iloc[0] == pytest.approx(50.0, abs=0.05)


def test_end_of_interval_soc_is_the_first_report_after_the_interval(tmp_path):
    soc = [10, 11, 12, 13, 14, 15, 16, 17, 18]                                    # one report per 5 minutes
    rows = runs(n=9, net=0, soc=soc)
    df = to_rows(rows)
    idx = pd.date_range("2026-07-01 05:00", periods=3, freq="15min", tz="UTC")
    agg = D.aggregate_runs(df, idx, 50.0, 50.0, 0.9, 1.0)
    # interval 0 ends at 05:15:00; the first run at or after it is the one at 05:15:10 -> soc 13
    assert list(agg["soc_mwh"].iloc[:2]) == [13.0, 16.0]


def test_day_ahead_position_value():
    idx = pd.date_range("2026-07-01 05:00", periods=4, freq="15min", tz="UTC")
    dam = pd.DataFrame({"hour_utc": [idx[0]], "dam_energy_mw": [10.0], "dam_spp": [40.0], "dam_regup": [0.0],
                        "dam_regdn": [0.0], "dam_rrspfr": [20.0], "dam_rrsffr": [0.0], "dam_rrsufr": [0.0], "dam_ecrs": [0.0],
                        "dam_nspin": [0.0], "mcpc_regup": [np.nan], "mcpc_regdn": [np.nan], "mcpc_rrs": [3.0],
                        "mcpc_ecrs": [np.nan], "mcpc_nspin": [np.nan]})
    prices = pd.DataFrame({"rt_usd_per_mwh": 30.0, as_price_col("RRS"): 1.0}, index=idx)
    pos = D.dam_position(dam, prices, idx)
    # per 15 minutes: 10 MW x 0.25 h x ($40 - $30) = $25 energy; 20 MW x 0.25 h x ($3 - $1) = $10 AS
    assert pos.sum() == pytest.approx(4 * (25.0 + 10.0))


def test_manifest_and_skip_logic(dispatch_home, tmp_path):
    f = D.staging_dir() / "sced" / "ext.00013052.test.zip"
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_bytes(b"PK" + b"0" * 98)
    doc = dict(kind="sced", report_type_id=13052, doc_id="1", name=f.name, size=100,
               posted_utc=pd.Timestamp("2026-10-10", tz="UTC").to_pydatetime(),
               operating_day=pd.Timestamp("2026-08-11").date())
    out = D.download([doc], log=lambda m: None)                                   # on disk, same size: no network at all
    assert out["requested"] == 0
    rows = D.read_manifest()
    assert len(rows) == 1 and rows[0]["sha256"] and int(rows[0]["bytes"]) == 100 and rows[0]["operating_day"] == "2026-08-11"
    assert D.posting_to_operating_day("2026-10-10T05:52:13-05:00") == pd.Timestamp("2026-08-11").date()
    assert set(D.MANIFEST_FIELDS) >= {"url", "file", "sha256", "bytes", "downloaded_utc"}


# ---------------------------------------------------------------------------------------------------- Gate 2: the real case study
SUMMARY = paths.REPORTS / "case_study" / "summary.json"


@pytest.mark.skipif(not SUMMARY.exists(), reason="run `python -m dispatch.attribution.report` to produce the case study first")
def test_gate2_real_case_study_closure():
    s = json.loads(SUMMARY.read_text(encoding="utf-8"))
    assert s["coverage"] >= 0.99
    assert s["identity_max_abs_error"] < 1e-6
    assert s["closure"] < ATTRIBUTION_CLOSURE_MAX
    assert 0 < s["capture_actual"] < 1 and s["pf_usd"] > s["actual_usd"]
    assert s["closure_basis"] in ("revaluation", "reported")


def test_dam_readers_convert_hours_awards_and_hub_prices(tmp_path):
    hdr = ["Delivery Date", "Hour Ending", "QSE", "DME", "Resource Name", "Resource Type", "HSL", "LSL", "Resource Status",
           "Awarded Quantity", "Settlement Point Name", "Energy Settlement Point Price", "RegUp Awarded", "RegUp MCPC",
           "RegDown Awarded", "RegDown MCPC", "RRSPFR Awarded", "RRSFFR Awarded", "RRSUFR Awarded", "RRS MCPC",
           "ECRSSD Awarded", "ECRS MCPC", "NonSpin Awarded", "NonSpin MCPC"]
    row = {h: "" for h in hdr}
    row.update({"Delivery Date": "07/01/2026", "Hour Ending": "1", "Resource Name": "TEST_ESR1", "Resource Type": "ESR",
                "HSL": "50", "LSL": "-50", "Resource Status": "ON", "Awarded Quantity": "-12.5",
                "Settlement Point Name": "TEST_RN", "Energy Settlement Point Price": "21.5", "RRSPFR Awarded": "10",
                "RRS MCPC": "2.5"})
    esr = ",".join(f'"{h}"' for h in hdr) + "\n" + ",".join(f'"{row[h]}"' for h in hdr) + "\n"
    awards = ('"Delivery Date","Hour Ending","Settlement Point","QSE Name","Energy Only Bid Award in MW",'
              '"Settlement Point Price","Bid ID"\n'
              '"07/01/2026","1","HB_SOUTH","Q1","5","24.00","a"\n"07/01/2026","1","HB_SOUTH","Q2","7","24.00","b"\n'
              '"07/01/2026","1","SOME_RN","Q1","1","99.00","c"\n"07/01/2026","2","LZ_NORTH","Q1","1","30.25","d"\n')
    z = tmp_path / "dam.zip"
    with zipfile.ZipFile(z, "w") as zf:
        zf.writestr("60d_DAM_ESR_Data-10-OCT-26.csv", esr)
        zf.writestr("60d_DAM_EnergyBidAwards-10-OCT-26.csv", awards)
    d = D.read_esr_dam(z)
    assert len(d) == 1 and d["hour_utc"].iloc[0] == pd.Timestamp("2026-07-01 05:00", tz="UTC")     # HE1 = 00:00-01:00 CDT
    assert d["dam_energy_mw"].iloc[0] == -12.5 and d["dam_rrspfr"].iloc[0] == 10 and d["dam_regup"].iloc[0] == 0
    assert d["settlement_point"].iloc[0] == "TEST_RN" and d["dam_spp"].iloc[0] == 21.5 and d["mcpc_rrs"].iloc[0] == 2.5
    h = D.read_dam_hub_spp(z)
    assert set(h["settlement_point"]) == {"HB_SOUTH", "LZ_NORTH"}                       # only hubs and zones, one price per hour
    assert len(h) == 2 and h.loc[h.settlement_point == "HB_SOUTH", "price"].iloc[0] == 24.0
    assert h.loc[h.settlement_point == "LZ_NORTH", "hour_utc"].iloc[0] == pd.Timestamp("2026-07-01 06:00", tz="UTC")


def test_a_paper_ledger_records_executed_intervals_only_so_absent_rows_are_idle(prices):
    plan = lp.solve(SPEC, prices, SOC0, mode="pf", allow_as=False)                 # energy only: idle stretches exist
    full = to_actual(plan).frame
    busy = (plan.frame.discharge_mw > 0) | (plan.frame.charge_mw > 0)
    assert 10 < (~busy).sum() < len(prices) - 10, "the test needs both idle and busy intervals"
    sparse = full[busy]                                                           # what ledger.as_actual() returns
    pf = engine.run_attribution(SPEC, ActualDispatch("syn", "paper-ledger", sparse), prices, None)
    assert pf.summary["absent_rows"] == "idle" and pf.summary["coverage"] == 1.0
    ref = engine.run_attribution(SPEC, ActualDispatch("syn", "paper-ledger", full), prices, None)
    for k in ("actual_usd", "gap_usd", "pf_usd"):                                  # same answer as the full frame
        assert pf.summary[k] == pytest.approx(ref.summary[k], rel=1e-9, abs=1e-6)
    # the same sparse frame from a telemetry-like source is a data gap instead
    gap = engine.run_attribution(SPEC, ActualDispatch("syn", "owner-export:x", sparse), prices, None)
    assert gap.summary["absent_rows"] == "gap" and gap.summary["coverage"] < 1.0 and gap.summary["closure"] > 0.0
    forced = engine.run_attribution(SPEC, ActualDispatch("syn", "owner-export:x", sparse), prices, None, absent_rows="idle")
    assert forced.summary["actual_usd"] == pytest.approx(pf.summary["actual_usd"])
    with pytest.raises(ValueError):
        engine.run_attribution(SPEC, ActualDispatch("syn", "x", sparse), prices, None, absent_rows="bogus")


def test_long_periods_are_solved_in_blocks_with_a_lookahead_and_agree_with_one_lp(monkeypatch):
    rng = np.random.default_rng(8)
    prices = make_prices(8, 3)
    prices["rt_usd_per_mwh"] = np.clip(np.cumsum(rng.normal(0, 4, len(prices))) + 40, 5, None)   # not periodic
    idle = ActualDispatch("syn", "idle", pd.DataFrame({"net_mw": 0.0, "soc_mwh": SOC0, "available_mw": 10.0},
                                                      index=prices.index))
    calls = []
    real = lp.solve
    monkeypatch.setattr(lp, "solve", lambda *a, **k: (calls.append(len(a[1])), real(*a, **k))[1])
    one = engine.run_attribution(SPEC, idle, prices, None)
    n_one = len(calls)
    monkeypatch.setattr(engine, "MAX_SINGLE_LP_INTERVALS", 96 * 3)
    monkeypatch.setattr(engine, "BLOCK_INTERVALS", 96 * 3)
    monkeypatch.setattr(engine, "LOOKAHEAD_INTERVALS", 96)
    calls.clear()
    blocked = engine.run_attribution(SPEC, idle, prices, None)
    assert len(calls) > n_one and 96 * 3 + 96 in calls                              # blocks of 3 days plus a day of lookahead
    for k in ("pf_usd", "gap_usd"):
        assert blocked.summary[k] == pytest.approx(one.summary[k], rel=5e-3)
    assert blocked.summary["identity_max_abs_error"] < 1e-9
