"""The immutable raw lake and the ERCOT MIS fetch layer (PRD FR1, FR2).

Covers: immutability, idempotency, the conflict error, catalog lines, publish-time parsing from ERCOT file names including the
November repeated hour, the MIS listing parser, retries and the 4-request concurrency cap, backfill, and the API quota rule.
All with a temp DISPATCH_HOME (the `dispatch_home` fixture) and mock HTTP transports: no network, nothing outside the temp dir.
"""
from __future__ import annotations

import hashlib
import json
import threading
import zipfile
from io import BytesIO

import httpx
import pandas as pd
import pytest

from dispatch.data import assumptions as A
from dispatch.data import catalog, ercot, lake, reports
from dispatch.data.times import parse_mis_publish

NAME = "cdr.00012301.0000000000000000.20261010.101702135.SPPHLZNP6905_20261010_1015_csv.zip"


@pytest.fixture(autouse=True)
def _close_lake_handles():
    yield
    lake.close_all()


def _zip_bytes(csv_text: str = "a,b\n1,2\n", name: str = "x.csv") -> bytes:
    buf = BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(name, csv_text)
    return buf.getvalue()


def _catalog_lines(dispatch_home) -> list[dict]:
    p = dispatch_home.CATALOG
    return [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines() if x.strip()] if p.exists() else []


# ------------------------------------------------------------------------------------------------ publish time from the name
def test_publish_time_example_from_the_spec():
    """posted 2026-10-10 10:17:02.135 Central Prevailing Time = 15:17:02.135 UTC (CDT is UTC-5)."""
    t = parse_mis_publish(NAME)
    assert t == pd.Timestamp("2026-10-10 15:17:02.135", tz="UTC")
    assert t.tz is not None and str(t.tz) == "UTC"


def test_publish_time_winter_is_utc_minus_six():
    t = parse_mis_publish("cdr.00012301.0000000000000000.20261201.101702135.X_csv.zip")
    assert t == pd.Timestamp("2026-12-01 16:17:02.135", tz="UTC")


@pytest.mark.parametrize("prefix", ["cdr", "rpt", "RPT"])
def test_publish_time_accepts_every_prefix(prefix):
    assert parse_mis_publish(f"{prefix}.00010008.0000000000000000.20261001.000121352.CIM_Oct_ML1.zip") == \
        pd.Timestamp("2026-10-01 05:01:21.352", tz="UTC")


def test_publish_time_rejects_other_names():
    with pytest.raises(ValueError):
        parse_mis_publish("RTMLZHBSPP_2026.zip")
    with pytest.raises(ValueError):
        parse_mis_publish("cdr.12301.0.20261010.101702135.x.zip")


class TestDaylightSavingRule:
    """RULE (assumptions.MIS_AMBIGUOUS_IS_STANDARD_TIME): an ambiguous November wall-clock time is the SECOND occurrence
    (standard time, the LATER UTC instant), so a file can only ever look published later than it was, never earlier."""

    @staticmethod
    def _name(date, hhmmssmmm):
        return f"cdr.00012301.0000000000000000.{date}.{hhmmssmmm}.X_csv.zip"

    def test_ambiguous_hour_is_read_as_standard_time(self):
        # Sun 1 Nov 2026: 01:00-02:00 happens twice (CDT then CST)
        assert parse_mis_publish(self._name("20261101", "013000000")) == pd.Timestamp("2026-11-01 07:30:00", tz="UTC")
        assert parse_mis_publish(self._name("20261101", "010000000")) == pd.Timestamp("2026-11-01 07:00:00", tz="UTC")
        assert parse_mis_publish(self._name("20261101", "015959999")) == pd.Timestamp("2026-11-01 07:59:59.999", tz="UTC")

    def test_edges_of_the_ambiguous_hour_are_unambiguous(self):
        assert parse_mis_publish(self._name("20261101", "005959000")) == pd.Timestamp("2026-11-01 05:59:59", tz="UTC")     # CDT
        assert parse_mis_publish(self._name("20261101", "020000000")) == pd.Timestamp("2026-11-01 08:00:00", tz="UTC")     # CST

    def test_the_rule_never_makes_a_file_look_earlier_than_it_was(self):
        """Both candidate instants for every minute of the repeated hour: the rule picks the later one."""
        for minute in range(0, 60, 7):
            name = self._name("20261101", f"01{minute:02d}00000")
            got = parse_mis_publish(name)
            first = pd.Timestamp(f"2026-11-01 01:{minute:02d}:00").tz_localize("America/Chicago", ambiguous=True).tz_convert("UTC")
            second = pd.Timestamp(f"2026-11-01 01:{minute:02d}:00").tz_localize("America/Chicago", ambiguous=False).tz_convert("UTC")
            assert first < second
            assert got == second

    def test_listing_offset_can_resolve_the_ambiguity_exactly(self):
        hint_first = pd.Timestamp("2026-11-01T01:30:01-05:00")      # the JSON listing carries the true offset
        hint_second = pd.Timestamp("2026-11-01T01:30:01-06:00")
        name = self._name("20261101", "013000500")
        assert parse_mis_publish(name, hint=hint_first) == pd.Timestamp("2026-11-01 06:30:00.5", tz="UTC")
        assert parse_mis_publish(name, hint=hint_second) == pd.Timestamp("2026-11-01 07:30:00.5", tz="UTC")
        # a hint far from both candidates is ignored (the rule applies)
        assert parse_mis_publish(name, hint=pd.Timestamp("2026-11-03T00:00:00Z")) == pd.Timestamp("2026-11-01 07:30:00.5", tz="UTC")

    def test_spring_forward_gap_shifts_forward(self):
        # Sun 14 Mar 2027: 02:00-03:00 does not exist
        got = parse_mis_publish(self._name("20270314", "023000000"))
        assert got == pd.Timestamp("2027-03-14 08:30:00", tz="UTC")      # 03:30 CDT

    def test_ordinary_days_are_unaffected(self):
        assert parse_mis_publish(self._name("20261031", "013000000")) == pd.Timestamp("2026-10-31 06:30:00", tz="UTC")
        assert parse_mis_publish(self._name("20261102", "013000000")) == pd.Timestamp("2026-11-02 07:30:00", tz="UTC")

    def test_documented_constant(self):
        assert A.MIS_AMBIGUOUS_IS_STANDARD_TIME is True


# ------------------------------------------------------------------------------------------------ immutability
def test_put_layout_and_catalog_line(dispatch_home):
    data = _zip_bytes()
    pub = parse_mis_publish(NAME)
    p = lake.put("ercot", "NP6-905-CD", pub, NAME, data, "mis")
    assert p == dispatch_home.LAKE / "raw" / "ercot" / "NP6-905-CD" / "2026-10-10" / NAME
    assert p.read_bytes() == data
    (line,) = _catalog_lines(dispatch_home)
    assert list(line) == ["source", "report_id", "published_utc", "ingested_utc", "path", "bytes", "sha256", "origin"]
    assert line["source"] == "ercot" and line["report_id"] == "NP6-905-CD" and line["origin"] == "mis"
    assert line["published_utc"] == "2026-10-10T15:17:02.135000Z"
    assert line["path"] == f"raw/ercot/NP6-905-CD/2026-10-10/{NAME}"            # relative to the lake, forward slashes
    assert line["bytes"] == len(data) and line["sha256"] == hashlib.sha256(data).hexdigest()
    pd.Timestamp(line["ingested_utc"])                                           # parses


def test_date_folder_is_the_utc_date_of_publish(dispatch_home):
    name = "cdr.00012301.0000000000000000.20261010.213000000.X_csv.zip"          # 21:30 CDT = 02:30 UTC on the 11th
    p = lake.put("ercot", "NP6-905-CD", parse_mis_publish(name), name, b"PK1", "mis")
    assert p.parent.name == "2026-10-11"


def test_put_is_idempotent(dispatch_home):
    data = _zip_bytes()
    pub = parse_mis_publish(NAME)
    p1 = lake.put("ercot", "NP6-905-CD", pub, NAME, data, "mis")
    mtime = p1.stat().st_mtime_ns
    p2 = lake.put("ercot", "NP6-905-CD", pub, NAME, data, "mis")
    p3 = lake.put("ercot", 12301, pub, NAME, data, "mis")                        # the MIS reportTypeId is an alias
    assert p1 == p2 == p3
    assert p1.stat().st_mtime_ns == mtime                                        # not rewritten
    assert len(_catalog_lines(dispatch_home)) == 1                               # no second line


def test_same_name_different_bytes_raises_and_leaves_the_original(dispatch_home):
    pub = parse_mis_publish(NAME)
    p = lake.put("ercot", "NP6-905-CD", pub, NAME, b"PK-original", "mis")
    with pytest.raises(lake.LakeConflictError):
        lake.put("ercot", "NP6-905-CD", pub, NAME, b"PK-changed!!", "mis")
    assert p.read_bytes() == b"PK-original"
    assert len(_catalog_lines(dispatch_home)) == 1


def test_conflict_is_detected_even_with_another_publish_date(dispatch_home):
    lake.put("ercot", "NP6-905-CD", pd.Timestamp("2026-10-10T15:17:02Z"), NAME, b"PK-a", "mis")
    with pytest.raises(lake.LakeConflictError):
        lake.put("ercot", "NP6-905-CD", pd.Timestamp("2026-10-11T00:00:00Z"), NAME, b"PK-b", "mis")


def test_adopt_copies_and_never_touches_the_original(dispatch_home, tmp_path):
    src = tmp_path / "research" / NAME
    src.parent.mkdir()
    src.write_bytes(_zip_bytes())
    before = (src.read_bytes(), src.stat().st_mtime_ns)
    p = lake.put("ercot", "NP6-905-CD", parse_mis_publish(NAME), NAME, src, f"adopt:research-data/{NAME}")
    assert p != src and p.read_bytes() == src.read_bytes()
    assert (src.read_bytes(), src.stat().st_mtime_ns) == before and src.exists()
    assert _catalog_lines(dispatch_home)[0]["origin"].startswith("adopt:")


def test_a_cataloged_file_missing_on_disk_is_restored_with_the_same_bytes(dispatch_home):
    pub = parse_mis_publish(NAME)
    p = lake.put("ercot", "NP6-905-CD", pub, NAME, b"PK-x", "mis")
    import os, stat
    os.chmod(p, stat.S_IWRITE)
    p.unlink()
    assert lake.verify() == [f"missing: raw/ercot/NP6-905-CD/2026-10-10/{NAME}"]
    lake.put("ercot", "NP6-905-CD", pub, NAME, b"PK-x", "mis")
    assert lake.verify(check_hash=True) == [] and len(_catalog_lines(dispatch_home)) == 1


def test_file_on_disk_but_not_in_catalog_is_recovered_not_duplicated(dispatch_home):
    """A crash between writing the file and appending the catalog line must not leave the lake stuck."""
    pub = parse_mis_publish(NAME)
    dest = lake.raw_path("ercot", "NP6-905-CD", pub, NAME)
    dest.parent.mkdir(parents=True)
    dest.write_bytes(b"PK-crash")
    lake.put("ercot", "NP6-905-CD", pub, NAME, b"PK-crash", "mis")
    assert len(_catalog_lines(dispatch_home)) == 1
    other = "cdr.00012301.0000000000000000.20261010.103202135.SPPHLZNP6905_20261010_1030_csv.zip"
    dest2 = lake.raw_path("ercot", "NP6-905-CD", parse_mis_publish(other), other)
    dest2.write_bytes(b"PK-left-behind")
    with pytest.raises(lake.LakeConflictError):
        lake.put("ercot", "NP6-905-CD", parse_mis_publish(other), other, b"PK-different", "mis")


def test_concurrent_puts_write_each_file_once(dispatch_home):
    names = [f"cdr.00012301.0000000000000000.20261010.{10 + i // 6:02d}{(i % 6) * 10:02d}00000.SPPHLZNP6905_x{i}_csv.zip" for i in range(20)]
    errors = []

    def worker():
        try:
            for n in names:
                lake.put("ercot", "NP6-905-CD", parse_mis_publish(n), n, b"PK" + n.encode(), "mis")
        except Exception as e:                                                   # pragma: no cover
            errors.append(e)
    threads = [threading.Thread(target=worker) for _ in range(6)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert not errors
    lines = _catalog_lines(dispatch_home)
    assert len(lines) == len(names) and len({x["path"] for x in lines}) == len(names)
    assert lake.verify(check_hash=True) == []


@pytest.mark.parametrize("bad", ["../escape.zip", "a/b.zip", ".hidden", "", "with space.zip"])
def test_unsafe_file_names_are_refused(dispatch_home, bad):
    with pytest.raises(ValueError):
        lake.put("ercot", "NP6-905-CD", pd.Timestamp("2026-10-10T00:00:00Z"), bad, b"x", "mis")


def test_origin_must_be_one_of_the_documented_values(dispatch_home):
    for ok in ("mis", "api", "web", "adopt:research-data/x"):
        lake.put("ercot", "R", pd.Timestamp("2026-10-10T00:00:00Z"), f"f_{ok.replace(':', '_').replace('/', '_')}.zip", b"x", ok)
    with pytest.raises(ValueError):
        lake.put("ercot", "R", pd.Timestamp("2026-10-10T00:00:00Z"), "g.zip", b"x", "copy")


def test_naive_timestamps_are_refused(dispatch_home):
    with pytest.raises(ValueError):
        lake.put("ercot", "R", pd.Timestamp("2026-10-10 00:00:00"), "n.zip", b"x", "mis")


# ------------------------------------------------------------------------------------------------ iter_raw and the catalog
def test_iter_raw_orders_by_publish_time_and_filters_inclusively(dispatch_home):
    stamps = ["20261010.110000000", "20261010.100000000", "20261010.120000000"]
    for s in stamps:
        n = f"cdr.00012301.0000000000000000.{s}.X_csv.zip"
        lake.put("ercot", "NP6-905-CD", parse_mis_publish(n), n, b"PK" + s.encode(), "mis")
    lake.put("ercot", "NP4-190-CD", pd.Timestamp("2026-10-10T00:00:00Z"), "cdr.00012331.0000000000000000.20261010.000000000.D_csv.zip", b"PK-d", "mis")
    got = list(lake.iter_raw("NP6-905-CD"))
    assert [r.published_utc.hour for r in got] == [15, 16, 17]                  # 10, 11, 12 CDT
    assert [r.report_id for r in lake.iter_raw(12301)] == ["NP6-905-CD"] * 3     # numeric alias
    cut = pd.Timestamp("2026-10-10T16:00:00Z")
    assert [r.published_utc for r in lake.iter_raw("NP6-905-CD", published_before=cut)] == [pd.Timestamp("2026-10-10T15:00:00Z"), cut]
    assert all(r.path.exists() and r.sha256 and r.bytes for r in got)
    assert got[0].read_bytes().startswith(b"PK")


def test_freshness_is_measured_against_the_newest_published_time(dispatch_home):
    for hh in (10, 11):
        n = f"cdr.00012301.0000000000000000.20261010.{hh}0200000.X_csv.zip"
        lake.put("ercot", "NP6-905-CD", parse_mis_publish(n), n, b"PK" + bytes([hh]), "mis")
    n = "cdr.00012331.0000000000000000.20261009.135445053.DAMSPNP4190_csv.zip"
    lake.put("ercot", "NP4-190-CD", parse_mis_publish(n), n, b"PKdam", "mis")
    now = pd.Timestamp("2026-10-10T17:00:00Z")                                  # RT newest = 11:02 CDT = 16:02 UTC
    f = catalog.freshness(now)
    assert f["NP6-905-CD"] == pytest.approx(58 * 60)
    assert f["NP4-190-CD"] == pytest.approx((now - pd.Timestamp("2026-10-09T18:54:45.053Z")).total_seconds())
    assert set(f) == {"NP6-905-CD", "NP4-190-CD"}
    assert catalog.freshness(now, report_ids=[12331]) == {"NP4-190-CD": f["NP4-190-CD"]}


# ------------------------------------------------------------------------------------------------ report registry
def test_registry_has_the_required_ids():
    want = {"NP6-905-CD": 12301, "NP4-190-CD": 12331, "NP4-188-CD": 12329, "NP6-331-CD": 24898, "NP6-86-CD": 12302,
            "NP6-322-CD": 13114, "NP4-160-SG": 10008, "NP3-565-CD": 14837, "HIST-DAM-MCPC": 13091, "HIST-RTM-MCPC-SCED": 25569,
            "HIST-RTM-MCPC-15": 25570, "NP6-785-ER": 13061, "NP4-180-ER": 13060}
    for rid, rtid in want.items():
        assert reports.REPORTS[rid].rtid == rtid
        assert reports.canonical_id(rtid) == rid and reports.canonical_id(str(rtid)) == rid


# ------------------------------------------------------------------------------------------------ MIS listing and download
HTML = """<html><body><table>
<tr><td class='labelOptional_ind'>cdr.00012331.0000000000000000.20261010.123305243.DAMSPNP4190_csv.zip</td><td>&nbsp;</td><td>&nbsp;</td><td><a href='/misdownload/servlets/mirDownload?mimic_duns=000000000&doclookupId=1001'>zip</a></td></tr>
<tr><td class='labelOptional_ind'>cdr.00012331.0000000000000000.20261010.123304935.DAMSPNP4190_xml.zip</td><td>&nbsp;</td><td>&nbsp;</td><td><a href='/misdownload/servlets/mirDownload?mimic_duns=000000000&doclookupId=1002'>zip</a></td></tr>
<tr><td class='labelOptional_ind'>cdr.00012331.0000000000000000.20261009.135445053.DAMSPNP4190_csv.zip</td><td>&nbsp;</td><td>&nbsp;</td><td><a href='/misdownload/servlets/mirDownload?mimic_duns=000000000&doclookupId=1003'>zip</a></td></tr>
</table></body></html>"""


def test_html_listing_parser_and_csv_selection():
    docs = ercot.parse_html_listing(HTML)
    assert [d.doc_id for d in docs] == ["1001", "1002", "1003"]
    sel = ercot.select(reports.get("NP4-190-CD"), docs)
    assert [d.doc_id for d in sel] == ["1001", "1003"]                          # csv only
    assert sel[0].published_utc == pd.Timestamp("2026-10-10 17:33:05.243", tz="UTC")
    assert sel[0].url.endswith("mirDownload?doclookupId=1001")


def test_json_listing_parser_carries_the_exact_offset():
    payload = {"ListDocsByRptTypeRes": {"DocumentList": [{"Document": {
        "DocID": "55", "ConstructedName": NAME, "ContentSize": "8619", "PublishDate": "2026-10-10T10:17:01-05:00"}}]}}
    (d,) = ercot.parse_json_listing(payload)
    assert d.size == 8619 and d.listed_publish == pd.Timestamp("2026-10-10T15:17:01Z")
    assert d.published_utc == pd.Timestamp("2026-10-10T15:17:02.135Z")


def _transport(handler):
    return httpx.Client(transport=httpx.MockTransport(handler), headers={"User-Agent": ercot.USER_AGENT})


def test_fetcher_retries_with_backoff_then_succeeds():
    calls, sleeps = [], []

    def handler(request):
        calls.append(request.url)
        return httpx.Response(503 if len(calls) < 3 else 200, content=b"PKdata")
    f = ercot.Fetcher(_transport(handler), min_spacing_s=0, backoff_base_s=1.0, sleep=sleeps.append)
    assert f.get_bytes("https://example.test/x") == b"PKdata"
    assert len(calls) == 3
    backoffs = [s for s in sleeps if s >= 1.0]
    assert len(backoffs) == 2 and backoffs[1] >= 2.0                             # exponential: ~1 s, then ~2 s (plus jitter)


def test_fetcher_honours_retry_after_and_gives_up():
    sleeps = []
    f = ercot.Fetcher(_transport(lambda r: httpx.Response(429, headers={"Retry-After": "7"})), retries=2, min_spacing_s=0, sleep=sleeps.append)
    with pytest.raises(ercot.FetchError):
        f.get_bytes("https://example.test/x?secret=abc")
    assert sum(1 for s in sleeps if 7.0 <= s < 8.0) == 2
    try:
        f.get_bytes("https://example.test/x?secret=abc")
    except ercot.FetchError as e:
        assert "secret" not in str(e)                                            # the query string never reaches an error message


def test_fetcher_never_has_more_than_four_requests_in_flight():
    lock, state = threading.Lock(), {"now": 0, "peak": 0}
    barrier_hits = threading.Event()

    def handler(request):
        with lock:
            state["now"] += 1
            state["peak"] = max(state["peak"], state["now"])
        barrier_hits.wait(0.05)
        with lock:
            state["now"] -= 1
        return httpx.Response(200, content=b"PK")
    f = ercot.Fetcher(_transport(handler), min_spacing_s=0, max_concurrent=16)         # asking for 16 is capped at 4
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(16) as ex:
        list(ex.map(lambda i: f.get_bytes(f"https://example.test/{i}"), range(40)))
    assert 1 <= state["peak"] <= 4 and f.in_flight_peak <= 4


def _mock_mis(files: dict[str, bytes]):
    """A fake MIS: report 12331 lists `files` (csv + xml twins) and serves them by doclookupId."""
    ids = {name: str(2000 + i) for i, name in enumerate(files)}
    rows = "".join(f"<tr><td class='labelOptional_ind'>{n}</td><td><a href='/misdownload/servlets/mirDownload?mimic_duns=0&doclookupId={i}'>zip</a></td></tr>"
                   for n, i in ids.items())

    def handler(request):
        url = str(request.url)
        if "GetReports.do" in url:
            return httpx.Response(200, text=f"<table>{rows}</table>")
        if "mirDownload" in url:
            did = url.split("doclookupId=")[1]
            name = next(n for n, i in ids.items() if i == did)
            return httpx.Response(200, content=files[name])
        return httpx.Response(404)
    return handler


def test_backfill_downloads_only_what_the_catalog_lacks(dispatch_home):
    csv1 = "cdr.00012331.0000000000000000.20261009.135445053.DAMSPNP4190_csv.zip"
    csv2 = "cdr.00012331.0000000000000000.20261010.123305243.DAMSPNP4190_csv.zip"
    xml2 = "cdr.00012331.0000000000000000.20261010.123304935.DAMSPNP4190_xml.zip"
    files = {csv1: _zip_bytes("1"), csv2: _zip_bytes("2"), xml2: _zip_bytes("3")}
    f = ercot.Fetcher(_transport(_mock_mis(files)), min_spacing_s=0)
    r = ercot.backfill("NP4-190-CD", fetcher=f)
    assert (r["listed"], r["selected"], r["downloaded"], r["failed"]) == (3, 2, 2, [])
    assert {x.filename for x in lake.iter_raw("NP4-190-CD")} == {csv1, csv2}      # xml never taken
    assert all(x.origin == "mis" for x in lake.iter_raw("NP4-190-CD"))
    again = ercot.backfill("NP4-190-CD", fetcher=f)
    assert (again["downloaded"], again["to_fetch"], again["already_have"]) == (0, 0, 2)
    assert len(_catalog_lines(dispatch_home)) == 2
    assert r["newest_published_utc"] == "2026-10-10T17:33:05.243000Z"


def test_backfill_survives_a_bad_download_and_reports_it(dispatch_home):
    good = "cdr.00012331.0000000000000000.20261009.135445053.DAMSPNP4190_csv.zip"
    bad = "cdr.00012331.0000000000000000.20261010.123305243.DAMSPNP4190_csv.zip"
    files = {good: _zip_bytes("ok"), bad: b"<html>session expired</html>"}
    r = ercot.backfill("NP4-190-CD", fetcher=ercot.Fetcher(_transport(_mock_mis(files)), min_spacing_s=0))
    assert r["downloaded"] == 1 and len(r["failed"]) == 1 and "not a zip" in r["failed"][0]
    assert [x.filename for x in lake.iter_raw("NP4-190-CD")] == [good]


def test_ingest_once_only_looks_back_a_few_hours(dispatch_home):
    old = "cdr.00012331.0000000000000000.20261001.135445053.DAMSPNP4190_csv.zip"
    new = "cdr.00012331.0000000000000000.20261010.123305243.DAMSPNP4190_csv.zip"
    f = ercot.Fetcher(_transport(_mock_mis({old: _zip_bytes("1"), new: _zip_bytes("2")})), min_spacing_s=0)
    out = ercot.ingest_once(["NP4-190-CD"], fetcher=f, lookback=pd.Timedelta(hours=6), now=pd.Timestamp("2026-10-10T19:00:00Z"))
    assert out["NP4-190-CD"]["downloaded"] == 1
    assert [x.filename for x in lake.iter_raw("NP4-190-CD")] == [new]


# ------------------------------------------------------------------------------------------------ the API path
def test_api_path_does_nothing_without_credentials(dispatch_home, monkeypatch):
    for v in (ercot.ENV_API_USER, ercot.ENV_API_PASS, ercot.ENV_API_KEY):
        monkeypatch.delenv(v, raising=False)
    assert ercot.fetch_api_archive("NP6-905-CD", "2026-01-01", "2026-02-01") == {"report_id": "NP6-905-CD", "skipped": "no credentials"}


def test_api_quota_counts_both_ledgers(dispatch_home, monkeypatch, tmp_path):
    """3 API downloads of a report per 12 months: the lake catalog AND research-data/grid/manifest.csv both count."""
    manifest = tmp_path / "manifest.csv"
    manifest.write_text("source,report_id,url,file,bytes,sha256,downloaded_at_utc,method\n"
                        "ercot,NP6-905-CD,u1,f1,1,x,2026-03-01T10:00:00+00:00,api\n"
                        "ercot,NP6-905-CD,u2,f2,1,x,2026-03-01T11:00:00+00:00,api\n"          # same day: counts once
                        "ercot,NP6-905-CD,u3,f3,1,x,2024-01-01T11:00:00+00:00,api\n"          # older than 12 months: ignored
                        "ercot,NP6-905-CD,u4,f4,1,x,2026-04-01T11:00:00+00:00,mis\n", encoding="utf-8")
    monkeypatch.setattr(ercot.paths, "GRID_MANIFEST", manifest)
    now = pd.Timestamp("2026-10-10T00:00:00Z")
    assert ercot.api_download_days("NP6-905-CD", now) == {"2026-03-01"}
    assert ercot.api_check_quota("NP6-905-CD", now) == 1
    lake.put("ercot", "NP6-905-CD", pd.Timestamp("2026-05-05T00:00:00Z"), "a.zip", b"1", "api", ingested_utc=pd.Timestamp("2026-05-05T12:00:00Z"))
    lake.put("ercot", "NP6-905-CD", pd.Timestamp("2026-06-06T00:00:00Z"), "b.zip", b"2", "api", ingested_utc=pd.Timestamp("2026-06-06T12:00:00Z"))
    assert ercot.api_download_days("NP6-905-CD", now) == {"2026-03-01", "2026-05-05", "2026-06-06"}
    with pytest.raises(ercot.ApiQuotaError):
        ercot.api_check_quota("NP6-905-CD", now)
    assert ercot.api_check_quota("NP4-190-CD", now) == 0                          # other reports are unaffected


def test_api_refuses_to_download_when_the_quota_is_used(dispatch_home, monkeypatch, tmp_path):
    monkeypatch.setenv(ercot.ENV_API_USER, "u")
    monkeypatch.setenv(ercot.ENV_API_PASS, "p")
    monkeypatch.setenv(ercot.ENV_API_KEY, "k")
    for i, d in enumerate(("2026-02-01", "2026-03-01", "2026-04-01")):
        lake.put("ercot", "NP6-905-CD", pd.Timestamp(d + "T00:00:00Z"), f"api{i}.zip", bytes([i]), "api", ingested_utc=pd.Timestamp(d + "T12:00:00Z"))
    monkeypatch.setattr(ercot.paths, "GRID_MANIFEST", tmp_path / "none.csv")
    calls = []
    f = ercot.Fetcher(_transport(lambda r: calls.append(str(r.url)) or httpx.Response(500)), retries=0, min_spacing_s=0)
    with pytest.raises(ercot.ApiQuotaError):
        ercot.fetch_api_archive("NP6-905-CD", "2026-01-01", "2026-02-01", fetcher=f, now=pd.Timestamp("2026-10-10T00:00:00Z"))
    assert calls == []                                                           # no request, not even the token, left the machine


# ------------------------------------------------------------------------------------------------ NWS forecast snapshots and weather mapping
def _nws_handler(requests_seen):
    def handler(request):
        requests_seen.append((str(request.url), dict(request.headers)))
        url = str(request.url)
        if "/points/" in url:
            return httpx.Response(200, json={"properties": {"gridId": "FWD", "gridX": 80, "gridY": 108,
                                                            "forecastHourly": "https://api.weather.gov/gridpoints/FWD/80,108/forecast/hourly"}})
        return httpx.Response(200, json={"properties": {"generatedAt": "2026-10-10T19:02:38+00:00", "updateTime": "2026-10-10T18:30:00+00:00", "periods": [
            {"startTime": "2026-10-10T14:00:00-05:00", "temperature": 90, "temperatureUnit": "F", "windSpeed": "5 to 10 mph",
             "probabilityOfPrecipitation": {"value": 20}, "dewpoint": {"value": 13.3}, "relativeHumidity": {"value": 32}, "shortForecast": "Sunny"},
            {"startTime": "2026-10-10T15:00:00-05:00", "temperature": 91, "temperatureUnit": "F", "windSpeed": "10 mph",
             "probabilityOfPrecipitation": {"value": None}, "dewpoint": {"value": None}, "relativeHumidity": {"value": None}, "shortForecast": "Sunny"}]}})
    return handler


def test_nws_snapshot_is_stored_raw_keyed_by_issue_time_and_parses(dispatch_home):
    import numpy as np
    from dispatch.data import parse, weather
    seen = []
    f = ercot.Fetcher(_transport(_nws_handler(seen)), min_spacing_s=0)
    out = weather.snapshot_forecasts({"USW00003927": weather.STATIONS["USW00003927"]}, fetcher=f, now=pd.Timestamp("2026-10-10T20:00:00Z"))
    assert out["failed"] == {} and out["stations"]["USW00003927"]["periods"] == 2
    (rf,) = list(lake.iter_raw("FORECAST-HOURLY"))
    assert rf.filename == "DFW.20261010T190238Z.json" and rf.source == "nws" and rf.origin == "api"
    assert rf.published_utc == pd.Timestamp("2026-10-10T19:02:38Z")                      # the issue time, not the fetch time
    assert rf.path.parent.name == "2026-10-10"
    assert all(h.get("user-agent") == "hugging-base-dispatch (research)" for _, h in seen)
    assert all("@" not in u and "@" not in str(h) for u, h in seen)                      # no email or personal data leaves the machine
    res = parse.parse_dataset("weather_fcst")
    assert res["rows"] == 2 and not res["errors"]
    df = parse.read("weather_fcst")
    assert (df["published_utc"] == pd.Timestamp("2026-10-10T19:02:38Z")).all() and (df["pub_basis"] == "REAL").all()
    assert df["interval_start_utc"].tolist() == [pd.Timestamp("2026-10-10T19:00:00Z"), pd.Timestamp("2026-10-10T20:00:00Z")]
    assert df["wind_speed_mph"].tolist() == [10.0, 10.0] and df["precip_probability_pct"].iloc[0] == 20.0 and np.isnan(df["precip_probability_pct"].iloc[1])


def test_nws_forecast_issued_in_the_future_is_capped_at_the_fetch_time(dispatch_home):
    from dispatch.data import weather
    f = ercot.Fetcher(_transport(_nws_handler([])), min_spacing_s=0)
    weather.snapshot_forecasts({"USW00003927": weather.STATIONS["USW00003927"]}, fetcher=f, now=pd.Timestamp("2026-10-10T18:00:00Z"))   # fetch before 'issue'
    (rf,) = list(lake.iter_raw("FORECAST-HOURLY"))
    assert rf.published_utc == pd.Timestamp("2026-10-10T18:00:00Z")


def test_one_failing_station_does_not_stop_the_others(dispatch_home):
    from dispatch.data import weather

    def handler(request):
        if "29.9800" in str(request.url):                                                  # Houston fails
            return httpx.Response(503)
        return _nws_handler([])(request)
    f = ercot.Fetcher(_transport(handler), retries=0, min_spacing_s=0)
    st = {k: weather.STATIONS[k] for k in ("USW00003927", "USW00012960")}
    out = weather.snapshot_forecasts(st, fetcher=f, now=pd.Timestamp("2026-10-10T20:00:00Z"))
    assert set(out["stations"]) == {"USW00003927"} and set(out["failed"]) == {"USW00012960"}


def test_weather_zone_mapping():
    from dispatch.data import weather
    assert weather.load_zone_of("HB_NORTH") == "LZ_NORTH" and weather.load_zone_of("HB_BUSAVG") == "SYSTEM"
    assert weather.load_zone_of("LZ_HOUSTON") == "LZ_HOUSTON" and weather.load_zone_of("SOME_RN") == "SYSTEM"
    assert weather.stations_for("LZ_SOUTH") == {"USW00012924": 0.5, "USW00012919": 0.5}
    assert sum(weather.stations_for("SYSTEM").values()) == pytest.approx(1.0)
    sp_map = pd.DataFrame({"settlement_point": ["WIND_RN"], "kind": ["RESOURCE_NODE"], "load_zone": ["LZ_WEST"],
                           "published_utc": [pd.Timestamp("2026-10-01T00:00:00Z")]})
    assert weather.load_zone_of("WIND_RN", sp_map) == "LZ_WEST"


def test_flows_default_to_plain_functions_and_never_start_prefect(dispatch_home):
    import importlib
    from dispatch.data import flows, parse
    importlib.reload(flows)
    assert flows.PREFECT is False and not hasattr(flows.ingest_once, "fn")                # plain function by default
    for name in ("ingest_once", "backfill_all", "adopt_all", "parse_all", "rebuild_all"):
        assert callable(getattr(flows, name))
    out = flows.parse_all(strict=False)
    assert set(out) == set(parse.DATASETS) and all(v["written"] == 0 for v in out.values())
    assert flows.main(["parse"]) == 0


# ------------------------------------------------------------------------------------------------ the 60-day disclosure zips D1b stages
def test_ext_prefixed_names_are_mis_names():
    n = "ext.00013052.0000000000000000.20261010.055240368.60_Day_SCED_Disclosure.zip"
    assert parse_mis_publish(n) == pd.Timestamp("2026-10-10 10:52:40.368", tz="UTC")


def test_staged_60_day_disclosures_are_copied_once(dispatch_home, tmp_path):
    from dispatch.data import adopt
    root = tmp_path / "ercot-60day"
    (root / "sced").mkdir(parents=True)
    (root / "dam").mkdir()
    sced = root / "sced" / "ext.00013052.0000000000000000.20261010.055240368.60_Day_SCED_Disclosure.zip"
    dam = root / "dam" / "ext.00013051.0000000000000000.20261010.063747402.60_Day_DAM_Disclosure.zip"
    sced.write_bytes(_zip_bytes("s"))
    dam.write_bytes(_zip_bytes("d"))
    (root / "manifest.csv").write_text("report_key\n", encoding="utf-8")
    out = adopt.adopt_staging_60day(root)
    assert out["copied"] == 2 and out["per_report"] == {"60D-SCED": {"copied": 1, "already_in_lake": 0}, "60D-DAM": {"copied": 1, "already_in_lake": 0}}
    assert [r.filename for r in lake.iter_raw(13052)] == [sced.name] and [r.filename for r in lake.iter_raw("60D-DAM")] == [dam.name]
    assert sced.exists() and dam.exists()                                                  # the staging originals stay
    again = adopt.adopt_staging_60day(root)
    assert again["copied"] == 0 and again["already_in_lake"] == 2 and len(_catalog_lines(dispatch_home)) == 2
    assert adopt.adopt_staging_60day(tmp_path / "nowhere")["error"] == "staging folder not found"


def test_prefect_flows_wrap_cleanly_when_asked_and_are_never_run(tmp_path):
    """DISPATCH_FLOWS=prefect swaps the five functions for Prefect @flows of the same names. Calling one would start Prefect's
    temporary server (measured: ~40 s), so this only imports and inspects them, in a fresh interpreter."""
    import subprocess
    import sys
    from pathlib import Path
    code = ("import os; os.environ['DISPATCH_FLOWS']='prefect'\n"
            "from dispatch.data import flows\n"
            "from prefect.flows import Flow\n"
            "names = ['ingest_once','backfill_all','adopt_all','parse_all','rebuild_all']\n"
            "print('PREFECT', flows.PREFECT, all(isinstance(getattr(flows, n), Flow) for n in names), "
            "sorted(getattr(flows, n).name for n in names))\n")
    project = Path(__file__).resolve().parents[1]
    r = subprocess.run([sys.executable, "-I", "-c", "import sys; sys.path.insert(0, %r); exec(%r)" % (str(project), code)],
                       capture_output=True, text=True, cwd=str(project), timeout=300,
                       env={**__import__("os").environ, "PYTHONIOENCODING": "utf-8", "PREFECT_HOME": str(tmp_path / "prefect_home")})
    assert r.returncode == 0, r.stderr[-1500:]
    line = next(x for x in r.stdout.splitlines() if x.startswith("PREFECT"))
    assert line.startswith("PREFECT True True") and "dispatch-ingest-once" in line and "Starting temporary server" not in r.stdout + r.stderr
    assert not (tmp_path / "prefect_home" / "prefect.db").exists()                        # importing started no server and created no database
