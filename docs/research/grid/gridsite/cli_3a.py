"""Phase 3a commands: arena hook proof, nodal runner, AS layer, connection costs, queue map.

  run_grid.py compat-test [year=2024 | days=30] [skip-siting] [skip-nodal]
        arena group-cap hook: tests/test_arena_compat.py (>= 500 randomised cases, every rule, bit-identical), then the NodalMarket
        equivalence: the arena's simulate_market steps vs a NodalMarket of the 8 zone lanes over the same days, ledgers compared exactly.
        year=2024 runs a full year in 3 processes (about 10 minutes); the default is the first 30 days of the arena backtest.
  run_grid.py as-layer [skip-coopt]
        data/as_revenue.csv (+ labels); prints the upper bound by year for 2 h and 4 h and the co-optimisation estimate on the zone lanes.
  run_grid.py costs                         data/connection_costs.csv (+ labels); prints class counts and cost summary.
  run_grid.py queue-map [calibrate]         data/queue_sub_map.csv (+ labels); prints MW mapped by confidence. `calibrate` re-measures the
                                            hit rates behind the name and nearest-substation confidences.
  run_grid.py runner-bench [lanes=64] [days=60] [workers=8] [chunk=16] [spec=grid] [matrix]
        throughput of runner.run_lanes on synthetic nodes, in lane-days per second. `matrix` tries several worker/chunk settings.

Run from docs/research/grid with the research venv. Entry points that start processes sit behind run_grid.py's __main__ guard.
"""
from __future__ import annotations

import subprocess
import sys
import time

import numpy as np

from . import paths


def _kv(args):
    """['a=1', 'flag'] -> ({'a': '1'}, {'flag'})"""
    kv, flags = {}, set()
    for a in args:
        if "=" in a:
            k, v = a.split("=", 1)
            kv[k.strip()] = v.strip()
        else:
            flags.add(a.strip())
    return kv, flags


def _table(rows, cols, fmt=None):
    fmt = fmt or {}
    w = {c: max(len(c), *(len(fmt.get(c, str)(r[c])) for r in rows)) for c in cols}
    print("  " + "  ".join(c.rjust(w[c]) for c in cols))
    for r in rows:
        print("  " + "  ".join(fmt.get(c, str)(r[c]).rjust(w[c]) for c in cols))


# --------------------------------------------------------------------------- compat-test
def cmd_compat_test(*args):
    kv, flags = _kv(args)
    if "skip-siting" not in flags:
        print("== arena siting hook: tests/test_arena_compat.py", flush=True)
        r = subprocess.run([sys.executable, str(paths.GRID_DIR / "tests" / "test_arena_compat.py")], cwd=str(paths.GRID_DIR))
        if r.returncode != 0:
            raise SystemExit("siting compatibility test FAILED")
    if "skip-nodal" in flags:
        return
    from . import runner
    if "year" in kv:
        start, end = f"{kv['year']}-01-01", f"{kv['year']}-12-31"
    else:
        start, end = "2015-01-01", f"2015-01-{int(kv.get('days', 30)):02d}"
    spec = kv.get("spec", "grid")
    print(f"== NodalMarket (8 zone lanes) vs the arena's simulate_market steps, {spec}, {start}..{end}", flush=True)
    out = runner.equivalence_check(spec, start, end, include_lane=True)
    print(f"  days {out['n_days']}  lanes {len(out['lanes'])}  wall {out['wall_s']:.0f}s")
    print(f"  NodalMarket ledgers == arena ledgers (A1-A5 profit/energy/wear/mwh, backup energy, learner picks): "
          f"{'IDENTICAL' if out['nodal_equals_arena'] and out['picks_equal'] else 'DIFFERENT'}")
    if not out["nodal_equals_arena"]:
        print("  field by field:", out["field_equal"])
    print("  arena $/kW-yr by zone (annualised):")
    for a, v in out["usd_kw_yr_arena"].items():
        print(f"    {a}: " + " ".join(f"{x:7.2f}" for x in v))
    if "lane_context_vs_arena_usd_kw_yr" in out:
        print(f"  lane-safe context (runner default) minus arena context, $/kW-yr; identical ledgers: {out['lane_context_identical_to_arena']}")
        for a, v in out["lane_context_vs_arena_usd_kw_yr"].items():
            print(f"    {a}: " + " ".join(f"{x:7.2f}" for x in v))
    if not (out["nodal_equals_arena"] and out["picks_equal"]):
        raise SystemExit("NodalMarket equivalence FAILED")


# --------------------------------------------------------------------------- as-layer
def cmd_as_layer(*args):
    kv, flags = _kv(args)
    from . import ancillary as A
    df = A.write_as_revenue_csv()
    print(f"wrote {paths.REPO_DATA / A.CSV_NAME} ({len(df)} rows) and its labels")
    rows = []
    for y in range(2018, 2027):
        u2, u4 = A.as_upper_bound(y, "grid"), A.as_upper_bound(y, "grid4h")
        s2, s4 = A.as_upper_bound(y, "grid", stack_regdn=True), A.as_upper_bound(y, "grid4h", stack_regdn=True)
        rows.append(dict(year=f"{y}{'*' if u2['partial'] else ''}", regup=u2["by_product"]["REGUP"] / 8.76, ub2=u2["usd_kw_yr"], ub4=u4["usd_kw_yr"],
                         st2=s2["usd_kw_yr"], st4=s4["usd_kw_yr"], top1=u2["top1pct_share"]))
    print("DAM AS upper bound, $/kW-yr (best eligible product each interval); regup = mean Reg Up price $/MW-h; * = partial year, annualised")
    _table(rows, ["year", "regup", "ub2", "ub4", "st2", "st4", "top1"], {c: (lambda v: f"{v:.2f}") for c in ("regup", "top1")} | {c: (lambda v: f"{v:.1f}") for c in ("ub2", "ub4", "st2", "st4")})
    print("  ub2/ub4 = 2 h / 4 h battery; st2/st4 = the looser bound that also sells Reg Down; top1 = share of the bound earned in the top 1% of intervals")
    for y in (2025, 2026):
        u2, u4 = A.as_upper_bound(y, "grid", "RT"), A.as_upper_bound(y, "grid4h", "RT")
        print(f"  RT {y}: 2 h {u2['usd_kw_yr']:.1f}  4 h {u4['usd_kw_yr']:.1f} $/kW-yr, coverage {u2['coverage']:.2f}{' (partial, annualised)' if u2['partial'] else ''}")
    if "skip-coopt" in flags:
        return
    import numpy as np  # noqa: F811
    from arena import config as C
    from arena.data import Market
    bt = np.load(C.CACHE / "backtest_ERCOT_grid.npz", allow_pickle=True)
    bdates = bt["dates"].astype(str)
    m = Market("ERCOT")
    P = m.P[np.searchsorted(m.dates, bdates)]
    print("Co-optimisation estimate on the arena's stored 2 h ERCOT ledgers (mean of the 8 zones, $/kW-yr; DAM AS prices):")
    out = []
    for algo in ("A1", "A5"):
        per = [A.as_vs_energy(P[:, z, :], bt[f"{algo}_energy"][:, z], bt[f"{algo}_wear"][:, z], bt[f"{algo}_mwh"][:, z], bdates, "grid")["by_year"]
               for z in range(8)]
        for y in range(2022, 2027):
            out.append(dict(algo=algo, year=f"{y}{'*' if per[0][y]['partial'] else ''}", energy=np.mean([p[y]["energy"] for p in per]),
                            as_idle=np.mean([p[y]["as_idle"] for p in per]), swap=np.mean([p[y]["as_swap"] for p in per]),
                            total=np.mean([p[y]["total"] for p in per])))
    _table(out, ["algo", "year", "energy", "as_idle", "swap", "total"], {c: (lambda v: f"{v:.1f}") for c in ("energy", "as_idle", "swap", "total")})
    print("Perfect-foresight ceilings for LZ_NORTH (energy-only oracle vs energy + AS, Reg Down stacked), $/kW-yr:")
    sel = np.where(np.isin(m.dates, bdates) & (m.years >= 2023))[0]
    for key in ("grid", "grid4h"):
        up = A.as_oracle_uplift(m.P[sel, 0, :], m.dates[sel], key)
        for y, d in up.items():
            print(f"  {key:6s} {y}{'*' if d['partial'] else ' '} energy oracle {d['energy_oracle']:7.1f}  energy+AS oracle {d['energy_plus_as_oracle']:7.1f}  AS uplift {d['as_uplift']:7.1f}")


# --------------------------------------------------------------------------- costs
def cmd_costs(*args):
    from . import costs as K
    df = K.write_connection_costs()
    print(f"wrote {K.OUT_CSV} ({len(df)} rows) and its labels")
    print("connection class counts:")
    cnt = df["connection_class"].value_counts()
    for k, v in cnt.items():
        print(f"  {k:15s} {v:5d}")
    conf = df[df["class_conf"] >= 0.5]
    print(f"  of which class_conf >= 0.5 (geometry or graph evidence): {conf['connection_class'].value_counts().to_dict()}")
    print(f"  default class (class_conf 0.3: unplaced or low-confidence placement): {(df['class_conf'] < 0.5).sum()}")
    g = df.groupby("connection_class")[["cost_mid_musd", "owner_share_musd", "lead_time_months", "gen_tie_km"]].median().round(2)
    print("medians by class:\n" + g.to_string())
    print(f"owner share above the allowance > 0: {(df['owner_share_musd'] > 0).sum()} substations; lead time median {df['lead_time_months'].median():.1f} months")


# --------------------------------------------------------------------------- queue-map
def cmd_queue_map(*args):
    kv, flags = _kv(args)
    from . import queue_map as Q
    df = Q.write_queue_sub_map()
    print(f"wrote {Q.OUT_CSV} ({len(df)} rows) and its labels")
    print(Q.summary(df).to_string())
    print(df.groupby(["source", "method"]).agg(rows=("mw", "size"), mw=("mw", "sum")).round(0).to_string())
    if "calibrate" in flags:
        Q.calibrate()


# --------------------------------------------------------------------------- runner-bench
def cmd_runner_bench(*args):
    kv, flags = _kv(args)
    paths.use_arena()
    from arena.data import Market
    from . import runner
    lanes_n, days = int(kv.get("lanes", 64)), int(kv.get("days", 60))
    spec = kv.get("spec", "grid")
    configs = [(int(kv.get("workers", runner.DEFAULT_WORKERS)), int(kv.get("chunk", runner.DEFAULT_CHUNK_LANES)))]
    if "matrix" in flags:
        configs = [(1, lanes_n), (4, 16), (8, 8), (8, 16), (8, 32)]
    m = Market("ERCOT")
    lane_ids = [f"NODE_{i:04d}" for i in range(lanes_n)]
    zone_of = {x: m.zones[i % len(m.zones)] for i, x in enumerate(lane_ids)}
    provider = runner.SyntheticNodeProvider(m.P, lane_ids, list(range(len(m.zones))))
    start, end = m.dates[-days], m.dates[-1]
    print(f"{lanes_n} synthetic nodes x {days} simulated days ({start}..{end}), history {m.dates[0]}..{m.dates[-1]} ({len(m.dates)} days), spec {spec}")
    rows = []
    for workers, chunk in configs:
        if workers == 1 and lanes_n > 16:
            sel = lane_ids[:16]            # a single process over every lane would take too long; time 16 lanes
        else:
            sel = lane_ids
        t0 = time.time()
        res = runner.run_lanes(sel, None, ref=m.ref, dates=m.dates, lane_zone=zone_of, spec=spec, start=start, end=end,
                               provider=provider, workers=workers, chunk=chunk, progress=False)
        tm = res.timing
        setup = np.mean([c["total_s"] - c["run_s"] for c in tm["chunks"]])
        run = np.mean([c["run_s"] for c in tm["chunks"]])
        rows.append(dict(workers=workers, chunk=tm["chunk"], lanes=len(sel), days=tm["n_days"], wall=tm["wall_s"], lane_days_s=tm["lane_days_per_s"],
                         setup=setup, run=run, rss=tm["peak_worker_rss_mb"]))
        print(f"  workers {workers} chunk {tm['chunk']}: {len(sel)} lanes x {tm['n_days']} days in {tm['wall_s']:.0f}s = "
              f"{tm['lane_days_per_s']:.1f} lane-days/s (per chunk: setup {setup:.0f}s, dispatch {run:.0f}s; peak worker RSS {tm['peak_worker_rss_mb']:.0f} MB)", flush=True)
    if len(rows) > 1:
        _table(rows, ["workers", "chunk", "lanes", "days", "wall", "lane_days_s", "setup", "run", "rss"],
               {c: (lambda v: f"{v:.1f}") for c in ("wall", "lane_days_s", "setup", "run", "rss")})


COMMANDS = {
    "compat-test": cmd_compat_test,
    "as-layer": cmd_as_layer,
    "costs": cmd_costs,
    "queue-map": cmd_queue_map,
    "runner-bench": cmd_runner_bench,
}
