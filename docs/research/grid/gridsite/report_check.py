"""Report checks: does README.md say what the CSVs say? (run_grid.py report-check)

  1. every data/*.csv has a label sidecar that covers every column (gridsite.check_labels);
  2. the README has its sections, 5-7 TL;DR bullets, the 32-item constraint table and the two site tables, and the numbers in the top-15 table and Appendix A equal
     the numbers in data/sites.csv, row by row;
  3. spot-checks of the numbers quoted in the text against the CSVs (each check names the README text, the value recomputed from the data, and whether they agree);
  4. the outputs of Phase 6 (upgrade_rerank.csv, tier2_valued.csv) satisfy their invariants;
  5. the map: files, size, Leaflet 1.9.4 from cdnjs, OpenStreetMap attribution, dark and light chrome, a viewport meta tag, the payload parses.

Prints one line per check and exits with status 1 if any fails. Reads only files; writes nothing.

R2 (revision 2) made that true. Before, the portfolio-cap check called into gridsite.nodal_lanes, whose caches are keyed on the input files, so a check run after any
upstream change rebuilt and overwrote data/constraint_synth.csv and two lane caches (found by G1). Now the process runs with nodal_lanes.READ_ONLY = True (a stale cache is
recomputed in memory and never saved), a snapshot of every table and cache is compared before and after (check "writes nothing"), and `check_recomputation` recomputes the
congestion structure from scratch in memory and compares it with data/constraint_structure_rev2.csv and data/constraint_synth.csv, and the stored lane file's key with a fresh one.
"""
from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

from . import check_labels, paths

GRID = paths.GRID_DIR
DATA = paths.REPO_DATA
README = GRID / "README.md"
MAP_HTML = paths.MAP / "index.html"
MAP_DATA = paths.MAP / "index_data.js"
MAP_MAX_MB = 5.0                  # REAL: brief: total map data under about 5 MB
N_TLDR_MIN, N_TLDR_MAX = 5, 7     # REAL: brief: TL;DR of 5-7 bullets
N_CONSTRAINT_ITEMS = 32           # REAL: PROMPT.md section 3 lists 32 items
N_SITES = 40                      # REAL: 40 candidate sites
SECTIONS = ["## TL;DR", "## What changed in this revision", "## 1. Where to put batteries", "## 2. The physical-constraints map", "## 3. Upgrades", "## 4. Trading after placement",
            "## 5. Reliability and physical risk", "## 6. Tier 2", "## 7. Regions: Panhandle, Valley, Far West", "## 8. How this feeds the dispatch platform", "## 9. Confidence and limits",
            "## 10. Reproduce"]
LABELS = ("REAL", "DERIVED", "SIM", "ASSUMPTION")
MIN_LABEL_USES = 20               # ASSUMPTION: each label word appears at least this often in the README


def _read(name: str) -> pd.DataFrame:
    return pd.read_csv(DATA / name, keep_default_na=False, na_values=[""], low_memory=False)


def f1(x) -> str:
    """One decimal with a thousands separator; a negative number uses the Unicode minus the README tables use."""
    return f"{float(x):,.1f}".replace("-", "−")


def f0(x) -> str:
    return f"{float(x):,.0f}"


@dataclass
class Result:
    name: str
    ok: bool
    detail: str = ""


# ------------------------------------------------------------------------------------------------------------------------------------ README parsing
def _table_after(lines: list[str], header_start: str) -> list[str]:
    """Rows (excluding header and separator) of the markdown table whose header line starts with header_start."""
    for i, ln in enumerate(lines):
        if ln.startswith(header_start):
            rows = []
            j = i + 2
            while j < len(lines) and lines[j].startswith("|"):
                rows.append(lines[j])
                j += 1
            return rows
    return []


def _cells(row: str) -> list[str]:
    return [c.strip() for c in row.strip().strip("|").split("|")]


# ------------------------------------------------------------------------------------------------------------------------------------ checks
def check_labels_all() -> list[Result]:
    res = check_labels.check_all()
    bad = [r for r in res if not r["ok"]]
    out = [Result("label sidecars", not bad, f"{len(res) - len(bad)} of {len(res)} tables pass" + (": " + ", ".join(r["file"] for r in bad) if bad else ""))]
    orph = check_labels.orphan_sidecars()
    out.append(Result("no sidecar without a table", not orph, ", ".join(orph)))
    return out


def check_structure(txt: str) -> list[Result]:
    out = []
    lines = txt.splitlines()
    pos = [txt.find(s) for s in SECTIONS]
    out.append(Result("sections present and in order", all(p >= 0 for p in pos) and pos == sorted(pos), f"missing: {[s for s, p in zip(SECTIONS, pos) if p < 0]}"))
    out.append(Result("findings first: TL;DR before section 1", 0 <= txt.find("## TL;DR") < txt.find("## 1. Where to put batteries")))
    tl = txt[txt.find("## TL;DR"): txt.find("## What changed in this revision")]
    n = len(re.findall(r"^\d+\. \*\*", tl, flags=re.M))
    out.append(Result("TL;DR has 5-7 bullets", N_TLDR_MIN <= n <= N_TLDR_MAX, f"{n} bullets"))
    rows = _table_after(lines, "| # | Item | What the data showed")
    nums = [int(_cells(r)[0]) for r in rows if _cells(r)[0].isdigit()]
    out.append(Result("32-item constraint table complete", nums == list(range(1, N_CONSTRAINT_ITEMS + 1)), f"items {nums[:3]}..{nums[-3:]} ({len(nums)})"))
    uses = {l: len(re.findall(rf"\b{l}\b", txt)) for l in LABELS}
    out.append(Result("every label word is used", all(v >= MIN_LABEL_USES for v in uses.values()), str(uses)))
    for must in ("Beryl", "derecho", "Uri", "ADER", "DESR", "RTC+B", "4CP", "half-life", "first ~10 MW", "API key", "Gate A", "Gate B", "dispatch/"):
        out.append(Result(f"README mentions {must}", must in txt))
    return out


def check_tables(txt: str) -> list[Result]:
    """Top-15 table and Appendix A against data/sites.csv, row by row."""
    out = []
    s = _read("sites.csv").sort_values("h2_mean2125_A5", ascending=False).reset_index(drop=True)
    lines = txt.splitlines()
    top = _table_after(lines, "| # | Sub (kV) |")
    bad = []
    for i, r in enumerate(s.head(15).itertuples()):
        row = top[i] if i < len(top) else ""
        c = _cells(row)
        toks = [f1(r.h2_mean2125_A5), f1(r.h2_y2025_A5), f1(r.h4_mean2425_A5), f1(r.h2_mean2125_uplift_usd_kw_yr_A5), str(r.basis_half_life), f"{r.conn_lead_months:.1f}",
                f"{r.constraint1_sf:+.2f}".replace("-", "−"), ("n/a" if pd.isna(r.constraint1_hops) else str(int(r.constraint1_hops))), str(r.county).title(), r.sub]
        if not pd.isna(r.reliability_need_rank):
            toks.append(f0(r.reliability_need_rank))
        miss = [t for t in toks if t not in row]
        if len(c) < 11 or miss:
            bad.append(f"{r.sub}: {miss}")
    out.append(Result("top-15 table equals data/sites.csv (15 rows, 11 fields each)", len(top) == 15 and not bad, "; ".join(bad[:4])))
    app = _table_after(lines, "| # | Sub | Zone | kV | Bus")
    bad = []
    for i, r in enumerate(s.itertuples()):
        row = app[i] if i < len(app) else ""
        toks = [r.sub, str(int(r.kv_max)), str(r.node_bus), f1(r.h2_mean2125_A5), f1(r.h2_y2025_A5), f1(r.h2_mean2125_uplift_usd_kw_yr_A5), f1(r.fwd_learner_2031), str(r.basis_half_life),
                f"{r.conn_lead_months:.1f}", f1(r.net_of_connection_usd_kw_yr), f"{r.constraint1_sf:+.2f}".replace("-", "−")]
        if not pd.isna(r.existing_resource_node):
            toks.append(str(r.existing_resource_node))
        miss = [t for t in toks if t not in row]
        if miss:
            bad.append(f"{r.sub}: {miss}")
    out.append(Result("Appendix A equals data/sites.csv (40 rows, queue order)", len(app) == N_SITES and not bad, "; ".join(bad[:4])))
    return out


def spot_checks(txt: str) -> list[Result]:
    """Numbers quoted in the text, recomputed from the CSVs. Each check lists README snippets that must be present and the values they must equal."""
    from scipy.stats import spearmanr

    from . import upgrade_rerank as R

    out = []
    s = _read("sites.csv")
    sy = _read("site_years.csv")
    up = _read("upgrades.csv")
    rr = _read("upgrade_rerank.csv")
    t2 = _read("tier2_valued.csv")
    rf = _read("risk_flags.csv")
    hp = _read("hops.csv")

    def chk(name, pairs):
        """pairs = [(README snippet, recomputed string)]."""
        bad = [f"README '{a}' vs data '{b}'" for a, b in pairs if a != b or a not in txt]
        out.append(Result(name, not bad, "; ".join(bad) if bad else " | ".join(f"{a}" for a, _ in pairs)))

    m = s[s["sub"] == "MIDW"].iloc[0]
    chk("1. MIDW 2 h learner 2021-25 and its half-life", [("270.4", f1(m.h2_mean2125_A5)), ("2028", str(m.basis_half_life))])
    k = s[s["sub"] == "KNAPP"].iloc[0]
    chk("2. KNAPP 4 h learner and 2021-25 value", [("112.4", f1(k.h4_mean2425_A5)), ("98.1", f1(k.h2_mean2125_A5))])
    h = sy[(sy["spec"] == "grid") & (sy["period"] == "mean_2021_25")]
    nodes, zones = h[h["role"] == "candidate"], h[h["role"] == "control_zone"]
    chk("3. mean learner at the 40 nodes and the 8 zones, 2021-25", [("$79.0", "$" + f1(nodes["usd_kw_yr_A5"].mean())), ("$44.4", "$" + f1(zones["usd_kw_yr_A5"].mean()))])
    n_a2 = int((nodes["usd_kw_yr_A2"] > nodes["usd_kw_yr_A5"]).sum())
    chk("4. Forecaster beats the learner at 34 of 40 nodes", [("34 of the 40 nodes", f"{n_a2} of the {len(nodes)} nodes"), ("$81.9", "$" + f1(nodes["usd_kw_yr_A2"].mean()))])
    chk("5. mean gain over the zone, 2025 replay and 2029", [("$38.5", "$" + f1(s["fwd_uplift_learner_2025_replay"].mean())), ("$20.0", "$" + f1(s["fwd_uplift_learner_2029"].mean())),
                                                          ("$33.4", "$" + f1(s["fwd_uplift_learner_2027"].mean()))])
    chk("6. battery against wires: median ratios", [("21 times", f"{up['nwa_vs_wires_ratio'].median():.0f} times"), ("192 times", f"{up['nwa_vs_cheapest_ratio'].median():.0f} times")])
    g = rr[(rr["scenario"] == "U03") & (rr["sub"] == "MIDW")].iloc[0]
    chk("7. MIDW after the Twin Buttes upgrade", [("$247.9 to $50.6", f"${f1(g.value_before_usd_kw_yr)} to ${f1(g.value_after_usd_kw_yr)}"), ("(#1 → #10)", f"(#{g.rank_before} → #{g.rank_after})"),
                                                  ("−30.7%", f"−{abs(g.scn_sum40_change_pct):.1f}%")])
    pa = R.pooled_agreement(rr)
    chk("8. first-order against full: baseline rank correlation and pooled pairs", [("0.83", f"{pa['rho_baseline_rank']:.2f}"), ("0.42", f"{pa['rho_delta']:.2f}"), ("87 (upgrade, site) pairs", f"{pa['n_moved']} (upgrade, site) pairs")])
    v = t2[t2["nodal_value_screened"].astype(str).str.lower() == "true"]
    chk("9. Tier 2: valued candidates, buses, mean gain 2025 and 2029", [("167 valued", f"{len(v)} valued"), ("150 buses", f"{v['route_desr_nodal_sub'].nunique()} buses"),
                                                                    ("+$26.4", "+$" + f1(v["nodal_vs_zone_uplift_usd_kw_yr"].mean())), ("+$11.8", "+$" + f1(v["nodal_vs_zone_uplift_2029_usd_kw_yr"].mean())),
                                                                    ("$24.2", "$" + f1(v["ader_zone_value_usd_kw_yr"].mean()))])
    rk = s.set_index("sub")["reliability_need_rank"]
    chk("10. Houston PK-DT sites in the reliability top 100", [("MID #10", f"MID #{int(rk['MID'])}"), ("BG #11", f"BG #{int(rk['BG'])}"), ("GT #62", f"GT #{int(rk['GT'])}"), ("PK #65", f"PK #{int(rk['PK'])}"),
                                                              ("four of the 40 sites", f"{ {1: 'one', 2: 'two', 3: 'three', 4: 'four', 5: 'five'}.get(int((rk <= 100).sum()), str(int((rk <= 100).sum()))) } of the 40 sites")])
    ex = rf[rf["exclude_flag"].astype(str).str.lower() == "true"]
    assessed = int((rf["risk_basis"] == "site").sum())
    chk("11. physical-risk exclusions", [("148 of 2,884", f"{len(ex)} of {assessed:,}")])
    gt = R.portfolio_groups_table()
    caps = gt.drop_duplicates("dominant_element")["group_cap_mw"].sum()
    summ_path = paths.DERIVED / "runs" / "portfolio_summary.json"
    a5 = json.loads(summ_path.read_text())["A5"]["usd_per_kw_yr_capped"] if summ_path.exists() else float("nan")
    chk("12. group caps total and the learner rule with caps", [("**220**", f"**{caps:.0f}**"), ("**35.4**", f"**{a5:.1f}**")])
    hh = hp[hp["hops"].notna() & (hp["hops"] < 99) & hp["sf"].notna()].copy()
    hh["a"] = hh["sf"].abs()
    rho = spearmanr(hh["hops"], hh["a"]).statistic
    big = hh[hh["a"] >= 0.10]
    chk("13. hops against shift factor", [("104,969", f"{len(hh):,}"), ("−0.42", f"−{abs(rho):.2f}"), ("1,760", f"{int((big['hops'] >= 8).sum()):,}"), ("46%", f"{100 * (big['hops'] >= 8).mean():.0f}%")])
    chk("14. net of connection and break-even", [("$22.0/kW-yr", f"${f1(s['net_of_connection_usd_kw_yr'].median())}/kW-yr"), ("$390/kW", f"${f0(s['breakeven_installed_usd_per_kw'].median())}/kW")])
    ncand = int((s["h2_mean2125_uplift_usd_kw_yr_A5"] > 0).sum())
    chk("15. sites that beat their zone and the import / export gains", [("38 of the 40", f"{ncand} of the 40"), ("+$40.1", "+$" + f1(s[s["dominant_side"] == "import"]["h2_mean2125_uplift_usd_kw_yr_A5"].mean())),
                                                                       ("+$11.9", "+$" + f1(s[s["dominant_side"] == "export"]["h2_mean2125_uplift_usd_kw_yr_A5"].mean()))])
    return out


RERANK_ROWS = {"U03": "**U03** Twin Buttes", "U06": "**U06** PK", "U01": "**U01** Scurry", "PLANNED_ALL": "**PLANNED_ALL**", "U05": "**U05** HIGH36", "U09": "**U09** Encrt",
               "U08": "**U08** Bell", "U10": "**U10** Salds", "PLANNED_2031": "**PLANNED_2031**", "U04": "**U04** WESTEX"}      # DERIVED: the first cell of each scenario's row in the README table of section 3.2


def _sp(x: float) -> str:
    """A signed percentage to one decimal in the README's style: a Unicode minus, no sign for zero."""
    v = round(float(x), 1)
    return "0.0" if v == 0 else f"{v:+.1f}".replace("-", "−")


def check_rerank_table(txt: str) -> list[Result]:
    """Section 3.2: sum of the 40 sites, largest loss, number of losers and the portfolio change (the 220 MW the caps allow) in each README row against data/upgrade_rerank.csv."""
    rr = _read("upgrade_rerank.csv")
    lines = txt.splitlines()
    bad = []
    for sid, key in RERANK_ROWS.items():
        g = rr[rr["scenario"] == sid]
        row = next((ln for ln in lines if ln.startswith("| " + key) and "full dispatch valuation" not in ln), "")        # the effects table of 3.2 has the same first cells
        r0 = g.iloc[0]
        toks = [f"{r0.scn_sum40_before_usd_kw_yr:,.0f} → {r0.scn_sum40_after_usd_kw_yr:,.0f} ({_sp(r0.scn_sum40_change_pct)}%)", f"{_sp(r0.scn_portfolio200_static_change_pct)}%",
                f"{r0.scn_largest_loss_site} {r0.scn_largest_loss_usd_kw_yr:.1f}".replace("-", "−")]
        if r0.scn_n_losers > 0:
            toks.append(f"{int(r0.scn_n_losers)} sites")
        miss = [t for t in toks if t not in row]
        if not row or miss:
            bad.append(f"{sid}: {miss or 'row not found'}")
    return [Result("section 3.2 rerank table equals data/upgrade_rerank.csv (10 evaluated scenarios)", not bad, "; ".join(bad))]


def check_disagreement_table(txt: str) -> list[Result]:
    """Section 3.3: the ten largest disagreements between the first-order and the full valuation, against data/upgrade_rerank.csv."""
    from . import upgrade_rerank as R

    d = R.disagreements(_read("upgrade_rerank.csv"), 10)
    lines = txt.splitlines()
    bad = []

    def m(x):
        return f"{x:.1f}".replace("-", "−")

    for r in d.itertuples():
        row = next((ln for ln in lines if ln.startswith(f"| {r.scenario} | {r.sub} | ")), "")
        toks = [m(r.delta_usd_kw_yr), m(r.fo_delta_usd_kw_yr), f"#{int(r.rank_before)} → #{int(r.rank_after)}", f"#{int(r.fo_rank_before)} → #{int(r.fo_rank_after)}"]
        miss = [t for t in toks if t not in row]
        if not row or miss:
            bad.append(f"{r.scenario} {r.sub}: {miss or 'row not found'}")
    return [Result("section 3.3 disagreement table equals data/upgrade_rerank.csv (10 rows)", not bad, "; ".join(bad))]


def check_tier2_table(txt: str) -> list[Result]:
    """Section 6: the results table (all valued candidates, and split by the distance of the settlement bus from its line) against data/tier2_valued.csv."""
    t2 = _read("tier2_valued.csv")
    v = t2[t2["nodal_value_screened"].astype(str).str.lower() == "true"]
    groups = [v, v[v["nodal_distance_class"] == "within_150km"], v[v["nodal_distance_class"] == "beyond_150km"], v[v["nodal_distance_class"] == "element_not_placed"]]

    def mean(g, c):
        return f1(g[c].mean())

    def both(g, c):
        return f"+${f1(g[c].mean())} (+${f1(g[c].median())})"

    toks = ["Within 150 km of the line (%d) | Beyond 150 km (%d) | Line not placed (%d)" % tuple(len(g) for g in groups[1:])]
    toks.append("| DESR nodal value, 2025 replay, mean | " + " | ".join("$" + mean(g, "nodal_value_usd_kw_yr") for g in groups) + " |")
    toks.append("| ADER zone value of the same battery, mean | " + " | ".join("$" + mean(g, "ader_zone_value_usd_kw_yr") for g in groups) + " |")
    toks.append("| **Gain of the nodal route, 2025: mean (median)** | **" + both(groups[0], "nodal_vs_zone_uplift_usd_kw_yr").replace(" (", "** (", 1) + " | **"
                + both(groups[1], "nodal_vs_zone_uplift_usd_kw_yr").replace(" (", "** (", 1) + " | " + both(groups[2], "nodal_vs_zone_uplift_usd_kw_yr") + " | " + both(groups[3], "nodal_vs_zone_uplift_usd_kw_yr") + " |")
    toks.append("| Gain of the nodal route, 2029 after saturation: mean (median) | " + " | ".join(both(g, "nodal_vs_zone_uplift_2029_usd_kw_yr") for g in groups) + " |")
    miss = [t for t in toks if t not in txt]
    return [Result("section 6 Tier 2 table equals data/tier2_valued.csv (all, near, far, not placed)", not miss, str(miss))]


def check_phase6_outputs() -> list[Result]:
    out = []
    rr = _read("upgrade_rerank.csv")
    out.append(Result("upgrade_rerank.csv: 12 scenarios x 40 sites", len(rr) == 480 and rr["scenario"].nunique() == 12 and rr.groupby("scenario")["sub"].nunique().eq(40).all(), f"{len(rr)} rows"))
    d = (rr["value_after_usd_kw_yr"] - rr["value_before_usd_kw_yr"] - rr["delta_usd_kw_yr"]).abs().max()
    out.append(Result("upgrade_rerank.csv: delta = after - before", d < 2e-3, f"max {d:.5f}"))
    nr = rr[rr["scenario_effect"] == "not_replayed"]
    out.append(Result("upgrade_rerank.csv: the two not-replayed upgrades (Vealmoor-Koch Tap, Longshore-Consavvy) move nothing", len(nr) == 80 and nr["delta_usd_kw_yr"].abs().max() < 1e-9 and set(nr["scenario"]) == {"U02", "U07"}, f"{nr['scenario'].unique().tolist()}"))
    base = rr.groupby("scenario")["rank_before"].apply(list)
    out.append(Result("upgrade_rerank.csv: the 'before' ranks are the same in every scenario", all(base.iloc[0] == b for b in base)))
    s = _read("sites.csv").set_index("sub")
    b = rr[rr["scenario"] == "U01"].set_index("sub")["value_before_usd_kw_yr"]
    out.append(Result("upgrade_rerank.csv: 'before' equals sites.csv fwd_learner_2025_replay", (b - s.loc[b.index, "fwd_learner_2025_replay"]).abs().max() < 2e-3))
    t0, t2 = _read("tier2.csv"), _read("tier2_valued.csv")
    out.append(Result("tier2_valued.csv: all tier2.csv columns present, same rows", list(t2.columns[: len(t0.columns)]) == list(t0.columns) and (t2["cand_id"] == t0["cand_id"]).all(), f"{len(t2)} rows, {len(t2.columns)} columns"))
    same = all(((t0[c] == t2[c]) | (t0[c].isna() & t2[c].isna())).all() for c in ("reliability_need_score", "deferral_usd_kw_yr_mid", "voll_usd_yr_duration", "need_rank"))
    out.append(Result("tier2_valued.csv: reliability and deferral columns untouched, separate from market value", same))
    scr = t2[t2["nodal_value_screened"].astype(str).str.lower() == "true"]
    out.append(Result("tier2_valued.csv: at most 150 buses valued", scr["route_desr_nodal_sub"].nunique() <= 150, f"{scr['route_desr_nodal_sub'].nunique()} buses"))
    zone = t2[~t2["nodal_value_screened"].astype(str).str.lower().eq("true")]
    out.append(Result("tier2_valued.csv: unscreened candidates carry the zone value, gain 0, and a label",
                      (zone["nodal_vs_zone_uplift_usd_kw_yr"] == 0).all() and zone["nodal_value_source"].str.startswith("zone_value").all()))
    zv = t2.groupby("route_ader_zone")["ader_zone_value_usd_kw_yr"].nunique()
    out.append(Result("tier2_valued.csv: the ADER route carries one load-zone value per zone", (zv == 1).all()))
    bad = [c for c in t2.columns if re.search(r"(total|combined|sum)_", c)]
    out.append(Result("tier2_valued.csv: no column adds market value to reliability or deferral dollars", not bad, str(bad)))
    return out


def check_map() -> list[Result]:
    out = []
    ok = MAP_HTML.exists() and MAP_DATA.exists()
    out.append(Result("map files exist", ok))
    if not ok:
        return out
    size = (MAP_HTML.stat().st_size + MAP_DATA.stat().st_size) / 1e6
    out.append(Result("map data under 5 MB", size < MAP_MAX_MB, f"{size:.2f} MB"))
    h = MAP_HTML.read_text(encoding="utf-8")
    out.append(Result("map: Leaflet 1.9.4 from cdnjs with an exact version", "cdnjs.cloudflare.com/ajax/libs/leaflet/1.9.4/leaflet.min.js" in h and "leaflet.min.css" in h))
    out.append(Result("map: OpenStreetMap tiles with attribution", "tile.openstreetmap.org" in h and "OpenStreetMap" in h and "contributors" in h))
    out.append(Result("map: light and dark chrome, viewport, 16 px gutter", "prefers-color-scheme: dark" in h and 'name="viewport"' in h and "--gutter:16px" in h))
    out.append(Result("map: data comes in through a script tag (opens as a local file)", '<script src="index_data.js">' in h and "fetch(" not in h))
    out.append(Result("map: regional candidates and measured basis layers are in the page", 'id="l-reg"' in h and 'id="l-basis"' in h and 'id="regsum"' in h))
    txt = MAP_DATA.read_text(encoding="utf-8")
    try:
        body = txt[len("window.GRID_REPORT_MAP="):].rstrip()
        payload = json.loads(body[:-1] if body.endswith(";") else body)
        counts = dict(subs=len(payload["subs"]["sub"]), sites=len(payload["sites"]["rows"]), constraints=len(payload["constraints"]), upgrades=len(payload["upgrades"]),
                      excluded=len(payload["excluded"]), reliability=len(payload["reliability"]), tier2=len(payload["tier2"]), regional=len(payload["regional"]["rows"]),
                      basis=len(payload["basis"]["sp"]), regions=len(payload["region_summary"]))
        rf = _read("risk_flags.csv")
        n_excl = int((rf["exclude_flag"].astype(str).str.lower() == "true").sum())
        ex = _read("extra_sites.csv")
        rb = _read("regional_basis.csv")
        good = (counts["sites"] == N_SITES and counts["reliability"] == 100 and counts["excluded"] == n_excl and counts["upgrades"] == 10 and counts["subs"] > 3000
                and counts["regional"] == int((ex["role"] == "regional").sum()) and counts["basis"] == int(rb["lat"].notna().sum()) and counts["regions"] >= 5)
        out.append(Result("map payload parses and has every layer (40 sites, regional candidates, regional basis)", good, str(counts)))
        out.append(Result("map payload holds the regional layers' keys", all(k in payload for k in ("regional", "basis", "region_summary")), ""))
    except Exception as ex:                                  # noqa: BLE001
        out.append(Result("map payload parses and has every layer", False, repr(ex)))
    return out


NUMBER_WORDS = {1: "one", 2: "two", 3: "three", 4: "four", 5: "five", 6: "six", 7: "seven", 8: "eight", 9: "nine", 10: "ten"}


def _word(n: int, cap: bool = False) -> str:
    w = NUMBER_WORDS.get(int(n), str(int(n)))
    return w.capitalize() if cap else w


def check_revision2(txt: str) -> list[Result]:
    """Revision 2: the tables generated by gridsite.report_tables appear in the README verbatim (so they cannot drift from their CSVs), and the counts and amounts the
    new sections quote equal the CSVs they come from."""
    from . import report_tables as RT

    out = []
    gens = [("anchors", RT.anchors_table), ("regions", RT.region_summary_table), ("targets", RT.targets_table), ("picks", RT.regional_picks_table), ("picks_top", RT.regional_top_table),
            ("churn", RT.churn_table), ("sens_top10", RT.sens_top10_table), ("sens_summary", RT.sens_summary_table), ("upgrade_effects", lambda: RT.upgrade_effects_table()[0])]
    for name, fn in gens:
        lines = fn()
        miss = [ln for ln in lines if ln not in txt]
        out.append(Result(f"README table '{name}' equals its CSV (every row verbatim)", len(lines) > 2 and not miss, f"{len(lines) - 2} rows" if not miss else f"{len(miss)} rows differ: {miss[0][:110]}"))
    s, r1, ex = _read("sites.csv"), _read("site_candidates_rev1.csv"), _read("extra_sites.csv")
    _, n_moving = RT.upgrade_effects_table()
    ph = f"{_word(n_moving, True)} of the ten upgrades now move at least one site"
    out.append(Result("section 3.2 says how many of the ten upgrades move a site", ph in txt, ph))
    enter, leave = set(s["sub"]) - set(r1["sub"]), set(r1["sub"]) - set(s["sub"])
    ph = f"{_word(len(enter), True)} sites enter the list and {_word(len(leave))} leave it"
    out.append(Result("'What changed' says how many sites entered and left the 40", ph in txt, ph))
    out.append(Result("extra_sites.csv: 41 regional picks and the 5 sites that left the 40, none of them in the 40",
                      (ex["role"] == "regional").sum() == 41 and (ex["role"] == "rev1_only").sum() == len(leave) == 5 and not (set(ex["sub"]) & set(s["sub"])), str(ex["role"].value_counts().to_dict())))
    cs = _read("constraint_structure_rev2.csv")
    rent = float((cs["sph_usd_mw_yr"] * cs["median_limit_mw"]).sum() / 1e6)
    geo = int((cs["driver_basis"] == "geo").sum())
    toks = [f"{len(cs)} of 1,386 constraints enter", f"${rent:,.0f}M of replayed rent", f"{geo} of the {len(cs)} replayed constraints ({100 * geo / len(cs):.0f}%)"]
    miss = [t for t in toks if t not in txt]
    out.append(Result("section 9: replayed constraints, rent and geographic-rule share equal constraint_structure_rev2.csv", not miss, str(miss) if miss else "; ".join(toks)))
    lv = _read("lane_validation.csv")
    v = lv[(lv["check"] == "a_window_rebuild") & lv["scope"].str.startswith("3-10 Oct, all intervals | all replayed")].set_index("metric")["value"]
    toks = [f"correlation {v['corr_pooled']:.3f} and a mean error of ${v['mae_usd_mwh']:.2f}/MWh"]
    miss = [t for t in toks if t not in txt]
    out.append(Result("sections 2.1 and 9: the lane validation equals lane_validation.csv", not miss, str(miss) if miss else toks[0]))
    anc = _read("imm_2026_anchors.csv")
    da = anc[(anc["market"] == "DA") & (anc["metric"] == "rent_musd") & anc["code"].isin(["SEA_AAT1", "MDOPHR99_A"])].set_index("code")["ytd"]
    toks = [f"SEA 138/69 kV (${da['SEA_AAT1']:.1f}M)", f"MDO–PHR (${da['MDOPHR99_A']:.1f}M)"]
    miss = [t for t in toks if t not in txt]
    out.append(Result("deviation D1: the two day-ahead-only rents equal imm_2026_anchors.csv", not miss, str(miss) if miss else "; ".join(toks)))
    log = _read("anchor_2026_applied.csv")
    n_el, n_used = log["constraint_name"].nunique(), log[log["used_in_primary"].astype(str).str.lower() == "true"]["constraint_name"].nunique()
    ph = f"{_word(n_el, True)} elements qualify"
    out.append(Result("'What changed': the number of elements re-anchored on a 2026 rent", ph in txt and n_el == 6 and n_used == 5, f"{n_el} qualify, {n_used} reach a lane"))
    go = _read("geo_overrides.csv")
    rc = _read("regional_candidates.csv")
    rr = rc[rc["role"] == "regional"]
    toks = [f"{len(go)} overrides", f"{len(rr)} regional picks", f"{int((rr['basis_source'] == 'measured').sum())} of the {len(rr)} measured"]
    miss = [t for t in toks if t not in txt]
    out.append(Result("G1 and R1 counts quoted (overrides, regional picks, measured picks)", not miss, str(miss) if miss else "; ".join(toks)))
    sm = _read("anchor_sensitivity_summary.csv")
    out.append(Result("anchor_sensitivity_summary.csv has the primary and four variants", {"geo_only", "anchors2025", "anchors2026da", "panhandle"} <= set(sm["variant"]), str(sm["variant"].tolist())))
    return out


# ------------------------------------------------------------------------------------------------------------------------------------ read-only guard and recomputation
def _snapshot() -> dict:
    """(size, mtime in ns) of every file that report-check must not change: the tables, the map, the README, the lane and structure caches, the value cache."""
    files = list(DATA.glob("*.csv")) + list(paths.MAP.glob("*")) + [README]
    files += list(paths.DERIVED.glob("nodal_lanes_*")) + [paths.DERIVED / "runs" / "value_cache.json", paths.DERIVED / "runs" / "forward_meta.json"]
    out = {}
    for f in files:
        try:
            st = f.stat()
            out[str(f)] = (st.st_size, st.st_mtime_ns)
        except OSError:
            out[str(f)] = None
    return out


def check_recomputation() -> list[Result]:
    """The committed structure tables and the stored lane file against a clean recomputation (in memory; nothing is saved)."""
    from . import nodal_lanes as L
    from . import rev2
    from . import sites as S

    out = []
    if rev2.REVISION == "1":
        return [Result("recomputation checks", True, "GRIDSITE_REVISION=1: nothing to compare")]
    base = L._r2_base_structure(use_cache=False)
    committed = _read("constraint_synth.csv")
    key = ["cid", "sph_usd_mw_yr", "H_hours", "cap_usd_mwh", "median_limit_mw", "driver_sign"]
    same = list(base["cid"]) == list(committed["cid"]) and all(np.allclose(base[k].astype(float), committed[k].astype(float), rtol=1e-6, atol=1e-9) for k in key[1:]) \
        and list(base["driver_location"]) == list(committed["driver_location"])
    out.append(Result("constraint_synth.csv equals a clean recomputation of the Phase 3b structure", same, f"{len(base)} rows"))
    eff = L.constraint_structure(use_cache=False)
    tab = _read(rev2.EFFECTIVE_CSV)
    same = list(eff["cid"]) == list(tab["cid"]) and all(np.allclose(eff[k].astype(float), tab[k].astype(float), rtol=1e-6, atol=1e-9) for k in key[1:]) \
        and list(eff["driver_location"]) == list(tab["driver_location"]) and list(eff["sf_method"]) == list(tab["sf_method"])
    out.append(Result("constraint_structure_rev2.csv equals a clean recomputation of the structure in force", same, f"{len(eff)} rows, switches {rev2.config()}"))
    log = _read(rev2.ANCHOR_LOG_CSV)
    mism = [c for c, v in zip(log["cid"], log["sph_after"]) if c in set(eff["cid"]) and abs(float(eff.set_index("cid").at[c, "sph_usd_mw_yr"]) - float(v)) > 1e-3 * max(1.0, abs(float(v)))]
    out.append(Result("anchor_2026_applied.csv rows agree with the structure", not mism and (log["used_in_primary"].astype(str).str.lower() == "true").sum() == (log["cid"].isin(set(eff["cid"]))).sum(), f"{len(log)} rows, mismatches {mism[:3]}"))
    subs = S.lane_subs()
    meta = L.lane_file_meta(S.LANE_TAG)
    out.append(Result("stored lane file carries the key of a fresh computation", meta["sig"] == L.lane_signature(subs), f"{len(subs)} substation lanes"))
    try:
        S.check_provenance((S.SPEC_2H, S.SPEC_4H))
        out.append(Result("forward values were computed from the stored lanes and the stored ratios", True))
    except Exception as ex:                                  # noqa: BLE001
        out.append(Result("forward values were computed from the stored lanes and the stored ratios", False, str(ex)[:160]))
    return out


# ------------------------------------------------------------------------------------------------------------------------------------ entry point
def run(*args) -> int:
    from . import nodal_lanes as L

    if not README.exists():
        print("README.md is missing")
        return 1
    txt = README.read_text(encoding="utf-8")
    results = []
    before = _snapshot()
    L.READ_ONLY = True                                       # nothing in this process may write a cache, a table or the README
    try:
        for fn in (check_labels_all, lambda: check_structure(txt), lambda: check_tables(txt), lambda: spot_checks(txt), lambda: check_rerank_table(txt), lambda: check_disagreement_table(txt),
                   lambda: check_tier2_table(txt), check_phase6_outputs, lambda: check_revision2(txt), check_recomputation, check_map):
            try:
                results += fn()
            except Exception as ex:                          # noqa: BLE001
                results.append(Result(getattr(fn, "__name__", "check"), False, f"raised {ex!r}"))
    finally:
        L.READ_ONLY = False
    after = _snapshot()
    changed = [Path(k).name for k in after if after[k] != before.get(k)]
    results.append(Result("report-check wrote nothing (tables, map, README, lane and structure caches unchanged)", not changed, ", ".join(changed[:6])))
    w = max(len(r.name) for r in results)
    for r in results:
        print(f"{'PASS' if r.ok else 'FAIL'}  {r.name:{w}s}  {r.detail}")
    bad = [r for r in results if not r.ok]
    print(f"{len(results) - len(bad)} of {len(results)} checks pass" + (f"; FAILED: {', '.join(r.name for r in bad)}" if bad else ""))
    if bad:
        sys.exit(1)
    return 0
