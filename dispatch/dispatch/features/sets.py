"""Versioned feature set `rt_v1`: forecasting the real-time settlement point price (RT SPP) at one settlement point.

One training row is a (decision time `asof_utc`, target interval) pair, horizon h = 1..96 fifteen-minute intervals ahead:
target interval h starts at floor_15min(asof_utc) + (h - 1) * 15 min
(h = 1 is the interval that contains the decision, as in D1a's `horizon_steps` and D2's guard). Every feature is computed from rows with
`published_utc <= asof_utc` only (DESIGN.md, Point in time) and every row carries `max_published_utc`, the newest publish time
of anything that fed it. The leakage test (tests/test_leakage.py) fails the build if that ever exceeds `asof_utc`.

Feature groups (all optional and NaN-tolerant: a missing input gives NaN, never a guess):
  rt    the settlement point's own RT SPP: latest known, its age, rolling means/stds, same interval yesterday / last week
  hb    HB_BUSAVG (the system price) lags, and the basis to it
  dam   DAM SPP for the target hour, and DAM ancillary MCPCs, only if published by asof
  cal   calendar of the target interval (local Central time) and the horizon
  gas   Henry Hub, as published (a publish lag applies, see data/assumptions.py)
  wx    yesterday's observations (the latest published day, never the same day) at the zone's weather stations
  lf    7-day load forecast for the target hour (latest issue <= asof, the model ERCOT marks in use); history is short

CHANGES
  1.1.0  + the 41 columns D1a's models need (notes/d1a.md section 10): windows of the last 4 to 672 known intervals (min, max, spread, quantiles,
           shares above 100 and below 0), the residual of the price against its own 7-day same-time-of-day median (the "anchor") and its
           window statistics, lags of 2, 3 and 14 days, the 7-day and 14-day same-time-of-day profiles, day of year, and the 14-day median
           and the relative level of the day-ahead ancillary prices. Same names and meaning as in that spec, so a model trained on
           D1a's interim builder reads these columns after a rename (features/compat.py).
         ~ rt_std_24h and rt_std_7d are POPULATION standard deviations (ddof 0, as the interim builder and the trained models have them);
           1.0.0 had the sample deviation. is_holiday marks the OBSERVED federal holiday only (the Friday or Monday when the date falls on
           a weekend), as pandas' USFederalHolidayCalendar does; 1.0.0 also marked the weekend date itself.
         ~ LOOKBACK_DAYS 8 -> 15 (the anchor and the 14-day columns need it). Existing columns keep their names and dtypes.
  1.0.0  the first 41 columns.

Settlement points: any point with RT SPP rows in the lake works. Hubs and load zones have history from 2011 (NP6-785-ER);
resource nodes and other nodal points have only the MIS window (7 days) plus whatever `collect` has added since.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import date, timedelta

import numpy as np

FEATURE_SET = "rt_v1"
VERSION = "1.1.0"             # 1.1.0 adds the 41 columns of notes/d1a.md section 10 and fixes two definitions (see CHANGES)
HORIZON = 96                  # REAL: 24 h of 15-minute settlement intervals
STEP_MIN = 15
HB_REF = "HB_BUSAVG"          # the system reference price (REAL: ERCOT bus-average hub)
LAG_1D = 96                   # intervals in 24 h
LAG_7D = 672                  # intervals in 7 d
LOOKBACK_DAYS = 15            # history read before the first decision: the residual windows need the anchor (7 d) behind a 7 d window,
                              # and rt_lag_14d / rt_prof14_dt look 14 d behind the target; 15 d covers 14 d + 1 d margin
LF_MAX_HOURS = 240            # widest target span of one load forecast issue we keep (the report has 192 hours)
DAM_AS_PRODUCTS = ("REGUP", "REGDN", "RRS", "ECRS", "NSPIN")
LABEL = "y_rt_usd_per_mwh"
META_COLUMNS = ("settlement_point", "asof_utc", "max_published_utc")


@dataclass(frozen=True)
class Feature:
    name: str
    dtype: str
    group: str
    description: str
    publish_rule: str


_F = Feature
FEATURE_TABLE = (
    _F("rt_last", "float32", "rt", "latest RT SPP known at asof, $/MWh", "REAL (MIS) or ASSUMPTION lag (archive)"),
    _F("rt_last_age_min", "float32", "rt", "minutes from the end of that interval to asof", "derived"),
    _F("rt_mean_1h", "float32", "rt", "mean of the last 4 known intervals, $/MWh", "as rt_last"),
    _F("rt_mean_24h", "float32", "rt", "mean of the last 96 known intervals, $/MWh (needs >= 48)", "as rt_last"),
    _F("rt_std_24h", "float32", "rt", "population std (ddof 0) of the last 96 known intervals, $/MWh (needs >= 48)", "as rt_last"),
    _F("rt_mean_7d", "float32", "rt", "mean of the last 672 known intervals, $/MWh (needs >= 336)", "as rt_last"),
    _F("rt_std_7d", "float32", "rt", "population std (ddof 0) of the last 672 known intervals, $/MWh (needs >= 336)", "as rt_last"),
    _F("rt_lag_1d", "float32", "rt", "RT SPP at target - 24 h, NaN until it is published", "as rt_last"),
    _F("rt_lag_7d", "float32", "rt", "RT SPP at target - 7 d", "as rt_last"),
    _F("hb_last", "float32", "hb", "latest HB_BUSAVG RT SPP known at asof, $/MWh", "as rt_last"),
    _F("hb_lag_1h", "float32", "hb", "HB_BUSAVG one hour before the latest known interval", "as rt_last"),
    _F("hb_lag_24h", "float32", "hb", "HB_BUSAVG 24 h before the latest known interval", "as rt_last"),
    _F("hb_mean_24h", "float32", "hb", "mean HB_BUSAVG over the last 96 known intervals", "as rt_last"),
    _F("hb_lag_1d", "float32", "hb", "HB_BUSAVG at target - 24 h, NaN until published", "as rt_last"),
    _F("basis_last", "float32", "hb", "rt_last - hb_last, $/MWh", "derived"),
    _F("dam_spp", "float32", "dam", "DAM SPP of the hour containing the target, $/MWh, only if published by asof", "REAL (NP4-190-CD) or ASSUMPTION 14:00 CPT D-1"),
    _F("dam_spp_minus_rt_last", "float32", "dam", "dam_spp - rt_last, $/MWh", "derived"),
    _F("dam_as_regup", "float32", "dam", "DAM REGUP MCPC of the target hour, $/MW-h, only if published", "REAL (NP4-188-CD) or ASSUMPTION 14:00 CPT D-1"),
    _F("dam_as_regdn", "float32", "dam", "DAM REGDN MCPC", "as dam_as_regup"),
    _F("dam_as_rrs", "float32", "dam", "DAM RRS MCPC", "as dam_as_regup"),
    _F("dam_as_ecrs", "float32", "dam", "DAM ECRS MCPC (from 2023)", "as dam_as_regup"),
    _F("dam_as_nspin", "float32", "dam", "DAM NSPIN MCPC", "as dam_as_regup"),
    _F("horizon", "int16", "cal", "target interval number, 1..96", "known"),
    _F("hour_local", "int8", "cal", "hour of day of the target interval start, Central Prevailing Time", "known"),
    _F("qtr_hour", "int8", "cal", "quarter hour within the hour, 0..3", "known"),
    _F("dow", "int8", "cal", "local day of week of the target, Monday = 0", "known"),
    _F("month", "int8", "cal", "local month of the target, 1..12", "known"),
    _F("is_holiday", "int8", "cal", "1 if the local date is an OBSERVED US federal holiday (a Saturday holiday is the Friday, a Sunday holiday the Monday)", "known"),
    _F("is_weekend", "int8", "cal", "1 on Saturday and Sunday", "known"),
    _F("gas_hh", "float32", "gas", "latest Henry Hub spot published by asof, $/MMBtu", "ASSUMPTION lag 7 d"),
    _F("gas_hh_age_days", "float32", "gas", "days from the start of that trade date to asof", "derived"),
    _F("wx_tmax_f", "float32", "wx", "latest published day's TMAX at the zone's stations, degF", "ASSUMPTION: day D public 18:00 CPT D+1"),
    _F("wx_tmin_f", "float32", "wx", "latest published day's TMIN, degF", "as wx_tmax_f"),
    _F("wx_prcp_in", "float32", "wx", "latest published day's precipitation (max over stations), inches", "as wx_tmax_f"),
    _F("wx_wsf2_mph", "float32", "wx", "latest published day's fastest 2-minute wind (max over stations), mph", "as wx_tmax_f"),
    _F("wx_cdd", "float32", "wx", "cooling degree days of that day, base 65 F", "derived"),
    _F("wx_hdd", "float32", "wx", "heating degree days of that day, base 65 F", "derived"),
    _F("wx_age_days", "float32", "wx", "days from the end of that observation day to asof", "derived"),
    _F("lf_system_mw", "float32", "lf", "ERCOT system load forecast for the target hour, latest issue <= asof, in-use model, MW", "REAL (NP3-565-CD issue time)"),
    _F("lf_zone_mw", "float32", "lf", "same, weather zones that overlap the settlement point's load zone, MW", "REAL"),
    _F("lf_age_h", "float32", "lf", "hours from that issue to asof", "derived"),
    # ---- 1.1.0: windows of the last w KNOWN intervals (w slots ending at the newest known price, NaN slots skipped, >= w/2 valid) ----
    _F("rt_mean_16", "float32", "rt", "mean of the last 16 known intervals, $/MWh (needs >= 8)", "as rt_last"),
    _F("rt_min_96", "float32", "rt", "minimum of the last 96 known intervals, $/MWh (needs >= 48)", "as rt_last"),
    _F("rt_max_96", "float32", "rt", "maximum of the last 96 known intervals, $/MWh (needs >= 48)", "as rt_last"),
    _F("rt_spread_96", "float32", "rt", "mean of the 8 highest minus mean of the 8 lowest of the last 96 known intervals, $/MWh (the arena's daily spread)", "as rt_last"),
    _F("rt_q10_672", "float32", "rt", "10th percentile of the last 672 known intervals, $/MWh (linear interpolation, needs >= 336)", "as rt_last"),
    _F("rt_q50_672", "float32", "rt", "median of the last 672 known intervals, $/MWh", "as rt_last"),
    _F("rt_q90_672", "float32", "rt", "90th percentile of the last 672 known intervals, $/MWh", "as rt_last"),
    _F("rt_frac_hi_96", "float32", "rt", "share of the last 96 known intervals at or above $100/MWh", "as rt_last"),
    _F("rt_frac_hi_672", "float32", "rt", "share of the last 672 known intervals at or above $100/MWh", "as rt_last"),
    _F("rt_frac_neg_96", "float32", "rt", "share of the last 96 known intervals below $0", "as rt_last"),
    _F("rt_frac_neg_672", "float32", "rt", "share of the last 672 known intervals below $0", "as rt_last"),
    # ---- 1.1.0: the residual R[i] = P[i] - A[i], A[i] = median of P at the same time of day on the 7 previous days ----
    _F("res_last", "float32", "rt", "residual of the newest known interval: price minus its 7-day same-time-of-day median, $/MWh", "as rt_last"),
    _F("res_mean_4", "float32", "rt", "mean of the residual over the last 4 known intervals (needs >= 2)", "as rt_last"),
    _F("res_mean_16", "float32", "rt", "mean of the residual over the last 16 known intervals (needs >= 8)", "as rt_last"),
    _F("res_mean_96", "float32", "rt", "mean of the residual over the last 96 known intervals (needs >= 48)", "as rt_last"),
    _F("res_q10_96", "float32", "rt", "10th percentile of the residual over the last 96 known intervals", "as rt_last"),
    _F("res_q50_96", "float32", "rt", "median of the residual over the last 96 known intervals", "as rt_last"),
    _F("res_q90_96", "float32", "rt", "90th percentile of the residual over the last 96 known intervals", "as rt_last"),
    _F("res_q10_672", "float32", "rt", "10th percentile of the residual over the last 672 known intervals (needs >= 336)", "as rt_last"),
    _F("res_q50_672", "float32", "rt", "median of the residual over the last 672 known intervals", "as rt_last"),
    _F("res_q90_672", "float32", "rt", "90th percentile of the residual over the last 672 known intervals", "as rt_last"),
    _F("res_mad_672", "float32", "rt", "mean absolute residual over the last 672 known intervals", "as rt_last"),
    # ---- 1.1.0: lags and same-time-of-day profiles of the TARGET (slot j - 96 m, only if inside the known prefix) ----
    _F("rt_lag_2d", "float32", "rt", "RT SPP at target - 48 h, NaN until it is published", "as rt_last"),
    _F("rt_lag_3d", "float32", "rt", "RT SPP at target - 72 h", "as rt_last"),
    _F("rt_lag_14d", "float32", "rt", "RT SPP at target - 14 d", "as rt_last"),
    _F("rt_prof7_mean", "float32", "rt", "mean of the RT SPP at the target's time of day on the 7 previous days (known ones)", "as rt_last"),
    _F("rt_prof7_med", "float32", "rt", "median of the same 7 values: the anchor the booster predicts deviations from", "as rt_last"),
    _F("rt_prof7_min", "float32", "rt", "minimum of the same 7 values", "as rt_last"),
    _F("rt_prof7_max", "float32", "rt", "maximum of the same 7 values", "as rt_last"),
    _F("rt_prof7_std", "float32", "rt", "population std of the same 7 values", "as rt_last"),
    _F("rt_prof14_dt", "float32", "rt", "mean of the target's time of day over the 14 previous days, days of the target's weekend/weekday type only if at least 2 exist, else all (the arena's profile)", "as rt_last"),
    _F("doy_sin", "float32", "cal", "sin(2 pi (day of year - 1) / 365.25) of the target's local date", "known"),
    _F("doy_cos", "float32", "cal", "cos(2 pi (day of year - 1) / 365.25) of the target's local date", "known"),
    # ---- 1.1.0: day-ahead ancillary prices relative to their own recent level ----
    _F("dam_as_regup_med14", "float32", "dam", "median of the DAM REGUP MCPC at the target's local hour of day over the 14 newest operating dates with published results", "as dam_as_regup"),
    _F("dam_as_regdn_med14", "float32", "dam", "same for REGDN", "as dam_as_regup"),
    _F("dam_as_rrs_med14", "float32", "dam", "same for RRS", "as dam_as_regup"),
    _F("dam_as_nspin_med14", "float32", "dam", "same for NSPIN", "as dam_as_regup"),
    _F("dam_as_regup_rel", "float32", "dam", "dam_as_regup / max(dam_as_regup_med14, 0.1)", "as dam_as_regup"),
    _F("dam_as_regdn_rel", "float32", "dam", "dam_as_regdn / max(dam_as_regdn_med14, 0.1)", "as dam_as_regup"),
    _F("dam_as_rrs_rel", "float32", "dam", "dam_as_rrs / max(dam_as_rrs_med14, 0.1)", "as dam_as_regup"),
    _F("dam_as_nspin_rel", "float32", "dam", "dam_as_nspin / max(dam_as_nspin_med14, 0.1)", "as dam_as_regup"),
)
AS_REL_PRODUCTS = ("REGUP", "REGDN", "RRS", "NSPIN")      # ECRS has existed only since June 2023: no median, no ratio
AS_REL_FLOOR = 0.1                                          # ASSUMPTION (D1a): $/MW-h floor under the 14-day median in the ratio
AS_MED_DATES = 14                                           # operating dates in the median
SPIKE_HI_USD = 100.0                                        # ASSUMPTION (D1a): "high price" threshold of rt_frac_hi_*, $/MWh
FEATURES = tuple(f.name for f in FEATURE_TABLE)
DTYPES = {f.name: f.dtype for f in FEATURE_TABLE}
GROUPS = {g: tuple(f.name for f in FEATURE_TABLE if f.group == g) for g in ("rt", "hb", "dam", "cal", "gas", "wx", "lf")}


def schema_hash() -> str:
    """Fingerprint of the feature definition (names, dtypes, and the constants that shape them). Written into every offline
    parquet file so a stale store is recognised."""
    blob = json.dumps({"set": FEATURE_SET, "version": VERSION, "features": [(f.name, f.dtype) for f in FEATURE_TABLE],
                       "horizon": HORIZON, "lags": [LAG_1D, LAG_7D], "ref": HB_REF, "label": LABEL, "lookback_days": LOOKBACK_DAYS,
                       "defs": {"std_ddof": 0, "holiday": "observed", "window_minp": "w/2", "quantile": "linear", "spike_hi": SPIKE_HI_USD,
                                "as_rel_floor": AS_REL_FLOOR, "as_med_dates": AS_MED_DATES}}, sort_keys=True)
    return hashlib.sha256(blob.encode()).hexdigest()[:16]


# ------------------------------------------------------------------------------------------------ US federal holidays
def _nth_weekday(year: int, month: int, weekday: int, n: int) -> date:
    """n-th (1-based) given weekday of a month; n = -1 means the last one. Monday = 0."""
    if n > 0:
        d = date(year, month, 1)
        d += timedelta(days=(weekday - d.weekday()) % 7 + 7 * (n - 1))
        return d
    d = date(year + (month == 12), month % 12 + 1, 1) - timedelta(days=1)
    return d - timedelta(days=(d.weekday() - weekday) % 7)


def us_federal_holidays(year: int) -> set:
    """Dates of the OBSERVED US federal holidays of `year`'s calendar rules: a Saturday holiday is observed on the Friday before,
    a Sunday holiday on the Monday after, any other on its own date. Exactly what pandas' USFederalHolidayCalendar returns
    (checked in the tests) and what D1a's trained models were fitted on; 1.0.0 also flagged the weekend date itself.
    Juneteenth counts from 2021."""
    actual = [date(year, 1, 1), _nth_weekday(year, 1, 0, 3), _nth_weekday(year, 2, 0, 3), _nth_weekday(year, 5, 0, -1),
              date(year, 7, 4), _nth_weekday(year, 9, 0, 1), _nth_weekday(year, 10, 0, 2), date(year, 11, 11),
              _nth_weekday(year, 11, 3, 4), date(year, 12, 25)]
    if year >= 2021:
        actual.append(date(year, 6, 19))
    out = set()
    for d in actual:
        out.add(d - timedelta(days=1) if d.weekday() == 5 else d + timedelta(days=1) if d.weekday() == 6 else d)
    return out


_HOLIDAY_CACHE: dict[int, set] = {}


def holiday_dates(years) -> set:
    out = set()
    for y in set(int(v) for v in years):
        for yy in (y - 1, y, y + 1):                    # Jan 1 on a Saturday is observed on Dec 31 of the year before
            if yy not in _HOLIDAY_CACHE:
                _HOLIDAY_CACHE[yy] = us_federal_holidays(yy)
            out |= _HOLIDAY_CACHE[yy]
    return out


def is_holiday(local_dates) -> np.ndarray:
    """Boolean array: is each local calendar date (numpy datetime64[D] or a DatetimeIndex.date array) a US federal holiday?"""
    d = np.asarray(local_dates, dtype="datetime64[D]")
    if d.size == 0:
        return np.zeros(0, dtype=bool)
    years = np.unique(d.astype("datetime64[Y]").astype(int) + 1970)
    hol = np.array(sorted(np.datetime64(h, "D") for h in holiday_dates(years)), dtype="datetime64[D]")
    return np.isin(d, hol)
