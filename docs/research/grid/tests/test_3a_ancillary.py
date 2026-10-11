"""The AS layer: eligibility by duration, the upper bound, the ledger-based co-optimisation estimate and the DP ceiling.

The DP is checked against its own limits: with no AS prices it equals the arena's oracle_days exactly, and with simple
constant prices it matches closed forms worked out by hand.

Run:  python tests/test_3a_ancillary.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _harness_3a as H  # noqa: E402

import numpy as np  # noqa: E402

from gridsite import ancillary as A  # noqa: E402
from gridsite import paths  # noqa: E402

paths.use_arena()
from arena import config as C  # noqa: E402
from arena.battery import Spec, oracle_days  # noqa: E402

NAN = np.nan


def _grid(dates, **const):
    """AsGrid with constant prices per product (NaN where not given), for hand calculations."""
    n = len(dates)
    price = {p: np.full((n, 96), float(const[p])) if p in const else np.full((n, 96), NAN) for p in A.PRODUCTS}
    return A.AsGrid("DAM", np.array(dates), price)


def test_eligibility_by_duration():
    for key, nspin in (("grid", 0.5), ("grid4h", 1.0)):
        f = A._fractions(key, ["2026-03-01"])
        assert f["REGUP"][0] == f["REGDN"][0] == f["RRS"][0] == f["ECRS"][0] == 1.0, key
        assert f["NSPIN"][0] == nspin, key
    # before RTC+B the sustain times were Reg/RRS 15 min and ECRS 2 h; a 2 h battery is still fully eligible
    f = A._fractions("grid", ["2024-06-01"])
    assert f["ECRS"][0] == 1.0 and f["REGUP"][0] == 1.0 and f["NSPIN"][0] == 0.5
    # a 1 MW / 1 MWh battery could take only half of ECRS (1 h = ok) and a quarter of Non-Spin
    assert A.eligible_fraction(1.0, 4.0) == 0.25 and A.eligible_fraction(8.0, 4.0) == 1.0
    assert A.durations_on(["2025-12-04", "2025-12-05"])["ECRS"].tolist() == [2.0, 1.0]


def test_upper_bound_hand_calculation():
    dates = [f"2026-03-{d:02d}" for d in range(1, 31)]
    g = _grid(dates, REGUP=2.0, REGDN=1.0, RRS=3.0, ECRS=2.5, NSPIN=5.0)
    hy = 8.76                                                    # 8,760 h / 1000
    u2 = A.as_upper_bound(2026, "grid", grid=g)
    u4 = A.as_upper_bound(2026, "grid4h", grid=g)
    assert abs(u2["usd_kw_yr"] - 3.0 * hy) < 1e-9, u2["usd_kw_yr"]        # 2 h: Non-Spin only half eligible -> RRS $3 wins
    assert abs(u4["usd_kw_yr"] - 5.0 * hy) < 1e-9, u4["usd_kw_yr"]        # 4 h: Non-Spin fully eligible -> $5 wins
    assert abs(A.as_upper_bound(2026, "grid", grid=g, stack_regdn=True)["usd_kw_yr"] - (3.0 + 1.0) * hy) < 1e-9
    assert abs(A.as_upper_bound(2026, "grid4h", grid=g, stack_regdn=True)["usd_kw_yr"] - (5.0 + 1.0) * hy) < 1e-9
    assert u2["partial"] and abs(u2["coverage"] - 30 / 365) < 1e-9 and u2["n_hours"] == 30 * 24
    assert abs(u2["usd_kw_covered"] - 3.0 * 30 * 24 / 1000) < 1e-9
    assert abs(u2["by_product"]["NSPIN"] - 5.0 * 0.5 * hy) < 1e-9 and abs(u4["by_product"]["NSPIN"] - 5.0 * hy) < 1e-9
    assert u2["best_share"]["RRS"] == 1.0 and u4["best_share"]["NSPIN"] == 1.0


def test_upper_bound_best_product_changes_by_interval():
    dates = ["2026-03-01"]
    price = {p: np.full((1, 96), NAN) for p in A.PRODUCTS}
    price["REGUP"] = np.where(np.arange(96) < 48, 10.0, 1.0)[None, :]
    price["RRS"] = np.where(np.arange(96) < 48, 1.0, 7.0)[None, :]
    g = A.AsGrid("DAM", np.array(dates), price)
    u = A.as_upper_bound(2026, "grid", grid=g)
    assert abs(u["mean_best_usd_mw_h"] - (10.0 * 48 + 7.0 * 48) / 96) < 1e-9
    assert u["best_share"]["REGUP"] == 0.5 and u["best_share"]["RRS"] == 0.5


def test_real_prices_reproduce_the_documented_collapse():
    """notes/phase1-market.md: DAM REGUP averaged $26.80/MW-h in 2023 and $2.11 in 2026 to date."""
    rows = {(r["year"], r["market"], r["product"]): r for r in A.as_revenue_rows()}
    assert abs(rows[(2023, "DAM", "REGUP")]["mean_mcpc_usd_mw_h"] - 26.80) < 0.05
    assert abs(rows[(2026, "DAM", "REGUP")]["mean_mcpc_usd_mw_h"] - 2.11) < 0.05
    r23, r26 = rows[(2023, "DAM", "REGUP")], rows[(2026, "DAM", "REGUP")]
    assert abs(r23["ub_2h_usd_kw_yr"] - 26.79 * 8.76) < 0.5 and r23["ub_2h_usd_kw_yr"] / r26["ub_2h_usd_kw_yr"] > 10
    # best-product bounds dominate every single product; stacking Reg Down only adds
    for (y, mk, p), r in rows.items():
        if p in A.PRODUCTS:
            best = rows[(y, mk, A.BEST)]
            if r["coverage"] < best["coverage"] - 1e-9:        # ECRS 2023 exists only from 10 Jun: annualised over other hours
                assert r["partial_year"], (y, mk, p)
                continue
            assert best["ub_4h_usd_kw_yr"] >= r["ub_4h_usd_kw_yr"] - 1e-6, (y, mk, p)
            assert best["ub_2h_usd_kw_yr"] >= r["ub_2h_usd_kw_yr"] - 1e-6, (y, mk, p)
        if p == A.BEST:
            st = rows[(y, mk, A.BEST_STACKED)]
            assert st["ub_2h_usd_kw_yr"] >= r["ub_2h_usd_kw_yr"] - 1e-9 and st["ub_4h_usd_kw_yr"] >= r["ub_4h_usd_kw_yr"] - 1e-9
            assert r["ub_4h_usd_kw_yr"] >= r["ub_2h_usd_kw_yr"] - 1e-9            # more energy never hurts eligibility
    assert (2026, "RT", "REGUP") in rows and (2025, "RT", "RRS") in rows and (2022, "RT", "RRS") not in rows
    assert rows[(2025, "RT", "RRS")]["partial_year"] and rows[(2026, "DAM", "RRS")]["partial_year"]
    assert not rows[(2024, "DAM", "RRS")]["partial_year"]


def test_grid_dst_handling():
    g = A.AsGrid.load("DAM")
    i = g.index
    for day in ("2024-03-10", "2024-11-03", "2025-03-09", "2025-11-02"):       # spring-forward and fall-back days
        a = g.price["REGUP"][i[day]]
        assert a.shape == (96,) and np.isfinite(a).all(), day
    # a skipped spring-forward hour repeats the hour before: 02:00 CPT does not exist on 2024-03-10 (23 rows in the file)
    a = g.price["REGUP"][i["2024-03-10"]]
    assert a[4] == 2.33 and np.array_equal(a[8:12], a[4:8]) and a[12] == 2.45
    # the repeated fall-back hour is dropped (the file flags it dst_rep; the first 01:00 hour, 0.55, stays)
    b = g.price["REGUP"][i["2024-11-03"]]
    assert b[4] == 0.55 and b[8] == 0.85
    assert not np.isfinite(g.price["ECRS"][i["2023-06-09"]]).any() and np.isfinite(g.price["ECRS"][i["2023-06-11"]]).all()


def test_oracle_with_as_reduces_to_oracle_days_and_closed_forms():
    dates = ["2026-03-01", "2026-03-02"]
    rng = np.random.default_rng(1)
    P = np.stack([35 + 20 * np.sin(np.arange(96) / 96 * 4 * np.pi) + rng.normal(0, 5, 96) for _ in range(2)])
    zero = {p: 0.0 for p in A.PRODUCTS}
    for key in ("grid", "grid4h"):
        g0 = _grid(dates, **zero)
        assert np.array_equal(A.oracle_with_as(P, dates, key, grid=g0), oracle_days(P, Spec(key))), key
        g_nan = _grid(dates)                                    # no prices at all behaves like zero prices
        assert np.array_equal(A.oracle_with_as(P, dates, key, grid=g_nan), oracle_days(P, Spec(key)))
    Pz = np.zeros((2, 96))
    # Reg Up (RRS) only at $10: charge for free from empty at full power, then hold full. The first interval holds only
    # 0.25 MWh (0.5 MW deliverable for 30 min), every later one a full 1 MW: 2.5 x 96 - 1.25
    g = _grid(dates, RRS=10.0)
    assert np.allclose(A.oracle_with_as(Pz, dates, "grid", grid=g, stack_regdn=False), 2.5 * 96 - 1.25)
    # Reg Down only: stay empty and sell 1 MW of room all day
    g = _grid(dates, REGDN=10.0)
    assert np.allclose(A.oracle_with_as(Pz, dates, "grid", grid=g, stack_regdn=True), 240.0)
    assert np.allclose(A.oracle_with_as(Pz, dates, "grid", grid=g, stack_regdn=False), 0.0)       # not allowed -> nothing
    # Both at once. Durations are 30 minutes, so at 4+ units (0.5 MWh) of a 2 MWh battery both fit: 1 MW of Reg Up needs
    # 0.5 MWh stored, 1 MW of Reg Down needs 0.5 MWh of room. While charging at full power there is no charge-side
    # headroom, so: t0 (SOC 2 units) 0.5 MW up = 1.25; t1 (SOC 4 units, still charging) 1 MW up = 2.5; then 5.0 an
    # interval for the remaining 94: 1.25 + 2.5 + 94 x 5.0 = 473.75
    g = _grid(dates, REGDN=10.0, RRS=10.0)
    v = A.oracle_with_as(Pz, dates, "grid", grid=g, stack_regdn=True)
    assert np.allclose(v, 473.75), v


def test_oracle_with_as_bounds_on_real_days():
    from arena.data import Market
    m = Market("ERCOT")
    sel = np.where((m.dates >= "2024-07-01") & (m.dates <= "2024-07-21"))[0]
    P, dates = m.P[sel, 0, :], m.dates[sel]
    g = A.AsGrid.load("DAM")
    e0 = oracle_days(P, Spec("grid"))
    on = A.oracle_with_as(P, dates, "grid", grid=g, stack_regdn=False)
    stk = A.oracle_with_as(P, dates, "grid", grid=g, stack_regdn=True)
    on4 = A.oracle_with_as(P, dates, "grid4h", grid=g, stack_regdn=True)
    assert (on >= e0 - 1e-9).all() and (stk >= on - 1e-9).all() and (on4 >= stk - 1e-9).all()
    ub = A.as_upper_bound(2024, "grid", grid=g, stack_regdn=True)
    # the AS part of the co-optimised result cannot beat selling the best products in every interval of those days
    prices, _ = g.for_dates(dates)
    val, has = A._best_value(prices, A._fractions("grid", dates), stack_regdn=True)
    cap = (val * C.DT).sum(1)
    assert (stk - e0 <= cap + 1e-6).all()
    assert ub["usd_kw_yr"] > 0


def test_busy_footprint_and_soc_state():
    P = np.full((1, 96), 40.0)
    P[0, 8:16] = 10.0                                   # 02:00-04:00 cheapest: charge
    P[0, 72:80] = 120.0                                 # 18:00-20:00 dearest: discharge
    dis, chg = A.busy_footprint(P, np.array([2.0]))     # 2 MWh = 8 full-power intervals each way
    assert abs(dis.sum() - 8) < 1e-9 and abs(chg.sum() - 8) < 1e-9
    assert dis[0, 72:80].sum() == 8 and chg[0, 8:16].sum() == 8
    st = A._soc_state(dis, chg)[0]                      # the state going INTO each interval; only idle intervals use it
    assert (st[:9] == -1).all()                         # empty until the first charge interval has happened
    assert (st[9:73] == 1).all()                        # holding energy from the first charge to the last interval before discharge
    assert (st[73:] == -1).all()                        # empty again once discharging has started
    idle = (dis[0] + chg[0]) == 0
    assert idle.sum() == 80 and (st[idle][:8] == -1).all() and (st[16:72] == 1).all() and (st[80:] == -1).all()
    # fractional boundary
    dis, chg = A.busy_footprint(P, np.array([2.125]))   # 8.5 intervals
    assert abs(dis.sum() - 8.5) < 1e-9 and abs(chg.sum() - 8.5) < 1e-9
    # a day with no busy block inherits the state the day before ended in (charge-only day leaves the battery full)
    P2 = np.vstack([P, np.full((1, 96), 40.0)])
    dis2 = np.vstack([np.zeros((1, 96)), np.zeros((1, 96))])
    chg2 = np.vstack([np.zeros((1, 96)), np.zeros((1, 96))])
    chg2[0, 10:14] = 1.0
    st2 = A._soc_state(dis2, chg2)
    assert (st2[0, :11] == -1).all() and (st2[0, 11:] == 1).all() and (st2[1] == 1).all()
    # zero energy traded: no busy intervals at all
    dz, cz = A.busy_footprint(P, np.array([0.0]))
    assert dz.sum() == 0 and cz.sum() == 0


def test_as_vs_energy_accounting():
    dates = ["2026-03-01"]
    P = np.full((1, 96), 40.0)
    P[0, 8:16] = 10.0
    P[0, 72:80] = 120.0
    mwh = np.array([2.0])
    # profit 60 over 16 busy intervals -> 3.75 per busy interval; RRS at $10 pays 2.5 per interval: no swap
    g = _grid(dates, RRS=10.0, REGDN=4.0)
    r = A.as_vs_energy(P, np.array([100.0]), np.array([40.0]), mwh, dates, "grid", grid=g)
    y = r["by_year"][2026]
    n_full, n_empty = 56, 24                                  # idle intervals 16..71 hold energy; 0..7 and 80..95 are empty
    exp_idle = (n_full * 10.0 + n_empty * 4.0) * 0.25         # full: best up-product RRS; empty: Reg Down only
    scale = 365.0 / 1 / 1000.0                                # one-day "year" is annualised x 365
    assert abs(y["as_idle"] - exp_idle * scale) < 1e-6, (y["as_idle"], exp_idle * scale)
    assert abs(y["as_swap"]) < 1e-12 and abs(y["energy"] - 60.0 * scale) < 1e-9
    assert abs(y["total"] - (60.0 + exp_idle) * scale) < 1e-6 and y["partial"]
    # same day, tiny profit: AS now beats the energy slot, so busy intervals swap to AS (gain = AS - e per interval)
    r2 = A.as_vs_energy(P, np.array([1.6]), np.array([0.0]), mwh, dates, "grid", grid=g)
    e_slot = 1.6 / 16
    exp_swap = 16 * (10.0 * 0.25 - e_slot)
    assert abs(r2["by_year"][2026]["as_swap"] - exp_swap * scale) < 1e-6
    # not state-of-charge aware: every idle interval sells the best product -> never less
    r3 = A.as_vs_energy(P, np.array([100.0]), np.array([40.0]), mwh, dates, "grid", grid=g, soc_aware=False)
    assert abs(r3["by_year"][2026]["as_idle"] - 80 * 10.0 * 0.25 * scale) < 1e-6
    assert r3["by_year"][2026]["as_idle"] >= y["as_idle"]
    # a loss-making ledger does not make AS look better: swaps compare with max(0, profit per slot)
    r4 = A.as_vs_energy(P, np.array([-50.0]), np.array([0.0]), mwh, dates, "grid", grid=g)
    assert abs(r4["by_year"][2026]["as_swap"] - 16 * 2.5 * scale) < 1e-6
    assert abs(r4["by_year"][2026]["energy"] + 50.0 * scale) < 1e-9
    # days without AS prices contribute nothing and lower the coverage
    g2 = _grid(["2026-03-02"], RRS=10.0)
    r5 = A.as_vs_energy(P, np.array([100.0]), np.array([40.0]), mwh, dates, "grid", grid=g2)
    assert r5["by_year"][2026]["as_total"] == 0.0 and r5["by_year"][2026]["coverage"] == 0.0


def test_ledger_estimate_sits_below_the_dp_ceiling_on_real_days():
    from arena.data import Market
    m = Market("ERCOT")
    bt = np.load(C.CACHE / "backtest_ERCOT_grid.npz", allow_pickle=True)
    bdates = bt["dates"].astype(str)
    sel = np.where((bdates >= "2024-06-01") & (bdates <= "2024-06-30"))[0]
    mi = np.searchsorted(m.dates, bdates[sel])
    P = m.P[mi, 0, :]
    g = A.AsGrid.load("DAM")
    for algo in ("A1", "A5"):
        r = A.as_vs_energy(P, bt[f"{algo}_energy"][sel, 0], bt[f"{algo}_wear"][sel, 0], bt[f"{algo}_mwh"][sel, 0],
                           bdates[sel], "grid", grid=g)["by_year"][2024]
        up = A.as_oracle_uplift(P, bdates[sel], "grid", grid=g, stack_regdn=True)[2024]
        assert r["energy"] <= up["energy_oracle"] + 1e-6
        assert r["total"] <= up["energy_plus_as_oracle"] + 1e-6, (algo, r, up)
        assert r["as_total"] >= 0 and r["as_idle"] >= 0 and r["as_swap"] >= 0


def test_csv_written_with_labels():
    import pandas as pd
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        out = Path(td) / "as_revenue.csv"
        df = A.write_as_revenue_csv(out)
        back = pd.read_csv(out)
        lab = pd.read_csv(Path(td) / "as_revenue.labels.csv")
    assert list(back.columns) == A.CSV_COLUMNS and len(back) == len(df) > 100
    assert set(lab["column"]) == set(A.CSV_COLUMNS) and set(lab["label"]) <= {"REAL", "DERIVED", "SIM", "ASSUMPTION"}
    assert set(back["market"]) == {"DAM", "RT"} and A.BEST in set(back["product"])
    assert back["year"].min() == 2010 and back["year"].max() == 2026


if __name__ == "__main__":
    H.run_module(globals())
