"""HISTORICAL. How `interim_golden_*.parquet` were made, on 11 Oct 2026, from D1a's interim feature builder.

This script ran once, against `dispatch/models/interim_features.py` (git commit 8ce9649; sha256 of that file
5e120505a0e88bda6618d005003b0e11c41d0cf4324b8447ae92583c70fc10c2) and the real research data, to freeze what that builder produced. The builder
was retired the same day (notes/p1-retrain.md), so the script cannot run on the tree as it is; to reproduce the fixture, check out the commit
above. The fixture is what `tests/test_rt_v1_1_equivalence.py` holds D0's builder to.

What it froze, for four hubs (HB_NORTH, HB_WEST, HB_HOUSTON, HB_SOUTH) and five decision times each (the dates are the ones that break
time-of-day code: a spring-forward day, a fall-back day, Winter Storm Fern, an observed federal holiday, the newest data):

    interim_golden_features.parquet   one row per (hub, decision, horizon 1..96): the 59 columns of the registered model (interim names),
                                      the day-ahead ECRS price, and what version 1 of the registered model forecast from them
                                      (v1_rt_p10/p50/p90 and v1_as_<product>_p50)
    interim_golden_inputs.parquet     the raw inputs those frames were built from, so a test can rebuild them without the real lake:
                                      the 15-minute price of each hub and the hourly day-ahead AS prices, from 16 (30) days before each
                                      decision to just over a day after it
    interim_golden.sha256             sha256 of both files (a changed fixture must be a deliberate act)
"""
from __future__ import annotations

import hashlib
import sys
from pathlib import Path

import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1]))

HUBS = ("HB_NORTH", "HB_WEST", "HB_HOUSTON", "HB_SOUTH")
DECISIONS = (
    "2019-03-10T20:00:00Z",    # the spring-forward day of 2019 (15:00 CDT), early in the history
    "2025-11-02T16:00:00Z",    # the fall-back day of 2025 (10:00 CST): the repeated hour is in the day-ahead windows
    "2026-01-26T12:00:00Z",    # Winter Storm Fern: spikes, negative residuals, the largest scale features
    "2026-07-03T16:00:00Z",    # the observed Independence Day holiday (4 July 2026 is a Saturday)
    "2026-09-24T12:00:00Z",    # the newest data
)
PRODUCTS = ("regup", "regdn", "rrs", "ecrs", "nspin")


def main() -> None:
    from dispatch.models import interim_features as F
    from dispatch.registry import mlflow_registry as R

    as_hist = F.load_as_history()                        # the frozen copy v1 was trained with
    model = R.default().load_version("1")
    names = tuple(F.RT_FEATURES) + F.as_feature_columns(F.AS_MODEL_PRODUCTS) + ("as_ecrs_dam",)
    out, inputs = [], []
    for sp in HUBS:
        hist = F.load_history(sp)
        for d in DECISIONS:
            asof = pd.Timestamp(d)
            X = F.point_in_time(sp, asof, 96, history=hist, as_hist=as_hist)
            fc = model.predict(X, asof, settlement_point=sp).frame
            row = X[list(names)].copy()
            row.insert(0, "interval_start_utc", X.index)
            row.insert(0, "decision_utc", asof)
            row.insert(0, "sp", sp)
            for k in ("rt_p10", "rt_p50", "rt_p90"):
                row[f"v1_{k}"] = fc[k].to_numpy(dtype="float32")
            for p in PRODUCTS:
                row[f"v1_as_{p}_p50"] = fc[f"as_{p}_p50"].to_numpy(dtype="float32")
            out.append(row.reset_index(drop=True))
            lo, hi = asof - pd.Timedelta(days=16), asof + pd.Timedelta(hours=26)
            s = hist.series()
            s = s[(s.index >= lo) & (s.index < hi)].dropna()
            inputs.append(pd.DataFrame({"series": "rt", "key": sp, "interval_start_utc": s.index, "value": s.to_numpy()}))
            a = as_hist.frame[(as_hist.frame.index >= asof - pd.Timedelta(days=30)) & (as_hist.frame.index < asof + pd.Timedelta(days=2))]
            for p in PRODUCTS:
                v = a[p].dropna()
                inputs.append(pd.DataFrame({"series": "dam_as", "key": p, "interval_start_utc": v.index, "value": v.to_numpy()}))
    feats = pd.concat(out, ignore_index=True)
    inp = pd.concat(inputs, ignore_index=True).drop_duplicates(["series", "key", "interval_start_utc"]).sort_values(
        ["series", "key", "interval_start_utc"], ignore_index=True)
    inp["interval_start_utc"] = pd.DatetimeIndex(inp["interval_start_utc"]).as_unit("us")
    feats["decision_utc"] = pd.DatetimeIndex(feats["decision_utc"]).as_unit("us")
    feats["interval_start_utc"] = pd.DatetimeIndex(feats["interval_start_utc"]).as_unit("us")
    fp, ip = HERE / "interim_golden_features.parquet", HERE / "interim_golden_inputs.parquet"
    feats.to_parquet(fp, index=False, compression="zstd")
    inp.to_parquet(ip, index=False, compression="zstd")
    (HERE / "interim_golden.sha256").write_text(
        "".join(f"{hashlib.sha256(p.read_bytes()).hexdigest()}  {p.name}\n" for p in (fp, ip)), encoding="utf-8", newline="\n")
    print(feats.shape, inp.shape, fp.stat().st_size, ip.stat().st_size)


if __name__ == "__main__":
    main()
