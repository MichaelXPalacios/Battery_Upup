"""Time handling for the data layer: ERCOT Central Prevailing Time <-> UTC, MIS file-name publish times, DST rules.

Everything stored by the platform is tz-aware UTC at microsecond resolution (`datetime64[us, UTC]`). ERCOT files speak
local Central Prevailing Time (CPT) with explicit repeated-hour flags; the MIS file *names* speak CPT with no flag.
"""
from __future__ import annotations

import re
from typing import Optional, Union

import numpy as np
import pandas as pd

from . import assumptions as A

CPT = A.TZ_MARKET
UTC_US = "datetime64[us, UTC]"

# <cdr|rpt|ext>.<8-digit reportTypeId>.<16-digit DUNS>.<YYYYMMDD>.<HHMMSSmmm>.<friendly name>   (ext = the 60-day disclosure reports)
_MIS_NAME = re.compile(r"^(?P<prefix>cdr|rpt|ext)\.(?P<rid>\d{8})\.(?P<duns>\d{16})\.(?P<date>\d{8})\.(?P<time>\d{9})\.(?P<rest>.+)$",
                       re.IGNORECASE)


def utc(ts: Union[str, pd.Timestamp, "datetime.datetime", np.datetime64]) -> pd.Timestamp:
    """Any timestamp -> tz-aware UTC pandas Timestamp (microsecond resolution). Naive input is an error: say what zone."""
    t = pd.Timestamp(ts)
    if t.tzinfo is None:
        raise ValueError(f"naive timestamp {ts!r}: pass a tz-aware value (UTC) so a time zone is never guessed")
    return t.tz_convert("UTC").as_unit("us")


def iso_z(ts: pd.Timestamp) -> str:
    """UTC instant -> '2026-10-10T15:17:02.135000Z' (stable text form used in the catalog and JSON payloads)."""
    t = utc(ts)
    return t.strftime("%Y-%m-%dT%H:%M:%S.%f") + "Z"


def to_us_utc(s: pd.Series) -> pd.Series:
    """Series of tz-aware instants -> datetime64[us, UTC] (fixed dtype for parquet)."""
    return pd.Series(pd.DatetimeIndex(s).tz_convert("UTC").as_unit("us"), index=s.index if isinstance(s, pd.Series) else None)


# ------------------------------------------------------------------------------------------------ MIS file names
def is_mis_name(name: str) -> bool:
    return _MIS_NAME.match(name) is not None


def mis_name_parts(name: str) -> dict:
    """Split an ERCOT MIS constructed name. Raises ValueError for anything else."""
    m = _MIS_NAME.match(name)
    if not m:
        raise ValueError(f"not an ERCOT MIS file name: {name!r}")
    return {"prefix": m["prefix"].lower(), "report_type_id": int(m["rid"]), "date": m["date"], "time": m["time"], "rest": m["rest"]}


def parse_mis_publish(name: str, hint: Optional[pd.Timestamp] = None) -> pd.Timestamp:
    """Publish instant (UTC) of an ERCOT MIS file, from its name.

    `cdr.00012301.0000000000000000.20261010.101702135.SPPHLZNP6905_20261010_1015_csv.zip` was posted 2026-10-10 10:17:02.135
    Central Prevailing Time = 15:17:02.135 UTC.

    DST (the rule is documented in assumptions.MIS_AMBIGUOUS_IS_STANDARD_TIME and tested in tests/test_lake.py):
    * ambiguous wall-clock time (the repeated 01:00-02:00 hour on the November fall-back Sunday): the SECOND occurrence,
      standard time, i.e. the LATER UTC instant. Data can then look published up to an hour late, never early.
      If `hint` (an exact tz-aware instant, e.g. the offset-bearing `PublishDate` of the MIS JSON listing) is given and is
      one of the two candidates (within 60 s), the hint decides instead.
    * non-existent time (02:00-03:00 on the March spring-forward Sunday; no real file has one): read as standard time, i.e.
      shifted forward one hour, so 02:30 becomes 03:30 CDT. Again never earlier than the standard-time reading.
    """
    p = mis_name_parts(name)
    d, t = p["date"], p["time"]
    naive = pd.Timestamp(f"{d[:4]}-{d[4:6]}-{d[6:]} {t[:2]}:{t[2:4]}:{t[4:6]}.{t[6:]}")
    return local_to_utc_scalar(naive, hint=hint)


def local_to_utc_scalar(naive: pd.Timestamp, hint: Optional[pd.Timestamp] = None) -> pd.Timestamp:
    """One CPT wall-clock time -> UTC under the ambiguity rule above."""
    standard = pd.Timestamp(naive).tz_localize(CPT, ambiguous=not A.MIS_AMBIGUOUS_IS_STANDARD_TIME, nonexistent=pd.Timedelta(hours=1))
    out = standard.tz_convert("UTC")
    if hint is not None:
        try:
            first = pd.Timestamp(naive).tz_localize(CPT, ambiguous=True, nonexistent="shift_forward").tz_convert("UTC")
            second = pd.Timestamp(naive).tz_localize(CPT, ambiguous=False, nonexistent="shift_forward").tz_convert("UTC")
        except Exception:
            first = second = out
        if first != second:
            h = utc(hint)
            best = min((first, second), key=lambda c: abs((c - h).total_seconds()))
            if abs((best - h).total_seconds()) <= 60:
                out = best
    return out.as_unit("us")


# ------------------------------------------------------------------------------------------------ vectorised CPT -> UTC
def local_to_utc(naive, repeated=None) -> pd.Series:
    """Wall-clock CPT datetimes (naive) + optional flags 'second occurrence of a repeated hour' -> UTC instants.

    `repeated` True means the row is the repeated (second, standard-time) pass. Rows with no flag that fall in the
    ambiguous hour are read as the FIRST pass (daylight time): data files that carry a flag use it, so only unflagged
    rows are guessed. Non-existent times (spring-forward gap) shift forward.
    """
    s = pd.Series(naive).reset_index(drop=True)
    if repeated is None:
        amb = np.ones(len(s), dtype=bool)
    else:
        amb = ~np.asarray(pd.Series(repeated).astype(bool).reset_index(drop=True))
    out = s.dt.tz_localize(CPT, ambiguous=amb, nonexistent="shift_forward").dt.tz_convert("UTC")
    return out.astype(UTC_US)


def cpt_date_str(ts_utc: pd.Series) -> pd.Series:
    """UTC instants -> CPT calendar date 'YYYY-MM-DD'."""
    return pd.Series(pd.DatetimeIndex(ts_utc).tz_convert(CPT).strftime("%Y-%m-%d"), index=getattr(ts_utc, "index", None))


def _digits_hour(he: pd.Series) -> pd.Series:
    """'01:00' / '1:00' / '24:00' / '24:00:00' -> int hour ending."""
    return he.astype(str).str.strip().str.split(":").str[0].astype(int)


def rt_interval_start(delivery_date, delivery_hour, delivery_interval, repeated=None) -> pd.Series:
    """15-minute settlement rows: DeliveryDate 'MM/DD/YYYY', DeliveryHour 1..24, DeliveryInterval 1..4 -> interval START (UTC)."""
    h = pd.Series(delivery_hour).astype(int).reset_index(drop=True)
    i = pd.Series(delivery_interval).astype(int).reset_index(drop=True)
    d = pd.to_datetime(pd.Series(delivery_date).reset_index(drop=True), format="%m/%d/%Y")
    naive = d + pd.to_timedelta((h - 1) * 60 + (i - 1) * 15, unit="m")
    return local_to_utc(naive, repeated)


def he_to_start(delivery_date, hour_ending, repeated=None) -> pd.Series:
    """Hourly rows: DeliveryDate 'MM/DD/YYYY', HourEnding '01:00'..'24:00' -> START of that hour (UTC)."""
    he = _digits_hour(pd.Series(hour_ending).reset_index(drop=True))
    d = pd.to_datetime(pd.Series(delivery_date).reset_index(drop=True), format="%m/%d/%Y")
    naive = d + pd.to_timedelta((he - 1) * 60, unit="m")
    return local_to_utc(naive, repeated)


def sced_stamp(ts_strings, repeated=None, fmt: str = "%m/%d/%Y %H:%M:%S") -> pd.Series:
    """SCED run timestamps '10/10/2026 09:55:16' (+ repeated-hour flag) -> UTC instants."""
    naive = pd.to_datetime(pd.Series(ts_strings).reset_index(drop=True), format=fmt)
    return local_to_utc(naive, repeated)


def flag_is_repeated(flag) -> pd.Series:
    """'Y'/'N' flags (any case, stray spaces) -> bool."""
    return pd.Series(flag).astype(str).str.strip().str.upper().eq("Y").reset_index(drop=True)


def local_day_start_utc(date_str: str) -> pd.Timestamp:
    """'2026-10-10' -> the UTC instant of 00:00 CPT that day."""
    return pd.Timestamp(date_str).tz_localize(CPT, ambiguous=False, nonexistent="shift_forward").tz_convert("UTC").as_unit("us")


def local_naive_series_to_utc(naive: pd.Series) -> pd.Series:
    """Naive local timestamps with no repeat flag -> UTC, using the same conservative ambiguity rule as the file names."""
    amb = not A.MIS_AMBIGUOUS_IS_STANDARD_TIME
    s = pd.Series(naive).reset_index(drop=True)
    return s.dt.tz_localize(CPT, ambiguous=np.full(len(s), amb), nonexistent="shift_forward").dt.tz_convert("UTC").astype(UTC_US)
