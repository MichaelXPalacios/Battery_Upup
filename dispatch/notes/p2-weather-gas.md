# P2 notes: weather and gas fetchers (11 Oct 2026)

Closes the gap where `weather_obs` and `gas_hh` ended on 29 Sep 2026 and nothing could refresh them. Labels follow the repo: REAL (read from a source or measured on 11 Oct 2026),
DERIVED (computed by a stated rule), ASSUMPTION (a named constant in `dispatch/data/assumptions.py`). Times are UTC unless a zone is named. Nothing in `research-data/weather` or
`research-data/gas` was touched. No account, no key, no ERCOT or FRED call: FRED is blocked here and EIA's API v2 needs a key, so neither is used.

## 1. Result

| Item | Result |
|---|---|
| Weather fetcher | `weather.fetch_observations` -> raw `ncei/GHCND`, parsed into `weather_obs`. Real run: 1 request, 752 rows, 38,224 bytes, newest day **7 Oct** at all 18 stations |
| Gas fetcher | `gas.fetch_history` -> raw `eia/RNGWHHD`, parsed into `gas_hh`. Real run: 1 request, 308,446 bytes, 5,702 prices, newest trade date **6 Oct**, page released 7 Oct 13:14:54Z |
| Wiring | `flows.ingest_daily` and `python -m dispatch.data.flows daily` (run once a day); `flows.ingest_once(..., daily=True)` and `ingest --daily` call it. `prepare_signal` does not (the model does not read these two, and it must not wait on NCEI or EIA) |
| Freshness (`catalog.freshness`, 11 Oct 16:27Z) | `GHCND` 52 s (was 10.7 d), `RNGWHHD` 4.13 d (was 7.76 d, borrowed from the adopted FRED file). Newest observation day: 7 Oct for weather (was 29 Sep, 12.5 d old, now 4.5 d from the start of the day, **3.48 d from its end** = NCEI's own lag), 6 Oct for gas (5.5 d, **4.1 d since the release**) |
| Monitoring | `GHCND` (cadence 1 d, allowance 4 d + 300 s) and `RNGWHHD` (7 d, allowance 9 d + 300 s) in `REPORT_CADENCE_S` / `REPORT_EXTRA_ALLOWANCE_S`. **Alert only**; `HALT_ELIGIBLE_REPORTS` is unchanged |
| Gate 1 | `tests/test_rebuild.py` 18 passed and `tests/test_leakage.py` 43 passed with the new code. On the real lake: `weather_obs` + `gas_hh` rebuilt from the raw files alone in a fresh interpreter (hash seed 12345): **643 of 643** parquet files byte-identical; `lake.verify(check_hash=True)` of GHCND, RNGWHHD, DHHNGSP clean |
| New tests | `tests/test_weather_gas_fetch.py`, 53 tests, recorded fixtures, no network (an autouse fixture makes any socket connection fail). Six rule mutations (no dedupe, no lag floor, earliest instead of latest visible, no visibility cut, gas stamped at fetch, time-addressed names) each make it fail |
| Full suite | `pytest -q tests` (11 Oct, 11:36 to 11:53 CDT, with P1 running its own suites): **668 passed, 1 skipped, 1 failed** in 16 min 57 s. The failure, `test_guard_props.py::test_generator_reaches_both_outcomes`, is a coverage check on an unseeded random generator (193 of 200 required True draws) in code P2 did not touch; it passes on two reruns (48 passed each) |

## 2. Sources (both keyless; one request each; `hugging-base-dispatch (research)` as User-Agent, no email, nothing personal)

* **Weather**: `https://www.ncei.noaa.gov/access/services/data/v1?dataset=daily-summaries&stations=<18 ids>&startDate=..&endDate=..&dataTypes=TMAX,TMIN,PRCP,WSF2&units=standard&format=csv`.
  The 18 stations are the files of `research-data/weather` (the 8 ERCOT airports of `weather.STATIONS` and the 10 NYISO stations of the arena; a test checks the list against that folder). Units are
  degF, degF, inches, mph, as in the adopted files; the overlap of the two (609 station-days) has **0** differences. AWND is not requested (no feature reads it), so new rows have `awnd_mph` NaN.
* **Gas**: `https://www.eia.gov/dnav/ng/hist/rngwhhdD.htm`, the page the original study parsed (the XLS twin is BIFF, and nothing here reads BIFF). A table of weeks, Monday to Friday, blank where there was
  no price; footer lines `Release Date: 10/7/2026`, `Next Release Date: 10/15/2026` and a comment with the page time `10/7/2026 9:14:54 AM` (US Eastern; the HTTP `Last-Modified` is the same instant, 13:14:54 GMT).
  The parser is strict (each week label must start on a Monday, each row has five cells, a cell is a number or EIA's blank marks). Against the adopted FRED copy: **5,697 of 5,697** shared trade dates equal; 5 are new.
* Politeness: one request at a time, at least 2 s apart (`FETCH_MIN_SPACING_S`), 3 retries with backoff, responses above 20 MB refused. A response is data: it is validated before it may enter the lake (header, stations asked for,
  days inside the window, numbers in physical bounds, no duplicate station-day; for gas the title, the structure, at least 1,000 prices, no price dated in the future), and nothing executes or follows anything in it.

## 3. Publish-time rules

| Dataset | Raw file `published_utc` (catalog) | Row `published_utc` (parsed) | Basis |
|---|---|---|---|
| `weather_obs`, adopted station files | the copy time (file mtime) | unchanged: 18:00 CPT on D+1, capped at the copy | ASSUMPTION (kept) |
| `weather_obs`, fetched | the fetch time. Name `GHCND.<start>_<end>.<sha8>.csv`, origin `web` | the fetch time of the **first file that carried this value**, but never before 18:00 CPT on D+1 (the floor: a feature must never see the same day's reading) | REAL when the first-seen time governs, ASSUMPTION when the floor does. All 143 rows of 11 Oct are REAL |
| `gas_hh`, adopted FRED csv | the copy time | unchanged: 12:00 CPT on D+7, capped at the copy | ASSUMPTION (kept) |
| `gas_hh`, fetched | **the page's own release stamp** (footer, else `Last-Modified`), capped at the fetch time. Name `rngwhhdD.rel<YYYYMMDD>.<sha8>.htm` | the file's `published_utc`: all prices on a page are published when that page was released | REAL |

Why the fetched weather rows do not use the documented lag rule ("day D public at 18:00 CPT on D+1"): **it is not conservative, so it was not used.** Measured:

* 11 Oct 16:26Z: the newest day at all 18 stations is **7 Oct**, which ended 3.48 days earlier; 8 Oct (ended 2.46 days earlier) is absent.
* 3 Oct 21:29 to 22:03Z (when the adopted files were copied): newest 29 Sep (ended 3.71 days earlier); 30 Sep (2.71 days) absent.
* So NCEI posts a day about **2.7 to 3.5 days after it ends**, against the rule's 0.75 days. Two samples: enough to say the rule is optimistic, too few to call 3.5 days a bound. First-seen is a safe upper bound by construction.
  The 143 new rows are therefore stamped 11 Oct 16:26:40Z, whatever the real time was between 8 and 11 Oct.

Gas: EIA releases on Wednesday morning (9:40 AM on 9/30, 9:14 AM on 10/7, Eastern) with prices through the Tuesday before, so a trade date is public 1 to 7 days after it (Tuesday to Wednesday is 1 day, Wednesday
to the next Wednesday is 7). The adopted rule (D+7, 12:00 CPT) is exactly that maximum. But the page of 10/7 announces the next release for **Thursday 10/15**, so in a holiday week the Wednesday price takes 8 days: the 7-day rule is one
day short there. Fetched rows use the stamp and are exact.

## 4. Revisions

* **Keep everything.** Each response is a new raw file (name = window + first 8 hex of its sha256); a different file never overwrites an older one (`lake.put` raises on the same name with other bytes). The same bytes again are a no-op and the
  first fetch time stays.
* **Versions in `parse`** (`parse._drop_repeated_versions`): all files are read in publish order. A station-day (or trade date) that comes again with the same four values (TMAX, TMIN, PRCP, WSF2; the price) in a later file is dropped, so the
  row that stays is the one published when the value **first** appeared. A different value (a revision, a filled-in WSF2, a retraction to blank) is a **new row** with the later file's time. Both rows stay in `weather_obs` / `gas_hh`.
* **Reading at a decision time**: `catalog.observations_asof(dataset, asof_utc)` returns, per station-day / trade date, the **latest-published version with `published_utc <= asof_utc`** (inclusive, like `lake.iter_raw`); a day whose first
  version is later than `asof_utc` is absent. `catalog.latest_visible(df, keys, asof)` is the pure function behind it. Tests: a value is invisible one microsecond before its time, a revision becomes visible exactly at its time, the older
  version is what a decision between the two sees.
* **The feature builder does not do that.** `features/build.py` (`_weather_series`, `_gas_series`) keeps, per day, the **earliest** published version, as for prices ("what was known soonest"). That is safe (it can never use a value before it is public)
  and the lake keeps the rest, but a revision, or a WSF2 that arrives a day later, never reaches a feature. It is D0's file, not P2's, so it is a REQUEST (section 7). A test pins the current behaviour.
* **How much it matters, measured**: of the 609 weather station-days and 5,697 gas prices that the 11 Oct fetch had in common with the adopted files, **0 changed** (the adopted copies were already 4 to 8 days past NCEI's 3.5-day lag, and
  EIA did not revise). The revision path is therefore tested on a synthetic revision (a TMAX 87 -> 88, a blank WSF2 filled in, a gas price 3.02 -> 3.03), not on a real one. The real effect so far: one station-day (New York's USW00094745, 7 Oct) has no WSF2 yet in
  its only version.

## 5. Window and idempotence

* `fetch_observations` asks for `end` = today in Central time and `start` = the earlier of (newest day in the lake - 14 days) and (today - 45 days). The fixed 45-day floor means a second run on the same day asks for exactly the same days, gets the
  same bytes, and changes nothing; it also catches a late revision up to six weeks back. The newest day in the lake is read from the newest parsed partition (a raw scan if nothing is parsed). With an empty lake it raises and asks for `flows adopt`
  (pulling 23 years is not a daily fetch); a gap longer than 400 days is split into requests.
* Real second run (16:35Z, 9 minutes after the first): NCEI returned the identical 752 rows (same sha8 `441628cd`), EIA the identical page; `catalog.jsonl` stayed at 10,861 lines, the parse skipped both datasets as up to date.
* Cost: the 45-day window is about 38 KB a day of raw (14 MB a year); the gas page is 0.3 MB and changes weekly (16 MB a year). A parse after a new file rewrites every monthly partition (286 and 357 files; about 30 s for both on this machine); a no-op parse takes about 3 s.

## 6. Monitoring (alert only)

| Report | Cadence | Extra allowance | Allowance | Reason |
|---|---|---|---|---|
| `GHCND` | 86,400 s | 3 d | 345,900 s (4 d 5 min) | a new day every day, NCEI posts it ~3 days late: one stalled day and a missed fetch still pass |
| `RNGWHHD` | 604,800 s | 2 d | 777,900 s (9 d 5 min) | weekly release, 8 days in a holiday week; a skipped release (14 days) alerts |

`HALT_ELIGIBLE_REPORTS` is the same six ids; both new reports are in `MONITORED_REPORTS` and so alert only (`halts=False`, message ends "(alert only)"). A test asserts the halt set, the numbers, and that exactly-at-allowance is no breach.
Age is defined in `catalog.freshness`: **GHCND is the age of the newest observation published** (not of the newest file, which only says the fetcher ran: a fetch that brought nothing new would look fresh); **RNGWHHD is the age of the newest page
release**, and until the first page is fetched it borrows the adopted FRED file's time, so a deployment whose fetcher never runs goes stale in the monitor instead of being absent from it.
The registry (`reports.py`) holds both as `kind="web"` reports with synthetic `rtid` 9,000,001 and 9,000,002 (needed by the existing `test_monitoring` checks that every watched report is a registered, live, non-archive report whose number maps
back to its id); `INGEST_IDS` and the new `reports.mis_ingest_ids()` keep them away from the ERCOT downloader.

## 7. REQUESTs for the lead (files P2 does not own)

1. **`dispatch/data/ercot.py`, two lines.** `ingest_once()` and `backfill_all()` choose default ids with `if r.ingest and (include_archives or not r.archive)`, which now also lists `GHCND` and `RNGWHHD`. `flows` always passes explicit ids
   (`reports.mis_ingest_ids()`), and a test pins that, but a direct call with no ids would send `reportTypeId=9000001` to ercot.com. Fix: add `and r.kind == "mis"` to both comprehensions (lines 263-264 and 274).
2. **`tests/test_gate1_real_lake.py` line 53.** Change `("DHHNGSP", "GHCND")` to `("DHHNGSP", "GHCND", "RNGWHHD")`. Without it the opt-in real-lake Gate 1 fails on `gas_hh` (the rebuild would not see the EIA page). I ran the same check for the two datasets by script (section 1: 643 of 643).
3. **`features/build.py`: latest visible version instead of earliest** for weather and gas, if revisions should reach the features. `_weather_series` and `_gas_series` would hold a second array of later versions per day and pick, per decision, the latest
   with `pub <= asof` (`catalog.latest_visible` is the reference); the leakage oracle and `sets.py` text change with it. Not urgent: no real revision has been seen.
4. **Two history rules are optimistic for live use** (`assumptions.py`, left unchanged because moving them rewrites every historical weather and gas feature and changes `test_rebuild.py::test_assumed_publish_times_are_labelled_and_conservative`):
   weather `WEATHER_OBS_PUBLISH_DAYS_AFTER = 1` against a measured lag of ~3.5 days after the day ends (setting 4 at 18:00 CPT would cover it; the model trained on history sees weather ~3 days fresher than it will be served), and gas
   `GAS_PUBLISH_LAG_DAYS = 7` against 8 days in holiday weeks. `features/sets.py` describes both rules in its publish-rule column.
5. **Redis.** The online frames on :6390 still carry the 10 Oct `wx_age_days` / `gas_hh_age_days` (about 11 days) until the next `flows prepare`.
6. **Scheduling.** Nothing schedules itself. Put `python -m dispatch.data.flows daily` in cron / the paper loop's daily step; any hour works (a day is first seen when NCEI posts it, at most one run later).

README line for the known limits (the lead pastes it; P1 owns README.md):

> Weather (NOAA GHCN-Daily, 18 stations) and Henry Hub gas are refreshed by `python -m dispatch.data.flows daily` (NCEI Access Data Service and EIA's history page, keyless; run once a day, nothing schedules it). The served model does not read them;
> they are monitored alert-only (`GHCND` daily, `RNGWHHD` weekly). NCEI posts a day about 3.5 days after it ends, so the 1-day weather rule used for the adopted history is optimistic (fetched rows are stamped with the time we first saw them), and the
> feature builder reads the first-published version of a day: revisions are kept in the lake and resolved by `catalog.observations_asof`, but none has been seen yet.

## 8. Files

`dispatch/data/{weather,gas,flows,parse,catalog,assumptions,reports}.py` (`gas.py` is new), `dispatch/monitoring/checks.py` (the cadence and allowance entries and two comments), `tests/test_weather_gas_fetch.py`,
`tests/fixtures/weather_gas/` (9 files, 24 KB: real responses with three marked edits, the frozen research files' excerpts, one synthetic next EIA release), this note.
`parse.py` changed in `parse_gas_hh`, `parse_weather_obs`, the two dataset entries and a new `_drop_repeated_versions`; with adopted files alone its output is unchanged (tested, and `test_rebuild` passes). `gas_hh` now reads two report ids
(`DHHNGSP`, `RNGWHHD`). Recipes: `python -m dispatch.data.flows daily [--refetch-days N] [--no-parse]`, `python -m dispatch.data.flows ingest --daily`.
