"""R2: how much do the rankings depend on the congestion-anchor choice? Sensitivity runs of revision 2 (oracle-ratio method, SIM).

Variants (the environment switches of gridsite.rev2, set in a child process; the primary is the structure the dispatch ran on):

  primary         2026 RT-rent anchors, R1's structural rows without the grade-D Panhandle pattern
  geo_only        S0: the revision-1 structure on G1's geography (GRIDSITE_REVISION=1: no R1 rows, no 2026 anchors), to separate what G1's fixes did from what R1 and the anchors did
  anchors2025     S1: no 2026 re-anchoring (2025 IMM rent, else the persistence-discounted window; R1's rows still go in)
  anchors2026da   S2: S1's opposite: also anchor on the two day-ahead-only rents (SEA_AAT1, MDOPHR99_A), the literal reading of imm_2026_anchors.csv
  panhandle       S3: the primary plus R1's grade-D Panhandle pattern
  fw6945          S4: keep 6945__A (MGSES-CATSW, re-anchored on its 2026 rent) and leave out R1's Far West pattern that duplicates it

Method (the label that goes with every number): a year's learner value of a lane is the perfect-foresight oracle on the lane rebuilt for that year times the lane's own A5/oracle
ratio of the primary dispatch (2023-25 mean); the 2021-25 mean of that is the oracle-ratio estimate of the dispatch mean. On the primary it reproduces the dispatch mean with the
error that `combine` reports. A lane whose first-order exposure to the constraints that differ between the variant and the primary (sum of |DSF x shadow-price-hours| differences x the
2 h capture factor) is below FO_MIN_USD ($0.25/kW-yr) keeps the primary's value and is not recomputed: an approximation, listed per lane in derived/runs/anchor_sens_<variant>_screen.csv.
Nothing here writes a cache that the primary reads except derived/runs/value_cache.json, whose keys contain the structure hash.

  python -m gridsite.anchor_sens child <variant> [workers]     (run by `run_grid.py anchor-sensitivity`; reads the switches from the environment)
"""
from __future__ import annotations

import os
import subprocess
import sys
import time

import numpy as np
import pandas as pd

from . import paths

REPO_DATA = paths.REPO_DATA
RUNS = paths.DERIVED / "runs"
OUT_CSV = REPO_DATA / "anchor_sensitivity.csv"
SUMMARY_CSV = REPO_DATA / "anchor_sensitivity_summary.csv"
YEARS = (2021, 2022, 2023, 2024, 2025)          # REAL: the backtest years of the 2 h run (sites.BACKTEST_YEARS)
TOP_N = 15                                       # REAL: brief: the top 15 sites
FO_MIN_USD = 0.25                                # ASSUMPTION: a lane whose first-order exposure to the constraints that differ from the primary's moves by less than this, $/kW-yr, keeps the primary's value (not recomputed)
CAPTURE_2H = 0.205                               # REAL: Phase 4's capture factor of a 2 h battery (gridsite.upgrades.CAPTURE_FACTOR), used only for the first-order screen of which lanes to recompute
VARIANTS = {
    "geo_only": dict(GRIDSITE_REVISION="1"),
    "anchors2025": dict(GRIDSITE_ANCHORS="2025"),
    "anchors2026da": dict(GRIDSITE_ANCHORS="2026da"),
    "panhandle": dict(GRIDSITE_PANHANDLE="1"),
    "fw6945": dict(GRIDSITE_FAR_WEST="6945"),
}
VARIANT_TEXT = {
    "geo_only": "S0: revision-1 structure on G1's geography and the refreshed risk flags (no R1 rows, no 2026 anchors)",
    "primary": "primary: 2026 RT-rent anchors, R1 rows without the grade-D Panhandle pattern",
    "anchors2025": "S1: 2025-only anchors (no 2026 re-anchoring)",
    "anchors2026da": "S2: also anchor on the day-ahead-only 2026 rents (SEA_AAT1, MDOPHR99_A)",
    "panhandle": "S3: with R1's grade-D Panhandle pattern",
    "fw6945": "S4: 6945__A re-anchored and kept, R1's Far West pattern left out",
}


def _read(name: str) -> pd.DataFrame:
    return pd.read_csv(REPO_DATA / name, keep_default_na=False, na_values=[""], low_memory=False)


def primary_oracle_ratio() -> pd.DataFrame:
    """Per lane and year 2021-25: the primary dispatch's perfect-foresight oracle ($/kW-yr), the lane's A5/oracle ratio (2023-25) and their product."""
    from . import sites as S

    o = S.load_oracle(S.SPEC_2H)
    years = np.array([int(d[:4]) for d in o["dates"]])
    rat = S.dispatch_ratios(S.SPEC_2H)["ratio_A5"]
    rows = []
    for y in YEARS:
        ann = S._annual(o["oracle"], years, y)
        for ln, v in zip(o["lanes"], ann):
            rows.append(dict(lane=ln, year=y, oracle=float(v), ratio=float(rat.get(ln, np.nan))))
    d = pd.DataFrame(rows)
    d["learner"] = d["oracle"] * d["ratio"]
    return d


def screen_lanes(variant: str) -> tuple:
    """Which lanes does the variant change by at least FO_MIN_USD (first order)? Returns (lanes, first-order change per lane, recompute mask, constraints that differ)."""
    from . import nodal_lanes as L
    from . import rev2
    from . import sites as S

    prim = pd.read_csv(REPO_DATA / rev2.EFFECTIVE_CSV, keep_default_na=False, na_values=[""], low_memory=False)
    var = L.constraint_structure()
    lanes = [x for x in S.lane_subs()]
    Mp = L.sub_sf_matrix(lanes, prim)
    Mv = L.sub_sf_matrix(lanes, var)
    # first-order change of each lane's exposure: sum over constraints of |DSF x sph (variant) - DSF x sph (primary)| x capture / 1000, $/kW-yr (a constraint absent on one side counts in full)
    Ep = pd.DataFrame(Mp["DSF"] * prim["sph_usd_mw_yr"].to_numpy(float)[None, :], index=lanes, columns=list(prim["cid"]))
    Ev = pd.DataFrame(Mv["DSF"] * var["sph_usd_mw_yr"].to_numpy(float)[None, :], index=lanes, columns=list(var["cid"]))
    allc = sorted(set(Ep.columns) | set(Ev.columns))
    d_fo = (Ev.reindex(columns=allc).fillna(0.0) - Ep.reindex(columns=allc).fillna(0.0)).abs().sum(1).to_numpy() * CAPTURE_2H / 1000.0
    changed = [c for c in allc if (c not in Ep.columns) or (c not in Ev.columns) or abs(float(Ev[c].abs().sum()) - float(Ep[c].abs().sum())) > 1e-6]
    hit = d_fo >= FO_MIN_USD
    if rev2.REVISION == "1":
        hit[:] = True                                       # the revision-1 module has no structural shift factors to compare with the primary's: value every lane
    return lanes, d_fo, hit, changed


def run_child(variant: str, workers: int = 4) -> pd.DataFrame:
    """Value the lanes that the variant changes, for 2021-25, in this process (the switches are already in the environment). Writes derived/runs/anchor_sens_<variant>.csv."""
    from . import nodal_lanes as L
    from . import rev2
    from . import sites as S

    L.READ_ONLY = True
    t0 = time.time()
    print(f"variant {variant}: {rev2.config()}", flush=True)
    lanes, d_fo, hit, changed = screen_lanes(variant)
    affected = [ln for ln, h in zip(lanes, hit) if h]
    RUNS.mkdir(parents=True, exist_ok=True)
    pd.DataFrame({"lane": lanes, "first_order_change_usd_kw_yr": d_fo, "recomputed": hit}).to_csv(RUNS / f"anchor_sens_{variant}_screen.csv", index=False)
    print(f"  constraints that differ {len(changed)} ({changed[:6]}...), lanes recomputed {len(affected)} of {len(lanes)} (first-order change >= ${FO_MIN_USD}/kW-yr)", flush=True)
    vs = S.value_sites(affected, spec=S.SPEC_2H, years=YEARS, baseline="none", workers=workers) if affected else pd.DataFrame()
    out = vs[["lane", "year", "oracle_usd_kw_yr", "learner_usd_kw_yr", "zone", "zone_learner_usd_kw_yr", "ratio_A5"]].rename(
        columns={"oracle_usd_kw_yr": "oracle", "learner_usd_kw_yr": "learner", "ratio_A5": "ratio"}) if len(vs) else pd.DataFrame(columns=["lane", "year", "oracle", "learner", "zone", "zone_learner_usd_kw_yr", "ratio"])
    out["variant"] = variant
    out.to_csv(RUNS / f"anchor_sens_{variant}.csv", index=False)
    print(f"  done in {time.time() - t0:.0f}s -> anchor_sens_{variant}.csv", flush=True)
    return out


def run_variants(variants: list, workers: int = 4) -> None:
    """Run each variant in a fresh process with its switches in the environment."""
    grid = str(paths.GRID_DIR)
    for v in variants:
        env = dict(os.environ)
        for k in ("GRIDSITE_ANCHORS", "GRIDSITE_PANHANDLE", "GRIDSITE_FAR_WEST", "GRIDSITE_REVISION"):
            env.pop(k, None)
        env.update(VARIANTS[v])
        env["PYTHONIOENCODING"] = "utf-8"
        r = subprocess.run([sys.executable, "-u", "-m", "gridsite.anchor_sens", "child", v, str(workers)], cwd=grid, env=env)
        if r.returncode != 0:
            raise RuntimeError(f"variant {v} failed with status {r.returncode}")


def combine() -> pd.DataFrame:
    """data/anchor_sensitivity.csv: one row per lane, the primary dispatch value, the oracle-ratio value under every variant, ranks among the 40 and top-15 membership."""
    from . import sites as S

    cand = S.select_candidates()
    cand = cand[cand["role"].isin(("candidate",) + S.EXTRA_ROLES)]
    prim = primary_oracle_ratio()
    per = {"primary": prim.pivot_table(index="lane", columns="year", values="learner")}
    for v in VARIANTS:
        f = RUNS / f"anchor_sens_{v}.csv"
        if not f.exists():
            continue
        d = pd.read_csv(f, keep_default_na=False, na_values=[""])
        t = per["primary"].copy()
        if len(d):
            t.loc[d["lane"].unique()] = d.pivot_table(index="lane", columns="year", values="learner").reindex(d["lane"].unique()).to_numpy()
        per[v] = t
    sy = S.read_csv("site_years.csv")
    sy = sy[(sy["spec"] == S.SPEC_2H) & (sy["period"] == "mean_2021_25")].set_index("lane")
    sy25 = sy.copy()
    s25 = S.read_csv("site_years.csv")
    s25 = s25[(s25["spec"] == S.SPEC_2H) & (s25["period"] == "2025")].set_index("lane")
    out = cand[["role", "rank", "sub"]].rename(columns={"sub": "lane", "rank": "group_rank"}).reset_index(drop=True)
    out["zone"] = out["lane"].map(sy["zone"])
    out["dispatch_mean_2021_25"] = out["lane"].map(sy["usd_kw_yr_A5"])
    out["dispatch_2025"] = out["lane"].map(s25["usd_kw_yr_A5"])
    r1 = S.read_csv(S.REV1_LIST_CSV).set_index("sub")
    out["rev1_dispatch_mean_2021_25"] = out["lane"].map(r1["a5_2021_25_rev1"])
    out["rev1_rank_of_40"] = out["rev1_dispatch_mean_2021_25"].rank(ascending=False, method="min")
    for v, t in per.items():
        out[f"orv_{v}_mean_2021_25"] = out["lane"].map(t.mean(1))
        out[f"orv_{v}_2025"] = out["lane"].map(t[2025])
    main = out["role"] == "candidate"
    out["dispatch_rank_of_40"] = np.nan
    out.loc[main, "dispatch_rank_of_40"] = out.loc[main, "dispatch_mean_2021_25"].rank(ascending=False, method="min")
    for v in per:
        col = f"orv_{v}_mean_2021_25"
        out[f"rank_of_40_{v}"] = np.nan
        out.loc[main, f"rank_of_40_{v}"] = out.loc[main, col].rank(ascending=False, method="min")
        out[f"top{TOP_N}_{v}"] = (out[f"rank_of_40_{v}"] <= TOP_N) & main
    out[f"top{TOP_N}_dispatch"] = (out["dispatch_rank_of_40"] <= TOP_N) & main
    var_cols = [f"top{TOP_N}_{v}" for v in per]
    out["top15_depends_on_anchor"] = main & (out[var_cols].any(axis=1) & ~out[var_cols].all(axis=1))
    out["top15_in_which"] = [",".join(v for v in per if r[f"top{TOP_N}_{v}"]) if m else "" for (_, r), m in zip(out.iterrows(), main)]
    num = out.select_dtypes("number").columns
    out[num] = out[num].round(3)
    out.to_csv(OUT_CSV, index=False)
    _write_labels(out)
    # one row per variant: rank correlation with the primary, how many lanes changed, top-15 overlap, method error on the primary
    from scipy.stats import spearmanr

    rows = []
    m = out[main]
    err = (m["orv_primary_mean_2021_25"] - m["dispatch_mean_2021_25"])
    for v in per:
        d = m[f"orv_{v}_mean_2021_25"] - m["orv_primary_mean_2021_25"]
        top_v = set(m.loc[m[f"top{TOP_N}_{v}"], "lane"])
        top_p = set(m.loc[m[f"top{TOP_N}_primary"], "lane"])
        rows.append(dict(variant=v, description=VARIANT_TEXT[v], n_lanes_changed_of_all=int((out[f"orv_{v}_mean_2021_25"] - out["orv_primary_mean_2021_25"]).abs().gt(0.05).sum()),
                         n_changed_of_40=int((d.abs() > 0.05).sum()), rho_vs_primary_40=float(spearmanr(m[f"orv_{v}_mean_2021_25"], m["orv_primary_mean_2021_25"]).statistic),
                         top15_overlap=len(top_v & top_p), top15_entering=",".join(sorted(top_v - top_p)), top15_leaving=",".join(sorted(top_p - top_v)),
                         mean_change_of_40=float(d.mean()), max_gain=float(d.max()), max_gain_lane=m.loc[d.idxmax(), "lane"], max_loss=float(d.min()), max_loss_lane=m.loc[d.idxmin(), "lane"],
                         method_mae_vs_dispatch_primary=float(err.abs().mean()), method_rho_vs_dispatch_primary=float(spearmanr(m["orv_primary_mean_2021_25"], m["dispatch_mean_2021_25"]).statistic)))
    sm = pd.DataFrame(rows)
    num = sm.select_dtypes("number").columns
    sm[num] = sm[num].round(3)
    sm.to_csv(SUMMARY_CSV, index=False)
    S.write_labels(SUMMARY_CSV, SUMMARY_LABELS, sm)
    return out


def _write_labels(out: pd.DataFrame) -> None:
    from . import sites as S

    spec = [
        ("lane", "REAL", "NP4-160 SUBSTATION", "the substation lane"),
        ("role", "DERIVED", "data/site_candidates.csv", "candidate (the 40), regional (R1's picks) or rev1_only"),
        ("group_rank", "DERIVED", "data/site_candidates.csv", "rank inside the group at selection"),
        ("zone", "DERIVED", "data/site_years.csv", ""),
        ("dispatch_mean_2021_25", "SIM", "data/site_years.csv (primary dispatch, A5)", "2 h learner, $/kW-yr, 2021-25 mean: the number sites.csv ranks on"),
        ("dispatch_2025", "SIM", "data/site_years.csv", "2 h learner, 2025"),
        ("dispatch_rank_of_40", "DERIVED", "dispatch_mean_2021_25", "rank among the 40 candidates"),
        ("rev1_dispatch_mean_2021_25", "SIM", "data/site_candidates_rev1.csv (revision 1 as committed)", "2 h learner, 2021-25 mean in revision 1, $/kW-yr; blank for a site that was not one of the revision-1 40"),
        ("rev1_rank_of_40", "DERIVED", "rev1_dispatch_mean_2021_25", "rank among the revision-1 40"),
        ("orv_*_mean_2021_25", "SIM", "oracle x the lane's A5/oracle ratio (2023-25), mean of 2021-25", "the oracle-ratio estimate of the dispatch mean under that variant, $/kW-yr; labelled so: not a dispatch run"),
        ("orv_*_2025", "SIM", "oracle x ratio, 2025", "same, 2025 only"),
        ("rank_of_40_*", "DERIVED", "orv_*_mean_2021_25", "rank among the 40 candidates under that variant"),
        ("top15_dispatch", "DERIVED", "dispatch_rank_of_40", "in the top 15 of the 40 by the primary dispatch"),
        ("top15_depends_on_anchor", "DERIVED", "this module", "True if the site is in the top 15 under some variants and not under others"),
        ("top15_in_which", "DERIVED", "this module", "the variants under which the site is in the top 15"),
        ("top15_*", "DERIVED", "rank_of_40_*", "in the top 15 of the 40 under that variant (oracle-ratio footing)"),
    ]
    S.write_labels(OUT_CSV, spec, out)


SUMMARY_LABELS = [
    ("variant", "DERIVED", "gridsite.anchor_sens", "primary or the sensitivity variant"),
    ("description", "DERIVED", "gridsite.anchor_sens", ""),
    ("n_lanes_changed_of_all", "DERIVED", "oracle-ratio values", "lanes (of all valued) whose 2021-25 mean moves by more than $0.05/kW-yr against the primary"),
    ("n_changed_of_40", "DERIVED", "oracle-ratio values", "same, of the 40 candidates"),
    ("rho_vs_primary_40", "SIM", "Spearman", "rank correlation of the 40 candidates' oracle-ratio values with the primary's"),
    ("top15_overlap", "DERIVED", "oracle-ratio values", "of the primary's top 15 (of the 40), how many are in the variant's top 15"),
    ("top15_entering", "DERIVED", "oracle-ratio values", "sites in the variant's top 15 that are not in the primary's"),
    ("top15_leaving", "DERIVED", "oracle-ratio values", "sites in the primary's top 15 that are not in the variant's"),
    ("mean_change_of_40", "SIM", "oracle-ratio values", "mean change of the 40 candidates' value against the primary, $/kW-yr"),
    ("max_gain", "SIM", "oracle-ratio values", "largest gain against the primary among the 40, $/kW-yr"),
    ("max_gain_lane", "DERIVED", "oracle-ratio values", ""),
    ("max_loss", "SIM", "oracle-ratio values", "largest loss (negative) among the 40, $/kW-yr"),
    ("max_loss_lane", "DERIVED", "oracle-ratio values", ""),
    ("method_mae_vs_dispatch_primary", "SIM", "oracle x ratio against the dispatch, primary", "mean absolute error of the oracle-ratio method on the primary's 40 candidates, $/kW-yr (what the sensitivity numbers inherit)"),
    ("method_rho_vs_dispatch_primary", "SIM", "Spearman", "rank correlation of the method with the dispatch, primary, 40 candidates"),
]


if __name__ == "__main__":
    if len(sys.argv) >= 3 and sys.argv[1] == "child":
        run_child(sys.argv[2], int(sys.argv[3]) if len(sys.argv) > 3 else 4)
