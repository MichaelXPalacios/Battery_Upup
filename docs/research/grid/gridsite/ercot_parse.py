"""Parse the raw ERCOT zips (raw/ercot/<REPORT_ID>/) into parquet tables in research-data/grid/derived/.

Conventions (PLAN.md)
  * Prices are $/MWh, ERCOT clock is Central Prevailing Time (CPT, America/Chicago).  Every timestamp column named
    `*_utc` or `sced_ts` is a UTC instant (datetime64[us, UTC]); `date_cpt` is the CPT calendar date as 'YYYY-MM-DD';
    `i15` is the 15-minute wall-clock index of the CPT day (0..95).
  * DST: rows keep their true UTC instant.  A repeated (fall-back) hour is flagged `dst_rep` = True and its i15 equals
    the first occurrence's.  `to96()` applies the arena's rule (drop the repeat; a skipped spring-forward hour repeats
    the hour before) for anyone who needs a 96-interval day.  The 3 to 10 Oct 2026 window has no DST change, so this
    path has only been tested on synthetic data (see `selftest_dst`).
  * SCED-run data (lmp_bus, lambda, shadow_sced) joins on `sced_ts`, the UTC instant of the SCED run timestamp.
  * `cid` = CONSTRAINT_NAME|CONTINGENCY_NAME (whitespace stripped).

Incremental: each report is parsed into per-posting-day parts (derived/_parts/<table>/<YYYYMMDD>.<nfiles>.parquet); only
days whose file count changed are re-parsed, then the final parquet is rebuilt from the parts.  Re-running is cheap.
"""
import io
import re
import shutil
import zipfile
from collections import defaultdict
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq

from .paths import ARCHIVE_ERCOT, DERIVED, RAW, REPO_DATA, SP_MAPPING_SEP2026

CPT = "America/Chicago"               # REAL: ERCOT clock (Central Prevailing Time)
SCED_INTERVAL_H = 5 / 60              # ASSUMPTION: a SCED run covers 5 minutes (used to turn $/MWh-per-run into $/MW-h)
PARTS = DERIVED / "_parts"
ARCHIVE_NP686 = ARCHIVE_ERCOT / "n1-np686-raw"   # REAL: one day (25 Sep 2026) of NP6-86-CD saved by the earlier sprint
UTC_US = "datetime64[us, UTC]"
AS_PRODUCTS = ("REGUP", "REGDN", "RRS", "NSPIN", "ECRS")   # REAL: ERCOT ancillary-service products (ECRS from 2023)


# ---- time helpers -------------------------------------------------------------------------------------------------
def local_to_utc(naive, rep):
    """Wall-clock CPT datetimes (naive) + bool 'second occurrence of a repeated hour' -> UTC instants."""
    s = pd.Series(naive).reset_index(drop=True)
    amb = (~pd.Series(rep).astype(bool)).reset_index(drop=True).to_numpy()
    out = s.dt.tz_localize(CPT, ambiguous=amb, nonexistent="shift_forward").dt.tz_convert("UTC")
    return out.astype(UTC_US)


def parse_stamp(strs, rep, fmt="%m/%d/%Y %H:%M:%S"):
    """'10/03/2026 00:55:17' + 'N'/'Y' -> UTC instant, computed once per unique (string, flag) pair."""
    strs = pd.Series(strs).reset_index(drop=True)
    rep = pd.Series(rep).reset_index(drop=True).eq("Y")
    key = strs + "|" + rep.astype(str)
    uniq = pd.DataFrame({"k": key, "s": strs, "r": rep}).drop_duplicates("k")
    naive = pd.to_datetime(uniq["s"], format=fmt)
    utc = local_to_utc(naive, uniq["r"])
    return key.map(dict(zip(uniq["k"], utc)))


def cpt_parts(ts_utc):
    """UTC instants -> (date_cpt 'YYYY-MM-DD', i15 0..95 wall-clock index, dst_rep bool)."""
    ts = pd.Series(ts_utc).reset_index(drop=True)
    loc = ts.dt.tz_convert(CPT)
    naive = loc.dt.tz_localize(None)
    amb = naive.dt.tz_localize(CPT, ambiguous="NaT", nonexistent="NaT").isna()
    offset = naive - ts.dt.tz_convert("UTC").dt.tz_localize(None)          # -6 h in CST, -5 h in CDT
    rep = amb & (offset == pd.Timedelta(hours=-6))
    return loc.dt.strftime("%Y-%m-%d"), (loc.dt.hour * 4 + loc.dt.minute // 15).astype("int16"), rep


def hour_ending_to_start(date_str, he_str, rep_flag):
    """DAM rows: DeliveryDate 'MM/DD/YYYY', HourEnding '01:00' / '1:00' / '24:00:00', DST flag -> (UTC start of the hour,
    0-based CPT hour).  Computed once per unique (date, hour, flag) triple, so a 400k-row file costs a few ms."""
    df = pd.DataFrame({"d": pd.Series(date_str).reset_index(drop=True), "h": pd.Series(he_str).reset_index(drop=True),
                       "r": pd.Series(rep_flag).reset_index(drop=True)})
    gid = df.groupby(["d", "h", "r"], sort=False).ngroup().to_numpy()
    first = df.loc[~pd.Series(gid).duplicated().to_numpy()].copy()
    first["gid"] = gid[~pd.Series(gid).duplicated().to_numpy()]
    first = first.sort_values("gid")
    he = first["h"].str.split(":").str[0].astype(int)
    naive = pd.to_datetime(first["d"], format="%m/%d/%Y") + pd.to_timedelta(he - 1, unit="h")
    utc = local_to_utc(naive, first["r"].eq("Y")).reset_index(drop=True)
    hour0 = (he - 1).reset_index(drop=True)
    return utc.iloc[gid].reset_index(drop=True), hour0.iloc[gid].reset_index(drop=True)


def interval_start(date_str, hour, interval, rep_flag):
    """RT 15-minute rows: DeliveryHour 1..24, DeliveryInterval 1..4 -> UTC interval start."""
    h = pd.Series(hour).astype(int)
    i = pd.Series(interval).astype(int)
    naive = pd.to_datetime(pd.Series(date_str), format="%m/%d/%Y") + pd.to_timedelta((h - 1) * 60 + (i - 1) * 15, unit="m")
    return local_to_utc(naive, pd.Series(rep_flag).eq("Y")), ((h - 1) * 4 + (i - 1)).astype("int16")


def to96(df, day_col="date_cpt", i_col="i15", val_cols=("price",), rep_col="dst_rep"):
    """Arena rule for a DST day: drop the repeated hour, and a skipped hour repeats the hour before.  Returns a frame
    with exactly 96 rows per (day, key) for the given val_cols; keys are all other columns except time/value ones."""
    d = df[~df[rep_col]] if rep_col in df else df
    keys = [c for c in d.columns if c not in (day_col, i_col, rep_col, *val_cols) and not c.endswith("_utc")]
    out = []
    for k, g in d.groupby(keys + [day_col], sort=False, dropna=False):
        v = g.set_index(i_col)[list(val_cols)].reindex(range(96))
        for h in range(24):                                   # arena rule: a skipped hour repeats the hour before
            if v.iloc[4 * h:4 * h + 4].isna().all().all():
                src = v.iloc[4 * (h - 1):4 * h] if h > 0 else v.iloc[4 * (h + 1):4 * (h + 2)]
                v.iloc[4 * h:4 * h + 4] = src.to_numpy()
        v = v.ffill().bfill()                                 # isolated gaps carry the previous value
        v.insert(0, day_col, k[-1])
        for name, val in zip(keys, k[:-1]):
            v.insert(0, name, val)
        out.append(v.reset_index().rename(columns={"index": i_col}))
    return pd.concat(out, ignore_index=True) if out else d


def selftest_dst():
    """Synthetic checks of the DST path (the real window has no DST change)."""
    day = pd.to_datetime(["11/01/2026 01:30:00", "11/01/2026 01:30:00", "11/01/2026 02:30:00"], format="%m/%d/%Y %H:%M:%S")
    u = local_to_utc(day, [False, True, False])
    assert (u.iloc[1] - u.iloc[0]) == pd.Timedelta(hours=1), "repeated hour must be one hour after the first"
    assert (u.iloc[2] - u.iloc[1]) == pd.Timedelta(hours=1)
    dc, i15, rep = cpt_parts(u)
    assert list(i15) == [6, 6, 10] and list(rep) == [False, True, False], (list(i15), list(rep))
    sp = pd.to_datetime(["03/14/2027 01:45:00", "03/14/2027 03:00:00"], format="%m/%d/%Y %H:%M:%S")
    u2 = local_to_utc(sp, [False, False])
    assert (u2.iloc[1] - u2.iloc[0]) == pd.Timedelta(minutes=15), "spring forward skips 02:00-03:00"
    return True


# ---- generic part / consolidate machinery -------------------------------------------------------------------------
def _read_zip_csvs(path):
    """Yield (member_name, bytes) of every csv in a zip."""
    with zipfile.ZipFile(path) as zf:
        for n in zf.namelist():
            if n.lower().endswith(".csv"):
                yield n, zf.read(n)


def _csv(b, **kw):
    return pd.read_csv(io.BytesIO(b), skipinitialspace=True, dtype=str, keep_default_na=False, **kw)


def _day_key(path):
    m = re.search(r"\.(20\d{6})\.\d{6,}\.", Path(path).name)       # posting date; the report id (0001xxxx) must not match
    return m.group(1) if m else "undated"


def _parse_group(files, parse_one):
    frames = []
    for f in files:
        for _, b in _read_zip_csvs(f):
            df = parse_one(b)
            if df is not None and len(df):
                frames.append(df)
    return pd.concat(frames, ignore_index=True) if frames else None


def build_parts(table, report_id, parse_one, log=print, files=None):
    """Parse raw zips into per-posting-day parts; returns the list of part paths (sorted)."""
    pdir = PARTS / table
    pdir.mkdir(parents=True, exist_ok=True)
    groups = defaultdict(list)
    for f in sorted(files if files is not None else (RAW / "ercot" / report_id).glob("*.zip")):
        groups[_day_key(f)].append(f)
    keep, built = [], 0
    for day, fs in sorted(groups.items()):
        part = pdir / f"{day}.{len(fs)}.parquet"
        keep.append(part)
        if part.exists():
            continue
        for old in pdir.glob(f"{day}.*.parquet"):          # file count changed (e.g. today's posting day grew)
            old.unlink()
        df = _parse_group(fs, parse_one)
        if df is not None:
            df.to_parquet(part, index=False)
            built += 1
        else:
            keep.pop()
    for p in pdir.glob("*.parquet"):                        # drop parts whose raw files vanished
        if p not in keep and p.name.split(".")[0] not in groups and not p.name.startswith("archive."):
            p.unlink()
    if built:
        log(f"  {table}: parsed {built} new/changed posting days ({len(groups)} total)")
    return sorted(pdir.glob("*.parquet"))


def consolidate(table, parts, key=None, sort=None, stream=False, log=print):
    """Parts -> derived/<table>.parquet.  Small tables are de-duplicated on `key` (last wins) and sorted; big ones
    (stream=True) are copied part by part, one row group each, in posting-day order."""
    out = DERIVED / f"{table}.parquet"
    tmp = out.with_suffix(".tmp")
    if not parts:
        log(f"  {table}: nothing to write")
        return None
    if stream:
        w = None
        n = 0
        for p in parts:
            t = pq.read_table(p)
            if w is None:
                schema = t.schema
                w = pq.ParquetWriter(tmp, schema, compression="zstd")
            w.write_table(t.cast(schema))
            n += t.num_rows
        w.close()
        shutil.move(tmp, out)
        log(f"  {table}: {n:,} rows -> {out.name}")
        return out
    df = pd.concat([pd.read_parquet(p) for p in parts], ignore_index=True)
    if key:
        df = df.drop_duplicates(key, keep="last")
    if sort:
        df = df.sort_values(sort, kind="stable")
    df = df.reset_index(drop=True)
    df.to_parquet(tmp, index=False, compression="zstd")
    shutil.move(tmp, out)
    log(f"  {table}: {len(df):,} rows -> {out.name}")
    return out


def _strip_cols(df, cols):
    for c in cols:
        df[c] = df[c].str.strip()
    return df


def _num(df, cols, dtype="float64"):
    for c in cols:
        df[c] = pd.to_numeric(df[c], errors="coerce").astype(dtype)
    return df


# ---- NP6-905-CD  spp_node -----------------------------------------------------------------------------------------
def _p_spp_node(b):
    d = _csv(b)
    ts, i15 = interval_start(d["DeliveryDate"], d["DeliveryHour"], d["DeliveryInterval"], d["DSTFlag"])
    out = pd.DataFrame({"settlement_point": d["SettlementPointName"], "sp_type": d["SettlementPointType"],
                        "interval_start_utc": ts, "date_cpt": pd.to_datetime(d["DeliveryDate"], format="%m/%d/%Y").dt.strftime("%Y-%m-%d"),
                        "i15": i15, "price": pd.to_numeric(d["SettlementPointPrice"]), "dst_rep": d["DSTFlag"].eq("Y")})
    return out


def parse_spp_node(log=print):
    parts = build_parts("spp_node", "NP6-905-CD", _p_spp_node, log)
    return consolidate("spp_node", parts, key=["settlement_point", "interval_start_utc", "dst_rep"],
                       sort=["interval_start_utc", "settlement_point"], log=log)


# ---- NP6-86-CD  shadow_sced  (MIS window + the archived 25 Sep 2026 day) ---------------------------------------------
SHADOW_SCED_RENAME = {
    "SCEDTimeStamp": "sced_ts_raw", "RepeatedHourFlag": "repeated_hour_flag", "ConstraintID": "constraint_id",
    "ConstraintName": "constraint_name", "ContingencyName": "contingency_name", "ShadowPrice": "shadow_price",
    "MaxShadowPrice": "max_shadow_price", "Limit": "limit", "Value": "value", "ViolatedMW": "violated_mw",
    "FromStation": "from_station", "ToStation": "to_station", "FromStationkV": "from_kv", "ToStationkV": "to_kv",
    "CCTStatus": "cct_status"}


def element_type(from_station, to_station, from_kv, to_kv, contingency):
    """DERIVED heuristic, not an ERCOT field: gtc = no stations named (interface / stability limit),
    xfmr = the two ends are at different voltages, line = same voltage, other = anything else."""
    if not from_station and not to_station:
        return "gtc"
    if from_kv > 0 and to_kv > 0:
        return "xfmr" if from_kv != to_kv else "line"
    return "other"


def _p_shadow_sced(b, source="mis"):
    d = _csv(b).rename(columns=SHADOW_SCED_RENAME)
    for c in ("constraint_name", "contingency_name", "from_station", "to_station"):
        d[c] = d[c].str.strip()
    _num(d, ["shadow_price", "max_shadow_price", "limit", "value", "violated_mw", "from_kv", "to_kv"])
    d["constraint_id"] = pd.to_numeric(d["constraint_id"], errors="coerce").astype("Int32")
    d["sced_ts"] = parse_stamp(d["sced_ts_raw"], d["repeated_hour_flag"])
    d["date_cpt"], d["i15"], d["dst_rep"] = cpt_parts(d["sced_ts"])
    d["cid"] = d["constraint_name"] + "|" + d["contingency_name"]
    d["flow"] = d["value"]                                  # alias: ERCOT's `Value` is the flow (MW) SCED saw on the element
    d["element_type"] = [element_type(a, b_, c, e, g) for a, b_, c, e, g in
                         zip(d["from_station"], d["to_station"], d["from_kv"], d["to_kv"], d["contingency_name"])]
    d["source"] = source
    cols = ["sced_ts", "date_cpt", "i15", "dst_rep", "sced_ts_raw", "repeated_hour_flag", "constraint_id", "constraint_name",
            "contingency_name", "cid", "from_station", "to_station", "from_kv", "to_kv", "limit", "value", "flow",
            "shadow_price", "max_shadow_price", "violated_mw", "cct_status", "element_type", "source"]
    return d[cols]


def _archive_part(log=print):
    """25 Sep 2026 NP6-86-CD files from the earlier sprint (plain csv, not zip) -> one part with source='archive'."""
    fs = sorted(ARCHIVE_NP686.glob("*.csv")) if ARCHIVE_NP686.exists() else []
    if not fs:
        log(f"  shadow_sced: archive folder not found ({ARCHIVE_NP686})")
        return None
    pdir = PARTS / "shadow_sced"
    pdir.mkdir(parents=True, exist_ok=True)
    part = pdir / f"archive.{len(fs)}.parquet"
    if not part.exists():
        for old in pdir.glob("archive.*.parquet"):
            old.unlink()
        pd.concat([_p_shadow_sced(f.read_bytes(), source="archive") for f in fs], ignore_index=True).to_parquet(part, index=False)
        log(f"  shadow_sced: parsed {len(fs)} archived files ({ARCHIVE_NP686.name})")
    return part


def parse_shadow_sced(log=print):
    parts = build_parts("shadow_sced", "NP6-86-CD", _p_shadow_sced, log)
    parts = [p for p in parts if not p.name.startswith("archive.")]
    arch = _archive_part(log)
    if arch is not None:
        parts = parts + [arch]
    return consolidate("shadow_sced", parts, key=["sced_ts", "cid", "dst_rep"], sort=["sced_ts", "cid"], log=log)


# ---- NP4-191-CD  shadow_dam -----------------------------------------------------------------------------------------
def _p_shadow_dam(b):
    d = _csv(b).rename(columns={
        "DeliveryDate": "delivery_date", "HourEnding": "hour_ending", "ConstraintID": "constraint_id",
        "ConstraintName": "constraint_name", "ContingencyName": "contingency_name", "ConstraintLimit": "limit",
        "ConstraintValue": "value", "ViolationAmount": "violation_amount", "ShadowPrice": "shadow_price",
        "FromStation": "from_station", "ToStation": "to_station", "FromStationkV": "from_kv", "ToStationkV": "to_kv",
        "DeliveryTime": "delivery_time", "DSTFlag": "dst_flag"})
    _num(d, ["limit", "value", "violation_amount", "shadow_price", "from_kv", "to_kv"])
    d["constraint_id"] = pd.to_numeric(d["constraint_id"], errors="coerce").astype("Int32")
    d["interval_start_utc"], h = hour_ending_to_start(d["delivery_date"], d["hour_ending"], d["dst_flag"])
    d["h"] = h.astype("int16")
    d["date_cpt"] = pd.to_datetime(d["delivery_date"], format="%m/%d/%Y").dt.strftime("%Y-%m-%d")
    d["dst_rep"] = d["dst_flag"].eq("Y")
    d["cid"] = d["constraint_name"] + "|" + d["contingency_name"]
    d["flow"] = d["value"]
    d["element_type"] = [element_type(a, b_, c, e, g) for a, b_, c, e, g in
                         zip(d["from_station"], d["to_station"], d["from_kv"], d["to_kv"], d["contingency_name"])]
    d["source"] = "mis"
    return d[["interval_start_utc", "date_cpt", "h", "dst_rep", "delivery_date", "hour_ending", "constraint_id",
              "constraint_name", "contingency_name", "cid", "from_station", "to_station", "from_kv", "to_kv", "limit",
              "value", "flow", "violation_amount", "shadow_price", "delivery_time", "dst_flag", "element_type", "source"]]


def parse_shadow_dam(log=print):
    parts = build_parts("shadow_dam", "NP4-191-CD", _p_shadow_dam, log)
    return consolidate("shadow_dam", parts, key=["interval_start_utc", "cid", "dst_rep"], sort=["interval_start_utc", "cid"], log=log)


# ---- NP4-190-CD  spp_dam ----------------------------------------------------------------------------------------------
def _p_spp_dam(b):
    d = _csv(b)
    ts, h = hour_ending_to_start(d["DeliveryDate"], d["HourEnding"], d["DSTFlag"])
    return pd.DataFrame({"settlement_point": d["SettlementPoint"], "interval_start_utc": ts,
                         "date_cpt": pd.to_datetime(d["DeliveryDate"], format="%m/%d/%Y").dt.strftime("%Y-%m-%d"),
                         "h": h.astype("int16"), "price": pd.to_numeric(d["SettlementPointPrice"]), "dst_rep": d["DSTFlag"].eq("Y")})


def parse_spp_dam(log=print):
    parts = build_parts("spp_dam", "NP4-190-CD", _p_spp_dam, log)
    return consolidate("spp_dam", parts, key=["settlement_point", "interval_start_utc", "dst_rep"],
                       sort=["interval_start_utc", "settlement_point"], log=log)


# ---- NP4-523-CD  lambda_dam (extra, tiny) -----------------------------------------------------------------------------
def _p_lambda_dam(b):
    d = _csv(b)
    ts, h = hour_ending_to_start(d["DeliveryDate"], d["HourEnding"], d["DSTFlag"])
    return pd.DataFrame({"interval_start_utc": ts, "date_cpt": pd.to_datetime(d["DeliveryDate"], format="%m/%d/%Y").dt.strftime("%Y-%m-%d"),
                         "h": h.astype("int16"), "lambda": pd.to_numeric(d["SystemLambda"]), "dst_rep": d["DSTFlag"].eq("Y")})


def parse_lambda_dam(log=print):
    parts = build_parts("lambda_dam", "NP4-523-CD", _p_lambda_dam, log)
    return consolidate("lambda_dam", parts, key=["interval_start_utc", "dst_rep"], sort=["interval_start_utc"], log=log)


# ---- NP6-322-CD  lambda -------------------------------------------------------------------------------------------------
def _p_lambda(b):
    d = _csv(b)
    d["sced_ts"] = parse_stamp(d["SCEDTimeStamp"], d["RepeatedHourFlag"])
    d["date_cpt"], d["i15"], d["dst_rep"] = cpt_parts(d["sced_ts"])
    # `lambda` is the CAPPED system lambda (the price-bearing one); the uncapped value differs only in scarcity.
    return pd.DataFrame({"sced_ts": d["sced_ts"], "lambda": pd.to_numeric(d["CappedSystemLambda"]),
                         "lambda_uncapped": pd.to_numeric(d["UncappedSystemLambda"]),
                         "date_cpt": d["date_cpt"], "i15": d["i15"], "dst_rep": d["dst_rep"]})


def parse_lambda(log=print):
    parts = build_parts("lambda", "NP6-322-CD", _p_lambda, log)
    return consolidate("lambda", parts, key=["sced_ts", "dst_rep"], sort=["sced_ts"], log=log)


# ---- ancillary-service prices  as_prices ------------------------------------------------------------------------------
def _us_date(s):
    """'01/01/2012' / '2012-01-01' / '1/1/2011' -> 'MM/DD/YYYY'."""
    s = str(s).strip()[:10]
    if "-" in s:
        y, m, d = s.split("-")[:3]
        return f"{int(m):02d}/{int(d):02d}/{int(y):04d}"
    m, d, y = s.split("/")[:3]
    return f"{int(m):02d}/{int(d):02d}/{int(y):04d}"


def _as_dam_long(d, source, date_col="Delivery Date", he_col="Hour Ending", rep_col="Repeated Hour Flag"):
    """Wide DAM table (one column per product) -> long rows."""
    d = d.copy()
    d.columns = [str(c).strip() for c in d.columns]
    prods = [c for c in d.columns if c in AS_PRODUCTS]
    rep = d[rep_col].astype(str).str.strip()
    ts, h = hour_ending_to_start(d[date_col].map(_us_date), d[he_col].astype(str), rep)
    base = pd.DataFrame({"interval_start_utc": ts, "i15": (h * 4).astype("int16"), "dst_rep": rep.eq("Y").to_numpy()})
    base["date_cpt"], _, _ = cpt_parts(base["interval_start_utc"])
    frames = []
    for p in prods:
        x = base.copy()
        x["product"] = p
        x["mcpc"] = pd.to_numeric(d[p].to_numpy(), errors="coerce")
        frames.append(x)
    out = pd.concat(frames, ignore_index=True).dropna(subset=["mcpc"])
    out["market"], out["dur_min"], out["mcpc_uncapped"], out["source"] = "DAM", 60, np.nan, source
    return out


def _as_dam_mis(b):
    d = _csv(b)
    ts, h = hour_ending_to_start(d["DeliveryDate"], d["HourEnding"], d["DSTFlag"])
    out = pd.DataFrame({"interval_start_utc": ts, "i15": (h * 4).astype("int16"), "dst_rep": d["DSTFlag"].eq("Y"),
                        "product": d["AncillaryType"], "mcpc": pd.to_numeric(d["MCPC"])})
    out["date_cpt"], _, _ = cpt_parts(out["interval_start_utc"])
    out["market"], out["dur_min"], out["mcpc_uncapped"], out["source"] = "DAM", 60, np.nan, "mis"
    return out


def _as_rt_sced(ts_strs, type_col, capped, uncapped, rep, source, fmt):
    rep = pd.Series(rep).astype(str).str.strip()
    ts_strs = pd.Series(ts_strs).astype(str)
    if fmt.startswith("%Y"):          # archive xlsx: '2026-10-01 00:00:28.999000' is the SCED second 29, so round (not cut)
        ts_strs = pd.to_datetime(ts_strs, format="ISO8601").dt.round("s").dt.strftime(fmt)
    ts = parse_stamp(ts_strs, rep, fmt=fmt)
    out = pd.DataFrame({"interval_start_utc": ts, "product": pd.Series(type_col).astype(str).str.strip().to_numpy(),
                        "mcpc": pd.to_numeric(pd.Series(capped), errors="coerce").to_numpy(),
                        "mcpc_uncapped": pd.to_numeric(pd.Series(uncapped), errors="coerce").to_numpy(),
                        "dst_rep": rep.eq("Y").to_numpy()})
    out["date_cpt"], out["i15"], _ = cpt_parts(out["interval_start_utc"])
    out["market"], out["dur_min"], out["source"] = "RT_SCED", 5, source
    return out


def _as_rt_sced_mis(b):
    d = _csv(b)
    return _as_rt_sced(d["SCEDTimestamp"], d["ASType"], d["CappedMCPC"], d["UncappedMCPC"], d["RepeatedHourFlag"], "mis",
                       "%m/%d/%Y %H:%M:%S")


def _as_rt15(date_col, hour, interval, as_type, mcpc, rep, source):
    ds = pd.Series(date_col).map(_us_date)
    rep = pd.Series(rep).astype(str).str.strip()
    ts, i15 = interval_start(ds, hour, interval, rep)
    out = pd.DataFrame({"interval_start_utc": ts, "i15": i15, "product": pd.Series(as_type).astype(str).str.strip().to_numpy(),
                        "mcpc": pd.to_numeric(pd.Series(mcpc), errors="coerce").to_numpy(), "mcpc_uncapped": np.nan,
                        "dst_rep": rep.eq("Y").to_numpy()})
    out["date_cpt"] = pd.to_datetime(ds, format="%m/%d/%Y").dt.strftime("%Y-%m-%d").to_numpy()
    out["market"], out["dur_min"], out["source"] = "RT_15", 15, source
    return out


def _as_rt15_mis(b):
    d = _csv(b)
    hdr = {c.lower(): c for c in d.columns}
    rep = d[hdr["dstflag"]] if "dstflag" in hdr else d[hdr["repeatedhourflag"]]
    return _as_rt15(d[hdr["deliverydate"]], d[hdr["deliveryhour"]], d[hdr["deliveryinterval"]], d[hdr["astype"]],
                    d[hdr["mcpc"]], rep, "mis")


def _read_zip_any(path, exts):
    with zipfile.ZipFile(path) as zf:
        for n in zf.namelist():
            if n.lower().endswith(exts):
                yield n, zf.read(n)


def _xlsx_tables(zip_path):
    """Every data sheet of a MIS 'Historical ...' xlsx as a frame; the header row is the one starting 'SCED Timestamp'
    or 'Delivery Date' (the sheets carry title rows above it)."""
    out = {}
    for _, b in _read_zip_any(zip_path, (".xlsx",)):
        sheets = pd.read_excel(io.BytesIO(b), sheet_name=None, header=None)
        for name, raw in sheets.items():
            if name.lower().startswith("report info"):
                continue
            raw = raw.dropna(how="all")
            first = raw.iloc[:, 0].astype(str)
            hit = first[first.isin(["SCED Timestamp", "Delivery Date"])]
            if hit.empty:
                continue
            h = hit.index[0]
            df = raw.loc[raw.index > h].copy()
            df.columns = [str(c).strip() for c in raw.loc[h]]
            out[name] = df.dropna(subset=[df.columns[0]]).reset_index(drop=True)
    return out


def _latest_per_year(report_id):
    """Historical archives are re-published (new name, same year); keep the newest file for each year."""
    best = {}
    for f in sorted((RAW / "ercot" / report_id).glob("*.zip")):
        yr = re.search(r"_(20\d\d)\.zip$", f.name)
        if yr:
            best[yr.group(1)] = f
    return [best[k] for k in sorted(best)]


def _as_hist_dam():
    frames = []
    for f in _latest_per_year("HIST-DAM-MCPC"):
        for n, b in _read_zip_any(f, (".csv",)):
            frames.append(_as_dam_long(pd.read_csv(io.BytesIO(b), dtype=str, keep_default_na=False), "hist"))
        for _, d in _xlsx_tables(f).items():
            frames.append(_as_dam_long(d, "hist"))
    return frames


def _as_hist_rt():
    frames = []
    for f in _latest_per_year("HIST-RTM-MCPC-SCED"):
        for _, d in _xlsx_tables(f).items():
            c = {k.upper(): k for k in d.columns}
            capped = d[c["CAPPED_MCPC"]] if "CAPPED_MCPC" in c else d[c["MCPC"]]
            unc = d[c["UNCAPPED_MCPC"]] if "UNCAPPED_MCPC" in c else capped
            frames.append(_as_rt_sced(d[c["SCED TIMESTAMP"]], d[c["AS TYPE"]], capped, unc, d[c["REPEATED HOUR FLAG"]],
                                      "hist", "%Y-%m-%d %H:%M:%S"))
    for f in _latest_per_year("HIST-RTM-MCPC-15"):
        for _, d in _xlsx_tables(f).items():
            c = {k.upper(): k for k in d.columns}
            frames.append(_as_rt15(d[c["DELIVERY DATE"]], d[c["DELIVERY HOUR"]], d[c["DELIVERY INTERVAL"]], d[c["AS TYPE"]],
                                   d[c["MCPC"]], d[c["REPEATED HOUR FLAG"]], "hist"))
    return frames


def parse_as_prices(log=print):
    """DAM (2010 to now) and real-time (RTC+B, 5 Dec 2025 to now) AS clearing prices, one long table.
    market: DAM (hourly), RT_SCED (per SCED run), RT_15 (15-minute settlement interval).  `mcpc` is the capped price."""
    frames = _as_hist_dam() + _as_hist_rt()
    for table, rid, fn in (("as_dam", "NP4-188-CD", _as_dam_mis), ("as_rt_sced", "NP6-332-CD", _as_rt_sced_mis),
                           ("as_rt15", "NP6-331-CD", _as_rt15_mis)):
        parts = build_parts(table, rid, fn, log)
        frames += [pd.read_parquet(p) for p in parts]
    df = pd.concat(frames, ignore_index=True)
    df["_src"] = df["source"].map({"hist": 0, "mis": 1})
    df = df.sort_values(["market", "product", "interval_start_utc", "dst_rep", "_src"], kind="stable")
    df = df.drop_duplicates(["market", "product", "interval_start_utc", "dst_rep"], keep="last").drop(columns="_src")
    cols = ["market", "product", "interval_start_utc", "date_cpt", "i15", "dur_min", "mcpc", "mcpc_uncapped", "dst_rep", "source"]
    df = df[cols].reset_index(drop=True)
    out = DERIVED / "as_prices.parquet"
    tmp = DERIVED / "as_prices.tmp"
    df.to_parquet(tmp, index=False, compression="zstd")
    shutil.move(tmp, out)
    log(f"  as_prices: {len(df):,} rows -> {out.name}")
    return out


# ---- NP4-160-SG  settlement-point / electrical-bus mapping ------------------------------------------------------------
MAP_DIR = RAW / "ercot" / "NP4-160-SG"
MAP_TABLE_FILES = ("Settlement_Points", "NOIE_Mapping", "Resource_Node_to_Unit", "Hub_Name_AND_DC_Ties", "CCP_Resource_Names")
MAP_LABELS = [  # column, label, source, note
    ("ELECTRICAL_BUS", "REAL", "ERCOT NP4-160-SG Settlement_Points_*.csv", "bus key used by LMPs by Electrical Bus (NP6-787) and DAM hourly LMPs (NP4-183)"),
    ("NODE_NAME", "REAL", "ERCOT NP4-160-SG", "node name in the network model"),
    ("PSSE_BUS_NAME", "REAL", "ERCOT NP4-160-SG", "PSS/E bus name"),
    ("VOLTAGE_LEVEL", "REAL", "ERCOT NP4-160-SG", "bus nominal kV"),
    ("SUBSTATION", "REAL", "ERCOT NP4-160-SG", "substation code; the `sub` join key of the whole project"),
    ("SETTLEMENT_LOAD_ZONE", "REAL", "ERCOT NP4-160-SG", "load zone the bus settles in (LZ_NORTH, LZ_SOUTH, LZ_WEST, LZ_HOUSTON); NOIE zones (AEN, CPS, LCRA, RAYBN) are in NOIE_ZONE"),
    ("RESOURCE_NODE", "REAL", "ERCOT NP4-160-SG", "resource node the bus belongs to, blank if none"),
    ("HUB_BUS_NAME", "REAL", "ERCOT NP4-160-SG", "hub bus code, blank if the bus is not in a hub"),
    ("HUB", "REAL", "ERCOT NP4-160-SG", "trading hub the bus averages into, blank if none"),
    ("PSSE_BUS_NUMBER", "REAL", "ERCOT NP4-160-SG", "PSS/E bus number"),
    ("NOIE_ZONE", "DERIVED", "ERCOT NP4-160-SG NOIE_Mapping_*.csv joined on ELECTRICAL_BUS", "NOIE load zone(s) served at this bus, ';'-joined; blank if none"),
    ("IN_SEP2026", "DERIVED", "this table vs the Sep 2026 copy (model ML2, 22 Sep 2026)", "True if the bus is also in the Sep 2026 copy the repo already held"),
    ("IN_PREV_MODEL", "DERIVED", "this table vs the previous published model (Sep ML3, file dated 30 Sep 2026)", "False means the bus is new in the current model"),
]


def _model_versions():
    """[(effective_date 'YYYY-MM-DD', folder)] for every unpacked NP4-160 version, oldest first."""
    out = []
    for d in sorted(MAP_DIR.iterdir()) if MAP_DIR.exists() else []:
        m = re.search(r"_(\d{2})(\d{2})(\d{4})_SP_List", d.name)
        if d.is_dir() and m and any(d.glob("Settlement_Points_*.csv")):
            out.append((f"{m.group(3)}-{m.group(1)}-{m.group(2)}", d))
    return sorted(out)


def _read_map(folder, stem):
    f = sorted(folder.glob(f"{stem}_*.csv"))[-1]
    return pd.read_csv(f, dtype=str, keep_default_na=False)


def current_model():
    """The newest published NP4-160 model.  (The folder name carries a date, e.g. 10132026 for Oct ML1, but ERCOT's real-time
    LMPs by electrical bus already used exactly that model's 19,493 buses on 5 Oct, eight days earlier, so the date is
    not a reliable 'in force from' date.  `check_model_vs_prices` verifies the choice against lmp_bus.)"""
    vs = _model_versions()
    return vs[-1], vs


def check_model_vs_prices():
    """For each published model: how many of its buses are priced in lmp_bus, and how many priced buses it lacks."""
    f = DERIVED / "lmp_bus.parquet"
    if not f.exists():
        return pd.DataFrame()
    buses = set(pc.unique(pq.read_table(f, columns=["bus"]).column(0)).to_pylist())
    rows = []
    for eff, folder in _model_versions():
        m = set(_read_map(folder, "Settlement_Points")["ELECTRICAL_BUS"])
        rows.append(dict(model=folder.name.split("_SP_List")[0], file_date=eff, buses=len(m), priced=len(m & buses),
                         priced_buses_missing_from_model=len(buses - m), model_buses_never_priced=len(m - buses)))
    return pd.DataFrame(rows)


def mapping_diff(a, b):
    """Summary of what changed between two Settlement_Points frames (a = older, b = newer)."""
    A, B = set(a["ELECTRICAL_BUS"]), set(b["ELECTRICAL_BUS"])
    sa, sb = set(a["SUBSTATION"]), set(b["SUBSTATION"])
    ra, rb = set(a["RESOURCE_NODE"]) - {""}, set(b["RESOURCE_NODE"]) - {""}
    m = a.merge(b, on="ELECTRICAL_BUS", suffixes=("_a", "_b"))
    changed = {c: int((m[c + "_a"] != m[c + "_b"]).sum()) for c in
               ("NODE_NAME", "PSSE_BUS_NAME", "VOLTAGE_LEVEL", "SUBSTATION", "SETTLEMENT_LOAD_ZONE", "RESOURCE_NODE",
                "HUB_BUS_NAME", "HUB", "PSSE_BUS_NUMBER")}
    return dict(buses_a=len(A), buses_b=len(B), buses_added=sorted(B - A), buses_removed=sorted(A - B),
                subs_a=len(sa), subs_b=len(sb), subs_added=sorted(sb - sa), subs_removed=sorted(sa - sb),
                rn_added=sorted(rb - ra), rn_removed=sorted(ra - rb), changed={k: v for k, v in changed.items() if v})


def build_settlement_points(log=print):
    """data/settlement_points.csv (+ .labels.csv) from the newest published model; flat copies of its other NP4-160 files
    go into raw/ercot/NP4-160-SG/.  Returns (table, diffs): diffs compares the Sep 2026 copy with every later model and
    each model with the one before it."""
    from .fetch_ercot import unpack_mapping
    unpack_mapping()                                   # no-op when every downloaded version is already unzipped
    (eff, cur), versions = current_model()
    sp = _read_map(cur, "Settlement_Points")
    noie = _read_map(cur, "NOIE_Mapping")
    nz = noie.groupby("ELECTRICAL_BUS")["NOIE"].agg(lambda s: ";".join(sorted(set(s)))).rename("NOIE_ZONE")
    sp = sp.merge(nz, how="left", left_on="ELECTRICAL_BUS", right_index=True)
    sp["NOIE_ZONE"] = sp["NOIE_ZONE"].fillna("")
    sep = pd.read_csv(SP_MAPPING_SEP2026, dtype=str, keep_default_na=False)
    sp["IN_SEP2026"] = sp["ELECTRICAL_BUS"].isin(set(sep["ELECTRICAL_BUS"]))
    prev = versions[-2][1] if len(versions) > 1 else cur
    sp["IN_PREV_MODEL"] = sp["ELECTRICAL_BUS"].isin(set(_read_map(prev, "Settlement_Points")["ELECTRICAL_BUS"]))
    sp.to_csv(REPO_DATA / "settlement_points.csv", index=False)
    pd.DataFrame(MAP_LABELS, columns=["column", "label", "source", "note"]).to_csv(REPO_DATA / "settlement_points.labels.csv", index=False)
    for stem in MAP_TABLE_FILES:                                   # flat copies of the current files replace older flat copies
        for old in MAP_DIR.glob(f"{stem}_*.csv"):
            old.unlink()
        for src in cur.glob(f"{stem}_*.csv"):
            shutil.copy2(src, MAP_DIR / src.name)
    diffs = {("Sep2026 copy -> " + f.name.split("_SP_List")[0]): mapping_diff(sep, _read_map(f, "Settlement_Points"))
             for _, f in versions}
    for (_, a_), (_, b_) in zip(versions, versions[1:]):
        diffs[f"{a_.name.split('_SP_List')[0]} -> {b_.name.split('_SP_List')[0]}"] = mapping_diff(
            _read_map(a_, "Settlement_Points"), _read_map(b_, "Settlement_Points"))
    log(f"  settlement_points.csv: {len(sp):,} buses from {cur.name}; the Sep 2026 copy had {len(sep):,}")
    return sp, diffs


# ---- NP6-787-CD  lmp_bus: LMP at every electrical bus for every SCED run -------------------------------------------------
def _p_lmp_bus(b):
    d = pd.read_csv(io.BytesIO(b), dtype=str, keep_default_na=False,
                    usecols=["SCEDTimestamp", "RepeatedHourFlag", "ElectricalBus", "CappedLMP"])
    return pd.DataFrame({"sced_ts": parse_stamp(d["SCEDTimestamp"], d["RepeatedHourFlag"]), "bus": d["ElectricalBus"],
                         "lmp": pd.to_numeric(d["CappedLMP"]).astype("float32")})


def parse_lmp_bus(log=print):
    """sced_ts, bus, lmp (capped LMP, $/MWh, float32).  ~19.4k buses per SCED run; ordered by posting day, then file."""
    parts = build_parts("lmp_bus", "NP6-787-CD", _p_lmp_bus, log)
    return consolidate("lmp_bus", parts, stream=True, log=log)


# ---- NP4-183-CD  lmp_dam_bus: DAM hourly LMP at every electrical bus -------------------------------------------------------
def _p_lmp_dam_bus(b):
    d = pd.read_csv(io.BytesIO(b), dtype=str, keep_default_na=False)
    ts, h = hour_ending_to_start(d["DeliveryDate"], d["HourEnding"], d["DSTFlag"])
    iso = {u: pd.to_datetime(u, format="%m/%d/%Y").strftime("%Y-%m-%d") for u in d["DeliveryDate"].unique()}
    date_cpt = d["DeliveryDate"].map(iso)
    return pd.DataFrame({"bus": d["BusName"], "interval_start_utc": ts, "date_cpt": date_cpt,
                         "h": h.astype("int16"), "lmp": pd.to_numeric(d["LMP"]).astype("float32"),
                         "dst_rep": d["DSTFlag"].eq("Y")})


def parse_lmp_dam_bus(log=print):
    parts = build_parts("lmp_dam_bus", "NP4-183-CD", _p_lmp_dam_bus, log)
    return consolidate("lmp_dam_bus", parts, stream=True, log=log)


# ---- NP6-788-CD  lmp_node (extra): 5-minute LMPs at resource nodes, load zones and hubs --------------------------------
def _p_lmp_node(b):
    d = _csv(b)
    return pd.DataFrame({"sced_ts": parse_stamp(d["SCEDTimestamp"], d["RepeatedHourFlag"]),
                         "settlement_point": d["SettlementPoint"], "lmp": pd.to_numeric(d["LMP"]).astype("float32")})


def parse_lmp_node(log=print):
    parts = build_parts("lmp_node", "NP6-788-CD", _p_lmp_node, log)
    return consolidate("lmp_node", parts, stream=True, log=log)


# ---- NP6-323-CD  price_adders (extra): real-time price adders per SCED run ---------------------------------------------
def _p_price_adders(b):
    d = _csv(b)
    out = pd.DataFrame({"sced_ts": parse_stamp(d["SCEDTimestamp"], d["RepeatedHourFlag"])})
    for c in d.columns:
        if c not in ("SCEDTimestamp", "RepeatedHourFlag"):
            out[c.lower() if c != "SystemLambda" else "system_lambda"] = pd.to_numeric(d[c], errors="coerce")
    return out


def parse_price_adders(log=print):
    parts = build_parts("price_adders", "NP6-323-CD", _p_price_adders, log)
    return consolidate("price_adders", parts, key=["sced_ts"], sort=["sced_ts"], log=log)


# ---- driver -------------------------------------------------------------------------------------------------------------
TABLES = {                     # table name -> parser; order = cheapest first, the two big bus tables last
    "settlement_points": lambda log=print: build_settlement_points(log)[0],
    "spp_node": parse_spp_node, "lambda": parse_lambda, "shadow_sced": parse_shadow_sced,
    "shadow_dam": parse_shadow_dam, "spp_dam": parse_spp_dam, "lambda_dam": parse_lambda_dam,
    "as_prices": parse_as_prices, "price_adders": parse_price_adders, "lmp_node": parse_lmp_node,
    "lmp_dam_bus": parse_lmp_dam_bus, "lmp_bus": parse_lmp_bus,
}


def coverage(tables=None):
    """One row per derived parquet: rows, first/last timestamp, file size."""
    rows = []
    for t in tables or [k for k in TABLES if k != "settlement_points"]:
        f = DERIVED / f"{t}.parquet"
        if not f.exists():
            continue
        pf = pq.ParquetFile(f)
        names = pf.schema_arrow.names
        tcol = next((c for c in ("sced_ts", "interval_start_utc") if c in names), None)
        first = last = None
        if tcol:
            s = pq.read_table(f, columns=[tcol]).column(0)
            mm = pc.min_max(s).as_py()
            first, last = mm["min"], mm["max"]
        rows.append(dict(table=t, rows=pf.metadata.num_rows, first=first, last=last, mb=round(f.stat().st_size / 1e6, 1)))
    return pd.DataFrame(rows)


def parse_all(*names, log=print):
    """parse-ercot [table ...]: rebuild the named parquet tables (default all) from the raw zips, incrementally."""
    names = [n for n in names if not n.startswith("-")] or list(TABLES)
    bad = [n for n in names if n not in TABLES]
    if bad:
        raise SystemExit(f"unknown table(s) {bad}; known: {', '.join(TABLES)}")
    failed = {}
    for n in names:
        try:
            TABLES[n](log=log)
        except Exception as e:                       # one bad table must not stop the rest
            failed[n] = f"{type(e).__name__}: {e}"
            log(f"  {n}: FAILED {failed[n]}")
    cov = coverage()
    if len(cov):
        log(cov.to_string(index=False))
    return failed
