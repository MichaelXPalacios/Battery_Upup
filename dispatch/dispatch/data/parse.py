"""Raw lake -> parsed parquet (PRD FR2/FR3). A pure function of the raw files in the catalog.

    lake/parsed/<dataset>/date=YYYY-MM-DD/part.parquet

Every row has `published_utc` (when it became public) and `interval_start_utc` (the start of the interval it describes),
both tz-aware UTC at microsecond resolution, plus `pub_basis` (REAL when the publish time comes from ERCOT's file name,
ASSUMPTION when a named lag from assumptions.py was applied) and `raw_file` (the lake file it came from).

Partitions (`date=`):
* MIS datasets (one raw file = one report posting): the CPT date the file was posted. A partition is rebuilt from all
  the files posted that day, so it is incremental and cheap.
* bulk datasets (yearly archives, gas, weather): the first day of the CPT month of the interval (`date=2019-03-01`),
  to avoid thousands of one-day files. A year (or the whole snapshot set) is re-parsed when any of its files change.

Determinism (Gate 1): rows are sorted by a stable key, dtypes come from a fixed Arrow schema, the writer settings are fixed
(zstd level 3, row groups of 500k rows, parquet 2.6, no pandas metadata), and the only footer metadata is
(dataset, parser version, group, input sha256). No wall-clock value is ever written.
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import re
import zipfile
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable, Optional

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.dataset as pads
import pyarrow.parquet as pq

from .. import paths
from . import assumptions as A
from . import lake, reports, xlsx
from .adopt import asof_from_name
from .times import (CPT, UTC_US, cpt_date_str, flag_is_repeated, he_to_start, local_day_start_utc,  # noqa: F401
                    local_to_utc, rt_interval_start, sced_stamp, utc)

TS = pa.timestamp("us", tz="UTC")
STR, F8, BOOL, I32 = pa.string(), pa.float64(), pa.bool_(), pa.int32()
WRITER = dict(compression="zstd", compression_level=3, row_group_size=500_000, version="2.6", data_page_version="2.0",
              use_dictionary=True, write_statistics=True)

REAL, ASSUMPTION = "REAL", "ASSUMPTION"


class ParseError(RuntimeError):
    pass


def _schema(*cols) -> pa.Schema:
    return pa.schema([pa.field(n, t) for n, t in cols])


_COMMON = [("published_utc", TS), ("interval_start_utc", TS)]
_TAIL = [("pub_basis", STR), ("raw_file", STR)]


@dataclass(frozen=True)
class Dataset:
    name: str
    schema: pa.Schema
    sort_keys: tuple
    mode: str                                  # "file" (MIS posting) or "bulk" (archives and snapshots)
    report_ids: tuple
    parse: Callable                            # (list[RawFile]) -> DataFrame: one partition's files (file) or one group (bulk)
    version: int = 1                           # bump when the parsing logic changes: forces a rebuild
    group: Optional[Callable] = None           # bulk mode: raw files -> {group key: [RawFile]}
    description: str = ""
    units: str = ""


# ------------------------------------------------------------------------------------------------ raw readers
def _zip_members(rf: lake.RawFile, suffixes: tuple) -> list[tuple[str, bytes]]:
    out = []
    try:
        with zipfile.ZipFile(rf.path) as zf:
            for n in sorted(zf.namelist()):
                if n.lower().endswith(suffixes):
                    out.append((n, zf.read(n)))
    except zipfile.BadZipFile as e:
        raise ParseError(f"{rf.filename}: not a zip ({e})") from e
    return out


def _csv(b: bytes, **kw) -> pd.DataFrame:
    return pd.read_csv(io.BytesIO(b), dtype=str, keep_default_na=False, skipinitialspace=True, encoding="utf-8-sig", **kw)


def _read_many(files: list, need: Iterable[str], workers: int = 8) -> pd.DataFrame:
    """Read the csv inside every MIS zip of a batch into ONE string frame, plus `__pub` (the file's publish time) and `__file`
    (its name) per row. Parsing a day of files as one frame keeps the per-file pandas overhead off the hot path; the zips
    are opened on a few threads because each open is slow on this machine."""
    need = list(need)

    def one(rf: lake.RawFile) -> pd.DataFrame:
        frames = [_csv(b) for _, b in _zip_members(rf, (".csv",))]
        if not frames:
            raise ParseError(f"{rf.filename}: no csv inside")
        d = pd.concat(frames, ignore_index=True) if len(frames) > 1 else frames[0]
        d.columns = [c.strip() for c in d.columns]
        missing = [c for c in need if c not in d.columns]
        if missing:
            raise ParseError(f"{rf.filename}: columns {missing} not found; saw {list(d.columns)}")
        return d

    if len(files) > 1 and workers > 1:
        with ThreadPoolExecutor(max_workers=workers) as ex:
            dfs = list(ex.map(one, files))
    else:
        dfs = [one(f) for f in files]
    lens = np.array([len(x) for x in dfs])
    big = pd.concat(dfs, ignore_index=True)
    pubs = np.array([f.published_utc.value // 1000 for f in files], dtype="int64")          # ns -> us since epoch
    big["__pub"] = pd.Series(np.repeat(pubs, lens).astype("datetime64[us]")).dt.tz_localize("UTC")
    big["__file"] = np.repeat(np.array([f.filename for f in files], dtype=object), lens)
    return big


def _tail_from(out: pd.DataFrame, d: pd.DataFrame, basis: str) -> pd.DataFrame:
    out["pub_basis"] = basis
    out["raw_file"] = d["__file"].to_numpy()
    return out


def _num(s: pd.Series) -> pd.Series:
    return pd.to_numeric(s.astype(str).str.strip().replace({"": np.nan}), errors="coerce").astype("float64")


def _tail(df: pd.DataFrame, rf: lake.RawFile, basis: str) -> pd.DataFrame:
    df["pub_basis"] = basis
    df["raw_file"] = rf.filename
    return df


def _pub_col(ts: pd.Timestamp, n: int) -> pd.Series:
    return pd.Series(pd.DatetimeIndex([ts] * n)).astype(UTC_US)


# ------------------------------------------------------------------------------------------------ MIS file-mode parsers
# Each takes the batch of raw files that make up one partition (all files posted on one CPT day) and returns one frame.
def parse_rt_spp(files: list) -> pd.DataFrame:
    d = _read_many(files, ["DeliveryDate", "DeliveryHour", "DeliveryInterval", "SettlementPointName", "SettlementPointType",
                           "SettlementPointPrice", "DSTFlag"])
    out = pd.DataFrame({
        "published_utc": d["__pub"],
        "interval_start_utc": rt_interval_start(d["DeliveryDate"], d["DeliveryHour"], d["DeliveryInterval"], flag_is_repeated(d["DSTFlag"])),
        "settlement_point": d["SettlementPointName"].str.strip(), "sp_type": d["SettlementPointType"].str.strip(),
        "price_usd_per_mwh": _num(d["SettlementPointPrice"])})
    return _tail_from(out, d, REAL)


def parse_dam_spp(files: list) -> pd.DataFrame:
    d = _read_many(files, ["DeliveryDate", "HourEnding", "SettlementPoint", "SettlementPointPrice", "DSTFlag"])
    out = pd.DataFrame({
        "published_utc": d["__pub"],
        "interval_start_utc": he_to_start(d["DeliveryDate"], d["HourEnding"], flag_is_repeated(d["DSTFlag"])),
        "settlement_point": d["SettlementPoint"].str.strip(), "price_usd_per_mwh": _num(d["SettlementPointPrice"])})
    return _tail_from(out, d, REAL)


def parse_dam_as(files: list) -> pd.DataFrame:
    d = _read_many(files, ["DeliveryDate", "HourEnding", "AncillaryType", "MCPC", "DSTFlag"])
    out = pd.DataFrame({
        "published_utc": d["__pub"],
        "interval_start_utc": he_to_start(d["DeliveryDate"], d["HourEnding"], flag_is_repeated(d["DSTFlag"])),
        "product": d["AncillaryType"].str.strip().str.upper(), "mcpc_usd_per_mw_h": _num(d["MCPC"])})
    return _tail_from(out, d, REAL)


def parse_rt_as(files: list) -> pd.DataFrame:
    d = _read_many(files, ["DeliveryDate", "DeliveryHour", "DeliveryInterval", "RepeatedHourFlag", "ASType", "MCPC"])
    out = pd.DataFrame({
        "published_utc": d["__pub"],
        "interval_start_utc": rt_interval_start(d["DeliveryDate"], d["DeliveryHour"], d["DeliveryInterval"], flag_is_repeated(d["RepeatedHourFlag"])),
        "product": d["ASType"].str.strip().str.upper(), "mcpc_usd_per_mw_h": _num(d["MCPC"])})
    return _tail_from(out, d, REAL)


LOAD_ZONES = ("Coast", "East", "FarWest", "North", "NorthCentral", "SouthCentral", "Southern", "West", "SystemTotal")
LOAD_COLS = ("coast_mw", "east_mw", "far_west_mw", "north_mw", "north_central_mw", "south_central_mw", "southern_mw",
             "west_mw", "system_total_mw")


def parse_load_forecast(files: list) -> pd.DataFrame:
    d = _read_many(files, ["DeliveryDate", "HourEnding", *LOAD_ZONES, "Model", "InUseFlag", "DSTFlag"])
    out = pd.DataFrame({
        "published_utc": d["__pub"],                                     # the issue time of the forecast
        "interval_start_utc": he_to_start(d["DeliveryDate"], d["HourEnding"], flag_is_repeated(d["DSTFlag"])),   # the target hour
        "model": d["Model"].str.strip(), "in_use": d["InUseFlag"].str.strip().str.upper().eq("Y").to_numpy()})
    for src, dst in zip(LOAD_ZONES, LOAD_COLS):
        out[dst] = _num(d[src])
    return _tail_from(out, d, REAL)


def parse_sced_shadow(files: list) -> pd.DataFrame:
    d = _read_many(files, ["SCEDTimeStamp", "RepeatedHourFlag", "ConstraintID", "ConstraintName", "ContingencyName", "ShadowPrice",
                           "MaxShadowPrice", "Limit", "Value", "ViolatedMW", "FromStation", "ToStation", "FromStationkV",
                           "ToStationkV", "CCTStatus"])
    out = pd.DataFrame({
        "published_utc": d["__pub"],
        "interval_start_utc": sced_stamp(d["SCEDTimeStamp"], flag_is_repeated(d["RepeatedHourFlag"])),
        "constraint_id": pd.to_numeric(d["ConstraintID"], errors="coerce").astype("Int32"),
        "constraint_name": d["ConstraintName"].str.strip(), "contingency_name": d["ContingencyName"].str.strip(),
        "shadow_price_usd_per_mw": _num(d["ShadowPrice"]), "max_shadow_price_usd_per_mw": _num(d["MaxShadowPrice"]),
        "limit_mw": _num(d["Limit"]), "value_mw": _num(d["Value"]), "violated_mw": _num(d["ViolatedMW"]),
        "from_station": d["FromStation"].str.strip(), "to_station": d["ToStation"].str.strip(),
        "from_kv": _num(d["FromStationkV"]), "to_kv": _num(d["ToStationkV"]), "cct_status": d["CCTStatus"].str.strip()})
    return _tail_from(out, d, REAL)


def parse_sced_lambda(files: list) -> pd.DataFrame:
    d = _read_many(files, ["SCEDTimeStamp", "RepeatedHourFlag", "CappedSystemLambda", "UncappedSystemLambda"])
    out = pd.DataFrame({
        "published_utc": d["__pub"],
        "interval_start_utc": sced_stamp(d["SCEDTimeStamp"], flag_is_repeated(d["RepeatedHourFlag"])),
        "system_lambda_usd_per_mwh": _num(d["CappedSystemLambda"]),
        "system_lambda_uncapped_usd_per_mwh": _num(d["UncappedSystemLambda"])})
    return _tail_from(out, d, REAL)


def parse_sp_map(files: list) -> pd.DataFrame:
    """NP4-160-SG: one row per resource node (load zone and hub it settles in), plus the hubs and zones themselves."""
    parts = []
    for rf in files:
        members = [(n, b) for n, b in _zip_members(rf, (".csv",)) if "settlement_points" in n.lower().replace("-", "_")]
        if not members:
            raise ParseError(f"{rf.filename}: no Settlement_Points csv inside")
        d = _csv(members[0][1])
        d.columns = [c.strip() for c in d.columns]
        for c in ("ELECTRICAL_BUS", "SETTLEMENT_LOAD_ZONE", "RESOURCE_NODE", "HUB"):
            if c not in d.columns:
                raise ParseError(f"{rf.filename}: column {c} missing")
        rn = d[d["RESOURCE_NODE"].str.strip() != ""]
        # load zone of a resource node = the zone most of its buses settle in (ties: alphabetically first); hub = first non-empty
        zc = rn.groupby(["RESOURCE_NODE", "SETTLEMENT_LOAD_ZONE"], sort=True).size().reset_index(name="n")
        zc = zc.sort_values(["RESOURCE_NODE", "n", "SETTLEMENT_LOAD_ZONE"], ascending=[True, False, True], kind="stable")
        zone_of = zc.drop_duplicates("RESOURCE_NODE").set_index("RESOURCE_NODE")["SETTLEMENT_LOAD_ZONE"]
        hubs_rn = rn[rn["HUB"] != ""].groupby("RESOURCE_NODE")["HUB"].min()
        nb = rn.groupby("RESOURCE_NODE", sort=True).size()
        nodes = pd.DataFrame({
            "settlement_point": nb.index.to_numpy(), "kind": "RESOURCE_NODE",
            "load_zone": zone_of.reindex(nb.index).to_numpy(), "hub": hubs_rn.reindex(nb.index).fillna("").to_numpy(),
            "n_buses": nb.to_numpy().astype("int32")})
        zones = pd.DataFrame({"settlement_point": sorted(set(d["SETTLEMENT_LOAD_ZONE"]) - {""}), "kind": "LOAD_ZONE"})
        zones["load_zone"], zones["hub"], zones["n_buses"] = zones["settlement_point"], "", np.int32(0)
        hubs = pd.DataFrame({"settlement_point": sorted(set(d["HUB"]) - {""}), "kind": "HUB"})
        hubs["load_zone"], hubs["hub"], hubs["n_buses"] = "", hubs["settlement_point"], np.int32(0)
        out = pd.concat([nodes, zones, hubs], ignore_index=True)
        out.insert(0, "published_utc", _pub_col(rf.published_utc, len(out)))
        out.insert(1, "interval_start_utc", _pub_col(rf.published_utc, len(out)))        # valid from the posting
        parts.append(_tail(out, rf, REAL))
    return pd.concat(parts, ignore_index=True)


# ------------------------------------------------------------------------------------------------ 60-day disclosure parsers
# One daily zip of NP3-965-ER (60D-SCED) or NP3-966-ER (60D-DAM) holds ten or seventeen csv members, some of 300 MB. Only the
# member a dataset needs is opened. published_utc is the zip's POSTING time (REAL, from its file name), 60 days after the
# operating day: these rows were not public when the interval happened, and no feature may treat them as if they were.
ESR_SCED_MEMBER = "60d_ESR_Data_in_SCED"
ESR_SCED_COLUMNS = (          # (csv column, our column): numeric unless listed in ESR_SCED_TEXT
    ("Telemetered Resource Status", "status"), ("HSL", "hsl_mw"), ("HDL", "hdl_mw"), ("LSL", "lsl_mw"), ("LDL", "ldl_mw"),
    ("Base Point", "base_point_mw"), ("Telemetered Net Output", "net_output_mw"),
    ("State of Charge", "soc_mwh"), ("Minimum SOC", "min_soc_mwh"), ("Maximum SOC", "max_soc_mwh"),
    ("AS Capability REGUP", "as_cap_regup_mw"), ("AS Capability REGDN", "as_cap_regdn_mw"), ("AS Capability ECRS", "as_cap_ecrs_mw"),
    ("AS Capability NSPIN", "as_cap_nspin_mw"), ("AS Capability RRSPFR", "as_cap_rrspfr_mw"), ("AS Capability RRSFFR", "as_cap_rrsffr_mw"),
    ("AS Awards REGUP", "as_award_regup_mw"), ("AS Awards REGDN", "as_award_regdn_mw"), ("AS Awards ECRS", "as_award_ecrs_mw"),
    ("AS Awards NSPIN", "as_award_nspin_mw"), ("AS Awards RRSPFR", "as_award_rrspfr_mw"), ("AS Awards RRSFFR", "as_award_rrsffr_mw"),
    ("AS Awards RRSUFR", "as_award_rrsufr_mw"))
ESR_SCED_TEXT = ("QSE", "DME", "Resource Name", "Telemetered Resource Status", "SCED Time Stamp", "Repeated Hour Flag")
ESR_NUMERIC = tuple(out for src, out in ESR_SCED_COLUMNS if out != "status")
DAM_AWARD_MEMBERS = ("60d_DAM_EnergyBidAwards", "60d_DAM_EnergyOnlyOfferAwards")


def _zip_csv(rf: lake.RawFile, stem: str, usecols: list, dtype: dict) -> pd.DataFrame:
    """One csv member (name starting with `stem`) of a daily 60-day zip. Blank cells are NaN, text columns stay text, numeric
    columns must be numbers (a stray token raises instead of becoming NaN)."""
    try:
        with zipfile.ZipFile(rf.path) as zf:
            names = [n for n in zf.namelist() if n.startswith(stem) and n.lower().endswith(".csv")]
            if not names:
                raise ParseError(f"{rf.filename}: no member starting with {stem!r}; has {zf.namelist()[:6]}")
            with zf.open(sorted(names)[0]) as f:
                head = pd.read_csv(f, nrows=0, encoding="utf-8-sig").columns
            missing = [c for c in usecols if c not in set(head)]
            if missing:
                raise ParseError(f"{rf.filename}: {stem} lacks columns {missing}; has {list(head)[:8]}...")
            with zf.open(sorted(names)[0]) as f:
                return pd.read_csv(f, usecols=usecols, dtype=dtype, na_values=[""], keep_default_na=False, encoding="utf-8-sig")
    except zipfile.BadZipFile as e:
        raise ParseError(f"{rf.filename}: not a zip ({e})") from e


def parse_esr_sced(files: list) -> pd.DataFrame:
    """60d_ESR_Data_in_SCED: one row per energy storage resource and SCED run (about every 5 minutes): the telemetered state of
    charge with its minimum and maximum, the high and low limits, the base point, the telemetered output and the ancillary
    capabilities and awards. A blank cell stays NaN (a blank award means none)."""
    src_cols = ["SCED Time Stamp", "Repeated Hour Flag", "QSE", "DME", "Resource Name"] + [c for c, _ in ESR_SCED_COLUMNS]
    dtype = {c: (str if c in ESR_SCED_TEXT else "float64") for c in src_cols}
    parts = []
    for rf in files:
        d = _zip_csv(rf, ESR_SCED_MEMBER, src_cols, dtype)
        out = pd.DataFrame({
            "published_utc": _pub_col(rf.published_utc, len(d)),
            "interval_start_utc": sced_stamp(d["SCED Time Stamp"], flag_is_repeated(d["Repeated Hour Flag"])),
            "resource_name": d["Resource Name"].fillna("").str.strip().to_numpy(), "qse": d["QSE"].fillna("").str.strip().to_numpy(),
            "dme": d["DME"].fillna("").str.strip().to_numpy()})
        if out["interval_start_utc"].isna().any():
            raise ParseError(f"{rf.filename}: {int(out['interval_start_utc'].isna().sum())} rows without a SCED time stamp")
        for src, dst in ESR_SCED_COLUMNS:
            out[dst] = d[src].fillna("").str.strip().to_numpy() if dst == "status" else d[src].to_numpy(dtype="float64")
        parts.append(_tail(out, rf, REAL))
    return pd.concat(parts, ignore_index=True)


def parse_dam_spp_60d(files: list) -> pd.DataFrame:
    """The day-ahead settlement point price of every point that received an energy award, from the energy bid awards and the
    energy-only offer awards of the 60-day DAM disclosure (about 1,000 points x 24 hours a day: every hub and load zone and
    the resource and load nodes with awards). One row per (hour, point); two awards at one point and hour must agree on the
    price or the parse fails. The members have no repeated-hour flag, so an hour that appears twice at a point (a November
    fall-back day) is refused rather than guessed."""
    parts = []
    for rf in files:
        frames = []
        for stem in DAM_AWARD_MEMBERS:
            d = _zip_csv(rf, stem, ["Delivery Date", "Hour Ending", "Settlement Point", "Settlement Point Price"],
                         {"Delivery Date": str, "Hour Ending": str, "Settlement Point": str, "Settlement Point Price": "float64"})
            frames.append(d)
        d = pd.concat(frames, ignore_index=True)
        d["Settlement Point"] = d["Settlement Point"].fillna("").str.strip()
        d = d.drop_duplicates(["Delivery Date", "Hour Ending", "Settlement Point", "Settlement Point Price"])
        spread = d.groupby(["Delivery Date", "Hour Ending", "Settlement Point"], sort=False)["Settlement Point Price"].nunique()
        if (spread > 1).any():
            k = spread[spread > 1].index[0]
            raise ParseError(f"{rf.filename}: two different day-ahead prices at {k}")
        hour = pd.to_numeric(d["Hour Ending"].str.extract(r"^\s*(\d{1,2})")[0], errors="raise").astype("int64")
        if not hour.between(1, 24).all():
            raise ParseError(f"{rf.filename}: hour ending outside 1..24: {sorted(set(hour))}")
        out = pd.DataFrame({
            "published_utc": _pub_col(rf.published_utc, len(d)),
            "interval_start_utc": he_to_start(d["Delivery Date"], hour.astype(str), None),
            "settlement_point": d["Settlement Point"].to_numpy(), "price_usd_per_mwh": d["Settlement Point Price"].to_numpy(dtype="float64")})
        dup = out.duplicated(["interval_start_utc", "settlement_point"])
        if dup.any():
            raise ParseError(f"{rf.filename}: {int(dup.sum())} (hour, point) pairs appear twice; a DST day without a repeated-hour flag")
        parts.append(_tail(out, rf, REAL))
    return pd.concat(parts, ignore_index=True)


# ------------------------------------------------------------------------------------------------ bulk parsers (archives, snapshots)
def _year_of(name: str) -> Optional[str]:
    m = re.search(r"_(\d{4})\.(?:zip|xlsx|csv)$", name)
    return m.group(1) if m else None


def group_latest_per_year(files: list[lake.RawFile]) -> dict[str, list[lake.RawFile]]:
    """Yearly archives are re-published weekly under a new name: the newest posting of each year supersedes the older ones."""
    best: dict[str, lake.RawFile] = {}
    for f in sorted(files, key=lambda r: (r.published_utc, r.filename)):
        y = _year_of(f.filename)
        if y is not None:
            best[y] = f
    return {y: [f] for y, f in best.items()}


def group_all(files: list[lake.RawFile]) -> dict[str, list[lake.RawFile]]:
    return {"all": sorted(files, key=lambda r: (r.published_utc, r.filename))} if files else {}


def _hist_frames(rf: lake.RawFile, header_cells: tuple, need: Iterable[str]) -> Iterable[pd.DataFrame]:
    """Every data table inside a yearly archive zip (csv members, and each xlsx sheet that has a header row) that has the
    columns in `need`. Sheets that do not (e.g. 'Report Info') are skipped; if no table qualifies that is a ParseError."""
    need = list(need)
    found = False
    seen = []
    for n, b in _zip_members(rf, (".csv", ".xlsx")):
        if n.lower().endswith(".csv"):
            tables = [_csv(b)]
        else:
            tables = [df for _, df in xlsx.read_frames(b, header_cells)]
        for df in tables:
            df.columns = [str(c).strip() for c in df.columns]
            if all(c in df.columns for c in need):
                found = True
                yield df
            else:
                seen.append(list(df.columns))
    if not found:
        raise ParseError(f"{rf.filename}: no table with columns {need}; saw {seen[:3]}")


def _serial_to_naive_dates(s: pd.Series) -> pd.Series:
    """Midnight datetimes from Excel date serials (46023.0), 'MM/DD/YYYY' text, or a column that mixes both
    (some ERCOT workbooks switch representation between sheets and even within one)."""
    s = pd.Series(s).reset_index(drop=True)
    if s.dtype.kind in "fi":
        return (pd.Timestamp("1899-12-30") + pd.to_timedelta(s.astype("float64").round(0), unit="D")).astype("datetime64[us]")
    num = pd.to_numeric(s, errors="coerce")
    if num.notna().any():
        out = pd.Series(pd.NaT, index=s.index, dtype="datetime64[us]")
        m = num.notna()
        out[m] = (pd.Timestamp("1899-12-30") + pd.to_timedelta(num[m].round(0), unit="D")).astype("datetime64[us]")
        if (~m).any():
            out[~m] = pd.to_datetime(s[~m].astype(str).str.strip(), format="%m/%d/%Y").astype("datetime64[us]")
        return out
    return pd.to_datetime(s.astype(str).str.strip(), format="%m/%d/%Y").astype("datetime64[us]")


def _cap(published: pd.Series, cap: pd.Timestamp) -> pd.Series:
    """A row cannot have been public after the archive that contains it was published."""
    return published.where(published <= cap, other=pd.Timestamp(cap).as_unit("us")).astype(UTC_US)


def _assumed_rt_publish(start_utc: pd.Series, lag: pd.Timedelta) -> pd.Series:
    return (start_utc + pd.Timedelta(minutes=15) + lag).astype(UTC_US)


def _assumed_dam_publish(delivery_naive: pd.Series) -> pd.Series:
    """Delivery day D results are public at assumptions.DAM_PUBLISH_LOCAL_HOUR_MINUTE CPT on D - DAM_PUBLISH_DAYS_AHEAD."""
    h, m = A.DAM_PUBLISH_LOCAL_HOUR_MINUTE
    naive = delivery_naive.dt.normalize() - pd.Timedelta(days=A.DAM_PUBLISH_DAYS_AHEAD) + pd.Timedelta(hours=h, minutes=m)
    return naive.dt.tz_localize(CPT, ambiguous=False, nonexistent="shift_forward").dt.tz_convert("UTC").astype(UTC_US)


def parse_rt_spp_hist(files: list[lake.RawFile]) -> pd.DataFrame:
    """NP6-785-ER yearly workbooks: Delivery Date, Delivery Hour, Delivery Interval, Repeated Hour Flag, Settlement Point
    Name, Settlement Point Type, Settlement Point Price. No per-row publish time: ASSUMPTION `RT_PRICE_PUBLISH_LAG`."""
    parts = []
    for rf in files:
        need = ["Delivery Date", "Delivery Hour", "Delivery Interval", "Repeated Hour Flag", "Settlement Point Name",
                "Settlement Point Type", "Settlement Point Price"]
        for d in _hist_frames(rf, ("Delivery Date",), need):
            d = d.dropna(subset=["Delivery Hour", "Delivery Interval", "Settlement Point Name", "Settlement Point Price"]).reset_index(drop=True)
            date = d["Delivery Date"]
            start = local_to_utc(_serial_to_naive_dates(date) + pd.to_timedelta(
                (_num(d["Delivery Hour"]).astype("int64") - 1) * 60 + (_num(d["Delivery Interval"]).astype("int64") - 1) * 15, unit="m"),
                flag_is_repeated(d["Repeated Hour Flag"]))
            pub = _cap(_assumed_rt_publish(start, A.RT_PRICE_PUBLISH_LAG), rf.published_utc)
            parts.append(_tail(pd.DataFrame({
                "published_utc": pub, "interval_start_utc": start,
                "settlement_point": d["Settlement Point Name"].astype(str).str.strip(),
                "sp_type": d["Settlement Point Type"].astype(str).str.strip(),
                "price_usd_per_mwh": _num(d["Settlement Point Price"])}), rf, ASSUMPTION))
    return pd.concat(parts, ignore_index=True)


def parse_dam_spp_hist(files: list[lake.RawFile]) -> pd.DataFrame:
    """NP4-180-ER yearly workbooks: Delivery Date, Hour Ending, Repeated Hour Flag, Settlement Point, Settlement Point Price."""
    parts = []
    for rf in files:
        need = ["Delivery Date", "Hour Ending", "Repeated Hour Flag", "Settlement Point", "Settlement Point Price"]
        for d in _hist_frames(rf, ("Delivery Date",), need):
            d = d.dropna(subset=["Hour Ending", "Settlement Point", "Settlement Point Price"]).reset_index(drop=True)
            day = _serial_to_naive_dates(d["Delivery Date"])
            he = d["Hour Ending"].astype(str).str.strip().str.split(":").str[0].astype("int64")
            start = local_to_utc(day + pd.to_timedelta((he - 1) * 60, unit="m"), flag_is_repeated(d["Repeated Hour Flag"]))
            pub = _cap(_assumed_dam_publish(day), rf.published_utc)
            parts.append(_tail(pd.DataFrame({
                "published_utc": pub, "interval_start_utc": start,
                "settlement_point": d["Settlement Point"].astype(str).str.strip(),
                "price_usd_per_mwh": _num(d["Settlement Point Price"])}), rf, ASSUMPTION))
    return pd.concat(parts, ignore_index=True)


def parse_dam_as_hist(files: list[lake.RawFile]) -> pd.DataFrame:
    """HIST-DAM-MCPC (since 2010): wide table, one column per product. Older years are csv, 2026 is an xlsx with title rows."""
    parts = []
    for rf in files:
        for d in _hist_frames(rf, ("Delivery Date",), ["Delivery Date", "Hour Ending"]):
            d = d.rename(columns={c: c.strip().upper() for c in d.columns if c.strip().upper() in ("REGUP", "REGDN", "RRS", "NSPIN", "ECRS")})
            prods = [p for p in A_PRODUCTS if p in d.columns]
            if not prods:
                raise ParseError(f"{rf.filename}: no ancillary product columns, saw {list(d.columns)}")
            d = d.dropna(subset=["Delivery Date", "Hour Ending"]).reset_index(drop=True)
            day = _serial_to_naive_dates(d["Delivery Date"])
            he = d["Hour Ending"].astype(str).str.strip().str.split(":").str[0].astype("int64")
            rep = flag_is_repeated(d["Repeated Hour Flag"]) if "Repeated Hour Flag" in d.columns else None
            start = local_to_utc(day + pd.to_timedelta((he - 1) * 60, unit="m"), rep)
            pub = _cap(_assumed_dam_publish(day), rf.published_utc)
            for p in prods:
                price = _num(d[p])
                keep = price.notna().to_numpy()
                parts.append(_tail(pd.DataFrame({
                    "published_utc": pub[keep].reset_index(drop=True), "interval_start_utc": start[keep].reset_index(drop=True),
                    "product": p, "mcpc_usd_per_mw_h": price[keep].reset_index(drop=True)}), rf, ASSUMPTION))
    return pd.concat(parts, ignore_index=True)


A_PRODUCTS = ("REGUP", "REGDN", "RRS", "ECRS", "NSPIN")


def parse_rt_as_hist(files: list[lake.RawFile]) -> pd.DataFrame:
    """HIST-RTM-MCPC-15 (RTC+B, from 5 Dec 2025): Delivery Date (Excel serial), Delivery Hour, Delivery Interval, AS Type, MCPC."""
    parts = []
    for rf in files:
        need = ["Delivery Date", "Delivery Hour", "Delivery Interval", "AS Type", "MCPC", "Repeated Hour Flag"]
        for d in _hist_frames(rf, ("Delivery Date",), need):
            d = d.dropna(subset=["Delivery Hour", "Delivery Interval", "AS Type", "MCPC"]).reset_index(drop=True)
            day = _serial_to_naive_dates(d["Delivery Date"])
            start = local_to_utc(day + pd.to_timedelta(
                (_num(d["Delivery Hour"]).astype("int64") - 1) * 60 + (_num(d["Delivery Interval"]).astype("int64") - 1) * 15, unit="m"),
                flag_is_repeated(d["Repeated Hour Flag"]))
            pub = _cap(_assumed_rt_publish(start, A.RT_AS_PUBLISH_LAG), rf.published_utc)
            parts.append(_tail(pd.DataFrame({
                "published_utc": pub, "interval_start_utc": start, "product": d["AS Type"].astype(str).str.strip().str.upper(),
                "mcpc_usd_per_mw_h": _num(d["MCPC"])}), rf, ASSUMPTION))
    return pd.concat(parts, ignore_index=True)


def _local_noon_like(naive_days: pd.Series, days_after: int, hour: int) -> pd.Series:
    naive = naive_days.dt.normalize() + pd.Timedelta(days=days_after) + pd.Timedelta(hours=hour)
    return naive.dt.tz_localize(CPT, ambiguous=False, nonexistent="shift_forward").dt.tz_convert("UTC").astype(UTC_US)


def parse_gas_hh(files: list[lake.RawFile]) -> pd.DataFrame:
    """Henry Hub daily spot, FRED DHHNGSP: DATE,DHHNGSP. Public `GAS_PUBLISH_LAG_DAYS` after the trade date (ASSUMPTION),
    never later than the moment the snapshot holding it was fetched."""
    parts = []
    for rf in files:
        d = pd.read_csv(rf.path, dtype=str, keep_default_na=False, encoding="utf-8-sig")
        d.columns = [c.strip() for c in d.columns]
        if "DATE" not in d.columns or "DHHNGSP" not in d.columns:
            raise ParseError(f"{rf.filename}: expected DATE,DHHNGSP, saw {list(d.columns)}")
        price = _num(d["DHHNGSP"])
        keep = price.notna().to_numpy()
        day = pd.to_datetime(d["DATE"][keep].str.strip(), format="%Y-%m-%d").astype("datetime64[us]").reset_index(drop=True)
        fetched = asof_from_name(rf.filename) or rf.published_utc
        pub = _cap(_local_noon_like(day, A.GAS_PUBLISH_LAG_DAYS, A.GAS_PUBLISH_LOCAL_HOUR), fetched)
        parts.append(_tail(pd.DataFrame({
            "published_utc": pub, "interval_start_utc": day.dt.tz_localize(CPT, ambiguous=False, nonexistent="shift_forward").dt.tz_convert("UTC").astype(UTC_US),
            "obs_date": day.dt.strftime("%Y-%m-%d"), "price_usd_per_mmbtu": price[keep].reset_index(drop=True)}), rf, ASSUMPTION))
    return pd.concat(parts, ignore_index=True)


def parse_weather_obs(files: list[lake.RawFile]) -> pd.DataFrame:
    """NOAA GHCN-Daily station files: STATION,NAME,DATE,AWND,PRCP,TMAX,TMIN,WSF2 (degF, inches, mph). Day D is public at
    WEATHER_OBS_PUBLISH_LOCAL_HOUR CPT on D + WEATHER_OBS_PUBLISH_DAYS_AFTER (ASSUMPTION), never after the fetch of the file."""
    parts = []
    for rf in files:
        d = pd.read_csv(rf.path, dtype=str, keep_default_na=False, encoding="utf-8-sig")
        d.columns = [c.strip() for c in d.columns]
        for c in ("STATION", "DATE"):
            if c not in d.columns:
                raise ParseError(f"{rf.filename}: column {c} missing, saw {list(d.columns)}")
        day = pd.to_datetime(d["DATE"].str.strip(), format="%Y-%m-%d").astype("datetime64[us]")
        fetched = asof_from_name(rf.filename) or rf.published_utc
        pub = _cap(_local_noon_like(day, A.WEATHER_OBS_PUBLISH_DAYS_AFTER, A.WEATHER_OBS_PUBLISH_LOCAL_HOUR), fetched)

        def col(name):
            return _num(d[name]) if name in d.columns else pd.Series(np.nan, index=d.index, dtype="float64")
        parts.append(_tail(pd.DataFrame({
            "published_utc": pub, "interval_start_utc": day.dt.tz_localize(CPT, ambiguous=False, nonexistent="shift_forward").dt.tz_convert("UTC").astype(UTC_US),
            "station": d["STATION"].str.strip(), "obs_date": day.dt.strftime("%Y-%m-%d"),
            "tmax_f": col("TMAX"), "tmin_f": col("TMIN"), "prcp_in": col("PRCP"), "wsf2_mph": col("WSF2"), "awnd_mph": col("AWND")}),
            rf, ASSUMPTION))
    return pd.concat(parts, ignore_index=True)


def parse_weather_fcst(files: list) -> pd.DataFrame:
    """NWS hourly-forecast snapshots (see weather.snapshot_forecasts): published_utc = the NWS issue time,
    interval_start_utc = the start of each forecast hour."""
    return pd.concat([_parse_weather_fcst_one(rf) for rf in files], ignore_index=True)


def _parse_weather_fcst_one(rf: lake.RawFile) -> pd.DataFrame:
    from .weather import wind_mph
    js = json.loads(rf.path.read_bytes().decode("utf-8"))
    props = js["forecast"]["properties"]
    issued = utc(js["issued_utc"])
    rows = []
    for per in props.get("periods", []):
        t = per.get("temperature")
        if t is not None and str(per.get("temperatureUnit", "F")).upper() == "C":
            t = t * 9.0 / 5.0 + 32.0
        pop = (per.get("probabilityOfPrecipitation") or {}).get("value")
        dew = (per.get("dewpoint") or {}).get("value")
        rh = (per.get("relativeHumidity") or {}).get("value")
        rows.append({"interval_start_utc": utc(per["startTime"]), "temperature_f": np.nan if t is None else float(t),
                     "wind_speed_mph": wind_mph(per.get("windSpeed")), "precip_probability_pct": np.nan if pop is None else float(pop),
                     "dewpoint_c": np.nan if dew is None else float(dew), "relative_humidity_pct": np.nan if rh is None else float(rh),
                     "short_forecast": str(per.get("shortForecast") or "")})
    d = pd.DataFrame(rows)
    d["interval_start_utc"] = d["interval_start_utc"].astype(UTC_US)
    d.insert(0, "published_utc", _pub_col(issued, len(d)))
    d.insert(2, "station", str(js["code"]))
    return _tail(d, rf, REAL)


# ------------------------------------------------------------------------------------------------ registry
_RT_SPP = _schema(*_COMMON, ("settlement_point", STR), ("sp_type", STR), ("price_usd_per_mwh", F8), *_TAIL)
_DAM_SPP = _schema(*_COMMON, ("settlement_point", STR), ("price_usd_per_mwh", F8), *_TAIL)
_AS = _schema(*_COMMON, ("product", STR), ("mcpc_usd_per_mw_h", F8), *_TAIL)
_LF = _schema(*_COMMON, ("model", STR), ("in_use", BOOL), *[(c, F8) for c in LOAD_COLS], *_TAIL)
_SHADOW = _schema(*_COMMON, ("constraint_id", I32), ("constraint_name", STR), ("contingency_name", STR),
                  ("shadow_price_usd_per_mw", F8), ("max_shadow_price_usd_per_mw", F8), ("limit_mw", F8), ("value_mw", F8),
                  ("violated_mw", F8), ("from_station", STR), ("to_station", STR), ("from_kv", F8), ("to_kv", F8),
                  ("cct_status", STR), *_TAIL)
_LAMBDA = _schema(*_COMMON, ("system_lambda_usd_per_mwh", F8), ("system_lambda_uncapped_usd_per_mwh", F8), *_TAIL)
_ESR = _schema(*_COMMON, ("resource_name", STR), ("qse", STR), ("dme", STR), ("status", STR),
               *[(c, F8) for c in ESR_NUMERIC], *_TAIL)
_SPMAP = _schema(*_COMMON, ("settlement_point", STR), ("kind", STR), ("load_zone", STR), ("hub", STR), ("n_buses", I32), *_TAIL)
_GAS = _schema(*_COMMON, ("obs_date", STR), ("price_usd_per_mmbtu", F8), *_TAIL)
_WX = _schema(*_COMMON, ("station", STR), ("obs_date", STR), ("tmax_f", F8), ("tmin_f", F8), ("prcp_in", F8), ("wsf2_mph", F8),
              ("awnd_mph", F8), *_TAIL)
_WXF = _schema(*_COMMON, ("station", STR), ("temperature_f", F8), ("wind_speed_mph", F8), ("precip_probability_pct", F8),
               ("dewpoint_c", F8), ("relative_humidity_pct", F8), ("short_forecast", STR), *_TAIL)

DATASETS: dict[str, Dataset] = {d.name: d for d in [
    Dataset("rt_spp", _RT_SPP, ("interval_start_utc", "settlement_point", "sp_type", "published_utc"), "file", ("NP6-905-CD",), parse_rt_spp,
            description="15-minute settlement point prices at every resource node, hub and load zone (7-day MIS window, grows with collect)",
            units="$/MWh"),
    Dataset("dam_spp", _DAM_SPP, ("interval_start_utc", "settlement_point", "published_utc"), "file", ("NP4-190-CD",), parse_dam_spp,
            description="Day-ahead hourly settlement point prices (about 31 days of MIS, grows with collect)", units="$/MWh"),
    Dataset("dam_as", _AS, ("interval_start_utc", "product", "published_utc"), "file", ("NP4-188-CD",), parse_dam_as,
            description="Day-ahead ancillary service clearing prices (MCPC), hourly", units="$/MW-h"),
    Dataset("rt_as", _AS, ("interval_start_utc", "product", "published_utc"), "file", ("NP6-331-CD",), parse_rt_as,
            description="Real-time ancillary service clearing prices (MCPC) per 15-minute interval, RTC+B era", units="$/MW-h"),
    Dataset("load_forecast", _LF, ("published_utc", "interval_start_utc", "model"), "file", ("NP3-565-CD",), parse_load_forecast,
            description="7-day load forecast by model and weather zone: published_utc = issue time, interval_start_utc = target hour", units="MW"),
    Dataset("sced_shadow", _SHADOW, ("interval_start_utc", "constraint_name", "contingency_name", "published_utc"), "file", ("NP6-86-CD",),
            parse_sced_shadow, description="SCED shadow prices and binding transmission constraints (per SCED run)", units="$/MW"),
    Dataset("sced_lambda", _LAMBDA, ("interval_start_utc", "published_utc"), "file", ("NP6-322-CD",), parse_sced_lambda,
            description="SCED system lambda per SCED run", units="$/MWh"),
    Dataset("sp_map", _SPMAP, ("published_utc", "kind", "settlement_point"), "file", ("NP4-160-SG",), parse_sp_map,
            description="Resource node -> load zone and hub (NP4-160-SG), one table per posted model version"),
    Dataset("rt_spp_hist", _RT_SPP, ("interval_start_utc", "settlement_point", "sp_type", "published_utc"), "bulk", ("NP6-785-ER",),
            parse_rt_spp_hist, group=group_latest_per_year, description="NP6-785-ER: 15-minute hub and load zone prices since 2011 "
            "(yearly workbooks), publish time assumed", units="$/MWh"),
    Dataset("dam_spp_hist", _DAM_SPP, ("interval_start_utc", "settlement_point", "published_utc"), "bulk", ("NP4-180-ER",),
            parse_dam_spp_hist, group=group_latest_per_year, description="NP4-180-ER: hourly DAM hub and load zone prices since 2010, "
            "publish time assumed", units="$/MWh"),
    Dataset("dam_as_hist", _AS, ("interval_start_utc", "product", "published_utc"), "bulk", ("HIST-DAM-MCPC",),
            parse_dam_as_hist, group=group_latest_per_year, description="Day-ahead ancillary service clearing prices since Dec 2010 "
            "(yearly archive), publish time assumed", units="$/MW-h"),
    Dataset("rt_as_hist", _AS, ("interval_start_utc", "product", "published_utc"), "bulk", ("HIST-RTM-MCPC-15",),
            parse_rt_as_hist, group=group_latest_per_year, description="Real-time AS clearing prices per 15 minutes since 5 Dec 2025 "
            "(yearly archive), publish time assumed", units="$/MW-h"),
    Dataset("gas_hh", _GAS, ("interval_start_utc", "published_utc"), "bulk", ("DHHNGSP",), parse_gas_hh, group=group_all,
            description="Henry Hub daily spot since 1997 (FRED/EIA), publish time assumed", units="$/MMBtu"),
    Dataset("weather_obs", _WX, ("interval_start_utc", "station", "published_utc"), "bulk", ("GHCND",), parse_weather_obs, group=group_all,
            description="NOAA GHCN-Daily observations at the arena's ERCOT stations since 2003, publish time assumed"),
    Dataset("weather_fcst", _WXF, ("published_utc", "station", "interval_start_utc"), "file", ("FORECAST-HOURLY",), parse_weather_fcst,
            description="NWS hourly forecast snapshots at the arena's ERCOT stations: published_utc = NWS issue time"),
    Dataset("esr_sced", _ESR, ("interval_start_utc", "resource_name", "published_utc"), "file", ("60D-SCED",), parse_esr_sced,
            description="60-day SCED disclosure, energy storage resources: telemetered state of charge with its minimum and maximum, limits, base "
            "point, output and ancillary capabilities and awards per SCED run. published_utc = the zip's posting, 60 days after the operating day",
            units="MW, MWh"),
    Dataset("dam_spp_60d", _DAM_SPP, ("interval_start_utc", "settlement_point", "published_utc"), "file", ("60D-DAM",), parse_dam_spp_60d,
            description="60-day DAM disclosure: day-ahead price of every point with an energy award (hubs, zones, nodes), hourly. "
            "published_utc = the zip's posting, 60 days after the operating day (REAL); not used by the rt_v1 features", units="$/MWh"),
]}


def register(ds: Dataset) -> None:
    DATASETS[ds.name] = ds


# ------------------------------------------------------------------------------------------------ partitions
def dataset_dir(name: str) -> Path:
    return paths.PARSED / name


def partition_path(name: str, key: str) -> Path:
    return dataset_dir(name) / f"date={key}" / "part.parquet"


def partition_files(name: str, date_from: Optional[str] = None, date_to: Optional[str] = None) -> list[Path]:
    """Existing partition files, sorted by date key; optionally limited to keys in [date_from, date_to] (YYYY-MM-DD, inclusive)."""
    root = dataset_dir(name)
    if not root.exists():
        return []
    out = []
    for d in sorted(root.glob("date=*")):
        key = d.name[5:]
        if (date_from is None or key >= date_from) and (date_to is None or key <= date_to):
            p = d / "part.parquet"
            if p.exists():
                out.append(p)
    return out


def _meta(path: Path) -> dict:
    try:
        md = pq.read_metadata(path).metadata or {}
    except Exception:
        return {}
    return {k.decode(): v.decode() for k, v in md.items() if k.startswith(b"dispatch.")}


def _inputs_hash(ds: Dataset, files: list[lake.RawFile]) -> str:
    h = hashlib.sha256()
    h.update(f"{ds.name}|v{ds.version}|schema:{ds.schema.to_string(show_schema_metadata=False)}".encode())
    for f in sorted(files, key=lambda r: (r.filename, r.sha256)):
        h.update(f"|{f.filename}:{f.sha256}".encode())
    return h.hexdigest()


def _finish(df: pd.DataFrame, ds: Dataset) -> pd.DataFrame:
    cols = [f.name for f in ds.schema]
    missing = [c for c in cols if c not in df.columns]
    if missing:
        raise ParseError(f"{ds.name}: parser did not produce {missing}")
    return df[cols].sort_values(list(ds.sort_keys), kind="stable", ignore_index=True)


def _to_table(df: pd.DataFrame, ds: Dataset, meta: dict) -> pa.Table:
    t = pa.Table.from_pandas(df, schema=ds.schema, preserve_index=False, safe=True)
    return t.replace_schema_metadata({k: str(v) for k, v in meta.items()})


def write_table(path: Path, table: pa.Table) -> str:
    """Write one parquet file atomically with the fixed writer settings. Returns the file's sha256."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    pq.write_table(table, tmp, **WRITER)
    os.replace(tmp, path)
    return file_sha256(path)


def file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for c in iter(lambda: f.read(1 << 20), b""):
            h.update(c)
    return h.hexdigest()


def _month_keys(interval_utc: pd.Series) -> pd.Series:
    return pd.Series(pd.DatetimeIndex(interval_utc).tz_convert(CPT).strftime("%Y-%m-01"), index=interval_utc.index)


def _file_keys(files: list[lake.RawFile]) -> dict[str, list[lake.RawFile]]:
    groups: dict[str, list[lake.RawFile]] = {}
    for f in files:
        key = f.published_utc.tz_convert(CPT).strftime("%Y-%m-%d")
        groups.setdefault(key, []).append(f)
    return groups


def raw_files_for(ds: Dataset) -> list[lake.RawFile]:
    out = []
    for rid in ds.report_ids:
        out.extend(lake.iter_raw(rid))
    return out


def parse_dataset(name: str, *, force: bool = False, log: Callable[[str], None] = lambda s: None, limit_groups: Optional[int] = None,
                  workers: int = 2) -> dict:
    """Bring one dataset up to date with the lake. Idempotent: partitions whose inputs have not changed are left alone.

    Returns {'dataset', 'written', 'skipped', 'rows', 'errors'}; errors are collected per group and do not stop the others
    (the caller decides whether that is fatal; `parse_all(strict=True)` raises)."""
    ds = DATASETS[name]
    files = raw_files_for(ds)
    out = {"dataset": name, "written": 0, "skipped": 0, "rows": 0, "errors": [], "raw_files": len(files)}
    if not files:
        return out
    if ds.mode == "file":
        groups = _file_keys(files)
        for i, (key, fs) in enumerate(sorted(groups.items())):
            if limit_groups is not None and i >= limit_groups:
                break
            want = _inputs_hash(ds, fs)
            p = partition_path(name, key)
            if not force and p.exists() and _meta(p).get("dispatch.inputs_sha256") == want:
                out["skipped"] += 1
                continue
            try:
                df = _finish(ds.parse(sorted(fs, key=lambda r: (r.published_utc, r.filename))), ds)
            except Exception as e:
                out["errors"].append(f"{name} date={key}: {type(e).__name__}: {e}")
                continue
            write_table(p, _to_table(df, ds, {"dispatch.dataset": name, "dispatch.parser_version": ds.version,
                                              "dispatch.group": key, "dispatch.inputs_sha256": want, "dispatch.raw_files": len(fs)}))
            out["written"] += 1
            out["rows"] += len(df)
        log(f"  parse {name}: {out['written']} written, {out['skipped']} up to date, {len(out['errors'])} errors")
        return out
    # bulk: groups (a year, or everything) -> monthly partitions. Stale groups are parsed in worker processes when there are
    # several (each year of the NP6-785-ER workbooks takes ~30 s of CPU), one group per worker, writing disjoint partitions.
    existing: dict[str, set] = {}
    for p in partition_files(name):
        m = _meta(p)
        existing.setdefault(m.get("dispatch.group", "?"), set()).add(m.get("dispatch.inputs_sha256"))
    todo = []
    for gkey, fs in sorted(ds.group(files).items()):
        if not force and existing.get(gkey) == {_inputs_hash(ds, fs)}:
            out["skipped"] += 1
        else:
            todo.append(gkey)
    if len(todo) >= 3 and workers > 1:
        with ThreadPoolExecutor(max_workers=min(workers, len(todo))) as ex:          # each thread runs one worker process
            results = list(ex.map(lambda g: _bulk_group_subprocess(name, g), todo))
    else:
        results = [_bulk_group(name, g) for g in todo]
    for r in results:
        out["written"] += r["written"]
        out["rows"] += r["rows"]
        if r["error"]:
            out["errors"].append(r["error"])
    log(f"  parse {name}: {out['written']} written, {out['skipped']} groups up to date, {len(out['errors'])} errors")
    return out


def _bulk_group_subprocess(name: str, gkey: str) -> dict:
    """Parse one bulk group in a fresh interpreter (`python -m dispatch.data.parse --group <dataset> <group>`).

    A subprocess instead of multiprocessing: on Windows multiprocessing re-imports the caller's __main__, which breaks any script
    that has no `if __name__ == "__main__"` guard. DISPATCH_HOME reaches the child through the environment (or the default)."""
    import subprocess
    import sys
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    try:
        p = subprocess.run([sys.executable, "-m", "dispatch.data.parse", "--group", name, gkey], cwd=str(paths.PROJECT_DIR), env=env,
                           capture_output=True, text=True, encoding="utf-8", timeout=3 * 3600)
    except Exception as e:
        return {"written": 0, "rows": 0, "error": f"{name} group={gkey}: worker could not run ({type(e).__name__}: {e})"}
    for line in p.stdout.splitlines():
        if line.startswith("RESULT="):
            return json.loads(line[len("RESULT="):])
    return {"written": 0, "rows": 0, "error": f"{name} group={gkey}: worker exited {p.returncode}: {(p.stderr or p.stdout)[-400:]}"}


def _bulk_group(name: str, gkey: str) -> dict:
    """Parse one bulk group and write its monthly partitions (runs in a worker process or inline)."""
    ds = DATASETS[name]
    fs = ds.group(raw_files_for(ds))[gkey]
    want = _inputs_hash(ds, fs)
    try:
        df = _finish(ds.parse(fs), ds)
    except Exception as e:
        return {"written": 0, "rows": 0, "error": f"{name} group={gkey}: {type(e).__name__}: {e}"}
    keys = _month_keys(df["interval_start_utc"])
    produced, rows = set(), 0
    for mkey, part in df.groupby(keys, sort=True):
        write_table(partition_path(name, mkey), _to_table(part.reset_index(drop=True), ds, {
            "dispatch.dataset": name, "dispatch.parser_version": ds.version, "dispatch.group": gkey,
            "dispatch.inputs_sha256": want, "dispatch.raw_files": len(fs)}))
        produced.add(mkey)
        rows += len(part)
    for p in partition_files(name):                      # drop partitions of this group that the new parse no longer produces
        if _meta(p).get("dispatch.group") == gkey and p.parent.name[5:] not in produced:
            p.unlink()
    return {"written": len(produced), "rows": rows, "error": None}


def parse_all(datasets: Optional[Iterable[str]] = None, *, force: bool = False, strict: bool = True,
              log: Callable[[str], None] = lambda s: None, workers: int = 2) -> dict:
    """Parse every dataset (or the named ones). With strict=True a parse error raises ParseError after all datasets ran."""
    names = list(datasets) if datasets else list(DATASETS)
    results = {n: parse_dataset(n, force=force, log=log, workers=workers) for n in names}
    errors = [e for r in results.values() for e in r["errors"]]
    if errors and strict:
        raise ParseError(f"{len(errors)} parse error(s); first: {errors[0]}")
    return results


def clear_parsed() -> None:
    """Delete lake/parsed/ (the rebuild step of Gate 1). Raw files and the catalog are never touched."""
    import shutil
    if paths.PARSED.exists():
        shutil.rmtree(paths.PARSED)


# ------------------------------------------------------------------------------------------------ reading
def read(name: str, *, date_from: Optional[str] = None, date_to: Optional[str] = None, columns: Optional[list] = None,
         filter: Optional[pads.Expression] = None) -> pd.DataFrame:
    """Read a parsed dataset (optionally limited to partition keys in [date_from, date_to] and a pyarrow filter) as a DataFrame."""
    files = partition_files(name, date_from, date_to)
    ds = DATASETS[name]
    if not files:
        return pd.DataFrame({f.name: pd.Series(dtype="object") for f in ds.schema}) if columns is None else \
            pd.DataFrame({c: pd.Series(dtype="object") for c in columns})
    dset = pads.dataset([str(p) for p in files], schema=ds.schema, format="parquet")
    t = dset.to_table(columns=columns, filter=filter)
    return t.to_pandas()


if __name__ == "__main__":                 # worker entry: python -m dispatch.data.parse --group <dataset> <group>
    import sys
    if len(sys.argv) == 4 and sys.argv[1] == "--group":
        print("RESULT=" + json.dumps(_bulk_group(sys.argv[2], sys.argv[3])))
    else:
        raise SystemExit("usage: python -m dispatch.data.parse --group <dataset> <group>  (use `python -m dispatch.data.flows parse` to parse)")
