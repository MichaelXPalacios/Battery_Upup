"""Attribution inputs for a PUBLIC battery: what a real ESR did, from ERCOT's 60-day SCED disclosure (the pilot of the case study).

`GET /attribution?asset_id=PAD1_ESS_ESR1&start=2026-07-06&end=2026-07-13` answers for assets listed under `disclosure_assets` in the assets
file (ops/assets.example.json). They are not paper assets: no cycle runs for them (`/signal` answers 404), there is no ledger. Their
"actual dispatch" is the resource's own telemetry, which ERCOT publishes 60 days late.

    actual     D0's parsed dataset `esr_sced` (telemetered output, state of charge and limits, HSL/LSL, ancillary awards for every SCED run),
               aggregated onto the 15-minute grid by D1b's `attribution.disclosure.aggregate_runs` (time-weighted flows, end-of-interval SOC,
               derate-only availability). The standing auxiliary draw the case study measured (`aux_load_mw`) is added back, as the study did.
    prices     D0's `data.prices.price_source` at the settlement HUB (a labelled proxy: the node's own price history is only the 7-day MIS
               window). The case study measured the node-minus-hub basis; its stress test is why Gate 2 is "mechanically met, not proven".
    position   the day-ahead positions relative to real time (D1b's `dispatch_position`), when D1b's derived DAM cache holds the resource;
               without it the position term is left out and the response says so.

The window must lie inside the days the lake holds (`first_day` to `last_day`, Central time). Nothing here is a model: all of it is
accounting on published numbers.
"""
from __future__ import annotations

import dataclasses
import json
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Mapping, Optional

import pandas as pd

from ..contracts import ActualDispatch, BatterySpec

log = logging.getLogger("dispatch.service")

STEP = pd.Timedelta(minutes=15)
RUN_PAD = pd.Timedelta(minutes=30)               # SCED runs just outside the window that the first and last interval need (as the case study)
_SPEC_KEYS = {"power_mw", "energy_mwh", "soc_min_mwh", "soc_max_mwh", "rte", "loss_side", "wear_usd_per_mwh", "as_products"}
_EXTRA_KEYS = {"asset_id", "resource", "settlement_point", "price_hub", "charge_mw", "aux_load_mw", "first_day", "last_day", "labels", "mode"}


@dataclass(frozen=True)
class DisclosureAsset:
    asset_id: str                       # the public resource name, e.g. PAD1_ESS_ESR1
    resource: str                       # the same name as it appears in esr_sced
    settlement_point: str               # the resource node (informational: its own price history is too short)
    price_hub: str                      # the hub whose real-time price stands in for the node
    spec: BatterySpec
    charge_mw: float                    # charge limit (|LSL|), when it differs from the discharge limit
    aux_load_mw: float                  # standing station-service draw measured in the telemetry (added back to the output)
    first_day: str                      # first and last operating day the lake holds, Central time
    last_day: str
    labels: Mapping = field(default_factory=dict)

    @property
    def coverage_utc(self) -> tuple:
        """[start, end) of the days the lake holds, as UTC timestamps."""
        start = pd.Timestamp(self.first_day).tz_localize("America/Chicago").tz_convert("UTC")
        end = (pd.Timestamp(self.last_day) + pd.Timedelta(days=1)).tz_localize("America/Chicago").tz_convert("UTC")
        return start, end


def parse_disclosure_asset(raw: Mapping) -> DisclosureAsset:
    unknown = set(raw) - _SPEC_KEYS - _EXTRA_KEYS
    if unknown:
        raise ValueError(f"disclosure asset {raw.get('asset_id')}: unknown keys {sorted(unknown)}")
    if raw.get("mode", "disclosure") != "disclosure":
        raise ValueError(f"disclosure asset {raw.get('asset_id')}: mode must be 'disclosure'")
    kw = {k: raw[k] for k in _SPEC_KEYS if k in raw}
    if "as_products" in kw:
        kw["as_products"] = tuple(kw["as_products"])
    spec = BatterySpec(asset_id=raw["asset_id"], settlement_point=raw.get("price_hub") or raw["settlement_point"], **kw)
    return DisclosureAsset(asset_id=raw["asset_id"], resource=raw.get("resource", raw["asset_id"]), settlement_point=raw["settlement_point"],
                           price_hub=raw["price_hub"], spec=spec, charge_mw=float(raw.get("charge_mw", spec.power_mw)),
                           aux_load_mw=float(raw.get("aux_load_mw", 0.0)), first_day=raw["first_day"], last_day=raw["last_day"],
                           labels=dict(raw.get("labels", {})))


def load_disclosure_assets(path: Optional[Path] = None) -> dict:
    """{asset_id: DisclosureAsset} from the `disclosure_assets` list of the assets file (none when the key is absent)."""
    from .assets import DEFAULT_ASSETS_FILE
    path = Path(path or os.environ.get("DISPATCH_ASSETS_FILE") or DEFAULT_ASSETS_FILE)
    doc = json.loads(path.read_text(encoding="utf-8"))
    out = {}
    for raw in doc.get("disclosure_assets", []):
        a = parse_disclosure_asset(raw)
        if a.asset_id in out:
            raise ValueError(f"duplicate disclosure asset {a.asset_id}")
        out[a.asset_id] = a
    return out


# ================================================================================================ inputs for the engine
def esr_rows(resource: str, start: pd.Timestamp, end: pd.Timestamp, *, read: Optional[Callable] = None) -> pd.DataFrame:
    """SCED runs of one ESR in [start - 30 min, end + 30 min), in the column names D1b's `aggregate_runs` reads."""
    import pyarrow.dataset as pads
    if read is None:
        from ..data import parse
        read = parse.read
    lo, hi = start - RUN_PAD, end + RUN_PAD
    flt = (pads.field("resource_name") == resource) & (pads.field("interval_start_utc") >= lo.to_pydatetime()) \
        & (pads.field("interval_start_utc") < hi.to_pydatetime())
    d = read("esr_sced", filter=flt)
    if d.empty:
        return d
    out = pd.DataFrame({"ts_utc": pd.DatetimeIndex(d["interval_start_utc"]).tz_convert("UTC"), "resource": d["resource_name"],
                        "net_mw": d["net_output_mw"], "soc_mwh": d["soc_mwh"], "min_soc_mwh": d["min_soc_mwh"], "max_soc_mwh": d["max_soc_mwh"],
                        "hsl_mw": d["hsl_mw"], "lsl_mw": d["lsl_mw"], "base_point_mw": d["base_point_mw"]})
    for ours, theirs in (("aw_regup", "as_award_regup_mw"), ("aw_regdn", "as_award_regdn_mw"), ("aw_rrspfr", "as_award_rrspfr_mw"),
                         ("aw_rrsffr", "as_award_rrsffr_mw"), ("aw_rrsufr", "as_award_rrsufr_mw"), ("aw_ecrs", "as_award_ecrs_mw"),
                         ("aw_nspin", "as_award_nspin_mw")):
        out[ours] = d[theirs].fillna(0.0).to_numpy()                 # a blank award means none (D0 keeps the NaN, D1b's reader zero-fills)
    return out.sort_values("ts_utc").reset_index(drop=True)


def disclosure_inputs(asset: DisclosureAsset, start: pd.Timestamp, end: pd.Timestamp, *, price_source: Callable,
                      forecast_fn: Optional[Callable] = None, read: Optional[Callable] = None,
                      dam_loader: Optional[Callable] = None) -> tuple:
    """(ActualDispatch, PriceFrame, forecast_fn, engine kwargs) for `attribute(spec, actual, prices, forecast_fn, **kwargs)`.
    Raises LookupError (the endpoint answers 404 "no_data") for a window outside the lake's coverage or without runs."""
    from ..attribution import disclosure as D
    start, end = pd.Timestamp(start).tz_convert("UTC"), pd.Timestamp(end).tz_convert("UTC")
    if start.value % STEP.value or end.value % STEP.value:
        raise ValueError("start and end must be on the 15-minute UTC grid")
    c0, c1 = asset.coverage_utc
    if start < c0 or end > c1:
        raise LookupError(f"the lake holds the 60-day disclosure of {asset.asset_id} for operating days {asset.first_day} to {asset.last_day} "
                          f"only ({c0:%Y-%m-%d %H:%M}Z to {c1:%Y-%m-%d %H:%M}Z); asked for {start:%Y-%m-%d %H:%M}Z to {end:%Y-%m-%d %H:%M}Z")
    rows = esr_rows(asset.resource, start, end, read=read)
    if rows.empty:
        raise LookupError(f"no SCED runs of {asset.resource} between {start} and {end} in the lake's esr_sced dataset")
    idx = pd.date_range(start, end - STEP, freq="15min", name="interval_start_utc")
    spec = asset.spec
    agg = D.aggregate_runs(rows, idx, spec.power_mw, asset.charge_mw, spec.eta_charge, spec.eta_discharge, asset.aux_load_mw)
    actual = ActualDispatch(asset_id=asset.asset_id, source="ercot-60day-sced", frame=agg,
                            notes=(f"60-day SCED disclosure (NP3-965-ER) esr_sced for {asset.resource}, {start:%Y-%m-%d}..{end:%Y-%m-%d}",
                                   f"net_mw = telemetered net output + {asset.aux_load_mw:.2f} MW standing auxiliary draw, time-weighted over SCED runs",
                                   f"prices are the {asset.price_hub} hub proxy for node {asset.settlement_point}"))
    prices = price_source(spec, start, end)                       # spec.settlement_point is the hub: the proxy
    kwargs: dict = {"charge_limit_mw": asset.charge_mw}
    try:
        dam = (dam_loader or D.load_cached)("dam", resources=[asset.resource])
        if len(dam):
            kwargs["position_usd"] = D.dam_position(dam, prices, idx)
        else:
            actual = dataclasses.replace(actual, notes=actual.notes + ("no day-ahead position: the DAM disclosure cache holds no rows",))
    except Exception as exc:                                      # the position is an extra; the rest of the attribution stands without it
        log.info("no day-ahead position for %s: %s", asset.asset_id, exc)
        actual = dataclasses.replace(actual, notes=actual.notes + (f"no day-ahead position: {type(exc).__name__}",))
    return actual, prices, forecast_fn, kwargs
