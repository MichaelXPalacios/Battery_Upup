"""Phase 7 (agent R1): regional coverage from the ERCOT 60-day day-ahead disclosure.

Gap closed here. Phase 3b could replay only 260 of 1,386 constraints and had no candidate site in the Panhandle, the Rio Grande Valley or the Far West,
because shift factors came from one real-time week (3-10 Oct) and 31 day-ahead days (10 Sep-10 Oct), when the structural and seasonal constraints barely bound.
The dispatch platform's lake holds the 60-day DAM disclosure: hourly day-ahead settlement point prices (SPPs) for about 1,000 settlement points on the 42
delivery days 1 Jul - 11 Aug 2026 (peak summer). Those are REAL prices; the congestion between nodes in them is measured, not modelled.

Three products (commands in gridsite/cli_7.py):

  regional-basis       data/regional_basis.csv (+ summary): node basis against its hub, its load zone and the system, by region.            REAL / DERIVED
  sf-structural        derived/sf_structural.parquet + data/sf_structural.csv + data/sf_structural_validation.csv: shift factors for the     DERIVED
                       structural / regional constraints, from a low-rank (PCA + varimax) decomposition of the 42-day hourly basis matrix.
  regional-candidates  data/regional_candidates.csv: screened substations in the Panhandle, Valley, Far West and Laredo/South, plus the existing 40.  DERIVED

Sign convention (PLAN.md, used everywhere): cong[n,t] = LMP[n,t] - lambda[t] = - sum_k SF[n,k] * mu[k,t], mu >= 0 the shadow price.
SF < 0 is the IMPORT side (price rises when the constraint binds: a battery sells); SF > 0 is the EXPORT side (price falls: a battery charges).

Honest limits (repeated in notes/phase7-coverage.md): the lake holds no shadow prices, so the constraint behind a pattern is INFERRED (geography, timing,
IMM monthly reports, existing shift factors), never read; DAM only (no real-time); one summer window; resource-node prices only (a substation without a
resource node gets an interpolated shift factor, labelled as such).

Unverified constants are single named values at the top, each with a label (REAL / DERIVED / SIM / ASSUMPTION).
"""
from __future__ import annotations

import fnmatch
import glob
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd

from . import paths
from .nodal_lanes import PANHANDLE_BOX

DATA = paths.REPO_DATA
DERIVED = paths.DERIVED
CACHE = DERIVED / "_p7_cache"

# =================================================================================================================
# Constants
# =================================================================================================================
LAKE_DAM60 = paths.RESEARCH_DATA / "dispatch" / "lake" / "parsed" / "dam_spp_60d"   # REAL: 60-day DAM disclosure (NP3-966-ER, report 13051), parsed by the dispatch platform
SYSTEM_REF = "HB_BUSAVG"                 # REAL: ERCOT bus-average hub; the system reference every basis is also measured against
CPT = "America/Chicago"                  # REAL: Central Prevailing Time (all 42 days are daylight time, UTC-5)
MIN_HOURS_STATS = 168                    # ASSUMPTION: a settlement point needs at least a week of hours (168) for basis statistics
HUB_OF_ZONE = {"LZ_WEST": "HB_WEST", "LZ_NORTH": "HB_NORTH", "LZ_SOUTH": "HB_SOUTH", "LZ_HOUSTON": "HB_HOUSTON",
                "LZ_AEN": "HB_SOUTH", "LZ_CPS": "HB_SOUTH", "LZ_LCRA": "HB_SOUTH", "LZ_RAYBN": "HB_NORTH"}   # ASSUMPTION: the hub a zone is traded against; the municipal / co-op zones take the nearest hub (HB_SOUTH for Austin, San Antonio, LCRA; HB_NORTH for Rayburn)
PANHANDLE_HUB = "HB_PAN"                 # ASSUMPTION: a Panhandle node is measured against HB_PAN (same rule as nodal_lanes.geographic_driver)
BASIS_BIG = (10.0, 50.0)                 # REAL: the brief's two thresholds, $/MWh, for "hours with |basis| above"

# Region tags (brief task 1). Order matters: the first that matches wins. Boxes are (lat_min, lat_max, lon_min, lon_max).
RGV_COUNTIES = ("CAMERON", "HIDALGO", "WILLACY", "STARR")                  # REAL: the brief's Valley counties
FAR_WEST_LAT_MAX = 33.3                  # ASSUMPTION: ... and south of this latitude (the Permian and Delaware basins, Midland-Odessa, Big Spring); Lubbock and the South Plains lie north of it and are tagged Other (or Panhandle if inside PANHANDLE_BOX)
FAR_WEST_LON = -101.3                    # ASSUMPTION: LZ_WEST substations west of this longitude (Midland, Odessa, Pecos, Reeves, Ward, Howard, Borden) are "Far West" (Permian / Delaware); the Big Country, Concho Valley and Abilene (east of it) stay "Other"
LAREDO_COUNTIES = ("WEBB", "ZAPATA", "MAVERICK", "DIMMIT", "ZAVALA", "LA SALLE", "JIM HOGG", "BROOKS", "KENEDY", "KLEBERG", "DUVAL", "FRIO", "KINNEY", "VAL VERDE", "UVALDE")   # ASSUMPTION: LZ_SOUTH counties of the Laredo / Eagle Pass / Coastal Bend wind-and-solar belt, outside the Valley
DFW_BOX = (32.3, 33.5, -97.9, -96.3)     # ASSUMPTION: Dallas-Fort Worth metroplex
CENTRAL_BOX = (29.0, 31.4, -99.0, -96.9)  # ASSUMPTION: Austin - San Antonio - Waco corridor
CENTRAL_ZONES = ("LZ_AEN", "LZ_CPS", "LZ_LCRA")   # ASSUMPTION: municipal / co-op zones of Central Texas
REGIONS = ("Panhandle", "Rio Grande Valley", "Far West", "Laredo/South", "Houston", "DFW", "Central", "Other")

# 2 h merchant battery for the observed day-ahead value (arena SPECS["grid"]: 1 MW / 2 MWh, 85% round trip, $20/MWh wear)
BAT_MW = 1.0                             # REAL: arena spec "grid"
BAT_MWH = 2.0                            # REAL: arena spec "grid"
BAT_RTE = 0.85                           # REAL: arena spec "grid"
BAT_WEAR = 20.0                          # ASSUMPTION: arena wear cost, $ per MWh discharged
BAT_STEP_MWH = 0.25                      # ASSUMPTION: state grid of the day-ahead dispatch DP (0.25 MWh of stored energy)


# =================================================================================================================
# Small helpers
# =================================================================================================================
def _read_csv(name: str, **kw) -> pd.DataFrame:
    """Repo table; `NA` is a real substation code, so only the empty string is missing."""
    return pd.read_csv(DATA / name, keep_default_na=False, na_values=[""], low_memory=False, **kw)


def write_labels(csv_path, spec: list, df: pd.DataFrame) -> None:
    """Sidecar `<name>.labels.csv` (column,label,source,note); spec entries are (fnmatch pattern, label, source, note), first match wins."""
    rows = []
    for col in df.columns:
        hit = next((x for x in spec if fnmatch.fnmatchcase(col, x[0])), None)
        if hit is None:
            raise KeyError(f"no label for column {col!r} of {csv_path}")
        rows.append({"column": col, "label": hit[1], "source": hit[2], "note": hit[3]})
    pd.DataFrame(rows).to_csv(str(csv_path).replace(".csv", ".labels.csv"), index=False)


def _in_box(lat, lon, box) -> bool:
    la0, la1, lo0, lo1 = box
    return bool(pd.notna(lat) and pd.notna(lon) and la0 <= lat <= la1 and lo0 <= lon <= lo1)


# =================================================================================================================
# The 60-day day-ahead panel (REAL)
# =================================================================================================================
def dam60_files() -> list:
    fs = sorted(glob.glob(str(LAKE_DAM60 / "date=*" / "part.parquet")))
    if not fs:
        raise FileNotFoundError(f"no parquet partitions under {LAKE_DAM60}")
    return fs


def load_panel() -> pd.DataFrame:
    """Hours (UTC hour start, index) x settlement points: the day-ahead settlement point price, $/MWh. Read-only from the lake.

    Each delivery hour appears once (the disclosure is published once per delivery day); a duplicate (re-published day) keeps the mean."""
    parts = [pd.read_parquet(f, columns=["interval_start_utc", "settlement_point", "price_usd_per_mwh"]) for f in dam60_files()]
    d = pd.concat(parts, ignore_index=True)
    w = d.pivot_table(index="interval_start_utc", columns="settlement_point", values="price_usd_per_mwh", aggfunc="mean").sort_index()
    w.columns = [str(c) for c in w.columns]
    return w


def hour_of_day_cpt(index: pd.DatetimeIndex) -> np.ndarray:
    """Hour-beginning in CPT, 0..23 (HB19 is hour-ending 20)."""
    return index.tz_convert(CPT).hour.to_numpy()


# =================================================================================================================
# Settlement points -> substation -> coordinates -> region (re-read from the repo tables every call: agent G1 corrects coordinates)
# =================================================================================================================
def region_of(lat, lon, county, zone) -> tuple[str, str]:
    """(region, how). 'how' is 'coords' when a coordinate or county decided it, 'zone' when only the load zone did, 'none' otherwise."""
    county = str(county).upper() if pd.notna(county) else ""
    have_xy = pd.notna(lat) and pd.notna(lon)
    if have_xy and _in_box(lat, lon, PANHANDLE_BOX):
        return "Panhandle", "coords"
    if county in RGV_COUNTIES:
        return "Rio Grande Valley", "coords"
    if have_xy and zone == "LZ_WEST" and lon <= FAR_WEST_LON and lat <= FAR_WEST_LAT_MAX:
        return "Far West", "coords"
    if zone == "LZ_SOUTH" and county in LAREDO_COUNTIES:
        return "Laredo/South", "coords"
    if zone == "LZ_HOUSTON":
        return "Houston", "zone"
    if have_xy and zone in ("LZ_NORTH", "LZ_RAYBN") and _in_box(lat, lon, DFW_BOX):
        return "DFW", "coords"
    if zone in CENTRAL_ZONES:
        return "Central", "zone"
    if have_xy and _in_box(lat, lon, CENTRAL_BOX):
        return "Central", "coords"
    return "Other", ("coords" if have_xy else "none")


def sp_table(columns) -> pd.DataFrame:
    """One row per settlement point in the panel: type, substation, bus, kV, zone, hub, coordinates, region.

    Resource nodes come from settlement_points.csv (RESOURCE_NODE -> SUBSTATION / ELECTRICAL_BUS); coordinates and county from substations.csv.
    Hubs (HB_*), load zones (LZ_*) and DC ties (DC_*) are typed by name; anything else is 'other'."""
    sp = _read_csv("settlement_points.csv")
    ss = _read_csv("substations.csv").set_index("sub")
    rn = sp[sp["RESOURCE_NODE"].notna()].drop_duplicates("RESOURCE_NODE").set_index("RESOURCE_NODE")
    rows = []
    for c in columns:
        r = dict(settlement_point=c, sp_type="other", substation=None, bus=None, kv=np.nan, zone=None, hub=None, lat=np.nan, lon=np.nan, county=None, geo_conf=np.nan)
        if c in rn.index:
            x = rn.loc[c]
            r.update(sp_type="resource_node", substation=x["SUBSTATION"], bus=x["ELECTRICAL_BUS"], kv=x["VOLTAGE_LEVEL"],
                     zone=x["NOIE_ZONE"] if pd.notna(x["NOIE_ZONE"]) else x["SETTLEMENT_LOAD_ZONE"])
            if x["SUBSTATION"] in ss.index:
                s = ss.loc[x["SUBSTATION"]]
                r.update(lat=s["lat"], lon=s["lon"], county=s["county"], geo_conf=s["match_conf"])
        elif c.startswith("HB_"):
            r["sp_type"] = "hub"
        elif c.startswith("LZ_"):
            r.update(sp_type="load_zone", zone=c)
        elif c.startswith("DC_"):
            r["sp_type"] = "dc_tie"
        rows.append(r)
    t = pd.DataFrame(rows)
    reg = [region_of(a, b, cty, z) if tp == "resource_node" else (None, None) for a, b, cty, z, tp in zip(t["lat"], t["lon"], t["county"], t["zone"], t["sp_type"])]
    t["region"] = [x[0] for x in reg]
    t["region_how"] = [x[1] for x in reg]
    t["hub"] = [PANHANDLE_HUB if rg == "Panhandle" else HUB_OF_ZONE.get(z) for rg, z in zip(t["region"], t["zone"])]
    t.loc[t["sp_type"] == "load_zone", "hub"] = t.loc[t["sp_type"] == "load_zone", "zone"].map(HUB_OF_ZONE)
    return t


# =================================================================================================================
# Basis statistics (task 1)
# =================================================================================================================
def _stats(b: np.ndarray, hours_cpt: np.ndarray) -> dict:
    """Statistics of one basis series (NaN where the point had no price). Basis = node price - reference price, $/MWh."""
    ok = np.isfinite(b)
    n = int(ok.sum())
    out = dict(n=n)
    if n < MIN_HOURS_STATS:
        return {**out, "mean": np.nan, "p05": np.nan, "p95": np.nan, "n10": np.nan, "n50": np.nan, "n10_pos": np.nan, "n10_neg": np.nan, "top3": "", "top3_mean": ""}
    x = b[ok]
    h = hours_cpt[ok]
    hm = np.array([x[h == k].mean() if (h == k).any() else np.nan for k in range(24)])
    top = np.argsort(-np.nan_to_num(np.abs(hm), nan=-1))[:3]
    return dict(n=n, mean=float(x.mean()), p05=float(np.percentile(x, 5)), p95=float(np.percentile(x, 95)),
                n10=int((np.abs(x) > BASIS_BIG[0]).sum()), n50=int((np.abs(x) > BASIS_BIG[1]).sum()),
                n10_pos=int((x > BASIS_BIG[0]).sum()), n10_neg=int((x < -BASIS_BIG[0]).sum()),
                top3=";".join(str(int(k)) for k in top), top3_mean=";".join(f"{hm[k]:.1f}" for k in top))


def dam_oracle_value(P: np.ndarray, eff: float = BAT_RTE, wear: float = BAT_WEAR) -> np.ndarray:
    """Perfect-foresight day-ahead value of a 1 MW / 2 MWh battery on hourly prices, $ per kW-day, one value per day.

    P is (days, nodes, 24) of $/MWh. Each day starts empty. Stored-energy grid of BAT_STEP_MWH; charge e MWh of stored energy buys e/eta_c, discharge of e delivers e*eta_d
    (eta_c = eta_d = sqrt(RTE)); power is limited to BAT_MW at the meter; wear is charged per MWh delivered. A missing price (NaN) is filled by the day's mean for that node
    so a gap neither pays nor costs. Result in $ per kW (price $/MWh x MWh / 1000 kW per MW)."""
    D, N, T = P.shape
    ec = ed = np.sqrt(eff)
    S = int(round(BAT_MWH / BAT_STEP_MWH)) + 1
    max_c = int(np.floor(BAT_MW * ec / BAT_STEP_MWH + 1e-9))      # stored-energy steps chargeable in an hour at the power limit
    max_d = int(np.floor(BAT_MW / ed / BAT_STEP_MWH + 1e-9))
    p = P.reshape(D * N, T).astype(float)
    mean = np.nanmean(p, axis=1, keepdims=True)
    p = np.where(np.isfinite(p), p, np.where(np.isfinite(mean), mean, 0.0))
    V = np.full((D * N, S), 0.0)
    for t in range(T - 1, -1, -1):
        pt = p[:, t:t + 1]
        newV = np.full((D * N, S), -1e18)
        for s in range(S):
            best = V[:, s].copy()                                  # idle
            for k in range(1, max_c + 1):                          # charge k steps
                if s + k < S:
                    best = np.maximum(best, V[:, s + k] - pt[:, 0] * (k * BAT_STEP_MWH / ec))
            for k in range(1, max_d + 1):                          # discharge k steps
                if s - k >= 0:
                    deliver = k * BAT_STEP_MWH * ed
                    best = np.maximum(best, V[:, s - k] + (pt[:, 0] - wear) * deliver)
            newV[:, s] = best
        V = newV
    return (V[:, 0] / 1000.0).reshape(D, N)


def observed_dam_values(panel: pd.DataFrame, cols: list, zone_of: dict) -> pd.DataFrame:
    """Per column: 2 h oracle value over the whole window on its own price and on its zone's price, $ per kW over the window (sum of daily values)."""
    idx = panel.index.tz_convert(CPT)
    days = idx.normalize()
    ud = sorted(set(days))
    keep = [d for d in ud if (days == d).sum() == 24]
    arr = {}
    for c in set(cols) | {z for z in zone_of.values() if z in panel.columns}:
        a = panel[c].to_numpy(float)
        arr[c] = np.stack([a[days == d] for d in keep], axis=0)          # (D, 24)
    names = list(arr)
    P = np.stack([arr[c] for c in names], axis=1)                        # (D, N, 24)
    V = dam_oracle_value(P).sum(axis=0)                                  # $ per kW over the window
    val = dict(zip(names, V))
    return pd.DataFrame({"column": cols, "dam_value_node": [val.get(c, np.nan) for c in cols],
                         "dam_value_zone": [val.get(zone_of.get(c), np.nan) for c in cols], "n_days": len(keep)}).set_index("column")


REGIONAL_BASIS_LABELS = [
    ("settlement_point", "REAL", "ERCOT 60-day DAM disclosure (NP3-966-ER)", "settlement point name as published (resource node, hub, load zone, DC tie)"),
    ("sp_type", "DERIVED", "name prefix / data/settlement_points.csv", "resource_node, hub, load_zone, dc_tie or other"),
    ("substation", "REAL", "data/settlement_points.csv (NP4-160)", "RESOURCE_NODE -> SUBSTATION"),
    ("bus", "REAL", "data/settlement_points.csv (NP4-160)", "electrical bus of the resource node"),
    ("kv", "REAL", "data/settlement_points.csv (NP4-160)", "voltage of the resource node's bus"),
    ("zone", "REAL", "data/settlement_points.csv (NP4-160)", "NOIE zone (LZ_AEN, LZ_CPS, LZ_LCRA, LZ_RAYBN) if any, else the settlement load zone"),
    ("hub", "ASSUMPTION", "HUB_OF_ZONE and the Panhandle rule in gridsite/coverage.py", "the hub the node is measured against: zone -> hub map, HB_PAN for the Panhandle"),
    ("lat", "DERIVED", "data/substations.csv (phase 1c geocoding, match_conf in geo_conf)", "substation coordinates; a wrong placement gives a wrong region tag"),
    ("lon", "DERIVED", "data/substations.csv (phase 1c geocoding)", ""),
    ("county", "DERIVED", "data/substations.csv (phase 1c geocoding)", ""),
    ("geo_conf", "DERIVED", "data/substations.csv match_conf", "confidence of the substation placement, 0 to 1"),
    ("region", "ASSUMPTION", "region_of() in gridsite/coverage.py (PANHANDLE_BOX, county lists, boxes)", "Panhandle, Rio Grande Valley, Far West, Laredo/South, Houston, DFW, Central, Other"),
    ("region_how", "DERIVED", "this module", "coords (coordinate or county decided), zone (load zone only) or none"),
    ("n_hours", "REAL", "ERCOT 60-day DAM disclosure", "delivery hours with a price (1,008 = 42 days x 24)"),
    ("price_mean", "REAL", "ERCOT 60-day DAM disclosure", "mean day-ahead SPP over the window, $/MWh"),
    ("basis_hub_*", "DERIVED", "SPP minus the node's hub SPP", "$/MWh; p05 and p95 are percentiles over the 42 days of hours"),
    ("basis_zone_*", "DERIVED", "SPP minus the node's load-zone SPP", "$/MWh; the zone price is what a settlement-only or ADER resource earns"),
    ("basis_sys_*", "DERIVED", "SPP minus HB_BUSAVG", "$/MWh; the reference the factor model uses"),
    ("hours_gt10_hub", "DERIVED", "count of hours with |basis vs hub| > $10", "hours of 1,008"),
    ("hours_gt50_hub", "DERIVED", "count of hours with |basis vs hub| > $50", "hours of 1,008"),
    ("hours_gt10_zone", "DERIVED", "count of hours with |basis vs zone| > $10", "hours of 1,008"),
    ("hours_gt50_zone", "DERIVED", "count of hours with |basis vs zone| > $50", "hours of 1,008"),
    ("hours_gt10_pos_hub", "DERIVED", "hours with basis vs hub above +$10 (premium)", "hours"),
    ("hours_gt10_neg_hub", "DERIVED", "hours with basis vs hub below -$10 (discount)", "hours"),
    ("hours_gt10_sys", "DERIVED", "count of hours with |basis vs HB_BUSAVG| > $10", "hours of 1,008"),
    ("hours_gt50_sys", "DERIVED", "count of hours with |basis vs HB_BUSAVG| > $50", "hours of 1,008"),
    ("top3_hours_hub", "DERIVED", "the three hours of the day with the largest mean |basis vs hub|", "hour-beginning in CPT, ';'-separated (19 = hour-ending 20), largest first"),
    ("top3_mean_hub", "DERIVED", "mean basis vs hub at those three hours", "$/MWh, signed, same order"),
    ("top3_hours_zone", "DERIVED", "the same against the load zone", "a Panhandle node's hub is HB_PAN, which averages the Panhandle itself, so use the zone or system profile there"),
    ("top3_mean_zone", "DERIVED", "", "$/MWh"),
    ("top3_hours_sys", "DERIVED", "the same against HB_BUSAVG", ""),
    ("top3_mean_sys", "DERIVED", "", "$/MWh"),
    ("dam_value_node", "DERIVED", "2 h oracle (1 MW / 2 MWh, 85% round trip, $20/MWh wear) on the node's own day-ahead prices", "$ per kW over the 42 days; not annualised (summer window)"),
    ("dam_value_zone", "DERIVED", "the same on the node's load-zone prices", "$ per kW over the 42 days"),
    ("dam_uplift", "DERIVED", "dam_value_node - dam_value_zone", "$ per kW over the 42 days: what siting at the node added in this window (day-ahead only, perfect foresight)"),
]


def regional_basis(panel: pd.DataFrame | None = None, write: bool = True) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Task 1: per-settlement-point basis table and its summary by region. Returns (points, summary)."""
    panel = load_panel() if panel is None else panel
    cols = list(panel.columns)
    t = sp_table(cols)
    hours = hour_of_day_cpt(panel.index)
    sys_ref = panel[SYSTEM_REF].to_numpy(float)
    rows = []
    for _, r in t.iterrows():
        c = r["settlement_point"]
        p = panel[c].to_numpy(float)
        sysb = _stats(p - sys_ref, hours)
        hub = r["hub"]
        zone = r["zone"]
        hubb = _stats(p - panel[hub].to_numpy(float), hours) if hub in panel.columns and c != hub else _stats(np.full_like(p, np.nan), hours)
        zoneb = _stats(p - panel[zone].to_numpy(float), hours) if zone in panel.columns and c != zone else _stats(np.full_like(p, np.nan), hours)
        rows.append({"n_hours": int(np.isfinite(p).sum()), "price_mean": float(np.nanmean(p)) if np.isfinite(p).any() else np.nan,
                     "basis_hub_mean": hubb["mean"], "basis_hub_p05": hubb["p05"], "basis_hub_p95": hubb["p95"],
                     "basis_zone_mean": zoneb["mean"], "basis_zone_p05": zoneb["p05"], "basis_zone_p95": zoneb["p95"],
                     "basis_sys_mean": sysb["mean"], "basis_sys_p05": sysb["p05"], "basis_sys_p95": sysb["p95"],
                     "hours_gt10_hub": hubb["n10"], "hours_gt50_hub": hubb["n50"], "hours_gt10_zone": zoneb["n10"], "hours_gt50_zone": zoneb["n50"],
                     "hours_gt10_pos_hub": hubb["n10_pos"], "hours_gt10_neg_hub": hubb["n10_neg"],
                     "hours_gt10_sys": sysb["n10"], "hours_gt50_sys": sysb["n50"],
                     "top3_hours_hub": hubb["top3"], "top3_mean_hub": hubb["top3_mean"],
                     "top3_hours_zone": zoneb["top3"], "top3_mean_zone": zoneb["top3_mean"], "top3_hours_sys": sysb["top3"], "top3_mean_sys": sysb["top3_mean"]})
    pts = pd.concat([t, pd.DataFrame(rows)], axis=1)
    is_rn = (pts["sp_type"] == "resource_node") & pts["zone"].isin(panel.columns)
    vals = observed_dam_values(panel, list(pts.loc[is_rn, "settlement_point"]), dict(zip(pts["settlement_point"], pts["zone"])))
    pts["dam_value_node"] = pts["settlement_point"].map(vals["dam_value_node"])
    pts["dam_value_zone"] = pts["settlement_point"].map(vals["dam_value_zone"])
    pts["dam_uplift"] = pts["dam_value_node"] - pts["dam_value_zone"]
    pts.loc[~is_rn, ["dam_value_node", "dam_value_zone", "dam_uplift"]] = np.nan
    num = pts.select_dtypes("number").columns
    pts[num] = pts[num].round(3)
    summary = region_summary(pts, panel)
    if write:
        out = DATA / "regional_basis.csv"
        pts.to_csv(out, index=False)
        write_labels(out, REGIONAL_BASIS_LABELS, pts)
        out2 = DATA / "regional_basis_summary.csv"
        summary.to_csv(out2, index=False)
        write_labels(out2, REGIONAL_SUMMARY_LABELS, summary)
    return pts, summary


REGIONAL_SUMMARY_LABELS = [
    ("region", "ASSUMPTION", "region_of() in gridsite/coverage.py", "Panhandle, Rio Grande Valley, Far West, Laredo/South, Houston, DFW, Central, Other (resource nodes only)"),
    ("reference", "DERIVED", "this module", "what the basis is measured against: hub (the node's hub; HB_PAN in the Panhandle, so its own hub nets it out), zone (its load zone) or system (HB_BUSAVG)"),
    ("n_nodes", "DERIVED", "count of resource nodes", ""),
    ("n_substations", "DERIVED", "count of distinct substations", ""),
    ("basis_mean", "DERIVED", "mean over the nodes of the node's mean basis", "$/MWh"),
    ("basis_median", "DERIVED", "median over the nodes of the node's mean basis", "$/MWh"),
    ("pooled_p05", "DERIVED", "5th percentile of all node-hours of basis", "$/MWh; the typical bad hour"),
    ("pooled_p95", "DERIVED", "95th percentile of all node-hours of basis", "$/MWh"),
    ("share_hours_gt10", "DERIVED", "share of node-hours with |basis| > $10", "0 to 1"),
    ("share_hours_gt50", "DERIVED", "share of node-hours with |basis| > $50", "0 to 1"),
    ("share_hours_neg10", "DERIVED", "share of node-hours with basis below -$10 (discount)", "0 to 1"),
    ("share_hours_pos10", "DERIVED", "share of node-hours with basis above +$10 (premium)", "0 to 1"),
    ("worst_hour_cpt", "DERIVED", "hour-beginning (CPT) at which the region's mean basis is lowest", "0 to 23 (19 = hour-ending 20)"),
    ("worst_hour_mean", "DERIVED", "region mean basis at that hour", "$/MWh"),
    ("best_hour_cpt", "DERIVED", "hour-beginning (CPT) at which the region's mean basis is highest", "0 to 23"),
    ("best_hour_mean", "DERIVED", "region mean basis at that hour", "$/MWh"),
    ("basis_mean_jul", "DERIVED", "region mean basis, July hours", "$/MWh"),
    ("basis_mean_aug", "DERIVED", "region mean basis, 1-11 August hours", "$/MWh"),
    ("dam_uplift_mean", "DERIVED", "mean over the nodes of dam_uplift (the same on all three reference rows)", "$ per kW over 42 days: 2 h day-ahead oracle at the node minus at its load zone"),
    ("dam_uplift_p90", "DERIVED", "90th percentile over the nodes of dam_uplift", "$ per kW over 42 days"),
    ("verdict", "ASSUMPTION", "VERDICT_TAIL_SHARE / VERDICT_HOUR_MEAN rules in region_summary()", "discount (export side), premium (import side), two-sided or little"),
]

VERDICT_TAIL_SHARE = 0.05                # ASSUMPTION: a region has a discount (premium) if at least this share of node-hours is below -$10 (above +$10) ...
VERDICT_HOUR_MEAN = 5.0                  # ASSUMPTION: ... or if its mean basis at its worst (best) hour of the day is beyond -$5 (+$5)
REFERENCES = ("hub", "zone", "system")


def _ref_series(panel: pd.DataFrame, g: pd.DataFrame, reference: str) -> np.ndarray:
    """(hours, nodes) basis of the nodes of g against the chosen reference."""
    if reference == "hub":
        refs = list(g["hub"])
    elif reference == "zone":
        refs = list(g["zone"])
    else:
        refs = [SYSTEM_REF] * len(g)
    return np.stack([panel[c].to_numpy(float) - panel[r].to_numpy(float) for c, r in zip(g["settlement_point"], refs)], axis=1)


def region_summary(pts: pd.DataFrame, panel: pd.DataFrame) -> pd.DataFrame:
    """Summary by region and reference over the resource nodes with enough hours. Hour profiles use the region mean of the node basis."""
    rn = pts[(pts["sp_type"] == "resource_node") & (pts["n_hours"] >= MIN_HOURS_STATS) & pts["zone"].isin(panel.columns) & pts["hub"].isin(panel.columns)].copy()
    hours = hour_of_day_cpt(panel.index)
    month = panel.index.tz_convert(CPT).month.to_numpy()
    node_mean = {"hub": "basis_hub_mean", "zone": "basis_zone_mean", "system": "basis_sys_mean"}
    rows = []
    for rg in REGIONS:
        g = rn[rn["region"] == rg]
        if g.empty:
            continue
        for ref in REFERENCES:
            B = _ref_series(panel, g, ref)
            flat = B[np.isfinite(B)]
            reg_mean = np.nanmean(B, axis=1)
            hm = np.array([np.nanmean(reg_mean[hours == k]) for k in range(24)])
            neg10 = float((flat < -BASIS_BIG[0]).mean())
            pos10 = float((flat > BASIS_BIG[0]).mean())
            disc = neg10 >= VERDICT_TAIL_SHARE or np.nanmin(hm) <= -VERDICT_HOUR_MEAN
            prem = pos10 >= VERDICT_TAIL_SHARE or np.nanmax(hm) >= VERDICT_HOUR_MEAN
            verdict = "two-sided" if (disc and prem) else "discount (export side)" if disc else "premium (import side)" if prem else "little"
            rows.append(dict(region=rg, reference=ref, n_nodes=len(g), n_substations=g["substation"].nunique(),
                             basis_mean=g[node_mean[ref]].mean(), basis_median=g[node_mean[ref]].median(),
                             pooled_p05=float(np.percentile(flat, 5)), pooled_p95=float(np.percentile(flat, 95)),
                             share_hours_gt10=float((np.abs(flat) > BASIS_BIG[0]).mean()), share_hours_gt50=float((np.abs(flat) > BASIS_BIG[1]).mean()),
                             share_hours_neg10=neg10, share_hours_pos10=pos10,
                             worst_hour_cpt=int(np.nanargmin(hm)), worst_hour_mean=float(np.nanmin(hm)), best_hour_cpt=int(np.nanargmax(hm)), best_hour_mean=float(np.nanmax(hm)),
                             basis_mean_jul=float(np.nanmean(reg_mean[month == 7])), basis_mean_aug=float(np.nanmean(reg_mean[month == 8])),
                             dam_uplift_mean=g["dam_uplift"].mean(), dam_uplift_p90=g["dam_uplift"].quantile(0.9), verdict=verdict))
    s = pd.DataFrame(rows)
    num = s.select_dtypes("number").columns
    s[num] = s[num].round(3)
    return s


# =================================================================================================================
# Low-rank decomposition of the hourly basis matrix (task 2a)
# =================================================================================================================
FACTOR_MIN_COVERAGE = 0.90               # ASSUMPTION: a substation enters the decomposition if it has a price in at least this share of the 1,008 hours
FACTOR_MIN_SD = 1.5                      # ASSUMPTION: ... and if its basis against HB_BUSAVG has a standard deviation of at least this, $/MWh (quieter stations carry no congestion signal)
FACTOR_SCALE_CLIP = (2.0, 8.0)           # ASSUMPTION: each station's series is divided by its standard deviation clipped to this range before the decomposition: a single wild node ($60 sd) cannot dominate, a regional pattern keeps its amplitude
HOLDOUT_DAYS = 10                        # REAL: brief: the last 10 days of the 42 are held out
K_PCA = 16                               # ASSUMPTION: components kept before the varimax rotation; chosen by the hold-out curve in notes/phase7-coverage.md
LOCAL_POCKET_STATIONS = 4                # ASSUMPTION: a factor whose loading is above half its maximum at fewer stations than this is a local pocket (one constraint on one or two stations), not a regional pattern


def basis_matrix(panel: pd.DataFrame, sp: pd.DataFrame | None = None) -> pd.DataFrame:
    """Hours x substations: day-ahead SPP of the substation's resource nodes (median over them) minus HB_BUSAVG, $/MWh. Substations with < FACTOR_MIN_COVERAGE of hours are dropped."""
    sp = sp_table(panel.columns) if sp is None else sp
    rn = sp[(sp["sp_type"] == "resource_node") & sp["substation"].notna()]
    B = panel[list(rn["settlement_point"])].sub(panel[SYSTEM_REF], axis=0)
    B.columns = list(rn["substation"])
    B = B.T.groupby(level=0).median().T
    return B.loc[:, B.notna().mean() >= FACTOR_MIN_COVERAGE]


def varimax(phi: np.ndarray, gamma: float = 1.0, q: int = 500, tol: float = 1e-8) -> tuple[np.ndarray, np.ndarray]:
    """Kaiser varimax rotation of a loading matrix (p, k). Returns (rotated loadings, rotation matrix)."""
    p, k = phi.shape
    R = np.eye(k)
    d = 0.0
    for _ in range(q):
        d_old = d
        lam = phi @ R
        u, s, vh = np.linalg.svd(phi.T @ (lam ** 3 - (gamma / p) * lam @ np.diag(np.diag(lam.T @ lam))))
        R = u @ vh
        d = float(s.sum())
        if d_old != 0 and d / d_old < 1 + tol:
            break
    return phi @ R, R


def station_scale(B: pd.DataFrame) -> pd.Series:
    return B.std().clip(lower=FACTOR_SCALE_CLIP[0], upper=FACTOR_SCALE_CLIP[1])


def fit_pca_varimax(B: pd.DataFrame, k: int = K_PCA, train: np.ndarray | None = None) -> dict:
    """PCA of the standardised, station-median-filled basis matrix on the rows `train` (default all), varimax rotation of the first k components.

    Returns dict(L: (N,k) standardised rotated loadings, scale: station scales, cols, explained: variance share per rotated factor, fill: station medians)."""
    rows = np.arange(len(B)) if train is None else np.asarray(train)
    X = B.iloc[rows]
    keep = X.std() >= FACTOR_MIN_SD
    X = X.loc[:, keep]
    fill = X.median()
    X = X.fillna(fill)
    scale = station_scale(X)
    Z = (X / scale).to_numpy(float)
    U, S, Vt = np.linalg.svd(Z, full_matrices=False)
    L0 = Vt[:k].T * S[:k] / np.sqrt(Z.shape[0])
    L, R = varimax(L0)
    ss = (L ** 2).sum(0)
    return dict(L=L, scale=scale, cols=list(X.columns), explained=ss / (Z ** 2).sum() * Z.shape[0], fill=fill, svals=S[:k] ** 2 / (S ** 2).sum())


def scores_given_loadings(Braw: np.ndarray, Lraw: np.ndarray, ridge: float = 1e-6) -> np.ndarray:
    """Least-squares factor scores (T, k) for basis rows Braw (T, N) given raw loadings Lraw (N, k)."""
    A = Lraw.T @ Lraw + ridge * np.eye(Lraw.shape[1])
    return np.linalg.solve(A, Lraw.T @ Braw.T).T


def loadings_given_scores(Braw: np.ndarray, F: np.ndarray, ridge: float = 1e-6) -> np.ndarray:
    """Least-squares raw loadings (N, k) of columns of Braw (T, N) on scores F (T, k); NaN cells are skipped column by column."""
    N = Braw.shape[1]
    L = np.zeros((N, F.shape[1]))
    for j in range(N):
        ok = np.isfinite(Braw[:, j])
        Fj = F[ok]
        L[j] = np.linalg.solve(Fj.T @ Fj + ridge * np.eye(F.shape[1]), Fj.T @ Braw[ok, j])
    return L


# =================================================================================================================
# Non-negative decomposition: the patterns (task 2a)
# =================================================================================================================
NMF_K = 32                               # ASSUMPTION: components; chosen where the hold-out curve flattens (PCA/NMF sweep in notes/phase7-coverage.md): held-out R2 0.43 (6) -> 0.69 (12) -> 0.78 (24) -> 0.79 (30) for regional factors
NMF_TAU = 1.0                            # ASSUMPTION: basis within +-$1/MWh of the system reference is read as "no congestion" (soft threshold), which makes the patterns sparse in time like binding constraints
NMF_SEED = 0                             # REAL: NNDSVDa start is deterministic; the seed only feeds the coordinate-descent tie breaks
NMF_MAX_ITER = 1500                      # ASSUMPTION
POLE_TOP_N = 5                           # ASSUMPTION: a pattern's scale is the mean of its POLE_TOP_N largest absolute loadings, so one wild station cannot set it
SF_ABS_CAP = 1.0                         # ASSUMPTION: |SF| is limited to 1 (same as nodal_lanes.SF_ABS_CAP): a node price cannot move by more than the shadow price
SF_FOOTPRINT_MIN = 0.20                  # ASSUMPTION: on the pole-normalised scale a station loading below this is read as zero: regional patterns have a clear bimodal footprint (most stations at |SF| < 0.1 = regression noise of about 0.1, the footprint at 0.5 and above), and the mid-band is mostly isolated stations far from the footprint
SF_NOISE_FLOOR = 0.05                    # ASSUMPTION: a station with |SF| below this on the pole-normalised scale is not written (read absent as 0, as in phase 2): below it the loading is within the noise of a regression on 1,008 hours
REGIONAL_MIN_STATIONS = 4                # ASSUMPTION: a pattern is regional if at least this many stations have |SF| >= 0.5 (fewer = a local pocket on one or two stations)
REGIONAL_MIN_PEAK = 5.0                  # ASSUMPTION: ... and its shadow-price scale peaks at least this high, $/MWh at the pole
ACTIVE_USD = 1.0                         # ASSUMPTION: a pattern is "binding" in an hour when its scale (the shadow price at the pole) is at least this, $/MWh


def fit_nmf(B: pd.DataFrame, k: int = NMF_K, tau: float = NMF_TAU, rows: np.ndarray | None = None) -> dict:
    """NMF of the sign-split, soft-thresholded, standardised basis matrix: X = [max(-z,0) | max(z,0)] (discount | premium), z = soft(B / scale, tau / scale).

    Returns dict(cols, scale, W (T,k) scores, Hd, Hp (k,N) discount / premium parts, sig (N,k) signed raw loading in $/MWh per unit score (+ = discount = export side), rows).
    Stations with a standard deviation below FACTOR_MIN_SD are left out; NaN cells are filled with the station median."""
    from sklearn.decomposition import NMF

    rows = np.arange(len(B)) if rows is None else np.asarray(rows)
    X0 = B.iloc[rows]
    cols = list(X0.columns[X0.std() >= FACTOR_MIN_SD])
    Xf = X0[cols].fillna(X0[cols].median())
    scale = Xf.std().clip(*FACTOR_SCALE_CLIP).to_numpy()
    Z = Xf.to_numpy(float) / scale
    s = np.sign(Z) * np.maximum(np.abs(Z) - tau / scale, 0.0)
    X = np.concatenate([np.maximum(-s, 0.0), np.maximum(s, 0.0)], axis=1)
    N = len(cols)
    m = NMF(n_components=k, init="nndsvda", solver="cd", max_iter=NMF_MAX_ITER, tol=1e-6, random_state=NMF_SEED)
    W = m.fit_transform(X)
    H = m.components_
    sig = ((H[:, :N] - H[:, N:]) * scale).T            # (N, k): + = discount (export side), - = premium
    return dict(cols=cols, scale=scale, W=W, Hd=H[:, :N], Hp=H[:, N:], sig=sig, rows=rows, k=k, tau=tau,
                recon_r2=float(1 - ((X - W @ H) ** 2).sum() / (X ** 2).sum()))


def pole_scale(sig_col: np.ndarray, top_n: int = POLE_TOP_N) -> float:
    a = np.sort(np.abs(sig_col))[::-1]
    return float(a[:top_n].mean()) if len(a) else 0.0


def pattern_scores(model: dict) -> np.ndarray:
    """(T, k) shadow-price scale of each pattern in $/MWh at its pole: W times the pole loading. Non-negative by construction."""
    return model["W"] * np.array([pole_scale(model["sig"][:, j]) for j in range(model["k"])])[None, :]


def station_sf(Braw: np.ndarray, mu: np.ndarray, ridge: float = 1e-3) -> tuple[np.ndarray, np.ndarray]:
    """Station shift factors by least squares: b[n,t] = - sum_j SF[n,j] mu[t,j] (no intercept). Braw (T, N) with NaN allowed, mu (T, k).

    Returns (SF (N, k), se (N, k)); cap and threshold are applied by the caller. The ridge keeps the solve stable for patterns that are never active (their SF is 0)."""
    T, N = Braw.shape
    k = mu.shape[1]
    SF = np.zeros((N, k))
    SE = np.zeros((N, k))
    for n in range(N):
        ok = np.isfinite(Braw[:, n])
        M = mu[ok]
        A = M.T @ M + ridge * np.eye(k)
        beta = np.linalg.solve(A, M.T @ Braw[ok, n])
        res = Braw[ok, n] - M @ beta
        s2 = float(res @ res) / max(ok.sum() - k, 1)
        cov = s2 * np.linalg.inv(A)
        SF[n] = -beta
        SE[n] = np.sqrt(np.maximum(np.diag(cov), 0.0))
    return SF, SE


# =================================================================================================================
# Station table (substation level) with region tags
# =================================================================================================================
def station_table() -> pd.DataFrame:
    """One row per substation of data/substations.csv: coordinates, county, kV, NP4-160 membership, placement confidence, zone (NOIE zone first, else load zone),
    resource-node flag and region. Re-read from the repo tables on every call (agent G1 corrects coordinates in parallel)."""
    ss = _read_csv("substations.csv")
    sp = _read_csv("settlement_points.csv")
    z = sp.drop_duplicates("SUBSTATION").set_index("SUBSTATION")
    z = z["NOIE_ZONE"].where(z["NOIE_ZONE"].notna(), z["SETTLEMENT_LOAD_ZONE"])
    t = ss[["sub", "lat", "lon", "county", "kv_max", "match_conf", "in_np4160", "has_resource_node"]].copy()
    t["zone"] = t["sub"].map(z)
    t["zone"] = t["zone"].where(t["zone"].notna(), ss["zone"])
    reg = [region_of(a, b, c, d) for a, b, c, d in zip(t["lat"], t["lon"], t["county"], t["zone"])]
    t["region"] = [r[0] for r in reg]
    t["region_how"] = [r[1] for r in reg]
    return t.set_index("sub", drop=False)


def km_between(la1, lo1, la2, lo2) -> np.ndarray:
    """Great-circle distance in km (haversine), broadcasting."""
    la1, lo1, la2, lo2 = (np.radians(np.asarray(x, float)) for x in (la1, lo1, la2, lo2))
    a = np.sin((la2 - la1) / 2) ** 2 + np.cos(la1) * np.cos(la2) * np.sin((lo2 - lo1) / 2) ** 2
    return 6371.0 * 2 * np.arcsin(np.sqrt(a))


# =================================================================================================================
# Evidence from the market monitor and from the Sep-Oct window (REAL)
# =================================================================================================================
IMM_MONTHS = ("JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG")   # REAL: the months available in the Potomac Economics monthly report of August 2026
# REAL: Potomac Economics, ERCOT IMM Monthly Reports to PUCT for July 2026 (published Aug 2026) and August 2026 (published 21 Sep 2026), Report 18-19 ("RT / DA Congestion Constraint Rankings").
# Monthly 2026 values of the elements the brief asks about; the August report restates the earlier months. RT intervals are 5-minute SCED intervals; DA hours are hours.
IMM_2026 = {
    ("RT", "intervals"): {"NELRIO": [1322, 1510, 2517, 2967, 1567, 633, 2020, 1744], "NE_LOB": [530, 336, 2113, 1577, 577, 380, 1140, 847],
                          "WESTEX": [185, 664, 1274, 828, 1523, 1204, 431, 262], "6965__A": [1048, 584, 2401, 1131, 743, 643, 35, 1],
                          "6437__F": [481, 425, 833, 934, 844, 1239, 154, 600]},
    ("RT", "rent_musd"): {"WESTEX": [1.27, 7.87, 15.48, 12.32, 24.00, 17.25, 4.54, 2.03], "6965__A": [9.57, 3.75, 17.78, 8.81, 6.03, 2.97, 0.03, 0.02]},
    ("DA", "hours"): {"NELRIO": [165, 218, 401, 370, 242, 301, 278, 251], "6437__F": [489, 564, 451, 518, 292, 430, 331, 468],
                      "HARGRO_TWINBU1_1": [658, 419, 404, 335, 435, 386, 460, 325]},
    ("DA", "rent_musd"): {"WESTEX": [2.11, 7.77, 20.93, 13.42, 22.44, 26.70, 4.57, 5.61], "E_PASP": [46.60, 0.82, 2.78, 5.40, 1.83, 1.56, 3.48, 5.31],
                          "6965__A": [10.43, 4.01, 14.65, 7.14, 6.52, 6.67, 0.07, 0.09]},
}
IMM_2026_TOP10_FLOOR = {"RT_rent_musd": 27.14, "RT_intervals": 4937, "DA_rent_musd": 31.91, "DA_hours": 2065}   # REAL: the 10th-ranked value of each 2026 year-to-date top-10 list (Jan-Aug, August report); an element below it is not in the list
IMM_PATTERN_WINDOW = {"JUL": 1.0, "AUG": 11.0 / 31.0}                  # REAL: the 42 delivery days cover all of July and 1-11 August (share of each month inside the window)
HOURS_JUL = 31 * 24                                                    # REAL
RT_INTERVALS_JUL = 31 * 24 * 12                                        # REAL: 5-minute intervals in July


def imm_july_share(key: str) -> dict:
    """Share of July in which the element bound, by market: {'DA': hours/744, 'RT': intervals/8928} (missing = not in the report)."""
    out = {}
    v = IMM_2026[("DA", "hours")].get(key)
    if v:
        out["DA"] = v[6] / HOURS_JUL
    v = IMM_2026[("RT", "intervals")].get(key)
    if v:
        out["RT"] = v[6] / RT_INTERVALS_JUL
    return out


# The thirteen locations a shadow price is timed against in Phase 3b (nodal_lanes.DRIVER_LOCATIONS): five hubs and eight load zones
DRIVER_LOCS = ("HB_WEST", "HB_NORTH", "HB_SOUTH", "HB_HOUSTON", "HB_PAN", "LZ_WEST", "LZ_NORTH", "LZ_SOUTH", "LZ_HOUSTON", "LZ_AEN", "LZ_CPS", "LZ_LCRA", "LZ_RAYBN")   # REAL

SEP_OCT_SPP = DERIVED / "spp_dam.parquet"                 # REAL: day-ahead settlement point prices, 10 Sep - 10 Oct 2026 (phase 1a)
SEP_OCT_SHADOW = DERIVED / "shadow_dam.parquet"           # REAL: day-ahead published shadow prices, same window (NP4-191-CD)
SEP_OCT_SF_SUB = DERIVED / "_p2_cache" / "sf_sub.parquet"  # DERIVED: phase 2 substation shift factors (all methods)


def load_sepoct_basis(sp: pd.DataFrame, stations: list) -> tuple[pd.DataFrame, pd.DataFrame]:
    """(basis, prices): hours (UTC start) x stations for 10 Sep - 10 Oct 2026, the median of the substation's resource nodes minus HB_BUSAVG, and the full price panel."""
    spp = pd.read_parquet(SEP_OCT_SPP, columns=["settlement_point", "interval_start_utc", "price"])
    w = spp.pivot_table(index="interval_start_utc", columns="settlement_point", values="price", aggfunc="mean").sort_index()
    rn = sp[(sp["sp_type"] == "resource_node") & sp["substation"].notna() & sp["settlement_point"].isin(w.columns)]
    B = w[list(rn["settlement_point"])].sub(w[SYSTEM_REF], axis=0)
    B.columns = list(rn["substation"])
    B = B.T.groupby(level=0).median().T
    return B.reindex(columns=stations), w


def load_published_mu(index: pd.DatetimeIndex) -> pd.DataFrame:
    """Hours x cid: the published day-ahead shadow price ($/MWh, 0 when not binding) for 10 Sep - 10 Oct 2026."""
    sh = pd.read_parquet(SEP_OCT_SHADOW, columns=["interval_start_utc", "cid", "shadow_price"])
    p = sh.pivot_table(index="interval_start_utc", columns="cid", values="shadow_price", aggfunc="max")
    return p.reindex(index).fillna(0.0)


def nnls_hourly(Bhours: np.ndarray, SF: np.ndarray) -> np.ndarray:
    """Hour by hour non-negative least squares of the observed basis on -SF: b[n] = - sum_j SF[n,j] mu[j]. Bhours (T, N) may contain NaN; returns mu (T, k) in $/MWh."""
    from scipy.optimize import nnls

    T = Bhours.shape[0]
    mu = np.zeros((T, SF.shape[1]))
    for t in range(T):
        ok = np.isfinite(Bhours[t])
        mu[t], _ = nnls(-SF[ok], Bhours[t, ok], maxiter=2000)
    return mu


def deseasonalise(x: np.ndarray, hours: np.ndarray) -> np.ndarray:
    s = pd.Series(np.asarray(x, float))
    return (s - s.groupby(hours).transform("mean")).to_numpy()


def corr_safe(a: np.ndarray, b: np.ndarray) -> float:
    a = np.asarray(a, float)
    b = np.asarray(b, float)
    ok = np.isfinite(a) & np.isfinite(b)
    if ok.sum() < 10 or a[ok].std() == 0 or b[ok].std() == 0:
        return np.nan
    return float(np.corrcoef(a[ok], b[ok])[0, 1])


# =================================================================================================================
# Target constraints and the evidence for each (task 2b)
# =================================================================================================================
POCKET_RADIUS_KM = 40.0                  # ASSUMPTION: stations within this distance of a line's ends form its pocket; those 40-150 km away are the surround
POCKET_SURROUND_KM = 150.0               # ASSUMPTION
POCKET_BIG = 10.0                        # ASSUMPTION: hours in which the pocket's median basis differs from the surround's by more than $10/MWh
POCKET_MIN_HOURS = 24                    # ASSUMPTION: a line target counts as binding in the window if its pocket deviates by more than $10 in at least this many hours (one day)
EVID_MIN_PRECISION = 0.5                 # ASSUMPTION: at least this share of a pattern's loading mass (|SF| >= 0.3) must lie in the target's domain
EVID_MIN_STATIONS = 4                    # ASSUMPTION: and at least this many of the domain's priced stations must carry |SF| >= 0.3 on the right side (recall, the share of the domain, is reported but a wide domain does not fail a tight pattern)
EVID_MIN_SIDE = 0.7                      # ASSUMPTION: at least this share of the in-domain mass must be on the target's side (export: SF > 0, import: SF < 0)
SF_MASS_MIN = 0.3                        # ASSUMPTION: stations with |SF| below this do not count towards a pattern's footprint
OOP_MIN_BOUND_HOURS = 20                 # ASSUMPTION: a published Sep-Oct series is a usable test if the constraint bound in at least this many hours
OOP_GRADE_A = 0.60                       # ASSUMPTION: deseasonalised correlation of the pattern's Sep-Oct activity with the published shadow price: grade A at or above ...
OOP_GRADE_B = 0.40                       # ASSUMPTION: ... grade B at or above this; below it the published series contradicts the assignment (grade D)
SCALE_SLOPE_RANGE = (0.03, 1.00)         # ASSUMPTION: a calibration slope outside this range is not a physical pole shift factor (an interface's sensitivity to one station is rarely below 0.03, never above 1) and is ignored

# kind "gtc": the domain is a region / county / zone set; kind "line": the domain is the stations around the element's two ends (pocket test).
# `published` is the cid in the Sep-Oct published shadow prices (NP4-191-CD); `imm` the key in IMM_2026; `group` targets that cannot be told apart share a pattern.
TARGETS = [
    dict(key="WESTEX", cid="WESTEX|BASE CASE", label="West Texas Export GTC", kind="gtc", side="export", domain=dict(zones=["LZ_WEST"]), published="WESTEX|BASE CASE", imm="WESTEX", group=""),
    dict(key="I_FW_N", cid="I_FW_N|BASE CASE", label="Far West import from the North GTC", kind="gtc", side="import", domain=dict(regions=["Far West"]), published="I_FW_N|BASE CASE", imm=None, group="FARWEST_IMPORT"),
    dict(key="I_FW_S", cid="I_FW_S|BASE CASE", label="Far West import from the South GTC", kind="gtc", side="import", domain=dict(regions=["Far West"]), published="I_FW_S|BASE CASE", imm=None, group="FARWEST_IMPORT"),
    dict(key="PNHNDL", cid="PNHNDL|BASE CASE", label="Panhandle GTC", kind="gtc", side="export", domain=dict(regions=["Panhandle"], box=(33.4, 36.6, -103.1, -100.0)), published="PNHNDL|BASE CASE", imm=None, group=""),
    dict(key="NELRIO", cid="NELRIO|BASE CASE", label="Nelson Sharpe - Rio Hondo GTC", kind="gtc", side="export",
         domain=dict(counties=["KENEDY", "WILLACY", "CAMERON", "KLEBERG", "NUECES"]), published="NELRIO|BASE CASE", imm="NELRIO", group=""),
    dict(key="NE_LOB", cid="NE_LOB|BASE CASE", label="North Edinburg - Lobo GTC", kind="gtc", side="export",
         domain=dict(counties=["WEBB", "ZAPATA", "STARR", "HIDALGO", "JIM HOGG", "BROOKS"]), published="NE_LOB|BASE CASE", imm="NE_LOB", group="VALLEY_EXPORT"),
    dict(key="VALEXP", cid="VALEXP|BASE CASE", label="Valley Export GTC", kind="gtc", side="export", domain=dict(regions=["Rio Grande Valley"]), published=None, imm=None, group="VALLEY_EXPORT"),
    dict(key="VALIMP", cid="VALIMP|BASE CASE", label="Valley Import GTC", kind="gtc", side="import", domain=dict(regions=["Rio Grande Valley"]), published="VALIMP|BASE CASE", imm=None, group=""),
    dict(key="PASP_PATA", cid="E_PASP|BASE CASE", label="South Texas Export GTC (E_PASP / PASP_PATA)", kind="gtc", side="export",
         domain=dict(counties=["BEE", "KARNES", "GOLIAD", "SAN PATRICIO", "NUECES", "KENEDY", "WILLACY", "HIDALGO", "STARR", "JIM WELLS", "CAMERON", "KLEBERG", "WEBB", "ZAPATA", "LIVE OAK", "REFUGIO", "BROOKS", "DUVAL", "JIM HOGG"]),
         published="E_PASP|BASE CASE", imm="E_PASP", group=""),
    dict(key="LARDVFT", cid="LARDVNTH-LASCRUCE|UNSEEN", label="Laredo VFT North - Las Cruces 138 kV", kind="line", side="either", domain=dict(ends=("LARDVNTH", "LASCRUCE")), published="LARDVFT_VFTN|BASE CASE", imm=None, group=""),
    dict(key="ODEHV_YARBR", cid="ODEHV-YARBR|UNSEEN", label="Odessa EHV - Yarbrough 138 kV", kind="line", side="either", domain=dict(ends=("ODEHV", "YARBR")), published=None, imm=None, group=""),
    dict(key="VEALMOOR_KOCH", cid="VEALMOOR-KOCHTAP|UNSEEN", label="Vealmoor - Koch Tap 138 kV", kind="line", side="either", domain=dict(ends=("VEALMOOR", "KOCH")), published=None, imm=None, group=""),
    dict(key="LNGSW_CONSW", cid="LNGSW-CONSW|UNSEEN", label="Longshore - Consavvy 345 kV", kind="line", side="either", domain=dict(ends=("LNGSW", "CONSW")), published=None, imm="6965__A", group=""),
    dict(key="BELCNTY_SALSW", cid="BELCNTY-SALSW|UNSEEN", label="Bell County - Salado 138 kV", kind="line", side="either", domain=dict(ends=("BELCNTY", "SALSW")), published=None, imm=None, group=""),
]


def domain_mask(st: pd.DataFrame, dom: dict, ends_xy: dict | None = None) -> np.ndarray:
    """Boolean mask over the rows of the station table `st` for a target domain: regions, counties, zones, a box, or the stations within POCKET_RADIUS_KM of a line's ends."""
    m = np.zeros(len(st), bool)
    if dom.get("regions"):
        m |= st["region"].isin(dom["regions"]).to_numpy()
    if dom.get("counties"):
        m |= st["county"].astype(str).str.upper().isin(dom["counties"]).to_numpy()
    if dom.get("zones"):
        m |= st["zone"].isin(dom["zones"]).to_numpy()
    if dom.get("box"):
        la0, la1, lo0, lo1 = dom["box"]
        m |= ((st["lat"] >= la0) & (st["lat"] <= la1) & (st["lon"] >= lo0) & (st["lon"] <= lo1)).to_numpy()
    if dom.get("ends") and ends_xy:
        pts = [ends_xy[e] for e in dom["ends"] if e in ends_xy]
        if pts:
            la = np.mean([p[0] for p in pts])
            lo = np.mean([p[1] for p in pts])
            d = km_between(st["lat"].to_numpy(float), st["lon"].to_numpy(float), la, lo)
            m |= np.isfinite(d) & (d <= POCKET_RADIUS_KM)
    return m


def line_ends_xy(cid: str, st: pd.DataFrame) -> dict:
    """{station: (lat, lon)} for the two ends of a constraint of data/constraints.csv (aliased NP4-160 substations); only the ends with coordinates."""
    c = _read_csv("constraints.csv")
    r = c[c["cid"] == cid]
    out = {}
    if r.empty:
        return out
    for col in ("from_sub", "to_sub"):
        s = r.iloc[0][col]
        if isinstance(s, str) and s in st.index and pd.notna(st.at[s, "lat"]):
            out[s] = (float(st.at[s, "lat"]), float(st.at[s, "lon"]))
    return out


def pocket_series(B: pd.DataFrame, st: pd.DataFrame, ends_xy: dict) -> tuple[np.ndarray, int, int]:
    """Median basis of the priced stations within POCKET_RADIUS_KM of the element's ends minus that of the stations POCKET_RADIUS_KM-POCKET_SURROUND_KM away: (series, n_near, n_far)."""
    if not ends_xy:
        return np.full(len(B), np.nan), 0, 0
    la = np.mean([p[0] for p in ends_xy.values()])
    lo = np.mean([p[1] for p in ends_xy.values()])
    s = st.reindex(B.columns)
    d = km_between(s["lat"].to_numpy(float), s["lon"].to_numpy(float), la, lo)
    near = np.where(np.isfinite(d) & (d <= POCKET_RADIUS_KM))[0]
    far = np.where(np.isfinite(d) & (d > POCKET_RADIUS_KM) & (d <= POCKET_SURROUND_KM))[0]
    if len(near) < 1 or len(far) < 3:
        return np.full(len(B), np.nan), len(near), len(far)
    X = B.to_numpy(float)
    return np.nanmedian(X[:, near], axis=1) - np.nanmedian(X[:, far], axis=1), len(near), len(far)


# =================================================================================================================
# The analysis: patterns, evidence, assignment, scale, driver (task 2a-c)
# =================================================================================================================
def pattern_descriptors(mu: np.ndarray, SF: np.ndarray, cols: list, st: pd.DataFrame, index: pd.DatetimeIndex) -> pd.DataFrame:
    """One row per pattern: size, activity, footprint (stations, sides, regions) and time profile. mu (T, k) in $/MWh at the pole, SF (N, k)."""
    hours = hour_of_day_cpt(index)
    month = index.tz_convert(CPT).month.to_numpy()
    s = st.reindex(cols)
    rows = []
    for j in range(mu.shape[1]):
        sf = SF[:, j]
        big = np.abs(sf) >= SF_MASS_MIN
        mass = np.abs(sf) * big
        reg = pd.Series(mass, index=s["region"].fillna("Other").to_numpy()).groupby(level=0).sum()
        reg = (reg / reg.sum()).sort_values(ascending=False) if reg.sum() > 0 else reg
        act = mu[:, j] >= ACTIVE_USD
        prof = pd.Series(mu[:, j]).groupby(hours).mean()
        pole = int(np.argmax(np.abs(sf)))
        rows.append(dict(pattern=j, pole_station=cols[pole], pole_county=s["county"].iloc[pole], pole_sf=float(sf[pole]),
                         n_sf05=int((np.abs(sf) >= 0.5).sum()), n_export=int((sf >= SF_MASS_MIN).sum()), n_import=int((sf <= -SF_MASS_MIN).sum()),
                         export_share=float(np.clip(sf[big], 0, None).sum() / max(np.abs(sf[big]).sum(), 1e-9)) if big.any() else np.nan,
                         peak_usd=float(mu[:, j].max()), mean_usd=float(mu[:, j].mean()), sum_usd_h=float(mu[:, j].sum()),
                         active_share=float(act.mean()), active_jul=float(act[month == 7].mean()), active_aug=float(act[month == 8].mean()),
                         active_hours=int(act.sum()), peak_hour_cpt=int(prof.idxmax()), peak_hour_mean=float(prof.max()),
                         hour_profile=";".join(f"{v:.1f}" for v in prof.reindex(range(24)).fillna(0).to_numpy()),
                         regions=";".join(f"{k}:{v:.2f}" for k, v in reg.head(3).items()),
                         regional=bool((np.abs(sf) >= 0.5).sum() >= REGIONAL_MIN_STATIONS and mu[:, j].max() >= REGIONAL_MIN_PEAK)))
    return pd.DataFrame(rows)


def admissible_drivers(sfz_by_zone: dict, locs) -> list:
    """Phase 3b's physics rule (nodal_lanes._admissible): a hub or zone is a possible driver if its zone's |SF_zone| is at least half the largest over the eight zones;
    the sign is +1 (binds when the location is dear against HB_BUSAVG) if SF_zone < 0, else -1."""
    from .nodal_lanes import DRIVER_ZONE_REL_MIN, GEO_MIN_ZONE_SF, LOC_ZONE, ZONES

    zvec = np.array([sfz_by_zone.get(z, 0.0) for z in ZONES])
    zmax = float(np.abs(zvec).max())
    out = []
    for loc in locs:
        sfl = zvec[ZONES.index(LOC_ZONE[loc])]
        if abs(sfl) >= max(DRIVER_ZONE_REL_MIN * zmax, GEO_MIN_ZONE_SF):
            out.append((loc, 1 if sfl < 0 else -1))
    return out


def best_driver(mu_j: np.ndarray, spreads: pd.DataFrame, rows: np.ndarray, allowed: list | None = None) -> tuple:
    """(location, sign, correlation) of the signed spread (price - HB_BUSAVG) at one of the 13 locations that best tracks the pattern over `rows`; Phase 3b's driver form.
    `allowed` is a list of (location, sign) pairs (the physics rule); None = every location with both signs."""
    best = (None, 1, -9.0)
    for loc in spreads.columns:
        x = spreads[loc].to_numpy(float)[rows]
        for sg in (1, -1):
            if allowed is not None and (loc, sg) not in allowed:
                continue
            r = corr_safe(mu_j[rows], sg * x)
            if np.isfinite(r) and r > best[2]:
                best = (loc, sg, r)
    return best


def _imm_window_rent(key: str, market: str = "DA") -> float | None:
    """IMM rent over the 42-day window in $M: July plus the share of August inside the window (IMM_PATTERN_WINDOW). None if the report has no series."""
    v = IMM_2026.get((market, "rent_musd"), {}).get(key)
    if not v:
        return None
    return float(v[6] * IMM_PATTERN_WINDOW["JUL"] + v[7] * IMM_PATTERN_WINDOW["AUG"])


def target_evidence(t: dict, pt: pd.DataFrame, SF: np.ndarray, mu: np.ndarray, mu2: np.ndarray, pub: pd.DataFrame, hours2: np.ndarray,
                    cols: list, st: pd.DataFrame, B: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    """Evidence of every regional pattern for one target. Returns (table with one row per pattern, info about the target's own tests)."""
    s = st.reindex(cols)
    ends = line_ends_xy(t["cid"], st) if t["kind"] == "line" else None
    dom = domain_mask(s, t["domain"], ends)
    info = dict(n_domain=int(dom.sum()), n_bound_published=0, pocket_hours=np.nan, pocket_n_near=0, pocket_n_far=0, ends_found=len(ends) if ends else 0)
    p = None
    if t.get("published") and t["published"] in pub.columns:
        p = pub[t["published"]].to_numpy(float)
        info["n_bound_published"] = int((p > 0).sum())
        info["published_max"] = float(p.max())
    pocket = None
    if t["kind"] == "line":
        ps, n_near, n_far = pocket_series(B, st, ends or {})
        pocket = ps
        info.update(pocket_n_near=n_near, pocket_n_far=n_far, pocket_hours=int((np.abs(ps) > POCKET_BIG).sum()) if np.isfinite(ps).any() else np.nan,
                    pocket_min=float(np.nanmin(ps)) if np.isfinite(ps).any() else np.nan, pocket_max=float(np.nanmax(ps)) if np.isfinite(ps).any() else np.nan)
    jul_imm = imm_july_share(t["imm"]) if t.get("imm") else {}
    rows = []
    for j in range(SF.shape[1]):
        sf = SF[:, j]
        big = np.abs(sf) >= SF_MASS_MIN
        sign_ok = (sf > 0) if t["side"] == "export" else (sf < 0) if t["side"] == "import" else np.ones_like(sf, bool)
        mass = np.abs(sf) * big
        prec = float(mass[dom].sum() / mass.sum()) if mass.sum() > 0 else 0.0
        side = float((mass * sign_ok)[dom].sum() / mass[dom].sum()) if mass[dom].sum() > 0 else 0.0
        rec = float((big & sign_ok)[dom].sum() / dom.sum()) if dom.sum() > 0 else 0.0
        oop = corr_safe(deseasonalise(mu2[:, j], hours2), deseasonalise(p, hours2)) if (p is not None and info["n_bound_published"] >= 3) else np.nan
        pr = np.nan
        if pocket is not None and np.isfinite(pocket).any():
            sgn = -1.0 if t["side"] == "export" else 1.0 if t["side"] == "import" else 0.0
            cands = [corr_safe(mu[:, j], -pocket), corr_safe(mu[:, j], pocket)] if sgn == 0 else [corr_safe(mu[:, j], sgn * pocket)]
            pr = np.nanmax(cands) if np.isfinite(cands).any() else np.nan
        timing = np.nan
        if jul_imm:
            ref = jul_imm.get("DA", jul_imm.get("RT"))
            timing = float(pt.loc[j, "active_jul"] / ref) if ref and ref > 0 else np.nan
        rows.append(dict(pattern=j, regional=bool(pt.loc[j, "regional"]), precision=prec, recall=rec, n_cov=int((big & sign_ok)[dom].sum()), side_share=side, oop_r=oop, pocket_r=pr, timing_ratio=timing))
    ev = pd.DataFrame(rows)
    if t["kind"] == "gtc":
        ok = ev["regional"] & (ev["precision"] >= EVID_MIN_PRECISION) & (ev["side_share"] >= EVID_MIN_SIDE) & (ev["n_cov"] >= EVID_MIN_STATIONS)
        q = 0.6 + 0.4 * ev["oop_r"].fillna(0).clip(0, 1)
        ev["score"] = np.where(ok, ev["precision"] * ev["recall"] * ev["side_share"] * q, 0.0)
    else:
        have = np.isfinite(info.get("pocket_hours", np.nan)) and info["pocket_hours"] >= POCKET_MIN_HOURS
        ok = ev["pocket_r"].fillna(-1) >= 0.6
        cover = np.array([float((np.abs(SF[:, j]) >= SF_MASS_MIN)[dom].mean()) if dom.sum() else 0.0 for j in range(SF.shape[1])])
        ev["recall"] = cover
        ev["score"] = np.where(ok & (cover >= 0.5) & have, ev["pocket_r"].fillna(0) * cover, 0.0)
    return ev, info


def grade_target(t: dict, ev: pd.DataFrame, info: dict) -> dict:
    """Verdict for one target from its evidence table: status, grade, pattern and the reason in words."""
    best = ev.sort_values("score", ascending=False).iloc[0]
    nearest = int(ev.sort_values(["score", "precision"], ascending=False).iloc[0]["pattern"])
    out = dict(key=t["key"], cid=t["cid"], label=t["label"], kind=t["kind"], side=t["side"], group=t["group"], pattern=-1, grade="X", status="not_identified",
               nearest_pattern=nearest, reason="")
    if best["score"] <= 0:
        if t["kind"] == "line":
            ph = info.get("pocket_hours", np.nan)
            if np.isfinite(ph) and ph >= POCKET_MIN_HOURS:
                out["reason"] = f"a pocket of {int(ph)} hours beyond ${POCKET_BIG:.0f} exists but no regional pattern tracks it (r >= 0.6) with the pocket's stations covered"
                return out
            out["reason"] = (f"no pocket: the {info.get('pocket_n_near', 0)} priced stations within {POCKET_RADIUS_KM:.0f} km of its ends deviate from the surround by more than ${POCKET_BIG:.0f} "
                             f"in {0 if not np.isfinite(ph) else int(ph)} of 1,008 hours (needs {POCKET_MIN_HOURS})" if info.get("pocket_n_near", 0) else "its ends have no placed station with a price in the window")
        else:
            out["reason"] = "no regional pattern lies in its domain on its side"
        return out
    j = int(best["pattern"])
    out["pattern"] = j
    usable = (info.get("n_bound_published", 0) >= OOP_MIN_BOUND_HOURS) and np.isfinite(best["oop_r"])
    if t["kind"] == "line":
        out.update(grade="C", status="identified", reason=f"pocket of {int(info['pocket_hours'])} hours beyond ${POCKET_BIG:.0f}; pattern tracks the pocket at r={best['pocket_r']:.2f}")
        return out
    if usable:
        if best["oop_r"] >= OOP_GRADE_A:
            out.update(grade="A", status="identified", reason=f"Sep-Oct activity tracks its published shadow price at r={best['oop_r']:.2f} ({info['n_bound_published']} bound hours)")
        elif best["oop_r"] >= OOP_GRADE_B:
            out.update(grade="B", status="identified", reason=f"Sep-Oct activity tracks its published shadow price at r={best['oop_r']:.2f} ({info['n_bound_published']} bound hours)")
        else:
            out.update(grade="D", status="pattern_only", reason=f"geography and side fit, but the published Sep-Oct shadow price is not tracked (r={best['oop_r']:.2f}, {info['n_bound_published']} bound hours)")
    else:
        out.update(grade="C", status="identified", reason="geography and side fit; no published Sep-Oct series to test it" if not t.get("published") else
                   f"geography and side fit; too few Sep-Oct bound hours to test it ({info.get('n_bound_published', 0)})")
    return out


def analyse(k: int = NMF_K, verbose: bool = True) -> dict:
    """Everything task 2 needs, computed once: the panel, the NMF patterns, station / zone / hub shift factors (pole scale), the Sep-Oct recovery of each pattern's activity,
    the evidence table of every target and the verdicts. Returns a dict (see the keys at the end)."""
    import time

    t0 = time.time()
    panel = load_panel()
    sp = sp_table(panel.columns)
    B = basis_matrix(panel, sp)
    st = station_table()
    cols = list(B.columns)
    model = fit_nmf(B, k)
    mu = pattern_scores(model)
    Braw = B.to_numpy(float)
    SF, SE = station_sf(Braw, mu)
    zh = [c for c in panel.columns if c.startswith(("LZ_", "HB_")) and c != SYSTEM_REF]
    SFz, SEz = station_sf(panel[zh].sub(panel[SYSTEM_REF], axis=0).to_numpy(float), mu)
    pt = pattern_descriptors(mu, SF, cols, st, B.index)
    if verbose:
        print(f"  NMF k={k}: {len(cols)} stations, reconstruction R2 {model['recon_r2']:.3f}, {int(pt['regional'].sum())} regional patterns [{time.time() - t0:.0f}s]", flush=True)
    B2, w2 = load_sepoct_basis(sp, cols)
    mu2 = nnls_hourly(B2.to_numpy(float), SF)
    pub = load_published_mu(B2.index)
    hours2 = hour_of_day_cpt(B2.index)
    if verbose:
        print(f"  Sep-Oct recovery: {B2.shape[0]} hours, {int(B2.notna().all().sum())} stations fully priced [{time.time() - t0:.0f}s]", flush=True)
    evs, infos, verdicts = {}, {}, []
    for t in TARGETS:
        ev, info = target_evidence(t, pt, SF, mu, mu2, pub, hours2, cols, st, B)
        evs[t["key"]] = ev
        infos[t["key"]] = info
        verdicts.append(grade_target(t, ev, info))
    verdicts = pd.DataFrame(verdicts)
    spreads = pd.DataFrame({loc: panel[loc] - panel[SYSTEM_REF] for loc in DRIVER_LOCS})
    return dict(panel=panel, sp=sp, B=B, st=st, cols=cols, model=model, mu=mu, SF=SF, SE=SE, zh=zh, SFz=SFz, pt=pt, B2=B2, mu2=mu2, pub=pub, hours2=hours2,
                evidence=evs, info=infos, verdicts=verdicts, spreads=spreads)


# =================================================================================================================
# Naming, scale and drivers of the retained patterns (task 2b-c)
# =================================================================================================================
UNNAMED_REGIONS = ("Panhandle", "Rio Grande Valley", "Far West", "Laredo/South")   # REAL: the brief's regions without a candidate site in Phase 3b
UNNAMED_MIN_ACTIVE = 0.05                # ASSUMPTION: an unmatched regional pattern in those regions is kept if it binds (>= $1 at the pole) in at least this share of hours
UNNAMED_MIN_REGION_MASS = 0.5            # ASSUMPTION: ... and at least this share of its loading mass lies in those regions
COVERED_MIN_R = 0.7                      # ASSUMPTION: an unclaimed pattern whose recovered Sep-Oct activity tracks a published constraint at this deseasonalised r is "covered by" that constraint (Phase 2 already has its shift factors) and is not retained
WINDOW_DAYS = 42.0                       # REAL: delivery days 1 Jul - 11 Aug 2026
HOURS_PER_YEAR = 8760.0                  # REAL
SHADOW_CAP_GTC = 5251.0                  # REAL: IMM 2025 SOM p.62 interface shadow-price cap (same as nodal_lanes.SHADOW_CAP_DEFAULT["gtc"])
SHADOW_CAP_LINE = {345: 4500.0, 138: 3500.0, 69: 2800.0}   # REAL: same source, by voltage class
SEASON_FACTOR_DEFAULT = 1.0              # ASSUMPTION: a pattern with no IMM monthly series is taken to bind as much in an average month as in July-August (IMM 2026 shows the Valley and West export interfaces binding in every month)
INTERP_K = 4                             # ASSUMPTION: a substation without a priced resource node takes the inverse-distance-weighted shift factor of its INTERP_K nearest priced stations ...
INTERP_MAX_KM = 60.0                     # ASSUMPTION: ... within this distance (at least two), with a 5 km softening in the weight
INTERP_FAR_KM = 30.0                     # ASSUMPTION: nearest priced station farther than this is tagged `far`
ANCHOR_CONF = 0.5                        # REAL: phase 2 ANCHOR_CONF: a substation is placed if match_conf >= 0.5
SF_CSV_MIN = 0.05                        # ASSUMPTION: data/sf_structural.csv lists substation-constraint pairs with |SF| on the pole scale at least this large


def cid_for_pattern(claims: list, j: int, desc: pd.Series) -> tuple[str, str, str]:
    """(cid, grade, status) for a retained pattern from the targets that claim it.

    One claimant, or several in one `group` (they cannot be told apart): the claimant's constraint id if its grade is A, B or C. Several unlike claimants, or only a D claim:
    an unnamed id R1_<region>_<n> (the pattern is real, the name is not supported); the claimants are listed in `candidate_names`."""
    if not claims:
        reg = (desc["regions"].split(";")[0].split(":")[0] if desc["regions"] else "X").upper().replace(" ", "").replace("/", "")
        return f"R1_{reg}_P{j:02d}|BASE CASE", "U", "unnamed"
    order = {"A": 0, "B": 1, "C": 2, "D": 3}
    claims = sorted(claims, key=lambda c: (order.get(c["grade"], 9), TARGETS.index(next(t for t in TARGETS if t["key"] == c["key"]))))
    best = claims[0]
    groups = {c["group"] for c in claims}
    same_kind = len(claims) == 1 or (len(groups) == 1 and "" not in groups)
    if best["grade"] in ("A", "B", "C") and same_kind:
        return best["cid"], best["grade"], "identified"
    reg = (desc["regions"].split(";")[0].split(":")[0] if desc["regions"] else "X").upper().replace(" ", "").replace("/", "")
    return f"R1_{reg}_P{j:02d}|BASE CASE", best["grade"] if best["grade"] == "D" else "D", "pattern_only"


def retained_patterns(R: dict) -> pd.DataFrame:
    """One row per retained pattern: assigned cid, grade, claimants, domain, replay flag, and the numbers a constraint table needs."""
    v = R["verdicts"]
    pt = R["pt"]
    rows = []
    claimed = {}
    for _, r in v[v["pattern"] >= 0].iterrows():
        claimed.setdefault(int(r["pattern"]), []).append(r.to_dict())
    s = R["st"].reindex(R["cols"])
    bp = best_published_match(R).set_index("pattern")
    for j in range(len(pt)):
        d = pt.loc[j]
        if j in claimed:
            cid, grade, status = cid_for_pattern(claimed[j], j, d)
        else:
            if not d["regional"] or d["active_share"] < UNNAMED_MIN_ACTIVE:
                continue
            sf = R["SF"][:, j]
            mass = np.abs(sf) * (np.abs(sf) >= SF_MASS_MIN)
            inreg = mass[s["region"].isin(UNNAMED_REGIONS).to_numpy()].sum() / max(mass.sum(), 1e-9)
            if inreg < UNNAMED_MIN_REGION_MASS or bp.at[j, "best_published_r"] >= COVERED_MIN_R:
                continue
            cid, grade, status = cid_for_pattern([], j, d)
        claims = claimed.get(j, [])
        rows.append(dict(pattern=j, cid=cid, grade=grade, status=status, claimants=";".join(c["key"] for c in claims),
                         candidate_names=";".join(c["cid"] for c in claims), group=";".join(sorted({c["group"] for c in claims if c["group"]})),
                         mask_to_domain=bool(any(c["key"] == "PNHNDL" for c in claims) and status == "pattern_only")))
    out = pd.DataFrame(rows)
    out["replay"] = ~out["cid"].duplicated()
    return out


def scale_for_pattern(R: dict, j: int, claim_keys: list) -> dict:
    """The scale that turns the pole-normalised shift factor into a physical one, from the best available evidence.

    published : OLS slope of the pattern's recovered Sep-Oct activity (pole $/MWh) on the published shadow price of the claimed constraint, used when the
                deseasonalised correlation is at least OOP_GRADE_A and the slope is inside SCALE_SLOPE_RANGE; |SF_physical| = slope x |SF_pole| (the pole price is
                the shadow price times the pole's shift factor).
    rent      : s = limit x sum(mu_pole over the window) / IMM day-ahead rent over the same window; used when inside SCALE_SLOPE_RANGE and no published slope. A slope above 1 means the pattern is
                larger than the claimed constraint alone (it also carries co-binding constraints): the scale then stays at the pole.
    pole      : otherwise 1.0 (the largest loadings are scaled to |SF| = 1; the shadow-price scale is then the price at the pole, a lower bound on the true shadow price)."""
    out = dict(scale=1.0, method="pole", published_slope=np.nan, published_r=np.nan, rent_scale=np.nan, imm_window_rent_musd=np.nan, limit_mw=np.nan)
    cons = _read_csv("constraints.csv").set_index("cid")
    for key in claim_keys:
        t = next(x for x in TARGETS if x["key"] == key)
        p = None
        if t.get("published") and t["published"] in R["pub"].columns:
            p = R["pub"][t["published"]].to_numpy(float)
        if p is not None and (p > 0).sum() >= OOP_MIN_BOUND_HOURS:
            x = R["mu2"][:, j]
            r = corr_safe(deseasonalise(x, R["hours2"]), deseasonalise(p, R["hours2"]))
            A = np.vstack([p, np.ones_like(p)]).T
            slope = float(np.linalg.lstsq(A, x, rcond=None)[0][0])
            if np.isfinite(r) and (not np.isfinite(out["published_r"]) or r > out["published_r"]):
                out.update(published_slope=slope, published_r=r)
        rent = _imm_window_rent(t["imm"]) if t.get("imm") else None
        lim = float(cons.at[t["published"], "median_limit_mw"]) if t.get("published") in cons.index and pd.notna(cons.at[t["published"], "median_limit_mw"]) else np.nan
        if rent and np.isfinite(lim) and lim > 0:
            s = lim * float(R["mu"][:, j].sum()) / (rent * 1e6)
            out.update(rent_scale=s, imm_window_rent_musd=rent, limit_mw=lim)
        elif np.isfinite(lim):
            out["limit_mw"] = lim
    lo, hi = SCALE_SLOPE_RANGE
    if np.isfinite(out["published_r"]) and out["published_r"] >= OOP_GRADE_A and lo <= out["published_slope"] <= hi:
        out.update(scale=out["published_slope"], method="published")
    elif np.isfinite(out["rent_scale"]) and lo <= out["rent_scale"] <= hi:
        out.update(scale=out["rent_scale"], method="rent")
    return out


def _season_factor(claim_keys: list) -> tuple[float, str]:
    """Annualisation factor from the IMM: the median 2026 month (January-August; the median, so that one storm month does not set it) over the average of the window months (July, and 11/31 of August), on the first
    series that exists (DA rent, then RT rent, then DA hours, then RT intervals). SEASON_FACTOR_DEFAULT when there is none."""
    for key in claim_keys:
        t = next(x for x in TARGETS if x["key"] == key)
        k = t.get("imm")
        if not k:
            continue
        for market, metric in (("DA", "rent_musd"), ("RT", "rent_musd"), ("DA", "hours"), ("RT", "intervals")):
            v = IMM_2026.get((market, metric), {}).get(k)
            if v:
                ytd = float(np.median(v))
                win = (v[6] * IMM_PATTERN_WINDOW["JUL"] + v[7] * IMM_PATTERN_WINDOW["AUG"]) / (IMM_PATTERN_WINDOW["JUL"] + IMM_PATTERN_WINDOW["AUG"])
                if win > 0:
                    return float(np.clip(ytd / win, 0.25, 4.0)), f"IMM {market} {metric} {k}"
    return SEASON_FACTOR_DEFAULT, "default"


# =================================================================================================================
# Building the outputs (task 2c-d)
# =================================================================================================================
def physical_sf(R: dict, asg: pd.DataFrame, cal: dict) -> dict:
    """{pattern: dict(sf (N,), se (N,), sfz (Z,), masked (int))}: shift factors on the scale chosen for the pattern, clipped to +-SF_ABS_CAP.

    Shift factors are re-referenced to the median priced station of each pattern (offset): the regression is on the basis against HB_BUSAVG, which itself moves when a
    regional pattern binds, so every station would otherwise carry a small spurious loading of the opposite sign.
    A pattern whose name is not supported but whose Panhandle part is (mask_to_domain) loses loadings of |SF| >= SF_MASS_MIN outside its Panhandle / South Plains box:
    the import-side station KNAPP (the end of Scurry Chevron-Knapp, already replayed) and a Valley station would otherwise double count other constraints."""
    out = {}
    s = R["st"].reindex(R["cols"])
    for _, a in asg.iterrows():
        j = int(a["pattern"])
        sc = cal[j]["scale"]
        off = float(np.median(R["SF"][:, j]))               # re-reference: HB_BUSAVG moves with the pattern (it averages all buses), the typical station is the unconstrained bulk (SF = 0, as in phase 2)
        sf = R["SF"][:, j] - off
        masked = 0
        if a["mask_to_domain"]:
            t = next(x for x in TARGETS if x["key"] == "PNHNDL")
            dom = domain_mask(s, t["domain"])
            drop = (~dom) & (np.abs(sf) >= SF_MASS_MIN)
            masked = int(drop.sum())
            sf[drop] = 0.0
        sf = np.where(np.abs(sf) >= SF_FOOTPRINT_MIN, sf, 0.0)
        sf_pole = np.clip(sf, -SF_ABS_CAP, SF_ABS_CAP)
        sf = np.clip(sf * sc, -SF_ABS_CAP, SF_ABS_CAP)
        se = R["SE"][:, j] * sc
        sfz = np.clip((R["SFz"][:, j] - off) * sc, -SF_ABS_CAP, SF_ABS_CAP)
        out[j] = dict(sf=sf, sf_pole=sf_pole, se=se, sfz=sfz, sfz_pole=np.clip(R["SFz"][:, j] - off, -SF_ABS_CAP, SF_ABS_CAP), masked=masked, offset=off)
    return out


def weighted_median(v: np.ndarray, w: np.ndarray) -> np.ndarray:
    """Row-wise weighted median of v (n, k) with weights w (n, k) >= 0; rows with no weight give NaN. A single odd neighbour (a solar farm that behaves like the region it is not in)
    cannot move it, which an inverse-distance mean would let it do."""
    o = np.argsort(v, axis=1)
    vs = np.take_along_axis(v, o, 1)
    ws = np.take_along_axis(w, o, 1)
    cw = np.cumsum(ws, axis=1)
    half = cw[:, -1:] / 2.0
    idx = (cw >= half - 1e-12).argmax(1)
    out = vs[np.arange(len(v)), idx]
    return np.where(cw[:, -1] > 0, out, np.nan)


def interpolate_unpriced(R: dict, sf_by_pattern: dict) -> pd.DataFrame:
    """Inverse-distance-weighted MEDIAN of every retained pattern's shift factor over the INTERP_K nearest priced stations (within INTERP_MAX_KM, at least two), for the placed
    substations without a priced resource node.

    Returns a frame indexed by substation with columns sf_<pattern> (chosen scale), sfp_<pattern> (pole scale), dist_km (nearest priced station) and n_support."""
    st = R["st"]
    pr = st.reindex(R["cols"])
    ok_p = pr["lat"].notna() & pr["lon"].notna()
    P = pr[ok_p]
    idx_p = np.where(ok_p.to_numpy())[0]
    tgt = st[(~st.index.isin(R["cols"])) & st["lat"].notna() & st["lon"].notna() & (st["match_conf"] >= ANCHOR_CONF)]
    D = km_between(tgt["lat"].to_numpy(float)[:, None], tgt["lon"].to_numpy(float)[:, None], P["lat"].to_numpy(float)[None, :], P["lon"].to_numpy(float)[None, :])
    order = np.argsort(D, axis=1)[:, :INTERP_K]
    rows = np.arange(len(tgt))[:, None]
    dk = D[rows, order]
    valid = dk <= INTERP_MAX_KM
    w = np.where(valid, 1.0 / (dk + 5.0) ** 2, 0.0)
    n_sup = valid.sum(1)
    out = pd.DataFrame(index=tgt.index)
    out["dist_km"] = dk[:, 0]
    out["n_support"] = n_sup
    wsum = w.sum(1)
    for j, d in sf_by_pattern.items():
        out[f"sf_{j}"] = np.where(n_sup >= 2, weighted_median(d["sf"][idx_p][order], w), np.nan)
        out[f"sfp_{j}"] = np.where(n_sup >= 2, weighted_median(d["sf_pole"][idx_p][order], w), np.nan)
    return out


def sf_rows(R: dict, asg: pd.DataFrame, cal: dict, phys: dict) -> pd.DataFrame:
    """The long table behind sf_structural.parquet: (sub, bus, level, cid, sf, se, method, quality, dist_km, n_support) for stations (measured and interpolated), zones and hubs."""
    sp = R["sp"]
    rn = sp[(sp["sp_type"] == "resource_node") & sp["substation"].notna()].copy()
    rn["kv"] = pd.to_numeric(rn["kv"], errors="coerce")
    bus_of = rn.sort_values("kv", ascending=False).drop_duplicates("substation").set_index("substation")["bus"]
    st = R["st"]
    inter = interpolate_unpriced(R, phys)
    parts = []
    for _, a in asg.iterrows():
        j = int(a["pattern"])
        d = phys[j]
        tags = f"+scale_{cal[j]['method']}" + ("+shared" if a["group"] else "") + ("+masked" if d["masked"] else "")
        base = f"{a['grade']}{tags}"
        # measured stations
        sf = d["sf"]
        weak = np.abs(sf) < 2 * np.maximum(d["se"], 1e-9)
        m = pd.DataFrame({"sub": R["cols"], "level": "station", "sf": sf, "sf_pole": d["sf_pole"], "se": d["se"],
                          "quality": [base + ("+weak" if w else "") + ("" if (st.at[c, "match_conf"] if c in st.index else 0) >= ANCHOR_CONF else "+lowconf") for c, w in zip(R["cols"], weak)],
                          "dist_km": 0.0, "n_support": 1, "method": "dam60_factor"})
        m["bus"] = m["sub"].map(bus_of)
        # interpolated stations
        v = inter[f"sf_{j}"]
        ii = pd.DataFrame({"sub": inter.index, "level": "station", "sf": v.to_numpy(), "sf_pole": inter[f"sfp_{j}"].to_numpy(), "se": np.nan,
                           "quality": [base + "+interp" + ("+far" if dk > INTERP_FAR_KM else "") for dk in inter["dist_km"]],
                           "dist_km": inter["dist_km"].to_numpy(), "n_support": inter["n_support"].to_numpy(), "method": "dam60_factor_interp", "bus": None})
        ii = ii[ii["sf"].notna()]
        # zones and hubs
        zrows = []
        for c, val, valp in zip(R["zh"], d["sfz"], d["sfz_pole"]):
            zrows.append(dict(sub=c, level="zone" if c.startswith("LZ_") else "hub", sf=float(val), sf_pole=float(valp), se=np.nan, quality=base, dist_km=0.0, n_support=1, method="dam60_factor", bus=None))
        z = pd.DataFrame(zrows)
        p = pd.concat([m, ii, z], ignore_index=True)
        p["cid"] = a["cid"]
        p = p[(p["sf_pole"].abs() >= SF_NOISE_FLOOR) | (p["level"] != "station")]
        parts.append(p)
    out = pd.concat(parts, ignore_index=True)
    out["sf"] = out["sf"].astype("float32")
    out["sf_pole"] = out["sf_pole"].astype("float32")
    return out[["sub", "bus", "level", "cid", "sf", "sf_pole", "se", "method", "quality", "dist_km", "n_support"]]


def constraint_rows(R: dict, asg: pd.DataFrame, cal: dict, phys: dict, pm: pd.DataFrame | None = None) -> pd.DataFrame:
    """One row per retained pattern in the schema of data/constraint_synth.csv (what Phase 3b builds its synthetic shadow prices from), plus the evidence columns."""
    cons = _read_csv("constraints.csv").set_index("cid")
    mu = R["mu"]
    T = len(mu)
    n_train = T - HOLDOUT_DAYS * 24
    tr = np.arange(n_train)
    te = np.arange(n_train, T)
    rows = []
    pmi = pm.set_index("pattern") if pm is not None else None
    for _, a in asg.iterrows():
        j = int(a["pattern"])
        keys = [k for k in a["claimants"].split(";") if k]
        names = [next(t for t in TARGETS if t["key"] == k) for k in keys]
        c = cal[j]
        season, season_basis = _season_factor(keys)
        sum_mu = float(mu[:, j].sum())
        act_h = float((mu[:, j] >= ACTIVE_USD).sum())
        ann = HOURS_PER_YEAR / (WINDOW_DAYS * 24.0)
        sph = sum_mu / c["scale"] * ann * season
        H = max(act_h * ann * season, 1.0)
        pubs = [t["published"] for t in names if t.get("published") in cons.index] if a["status"] == "identified" else []
        crow = cons.loc[pubs[0]] if pubs else None
        lim = np.nan
        if pubs:
            lims = [float(cons.at[p, "median_limit_mw"]) for p in pubs if pd.notna(cons.at[p, "median_limit_mw"])]
            lim = max(lims) if lims else np.nan
        if not np.isfinite(lim) and a["status"] == "identified":
            ups = _read_csv("upgrades.csv")
            hit = ups[ups["main_cid"] == a["cid"]]
            if len(hit) and pd.notna(hit["limit_mw"].iloc[0]):
                lim = float(hit["limit_mw"].iloc[0])              # phase 4's limit (a class median where the window has none, see upgrades.csv limit_basis)
        cid_known = a["cid"] in cons.index
        crow2 = cons.loc[a["cid"]] if cid_known else None
        kind = "line" if (names and names[0]["kind"] == "line") else "gtc"
        kv = float(crow2["kv"]) if (crow2 is not None and pd.notna(crow2["kv"])) else np.nan
        cap = SHADOW_CAP_GTC if kind == "gtc" else SHADOW_CAP_LINE.get(345 if kv >= 345 else 138 if kv >= 138 else 69, 3500.0)
        from .nodal_lanes import MIN_DRIVER_CORR

        allowed = admissible_drivers(dict(zip(R["zh"], phys[j]["sfz"])), list(R["spreads"].columns))
        loc, sg, r_tr = best_driver(mu[:, j], R["spreads"], tr, allowed if allowed else None)
        loc_any, sg_any, r_any = best_driver(mu[:, j], R["spreads"], tr)
        r_te = corr_safe(mu[te, j], sg * R["spreads"][loc].to_numpy(float)[te]) if loc else np.nan
        drv_basis = "fit_dam60" if (loc and r_tr >= MIN_DRIVER_CORR) else "geo_needed"
        rows.append(dict(
            cid=a["cid"], constraint_name=a["cid"].split("|")[0], element=(names[0]["label"] if names and a["status"] == "identified" else f"pattern {j}: {R['pt'].at[j, 'regions']}"),
            element_type="gtc" if kind == "gtc" else "line", kv=kv, from_sub=(crow2["from_sub"] if crow2 is not None else R["pt"].at[j, "pole_station"]),
            to_sub=(crow2["to_sub"] if crow2 is not None else ""), median_limit_mw=lim, sf_use="dam60", sf_method="dam60_factor", sf_rt_shrink=np.nan, sf_dam_shrink=np.nan,
            priority_rank=np.nan, annual_rent_alloc_musd=(float(crow2["annual_rent_2025_musd"]) if (crow2 is not None and pd.notna(crow2["annual_rent_2025_musd"])) else np.nan),
            annual_binding_hours=(float(crow2["annual_binding_hours"]) if (crow2 is not None and pd.notna(crow2["annual_binding_hours"])) else np.nan),
            rt_hours_binding=0.0, dam_hours_binding=act_h, rt_mean_shadow=np.nan, dam_mean_shadow=(sum_mu / c["scale"] / act_h if act_h else np.nan),
            sph_usd_mw_yr=sph, sph_basis="dam60_window_x_season", H_hours=H, H_basis="dam60_active_hours_x_season", mean_shadow_usd_mwh=sph / H, cap_usd_mwh=cap,
            driver_location=loc, driver_sign=sg, driver_corr=r_tr, driver_basis=drv_basis, driver_n_binding=int(act_h), k_hours_per_musd=np.nan,
            upgrade_project_id=(crow2["upgrade_project_id"] if crow2 is not None else ""), upgrade_in_service_date=(crow2["upgrade_in_service_date"] if crow2 is not None else ""),
            upgrade_match_conf=(float(crow2["upgrade_match_conf"]) if (crow2 is not None and pd.notna(crow2["upgrade_match_conf"])) else np.nan),
            gtc=bool(kind == "gtc"),
            pattern=j, grade=a["grade"], status=a["status"], claimants=a["claimants"], candidate_names=a["candidate_names"], group=a["group"], replay=bool(a["replay"]),
            scale=c["scale"], scale_method=c["method"], scale_published_slope=c["published_slope"], scale_published_r=c["published_r"], scale_rent=c["rent_scale"],
            imm_window_rent_musd=c["imm_window_rent_musd"], season_factor=season, season_basis=season_basis,
            mu_window_sum_pole=sum_mu, mu_peak_pole=float(mu[:, j].max()), active_hours_window=act_h, active_share=float(act_h / T),
            imm_2026_da_rent_annualised_musd=(float(np.sum(IMM_2026[("DA", "rent_musd")][names[0]["imm"]]) * 12.0 / IMM_YTD_MONTHS) if (names and names[0].get("imm") and names[0]["imm"] in IMM_2026[("DA", "rent_musd")] and a["status"] == "identified") else np.nan),
            phase2_match_cid=(pmi.at[j, "phase2_match_cid"] if pmi is not None else ""), phase2_match_r=(pmi.at[j, "phase2_match_r"] if pmi is not None else np.nan),
            phase2_match_replayed=(bool(pmi.at[j, "phase2_match_replayed"]) if pmi is not None else False), phase2_matches=(pmi.at[j, "phase2_matches"] if pmi is not None else ""),
            replaces_cid=(pmi.at[j, "replaces_cid"] if pmi is not None else ""),
            driver_corr_holdout=r_te, driver_any_location=loc_any, driver_any_sign=sg_any, driver_any_corr=r_any, sph_pole_usd_mw_yr=sum_mu * ann * season,
            pole_station=R["pt"].at[j, "pole_station"], pole_county=R["pt"].at[j, "pole_county"], n_stations_sf05=int(R["pt"].at[j, "n_sf05"]),
            regions=R["pt"].at[j, "regions"], hour_profile=R["pt"].at[j, "hour_profile"]))
    return pd.DataFrame(rows)


# =================================================================================================================
# Validation (task 2d): hold-out, stability, out-of-period
# =================================================================================================================
def _match_patterns(SFa: np.ndarray, SFb: np.ndarray) -> np.ndarray:
    """For each column of SFa the best correlation with any column of SFb (rows = same stations)."""
    A = (SFa - SFa.mean(0)) / np.where(SFa.std(0) > 0, SFa.std(0), 1)
    Bm = (SFb - SFb.mean(0)) / np.where(SFb.std(0) > 0, SFb.std(0), 1)
    return (A.T @ Bm / len(A)).max(1)


def _r2_std(Y: np.ndarray, P: np.ndarray, scale: np.ndarray) -> float:
    Z, Q = Y / scale, P / scale
    ok = np.isfinite(Z)
    return float(1 - ((Z - Q)[ok] ** 2).sum() / (Z[ok] ** 2).sum())


K_SWEEP = (8, 16, 24, 32, 40)             # ASSUMPTION: numbers of components shown in the hold-out curve that justifies NMF_K


def scale_of(B: pd.DataFrame, cols: list) -> np.ndarray:
    return station_scale(B[cols].fillna(B[cols].median())).to_numpy()


def validation_table(R: dict, asg: pd.DataFrame, cal: dict, phys: dict, verbose: bool = True) -> pd.DataFrame:
    """Rows (cid, check, scope, metric, value, note) for every retained pattern. See notes/phase7-coverage.md for how to read each check."""
    rows = []

    def add(cid, check, scope, metric, value, note=""):
        rows.append(dict(cid=cid, check=check, scope=scope, metric=metric, value=(float(value) if value is not None and np.isfinite(value) else np.nan), note=note))

    B, cols, mu, SF = R["B"], R["cols"], R["mu"], R["SF"]
    T = len(B)
    n_hold = HOLDOUT_DAYS * 24
    tr = np.arange(T - n_hold)
    te = np.arange(T - n_hold, T)
    Braw = B.to_numpy(float)
    scale = station_scale(B.fillna(B.median())).to_numpy()
    # ---- (a) hold-out: patterns learned on the first 32 days, tested on the last 10
    mtr = fit_nmf(B, NMF_K, rows=tr)
    cols_tr = mtr["cols"]
    mu_tr = pattern_scores(mtr)
    SF_tr, _ = station_sf(Braw[tr], mu_tr)
    idx = [cols.index(c) for c in cols if c in cols_tr]
    best_tr = _match_patterns(SF[idx], SF_tr[idx])
    # scores on the test days from all stations (NNLS), reconstruction on all stations; and the spatial split (scores from half the stations, predict the other half)
    mu_te = nnls_hourly(Braw[te], SF_tr)
    pred_all = -(mu_te @ SF_tr.T)
    r2_test_all = _r2_std(Braw[te], pred_all, scale)
    rng = np.random.default_rng(7)
    perms = [rng.permutation(len(cols)) for _ in range(6)]
    def split_r2(drop_pattern: int | None) -> dict:
        res = []
        for pm in perms:
            a, b = pm[: len(cols) // 2], pm[len(cols) // 2:]
            keep = [i for i in range(SF_tr.shape[1]) if i != drop_pattern]
            m_a = nnls_hourly(Braw[te][:, a], SF_tr[a][:, keep])
            res.append((b, -(m_a @ SF_tr[b][:, keep].T)))
        return res
    base = split_r2(None)
    r2_split = float(np.mean([_r2_std(Braw[te][:, b], P, scale[b]) for b, P in base]))
    if verbose:
        print(f"  hold-out: patterns from days 1-32, test days 33-42: R2 all stations {r2_test_all:.3f}, half-stations-to-other-half {r2_split:.3f}", flush=True)
    # ---- (b) stability: refit with k-4 and k+4 on all 42 days
    stab = {}
    for kk in (NMF_K - 4, NMF_K + 4):
        mk = fit_nmf(B, kk)
        SFk, _ = station_sf(Braw, pattern_scores(mk))
        stab[kk] = _match_patterns(SF, SFk)
    # ---- (c) out-of-period: Sep-Oct
    B2 = R["B2"].to_numpy(float)
    mu2 = R["mu2"]
    SF2sup = {}
    pub = R["pub"]
    scale2 = scale
    r2_oop = _r2_std(B2, -(mu2 @ SF.T), scale2)
    if verbose:
        print(f"  Sep-Oct: all {mu2.shape[1]} patterns with loadings from 1 Jul-11 Aug explain R2 {r2_oop:.3f} of the standardised basis (activity re-estimated hour by hour)", flush=True)
    sf_sub = pd.read_parquet(SEP_OCT_SF_SUB, columns=["sub", "cid", "method", "sf"])
    for _, a in asg.iterrows():
        j = int(a["pattern"])
        cid = a["cid"]
        keys = [k for k in a["claimants"].split(";") if k]
        d = phys[j]
        add(cid, "holdout_split", "last 10 days", "pattern_match_r_train_only", best_tr[idx.index(j)] if j in idx else np.nan, "best correlation of this pattern's shift factors with any pattern fitted on days 1-32 only")
        add(cid, "stability", f"k={NMF_K - 4}", "pattern_match_r", stab[NMF_K - 4][j], "best correlation with any pattern of the refit with 4 fewer components")
        add(cid, "stability", f"k={NMF_K + 4}", "pattern_match_r", stab[NMF_K + 4][j], "best correlation with any pattern of the refit with 4 more components")
        fp = np.abs(SF[:, j]) >= SF_MASS_MIN
        if fp.sum() >= 2:
            # leave-the-pattern-out on the test days, at the pattern's own footprint (spatial split)
            if j in idx:
                jt = int(np.argmax([np.corrcoef(SF[idx, j], SF_tr[idx, q])[0, 1] if SF_tr[idx, q].std() > 0 else -1 for q in range(SF_tr.shape[1])]))
                dropped = split_r2(jt)
                with_r, wo_r = [], []
                for (b, P1), (b2, P2) in zip(base, dropped):
                    fb = np.isin(b, np.where(fp)[0])
                    if fb.sum() >= 2:
                        with_r.append(_r2_std(Braw[te][:, b][:, fb], P1[:, fb], scale[b][fb]))
                        wo_r.append(_r2_std(Braw[te][:, b][:, fb], P2[:, fb], scale[b][fb]))
                add(cid, "holdout_split", "footprint stations, last 10 days", "r2_with_pattern", np.mean(with_r) if with_r else np.nan, "R2 (standardised) at held-out footprint stations, scores from the other half of the stations")
                add(cid, "holdout_split", "footprint stations, last 10 days", "r2_without_pattern", np.mean(wo_r) if wo_r else np.nan, "the same with the matching pattern removed: the gap is what the pattern adds out of sample")
        for key in keys:
            t = next(x for x in TARGETS if x["key"] == key)
            pc = t.get("published")
            if pc and pc in pub.columns:
                p = pub[pc].to_numpy(float)
                nb = int((p > 0).sum())
                add(cid, "sepoct_published", pc, "bound_hours", nb, "hours of 744 in which the published day-ahead shadow price is positive")
                add(cid, "sepoct_published", pc, "corr_raw", corr_safe(mu2[:, j], p), "correlation of the pattern's recovered Sep-Oct activity with the published shadow price")
                add(cid, "sepoct_published", pc, "corr_deseasonalised", corr_safe(deseasonalise(mu2[:, j], R["hours2"]), deseasonalise(p, R["hours2"])), "same after removing the hour-of-day mean of both series")
                if nb >= 3:
                    # supervised shift factors: station basis regressed on the published shadow price alone (through the origin), signed to PLAN.md (SF = -slope)
                    num = np.nansum(B2 * p[:, None], axis=0)
                    sup = -num / float((p ** 2).sum())
                    sup = sup - np.nanmedian(sup)                    # same reference as the pattern: the median priced station
                    good = np.isfinite(sup)
                    add(cid, "sepoct_published", pc, "sf_corr_with_supervised", corr_safe(d["sf"][good], sup[good]), "correlation over stations of this pattern's shift factor with the one implied by the published series alone (confounded by co-binding constraints)")
                    big = good & (np.abs(sup) >= 0.1) & (d["sf_pole"] != 0)
                    add(cid, "sepoct_published", pc, "sf_sign_agreement", float((np.sign(d["sf"]) == np.sign(sup))[big].mean()) if big.any() else np.nan, "share of the pattern's footprint stations (nonzero shift factor) with |supervised SF| >= 0.1 whose sign agrees")
                    p2 = sf_sub[(sf_sub["cid"] == pc) & (sf_sub["method"] == "dam_reg")].set_index("sub")["sf"]
                    if len(p2):
                        v2 = p2.reindex(cols).to_numpy(float)
                        ok = np.isfinite(v2)
                        add(cid, "phase2_comparison", pc, "sf_corr_with_phase2_dam_reg", corr_safe(d["sf"][ok], v2[ok]), "correlation over stations with the Phase 2 day-ahead regression shift factor of the same constraint")
                        if t["kind"] == "gtc" and ok.sum() >= 10:
                            dm = domain_mask(R["st"].reindex(cols), t["domain"])
                            top = np.argsort(-np.where(ok, np.abs(v2), -1.0))[:10]
                            add(cid, "phase2_comparison", pc, "phase2_top10_in_domain_share", float(dm[top].mean()), "share of the 10 largest |SF| of the Phase 2 fit that lie in the target's domain (a coherent interface pattern is near 1)")
            if t.get("imm"):
                for mk, v in imm_july_share(t["imm"]).items():
                    add(cid, "imm_timing", f"{t['imm']} {mk}", "imm_july_share", v, "share of July the IMM reports the element bound (DA hours / 744, RT intervals / 8,928)")
                    add(cid, "imm_timing", f"{t['imm']} {mk}", "pattern_july_active_share", float(R["pt"].at[j, "active_jul"]), "share of July hours in which the pattern's pole shadow price is at least $1")
                rent = _imm_window_rent(t["imm"])
                if rent and np.isfinite(cal[j]["limit_mw"]):
                    add(cid, "imm_rent", t["imm"], "imm_da_rent_window_musd", rent, "IMM day-ahead rent for July plus 11/31 of August, $M")
                    add(cid, "imm_rent", t["imm"], "pole_rent_window_musd", cal[j]["limit_mw"] * float(mu[:, j].sum()) / 1e6, "limit x sum of the pattern's pole shadow price over the window, $M: the rent if the pole shift factor were 1")
                    add(cid, "imm_rent", t["imm"], "implied_pole_sf", cal[j]["rent_scale"], "pole rent / IMM rent: above 1.2 the pattern is larger than this constraint alone can explain")
        add(cid, "scale", cal[j]["method"], "scale_used", cal[j]["scale"], "factor applied to the pole-normalised shift factor")
        add(cid, "scale", "published", "slope", cal[j]["published_slope"], "OLS slope of the recovered Sep-Oct activity on the published shadow price")
        add(cid, "scale", "rent", "rent_scale", cal[j]["rent_scale"], "IMM-rent based scale")
        add(cid, "pattern", "window", "active_hours", float(R["pt"].at[j, "active_hours"]), "hours of 1,008 with the pole shadow price at least $1")
        add(cid, "pattern", "window", "stations_sf05", float(R["pt"].at[j, "n_sf05"]), "stations with |SF| >= 0.5 on the pole scale")
    # ---- (d) choice of k: hold-out curve and PCA variance
    Zc = (B.iloc[tr].fillna(B.iloc[tr].median())[cols_tr] / scale_of(B, cols_tr)).to_numpy(float)
    sv = np.linalg.svd(Zc, compute_uv=False) ** 2
    for kk in K_SWEEP:
        add("ALL", "k_sweep", f"k={kk}", "pca_cumulative_variance", float(sv[:kk].sum() / sv.sum()), "share of the standardised variance of days 1-32 in the first k principal components")
        mk = fit_nmf(B, kk, rows=tr)
        SFk, _ = station_sf(Braw[tr], pattern_scores(mk))
        reg_k = np.where([(np.abs(SFk[:, q]) >= 0.5).sum() >= REGIONAL_MIN_STATIONS for q in range(kk)])[0]
        for name, keep in (("all", np.arange(kk)), ("regional", reg_k)):
            if len(keep) == 0:
                continue
            vals = []
            for pm in perms[:4]:
                a, b = pm[: len(cols) // 2], pm[len(cols) // 2:]
                m_a = nnls_hourly(Braw[te][:, a], SFk[a][:, keep])
                vals.append(_r2_std(Braw[te][:, b], -(m_a @ SFk[b][:, keep].T), scale[b]))
            add("ALL", "k_sweep", f"k={kk}", f"r2_half_to_half_{name}_patterns", float(np.mean(vals)), "patterns fitted on days 1-32; activity re-estimated on days 33-42 from half the stations; prediction of the other half (standardised units)")
        if verbose:
            print(f"  k={kk}: PCA {sv[:kk].sum() / sv.sum():.3f}, hold-out half-to-half R2 (regional patterns) {vals[-1] if len(reg_k) else float('nan'):.3f}", flush=True)
    # ---- (e) interpolation to unpriced substations: leave-one-out over the priced stations, the same rule as interpolate_unpriced
    st_ = R["st"].reindex(cols)
    okp = (st_["lat"].notna() & st_["lon"].notna()).to_numpy()
    ip = np.where(okp)[0]
    D = km_between(st_["lat"].to_numpy(float)[ip][:, None], st_["lon"].to_numpy(float)[ip][:, None], st_["lat"].to_numpy(float)[ip][None, :], st_["lon"].to_numpy(float)[ip][None, :])
    np.fill_diagonal(D, np.inf)
    order = np.argsort(D, axis=1)[:, :INTERP_K]
    dk = D[np.arange(len(ip))[:, None], order]
    w = np.where(dk <= INTERP_MAX_KM, 1.0 / (dk + 5.0) ** 2, 0.0)
    enough = (dk <= INTERP_MAX_KM).sum(1) >= 2
    for _, a in asg.iterrows():
        j = int(a["pattern"])
        v = phys[j]["sf_pole"][ip]
        pred = weighted_median(v[order], w)
        m = enough
        fp = m & (np.abs(v) >= SF_FOOTPRINT_MIN)
        add(a["cid"], "interp_loo", "priced stations", "corr_pole_scale", corr_safe(pred[m], v[m]), "leave-one-out: the shift factor of each priced station predicted (weighted median) from its nearest priced neighbours (>= 2 within 60 km), correlation over stations")
        add(a["cid"], "interp_loo", "priced stations", "mae_pole_scale", float(np.abs(pred[m] - v[m]).mean()), "mean absolute error of the interpolation, pole-normalised shift-factor units")
        add(a["cid"], "interp_loo", "footprint stations", "sign_agreement", float((np.sign(pred[fp]) == np.sign(v[fp])).mean()) if fp.any() else np.nan, "share of footprint stations (|SF| >= 0.2) whose interpolated shift factor has the right sign")
        add(a["cid"], "interp_loo", "footprint stations", "n_evaluated", float(fp.sum()), "stations")
    # ---- (f) the lane formula with the retained patterns and their real activity: how much of the node-minus-zone basis of 1 Jul-11 Aug they reproduce
    zone_of = R["st"].reindex(cols)["zone"].to_numpy()
    panel = R["panel"]
    ref_p = panel[SYSTEM_REF].to_numpy(float)
    zcols = {z: i for i, z in enumerate(R["zh"])}
    pred_all = np.zeros_like(Braw)
    obs_vs_zone = np.full_like(Braw, np.nan)
    for n_, (c_, z_) in enumerate(zip(cols, zone_of)):
        if z_ in panel.columns:
            obs_vs_zone[:, n_] = Braw[:, n_] - (panel[z_].to_numpy(float) - ref_p)
    for _, a in asg.iterrows():
        j = int(a["pattern"])
        sfz_pole = phys[j]["sfz_pole"]
        dsf = phys[j]["sf_pole"].copy()
        for n_, z_ in enumerate(zone_of):
            dsf[n_] -= sfz_pole[zcols[z_]] if z_ in zcols else 0.0
        pred_all += -np.outer(mu[:, j], dsf)
    regs = R["st"].reindex(cols)["region"].to_numpy()
    for rg in REGIONS:
        m = (regs == rg) & np.isfinite(obs_vs_zone).all(0)
        if m.sum() < 3:
            continue
        o, q = obs_vs_zone[:, m], pred_all[:, m]
        add("ALL", "lane_formula", rg, "corr_node_minus_zone", corr_safe(o.ravel(), q.ravel()), "pooled correlation over the region's priced stations x 1,008 hours: observed node minus zone price vs -sum (SF - SF_zone) x pole shadow price of the retained patterns (in sample)")
        add("ALL", "lane_formula", rg, "mae_usd_mwh", float(np.abs(o - q).mean()), "mean absolute error, $/MWh")
        add("ALL", "lane_formula", rg, "mae_if_no_basis_usd_mwh", float(np.abs(o).mean()), "the same if the node traded at its zone price")
        add("ALL", "lane_formula", rg, "n_stations", float(m.sum()), "priced stations")
    add("ALL", "holdout_split", "last 10 days", "r2_all_stations", r2_test_all, "patterns fitted on days 1-32, activity re-estimated on days 33-42 from all stations (standardised units)")
    add("ALL", "holdout_split", "last 10 days", "r2_half_to_half", r2_split, "activity from half of the stations, prediction of the other half")
    add("ALL", "sepoct_recovery", "10 Sep-10 Oct", "r2_all_patterns", r2_oop, "loadings from 1 Jul-11 Aug, activity re-estimated hour by hour (standardised units)")
    add("ALL", "fit", "1 Jul-11 Aug", "nmf_reconstruction_r2", R["model"]["recon_r2"], "of the sign-split thresholded standardised basis")
    return pd.DataFrame(rows)


# =================================================================================================================
# Writing the sf-structural products
# =================================================================================================================
SF_STRUCTURAL_PARQUET = DERIVED / "sf_structural.parquet"
SF_SUB_PLUS = CACHE / "sf_sub_plus_structural.parquet"
ZONE_SF_PLUS = CACHE / "zone_sf_plus_structural.parquet"
ZONE_SF_FILE = DERIVED / "nodal_lanes_zone_sf.parquet"

SRC60 = "ERCOT 60-day DAM disclosure (NP3-966-ER) via the dispatch lake; gridsite/coverage.py"

TARGET_LABELS = [
    ("target", "DERIVED", "TARGETS in gridsite/coverage.py (the brief's list)", "short key"),
    ("cid", "REAL", "data/constraints.csv / IMM", "constraint id of the target as in data/constraints.csv"),
    ("label", "REAL", "IMM / ERCOT", "name"),
    ("kind", "ASSUMPTION", "TARGETS", "gtc (interface: tested against a region) or line (tested for a pocket around its two ends)"),
    ("side", "ASSUMPTION", "TARGETS", "export (SF > 0, price falls when it binds), import (SF < 0) or either"),
    ("status", "DERIVED", "grade_target() in gridsite/coverage.py", "identified (A, B or C), pattern_only (D: a pattern fits the geography but the name is not supported) or not_identified (X)"),
    ("grade", "DERIVED", "grade_target()", "A: published Sep-Oct shadow price tracked at r >= 0.6; B: 0.4-0.6; C: geography and side fit, no usable published test; D: published series contradicts; X: not identified"),
    ("uses_cid", "DERIVED", "retained_patterns()", "the constraint id under which its pattern is stored in sf_structural.parquet (an R1_ id when the name is not supported); blank if none"),
    ("pattern", "DERIVED", "NMF component number", "index of the pattern (see data/sf_structural_components.csv); -1 if none"),
    ("nearest_pattern", "DERIVED", "evidence table", "the pattern with the highest evidence score even when it fails the thresholds"),
    ("n_domain", "DERIVED", "domain_mask()", "priced stations in the target's domain"),
    ("precision", "DERIVED", "share of the pattern's |SF| >= 0.3 mass inside the domain", "0 to 1"),
    ("recall", "DERIVED", "share of the domain's stations with |SF| >= 0.3 on the right side", "0 to 1"),
    ("n_cov", "DERIVED", "count", "domain stations with |SF| >= 0.3 on the right side"),
    ("side_share", "DERIVED", "share of in-domain mass on the target's side", "0 to 1"),
    ("oop_r", "DERIVED", "deseasonalised correlation, Sep-Oct recovered pattern activity vs published shadow price (REAL)", "blank if the constraint bound too few hours in 10 Sep-10 Oct"),
    ("n_bound_published", "REAL", "NP4-191-CD", "hours of 744 in which the target's published shadow price is positive"),
    ("pocket_hours", "DERIVED", "pocket_series()", "line targets: hours with the pocket's median basis more than $10 from the surround's"),
    ("pocket_r", "DERIVED", "correlation of the pattern's activity with the pocket series", "line targets"),
    ("imm_july_share", "REAL", "IMM monthly report, July 2026", "share of July the element was binding (DA hours / 744 else RT intervals / 8,928); blank if not in the report"),
    ("pattern_july_active", "DERIVED", "share of July hours with the pole shadow price >= $1", ""),
    ("reason", "DERIVED", "grade_target()", "the evidence in words"),
]

COMPONENT_LABELS = [
    ("pattern", "DERIVED", "NMF component of the sign-split basis matrix", "0 to k-1"),
    ("retained", "DERIVED", "retained_patterns()", "True if the pattern is in sf_structural.parquet"),
    ("cid", "DERIVED", "retained_patterns()", "constraint id it is stored under; blank if not retained"),
    ("grade", "DERIVED", "grade_target()", "A, B, C, D (name not supported) or U (unnamed regional pattern); blank if not retained"),
    ("claimants", "DERIVED", "TARGETS", "targets whose evidence picked this pattern"),
    ("pole_station", "DERIVED", "largest |SF|", "substation"),
    ("pole_county", "DERIVED", "data/substations.csv", ""),
    ("pole_sf", "DERIVED", "pole-normalised regression SF", "sign: + export side"),
    ("n_sf05", "DERIVED", "count", "stations with |SF| >= 0.5"),
    ("n_export", "DERIVED", "count", "stations with SF >= 0.3"),
    ("n_import", "DERIVED", "count", "stations with SF <= -0.3"),
    ("export_share", "DERIVED", "share of |SF| >= 0.3 mass on the export side", "0 to 1"),
    ("peak_usd", "DERIVED", "pole shadow price scale", "$/MWh, maximum over the 1,008 hours (the price at the pole station, not a published shadow price)"),
    ("mean_usd", "DERIVED", "", "$/MWh mean over 1,008 hours"),
    ("sum_usd_h", "DERIVED", "", "$ x hours over the window: the pattern's window shadow-price-hours at the pole"),
    ("active_share", "DERIVED", "share of hours with pole shadow price >= $1", "0 to 1"),
    ("active_jul", "DERIVED", "same, July", ""),
    ("active_aug", "DERIVED", "same, 1-11 August", ""),
    ("active_hours", "DERIVED", "hours", "of 1,008"),
    ("peak_hour_cpt", "DERIVED", "hour-beginning CPT of the highest mean", "0 to 23"),
    ("peak_hour_mean", "DERIVED", "", "$/MWh"),
    ("hour_profile", "DERIVED", "mean pole shadow price by hour-beginning CPT 0..23", "';'-separated"),
    ("regions", "DERIVED", "share of |SF| >= 0.3 mass by region tag", "top three"),
    ("regional", "DERIVED", "n_sf05 >= 4 and peak >= $5", "False = a local pocket on one to three stations"),
    ("best_published", "REAL", "NP4-191-CD constraint whose published Sep-Oct shadow price the pattern's recovered activity tracks best (deseasonalised r)", "among constraints binding at least 20 hours"),
    ("best_published_r", "DERIVED", "deseasonalised correlation", ""),
    ("best_published_element", "DERIVED", "data/constraints.csv", "its element name"),
]

CONSTRAINT_LABELS = [
    ("cid", "DERIVED", "retained_patterns()", "CONSTRAINT_NAME|CONTINGENCY_NAME; an R1_ id when the pattern is real but its name is not supported"),
    ("constraint_name", "DERIVED", "cid", ""),
    ("element", "DERIVED", "TARGETS label or pattern footprint", ""),
    ("element_type", "DERIVED", "gtc or line", ""),
    ("kv", "REAL", "data/constraints.csv", "blank for interfaces"),
    ("from_sub", "DERIVED", "data/constraints.csv, else the pattern's pole station", ""),
    ("to_sub", "DERIVED", "data/constraints.csv", ""),
    ("median_limit_mw", "REAL", "data/constraints.csv (NP4-191-CD limit, 10 Sep-10 Oct median); else data/upgrades.csv limit_mw (a class median for lines with no window data)", "blank for unnamed patterns; the larger limit of the claimed constraints for a shared pattern"),
    ("sf_use", "DERIVED", "this module", "dam60 = shift factors from the 60-day factor model"),
    ("sf_method", "DERIVED", "this module", "dam60_factor: the method tag of the rows in sf_structural.parquet"),
    ("sf_rt_shrink", "DERIVED", "n/a", "blank"),
    ("sf_dam_shrink", "DERIVED", "n/a", "blank"),
    ("priority_rank", "DERIVED", "n/a", "blank"),
    ("annual_rent_alloc_musd", "REAL", "IMM 2025 (data/constraints.csv)", "2025 RT rent of the named element, $M"),
    ("annual_binding_hours", "REAL", "IMM 2025 (data/constraints.csv)", ""),
    ("rt_hours_binding", "DERIVED", "n/a", "0: no real-time data in this window"),
    ("dam_hours_binding", "DERIVED", "hours with pole shadow price >= $1", "in the 1,008-hour window"),
    ("rt_mean_shadow", "DERIVED", "n/a", "blank"),
    ("dam_mean_shadow", "DERIVED", "sum of the pole shadow price / scale / active hours", "$/MWh while active, on the scale of sf"),
    ("sph_usd_mw_yr", "DERIVED", "window sum of the pole shadow price / scale x 8,760 / 1,008 x season factor", "annual shadow-price-hours on the same scale as the shift factors in sf_structural.parquet, $/MW-yr"),
    ("sph_basis", "DERIVED", "this module", ""),
    ("H_hours", "DERIVED", "active hours x 8,760 / 1,008 x season factor", "binding hours a year"),
    ("H_basis", "DERIVED", "this module", ""),
    ("mean_shadow_usd_mwh", "DERIVED", "sph / H", ""),
    ("cap_usd_mwh", "REAL", "IMM 2025 SOM p.62 caps", "interface 5,251; line by kV"),
    ("driver_location", "DERIVED", "best of the 13 hub / zone spreads (price minus HB_BUSAVG), fitted on days 1-32", "same form as Phase 3b"),
    ("driver_sign", "DERIVED", "sign of the best correlation", "+1: binds when the location is dear against HB_BUSAVG"),
    ("driver_corr", "DERIVED", "Pearson on days 1-32", ""),
    ("driver_basis", "DERIVED", "fit_dam60", "fitted on the 60-day day-ahead window"),
    ("driver_n_binding", "DERIVED", "active hours", ""),
    ("k_hours_per_musd", "DERIVED", "n/a", "blank"),
    ("upgrade_project_id", "DERIVED", "data/constraints.csv", ""),
    ("upgrade_in_service_date", "DERIVED", "data/constraints.csv", ""),
    ("upgrade_match_conf", "DERIVED", "data/constraints.csv", ""),
    ("gtc", "DERIVED", "", ""),
    ("pattern", "DERIVED", "NMF component", ""),
    ("grade", "DERIVED", "grade_target()", "A, B, C, D or U (see sf_structural_targets.csv)"),
    ("status", "DERIVED", "retained_patterns()", "identified, pattern_only or unnamed"),
    ("claimants", "DERIVED", "TARGETS", "targets whose evidence picked this pattern"),
    ("candidate_names", "DERIVED", "TARGETS", "constraint ids the pattern may stand for"),
    ("group", "DERIVED", "TARGETS", "targets that cannot be told apart share one pattern"),
    ("replay", "DERIVED", "this module", "True: feed this row to Phase 3b"),
    ("scale", "DERIVED", "scale_for_pattern()", "factor applied to the pole-normalised SF (largest loadings = 1) to get sf in sf_structural.parquet"),
    ("scale_method", "DERIVED", "scale_for_pattern()", "published (slope on the published Sep-Oct shadow price), rent (IMM rent / limit) or pole (no physical anchor: SF is pole-normalised)"),
    ("scale_published_slope", "DERIVED", "OLS slope", "of the recovered Sep-Oct activity on the published shadow price"),
    ("scale_published_r", "DERIVED", "deseasonalised correlation", ""),
    ("scale_rent", "DERIVED", "limit x window sum / IMM window rent", "implied pole shift factor"),
    ("imm_window_rent_musd", "REAL", "IMM monthly reports", "day-ahead rent, July plus 11/31 of August, $M"),
    ("season_factor", "ASSUMPTION", "IMM 2026 year-to-date monthly average over the window months, else 1.0", "annualises the window; 1.0 = no IMM series"),
    ("season_basis", "DERIVED", "this module", ""),
    ("mu_window_sum_pole", "DERIVED", "sum over 1,008 hours of the pole shadow price", "$ x hours, pole scale"),
    ("mu_peak_pole", "DERIVED", "maximum", "$/MWh"),
    ("active_hours_window", "DERIVED", "hours with pole shadow price >= $1", ""),
    ("active_share", "DERIVED", "", "0 to 1"),
    ("driver_corr_holdout", "DERIVED", "Pearson on days 33-42", "the same location and sign as driver_corr"),
    ("imm_2026_da_rent_annualised_musd", "REAL", "IMM monthly reports 2026, January-August day-ahead rent x 12 / 8", "$M a year; blank for patterns whose name is not supported or that have no IMM rent series"),
    ("phase2_match_cid", "DERIVED", "phase 2 regression shift factors (Sep-Oct windows)", "the fitted constraint whose shift factors over the priced stations correlate best with this pattern's"),
    ("phase2_match_r", "DERIVED", "Pearson over the priced stations", "same-scale correlation: a pattern identical in shape to a phase 2 constraint scores near 1"),
    ("phase2_match_replayed", "DERIVED", "data/constraint_synth.csv", "True if that constraint is one of the 260 replayed in Phase 3b"),
    ("phase2_matches", "DERIVED", "top three", "cid:r"),
    ("replaces_cid", "DERIVED", "phase2_match_r >= 0.90 and replayed and not the same cid", "Phase 3b already replays a constraint with the same footprint: drop that row when you add this one, or the congestion is counted twice"),
    ("driver_any_location", "DERIVED", "best of the 13 spreads without the physics restriction", "for comparison"),
    ("driver_any_sign", "DERIVED", "", ""),
    ("driver_any_corr", "DERIVED", "Pearson on days 1-32", ""),
    ("sph_pole_usd_mw_yr", "DERIVED", "window sum x 8,760 / 1,008 x season factor, pole scale", "annual shadow-price-hours on the pole-normalised scale (largest loadings = 1): pair it with sf_pole, not sf"),
    ("pole_station", "DERIVED", "largest |SF|", ""),
    ("pole_county", "DERIVED", "", ""),
    ("n_stations_sf05", "DERIVED", "count", "stations with |SF| >= 0.5 on the pole scale"),
    ("regions", "DERIVED", "", "top three region tags by loading mass"),
    ("hour_profile", "DERIVED", "mean pole shadow price by hour-beginning CPT", "';'-separated, hours 0..23"),
]

SF_CSV_LABELS = [
    ("sub", "REAL", "data/substations.csv (NP4-160)", "substation code"),
    ("cid", "DERIVED", "sf_structural_constraints.csv", ""),
    ("sf", "DERIVED", SRC60, "shift factor, PLAN.md sign: negative = import side (price rises when it binds), positive = export side"),
    ("sf_pole", "DERIVED", SRC60, "the same on the pole-normalised scale (largest loadings = 1); pair with sph_pole_usd_mw_yr of sf_structural_constraints.csv"),
    ("side", "DERIVED", "sign of sf_pole", "import (sf < 0: price rises when it binds, a battery sells) or export (sf > 0: price falls, a battery charges)"),
    ("method", "DERIVED", "this module", "dam60_factor (priced resource node at the substation) or dam60_factor_interp (inverse-distance interpolation)"),
    ("quality", "DERIVED", "this module", "grade (A-D, U) + scale tag (+scale_published, +scale_rent, +scale_pole) + +shared +masked +weak +lowconf +interp +far"),
    ("dist_km", "DERIVED", "great-circle km to the nearest priced station", "0 for a priced substation"),
    ("n_support", "DERIVED", "count", "priced stations behind the value (1 if measured)"),
    ("replay", "DERIVED", "sf_structural_constraints.csv", "True if the constraint's row is to be replayed"),
    ("zone", "REAL", "data/settlement_points.csv", "NOIE zone first, else load zone"),
    ("region", "ASSUMPTION", "region_of()", ""),
    ("county", "DERIVED", "data/substations.csv", ""),
    ("lat", "DERIVED", "data/substations.csv", ""),
    ("lon", "DERIVED", "data/substations.csv", ""),
    ("kv_max", "REAL", "data/substations.csv", ""),
    ("match_conf", "DERIVED", "data/substations.csv", "placement confidence"),
]

VALIDATION_LABELS = [
    ("cid", "DERIVED", "sf_structural_constraints.csv", "ALL = whole-model rows"),
    ("check", "DERIVED", "this module", "holdout_split, stability, k_sweep, interp_loo, lane_formula, sepoct_published, sepoct_recovery, phase2_comparison, imm_timing, imm_rent, scale, pattern, fit"),
    ("scope", "DERIVED", "this module", "what the number is computed on"),
    ("metric", "DERIVED", "this module", ""),
    ("value", "DERIVED", "REAL prices (60-day and Sep-Oct DAM), REAL published shadow prices and IMM reports", "see note"),
    ("note", "DERIVED", "this module", "how to read the number"),
]


def _write(df: pd.DataFrame, name: str, labels: list) -> Path:
    out = DATA / name
    df.to_csv(out, index=False)
    write_labels(out, labels, df)
    return out


def best_published_match(R: dict, min_bound: int = OOP_MIN_BOUND_HOURS) -> pd.DataFrame:
    """For each pattern the published Sep-Oct constraint (>= min_bound bound hours) whose shadow price its recovered activity tracks best (deseasonalised r)."""
    pub = R["pub"]
    nb = (pub > 0).sum()
    cand = list(nb[nb >= min_bound].index)
    h = R["hours2"]
    P = np.stack([deseasonalise(pub[c].to_numpy(float), h) for c in cand], axis=1)
    P = (P - P.mean(0)) / np.where(P.std(0) > 0, P.std(0), 1)
    out = []
    cons = _read_csv("constraints.csv").set_index("cid")
    for j in range(R["mu2"].shape[1]):
        x = deseasonalise(R["mu2"][:, j], h)
        if x.std() == 0:
            out.append(dict(pattern=j, best_published="", best_published_r=np.nan, best_published_element=""))
            continue
        r = ((x - x.mean()) / x.std()) @ P / len(x)
        a = int(np.argmax(r))
        out.append(dict(pattern=j, best_published=cand[a], best_published_r=float(r[a]), best_published_element=str(cons.at[cand[a], "element"]) if cand[a] in cons.index else ""))
    return pd.DataFrame(out)


def targets_table(R: dict, asg: pd.DataFrame) -> pd.DataFrame:
    v = R["verdicts"].copy()
    cid_of = dict(zip(asg["pattern"], asg["cid"]))
    rows = []
    for _, r in v.iterrows():
        ev = R["evidence"][r["key"]]
        info = R["info"][r["key"]]
        j = int(r["pattern"]) if r["pattern"] >= 0 else int(r["nearest_pattern"])
        e = ev[ev["pattern"] == j].iloc[0]
        t = next(x for x in TARGETS if x["key"] == r["key"])
        jul = imm_july_share(t["imm"]) if t.get("imm") else {}
        rows.append(dict(target=r["key"], cid=r["cid"], label=r["label"], kind=r["kind"], side=r["side"], status=r["status"], grade=r["grade"],
                         uses_cid=cid_of.get(int(r["pattern"]), "") if r["pattern"] >= 0 else "", pattern=int(r["pattern"]), nearest_pattern=int(r["nearest_pattern"]),
                         n_domain=info["n_domain"], precision=e["precision"], recall=e["recall"], n_cov=e["n_cov"], side_share=e["side_share"], oop_r=e["oop_r"],
                         n_bound_published=info["n_bound_published"], pocket_hours=info.get("pocket_hours", np.nan), pocket_r=e["pocket_r"],
                         imm_july_share=jul.get("DA", jul.get("RT", np.nan)), pattern_july_active=float(R["pt"].at[j, "active_jul"]), reason=r["reason"]))
    out = pd.DataFrame(rows)
    num = out.select_dtypes("number").columns
    out[num] = out[num].round(3)
    return out


def components_table(R: dict, asg: pd.DataFrame) -> pd.DataFrame:
    pt = R["pt"].copy()
    bp = best_published_match(R)
    pt = pt.merge(bp, on="pattern")
    m = asg.set_index("pattern")
    pt["retained"] = pt["pattern"].isin(m.index)
    pt["cid"] = pt["pattern"].map(m["cid"]).fillna("")
    pt["grade"] = pt["pattern"].map(m["grade"]).fillna("")
    pt["claimants"] = pt["pattern"].map(m["claimants"]).fillna("")
    cols = ["pattern", "retained", "cid", "grade", "claimants", "pole_station", "pole_county", "pole_sf", "n_sf05", "n_export", "n_import", "export_share", "peak_usd", "mean_usd",
            "sum_usd_h", "active_share", "active_jul", "active_aug", "active_hours", "peak_hour_cpt", "peak_hour_mean", "hour_profile", "regions", "regional",
            "best_published", "best_published_r", "best_published_element"]
    out = pt[cols]
    num = out.select_dtypes("number").columns
    out = out.copy()
    out[num] = out[num].round(3)
    return out


def run_sf_structural(*args) -> dict:
    """The whole task-2 pipeline. Options: skip-validation (no refits; the validation file is then not rewritten)."""
    import time

    t0 = time.time()
    R = analyse()
    asg = retained_patterns(R)
    cal = {int(a["pattern"]): scale_for_pattern(R, int(a["pattern"]), [k for k in a["claimants"].split(";") if k]) for _, a in asg.iterrows()}
    phys = physical_sf(R, asg, cal)
    rows = sf_rows(R, asg, cal, phys)
    CACHE.mkdir(parents=True, exist_ok=True)
    rows.to_parquet(SF_STRUCTURAL_PARQUET, index=False)
    R["st"].reindex(R["cols"])[["sub", "lat", "lon", "county", "region", "match_conf"]].to_parquet(PRICED_STATIONS, index=False)
    pm = phase2_matches(R, asg)
    cons = constraint_rows(R, asg, cal, phys, pm)
    num = cons.select_dtypes("number").columns
    cons[num] = cons[num].round(4)
    _write(cons, "sf_structural_constraints.csv", CONSTRAINT_LABELS)
    tg = targets_table(R, asg)
    _write(tg, "sf_structural_targets.csv", TARGET_LABELS)
    comp = components_table(R, asg)
    _write(comp, "sf_structural_components.csv", COMPONENT_LABELS)
    # substation-level summary
    st = R["st"]
    sub = rows[(rows["level"] == "station") & (rows["sf_pole"].abs() >= SF_CSV_MIN)].copy()
    sub["side"] = np.where(sub["sf_pole"] < 0, "import", "export")
    sub["replay"] = sub["cid"].map(dict(zip(cons["cid"], cons["replay"])))
    for col in ("zone", "region", "county", "lat", "lon", "kv_max", "match_conf"):
        sub[col] = sub["sub"].map(st[col])
    sub = sub[["sub", "cid", "sf", "sf_pole", "side", "method", "quality", "dist_km", "n_support", "replay", "zone", "region", "county", "lat", "lon", "kv_max", "match_conf"]]
    sub["sf"] = sub["sf"].astype(float).round(4)
    sub["sf_pole"] = sub["sf_pole"].astype(float).round(4)
    sub["dist_km"] = sub["dist_km"].astype(float).round(1)
    sub = sub.sort_values(["cid", "sf"]).reset_index(drop=True)
    _write(sub, "sf_structural.csv", SF_CSV_LABELS)
    # merged inputs for Phase 3b (R2): phase 2 sub-level table + ours, and the zone table + ours
    write_phase3b_inputs(rows, cons)
    try:
        imm_anchors()
    except (FileNotFoundError, StopIteration, OSError) as e:
        print(f"  warning: IMM anchors table not rebuilt ({e}); needs pdftotext and the raw report")
    if "skip-validation" not in args:
        val = validation_table(R, asg, cal, phys)
        _write(val.round({"value": 4}), "sf_structural_validation.csv", VALIDATION_LABELS)
    print(f"sf-structural done in {time.time() - t0:.0f}s: {len(asg)} retained patterns, {len(rows):,} shift-factor rows")
    print(tg[["target", "status", "grade", "uses_cid", "pattern", "oop_r", "reason"]].to_string(index=False))
    print(cons[["cid", "grade", "status", "replay", "scale", "scale_method", "sph_usd_mw_yr", "H_hours", "driver_location", "driver_sign", "driver_corr", "driver_corr_holdout"]].to_string(index=False))
    return dict(R=R, asg=asg, cal=cal, phys=phys, rows=rows, cons=cons, targets=tg, components=comp)



def write_phase3b_inputs(rows: pd.DataFrame, cons: pd.DataFrame) -> None:
    """Two parquet files in the exact schemas Phase 3b reads, so that pointing nodal_lanes.SF_SUB and nodal_lanes.ZONE_SF_FILE at them adds the new constraints.

    sf_sub_plus_structural.parquet: phase 2's _p2_cache/sf_sub.parquet (sub, cid, method, sf, se, sf_min, sf_max, quality) plus one row per station and replayed pattern with method dam60_factor
    (interpolated rows use the same method name: their quality carries +interp). zone_sf_plus_structural.parquet: phase 3b's zone table (cid, method, zone, sf, bus_mean, r2_zone, source) plus
    the eight load zones for each replayed pattern."""
    rep = set(cons.loc[cons["replay"], "cid"])
    st = rows[(rows["level"] == "station") & rows["cid"].isin(rep)]
    add = pd.DataFrame({"sub": st["sub"].astype(str), "cid": st["cid"].astype(str), "method": "dam60_factor", "sf": st["sf"].astype("float32"),
                        "se": st["se"].astype("float32"), "sf_min": st["sf"].astype("float32"), "sf_max": st["sf"].astype("float32"), "quality": st["quality"].astype(str)})
    base = pd.read_parquet(SEP_OCT_SF_SUB)
    for c in ("sub", "cid", "method", "quality"):
        base[c] = base[c].astype(str)
    base = base[~((base["method"] == "dam60_factor") & base["cid"].isin(rep))]
    pd.concat([base, add[base.columns]], ignore_index=True).to_parquet(SF_SUB_PLUS, index=False)
    zs = pd.read_parquet(ZONE_SF_FILE)
    z = rows[(rows["level"] == "zone") & rows["cid"].isin(rep)]
    zadd = pd.DataFrame({"cid": z["cid"].astype(str), "method": "dam60_factor", "zone": z["sub"].astype(str), "sf": z["sf"].astype(float), "bus_mean": 0.0, "r2_zone": np.nan, "source": "dam60_factor"})
    zs = zs[~((zs["method"] == "dam60_factor") & zs["cid"].isin(rep))]
    pd.concat([zs, zadd[zs.columns]], ignore_index=True).to_parquet(ZONE_SF_PLUS, index=False)


# =================================================================================================================
# Regional candidate sites (task 3)
# =================================================================================================================
CAND_REGIONS = ("Panhandle", "Rio Grande Valley", "Far West", "Laredo/South", "Central (Bell County)")   # REAL: the brief's regions; the fifth is the Bell County / Salado pocket, kept so that upgrade U08 can be tested
CAND_PER_SIDE = 5                        # ASSUMPTION: up to this many sites per region and side (brief: about five)
CAND_MIN_SEP_KM = 10.0                   # ASSUMPTION: two picks in one region and side are at least this far apart (stations a few km apart share a bus group and one price)
CAND_MIN_TAIL = 2.0                      # ASSUMPTION: an import-side pick needs a 95th-percentile basis against its zone of at least +$2/MWh in the window (an export-side pick a 5th percentile of at most -$2): the observed prices must show the side
CAND_MIN_EXPOSURE = 500.0                # ASSUMPTION: a site needs a price exposure of at least this much, $/MW-yr (sum of |SF - SF_zone| x annual shadow-price-hours), to be listed
BELL_RADIUS_KM = 60.0                    # ASSUMPTION: "Central (Bell County)" = substations within this distance of Salado Switch / Bell County
UPGRADE_MOVE_USD = 1.0                   # REAL: Phase 6's threshold for "the upgrade moves a site": a change of more than $1/kW-yr
PRICED_STATIONS = CACHE / "priced_stations.parquet"

CANDIDATE_LABELS = [
    ("role", "DERIVED", "this module", "regional (new pick, to be valued by Phase 3b) or existing (one of the 40 Phase 3b candidates, shown with its new exposures)"),
    ("region", "ASSUMPTION", "region_of() / Bell County rule", "Panhandle, Rio Grande Valley, Far West, Laredo/South, Central (Bell County); existing rows keep their own region tag"),
    ("side", "DERIVED", "sign of the dominant term |SF - SF_zone| x shadow-price-hours", "import (price rises against the zone when the constraint binds: a battery sells high) or export (price falls: a battery charges low)"),
    ("rank_in_group", "DERIVED", "measured stations first, then screen_score within region and side", "1 = best; blank for existing rows"),
    ("sub", "REAL", "data/substations.csv (NP4-160)", "substation code"),
    ("kv_max", "REAL", "data/substations.csv", ""),
    ("county", "DERIVED", "data/substations.csv", ""),
    ("arena_zone", "REAL", "gridsite.sites.eligible_substations", "zone whose price the site is measured against"),
    ("lat", "DERIVED", "data/substations.csv", ""),
    ("lon", "DERIVED", "data/substations.csv", ""),
    ("match_conf", "DERIVED", "data/substations.csv", "placement confidence (>= 0.5 required)"),
    ("node_bus", "REAL", "data/settlement_points.csv", "highest-kV bus of the substation (the price a transmission ESR settles at)"),
    ("existing_resource_node", "REAL", "data/settlement_points.csv", "blank if the substation has no resource node today"),
    ("flood_flag", "DERIVED", "data/risk_flags.csv", ""),
    ("wildfire_class", "DERIVED", "data/risk_flags.csv", ""),
    ("coastal_flag", "DERIVED", "data/risk_flags.csv", ""),
    ("seismic_flag", "DERIVED", "data/risk_flags.csv", ""),
    ("basis_source", "DERIVED", "this module", "measured (a resource node at the substation was priced in the 60-day window) or interpolated (median of the nearest priced stations, at least two within 60 km)"),
    ("dist_priced_km", "DERIVED", "great-circle km", "0 if measured"),
    ("dominant_cid", "DERIVED", "largest |SF - SF_zone| x sph_pole", "structural constraint contributing most (sf_structural_constraints.csv)"),
    ("dominant_grade", "DERIVED", "grade_target()", "A-D, U"),
    ("dominant_sf", "DERIVED", "sf_structural.parquet, pole scale", "the site's shift factor to it"),
    ("dominant_dsf", "DERIVED", "SF - SF of the arena zone", "pole scale"),
    ("dominant_share", "DERIVED", "dominant term / total", "0 to 1"),
    ("exposure_import_usd_mw_yr", "DERIVED", "sum over the replayed structural constraints of |DSF| x sph_pole where DSF < 0", "$/MW-yr of price exposure (not a value)"),
    ("exposure_export_usd_mw_yr", "DERIVED", "same where DSF > 0", ""),
    ("exposure_total_usd_mw_yr", "DERIVED", "sum", ""),
    ("n_constraints", "DERIVED", "count", "structural constraints with |DSF| >= 0.05 at the site"),
    ("exposure_detail", "DERIVED", "top three terms", "cid:signed DSF x sph, $/MW-yr (negative = import side)"),
    ("basis_zone_mean", "DERIVED", "DAM price at the site minus the arena zone price, 1 Jul-11 Aug 2026", "$/MWh (REAL prices; interpolated sites use the median of nearby priced stations)"),
    ("basis_zone_p05", "DERIVED", "", "$/MWh"),
    ("basis_zone_p95", "DERIVED", "", "$/MWh"),
    ("hours_gt10_zone", "DERIVED", "hours with |basis vs zone| > $10", "of 1,008"),
    ("worst_hour_cpt", "DERIVED", "hour-beginning CPT with the lowest mean basis vs zone", ""),
    ("worst_hour_mean", "DERIVED", "", "$/MWh"),
    ("best_hour_cpt", "DERIVED", "hour-beginning CPT with the highest mean basis vs zone", ""),
    ("best_hour_mean", "DERIVED", "", "$/MWh"),
    ("dam_value_node", "DERIVED", "2 h day-ahead oracle on the site's own prices", "$ per kW over the 42 days (not annualised; summer; energy only; perfect foresight)"),
    ("dam_value_zone", "DERIVED", "same on the zone price", "$ per kW over the 42 days"),
    ("dam_uplift", "DERIVED", "dam_value_node - dam_value_zone", "what siting here added in this window"),
    ("pct_exposure", "DERIVED", "percentile rank of the side's exposure among eligible covered stations of the region", "0 to 100"),
    ("pct_uplift", "DERIVED", "percentile rank of dam_uplift in the same set", "0 to 100"),
    ("screen_score", "ASSUMPTION", "SCREEN_W_EXPOSURE x pct_exposure + (1 - SCREEN_W_EXPOSURE) x pct_uplift", "0 to 100; a screening number to order sites, not a value in dollars"),
    ("new_exposure_usd_kw_yr", "DERIVED", "capture 0.205 x sum of |DSF| x annual shadow-price-hours over the replayed structural constraints / 1000", "$/kW-yr: an upper bound on how much of the site's value the new and replaced constraints can move (first order, no dispatch)"),
    ("revalue_existing", "DERIVED", "new_exposure_usd_kw_yr >= REVALUE_MIN_USD", "True for an existing candidate whose lane Phase 3b should rebuild and dispatch again; blank for regional picks"),
    ("exposure_U02_vealmoor", "DERIVED", "first-order |DSF| x sph x capture x (1 - residual) / 1000", "$/kW-yr the Vealmoor-Koch Tap upgrade could move this site; blank = no shift factor (constraint not identified)"),
    ("exposure_U04_westex", "DERIVED", "same for WESTEX (residual 0.5)", "$/kW-yr"),
    ("exposure_U07_longshore", "DERIVED", "same for Longshore-Consavvy", "blank = constraint not identified"),
    ("exposure_U08_bell", "DERIVED", "same for Bell County-Salado", "$/kW-yr"),
]
REVALUE_MIN_USD = 1.0                    # ASSUMPTION: an existing candidate is flagged for re-dispatch if the structural constraints can move its value by this much, $/kW-yr (first order)
SCREEN_W_EXPOSURE = 0.5                  # ASSUMPTION: the screening score weighs exposure to the structural constraints and the observed day-ahead uplift equally

UPGRADE_REACH_LABELS = [
    ("upgrade_id", "REAL", "data/upgrades.csv", "U01-U10"),
    ("element", "REAL", "data/upgrades.csv", ""),
    ("evaluated_by", "DERIVED", "this module", "phase6_full (the dispatch valuation of Phase 6) or first_order (|DSF| x sph x capture x (1 - residual) from sf_structural), or none"),
    ("moves_a_site", "DERIVED", "any site above UPGRADE_MOVE_USD", "True/False"),
    ("n_sites_above_1usd", "DERIVED", "count", "sites (40 existing + regional picks) whose value changes by more than $1/kW-yr"),
    ("n_sites_above_0p25usd", "DERIVED", "count", "same threshold at $0.25"),
    ("max_change_usd_kw_yr", "DERIVED", "largest |change|", "$/kW-yr"),
    ("largest_site", "DERIVED", "", "site with the largest change"),
    ("n_existing_40_exposed", "DERIVED", "count", "of the 40 Phase 3b candidates, those with a first-order change above $0.05 (first_order rows) or any change above $0.05 (phase6_full rows)"),
    ("n_regional_exposed", "DERIVED", "count", "of the regional picks"),
    ("status_before_phase7", "DERIVED", "notes/phase6.md / README section 3.2", "what Phase 6 could say: evaluated or not evaluable"),
    ("note", "DERIVED", "", ""),
]


def _pctrank(x: pd.Series) -> pd.Series:
    return x.rank(pct=True, method="average") * 100.0


def station_prices(panel: pd.DataFrame, sp: pd.DataFrame, st: pd.DataFrame) -> pd.DataFrame:
    """Hours x priced substations: median day-ahead price of the substation's resource nodes."""
    rn = sp[(sp["sp_type"] == "resource_node") & sp["substation"].notna()]
    P = panel[list(rn["settlement_point"])].copy()
    P.columns = list(rn["substation"])
    return P.T.groupby(level=0).median().T


def interpolated_prices(P: pd.DataFrame, st: pd.DataFrame, subs: list) -> tuple[pd.DataFrame, pd.Series]:
    """For substations without a priced node: the median price series of their INTERP_K nearest priced stations within INTERP_MAX_KM (at least two). Returns (series, distance to nearest)."""
    pr = st.reindex(P.columns)
    ok = pr["lat"].notna() & pr["lon"].notna()
    pidx = np.where(ok.to_numpy())[0]
    X = P.to_numpy(float)
    out, dist = {}, {}
    for s in subs:
        if s not in st.index or pd.isna(st.at[s, "lat"]):
            continue
        d = km_between(st.at[s, "lat"], st.at[s, "lon"], pr["lat"].to_numpy(float)[pidx], pr["lon"].to_numpy(float)[pidx])
        o = np.argsort(d)[:INTERP_K]
        o = o[d[o] <= INTERP_MAX_KM]
        if len(o) >= 2:
            out[s] = np.nanmedian(X[:, pidx[o]], axis=1)
            dist[s] = float(d[o[0]])
    return pd.DataFrame(out, index=P.index), pd.Series(dist)


def run_regional_candidates(*args) -> pd.DataFrame:
    """Task 3: the screened regional candidates, the 40 existing candidates' new exposures, and the upgrade reach table."""
    from . import sites as SITES
    from . import upgrades as UPG

    cons = _read_csv("sf_structural_constraints.csv")
    cons = cons[cons["replay"] == True].copy()   # noqa: E712
    rows = pd.read_parquet(SF_STRUCTURAL_PARQUET)
    rows = rows[rows["cid"].isin(cons["cid"])]
    st = station_table()
    priced = pd.read_parquet(PRICED_STATIONS)["sub"].tolist()
    panel = load_panel()
    sp = sp_table(panel.columns)
    Pst = station_prices(panel, sp, st)
    Pst = Pst[[c for c in Pst.columns if c in priced]]
    el = SITES.eligible_substations().copy()
    el = el.merge(st[["region", "region_how"]], left_on="sub", right_index=True, how="left")
    # Bell County rule
    salsw = line_ends_xy("BELCNTY-SALSW|UNSEEN", st)
    if salsw:
        la = np.mean([p[0] for p in salsw.values()])
        lo = np.mean([p[1] for p in salsw.values()])
        d = km_between(el["lat"].to_numpy(float), el["lon"].to_numpy(float), la, lo)
        el.loc[np.isfinite(d) & (d <= BELL_RADIUS_KM), "region"] = "Central (Bell County)"
    el = el[el["region"].isin(CAND_REGIONS)].copy()
    existing = _read_csv("site_candidates.csv")
    existing = existing[existing["role"] == "candidate"]
    ex_subs = list(existing["sub"])
    ex = SITES.eligible_substations()
    ex = ex[ex["sub"].isin(ex_subs)].merge(st[["region"]], left_on="sub", right_index=True, how="left")
    pool = pd.concat([el.assign(role="regional"), ex.assign(role="existing")], ignore_index=True)
    pool = pool.drop_duplicates(["sub", "role"])
    # shift factors and coverage
    Sp = rows[rows["level"] == "station"].pivot_table(index="sub", columns="cid", values="sf_pole", aggfunc="first")
    Zp = rows[rows["level"] == "zone"].pivot_table(index="sub", columns="cid", values="sf_pole", aggfunc="first")
    pr = st.reindex(priced)
    need = [s for s in pool["sub"].unique() if s not in priced]
    Pint, dint = interpolated_prices(Pst, st, need)
    covered = set(priced) | set(Pint.columns)
    pool["covered"] = pool["sub"].isin(covered)
    pool = pool[(pool["role"] == "existing") | pool["covered"]].reset_index(drop=True)
    cids = list(cons["cid"])
    sph = dict(zip(cons["cid"], cons["sph_pole_usd_mw_yr"]))
    grade = dict(zip(cons["cid"], cons["grade"]))
    n = len(pool)
    DSF = np.zeros((n, len(cids)))
    SFm = np.zeros((n, len(cids)))
    for k, c in enumerate(cids):
        s_sf = Sp[c].reindex(pool["sub"]).fillna(0.0).to_numpy(float) if c in Sp.columns else np.zeros(n)
        z_sf = Zp[c].reindex(pool["arena_zone"]).fillna(0.0).to_numpy(float) if c in Zp.columns else np.zeros(n)
        SFm[:, k] = s_sf
        DSF[:, k] = s_sf - z_sf
    W = np.array([sph[c] for c in cids])
    term = DSF * W[None, :]
    imp = np.where(DSF <= -0.05, -term, 0.0).sum(1)
    exp_ = np.where(DSF >= 0.05, term, 0.0).sum(1)
    nocov = ~pool["covered"].to_numpy()
    imp = np.where(nocov, np.nan, imp)
    exp_ = np.where(nocov, np.nan, exp_)
    pool["exposure_import_usd_mw_yr"] = imp
    pool["exposure_export_usd_mw_yr"] = exp_
    pool["exposure_total_usd_mw_yr"] = imp + exp_
    pool["n_constraints"] = (np.abs(DSF) >= 0.05).sum(1)
    big = np.argmax(np.abs(term), axis=1)
    pool["dominant_cid"] = [cids[b] if abs(term[i, b]) > 0 else "" for i, b in enumerate(big)]
    pool["dominant_grade"] = [grade.get(c, "") for c in pool["dominant_cid"]]
    pool["dominant_sf"] = [SFm[i, b] for i, b in enumerate(big)]
    pool["dominant_dsf"] = [DSF[i, b] for i, b in enumerate(big)]
    pool["dominant_share"] = [abs(term[i, b]) / max(np.abs(term[i]).sum(), 1e-9) for i, b in enumerate(big)]
    pool["side"] = np.where(pool["dominant_dsf"] < 0, "import", "export")
    for col in ("dominant_cid", "dominant_grade", "exposure_detail", "side"):
        pool.loc[nocov, col] = ""
    for col in ("dominant_sf", "dominant_dsf", "dominant_share"):
        pool.loc[nocov, col] = np.nan
    detail = []
    for i in range(n):
        o = np.argsort(-np.abs(term[i]))[:3]
        detail.append(";".join(f"{cids[b]}:{term[i, b]:+.0f}" for b in o if abs(term[i, b]) > 0))
    pool["exposure_detail"] = detail
    # observed day-ahead basis against the zone
    hours = hour_of_day_cpt(panel.index)
    basis, value_node, value_zone, src, dist = {}, {}, {}, {}, {}
    series = {}
    for s, z in zip(pool["sub"], pool["arena_zone"]):
        if s in Pst.columns:
            series[s] = Pst[s].to_numpy(float)
            src[s] = "measured"
            dist[s] = 0.0
        elif s in Pint.columns:
            series[s] = Pint[s].to_numpy(float)
            src[s] = "interpolated"
            dist[s] = float(dint[s])
    subs = list(series)
    zones = dict(zip(pool["sub"], pool["arena_zone"]))
    vals = observed_dam_values(pd.DataFrame({**{s: series[s] for s in subs}, **{z: panel[z].to_numpy(float) for z in set(zones.values()) if z in panel.columns}}, index=panel.index), subs, zones)
    stats = {}
    for s in subs:
        z = zones[s]
        b = series[s] - panel[z].to_numpy(float)
        hm = np.array([np.nanmean(b[hours == h]) for h in range(24)])
        stats[s] = dict(basis_zone_mean=float(np.nanmean(b)), basis_zone_p05=float(np.nanpercentile(b, 5)), basis_zone_p95=float(np.nanpercentile(b, 95)),
                        hours_gt10_zone=int((np.abs(b) > BASIS_BIG[0]).sum()), worst_hour_cpt=int(np.nanargmin(hm)), worst_hour_mean=float(np.nanmin(hm)),
                        best_hour_cpt=int(np.nanargmax(hm)), best_hour_mean=float(np.nanmax(hm)))
    sdf = pd.DataFrame(stats).T
    pool = pool.merge(sdf, left_on="sub", right_index=True, how="left")
    pool["basis_source"] = pool["sub"].map(src)
    pool["dist_priced_km"] = pool["sub"].map(dist)
    pool["dam_value_node"] = pool["sub"].map(vals["dam_value_node"])
    pool["dam_value_zone"] = pool["sub"].map(vals["dam_value_zone"])
    pool["dam_uplift"] = pool["dam_value_node"] - pool["dam_value_zone"]
    # first-order upgrade exposure (capture factor and residuals of phase 4)
    cap = UPG.CAPTURE_FACTOR["2h"]
    pool["new_exposure_usd_kw_yr"] = np.where(nocov, np.nan, np.abs(term).sum(1) * cap / 1000.0)
    pool["revalue_existing"] = np.where(pool["role"] == "existing", pool["new_exposure_usd_kw_yr"].fillna(0) >= REVALUE_MIN_USD, np.nan)
    for col, cid, resid in (("exposure_U02_vealmoor", "VEALMOOR-KOCHTAP|UNSEEN", UPG.RESIDUAL_MU_SCALE_WIRES), ("exposure_U04_westex", "WESTEX|BASE CASE", UPG.RESIDUAL_MU_SCALE_GTC),
                            ("exposure_U07_longshore", "LNGSW-CONSW|UNSEEN", UPG.RESIDUAL_MU_SCALE_WIRES), ("exposure_U08_bell", "BELCNTY-SALSW|UNSEEN", UPG.RESIDUAL_MU_SCALE_WIRES)):
        if cid in cids:
            k = cids.index(cid)
            pool[col] = np.where(nocov, np.nan, np.abs(DSF[:, k]) * W[k] * cap * (1.0 - resid) / 1000.0)
        else:
            pool[col] = np.nan
    # screening score within region (regional rows) and pick
    pool["pct_exposure"] = np.nan
    pool["pct_uplift"] = np.nan
    pool["screen_score"] = np.nan
    pool["rank_in_group"] = np.nan
    reg = pool["role"] == "regional"
    for rgn, g in pool[reg].groupby("region"):
        side_exp = np.where(g["side"] == "import", g["exposure_import_usd_mw_yr"], g["exposure_export_usd_mw_yr"])
        pe = pd.Series(side_exp, index=g.index)
        pool.loc[g.index, "pct_exposure"] = _pctrank(pe)
        pool.loc[g.index, "pct_uplift"] = _pctrank(g["dam_uplift"])
    pool.loc[reg, "screen_score"] = SCREEN_W_EXPOSURE * pool.loc[reg, "pct_exposure"] + (1 - SCREEN_W_EXPOSURE) * pool.loc[reg, "pct_uplift"]
    picks = []
    for (rgn, side), g in pool[reg].groupby(["region", "side"]):
        g = g[np.where(g["side"] == "import", g["exposure_import_usd_mw_yr"], g["exposure_export_usd_mw_yr"]) >= CAND_MIN_EXPOSURE]
        g = g[(g["basis_zone_p95"] >= CAND_MIN_TAIL) if side == "import" else (g["basis_zone_p05"] <= -CAND_MIN_TAIL)]
        g = g.assign(_measured=(g["basis_source"] == "measured")).sort_values(["_measured", "screen_score", "dam_uplift"], ascending=False)
        chosen = []
        for i, r in g.iterrows():
            if all(km_between(r["lat"], r["lon"], pool.at[j, "lat"], pool.at[j, "lon"]) >= CAND_MIN_SEP_KM for j in chosen):
                chosen.append(i)
            if len(chosen) >= CAND_PER_SIDE:
                break
        for rk, i in enumerate(chosen, 1):
            pool.at[i, "rank_in_group"] = rk
            picks.append(i)
    out = pool[(pool["role"] == "existing") | pool.index.isin(picks)].copy()
    cols = [c[0] for c in CANDIDATE_LABELS]
    out["kv_max"] = out["kv_max"].astype(float)
    out = out.sort_values(["role", "region", "side", "rank_in_group"], ascending=[False, True, True, True])
    out = out[cols]
    num = out.select_dtypes("number").columns
    out[num] = out[num].round(3)
    _write(out, "regional_candidates.csv", CANDIDATE_LABELS)
    reach = upgrade_reach(out, pool, cons)
    _write(reach, "regional_upgrade_reach.csv", UPGRADE_REACH_LABELS)
    print(f"regional-candidates: {int((out['role'] == 'regional').sum())} regional picks, {int((out['role'] == 'existing').sum())} existing candidates with new exposures")
    show = out[out["role"] == "regional"][["region", "side", "rank_in_group", "sub", "county", "kv_max", "basis_source", "dominant_cid", "dominant_dsf", "exposure_total_usd_mw_yr", "basis_zone_mean", "dam_uplift", "screen_score"]]
    print(show.to_string(index=False))
    print(reach[["upgrade_id", "element", "evaluated_by", "moves_a_site", "n_sites_above_1usd", "max_change_usd_kw_yr", "largest_site"]].to_string(index=False))
    return out


def upgrade_reach(out: pd.DataFrame, pool: pd.DataFrame, cons: pd.DataFrame) -> pd.DataFrame:
    """Which of the ten top upgrades move at least one site: Phase 6's dispatch valuation where it exists, the first-order estimate from sf_structural where Phase 6 could not evaluate."""
    ups = _read_csv("upgrades.csv")
    ups = ups[ups["upgrade_id"].isin([f"U{i:02d}" for i in range(1, 11)])]
    rr = _read_csv("upgrade_rerank.csv")
    first = {"U02": "exposure_U02_vealmoor", "U04": "exposure_U04_westex", "U07": "exposure_U07_longshore", "U08": "exposure_U08_bell"}
    rows = []
    reg = out[out["role"] == "regional"]
    exs = out[out["role"] == "existing"]
    for _, u in ups.iterrows():
        uid = u["upgrade_id"]
        r = dict(upgrade_id=uid, element=u["element"], evaluated_by="none", moves_a_site=False, n_sites_above_1usd=0, n_sites_above_0p25usd=0, max_change_usd_kw_yr=0.0, largest_site="",
                 n_existing_40_exposed=0, n_regional_exposed=0, status_before_phase7="", note="")
        sc = rr[rr["scenario"] == uid]
        if len(sc) and sc["scenario_effect"].iloc[0] == "evaluated":
            d = sc["delta_usd_kw_yr"].abs()
            r.update(evaluated_by="phase6_full", n_sites_above_1usd=int((d > UPGRADE_MOVE_USD).sum()), n_sites_above_0p25usd=int((d > 0.25).sum()), max_change_usd_kw_yr=float(d.max()),
                     largest_site=str(sc.loc[d.idxmax(), "sub"]), n_existing_40_exposed=int((d > 0.05).sum()), status_before_phase7="evaluated")
            r["moves_a_site"] = r["n_sites_above_1usd"] > 0
            r["note"] = "Phase 6 dispatch valuation of the 40 existing sites"
        else:
            r["status_before_phase7"] = "not evaluable: no shift factor"
            col = first.get(uid)
            if col and out[col].notna().any():
                allv = pd.concat([exs[col], reg[col]])
                allsub = pd.concat([exs["sub"], reg["sub"]])
                r.update(evaluated_by="first_order", n_sites_above_1usd=int((allv > UPGRADE_MOVE_USD).sum()), n_sites_above_0p25usd=int((allv > 0.25).sum()),
                         max_change_usd_kw_yr=float(allv.max()), largest_site=str(allsub.loc[allv.idxmax()]), n_existing_40_exposed=int((exs[col] > 0.05).sum()),
                         n_regional_exposed=int((reg[col] > 0.05).sum()))
                r["moves_a_site"] = r["n_sites_above_1usd"] > 0
                r["note"] = "first-order |DSF| x annual shadow-price-hours x capture 0.205 x (1 - residual), from sf_structural.parquet"
            else:
                r["note"] = "constraint not identified in the 60-day window (sf_structural_targets.csv): no shift factor, so no site moves"
        rows.append(r)
    res = pd.DataFrame(rows)
    num = res.select_dtypes("number").columns
    res[num] = res[num].round(3)
    return res


# =================================================================================================================
# IMM 2026 year-to-date anchors (task 2c: "mu scale from IMM rent / limit") and the Phase 2 shift-factor matches
# =================================================================================================================
IMM_PDF_AUG = paths.RAW / "docs" / "Potomac_2026-08_Nodal_Monthly_Report.pdf"      # REAL: IMM monthly report for August 2026 (published 21 Sep 2026), recorded in the manifest
IMM_ANCHORS_CSV = "imm_2026_anchors.csv"
IMM_YTD_MONTHS = 8                       # REAL: January-August are in the report
SF_MATCH_MIN_STATIONS = 300              # ASSUMPTION: a Phase 2 constraint is a candidate identity for a pattern if its shift factor exists at this many of the priced stations ...
SF_MATCH_MIN_BIG = 5                     # ASSUMPTION: ... and at least this many carry |SF| > 0.1
SF_MATCH_DUPLICATE_R = 0.90              # ASSUMPTION: a retained pattern whose shift factors correlate with an already replayed Phase 3b constraint at this r duplicates it (`replaces_cid`)

IMM_ANCHOR_LABELS = [
    ("market", "REAL", "IMM monthly report, August 2026 (Report 18-19)", "RT or DA"),
    ("metric", "REAL", "", "rent_musd (total congestion rent, $M) or count (RT: 5-minute SCED intervals binding; DA: hours binding)"),
    ("rank", "REAL", "", "rank in the 2026 year-to-date top-10 list"),
    ("code", "REAL", "", "constraint code as the IMM prints it (CONSTRAINT_NAME for lines, the GTC code for interfaces)"),
    ("element", "REAL", "", "element as the IMM prints it"),
    ("ytd", "REAL", "", "January-August 2026 total"),
    ("jan", "REAL", "", "monthly value"),
    ("feb", "REAL", "", ""),
    ("mar", "REAL", "", ""),
    ("apr", "REAL", "", ""),
    ("may", "REAL", "", ""),
    ("jun", "REAL", "", ""),
    ("jul", "REAL", "", ""),
    ("aug", "REAL", "", ""),
    ("constraint_name", "DERIVED", "data/constraints.csv", "constraint_name matched on the IMM code; blank if the code never appeared in the Sep-Oct windows"),
    ("limit_mw", "REAL", "data/constraints.csv", "largest median limit among its cids (NP6-86-CD / NP4-191-CD)"),
    ("n_cids_replayed", "DERIVED", "data/constraint_synth.csv", "cids of the element in the 260 replayed constraints"),
    ("priority_rank", "DERIVED", "data/constraints.csv", "best phase 2 priority rank among its cids"),
    ("implied_sph_usd_mw_yr", "DERIVED", "rent_musd x 1e6 x 12 / 8 / limit", "annual shadow-price-hours this year-to-date rent implies, $/MW-yr (rent rows only)"),
    ("phase3b_sph_usd_mw_yr", "DERIVED", "data/constraint_synth.csv", "sum over the element's replayed cids"),
    ("ratio", "DERIVED", "implied / phase 3b", "above 1: Phase 3b's annual target is below what 2026 shows"),
]


def parse_imm_report(pdf: Path) -> pd.DataFrame:
    """The four 2026 year-to-date top-10 lists (RT and DA, by rent and by frequency) of an IMM monthly report with their monthly values, via pdftotext -layout."""
    import re
    import subprocess

    txt = subprocess.run(["pdftotext", "-layout", str(pdf), "-"], capture_output=True, text=True, timeout=120).stdout
    lines = txt.splitlines()
    i_rt = next(i for i, l in enumerate(lines) if "RT Congestion Constraint Rankings - Top 10" in l)
    i_da = next(i for i, l in enumerate(lines) if "DA Congestion Constraint Rankings - Top 10" in l)
    i_crr = next(i for i, l in enumerate(lines) if "Top 10 CRR Pairs Ranked by Monthly Auction Values - Top 10" in l)
    num = r"[\d,]+(?:\.\d+)?"
    freq_re = re.compile(rf"^(?:FREQ_EXIST|TOP10_RENT)\s+2026\s+(?P<name>\S.*?\))\s+(?P<val>{num})\s+(?P<rank>\d+)\s+(?P<m>(?:{num}\s+){{7}}{num})")
    rent_re = re.compile(rf"^\s+(?P<name>\S.*?\))\s+\$\s*(?P<val>{num})\s+(?P<rank>\d+)\s+(?P<m>(?:\$\s*(?:{num}|-)\s*){{8}})")
    rows = []
    for market, a, b in (("RT", i_rt, i_da), ("DA", i_da, i_crr)):
        for l in lines[a:b]:
            m = freq_re.match(l)
            kind = "count"
            if not m:
                m = rent_re.match(l)
                kind = "rent_musd"
            if not m:
                continue
            name = m.group("name").strip()
            code, _, rest = name.partition("(")
            vals = [float(x.replace(",", "")) if x not in ("-", "") else 0.0 for x in re.findall(rf"{num}|-", m.group("m").replace("$", " "))][:8]
            if len(vals) < 8:
                continue
            rows.append(dict(market=market, metric=kind, rank=int(m.group("rank")), code=code.strip(), element=rest.rstrip(") ").strip(),
                             ytd=float(m.group("val").replace(",", "")), **dict(zip(("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug"), vals))))
    return pd.DataFrame(rows)


def imm_anchors(write: bool = True) -> pd.DataFrame:
    """The IMM 2026 year-to-date top-10 lists joined to the constraint table: what each implies for the annual shadow-price-hours against what Phase 3b replayed."""
    t = parse_imm_report(IMM_PDF_AUG)
    cons = _read_csv("constraints.csv")
    synth = _read_csv("constraint_synth.csv")
    lim = cons.groupby("constraint_name")["median_limit_mw"].max()
    prio = cons.groupby("constraint_name")["priority_rank"].min()
    sph = synth.groupby("constraint_name")["sph_usd_mw_yr"].sum()
    ncid = synth.groupby("constraint_name").size()
    t["constraint_name"] = t["code"].where(t["code"].isin(cons["constraint_name"]), "")
    t["limit_mw"] = t["constraint_name"].map(lim)
    t["n_cids_replayed"] = t["constraint_name"].map(ncid).fillna(0).astype(int)
    t["priority_rank"] = t["constraint_name"].map(prio)
    is_rent = t["metric"] == "rent_musd"
    t["implied_sph_usd_mw_yr"] = np.where(is_rent & (t["limit_mw"] > 0), t["ytd"] * 1e6 * 12.0 / IMM_YTD_MONTHS / t["limit_mw"], np.nan)
    t["phase3b_sph_usd_mw_yr"] = t["constraint_name"].map(sph)
    t["ratio"] = t["implied_sph_usd_mw_yr"] / t["phase3b_sph_usd_mw_yr"]
    num_cols = t.select_dtypes("number").columns
    t[num_cols] = t[num_cols].round(3)
    if write:
        _write(t, IMM_ANCHORS_CSV, IMM_ANCHOR_LABELS)
    return t


def phase2_matches(R: dict, asg: pd.DataFrame) -> pd.DataFrame:
    """For every retained pattern the Phase 2 constraint (regression shift factors from the Sep-Oct windows, rt_reg or dam_reg, all fitted constraints) whose shift factors over the
    priced stations correlate best with the pattern's, and whether that constraint is one of the 260 Phase 3b replays."""
    sub = pd.read_parquet(SEP_OCT_SF_SUB, columns=["sub", "cid", "method", "sf"])
    sub = sub[sub["method"].isin(["rt_reg", "dam_reg"]) & sub["sub"].isin(R["cols"])]
    sub["sub"] = sub["sub"].astype(str)
    sub["cid"] = sub["cid"].astype(str)
    M = sub.pivot_table(index="cid", columns="sub", values="sf", aggfunc="first").reindex(columns=R["cols"])
    ok = M.notna().sum(1) >= SF_MATCH_MIN_STATIONS
    M = M[ok & ((M.abs() > 0.1).sum(1) >= SF_MATCH_MIN_BIG)]
    X = M.to_numpy(float)
    X = np.where(np.isfinite(X), X, 0.0)
    X = X - X.mean(1, keepdims=True)
    Xn = X / np.where(np.linalg.norm(X, axis=1, keepdims=True) > 0, np.linalg.norm(X, axis=1, keepdims=True), 1)
    synth = set(_read_csv("constraint_synth.csv")["cid"])
    rows = []
    for _, a in asg.iterrows():
        j = int(a["pattern"])
        v = R["SF"][:, j] - R["SF"][:, j].mean()
        v = v / max(np.linalg.norm(v), 1e-12)
        r = Xn @ v
        o = np.argsort(-r)[:3]
        best = M.index[o[0]]
        rows.append(dict(pattern=j, phase2_match_cid=best, phase2_match_r=float(r[o[0]]), phase2_match_replayed=bool(best in synth),
                         phase2_matches=";".join(f"{M.index[k]}:{r[k]:.2f}" for k in o),
                         replaces_cid=(best if (r[o[0]] >= SF_MATCH_DUPLICATE_R and best in synth and best != a["cid"]) else "")))
    return pd.DataFrame(rows)


# =================================================================================================================
# Hand-off to Phase 3b (R2): the same three edits as a runtime patch
# =================================================================================================================
def structural_replay_rows(exclude_extra: tuple = ()) -> pd.DataFrame:
    """Rows of data/sf_structural_constraints.csv flagged replay, in the schema of data/constraint_synth.csv (the first 35 columns).
    R2: `exclude_extra` lists structural cids to leave out (the grade-D Panhandle pattern in the primary run of revision 2)."""
    cons = _read_csv("sf_structural_constraints.csv")
    synth_cols = list(_read_csv("constraint_synth.csv").columns)
    cons = cons[(cons["replay"] == True) & ~cons["cid"].isin(set(exclude_extra))]   # noqa: E712
    return cons[synth_cols].reset_index(drop=True)


def structural_replaced_cids(exclude_extra: tuple = (), keep_replaced: tuple = ()) -> list:
    """Phase 3b cids whose rows must be dropped when the structural rows are added: the same cid (NELRIO, E_PASP: their phase 2 day-ahead fits are replaced) and the cids
    a pattern duplicates (`replaces_cid`). R2: a row left out through `exclude_extra` replaces nothing, and `keep_replaced` keeps the named Phase 3b cids."""
    cons = _read_csv("sf_structural_constraints.csv")
    cons = cons[(cons["replay"] == True) & ~cons["cid"].isin(set(exclude_extra))]   # noqa: E712
    synth = _read_csv("constraint_synth.csv")
    out = set(cons["cid"]) & set(synth["cid"])
    out |= {c for c in cons["replaces_cid"].dropna() if c}
    return sorted(out - set(keep_replaced))


def install_into_nodal_lanes(drop_replaced: bool = True, exclude_extra: tuple = (), keep_replaced: tuple = ()) -> dict:
    """Make `gridsite.nodal_lanes` read the structural constraints without editing the file: point SF_SUB at the merged substation table, append our zone shift factors in
    `zone_sf()`, and append our constraint rows (and drop the replaced ones) in `constraint_structure()`. Call once per process, before `mu_tilde()`, `sub_sf_matrix()` or
    `sites.value_sites()`. Returns what was done. The lane cache keys include the shift-factor file stamps and the structure hash, so lanes are rebuilt, not reused.

    Not patched: `bus_sf_matrix` (bus-level validation against resource nodes reads sf_long) and the on-disk copy data/constraint_synth.csv (written by the unpatched function)."""
    from . import nodal_lanes as L

    if getattr(L, "_p7_installed", False):
        return dict(already=True)
    extra = structural_replay_rows(exclude_extra)                  # R2: exclude_extra / keep_replaced are the revision-2 switches (gridsite.rev2)
    drop = set(structural_replaced_cids(exclude_extra, keep_replaced)) if drop_replaced else set()
    zrows = pd.read_parquet(ZONE_SF_PLUS)
    zrows = zrows[zrows["method"] == "dam60_factor"]
    L.SF_SUB = SF_SUB_PLUS
    orig_zone, orig_struct = L.zone_sf, L.constraint_structure

    def zone_sf(refresh: bool = False):
        z = orig_zone(refresh)
        z = z[~((z["method"] == "dam60_factor") & z["cid"].isin(set(zrows["cid"])))]
        return pd.concat([z, zrows[z.columns]], ignore_index=True)

    def constraint_structure(*a, **k):
        s = orig_struct(*a, **k)
        s = s[~s["cid"].isin(drop | set(extra["cid"]))]
        return pd.concat([s, extra[s.columns]], ignore_index=True)

    L.zone_sf = zone_sf
    L.constraint_structure = constraint_structure
    L.mu_tilde.cache_clear()
    L._p7_installed = True
    return dict(sf_sub=str(SF_SUB_PLUS), n_added=len(extra), dropped=sorted(drop))


# R2: nodal_lanes installs the revision-2 structure when it is imported (gridsite.rev2). If this module was the first one imported, nodal_lanes could not do it yet
# (this module was only half defined); do it now.
from . import nodal_lanes as _nodal_lanes  # noqa: E402

if getattr(_nodal_lanes, "_r2_pending", False):
    _nodal_lanes._r2_pending = False
    from . import rev2 as _rev2  # noqa: E402

    _rev2.install()
