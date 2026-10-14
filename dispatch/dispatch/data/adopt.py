"""Adopt existing research data into the lake by COPYING it. The originals are never moved, edited or deleted.

* `adopt_grid_raw()`    ~10k MIS zips fetched by research agent 1a: research-data/grid/raw/ercot/<REPORT_ID>/
* `adopt_ercot_history()`  the yearly NP6-785-ER workbooks (2011 to 26 Sep 2026): research-data/ercot/
* `adopt_gas()`         Henry Hub daily, research-data/gas/DHHNGSP.csv (FRED series, copied from EIA)
* `adopt_weather()`     the arena's NOAA GHCN-Daily station files, research-data/weather/USW*.csv

Publish times. MIS file names carry theirs (REAL). The yearly workbooks keep their MIS name (resolved from the saved
listing `list13061.json`, which also has the offset-bearing PublishDate). Gas and weather files carry no publish time;
the lake file name records when the copy was fetched (`<name>.asof_<UTC time>.csv`, from the file's modification time),
and parse.py applies the named ASSUMPTION lag per row, capped at that fetch time.
"""
from __future__ import annotations

import csv
import hashlib
import json
import re
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Callable, Optional

import pandas as pd

from .. import paths
from . import lake, reports
from .times import parse_mis_publish, utc

ASOF_TAG = re.compile(r"\.asof_(\d{8}T\d{6}Z)\.")


def asof_tag(ts: pd.Timestamp) -> str:
    return utc(ts).strftime("%Y%m%dT%H%M%SZ")


def asof_from_name(name: str) -> Optional[pd.Timestamp]:
    """'DHHNGSP.asof_20261003T221208Z.csv' -> 2026-10-03 22:12:08+00:00 (None if the name has no tag)."""
    m = ASOF_TAG.search(name)
    return pd.Timestamp(m.group(1), tz="UTC") if m else None


def _origin(path: Path) -> str:
    try:
        rel = Path(path).resolve().relative_to(paths.RESEARCH_DATA.resolve())
    except ValueError:
        rel = Path(path)
    return "adopt:research-data/" + rel.as_posix() if not rel.is_absolute() else "adopt:" + rel.as_posix()


def _mtime_utc(path: Path) -> pd.Timestamp:
    return pd.Timestamp(path.stat().st_mtime, unit="s", tz="UTC").floor("s").as_unit("us")


def _manifest_hashes() -> dict[str, str]:
    """file name -> sha256, from research-data/grid/manifest.csv (the integrity check on adoption)."""
    out = {}
    if paths.GRID_MANIFEST.exists():
        with open(paths.GRID_MANIFEST, newline="", encoding="utf-8") as fh:
            for row in csv.DictReader(fh):
                out[Path(row["file"].replace("\\", "/")).name] = row["sha256"]
    return out


def adopt_grid_raw(src_root: Optional[Path] = None, log: Callable[[str], None] = lambda s: None, workers: int = 8) -> dict:
    """Copy the MIS zips under research-data/grid/raw/ercot/<REPORT_ID>/ into the lake (origin 'adopt:<path>').

    Only files that are real MIS zips (the cdr./rpt. naming) and match the report's file rule are taken: that skips the
    unpacked mapping folders and flat csv copies the research code left next to the NP4-160 zips. Each file's sha256 is
    checked against grid/manifest.csv; mismatches are reported, not hidden."""
    root = Path(src_root) if src_root else paths.GRID_RAW_ERCOT
    out = {"root": str(root), "per_report": {}, "copied": 0, "already_in_lake": 0, "skipped": 0, "manifest_mismatch": []}
    if not root.exists():
        out["error"] = "source folder not found"
        return out
    manifest = _manifest_hashes()
    work = []                                            # (report, file, publish time)
    for folder in sorted(p for p in root.iterdir() if p.is_dir()):
        try:
            rep = reports.get(folder.name)
        except KeyError:
            out["per_report"][folder.name] = {"skipped": "unknown report folder"}
            continue
        per = out["per_report"].setdefault(rep.report_id, {"copied": 0, "already_in_lake": 0, "skipped": 0})
        for f in sorted(folder.glob("*.zip")):
            name = f.name
            try:
                pub = parse_mis_publish(name)
            except ValueError:
                per["skipped"] += 1
                continue
            if rep.files == "csv" and not name.lower().endswith("_csv.zip"):
                per["skipped"] += 1
                continue
            work.append((rep, f, pub))

    def one(item):
        rep, f, pub = item
        existed = lake.has(rep.source, rep.report_id, f.name)
        p = lake.put(rep.source, rep.report_id, pub, f.name, f, _origin(f))
        bad = None
        if not existed:
            want = manifest.get(f.name)
            have = lake._fresh_state().by_key[(rep.source, rep.report_id, f.name)]["sha256"]
            if want and have != want:
                bad = f.name
        return rep.report_id, existed, bad

    with ThreadPoolExecutor(max_workers=workers) as ex:
        for rid, existed, bad in ex.map(one, work):
            per = out["per_report"][rid]
            per["already_in_lake" if existed else "copied"] += 1
            if bad:
                out["manifest_mismatch"].append(bad)
    for rid, per in out["per_report"].items():
        if "copied" in per:
            out["copied"] += per["copied"]
            out["already_in_lake"] += per["already_in_lake"]
            out["skipped"] += per["skipped"]
            log(f"  adopt {rid}: {per['copied']} copied, {per['already_in_lake']} already there, {per['skipped']} skipped")
    return out


def _zip_member_sha256(zip_path: Path) -> dict[str, str]:
    out = {}
    with zipfile.ZipFile(zip_path) as zf:
        for info in zf.infolist():
            h = hashlib.sha256()
            with zf.open(info) as fh:
                for chunk in iter(lambda: fh.read(1 << 20), b""):
                    h.update(chunk)
            out[Path(info.filename).name] = h.hexdigest()
    return out


def adopt_ercot_history(src: Optional[Path] = None, log: Callable[[str], None] = lambda s: None) -> dict:
    """The yearly NP6-785-ER (RTM load zone and hub prices) zips in research-data/ercot/, 2011 to 26 Sep 2026.

    The zip is the file ERCOT published; it holds the workbook. The loose `rpt.00013061...RTMLZHBSPP_<year>.xlsx` files
    next to them are checked to be byte-identical to the zip member and, if they are, NOT copied a second time (the lake
    holds those bytes once, inside the zip). A workbook that differs from its zip would be adopted under its own name.
    The arena's converted `rtm<year>.csv` files are derived, not raw, and are not adopted."""
    root = Path(src) if src else paths.ZONAL_HISTORY
    out = {"root": str(root), "copied": 0, "already_in_lake": 0, "workbook_identical_to_zip": 0, "workbook_adopted_separately": 0,
           "files": []}
    if not root.exists():
        out["error"] = "source folder not found"
        return out
    listing = {}
    lj = root / "list13061.json"
    if lj.exists():
        js = json.loads(lj.read_text(encoding="utf-8"))
        dl = js["ListDocsByRptTypeRes"]["DocumentList"]
        for x in (dl if isinstance(dl, list) else [dl]):
            d = x["Document"]
            listing[d["FriendlyName"]] = (d["ConstructedName"], pd.Timestamp(d["PublishDate"]).tz_convert("UTC"))
    rep = reports.get("NP6-785-ER")
    for z in sorted(root.glob("RTMLZHBSPP_*.zip")):
        key = z.stem
        if key not in listing:
            log(f"  {z.name}: not in list13061.json, skipped")
            continue
        name, pub = listing[key]
        existed = lake.has(rep.source, rep.report_id, name)
        p = lake.put(rep.source, rep.report_id, parse_mis_publish(name, hint=pub), name, z, _origin(z))
        out["already_in_lake" if existed else "copied"] += 1
        out["files"].append(name)
        # the loose workbook for the same year
        year = key.split("_")[-1]
        wb = next(iter(root.glob(f"rpt.00013061.*RTMLZHBSPP_{year}.xlsx")), None)
        if wb is not None:
            member = _zip_member_sha256(p).get(wb.name)
            if member is not None and member == lake._file_sha256(wb):
                out["workbook_identical_to_zip"] += 1
            else:
                lake.put(rep.source, rep.report_id, parse_mis_publish(name, hint=pub), wb.name, wb, _origin(wb))
                out["workbook_adopted_separately"] += 1
    log(f"  adopt NP6-785-ER: {out['copied']} copied, {out['already_in_lake']} already there, "
        f"{out['workbook_identical_to_zip']} workbooks identical to their zip")
    return out


def _adopt_plain(src: Path, source: str, report_id: str, log) -> Optional[Path]:
    """Copy a loose file whose publish time is unknown: the name records the copy's fetch time (file mtime)."""
    asof = _mtime_utc(src)
    stem, ext = src.stem, src.suffix
    name = f"{stem}.asof_{asof_tag(asof)}{ext}"
    return lake.put(source, report_id, asof, name, src, _origin(src))


def adopt_gas(src: Optional[Path] = None, log: Callable[[str], None] = lambda s: None) -> dict:
    """Henry Hub daily spot (FRED DHHNGSP, copied from EIA) -> lake/raw/fred/DHHNGSP/."""
    f = Path(src) if src else paths.RESEARCH_DATA / "gas" / "DHHNGSP.csv"
    if not f.exists():
        return {"error": f"{f} not found"}
    existed = lake.has("fred", "DHHNGSP", f"{f.stem}.asof_{asof_tag(_mtime_utc(f))}{f.suffix}")
    p = _adopt_plain(f, "fred", "DHHNGSP", log)
    log(f"  adopt gas: {p.name} ({'already there' if existed else 'copied'})")
    return {"files": [p.name], "copied": 0 if existed else 1}


def adopt_weather(src: Optional[Path] = None, log: Callable[[str], None] = lambda s: None) -> dict:
    """The arena's NOAA GHCN-Daily station files -> lake/raw/ncei/GHCND/ (one file per station and fetch)."""
    root = Path(src) if src else paths.WEATHER_HISTORY
    out = {"files": [], "copied": 0, "already_in_lake": 0}
    if not root.exists():
        out["error"] = f"{root} not found"
        return out
    for f in sorted(root.glob("USW*.csv")):
        name = f"{f.stem}.asof_{asof_tag(_mtime_utc(f))}{f.suffix}"
        existed = lake.has("ncei", "GHCND", name)
        lake.put("ncei", "GHCND", _mtime_utc(f), name, f, _origin(f))
        out["already_in_lake" if existed else "copied"] += 1
        out["files"].append(name)
    log(f"  adopt weather: {out['copied']} copied, {out['already_in_lake']} already there")
    return out




STAGING_60DAY = {"sced": "60D-SCED", "dam": "60D-DAM"}


def adopt_staging_60day(src: Optional[Path] = None, log: Callable[[str], None] = lambda s: None, workers: int = 3) -> dict:
    """D1b's staged 60-day disclosure zips (DISPATCH_HOME/staging/ercot-60day/{sced,dam}/*.zip, ~2.7 GB) -> the lake, copied.
    Reports 60D-SCED (13052) and 60D-DAM (13051). The lake stamps each zip with the posting time in its file name (the manifest's
    `posted_utc` is the listing's value to the second and is 2 to 27 s earlier), 60 days after the operating day, never the operating
    day. `parse` turns two members into datasets (esr_sced, dam_spp_60d). The staging copy stays."""
    root = Path(src) if src else paths.STAGING / "ercot-60day"
    out = {"root": str(root), "per_report": {}, "copied": 0, "already_in_lake": 0, "bytes": 0}
    if not root.exists():
        out["error"] = "staging folder not found"
        return out
    work = []
    for sub, rid in STAGING_60DAY.items():
        rep = reports.get(rid)
        for f in sorted((root / sub).glob("*.zip")):
            try:
                work.append((rep, f, parse_mis_publish(f.name)))
            except ValueError:
                continue

    def one(item):
        rep, f, pub = item
        existed = lake.has(rep.source, rep.report_id, f.name)
        lake.put(rep.source, rep.report_id, pub, f.name, f, _origin(f))
        return rep.report_id, existed, f.stat().st_size

    with ThreadPoolExecutor(max_workers=workers) as ex:
        for rid, existed, size in ex.map(one, work):
            per = out["per_report"].setdefault(rid, {"copied": 0, "already_in_lake": 0})
            per["already_in_lake" if existed else "copied"] += 1
            out["already_in_lake" if existed else "copied"] += 1
            out["bytes"] += 0 if existed else size
    log(f"  adopt 60-day disclosure: {out['copied']} copied ({out['bytes'] / 1e9:.2f} GB), {out['already_in_lake']} already there")
    return out


def adopt_all(log: Callable[[str], None] = lambda s: None, *, staging: bool = True) -> dict:
    """Everything in research-data (and D1b's staged 60-day disclosures) that belongs in the lake. Idempotent."""
    out = {"grid_raw": adopt_grid_raw(log=log), "ercot_history": adopt_ercot_history(log=log),
           "gas": adopt_gas(log=log), "weather": adopt_weather(log=log)}
    if staging:
        out["staging_60day"] = adopt_staging_60day(log=log)
    return out
