#!/usr/bin/env python3
"""Export the ERCOT physical-grid siting study (docs/research/grid/) to the story app's Siting page:
ui/data/siting/index.json (hb.siting.v1) and ui/data/siting/texas.json (hb.texas.v1, the state outline).

    <python with numpy> scripts/export_siting.py

Reads (never writes):
  docs/research/grid/data/*.csv + *.labels.csv   sites, site_years, upgrade_rerank, upgrades, hops, regional_basis_summary,
                                                 extra_sites, tier2_valued, risk_flags, reliability, constraints
  docs/research/grid/README.md                   five small tables the study only prints (the element table of 1.1, the
                                                 group caps and the portfolio result of 1.3, the yearly fade of 4.4); each is
                                                 cross-checked against the CSVs before it is used
  docs/research/grid/map/index_data.js           the drawn constraint lines (line ends, placed by the geography audit)
  $GRID_GEO (default ../research-data/grid/raw/geo/cb_2023_us_state_20m_kml/extracted/cb_2023_us_state_20m.kml)
                                                 US Census cartographic-boundary state file 2023 (20m KML, public domain),
                                                 downloaded from https://www2.census.gov/geo/tiger/GENZ2023/kml/cb_2023_us_state_20m.zip

Every column keeps the label of its .labels.csv sidecar (REAL / DERIVED / SIM / ASSUMPTION); headline numbers are labelled dicts
and bulk columns are labelled once in `series`. Node prices are SIM (levels good to about a factor of two, rankings steadier).
Deterministic: the same inputs give byte-identical output. No language model produces any number here.
"""
import csv
import hashlib
import json
import math
import os
import re
import sys
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from sim.contracts import envelope, labelled, write_json  # noqa: E402

GRID = ROOT / "docs" / "research" / "grid"
DATA = GRID / "data"
OUT = ROOT / "ui" / "data" / "siting"
REPORT = "docs/research/grid/README.md"
GEO = Path(os.environ.get("GRID_GEO", str(ROOT.parent / "research-data" / "grid" / "raw" / "geo" / "cb_2023_us_state_20m_kml"
                                          / "extracted" / "cb_2023_us_state_20m.kml")))
OUTLINE_MAX_POINTS = 300
ALGOS = [("A1", "Clockwork"), ("A2", "Forecaster"), ("A3", "Weather-regime"), ("A4", "Spike hunter"), ("A5", "Adaptive learner")]
YEARS = [2025, 2026, 2027, 2028, 2029, 2030, 2031]          # 2025 is the replay year
# the ten upgrade elements as the README prose names them (data/upgrades.csv prints them in upper case)
UPGRADE_NAMES = {"U01": "Scurry Chevron–Knapp 138 kV", "U02": "Vealmoor–Koch Tap 138 kV", "U03": "Twin Buttes–Hargrove 138 kV",
                 "U04": "WESTEX interface", "U05": "HIGH36–Brenno 138 kV", "U06": "PK–DT 138 kV (Houston)", "U07": "Longshore–Consavvy 345 kV",
                 "U08": "Bell County–Salado 138 kV", "U09": "Encrt–Brand 138 kV", "U10": "Salds–Sonterra 138 kV",
                 "PLANNED_2031": "Every planned upgrade in service by 2031", "PLANNED_ALL": "Every dated planned upgrade, including 2032 and later"}


# ---------------------------------------------------------------- readers
def rd(name):
    with open(DATA / f"{name}.csv", encoding="utf-8", newline="") as fh:
        return list(csv.DictReader(fh))


def sidecar(name):
    with open(DATA / f"{name}.labels.csv", encoding="utf-8", newline="") as fh:
        return {r["column"]: r for r in csv.DictReader(fh)}


def fl(x):
    return None if x in ("", None) else float(x)


def r2(x, nd=3):
    return None if x is None else round(float(x), nd)


def yes(x):
    return str(x).strip().lower() in ("true", "1", "yes")


def lab(v, label, cite):
    return labelled(r2(v) if isinstance(v, (float, np.floating)) else (int(v) if isinstance(v, (np.integer,)) else v), label, cite)


def mean(a):
    a = [x for x in a if x is not None]
    return float(np.mean(a)) if a else None


def rankdata(a):
    a = np.asarray(a, float)
    _, inv, cnt = np.unique(a, return_inverse=True, return_counts=True)
    cs = np.cumsum(cnt)
    return ((cs - cnt + 1 + cs) / 2.0)[inv]


def md_table(heading, header_has, nth=0):
    """Rows (lists of cell strings) of the nth markdown table under the heading whose header row contains `header_has`."""
    lines = (GRID / "README.md").read_text(encoding="utf-8").splitlines()
    start = next(i for i, ln in enumerate(lines) if ln.startswith("#") and heading in ln)
    seen = 0
    i = start + 1
    while i < len(lines):
        if lines[i].startswith("#") and i > start + 1 and not lines[i].startswith("####"):
            break
        if lines[i].startswith("|") and header_has in lines[i]:
            if seen == nth:
                rows = []
                j = i + 2
                while j < len(lines) and lines[j].startswith("|"):
                    rows.append([c.strip() for c in lines[j].strip().strip("|").split("|")])
                    j += 1
                return rows
            seen += 1
        i += 1
    raise ValueError(f"README table not found: {heading!r} / {header_has!r}")


def cell_num(s):
    """First number in a README cell ('**35.4**', '$1,030', '137.0 / 945' -> 137.0)."""
    m = re.search(r"-?\d[\d,]*\.?\d*", s.replace("−", "-"))
    return float(m.group(0).replace(",", "")) if m else None


# ---------------------------------------------------------------- the Texas outline (US Census, REAL)
def douglas_peucker(pts, eps):
    pts = np.asarray(pts, float)
    keep = np.zeros(len(pts), bool)
    keep[0] = keep[-1] = True
    stack = [(0, len(pts) - 1)]
    while stack:
        a, b = stack.pop()
        if b <= a + 1:
            continue
        p, q = pts[a], pts[b]
        d = q - p
        norm = math.hypot(d[0], d[1])
        seg = pts[a + 1:b]
        if norm == 0:
            dist = np.hypot(seg[:, 0] - p[0], seg[:, 1] - p[1])
        else:
            dist = np.abs(d[0] * (seg[:, 1] - p[1]) - d[1] * (seg[:, 0] - p[0])) / norm
        k = int(np.argmax(dist))
        if dist[k] > eps:
            m = a + 1 + k
            keep[m] = True
            stack += [(a, m), (m, b)]
    return pts[keep]


def inside(poly, lon, lat):
    c = False
    n = len(poly)
    for i in range(n):
        x1, y1 = poly[i]
        x2, y2 = poly[(i + 1) % n]
        if (y1 > lat) != (y2 > lat) and lon < (x2 - x1) * (lat - y1) / (y2 - y1) + x1:
            c = not c
    return c


def texas_outline():
    raw = GEO.read_bytes()
    if b"<!DOCTYPE" in raw or b"<!ENTITY" in raw:
        raise ValueError("the KML carries a DTD; refusing to parse")
    ns = "{http://www.opengis.net/kml/2.2}"
    root = ET.fromstring(raw)
    for pm in root.iter(ns + "Placemark"):
        d = {sd.get("name"): sd.text for sd in pm.iter(ns + "SimpleData")}
        if d.get("STUSPS") != "TX":
            continue
        polys = list(pm.iter(ns + "Polygon"))
        ring = max((p.find(f"{ns}outerBoundaryIs/{ns}LinearRing/{ns}coordinates").text.split() for p in polys), key=len)
        pts = [tuple(float(v) for v in c.split(",")[:2]) for c in ring]
        if pts[0] == pts[-1]:
            pts = pts[:-1]
        return pts, hashlib.sha256(raw).hexdigest(), d
    raise ValueError("Texas not in the state file")


def simplified_outline(pts, must_contain):
    """Douglas-Peucker with the smallest epsilon (a bisection) that leaves at most OUTLINE_MAX_POINTS and keeps every site inside."""
    closed = pts + [pts[0]]
    lo, hi = 0.0, 0.5
    best = None
    for _ in range(40):
        mid = (lo + hi) / 2
        s = douglas_peucker(closed, mid)[:-1]
        if len(s) <= OUTLINE_MAX_POINTS:
            best, hi = (mid, s), mid
        else:
            lo = mid
    eps, s = best
    poly = [tuple(p) for p in s]
    while not all(inside(poly, lon, lat) for lon, lat in must_contain):       # tighten until every site is inside
        eps *= 0.9
        poly = [tuple(p) for p in douglas_peucker(closed, eps)[:-1]]
    return eps, poly


# ---------------------------------------------------------------- the 40 sites
# (json key, csv column, kind, unit)  kind: s string, i int, f float(nd), b bool
SITE_COLS = [
    ("sub", "sub", "s", ""), ("kv", "kv_max", "i", "kV"), ("county", "county", "s", ""), ("zone", "zone", "s", ""),
    ("lat", "lat", "f4", "deg"), ("lon", "lon", "f4", "deg"), ("conf", "match_conf", "f2", ""),
    ("bus", "node_bus", "s", ""), ("rn", "existing_resource_node", "s", ""), ("el", "dominant_element", "s", ""),
    ("side", "dominant_side", "s", ""), ("screen", "rank", "i", ""),
    ("v2A1", "h2_mean2125_A1", "f3", "$/kW-yr"), ("v2A2", "h2_mean2125_A2", "f3", "$/kW-yr"), ("v2A3", "h2_mean2125_A3", "f3", "$/kW-yr"),
    ("v2A4", "h2_mean2125_A4", "f3", "$/kW-yr"), ("v2A5", "h2_mean2125_A5", "f3", "$/kW-yr"), ("v2orc", "h2_mean2125_oracle", "f3", "$/kW-yr"),
    ("y25", "h2_y2025_A5", "f3", "$/kW-yr"), ("m2A1", "h2_mean2125_usd_per_mwh_A1", "f1", "$/MWh"), ("m2A2", "h2_mean2125_usd_per_mwh_A2", "f1", "$/MWh"),
    ("m2A3", "h2_mean2125_usd_per_mwh_A3", "f1", "$/MWh"), ("m2A4", "h2_mean2125_usd_per_mwh_A4", "f1", "$/MWh"), ("m2A5", "h2_mean2125_usd_per_mwh_A5", "f1", "$/MWh"),
    ("v4", "h4_mean2425_A5", "f3", "$/kW-yr"), ("v4orc", "h4_mean2425_oracle", "f3", "$/kW-yr"),
    ("up", "h2_mean2125_uplift_usd_kw_yr_A5", "f3", "$/kW-yr"), ("up25", "h2_y2025_uplift_usd_kw_yr_A5", "f3", "$/kW-yr"),
    ("up4", "h4_mean2425_uplift_usd_kw_yr_A5", "f3", "$/kW-yr"),
    ("f25", "fwd_uplift_learner_2025_replay", "f3", "$/kW-yr"), ("f27", "fwd_uplift_learner_2027", "f3", "$/kW-yr"),
    ("f29", "fwd_uplift_learner_2029", "f3", "$/kW-yr"), ("f31", "fwd_uplift_learner_2031", "f3", "$/kW-yr"),
    ("l31", "fwd_learner_2031", "f3", "$/kW-yr"), ("sat", "sat_adjusted_learner_2027_31_mean", "f3", "$/kW-yr"),
    ("half", "basis_half_life", "s", ""), ("cap", "price_taker_cap_mw", "i", "MW"), ("share100", "relief_100mw_share_of_limit", "f3", "share of the limit"),
    ("upProj", "dominant_upgrade_project", "s", ""), ("upDate", "dominant_upgrade_date", "s", ""), ("upConf", "dominant_upgrade_match_conf", "f2", ""),
    ("conn", "conn_class", "s", ""), ("connKm", "conn_gen_tie_km", "f1", "km"), ("lead", "conn_lead_months", "f1", "months"),
    ("connKw", "conn_all_in_usd_per_kw", "f1", "$/kW"), ("connYr", "conn_usd_kw_yr", "f1", "$/kW-yr"),
    ("net", "net_of_connection_usd_kw_yr", "f1", "$/kW-yr"), ("be", "breakeven_installed_usd_per_kw", "i", "$/kW"),
    ("flood", "risk_flood", "s", ""), ("fire", "risk_wildfire_class", "s", ""), ("coast", "risk_coastal", "b", ""),
    ("quake", "risk_seismic", "b", ""), ("excl", "risk_exclude", "b", ""),
    ("rel", "reliability_need_score", "f1", "0-100"), ("relRank", "reliability_need_rank", "i", ""), ("relDrv", "reliability_main_driver", "s", ""),
]
CONS_COLS = [("sf", "sf", "f3"), ("hops", "hops", "i"), ("curtail", "curtail_flag", "b"), ("upg", "has_upgrade", "b")]


def site_value(row, col, kind):
    raw = row[col]
    if kind == "s":
        return raw if raw != "" else None
    if raw == "":
        return None
    if kind == "b":
        return yes(raw)
    if kind == "i":
        return int(round(float(raw)))
    return round(float(raw), int(kind[1:]))


def site_label(sc, col):
    r = sc[col]
    text = f"data/sites.csv {col}: {r['source']}" + (f"; {r['note']}" if r["note"] else "")
    return r["label"], text[:300]


def build_sites():
    rows = rd("sites")
    sc = sidecar("sites")
    vals = [float(r["h2_mean2125_A5"]) for r in rows]
    assert vals == sorted(vals, reverse=True), "sites.csv is not in queue order"
    out = []
    for i, r in enumerate(rows):
        d = {}
        for key, col, kind, _unit in SITE_COLS:
            d[key] = site_value(r, col, kind)
        d["rank"] = i + 1
        cons = []
        for k in (1, 2, 3):
            el = r[f"constraint{k}"]
            if el == "":
                continue
            c = {"el": el, "side": r[f"constraint{k}_side"] or None}
            for key, col, kind in CONS_COLS:
                src = f"constraint{k}_{col}" if col != "sf" else f"constraint{k}_sf"
                c[key] = site_value(r, src, kind)
            cons.append(c)
        d["cons"] = cons
        out.append(d)
    series = {}
    for key, col, kind, unit in SITE_COLS:
        if kind == "s":
            continue
        label, text = site_label(sc, col)
        series[f"site.{key}"] = {"label": label, "unit": unit, "text": text}
    series["site.rank"] = {"label": "DERIVED", "unit": "", "text": "DERIVED: position in the queue, the 2 h learner's 2021-25 mean, best first (data/sites.csv row order)"}
    for key, col, _ in CONS_COLS:
        label, text = site_label(sc, f"constraint1_{col}" if col != "sf" else "constraint1_sf")
        series[f"site.cons.{key}"] = {"label": label, "unit": "", "text": text}
    return out, series


# ---------------------------------------------------------------- constraint lines
def map_constraints():
    t = (GRID / "map" / "index_data.js").read_text(encoding="utf-8")
    j = json.loads(t[t.index("=") + 1:].rstrip().rstrip(";"))
    return {c["key"]: c for c in j["constraints"]}


def build_lines(sites):
    """The 12 constraints behind the 40 sites: README table 1.1 (name, limit, replayed rent, hours) matched to the sites by
    their node sets, the group caps of 1.3, and the drawn line ends from the geography audit's map."""
    groups = defaultdict(list)
    for s in sites:
        groups[s["el"]].append(s)
    tab = md_table("1.1 The 15 best transmission sites", "Limit MW [REAL]")
    caps = {r[0]: r for r in md_table("1.3 The first ~10 MW only", "Group cap MW")}
    cm = map_constraints()
    lines, matched = [], set()
    for r in tab:
        name = r[0]
        nodes = {}
        for part in r[3].split(";"):
            m = re.match(r"\s*(.+?)\s*\((import|export)\)\s*$", part)
            for n in [x.strip() for x in m.group(1).split(",")]:
                nodes[n] = m.group(2)
        key = next(k for k, g in groups.items() if {s["sub"] for s in g} == set(nodes))
        assert key not in matched
        matched.add(key)
        g = groups[key]
        for s in g:
            assert nodes[s["sub"]] == s["side"], f"{s['sub']} side differs from the README"
        rent, hours = (cell_num(x) for x in r[2].split("/"))
        learner, gain = (cell_num(x) for x in r[4].split("/"))
        assert abs(mean([s["v2A5"] for s in g]) - learner) < 0.06, f"{key}: mean learner differs from the README"
        assert abs(mean([s["up"] for s in g]) - gain) < 0.06, f"{key}: mean gain differs from the README"
        kv = re.search(r"(\d+(?:/\d+)?) kV", name).group(1)
        cap_row = caps[key]
        assert abs(cell_num(cap_row[1]) - cell_num(r[1])) <= 6, f"{key}: limit differs between 1.1 and 1.3"
        c = cm.get(key)
        pts = [[round(p[0], 4), round(p[1], 4)] for p in (c["pts"] if c else [])]
        ends = [e.get("side") for e in (c["ends"] if c else [])]
        if len(pts) == 2 and pts[0] == pts[1]:
            kind = "transformer"
        elif len(pts) == 2:
            kind = "line"
        else:
            kind = "unplaced"
        lines.append({"key": key, "name": name, "kv": kv, "kind": kind, "pts": pts, "ends": ends,
                      "limit": cell_num(r[1]), "rent": rent, "hours": hours, "cap": int(cell_num(cap_row[3])),
                      "sites": [s["sub"] for s in g]})
    assert len(lines) == 12 and sum(l["cap"] for l in lines) == int(cell_num(caps["**Total**"][3])) == 220
    return lines


# ---------------------------------------------------------------- hops versus shift factor
def build_hops():
    rows = [h for h in rd("hops") if h["hops"] != "" and h["sf"] != "" and float(h["hops"]) < 99]
    hh = np.array([float(h["hops"]) for h in rows])
    ss = np.array([abs(float(h["sf"])) for h in rows])
    rt = np.array([h["sf_method"] == "rt_reg" for h in rows])
    rho = float(np.corrcoef(rankdata(hh), rankdata(ss))[0, 1])
    rho_rt = float(np.corrcoef(rankdata(hh[rt]), rankdata(ss[rt]))[0, 1])
    strong = ss >= 0.10
    far = hh >= 8
    near = hh <= 3
    c = "DERIVED: data/hops.csv, every substation-line pair with a hop count below 99 (99 = not connected) and a shift factor; Spearman rank correlation of hops with |SF|"
    # the strong pairs, binned (hops, |SF| to 0.02) with a count
    cells = Counter((int(h), round(math.floor(s / 0.02) * 0.02 + 0.01, 2)) for h, s in zip(hh[strong], ss[strong]))
    pairs = [[h, s, n] for (h, s), n in sorted(cells.items())]
    st = {
        "pairs": lab(len(rows), "DERIVED", c),
        "rho": lab(round(rho, 2), "DERIVED", c),
        "rhoRt": lab(round(rho_rt, 2), "DERIVED", c + ", real-time fits only"),
        "strong": lab(int(strong.sum()), "DERIVED", "DERIVED: pairs with |SF| of 0.10 or more (data/hops.csv)"),
        "strongFar": lab(int((strong & far).sum()), "DERIVED", "DERIVED: of those, pairs 8 or more hops from the line"),
        "strongFarShare": lab(round(float((strong & far).sum() / strong.sum()) * 100), "DERIVED", "DERIVED: share of the strong pairs that are 8 or more hops away"),
        "nearWeakShare": lab(round(float((ss[near] < 0.05).mean()) * 100), "DERIVED", "DERIVED: among pairs within 3 hops, share with |SF| below 0.05 (nearby but electrically weak)"),
    }
    assert st["pairs"]["v"] == 104969 and st["rho"]["v"] == -0.42 and st["strong"]["v"] == 3833 and st["strongFar"]["v"] == 1760, "hops statistics differ from the README"
    return st, pairs


# ---------------------------------------------------------------- the rerank (U01..U10 and the planned bundles)
def build_rerank():
    R = rd("upgrade_rerank")
    sc = sidecar("upgrade_rerank")
    ups = {u["upgrade_id"]: u for u in rd("upgrades") if re.fullmatch(r"U\d\d", u["upgrade_id"])}
    order = []
    for r in R:
        if r["scenario"] not in order:
            order.append(r["scenario"])
    scen = []
    for sid in order:
        rows = [r for r in R if r["scenario"] == sid]
        r0 = rows[0]
        u = ups.get(sid)
        item = {
            "id": sid, "name": UPGRADE_NAMES[sid], "kind": r0["upgrade_type"], "effect": r0["scenario_effect"],
            "planned": r0["planned"], "plannedIsd": r0["planned_isd"] or None,
            "cost": r2(fl(r0["cost_mid_musd"]), 2), "lead": r2(fl(r0["lead_time_mid_months"]), 1),
            "ratio": r2(fl(r0["nwa_vs_wires_ratio"]), 1), "residual": r2(fl(r0["residual"]), 2),
            "losers": int(r0["scn_n_losers"]), "gainers": int(r0["scn_n_gainers"]), "largest": r0["scn_largest_loss_site"],
            "sites": [{"sub": r["sub"], "before": r2(fl(r["value_before_usd_kw_yr"]), 3), "after": r2(fl(r["value_after_usd_kw_yr"]), 3),
                       "rb": int(r["rank_before"]), "ra": int(r["rank_after"]), "on": yes(r["on_upgraded_element"])} for r in rows],
        }
        if u:
            item.update({"limit": r2(fl(u["limit_mw"]), 0), "miles": r2(fl(u["length_mi"]), 1), "costLow": r2(fl(u["cost_low_musd"]), 2),
                         "costHigh": r2(fl(u["cost_high_musd"]), 2), "rent25": r2(fl(u["imm_rent_2025_musd"]), 1),
                         "nwaMw": r2(fl(u["nwa_central_mw"]), 0), "nwaMwh": r2(fl(u["nwa_central_mwh"]), 0), "nwaHours": r2(fl(u["nwa_central_hours"]), 1),
                         "vsCheapest": r2(fl(u["nwa_vs_cheapest_ratio"]), 0), "project": u["planned_id"] or None})
        scen.append(item)
        # the scenario's sum, from the rows (checked against the file's own total)
        b = sum(s["before"] for s in item["sites"])
        a = sum(s["after"] for s in item["sites"])
        assert abs(b - float(r0["scn_sum40_before_usd_kw_yr"])) < 0.03 and abs(a - float(r0["scn_sum40_after_usd_kw_yr"])) < 0.03, f"{sid}: sums differ"
    series = {}
    for key, col in (("value", "value_before_usd_kw_yr"), ("cost", "cost_mid_musd"), ("lead", "lead_time_mid_months"), ("ratio", "nwa_vs_wires_ratio"),
                     ("rank", "rank_before"), ("residual", "residual")):
        r = sc[col]
        series[f"rerank.{key}"] = {"label": r["label"], "unit": {"value": "$/kW-yr", "cost": "$M", "lead": "months", "ratio": "x", "rank": "", "residual": ""}[key],
                                   "text": f"data/upgrade_rerank.csv {col}: {r['source']}; {r['note']}"[:300]}
    us = sidecar("upgrades")
    for key, col, unit in (("limit", "limit_mw", "MW"), ("miles", "length_mi", "miles"), ("costLow", "cost_low_musd", "$M"), ("costHigh", "cost_high_musd", "$M"),
                           ("rent25", "imm_rent_2025_musd", "$M"), ("nwaMw", "nwa_central_mw", "MW"), ("nwaMwh", "nwa_central_mwh", "MWh"),
                           ("nwaHours", "nwa_central_hours", "hours"), ("vsCheapest", "nwa_vs_cheapest_ratio", "x"), ("isd", "planned_isd", "date")):
        r = us[col]
        series[f"upgrade.{key}"] = {"label": r["label"], "unit": unit, "text": f"data/upgrades.csv {col}: {r['source']}; {r['note']}"[:300]}
    return scen, series


def build_upgrade_text():
    """Median battery-vs-wires ratios over the 24 elements the study could size (data/upgrades.csv)."""
    U = rd("upgrades")
    a = [float(x["nwa_vs_wires_ratio"]) for x in U if x["nwa_vs_wires_ratio"] != ""]
    b = [float(x["nwa_vs_cheapest_ratio"]) for x in U if x["nwa_vs_cheapest_ratio"] != ""]
    c = "DERIVED: data/upgrades.csv, the elements whose battery relief could be sized; battery that relieves 10% of the limit in 80% of the element's binding hours, cost over wires cost"
    assert len(a) == 24 and round(float(np.median(a))) == 21 and round(float(np.median(b))) == 192
    return {"n": lab(len(a), "DERIVED", c), "medianWires": lab(round(float(np.median(a)), 1), "DERIVED", c), "medianCheapest": lab(round(float(np.median(b))), "DERIVED", c + ", against the cheapest wires option whose rating uplift covers the relief"),
            "minWires": lab(round(min(a), 1), "DERIVED", c), "maxWires": lab(round(max(a), 1), "DERIVED", c)}


# ---------------------------------------------------------------- the fade, algorithms, portfolio
def build_fade(sites):
    rows = md_table("4.4 Forward saturation", "Learner at the 40 nodes")
    mean_up, storage, mean_val = {}, {}, {}
    for r in rows:
        y = 2025 if "2025" in r[0] else int(re.search(r"\d{4}", r[0]).group(0))
        mean_val[y] = cell_num(r[1])
        mean_up[y] = cell_num(r[3])
        storage[y] = cell_num(r[5]) if r[5] else None
    assert sorted(mean_up) == YEARS
    for y, k in ((2025, "f25"), (2027, "f27"), (2029, "f29"), (2031, "f31")):                 # the README rows agree with the CSV means
        assert abs(mean([s[k] for s in sites]) - mean_up[y]) < 0.06, f"fade {y} differs from sites.csv"
    c = "SIM: mean over the 40 candidates of the 2 h learner's gain over the zone, the oracle-ratio method on the 2025 replay prices with planned upgrades and storage saturation applied (README 4.4, checked against data/sites.csv)"
    return {"years": YEARS, "meanUp": [r2(mean_up[y], 1) for y in YEARS], "meanVal": [r2(mean_val[y], 1) for y in YEARS],
            "storageGw": [storage[y] for y in YEARS],
            "halfLife": {k: lab(v, "SIM", "SIM: basis half-life, the first year from 2026 in which a site's gain falls below half of its 2025 value (data/sites.csv basis_half_life)")
                         for k, v in Counter(s["half"] for s in sites).items()}}, {
        "fade.meanUp": {"label": "SIM", "unit": "$/kW-yr", "text": c},
        "fade.meanVal": {"label": "SIM", "unit": "$/kW-yr", "text": c.replace("gain over the zone", "value")},
        "fade.storageGw": {"label": "ASSUMPTION", "unit": "GW", "text": "ASSUMPTION: effective storage online behind the constraints at 60% realisation of unbuilt MW (41.0 GW listed); README 4.4, research-data forward_meta.json"}}


def build_algorithms(sites):
    Y = [y for y in rd("site_years") if y["role"] == "control_zone" and y["spec"] == "grid" and y["period"] == "mean_2021_25"]
    assert len(Y) == 8
    ys25 = [y for y in rd("site_years") if y["role"] == "control_zone" and y["spec"] == "grid" and y["period"] == "2025"]
    c_n = "SIM: arena dispatch on the 40 rebuilt node lanes, 2 h, mean of 2021-25, mean of the 40 sites (data/sites.csv)"
    c_z = "SIM: the same dispatch on the 8 load-zone prices, 2 h, mean of 2021-25 (data/site_years.csv, control lanes)"
    out = {"algos": []}
    for a, name in ALGOS + [("orc", "Perfect foresight")]:
        k_site = f"v2{a}"
        k_zone = "oracle" if a == "orc" else a
        nodes = mean([s[k_site] for s in sites])
        zones = float(np.mean([float(y[f"usd_kw_yr_{k_zone}"]) for y in Y]))
        item = {"id": a, "name": name, "nodes": lab(nodes, "SIM", c_n), "zones": lab(zones, "SIM", c_z)}
        if a != "orc":
            item["permwhNodes"] = lab(mean([s[f"m2{a}"] for s in sites]), "SIM", c_n.replace("2 h,", "2 h, per MWh discharged,"))
            wins_n = sum(1 for s in sites if max((s[f"v2A{i}"], i) for i in range(1, 6))[1] == int(a[1]))
            zbest = Counter()
            for lane in {y["lane"] for y in Y}:
                v = {f"A{i}": float(next(y for y in Y if y["lane"] == lane)[f"usd_kw_yr_A{i}"]) for i in range(1, 6)}
                zbest[max(v, key=v.get)] += 1
            item["bestNodes"] = lab(wins_n, "SIM", "SIM: sites (of 40) where this algorithm earns the most, 2 h, 2021-25 mean")
            item["bestZones"] = lab(zbest.get(a, 0), "SIM", "SIM: zones (of 8) where this algorithm earns the most, 2 h, 2021-25 mean")
        out["algos"].append(item)
    out["nodes25"] = lab(mean([s["y25"] for s in sites]), "SIM", "SIM: 2 h learner at the 40 sites in 2025 (data/sites.csv h2_y2025_A5)")
    out["zones25"] = lab(float(np.mean([float(y["usd_kw_yr_A5"]) for y in ys25])), "SIM", "SIM: 2 h learner at the 8 zones in 2025 (data/site_years.csv)")
    return out


def build_portfolio():
    rules = []
    for r in md_table("1.3 The first ~10 MW only", "MW at nodes / at zones"):
        name = re.sub(r"\*", "", r[0]).strip()
        mw_n, mw_z = (int(cell_num(x)) for x in r[2].split("/"))
        c = "SIM: README 1.3, arena siting rules on a 1,000 MW fleet added 200 MW a year 2027-31, group caps of 10% of each limit (ASSUMPTION), $/kW-yr of the whole fleet"
        rules.append({"id": name.split()[0], "name": name.split(" ", 1)[1], "capped": lab(cell_num(r[1]), "SIM", c), "mwNodes": lab(mw_n, "SIM", c),
                      "mwZones": lab(mw_z, "SIM", c), "uncapped": lab(cell_num(r[3]), "SIM", c.replace("group caps of 10% of each limit (ASSUMPTION)", "no caps at all")),
                      "zonesOnly": lab(cell_num(r[4]), "SIM", c.replace("group caps of 10% of each limit (ASSUMPTION)", "every unit at its load-zone price"))})
    assert [r["id"] for r in rules] == ["A1", "A2", "A3", "A4", "A5"] and rules[4]["capped"]["v"] == 35.4 and rules[4]["zonesOnly"]["v"] == 27.8
    return rules


# ---------------------------------------------------------------- regions, risk, reliability, tier 2
REGIONS = ["Rio Grande Valley", "Panhandle", "Far West", "Laredo/South", "Central"]


def build_regions():
    B = rd("regional_basis_summary")
    sc = sidecar("regional_basis_summary")
    out = []
    for name in REGIONS:
        z = next(b for b in B if b["region"] == name and b["reference"] == "zone")
        s = next(b for b in B if b["region"] == name and b["reference"] == "system")
        out.append({"name": name, "n": int(z["n_nodes"]), "mean": r2(fl(z["basis_mean"]), 2), "neg10": r2(fl(z["share_hours_neg10"]) * 100, 1),
                    "pos10": r2(fl(z["share_hours_pos10"]) * 100, 1), "sysNeg10": r2(fl(s["share_hours_neg10"]) * 100, 1),
                    "sysPos10": r2(fl(s["share_hours_pos10"]) * 100, 1), "worstHour": int(z["worst_hour_cpt"]), "worstMean": r2(fl(z["worst_hour_mean"]), 1),
                    "uplift": r2(fl(z["dam_uplift_mean"]), 2), "upliftP90": r2(fl(z["dam_uplift_p90"]), 2), "verdict": z["verdict"]})
    ser = {}
    for key, col, unit in (("n", "n_nodes", "nodes"), ("mean", "basis_mean", "$/MWh"), ("neg10", "share_hours_neg10", "%"), ("pos10", "share_hours_pos10", "%"),
                           ("sysNeg10", "share_hours_neg10", "%"), ("sysPos10", "share_hours_pos10", "%"), ("worstMean", "worst_hour_mean", "$/MWh"),
                           ("uplift", "dam_uplift_mean", "$ per kW over 42 days"), ("upliftP90", "dam_uplift_p90", "$ per kW over 42 days")):
        r = sc[col]
        ser[f"region.{key}"] = {"label": r["label"], "unit": unit, "text": (f"data/regional_basis_summary.csv {col}: {r['source']}; {r['note']}. "
                                                                           "REAL day-ahead prices, ERCOT 60-day disclosure, 1 Jul - 11 Aug 2026")[:300]}
    return out, ser


def build_picks():
    E = [e for e in rd("extra_sites") if e["role"] == "regional"]
    sc = sidecar("extra_sites")
    out = []
    for e in E:
        out.append({"sub": e["sub"], "region": e["region"], "side": e["region_side"], "county": e["county"], "lat": r2(fl(e["lat"]), 4), "lon": r2(fl(e["lon"]), 4),
                    "gain": r2(fl(e["h2_mean2125_uplift_usd_kw_yr_A5"]), 3), "gain25": r2(fl(e["h2_y2025_uplift_usd_kw_yr_A5"]), 3),
                    "obs": r2(fl(e["obs_basis_vs_zone_mean_usd_mwh"]), 2), "dam": r2(fl(e["obs_dam_uplift_42d_usd_kw"]), 2),
                    "el": e["dominant_element"], "km": r2(fl(e["dominant_distance_km"]), 0), "cls": e["dominant_distance_class"] or None, "src": e["basis_source"]})
    ser = {}
    for key, col, unit in (("gain", "h2_mean2125_uplift_usd_kw_yr_A5", "$/kW-yr"), ("gain25", "h2_y2025_uplift_usd_kw_yr_A5", "$/kW-yr"),
                           ("obs", "obs_basis_vs_zone_mean_usd_mwh", "$/MWh"), ("dam", "obs_dam_uplift_42d_usd_kw", "$ per kW over 42 days"),
                           ("km", "dominant_distance_km", "km")):
        r = sc[col]
        ser[f"pick.{key}"] = {"label": r["label"], "unit": unit, "text": f"data/extra_sites.csv {col}: {r['source']}; {r['note']}"[:300]}
    return out, ser


def build_risk(sites):
    F = rd("risk_flags")
    assessed = [f for f in F if f["risk_basis"] == "site"]
    ex = Counter(f["exclude_reason"] for f in assessed if f["exclude_flag"] == "True")
    assert len(assessed) == 2884 and sum(ex.values()) == 148
    c = "DERIVED: data/risk_flags.csv, substations assessed at the site (FEMA flood map, USFS wildfire hazard, USGS quakes)"
    return {"assessed": lab(len(assessed), "DERIVED", c), "excluded": lab(sum(ex.values()), "DERIVED", c),
            "flood": lab(ex["flood_sfha"], "DERIVED", c + "; in a FEMA special flood hazard area"),
            "floodway": lab(ex["floodway"], "DERIVED", c + "; in a regulatory floodway"),
            "wildfire": lab(ex["wildfire_very_high"], "DERIVED", c + "; at very high wildfire hazard"),
            "unmapped": lab(sum(1 for f in assessed if f["flood_flag"] == "unmapped"), "DERIVED", c + "; no digital flood polygon (unmapped is not low risk)")}


def build_reliability(sites):
    R = [r for r in rd("reliability") if r["need_rank"] != ""]
    top = sorted(R, key=lambda r: float(r["need_rank"]))[:10]
    c = "DERIVED: data/reliability.csv, need rank among the substations the county outage data can rank (1 = most need); reliability value, never added to market value"
    return {"nRanked": lab(len(R), "DERIVED", c),
            "top": [{"sub": r["sub"], "county": r["county"], "rank": int(float(r["need_rank"])), "score": r2(fl(r["reliability_need_score"]), 1), "driver": r["main_driver"]} for r in top],
            "inTop100": lab(sum(1 for s in sites if s["relRank"] is not None and s["relRank"] <= 100), "DERIVED", c + "; of the 40 sites")}, {
        "rel.score": {"label": "ASSUMPTION", "unit": "0-100", "text": "ASSUMPTION: reliability need score, weights are a judgement (data/reliability.csv); its own units, never added to market value"},
        "rel.rank": {"label": "DERIVED", "unit": "", "text": c}}


def build_tier2():
    T = [t for t in rd("tier2_valued") if t["nodal_value_source"].startswith("value_sites_2025_replay")]
    assert len(T) == 167
    groups = [("all", "All valued candidates", T),
              ("within", "Within 150 km of the line", [t for t in T if t["nodal_distance_class"] == "within_150km"]),
              ("beyond", "Beyond 150 km", [t for t in T if t["nodal_distance_class"] == "beyond_150km"]),
              ("unplaced", "Line not placed", [t for t in T if t["nodal_distance_class"] == "element_not_placed"])]
    c = "SIM: data/tier2_valued.csv, 2 h learner, 2025 replay, mean over the candidates valued with the dispatch model (150 settlement buses); "
    out = []
    for gid, name, rows in groups:
        g = lambda col: [float(t[col]) for t in rows]  # noqa: E731
        out.append({"id": gid, "name": name, "n": lab(len(rows), "DERIVED", c + "count"),
                    "desr": lab(float(np.mean(g("nodal_value_usd_kw_yr"))), "SIM", c + "DESR route: paid the node price"),
                    "ader": lab(float(np.mean(g("ader_zone_value_usd_kw_yr"))), "SIM", c + "ADER route: a home-battery fleet is paid the zone price"),
                    "gain": lab(float(np.mean(g("nodal_vs_zone_uplift_usd_kw_yr"))), "SIM", c + "gain of the node route over the zone"),
                    "gainMedian": lab(float(np.median(g("nodal_vs_zone_uplift_usd_kw_yr"))), "SIM", c + "median gain of the node route over the zone"),
                    "gain29": lab(float(np.mean(g("nodal_vs_zone_uplift_2029_usd_kw_yr"))), "SIM", c + "gain in 2029 after planned upgrades and storage saturation")})
    allg = [float(t["nodal_vs_zone_uplift_usd_kw_yr"]) for t in T]
    extra = {"gaining": lab(sum(1 for x in allg if x > 0), "DERIVED", "DERIVED: of the 167 valued candidates, how many gain over the zone (data/tier2_valued.csv)"),
             "gainingTen": lab(round(sum(1 for x in allg if x > 10) / len(allg) * 100), "DERIVED", "DERIVED: share of the 167 valued candidates that gain more than $10/kW-yr over the zone")}
    assert abs(out[0]["gain"]["v"] - 26.4) < 0.06 and out[1]["n"]["v"] == 84
    return out, extra


# ---------------------------------------------------------------- main
PLACES = [("Houston", 29.76, -95.37), ("Dallas", 32.78, -96.80), ("Austin", 30.27, -97.74), ("San Antonio", 29.42, -98.49),
          ("Lubbock", 33.58, -101.86), ("Midland", 32.00, -102.08), ("Corpus Christi", 27.80, -97.40), ("Amarillo", 35.22, -101.83)]



_CITE_LABEL = re.compile(r"^(REAL|SIM|DERIVED|ASSUMPTION)(?=[:\s/])")


def align_cites(o):
    """Make every cite that opens with a label word open with the value's own label (lead, 14 Oct 2026): a shared cite
    string must not tell the reader DERIVED next to a SIM tag. The rest of the cite is kept."""
    if isinstance(o, dict):
        lab, key = o.get("label"), ("cite" if isinstance(o.get("cite"), str) else "text" if isinstance(o.get("text"), str) else None)
        if lab in ("REAL", "SIM", "DERIVED", "ASSUMPTION") and key:
            o[key] = _CITE_LABEL.sub(lab, o[key], count=1)
        for v in o.values():
            align_cites(v)
    elif isinstance(o, list):
        for v in o:
            align_cites(v)
    return o


def main():
    sites, site_series = build_sites()
    picks, pick_series = build_picks()
    must = [(s["lon"], s["lat"]) for s in sites] + [(p["lon"], p["lat"]) for p in picks if p["lat"] is not None] + [(lon, lat) for _n, lat, lon in PLACES]
    ring, sha, props = texas_outline()
    eps, poly = simplified_outline(ring, must)
    hops_stats, hop_pairs = build_hops()
    lines = build_lines(sites)
    rerank, rerank_series = build_rerank()
    fade, fade_series = build_fade(sites)
    algos = build_algorithms(sites)
    portfolio = build_portfolio()
    regions, region_series = build_regions()
    reliab, rel_series = build_reliability(sites)
    tier2, tier2_extra = build_tier2()
    risk = build_risk(sites)
    ups = build_upgrade_text()

    n = len(sites)
    c40 = "SIM: arena dispatch on the 40 rebuilt node lanes, 2 h learner (A5), mean of 2021-25 (data/sites.csv); node prices are SIM"
    cz = "SIM: the same battery at the eight load-zone prices (data/site_years.csv control lanes)"
    imp = [s for s in sites if s["side"] == "import"]
    exp_ = [s for s in sites if s["side"] == "export"]
    beat = sum(1 for s in sites if s["up"] > 0)
    headline = {
        "sites": lab(n, "DERIVED", "DERIVED: candidate substations in data/sites.csv"),
        "zones": lab(len({y["lane"] for y in rd("site_years") if y["role"] == "control_zone" and y["spec"] == "grid"}), "DERIVED",
                     "DERIVED: load zones in the control run (data/site_years.csv)"),
        "constraints": lab(len(lines), "DERIVED", "DERIVED: distinct dominant constraints behind the 40 sites (data/sites.csv dominant_element)"),
        "nodesMean": lab(mean([s["v2A5"] for s in sites]), "SIM", c40),
        "zonesMean": lab(next(a for a in algos["algos"] if a["id"] == "A5")["zones"]["v"], "SIM", cz),
        "nodes25": algos["nodes25"], "zones25": algos["zones25"],
        "beatZone": lab(beat, "SIM", "SIM: sites whose 2 h learner earns more than the same battery at its load-zone price (2021-25 mean)"),
        "gainMean": lab(mean([s["up"] for s in sites]), "SIM", "SIM: mean gain over the zone, 2 h learner, 2021-25 (data/sites.csv)"),
        "gainImport": lab(mean([s["up"] for s in imp]), "SIM", "SIM: mean gain over the zone at the import-side sites"),
        "gainExport": lab(mean([s["up"] for s in exp_]), "SIM", "SIM: mean gain over the zone at the export-side sites"),
        "nImport": lab(len(imp), "DERIVED", "DERIVED: sites on the import side of their dominant constraint"),
        "nExport": lab(len(exp_), "DERIVED", "DERIVED: sites on the export side of their dominant constraint"),
        "roomMw": lab(sum(l["cap"] for l in lines), "ASSUMPTION", "ASSUMPTION: the nodes behind each constraint share a cap of 10% of its limit in whole 10 MW units; README 1.3"),
        "fleetMw": lab(1000, "ASSUMPTION", "ASSUMPTION: a 1,000 MW fleet added 200 MW a year over 2027-31; README 1.3"),
        "gainHalf2028": lab(sum(1 for s in sites if s["half"] == "2028"), "SIM", "SIM: sites whose gain falls below half of its 2025 value by 2028"),
    }
    assert abs(headline["nodesMean"]["v"] - 79.0) < 0.06 and abs(headline["zonesMean"]["v"] - 44.4) < 0.06 and headline["beatZone"]["v"] == 38

    series = {
        "outline": {"label": "REAL", "unit": "deg", "text": "REAL: US Census Bureau cartographic boundary file cb_2023_us_state_20m (KML), the Texas ring simplified by Douglas-Peucker; public domain"},
        "places": {"label": "REAL", "unit": "deg", "text": "REAL: approximate city centres (public geography), used only to orient the map"},
        "line.pts": {"label": "DERIVED", "unit": "deg", "text": "DERIVED: line ends placed by the geography audit (docs/research/grid/map/index_data.js); an end that could not be placed is not drawn"},
        "line.limit": {"label": "REAL", "unit": "MW", "text": "REAL: median SCED / day-ahead limit of the element (ERCOT NP6-86-CD / NP4-191-CD), README 1.1"},
        "line.rent": {"label": "SIM", "unit": "$M", "text": "SIM: replayed 2025 congestion rent of the element, README 1.1 (anchored on the market monitor's rents)"},
        "line.hours": {"label": "SIM", "unit": "hours", "text": "SIM: hours the element binds in the 2025 replay, README 1.1"},
        "line.cap": {"label": "ASSUMPTION", "unit": "MW", "text": "ASSUMPTION: price-taker room of the nodes behind the element, 10% of its limit in whole 10 MW units; README 1.3"},
        "hops.pairs": {"label": "DERIVED", "unit": "pairs", "text": "DERIVED: substation-line pairs with |SF| of 0.10 or more, binned by hops and |SF| (data/hops.csv); the dot area is the count"},
    }
    series.update(site_series)
    series.update(rerank_series)
    series.update(fade_series)
    series.update(region_series)
    series.update(pick_series)
    series.update(rel_series)

    constants = {
        "BATTERY": {"value": "1 MW / 2 MWh (2 h) and 1 MW / 4 MWh (4 h)", "label": "ASSUMPTION", "cite": "the grid batteries valued in docs/research/grid/README.md; energy only, $20/MWh wear, 85% round trip"},
        "WEAR_USD_MWH": {"value": 20, "label": "ASSUMPTION", "cite": "wear cost per MWh discharged; docs/research/grid/README.md", "unit": "$/MWh"},
        "LEVEL_FACTOR": {"value": 2, "label": "SIM", "cite": "node-price levels are good to about a factor of two, rankings are steadier (rank correlation 0.90 to 1.00 across six alternative SIM choices); README section 9", "unit": "x"},
        "PRICE_TAKER_SHARE": {"value": 10, "label": "ASSUMPTION", "cite": "group cap: the nodes behind a constraint share 10% of its limit, in whole 10 MW units; README 1.3", "unit": "%"},
        "FLEET_ADD_MW_YR": {"value": 200, "label": "ASSUMPTION", "cite": "the 1,000 MW fleet adds this many MW a year over 2027-31; README 1.3", "unit": "MW"},
        "UNIT_MW": {"value": 10, "label": "ASSUMPTION", "cite": "one unit of siting is 10 MW (the first ~10 MW of a pocket); README 1.3", "unit": "MW"},
        "FAR_KM": {"value": 150, "label": "ASSUMPTION", "cite": "beyond this distance a shift factor of 0.10 or more is physically unlikely; gridsite.tier2_value.FAR_KM", "unit": "km"},
        "SAT_REALISATION": {"value": 60, "label": "ASSUMPTION", "cite": "share of unbuilt storage MW that is realised; README 4.4", "unit": "%"},
        "STORAGE_LISTED_GW": {"value": 41.0, "label": "REAL", "cite": "storage listed behind the constraints: 3.9 GW operating, 34.4 GW of queue with agreements, 2.7 GW firm planned; README 4.4", "unit": "GW"},
        "UPGRADE_RESIDUAL": {"value": 10, "label": "ASSUMPTION", "cite": "share of the shadow prices left after a line or transformer upgrade; gridsite.upgrades", "unit": "%"},
        "UPGRADE_RESIDUAL_GTC": {"value": 50, "label": "ASSUMPTION", "cite": "share of the shadow prices left after an interface upgrade; gridsite.upgrades", "unit": "%"},
        "ALLOWANCE_138_MUSD": {"value": 14, "label": "REAL", "cite": "interconnection facilities allowance at 138 kV and below; 16 TAC 25.195(f)(3)", "unit": "$M"},
        "ALLOWANCE_ABOVE_MUSD": {"value": 20, "label": "REAL", "cite": "interconnection facilities allowance above 138 kV; 16 TAC 25.195(f)(3)", "unit": "$M"},
        "TIER1_MUSD": {"value": 200, "label": "REAL", "cite": "Tier 1 projects of this cost or more need ERCOT Board endorsement; Nodal Protocols 3.11.4", "unit": "$M"},
        "TIER2_DAYS": {"value": 120, "label": "REAL", "cite": "review period of a Tier 2 project with a CCN; Nodal Protocols 3.11.4", "unit": "days"},
        "SB415_CAP_MW": {"value": 100, "label": "REAL", "cite": "a utility may contract storage for distribution reliability up to this size and may not own it; PURA 35.153 (SB 415)", "unit": "MW"},
        "ADER_UNIT_MW": {"value": 1, "label": "REAL", "cite": "an ADER aggregates resources of this size or less each; ADER Governing Document 3.3 (README 6)", "unit": "MW"},
        "ADER_CAP_MW": {"value": 500, "label": "REAL", "cite": "ADER pilot cap; ADER Governing Document 3.3 (README 6)", "unit": "MW"},
        "DESR_MAX_MW": {"value": 10, "label": "REAL", "cite": "storage of this size or less settles at the load zone while it is over 50% of the site; NPRR995 (README 6)", "unit": "MW"},
        "LEAD_69_MONTHS": {"value": 38.5, "label": "DERIVED", "cite": "a 69 kV site needs a medium-size step-up transformer; README 1.1", "unit": "months"},
        "LEAD_138_MONTHS": {"value": 56.5, "label": "DERIVED", "cite": "a 138 kV site needs a large power transformer; README 1.1", "unit": "months"},
        "STRONG_SF": {"value": 0.10, "label": "ASSUMPTION", "cite": "a pair counts as electrically strong at this |shift factor| or more; README 2.3", "unit": ""},
        "WEAK_SF": {"value": 0.05, "label": "ASSUMPTION", "cite": "below this |shift factor| a nearby pair is electrically weak; README 2.3", "unit": ""},
        "FAR_HOPS": {"value": 8, "label": "ASSUMPTION", "cite": "a pair this many hops from the line or more is far; README 2.3", "unit": ""},
        "NEAR_HOPS": {"value": 3, "label": "ASSUMPTION", "cite": "a pair this many hops from the line or fewer is near; README 2.3", "unit": ""},
        "MOVED_USD": {"value": 1, "label": "ASSUMPTION", "cite": "the study counts a site as moved by an upgrade when its value changes by more than this; README 3.2", "unit": "$/kW-yr"},
        "PICK_USD": {"value": 1, "label": "ASSUMPTION", "cite": "a regional pick counts as positive above this modelled gain over its zone; README 7.4", "unit": "$/kW-yr"},
        "RELIEF_SHARE": {"value": 10, "label": "ASSUMPTION", "cite": "battery sized to relieve this share of an element's limit; README 3.1", "unit": "%"},
        "RELIEF_HOURS": {"value": 80, "label": "ASSUMPTION", "cite": "... in this share of the element's binding hours; README 3.1", "unit": "%"},
        "SAT_SUBS": {"value": 15, "label": "SIM", "cite": "storage-hosting substations behind Twin Buttes-Hargrove whose shift factor of about -0.065 drives the 2028 half-life; README 4.4", "unit": ""},
        "SAT_SF": {"value": -0.065, "label": "SIM", "cite": "shift factor of those substations (t about 7); README 4.4", "unit": ""},
        "BASIS_USD": {"value": 10, "label": "ASSUMPTION", "cite": "a node-hour counts as a premium or a discount beyond this many dollars a MWh from its zone; data/regional_basis_summary.csv", "unit": "$/MWh"},
        "DAM_DAYS": {"value": 42, "label": "REAL", "cite": "delivery days in ERCOT's 60-day day-ahead disclosure used here, 1 Jul to 11 Aug 2026; README 7", "unit": "days"},
        "DAM_POINTS": {"value": 1003, "label": "REAL", "cite": "settlement points with measured day-ahead prices in that window; README 7", "unit": ""},
        "RELIABILITY_TOP": {"value": 100, "label": "ASSUMPTION", "cite": "the reliability screen looks at this many substations with the most need; README 5", "unit": ""},
        "PEAK_VOLL": {"value": 35685, "label": "REAL", "cite": "ERCOT-wide one-hour value of lost load, Brattle 2024; README 5", "unit": "$/MWh"},
        "FIRST_STUDY_MONTHS": {"value": 20.5, "label": "REAL", "cite": "median months from study request to interconnection agreement; README 2.1 item 26", "unit": "months"},
    }
    sources = {
        "study": {"label": "SIM", "text": REPORT + ": ERCOT physical-grid siting study, revision 2 (11 Oct 2026); node prices rebuilt from the real zone price and a replayed congestion structure"},
        "ercot": {"label": "REAL", "text": "ERCOT public reports: NP4-160 substations, NP6-86 / NP4-191 shadow prices, the 60-day day-ahead disclosure (1 Jul - 11 Aug 2026); no ERCOT API key was used"},
        "imm": {"label": "REAL", "text": "Potomac Economics, ERCOT independent market monitor reports 2025 and 2026 (congestion rents)"},
        "census": {"label": "REAL", "text": f"US Census Bureau, cartographic boundary file cb_2023_us_state_20m (KML), public domain; sha256 {sha}"},
    }
    doc = envelope("siting", "scripts.export_siting",
                   inputs={"prices_sha256": None, "loads_sha256": None, "topology_sha256": None},
                   constants=constants, sources=sources, series=series)
    doc["meta"] = {
        "report": REPORT, "revision": "2", "built": "11 Oct 2026",
        "battery": "1 MW grid battery, 2 h (and 4 h for the top 15), energy only, $20/MWh wear, dispatched by the arena's Adaptive learner (A5) unless stated",
        "units": "$ per kW of battery per year, gross energy value, not a profit; ancillary revenue, capacity payments and O&M are not in these numbers",
        "caveat": "Node prices are a model: ERCOT publishes one week of real-time nodal prices without an API key, so the price at a substation over 2019 to 2026 is rebuilt from the real zone price plus a replayed congestion structure.",
        "algorithms": {a: n_ for a, n_ in ALGOS},
        "sideWords": {"import": "Import side: the price rises when the line binds, so the battery sells into the spike.",
                      "export": "Export side: the price falls when the line binds, so the battery charges cheaply, but its injection loads the line."},
        "outline": {"epsilonDeg": round(eps, 4), "points": len(poly), "source": "US Census cartographic boundary 2023, 20m"},
    }
    doc["headline"] = headline
    doc["sites"] = sites
    doc["lines"] = lines
    doc["places"] = [{"name": n_, "lat": lat, "lon": lon} for n_, lat, lon in PLACES]
    doc["hopsSf"] = {"stats": hops_stats, "pairs": hop_pairs}
    doc["algorithms"] = algos
    doc["portfolio"] = {"rules": portfolio, "caps": [{"key": l["key"], "name": l["name"], "limit": l["limit"], "cap": l["cap"], "sites": l["sites"]} for l in lines]}
    doc["rerank"] = {"scenarios": rerank, "wires": ups}
    doc["fade"] = fade
    doc["regions"] = regions
    doc["picks"] = picks
    doc["risk"] = risk
    doc["reliability"] = reliab
    doc["tier2"] = {"groups": tier2, **tier2_extra}
    doc["who"] = [
        {"lead": "The transmission owner decides.", "text": "The TSP (the utility that owns the wires) decides, builds, owns and recovers the cost from load through its transmission cost of service. A wholesale battery does not pay it ([[16 TAC 25.192]]).", "label": "REAL"},
        {"lead": "A new line needs a permit; a reconductor does not.", "text": "A new line needs a PUCT certificate (a CCN). Rebuilding on the existing right of way or reconductoring does not ([[16 TAC 25.101]]). A ratings change is a neutral [[Tier 4]] project that ERCOT does not review.", "label": "REAL"},
        {"lead": "Bigger projects get reviewed.", "text": "[[Tier 1]] projects of {TIER1_MUSD} or more need the ERCOT Board; a [[Tier 2]] project under that with a CCN gets a review of {TIER2_DAYS}. Everything else is [[Tier 3 or 4]].", "label": "REAL"},
        {"lead": "A battery owner can ask, and can pay for its own connection.", "text": "It can propose a project to the Regional Planning Group ([[NP 3.11.4.1]]), offer to fund one the TSP chooses to pursue, and pay interconnection facilities above {ALLOWANCE_138_MUSD} (at [[138 kV]] and below) or {ALLOWANCE_ABOVE_MUSD} (above). It cannot order, own, schedule or fund a network upgrade on its own.", "label": "REAL"},
        {"lead": "Storage is not a transmission alternative in ERCOT today.", "text": "Planning treats it as a dispatchable resource. [[SB 415]] lets a utility contract for storage, but only for distribution reliability, up to {SB415_CAP_MW}, and the utility may not own it.", "label": "REAL"},
    ]
    doc["tier2Rules"] = [
        {"route": "DESR", "what": "Distribution-connected storage registered as a resource", "settles": "A resource node fixed at one bus, the highest-voltage bus of the substation", "nodal": True},
        {"route": "ADER", "what": "An aggregation of home batteries ({ADER_UNIT_MW} or less each)", "settles": "The load-zone price", "nodal": False},
        {"route": "Settlement-only", "what": "Storage of {DESR_MAX_MW} or less", "settles": "The load zone while storage is over half of the site; nodal after [[NPRR995]]", "nodal": False},
    ]
    n_bytes = write_json(OUT / "index.json", align_cites(doc))

    tx = envelope("texas", "scripts.export_siting", inputs={"prices_sha256": None, "loads_sha256": None, "topology_sha256": None},
                  constants={}, sources={"census": sources["census"]}, series={"outline": series["outline"]})
    lons = [p[0] for p in poly]
    lats = [p[1] for p in poly]
    tx["name"] = props["NAME"]
    tx["bbox"] = [round(min(lons), 4), round(min(lats), 4), round(max(lons), 4), round(max(lats), 4)]
    tx["outline"] = [[round(p[0], 4), round(p[1], 4)] for p in poly]
    n_tx = write_json(OUT / "texas.json", tx)
    print(f"wrote {(OUT / 'index.json').relative_to(ROOT).as_posix()} ({n_bytes / 1024:.1f} KB) and texas.json ({n_tx / 1024:.1f} KB, {len(poly)} points, epsilon {eps:.4f} deg)")


if __name__ == "__main__":
    main()
