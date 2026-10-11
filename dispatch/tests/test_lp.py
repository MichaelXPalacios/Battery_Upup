"""FR5 tests: the LP (perfect foresight, forecast, fixed), its constraints, its accounting, the research-DP cross-check,
the simultaneous charge/discharge logic and the fail-closed behaviour."""
import time

import numpy as np
import pandas as pd
import pytest

from dispatch import paths
from dispatch.config import AS_DURATION_H
from dispatch.contracts import BatterySpec, ForecastFrame, PLAN_COLUMNS, as_award_col, as_price_col
from dispatch.optimizer import lp, rolling

TOL = 1e-6
UP = ("REGUP", "RRS", "ECRS", "NSPIN")


def make_prices(T=96, seed=0, with_as=True, start="2026-07-01 05:00", scale=1.0):
    rng = np.random.default_rng(seed)
    idx = pd.date_range(start, periods=T, freq="15min", tz="UTC")
    t = np.arange(T)
    rt = 30 + 35 * np.sin(2 * np.pi * t / 96 - 1.3) + rng.normal(0, 8, T)
    rt[rng.integers(0, T, 3)] += rng.uniform(100, 500, 3)
    df = pd.DataFrame({"rt_usd_per_mwh": rt * scale}, index=idx)
    if with_as:
        for k, m in (("regup", 3.0), ("regdn", 2.0), ("rrs", 2.5), ("ecrs", 2.0), ("nspin", 1.0)):
            df[f"as_{k}_usd_per_mw_h"] = np.abs(rng.normal(m, m / 2, T))
    return df


def spec_default(**kw):
    base = dict(asset_id="t", settlement_point="HB_NORTH", power_mw=10.0, energy_mwh=25.0, soc_min_mwh=1.0,
                soc_max_mwh=24.0, rte=0.88, wear_usd_per_mwh=8.0)
    base.update(kw)
    return BatterySpec(**base)


# ---------------------------------------------------------------------------------------------------- units
def test_one_mw_for_a_quarter_hour_at_100_dollars_is_25():
    spec = BatterySpec("one", "HB_NORTH", 1.0, 2.0, rte=1.0, wear_usd_per_mwh=0.0, as_products=())
    idx = pd.date_range("2026-07-01", periods=1, freq="15min", tz="UTC")
    prices = pd.DataFrame({"rt_usd_per_mwh": [100.0]}, index=idx)
    # fixed valuation of 1 MW discharged for one interval
    plan = lp.evaluate(spec, prices, pd.DataFrame({"charge_mw": [0.0], "discharge_mw": [1.0]}, index=idx), 1.0)
    assert plan.ok and plan.objective_usd == pytest.approx(25.0)
    # and the optimiser finds the same $25 from a full battery over one interval
    plan = lp.solve(spec, prices, 1.0, mode="pf", allow_as=False)
    assert plan.ok and plan.objective_usd == pytest.approx(25.0)
    assert plan.frame["discharge_mw"].iloc[0] == pytest.approx(1.0)


def test_as_revenue_is_price_times_mw_times_hours():
    spec = BatterySpec("one", "HB_NORTH", 10.0, 20.0, rte=1.0, wear_usd_per_mwh=0.0, as_products=("REGUP",))
    idx = pd.date_range("2026-07-01", periods=1, freq="15min", tz="UTC")
    prices = pd.DataFrame({"rt_usd_per_mwh": [50.0], as_price_col("REGUP"): [2.0]}, index=idx)
    plan = lp.evaluate(spec, prices, pd.DataFrame({"charge_mw": [0.0], "discharge_mw": [0.0]}, index=idx), 10.0,
                       fixed_as=pd.DataFrame({as_award_col("REGUP"): [10.0]}, index=idx))
    assert plan.objective_usd == pytest.approx(5.0)               # $2/MW-h x 10 MW x 0.25 h
    assert plan.frame["as_usd"].iloc[0] == pytest.approx(5.0)


def test_plan_has_the_contract_columns():
    spec = spec_default()
    plan = lp.solve(spec, make_prices(), 5.0, mode="pf")
    assert plan.ok and plan.mode == "pf" and plan.solver in ("HIGHS", "CLARABEL", "HIGHS-MILP")
    for c in PLAN_COLUMNS:
        assert c in plan.frame.columns
    for k in spec.as_products:
        assert as_award_col(k) in plan.frame.columns
    assert plan.frame.index.name == "interval_start_utc" and str(plan.frame.index.tz) == "UTC"
    assert len(plan.inputs_sha256) == 64
    assert plan.objective_usd == pytest.approx((plan.frame.energy_usd + plan.frame.as_usd - plan.frame.wear_usd).sum())


# ---------------------------------------------------------------------------------------------------- constraints
@pytest.mark.parametrize("seed", range(4))
@pytest.mark.parametrize("loss_side", ["charge", "split"])
def test_soc_and_power_bounds_hold(seed, loss_side):
    rng = np.random.default_rng(100 + seed)
    spec = spec_default(loss_side=loss_side)
    prices = make_prices(192, seed)
    avail = pd.Series(np.where(rng.random(192) < 0.2, rng.uniform(0.2, 1.0, 192), 1.0), index=prices.index)
    soc0 = float(rng.uniform(spec.soc_min_mwh, spec.soc_hi_mwh))
    plan = lp.solve(spec, prices, soc0, mode="pf", availability=avail)
    assert plan.ok
    f = plan.frame
    assert f["soc_end_mwh"].min() >= spec.soc_min_mwh - TOL and f["soc_end_mwh"].max() <= spec.soc_hi_mwh + TOL
    pmax = spec.power_mw * avail
    assert (f["charge_mw"] <= pmax + TOL).all() and (f["discharge_mw"] <= pmax + TOL).all()
    up = sum(f[as_award_col(k)] for k in UP)
    assert (f["discharge_mw"] + up <= pmax + TOL).all()
    assert (f["charge_mw"] + f[as_award_col("REGDN")] <= pmax + TOL).all()
    assert (f[[c for c in f.columns if c.endswith("_mw")]] >= -TOL).all().all()
    # SOC dynamics: recompute from the actions
    s = soc0 + np.cumsum(spec.eta_charge * 0.25 * f["charge_mw"] - 0.25 / spec.eta_discharge * f["discharge_mw"])
    assert np.abs(s.to_numpy() - f["soc_end_mwh"].to_numpy()).max() < TOL
    assert lp.check_plan(spec, f, soc0, avail)["max_violation"] <= TOL


@pytest.mark.parametrize("seed", range(4))
def test_as_energy_sufficiency_holds(seed):
    spec = spec_default(rte=0.9, loss_side="split")
    prices = make_prices(96, seed)
    for k in ("regup", "rrs", "ecrs", "nspin", "regdn"):          # make AS attractive so awards are binding
        prices[f"as_{k}_usd_per_mw_h"] = prices[f"as_{k}_usd_per_mw_h"] * 6.0
    plan = lp.solve(spec, prices, 12.0, mode="pf")
    assert plan.ok
    f = plan.frame
    need_up = sum(AS_DURATION_H[k] / spec.eta_discharge * f[as_award_col(k)] for k in UP)
    assert (need_up <= f["soc_end_mwh"] - spec.soc_min_mwh + TOL).all()
    need_dn = spec.eta_charge * AS_DURATION_H["REGDN"] * f[as_award_col("REGDN")]
    assert (need_dn <= spec.soc_hi_mwh - f["soc_end_mwh"] + TOL).all()
    assert sum(f[as_award_col(k)].sum() for k in spec.as_products) > 0, "test must exercise awards"


def test_nspin_four_hours_limits_the_award_to_stored_energy():
    # 4 h x award <= SOC - floor: with 8 MWh above the floor at most 2 MW of NSPIN can be sold
    spec = BatterySpec("n", "HB_NORTH", 10.0, 8.0, soc_min_mwh=0.0, rte=1.0, wear_usd_per_mwh=0.0, as_products=("NSPIN",))
    idx = pd.date_range("2026-07-01", periods=4, freq="15min", tz="UTC")
    prices = pd.DataFrame({"rt_usd_per_mwh": 0.0, as_price_col("NSPIN"): 5.0}, index=idx)
    plan = lp.solve(spec, prices, 8.0, mode="pf")
    assert plan.ok and plan.frame[as_award_col("NSPIN")].max() == pytest.approx(2.0, abs=1e-6)


def test_time_varying_soc_limits_and_asymmetric_charge_limit_are_respected():
    spec = spec_default(as_products=())
    prices = make_prices(96, 3, with_as=False)
    cap = pd.Series(10.0, index=prices.index)
    plan = lp.solve(spec, prices, 4.0, mode="pf", allow_as=False, soc_max_series=cap, charge_limit_mw=6.0)
    assert plan.ok and plan.frame["soc_end_mwh"].max() <= 10.0 + TOL and plan.frame["charge_mw"].max() <= 6.0 + TOL
    free = lp.solve(spec, prices, 4.0, mode="pf", allow_as=False)
    assert free.objective_usd > plan.objective_usd


def test_terminal_soc_is_a_lower_bound():
    spec = spec_default(as_products=())
    prices = make_prices(96, 5, with_as=False)
    free = lp.solve(spec, prices, 2.0, mode="pf", allow_as=False)
    held = lp.solve(spec, prices, 2.0, mode="pf", allow_as=False, terminal_soc_mwh=15.0)
    assert held.ok and held.frame["soc_end_mwh"].iloc[-1] >= 15.0 - TOL and held.objective_usd <= free.objective_usd + TOL


def test_daily_cycle_cap():
    spec = spec_default(as_products=(), max_cycles_per_day=0.5, wear_usd_per_mwh=0.0)
    prices = make_prices(96, 7, with_as=False)
    plan = lp.solve(spec, prices, spec.soc_min_mwh, mode="pf", allow_as=False)
    assert plan.ok and plan.frame["discharge_mw"].sum() * 0.25 <= 0.5 * (spec.soc_hi_mwh - spec.soc_min_mwh) + TOL


# ---------------------------------------------------------------------------------------------------- optimality
def random_feasible_plan(spec, prices, rng, soc0, avail):
    T = len(prices)
    dt, P = 0.25, spec.power_mw
    lo, hi, eta_c, eta_d = spec.soc_min_mwh, spec.soc_hi_mwh, spec.eta_charge, spec.eta_discharge
    c, d, soc = np.zeros(T), np.zeros(T), np.zeros(T)
    s = soc0
    for t in range(T):
        m = rng.integers(3)
        if m == 1:
            c[t] = rng.uniform(0, min(P * avail[t], (hi - s) / (eta_c * dt)))
        elif m == 2:
            d[t] = rng.uniform(0, min(P * avail[t], (s - lo) * eta_d / dt))
        s = s + eta_c * c[t] * dt - d[t] * dt / eta_d
        soc[t] = s
    aw = {}
    for k in ("REGUP", "RRS", "REGDN"):
        aw[as_award_col(k)] = np.zeros(T)
    for t in range(T):
        room_up = min(P * avail[t] - d[t], (soc[t] - lo) * eta_d / AS_DURATION_H["REGUP"])
        a = rng.uniform(0, max(room_up, 0.0))
        split = rng.random()
        aw["as_regup_mw"][t], aw["as_rrs_mw"][t] = a * split * 0.5, a * (1 - split) * 0.5
        aw["as_regdn_mw"][t] = rng.uniform(0, max(min(P * avail[t] - c[t],
                                                       (hi - soc[t]) / (eta_c * AS_DURATION_H["REGDN"])), 0.0))
    return pd.DataFrame({"charge_mw": c, "discharge_mw": d}, index=prices.index), pd.DataFrame(aw, index=prices.index)


@pytest.mark.parametrize("seed", range(3))
def test_perfect_foresight_beats_every_feasible_fixed_plan(seed):
    rng = np.random.default_rng(seed)
    spec = spec_default()
    prices = make_prices(96, seed + 20)
    avail = np.where(rng.random(96) < 0.15, 0.5, 1.0)
    av = pd.Series(avail, index=prices.index)
    soc0 = 10.0
    best = lp.solve(spec, prices, soc0, mode="pf", availability=av)
    assert best.ok
    n_checked = 0
    for _ in range(25):
        acts, awards = random_feasible_plan(spec, prices, rng, soc0, avail)
        fixed = lp.evaluate(spec, prices, acts, soc0, availability=av, fixed_as=awards)
        assert fixed.ok, fixed.frame.attrs["diagnostics"]["check"]       # the random plan is feasible by construction
        assert best.objective_usd >= fixed.objective_usd - TOL
        n_checked += 1
    assert n_checked == 25


def test_fixed_mode_accounting_equals_the_optimisers_own():
    spec = spec_default()
    prices = make_prices(96, 9)
    plan = lp.solve(spec, prices, 6.0, mode="pf")
    acts = plan.frame[["charge_mw", "discharge_mw"]]
    awards = plan.frame[[as_award_col(k) for k in spec.as_products]]
    again = lp.evaluate(spec, prices, acts, 6.0, fixed_as=awards)
    assert again.ok and again.objective_usd == pytest.approx(plan.objective_usd, abs=1e-9)
    for c in ("energy_usd", "as_usd", "wear_usd", "soc_end_mwh"):
        assert np.allclose(again.frame[c], plan.frame[c], atol=1e-9)
    assert again.mode == "fixed"


def test_forecast_mode_with_a_perfect_forecast_equals_perfect_foresight():
    spec = spec_default()
    prices = make_prices(96, 11)
    cols = {}
    for c in prices.columns:
        s = "rt" if c == "rt_usd_per_mwh" else c[: -len("_usd_per_mw_h")]
        for q in ("p10", "p50", "p90"):
            cols[f"{s}_{q}"] = prices[c].to_numpy()
    ff = ForecastFrame(prices.index[0], "HB_NORTH", "perfect", pd.DataFrame(cols, index=prices.index), prices.index[0])
    pf = lp.solve(spec, prices, 4.0, mode="pf")
    fc = lp.solve(spec, ff.price_view("p50"), 4.0, mode="forecast")
    assert fc.mode == "forecast" and fc.ok
    assert fc.objective_usd == pytest.approx(pf.objective_usd, abs=1e-9)
    assert np.allclose(fc.frame.to_numpy(), pf.frame.to_numpy(), atol=1e-9)


def test_rolling_with_perfect_forecast_and_a_whole_horizon_step_equals_perfect_foresight():
    spec = spec_default()
    prices = make_prices(96 * 2, 12)
    res = rolling.run_shadow(spec, prices, rolling.perfect_forecast_fn(prices, 96 * 2), soc0_mwh=3.0,
                             step_intervals=96 * 2)
    pf = lp.solve(spec, prices, 3.0, mode="pf")
    assert res.n_failed == 0 and res.revenue_usd == pytest.approx(pf.objective_usd, abs=1e-6)


# ---------------------------------------------------------------------------------------------------- cross-check with the arena DP
ARENA_CACHE = paths.RESEARCH_DATA / "arena_cache" / "prices_ERCOT.npz"


@pytest.mark.skipif(not ARENA_CACHE.exists(), reason="arena price cache not available on this machine")
def test_lp_matches_the_research_dynamic_programme_on_historical_days():
    from dispatch.optimizer import dp_check
    df = dp_check.compare(n_days=50)
    s = dp_check.summarize(df)
    assert s["n_days"] >= 50 and s["n_zones"] >= 4
    d = df["lp_arena_usd"] - df["dp_usd"]
    assert d.min() >= -1e-6, "the LP may never earn less than the DP on the same power basis"
    assert d.abs().max() <= 1e-6, "and it should match to numerical precision (same vertices as the DP grid)"
    assert abs(s["arena_total_rel_diff"]) < 1e-9
    # the guard-compatible grid-side charge limit costs a few percent, never more than the 1 - rte cut
    assert (df["lp_ac_usd"] <= df["lp_arena_usd"] + 1e-6).all()
    assert -0.06 < s["ac_total_rel_diff"] <= 0.0
    assert (df["lp_ac_usd"] / df["dp_usd"].where(df["dp_usd"] > 1)).dropna().min() >= 0.85 - 1e-6


@pytest.mark.skipif(not ARENA_CACHE.exists(), reason="arena price cache not available on this machine")
def test_the_overlap_scan_takes_any_spec_and_counts_against_that_specs_own_threshold():
    """The pilot's p* is -w rte / (1 - rte) = -$182/MWh, not the arena battery's -$113/MWh that D2 quoted."""
    from dispatch.optimizer import dp_check
    pilot = BatterySpec("pilot-like", "HB_SOUTH", 50.0, 122.0, rte=0.901, wear_usd_per_mwh=20.0, as_products=())
    sc = dp_check.overlap_scan(pilot)
    assert sc["threshold_usd_per_mwh"] == pytest.approx(-20.0 * 0.901 / 0.099) == pytest.approx(lp.overlap_price_threshold(pilot))
    _, _, P = dp_check.load_zonal_days()
    assert sc["intervals_below"] == int((P < sc["threshold_usd_per_mwh"]).sum())            # an independent recount
    assert sc["days_with_interval_below"] == int((P.min(axis=2) < sc["threshold_usd_per_mwh"]).sum()) == len(sc["solved"])
    assert sum(sc["intervals_below_by_zone"].values()) == sum(sc["intervals_below_by_year"].values()) == sc["intervals_below"]
    assert sc["intervals_below"] <= int((P < -113.0).sum()), "a deeper threshold can only count fewer intervals"
    assert sc["min_price_usd_per_mwh"] == pytest.approx(float(P.min()))
    for day in sc["solved"]:
        assert day["status"] == "optimal" and day["min_price"] < sc["threshold_usd_per_mwh"]
        assert day["overlap_intervals"] == 0 or day["milp"], "an overlap in the relaxation must have gone to the MILP"


# ---------------------------------------------------------------------------------------------------- simultaneous charge and discharge
def _overlap(plan):
    return float(np.minimum(plan.frame["charge_mw"], plan.frame["discharge_mw"]).max())


@pytest.mark.parametrize("seed", range(6))
def test_no_simultaneous_charge_and_discharge_at_ordinary_prices(seed):
    spec = spec_default(rte=0.85, wear_usd_per_mwh=20.0)
    prices = make_prices(96, 40 + seed, scale=1.0)
    plan = lp.solve(spec, prices, 8.0, mode="pf")
    assert plan.ok and _overlap(plan) <= 1e-6
    assert plan.frame.attrs["diagnostics"]["milp"] is False
    assert lp.overlap_price_threshold(spec) == pytest.approx(-20 * 0.85 / 0.15)


def test_degenerate_lossless_battery_is_netted_not_left_overlapping():
    spec = BatterySpec("d", "HB_NORTH", 5.0, 10.0, rte=1.0, wear_usd_per_mwh=0.0, as_products=())
    prices = pd.DataFrame({"rt_usd_per_mwh": np.zeros(24)}, index=pd.date_range("2026-07-01", periods=24, freq="15min", tz="UTC"))
    plan = lp.solve(spec, prices, 5.0, mode="pf", allow_as=False)
    assert plan.ok and _overlap(plan) <= 1e-6


def _toy_milp_reference(spec, rt, soc0):
    """Independent scipy MILP (and relaxed LP) of the energy-only problem with a charge-mode binary on every interval."""
    from scipy.optimize import linprog, milp, LinearConstraint, Bounds
    T, dt = len(rt), 0.25
    ec, ed, lo, hi, P, w = spec.eta_charge, spec.eta_discharge, spec.soc_min_mwh, spec.soc_hi_mwh, spec.power_mw, \
        spec.wear_usd_per_mwh
    n = 4 * T                                               # c, d, s, u
    cost = np.zeros(n)
    cost[:T] = rt * dt
    cost[T:2 * T] = -(rt * dt) + w * dt
    A = np.zeros((T, n))
    b = np.zeros(T)
    for t in range(T):
        A[t, 2 * T + t] = 1
        if t:
            A[t, 2 * T + t - 1] = -1
        A[t, t] = -ec * dt
        A[t, T + t] = dt / ed
        b[t] = soc0 if t == 0 else 0
    lb = np.r_[np.zeros(2 * T), np.full(T, lo), np.zeros(T)]
    ub = np.r_[np.full(2 * T, P), np.full(T, hi), np.ones(T)]
    # relaxed (u continuous, no linking)
    relaxed = linprog(cost[: 3 * T], A_eq=A[:, : 3 * T], b_eq=b, bounds=list(zip(lb[: 3 * T], ub[: 3 * T])), method="highs")
    link = np.zeros((2 * T, n))
    for t in range(T):
        link[t, t] = 1
        link[t, 3 * T + t] = -P                              # c <= P u
        link[T + t, T + t] = 1
        link[T + t, 3 * T + t] = P                           # d <= P (1 - u)  ->  d + P u <= P
    cons = [LinearConstraint(A, b, b), LinearConstraint(link[:T], -np.inf, 0), LinearConstraint(link[T:], -np.inf, P)]
    integrality = np.r_[np.zeros(3 * T), np.ones(T)]
    m = milp(cost, constraints=cons, integrality=integrality, bounds=Bounds(lb, ub))
    c = relaxed.x[:T]
    d = relaxed.x[T:2 * T]
    return -relaxed.fun, float(np.minimum(c, d).max()), -m.fun


def test_deeply_negative_prices_trigger_the_milp_and_remove_the_overlap():
    spec = BatterySpec("m", "HB_NORTH", 10.0, 20.0, soc_min_mwh=0.0, rte=0.85, wear_usd_per_mwh=20.0, as_products=())
    T = 12
    rt = np.array([30, 40, 35, -250, -250, -250, -250, 40, 60, 45, 30, 20], float)
    idx = pd.date_range("2026-07-01", periods=T, freq="15min", tz="UTC")
    prices = pd.DataFrame({"rt_usd_per_mwh": rt}, index=idx)
    relaxed_obj, relaxed_overlap, milp_obj = _toy_milp_reference(spec, rt, soc0=20.0)
    assert relaxed_overlap > 1e-3, "the toy must make the relaxation overlap (battery full at -250)"
    assert relaxed_obj > milp_obj + 1e-3
    plan = lp.solve(spec, prices, 20.0, mode="pf", allow_as=False)
    dg = plan.frame.attrs["diagnostics"]
    assert plan.ok and dg["milp"] is True and plan.solver == "HIGHS-MILP"
    assert _overlap(plan) <= 1e-6
    assert plan.objective_usd == pytest.approx(milp_obj, abs=1e-6)
    assert plan.objective_usd < relaxed_obj - 1e-3


def test_netting_an_overlap_never_lowers_revenue_above_the_threshold():
    # the algebra behind the proof: removing delta of charge and rte*delta of discharge changes revenue by
    # delta*dt*(p(1-rte) + w*rte), which is >= 0 exactly when p >= p* = -w*rte/(1-rte)
    spec = spec_default(rte=0.85, wear_usd_per_mwh=20.0)
    pstar = lp.overlap_price_threshold(spec)
    for p, sign in ((pstar - 10, -1), (pstar + 10, 1), (0.0, 1), (500.0, 1)):
        delta = 1.0
        d_rev = delta * 0.25 * (p * (1 - spec.rte) + spec.wear_usd_per_mwh * spec.rte)
        assert np.sign(d_rev) == sign


# ---------------------------------------------------------------------------------------------------- fail closed
def test_non_optimal_solves_are_not_ok_and_yield_no_setpoint():
    spec = spec_default(as_products=())
    prices = make_prices(8, 1, with_as=False)
    plan = lp.solve(spec, prices, 500.0, mode="pf", allow_as=False)        # SOC far above the ceiling: infeasible
    assert not plan.ok and plan.status != "optimal"
    with pytest.raises(ValueError):
        lp.first_setpoint(plan, model_version="x", issued_utc=prices.index[0])
    ok = lp.solve(spec, prices, 5.0, mode="pf", allow_as=False)
    sp = lp.first_setpoint(ok, model_version="v1", issued_utc=prices.index[0])
    assert sp.net_mw == pytest.approx(ok.frame["discharge_mw"].iloc[0] - ok.frame["charge_mw"].iloc[0])
    assert sp.plan_id == ok.inputs_sha256[:16] and sp.interval_start_utc == prices.index[0]


def test_bad_inputs_raise():
    spec = spec_default()
    prices = make_prices(24, 1)
    bad = prices.copy()
    bad.iloc[3, 0] = np.nan
    with pytest.raises(ValueError):
        lp.solve(spec, bad, 5.0, mode="pf")
    with pytest.raises(ValueError):
        lp.solve(spec, prices.iloc[[0, 1, 3, 4]], 5.0, mode="pf")                  # gap in the grid
    with pytest.raises(ValueError):
        lp.solve(spec, prices.tz_localize(None), 5.0, mode="pf")                  # naive index
    with pytest.raises(ValueError):
        lp.solve(spec, prices, 5.0, mode="pf", availability=pd.Series(10.0, index=prices.index))   # MW, not a fraction
    with pytest.raises(ValueError):
        lp.solve(spec, prices, 5.0, mode="bogus")
    with pytest.raises(ValueError):
        lp.solve(spec, prices, 5.0, mode="fixed")                                  # needs fixed_energy


def test_inputs_hash_is_stable_and_sensitive():
    spec = spec_default()
    prices = make_prices(48, 2)
    a = lp.solve(spec, prices, 5.0, mode="pf")
    b = lp.solve(spec, prices, 5.0, mode="pf")
    assert a.inputs_sha256 == b.inputs_sha256
    p2 = prices.copy()
    p2.iloc[10, 0] += 1.0
    assert lp.solve(spec, p2, 5.0, mode="pf").inputs_sha256 != a.inputs_sha256
    assert lp.solve(spec, prices, 5.5, mode="pf").inputs_sha256 != a.inputs_sha256
    assert lp.solve(spec, prices, 5.0, mode="forecast").inputs_sha256 != a.inputs_sha256
    assert lp.solve(spec, prices, 5.0, mode="pf", allow_as=False).inputs_sha256 != a.inputs_sha256


# ---------------------------------------------------------------------------------------------------- speed
def test_one_day_solves_well_under_a_second():
    spec = spec_default()
    lp.warmup(spec)
    times = []
    for i in range(7):
        prices = make_prices(96, 60 + i)
        t0 = time.perf_counter()
        plan = lp.solve(spec, prices, 6.0, mode="forecast")
        times.append(time.perf_counter() - t0)
        assert plan.ok
    assert float(np.median(times)) < 0.5 and max(times) < 1.0, times


# ---------------------------------------------------------------------------------------------------- seasonal-naive forecaster
def test_seasonal_naive_forecaster_uses_only_published_prices():
    prices = make_prices(96 * 6, 3)
    t_issue = prices.index[96 * 3 + 10]
    fc = rolling.SeasonalNaiveForecaster(prices, horizon_intervals=96)
    a = fc(t_issue)
    future = prices.copy()
    future.iloc[96 * 3 + 10:, :] *= 5.0                                # everything from the decision time on changes
    b = rolling.SeasonalNaiveForecaster(future, horizon_intervals=96)(t_issue)
    assert np.allclose(a.frame.to_numpy(), b.frame.to_numpy())
    assert a.max_feature_published_utc <= t_issue and a.issued_utc == t_issue
    assert a.frame.index[0] == t_issue and len(a.frame) == 96
    assert (a.frame["rt_p10"] <= a.frame["rt_p50"] + 1e-9).all() and (a.frame["rt_p50"] <= a.frame["rt_p90"] + 1e-9).all()
    first = a.frame.index[0]
    assert a.frame["rt_p50"].iloc[0] == pytest.approx(prices.loc[first - pd.Timedelta(days=1), "rt_usd_per_mwh"])
    view = a.price_view("p50")
    assert "rt_usd_per_mwh" in view.columns and as_price_col("REGUP") in view.columns


def test_shadow_never_beats_perfect_foresight_and_fails_closed_on_a_bad_forecast():
    spec = spec_default()
    prices = make_prices(96 * 5, 4)
    fc = rolling.SeasonalNaiveForecaster(prices, horizon_intervals=192)
    res = rolling.run_shadow(spec, prices.iloc[96 * 2:], fc, soc0_mwh=5.0, step_intervals=4)
    pf = lp.solve(spec, prices.iloc[96 * 2:], 5.0, mode="pf")
    assert res.n_failed == 0 and res.revenue_usd <= pf.objective_usd + 1e-6
    assert res.frame["soc_end_mwh"].between(spec.soc_min_mwh - TOL, spec.soc_hi_mwh + TOL).all()
    assert list(res.frame.columns)[:2] == ["charge_mw", "discharge_mw"]

    def broken(issued):
        raise RuntimeError("model unavailable")
    idle = rolling.run_shadow(spec, prices.iloc[:96], broken, soc0_mwh=5.0, step_intervals=24)
    assert idle.n_failed == idle.n_solves > 0 and idle.revenue_usd == 0.0
    assert (idle.frame[["charge_mw", "discharge_mw"]] == 0).all().all()


# ---------------------------------------------------------------------------------------------------- protocol and hygiene
def test_solve_conforms_to_the_optimizer_protocol():
    import inspect
    from dispatch.contracts import Optimizer
    proto = inspect.signature(Optimizer.__call__).parameters
    impl = inspect.signature(lp.solve).parameters
    for name, p in proto.items():
        if name == "self":
            continue
        assert name in impl, name
        assert impl[name].kind == p.kind and impl[name].default == p.default, name


@pytest.mark.parametrize("seed", range(3))
def test_plans_never_exceed_power_limits_even_by_numerical_noise(seed):
    spec = spec_default()
    prices = make_prices(192, 300 + seed)
    for k in ("regup", "regdn", "rrs", "ecrs", "nspin"):
        prices[f"as_{k}_usd_per_mw_h"] = prices[f"as_{k}_usd_per_mw_h"] * 5.0
    plan = lp.solve(spec, prices, 12.0, mode="pf")
    f = plan.frame
    assert plan.ok
    assert (f["charge_mw"] <= spec.power_mw).all() and (f["discharge_mw"] <= spec.power_mw).all()
    assert (f["discharge_mw"] + sum(f[as_award_col(k)] for k in UP) <= spec.power_mw + 1e-12).all()
    assert (f["charge_mw"] + f[as_award_col("REGDN")] <= spec.power_mw + 1e-12).all()
