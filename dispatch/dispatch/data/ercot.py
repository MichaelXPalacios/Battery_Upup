"""ERCOT MIS listing, download and backfill into the raw lake (PRD FR1 and FR2).

Listing:   https://www.ercot.com/misapp/GetReports.do?reportTypeId=<id>          (HTML; the JSON servlet is the fallback)
Download:  https://www.ercot.com/misdownload/servlets/mirDownload?doclookupId=<id>

* httpx with retries, exponential backoff with jitter (honouring Retry-After), and a hard cap of 4 requests in flight.
* Only the csv zips are taken (each MIS list holds both *_csv.zip and *_xml.zip), except the reports that do not split by
  format (the NP4-160 mapping and the yearly archives), where every zip is taken.
* `backfill(report_id)` fetches everything in the MIS window that the catalog does not have yet. It is safe to repeat.
* Everything lands in the lake through `lake.put`, so a re-download of identical bytes is a no-op and a re-published file
  under the same name with different bytes raises.

Optional ERCOT Public API archive path (`fetch_api_archive`): needs ERCOT_API_USERNAME, ERCOT_API_PASSWORD and
ERCOT_API_SUBSCRIPTION_KEY in the environment; does nothing without them. UNTESTED against the live API (no credentials
exist today); only the quota bookkeeping and the request shapes are unit-tested with a mock transport. Secrets only ever
travel in request bodies or headers, never in URLs, logs or the catalog.

Downloaded content is data. Nothing here executes it.
"""
from __future__ import annotations

import csv
import io
import os
import random
import re
import threading
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Callable, Optional

import httpx
import pandas as pd

from .. import paths
from . import lake, reports
from .times import iso_z, parse_mis_publish, utc

MIS_LIST_HTML = "https://www.ercot.com/misapp/GetReports.do?reportTypeId={rtid}"             # REAL: spec'd listing page
MIS_LIST_JSON = "https://www.ercot.com/misapp/servlets/IceDocListJsonWS?reportTypeId={rtid}"  # REAL: same rows + size + offset-bearing publish time
MIS_DOWNLOAD = "https://www.ercot.com/misdownload/servlets/mirDownload?doclookupId={doc_id}"  # REAL
MAX_CONCURRENT = 4              # REAL: the task caps requests in flight at 4
MIN_SPACING_S = 0.05            # ASSUMPTION: minimum gap between two request starts, across all threads
RETRIES = 6                     # ASSUMPTION: retries after the first attempt
BACKOFF_BASE_S = 1.0            # ASSUMPTION: first retry waits ~1 s, then doubles
BACKOFF_MAX_S = 60.0
REQUEST_TIMEOUT_S = 120.0
USER_AGENT = "hugging-base-dispatch (research)"

API_BASE = "https://api.ercot.com/api/public-reports"      # REAL (401 without a key)
API_TOKEN_URL = "https://ercotb2c.b2clogin.com/ercotb2c.onmicrosoft.com/B2C_1_PUBAPI-ROPC-FLOW/oauth2/v2.0/token"   # from ERCOT API docs; UNTESTED
API_CLIENT_ID = "fec253ea-0d06-4272-a5e6-b478baeecd70"     # public client id of the ERCOT B2C flow, from ERCOT API docs; UNTESTED
ENV_API_USER, ENV_API_PASS, ENV_API_KEY = "ERCOT_API_USERNAME", "ERCOT_API_PASSWORD", "ERCOT_API_SUBSCRIPTION_KEY"
API_MAX_DOWNLOAD_DAYS_PER_12M = 3   # REAL (ERCOT rule, CLAUDE.md): the same report may be downloaded via the API 3 times per 12 months
API_DOWNLOAD_BATCH = 200            # ASSUMPTION: docIds per archive download request (API accepts up to 1000)


class FetchError(RuntimeError):
    pass


class ApiQuotaError(RuntimeError):
    """The 3-downloads-per-report-per-12-months API rule would be broken."""


# ------------------------------------------------------------------------------------------------ HTTP
class Fetcher:
    """httpx with spacing, retry and backoff on 429 / 5xx / transport errors; at most `max_concurrent` requests in flight."""

    def __init__(self, client: Optional[httpx.Client] = None, *, max_concurrent: int = MAX_CONCURRENT,
                 min_spacing_s: float = MIN_SPACING_S, retries: int = RETRIES, backoff_base_s: float = BACKOFF_BASE_S,
                 backoff_max_s: float = BACKOFF_MAX_S, sleep: Callable[[float], None] = time.sleep):
        self.client = client or httpx.Client(headers={"User-Agent": USER_AGENT}, timeout=REQUEST_TIMEOUT_S,
                                             follow_redirects=True,
                                             limits=httpx.Limits(max_connections=max_concurrent))
        self._sem = threading.BoundedSemaphore(max(1, min(max_concurrent, MAX_CONCURRENT)))
        self._spacing_lock = threading.Lock()
        self._last_start = 0.0
        self.min_spacing_s, self.retries = min_spacing_s, retries
        self.backoff_base_s, self.backoff_max_s = backoff_base_s, backoff_max_s
        self._sleep = sleep
        self.in_flight_peak = 0
        self._in_flight = 0
        self._count_lock = threading.Lock()

    def _space(self):
        with self._spacing_lock:
            wait = self._last_start + self.min_spacing_s - time.monotonic()
            if wait > 0:
                self._sleep(wait)
            self._last_start = time.monotonic()

    def request(self, method: str, url: str, **kw) -> httpx.Response:
        last = "no attempt"
        for attempt in range(self.retries + 1):
            with self._sem:
                with self._count_lock:
                    self._in_flight += 1
                    self.in_flight_peak = max(self.in_flight_peak, self._in_flight)
                try:
                    self._space()
                    r = self.client.request(method, url, **kw)
                    if r.status_code == 429 or r.status_code >= 500:
                        last = f"HTTP {r.status_code}"
                        ra = r.headers.get("Retry-After", "")
                        delay = float(ra) if ra.replace(".", "", 1).isdigit() else None
                        r.close()
                    else:
                        return r
                except httpx.TransportError as e:
                    last, delay = type(e).__name__, None
                finally:
                    with self._count_lock:
                        self._in_flight -= 1
            if attempt == self.retries:
                break
            back = min(self.backoff_base_s * 2 ** attempt, self.backoff_max_s)
            self._sleep((delay if delay is not None else back) + random.uniform(0, 0.25 * self.backoff_base_s))
        # never log the query string (API calls carry none, but keep the habit)
        raise FetchError(f"giving up after {self.retries + 1} tries ({last}): {url.split('?')[0]}")

    def get_bytes(self, url: str) -> bytes:
        r = self.request("GET", url)
        if r.status_code != 200:
            raise FetchError(f"HTTP {r.status_code}: {url.split('?')[0]}")
        return r.content

    def close(self):
        self.client.close()


# ------------------------------------------------------------------------------------------------ listing
@dataclass(frozen=True)
class Doc:
    doc_id: str
    name: str                                   # ConstructedName, e.g. cdr.00012301.0000000000000000.20261010.101702135.SPP...zip
    size: Optional[int] = None
    listed_publish: Optional[pd.Timestamp] = None     # offset-bearing publish time, JSON listing only

    @property
    def published_utc(self) -> pd.Timestamp:
        return parse_mis_publish(self.name, hint=self.listed_publish)

    @property
    def url(self) -> str:
        return MIS_DOWNLOAD.format(doc_id=self.doc_id)


_HTML_ROW = re.compile(r"<td class='labelOptional_ind'>\s*([^<]+?\.(?:zip|xlsx|csv|xml))\s*</td>(.*?)</tr>", re.S | re.I)
_DOC_ID = re.compile(r"doclookupId=(\d+)")


def parse_html_listing(html_text: str) -> list[Doc]:
    docs = []
    for m in _HTML_ROW.finditer(html_text):
        ids = _DOC_ID.findall(m.group(2))
        if ids:
            docs.append(Doc(doc_id=ids[0], name=m.group(1).strip()))
    return docs


def parse_json_listing(payload: dict) -> list[Doc]:
    dl = payload["ListDocsByRptTypeRes"]["DocumentList"]
    dl = dl if isinstance(dl, list) else [dl]
    out = []
    for x in dl:
        d = x["Document"]
        out.append(Doc(doc_id=str(d["DocID"]), name=d["ConstructedName"], size=int(d.get("ContentSize") or 0),
                       listed_publish=pd.Timestamp(d["PublishDate"]).tz_convert("UTC") if d.get("PublishDate") else None))
    return out


def list_docs(rtid: int, fetcher: Optional[Fetcher] = None) -> list[Doc]:
    """Every document on an MIS report list (csv and xml). GetReports.do first, the JSON servlet as the fallback."""
    f = fetcher or Fetcher()
    r = f.request("GET", MIS_LIST_HTML.format(rtid=rtid))
    docs = parse_html_listing(r.text) if r.status_code == 200 else []
    if not docs:
        j = f.request("GET", MIS_LIST_JSON.format(rtid=rtid))
        if j.status_code != 200:
            raise FetchError(f"listing failed for reportTypeId {rtid}: HTTP {r.status_code} / {j.status_code}")
        docs = parse_json_listing(j.json())
    return docs


def select(report: reports.Report, docs: list[Doc]) -> list[Doc]:
    """csv zips only, or every zip for reports that do not split by format. Documents with an unreadable name are dropped."""
    out = []
    for d in docs:
        n = d.name.lower()
        if not n.endswith(".zip"):
            continue
        if report.files == "csv" and not n.endswith("_csv.zip"):
            continue
        try:
            parse_mis_publish(d.name)
        except ValueError:
            continue
        out.append(d)
    return out


# ------------------------------------------------------------------------------------------------ download into the lake
def _is_zip(data: bytes) -> bool:
    return data[:2] == b"PK"


def _fetch_one(report: reports.Report, doc: Doc, fetcher: Fetcher) -> dict:
    try:
        data = fetcher.get_bytes(doc.url)
        if not _is_zip(data):                           # an HTML error page or an expired document
            return {"ok": False, "name": doc.name, "why": "not a zip (expired or error page)"}
        lake.put(report.source, report.report_id, doc.published_utc, doc.name, data, "mis")
        return {"ok": True, "name": doc.name, "bytes": len(data), "published_utc": doc.published_utc}
    except Exception as e:                              # one bad file must not stop the run
        return {"ok": False, "name": doc.name, "why": f"{type(e).__name__}: {e}"}


def backfill(report_id, *, fetcher: Optional[Fetcher] = None, since=None, until=None, limit: Optional[int] = None,
             dry_run: bool = False, workers: int = MAX_CONCURRENT, log: Callable[[str], None] = lambda s: None) -> dict:
    """Fetch every document of one MIS report that the catalog does not have yet.

    `since` / `until` (tz-aware) restrict by posting time. `limit` caps the number of downloads (newest first), which is
    how `ingest_once` polls cheaply. Returns a summary dict; failures are listed, never raised.
    """
    rep = reports.get(report_id)
    f = fetcher or Fetcher()
    listing = list_docs(rep.rtid, f)
    docs = select(rep, listing)
    cut_lo = utc(since) if since is not None else None
    cut_hi = utc(until) if until is not None else None
    wanted = [d for d in docs if (cut_lo is None or d.published_utc >= cut_lo) and (cut_hi is None or d.published_utc <= cut_hi)]
    missing = [d for d in wanted if not lake.has(rep.source, rep.report_id, d.name)]
    missing.sort(key=lambda d: d.published_utc, reverse=True)        # newest first
    todo = missing[:limit] if limit is not None else missing
    out = {"report_id": rep.report_id, "rtid": rep.rtid, "listed": len(listing), "selected": len(wanted),
           "already_have": len(wanted) - len(missing), "to_fetch": len(todo), "downloaded": 0, "bytes": 0,
           "failed": [], "newest_published_utc": None}
    log(f"  {rep.report_id} (rtid {rep.rtid}): {len(listing)} listed, {len(wanted)} wanted, {len(todo)} to fetch")
    if dry_run or not todo:
        return out
    with ThreadPoolExecutor(max_workers=max(1, min(workers, MAX_CONCURRENT))) as ex:
        results = list(ex.map(lambda d: _fetch_one(rep, d, f), todo))
    for r in results:
        if r["ok"]:
            out["downloaded"] += 1
            out["bytes"] += r["bytes"]
            if out["newest_published_utc"] is None or r["published_utc"] > out["newest_published_utc"]:
                out["newest_published_utc"] = r["published_utc"]
        else:
            out["failed"].append(f"{r['name']}: {r['why']}")
    if out["newest_published_utc"] is not None:
        out["newest_published_utc"] = iso_z(out["newest_published_utc"])
    log(f"  {rep.report_id}: downloaded {out['downloaded']} ({out['bytes'] / 1e6:.1f} MB), failed {len(out['failed'])}")
    return out


def backfill_all(report_ids=None, *, fetcher: Optional[Fetcher] = None, log: Callable[[str], None] = lambda s: None,
                 include_archives: bool = True, **kw) -> dict:
    """`backfill` for every ingest report (the live windows, and by default the free yearly archives)."""
    ids = report_ids or [r.report_id for r in reports.REPORTS.values()
                         if r.ingest and (include_archives or not r.archive)]
    f = fetcher or Fetcher()
    return {rid: backfill(rid, fetcher=f, log=log, **kw) for rid in ids}


def ingest_once(report_ids=None, *, fetcher: Optional[Fetcher] = None, lookback=pd.Timedelta(hours=6),
                now=None, include_archives: bool = False, log: Callable[[str], None] = lambda s: None) -> dict:
    """One polling pass: list each live report and fetch what was posted in the last `lookback` that we do not have.
    Cheap enough to run every minute (one listing request per report plus the new files)."""
    t_now = utc(now) if now is not None else pd.Timestamp.now(tz="UTC")
    ids = report_ids or [r.report_id for r in reports.REPORTS.values() if r.ingest and (include_archives or not r.archive)]
    f = fetcher or Fetcher()
    return {rid: backfill(rid, fetcher=f, since=t_now - lookback, log=log) for rid in ids}


# ------------------------------------------------------------------------------------------------ API archive path
def api_credentials() -> Optional[tuple[str, str, str]]:
    u, p, k = (os.environ.get(v) for v in (ENV_API_USER, ENV_API_PASS, ENV_API_KEY))
    return (u, p, k) if (u and p and k) else None


def api_download_days(report_id: str, now=None) -> set[str]:
    """UTC dates, in the last 12 months, on which this report was downloaded through the API, from BOTH ledgers:
    `lake/catalog.jsonl` (origin == 'api') and `research-data/grid/manifest.csv` (method == 'api').
    One calendar day counts once, however many files came down: a conservative proxy for 'one download'."""
    t_now = utc(now) if now is not None else pd.Timestamp.now(tz="UTC")
    since = t_now - pd.Timedelta(days=365)
    rid = reports.canonical_id(report_id)
    days: set[str] = set()
    for e in lake.catalog_entries(rid):
        if e["origin"] == "api" and utc(e["ingested_utc"]) >= since:
            days.add(e["ingested_utc"][:10])
    if paths.GRID_MANIFEST.exists():
        with open(paths.GRID_MANIFEST, newline="", encoding="utf-8") as fh:
            for row in csv.DictReader(fh):
                if row.get("method") == "api" and reports.canonical_id(row.get("report_id", "")) == rid:
                    try:
                        if utc(row["downloaded_at_utc"]) >= since:
                            days.add(row["downloaded_at_utc"][:10])
                    except (ValueError, KeyError):
                        continue
    return days


def api_check_quota(report_id: str, now=None) -> int:
    """Raise ApiQuotaError if one more API download of this report would be the 4th in 12 months. Returns downloads so far."""
    used = len(api_download_days(report_id, now))
    if used >= API_MAX_DOWNLOAD_DAYS_PER_12M:
        raise ApiQuotaError(f"{report_id}: {used} API downloads in the last 12 months across lake/catalog.jsonl and "
                            f"research-data/grid/manifest.csv; ERCOT allows {API_MAX_DOWNLOAD_DAYS_PER_12M}")
    return used


def _api_headers(token: str, key: str) -> dict:
    return {"Authorization": f"Bearer {token}", "Ocp-Apim-Subscription-Key": key}


def api_token(fetcher: Fetcher, user: str, password: str) -> str:
    """ROPC token. Credentials go in the POST body, never in a URL."""
    body = {"username": user, "password": password, "grant_type": "password", "response_type": "id_token",
            "scope": f"openid {API_CLIENT_ID} offline_access", "client_id": API_CLIENT_ID}
    r = fetcher.request("POST", API_TOKEN_URL, data=body)
    if r.status_code != 200:
        raise FetchError(f"API token request failed: HTTP {r.status_code}")
    return r.json()["id_token"]


def fetch_api_archive(report_id, start: str, end: str, *, fetcher: Optional[Fetcher] = None, now=None,
                      log: Callable[[str], None] = lambda s: None) -> dict:
    """Download ERCOT Public API archive documents for [start, end) ('YYYY-MM-DD') into the lake (origin 'api').

    Order of checks, before anything is downloaded: credentials present; the 12-month quota from both ledgers; then only
    documents whose file name the lake does not already hold are requested. UNTESTED against the live API."""
    rep = reports.get(report_id)
    creds = api_credentials()
    if not creds:
        log(f"  {rep.report_id}: API path skipped, {ENV_API_USER} / {ENV_API_PASS} / {ENV_API_KEY} are not all set")
        return {"report_id": rep.report_id, "skipped": "no credentials"}
    if not rep.emil:
        return {"report_id": rep.report_id, "skipped": "no API product id for this report"}
    api_check_quota(rep.report_id, now)
    f = fetcher or Fetcher()
    user, password, key = creds
    token = api_token(f, user, password)
    base = f"{API_BASE}/archive/{rep.emil}"
    docs, page = [], 1
    while True:
        r = f.request("GET", base, headers=_api_headers(token, key),
                      params={"postDatetimeFrom": f"{start}T00:00:00", "postDatetimeTo": f"{end}T00:00:00", "page": page, "size": 1000})
        if r.status_code != 200:
            raise FetchError(f"API archive listing failed: HTTP {r.status_code}")
        js = r.json()
        docs += js.get("archives", [])
        if page >= int(js.get("_meta", {}).get("totalPages", 1)):
            break
        page += 1
    new = [d for d in docs if not lake.has(rep.source, rep.report_id, str(d.get("friendlyName") or d.get("docId")))]
    out = {"report_id": rep.report_id, "listed": len(docs), "new": len(new), "downloaded": 0, "bytes": 0}
    for i in range(0, len(new), API_DOWNLOAD_BATCH):
        batch = new[i:i + API_DOWNLOAD_BATCH]
        r = f.request("POST", f"{base}/download", headers=_api_headers(token, key), json={"docIds": [d["docId"] for d in batch]})
        if r.status_code != 200:
            raise FetchError(f"API archive download failed: HTTP {r.status_code}")
        with zipfile.ZipFile(io.BytesIO(r.content)) as zf:                  # a zip of zips
            for info in zf.infolist():
                name = os.path.basename(info.filename)
                try:
                    published = parse_mis_publish(name)
                except ValueError:
                    published = pd.Timestamp.now(tz="UTC")
                lake.put(rep.source, rep.report_id, published, name, zf.read(info), "api")
                out["downloaded"] += 1
                out["bytes"] += info.file_size
        break                                                                # one batch per call: stay far from the quota
    return out
