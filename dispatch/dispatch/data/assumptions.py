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

# ------------------------------------------------------------------------------------------------ fetched weather and gas (P2, 11 Oct 2026)
# The two rules above stay as they are: they stamp the ADOPTED history (the files copied from research-data), and changing them would move every
# historical gas and weather feature. What P2 measured on 11 Oct 2026 says both are optimistic for live use, so rows that come from the
# fetchers (data.weather.fetch_observations, data.gas.fetch_history) do NOT use them. They use what was observed (see notes/p2-weather-gas.md).
#
# NCEI daily summaries (GHCN-Daily): at 2026-10-11 16:26Z the newest day for all 18 stations was 2026-10-07 (3.48 days after it ended) and 8 Oct was
# absent (2.46 days after it ended); the adopted files, fetched 3 Oct 21:29 to 22:03Z, ended 29 Sep (3.71 days after it ended) and 30 Sep was absent
# (2.71 days). A day is therefore public between ~2.7 and ~3.5 days after it ends (about D+3.7 to D+4.5 from the start of the day), not on D+1 at
# 18:00 CPT (1.75 days from the start of D). Two samples: enough to say the 1-day rule is optimistic, too few to call 3.5 days a bound.
WEATHER_OBS_LAG_AFTER_DAY_END_DAYS_MEASURED = (2.7, 3.5)   # REAL (measured, 2 samples): the day-end to first-seen lag lies in this range
# A fetched weather row is published at the moment its value first appeared in a response we fetched (the file's `published_utc`), never before
# the lag rule above (the floor: a feature must never see the same day's reading). The first-seen time is an upper bound on the true time.
WEATHER_FETCH_REFETCH_DAYS = 14                            # ASSUMPTION: each fetch re-requests this many days before the newest day in the lake,
                                                           #   so a revised value shows up as a new version (11 Oct: 0 of 609 overlapping station-days and 0 of 5,697 gas prices had changed)
WEATHER_FETCH_MIN_WINDOW_DAYS = 45                         # ASSUMPTION: a window never starts later than this many days before today, so a second run on the
                                                           #   same day asks for exactly the same days (same bytes, a no-op in the lake) and a late revision up to
                                                           #   six weeks back is still caught
WEATHER_FETCH_MAX_SPAN_DAYS = 400                          # ASSUMPTION: one request covers at most this many days (longer gaps are split)
# EIA's Henry Hub history page: Release Date 9/30/2026 and 10/7/2026 (Wednesdays), data through the Tuesday before, so a trade date is public 1 to 7
# days after it; the page after 10/7 announces the next release on Thursday 10/15, so a holiday week stretches that to 8 days for the Wednesday
# trade date. GAS_PUBLISH_LAG_DAYS = 7 above is the normal maximum, one day short in such a week.
GAS_RELEASE_LAG_DAYS_MEASURED = (1, 8)                     # REAL (measured on two releases and the announced next one): trade date -> release, days
EIA_PAGE_TZ = "America/New_York"                           # REAL: the page footer says 10/7/2026 9:14:54 AM and the HTTP Last-Modified is 13:14:54 GMT
# A fetched gas row is published at the page's own release stamp (footer time, else the Last-Modified header), capped at the fetch time.
DAILY_FETCH_MIN_GAP_S = 6 * 3600                           # ASSUMPTION: flows.ingest_daily asks a source again no sooner than this (in one process)
FETCH_USER_AGENT = "hugging-base-dispatch (research)"      # REAL: no email, no personal data
FETCH_MIN_SPACING_S = 2.0                                  # ASSUMPTION: at least 2 s between two requests to NCEI or EIA
FETCH_RETRIES = 3                                          # ASSUMPTION: retries after the first attempt (backoff 1 s, 2 s, 4 s)
FETCH_MAX_BYTES = 20 * 1024 * 1024                         # ASSUMPTION: a response above 20 MB is refused (the whole GHCN history of 18 stations is ~16 MB)

# ------------------------------------------------------------------------------------------------ features
MAX_HORIZON_INTERVALS = 96                                # REAL: 24 h of 15-minute settlement intervals
FEATURE_SET = "rt_v1"                                     # name of the versioned feature set (features/sets.py)

LABELS = {
    "RT_PRICE_PUBLISH_LAG": "ASSUMPTION", "RT_AS_PUBLISH_LAG": "ASSUMPTION",
    "DAM_PUBLISH_LOCAL_HOUR_MINUTE": "ASSUMPTION", "DAM_PUBLISH_DAYS_AHEAD": "REAL",
    "GAS_PUBLISH_LAG_DAYS": "ASSUMPTION", "WEATHER_OBS_PUBLISH_DAYS_AFTER": "ASSUMPTION",
    "WEATHER_OBS_LAG_AFTER_DAY_END_DAYS_MEASURED": "REAL", "WEATHER_FETCH_REFETCH_DAYS": "ASSUMPTION", "WEATHER_FETCH_MIN_WINDOW_DAYS": "ASSUMPTION",
    "GAS_RELEASE_LAG_DAYS_MEASURED": "REAL", "DAILY_FETCH_MIN_GAP_S": "ASSUMPTION",
    "MIS_AMBIGUOUS_IS_STANDARD_TIME": "ASSUMPTION",
}
