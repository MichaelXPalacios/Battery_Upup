"""Weather for the data layer: station metadata, settlement point -> stations, and NWS hourly forecast snapshots.

Observations (training features) come from the adopted NOAA GHCN-Daily station files (`adopt.adopt_weather`, dataset
`weather_obs`). A day's observation is public the next day: `assumptions.WEATHER_OBS_PUBLISH_*` (ASSUMPTION). A feature at
decision time `asof` may only use observation days whose `published_utc <= asof`, never the same day's reading.

Forecasts come from the National Weather Service API (api.weather.gov): `points/{lat},{lon}` gives the grid cell, then the
gridpoint's hourly forecast. NWS keeps no forecast archive, so `rt_v1` training features do NOT use forecasts. We take a
snapshot whenever asked and store it raw in the lake (`nws/FORECAST-HOURLY`), keyed by issue time, so a forecast history
accumulates from now on (dataset `weather_fcst`).

Requests identify themselves as `hugging-base-dispatch (research)` and carry no email or personal data.
"""
from __future__ import annotations

import json
import re
from typing import Callable, Optional

import pandas as pd

from . import lake
from .ercot import FetchError, Fetcher
from .times import iso_z, utc

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
