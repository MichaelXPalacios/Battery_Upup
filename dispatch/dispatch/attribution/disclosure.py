"""ERCOT 60-day disclosure reports -> ActualDispatch for one public pilot battery (the PRD's public case study).

ERCOT posts, every day, the resource-level data of the operating day that is 60 days old:

* NP3-965-ER "60-Day SCED Disclosure Reports"  (MIS reportTypeId 13052): SCED telemetry per resource and SCED run
  (telemetered net output, base point, HSL/LSL, ancillary-service responsibilities, ...), load-resource data and
  settlement-metered net energy.
* NP3-966-ER "60-Day DAM Disclosure Reports"   (MIS reportTypeId 13051): day-ahead awards, ancillary-service awards and
  the day-ahead settlement point price per resource.

Part A of this module lists and downloads the daily zips into ``DISPATCH_HOME/staging/ercot-60day/`` and keeps a small
append-only manifest (url, file, sha256, bytes, utc time). D0 adopts staging into the lake later. Part B reads the ESR rows
(SCED telemetry, DAM awards) into parquet caches, Part C chooses the pilot ESR by written rules, prices it and builds the
15-minute `ActualDispatch`, Part D reads the day-ahead hub prices from the same DAM reports. Nothing downloaded is ever
executed; archives are read in memory with ``zipfile`` and ``csv``/``pandas``.

Politeness: at most ``MAX_CONCURRENT`` (4) requests in flight, spacing between request starts, exponential backoff on
HTTP 429/5xx, and a browser-like User-Agent. Labels used in comments: REAL (read from a named source), DERIVED
(computed), ASSUMPTION (our choice).
"""
from __future__ import annotations

import csv
import hashlib
import random
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable, Optional

from .. import paths

# ------------------------------------------------------------------------------------------------ constants
MIS_LIST_JSON = "https://www.ercot.com/misapp/servlets/IceDocListJsonWS?reportTypeId={rtid}"   # REAL (checked 10 Oct 2026)
MIS_DOWNLOAD = "https://www.ercot.com/misdownload/servlets/mirDownload?doclookupId={doc_id}"   # REAL (checked 10 Oct 2026)
REPORT_TYPE_IDS = {"sced": 13052, "dam": 13051}      # REAL: IceRepListJsonWS, "60-Day SCED/DAM Disclosure Reports"
REPORT_NAMES = {"sced": "NP3-965-ER 60-Day SCED Disclosure Reports", "dam": "NP3-966-ER 60-Day DAM Disclosure Reports"}
DISCLOSURE_LAG_DAYS = 60                              # REAL: a file posted on day D holds operating day D - 60 (verified in the data)
USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
              "Chrome/124.0 Safari/537.36")           # ASSUMPTION: browser-like, as the brief asks
MAX_CONCURRENT = 4                                    # brief: at most 4 concurrent MIS requests
MIN_SPACING_S = 0.25                                  # ASSUMPTION: gap between two request starts across all threads
RETRIES = 6                                           # ASSUMPTION
BACKOFF_BASE_S = 2.0                                  # ASSUMPTION: first retry after ~2 s, doubling
REQUEST_TIMEOUT_S = 300                               # ASSUMPTION: the daily SCED zip is ~55 MB
NOT_ZIP_RETRIES = 4                                   # ASSUMPTION: attempts when MIS answers 200 with an HTML page

MANIFEST_FIELDS = ["report_key", "report_type_id", "doc_id", "url", "file", "sha256", "bytes", "posted_utc",
                   "operating_day", "downloaded_utc"]
_REQ_LOCK = threading.Lock()
_LAST_REQ = [0.0]
_MANIFEST_LOCK = threading.Lock()


def staging_dir() -> Path:
    """DISPATCH_HOME/staging/ercot-60day (re-read from paths so tests can monkeypatch DISPATCH_HOME)."""
    return paths.HOME / "staging" / "ercot-60day"


def manifest_path() -> Path:
    return staging_dir() / "manifest.csv"


# ------------------------------------------------------------------------------------------------ HTTP
def _throttle():
    with _REQ_LOCK:
        wait = _LAST_REQ[0] + MIN_SPACING_S - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        _LAST_REQ[0] = time.monotonic()


def _client():
    import httpx
    return httpx.Client(headers={"User-Agent": USER_AGENT, "Accept": "*/*"}, timeout=REQUEST_TIMEOUT_S,
                        follow_redirects=True)


def _get(client, url, *, stream_to: Optional[Path] = None):
    """GET with spacing and retry/backoff on 429, 5xx and connection errors. Returns (status, bytes_or_None)."""
    import httpx
    last = None
    for attempt in range(RETRIES):
        _throttle()
        wait = BACKOFF_BASE_S * 2 ** attempt
        try:
            if stream_to is None:
                r = client.get(url)
                if r.status_code == 429 or r.status_code >= 500:
                    last = f"HTTP {r.status_code}"
                    ra = r.headers.get("Retry-After")
                    wait = float(ra) if ra and ra.replace(".", "", 1).isdigit() else wait
                else:
                    return r.status_code, r.content
            else:
                with client.stream("GET", url) as r:
                    if r.status_code == 429 or r.status_code >= 500:
                        last = f"HTTP {r.status_code}"
                        ra = r.headers.get("Retry-After")
                        wait = float(ra) if ra and ra.replace(".", "", 1).isdigit() else wait
                    elif r.status_code != 200:
                        return r.status_code, None
                    else:
                        tmp = stream_to.with_suffix(stream_to.suffix + ".part")
                        with open(tmp, "wb") as f:
                            for chunk in r.iter_bytes(1 << 16):
                                f.write(chunk)
                        return 200, tmp
        except httpx.HTTPError as e:
            last = type(e).__name__
        time.sleep(wait + random.uniform(0, 1))
    raise RuntimeError(f"giving up after {RETRIES} tries ({last}): {url.split('?')[0]}")


# ------------------------------------------------------------------------------------------------ listing
def list_docs(kind: str) -> list[dict]:
    """All documents on the MIS list for 'sced' or 'dam': doc_id, name, size, posted_utc (tz-aware), operating_day."""
    rtid = REPORT_TYPE_IDS[kind]
    with _client() as c:
        status, body = _get(c, MIS_LIST_JSON.format(rtid=rtid))
    if status != 200:
        raise RuntimeError(f"MIS listing for reportTypeId {rtid} failed: HTTP {status}")
    import json
    dl = json.loads(body)["ListDocsByRptTypeRes"]["DocumentList"]
    dl = dl if isinstance(dl, list) else [dl]
    out = []
    for x in dl:
        d = x["Document"]
        if str(d.get("Extension", "zip")).lower() != "zip":
            continue
        posted = datetime.fromisoformat(d["PublishDate"]).astimezone(timezone.utc)
        out.append(dict(kind=kind, report_type_id=rtid, doc_id=str(d["DocID"]), name=d["ConstructedName"],
                        size=int(d.get("ContentSize") or 0), posted_utc=posted,
                        operating_day=posting_to_operating_day(d["PublishDate"])))
    return sorted(out, key=lambda r: r["posted_utc"])


def posting_to_operating_day(publish_iso: str) -> date:
    """The CPT calendar date of the posting minus 60 days (REAL, checked against the file contents)."""
    posted_local = datetime.fromisoformat(publish_iso)         # carries the CPT offset from the list
    return posted_local.date() - timedelta(days=DISCLOSURE_LAG_DAYS)


# ------------------------------------------------------------------------------------------------ manifest
def _sha256(path: Path, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(chunk), b""):
            h.update(b)
    return h.hexdigest()


def read_manifest() -> list[dict]:
    p = manifest_path()
    if not p.exists():
        return []
    with open(p, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def _append_manifest(row: dict):
    p = manifest_path()
    with _MANIFEST_LOCK:
        new = not p.exists()
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "a", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=MANIFEST_FIELDS)
            if new:
                w.writeheader()
            w.writerow(row)


# ------------------------------------------------------------------------------------------------ download
def _download_one(doc: dict, client) -> dict:
    dest_dir = staging_dir() / doc["kind"]
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / doc["name"]
    url = MIS_DOWNLOAD.format(doc_id=doc["doc_id"])
    tmp = None
    for attempt in range(NOT_ZIP_RETRIES):                   # MIS sometimes answers 200 with an HTML page under load
        status, tmp = _get(client, url, stream_to=dest)
        if status != 200:
            return dict(ok=False, doc=doc, why=f"HTTP {status}")
        with open(tmp, "rb") as f:
            magic = f.read(2)
        if magic == b"PK":
            break
        Path(tmp).unlink(missing_ok=True)
        time.sleep(BACKOFF_BASE_S * 2 ** attempt + random.uniform(0, 1))
    else:
        return dict(ok=False, doc=doc, why="not a zip (expired or error page)")
    sha = _sha256(Path(tmp))
    if dest.exists():                                        # staging is immutable: same bytes no-op, different bytes error
        if _sha256(dest) != sha:
            Path(tmp).unlink(missing_ok=True)
            raise RuntimeError(f"{dest.name}: different bytes under the same name; staging is immutable")
        Path(tmp).unlink(missing_ok=True)
    else:
        Path(tmp).replace(dest)
    row = dict(report_key=doc["kind"], report_type_id=doc["report_type_id"], doc_id=doc["doc_id"], url=url,
               file=str(dest), sha256=sha, bytes=dest.stat().st_size, posted_utc=doc["posted_utc"].isoformat(),
               operating_day=doc["operating_day"].isoformat(),
               downloaded_utc=datetime.now(timezone.utc).isoformat(timespec="seconds"))
    _append_manifest(row)
    return dict(ok=True, doc=doc, row=row)


def download(docs: Iterable[dict], log=print) -> dict:
    """Download the given listed documents into staging (<=4 concurrent). Files already in the manifest with the
    same name and size are skipped. Returns a summary dict."""
    have = {(Path(r["file"]).name, int(r["bytes"])) for r in read_manifest()}
    todo = []
    for d in docs:
        dest = staging_dir() / d["kind"] / d["name"]
        if (d["name"], d["size"]) in have and dest.exists():
            continue
        if dest.exists() and d["size"] and dest.stat().st_size == d["size"]:       # on disk, missing from the manifest
            _append_manifest(dict(report_key=d["kind"], report_type_id=d["report_type_id"], doc_id=d["doc_id"],
                                  url=MIS_DOWNLOAD.format(doc_id=d["doc_id"]), file=str(dest), sha256=_sha256(dest),
                                  bytes=dest.stat().st_size, posted_utc=d["posted_utc"].isoformat(),
                                  operating_day=d["operating_day"].isoformat(),
                                  downloaded_utc=datetime.now(timezone.utc).isoformat(timespec="seconds")))
            continue
        todo.append(d)
    out = dict(requested=len(todo), downloaded=0, mb=0.0, failed=[])
    if not todo:
        return out
    log(f"downloading {len(todo)} files (~{sum(d['size'] for d in todo) / 1e6:.0f} MB) with {MAX_CONCURRENT} workers")
    clients = threading.local()

    def work(d):
        c = getattr(clients, "c", None)
        if c is None:
            c = clients.c = _client()
        return _download_one(d, c)

    with ThreadPoolExecutor(MAX_CONCURRENT) as ex:
        futs = {ex.submit(work, d): d for d in todo}
        for i, fu in enumerate(as_completed(futs), 1):
            try:
                res = fu.result()
            except Exception as e:                           # one bad file must not stop the run
                out["failed"].append(f"{futs[fu]['name']}: {type(e).__name__}: {e}")
                continue
            if res["ok"]:
                out["downloaded"] += 1
                out["mb"] += res["row"]["bytes"] / 1e6 if isinstance(res["row"]["bytes"], (int, float)) else int(res["row"]["bytes"]) / 1e6
            else:
                out["failed"].append(f"{res['doc']['name']}: {res['why']}")
            if i % 5 == 0 or i == len(todo):
                log(f"  {i}/{len(todo)} done, {out['downloaded']} ok, {len(out['failed'])} failed")
    out["mb"] = round(out["mb"], 1)
    return out


def select_days(docs: list[dict], first_day: date, last_day: date) -> list[dict]:
    """Documents whose operating day lies in [first_day, last_day]."""
    return [d for d in docs if first_day <= d["operating_day"] <= last_day]


# ==================================================================================================================
# Part B: read the ESR rows of the 60-day reports
# ==================================================================================================================
import zipfile

import numpy as np
import pandas as pd

SCED_ESR_MEMBER = "60d_ESR_Data_in_SCED"            # REAL: member name stem inside the SCED zip (suffix is the post date)
DAM_ESR_MEMBER = "60d_DAM_ESR_Data"                  # REAL: member name stem inside the DAM zip
SCED_ESR_COLS = {                                    # REAL column names of 60d_ESR_Data_in_SCED -> our names
    "SCED Time Stamp": "ts_local", "Repeated Hour Flag": "rep", "Resource Name": "resource",
    "HSL": "hsl_mw", "LSL": "lsl_mw", "HDL": "hdl_mw", "LDL": "ldl_mw", "Base Point": "base_point_mw",
    "Telemetered Net Output": "net_mw", "State of Charge": "soc_mwh", "Minimum SOC": "min_soc_mwh",
    "Maximum SOC": "max_soc_mwh", "Telemetered Resource Status": "status",
    "AS Awards REGUP": "aw_regup", "AS Awards REGDN": "aw_regdn", "AS Awards RRSPFR": "aw_rrspfr",
    "AS Awards RRSFFR": "aw_rrsffr", "AS Awards RRSUFR": "aw_rrsufr", "AS Awards ECRS": "aw_ecrs",
    "AS Awards NSPIN": "aw_nspin",
}
DAM_ESR_COLS = {                                     # REAL column names of 60d_DAM_ESR_Data -> our names
    "Delivery Date": "date", "Hour Ending": "he", "Resource Name": "resource", "HSL": "hsl_mw", "LSL": "lsl_mw",
    "Resource Status": "status", "Awarded Quantity": "dam_energy_mw", "Settlement Point Name": "settlement_point",
    "Energy Settlement Point Price": "dam_spp", "RegUp Awarded": "dam_regup", "RegUp MCPC": "mcpc_regup",
    "RegDown Awarded": "dam_regdn", "RegDown MCPC": "mcpc_regdn", "RRSPFR Awarded": "dam_rrspfr",
    "RRSFFR Awarded": "dam_rrsffr", "RRSUFR Awarded": "dam_rrsufr", "RRS MCPC": "mcpc_rrs",
    "ECRSSD Awarded": "dam_ecrs", "ECRS MCPC": "mcpc_ecrs", "NonSpin Awarded": "dam_nspin", "NonSpin MCPC": "mcpc_nspin",
}
SCED_RUN_S = 300.0                                   # REAL: SCED runs about every 5 minutes (288 runs per day seen)
MAX_RUN_COVER_S = 600.0                              # ASSUMPTION: a run is credited at most 10 minutes (2 nominal runs)
STD_AS_MAP = {"REGUP": ("aw_regup",), "REGDN": ("aw_regdn",), "ECRS": ("aw_ecrs",), "NSPIN": ("aw_nspin",),
              "RRS": ("aw_rrspfr", "aw_rrsffr", "aw_rrsufr")}   # RRS = PFR + FFR + UFR (all three are Responsive Reserve)


def _member(z: zipfile.ZipFile, stem: str) -> str:
    for n in z.namelist():
        if n.startswith(stem):
            return n
    raise KeyError(f"{stem} not in {z.filename}")


def read_esr_sced(zip_path, resources: Optional[Iterable[str]] = None) -> pd.DataFrame:
    """ESR rows of one daily SCED disclosure zip: one row per resource and SCED run, UTC timestamps, numeric columns.

    Reads the zip member in memory (nothing is extracted) and keeps only SCED_ESR_COLS. `resources` filters by name.
    """
    with zipfile.ZipFile(zip_path) as z:
        with z.open(_member(z, SCED_ESR_MEMBER)) as f:
            df = pd.read_csv(f, usecols=list(SCED_ESR_COLS), dtype=str, keep_default_na=False)
    df = df.rename(columns=SCED_ESR_COLS)
    if resources is not None:
        df = df[df["resource"].isin(set(resources))]
    return _convert_sced(df)


def _convert_sced(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    naive = pd.to_datetime(df["ts_local"], format="%m/%d/%Y %H:%M:%S")
    dst = (df["rep"].str.upper() != "Y").to_numpy()               # the repeated (second) hour of a fall-back is standard time
    df["ts_utc"] = naive.dt.tz_localize("America/Chicago", ambiguous=dst, nonexistent="shift_forward").dt.tz_convert("UTC")
    for c in df.columns:
        if c in ("ts_local", "rep", "resource", "status", "ts_utc"):
            continue
        df[c] = pd.to_numeric(df[c].replace("", np.nan), errors="coerce")
    for c in [c for c in df.columns if c.startswith("aw_")]:
        df[c] = df[c].fillna(0.0)                                  # a blank award means no award
    return df.drop(columns=["ts_local"]).sort_values(["resource", "ts_utc"]).reset_index(drop=True)


def read_esr_dam(zip_path, resources: Optional[Iterable[str]] = None) -> pd.DataFrame:
    """ESR rows of one daily DAM disclosure zip: one row per resource and delivery hour (UTC hour start)."""
    with zipfile.ZipFile(zip_path) as z:
        with z.open(_member(z, DAM_ESR_MEMBER)) as f:
            df = pd.read_csv(f, usecols=list(DAM_ESR_COLS), dtype=str, keep_default_na=False)
    df = df.rename(columns=DAM_ESR_COLS)
    if resources is not None:
        df = df[df["resource"].isin(set(resources))]
    df = df.copy()
    day = pd.to_datetime(df["date"], format="%m/%d/%Y").dt.tz_localize("America/Chicago")
    he = pd.to_numeric(df["he"], errors="coerce")
    df["hour_utc"] = day.dt.tz_convert("UTC") + pd.to_timedelta(he - 1, unit="h")   # exact except on DST days (not in Jul-Aug)
    for c in df.columns:
        if c in ("date", "he", "resource", "status", "settlement_point", "hour_utc"):
            continue
        df[c] = pd.to_numeric(df[c].replace("", np.nan), errors="coerce")
    for c in [c for c in df.columns if c.startswith("dam_")]:
        df[c] = df[c].fillna(0.0)
    return df.drop(columns=["date", "he"]).sort_values(["resource", "hour_utc"]).reset_index(drop=True)


def staged_files(kind: str) -> list[Path]:
    """Downloaded daily zips of 'sced' or 'dam', sorted by operating day (from the manifest)."""
    rows = [r for r in read_manifest() if r["report_key"] == kind]
    rows.sort(key=lambda r: r["operating_day"])
    return [Path(r["file"]) for r in rows if Path(r["file"]).exists()]


def staged_days(kind: str) -> dict[str, Path]:
    return {r["operating_day"]: Path(r["file"]) for r in read_manifest()
            if r["report_key"] == kind and Path(r["file"]).exists()}


def derived_dir() -> Path:
    return staging_dir() / "derived"


def cache_esr_days(kind: str = "sced", log=print) -> list[Path]:
    """Parse every staged daily zip once into derived/esr_<kind>/<operating_day>.parquet (all ESRs, selected columns).
    Re-runs skip days already parsed."""
    out_dir = derived_dir() / f"esr_{kind}"
    out_dir.mkdir(parents=True, exist_ok=True)
    reader = read_esr_sced if kind == "sced" else read_esr_dam
    paths_out = []
    for day, zp in sorted(staged_days(kind).items()):
        dest = out_dir / f"{day}.parquet"
        if not dest.exists():
            df = reader(zp)
            tmp = dest.with_suffix(".tmp")
            df.to_parquet(tmp, index=False)
            tmp.replace(dest)
            log(f"  parsed {kind} {day}: {len(df):,} rows")
        paths_out.append(dest)
    return paths_out


def load_cached(kind: str = "sced", resources: Optional[Iterable[str]] = None) -> pd.DataFrame:
    parts = []
    for p in sorted((derived_dir() / f"esr_{kind}").glob("*.parquet")):
        d = pd.read_parquet(p)
        if resources is not None:
            d = d[d["resource"].isin(set(resources))]
        parts.append(d)
    return pd.concat(parts, ignore_index=True) if parts else pd.DataFrame()


# ==================================================================================================================
# Part C: choose the pilot ESR, build prices and an ActualDispatch
# ==================================================================================================================
from dataclasses import dataclass, field

from ..contracts import ActualDispatch, BatterySpec, as_award_col, as_price_col
from ..config import DT_RT_H

HUBS = ("HB_NORTH", "HB_SOUTH", "HB_WEST", "HB_HOUSTON", "HB_PAN", "HB_BUSAVG", "HB_HUBAVG")   # REAL: ERCOT hubs
LZ_HUB = {"LZ_NORTH": "HB_NORTH", "LZ_SOUTH": "HB_SOUTH", "LZ_WEST": "HB_WEST", "LZ_HOUSTON": "HB_HOUSTON"}
STEP = pd.Timedelta(minutes=15)
MIN_COVER_S = 0.8 * 900.0                 # ASSUMPTION: an interval needs >= 80% of its seconds covered by SCED runs
DEFAULT_WEAR = 20.0                       # ASSUMPTION: same $/MWh discharge wear as the research arena


@dataclass(frozen=True)
class PilotCriteria:
    """Pre-registered selection rules (written before looking at which ESR they pick). Every rule is a measured fact."""
    min_mw: float = 10.0                  # PRD first user: asset manager with a 10-100 MW battery
    max_mw: float = 100.0
    min_days: int = 40                    # present in (nearly) every staged day
    max_basis_rmse: float = 5.0           # $/MWh, 15-min node minus best hub over the 7-day nodal window
    max_abs_bias: float = 2.0             # $/MWh
    min_cycles_day: float = 0.3           # discharged MWh per day over usable MWh: the unit actually trades
    min_award_share: float = 0.10         # share of SCED runs with any RT ancillary award: exercises the AS term
    single_unit_node: bool = True         # the resource node holds only this unit in NP4-160


def _grid_paths():
    base = paths.GRID_RAW_ERCOT / "NP4-160-SG"
    vers = sorted(p for p in base.glob("CIM_*_SP_List_and_EB_Mapping") if p.is_dir())
    return vers[-1] if vers else None


def read_node_mapping() -> pd.DataFrame:
    """NP4-160: resource node -> settlement load zone (from the research repo settlement_points.csv, ERCOT Oct ML1
    model) and the number of units mapped to the node (Resource_Node_to_Unit)."""
    sp = pd.read_csv(paths.GRID_DIR / "data" / "settlement_points.csv", dtype=str,
                     usecols=["RESOURCE_NODE", "SETTLEMENT_LOAD_ZONE", "SUBSTATION"])
    sp = sp[sp["RESOURCE_NODE"].notna() & (sp["RESOURCE_NODE"] != "")]
    node = sp.groupby("RESOURCE_NODE").agg(load_zone=("SETTLEMENT_LOAD_ZONE", lambda s: s.mode().iloc[0]),
                                           substation=("SUBSTATION", lambda s: s.mode().iloc[0]),
                                           n_buses=("SUBSTATION", "size"))
    d = _grid_paths()
    if d is not None:
        f = next(iter(sorted(d.glob("Resource_Node_to_Unit_*.csv"))), None)
        if f is not None:
            r2u = pd.read_csv(f, dtype=str)
            node["units_at_node"] = r2u.groupby("RESOURCE_NODE")["UNIT_NAME"].nunique()
            node["mapping_file"] = f.name
    return node


def read_spp_window() -> pd.DataFrame:
    """The 7-day 15-minute nodal SPP window (NP6-905) as a wide table: interval_start_utc x settlement point."""
    long = pd.read_parquet(paths.GRID_DERIVED / "spp_node.parquet",
                           columns=["settlement_point", "interval_start_utc", "price"])
    return long.pivot_table(index="interval_start_utc", columns="settlement_point", values="price")


def basis_vs_hubs(nodes: Iterable[str], window: Optional[pd.DataFrame] = None) -> pd.DataFrame:
    """For each node: error statistics of every hub as a proxy for the node over the 7-day window; keeps the best hub
    (lowest RMSE). basis = node - hub ($/MWh)."""
    w = read_spp_window() if window is None else window
    rows = []
    for n in nodes:
        if n not in w.columns:
            rows.append(dict(node=n, in_window=False))
            continue
        x = w[n]
        best = None
        for h in HUBS:
            if h not in w.columns:
                continue
            d = (x - w[h]).dropna()
            r = dict(hub=h, rmse=float(np.sqrt((d ** 2).mean())), bias=float(d.mean()), mae=float(d.abs().mean()),
                     corr=float(x.corr(w[h])), p95_abs=float(d.abs().quantile(0.95)), n=int(len(d)))
            if best is None or r["rmse"] < best["rmse"]:
                best = r
        rows.append(dict(node=n, in_window=True, **best))
    return pd.DataFrame(rows).set_index("node")


def scan_esrs(sced: pd.DataFrame, dam: pd.DataFrame) -> pd.DataFrame:
    """One row per ESR over the staged days: size, node, activity, derates and award participation (all measured)."""
    s = sced.copy()
    s["day"] = s["ts_utc"].dt.tz_convert("America/Chicago").dt.date
    g = s.groupby("resource")
    nameplate = g["hsl_mw"].transform("max")
    soc_head = s["soc_mwh"] - s["min_soc_mwh"]
    s["derated"] = (s["hsl_mw"] < 0.95 * nameplate) & (soc_head > nameplate * (SCED_RUN_S / 3600.0))
    aw = [c for c in s.columns if c.startswith("aw_")]
    s["has_award"] = s[aw].sum(axis=1) > 0
    s["dis_mwh"] = s["net_mw"].clip(lower=0) * (SCED_RUN_S / 3600.0)
    g = s.groupby("resource")
    st = pd.DataFrame({"days": g["day"].nunique(), "runs": g.size(), "hsl_max_mw": g["hsl_mw"].max(),
                       "lsl_min_mw": g["lsl_mw"].min(), "max_soc_mwh": g["max_soc_mwh"].max(),
                       "min_soc_mwh": g["min_soc_mwh"].min(), "derate_share": g["derated"].mean(),
                       "award_share": g["has_award"].mean()})
    st["duration_h"] = st["max_soc_mwh"] / st["hsl_max_mw"]
    usable = (st["max_soc_mwh"] - st["min_soc_mwh"].clip(lower=0)).replace(0, np.nan)
    st["cycles_per_day"] = g["dis_mwh"].sum() / st["days"] / usable
    st["runs_per_day"] = st["runs"] / st["days"]
    st["node"] = dam.groupby("resource")["settlement_point"].agg(lambda x: x.mode().iloc[0] if len(x.mode()) else None)
    return st


@dataclass
class PilotSelection:
    table: pd.DataFrame                   # every ESR with the measurements and the pass/fail of each rule
    chosen: str
    node: str
    hub: str
    criteria: PilotCriteria
    reason: str


def select_pilot(sced: pd.DataFrame, dam: pd.DataFrame, criteria: PilotCriteria = PilotCriteria()) -> PilotSelection:
    st = scan_esrs(sced, dam)
    nm = read_node_mapping()
    st = st.join(nm[["load_zone", "units_at_node"]], on="node")
    st["maps_via_np4160"] = st["node"].isin(nm.index)
    bs = basis_vs_hubs(st["node"].dropna().unique())
    st = st.join(bs, on="node")
    st["natural_hub"] = st["load_zone"].map(LZ_HUB)
    c = criteria
    st["ok_size"] = st["hsl_max_mw"].between(c.min_mw, c.max_mw)
    st["ok_rows"] = st["days"] >= c.min_days
    st["ok_map"] = st["maps_via_np4160"] & st["in_window"].fillna(False).astype(bool)
    st["ok_basis"] = (st["rmse"] <= c.max_basis_rmse) & (st["bias"].abs() <= c.max_abs_bias)
    st["ok_activity"] = (st["cycles_per_day"] >= c.min_cycles_day) & (st["award_share"] >= c.min_award_share)
    st["ok_node_alone"] = (st["units_at_node"] == 1) if c.single_unit_node else True
    rules = ["ok_size", "ok_rows", "ok_map", "ok_basis", "ok_activity", "ok_node_alone"]
    st["passes_all"] = st[rules].fillna(False).all(axis=1)
    ranked = st[st["passes_all"]].sort_values("rmse")
    if ranked.empty:
        raise RuntimeError("no ESR passes every pilot rule; relax the criteria explicitly")
    chosen = ranked.index[0]
    reason = (f"{chosen} passes all {len(rules)} rules and has the lowest node-to-hub RMSE "
              f"(${ranked.iloc[0]['rmse']:.2f}/MWh vs {ranked.iloc[0]['hub']}) among the {len(ranked)} that do")
    return PilotSelection(st.sort_values(["passes_all", "rmse"], ascending=[False, True]), chosen,
                          str(ranked.iloc[0]["node"]), str(ranked.iloc[0]["hub"]), criteria, reason)


# ------------------------------------------------------------------------------------------------ prices
def read_hub_rt(hub: str, start_utc, end_utc) -> pd.Series:
    """Hub real-time SPP ($/MWh) on the 15-minute UTC grid from the NP6-785-ER yearly csv (REAL; the proxy for the node)."""
    frames = []
    for year in sorted({pd.Timestamp(start_utc).year, pd.Timestamp(end_utc).year}):
        f = paths.ZONAL_HISTORY / f"rtm{year}.csv"
        if f.exists():
            d = pd.read_csv(f, dtype={"rep": str})
            frames.append(d[d["sp"] == hub])
    d = pd.concat(frames, ignore_index=True)
    local = pd.to_datetime(d["date"]) + pd.to_timedelta((d["hour"] - 1) * 60 + (d["interval"] - 1) * 15, unit="m")
    ts = local.dt.tz_localize("America/Chicago", ambiguous=(d["rep"].to_numpy() != "Y"), nonexistent="shift_forward")
    s = pd.Series(d["price"].to_numpy(float), index=ts.dt.tz_convert("UTC"), name="rt_usd_per_mwh")
    s = s[~s.index.duplicated(keep="last")].sort_index()
    return s.loc[pd.Timestamp(start_utc): pd.Timestamp(end_utc) - STEP]


def read_as_rt15(start_utc, end_utc) -> pd.DataFrame:
    """Real-time ancillary clearing prices for capacity on the 15-minute grid (RT_15, since 5 Dec 2025; REAL)."""
    a = pd.read_parquet(paths.GRID_DERIVED / "as_prices.parquet",
                        columns=["market", "product", "interval_start_utc", "mcpc"])
    a = a[a["market"] == "RT_15"]
    w = a.pivot_table(index="interval_start_utc", columns="product", values="mcpc")
    w = w.rename(columns={p: as_price_col(p) for p in w.columns})
    return w.loc[pd.Timestamp(start_utc): pd.Timestamp(end_utc) - STEP]


def build_prices(hub: str, start_utc, end_utc) -> pd.DataFrame:
    """PriceFrame for the case study: hub RT SPP as the nodal proxy plus RT_15 ancillary prices. Raises on gaps."""
    idx = pd.date_range(pd.Timestamp(start_utc), pd.Timestamp(end_utc) - STEP, freq="15min")
    rt = read_hub_rt(hub, start_utc, end_utc).reindex(idx)
    as_ = read_as_rt15(start_utc, end_utc).reindex(idx)
    df = pd.concat([rt, as_], axis=1)
    missing = df.isna().sum()
    if missing.any():
        raise ValueError(f"price gaps in the case-study window: {missing[missing > 0].to_dict()}")
    return df


# ------------------------------------------------------------------------------------------------ SCED -> 15 minutes
def _pieces(t: np.ndarray, g0: float, n: int) -> pd.DataFrame:
    """Split each SCED run [t_i, t_i + cover_i) (seconds since epoch) at 15-minute boundaries: (run, interval, seconds)."""
    nxt = np.r_[t[1:], t[-1] + SCED_RUN_S]
    cover = np.minimum(nxt - t, MAX_RUN_COVER_S)
    k_start = np.floor((t - g0) / 900.0).astype(int)
    end = t + cover
    k_end = np.floor((end - g0 - 1e-9) / 900.0).astype(int)
    rows = []
    for i in range(len(t)):
        for k in range(k_start[i], k_end[i] + 1):
            lo = max(t[i], g0 + k * 900.0)
            hi = min(end[i], g0 + (k + 1) * 900.0)
            if hi > lo and 0 <= k < n:
                rows.append((i, k, hi - lo))
    return pd.DataFrame(rows, columns=["run", "k", "sec"])


def _epoch_s(ts: pd.Series) -> np.ndarray:
    return ts.dt.tz_convert("UTC").dt.tz_localize(None).to_numpy().astype("datetime64[ns]").astype("int64") / 1e9


def estimate_aux_load(rows: pd.DataFrame) -> float:
    """Standing station-service draw (MW >= 0): minus the median telemetered net output over runs whose base point is
    zero. DERIVED. It is a real cost the owner pays, but it does not charge the battery (the SOC does not move), so it is
    taken out of the dispatch and reported as a memo item; it is the same in every counterfactual and cancels in the gap."""
    idle = rows[rows["base_point_mw"].abs() < 0.05]
    if len(idle) < 100:
        return 0.0
    return float(max(-idle["net_mw"].median(), 0.0))


def aggregate_runs(rows: pd.DataFrame, idx: pd.DatetimeIndex, nameplate_mw: float, nameplate_charge_mw: float,
                   eta_c: float, eta_d: float, aux_mw: float = 0.0) -> pd.DataFrame:
    """SCED runs of ONE resource -> the 15-minute grid: time-weighted flows, end-of-interval SOC, derate-only availability.
    `net_mw` is the dispatch (telemetered net output plus the standing auxiliary draw `aux_mw`)."""
    r = rows.sort_values("ts_utc").reset_index(drop=True)
    n = len(idx)
    t = _epoch_s(r["ts_utc"])
    g0 = idx[0].tz_convert("UTC").tz_localize(None).value / 1e9
    pc = _pieces(t, g0, n)
    # a reduced HSL/LSL with the SOC near its floor/ceiling is the SOC constraint, not a derate
    head_dis = (r["soc_mwh"] - r["min_soc_mwh"]).to_numpy()
    head_chg = (r["max_soc_mwh"] - r["soc_mwh"]).to_numpy()
    run_h = SCED_RUN_S / 3600.0
    lim_dis = head_dis < nameplate_mw * run_h / eta_d
    lim_chg = head_chg < nameplate_charge_mw * run_h * eta_c
    hsl_eff = np.where(lim_dis, nameplate_mw, r["hsl_mw"].to_numpy())
    lsl_eff = np.where(lim_chg, nameplate_charge_mw, -r["lsl_mw"].to_numpy())      # positive charge limit
    vals = pd.DataFrame({"net_mw": r["net_mw"].to_numpy() + aux_mw, "net_telemetered_mw": r["net_mw"].to_numpy(),
                         "base_point_mw": r["base_point_mw"].to_numpy(),
                         "hsl_mw": r["hsl_mw"].to_numpy(), "lsl_mw": r["lsl_mw"].to_numpy(),
                         "available_mw": np.minimum(hsl_eff, nameplate_mw),
                         "available_charge_mw": np.minimum(lsl_eff, nameplate_charge_mw)})
    for p, cols in STD_AS_MAP.items():
        vals[as_award_col(p)] = r[list(cols)].sum(axis=1).to_numpy()
    p = pc.join(vals, on="run")
    w = p["sec"].to_numpy()
    out = pd.DataFrame(index=range(n))
    cover = pd.Series(w).groupby(p["k"].to_numpy()).sum().reindex(range(n)).fillna(0.0)
    for c in vals.columns:
        num = pd.Series(p[c].to_numpy() * w).groupby(p["k"].to_numpy()).sum().reindex(range(n))
        out[c] = (num / cover.replace(0.0, np.nan)).to_numpy()
    # tightest SOC limits seen in the interval
    pk = pd.DataFrame({"k": p["k"].to_numpy(), "minsoc": r["min_soc_mwh"].to_numpy()[p["run"].to_numpy()],
                       "maxsoc": r["max_soc_mwh"].to_numpy()[p["run"].to_numpy()]})
    out["soc_min_mwh"] = pk.groupby("k")["minsoc"].max().reindex(range(n)).to_numpy()
    out["soc_max_mwh"] = pk.groupby("k")["maxsoc"].min().reindex(range(n)).to_numpy()
    # end-of-interval SOC: the SOC reported by the first run at or after the interval end (the state after the interval)
    ends = g0 + (np.arange(n) + 1) * 900.0
    j = np.searchsorted(t, ends - 1e-6)
    soc = r["soc_mwh"].to_numpy()
    jj = np.minimum(j, len(t) - 1)
    gap = np.abs(t[jj] - ends)                        # past the last run the nearest report is the last run itself
    out["soc_mwh"] = np.where(gap <= MAX_RUN_COVER_S, soc[jj], np.nan)
    out["coverage_s"] = cover.to_numpy()
    out.index = idx
    bad = (out["coverage_s"] < MIN_COVER_S).to_numpy()
    flow_cols = list(vals.columns) + ["soc_min_mwh", "soc_max_mwh"]
    out.loc[bad, flow_cols] = np.nan
    return out


def estimate_efficiency(rows: pd.DataFrame, nameplate_mw: float, aux_mw: float = 0.0) -> dict:
    """Round-trip efficiency from the telemetry itself, three ways (DERIVED):

    * balance  (used): losses on the charging side as in the arena convention (eta_d = 1), eta_c chosen so that the
      whole period's energy balance closes: eta_c = (total SOC change + MWh discharged) / MWh charged;
    * medians  SOC change per MWh charged / discharged between consecutive SCED runs, runs moving >= 20% of nameplate;
    * LS       least-squares fit of the SOC change on charged and discharged MWh."""
    r = rows.sort_values("ts_utc").reset_index(drop=True)
    dt_h = r["ts_utc"].diff().dt.total_seconds() / 3600.0
    dsoc = r["soc_mwh"].diff()
    net = (r["net_mw"] + aux_mw).shift(1)             # output during the previous run produced the SOC change seen now
    ok = (dt_h > 0.06) & (dt_h < 0.11)
    chg = (-net).clip(lower=0) * dt_h
    dis = net.clip(lower=0) * dt_h
    mc = ok & (-net > 0.2 * nameplate_mw)
    md = ok & (net > 0.2 * nameplate_mw)
    eta_c = float((dsoc[mc] / chg[mc]).median())
    inv_eta_d = float((-dsoc[md] / dis[md]).median())
    X = np.column_stack([chg[ok], -dis[ok]])
    coef, *_ = np.linalg.lstsq(X, dsoc[ok].to_numpy(), rcond=None)
    balance = float((dsoc[ok].sum() + dis[ok].sum()) / chg[ok].sum())
    return dict(eta_charge_balance=balance, rte_balance=balance,
                eta_charge_median=eta_c, inv_eta_discharge_median=inv_eta_d, rte_median=eta_c / inv_eta_d,
                eta_charge_ls=float(coef[0]), inv_eta_discharge_ls=float(coef[1]), rte_ls=float(coef[0] / coef[1]),
                n_charge_runs=int(mc.sum()), n_discharge_runs=int(md.sum()))


def dam_position_parts(dam: pd.DataFrame, prices: pd.DataFrame, idx: pd.DatetimeIndex) -> pd.DataFrame:
    """Settlement value, per 15-minute interval, of the day-ahead positions relative to real time (DERIVED), by part:

        energy:  DAM award MW * dt * (DAM nodal SPP - RT price)
        <AS p>:  DAM award_p MW * dt * (DAM MCPC_p - RT MCPC_p)         one column per product (lower case)

    RTC+B settles the day-ahead awards financially against the real-time prices; the physical RT dispatch is valued
    separately at RT prices. Hourly DAM data is repeated over the four intervals of the hour. The DAM SPP is ERCOT's own
    nodal price at the unit's resource node (REAL); the RT price is the hub proxy."""
    d = dam.set_index("hour_utc").sort_index()
    d = d[~d.index.duplicated(keep="last")]
    h = d.reindex(idx.floor("h"))
    rt = prices.loc[idx, "rt_usd_per_mwh"].to_numpy()
    out = {"energy": np.nan_to_num(h["dam_energy_mw"].fillna(0.0).to_numpy() * DT_RT_H * (h["dam_spp"].to_numpy() - rt))}
    pairs = {"REGUP": (["dam_regup"], "mcpc_regup"), "REGDN": (["dam_regdn"], "mcpc_regdn"),
             "RRS": (["dam_rrspfr", "dam_rrsffr", "dam_rrsufr"], "mcpc_rrs"), "ECRS": (["dam_ecrs"], "mcpc_ecrs"),
             "NSPIN": (["dam_nspin"], "mcpc_nspin")}
    for p, (cols, mc) in pairs.items():
        col = as_price_col(p)
        if col not in prices.columns:
            out[p.lower()] = np.zeros(len(idx))
            continue
        award = h[cols].sum(axis=1).fillna(0.0).to_numpy()
        diff = np.nan_to_num(h[mc].to_numpy() - prices.loc[idx, col].to_numpy())
        out[p.lower()] = award * DT_RT_H * diff
    return pd.DataFrame(out, index=idx)


def dam_position(dam: pd.DataFrame, prices: pd.DataFrame, idx: pd.DatetimeIndex) -> pd.Series:
    """Total day-ahead position value per interval (sum of `dam_position_parts`)."""
    return dam_position_parts(dam, prices, idx).sum(axis=1).rename("position_usd")


@dataclass
class PilotData:
    spec: BatterySpec
    charge_limit_mw: float
    actual: ActualDispatch
    prices: pd.DataFrame
    position_usd: pd.Series
    selection: PilotSelection
    meta: dict = field(default_factory=dict)
    dam: Optional[pd.DataFrame] = None
    sced_rows: Optional[pd.DataFrame] = None
    position_parts: Optional[pd.DataFrame] = None


def build_pilot(first_day: str = "2026-07-01", last_day: str = "2026-08-11", criteria: PilotCriteria = PilotCriteria(),
                wear_usd_per_mwh: float = DEFAULT_WEAR, resource: Optional[str] = None, log=print) -> PilotData:
    """Select the pilot ESR (unless `resource` is given), derive its spec from the telemetry and build the inputs of
    `attribution.engine.run_attribution`: the 15-minute ActualDispatch, the proxy PriceFrame and the day-ahead position."""
    sced_all = load_cached("sced")
    dam_all = load_cached("dam")
    sel = select_pilot(sced_all, dam_all, criteria)
    res = resource or sel.chosen
    log(f"pilot: {res} ({sel.reason})")
    rows = sced_all[sced_all["resource"] == res]
    dam = dam_all[dam_all["resource"] == res].copy()
    node = str(dam["settlement_point"].mode().iloc[0])
    row = sel.table.loc[res]
    hub = str(row["hub"])
    p_dis = float(rows["hsl_mw"].max())
    p_chg = float(-rows["lsl_mw"].min())
    aux = estimate_aux_load(rows)
    eff = estimate_efficiency(rows, p_dis, aux)
    # the telemetry shows losses on the charging side (1/eta_d ~ 1): keep the arena convention (loss_side="charge")
    # with eta_c from the energy balance of the whole period
    rte = float(np.clip(eff["rte_balance"], 0.5, 1.0))
    spec = BatterySpec(asset_id=res, settlement_point=node, power_mw=p_dis, energy_mwh=float(rows["max_soc_mwh"].max()),
                       soc_min_mwh=0.0, rte=round(rte, 3), loss_side="charge", wear_usd_per_mwh=wear_usd_per_mwh)
    start = pd.Timestamp(first_day).tz_localize("America/Chicago").tz_convert("UTC")
    end = (pd.Timestamp(last_day) + pd.Timedelta(days=1)).tz_localize("America/Chicago").tz_convert("UTC")
    idx = pd.date_range(start, end - STEP, freq="15min")
    win = rows[(rows["ts_utc"] >= start - pd.Timedelta(minutes=30)) & (rows["ts_utc"] < end + pd.Timedelta(minutes=30))]
    agg = aggregate_runs(win, idx, p_dis, p_chg, spec.eta_charge, spec.eta_discharge, aux)
    actual = ActualDispatch(asset_id=res, source="ercot-60day-sced", frame=agg,
                            notes=(f"60-day SCED disclosure NP3-965-ER (ESR data), {first_day}..{last_day}",
                                   f"net_mw = telemetered net output + {aux:.2f} MW standing auxiliary draw, time-weighted over SCED runs",
                                   "available_mw/available_charge_mw = HSL/-LSL excluding SOC-limited runs (derates only)"))
    prices = build_prices(hub, start, end)
    parts = dam_position_parts(dam, prices, idx)
    pos = parts.sum(axis=1).rename("position_usd")
    aux_cost = float((aux * DT_RT_H * prices["rt_usd_per_mwh"]).sum())
    meta = dict(resource=res, node=node, hub=hub, first_day=first_day, last_day=last_day, p_dis_mw=p_dis, p_chg_mw=p_chg,
                aux_load_mw=aux, aux_load_cost_usd=aux_cost,
                efficiency=eff, rte_used=spec.rte, selection_reason=sel.reason, natural_hub=row.get("natural_hub"),
                hub_stats={k: float(row[k]) for k in ("rmse", "bias", "mae", "corr", "p95_abs", "n")},
                load_zone=row.get("load_zone"), dam_hours=int(len(dam)), runs=int(len(win)))
    return PilotData(spec, p_chg, actual, prices, pos, sel, meta, dam, win, parts)


# ==================================================================================================================
# Part D: day-ahead hub prices from the same 60-day DAM disclosure (a same-period check on the nodal proxy)
# ==================================================================================================================
DAM_AWARD_MEMBER = "60d_DAM_EnergyBidAwards"         # REAL: carries the DAM settlement point price of every point that cleared


def read_dam_hub_spp(zip_path) -> pd.DataFrame:
    """DAM settlement point prices of the hubs and load zones for one operating day, from the energy bid awards
    (every hub and zone clears bids in every hour, with one consistent price per hour). Columns: hour_utc,
    settlement_point, price."""
    with zipfile.ZipFile(zip_path) as z:
        with z.open(_member(z, DAM_AWARD_MEMBER)) as f:
            df = pd.read_csv(f, usecols=["Delivery Date", "Hour Ending", "Settlement Point", "Settlement Point Price"],
                             dtype=str, keep_default_na=False)
    df = df[df["Settlement Point"].str.startswith(("HB_", "LZ_"))]
    df = df.drop_duplicates(["Delivery Date", "Hour Ending", "Settlement Point"])
    day = pd.to_datetime(df["Delivery Date"], format="%m/%d/%Y").dt.tz_localize("America/Chicago")
    he = pd.to_numeric(df["Hour Ending"], errors="coerce")
    out = pd.DataFrame({"hour_utc": day.dt.tz_convert("UTC") + pd.to_timedelta(he - 1, unit="h"),
                        "settlement_point": df["Settlement Point"].to_numpy(),
                        "price": pd.to_numeric(df["Settlement Point Price"], errors="coerce").to_numpy()})
    return out.dropna().sort_values(["settlement_point", "hour_utc"]).reset_index(drop=True)


def cache_dam_hubs(log=print) -> list[Path]:
    """Parse every staged DAM zip once into derived/dam_hub_spp/<operating_day>.parquet."""
    out_dir = derived_dir() / "dam_hub_spp"
    out_dir.mkdir(parents=True, exist_ok=True)
    made = []
    for day, zp in sorted(staged_days("dam").items()):
        dest = out_dir / f"{day}.parquet"
        if not dest.exists():
            tmp = dest.with_suffix(".tmp")
            read_dam_hub_spp(zp).to_parquet(tmp, index=False)
            tmp.replace(dest)
            log(f"  parsed dam hub prices {day}")
        made.append(dest)
    return made


def load_dam_hubs() -> pd.DataFrame:
    parts = [pd.read_parquet(p) for p in sorted((derived_dir() / "dam_hub_spp").glob("*.parquet"))]
    return pd.concat(parts, ignore_index=True) if parts else pd.DataFrame(columns=["hour_utc", "settlement_point", "price"])


# ==================================================================================================================
# Command line: python -m dispatch.attribution.disclosure download 2026-07-01 2026-08-11 [sced|dam ...]
# ==================================================================================================================
def _main(argv: list) -> int:
    if len(argv) >= 3 and argv[0] == "download":
        first, last = date.fromisoformat(argv[1]), date.fromisoformat(argv[2])
        kinds = argv[3:] or ["sced", "dam"]
        docs = []
        for k in kinds:
            docs += select_days(list_docs(k), first, last)
        print(f"{len(docs)} files, about {sum(d['size'] for d in docs) / 1e9:.2f} GB; staging {staging_dir()}")
        out = download(docs)
        print(out)
        return 1 if out["failed"] else 0
    print("usage: python -m dispatch.attribution.disclosure download FIRST_OPERATING_DAY LAST_OPERATING_DAY [sced] [dam]")
    return 2


if __name__ == "__main__":
    import sys
    raise SystemExit(_main(sys.argv[1:]))
