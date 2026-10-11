"""FR5: one linear programme for perfect foresight, forecast and fixed-action valuation.

Everything the platform values (a live plan, a backtest, the shadow optimizer, the actual dispatch in an attribution run)
goes through the same accounting function (`account`), so two numbers computed on the same actions cannot disagree.

Formulation (all quantities on the 15-minute grid, dt = 0.25 h, t = 1..T)

    variables   c_t >= 0   grid-side charge power (MW)
                d_t >= 0   discharge power (MW)
                s_t        state of charge at the END of interval t (MWh)
                r_kt >= 0  ancillary-service award of product k (MW), k in spec.as_products that have a price column

    maximise    sum_t [ lam_t (d_t - c_t) dt  +  sum_k pi_kt r_kt dt  -  w d_t dt ]
    subject to  s_t = s_{t-1} + eta_c c_t dt - d_t dt / eta_d          (s_0 = soc0)
                s_lo,t <= s_t <= s_hi,t,      s_T >= terminal (optional)
                c_t <= Pc a^c_t
                d_t + sum_{k in UP} r_kt <= P a_t         UP = {REGUP, RRS, ECRS, NSPIN}
                c_t + r_{REGDN,t}        <= Pc a^c_t
                sum_{k in UP} dur_k r_kt / eta_d <= s_t - s_lo,t          (energy to deliver every up product)
                eta_c dur_REGDN r_{REGDN,t}      <= s_hi,t - s_t          (headroom to absorb REGDN)
                sum_{t in CPT day} d_t dt <= N (s_hi - s_lo)              (only if spec.max_cycles_per_day = N)

    lam_t   real-time energy price ($/MWh) = prices["rt_usd_per_mwh"]   (perfect foresight: realised; forecast: P50 view)
    pi_kt   clearing price for capacity ($/MW-h) = prices["as_<k>_usd_per_mw_h"]
    a_t     availability as a FRACTION of power_mw in [0, 1] (None -> 1); a^c_t the charge-side fraction (default a_t)
    P, Pc   spec.power_mw; Pc = charge_limit_mw (default P) when the charge limit differs from the discharge limit
    dur_k   dispatch.config.AS_DURATION_H (REAL, NPRR1282)
    eta_c, eta_d, w, N   from the BatterySpec; s_lo,t / s_hi,t default to the spec's soc_min / soc_max and may be
            tightened per interval (energy derates: `soc_min_series`, `soc_max_series`)

The power and energy constraints are deliberately conservative (no netting of charging against up-AS headroom), which
keeps every plan inside the guard's limits. Day-ahead energy positions are out of scope in v1: only the RT price enters.

Simultaneous charge and discharge. With rte < 1 and wear > 0, doing both in one interval only burns energy, so the
LP never does it unless the price is deep below the threshold p* = -w * rte / (1 - rte) ($-113/MWh for the research
arena's 85% / $20 battery). Proof: replace (c, d) by (c - delta, d - rte*delta); SOC is unchanged, every constraint is
weaker, and revenue changes by delta*dt*(p (1 - rte) + w rte) >= 0 iff p >= p*. So (a) for p >= p* an overlap-free
optimum always exists and any overlap the solver returns is netted away at no cost; (b) only at intervals with p < p*
can the relaxation profit from the (unphysical) trick of burning energy, and there `solve` re-solves a MILP with a
charge/discharge binary on exactly those intervals (HiGHS). Counts are reported in `plan.frame.attrs["diagnostics"]`.

Fail closed: any status other than "optimal" makes `Plan.ok` False; "optimal_inaccurate" is not accepted; the finished
plan is re-checked against every constraint (`check_plan`, tolerance 1e-6) and downgraded to "numerical_violation" if
it fails; invalid inputs raise ValueError.
"""
from __future__ import annotations

import hashlib
import json
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass
from typing import Optional

import numpy as np
import pandas as pd

from .. import units
from ..config import AS_DURATION_H, DT_RT_H, TZ_MARKET
from ..contracts import BatterySpec, Plan, RT, Setpoint, as_award_col, as_price_col

LP_VERSION = "lp-1.2"
UP_PRODUCTS = ("REGUP", "RRS", "ECRS", "NSPIN")      # REAL: products that need energy above the SOC floor
DOWN_PRODUCTS = ("REGDN",)                           # REAL: needs headroom below the SOC ceiling
MODES = ("pf", "forecast", "fixed")
TOL = 1e-6                                           # feasibility tolerance of a finished plan (MW, MWh)
OVERLAP_TOL_MW = 1e-6                                # min(c, d) above this counts as simultaneous charge and discharge
CLEAN_EPS = 1e-9                                     # values below this are numerical noise and become zero
MILP_TIME_LIMIT_S = 30.0                             # ASSUMPTION: cap for the (rare) MILP re-solve
_CACHE_SIZE = 12                                     # compiled models kept per thread
_STEP_NS = int(round(DT_RT_H * 3600 * 1e9))


# ------------------------------------------------------------------------------------------------ accounting
def account(spec: BatterySpec, rt_usd_per_mwh, charge_mw, discharge_mw, awards_mw: Optional[dict] = None,
            as_price: Optional[dict] = None) -> pd.DataFrame:
    """THE accounting: energy_usd (signed, + when selling), as_usd, wear_usd (a positive cost). Arrays in, DataFrame out
    (no index). Revenue of an interval is energy_usd + as_usd - wear_usd. All arithmetic goes through `dispatch.units`.
    """
    rt = np.asarray(rt_usd_per_mwh, float)
    c = np.asarray(charge_mw, float)
    d = np.asarray(discharge_mw, float)
    net_mwh = units.energy_mwh(d - c)                                   # MW held for dt -> MWh
    energy = units.energy_revenue_usd(rt, net_mwh)                      # $/MWh x MWh
    as_usd = np.zeros_like(rt)
    for k, award in (awards_mw or {}).items():
        if as_price and k in as_price:
            as_usd = as_usd + units.as_revenue_usd(np.asarray(as_price[k], float), np.asarray(award, float))
    wear = spec.wear_usd_per_mwh * units.energy_mwh(d)                  # $ per MWh discharged x MWh discharged
    return pd.DataFrame({"energy_usd": energy, "as_usd": as_usd, "wear_usd": wear})


def revenue(frame: pd.DataFrame) -> pd.Series:
    """Per-interval revenue of a plan frame: energy + AS - wear."""
    return frame["energy_usd"] + frame["as_usd"] - frame["wear_usd"]


def net_mw(frame: pd.DataFrame) -> pd.Series:
    """Signed net power (+ discharge) of a plan frame, the Setpoint convention."""
    return frame["discharge_mw"] - frame["charge_mw"]


def overlap_price_threshold(spec: BatterySpec) -> float:
    """p* = -w rte / (1 - rte): below it the relaxed LP may profit from charging and discharging in the same interval."""
    if spec.rte >= 1.0:
        return -np.inf
    return -spec.wear_usd_per_mwh * spec.rte / (1.0 - spec.rte)


# ------------------------------------------------------------------------------------------------ input handling
@dataclass
class _Inputs:
    index: pd.DatetimeIndex
    T: int
    rt: np.ndarray
    products: tuple                       # AS products that have a price column (empty if allow_as is False)
    pi: dict                              # product -> price array
    pmax: np.ndarray                      # discharge-side power limit per interval (MW) = P * availability
    pmax_c: np.ndarray                    # charge-side power limit per interval (MW)
    lo_t: np.ndarray                      # SOC floor per interval (MWh)
    hi_t: np.ndarray                      # SOC ceiling per interval (MWh)
    soc0: float
    terminal: float
    fix_lo: dict                          # product -> lower bound array (fixed award or 0)
    fix_hi: dict                          # product -> upper bound array (fixed award or the side's power limit)
    unpriced: tuple
    fixed_c: Optional[np.ndarray] = None
    fixed_d: Optional[np.ndarray] = None


def _check_index(idx) -> pd.DatetimeIndex:
    if not isinstance(idx, pd.DatetimeIndex) or idx.tz is None:
        raise ValueError("prices must be indexed by a tz-aware DatetimeIndex (UTC interval_start_utc)")
    if len(idx) == 0:
        raise ValueError("prices is empty")
    idx = idx.tz_convert("UTC")
    ns = idx.as_unit("ns").asi8
    if len(ns) > 1 and not np.all(np.diff(ns) == _STEP_NS):
        raise ValueError("prices index must be strictly regular on the 15-minute grid (no gaps, no duplicates)")
    return idx


def _align(obj, index, name, fill=None):
    s = obj.reindex(index) if hasattr(obj, "reindex") else pd.Series(np.asarray(obj, float), index=index)
    if fill is not None:
        s = s.fillna(fill)
    arr = s.to_numpy(float)
    if np.isnan(arr).any():
        raise ValueError(f"{name} has NaN on the optimisation grid (missing values fail closed)")
    return arr


def _fraction(series, idx, name) -> np.ndarray:
    a = _align(pd.Series(series), idx, name)
    if a.min() < -1e-12 or a.max() > 1.0 + 1e-9:
        raise ValueError(f"{name} is a FRACTION of the power limit in [0, 1]; got min={a.min():.4g} max={a.max():.4g} "
                         "(convert MW with available_mw / power_mw)")
    return np.clip(a, 0.0, 1.0)


def _soc_bounds(spec, idx, soc_min_series, soc_max_series):
    T = len(idx)
    lo = np.full(T, spec.soc_min_mwh)
    hi = np.full(T, spec.soc_hi_mwh)
    if soc_min_series is not None:
        v = pd.Series(soc_min_series).reindex(idx).to_numpy(float)
        lo = np.maximum(lo, np.where(np.isnan(v), lo, v))
    if soc_max_series is not None:
        v = pd.Series(soc_max_series).reindex(idx).to_numpy(float)
        hi = np.minimum(hi, np.where(np.isnan(v), hi, v))
    return lo, np.maximum(hi, lo)


def _prepare(spec, prices, soc0_mwh, mode, allow_as, availability, fixed_as, terminal_soc_mwh, fixed_energy,
             charge_availability, charge_limit_mw, soc_min_series=None, soc_max_series=None) -> _Inputs:
    if mode not in MODES:
        raise ValueError(f"mode must be one of {MODES}")
    if not isinstance(prices, pd.DataFrame) or RT not in prices.columns:
        raise ValueError(f"prices must be a DataFrame with a '{RT}' column")
    idx = _check_index(prices.index)
    T = len(idx)
    prices = prices.set_axis(idx, axis=0)
    rt = _align(prices[RT], idx, RT)
    if not np.isfinite(rt).all():
        raise ValueError("rt price contains infinities")
    soc0 = float(soc0_mwh)
    if not np.isfinite(soc0):
        raise ValueError("soc0_mwh must be finite")
    avail = np.ones(T) if availability is None else _fraction(availability, idx, "availability")
    avail_c = avail if charge_availability is None else _fraction(charge_availability, idx, "charge_availability")
    cl = spec.power_mw if charge_limit_mw is None else float(charge_limit_mw)
    if not np.isfinite(cl) or cl <= 0:
        raise ValueError("charge_limit_mw must be positive")
    pmax, pmax_c = spec.power_mw * avail, cl * avail_c
    lo_t, hi_t = _soc_bounds(spec, idx, soc_min_series, soc_max_series)
    products, pi, unpriced, lo, hi = [], {}, [], {}, {}
    fixed_df = None
    if fixed_as is not None:
        fixed_df = fixed_as.copy()
        fixed_df.index = _check_index(fixed_df.index)
    for k in spec.as_products:
        col = as_price_col(k)
        if not allow_as and mode != "fixed":
            continue
        fcol = as_award_col(k)
        has_fixed = fixed_df is not None and fcol in fixed_df.columns
        if col not in prices.columns:
            if mode == "fixed" and has_fixed and np.nan_to_num(fixed_df[fcol].to_numpy(float)).any():
                raise ValueError(f"fixed award for {k} but prices has no '{col}' column to value it")
            unpriced.append(k)
            continue
        pi[k] = _align(prices[col], idx, col)
        products.append(k)
        side = pmax_c if k in DOWN_PRODUCTS else pmax
        if has_fixed:
            v = np.clip(_align(fixed_df[fcol], idx, fcol, fill=0.0), 0.0, None)
            lo[k], hi[k] = v, v
        elif mode == "fixed":
            lo[k], hi[k] = np.zeros(T), np.zeros(T)
        else:
            lo[k] = np.zeros(T)
            hi[k] = side.copy() if allow_as else np.zeros(T)
    inp = _Inputs(index=idx, T=T, rt=rt, products=tuple(products), pi=pi, pmax=pmax, pmax_c=pmax_c, lo_t=lo_t,
                  hi_t=hi_t, soc0=soc0,
                  terminal=float(lo_t[-1]) if terminal_soc_mwh is None else float(terminal_soc_mwh),
                  fix_lo=lo, fix_hi=hi, unpriced=tuple(unpriced))
    if mode == "fixed":
        if fixed_energy is None:
            raise ValueError("mode='fixed' needs fixed_energy with charge_mw/discharge_mw (or net_mw) columns")
        fe = fixed_energy.copy()
        fe.index = _check_index(fe.index)
        if "charge_mw" in fe.columns and "discharge_mw" in fe.columns:
            cc = _align(fe["charge_mw"], idx, "charge_mw", fill=0.0)
            dd = _align(fe["discharge_mw"], idx, "discharge_mw", fill=0.0)
        elif "net_mw" in fe.columns:
            nn = _align(fe["net_mw"], idx, "net_mw", fill=0.0)
            cc, dd = np.clip(-nn, 0, None), np.clip(nn, 0, None)
        else:
            raise ValueError("fixed_energy needs charge_mw/discharge_mw or net_mw")
        inp.fixed_c, inp.fixed_d = np.clip(cc, 0.0, None), np.clip(dd, 0.0, None)
    return inp


def _inputs_sha(spec, inp: _Inputs, mode, allow_as, terminal_given) -> str:
    h = hashlib.sha256()
    meta = dict(version=LP_VERSION, mode=mode, allow_as=bool(allow_as), soc0=inp.soc0,
                terminal=inp.terminal if terminal_given else None, products=list(inp.products),
                spec=dict(asset_id=spec.asset_id, sp=spec.settlement_point, P=spec.power_mw, E=spec.energy_mwh,
                          lo=spec.soc_min_mwh, hi=spec.soc_hi_mwh, rte=spec.rte, loss=spec.loss_side,
                          wear=spec.wear_usd_per_mwh, cyc=spec.max_cycles_per_day, as_products=list(spec.as_products)))
    h.update(json.dumps(meta, sort_keys=True).encode())
    h.update(inp.index.as_unit("ns").asi8.tobytes())

    def put(tag, arr):
        h.update(tag.encode())
        h.update(np.ascontiguousarray(arr, dtype="<f8").tobytes())

    put("rt", inp.rt)
    put("pmax", inp.pmax)
    put("pmax_c", inp.pmax_c)
    put("soc_lo", inp.lo_t)
    put("soc_hi", inp.hi_t)
    for k in inp.products:
        put("pi_" + k, inp.pi[k])
        put("lo_" + k, inp.fix_lo[k])
        put("hi_" + k, inp.fix_hi[k])
    if inp.fixed_c is not None:
        put("fc", inp.fixed_c)
        put("fd", inp.fixed_d)
    return h.hexdigest()


# ------------------------------------------------------------------------------------------------ compiled model
def _day_groups(index: pd.DatetimeIndex) -> tuple:
    """Index ranges of consecutive intervals sharing a CPT calendar date (for the daily cycle cap)."""
    dates = index.tz_convert(TZ_MARKET).normalize().asi8
    cuts = np.flatnonzero(np.diff(dates)) + 1
    starts = np.r_[0, cuts]
    ends = np.r_[cuts, len(dates)]
    return tuple(zip(starts.tolist(), ends.tolist()))


class _Model:
    """A cvxpy problem compiled once per (spec numbers, horizon, products, day grouping) and re-solved by changing
    Parameter values (DPP), which brings a 96-interval solve to tens of milliseconds."""

    def __init__(self, spec: BatterySpec, T: int, products: tuple, groups):
        import cvxpy as cp
        self.cp = cp
        self.T, self.products = T, products
        dt = DT_RT_H
        eta_c, eta_d = spec.eta_charge, spec.eta_discharge
        self.c = cp.Variable(T, nonneg=True)
        self.d = cp.Variable(T, nonneg=True)
        self.s = cp.Variable(T)
        self.r = {k: cp.Variable(T, nonneg=True) for k in products}
        self.p_soc_lo = cp.Parameter(T)
        self.p_soc_hi = cp.Parameter(T)
        self.p_lam = cp.Parameter(T)
        self.p_pi = {k: cp.Parameter(T) for k in products}
        self.p_pmax = cp.Parameter(T, nonneg=True)
        self.p_pmax_c = cp.Parameter(T, nonneg=True)
        self.p_lo = {k: cp.Parameter(T, nonneg=True) for k in products}
        self.p_hi = {k: cp.Parameter(T, nonneg=True) for k in products}
        self.p_soc0 = cp.Parameter()
        self.p_term = cp.Parameter()
        s_prev = self.p_soc0 if T == 1 else cp.hstack([self.p_soc0, self.s[:-1]])
        up = [k for k in products if k in UP_PRODUCTS]
        dn = [k for k in products if k in DOWN_PRODUCTS]
        cons = [self.s == s_prev + eta_c * dt * self.c - (dt / eta_d) * self.d,
                self.s >= self.p_soc_lo, self.s <= self.p_soc_hi, self.s[-1] >= self.p_term,
                self.c <= self.p_pmax_c]
        up_sum = sum(self.r[k] for k in up) if up else 0
        dn_sum = sum(self.r[k] for k in dn) if dn else 0
        cons.append(self.d + up_sum <= self.p_pmax)
        cons.append(self.c + dn_sum <= self.p_pmax_c)
        if up:
            cons.append(sum((AS_DURATION_H[k] / eta_d) * self.r[k] for k in up) <= self.s - self.p_soc_lo)
        if dn:
            cons.append(sum(eta_c * AS_DURATION_H[k] * self.r[k] for k in dn) <= self.p_soc_hi - self.s)
        for k in products:
            cons += [self.r[k] >= self.p_lo[k], self.r[k] <= self.p_hi[k]]
        if spec.max_cycles_per_day is not None:
            cap = spec.max_cycles_per_day * (spec.soc_hi_mwh - spec.soc_min_mwh)
            for a, b in groups:
                cons.append(cp.sum(self.d[a:b]) * dt <= cap)
        obj = cp.sum(cp.multiply(self.p_lam, self.d - self.c)) * dt - spec.wear_usd_per_mwh * dt * cp.sum(self.d)
        for k in products:
            obj = obj + cp.sum(cp.multiply(self.p_pi[k], self.r[k])) * dt
        self.prob = cp.Problem(cp.Maximize(obj), cons)

    def set(self, inp: _Inputs):
        self.p_soc_lo.value = inp.lo_t
        self.p_soc_hi.value = inp.hi_t
        self.p_lam.value = inp.rt
        self.p_pmax.value = inp.pmax
        self.p_pmax_c.value = inp.pmax_c
        self.p_soc0.value = inp.soc0
        self.p_term.value = inp.terminal
        for k in self.products:
            self.p_pi[k].value = inp.pi[k]
            self.p_lo[k].value = inp.fix_lo[k]
            self.p_hi[k].value = np.maximum(inp.fix_hi[k], inp.fix_lo[k])


_TLS = threading.local()


def _get_model(spec: BatterySpec, inp: _Inputs) -> _Model:
    cache = getattr(_TLS, "cache", None)
    if cache is None:
        cache = _TLS.cache = OrderedDict()
    groups = _day_groups(inp.index) if spec.max_cycles_per_day is not None else ()
    key = (spec.eta_charge, spec.eta_discharge, spec.soc_min_mwh, spec.soc_hi_mwh,
           spec.wear_usd_per_mwh, spec.max_cycles_per_day, inp.T, inp.products, groups)   # bounds are Parameters, but
    # the cycle cap constant above depends on spec bounds, so they stay in the key
    m = cache.get(key)
    if m is None:
        m = _Model(spec, inp.T, inp.products, groups)
        cache[key] = m
        while len(cache) > _CACHE_SIZE:
            cache.popitem(last=False)
    else:
        cache.move_to_end(key)
    return m


def clear_cache():
    """Drop compiled models of the calling thread (tests, memory)."""
    _TLS.cache = OrderedDict()


def warmup(spec: Optional[BatterySpec] = None) -> float:
    """Import and compile once (the first solve in a process takes seconds). Returns the seconds spent."""
    t0 = time.perf_counter()
    spec = spec or BatterySpec("warmup", "HB_NORTH", 1.0, 2.0)
    idx = pd.date_range("2026-01-01", periods=96, freq="15min", tz="UTC")
    solve(spec, pd.DataFrame({RT: np.linspace(10, 40, 96)}, index=idx), spec.soc_min_mwh, mode="pf")
    return time.perf_counter() - t0


# ------------------------------------------------------------------------------------------------ solving
def _run(prob, solvers_opts, log):
    """Try HIGHS then CLARABEL. Returns (status, solver_name)."""
    import cvxpy as cp
    last_status = "solver_error"
    for name, kwargs in solvers_opts:
        try:
            prob.solve(solver=getattr(cp, name), **kwargs)
        except Exception as e:                                     # cvxpy.error.SolverError, highspy hiccups
            log.append(f"{name}: {type(e).__name__}")
            last_status = "solver_error"
            continue
        last_status = prob.status
        if last_status in ("optimal", "infeasible", "unbounded"):
            return last_status, name
        log.append(f"{name}: {last_status}")
    return last_status, solvers_opts[-1][0]


def _extract(model: _Model, T: int):
    def val(v):
        x = v.value
        return np.zeros(T) if x is None else np.asarray(x, float).reshape(-1)
    return val(model.c), val(model.d), {k: val(model.r[k]) for k in model.products}


def _milp(spec, inp: _Inputs, binary_idx: np.ndarray):
    """Re-solve with a charge/discharge binary on the given intervals (HiGHS MIP). Fresh problem, not cached."""
    import cvxpy as cp
    T, dt = inp.T, DT_RT_H
    eta_c, eta_d = spec.eta_charge, spec.eta_discharge
    lo, hi = inp.lo_t, inp.hi_t
    c, d, s = cp.Variable(T, nonneg=True), cp.Variable(T, nonneg=True), cp.Variable(T)
    r = {k: cp.Variable(T, nonneg=True) for k in inp.products}
    s_prev = np.array([inp.soc0]) if T == 1 else cp.hstack([inp.soc0, s[:-1]])
    up = [k for k in inp.products if k in UP_PRODUCTS]
    dn = [k for k in inp.products if k in DOWN_PRODUCTS]
    cons = [s == s_prev + eta_c * dt * c - (dt / eta_d) * d, s >= lo, s <= hi, s[-1] >= inp.terminal,
            c <= inp.pmax_c,
            d + (sum(r[k] for k in up) if up else 0) <= inp.pmax,
            c + (sum(r[k] for k in dn) if dn else 0) <= inp.pmax_c]
    if up:
        cons.append(sum((AS_DURATION_H[k] / eta_d) * r[k] for k in up) <= s - lo)
    if dn:
        cons.append(sum(eta_c * AS_DURATION_H[k] * r[k] for k in dn) <= hi - s)
    for k in inp.products:
        cons += [r[k] >= inp.fix_lo[k], r[k] <= np.maximum(inp.fix_hi[k], inp.fix_lo[k])]
    if spec.max_cycles_per_day is not None:
        cap = spec.max_cycles_per_day * (spec.soc_hi_mwh - spec.soc_min_mwh)
        for a, b in _day_groups(inp.index):
            cons.append(cp.sum(d[a:b]) * dt <= cap)
    u = cp.Variable(len(binary_idx), boolean=True)                    # 1 = charge mode, 0 = discharge/idle mode
    cons += [c[binary_idx] <= cp.multiply(inp.pmax_c[binary_idx], u),
             d[binary_idx] <= cp.multiply(inp.pmax[binary_idx], 1 - u)]
    obj = cp.sum(cp.multiply(inp.rt, d - c)) * dt - spec.wear_usd_per_mwh * dt * cp.sum(d)
    for k in inp.products:
        obj = obj + cp.sum(cp.multiply(inp.pi[k], r[k])) * dt
    prob = cp.Problem(cp.Maximize(obj), cons)
    try:
        prob.solve(solver=cp.HIGHS, time_limit=MILP_TIME_LIMIT_S)       # cvxpy passes extra kwargs to HiGHS options
        status = prob.status
    except Exception as e:
        status = f"solver_error:{type(e).__name__}"
    if status != "optimal" or c.value is None:
        return status, None, None, None
    cv = np.asarray(c.value, float).reshape(-1)
    dv = np.asarray(d.value, float).reshape(-1)
    rv = {k: np.asarray(r[k].value, float).reshape(-1) for k in inp.products}
    return status, cv, dv, rv


def _net_overlap(spec: BatterySpec, c: np.ndarray, d: np.ndarray):
    """Remove simultaneous charge and discharge without changing the SOC path: c -= delta, d -= rte*delta."""
    rho = spec.eta_charge * spec.eta_discharge
    delta = np.minimum(c, d / rho)
    mask = delta > 0
    c2, d2 = c.copy(), d.copy()
    c2[mask] = c[mask] - delta[mask]
    d2[mask] = d[mask] - rho * delta[mask]
    return np.clip(c2, 0, None), np.clip(d2, 0, None)


def _soc_path(spec, soc0, c, d):
    steps = spec.eta_charge * DT_RT_H * c - (DT_RT_H / spec.eta_discharge) * d
    return soc0 + np.cumsum(steps)


def _clean(x):
    x = np.where(np.abs(x) < CLEAN_EPS, 0.0, x)
    return np.clip(x, 0.0, None)


def _check_arrays(spec, c, d, s, awards, soc0, pmax, pmax_c, lo, hi) -> dict:
    n = len(c)
    eta_c, eta_d = spec.eta_charge, spec.eta_discharge
    up = np.zeros(n)
    dn = np.zeros(n)
    need_up = np.zeros(n)
    need_dn = np.zeros(n)
    neg = max(-c.min(initial=0.0), -d.min(initial=0.0))
    for k, v in awards.items():
        neg = max(neg, -v.min(initial=0.0))
        if k in UP_PRODUCTS:
            up = up + v
            need_up = need_up + AS_DURATION_H[k] / eta_d * v
        elif k in DOWN_PRODUCTS:
            dn = dn + v
            need_dn = need_dn + eta_c * AS_DURATION_H[k] * v
    s_prev = np.r_[soc0, s[:-1]]
    out = dict(
        soc_dynamics=float(np.max(np.abs(s - (s_prev + eta_c * DT_RT_H * c - DT_RT_H / eta_d * d)), initial=0.0)),
        soc_below_min=float(np.max(lo - s, initial=0.0)),
        soc_above_max=float(np.max(s - hi, initial=0.0)),
        charge_over_power=float(np.max(c - pmax_c, initial=0.0)),
        discharge_plus_up_over_power=float(np.max(d + up - pmax, initial=0.0)),
        charge_plus_dn_over_power=float(np.max(c + dn - pmax_c, initial=0.0)),
        up_energy_short=float(np.max(need_up - (s - lo), initial=0.0)),
        dn_headroom_short=float(np.max(need_dn - (hi - s), initial=0.0)),
        negative=float(neg),
        simultaneous_mw=float(np.max(np.minimum(c, d), initial=0.0)),
    )
    out["max_violation"] = max(v for k, v in out.items() if k != "simultaneous_mw")
    return out


def check_plan(spec: BatterySpec, frame: pd.DataFrame, soc0_mwh: float, availability=None,
               charge_availability=None, charge_limit_mw: Optional[float] = None, soc_min_series=None,
               soc_max_series=None) -> dict:
    """Largest violation of each constraint family (all <= TOL for a feasible plan); `max_violation` is their max.
    Availability arguments are the fractions given to `solve` (None = full)."""
    n = len(frame)

    def arr(a):
        if a is None:
            return np.ones(n)
        if isinstance(a, pd.Series):
            return a.reindex(frame.index).to_numpy(float)
        return np.asarray(a, float)

    avail = arr(availability)
    avail_c = avail if charge_availability is None else arr(charge_availability)
    cl = spec.power_mw if charge_limit_mw is None else charge_limit_mw
    awards = {k: frame[as_award_col(k)].to_numpy(float) for k in spec.as_products if as_award_col(k) in frame.columns}
    lo, hi = _soc_bounds(spec, frame.index, soc_min_series, soc_max_series)
    return _check_arrays(spec, frame["charge_mw"].to_numpy(float), frame["discharge_mw"].to_numpy(float),
                         frame["soc_end_mwh"].to_numpy(float), awards, soc0_mwh, spec.power_mw * avail, cl * avail_c,
                         lo, hi)


@dataclass
class _Built:
    frame: pd.DataFrame
    objective: float
    check: dict


def _assemble(spec, inp: _Inputs, c, d, awards, soc0) -> _Built:
    c, d = _clean(c), _clean(d)
    awards = {k: _clean(v) for k, v in awards.items()}
    soc = _soc_path(spec, soc0, c, d)
    soc = np.where((soc < inp.lo_t) & (soc > inp.lo_t - TOL), inp.lo_t, soc)
    soc = np.where((soc > inp.hi_t) & (soc < inp.hi_t + TOL), inp.hi_t, soc)
    cols = {"charge_mw": c, "discharge_mw": d}
    for k in spec.as_products:
        cols[as_award_col(k)] = awards.get(k, np.zeros(inp.T))
    cols["soc_end_mwh"] = soc
    acc = account(spec, inp.rt, c, d, awards, inp.pi)
    energy, as_usd, wear = (acc[n].to_numpy() for n in ("energy_usd", "as_usd", "wear_usd"))
    cols["energy_usd"], cols["as_usd"], cols["wear_usd"] = energy, as_usd, wear
    frame = pd.DataFrame(cols, index=pd.DatetimeIndex(inp.index, name="interval_start_utc"))
    chk = _check_arrays(spec, c, d, soc, awards, soc0, inp.pmax, inp.pmax_c, inp.lo_t, inp.hi_t)
    return _Built(frame, float(energy.sum() + as_usd.sum() - wear.sum()), chk)


def solve(spec: BatterySpec, prices: pd.DataFrame, soc0_mwh: float, *, mode: str, allow_as: bool = True,
          availability: Optional[pd.Series] = None, fixed_as: Optional[pd.DataFrame] = None,
          terminal_soc_mwh: Optional[float] = None, fixed_energy: Optional[pd.DataFrame] = None,
          charge_availability: Optional[pd.Series] = None, charge_limit_mw: Optional[float] = None,
          soc_min_series: Optional[pd.Series] = None, soc_max_series: Optional[pd.Series] = None) -> Plan:
    """Optimise (mode 'pf' or 'forecast') or value given actions (mode 'fixed') through one code path.

    The first seven arguments are the `Optimizer` Protocol; the rest are optional extensions.

    prices            PriceFrame (perfect foresight) or ForecastFrame.price_view("p50"): tz-aware UTC 15-minute
                      index, column rt_usd_per_mwh, optional as_<product>_usd_per_mw_h.
    soc0_mwh          state of charge at the start of the first interval.
    allow_as          False: no ancillary awards (energy only).
    availability      Series of FRACTION of power_mw available per interval (1 = full); NaN is an error.
    fixed_as          DataFrame with as_<product>_mw columns: those awards are held fixed; other products stay free.
    terminal_soc_mwh  optional lower bound for the SOC at the end of the horizon.
    fixed_energy      mode 'fixed' only: DataFrame with charge_mw and discharge_mw (or net_mw, + discharge).
    charge_availability  fraction for the charge side if it differs from `availability`.
    charge_limit_mw   grid-side charge limit when it differs from power_mw (ESRs often have |LSL| != HSL).
    soc_min_series, soc_max_series   per-interval SOC floor / ceiling in MWh (energy derates, e.g. ERCOT's reported
                      Minimum/Maximum SOC); each interval uses the tighter of these and the spec's static bounds.
    Returns a Plan; any status other than "optimal" means the service must not dispatch it.
    """
    t0 = time.perf_counter()
    inp = _prepare(spec, prices, soc0_mwh, mode, allow_as, availability, fixed_as, terminal_soc_mwh, fixed_energy,
                   charge_availability, charge_limit_mw, soc_min_series, soc_max_series)
    sha = _inputs_sha(spec, inp, mode, allow_as, terminal_soc_mwh is not None)
    diag = dict(n_intervals=inp.T, products=list(inp.products), unpriced_products=list(inp.unpriced),
                overlap_intervals=0, overlap_netted=False, milp=False, milp_intervals=0, notes=[])
    zeros = np.zeros(inp.T)
    if mode == "fixed":
        awards = {k: inp.fix_hi[k] for k in inp.products}      # fixed mode: hi == lo == the given award (0 if none)
        built = _assemble(spec, inp, inp.fixed_c, inp.fixed_d, awards, inp.soc0)
        diag["check"] = built.check
        status = "optimal" if built.check["max_violation"] <= TOL else "infeasible"
        return _plan(spec, mode, "fixed", status, built, sha, diag, t0)

    model = _get_model(spec, inp)
    model.set(inp)
    log: list = []
    status, solver = _run(model.prob, [("HIGHS", {}), ("CLARABEL", {})], log)
    if status != "optimal":
        diag["notes"] += log
        return _plan(spec, mode, solver, status, _assemble(spec, inp, zeros, zeros, {}, inp.soc0), sha, diag, t0)
    c, d, awards = _extract(model, inp.T)
    ov = np.minimum(c, d) > OVERLAP_TOL_MW
    diag["overlap_intervals"] = int(ov.sum())
    diag["overlap_max_mw"] = float(np.max(np.minimum(c, d), initial=0.0))
    if ov.any():
        pstar = overlap_price_threshold(spec)
        if (ov & (inp.rt < pstar)).any():
            binary_idx = np.flatnonzero(inp.rt < pstar)
            st2, c2, d2, a2 = _milp(spec, inp, binary_idx)
            diag.update(milp=True, milp_intervals=int(len(binary_idx)))
            if st2 != "optimal":
                diag["notes"].append(f"MILP status {st2}")
                return _plan(spec, mode, "HIGHS-MILP", st2, _assemble(spec, inp, zeros, zeros, {}, inp.soc0),
                             sha, diag, t0)
            c, d, awards, solver = c2, d2, a2, "HIGHS-MILP"
        c, d = _net_overlap(spec, c, d)
        diag["overlap_netted"] = True
    c, d, awards = _enforce_power(inp, c, d, awards)
    built = _assemble(spec, inp, c, d, awards, inp.soc0)
    chk = built.check
    if terminal_soc_mwh is not None:
        chk["terminal_short"] = float(max(terminal_soc_mwh - built.frame["soc_end_mwh"].iloc[-1], 0.0))
        chk["max_violation"] = max(chk["max_violation"], chk["terminal_short"])
    diag["check"] = chk
    status = "optimal" if chk["max_violation"] <= TOL else "numerical_violation"
    return _plan(spec, mode, solver, status, built, sha, diag, t0)


def _enforce_power(inp: _Inputs, c, d, awards):
    """Numerical hygiene for plans the service may dispatch: a solver can return a value a hair (about 1e-9 MW) above a
    power limit, and the guard rejects rather than clips, so the plan is clipped here (and up/down awards scaled to the
    remaining headroom). Changes are far below the plan's own tolerance (1e-6)."""
    c = np.minimum(c, inp.pmax_c)
    d = np.minimum(d, inp.pmax)
    awards = {k: v.copy() for k, v in awards.items()}
    for names, side in (([k for k in awards if k in UP_PRODUCTS], np.maximum(inp.pmax - d, 0.0)),
                        ([k for k in awards if k in DOWN_PRODUCTS], np.maximum(inp.pmax_c - c, 0.0))):
        if names:
            tot = sum(awards[k] for k in names)
            f = np.where(tot > side, side / np.maximum(tot, 1e-300), 1.0)
            for k in names:
                awards[k] = awards[k] * f
    return c, d, awards


def _plan(spec, mode, solver, status, built: _Built, sha, diag, t0) -> Plan:
    diag["solve_s"] = time.perf_counter() - t0
    frame = built.frame
    frame.attrs["diagnostics"] = diag
    obj = built.objective if (status == "optimal" or mode == "fixed") else float("nan")
    return Plan(asset_id=spec.asset_id, mode=mode, solver=solver, status=status, objective_usd=obj,
                inputs_sha256=sha, frame=frame)


def first_setpoint(plan: Plan, *, model_version: str, issued_utc: pd.Timestamp) -> Setpoint:
    """The first interval of an ok plan as a Setpoint (plan_id = first 16 hex of inputs_sha256)."""
    if not plan.ok:
        raise ValueError(f"plan status is {plan.status!r}; no setpoint")
    row = plan.frame.iloc[0]
    as_mw = {c[3:-3].upper(): float(row[c]) for c in plan.frame.columns if c.startswith("as_") and c.endswith("_mw")}
    return Setpoint(asset_id=plan.asset_id, interval_start_utc=plan.frame.index[0],
                    net_mw=float(row["discharge_mw"] - row["charge_mw"]), as_mw=as_mw,
                    plan_id=plan.inputs_sha256[:16], model_version=model_version, issued_utc=issued_utc)


def evaluate(spec: BatterySpec, prices: pd.DataFrame, actions: pd.DataFrame, soc0_mwh: float,
             availability=None, fixed_as: Optional[pd.DataFrame] = None, **kw) -> Plan:
    """Value given actions with the same accounting as the optimiser (mode 'fixed'). `actions` has charge_mw and
    discharge_mw (or net_mw); awards come from `fixed_as`, or from as_<product>_mw columns of `actions`."""
    if fixed_as is None:
        cols = [c for c in actions.columns if c.startswith("as_") and c.endswith("_mw")]
        fixed_as = actions[cols] if cols else None
    return solve(spec, prices, soc0_mwh, mode="fixed", availability=availability, fixed_as=fixed_as,
                 fixed_energy=actions, **kw)
