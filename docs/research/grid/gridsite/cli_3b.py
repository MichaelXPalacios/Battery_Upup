"""Phase 3b commands: candidate sites, nodal lanes, dispatch, forward values, portfolio and the final site table.

  run_grid.py candidates [refresh]
        screen every eligible substation, write data/site_candidates.csv (+labels): top N_CANDIDATES by sum_k |SF - SF_zone| x shadow-price-hours.
  run_grid.py lanes [refresh] [skip-validation] [shrunk]
        constraint structure (data/constraint_synth.csv), synthetic shadow prices, the lane file for the candidates and the 8 controls
        (research-data/grid/derived/nodal_lanes_cand40.npz) and data/lane_validation.csv (+labels).
  run_grid.py dispatch [spec=grid|grid4h|all] [workers=4] [skip-oracle] [skip-dispatch] [skip-table]
        A1-A5 and the oracle on every lane: 2 h from 2021-01-01 (all candidates + controls), 4 h from 2024-01-01 (top 15 + controls); data/site_years.csv.
  run_grid.py forward [spec=grid|grid4h|all] [workers=4]
        2027-2031: planned upgrades and storage saturation as constraint scale factors, oracle on the rebuilt 2025 replay year, basis half-life.
  run_grid.py portfolio
        siting.portfolio per rule A1-A5, 2027-2031, 10 MW units, group caps per constraint, POI caps per node -> data/portfolio.csv.
  run_grid.py sites
        the final table data/sites.csv (+labels), joined from everything above.

Run from docs/research/grid with the research venv. Entry points that start worker processes sit behind run_grid.py's __main__ guard.
"""
from __future__ import annotations

import time

import numpy as np
import pandas as pd

from . import paths


def _kv(args):
    kv, flags = {}, set()
    for a in args:
        if "=" in a:
            k, v = a.split("=", 1)
            kv[k.strip()] = v.strip()
        else:
            flags.add(a.strip())
    return kv, flags


def cmd_candidates(*args):
    from . import sites as S

    _, fl = _kv(args)
    t0 = time.time()
    c = S.select_candidates(refresh="refresh" in fl)
    cand = c[c["role"] == "candidate"]
    cols = ["rank", "sub", "kv_max", "arena_zone", "county", "score_usd_mw_yr", "dominant_element", "dominant_side", "dominant_sf", "dominant_hops"]
    pd.set_option("display.width", 250)
    print(cand[cols].round(3).to_string(index=False))
    extra = {r: int((c["role"] == r).sum()) for r in S.EXTRA_ROLES}
    print(f"{len(cand)} candidates + {int((c['role'] == 'control_zone').sum())} controls + extra groups {extra} -> {S.CANDIDATES_CSV.name} ({time.time() - t0:.0f}s)")


def cmd_lanes(*args):
    from . import nodal_lanes as L
    from . import sites as S

    _, fl = _kv(args)
    t0 = time.time()
    include_shrunk = "shrunk" in fl
    struct, Mu, st = L.mu_tilde(include_shrunk)
    print(f"structure: {len(struct)} replayed constraints, {Mu.nnz:,} non-zero shadow prices ({time.time() - t0:.0f}s)")
    S.select_candidates(refresh="refresh" in fl)
    z = S.ensure_lanes(refresh="refresh" in fl)
    print(f"lanes: {len(z['lanes'])} x {len(z['dates'])} days -> derived/nodal_lanes_{S.LANE_TAG}.npz ({time.time() - t0:.0f}s)")
    from . import rev2

    info = rev2.write_tables()                       # R2: data/constraint_structure_rev2.csv and data/anchor_2026_applied.csv for the configuration in force
    print(f"revision-2 structure {rev2.config()}: {info}")
    if "skip-validation" not in fl:
        v = L.validation_table(struct, Mu, st)
        v.to_csv(S.VALIDATION_CSV, index=False)
        S.write_labels(S.VALIDATION_CSV, S.VALIDATION_LABELS, v)
        a = v[(v["check"] == "a_window_rebuild") & (v["scope"].str.startswith("3-10 Oct, all")) & v["scope"].str.endswith("all replayed constraints")]
        print(a[["metric", "value"]].to_string(index=False))
        print(f"validation -> {S.VALIDATION_CSV.name} ({time.time() - t0:.0f}s)")


def cmd_dispatch(*args):
    from . import sites as S

    kv, fl = _kv(args)
    spec = kv.get("spec", "all")
    workers = int(kv.get("workers", S.DISPATCH_WORKERS))
    t0 = time.time()
    z = S.ensure_lanes()
    ctrl = list(S.L.ZONES)
    cand = S.lane_subs()                              # R2: the 40, the regional picks and the revision-1 sites that left the list
    if spec in ("grid", "all"):
        lanes = ctrl + cand
        if "skip-dispatch" not in fl:
            chunk = int(kv.get("chunk", min(32, -(-len(lanes) // max(workers, 1)))))   # one chunk per worker: the cost of a task is nearly flat in its lanes (notes/phase3a.md section 5)
            res = S.run_dispatch(S.SPEC_2H, lanes, S.SIM_START, workers=workers, z=z, chunk=chunk)
            print(f"2 h dispatch: {len(lanes)} lanes, wall {time.time() - t0:.0f}s")
        if "skip-oracle" not in fl:
            S.run_oracle(S.SPEC_2H, lanes, S.SIM_START, workers=workers, z=z)
            print(f"2 h oracle done ({time.time() - t0:.0f}s)")
    if spec in ("grid4h", "all"):
        top = S.top_candidates_2h()
        lanes4 = ctrl + top
        if "skip-dispatch" not in fl:
            S.run_dispatch(S.SPEC_4H, lanes4, S.SIM_START_4H, workers=workers, z=z, chunk=-(-len(lanes4) // max(workers, 1)))
            print(f"4 h dispatch: {len(lanes4)} lanes ({time.time() - t0:.0f}s)")
        if "skip-oracle" not in fl:
            S.run_oracle(S.SPEC_4H, lanes4, S.SIM_START_4H, workers=workers, z=z)
            print(f"4 h oracle done ({time.time() - t0:.0f}s)")
    if "skip-table" not in fl:
        S.write_site_years()
        print(f"site_years -> {S.SITE_YEARS_CSV.name} ({time.time() - t0:.0f}s)")


def cmd_forward(*args):
    from . import sites as S

    kv, fl = _kv(args)
    spec = kv.get("spec", "all")
    specs = (S.SPEC_2H, S.SPEC_4H) if spec == "all" else (spec,)
    S.run_forward(workers=int(kv.get("workers", S.DISPATCH_WORKERS)), specs=specs)


def cmd_portfolio(*args):
    from . import sites as S

    S.run_portfolio()


def cmd_sites(*args):
    from . import sites as S

    S.write_sites()
    ex = S.write_sites(roles=S.EXTRA_ROLES)                   # R2: data/extra_sites.csv
    print(f"extra_sites: {len(ex)} rows ({ex['role'].value_counts().to_dict()}) -> {S.EXTRA_SITES_CSV.name}")
    S.print_report()


COMMANDS = {"candidates": cmd_candidates, "lanes": cmd_lanes, "dispatch": cmd_dispatch, "forward": cmd_forward, "portfolio": cmd_portfolio, "sites": cmd_sites}
