"""Phase 5a: reliability. Outage history (EAGLE-I), Oncor feeder SAIFI/SAIDI, critical facilities (OSM), radial
pockets (line graph), a combined score, and the unserved energy a 1 MW / 4 MWh battery would have avoided.

  run_grid.py fetch-reliability [eaglei|oncor|osm|voll|all] [force]   downloads into research-data/grid/raw/reliability/
  run_grid.py reliability [fresh]                              builds data/eaglei_county.csv, critical_facilities.csv,
                                                              reliability.csv (+ .labels.csv sidecars)

Market value and reliability value stay separate. Dollars appear only through the named VoLL constant below.
Deterministic code produces every number and rank. Labels: REAL, DERIVED, SIM, ASSUMPTION.
"""
import csv
import gzip
import hashlib
import io
import json
import math
import re
import time
import zipfile
from pathlib import Path

import numpy as np
import pandas as pd
import requests

from . import manifest
from .paths import RAW, REPO_DATA, DERIVED, NOTES

# ====================================================================================================== constants
# Every unverified constant is a single named value here. The comment gives its label and source.

# --- sources
RAW_REL = RAW / "reliability"
FIGSHARE_API = "https://api.figshare.com/v2/articles/24237376"      # REAL: EAGLE-I 2014-2025, ORNL, CC BY 4.0 (v4)
EAGLEI_FIPS_PREFIX = b"48"                                           # REAL: Texas FIPS state code
ONCOR_SQR_ZIP_URL = "https://interchange.puc.texas.gov/Documents/56005_8_1366305.ZIP"   # REAL: Oncor 2023 Service Quality Report, PUCT 56005 item 8
VOLL_PDF_URL = "https://brattle.com/wp-content/uploads/2024/09/Value-of-Lost-Load-Study-for-the-ERCOT-Region.pdf"  # REAL: Brattle VoLL study
OVERPASS_URL = "https://overpass-api.de/api/interpreter"             # REAL: public Overpass endpoint
OVERPASS_UA = "hugging-base-grid-research/0.1"                       # REAL: the endpoint answers 406 to browser-like agents
BROWSER_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
              "Chrome/124.0 Safari/537.36")                          # REAL: PUCT / figshare accept this
OVERPASS_TIMEOUT_S = 600                                             # server-side query timeout
OVERPASS_HTTP_TIMEOUT_S = 900                                        # client read timeout
OVERPASS_MAX_TRIES = 6
OVERPASS_BACKOFF_S = 45
OVERPASS_PAUSE_S = 10
TX_AREA = 'area["ISO3166-2"="US-TX"]->.a;'                           # REAL: OSM area for Texas
CRITICAL_QUERIES = {                                                 # REAL: OSM tag definitions
    "hospitals": 'nwr["amenity"="hospital"](area.a);',
    "water": 'nwr["man_made"~"^(water_works|wastewater_plant)$"](area.a);',
    "emergency": 'nwr["amenity"~"^(fire_station|police)$"](area.a);nwr["emergency"="ambulance_station"](area.a);',
}

# --- EAGLE-I processing
EAGLEI_WINDOW_START = 2018        # ASSUMPTION: DQI.csv shows customer coverage of 85-98% from 2018; 2014-2017 files are patchy
EAGLEI_WINDOW_END = 2025          # REAL: last full-year file in the figshare article
EAGLEI_STEP_H = 0.25              # REAL: EAGLE-I snapshots are every 15 minutes
NA_FILL_ROWS = 12                 # ASSUMPTION: explicit NA snapshots (2017-2021 files) carry the last value for up to 12 snapshots (3 h), then count as 0
EVENT_MIN_PCT = 1.0               # ASSUMPTION: an "event" is a run of snapshots with at least this % of county customers out
EVENT_MIN_CUSTOMERS = 25          # ASSUMPTION: floor on that threshold so tiny counties do not log noise
DEN_OK_RATIO = 1.2                # ASSUMPTION: the 99.9th percentile of a county's customers_out up to 1.2 x its EAGLE-I customer count is accepted (meter undercount)
DEN_ARTIFACT_RATIO = 1.5          # ASSUMPTION: beyond 1.5 x, a utility reported at system level or under the wrong county: share metrics are dropped
DEN_QUANTILE = 0.999              # ASSUMPTION: quantile used instead of the maximum so one bad snapshot does not flag a county
EVENT_GAP_H = 2.0                 # ASSUMPTION: two qualifying snapshots less than this apart belong to one event (reports drop out)
NAMED_EVENTS = {                  # REAL: dates from docs/research/ercot.md section 7.2 (UTC windows widened by a day each side)
    "uri": ("2021-02-13", "2021-02-23"),
    "derecho": ("2024-05-16", "2024-05-23"),
    "beryl": ("2024-07-08", "2024-07-22"),
}

# --- battery and VoLL
BATT_MW = 1.0                     # REAL: the battery the task fixes (1 MW)
BATT_MWH = 4.0                    # REAL: the battery the task fixes (4 MWh)
BATT_USABLE_FRAC = 0.90           # ASSUMPTION: usable share of nameplate energy during an outage (depth of discharge and losses)
SOC_FULL = 1.0                    # ASSUMPTION: bound 1: a dedicated backup battery sits full when the outage starts
SOC_MARKET_RESERVE = 0.20         # ASSUMPTION: bound 2: a market-dispatched fleet holds only its 20% reserve (arena config, home spec)
CRITICAL_LOAD_MW = 1.0            # ASSUMPTION: the battery serves 1 MW of critical load for the whole outage (it never sits idle)
VOLL_USD_MWH = 35685.0            # REAL: Brattle "Value of Lost Load Study for the ERCOT Region", Aug 2024, PUCT Project 55837: the one-hour
                                  # system-wide VoLL is $35,685/MWh; ERCOT recommended about $35,000 for planning including the reliability
                                  # standard; Utility Dive reports the PUCT adopted the study on 29 Aug 2024 (the order itself was not read: UNVERIFIED). Longer outages are valued lower per
                                  # MWh in the study, so a multi-day event at this price is an upper bound.
VOLL_RESIDENTIAL_USD_MWH = 4000.0 # REAL: same study, residential class, one-hour outage (about $4,000/MWh, energychoicematters 22 Aug 2024)
VOLL_DURATION_DISCOUNT = 1.0      # ASSUMPTION: no discount for outage length (1.0 = use VoLL as published)

# --- critical facilities
RADIUS_HOSPITAL_KM = 10.0         # ASSUMPTION: a hospital within 10 km counts as served by the substation area
RADIUS_WATER_KM = 10.0            # ASSUMPTION: water and wastewater works within 10 km
RADIUS_EMERGENCY_KM = 5.0         # ASSUMPTION: fire, police and ambulance stations within 5 km
FACILITY_WEIGHTS = {"hospital": 5.0, "water": 3.0, "emergency": 1.0}   # ASSUMPTION: criticality weight per facility type

# --- network weakness
GRAPH_MIN_KV = 69.0               # REAL: lines.csv holds lines at or above 69 kV
BACKBONE_KV = 345.0               # REAL: ERCOT bulk backbone voltage (>= 345 kV)
MAX_PATHS_COUNTED = 3             # ASSUMPTION: edge connectivity is capped at 3 independent paths
POCKET_SATURATION = 8             # ASSUMPTION: a pocket of this many substations (or more) behind one corridor gives the size part 1.0 (largest pocket 18, median 1)
NODE_JOIN_KM = 3.0                # ASSUMPTION: unlinked placed subs borrow the nearest line-graph node within this distance
MIN_CONF = 0.5                    # REAL: phase1-geo.md says use match_conf >= 0.5 for site-level work

# --- Oncor SAIFI / SAIDI
SAIDI_SATURATION_MIN = 160.0      # ASSUMPTION: feeder SAIDI of about the Oncor p90 (162 min, DATA-GRID-ASSETS.md) gives sub-score 1.0
SAIFI_SATURATION = 1.75           # ASSUMPTION: feeder SAIFI of about the Oncor p90 (1.75) gives sub-score 1.0

# --- large-load growth
LARGE_LOAD_RADIUS_KM = 50.0       # ASSUMPTION: large-load growth within 50 km of the substation stresses it
LARGE_LOAD_SATURATION_MW = 5000.0 # ASSUMPTION: 5 GW of conditional Batch Zero large load in the county (own + half of neighbours) gives sub-score 1.0
LARGE_LOAD_NEARBY_WEIGHT = 0.5    # ASSUMPTION: large load in a neighbouring county within the radius counts half as much as load in the sub's own county

# --- combined score (ASSUMPTION: the weights are a judgement; they sum to 1 and are reported beside every sub-score)
SCORE_WEIGHTS = {"outage": 0.30, "pocket": 0.25, "facilities": 0.20, "oncor": 0.10, "large_load": 0.15}   # ASSUMPTION: judgement weights, sum 1; renormalised over the components present
OUTAGE_MIX = {"hours": 0.5, "p95": 0.25, "named": 0.25}   # ASSUMPTION: blend inside the outage sub-score; each part is a percentile rank across Texas counties
                                                          # (no saturation guess: a rank of 1.0 means the worst county on that measure)
NEED_TOP_N = 20                   # REAL: the task asks for the top 20 by reliability need

LABELS_COLS = ["column", "label", "source", "note"]


# ====================================================================================================== helpers
def write_labels(name, rows):
    """data/<name>.labels.csv from (column, label, source, note) tuples."""
    with open(REPO_DATA / f"{name}.labels.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(LABELS_COLS)
        w.writerows(rows)


def haversine_km(lat1, lon1, lat2, lon2):
    lat1, lon1, lat2, lon2 = map(np.radians, (lat1, lon1, lat2, lon2))
    a = np.sin((lat2 - lat1) / 2) ** 2 + np.cos(lat1) * np.cos(lat2) * np.sin((lon2 - lon1) / 2) ** 2
    return 2 * 6371.0088 * np.arcsin(np.sqrt(a))


def unit_xyz(lat, lon):
    """Unit-sphere coordinates for a cKDTree (chord length * R approximates great-circle distance well below 100 km)."""
    lat = np.radians(np.asarray(lat, float))
    lon = np.radians(np.asarray(lon, float))
    return np.column_stack([np.cos(lat) * np.cos(lon), np.cos(lat) * np.sin(lon), np.sin(lat)])


def chord_km(km):
    return 2 * np.sin(km / 6371.0088 / 2)


def stream_download(url, dest, ua=BROWSER_UA, tries=3, timeout=300):
    dest.parent.mkdir(parents=True, exist_ok=True)
    last = None
    for a in range(1, tries + 1):
        try:
            with requests.get(url, headers={"User-Agent": ua} if ua else {}, stream=True, timeout=timeout) as r:
                r.raise_for_status()
                if r.status_code != 200:
                    raise requests.RequestException(f"HTTP {r.status_code} (no content)")
                with open(dest, "wb") as f:
                    for chunk in r.iter_content(1 << 20):
                        f.write(chunk)
            if dest.stat().st_size == 0:
                raise requests.RequestException("empty body")
            return dest
        except requests.RequestException as e:
            last = e
            print(f"  {dest.name}: {type(e).__name__} (try {a}); retrying", flush=True)
            time.sleep(5 * a)
    raise RuntimeError(f"download failed: {url}: {last}")


def overpass_query(name, body, out_dir, source="osm-overpass"):
    """Overpass with back-off. `body` is the union of selectors; saved as out_dir/<name>.json and recorded in the manifest."""
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"{name}.json"
    q = f"[out:json][timeout:{OVERPASS_TIMEOUT_S}];{TX_AREA}({body});out center tags;"
    return _overpass_raw(name, q, out, source)


def _overpass_raw(name, q, out, source):
    wait = OVERPASS_BACKOFF_S
    for attempt in range(1, OVERPASS_MAX_TRIES + 1):
        try:
            r = requests.post(OVERPASS_URL, data={"data": q}, headers={"User-Agent": OVERPASS_UA}, timeout=OVERPASS_HTTP_TIMEOUT_S)
            if r.status_code == 200 and r.content.lstrip().startswith(b"{"):
                payload = r.json()
                if "remark" in payload and not payload.get("elements"):
                    raise RuntimeError("overpass remark with no elements: " + payload["remark"][:120])
                out.write_bytes(r.content)
                manifest.record(source, f"overpass:{name}", OVERPASS_URL + "#" + name, out, "web")
                print(f"  saved {out.name}: {len(payload.get('elements', []))} elements, {out.stat().st_size/1e6:.1f} MB", flush=True)
                return out
            print(f"  {name}: HTTP {r.status_code} (try {attempt}); backing off {wait}s", flush=True)
        except (requests.RequestException, RuntimeError, ValueError) as e:
            print(f"  {name}: {type(e).__name__}: {str(e)[:120]} (try {attempt}); backing off {wait}s", flush=True)
        time.sleep(wait)
        wait *= 2
    raise RuntimeError(f"overpass query {name} failed after {OVERPASS_MAX_TRIES} tries")


# ====================================================================================================== fetch
def fetch_eaglei(years=None, force=False):
    """Stream each national yearly file once and keep only the Texas rows (FIPS 48xxx). The national files are 0.6-1.4 GB each,
    so the filtered copy is what is stored; the sha256 of the full stream is kept in eaglei_fullstream.json for audit."""
    d = RAW_REL / "eaglei"
    d.mkdir(parents=True, exist_ok=True)
    art = requests.get(FIGSHARE_API, timeout=60).json()
    files = {f["name"]: f for f in art["files"]}
    side = d / "eaglei_fullstream.json"
    audit = json.loads(side.read_text(encoding="utf-8")) if side.exists() else {}
    for small in ("MCC.csv", "DQI.csv", "coverage_history.csv"):
        dest = d / small
        if small in files and (force or not dest.exists() or dest.stat().st_size == 0):
            stream_download(files[small]["download_url"], dest, ua=None)   # figshare answers a browser UA with an empty HTTP 202
            manifest.record("eaglei-ornl-figshare", f"eaglei:{small}", files[small]["download_url"], dest, "web")
            print(f"  saved {small}", flush=True)
    names = sorted(n for n in files if re.fullmatch(r"eaglei_outages_\d{4}\.csv", n))
    for n in names:
        y = int(n[15:19])
        if years and y not in years:
            continue
        dest = d / f"eaglei_outages_{y}_TX.csv"
        if dest.exists() and dest.stat().st_size > 1000 and not force:
            print(f"have {dest.name}", flush=True)
            continue
        url = files[n]["download_url"]
        print(f"eaglei {y}: streaming {files[n]['size']/1e6:.0f} MB ...", flush=True)
        t0 = time.time()
        for attempt in range(1, 4):
            try:
                h = hashlib.sha256()
                total = 0
                rows = 0
                with requests.get(url, stream=True, timeout=300) as r:      # default UA: figshare answers a browser UA with an empty HTTP 202
                    r.raise_for_status()
                    if r.status_code != 200:
                        raise requests.RequestException(f"HTTP {r.status_code}")
                    tmp = dest.with_suffix(".part")
                    with open(tmp, "wb") as out:
                        rem = b""
                        first = True
                        for chunk in r.iter_content(4 << 20):
                            h.update(chunk)
                            total += len(chunk)
                            buf = rem + chunk
                            cut = buf.rfind(b"\n")
                            if cut < 0:
                                rem = buf
                                continue
                            rem = buf[cut + 1:]
                            lines = buf[:cut + 1].split(b"\n")
                            keep = [ln for ln in lines if ln.startswith(EAGLEI_FIPS_PREFIX) or (first and ln.startswith(b"fips_code"))]
                            first = False
                            rows += len(keep)
                            if keep:
                                out.write(b"\n".join(keep) + b"\n")
                        if rem.startswith(EAGLEI_FIPS_PREFIX):
                            out.write(rem + b"\n")
                            rows += 1
                if total == 0 or rows == 0:
                    raise requests.RequestException("empty stream")
                tmp.replace(dest)
                break
            except requests.RequestException as e:
                print(f"  eaglei {y}: {type(e).__name__} (try {attempt}); retrying", flush=True)
                time.sleep(10 * attempt)
        else:
            raise RuntimeError(f"eaglei {y} failed")
        audit[str(y)] = {"national_bytes": total, "national_sha256": h.hexdigest(), "tx_rows": rows, "url": url}
        side.write_text(json.dumps(audit, indent=1), encoding="utf-8")
        manifest.record("eaglei-ornl-figshare", f"eaglei_outages_{y}.csv#TX-rows-only", url, dest, "web")
        print(f"  eaglei {y}: {rows:,} Texas rows of national {total/1e6:.0f} MB in {time.time()-t0:.0f}s", flush=True)


def fetch_oncor(force=False):
    d = RAW_REL / "oncor"
    d.mkdir(parents=True, exist_ok=True)
    z = d / "56005_8_1366305.ZIP"
    if force or not z.exists():
        stream_download(ONCOR_SQR_ZIP_URL, z)
        manifest.record("puct-interchange", "56005-item8-oncor-sqr-2023", ONCOR_SQR_ZIP_URL, z, "web")
    ex = d / "extracted"
    ex.mkdir(exist_ok=True)
    with zipfile.ZipFile(z) as zf:
        for m in zf.namelist():
            tgt = (ex / m).resolve()
            if not str(tgt).startswith(str(ex.resolve())):
                raise RuntimeError("unsafe path in zip: " + m)
        zf.extractall(ex)
    print("  oncor zip extracted:", [p.name for p in ex.rglob("*") if p.is_file()], flush=True)


def fetch_osm_critical(force=False):
    d = RAW_REL / "osm"
    for name, body in CRITICAL_QUERIES.items():
        out = d / f"{name}.json"
        if out.exists() and out.stat().st_size > 1000 and not force:
            print(f"have {out.name}", flush=True)
            continue
        print(f"overpass {name} ...", flush=True)
        overpass_query(name, body, d)
        time.sleep(OVERPASS_PAUSE_S)


def fetch_voll(force=False):
    d = RAW_REL / "voll"
    dest = d / "Brattle_Value_of_Lost_Load_Study_ERCOT_2024.pdf"
    if force or not dest.exists():
        try:
            stream_download(VOLL_PDF_URL, dest, tries=2)
        except RuntimeError as e:        # brattle.com answers 403 to scripted clients; place the PDF by hand and re-run
            print("  VoLL study not downloaded (kept as a citation only):", str(e)[:100], flush=True)
            return
        manifest.record("brattle-voll", "puct-55837-voll-study", VOLL_PDF_URL, dest, "web")
        print("  saved VoLL study", flush=True)


def fetch_reliability(what="all", *flags):
    force = "--force" in flags or "force" in flags or what in ("--force", "force")   # argparse rejects bare --flags: `-- --force` or `force` both work
    if what in ("--force", "force"):
        what = "all"
    what = what or "all"
    if what in ("oncor", "all"):
        fetch_oncor(force)
    if what in ("voll", "all"):
        fetch_voll(force)
    if what in ("osm", "all"):
        fetch_osm_critical(force)
    if what in ("eaglei", "all"):
        fetch_eaglei(force=force)


# ====================================================================================================== common tables
def read_dbf(path):
    """Minimal dBase reader (the venv has no pyshp/geopandas)."""
    import struct
    with open(path, "rb") as f:
        hdr = f.read(32)
        nrec = struct.unpack("<I", hdr[4:8])[0]
        hlen, rlen = struct.unpack("<HH", hdr[8:12])
        fields, off = [], 1
        while True:
            fd = f.read(32)
            if fd[0] == 0x0D:
                break
            fields.append((fd[:11].split(b"\0")[0].decode(), off, fd[16]))
            off += fd[16]
        f.seek(hlen)
        rows = []
        for _ in range(nrec):
            rec = f.read(rlen)
            rows.append({nm: rec[o:o + ln].decode("utf-8", "replace").strip() for nm, o, ln in fields})
    return pd.DataFrame(rows)


def texas_counties():
    """fips (5 digits, '48xxx'), county (upper case) from the Census cartographic file already in raw/geo."""
    d = read_dbf(RAW / "geo" / "cb_2023_us_county_500k" / "cb_2023_us_county_500k.dbf")
    d = d[d.STATEFP == "48"]
    return pd.DataFrame({"fips": "48" + d.COUNTYFP, "county": d.NAME.str.upper()}).reset_index(drop=True)


def load_subs():
    s = pd.read_csv(REPO_DATA / "substations.csv", dtype={"sub": str}, keep_default_na=False, na_values=[""])   # "NA" is a real substation code
    s = s[s.in_np4160.astype(str) == "True"].copy()
    s["has_resource_node"] = s.has_resource_node.astype(str).eq("True")
    return s.reset_index(drop=True)


# ====================================================================================================== EAGLE-I
VOLL_BY_DURATION_H = {1: 35685.0, 2: 21326.0, 4: 13340.0}   # REAL: Brattle Aug 2024, ERCOT-wide average $/unserved MWh by outage duration
                                                             # (8 h $10,435 and 16 h $13,581 are in the study; a 4 MWh battery never covers beyond 4 h of a 1 MW load)


def voll_for_hours(h):
    """Duration-adjusted VoLL ($/MWh) by linear interpolation of the Brattle table (1, 2 and 4 hours); flat outside."""
    xs, ys = zip(*sorted(VOLL_BY_DURATION_H.items()))
    return float(np.interp(h, xs, ys))


def _event_table(ts, cust, thr, step=EAGLEI_STEP_H, gap=EVENT_GAP_H):
    """Segment one county's series (sorted snapshot times in hours, customers out) into events.
    Returns a DataFrame: start_h, end_h, peak, cust_hours, duration_h."""
    q = np.flatnonzero(cust >= thr)
    if q.size == 0:
        return pd.DataFrame(columns=["start_h", "end_h", "peak", "cust_hours", "duration_h"])
    breaks = np.flatnonzero(np.diff(ts[q]) > gap) + 1
    firsts = np.concatenate([[0], breaks])
    lasts = np.concatenate([breaks - 1, [q.size - 1]])
    out = []
    csum = np.concatenate([[0.0], np.cumsum(cust.astype(float))])
    for a, b in zip(firsts, lasts):
        i0, i1 = q[a], q[b]
        out.append((ts[i0], ts[i1] + step, float(cust[i0:i1 + 1].max()), float(csum[i1 + 1] - csum[i0]) * step, ts[i1] + step - ts[i0]))
    return pd.DataFrame(out, columns=["start_h", "end_h", "peak", "cust_hours", "duration_h"])


def battery_avoided(peak, cust_hours, n_cust, soc):
    """Expected unserved energy (MWh) a 1 MW / 4 MWh battery at a random site of the county avoids in one event.
    f = share of county customers hit (peak / customers); d = mean outage hours per affected customer (customer-hours / peak);
    the battery serves CRITICAL_LOAD_MW up to its usable energy: min(P*d, E*usable*soc).  Returns (mwh, covered_hours, f, d)."""
    f = min(1.0, peak / max(n_cust, 1.0))
    d = cust_hours / max(peak, 1.0)
    e = BATT_MWH * BATT_USABLE_FRAC * soc
    p = min(CRITICAL_LOAD_MW, BATT_MW)
    served = min(p * d, e)
    return f * served, served / p, f, d


def build_eaglei():
    d = RAW_REL / "eaglei"
    files = sorted(f for f in d.glob("eaglei_outages_*_TX.csv") if f.stat().st_size > 1000)
    if not files:
        raise SystemExit("no EAGLE-I Texas files: run fetch-reliability eaglei first")
    mcc = pd.read_csv(d / "MCC.csv", encoding="utf-8-sig")
    mcc = mcc[pd.to_numeric(mcc.County_FIPS, errors="coerce").notna()].copy()         # MCC.csv ends with a "Grand Total" row
    mcc["fips"] = mcc.County_FIPS.astype(int).astype(str).str.zfill(5)
    cust_n = mcc.set_index("fips").Customers
    tc = texas_counties()
    frames = []
    for f in files:
        col = "customers_out" if "customers_out" in pd.read_csv(f, nrows=0).columns else "sum"      # the 2023+ files call it "sum"
        df = pd.read_csv(f, dtype={"fips_code": "int32", col: "float32"}, usecols=["fips_code", col, "run_start_time"])
        df = df.rename(columns={"fips_code": "fips", col: "customers_out"})
        frames.append(df[["fips", "customers_out", "run_start_time"]])
    ev = pd.concat(frames, ignore_index=True)
    ev["t"] = pd.to_datetime(ev.run_start_time, format="%Y-%m-%d %H:%M:%S", utc=True)
    ev = ev.drop(columns="run_start_time")
    ev = ev.sort_values(["fips", "t"]).drop_duplicates(["fips", "t"], keep="last")
    n_na = int(ev.customers_out.isna().sum())
    ev["customers_out"] = ev.groupby("fips", sort=False).customers_out.ffill(limit=NA_FILL_ROWS).fillna(0.0)   # explicit "NA" snapshots: carry the last value up to 3 h
    ev["h"] = (ev.t - pd.Timestamp("1970-01-01", tz="UTC")).dt.total_seconds() / 3600.0
    years_all = sorted(ev.t.dt.year.unique())
    win0 = pd.Timestamp(f"{EAGLEI_WINDOW_START}-01-01", tz="UTC")
    win1 = pd.Timestamp(f"{EAGLEI_WINDOW_END + 1}-01-01", tz="UTC")
    n_years = EAGLEI_WINDOW_END - EAGLEI_WINDOW_START + 1
    named = {k: (pd.Timestamp(a, tz="UTC"), pd.Timestamp(b, tz="UTC")) for k, (a, b) in NAMED_EVENTS.items()}
    by_fips = {k: v for k, v in ev.groupby("fips")}
    rows = []
    for _, c in tc.iterrows():
        g = by_fips.get(int(c.fips))
        n = float(cust_n.get(c.fips, np.nan))
        row = dict(fips=c.fips, county=c.county, customers=n)
        if g is None or g.empty or not n == n:
            rows.append(row)
            continue
        w = g[(g.t >= win0) & (g.t < win1)]
        qd = float(np.quantile(w.customers_out.values, DEN_QUANTILE)) if len(w) else 0.0
        ratio = qd / n
        flag = "ok" if ratio <= DEN_OK_RATIO else ("inflated" if ratio <= DEN_ARTIFACT_RATIO else "artifact")
        row.update(q999_customers_out=qd, out_to_customers_ratio=ratio, denominator_flag=flag)
        n = max(n, qd)               # shares are measured against the larger of the two and clipped to it, so they never exceed 100%
        w = w.assign(customers_out=np.minimum(w.customers_out.values, n))
        g = g.assign(customers_out=np.minimum(g.customers_out.values, n))
        ts, cu = w.h.values, w.customers_out.values.astype(float)
        thr = max(EVENT_MIN_CUSTOMERS, EVENT_MIN_PCT / 100.0 * n)
        evt = _event_table(ts, cu, thr)
        ch = float(cu.sum() * EAGLEI_STEP_H)
        row.update(years_window=n_years, cust_hours_yr=ch / n_years, cust_hours_per_cust_yr=ch / n_years / n,
                   max_pct_out=100.0 * float(cu.max()) / n if len(cu) else np.nan, event_threshold_customers=thr,
                   n_events=len(evt), events_per_yr=len(evt) / n_years)
        if len(evt):
            row.update(p95_event_peak_customers=float(np.percentile(evt.peak, 95)),
                       p95_event_peak_pct=100.0 * float(np.percentile(evt.peak, 95)) / n,
                       median_event_hours=float(evt.duration_h.median()), p95_event_hours=float(np.percentile(evt.duration_h, 95)))
        av_full = av_low = usd_head = usd_dur = 0.0
        for e in evt.itertuples():
            a1, cov, f_, d_ = battery_avoided(e.peak, e.cust_hours, n, SOC_FULL)
            a2 = battery_avoided(e.peak, e.cust_hours, n, SOC_MARKET_RESERVE)[0]
            av_full += a1
            av_low += a2
            usd_head += a1 * VOLL_USD_MWH * VOLL_DURATION_DISCOUNT
            usd_dur += a1 * voll_for_hours(max(cov, 1e-9))
        row.update(eue_avoided_mwh_yr_full=av_full / n_years, eue_avoided_mwh_yr_lowsoc=av_low / n_years,
                   voll_usd_yr_headline=usd_head / n_years, voll_usd_yr_duration=usd_dur / n_years)
        for k, (a, b) in named.items():
            wk = g[(g.t >= a) & (g.t < b)]
            if wk.empty:
                row.update({f"{k}_peak_customers": 0.0, f"{k}_peak_pct": 0.0, f"{k}_cust_hours": 0.0, f"{k}_site_hours": 0.0,
                            f"{k}_eue_mwh_full": 0.0, f"{k}_eue_mwh_lowsoc": 0.0})
                continue
            pk = float(wk.customers_out.max())
            chk = float(wk.customers_out.sum() * EAGLEI_STEP_H)
            a1 = battery_avoided(pk, chk, n, SOC_FULL)
            a2 = battery_avoided(pk, chk, n, SOC_MARKET_RESERVE)
            row.update({f"{k}_peak_customers": pk, f"{k}_peak_pct": 100.0 * min(1.0, pk / n), f"{k}_cust_hours": chk,
                        f"{k}_site_hours": chk / max(pk, 1.0), f"{k}_eue_mwh_full": a1[0], f"{k}_eue_mwh_lowsoc": a2[0]})
        if flag == "artifact":       # share-based metrics are not trustworthy: keep the absolute ones, drop the rest
            for k in list(row):
                if k.endswith("_pct") or "eue" in k or k.startswith("voll_") or k in ("cust_hours_per_cust_yr", "max_pct_out"):
                    row[k] = np.nan
        rows.append(row)
    out = pd.DataFrame(rows)
    out["named_max_peak_pct"] = out[[f"{k}_peak_pct" for k in NAMED_EVENTS]].max(axis=1)
    valid = out.denominator_flag.isin(["ok", "inflated"])
    pr = {m: out.loc[valid, c].rank(pct=True) for m, c in (("hours", "cust_hours_per_cust_yr"), ("p95", "p95_event_peak_pct"), ("named", "named_max_peak_pct"))}
    out["outage_score"] = np.nan
    out.loc[valid, "outage_score"] = sum(OUTAGE_MIX[m] * pr[m] for m in OUTAGE_MIX)
    num = out.select_dtypes("number").columns
    out[num] = out[num].round(4)
    out.to_csv(REPO_DATA / "eaglei_county.csv", index=False)
    write_labels("eaglei_county", EAGLEI_LABELS)
    cov = dict(years_files=[int(y) for y in years_all], counties=int(out.cust_hours_yr.notna().sum()), of=len(out),
               denominator_flags=out.denominator_flag.value_counts().to_dict())
    cov["na_snapshots_filled_or_zeroed"] = n_na
    print(f"eaglei_county.csv: {len(out)} counties, {cov['counties']} with outage records; years {cov['years_files'][0]}-{cov['years_files'][-1]}", flush=True)
    return out


EAGLEI_LABELS = [
    ("fips", "REAL", "Census TIGER county GEOID", "48xxx"),
    ("county", "REAL", "Census TIGER county name, upper case", "join key to substations.county"),
    ("customers", "REAL", "EAGLE-I MCC.csv (customers tracked per county)", "ORNL's customer count; percentages use max(this, largest customers_out seen)"),
    ("q999_customers_out", "DERIVED", "EAGLE-I, window", "99.9th percentile of recorded customers_out"),
    ("out_to_customers_ratio", "DERIVED", "EAGLE-I / MCC", "above 1 means the reports exceed the county's customer count"),
    ("denominator_flag", "ASSUMPTION", "DEN_OK_RATIO 1.2, DEN_ARTIFACT_RATIO 1.5", "ok / inflated / artifact; artifact counties (a utility reported at system level or under another county) have their share, EUE and VoLL columns blank; elsewhere customers_out is clipped at max(customers, q999)"),
    ("outage_score", "ASSUMPTION", "OUTAGE_MIX over percentile ranks across counties with a valid denominator", "0-1: .5 hours per customer, .25 p95 event share, .25 worst named-event share; relative to other Texas counties, not an absolute scale"),
    ("years_window", "ASSUMPTION", "EAGLEI_WINDOW_START..END", "2018-2025: DQI shows 85-98% customer coverage from 2018; earlier years are patchy"),
    ("cust_hours_yr", "DERIVED", "EAGLE-I 15-minute customers_out x 0.25 h", "customer-hours out per year, window mean; reported outages only"),
    ("cust_hours_per_cust_yr", "DERIVED", "same / customers", "hours per customer per year; NOT IEEE SAIDI (no cause screening)"),
    ("max_pct_out", "DERIVED", "EAGLE-I", "largest share of county customers out at one snapshot, window"),
    ("event_threshold_customers", "ASSUMPTION", "EVENT_MIN_PCT, EVENT_MIN_CUSTOMERS", "customers out that start an event: max(25, 1% of county)"),
    ("n_events", "DERIVED", "eaglei segmentation", "events in the window (runs above threshold; gaps under 2 h merged)"),
    ("events_per_yr", "DERIVED", "eaglei segmentation", ""),
    ("p95_event_peak_customers", "DERIVED", "eaglei segmentation", "95th percentile of event peak customers out"),
    ("p95_event_peak_pct", "DERIVED", "same / customers", "percent of county customers"),
    ("median_event_hours", "DERIVED", "eaglei segmentation", ""),
    ("p95_event_hours", "DERIVED", "eaglei segmentation", ""),
    ("eue_avoided_mwh_yr_full", "DERIVED", "battery_avoided(): f x min(P*d, E)", "MWh a year a 1 MW / 4 MWh battery at a random county site avoids; starts full (SOC_FULL); load 1 MW; events only"),
    ("eue_avoided_mwh_yr_lowsoc", "DERIVED", "same with SOC_MARKET_RESERVE = 20%", "market-dispatched fleet that keeps only its 20% reserve"),
    ("voll_usd_yr_headline", "DERIVED", "eue_full x VOLL_USD_MWH ($35,685, Brattle Aug 2024)", "one-hour VoLL on every MWh: an upper bound"),
    ("voll_usd_yr_duration", "DERIVED", "eue_full x Brattle duration table (1 h $35,685, 2 h $21,326, 4 h $13,340)", "duration-adjusted"),
] + [(f"{k}_{m}", lab, "EAGLE-I, named window", note)
     for k in NAMED_EVENTS for m, lab, note in [("peak_customers", "DERIVED", "max customers out in the window"), ("peak_pct", "DERIVED", "% of county customers"),
                                                ("cust_hours", "DERIVED", "customer-hours in the window"),
                                                ("site_hours", "DERIVED", "customer-hours / peak = mean outage hours per affected customer (approximation)"),
                                                ("eue_mwh_full", "DERIVED", "MWh avoided in this event, full battery"),
                                                ("eue_mwh_lowsoc", "DERIVED", "MWh avoided, 20% SOC")]] + [
    ("named_max_peak_pct", "DERIVED", "max over Uri, derecho, Beryl", "worst named-event share of county customers out"),
]


# ====================================================================================================== Oncor SAIFI / SAIDI
def build_oncor():
    import openpyxl
    xl = next((RAW_REL / "oncor" / "extracted").glob("*.xlsx"))
    wb = openpyxl.load_workbook(xl, read_only=True, data_only=True)

    def sheet(name, val):
        rows = list(wb[name].iter_rows(values_only=True))
        h = next(i for i, r in enumerate(rows) if r and r[2] == "Substation Identification")
        return pd.DataFrame([r[2:6] for r in rows[h + 1:] if r[2] is not None], columns=["sub", "feeder", "customers", val])

    a = sheet("Feeder-SAIFI", "saifi")
    b = sheet("Feeder-SAIDI", "saidi")
    f = a.merge(b[["sub", "feeder", "saidi"]], on=["sub", "feeder"], how="outer")
    f["sub"] = f["sub"].astype(str).str.upper().str.strip()
    f["customers"] = pd.to_numeric(f.customers, errors="coerce")
    f["cs"] = f.customers * f.saifi
    f["cd"] = f.customers * f.saidi
    p90 = f.saidi.quantile(0.9)
    f["hi"] = f.saidi > p90
    g = f.groupby("sub").agg(oncor_feeders=("feeder", "nunique"), oncor_customers=("customers", "sum"), cs=("cs", "sum"), cd=("cd", "sum"),
                             oncor_saidi_max=("saidi", "max"), oncor_feeders_saidi_gt_p90=("hi", "sum"))
    g["oncor_saifi"] = g.cs / g.oncor_customers
    g["oncor_saidi_min"] = g.cd / g.oncor_customers
    g = g.drop(columns=["cs", "cd"]).reset_index()
    sys_row = dict(feeders=int(len(f)), customers=int(f.customers.sum()), saidi_p50=float(f.saidi.median()), saidi_p90=float(p90),
                   saifi_p50=float(f.saifi.median()), saifi_p90=float(f.saifi.quantile(0.9)))
    return g, f, sys_row


# ====================================================================================================== critical facilities
FAC_DEDUPE_M = 150.0   # ASSUMPTION: same-kind OSM objects closer than this are one facility (node + polygon + relation of one site)


def _load_facilities():
    pts = []
    for grp in CRITICAL_QUERIES:
        j = json.loads((RAW_REL / "osm" / f"{grp}.json").read_text(encoding="utf-8"))
        for e in j["elements"]:
            lat = e.get("lat") or (e.get("center") or {}).get("lat")
            lon = e.get("lon") or (e.get("center") or {}).get("lon")
            if lat is None:
                continue
            tags = e.get("tags", {})
            kind = {"hospitals": "hospital", "water": "water", "emergency": "emergency"}[grp]
            pts.append((kind, tags.get("name", ""), float(lat), float(lon), tags.get("amenity") or tags.get("man_made") or tags.get("emergency") or ""))
    return pd.DataFrame(pts, columns=["kind", "name", "lat", "lon", "subtype"])


def _dedupe(df, radius_m):
    """Hospitals and works are mapped as nodes, ways and relations at once: merge same-kind points closer than radius_m."""
    from scipy.spatial import cKDTree
    keep = np.ones(len(df), bool)
    for kind in df.kind.unique():
        idx = np.flatnonzero(df.kind.values == kind)
        t = cKDTree(unit_xyz(df.lat.values[idx], df.lon.values[idx]))
        pairs = t.query_pairs(chord_km(radius_m / 1000.0))
        parent = list(range(len(idx)))

        def find(x):
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x
        for a, b in pairs:
            ra, rb = find(a), find(b)
            if ra != rb:
                parent[rb] = ra
        seen = set()
        for i in range(len(idx)):
            r = find(i)
            if r in seen:
                keep[idx[i]] = False
            seen.add(r)
    return df[keep].reset_index(drop=True)


def build_critical(subs):
    from scipy.spatial import cKDTree
    fac = _dedupe(_load_facilities(), FAC_DEDUPE_M)
    s = subs[subs.lat.notna()].copy()
    pts = unit_xyz(s.lat.values, s.lon.values)
    out = pd.DataFrame({"sub": s["sub"].values})
    rad = {"hospital": RADIUS_HOSPITAL_KM, "water": RADIUS_WATER_KM, "emergency": RADIUS_EMERGENCY_KM}
    weighted = np.zeros(len(s))
    for kind, r in rad.items():
        f = fac[fac.kind == kind]
        t = cKDTree(unit_xyz(f.lat.values, f.lon.values))
        cnt = np.array([len(x) for x in t.query_ball_point(pts, chord_km(r))])
        dist, _ = t.query(pts)
        out[f"n_{kind}_{int(r)}km"] = cnt
        out[f"nearest_{kind}_km"] = np.round(dist / chord_km(1.0), 2)
        weighted += FACILITY_WEIGHTS[kind] * cnt
    out["facility_weighted_count"] = weighted
    out["facility_score"] = pd.Series(weighted).rank(pct=True).values.round(4)       # percentile among placed subs: no saturation guess
    out = s[["sub", "county", "zone", "lat", "lon", "match_conf"]].merge(out, on="sub")
    out.to_csv(REPO_DATA / "critical_facilities.csv", index=False)
    write_labels("critical_facilities", CRIT_LABELS)
    print(f"critical_facilities.csv: {len(out)} subs; facilities after dedupe: {fac.kind.value_counts().to_dict()}", flush=True)
    return out, fac


CRIT_LABELS = [
    ("sub", "REAL", "ERCOT NP4-160", ""), ("county", "DERIVED", "data/substations.csv", ""), ("zone", "REAL", "ERCOT NP4-160", ""),
    ("lat", "DERIVED", "data/substations.csv", ""), ("lon", "DERIVED", "data/substations.csv", ""),
    ("match_conf", "DERIVED", "data/substations.csv", "counts are only as good as the coordinate"),
    ("n_hospital_10km", "DERIVED", "OSM amenity=hospital (Overpass, 10 Oct 2026), RADIUS_HOSPITAL_KM", "OSM is volunteer-mapped: undercounts rural clinics; same-site nodes and polygons merged within 150 m"),
    ("nearest_hospital_km", "DERIVED", "OSM", "straight-line"),
    ("n_water_10km", "DERIVED", "OSM man_made=water_works|wastewater_plant, RADIUS_WATER_KM", "partial coverage in rural Texas"),
    ("nearest_water_km", "DERIVED", "OSM", ""),
    ("n_emergency_5km", "DERIVED", "OSM amenity=fire_station|police, emergency=ambulance_station, RADIUS_EMERGENCY_KM", ""),
    ("nearest_emergency_km", "DERIVED", "OSM", ""),
    ("facility_weighted_count", "ASSUMPTION", "FACILITY_WEIGHTS (hospital 5, water 3, emergency 1)", "weights are a judgement"),
    ("facility_score", "ASSUMPTION", "percentile rank of facility_weighted_count among placed subs", "0-1, relative; dense cities score near 1, so read it with the other components"),
]


# ====================================================================================================== network weakness (radial pockets)
POCKET_MIX = {"thin": 0.5, "size": 0.5}   # ASSUMPTION: pocket sub-score = half "few independent circuits to the backbone", half "size of the pocket behind a bridge"
BRIDGE_DOUBLE_CIRCUIT_FACTOR = 0.5         # ASSUMPTION: a bridge that carries 2+ circuits survives the loss of one line (N-1), so its size part counts half
BRIDGE_UNCONFIRMED_FACTOR = 0.5            # ASSUMPTION: a bridge that disappears when HIFLD-only lines are added may be an OSM mapping gap, so its size part counts half
INF_CAP = 1000                             # DERIVED: sink-arc capacity for the max-flow; any value above the degree of a node


def _collapse_edges(df):
    """One edge per unordered substation pair: max kV, summed circuits (cap 6), shortest length."""
    d = df.copy()
    a = d[["from_osm_sub", "to_osm_sub"]].apply(lambda r: tuple(sorted(r)), axis=1)
    d["u"] = [x[0] for x in a]
    d["v"] = [x[1] for x in a]
    d = d[d.u != d.v]
    g = d.groupby(["u", "v"]).agg(kv=("voltage_kv", "max"), circ=("n_circuits", "sum"), km=("length_km", "min")).reset_index()
    g["circ"] = g.circ.clip(upper=6)
    return g


def graph_inputs(subs):
    lines = pd.read_csv(REPO_DATA / "lines.csv", low_memory=False)
    osm_edges = lines[(lines.source == "osm") & (lines.voltage_kv >= GRAPH_MIN_KV)]
    hifld = lines[(lines.source == "hifld") & lines.from_osm_sub.notna() & lines.to_osm_sub.notna()
                  & ~lines.in_osm_pair.astype(str).eq("True") & (lines.voltage_kv >= GRAPH_MIN_KV)]
    nodes = pd.read_csv(REPO_DATA / "osm_substations.csv")
    ercot_counties = set(subs.loc[subs.match_conf >= MIN_CONF, "county"].dropna())
    inside = nodes.set_index("osm_id").county.fillna("").isin(ercot_counties)
    linked = set(subs.osm_id.dropna())
    keep = set(inside[inside].index) | linked
    e1 = _collapse_edges(osm_edges)
    e1 = e1[e1.u.isin(keep) & e1.v.isin(keep)]
    e2 = _collapse_edges(hifld)
    e2 = e2[e2.u.isin(keep) & e2.v.isin(keep)]
    return e1, e2, nodes.set_index("osm_id"), keep


def _bridge_decomposition(edges, sink_kv, node_weight):
    """Bridge tree of the graph: returns per-node dict of structure measures."""
    import networkx as nx
    G = nx.Graph()
    for r in edges.itertuples():
        G.add_edge(r.u, r.v, kv=r.kv, circ=r.circ)
    br = list(nx.bridges(G))
    H = G.copy()
    H.remove_edges_from(br)
    block = {}
    size, wsum, bb = {}, {}, {}
    backbone = {n for n in G if any(d["kv"] >= sink_kv for d in G[n].values())}
    for i, comp in enumerate(nx.connected_components(H)):
        for n in comp:
            block[n] = i
        size[i] = len(comp)
        wsum[i] = sum(node_weight.get(n, 0) for n in comp)
        bb[i] = len(comp & backbone)
    T = nx.Graph()
    T.add_nodes_from(size)
    for u, v in br:
        T.add_edge(block[u], block[v], circ=G[u][v]["circ"], kv=G[u][v]["kv"])
    core = max(size, key=lambda b: (bb[b], size[b]))
    parent, depth, order, root_of = {}, {}, [], {}
    for comp in nx.connected_components(T):
        root = core if core in comp else max(comp, key=lambda b: size[b])
        parent[root], depth[root] = None, 0
        stack = [root]
        while stack:
            b = stack.pop()
            order.append(b)
            root_of[b] = root
            for c in T[b]:
                if c != parent[b]:
                    parent[c], depth[c] = b, depth[b] + 1
                    stack.append(c)
    sub_n, sub_w = dict(size), dict(wsum)
    for b in reversed(order):
        if parent[b] is not None:
            sub_n[parent[b]] += sub_n[b]
            sub_w[parent[b]] += sub_w[b]
    res = {}
    for n in G:
        b = block[n]
        d = depth[b]
        outer = b
        while depth[outer] > 1:
            outer = parent[outer]
        island = root_of[b] != core
        res[n] = dict(in_core=(b == core), island=island, n_bridges_to_core=d if not island else np.nan,
                      behind_bridge=(d > 0) or island,
                      bridge_circuits=T[b][parent[b]]["circ"] if d > 0 else np.nan, bridge_kv=T[b][parent[b]]["kv"] if d > 0 else np.nan,
                      pocket_first_nodes=sub_n[b] if d > 0 else (sub_n[root_of[b]] if island else 0),
                      pocket_outer_nodes=sub_n[outer] if d > 0 else (sub_n[root_of[b]] if island else 0),
                      pocket_outer_subs=sub_w[outer] if d > 0 else (sub_w[root_of[b]] if island else 0))
    return G, res, backbone


def _flow_paths(G, sinkset, cap=MAX_PATHS_COUNTED, circuits=False):
    """Edge-disjoint paths from every node to a super-sink wired to `sinkset` (the node's own sink arc removed).
    circuits=False: one unit per line corridor (independent routes). circuits=True: a corridor carries min(circuits, cap) units."""
    from scipy.sparse import csr_matrix
    from scipy.sparse.csgraph import maximum_flow
    nodes = list(G.nodes)
    ix = {n: i for i, n in enumerate(nodes)}
    N = len(nodes)
    rows, cols = [], []
    cap_arr = []
    for u, v in G.edges:
        rows += [ix[u], ix[v]]
        cols += [ix[v], ix[u]]
        c_ = int(min(G[u][v]["circ"], cap)) if circuits else 1
        cap_arr += [c_, c_]
    sink_pos = {}
    for n in sinkset:
        sink_pos[n] = len(rows)
        rows.append(ix[n])
        cols.append(N)
        cap_arr.append(INF_CAP)
    A = csr_matrix((np.array(cap_arr, dtype=np.int32), (rows, cols)), shape=(N + 1, N + 1))
    out = {}
    for n in nodes:
        B = A
        if n in sinkset:
            B = A.copy()
            r0, r1 = B.indptr[ix[n]], B.indptr[ix[n] + 1]
            j = np.flatnonzero(B.indices[r0:r1] == N)
            B.data[r0 + j] = 0
        out[n] = min(cap, int(maximum_flow(B, ix[n], N).flow_value))
    return out


def graph_metrics(subs):
    """Per OSM node: connectivity to the >= 345 kV backbone and to the >= 138 kV network, bridge position and pocket size."""
    e1, e2, nodes, keep = graph_inputs(subs)
    weight = subs[subs.match_conf >= MIN_CONF].dropna(subset=["osm_id"]).groupby("osm_id").size().to_dict()
    G, res, backbone = _bridge_decomposition(e1, BACKBONE_KV, weight)
    G2, res2, _ = _bridge_decomposition(pd.concat([e1, e2], ignore_index=True).drop_duplicates(["u", "v"]), BACKBONE_KV, weight)
    s138 = {n for n in G if any(d["kv"] >= 138 for d in G[n].values())}
    p345 = _flow_paths(G, backbone)
    p345c = _flow_paths(G, backbone, circuits=True)
    p138 = _flow_paths(G, s138)
    rows = []
    for n in G:
        r = dict(osm_node=n, node_kv=max(d["kv"] for d in G[n].values()), node_degree=G.degree(n), paths_345=p345[n], paths_345_circuits=p345c[n], paths_138=p138[n],
                 behind_bridge_with_hifld=res2.get(n, {}).get("behind_bridge", np.nan))
        r.update(res[n])
        rows.append(r)
    df = pd.DataFrame(rows).set_index("osm_node")
    info = dict(nodes=G.number_of_nodes(), edges=G.number_of_edges(), hifld_extra_edges=len(e2), backbone=len(backbone),
                bridges=int(df.behind_bridge.sum()), in_core=int(df.in_core.sum()), islands=int(df.island.sum()))
    return df, info, nodes


# ====================================================================================================== large-load growth
def large_load_by_county():
    ll = pd.read_csv(REPO_DATA / "large_loads.csv")
    d = {}
    for r in ll.itertuples():
        if r.area_type == "county":
            names = [str(r.area).split(" County")[0].strip().upper()]
        elif r.area_type == "county_group":
            head = re.split(r" counties", str(r.area))[0]
            names = [x.strip().upper() for x in re.split(r",| and ", head) if x.strip()]
        else:
            continue
        for n in names:
            d[n] = max(d.get(n, 0.0), float(r.mw) / (len(names) if r.area_type == "county_group" else 1))
    return d


# ====================================================================================================== score
def _clip01(x):
    return np.clip(x, 0.0, 1.0)


def combine_scores(r, baseline=None):
    """Weighted mean of the five sub-scores that are present (weights renormalised), 0-100. Returns (score, n_components, main_driver).
    Needs columns outage_score, pocket_score, facility_score, oncor_score, large_load_score (NaN = not available).
    main_driver = the component whose weighted contribution most exceeds the typical (mean) contribution in `baseline`
    (a Series from a previous call's attribute .baseline), i.e. what sets this sub apart, not just what carries the largest weight."""
    comp = {"outage": "outage_score", "pocket": "pocket_score", "facilities": "facility_score", "oncor": "oncor_score", "large_load": "large_load_score"}
    W = pd.DataFrame({k: np.where(r[c].notna(), SCORE_WEIGHTS[k], 0.0) for k, c in comp.items()}, index=r.index)
    S = pd.DataFrame({k: r[c].fillna(0.0) for k, c in comp.items()}, index=r.index)
    wsum = W.sum(axis=1)
    contrib = (W * S).div(wsum.where(wsum > 0), axis=0)
    score = np.where(wsum > 0, 100.0 * contrib.sum(axis=1), np.nan)
    base = contrib[(W > 0).sum(axis=1) >= 3].mean() if baseline is None else baseline
    driver = np.where(wsum > 0, (contrib - base).fillna(-9.0).idxmax(axis=1), "")
    combine_scores.baseline = base
    return score, (W > 0).sum(axis=1), driver


SENSITIVITY_WEIGHTS = {            # ASSUMPTION: alternative weightings used only to show how stable the top-20 is
    "equal": {"outage": 0.2, "pocket": 0.2, "facilities": 0.2, "oncor": 0.2, "large_load": 0.2},
    "no_large_load": {"outage": 0.35, "pocket": 0.30, "facilities": 0.25, "oncor": 0.10, "large_load": 0.0},
    "outage_only": {"outage": 1.0, "pocket": 0.0, "facilities": 0.0, "oncor": 0.0, "large_load": 0.0},
    "network_only": {"outage": 0.0, "pocket": 1.0, "facilities": 0.0, "oncor": 0.0, "large_load": 0.0},
}


def alt_scores(r, weights):
    comp = {"outage": "outage_score", "pocket": "pocket_score", "facilities": "facility_score", "oncor": "oncor_score", "large_load": "large_load_score"}
    W = pd.DataFrame({k: np.where(r[c].notna(), weights[k], 0.0) for k, c in comp.items()}, index=r.index)
    S = pd.DataFrame({k: r[c].fillna(0.0) for k, c in comp.items()}, index=r.index)
    ws = W.sum(axis=1)
    return pd.Series(np.where(ws > 0, 100.0 * (W * S).sum(axis=1) / ws.where(ws > 0), np.nan), index=r.index)


def why_text(x):
    """One deterministic sentence from the numbers: what puts this substation high."""
    parts = [f"{x.county.title()} outage percentile {x.outage_score:.2f} (worst named event took out {x.named_max_peak_pct:.0f}% of county customers, {x.cust_hours_per_cust_yr:.1f} customer-hours per customer-year)"]
    if str(x.behind_bridge) == "True":
        conf = "" if str(x.behind_bridge_with_hifld) == "True" else ", bridge not confirmed by HIFLD"
        parts.append(f"behind {int(x.n_bridges_to_core) if x.n_bridges_to_core == x.n_bridges_to_core else '?'} bridge(s) with {int(x.pocket_outer_nodes)} substations in the pocket, {int(x.bridge_circuits)} circuit(s){conf}")
    else:
        parts.append(f"{int(x.paths_345_circuits)} circuit-path(s) to the 345 kV backbone")
    parts.append(f"{int(x.n_hospital_10km)} hospitals, {int(x.n_water_10km)} water works within 10 km, {int(x.n_emergency_5km)} emergency stations within 5 km")
    if x.oncor_saidi_min == x.oncor_saidi_min:
        parts.append(f"Oncor feeders SAIDI {x.oncor_saidi_min:.0f} min")
    if x.large_load_mw_county and x.large_load_mw_county == x.large_load_mw_county and x.large_load_mw_county > 0:
        parts.append(f"{x.large_load_mw_county:,.0f} MW of Batch Zero large load in the county")
    return "; ".join(parts)


def build_reliability(fresh=False):
    from scipy.spatial import cKDTree
    subs = load_subs()
    if fresh or not (REPO_DATA / "eaglei_county.csv").exists():
        eag = build_eaglei()
    else:
        eag = pd.read_csv(REPO_DATA / "eaglei_county.csv", dtype={"fips": str})
    crit, _ = build_critical(subs)
    onc, _, onc_sys = build_oncor()
    gm, ginfo, onodes = graph_metrics(subs)
    print("graph:", ginfo, flush=True)

    r = subs[["sub", "county", "zone", "kv_max", "lat", "lon", "match_conf", "osm_id", "has_resource_node", "noie_zone"]].copy()
    r["site_level"] = (r.match_conf >= MIN_CONF) & r.lat.notna()

    # --- outage history (county)
    ecols = ["county", "customers", "outage_score", "denominator_flag", "cust_hours_per_cust_yr", "events_per_yr", "p95_event_peak_pct", "p95_event_peak_customers",
             "uri_peak_pct", "derecho_peak_pct", "beryl_peak_pct", "named_max_peak_pct", "eue_avoided_mwh_yr_full", "eue_avoided_mwh_yr_lowsoc",
             "voll_usd_yr_headline", "voll_usd_yr_duration", "uri_eue_mwh_full", "derecho_eue_mwh_full", "beryl_eue_mwh_full"]
    r["county_u"] = r.county.str.upper()
    r = r.merge(eag[ecols].rename(columns={"county": "county_u"}), on="county_u", how="left")

    # --- radial pocket: linked OSM node, else nearest node within NODE_JOIN_KM
    r["pocket_basis"] = np.where(r.osm_id.isin(gm.index), "linked_node", "none")
    node_ids = np.array(gm.index)
    xy = unit_xyz(onodes.loc[node_ids, "lat"].values, onodes.loc[node_ids, "lon"].values)
    tree = cKDTree(xy)
    need = r.index[(r.pocket_basis == "none") & r.lat.notna()]
    d, i = tree.query(unit_xyz(r.loc[need, "lat"].values, r.loc[need, "lon"].values))
    ok = d / chord_km(1.0) <= NODE_JOIN_KM
    r["osm_node"] = np.where(r.pocket_basis == "linked_node", r.osm_id, None)
    r.loc[need[ok], "osm_node"] = node_ids[i[ok]]
    r.loc[need[ok], "pocket_basis"] = "nearest_node"
    m = gm.reset_index().rename(columns={"osm_node": "osm_node"})
    r = r.merge(m, on="osm_node", how="left")
    r["behind_bridge"] = r.behind_bridge.where(r.osm_node.notna())
    thin = _clip01(1.0 - (np.minimum(r.paths_345_circuits, MAX_PATHS_COUNTED) - 1) / (MAX_PATHS_COUNTED - 1))
    bb = r.behind_bridge.fillna(False).astype(bool)
    factor = (np.where(r.bridge_circuits.fillna(1) >= 2, BRIDGE_DOUBLE_CIRCUIT_FACTOR, 1.0)
              * np.where(r.behind_bridge_with_hifld.fillna(False).astype(bool), 1.0, BRIDGE_UNCONFIRMED_FACTOR))
    size = np.where(bb, _clip01(r.pocket_outer_nodes / POCKET_SATURATION) * factor, 0.0)
    r["pocket_score"] = np.where(r.osm_node.notna() & ~r.island.fillna(False).astype(bool), POCKET_MIX["thin"] * thin + POCKET_MIX["size"] * size, np.nan)
    # an "island" is a piece of the OSM graph with no route to the core: that is a mapping or footprint gap, not evidence of a pocket, so it gets no pocket score

    # --- critical facilities
    r = r.merge(crit[["sub", "n_hospital_10km", "n_water_10km", "n_emergency_5km", "nearest_hospital_km", "facility_weighted_count", "facility_score"]],
                on="sub", how="left")

    # --- Oncor feeders (exact substation code)
    onc["sub"] = onc["sub"].astype(str)
    r["_key"] = r["sub"].str.upper()
    r = r.merge(onc.rename(columns={"sub": "_key"}), on="_key", how="left").drop(columns="_key")
    r["oncor_score"] = 0.5 * _clip01(r.oncor_saidi_min / SAIDI_SATURATION_MIN) + 0.5 * _clip01(r.oncor_saifi / SAIFI_SATURATION)

    # --- large-load growth near the substation
    ll = large_load_by_county()
    cent = r[r.site_level].groupby("county_u")[["lat", "lon"]].mean()
    r["large_load_mw_county"] = r.county_u.map(ll)
    r["large_load_mw_county"] = r.large_load_mw_county.fillna(0.0).where(r.county_u.notna())
    near = np.full(len(r), np.nan)
    for j in r.index[r.lat.notna()]:
        t = 0.0
        for cn, mw in ll.items():
            if cn == r.at[j, "county_u"] or cn not in cent.index:
                continue
            if haversine_km(r.at[j, "lat"], r.at[j, "lon"], cent.at[cn, "lat"], cent.at[cn, "lon"]) <= LARGE_LOAD_RADIUS_KM:
                t += mw
        near[j] = t
    r["large_load_mw_50km"] = near
    r["large_load_score"] = _clip01((r.large_load_mw_county + LARGE_LOAD_NEARBY_WEIGHT * r.large_load_mw_50km) / LARGE_LOAD_SATURATION_MW)

    # --- combined score
    r["reliability_need_score"], r["n_components"], r["main_driver"] = combine_scores(r)
    r["rank_eligible"] = r.site_level & r.outage_score.notna() & r.pocket_score.notna() & r.facility_score.notna() & (r.kv_max >= 69)
    r["need_rank"] = r.loc[r.rank_eligible, "reliability_need_score"].rank(ascending=False, method="first")
    r["voll_usd_per_mwh"] = VOLL_USD_MWH

    cols = ["sub", "county", "zone", "kv_max", "lat", "lon", "match_conf", "site_level", "has_resource_node",
            "reliability_need_score", "need_rank", "rank_eligible", "main_driver", "n_components",
            "outage_score", "denominator_flag", "pocket_score", "facility_score", "oncor_score", "large_load_score",
            "cust_hours_per_cust_yr", "events_per_yr", "p95_event_peak_pct", "uri_peak_pct", "derecho_peak_pct", "beryl_peak_pct", "named_max_peak_pct",
            "pocket_basis", "osm_node", "node_kv", "node_degree", "paths_345", "paths_345_circuits", "paths_138", "behind_bridge", "behind_bridge_with_hifld", "island",
            "n_bridges_to_core", "bridge_circuits", "bridge_kv", "pocket_first_nodes", "pocket_outer_nodes", "pocket_outer_subs",
            "n_hospital_10km", "n_water_10km", "n_emergency_5km", "nearest_hospital_km", "facility_weighted_count",
            "oncor_feeders", "oncor_customers", "oncor_saifi", "oncor_saidi_min", "oncor_saidi_max",
            "large_load_mw_county", "large_load_mw_50km",
            "eue_avoided_mwh_yr_full", "eue_avoided_mwh_yr_lowsoc", "voll_usd_per_mwh", "voll_usd_yr_headline", "voll_usd_yr_duration",
            "uri_eue_mwh_full", "derecho_eue_mwh_full", "beryl_eue_mwh_full"]
    out = r[cols].copy()
    num = out.select_dtypes("number").columns
    out[num] = out[num].round(4)
    out.to_csv(REPO_DATA / "reliability.csv", index=False)
    write_labels("reliability", REL_LABELS)
    summary = dict(rows=len(out), site_level=int(out.site_level.sum()), rank_eligible=int(out.rank_eligible.sum()),
                   oncor_joined=int(out.oncor_feeders.notna().sum()), oncor_system=onc_sys, graph=ginfo,
                   pocket_basis=out.pocket_basis.value_counts().to_dict())
    (RAW_REL / "reliability_build_summary.json").write_text(json.dumps(summary, indent=1, default=str), encoding="utf-8")
    print(json.dumps(summary, indent=1, default=str), flush=True)
    top = out[out.rank_eligible].sort_values("need_rank").head(NEED_TOP_N).copy()
    top["why"] = top.apply(why_text, axis=1)
    elig = out[out.rank_eligible].copy()
    base_top = set(elig.nsmallest(NEED_TOP_N, "need_rank")["sub"])
    sens = {}
    for name, w in SENSITIVITY_WEIGHTS.items():
        sc = alt_scores(elig, w)
        alt_top = set(elig.loc[sc.nlargest(NEED_TOP_N).index, "sub"])
        sens[name] = dict(top20_overlap=len(base_top & alt_top), spearman=round(float(elig.reliability_need_score.corr(sc, method="spearman")), 3))
    summary["sensitivity"] = sens
    summary["top_counties"] = top.county.value_counts().to_dict()
    (RAW_REL / "reliability_build_summary.json").write_text(json.dumps(summary, indent=1, default=str), encoding="utf-8")
    top[["need_rank", "sub", "county", "zone", "kv_max", "reliability_need_score", "main_driver", "why"]].to_csv(RAW_REL / "top_need.csv", index=False)
    print(top[["need_rank", "sub", "county", "zone", "kv_max", "reliability_need_score", "main_driver", "outage_score", "pocket_score",
               "facility_score", "oncor_score", "large_load_score"]].to_string(index=False), flush=True)
    print("sensitivity (top-20 overlap with the base weights, Spearman):", sens, flush=True)
    return out


REL_LABELS = [
    ("sub", "REAL", "ERCOT NP4-160 SUBSTATION", "join key"),
    ("county", "DERIVED", "data/substations.csv", ""), ("zone", "REAL", "ERCOT NP4-160", ""), ("kv_max", "REAL", "ERCOT NP4-160", ""),
    ("lat", "DERIVED", "data/substations.csv", ""), ("lon", "DERIVED", "data/substations.csv", ""), ("match_conf", "DERIVED", "data/substations.csv", ""),
    ("site_level", "DERIVED", "reliability.py", "match_conf >= 0.5 and a coordinate; other rows lean on county data"),
    ("has_resource_node", "REAL", "ERCOT NP4-160", ""),
    ("reliability_need_score", "ASSUMPTION", "SCORE_WEIGHTS (outage .30, pocket .25, facilities .20, Oncor .10, large load .15)", "0-100, weights renormalised over the components present; the weights are a judgement, the sub-scores are in their own columns"),
    ("need_rank", "DERIVED", "reliability.py", "1 = highest need among rank_eligible subs"),
    ("rank_eligible", "DERIVED", "reliability.py", "site-level, 69 kV or more, with outage, pocket and facility components"),
    ("main_driver", "DERIVED", "reliability.py", "component with the largest weighted contribution"),
    ("n_components", "DERIVED", "reliability.py", "of 5"),
    ("outage_score", "ASSUMPTION", "eaglei_county.csv", "0-1, percentile-based: .5 hours per customer, .25 p95 event share, .25 worst named-event share; blank in artifact counties"),
    ("denominator_flag", "ASSUMPTION", "eaglei_county.csv", "ok / inflated / artifact (EAGLE-I reports exceed the county's customers)"),
    ("pocket_score", "ASSUMPTION", "OSM line graph; POCKET_MIX, BRIDGE_*_FACTOR, POCKET_SATURATION", "0-1: half few circuits to the 345 kV backbone, half size of the pocket behind a bridge (halved for a double-circuit bridge, halved again if HIFLD adds a line that removes the bridge)"),
    ("facility_score", "ASSUMPTION", "OSM; FACILITY_WEIGHTS", "0-1"),
    ("oncor_score", "ASSUMPTION", "Oncor 2023 SQR; SAIDI/SAIFI saturation constants", "0-1; Oncor substations only (exact code join)"),
    ("large_load_score", "ASSUMPTION", "ERCOT Batch Zero deck county MW (data/large_loads.csv); LARGE_LOAD_* constants", "0-1; only the top-5 counties and county groups are public, so most counties read 0 (not zero load)"),
    ("cust_hours_per_cust_yr", "DERIVED", "eaglei_county.csv", "county value"), ("events_per_yr", "DERIVED", "eaglei_county.csv", "county value"),
    ("p95_event_peak_pct", "DERIVED", "eaglei_county.csv", "county value"),
    ("uri_peak_pct", "DERIVED", "eaglei_county.csv", "% of county customers out at the peak, 13-23 Feb 2021"),
    ("derecho_peak_pct", "DERIVED", "eaglei_county.csv", "16-23 May 2024"), ("beryl_peak_pct", "DERIVED", "eaglei_county.csv", "8-22 Jul 2024"),
    ("named_max_peak_pct", "DERIVED", "eaglei_county.csv", ""),
    ("pocket_basis", "DERIVED", "reliability.py", "linked_node = the sub's OSM node from phase 1c; nearest_node = nearest line-graph node within 3 km; none = no graph position"),
    ("osm_node", "DERIVED", "data/substations.csv / osm_substations.csv", ""),
    ("node_kv", "DERIVED", "data/lines.csv (OSM edges)", "highest line voltage at the node"), ("node_degree", "DERIVED", "lines.csv", "distinct neighbouring substations"),
    ("paths_345", "DERIVED", "networkx/scipy max-flow on OSM edges >= 69 kV in ERCOT counties", "edge-disjoint corridors to the >= 345 kV backbone, capped at 3 (own sink arc removed for backbone nodes)"),
    ("paths_345_circuits", "DERIVED", "same, each corridor counted as min(circuits, 3)", "circuit-level redundancy: 1 = one circuit all the way (loses the sub on any single line outage), 2 = survives N-1, 3 = survives N-1-1; used by pocket_score"),
    ("paths_138", "DERIVED", "same", "edge-disjoint corridors to the >= 138 kV network"),
    ("behind_bridge", "DERIVED", "networkx bridges", "True if one line corridor cut separates the sub from the core (largest 2-edge-connected block holding the backbone)"),
    ("behind_bridge_with_hifld", "DERIVED", "same plus HIFLD-only edges", "False means the bridge disappears when HIFLD adds a line OSM lacks: treat the OSM bridge as a possible mapping gap"),
    ("island", "DERIVED", "networkx", "the sub's piece of the graph does not connect to the core at all: treated as a mapping gap, pocket_score left blank"),
    ("n_bridges_to_core", "DERIVED", "bridge tree", "bridges between the sub and the core"),
    ("bridge_circuits", "DERIVED", "lines.csv n_circuits", "circuits on the first bridge toward the core (2 = double circuit: survives N-1, not a tower loss)"),
    ("bridge_kv", "DERIVED", "lines.csv", ""),
    ("pocket_first_nodes", "DERIVED", "bridge tree", "substations cut off if the first bridge toward the core fails"),
    ("pocket_outer_nodes", "DERIVED", "bridge tree", "substations cut off if the outermost bridge (nearest the core) fails"),
    ("pocket_outer_subs", "DERIVED", "bridge tree", "ERCOT subs at match_conf >= 0.5 inside that outer pocket"),
    ("n_hospital_10km", "DERIVED", "critical_facilities.csv", ""), ("n_water_10km", "DERIVED", "critical_facilities.csv", ""), ("n_emergency_5km", "DERIVED", "critical_facilities.csv", ""),
    ("nearest_hospital_km", "DERIVED", "critical_facilities.csv", ""), ("facility_weighted_count", "ASSUMPTION", "critical_facilities.csv", ""),
    ("oncor_feeders", "REAL", "Oncor 2023 PUCT Service Quality Report (Project 56005 item 8)", "feeders at the substation with 10 or more customers; forced interruptions"),
    ("oncor_customers", "REAL", "same", ""), ("oncor_saifi", "DERIVED", "same", "customer-weighted mean feeder SAIFI, forced interruptions"),
    ("oncor_saidi_min", "DERIVED", "same", "customer-weighted mean feeder SAIDI in minutes, forced interruptions"), ("oncor_saidi_max", "REAL", "same", "worst feeder SAIDI"),
    ("large_load_mw_county", "REAL", "ERCOT Batch Zero deck (Sep 2026), data/large_loads.csv", "conditional Batch Zero MW in the sub's county where listed (top-5 lists, RPG study counties)"),
    ("large_load_mw_50km", "DERIVED", "same + mean coordinate of each county's subs", "MW in other listed counties whose centroid is within 50 km"),
    ("eue_avoided_mwh_yr_full", "DERIVED", "eaglei_county.csv", "county value: 1 MW / 4 MWh battery, starts full, 1 MW critical load, events above 1% of customers; a lower bound on outage exposure"),
    ("eue_avoided_mwh_yr_lowsoc", "DERIVED", "eaglei_county.csv", "same with only a 20% reserve at the start"),
    ("voll_usd_per_mwh", "REAL", "Brattle VoLL study, PUCT Project 55837 (Aug 2024)", "VOLL_USD_MWH = 35685 (one-hour, ERCOT-wide)"),
    ("voll_usd_yr_headline", "DERIVED", "eue_full x $35,685", "reliability value, kept apart from market value; upper bound because the study values longer outages lower"),
    ("voll_usd_yr_duration", "DERIVED", "eue_full x Brattle duration table", ""),
    ("uri_eue_mwh_full", "DERIVED", "eaglei_county.csv", "MWh one such battery would have served in Uri"),
    ("derecho_eue_mwh_full", "DERIVED", "eaglei_county.csv", ""), ("beryl_eue_mwh_full", "DERIVED", "eaglei_county.csv", ""),
]


def reliability(*flags):
    build_reliability(fresh=any(f in ("--fresh", "fresh") for f in flags))
