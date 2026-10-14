"""Revision 2: the README tables that are new or rebuilt, as lists of markdown lines generated from the CSVs.

The README is written once from these lines (paste the output) and `report_check` asks that every generated line appears verbatim in the README, so a table cannot drift from its
data. Formatting follows the rest of the README: one decimal with a thousands separator, the Unicode minus.

  python -m gridsite.report_tables <name>        prints one table (names: see TABLES)
"""
from __future__ import annotations

import sys

import numpy as np
import pandas as pd

from . import paths

DATA = paths.REPO_DATA


def _read(name: str) -> pd.DataFrame:
    return pd.read_csv(DATA / name, keep_default_na=False, na_values=[""], low_memory=False)


def f1(x) -> str:
    return f"{float(x):,.1f}".replace("-", "−")


def f2(x) -> str:
    return f"{float(x):,.2f}".replace("-", "−")


def f0(x) -> str:
    return f"{float(x):,.0f}".replace("-", "−")


def sg(x, nd=1) -> str:
    return (f"{float(x):+,.{nd}f}").replace("-", "−")


def pct(x, nd=0) -> str:
    return f"{100 * float(x):.{nd}f}%"


def _table(header: list, rows: list) -> list:
    return ["| " + " | ".join(header) + " |", "|" + "|".join("---" for _ in header) + "|"] + ["| " + " | ".join(str(c) for c in r) + " |" for r in rows]


PRETTY = {"6437__F": "Scurry Chevron–Knapp 138 kV", "HARGRO_TWINBU1_1": "Twin Buttes–Hargrove 138 kV", "FORTMA_YELWJC1_1": "Fort Mason–Yellow Jacket 138 kV", "587__A": "Argyle–Lewisville 138 kV",
          "DT_PK_91_A": "PK–DT 138 kV (Houston)", "LAKENA_SAMATH1_1": "Lakena–Samathis 69 kV", "215T215_1": "HIGH36–Brenno 138 kV", "SEA_AAT1": "SEA 138/69 kV transformer",
          "COLLE_JUPIT_1": "College–Jupiter 138 kV", "COL_BRAN_1": "Brand–College 138 kV", "6429__D": "Encrt–Brand 138 kV", "LPLMK_LPLNE_1": "Lubbock LPLMK–LPLNE 115 kV",
          "WESTEX": "WESTEX interface", "STPWAP39_1": "STP–Wap 345 kV", "6945__A": "Mgses–Catsw 345 kV", "EASTEX": "EASTEX interface", "35055__A": "Samsw–Vensw 345 kV",
          "MDOPHR99_A": "MDO–PHR 345 kV", "E_PASP": "South Texas Export (E_PASP)", "BELCNTY-SALSW": "Bell County–Salado 138 kV", "VALEXP": "Valley export pattern", "NELRIO": "Nelson Sharpe–Rio Hondo",
          "I_FW_N": "Far West import (I_FW_N)"}


def anchors_table() -> list:
    """Section 'What changed': the elements re-anchored on their 2026 IMM rent."""
    a = _read("anchor_2026_applied.csv")
    rows = []
    for el, g in a.groupby("constraint_name", sort=False):
        r0 = g.iloc[0]
        sph0 = (g["sph_before"] * g["limit_mw"]).sum() / g["limit_mw"].iloc[0] if False else None
        rent0 = (g["sph_before"] * g["limit_mw"]).sum() / 1e6
        rent1 = (g["sph_after"] * g["limit_mw"]).sum() / 1e6
        used = "yes" if bool(g["used_in_primary"].astype(str).str.lower().eq("true").any()) else "no: replaced by R1's Far West pattern"
        flag = f", {r0['largest_month'].capitalize()} {pct(r0['largest_month_share'])}" if bool(str(r0["one_month_dominates"]).lower() == "true") else ""
        rows.append([PRETTY.get(el, el), r0["market"], f"{f1(r0['ytd_musd'])} (Jan–Aug{flag})", f1(r0["annualised_musd"]), f"{f1(rent0)} → {f1(rent1)}", f"{f1(rent1 / max(rent0, 1e-9))}×",
                     f"{f0(g['H_before'].max())} → {f0(g['H_after'].max())}", used])
    return _table(["Element", "Market", "2026 rent so far, $M [REAL]", "Annualised, $M [DERIVED]", "Replayed annual rent before → after, $M [SIM]", "Ratio", "Binding hours a year before → after [DERIVED]", "Used in the primary run"], rows)


def region_summary_table() -> list:
    s = _read("regional_basis_summary.csv")
    rows = []
    order = ["Rio Grande Valley", "Panhandle", "Far West", "Laredo/South", "Central"]
    for reg in order:
        z = s[(s["region"] == reg) & (s["reference"] == "zone")].iloc[0]
        y = s[(s["region"] == reg) & (s["reference"] == "system")].iloc[0]
        rows.append([reg, int(z["n_nodes"]), f"{f2(z['basis_mean'])} ({f2(y['basis_mean'])})", f"{f1(z['pooled_p05'])} ({f1(y['pooled_p05'])})", pct(z["share_hours_neg10"], 1), pct(z["share_hours_pos10"], 1),
                     f"{f1(z['worst_hour_mean'])} (hour {int(z['worst_hour_cpt'])})", sg(z["dam_uplift_mean"], 2), z["verdict"]])
    return _table(["Region", "Nodes", "Mean basis against the load zone (against the system) $/MWh [REAL]", "5th percentile of node-hours [REAL]", "Node-hours below −$10", "Node-hours above +$10",
                   "Worst hour of the day, mean against the zone [REAL]", "2 h day-ahead value, node minus zone, $ per kW over the 42 days [DERIVED]", "Reading"], rows)


REGION_ORDER = ["Rio Grande Valley", "Panhandle", "Far West", "Laredo/South", "Central (Bell County)"]


def regional_picks_table() -> list:
    """Section 7.4: R1's 41 regional picks by region and side, measured against modelled."""
    e = _read("extra_sites.csv")
    e = e[e["role"] == "regional"]
    rows = []
    for reg in REGION_ORDER:
        for side in ("import", "export"):
            g = e[(e["region"] == reg) & (e["region_side"] == side)]
            if g.empty:
                rows.append([reg, side, 0, "none", "none", "none", "none", "none", "none"])
                continue
            gain = g["h2_mean2125_uplift_usd_kw_yr_A5"]
            far = int((g["dominant_distance_class"] == "beyond_150km").sum())
            rows.append([reg, side, len(g), f2(g["obs_basis_vs_zone_mean_usd_mwh"].mean()), sg(g["obs_dam_uplift_42d_usd_kw"].mean(), 2),
                         f"{sg(gain.mean())} ({sg(gain.min())} to {sg(gain.max())})", sg(g["h2_y2025_uplift_usd_kw_yr_A5"].mean()),
                         f"{int((gain > 1).sum())} of {len(g)}", f"{far} of {len(g)}"])
    return _table(["Region", "Side", "Picks", "Measured: node minus zone, mean $/MWh [REAL]", "Observed 2 h day-ahead value, node minus zone, $ per kW over 42 days [DERIVED]",
                   "Modelled gain over the zone, 2021–25 mean, $/kW-yr: mean (range) [SIM]", "Modelled gain in 2025 [SIM]", "Picks with a modelled gain above $1", "Picks whose dominant element is more than 150 km away"], rows)


def sens_top10_table() -> list:
    """What changed: the primary top 10 of the 40 with their value under 2025-only anchors and under the 2026 anchors (same oracle-ratio footing), ranks and the top-15 flag."""
    a = _read("anchor_sensitivity.csv")
    a = a[a["role"] == "candidate"].sort_values("dispatch_mean_2021_25", ascending=False).head(10)
    c = _read("site_candidates.csv").set_index("sub")
    rows = []
    for i, r in enumerate(a.itertuples(), 1):
        d = r.orv_primary_mean_2021_25 - r.orv_anchors2025_mean_2021_25
        rev1 = "–" if pd.isna(r.rev1_dispatch_mean_2021_25) else f1(r.rev1_dispatch_mean_2021_25)
        flag = "yes" if bool(r.top15_depends_on_anchor) else "no"
        rows.append([i, r.lane, PRETTY.get(c.at[r.lane, "dominant_element"], c.at[r.lane, "dominant_element"]), rev1, f1(r.dispatch_mean_2021_25), f1(r.orv_anchors2025_mean_2021_25), f1(r.orv_primary_mean_2021_25), sg(d),
                     f"#{int(r.rank_of_40_anchors2025)} → #{int(r.rank_of_40_primary)}", flag])
    return _table(["#", "Sub", "Dominant element", "Revision 1, learner 2021–25 [SIM]", "Revision 2, learner 2021–25 [SIM]", "2025-only anchors [SIM, oracle × ratio]", "2026 anchors [SIM, oracle × ratio]",
                   "Change, 2026 minus 2025", "Rank of the 40, oracle × ratio: 2025 anchors → 2026 anchors", "Top 15 depends on the anchor"], rows)


def sens_summary_table() -> list:
    """What changed: the sensitivity variants against the primary (oracle-ratio footing for every row of the comparison)."""
    sm = _read("anchor_sensitivity_summary.csv")
    an = _read("anchor_sensitivity.csv")
    an = an[an["role"] == "candidate"]
    rows = []
    for r in sm.itertuples():
        if r.variant == "primary":
            continue
        d = an[f"orv_{r.variant}_mean_2021_25"] - an["orv_primary_mean_2021_25"]
        n1 = int((d.abs() > 1.0).sum())
        big = d[d.abs() > 5.0].sort_values(key=lambda x: -x.abs())
        bigtxt = ", ".join(f"{an.at[i, 'lane']} {sg(x)}" for i, x in big.items()) if len(big) else "none"
        ent = ", ".join(r.top15_entering.split(",")) if isinstance(r.top15_entering, str) and r.top15_entering else "none"
        lev = ", ".join(r.top15_leaving.split(",")) if isinstance(r.top15_leaving, str) and r.top15_leaving else "none"
        rows.append([r.description, n1, bigtxt, f2(r.rho_vs_primary_40), int(r.top15_overlap), ent, lev])
    return _table(["Variant (against the primary run)", "Sites of the 40 that move by more than $1/kW-yr", "Sites that move by more than $5 [SIM]", "Rank correlation with the primary", "Primary top 15 kept (of 15)",
                   "Enters the top 15", "Leaves the top 15"], rows)


UP_NAMES = {"U01": "Scurry Chevron–Knapp", "U02": "Vealmoor–Koch Tap", "U03": "Twin Buttes–Hargrove", "U04": "WESTEX interface", "U05": "HIGH36–Brenno", "U06": "PK–DT", "U07": "Longshore–Consavvy",
            "U08": "Bell County–Salado", "U09": "Encrt–Brand", "U10": "Salds–Sonterra"}


def upgrade_effects_table() -> list:
    """What changed / section 3: for each of the top-10 upgrades, how many of the valued sites it moves by more than $1/kW-yr and its largest effect (the 40 and the extra groups)."""
    rr = _read("upgrade_rerank.csv")
    ex = _read("upgrade_rerank_extra.csv")
    rows = []
    n_moving = 0
    for uid in UP_NAMES:
        g = rr[rr["scenario"] == uid]
        x = ex[ex["scenario"] == uid]
        a = g[g["delta_usd_kw_yr"].abs() > 1.0]
        b = x[x["delta_usd_kw_yr"].abs() > 1.0]
        eff = g["scenario_effect"].iloc[0]
        if eff == "not_replayed":
            rows.append([f"**{uid}** {UP_NAMES[uid]}", "no", "–", "–", "did not bind in summer 2026 (R1); no shift factor"])
            continue
        n_moving += int(len(a) + len(b) > 0)
        both = pd.concat([g.assign(group="the 40")[["sub", "delta_usd_kw_yr", "group"]], x.assign(group="regional / left out")[["sub", "delta_usd_kw_yr", "group"]]])
        both = both.reset_index(drop=True)
        top = both.loc[both["delta_usd_kw_yr"].abs().idxmax()]
        rows.append([f"**{uid}** {UP_NAMES[uid]}", "yes" if len(a) + len(b) else "no", f"{len(a)} of 40; {len(b)} of {len(x)}", f"{top['sub']} {sg(top['delta_usd_kw_yr'])}" if abs(top["delta_usd_kw_yr"]) > 1.0 else "none above $1",
                     "full dispatch valuation"])
    return _table(["Upgrade", "Moves at least one site", "Sites moved by more than $1/kW-yr: the 40; regional and left-out", "Largest effect, $/kW-yr", "Evaluated by"], rows), n_moving


def regional_top_table(n: int = 8) -> list:
    """Section 7.4: the regional picks with the largest modelled gain, with what the measured prices say and how far the line behind the value is."""
    e = _read("extra_sites.csv")
    e = e[e["role"] == "regional"].sort_values("h2_mean2125_uplift_usd_kw_yr_A5", ascending=False).head(n)
    cls = {"beyond_150km": "line more than 150 km away", "within_150km": "line within 150 km", "element_not_placed": "structural pattern, no line ends"}
    rows = []
    for r in e.itertuples():
        el = PRETTY.get(r.dominant_element, r.dominant_element)
        dist = "" if pd.isna(r.dominant_distance_km) else f", {f0(r.dominant_distance_km)} km"
        rows.append([f"{r.sub} ({str(r.county).title()})", f"{r.region}, {r.region_side}", f"{el} ({r.dominant_side}{dist})", f2(r.obs_basis_vs_zone_mean_usd_mwh), sg(r.obs_dam_uplift_42d_usd_kw, 2),
                     f"{sg(r.h2_mean2125_uplift_usd_kw_yr_A5)} / {sg(r.h2_y2025_uplift_usd_kw_yr_A5)}", sg(r.fwd_uplift_learner_2029), cls[r.dominant_distance_class]])
    return _table(["Pick (county)", "Region, side", "Dominant element (side, distance to nearest end)", "Measured: node minus zone, mean $/MWh [REAL]",
                   "Observed 2 h day-ahead value, node minus zone, $ per kW over 42 days [DERIVED]", "Modelled gain over the zone: 2021–25 mean / 2025 [SIM]", "Modelled gain in 2029 [SIM]", "Distance reading [DERIVED]"], rows)


CHURN_WHY = {
    "ATBR": "placed by the geography audit (Coke County, confidence 0.60), so it passes the 0.5 gate; screening rank 5",
    "JUPITER": "placed by the geography audit (Dallas County, 0.50)",
    "LPLNE": "a Lubbock Power & Light station the audit moved from the wrong county (Hardeman); the Lubbock rent re-anchored on 2026 (64 times) lifts its screening score",
    "LPLMK": "as LPLNE (export side)",
    "LPLNW": "as LPLNE (export side); its screening score, 121,355, is the 40th",
    "NOVICE": "the sixth Twin Buttes site: the cap of five per element removes it once ATBR is eligible",
    "CRNTH": "the rule of at least 10 export-side sites drops the lowest-scored import sites",
    "SEA": "as CRNTH",
    "ECRSW": "its screening score is below the new cut-off (the 40th scores 121,355)",
    "CENTRVIL": "as ECRSW",
}


def churn_table() -> list:
    """What changed: the sites that entered and left the 40, with their value in both revisions (the leavers are still valued, as the group 'left out')."""
    s = _read("sites.csv").sort_values("h2_mean2125_A5", ascending=False).reset_index(drop=True)
    s["rank"] = s.index + 1
    r1 = _read("site_candidates_rev1.csv")
    ex = _read("extra_sites.csv")
    ex = ex[ex["role"] == "rev1_only"].set_index("sub")
    r1i = r1.set_index("sub")
    enter = s[~s["sub"].isin(r1["sub"])]
    leave = r1[~r1["sub"].isin(s["sub"])].sort_values("a5_2021_25_rev1", ascending=False)
    rows = []
    for r in enter.itertuples():
        rows.append([r.sub, "enters", PRETTY.get(r.dominant_element, r.dominant_element) + f" ({r.dominant_side})", "–", f"{f1(r.h2_mean2125_A5)}, #{r.rank} of 40", CHURN_WHY[r.sub]])
    for r in leave.itertuples():
        e = ex.loc[r.sub]
        rows.append([r.sub, "leaves", PRETTY.get(r.dominant_element_rev1, r.dominant_element_rev1) + f" ({e['dominant_side']})", f1(r.a5_2021_25_rev1), f"{f1(e['h2_mean2125_A5'])}, outside the 40", CHURN_WHY[r.sub]])
    return _table(["Site", "Change", "Dominant element (side)", "Revision 1: learner 2021–25 [SIM]", "Revision 2: learner 2021–25, rank [SIM]", "Why [DERIVED]"], rows)


TARGET_USE = {"WESTEX": "primary", "I_FW_N": "primary", "I_FW_S": "via I_FW_N", "NELRIO": "primary", "PASP_PATA": "primary (as E_PASP)", "VALEXP": "primary", "BELCNTY_SALSW": "primary",
              "PNHNDL": "sensitivity S3 only", "NE_LOB": "via VALEXP", "VALIMP": "no", "LARDVFT": "no", "ODEHV_YARBR": "no", "VEALMOOR_KOCH": "no", "LNGSW_CONSW": "no"}


def targets_table() -> list:
    """Section 7.3: the structural targets, their grade and whether the primary run replays them."""
    t = _read("sf_structural_targets.csv")
    rows = []
    for r in t.itertuples():
        if r.kind == "line" and pd.notna(r.pocket_hours):
            ev = f"price pocket: {int(r.pocket_hours)} of 1,008 hours beyond $10 (24 needed)" if r.status != "identified" else f"price pocket: {int(r.pocket_hours)} of 1,008 hours beyond $10; the pattern tracks it, r = {f2(r.pocket_r)}"
        elif pd.notna(r.oop_r) and r.n_bound_published >= 20:
            ev = (f"tracks the published Sep–Oct shadow price, r = {f2(r.oop_r)}" if r.oop_r >= 0.4 else f"does not track the published Sep–Oct shadow price, r = {f2(r.oop_r)}") + f" ({int(r.n_bound_published)} bound hours)"
        elif r.grade == "C" and r.n_bound_published > 0:
            ev = f"geography and side only; too few published bound hours to test ({int(r.n_bound_published)})"
        elif r.grade == "C":
            ev = "geography and side only; no published series"
        else:
            ev = "no pattern in its domain on its side"
        rows.append([f"`{r.target}`", r.label.replace(" - ", "–"), r.side, r.grade, ev, TARGET_USE[r.target]])
    return _table(["Target", "Constraint", "Side", "Grade", "Evidence [DERIVED]", "Replayed"], rows)


TABLES = {"anchors": anchors_table, "regions": region_summary_table, "picks": regional_picks_table, "sens_top10": sens_top10_table, "sens_summary": sens_summary_table,
          "upgrade_effects": lambda: upgrade_effects_table()[0], "targets": targets_table, "picks_top": regional_top_table, "churn": churn_table}

if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    for ln in TABLES[sys.argv[1]]():
        print(ln)
