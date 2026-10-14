"""Phase 2 commands: aliases and constraint table, shift factors, physics proxy, hops, map.

  run_grid.py constraints [rebuild-graph]   graph + data/station_alias.csv + data/constraints.csv (+ labels)
  run_grid.py shift-factors [fast]          RT and DAM ridge fits, validation, sf_long.parquet, data/sf_validation.csv, data/shift_factors.csv
  run_grid.py physics-proxy [rebuild-graph] DC power-flow PTDFs, calibration against the regressions, derived/ptdf_proxy.parquet
  run_grid.py hops                            data/hops.csv and data/hops_summary.csv
  run_grid.py map                             map/substations_data.js (+ .json); map/substations.html is static

Order matters: constraints -> shift-factors -> physics-proxy -> hops -> map. Run from docs/research/grid with the research venv.
"""
from __future__ import annotations

import json

import numpy as np
import pandas as pd

from . import network as N
from . import paths
from . import shiftfactors as S

MAP_TOP_LINES = 60               # ASSUMPTION: priority constraints drawn as lines on the map
MAP_SF_SCALE = 1000              # ASSUMPTION: SF values are stored as integers in thousandths to keep the data file small
MAP_NO_DATA = 32767              # ASSUMPTION: integer sentinel for "no SF / no hops value"


def _f(x):
    try:
        v = float(x)
        return v if np.isfinite(v) else None
    except (TypeError, ValueError):
        return None


def cmd_constraints(*args):
    rebuild = "rebuild-graph" in args
    G = N.build_graph() if rebuild else N.load_graph()
    print(f"graph: {len(G.nodes)} nodes, {len(G.edges)} edges ({G.edges['source'].value_counts().to_dict()})")
    alias = N.build_station_alias(G)
    alias.to_csv(N.STATION_ALIAS_CSV, index=False)
    S.write_labels_alias()
    print(f"station_alias.csv: {len(alias)} rows; methods {alias['method'].value_counts().to_dict()}")
    S.run_constraints()


def cmd_shift_factors(*args):
    S.run_shift_factors(*args)
    S.write_labels_validation()
    S.write_labels_sf_long()


def cmd_physics_proxy(*args):
    S.run_physics_proxy(*args)


def cmd_hops(*args):
    S.run_hops(*args)


def cmd_map(*args):
    cons = pd.read_csv(S.CONSTRAINTS_CSV, keep_default_na=False)
    subs = N.load_substations()
    subs = subs[subs["in_np4160"] & subs["lat"].notna()].reset_index(drop=True).sort_values("sub").reset_index(drop=True)
    G = N.load_graph()
    alias = pd.read_csv(N.STATION_ALIAS_CSV, keep_default_na=False, dtype=str)
    res = N.Resolver(N.load_substations(), alias)
    ll = G.nodes.set_index("osm_id")[["lat", "lon"]]
    hs = pd.read_csv(S.HOPS_CSV, keep_default_na=False)
    top = cons[cons["cid"].isin(hs["cid"].unique())].sort_values("priority_rank")
    index = {s: i for i, s in enumerate(subs["sub"])}
    sf_arr, hop_arr, cinfo = {}, {}, []
    for r in top.itertuples():
        h = hs[hs["cid"] == r.cid]
        sf = np.full(len(subs), MAP_NO_DATA, dtype=int)
        hp = np.full(len(subs), MAP_NO_DATA, dtype=int)
        for sub, sfv, hv in zip(h["sub"], h["sf"], h["hops"]):
            i = index.get(sub)
            if i is None:
                continue
            if sfv != "" and pd.notna(sfv):
                sf[i] = int(round(float(sfv) * MAP_SF_SCALE))
            if hv != "" and pd.notna(hv) and float(hv) < N.HOPS_UNREACHED:
                hp[i] = int(float(hv))
        sf_arr[r.cid], hop_arr[r.cid] = sf.tolist(), hp.tolist()
    lines = []
    for r in cons.sort_values("priority_rank").head(MAP_TOP_LINES).itertuples():
        pts = []
        for st, node in ((r.from_station, r.from_node), (r.to_station, r.to_node)):
            if isinstance(node, str) and node and node in ll.index:
                pts.append([round(float(ll.at[node, "lat"]), 5), round(float(ll.at[node, "lon"]), 5)])
        meth = hs.loc[hs["cid"] == r.cid, "sf_method"]
        cinfo.append(dict(
            cid=r.cid, rank=int(r.priority_rank), element=r.element, type=r.element_type, kv=_f(r.kv), score=round(_f(r.priority_score) or 0.0, 2),
            binding=bool(r.window_binding), rt_h=round(_f(r.rt_hours_binding) or 0.0, 1), dam_h=round(_f(r.dam_hours_binding) or 0.0, 1),
            rent25=_f(r.annual_rent_2025_musd), up_id=r.upgrade_project_id, up_date=r.upgrade_in_service_date, up_status=r.upgrade_status,
            up_conf=_f(r.upgrade_match_conf), sf_method=(meth.iloc[0] if len(meth) else r.sf_best), has_sf=r.cid in sf_arr, pts=pts if len(pts) == 2 else []))
    payload = dict(
        note="SF scaled x1000 (integers); 32767 = no value. cong = LMP - lambda = -sum SF x shadow price, so SF<0 is the import side.",
        scale=MAP_SF_SCALE, nodata=MAP_NO_DATA, hops_unreached=N.HOPS_UNREACHED, sf_side_min=N.SF_SIDE_MIN, sf_curtail_min=N.SF_CURTAIL_MIN,
        subs=dict(sub=list(subs["sub"]), lat=[round(float(x), 5) for x in subs["lat"]], lon=[round(float(x), 5) for x in subs["lon"]],
                  kv=[float(x) for x in subs["kv_max"]], zone=list(subs["zone"]), county=list(subs["county"]), conf=[round(float(x), 2) for x in subs["match_conf"]],
                  method=list(subs["match_method"]), rn=[1 if x else 0 for x in subs["has_resource_node"]]),
        constraints=cinfo, sf=sf_arr, hops=hop_arr)
    js = json.dumps(payload, separators=(",", ":"))
    (paths.MAP / "substations_data.json").write_text(js)
    (paths.MAP / "substations_data.js").write_text("window.GRID_MAP=" + js + ";\n")
    print(f"map data: {len(subs)} substations, {len(cinfo)} constraint lines, {len(sf_arr)} SF layers, {len(js) / 1e6:.2f} MB -> {paths.MAP}")


COMMANDS = {
    "constraints": cmd_constraints,
    "shift-factors": cmd_shift_factors,
    "physics-proxy": cmd_physics_proxy,
    "hops": cmd_hops,
    "map": cmd_map,
}
