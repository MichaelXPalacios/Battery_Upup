"""Phase 5b: physical risk flags per substation -> data/risk_flags.csv (+ labels sidecar).

  run_grid.py risk [fetch|build|all] [force]

Sources (all public, no key):
  flood      FEMA NFHL "Flood Hazard Zones" layer, ArcGIS REST point query (S_FLD_HAZ_AR)
  wildfire   USFS Wildfire Hazard Potential 2023 (classified, 270 m), sampled at the site and on a ring
  storms     NOAA NCEI Storm Events Database, county counts 2000-2025
  coast      OSM natural=coastline, distance to the Gulf shore (surge exposure proxy)
  seismic    USGS ComCat, M >= 3 within 25 km since 2015 (Permian induced-seismicity flag, flag only)
  heat/cold  the arena's NOAA GHCN weather stations (nearest of eight), coarse
Site-level checks run for subs with match_conf >= 0.5 and kv_max >= 69. Others get county-level storm counts only.
Deterministic code produces every flag. Exclusion rules are the named constants below.
"""
import io
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
import pandas as pd
import requests

from . import manifest
from .paths import RAW, REPO_DATA, RESEARCH_DATA
from .reliability import (BROWSER_UA, MIN_CONF, _overpass_raw, haversine_km, stream_download, texas_counties, write_labels)

# ====================================================================================================== constants
RAW_HAZ = RAW / "hazard"
SITE_MIN_KV = 69.0                  # REAL: the task queries subs at 69 kV and above

# --- FEMA NFHL
FEMA_URL = "https://hazards.fema.gov/arcgis/rest/services/public/NFHL/MapServer/28/query"   # REAL: NFHL Flood Hazard Zones layer
FEMA_FIELDS = "FLD_ZONE,ZONE_SUBTY,SFHA_TF,STATIC_BFE,DFIRM_ID"
FEMA_WORKERS = 2                    # REAL: the task allows at most two simultaneous point queries
FEMA_TIMEOUT_S = 60
FEMA_TRIES = 4
FEMA_BUFFER_M = 100.0               # ASSUMPTION: a 100 m buffer allows for the error in a placed coordinate (plant point or OSM centre)
FEMA_PAUSE_S = 0.15                 # ASSUMPTION: pause between a worker's requests
SFHA_ZONES = {"A", "AE", "AH", "AO", "AR", "A99", "V", "VE"}   # REAL: FEMA special flood hazard area zone codes
SEVERITY = {"VE": 9, "V": 8, "AE": 7, "A": 6, "AH": 5, "AO": 4, "AR": 3, "A99": 2}   # REAL ordering: coastal high hazard first

# --- USFS wildfire hazard potential
WHP_URL = ("https://imagery.geoplatform.gov/iipp/rest/services/Fire_Aviation/"
           "USFS_EDW_RMRS_WildfireHazardPotentialClassified/ImageServer")   # REAL: USFS RMRS WHP 2023 (270 m); the old apps.fs.usda.gov service answers 403 "migrated to IIPP"   # REAL: USFS RMRS WHP 2023, 270 m, hosted on the IIPP server
WHP_CLASSES = {1: "very_low", 2: "low", 3: "moderate", 4: "high", 5: "very_high", 6: "non_burnable", 7: "water"}   # REAL: USFS legend
WHP_RING_M = 600.0                  # ASSUMPTION: also sample eight points on a ring this far out; urban sites read "non-burnable" at the centre
WHP_BATCH_SUBS = 20                 # ASSUMPTION: subs per getSamples request (9 points each)

# --- NOAA Storm Events
STORM_URL = "https://www.ncei.noaa.gov/pub/data/swdi/stormevents/csvfiles/"   # REAL
STORM_YEARS = (2000, 2025)          # REAL: the task's window
STORM_GROUPS = {                    # REAL: NOAA event type names; the grouping is ASSUMPTION
    "hurricane": {"Hurricane", "Hurricane (Typhoon)", "Tropical Storm", "Tropical Depression", "Storm Surge/Tide"},
    "tornado": {"Tornado"},
    "winter": {"Winter Storm", "Ice Storm", "Blizzard", "Heavy Snow", "Winter Weather", "Sleet", "Freezing Fog"},
    "flood": {"Flood", "Flash Flood", "Coastal Flood", "Lakeshore Flood"},
    "wind": {"Thunderstorm Wind", "High Wind", "Strong Wind"},
    "coldheat": {"Extreme Cold/Wind Chill", "Cold/Wind Chill", "Excessive Heat", "Heat", "Frost/Freeze"},
    "wildfire": {"Wildfire"},
}

# --- USGS ComCat
COMCAT_URL = "https://earthquake.usgs.gov/fdsnws/event/1/query"      # REAL
COMCAT_START = "2015-01-01"         # REAL: the task's window
COMCAT_MIN_MAG = 3.0                # REAL: the task's threshold
COMCAT_BOX = dict(minlatitude=25.5, maxlatitude=37.0, minlongitude=-107.0, maxlongitude=-93.0)   # REAL: box around Texas
SEISMIC_RADIUS_KM = 25.0            # REAL: the task's radius
SEISMIC_FLAG_MIN_EVENTS = 3         # ASSUMPTION: events in the radius before the flag is set (one stray event is not a pattern)

# --- coast
COAST_BOX = (25.5, -98.5, 30.5, -93.5)   # REAL: south, west, north, east around the Texas Gulf shore
COASTAL_KM = 25.0                   # ASSUMPTION: within 25 km of the OSM coastline (bays included) counts as coastal / surge-exposed

# --- weather stations (the arena's NOAA GHCN-Daily airports)
STATIONS = {                        # REAL: ids from arena/config.py ERCOT_STATIONS; coordinates are the airports' (approximate, REAL)
    "USW00003927": ("DFW", 32.898, -97.019),
    "USW00012960": ("IAH", 29.980, -95.360),
    "USW00012924": ("CRP", 27.774, -97.512),
    "USW00012919": ("BRO", 25.905, -97.426),
    "USW00023023": ("MAF", 31.949, -102.209),
    "USW00013962": ("ABI", 32.411, -99.682),
    "USW00013904": ("AUS", 30.183, -97.680),
    "USW00012921": ("SAT", 29.544, -98.484),
}
HEAT_F = 100.0                      # ASSUMPTION: a day at or above this Tmax is an extreme-heat day (arena uses 100 F)
COLD_F = 20.0                       # ASSUMPTION: a day at or below this Tmin is an extreme-cold day (arena uses 15 and 32 F)
WX_YEARS = (2003, 2025)             # REAL: the station files start in 2003

# --- exclusion rules (ASSUMPTION: siting judgement, each a named constant; Phase 3 reads exclude_flag)
EXCLUDE_FLOOD_ZONES = SFHA_ZONES    # ASSUMPTION: a point in a special flood hazard area is excluded
EXCLUDE_FLOODWAY = True             # ASSUMPTION: a regulatory floodway is excluded (also inside the SFHA set; reported as its own reason)
EXCLUDE_WILDFIRE_MIN_CLASS = 5      # ASSUMPTION: WHP class 5 (very high) at the site or within the ring excludes the site
EXCLUDE_SEISMIC = False             # REAL (the task): the task says flag only for seismicity: never excludes
EXCLUDE_COASTAL = False             # ASSUMPTION: coastal / surge proximity flags but does not exclude (the NFHL V/AE zones already do)


# ====================================================================================================== fetch
def _subs():
    s = pd.read_csv(REPO_DATA / "substations.csv", dtype={"sub": str}, keep_default_na=False, na_values=[""])   # "NA" is a real substation code
    return s[s.in_np4160.astype(str) == "True"].copy()


def _site_set():
    """Subs assessed at site level, resource-node subs first, then by confidence."""
    s = _subs()
    s = s[(s.match_conf >= MIN_CONF) & (s.kv_max >= SITE_MIN_KV) & s.lat.notna() & s.lon.notna()].copy()
    s["_p"] = (~s.has_resource_node.astype(str).eq("True")).astype(int)
    return s.sort_values(["_p", "match_conf"], ascending=[True, False]).reset_index(drop=True)


def _fema_query(sess, lat, lon, buffer_m):
    p = dict(f="json", geometry=f"{lon:.6f},{lat:.6f}", geometryType="esriGeometryPoint", inSR=4326,
             spatialRel="esriSpatialRelIntersects", outFields=FEMA_FIELDS, returnGeometry="false")
    if buffer_m:
        p.update(distance=buffer_m, units="esriSRUnit_Meter")
    wait = 2.0
    for a in range(1, FEMA_TRIES + 1):
        try:
            r = sess.get(FEMA_URL, params=p, timeout=FEMA_TIMEOUT_S)
            if r.status_code == 200:
                j = r.json()
                if "error" not in j:
                    return [f["attributes"] for f in j.get("features", [])]
            time.sleep(wait)
        except (requests.RequestException, ValueError):
            time.sleep(wait)
        wait *= 2
    return None


def _worst(feats):
    """Most severe zone among features: (zone, subtype, bfe). Floodway wins; then SFHA by severity; then X shaded; then X."""
    best, bk = None, (-1, -1)
    for f in feats:
        z = (f.get("FLD_ZONE") or "").strip()
        sub = (f.get("ZONE_SUBTY") or "") or ""
        k = (1 if "FLOODWAY" in sub.upper() else 0,
             SEVERITY.get(z, 1 if "0.2" in sub else 0))
        if k > bk:
            best, bk = f, k
    return best


def _fema_site(args):
    sess, sub, lat, lon = args
    feats = _fema_query(sess, lat, lon, FEMA_BUFFER_M)
    time.sleep(FEMA_PAUSE_S)
    if feats is None:
        return dict(sub=sub, status="error")
    zones = {(f.get("FLD_ZONE") or "", f.get("ZONE_SUBTY") or "") for f in feats}
    sfha_near = any((f.get("FLD_ZONE") or "").strip() in SFHA_ZONES for f in feats)
    if not feats:
        return dict(sub=sub, status="nodata", sfha_100m=False)
    if len(zones) == 1:
        pt = feats
    else:
        pt = _fema_query(sess, lat, lon, 0)
        time.sleep(FEMA_PAUSE_S)
        if pt is None:
            return dict(sub=sub, status="error")
        if not pt:
            return dict(sub=sub, status="nodata", sfha_100m=sfha_near)
    w = _worst(pt)
    return dict(sub=sub, status="ok", fld_zone=(w.get("FLD_ZONE") or "").strip(), zone_subty=w.get("ZONE_SUBTY") or "",
                sfha_tf=w.get("SFHA_TF") or "", static_bfe=w.get("STATIC_BFE"), dfirm_id=w.get("DFIRM_ID") or "",
                sfha_100m=sfha_near)


def fetch_fema(force=False):
    RAW_HAZ.mkdir(parents=True, exist_ok=True)
    cache = RAW_HAZ / "fema_nfhl_sites.csv"
    sites = _site_set()
    have = pd.read_csv(cache, dtype={"sub": str}, keep_default_na=False, na_values=[""]) if cache.exists() and not force else pd.DataFrame(columns=["sub", "status"])
    have = have[have["sub"].notna() & (have["sub"] != "")]
    done = set(have.loc[have.status.isin(["ok", "nodata"]), "sub"])
    todo = sites[~sites["sub"].isin(done)]
    print(f"FEMA NFHL: {len(sites)} site-level subs, {len(done)} cached, {len(todo)} to query ({FEMA_WORKERS} workers)", flush=True)
    if todo.empty:
        return cache
    rows = have[have.status.isin(["ok", "nodata"])].to_dict("records")
    sessions = [requests.Session() for _ in range(FEMA_WORKERS)]
    for s in sessions:
        s.headers.update({"User-Agent": BROWSER_UA})
    jobs = [(sessions[i % FEMA_WORKERS], r["sub"], r["lat"], r["lon"]) for i, r in enumerate(todo.to_dict("records"))]
    t0 = time.time()
    n = 0
    with ThreadPoolExecutor(FEMA_WORKERS) as ex:
        for out in ex.map(_fema_site, jobs):
            rows.append(out)
            n += 1
            if n % 100 == 0:
                pd.DataFrame(rows).to_csv(cache, index=False)
                print(f"  {n}/{len(jobs)} in {time.time()-t0:.0f}s", flush=True)
    df = pd.DataFrame(rows)
    df["queried_utc"] = pd.Timestamp.now("UTC").isoformat(timespec="seconds")
    df.to_csv(cache, index=False)
    manifest.record("fema-nfhl-rest", "NFHL/MapServer/28:site-queries", FEMA_URL, cache, "web")
    print(f"  FEMA done: {df.status.value_counts().to_dict()}", flush=True)
    return cache


def _ring_points(lat, lon):
    dlat = WHP_RING_M / 111320.0
    dlon = WHP_RING_M / (111320.0 * np.cos(np.radians(lat)))
    pts = [(lon, lat)]
    for k in range(8):
        a = k * np.pi / 4
        pts.append((lon + dlon * np.cos(a), lat + dlat * np.sin(a)))
    return pts


def fetch_whp(force=False):
    RAW_HAZ.mkdir(parents=True, exist_ok=True)
    cache = RAW_HAZ / "whp_samples_sites.csv"
    sites = _site_set()
    rows = []
    # R2 (11 Oct 2026): incremental like fetch_fema. A rerun queries only the site-level subs that have no cached samples, so a station whose placement moved can be
    # refreshed by deleting its rows (the cache is keyed by `sub`, not by coordinate). Before this edit an existing cache skipped the whole fetch.
    if cache.exists() and not force:
        have = pd.read_csv(cache, dtype={"sub": str}, keep_default_na=False, na_values=[""])
        have = have[have["sub"].notna() & (have["sub"] != "")]
        todo_subs = set(sites["sub"]) - set(have["sub"])
        if not todo_subs:
            print("have whp_samples_sites.csv", flush=True)
            return cache
        print(f"WHP: {len(set(have['sub']))} subs cached, {len(todo_subs)} to sample", flush=True)
        rows = have.to_dict("records")
        sites = sites[sites["sub"].isin(todo_subs)].reset_index(drop=True)
    sess = requests.Session()
    sess.headers.update({"User-Agent": BROWSER_UA})
    for i in range(0, len(sites), WHP_BATCH_SUBS):
        chunk = sites.iloc[i:i + WHP_BATCH_SUBS]
        pts, owner = [], []
        for _, r in chunk.iterrows():
            for k, (x, y) in enumerate(_ring_points(r.lat, r.lon)):
                pts.append([round(x, 6), round(y, 6)])
                owner.append((r["sub"], k))
        data = dict(f="json", geometry=json.dumps({"points": pts, "spatialReference": {"wkid": 4326}}),
                    geometryType="esriGeometryMultipoint", returnFirstValueOnly="true", sampleCount=len(pts))
        wait = 3.0
        for a in range(1, 5):
            try:
                resp = sess.post(WHP_URL + "/getSamples", data=data, timeout=120)
                j = resp.json()
                if "samples" in j:
                    break
            except (requests.RequestException, ValueError):
                pass
            time.sleep(wait)
            wait *= 2
        else:
            raise RuntimeError("WHP getSamples failed")
        vals = {s["locationId"]: s.get("value") for s in j["samples"]}
        for idx, (sub, k) in enumerate(owner):
            v = vals.get(idx)
            rows.append(dict(sub=sub, k=k, value=int(float(v)) if v not in (None, "", "NoData") else -1))
        time.sleep(1.0)
        if (i // WHP_BATCH_SUBS) % 20 == 0:
            print(f"  WHP {i+len(chunk)}/{len(sites)}", flush=True)
    pd.DataFrame(rows).to_csv(cache, index=False)
    manifest.record("usfs-whp-imageserver", "WHP2023-classified:site-samples", WHP_URL + "/getSamples", cache, "web")
    return cache


def fetch_storm_events(force=False):
    d = RAW_HAZ / "stormevents"
    d.mkdir(parents=True, exist_ok=True)
    idx = requests.get(STORM_URL, headers={"User-Agent": BROWSER_UA}, timeout=120).text
    import re
    names = dict()
    for m in re.finditer(r'href="(StormEvents_details-ftp_v1\.0_d(\d{4})_c\d+\.csv\.gz)"', idx):
        names[int(m.group(2))] = m.group(1)
    for y in range(STORM_YEARS[0], STORM_YEARS[1] + 1):
        dest = d / names[y]
        if dest.exists() and dest.stat().st_size > 1000 and not force:
            continue
        stream_download(STORM_URL + names[y], dest)
        manifest.record("noaa-storm-events", f"stormevents-details-{y}", STORM_URL + names[y], dest, "web")
        print(f"  storm events {y}: {dest.stat().st_size/1e6:.1f} MB", flush=True)


def fetch_comcat(force=False):
    d = RAW_HAZ / "comcat"
    d.mkdir(parents=True, exist_ok=True)
    dest = d / "comcat_m3_tx_2015_2026.csv"
    if dest.exists() and dest.stat().st_size > 1000 and not force:
        return dest
    end = pd.Timestamp.now("UTC").strftime("%Y-%m-%d")
    p = dict(format="csv", starttime=COMCAT_START, endtime=end, minmagnitude=COMCAT_MIN_MAG, orderby="time-asc", limit=20000, **COMCAT_BOX)
    r = requests.get(COMCAT_URL, params=p, headers={"User-Agent": BROWSER_UA}, timeout=180)
    r.raise_for_status()
    dest.write_bytes(r.content)
    manifest.record("usgs-comcat", "fdsnws-event:M3-TX-box", COMCAT_URL + "?" + "&".join(f"{k}={v}" for k, v in p.items()), dest, "web")
    return dest


def fetch_coastline(force=False):
    d = RAW_HAZ / "osm"
    d.mkdir(parents=True, exist_ok=True)
    out = d / "coastline_tx.json"
    if out.exists() and out.stat().st_size > 1000 and not force:
        return out
    s, w, n, e = COAST_BOX
    q = f'[out:json][timeout:300];way["natural"="coastline"]({s},{w},{n},{e});out geom;'
    return _overpass_raw("coastline_tx", q, out, "osm-overpass")


def fetch_hazard(force=False):
    fetch_storm_events(force)
    fetch_comcat(force)
    fetch_coastline(force)
    fetch_whp(force)
    fetch_fema(force)


# ====================================================================================================== build
def _tf(known, value):
    """True/False where `known`, else empty (None): readable boolean columns that survive a CSV round trip."""
    return pd.Series([bool(v) if k else None for k, v in zip(np.asarray(known), np.asarray(value))], index=known.index if hasattr(known, "index") else None, dtype=object)


def _parse_damage(s):
    if not isinstance(s, str) or not s:
        return 0.0
    s = s.strip()
    mult = {"K": 1e3, "M": 1e6, "B": 1e9}.get(s[-1].upper())
    try:
        return float(s[:-1]) * mult if mult else float(s)
    except ValueError:
        return 0.0


def storm_county_table():
    """County counts of NOAA Storm Events 2000-2025 (Texas), by group. Zone-type records are kept when the zone name is a county name."""
    d = RAW_HAZ / "stormevents"
    frames = []
    for f in sorted(d.glob("StormEvents_details-ftp_v1.0_d*.csv.gz")):
        df = pd.read_csv(f, usecols=["STATE", "EVENT_TYPE", "CZ_TYPE", "CZ_NAME", "BEGIN_YEARMONTH", "DAMAGE_PROPERTY"],
                         dtype=str, low_memory=False)
        frames.append(df[df.STATE == "TEXAS"])
    ev = pd.concat(frames, ignore_index=True)
    ev = ev[ev.CZ_TYPE.isin(["C", "Z"])].copy()
    ev["county"] = (ev.CZ_NAME.str.upper().str.strip()
                    .str.replace(r"^(COASTAL|INLAND|NORTHERN|SOUTHERN|EASTERN/CENTRAL|EASTERN|WESTERN|CENTRAL)\s+", "", regex=True)
                    .str.replace(r"\s+COUNTY$", "", regex=True).replace({"DE WITT": "DEWITT"}))   # ASSUMPTION: zone names that wrap one county resolve to it
    ev["dmg"] = ev.DAMAGE_PROPERTY.map(_parse_damage)
    counties = set(_subs().county.dropna().str.upper())
    census = set(texas_counties().county)
    known = counties | census
    ev["resolved"] = ev.county.isin(known)
    n_all, n_ok = len(ev), int(ev.resolved.sum())
    out = pd.DataFrame(index=sorted(known))
    for g, types in STORM_GROUPS.items():
        out[f"storm_{g}_ct"] = ev[ev.resolved & ev.EVENT_TYPE.isin(types)].groupby("county").size().reindex(out.index).fillna(0).astype(int)
    out["storm_damage_musd"] = (ev[ev.resolved].groupby("county").dmg.sum().reindex(out.index).fillna(0) / 1e6).round(2)
    out.index.name = "county"
    cov = dict(events=n_all, resolved_to_county=n_ok, unresolved_names=sorted(ev.loc[~ev.resolved, "county"].value_counts().head(15).index.tolist()))
    return out.reset_index(), cov


def comcat_table():
    f = RAW_HAZ / "comcat" / "comcat_m3_tx_2015_2026.csv"
    df = pd.read_csv(f)
    df = df[df["type"].eq("earthquake") & (df.mag >= COMCAT_MIN_MAG)]
    return df[["time", "latitude", "longitude", "mag", "place"]].reset_index(drop=True)


def coast_distance_km(lat, lon):
    """Distance from points to the OSM coastline polylines (equirectangular projection around 28 N, accurate to <1% here)."""
    j = json.loads((RAW_HAZ / "osm" / "coastline_tx.json").read_text(encoding="utf-8"))
    segs = []
    for w in j["elements"]:
        g = w.get("geometry") or []
        for a, b in zip(g[:-1], g[1:]):
            segs.append((a["lat"], a["lon"], b["lat"], b["lon"]))
    sg = np.array(segs)
    k = np.cos(np.radians(28.0))
    ax, ay, bx, by = sg[:, 1] * k, sg[:, 0], sg[:, 3] * k, sg[:, 2]
    dx, dy = bx - ax, by - ay
    den = np.maximum(dx * dx + dy * dy, 1e-12)
    out = np.full(len(lat), np.nan)
    lat = np.asarray(lat, float)
    lon = np.asarray(lon, float)
    for i in range(0, len(lat), 100):
        px = (lon[i:i + 100] * k)[:, None]
        py = lat[i:i + 100][:, None]
        t = np.clip(((px - ax) * dx + (py - ay) * dy) / den, 0, 1)
        d2 = (px - (ax + t * dx)) ** 2 + (py - (ay + t * dy)) ** 2
        out[i:i + 100] = np.sqrt(d2.min(axis=1)) * 111.195
    return out


def weather_extremes():
    """Per station: mean extreme-heat days a year (Tmax >= HEAT_F) and extreme-cold days (Tmin <= COLD_F), arena GHCN files."""
    rows = []
    wdir = RESEARCH_DATA / "weather"
    for sid, (nm, la, lo) in STATIONS.items():
        f = wdir / f"{sid}.csv"
        if not f.exists():
            continue
        d = pd.read_csv(f, usecols=["DATE", "TMAX", "TMIN"], parse_dates=["DATE"])
        d = d[(d.DATE.dt.year >= WX_YEARS[0]) & (d.DATE.dt.year <= WX_YEARS[1])]
        yrs = d.DATE.dt.year.nunique()
        rows.append(dict(station=nm, st_lat=la, st_lon=lo, heat_days_yr=round((d.TMAX >= HEAT_F).sum() / yrs, 2),
                         cold_days_yr=round((d.TMIN <= COLD_F).sum() / yrs, 2), tmin_record_f=float(d.TMIN.min())))
    return pd.DataFrame(rows)


def build_risk():
    subs = _subs()
    site = _site_set()
    r = subs[["sub", "county", "zone", "kv_max", "lat", "lon", "match_conf", "has_resource_node"]].copy()
    r["county_u"] = r.county.str.upper()
    r["risk_basis"] = np.where(r["sub"].isin(site["sub"]), "site", np.where(r.county.notna(), "county", "none"))

    # --- flood
    fe = pd.read_csv(RAW_HAZ / "fema_nfhl_sites.csv", dtype={"sub": str}, keep_default_na=False, na_values=[""])
    fe = fe[fe.status.isin(["ok", "nodata"])].drop_duplicates("sub", keep="last")
    r = r.merge(fe.rename(columns={"fld_zone": "flood_zone", "zone_subty": "flood_subtype", "static_bfe": "flood_bfe_ft",
                                    "sfha_100m": "flood_sfha_within_100m"})[
        ["sub", "status", "flood_zone", "flood_subtype", "flood_bfe_ft", "flood_sfha_within_100m"]], on="sub", how="left")
    assessed = r.status.notna()
    r.loc[assessed & (r.status == "nodata"), "flood_zone"] = "NOT_MAPPED"
    r["flood_bfe_ft"] = r.flood_bfe_ft.where(r.flood_bfe_ft > -9000)
    zone = r.flood_zone.fillna("")
    sub_u = r.flood_subtype.fillna("").str.upper()
    flag = pd.Series("not_assessed", index=r.index)
    flag[assessed & zone.isin(SFHA_ZONES)] = "high"
    flag[assessed & zone.eq("X") & sub_u.str.contains("0.2 PCT|FUTURE|LEVEE")] = "moderate"
    flag[assessed & zone.eq("X") & ~sub_u.str.contains("0.2 PCT|FUTURE|LEVEE")] = "minimal"
    flag[assessed & zone.isin(["D", "AREA NOT INCLUDED"])] = "undetermined"
    flag[assessed & zone.eq("NOT_MAPPED")] = "unmapped"
    flag[assessed & zone.eq("OPEN WATER")] = "open_water_check_coordinates"
    r["flood_flag"] = flag
    r["flood_floodway"] = assessed & sub_u.str.contains("FLOODWAY") & ~sub_u.str.contains("AREA OF MINIMAL")
    r = r.drop(columns="status")

    # --- wildfire (site level from WHP samples; county fallback = most severe burnable class among the county's site-level subs)
    wh = pd.read_csv(RAW_HAZ / "whp_samples_sites.csv", dtype={"sub": str}, keep_default_na=False, na_values=[""])
    c = wh[wh.k == 0].set_index("sub").value.rename("whp_site")
    ring = wh[wh.value.between(1, 5)].groupby("sub").value.max().rename("whp_max")
    w = pd.concat([c, ring], axis=1).reset_index()
    r = r.merge(w, on="sub", how="left")
    r["whp_site"] = r.whp_site.where(r.whp_site >= 0)
    r["whp_burn_max"] = np.fmax(r.whp_site.where(r.whp_site.between(1, 5)), r.whp_max)
    r["wildfire_class"] = r.whp_burn_max.map({1: "very_low", 2: "low", 3: "moderate", 4: "high", 5: "very_high"})
    r.loc[r.wildfire_class.isna() & r.whp_site.eq(6), "wildfire_class"] = "non_burnable"
    r.loc[r.wildfire_class.isna() & r.whp_site.eq(7), "wildfire_class"] = "water"
    r["wildfire_basis"] = np.where(r.whp_site.notna(), "site", "none")
    cty = r[r.whp_burn_max.notna()].groupby("county_u").whp_burn_max.max().map({1: "very_low", 2: "low", 3: "moderate", 4: "high", 5: "very_high"})
    fill = r.wildfire_class.isna() & r.county_u.isin(cty.index)
    r.loc[fill, "wildfire_class"] = r.loc[fill, "county_u"].map(cty)
    r.loc[fill, "wildfire_basis"] = "county_site_max"
    r["whp_class_num"] = r.whp_burn_max
    r = r.rename(columns={"whp_site": "whp_class_site_raw", "whp_max": "whp_class_ring_max"})
    r = r.drop(columns=["whp_burn_max"])

    # --- storms (county)
    st, cov = storm_county_table()
    r = r.merge(st.rename(columns={"county": "county_u"}), on="county_u", how="left")
    for col in [c for c in st.columns if c != "county"]:
        r[col] = r[col].where(r.county_u.notna())

    # --- coast
    has = r.lat.notna()
    r["dist_coast_km"] = np.nan
    r.loc[has, "dist_coast_km"] = coast_distance_km(r.loc[has, "lat"].values, r.loc[has, "lon"].values).round(1)
    r["coastal_flag"] = _tf(r.dist_coast_km.notna(), r.dist_coast_km <= COASTAL_KM)

    # --- seismic
    eq = comcat_table()
    r["seismic_n_m3_25km"] = np.nan
    r["seismic_max_mag"] = np.nan
    for i in r.index[has]:
        d = haversine_km(r.at[i, "lat"], r.at[i, "lon"], eq.latitude.values, eq.longitude.values)
        m = d <= SEISMIC_RADIUS_KM
        r.at[i, "seismic_n_m3_25km"] = int(m.sum())
        r.at[i, "seismic_max_mag"] = float(eq.mag.values[m].max()) if m.any() else np.nan
    r["seismic_flag"] = _tf(r.seismic_n_m3_25km.notna(), r.seismic_n_m3_25km >= SEISMIC_FLAG_MIN_EVENTS)

    # --- heat and cold (nearest of the arena's eight stations)
    wx = weather_extremes()
    r["wx_station"] = None
    r["wx_station_km"] = np.nan
    r["heat_days_100f_yr"] = np.nan
    r["cold_days_20f_yr"] = np.nan
    for i in r.index[has]:
        d = haversine_km(r.at[i, "lat"], r.at[i, "lon"], wx.st_lat.values, wx.st_lon.values)
        j = int(np.argmin(d))
        r.at[i, "wx_station"] = wx.station.iloc[j]
        r.at[i, "wx_station_km"] = round(float(d[j]), 0)
        r.at[i, "heat_days_100f_yr"] = wx.heat_days_yr.iloc[j]
        r.at[i, "cold_days_20f_yr"] = wx.cold_days_yr.iloc[j]

    # --- exclusion
    reason = pd.Series("", index=r.index)
    site_mask = r.risk_basis.eq("site")
    if EXCLUDE_FLOODWAY:
        reason[site_mask & r.flood_floodway.fillna(False)] = "floodway"
    reason[site_mask & reason.eq("") & r.flood_zone.isin(EXCLUDE_FLOOD_ZONES)] = "flood_sfha"
    reason[site_mask & reason.eq("") & (r.whp_class_num >= EXCLUDE_WILDFIRE_MIN_CLASS)] = "wildfire_very_high"
    if EXCLUDE_SEISMIC:
        reason[site_mask & reason.eq("") & r.seismic_flag.astype(str).eq("True")] = "seismic"
    if EXCLUDE_COASTAL:
        reason[site_mask & reason.eq("") & r.coastal_flag.astype(str).eq("True")] = "coastal"
    r["exclude_flag"] = _tf(site_mask, reason.ne(""))
    r["exclude_reason"] = np.where(site_mask, np.where(reason.eq(""), "none", reason), "not_assessed")

    cols = ["sub", "county", "zone", "kv_max", "lat", "lon", "match_conf", "risk_basis",
            "flood_zone", "flood_subtype", "flood_flag", "flood_floodway", "flood_sfha_within_100m", "flood_bfe_ft",
            "wildfire_class", "wildfire_basis", "whp_class_site_raw", "whp_class_ring_max",
            "storm_hurricane_ct", "storm_tornado_ct", "storm_winter_ct", "storm_flood_ct", "storm_wind_ct",
            "storm_coldheat_ct", "storm_wildfire_ct", "storm_damage_musd",
            "dist_coast_km", "coastal_flag", "seismic_n_m3_25km", "seismic_max_mag", "seismic_flag",
            "wx_station", "wx_station_km", "heat_days_100f_yr", "cold_days_20f_yr", "exclude_flag", "exclude_reason"]
    out = r[cols].copy()
    out["flood_floodway"] = _tf(site_mask, out.flood_floodway.fillna(False).astype(bool))
    out.to_csv(REPO_DATA / "risk_flags.csv", index=False)
    write_labels("risk_flags", RISK_LABELS)
    summ = dict(rows=len(out), site=int(site_mask.sum()), exclude=out.loc[site_mask & out.exclude_flag.astype(str).eq("True"), "exclude_reason"].value_counts().to_dict(), flood_flag=out.flood_flag.value_counts().to_dict(),
                storm_cov=cov)
    (RAW_HAZ / "risk_build_summary.json").write_text(json.dumps(summ, indent=1, default=str), encoding="utf-8")
    print(json.dumps(summ, indent=1, default=str), flush=True)
    return out


RISK_LABELS = [
    ("sub", "REAL", "ERCOT NP4-160 SUBSTATION", "join key"),
    ("county", "DERIVED", "data/substations.csv (Census TIGER polygons)", "county of the placed coordinate"),
    ("zone", "REAL", "ERCOT NP4-160", "load zone"),
    ("kv_max", "REAL", "ERCOT NP4-160", ""),
    ("lat", "DERIVED", "data/substations.csv", "placed coordinate; for plant-matched subs this is the plant, not the fence"),
    ("lon", "DERIVED", "data/substations.csv", ""),
    ("match_conf", "DERIVED", "data/substations.csv", "site-level checks need >= 0.5"),
    ("risk_basis", "DERIVED", "risk.py", "site = FEMA/WHP/seismic at the coordinate (match_conf >= 0.5, kv >= 69); county = county-level storm counts only; none = no county"),
    ("flood_zone", "REAL", "FEMA NFHL S_FLD_HAZ_AR (point query, worst zone in a 100 m buffer if the point is ambiguous)", "NOT_MAPPED = no NFHL polygon"),
    ("flood_subtype", "REAL", "FEMA NFHL ZONE_SUBTY", ""),
    ("flood_flag", "DERIVED", "risk.py from flood_zone", "high = special flood hazard area; moderate = 0.2% annual chance; minimal; unmapped; undetermined"),
    ("flood_floodway", "DERIVED", "risk.py from ZONE_SUBTY", "regulatory floodway"),
    ("flood_sfha_within_100m", "DERIVED", "risk.py, FEMA_BUFFER_M", "a special flood hazard area lies within 100 m (coordinate-error allowance)"),
    ("flood_bfe_ft", "REAL", "FEMA NFHL STATIC_BFE", "base flood elevation where published"),
    ("wildfire_class", "DERIVED", "USFS WHP 2023 classified raster (site + 8-point 600 m ring); county_site_max fallback", "most severe burnable class; non_burnable/water if none"),
    ("wildfire_basis", "DERIVED", "risk.py", "site or county_site_max"),
    ("whp_class_site_raw", "REAL", "USFS WHP 2023", "raw class at the coordinate: 1 very low .. 5 very high, 6 non-burnable, 7 water"),
    ("whp_class_ring_max", "REAL", "USFS WHP 2023", "max burnable class on the ring"),
    ("storm_hurricane_ct", "REAL", "NOAA NCEI Storm Events 2000-2025, county", "hurricane, tropical storm/depression, storm surge records"),
    ("storm_tornado_ct", "REAL", "NOAA Storm Events", ""),
    ("storm_winter_ct", "REAL", "NOAA Storm Events", "winter storm, ice storm, blizzard, heavy snow, sleet, winter weather, freezing fog"),
    ("storm_flood_ct", "REAL", "NOAA Storm Events", "flood, flash flood, coastal flood"),
    ("storm_wind_ct", "REAL", "NOAA Storm Events", "thunderstorm wind, high wind, strong wind"),
    ("storm_coldheat_ct", "REAL", "NOAA Storm Events", "extreme cold, cold, excessive heat, heat, frost/freeze"),
    ("storm_wildfire_ct", "REAL", "NOAA Storm Events", ""),
    ("storm_damage_musd", "REAL", "NOAA Storm Events DAMAGE_PROPERTY, all event types", "nominal $ million, county"),
    ("dist_coast_km", "DERIVED", "OSM natural=coastline (bays included)", "straight-line distance"),
    ("coastal_flag", "DERIVED", "risk.py, COASTAL_KM", "within 25 km of the coastline (ASSUMPTION threshold)"),
    ("seismic_n_m3_25km", "REAL", "USGS ComCat M>=3 since 2015", "events within 25 km"),
    ("seismic_max_mag", "REAL", "USGS ComCat", ""),
    ("seismic_flag", "DERIVED", "risk.py, SEISMIC_FLAG_MIN_EVENTS", "Permian induced-seismicity flag only; never excludes"),
    ("wx_station", "DERIVED", "arena NOAA GHCN-Daily airports", "nearest of eight; coarse, can be several hundred km away"),
    ("wx_station_km", "DERIVED", "risk.py", ""),
    ("heat_days_100f_yr", "DERIVED", "arena NOAA GHCN-Daily 2003-2025", "mean days a year with Tmax >= 100 F at the nearest station"),
    ("cold_days_20f_yr", "DERIVED", "arena NOAA GHCN-Daily 2003-2025", "mean days a year with Tmin <= 20 F at the nearest station"),
    ("exclude_flag", "DERIVED", "risk.py exclusion constants", "True for site-assessed subs that trip a rule; blank if not assessed"),
    ("exclude_reason", "DERIVED", "risk.py", "floodway, flood_sfha, wildfire_very_high (first rule that trips), none, or not_assessed"),
]


def risk(what="all", *flags):
    force = "--force" in flags or "force" in flags or what in ("--force", "force")
    if what in ("--force", "force"):
        what = "all"
    if what in ("fetch", "all"):
        fetch_hazard(force)
    if what in ("build", "all"):
        build_risk()
