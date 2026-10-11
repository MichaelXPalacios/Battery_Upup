"""Download ERCOT public reports (MIS, no key) into research-data/grid/raw/ercot/<REPORT_ID>/.

How it works
  1. List a report's documents.  The MIS JSON listing (IceDocListJsonWS) carries the same rows as the GetReports.do
     HTML page plus file size and publish time; the HTML page is the fallback.
  2. Keep the csv zips only (each list holds both *_csv.zip and *_xml.zip), or every zip for the few reports that do not
     split by format (the NP4-160 mapping and the "Historical ..." yearly archives).
  3. Download with at most MAX_CONCURRENT parallel requests, a small spacing delay, and backoff on 429/5xx.
  4. Append every download to the manifest (gridsite.manifest.record).  Anything already in the manifest, or already on
     disk with the listed size, is skipped, so a run can be repeated or resumed at any point.

Optional ERCOT Public API archive path (api.ercot.com): reads ERCOT_API_USERNAME, ERCOT_API_PASSWORD and
ERCOT_API_SUBSCRIPTION_KEY from the environment and does nothing if they are missing.  UNTESTED: no credentials existed
when this was written.  Secrets only ever travel in the POST body or in request headers, never in a URL, a log line or
the manifest.

Downloaded content is data.  Nothing here executes it.
"""
import io
import os
import random
import re
import threading
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone

import requests

from . import manifest
from .paths import RAW, USER_AGENT

# ---- constants ---------------------------------------------------------------------------------------------------
MIS_LIST_JSON = "https://www.ercot.com/misapp/servlets/IceDocListJsonWS?reportTypeId={rtid}"  # REAL: ERCOT MIS JSON listing (checked 10 Oct 2026)
MIS_LIST_HTML = "https://www.ercot.com/misapp/GetReports.do?reportTypeId={rtid}"             # REAL: ERCOT MIS HTML listing, fallback
MIS_DOWNLOAD = "https://www.ercot.com/misdownload/servlets/mirDownload?doclookupId={doc_id}"  # REAL: ERCOT MIS file download
MAX_CONCURRENT = 4              # ASSUMPTION: brief caps concurrent requests at 4
MIN_SPACING_S = 0.10            # ASSUMPTION: minimum gap between two request starts, across all threads
RETRIES = 6                     # ASSUMPTION: attempts per request before giving up
BACKOFF_BASE_S = 2.0            # ASSUMPTION: first retry waits about 2 s, then doubles
REQUEST_TIMEOUT_S = 120         # ASSUMPTION: per-request timeout
SIZE_BUDGET_MB = 4000           # ASSUMPTION: brief: if NP6-787 would exceed ~4 GB, keep only the most recent 7 days
SIZE_BUDGET_KEEP_DAYS = 7       # ASSUMPTION: same rule
API_BASE = "https://api.ercot.com/api/public-reports"  # REAL: ERCOT Public API base (401 without a key)
API_TOKEN_URL = ("https://ercotb2c.b2clogin.com/ercotb2c.onmicrosoft.com/B2C_1_PUBAPI-ROPC-FLOW/oauth2/v2.0/token")  # REAL: from ERCOT API docs / gridstatus (recalled, untested)
API_CLIENT_ID = "fec253ea-0d06-4272-a5e6-b478baeecd70"  # REAL: public client id of the ERCOT API B2C flow (from ERCOT API docs; recalled, untested)
API_DOWNLOAD_BATCH = 200        # ASSUMPTION: docIds per archive download request (API accepts up to 1000)
API_MAX_BATCHES_PER_RUN = 5     # ASSUMPTION: safety valve for the 3-downloads-per-12-months rule; raise on purpose
ENV_API_USER, ENV_API_PASS, ENV_API_KEY = "ERCOT_API_USERNAME", "ERCOT_API_PASSWORD", "ERCOT_API_SUBSCRIPTION_KEY"

# Report registry.  rtid = MIS reportTypeId (REAL, checked 10 Oct 2026 against IceRepListJsonWS and the listings).
# files: "csv" keeps *_csv.zip only; "all" keeps every zip.  window = retention seen on 10 Oct 2026.
REPORTS = {
    "NP4-160-SG": dict(rtid=10008, files="all", title="Settlement Points List and Electrical Buses Mapping", window="3 versions, ~1 month", emil="np4-160-sg"),
    "NP6-86-CD": dict(rtid=12302, files="csv", title="SCED Shadow Prices and Binding Transmission Constraints", window="7 days (hourly files)", emil="np6-86-cd"),
    "NP6-905-CD": dict(rtid=12301, files="csv", title="Settlement Point Prices at Resource Nodes, Hubs and Load Zones (15 min)", window="7 days", emil="np6-905-cd"),
    "NP6-787-CD": dict(rtid=11485, files="csv", title="LMPs by Electrical Bus (per SCED run, ~5 min)", window="5 days", emil="np6-787-cd"),
    "NP6-322-CD": dict(rtid=13114, files="csv", title="SCED System Lambda", window="5 days", emil="np6-322-cd"),
    "NP4-183-CD": dict(rtid=12328, files="csv", title="DAM Hourly LMPs (by electrical bus)", window="~31 days", emil="np4-183-cd"),
    "NP4-191-CD": dict(rtid=12332, files="csv", title="DAM Shadow Prices", window="~31 days", emil="np4-191-cd"),
    "NP4-190-CD": dict(rtid=12331, files="csv", title="DAM Settlement Point Prices", window="~31 days", emil="np4-190-cd"),
    "NP4-188-CD": dict(rtid=12329, files="csv", title="DAM Clearing Prices for Capacity (ancillary services)", window="~31 days", emil="np4-188-cd"),
    "NP4-523-CD": dict(rtid=13113, files="csv", title="DAM System Lambda", window="~31 days", emil="np4-523-cd"),
    "NP6-332-CD": dict(rtid=24891, files="csv", title="Real-Time Clearing Prices for Capacity by SCED Interval (RTC+B AS prices)", window="7 days", emil="np6-332-cd"),
    "NP6-331-CD": dict(rtid=24898, files="csv", title="Real-Time Clearing Prices for Capacity by 15-Minute Settlement Interval", window="7 days", emil="np6-331-cd"),
    "NP6-788-CD": dict(rtid=12300, files="csv", title="LMPs by Resource Nodes, Load Zones and Trading Hubs (per SCED run)", window="5 days", emil="np6-788-cd"),
    "NP6-323-CD": dict(rtid=13221, files="csv", title="Real-Time Price Adders by SCED Interval", window="5 days", emil="np6-323-cd"),
    # Yearly archives, kept forever on the public MIS list, so they are the only deep history available without an API key.
    "HIST-DAM-MCPC": dict(rtid=13091, files="all", title="Historical DAM Clearing Prices for Capacity (yearly zips, 2010 to now)", window="2010 to now", emil=None),
    "HIST-RTM-MCPC-SCED": dict(rtid=25569, files="all", title="Historical RTM Clearing Prices for Capacity by SCED Interval (RTC+B era, 2025 and 2026)", window="5 Dec 2025 to now", emil=None),
    "HIST-RTM-MCPC-15": dict(rtid=25570, files="all", title="Historical RTM Clearing Prices for Capacity by 15-Minute Settlement Interval", window="5 Dec 2025 to now", emil=None),
    "EB-TO-HUB": dict(rtid=10011, files="all", title="Electrical Bus to Hub List", window="current", emil=None),
}
# What `fetch-ercot all` pulls.  The set is everything in REPORTS: it is all small except NP6-787 (~165 MB compressed).
DEFAULT_REPORTS = list(REPORTS)

_REQ_LOCK = threading.Lock()
_LAST_REQ = [0.0]
_TLS = threading.local()


def _session():
    s = getattr(_TLS, "s", None)
    if s is None:
        s = requests.Session()
        s.headers["User-Agent"] = USER_AGENT
        _TLS.s = s
    return s


def _throttle():
    with _REQ_LOCK:
        wait = _LAST_REQ[0] + MIN_SPACING_S - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        _LAST_REQ[0] = time.monotonic()


def _request(method, url, **kw):
    """requests with spacing, retry and backoff on 429 / 5xx / connection errors.  Never logs headers or bodies."""
    last = None
    for attempt in range(RETRIES):
        _throttle()
        try:
            r = _session().request(method, url, timeout=REQUEST_TIMEOUT_S, **kw)
            if r.status_code == 429 or r.status_code >= 500:
                last = f"HTTP {r.status_code}"
                ra = r.headers.get("Retry-After")
                wait = float(ra) if ra and ra.replace(".", "", 1).isdigit() else BACKOFF_BASE_S * 2 ** attempt
                r.close()
            else:
                return r
        except requests.RequestException as e:
            last = type(e).__name__
            wait = BACKOFF_BASE_S * 2 ** attempt
        time.sleep(wait + random.uniform(0, 1))
    raise RuntimeError(f"giving up after {RETRIES} tries ({last}): {url.split('?')[0]}")


# ---- listing ------------------------------------------------------------------------------------------------------
def list_docs(rtid):
    """All documents in an MIS report list: dicts with doc_id, name, friendly, size, publish (aware datetime), ext."""
    r = _request("GET", MIS_LIST_JSON.format(rtid=rtid))
    docs = []
    if r.status_code == 200:
        try:
            dl = r.json()["ListDocsByRptTypeRes"]["DocumentList"]
            dl = dl if isinstance(dl, list) else [dl]
            for x in dl:
                d = x["Document"]
                docs.append(dict(doc_id=str(d["DocID"]), name=d["ConstructedName"], friendly=d["FriendlyName"],
                                 size=int(d.get("ContentSize") or 0), ext=d.get("Extension", "zip"),
                                 publish=datetime.fromisoformat(d["PublishDate"])))
            return docs
        except (ValueError, KeyError, TypeError):
            docs = []
    # fallback: the HTML list page (rows with the file name and a doclookupId link)
    r = _request("GET", MIS_LIST_HTML.format(rtid=rtid))
    if r.status_code != 200:
        raise RuntimeError(f"listing failed for reportTypeId {rtid}: HTTP {r.status_code}")
    for m in re.finditer(r"<td class='labelOptional_ind'>([^<]+?\.zip)</td>(.*?)</tr>", r.text, flags=re.S):
        name, rest = m.group(1).strip(), m.group(2)
        ids = re.findall(r"doclookupId=(\d+)", rest)
        pub = re.search(r"\.(20\d{6})\.(\d{6})\d*\.", name)      # posting date.time; the report id (0001xxxx) must not match
        if not ids or not pub:
            continue
        friendly = name.split(".", 5)[-1].rsplit(".zip", 1)[0]
        publish = datetime.strptime(pub.group(1) + pub.group(2), "%Y%m%d%H%M%S").replace(tzinfo=timezone(timedelta(hours=-5)))
        docs.append(dict(doc_id=ids[0], name=name, friendly=friendly, size=0, ext="zip", publish=publish))
    return docs


def select(report_id, docs):
    """csv zips only (or every zip for reports that don't split by format)."""
    if REPORTS[report_id]["files"] == "all":
        return [d for d in docs if d["ext"].lower() == "zip"]
    return [d for d in docs if d["friendly"].lower().endswith("csv")]


def estimate(report_id, docs=None):
    docs = docs if docs is not None else select(report_id, list_docs(REPORTS[report_id]["rtid"]))
    pubs = sorted(d["publish"] for d in docs)
    return dict(report_id=report_id, files=len(docs), mb=sum(d["size"] for d in docs) / 1e6,
                first=pubs[0].isoformat() if pubs else None, last=pubs[-1].isoformat() if pubs else None)


# ---- MIS download -------------------------------------------------------------------------------------------------
def _dest(report_id, doc):
    d = RAW / "ercot" / report_id
    d.mkdir(parents=True, exist_ok=True)
    return d / doc["name"]


def _download_one(report_id, doc):
    url = MIS_DOWNLOAD.format(doc_id=doc["doc_id"])
    dest = _dest(report_id, doc)
    r = _request("GET", url, stream=True)
    try:
        if r.status_code != 200:
            return dict(ok=False, doc=doc, why=f"HTTP {r.status_code}")
        tmp = dest.with_suffix(dest.suffix + ".part")
        with open(tmp, "wb") as f:
            for chunk in r.iter_content(1 << 16):
                f.write(chunk)
    finally:
        r.close()
    with open(tmp, "rb") as f:
        magic = f.read(2)
    if magic != b"PK":                                   # an HTML error page or an expired document
        tmp.unlink(missing_ok=True)
        return dict(ok=False, doc=doc, why="not a zip (expired or error page)")
    os.replace(tmp, dest)
    return dict(ok=True, doc=doc, url=url, path=dest)


def fetch_report(report_id, dry_run=False, since=None, log=print):
    """Download whatever is new for one report.  Returns a summary dict.  since = 'YYYY-MM-DD' to skip older publishes."""
    meta = REPORTS[report_id]
    docs = select(report_id, list_docs(meta["rtid"]))
    if since:
        cut = datetime.fromisoformat(since).replace(tzinfo=timezone.utc)
        docs = [d for d in docs if d["publish"] >= cut]
    # size budget (only NP6-787 can plausibly hit it): keep the most recent days
    total_mb = sum(d["size"] for d in docs) / 1e6
    if total_mb > SIZE_BUDGET_MB:
        newest = max(d["publish"] for d in docs)
        docs = [d for d in docs if d["publish"] >= newest - timedelta(days=SIZE_BUDGET_KEEP_DAYS)]
        log(f"  {report_id}: {total_mb:.0f} MB listed > {SIZE_BUDGET_MB} MB budget, keeping the last {SIZE_BUDGET_KEEP_DAYS} days")
    have_urls = {r["url"] for r in manifest._rows()}
    todo, skipped = [], 0
    for d in docs:
        url = MIS_DOWNLOAD.format(doc_id=d["doc_id"])
        dest = _dest(report_id, d)
        if url in have_urls and dest.exists():
            skipped += 1
        elif dest.exists() and d["size"] and dest.stat().st_size == d["size"]:
            manifest.record("ercot", report_id, url, dest, "mis")     # on disk but not yet in the manifest
            skipped += 1
        else:
            todo.append(d)
    est = sum(d["size"] for d in todo) / 1e6
    out = dict(report_id=report_id, listed=len(docs), skipped=skipped, to_download=len(todo), est_mb=round(est, 1),
               downloaded=0, mb=0.0, failed=[])
    log(f"  {report_id} (rtid {meta['rtid']}): {len(docs)} listed, {skipped} already have, {len(todo)} to fetch (~{est:.1f} MB)")
    if dry_run or not todo:
        return out
    with ThreadPoolExecutor(MAX_CONCURRENT) as ex:
        futs = [ex.submit(_download_one, report_id, d) for d in todo]
        for i, fu in enumerate(as_completed(futs), 1):
            try:
                res = fu.result()
            except Exception as e:                       # one bad file must not stop the run
                out["failed"].append(f"{type(e).__name__}: {e}")
                continue
            if res["ok"]:
                row = manifest.record("ercot", report_id, res["url"], res["path"], "mis")
                out["downloaded"] += 1
                out["mb"] += row["bytes"] / 1e6
            else:
                out["failed"].append(f"{res['doc']['name']}: {res['why']}")
            if i % 200 == 0:
                log(f"    {report_id}: {i}/{len(todo)}")
    out["mb"] = round(out["mb"], 1)
    log(f"  {report_id}: downloaded {out['downloaded']} ({out['mb']} MB), failed {len(out['failed'])}")
    if report_id == "NP4-160-SG":
        unpack_mapping()
    return out


def unpack_mapping():
    """Unzip each NP4-160 mapping version into NP4-160-SG/<version>/ ; the newest version already in force also goes flat."""
    base = RAW / "ercot" / "NP4-160-SG"
    for z in sorted(base.glob("*.zip")):
        sub = base / z.stem.split(".")[-1]               # e.g. CIM_Sep_ML3_1_09302026_SP_List_and_EB_Mapping
        if sub.exists() and any(sub.glob("*.csv")):
            continue
        sub.mkdir(exist_ok=True)
        with zipfile.ZipFile(z) as zf:
            for n in zf.namelist():
                if n.lower().endswith(".csv"):                      # the zip nests the files in SP_List_EB_Mapping/
                    (sub / os.path.basename(n)).write_bytes(zf.read(n))


# ---- optional API archive path (UNTESTED: no credentials existed when written) -------------------------------------
def api_credentials():
    u, p, k = (os.environ.get(v) for v in (ENV_API_USER, ENV_API_PASS, ENV_API_KEY))
    return (u, p, k) if (u and p and k) else None


def _api_headers(token, key):
    return {"Authorization": f"Bearer {token}", "Ocp-Apim-Subscription-Key": key}


def api_token(user, password):
    """ROPC token.  Credentials go in the POST body, never in the URL."""
    body = {"username": user, "password": password, "grant_type": "password", "response_type": "id_token",
            "scope": f"openid {API_CLIENT_ID} offline_access", "client_id": API_CLIENT_ID}
    r = _request("POST", API_TOKEN_URL, data=body)
    if r.status_code != 200:
        raise RuntimeError(f"API token request failed: HTTP {r.status_code}")
    return r.json()["id_token"]


def fetch_api_archive(report_id, start, end, log=print):
    """Download archive documents for [start, end) ('YYYY-MM-DD') through the Public API.

    Skips documents already in the manifest, never downloads more than API_MAX_BATCHES_PER_RUN batches per call, and
    records each document with method='api'.  Returns a summary dict; does nothing if credentials are not set."""
    creds = api_credentials()
    if not creds:
        log(f"  {report_id}: API path skipped, {ENV_API_USER} / {ENV_API_PASS} / {ENV_API_KEY} are not all set")
        return dict(report_id=report_id, skipped="no credentials")
    emil = REPORTS[report_id].get("emil")
    if not emil:
        return dict(report_id=report_id, skipped="no API product id for this report")
    user, password, key = creds
    token = api_token(user, password)
    base = f"{API_BASE}/archive/{emil}"
    have = {r["url"] for r in manifest._rows()}
    docs, page = [], 1
    while True:
        r = _request("GET", base, headers=_api_headers(token, key),
                     params={"postDatetimeFrom": f"{start}T00:00:00", "postDatetimeTo": f"{end}T00:00:00", "page": page, "size": 1000})
        if r.status_code != 200:
            raise RuntimeError(f"API archive listing failed: HTTP {r.status_code}")
        js = r.json()
        docs += js.get("archives", [])
        if page >= int(js.get("_meta", {}).get("totalPages", 1)):
            break
        page += 1
    out = dict(report_id=report_id, listed=len(docs), downloaded=0, mb=0.0, batches=0)
    new = [d for d in docs if f"{base}/download#docId={d['docId']}" not in have]
    out_dir = RAW / "ercot" / report_id
    out_dir.mkdir(parents=True, exist_ok=True)
    for i in range(0, len(new), API_DOWNLOAD_BATCH):
        if out["batches"] >= API_MAX_BATCHES_PER_RUN:
            log(f"  {report_id}: stopped at {API_MAX_BATCHES_PER_RUN} batches (API_MAX_BATCHES_PER_RUN)")
            break
        batch = new[i:i + API_DOWNLOAD_BATCH]
        r = _request("POST", f"{base}/download", headers=_api_headers(token, key), json={"docIds": [d["docId"] for d in batch]})
        if r.status_code != 200:
            raise RuntimeError(f"API archive download failed: HTTP {r.status_code}")
        out["batches"] += 1
        with zipfile.ZipFile(io.BytesIO(r.content)) as zf:          # a zip of zips
            for info in zf.infolist():
                dest = out_dir / os.path.basename(info.filename)
                dest.write_bytes(zf.read(info))
                doc = next((d for d in batch if str(d.get("friendlyName", "")) and d.get("friendlyName") in info.filename), None)
                tag = doc["docId"] if doc else os.path.basename(info.filename)
                row = manifest.record("ercot", report_id, f"{base}/download#docId={tag}", dest, "api")
                out["downloaded"] += 1
                out["mb"] += row["bytes"] / 1e6
    return out


# ---- command-line entry points ------------------------------------------------------------------------------------
def _parse_args(args):
    ids, opts = [], {}
    for a in args:
        if "=" in a:
            k, v = a.split("=", 1)
            opts[k.lstrip("-")] = v
        elif a.lstrip("-") in ("dry-run", "dry_run", "api", "list"):
            opts[a.lstrip("-").replace("-", "_")] = True
        else:
            ids.append(a)
    if not ids or ids == ["all"]:
        ids = list(DEFAULT_REPORTS)
    bad = [i for i in ids if i not in REPORTS]
    if bad:
        raise SystemExit(f"unknown report id(s) {bad}; known: {', '.join(REPORTS)}")
    return ids, opts


def fetch_ercot(*args):
    """fetch-ercot [REPORT_ID ... | all] [dry-run] [since=YYYY-MM-DD] [api start=YYYY-MM-DD end=YYYY-MM-DD]

    Downloads new csv zips from the MIS lists.  `dry-run` only lists and estimates.  `api` also tries the Public API
    archive (needs the three ERCOT_API_* environment variables; skipped otherwise)."""
    ids, opts = _parse_args(args)
    results = []
    for rid in ids:
        try:
            results.append(fetch_report(rid, dry_run=bool(opts.get("dry_run")), since=opts.get("since")))
        except Exception as e:
            print(f"  {rid}: FAILED {type(e).__name__}: {e}")
            results.append(dict(report_id=rid, failed=[str(e)]))
        if opts.get("api") and not opts.get("dry_run"):
            start, end = opts.get("start"), opts.get("end")
            if start and end:
                fetch_api_archive(rid, start, end)
            else:
                print("  api: pass start=YYYY-MM-DD end=YYYY-MM-DD")
    tot = sum(r.get("mb", 0) for r in results)
    print(f"done: {sum(r.get('downloaded', 0) for r in results)} files, {tot:.1f} MB downloaded")
    return results


if __name__ == "__main__":
    import sys
    fetch_ercot(*sys.argv[1:])
