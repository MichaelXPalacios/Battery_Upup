"""Weather for the data layer: station metadata, settlement point -> stations, and NWS hourly forecast snapshots.

Observations (training features) come from the adopted NOAA GHCN-Daily station files (`adopt.adopt_weather`, dataset
`weather_obs`). A day's observation is public the next day: `assumptions.WEATHER_OBS_PUBLISH_*` (ASSUMPTION). A feature at
decision time `asof` may only use observation days whose `published_utc <= asof`, never the same day's reading.

Forecasts come from the National Weather Service API (api.weather.gov): `points/{lat},{lon}` gives the grid cell, then the
gridpoint's hourly forecast. NWS keeps no forecast archive, so `rt_v1` training features do NOT use forecasts. We take a
snapshot whenever asked and store it raw in the lake (`nws/FORECAST-HOURLY`), keyed by issue time, so a forecast history
accumulates from now on (dataset `weather_fcst`).

Requests identify themselves as `hugging-base-dispatch (research)` and carry no email or personal data.

Observations after the adopted history come from `fetch_observations`: NCEI's keyless Access Data Service (GHCN-Daily daily
summaries, TMAX/TMIN/PRCP/WSF2, standard units). Each response is stored raw and immutably (`ncei / GHCND / <fetch date> /
GHCND.<start>_<end>.<sha8>.csv`) with `published_utc` = the moment we fetched it. The window starts `WEATHER_FETCH_REFETCH_DAYS` before the
newest day in the lake so a revised value comes back as a new file. `parse.parse_weather_obs` turns the files into versions (a row appears
again only when a value changed), each published when it first appeared; `catalog.observations_asof` resolves them at a decision time.
"""
from __future__ import annotations

import hashlib
import io
import json
import re
from typing import Callable, Optional

import pandas as pd

from . import assumptions as A
from . import lake
from .ercot import FetchError, Fetcher
from .times import CPT, iso_z, utc

NWS_API = "https://api.weather.gov"
USER_AGENT = "hugging-base-dispatch (research)"
NWS_REPORT_ID = "FORECAST-HOURLY"

# GHCN-Daily airport stations: id -> (code, lat, lon). REAL ids from docs/research/arena/arena/config.py (ERCOT_STATIONS);
# coordinates are the airports' (approximate, REAL) from docs/research/grid/gridsite/risk.py STATIONS.
STATIONS = {
    "USW00003927": ("DFW", 32.898, -97.019),
    "USW00012960": ("IAH", 29.980, -95.360),
    "USW00012924": ("CRP", 27.774, -97.512),
    "USW00012919": ("BRO", 25.905, -97.426),
    "USW00023023": ("MAF", 31.949, -102.209),
    "USW00013962": ("ABI", 32.411, -99.682),
    "USW00013904": ("AUS", 30.183, -97.680),
    "USW00012921": ("SAT", 29.544, -98.484),
}
# Load zone -> stations. REAL (arena config, ASSUMPTION there: station choice).
ZONE_STATIONS = {
    "LZ_NORTH": ("USW00003927",), "LZ_HOUSTON": ("USW00012960",), "LZ_SOUTH": ("USW00012924", "USW00012919"),
    "LZ_WEST": ("USW00023023", "USW00013962"), "LZ_AEN": ("USW00013904",), "LZ_CPS": ("USW00012921",),
    "LZ_LCRA": ("USW00013904", "USW00012921"), "LZ_RAYBN": ("USW00003927",),
}
# System-wide index weights. REAL (arena config; rough load shares there, ASSUMPTION).
SYSTEM_WEIGHTS = {"USW00003927": 0.36, "USW00012960": 0.30, "USW00012921": 0.12, "USW00013904": 0.10,
                  "USW00012924": 0.06, "USW00023023": 0.06}
# Hubs -> the load zone whose weather they follow. ASSUMPTION: HB_PAN (Panhandle) follows LZ_WEST; the system hubs use the index.
HUB_ZONE = {"HB_NORTH": "LZ_NORTH", "HB_SOUTH": "LZ_SOUTH", "HB_WEST": "LZ_WEST", "HB_HOUSTON": "LZ_HOUSTON", "HB_PAN": "LZ_WEST",
            "HB_BUSAVG": "SYSTEM", "HB_HUBAVG": "SYSTEM"}
# Load zone -> ERCOT weather-zone columns of the NP3-565-CD load forecast. ASSUMPTION: weather zones are not the settlement
# load zones; this is the nearest overlap (NOIE zones map to the weather zone they sit in).
LZ_WEATHER_ZONES = {"LZ_NORTH": ("north_central_mw", "north_mw"), "LZ_HOUSTON": ("coast_mw",), "LZ_SOUTH": ("southern_mw", "south_central_mw"),
                    "LZ_WEST": ("far_west_mw", "west_mw"), "LZ_AEN": ("south_central_mw",), "LZ_CPS": ("south_central_mw",),
                    "LZ_LCRA": ("south_central_mw",), "LZ_RAYBN": ("north_central_mw",), "SYSTEM": ("system_total_mw",)}


def load_zone_of(sp: str, sp_map: Optional[pd.DataFrame] = None) -> str:
    """Load zone (or 'SYSTEM') whose weather and load forecast a settlement point follows.
    Hubs and zones are built in; a resource node is looked up in the parsed NP4-160 `sp_map` if given; unknown -> 'SYSTEM'."""
    s = sp.strip().upper()
    if s in HUB_ZONE:
        return HUB_ZONE[s]
    if s in ZONE_STATIONS:
        return s
    if sp_map is not None and len(sp_map):
        m = sp_map[(sp_map["settlement_point"] == sp) & (sp_map["kind"] == "RESOURCE_NODE")]
        if len(m):
            z = m.sort_values("published_utc").iloc[-1]["load_zone"]
            if z in ZONE_STATIONS:
                return z
    return "SYSTEM"


def stations_for(zone: str) -> dict[str, float]:
    """{station: weight} for a load zone (equal weights) or 'SYSTEM' (the arena's system weights)."""
    if zone == "SYSTEM":
        return dict(SYSTEM_WEIGHTS)
    st = ZONE_STATIONS.get(zone, ())
    return {s: 1.0 / len(st) for s in st} if st else dict(SYSTEM_WEIGHTS)


# ------------------------------------------------------------------------------------------------ NWS forecast snapshot
def _nws_fetcher() -> Fetcher:
    import httpx
    return Fetcher(httpx.Client(headers={"User-Agent": USER_AGENT, "Accept": "application/geo+json"}, timeout=60.0,
                                follow_redirects=True))


def _get_json(f: Fetcher, url: str) -> dict:
    r = f.request("GET", url)
    if r.status_code != 200:
        raise FetchError(f"NWS HTTP {r.status_code}: {url}")
    return r.json()


def snapshot_forecasts(stations: Optional[dict] = None, *, fetcher: Optional[Fetcher] = None, now=None,
                       log: Callable[[str], None] = lambda s: None) -> dict:
    """Take one NWS hourly-forecast snapshot for each station and store the raw JSON in the lake.

    Lake file: nws / FORECAST-HOURLY / <issue date> / <CODE>.<issue time>.json, with published_utc = the NWS issue time
    (`generatedAt`, capped at the moment we fetched it, since we cannot have known it earlier). Returns a summary dict;
    one failing station does not stop the others."""
    f = fetcher or _nws_fetcher()
    t_fetch = utc(now) if now is not None else pd.Timestamp.now(tz="UTC")
    out = {"fetched_utc": iso_z(t_fetch), "stations": {}, "failed": {}}
    for sid, (code, lat, lon) in (stations or STATIONS).items():
        try:
            pt = _get_json(f, f"{NWS_API}/points/{lat:.4f},{lon:.4f}")
            url = pt["properties"]["forecastHourly"]
            fc = _get_json(f, url)
            props = fc["properties"]
            issued = utc(props.get("generatedAt") or props.get("updateTime") or props.get("updated") or t_fetch)
            issued = min(issued, t_fetch)
            body = json.dumps({"station": sid, "code": code, "lat": lat, "lon": lon, "grid": pt["properties"].get("gridId"),
                               "gridX": pt["properties"].get("gridX"), "gridY": pt["properties"].get("gridY"),
                               "fetched_utc": iso_z(t_fetch), "issued_utc": iso_z(issued), "forecast": fc},
                              sort_keys=True, separators=(",", ":")).encode("utf-8")
            name = f"{code}.{issued.strftime('%Y%m%dT%H%M%SZ')}.json"
            p = lake.put("nws", NWS_REPORT_ID, issued, name, body, "api")
            out["stations"][sid] = {"file": p.name, "issued_utc": iso_z(issued), "periods": len(props.get("periods", []))}
            log(f"  NWS {code}: issued {iso_z(issued)}, {len(props.get('periods', []))} hourly periods")
        except Exception as e:
            out["failed"][sid] = f"{type(e).__name__}: {e}"
    return out


_NUM = re.compile(r"-?\d+(?:\.\d+)?")


def wind_mph(text) -> float:
    """'10 mph' -> 10.0; '5 to 10 mph' -> 10.0 (the larger figure, gusts aside); anything unreadable -> NaN."""
    if not isinstance(text, str):
        return float("nan")
    nums = [float(x) for x in _NUM.findall(text)]
    return max(nums) if nums else float("nan")


# ------------------------------------------------------------------------------------------------ GHCN-Daily observations (NCEI)
NCEI_URL = "https://www.ncei.noaa.gov/access/services/data/v1"
OBS_SOURCE, OBS_REPORT_ID = "ncei", "GHCND"
OBS_DATA_TYPES = ("TMAX", "TMIN", "PRCP", "WSF2")           # degF, degF, inches, mph with units=standard
# The 18 stations the research folder holds (research-data/weather/USW*.csv): the 8 ERCOT airports of `STATIONS` and the 10 NYISO stations
# of the arena (NY_STATIONS in docs/research/arena/arena/config.py). The ERCOT features read only the first 8; the lake keeps all 18.
NY_OBS_STATIONS = ("USW00014733", "USW00014768", "USW00014771", "USW00094725", "USW00014735", "USW00014757", "USW00094745", "USW00094728",
                   "USW00014732", "USW00004781")
OBS_STATIONS = tuple(sorted(set(STATIONS) | set(NY_OBS_STATIONS)))
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_BOUNDS = {"TMAX": (-100.0, 150.0), "TMIN": (-100.0, 150.0), "PRCP": (0.0, 60.0), "WSF2": (0.0, 300.0)}       # an absurd value is a corrupt file


def obs_fetcher() -> Fetcher:
    """One request at a time, A.FETCH_MIN_SPACING_S apart, a few retries with backoff; identifies itself, sends nothing personal."""
    import httpx
    client = httpx.Client(headers={"User-Agent": A.FETCH_USER_AGENT, "Accept": "text/csv"}, timeout=90.0, follow_redirects=True)
    return Fetcher(client, max_concurrent=1, min_spacing_s=A.FETCH_MIN_SPACING_S, retries=A.FETCH_RETRIES)


def obs_request_params(stations, start: str, end: str) -> dict:
    """The query of one Access Data Service request (the URL is NCEI_URL)."""
    return {"dataset": "daily-summaries", "stations": ",".join(stations), "startDate": start, "endDate": end,
            "dataTypes": ",".join(OBS_DATA_TYPES), "units": "standard", "format": "csv"}


def last_observation_date() -> Optional[str]:
    """Newest observation day ('YYYY-MM-DD', the station's local date) the lake holds, or None. Reads the newest parsed monthly partition (cheap);
    without parsed data it scans the raw files' DATE columns. A fetch that was stored but not parsed yet only makes the next window longer."""
    from . import parse
    files = parse.partition_files("weather_obs")
    if files:
        import pyarrow.parquet as pq
        days = pq.read_table(files[-1], columns=["obs_date"]).column(0).to_pylist()
        if days:
            return max(days)
    best = None
    for rf in lake.iter_raw(OBS_REPORT_ID):
        d = pd.read_csv(rf.path, usecols=["DATE"], dtype=str, keep_default_na=False)
        if len(d):
            m = d["DATE"].str.strip().max()
            best = m if best is None or m > best else best
    return best


def validate_obs_csv(body: bytes, stations, start: str, end: str) -> pd.DataFrame:
    """Check one response before it may enter the lake and return it as a string frame. Raises FetchError for anything that is not a GHCN-Daily csv
    for these stations and days: an HTML error page, a wrong header, a station or day we did not ask for, a duplicate, a number that is not one.
    A well-formed response with no rows returns an empty frame (nothing new yet; the caller stores nothing)."""
    if len(body) > A.FETCH_MAX_BYTES:
        raise FetchError(f"NCEI response of {len(body)} bytes is above the {A.FETCH_MAX_BYTES} byte limit")
    text = body.decode("utf-8-sig", errors="replace")
    if not text.strip():
        return pd.DataFrame(columns=["STATION", "DATE", *OBS_DATA_TYPES])
    head = text.lstrip().splitlines()[0]
    if "STATION" not in head or "DATE" not in head or head.lstrip().startswith("<"):
        raise FetchError("NCEI response is not a GHCN-Daily csv (header: %r)" % head[:60])
    d = pd.read_csv(io.StringIO(text), dtype=str, keep_default_na=False, skipinitialspace=True)
    d.columns = [c.strip() for c in d.columns]
    d["STATION"], d["DATE"] = d["STATION"].str.strip(), d["DATE"].str.strip()
    if not d["DATE"].map(lambda x: bool(_DATE.match(x))).all():
        raise FetchError("NCEI response has a DATE that is not YYYY-MM-DD")
    stray = sorted(set(d["STATION"]) - set(stations))
    if stray:
        raise FetchError(f"NCEI response has stations that were not requested: {stray[:3]}")
    if (d["DATE"] < start).any() or (d["DATE"] > end).any():
        raise FetchError(f"NCEI response has days outside {start}..{end}")
    if d.duplicated(["STATION", "DATE"]).any():
        raise FetchError("NCEI response repeats a station and day")
    for c in OBS_DATA_TYPES:
        if c not in d.columns:
            continue
        raw = d[c].str.strip()
        num = pd.to_numeric(raw.replace({"": None}), errors="coerce")
        if (num.isna() & raw.ne("")).any():
            raise FetchError(f"NCEI response has a non-numeric {c}")
        lo, hi = _BOUNDS[c]
        if ((num < lo) | (num > hi)).any():
            raise FetchError(f"NCEI response has a {c} outside [{lo}, {hi}]")
    return d


def _chunks(start: str, end: str, max_days: int):
    a, b = pd.Timestamp(start), pd.Timestamp(end)
    while a <= b:
        e = min(a + pd.Timedelta(days=max_days - 1), b)
        yield a.strftime("%Y-%m-%d"), e.strftime("%Y-%m-%d")
        a = e + pd.Timedelta(days=1)


def fetch_observations(now=None, *, fetcher: Optional[Fetcher] = None, start: Optional[str] = None, end: Optional[str] = None,
                       refetch_days: Optional[int] = None, stations=None, log: Callable[[str], None] = lambda s: None) -> dict:
    """Fetch GHCN-Daily for the stations and store each response raw. The window ends today (Central date) and starts `refetch_days` before the
    newest day in the lake, or A.WEATHER_FETCH_MIN_WINDOW_DAYS before today if that is earlier.

    * published_utc of the raw file = `now` (the moment we held it); origin 'web'. The name carries the window and the first 8 hex digits of the
      sha256, so fetching the same bytes again is a no-op in the lake (the first fetch time stays) and different bytes (a new day, a revised or
      filled-in value) are a new file next to the old one: both are kept, nothing is overwritten.
    * Nothing is stored for a response that fails `validate_obs_csv` (the call raises) or has no rows.
    * With an empty lake and no `start` this raises: pulling the whole history is `flows adopt`, not a daily fetch.
    Returns a summary; `lag_days` is the age of the newest day at the fetch, measured from the end of that day.
    """
    f = fetcher or obs_fetcher()
    t_fetch = utc(now) if now is not None else pd.Timestamp.now(tz="UTC")
    stations = tuple(stations or OBS_STATIONS)
    refetch = A.WEATHER_FETCH_REFETCH_DAYS if refetch_days is None else int(refetch_days)
    end_s = str(end) if end else t_fetch.tz_convert(CPT).strftime("%Y-%m-%d")
    last = last_observation_date()
    if start is not None:
        start_s = str(start)
    elif last is None:
        raise FetchError("the lake holds no weather observations: adopt the history first (python -m dispatch.data.flows adopt) or pass start=")
    else:
        first = pd.Timestamp(last) - pd.Timedelta(days=refetch)
        floor = pd.Timestamp(end_s) - pd.Timedelta(days=A.WEATHER_FETCH_MIN_WINDOW_DAYS)
        start_s = min(first, floor).strftime("%Y-%m-%d")
    start_s = min(start_s, end_s)
    out = {"source": OBS_SOURCE, "report_id": OBS_REPORT_ID, "fetched_utc": iso_z(t_fetch), "start": start_s, "end": end_s,
           "last_day_in_lake": last, "requests": 0, "stored": [], "rows": 0, "newest_obs_date": None, "lag_days": None,
           "missing_stations": []}
    seen_stations, newest = set(), None
    for cs, ce in _chunks(start_s, end_s, A.WEATHER_FETCH_MAX_SPAN_DAYS):
        r = f.request("GET", NCEI_URL, params=obs_request_params(stations, cs, ce))
        out["requests"] += 1
        if r.status_code != 200:
            raise FetchError(f"NCEI HTTP {r.status_code}")
        d = validate_obs_csv(r.content, stations, cs, ce)
        if d.empty:
            log(f"  NCEI {cs}..{ce}: no rows")
            continue
        seen_stations |= set(d["STATION"])
        top = d["DATE"].max()
        newest = top if newest is None or top > newest else newest
        name = f"{OBS_REPORT_ID}.{cs}_{ce}.{hashlib.sha256(r.content).hexdigest()[:8]}.csv"
        new = not lake.has(OBS_SOURCE, OBS_REPORT_ID, name)
        lake.put(OBS_SOURCE, OBS_REPORT_ID, t_fetch, name, r.content, "web")
        out["stored"].append({"file": name, "bytes": len(r.content), "rows": len(d), "new": new})
        out["rows"] += len(d)
        log(f"  NCEI {cs}..{ce}: {len(d)} rows, newest day {top}, {'stored' if new else 'identical to a file already in the lake'}")
    out["missing_stations"] = sorted(set(stations) - seen_stations) if out["stored"] else []
    if newest is not None:
        out["newest_obs_date"] = newest
        day_end = (pd.Timestamp(newest) + pd.Timedelta(days=1)).tz_localize(CPT, ambiguous=False, nonexistent="shift_forward")
        out["lag_days"] = round((t_fetch - day_end).total_seconds() / 86400, 3)
    out["changed"] = any(x["new"] for x in out["stored"])
    return out
