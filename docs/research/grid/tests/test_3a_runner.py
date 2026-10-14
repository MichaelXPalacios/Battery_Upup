"""The parallel runner: chunked == single-process (exactly), lane order and chunk-mates do not matter, the chunked
oracle equals oracle_days, and the results container behaves.

Lanes in these tests are three real zone lanes plus three synthetic nodes (zone price with a different scale, a daily
wiggle and noise), so lanes in one chunk have different price levels and the pooled quantities the arena would use
(analog standardisation, kernel bandwidth) would differ between chunkings.

Run:  python tests/test_3a_runner.py
"""
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _harness_3a as H  # noqa: E402

import numpy as np  # noqa: E402

from gridsite import nodal_market as N  # noqa: E402
from gridsite import paths, runner  # noqa: E402

paths.use_arena()
from arena.battery import Spec, oracle_days  # noqa: E402
from arena.data import Market  # noqa: E402

END_HISTORY = "2015-02-20"
START = "2015-01-26"                                    # 26 simulated days; models exist (>365 earlier days)
_CACHE = {}


def _inputs():
    if "x" in _CACHE:
        return _CACHE["x"]
    m = Market("ERCOT")
    n = int(np.searchsorted(m.dates, END_HISTORY, side="right"))
    dates, ref = m.dates[:n], m.ref[:n]
    g = np.random.default_rng(7)
    cols, ids, zone = [], [], {}
    for z in ("LZ_NORTH", "LZ_WEST", "LZ_HOUSTON"):
        cols.append(m.P[:n, m.zones.index(z), :])
        ids.append(z)
        zone[z] = z
    t = np.arange(96)
    for k, z in enumerate(("LZ_WEST", "LZ_SOUTH", "LZ_NORTH"), 1):
        base = m.P[:n, m.zones.index(z), :]
        P = base * (1 + 0.12 * k) + 4.0 * k * np.sin(2 * np.pi * t / 96)[None, :] + g.normal(0, 2.0 * k, base.shape)
        cols.append(P)
        ids.append(f"NODE_{k}")
        zone[f"NODE_{k}"] = z
    P = np.stack(cols, 1)
    _CACHE["x"] = (ids, P, ref, dates, zone)
    return _CACHE["x"]


def _run(ids, P, ref, dates, zone, **kw):
    kw.setdefault("start", START)
    kw.setdefault("progress", False)
    return runner.run_lanes(ids, P, ref=ref, dates=dates, lane_zone=zone, **kw)


def test_plan_chunks():
    assert runner.plan_chunks(10, 4, 3) == [(0, 3), (3, 6), (6, 9), (9, 10)]
    assert runner.plan_chunks(200, 8) == [(a, min(a + 16, 200)) for a in range(0, 200, 16)]   # default chunk size
    assert runner.plan_chunks(100, 8) == [(a, min(a + 13, 100)) for a in range(0, 100, 13)]   # < 8 full chunks: 8 balanced
    assert len(runner.plan_chunks(100, 8)) == 8
    assert runner.plan_chunks(5, 8) == [(i, i + 1) for i in range(5)]                      # small jobs are split finer
    assert runner.plan_chunks(1000, 8, 500) == [(a, min(a + 64, 1000)) for a in range(0, 1000, 64)]   # MAX_CHUNK_LANES


def test_chunked_equals_single_process_exactly():
    ids, P, ref, dates, zone = _inputs()
    one = _run(ids, P, ref, dates, zone, workers=1, chunk=6)                  # one chunk, this process
    chunked = _run(ids, P, ref, dates, zone, workers=3, chunk=2)              # three chunks, three processes
    uneven = _run(ids, P, ref, dates, zone, workers=2, chunk=4)               # chunks of 4 and 2
    assert one.equals(chunked), "chunked run differs from single-process run"
    assert one.equals(uneven)
    assert one.timing["n_chunks"] == 1 and chunked.timing["n_chunks"] == 3
    assert one.picks.shape == (26, 6) and one.algos["A5"]["profit"].shape == (26, 6)
    assert np.isfinite(one.algos["A5"]["profit"]).all()
    # the lanes are genuinely different from one another (the test would be vacuous otherwise)
    assert len({round(float(one.algos["A2"]["profit"][:, i].sum()), 3) for i in range(6)}) == 6
    print(f"      single {one.timing['wall_s']:.0f}s vs 3 workers {chunked.timing['wall_s']:.0f}s", flush=True)


def test_a_lane_does_not_depend_on_its_chunk_mates_or_order():
    ids, P, ref, dates, zone = _inputs()
    full = _run(ids, P, ref, dates, zone, workers=1, chunk=6)
    rev = _run(ids[::-1], P[:, ::-1, :], ref, dates, zone, workers=2, chunk=3)       # new order, new chunk-mates
    for lane in ids:
        a, b = full.lane(lane), rev.lane(lane)
        for algo in N.ALGO_KEYS:
            for f in N.LEDGER_FIELDS:
                assert np.array_equal(a["algos"][algo][f], b["algos"][algo][f]), (lane, algo, f)
        assert np.array_equal(a["picks"], b["picks"]), lane
    solo = _run(["NODE_2"], P[:, [ids.index("NODE_2")], :], ref, dates, zone, workers=1, chunk=1)
    assert np.array_equal(solo.algos["A5"]["profit"][:, 0], full.lane("NODE_2")["algos"]["A5"]["profit"])
    assert np.array_equal(solo.algos["A3"]["energy"][:, 0], full.lane("NODE_2")["algos"]["A3"]["energy"])


def test_provider_paths_equal_array_path():
    ids, P, ref, dates, zone = _inputs()
    base = _run(ids, P, ref, dates, zone, workers=1, chunk=6)
    via_array = runner.run_lanes(ids, None, ref=ref, dates=dates, lane_zone=zone, start=START, progress=False,
                                 provider=runner.ArrayProvider(P, ids), workers=2, chunk=3)
    assert base.equals(via_array)
    with tempfile.TemporaryDirectory() as td:
        f = Path(td) / "lanes.npy"
        np.save(f, P.astype(np.float64))
        via_npy = runner.run_lanes(ids, None, ref=ref, dates=dates, lane_zone=zone, start=START, progress=False,
                                   provider=runner.NpyProvider(f, ids), workers=2, chunk=3)
        assert base.equals(via_npy)
        del via_npy


def test_results_container():
    ids, P, ref, dates, zone = _inputs()
    r = _run(ids, P, ref, dates, zone, workers=1, chunk=6)
    for algo in ("A1", "A5"):
        v = r.usd_kw_yr(algo, 2015)
        assert v.shape == (6,)
        tot = r.algos[algo]["profit"].sum(0)
        assert np.allclose(v, tot * (365.0 / 26) / 1000.0)                          # partial year annualised (arena rule)
        assert np.allclose(r.usd_kw_yr(algo, 2015, annualise=False), tot / 1000.0)
        assert np.allclose(r.usd_kw_yr(algo, None), v)                              # one calendar year in the span
    pm = r.usd_per_mwh("A5", 2015)
    assert np.allclose(pm, r.algos["A5"]["profit"].sum(0) / r.algos["A5"]["mwh"].sum(0))
    assert np.allclose(r.pick_shares().sum(1), 1.0) and r.pick_shares("NODE_1").shape == (6,)
    tab = r.table(2015)
    assert set(tab) == set(ids) and set(tab["NODE_1"]) == set(N.ALGO_KEYS)
    # save / load round trip, and merging separate runs of different lanes
    with tempfile.TemporaryDirectory() as td:
        r.save(Path(td) / "r.npz")
        back = runner.LaneResults.load(Path(td) / "r.npz")
        assert r.equals(back) and back.spec == "grid" and back.context == "lane"
    a = _run(ids[:2], P[:, :2, :], ref, dates, zone, workers=1, chunk=2)
    b = _run(ids[2:], P[:, 2:, :], ref, dates, zone, workers=1, chunk=4)
    assert runner.LaneResults.merge_lanes([a, b]).equals(r)
    try:
        runner.LaneResults.merge_lanes([a, a])
    except ValueError:
        pass
    else:
        raise AssertionError("duplicate lanes must be rejected")


def test_resumable_cache():
    ids, P, ref, dates, zone = _inputs()
    sel = [0, 1, 3, 4]                                              # 4 lanes, chunk 2 -> 2 chunks
    ids4, P4 = [ids[i] for i in sel], P[:, sel, :]
    with tempfile.TemporaryDirectory() as td:
        first = _run(ids4, P4, ref, dates, zone, workers=2, chunk=2, cache_dir=td)
        files = sorted(Path(td).glob("lanes_*.npz"))
        assert len(files) == 2 and first.timing["cached_chunks"] == 0
        again = _run(ids4, P4, ref, dates, zone, workers=2, chunk=2, cache_dir=td)
        assert again.timing["cached_chunks"] == 2 and again.equals(first)
        assert np.isnan(again.timing["lane_days_per_s"])                       # nothing was computed in that call
        files[0].unlink()                                                      # a crashed run lost one chunk
        resumed = _run(ids4, P4, ref, dates, zone, workers=1, chunk=2, cache_dir=td)
        assert resumed.timing["cached_chunks"] == 1 and resumed.equals(first)
        assert len(list(Path(td).glob("lanes_*.npz"))) == 2
        # a changed price changes the key: nothing is reused
        P5 = P4.copy()
        P5[-3, 0, 5] += 1.0
        changed = _run(ids4, P5, ref, dates, zone, workers=1, chunk=2, cache_dir=td)
        assert changed.timing["cached_chunks"] == 1                            # only the untouched chunk (lanes 2,3) is reused
        # providers need a token
        prov = runner.ArrayProvider(P4, ids4)
        try:
            runner.run_lanes(ids4, None, ref=ref, dates=dates, lane_zone=zone, start=START, progress=False, provider=prov,
                             workers=1, chunk=2, cache_dir=td)
        except ValueError as e:
            assert "cache_token" in str(e)
        else:
            raise AssertionError("a provider run needs a cache_token")
        viap = runner.run_lanes(ids4, None, ref=ref, dates=dates, lane_zone=zone, start=START, progress=False, provider=prov,
                                workers=1, chunk=2, cache_dir=td, cache_token="v1")
        assert viap.equals(first)


def test_oracle_chunked_equals_oracle_days():
    ids, P, ref, dates, zone = _inputs()
    Q = P[-30:, :5, :]
    for key in ("grid", "grid4h"):
        direct = np.stack([oracle_days(Q[:, l, :], Spec(key)) for l in range(5)], 1)
        one = runner.oracle_lanes(Q, key, workers=1, chunk=2, day_block=7)
        par = runner.oracle_lanes(Q, key, workers=2, chunk=2, day_block=11)
        assert np.array_equal(direct, one) and np.array_equal(direct, par), key
    assert (runner.oracle_lanes(Q, "grid4h", workers=1) >= runner.oracle_lanes(Q, "grid", workers=1) - 1e-9).all()


def test_worker_failure_propagates():
    ids, P, ref, dates, zone = _inputs()
    bad = dict(zone)
    bad["NODE_2"] = "LZ_MARS"
    try:
        _run(ids, P, ref, dates, bad, workers=2, chunk=3)
    except ValueError as e:
        assert "LZ_MARS" in str(e)
    else:
        raise AssertionError("a bad lane must raise in the parent")
    try:
        runner.run_lanes(ids, P, provider=lambda x: P, ref=ref, dates=dates, lane_zone=zone)
    except ValueError:
        pass
    else:
        raise AssertionError("P and provider together must raise")


if __name__ == "__main__":
    H.run_module(globals())
