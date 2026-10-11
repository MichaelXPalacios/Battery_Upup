"""Report checks: does README.md say what the CSVs say? (run_grid.py report-check)

  1. every data/*.csv has a label sidecar that covers every column (gridsite.check_labels);
  2. the README has its sections, 5-7 TL;DR bullets, the 32-item constraint table and the two site tables, and the numbers in the top-15 table and Appendix A equal
     the numbers in data/sites.csv, row by row;
  3. spot-checks of the numbers quoted in the text against the CSVs (each check names the README text, the value recomputed from the data, and whether they agree);
  4. the outputs of Phase 6 (upgrade_rerank.csv, tier2_valued.csv) satisfy their invariants;
  5. the map: files, size, Leaflet 1.9.4 from cdnjs, OpenStreetMap attribution, dark and light chrome, a viewport meta tag, the payload parses.

Prints one line per check and exits with status 1 if any fails. Reads only files; writes nothing.
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
SECTIONS = ["## TL;DR", "## 1. Where to put batteries", "## 2. The physical-constraints map", "## 3. Upgrades", "## 4. Trading after placement",
            "## 5. Reliability and physical risk", "## 6. Tier 2", "## 7. How this feeds the dispatch platform", "## 8. Confidence and limits", "## 9. Reproduce"]
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
    tl = txt[txt.find("## TL;DR"): txt.find("## 1. Where to put batteries")]
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
                f"{r.constraint1_sf:+.2f}".replace("-", "−"), str(int(r.constraint1_hops)), str(r.county).title(), r.sub]
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
    chk("1. MIDW 2 h learner 2021-25 and its half-life", [("269.7", f1(m.h2_mean2125_A5)), ("2028", str(m.basis_half_life))])
    k = s[s["sub"] == "KNAPP"].iloc[0]
    chk("2. KNAPP 4 h learner and 2021-25 value", [("111.4", f1(k.h4_mean2425_A5)), ("98.2", f1(k.h2_mean2125_A5))])
    h = sy[(sy["spec"] == "grid") & (sy["period"] == "mean_2021_25")]
    nodes, zones = h[h["role"] == "candidate"], h[h["role"] == "control_zone"]
    chk("3. mean learner at the 40 nodes and the 8 zones, 2021-25", [("$78.3", "$" + f1(nodes["usd_kw_yr_A5"].mean())), ("$44.4", "$" + f1(zones["usd_kw_yr_A5"].mean()))])
    n_a2 = int((nodes["usd_kw_yr_A2"] > nodes["usd_kw_yr_A5"]).sum())
    chk("4. Forecaster beats the learner at 36 of 40 nodes", [("36 of the 40 nodes", f"{n_a2} of the {len(nodes)} nodes"), ("$81.0", "$" + f1(nodes["usd_kw_yr_A2"].mean()))])
    chk("5. mean gain over the zone, 2025 replay and 2029", [("$38.4", "$" + f1(s["fwd_uplift_learner_2025_replay"].mean())), ("$19.3", "$" + f1(s["fwd_uplift_learner_2029"].mean())),
                                                          ("$33.1", "$" + f1(s["fwd_uplift_learner_2027"].mean()))])
    chk("6. battery against wires: median ratios", [("21 times", f"{up['nwa_vs_wires_ratio'].median():.0f} times"), ("192 times", f"{up['nwa_vs_cheapest_ratio'].median():.0f} times")])
    g = rr[(rr["scenario"] == "U03") & (rr["sub"] == "MIDW")].iloc[0]
    chk("7. MIDW after the Twin Buttes upgrade", [("$247.7 to $51.0", f"${f1(g.value_before_usd_kw_yr)} to ${f1(g.value_after_usd_kw_yr)}"), ("(#1 → #9)", f"(#{g.rank_before} → #{g.rank_after})"),
                                                  ("−31.2%", f"−{abs(g.scn_sum40_change_pct):.1f}%")])
    pa = R.pooled_agreement(rr)
    chk("8. first-order against full: baseline rank correlation and pooled pairs", [("0.89", f"{pa['rho_baseline_rank']:.2f}"), ("0.46", f"{pa['rho_delta']:.2f}"), ("76 (upgrade, site) pairs", f"{pa['n_moved']} (upgrade, site) pairs")])
    v = t2[t2["nodal_value_screened"].astype(str).str.lower() == "true"]
    chk("9. Tier 2: valued candidates, buses, mean gain 2025 and 2029", [("172 valued", f"{len(v)} valued"), ("150 buses", f"{v['route_desr_nodal_sub'].nunique()} buses"),
                                                                    ("+$27.0", "+$" + f1(v["nodal_vs_zone_uplift_usd_kw_yr"].mean())), ("+$13.0", "+$" + f1(v["nodal_vs_zone_uplift_2029_usd_kw_yr"].mean())),
                                                                    ("$23.9", "$" + f1(v["ader_zone_value_usd_kw_yr"].mean()))])
    rk = s.set_index("sub")["reliability_need_rank"]
    chk("10. Houston PK-DT sites in the reliability top 100", [("MID #10", f"MID #{int(rk['MID'])}"), ("BG #11", f"BG #{int(rk['BG'])}"), ("GT #62", f"GT #{int(rk['GT'])}"), ("PK #64", f"PK #{int(rk['PK'])}"),
                                                              ("four of the 40 sites", f"{ {1: 'one', 2: 'two', 3: 'three', 4: 'four', 5: 'five'}.get(int((rk <= 100).sum()), str(int((rk <= 100).sum()))) } of the 40 sites")])
    ex = rf[rf["exclude_flag"].astype(str).str.lower() == "true"]
    assessed = int((rf["risk_basis"] == "site").sum())
    chk("11. physical-risk exclusions", [("147 of 2,869", f"{len(ex)} of {assessed:,}")])
    gt = R.portfolio_groups_table()
    caps = gt.drop_duplicates("dominant_element")["group_cap_mw"].sum()
    summ_path = paths.DERIVED / "runs" / "portfolio_summary.json"
    a5 = json.loads(summ_path.read_text())["A5"]["usd_per_kw_yr_capped"] if summ_path.exists() else float("nan")
    chk("12. group caps total and the learner rule with caps", [("**200**", f"**{caps:.0f}**"), ("**34.9**", f"**{a5:.1f}**")])
    hh = hp[hp["hops"].notna() & (hp["hops"] < 99) & hp["sf"].notna()].copy()
    hh["a"] = hh["sf"].abs()
    rho = spearmanr(hh["hops"], hh["a"]).statistic
    big = hh[hh["a"] >= 0.10]
    chk("13. hops against shift factor", [("95,948", f"{len(hh):,}"), ("−0.41", f"−{abs(rho):.2f}"), ("1,787", f"{int((big['hops'] >= 8).sum()):,}"), ("47%", f"{100 * (big['hops'] >= 8).mean():.0f}%")])
    chk("14. net of connection and break-even", [("$21.9/kW-yr", f"${f1(s['net_of_connection_usd_kw_yr'].median())}/kW-yr"), ("$389/kW", f"${f0(s['breakeven_installed_usd_per_kw'].median())}/kW")])
    ncand = int((s["h2_mean2125_uplift_usd_kw_yr_A5"] > 0).sum())
    chk("15. sites that beat their zone and the import / export gains", [("37 of the 40", f"{ncand} of the 40"), ("+$40.1", "+$" + f1(s[s["dominant_side"] == "import"]["h2_mean2125_uplift_usd_kw_yr_A5"].mean())),
                                                                       ("+$10.9", "+$" + f1(s[s["dominant_side"] == "export"]["h2_mean2125_uplift_usd_kw_yr_A5"].mean()))])
    return out


RERANK_ROWS = {"U03": "**U03** Twin Buttes", "U06": "**U06** PK", "U01": "**U01** Scurry", "PLANNED_ALL": "**PLANNED_ALL**", "U05": "**U05** HIGH36", "U09": "**U09** Encrt",
               "U10": "**U10** Salds", "PLANNED_2031": "**PLANNED_2031**"}      # DERIVED: the first cell of each scenario's row in the README table of section 3.2


def check_rerank_table(txt: str) -> list[Result]:
    """Section 3.2: sum of the 40 sites, largest loss, number of losers and the 200 MW portfolio change in each README row against data/upgrade_rerank.csv."""
    rr = _read("upgrade_rerank.csv")
    lines = txt.splitlines()
    bad = []
    for sid, key in RERANK_ROWS.items():
        g = rr[rr["scenario"] == sid]
        row = next((ln for ln in lines if ln.startswith("| " + key)), "")
        r0 = g.iloc[0]
        toks = [f"{r0.scn_sum40_before_usd_kw_yr:,.0f} → {r0.scn_sum40_after_usd_kw_yr:,.0f} ({r0.scn_sum40_change_pct:+.1f}%)".replace("-", "−"),
                f"{r0.scn_portfolio200_static_change_pct:+.1f}%".replace("-", "−"), f"{r0.scn_largest_loss_site} {r0.scn_largest_loss_usd_kw_yr:.1f}".replace("-", "−")]
        if r0.scn_n_losers > 0:
            toks.append(f"{int(r0.scn_n_losers)} sites")
        miss = [t for t in toks if t not in row]
        if not row or miss:
            bad.append(f"{sid}: {miss or 'row not found'}")
    return [Result("section 3.2 rerank table equals data/upgrade_rerank.csv (8 evaluated scenarios)", not bad, "; ".join(bad))]


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
    out.append(Result("upgrade_rerank.csv: the four not-replayed upgrades move nothing", len(nr) == 160 and nr["delta_usd_kw_yr"].abs().max() < 1e-9, f"{nr['scenario'].unique().tolist()}"))
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
    txt = MAP_DATA.read_text(encoding="utf-8")
    try:
        body = txt[len("window.GRID_REPORT_MAP="):].rstrip()
        payload = json.loads(body[:-1] if body.endswith(";") else body)
        counts = dict(subs=len(payload["subs"]["sub"]), sites=len(payload["sites"]["rows"]), constraints=len(payload["constraints"]), upgrades=len(payload["upgrades"]),
                      excluded=len(payload["excluded"]), reliability=len(payload["reliability"]), tier2=len(payload["tier2"]))
        good = counts["sites"] == N_SITES and counts["reliability"] == 100 and counts["excluded"] == 147 and counts["upgrades"] == 10 and counts["subs"] > 3000
        out.append(Result("map payload parses and has every layer", good, str(counts)))
    except Exception as ex:                                  # noqa: BLE001
        out.append(Result("map payload parses and has every layer", False, repr(ex)))
    return out


# ------------------------------------------------------------------------------------------------------------------------------------ entry point
def run(*args) -> int:
    if not README.exists():
        print("README.md is missing")
        return 1
    txt = README.read_text(encoding="utf-8")
    results = []
    for fn in (check_labels_all, lambda: check_structure(txt), lambda: check_tables(txt), lambda: spot_checks(txt), lambda: check_rerank_table(txt), lambda: check_disagreement_table(txt), lambda: check_tier2_table(txt),
               check_phase6_outputs, check_map):
        try:
            results += fn()
        except Exception as ex:                              # noqa: BLE001
            results.append(Result(getattr(fn, "__name__", "check"), False, f"raised {ex!r}"))
    w = max(len(r.name) for r in results)
    for r in results:
        print(f"{'PASS' if r.ok else 'FAIL'}  {r.name:{w}s}  {r.detail}")
    bad = [r for r in results if not r.ok]
    print(f"{len(results) - len(bad)} of {len(results)} checks pass" + (f"; FAILED: {', '.join(r.name for r in bad)}" if bad else ""))
    if bad:
        sys.exit(1)
    return 0
