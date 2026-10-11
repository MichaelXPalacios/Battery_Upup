"""Read-only views of what the lake holds: freshness per report, raw-file summary, parsed-dataset coverage.

`freshness(now_utc)` is the contract D2's monitoring reads (DESIGN.md Interfaces): seconds since the NEWEST `published_utc`
of each report in `lake/catalog.jsonl`. ERCOT publish time, not our ingest time, so a stalled poller shows up as growing age.
"""
from __future__ import annotations

from typing import Iterable, Optional

import pandas as pd

from .. import paths
from . import lake, parse, reports
from .times import utc

# Reports whose age is the age of their DATA, not of the file that carried it (P2, 11 Oct 2026). An ERCOT file is stamped with the moment
# ERCOT posted it, so the age of the newest file is the age of the newest information. A fetched response is stamped with the moment we
# held it, which says only that the fetcher ran: a fetch that brought nothing new would look fresh. For GHCND the age is therefore taken from the
# newest `published_utc` of the parsed weather observations (the first time the newest day was seen). EIA's page is stamped with its own release
# time, so RNGWHHD needs no help, but until the first page has been fetched it borrows the adopted FRED file's time, so that a lake whose fetcher
# never ran goes stale in the monitor instead of being absent from it.
DATA_CURRENCY = {"GHCND": "weather_obs"}                       # report id -> parsed dataset whose newest published_utc is the report's age
BORROWS_UNTIL_FETCHED = {"RNGWHHD": "DHHNGSP"}                 # report id -> the adopted report whose time stands in until the first fetch
_NEWEST_PUB: dict = {}                                         # (partition path, mtime, size) -> newest published_utc in it


def newest_row_published(dataset: str) -> Optional[pd.Timestamp]:
    """Newest `published_utc` in the newest monthly partition of a parsed dataset (None if nothing is parsed). Cached per file version, so the
    monitor can call it every pass for the price of a stat."""
    files = parse.partition_files(dataset)
    if not files:
        return None
    p = files[-1]
    st = p.stat()
    key = (str(p), st.st_mtime_ns, st.st_size)
    if key not in _NEWEST_PUB:
        import pyarrow.compute as pc
        import pyarrow.parquet as pq
        for k in [k for k in _NEWEST_PUB if k[0] == key[0]]:
            del _NEWEST_PUB[k]
        mx = pc.max(pq.read_table(p, columns=["published_utc"]).column(0)).as_py()
        _NEWEST_PUB[key] = utc(mx) if mx is not None else None
    return _NEWEST_PUB[key]


def freshness(now_utc=None, report_ids: Optional[Iterable] = None) -> dict[str, float]:
    """{report_id: age_s}: seconds from the newest published_utc in the catalog to `now_utc` (tz-aware; default: now).

    Only reports present in the catalog appear (plus RNGWHHD, which stands in for the adopted gas file until it is fetched). Pass `report_ids`
    (ids or MIS reportTypeIds) to restrict the answer. A negative age cannot occur for real data; it is clipped to 0 for files stamped slightly in
    the future. GHCND is the age of the newest observation published, not of the newest file (see DATA_CURRENCY)."""
    now = utc(now_utc) if now_utc is not None else pd.Timestamp.now(tz="UTC")
    newest: dict[str, pd.Timestamp] = {}
    for e in lake.catalog_entries():
        t = utc(e["published_utc"])
        if e["report_id"] not in newest or t > newest[e["report_id"]]:
            newest[e["report_id"]] = t
    for rid, dataset in DATA_CURRENCY.items():
        if rid in newest:
            t = newest_row_published(dataset)
            if t is not None:
                newest[rid] = t
    for rid, lender in BORROWS_UNTIL_FETCHED.items():
        if rid not in newest and lender in newest:
            newest[rid] = newest[lender]
    if report_ids is not None:
        keep = {reports.canonical_id(r) for r in report_ids}
        newest = {k: v for k, v in newest.items() if k in keep}
    return {rid: max(0.0, (now - t).total_seconds()) for rid, t in sorted(newest.items())}


def newest_published(report_id) -> Optional[pd.Timestamp]:
    rid = reports.canonical_id(report_id)
    times = [utc(e["published_utc"]) for e in lake.catalog_entries(rid)]
    return max(times) if times else None


def raw_summary(now_utc=None) -> pd.DataFrame:
    """One row per (source, report_id): files, bytes, first and last published_utc, age_s, origins."""
    now = utc(now_utc) if now_utc is not None else pd.Timestamp.now(tz="UTC")
    rows = {}
    for e in lake.catalog_entries():
        r = rows.setdefault((e["source"], e["report_id"]), {"files": 0, "bytes": 0, "first": None, "last": None, "origins": set()})
        t = utc(e["published_utc"])
        r["files"] += 1
        r["bytes"] += int(e["bytes"])
        r["first"] = t if r["first"] is None or t < r["first"] else r["first"]
        r["last"] = t if r["last"] is None or t > r["last"] else r["last"]
        r["origins"].add(e["origin"].split(":")[0])
    out = [{"source": s, "report_id": rid, "files": v["files"], "mb": round(v["bytes"] / 1e6, 2), "first_published_utc": v["first"],
            "last_published_utc": v["last"], "age_s": max(0.0, (now - v["last"]).total_seconds()),
            "origins": ",".join(sorted(v["origins"]))} for (s, rid), v in sorted(rows.items())]
    return pd.DataFrame(out)


def dataset_coverage() -> pd.DataFrame:
    """One row per parsed dataset: partitions, rows, first/last interval and publish time, size on disk, publish-time basis.
    One DuckDB aggregate per dataset over the parquet files (a few seconds for the whole lake)."""
    from . import duck
    con = duck.connect()
    out = []
    for name in parse.DATASETS:
        files = parse.partition_files(name)
        if not files:
            out.append({"dataset": name, "partitions": 0, "rows": 0})
            continue
        n, t0, t1, p0, p1 = con.sql(
            f"SELECT count(*), min(interval_start_utc), max(interval_start_utc), min(published_utc), max(published_utc) FROM {name}").fetchone()
        basis = [r[0] for r in con.sql(f"SELECT DISTINCT pub_basis FROM {name} ORDER BY 1").fetchall()]
        out.append({"dataset": name, "partitions": len(files), "rows": int(n), "first_interval_utc": pd.Timestamp(t0), "last_interval_utc": pd.Timestamp(t1),
                    "first_published_utc": pd.Timestamp(p0), "last_published_utc": pd.Timestamp(p1),
                    "mb": round(sum(f.stat().st_size for f in files) / 1e6, 2), "pub_basis": ",".join(basis)})
    return pd.DataFrame(out)


# ------------------------------------------------------------------------------------------------ point in time: which version was visible
VERSION_KEYS = {"weather_obs": ["station", "interval_start_utc"], "gas_hh": ["interval_start_utc"]}


def latest_visible(df: pd.DataFrame, keys: list, asof_utc) -> pd.DataFrame:
    """One row per key: the version with the greatest `published_utc` that is <= `asof_utc` (inclusive, like `lake.iter_raw`: a row feeds a decision
    when published_utc <= asof). Keys with no visible version are absent, so a value is never seen before its publication. Ties on published_utc
    keep the last row in the frame's order. The result is sorted by `keys`."""
    asof = utc(asof_utc)
    seen = df[df["published_utc"] <= asof]
    if seen.empty:
        return seen.reset_index(drop=True)
    seen = seen.sort_values(["published_utc"], kind="stable")
    return seen.drop_duplicates(keys, keep="last").sort_values(keys, kind="stable").reset_index(drop=True)


def observations_asof(dataset: str, asof_utc, *, date_from: Optional[str] = None, date_to: Optional[str] = None) -> pd.DataFrame:
    """The weather observations ('weather_obs') or Henry Hub prices ('gas_hh') as they stood at `asof_utc`: for each station-day (trade date), the
    latest-published version visible then. A day whose first version is published after `asof_utc` is absent. `date_from` / `date_to` limit the
    monthly partitions read, as in `parse.read`."""
    if dataset not in VERSION_KEYS:
        raise KeyError(f"{dataset!r} has no versions to resolve; use one of {sorted(VERSION_KEYS)}")
    return latest_visible(parse.read(dataset, date_from=date_from, date_to=date_to), VERSION_KEYS[dataset], asof_utc)
