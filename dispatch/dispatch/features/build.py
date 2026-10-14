"""Point-in-time feature builder for `rt_v1` (PRD FR3) and the offline feature store.

    point_in_time(sp, asof_utc, horizon=96) -> DataFrame          one row per target interval, indexed by interval_start_utc
    training_frame(sp, start_utc, end_utc, decision_every="4h") -> (X, y, decision_utc)
    write_offline(sp, start_utc, end_utc, ...) -> [Path]           features/rt_v1/<version>/sp=<sp>/part-YYYY-MM.parquet

Both frames come out of ONE routine (`_compute`), so training and serving cannot disagree.

THE point-in-time rule has exactly two implementations, both in this file:
* `_known_prefix(pcm, asof)` for series that arrive in order (RT prices, gas, weather days, the operating dates of the day-ahead
  prices): knowledge at asof is a prefix of the series. `pcm` is the running maximum of the rows' publish times, so one late row
  hides everything after it until it is published (conservative: a lake that holds such a late row gives a frame with less in it than
  a lake cut at asof; with publication in order, as ERCOT's is, the two are identical, which tests/test_leakage.py proves).
  Windows, lags and profiles only ever read inside that prefix.
* `_visible(pub, asof)` for rows looked up one by one (DAM prices and ancillary prices by target hour, load forecast issues).
Nothing else compares times to `asof`. The leakage test patches these two to prove it notices if they break.

Every output row also carries `max_published_utc` (the newest publish time of anything that fed it), which is <= asof by
construction here and re-checked independently in tests/test_leakage.py.

Window arithmetic (means, deviations, quantiles, the 7-day anchor) is in windows.py: pure numpy, no clock, exact and independent of
the range that was loaded, so the same decision gives the same bits in `training_frame`, `point_in_time` and any rebuild.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.dataset as pads
import pyarrow.parquet as pq

from .. import paths
from ..data import parse
from ..data import weather as wxm
from ..data.reader import read_as, read_prices, read_window
from ..data.times import CPT, utc
from . import sets
from . import windows as W

TS = pa.timestamp("us", tz="UTC")
STEP_US = 15 * 60 * 10**6
HOUR_US = 3600 * 10**6
DAY_US = 24 * HOUR_US
NEG = np.iinfo(np.int64).min          # "never published"
EW_TYPES = ("LZEW", "LZ_DCEW")        # load-zone price variants that duplicate the LZ rows
CHUNK = 1200                          # decision times computed at once (bounds memory: CHUNK x 96 x ~45 floats)


class FeatureDataError(RuntimeError):
    """The lake cannot supply what a feature row needs (no price history for the settlement point, no data at all)."""


# ------------------------------------------------------------------------------------------------ the point-in-time rule
def _known_prefix(pcm: np.ndarray, asof_us) -> np.ndarray:
    """Index of the last element of the known prefix at each asof (-1 if none): pcm[i] <= asof. `pcm` is non-decreasing."""
    return np.searchsorted(pcm, asof_us, side="right") - 1


def _visible(pub_us: np.ndarray, asof_us) -> np.ndarray:
    """Row-wise visibility: published at or before the decision time."""
    return pub_us <= asof_us


# ------------------------------------------------------------------------------------------------ arrays
@dataclass
class Series:
    """A value series on a regular grid with the publish time of each value."""
    t0_us: int
    step_us: int
    val: np.ndarray                    # float64, NaN = missing
    pub: np.ndarray                    # int64 us, NEG = never published
    start_us: Optional[np.ndarray] = None   # int64 us start of each slot (daily series with irregular local days)
    pcm: np.ndarray = field(init=False)
    lastvalid: np.ndarray = field(init=False)

    def __post_init__(self):
        self.pcm = np.maximum.accumulate(self.pub) if len(self.pub) else self.pub
        idx = np.where(np.isnan(self.val), -1, np.arange(len(self.val)))
        self.lastvalid = np.maximum.accumulate(idx) if len(idx) else idx.astype(np.int64)

    def at(self, k: np.ndarray):
        """For prefix ends k (-1 = none): (index of the last valid value, True where there is one)."""
        kk = np.where(k >= 0, self.lastvalid[np.maximum(k, 0)], -1)
        return kk, kk >= 0


def _take(arr: np.ndarray, idx: np.ndarray, ok: np.ndarray, fill=np.nan) -> np.ndarray:
    out = np.full(idx.shape, fill, dtype=np.float64)
    if arr.size and ok.any():
        out[ok] = arr[idx[ok]]
    return out


def _to_us(s: pd.Series) -> np.ndarray:
    return s.dt.tz_convert("UTC").dt.as_unit("us").astype("int64").to_numpy()


def _grid_series(df: pd.DataFrame, t0_us: int, step_us: int, n: int, value: str, *, latest: bool = False) -> Series:
    """Put rows (interval_start_utc, published_utc, value[, prio]) on a grid. One value per slot: lowest `prio` first
    (0 = REAL, 1 = ASSUMPTION), then the EARLIEST published (what was known soonest) or, with latest=True, the LATEST."""
    val = np.full(n, np.nan)
    pub = np.full(n, NEG, dtype=np.int64)
    if len(df):
        ts = _to_us(df["interval_start_utc"])
        p = _to_us(df["published_utc"])
        prio = df["prio"].to_numpy() if "prio" in df.columns else np.zeros(len(df), dtype=np.int64)
        rel = ts - t0_us
        keep = (rel >= 0) & (rel % step_us == 0) & ((rel // step_us) < n)
        idx, p, prio, v = (rel[keep] // step_us), p[keep], prio[keep], df[value].to_numpy(dtype=np.float64)[keep]
        order = np.lexsort(((-p if latest else p), prio, idx))
        idx, p, v = idx[order], p[order], v[order]
        first = np.ones(len(idx), dtype=bool)
        first[1:] = idx[1:] != idx[:-1]
        val[idx[first]], pub[idx[first]] = v[first], p[first]
    return Series(t0_us, step_us, val, pub)


@dataclass
class AsMatrix:
    """Day-ahead ancillary prices of one product by LOCAL operating date (rows) and LOCAL hour of day (columns)."""
    val: np.ndarray          # (dates, 24) price, NaN where the hour is absent (the skipped spring hour, a missing row)
    pub: np.ndarray          # (dates, 24) publish time of that row, NEG where there is none
    pcm: np.ndarray          # (dates,) running maximum of each non-empty date's earliest publish time (the prefix rule, as for RT prices)
    lastrow: np.ndarray      # (dates,) index of the newest date with any row at or before row d (-1: none); trailing and gap dates are skipped
    first_date: int          # local date ordinal (days since 1970-01-01) of row 0


def _as_matrix(s: Series, h0_us: int, nh: int) -> AsMatrix:
    """Re-index an hourly UTC series by local (date, hour of day). The repeated hour of a fall-back day keeps its FIRST pass (as the
    interim builder does). A day without rows hides nothing and is never "the newest date" (`lastrow`), but it still counts as a date in
    the window of 14, so the window is calendar-based: tomorrow's results not being in the lake yet must not shift it."""
    t = pd.to_datetime(h0_us + np.arange(nh, dtype=np.int64) * HOUR_US, unit="us", utc=True).tz_convert(CPT)
    ldate = t.tz_localize(None).normalize().to_numpy().astype("datetime64[D]").astype(np.int64)
    lhour = t.hour.to_numpy().astype(np.int64)
    first_date = int(ldate.min())
    row = ldate - first_date
    _, first = np.unique(row * 24 + lhour, return_index=True)
    val = np.full((int(row.max()) + 1, 24), np.nan)
    pub = np.full(val.shape, NEG, dtype=np.int64)
    val[row[first], lhour[first]] = s.val[first]
    pub[row[first], lhour[first]] = s.pub[first]
    has = ~np.isnan(val)
    earliest = np.where(has, pub, np.iinfo(np.int64).max).min(axis=1)
    nonempty = has.any(axis=1)
    pcm = np.maximum.accumulate(np.where(nonempty, earliest, NEG))
    lastrow = np.maximum.accumulate(np.where(nonempty, np.arange(len(nonempty)), -1))
    return AsMatrix(val, pub, pcm, lastrow, first_date)


# ------------------------------------------------------------------------------------------------ reading parsed data
_read = read_window                       # the readers live in data/reader.py (shared with the realised-price source)
_read_prices = read_prices


# ------------------------------------------------------------------------------------------------ context
@dataclass
class Context:
    sp: str
    zone: str
    t0_us: int
    n: int
    rt: Series
    rt_label: np.ndarray
    resid: np.ndarray                  # price minus its 7-day same-time-of-day median (the anchor), on the 15-minute grid
    hb: Series
    h0_us: int
    nh: int
    dam: Series
    das: dict
    as_mat: dict                       # product -> AsMatrix: the day-ahead ancillary prices by local operating date and local hour
    lf_pub: np.ndarray
    lf_first: np.ndarray
    lf_sys: np.ndarray
    lf_zone: np.ndarray
    gas: Series
    wx: dict
    wx_pcm: np.ndarray
    wx_start: np.ndarray
    wx_last: np.ndarray
    cal: dict


def _floor(x_us: int, step: int) -> int:
    return (x_us // step) * step


def load_context(sp: str, lo: pd.Timestamp, hi: pd.Timestamp) -> Context:
    """Everything needed for decisions whose history reaches back to `lo` and whose targets reach `hi` (both tz-aware)."""
    lo, hi = utc(lo), utc(hi)
    t0 = _floor(lo.value // 1000, STEP_US)
    n = int((hi.value // 1000 - t0) // STEP_US) + 2
    h0 = _floor(t0, HOUR_US)
    # the hourly grid reaches 2 days past the last target: the 14-date median of the day-ahead prices needs the rows of the newest
    # operating date, which at a decision after the daily publication is TOMORROW, up to 47 hours after the decision
    nh = int((t0 + n * STEP_US - h0) // HOUR_US) + 2 + 48
    lo_ts = pd.Timestamp(t0, unit="us", tz="UTC")
    hi_ts = pd.Timestamp(t0 + n * STEP_US, unit="us", tz="UTC")

    # --- RT prices of the settlement point and of HB_BUSAVG
    rt_df = _read_prices("rt_spp", "rt_spp_hist", sp, lo_ts, hi_ts, has_type=True)
    if rt_df.empty:
        raise FeatureDataError(f"no RT SPP rows for settlement point {sp!r} between {lo_ts} and {hi_ts}. Check the name "
                               f"(e.g. HB_NORTH, LZ_HOUSTON, a resource node) and that data.flows.parse_all() has run.")
    rt = _grid_series(rt_df, t0, STEP_US, n, "price_usd_per_mwh")
    rt_label = _grid_series(rt_df, t0, STEP_US, n, "price_usd_per_mwh", latest=True).val
    hb_df = rt_df if sp == sets.HB_REF else _read_prices("rt_spp", "rt_spp_hist", sets.HB_REF, lo_ts, hi_ts, has_type=True)
    hb = rt if sp == sets.HB_REF else _grid_series(hb_df, t0, STEP_US, n, "price_usd_per_mwh")

    resid = rt.val - W.anchor_grid(rt.val, days=7, step=sets.LAG_1D)

    # --- DAM prices and ancillary prices (hourly)
    dam_df = _read_prices("dam_spp", "dam_spp_hist", sp, lo_ts, hi_ts + pd.Timedelta(days=2), has_type=False)
    dam = _grid_series(dam_df, h0, HOUR_US, nh, "price_usd_per_mwh")
    das = {}
    as_lo = lo_ts - pd.Timedelta(days=sets.AS_MED_DATES)               # the 14-date median reaches 13 dates behind the newest date, which can be stale
    as_all = read_as("dam_as", "dam_as_hist", as_lo, hi_ts + pd.Timedelta(days=2))
    h0_as = _floor(as_lo.value // 1000, HOUR_US)
    nh_as = int((t0 + n * STEP_US - h0_as) // HOUR_US) + 2 + 48
    as_mat = {}
    for prod in sets.DAM_AS_PRODUCTS:
        rows = as_all[as_all["product"] == prod] if len(as_all) else as_all
        das[prod] = _grid_series(rows, h0, HOUR_US, nh, "mcpc_usd_per_mw_h")
        if prod in sets.AS_REL_PRODUCTS:
            as_mat[prod] = _as_matrix(_grid_series(rows, h0_as, HOUR_US, nh_as, "mcpc_usd_per_mw_h"), h0_as, nh_as)

    zone = wxm.load_zone_of(sp, _sp_map())
    # --- load forecast: per issue, the in-use model's hourly values
    lf = _read("load_forecast", lo_ts, hi_ts + pd.Timedelta(days=9),
               ["interval_start_utc", "published_utc", "in_use", "system_total_mw"] + sorted(
                   {c for z in wxm.LZ_WEATHER_ZONES.values() for c in z} - {"system_total_mw"}), pads.field("in_use") == True)  # noqa: E712
    lf_pub, lf_first, lf_sys, lf_zone = _lf_arrays(lf, h0, wxm.LZ_WEATHER_ZONES.get(zone, ("system_total_mw",)))

    # --- gas and weather (daily, local calendar days)
    day0 = (lo_ts.tz_convert(CPT).tz_localize(None).normalize() - pd.Timedelta(days=45))
    nd = int((hi_ts.tz_convert(CPT).tz_localize(None).normalize() - day0).days) + 3
    gas = _gas_series(day0, nd, lo_ts - pd.Timedelta(days=60), hi_ts)
    wx, wx_pcm, wx_start, wx_last = _weather_series(zone, day0, nd, lo_ts - pd.Timedelta(days=60), hi_ts)

    # --- calendar of every grid slot (local Central time)
    grid = pd.to_datetime(t0 + np.arange(n, dtype=np.int64) * STEP_US, unit="us", utc=True)
    local = grid.tz_convert(CPT)
    cal = {"hour_local": local.hour.to_numpy(), "qtr_hour": (local.minute // 15).to_numpy(), "dow": local.dayofweek.to_numpy(),
           "month": local.month.to_numpy(),
           "is_holiday": sets.is_holiday(local.tz_localize(None).normalize().to_numpy().astype("datetime64[D]")).astype(np.int8)}
    cal["is_weekend"] = (cal["dow"] >= 5).astype(np.int8)
    ang = 2 * np.pi * (local.dayofyear.to_numpy() - 1) / 365.25                  # day of year of the slot's LOCAL date
    cal["doy_sin"], cal["doy_cos"] = np.sin(ang), np.cos(ang)
    return Context(sp, zone, t0, n, rt, rt_label, resid, hb, h0, nh, dam, das, as_mat, lf_pub, lf_first, lf_sys, lf_zone,
                   gas, wx, wx_pcm, wx_start, wx_last, cal)


_SP_MAP_CACHE: dict = {}


def _sp_map() -> Optional[pd.DataFrame]:
    files = parse.partition_files("sp_map")
    if not files:
        return None
    key = tuple(str(p) for p in files)
    if key not in _SP_MAP_CACHE:
        _SP_MAP_CACHE.clear()
        _SP_MAP_CACHE[key] = parse.read("sp_map", columns=["published_utc", "settlement_point", "kind", "load_zone"])
    return _SP_MAP_CACHE[key]


def _lf_arrays(lf: pd.DataFrame, h0_us: int, zone_cols: tuple):
    """Load forecast issues -> (issue publish times, first target hour index, system MW matrix, zone MW matrix)."""
    if lf is None or lf.empty:
        return (np.zeros(0, dtype=np.int64), np.zeros(0, dtype=np.int64), np.zeros((0, 1)), np.zeros((0, 1)))
    lf = lf.sort_values(["published_utc", "interval_start_utc"], kind="stable")
    zone_mw = lf[list(zone_cols)].sum(axis=1, min_count=len(zone_cols)).to_numpy()
    pubs = _to_us(lf["published_utc"])
    hour = (_to_us(lf["interval_start_utc"]) - h0_us) // HOUR_US
    uniq, start = np.unique(pubs, return_index=True)
    ends = np.r_[start[1:], len(pubs)]
    first = np.array([hour[s:e].min() for s, e in zip(start, ends)], dtype=np.int64)
    width = int(min(sets.LF_MAX_HOURS, max(hour[s:e].max() - hour[s:e].min() + 1 for s, e in zip(start, ends))))
    sysm = np.full((len(uniq), width), np.nan)
    zonm = np.full((len(uniq), width), np.nan)
    sys_v = lf["system_total_mw"].to_numpy(dtype=np.float64)
    for i, (s, e) in enumerate(zip(start, ends)):
        col = hour[s:e] - first[i]
        ok = (col >= 0) & (col < width)
        sysm[i, col[ok]] = sys_v[s:e][ok]
        zonm[i, col[ok]] = zone_mw[s:e][ok]
    return uniq, first, sysm, zonm


def _gas_series(day0: pd.Timestamp, nd: int, lo: pd.Timestamp, hi: pd.Timestamp) -> Series:
    d = _read("gas_hh", lo, hi, ["interval_start_utc", "published_utc", "price_usd_per_mmbtu"])
    starts = _local_day_starts(day0, nd)
    val = np.full(nd, np.nan)
    pub = np.full(nd, NEG, dtype=np.int64)
    if len(d):
        ts = _to_us(d["interval_start_utc"])
        p = _to_us(d["published_utc"])
        v = d["price_usd_per_mmbtu"].to_numpy(dtype=np.float64)
        order = np.lexsort((p, ts))                       # earliest published first per day
        ts, p, v = ts[order], p[order], v[order]
        pos = np.searchsorted(starts, ts)
        ok = (pos < nd) & (starts[np.minimum(pos, nd - 1)] == ts)
        seen = set()
        for i in np.flatnonzero(ok):
            if pos[i] not in seen:
                seen.add(pos[i])
                val[pos[i]], pub[pos[i]] = v[i], p[i]
    s = Series(int(starts[0]), DAY_US, val, pub, start_us=starts)
    return s


def _local_day_starts(day0: pd.Timestamp, nd: int) -> np.ndarray:
    days = pd.date_range(day0, periods=nd, freq="D")
    return days.tz_localize(CPT, ambiguous=False, nonexistent="shift_forward").tz_convert("UTC").as_unit("us").asi8.copy()


def _weather_series(zone: str, day0: pd.Timestamp, nd: int, lo: pd.Timestamp, hi: pd.Timestamp):
    """Daily zone aggregate of the latest observations: (dict name -> array, running-max publish time, day start us, last valid)."""
    stations = wxm.stations_for(zone)
    d = _read("weather_obs", lo, hi, ["interval_start_utc", "published_utc", "station", "tmax_f", "tmin_f", "prcp_in", "wsf2_mph"],
              pads.field("station").isin(list(stations)))
    starts = _local_day_starts(day0, nd)
    tmax = np.full((len(stations), nd), np.nan)
    tmin, prcp, wsf = tmax.copy(), tmax.copy(), tmax.copy()
    pub = np.full((len(stations), nd), NEG, dtype=np.int64)
    w = np.array(list(stations.values()), dtype=np.float64)
    if len(d):
        d = d.sort_values(["published_utc", "station"], kind="stable").drop_duplicates(["station", "interval_start_utc"], keep="first")
        ts = _to_us(d["interval_start_utc"])
        pos = np.searchsorted(starts, ts)
        ok = (pos < nd) & (starts[np.minimum(pos, nd - 1)] == ts)
        names = list(stations)
        for row, st in enumerate(names):
            m = ok & (d["station"].to_numpy() == st)
            tmax[row, pos[m]] = d["tmax_f"].to_numpy(dtype=np.float64)[m]
            tmin[row, pos[m]] = d["tmin_f"].to_numpy(dtype=np.float64)[m]
            prcp[row, pos[m]] = d["prcp_in"].to_numpy(dtype=np.float64)[m]
            wsf[row, pos[m]] = d["wsf2_mph"].to_numpy(dtype=np.float64)[m]
            pub[row, pos[m]] = _to_us(d["published_utc"])[m]
    has = ~np.isnan(tmax)                                                   # a station counts for the day only if it has TMAX

    def wavg(x):                                                            # weighted mean over the stations that have a value
        ok = ~np.isnan(x) & has
        ww = np.where(ok, w[:, None], 0.0)
        den = ww.sum(axis=0)
        with np.errstate(invalid="ignore", divide="ignore"):
            return np.where(den > 0, (np.where(ok, x, 0.0) * ww).sum(axis=0) / den, np.nan)
    agg = {"wx_tmax_f": wavg(tmax), "wx_tmin_f": wavg(tmin)}
    agg["wx_prcp_in"] = np.where(has.any(axis=0), np.nanmax(np.where(np.isnan(prcp), -1e9, prcp), axis=0), np.nan)
    agg["wx_prcp_in"] = np.where(agg["wx_prcp_in"] < -1e8, np.nan, agg["wx_prcp_in"])
    agg["wx_wsf2_mph"] = np.where(has.any(axis=0), np.nanmax(np.where(np.isnan(wsf), -1e9, wsf), axis=0), np.nan)
    agg["wx_wsf2_mph"] = np.where(agg["wx_wsf2_mph"] < -1e8, np.nan, agg["wx_wsf2_mph"])
    day_pub = np.where(has, pub, NEG).max(axis=0)                           # the aggregate exists when its slowest station has published
    pcm = np.maximum.accumulate(day_pub)
    lastvalid = np.maximum.accumulate(np.where(np.isnan(agg["wx_tmax_f"]), -1, np.arange(nd)))
    agg["_pub"] = day_pub
    return agg, pcm, starts, lastvalid


# ------------------------------------------------------------------------------------------------ the computation
def _floor_step(asof_us: np.ndarray) -> np.ndarray:
    """Start of the 15-minute interval that contains each decision time (horizon 1 is that interval: the platform
    convention shared with D1a's `horizon_steps` and D2's guard, which accepts the current or the next interval)."""
    return (asof_us // STEP_US) * STEP_US


def _decision_stats(P: np.ndarray, R: np.ndarray) -> dict:
    """The decision-level price columns, each a (D,) array, from the 672 slots ending at the newest known interval (`P`, prices; `R`,
    residuals; oldest first, NaN where unknown). A window needs at least half of its slots (w/2); NaN slots are skipped."""
    d: dict[str, np.ndarray] = {}
    P96, P16, P4 = W.tail(P, 96), W.tail(P, 16), W.tail(P, 4)
    d["rt_mean_1h"] = W.mean_of(P4, 2)
    d["rt_mean_16"] = W.mean_of(P16, 8)
    d["rt_mean_24h"] = W.mean_of(P96, 48)
    d["rt_std_24h"] = W.std_of(P96, 48)
    d["rt_mean_7d"] = W.mean_of(P, 336)
    d["rt_std_7d"] = W.std_of(P, 336)
    s96, n96 = W.sort_valid(P96)
    s672, n672 = W.sort_valid(P)
    d["rt_min_96"] = W.minimum(s96, n96, 48)
    d["rt_max_96"] = W.maximum(s96, n96, 48)
    d["rt_spread_96"] = W.spread(s96, n96, 48)
    for name, q in (("q10", 0.1), ("q50", 0.5), ("q90", 0.9)):
        d[f"rt_{name}_672"] = W.quantile(s672, n672, q, 336)
    hi, neg = (lambda v: v >= sets.SPIKE_HI_USD), (lambda v: v < 0.0)
    d["rt_frac_hi_96"], d["rt_frac_hi_672"] = W.share(P96, hi, 48), W.share(P, hi, 336)
    d["rt_frac_neg_96"], d["rt_frac_neg_672"] = W.share(P96, neg, 48), W.share(P, neg, 336)
    R96, R16, R4 = W.tail(R, 96), W.tail(R, 16), W.tail(R, 4)
    d["res_last"] = R[:, -1].copy()
    d["res_mean_4"] = W.mean_of(R4, 2)
    d["res_mean_16"] = W.mean_of(R16, 8)
    d["res_mean_96"] = W.mean_of(R96, 48)
    rs96, rn96 = W.sort_valid(R96)
    rs672, rn672 = W.sort_valid(R)
    for name, q in (("q10", 0.1), ("q50", 0.5), ("q90", 0.9)):
        d[f"res_{name}_96"] = W.quantile(rs96, rn96, q, 48)
        d[f"res_{name}_672"] = W.quantile(rs672, rn672, q, 336)
    d["res_mad_672"] = W.mean_abs(R, 336)
    return d


def _compute(ctx: Context, asof_us: np.ndarray, horizon: int = sets.HORIZON) -> dict:
    """Features for D decision times x `horizon` target intervals. Returns arrays shaped (D, H) in `out`, plus
    '_max_pub' (int64 us, NEG = nothing used), '_interval_us' (target starts) and '_y'.

    Every comparison of a publish time with a decision time goes through `_known_prefix` (series that arrive in order) or `_visible`
    (rows looked up one by one); the helpers in windows.py never see a time."""
    D, H = len(asof_us), horizon
    a = asof_us[:, None]
    j1 = (_floor_step(asof_us) - ctx.t0_us) // STEP_US
    j = j1[:, None] + np.arange(H)[None, :]
    if (j < 0).any() or (j >= ctx.n).any():
        raise FeatureDataError("decision/target times fall outside the loaded context window")
    interval_us = ctx.t0_us + j * STEP_US
    out: dict[str, np.ndarray] = {}
    max_pub = np.full((D, H), NEG, dtype=np.int64)

    def per_decision(x) -> np.ndarray:                                    # (D,) -> (D, H)
        return np.repeat(np.asarray(x)[:, None], H, axis=1)

    # --- RT (own): the newest known interval kv, the 672 slots behind it, and the lags / profiles of each target
    k = _known_prefix(ctx.rt.pcm, asof_us)
    kv, okv = ctx.rt.at(k)
    out["rt_last"] = per_decision(_take(ctx.rt.val, kv, okv))
    age = np.where(okv, (asof_us - (ctx.t0_us + (kv + 1) * STEP_US)) / 60e6, np.nan)
    out["rt_last_age_min"] = per_decision(age)
    stats = _decision_stats(W.window_ending(ctx.rt.val, kv, 672, okv), W.window_ending(ctx.resid, kv, 672, okv))
    for name, v in stats.items():
        out[name] = per_decision(v)
    m14 = np.arange(1, 15, dtype=np.int64)
    slot = j[:, :, None] - sets.LAG_1D * m14[None, None, :]               # (D, H, 14): the same time of day 1..14 days earlier
    known = (slot >= 0) & (slot <= k[:, None, None])                       # inside the known prefix
    slot_c = np.clip(slot, 0, ctx.n - 1)
    V = np.where(known, ctx.rt.val[slot_c], np.nan)
    for name, d in (("rt_lag_1d", 1), ("rt_lag_2d", 2), ("rt_lag_3d", 3), ("rt_lag_7d", 7), ("rt_lag_14d", 14)):
        out[name] = V[:, :, d - 1]
    V7 = V[:, :, :7]
    s7, n7 = W.sort_valid(V7)
    out["rt_prof7_mean"], out["rt_prof7_med"] = W.mean_of(V7), W.median(s7, n7)
    out["rt_prof7_min"], out["rt_prof7_max"], out["rt_prof7_std"] = W.minimum(s7, n7), W.maximum(s7, n7), W.std_of(V7)
    same = ~np.isnan(V) & (ctx.cal["is_weekend"][slot_c] == ctx.cal["is_weekend"][j][:, :, None])    # days of the target's weekday/weekend type
    out["rt_prof14_dt"] = np.where(same.sum(axis=-1) >= 2, W.mean_of(np.where(same, V, np.nan)), W.mean_of(V))
    max_pub = np.maximum(max_pub, np.where(okv, ctx.rt.pcm[np.maximum(kv, 0)], NEG)[:, None])

    # --- HB_BUSAVG
    kh = _known_prefix(ctx.hb.pcm, asof_us)
    kvh, okh = ctx.hb.at(kh)
    out["hb_last"] = per_decision(_take(ctx.hb.val, kvh, okh))
    for name, back in (("hb_lag_1h", 4), ("hb_lag_24h", 96)):
        idx = kvh - back
        ok = okh & (idx >= 0)
        out[name] = per_decision(_take(ctx.hb.val, np.where(ok, idx, 0), ok))
    out["hb_mean_24h"] = per_decision(W.mean_of(W.window_ending(ctx.hb.val, kvh, 96, okh), 48))
    jl = j - sets.LAG_1D
    ok = (jl >= 0) & (jl <= kh[:, None])
    out["hb_lag_1d"] = _take(ctx.hb.val, np.where(ok, jl, 0), ok)
    out["basis_last"] = out["rt_last"] - out["hb_last"]
    max_pub = np.maximum(max_pub, np.where(okh, ctx.hb.pcm[np.maximum(kvh, 0)], NEG)[:, None])

    # --- DAM and ancillary prices of the target hour
    m = (interval_us - ctx.h0_us) // HOUR_US
    vis = _visible(ctx.dam.pub[m], a)
    out["dam_spp"] = np.where(vis, ctx.dam.val[m], np.nan)
    out["dam_spp_minus_rt_last"] = out["dam_spp"] - out["rt_last"]
    max_pub = np.maximum(max_pub, np.where(vis & ~np.isnan(out["dam_spp"]), ctx.dam.pub[m], NEG))
    for prod in sets.DAM_AS_PRODUCTS:
        s = ctx.das[prod]
        v = _visible(s.pub[m], a)
        out[f"dam_as_{prod.lower()}"] = np.where(v, s.val[m], np.nan)
        max_pub = np.maximum(max_pub, np.where(v & ~np.isnan(s.val[m]), s.pub[m], NEG))

    # --- the day-ahead ancillary price at the target's local hour of day over the 14 newest operating dates with published results
    hh = ctx.cal["hour_local"][j]
    for prod in sets.AS_REL_PRODUCTS:
        am = ctx.as_mat[prod]
        cand = _known_prefix(am.pcm, asof_us)                              # last date whose publish time (and every earlier one's) is <= asof ...
        newest = np.where(cand >= 0, am.lastrow[np.maximum(cand, 0)], -1)  # ... and the newest of those that has results at all (-1: none)
        rows = newest[:, None] - np.arange(sets.AS_MED_DATES - 1, -1, -1, dtype=np.int64)[None, :]          # (D, 14), oldest first
        ok_row = (rows >= 0) & (newest[:, None] >= 0)
        rows_c = np.clip(rows, 0, am.val.shape[0] - 1)
        vals = am.val[rows_c[:, None, :], hh[:, :, None]]                  # (D, H, 14)
        pubs = am.pub[rows_c[:, None, :], hh[:, :, None]]
        used = ok_row[:, None, :] & _visible(pubs, a[:, :, None]) & ~np.isnan(vals)
        sv, nv = W.sort_valid(np.where(used, vals, np.nan))
        med = W.median(sv, nv)
        low = prod.lower()
        out[f"dam_as_{low}_med14"] = med
        out[f"dam_as_{low}_rel"] = out[f"dam_as_{low}"] / np.maximum(med, sets.AS_REL_FLOOR)
        max_pub = np.maximum(max_pub, np.where(used, pubs, NEG).max(axis=-1))

    # --- calendar of the target
    out["horizon"] = np.broadcast_to(np.arange(1, H + 1)[None, :], (D, H)).copy()
    for name in ("hour_local", "qtr_hour", "dow", "month", "is_holiday", "is_weekend", "doy_sin", "doy_cos"):
        out[name] = ctx.cal[name][j]

    # --- gas
    kg = _known_prefix(ctx.gas.pcm, asof_us)
    kvg, okg = ctx.gas.at(kg)
    out["gas_hh"] = per_decision(_take(ctx.gas.val, kvg, okg))
    gage = np.where(okg, (asof_us - ctx.gas.start_us[np.maximum(kvg, 0)]) / DAY_US, np.nan)
    out["gas_hh_age_days"] = per_decision(gage)
    max_pub = np.maximum(max_pub, np.where(okg, ctx.gas.pcm[np.maximum(kvg, 0)], NEG)[:, None])

    # --- weather: the latest PUBLISHED observation day (never the day in progress)
    kw = _known_prefix(ctx.wx_pcm, asof_us)
    kvw = np.where(kw >= 0, ctx.wx_last[np.maximum(kw, 0)], -1)
    okw = kvw >= 0
    for name in ("wx_tmax_f", "wx_tmin_f", "wx_prcp_in", "wx_wsf2_mph"):
        out[name] = per_decision(_take(ctx.wx[name], kvw, okw))
    tmean = (out["wx_tmax_f"] + out["wx_tmin_f"]) / 2.0
    out["wx_cdd"] = np.maximum(tmean - 65.0, 0.0)
    out["wx_hdd"] = np.maximum(65.0 - tmean, 0.0)
    day_end = ctx.wx_start[np.minimum(np.maximum(kvw, 0) + 1, len(ctx.wx_start) - 1)]
    out["wx_age_days"] = per_decision(np.where(okw, (asof_us - day_end) / DAY_US, np.nan))
    max_pub = np.maximum(max_pub, np.where(okw, ctx.wx_pcm[np.maximum(kvw, 0)], NEG)[:, None])

    # --- load forecast: the latest issue visible at asof, then the target hour inside it
    if len(ctx.lf_pub):
        ii = _known_prefix(ctx.lf_pub, asof_us)
        oki = ii >= 0
        iic = np.maximum(ii, 0)
        col = m - ctx.lf_first[iic][:, None]
        valid = oki[:, None] & (col >= 0) & (col < ctx.lf_sys.shape[1])
        colc = np.clip(col, 0, ctx.lf_sys.shape[1] - 1)
        out["lf_system_mw"] = np.where(valid, ctx.lf_sys[iic[:, None], colc], np.nan)
        out["lf_zone_mw"] = np.where(valid, ctx.lf_zone[iic[:, None], colc], np.nan)
        out["lf_age_h"] = per_decision(np.where(oki, (asof_us - ctx.lf_pub[iic]) / HOUR_US, np.nan))
        max_pub = np.maximum(max_pub, np.where(valid & ~np.isnan(out["lf_system_mw"]), ctx.lf_pub[iic][:, None], NEG))
    else:
        for name in ("lf_system_mw", "lf_zone_mw", "lf_age_h"):
            out[name] = np.full((D, H), np.nan)

    out["_max_pub"] = max_pub
    out["_interval_us"] = interval_us
    out["_y"] = np.where(j < ctx.n, ctx.rt_label[np.minimum(j, ctx.n - 1)], np.nan)
    return out


def _frame(out: dict, ctx: Context, asof_us: np.ndarray, *, with_label: bool) -> pd.DataFrame:
    """Flatten (D, H) arrays into rows ordered by (decision, horizon) with the declared dtypes."""
    D, H = out["_max_pub"].shape
    cols = {}
    for f in sets.FEATURE_TABLE:
        a = out[f.name].reshape(-1)
        cols[f.name] = a.astype(f.dtype) if f.dtype.startswith("float") else a.astype(f.dtype)
    df = pd.DataFrame(cols)
    df["settlement_point"] = ctx.sp
    df["asof_utc"] = pd.to_datetime(np.repeat(asof_us, H), unit="us", utc=True)
    df["interval_start_utc"] = pd.to_datetime(out["_interval_us"].reshape(-1), unit="us", utc=True)
    mp = out["_max_pub"].reshape(-1)
    df["max_published_utc"] = pd.to_datetime(mp, unit="us", utc=True)           # NEG is the int64 minimum = NaT
    if with_label:
        df[sets.LABEL] = out["_y"].reshape(-1)
    return df


# ------------------------------------------------------------------------------------------------ public API
def point_in_time(sp: str, asof_utc, horizon: int = sets.HORIZON, *, context: Optional[Context] = None) -> pd.DataFrame:
    """Features for the next `horizon` 15-minute intervals at decision time `asof_utc`, using only data published by then.

    Returns one row per target interval, indexed by `interval_start_utc` (UTC, 15-minute grid). Horizon 1 is the interval
    CONTAINING the decision time (floor_15min(asof_utc)), horizon 2 the next, ... horizon 96 starts 23.75 h later. Columns: the model inputs `sets.FEATURES`, then `settlement_point`, `asof_utc`, `max_published_utc`.
    Raises FeatureDataError if the settlement point has no RT price known at asof (the platform fails closed on that)."""
    if not 1 <= horizon <= sets.HORIZON:
        raise ValueError(f"horizon must be 1..{sets.HORIZON}")
    asof = utc(asof_utc)
    asof_us = np.array([asof.value // 1000], dtype=np.int64)
    ctx = context or load_context(sp, asof - pd.Timedelta(days=sets.LOOKBACK_DAYS), asof + pd.Timedelta(minutes=15 * (horizon + 1), hours=1))
    out = _compute(ctx, asof_us, horizon)
    df = _frame(out, ctx, asof_us, with_label=False)
    if df["rt_last"].isna().all():
        raise FeatureDataError(f"no RT SPP for {sp!r} is published by {asof}: the newest known interval is missing or older than the "
                               f"loaded window. Features cannot be built (fail closed).")
    return df.set_index("interval_start_utc")


def decision_times(start_utc, end_utc, decision_every: str = "4h", offset: str = "0min") -> pd.DatetimeIndex:
    """Decision times in [start, end): multiples of `decision_every` counted from the Unix epoch (UTC), plus `offset`, so the grid
    is the same whatever `start` is. Deterministic. The default grid sits on interval boundaries, where the newest known price
    is always 15 minutes old; an offset such as "7min30s" trains on other ages, closer to a service that decides at any second."""
    step, off = pd.Timedelta(decision_every), pd.Timedelta(offset)
    if step < pd.Timedelta(minutes=1) or not pd.Timedelta(0) <= off < step:
        raise ValueError("decision_every must be at least a minute and 0 <= offset < decision_every")
    s, e = utc(start_utc), utc(end_utc)
    first = (s - off).ceil(step) + off
    return pd.date_range(first, e, freq=step, inclusive="left") if first < e else pd.DatetimeIndex([], tz="UTC")


def training_frame(sp: str, start_utc, end_utc, decision_every: str = "4h", *, horizon: int = sets.HORIZON,
                   context: Optional[Context] = None, offset: str = "0min") -> tuple[pd.DataFrame, pd.Series, pd.Series]:
    """Training rows for decisions in [start_utc, end_utc) every `decision_every`, horizons 1..`horizon`.

    Returns (X, y, decision_utc):
      X  RangeIndex rows ordered by (decision, horizon); columns `sets.FEATURES` (model inputs), then settlement_point, asof_utc,
         interval_start_utc, max_published_utc. Rows with no known RT price at the decision, or no realised target price, are dropped.
      y  the realised RT SPP of the target interval, $/MWh (float64), same index. It is the label, never a feature.
      decision_utc  the decision time of each row (equals X['asof_utc']); group walk-forward folds by it.
    Built by the same routine as `point_in_time`, so a model sees the same features in training and at the service."""
    dts = decision_times(start_utc, end_utc, decision_every, offset)
    if len(dts) == 0:
        raise ValueError("no decision times in [start_utc, end_utc)")
    ctx = context or load_context(sp, dts[0] - pd.Timedelta(days=sets.LOOKBACK_DAYS), dts[-1] + pd.Timedelta(minutes=15 * (horizon + 1), hours=1))
    asof_all = dts.as_unit("us").asi8.astype(np.int64)
    parts = []
    for i in range(0, len(asof_all), CHUNK):
        a = asof_all[i:i + CHUNK]
        out = _compute(ctx, a, horizon)
        df = _frame(out, ctx, a, with_label=True)
        keep = df["rt_last"].notna() & df[sets.LABEL].notna()
        parts.append(df[keep])
    df = pd.concat(parts, ignore_index=True) if parts else _frame(_compute(ctx, asof_all[:1], horizon), ctx, asof_all[:1], with_label=True).iloc[0:0]
    y = df.pop(sets.LABEL).astype("float64")
    X = df
    return X, y, X["asof_utc"].copy()


# ------------------------------------------------------------------------------------------------ offline store
def store_dir(version: str = sets.VERSION) -> Path:
    return paths.FEATURES / sets.FEATURE_SET / version


def sp_dir(sp: str, version: str = sets.VERSION) -> Path:
    return store_dir(version) / f"sp={sp}"


OFFLINE_COLUMNS = ["settlement_point", "asof_utc", "interval_start_utc", "max_published_utc", *sets.FEATURES, sets.LABEL]


def _offline_schema() -> pa.Schema:
    fields = [pa.field("settlement_point", pa.string()), pa.field("asof_utc", TS), pa.field("interval_start_utc", TS),
              pa.field("max_published_utc", TS)]
    for f in sets.FEATURE_TABLE:
        fields.append(pa.field(f.name, {"float32": pa.float32(), "int16": pa.int16(), "int8": pa.int8()}[f.dtype]))
    fields.append(pa.field(sets.LABEL, pa.float64()))
    return pa.schema(fields)


def write_offline(sp: str, start_utc, end_utc, decision_every: str = "4h", *, version: str = sets.VERSION,
                  offset: str = "0min", log=lambda s: None) -> list[Path]:
    """Build and store the offline features of one settlement point for decisions in [start_utc, end_utc).

    One parquet file per calendar month of decision time (UTC): features/rt_v1/<version>/sp=<sp>/part-YYYY-MM.parquet.
    `offset` shifts the decision grid (see decision_times). Rebuilding from the same raw lake gives byte-identical files
    (Gate 1). Existing files for the months are replaced."""
    dts = decision_times(start_utc, end_utc, decision_every, offset)
    if len(dts) == 0:
        return []
    ctx = load_context(sp, dts[0] - pd.Timedelta(days=sets.LOOKBACK_DAYS), dts[-1] + pd.Timedelta(minutes=15 * (sets.HORIZON + 1), hours=1))
    schema = _offline_schema()
    meta = {"dispatch.feature_set": sets.FEATURE_SET, "dispatch.version": version, "dispatch.schema_hash": sets.schema_hash(),
            "dispatch.sp": sp, "dispatch.decision_every": decision_every, "dispatch.offset": offset}
    written = []
    months = dts.strftime("%Y-%m")
    for mk in pd.unique(months):
        asof_all = dts[months == mk].as_unit("us").asi8.astype(np.int64)
        parts = []
        for i in range(0, len(asof_all), CHUNK):
            a = asof_all[i:i + CHUNK]
            df = _frame(_compute(ctx, a), ctx, a, with_label=True)
            parts.append(df[df["rt_last"].notna() & df[sets.LABEL].notna()])
        df = pd.concat(parts, ignore_index=True)
        if df.empty:
            continue
        df = df[OFFLINE_COLUMNS]
        table = pa.Table.from_pandas(df, schema=schema, preserve_index=False, safe=False).replace_schema_metadata(
            {**meta, "dispatch.month": mk, "dispatch.rows": str(len(df))})
        path = sp_dir(sp, version) / f"part-{mk}.parquet"
        parse.write_table(path, table)
        written.append(path)
        log(f"  features {sp} {mk}: {len(df):,} rows")
    return written


def offline_files(sp: str, version: str = sets.VERSION) -> list[Path]:
    d = sp_dir(sp, version)
    return sorted(d.glob("part-*.parquet")) if d.exists() else []


def read_offline(sp: str, start_utc=None, end_utc=None, version: str = sets.VERSION) -> tuple[pd.DataFrame, pd.Series, pd.Series]:
    """The offline store as (X, y, decision_utc), optionally limited to decisions in [start_utc, end_utc)."""
    files = offline_files(sp, version)
    if not files:
        raise FeatureDataError(f"no offline features for {sp!r} under {sp_dir(sp, version)}; run write_offline first")
    df = pd.concat([pq.read_table(p).to_pandas() for p in files], ignore_index=True)     # one file at a time: no hive 'sp' column
    if start_utc is not None:
        df = df[df["asof_utc"] >= utc(start_utc)]
    if end_utc is not None:
        df = df[df["asof_utc"] < utc(end_utc)]
    df = df.reset_index(drop=True)
    y = df.pop(sets.LABEL).astype("float64")
    return df, y, df["asof_utc"].copy()
