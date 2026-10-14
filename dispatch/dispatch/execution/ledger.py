"""The paper ledger (PRD FR9): where an accepted setpoint "executes" in v1.

Nothing here talks to a market. The ledger records every setpoint the guard accepted, keeps the state of charge that the
asset would have (in paper mode this IS the SOC telemetry the guard checks), settles the setpoints against realised prices,
and can replay a period from its own file, which is how Gate 3 ("paper matches backtest") is measured.

File: `DISPATCH_HOME/runtime/ledger/ledger-<asset_id>.jsonl`, append only, one JSON object per line.

    {"type":"init", "schema":1, "asset_id":..., "soc_mwh":10.0, "cost_basis_usd":0.0, "ts_utc":...}
    {"type":"setpoint", "schema":1, "seq":0, "asset_id":..., "interval_start_utc":..., "net_mw":..., "as_mw":{...},
     "plan_id":..., "model_version":..., "issued_utc":..., "recorded_utc":..., "soc_start_mwh":..., "soc_end_mwh":...,
     "cost_basis_end_usd":..., "ref_price_usd_per_mwh":..., "audit_hash":...}

Rules
  - One setpoint covers a whole 15-minute interval. A later cycle in the same interval may issue a new setpoint for it: it
    supersedes the earlier line (the earlier line stays in the file). Settlement and SOC use the last line per interval.
  - A setpoint for an interval older than the newest recorded one is refused ("stale_interval"), and so is a setpoint whose
    `issued_utc` does not move forward ("non_increasing_issue"). Resubmitting an identical setpoint is a no-op. This is the
    fencing rule of resilience/runtime/device.py (EpochDevice: stale epoch, non-increasing seq) applied to the ledger.
  - Defence in depth: `record` re-checks power, AS and SOC limits against the SOC the ledger itself holds. If the guard saw
    a different SOC than the ledger (a race, a bug), the ledger refuses and nothing "executes".
  - SOC moves with dispatch.guard.guard.project_soc_end, the same function the guard uses.
  - Money moves through dispatch.units only: energy $ = $/MWh x MWh, AS $ = $/MW-h x MW x h. Revenue of an interval is
    energy + AS - wear, the same accounting as dispatch.optimizer.lp.account. Ancillary deployment energy is not modelled,
    only the capacity payment. Wear is charged on discharged MWh.
  - Exposure (for the guard's cap) is the cost basis of the energy in the battery: charging adds MWh x reference price
    (the P90 RT forecast the cycle passes, or the ERCOT price cap if none), discharging removes the same share of the
    stored energy as of the cost. Energy the battery starts with costs nothing.
"""
from __future__ import annotations

import dataclasses
import json
import math
import os
import re
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping, Optional

import numpy as np
import pandas as pd

from .. import paths, units
from ..config import AS_DURATION_H, ERCOT_PRICE_CAP, GUARD_EPS_MW
from ..contracts import RT, ActualDispatch, BatterySpec, Setpoint, as_award_col, as_price_col
from ..guard.guard import GUARD_EPS_MWH, project_soc_end
from .audit import to_jsonable

SCHEMA = 1
STEP = pd.Timedelta(minutes=15)
_SAFE_ID = re.compile(r"^[A-Za-z0-9_.\-]{1,64}$")
REVENUE_COLUMNS = ("energy_usd", "as_usd", "wear_usd", "revenue_usd")


class LedgerError(ValueError):
    """The ledger refused a record. `code` is machine readable (stale_interval, non_increasing_issue, limit_violation:..., ...)."""

    def __init__(self, code: str, message: str = ""):
        super().__init__(f"{code}: {message}" if message else code)
        self.code = code


@dataclass(frozen=True)
class LedgerEntry:
    seq: int
    asset_id: str
    interval_start_utc: pd.Timestamp
    net_mw: float
    as_mw: Mapping[str, float]
    plan_id: str
    model_version: str
    issued_utc: pd.Timestamp
    recorded_utc: pd.Timestamp
    soc_start_mwh: float
    soc_end_mwh: float
    cost_basis_end_usd: float
    ref_price_usd_per_mwh: Optional[float] = None
    audit_hash: str = ""

    def to_setpoint(self) -> Setpoint:
        return Setpoint(self.asset_id, self.interval_start_utc, self.net_mw, dict(self.as_mw), self.plan_id,
                        self.model_version, self.issued_utc)


@dataclass(frozen=True)
class Transition:
    soc_start_mwh: float
    soc_end_mwh: float
    cost_basis_start_usd: float
    cost_basis_end_usd: float


@dataclass
class ReplayResult:
    frame: pd.DataFrame                        # one row per executed interval (see PaperLedger.settle) plus replayed SOC
    revenue_usd: float
    n_intervals: int
    soc_max_abs_diff_mwh: float                # replayed SOC against the SOC written in the file
    problems: list = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.problems


def reference_transition(spec: BatterySpec, soc0_mwh: float, cost0_usd: float, net_mw: float,
                         ref_price_usd_per_mwh: Optional[float] = None) -> Transition:
    """SOC and exposure after holding `net_mw` for one interval. Used by `preview`, `record` and `replay`."""
    charge_mwh = units.energy_mwh(max(-net_mw, 0.0))
    discharge_mwh = units.energy_mwh(max(net_mw, 0.0))
    soc1 = project_soc_end(spec, soc0_mwh, net_mw)
    cost1 = cost0_usd
    if charge_mwh > 0:
        price = ref_price_usd_per_mwh
        if price is None or not math.isfinite(price):
            price = ERCOT_PRICE_CAP                                  # no quote: assume the most expensive case
        cost1 = cost0_usd + units.energy_revenue_usd(max(price, 0.0), charge_mwh)
    elif discharge_mwh > 0:
        stored_out = discharge_mwh / spec.eta_discharge
        inventory = max(soc0_mwh - spec.soc_min_mwh, 0.0)
        share = 1.0 if inventory <= 0 else min(stored_out / inventory, 1.0)
        cost1 = cost0_usd * (1.0 - share)
    return Transition(soc0_mwh, soc1, cost0_usd, cost1)


def account_entries(spec: BatterySpec, entries: list, prices: pd.DataFrame) -> pd.DataFrame:
    """Per-interval revenue of executed setpoints at realised prices (the accounting described in the module docstring)."""
    cols = ["net_mw", "charge_mw", "discharge_mw", "soc_start_mwh", "soc_end_mwh", RT, "energy_usd", "as_usd",
            "wear_usd", "revenue_usd", "plan_id", "model_version"]
    idx = pd.DatetimeIndex([e.interval_start_utc for e in entries], name="interval_start_utc")
    if not entries:
        return pd.DataFrame(columns=cols, index=idx)
    if not isinstance(prices, pd.DataFrame) or RT not in prices.columns:
        raise LedgerError("missing_price", f"prices need a '{RT}' column")
    pidx = prices.index
    if not isinstance(pidx, pd.DatetimeIndex) or pidx.tz is None or pidx.has_duplicates:
        raise LedgerError("bad_prices", "prices need a unique, tz-aware DatetimeIndex")
    px = prices.set_axis(pidx.tz_convert("UTC"), axis=0).reindex(idx)
    rt = px[RT].to_numpy(float)
    if np.isnan(rt).any():
        first = idx[np.isnan(rt)][0]
        raise LedgerError("missing_price", f"no realised RT price for interval {first}")
    net = np.array([e.net_mw for e in entries], float)
    charge, discharge = np.clip(-net, 0.0, None), np.clip(net, 0.0, None)
    energy = units.energy_revenue_usd(rt, units.energy_mwh(discharge - charge))
    as_usd = np.zeros(len(entries))
    for k, e in enumerate(entries):
        for product, award in e.as_mw.items():
            if award == 0:
                continue
            col = as_price_col(product)
            if col not in px.columns or math.isnan(px[col].iloc[k]):
                raise LedgerError("missing_price", f"no {col} for interval {idx[k]} but an award of {award} MW")
            as_usd[k] += units.as_revenue_usd(float(px[col].iloc[k]), float(award))
    wear = units.energy_revenue_usd(spec.wear_usd_per_mwh, units.energy_mwh(discharge))
    out = pd.DataFrame({
        "net_mw": net, "charge_mw": charge, "discharge_mw": discharge,
        "soc_start_mwh": [e.soc_start_mwh for e in entries], "soc_end_mwh": [e.soc_end_mwh for e in entries],
        RT: rt, "energy_usd": energy, "as_usd": as_usd, "wear_usd": wear, "revenue_usd": energy + as_usd - wear,
        "plan_id": [e.plan_id for e in entries], "model_version": [e.model_version for e in entries]}, index=idx)
    return out


class PaperLedger:
    def __init__(self, directory: Optional[Path] = None, *, clock=None, fsync: bool = True):
        self.dir = Path(directory) if directory is not None else Path(paths.LEDGER)   # read at call time (tests re-point DISPATCH_HOME)
        self.dir.mkdir(parents=True, exist_ok=True)
        self._clock = clock or (lambda: pd.Timestamp.now(tz="UTC"))
        self._fsync = fsync
        self._lock = threading.RLock()
        self._state: dict = {}                 # asset_id -> _Asset

    def _now(self) -> pd.Timestamp:
        ts = pd.Timestamp(self._clock())
        return ts.tz_localize("UTC") if ts.tzinfo is None else ts.tz_convert("UTC")

    # -- files and loading
    def path(self, asset_id: str) -> Path:
        if not _SAFE_ID.match(str(asset_id)):
            raise LedgerError("bad_asset_id", repr(asset_id))
        return self.dir / f"ledger-{asset_id}.jsonl"

    def has_asset(self, asset_id: str) -> bool:
        return self.path(asset_id).exists() and self.path(asset_id).stat().st_size > 0

    def _load(self, asset_id: str) -> "_Asset":
        path = self.path(asset_id)
        if not path.exists() or path.stat().st_size == 0:
            raise LedgerError("unknown_asset", f"{asset_id} has no ledger; call init_asset first")
        size = path.stat().st_size
        st = self._state.get(asset_id)
        if st is not None and st.size == size:
            return st
        st = _Asset.parse(path.read_bytes(), asset_id)
        st.size = size
        self._state[asset_id] = st
        return st

    def init_asset(self, spec: BatterySpec, soc_mwh: float, *, cost_basis_usd: float = 0.0) -> bool:
        """Start a ledger for an asset (writes the init line). Returns False, and changes nothing, if one already exists."""
        with self._lock:
            if self.has_asset(spec.asset_id):
                self._load(spec.asset_id)
                return False
            if not (math.isfinite(soc_mwh) and -GUARD_EPS_MWH <= soc_mwh <= spec.energy_mwh + GUARD_EPS_MWH):
                raise LedgerError("bad_initial_soc", f"{soc_mwh} MWh outside [0, {spec.energy_mwh}]")
            line = {"type": "init", "schema": SCHEMA, "asset_id": spec.asset_id, "soc_mwh": float(soc_mwh),
                    "cost_basis_usd": float(cost_basis_usd), "ts_utc": self._now()}
            self._write(spec.asset_id, line)
            self._load(spec.asset_id)
            return True

    def _write(self, asset_id: str, line: Mapping) -> None:
        data = (json.dumps(to_jsonable(line), sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n").encode()
        try:
            with open(self.path(asset_id), "ab") as f:
                f.write(data)
                f.flush()
                if self._fsync:
                    os.fsync(f.fileno())
        except OSError as exc:
            raise LedgerError("write_failed", str(exc)) from exc

    # -- SOC source (paper mode: the ledger IS the telemetry)
    def state_at(self, asset_id: str, at_utc: pd.Timestamp) -> tuple:
        """(soc_mwh, cost_basis_usd) at the START of the interval beginning at `at_utc`, assuming idle where nothing is recorded."""
        with self._lock:
            return self._load(asset_id).state_at(pd.Timestamp(at_utc))

    def soc_at(self, asset_id: str, at_utc: pd.Timestamp) -> float:
        return self.state_at(asset_id, at_utc)[0]

    def soc_reading(self, asset_id: str, at_utc: pd.Timestamp) -> tuple:
        """(soc_mwh, age_s) in the shape the guard wants. Paper SOC is computed, never measured, so its age is 0."""
        return self.soc_at(asset_id, at_utc), 0.0

    def preview(self, spec: BatterySpec, setpoint: Setpoint, ref_price_usd_per_mwh: Optional[float] = None) -> Transition:
        """What recording `setpoint` would do to SOC and exposure, without writing anything."""
        soc0, cost0 = self.state_at(spec.asset_id, setpoint.interval_start_utc)
        return reference_transition(spec, soc0, cost0, float(setpoint.net_mw), ref_price_usd_per_mwh)

    # -- record
    def record(self, spec: BatterySpec, setpoint: Setpoint, *, ref_price_usd_per_mwh: Optional[float] = None,
               audit_hash: str = "") -> LedgerEntry:
        """Execute an accepted setpoint on paper. Raises LedgerError (nothing written) if it cannot be recorded."""
        with self._lock:
            st = self._load(spec.asset_id)
            ts = pd.Timestamp(setpoint.interval_start_utc)
            if setpoint.asset_id != spec.asset_id:
                raise LedgerError("asset_mismatch", f"{setpoint.asset_id} vs {spec.asset_id}")
            if ts is pd.NaT or ts.tzinfo is None:
                raise LedgerError("bad_interval", f"{ts} is not a tz-aware timestamp")
            ts = ts.tz_convert("UTC")
            if ts != ts.floor("15min"):
                raise LedgerError("bad_interval", f"{ts} is not on the 15-minute grid")
            issued = pd.Timestamp(setpoint.issued_utc)
            if issued is pd.NaT or issued.tzinfo is None:
                raise LedgerError("bad_issue_time", f"{issued} is not a tz-aware timestamp")
            issued = issued.tz_convert("UTC")
            net = float(setpoint.net_mw)
            as_mw = {str(k): float(v) for k, v in dict(setpoint.as_mw).items()}
            last = st.entries[-1] if st.entries else None
            if last is not None:
                same = (ts == last.interval_start_utc and issued == last.issued_utc and net == last.net_mw
                        and as_mw == dict(last.as_mw) and setpoint.plan_id == last.plan_id)
                if same:
                    return last                                           # idempotent resubmission
                if ts < last.interval_start_utc:
                    raise LedgerError("stale_interval", f"{ts} is older than the newest recorded interval {last.interval_start_utc}")
                if issued <= last.issued_utc:
                    raise LedgerError("non_increasing_issue", f"issued {issued} <= last issued {last.issued_utc}")
            soc0, cost0 = st.state_at(ts)
            tr = reference_transition(spec, soc0, cost0, net, ref_price_usd_per_mwh)
            self._assert_limits(spec, net, as_mw, tr.soc_end_mwh)
            soc1 = min(max(tr.soc_end_mwh, spec.soc_min_mwh), spec.soc_hi_mwh)     # inside the guard's tolerance: snap to the bound
            entry = LedgerEntry(seq=len(st.entries), asset_id=spec.asset_id, interval_start_utc=ts, net_mw=net,
                                as_mw=as_mw, plan_id=setpoint.plan_id, model_version=setpoint.model_version,
                                issued_utc=issued, recorded_utc=self._now(), soc_start_mwh=soc0,
                                soc_end_mwh=soc1, cost_basis_end_usd=tr.cost_basis_end_usd,
                                ref_price_usd_per_mwh=ref_price_usd_per_mwh, audit_hash=audit_hash)
            line = {"type": "setpoint", "schema": SCHEMA, **dataclasses.asdict(entry)}
            self._write(spec.asset_id, line)
            st.add(entry)
            st.size = self.path(spec.asset_id).stat().st_size
            return entry

    @staticmethod
    def _assert_limits(spec: BatterySpec, net: float, as_mw: Mapping, soc_end: float) -> None:
        """The ledger's own, independent look at the limits (the guard is the first line; this is the last)."""
        if not all(math.isfinite(v) for v in [net, soc_end, *as_mw.values()]):
            raise LedgerError("limit_violation:non_finite")
        if abs(net) > spec.power_mw + GUARD_EPS_MW:
            raise LedgerError("limit_violation:power", f"|{net}| MW > {spec.power_mw} MW")
        if any(v < -GUARD_EPS_MW for v in as_mw.values()):
            raise LedgerError("limit_violation:as_negative")
        if soc_end > spec.soc_hi_mwh + GUARD_EPS_MWH or soc_end < spec.soc_min_mwh - GUARD_EPS_MWH:
            raise LedgerError("limit_violation:soc", f"end-of-interval SOC {soc_end:.6f} MWh outside "
                                                    f"[{spec.soc_min_mwh}, {spec.soc_hi_mwh}]")
        unknown = [k for k in as_mw if k not in AS_DURATION_H or k not in spec.as_products]
        if unknown:
            raise LedgerError("limit_violation:as_product", str(unknown))

    # -- read, settle, replay
    def entries(self, asset_id: str, *, start_utc=None, end_utc=None, final_only: bool = True) -> list:
        """Executed setpoints with start <= interval_start < end. final_only keeps the last line per interval."""
        with self._lock:
            st = self._load(asset_id)
            rows = st.final if final_only else st.entries
        lo = pd.Timestamp(start_utc) if start_utc is not None else None
        hi = pd.Timestamp(end_utc) if end_utc is not None else None
        return [e for e in rows if (lo is None or e.interval_start_utc >= lo) and (hi is None or e.interval_start_utc < hi)]

    def settle(self, spec: BatterySpec, prices: pd.DataFrame, *, start_utc=None, end_utc=None) -> pd.DataFrame:
        """Realised revenue of the executed setpoints. `prices` is a PriceFrame (rt_usd_per_mwh and as_<product>_usd_per_mw_h)."""
        return account_entries(spec, self.entries(spec.asset_id, start_utc=start_utc, end_utc=end_utc), prices)

    def revenue_usd(self, spec: BatterySpec, prices: pd.DataFrame, *, start_utc=None, end_utc=None) -> float:
        frame = self.settle(spec, prices, start_utc=start_utc, end_utc=end_utc)
        return float(frame["revenue_usd"].sum()) if len(frame) else 0.0

    def as_actual(self, spec: BatterySpec, *, start_utc=None, end_utc=None) -> ActualDispatch:
        """The executed setpoints in the shape D1b's attribution engine takes as "what the asset did". One row per executed
        interval (idle intervals have no row; reindex if the engine needs a full grid). `available_mw` is the full inverter
        power and `soc_mwh` is the computed end-of-interval SOC, because a paper asset has no telemetry."""
        entries = self.entries(spec.asset_id, start_utc=start_utc, end_utc=end_utc)
        idx = pd.DatetimeIndex([e.interval_start_utc for e in entries], name="interval_start_utc")
        cols = {"net_mw": [e.net_mw for e in entries]}
        for product in spec.as_products:
            cols[as_award_col(product)] = [float(e.as_mw.get(product, 0.0)) for e in entries]
        cols["available_mw"] = [spec.power_mw] * len(entries)
        cols["soc_mwh"] = [e.soc_end_mwh for e in entries]
        return ActualDispatch(asset_id=spec.asset_id, source="paper-ledger", frame=pd.DataFrame(cols, index=idx),
                              notes=("paper dispatch: SOC is computed from the setpoints, not telemetered",))

    def inventory_cost_usd(self, asset_id: str, at_utc: pd.Timestamp) -> float:
        return self.state_at(asset_id, at_utc)[1]

    def replay(self, spec: BatterySpec, prices: pd.DataFrame, *, start_utc=None, end_utc=None) -> ReplayResult:
        """Rerun a period from the FILE (not from memory): re-simulate SOC and exposure from the init line through every
        executed setpoint with the same functions the guard and the cycle use, re-check every limit, and settle at
        `prices`. Disagreement with what the file says is reported in `problems`. Gate 3 compares `revenue_usd`."""
        path = self.path(spec.asset_id)
        if not path.exists():
            raise LedgerError("unknown_asset", spec.asset_id)
        st = _Asset.parse(path.read_bytes(), spec.asset_id)
        problems: list = []
        soc, cost = st.init_soc, st.init_cost
        prev_ts, prev_issue = None, None
        replayed, max_diff = [], 0.0
        for e in st.final:
            if prev_ts is not None and e.interval_start_utc <= prev_ts:
                problems.append(f"{e.interval_start_utc}: not strictly after {prev_ts}")
            if prev_issue is not None and e.issued_utc < prev_issue:
                problems.append(f"{e.interval_start_utc}: issued before the previous setpoint")
            tr = reference_transition(spec, soc, cost, e.net_mw, e.ref_price_usd_per_mwh)
            try:
                self._assert_limits(spec, e.net_mw, dict(e.as_mw), tr.soc_end_mwh)
            except LedgerError as exc:
                problems.append(f"{e.interval_start_utc}: {exc.code}")
            diff = abs(min(max(tr.soc_end_mwh, spec.soc_min_mwh), spec.soc_hi_mwh) - e.soc_end_mwh)
            max_diff = max(max_diff, diff, abs(soc - e.soc_start_mwh))
            if diff > GUARD_EPS_MWH or abs(soc - e.soc_start_mwh) > GUARD_EPS_MWH:
                problems.append(f"{e.interval_start_utc}: recorded SOC differs from the replayed SOC by "
                                f"{max(diff, abs(soc - e.soc_start_mwh)):.6g} MWh")
            soc = min(max(tr.soc_end_mwh, spec.soc_min_mwh), spec.soc_hi_mwh)
            cost = tr.cost_basis_end_usd
            replayed.append(dataclasses.replace(e, soc_start_mwh=tr.soc_start_mwh, soc_end_mwh=soc, cost_basis_end_usd=cost))
            prev_ts, prev_issue = e.interval_start_utc, e.issued_utc
        lo = pd.Timestamp(start_utc) if start_utc is not None else None
        hi = pd.Timestamp(end_utc) if end_utc is not None else None
        window = [e for e in replayed if (lo is None or e.interval_start_utc >= lo) and (hi is None or e.interval_start_utc < hi)]
        frame = account_entries(spec, window, prices)
        revenue = float(frame["revenue_usd"].sum()) if len(frame) else 0.0
        return ReplayResult(frame=frame, revenue_usd=revenue, n_intervals=len(window), soc_max_abs_diff_mwh=max_diff,
                            problems=problems)


class _Asset:
    """Parsed content of one ledger file."""

    def __init__(self, asset_id: str, init_soc: float, init_cost: float):
        self.asset_id, self.init_soc, self.init_cost = asset_id, init_soc, init_cost
        self.entries: list = []                # every line, in file order
        self.final: list = []                  # last line per interval, in interval order
        self.size = 0

    @classmethod
    def parse(cls, data: bytes, asset_id: str) -> "_Asset":
        if data and not data.endswith(b"\n"):
            raise LedgerError("corrupt_ledger", f"{asset_id}: the file ends in the middle of a line")
        st = None
        for n, raw in enumerate(data.split(b"\n")[:-1], start=1):
            try:
                rec = json.loads(raw)
            except ValueError as exc:
                raise LedgerError("corrupt_ledger", f"{asset_id} line {n}: {exc}") from exc
            if rec.get("type") == "init":
                if st is not None:
                    raise LedgerError("corrupt_ledger", f"{asset_id} line {n}: second init")
                st = cls(asset_id, float(rec["soc_mwh"]), float(rec.get("cost_basis_usd", 0.0)))
            elif rec.get("type") == "setpoint":
                if st is None:
                    raise LedgerError("corrupt_ledger", f"{asset_id} line {n}: setpoint before init")
                st.entries.append(LedgerEntry(
                    seq=int(rec["seq"]), asset_id=rec["asset_id"], interval_start_utc=pd.Timestamp(rec["interval_start_utc"]),
                    net_mw=float(rec["net_mw"]), as_mw={k: float(v) for k, v in rec["as_mw"].items()},
                    plan_id=rec["plan_id"], model_version=rec["model_version"], issued_utc=pd.Timestamp(rec["issued_utc"]),
                    recorded_utc=pd.Timestamp(rec["recorded_utc"]), soc_start_mwh=float(rec["soc_start_mwh"]),
                    soc_end_mwh=float(rec["soc_end_mwh"]), cost_basis_end_usd=float(rec["cost_basis_end_usd"]),
                    ref_price_usd_per_mwh=rec.get("ref_price_usd_per_mwh"), audit_hash=rec.get("audit_hash", "")))
            else:
                raise LedgerError("corrupt_ledger", f"{asset_id} line {n}: unknown line type {rec.get('type')!r}")
        if st is None:
            raise LedgerError("corrupt_ledger", f"{asset_id}: no init line")
        st.rebuild()
        return st

    def rebuild(self) -> None:
        last: dict = {}
        for e in self.entries:
            last[e.interval_start_utc] = e
        self.final = [last[k] for k in sorted(last)]

    def add(self, entry: "LedgerEntry") -> None:
        """Append one line. record() only ever writes the newest interval or revises it, so `final` stays sorted."""
        self.entries.append(entry)
        if self.final and self.final[-1].interval_start_utc == entry.interval_start_utc:
            self.final[-1] = entry
        else:
            self.final.append(entry)

    def state_at(self, at_utc: pd.Timestamp) -> tuple:
        for e in reversed(self.final):
            if e.interval_start_utc < at_utc:
                return e.soc_end_mwh, e.cost_basis_end_usd
        return self.init_soc, self.init_cost
