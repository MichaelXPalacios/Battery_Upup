"""Phase 6c: the interactive map of the study (map/index.html + map/index_data.js).

One Leaflet page that opens as a local file (the data comes in through a <script> tag, because browsers block fetch() of local JSON). Layers, each with a toggle:

  placed substations (by match confidence) | the 40 candidate sites (size = 2 h learner value, colour = gain over the load zone, popup = the whole row of data/sites.csv)
  priority constraint lines (width = priority, ends marked import / export where the shift factor is known) | upgrades U01-U10 (cost, battery-vs-wires ratio, rerank)
  risk-excluded substations | the 100 substations with the highest reliability need | Tier 2 candidates (thinned, optional)
  R2 (revision 2): regional candidates (R1's 41 picks in the Panhandle, Valley, Far West, Laredo/South and the Bell County pocket, valued like the 40) | regional basis
  (the measured summer 2026 day-ahead basis of the placed settlement points against their load zone, with the five-region summary)

Reads only tables in data/ and derived/graph.nodes.parquet (line end coordinates). Run: run_grid.py map          (the Phase 2 map is `run_grid.py map substations`)
Labels on the page follow PLAN.md: REAL, DERIVED, SIM, ASSUMPTION.
"""
from __future__ import annotations

import json
import math

import numpy as np
import pandas as pd

from . import paths

REPO_DATA = paths.REPO_DATA
MAP_DIR = paths.MAP
OUT_HTML = MAP_DIR / "index.html"
OUT_DATA = MAP_DIR / "index_data.js"

# =================================================================================================================
# Constants
# =================================================================================================================
AS_OF = "11 Oct 2026"                    # REAL: date of this build (the study's last day)
LEAFLET_VERSION = "1.9.4"                # REAL: brief: Leaflet 1.9.4 from cdnjs, an exact version
LEAFLET_JS_SRI = "sha512-puJW3E/qXDqYp9IfhAI54BJEaWIfloJ7JWs7OeD5i6ruC9JZL1gERT1wjtwXFlh7CjE7ZJ+/vcRZRkIYIb6p4g=="    # REAL: sha512 of cdnjs leaflet.min.js 1.9.4 (computed on 11 Oct 2026)
LEAFLET_CSS_SRI = "sha512-h9FcoyWjHcOcmEVkxOfTLnmZFWIH0iZhZT1H2TbOq55xssQGEJHEaIm+PgoUaZbRvQTNTluNOEfb1ZRy6D3BOw=="   # REAL: sha512 of cdnjs leaflet.min.css 1.9.4 (computed on 11 Oct 2026)
TOP_CONSTRAINTS = 60                     # ASSUMPTION: priority constraints drawn (the same count as the Phase 2 map)
TOP_RELIABILITY = 100                    # REAL: brief: the top 100 by reliability need
TIER2_VALUED_ALL = True                  # ASSUMPTION: every Tier 2 candidate at a valued bus goes on the map
TIER2_TOP_RELIABILITY = 150              # ASSUMPTION: plus the Tier 2 candidates with the highest reliability need
TIER2_CELL_DEG = 0.5                     # ASSUMPTION: plus the best (by reliability need) in each cell of this size, so the layer covers the state
TIER2_MAX = 700                          # ASSUMPTION: cap on the Tier 2 layer
COORD_DECIMALS = 4                       # ASSUMPTION: 4 decimals is about 11 m, finer than the placement
MAX_MB = 5.0                             # REAL: brief: total data under about 5 MB
SF_SIDE_MIN = 0.02                       # REAL: phase 2 SF_SIDE_MIN: below this a shift factor is called neutral
UPLIFT_CLASSES = (0.0, 10.0, 25.0, 50.0, 100.0)   # ASSUMPTION: class edges of the colour ramp, $/kW-yr of gain over the load zone
CONF_BANDS = (0.85, 0.7, 0.5)            # REAL: phase 1c reading of match_conf: 0.85 and up name, county and voltage agree; 0.7-0.85 name and voltage; 0.5-0.7 plausible; below 0.5 a hint (notes/phase1-geo.md)


# =================================================================================================================
# Helpers
# =================================================================================================================
def _read(name: str) -> pd.DataFrame:
    return pd.read_csv(REPO_DATA / name, keep_default_na=False, na_values=[""], low_memory=False)


def _clean(x):
    """JSON-safe: numpy -> python, NaN / inf -> None, floats rounded."""
    if isinstance(x, (np.floating, float)):
        x = float(x)
        return None if (math.isnan(x) or math.isinf(x)) else round(x, 4)
    if isinstance(x, (np.integer,)):
        return int(x)
    if isinstance(x, (np.bool_,)):
        return bool(x)
    if x is pd.NA or x is pd.NaT:
        return None
    if isinstance(x, dict):
        return {str(k): _clean(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [_clean(v) for v in x]
    return x


def _ll(x):
    return round(float(x), COORD_DECIMALS)


def _true(series: pd.Series) -> pd.Series:
    """True where a CSV column that mixes True / False / blank says True (the dtype is bool or object depending on the blanks)."""
    return series.astype(str).str.strip().str.lower().eq("true")


# =================================================================================================================
# Payload
# =================================================================================================================
def _subs() -> dict:
    s = _read("substations.csv")
    s = s[_true(s["in_np4160"]) & s["lat"].notna()].sort_values("sub").reset_index(drop=True)
    return dict(sub=list(s["sub"]), lat=[_ll(v) for v in s["lat"]], lon=[_ll(v) for v in s["lon"]], kv=[_clean(v) for v in s["kv_max"]],
                conf=[round(float(v), 2) for v in s["match_conf"]], zone=[_clean(v) for v in s["zone"]], county=[_clean(v) for v in s["county"]],
                rn=[1 if x else 0 for x in _true(s["has_resource_node"])])


def _sites() -> dict:
    s = _read("sites.csv")
    lab = _read("sites.labels.csv").set_index("column")["label"].to_dict()
    cols = list(s.columns)
    rr = _read("upgrade_rerank.csv") if (REPO_DATA / "upgrade_rerank.csv").exists() else None
    eff = {}
    if rr is not None:
        from .upgrade_rerank import MATERIAL_USD_KW_YR

        m = rr[rr["delta_usd_kw_yr"].abs() >= MATERIAL_USD_KW_YR]
        for r in m.itertuples():
            eff.setdefault(r.sub, []).append([r.scenario, round(r.value_before_usd_kw_yr, 1), round(r.value_after_usd_kw_yr, 1), int(r.rank_before), int(r.rank_after)])
    rows = [[_clean(v) for v in r] for r in s.itertuples(index=False, name=None)]
    return dict(cols=cols, labels=[lab.get(c, "") for c in cols], rows=rows, effects={k: v for k, v in eff.items()})


def _graph_ll() -> pd.DataFrame:
    return pd.read_parquet(paths.DERIVED / "graph.nodes.parquet").set_index("osm_id")[["lat", "lon"]]


def _end_pts(row, ll) -> list:
    """[lat, lon] of each line end that has a graph node (phase 2 drops both ends of a line whose placed ends are over 80 km apart, and so do we)."""
    pts = []
    for node in (row["from_node"], row["to_node"]):
        pts.append([_ll(ll.at[node, "lat"]), _ll(ll.at[node, "lon"])] if isinstance(node, str) and node in ll.index else None)
    return pts


def _constraints() -> list:
    c = _read("constraints.csv")
    sf = _read("shift_factors.csv")[["sub", "cid", "sf", "method"]]
    sfi = {(r.sub, r.cid): (r.sf, r.method) for r in sf.itertuples()}
    ll = _graph_ll()
    out = []
    c = c[c["priority_rank"].notna()].sort_values("priority_rank")
    for el, g in c.groupby("constraint_name", sort=False):
        g = g.sort_values("priority_rank")
        r = g.iloc[0]
        if int(r["priority_rank"]) > TOP_CONSTRAINTS:
            continue
        pts = _end_pts(r, ll)
        ends = []
        for sb, p in zip((r["from_sub"], r["to_sub"]), pts):
            hit = None
            for cid in g["cid"]:
                if (sb, cid) in sfi:
                    hit = sfi[(sb, cid)]
                    break
            v = float(hit[0]) if hit else None
            side = None if v is None else ("import" if v <= -SF_SIDE_MIN else "export" if v >= SF_SIDE_MIN else "neutral")
            ends.append(dict(sub=sb if isinstance(sb, str) else None, pt=p, sf=None if v is None else round(v, 3), side=side, method=hit[1] if hit else None))
        both = pts[0] is not None and pts[1] is not None
        out.append(dict(
            key=el, rank=int(r["priority_rank"]), element=r["element"], type=r["element_type"], kv=_clean(r["kv"]), n_cid=int(len(g)), how=str(r["endpoint_how"]),
            score=round(float(g["priority_score"].max()), 2), binding=bool(g["window_binding"].any()), rt_h=round(float(g["rt_hours_binding"].sum()), 1),
            dam_h=round(float(g["dam_hours_binding"].max()), 1), rent25=_clean(g["annual_rent_2025_musd"].max()), up_id=_clean(r["upgrade_project_id"]),
            up_date=_clean(r["upgrade_in_service_date"]), up_status=_clean(r["upgrade_status"]), up_conf=_clean(r["upgrade_match_conf"]),
            pts=[pts[0], pts[1]] if both else [], ends=ends, drawn=bool(both)))
    return out


def _upgrades(cons: list) -> list:
    u = _read("upgrades.csv")
    u = u[u["upgrade_id"].isin([f"U{i:02d}" for i in range(1, 11)])].sort_values("upgrade_id")
    ck = {c["key"]: c for c in cons}
    rr = _read("upgrade_rerank.csv") if (REPO_DATA / "upgrade_rerank.csv").exists() else None
    from .upgrade_rerank import MATERIAL_USD_KW_YR

    sub = _read("substations.csv")
    sub = sub[_true(sub["in_np4160"]) & sub["lat"].notna() & (sub["match_conf"] >= CONF_BANDS[-1])]
    subs_ll = {r.sub: [_ll(r.lat), _ll(r.lon)] for r in sub.itertuples()}
    out = []
    for r in u.itertuples():
        c = ck.get(r.element_key)
        pts, mark = [], None
        if c and c["drawn"]:
            pts = c["pts"]
        elif c:
            for e in c["ends"]:                                  # one end has a graph node
                if e["pt"]:
                    mark = e["pt"]
                    break
            if mark is None:                                     # both ends failed the length test: use an end that is an exact NP4-160 code
                parts = c["how"].split("|")
                if parts and parts[0].startswith("rejected_") and len(parts) == 3:
                    for e, how in zip(c["ends"], parts[1:]):
                        if how == "np4160" and e["sub"] in subs_ll:
                            mark = subs_ll[e["sub"]]
                            break
        d = dict(id=r.upgrade_id, element=r.element, key=r.element_key, type=r.element_type, upgrade_type=r.upgrade_type, kv=_clean(r.kv), limit_mw=_clean(r.limit_mw),
                 cost_low=_clean(r.cost_low_musd), cost_mid=_clean(r.cost_mid_musd), cost_high=_clean(r.cost_high_musd), lead_mid=_clean(r.lead_time_mid_months),
                 lead_low=_clean(r.lead_time_low_months), lead_high=_clean(r.lead_time_high_months), planned=_clean(r.planned), planned_id=_clean(r.planned_id),
                 planned_isd=_clean(r.planned_isd), nwa_mw=_clean(r.nwa_central_mw), nwa_mwh=_clean(r.nwa_central_mwh), nwa_hours=_clean(r.nwa_central_hours),
                 nwa_ratio=_clean(r.nwa_vs_wires_ratio), nwa_vs_cheapest=_clean(r.nwa_vs_cheapest_ratio), rent25=_clean(r.imm_rent_2025_musd),
                 rent_to_cost=_clean(r.imm_rent_to_annual_wires_cost), length_mi=_clean(r.length_mi), pts=pts, mark=mark,
                 where="line" if pts else ("end" if mark else "none"))
        if rr is not None:
            g = rr[rr["scenario"] == r.upgrade_id]
            if len(g):
                top = g.sort_values("delta_usd_kw_yr").head(4)
                d["rerank"] = dict(effect=g["scenario_effect"].iloc[0], n_losers=int(g["scn_n_losers"].iloc[0]), n_gainers=int(g["scn_n_gainers"].iloc[0]),
                                   sum40_pct=_clean(g["scn_sum40_change_pct"].iloc[0]), port_static_pct=_clean(g["scn_portfolio200_static_change_pct"].iloc[0]),
                                   losers=[[x.sub, round(x.value_before_usd_kw_yr, 1), round(x.value_after_usd_kw_yr, 1), int(x.rank_before), int(x.rank_after)] for x in top.itertuples()
                                           if x.delta_usd_kw_yr <= -MATERIAL_USD_KW_YR])
        out.append(d)
    return out


def _excluded() -> list:
    r = _read("risk_flags.csv")
    r = r[_true(r["exclude_flag"]) & r["lat"].notna()]
    return [dict(sub=x.sub, lat=_ll(x.lat), lon=_ll(x.lon), kv=_clean(x.kv_max), county=_clean(x.county), zone=_clean(x.zone), reason=_clean(x.exclude_reason),
                 flood=_clean(x.flood_zone), wildfire=_clean(x.wildfire_class), conf=_clean(x.match_conf)) for x in r.itertuples()]


def _reliability() -> list:
    r = _read("reliability.csv")
    r = r[r["need_rank"].notna() & (r["need_rank"] <= TOP_RELIABILITY) & r["lat"].notna()].sort_values("need_rank")
    out = []
    for x in r.itertuples():
        out.append(dict(sub=x.sub, lat=_ll(x.lat), lon=_ll(x.lon), kv=_clean(x.kv_max), county=_clean(x.county), zone=_clean(x.zone), rank=int(x.need_rank),
                        score=_clean(x.reliability_need_score), driver=_clean(x.main_driver), outage=_clean(x.outage_score), pocket=_clean(x.pocket_score),
                        facility=_clean(x.facility_score), large_load=_clean(x.large_load_score), eue=_clean(x.eue_avoided_mwh_yr_full), voll=_clean(x.voll_usd_yr_duration),
                        bridge=_clean(x.behind_bridge), pocket_nodes=_clean(x.pocket_outer_nodes), hospitals=_clean(x.n_hospital_10km)))
    return out


def _tier2() -> list:
    name = "tier2_valued.csv" if (REPO_DATA / "tier2_valued.csv").exists() else "tier2.csv"
    t = _read(name)
    t = t[t["lat"].notna()].copy()
    valued = t["nodal_value_screened"].astype(str).str.lower().eq("true") if "nodal_value_screened" in t.columns else pd.Series(False, index=t.index)
    keep = pd.Series(False, index=t.index)
    if TIER2_VALUED_ALL:
        keep |= valued
    need = pd.to_numeric(t["reliability_need_score"], errors="coerce")
    keep |= need.rank(ascending=False, method="first") <= TIER2_TOP_RELIABILITY
    cell = (np.floor(t["lat"] / TIER2_CELL_DEG).astype(int).astype(str) + "_" + np.floor(t["lon"] / TIER2_CELL_DEG).astype(int).astype(str))
    best = need.fillna(-1).groupby(cell).idxmax()
    keep.loc[best.values] = True
    t = t[keep].copy()
    if len(t) > TIER2_MAX:
        t = t.assign(_k=valued[t.index].astype(int) * 1e6 + pd.to_numeric(t["reliability_need_score"], errors="coerce").fillna(0)).sort_values("_k", ascending=False).head(TIER2_MAX)
    has = "nodal_value_usd_kw_yr" in t.columns
    out = []
    for x in t.itertuples():
        d = x._asdict()
        out.append(dict(
            id=d["cand_id"], src=d["candidate_source"], lat=_ll(d["lat"]), lon=_ll(d["lon"]), kv=_clean(d["kv_max"]), county=_clean(d["county"]), zone=_clean(d["settlement_zone"]),
            desr_sub=_clean(d["route_desr_nodal_sub"]), desr_basis=_clean(d["route_desr_basis"]), ader_zone=_clean(d["route_ader_zone"]),
            nodal=_clean(d["nodal_value_usd_kw_yr"]) if has else None, uplift=_clean(d["nodal_vs_zone_uplift_usd_kw_yr"]) if has else None,
            nodal29=_clean(d["nodal_value_2029_usd_kw_yr"]) if has and "nodal_value_2029_usd_kw_yr" in d else None, vsrc=_clean(d["nodal_value_source"]) if has else None,
            ader=_clean(d["ader_zone_value_usd_kw_yr"]) if has and "ader_zone_value_usd_kw_yr" in d else None, element=_clean(d["nodal_dominant_element"]) if has and "nodal_dominant_element" in d else None,
            side=_clean(d["nodal_dominant_side"]) if has and "nodal_dominant_side" in d else None, defer=_clean(d["deferral_usd_kw_yr_mid"]), need=_clean(d["reliability_need_score"]),
            rank=_clean(d["need_rank"]), cap=_clean(d["screen_fast_path_cap_mw"]), excl=_clean(d["exclude_reason"]) if str(d["exclude_flag"]).strip().lower() == "true" else None,
            home=_clean(d["home_fleet_merchant_usd_kw_yr"]), program=_clean(d["home_fleet_best_program"]), program_v=_clean(d["home_fleet_best_program_usd_kw_yr"]),
            dist=_clean(d["nodal_distance_to_element_km"]) if has and "nodal_distance_to_element_km" in d else None,
            dclass=_clean(d["nodal_distance_class"]) if has and "nodal_distance_class" in d else None,
            valued=bool(valued.loc[x.Index]) if has else False))
    return out


REGION_COLORS = {"Panhandle": "#7b3f98", "Rio Grande Valley": "#c0392b", "Far West": "#d9822b", "Laredo/South": "#1f8a70", "Central (Bell County)": "#2f6f9f"}   # ASSUMPTION: one colour per region of the regional layer
BASIS_CLASSES = (1.0, 3.0, 6.0)          # ASSUMPTION: class edges of the measured-basis ramp, $/MWh of |mean day-ahead basis against the load zone|


def _regional() -> dict:
    """R2: R1's regional picks, valued like the 40 (data/extra_sites.csv, role regional): the whole row plus its labels, as for the sites."""
    e = _read("extra_sites.csv")
    e = e[e["role"] == "regional"].reset_index(drop=True)
    lab = _read("extra_sites.labels.csv").set_index("column")["label"].to_dict()
    cols = list(e.columns)
    rows = [[_clean(v) for v in r] for r in e.itertuples(index=False, name=None)]
    return dict(cols=cols, labels=[lab.get(c, "") for c in cols], rows=rows, colors=REGION_COLORS)


def _basis() -> dict:
    """R2: the measured summer 2026 day-ahead basis (data/regional_basis.csv) of every placed settlement point, in columns."""
    b = _read("regional_basis.csv")
    b = b[b["lat"].notna() & b["lon"].notna()].reset_index(drop=True)
    return dict(sp=list(b["settlement_point"]), sub=[_clean(v) for v in b["substation"]], region=list(b["region"]), zone=[_clean(v) for v in b["zone"]], kv=[_clean(v) for v in b["kv"]],
                county=[_clean(v) for v in b["county"]], lat=[_ll(v) for v in b["lat"]], lon=[_ll(v) for v in b["lon"]], conf=[_clean(v) for v in b["geo_conf"]],
                zmean=[_clean(v) for v in b["basis_zone_mean"]], zp05=[_clean(v) for v in b["basis_zone_p05"]], zp95=[_clean(v) for v in b["basis_zone_p95"]],
                smean=[_clean(v) for v in b["basis_sys_mean"]], g10=[_clean(v) for v in b["hours_gt10_zone"]], worst=[_clean(v) for v in b["top3_mean_zone"]],
                vnode=[_clean(v) for v in b["dam_value_node"]], vzone=[_clean(v) for v in b["dam_value_zone"]], upl=[_clean(v) for v in b["dam_uplift"]],
                n=[_clean(v) for v in b["n_hours"]], classes=list(BASIS_CLASSES))


def _region_summary() -> list:
    s = _read("regional_basis_summary.csv")
    out = []
    for reg, g in s.groupby("region", sort=False):
        z = g[g["reference"] == "zone"].iloc[0]
        y = g[g["reference"] == "system"].iloc[0]
        h = g[g["reference"] == "hub"].iloc[0]
        out.append(dict(region=reg, n=int(z["n_nodes"]), zone=_clean(z["basis_mean"]), zone_p05=_clean(z["pooled_p05"]), system=_clean(y["basis_mean"]), hub=_clean(h["basis_mean"]),
                        neg10=_clean(z["share_hours_neg10"]), pos10=_clean(z["share_hours_pos10"]), worst_hour=_clean(z["worst_hour_cpt"]), worst_mean=_clean(z["worst_hour_mean"]),
                        uplift=_clean(z["dam_uplift_mean"]), verdict=_clean(z["verdict"])))
    return out


def build_payload() -> dict:
    cons = _constraints()
    return dict(
        meta=dict(built=AS_OF, leaflet=LEAFLET_VERSION, uplift_classes=list(UPLIFT_CLASSES), sf_side_min=SF_SIDE_MIN, conf_bands=list(CONF_BANDS),
                  note="SIM = rebuilt node prices (levels good to about a factor of 2, ranking more stable); REAL = a named public source; DERIVED = computed from REAL numbers; "
                       "ASSUMPTION = our choice. Market value and reliability value are never added."),
        subs=_subs(), sites=_sites(), constraints=cons, upgrades=_upgrades(cons), excluded=_excluded(), reliability=_reliability(), tier2=_tier2(),
        regional=_regional(), basis=_basis(), region_summary=_region_summary())


# =================================================================================================================
# Page
# =================================================================================================================
HTML = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="color-scheme" content="light dark">
<title>ERCOT battery siting map</title>
<link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/leaflet/__LEAFLET__/leaflet.min.css" integrity="__CSS_SRI__" crossorigin="anonymous">
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Hanken+Grotesk:wght@400;700&display=swap">
<style>
:root{
  --brand:#1e4d2b; --brand-hover:#2a6a3b; --brand-tint:#e4efe6; --ink:#10231a; --ink-2:#34443a; --muted:#5f6b63;
  --bg:#f7f4ec; --panel:#fffdf8; --border:#e3dfd3; --border-strong:#cfcabd; --board:#efece3;
  --import:#2f6f9f; --export:#c7962b; --flag:#b23a2f; --home:#b9b4a6; --upgrade:#6b4c9a; --reliab:#0f6b6b; --tier2:#7b6a4f;
  --font-sans:'Hanken Grotesk',system-ui,-apple-system,'Segoe UI',sans-serif; --ease-state:cubic-bezier(.3,.7,.2,1); --dur-state:300ms;
  --gutter:16px; --panel-w:344px; --sw-line:rgba(16,35,26,.45);
}
@media (prefers-color-scheme: dark){
  :root:not([data-theme="light"]){
    --brand:#8fc29c; --brand-hover:#a9d4b4; --brand-tint:#1d3326; --ink:#e7efe9; --ink-2:#c4d1c8; --muted:#97a79c;
    --bg:#0f1713; --panel:#16231b; --border:#26372d; --border-strong:#3a4f42; --board:#1b2a21; --sw-line:rgba(231,239,233,.6);
  }
}
:root[data-theme="dark"]{
  --brand:#8fc29c; --brand-hover:#a9d4b4; --brand-tint:#1d3326; --ink:#e7efe9; --ink-2:#c4d1c8; --muted:#97a79c;
  --bg:#0f1713; --panel:#16231b; --border:#26372d; --border-strong:#3a4f42; --board:#1b2a21; --sw-line:rgba(231,239,233,.6);
}
*{box-sizing:border-box}
html,body{height:100%;margin:0}
body{font-family:var(--font-sans);font-size:14px;line-height:1.5;color:var(--ink);background:var(--bg);display:flex;font-variant-numeric:tabular-nums;overflow-x:hidden}
aside{width:var(--panel-w);flex:none;padding:var(--gutter);overflow-y:auto;overflow-x:hidden;background:var(--panel);border-right:1px solid var(--border);overflow-wrap:anywhere}
#map{flex:1;min-width:0;background:var(--board)}
h1{font-size:20px;line-height:1.15;letter-spacing:-0.02em;margin:0 0 4px;font-weight:700}
h2{font-size:10px;letter-spacing:0.08em;text-transform:uppercase;color:var(--muted);margin:16px 0 6px;font-weight:700}
h3{font-size:15px;line-height:1.2;margin:0 0 2px;font-weight:700}
p{margin:0 0 8px}
a{color:var(--brand)}
.small{font-size:12.5px;color:var(--ink-2)}
.tag{display:inline-block;font-size:10px;letter-spacing:0.08em;text-transform:uppercase;border:1px solid var(--border-strong);border-radius:3px;padding:0 5px;color:var(--muted);margin-left:4px;vertical-align:1px;white-space:nowrap}
label{display:flex;gap:8px;align-items:flex-start;margin:3px 0;cursor:pointer}
label input{margin-top:3px;flex:none}
select{width:100%;font:inherit;padding:6px 8px;border:1px solid var(--border-strong);border-radius:6px;background:var(--panel);color:var(--ink)}
button{font:inherit;border:1px solid var(--border-strong);background:var(--panel);border-radius:8px;padding:4px 10px;cursor:pointer;color:var(--ink)}
button:hover{background:var(--brand-tint)}
details{border:1px solid var(--border);border-radius:10px;padding:6px 12px;margin:8px 0;background:var(--panel)}
details>summary{cursor:pointer;font-weight:700;list-style:none}
details>summary::-webkit-details-marker{display:none}
details[open]>summary{margin-bottom:6px}
.sw{display:inline-block;width:12px;height:12px;border-radius:50%;margin-right:6px;vertical-align:-2px;border:1px solid var(--sw-line);flex:none}
.sw.sq{border-radius:2px}
.row{display:flex;justify-content:space-between;gap:8px}
.legend-line{display:flex;align-items:center;gap:8px;margin:3px 0}
.legend-line .ln{display:inline-block;width:28px;height:0;border-top:3px solid var(--ink);flex:none}
.legend-line .ln.dash{border-top-style:dashed;border-top-color:var(--muted)}
.ramp{display:flex;gap:2px;margin:6px 0 2px}
.ramp span{flex:1;height:12px;border:1px solid var(--sw-line)}
.sizes{display:flex;align-items:flex-end;gap:14px;margin:6px 0}
.sizes div{text-align:center;font-size:11px;color:var(--muted)}
.sizes i{display:block;margin:0 auto 2px;border-radius:50%;background:var(--brand);opacity:.85;border:1px solid var(--ink)}
.warn{border:1px solid var(--flag);border-radius:10px;padding:8px 12px;margin-top:8px}
.leaflet-container{font-family:var(--font-sans);background:var(--board)}
.leaflet-popup-content-wrapper,.leaflet-popup-tip{background:var(--panel);color:var(--ink);box-shadow:none;border:1px solid var(--border-strong)}
.leaflet-popup-content{font-size:13px;margin:10px 12px;line-height:1.45}
.pop h3{margin-bottom:2px}
.pop table{border-collapse:collapse;width:100%;margin:6px 0}
.pop th{font-weight:400;color:var(--muted);text-align:left;padding:1px 8px 1px 0;vertical-align:top;width:46%}
.pop td{padding:1px 0;vertical-align:top;overflow-wrap:anywhere}
.pop .sec{font-size:10px;letter-spacing:0.08em;text-transform:uppercase;color:var(--muted);font-weight:700;margin:8px 0 2px}
.pop details{padding:4px 8px;margin:6px 0}
.pop details table th{width:58%}
.mk-x{color:var(--flag);font-weight:700;font-size:18px;line-height:14px;text-align:center;text-shadow:0 0 2px #fff}
.mk-up{background:var(--upgrade);color:#fff;font-weight:700;font-size:11px;line-height:16px;text-align:center;border-radius:4px;border:1.5px solid #fff;min-width:30px;padding:0 3px}
.mk-t2{width:9px;height:9px;background:var(--tier2);border:1px solid #fff;transform:rotate(45deg)}
.mk-reg{width:11px;height:11px;border:1.5px solid #10231a;transform:rotate(45deg);margin:1px}
.panel-toggle{font:inherit;font-weight:700;padding:5px 12px;border-radius:8px;border:1px solid var(--border-strong);background:var(--panel);color:var(--ink);cursor:pointer}
.panel-toggle:hover{background:var(--brand-tint)}
body.panel-hidden aside{display:none}
#err{display:none}
@media (max-width:760px){
  body{flex-direction:column}
  aside{width:100%;max-height:46vh;border-right:0;border-bottom:1px solid var(--border)}
  #map{min-height:300px}
}
</style>
</head>
<body>
<aside id="panel">
  <h1>Where to put batteries on the ERCOT grid</h1>
  <p class="small">Batter Up research, __BUILT__. Public data only. A 2 h battery, energy only, earns different money at different substations; this map shows where, and what would change it.</p>

  <details open id="howto"><summary>How to read this map</summary>
    <p class="small"><b>Green circles</b> are the 40 substations where a 2 h battery earns the most in our model. <b>Bigger</b> = more $/kW-yr (learner, 2021&ndash;25 mean). <b>Darker</b> = a bigger gain over what the same battery earns at its load-zone price. Click one for its whole row.</p>
    <p class="small"><b>Diamonds</b> are the regional candidates in the Panhandle, the Valley, the Far West, Laredo/South and the Bell County pocket (picked from measured summer 2026 prices, valued like the 40 but never ranked with them). The <b>basis layer</b> shows those measured prices: where a node traded below (amber) or above (blue) its load zone. In this window the Valley and Panhandle trade <b>below</b> their zone, an export discount a battery does not gain from.</p>
    <p class="small"><b>Lines</b> are the transmission constraints that bind most (thicker = higher priority; dashed = did not bind in our week of data). A <b style="color:var(--import)">blue end</b> is the <b>import side</b>: the price rises when the line binds, so a battery sells there. An <b style="color:var(--export)">amber end</b> is the <b>export side</b>: the price falls, so a battery charges there and, injecting, loads the line.</p>
    <p class="small">Electrical distance, not hop count, decides value: a substation 8 hops away can be worth more than one at the end of the line. Values hold for the <b>first ~10 MW</b> behind a line only.</p>
    <p class="small">Every number carries a label: <span class="tag">REAL</span> a named source, <span class="tag">DERIVED</span> computed from real numbers, <span class="tag">SIM</span> a model output, <span class="tag">ASSUMPTION</span> our choice. Node prices are <span class="tag">SIM</span>: levels are good to about a factor of 2, rankings are more stable. Market value and reliability value are never added.</p>
  </details>

  <h2>Layers</h2>
  <label><input type="checkbox" id="l-sites" checked><span><span class="sw" style="background:var(--brand)"></span>Candidate sites (<span id="n-sites"></span>)</span></label>
  <label><input type="checkbox" id="l-cons" checked><span><span class="sw sq" style="background:var(--ink)"></span>Priority constraint lines (<span id="n-cons"></span>)</span></label>
  <label><input type="checkbox" id="l-up" checked><span><span class="sw sq" style="background:var(--upgrade)"></span>Upgrades U01&ndash;U10 (<span id="n-up"></span>)</span></label>
  <label><input type="checkbox" id="l-subs" checked><span><span class="sw" style="background:#8f9a92"></span>Placed substations (<span id="n-subs"></span>)</span></label>
  <label style="margin-left:22px"><input type="checkbox" id="l-low"><span>include confidence below __CONF_LO__ <span class="small">(a hint, not a position)</span></span></label>
  <label><input type="checkbox" id="l-excl"><span><span class="sw" style="background:#fff;border-color:var(--flag);color:var(--flag);font-size:11px;line-height:10px;text-align:center">&times;</span>Risk-excluded substations (<span id="n-excl"></span>)</span></label>
  <label><input type="checkbox" id="l-rel"><span><span class="sw" style="background:transparent;border:2px solid var(--reliab)"></span>Top 100 by reliability need (<span id="n-rel"></span>)</span></label>
  <label><input type="checkbox" id="l-t2"><span><span class="sw sq" style="background:var(--tier2);transform:rotate(45deg) scale(.8)"></span>Tier 2 distribution candidates, thinned (<span id="n-t2"></span>)</span></label>
  <label><input type="checkbox" id="l-reg" checked><span><span class="sw sq" style="background:#d9822b;transform:rotate(45deg) scale(.8)"></span>Regional candidates: Panhandle, Valley, Far West, Laredo/South, Bell County (<span id="n-reg"></span>)</span></label>
  <label><input type="checkbox" id="l-basis"><span><span class="sw" style="background:#2f6f9f"></span><span class="sw" style="background:#c7962b"></span>Measured summer basis, 1 Jul&ndash;11 Aug 2026 (<span id="n-basis"></span> placed points)</span></label>

  <h2>Jump to a site</h2>
  <select id="jump" aria-label="Jump to a candidate site"></select>

  <h2>Legend</h2>
  <div class="small"><b>Candidate sites</b>: size = 2 h learner value, <span class="tag">SIM</span></div>
  <div class="sizes" id="sizes"></div>
  <div class="small">colour = gain over the load zone, $/kW-yr</div>
  <div class="ramp" id="ramp"></div>
  <div class="row small" id="ramp-lab"></div>
  <div class="small" style="margin-top:10px"><b>Placed substations</b> by match confidence <span class="tag">DERIVED</span></div>
  <div class="legend-line"><span class="sw" style="background:#34443a"></span>__CONF_HI__ or more</div>
  <div class="legend-line"><span class="sw" style="background:#5f6b63"></span>__CONF_MID__ to __CONF_HI__</div>
  <div class="legend-line"><span class="sw" style="background:#8f9a92"></span>__CONF_LO__ to __CONF_MID__</div>
  <div class="legend-line"><span class="sw" style="background:#cfcabd"></span>below __CONF_LO__</div>
  <div class="small" style="margin-top:10px"><b>Constraint lines</b></div>
  <div class="legend-line"><span class="ln"></span>binds in the week of data; thicker = higher priority</div>
  <div class="legend-line"><span class="ln dash"></span>did not bind in the week (from the 2025 market-monitor rents)</div>
  <div class="legend-line"><span class="sw" style="background:var(--import)"></span>import end (shift factor &le; &minus;__SF_SIDE__)</div>
  <div class="legend-line"><span class="sw" style="background:var(--export)"></span>export end (shift factor &ge; +__SF_SIDE__)</div>
  <div class="legend-line"><span class="sw" style="background:#fff"></span>end with no fitted shift factor</div>

  <div class="small" style="margin-top:10px"><b>Regional candidates</b> <span class="tag">DERIVED</span> (diamonds; R1's picks, valued like the 40 but never ranked with them)</div>
  <div id="reg-legend"></div>
  <div class="small" style="margin-top:10px"><b>Measured basis</b> = mean day-ahead price at the node minus its load zone, $/MWh <span class="tag">REAL</span></div>
  <div class="ramp" id="bramp"></div>
  <div class="row small" id="bramp-lab"></div>

  <details id="regsum"><summary>Regions: what the summer prices show</summary><div class="small" id="regsum-body"></div></details>
  <details id="notdrawn"><summary>Upgrades and lines not on the map</summary><div class="small" id="notdrawn-body"></div></details>
  <p class="small">Data: <code>index_data.js</code> beside this page, built by <code>gridsite/report_map.py</code> from the tables in <code>data/</code>. Report: <a href="../README.md">README.md</a>. Base map &copy; OpenStreetMap contributors (ODbL).</p>
  <div class="warn small" id="err"></div>
  <noscript><div class="warn small">This map needs JavaScript.</div></noscript>
</aside>
<div id="map" role="application" aria-label="Map of ERCOT substations, constraints and candidate battery sites"></div>

<script src="https://cdnjs.cloudflare.com/ajax/libs/leaflet/__LEAFLET__/leaflet.min.js" integrity="__JS_SRI__" crossorigin="anonymous"></script>
<script src="index_data.js"></script>
<script>
(function(){
'use strict';
var D = window.GRID_REPORT_MAP, errBox = document.getElementById('err');
function fail(msg){ errBox.style.display='block'; errBox.textContent=msg; }
if(!D){ fail('index_data.js did not load. Run run_grid.py map from docs/research/grid, then reload.'); return; }
if(!window.L){ fail('Leaflet __LEAFLET__ did not load from cdnjs (offline?). The page needs network access for Leaflet and the map tiles.'); return; }
try{

function esc(s){ return String(s==null?'':s).replace(/[&<>"']/g,function(c){return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c];}); }
function num(x,nd,pre,suf){ if(x==null||x===''||isNaN(x)) return 'n/a'; var v=Number(x), a=Math.abs(v).toLocaleString('en-US',{minimumFractionDigits:nd,maximumFractionDigits:nd}); var neg=v<0&&Number(a.replace(/,/g,''))!==0; return (neg?'−':'')+(pre||'')+a+(suf||''); }
function usd(x,nd){ return num(x,nd==null?1:nd,'$'); }
function tag(l){ return l?'<span class="tag">'+esc(l)+'</span>':''; }
function tr(k,v,l){ return '<tr><th>'+k+'</th><td>'+v+tag(l)+'</td></tr>'; }
function sec(t){ return '<div class="sec">'+t+'</div>'; }
function yn(b){ return b===true?'yes':(b===false?'no':'n/a'); }
var MW = Math.min(380, Math.max(240, window.innerWidth-64));
var popOpt = {maxWidth:MW, maxHeight:Math.round(Math.max(260, window.innerHeight*0.62)), autoPanPadding:[16,16]};

var map = L.map('map',{zoomSnap:0.25, preferCanvas:true}).setView([31.2,-99.3], window.innerWidth<760?5.5:6);
L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png',{maxZoom:18,attribution:'&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors (ODbL)'}).addTo(map);
var panes = {subs:410,lines:420,tier2:425,upg:430,rel:440,excl:445,ends:450,sites:460};
Object.keys(panes).forEach(function(k){ var p=map.createPane(k); p.style.zIndex=panes[k]; });
var canvasSubs = L.canvas({pane:'subs',padding:0.5});

var tgl=L.control({position:'topright'});
tgl.onAdd=function(){ var b=L.DomUtil.create('button','panel-toggle'); b.type='button'; b.textContent='Hide panel'; b.setAttribute('aria-expanded','true'); L.DomEvent.disableClickPropagation(b);
  b.addEventListener('click',function(){ var h=document.body.classList.toggle('panel-hidden'); b.textContent=h?'Show panel':'Hide panel'; b.setAttribute('aria-expanded',h?'false':'true'); setTimeout(function(){ map.invalidateSize(); },60); }); return b; };
tgl.addTo(map);

// ---------- candidate sites
var S = D.sites, ci = {}; S.cols.forEach(function(c,i){ ci[c]=i; });
function g(row,c){ var v=row[ci[c]]; return v===undefined?null:v; }
var lab = {}; S.cols.forEach(function(c,i){ lab[c]=S.labels[i]; });
var CL = D.meta.uplift_classes;
var RAMP = ['#cfcabd','#d7e5da','#a9c9b1','#5f9a6e','#2f7040','#10331c'];
function upCls(u){ if(u==null||u<=CL[0]) return 0; for(var i=1;i<CL.length;i++){ if(u<CL[i]) return i; } return CL.length; }
function rad(v){ return 4+0.8*Math.sqrt(Math.max(v||0,0)); }

function sitePopup(row){
  var f=function(c){return g(row,c);};
  var h='<div class="pop"><h3>'+esc(f('sub'))+' <span class="tag">'+f('kv_max')+' kV</span></h3><div class="small">'+esc(f('county'))+' County, '+esc(f('zone'))+' &middot; rank '+f('rank')+' of 40 by screening score &middot; placement confidence '+f('match_conf')+tag('DERIVED')+'</div>';
  h+=sec('What a 2 h battery earns here (energy only, after wear)')+'<table>';
  h+=tr('Learner, 2021&ndash;25 mean',usd(f('h2_mean2125_A5'))+'/kW-yr','SIM');
  h+=tr('Learner, 2025 / 2026 to 26 Sep',usd(f('h2_y2025_A5'))+' / '+usd(f('h2_ytd2026_A5')),'SIM');
  h+=tr('Gain over its load zone (2021&ndash;25)',usd(f('h2_mean2125_uplift_usd_kw_yr_A5')),'SIM');
  var av=['A1','A2','A3','A4','A5'].map(function(a){return f('h2_mean2125_'+a);}), best=Math.max.apply(null,av);
  h+=tr('The five algorithms, 2021&ndash;25 ($/kW-yr)',av.map(function(v,i){var t='A'+(i+1)+' '+num(v,0); return v===best?'<b>'+t+'</b>':t;}).join(' &middot; '),'SIM');
  h+=tr('Perfect-foresight ceiling',usd(f('h2_mean2125_oracle')),'SIM');
  h+=tr('Margin per MWh discharged (learner)',usd(f('h2_mean2125_usd_per_mwh_A5'),0),'SIM');
  h+=tr('4 h battery, 2024&ndash;25 (learner)',f('h4_mean2425_A5')==null?'not run (top 15 only)':usd(f('h4_mean2425_A5')),'SIM');
  h+=tr('Ancillary services (system-wide, not added)',usd(f('as_system_estimate_2h_2025'))+' (2025), '+usd(f('as_system_estimate_2h_2026'))+' (2026)','SIM');
  h+='</table>'+sec('The constraint behind it')+'<table>';
  h+=tr('Dominant element',esc(f('dominant_element'))+' ('+esc(f('dominant_side'))+' side)','DERIVED');
  h+=tr('Shift factor / hops',num(f('constraint1_sf'),2)+' ('+esc(f('constraint1_sf_method'))+') / '+(f('constraint1_hops')==null?'n/a':num(f('constraint1_hops'),0)),'DERIVED');
  [2,3].forEach(function(k){ if(f('constraint'+k)) h+=tr('Also exposed to',esc(f('constraint'+k))+' ('+esc(f('constraint'+k+'_side'))+', SF '+num(f('constraint'+k+'_sf'),2)+')','DERIVED'); });
  h+=tr('Planned upgrade on the element',f('dominant_upgrade_project')?esc(f('dominant_upgrade_project'))+', '+(f('dominant_upgrade_date')?esc(f('dominant_upgrade_date')):'no date')+' (match '+f('dominant_upgrade_match_conf')+')':'none matched','DERIVED');
  h+='</table>'+sec('2027&ndash;2031: saturation and upgrades')+'<table>';
  h+=tr('Learner 2027 / 2029 / 2031',usd(f('fwd_learner_2027'))+' / '+usd(f('fwd_learner_2029'))+' / '+usd(f('fwd_learner_2031')),'SIM');
  h+=tr('Basis half-life',esc(f('basis_half_life')),'SIM');
  h+=tr('Room: price-taker cap / 100 MW offsets',num(f('price_taker_cap_mw'),0)+' MW for all sites behind the element / '+(f('relief_100mw_share_of_limit')==null?'n/a':num(100*f('relief_100mw_share_of_limit'),0)+'%')+' of its limit','ASSUMPTION');
  h+='</table>'+sec('Getting connected')+'<table>';
  h+=tr('Connection',esc(f('conn_class'))+', lead time '+num(f('conn_lead_months'),1)+' months, all-in '+usd(f('conn_all_in_usd_per_kw'),0)+'/kW','DERIVED');
  h+=tr('Net of connection / break-even installed cost',usd(f('net_of_connection_usd_kw_yr'))+'/kW-yr / '+usd(f('breakeven_installed_usd_per_kw'),0)+'/kW','DERIVED');
  h+=tr('Physical risk',['flood '+esc(f('risk_flood')),'wildfire '+esc(f('risk_wildfire_class')),'seismic '+yn(f('risk_seismic')),'excluded '+yn(f('risk_exclude'))].join(', '),'DERIVED');
  h+='</table>'+sec('Reliability (own units, never added to market value)')+'<table>';
  h+=tr('Need score / rank',f('reliability_need_score')==null?'not scored':num(f('reliability_need_score'),1)+' / '+(f('reliability_need_rank')==null?'not ranked':'#'+num(f('reliability_need_rank'),0)+' of 2,554'),'ASSUMPTION');
  h+='</table>';
  var ef=(S.effects[f('sub')]||[]);
  if(ef.length){
    h+=sec('If an upgrade happens (full valuation, 2025 replay)')+'<table>';
    ef.sort(function(a,b){return (a[2]-a[1])-(b[2]-b[1]);}).slice(0,6).forEach(function(e){ h+=tr(esc(e[0]),usd(e[1])+' &rarr; '+usd(e[2])+', rank '+e[3]+' &rarr; '+e[4],'SIM'); });
    h+='</table>';
  }
  h+='<details><summary>All '+S.cols.length+' fields of this row</summary><table>';
  S.cols.forEach(function(c,i){ var v=row[i]; h+='<tr><th>'+esc(c)+'</th><td>'+(v==null?'':esc(typeof v==='number'?(Math.round(v*1000)/1000):v))+tag(lab[c])+'</td></tr>'; });
  h+='</table></details></div>';
  return h;
}
var siteLayer=L.layerGroup(), siteMarkers={};
S.rows.forEach(function(row){
  var v=g(row,'h2_mean2125_A5'), u=g(row,'h2_mean2125_uplift_usd_kw_yr_A5');
  var m=L.circleMarker([g(row,'lat'),g(row,'lon')],{pane:'sites',radius:rad(v),weight:1.5,color:'#10231a',fillColor:RAMP[upCls(u)],fillOpacity:0.88});
  m.bindPopup(sitePopup(row),popOpt);
  m.bindTooltip(g(row,'sub')+'  '+usd(v,0),{direction:'top',offset:[0,-4]});
  m.addTo(siteLayer); siteMarkers[g(row,'sub')]=m;
});
siteLayer.addTo(map);
document.getElementById('n-sites').textContent=S.rows.length;
var jump=document.getElementById('jump'); jump.innerHTML='<option value="">(choose a site)</option>';
S.rows.slice().sort(function(a,b){return g(b,'h2_mean2125_A5')-g(a,'h2_mean2125_A5');}).forEach(function(r){
  var o=document.createElement('option'); o.value=g(r,'sub'); o.textContent=g(r,'sub')+'  '+usd(g(r,'h2_mean2125_A5'),0)+'/kW-yr  ('+g(r,'zone').replace('LZ_','')+')'; jump.appendChild(o); });
jump.addEventListener('change',function(){ var m=siteMarkers[jump.value]; if(!m) return; map.setView(m.getLatLng(),Math.max(map.getZoom(),9)); m.openPopup(); });

// legend: sizes and ramp
var sz=document.getElementById('sizes'); [25,100,250].forEach(function(v){ var d=document.createElement('div'); var r=rad(v); d.innerHTML='<i style="width:'+2*r+'px;height:'+2*r+'px"></i>$'+v; sz.appendChild(d); });
var rp=document.getElementById('ramp'); RAMP.forEach(function(c){ var s=document.createElement('span'); s.style.background=c; rp.appendChild(s); });
document.getElementById('ramp-lab').innerHTML='<span>none</span><span>0&ndash;10</span><span>10&ndash;25</span><span>25&ndash;50</span><span>50&ndash;100</span><span>100+</span>';

// ---------- placed substations
var Sb=D.subs, nb=Sb.sub.length, subLayers={hi:L.layerGroup(),mid:L.layerGroup(),lo:L.layerGroup(),low:L.layerGroup()};
var CB=D.meta.conf_bands;
function confBand(c){ return c>=CB[0]?'hi':(c>=CB[1]?'mid':(c>=CB[2]?'lo':'low')); }
var BCOL={hi:'#34443a',mid:'#5f6b63',lo:'#8f9a92',low:'#cfcabd'};
for(var i=0;i<nb;i++){
  var b=confBand(Sb.conf[i]), k=Sb.kv[i]||0;
  var m=L.circleMarker([Sb.lat[i],Sb.lon[i]],{renderer:canvasSubs,radius:k>=345?3.6:(k>=138?2.6:1.9),weight:0.6,color:'rgba(16,35,26,.45)',fillColor:BCOL[b],fillOpacity:b==='low'?0.55:0.9});
  m.bindPopup('<div class="pop"><h3>'+esc(Sb.sub[i])+' <span class="tag">'+k+' kV</span></div><div class="small">'+esc(Sb.county[i]||'')+' County, '+esc(Sb.zone[i]||'')+'<br>Position: match confidence '+Sb.conf[i]+tag('DERIVED')+(Sb.conf[i]<CB[2]?' (below '+CB[2]+': a hint, not a position)':'')+(Sb.rn[i]?'<br>Has a resource node today':'')+'</div>',popOpt);
  subLayers[b].addLayer(m);
}
document.getElementById('n-subs').textContent=nb.toLocaleString('en-US');
function showSubs(){ var on=document.getElementById('l-subs').checked, low=document.getElementById('l-low').checked;
  ['hi','mid','lo'].forEach(function(k){ if(on) map.addLayer(subLayers[k]); else map.removeLayer(subLayers[k]); });
  if(on&&low) map.addLayer(subLayers.low); else map.removeLayer(subLayers.low); }
document.getElementById('l-subs').addEventListener('change',showSubs); document.getElementById('l-low').addEventListener('change',showSubs); showSubs();

// ---------- constraint lines
var CN=D.constraints, consLayer=L.layerGroup(), endsLayer=L.layerGroup(), maxScore=Math.max.apply(null,CN.map(function(c){return c.score;}));
function sideCol(s){ return s==='import'?'#2f6f9f':(s==='export'?'#c7962b':'#fffdf8'); }
function consPopup(c){
  var h='<div class="pop"><h3>#'+c.rank+' '+esc(c.element)+'</h3><div class="small">'+esc(c.type)+(c.kv?', '+c.kv+' kV':'')+' &middot; '+c.n_cid+' contingenc'+(c.n_cid===1?'y':'ies')+'</div><table>';
  h+=tr('Priority score',num(c.score,1),'DERIVED');
  h+=tr('Binding in our week of data',c.binding?('real time '+num(c.rt_h,1)+' h (all contingencies), day-ahead '+num(c.dam_h,0)+' h'):'did not bind','REAL');
  h+=tr('2025 market-monitor rent',c.rent25==null?'n/a':'$'+num(c.rent25,1)+' M','REAL');
  h+=tr('Planned project matched',c.up_id?esc(c.up_id)+', '+esc(c.up_status||'')+', in service '+esc(c.up_date||'?')+' (match '+(c.up_conf==null?'?':c.up_conf)+')':'none found','DERIVED');
  c.ends.forEach(function(e,i){ h+=tr(i?'Other end':'One end',e.sub?esc(e.sub)+(e.sf==null?': no fitted shift factor':': SF '+num(e.sf,2)+' ('+e.side+' side)'):'no placement',e.sf==null?'':'DERIVED'); });
  return h+'</table></div>';
}
CN.forEach(function(c){
  if(!c.drawn) return;
  var pl=L.polyline(c.pts,{pane:'lines',color:c.binding?'#10231a':'#5f6b63',weight:1.5+7*c.score/maxScore,opacity:0.8,dashArray:c.binding?null:'6 5'});
  pl.bindPopup(consPopup(c),popOpt); pl.bindTooltip('#'+c.rank+' '+c.element,{sticky:true}); pl.addTo(consLayer);
  c.ends.forEach(function(e){ if(!e.pt) return; var em=L.circleMarker(e.pt,{pane:'ends',radius:5,weight:1.2,color:'#10231a',fillColor:sideCol(e.side),fillOpacity:e.sf==null?0.5:1}); em.bindPopup(consPopup(c),popOpt); em.addTo(endsLayer); });
});
consLayer.addTo(map); endsLayer.addTo(map);
document.getElementById('n-cons').textContent=CN.filter(function(c){return c.drawn;}).length+' of '+CN.length+' priority elements';
document.getElementById('l-cons').addEventListener('change',function(e){ if(e.target.checked){ map.addLayer(consLayer); map.addLayer(endsLayer);} else { map.removeLayer(consLayer); map.removeLayer(endsLayer);} });

// ---------- upgrades
var UP=D.upgrades, upLayer=L.layerGroup(), notDrawn=[];
function upPopup(u){
  var h='<div class="pop"><h3>'+esc(u.id)+' '+esc(u.element)+'</h3><div class="small">'+esc(u.upgrade_type)+(u.length_mi?', '+num(u.length_mi,1)+' mi':'')+(u.limit_mw?', limit '+num(u.limit_mw,0)+' MW':'')+'</div><table>';
  h+=tr('Wires fix, cost low / mid / high','$'+num(u.cost_low,1)+' / <b>$'+num(u.cost_mid,1)+'</b> / $'+num(u.cost_high,1)+' M (2024 dollars)','DERIVED');
  h+=tr('Lead time (RPG to in service)',num(u.lead_low,0)+' / <b>'+num(u.lead_mid,0)+'</b> / '+num(u.lead_high,0)+' months','DERIVED');
  h+=tr('Planned?',esc(u.planned)+(u.planned_id?': '+esc(u.planned_id)+(u.planned_isd?', '+esc(u.planned_isd):''):''),'DERIVED');
  h+=tr('Battery that relieves 10% of the limit in 80% of binding hours',u.nwa_mw==null?'not sized':num(u.nwa_mw,0)+' MW / '+num(u.nwa_mwh,0)+' MWh ('+num(u.nwa_hours,1)+' h)','DERIVED');
  h+=tr('Battery cost over wires cost',u.nwa_ratio==null?'n/a':num(u.nwa_ratio,1)+'x ('+num(u.nwa_vs_cheapest,0)+'x the cheapest rating fix)','DERIVED');
  h+=tr('2025 market-monitor rent',u.rent25==null?'n/a':'$'+num(u.rent25,1)+' M'+(u.rent_to_cost?' = '+num(u.rent_to_cost,0)+'x a year of the fix':''),'REAL');
  h+='</table>';
  var r=u.rerank;
  if(r){
    h+=sec('If it happens: the 40 sites revalued (full dispatch, 2025 replay)');
    if(r.effect==='not_replayed') h+='<p class="small">The element has no usable shift factor, so it is not in the replayed congestion structure: no site is moved. That is a limit of the data, not evidence that the upgrade does nothing.</p>';
    else { h+='<table>'; r.losers.forEach(function(l){ h+=tr(esc(l[0]),usd(l[1])+' &rarr; '+usd(l[2])+', rank '+l[3]+' &rarr; '+l[4],'SIM'); });
      h+=tr('Sites losing / gaining more than $1',r.n_losers+' / '+r.n_gainers,'SIM')+tr('The 200 MW the caps allow at the nodes',num(r.port_static_pct,1)+'%','SIM')+'</table>'; }
  }
  return h+'</div>';
}
UP.forEach(function(u){
  var badge=function(pt){ return L.marker(pt,{pane:'upg',icon:L.divIcon({className:'',html:'<div class="mk-up">'+u.id+'</div>',iconSize:[34,18],iconAnchor:[17,9]})}); };
  if(u.where==='line'){
    L.polyline(u.pts,{pane:'upg',color:'#f7f4ec',weight:10,opacity:0.9}).addTo(upLayer);
    var pl=L.polyline(u.pts,{pane:'upg',color:'#6b4c9a',weight:5,opacity:0.95,dashArray:'2 8',lineCap:'round'}); pl.bindPopup(upPopup(u),popOpt); pl.addTo(upLayer);
    var mid=[(u.pts[0][0]+u.pts[1][0])/2,(u.pts[0][1]+u.pts[1][1])/2]; badge(mid).bindPopup(upPopup(u),popOpt).addTo(upLayer);
  } else if(u.where==='end'){ badge(u.mark).bindPopup(upPopup(u),popOpt).addTo(upLayer); notDrawn.push(u.id+' '+u.element+': only one end is placed, so the badge sits at that end');
  } else notDrawn.push(u.id+' '+u.element+': '+(u.type==='gtc'?'an interface of many lines, no single place':'neither end is placed'));
});
upLayer.addTo(map);
document.getElementById('n-up').textContent=UP.filter(function(u){return u.where!=='none';}).length+' of '+UP.length;
document.getElementById('l-up').addEventListener('change',function(e){ if(e.target.checked) map.addLayer(upLayer); else map.removeLayer(upLayer); });
var undrawn=CN.filter(function(c){return !c.drawn;}).map(function(c){return '#'+c.rank+' '+c.element;});
document.getElementById('notdrawn-body').innerHTML='<p>'+notDrawn.map(esc).join('<br>')+'</p><p>Priority constraints with no line (an end has no placement or the two ends are over 80 km apart): '+undrawn.map(esc).join(', ')+'.</p>';

// ---------- risk-excluded
var exL=L.layerGroup();
D.excluded.forEach(function(x){
  var m=L.marker([x.lat,x.lon],{pane:'excl',icon:L.divIcon({className:'',html:'<div class="mk-x">&times;</div>',iconSize:[14,14],iconAnchor:[7,7]})});
  m.bindPopup('<div class="pop"><h3>'+esc(x.sub)+' <span class="tag">'+x.kv+' kV</span></h3><div class="small">'+esc(x.county||'')+' County, '+esc(x.zone||'')+'</div><table>'+tr('Excluded because',esc(String(x.reason||'').replace(/_/g,' ')),'DERIVED')+tr('Flood zone',esc(x.flood||'n/a'),'REAL')+tr('Wildfire hazard class',esc(x.wildfire||'n/a'),'REAL')+tr('Placement confidence',x.conf,'DERIVED')+'</table><p class="small">Excluded from the candidate list on physical risk (FEMA special flood hazard area or floodway, or very high wildfire hazard). Siting here is possible only with mitigation.</p></div>',popOpt);
  m.addTo(exL); });
document.getElementById('n-excl').textContent=D.excluded.length;
document.getElementById('l-excl').addEventListener('change',function(e){ if(e.target.checked) map.addLayer(exL); else map.removeLayer(exL); });

// ---------- reliability top 100
var relL=L.layerGroup();
D.reliability.forEach(function(x){
  var m=L.circleMarker([x.lat,x.lon],{pane:'rel',radius:8,weight:2.5,color:'#0f6b6b',fillColor:'#0f6b6b',fillOpacity:0.12});
  m.bindPopup('<div class="pop"><h3>#'+x.rank+' '+esc(x.sub)+' <span class="tag">'+x.kv+' kV</span></h3><div class="small">'+esc(x.county||'')+' County, '+esc(x.zone||'')+'</div><table>'+
    tr('Reliability need score',num(x.score,1)+' of 100','ASSUMPTION')+tr('Main driver',esc(x.driver||'n/a'),'DERIVED')+
    tr('Sub-scores: outage / pocket / facilities / large load',[x.outage,x.pocket,x.facility,x.large_load].map(function(v){return v==null?'-':num(v,2);}).join(' / '),'ASSUMPTION')+
    tr('Behind a bridge in the line graph',yn(x.bridge)+(x.pocket_nodes?' ('+x.pocket_nodes+' substations cut off if the outermost bridge fails)':''),'DERIVED')+
    tr('Hospitals within 10 km',x.hospitals==null?'n/a':x.hospitals,'REAL')+
    tr('Avoided unserved energy, 1 MW / 4 MWh battery',x.eue==null?'n/a (county data unusable)':num(x.eue,1)+' MWh a year','DERIVED')+
    tr('That energy at the duration-adjusted value of lost load',x.voll==null?'n/a':usd(x.voll/1000,0)+'k a year (customer value, not revenue)','DERIVED')+
    '</table><p class="small">Reliability is in its own units. It is never added to market value. The ranking is a screen: the top 20 changes with the weights.</p></div>',popOpt);
  m.addTo(relL); });
document.getElementById('n-rel').textContent=D.reliability.length;
document.getElementById('l-rel').addEventListener('change',function(e){ if(e.target.checked) map.addLayer(relL); else map.removeLayer(relL); });

// ---------- Tier 2 (thinned)
var t2L=L.layerGroup();
D.tier2.forEach(function(x){
  var m=L.marker([x.lat,x.lon],{pane:'tier2',icon:L.divIcon({className:'',html:'<div class="mk-t2" style="'+(x.valued?'background:#1e4d2b':'')+'"></div>',iconSize:[11,11],iconAnchor:[5,5]})});
  m.bindPopup('<div class="pop"><h3>'+esc(x.id)+' <span class="tag">'+(x.kv==null?'?':x.kv)+' kV</span></h3><div class="small">'+esc(x.county||'')+' County, '+esc(x.zone||'')+' &middot; '+(x.src==='osm_only'?'OSM distribution substation':'NP4-160 substation')+'</div>'+
    sec('Market value, 2 h battery (energy only)')+'<table>'+
    tr('DESR route, nodal at '+esc(x.desr_sub||'?'),x.nodal==null?'n/a':usd(x.nodal)+'/kW-yr'+(x.valued?'':' (zone value: not valued)'),'SIM')+
    tr('ADER route, load-zone price ('+esc(x.ader_zone||'?')+')',x.ader==null?'n/a':usd(x.ader)+'/kW-yr','SIM')+
    tr('Gain of the nodal route',x.uplift==null?'n/a':usd(x.uplift),'SIM')+
    (x.valued?tr('Behind',esc(x.element||'')+' ('+esc(x.side||'')+' side)','DERIVED')+tr('Distance to that line',x.dist==null?'its ends are not placed':num(x.dist,0)+' km'+(x.dclass==='beyond_150km'?' (far from a placed end of that line: either a shift factor this large at this distance is unlikely or the line end is mis-placed; treat the value with caution)':''),'DERIVED')+tr('2029, after saturation',usd(x.nodal29),'SIM'):'')+
    tr('Home-fleet value (home spec, energy only; best programme)',(x.home==null?'n/a':usd(x.home)+' merchant')+(x.program&&x.program.indexOf('none')<0?'; '+esc(x.program)+' '+usd(x.program_v,0):''),'SIM')+'</table>'+
    sec('Not added to the above')+'<table>'+tr('Deferral value, if a utility documents a need',usd(x.defer,0)+'/kW-yr','REAL')+
    tr('Reliability need score / rank',x.need==null?'n/a':num(x.need,1)+' / '+(x.rank==null?'n/a':'#'+x.rank),'ASSUMPTION')+
    tr('15% screen, no-study fast path',x.cap==null?'n/a':num(x.cap,2)+' MW','ASSUMPTION')+
    (x.excl?tr('Physical-risk exclusion',esc(String(x.excl).replace(/_/g,' ')),'DERIVED'):'')+'</table>'+
    '<p class="small">A DESR is paid the node price; an ADER fleet is paid the load-zone price and gets none of the nodal gain. '+(x.valued?'Dark diamonds are buses that were valued with the dispatch model.':'This bus was not valued (no usable shift factor, or outside the best 150 of the screen), so the nodal route carries the zone value.')+'</p></div>',popOpt);
  m.addTo(t2L); });
document.getElementById('n-t2').textContent=D.tier2.length;
document.getElementById('l-t2').addEventListener('change',function(e){ if(e.target.checked) map.addLayer(t2L); else map.removeLayer(t2L); });

// ---------- R2: regional candidates (R1's picks, valued like the 40)
var RG=D.regional, rci={}, regL=L.layerGroup(), regMarkers={}; RG.cols.forEach(function(c,i){ rci[c]=i; });
function rg(row,c){ var v=row[rci[c]]; return v===undefined?null:v; }
var rlab={}; RG.cols.forEach(function(c,i){ rlab[c]=RG.labels[i]; });
function regPopup(row){
  var f=function(c){return rg(row,c);};
  var h='<div class="pop"><h3>'+esc(f('sub'))+' <span class="tag">'+f('kv_max')+' kV</span></h3><div class="small">'+esc(f('county'))+' County, '+esc(f('zone'))+' &middot; '+esc(f('region'))+' &middot; picked by R1 for the <b>'+esc(f('region_side'))+'</b> side &middot; price source: '+esc(f('basis_source'))+(f('basis_source')==='interpolated'?' ('+num(f('dist_priced_km'),0)+' km from a priced node)':'')+tag('DERIVED')+'</div>';
  h+=sec('Measured: summer 2026 day-ahead, 1 Jul&ndash;11 Aug (not a model)')+'<table>';
  h+=tr('Node minus load zone: mean / 5th / 95th percentile',num(f('obs_basis_vs_zone_mean_usd_mwh'),2)+' / '+num(f('obs_basis_vs_zone_p05_usd_mwh'),1)+' / '+num(f('obs_basis_vs_zone_p95_usd_mwh'),1)+' $/MWh','REAL');
  h+=tr('2 h day-ahead value, node minus zone, 42 days',usd(f('obs_dam_uplift_42d_usd_kw'),2)+' per kW (not annualised)','DERIVED');
  h+='</table>'+sec('Our model (rebuilt node prices, energy only, after wear)')+'<table>';
  h+=tr('Learner, 2021&ndash;25 mean / 2025',usd(f('h2_mean2125_A5'))+' / '+usd(f('h2_y2025_A5'))+'/kW-yr','SIM');
  h+=tr('Gain over its load zone (2021&ndash;25)',usd(f('h2_mean2125_uplift_usd_kw_yr_A5')),'SIM');
  h+=tr('Learner 2027 / 2031 (after saturation and upgrades)',usd(f('fwd_learner_2027'))+' / '+usd(f('fwd_learner_2031')),'SIM');
  h+=tr('Basis half-life',esc(f('basis_half_life')),'SIM');
  h+='</table>'+sec('Exposed to (our screen)')+'<table>';
  h+=tr('Elements',[1,2,3].filter(function(k){return f('constraint'+k);}).map(function(k){return esc(f('constraint'+k))+' ('+esc(f('constraint'+k+'_side'))+', SF '+num(f('constraint'+k+'_sf'),2)+')';}).join('; ')||'none','DERIVED');
  h+='</table>'+sec('Getting connected, risk, reliability')+'<table>';
  h+=tr('Connection',esc(f('conn_class'))+', lead time '+num(f('conn_lead_months'),1)+' months, all-in '+usd(f('conn_all_in_usd_per_kw'),0)+'/kW','DERIVED');
  h+=tr('Physical risk',['flood '+esc(f('risk_flood')),'wildfire '+esc(f('risk_wildfire_class')),'seismic '+yn(f('risk_seismic')),'excluded '+yn(f('risk_exclude'))].join(', '),'DERIVED');
  h+=tr('Reliability need score / rank',f('reliability_need_score')==null?'not scored':num(f('reliability_need_score'),1)+' / '+(f('reliability_need_rank')==null?'not ranked':'#'+num(f('reliability_need_rank'),0)),'ASSUMPTION');
  h+='</table><p class="small">In this window the Valley and Panhandle sit <b>below</b> their zone (an export discount) and a battery gains nothing from it; the modelled gains here are small or negative, as the measured prices say. Not part of the 40-site ranking or the portfolio.</p>';
  h+='<details><summary>All '+RG.cols.length+' fields of this row</summary><table>';
  RG.cols.forEach(function(c,i){ var v=row[i]; h+='<tr><th>'+esc(c)+'</th><td>'+(v==null?'':esc(typeof v==='number'?(Math.round(v*1000)/1000):v))+tag(rlab[c])+'</td></tr>'; });
  h+='</table></details></div>';
  return h;
}
RG.rows.forEach(function(row){
  var col=RG.colors[rg(row,'region')]||'#555';
  var m=L.marker([rg(row,'lat'),rg(row,'lon')],{pane:'sites',icon:L.divIcon({className:'',html:'<div class="mk-reg" style="background:'+col+'"></div>',iconSize:[14,14],iconAnchor:[7,7]})});
  m.bindPopup(regPopup(row),popOpt); m.bindTooltip(rg(row,'sub')+' ('+rg(row,'region')+', '+rg(row,'region_side')+') '+usd(rg(row,'h2_mean2125_A5'),0),{direction:'top',offset:[0,-6]});
  m.addTo(regL); regMarkers[rg(row,'sub')]=m;
});
regL.addTo(map);
document.getElementById('n-reg').textContent=RG.rows.length;
document.getElementById('l-reg').addEventListener('change',function(e){ if(e.target.checked) map.addLayer(regL); else map.removeLayer(regL); });
var rgl=document.getElementById('reg-legend'); Object.keys(RG.colors).forEach(function(k){ var d=document.createElement('div'); d.className='legend-line'; d.innerHTML='<span class="sw sq" style="background:'+RG.colors[k]+';transform:rotate(45deg) scale(.8)"></span>'+esc(k); rgl.appendChild(d); });

// ---------- R2: measured summer basis
var B=D.basis, bL=L.layerGroup(), BC=B.classes;
var BPOS=['#d7e1ea','#a9c3d9','#6e9bc0','#2f6f9f'], BNEG=['#efe3c4','#e3c987','#d5ac4f','#b57f10'];
function bCls(x){ var a=Math.abs(x); return a<BC[0]?0:(a<BC[1]?1:(a<BC[2]?2:3)); }
function bCol(x){ return x==null?'#cfcabd':(x>=0?BPOS[bCls(x)]:BNEG[bCls(x)]); }
for(var j=0;j<B.sp.length;j++){
  var bm=L.circleMarker([B.lat[j],B.lon[j]],{pane:'lines',renderer:canvasSubs,radius:3.4+0.5*bCls(B.zmean[j]||0),weight:0.7,color:'rgba(16,35,26,.55)',fillColor:bCol(B.zmean[j]),fillOpacity:0.9});
  bm.bindPopup('<div class="pop"><h3>'+esc(B.sp[j])+' <span class="tag">'+(B.kv[j]==null?'?':B.kv[j])+' kV</span></h3><div class="small">'+esc(B.county[j]||'')+' County, '+esc(B.zone[j]||'')+' &middot; '+esc(B.region[j])+' &middot; '+B.n[j]+' hours</div><table>'+
    tr('Node minus load zone: mean',num(B.zmean[j],2)+' $/MWh','REAL')+tr('5th / 95th percentile',num(B.zp05[j],1)+' / '+num(B.zp95[j],1),'REAL')+tr('Hours more than $10 from the zone',num(B.g10[j],0)+' of '+B.n[j],'REAL')+
    tr('Mean at its three worst hours of the day',num(B.worst[j],1)+' $/MWh','REAL')+tr('Node minus system (HB_BUSAVG): mean',num(B.smean[j],2),'REAL')+
    tr('2 h day-ahead value: node / zone / difference',usd(B.vnode[j],2)+' / '+usd(B.vzone[j],2)+' / '+usd(B.upl[j],2)+' per kW over 42 days','DERIVED')+'</table><p class="small">Positive = a premium (price above the zone: import side, a battery sells); negative = a discount (export side). One summer window, day-ahead only.</p></div>',popOpt);
  bm.addTo(bL);
}
document.getElementById('n-basis').textContent=B.sp.length.toLocaleString('en-US');
document.getElementById('l-basis').addEventListener('change',function(e){ if(e.target.checked) map.addLayer(bL); else map.removeLayer(bL); });
var br=document.getElementById('bramp'); BNEG.slice().reverse().concat(BPOS).forEach(function(c){ var s=document.createElement('span'); s.style.background=c; br.appendChild(s); });
document.getElementById('bramp-lab').innerHTML='<span>&lt; &minus;'+BC[2]+'</span><span>&minus;'+BC[1]+'</span><span>&minus;'+BC[0]+'</span><span>0</span><span>+'+BC[0]+'</span><span>+'+BC[1]+'</span><span>&gt; +'+BC[2]+'</span>';
var rs=D.region_summary, rsh='<table>'; rsh+='<tr><th>Region</th><td><b>nodes; mean vs zone; 5th pct; share of node-hours below &minus;$10</b></td></tr>';
rs.forEach(function(r){ rsh+='<tr><th>'+esc(r.region)+'</th><td>'+r.n+'; '+num(r.zone,2)+'; '+num(r.zone_p05,1)+'; '+num(100*(r.neg10||0),1)+'% <span class="tag">REAL</span><br>'+esc(r.verdict||'')+'</td></tr>'; });
document.getElementById('regsum-body').innerHTML=rsh+'</table><p>Mean day-ahead price at the node minus its load zone, $/MWh, 1 Jul&ndash;11 Aug 2026 (the 60-day disclosure). The Valley and Panhandle discounts are on the export side; a battery gains nothing from them in this window (the day-ahead value at the node minus at the zone averages &minus;$0.39 per kW in the Valley and &minus;$0.31 in the Panhandle over the 42 days).</p>';

window.GRID_REPORT_MAP_VIEW={map:map,sites:siteMarkers,regional:regMarkers,layers:{sites:siteLayer,cons:consLayer,ends:endsLayer,upg:upLayer,excl:exL,rel:relL,tier2:t2L,subs:subLayers,regional:regL,basis:bL}};   // handle for tests
}catch(ex){ fail('Map error: '+ex.message); if(window.console) console.error(ex); }
})();
</script>
</body>
</html>
"""


def write_html() -> None:
    html = (HTML.replace("__LEAFLET__", LEAFLET_VERSION).replace("__JS_SRI__", LEAFLET_JS_SRI).replace("__CSS_SRI__", LEAFLET_CSS_SRI).replace("__BUILT__", AS_OF)
            .replace("__CONF_HI__", str(CONF_BANDS[0])).replace("__CONF_MID__", str(CONF_BANDS[1])).replace("__CONF_LO__", str(CONF_BANDS[2])).replace("__SF_SIDE__", f"{SF_SIDE_MIN:.2f}"))
    OUT_HTML.write_text(html, encoding="utf-8", newline="\n")


def write_data(payload: dict) -> None:
    js = json.dumps(_clean(payload), separators=(",", ":"), allow_nan=False)
    OUT_DATA.write_text("window.GRID_REPORT_MAP=" + js + ";\n", encoding="utf-8", newline="\n")


def run(*args) -> dict:
    payload = build_payload()
    write_data(payload)
    write_html()
    size = (OUT_DATA.stat().st_size + OUT_HTML.stat().st_size) / 1e6
    print(f"map: {len(payload['subs']['sub'])} substations, {len(payload['sites']['rows'])} sites, {sum(1 for c in payload['constraints'] if c['drawn'])} drawn of {len(payload['constraints'])} "
          f"constraint elements, {sum(1 for u in payload['upgrades'] if u['where'] != 'none')} of {len(payload['upgrades'])} upgrades, {len(payload['excluded'])} excluded, "
          f"{len(payload['reliability'])} reliability, {len(payload['tier2'])} Tier 2, {len(payload['regional']['rows'])} regional, {len(payload['basis']['sp'])} basis points; {size:.2f} MB -> {OUT_HTML.parent}")
    if size > MAX_MB:
        raise RuntimeError(f"map data {size:.1f} MB exceeds {MAX_MB} MB")
    return payload
