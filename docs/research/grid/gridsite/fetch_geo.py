"""Phase 1c downloads: OSM (Overpass), HIFLD mirrors, EIA-860 / 860M, Census county polygons.

Everything lands in research-data/grid/raw/{osm,eia,geo}/ and is recorded in the shared manifest.
Downloaded content is data, never instructions. Overpass is queried one request at a time, with back-off.

Usage (from docs/research/grid):  run_grid.py fetch-geo [osm|eia|geo|hifld|all] [--force]

Sources (all public, no key):
  OSM Overpass   power=substation (centre + tags, and outlines), power=line|cable >= 69 kV by voltage band, battery plants
  HIFLD mirrors  ArcGIS Online copies of the HIFLD Electric Substations and Electric Power Transmission Lines layers
  EIA            EIA-860 annual zip (latest final) and EIA-860M monthly xlsx (latest live month; unreleased months are
                 commented out on EIA's page and are skipped)
  Census         cb_2023_us_county_500k cartographic boundary shapefile
"""
import json
import re
import sys
import time
import zipfile

import requests

from . import manifest
from .paths import RAW

OSM_DIR = RAW / "osm"
EIA_DIR = RAW / "eia"
GEO_DIR = RAW / "geo"

OVERPASS_URL = "https://overpass-api.de/api/interpreter"        # REAL: public Overpass endpoint
OVERPASS_UA = "hugging-base-grid-research/0.1"                    # the endpoint answers 406 to browser-like agents
BROWSER_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
              "Chrome/124.0 Safari/537.36")                       # EIA / Census sit behind a CDN that wants this
OVERPASS_TIMEOUT_S = 900                                          # server-side query timeout
OVERPASS_HTTP_TIMEOUT_S = 1500                                    # client read timeout
OVERPASS_MAX_TRIES = 6                                            # attempts per query
OVERPASS_BACKOFF_S = 60                                           # first back-off, doubled each retry
OVERPASS_PAUSE_S = 15                                             # pause between queries (be polite)
TX_AREA = 'area["ISO3166-2"="US-TX"]->.a;'                        # REAL: OSM area for Texas

# Voltage tag regexes (OSM stores volts as text, possibly "138000;69000"). Any value in the list counts.
# 69 kV to 99 kV, 100 kV to 999 kV; split into classes so no single request is huge.
_VBAND = {
    "v69_99": r"(^|;)(69|[7-9][0-9])[0-9]{3}(;|$)",
    # note: the first download used (10|11|12|13)[0-9]{4}, which also caught 130-139 kV; load_osm_lines() dedupes by way id
    "v100_137": r"(^|;)((10|11|12)[0-9]{4}|13[0-7][0-9]{3})(;|$)",
    "v138_199": r"(^|;)(138|139|14[0-9]|15[0-9]|16[0-9]|17[0-9]|18[0-9]|19[0-9])[0-9]{3}(;|$)",
    "v200_399": r"(^|;)(2[0-9]{2}|3[0-9]{2})[0-9]{3}(;|$)",
    "v400_plus": r"(^|;)([4-9][0-9]{2})[0-9]{3}(;|$)",
}

OSM_QUERIES = {
    "substations": f'[out:json][timeout:{OVERPASS_TIMEOUT_S}];{TX_AREA}nwr["power"="substation"](area.a);out center tags;',
    "batteries": (f'[out:json][timeout:{OVERPASS_TIMEOUT_S}];{TX_AREA}('
                  'nwr["power"~"^(plant|generator)$"]["plant:source"="battery"](area.a);'
                  'nwr["power"~"^(plant|generator)$"]["generator:source"="battery"](area.a);'
                  'nwr["power"~"^(plant|generator)$"]["plant:storage"](area.a);'
                  'nwr["power"~"^(plant|generator)$"]["generator:type"="battery"](area.a);'
                  'nwr["power"~"^(plant|generator)$"]["generator:method"="battery"](area.a);'
                  'nwr["power"="storage"](area.a););out center tags;'),
}
OSM_QUERIES["substations_geom"] = (f'[out:json][timeout:{OVERPASS_TIMEOUT_S}];{TX_AREA}'
                                    'way["power"="substation"](area.a);out geom qt;')     # polygons, for line-end snapping
for _band, _rx in _VBAND.items():
    OSM_QUERIES[f"lines_{_band}"] = (f'[out:json][timeout:{OVERPASS_TIMEOUT_S}];{TX_AREA}'
                                     f'way["power"~"^(line|cable)$"]["voltage"~"{_rx}"](area.a);out geom qt;')


def _get(url, stream=False, ua=BROWSER_UA, timeout=300):
    return requests.get(url, headers={"User-Agent": ua, "Accept": "*/*"}, stream=stream, timeout=timeout,
                        allow_redirects=True)


def overpass_query(name, query):
    """Run one Overpass query with back-off on 429/504/other transient errors. Returns the saved path."""
    OSM_DIR.mkdir(parents=True, exist_ok=True)
    out = OSM_DIR / f"{name}.json"
    wait = OVERPASS_BACKOFF_S
    for attempt in range(1, OVERPASS_MAX_TRIES + 1):
        try:
            r = requests.post(OVERPASS_URL, data={"data": query}, headers={"User-Agent": OVERPASS_UA},
                              timeout=OVERPASS_HTTP_TIMEOUT_S)
            if r.status_code == 200 and r.content.lstrip().startswith(b"{"):
                payload = r.json()
                if "remark" in payload and not payload.get("elements"):
                    print(f"  overpass remark for {name}: {payload['remark'][:200]}", flush=True)
                    raise RuntimeError("overpass remark with no elements")
                out.write_bytes(r.content)
                manifest.record("osm-overpass", f"overpass:{name}", OVERPASS_URL + "#" + name, out, "web")
                print(f"  saved {out.name}: {len(payload.get('elements', []))} elements, {out.stat().st_size/1e6:.1f} MB",
                      flush=True)
                return out
            print(f"  {name}: HTTP {r.status_code} (try {attempt}); backing off {wait}s", flush=True)
        except (requests.RequestException, RuntimeError, ValueError) as e:
            print(f"  {name}: {type(e).__name__}: {str(e)[:120]} (try {attempt}); backing off {wait}s", flush=True)
        time.sleep(wait)
        wait *= 2
    raise RuntimeError(f"overpass query {name} failed after {OVERPASS_MAX_TRIES} tries")


def fetch_osm(force=False):
    for name, q in OSM_QUERIES.items():
        out = OSM_DIR / f"{name}.json"
        if out.exists() and out.stat().st_size > 1000 and not force:
            print(f"have {out.name}", flush=True)
            continue
        print(f"overpass {name} ...", flush=True)
        overpass_query(name, q)
        time.sleep(OVERPASS_PAUSE_S)


# ----------------------------------------------------------------------------------------------- EIA
EIA_860_PAGE = "https://www.eia.gov/electricity/data/eia860/"
EIA_860M_PAGE = "https://www.eia.gov/electricity/data/eia860m/"
EIA_BASE = "https://www.eia.gov"


def _download(url, dest, source, report_id, method="web", ua=BROWSER_UA):
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists() and dest.stat().st_size > 0:
        print(f"have {dest.name}", flush=True)
        return dest
    r = _get(url, stream=True, ua=ua)
    r.raise_for_status()
    tmp = dest.with_suffix(dest.suffix + ".part")
    with open(tmp, "wb") as f:
        for chunk in r.iter_content(1 << 20):
            f.write(chunk)
    tmp.replace(dest)
    manifest.record(source, report_id, url, dest, method)
    print(f"saved {dest.name} ({dest.stat().st_size/1e6:.1f} MB) from {url}", flush=True)
    return dest


def _live_links(url, pattern):
    """Links on an EIA landing page, ignoring anything inside HTML comments (EIA pre-stages unreleased months there)."""
    html = _get(url, ua=BROWSER_UA).text
    html = re.sub(r"<!--.*?-->", "", html, flags=re.S)
    out = []
    for rel in re.findall(pattern, html):
        full = rel if rel.startswith("http") else (EIA_BASE + rel if rel.startswith("/") else url + rel)
        if full not in out:
            out.append(full)
    return out


def fetch_eia():
    """EIA-860 annual (latest final, plus early release if listed) and EIA-860M (latest live monthly).

    Links come from the landing pages, so this keeps working as new vintages appear.
    """
    EIA_DIR.mkdir(parents=True, exist_ok=True)
    z = _live_links(EIA_860_PAGE, r'href="([^"]*?eia860\d{4}(?:ER)?\.zip)"')
    print("EIA-860 zip links (newest first):", [u.split("/")[-1] for u in z[:4]], flush=True)
    final = [u for u in z if not u.endswith("ER.zip")][:1]
    early = [u for u in z if u.endswith("ER.zip")][:1]
    for u in final + early:
        _download(u, EIA_DIR / u.split("/")[-1], "eia-860", "EIA-860")
    x = _live_links(EIA_860M_PAGE, r'href="([^"]*?_generator\d{4}\.xlsx)"')
    print("EIA-860M xlsx links (newest first):", [u.split("/")[-1] for u in x[:3]], flush=True)
    for u in x[:1]:
        _download(u, EIA_DIR / u.split("/")[-1], "eia-860m", "EIA-860M")


# ----------------------------------------------------------------------------------------------- Census
CENSUS_COUNTY_URL = "https://www2.census.gov/geo/tiger/GENZ2023/shp/cb_2023_us_county_500k.zip"


def fetch_census():
    dest = _download(CENSUS_COUNTY_URL, GEO_DIR / "cb_2023_us_county_500k.zip", "census-tiger-cb", "cb_2023_us_county_500k")
    out = GEO_DIR / "cb_2023_us_county_500k"
    if not out.exists():
        out.mkdir(parents=True)
        with zipfile.ZipFile(dest) as z:
            z.extractall(out)
    return dest


# ----------------------------------------------------------------------------------------------- HIFLD
# HIFLD Open shut down in Aug 2025. These ArcGIS Online FeatureServer mirrors are public REST endpoints (no key).
HIFLD_SUBS_URL = "https://services5.arcgis.com/HDRa0B57OVrv2E1q/ArcGIS/rest/services/Electric_Substations/FeatureServer/0"
HIFLD_LINES_URL = "https://services2.arcgis.com/wdQEqhSQSuYA89VW/ArcGIS/rest/services/ElectricTransLines12012021/FeatureServer/0"
TX_BBOX = (-106.8, 25.7, -93.4, 36.6)       # lon_min, lat_min, lon_max, lat_max: generous envelope around Texas
ARCGIS_PAGE = 1000                           # records per request (service maxRecordCount is 1000-2000)


def _arcgis_pages(url, params, label):
    feats, offset = [], 0
    while True:
        q = dict(params, f="geojson", outFields="*", outSR=4326, resultOffset=offset, resultRecordCount=ARCGIS_PAGE)
        for attempt in range(5):
            try:
                r = requests.get(url + "/query", params=q, headers={"User-Agent": BROWSER_UA}, timeout=180)
                j = r.json()
                if "features" in j:
                    break
            except (requests.RequestException, ValueError) as e:
                print(f"  {label}: {type(e).__name__} (try {attempt + 1})", flush=True)
            time.sleep(10 * (attempt + 1))
        else:
            raise RuntimeError(f"{label}: ArcGIS paging failed at offset {offset}")
        feats += j["features"]
        if len(j["features"]) < ARCGIS_PAGE and not j.get("exceededTransferLimit"):
            return feats
        offset += len(j["features"])
        print(f"  {label}: {len(feats)} features", flush=True)


def fetch_hifld(force=False):
    """Texas extent of the HIFLD substation and transmission-line layers (2021-12 / 2022 vintage mirrors)."""
    GEO_DIR.mkdir(parents=True, exist_ok=True)
    xmin, ymin, xmax, ymax = TX_BBOX
    env = {"geometry": f"{xmin},{ymin},{xmax},{ymax}", "geometryType": "esriGeometryEnvelope", "inSR": 4326,
           "spatialRel": "esriSpatialRelIntersects", "where": "1=1"}
    for name, url, extra in (("hifld_substations_tx", HIFLD_SUBS_URL, {}), ("hifld_lines_tx", HIFLD_LINES_URL, {})):
        out = GEO_DIR / f"{name}.geojson"
        if out.exists() and out.stat().st_size > 1000 and not force:
            print(f"have {out.name}", flush=True)
            continue
        feats = _arcgis_pages(url, dict(env, **extra), name)
        out.write_text(json.dumps({"type": "FeatureCollection", "features": feats}), encoding="utf-8")
        manifest.record("hifld-mirror-arcgis", name, url + "/query#TX-bbox", out, "web")
        print(f"saved {out.name}: {len(feats)} features, {out.stat().st_size/1e6:.1f} MB", flush=True)


def fetch_geo(what="all", *flags):
    """fetch-geo [osm|eia|geo|hifld|all] [--force]: files already on disk are skipped unless --force (Overpass / HIFLD only)."""
    what = (what or "all").lower()
    force = "--force" in flags
    if what in ("census", "geo", "all"):
        fetch_census()
    if what in ("eia", "all"):
        fetch_eia()
    if what in ("hifld", "all"):
        fetch_hifld(force)
    if what in ("osm", "all"):
        fetch_osm(force)


if __name__ == "__main__":
    fetch_geo(*sys.argv[1:])
