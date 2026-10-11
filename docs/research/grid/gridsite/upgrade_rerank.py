"""Phase 6a: rerank the 40 candidate sites after each upgrade, with the full dispatch valuation (data/upgrade_rerank.csv).

Phase 4 ranked the effect of an upgrade to first order (data/upgrade_rerank_first_order.csv: a site's value is the sum over constraints of
|SF - SF_zone| x shadow-price-hours x a fixed capture factor). This module does it with the real thing: for each of the top 10 upgrades of data/upgrades.csv
(U01..U10) and two bundles of planned upgrades, the shadow prices of the replayed congestion structure are scaled by `gridsite.upgrades.apply_upgrade`, the
price lane of every one of the 40 candidates is rebuilt, and `gridsite.sites.value_sites` values it again (the perfect-foresight oracle on the rebuilt 2025 lane
times the lane's own learner/oracle ratio of the 2023-25 backtest, 2 h battery). Everything is SIM: a node's price is a model (notes/phase3.md).

What it does not touch: zone prices. They are real, and already contain the congestion of the zone; an upgrade removes only the node-minus-zone part
(the formula of nodal_lanes: p_node = p_zone - sum_k (SF[n,k] - SF_zone[k]) x mu_k).

Run: run_grid.py rerank [workers=3] [skip-first-order]
"""
from __future__ import annotations

import json
import time

import numpy as np
import pandas as pd

from . import paths
from . import sites as S

REPO_DATA = paths.REPO_DATA
OUT_CSV = REPO_DATA / "upgrade_rerank.csv"
FIRST_ORDER_CSV = REPO_DATA / "upgrade_rerank_first_order.csv"
RUNS6 = paths.DERIVED / "runs6"

# =================================================================================================================
# Constants (unverified ones are labelled; none is inlined)
# =================================================================================================================
SPEC = "grid"                      # REAL: brief: the 2 h battery (arena spec 'grid', 1 MW / 2 MWh)
YEAR = 2025                        # REAL: brief: valued at 2025, the replay year of the congestion structure (nodal_lanes.REPLAY_YEAR)
UPGRADE_IDS = tuple(f"U{i:02d}" for i in range(1, 11))   # REAL: brief: the top 10 upgrades of data/upgrades.csv
PLANNED_LAST_YEAR = 2031           # REAL: brief: the planned bundle holds every planned upgrade in service by 2031
BUNDLE_BY_2031 = "PLANNED_2031"    # DERIVED: scenario id of that bundle
BUNDLE_ALL = "PLANNED_ALL"         # DERIVED: scenario id of the same bundle with the 2032+ projects as well (Phase 4's PLANNED_ALL, for the comparison)
MATERIAL_USD_KW_YR = 1.0           # ASSUMPTION: a change of a site's value below this ($/kW-yr, about 2% of a typical node) is called "unchanged"; sites off the upgraded element still move by cents through their zone terms
WORKERS_DEFAULT = 3                # REAL: brief: at most 4 workers; about 2 GB of RAM free, so 3
MIN_COMPARE = 5                    # ASSUMPTION: a rank correlation is reported only with at least this many sites that change in either valuation
GROUP_CAP_NOTE = "S.PRICE_TAKER_SHARE x median limit of the dominant element, whole 10 MW units (phase 3b)"   # DERIVED: the group caps of the 200 MW portfolio


# =================================================================================================================
# Scenarios
# =================================================================================================================
def _upgrades() -> pd.DataFrame:
    return pd.read_csv(REPO_DATA / "upgrades.csv", keep_default_na=False, na_values=[""])


def planned_bundle(last_year: int | None = PLANNED_LAST_YEAR) -> tuple[dict, pd.DataFrame]:
    """{cid: mu_scale} of the planned upgrades that are new against the 2025 replay, and the schedule rows behind it.

    Starts from gridsite.upgrades.planned_schedule() (matches at confidence >= 0.5) restricted by gridsite.saturation.planned_upgrades to the replayed constraints with
    a date, then applies the two rules of saturation.upgrade_scale: a project in service by the end of 2025 is already in the 2025 market data, and for a constraint whose
    target comes from the Oct 2026 window a project in service by then is already in that window. `last_year` keeps projects in service by 31 Dec of that year
    (None = every dated project). Every project takes its residual in full (0.10 line / transformer, 0.50 interface): the bundle is the 'all done' state, not a ramp."""
    from . import nodal_lanes as L
    from . import saturation as SA
    from . import upgrades as U

    struct, _, _ = L.mu_tilde()
    sched, _ = SA.planned_upgrades(struct)
    base_end = pd.Timestamp(f"{SA.BASE_YEAR}-12-31")
    windowed = set(struct.loc[struct["sph_basis"].astype(str).str.startswith("window"), "cid"])
    keep = (sched["isd"] > base_end) & ~(sched["cid"].isin(windowed) & (sched["isd"] <= SA.AS_OF))
    if last_year is not None:
        keep &= sched["isd"] <= pd.Timestamp(f"{last_year}-12-31")
    rows = sched[keep].sort_values(["isd", "cid"]).reset_index(drop=True)
    return U.apply_upgrade(list(rows["cid"]), expand_element=False), rows


def scenario_list() -> list[dict]:
    """The 12 scenarios: id, label, upgrade type, cost and lead time of the wires fix, the mu_scale and what is behind it."""
    from . import upgrades as U

    up = _upgrades().set_index("upgrade_id")
    out = []
    for uid in UPGRADE_IDS:
        r = up.loc[uid]
        ms = U.apply_upgrade(uid)
        out.append(dict(
            scenario=uid, label=f"{r['element']}", element_key=str(r["element_key"]), element_type=str(r["element_type"]), upgrade_type=str(r["upgrade_type"]),
            cost_mid_musd=float(r["cost_mid_musd"]), lead_time_mid_months=float(r["lead_time_mid_months"]), planned=str(r["planned"]), planned_isd=str(r["planned_isd"]) if pd.notna(r["planned_isd"]) else "",
            nwa_vs_wires_ratio=float(r["nwa_vs_wires_ratio"]) if pd.notna(r["nwa_vs_wires_ratio"]) else np.nan,
            nwa_vs_cheapest_ratio=float(r["nwa_vs_cheapest_ratio"]) if pd.notna(r["nwa_vs_cheapest_ratio"]) else np.nan,
            mu_scale=ms, residual=float(min(ms.values())) if ms else np.nan, n_projects=1, elements={str(r["element_key"])}))
    for sid, last, label in ((BUNDLE_BY_2031, PLANNED_LAST_YEAR, f"every planned upgrade in service by {PLANNED_LAST_YEAR}"),
                             (BUNDLE_ALL, None, "every dated planned upgrade, including 2032 and later")):
        ms, rows = planned_bundle(last)
        out.append(dict(scenario=sid, label=label, element_key="bundle", element_type="bundle", upgrade_type="planned bundle", cost_mid_musd=np.nan,
                        lead_time_mid_months=np.nan, planned="yes", planned_isd="", nwa_vs_wires_ratio=np.nan, nwa_vs_cheapest_ratio=np.nan, mu_scale=ms, residual=np.nan,
                        n_projects=int(rows["project_id"].nunique()), elements=set(c.split("|")[0] for c in ms)))
    return out


# =================================================================================================================
# Full valuation
# =================================================================================================================
def value_all(scen: list, workers: int = WORKERS_DEFAULT, verbose: bool = True) -> dict:
    """{scenario: value_sites frame} for the baseline ('BASE') and every scenario. Each call caches in derived/runs/value_cache.json (value_sites), so a rerun is quick."""
    cand = S.candidate_subs()
    res, meta = {}, {}
    t0 = time.time()
    base = S.value_sites(cand, spec=SPEC, years=(YEAR,), baseline="none", workers=workers)
    res["BASE"] = base
    if verbose:
        print(f"BASE   {len(cand)} sites  {time.time() - t0:.0f}s", flush=True)
    for sc in scen:
        t1 = time.time()
        vs = S.value_sites(cand, mu_scale=sc["mu_scale"], spec=SPEC, years=(YEAR,), baseline="none", workers=workers)
        ign = vs.attrs.get("ignored_mu_scale_keys", [])
        meta[sc["scenario"]] = dict(n_keys=len(sc["mu_scale"]), n_ignored=len(ign), n_scaled=len(sc["mu_scale"]) - len(ign))
        res[sc["scenario"]] = vs
        if verbose:
            print(f"{sc['scenario']:12s} keys {len(sc['mu_scale']):3d} replayed {len(sc['mu_scale']) - len(ign):3d}  {time.time() - t1:.0f}s", flush=True)
    RUNS6.mkdir(parents=True, exist_ok=True)
    (RUNS6 / "rerank_meta.json").write_text(json.dumps(meta, indent=1))
    return {"frames": res, "meta": meta}


# =================================================================================================================
# First-order values for the same 40 sites (Phase 4's formula, gridsite.upgrades.value_table)
# =================================================================================================================
def first_order(scenarios: list[dict], subs: list) -> pd.DataFrame:
    """Per scenario and site: first-order value before and after (2 h, $/kW-yr), from Phase 4's value_table (the same numbers as data/upgrade_rerank_first_order.csv)."""
    from . import upgrades as U

    vt = U.value_table()
    vt = vt[vt["sub"].isin(subs)]
    v0 = vt.groupby("sub")["v_2h"].sum().reindex(subs).fillna(0.0)
    rows = []
    for sc in scenarios:
        scale = vt["cid"].map(sc["mu_scale"]).fillna(1.0)
        v1 = (vt["v_2h"] * scale).groupby(vt["sub"]).sum().reindex(subs).fillna(0.0)
        for s in subs:
            rows.append(dict(scenario=sc["scenario"], sub=s, fo_value_before=float(v0[s]), fo_value_after=float(v1[s])))
    return pd.DataFrame(rows)


def check_first_order(fo: pd.DataFrame) -> dict:
    """Do the recomputed first-order values reproduce the rows of data/upgrade_rerank_first_order.csv (2 h) for the candidates it lists?"""
    pub = pd.read_csv(FIRST_ORDER_CSV, keep_default_na=False, na_values=[""])
    pub = pub[(pub["spec"] == "2h") & pub["group"].isin(["loser"]) & pub["sub"].notna()]
    m = pub.merge(fo, left_on=["scenario", "sub"], right_on=["scenario", "sub"], how="inner")
    if m.empty:
        return dict(n=0)
    d0 = (m["v0_usd_kw_yr"] - m["fo_value_before"]).abs()
    d1 = (m["v1_usd_kw_yr"] - m["fo_value_after"]).abs()
    return dict(n=int(len(m)), max_abs_diff_v0=float(d0.max()), max_abs_diff_v1=float(d1.max()), scenarios=sorted(m["scenario"].unique()))


# =================================================================================================================
# Assemble the table
# =================================================================================================================
def _rank(v: pd.Series) -> pd.Series:
    return v.rank(ascending=False, method="min").astype(int)


def _spearman(a: pd.Series, b: pd.Series) -> float:
    if len(a) < 3 or a.nunique() < 2 or b.nunique() < 2:
        return float("nan")
    from scipy.stats import spearmanr

    return float(spearmanr(a, b).statistic)


def portfolio_groups_table() -> pd.DataFrame:
    """The groups of the 200 MW portfolio (phase 3b caps): sub, dominant element, the group's cap in MW (whole 10 MW units of PRICE_TAKER_SHARE x limit)."""
    cand = S.select_candidates()
    cand = cand[cand["role"] == "candidate"].sort_values("rank").reset_index(drop=True)
    g = S.portfolio_groups(cand)
    cap_mw = {el: float(S.group_cap_units(lim)) * S.UNIT_MW for el, lim in g["lim"].items()}
    out = cand[["sub", "dominant_element", "kv_max"]].copy()
    out["group_cap_mw"] = out["dominant_element"].map(cap_mw)
    out["poi_cap_mw"] = [S.POI_CAP_MW[S.poi_class(k)] for k in out["kv_max"]]
    return out


def portfolio200(values: pd.Series, groups: pd.DataFrame, placement: dict | None = None) -> tuple[float, dict]:
    """Value ($M a year) of the node portfolio the price-taker caps allow: each group's cap placed at its best node (POI cap permitting), or at the nodes of `placement`
    ({sub: MW}). values: learner $/kW-yr by sub. MW x $/kW-yr / 1000 = $M a year."""
    if placement is None:
        placement = {}
        for el, g in groups.groupby("dominant_element"):
            cap = float(g["group_cap_mw"].iloc[0])
            left = cap
            for s in sorted(g["sub"], key=lambda x: -values[x]):
                take = min(left, float(g.set_index("sub").at[s, "poi_cap_mw"]))
                if take > 0:
                    placement[s] = placement.get(s, 0.0) + take
                    left -= take
                if left <= 0:
                    break
    return float(sum(mw * values[s] for s, mw in placement.items()) / 1000.0), placement


def build(workers: int = WORKERS_DEFAULT, with_first_order: bool = True, verbose: bool = True) -> pd.DataFrame:
    scen = scenario_list()
    run = value_all(scen, workers, verbose)
    base = run["frames"]["BASE"].set_index("lane")
    cand = pd.read_csv(REPO_DATA / "sites.csv", keep_default_na=False, na_values=[""]).set_index("sub")
    subs = list(base.index)
    gtab = portfolio_groups_table()
    v_before = base["learner_usd_kw_yr"]
    rank_before = _rank(v_before)
    _, place0 = portfolio200(v_before, gtab)
    port0, _ = portfolio200(v_before, gtab, place0)
    fo = first_order(scen, subs) if with_first_order else None
    fo_chk = check_first_order(fo) if with_first_order else {}
    rows = []
    summaries = {}
    for sc in scen:
        sid = sc["scenario"]
        a = run["frames"][sid].set_index("lane")
        meta = run["meta"][sid]
        v_after = a["learner_usd_kw_yr"]
        delta = v_after - v_before
        rank_after = _rank(v_after)
        zone_ctrl = base["zone_learner_usd_kw_yr"]
        up_b, up_a = base["uplift_learner_usd_kw_yr"], a["uplift_learner_usd_kw_yr"]
        if fo is not None:
            f = fo[fo["scenario"] == sid].set_index("sub")
            fo_b, fo_a = f["fo_value_before"], f["fo_value_after"]
            fo_rank_b, fo_rank_a = _rank(fo_b), _rank(fo_a)
            fo_delta = fo_a - fo_b
        else:
            fo_b = fo_a = fo_rank_b = fo_rank_a = fo_delta = pd.Series(np.nan, index=subs)
        loser = delta < -MATERIAL_USD_KW_YR
        gainer = delta > MATERIAL_USD_KW_YR
        big = delta.idxmin()
        # portfolio: static (the placement of today) and re-optimised (best node per group after the upgrade)
        port_static, _ = portfolio200(v_after, gtab, place0)
        port_reopt, place1 = portfolio200(v_after, gtab)
        # first-order against full: sites that change in either valuation
        moved = loser | (fo_delta < -MATERIAL_USD_KW_YR) | gainer
        n_cmp = int(moved.sum())
        rho_loss = _spearman(-delta[moved], -fo_delta[moved]) if n_cmp >= MIN_COMPARE else float("nan")
        rho_rank = _spearman(rank_after, fo_rank_a) if with_first_order else float("nan")
        eff = "evaluated" if meta["n_scaled"] > 0 else "not_replayed"
        summaries[sid] = dict(
            n_losers=int(loser.sum()), n_gainers=int(gainer.sum()), largest_loss_site=str(big), largest_loss=float(delta.min()),
            sum40_before=float(v_before.sum()), sum40_after=float(v_after.sum()), port0=port0, port_static=port_static, port_reopt=port_reopt,
            rho_loss=rho_loss, rho_rank=rho_rank, n_cmp=n_cmp, effect=eff)
        on_el = {s: bool(sc["elements"] & ({str(cand.at[s, "dominant_element"])} | {str(cand.at[s, f"constraint{i}"]) for i in (1, 2, 3)})) for s in subs}
        for s in subs:
            rows.append(dict(
                scenario=sid, scenario_label=sc["label"], upgrade_type=sc["upgrade_type"], element_key=sc["element_key"], scenario_effect=eff,
                cost_mid_musd=sc["cost_mid_musd"], lead_time_mid_months=sc["lead_time_mid_months"], planned=sc["planned"], planned_isd=sc["planned_isd"],
                nwa_vs_wires_ratio=sc["nwa_vs_wires_ratio"], n_projects=sc["n_projects"], n_constraints_scaled=meta["n_scaled"], n_keys_not_replayed=meta["n_ignored"],
                residual=sc["residual"],
                sub=s, zone=base.at[s, "zone"], kv_max=cand.at[s, "kv_max"], dominant_element=cand.at[s, "dominant_element"], dominant_side=cand.at[s, "dominant_side"],
                on_upgraded_element=bool(on_el[s]), exposure_weighted_scale=float(a.at[s, "exposure_weighted_scale"]),
                value_before_usd_kw_yr=float(v_before[s]), value_after_usd_kw_yr=float(v_after[s]), delta_usd_kw_yr=float(delta[s]),
                share_of_value_lost=float(-delta[s] / v_before[s]) if v_before[s] else np.nan,
                zone_control_usd_kw_yr=float(zone_ctrl[s]), uplift_before_usd_kw_yr=float(up_b[s]), uplift_after_usd_kw_yr=float(up_a[s]),
                share_of_uplift_lost=float((up_b[s] - up_a[s]) / up_b[s]) if up_b[s] > 0 else np.nan,
                oracle_before_usd_kw_yr=float(base.at[s, "oracle_usd_kw_yr"]), oracle_after_usd_kw_yr=float(a.at[s, "oracle_usd_kw_yr"]),
                rank_before=int(rank_before[s]), rank_after=int(rank_after[s]), rank_change=int(rank_before[s] - rank_after[s]),
                value_effect=("loses" if loser[s] else "gains" if gainer[s] else "unchanged"),
                rank_effect=("up" if rank_after[s] < rank_before[s] else "down" if rank_after[s] > rank_before[s] else "same"),
                fo_value_before_usd_kw_yr=float(fo_b[s]), fo_value_after_usd_kw_yr=float(fo_a[s]), fo_delta_usd_kw_yr=float(fo_delta[s]),
                fo_share_of_value_lost=float(-fo_delta[s] / fo_b[s]) if fo_b[s] else np.nan,
                fo_rank_before=int(fo_rank_b[s]) if with_first_order else np.nan, fo_rank_after=int(fo_rank_a[s]) if with_first_order else np.nan,
                scn_n_losers=summaries[sid]["n_losers"], scn_n_gainers=summaries[sid]["n_gainers"],
                scn_largest_loss_site=summaries[sid]["largest_loss_site"], scn_largest_loss_usd_kw_yr=summaries[sid]["largest_loss"],
                scn_sum40_before_usd_kw_yr=summaries[sid]["sum40_before"], scn_sum40_after_usd_kw_yr=summaries[sid]["sum40_after"],
                scn_sum40_change_pct=float(100.0 * (summaries[sid]["sum40_after"] / summaries[sid]["sum40_before"] - 1.0)),
                scn_portfolio200_before_musd_yr=port0, scn_portfolio200_static_after_musd_yr=port_static, scn_portfolio200_reopt_after_musd_yr=port_reopt,
                scn_portfolio200_static_change_pct=float(100.0 * (port_static / port0 - 1.0)), scn_portfolio200_reopt_change_pct=float(100.0 * (port_reopt / port0 - 1.0)),
                scn_spearman_loss_vs_first_order=rho_loss, scn_spearman_rank_after_vs_first_order=rho_rank, scn_n_sites_changed=n_cmp))
    df = pd.DataFrame(rows)
    df.attrs["first_order_check"] = fo_chk
    df.attrs["summaries"] = summaries
    if with_first_order:
        df.attrs["pooled"] = pooled_agreement(df)
    return df


def pooled_agreement(df: pd.DataFrame) -> dict:
    """First order against the full valuation over every (scenario, site) pair of an evaluated scenario: the baseline rank correlation of the 40 sites, the rank
    correlation of the change in value over the pairs that move by more than MATERIAL_USD_KW_YR in either valuation, and how often the two disagree on the sign."""
    ev = df[df["scenario_effect"] == "evaluated"]
    base = ev[ev["scenario"] == ev["scenario"].iloc[0]]
    mv = ev[(ev["delta_usd_kw_yr"].abs() > MATERIAL_USD_KW_YR) | (ev["fo_delta_usd_kw_yr"].abs() > MATERIAL_USD_KW_YR)]
    flip = mv[(mv["delta_usd_kw_yr"] * mv["fo_delta_usd_kw_yr"] < 0) & (mv["delta_usd_kw_yr"].abs() > MATERIAL_USD_KW_YR) & (mv["fo_delta_usd_kw_yr"].abs() > MATERIAL_USD_KW_YR)]
    both_big = mv[(mv["delta_usd_kw_yr"] < -MATERIAL_USD_KW_YR) & (mv["fo_delta_usd_kw_yr"] < -MATERIAL_USD_KW_YR)]
    only_full = mv[(mv["delta_usd_kw_yr"] < -MATERIAL_USD_KW_YR) & (mv["fo_delta_usd_kw_yr"] >= -MATERIAL_USD_KW_YR)]
    only_fo = mv[(mv["delta_usd_kw_yr"] >= -MATERIAL_USD_KW_YR) & (mv["fo_delta_usd_kw_yr"] < -MATERIAL_USD_KW_YR)]
    return dict(n_pairs=int(len(ev)), n_moved=int(len(mv)), rho_baseline_rank=_spearman(base["value_before_usd_kw_yr"], base["fo_value_before_usd_kw_yr"]),
                rho_baseline_value=_spearman(base["value_before_usd_kw_yr"].rank(), base["fo_value_before_usd_kw_yr"].rank()),
                rho_delta=_spearman(mv["delta_usd_kw_yr"], mv["fo_delta_usd_kw_yr"]), n_sign_flips=int(len(flip)), n_both_lose=int(len(both_big)),
                n_only_full_loses=int(len(only_full)), n_only_first_order_loses=int(len(only_fo)))


def disagreements(df: pd.DataFrame, n: int = 12) -> pd.DataFrame:
    """The (scenario, site) pairs where the full valuation and the first-order one differ most in the change of value, $/kW-yr (evaluated scenarios only)."""
    ev = df[df["scenario_effect"] == "evaluated"].copy()
    ev["full_minus_first_order"] = ev["delta_usd_kw_yr"] - ev["fo_delta_usd_kw_yr"]
    cols = ["scenario", "sub", "dominant_element", "delta_usd_kw_yr", "fo_delta_usd_kw_yr", "full_minus_first_order", "rank_before", "rank_after", "fo_rank_before", "fo_rank_after"]
    return ev.reindex(ev["full_minus_first_order"].abs().sort_values(ascending=False).index)[cols].head(n).reset_index(drop=True)


# =================================================================================================================
# Labels and writer
# =================================================================================================================
LABELS = [
    ("scenario", "DERIVED", "data/upgrades.csv upgrade_id; this module", "U01..U10 = the top-10 upgrades of data/upgrades.csv; PLANNED_2031 = every planned upgrade in service by 2031; PLANNED_ALL = every dated planned upgrade"),
    ("scenario_label", "DERIVED", "data/upgrades.csv element", ""),
    ("upgrade_type", "ASSUMPTION", "data/upgrades.csv upgrade_type", "the fix a TSP would most likely file (reconductor, rebuild, 765 kV plan); 'planned bundle' for the two bundles"),
    ("element_key", "REAL", "ERCOT NP6-86-CD / NP4-191-CD constraint_name", "monitored element (constraint_name); the word bundle for the two planned bundles"),
    ("scenario_effect", "DERIVED", "this module", "evaluated = at least one constraint of the scenario is in the replayed set; not_replayed = none is, so the full valuation cannot move any site (a limit of the data, not evidence that the upgrade does nothing)"),
    ("cost_mid_musd", "DERIVED", "data/upgrades.csv", "mid cost of the wires fix, $M, 2024 dollars (MISO MTEP24 unit costs); blank for bundles"),
    ("lead_time_mid_months", "DERIVED", "data/upgrades.csv", "RPG submittal to in service, mid case; blank for bundles"),
    ("planned", "DERIVED", "data/upgrades.csv planned", "yes / likely / identified / hint / adjacent / no: whether a TSP project is on file for the element"),
    ("planned_isd", "REAL", "TPIT / RTP via data/upgrades.csv", "in-service date of the matched project, if dated"),
    ("nwa_vs_wires_ratio", "DERIVED", "data/upgrades.csv", "battery cost over wires cost for the central relief scenario (10% of the limit in 80% of binding hours)"),
    ("n_projects", "DERIVED", "gridsite.upgrades.planned_schedule", "planned projects behind a bundle; 1 for a single upgrade"),
    ("n_constraints_scaled", "DERIVED", "gridsite.sites.value_sites", "constraints of the scenario that are in the 260 replayed constraints (the others cannot move a node price)"),
    ("n_keys_not_replayed", "DERIVED", "gridsite.sites.value_sites", "constraints of the scenario that are not replayed (no regression shift factor or no usable window)"),
    ("residual", "ASSUMPTION", "gridsite.upgrades.RESIDUAL_MU_SCALE_*", "share of the shadow prices left after the upgrade: 0.10 for a line or transformer, 0.50 for an interface"),
    ("sub", "REAL", "NP4-160 SUBSTATION", "candidate substation (data/sites.csv)"),
    ("zone", "REAL", "data/settlement_points.csv", "arena load zone the node price is measured against"),
    ("kv_max", "REAL", "NP4-160", ""),
    ("dominant_element", "DERIVED", "data/sites.csv", "the element the site earns most of its congestion value from"),
    ("dominant_side", "DERIVED", "data/sites.csv", "import = node price rises above the zone when the element binds; export = falls"),
    ("on_upgraded_element", "DERIVED", "this module", "the upgraded element is one of the site's top-3 constraints"),
    ("exposure_weighted_scale", "DERIVED", "gridsite.sites.value_sites", "mean scale applied to the site's shadow-price exposure (1 = untouched)"),
    ("value_before_usd_kw_yr", "SIM", "gridsite.sites.value_sites", "2 h learner, $/kW-yr, oracle on the rebuilt 2025 lane x the lane's own learner/oracle ratio (= sites.csv fwd_learner_2025_replay)"),
    ("value_after_usd_kw_yr", "SIM", "gridsite.sites.value_sites with mu_scale = apply_upgrade(...)", "same, after the upgrade"),
    ("delta_usd_kw_yr", "SIM", "after - before", "negative = the site loses"),
    ("share_of_value_lost", "SIM", "-(delta) / before", ""),
    ("zone_control_usd_kw_yr", "SIM", "gridsite.sites.value_sites", "the same battery on the real zone price (an ADER fleet or an uncongested node)"),
    ("uplift_before_usd_kw_yr", "SIM", "value - zone control", ""),
    ("uplift_after_usd_kw_yr", "SIM", "value - zone control", ""),
    ("share_of_uplift_lost", "SIM", "(uplift before - after) / before", "the share of the nodal premium that goes; comparable with fo_share_of_value_lost"),
    ("oracle_before_usd_kw_yr", "SIM", "gridsite.sites.value_sites", "perfect-foresight ceiling on the rebuilt lane"),
    ("oracle_after_usd_kw_yr", "SIM", "gridsite.sites.value_sites", ""),
    ("rank_before", "SIM", "rank of value_before among the 40", "1 = best; ties share the lower number"),
    ("rank_after", "SIM", "rank of value_after among the 40", ""),
    ("rank_change", "SIM", "rank_before - rank_after", "positive = moves up"),
    ("value_effect", "DERIVED", "delta against MATERIAL_USD_KW_YR (1.0)", "loses / gains / unchanged"),
    ("rank_effect", "DERIVED", "rank_after against rank_before", "up / down / same: a site can move up without gaining value because others fall"),
    ("fo_value_before_usd_kw_yr", "ASSUMPTION", "gridsite.upgrades.value_table (Phase 4)", "first-order value: sum over constraints of min(|SF - SF_zone|, 1) x shadow-price-hours / 1000 x capture factor 0.205 (2 h)"),
    ("fo_value_after_usd_kw_yr", "ASSUMPTION", "same with the upgraded constraints scaled", ""),
    ("fo_delta_usd_kw_yr", "ASSUMPTION", "after - before", ""),
    ("fo_share_of_value_lost", "ASSUMPTION", "-(delta) / before", "first-order share of congestion value lost"),
    ("fo_rank_before", "ASSUMPTION", "rank of the first-order value among the 40", ""),
    ("fo_rank_after", "ASSUMPTION", "", ""),
    ("scn_n_losers", "DERIVED", "this module", "scenario level (repeated on each row): sites that lose more than 1 $/kW-yr"),
    ("scn_n_gainers", "DERIVED", "this module", "sites that gain more than 1 $/kW-yr"),
    ("scn_largest_loss_site", "SIM", "this module", "the site with the largest fall in value"),
    ("scn_largest_loss_usd_kw_yr", "SIM", "this module", "its change, $/kW-yr"),
    ("scn_sum40_before_usd_kw_yr", "SIM", "this module", "sum over the 40 candidates of the learner value (1 kW at each site), $/kW-yr"),
    ("scn_sum40_after_usd_kw_yr", "SIM", "this module", ""),
    ("scn_sum40_change_pct", "SIM", "this module", "percent change of that sum"),
    ("scn_portfolio200_before_musd_yr", "SIM", "this module", "the 200 MW the price-taker caps allow at the nodes (each constraint group's cap at its best node), $M a year at 2025 replay values"),
    ("scn_portfolio200_static_after_musd_yr", "SIM", "this module", "the same MW at the same nodes, valued after the upgrade (the owner cannot move)"),
    ("scn_portfolio200_reopt_after_musd_yr", "SIM", "this module", "the best placement after the upgrade (the owner re-sites)"),
    ("scn_portfolio200_static_change_pct", "SIM", "this module", ""),
    ("scn_portfolio200_reopt_change_pct", "SIM", "this module", ""),
    ("scn_spearman_loss_vs_first_order", "DERIVED", "this module", "Spearman correlation of the fall in value, full valuation against first order, over the sites that change in either (blank if fewer than 5 sites change)"),
    ("scn_spearman_rank_after_vs_first_order", "DERIVED", "this module", "Spearman correlation of the post-upgrade ranks of the 40 sites, full against first order"),
    ("scn_n_sites_changed", "DERIVED", "this module", "sites that change by more than 1 $/kW-yr in either valuation"),
]


def write(df: pd.DataFrame) -> None:
    out = df.copy()
    num = out.select_dtypes("float").columns
    out[num] = out[num].round(4)
    out.to_csv(OUT_CSV, index=False)
    S.write_labels(OUT_CSV, LABELS, out)


def run(*args) -> pd.DataFrame:
    kv, fl = {}, set()
    for a in args:
        if "=" in a:
            k, v = a.split("=", 1)
            kv[k.strip()] = v.strip()
        else:
            fl.add(a.strip())
    workers = int(kv.get("workers", WORKERS_DEFAULT))
    df = build(workers=workers, with_first_order="skip-first-order" not in fl)
    write(df)
    return df
