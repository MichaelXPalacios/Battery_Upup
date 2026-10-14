"""Phase 1c: place ERCOT substations (NP4-160 SUBSTATION codes) on the map and build the line graph.

Pipeline (see notes/phase1-geo.md for the findings and the confidence definition):
  1. load ERCOT mapping, CDR unit list, GIS report, EIA-860/860M, OSM, HIFLD, Census counties
  2. county of every point (pure-numpy point-in-polygon on the TIGER 500k shapefile)
  3. match every ERCOT `sub` to coordinates: eia_unit > gis_poi > osm_name/hifld_name > neighbor
  4. build substation-to-substation lines from OSM ways (and HIFLD segments), snap endpoints to substations
  5. write data/*.csv (+ .labels.csv), derived/*.geojson

Everything here is deterministic. No language-model output decides a coordinate or a confidence.
"""
import json
import math
import re
import struct
import zipfile
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.spatial import cKDTree

from . import geo_overrides  # G1: audited corrections applied inside match_geo
from . import paths
from .paths import DERIVED, RAW, REPO_DATA, SP_MAPPING_SEP2026

# ------------------------------------------------------------------ constants (label, source) ------------------
SNAP_TOL_M = 500               # ASSUMPTION: line endpoint / mid-line vertex snaps to an OSM substation within this distance
GAP_TOL_M = 150                # ASSUMPTION: two line ends of equal voltage closer than this are one corridor (OSM gaps)
HIFLD_SNAP_M = 1500            # ASSUMPTION: HIFLD vertices are coarser (digitised from imagery); snap looser
COUNTY_NEAR_KM = 12            # ASSUMPTION: a point within this distance of a county polygon "agrees" with that county
MIN_TX_KV = 69                 # REAL: scope of the study (69 kV and above)
KV_TOL = 0.12                  # ASSUMPTION: relative tolerance when comparing nominal kV (e.g. 138 vs 138.0, 345 vs 345)
EARTH_R_KM = 6371.0088         # REAL: mean earth radius
DEG_KM = 111.195               # REAL: km per degree of latitude (R*pi/180)
GEO_DIR = RAW / "geo"
OSM_DIR = RAW / "osm"
EIA_DIR = RAW / "eia"
DOCS_DIR = RAW / "docs"
NEIGHBOR_MAX_KM = 120          # ASSUMPTION: two stations joined by one binding line are closer than this
STATE_FIPS_NEAR_TX = ("48", "40", "35", "22", "05")   # REAL: TX, OK, NM, LA, AR (so border points are not forced into TX)


# ------------------------------------------------------------------ geometry helpers ---------------------------
def haversine_km(lat1, lon1, lat2, lon2):
    p1, p2 = np.radians(lat1), np.radians(lat2)
    dphi = p2 - p1
    dl = np.radians(lon2) - np.radians(lon1)
    a = np.sin(dphi / 2) ** 2 + np.cos(p1) * np.cos(p2) * np.sin(dl / 2) ** 2
    return 2 * EARTH_R_KM * np.arcsin(np.sqrt(a))


LAT0 = 31.0   # projection centre for k-d trees (Texas); distances are re-checked with haversine


def to_xy_km(lat, lon):
    lat = np.asarray(lat, float)
    lon = np.asarray(lon, float)
    return np.column_stack([(lon + 100.0) * DEG_KM * math.cos(math.radians(LAT0)), (lat - LAT0) * DEG_KM])


def line_length_km(lat, lon):
    lat = np.asarray(lat, float)
    lon = np.asarray(lon, float)
    if len(lat) < 2:
        return 0.0
    return float(haversine_km(lat[:-1], lon[:-1], lat[1:], lon[1:]).sum())


# ------------------------------------------------------------------ shapefile + counties ------------------------
def read_shapefile(path_no_ext):
    """Minimal ESRI shapefile reader for polygon layers (TIGER cartographic boundaries). Returns (records, geoms).

    geoms[i] = (bbox, [ring arrays (n,2) of lon,lat]). No shapely/pyshp in the research venv, so this is hand-rolled.
    """
    base = Path(path_no_ext)
    shp = base.with_suffix(".shp").read_bytes()
    dbf = base.with_suffix(".dbf").read_bytes()
    geoms = []
    pos = 100
    while pos < len(shp):
        _num, clen = struct.unpack(">ii", shp[pos:pos + 8])
        body = shp[pos + 8: pos + 8 + clen * 2]
        pos += 8 + clen * 2
        stype = struct.unpack("<i", body[:4])[0]
        if stype == 0:
            geoms.append(None)
            continue
        bbox = struct.unpack("<4d", body[4:36])
        nparts, npts = struct.unpack("<ii", body[36:44])
        parts = struct.unpack(f"<{nparts}i", body[44:44 + 4 * nparts])
        pts = np.frombuffer(body, dtype="<f8", count=2 * npts, offset=44 + 4 * nparts).reshape(npts, 2)
        rings = [pts[a:b] for a, b in zip(parts, list(parts[1:]) + [npts])]
        geoms.append((bbox, rings))
    nrec = struct.unpack("<I", dbf[4:8])[0]
    hlen, rlen = struct.unpack("<HH", dbf[8:12])
    fields, p = [], 32
    while dbf[p] != 0x0D:
        name = dbf[p:p + 11].split(b"\x00")[0].decode("ascii")
        flen = dbf[p + 16]
        fields.append((name, flen))
        p += 32
    recs = []
    for i in range(nrec):
        row = dbf[hlen + i * rlen: hlen + (i + 1) * rlen]
        off, rec = 1, {}
        for name, flen in fields:
            rec[name] = row[off:off + flen].decode("latin-1").strip()
            off += flen
        recs.append(rec)
    return recs, geoms


def _pip(px, py, ring):
    """Vectorised even-odd ray cast: which of (px,py) lie inside one ring (lon,lat)."""
    x, y = ring[:, 0], ring[:, 1]
    x2, y2 = np.roll(x, -1), np.roll(y, -1)
    inside = np.zeros(len(px), bool)
    for xa, ya, xb, yb in zip(x, y, x2, y2):
        if ya == yb:
            continue
        cond = ((ya > py) != (yb > py)) & (px < (xb - xa) * (py - ya) / (yb - ya) + xa)
        inside ^= cond
    return inside


class Counties:
    """County lookup for Texas (plus border-state counties so border points are not forced into Texas)."""

    def __init__(self):
        recs, geoms = read_shapefile(GEO_DIR / "cb_2023_us_county_500k" / "cb_2023_us_county_500k")
        self.items = []
        for r, g in zip(recs, geoms):
            if g is None or r["STATEFP"] not in STATE_FIPS_NEAR_TX:
                continue
            self.items.append(dict(fips=r["GEOID"], name=r["NAME"].upper(), state=r["STATEFP"], bbox=g[0], rings=g[1]))
        self.tx = [c for c in self.items if c["state"] == "48"]
        # boundary vertices of each TX county, for distance-to-county
        self._tx_xy = {c["name"]: to_xy_km(np.concatenate([r[:, 1] for r in c["rings"]]),
                                           np.concatenate([r[:, 0] for r in c["rings"]])) for c in self.tx}
        self._tx_tree = {k: cKDTree(v) for k, v in self._tx_xy.items()}
        self.centroid = {}
        for c in self.tx:
            r = max(c["rings"], key=len)
            self.centroid[c["name"]] = (float(r[:, 1].mean()), float(r[:, 0].mean()))

    def lookup(self, lat, lon):
        """County name (upper) for each point; '' when not in a Texas county; ' OUT' marker avoided (returns '')."""
        lat = np.asarray(lat, float)
        lon = np.asarray(lon, float)
        out = np.full(len(lat), "", dtype=object)
        done = np.zeros(len(lat), bool)
        for c in self.items:
            x0, y0, x1, y1 = c["bbox"]
            cand = np.where(~done & (lon >= x0) & (lon <= x1) & (lat >= y0) & (lat <= y1))[0]
            if not len(cand):
                continue
            ins = np.zeros(len(cand), bool)
            for ring in c["rings"]:
                ins ^= _pip(lon[cand], lat[cand], ring)
            hit = cand[ins]
            if len(hit):
                done[hit] = True
                if c["state"] == "48":
                    out[hit] = c["name"]
        return out

    def dist_km(self, lat, lon, county, own=None):
        """Approximate distance (km) from a point to a TX county (0 if inside; boundary-vertex distance otherwise).

        `own` is the point's already-known county, which skips the polygon test."""
        county = (county or "").upper()
        if county not in self._tx_tree:
            return np.nan
        if (own if own is not None else self.lookup([lat], [lon])[0]) == county:
            return 0.0
        d, _ = self._tx_tree[county].query(to_xy_km([lat], [lon]))
        return float(d[0])


# ------------------------------------------------------------------ loaders --------------------------------------
def _latest(pattern, folder):
    fs = sorted(Path(folder).glob(pattern))
    return fs[-1] if fs else None


def sp_file():
    """Newest NP4-160 settlement-point list: 1a's copy if it exists, else the Sep 2026 archive copy."""
    cand = REPO_DATA / "settlement_points.csv"
    if cand.exists() and cand.stat().st_size > 1000:
        return cand
    return SP_MAPPING_SEP2026


def load_ercot():
    """Returns (sp, r2u, noie) as string frames (keep_default_na=False: the substation code 'NA' is a real code)."""
    f = sp_file()
    sp = pd.read_csv(f, dtype=str, keep_default_na=False)
    sp.columns = [c.upper() for c in sp.columns]
    sp["KV"] = pd.to_numeric(sp["VOLTAGE_LEVEL"], errors="coerce")
    folder = SP_MAPPING_SEP2026.parent
    r2u = pd.read_csv(_latest("Resource_Node_to_Unit_*.csv", folder), dtype=str, keep_default_na=False)
    noie = pd.read_csv(_latest("NOIE_Mapping_*.csv", folder), dtype=str, keep_default_na=False)
    return sp, r2u, noie


def load_cdr():
    """ERCOT CDR unit list: unit code (= UNIT_SUBSTATION + '_' + UNIT_NAME), readable name, county, tech, MW."""
    f = _latest("ERCOT_CDR_*.xlsx", DOCS_DIR)
    cols = ["unit_code", "unit_name", "county", "tech", "fuel", "mw", "status", "zone", "inr"]
    if f is None:
        return pd.DataFrame(columns=cols)
    d = pd.read_excel(f, "Unit Details", header=2)
    d.columns = [str(c).replace("\n", " ").strip() for c in d.columns]
    out = pd.DataFrame({
        "unit_code": d["UNIT CODE"].astype(str).str.strip(), "unit_name": d["UNIT NAME"].astype(str).str.strip(),
        "county": d["COUNTY"].astype(str).str.upper().str.strip(), "tech": d["TECHNOLOGY"].astype(str), "fuel": d["FUEL"].astype(str),
        "mw": pd.to_numeric(d["INSTALLED CAPACITY (MW)"], errors="coerce"), "status": d["CDR STATUS"].astype(str),
        "zone": d["ZONE"].astype(str), "inr": d.iloc[:, 1].astype(str)})
    out = out[out.unit_code.notna() & (out.unit_code != "nan") & ~out.unit_code.str.endswith("_UNAVAIL")]
    return out.drop_duplicates(["unit_code", "tech", "fuel"]).reset_index(drop=True)


def load_gis():
    """ERCOT GIS report (PG7-200-ER): project name, county, POI text (often with PSS/E bus numbers)."""
    f = _latest("*GIS_Report_*.xlsx", DOCS_DIR)
    if f is None:
        return pd.DataFrame(columns=["inr", "project", "poi", "county", "fuel", "tech", "mw", "entity"])
    a = pd.read_excel(f, "Project Details - Large Gen", header=30)
    b = pd.read_excel(f, "Project Details - Small Gen", header=14)
    rows = []
    for d in (a, b):
        d = d[d["INR"].notna() & d["POI Location"].notna()]
        rows.append(pd.DataFrame({"inr": d["INR"].astype(str), "project": d["Project Name"].astype(str),
                                  "poi": d["POI Location"].astype(str), "county": d["County"].astype(str).str.upper().str.strip(),
                                  "fuel": d["Fuel"].astype(str), "tech": d["Technology"].astype(str),
                                  "mw": pd.to_numeric(d["Capacity (MW)"], errors="coerce"),
                                  "entity": d["Interconnecting Entity"].astype(str)}))
    return pd.concat(rows, ignore_index=True)


def load_eia():
    """EIA-860 plants + generators and EIA-860M operating/planned, Texas only. Returns (plants, gens)."""
    zf = _latest("eia860*.zip", EIA_DIR)
    z = zipfile.ZipFile(zf)
    pl = pd.read_excel(z.open(next(n for n in z.namelist() if n.startswith("2___Plant"))), header=1)
    pl = pl[pl["State"] == "TX"]
    plants = pd.DataFrame({
        "plant_code": pl["Plant Code"].astype(int), "name": pl["Plant Name"].astype(str), "lat": pd.to_numeric(pl["Latitude"], errors="coerce"),
        "lon": pd.to_numeric(pl["Longitude"], errors="coerce"), "county": pl["County"].astype(str).str.upper().str.strip(),
        "utility": pl["Utility Name"].astype(str), "ba": pl["Balancing Authority Code"].astype(str),
        "tdsp": pl["Transmission or Distribution System Owner"].astype(str),
        "grid_kv": pd.to_numeric(pl["Grid Voltage (kV)"], errors="coerce"), "grid_kv2": pd.to_numeric(pl["Grid Voltage 2 (kV)"], errors="coerce"),
        "grid_kv3": pd.to_numeric(pl["Grid Voltage 3 (kV)"], errors="coerce"), "src": "eia860"})
    g = pd.read_excel(z.open(next(n for n in z.namelist() if n.startswith("3_1_Generator"))), header=1)
    g = g[g["State"] == "TX"]
    gens = pd.DataFrame({
        "plant_code": g["Plant Code"].astype(int), "gen_id": g["Generator ID"].astype(str), "tech": g["Technology"].astype(str),
        "mw": pd.to_numeric(g["Nameplate Capacity (MW)"], errors="coerce"), "status": g["Status"].astype(str),
        "op_year": pd.to_numeric(g["Operating Year"], errors="coerce"), "op_month": pd.to_numeric(g["Operating Month"], errors="coerce"),
        "lmp_node": g["RTO/ISO LMP Node Designation"].astype(str).str.strip(), "mwh": np.nan, "source": "eia860"})
    gens.loc[gens.lmp_node.isin(["nan", ""]), "lmp_node"] = ""
    # energy storage sheet adds MWh for the annual file
    try:
        es = pd.read_excel(z.open(next(n for n in z.namelist() if n.startswith("3_4_Energy"))), header=1)
        es = es[es["State"] == "TX"]
        key = {(int(a), str(b)): c for a, b, c in zip(es["Plant Code"], es["Generator ID"], pd.to_numeric(es["Nameplate Energy Capacity (MWh)"], errors="coerce"))}
        gens["mwh"] = [key.get((int(a), str(b)), np.nan) for a, b in zip(gens.plant_code, gens.gen_id)]
    except (StopIteration, KeyError):
        pass
    f860m = _latest("*_generator20*.xlsx", EIA_DIR)
    extra_p, extra_g = [], []
    if f860m is not None:
        x = pd.ExcelFile(f860m)
        for sheet in ("Operating", "Planned"):
            d = x.parse(sheet, header=2)
            d = d[d["Plant State"] == "TX"]
            ycol, mcol = ("Operating Year", "Operating Month") if sheet == "Operating" else ("Planned Operation Year", "Planned Operation Month")
            extra_g.append(pd.DataFrame({
                "plant_code": d["Plant ID"].astype(int), "gen_id": d["Generator ID"].astype(str), "tech": d["Technology"].astype(str),
                "mw": pd.to_numeric(d["Nameplate Capacity (MW)"], errors="coerce"), "status": d["Status"].astype(str),
                "op_year": pd.to_numeric(d[ycol], errors="coerce"), "op_month": pd.to_numeric(d[mcol], errors="coerce"), "lmp_node": "",
                "mwh": pd.to_numeric(d["Nameplate Energy Capacity (MWh)"], errors="coerce") if "Nameplate Energy Capacity (MWh)" in d else np.nan,
                "source": "eia860m_" + sheet.lower()}))
            extra_p.append(pd.DataFrame({
                "plant_code": d["Plant ID"].astype(int), "name": d["Plant Name"].astype(str), "lat": pd.to_numeric(d["Latitude"], errors="coerce"),
                "lon": pd.to_numeric(d["Longitude"], errors="coerce"), "county": d["County"].astype(str).str.upper().str.strip(),
                "utility": d["Entity Name"].astype(str), "ba": d["Balancing Authority Code"].astype(str), "tdsp": "", "grid_kv": np.nan,
                "grid_kv2": np.nan, "grid_kv3": np.nan, "src": "eia860m"}))
        ep = pd.concat(extra_p).drop_duplicates("plant_code").set_index("plant_code")
        miss = plants.lat.isna() | plants.lon.isna()
        for i in plants.index[miss]:
            pc = plants.at[i, "plant_code"]
            if pc in ep.index:
                plants.at[i, "lat"], plants.at[i, "lon"] = ep.at[pc, "lat"], ep.at[pc, "lon"]
        new = ep[~ep.index.isin(plants.plant_code)].reset_index()
        plants = pd.concat([plants, new], ignore_index=True)
        gens = pd.concat([gens, *extra_g], ignore_index=True)
    plants["ba"] = plants["ba"].replace({"nan": ""})
    return plants.reset_index(drop=True), gens.reset_index(drop=True)


def _kv_list(v):
    """OSM voltage tag ('138000;69000') -> sorted list of kV floats (>0), largest first."""
    out = []
    for tok in re.split(r"[;,/ ]+", str(v or "")):
        tok = tok.strip().lower().replace("kv", "")
        try:
            x = float(tok)
        except ValueError:
            continue
        if x >= 1000:
            x = x / 1000.0
        if 0.2 <= x <= 1000:
            out.append(round(x, 2))
    return sorted(set(out), reverse=True)


def load_osm_substations():
    j = json.loads((OSM_DIR / "substations.json").read_text(encoding="utf-8"))
    rows = []
    for e in j["elements"]:
        t = e.get("tags", {})
        c = e.get("center") or {"lat": e.get("lat"), "lon": e.get("lon")}
        if c["lat"] is None:
            continue
        kv = _kv_list(t.get("voltage"))
        rows.append({
            "osm_id": e["type"][0] + str(e["id"]), "name": t.get("name") or t.get("official_name") or t.get("alt_name") or "",
            "ref": t.get("ref", ""), "voltage_raw": t.get("voltage", ""), "kv_list": kv, "kv_max": max(kv) if kv else np.nan,
            "operator": t.get("operator") or t.get("owner") or "", "sub_type": t.get("substation", ""), "lat": c["lat"], "lon": c["lon"]})
    return pd.DataFrame(rows)


def load_osm_batteries():
    """OSM battery plants / generators (power=plant|generator with battery source): name, centre, operator. Used as name-match targets."""
    f = OSM_DIR / "batteries.json"
    if not f.exists():
        return pd.DataFrame(columns=["osm_id", "name", "ref", "voltage_raw", "kv_list", "kv_max", "operator", "sub_type", "lat", "lon"])
    rows = []
    for e in json.loads(f.read_text(encoding="utf-8"))["elements"]:
        t = e.get("tags", {})
        c = e.get("center") or {"lat": e.get("lat"), "lon": e.get("lon")}
        if c["lat"] is None or not t.get("name"):
            continue
        rows.append({"osm_id": e["type"][0] + str(e["id"]), "name": t["name"], "ref": "", "voltage_raw": "", "kv_list": [], "kv_max": np.nan,
                     "operator": t.get("operator", ""), "sub_type": "battery_plant", "lat": c["lat"], "lon": c["lon"]})
    return pd.DataFrame(rows)


def load_hifld():
    """HIFLD substations and lines (Texas bbox extracts of the ArcGIS mirrors)."""
    fs, fl = GEO_DIR / "hifld_substations_tx.geojson", GEO_DIR / "hifld_lines_tx.geojson"
    subs = lines = None
    if fs.exists():
        j = json.loads(fs.read_text(encoding="utf-8"))
        rows = []
        for f in j["features"]:
            p = f["properties"]
            lon, lat = f["geometry"]["coordinates"][:2]
            kv = [v for v in (p.get("MAX_VOLT"), p.get("MIN_VOLT")) if v and v > 0]
            rows.append({"hifld_id": str(p["ID"]), "name": p["NAME"] or "", "city": p.get("CITY") or "", "state": p.get("STATE"),
                         "county": (p.get("COUNTY") or "").upper(), "type": p.get("TYPE"), "kv_max": max(kv) if kv else np.nan,
                         "kv_list": sorted(set(kv), reverse=True), "n_lines": p.get("LINES"), "lat": lat, "lon": lon})
        subs = pd.DataFrame(rows)
        subs["named"] = ~subs.name.str.match(r"^(UNKNOWN|TAP)\d*$") & (subs.name != "")
    if fl.exists():
        j = json.loads(fl.read_text(encoding="utf-8"))
        rows = []
        for f in j["features"]:
            p, g = f["properties"], f["geometry"]
            if not g:
                continue
            segs = g["coordinates"] if g["type"] == "MultiLineString" else [g["coordinates"]]
            coords = [pt for s in segs for pt in s]
            v = p.get("VOLTAGE")
            rows.append({"hifld_line_id": str(p["ID"]), "sub_1": p.get("SUB_1") or "", "sub_2": p.get("SUB_2") or "",
                         "kv": v if v and v > 0 else np.nan, "owner": p.get("OWNER") or "", "status": p.get("STATUS") or "",
                         "lon": [c[0] for c in coords], "lat": [c[1] for c in coords]})
        lines = pd.DataFrame(rows)
    return subs, lines


# ------------------------------------------------------------------ ERCOT-side tables ----------------------------
GENERIC_WORDS = {
    "substation", "sub", "station", "stn", "switching", "switchyard", "switch", "tap", "electric", "electrical", "transmission",
    "distribution", "power", "plant", "ss", "sw", "the", "of", "and", "energy", "center", "centre", "facility", "company", "co",
    "inc", "llc", "lp", "project", "generating", "generation", "gen", "unit", "units", "u", "wind", "solar", "farm", "ranch", "bess",
    "storage", "battery", "ess", "esr", "slr", "pv", "cc", "ct", "gt", "st", "ic", "dc", "tie", "ehv", "poi", "xfmr", "bus",
}
COMPASS = {"n": "north", "s": "south", "e": "east", "w": "west", "ne": "northeast", "nw": "northwest", "se": "southeast",
           "no": "north", "so": "south", "mt": "mount", "ft": "fort", "lk": "lake", "pt": "point", "jct": "junction",
           "hwy": "highway", "rd": "road", "cr": "creek", "cnty": "county", "spgs": "springs", "spg": "springs", "hts": "heights",
           "vly": "valley", "cty": "city", "pk": "park", "pkwy": "parkway"}


def name_tokens(s, drop_generic=True):
    """Lowercase alphanumeric tokens; punctuation split; generic substation words dropped; compass abbreviations expanded."""
    s = re.sub(r"\(.*?\)", " ", str(s))
    out = []
    for t in re.findall(r"[a-z]+|\d+", s.lower()):
        t = COMPASS.get(t, t)
        if drop_generic and t in GENERIC_WORDS:
            continue
        out.append(t)
    return out


def letters(s):
    return re.sub(r"[^a-z]", "", str(s).lower())


def is_subseq(code, name):
    """True if `code` is a subsequence of `name` (both lowercase letters only)."""
    it = iter(name)
    return all(ch in it for ch in code)


def tech_class(tech, fuel=""):
    """CDR / GIS / EIA technology -> coarse class used to pair units with plants.

    CDR and GIS use short codes (BA, PV, WT, CC, GT, ST, IC, HY and fuel SOL, WIN, NUC, HYD); EIA uses long names.
    """
    tk, fl = str(tech).strip().lower(), str(fuel).strip().lower()
    t = tk + " " + fl
    if tk in ("ba", "mwh") or "batter" in t or "storage" in t or "flywheel" in t:
        return "battery"
    if tk == "pv" or fl == "sol" or "solar" in t:
        return "solar"
    if tk == "wt" or fl == "win" or "wind" in t:
        return "wind"
    if tk == "hy" or fl in ("hyd", "wat") or "hydro" in t:
        return "hydro"
    if fl == "nuc" or "nuclear" in t:
        return "nuclear"
    return "thermal"


def class_from_code(code):
    """Class hint from an ERCOT sub / resource node code suffix (GAIA_SLR -> solar), or '' when the code says nothing."""
    c = str(code).upper()
    if re.search(r"(_SLR|SOLAR|_PV|SLR_|_SOL)", c):
        return "solar"
    if re.search(r"(_ESS|BESS|_ESR|ESS_|BES_|BATT|_BAT)", c):
        return "battery"
    if re.search(r"(_WND|WIND|_WT)", c):
        return "wind"
    return ""


def build_ercot_subs(sp, r2u, noie):
    """One row per ERCOT SUBSTATION code with kV, zone and resource-node facts."""
    sp = sp.copy()
    sp["has_rn"] = sp["RESOURCE_NODE"] != ""
    u_by_sub = r2u.groupby("UNIT_SUBSTATION")["RESOURCE_NODE"].apply(set).to_dict()
    rows = []
    for sub, d in sp.groupby("SUBSTATION"):
        kvs = sorted({k for k in d.KV.dropna() if k > 0}, reverse=True)
        rn = set(d.loc[d.has_rn, "RESOURCE_NODE"]) | u_by_sub.get(sub, set())
        rows.append({
            "sub": sub, "n_buses": len(d), "kv_max": kvs[0] if kvs else np.nan,
            "kv_list": ";".join(f"{k:g}" for k in kvs), "kv_set": kvs,
            "zone": d.SETTLEMENT_LOAD_ZONE.mode().iat[0] if len(d) else "",
            "has_resource_node": bool(rn), "n_resource_nodes": len(rn), "resource_nodes": sorted(rn),
            "buses": sorted(d.ELECTRICAL_BUS), "psse_names": sorted(set(d.PSSE_BUS_NAME)),
            "psse_nums": sorted({int(x) for x in d.PSSE_BUS_NUMBER if x.isdigit()}),
            "hub": ";".join(sorted(set(d.HUB) - {""})),
        })
    subs = pd.DataFrame(rows).set_index("sub", drop=False)
    # NOIE (municipal / co-op load zone) membership, a coarse regional prior (AEN = Austin, CPS = San Antonio, ...)
    nz = noie.groupby("SUBSTATION")["NOIE"].agg(lambda s: ";".join(sorted(set(s))))
    subs["noie_zone"] = nz.reindex(subs.index).fillna("")
    return subs


def assign_cdr_to_subs(cdr, subs):
    """Attach each CDR unit to the longest ERCOT sub code that prefixes its unit code (AEEC_ANTLP_1 -> AEEC)."""
    codes = set(subs.index)

    def assign(code):
        parts = code.split("_")
        best = None
        for k in range(1, len(parts)):
            p = "_".join(parts[:k])
            if p in codes:
                best = p
        return best

    out = cdr.copy()
    out["sub"] = out.unit_code.map(assign)
    out["cls"] = [tech_class(t, f) for t, f in zip(out.tech, out.fuel)]
    return out[out["sub"].notna()].copy()


KV_PLAIN = {12, 13, 14, 34, 35, 69, 115, 138, 161, 230, 345, 500, 765}   # voltages that appear as bare numbers in POI text
_SEG_SPLIT = re.compile(r"\s+(?:-|–|—|�|to|and)\s+|;|,|\n|\btap\b", re.I)
_BUSNUM = re.compile(r"(?<![\d.])(\d{3,6})(?![\d.])(?!\s*[kK][vV])")


def gis_bus_hits(gis, sp):
    """GIS projects whose POI text names a PSS/E bus number that exists in NP4-160. One row per (project, bus).

    The POI text looks like '1444 BROWN SWITCH', 'Tap 345kV 3109 Stryker - 3123 Trinidad' or 'Bluff Creek (Bus #60040)'.
    `name` is the readable words attached to that number; `is_tap` marks projects that tap a line between two buses
    (their county is then only loosely tied to either end).
    """
    busnum = {}
    for n, s_ in zip(sp.PSSE_BUS_NUMBER, sp.SUBSTATION):
        busnum.setdefault(n, set()).add(s_)
    rows = []
    for r in gis.itertuples():
        txt = r.poi.replace("\n", " ")
        nums_all = [m for m in _BUSNUM.findall(txt) if int(m) not in KV_PLAIN]
        is_tap = len(set(nums_all)) >= 2 or bool(re.search(r"\btap\b", txt, re.I))
        for seg in [x for x in _SEG_SPLIT.split(txt) if x and x.strip()]:
            nums = [m for m in _BUSNUM.findall(seg) if int(m) not in KV_PLAIN]
            if len(nums) != 1:
                continue
            n = nums[0]
            subs_ = busnum.get(n)
            if not subs_ or len(subs_) != 1:
                continue
            nm = re.sub(r"\(.*?\)|#?\d+\s*[kK][vV]|\bkv\b|\bbus\b|[#()]", " ", seg, flags=re.I)
            nm = re.sub(r"(?<![\d.])%s(?![\d.])" % n, " ", nm)
            nm = re.sub(r"_?\d+[A-Za-z]?\b", " ", nm)
            nm = re.sub(r"\s+", " ", nm).strip(" -_.:")
            rows.append({"inr": r.inr, "project": r.project, "poi": txt, "bus": n, "sub": next(iter(subs_)), "county": r.county,
                         "fuel": r.fuel, "tech": r.tech, "mw": r.mw, "is_tap": is_tap, "name": nm})
    return pd.DataFrame(rows)


# ------------------------------------------------------------------ reference pool + name matching ---------------
ABBREV_MIN_COV_INITIALS = 0.3   # ASSUMPTION: minimum code/name length ratio for an abbreviation that hits every word initial (NLAR ~ North Laredo)
REF_TDSP = {"CenterPoint": ("LZ_HOUSTON", "LZ_SOUTH"), "Austin Energy": ("LZ_SOUTH",), "CPS": ("LZ_SOUTH",)}   # REAL: operators whose OSM `ref` tag is the ERCOT station code (189 checked), with the load zones they serve
OPERATOR_ZONE_F = 0.35         # ASSUMPTION: factor when an OSM operator that serves one city (CenterPoint, Austin Energy, CPS) sits outside the sub's load zone
REF_TDSP_SIM = 0.95            # ASSUMPTION: name similarity credited for an exact `ref` == ERCOT code on those operators
REF_OTHER_SIM = 0.4            # ASSUMPTION: same for other operators, or an operator outside its own load zone (CenterPoint and Austin Energy reuse short codes)
PSSE_NOISE = re.compile(r"^(\d+[A-Z]?|\d*[A-Z]\d+[A-Z]?\d*|POI|TXU|SW|BB\d*|[A-Z]?\d{1,2})$")


def psse_stem(name):
    """PSS/E bus name -> readable-ish stem: 'ELM_MOTT__8' -> 'ELM MOTT', 'MAXWELL4A' -> 'MAXWELL', 'L_BUCKRA8_1Y' -> 'BUCKRA'."""
    s = str(name).upper().strip()
    s = re.sub(r"^L_(?=[A-Z])", "", s)
    parts = [p for p in re.split(r"_+", s) if p]
    keep = []
    for i, p in enumerate(parts):
        if i > 0 and PSSE_NOISE.match(p):
            continue
        keep.append(re.sub(r"\d[A-Z]?$", "", p) if not p.isdigit() else "")
    return " ".join(k for k in keep if k).strip()


def sim_abbrev(code, ref_tokens):
    """How well an ERCOT code/stem (letters) abbreviates a reference name (tokens). 0..0.9."""
    rl = "".join(re.sub(r"[^a-z]", "", t) for t in ref_tokens)
    if not rl or len(code) < 3:
        return 0.0
    if code == rl:
        return 1.0
    if len(code) >= 4 and len(rl) >= 4 and (rl.startswith(code) or code.startswith(rl)):
        return 0.85 if min(len(code), len(rl)) / max(len(code), len(rl)) >= 0.5 else 0.7
    if len(code) >= 4 and code[0] == rl[0] and is_subseq(code, rl):
        cov = len(code) / len(rl)
        inits = "".join(t[0] for t in ref_tokens if t and t[0].isalpha())
        init_ok = len(ref_tokens) > 1 and is_subseq(inits, code)
        if cov >= 0.45 or (init_ok and cov >= ABBREV_MIN_COV_INITIALS):
            return min(0.8, 0.5 + 0.35 * cov + (0.05 if init_ok else 0.0))
    return 0.0


def _char_ratio(a, b):
    from difflib import SequenceMatcher
    return SequenceMatcher(None, a, b).ratio()


def sim_read(var_tokens, ref_tokens):
    """Readable-name similarity between two token lists (0..1)."""
    if not var_tokens or not ref_tokens:
        return 0.0
    a, b = "".join(var_tokens), "".join(ref_tokens)
    if a == b:
        return 1.0
    sa, sb = set(var_tokens), set(ref_tokens)
    short, long_ = (sa, sb) if len(sa) <= len(sb) else (sb, sa)
    dist = [t for t in short if len(t) >= 4 or not t.isalpha()]
    if short <= long_ and dist:
        return 0.9 if len(short) == len(long_) else 0.8
    inter = len(sa & sb)
    if inter:
        return 0.75 * inter / len(sa | sb) + 0.1
    r = _char_ratio(a, b)
    return 0.9 * r if r >= 0.85 and len(a) >= 5 else 0.0


def build_pool(osm, hifld_subs, counties, batteries=None):
    """Reference substations with names: OSM (power=substation), HIFLD (named only) and OSM battery plants (src osm_plant)."""
    o = osm.copy()
    o["ref_id"] = o.osm_id
    o["src"] = "osm"
    o["county"] = counties.lookup(o.lat.values, o.lon.values)
    o["n_lines"] = np.nan
    h = hifld_subs[hifld_subs.state.eq("TX") & hifld_subs.named].copy()
    h["ref_id"] = "h" + h.hifld_id
    h["src"] = "hifld"
    h["operator"] = ""
    h["sub_type"] = h["type"].str.lower()
    h["ref"] = ""
    parts = [o, h]
    if batteries is not None and len(batteries):
        b = batteries.copy()
        b["ref_id"] = b.osm_id
        b["src"] = "osm_plant"
        b["county"] = counties.lookup(b.lat.values, b.lon.values)
        b["n_lines"] = np.nan
        parts.append(b)
    pool = pd.concat(parts, ignore_index=True)
    pool["tokens"] = [name_tokens(n) for n in pool.name]
    pool["tokens_full"] = [name_tokens(n, drop_generic=False) for n in pool.name]
    pool["letters"] = ["".join(letters(t) for t in tk) for tk in pool.tokens]
    pool = pool.reset_index(drop=True)
    pool["has_name"] = pool.letters.str.len() >= 3
    return pool


class NameIndex:
    """Retrieval over the reference pool: first-letter buckets (abbreviations), token inverted index, char n-gram tf-idf,
    and the OSM `ref` tag (CenterPoint / Austin Energy / CPS station codes equal the ERCOT codes)."""

    def __init__(self, pool):
        from sklearn.feature_extraction.text import TfidfVectorizer
        self.pool = pool
        self.letters = pool.letters.tolist()
        self.tokens = pool.tokens.tolist()
        self.refs = pool.ref.tolist()
        self.oper = pool.operator.tolist()
        self.ok = np.where(pool.has_name.values)[0]
        self.by_first = defaultdict(list)
        self.by_tok = defaultdict(list)
        self.by_ref = defaultdict(list)
        for i in self.ok:
            self.by_first[self.letters[i][0]].append(i)
            for t in set(self.tokens[i]):
                if len(t) >= 3 and t.isalpha():
                    self.by_tok[t].append(i)
        for i, r in enumerate(self.refs):
            if r and pool.src.iat[i] == "osm":
                self.by_ref[letters(r)].append(i)
        self.vec = TfidfVectorizer(analyzer="char_wb", ngram_range=(2, 4), lowercase=True)
        self.mat = self.vec.fit_transform([self.letters[i] for i in self.ok])

    def candidates(self, kind, text, k=12):
        """Indices into the pool that might match one ERCOT name variant."""
        toks = name_tokens(text)
        code = letters(text)
        out = set()
        if kind == "code" and len(code) >= 2:
            out.update(self.by_ref.get(code, ()))
        if len(code) >= 3:
            if kind in ("code", "psse"):
                for i in self.by_first.get(code[0], []):
                    rl = self.letters[i]
                    if (len(code) >= 4 and is_subseq(code, rl)) or rl == code:
                        out.add(i)
                    elif len(code) >= 4 and len(rl) >= 4 and (rl.startswith(code) or code.startswith(rl)):
                        out.add(i)
            for t in toks:
                if len(t) >= 3 and t.isalpha():
                    out.update(self.by_tok.get(t, ()))
            if not out or kind == "read":
                q = self.vec.transform([code])
                sims = (self.mat @ q.T).toarray().ravel()
                top = np.argsort(-sims)[:k]
                out.update(int(self.ok[j]) for j in top if sims[j] >= 0.45)
        return out

    def score(self, kind, text, i, zone=""):
        ref_tokens = self.tokens[i]
        if kind in ("code", "psse"):
            code = letters(text)
            s = sim_abbrev(code, ref_tokens)
            toks = name_tokens(text)
            if len(toks) > 1:    # 'S MISSIN' -> also try the expanded compass words
                s = max(s, sim_abbrev(letters("".join(toks)), ref_tokens))
            if kind == "code" and self.refs[i] and letters(self.refs[i]) == code and len(code) >= 2:
                zones = next((z for t, z in REF_TDSP.items() if t in self.oper[i]), None)
                s = max(s, REF_TDSP_SIM if (zones and (not zone or zone in zones)) else REF_OTHER_SIM)
            return s
        return sim_read(name_tokens(text), ref_tokens)


# ------------------------------------------------------------------ method (a): ERCOT unit -> EIA plant ------------
CLASS_PRIOR = {"thermal": 0.85, "nuclear": 0.9, "hydro": 0.8, "battery": 0.75, "solar": 0.6, "wind": 0.55}   # ASSUMPTION: P(plant point is the right site)
EIA_MIN_SCORE = 0.5            # ASSUMPTION: minimum combined name / capacity score to accept a plant in the same county
EIA_NAME_ONLY_MIN = 0.8        # ASSUMPTION: minimum name similarity when no plant in the evidence county fits (county mismatch / unknown)
EIA_NAME_ONLY_PRIOR = 0.7      # ASSUMPTION: confidence multiplier for name-only (county-free) plant matches
EIA_BAD_COORD_F = 0.5          # ASSUMPTION: confidence factor for an EIA plant whose coordinates fall outside its own stated county (EIA data error)
EIA_GEN_TIE_F = 0.7             # ASSUMPTION: confidence factor when the plant is reached through a resource node at a POI sub (gen-tie distance)
EIA_SAME_SITE_KM = 4.0         # ASSUMPTION: EIA plants closer than this are one site (co-located solar + battery)
EIA_RIVAL_GAP = 0.1            # ASSUMPTION: a second plant scoring within this of the best halves trust
UNIT_WORDS = re.compile(r"\b(u\d*|unit\d*|ctg?\d*|stg\d*|gt\d*|st\d*|ic\d*|bess\d*|bes\d*|esr\d*|ess|slf|ph|phase|i{1,3}|iv|\d+)\b", re.I)


def plant_class_mw(gens):
    """EIA plant -> {class: MW}. Annual EIA-860 and EIA-860M overlap, so per plant keep the source group with more MW."""
    out = {}
    g = gens.assign(grp=np.where(gens.source.eq("eia860"), "annual", "monthly"))
    for (pc, _grp), d in g.groupby(["plant_code", "grp"]):
        m = defaultdict(float)
        for tech, mw in zip(d.tech, d.mw.fillna(0)):
            m[tech_class(tech)] += float(mw)
        out.setdefault(pc, []).append(dict(m))
    return {pc: max(v, key=lambda m: sum(m.values())) for pc, v in out.items()}


def sub_unit_records(subs, cdr_units, gis_hits, r2u=None, sp=None):
    """Per ERCOT sub: unit records {name, county, cls, mw, strong, remote} from the CDR and from GIS projects tied to the sub's buses.

    With `r2u` and `sp`, a sub that carries a resource node whose units are registered at another sub (a POI switchyard
    for a wind / solar / battery plant) also receives those units as `remote` records (resource node -> unit -> plant).
    """
    recs = defaultdict(list)
    for r in cdr_units.itertuples():
        recs[r.sub].append(dict(name=r.unit_name, county=r.county, cls=r.cls, mw=0.0 if pd.isna(r.mw) else float(r.mw), strong=True,
                                src="cdr", unit_code=r.unit_code, remote=False))
    for r in gis_hits.itertuples():
        recs[r.sub].append(dict(name=r.project, county=r.county, cls=tech_class(r.tech, r.fuel), mw=0.0 if pd.isna(r.mw) else float(r.mw),
                                strong=not r.is_tap, src="gis", unit_code="", remote=False))
    if r2u is not None and sp is not None:
        node_units = r2u.groupby("RESOURCE_NODE").UNIT_SUBSTATION.apply(set).to_dict()
        sp_nodes = sp[sp.RESOURCE_NODE != ""].groupby("SUBSTATION").RESOURCE_NODE.apply(set).to_dict()
        own = {k: list(v) for k, v in recs.items()}
        for sub, nodes in sp_nodes.items():
            for n in nodes:
                for us in node_units.get(n, ()):
                    if us != sub:
                        for u in own.get(us, []):
                            recs[sub].append(dict(u, strong=False, remote=True, src="remote_" + u["src"]))
    return recs


def _clean_unit_name(n):
    return [t for t in name_tokens(UNIT_WORDS.sub(" ", str(n))) if not t.isdigit() and len(t) > 1]


def _score_plant(p, pcm, mw_cls, total_sub, names, codes, counties_ev, direct, class_hint):
    pm = pcm.get(p.plant_code, {})
    ptot = sum(pm.values()) or 1.0
    overlap = sum(min(mw_cls.get(c, 0.0), pm.get(c, 0.0)) for c in set(mw_cls) | set(pm)) / max(total_sub, ptot) if mw_cls else 0.0
    cls_ok = (any(c in pm for c in mw_cls) if mw_cls else (not class_hint or class_hint in pm))
    nm = 0.0
    for toks in names:
        nm = max(nm, sim_read(list(toks), p.tokens))
    for code in codes:
        if len(code) >= 3:
            nm = max(nm, sim_abbrev(code, p.tokens) * 0.9)
    return pm, overlap, cls_ok, nm


def match_eia(subs, recs, plants, gens, counties):
    """Candidate coordinates for ERCOT subs from EIA plants. Returns {sub: [candidate dicts]}.

    Pass 1: plants in the unit's county (CDR / GIS county), score = 0.45 capacity overlap + 0.45 name + 0.10 county.
    Pass 2 (nothing fits, or no county known): any Texas plant whose name matches strongly (EIA_NAME_ONLY_MIN).
    A plant whose 'RTO/ISO LMP Node Designation' equals the ERCOT unit code or resource node is a direct hit.
    """
    pcm = plant_class_mw(gens)
    lmp = defaultdict(set)
    for r in gens.itertuples():
        if isinstance(r.lmp_node, str) and r.lmp_node:
            lmp[r.lmp_node.upper()].add(r.plant_code)
    pl = plants[plants.lat.notna() & plants.lon.notna()].copy()
    pl["tokens"] = [[t for t in name_tokens(n) if not t.isdigit()] for n in pl.name]
    pip = counties.lookup(pl.lat.values, pl.lon.values)
    pl["coord_ok"] = [(p_ == c) or (counties.dist_km(la, lo, c, own=p_) <= COUNTY_NEAR_KM) if c in counties._tx_tree else True
                      for p_, c, la, lo in zip(pip, pl.county, pl.lat, pl.lon)]
    by_county = {c: d for c, d in pl.groupby("county")}
    pl_rows = list(pl.itertuples())
    pl_letters = ["".join(letters(t) for t in r.tokens) for r in pl_rows]
    tok_idx, first_idx = defaultdict(list), defaultdict(list)
    for i, r in enumerate(pl_rows):
        for t in set(r.tokens):
            if len(t) >= 3:
                tok_idx[t].append(i)
        if pl_letters[i]:
            first_idx[pl_letters[i][0]].append(i)
    out = {}
    for sub in subs.index:
        s = subs.loc[sub]
        rows = recs.get(sub, [])
        class_hint = class_from_code(sub) or next((class_from_code(r) for r in s.resource_nodes if class_from_code(r)), "")
        if not rows and not s.has_resource_node:
            continue
        all_remote = bool(rows) and all(u.get("remote") for u in rows)
        mw_cls, cw = defaultdict(float), defaultdict(float)
        for u in rows:
            mw_cls[u["cls"]] += u["mw"]
            cw[u["county"]] += (u["mw"] + 1.0) * (1.0 if u["strong"] else 0.4)
        total_sub = sum(mw_cls.values()) or 1.0
        counties_ev = [c for c, _ in sorted(cw.items(), key=lambda kv: -kv[1])][:2]
        names = {tuple(_clean_unit_name(u["name"])) for u in rows} - {()}
        stripped = re.sub(r"_(ESS|BESS|SLR|WND|WIND|SOLAR|PV|RN|ALL)$", "", sub)
        codes = {letters(sub), letters(stripped)}
        direct = set()
        for u in rows:
            if u["unit_code"]:
                direct |= lmp.get(u["unit_code"].upper(), set())
        for rn in s.resource_nodes:
            direct |= lmp.get(str(rn).upper(), set())
        scored = []
        pools = [by_county[c] for c in counties_ev if c in by_county]
        pools += [pl[pl.plant_code.isin(direct)]] if direct else []
        if pools:
            for p in pd.concat(pools).drop_duplicates("plant_code").itertuples():
                pm, overlap, cls_ok, nm = _score_plant(p, pcm, mw_cls, total_sub, names, codes, counties_ev, direct, class_hint)
                is_direct = p.plant_code in direct
                score = 0.45 * overlap + 0.45 * nm + (0.1 if p.county in counties_ev[:1] else 0.0)
                if is_direct:
                    score = max(score, 0.95)
                if not cls_ok and not is_direct:
                    score *= 0.5
                if score >= EIA_MIN_SCORE:
                    scored.append((score, nm, overlap, is_direct, p, False))
        if not scored:      # pass 2: name only, any county (candidates by shared token or abbreviation prefix)
            cand_rows = set()
            for toks in names:
                for t in toks:
                    cand_rows.update(tok_idx.get(t, ()))
            for code in codes:
                if len(code) >= 3:
                    cand_rows.update(i for i in first_idx.get(code[0], ()) if is_subseq(code, pl_letters[i]) or pl_letters[i].startswith(code[:3]))
            for p in (pl_rows[i] for i in sorted(cand_rows)):
                pm, overlap, cls_ok, nm = _score_plant(p, pcm, mw_cls, total_sub, names, codes, counties_ev, direct, class_hint)
                if nm >= EIA_NAME_ONLY_MIN and (cls_ok or not (mw_cls or class_hint)):
                    scored.append((nm * 0.9, nm, overlap, False, p, True))
        if not scored:
            continue
        scored.sort(key=lambda t: -t[0])
        score, nm, overlap, is_direct, best, name_only = scored[0]
        same = [t for t in scored if haversine_km(best.lat, best.lon, t[4].lat, t[4].lon) <= EIA_SAME_SITE_KM]
        rival = [t for t in scored if t not in same]
        margin = score - (rival[0][0] if rival else 0.0)
        dom = max(mw_cls, key=mw_cls.get) if mw_cls else (class_hint or "thermal")
        conf = CLASS_PRIOR.get(dom, 0.6) * min(1.0, score / 0.8)
        if name_only:
            conf *= EIA_NAME_ONLY_PRIOR
        if all_remote:
            conf *= EIA_GEN_TIE_F
        if not best.coord_ok:
            conf *= EIA_BAD_COORD_F
        if is_direct:
            conf = max(conf, 0.92)
        elif rival and margin < EIA_RIVAL_GAP and nm < 0.9:
            conf *= 0.7
        out[sub] = [dict(method="eia_unit", lat=float(best.lat), lon=float(best.lon), conf=float(min(conf, 0.95)),
                         plant_codes=sorted({int(t[4].plant_code) for t in same}), county=best.county,
                         evidence=f"EIA plant {best.name} ({best.county}) score {score:.2f}" + (" LMP-node designation" if is_direct else "")
                         + (" name-only" if name_only else ""), grid_kv=best.grid_kv, tdsp=best.tdsp, cls=dom, n_rivals=len(rival),
                         plant_name=best.name, coord_ok=bool(best.coord_ok), direct=bool(is_direct))]
    return out


# ------------------------------------------------------------------ method (b): name / voltage / county -----------
NAME_MIN = 0.55                # ASSUMPTION: minimum name similarity to keep a reference substation as a candidate
VARIANT_WEIGHT = {"code": 0.9, "psse": 0.95, "gis": 1.0, "cdr": 0.85}   # ASSUMPTION: how much each name source is trusted
SRC_PRIOR = {"osm": 0.95, "hifld": 0.8, "osm_plant": 0.85}      # ASSUMPTION: OSM is current and surveyed by volunteers; HIFLD is a 2021-22 imagery snapshot
KV_MISMATCH_F = 0.6            # ASSUMPTION: factor when both sides list voltages and none agree
KV_UNKNOWN_F = 0.85            # ASSUMPTION: factor when the reference has no voltage tag
KV_LOWONLY_F = 0.9             # ASSUMPTION: factor when the ERCOT sub only has buses below 69 kV (plant terminals, collectors)
COUNTY_FAR_F = 0.25            # ASSUMPTION: factor when strong county evidence puts the reference outside the county
COUNTY_TAP_FAR_KM = 40         # ASSUMPTION: weak (line-tap) county evidence tolerates this distance
COUNTY_TAP_FAR_F = 0.5         # ASSUMPTION: factor when weak county evidence is violated
CLUSTER_KM = 3.0               # ASSUMPTION: OSM + HIFLD records closer than this are the same substation
UNIQ_RATIO_FREE = 0.7          # ASSUMPTION: a rival cluster scoring below 70% of the best does not hurt confidence
ZONE_ANCHOR_KM = 100           # ASSUMPTION: only verified anchors within this distance vote on the load zone of a candidate site
ZONE_VOTERS = 5                # ASSUMPTION: number of nearest verified anchors that vote
ZONE_FAR_F = 0.5               # ASSUMPTION: factor when none of the nearest anchors is in the sub's load zone (or none is near)
ANCHOR_MIN_CONF = 0.6          # ASSUMPTION: round-1 confidence needed for a *verified* sub (county-checked name match, or EIA plant) to anchor the priors
NB_FAR_F = 0.6                 # ASSUMPTION: factor for a candidate farther than NEIGHBOR_MAX_KM from every confident binding-constraint neighbour
MULTI_SRC_BONUS = 0.05         # ASSUMPTION: added when OSM and HIFLD both name the same site
NAME_CONF_CAP = 0.95           # ASSUMPTION: name matches are never certain


def kv_factor(kv_set, ref_kvs):
    tx = [k for k in kv_set if k >= 60]
    if not tx:
        return KV_LOWONLY_F
    if not ref_kvs:
        return KV_UNKNOWN_F
    for k in tx:
        for r in ref_kvs:
            if abs(k - r) <= KV_TOL * k:
                return 1.0
    return KV_MISMATCH_F


def sub_variants(sub, s, gis_names, recs):
    """Name variants for one ERCOT sub: (kind, text). kind in code / psse / gis / cdr."""
    v = [("code", sub)]
    base = re.sub(r"_(ESS|BESS|SLR|WND|WIND|SOLAR|PV|RN|ALL|GEN)$", "", sub)
    if base != sub and len(base) >= 3:
        v.append(("code", base))
    sw = re.sub(r"(SW|SS|TP|TAP|SWT)$", "", sub)       # switch / station / tap suffixes: NLARSW -> NLAR
    if sw != sub and len(sw) >= 3:
        v.append(("code", sw))
    seen = set()
    for nm in s.psse_names:
        st = psse_stem(nm)
        if st and st not in seen and letters(st) != letters(sub):
            seen.add(st)
            v.append(("psse", st))
        if len(seen) >= 6:
            break
    for nm in gis_names.get(sub, []):
        if len(letters(nm)) >= 4:
            v.append(("gis", nm))
    for u in recs.get(sub, [])[:6]:
        toks = _clean_unit_name(u["name"])
        if toks and u["src"] == "cdr" and not u.get("remote"):
            v.append(("cdr", " ".join(toks)))
    return v


def name_match(subs, idx, gis_names, recs, county_ev, counties, anchors=None, nb_pts=None):
    """Candidate coordinates from OSM / HIFLD names. Returns {sub: [cand dict, ...]} (best cluster first).

    `anchors` = (k-d tree, [sub ids], [zones]) of verified subs, which vote on the load zone of a candidate site, and
    `nb_pts` = {sub: xy array of its confident binding-constraint neighbours}; both only down-weight implausible sites
    (round 2 of the pipeline).
    """
    nb_pts = nb_pts or {}
    pool = idx.pool
    P = {c: pool[c].tolist() for c in ("lat", "lon", "kv_list", "ref_id", "name", "src", "county", "operator")}
    out = {}
    for sub in subs.index:
        s = subs.loc[sub]
        variants = sub_variants(sub, s, gis_names, recs)
        best = {}          # pool index -> (score, variant text)
        for kind, text in variants:
            if len(letters(text)) < (2 if kind == "code" else 3):
                continue
            for i in idx.candidates(kind, text):
                sc = idx.score(kind, text, i, s.zone) * VARIANT_WEIGHT[kind]
                if sc >= NAME_MIN and sc > best.get(i, (0, ""))[0]:
                    best[i] = (sc, f"{kind}:{text}")
        if not best:
            continue
        cands = []
        for i, (sc, via) in best.items():
            lat, lon = float(P["lat"][i]), float(P["lon"][i])
            f = kv_factor(s.kv_set, P["kv_list"][i])
            cf, cnote = 1.0, ""
            if county_ev.get(sub):
                dists = [(counties.dist_km(lat, lon, c, own=P["county"][i]), strength) for c, strength in county_ev[sub]]
                strong = [d for d, st in dists if st >= 1.0]
                weak = [d for d, st in dists if st < 1.0]
                if strong:
                    d = min(strong)
                    cf = 1.0 if d == 0 else (0.9 if d <= COUNTY_NEAR_KM else COUNTY_FAR_F)
                    cnote = f"county {d:.0f} km"
                elif weak:
                    d = min(weak)
                    cf = 1.0 if d <= COUNTY_TAP_FAR_KM else COUNTY_TAP_FAR_F
                    cnote = f"tap-county {d:.0f} km"
            zf, xy = 1.0, to_xy_km([lat], [lon])
            if anchors is not None and s.zone:
                tr, ids_, zs_ = anchors
                d, jj = tr.query(xy, k=ZONE_VOTERS + 1)
                votes = [zs_[y] for x, y in zip(np.atleast_1d(d[0]), np.atleast_1d(jj[0]))
                         if x <= ZONE_ANCHOR_KM and y < len(ids_) and ids_[y] != sub][:ZONE_VOTERS]
                zf = 1.0 if s.zone in votes else ZONE_FAR_F
            if sub in nb_pts:
                zf *= 1.0 if np.min(np.hypot(*(nb_pts[sub] - xy).T)) <= NEIGHBOR_MAX_KM else NB_FAR_F
            src = P["src"][i]
            allowed = next((z for t, z in REF_TDSP.items() if t in P["operator"][i]), None)
            if allowed and s.zone and s.zone not in allowed:
                zf *= OPERATOR_ZONE_F
            cands.append(dict(i=i, lat=lat, lon=lon, score=sc * f * cf * zf * SRC_PRIOR[src], name_sim=sc, kvf=f, cf=cf, zf=zf, src=src,
                              ref_id=P["ref_id"][i], ref_name=P["name"][i], via=via, cnote=cnote, county=P["county"][i]))
        cands.sort(key=lambda c: -c["score"])
        # cluster candidates that sit on the same site
        clusters = []
        for c in cands:
            for cl in clusters:
                if haversine_km(c["lat"], c["lon"], cl[0]["lat"], cl[0]["lon"]) <= CLUSTER_KM:
                    cl.append(c)
                    break
            else:
                clusters.append([c])
        res = []
        top = clusters[0][0]["score"]
        for k, cl in enumerate(clusters[:4]):
            lead = cl[0]
            conf = lead["score"]
            srcs = {c["src"] for c in cl}
            if len(srcs) > 1:
                conf += MULTI_SRC_BONUS
            if k == 0 and len(clusters) > 1:
                r = clusters[1][0]["score"] / max(top, 1e-9)
                if r > UNIQ_RATIO_FREE:
                    conf *= 1 - 0.5 * (r - UNIQ_RATIO_FREE) / (1 - UNIQ_RATIO_FREE)
            osm_m = next((c for c in cl if c["src"] == "osm"), None)
            pla_m = next((c for c in cl if c["src"] == "osm_plant"), None)
            hif_m = next((c for c in cl if c["src"] == "hifld"), None)
            pick = osm_m or pla_m or hif_m
            res.append(dict(method="hifld_name" if pick is hif_m else "osm_name", lat=pick["lat"], lon=pick["lon"], conf=float(min(conf, NAME_CONF_CAP)),
                            is_plant=pick is pla_m, osm_id=osm_m["ref_id"] if osm_m else "", hifld_id=(hif_m["ref_id"][1:] if hif_m else ""),
                            ref_name=pick["ref_name"], county=pick["county"], via=lead["via"], name_sim=lead["name_sim"],
                            evidence=f"{lead['via']} ~ {pick['ref_name']} (name {lead['name_sim']:.2f}, kv x{lead['kvf']:.2f}, {lead['cnote'] or 'no county'})",
                            n_clusters=len(clusters), rank=k, score_raw=lead["score"],
                            verified=bool(lead["cnote"].startswith("county ") and lead["cf"] >= 0.9)))
        out[sub] = res
    return out


def build_evidence(recs, hits):
    """Per-sub readable names (from GIS POI text) and county evidence [(county, strength)] (1.0 strong, 0.5 line-tap)."""
    gis_names = defaultdict(list)
    for r in hits.itertuples():
        if r.name and r.name not in gis_names[r.sub]:
            gis_names[r.sub].append(r.name)
    county_ev = {}
    for sub, rows in recs.items():
        d = {}
        for u in rows:
            if not u["county"] or u["county"] == "NAN":
                continue
            st = 1.0 if u["strong"] else 0.5
            d[u["county"]] = max(d.get(u["county"], 0), st)
        county_ev[sub] = sorted(d.items(), key=lambda kv: -kv[1])
    return dict(gis_names), county_ev


def anchor_priors(subs, res, pairs, min_conf=ANCHOR_MIN_CONF):
    """Round-1 results -> (zone-vote anchors, neighbour points). Used only as plausibility priors for round 2.

    Anchors are *verified* placements: an EIA plant match (wind excluded, its coordinates are loose) or a name match whose
    site lies inside the county that CDR / GIS gives for the sub's units.
    """
    pts = []
    xy = {}
    for sub, r in res.items():
        st = r["sites"][r["chosen"]]
        p = st["pick"]
        verified = p.get("verified") or (p["method"] == "eia_unit" and p.get("cls") != "wind")
        if st["conf"] >= min_conf and sub in subs.index and verified:
            pts.append((sub, st["lat"], st["lon"], subs.at[sub, "zone"]))
            xy[sub] = to_xy_km([st["lat"]], [st["lon"]])[0]
    anchors = None
    if len(pts) > ZONE_VOTERS:
        anchors = (cKDTree(to_xy_km([p[1] for p in pts], [p[2] for p in pts])), [p[0] for p in pts], [p[3] for p in pts])
    nbr = defaultdict(list)
    for r in pairs.itertuples():
        if r.b in xy:
            nbr[r.a].append(xy[r.b])
        if r.a in xy:
            nbr[r.b].append(xy[r.a])
    return anchors, {k: np.array(v) for k, v in nbr.items()}


# ------------------------------------------------------------------ lines: OSM ways -> substation-to-substation edges
MAX_EDGE_KM = 250              # ASSUMPTION: a walk longer than this (through unsnapped substations) is not one line
MAX_CIRCUITS = 6               # ASSUMPTION: cap on summed parallel circuits between two substations
GEOJSON_STEP_M = 150           # ASSUMPTION: vertex spacing kept when writing line geometry for maps
FENCE_STEP_M = 30               # ASSUMPTION: spacing of fence / interior sample points used for snapping
ON_FENCE_M = 30                # ASSUMPTION: a mid-way vertex this close to a fence is connected to the substation whatever its voltage tag says
PASS_TOL_M = 100               # ASSUMPTION: a vertex in the middle of a way (a line passing a substation) must be this close to the fence to count
PASS_KV_FRAC = 0.9             # ASSUMPTION: ... and the substation must be tagged at least this fraction of the line voltage (or untagged)
SNAP_SUB_MIN_KV = 60           # ASSUMPTION: only substations tagged >= this (or untagged) can terminate a >=69 kV line


def load_osm_lines(min_kv=MIN_TX_KV):
    """All power=line|cable ways >= min_kv in Texas (4 voltage-band files, deduplicated by way id)."""
    seen = {}
    for f in sorted(OSM_DIR.glob("lines_*.json")):
        for e in json.loads(f.read_text(encoding="utf-8"))["elements"]:
            if e["type"] != "way" or e["id"] in seen or "geometry" not in e:
                continue
            t = e.get("tags", {})
            raw = [x for x in re.split(r"[;,]", str(t.get("voltage", ""))) if x.strip()]
            kv = _kv_list(t.get("voltage"))
            if not kv or max(kv) < min_kv or len(e["nodes"]) != len(e["geometry"]) or len(e["nodes"]) < 2:
                continue
            kmax = max(kv)
            n_top = sum(1 for x in raw if _kv_list(x) and abs(_kv_list(x)[0] - kmax) < 1e-6)
            circ = t.get("circuits")
            circuits = int(circ) if str(circ).isdigit() else (int(t["cables"]) // 3 if str(t.get("cables", "")).isdigit() and int(t["cables"]) >= 3 else n_top)
            seen[e["id"]] = dict(id=e["id"], kv=kmax, mixed=len(kv) > 1, power=t.get("power", ""), circuits=max(1, circuits),
                                 operator=t.get("operator", ""), nodes=np.asarray(e["nodes"], dtype=np.int64),
                                 lat=np.array([g["lat"] for g in e["geometry"]]), lon=np.array([g["lon"] for g in e["geometry"]]))
    return list(seen.values())


def load_osm_polygons():
    """Substation outlines from substations_geom.json: {osm_id: (lat array, lon array)}."""
    f = OSM_DIR / "substations_geom.json"
    out = {}
    if f.exists():
        for e in json.loads(f.read_text(encoding="utf-8"))["elements"]:
            if e["type"] == "way" and "geometry" in e:
                out["w" + str(e["id"])] = (np.array([g["lat"] for g in e["geometry"]]), np.array([g["lon"] for g in e["geometry"]]))
    return out


def build_snap_tree(osm_subs, polygons):
    """k-d tree over substation fences (edges densified every FENCE_STEP_M) and interiors (grid every FENCE_STEP_M), plus centres.

    A line vertex anywhere inside or near a switchyard is then within a few tens of metres of a tree point, even for
    yards a few hundred metres across whose outlines have only corner vertices.
    """
    ok = osm_subs[(osm_subs.kv_max.isna()) | (osm_subs.kv_max >= SNAP_SUB_MIN_KV)]
    ids, xs, ys = [], [], []
    step = FENCE_STEP_M / 1000.0
    for r in ok.itertuples():
        c = to_xy_km([r.lat], [r.lon])[0]
        pts = [c]
        if r.osm_id in polygons:
            la, lo = polygons[r.osm_id]
            xy = to_xy_km(la, lo)
            pts.extend(xy)
            for a_, b_ in zip(xy[:-1], xy[1:]):
                n = int(np.hypot(*(b_ - a_)) / step)
                if n > 1:
                    t = (np.arange(1, n) / n)[:, None]
                    pts.extend(a_ + t * (b_ - a_))
            if len(xy) >= 4:
                x0, y0 = xy.min(axis=0)
                x1, y1 = xy.max(axis=0)
                if (x1 - x0) > 2 * step and (y1 - y0) > 2 * step:
                    gx, gy = np.meshgrid(np.arange(x0, x1, step), np.arange(y0, y1, step))
                    gx, gy = gx.ravel(), gy.ravel()
                    inside = _pip(gx, gy, xy)
                    pts.extend(np.column_stack([gx[inside], gy[inside]]))
        pts = np.asarray(pts)
        ids.extend([r.osm_id] * len(pts))
        xs.extend(pts[:, 0])
        ys.extend(pts[:, 1])
    return cKDTree(np.column_stack([xs, ys])), np.array(ids, dtype=object)


def build_osm_edges(ways, osm_subs, polygons, snap_tol_m=SNAP_TOL_M, gap_tol_m=GAP_TOL_M, pass_tol_m=PASS_TOL_M):
    """Walk the OSM way graph from every substation and return substation-to-substation edges.

    Ways are cut at every vertex that is shared with another way or lies within `snap_tol_m` of a substation fence.
    A walk starts at a substation, follows ways of the same voltage through plain junctions (T-taps are flagged), and
    stops at the next substation. Parallel ways between the same two substations add circuits.
    Returns (edges DataFrame, geometry dict {line_id: (lat array, lon array)}).
    """
    tree, tree_ids = build_snap_tree(osm_subs, polygons)
    sub_index = {s: k for k, s in enumerate(sorted(set(tree_ids)))}
    sub_names = {k: s for s, k in sub_index.items()}
    all_nodes = np.concatenate([w["nodes"] for w in ways])
    all_lat = np.concatenate([w["lat"] for w in ways])
    all_lon = np.concatenate([w["lon"] for w in ways])
    uniq, first_idx, counts = np.unique(all_nodes, return_index=True, return_counts=True)
    ulat, ulon = all_lat[first_idx], all_lon[first_idx]
    d, j = tree.query(to_xy_km(ulat, ulon), distance_upper_bound=snap_tol_m / 1000.0)
    shared = counts >= 2
    ends = np.zeros(len(uniq), bool)
    for w in ways:
        for n in (w["nodes"][0], w["nodes"][-1]):
            ends[np.searchsorted(uniq, n)] = True
    # way ends and junctions snap within snap_tol_m; a vertex in the middle of one way (a line passing a substation) is
    # stricter: on the fence (ON_FENCE_M), or within pass_tol_m of a substation whose voltage tag fits the line
    all_kv = np.concatenate([np.full(len(w["nodes"]), w["kv"]) for w in ways])[first_idx]
    sub_kv = osm_subs.set_index("osm_id").kv_max.to_dict()
    interior = ~(shared | ends)
    hit = np.isfinite(d)
    ok = hit.copy()
    for k in np.where(hit & interior)[0]:
        kvm = sub_kv.get(tree_ids[j[k]], np.nan)
        ok[k] = d[k] <= ON_FENCE_M / 1000.0 or (d[k] <= pass_tol_m / 1000.0 and (kvm != kvm or kvm >= PASS_KV_FRAC * all_kv[k]))
    term = np.full(len(uniq), -1, dtype=np.int64)
    term[ok] = [sub_index[tree_ids[x]] for x in j[ok]]
    special = shared | ends | (term >= 0)
    # adjacency over contracted nodes: substation k >= 0 ; plain vertex -(u + 1)
    adj = defaultdict(list)
    eid = 0
    free_ends = []
    for wi, w in enumerate(ways):
        u = np.searchsorted(uniq, w["nodes"])
        seg = np.zeros(len(u))
        seg[1:] = haversine_km(w["lat"][:-1], w["lon"][:-1], w["lat"][1:], w["lon"][1:])
        cum = np.cumsum(seg)
        pos = np.where(special[u])[0]
        key = [int(term[u[p]]) if term[u[p]] >= 0 else -(int(u[p]) + 1) for p in pos]
        for a in range(len(pos) - 1):
            ka, kb = key[a], key[a + 1]
            if ka == kb:
                continue
            length = float(cum[pos[a + 1]] - cum[pos[a]])
            adj[ka].append((eid, kb, length, w["kv"], wi, int(pos[a]), int(pos[a + 1]), w["circuits"]))
            adj[kb].append((eid, ka, length, w["kv"], wi, int(pos[a + 1]), int(pos[a]), w["circuits"]))
            eid += 1
    # bridge small gaps between free ends and other same-voltage vertices (OSM mapping gaps)
    if gap_tol_m:
        deg = {k: len(v) for k, v in adj.items()}
        dead = [k for k, v in deg.items() if k < 0 and v == 1]
        if dead:
            vx = np.array([-(k + 1) for k in dead])
            t2 = cKDTree(to_xy_km(ulat[vx], ulon[vx]))
            for a_i, b_i in t2.query_pairs(gap_tol_m / 1000.0):
                ka, kb = dead[a_i], dead[b_i]
                ea, eb = adj[ka][0], adj[kb][0]
                if abs(ea[3] - eb[3]) < 1e-6 and ea[4] != eb[4]:
                    adj[ka].append((eid, kb, 0.0, ea[3], -1, -1, -1, 0))
                    adj[kb].append((eid, ka, 0.0, ea[3], -1, -1, -1, 0))
                    eid += 1
    # walk
    paths = defaultdict(list)       # (s1, s2, kv) -> list of dict
    for s in [k for k in adj if k >= 0]:
        for (e0, nb, L, kv, wi, p0, p1, circ) in adj[s]:
            stack = [(nb, L, e0, ((wi, p0, p1),), circ, False)]
            seen = {s}
            while stack:
                node, dist, ein, segs, circ_, tap = stack.pop()
                if dist > MAX_EDGE_KM:
                    continue
                if node >= 0:
                    if node != s:
                        paths[(min(s, node), max(s, node), kv)].append(dict(a=s, b=node, length=dist, segs=segs, circuits=circ_, tap=tap))
                    continue
                if node in seen:
                    continue
                seen.add(node)
                nxt = [e for e in adj[node] if e[0] != ein and abs(e[3] - kv) < 1e-6]
                for (e1, nb2, L2, kv2, wi2, q0, q1, c2) in nxt:
                    stack.append((nb2, dist + L2, e1, segs + (((wi2, q0, q1),) if wi2 >= 0 else ()), max(circ_, c2), tap or len(nxt) > 1))
    rows, geoms = [], {}
    for (a, b, kv), plist in paths.items():
        plist.sort(key=lambda p: p["length"])
        chosen, used = [], []
        for p in plist:
            ws = {sg[0] for sg in p["segs"]}
            if any(ws & u for u in used):
                continue
            used.append(ws)
            chosen.append(p)
        rep = chosen[0]
        circuits = min(MAX_CIRCUITS, sum(max(1, p["circuits"]) for p in chosen))
        ops = Counter(ways[sg[0]]["operator"] for p in chosen for sg in p["segs"] if ways[sg[0]]["operator"])
        mixed = any(ways[sg[0]]["mixed"] for sg in rep["segs"])
        lid = f"osm:{sub_names[a]}-{sub_names[b]}-{kv:g}"
        rows.append(dict(line_id=lid, source="osm", voltage_kv=kv, length_km=round(rep["length"], 3), from_osm_sub=sub_names[a],
                         to_osm_sub=sub_names[b], from_sub="", to_sub="", operator=ops.most_common(1)[0][0] if ops else "",
                         n_circuits=circuits, via_tap=bool(rep["tap"]), n_ways=len(rep["segs"]), mixed_voltage=bool(mixed),
                         n_parallel_paths=len(chosen)))
        la, lo = [], []
        for (wi, p0, p1) in rep["segs"]:
            w = ways[wi]
            sl = slice(p0, p1 + 1) if p1 >= p0 else slice(p1, p0 + 1)
            x, y = w["lat"][sl], w["lon"][sl]
            if p1 < p0:
                x, y = x[::-1], y[::-1]
            la += list(x); lo += list(y)
        geoms[lid] = (la, lo)
    return pd.DataFrame(rows), geoms


def hifld_edges(hl, osm_subs, tree_and_ids, snap_m=HIFLD_SNAP_M):
    """HIFLD line segments as edges. Endpoints snap to the nearest OSM substation within snap_m; otherwise left blank."""
    tree, ids = tree_and_ids
    rows = []
    for r in hl.itertuples():
        if not r.lat or not (r.kv == r.kv) or r.kv < MIN_TX_KV:
            continue
        ends = to_xy_km([r.lat[0], r.lat[-1]], [r.lon[0], r.lon[-1]])
        d, j = tree.query(ends, distance_upper_bound=snap_m / 1000.0)
        osm_ids = [ids[x] if np.isfinite(dd) else "" for dd, x in zip(d, j)]
        rows.append(dict(line_id=f"hifld:{r.hifld_line_id}", source="hifld", voltage_kv=float(r.kv),
                         length_km=round(line_length_km(r.lat, r.lon), 3), from_osm_sub=osm_ids[0], to_osm_sub=osm_ids[1], from_sub="",
                         to_sub="", operator=r.owner if r.owner != "NOT AVAILABLE" else "", n_circuits=1, via_tap=False, n_ways=1,
                         mixed_voltage=False, n_parallel_paths=1, hifld_sub_1=r.sub_1, hifld_sub_2=r.sub_2, status=r.status,
                         lat0=r.lat[0], lon0=r.lon[0], lat1=r.lat[-1], lon1=r.lon[-1]))
    return pd.DataFrame(rows)


# ------------------------------------------------------------------ fusion of methods + neighbor stage ------------
AGREE_KM = 6.0                 # ASSUMPTION: candidates from different methods closer than this are the same site
SECOND_METHOD_WEIGHT = 0.8     # ASSUMPTION: a second, partly dependent method adds this fraction of its confidence (noisy-OR)
CONFLICT_PENALTY = 0.3         # ASSUMPTION: confidence lost when a rival site has nearly the same support
CONFLICT_RATIO = 0.5           # ASSUMPTION: a rival site counts as a conflict when it has at least this fraction of the best conf
OSM_LINK_KM = 2.0              # ASSUMPTION: a name/HIFLD site links to an OSM substation (graph node) within this distance
OSM_LINK_PLANT_KM = 5.0        # ASSUMPTION: same for plant-based (EIA) sites, whose coordinates are the plant, not the fence
NEIGHBOR_BONUS = 0.25          # ASSUMPTION: noisy-OR strength of "consistent with a confident binding-constraint neighbour"
NEIGHBOR_CONFIDENT = 0.7       # ASSUMPTION: a neighbour must be at least this sure to confirm or break a tie
NEIGHBOR_SWAP_RATIO = 0.6      # ASSUMPTION: an alternative site may replace the best only if it has this fraction of its conf
NEIGHBOR_CONTRA_F = 0.8        # ASSUMPTION: confidence factor when no confident neighbour is within range of the chosen site
NEIGHBOR_MAX_HOPS = 4          # ASSUMPTION: stations of one binding line are within this many OSM line hops
NEIGHBOR_FILL_CONF = 0.4       # ASSUMPTION: confidence of a purely topological fill-in from one neighbour
NEIGHBOR_FILL_CONF_2 = 0.55    # ASSUMPTION: ... from two or more neighbours that agree on the same substation
NEIGHBOR_FILL_HOPS = 2         # ASSUMPTION: fill-in looks this many OSM line hops from a confident neighbour
LINK_MIN_CONF = 0.5            # ASSUMPTION: only subs at least this sure label the ends of lines.csv rows
PRIORITY_COVER_CONF = 0.5      # ASSUMPTION: a sub counts as "covered" in the notes at this confidence or higher


def _noisy_or(confs):
    confs = sorted(confs, reverse=True)
    miss = 1.0
    for k, c in enumerate(confs):
        miss *= 1 - (c if k == 0 else SECOND_METHOD_WEIGHT * c)
    return 1 - miss


def fuse_candidates(sub_ids, ea, nm):
    """Merge EIA and name candidates into sites and pick the best. Returns {sub: result dict with .sites}."""
    out = {}
    for sub in sub_ids:
        cands = list(ea.get(sub, [])) + list(nm.get(sub, []))
        if not cands:
            continue
        sites = []
        for c in sorted(cands, key=lambda c: -c["conf"]):
            for st in sites:
                if haversine_km(c["lat"], c["lon"], st["lat"], st["lon"]) <= AGREE_KM:
                    st["members"].append(c)
                    break
            else:
                sites.append(dict(lat=c["lat"], lon=c["lon"], members=[c]))
        for st in sites:
            best_by_method = {}
            for m in st["members"]:
                if m["conf"] > best_by_method.get(m["method"], {"conf": -1})["conf"]:
                    best_by_method[m["method"]] = m
            st["methods"] = sorted(best_by_method)
            st["conf"] = _noisy_or([m["conf"] for m in best_by_method.values()])
            pick = next((m for m in st["members"] if m["method"] == "osm_name"), None) or \
                next((m for m in st["members"] if m["method"] == "hifld_name"), None) or st["members"][0]
            st["pick"], st["lat"], st["lon"] = pick, pick["lat"], pick["lon"]
        sites.sort(key=lambda s_: -s_["conf"])
        direct = any(m.get("direct") for m in sites[0]["members"])        # an EIA LMP-node designation is not shaken by a rival name
        if len(sites) > 1 and not direct and sites[1]["conf"] >= CONFLICT_RATIO * sites[0]["conf"]:
            sites[0]["conf"] *= 1 - CONFLICT_PENALTY * sites[1]["conf"] / sites[0]["conf"]
        sites[0]["conf"] = min(sites[0]["conf"], 0.97)
        out[sub] = dict(sites=sites, chosen=0)
    return out


def chosen_site(res):
    return res["sites"][res["chosen"]]


def osm_link(lat, lon, kv_set, snap_tree, snap_ids, snap_kv, max_km):
    """Nearest OSM substation to a point with compatible voltage. Returns (osm_id, km) or ('', nan)."""
    d, j = snap_tree.query(to_xy_km([lat], [lon]), k=8, distance_upper_bound=max_km)
    best = ("", np.nan)
    for dd, jj in zip(np.atleast_1d(d[0]), np.atleast_1d(j[0])):
        if not np.isfinite(dd):
            break
        if kv_factor(kv_set, snap_kv[snap_ids[jj]]) >= KV_UNKNOWN_F:
            return snap_ids[jj], float(dd)
    return best


def station_pairs(derived=DERIVED):
    """Binding-constraint stations. Returns (pairs, counts, kv): unique (a, b) line pairs with row counts per source,
    {source: {station: n rows}} and {station: set of kV seen}."""
    frames = []
    for name, tag in (("shadow_sced.parquet", "sced"), ("shadow_dam.parquet", "dam")):
        f = derived / name
        if f.exists():
            d = pd.read_parquet(f, columns=["from_station", "to_station", "from_kv", "to_kv", "element_type"])
            d["src"] = tag
            frames.append(d)
    if not frames:
        return pd.DataFrame(columns=["a", "b", "n_sced", "n_dam"]), {"sced": {}, "dam": {}}, {}
    d = pd.concat(frames, ignore_index=True)
    d = d[d.from_station.notna() & d.to_station.notna() & (d.from_station != "") & (d.to_station != "")]
    counts = {}
    for tag in ("sced", "dam"):
        x = d[d.src == tag]
        counts[tag] = pd.concat([x.from_station, x.to_station]).value_counts().to_dict()
    kv = defaultdict(set)
    for st, k in zip(pd.concat([d.from_station, d.to_station]), pd.concat([d.from_kv, d.to_kv])):
        if k == k and k > 0:
            kv[st].add(float(k))
    pairs = d[d.from_station != d.to_station].copy()
    pairs["a"] = np.where(pairs.from_station < pairs.to_station, pairs.from_station, pairs.to_station)
    pairs["b"] = np.where(pairs.from_station < pairs.to_station, pairs.to_station, pairs.from_station)
    g = pairs.groupby(["a", "b"]).src.agg(n_sced=lambda s_: int((s_ == "sced").sum()), n_dam=lambda s_: int((s_ == "dam").sum())).reset_index()
    return g, counts, dict(kv)


def neighbor_stage(res, pairs, edges, osm_xy, kv_sets):
    """Use binding-constraint adjacency (stations of one line) to confirm, break ties and fill in.

    - confirm: a site within NEIGHBOR_MAX_KM and NEIGHBOR_MAX_HOPS of a confident neighbour gets +NEIGHBOR_BONUS;
    - tie-break: an alternative site replaces the best when only it is consistent with a confident neighbour;
    - fill: an unplaced station next to a confident one takes the unique compatible OSM substation within
      NEIGHBOR_FILL_HOPS line hops (method 'neighbor', confidence <= 0.55).
    """
    import networkx as nx
    g = nx.Graph()
    for r in edges.itertuples():
        if r.from_osm_sub and r.to_osm_sub and r.from_osm_sub != r.to_osm_sub:
            g.add_edge(r.from_osm_sub, r.to_osm_sub, kv=r.voltage_kv, km=r.length_km)
    nbrs = defaultdict(set)
    for r in pairs.itertuples():
        nbrs[r.a].add(r.b)
        nbrs[r.b].add(r.a)

    def hops(u, v):
        if not u or not v or u not in g or v not in g:
            return None
        try:
            return nx.shortest_path_length(g, u, v)
        except nx.NetworkXNoPath:
            return None

    def consistent(site, other_site):
        d = haversine_km(site["lat"], site["lon"], other_site["lat"], other_site["lon"])
        if d > NEIGHBOR_MAX_KM:
            return False
        h = hops(site["pick"].get("osm_id", ""), other_site["pick"].get("osm_id", ""))
        return h is None or h <= NEIGHBOR_MAX_HOPS

    stats = Counter()
    for sub, r in res.items():
        conf_nb = [chosen_site(res[n]) for n in nbrs.get(sub, ()) if n in res and chosen_site(res[n])["conf"] >= NEIGHBOR_CONFIDENT]
        if not conf_nb:
            continue
        flags = [[consistent(s_, nb) for nb in conf_nb] for s_ in r["sites"]]
        top = chosen_site(r)
        cur = flags[r["chosen"]]
        if not any(cur):
            alts = [k for k, f in enumerate(flags) if any(f) and r["sites"][k]["conf"] >= NEIGHBOR_SWAP_RATIO * top["conf"]]
            if alts:
                r["chosen"] = max(alts, key=lambda k: r["sites"][k]["conf"])
                stats["swapped"] += 1
                cur = flags[r["chosen"]]
        site = chosen_site(r)
        if any(cur):
            site["conf"] = min(0.97, 1 - (1 - site["conf"]) * (1 - NEIGHBOR_BONUS))
            site["neighbor_ok"] = int(sum(cur))
            stats["confirmed"] += 1
        else:
            site["neighbor_ok"] = 0
            site["conf"] *= NEIGHBOR_CONTRA_F
            stats["contradicted"] += 1
    # fill-in for unplaced stations
    taken = {chosen_site(r)["pick"].get("osm_id") for r in res.values() if chosen_site(r)["conf"] >= NEIGHBOR_CONFIDENT}
    fills = {}
    for sub, ns in nbrs.items():
        if sub in res and chosen_site(res[sub])["conf"] >= 0.3:
            continue
        kvs = [k for k in kv_sets.get(sub, []) if k >= 60]
        cand_sets = []
        for n in ns:
            if n in res and chosen_site(res[n])["conf"] >= NEIGHBOR_CONFIDENT:
                oid = chosen_site(res[n])["pick"].get("osm_id", "")
                if oid in g:
                    reach = nx.single_source_shortest_path_length(g, oid, cutoff=NEIGHBOR_FILL_HOPS)
                    ok = set()
                    for node, h in reach.items():
                        if h == 0 or node in taken:
                            continue
                        if kvs and not any(abs(k - node_kv) <= KV_TOL * k for k in kvs for node_kv in {e["kv"] for e in g[node].values()}):
                            continue
                        ok.add(node)
                    cand_sets.append(ok)
        if not cand_sets:
            continue
        inter = set.intersection(*cand_sets)
        pool_ = inter if (len(cand_sets) >= 2 and inter) else (cand_sets[0] if len(cand_sets) == 1 else set())
        if len(pool_) == 1:
            node = next(iter(pool_))
            fills[sub] = (node, NEIGHBOR_FILL_CONF_2 if len(cand_sets) >= 2 else NEIGHBOR_FILL_CONF, len(cand_sets))
    for sub, (node, conf, nn) in fills.items():
        lat, lon = osm_xy[node]
        site = dict(lat=lat, lon=lon, conf=conf, methods=["neighbor"], neighbor_ok=nn, members=[],
                    pick=dict(method="neighbor", osm_id=node, hifld_id="", ref_name="", lat=lat, lon=lon,
                              evidence=f"only compatible OSM substation within {NEIGHBOR_FILL_HOPS} line hops of {nn} confident binding-constraint neighbour(s)"))
        res[sub] = dict(sites=[site], chosen=0)
        stats["filled"] += 1
    return res, stats, g


# ------------------------------------------------------------------ orchestration + outputs -----------------------
CACHE = DERIVED / "_geo_cache"
SIMPLIFY_M = GEOJSON_STEP_M


def _cached(name, fn, fresh, inputs=()):
    """Pickle cache for the slow Excel parses, keyed on the input files' names, sizes and mtimes (so a newer download is re-read)."""
    CACHE.mkdir(parents=True, exist_ok=True)
    f = CACHE / f"{name}.pkl"
    key = "|".join(f"{p.name}:{p.stat().st_size}:{p.stat().st_mtime_ns}" for p in inputs if p is not None and Path(p).exists())
    if f.exists() and not fresh:
        obj = pd.read_pickle(f)
        if isinstance(obj, dict) and obj.get("key") == key:
            return obj["value"]
    v = fn()
    pd.to_pickle({"key": key, "value": v}, f)
    return v


def _write(df, name, labels):
    """Write data/<name>.csv and its .labels.csv sidecar (column,label,source,note)."""
    REPO_DATA.mkdir(parents=True, exist_ok=True)
    df.to_csv(REPO_DATA / f"{name}.csv", index=False, encoding="utf-8", lineterminator="\n")
    rows = []
    for c in df.columns:
        lab, src, note = labels.get(c, ("DERIVED", "gridsite.geomatch", ""))
        rows.append({"column": c, "label": lab, "source": src, "note": note})
    pd.DataFrame(rows).to_csv(REPO_DATA / f"{name}.labels.csv", index=False, encoding="utf-8", lineterminator="\n")


def _simplify(lat, lon, step_m=SIMPLIFY_M):
    lat, lon = np.asarray(lat, float), np.asarray(lon, float)
    if len(lat) <= 2:
        return lat, lon
    keep = [0]
    acc = 0.0
    seg = haversine_km(lat[:-1], lon[:-1], lat[1:], lon[1:]) * 1000.0
    for i, d in enumerate(seg, start=1):
        acc += d
        if acc >= step_m:
            keep.append(i)
            acc = 0.0
    if keep[-1] != len(lat) - 1:
        keep.append(len(lat) - 1)
    return lat[keep], lon[keep]


def extra_station_rows(stations, kv, existing):
    """Binding-constraint stations that are not NP4-160 SUBSTATION codes (taps, switches): pseudo-subs with n_buses = 0."""
    rows = []
    for st in sorted(set(stations) - set(existing)):
        ks = sorted(kv.get(st, set()), reverse=True)
        rows.append({"sub": st, "n_buses": 0, "kv_max": ks[0] if ks else np.nan, "kv_list": ";".join(f"{k:g}" for k in ks), "kv_set": ks,
                     "zone": "", "has_resource_node": False, "n_resource_nodes": 0, "resource_nodes": [], "buses": [], "psse_names": [],
                     "psse_nums": [], "hub": "", "noie_zone": ""})
    return pd.DataFrame(rows).set_index("sub", drop=False) if rows else pd.DataFrame()


def match_geo(*args):
    """Match every ERCOT substation to coordinates; write data/{substations,osm_substations,lines,eia_plants_tx}.csv + geojson."""
    import time
    fresh = "--fresh" in args
    t0 = time.time()

    def log(msg):
        print(f"[{time.time() - t0:6.1f}s] {msg}", flush=True)

    sp, r2u, noie = load_ercot()
    subs = build_ercot_subs(sp, r2u, noie)
    subs["in_np4160"] = True
    pairs, counts, st_kv = station_pairs()
    stations = set(counts["sced"]) | set(counts["dam"])
    extra = extra_station_rows(stations, st_kv, subs.index)
    if len(extra):
        extra["in_np4160"] = False
        subs = pd.concat([subs, extra])
    log(f"ERCOT: {int(subs.in_np4160.sum())} subs in NP4-160 ({sp.shape[0]} buses), {len(extra)} binding-constraint stations outside it; settlement file {sp_file().name}")
    cdr = _cached("cdr", load_cdr, fresh, [_latest("ERCOT_CDR_*.xlsx", DOCS_DIR)])
    gis = _cached("gis", load_gis, fresh, [_latest("*GIS_Report_*.xlsx", DOCS_DIR)])
    plants, gens = _cached("eia", load_eia, fresh, [_latest("eia860*.zip", EIA_DIR), _latest("*_generator20*.xlsx", EIA_DIR)])
    osm = load_osm_substations()
    hs, hl = load_hifld()
    counties = Counties()
    log(f"loaded CDR {len(cdr)} units, GIS {len(gis)} projects, EIA {len(plants)} TX plants / {len(gens)} generators, OSM {len(osm)} substations, "
        f"HIFLD {len(hs)} subs / {len(hl)} lines")
    cu = assign_cdr_to_subs(cdr, subs)
    hits = gis_bus_hits(gis, sp)
    recs = sub_unit_records(subs, cu, hits, r2u, sp)
    gis_names, county_ev = build_evidence(recs, hits)
    log(f"unit records for {len(recs)} subs; GIS POI bus numbers resolve to {hits['sub'].nunique()} subs")

    ea = match_eia(subs, recs, plants, gens, counties)
    log(f"method (a) eia_unit: {len(ea)} subs")
    pool = build_pool(osm, hs, counties, load_osm_batteries())
    idx = NameIndex(pool)
    nm = name_match(subs, idx, gis_names, recs, county_ev, counties)
    res = fuse_candidates(list(subs.index), ea, nm)
    anchors, nb_pts = anchor_priors(subs, res, pairs)
    log(f"round 1: {len(nm)} subs with name candidates; {len(anchors[1]) if anchors else 0} verified anchors >= {ANCHOR_MIN_CONF}; re-scoring with zone-vote + neighbour priors")
    nm = name_match(subs, idx, gis_names, recs, county_ev, counties, anchors, nb_pts)
    res = fuse_candidates(list(subs.index), ea, nm)
    log(f"method (b) osm_name / hifld_name: {len(nm)} subs")

    ways = load_osm_lines()
    polys = load_osm_polygons()
    edges, geoms = build_osm_edges(ways, osm, polys)
    log(f"OSM lines: {len(ways)} ways >= {MIN_TX_KV} kV -> {len(edges)} substation-to-substation edges")

    # link every site to an OSM substation (graph node)
    tree, tree_ids = build_snap_tree(osm, polys)
    snap_kv = dict(zip(osm.osm_id, osm.kv_list))
    osm_kvmax = dict(zip(osm.osm_id, osm.kv_max))
    for sub, r in res.items():
        s = subs.loc[sub]
        tx = [k for k in s.kv_set if k >= 60]
        for st in r["sites"]:
            p = st["pick"]
            if p.get("osm_id"):
                continue
            plant_site = p["method"] == "eia_unit" or p.get("is_plant", False)
            oid, dkm = osm_link(st["lat"], st["lon"], s.kv_set, tree, tree_ids, snap_kv, OSM_LINK_PLANT_KM if plant_site else OSM_LINK_KM)
            if oid and (tx or not plant_site or (osm_kvmax.get(oid) or 0) >= MIN_TX_KV):
                if not tx and plant_site and not ((osm_kvmax.get(oid) or 0) >= MIN_TX_KV):
                    continue
                p["osm_id"] = oid
                p["osm_link"] = "snap"
                p["osm_snap_km"] = dkm
    osm_xy = {r.osm_id: (r.lat, r.lon) for r in osm.itertuples()}
    kv_sets = {sub: list(subs.at[sub, "kv_set"]) for sub in subs.index}
    # G1: pin the audited corrections (data/geo_overrides.csv) before the neighbour stage, so confident neighbours are judged against corrected positions,
    # and again after it, because the stage rewrites confidences (an override confidence is final). See gridsite/geo_overrides.py and notes/phase7-geo.md.
    ov_link = lambda lat, lon, kv: osm_link(lat, lon, kv, tree, tree_ids, snap_kv, OSM_LINK_KM)[0]   # noqa: E731
    res, ov_stats = geo_overrides.apply_overrides(res, subs, link=ov_link)
    res, nb_stats, graph = neighbor_stage(res, pairs, edges, osm_xy, kv_sets)
    res, _ = geo_overrides.apply_overrides(res, subs, link=ov_link, post=True)
    log(f"G1 overrides: {dict(ov_stats)}")
    log(f"method (c) neighbor: {dict(nb_stats)}")
    return _write_outputs(subs, res, ea, nm, edges, geoms, osm, hs, hl, plants, gens, counties, counts, pairs, hits, gis_names, polys, tree, tree_ids, log)


def _write_outputs(subs, res, ea, nm, edges, geoms, osm, hs, hl, plants, gens, counties, counts, pairs, hits, gis_names, polys, tree, tree_ids, log):
    # ---- substations.csv
    rows = []
    for sub, s in subs.iterrows():
        r = res.get(sub)
        row = dict(sub=sub, n_buses=int(s.n_buses), kv_max=s.kv_max, kv_list=s.kv_list, zone=s.zone, has_resource_node=bool(s.has_resource_node),
                   n_resource_nodes=int(s.n_resource_nodes), lat=np.nan, lon=np.nan, county="", match_method="none", match_conf=0.0, osm_id="",
                   hifld_id="", eia_plant_codes="", name_guess="")
        e = ea.get(sub, [None])[0]
        if e:
            row["eia_plant_codes"] = ";".join(str(c) for c in e["plant_codes"])
            row["name_guess"] = e["plant_name"]
        if r:
            st = chosen_site(r)
            p = st["pick"]
            row.update(lat=round(st["lat"], 6), lon=round(st["lon"], 6), match_method=p["method"], match_conf=round(float(st["conf"]), 3),
                       osm_id=p.get("osm_id", ""), hifld_id=p.get("hifld_id", ""))
            if p.get("ref_name"):
                row["name_guess"] = p["ref_name"]
            row["methods_agree"] = "+".join(st["methods"])
            row["evidence"] = p.get("evidence", "")
            row["osm_link"] = p.get("osm_link", "name" if (p["method"] == "osm_name" and not p.get("is_plant")) else ("neighbor" if p["method"] == "neighbor" else ""))
            row["neighbor_ok"] = st.get("neighbor_ok", "")
            row["n_sites"] = len(r["sites"])
            if e:
                row["eia_dist_km"] = round(float(haversine_km(st["lat"], st["lon"], e["lat"], e["lon"])), 2)
        rows.append(row)
    df = pd.DataFrame(rows)
    df["county"] = counties.lookup(df.lat.fillna(0).values, df.lon.fillna(0).values)
    df.loc[df.lat.isna(), "county"] = ""
    n_sced, n_dam = counts["sced"], counts["dam"]
    df["n_binding_sced"] = df["sub"].map(n_sced).fillna(0).astype(int)
    df["n_binding_dam"] = df["sub"].map(n_dam).fillna(0).astype(int)
    df["noie_zone"] = df["sub"].map(subs["noie_zone"]).fillna("")
    df["in_np4160"] = df["sub"].map(subs["in_np4160"]).astype(bool)
    for c in ("methods_agree", "evidence", "osm_link", "neighbor_ok", "n_sites", "eia_dist_km"):
        if c not in df:
            df[c] = ""
    for c in ("n_sites", "neighbor_ok"):
        df[c] = pd.to_numeric(df[c], errors="coerce").astype("Int64")
    df["eia_dist_km"] = pd.to_numeric(df["eia_dist_km"], errors="coerce")
    share = df[(df.osm_id != "") & (df.match_conf >= LINK_MIN_CONF)].osm_id.value_counts()
    df["osm_shared_by"] = df.osm_id.map(share).fillna(0).astype(int)
    df = df.sort_values("sub", kind="stable").reset_index(drop=True)
    sub_labels = {
        "sub": ("REAL", "ERCOT NP4-160 SUBSTATION", "key; also stations named in NP6-86-CD / NP4-191 that are not NP4-160 codes (in_np4160 = False)"),
        "n_buses": ("REAL", "ERCOT NP4-160", "electrical buses in the substation (0 for stations outside NP4-160)"),
        "kv_max": ("REAL", "ERCOT NP4-160 VOLTAGE_LEVEL", "highest bus voltage"),
        "kv_list": ("REAL", "ERCOT NP4-160 VOLTAGE_LEVEL", "all distinct bus voltages, descending"),
        "zone": ("REAL", "ERCOT NP4-160 SETTLEMENT_LOAD_ZONE", "modal load zone of the buses"),
        "has_resource_node": ("REAL", "ERCOT NP4-160 RESOURCE_NODE + Resource_Node_to_Unit", "any bus carries a resource node, or the sub is a UNIT_SUBSTATION"),
        "n_resource_nodes": ("REAL", "ERCOT NP4-160 + Resource_Node_to_Unit", "distinct resource nodes"),
        "lat": ("DERIVED", "geomatch fused site", "WGS84; blank when unmatched. Coordinates of the matched OSM / HIFLD substation or EIA plant"),
        "lon": ("DERIVED", "geomatch fused site", "WGS84; blank when unmatched"),
        "county": ("DERIVED", "Census TIGER cb_2023_us_county_500k", "county containing (lat, lon); blank outside Texas or unmatched"),
        "match_method": ("DERIVED", "geomatch", "eia_unit | osm_name | hifld_name | neighbor | override | none (see notes/phase1-geo.md; override = audited correction from data/geo_overrides.csv, notes/phase7-geo.md)"),
        "match_conf": ("ASSUMPTION", "geomatch", "0-1 heuristic score, not a calibrated probability; formula in notes/phase1-geo.md; every constant in it is an ASSUMPTION checked only by the independent tests in the notes"),
        "osm_id": ("REAL", "OpenStreetMap", "substation id (w=way, n=node, r=relation) linked to this sub; graph node for lines.csv"),
        "hifld_id": ("REAL", "HIFLD Electric Substations mirror", "HIFLD ID of the matched substation, when that layer supplied the name match"),
        "eia_plant_codes": ("REAL", "EIA-860 / 860M", "plants the sub's units were matched to (semicolon list), even when the final site is elsewhere"),
        "name_guess": ("DERIVED", "OSM / HIFLD / EIA name of the match", "readable name of the matched substation or plant"),
        "methods_agree": ("DERIVED", "geomatch", "methods supporting the chosen site, joined with +"),
        "evidence": ("DERIVED", "geomatch", "short text: which name variant matched what, voltage and county checks"),
        "osm_link": ("DERIVED", "geomatch", "name = matched an OSM name; snap = nearest compatible OSM substation within OSM_LINK_KM / OSM_LINK_PLANT_KM; neighbor; override = osm_id pinned by data/geo_overrides.csv"),
        "neighbor_ok": ("DERIVED", "binding-constraint adjacency", "number of confident constraint neighbours consistent with the site (0 = contradicted)"),
        "n_sites": ("DERIVED", "geomatch", "distinct candidate sites that were considered"),
        "eia_dist_km": ("DERIVED", "haversine", "distance between the chosen site and the EIA plant matched to the sub's units"),
        "n_binding_sced": ("REAL", "NP6-86-CD via derived/shadow_sced.parquet", "SCED binding-constraint rows naming this station"),
        "n_binding_dam": ("REAL", "NP4-191 via derived/shadow_dam.parquet", "DAM binding-constraint rows naming this station"),
        "noie_zone": ("REAL", "ERCOT NOIE_Mapping", "non-opt-in entity load zone(s) with load at this sub (AEN, CPS, LCRA, RAYBN ...)"),
        "in_np4160": ("REAL", "ERCOT NP4-160", "False for constraint stations that are not settlement-point substations"),
        "osm_shared_by": ("DERIVED", "geomatch", f"ERCOT subs with match_conf >= {LINK_MIN_CONF} linked to the same osm_id (ERCOT splits some stations by owner / voltage)"),
    }
    _write(df, "substations", sub_labels)
    log(f"wrote substations.csv: {len(df)} rows, {int((df.match_method != 'none').sum())} with coordinates")

    # ---- osm_substations.csv
    ercot_of_osm = {}
    for r in df[df.osm_id != ""].sort_values("match_conf", ascending=False).itertuples():
        ercot_of_osm.setdefault(r.osm_id, r)
    od = osm.copy()
    od["county"] = counties.lookup(od.lat.values, od.lon.values)
    od["voltage_kv"] = od.kv_max
    od["voltage_list_kv"] = od.kv_list.map(lambda k: ";".join(f"{x:g}" for x in k))
    od["ercot_sub"] = od.osm_id.map(lambda i: ercot_of_osm[i].sub if i in ercot_of_osm and ercot_of_osm[i].match_conf >= LINK_MIN_CONF else "")
    od = od[["osm_id", "name", "voltage_kv", "lat", "lon", "operator", "voltage_list_kv", "sub_type", "ref", "county", "ercot_sub"]]
    od["lat"], od["lon"] = od.lat.round(6), od.lon.round(6)
    _write(od, "osm_substations", {
        "osm_id": ("REAL", "OpenStreetMap", "w/n/r + OSM id"), "name": ("REAL", "OpenStreetMap name|official_name|alt_name", ""),
        "voltage_kv": ("REAL", "OpenStreetMap voltage tag", "largest value, kV; blank when untagged"),
        "lat": ("REAL", "OpenStreetMap", "centre of the outline"), "lon": ("REAL", "OpenStreetMap", "centre of the outline"),
        "operator": ("REAL", "OpenStreetMap operator|owner", ""), "voltage_list_kv": ("REAL", "OpenStreetMap voltage tag", "all values, kV"),
        "sub_type": ("REAL", "OpenStreetMap substation=*", "transmission, distribution, industrial, generation ..."),
        "ref": ("REAL", "OpenStreetMap ref", "CenterPoint / Austin Energy / CPS station codes equal ERCOT SUBSTATION codes"),
        "county": ("DERIVED", "Census TIGER cb_2023_us_county_500k", "point-in-polygon"),
        "ercot_sub": ("DERIVED", "geomatch", f"best ERCOT sub with match_conf >= {LINK_MIN_CONF}; blank otherwise")})

    # ---- lines.csv
    sub_of_osm = {i: r.sub for i, r in ercot_of_osm.items() if r.match_conf >= LINK_MIN_CONF}
    osm_edges = edges.copy()
    pair_set = {(min(a, b), max(a, b)) for a, b in zip(osm_edges.from_osm_sub, osm_edges.to_osm_sub)}
    hl_tree = (tree, tree_ids)
    he = hifld_edges(hl, osm, hl_tree) if hl is not None else pd.DataFrame()
    allw = pd.concat([osm_edges, he], ignore_index=True) if len(he) else osm_edges
    allw["from_sub"] = allw.from_osm_sub.map(sub_of_osm).fillna("")
    allw["to_sub"] = allw.to_osm_sub.map(sub_of_osm).fillna("")
    allw["in_osm_pair"] = [(a != "" and b != "" and (min(a, b), max(a, b)) in pair_set) if s_ == "hifld" else True
                           for a, b, s_ in zip(allw.from_osm_sub, allw.to_osm_sub, allw.source)]
    allw["voltage_kv"] = allw.voltage_kv.astype(float)
    cols = ["line_id", "source", "voltage_kv", "length_km", "from_osm_sub", "to_osm_sub", "from_sub", "to_sub", "operator", "n_circuits",
            "via_tap", "n_ways", "mixed_voltage", "n_parallel_paths", "in_osm_pair", "hifld_sub_1", "hifld_sub_2", "status"]
    for c in cols:
        if c not in allw:
            allw[c] = ""
    allw = allw[cols].sort_values(["source", "voltage_kv", "line_id"], ascending=[True, False, True]).reset_index(drop=True)
    _write(allw, "lines", {
        "line_id": ("DERIVED", "geomatch", "osm:<from>-<to>-<kV> or hifld:<HIFLD ID>"),
        "source": ("REAL", "OSM Overpass | HIFLD mirror", "osm = ways chained substation to substation; hifld = 2021-22 segments (endpoints may be taps)"),
        "voltage_kv": ("REAL", "OSM voltage tag | HIFLD VOLTAGE", "nominal kV of the line (largest tagged value)"),
        "length_km": ("DERIVED", "haversine along the way geometry", "path length of the shortest chain between the two ends"),
        "from_osm_sub": ("DERIVED", "geomatch snap", f"OSM substation within SNAP_TOL_M = {SNAP_TOL_M} m of the line end (HIFLD: {HIFLD_SNAP_M} m)"),
        "to_osm_sub": ("DERIVED", "geomatch snap", ""),
        "from_sub": ("DERIVED", "geomatch", f"ERCOT sub linked to from_osm_sub with match_conf >= {LINK_MIN_CONF}; blank otherwise"),
        "to_sub": ("DERIVED", "geomatch", ""), "operator": ("REAL", "OSM operator | HIFLD OWNER", "most common tag along the chain"),
        "n_circuits": ("DERIVED", "OSM circuits|cables|voltage tags", f"summed over distinct parallel ways, capped at {MAX_CIRCUITS}; 1 when untagged"),
        "via_tap": ("DERIVED", "geomatch", "True if the chain passes a T-junction with no substation (a tapped line)"),
        "n_ways": ("DERIVED", "geomatch", "OSM ways (or contracted segments) in the representative chain"),
        "mixed_voltage": ("REAL", "OSM voltage tag", "way carries more than one voltage (e.g. 345;138 underbuild)"),
        "n_parallel_paths": ("DERIVED", "geomatch", "disjoint chains found between the same two substations at this voltage"),
        "in_osm_pair": ("DERIVED", "geomatch", "HIFLD rows: both ends snapped and the pair also exists as an OSM edge (so it duplicates it)"),
        "hifld_sub_1": ("REAL", "HIFLD SUB_1", "HIFLD rows only"), "hifld_sub_2": ("REAL", "HIFLD SUB_2", "HIFLD rows only"),
        "status": ("REAL", "HIFLD STATUS", "HIFLD rows only")})
    log(f"wrote lines.csv: {len(allw)} rows ({(allw.source == 'osm').sum()} osm, {(allw.source == 'hifld').sum()} hifld)")

    # ---- eia_plants_tx.csv
    pt = _eia_plants_table(plants, gens)
    pt["county_from_coords"] = counties.lookup(pt.lat.fillna(0).values, pt.lon.fillna(0).values)
    pt.loc[pt.lat.isna(), "county_from_coords"] = ""
    pt["coord_county_match"] = pt.county_from_coords == pt.county
    _write(pt, "eia_plants_tx", {
        "county_from_coords": ("DERIVED", "Census TIGER point-in-polygon", "county containing (lat, lon); differs from `county` for a handful of EIA records (data errors or border plants)"),
        "coord_county_match": ("DERIVED", "geomatch", "False flags an EIA record whose coordinates fall outside its stated county; match_eia down-weights those by EIA_BAD_COORD_F"),
        "plant_code": ("REAL", "EIA-860 / 860M", "EIA plant code"), "name": ("REAL", "EIA-860 / 860M", ""), "lat": ("REAL", "EIA-860 Plant sheet | 860M", ""),
        "lon": ("REAL", "EIA-860 Plant sheet | 860M", ""), "county": ("REAL", "EIA-860 / 860M", "upper case"),
        "tech_mix": ("DERIVED", "EIA-860 / 860M Technology", "technology:MW (nameplate, operating + planned) joined with ;"),
        "mw_operating": ("DERIVED", "EIA-860M Operating sheet (Aug 2026); EIA-860 2025 for plants absent from it", "nameplate MW"),
        "mw_planned": ("DERIVED", "EIA-860M Planned sheet (Aug 2026)", "nameplate MW, any planned status (P, L, T, U, V, TS)"),
        "storage_mw": ("DERIVED", "EIA-860M", "battery + flywheel nameplate MW, operating"),
        "storage_mw_planned": ("DERIVED", "EIA-860M", "battery nameplate MW, planned"),
        "storage_mwh": ("DERIVED", "EIA-860M", "battery nameplate energy MWh, operating"),
        "planned_cod_first": ("REAL", "EIA-860M Planned sheet", "earliest planned operation date (YYYY-MM) among the plant's planned units"),
        "utility": ("REAL", "EIA-860", "owner / operator entity"), "tdsp": ("REAL", "EIA-860 Plant sheet", "transmission or distribution system owner"),
        "grid_kv": ("REAL", "EIA-860 Plant sheet", "grid voltage at the point of interconnection, kV (annual file only)"),
        "balancing_authority": ("REAL", "EIA-860 / 860M", "ERCO = ERCOT"), "in_ercot": ("DERIVED", "balancing authority", "True when ERCO")})

    _write(_eia_generators_table(plants, gens), "eia_generators_tx", {
        "plant_code": ("REAL", "EIA-860 / 860M", ""), "gen_id": ("REAL", "EIA-860 / 860M", "generator ID within the plant"),
        "plant_name": ("REAL", "EIA-860 / 860M", ""), "county": ("REAL", "EIA-860 / 860M", ""), "lat": ("REAL", "EIA-860 Plant sheet | 860M", ""),
        "lon": ("REAL", "EIA-860 Plant sheet | 860M", ""), "balancing_authority": ("REAL", "EIA-860 / 860M", "ERCO = ERCOT"),
        "tech": ("REAL", "EIA-860 / 860M Technology", ""), "class": ("DERIVED", "geomatch.tech_class", "battery | solar | wind | hydro | nuclear | thermal"),
        "mw": ("REAL", "EIA-860 / 860M nameplate capacity", ""), "mwh": ("REAL", "EIA-860M nameplate energy capacity", "storage units only"),
        "status": ("REAL", "EIA-860M status code", "OP operating; planned codes P, L, T, U, V, TS"),
        "status_group": ("DERIVED", "EIA-860M sheet", "operating = Operating sheet (or EIA-860 2025 for plants absent from 860M); planned = Planned sheet"),
        "year": ("REAL", "EIA-860M Operating Year | Planned Operation Year", "in-service year (operating) or planned operation year"),
        "month": ("REAL", "EIA-860M", ""), "source": ("DERIVED", "geomatch", "eia860 | eia860m_operating | eia860m_planned"),
        "lmp_node": ("REAL", "EIA-860 RTO/ISO LMP Node Designation", "ERCOT unit code or resource node when filled (annual file only)")})

    # ---- geojson for maps
    pts = []
    for r in df[df.lat.notna()].itertuples():
        pts.append({"type": "Feature", "geometry": {"type": "Point", "coordinates": [r.lon, r.lat]},
                    "properties": {"kind": "ercot_sub", "sub": r.sub, "kv_max": r.kv_max, "zone": r.zone, "conf": r.match_conf, "method": r.match_method,
                                   "name": r.name_guess, "osm_id": r.osm_id, "binding_dam": int(r.n_binding_dam), "binding_sced": int(r.n_binding_sced)}})
    for r in od[(od.ercot_sub == "") & (od.voltage_kv >= MIN_TX_KV)].itertuples():
        pts.append({"type": "Feature", "geometry": {"type": "Point", "coordinates": [r.lon, r.lat]},
                    "properties": {"kind": "osm_sub", "osm_id": r.osm_id, "name": r.name, "kv_max": r.voltage_kv, "operator": r.operator}})
    (DERIVED / "substations.geojson").write_text(json.dumps({"type": "FeatureCollection", "features": pts}), encoding="utf-8")
    feats = []
    for r in allw[allw.source == "osm"].itertuples():
        la, lo = _simplify(*geoms[r.line_id])
        feats.append({"type": "Feature", "geometry": {"type": "LineString", "coordinates": [[round(float(x), 5), round(float(y), 5)] for y, x in zip(la, lo)]},
                      "properties": {"line_id": r.line_id, "kv": r.voltage_kv, "km": r.length_km, "circuits": int(r.n_circuits), "from": r.from_osm_sub,
                                     "to": r.to_osm_sub, "from_sub": r.from_sub, "to_sub": r.to_sub, "operator": r.operator}})
    (DERIVED / "lines.geojson").write_text(json.dumps({"type": "FeatureCollection", "features": feats}), encoding="utf-8")
    log(f"wrote lines.geojson ({len(feats)} lines) and substations.geojson ({len(pts)} points)")
    coverage = coverage_report(df, allw, counts, pairs)
    (DERIVED / "geo_coverage.json").write_text(json.dumps(coverage, indent=1, default=float), encoding="utf-8")
    valid = validation_report(df, allw, pairs, ea, nm, edges)
    (DERIVED / "geo_validation.json").write_text(json.dumps(valid, indent=1, default=float), encoding="utf-8")
    spot_check_sample(df).to_csv(DERIVED / "geo_spot_check.csv", index=False, encoding="utf-8")
    log("wrote derived/geo_coverage.json, geo_validation.json, geo_spot_check.csv")
    return df, allw, coverage


def _eia_generators_table(plants, gens):
    """One row per EIA generator / storage unit in Texas: EIA-860M (Aug 2026) where the plant is in it, else EIA-860 2025."""
    g = gens.copy()
    monthly = set(g[g.source != "eia860"].plant_code)
    use = g[(g.source != "eia860") | (~g.plant_code.isin(monthly))].copy()
    use["class"] = [tech_class(t) for t in use.tech]
    use["status_group"] = np.where(use.source.isin(["eia860", "eia860m_operating"]), "operating", "planned")
    p = plants.drop_duplicates("plant_code").set_index("plant_code")
    use["plant_name"] = use.plant_code.map(p.name)
    use["county"] = use.plant_code.map(p.county)
    use["lat"] = use.plant_code.map(p.lat).round(6)
    use["lon"] = use.plant_code.map(p.lon).round(6)
    use["balancing_authority"] = use.plant_code.map(p.ba)
    use = use.rename(columns={"op_year": "year", "op_month": "month"})
    use["year"] = use.year.astype("Int64")
    use["month"] = use.month.astype("Int64")
    cols = ["plant_code", "gen_id", "plant_name", "county", "lat", "lon", "balancing_authority", "tech", "class", "mw", "mwh", "status", "status_group",
            "year", "month", "source", "lmp_node"]
    return use[cols].sort_values(["plant_code", "gen_id"]).reset_index(drop=True)


def _eia_plants_table(plants, gens):
    g = gens.copy()
    g["grp"] = np.where(g.source.eq("eia860"), "annual", "monthly")
    monthly = set(g[g.grp == "monthly"].plant_code)
    use = g[(g.grp == "monthly") | (~g.plant_code.isin(monthly))].copy()
    use["cls"] = [tech_class(t) for t in use.tech]
    use["op"] = use.source.isin(["eia860", "eia860m_operating"])
    rows = []
    for pc, d in use.groupby("plant_code"):
        op, pl_ = d[d.op], d[~d.op]
        mix = d.groupby("tech").mw.sum().sort_values(ascending=False)
        bat = d[d.cls == "battery"]
        pcod = pl_.dropna(subset=["op_year"])
        first = ""
        if len(pcod):
            y = pcod.op_year.astype(int)
            m = pcod.op_month.fillna(1).astype(int).clip(1, 12)
            first = min(f"{a:04d}-{b:02d}" for a, b in zip(y, m))
        rows.append({"plant_code": int(pc), "tech_mix": ";".join(f"{k}:{v:g}" for k, v in mix.items() if v > 0),
                     "mw_operating": round(float(op.mw.sum()), 2), "mw_planned": round(float(pl_.mw.sum()), 2),
                     "storage_mw": round(float(bat[bat.op].mw.sum()), 2), "storage_mw_planned": round(float(bat[~bat.op].mw.sum()), 2),
                     "storage_mwh": round(float(bat[bat.op].mwh.sum()), 2) if bat.mwh.notna().any() else np.nan, "planned_cod_first": first})
    t = pd.DataFrame(rows)
    p = plants.drop_duplicates("plant_code").rename(columns={"ba": "balancing_authority"})
    out = p[["plant_code", "name", "lat", "lon", "county", "utility", "tdsp", "grid_kv", "balancing_authority"]].merge(t, on="plant_code", how="left")
    out["in_ercot"] = out.balancing_authority.eq("ERCO")
    out = out[["plant_code", "name", "lat", "lon", "county", "tech_mix", "mw_operating", "mw_planned", "storage_mw", "storage_mw_planned", "storage_mwh",
               "planned_cod_first", "utility", "tdsp", "grid_kv", "balancing_authority", "in_ercot"]]
    out["lat"], out["lon"] = out.lat.round(6), out.lon.round(6)
    return out.sort_values("plant_code").reset_index(drop=True)


def coverage_report(df, lines, counts, pairs):
    """Coverage numbers used by notes/phase1-geo.md."""
    d = df.copy()
    d["placed"] = d.match_method != "none"
    d["covered"] = d.match_conf >= PRIORITY_COVER_CONF
    d["band"] = pd.cut(d.match_conf, [-0.001, 0.0, 0.3, 0.5, 0.7, 0.85, 1.0], labels=["none", "0-0.3", "0.3-0.5", "0.5-0.7", "0.7-0.85", "0.85-1"])
    sets = {
        "all_np4160": d[d.in_np4160],
        "np4160_tx_kv_ge_69": d[d.in_np4160 & (d.kv_max >= MIN_TX_KV)],
        "resource_node_subs": d[d.in_np4160 & d.has_resource_node],
        "binding_sced_subs": d[(d.n_binding_sced > 0)],
        "binding_dam_subs": d[(d.n_binding_dam > 0)],
        "binding_any_subs": d[(d.n_binding_sced > 0) | (d.n_binding_dam > 0)],
        "binding_any_in_np4160": d[d.in_np4160 & ((d.n_binding_sced > 0) | (d.n_binding_dam > 0))],
        "binding_any_not_in_np4160": d[~d.in_np4160],
        "priority_union": d[(d.in_np4160 & d.has_resource_node) | (d.n_binding_sced > 0) | (d.n_binding_dam > 0)],
    }
    rep = {}
    for k, x in sets.items():
        rep[k] = dict(n=int(len(x)), placed=int(x.placed.sum()), conf_ge_0_5=int(x.covered.sum()), conf_ge_0_7=int((x.match_conf >= 0.7).sum()),
                      by_method={m: int(v) for m, v in x[x.placed].match_method.value_counts().items()},
                      by_band={str(b): int(v) for b, v in x.band.value_counts().sort_index().items()},
                      mean_conf=float(x.match_conf.mean()))
    return rep


# ------------------------------------------------------------------ validation + graph statistics ----------------
def validation_report(df, lines, pairs, ea, nm, graph_edges):
    """Independent checks on the placements (numbers quoted in notes/phase1-geo.md)."""
    import networkx as nx
    d = df.copy()
    d["conf"] = d.match_conf.astype(float)
    d = d.set_index("sub", drop=False)
    o = lines[lines.source == "osm"]
    g = nx.Graph()
    g.add_edges_from(zip(o.from_osm_sub, o.to_osm_sub))
    rows = []
    for r in pairs.itertuples():
        if r.a in d.index and r.b in d.index:
            a, b = d.loc[r.a], d.loc[r.b]
            if a.match_method == "none" or b.match_method == "none":
                continue
            km = float(haversine_km(a.lat, a.lon, b.lat, b.lon))
            h = None
            if a.osm_id in g and b.osm_id in g:
                try:
                    h = nx.shortest_path_length(g, a.osm_id, b.osm_id)
                except nx.NetworkXNoPath:
                    h = -1
            rows.append(dict(a=r.a, b=r.b, minconf=min(a.conf, b.conf), km=km, hops=h))
    p = pd.DataFrame(rows)
    out = {"constraint_pairs": []}
    for lo, hi in ((0.0, 0.5), (0.5, 0.7), (0.7, 0.85), (0.85, 1.01)):
        y = p[(p.minconf >= lo) & (p.minconf < hi)] if len(p) else p
        if len(y):
            hs = y.hops.dropna()
            out["constraint_pairs"].append(dict(minconf=f"{lo}-{min(hi, 1.0)}", n=int(len(y)), median_km=float(y.km.median()), p90_km=float(y.km.quantile(0.9)),
                                                 within_60km=float((y.km <= 60).mean()), within_120km=float((y.km <= 120).mean()),
                                                 n_with_hops=int(len(hs)), hops_le_3=float((hs.between(0, 3)).mean()) if len(hs) else None,
                                                 hops_le_5=float((hs.between(0, 5)).mean()) if len(hs) else None))
    ag = []
    for sub in set(ea) & set(nm):
        e, n = ea[sub][0], nm[sub][0]
        if e["conf"] >= 0.6 and sub in d.index:
            ag.append(dict(sub=sub, nconf=n["conf"], km=float(haversine_km(e["lat"], e["lon"], n["lat"], n["lon"])), cls=e["cls"]))
    a = pd.DataFrame(ag)
    out["eia_vs_name"] = []
    for lo, hi in ((0.0, 0.3), (0.3, 0.5), (0.5, 0.7), (0.7, 0.85), (0.85, 1.01)):
        y = a[(a.nconf >= lo) & (a.nconf < hi)] if len(a) else a
        if len(y):
            out["eia_vs_name"].append(dict(name_conf=f"{lo}-{min(hi, 1.0)}", n=int(len(y)), within_10km=float((y.km <= 10).mean()), within_25km=float((y.km <= 25).mean()),
                                           median_km=float(y.km.median())))
    m = d[(d.osm_id != "") & (d.conf >= LINK_MIN_CONF)]
    out["osm_sharing"] = dict(subs=int(len(m)), distinct_osm=int(m.osm_id.nunique()), osm_ids_shared=int((m.osm_id.value_counts() > 1).sum()))
    # graph connectivity
    conn = {}
    for thr in (69, 138, 345):
        e = o[o.voltage_kv >= thr]
        gg = nx.Graph()
        gg.add_edges_from(zip(e.from_osm_sub, e.to_osm_sub))
        cc = sorted(nx.connected_components(gg), key=len, reverse=True)
        big = cc[0] if cc else set()
        conn[str(thr)] = dict(edges=int(len(e)), nodes=int(gg.number_of_nodes()), components=int(len(cc)), sizes=[len(c) for c in cc[:6]],
                              matched_subs_conf50_in_graph=int(m.osm_id.isin(gg.nodes).sum()), matched_subs_conf50_in_largest=int(m.osm_id.isin(big).sum()),
                              matched_all_in_largest=int(d[(d.osm_id != "")].osm_id.isin(big).sum()))
    out["connectivity"] = conn
    out["lines_by_kv"] = {f"{k:g}": int(v) for k, v in o.groupby("voltage_kv").size().items()}
    out["line_km_by_kv"] = {f"{k:g}": round(float(v)) for k, v in o.groupby("voltage_kv").length_km.sum().items()}
    out["lines_both_ends_labelled"] = int(((o.from_sub != "") & (o.to_sub != "")).sum())
    out["hifld_lines_total"] = int((lines.source == "hifld").sum())
    out["hifld_in_osm_pair"] = int(((lines.source == "hifld") & (lines.in_osm_pair == True)).sum())  # noqa: E712
    return out


def spot_check_sample(df, seed=11):
    """20 matches for the by-eye check: 8 at conf >= 0.85, 6 at 0.7-0.85, 6 at 0.5-0.7 (random within band)."""
    d = df[df.match_method != "none"].copy()
    d["conf"] = d.match_conf.astype(float)
    parts = []
    for lo, hi, n in ((0.85, 1.01, 8), (0.7, 0.85, 6), (0.5, 0.7, 6)):
        y = d[(d.conf >= lo) & (d.conf < hi) & d.in_np4160]
        parts.append(y.sample(min(n, len(y)), random_state=seed))
    return pd.concat(parts)
