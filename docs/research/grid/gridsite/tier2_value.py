"""Phase 6b: market value of the Tier 2 (distribution) candidates (data/tier2_valued.csv).

Phase 5 listed 2,591 distribution candidates and the settlement routes open to each (data/tier2.csv). Its nodal-value columns were left empty on purpose. This module fills them:

  DESR route (a distribution-connected storage resource): settles at a node, the highest-voltage bus of the substation (own bus) or of the nearest placed substation
      (OSM-only candidates). A settlement bus with a usable shift factor to one of the replayed constraints is screened by the cheap score of Phase 3b
      (sum_k |SF[n,k] - SF_zone[k]| x shadow-price-hours[k], $ per MW-year of price exposure); the best MAX_NODES buses are valued with gridsite.sites.value_sites
      (2 h battery, oracle on the rebuilt 2025 lane x the zone's learner/oracle ratio, then the same with the planned upgrades and storage saturation of 2029).
      Every other candidate gets its zone value, labelled as such.
  ADER route (home-battery fleets): settles at the load-zone price (notes/rules.md (a)), so it gets the zone value, not a node's.

The deferral value (REAL, $43 / $48 / $60 per kW-yr, tier2.csv) and the reliability need score (ASSUMPTION weights, tier2.csv) stay in their own columns; nothing here
adds a market value to a deferral or a VoLL dollar figure.

Run: run_grid.py tier2-value [workers=3] [max-nodes=150] [skip-2029]
"""
from __future__ import annotations

import time

import numpy as np
import pandas as pd

from . import paths
from . import sites as S

REPO_DATA = paths.REPO_DATA
OUT_CSV = REPO_DATA / "tier2_valued.csv"
IN_CSV = REPO_DATA / "tier2.csv"

# =================================================================================================================
# Constants
# =================================================================================================================
SPEC = "grid"                      # REAL: brief: the 2 h battery (arena spec 'grid')
YEAR_NOW = 2025                    # REAL: the replay year of the congestion structure (value as of 2025)
YEAR_FWD = 2029                    # ASSUMPTION: the forward year shown next to it (the year the pocket premium has halved in phase 3b: planned upgrades and storage saturation applied)
MAX_NODES = 150                    # REAL: brief: screen to at most ~150 buses
SF_MIN_USABLE = S.SF_MIN_ELIGIBLE  # ASSUMPTION: a usable |SF| to at least one replayed constraint at least this large (phase 3b: 0.10)
WORKERS_DEFAULT = 3                # REAL: brief: at most 4 workers; about 2 GB of RAM free
SRC_NODAL_OWN = "value_sites_2025_replay_own_ratio"           # DERIVED: node valued; learner ratio from the node's own backtest (a phase 3b candidate)
SRC_NODAL_ZONE_RATIO = "value_sites_2025_replay_zone_ratio"   # DERIVED: node valued; learner ratio of its zone control
SRC_ZONE_NO_SF = "zone_value_no_usable_sf"                    # DERIVED: settlement bus has no shift factor of 0.10 or more to a replayed constraint
SRC_ZONE_CAP = "zone_value_below_screen_cap"                  # DERIVED: usable SF, but not among the best MAX_NODES buses of the screen
SRC_ZONE_NO_ROUTE = "zone_value_no_desr_route"                # DERIVED: no settlement bus for a DESR route
PRICE_TAKER_SHARE = S.PRICE_TAKER_SHARE   # ASSUMPTION: 10% of the dominant element's limit is the room for storage behind it (phase 3b)
FAR_KM = 150.0                     # ASSUMPTION: a settlement bus farther than this from the nearest placed end of its dominant element is flagged: a shift factor of 0.1 or more at that distance is physically unlikely (a day-ahead fit artifact) or an end of the element is mis-placed
EARTH_RADIUS_KM = 6371.0088        # REAL: mean Earth radius
CLASS_NEAR, CLASS_FAR, CLASS_UNPLACED = "within_150km", "beyond_150km", "element_not_placed"   # DERIVED: values of nodal_distance_class


# =================================================================================================================
# Screen
# =================================================================================================================
def _haversine_km(lat1, lon1, lat2, lon2):
    p1, p2 = np.radians(lat1), np.radians(lat2)
    a = np.sin((p2 - p1) / 2) ** 2 + np.cos(p1) * np.cos(p2) * np.sin(np.radians(lon2 - lon1) / 2) ** 2
    return 2 * EARTH_RADIUS_KM * np.arcsin(np.sqrt(a))


def element_end_points() -> dict:
    """{element (constraint_name): [(lat, lon), ...]}: the line-graph nodes of the ends of the element's constraints that have one (phase 2 drops the ends of a pair that
    fails its length test, so an element can have no end)."""
    c = S.read_csv("constraints.csv")
    g = pd.read_parquet(paths.DERIVED / "graph.nodes.parquet").set_index("osm_id")[["lat", "lon"]]
    out = {}
    for el, gr in c.groupby("constraint_name"):
        pts = set()
        for node in pd.concat([gr["from_node"], gr["to_node"]]).dropna():
            if node in g.index:
                pts.add((float(g.at[node, "lat"]), float(g.at[node, "lon"])))
        out[el] = sorted(pts)
    return out


def distance_to_element(subs, elements) -> np.ndarray:
    """Distance in km from each substation to the nearest placed end of its element; NaN if the substation or every end of the element is not placed."""
    sub = S.read_csv("substations.csv").drop_duplicates("sub").set_index("sub")
    ends = element_end_points()
    out = []
    for s_, el in zip(subs, elements):
        pts = ends.get(el) or []
        if not pts or s_ not in sub.index or pd.isna(sub.at[s_, "lat"]):
            out.append(np.nan)
            continue
        la, lo = float(sub.at[s_, "lat"]), float(sub.at[s_, "lon"])
        out.append(min(float(_haversine_km(la, lo, a, b)) for a, b in pts))
    return np.array(out, dtype=float)


def screen(subs: list) -> pd.DataFrame:
    """Per settlement substation: arena zone, largest |SF|, screening score, dominant element and side, the SF to it, the element's limit and price-taker room.
    Substations without an arena zone cannot be valued and are returned with usable = False."""
    from . import nodal_lanes as L

    struct, _, _ = L.mu_tilde()
    zmap = L.zone_of_substations()
    have = [s for s in subs if s in zmap]
    M = L.sub_sf_matrix(have, struct)
    sph = struct["sph_usd_mw_yr"].to_numpy(float)
    contrib = np.abs(M["DSF"]) * sph[None, :]
    el = struct["constraint_name"].to_numpy()
    names = np.unique(el)
    E = np.stack([contrib[:, el == n].sum(1) for n in names], 1)
    dom = E.argmax(1)
    lim = struct.groupby("constraint_name")["median_limit_mw"].median().to_dict()
    cid_dom = []
    for i, n in enumerate(names[dom]):
        ix = np.where(el == n)[0]
        j = ix[np.argmax(contrib[i, ix])]
        cid_dom.append(j)
    cid_dom = np.array(cid_dom)
    out = pd.DataFrame({
        "settle_sub": have, "arena_zone": M["zone"], "max_abs_sf": np.abs(M["SF"]).max(1), "score_usd_mw_yr": contrib.sum(1),
        "dominant_element": names[dom], "dominant_share": E.max(1) / np.maximum(contrib.sum(1), 1e-9),
        "dominant_sf": M["SF"][np.arange(len(have)), cid_dom], "dominant_dsf": M["DSF"][np.arange(len(have)), cid_dom],
        "dominant_sf_method": struct["sf_method"].to_numpy()[cid_dom]})
    out["dominant_side"] = np.where(out["dominant_dsf"] < 0, "import", "export")
    out["dominant_limit_mw"] = out["dominant_element"].map(lim)
    out["group_cap_mw"] = [S.group_cap_units(x) * S.UNIT_MW for x in out["dominant_limit_mw"]]
    out["usable"] = out["max_abs_sf"] >= SF_MIN_USABLE
    out["dist_km"] = distance_to_element(out["settle_sub"], out["dominant_element"])
    out["dist_class"] = np.where(out["dist_km"].isna(), CLASS_UNPLACED, np.where(out["dist_km"] <= FAR_KM, CLASS_NEAR, CLASS_FAR))
    miss = pd.DataFrame({"settle_sub": [s for s in subs if s not in zmap]})
    if len(miss):
        miss["usable"] = False
        out = pd.concat([out, miss], ignore_index=True)
    return out


def value(lanes: list, workers: int, years=(YEAR_NOW, YEAR_FWD)) -> pd.DataFrame:
    """value_sites for the lanes (nodes and zones) in the given years, baseline 'both' (planned upgrades and storage saturation of the year; 1.0 in 2025)."""
    return S.value_sites(lanes, spec=SPEC, years=years, baseline="both", workers=workers)


# =================================================================================================================
# Build
# =================================================================================================================
def build(workers: int = WORKERS_DEFAULT, max_nodes: int = MAX_NODES, with_fwd: bool = True, verbose: bool = True) -> pd.DataFrame:
    from . import nodal_lanes as L

    t0 = time.time()
    t = pd.read_csv(IN_CSV, keep_default_na=False, na_values=[""], low_memory=False)
    subs = sorted(set(t["route_desr_nodal_sub"].dropna()))
    sc = screen(subs).set_index("settle_sub")
    elig = sc[sc["usable"].astype(bool)].sort_values("score_usd_mw_yr", ascending=False)
    sc["screen_rank"] = np.nan
    sc.loc[elig.index, "screen_rank"] = np.arange(1, len(elig) + 1)
    chosen = list(elig.index[:max_nodes])
    if verbose:
        print(f"settlement buses {len(subs)}; usable SF {len(elig)}; screened {len(chosen)} (score cut-off {elig['score_usd_mw_yr'].iloc[len(chosen) - 1]:,.0f} $/MW-yr)", flush=True)
    years = (YEAR_NOW, YEAR_FWD) if with_fwd else (YEAR_NOW,)
    vs = value(list(L.ZONES) + chosen, workers, years)
    if verbose:
        print(f"value_sites: {len(chosen)} nodes x {len(years)} years  {time.time() - t0:.0f}s", flush=True)
    vz = vs[vs["role"] == "control_zone"].set_index(["lane", "year"])
    vn = vs[vs["role"] == "candidate"].set_index(["lane", "year"])
    zone_now = {z: float(vz.at[(z, YEAR_NOW), "learner_usd_kw_yr"]) for z in L.ZONES}
    zone_fwd = {z: float(vz.at[(z, YEAR_FWD), "learner_usd_kw_yr"]) for z in L.ZONES} if with_fwd else {}

    out = t.copy()
    desr = out["route_desr_nodal_sub"]
    node_zone = desr.map(sc["arena_zone"])
    out["nodal_screen_usable_sf"] = desr.map(sc["usable"]).fillna(False).astype(bool)
    out["nodal_screen_max_abs_sf"] = desr.map(sc["max_abs_sf"])
    out["nodal_screen_score_usd_mw_yr"] = desr.map(sc["score_usd_mw_yr"])
    out["nodal_screen_rank"] = desr.map(sc["screen_rank"])
    out["nodal_dominant_element"] = desr.map(sc["dominant_element"])
    out["nodal_dominant_side"] = desr.map(sc["dominant_side"])
    out["nodal_dominant_sf"] = desr.map(sc["dominant_sf"])
    out["nodal_dominant_sf_method"] = desr.map(sc["dominant_sf_method"])
    out["nodal_group_cap_mw"] = desr.map(sc["group_cap_mw"])
    out["nodal_distance_to_element_km"] = desr.map(sc["dist_km"])
    out["nodal_distance_class"] = desr.map(sc["dist_class"])
    out["nodal_zone"] = node_zone
    val, upl, src, ratio_basis, val_f, upl_f = [], [], [], [], [], []
    for sub, z, us, route in zip(desr, node_zone, out["nodal_screen_usable_sf"], out["route_desr_basis"]):
        if not isinstance(sub, str) or not isinstance(z, str):
            val.append(np.nan); upl.append(np.nan); src.append(SRC_ZONE_NO_ROUTE); ratio_basis.append(""); val_f.append(np.nan); upl_f.append(np.nan)
            continue
        if sub in chosen:
            r = vn.loc[(sub, YEAR_NOW)]
            val.append(float(r["learner_usd_kw_yr"])); upl.append(float(r["uplift_learner_usd_kw_yr"]))
            src.append(SRC_NODAL_OWN if r["ratio_basis"] == "own" else SRC_NODAL_ZONE_RATIO); ratio_basis.append(str(r["ratio_basis"]))
            if with_fwd:
                rf = vn.loc[(sub, YEAR_FWD)]
                val_f.append(float(rf["learner_usd_kw_yr"])); upl_f.append(float(rf["uplift_learner_usd_kw_yr"]))
            else:
                val_f.append(np.nan); upl_f.append(np.nan)
        else:
            val.append(zone_now[z]); upl.append(0.0)
            src.append(SRC_ZONE_CAP if us else SRC_ZONE_NO_SF); ratio_basis.append("zone_control")
            val_f.append(zone_fwd.get(z, np.nan)); upl_f.append(0.0 if with_fwd else np.nan)
    out["nodal_value_usd_kw_yr"] = val
    out["nodal_vs_zone_uplift_usd_kw_yr"] = upl
    out["nodal_value_source"] = src
    out["nodal_ratio_basis"] = ratio_basis
    out["nodal_value_2029_usd_kw_yr"] = val_f
    out["nodal_vs_zone_uplift_2029_usd_kw_yr"] = upl_f
    out["nodal_value_p10_usd_kw_yr"] = np.nan          # EMPTY by design: there is no distribution of futures for a rebuilt node lane (the 2029 column is the forward case)
    out["ader_zone_value_usd_kw_yr"] = out["route_ader_zone"].map(zone_now)
    out["desr_minus_ader_usd_kw_yr"] = out["nodal_value_usd_kw_yr"] - out["ader_zone_value_usd_kw_yr"]
    out["nodal_value_screened"] = out["nodal_value_source"].isin([SRC_NODAL_OWN, SRC_NODAL_ZONE_RATIO])
    if verbose:
        print(f"done {time.time() - t0:.0f}s", flush=True)
    return out


# =================================================================================================================
# Labels and writer
# =================================================================================================================
NEW_LABELS = {
    "nodal_value_usd_kw_yr": ("SIM", "gridsite.sites.value_sites; zone control for the rest", "2 h learner value of the DESR route at the settlement bus, $/kW-yr, 2025 replay: the oracle on the rebuilt lane x the zone's learner/oracle ratio. Candidates that were not screened carry their zone value (see nodal_value_source). Energy only, after the $20/MWh wear charge; not added to deferral or reliability dollars"),
    "nodal_value_p10_usd_kw_yr": ("SIM", "none", "EMPTY by design: a rebuilt node lane has no distribution of futures; the forward case is nodal_value_2029_usd_kw_yr"),
    "nodal_vs_zone_uplift_usd_kw_yr": ("SIM", "value_sites: node minus the zone control", "the DESR route's gain over the load-zone price (what an ADER would get); 0 where the zone value is used"),
    "nodal_value_source": ("DERIVED", "this module", "value_sites_2025_replay_own_ratio / _zone_ratio = a node valued; zone_value_below_screen_cap = usable SF but outside the best MAX_NODES; zone_value_no_usable_sf = no |SF| of 0.10 or more to a replayed constraint; zone_value_no_desr_route"),
    "nodal_screen_usable_sf": ("DERIVED", "gridsite.nodal_lanes.sub_sf_matrix", "the settlement bus has |SF| >= 0.10 to at least one of the 260 replayed constraints (rt_reg, or non-attenuated dam_reg)"),
    "nodal_screen_max_abs_sf": ("DERIVED", "data/shift_factors logic (sf_sub)", "largest |SF| of the settlement bus to a replayed constraint, capped at 1"),
    "nodal_screen_score_usd_mw_yr": ("DERIVED", "sum_k |SF - SF_zone| x shadow-price-hours", "the cheap screening score of phase 3b, $ per MW-year of price exposure; not a value"),
    "nodal_screen_rank": ("DERIVED", "this module", "rank of the settlement bus among the buses with a usable SF (1 = best score); the best 150 are valued"),
    "nodal_dominant_element": ("DERIVED", "this module", "the replayed element that contributes most to the screening score"),
    "nodal_dominant_side": ("DERIVED", "sign of SF - SF_zone", "import = the node price rises above the zone when the element binds (a battery sells into it); export = it falls"),
    "nodal_dominant_sf": ("DERIVED", "phase 2 sf_sub", "SF of the settlement bus to the dominant element's main contingency (PLAN.md sign convention: SF < 0 = import side)"),
    "nodal_dominant_sf_method": ("DERIVED", "phase 2", "rt_reg or dam_reg (day-ahead fits are the weaker ones; Twin Buttes-Hargrove is one)"),
    "nodal_group_cap_mw": ("ASSUMPTION", "10% of the element's median limit, whole 10 MW units (phase 3b)", "MW of storage the whole pocket behind this element can hold before the premium is eroded (shared with Tier 1 and with every other Tier 2 battery behind the element)"),
    "nodal_zone": ("DERIVED", "gridsite.nodal_lanes.zone_of_substations", "arena zone the settlement bus is measured against (NOIE zone first)"),
    "nodal_distance_to_element_km": ("DERIVED", "data/substations.csv and the line graph (phase 1c / 2)", "great-circle distance from the settlement bus to the nearest placed end of its dominant element; blank if the bus or every end of the element is not placed. Coordinates of substations with match confidence below 0.5 are hints"),
    "nodal_distance_class": ("DERIVED", "this module, FAR_KM = 150 km [ASSUMPTION]", "within_150km, beyond_150km (a shift factor of 0.1 or more at that distance is physically unlikely: a day-ahead fit artifact, or an end of the element is mis-placed) or element_not_placed; a caution on the value, not a correction"),
    "nodal_ratio_basis": ("DERIVED", "gridsite.sites.value_sites", "own = the learner/oracle ratio of the node's own 2021-25 backtest (a phase 3b candidate); zone_control = the zone's ratio"),
    "nodal_value_2029_usd_kw_yr": ("SIM", "gridsite.sites.value_sites, baseline both, 2029", "the same value with the planned upgrades and the storage that arrives by 2029 (phase 3b saturation); zone value for buses that were not valued"),
    "nodal_vs_zone_uplift_2029_usd_kw_yr": ("SIM", "value_sites", "the DESR route's gain over the zone in 2029"),
    "ader_zone_value_usd_kw_yr": ("SIM", "gridsite.sites.value_sites zone control", "the same 2 h battery at the load-zone price of route_ader_zone: what an ADER fleet settles at (notes/rules.md (a)); the home-spec fleet value is home_fleet_merchant_usd_kw_yr"),
    "desr_minus_ader_usd_kw_yr": ("SIM", "nodal_value - ader_zone_value", "what the nodal route adds to the zone route for the same battery"),
    "nodal_value_screened": ("DERIVED", "this module", "True if the node was among the best MAX_NODES buses and valued with value_sites"),
}


def write(df: pd.DataFrame) -> None:
    old = pd.read_csv(paths.REPO_DATA / "tier2.labels.csv", keep_default_na=False)
    base = {r.column: (r.label, r.source, r.note) for r in old.itertuples()}
    rows = []
    for c in df.columns:
        if c in NEW_LABELS:
            lab, src, note = NEW_LABELS[c]
        elif c in base:
            lab, src, note = base[c]
        else:
            raise KeyError(f"no label for {c}")
        rows.append({"column": c, "label": lab, "source": src, "note": note})
    out = df.copy()
    num = out.select_dtypes("float").columns
    out[num] = out[num].round(4)
    out.to_csv(OUT_CSV, index=False)
    pd.DataFrame(rows).to_csv(str(OUT_CSV).replace(".csv", ".labels.csv"), index=False)


def run(*args) -> pd.DataFrame:
    kv, fl = {}, set()
    for a in args:
        if "=" in a:
            k, v = a.split("=", 1)
            kv[k.strip().replace("-", "_")] = v.strip()
        else:
            fl.add(a.strip())
    df = build(workers=int(kv.get("workers", WORKERS_DEFAULT)), max_nodes=int(kv.get("max_nodes", MAX_NODES)), with_fwd="skip-2029" not in fl)
    write(df)
    scr = df[df["nodal_value_screened"]]
    print(f"wrote {OUT_CSV} ({len(df)} rows; {int(df['nodal_value_screened'].sum())} candidates at {scr['route_desr_nodal_sub'].nunique()} valued buses)")
    print(df["nodal_value_source"].value_counts().to_string())
    return df
