"""Providers that turn the paper ledger, a price source and the optimizer or a backtest into the inputs of the capture-rate and
slippage checks (monitoring/runner.py). They are glue, not policy: the maths is in monitoring/checks.py.

    price_source(spec, start_utc, end_utc) -> PriceFrame   realised prices on the 15-minute grid for [start, end)
                                                            (rt_usd_per_mwh and as_<product>_usd_per_mw_h); raises if they are not there yet
    backtest_fn(spec, start_utc, end_utc) -> float          the backtest's revenue over the same period (D1b's walk-forward harness)

Both come from the data lake and D1b, so the service only builds these providers when it is given them (REQUEST in notes/d2.md).
A period whose prices are not available yet is "no data" (the provider returns None and the check is skipped), never a breach:
realised prices lag, and a lag must not halt dispatch. A ledger that does not replay cleanly IS a breach (the provider raises,
which the runner records as an error breach).

Both providers are asked every monitoring pass (a minute) but their window only moves when an interval settles (every 15 minutes), so the
two expensive parts are remembered for the window they were computed for (I1, 10 Oct 2026): the realised prices (a week read from the lake)
and the perfect-foresight solve. The ledger replay is NOT remembered: it re-reads the ledger FILE every pass, which is how an edited ledger is
noticed within a minute.
"""
from __future__ import annotations

import logging
import math
from typing import Callable, Optional, Sequence

import pandas as pd

from ..contracts import BatterySpec
from ..execution.ledger import LedgerError, PaperLedger
from .runner import CaptureInputs, SlippageInputs

log = logging.getLogger("dispatch.monitoring")

WINDOW = pd.Timedelta(days=7)          # ASSUMPTION: capture rate and slippage are judged over the last seven days
PRICE_LAG = pd.Timedelta(hours=1)      # ASSUMPTION: realised prices (and AS prices, which are revised) are final this long after an interval
STEP = pd.Timedelta(minutes=15)


def period(now_utc: pd.Timestamp, span: pd.Timedelta = WINDOW, lag: pd.Timedelta = PRICE_LAG) -> tuple:
    """[start, end) of the window judged at `now_utc`: it ends `lag` ago, on the 15-minute grid."""
    end = (pd.Timestamp(now_utc).tz_convert("UTC") - lag).floor("15min")
    return end - span, end


def _prices(price_source: Callable, spec: BatterySpec, start: pd.Timestamp, end: pd.Timestamp) -> Optional[pd.DataFrame]:
    try:
        return price_source(spec, start, end)
    except Exception as exc:
        log.info("no realised prices for %s [%s, %s): %s", spec.asset_id, start, end, exc)
        return None


class _Memo:
    """A few recent answers keyed by (asset, window[, state]); small on purpose, a week of prices is a few hundred kilobytes."""

    def __init__(self, keep: int = 4):
        self.keep, self.items = keep, {}

    def get(self, key, make):
        if key in self.items:
            return self.items[key]
        value = make()
        if value is not None:
            while len(self.items) >= self.keep:
                self.items.pop(next(iter(self.items)))
            self.items[key] = value
        return value


def capture_provider(ledger: PaperLedger, specs: Sequence[BatterySpec], price_source: Callable, solve: Callable, *,
                     baseline: Optional[float] = None, span: pd.Timedelta = WINDOW,
                     lag: pd.Timedelta = PRICE_LAG) -> Callable[[pd.Timestamp], Optional[CaptureInputs]]:
    """Rolling capture rate = paper revenue / perfect-foresight revenue over the window, summed over the assets.
    Perfect foresight is the same LP in mode="pf" on the realised prices, started from the paper SOC at the window start.
    `baseline` is the capture rate the decay check compares against (the PRD sets the target after the first baseline run);
    without it the rate is exported and the check is skipped."""
    prices_memo, pf_memo = _Memo(), _Memo()

    def provider(now_utc):
        start, end = period(now_utc, span, lag)
        paper = pf = 0.0
        used = 0
        for spec in specs:
            if not ledger.has_asset(spec.asset_id) or not ledger.entries(spec.asset_id, start_utc=start, end_utc=end):
                continue
            prices = prices_memo.get((spec.asset_id, start, end), lambda: _prices(price_source, spec, start, end))
            if prices is None:
                return None
            try:
                paper += ledger.revenue_usd(spec, prices, start_utc=start, end_utc=end)
            except LedgerError as exc:
                log.info("capture: %s", exc)
                return None
            soc0 = ledger.soc_at(spec.asset_id, start)

            def solve_pf():
                plan = solve(spec, prices.loc[start:end - STEP], soc0, mode="pf")
                if not plan.ok:
                    raise RuntimeError(f"perfect-foresight solve for {spec.asset_id} ended {plan.status!r}")
                return float(plan.objective_usd)
            pf += pf_memo.get((spec.asset_id, start, end, round(float(soc0), 6)), solve_pf)
            used += 1
        return CaptureInputs(paper, pf, math.nan if baseline is None else float(baseline)) if used else None
    return provider


def slippage_provider(ledger: PaperLedger, specs: Sequence[BatterySpec], price_source: Callable, backtest_fn: Callable, *,
                      span: pd.Timedelta = WINDOW, lag: pd.Timedelta = PRICE_LAG) -> Callable[[pd.Timestamp], Optional[SlippageInputs]]:
    """Gate 3 as a rolling check: the ledger FILE replayed over the window at realised prices, against the backtest's revenue
    for the same window. A ledger that does not replay cleanly raises (an error breach)."""
    prices_memo = _Memo()

    def provider(now_utc):
        start, end = period(now_utc, span, lag)
        paper = backtest = 0.0
        used = 0
        for spec in specs:
            if not ledger.has_asset(spec.asset_id) or not ledger.entries(spec.asset_id, start_utc=start, end_utc=end):
                continue
            prices = prices_memo.get((spec.asset_id, start, end), lambda: _prices(price_source, spec, start, end))
            if prices is None:
                return None
            try:
                rep = ledger.replay(spec, prices, start_utc=start, end_utc=end)
            except LedgerError as exc:
                log.info("slippage: %s", exc)
                return None
            if not rep.ok:
                raise RuntimeError(f"the paper ledger for {spec.asset_id} does not replay cleanly: {rep.problems[:3]}")
            try:
                bt = float(backtest_fn(spec, start, end))
            except LookupError as exc:                           # e.g. no model loaded yet: no data, not a breach
                log.info("slippage: no backtest for %s: %s", spec.asset_id, exc)
                return None
            paper += rep.revenue_usd
            backtest += bt
            used += 1
        return SlippageInputs(paper, backtest) if used else None
    return provider
