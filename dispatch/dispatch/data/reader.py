"""Windowed reads of the parsed datasets, shared by the feature builder and the realised-price source.

`read_window(dataset, lo, hi, columns, flt)` returns the rows describing intervals in [lo, hi]. Partitions are chosen from their
`date=` key (see parse.py): a file-mode dataset is partitioned by the CPT date it was POSTED, so the read adds slack on both
sides; a bulk dataset by the CPT month of the interval. The pyarrow filter then does the exact cut.
"""
from __future__ import annotations

import pandas as pd
import pyarrow as pa
import pyarrow.dataset as pads

from . import parse
from .times import CPT

TS = pa.timestamp("us", tz="UTC")
EW_TYPES = ("LZEW", "LZ_DCEW")        # load-zone price variants that duplicate the LZ rows


def cpt_date(ts: pd.Timestamp) -> str:
    return ts.tz_convert(CPT).strftime("%Y-%m-%d")


def month_floor(ts: pd.Timestamp) -> str:
    return ts.tz_convert(CPT).strftime("%Y-%m-01")


def read_window(dataset: str, lo: pd.Timestamp, hi: pd.Timestamp, columns: list, flt=None) -> pd.DataFrame:
    """Rows of a parsed dataset describing intervals in [lo, hi] (both tz-aware, inclusive)."""
    ds = parse.DATASETS[dataset]
    if ds.mode == "file":
        d_from, d_to = cpt_date(lo - pd.Timedelta(days=3)), cpt_date(hi + pd.Timedelta(days=1))
    else:
        d_from, d_to = month_floor(lo), cpt_date(hi)
    files = parse.partition_files(dataset, d_from, d_to)
    if not files:
        return pd.DataFrame({c: pd.Series(dtype="object") for c in columns})
    f = (pads.field("interval_start_utc") >= pa.scalar(lo.to_pydatetime(), TS)) & \
        (pads.field("interval_start_utc") <= pa.scalar(hi.to_pydatetime(), TS))
    if flt is not None:
        f = f & flt
    return pads.dataset([str(p) for p in files], schema=ds.schema, format="parquet").to_table(columns=columns, filter=f).to_pandas()


def read_prices(main: str, hist: str, sp: str, lo, hi, *, has_type: bool) -> pd.DataFrame:
    """One settlement point's prices from the live window (prio 0, REAL) and the yearly history (prio 1, ASSUMPTION).
    Columns: interval_start_utc, published_utc, price_usd_per_mwh, prio."""
    cols = ["interval_start_utc", "published_utc", "price_usd_per_mwh"] + (["sp_type"] if has_type else [])
    parts = []
    for name, prio in ((main, 0), (hist, 1)):
        flt = pads.field("settlement_point") == sp
        if has_type:
            flt = flt & ~pads.field("sp_type").isin(list(EW_TYPES))
        d = read_window(name, lo, hi, cols, flt)
        if len(d):
            d["prio"] = prio
            parts.append(d.drop(columns=["sp_type"], errors="ignore"))
    if not parts:
        return pd.DataFrame(columns=["interval_start_utc", "published_utc", "price_usd_per_mwh", "prio"])
    return pd.concat(parts, ignore_index=True)


def read_as(main: str, hist: str, lo, hi) -> pd.DataFrame:
    """Ancillary clearing prices from the live window (prio 0) and the yearly history (prio 1).
    Columns: interval_start_utc, published_utc, product, mcpc_usd_per_mw_h, prio."""
    cols = ["interval_start_utc", "published_utc", "product", "mcpc_usd_per_mw_h"]
    parts = []
    for name, prio in ((main, 0), (hist, 1)):
        d = read_window(name, lo, hi, cols)
        if len(d):
            d["prio"] = prio
            parts.append(d)
    if not parts:
        return pd.DataFrame(columns=cols + ["prio"])
    return pd.concat(parts, ignore_index=True)
