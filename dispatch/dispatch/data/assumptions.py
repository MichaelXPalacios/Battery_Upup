"""Named publish-time rules and data constants for the data layer (D0).

Every value carries its label: REAL (named source or measured), DERIVED, ASSUMPTION, or PROPOSED. A row may feed a
decision at `asof_utc` only if `published_utc <= asof_utc` (DESIGN.md, Point in time), so every dataset needs a
`published_utc`. Where ERCOT puts it in the file name (MIS CDR reports) it is REAL. Historical files with no per-row
publish time get a rule below, and each rule is conservative: when unsure, assume the data became public LATER.
Whatever the rule says, a row is also capped at the publish/fetch time of the file that holds it (it cannot have
become public after the file containing it existed).

REQUEST (D0 -> lead): these live here because config.py is lead-owned. Move them there if you want one home.
"""
from __future__ import annotations

import pandas as pd

# ------------------------------------------------------------------------------------------------ time zone
TZ_MARKET = "America/Chicago"   # REAL: ERCOT clock is Central Prevailing Time (same value as config.TZ_MARKET)

# MIS file names carry the posting time in Central Prevailing Time with no offset. On the November fall-back day the
# hour 01:00-02:00 happens twice and the name cannot say which. DESIGN CHOICE (deterministic, conservative):
# an ambiguous wall-clock time is read as the SECOND occurrence (standard time, the later UTC instant). The error
# then only ever makes data look published later than it was, never earlier, so it cannot create leakage.
MIS_AMBIGUOUS_IS_STANDARD_TIME = True   # ASSUMPTION (design choice, documented in notes/d0.md and tested in test_lake.py)

# ------------------------------------------------------------------------------------------------ real-time prices
# Measured on 718 NP6-905-CD files posted 3-10 Oct 2026: the file for the interval ending at t is posted at t + 122.3 s
# (median), t + 127.5 s (p99), t + 237 s (p99.9), t + 507.6 s (max).
RT_SPP_MEASURED_LAG_S = {"median": 122.3, "p99": 127.5, "p999": 237.0, "max": 507.6}   # REAL (measured)
# Rows from the yearly NP6-785-ER workbook (and any other bulk RT price file) carry no posting time. We say the price
# of the interval [t, t+15min) became public at t + 15min + this lag. 10 min is above the longest delay measured (8.5 min).
RT_PRICE_PUBLISH_LAG = pd.Timedelta(minutes=10)           # ASSUMPTION (bounded by the measurement above)
RT_AS_PUBLISH_LAG = pd.Timedelta(minutes=10)              # ASSUMPTION: NP6-331-CD files post ~2 min after the interval; same bound

# ------------------------------------------------------------------------------------------------ day-ahead market
# Measured on 31 NP4-190-CD / NP4-188-CD files posted 9 Sep - 9 Oct 2026: results for delivery day D post on D-1 between
# 12:32 and 13:56 CPT (median ~12:41). The protocol's 13:30 CPT target was missed on several days.
DAM_MEASURED_POST_CPT = {"first": "12:32", "last": "13:56"}   # REAL (measured)
DAM_PUBLISH_LOCAL_HOUR_MINUTE = (14, 0)                   # ASSUMPTION: DAM results public by 14:00 CPT on D-1 (above all 31 samples)
DAM_PUBLISH_DAYS_AHEAD = 1                                # REAL: results for delivery day D are posted on D-1

# ------------------------------------------------------------------------------------------------ gas and weather
# Henry Hub (FRED series DHHNGSP, copied from EIA). The adopted file was fetched 3 Oct 2026 and ends Tue 29 Sep, which fits
# a weekly Wednesday release; a trade-date price is then public between 1 and 7 days after the date. We use the 7-day bound.
GAS_PUBLISH_LAG_DAYS = 7                                  # ASSUMPTION: the price for trade date D is public by 12:00 CPT on D+7
GAS_PUBLISH_LOCAL_HOUR = 12
# GHCN-Daily, one row per station-day. "A day's observation is published the next day": day D is public at 18:00 CPT on D+1.
WEATHER_OBS_PUBLISH_DAYS_AFTER = 1                        # ASSUMPTION
WEATHER_OBS_PUBLISH_LOCAL_HOUR = 18                       # ASSUMPTION

# ------------------------------------------------------------------------------------------------ features
MAX_HORIZON_INTERVALS = 96                                # REAL: 24 h of 15-minute settlement intervals
FEATURE_SET = "rt_v1"                                     # name of the versioned feature set (features/sets.py)

LABELS = {
    "RT_PRICE_PUBLISH_LAG": "ASSUMPTION", "RT_AS_PUBLISH_LAG": "ASSUMPTION",
    "DAM_PUBLISH_LOCAL_HOUR_MINUTE": "ASSUMPTION", "DAM_PUBLISH_DAYS_AHEAD": "REAL",
    "GAS_PUBLISH_LAG_DAYS": "ASSUMPTION", "WEATHER_OBS_PUBLISH_DAYS_AFTER": "ASSUMPTION",
    "MIS_AMBIGUOUS_IS_STANDARD_TIME": "ASSUMPTION",
}
