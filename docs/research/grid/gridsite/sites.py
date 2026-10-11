"""Phase 3b: candidate sites, site values, the forward valuation `value_sites`, the placement portfolio and the final `sites.csv`.

Pipeline (run_grid.py candidates | lanes | dispatch | forward | portfolio | sites):

  candidates  screen every eligible substation on  score = sum_k |SF[n,k] - SF_zone[k]| x shadow-price-hours[k]  and keep the top N_CANDIDATES
              (at most MAX_PER_CONSTRAINT per dominant element, both sides of a constraint represented)  -> data/site_candidates.csv
  lanes       rebuild 15-minute price lanes for the candidates plus the 8 zone controls (nodal_lanes)
  dispatch    A1-A5 and the perfect-foresight oracle on every lane, 2 h from 2021 and 4 h from 2024       -> data/site_years.csv
  forward     2027-2031 values with planned upgrades and storage saturation (saturation.py), basis half-life
  portfolio   siting.portfolio with group caps (constraints) and per-node caps (POI)                      -> data/portfolio.csv
  sites       the final table                                                                              -> data/sites.csv

Labels (PLAN.md): REAL = from a named source, DERIVED = computed from REAL numbers, SIM = model output, ASSUMPTION = our choice.
"""
from __future__ import annotations

import fnmatch
import json
import math
import time
import warnings
from functools import lru_cache

import numpy as np
import pandas as pd

from . import costs as K
from . import nodal_lanes as L
from . import paths

paths.use_arena()
from arena import config as C  # noqa: E402

REPO_DATA = paths.REPO_DATA
DERIVED = paths.DERIVED

# =================================================================================================================
# Constants (candidate screening)
# =================================================================================================================
N_CANDIDATES = 40                 # ASSUMPTION: brief ("about 40"); the dispatch cost scales with the number of lanes (about 1 min per lane)
MAX_PER_CONSTRAINT = 5            # ASSUMPTION: brief ("about 5"): at most this many candidates share a dominant element
MIN_PER_SIDE = 10                 # ASSUMPTION: at least this many candidates on each side (import / export) of their dominant element
SF_MIN_ELIGIBLE = 0.10            # ASSUMPTION: a usable |SF| to at least one top-priority constraint at least this large (about 5x the 90th percentile |SF| of RT constraints, phase 2)
ELIGIBLE_TOP_PRIORITY = 40        # ASSUMPTION: "top ~40 priority constraints": the 40 best by constraints.csv priority among those with a usable SF magnitude
MIN_MATCH_CONF = 0.5              # REAL: notes/phase1-geo.md, "use match_conf >= 0.5 for anything that ranks sites"
MIN_KV = 69.0                     # REAL: PROMPT.md Tier 1 scope, 69-345 kV substations
TOP4H = 15                        # ASSUMPTION: brief: the 4-hour spec runs the top 15 candidates by 2-hour learner value
TOP_ELEMENTS_REPORTED = 3         # REAL: brief: top-3 constraints per site in sites.csv
SF_REPORT_MIN = 0.05              # ASSUMPTION: an element is named for a site only if the site's own |SF| to it is at least this (phase 2 TOP_BIG_SF: a "large" SF); the rest of its score term is the zone's average effect

CANDIDATES_CSV = REPO_DATA / "site_candidates.csv"
SITE_YEARS_CSV = REPO_DATA / "site_years.csv"
SITES_CSV = REPO_DATA / "sites.csv"
PORTFOLIO_CSV = REPO_DATA / "portfolio.csv"
VALIDATION_CSV = REPO_DATA / "lane_validation.csv"


# =================================================================================================================
# Helpers
# =================================================================================================================
def read_csv(name: str) -> pd.DataFrame:
    """Substation tables are read with keep_default_na=False: the ERCOT substation code 'NA' is real."""
    return pd.read_csv(REPO_DATA / name, keep_default_na=False, na_values=[""], low_memory=False)


def write_labels(csv_path, spec: list, df: pd.DataFrame) -> None:
    """Sidecar `<name>.labels.csv` (column,label,source,note). spec = [(column or prefix*, label, source, note)]; every CSV column gets a row."""
    rows = []
    for col in df.columns:
        hit = None
        for s in spec:
            if fnmatch.fnmatchcase(col, s[0]):
                hit = s
                break
        if hit is None:
            raise KeyError(f"no label for column {col!r} of {csv_path}")
        rows.append({"column": col, "label": hit[1], "source": hit[2], "note": hit[3]})
    pd.DataFrame(rows).to_csv(str(csv_path).replace(".csv", ".labels.csv"), index=False)


@lru_cache(maxsize=1)
def eligible_substations() -> pd.DataFrame:
    """NP4-160 substations that may host a candidate: placed with confidence >= MIN_MATCH_CONF, >= MIN_KV, not excluded for physical risk,
    with an arena zone. Adds the node bus (highest-kV bus) and the arena zone."""
    s = read_csv("substations.csv")
    s = s[(s["in_np4160"] == True) & (s["match_conf"] >= MIN_MATCH_CONF) & (s["kv_max"] >= MIN_KV)].copy()   # noqa: E712
    rf = read_csv("risk_flags.csv")[["sub", "exclude_flag", "exclude_reason", "flood_flag", "wildfire_class", "coastal_flag", "seismic_flag"]]
    s = s.merge(rf, on="sub", how="left")
    s = s[s["exclude_flag"] != True].copy()                                                                  # noqa: E712
    zmap = L.zone_of_substations()
    s["arena_zone"] = s["sub"].map(zmap)
    s = s[s["arena_zone"].isin(L.ZONES)].copy()
    sp = read_csv("settlement_points.csv")
    sp["kv"] = pd.to_numeric(sp["VOLTAGE_LEVEL"], errors="coerce")
    sp["has_rn"] = sp["RESOURCE_NODE"].notna()
    best = sp.sort_values(["SUBSTATION", "kv", "has_rn", "ELECTRICAL_BUS"], ascending=[True, False, False, True]).drop_duplicates("SUBSTATION")
    s = s.merge(best[["SUBSTATION", "ELECTRICAL_BUS", "kv", "RESOURCE_NODE"]].rename(columns={"SUBSTATION": "sub", "ELECTRICAL_BUS": "node_bus", "kv": "node_kv",
                                                                                          "RESOURCE_NODE": "existing_resource_node"}), on="sub", how="left")
    return s.reset_index(drop=True)


# =================================================================================================================
# 1. Candidate screening
# =================================================================================================================
def screen_substations(struct: pd.DataFrame | None = None) -> pd.DataFrame:
    """Score every eligible substation. Returns one row per eligible substation (also the ones that fail the SF threshold; flag `eligible`).

    score_usd_mw_yr = sum over replayed constraints k of |SF[n,k] - SF_zone[k]| x shadow-price-hours[k]   ($ per MW-year of price exposure, DERIVED)
    The score is also split by element (constraint_name) to find the dominant element, its side and its share."""
    struct = struct if struct is not None else L.constraint_structure()
    el = eligible_substations()
    M = L.sub_sf_matrix(el["sub"], struct)
    sph = struct["sph_usd_mw_yr"].to_numpy(float)
    contrib = np.abs(M["DSF"]) * sph[None, :]                    # (N, K) $/MW-yr
    named = np.abs(M["SF"]) >= SF_REPORT_MIN                      # coupling of the site itself (not the zone's average effect)
    score = contrib.sum(1)
    imp = (np.where(M["DSF"] < 0, contrib, 0.0)).sum(1)
    exp_ = (np.where(M["DSF"] > 0, contrib, 0.0)).sum(1)
    # element-level contributions
    names = struct["constraint_name"].to_numpy()
    uniq, inv = np.unique(names, return_inverse=True)
    E = np.zeros((len(el), len(uniq)))
    Es = np.zeros((len(el), len(uniq)))                           # signed: sum DSF x sph (negative = import side)
    np.add.at(E.T, inv, (contrib * named).T)
    np.add.at(Es.T, inv, (M["DSF"] * sph[None, :] * named).T)
    top = np.argsort(-E, axis=1)[:, :TOP_ELEMENTS_REPORTED]
    prio = struct.sort_values("priority_rank").head(ELIGIBLE_TOP_PRIORITY)["cid"].tolist()
    pj = [list(struct["cid"]).index(c) for c in prio]
    max_sf_top = np.abs(M["SF"][:, pj]).max(1)
    out = el[["sub", "kv_max", "county", "arena_zone", "lat", "lon", "match_conf", "node_bus", "node_kv", "existing_resource_node", "flood_flag",
              "wildfire_class", "coastal_flag", "seismic_flag"]].copy()
    out["score_usd_mw_yr"] = score
    out["score_import_usd_mw_yr"] = imp
    out["score_export_usd_mw_yr"] = exp_
    out["max_abs_sf_top_priority"] = max_sf_top
    out["n_constraints_nonzero"] = M["n_nonzero"]
    out["eligible"] = max_sf_top >= SF_MIN_ELIGIBLE
    for r in range(TOP_ELEMENTS_REPORTED):
        j = top[:, r]
        out[f"el{r + 1}"] = uniq[j]
        out[f"el{r + 1}_contrib_usd_mw_yr"] = E[np.arange(len(el)), j]
        out[f"el{r + 1}_side"] = np.where(Es[np.arange(len(el)), j] < 0, "import", "export")
    out["dominant_element"] = out["el1"]
    out["dominant_side"] = out["el1_side"]
    out["dominant_share"] = out["el1_contrib_usd_mw_yr"] / np.maximum(score, 1e-9)
    # for each of the top elements: the cid with the largest contribution, its SF and method
    cids = struct["cid"].to_numpy()
    meth = struct["sf_method"].to_numpy()
    for r in range(1, TOP_ELEMENTS_REPORTED + 1):
        cc, sfv, dsfv, mm = [], [], [], []
        for i, e in enumerate(out[f"el{r}"]):
            ks = np.where(names == e)[0]
            k = ks[np.argmax((contrib * named)[i, ks])]
            cc.append(cids[k]); sfv.append(M["SF"][i, k]); dsfv.append(M["DSF"][i, k]); mm.append(meth[k])
        out[f"el{r}_cid"], out[f"el{r}_sf"], out[f"el{r}_dsf"], out[f"el{r}_sf_method"] = cc, sfv, dsfv, mm
    out["dominant_cid"], out["dominant_sf"], out["dominant_dsf"], out["dominant_sf_method"] = out["el1_cid"], out["el1_sf"], out["el1_dsf"], out["el1_sf_method"]
    # a candidate must be coupled to the element it earns from: the dominant element's own |SF| must clear the eligibility threshold too
    out["eligible"] = out["eligible"] & (out["dominant_sf"].abs() >= SF_MIN_ELIGIBLE)
    return out


def select_candidates(refresh: bool = False) -> pd.DataFrame:
    """The deterministic candidate list -> data/site_candidates.csv (and its labels). Rank 1 = highest score."""
    if CANDIDATES_CSV.exists() and not refresh:
        return read_csv("site_candidates.csv")
    struct = L.constraint_structure()
    scr = screen_substations(struct)
    pool = scr[scr["eligible"]].sort_values("score_usd_mw_yr", ascending=False).reset_index(drop=True)
    chosen, count = [], {}
    for i, r in pool.iterrows():                      # greedy by score with a per-element cap
        if len(chosen) >= N_CANDIDATES:
            break
        if count.get(r["dominant_element"], 0) >= MAX_PER_CONSTRAINT:
            continue
        chosen.append(i)
        count[r["dominant_element"]] = count.get(r["dominant_element"], 0) + 1
    # both sides represented: swap the lowest-scored member of the over-represented side for the best unchosen on the short side
    def side_counts():
        s = pool.loc[chosen, "dominant_side"]
        return int((s == "import").sum()), int((s == "export").sum())

    for short in ("import", "export"):
        other = "export" if short == "import" else "import"
        guard = 0
        while side_counts()[0 if short == "import" else 1] < MIN_PER_SIDE and guard < 100:
            guard += 1
            avail = [i for i in pool.index if i not in chosen and pool.at[i, "dominant_side"] == short
                     and count.get(pool.at[i, "dominant_element"], 0) < MAX_PER_CONSTRAINT]
            drop = [i for i in chosen if pool.at[i, "dominant_side"] == other]
            if not avail or not drop:
                break
            add_i = avail[0]
            drop_i = drop[-1]
            chosen.remove(drop_i)
            count[pool.at[drop_i, "dominant_element"]] -= 1
            chosen.append(add_i)
            count[pool.at[add_i, "dominant_element"]] = count.get(pool.at[add_i, "dominant_element"], 0) + 1
    sel = pool.loc[chosen].sort_values("score_usd_mw_yr", ascending=False).reset_index(drop=True)
    sel.insert(0, "rank", np.arange(1, len(sel) + 1))
    sel.insert(1, "role", "candidate")
    ctrl = pd.DataFrame({"rank": 0, "role": "control_zone", "sub": L.ZONES, "arena_zone": L.ZONES})
    allc = pd.concat([sel, ctrl], ignore_index=True, sort=False)
    # hops to each reported cid (hops.csv covers the 40 best-priority constraints that have an SF)
    hp = read_csv("hops.csv")[["sub", "cid", "hops", "x_dist_pu", "side", "curtail_flag", "has_upgrade"]]
    for r in range(1, TOP_ELEMENTS_REPORTED + 1):
        allc = allc.merge(hp.rename(columns={"cid": f"el{r}_cid", "hops": f"el{r}_hops", "x_dist_pu": f"el{r}_x_dist_pu", "side": f"el{r}_hops_side",
                                             "curtail_flag": f"el{r}_curtail_flag", "has_upgrade": f"el{r}_has_upgrade"}), on=["sub", f"el{r}_cid"], how="left")
    allc["dominant_hops"], allc["dominant_x_dist_pu"], allc["dominant_curtail_flag"], allc["dominant_has_upgrade"] = (
        allc["el1_hops"], allc["el1_x_dist_pu"], allc["el1_curtail_flag"], allc["el1_has_upgrade"])
    allc.to_csv(CANDIDATES_CSV, index=False)
    write_labels(CANDIDATES_CSV, CANDIDATE_LABELS, allc)
    return allc


CANDIDATE_LABELS = [
    ("rank", "DERIVED", "score order", "1 = highest screening score; 0 for the control lanes"),
    ("role", "DERIVED", "this module", "candidate or control_zone (the 8 arena load zones, run as lanes next to the candidates)"),
    ("sub", "REAL", "NP4-160 SUBSTATION", "substation code, or the load zone name for a control lane"),
    ("kv_max", "REAL", "NP4-160 / data/substations.csv", "highest bus voltage"),
    ("county", "DERIVED", "data/substations.csv (agent 1c)", "placement of the substation"),
    ("arena_zone", "DERIVED", "NP4-160 NOIE zone, else settlement load zone", "the zone price the node is measured against and its weather"),
    ("lat", "DERIVED", "data/substations.csv (agent 1c)", "placement; confidence in match_conf"),
    ("lon", "DERIVED", "data/substations.csv (agent 1c)", ""),
    ("match_conf", "DERIVED", "data/substations.csv (agent 1c)", "placement confidence; candidates need >= 0.5"),
    ("node_bus", "REAL", "NP4-160 ELECTRICAL_BUS", "the substation's highest-kV bus, where a new resource node would sit"),
    ("node_kv", "REAL", "NP4-160 VOLTAGE_LEVEL", ""),
    ("existing_resource_node", "REAL", "NP4-160 RESOURCE_NODE", "blank if the highest-kV bus carries no resource node today"),
    ("flood_flag", "DERIVED", "data/risk_flags.csv (agent 5)", ""),
    ("wildfire_class", "DERIVED", "data/risk_flags.csv (agent 5)", ""),
    ("coastal_flag", "DERIVED", "data/risk_flags.csv (agent 5)", ""),
    ("seismic_flag", "DERIVED", "data/risk_flags.csv (agent 5)", ""),
    ("score_usd_mw_yr", "DERIVED", "sum_k |SF[n,k] - SF_zone[k]| x shadow-price-hours[k]", "screening score, $ per MW-year of price exposure; uses only rt_reg and un-attenuated dam_reg shift factors"),
    ("score_import_usd_mw_yr", "DERIVED", "same, import-side terms only", "terms where SF - SF_zone < 0 (the node price rises above the zone when the constraint binds)"),
    ("score_export_usd_mw_yr", "DERIVED", "same, export-side terms only", "terms where SF - SF_zone > 0"),
    ("max_abs_sf_top_priority", "DERIVED", "data/shift_factors.csv logic", "largest |SF| to the top-priority constraints with a usable SF"),
    ("n_constraints_nonzero", "DERIVED", "sf_sub", "replayed constraints with a non-zero SF at the substation"),
    ("eligible", "DERIVED", "this module", "max |SF| to a top-priority constraint >= SF_MIN_ELIGIBLE and the dominant element's own |SF| >= SF_MIN_ELIGIBLE"),
    ("el1_contrib_usd_mw_yr", "DERIVED", "same score split by element", "rank-1 element's share of the score (sum over its cids), $ per MW-year"),
    ("el2_contrib_usd_mw_yr", "DERIVED", "same score split by element", "rank-2 element"),
    ("el3_contrib_usd_mw_yr", "DERIVED", "same score split by element", "rank-3 element"),
    ("el1_side", "DERIVED", "sign of the contribution", "import = the node price rises above the zone when the element binds (SF - SF_zone < 0)"),
    ("el2_side", "DERIVED", "sign of the contribution", ""),
    ("el3_side", "DERIVED", "sign of the contribution", ""),
    ("el1_cid", "DERIVED", "largest-contribution cid of the element", "cid = CONSTRAINT_NAME|CONTINGENCY_NAME"),
    ("el2_cid", "DERIVED", "largest-contribution cid of the element", ""),
    ("el3_cid", "DERIVED", "largest-contribution cid of the element", ""),
    ("el1_sf", "DERIVED", "phase 2 sf_sub (rt_reg or un-attenuated dam_reg), |SF| capped at 1", "substation SF, PLAN.md sign convention (SF < 0 = import side)"),
    ("el2_sf", "DERIVED", "phase 2 sf_sub", ""),
    ("el3_sf", "DERIVED", "phase 2 sf_sub", ""),
    ("el1_dsf", "DERIVED", "SF - SF_zone", ""),
    ("el2_dsf", "DERIVED", "SF - SF_zone", ""),
    ("el3_dsf", "DERIVED", "SF - SF_zone", ""),
    ("el1_sf_method", "DERIVED", "phase 2", "rt_reg or dam_reg"),
    ("el2_sf_method", "DERIVED", "phase 2", ""),
    ("el3_sf_method", "DERIVED", "phase 2", ""),
    ("el1_hops", "DERIVED", "data/hops.csv (phase 2)", "graph hops to the cid's element (only when it is among the 40 priority constraints of hops.csv)"),
    ("el2_hops", "DERIVED", "data/hops.csv (phase 2)", ""),
    ("el3_hops", "DERIVED", "data/hops.csv (phase 2)", ""),
    ("el1_x_dist_pu", "DERIVED", "data/hops.csv (phase 2)", "typical-reactance distance, per unit on 100 MVA (ASSUMPTION reactances)"),
    ("el2_x_dist_pu", "DERIVED", "data/hops.csv (phase 2)", ""),
    ("el3_x_dist_pu", "DERIVED", "data/hops.csv (phase 2)", ""),
    ("el1_hops_side", "DERIVED", "data/hops.csv (phase 2)", ""),
    ("el2_hops_side", "DERIVED", "data/hops.csv (phase 2)", ""),
    ("el3_hops_side", "DERIVED", "data/hops.csv (phase 2)", ""),
    ("el1_curtail_flag", "DERIVED", "data/hops.csv (phase 2)", "export side with SF >= 0.05: injecting here loads the constraint"),
    ("el2_curtail_flag", "DERIVED", "data/hops.csv (phase 2)", ""),
    ("el3_curtail_flag", "DERIVED", "data/hops.csv (phase 2)", ""),
    ("el1_has_upgrade", "DERIVED", "data/hops.csv (phase 2)", "planned-upgrade match at confidence >= 0.5"),
    ("el2_has_upgrade", "DERIVED", "data/hops.csv (phase 2)", ""),
    ("el3_has_upgrade", "DERIVED", "data/hops.csv (phase 2)", ""),
    ("el1", "DERIVED", "same score split by element", "rank-1 element by contribution to the score (constraint_name)"),
    ("el2", "DERIVED", "same score split by element", "rank-2 element"),
    ("el3", "DERIVED", "same score split by element", "rank-3 element"),
    ("dominant_element", "DERIVED", "= el1", "the element contributing most to the score"),
    ("dominant_side", "DERIVED", "sign of the contribution", "import = node price rises above the zone when the element binds (SF - SF_zone < 0); export = it falls"),
    ("dominant_share", "DERIVED", "el1 / score", ""),
    ("dominant_cid", "DERIVED", "cid with the largest contribution within the dominant element", ""),
    ("dominant_sf", "DERIVED", "phase 2 sf_sub", "substation SF to the dominant cid; PLAN.md sign convention (SF < 0 = import side)"),
    ("dominant_dsf", "DERIVED", "SF - SF_zone", ""),
    ("dominant_sf_method", "DERIVED", "phase 2", "rt_reg or dam_reg"),
    ("dominant_hops", "DERIVED", "data/hops.csv (phase 2)", "graph hops to the dominant cid (only when it is among the 40 priority constraints of hops.csv)"),
    ("dominant_x_dist_pu", "DERIVED", "data/hops.csv (phase 2)", "typical-reactance distance, per unit on 100 MVA (ASSUMPTION reactances)"),
    ("hops_side", "DERIVED", "data/hops.csv (phase 2)", ""),
    ("dominant_curtail_flag", "DERIVED", "data/hops.csv (phase 2)", "export side with SF >= 0.05: injecting here loads the constraint"),
    ("dominant_has_upgrade", "DERIVED", "data/hops.csv (phase 2)", "planned-upgrade match at confidence >= 0.5"),
]


# =================================================================================================================
# 2. Lanes and dispatch (A1-A5 and the perfect-foresight oracle on every lane)
# =================================================================================================================
RUNS = DERIVED / "runs"
LANE_TAG = "cand40"
SIM_START = "2021-01-01"          # REAL: brief, first simulated day of the 2 h run
SIM_START_4H = "2024-01-01"       # REAL: brief, first simulated day of the 4 h run
SPEC_2H, SPEC_4H = "grid", "grid4h"
ALGOS = ("A1", "A2", "A3", "A4", "A5")
BACKTEST_YEARS = (2021, 2022, 2023, 2024, 2025)    # REAL: the complete years of the backtest (2026 ends 26 Sep)
RATIO_YEARS = (2023, 2024, 2025)  # REAL: brief, the learner / oracle ratio comes from the 2023-25 backtest
DISPATCH_WORKERS = 4              # REAL: brief, at most 4 workers (the machine is shared)
DISPATCH_CHUNK = 12               # ASSUMPTION: lanes per task; the cost of a task is nearly flat in the number of lanes (notes/phase3a.md section 5), so 48 lanes make 4 tasks
HOURS_PER_SPEC = {SPEC_2H: 2.0, SPEC_4H: 4.0}      # REAL: usable MWh per MW of the two merchant specs
FULL_YEAR_MIN_DAYS = 360          # REAL: the arena's yearly() rule: a year with fewer simulated days is scaled to DAYS_PER_YEAR
DAYS_PER_YEAR = 365.0             # REAL: annualisation
EXPERT_NAMES = ["Clockwork", "Forecaster", "Weather-regime", "Spike hunter q97", "Spike hunter q90", "Forecaster (low carry)"]   # REAL: arena.algorithms.Adaptive


def candidate_subs() -> list:
    c = select_candidates()
    return list(c[c["role"] == "candidate"].sort_values("rank")["sub"])


def ensure_lanes(refresh: bool = False) -> dict:
    """The lane file for the candidates and the 8 controls (derived/nodal_lanes_cand40.npz), rebuilt if missing or if its key (constants, structure, prices, substations) changed."""
    f = DERIVED / f"nodal_lanes_{LANE_TAG}.npz"
    subs = candidate_subs()
    sig = L.lane_signature(subs)
    if f.exists() and not refresh:
        z = L.load_lanes(LANE_TAG)
        if z["lanes"][len(L.ZONES):] == subs and z["meta"].get("sig") == sig:
            return z                                           # same substations, constants, structure and price history
    L.build_lanes(subs, LANE_TAG)
    return L.load_lanes(LANE_TAG)


def _lane_zone(z: dict, lane_ids) -> dict:
    return {ln: z["zones"][z["lanes"].index(ln)] for ln in lane_ids}


def run_dispatch(spec: str, lane_ids, start: str, *, workers: int = DISPATCH_WORKERS, chunk: int = DISPATCH_CHUNK, z: dict | None = None):
    """A1-A5 and the adaptive learner on `lane_ids` (controls included) with the arena's code (gridsite.runner, lane-safe context), resumable by chunk."""
    from . import runner

    z = z or ensure_lanes()
    ix = [z["lanes"].index(ln) for ln in lane_ids]
    RUNS.mkdir(parents=True, exist_ok=True)
    res = runner.run_lanes(list(lane_ids), z["P"][:, ix, :], ref=z["ref"], dates=z["dates"], lane_zone=_lane_zone(z, lane_ids), spec=spec, start=start, end=None,
                           workers=workers, chunk=chunk, context="lane", cache_dir=RUNS / f"cache_{spec}", progress=True)
    res.save(RUNS / f"{spec}_ledgers.npz")
    return res


def run_oracle(spec: str, lane_ids, start: str, *, workers: int = DISPATCH_WORKERS, z: dict | None = None) -> np.ndarray:
    """Perfect-foresight ceiling per day and lane ($ per MW per day) on the simulated days, saved to runs/<spec>_oracle.npz."""
    from . import runner

    z = z or ensure_lanes()
    ix = [z["lanes"].index(ln) for ln in lane_ids]
    day = np.where(z["dates"] >= start)[0]
    orc = runner.oracle_lanes(z["P"][day][:, ix, :], spec, workers=workers, progress=True)
    np.savez_compressed(RUNS / f"{spec}_oracle.npz", oracle=orc, dates=z["dates"][day], lanes=np.array(lane_ids))
    return orc


def load_ledgers(spec: str):
    from . import runner

    return runner.LaneResults.load(RUNS / f"{spec}_ledgers.npz")


def load_oracle(spec: str) -> dict:
    o = np.load(RUNS / f"{spec}_oracle.npz", allow_pickle=False)
    return {"oracle": o["oracle"], "dates": o["dates"].astype(str), "lanes": list(o["lanes"].astype(str))}


def _annual(arr_days: np.ndarray, years: np.ndarray, y: int) -> np.ndarray:
    """$/kW-yr per lane for one year from (days, lanes) $ per MW per day, annualised like the arena's yearly(): fewer than 360 days -> scaled to 365."""
    sel = years == y
    n = int(sel.sum())
    return arr_days[sel].sum(0) * (DAYS_PER_YEAR / n if n < FULL_YEAR_MIN_DAYS else 1.0) / 1000.0


def top_candidates_2h(res=None, n: int = TOP4H, years=BACKTEST_YEARS) -> list:
    """The n candidates with the highest 2-hour learner (A5) value, mean $/kW-yr over `years`; ties broken by the 2025 value, then by name."""
    res = res or load_ledgers(SPEC_2H)
    cand = set(candidate_subs())
    v = np.mean([res.usd_kw_yr("A5", y) for y in years], axis=0)
    v25 = res.usd_kw_yr("A5", 2025)
    order = sorted([(-v[i], -v25[i], ln) for i, ln in enumerate(res.lanes) if ln in cand])
    return [o[2] for o in order[:n]]


def site_years_table(res, orc: np.ndarray, spec: str, cand: pd.DataFrame, z: dict) -> pd.DataFrame:
    """One row per (spec, lane, period): $/kW-yr for A1-A5 and the oracle, $ per MWh discharged, uplift against the lane's zone control.

    Periods: each calendar year simulated, and for the 2 h spec the mean of 2021-25 and of 2023-25 (the learner/oracle ratio window); for 4 h the mean of 2024-25.
    A year with fewer than 360 simulated days (2026, to 26 Sep) is annualised to 365 days. Multi-year rows are the mean of the annual values; their
    $/MWh is total profit over total MWh. The zone controls are lanes of the same run, so uplift is like for like."""
    dates, years = res.dates, res.years
    lanes = res.lanes
    ycal = sorted(set(years))
    periods = [(str(y), [y]) for y in ycal]
    periods += [("mean_2021_25", list(BACKTEST_YEARS)), ("mean_2023_25", list(RATIO_YEARS))] if spec == SPEC_2H else [("mean_2024_25", [2024, 2025])]
    cinfo = cand[cand["role"] == "candidate"].set_index("sub")
    zone_of = dict(zip(z["lanes"], z["zones"]))
    rows = []
    shares = res.pick_shares()
    for name, ys in periods:
        if not all(y in ycal for y in ys):
            continue
        ann = {a: np.mean([_annual(res.algos[a]["profit"], years, y) for y in ys], axis=0) for a in ALGOS}
        ann["oracle"] = np.mean([_annual(orc, years, y) for y in ys], axis=0)
        sel = np.isin(years, ys)
        mwh = {a: res.algos[a]["mwh"][sel].sum(0) for a in ALGOS}
        prof = {a: res.algos[a]["profit"][sel].sum(0) for a in ALGOS}
        days = int(sel.sum())
        n_years = len(ys)
        for i, ln in enumerate(lanes):
            zl = zone_of[ln]
            zi = lanes.index(zl)
            is_ctrl = ln in L.ZONES
            row = {"spec": spec, "lane": ln, "role": "control_zone" if is_ctrl else "candidate",
                   "rank": 0 if is_ctrl else int(cinfo.at[ln, "rank"]), "zone": zl, "period": name, "days": days, "partial_year": bool(n_years == 1 and days < FULL_YEAR_MIN_DAYS)}
            for a in ALGOS:
                row[f"usd_kw_yr_{a}"] = ann[a][i]
            row["usd_kw_yr_oracle"] = ann["oracle"][i]
            row["a5_over_oracle"] = ann["A5"][i] / ann["oracle"][i] if ann["oracle"][i] > 0 else np.nan
            for a in ALGOS:
                row[f"usd_per_mwh_{a}"] = prof[a][i] / mwh[a][i] if mwh[a][i] > 0 else np.nan
            row["mwh_out_per_kw_yr_A5"] = mwh["A5"][i] / n_years / 1000.0 * (DAYS_PER_YEAR / (days / n_years) if days / n_years < FULL_YEAR_MIN_DAYS else 1.0)
            for a in ALGOS:
                row[f"uplift_usd_kw_yr_{a}"] = ann[a][i] - ann[a][zi]
            row["uplift_usd_kw_yr_oracle"] = ann["oracle"][i] - ann["oracle"][zi]
            cnt = np.array([(res.picks[sel][:, i] == e).sum() for e in range(len(EXPERT_NAMES))], float)
            row["a5_top_expert"] = EXPERT_NAMES[int(cnt.argmax())] if cnt.sum() > 0 else ""
            row["a5_top_expert_share"] = float(cnt.max() / max(cnt.sum(), 1))
            rows.append(row)
    return pd.DataFrame(rows)


SITE_YEARS_LABELS = [
    ("spec", "DERIVED", "this module", "grid = 1 MW / 2 MWh, grid4h = 1 MW / 4 MWh (gridsite.specs)"),
    ("lane", "REAL", "NP4-160 SUBSTATION / arena zone name", "a candidate substation (its highest-kV bus as a new resource node) or one of the 8 arena load zones (control)"),
    ("role", "DERIVED", "this module", "candidate or control_zone"),
    ("rank", "DERIVED", "data/site_candidates.csv", "screening rank; 0 for controls"),
    ("zone", "DERIVED", "NP4-160 NOIE zone, else load zone", "the zone control the uplift is measured against"),
    ("period", "DERIVED", "this module", "a calendar year, or mean_2021_25 / mean_2023_25 / mean_2024_25 (mean of the annual values)"),
    ("days", "DERIVED", "this module", "simulated days in the period"),
    ("partial_year", "DERIVED", "this module", "True for 2026: annualised from 1 Jan to 26 Sep (the missing Q4 is the cheap season, so the annualisation may flatter or hurt)"),
    ("usd_kw_yr_A*", "SIM", "arena dispatch (gridsite.runner) on the rebuilt lane", "energy-only profit after $20/MWh wear, $ per kW-yr; walk-forward, prices up to yesterday; control rows run on REAL zone prices"),
    ("usd_kw_yr_oracle", "SIM", "arena.battery.oracle_days", "perfect-foresight ceiling on the same lane (the arena's yardstick)"),
    ("a5_over_oracle", "DERIVED", "A5 / oracle", "the learner's share of the ceiling"),
    ("usd_per_mwh_A*", "SIM", "profit after wear / MWh discharged", "margin per MWh sold"),
    ("mwh_out_per_kw_yr_A5", "SIM", "arena ledger", "MWh discharged per kW-yr by the learner"),
    ("uplift_usd_kw_yr_*", "SIM", "node minus zone control, same algorithm, same run", "what siting at the node adds over the load zone (negative = the node is worse than the zone)"),
    ("a5_top_expert", "SIM", "learner picks", "the expert the learner followed most days"),
    ("a5_top_expert_share", "SIM", "learner picks", "share of days"),
]


def write_site_years() -> pd.DataFrame:
    """data/site_years.csv (+labels): the dispatch results of every spec that has been run (ledgers and oracle in derived/runs)."""
    cand = select_candidates()
    z = ensure_lanes()
    parts = []
    for spec in (SPEC_2H, SPEC_4H):
        if not (RUNS / f"{spec}_ledgers.npz").exists() or not (RUNS / f"{spec}_oracle.npz").exists():
            continue
        res = load_ledgers(spec)
        o = load_oracle(spec)
        if o["lanes"] != res.lanes or list(o["dates"]) != list(res.dates):
            raise ValueError(f"oracle and ledgers of {spec} do not cover the same lanes and days")
        parts.append(site_years_table(res, o["oracle"], spec, cand, z))
    if not parts:
        raise FileNotFoundError("no dispatch results in derived/runs; run `dispatch` first")
    df = pd.concat(parts, ignore_index=True)
    tmp = SITE_YEARS_CSV.with_suffix(".tmp")
    df.round(4).to_csv(tmp, index=False)
    tmp.replace(SITE_YEARS_CSV)                              # atomic: a forward run may be reading the ratios from this file
    write_labels(SITE_YEARS_CSV, SITE_YEARS_LABELS, df)
    return df


VALIDATION_LABELS = [
    ("check", "DERIVED", "this module", "a = rebuilt node basis vs actual node basis with the actual real-time shadow prices, a2 = the same in the day-ahead market; b = synthetic shadow prices vs their targets; c = control lanes vs the arena; d = scale of the synthetic basis; e = zone shift factors; f = drivers"),
    ("scope", "DERIVED", "this module", "the subset the metric is computed on"),
    ("metric", "DERIVED", "this module", "name and unit of the number"),
    ("value", "DERIVED", "REAL SPPs and shadow prices (a, d actual), SIM structure (b, d synthetic), REAL arena prices (c)", "see `check`: rows a and c compare against real prices, rows b and d (synthetic) describe the SIM structure"),
    ("n", "DERIVED", "this module", "sample size behind the value"),
    ("note", "DERIVED", "this module", ""),
]


# =================================================================================================================
# 3. Forward values: the 2025 replay year with the structure scaled year by year (planned upgrades, storage saturation)
# =================================================================================================================
BASE_YEAR = 2025                         # REAL: the replay year (the structure is the 2025 one)
FORWARD_REPORT_YEARS = (2027, 2029, 2031)    # REAL: brief: forward 2027 / 2029 / 2031 in sites.csv
RATIO_PERIOD = {SPEC_2H: "mean_2023_25", SPEC_4H: "mean_2024_25"}   # REAL: brief: the ratio comes from the 2023-25 backtest; the 4 h run starts in 2024, so its ratio is 2024-25
HALF_LIFE_FRACTION = 0.5                 # REAL: brief: uplift below 50% of its 2025 value
VALUE_CACHE = RUNS / "value_cache.json"


def dispatch_ratios(spec: str) -> pd.DataFrame:
    """Per lane: A_i / oracle over the ratio window of the backtest (site_years.csv). Empty if the dispatch has not been tabulated."""
    if not SITE_YEARS_CSV.exists():
        return pd.DataFrame(columns=[f"ratio_{a}" for a in ALGOS])
    d = read_csv("site_years.csv")
    d = d[(d["spec"] == spec) & (d["period"] == RATIO_PERIOD[spec])].set_index("lane")
    out = pd.DataFrame(index=d.index)
    for a in ALGOS:
        out[f"ratio_{a}"] = d[f"usd_kw_yr_{a}"] / d["usd_kw_yr_oracle"]
    return out


def _user_scale(mu_scale, struct: pd.DataFrame):
    """(K,) multipliers from a user dict {cid or element name: scale} (0 = fully relieved). Returns (vector, ignored keys)."""
    v = np.ones(len(struct))
    ignored = []
    if not mu_scale:
        return v, ignored
    cid_ix = {c: i for i, c in enumerate(struct["cid"])}
    el_ix = {k: np.asarray(ix) for k, ix in struct.groupby("constraint_name").indices.items()}
    for k, sc in dict(mu_scale).items():
        if k in cid_ix:
            v[cid_ix[k]] *= float(sc)
        elif k in el_ix:
            v[el_ix[k]] *= float(sc)
        else:
            ignored.append(k)
    return v, ignored


def _baseline_vector(year: int, struct: pd.DataFrame, baseline: str) -> np.ndarray:
    from . import saturation as SA

    if baseline in ("none", None, False):
        return np.ones(len(struct))
    up = baseline in ("both", "upgrades", True)
    sat = baseline in ("both", "saturation", True)
    if baseline not in ("both", "upgrades", "saturation", True):
        raise ValueError("baseline must be 'both', 'upgrades', 'saturation' or 'none'")
    return SA.scale_vector(year, struct, upgrades=up, saturation=sat)


def _load_value_cache() -> dict:
    import json as _json

    if VALUE_CACHE.exists():
        try:
            return _json.loads(VALUE_CACHE.read_text())
        except Exception:
            return {}
    return {}


def _save_value_cache(c: dict) -> None:
    import json as _json

    RUNS.mkdir(parents=True, exist_ok=True)
    tmp = VALUE_CACHE.with_suffix(".tmp")
    tmp.write_text(_json.dumps(c))
    tmp.replace(VALUE_CACHE)


def _hash(*arrs) -> str:
    import hashlib

    h = hashlib.sha1()
    for a in arrs:
        h.update(np.ascontiguousarray(np.round(np.asarray(a, float), 6)).tobytes())
    return h.hexdigest()[:16]


def _spec_def(spec: str) -> dict:
    """The arena's definition of the battery spec (energy units, efficiency, wear), part of every value-cache key."""
    from . import specs as _specs  # noqa: F401  (registers grid4h in arena.config.SPECS)

    return dict(C.SPECS[spec])


def _value_sigs(spec: str, struct: pd.DataFrame, Mu, hist: dict) -> tuple:
    """(node signature, zone signature) of the value cache: everything the oracle of a lane depends on except the lane's own shift factors and the scale vector,
    which are in the key itself. The battery definition, the named constants (the price clip among them), the structure (targets, hours, caps, drivers) and the prices."""
    spec_def = _spec_def(spec)
    sig = L._sig(spec_def, L.CACHE_VERSION, L.lane_constants(), L._struct_hash(struct), L._price_stamp(), [Mu.nnz, float(Mu.data.sum()), len(hist["dates"])])
    zsig = L._sig(spec_def, L.CACHE_VERSION, L._price_stamp())
    return sig, zsig


def value_sites(lane_ids, mu_scale=None, spec: str = SPEC_2H, years=(2025,), method: str = "oracle_ratio", baseline: str = "both", workers: int = 1) -> pd.DataFrame:
    """Value of sites with the congestion structure scaled: the API Phase 6 uses to rerank sites after any upgrade.

    lane_ids  candidate substations (or any NP4-160 substation with shift factors and an arena zone) and/or the arena zone names.
    mu_scale  None, or {cid or element name: multiplier on that constraint's synthetic shadow prices}, e.g. {"6437__F": 0.0} for a full relief
              (gridsite.upgrades.apply_upgrade(...) returns exactly this). It multiplies the baseline of the year.
    spec      "grid" (2 h) or "grid4h" (4 h).
    years     a year up to 2025 is that year's rebuilt prices; a later year is the 2025 replay with the baseline scale of that year
              (planned upgrades and storage saturation, gridsite.saturation) - zone prices stay at their 2025 values.
    method    "oracle_ratio": learner value = perfect-foresight oracle on the rebuilt prices x the lane's A_i/oracle ratio from the backtest (SIM);
              "oracle": the ceiling itself.
    baseline  which of the year's scale factors apply: "both" (default), "upgrades", "saturation" or "none".

    Returns one row per lane and year: oracle, learner and A1-A5 ($/kW-yr), the zone control's learner value and the uplift (node minus zone), the lane's
    ratio and where it comes from, and the exposure-weighted mean scale of the lane's constraints. `mu_scale=None, years=(2025,)` reproduces the 2025
    oracle of data/site_years.csv (tests/test_3b.py)."""
    from . import runner

    if method not in ("oracle_ratio", "oracle"):
        raise ValueError("method must be 'oracle_ratio' or 'oracle'")
    if spec not in (SPEC_2H, SPEC_4H):
        raise ValueError("spec must be 'grid' or 'grid4h'")
    struct, Mu, _ = L.mu_tilde()
    hist = L.price_history()
    lane_ids = [str(x) for x in lane_ids]
    zmap = L.zone_of_substations()
    user, ignored = _user_scale(mu_scale, struct)
    ratios = dispatch_ratios(spec)
    nodes = [x for x in lane_ids if x not in L.ZONES]
    bad = [n for n in nodes if n not in zmap]
    if bad:
        raise ValueError(f"no arena zone for {bad[:5]}")
    M = L.sub_sf_matrix(nodes, struct) if nodes else None
    node_pos = {n: i for i, n in enumerate(nodes)}
    zone_of = {x: (x if x in L.ZONES else zmap[x]) for x in lane_ids}
    zone_set = sorted(set(zone_of.values()))
    sph = struct["sph_usd_mw_yr"].to_numpy(float)
    cache = _load_value_cache()
    rows = []
    zi = {z: k for k, z in enumerate(L.ZONES)}
    sig, zsig = _value_sigs(spec, struct, Mu, hist)
    for y in years:
        py = y if y <= BASE_YEAR else BASE_YEAR
        idx = [i for i, d in enumerate(hist["dates"]) if d.startswith(str(py))]
        sl = slice(idx[0], idx[-1] + 1)
        n_days = len(idx)
        ann = (DAYS_PER_YEAR / n_days if n_days < FULL_YEAR_MIN_DAYS else 1.0) / 1000.0
        s = _baseline_vector(y, struct, baseline) * user
        skey = _hash(s)
        keys, todo = {}, []
        for x in lane_ids:
            if x in L.ZONES:
                keys[x] = f"{spec}|{x}|{py}|zone|{zsig}"
            else:
                keys[x] = f"{spec}|{x}|{py}|{sig}|{skey}|{_hash(M['DSF'][node_pos[x]])}"
        for z_ in zone_set:
            keys.setdefault(z_, f"{spec}|{z_}|{py}|zone|{zsig}")
        need = [k for k in dict.fromkeys(keys.values()) if k not in cache]
        if need:
            lanes_need = [x for x, k in keys.items() if k in need]
            cols = []
            for x in lanes_need:
                if x in L.ZONES:
                    cols.append(hist["P_zone"][sl][:, zi[x], :])
                else:
                    j = node_pos[x]
                    cols.append(L.node_prices(M["DSF"][j:j + 1], [M["zone"][j]], Mu, hist, mu_scale=s, day_slice=sl)[:, 0, :])
            Pn = np.stack(cols, 1)
            orc = runner.oracle_lanes(Pn, spec, workers=workers)
            for x, col in zip(lanes_need, orc.T):
                cache[keys[x]] = float(col.sum() * ann)
            _save_value_cache(cache)
        for x in lane_ids:
            z_ = zone_of[x]
            o = cache[keys[x]]
            oz = cache[keys[z_]]

            def ratio(lane, a):
                if lane in ratios.index:
                    return float(ratios.at[lane, f"ratio_{a}"]), "own"
                if z_ in ratios.index:
                    return float(ratios.at[z_, f"ratio_{a}"]), "zone_control"
                return np.nan, "none"

            row = {"spec": spec, "lane": x, "role": "control_zone" if x in L.ZONES else "candidate", "zone": z_, "year": int(y), "price_year": int(py),
                   "baseline": baseline, "method": method, "oracle_usd_kw_yr": o, "zone_oracle_usd_kw_yr": oz, "uplift_oracle_usd_kw_yr": o - oz}
            rA5, basis = ratio(x, "A5")
            rZ5, _ = ratio(z_, "A5")
            for a in ALGOS:
                ra, _ = ratio(x, a)
                row[f"usd_kw_yr_{a}"] = o if method == "oracle" else o * ra
            row["learner_usd_kw_yr"] = o if method == "oracle" else o * rA5
            row["zone_learner_usd_kw_yr"] = oz if method == "oracle" else oz * rZ5
            row["uplift_learner_usd_kw_yr"] = row["learner_usd_kw_yr"] - row["zone_learner_usd_kw_yr"]
            row["ratio_A5"], row["ratio_basis"] = rA5, basis
            if x in node_pos:
                w = np.abs(M["DSF"][node_pos[x]]) * sph
                row["exposure_weighted_scale"] = float((w * s).sum() / w.sum()) if w.sum() > 0 else 1.0       # no exposure: nothing to scale
            else:
                row["exposure_weighted_scale"] = 1.0
            rows.append(row)
    df = pd.DataFrame(rows)
    df.attrs["ignored_mu_scale_keys"] = ignored
    return df


def half_life_table(vs: pd.DataFrame, years=(2026, 2027, 2028, 2029, 2030, 2031)) -> pd.DataFrame:
    """Basis half-life per lane from a value_sites frame that holds BASE_YEAR and the later years (scenario 'both'): the first year in which the
    learner's uplift against the zone is below HALF_LIFE_FRACTION of its BASE_YEAR value, else '> 2031'."""
    out = []
    last = max(years)
    for lane, g in vs[vs["baseline"] == "both"].groupby("lane"):
        if lane in L.ZONES:
            continue
        g = g.set_index("year")
        if BASE_YEAR not in g.index:
            continue
        u0 = float(g.at[BASE_YEAR, "uplift_learner_usd_kw_yr"])
        hl = f"> {last}"
        first = None
        if u0 <= 0:
            hl = "n/a (no positive uplift in 2025)"
        else:
            for y in years:
                if y in g.index and float(g.at[y, "uplift_learner_usd_kw_yr"]) < HALF_LIFE_FRACTION * u0:
                    first = y
                    hl = str(y)
                    break
        out.append({"lane": lane, "uplift_2025_usd_kw_yr": u0, "half_life_year": hl, "half_life_year_num": first if first is not None else np.nan})
    return pd.DataFrame(out)


def run_forward(workers: int = DISPATCH_WORKERS, specs=(SPEC_2H, SPEC_4H)) -> pd.DataFrame:
    """Forward values for the 2 h candidates (all 40 + controls) and the 4 h top 15, 2025 and 2026-2031; writes derived/runs/forward_values.parquet and
    forward_halflife.parquet. Scenarios: both effects (the baseline), saturation only and upgrades only (2031). `specs` selects what is (re)computed; rows of
    the other spec already on disk are kept. The 4 h run needs data/site_years.csv with 4 h rows (the ratios)."""
    import json as _json

    from . import saturation as SA

    t0 = time.time()
    struct, _, _ = L.mu_tilde()
    SA.scale_table(struct).to_parquet(RUNS / "forward_scales.parquet")
    sched, src = SA.planned_upgrades(struct)
    proj = SA.storage_projects()
    meta = {"planned_upgrade_source": src, "n_scheduled_cids": int(len(sched)), "storage_rows": int(len(proj)), "storage_mw_listed": float(proj["mw"].sum()),
            "storage_mw_online": {int(y): float(SA.storage_online(y, proj).sum()) for y in SA.FORWARD_YEARS},
            "constants": {k: str(getattr(SA, k)) for k in ("UPGRADE_RESIDUAL", "UPGRADE_RESIDUAL_GTC", "SAT_MIN_SF", "SAT_SLOPE", "SAT_FLOOR", "SAT_REALISATION", "SAT_SIDES")}}
    (RUNS / "forward_meta.json").write_text(_json.dumps(meta, indent=1))
    ctrl = list(L.ZONES)
    parts, vs = [], None
    if SPEC_2H in specs:
        cand = candidate_subs()
        yrs = (BASE_YEAR,) + tuple(SA.FORWARD_YEARS)
        vs = value_sites(ctrl + cand, spec=SPEC_2H, years=yrs, baseline="both", workers=workers)
        parts.append(vs)
        parts.append(value_sites(ctrl + cand, spec=SPEC_2H, years=(2031,), baseline="saturation", workers=workers))
        parts.append(value_sites(ctrl + cand, spec=SPEC_2H, years=(2031,), baseline="upgrades", workers=workers))
        print(f"2 h forward done ({time.time() - t0:.0f}s)", flush=True)
    if SPEC_4H in specs:
        top = top_candidates_2h()
        parts.append(value_sites(ctrl + top, spec=SPEC_4H, years=(BASE_YEAR,) + FORWARD_REPORT_YEARS, baseline="both", workers=workers))
        print(f"4 h forward done ({time.time() - t0:.0f}s)", flush=True)
    new = pd.concat(parts, ignore_index=True)
    f = RUNS / "forward_values.parquet"
    if f.exists():
        old = pd.read_parquet(f)
        new = pd.concat([old[~old["spec"].isin(set(new["spec"]))], new], ignore_index=True)
    new.to_parquet(f, index=False)
    if vs is not None:
        half_life_table(vs).to_parquet(RUNS / "forward_halflife.parquet", index=False)
    mf = RUNS / "forward_meta.json"                              # provenance: which lanes and which learner/oracle ratios these values were computed from
    meta2 = _json.loads(mf.read_text())
    ratio_md5 = dict(meta2.get("ratio_md5", {}))
    for sp in specs:
        ratio_md5[sp] = _ratio_hash(sp)
    meta2.update(lane_sig=L.lane_file_meta(LANE_TAG).get("sig"), ratio_md5=ratio_md5)
    mf.write_text(_json.dumps(meta2, indent=1))
    return new


def _ratio_hash(spec: str) -> str:
    """Fingerprint of the lanes and learner/oracle ratios that dispatch_ratios(spec) returns."""
    import hashlib

    r = dispatch_ratios(spec).sort_index()
    h = hashlib.sha1()
    h.update(",".join(r.index.astype(str)).encode())
    h.update(np.round(r.to_numpy(float), 6).tobytes())
    return h.hexdigest()[:16]


def check_provenance(specs=(SPEC_2H,)) -> None:
    """Refuse to join forward values that were computed from other lanes, or with other learner/oracle ratios, than the ones on disk now (run `forward` again)."""
    mf = RUNS / "forward_meta.json"
    if not mf.exists():
        raise FileNotFoundError("no derived/runs/forward_meta.json: run `forward` first")
    meta = json.loads(mf.read_text())
    if meta.get("lane_sig") != L.lane_file_meta(LANE_TAG).get("sig"):
        raise ValueError("the forward values were computed from other lanes than the lane file on disk: run `forward` again")
    for sp in specs:
        if meta.get("ratio_md5", {}).get(sp) != _ratio_hash(sp):
            raise ValueError(f"the {sp} forward values were computed with other learner/oracle ratios than data/site_years.csv holds now: run `forward spec={sp}` again")


# =================================================================================================================
# 4. Placement portfolio (siting.portfolio with group caps and per-node caps)
# =================================================================================================================
PORTFOLIO_YEARS = (2027, 2028, 2029, 2030, 2031)   # REAL: brief: 2027-2031
HISTORY_YEARS = (2021, 2022, 2023, 2024, 2025, 2026)   # REAL: the backtest years the rules see ("known"); 2026 is annualised from 1 Jan to 26 Sep
UNIT_MW = 10.0                           # REAL: brief: units are 10 MW (siting.allocate places one unit at a time)
UNITS_PER_YEAR = 20                      # ASSUMPTION: brief example: 20 units = 200 MW a year
PRICE_TAKER_SHARE = 0.10                 # ASSUMPTION: brief: nodes behind one constraint share a cap of this share of the constraint's median limit
MIN_GROUP_UNITS = 1                      # ASSUMPTION: a group cap is never below one unit (10 MW), so no element is closed to a first unit by rounding
POI_CAP_MW = {69: 50.0, 138: 150.0, 345: 300.0}   # ASSUMPTION: brief: point-of-interconnection capacity of one site by voltage class
POI_CLASS_KV_EDGES = (115.0, 230.0)      # ASSUMPTION: a site at or below 115 kV is the 69 kV class, at or below 230 kV the 138 kV class, above that the 345 kV class
REF_BATTERY_MW = K.REF_MW                # REAL: gridsite.costs.REF_MW, the reference battery of data/connection_costs.csv (100 MW)
PORTFOLIO_SEED = 20261010                # ASSUMPTION: seed of the generator passed to siting.portfolio (only the Thompson variant draws numbers)
PORTFOLIO_RULES = ALGOS                  # REAL: brief: rules A1-A5


def poi_class(kv: float) -> int:
    """Voltage class of a site for the POI cap, by POI_CLASS_KV_EDGES."""
    return 69 if kv <= POI_CLASS_KV_EDGES[0] else (138 if kv <= POI_CLASS_KV_EDGES[1] else 345)


def group_cap_units(limit_mw: float) -> int:
    """Whole 10 MW units that nodes behind one element may hold together: PRICE_TAKER_SHARE x its median limit, rounded DOWN (siting.allocate treats less than one unit
    left as full, so a fractional cap is whole units anyway), at least MIN_GROUP_UNITS. The reported cap is this number x UNIT_MW."""
    return int(max(MIN_GROUP_UNITS, math.floor(PRICE_TAKER_SHARE * float(limit_mw) / UNIT_MW + 1e-9)))


def _fwd_matrix(fv: pd.DataFrame, spec: str, lanes, col: str, years) -> np.ndarray:
    d = fv[(fv["spec"] == spec) & (fv["baseline"] == "both") & fv["year"].isin(years)]
    p = d.pivot_table(index="year", columns="lane", values=col, aggfunc="first").reindex(index=list(years), columns=list(lanes))
    return p.to_numpy(float)


def _hist_matrix(sy: pd.DataFrame, spec: str, lanes, col: str, years) -> np.ndarray:
    d = sy[(sy["spec"] == spec) & sy["period"].astype(str).isin([str(y) for y in years])]
    p = d.pivot_table(index="period", columns="lane", values=col, aggfunc="first").reindex(index=[str(y) for y in years], columns=list(lanes))
    return p.to_numpy(float)


def portfolio_groups(cand: pd.DataFrame) -> dict:
    """Caps of the candidate nodes: per-node POI caps (units), and one group per dominant element (nodes behind one element share PRICE_TAKER_SHARE of its median limit,
    in whole units). Zone fallbacks (appended after the nodes) have no cap."""
    nodes = list(cand["sub"])
    zones = list(L.ZONES)
    struct, _, _ = L.mu_tilde()
    lim = struct.groupby("constraint_name")["median_limit_mw"].median().to_dict()
    caps_units = np.array([POI_CAP_MW[poi_class(kv)] / UNIT_MW for kv in cand["kv_max"]] + [np.inf] * len(zones))
    groups, group_info = [], []
    for el, g in cand.groupby("dominant_element"):
        cap_u = group_cap_units(lim[el])
        groups.append((np.where(cand["dominant_element"].to_numpy() == el)[0], float(cap_u)))
        group_info.append({"element": el, "n_nodes": int(len(g)), "limit_mw": float(lim[el]), "group_cap_mw": cap_u * UNIT_MW,
                           "group_cap_mw_unrounded": PRICE_TAKER_SHARE * float(lim[el]), "nodes": ",".join(g["sub"])})
    return {"nodes": nodes, "zones": zones, "options": nodes + zones, "caps_units": caps_units, "groups": groups, "group_info": pd.DataFrame(group_info), "lim": lim}


def portfolio_inputs():
    """Everything siting.portfolio needs. Options = the candidate nodes, then the 8 zone lanes as a FALLBACK ("an uncongested node in that zone", settled at the
    zone price, no cap): the congested nodes can only absorb a few hundred MW at the price-taker share, and the rest of the fleet has to go somewhere.
    Returns option names, per-option caps (units), the group list (nodes behind one element), and per-rule history and forward values ($/MW-yr)."""
    cand = select_candidates()
    cand = cand[cand["role"] == "candidate"].sort_values("rank").reset_index(drop=True)
    g = portfolio_groups(cand)
    options = g["options"]
    sy = read_csv("site_years.csv")
    fv = pd.read_parquet(RUNS / "forward_values.parquet")
    hist = {a: _hist_matrix(sy, SPEC_2H, options, f"usd_kw_yr_{a}", HISTORY_YEARS) * 1000.0 for a in ALGOS}
    fwd = {a: _fwd_matrix(fv, SPEC_2H, options, f"usd_kw_yr_{a}", PORTFOLIO_YEARS) * 1000.0 for a in ALGOS}
    return {"cand": cand, "nodes": g["nodes"], "zones": g["zones"], "options": options, "caps_units": g["caps_units"], "groups": g["groups"], "group_info": g["group_info"],
            "hist": hist, "fwd": fwd, "sy": sy, "fv": fv}


def run_portfolio() -> pd.DataFrame:
    """data/portfolio.csv (+labels): the fleet each rule A1-A5 builds in 2027-2031 (UNITS_PER_YEAR units of UNIT_MW a year) with group and POI caps.

    Rows: rule, year, sub, MW (added that year), plus option_type (node or zone_fallback), fleet_mw (cumulative), the rule's own value at the option that year, the
    dominant element and the caps. Known values = the rule's own dispatch results at each option in 2021-2026 (backtest), then the forward values as they happen;
    real values = forward values (oracle on the rebuilt 2025 prices x the node's A_i/oracle ratio, scaled by planned upgrades and saturation).
    Also writes derived/runs/portfolio_summary.json: per rule the value per MW-year of the fleet with the caps, with no caps at all, and if every MW had gone to the
    load zones, and how many MW the congested nodes took."""
    import json as _json

    from arena import siting

    check_provenance()
    inp = portfolio_inputs()
    options, nodes, cand = inp["options"], inp["nodes"], inp["cand"]
    n_opt, n_node = len(options), len(nodes)
    y0 = len(HISTORY_YEARS)
    n_years = len(PORTFOLIO_YEARS)
    rows, summary = [], {}
    z_idx = np.arange(n_node, n_opt)
    for rule in PORTFOLIO_RULES:
        known = np.vstack([inp["hist"][rule], inp["fwd"][rule]])
        mk = lambda: np.random.default_rng(PORTFOLIO_SEED)
        p = siting.portfolio(rule, known, known, y0, n_years, UNITS_PER_YEAR, None, inp["caps_units"], mk(), groups=inp["groups"])
        p_free = siting.portfolio(rule, known, known, y0, n_years, UNITS_PER_YEAR, None, None, mk(), groups=None)
        kz = known[:, z_idx]
        p_zone = siting.portfolio(rule, kz, kz, y0, n_years, UNITS_PER_YEAR, None, None, mk())
        fleet = p["fleet"]                                     # (years, options) cumulative units
        added = np.diff(np.vstack([np.zeros((1, n_opt)), fleet]), axis=0)
        for yi, y in enumerate(PORTFOLIO_YEARS):
            for j in np.where(added[yi] > 0)[0]:
                is_node = j < n_node
                rows.append({"rule": rule, "year": y, "sub": options[j], "MW": float(added[yi, j] * UNIT_MW), "option_type": "node" if is_node else "zone_fallback",
                             "fleet_mw": float(fleet[yi, j] * UNIT_MW), "value_usd_kw_yr": float(known[y0 + yi, j] / 1000.0),
                             "dominant_element": cand.at[j, "dominant_element"] if is_node else "", "dominant_side": cand.at[j, "dominant_side"] if is_node else "",
                             "kv_max": float(cand.at[j, "kv_max"]) if is_node else np.nan, "poi_cap_mw": float(inp["caps_units"][j] * UNIT_MW) if is_node else np.nan})
        free_nodes = float(p_free["fleet"][-1][:n_node].sum() * UNIT_MW)
        summary[rule] = {"usd_per_kw_yr_capped": p["usd_per_mw_yr"] / 1000.0, "usd_per_kw_yr_uncapped": p_free["usd_per_mw_yr"] / 1000.0,
                         "usd_per_kw_yr_zones_only": p_zone["usd_per_mw_yr"] / 1000.0, "mw_total_by_2031": float(fleet[-1].sum() * UNIT_MW),
                         "mw_at_nodes_capped": float(fleet[-1][:n_node].sum() * UNIT_MW), "mw_at_zone_fallback": float(fleet[-1][n_node:].sum() * UNIT_MW),
                         "n_nodes_used": int((fleet[-1][:n_node] > 0).sum()), "mw_at_nodes_uncapped": free_nodes,
                         "n_nodes_used_uncapped": int((p_free["fleet"][-1][:n_node] > 0).sum()),
                         "mw_at_top_node_capped": float(fleet[-1][:n_node].max() * UNIT_MW), "mw_at_top_node_uncapped": float(p_free["fleet"][-1][:n_node].max() * UNIT_MW)}
    df = pd.DataFrame(rows)
    df.round(3).to_csv(PORTFOLIO_CSV, index=False)
    write_labels(PORTFOLIO_CSV, PORTFOLIO_LABELS, df)
    inp["group_info"].to_csv(RUNS / "portfolio_groups.csv", index=False)
    (RUNS / "portfolio_summary.json").write_text(_json.dumps(summary, indent=1))
    return df


PORTFOLIO_LABELS = [
    ("rule", "DERIVED", "arena.siting rules A1-A5", "A1 last year's best option, A2 3-year mean plus half the trend, A3 5-year mean minus half the s.d., A4 best of 3 years, A5 recency-weighted and risk-adjusted (the learner)"),
    ("year", "REAL", "brief", "the year the units are added (2027-2031)"),
    ("sub", "REAL", "NP4-160 SUBSTATION / arena zone name", "candidate substation (a new resource node at its highest-kV bus), or a load zone for the fallback"),
    ("MW", "SIM", "arena.siting.portfolio", "MW added at the site that year (units of UNIT_MW = 10 MW)"),
    ("option_type", "DERIVED", "this module", "node = a candidate substation with its caps; zone_fallback = an uncongested node in that load zone (zone price, no cap) that takes the MW the congested nodes cannot"),
    ("fleet_mw", "SIM", "arena.siting.portfolio", "cumulative MW at the site after that year"),
    ("value_usd_kw_yr", "SIM", "forward value: oracle on rebuilt 2025 prices x the node's A_i/oracle ratio", "the rule's own forward value at the site that year, $/kW-yr, before any further price-taker effect of the fleet itself"),
    ("dominant_element", "DERIVED", "data/site_candidates.csv", "the element whose congestion the site mostly earns from; sites on one element share a price-taker cap"),
    ("dominant_side", "DERIVED", "data/site_candidates.csv", ""),
    ("kv_max", "REAL", "NP4-160", ""),
    ("poi_cap_mw", "ASSUMPTION", "POI_CAP_MW by voltage class", "69 kV 50 MW, 138 kV 150 MW, 345 kV 300 MW"),
]


# =================================================================================================================
# 5. Costs, ancillary services and the final table
# =================================================================================================================
WACC = 0.08                              # ASSUMPTION: 8%, the rate the arena uses for break-even installed cost (arena.config.DISCOUNT)
LIFE_YEARS = 15                          # ASSUMPTION: 15-year battery life (NREL TP-6A40-93281 sec. 3, as gridsite.upgrades.BATT_LIFE_YR); the arena's home contract is 12 years
AS_YEARS = (2025, 2026)                  # REAL: brief: report the AS estimate for 2025 and 2026 (to date, annualised)
SITES_FWD_YEARS = (2027, 2029, 2031)     # REAL: brief


def crf(rate: float = WACC, years: int = LIFE_YEARS) -> float:
    """Capital recovery factor: $/yr per $ of upfront cost (DERIVED)."""
    return rate * (1 + rate) ** years / ((1 + rate) ** years - 1)


def as_lines() -> dict:
    """The ancillary-service line, once and system-wide (phase 3a's layer: gridsite.ancillary), for 2025 and 2026 to date, 2 h and 4 h.

    upper_bound: all capacity in the best eligible day-ahead product every interval (a ceiling). estimate: the learner's ledger on each of the 8 zone lanes
    plus the AS it could sell in its idle intervals and by swapping (as_vs_energy; ASSUMPTION-grade, a floor on co-optimisation); reported as the mean and range
    over the 8 zones. AS prices are system-wide, so the line does not differentiate sites."""
    from . import ancillary as A

    z = ensure_lanes()
    out = {}
    for spec in (SPEC_2H, SPEC_4H):
        f = RUNS / f"{spec}_ledgers.npz"
        d = {}
        for y in AS_YEARS:
            try:
                d[y] = {"upper_bound": float(A.as_upper_bound(y, spec, market="DAM")["usd_kw_yr"]), "estimate_mean": np.nan, "estimate_min": np.nan, "estimate_max": np.nan}
            except Exception as ex:
                warnings.warn(f"AS upper bound for {y} / {spec} unavailable ({type(ex).__name__}: {ex}); the column is left empty", stacklevel=2)
                d[y] = {"upper_bound": np.nan, "estimate_mean": np.nan, "estimate_min": np.nan, "estimate_max": np.nan}
        if f.exists():
            res = load_ledgers(spec)
            day = np.array([int(np.where(z["dates"] == dd)[0][0]) for dd in res.dates])
            vals = {y: [] for y in AS_YEARS}
            for ln in L.ZONES:
                i = res.lanes.index(ln)
                r = A.as_vs_energy(z["P"][day][:, z["lanes"].index(ln), :], res.algos["A5"]["energy"][:, i], res.algos["A5"]["wear"][:, i], res.algos["A5"]["mwh"][:, i],
                                   res.dates, spec)
                for y in AS_YEARS:
                    if y in r["by_year"]:
                        vals[y].append(r["by_year"][y]["as_total"])
            for y in AS_YEARS:
                if vals[y]:
                    d[y].update(estimate_mean=float(np.mean(vals[y])), estimate_min=float(np.min(vals[y])), estimate_max=float(np.max(vals[y])))
        out[spec] = d
    return out


def _wide(d: pd.DataFrame, period: str, cols: list, prefix: str) -> pd.DataFrame:
    x = d[d["period"].astype(str) == period].set_index("lane")[cols]
    x.columns = [f"{prefix}_{c}" for c in cols]
    return x


def write_sites() -> pd.DataFrame:
    """data/sites.csv (+labels): one row per candidate site, joined from the screening, the dispatch, the forward run, connection costs, risk and reliability."""
    cand = select_candidates()
    cand = cand[cand["role"] == "candidate"].sort_values("rank").reset_index(drop=True)
    sy = read_csv("site_years.csv")
    s2 = sy[sy["spec"] == SPEC_2H]
    s4 = sy[sy["spec"] == SPEC_4H]
    check_provenance((SPEC_2H, SPEC_4H) if len(s4) else (SPEC_2H,))
    base = cand[["rank", "sub", "kv_max", "county", "arena_zone", "lat", "lon", "match_conf", "node_bus", "node_kv", "existing_resource_node", "score_usd_mw_yr",
                 "dominant_element", "dominant_side"]].rename(columns={"arena_zone": "zone"}).copy()
    for r in range(1, TOP_ELEMENTS_REPORTED + 1):
        base[f"constraint{r}"] = cand[f"el{r}"]
        base[f"constraint{r}_side"] = cand[f"el{r}_side"]
        base[f"constraint{r}_sf"] = cand[f"el{r}_sf"]
        base[f"constraint{r}_sf_method"] = cand[f"el{r}_sf_method"]
        base[f"constraint{r}_hops"] = cand[f"el{r}_hops"]
        base[f"constraint{r}_curtail_flag"] = cand[f"el{r}_curtail_flag"]
        base[f"constraint{r}_has_upgrade"] = cand[f"el{r}_has_upgrade"]
    base = base.set_index("sub")
    # ---- 2 h
    cols = [f"usd_kw_yr_{a}" for a in ALGOS] + ["usd_kw_yr_oracle", "a5_over_oracle"]
    h = base.copy()
    for period, tag in (("mean_2021_25", "mean2125"), ("2025", "y2025"), ("2026", "ytd2026")):
        w = _wide(s2, period, cols, f"h2_{tag}")
        w.columns = [c.replace("usd_kw_yr_", "") for c in w.columns]
        h = h.join(w)
    for period, tag in (("mean_2021_25", "mean2125"), ("2025", "y2025")):
        w = _wide(s2, period, [f"usd_per_mwh_{a}" for a in ALGOS], f"h2_{tag}")
        h = h.join(w)
        w = _wide(s2, period, ["uplift_usd_kw_yr_A5", "uplift_usd_kw_yr_oracle", "uplift_usd_kw_yr_A1"], f"h2_{tag}")
        h = h.join(w)
    h = h.join(_wide(s2, "mean_2023_25", ["a5_over_oracle"], "h2_ratio2325"))
    h = h.join(_wide(s2, "mean_2021_25", ["a5_top_expert"], "h2"))
    # ---- 4 h (where run)
    if len(s4):
        for period, tag in (("mean_2024_25", "mean2425"), ("2025", "y2025")):
            w = _wide(s4, period, [f"usd_kw_yr_{a}" for a in ALGOS] + ["usd_kw_yr_oracle"], f"h4_{tag}")
            w.columns = [c.replace("usd_kw_yr_", "") for c in w.columns]
            h = h.join(w)
        h = h.join(_wide(s4, "2025", ["usd_per_mwh_A5", "uplift_usd_kw_yr_A5", "uplift_usd_kw_yr_oracle"], "h4_y2025"))
        h = h.join(_wide(s4, "mean_2024_25", ["uplift_usd_kw_yr_A5"], "h4_mean2425"))
    # ---- ancillary services (system line)
    asl = as_lines()
    for spec, tag in ((SPEC_2H, "2h"), (SPEC_4H, "4h")):
        for y in AS_YEARS:
            h[f"as_system_estimate_{tag}_{y}"] = asl[spec][y]["estimate_mean"]
            h[f"as_system_upper_bound_{tag}_{y}"] = asl[spec][y]["upper_bound"]
    # ---- forward
    fv = pd.read_parquet(RUNS / "forward_values.parquet")
    f2 = fv[(fv["spec"] == SPEC_2H) & (fv["baseline"] == "both")]
    f2 = f2[f2["role"] == "candidate"]
    for y in SITES_FWD_YEARS:
        g = f2[f2["year"] == y].set_index("lane")
        h[f"fwd_learner_{y}"] = g["learner_usd_kw_yr"]
        h[f"fwd_uplift_learner_{y}"] = g["uplift_learner_usd_kw_yr"]
    g25 = f2[f2["year"] == BASE_YEAR].set_index("lane")
    h["fwd_learner_2025_replay"] = g25["learner_usd_kw_yr"]
    h["fwd_uplift_learner_2025_replay"] = g25["uplift_learner_usd_kw_yr"]
    h["fwd_oracle_2025_replay"] = g25["oracle_usd_kw_yr"]
    mean_fwd = f2[f2["year"].isin(PORTFOLIO_YEARS)].groupby("lane")["learner_usd_kw_yr"].mean()
    h["sat_adjusted_learner_2027_31_mean"] = mean_fwd
    h["fwd_exposure_scale_2031"] = f2[f2["year"] == 2031].set_index("lane")["exposure_weighted_scale"]
    for scen, tag in (("saturation", "sat_only"), ("upgrades", "upgrades_only")):
        g = fv[(fv["spec"] == SPEC_2H) & (fv["baseline"] == scen) & (fv["year"] == 2031) & (fv["role"] == "candidate")].set_index("lane")
        h[f"fwd_learner_2031_{tag}"] = g["learner_usd_kw_yr"]
    hl = pd.read_parquet(RUNS / "forward_halflife.parquet").set_index("lane")
    h["basis_half_life"] = hl["half_life_year"]
    f4 = fv[(fv["spec"] == SPEC_4H) & (fv["baseline"] == "both") & (fv["role"] == "candidate")]
    if len(f4):
        for y in (2027, 2031):
            h[f"h4_fwd_learner_{y}"] = f4[f4["year"] == y].set_index("lane")["learner_usd_kw_yr"]
    # ---- caps and planned upgrade on the dominant element
    struct, _, _ = L.mu_tilde()
    lim = struct.groupby("constraint_name")["median_limit_mw"].median().to_dict()
    h["price_taker_cap_mw"] = [group_cap_units(lim[e]) * UNIT_MW if e in lim else np.nan for e in h["dominant_element"]]
    h["poi_cap_mw"] = [POI_CAP_MW[poi_class(kv)] for kv in h["kv_max"]]
    h["relief_100mw_share_of_limit"] = [REF_BATTERY_MW * abs(sf) / lim.get(e, np.nan) if sf == sf else np.nan for sf, e in zip(h["constraint1_sf"], h["dominant_element"])]
    up = struct.drop_duplicates("constraint_name").set_index("constraint_name")[["upgrade_project_id", "upgrade_in_service_date", "upgrade_match_conf"]]
    h["dominant_upgrade_project"] = h["dominant_element"].map(up["upgrade_project_id"])
    h["dominant_upgrade_date"] = h["dominant_element"].map(up["upgrade_in_service_date"])
    h["dominant_upgrade_match_conf"] = h["dominant_element"].map(up["upgrade_match_conf"])
    # ---- connection
    cc = read_csv("connection_costs.csv").set_index("sub")
    keep = ["connection_class", "class_conf", "gen_tie_km", "cost_low_musd", "cost_mid_musd", "cost_high_musd", "owner_share_musd", "gsu_cost_musd", "all_in_mid_musd",
            "needs_lpt", "lead_time_months", "lead_low_months", "lead_high_months"]
    h = h.join(cc[keep].rename(columns={"connection_class": "conn_class", "class_conf": "conn_class_conf", "gen_tie_km": "conn_gen_tie_km", "cost_low_musd": "conn_cost_low_musd",
                                        "cost_mid_musd": "conn_cost_mid_musd", "cost_high_musd": "conn_cost_high_musd", "owner_share_musd": "conn_owner_share_musd",
                                        "gsu_cost_musd": "conn_gsu_musd", "all_in_mid_musd": "conn_all_in_musd", "needs_lpt": "conn_needs_lpt",
                                        "lead_time_months": "conn_lead_months", "lead_low_months": "conn_lead_low_months", "lead_high_months": "conn_lead_high_months"}))
    h["conn_all_in_usd_per_kw"] = h["conn_all_in_musd"] * 1e6 / (REF_BATTERY_MW * 1000.0)
    h["conn_usd_kw_yr"] = crf() * h["conn_all_in_usd_per_kw"]
    h["net_of_connection_usd_kw_yr"] = h["sat_adjusted_learner_2027_31_mean"] - h["conn_usd_kw_yr"]
    h["net_of_connection_backtest_usd_kw_yr"] = h["h2_mean2125_A5"] - h["conn_usd_kw_yr"]
    h["breakeven_installed_usd_per_kw"] = h["sat_adjusted_learner_2027_31_mean"] / crf()
    h["breakeven_battery_only_usd_per_kw"] = h["breakeven_installed_usd_per_kw"] - h["conn_all_in_usd_per_kw"]
    # ---- risk flags
    rf = read_csv("risk_flags.csv").set_index("sub")
    h = h.join(rf[["flood_flag", "flood_sfha_within_100m", "wildfire_class", "coastal_flag", "seismic_flag", "exclude_flag", "exclude_reason"]].rename(
        columns={"flood_flag": "risk_flood", "flood_sfha_within_100m": "risk_flood_sfha_100m", "wildfire_class": "risk_wildfire_class", "coastal_flag": "risk_coastal",
                 "seismic_flag": "risk_seismic", "exclude_flag": "risk_exclude", "exclude_reason": "risk_exclude_reason"}))
    # ---- reliability (kept separate from market value)
    rl = read_csv("reliability.csv").set_index("sub")
    h = h.join(rl[["reliability_need_score", "need_rank", "main_driver", "voll_usd_yr_duration"]].rename(
        columns={"reliability_need_score": "reliability_need_score", "need_rank": "reliability_need_rank", "main_driver": "reliability_main_driver",
                 "voll_usd_yr_duration": "reliability_voll_usd_yr_duration"}))
    h = h.reset_index()
    h.round(4).to_csv(SITES_CSV, index=False)
    write_labels(SITES_CSV, SITES_LABELS, h)
    return h


_ALGO_LABEL = "SIM: arena dispatch (walk-forward, energy only, after $20/MWh wear) on the rebuilt price lane; $/kW-yr"
SITES_LABELS = [
    ("rank", "DERIVED", "data/site_candidates.csv", "screening rank"),
    ("sub", "REAL", "NP4-160 SUBSTATION", ""),
    ("kv_max", "REAL", "NP4-160", ""),
    ("county", "DERIVED", "data/substations.csv (agent 1c)", "placement"),
    ("zone", "DERIVED", "NP4-160 NOIE zone, else load zone", "the zone control and the weather"),
    ("lat", "DERIVED", "data/substations.csv (agent 1c)", "placement"),
    ("lon", "DERIVED", "data/substations.csv (agent 1c)", "placement"),
    ("match_conf", "DERIVED", "data/substations.csv (agent 1c)", ""),
    ("node_bus", "REAL", "NP4-160", "highest-kV bus: where a new resource node would sit"),
    ("node_kv", "REAL", "NP4-160", ""),
    ("existing_resource_node", "REAL", "NP4-160", "blank if the highest-kV bus has none today"),
    ("score_usd_mw_yr", "DERIVED", "sum_k |SF - SF_zone| x shadow-price-hours", "screening score"),
    ("dominant_element", "DERIVED", "data/site_candidates.csv", ""),
    ("dominant_side", "DERIVED", "data/site_candidates.csv", "import = node price above the zone when the element binds"),
    ("constraint?_side", "DERIVED", "data/site_candidates.csv", "side of the site on the element: the sign of its summed shift-factor-difference x shadow-price-hours exposure to all of the element's constraints. constraint?_sf is the single contingency with the largest contribution, so the two can differ in sign for a weakly coupled slot (4 of 120; never for constraint1)"),
    ("constraint?_sf_method", "DERIVED", "phase 2", "rt_reg or dam_reg"),
    ("constraint?_sf", "DERIVED", "phase 2 sf_sub", "PLAN.md sign convention: SF < 0 = import side"),
    ("constraint?_hops", "DERIVED", "data/hops.csv (phase 2)", "graph hops to the element (blank if not among hops.csv's 40 constraints)"),
    ("constraint?_curtail_flag", "DERIVED", "data/hops.csv (phase 2)", "export side with SF >= 0.05: injecting here loads the constraint"),
    ("constraint?_has_upgrade", "DERIVED", "data/hops.csv (phase 2)", "planned-upgrade match at confidence >= 0.5"),
    ("constraint?", "DERIVED", "data/site_candidates.csv", "the site's 1st / 2nd / 3rd element by contribution to the score"),
    ("h2_mean2125_ratio*", "DERIVED", "A5 / oracle", ""),
    ("h2_ratio2325_a5_over_oracle", "DERIVED", "A5 / oracle, mean 2023-25", "the ratio used for the forward values"),
    ("h2_*_a5_over_oracle", "DERIVED", "A5 / oracle", "the learner's share of the ceiling in that period"),
    ("h2_*_uplift_*", "SIM", "node minus zone control, same run", "what the node adds over the load zone, $/kW-yr (2 h); the oracle version is the uplift of the ceiling"),
    ("h2_*_oracle", "SIM", "arena.battery.oracle_days", "perfect-foresight ceiling on the rebuilt lane, 2 h, $/kW-yr"),
    ("h2_*usd_per_mwh*", "SIM", "profit after wear / MWh discharged", "margin per MWh sold, 2 h"),
    ("h2_a5_top_expert", "SIM", "learner picks", "expert the learner followed most days, 2 h, 2021-25"),
    ("h2_*", "SIM", "arena dispatch on the rebuilt lane", "2 h, $/kW-yr; mean2125 = mean of 2021-25, y2025 = 2025, ytd2026 = 1 Jan - 26 Sep 2026 annualised; A5 is the Adaptive learner"),
    ("h4_*uplift*", "SIM", "node minus zone control, same run", "4 h, $/kW-yr"),
    ("h4_*usd_per_mwh*", "SIM", "profit after wear / MWh discharged", "4 h"),
    ("h4_fwd_learner_*", "SIM", "oracle on rebuilt 2025 prices x the node's A5/oracle ratio (2024-25 for 4 h)", "4 h forward value, $/kW-yr"),
    ("h4_*", "SIM", "arena dispatch on the rebuilt lane", "4 h, $/kW-yr; blank for sites outside the 4 h top 15"),
    ("as_system_estimate_*", "ASSUMPTION", "gridsite.ancillary.as_vs_energy on the 8 zone lanes (learner ledger)", "AS the learner could add in idle intervals and by swaps, $/kW-yr, mean of the 8 zones; system-wide, NOT added to the energy columns"),
    ("as_system_upper_bound_*", "DERIVED", "gridsite.ancillary.as_upper_bound (DAM clearing prices, REAL)", "all capacity in the best eligible product every interval: a ceiling, $/kW-yr"),
    ("fwd_learner_2025_replay", "SIM", "oracle on the rebuilt 2025 prices x the node's A5/oracle ratio", "the oracle-ratio method on the replay year; compare with h2_y2025_A5"),
    ("fwd_oracle_2025_replay", "SIM", "arena.battery.oracle_days", ""),
    ("fwd_uplift_*", "SIM", "learner(node) - learner(zone control), oracle-ratio method", "$/kW-yr"),
    ("fwd_learner_2031_*", "SIM", "as fwd_learner_2031 with only one effect", "saturation only / planned upgrades only"),
    ("fwd_learner_*", "SIM", "oracle on the 2025 replay prices with the shadow prices scaled for the year (planned upgrades, storage saturation) x the A5/oracle ratio", "$/kW-yr; zone prices stay at their 2025 values, so only the basis part moves"),
    ("fwd_exposure_scale_2031", "ASSUMPTION", "gridsite.saturation", "exposure-weighted mean scale left on the site's constraints in 2031 (1 = unchanged)"),
    ("sat_adjusted_learner_2027_31_mean", "SIM", "mean of the forward learner values 2027-2031", "value after planned upgrades and storage saturation, $/kW-yr"),
    ("basis_half_life", "SIM", "first year the learner's uplift falls below 50% of its 2025 value", "or '> 2031'; 2026-2031 evaluated"),
    ("price_taker_cap_mw", "ASSUMPTION", "PRICE_TAKER_SHARE x the median limit of the dominant element, in whole 10 MW units rounded down, at least MIN_GROUP_UNITS", "shared by every candidate on that element; a 38 MW or 99 MW limit still gets one unit (10 MW)"),
    ("poi_cap_mw", "ASSUMPTION", "POI_CAP_MW by voltage class", ""),
    ("relief_100mw_share_of_limit", "DERIVED", "REF_BATTERY_MW (100) x |SF| to the dominant element / its median limit", "saturation indicator: the share of the element's limit that a 100 MW battery here could offset; near or above 1 the congestion value does not survive a battery of that size"),
    ("dominant_upgrade_project", "DERIVED", "data/constraints.csv (phase 2, TPIT / RTP match)", "a name or bus overlap, not proof of relief. The forward clock uses agent 4's reviewed schedule (confidence 0.5 or more and a date), which can differ: 587__A is 1 Dec 2032 at 0.6 there"),
    ("dominant_upgrade_date", "DERIVED", "data/constraints.csv", ""),
    ("dominant_upgrade_match_conf", "DERIVED", "data/constraints.csv", ""),
    ("conn_class", "DERIVED", "data/connection_costs.csv (phase 3a)", "new_bay / new_switchyard / gen_tie"),
    ("conn_class_conf", "DERIVED", "data/connection_costs.csv", "0.3 = default class; filter >= 0.5 for a ranking"),
    ("conn_gen_tie_km", "DERIVED", "data/connection_costs.csv", ""),
    ("conn_cost_*", "DERIVED", "data/connection_costs.csv (MISO MTEP24 unit costs, 2024 USD)", "interconnection facilities, 100 MW reference battery, $M"),
    ("conn_owner_share_musd", "DERIVED", "data/connection_costs.csv (16 TAC 25.195(f)(3) allowance)", "facilities cost above the allowance, $M"),
    ("conn_gsu_musd", "ASSUMPTION", "data/connection_costs.csv", "step-up transformer, UNVERIFIED proxy"),
    ("conn_all_in_musd", "DERIVED", "data/connection_costs.csv", "facilities + step-up, $M, 100 MW"),
    ("conn_needs_lpt", "DERIVED", "data/connection_costs.csv", ""),
    ("conn_lead_*", "DERIVED", "data/connection_costs.csv", "months: FIS + longer of construction and step-up delivery"),
    ("conn_all_in_usd_per_kw", "DERIVED", "all-in / 100 MW", "$/kW"),
    ("conn_usd_kw_yr", "ASSUMPTION", "all-in $/kW x CRF(WACC, LIFE_YEARS)", "annualised connection cost, $/kW-yr; WACC 8%, 15 years"),
    ("net_of_connection_usd_kw_yr", "SIM", "sat_adjusted_learner_2027_31_mean - conn_usd_kw_yr", "energy only, before the battery's own capex"),
    ("net_of_connection_backtest_usd_kw_yr", "SIM", "h2_mean2125_A5 - conn_usd_kw_yr", ""),
    ("breakeven_installed_usd_per_kw", "SIM", "sat_adjusted_learner_2027_31_mean / CRF", "total installed cost (battery + connection) the energy value repays over LIFE_YEARS at WACC"),
    ("breakeven_battery_only_usd_per_kw", "SIM", "breakeven_installed - conn_all_in", "what is left for the battery itself after connection and step-up"),
    ("risk_*", "DERIVED", "data/risk_flags.csv (phase 5)", "physical-risk flags and exclusion reason"),
    ("reliability_*", "DERIVED", "data/reliability.csv (phase 5)", "need score, rank and the VoLL dollars: reliability value in its own units, never added to market value"),
]


# =================================================================================================================
# 6. Report tables (every number in notes/phase3.md comes from here)
# =================================================================================================================
def arena_zone_reference(years=BACKTEST_YEARS) -> pd.DataFrame:
    """The arena's own stored zone backtest (history from 2011): mean $/kW-yr of `years` per zone and algorithm, for the 2 h grid spec."""
    from arena.backtest import load_backtest

    bt = load_backtest("ERCOT", "grid")
    yrs = bt["years"]
    zones = bt["zones"]
    out = {}
    for a in list(ALGOS) + ["ORACLE"]:
        prof = bt["algos"][a]["profit"]
        ann = np.mean([prof[yrs == y].sum(0) / 1000.0 for y in years], axis=0)
        out["oracle" if a == "ORACLE" else a] = ann
    return pd.DataFrame(out, index=zones)


def report_tables() -> dict:
    """Tables for the notes, as DataFrames (printed by `run_grid.py sites`)."""
    sy = read_csv("site_years.csv")
    s2 = sy[sy["spec"] == SPEC_2H]
    out = {}
    m = s2[s2["period"].astype(str) == "mean_2021_25"].set_index("lane")
    ctrl = m[m["role"] == "control_zone"]
    cand = m[m["role"] == "candidate"]
    ref = arena_zone_reference()
    t = pd.DataFrame({f"mine_{a}": ctrl[f"usd_kw_yr_{a}"] for a in ALGOS} | {"mine_oracle": ctrl["usd_kw_yr_oracle"]})
    for a in ALGOS + ("oracle",):
        t[f"arena_{a}"] = ref[a].reindex(t.index)
    t.loc["MEAN"] = t.mean()
    out["controls_vs_arena"] = t
    # algorithms at nodes vs zones
    rows = []
    for nm, g in (("zone controls (8)", ctrl), ("candidate nodes (40)", cand)):
        r = {"lanes": nm}
        for a in ALGOS:
            r[a] = g[f"usd_kw_yr_{a}"].mean()
        r["oracle"] = g["usd_kw_yr_oracle"].mean()
        r["A5_over_oracle"] = g["usd_kw_yr_A5"].mean() / g["usd_kw_yr_oracle"].mean()
        for a in ALGOS:
            r[f"usd_per_mwh_{a}"] = g[f"usd_per_mwh_{a}"].mean()
        rows.append(r)
    out["algorithms_nodes_vs_zones_2021_25"] = pd.DataFrame(rows).set_index("lanes")
    y25 = s2[s2["period"].astype(str) == "2025"].set_index("lane")
    rows = []
    for nm, g in (("zone controls (8)", y25[y25["role"] == "control_zone"]), ("candidate nodes (40)", y25[y25["role"] == "candidate"])):
        r = {"lanes": nm}
        for a in ALGOS:
            r[a] = g[f"usd_kw_yr_{a}"].mean()
        r["oracle"] = g["usd_kw_yr_oracle"].mean()
        rows.append(r)
    out["algorithms_nodes_vs_zones_2025"] = pd.DataFrame(rows).set_index("lanes")
    # uplift by algorithm
    up = pd.DataFrame({a: cand[f"uplift_usd_kw_yr_{a}"] for a in ALGOS} | {"oracle": cand["uplift_usd_kw_yr_oracle"]})
    out["uplift_summary_2021_25"] = up.describe().T[["mean", "50%", "min", "max"]].rename(columns={"50%": "median"})
    best = cand[[f"usd_kw_yr_{a}" for a in ALGOS]].idxmax(axis=1).str.replace("usd_kw_yr_", "").value_counts()
    bestz = ctrl[[f"usd_kw_yr_{a}" for a in ALGOS]].idxmax(axis=1).str.replace("usd_kw_yr_", "").value_counts()
    out["best_algorithm_count"] = pd.DataFrame({"nodes": best, "zones": bestz}).fillna(0).astype(int)
    # candidate details
    cd = select_candidates()
    cd = cd[cd["role"] == "candidate"].set_index("sub")
    d = cand[["zone", "rank", "usd_kw_yr_A5", "usd_kw_yr_oracle", "a5_over_oracle", "uplift_usd_kw_yr_A5", "usd_per_mwh_A5", "a5_top_expert"]].copy()
    d = d.join(y25[["usd_kw_yr_A5", "uplift_usd_kw_yr_A5"]].rename(columns={"usd_kw_yr_A5": "A5_2025", "uplift_usd_kw_yr_A5": "uplift_2025"}))
    d = d.join(cd[["kv_max", "dominant_element", "dominant_side", "dominant_sf", "dominant_hops", "score_usd_mw_yr"]])
    out["candidates"] = d.sort_values("usd_kw_yr_A5", ascending=False)
    # spike hunter in pockets: A4 and A5 versus kV, side, basis
    d2 = cand[["usd_kw_yr_A1", "usd_kw_yr_A4", "usd_kw_yr_A5", "uplift_usd_kw_yr_A4", "uplift_usd_kw_yr_A5", "usd_per_mwh_A4"]].join(cd[["kv_max", "dominant_side"]])
    d2["a4_minus_a5"] = d2["usd_kw_yr_A4"] - d2["usd_kw_yr_A5"]
    out["spike_hunter_by_side"] = d2.groupby("dominant_side")[["usd_kw_yr_A4", "usd_kw_yr_A5", "a4_minus_a5", "uplift_usd_kw_yr_A4", "uplift_usd_kw_yr_A5", "usd_per_mwh_A4"]].mean()
    d2["kv_class"] = d2["kv_max"].map(poi_class)
    out["spike_hunter_by_kv"] = d2.groupby("kv_class")[["usd_kw_yr_A4", "usd_kw_yr_A5", "a4_minus_a5", "uplift_usd_kw_yr_A4", "uplift_usd_kw_yr_A5"]].mean()
    # learner's expert shares
    res = load_ledgers(SPEC_2H)
    sh = pd.DataFrame(res.pick_shares(), index=res.lanes, columns=EXPERT_NAMES)
    out["learner_expert_share"] = pd.DataFrame({"zone controls": sh.loc[list(L.ZONES)].mean(), "candidate nodes": sh.drop(list(L.ZONES)).mean()})
    return out


def print_report() -> None:
    pd.set_option("display.width", 250)
    pd.set_option("display.max_columns", 40)
    pd.set_option("display.max_rows", 100)
    for k, v in report_tables().items():
        print(f"\n=== {k}")
        print(v.round(2).to_string())
