"""The forecast log: what the model said, kept so that it can be judged once the prices are in.

DESIGN.md "Monitoring policy": every cycle stores the P10/P50/P90 it issued for the intervals it planned, plus the 7-day profile
median `rt_prof7_med` that the model's own forecast is anchored on. A day or a week later the calibration check joins these rows
with the realised prices (`data.prices.price_source`) and asks whether the quantiles behaved as quantiles.

    runtime/forecasts/forecasts-YYYYMMDD.jsonl      one JSON line per cycle, in the file of the UTC day it was issued

    {"schema":1, "asset_id":"PILOT_HB_NORTH", "settlement_point":"HB_NORTH", "model_version":"7",
     "issued_utc":"2026-10-11T14:04:10Z", "logged_utc":"...", "start_utc":"2026-10-11T14:00:00Z", "n":96,
     "p10":[...], "p50":[...], "p90":[...], "rt_prof7_med":[...] | null}

The line is the whole forecast of one cycle: interval k (0-based) starts at `start_utc + 15 min * k` and is horizon k + 1. About 3 KB a
line, 96 lines a day. Append only (mode "ab", one write per line, flushed); a torn last line is skipped by the reader and reported,
never raised, because the log feeds a monitor and a monitor must not die on a bad byte. Nothing here deletes anything.

A write that fails is counted (`ForecastLog.errors`, the metric `dispatch_forecast_log_errors_total`) and logged; it never stops a
cycle: dispatch safety does not depend on this file, but the calibration check does, and it reports "skipped" when it has too few rows.
"""
from __future__ import annotations

import json
import logging
import math
import threading
from pathlib import Path
from typing import Iterable, Optional

import numpy as np
import pandas as pd

from .. import paths

log = logging.getLogger("dispatch.monitoring")

SCHEMA = 1
FILE_PREFIX, FILE_SUFFIX = "forecasts-", ".jsonl"
STEP = pd.Timedelta(minutes=15)
DECIMALS = 4                                  # ASSUMPTION: $0.0001/MWh is far below any price tick; keeps a line near 3 KB
ANCHOR_COLUMN = "rt_prof7_med"                # the model's 7-day profile median (D1a's anchor; D0's rt_v1 1.1.0 carries it under the same name)
COLUMNS = ("asset_id", "settlement_point", "model_version", "issued_utc", "interval_start_utc", "horizon", "rt_p10", "rt_p50", "rt_p90",
           ANCHOR_COLUMN)


def _utc(ts) -> pd.Timestamp:
    t = pd.Timestamp(ts)
    return t.tz_localize("UTC") if t.tzinfo is None else t.tz_convert("UTC")


def _iso(ts) -> str:
    return _utc(ts).strftime("%Y-%m-%dT%H:%M:%S.%fZ").replace(".000000Z", "Z")


def _list(values) -> list:
    out = []
    for v in np.asarray(values, dtype="float64"):
        out.append(None if not math.isfinite(v) else round(float(v), DECIMALS))
    return out


class ForecastLogError(RuntimeError):
    pass


class ForecastLog:
    """Append-only JSONL forecast log under `DISPATCH_HOME/runtime/forecasts/` (or `directory`)."""

    def __init__(self, directory: Optional[Path] = None, *, fsync: bool = False):
        self.dir = Path(directory) if directory is not None else Path(paths.RUNTIME) / "forecasts"
        self.dir.mkdir(parents=True, exist_ok=True)
        self._fsync = fsync
        self._lock = threading.Lock()
        self.errors = 0
        self.last_error = ""

    # ---- writing
    def path_for(self, issued_utc) -> Path:
        return self.dir / f"{FILE_PREFIX}{_utc(issued_utc):%Y%m%d}{FILE_SUFFIX}"

    def record(self, *, asset_id: str, settlement_point: str, model_version: str, issued_utc, frame: pd.DataFrame,
               features: Optional[pd.DataFrame] = None) -> dict:
        """Build the line for one cycle from the forecast frame (`rt_p10/p50/p90` on the 15-minute index) and, when given, the feature
        frame the model was fed (its `rt_prof7_med` column, aligned on the same index)."""
        for c in ("rt_p10", "rt_p50", "rt_p90"):
            if c not in frame.columns:
                raise ForecastLogError(f"the forecast frame has no {c!r} column")
        idx = pd.DatetimeIndex(frame.index)
        if idx.tz is None or len(idx) == 0:
            raise ForecastLogError("the forecast frame needs a tz-aware, non-empty index")
        idx = idx.tz_convert("UTC")
        contiguous = bool(len(idx) == 1 or (idx[1:] - idx[:-1] == STEP).all())
        prof = None
        if features is not None and ANCHOR_COLUMN in features.columns and getattr(features.index, "tz", None) is not None:
            prof = features[ANCHOR_COLUMN].reindex(idx).to_numpy("float64")        # aligned on the interval, not on the row number
        rec = {"schema": SCHEMA, "asset_id": str(asset_id), "settlement_point": str(settlement_point), "model_version": str(model_version),
               "issued_utc": _iso(issued_utc), "logged_utc": _iso(pd.Timestamp.now(tz="UTC")), "start_utc": _iso(idx[0]), "n": int(len(idx)),
               "p10": _list(frame["rt_p10"]), "p50": _list(frame["rt_p50"]), "p90": _list(frame["rt_p90"]),
               ANCHOR_COLUMN: None if prof is None else _list(prof)}
        if not contiguous:
            rec["intervals"] = [_iso(t) for t in idx]
        return rec

    def append(self, rec: dict) -> Path:
        line = (json.dumps(rec, separators=(",", ":"), allow_nan=False) + "\n").encode("utf-8")
        path = self.path_for(rec["issued_utc"])
        with self._lock:
            with open(path, "ab+") as f:
                if f.tell() > 0:                                      # a crash in the middle of a line leaves no newline: do not glue onto it
                    f.seek(-1, 2)
                    if f.read(1) != b"\n":
                        f.write(b"\n")
                f.write(line)
                f.flush()
                if self._fsync:
                    import os
                    os.fsync(f.fileno())
        return path

    def log_cycle(self, **kw) -> bool:
        """`record` + `append`, never raising: True if the line was written. A failure is counted and logged."""
        try:
            self.append(self.record(**kw))
            return True
        except Exception as exc:                  # the log must not stop a cycle
            self.errors += 1
            self.last_error = f"{type(exc).__name__}: {exc}"[:300]
            log.error("forecast log write failed: %s", self.last_error)
            return False

    # ---- reading
    def files(self, start_utc=None, end_utc=None) -> list:
        """Daily files that can hold forecasts issued in [start_utc, end_utc] (both optional)."""
        out = []
        lo = _utc(start_utc).floor("D") if start_utc is not None else None
        hi = _utc(end_utc).floor("D") if end_utc is not None else None
        for p in sorted(self.dir.glob(f"{FILE_PREFIX}*{FILE_SUFFIX}")):
            try:
                day = pd.Timestamp(p.name[len(FILE_PREFIX):-len(FILE_SUFFIX)], tz="UTC")
            except ValueError:
                continue
            if (lo is None or day >= lo) and (hi is None or day <= hi):
                out.append(p)
        return out

    def read_records(self, start_issued=None, end_issued=None) -> Iterable[dict]:
        """The raw lines issued in [start_issued, end_issued], oldest first. A torn or unreadable line is skipped and counted."""
        lo = _utc(start_issued) if start_issued is not None else None
        hi = _utc(end_issued) if end_issued is not None else None
        for p in self.files(lo, hi):
            with open(p, "rb") as f:
                for raw in f:
                    if not raw.strip():
                        continue
                    try:
                        rec = json.loads(raw)
                        t = _utc(rec["issued_utc"])
                    except (ValueError, KeyError):
                        self.errors += 1
                        self.last_error = f"unreadable line in {p.name}"
                        continue
                    if (lo is None or t >= lo) and (hi is None or t <= hi):
                        yield rec

    def frame(self, start_target, end_target, *, asset_id: Optional[str] = None, settlement_point: Optional[str] = None,
              model_version: Optional[str] = None) -> pd.DataFrame:
        """Every logged (forecast, target interval) pair whose target starts in [start_target, end_target), as rows with COLUMNS.
        One row per issued forecast and interval (a target interval has up to 96, one per horizon). If a cycle was logged twice at the
        same issue time (a retry, a replay) the LAST line wins."""
        t0, t1 = _utc(start_target), _utc(end_target)
        latest: dict = {}
        for rec in self.read_records(t0 - pd.Timedelta(hours=24) - STEP, t1):
            if asset_id is not None and rec.get("asset_id") != asset_id:
                continue
            if settlement_point is not None and rec.get("settlement_point") != settlement_point:
                continue
            if model_version is not None and str(rec.get("model_version")) != str(model_version):
                continue
            latest[(rec.get("asset_id"), rec["issued_utc"])] = rec
        parts = []
        for rec in latest.values():
            n = int(rec["n"])
            if "intervals" in rec:
                starts = pd.DatetimeIndex([_utc(t) for t in rec["intervals"]])
            else:
                starts = pd.date_range(_utc(rec["start_utc"]), periods=n, freq="15min")
            issued = _utc(rec["issued_utc"])
            keep = (starts >= t0) & (starts < t1)
            if not keep.any():
                continue
            prof = rec.get(ANCHOR_COLUMN)
            d = {"asset_id": rec.get("asset_id"), "settlement_point": rec.get("settlement_point"),
                 "model_version": str(rec.get("model_version")), "issued_utc": issued, "interval_start_utc": starts,
                 "horizon": ((starts - issued.floor("15min")) / STEP).astype(int) + 1,      # horizon 1 is the interval that contains the issue time
                 "rt_p10": _arr(rec["p10"]), "rt_p50": _arr(rec["p50"]), "rt_p90": _arr(rec["p90"]),
                 ANCHOR_COLUMN: _arr(prof) if prof is not None else np.full(n, np.nan)}
            parts.append(pd.DataFrame(d)[keep])
        if not parts:
            return pd.DataFrame({c: [] for c in COLUMNS})
        out = pd.concat(parts, ignore_index=True)
        return out[list(COLUMNS)]


def _arr(values) -> np.ndarray:
    return np.array([np.nan if v is None else v for v in values], dtype="float64")
