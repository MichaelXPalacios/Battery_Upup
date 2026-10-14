"""The Gate 3 backtest: what the optimizer earns over [start, end) when it re-plans at every interval start.

    backtest_fn(spec, start_utc, end_utc) -> revenue_usd        the signature `monitoring.providers.slippage_provider` calls
                                                                (the service passes it as `create_app(backtest_fn=...)`)

Gate 3 compares the paper ledger's revenue over a window with this number. It is `rolling.run_shadow(step_intervals=1)`: a
decision at the start of every interval, a point-in-time forecast, `lp.solve(mode="forecast")`, only the first interval of each
plan executed, the dispatch valued on the REALISED prices with the one accounting function (`lp.account`). The paper cycle makes
the same decision from the same inputs, so when the guard changes nothing the two agree to the cent (D2's test_monitoring checks
exactly that against `run_shadow`).

Wiring (the same ledger object feeds the cycle, the SOC source and the backtest, so the two start from the same state):

    ledger = PaperLedger()
    bt = Backtest(price_source, forecast_fn, ledger=ledger)
    app = create_app(ledger=ledger, price_source=price_source, forecast_fn=forecast_fn, backtest_fn=bt)

With `ledger=` the starting SOC is the ledger's at the window start (`ledger_soc0`) and a window that begins before the ledger's
first executed interval is clipped to that interval: the monitor asks for the last seven days from the first day of paper
trading, when the paper side has two hours of history, and comparing two hours of paper against seven days of backtest would
halt a healthy service. After the clip, intervals with no ledger entry INSIDE the window (an outage, a setpoint the guard
rejected) still count: the backtest decides there and paper did not, which is slippage by design (a mask that ignored
unrecorded intervals would hide exactly the guard interference Gate 3 exists to find).

What it needs from the caller
  price_source(spec, start, end) -> PriceFrame   realised prices on the 15-minute grid (rt_usd_per_mwh, as_<product>_usd_per_mw_h)
  forecast_fn(issued_utc) -> ForecastFrame       POINT-IN-TIME: its answer for an issue time must not depend on anything published
                                                 later (D1a's registry forecaster over D0's point-in-time features, or the local
                                                 `rolling.SeasonalNaiveForecaster`). A mapping {asset_id: forecast_fn} serves a
                                                 fleet. A backtest on a forecaster that peeks is not a backtest.
  soc0(spec, start_utc) -> MWh                   the state of charge at the window start; `ledger=` (or `ledger_soc0(ledger)`) reads
                                                 the paper ledger's. The default (mid-window) is an ASSUMPTION for offline use only.

Cost and the memo. A seven-day window is 672 decisions, 672 forecasts and 672 LP solves (about a minute and a half with the
seasonal-naive forecaster on a laptop core, see notes/d1b.md), and the monitor asks every minute for a window that only moves
every 15. Each decision is a pure function of (asset, interval start, state of charge at that start, forecast version, solver
options), so the backtest remembers the first interval of every solved plan under that key (only plans that solved, never a
failure). The next window starts one interval later from the paper SOC; when paper and backtest agree, every decision but the
newest is a lookup and the pass costs one solve. When the paper SOC differs (the guard interfered, a model swap, a manual
override) the keys miss and the chain is recomputed from the new state, which is the correct answer. Two states within
0.5 Wh share a key (`SOC_KEY_DECIMALS`), a revenue effect of well under a cent a step. Realised prices are not part of the key
(a decision never sees them) and are re-read on every call, so revised prices change the valuation but not the decisions.

Failure handling matches the cycle: a forecast or solve that fails idles that interval (no charge, no discharge, no award) and
is counted in `last["failures"]`. The paper cycle idles on the same failures, so a shared data gap cancels out and a gap on one
side only shows up as slippage, which is what the check is for. Missing or NaN realised prices, an unaligned window, or (when
`max_failed_share` is set) too many failed decisions raise `BacktestError`; the monitor records a raise as an error breach, a
doubt, never as a slippage number.
"""
from __future__ import annotations

import collections
import hashlib
import logging
import threading
import time
from dataclasses import dataclass
from typing import Callable, Mapping, Optional, Union

import numpy as np
import pandas as pd

from ..contracts import BatterySpec, RT, as_award_col
from . import rolling

log = logging.getLogger("dispatch.optimizer.backtest")

STEP = rolling.STEP
SOC_KEY_DECIMALS = 6           # ASSUMPTION: states of charge within 0.5 Wh are the same state for the memo
DEFAULT_MEMO_ENTRIES = 200_000  # about 300 windows of one asset; an entry is a few floats
SCALAR_OPTIONS = ("allow_as", "terminal_soc_mwh", "charge_limit_mw", "availability_lookahead")
SERIES_OPTIONS = ("availability", "charge_availability", "soc_min_series", "soc_max_series")


class BacktestError(RuntimeError):
    """The backtest cannot be trusted for this window: realised prices missing, an unaligned window, or too many failed decisions."""


def ledger_soc0(ledger) -> Callable[[BatterySpec, pd.Timestamp], float]:
    """soc0 source for the paper service: the SOC the paper ledger holds at the start of the window."""
    def soc0(spec: BatterySpec, start_utc: pd.Timestamp) -> float:
        return float(ledger.soc_at(spec.asset_id, pd.Timestamp(start_utc)))
    return soc0


def frame_price_source(realised: pd.DataFrame) -> Callable:
    """price_source over a frame already in memory (offline use and tests): rows with start <= interval < end."""
    def source(spec: BatterySpec, start_utc: pd.Timestamp, end_utc: pd.Timestamp) -> pd.DataFrame:
        i = realised.index
        return realised.loc[(i >= pd.Timestamp(start_utc)) & (i < pd.Timestamp(end_utc))]
    return source


@dataclass(frozen=True)
class _Step:
    """The first interval of a solved plan: what the memo stores."""
    charge_mw: float
    discharge_mw: float
    awards: tuple                 # MW, in spec.as_products order
    soc_end_mwh: float
    forecast_version: str


def _utc(ts, name: str) -> pd.Timestamp:
    t = pd.Timestamp(ts)
    t = t.tz_localize("UTC") if t.tzinfo is None else t.tz_convert("UTC")
    if t != t.floor("15min"):
        raise ValueError(f"{name} {t} is not on the 15-minute grid")
    return t


class Backtest:
    """Callable `backtest_fn(spec, start_utc, end_utc) -> revenue_usd` (see the module docstring).

    The memo belongs to the instance and assumes `forecast_fn` (and its `version`, if it has one) is a function of the issue time
    alone; swap the forecaster or its data in place and call `clear()`. `soc0_mwh=` on a call overrides the `soc0` source.
    `ledger` is duck-typed: `soc_at(asset_id, ts)` and `entries(asset_id, start_utc=, end_utc=)` (D2's `PaperLedger`)."""

    def __init__(self, price_source: Callable, forecast_fn: Union[Callable, Mapping], *, ledger=None, clip_to_ledger: bool = True,
                 soc0: Optional[Callable[[BatterySpec, pd.Timestamp], float]] = None, memo: bool = True,
                 memo_entries: int = DEFAULT_MEMO_ENTRIES, max_failed_share: Optional[float] = None, **shadow_options):
        unknown = set(shadow_options) - set(SCALAR_OPTIONS) - set(SERIES_OPTIONS)
        if unknown:
            raise TypeError(f"unknown run_shadow options {sorted(unknown)}")
        self.price_source = price_source
        self.forecast_fn = forecast_fn
        self.ledger = ledger
        self.clip_to_ledger = bool(clip_to_ledger)
        self.soc0 = soc0 if soc0 is not None or ledger is None else ledger_soc0(ledger)
        self.max_failed_share = max_failed_share
        self.options = dict(shadow_options)
        # series options (derates) need the whole window in one run_shadow call for their lookahead, so those runs skip the memo
        self._series = any(self.options.get(k) is not None for k in SERIES_OPTIONS)
        self._memo_on = bool(memo) and not self._series
        self._options_sig = repr(sorted((k, self.options.get(k)) for k in SCALAR_OPTIONS))
        self._memo: "collections.OrderedDict[tuple, _Step]" = collections.OrderedDict()
        self._memo_entries = int(memo_entries)
        self._lock = threading.Lock()
        self.last: dict = {}

    # ---- the create_app(backtest_fn=...) signature
    def __call__(self, spec: BatterySpec, start_utc, end_utc, *, soc0_mwh: Optional[float] = None) -> float:
        return float(self.run(spec, start_utc, end_utc, soc0_mwh=soc0_mwh).revenue_usd)

    def clear(self) -> None:
        with self._lock:
            self._memo.clear()

    @property
    def memo_size(self) -> int:
        return len(self._memo)

    # ---- helpers
    def _forecast_for(self, spec: BatterySpec) -> Callable:
        f = self.forecast_fn
        if isinstance(f, Mapping):
            if spec.asset_id not in f:
                raise BacktestError(f"no forecast function for asset {spec.asset_id!r} (have {sorted(f)})")
            return f[spec.asset_id]
        return f

    def _clip_to_ledger(self, spec: BatterySpec, start: pd.Timestamp, end: pd.Timestamp) -> pd.Timestamp:
        """The later of `start` and the first interval the paper ledger executed in [start, end)."""
        try:
            rows = self.ledger.entries(spec.asset_id, start_utc=start, end_utc=end)
        except Exception as exc:
            raise BacktestError(f"cannot read the paper ledger for {spec.asset_id}: {type(exc).__name__}: {exc}") from exc
        if not rows:
            raise BacktestError(f"the paper ledger has no executed interval for {spec.asset_id} in [{start}, {end})")
        return max(start, pd.Timestamp(rows[0].interval_start_utc).tz_convert("UTC"))

    def _start_soc(self, spec: BatterySpec, start: pd.Timestamp, soc0_mwh: Optional[float]) -> float:
        if soc0_mwh is None and self.soc0 is not None:
            soc0_mwh = self.soc0(spec, start)
        if soc0_mwh is None:                                  # ASSUMPTION: offline default, the middle of the usable window
            soc0_mwh = 0.5 * (spec.soc_min_mwh + spec.soc_hi_mwh)
        soc0_mwh = float(soc0_mwh)
        if not np.isfinite(soc0_mwh) or not spec.soc_min_mwh - 1e-6 <= soc0_mwh <= spec.soc_hi_mwh + 1e-6:
            raise BacktestError(f"starting SOC {soc0_mwh} MWh is outside [{spec.soc_min_mwh}, {spec.soc_hi_mwh}]")
        return min(max(soc0_mwh, spec.soc_min_mwh), spec.soc_hi_mwh)         # float dust from the ledger arithmetic is clipped

    def _prices(self, spec: BatterySpec, start: pd.Timestamp, end: pd.Timestamp) -> tuple:
        realised = self.price_source(spec, start, end)
        if realised is None or len(realised) == 0 or RT not in realised.columns:
            raise BacktestError(f"no realised prices ({RT!r}) for {spec.asset_id} in [{start}, {end})")
        i = realised.index
        realised = realised.set_axis(i.tz_localize("UTC") if i.tz is None else i.tz_convert("UTC"), axis=0)
        realised = realised[~realised.index.duplicated(keep="last")]
        idx = pd.date_range(start, end, freq=STEP, inclusive="left", name="interval_start_utc")
        missing = idx.difference(realised.index)
        if len(missing):
            raise BacktestError(f"realised prices are missing for {len(missing)} of {len(idx)} intervals of {spec.asset_id}, "
                                f"first {missing[0]}")
        if realised.loc[idx, RT].isna().any():
            raise BacktestError(f"realised {RT} has NaN in [{start}, {end}) for {spec.asset_id}")
        return realised, idx

    def _key(self, spec_sig: str, fingerprint, ts: pd.Timestamp, soc: float) -> tuple:
        return spec_sig, self._options_sig, fingerprint, ts.value, round(soc, SOC_KEY_DECIMALS)

    def _get(self, key: tuple) -> Optional[_Step]:
        with self._lock:
            step = self._memo.get(key)
            if step is not None:
                self._memo.move_to_end(key)
            return step

    def _put(self, key: tuple, step: _Step) -> None:
        with self._lock:
            self._memo[key] = step
            self._memo.move_to_end(key)
            while len(self._memo) > self._memo_entries:
                self._memo.popitem(last=False)

    # ---- the run
    def run(self, spec: BatterySpec, start_utc, end_utc, *, soc0_mwh: Optional[float] = None) -> rolling.RollingResult:
        """Receding-horizon dispatch over [start_utc, end_utc) with a decision at every interval start, valued on realised prices.
        Returns the same `RollingResult` as `run_shadow` (per-interval frame, solve counts, failures); `last` keeps the statistics."""
        t_all = time.perf_counter()
        start, end = _utc(start_utc, "start_utc"), _utc(end_utc, "end_utc")
        if end <= start:
            raise ValueError(f"empty window [{start}, {end})")
        forecast_fn = self._forecast_for(spec)
        if self.ledger is not None and self.clip_to_ledger:
            start = self._clip_to_ledger(spec, start, end)
        realised, idx = self._prices(spec, start, end)
        soc0 = self._start_soc(spec, start, soc0_mwh)
        if self._series:
            res = rolling.run_shadow(spec, realised, forecast_fn, soc0_mwh=soc0, start_utc=start, end_utc=end,
                                     step_intervals=1, **self.options)
        else:
            res = self._chain(spec, forecast_fn, realised, idx, soc0)
        n = len(idx)
        share = res.n_failed / n
        self.last = {"asset_id": spec.asset_id, "start_utc": start, "end_utc": end, "intervals": n, "soc0_mwh": soc0,
                     "soc_end_mwh": res.soc_end_mwh, "solved": res.n_solves, "memo_hits": res.n_cached,
                     "failed": res.n_failed, "failures": list(res.failures[:20]), "forecast_version": res.forecast_version,
                     "revenue_usd": res.revenue_usd, "seconds": time.perf_counter() - t_all, "solve_seconds": res.solve_s,
                     "memo_size": len(self._memo)}
        if res.n_failed:
            log.warning("backtest %s [%s, %s): %d of %d decisions failed closed (first: %s)", spec.asset_id, start, end,
                        res.n_failed, n, res.failures[0] if res.failures else "?")
        log.log(logging.INFO if res.n_solves > 20 else logging.DEBUG,         # the monitor asks every minute; say so only when work was done
                "backtest %s [%s, %s): $%.2f, %d solved, %d from the memo, %.1f s", spec.asset_id, start, end,
                res.revenue_usd, res.n_solves, res.n_cached, self.last["seconds"])
        if self.max_failed_share is not None and share > self.max_failed_share:
            raise BacktestError(f"{res.n_failed} of {n} backtest decisions failed ({share:.0%} > {self.max_failed_share:.0%}); "
                                f"first: {res.failures[0] if res.failures else 'unknown'}")
        return res

    def _chain(self, spec: BatterySpec, forecast_fn: Callable, realised: pd.DataFrame, idx: pd.DatetimeIndex,
               soc0: float) -> rolling.RollingResult:
        """One `run_shadow(step_intervals=1)` call per missed decision, the SOC carried from one to the next exactly as
        `run_shadow` carries it, so the chain equals one run over the window (tests/test_backtest.py)."""
        n, products = len(idx), list(spec.as_products)
        c_out, d_out, soc_out = np.zeros(n), np.zeros(n), np.zeros(n)
        a_out = {p: np.zeros(n) for p in products}
        ok_out = np.zeros(n, bool)
        spec_sig = hashlib.sha1(repr(spec).encode()).hexdigest()[:16]
        fingerprint = getattr(forecast_fn, "version", None) or id(forecast_fn)       # unversioned forecasters key by identity
        res = rolling.RollingResult(frame=pd.DataFrame())
        soc = soc0
        for k, ts in enumerate(idx):
            step = self._get(self._key(spec_sig, fingerprint, ts, soc)) if self._memo_on else None
            if step is None:
                r = rolling.run_shadow(spec, realised, forecast_fn, soc0_mwh=soc, start_utc=ts, end_utc=ts + STEP,
                                       step_intervals=1, **self.options)
                row = r.frame.iloc[0]
                res.n_solves += r.n_solves
                res.solve_s += r.solve_s
                res.failures.extend(r.failures)
                res.forecast_version = r.forecast_version or res.forecast_version
                step = _Step(float(row["charge_mw"]), float(row["discharge_mw"]),
                             tuple(float(row[as_award_col(p)]) for p in products), float(row["soc_end_mwh"]),
                             r.forecast_version)
                ok = bool(row["plan_ok"])
                if ok and self._memo_on:
                    self._put(self._key(spec_sig, fingerprint, ts, soc), step)
            else:
                ok = True
                res.n_cached += 1
                res.forecast_version = step.forecast_version or res.forecast_version
            if not ok:
                res.n_failed += 1
            c_out[k], d_out[k], soc_out[k], ok_out[k] = step.charge_mw, step.discharge_mw, step.soc_end_mwh, ok
            for p, a in zip(products, step.awards):
                a_out[p][k] = a
            soc = step.soc_end_mwh
        decided = idx.tz_convert("UTC").tz_localize(None).to_numpy(dtype="datetime64[ns]")
        res.frame = rolling.assemble_frame(spec, realised, idx, c_out, d_out, a_out, soc_out, ok_out, decided)
        res.soc_end_mwh = soc
        return res


def make_backtest_fn(price_source: Callable, forecast_fn: Union[Callable, Mapping], **kw) -> Backtest:
    """`Backtest(price_source, forecast_fn, ...)`: the callable to pass as `create_app(backtest_fn=...)`."""
    return Backtest(price_source, forecast_fn, **kw)
