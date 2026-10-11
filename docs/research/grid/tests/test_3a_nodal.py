"""NodalMarket is a drop-in for the arena's Market: built from the 8 zone lanes it reproduces the arena exactly.

What is proved here (all exact, array_equal, no tolerance):
  * every attribute RegionModels / find_analogs / BacktestContext / run_combined read is identical to Market's;
  * RegionModels(nodal) fits the same coefficients as RegionModels(market);
  * the arena's own find_analogs(nodal) returns the arena's stored analog cache bit for bit;
  * the lane-safe analog helpers equal the arena's when the lanes are the 8 zones;
  * run_combined through the NodalMarket gives the same ledgers as the arena's simulate_market steps on the same
    slice, and the same as the arena's stored backtest (arena_cache/backtest_ERCOT_grid.npz) for those days.
The full-year check (2024) is `run_grid.py compat-test year=2024` (about 10 minutes); the slice here is 30 days.

Run:  python tests/test_3a_nodal.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _harness_3a as H  # noqa: E402

import numpy as np  # noqa: E402

from gridsite import nodal_market as N  # noqa: E402
from gridsite import paths  # noqa: E402

paths.use_arena()
from arena import config as C  # noqa: E402
from arena.backtest import BacktestContext  # noqa: E402
from arena.data import Market  # noqa: E402
from arena.models import RegionModels, analog_matrix, find_analogs  # noqa: E402

SLICE = ("2015-01-01", "2015-01-30")        # 30 simulated days; the first days of the arena's backtest

_CACHE = {}


def _market():
    if "m" not in _CACHE:
        _CACHE["m"] = Market("ERCOT")
        _CACHE["nm"] = N.NodalMarket.from_market(_CACHE["m"])
        z = np.load(C.CACHE / "analogs_past_ERCOT.npz")
        _CACHE["analogs"] = (z["idx"], z["dist"])
    return _CACHE["m"], _CACHE["nm"], _CACHE["analogs"]


def test_every_attribute_the_arena_reads_is_identical():
    m, nm, _ = _market()
    for k in ("P", "ref", "gas", "years", "month", "doy", "weekend", "dates"):
        assert np.array_equal(getattr(nm, k), getattr(m, k)), k
    for k in ("tmax", "tmin", "wsf2", "prcp", "sys_tmax", "sys_tmin"):
        assert np.array_equal(nm.W[k], m.W[k]), k
    assert nm.W["stations_found"] == m.W["stations_found"]
    assert nm.zones == m.zones and nm.labels == m.labels and nm.ref_name == m.ref_name and nm.name == m.name
    assert np.array_equal(nm.year_slice(2024), m.year_slice(2024))
    assert nm.P.dtype == m.P.dtype and nm.P.shape == m.P.shape


def test_region_models_identical():
    m, nm, _ = _market()
    a, b = RegionModels(m), RegionModels(nm)
    assert np.array_equal(a.X, b.X)
    for arr in ("y1", "y2", "y3"):
        assert np.array_equal(getattr(a, arr), getattr(b, arr))
    assert sorted(a.models) == sorted(b.models)
    for year in a.models:
        for r in range(len(m.zones)):
            for ma, mb in zip(a.models[year][r], b.models[year][r]):
                if ma is None or mb is None:
                    assert ma is None and mb is None
                    continue
                for xa, xb in zip(ma, mb):
                    assert np.array_equal(xa, xb), (year, r)
    for d in (400, 1500, 3000, 5000):
        for r in range(8):
            assert a.spike_prob(d, r) == b.spike_prob(d, r)


def test_arena_find_analogs_on_nodal_equals_arena_cache():
    _, nm, (idx, dist) = _market()
    i2, d2 = find_analogs(nm, K=24, mode="past")                  # the arena's own function, unmodified
    assert np.array_equal(i2, idx) and np.array_equal(d2, dist)


def test_reference_stats_and_lane_safe_helpers_equal_arena_on_zone_lanes():
    m, nm, (idx, dist) = _market()
    A_arena = analog_matrix(m)
    assert np.array_equal(N.analog_matrix_stats(nm, None), A_arena)                     # pooled == arena
    assert np.array_equal(N.analog_matrix_stats(nm, N.reference_stats()), A_arena)      # reference == pooled here
    days = nm.day_index(*SLICE)
    i2, d2 = N.analog_search(nm, A_arena, day_idx=days)
    assert np.array_equal(i2[days], idx[days]) and np.array_equal(d2[days], dist[days])
    # a different lane set gets the SAME features for a shared lane, which pooled standardisation would not give
    sub = nm.subset(["LZ_WEST", "LZ_HOUSTON"])
    ref = N.reference_stats()
    f_sub = N.analog_matrix_stats(sub, ref)
    assert np.array_equal(f_sub[:, 0, :], A_arena[:, nm.lane_ids.index("LZ_WEST"), :])
    assert not np.array_equal(N.analog_matrix_stats(sub, None)[:, 0, :], A_arena[:, nm.lane_ids.index("LZ_WEST"), :])


def test_context_pooled_equals_arena_context_and_lane_weights_are_valid():
    _, nm, analogs = _market()
    rm = RegionModels(nm)
    days = nm.day_index("2015-01-01", "2016-12-31")
    c_arena = BacktestContext(nm, days, rm, analogs)
    c_pool = N.NodalBacktestContext(nm, days, rm, analogs, "pooled")
    c_lane = N.NodalBacktestContext(nm, days, rm, analogs, "lane")
    n_with = 0
    for i in range(0, len(days), 9):
        a, p, l = c_arena[i], c_pool[i], c_lane[i]
        assert a.keys() == p.keys() == l.keys()
        for k in a:
            if k in ("analog_prices", "analog_w"):
                assert np.array_equal(a[k], p[k]), k
            elif k == "storm":
                assert np.array_equal(a[k], p[k]) and np.array_equal(a[k], l[k])
            else:
                assert a[k] == p[k] == l[k], k
        if "analog_w" in a:
            n_with += 1
            assert np.array_equal(l["analog_prices"], a["analog_prices"])        # same analog days, only the weights move
            assert np.allclose(l["analog_w"].sum(1), 1.0) and (l["analog_w"] >= 0).all()
    assert n_with > 50


def test_lane_bandwidth_does_not_depend_on_the_other_lanes():
    _, nm, analogs = _market()
    A = N.analog_matrix_stats(nm, N.reference_stats())
    days = nm.day_index("2015-01-01", "2015-03-31")
    idx, dist = N.analog_search(nm, A, day_idx=days)
    i = 40
    d = days[i]
    full = N.analog_weights_lane(dist[d])
    for lane in range(8):
        solo = N.analog_weights_lane(dist[d][lane:lane + 1])
        assert np.array_equal(full[lane], solo[0]), lane
    assert not np.array_equal(full, __import__("arena.backtest", fromlist=["x"]).analog_weights(dist[d]))


def test_slice_ledgers_identical_to_arena_and_to_stored_backtest():
    """The headline check: run_combined through the NodalMarket == the arena's simulate_market steps == the arena's
    stored ERCOT backtest, exactly, for the same days."""
    _, nm, analogs = _market()
    ref = N.arena_slice("grid", *SLICE)                                                    # arena classes only
    nodal = N.run_slice(nm, "grid", *SLICE, context="arena", verbose=True)                 # find_analogs on the NodalMarket
    assert N.ledgers_equal(ref, nodal), "NodalMarket ledgers differ from the arena's"
    stored = np.load(C.CACHE / "backtest_ERCOT_grid.npz", allow_pickle=True)
    n = len(ref["dates"])
    assert list(stored["dates"][:n].astype(str)) == list(ref["dates"])
    for k in N.ALGO_KEYS:
        for f in N.LEDGER_FIELDS:
            assert np.array_equal(stored[f"{k}_{f}"][:n], nodal["algos"][k][f]), (k, f)
    assert np.array_equal(stored["A5_picks"][:n], nodal["picks"])
    print(f"      {n} days x 8 zone lanes x A1-A5: identical to simulate_market and to the stored backtest", flush=True)


def test_built_from_helper_arrays_equals_from_market():
    m, nm, _ = _market()
    d, lanes, P, ref = N.zone_lanes()
    assert np.array_equal(P, m.P) and np.array_equal(ref, m.ref) and list(d) == list(m.dates)
    built = N.NodalMarket(lanes, P, ref, d, {z: z for z in lanes})
    for k in ("P", "ref", "gas", "years", "month", "doy", "weekend", "dates"):
        assert np.array_equal(getattr(built, k), getattr(nm, k)), k
    for k in ("tmax", "tmin", "wsf2", "prcp", "sys_tmax", "sys_tmin"):
        assert np.array_equal(built.W[k], nm.W[k]), k
    d2, l2, P2, r2 = N.zone_lanes(m.dates[200:230])
    assert np.array_equal(P2, m.P[200:230]) and np.array_equal(r2, m.ref[200:230])
    zones = N.arena_zone_of_substations()
    assert zones["BUBORA"] == "LZ_LCRA" and zones["NA"] in C.ERCOT_ZONES and len(zones) == 5035


def test_new_lanes_get_zone_weather_and_labels():
    m, nm, _ = _market()
    D = len(m.dates)
    lanes = ["NODE_A", "NODE_B", "NODE_C"]
    zone = {"NODE_A": "LZ_WEST", "NODE_B": "LZ_WEST", "NODE_C": "LZ_HOUSTON"}
    P = np.stack([m.P[:, m.zones.index("LZ_WEST"), :] + 5.0, m.P[:, m.zones.index("LZ_WEST"), :] - 3.0,
                  m.P[:, m.zones.index("LZ_HOUSTON"), :] * 1.1], 1)
    x = N.NodalMarket(lanes, P, m.ref, m.dates, zone, labels={"NODE_A": "Node A (test)"})
    assert x.P.shape == (D, 3, 96) and x.name == "ERCOT"
    iw, ih = m.zones.index("LZ_WEST"), m.zones.index("LZ_HOUSTON")
    for k in ("tmax", "tmin", "wsf2", "prcp"):
        assert np.array_equal(x.W[k][:, 0], m.W[k][:, iw]) and np.array_equal(x.W[k][:, 1], m.W[k][:, iw])
        assert np.array_equal(x.W[k][:, 2], m.W[k][:, ih])
    assert C.LABEL["NODE_A"] == "Node A (test)" and C.LABEL["NODE_B"] == "NODE_B"
    assert C.LABEL["LZ_WEST"] == "West / Permian"                                  # existing labels are never overwritten
    N.register_lanes(["LZ_WEST"], {"LZ_WEST": "changed"})
    assert C.LABEL["LZ_WEST"] == "West / Permian"
    # the whole arena stack runs on it (feature building, models, coefficient report that reads C.LABEL[z])
    short = x.subset([0, 2])
    assert short.lane_ids == ["NODE_A", "NODE_C"] and np.array_equal(short.P[:, 1], x.P[:, 2])
    rm = RegionModels(short)
    assert len(rm.coef_table()) == 2 and rm.coef_table()[0]["label"] == "Node A (test)"


def test_input_validation():
    m, _, _ = _market()
    dates, P, ref = m.dates[:40], m.P[:40, :2], m.ref[:40]
    z = {"LZ_NORTH": "LZ_NORTH", "LZ_HOUSTON": "LZ_HOUSTON"}
    ok = N.NodalMarket(["LZ_NORTH", "LZ_HOUSTON"], P, ref, dates, z)
    assert ok.P.shape == (40, 2, 96)

    def raises(fn):
        try:
            fn()
        except ValueError:
            return True
        return False

    assert raises(lambda: N.NodalMarket(["a", "a"], P, ref, dates, "LZ_NORTH"))                  # duplicate lanes
    assert raises(lambda: N.NodalMarket(["a"], P, ref, dates, "LZ_NORTH"))                       # shape mismatch
    assert raises(lambda: N.NodalMarket(["a", "b"], P, ref[:, :90], dates, "LZ_NORTH"))          # ref shape
    assert raises(lambda: N.NodalMarket(["a", "b"], P, ref, dates, "LZ_MARS"))                   # unknown zone
    assert raises(lambda: N.NodalMarket(["a", "b"], P, ref, dates, {"a": "LZ_NORTH"}))           # missing zone
    assert raises(lambda: N.NodalMarket(["a", "b"], P, ref, dates[::-1], "LZ_NORTH"))            # dates not increasing
    Pn = P.copy()
    Pn[3, 0, 10] = np.nan
    Pn[0, 1, 0] = np.nan
    assert raises(lambda: N.NodalMarket(["a", "b"], Pn, ref, dates, "LZ_NORTH"))                 # NaN without fill_gaps
    f = N.NodalMarket(["a", "b"], Pn, ref, dates, "LZ_NORTH", fill_gaps=True)
    assert np.isfinite(f.P).all()
    assert f.P[3, 0, 10] == P[3, 0, 9] and f.P[0, 1, 0] == P[0, 1, 1]                            # ffill; leading gap backfilled


if __name__ == "__main__":
    H.run_module(globals())
