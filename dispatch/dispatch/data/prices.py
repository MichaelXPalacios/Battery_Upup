"""Realised prices from the lake, for the monitors and /attribution (D2's `price_source`).

    price_source(spec, start_utc, end_utc) -> PriceFrame

A PriceFrame (contracts.py) is a DataFrame on the 15-minute UTC grid [start_utc, end_utc), index `interval_start_utc`, float64:

    rt_usd_per_mwh              realised real-time settlement point price at spec.settlement_point
    dam_usd_per_mwh             day-ahead price of the hour (forward-filled to 15 minutes); left out if the lake lacks any hour
    as_<product>_usd_per_mw_h   one column per product in spec.as_products (REGUP, REGDN, RRS, ECRS, NSPIN)

COMPLETE OR NOTHING. If any interval of the window lacks an RT price, or any requested AS product lacks a price, it raises
`PricesNotPublished` (a LookupError) and names what is missing: a monitor must see "not there yet", never a NaN that a solver
would turn into a wrong revenue. Callers that treat that as "no data" (D2's providers do) are right to.

Which price is "realised" (every choice is labelled; `df.attrs` records what was used)
* RT price: a row from `rt_spp` (MIS, REAL publish time) beats one from `rt_spp_hist` (NP6-785-ER archive, ASSUMPTION publish
  time); among rows of the same source the LATEST published wins, so ERCOT's price corrections are picked up.
* AS price: the real-time 15-minute MCPC (`rt_as`, `rt_as_hist`; RTC+B era, since 5 Dec 2025) where it exists, else the day-ahead
  MCPC of the hour (`dam_as`, `dam_as_hist`, since Dec 2010) forward-filled. ASSUMPTION: the benchmark prices AS capacity at the
  real-time clearing price once RTC+B exists (the case-study builder in attribution/disclosure.py does the same) and at the
  day-ahead price before. A product that did not exist yet at an interval (ECRS before its first price) is priced 0 there:
  no award was possible, so it cannot earn.
* `asof_utc` (default: now) hides rows published after it, so a replay at an earlier decision time sees what was public then.
"""
from __future__ import annotations

from typing import Optional

import numpy as np
import pandas as pd
import pyarrow.dataset as pads

from .. import config
from . import parse
from .reader import read_as, read_prices
from .times import utc

STEP = pd.Timedelta(minutes=15)
HOUR = pd.Timedelta(hours=1)
_FIRST_SEEN: dict = {}


class PricesNotPublished(LookupError):
    """The lake does not (yet) hold complete realised prices for the requested window."""


def as_price_col(product: str) -> str:
    return f"as_{product.lower()}_usd_per_mw_h"


def _pick(df: pd.DataFrame, asof: pd.Timestamp) -> pd.DataFrame:
    """One row per interval among rows published by `asof`: the REAL source before the ASSUMPTION one, then the latest published."""
    if df.empty:
        return df
    d = df[df["published_utc"] <= asof]
    if d.empty:
        return d
    d = d.sort_values(["interval_start_utc", "prio", "published_utc"], ascending=[True, True, False], kind="stable")
    return d.drop_duplicates("interval_start_utc", keep="first")


def _series(d: pd.DataFrame, value: str, grid: pd.DatetimeIndex) -> pd.Series:
    if d.empty:
        return pd.Series(np.nan, index=grid, dtype="float64")
    s = pd.Series(d[value].to_numpy(dtype="float64"), index=pd.DatetimeIndex(d["interval_start_utc"]).as_unit("us"))
    return s[~s.index.duplicated(keep="last")].reindex(grid)


def _signature() -> tuple:
    out = []
    for name in ("dam_as", "dam_as_hist", "rt_as", "rt_as_hist"):
        for p in parse.partition_files(name):
            out.append((str(p), p.stat().st_mtime_ns))
    return tuple(out)


def first_price_time(product: str) -> Optional[pd.Timestamp]:
    """The first interval for which the lake has ANY price of this ancillary product (None if never)."""
    key = (product, _signature())
    if key not in _FIRST_SEEN:
        if len(_FIRST_SEEN) > 64:
            _FIRST_SEEN.clear()
        firsts = []
        for name in ("dam_as_hist", "dam_as", "rt_as_hist", "rt_as"):
            files = parse.partition_files(name)
            if not files:
                continue
            t = pads.dataset([str(p) for p in files], schema=parse.DATASETS[name].schema, format="parquet").to_table(
                columns=["interval_start_utc"], filter=pads.field("product") == product)
            if t.num_rows:
                firsts.append(pd.Timestamp(t.column(0).to_pandas().min()))
        _FIRST_SEEN[key] = min(firsts) if firsts else None
    return _FIRST_SEEN[key]


def price_source(spec, start_utc, end_utc, *, asof_utc=None, include_dam: bool = True) -> pd.DataFrame:
    """Realised prices of `spec.settlement_point` on the 15-minute UTC grid [start_utc, end_utc). See the module docstring.

    spec        anything with `.settlement_point` and (optionally) `.as_products` (a contracts.BatterySpec)
    asof_utc    only rows published at or before it are used (default: now)
    include_dam add the hourly day-ahead price as `dam_usd_per_mwh` when it is complete for the window
    Raises PricesNotPublished if the window is not complete; ValueError if start/end are not on the 15-minute grid."""
    start, end = utc(start_utc), utc(end_utc)
    if start.value % STEP.value or end.value % STEP.value:
        raise ValueError("start_utc and end_utc must be on the 15-minute UTC grid")
    if end <= start:
        raise ValueError("end_utc must be after start_utc")
    asof = utc(asof_utc) if asof_utc is not None else pd.Timestamp.now(tz="UTC")
    sp = spec.settlement_point
    products = tuple(getattr(spec, "as_products", None) or config.AS_PRODUCTS)
    grid = pd.date_range(start, end, freq="15min", inclusive="left", name="interval_start_utc").as_unit("us")
    last = end - pd.Timedelta(microseconds=1)
    hour_of = grid.floor("h")

    rt_rows = read_prices("rt_spp", "rt_spp_hist", sp, start, last, has_type=True)
    rt_pick = _pick(rt_rows, asof)
    out = pd.DataFrame(index=grid)
    out["rt_usd_per_mwh"] = _series(rt_pick, "price_usd_per_mwh", grid).to_numpy()
    used = {"rt": rt_pick["prio"].map({0: "REAL", 1: "ASSUMPTION"}).value_counts().to_dict() if len(rt_pick) else {}}

    if include_dam:
        dam_rows = read_prices("dam_spp", "dam_spp_hist", sp, start - HOUR, last, has_type=False)
        dam_pick = _pick(dam_rows, asof)
        hourly = _series(dam_pick, "price_usd_per_mwh", pd.DatetimeIndex(sorted(set(hour_of))).as_unit("us"))
        dam = hourly.reindex(hour_of).to_numpy()
        if not np.isnan(dam).any():
            out["dam_usd_per_mwh"] = dam

    as_rt = read_as("rt_as", "rt_as_hist", start, last)
    as_dam = read_as("dam_as", "dam_as_hist", start - HOUR, last)
    as_used = {}
    for p in products:
        rt_p = _pick(as_rt[as_rt["product"] == p] if len(as_rt) else as_rt, asof)
        dam_p = _pick(as_dam[as_dam["product"] == p] if len(as_dam) else as_dam, asof)
        price = _series(rt_p, "mcpc_usd_per_mw_h", grid)
        from_rt = price.notna().to_numpy()
        hourly = _series(dam_p, "mcpc_usd_per_mw_h", pd.DatetimeIndex(sorted(set(hour_of))).as_unit("us"))
        price = price.fillna(pd.Series(hourly.reindex(hour_of).to_numpy(), index=grid))
        from_dam = price.notna().to_numpy() & ~from_rt
        missing = price.isna().to_numpy()
        zero = np.zeros(len(grid), dtype=bool)
        if missing.any():
            first = first_price_time(p)
            zero = missing & ((grid < first) if first is not None else np.zeros(len(grid), dtype=bool))
            price = price.mask(pd.Series(zero, index=grid), 0.0)
        out[as_price_col(p)] = price.to_numpy()
        as_used[p] = {"rt_15min": int(from_rt.sum()), "dam_hourly": int(from_dam.sum()), "not_yet_a_product": int(zero.sum())}

    gaps = {c: out.index[out[c].isna().to_numpy()] for c in out.columns if out[c].isna().any()}
    if gaps:
        parts = [f"{c}: {len(ix)} of {len(out)} intervals, first {ix[0]:%Y-%m-%d %H:%M}Z, last {ix[-1]:%Y-%m-%d %H:%M}Z" for c, ix in gaps.items()]
        raise PricesNotPublished(f"realised prices for {sp!r} are not complete for [{start:%Y-%m-%d %H:%M}Z, {end:%Y-%m-%d %H:%M}Z) "
                                 f"as published by {asof:%Y-%m-%d %H:%M}Z: " + "; ".join(parts))
    out = out.astype("float64")
    out.attrs.update({"settlement_point": sp, "asof_utc": asof, "rt_rows": used["rt"], "as_sources": as_used,
                      "as_rule": "rt_as 15-min MCPC where it exists, else the day-ahead MCPC of the hour; 0 before a product existed"})
    return out
