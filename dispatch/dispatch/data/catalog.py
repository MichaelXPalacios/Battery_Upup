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


def freshness(now_utc=None, report_ids: Optional[Iterable] = None) -> dict[str, float]:
    """{report_id: age_s}: seconds from the newest published_utc in the catalog to `now_utc` (tz-aware; default: now).

    Only reports present in the catalog appear. Pass `report_ids` (ids or MIS reportTypeIds) to restrict the answer.
    A negative age cannot occur for real data; it is clipped to 0 for files stamped slightly in the future."""
    now = utc(now_utc) if now_utc is not None else pd.Timestamp.now(tz="UTC")
    newest: dict[str, pd.Timestamp] = {}
    for e in lake.catalog_entries():
        t = utc(e["published_utc"])
        if e["report_id"] not in newest or t > newest[e["report_id"]]:
            newest[e["report_id"]] = t
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
