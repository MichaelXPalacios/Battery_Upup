"""The shadow optimizer (PRD wedge): the dispatch our code would have run, replayed on history.

At each decision time the optimizer asks an injected forecaster for a price forecast over the horizon, solves the LP
(`lp.solve(mode="forecast")`, the same code path as perfect foresight), executes only the first `step_intervals`
intervals, updates the state of charge, and repeats. The executed dispatch is then valued on the REALISED prices with the
same accounting (`lp.account`), interval by interval. Nothing here looks at a price that was not public at the decision
time: the forecaster is handed only `issued_utc`, and the default one enforces its own point-in-time rule.

Forecasters follow ``forecast_fn(issued_utc) -> ForecastFrame`` (the frame starts at the first interval to be dispatched).
The default `SeasonalNaiveForecaster` is local and has no model dependency; `registry_forecast_fn` wraps D1a's registry
(`dispatch.registry.mlflow_registry.load(alias)`).
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Callable, Optional

import numpy as np
import pandas as pd

from ..contracts import BatterySpec, ForecastFrame, QUANTILES, RT, as_award_col, as_price_col
from . import lp

STEP = pd.Timedelta(minutes=15)
PER_DAY = 96


# ------------------------------------------------------------------------------------------------ forecasters
def _row_quantile(a: np.ndarray, q: float) -> np.ndarray:
    """NaN-ignoring linear-interpolation quantile along axis 1 (same as numpy's default, but vectorised: `nanquantile`
    loops over rows in Python and cost 300 ms per forecast)."""
    srt = np.sort(a, axis=1)                                      # NaN sorts last
    n = np.sum(~np.isnan(a), axis=1)
    pos = q * np.maximum(n - 1, 0)
    lo = np.floor(pos).astype(int)
    hi = np.ceil(pos).astype(int)
    rows = np.arange(a.shape[0])
    last = a.shape[1] - 1
    vlo, vhi = srt[rows, np.clip(lo, 0, last)], srt[rows, np.clip(hi, 0, last)]
    out = vlo + (vhi - vlo) * (pos - lo)
    out[n == 0] = np.nan
    return out


class SeasonalNaiveForecaster:
    """Same time of day on the most recent day that was already published at the decision time.

    For a target interval tau and decision time t0, the median forecast is the realised price at tau - m days, with m
    the smallest integer >= 1 such that that interval had ended and been published (interval end + `known_lag_min`) by
    t0; if the value is missing the next older day is tried, up to `max_lags` days, then persistence of the last known
    price. p10 and p90 are the empirical 10th and 90th percentiles of the same time of day over the `n_quantile_days`
    days up to that one. Applies to the real-time price and to every ancillary column present in the history.

    history   PriceFrame on the 15-minute UTC grid holding the realised prices (it may extend past the decision time:
              only values published before `issued_utc` are ever read).
    """

    def __init__(self, history: pd.DataFrame, *, settlement_point: str = "", horizon_intervals: int = 192,
                 season_days: int = 1, known_lag_min: int = 20, n_quantile_days: int = 7, max_lags: int = 21,
                 version: Optional[str] = None):
        if RT not in history.columns:
            raise ValueError(f"history needs a '{RT}' column")
        idx = history.index
        if not isinstance(idx, pd.DatetimeIndex) or idx.tz is None:
            raise ValueError("history must have a tz-aware DatetimeIndex")
        idx = idx.tz_convert("UTC")
        self.t0 = idx[0]
        n = int((idx[-1] - idx[0]) / STEP) + 1
        dense = history.set_axis(idx, axis=0)
        dense = dense[~dense.index.duplicated(keep="last")].reindex(pd.date_range(idx[0], periods=n, freq="15min"))
        self.series = {RT: dense[RT].to_numpy(float)}
        for c in dense.columns:
            if c.startswith("as_") and c.endswith("_usd_per_mw_h"):
                self.series[c] = dense[c].to_numpy(float)
        self.settlement_point = settlement_point
        self.horizon = int(horizon_intervals)
        self.season = PER_DAY * int(season_days)
        self.lag = int(np.ceil(known_lag_min / 15.0))
        self.known_lag_min = known_lag_min
        self.nq = int(n_quantile_days)
        self.max_lags = int(max_lags)
        self.version = version or f"seasonal-naive-{season_days}d"

    def _pos(self, ts: pd.Timestamp) -> int:
        return int(round((ts - self.t0) / STEP))

    def __call__(self, issued_utc: pd.Timestamp) -> ForecastFrame:
        issued = pd.Timestamp(issued_utc)
        issued = issued.tz_localize("UTC") if issued.tzinfo is None else issued.tz_convert("UTC")
        first = issued.ceil("15min")
        j0 = self._pos(first)
        p_issued = self._pos(issued.floor("15min")) if first != issued else j0
        j = np.arange(j0, j0 + self.horizon)
        S, L = self.season, self.lag
        # known when position q satisfies q + 1 + L <= p_issued (interval ended and published)
        m = np.maximum(1, np.ceil((j + 1 + L - p_issued) / S).astype(int))
        out, max_used = {}, -1
        for name, arr in self.series.items():
            n = len(arr)

            def take(pos):
                ok = (pos >= 0) & (pos < n)
                v = np.full(pos.shape, np.nan)
                v[ok] = arr[pos[ok]]
                return v

            p50 = np.full(len(j), np.nan)
            for extra in range(self.max_lags):
                miss = np.isnan(p50)
                if not miss.any():
                    break
                pos = j - (m + extra) * S
                v = take(pos)
                fill = miss & ~np.isnan(v)
                p50[fill] = v[fill]
                if fill.any():
                    max_used = max(max_used, int(pos[fill].max()))
            if np.isnan(p50).any():                                   # persistence of the last known value
                last = arr[: max(p_issued - L, 0)]
                last = last[~np.isnan(last)]
                p50[np.isnan(p50)] = last[-1] if len(last) else 0.0
            cols = np.stack([take(j - (m + i) * S) for i in range(self.nq)], axis=1)
            lo = _row_quantile(cols, 0.1)
            hi = _row_quantile(cols, 0.9)
            lo = np.where(np.isnan(lo), p50, np.minimum(lo, p50))
            hi = np.where(np.isnan(hi), p50, np.maximum(hi, p50))
            series = "rt" if name == RT else name[: -len("_usd_per_mw_h")]
            out[f"{series}_{QUANTILES[0]}"], out[f"{series}_{QUANTILES[1]}"], out[f"{series}_{QUANTILES[2]}"] = lo, p50, hi
        index = pd.date_range(first, periods=self.horizon, freq="15min", name="interval_start_utc")
        published = self.t0 + (max_used + 1) * STEP + pd.Timedelta(minutes=self.known_lag_min) \
            if max_used >= 0 else issued
        return ForecastFrame(issued_utc=issued, settlement_point=self.settlement_point, model_version=self.version,
                             frame=pd.DataFrame(out, index=index), max_feature_published_utc=min(published, issued))


def perfect_forecast_fn(realised: pd.DataFrame, horizon_intervals: int = 192, version: str = "perfect-foresight"):
    """Test double: returns the realised prices as a (degenerate) forecast. Only for checking that forecast mode with
    a perfect forecast equals perfect foresight - it leaks the future on purpose."""
    def fn(issued_utc):
        issued = pd.Timestamp(issued_utc).ceil("15min")
        win = realised.loc[issued: issued + (horizon_intervals - 1) * STEP]
        cols = {}
        for c in win.columns:
            if c == RT:
                s = "rt"
            elif c.startswith("as_") and c.endswith("_usd_per_mw_h"):
                s = c[: -len("_usd_per_mw_h")]
            else:
                continue
            for q in QUANTILES:
                cols[f"{s}_{q}"] = win[c].to_numpy(float)
        return ForecastFrame(issued, "", version, pd.DataFrame(cols, index=win.index), issued)
    return fn


def registry_forecast_fn(alias: str, features_fn: Callable[[pd.Timestamp], pd.DataFrame],
                         settlement_point: Optional[str] = None):
    """Forecaster backed by D1a's registry: `dispatch.registry.mlflow_registry.load(alias)` returns a Forecaster
    (`.version`, `.predict(features_df, issued_utc, *, settlement_point=None) -> ForecastFrame`). `features_fn(issued_utc)`
    builds the point-in-time features (D0's `features.build.point_in_time`). The returned frame must start at the
    interval being decided (`issued_utc`); `run_shadow` fails closed (idles) when it does not. Raises `NoModelError` when
    the alias is unset (fail closed)."""
    from ..registry import mlflow_registry          # lazy: importing mlflow takes seconds
    model = mlflow_registry.load(alias)

    def fn(issued_utc):
        issued = pd.Timestamp(issued_utc)
        kw = {} if settlement_point is None else {"settlement_point": settlement_point}
        return model.predict(features_fn(issued), issued, **kw)
    fn.version = getattr(model, "version", alias)
    return fn


# ------------------------------------------------------------------------------------------------ rolling run
@dataclass
class RollingResult:
    frame: pd.DataFrame                    # executed dispatch and realised revenue per interval
    n_solves: int = 0
    n_failed: int = 0
    solve_s: float = 0.0
    soc_end_mwh: float = float("nan")
    failures: list = field(default_factory=list)
    forecast_version: str = ""
    n_cached: int = 0                      # decisions served from the backtest's memo instead of solved (0 in run_shadow)

    @property
    def revenue_usd(self) -> float:
        return float(self.frame["revenue_usd"].sum())


def run_shadow(spec: BatterySpec, realised: pd.DataFrame, forecast_fn: Callable[[pd.Timestamp], ForecastFrame], *,
               soc0_mwh: float, start_utc=None, end_utc=None, step_intervals: int = 4,
               availability: Optional[pd.Series] = None, availability_lookahead: bool = True,
               charge_limit_mw: Optional[float] = None, allow_as: bool = True,
               terminal_soc_mwh: Optional[float] = None, progress: Optional[Callable[[int, int], None]] = None,
               charge_availability: Optional[pd.Series] = None, soc_min_series: Optional[pd.Series] = None,
               soc_max_series: Optional[pd.Series] = None) -> RollingResult:
    """Receding-horizon dispatch over realised[start_utc:end_utc) (end exclusive).

    step_intervals        how many intervals of each plan are executed before re-planning (1 = every 15 minutes,
                          4 = hourly). The forecaster sees only `issued_utc` = the first interval to be executed.
    availability          fraction series of power_mw (None = full). With `availability_lookahead` False, the plan
                          assumes the value at the decision time persists over the horizon (derates are not foreseen).
    charge_availability, soc_min_series, soc_max_series   the other derates `lp.solve` accepts, treated the same way.
    A solve that is not optimal, or a forecast that does not start at the decision time, fails closed: the battery idles
    for that step (no charge, no discharge, no award) and the failure is recorded.
    """
    idx_all = realised.index
    start = idx_all[0] if start_utc is None else pd.Timestamp(start_utc)
    end = idx_all[-1] + STEP if end_utc is None else pd.Timestamp(end_utc)
    idx = idx_all[(idx_all >= start) & (idx_all < end)]
    n = len(idx)
    if RT not in realised.columns:                         # fail before the first solve, not after the last
        raise KeyError(f"realised prices need a {RT!r} column")
    avail = None if availability is None else availability.reindex(idx).fillna(1.0)
    avail_c = None if charge_availability is None else charge_availability.reindex(idx).fillna(1.0)
    smin = None if soc_min_series is None else soc_min_series.reindex(idx)
    smax = None if soc_max_series is None else soc_max_series.reindex(idx)
    products = list(spec.as_products)
    c_out, d_out = np.zeros(n), np.zeros(n)
    a_out = {k: np.zeros(n) for k in products}
    soc_out = np.zeros(n)
    ok_out = np.zeros(n, bool)
    decided = np.empty(n, dtype="datetime64[ns]")
    soc = float(soc0_mwh)
    res = RollingResult(frame=pd.DataFrame())
    k = 0
    while k < n:
        m = min(int(step_intervals), n - k)
        issued = idx[k]
        t0 = time.perf_counter()
        plan = None
        try:
            ff = forecast_fn(issued)
            view = ff.price_view("p50")
            res.forecast_version = ff.model_version
            if len(view) == 0 or view.index[0] != issued:
                raise ValueError(f"forecast starts at {None if len(view) == 0 else view.index[0]}, expected {issued}")
            def horizon(series, default):
                if series is None:
                    return None
                if availability_lookahead:
                    return series.reindex(view.index).fillna(default)
                return pd.Series(float(series.iloc[k]) if not np.isnan(series.iloc[k]) else default, index=view.index)

            av, av_c = horizon(avail, 1.0), horizon(avail_c, 1.0)
            lo_h = horizon(smin, spec.soc_min_mwh)           # derates are not foreseen when lookahead is off
            hi_h = horizon(smax, spec.soc_hi_mwh)
            if lo_h is not None:
                lo_h = lo_h.fillna(spec.soc_min_mwh)
            if hi_h is not None:
                hi_h = hi_h.fillna(spec.soc_hi_mwh)
            if lo_h is not None:                              # a tightened SOC limit forces the state inside it
                soc = max(soc, float(lo_h.iloc[0]))
            if hi_h is not None:
                soc = min(soc, float(hi_h.iloc[0]))
            plan = lp.solve(spec, view, soc, mode="forecast", allow_as=allow_as, availability=av,
                            charge_availability=av_c, terminal_soc_mwh=terminal_soc_mwh,
                            charge_limit_mw=charge_limit_mw, soc_min_series=lo_h, soc_max_series=hi_h)
        except Exception as e:                                         # fail closed: idle this step, keep the reason
            res.failures.append((issued, f"{type(e).__name__}: {e}"))
        res.solve_s += time.perf_counter() - t0
        res.n_solves += 1
        decided[k:k + m] = np.datetime64(issued.tz_convert("UTC").tz_localize(None), "ns")
        if plan is not None and plan.ok:
            f = plan.frame.iloc[:m]
            c_out[k:k + m] = f["charge_mw"].to_numpy()
            d_out[k:k + m] = f["discharge_mw"].to_numpy()
            for p in products:
                a_out[p][k:k + m] = f[as_award_col(p)].to_numpy()
            soc_out[k:k + m] = f["soc_end_mwh"].to_numpy()
            soc = float(f["soc_end_mwh"].iloc[-1])
            ok_out[k:k + m] = True
        else:
            if plan is not None:
                res.failures.append((issued, f"plan status {plan.status}"))
            res.n_failed += 1
            soc_out[k:k + m] = soc
        k += m
        if progress is not None:
            progress(k, n)
    res.frame = assemble_frame(spec, realised, idx, c_out, d_out, a_out, soc_out, ok_out, decided)
    res.soc_end_mwh = soc
    return res


def assemble_frame(spec: BatterySpec, realised: pd.DataFrame, idx: pd.DatetimeIndex, charge_mw, discharge_mw, awards: dict,
                   soc_end_mwh, plan_ok, decided_ns) -> pd.DataFrame:
    """The executed dispatch valued on the REALISED prices, interval by interval, with the one accounting function
    (`lp.account`). `run_shadow` and the Gate 3 backtest (`backtest.Backtest`) both end here, so the two cannot value the
    same actions differently. `decided_ns` is the decision (issue) time of each interval as datetime64[ns] (naive UTC)."""
    products = list(spec.as_products)
    rt = realised.loc[idx, RT].to_numpy(float)
    as_real = {k: realised.loc[idx, as_price_col(k)].to_numpy(float) for k in products if as_price_col(k) in realised.columns}
    acc = lp.account(spec, rt, charge_mw, discharge_mw, {p: awards[p] for p in products}, as_real)
    cols = {"charge_mw": charge_mw, "discharge_mw": discharge_mw}
    for p in products:
        cols[as_award_col(p)] = awards[p]
    cols["soc_end_mwh"] = soc_end_mwh
    cols["energy_usd"], cols["as_usd"], cols["wear_usd"] = (acc[x].to_numpy() for x in ("energy_usd", "as_usd", "wear_usd"))
    cols["revenue_usd"] = cols["energy_usd"] + cols["as_usd"] - cols["wear_usd"]
    cols["plan_ok"] = plan_ok
    out = pd.DataFrame(cols, index=pd.DatetimeIndex(idx, name="interval_start_utc"))
    out["decision_utc"] = pd.to_datetime(decided_ns).tz_localize("UTC")
    return out
