"""Load prices, weather and gas into aligned daily arrays (cached as .npz in ARENA_DATA/arena_cache).

Prices: one (days, zones, 96) array per market, 15-minute real-time prices in $/MWh.
  ERCOT: NP6-785-ER yearly workbooks (2011-2026), load zones plus HB_BUSAVG.
  NYISO: the 15-minute time-weighted zonal LBMPs built by nyiso_zone_study.py (2003-2026).
Daylight-saving days are normalised to 96 intervals (the repeated hour is dropped; a skipped hour repeats
the hour before it), so every day lines up on the same clock.
Weather: NOAA GHCN-Daily TMAX/TMIN (deg F), WSF2 (mph), PRCP (in) per station, gaps filled.
Gas: Henry Hub daily spot (FRED DHHNGSP), $/MMBtu, forward-filled over weekends and holidays.
"""
import csv
import glob
import io
import os
import zipfile
from collections import defaultdict
from datetime import date, datetime, timedelta

import numpy as np

from . import config as C


# ----------------------------------------------------------------------------------- ERCOT prices
def _ercot_year_csv(year):
    """Workbook -> csv once; same format as ercot_zone_study.py's cache (LZEW rows dropped)."""
    path = C.DATA / "ercot" / f"rtm{year}.csv"
    if path.exists():
        return path
    import openpyxl
    xlsx = glob.glob(str(C.DATA / "ercot" / f"*RTMLZHBSPP_{year}.xlsx"))[0]
    wb = openpyxl.load_workbook(xlsx, read_only=True)
    tmp = str(path) + ".tmp"
    with open(tmp, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["date", "hour", "interval", "rep", "sp", "sptype", "price"])
        for ws in wb.worksheets:
            ws.reset_dimensions()          # older workbooks carry a wrong sheet dimension
            for r in ws.iter_rows(min_row=2, values_only=True):
                if not r or len(r) < 7 or r[0] is None or r[6] is None or r[5] == "LZEW":
                    continue
                if isinstance(r[0], str):
                    m, d, y = r[0].split("/")
                    iso = f"{y}-{m}-{d}"
                else:
                    iso = r[0].date().isoformat()
                w.writerow([iso, r[1], r[2], r[3], r[4], r[5], r[6]])
    os.replace(tmp, path)
    return path


def _to96_ercot(rows):
    """rows: [(hour, interval, rep, price)] -> 96 values on a 24-hour clock."""
    grid = np.full((24, 4), np.nan)
    for h, i, rep, p in rows:
        if rep == "Y" or not (1 <= h <= 24) or not (1 <= i <= 4):
            continue
        grid[h - 1, i - 1] = p
    for h in range(24):                     # a skipped (spring-forward) hour repeats the hour before
        if np.isnan(grid[h]).all():
            grid[h] = grid[h - 1] if h > 0 else grid[h + 1]
    v = grid.reshape(-1)
    if np.isnan(v).any():                   # isolated gaps: carry the previous value
        for k in range(96):
            if np.isnan(v[k]):
                v[k] = v[k - 1] if k > 0 else np.nanmean(v)
    return v


def ercot_prices(years=range(2011, 2027)):
    cache = C.CACHE / "prices_ERCOT.npz"
    if cache.exists():
        z = np.load(cache)
        return z["dates"].astype(str), list(z["zones"].astype(str)), z["P"]
    zones = C.ERCOT_ZONES + [C.ERCOT_REF]
    zi = {z: k for k, z in enumerate(zones)}
    by_day = defaultdict(lambda: defaultdict(list))
    for y in years:
        with open(_ercot_year_csv(y), encoding="utf-8") as f:
            for r in csv.DictReader(f):
                if r["sp"] in zi:
                    by_day[r["date"]][r["sp"]].append((int(r["hour"]), int(r["interval"]), r["rep"], float(r["price"])))
    dates = sorted(d for d in by_day if all(z in by_day[d] for z in zones))
    P = np.zeros((len(dates), len(zones), C.NI), np.float32)
    for k, d in enumerate(dates):
        for z in zones:
            P[k, zi[z]] = _to96_ercot(by_day[d][z])
    C.CACHE.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(cache, dates=np.array(dates), zones=np.array(zones), P=P)
    return np.array(dates), zones, P


# ----------------------------------------------------------------------------------- NYISO prices
def _to96_ny(v):
    v = np.asarray(v, float)
    if len(v) == 96:
        return v
    if len(v) > 96:                         # fall-back day: the repeated hour sits at buckets 8-11
        return np.delete(v, range(8, 8 + len(v) - 96))
    return np.insert(v, 8, np.repeat(v[7], 96 - len(v)))


def nyiso_prices(years=range(2003, 2027)):
    cache = C.CACHE / "prices_NYISO.npz"
    if cache.exists():
        z = np.load(cache)
        return z["dates"].astype(str), list(z["zones"].astype(str)), z["P"]
    zones = list(C.NY_ZONES)
    per = {}
    for y in years:
        f = C.DATA / "nyiso" / "cache" / f"q15_{y}.npz"
        if not f.exists():
            continue
        data = np.load(f, allow_pickle=True)["data"].item()
        for z in zones:
            for d, (lb, _cg, _mx) in data.get(z, {}).items():
                per.setdefault(d, {})[z] = _to96_ny(lb)
    dates = sorted(d for d in per if all(z in per[d] for z in zones))
    P = np.zeros((len(dates), len(zones), C.NI), np.float32)
    for k, d in enumerate(dates):
        for j, z in enumerate(zones):
            P[k, j] = per[d][z]
    C.CACHE.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(cache, dates=np.array(dates), zones=np.array(zones), P=P)
    return np.array(dates), zones, P


# ----------------------------------------------------------------------------------- weather
FIELDS = ("TMAX", "TMIN", "WSF2", "PRCP")


def _station(st):
    path = C.DATA / "weather" / f"{st}.csv"
    out = {}
    if not path.exists():
        return out
    with open(path, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            vals = []
            for k in FIELDS:
                s = (r.get(k) or "").strip()
                try:
                    vals.append(float(s))
                except ValueError:
                    vals.append(np.nan)
            out[r["DATE"]] = vals
    return out


def _fill(series, dates):
    """Interpolate short gaps; fill long ones with that station's day-of-year mean."""
    x = series.copy()
    n = len(x)
    ok = ~np.isnan(x)
    if ok.sum() < 30:
        return x
    idx = np.arange(n)
    gap_fill = np.interp(idx, idx[ok], x[ok])
    # gap length per missing day
    run = np.zeros(n, int)
    k = 0
    while k < n:
        if not ok[k]:
            j = k
            while j < n and not ok[j]:
                j += 1
            run[k:j] = j - k
            k = j
        else:
            k += 1
    doy = np.array([datetime.strptime(d, "%Y-%m-%d").timetuple().tm_yday for d in dates]) - 1
    clim = np.array([np.nanmean(x[doy == t]) if np.any(ok & (doy == t)) else np.nan for t in range(366)])
    clim = np.where(np.isnan(clim), np.nanmean(x), clim)
    x = np.where(ok, x, np.where(run <= 5, gap_fill, clim[doy]))
    return x


def weather(market, dates):
    """Per-region local and system weather aligned to `dates` (dict of arrays shaped (days, regions))."""
    stations = C.ERCOT_STATIONS if market == "ERCOT" else C.NY_STATIONS
    weights = C.ERCOT_SYSTEM_WEIGHTS if market == "ERCOT" else C.NY_SYSTEM_WEIGHTS
    zones = C.ERCOT_ZONES if market == "ERCOT" else C.NY_ZONES
    needed = sorted({s for v in stations.values() for s in v} | set(weights))
    filled = {}
    for st in needed:
        raw = _station(st)
        if not raw:
            continue
        arr = np.array([raw.get(d, [np.nan] * 4) for d in dates], float)   # (days, 4)
        cols = []
        for j, k in enumerate(FIELDS):
            col = arr[:, j]
            if k in ("TMAX", "TMIN"):
                col = _fill(col, dates)
            else:
                col = np.where(np.isnan(col), 0.0, col)
            cols.append(col)
        filled[st] = np.stack(cols, 1)
    nd, nz = len(dates), len(zones)
    out = {k: np.full((nd, nz), np.nan) for k in ("tmax", "tmin", "wsf2", "prcp")}
    for j, z in enumerate(zones):
        sts = [s for s in stations[z] if s in filled]
        if not sts:
            continue
        stack = np.stack([filled[s] for s in sts], 0)     # (n_st, days, 4)
        out["tmax"][:, j] = np.nanmean(stack[:, :, 0], 0)
        out["tmin"][:, j] = np.nanmean(stack[:, :, 1], 0)
        out["wsf2"][:, j] = np.nanmax(stack[:, :, 2], 0)
        out["prcp"][:, j] = np.nanmax(stack[:, :, 3], 0)
    wsum = sum(weights[s] for s in weights if s in filled)
    sys_tmax = sum(filled[s][:, 0] * weights[s] for s in weights if s in filled) / wsum
    sys_tmin = sum(filled[s][:, 1] * weights[s] for s in weights if s in filled) / wsum
    out["sys_tmax"], out["sys_tmin"] = sys_tmax, sys_tmin
    for k in ("tmax", "tmin"):                            # a zone without its station uses the system index
        miss = np.isnan(out[k])
        if miss.any():
            out[k] = np.where(miss, (sys_tmax if k == "tmax" else sys_tmin)[:, None], out[k])
    for k in ("wsf2", "prcp"):
        out[k] = np.nan_to_num(out[k])
    out["stations_found"] = sorted(filled)
    return out


# ----------------------------------------------------------------------------------- gas
def gas(dates):
    path = C.DATA / "gas" / "DHHNGSP.csv"
    obs = {}
    with open(path, encoding="utf-8") as f:
        rd = csv.reader(f)
        next(rd)
        for r in rd:
            try:
                obs[r[0]] = float(r[1])
            except (ValueError, IndexError):
                pass
    keys = sorted(obs)
    out = np.empty(len(dates))
    last = obs[keys[0]]
    allk = iter(keys)
    # walk calendar days, carrying the last observation forward (weekends, holidays)
    pos = 0
    for i, d in enumerate(dates):
        while pos < len(keys) and keys[pos] <= d:
            last = obs[keys[pos]]
            pos += 1
        out[i] = last
    return np.clip(out, 1.0, None)


# ----------------------------------------------------------------------------------- market bundle
class Market:
    """Everything one market needs: dates, zones, prices, reference series, weather, gas."""

    def __init__(self, name):
        self.name = name
        if name == "ERCOT":
            dates, zones, P = ercot_prices()
            self.zones = C.ERCOT_ZONES
            self.ref_name = C.ERCOT_REF
        else:
            dates, zones, P = nyiso_prices()
            self.zones = C.NY_ZONES
            self.ref_name = C.NY_REF
        self.dates = np.asarray(dates)
        zi = {z: k for k, z in enumerate(zones)}
        self.P = P[:, [zi[z] for z in self.zones], :].astype(np.float64)     # (days, regions, 96)
        self.ref = P[:, zi[self.ref_name], :].astype(np.float64)             # (days, 96)
        self.years = np.array([int(d[:4]) for d in self.dates])
        dt = [datetime.strptime(d, "%Y-%m-%d") for d in self.dates]
        self.month = np.array([x.month for x in dt])
        self.doy = np.array([x.timetuple().tm_yday for x in dt])
        self.weekend = np.array([x.weekday() >= 5 for x in dt])
        self.W = weather(name, list(self.dates))
        self.gas = gas(list(self.dates))
        self.labels = [C.LABEL[z] for z in self.zones]

    def year_slice(self, y0, y1=None):
        y1 = y0 if y1 is None else y1
        return np.where((self.years >= y0) & (self.years <= y1))[0]
