"""Phase 5c: Tier 2 (distribution-connected batteries under 10 MW and home-battery fleets) -> data/tier2.csv (+ labels sidecar).

  run_grid.py tier2

One row per candidate distribution substation: where each connection type settles (notes/rules.md), the reliability score and
Oncor feeder statistics where they join, a hosting screen (16 TAC 25.211 and the Austin Energy 90% rule where inputs exist,
named ASSUMPTION defaults otherwise), the typical deferral value, and the arena's home-fleet value for the zone.
The nodal-value columns are empty on purpose: Phase 6 fills them from Phase 3's data/sites.csv.
Run `reliability` and `risk` first (tier2 reads data/reliability.csv and data/risk_flags.csv).

How sim/planner.py scales to a real feeder is written up in notes/phase5.md (section "From transformer to feeder").
"""
import json
import re

import numpy as np
import pandas as pd

from .paths import REPO_DATA, ARENA_DIR
from . import reliability as rel
from .reliability import (MIN_CONF, combine_scores, haversine_km, load_subs, unit_xyz, chord_km, write_labels)

# ====================================================================================================== constants
CAND_MAX_KV = 138.0                 # REAL: the task's cut: NP4-160 subs with kv_max <= 138 kV serve distribution
DIST_VOLTAGE_TAGS = {"distribution", "minor_distribution"}   # REAL: OSM substation=* values
LOW_BUS_MAX_KV = 34.5               # ASSUMPTION: a non-generator bus at or below 34.5 kV in NP4-160 means a distribution transformer secondary
OSM_ONLY_MAX_KM = 15.0              # ASSUMPTION: an OSM-only distribution substation must lie within this distance of a placed ERCOT sub to get a settlement bus
LOAD_CENTRE_MIN_FACILITIES = 10.0   # ASSUMPTION: weighted critical-facility count (hospital 5, water 3, emergency 1) that marks a load centre for OSM-only candidates
NEAREST_TX_MIN_KV = 69.0            # REAL: ERCOT treats 60 kV and above as transmission; NP4-160 buses start at 69 kV
CAND_MIN_KV = 60.0                  # ASSUMPTION: a sub whose highest bus is below 60 kV is a generator or DC-tie station (e.g. LARDVFT, 17.5 kV), not a load substation

# --- deferral value (REAL: docs/research/ercot.md section 5.3, citing the 25-study NWA review, PA PUC 2026)
DEFERRAL_USD_KW_YR_LOW = 43.0       # REAL: typical distribution deferral value, low end
DEFERRAL_USD_KW_YR_MID = 48.0       # REAL: 25-study average
DEFERRAL_USD_KW_YR_HIGH = 60.0      # REAL: typical high end (the best sites are worth far more)

# --- hosting screen
CORE_KW = 20.0                      # REAL: Base Core battery, 20 kW (sim/planner.py CORE_POWER_KW; DATA-INTERCONNECTION.md)
SCREEN_FEEDER_SHARE = 0.15          # REAL: 16 TAC 25.211(g)(1): export of at most 15% of feeder load gets the no-study-fee fast path
SCREEN_FAULT_SHARE = 0.25           # REAL: 16 TAC 25.211(g)(1): at most 25% of fault current
SCREEN_SHARE_NAMEPLATE = 1.0        # REAL: sim/planner.py PLAN_SCREEN_SHARE_NAMEPLATE (TDSP practice, PUCT 54233 items 85, 92)
SCREEN_SHARE_AE90 = 0.9             # REAL: Austin Energy DG guide rev 14 p.13: all DG kW AC at most 90% of transformer rating
TRANSFORMER_KVA = (25, 50, 75)      # REAL: common overhead single-phase sizes (NREL cost database rows; DOE rule representative unit)
FEEDER_KW_PER_CUSTOMER = 5.0        # ASSUMPTION: coincident feeder peak per customer on a mixed residential/commercial Texas feeder (bracket 3-8)
DEFAULT_FAULT_SCREEN_BINDING = False   # ASSUMPTION: no fault-current data is public; a <1 MW battery adds about 1.1-1.2 x rated current, so the 25% test is assumed not to bind
DEFAULT_REVERSE_FLOW_BINDING = False   # ASSUMPTION: substation-transformer reverse-flow limit not evaluated (no transformer MVA or minimum-load data)
TIER2_MAX_MW = 10.0                 # REAL: PROMPT scope, Tier 2 is under 10 MW

HOME_ZONE_NAMES = {                  # REAL: arena region keys (arena/config.py) for each settlement zone
    "LZ_NORTH": "ERCOT:LZ_NORTH", "LZ_HOUSTON": "ERCOT:LZ_HOUSTON", "LZ_SOUTH": "ERCOT:LZ_SOUTH", "LZ_WEST": "ERCOT:LZ_WEST",
    "LZ_AEN": "ERCOT:LZ_AEN", "LZ_CPS": "ERCOT:LZ_CPS", "LZ_LCRA": "ERCOT:LZ_LCRA", "LZ_RAYBN": "ERCOT:LZ_RAYBN",
}
ARENA_RESULTS = ARENA_DIR / "results" / "arena_results.json"


# ====================================================================================================== inputs
def home_fleet_values():
    """Arena forward tournament, A5 dispatch, median 2027-29 and P10, $/kW-yr, by zone. Parsed from arena_results.json (SIM)."""
    j = json.loads(ARENA_RESULTS.read_text(encoding="utf-8"))
    rank = j["forward"]["markets"]["ERCOT"]["ranking"]["A5"]
    out = {}
    for name, med, p10 in rank:
        m = re.fullmatch(r"ERCOT:(LZ_\w+)(?: \[(.+)\])?", name)
        z, prog = m.group(1), m.group(2)
        d = out.setdefault(z, {"programs": []})
        if prog is None:
            d["merchant"] = (med / 1000.0, p10 / 1000.0)
        else:
            d["programs"].append((prog, med / 1000.0, p10 / 1000.0))
    for z, d in out.items():
        d["programs"].sort(key=lambda x: -x[1])
    return out


def _hv_bus_table():
    sp = pd.read_csv(REPO_DATA / "settlement_points.csv", dtype=str, keep_default_na=False, na_values=[""])
    sp["kv"] = pd.to_numeric(sp.VOLTAGE_LEVEL, errors="coerce")
    top = sp.sort_values(["SUBSTATION", "kv"], ascending=[True, False]).drop_duplicates("SUBSTATION")
    top = top.set_index("SUBSTATION")[["ELECTRICAL_BUS", "kv", "RESOURCE_NODE"]].rename(
        columns={"ELECTRICAL_BUS": "hv_bus", "kv": "hv_bus_kv", "RESOURCE_NODE": "hv_resource_node"})
    sp["low_non_gen"] = (sp.kv <= LOW_BUS_MAX_KV) & sp.RESOURCE_NODE.isna()
    sp["l_prefix"] = sp.PSSE_BUS_NAME.str.startswith("L_", na=False)
    flags = sp.groupby("SUBSTATION")[["low_non_gen", "l_prefix"]].any()
    return top.join(flags)


def _hosting(df, onc_defaults):
    """Hosting screen columns, in place. Inputs that do not exist get the named ASSUMPTION defaults and say so in hosting_basis."""
    feeders = df.oncor_feeders.copy()
    cust = df.oncor_customers / df.oncor_feeders
    has = feeders.notna()
    df["hosting_feeders"] = feeders.fillna(onc_defaults["feeders_per_sub"])
    df["feeder_customers_avg"] = cust.fillna(onc_defaults["customers_per_feeder"])
    df["hosting_basis"] = np.where(has, "oncor_sqr_feeders", "default_feeders_ASSUMPTION")
    df["feeder_peak_mw_est"] = df.feeder_customers_avg * FEEDER_KW_PER_CUSTOMER / 1000.0
    df["screen_15pct_mw_per_feeder"] = SCREEN_FEEDER_SHARE * df.feeder_peak_mw_est
    df["screen_15pct_mw_substation"] = df.hosting_feeders * df.screen_15pct_mw_per_feeder
    df["screen_fast_path_cap_mw"] = np.minimum(TIER2_MAX_MW, df.screen_15pct_mw_substation)
    df["screen_fault_current_25pct"] = np.where(DEFAULT_FAULT_SCREEN_BINDING, "binding_ASSUMPTION", "not_binding_ASSUMPTION")
    df["screen_reverse_flow"] = np.where(DEFAULT_REVERSE_FLOW_BINDING, "binding_ASSUMPTION", "not_evaluated_ASSUMPTION")
    ae = df.settlement_zone.eq("LZ_AEN")
    df["screen_transformer_rule"] = np.where(ae, "ae90", "nameplate100")
    share = np.where(ae, SCREEN_SHARE_AE90, SCREEN_SHARE_NAMEPLATE)
    for kva in TRANSFORMER_KVA:
        df[f"cores_per_{kva}kva"] = np.floor(share * kva / CORE_KW + 1e-9).astype(int)


# ====================================================================================================== build
def build_tier2():
    subs = load_subs()
    hv = _hv_bus_table()
    relf = pd.read_csv(REPO_DATA / "reliability.csv", low_memory=False, keep_default_na=False, na_values=[""])
    risk_path = REPO_DATA / "risk_flags.csv"
    risk = pd.read_csv(risk_path, low_memory=False, keep_default_na=False, na_values=[""]) if risk_path.exists() else None
    eag = pd.read_csv(REPO_DATA / "eaglei_county.csv", dtype={"fips": str})
    osm = pd.read_csv(REPO_DATA / "osm_substations.csv")

    s = subs.join(hv, on="sub")
    osm_dist_ids = set(osm.loc[osm.sub_type.isin(DIST_VOLTAGE_TAGS), "osm_id"])
    s["osm_dist"] = s.osm_id.isin(osm_dist_ids)
    s["load_evidence"] = (np.where(s.l_prefix.fillna(False), "psse_L_bus;", "") + np.where(s.low_non_gen.fillna(False), "low_kv_bus;", "")
                          + np.where(s.osm_dist, "osm_distribution;", ""))
    gen_only = s.has_resource_node & ~s.l_prefix.fillna(False) & ~s.osm_dist       # a 34.5 kV bus at a generating site is a collector bus, not a feeder
    s.loc[gen_only, "load_evidence"] = ""
    c = s[(s.kv_max <= CAND_MAX_KV) & (s.kv_max >= CAND_MIN_KV) & s.load_evidence.ne("")].copy()
    c["candidate_source"] = "np4160"
    c["cand_id"] = c["sub"]
    c["osm_name"] = c.name_guess
    c["settlement_zone"] = c.noie_zone.fillna(c.zone)

    # placed transmission-class ERCOT subs for the nearest-bus search
    tx = s[(s.match_conf >= MIN_CONF) & s.lat.notna() & (s.kv_max >= NEAREST_TX_MIN_KV)].copy()
    from scipy.spatial import cKDTree
    tree = cKDTree(unit_xyz(tx.lat.values, tx.lon.values))
    km1 = chord_km(1.0)

    # --- OSM-only distribution substations near load centres
    linked = set(s.osm_id.dropna()) | set(osm.loc[osm.ercot_sub.notna(), "osm_id"])
    o = osm[osm.sub_type.isin(DIST_VOLTAGE_TAGS) & ~osm.osm_id.isin(linked) & osm.lat.notna()].copy()
    fac = rel._dedupe(rel._load_facilities(), rel.FAC_DEDUPE_M)
    wcount = np.zeros(len(o))
    pts = unit_xyz(o.lat.values, o.lon.values)
    for kind, rad in (("hospital", rel.RADIUS_HOSPITAL_KM), ("water", rel.RADIUS_WATER_KM), ("emergency", rel.RADIUS_EMERGENCY_KM)):
        f = fac[fac.kind == kind]
        t = cKDTree(unit_xyz(f.lat.values, f.lon.values))
        wcount += rel.FACILITY_WEIGHTS[kind] * np.array([len(x) for x in t.query_ball_point(pts, chord_km(rad))])
    o["facility_weighted_count"] = wcount
    d, i = tree.query(pts)
    o["route_desr_nodal_dist_km"] = d / km1
    o = o[(o.facility_weighted_count >= LOAD_CENTRE_MIN_FACILITIES) & (o.route_desr_nodal_dist_km <= OSM_ONLY_MAX_KM)].copy()
    near = tx.iloc[tree.query(unit_xyz(o.lat.values, o.lon.values))[1]]
    o["near_sub"] = near["sub"].values
    o["zone"] = near["zone"].values
    o["noie_zone"] = near["noie_zone"].values
    o["settlement_zone"] = o.noie_zone.fillna(o.zone)
    o["candidate_source"] = "osm_only"
    o["cand_id"] = o.osm_id
    o["sub"] = ""
    o["kv_max"] = o.voltage_kv
    o["match_conf"] = np.nan
    o["load_evidence"] = "osm_distribution_near_load_centre;"
    o["osm_name"] = o["name"]
    o["county"] = o.county.str.upper()

    # --- common columns
    keep = ["cand_id", "candidate_source", "sub", "osm_id", "osm_name", "county", "zone", "noie_zone", "settlement_zone", "kv_max",
            "lat", "lon", "match_conf", "load_evidence", "has_resource_node"]
    for df_ in (c, o):
        for k in keep:
            if k not in df_:
                df_[k] = np.nan
    t2 = pd.concat([c[keep], o[keep + []]], ignore_index=True)
    t2["has_resource_node"] = t2.has_resource_node.astype(object)
    t2["site_level"] = (t2.match_conf >= MIN_CONF) & t2.lat.notna()

    # --- settlement routes (rules.md section a)
    t2 = t2.join(hv[["hv_bus", "hv_bus_kv", "hv_resource_node"]], on="sub")
    own_ok = t2.hv_bus.notna() & (t2.hv_bus_kv >= NEAREST_TX_MIN_KV)
    d, i = tree.query(unit_xyz(t2.lat.fillna(31).values, t2.lon.fillna(-98).values))
    nearest_sub = tx["sub"].values[i]
    nearest_dist = np.where(t2.lat.notna(), d / km1, np.nan)
    t2["route_desr_nodal_sub"] = np.where(own_ok, t2["sub"], np.where(t2.lat.notna() & (nearest_dist <= OSM_ONLY_MAX_KM), nearest_sub, ""))
    t2["route_desr_nodal_dist_km"] = np.where(own_ok, 0.0, np.where(t2.lat.notna(), np.round(nearest_dist, 2), np.nan))
    bus = hv.hv_bus.reindex(t2.route_desr_nodal_sub.values).values
    kvb = hv.hv_bus_kv.reindex(t2.route_desr_nodal_sub.values).values
    t2["route_desr_nodal_bus"] = bus
    t2["route_desr_nodal_kv"] = kvb
    t2["route_desr_basis"] = np.where(own_ok, "own_hv_bus", np.where(t2.route_desr_nodal_sub.ne(""), "nearest_placed_sub", "none"))
    t2["route_ader_zone"] = t2.settlement_zone
    t2["route_sodg_storage_zone"] = t2.settlement_zone
    t2["settlement_options"] = np.where(t2.route_desr_nodal_sub.ne(""), "DESR_nodal|ADER_zone|SODG_storage_zone", "ADER_zone|SODG_storage_zone")

    # --- reliability and Oncor (NP4-160 rows from reliability.csv; OSM-only rows from the county and facility components)
    rc = ["sub", "reliability_need_score", "need_rank", "main_driver", "outage_score", "pocket_score", "facility_score", "oncor_score", "large_load_score",
          "paths_345", "behind_bridge", "pocket_outer_nodes", "oncor_feeders", "oncor_customers", "oncor_saifi", "oncor_saidi_min", "oncor_saidi_max",
          "eue_avoided_mwh_yr_full", "eue_avoided_mwh_yr_lowsoc", "voll_usd_yr_headline", "voll_usd_yr_duration"]
    t2 = t2.merge(relf[rc], on="sub", how="left")
    isosm = t2.candidate_source.eq("osm_only")
    cty = eag.set_index("county")
    for col in ["eue_avoided_mwh_yr_full", "eue_avoided_mwh_yr_lowsoc", "voll_usd_yr_headline", "voll_usd_yr_duration"]:
        t2.loc[isosm, col] = t2.loc[isosm, "county"].map(cty[col])
    t2.loc[isosm, "outage_score"] = t2.loc[isosm, "county"].map(cty.outage_score)
    fac_sorted = np.sort(pd.read_csv(REPO_DATA / "critical_facilities.csv", keep_default_na=False, na_values=[""]).facility_weighted_count.values)
    wc = pd.Series(np.nan, index=t2.index)
    wc[isosm] = o.set_index("osm_id").facility_weighted_count.reindex(t2.loc[isosm, "osm_id"]).values
    t2.loc[isosm, "facility_score"] = np.searchsorted(fac_sorted, wc[isosm].values, side="right") / len(fac_sorted)   # same percentile scale as reliability.py
    combine_scores(relf[relf.rank_eligible.astype(str) == "True"])          # population baseline for main_driver
    sc, nc, drv = combine_scores(t2, combine_scores.baseline)
    t2.loc[isosm, "reliability_need_score"] = sc[isosm.values]
    t2.loc[isosm, "main_driver"] = drv[isosm.values]
    t2["reliability_basis"] = np.where(isosm, "county_outage+facilities_only", "reliability.csv")

    # --- risk flags
    if risk is not None:
        t2 = t2.merge(risk[["sub", "flood_flag", "wildfire_class", "coastal_flag", "seismic_flag", "exclude_flag", "exclude_reason"]], on="sub", how="left")
    else:
        for k in ["flood_flag", "wildfire_class", "coastal_flag", "seismic_flag", "exclude_flag", "exclude_reason"]:
            t2[k] = np.nan

    # --- hosting screen
    g, f, _ = rel.build_oncor()
    defaults = dict(feeders_per_sub=float(g.oncor_feeders.median()), customers_per_feeder=float(f.customers.median()))
    _hosting(t2, defaults)

    # --- deferral value (typical NWA range; applies only where a utility documents a need)
    t2["deferral_usd_kw_yr_low"] = DEFERRAL_USD_KW_YR_LOW
    t2["deferral_usd_kw_yr_mid"] = DEFERRAL_USD_KW_YR_MID
    t2["deferral_usd_kw_yr_high"] = DEFERRAL_USD_KW_YR_HIGH

    # --- home-fleet value for the zone (arena forward tournament, A5)
    hf = home_fleet_values()
    z = t2.settlement_zone
    t2["home_fleet_merchant_usd_kw_yr"] = z.map(lambda k: hf.get(k, {}).get("merchant", (np.nan, np.nan))[0])
    t2["home_fleet_merchant_p10_usd_kw_yr"] = z.map(lambda k: hf.get(k, {}).get("merchant", (np.nan, np.nan))[1])
    t2["home_fleet_best_program"] = z.map(lambda k: hf.get(k, {}).get("programs", [("", np.nan, np.nan)])[0][0] if hf.get(k, {}).get("programs") else "")
    t2["home_fleet_best_program_usd_kw_yr"] = z.map(lambda k: hf.get(k, {}).get("programs", [("", np.nan, np.nan)])[0][1] if hf.get(k, {}).get("programs") else np.nan)
    t2["home_fleet_best_program_p10_usd_kw_yr"] = z.map(lambda k: hf.get(k, {}).get("programs", [("", np.nan, np.nan)])[0][2] if hf.get(k, {}).get("programs") else np.nan)
    t2["home_fleet_programs"] = z.map(lambda k: ";".join(f"{p}={m:.1f}" for p, m, _ in hf.get(k, {}).get("programs", [])))
    for col in ("home_fleet_best_program", "home_fleet_programs"):
        t2[col] = t2[col].replace("", "none modelled in the arena")

    # --- nodal-value columns: empty on purpose (Phase 6 fills them from Phase 3's data/sites.csv)
    for col in ["nodal_value_usd_kw_yr", "nodal_value_p10_usd_kw_yr", "nodal_vs_zone_uplift_usd_kw_yr", "nodal_value_source"]:
        t2[col] = np.nan

    num = t2.select_dtypes("number").columns
    t2[num] = t2[num].round(4)
    t2 = t2.drop(columns=[c_ for c_ in ["hv_bus", "hv_bus_kv", "hv_resource_node"] if c_ in t2], errors="ignore")
    t2.to_csv(REPO_DATA / "tier2.csv", index=False)
    write_labels("tier2", T2_LABELS)
    summ = dict(rows=len(t2), by_source=t2.candidate_source.value_counts().to_dict(),
                site_level=int(t2.site_level.sum()), route_basis=t2.route_desr_basis.value_counts().to_dict(),
                settlement_zone=t2.settlement_zone.value_counts().to_dict(),
                hosting_basis=t2.hosting_basis.value_counts().to_dict(),
                options=t2.settlement_options.value_counts().to_dict(),
                oncor_joined=int(t2.oncor_feeders.notna().sum()), defaults=defaults,
                exclude=t2.exclude_reason.value_counts(dropna=False).to_dict())
    print(json.dumps(summ, indent=1, default=str), flush=True)
    return t2


T2_LABELS = [
    ("cand_id", "DERIVED", "tier2.py", "NP4-160 SUBSTATION code, or the OSM id for OSM-only candidates"),
    ("candidate_source", "DERIVED", "tier2.py", "np4160 = NP4-160 sub at 138 kV or less with load evidence; osm_only = OSM distribution substation near a load centre with no ERCOT match"),
    ("sub", "REAL", "ERCOT NP4-160", "blank for OSM-only candidates"),
    ("osm_id", "REAL", "OpenStreetMap", ""),
    ("osm_name", "REAL", "OpenStreetMap / data/substations.csv name_guess", ""),
    ("county", "DERIVED", "Census TIGER via data/substations.csv", ""),
    ("zone", "REAL", "ERCOT NP4-160 SETTLEMENT_LOAD_ZONE", "the four competitive zones"),
    ("noie_zone", "REAL", "ERCOT NP4-160 NOIE_ZONE", "LZ_AEN, LZ_CPS, LZ_LCRA, LZ_RAYBN where the bus belongs to a municipal or co-op"),
    ("settlement_zone", "DERIVED", "noie_zone if present else zone", "the zone price an ADER fleet or storage-dominated settlement-only site is paid"),
    ("kv_max", "REAL", "ERCOT NP4-160 / OSM", "highest bus voltage in NP4-160"),
    ("lat", "DERIVED", "data/substations.csv / OSM", ""), ("lon", "DERIVED", "data/substations.csv / OSM", ""),
    ("match_conf", "DERIVED", "data/substations.csv", "blank for OSM-only"),
    ("load_evidence", "ASSUMPTION", "NP4-160 bus names and voltages; OSM substation=distribution", "NP4-160 has no load flag: a sub counts as load-serving if it has an L_ bus, a non-generator bus at 34.5 kV or less, or an OSM distribution match"),
    ("has_resource_node", "REAL", "ERCOT NP4-160", "a generation or storage resource node already sits at the sub"),
    ("site_level", "DERIVED", "tier2.py", "match_conf >= 0.5 and a coordinate"),
    ("route_desr_nodal_sub", "DERIVED", "notes/rules.md (a): 16 TAC 25.501(m)(2); NP 3.8.6", "substation whose highest-voltage bus is the nearest transmission bus; a DESR settles nodal there. UNVERIFIED which bus a feeder maps to: it is in the DESR's registration data"),
    ("route_desr_nodal_bus", "REAL", "ERCOT NP4-160 ELECTRICAL_BUS", "highest-voltage bus of that substation"),
    ("route_desr_nodal_kv", "REAL", "ERCOT NP4-160", ""),
    ("route_desr_nodal_dist_km", "DERIVED", "haversine", "0 when the candidate's own high-side bus is used"),
    ("route_desr_basis", "DERIVED", "tier2.py", "own_hv_bus, nearest_placed_sub (within 15 km), or none"),
    ("route_ader_zone", "DERIVED", "notes/rules.md (a): ADER Governing Document Phase 3.3", "home-battery fleets settle at the load-zone price: no nodal congestion value"),
    ("route_sodg_storage_zone", "DERIVED", "notes/rules.md (a): NP 6.6.3.8", "settlement-only sites of 10 MW or less with more than 50% storage settle at the load zone today (NPRR995 would make them nodal)"),
    ("settlement_options", "DERIVED", "tier2.py", "which routes exist for this candidate"),
    ("reliability_need_score", "ASSUMPTION", "data/reliability.csv (OSM-only rows: county outage + facilities)", "0-100; weights in reliability.py"),
    ("need_rank", "DERIVED", "data/reliability.csv", "blank for OSM-only"),
    ("main_driver", "DERIVED", "reliability.py", ""),
    ("outage_score", "ASSUMPTION", "reliability.py", ""), ("pocket_score", "ASSUMPTION", "reliability.py", ""),
    ("facility_score", "ASSUMPTION", "reliability.py", ""), ("oncor_score", "ASSUMPTION", "reliability.py", ""),
    ("large_load_score", "ASSUMPTION", "reliability.py", ""),
    ("paths_345", "DERIVED", "reliability.py", ""), ("behind_bridge", "DERIVED", "reliability.py", ""), ("pocket_outer_nodes", "DERIVED", "reliability.py", ""),
    ("oncor_feeders", "REAL", "Oncor 2023 PUCT Service Quality Report", "exact substation-code join; Oncor substations only"),
    ("oncor_customers", "REAL", "same", ""), ("oncor_saifi", "DERIVED", "same", "customer-weighted, forced interruptions"),
    ("oncor_saidi_min", "DERIVED", "same", ""), ("oncor_saidi_max", "REAL", "same", ""),
    ("eue_avoided_mwh_yr_full", "DERIVED", "eaglei_county.csv", "county-level, 1 MW / 4 MWh battery"),
    ("eue_avoided_mwh_yr_lowsoc", "DERIVED", "eaglei_county.csv", ""),
    ("voll_usd_yr_headline", "DERIVED", "EUE x $35,685/MWh (Brattle, PUCT 55837)", "reliability value: kept apart from market value"),
    ("voll_usd_yr_duration", "DERIVED", "EUE x Brattle duration table", ""),
    ("reliability_basis", "DERIVED", "tier2.py", ""),
    ("flood_flag", "DERIVED", "data/risk_flags.csv", ""), ("wildfire_class", "DERIVED", "data/risk_flags.csv", ""),
    ("coastal_flag", "DERIVED", "data/risk_flags.csv", ""), ("seismic_flag", "DERIVED", "data/risk_flags.csv", ""),
    ("exclude_flag", "DERIVED", "data/risk_flags.csv", ""), ("exclude_reason", "DERIVED", "data/risk_flags.csv", ""),
    ("hosting_feeders", "DERIVED", "Oncor SQR feeders at the sub, else the SQR median per substation", "ASSUMPTION default when the sub is not an Oncor sub"),
    ("feeder_customers_avg", "DERIVED", "Oncor SQR customers per feeder, else the SQR median", ""),
    ("hosting_basis", "DERIVED", "tier2.py", "oncor_sqr_feeders or default_feeders_ASSUMPTION"),
    ("feeder_peak_mw_est", "ASSUMPTION", "customers x FEEDER_KW_PER_CUSTOMER (5 kW)", "no Texas utility publishes feeder loading (DATA-GRID-ASSETS.md); scales linearly with the constant"),
    ("screen_15pct_mw_per_feeder", "DERIVED", "16 TAC 25.211(g)(1): 15% of feeder load", "the rule is REAL, the feeder peak is ASSUMPTION"),
    ("screen_15pct_mw_substation", "DERIVED", "feeders x per-feeder screen", "no-study-fee fast path total; larger batteries need a study, not a refusal"),
    ("screen_fast_path_cap_mw", "DERIVED", "min(10 MW, substation screen)", ""),
    ("screen_fault_current_25pct", "ASSUMPTION", "16 TAC 25.211(g)(1): 25% of fault current", "no fault-current data is public: assumed not binding"),
    ("screen_reverse_flow", "ASSUMPTION", "substation transformer reverse flow", "not evaluated: no transformer MVA or minimum-load data"),
    ("screen_transformer_rule", "DERIVED", "Austin Energy DG guide rev 14 (90%) in LZ_AEN, nameplate (100%) elsewhere", "service-transformer rule used by sim/planner.py"),
    ("cores_per_25kva", "DERIVED", "floor(share x kVA / 20 kW)", "Base Core batteries per service transformer under the rule"),
    ("cores_per_50kva", "DERIVED", "same", ""), ("cores_per_75kva", "DERIVED", "same", ""),
    ("deferral_usd_kw_yr_low", "REAL", "docs/research/ercot.md section 5.3 (25 non-wires studies, PA PUC 2026)", "typical distribution deferral value; applies only where a utility documents a need"),
    ("deferral_usd_kw_yr_mid", "REAL", "same", "25-study average $48"), ("deferral_usd_kw_yr_high", "REAL", "same", ""),
    ("home_fleet_merchant_usd_kw_yr", "SIM", "arena forward tournament, A5, median 2027-29, results/arena_results.json", "home fleet, energy only, at the zone price"),
    ("home_fleet_merchant_p10_usd_kw_yr", "SIM", "same", "bad-case (P10)"),
    ("home_fleet_best_program", "SIM", "arena economics.py programs", "highest-paying programme modelled for the zone; contract terms are ASSUMPTIONs in the arena"),
    ("home_fleet_best_program_usd_kw_yr", "SIM", "arena forward tournament", "total, including merchant trading"),
    ("home_fleet_best_program_p10_usd_kw_yr", "SIM", "same", ""),
    ("home_fleet_programs", "SIM", "same", "all programmes for the zone, name=median"),
    ("nodal_value_usd_kw_yr", "SIM", "Phase 3 data/sites.csv (filled by Phase 6)", "EMPTY on purpose"),
    ("nodal_value_p10_usd_kw_yr", "SIM", "same", "EMPTY"), ("nodal_vs_zone_uplift_usd_kw_yr", "SIM", "same", "EMPTY"),
    ("nodal_value_source", "SIM", "same", "EMPTY"),
]


def tier2(*flags):
    build_tier2()
