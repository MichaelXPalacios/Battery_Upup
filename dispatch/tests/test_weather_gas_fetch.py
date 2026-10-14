"""P2: the daily weather (NOAA GHCN-Daily via NCEI) and gas (EIA Henry Hub) fetchers, their parsers, publish times, revisions, freshness and monitoring.

No test here touches the network: HTTP goes through `httpx.MockTransport`, and an autouse fixture makes any real socket connection a failure.
Every response comes from `tests/fixtures/weather_gas/`:

* `ncei_fetch1.csv`   the REAL response of NCEI's Access Data Service fetched on 11 Oct 2026 (3 of the 18 stations: DFW USW00003927, IAH USW00012960,
                      New York Central Park USW00094728; 25 Sep to 7 Oct). One edit: New York's WSF2 of 7 Oct is blank (the first version of a day often lacks it).
* `ncei_fetch2.csv`   the same response as it would look a day later: DFW's TMAX of 6 Oct is 88 instead of 87 (a revision), New York's WSF2 of 7 Oct is
                      filled in (the real 12.1), and 8 Oct is a new day (SYNTHETIC values).
* `adopted_*.csv`     the frozen research files (research-data/weather, research-data/gas), 20 to 29 Sep for the stations and 24 Nov 2025 to 29 Sep for gas.
* `eia_page_release_1007.htm`   EIA's real Henry Hub page of release 10/7/2026 (9:14:54 AM Eastern), trimmed to 9 weeks (Thanksgiving, the New Year week
                      that crosses the year, a Monday holiday, the last weeks); the head and tail of the page are untouched.
* `eia_page_release_1015.htm`   SYNTHETIC next release (10/15/2026 9:12:10 AM): 7 to 9 Oct and Tuesday 13 Oct filled in, 30 Sep revised from 3.02 to 3.03.
* `ncei_error_page.html`        what a failing gateway sends instead of a csv.
"""
from __future__ import annotations

import hashlib
import re
import socket
from pathlib import Path

import httpx
import numpy as np
import pandas as pd
import pytest

from dispatch import paths as paths_mod
from dispatch.data import adopt, catalog, ercot, flows, gas, lake, parse, reports, weather
from dispatch.data import assumptions as A
from dispatch.config import DATA_FRESHNESS_TARGET_S
from dispatch.data.ercot import FetchError, Fetcher
from dispatch.monitoring import checks as C

FIX = Path(__file__).parent / "fixtures" / "weather_gas"
STATIONS = ("USW00003927", "USW00012960", "USW00094728")
T0 = pd.Timestamp("2026-10-03T21:50:00Z")          # the moment the adopted station files were copied
TFRED = pd.Timestamp("2026-10-03T22:12:08Z")
T1 = pd.Timestamp("2026-10-11T16:05:00Z")          # first daily fetch (a Sunday)
T2 = pd.Timestamp("2026-10-12T15:00:00Z")          # the next morning
T3 = pd.Timestamp("2026-10-15T14:00:00Z")          # after EIA's next release
STAMP_A = pd.Timestamp("2026-10-07T13:14:54Z")     # EIA's page footer 10/7/2026 9:14:54 AM Eastern
STAMP_B = pd.Timestamp("2026-10-15T13:12:10Z")     # 10/15/2026 9:12:10 AM Eastern
DAY = pd.Timedelta(days=1)


@pytest.fixture(autouse=True)
def _close_lake_handles():
    yield
    lake.close_all()


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    def refuse(*a, **k):
        raise AssertionError("tests/test_weather_gas_fetch.py must not touch the network")
    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket.socket, "connect_ex", refuse)
    monkeypatch.setattr(socket, "getaddrinfo", refuse)
    flows._LAST_DAILY.clear()


# ------------------------------------------------------------------------------------------------ helpers
class Server:
    """A recorded NCEI / EIA: serves fixed bytes by host, remembers every request."""

    def __init__(self, ncei: bytes = b"", eia: bytes = b"", ncei_status: int = 200, eia_status: int = 200, last_modified: str | None = None):
        self.ncei, self.eia, self.ncei_status, self.eia_status, self.last_modified = ncei, eia, ncei_status, eia_status, last_modified
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if request.url.host.endswith("ncei.noaa.gov"):
            return httpx.Response(self.ncei_status, content=self.ncei, headers={"content-type": "text/csv;charset=utf-8"})
        if request.url.host.endswith("eia.gov"):
            h = {"content-type": "text/html"}
            if self.last_modified:
                h["Last-Modified"] = self.last_modified
            return httpx.Response(self.eia_status, content=self.eia, headers=h)
        return httpx.Response(404)

    def fetcher(self, retries: int = 0) -> Fetcher:
        return Fetcher(httpx.Client(transport=httpx.MockTransport(self), headers={"User-Agent": A.FETCH_USER_AGENT}), min_spacing_s=0,
                       retries=retries, sleep=lambda s: None)

    def ncei_queries(self) -> list[dict]:
        return [dict(r.url.params) for r in self.requests if r.url.host.endswith("ncei.noaa.gov")]


def fx(name: str) -> bytes:
    return (FIX / name).read_bytes()


def put_adopted(weather_rows: bool = True, gas_rows: bool = True) -> None:
    """The history the platform adopts from research-data, as the lake holds it: a file per station / the FRED csv, named with the copy time."""
    if weather_rows:
        for st in STATIONS:
            lake.put("ncei", "GHCND", T0, f"{st}.asof_{adopt.asof_tag(T0)}.csv", FIX / f"adopted_{st}.csv", f"adopt:research-data/weather/{st}.csv")
    if gas_rows:
        lake.put("fred", "DHHNGSP", TFRED, f"DHHNGSP.asof_{adopt.asof_tag(TFRED)}.csv", FIX / "adopted_DHHNGSP.csv", "adopt:research-data/gas/DHHNGSP.csv")


def hash_parsed(*datasets: str) -> dict:
    return {p.relative_to(paths_mod.PARSED).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest() for d in datasets for p in parse.partition_files(d)}


def versions(df: pd.DataFrame, station: str, day: str) -> pd.DataFrame:
    return df[(df["station"] == station) & (df["obs_date"] == day)].sort_values("published_utc")


def fetch_weather(server: Server, now: pd.Timestamp, **kw) -> dict:
    return weather.fetch_observations(now, fetcher=server.fetcher(), stations=STATIONS, **kw)


def fetch_gas(server: Server, now: pd.Timestamp, **kw) -> dict:
    return gas.fetch_history(now, fetcher=server.fetcher(), min_rows=10, **kw)


# ================================================================================================ the stations and the request
def test_the_18_stations_are_the_research_folders_stations():
    assert len(weather.OBS_STATIONS) == 18 and set(weather.STATIONS) <= set(weather.OBS_STATIONS)
    folder = paths_mod.WEATHER_HISTORY
    if folder.exists():
        assert sorted(p.stem for p in folder.glob("USW*.csv")) == list(weather.OBS_STATIONS)       # the arena's ERCOT and NYISO stations


def test_request_follows_the_ncei_service_and_identifies_itself(dispatch_home, monkeypatch):
    """The documented query, 18 stations by default, one polite User-Agent with no email, a window from the lake's last day."""
    put_adopted()
    server = Server(ncei=fx("ncei_fetch1.csv"))
    real = httpx.Client
    monkeypatch.setattr(httpx, "Client", lambda **kw: real(transport=httpx.MockTransport(server), **kw))     # the REAL fetcher, a mock wire
    f = weather.obs_fetcher()
    assert f.min_spacing_s == A.FETCH_MIN_SPACING_S >= 1.0 and f._sem._value == 1                    # one request at a time, spaced
    out = weather.fetch_observations(T1, fetcher=f, stations=STATIONS)
    (req,) = server.requests
    assert req.method == "GET" and str(req.url).startswith("https://www.ncei.noaa.gov/access/services/data/v1?")
    q = dict(req.url.params)
    assert q == {"dataset": "daily-summaries", "stations": ",".join(STATIONS), "startDate": "2026-08-27", "endDate": "2026-10-11",
                 "dataTypes": "TMAX,TMIN,PRCP,WSF2", "units": "standard", "format": "csv"}
    ua = req.headers["user-agent"]
    assert ua == "hugging-base-dispatch (research)" and "@" not in ua and "mailto" not in str(req.headers).lower()
    assert set(req.headers) <= {"host", "accept", "accept-encoding", "connection", "user-agent"}, "no cookie, token or key travels"
    assert weather.obs_request_params(weather.OBS_STATIONS, "2026-10-01", "2026-10-02")["stations"].count(",") == 17
    assert out["start"] == "2026-08-27" and out["end"] == "2026-10-11" and out["last_day_in_lake"] == "2026-09-29"


def test_window_starts_before_the_last_day_and_never_later_than_45_days_back(dispatch_home):
    put_adopted()
    server = Server(ncei=fx("ncei_fetch1.csv"))
    # the lake's last day is 29 Sep; 14 days before is 15 Sep, but a window is at least 45 days: 27 Aug
    assert fetch_weather(server, T1)["start"] == "2026-08-27"
    # a long outage: the last day is far behind, so the window reaches back to it (minus the re-fetch days)
    assert fetch_weather(server, T1, start="2026-01-01")["start"] == "2026-01-01"
    assert fetch_weather(Server(ncei=b""), T1, start="2026-01-01", end="2026-01-31")["end"] == "2026-01-31"
    # every request that was sent
    assert [(q["startDate"], q["endDate"]) for q in server.ncei_queries()][:1] == [("2026-08-27", "2026-10-11")]


def test_the_window_ends_today_in_central_time_not_utc(dispatch_home):
    put_adopted()
    server = Server(ncei=fx("ncei_fetch1.csv"))
    out = fetch_weather(server, pd.Timestamp("2026-10-12T03:30:00Z"))              # 22:30 CDT on 11 Oct
    assert out["end"] == "2026-10-11"


def test_a_long_gap_is_split_into_requests_of_at_most_400_days(dispatch_home):
    put_adopted()
    server = Server(ncei=b'"STATION","DATE","PRCP","TMAX","TMIN","WSF2"\n')
    out = fetch_weather(server, T1, start="2023-10-01")                           # 1,107 days
    spans = [(pd.Timestamp(q["endDate"]) - pd.Timestamp(q["startDate"])).days + 1 for q in server.ncei_queries()]
    assert len(spans) == out["requests"] == 3 and max(spans) <= A.WEATHER_FETCH_MAX_SPAN_DAYS and sum(spans) == 1107
    assert out["stored"] == []                                                    # empty responses are not stored


def test_an_empty_lake_asks_for_adoption_instead_of_pulling_the_history(dispatch_home):
    with pytest.raises(FetchError, match="adopt"):
        fetch_weather(Server(ncei=fx("ncei_fetch1.csv")), T1)


# ================================================================================================ weather: raw files, parsing, publish time
def test_raw_response_is_stored_immutably_with_the_fetch_time(dispatch_home):
    put_adopted()
    server = Server(ncei=fx("ncei_fetch1.csv"))
    out = fetch_weather(server, T1)
    (st,) = out["stored"]
    assert st["new"] and st["rows"] == 39 and out["newest_obs_date"] == "2026-10-07" and out["missing_stations"] == []
    (e,) = [x for x in lake.catalog_entries("GHCND") if x["origin"] == "web"]
    assert (e["source"], e["report_id"], e["published_utc"]) == ("ncei", "GHCND", "2026-10-11T16:05:00.000000Z")
    assert e["sha256"] == hashlib.sha256(fx("ncei_fetch1.csv")).hexdigest() and e["path"].endswith(f"/2026-10-11/{st['file']}")
    assert re.fullmatch(r"GHCND\.2026-08-27_2026-10-11\.[0-9a-f]{8}\.csv", st["file"])
    assert (paths_mod.LAKE / e["path"]).read_bytes() == fx("ncei_fetch1.csv")                   # as published, byte for byte
    with pytest.raises(lake.LakeConflictError):                                                  # raw files are immutable
        lake.put("ncei", "GHCND", T2, st["file"], b"other bytes", "web")
    assert lake.verify(check_hash=True) == []
    # the lag NCEI shows: the newest day (7 Oct) was 3.46 days over when we looked, the number the notes report
    assert out["lag_days"] == pytest.approx(3.462, abs=0.001)


def test_parse_reads_the_fetched_csv(dispatch_home):
    put_adopted()
    fetch_weather(Server(ncei=fx("ncei_fetch1.csv")), T1)
    assert parse.parse_dataset("weather_obs")["errors"] == []
    wx = parse.read("weather_obs")
    assert len(wx) == 30 + (39 - 15)                                           # 15 repeated station-days (25 to 29 Sep) are not new rows
    assert wx.groupby(["station", "obs_date"]).size().max() == 1
    dfw = wx[(wx.station == "USW00003927") & (wx.obs_date == "2026-10-06")].iloc[0]
    assert (dfw.tmax_f, dfw.tmin_f, dfw.prcp_in, dfw.wsf2_mph) == (87.0, 62.0, 0.0, 10.1)         # degF, degF, inches, mph
    assert pd.isna(dfw.awnd_mph)                                               # AWND is not requested any more
    assert str(dfw.interval_start_utc) == "2026-10-06 05:00:00+00:00"          # 00:00 CDT
    nyc = wx[(wx.station == "USW00094728") & (wx.obs_date == "2026-10-07")].iloc[0]
    assert pd.isna(nyc.wsf2_mph) and nyc.tmax_f == 65.0                         # a blank cell stays NaN
    assert str(wx["published_utc"].dtype) == "datetime64[us, UTC]" and str(wx["interval_start_utc"].dtype) == "datetime64[us, UTC]"
    old = wx[wx.obs_date <= "2026-09-29"]
    assert (old.pub_basis == "ASSUMPTION").all() and old.raw_file.str.contains(".asof_").all()     # adopted rows keep their rule


def test_fetched_rows_are_published_when_first_seen(dispatch_home):
    put_adopted()
    fetch_weather(Server(ncei=fx("ncei_fetch1.csv")), T1)
    parse.parse_dataset("weather_obs")
    wx = parse.read("weather_obs")
    new = wx[wx.obs_date >= "2026-09-30"]
    assert len(new) == 24 and (new.published_utc == T1).all() and (new.pub_basis == "REAL").all()
    assert new.raw_file.str.startswith("GHCND.2026-08-27_2026-10-11.").all()
    # the adopted rows keep the rule "18:00 CPT on D+1, capped at the copy": 29 Sep is public on 30 Sep 18:00 CDT = 23:00 UTC
    last = wx[wx.obs_date == "2026-09-29"]
    assert (last.published_utc == pd.Timestamp("2026-09-30T23:00:00Z")).all() and (last.raw_file.str.contains(".asof_")).all()
    # a day is never public before it is over
    assert (wx.published_utc >= wx.interval_start_utc + DAY).all()


def test_the_lag_rule_is_a_floor_a_same_day_reading_waits_for_it(dispatch_home):
    """A response that carries today's reading (it cannot be final) is published at the rule's time, 18:00 CPT on D+1, not at the fetch."""
    put_adopted()
    today = b'"STATION","DATE","PRCP","TMAX","TMIN","WSF2"\n"USW00003927","2026-10-07","0.00","88","63","13.0"\n"USW00003927","2026-10-11","0.00","90","70","12.0"\n'
    fetch_weather(Server(ncei=today), T1)
    parse.parse_dataset("weather_obs")
    wx = parse.read("weather_obs")
    seen = wx[wx.obs_date == "2026-10-07"].iloc[0]
    same_day = wx[wx.obs_date == "2026-10-11"].iloc[0]
    assert seen.published_utc == T1 and seen.pub_basis == "REAL"
    assert same_day.published_utc == pd.Timestamp("2026-10-12T23:00:00Z") and same_day.pub_basis == "ASSUMPTION"
    assert same_day.published_utc > T1


def test_adopted_files_alone_parse_as_before(dispatch_home):
    """The version logic must not touch history: one row per station-day, the rule time, ASSUMPTION, nothing dropped."""
    put_adopted()
    res = parse.parse_dataset("weather_obs")
    wx = parse.read("weather_obs")
    assert res["rows"] == len(wx) == 30 and (wx.pub_basis == "ASSUMPTION").all()
    day = pd.to_datetime(wx["obs_date"]).dt.tz_localize("America/Chicago", ambiguous=False)
    rule = (day + pd.Timedelta(days=A.WEATHER_OBS_PUBLISH_DAYS_AFTER, hours=A.WEATHER_OBS_PUBLISH_LOCAL_HOUR)).dt.tz_convert("UTC")
    assert (wx["published_utc"] == rule.where(rule <= T0, T0)).all() and (wx["published_utc"] <= T0).all()
    assert parse.parse_dataset("gas_hh")["errors"] == []
    gs = parse.read("gas_hh")
    raw = pd.read_csv(FIX / "adopted_DHHNGSP.csv")
    assert len(gs) == int(pd.to_numeric(raw["DHHNGSP"], errors="coerce").notna().sum()) and (gs.pub_basis == "ASSUMPTION").all()


# ================================================================================================ weather: revisions and point in time
def test_a_revision_keeps_both_raw_files_and_both_versions(dispatch_home):
    put_adopted()
    s1, s2 = Server(ncei=fx("ncei_fetch1.csv")), Server(ncei=fx("ncei_fetch2.csv"))
    fetch_weather(s1, T1)
    parse.parse_dataset("weather_obs")
    out2 = fetch_weather(s2, T2)
    assert out2["changed"] and out2["newest_obs_date"] == "2026-10-08" and out2["last_day_in_lake"] == "2026-10-07"
    raw = [x for x in lake.catalog_entries("GHCND") if x["origin"] == "web"]
    assert len(raw) == 2 and len({x["path"] for x in raw}) == 2 and [x["published_utc"][:19] for x in raw] == ["2026-10-11T16:05:00", "2026-10-12T15:00:00"]
    for x in raw:                                                                       # the older file is untouched
        assert hashlib.sha256((paths_mod.LAKE / x["path"]).read_bytes()).hexdigest() == x["sha256"]
    parse.parse_dataset("weather_obs")
    wx = parse.read("weather_obs")
    assert len(wx) == 54 + 2 + 3                                                       # two revised station-days, three new days
    dfw = versions(wx, "USW00003927", "2026-10-06")
    assert list(dfw.tmax_f) == [87.0, 88.0] and list(dfw.published_utc) == [T1, T2]
    nyc = versions(wx, "USW00094728", "2026-10-07")
    assert len(nyc) == 2 and pd.isna(nyc.wsf2_mph.iloc[0]) and nyc.wsf2_mph.iloc[1] == 12.1 and list(nyc.published_utc) == [T1, T2]
    counts = wx.groupby(["station", "obs_date"]).size()
    assert (counts == 2).sum() == 2 and (counts == 1).sum() == len(counts) - 2          # every other station-day appears once
    assert (wx[wx.obs_date == "2026-10-08"].published_utc == T2).all()


def test_a_value_is_not_visible_before_its_published_utc(dispatch_home):
    put_adopted()
    fetch_weather(Server(ncei=fx("ncei_fetch1.csv")), T1)
    parse.parse_dataset("weather_obs")
    fetch_weather(Server(ncei=fx("ncei_fetch2.csv")), T2)
    parse.parse_dataset("weather_obs")

    def at(asof):
        return catalog.observations_asof("weather_obs", asof)

    def val(df, station, day, col):
        r = df[(df.station == station) & (df.obs_date == day)]
        return None if r.empty else r.iloc[0][col]

    before = at(T1 - pd.Timedelta(microseconds=1))
    assert before.obs_date.max() == "2026-09-29" and val(before, "USW00003927", "2026-10-06", "tmax_f") is None      # not yet public
    first = at(T1)                                                                      # published_utc <= asof is inclusive
    assert val(first, "USW00003927", "2026-10-06", "tmax_f") == 87.0 and first.obs_date.max() == "2026-10-07"
    assert pd.isna(val(first, "USW00094728", "2026-10-07", "wsf2_mph"))
    still = at(T2 - pd.Timedelta(microseconds=1))
    assert val(still, "USW00003927", "2026-10-06", "tmax_f") == 87.0 and val(still, "USW00003927", "2026-10-08", "tmax_f") is None
    second = at(T2)                                                                     # the latest-published value that is visible
    assert val(second, "USW00003927", "2026-10-06", "tmax_f") == 88.0 and val(second, "USW00094728", "2026-10-07", "wsf2_mph") == 12.1
    assert val(second, "USW00003927", "2026-10-08", "tmax_f") == 89.0
    assert at(T2).groupby(["station", "obs_date"]).size().max() == 1                     # one row per station-day, whatever the number of versions
    assert len(at(pd.Timestamp("2026-09-01T00:00:00Z"))) == 0


def test_latest_visible_is_a_pure_function_of_published_utc():
    df = pd.DataFrame({"station": ["a", "a", "a", "b"], "interval_start_utc": pd.to_datetime(["2026-10-01"] * 4, utc=True),
                       "published_utc": pd.to_datetime(["2026-10-03", "2026-10-05", "2026-10-04", "2026-10-09"], utc=True), "v": [1, 3, 2, 9]})
    keys = ["station", "interval_start_utc"]
    f = lambda t: catalog.latest_visible(df, keys, pd.Timestamp(t, tz="UTC"))
    assert len(f("2026-10-02")) == 0
    assert list(f("2026-10-03").v) == [1] and list(f("2026-10-04").v) == [2]            # not the row order, the publish order
    assert list(f("2026-10-08").v) == [3] and list(f("2026-10-09").v) == [3, 9]
    with pytest.raises(KeyError):
        catalog.observations_asof("rt_spp", T1)


def test_refetching_the_same_bytes_changes_nothing(dispatch_home):
    put_adopted()
    server = Server(ncei=fx("ncei_fetch1.csv"))
    fetch_weather(server, T1)
    parse.parse_dataset("weather_obs")
    lines, hashes, frame = len(lake.catalog_entries()), hash_parsed("weather_obs"), parse.read("weather_obs")
    again = fetch_weather(server, T1 + pd.Timedelta(hours=3))                          # same window (same day), same bytes
    assert again["changed"] is False and again["stored"][0]["new"] is False
    assert len(lake.catalog_entries()) == lines
    (e,) = [x for x in lake.catalog_entries("GHCND") if x["origin"] == "web"]
    assert e["published_utc"] == "2026-10-11T16:05:00.000000Z"                          # the first fetch time stays: that is when it was first seen
    parse.parse_dataset("weather_obs")
    assert hash_parsed("weather_obs") == hashes                                          # the parse found its inputs unchanged
    pd.testing.assert_frame_equal(parse.read("weather_obs"), frame)
    assert lake.verify(check_hash=True) == []


# ================================================================================================ weather: responses that must not enter the lake
@pytest.mark.parametrize("body,why", [
    pytest.param(fx("ncei_error_page.html"), "not a GHCN-Daily csv", id="html-error-page"),
    pytest.param(b'"STATION","DATE","TMAX"\n"USW00099999","2026-10-01","90"\n', "not requested", id="station-not-asked-for"),
    pytest.param(b'"STATION","DATE","TMAX"\n"USW00003927","2026-12-25","90"\n', "outside", id="day-after-the-window"),
    pytest.param(b'"STATION","DATE","TMAX"\n"USW00003927","2026-10-01","hot"\n', "non-numeric", id="word-for-a-number"),
    pytest.param(b'"STATION","DATE","TMAX"\n"USW00003927","2026-10-01","900"\n', "outside", id="absurd-temperature"),
    pytest.param(b'"STATION","DATE","TMAX"\n"USW00003927","2026-10-01","90"\n"USW00003927","2026-10-01","91"\n', "repeats", id="duplicate-station-day"),
    pytest.param(b'"STATION","DATE","TMAX"\n"USW00003927","10/01/2026","90"\n', "YYYY-MM-DD", id="date-format"),
])
def test_bad_responses_are_rejected_and_nothing_is_stored(dispatch_home, body, why):
    put_adopted()
    lines = len(lake.catalog_entries())
    with pytest.raises(FetchError, match=why):
        fetch_weather(Server(ncei=body), T1)
    assert len(lake.catalog_entries()) == lines


def test_an_oversized_response_is_refused(dispatch_home):
    put_adopted()
    lines = len(lake.catalog_entries())
    with pytest.raises(FetchError, match="limit"):
        fetch_weather(Server(ncei=b"x" * (A.FETCH_MAX_BYTES + 1)), T1)
    assert len(lake.catalog_entries()) == lines


def test_http_errors_are_retried_then_raised_and_nothing_is_stored(dispatch_home):
    put_adopted()
    lines = len(lake.catalog_entries())
    server = Server(ncei=b"gateway", ncei_status=503)
    with pytest.raises(FetchError, match="giving up after 3 tries"):
        weather.fetch_observations(T1, fetcher=server.fetcher(retries=2), stations=STATIONS)
    assert len(server.requests) == 3
    with pytest.raises(FetchError, match="HTTP 404"):
        weather.fetch_observations(T1, fetcher=Server(ncei=b"", ncei_status=404).fetcher(), stations=STATIONS)
    assert len(lake.catalog_entries()) == lines


def test_no_new_rows_is_not_an_error_and_stores_nothing(dispatch_home):
    put_adopted()
    out = fetch_weather(Server(ncei=b""), T1)
    assert out["stored"] == [] and out["changed"] is False and out["newest_obs_date"] is None
    assert [x for x in lake.catalog_entries("GHCND") if x["origin"] == "web"] == []


# ================================================================================================ gas: the page
def test_the_eia_page_parses_with_weeks_holidays_and_the_year_boundary():
    page = gas.parse_history_html(fx("eia_page_release_1007.htm"))
    d = dict(zip(page.obs_date, page.price))
    assert len(page) == 36 and page.obs_date.is_monotonic_increasing and not page.obs_date.duplicated().any()
    assert d["2025-11-24"] == 4.15 and d["2025-11-26"] == 4.59 and "2025-11-27" not in d and "2025-11-28" not in d      # Thanksgiving: blank cells
    assert [d[x] for x in ("2025-12-29", "2025-12-30", "2025-12-31")] == [4.35, 4.40, 4.00]                          # a week that crosses the year
    assert "2026-01-01" not in d and "2026-01-02" not in d
    assert "2026-01-19" not in d and d["2026-01-20"] == 4.00 and d["2026-01-23"] == 30.72                              # a blank Monday, the cold-snap spike
    assert "2026-09-07" not in d and d["2026-09-08"] == 2.90                                                           # Labor Day
    assert d["2026-10-02"] == 2.90 and d["2026-10-05"] == 2.96 and d["2026-10-06"] == 3.03 and "2026-10-07" not in d
    assert all(pd.Timestamp(x).dayofweek < 5 for x in d), "prices fall on weekdays"
    assert page.price.dtype == "float64"


def test_the_page_stamp_and_release_dates():
    body = fx("eia_page_release_1007.htm")
    assert gas.page_stamp(body) == STAMP_A                                                       # 9:14:54 AM Eastern (EDT) = 13:14:54 GMT
    assert gas.release_dates(body) == ("2026-10-07", "2026-10-15")                               # the next release is a Thursday: a holiday week
    assert gas.page_stamp(fx("eia_page_release_1015.htm")) == STAMP_B and gas.release_dates(fx("eia_page_release_1015.htm")) == ("2026-10-15", "2026-10-21")
    stripped = re.sub(rb"<!--\s*10/7/2026 9:14:54 AM\s*-->", b"", body)
    assert gas.page_stamp(stripped) is None
    assert gas.release_stamp(stripped, "Wed, 07 Oct 2026 13:14:54 GMT") == STAMP_A                  # the header says the same instant
    assert gas.release_stamp(stripped, "not a date") is None and gas.release_stamp(stripped) is None
    winter = body.replace(b"10/7/2026 9:14:54 AM", b"12/2/2026 9:14:54 AM")
    assert gas.page_stamp(winter) == pd.Timestamp("2026-12-02T14:14:54Z")                           # EST is UTC-5


@pytest.mark.parametrize("mutate,why", [
    (lambda b: b.replace(b"Henry Hub Natural Gas Spot Price", b"Something Else"), "not the Henry Hub"),
    (lambda b: b.replace(b"2025 Nov-24 to Nov-28", b"2025 Nov-25 to Nov-29"), "Monday"),
    (lambda b: b.replace(b"<td class='B3'>4.15</td>", b"", 1), "cells, expected 5"),
    (lambda b: b.replace(b"<td class='B3'>4.15</td>", b"<td class='B3'>n/a</td>", 1), "neither a price"),
    (lambda b: b.replace(b"<td class='B3'>4.15</td>", b"<td class='B3'>4,15</td>", 1), "neither a price"),
])
def test_a_page_that_changed_shape_is_refused_loudly(mutate, why):
    with pytest.raises(gas.GasPageError, match=why):
        gas.parse_history_html(mutate(fx("eia_page_release_1007.htm")))


def test_the_page_reader_agrees_with_fred_on_every_shared_day():
    """The EIA page and the adopted FRED copy are one series: every trade date they share has the same price."""
    page = gas.parse_history_html(fx("eia_page_release_1007.htm"))
    fred = pd.read_csv(FIX / "adopted_DHHNGSP.csv", dtype=str)
    fred = fred[pd.to_numeric(fred["DHHNGSP"], errors="coerce").notna()].rename(columns={"DATE": "obs_date"})
    m = page.merge(fred, on="obs_date")
    assert len(m) >= 30 and (m.price == m.DHHNGSP.astype(float)).all()
    assert set(page.obs_date) - set(fred.obs_date) == {"2026-09-30", "2026-10-01", "2026-10-02", "2026-10-05", "2026-10-06"}     # the days FRED's copy does not have yet


# ================================================================================================ gas: publish time, versions, re-fetch
def test_gas_page_is_published_at_its_release_stamp_capped_at_the_fetch(dispatch_home):
    server = Server(eia=fx("eia_page_release_1007.htm"))
    out = fetch_gas(server, T1)                                                          # fetched four days after the release
    assert out["published_utc"] == "2026-10-07T13:14:54.000000Z" and out["stamp_basis"] == "page footer" and out["new"]
    assert out["release_date"] == "2026-10-07" and out["next_release_date"] == "2026-10-15" and out["newest_obs_date"] == "2026-10-06"
    (e,) = lake.catalog_entries("RNGWHHD")
    assert (e["source"], e["origin"], e["published_utc"]) == ("eia", "web", "2026-10-07T13:14:54.000000Z")
    assert re.fullmatch(r"rngwhhdD\.rel20261007\.[0-9a-f]{8}\.htm", Path(e["path"]).name) and e["path"].startswith("raw/eia/RNGWHHD/2026-10-07/")
    assert (paths_mod.LAKE / e["path"]).read_bytes() == fx("eia_page_release_1007.htm")
    assert [q.url.path for q in server.requests] == ["/dnav/ng/hist/rngwhhdD.htm"] and server.requests[0].headers["user-agent"] == A.FETCH_USER_AGENT
    # a clock earlier than the stamp (skew) cannot make the page older than we held it
    early = pd.Timestamp("2026-10-07T12:00:00Z")
    out2 = fetch_gas(Server(eia=fx("eia_page_release_1007.htm").replace(b"<td class='B3'>2.96</td>", b"<td class='B3'>2.97</td>")), early)
    assert out2["published_utc"] == "2026-10-07T12:00:00.000000Z"


def test_gas_stamp_falls_back_to_last_modified_then_to_the_fetch_time(dispatch_home):
    plain = re.sub(rb"<!--\s*10/7/2026 9:14:54 AM\s*-->", b"", fx("eia_page_release_1007.htm"))
    a = fetch_gas(Server(eia=plain, last_modified="Wed, 07 Oct 2026 13:14:54 GMT"), T1)
    assert a["published_utc"] == "2026-10-07T13:14:54.000000Z" and a["stamp_basis"] == "Last-Modified"
    b = fetch_gas(Server(eia=plain.replace(b"3.03", b"3.04")), T1)
    assert b["published_utc"] == "2026-10-11T16:05:00.000000Z" and b["stamp_basis"] == "fetch time"


def test_gas_rows_are_published_with_their_page_and_repeats_are_dropped(dispatch_home):
    put_adopted(weather_rows=False)
    fetch_gas(Server(eia=fx("eia_page_release_1007.htm")), T1)
    assert parse.parse_dataset("gas_hh")["errors"] == []
    gs = parse.read("gas_hh")
    fred_rows = int(pd.to_numeric(pd.read_csv(FIX / "adopted_DHHNGSP.csv")["DHHNGSP"], errors="coerce").notna().sum())
    assert len(gs) == fred_rows + 5 and gs.obs_date.is_unique                              # the page's 36 prices: 31 are repeats, 5 are news
    new = gs[gs.obs_date >= "2026-09-30"]
    assert list(new.obs_date) == ["2026-09-30", "2026-10-01", "2026-10-02", "2026-10-05", "2026-10-06"] and list(new.price_usd_per_mmbtu) == [3.02, 3.00, 2.90, 2.96, 3.03]
    assert (new.published_utc == STAMP_A).all() and (new.pub_basis == "REAL").all() and new.raw_file.str.startswith("rngwhhdD.rel20261007.").all()
    old = gs[gs.obs_date <= "2026-09-29"]
    assert (old.pub_basis == "ASSUMPTION").all() and (old.published_utc <= TFRED).all()     # the adopted days keep the adopted rule
    assert str(new.iloc[0].interval_start_utc) == "2026-09-30 05:00:00+00:00"
    assert (gs.published_utc >= gs.interval_start_utc + DAY).all()                           # no price before its trade day is over


def test_gas_revision_keeps_both_pages_and_both_versions(dispatch_home):
    put_adopted(weather_rows=False)
    fetch_gas(Server(eia=fx("eia_page_release_1007.htm")), T1)
    parse.parse_dataset("gas_hh")
    out = fetch_gas(Server(eia=fx("eia_page_release_1015.htm")), T3)
    assert out["published_utc"] == "2026-10-15T13:12:10.000000Z" and out["newest_obs_date"] == "2026-10-13" and out["new"]
    raw = lake.catalog_entries("RNGWHHD")
    assert len(raw) == 2 and len({x["path"] for x in raw}) == 2 and [x["published_utc"][:19] for x in raw] == ["2026-10-07T13:14:54", "2026-10-15T13:12:10"]
    parse.parse_dataset("gas_hh")
    gs = parse.read("gas_hh")
    sep30 = gs[gs.obs_date == "2026-09-30"].sort_values("published_utc")
    assert list(sep30.price_usd_per_mmbtu) == [3.02, 3.03] and list(sep30.published_utc) == [STAMP_A, STAMP_B]
    fresh = gs[gs.obs_date.isin(["2026-10-07", "2026-10-08", "2026-10-09", "2026-10-13"])]
    assert sorted(fresh.obs_date) == ["2026-10-07", "2026-10-08", "2026-10-09", "2026-10-13"] and (fresh.published_utc == STAMP_B).all()
    assert gs.groupby("obs_date").size().max() == 2 and (gs.groupby("obs_date").size() == 2).sum() == 1      # only 30 Sep has two versions
    # point in time
    px = lambda asof, day: catalog.observations_asof("gas_hh", asof).set_index("obs_date").price_usd_per_mmbtu.get(day)
    assert px(STAMP_A - pd.Timedelta(seconds=1), "2026-09-30") is None                       # 30 Sep is not public until the 10/7 release
    assert px(STAMP_A, "2026-09-30") == 3.02 and px(STAMP_B - pd.Timedelta(seconds=1), "2026-09-30") == 3.02
    assert px(STAMP_B, "2026-09-30") == 3.03 and px(STAMP_B - pd.Timedelta(seconds=1), "2026-10-13") is None and px(STAMP_B, "2026-10-13") == 3.20


def test_refetching_the_same_gas_page_changes_nothing(dispatch_home):
    put_adopted(weather_rows=False)
    server = Server(eia=fx("eia_page_release_1007.htm"))
    first = fetch_gas(server, T1)
    parse.parse_dataset("gas_hh")
    lines, hashes = len(lake.catalog_entries()), hash_parsed("gas_hh")
    again = fetch_gas(server, T1 + pd.Timedelta(days=2))
    assert again["new"] is False and again["file"] == first["file"] and again["published_utc"] == first["published_utc"]
    assert len(lake.catalog_entries()) == lines
    parse.parse_dataset("gas_hh")
    assert hash_parsed("gas_hh") == hashes and lake.verify(check_hash=True) == []


def test_bad_gas_pages_are_rejected_and_nothing_is_stored(dispatch_home):
    page = fx("eia_page_release_1007.htm")
    with pytest.raises(FetchError, match="not the Henry Hub"):
        fetch_gas(Server(eia=fx("ncei_error_page.html")), T1)
    with pytest.raises(FetchError, match="at least 1000"):
        gas.fetch_history(T1, fetcher=Server(eia=page).fetcher())                               # a 36-price page is not the 5,700-price history
    with pytest.raises(FetchError, match="in the future"):
        fetch_gas(Server(eia=page), pd.Timestamp("2026-10-05T12:00:00Z"))                        # a price dated after the clock
    with pytest.raises(FetchError, match="HTTP 503"):
        fetch_gas(Server(eia=b"down", eia_status=503), T1)
    with pytest.raises(FetchError, match="limit"):
        fetch_gas(Server(eia=b"x" * (A.FETCH_MAX_BYTES + 1)), T1)
    assert lake.catalog_entries("RNGWHHD") == []


# ================================================================================================ freshness
def test_freshness_drops_from_twelve_days_to_the_publish_lag(dispatch_home):
    """The lead's check: the data in the lake ends on 29 Sep (12.5 days before 11 Oct); after the fetch the newest day is 7 Oct (3.5 days over at the
    fetch, NCEI's own lag) and the newest gas price is 6 Oct, released 7 Oct."""
    put_adopted()
    parse.parse_all(["weather_obs", "gas_hh"])
    day_age = lambda ds: (T1 - parse.read(ds)["interval_start_utc"].max()) / DAY
    assert day_age("weather_obs") == pytest.approx(12.46, abs=0.01) and day_age("gas_hh") == pytest.approx(12.46, abs=0.01)
    before = catalog.freshness(T1, ["GHCND", "RNGWHHD", "DHHNGSP"])
    assert before["GHCND"] / 86400 == pytest.approx(10.7, abs=0.1)                                    # the newest observation became public on 30 Sep
    assert before["RNGWHHD"] == before["DHHNGSP"] and before["RNGWHHD"] / 86400 == pytest.approx(7.7, abs=0.1)     # not fetched yet: borrows the adopted file
    out = flows.ingest_daily(now_utc=T1, weather_fetcher=Server(ncei=fx("ncei_fetch1.csv")).fetcher(), gas_fetcher=Server(eia=fx("eia_page_release_1007.htm")).fetcher(),
                             gas_min_rows=10, force=True)
    assert out["weather"]["parsed"]["errors"] == [] and out["gas"]["parsed"]["errors"] == []
    assert day_age("weather_obs") == pytest.approx(4.46, abs=0.01) and day_age("gas_hh") == pytest.approx(5.46, abs=0.01)    # 7 Oct and 6 Oct
    after = catalog.freshness(T1, ["GHCND", "RNGWHHD", "DHHNGSP"])
    assert after["GHCND"] == 0.0                                                                      # the newest observation was first seen at this very fetch
    assert after["RNGWHHD"] == (T1 - STAMP_A).total_seconds() and after["RNGWHHD"] / 86400 == pytest.approx(4.12, abs=0.01)   # released 4.1 days earlier
    assert after["DHHNGSP"] == before["DHHNGSP"]                                                      # the adopted file's own age is unchanged
    later = catalog.freshness(T1 + pd.Timedelta(days=2), ["GHCND", "RNGWHHD"])                         # nothing new for two days: both age
    assert later["GHCND"] == 2 * 86400 and later["RNGWHHD"] == after["RNGWHHD"] + 2 * 86400


def test_ghcnd_age_is_the_data_not_the_file(dispatch_home):
    """A fetch that brought nothing new must not make the weather look fresh: file time says 0 s, the data says 10 days."""
    put_adopted()
    parse.parse_dataset("weather_obs")
    adopted_only = b'"STATION","DATE","PRCP","TMAX","TMIN","WSF2"\n"USW00003927","2026-09-29","0.00","95","77","21.9"\n'
    fetch_weather(Server(ncei=adopted_only), T1)                                                       # a file stamped at T1, with a day we already had
    parse.parse_dataset("weather_obs")
    file_age = (T1 - max(pd.Timestamp(e["published_utc"]) for e in lake.catalog_entries("GHCND"))).total_seconds()
    assert file_age == 0.0
    assert catalog.freshness(T1, ["GHCND"])["GHCND"] / 86400 == pytest.approx(10.7, abs=0.1)
    assert catalog.raw_summary(T1).set_index("report_id").loc["GHCND", "age_s"] == 0.0                # the raw summary still tells when we last fetched


def test_freshness_of_a_lake_that_never_fetched_still_lists_the_gas_report(dispatch_home):
    put_adopted()
    assert {"GHCND", "RNGWHHD", "DHHNGSP"} <= set(catalog.freshness(T1))
    assert "RNGWHHD" not in catalog.freshness(T1, ["NP6-905-CD"])


# ================================================================================================ flows
def test_ingest_daily_fetches_parses_and_isolates_failures(dispatch_home):
    put_adopted()
    ok = Server(eia=fx("eia_page_release_1007.htm"))
    down = Server(ncei=b"down", ncei_status=503)
    out = flows.ingest_daily(now_utc=T1, weather_fetcher=down.fetcher(), gas_fetcher=ok.fetcher(), gas_min_rows=10, force=True)
    assert out["weather"]["error"].startswith("FetchError") and "stored" not in out["weather"]
    assert out["gas"]["changed"] and out["gas"]["parsed"]["written"] > 0 and out["gas"]["parsed"]["errors"] == []     # the gas still ran
    assert [x for x in lake.catalog_entries("GHCND") if x["origin"] == "web"] == []
    out2 = flows.ingest_daily(now_utc=T1, weather_fetcher=Server(ncei=fx("ncei_fetch1.csv")).fetcher(), gas_fetcher=ok.fetcher(), gas_min_rows=10, force=True)
    assert out2["weather"]["changed"] and out2["weather"]["parsed"]["rows"] == 54
    assert out2["gas"]["changed"] is False                                                             # the same page again


def test_ingest_daily_asks_a_source_once_per_gap_per_process(dispatch_home):
    put_adopted()
    s = Server(ncei=fx("ncei_fetch1.csv"), eia=fx("eia_page_release_1007.htm"))
    first = flows.ingest_daily(now_utc=T1, weather_fetcher=s.fetcher(), gas_fetcher=s.fetcher(), gas_min_rows=10, do_parse=False)
    n = len(s.requests)
    second = flows.ingest_daily(now_utc=T1, weather_fetcher=s.fetcher(), gas_fetcher=s.fetcher(), gas_min_rows=10, do_parse=False)
    assert n == 2 and len(s.requests) == 2 and "skipped" in second["weather"] and "skipped" in second["gas"]
    third = flows.ingest_daily(now_utc=T1, weather_fetcher=s.fetcher(), gas_fetcher=s.fetcher(), gas_min_rows=10, do_parse=False, force=True)
    assert len(s.requests) == 4 and third["weather"]["changed"] is False
    only = flows.ingest_daily(now_utc=T1, gas_on=False, weather_fetcher=s.fetcher(), force=True, do_parse=False)
    assert "gas" not in only and "weather" in only and first["weather"]["stored"]


def test_ingest_once_runs_the_daily_fetch_only_when_asked(dispatch_home, monkeypatch):
    calls = []
    monkeypatch.setattr(ercot, "ingest_once", lambda ids, **kw: calls.append(list(ids)) or {})
    monkeypatch.setattr(flows, "ingest_daily", lambda **kw: calls.append("daily") or {"weather": {}, "gas": {}})
    flows.ingest_once(["NP4-190-CD"])
    assert calls == [["NP4-190-CD"]]
    calls.clear()
    out = flows.ingest_once(["NP4-190-CD"], daily=True)
    assert calls == [["NP4-190-CD"], "daily"] and "daily" in out


def test_the_web_reports_never_reach_the_ercot_downloader(dispatch_home, monkeypatch):
    """ercot.ingest_once() and backfill_all() choose their own default ids from the registry; flows always passes explicit MIS ids."""
    seen = []
    monkeypatch.setattr(ercot, "ingest_once", lambda ids, **kw: seen.append(("ingest", list(ids))) or {})
    monkeypatch.setattr(ercot, "backfill_all", lambda ids, **kw: seen.append(("backfill", list(ids))) or {})
    monkeypatch.setattr(parse, "parse_all", lambda *a, **k: {})
    flows.ingest_once()
    flows.backfill_all()
    flows.backfill_all(include_archives=False)
    for kind, ids in seen:
        assert ids and not {"GHCND", "RNGWHHD"} & set(ids) and all(reports.get(i).kind == "mis" for i in ids), (kind, ids)
    assert seen[0][1] == list(reports.INGEST_IDS) == reports.mis_ingest_ids() and "NP6-905-CD" in seen[0][1]
    assert set(reports.mis_ingest_ids(include_archives=True)) - set(reports.mis_ingest_ids()) == set(reports.ARCHIVE_IDS)
    assert set(reports.WEB_IDS) == {"GHCND", "RNGWHHD"} and not set(reports.WEB_IDS) & set(reports.INGEST_IDS)


def test_the_daily_command_line(dispatch_home, monkeypatch, capsys):
    seen = {}
    monkeypatch.setattr(flows, "ingest_daily", lambda **kw: seen.update(kw) or {"weather": {"rows": 1}, "gas": {"rows": 2}})
    assert flows.main(["daily", "--refetch-days", "30", "--no-parse"]) == 0
    assert seen["force"] is True and seen["refetch_days"] == 30 and seen["do_parse"] is False
    monkeypatch.setattr(flows, "ingest_daily", lambda **kw: {"weather": {"error": "FetchError: down"}, "gas": {}})
    assert flows.main(["daily"]) == 1                                                                   # an error in any source is a failing exit code


# ================================================================================================ monitoring: alert only
def test_weather_and_gas_are_watched_daily_and_weekly_and_never_halt():
    assert C.REPORT_CADENCE_S["GHCND"] == 86400 and C.REPORT_CADENCE_S["RNGWHHD"] == 7 * 86400
    assert C.report_allowance_s("GHCND") == 86400 + 3 * 86400 + DATA_FRESHNESS_TARGET_S == 345_900      # 4 days and 5 minutes
    assert C.report_allowance_s("RNGWHHD") == 7 * 86400 + 2 * 86400 + DATA_FRESHNESS_TARGET_S == 777_900  # 9 days and 5 minutes
    assert {"GHCND", "RNGWHHD"} <= C.MONITORED_REPORTS and not {"GHCND", "RNGWHHD"} & C.HALT_ELIGIBLE_REPORTS
    assert C.HALT_ELIGIBLE_REPORTS == {"NP6-905-CD", "12301", "NP4-190-CD", "12331", "NP4-188-CD", "12329"}          # unchanged
    old = {"GHCND": 10 * 86400.0, "RNGWHHD": 30 * 86400.0}
    found = {b.subject: b for b in C.stale_data_breaches(old)}
    assert set(found) == {"GHCND", "RNGWHHD"} and not any(b.halts for b in found.values())
    assert all(b.check == "stale_data" and "(alert only)" in b.message for b in found.values())
    assert found["GHCND"].threshold == 345_900 and found["RNGWHHD"].threshold == 777_900
    assert C.stale_data_breaches({"GHCND": 345_900.0, "RNGWHHD": 777_900.0}) == []                     # exactly at the allowance is not a breach
    assert C.stale_data_breaches({"GHCND": 3.9 * 86400, "RNGWHHD": 8.5 * 86400}) == []                 # normal lateness: NCEI's lag, EIA's holiday week
    nan = C.stale_data_breaches({"GHCND": float("nan")})
    assert len(nan) == 1 and nan[0].halts is False


def test_the_monitor_sees_the_fetched_reports_through_catalog_freshness(dispatch_home):
    """The runner's stale-data pass takes `catalog.freshness` and keeps what is monitored: the two new reports, and only they alert."""
    put_adopted()
    parse.parse_all(["weather_obs", "gas_hh"])
    ages = catalog.freshness(T1 + pd.Timedelta(days=2))                                                # 2 days after the first fetch time, nothing fetched yet
    watched = {k: v for k, v in ages.items() if k in C.MONITORED_REPORTS}
    assert set(watched) == {"GHCND", "RNGWHHD"} and watched["GHCND"] / 86400 == pytest.approx(12.7, abs=0.1)
    breaches = C.stale_data_breaches(watched, allowances=C.allowances_for(watched))
    assert {b.subject for b in breaches} == {"GHCND", "RNGWHHD"} and not any(b.halts for b in breaches)
    flows.ingest_daily(now_utc=T1 + pd.Timedelta(days=2), weather_fetcher=Server(ncei=fx("ncei_fetch1.csv")).fetcher(),
                       gas_fetcher=Server(eia=fx("eia_page_release_1007.htm")).fetcher(), gas_min_rows=10, force=True)
    fresh = {k: v for k, v in catalog.freshness(T1 + pd.Timedelta(days=2)).items() if k in C.MONITORED_REPORTS}
    assert C.stale_data_breaches(fresh, allowances=C.allowances_for(fresh)) == []                       # fetched: back inside the allowances


# ================================================================================================ Gate 1 and the leakage rules
def test_gate1_the_parsed_weather_and_gas_rebuild_exactly_from_raw(dispatch_home):
    """Delete parsed/, parse again from the raw lake alone: every partition has the same sha256, with versions, revisions and both gas pages."""
    put_adopted()
    fetch_weather(Server(ncei=fx("ncei_fetch1.csv")), T1)
    fetch_weather(Server(ncei=fx("ncei_fetch2.csv")), T2)
    fetch_gas(Server(eia=fx("eia_page_release_1007.htm")), T1)
    fetch_gas(Server(eia=fx("eia_page_release_1015.htm")), T3)
    parse.parse_all(["weather_obs", "gas_hh"], strict=True)
    first = hash_parsed("weather_obs", "gas_hh")
    frames = {d: parse.read(d) for d in ("weather_obs", "gas_hh")}
    raw_before = [(e["path"], e["sha256"]) for e in lake.catalog_entries()]
    parse.clear_parsed()
    assert hash_parsed("weather_obs", "gas_hh") == {}
    parse.parse_all(["weather_obs", "gas_hh"], strict=True)
    assert hash_parsed("weather_obs", "gas_hh") == first and len(first) > 2
    for d, f in frames.items():
        pd.testing.assert_frame_equal(parse.read(d), f)
    assert [(e["path"], e["sha256"]) for e in lake.catalog_entries()] == raw_before and lake.verify(check_hash=True) == []
    # the order the files reach the parser in does not matter either (the parser orders them by publish time, not by the caller's list)
    files = list(reversed(parse.raw_files_for(parse.DATASETS["weather_obs"])))
    a = parse.parse_weather_obs(files).sort_values(["interval_start_utc", "station", "published_utc"]).reset_index(drop=True)
    b = parse.parse_weather_obs(sorted(files, key=lambda r: r.filename)).sort_values(["interval_start_utc", "station", "published_utc"]).reset_index(drop=True)
    pd.testing.assert_frame_equal(a, b)


def test_no_row_is_published_before_it_could_exist(dispatch_home):
    """The point-in-time contract on the parsed data: a value's published_utc is at or after the end of its day, and at or after the moment
    the raw file holding it was published (a row cannot be public before the file that carries it)."""
    put_adopted()
    fetch_weather(Server(ncei=fx("ncei_fetch1.csv")), T1)
    fetch_weather(Server(ncei=fx("ncei_fetch2.csv")), T2)
    fetch_gas(Server(eia=fx("eia_page_release_1007.htm")), T1)
    fetch_gas(Server(eia=fx("eia_page_release_1015.htm")), T3)
    parse.parse_all(["weather_obs", "gas_hh"], strict=True)
    when = {Path(e["path"]).name: pd.Timestamp(e["published_utc"]) for e in lake.catalog_entries()}
    for ds in ("weather_obs", "gas_hh"):
        d = parse.read(ds)
        assert (d.published_utc >= d.interval_start_utc + DAY).all(), ds
        fetched = d[~d.raw_file.str.contains(".asof_")]
        assert len(fetched) and all(p >= when[f] for p, f in zip(fetched.published_utc, fetched.raw_file)), ds
        assert not d.duplicated(["interval_start_utc", "published_utc"] + (["station"] if ds == "weather_obs" else [])).any()
    wx = parse.read("weather_obs")
    rule = (pd.to_datetime(wx.obs_date).dt.tz_localize("America/Chicago", ambiguous=False)
            + pd.Timedelta(days=A.WEATHER_OBS_PUBLISH_DAYS_AFTER, hours=A.WEATHER_OBS_PUBLISH_LOCAL_HOUR)).dt.tz_convert("UTC")
    fresh = ~wx.raw_file.str.contains(".asof_")
    assert (wx[fresh].published_utc >= rule[fresh]).all()                                              # the floor holds for every fetched row


def test_the_feature_builder_reads_first_published_versions_and_their_times(dispatch_home):
    """features/build.py takes, per day, the EARLIEST published version (what was known soonest): a revision never moves a feature, and the
    publish time it reports is the first-seen time. `catalog.observations_asof` is the latest-visible resolver (see notes/p2-weather-gas.md)."""
    from dispatch.features import build
    put_adopted()
    fetch_weather(Server(ncei=fx("ncei_fetch1.csv")), T1)
    fetch_weather(Server(ncei=fx("ncei_fetch2.csv")), T2)
    fetch_gas(Server(eia=fx("eia_page_release_1007.htm")), T1)
    fetch_gas(Server(eia=fx("eia_page_release_1015.htm")), T3)
    parse.parse_all(["weather_obs", "gas_hh"], strict=True)
    day0, nd = pd.Timestamp("2026-09-20"), 25
    lo, hi = pd.Timestamp("2026-08-01", tz="UTC"), pd.Timestamp("2026-11-01", tz="UTC")
    agg, pcm, starts, last = build._weather_series("LZ_NORTH", day0, nd, lo, hi)              # LZ_NORTH = DFW
    k = lambda day: (pd.Timestamp(day) - day0).days
    us = lambda t: int(pd.Timestamp(t).as_unit("us").value // 1000)
    assert agg["wx_tmax_f"][k("2026-10-06")] == 87.0 and agg["_pub"][k("2026-10-06")] == us(T1)       # the first version, not the revised 88
    assert agg["wx_tmax_f"][k("2026-10-08")] == 89.0 and agg["_pub"][k("2026-10-08")] == us(T2)
    assert agg["wx_tmax_f"][k("2026-09-29")] == 95.0 and agg["_pub"][k("2026-09-29")] == us("2026-09-30T23:00:00Z")
    g = build._gas_series(day0, nd, lo, hi)
    assert g.val[k("2026-09-30")] == 3.02 and g.pub[k("2026-09-30")] == us(STAMP_A)                        # the first version, not the revised 3.03
    assert g.val[k("2026-10-07")] == 3.08 and g.pub[k("2026-10-07")] == us(STAMP_B)                        # a day only the second page has
    assert g.val[k("2026-10-13")] == 3.20 and g.pub[k("2026-10-13")] == us(STAMP_B)
    # a decision between the two fetches knows the days of the first and none of the second
    asof = us(T2 - pd.Timedelta(minutes=1))
    assert (g.pub[k("2026-10-07")] > asof) and (agg["_pub"][k("2026-10-07")] <= asof) and (agg["_pub"][k("2026-10-08")] > asof)


def test_the_network_guard_works():
    with pytest.raises(AssertionError, match="must not touch the network"):
        socket.create_connection(("example.com", 80), timeout=1)
