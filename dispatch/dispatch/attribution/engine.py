"""FR7: split the gap between perfect foresight and what an asset actually earned, interval by interval.

Every number comes from the same LP and the same accounting (`dispatch.optimizer.lp`): perfect foresight, the shadow
optimizer, and the actual dispatch revalued with `mode="fixed"`. The gap is explained by a CHAIN of counterfactuals,
each adding one real-world restriction to the previous one. Per-interval revenue of consecutive stages is subtracted,
so the components telescope and sum to the gap EXACTLY, interval by interval.

    stage   what is held to the actual                                    revenue (per interval)
    S0 pf   nothing: full power, free AS, free SOC path (soc0 = actual)    r0   the potential
    S1      availability: HSL/LSL power derates and reported Min/Max SOC    r1
    S2      + ancillary awards fixed to the actual awards                   r2
    S3      + SOC anchored to the telemetered SOC at each market day's
            start (and no lower than the actual SOC at its end)             r3
    S4      the actual dispatch itself, revalued with the same accounting   r4

    availability_usd = r0 - r1     as_usd = r1 - r2     soc_usd = r2 - r3     forecast_usd = r3 - r4 - position_usd
    actual_usd = r4 + position_usd (settlement value of day-ahead positions, if supplied) or the reported revenue
    gap_usd = pf_usd - actual_usd = availability + as + soc + forecast + unexplained

`forecast_usd` is everything the actual dispatch did worse than the best response to realised prices from the same
starting state (forecast error, decision rules, risk limits), including the settlement value of day-ahead positions.
`unexplained_usd` is the ONLY term not produced by a counterfactual:

* intervals with no actual data (a data gap): the whole pf value is unexplained, the four components are zero (a paper
  ledger records only executed intervals, so for it an absent row means idle, not missing: see `absent_rows`);
* intervals with data: (r4 + position) - reported_usd, when the caller supplies settled or reported revenue
  (an owner export). Without it the actual revenue IS the revaluation and this term is zero BY CONSTRUCTION: the
  closure then measures coverage, not accuracy. `AttributionResult.summary["closure_basis"]` says which case applies.

Order dependence. The split depends on the order of the chain (availability, then AS, then SOC). `shapley=True` also
computes the average over all six orderings of the first three stages (all eight constraint subsets), per interval, and
`summary["order_spread_usd"]` reports the largest difference between the sequential and the Shapley components.

Closure = |sum unexplained| / |sum gap| (PRD, target < 5%); `closure_l1` = sum|unexplained| / sum|gap| is stricter.
"""
from __future__ import annotations

import itertools
import time
from dataclasses import dataclass, field
from typing import Callable, Optional

import numpy as np
import pandas as pd

from ..config import ATTRIBUTION_CLOSURE_MAX, AS_DURATION_H, DT_RT_H, TZ_MARKET
from ..contracts import ATTRIBUTION_COLUMNS, ActualDispatch, BatterySpec, RT, as_award_col, as_price_col
from ..optimizer import lp, rolling

STAGES = ("pf", "availability", "as", "soc", "actual")
MAX_SINGLE_LP_INTERVALS = 96 * 70                  # ASSUMPTION: longer periods are solved in blocks with a lookahead
BLOCK_INTERVALS = 96 * 30
LOOKAHEAD_INTERVALS = 96


# ------------------------------------------------------------------------------------------------ prepared inputs
@dataclass
class _Ctx:
    spec: BatterySpec
    idx: pd.DatetimeIndex
    prices: pd.DataFrame
    products: tuple
    known: np.ndarray                     # actual data present (net_mw and rt price)
    c_act: np.ndarray
    d_act: np.ndarray
    awards: pd.DataFrame                  # as_<p>_mw actual awards, projected to what the LP can represent
    avail_dis: np.ndarray
    avail_chg: np.ndarray
    lo_t: np.ndarray                      # actual reported SOC floor / ceiling (MWh)
    hi_t: np.ndarray
    soc_end: np.ndarray                   # telemetered end-of-interval SOC, NaN where unknown
    soc0: float
    terminal: Optional[float]
    charge_limit: Optional[float]
    days: list
    notes: list = field(default_factory=list)
    stats: dict = field(default_factory=dict)


def _utc_index(idx) -> pd.DatetimeIndex:
    idx = pd.DatetimeIndex(idx)
    return idx.tz_localize("UTC") if idx.tz is None else idx.tz_convert("UTC")


def _col(frame: pd.DataFrame, name: str, idx, fill=np.nan) -> np.ndarray:
    if name in frame.columns:
        return frame[name].reindex(idx).to_numpy(float)
    return np.full(len(idx), fill)


def _project_awards(spec, awards: dict, pmax, pmax_c, lo_t, hi_t, notes) -> dict:
    """Scale awards down where the LP's (conservative) constraints cannot represent them. Real awards come from
    ERCOT's own feasibility rules, so this is expected to be a no-op; the clipped energy is reported."""
    out = {k: v.copy() for k, v in awards.items()}
    up = [k for k in out if k in lp.UP_PRODUCTS]
    dn = [k for k in out if k in lp.DOWN_PRODUCTS]
    clipped = 0.0
    if up:
        tot = sum(out[k] for k in up)
        need = sum(AS_DURATION_H[k] / spec.eta_discharge * out[k] for k in up)
        f = np.minimum(np.where(tot > pmax, pmax / np.maximum(tot, 1e-12), 1.0),
                       np.where(need > hi_t - lo_t, (hi_t - lo_t) / np.maximum(need, 1e-12), 1.0))
        clipped += float(sum(((1 - f) * out[k]).sum() for k in up) * DT_RT_H)
        for k in up:
            out[k] = out[k] * f
    if dn:
        tot = sum(out[k] for k in dn)
        need = sum(spec.eta_charge * AS_DURATION_H[k] * out[k] for k in dn)
        f = np.minimum(np.where(tot > pmax_c, pmax_c / np.maximum(tot, 1e-12), 1.0),
                       np.where(need > hi_t - lo_t, (hi_t - lo_t) / np.maximum(need, 1e-12), 1.0))
        clipped += float(sum(((1 - f) * out[k]).sum() for k in dn) * DT_RT_H)
        for k in dn:
            out[k] = out[k] * f
    if clipped > 1e-9:
        notes.append(f"{clipped:.1f} MWh of actual AS awards exceeded what the LP's power/energy constraints can "
                     "represent and were scaled down for the counterfactual stages")
    return out, clipped


def _market_days(idx: pd.DatetimeIndex) -> list:
    d = idx.tz_convert(TZ_MARKET).normalize().asi8
    cuts = np.flatnonzero(np.diff(d)) + 1
    starts = np.r_[0, cuts]
    ends = np.r_[cuts, len(d)]
    return list(zip(starts.tolist(), ends.tolist()))


def _absent_rows_mode(actual: ActualDispatch, absent_rows: Optional[str]) -> str:
    if absent_rows is None:
        # a paper ledger records executed setpoints only, so an interval with no row was idle; telemetry or an owner
        # export with no row is missing data
        return "idle" if str(actual.source).lower().startswith("paper") else "gap"
    if absent_rows not in ("gap", "idle"):
        raise ValueError("absent_rows must be 'gap', 'idle' or None")
    return absent_rows


def _prepare(spec: BatterySpec, actual: ActualDispatch, prices: pd.DataFrame,
             charge_limit_mw: Optional[float], absent_rows: Optional[str] = None) -> _Ctx:
    idx = lp._check_index(prices.index)
    prices = prices.set_axis(idx, axis=0)
    if RT not in prices.columns:
        raise ValueError(f"prices needs '{RT}'")
    af = actual.frame.copy()
    af.index = _utc_index(af.index)
    af = af[~af.index.duplicated(keep="last")]
    notes: list = []
    rt = prices[RT].to_numpy(float)
    if np.isnan(rt).any():
        raise ValueError("prices has NaN real-time prices; fill or drop them before attribution (fail closed)")
    mode = _absent_rows_mode(actual, absent_rows)
    net = _col(af, "net_mw", idx)
    present = ~np.isnan(net)
    known = np.ones(len(idx), bool) if mode == "idle" else present
    if mode == "idle" and (~present).any():
        notes.append(f"{int((~present).sum())} of {len(idx)} grid intervals have no actual row and were treated as idle "
                     f"(source {actual.source!r}: only executed intervals are recorded)")
    net0 = np.where(present, net, 0.0)
    c_act, d_act = np.clip(-net0, 0, None), np.clip(net0, 0, None)
    cl = spec.power_mw if charge_limit_mw is None else float(charge_limit_mw)
    a_dis = _col(af, "available_mw", idx)
    avail_dis = np.clip(np.where(np.isnan(a_dis), 1.0, a_dis / spec.power_mw), 0.0, 1.0)
    a_chg = _col(af, "available_charge_mw", idx)
    avail_chg = np.clip(np.where(np.isnan(a_chg), avail_dis, a_chg / cl), 0.0, 1.0) if "available_charge_mw" in af.columns \
        else avail_dis.copy()
    lo_t, hi_t = lp._soc_bounds(spec, idx, af["soc_min_mwh"] if "soc_min_mwh" in af.columns else None,
                                af["soc_max_mwh"] if "soc_max_mwh" in af.columns else None)
    soc_end = _col(af, "soc_mwh", idx)
    n_soc_gaps = int(np.isnan(soc_end).sum())
    if mode == "idle":
        soc_end = pd.Series(soc_end).ffill().to_numpy()               # an idle interval leaves the SOC where it was
    else:
        soc_end = pd.Series(soc_end).interpolate(limit=2, limit_area="inside").to_numpy()   # short telemetry gaps only
    if mode != "idle" and n_soc_gaps and not np.isnan(soc_end).all():
        notes.append(f"{n_soc_gaps - int(np.isnan(soc_end).sum())} of {n_soc_gaps} missing SOC readings were "
                     "interpolated (gaps of at most 2 intervals)")
    products = tuple(k for k in spec.as_products if as_price_col(k) in prices.columns)
    raw = {k: np.clip(np.nan_to_num(_col(af, as_award_col(k), idx, fill=0.0)), 0, None) for k in products}
    pmax, pmax_c = spec.power_mw * avail_dis, cl * avail_chg
    proj, clipped = _project_awards(spec, raw, pmax, pmax_c, lo_t, hi_t, notes)
    awards = pd.DataFrame({as_award_col(k): proj[k] for k in products}, index=idx)
    # starting SOC: back out of the first telemetered end-of-interval SOC if the start is not given
    finite = np.flatnonzero(~np.isnan(soc_end))
    if len(finite) and (finite[0] == 0 or mode == "idle"):
        j = finite[0]
        soc0 = soc_end[j] - (spec.eta_charge * c_act[j] - d_act[j] / spec.eta_discharge) * DT_RT_H
        if j > 0:                                                     # idle before the first record: SOC = its start SOC
            soc_end[:j] = np.clip(soc0, spec.soc_min_mwh, spec.soc_hi_mwh)
            finite = np.flatnonzero(~np.isnan(soc_end))
    elif len(finite):
        soc0 = soc_end[finite[0]]
        notes.append("no telemetered SOC for the first interval; the first known SOC is used as the start")
    else:
        soc0 = spec.soc_min_mwh
        notes.append("no SOC telemetry at all: the start SOC is the spec's floor and the SOC stage is a no-op")
    soc0 = float(np.clip(soc0, lo_t[0], hi_t[0]))
    terminal = float(np.clip(soc_end[finite[-1]], lo_t[finite[-1]], hi_t[finite[-1]])) \
        if len(finite) and finite[-1] == len(idx) - 1 else None
    stats = dict(n_intervals=len(idx), n_known=int(known.sum()), coverage=float(known.mean()), absent_rows=mode,
                 soc_known_share=float((~np.isnan(soc_end)).mean()), awards_clipped_mwh=clipped,
                 mean_avail_dis=float(avail_dis.mean()), mean_avail_chg=float(avail_chg.mean()))
    return _Ctx(spec, idx, prices, products, known, c_act, d_act, awards, avail_dis, avail_chg, lo_t, hi_t, soc_end,
                soc0, terminal, charge_limit_mw, _market_days(idx), notes, stats)


# ------------------------------------------------------------------------------------------------ stages
def _stage_kwargs(ctx: _Ctx, avail_on: bool, as_on: bool, a: int, b: int) -> dict:
    sl = slice(a, b)
    idx = ctx.idx[sl]
    kw: dict = dict(charge_limit_mw=ctx.charge_limit)
    if avail_on:
        kw["availability"] = pd.Series(ctx.avail_dis[sl], index=idx)
        kw["charge_availability"] = pd.Series(ctx.avail_chg[sl], index=idx)
        kw["soc_min_series"] = pd.Series(ctx.lo_t[sl], index=idx)
        kw["soc_max_series"] = pd.Series(ctx.hi_t[sl], index=idx)
    if as_on:
        kw["fixed_as"] = ctx.awards.iloc[sl]
    return kw


def _bounds_at(ctx: _Ctx, avail_on: bool, i: int):
    return (ctx.lo_t[i], ctx.hi_t[i]) if avail_on else (ctx.spec.soc_min_mwh, ctx.spec.soc_hi_mwh)


def _window(ctx: _Ctx, a: int, b: int, soc0: float, terminal: Optional[float], avail_on: bool, as_on: bool):
    kw = _stage_kwargs(ctx, avail_on, as_on, a, b)
    lo0, hi0 = _bounds_at(ctx, avail_on, a)
    soc0 = float(np.clip(soc0, lo0, hi0))
    plan = lp.solve(ctx.spec, ctx.prices.iloc[a:b], soc0, mode="pf", terminal_soc_mwh=terminal, **kw)
    return plan


def _frame_arrays(spec, frame: pd.DataFrame) -> dict:
    awards = [as_award_col(k) for k in spec.as_products if as_award_col(k) in frame.columns]
    return dict(rev=(frame["energy_usd"] + frame["as_usd"] - frame["wear_usd"]).to_numpy(),
                net=(frame["discharge_mw"] - frame["charge_mw"]).to_numpy(),
                soc=frame["soc_end_mwh"].to_numpy(),
                award_mw=frame[awards].sum(axis=1).to_numpy() if awards else np.zeros(len(frame)),
                energy=frame["energy_usd"].to_numpy(), asrev=frame["as_usd"].to_numpy(),
                wear=frame["wear_usd"].to_numpy(), frame=frame)


def _whole_period(ctx: _Ctx, avail_on: bool, as_on: bool) -> dict:
    """The period as one LP (or blocks with a lookahead when it is very long)."""
    T = len(ctx.idx)
    lo_T, hi_T = _bounds_at(ctx, avail_on, T - 1)
    terminal = None if ctx.terminal is None else float(np.clip(ctx.terminal, lo_T, hi_T))
    if T <= MAX_SINGLE_LP_INTERVALS:
        plan = _window(ctx, 0, T, ctx.soc0, terminal, avail_on, as_on)
        if not plan.ok and terminal is not None:                       # an unreachable end inventory: drop it, note it
            ctx.notes.append(f"whole-period LP infeasible with the end-SOC constraint ({plan.status}); re-solved without")
            plan = _window(ctx, 0, T, ctx.soc0, None, avail_on, as_on)
        if not plan.ok:
            raise RuntimeError(f"perfect-foresight LP failed: {plan.status}")
        return _frame_arrays(ctx.spec, plan.frame)
    parts, soc = [], ctx.soc0
    for start in range(0, T, BLOCK_INTERVALS):
        end = min(start + BLOCK_INTERVALS, T)
        look = min(end + LOOKAHEAD_INTERVALS, T)
        plan = _window(ctx, start, look, soc, terminal if look == T else None, avail_on, as_on)
        if not plan.ok:
            raise RuntimeError(f"perfect-foresight LP failed in block at {ctx.idx[start]}: {plan.status}")
        f = plan.frame.iloc[: end - start]
        parts.append(f)
        soc = float(f["soc_end_mwh"].iloc[-1])
    return _frame_arrays(ctx.spec, pd.concat(parts))


def _daily_anchored(ctx: _Ctx, avail_on: bool, as_on: bool, fallback: dict) -> dict:
    """One LP per market day, started at the telemetered SOC of the day's start and ending no lower than the telemetered
    SOC of its end. A day without a usable anchor keeps the unanchored plan (no SOC effect there)."""
    frames = []
    n_fallback = n_relaxed = 0
    base = fallback["frame"]
    for a, b in ctx.days:
        s_start = ctx.soc0 if a == 0 else ctx.soc_end[a - 1]
        s_end = ctx.soc_end[b - 1]
        if np.isnan(s_start) or np.isnan(s_end):
            frames.append(base.iloc[a:b])
            n_fallback += 1
            continue
        lo_e, hi_e = _bounds_at(ctx, avail_on, b - 1)
        plan = _window(ctx, a, b, s_start, float(np.clip(s_end, lo_e, hi_e)), avail_on, as_on)
        if not plan.ok:
            plan = _window(ctx, a, b, s_start, None, avail_on, as_on)
            n_relaxed += 1
        if not plan.ok:
            frames.append(base.iloc[a:b])
            n_fallback += 1
            continue
        frames.append(plan.frame)
    note = (f"{n_fallback} market days had no SOC anchor or no feasible anchored LP; the unanchored plan "
            "was used there (no SOC effect attributed)")
    if n_fallback and note not in ctx.notes:
        ctx.notes.append(note)
    note = (f"{n_relaxed} days were infeasible with the end-of-day SOC constraint (actual round-trip "
            "efficiency differs from the spec); the end constraint was dropped on those days")
    if n_relaxed and note not in ctx.notes:
        ctx.notes.append(note)
    if (avail_on, as_on) == (True, True):                                  # the main chain; Shapley reruns are not counted
        ctx.stats["soc_days"] = len(ctx.days)
        ctx.stats["soc_days_fallback"] = n_fallback
        ctx.stats["soc_days_relaxed"] = n_relaxed
    return _frame_arrays(ctx.spec, pd.concat(frames))


def _stage(ctx: _Ctx, avail_on: bool, as_on: bool, soc_on: bool, cache: dict) -> dict:
    key = (avail_on, as_on, soc_on)
    if key in cache:
        return cache[key]
    base_key = (avail_on, as_on, False)
    if base_key not in cache:
        cache[base_key] = _whole_period(ctx, avail_on, as_on)
    if not soc_on:
        return cache[base_key]
    cache[key] = _daily_anchored(ctx, avail_on, as_on, cache[base_key])
    return cache[key]


def _actual_feasibility(ctx: _Ctx) -> dict:
    """How well the LP represents the real asset. Per market day, the actual flows are run through the LP's dynamics from
    the telemetered start-of-day SOC and checked against every LP constraint (worst day reported); the modelled end-of-day
    SOC is compared with the telemetered one (the gap is the round-trip efficiency and auxiliary-load mismatch)."""
    spec = ctx.spec
    cl = spec.power_mw if ctx.charge_limit is None else ctx.charge_limit
    worst: dict = {}
    resid = []
    checked = 0
    for a, b in ctx.days:
        s_start = ctx.soc0 if a == 0 else ctx.soc_end[a - 1]
        if np.isnan(s_start) or not ctx.known[a:b].all():
            continue
        c, d = ctx.c_act[a:b], ctx.d_act[a:b]
        soc = s_start + np.cumsum(spec.eta_charge * DT_RT_H * c - DT_RT_H / spec.eta_discharge * d)
        awards = {k: ctx.awards[as_award_col(k)].to_numpy()[a:b] for k in ctx.products}
        chk = lp._check_arrays(spec, c, d, soc, awards, s_start, spec.power_mw * ctx.avail_dis[a:b],
                               cl * ctx.avail_chg[a:b], ctx.lo_t[a:b], ctx.hi_t[a:b])
        for k, v in chk.items():
            worst[k] = max(worst.get(k, 0.0), v)
        if not np.isnan(ctx.soc_end[b - 1]):
            resid.append(float(soc[-1] - ctx.soc_end[b - 1]))
        checked += 1
    r = np.array(resid) if resid else np.zeros(1)
    worst.update(days_checked=checked, soc_balance_resid_mean_mwh=float(r.mean()),
                 soc_balance_resid_mean_abs_mwh=float(np.abs(r).mean()), soc_balance_resid_max_abs_mwh=float(np.abs(r).max()))
    return worst


def _revalue_actual(ctx: _Ctx) -> tuple:
    acts = pd.DataFrame({"charge_mw": ctx.c_act, "discharge_mw": ctx.d_act}, index=ctx.idx)
    plan = lp.evaluate(ctx.spec, ctx.prices, acts, ctx.soc0, fixed_as=ctx.awards if len(ctx.products) else None,
                       charge_limit_mw=ctx.charge_limit)
    fa = _frame_arrays(ctx.spec, plan.frame)
    rev = np.where(ctx.known, fa["rev"], 0.0)
    return rev, fa, _actual_feasibility(ctx), plan


# ------------------------------------------------------------------------------------------------ result
@dataclass
class AttributionResult:
    frame: pd.DataFrame                       # interval_start_utc + ATTRIBUTION_COLUMNS
    stages: pd.DataFrame                      # per-interval revenue and dispatch of every stage (for explanations)
    summary: dict
    notes: list
    shapley: Optional[pd.DataFrame] = None
    shadow: Optional[rolling.RollingResult] = None
    plans: dict = field(default_factory=dict)       # stage name -> plan frame (charge/discharge/awards/SOC/revenue parts)


def run_attribution(spec: BatterySpec, actual: ActualDispatch, prices: pd.DataFrame,
                    forecast_fn: Optional[Callable] = None, *, position_usd: Optional[pd.Series] = None,
                    reported_usd: Optional[pd.Series] = None, charge_limit_mw: Optional[float] = None,
                    shadow_step_intervals: int = 4, shadow_availability_lookahead: bool = True,
                    shapley: bool = False, progress: Optional[Callable[[str], None]] = None,
                    absent_rows: Optional[str] = None) -> AttributionResult:
    """Full attribution with stages, summary and notes. See the module docstring for the method.

    spec            the asset (power_mw is the discharge limit; pass charge_limit_mw if |LSL| differs)
    actual          ActualDispatch: net_mw (+ discharge) required; as_<p>_mw, available_mw, available_charge_mw,
                    soc_mwh, soc_min_mwh, soc_max_mwh optional (NaN = unknown)
    prices          PriceFrame on the 15-minute UTC grid covering the period: rt_usd_per_mwh and as_<p>_usd_per_mw_h
                    (complete: NaN prices fail closed)
    forecast_fn     forecast_fn(issued_utc) -> ForecastFrame for the shadow optimizer; None skips the shadow
    position_usd    optional per-interval settlement value of forward (day-ahead) positions relative to real time;
                    part of actual revenue, attributed to forecast_usd
    reported_usd    optional per-interval reported or settled revenue (owner export); replaces the revaluation as
                    "actual" and makes unexplained_usd = revaluation + position - reported
    absent_rows     what a grid interval with no row in actual.frame means: "gap" (missing data: the whole pf value of
                    that interval is unexplained) or "idle" (the asset did nothing: net 0, no awards, SOC unchanged).
                    None infers it from actual.source: the paper ledger ("paper-ledger") records only executed
                    intervals, so its absent rows are idle; telemetry and owner exports are gaps.
    """
    log = progress or (lambda m: None)
    t_start = time.perf_counter()
    ctx = _prepare(spec, actual, prices, charge_limit_mw, absent_rows)
    idx, T = ctx.idx, len(ctx.idx)
    known = ctx.known
    cache: dict = {}
    log("stage 0: perfect foresight")
    s0 = _stage(ctx, False, False, False, cache)
    log("stage 1: availability")
    s1 = _stage(ctx, True, False, False, cache)
    log("stage 2: AS awards fixed")
    s2 = _stage(ctx, True, True, False, cache)
    log("stage 3: SOC anchored daily")
    s3 = _stage(ctx, True, True, True, cache)
    log("stage 4: actual revalued")
    r4, fa4, chk4, plan4 = _revalue_actual(ctx)
    r0, r1, r2, r3 = s0["rev"], s1["rev"], s2["rev"], s3["rev"]
    pos = np.zeros(T) if position_usd is None else \
        pd.Series(position_usd).reindex(idx).fillna(0.0).to_numpy(float)
    pos = np.where(known, pos, 0.0)
    revalued = r4 + pos
    if reported_usd is None:
        actual_usd = revalued.copy()
        basis = "revaluation"
    else:
        rep = pd.Series(reported_usd).reindex(idx).to_numpy(float)
        actual_usd = np.where(known & ~np.isnan(rep), rep, revalued)
        basis = "reported"
    actual_usd = np.where(known, actual_usd, 0.0)
    gap = r0 - actual_usd
    comp_avail = np.where(known, r0 - r1, 0.0)
    comp_as = np.where(known, r1 - r2, 0.0)
    comp_soc = np.where(known, r2 - r3, 0.0)
    comp_fc = np.where(known, r3 - r4 - pos, 0.0)
    unexpl = np.where(known, revalued - actual_usd, r0)
    ident = gap - (comp_avail + comp_as + comp_soc + comp_fc + unexpl)
    shadow_res = None
    shadow_usd = np.full(T, np.nan)
    if forecast_fn is not None:
        log("shadow optimizer")
        lo_s = pd.Series(ctx.lo_t, index=idx)
        hi_s = pd.Series(ctx.hi_t, index=idx)
        shadow_res = rolling.run_shadow(
            spec, ctx.prices, forecast_fn, soc0_mwh=ctx.soc0, step_intervals=shadow_step_intervals,
            availability=pd.Series(ctx.avail_dis, index=idx), charge_availability=pd.Series(ctx.avail_chg, index=idx),
            soc_min_series=lo_s, soc_max_series=hi_s, charge_limit_mw=ctx.charge_limit,
            availability_lookahead=shadow_availability_lookahead)
        shadow_usd = shadow_res.frame["revenue_usd"].reindex(idx).to_numpy(float)
    cols = {"actual_usd": actual_usd, "shadow_usd": shadow_usd, "pf_usd": r0, "gap_usd": gap,
            "forecast_usd": comp_fc, "soc_usd": comp_soc, "as_usd": comp_as, "availability_usd": comp_avail,
            "unexplained_usd": unexpl}
    out = pd.DataFrame({"interval_start_utc": idx, **{c: cols[c] for c in ATTRIBUTION_COLUMNS}}).reset_index(drop=True)
    stages = pd.DataFrame({
        "known": known, "rt_usd_per_mwh": ctx.prices[RT].to_numpy(),
        "avail_dis": ctx.avail_dis, "avail_chg": ctx.avail_chg, "soc_lo_mwh": ctx.lo_t, "soc_hi_mwh": ctx.hi_t,
        "r0": r0, "r1": r1, "r2": r2, "r3": r3, "r4": r4, "position_usd": pos,
        "net_pf": s0["net"], "net_avail": s1["net"], "net_as": s2["net"], "net_soc": s3["net"],
        "net_actual": ctx.d_act - ctx.c_act,
        "soc_pf": s0["soc"], "soc_soc_stage": s3["soc"], "soc_actual": ctx.soc_end,
        "soc_actual_model": fa4["soc"],
        "award_mw_pf": s0["award_mw"], "award_mw_actual": ctx.awards.sum(axis=1).to_numpy() if len(ctx.products)
        else np.zeros(T),
        "net_shadow": (shadow_res.frame["discharge_mw"] - shadow_res.frame["charge_mw"]).reindex(idx).to_numpy()
        if shadow_res is not None else np.full(T, np.nan),
    }, index=idx)
    summary = _summary(ctx, out, stages, chk4, ident, basis, shadow_res, time.perf_counter() - t_start)
    arrays = dict(zip(STAGES, (s0, s1, s2, s3, fa4)))
    gross = {}
    for name, a in arrays.items():
        gross[name] = dict(energy_usd=float(a["energy"][known].sum()), as_usd=float(a["asrev"][known].sum()),
                           wear_usd=float(a["wear"][known].sum()))
    summary["stage_breakdown_usd"] = gross
    if position_usd is not None:
        summary["position_usd"] = float(pos.sum())
    result = AttributionResult(frame=out, stages=stages, summary=summary, notes=ctx.notes, shadow=shadow_res,
                               plans={n: a["frame"] for n, a in arrays.items()})
    if shapley:
        log("shapley orderings")
        result.shapley = _shapley(ctx, cache, r4, pos, known)
        sq = out[["availability_usd", "as_usd", "soc_usd"]].sum()
        sh = result.shapley[["availability_usd", "as_usd", "soc_usd"]].sum()
        summary["shapley_total_usd"] = {k: float(v) for k, v in sh.items()}
        summary["sequential_total_usd"] = {k: float(v) for k, v in sq.items()}
        summary["order_spread_usd"] = float(max(abs(sh[k] - sq[k]) for k in sh.index))
        summary["order_spread_share_of_gap"] = summary["order_spread_usd"] / max(abs(summary["gap_usd"]), 1e-9)
    out.attrs["summary"] = summary
    return result


def attribute(spec: BatterySpec, actual: ActualDispatch, prices: pd.DataFrame, forecast_fn: Optional[Callable] = None,
              **kw) -> pd.DataFrame:
    """The FR7 entry point: one row per 15-minute interval with `interval_start_utc` and ATTRIBUTION_COLUMNS.
    Extra keyword arguments are those of `run_attribution`; `frame.attrs["summary"]` holds the totals and closure."""
    return run_attribution(spec, actual, prices, forecast_fn, **kw).frame


def _shapley(ctx: _Ctx, cache: dict, r4, pos, known) -> pd.DataFrame:
    rev = {}
    for av, asn, so in itertools.product((False, True), repeat=3):
        rev[(av, asn, so)] = _stage(ctx, av, asn, so, cache)["rev"]
    names = ("availability_usd", "as_usd", "soc_usd")
    contrib = {n: np.zeros(len(ctx.idx)) for n in names}
    orders = list(itertools.permutations(range(3)))
    for order in orders:
        state = [False, False, False]
        prev = rev[tuple(state)]
        for f in order:
            state[f] = True
            cur = rev[tuple(state)]
            contrib[names[f]] += (prev - cur) / len(orders)
            prev = cur
    out = pd.DataFrame({n: np.where(known, v, 0.0) for n, v in contrib.items()}, index=ctx.idx)
    out["forecast_usd"] = np.where(known, rev[(True, True, True)] - r4 - pos, 0.0)
    return out


def _summary(ctx: _Ctx, out: pd.DataFrame, st: pd.DataFrame, chk4: dict, ident: np.ndarray, basis: str,
             shadow_res, elapsed: float) -> dict:
    known = ctx.known
    tot = out[list(ATTRIBUTION_COLUMNS)].sum()
    gap = float(tot["gap_usd"])
    pf_known = float(out.loc[known, "pf_usd"].sum())
    act = float(tot["actual_usd"])
    unexpl = float(tot["unexplained_usd"])
    s: dict = dict(
        n_intervals=len(out), coverage=float(known.mean()), closure_basis=basis,
        actual_usd=act, pf_usd=float(tot["pf_usd"]), pf_known_usd=pf_known, gap_usd=gap,
        components_usd={k: float(tot[k]) for k in ("forecast_usd", "soc_usd", "as_usd", "availability_usd",
                                                   "unexplained_usd")},
        components_share_of_gap={k: (float(tot[k]) / gap if gap else float("nan"))
                                 for k in ("forecast_usd", "soc_usd", "as_usd", "availability_usd", "unexplained_usd")},
        capture_actual=act / pf_known if pf_known else float("nan"),
        closure=abs(unexpl) / abs(gap) if gap else float("nan"),
        closure_l1=float(out["unexplained_usd"].abs().sum() / max(out["gap_usd"].abs().sum(), 1e-12)),
        closure_target=ATTRIBUTION_CLOSURE_MAX,
        stage_totals_usd={n: float(st[c][known].sum()) for n, c in zip(STAGES, ("r0", "r1", "r2", "r3", "r4"))},
        identity_max_abs_error=float(np.max(np.abs(ident))) if len(ident) else 0.0,
        stage_monotone=bool(all(a >= b - 1e-6 for a, b in zip(
            [st[c].sum() for c in ("r0", "r1", "r2")], [st[c].sum() for c in ("r1", "r2", "r3")]))),
        actual_model_violation=chk4, elapsed_s=elapsed)
    s.update({k: v for k, v in ctx.stats.items() if k not in s})
    if not np.all(np.isnan(out["shadow_usd"])):
        sh = float(out.loc[known, "shadow_usd"].sum())
        s["shadow_usd"] = sh
        s["capture_shadow"] = sh / pf_known if pf_known else float("nan")
        recov = (sh - float(out.loc[known, "actual_usd"].sum())) / (pf_known - float(out.loc[known, "actual_usd"].sum())) \
            if pf_known != float(out.loc[known, "actual_usd"].sum()) else float("nan")
        s["shadow_recovers_share_of_gap"] = recov
        s["shadow_failed_solves"] = int(shadow_res.n_failed) if shadow_res is not None else 0
        s["shadow_solves"] = int(shadow_res.n_solves) if shadow_res is not None else 0
        s["shadow_end_soc_mwh"] = float(shadow_res.soc_end_mwh) if shadow_res is not None else float("nan")
    s["notes"] = list(ctx.notes)
    return s


# ------------------------------------------------------------------------------------------------ explanations
COMPONENTS = ("forecast_usd", "soc_usd", "as_usd", "availability_usd", "unexplained_usd")


def explain_top(result: AttributionResult, n: int = 20, spec: Optional[BatterySpec] = None) -> pd.DataFrame:
    """The n intervals with the largest gap, each with its dominant component and a plain-language reason built only
    from measured quantities and the stage plans (no free text from a model): interval, gap, the five components, the
    dominant one, the price, the dispatch of every stage (MW), SOC, availability, award MW, and `explanation`."""
    fr, st = result.frame.set_index("interval_start_utc"), result.stages
    top = fr.sort_values("gap_usd", ascending=False).head(n)
    rows = []
    for ts, r in top.iterrows():
        s = st.loc[ts]
        comps = {c: float(r[c]) for c in COMPONENTS}
        dom = max(comps, key=lambda c: abs(comps[c]))
        rows.append(dict(interval_start_utc=ts, gap_usd=float(r["gap_usd"]), pf_usd=float(r["pf_usd"]),
                         actual_usd=float(r["actual_usd"]), **comps, dominant=dom.replace("_usd", ""),
                         rt_usd_per_mwh=float(s["rt_usd_per_mwh"]), net_actual_mw=float(s["net_actual"]),
                         net_pf_mw=float(s["net_pf"]), net_avail_mw=float(s["net_avail"]), net_as_mw=float(s["net_as"]),
                         net_soc_mw=float(s["net_soc"]), net_shadow_mw=float(s["net_shadow"]),
                         soc_actual_mwh=float(s["soc_actual"]), soc_pf_mwh=float(s["soc_pf"]),
                         avail_dis=float(s["avail_dis"]), award_mw_actual=float(s["award_mw_actual"]),
                         award_mw_pf=float(s["award_mw_pf"]), explanation=_sentence(dom, comps, s, r)))
    return pd.DataFrame(rows)


def _sentence(dom: str, comps: dict, s, r) -> str:
    gap = float(r["gap_usd"])
    if abs(comps[dom]) > abs(gap) + 0.5:
        share = f"This term is {_usd(comps[dom])}, more than the {_usd(gap)} missed in the interval (other terms offset it)."
    else:
        share = f"This term is {_usd(comps[dom])} of the {_usd(gap)} missed in the interval."
    if not bool(s["known"]):
        return f"No actual data for this interval (a data gap): the whole {_usd(gap)} is unexplained."
    head = (f"RT price ${s['rt_usd_per_mwh']:,.0f}/MWh. Perfect foresight moves {s['net_pf']:+.0f} MW, "
            f"the asset moved {s['net_actual']:+.0f} MW.")
    if dom == "availability_usd":
        return (f"{head} Availability: power was limited to {s['avail_dis']:.0%} of nameplate and the reported SOC window was "
                f"{s['soc_lo_mwh']:.0f}-{s['soc_hi_mwh']:.0f} MWh; with those limits the best plan moves "
                f"{s['net_avail']:+.0f} MW. {share}")
    if dom == "as_usd":
        return (f"{head} Ancillary awards: the asset held {s['award_mw_actual']:.0f} MW against {s['award_mw_pf']:.0f} MW in "
                f"the free plan; with the actual awards fixed the best plan moves {s['net_as']:+.0f} MW instead of "
                f"{s['net_avail']:+.0f} MW. {share}")
    if dom == "soc_usd":
        return (f"{head} State of charge: starting the day from the telemetered SOC the best plan moves "
                f"{s['net_soc']:+.0f} MW, against {s['net_as']:+.0f} MW if the SOC path were free. {share}")
    if dom == "unexplained_usd":
        return f"{head} {share} It is not explained by the counterfactuals."
    shadow = "" if np.isnan(s["net_shadow"]) else f" The shadow optimizer moved {s['net_shadow']:+.0f} MW."
    return (f"{head} Forecast or decision: from the asset's own start SOC, awards and derates the best response moves "
            f"{s['net_soc']:+.0f} MW.{shadow} {share}")


def _usd(x: float) -> str:
    return f"-${abs(x):,.0f}" if x < 0 else f"${x:,.0f}"
