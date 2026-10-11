"""Ancillary-service (AS) revenue layer. SEPARATE from the arena's energy-only dispatch and labelled as such.

The arena is energy-only (README: "no ancillary services"). Nothing here changes an arena number. This module answers
three questions from ERCOT's AS clearing prices (`derived/as_prices.parquet`, agent 1a):

1. `as_upper_bound(year, spec)`   the most AS can add if ALL of the battery's capacity sits in the best eligible product
                                  in every interval. Eligibility is by duration: a product that must be sustained for D
                                  hours can take at most min(1, energy_mwh / (power_mw * D)) of the capacity.
2. `as_vs_energy(...)`            a simple, documented co-optimisation estimate for a given dispatch ledger (A1-A5 or the
                                  learner): each interval of the battery's day either trades energy (the ledger) or sells
                                  AS, and a swap happens when AS pays more than the energy slot. See the function.
3. `oracle_with_as(...)`          a perfect-foresight co-optimisation by dynamic programming (no LP), the AS counterpart of
                                  the arena's `oracle_days`: the ceiling on energy + AS together under state-of-charge and
                                  power-headroom limits. `as_oracle_uplift` reports it next to the energy-only ceiling.

Markets. `DAM` is the day-ahead clearing price for capacity (hourly, 2010-12 onward). `RT` is the real-time clearing
price for capacity at 15-minute settlement intervals, which exists only from 5 Dec 2025 (RTC+B). Prices are system-wide
in ERCOT, so AS value varies by site only through what the site's energy dispatch leaves free.

Units. Prices $/MW-h of capacity. Value $/kW-yr per kW of battery, the arena's unit. Partial years are annualised
(mean value per hour x 8,760 h) and flagged.
"""
from __future__ import annotations

import numpy as np

from . import paths

paths.use_arena()
from arena import config as C  # noqa: E402
from arena.battery import NEG, Spec, oracle_days  # noqa: E402

from . import specs as _specs  # noqa: E402

# --------------------------------------------------------------------------- constants (label: PLAN.md)
AS_PRICES_FILE = paths.DERIVED / "as_prices.parquet"   # REAL: ERCOT DAM/RT clearing prices for capacity (agent 1a)
MARKET_KEY = {"DAM": "DAM", "RT": "RT_15"}              # DERIVED: RT = the 15-minute settlement-interval prices
PRODUCTS = ("REGUP", "REGDN", "RRS", "ECRS", "NSPIN")   # REAL: ERCOT AS products in the price file
UP_PRODUCTS = ("REGUP", "RRS", "ECRS", "NSPIN")         # REAL: discharge-side products (need stored energy)
DOWN_PRODUCTS = ("REGDN",)                              # REAL: charge-side product (needs room to absorb energy)
RTCB_GO_LIVE = "2025-12-05"                             # REAL: ERCOT news release 5 Dec 2025 (notes/rules.md (b))
# Sustain durations in hours. REAL for Reg/RRS/ECRS from NPRR1282 (Board 24 Jun 2025, PUCT 31 Jul 2025): Reg and RRS
# 30 min (were 15), ECRS 1 h (was 2). Non-Spin "4 hours or more": UNVERIFIED final number in the 1 Aug 2026 protocol text.
AS_DURATION_H_RTCB = {"REGUP": 0.5, "REGDN": 0.5, "RRS": 0.5, "ECRS": 1.0, "NSPIN": 4.0}
AS_DURATION_H_PRE = {"REGUP": 0.25, "REGDN": 0.25, "RRS": 0.25, "ECRS": 2.0, "NSPIN": 4.0}   # REAL (NPRR1282 "was"); NSPIN UNVERIFIED
HOURS_PER_YEAR = 8760.0                                 # DERIVED: 365 x 24, used to annualise partial years
DT_H = C.DT                                             # REAL: arena interval, 0.25 h
POWER_PER_UNIT_MW = C.U / C.DT                          # DERIVED: one state unit per interval = 0.5 MW of a 1 MW battery
SPEC_KEYS = ("grid", "grid4h")                          # the 2 h and 4 h merchant specs
CSV_NAME = "as_revenue.csv"


def _hours_of(spec):
    """Usable energy per MW of power in hours: (energy units - reserve units) x U. Accepts a spec key or arena Spec."""
    if isinstance(spec, str):
        _specs.register()
        return _specs.hours(spec)
    return (spec.E - spec.R) * C.U


def _spec_obj(spec):
    if isinstance(spec, str):
        _specs.register()
        return Spec(spec)
    return spec


def durations_on(dates):
    """{product: (n_days,) hours} using the pre-RTC+B or RTC+B sustain times by date."""
    post = (np.asarray(dates).astype(str) >= RTCB_GO_LIVE)
    return {p: np.where(post, AS_DURATION_H_RTCB[p], AS_DURATION_H_PRE[p]) for p in PRODUCTS}


def eligible_fraction(energy_hours, duration_h):
    """Largest share of the capacity a product with sustain time `duration_h` may take: min(1, E / (P x D)), P = 1 MW."""
    return np.minimum(1.0, energy_hours / np.asarray(duration_h, float))


# --------------------------------------------------------------------------- the price grid
class AsGrid:
    """AS clearing prices on the arena's clock: for each CPT day, 96 fifteen-minute intervals per product.

    Hourly DAM prices are held for the four intervals of their hour. The repeated fall-back hour is dropped and a skipped
    spring-forward hour repeats the hour before (the arena's DST rule). A product with no price on a day (ECRS before
    10 Jun 2023, anything before the market existed) is NaN there.
    """

    _CACHE = {}

    def __init__(self, market, dates, price):
        self.market, self.dates, self.price = market, dates, price
        self.index = {d: i for i, d in enumerate(dates)}

    @classmethod
    def load(cls, market="DAM", path=None):
        key = (market, str(path or AS_PRICES_FILE))
        if key in cls._CACHE:
            return cls._CACHE[key]
        import pandas as pd
        if market not in MARKET_KEY:
            raise ValueError(f"market must be one of {list(MARKET_KEY)}")
        df = pd.read_parquet(path or AS_PRICES_FILE, columns=["market", "product", "date_cpt", "i15", "mcpc", "dst_rep"],
                             filters=[("market", "==", MARKET_KEY[market])])
        df = df[~df["dst_rep"]]
        dates, inv = np.unique(df["date_cpt"].to_numpy().astype(str), return_inverse=True)
        slot = (df["i15"].to_numpy() // 4) if market == "DAM" else df["i15"].to_numpy()
        n_slot = 24 if market == "DAM" else C.NI
        price = {}
        for p in PRODUCTS:
            m = (df["product"].to_numpy() == p)
            arr = np.full((len(dates), n_slot), np.nan)
            arr[inv[m], slot[m]] = df["mcpc"].to_numpy()[m]
            price[p] = cls._fill_days(arr)
            if market == "DAM":
                price[p] = np.repeat(price[p], 4, axis=1)
        out = cls(market, dates, price)
        cls._CACHE[key] = out
        return out

    @staticmethod
    def _fill_days(arr):
        """Inside each day that has at least one price, fill gaps with the slot before (the first slot with the next)."""
        out = arr.copy()
        has = np.isfinite(arr).any(1)
        for d in np.flatnonzero(has & ~np.isfinite(arr).all(1)):
            row = out[d]
            ok = np.isfinite(row)
            idx = np.where(ok, np.arange(row.size), 0)
            np.maximum.accumulate(idx, out=idx)
            idx[:np.flatnonzero(ok)[0]] = np.flatnonzero(ok)[0]
            out[d] = row[idx]
        return out

    def for_dates(self, dates):
        """({product: (len(dates), 96)}, has) with NaN rows for dates the file does not cover; `has` marks covered days."""
        dates = np.asarray(dates).astype(str)
        ix = np.array([self.index.get(d, -1) for d in dates])
        out = {}
        for p in PRODUCTS:
            a = np.full((len(dates), C.NI), np.nan)
            ok = ix >= 0
            a[ok] = self.price[p][ix[ok]]
            out[p] = a
        has = ix >= 0
        return out, has


# --------------------------------------------------------------------------- 1. upper bound
def _best_value(prices, frac, products=PRODUCTS, stack_regdn=False):
    """Per-interval $/MW-h of the best eligible product: max over products of fraction x price (NaN products ignored).

    stack_regdn=True adds the charge-side product on top of the best discharge-side one (Reg Down uses the other half
    of the inverter's range). Returns (value, has_price) with value 0 where nothing is priced."""
    shape = next(iter(prices.values())).shape
    best = np.full(shape, -np.inf)
    use = UP_PRODUCTS if stack_regdn else products
    for p in use:
        v = prices[p] * frac[p][:, None]
        best = np.fmax(best, np.where(np.isfinite(v), v, -np.inf))
    has = np.isfinite(best)
    val = np.where(has, best, 0.0)
    if stack_regdn:
        v = prices["REGDN"] * frac["REGDN"][:, None]
        has = has | np.isfinite(v)
        val = val + np.where(np.isfinite(v), v, 0.0)
    return val, has


def _fractions(spec, dates, products=PRODUCTS):
    h = _hours_of(spec)
    dur = durations_on(dates)
    return {p: eligible_fraction(h, dur[p]) for p in products}


def as_upper_bound(year, spec="grid", market="DAM", *, stack_regdn=False, grid=None):
    """Upper bound on AS revenue for one calendar year, $/kW-yr: all capacity in the best eligible product each interval.

    Per interval the value is max over products of  min(1, E/(P D_p)) x MCPC_p  in $/MW-h, summed over the year's
    intervals x 0.25 h. `spec` is "grid" (1 MW / 2 MWh), "grid4h" (1 MW / 4 MWh) or an arena Spec.
    stack_regdn=True also sells Reg Down on the charge side at full MW on top of the best discharge-side product
    (ASSUMPTION: base point near zero, state of charge mid-range) - a looser bound.
    Returns a dict:  usd_kw_yr (annualised to 8,760 h), usd_kw_covered (as realised over the covered hours), n_hours,
    coverage (share of the year priced), partial (bool), by_product (single-product bounds, $/kW-yr), best_share (how
    often each product was the best), mean_best_usd_mw_h, top1pct_share (share of the bound earned in the top 1% of
    intervals: how spike-driven it is)."""
    g = grid or AsGrid.load(market)
    dates = np.array([d for d in g.dates if d[:4] == str(year)])
    if len(dates) == 0:
        raise ValueError(f"no {market} AS prices in {year}")
    prices, has_day = g.for_dates(dates)
    frac = _fractions(spec, dates)
    val, has = _best_value(prices, frac, stack_regdn=stack_regdn)
    n_int = int(has.sum())
    hours = n_int * DT_H
    year_hours = (366 if (int(year) % 4 == 0 and (int(year) % 100 != 0 or int(year) % 400 == 0)) else 365) * 24
    mean_h = float(val[has].sum() * DT_H / hours) if hours else 0.0                  # $/MW-h averaged over priced hours
    by_p = {}
    for p in PRODUCTS:
        v = prices[p] * frac[p][:, None]
        m = np.isfinite(v)
        by_p[p] = float(v[m].sum() * DT_H / (m.sum() * DT_H) * HOURS_PER_YEAR / 1000.0) if m.any() else float("nan")
    best_share = {}
    if not stack_regdn:
        stack = np.stack([np.where(np.isfinite(prices[p] * frac[p][:, None]), prices[p] * frac[p][:, None], -np.inf)
                          for p in PRODUCTS], 0)
        arg = stack.argmax(0)[has]
        best_share = {p: float((arg == i).mean()) for i, p in enumerate(PRODUCTS)}
    flat = np.sort(val[has])[::-1]
    k = max(1, int(round(0.01 * flat.size)))
    top = float(flat[:k].sum() / flat.sum()) if flat.sum() > 0 else float("nan")
    return {"year": int(year), "market": market, "spec": spec if isinstance(spec, str) else spec.key,
            "usd_kw_yr": mean_h * HOURS_PER_YEAR / 1000.0, "usd_kw_covered": float(val[has].sum() * DT_H / 1000.0),
            "n_hours": hours, "coverage": hours / year_hours, "partial": hours < 0.98 * year_hours,
            "by_product": by_p, "best_share": best_share, "mean_best_usd_mw_h": mean_h, "top1pct_share": top,
            "stack_regdn": stack_regdn}


# --------------------------------------------------------------------------- 2. co-optimisation estimate from a ledger
def busy_footprint(prices, mwh):
    """Where in the day a battery that discharged `mwh` MWh (per MW) was busy, from the day's price ranking.

    Full-power equivalent intervals: n = mwh / (2 U) discharging at the dearest intervals and the same number charging
    at the cheapest (the arena books the charge loss on the grid side, so stored MWh in equals MWh out). Returns
    (dis_w, chg_w), (days, 96) weights in [0, 1]; the interval at the boundary of a block carries the fractional part.
    This is the same idealisation as a perfect-foresight schedule (it ignores charge-before-discharge ordering), used only
    to decide which intervals the dispatch occupied."""
    P = np.asarray(prices, float)
    n = np.asarray(mwh, float) / (2.0 * C.U)
    order = np.argsort(P, axis=1, kind="stable")
    rank_low = np.empty_like(order)
    np.put_along_axis(rank_low, order, np.arange(P.shape[1])[None, :].repeat(P.shape[0], 0), axis=1)
    rank_high = P.shape[1] - 1 - rank_low
    dis_w = np.clip(n[:, None] - rank_high, 0.0, 1.0)
    chg_w = np.clip(n[:, None] - rank_low, 0.0, 1.0)
    over = dis_w + chg_w > 1.0                                  # a day so active that the blocks meet: split the overlap
    scale = np.where(over, 1.0 / np.maximum(dis_w + chg_w, 1e-12), 1.0)
    return dis_w * scale, chg_w * scale


def _soc_state(dis_w, chg_w):
    """+1 where the battery holds energy (the last busy block before the interval was a charge), -1 where it is empty
    (a discharge, or nothing yet: the arena's batteries start each simulation empty). Strictly before the interval."""
    ev = np.where(dis_w >= 0.5, -1, np.where(chg_w >= 0.5, 1, 0))
    D, T = ev.shape
    idx = np.where(ev != 0, np.arange(T)[None, :], -1)
    np.maximum.accumulate(idx, axis=1, out=idx)                       # index of the last busy interval up to t, or -1
    last = np.take_along_axis(ev, np.maximum(idx, 0), 1)
    last = np.where(idx >= 0, last, 0)                                # 0 = no busy block yet today
    prev = np.concatenate([np.zeros((D, 1), int), last[:, :-1]], 1)   # shift: the state going INTO each interval
    # a day's first intervals (before its first busy block) inherit the state the day before ended in; a day with no busy
    # block at all passes its own inherited state on. The very first day starts empty (-1).
    end = last[:, -1]
    carry = np.empty(D, int)
    state = -1
    for d in range(D):
        carry[d] = state
        if end[d] != 0:
            state = end[d]
    return np.where(prev == 0, carry[:, None], prev)


def as_vs_energy(prices, energy, wear, mwh, dates, spec="grid", market="DAM", *, soc_aware=True, stack_regdn=False,
                 grid=None):
    """Co-optimisation estimate for one lane and one dispatch ledger. ASSUMPTION-grade, documented here.

    Inputs: `prices` (days, 96) the lane's energy prices (used only to place the busy intervals); `energy`, `wear`, `mwh`
    (days,) the ledger of one algorithm on that lane, $ and MWh per MW; `dates` ISO CPT days; `spec` as above.

    Model. In every interval the battery's 1 MW does one thing:
      * busy   the dispatch trades energy there (the dearest `mwh/0.25` intervals discharge, the same number of the
               cheapest charge: busy_footprint). Its energy value per busy interval is e = max(0, (energy - wear) / busy);
      * idle   the capacity is free and sells AS.
    Idle intervals sell the best product the state of charge allows (soc_aware=True): if the last busy block before it was a
    charge the battery holds energy and may sell any discharge-side product (Reg Up, RRS, ECRS, Non-Spin) up to its
    duration share; if it was a discharge the battery is empty and may only sell Reg Down. soc_aware=False lets every idle
    interval sell the best product, an optimistic bound. A busy interval switches to AS when AS pays more than e (a swap),
    so total = ledger profit + AS in idle intervals + max(0, AS - e) in busy ones, per interval.
    The ledger's own profit is never reduced, so the estimate is a floor on what co-optimising the same battery would do.

    Returns {"by_year": {year: {energy, as_idle, as_swap, total, as_total, coverage, partial}} in $/kW-yr, "days": {...}}.
    """
    P = np.asarray(prices, float)
    dates = np.asarray(dates).astype(str)
    g = grid or AsGrid.load(market)
    ap, has_day = g.for_dates(dates)
    fr = _fractions(spec, dates)
    dis_w, chg_w = busy_footprint(P, mwh)
    busy = np.minimum(dis_w + chg_w, 1.0)
    idle = 1.0 - busy
    up_val, up_has = _best_value(ap, fr, UP_PRODUCTS)
    dn = ap["REGDN"] * fr["REGDN"][:, None]
    dn_has = np.isfinite(dn)
    dn_val = np.where(dn_has, dn, 0.0)
    best_val, best_has = _best_value(ap, fr, PRODUCTS, stack_regdn=stack_regdn)
    if soc_aware:
        full = _soc_state(dis_w, chg_w) > 0
        a_idle = np.where(full, up_val, dn_val)
        if stack_regdn:
            a_idle = np.where(full, up_val + dn_val, dn_val)
    else:
        a_idle = best_val
    n_busy = busy.sum(1)
    e_day = np.asarray(energy, float) - np.asarray(wear, float)
    e_slot = np.where(n_busy > 1e-9, np.maximum(e_day / np.maximum(n_busy, 1e-9), 0.0), 0.0)        # $/MW per busy interval
    as_busy = best_val * DT_H                                                                          # $/MW per interval
    as_idle_day = (idle * a_idle).sum(1) * DT_H
    swap_day = (busy * np.maximum(as_busy - e_slot[:, None], 0.0)).sum(1)
    covered = has_day.astype(float)
    as_idle_day, swap_day = as_idle_day * covered, swap_day * covered
    years = np.array([int(d[:4]) for d in dates])
    out = {}
    for y in sorted(set(years)):
        s = years == y
        n = int(s.sum())
        scale = (365.0 / n if n < 360 else 1.0) / 1000.0
        cov = float(covered[s].mean())
        e_y = float(e_day[s].sum() * scale)
        # AS only exists on covered days: annualise the AS terms over the covered days only
        nc = max(int(covered[s].sum()), 1)
        sc_as = (365.0 / nc if nc < 360 else 1.0) / 1000.0
        a_y, s_y = float(as_idle_day[s].sum() * sc_as), float(swap_day[s].sum() * sc_as)
        out[int(y)] = {"energy": e_y, "as_idle": a_y, "as_swap": s_y, "as_total": a_y + s_y, "total": e_y + a_y + s_y,
                       "coverage": cov, "partial": n < 360 or cov < 0.98}
    return {"by_year": out, "days": {"as_idle": as_idle_day, "as_swap": swap_day, "busy_intervals": n_busy}}


# --------------------------------------------------------------------------- 3. perfect-foresight co-optimisation (DP)
def oracle_with_as(P, dates, spec="grid", market="DAM", *, stack_regdn=True, grid=None, day_block=200):
    """Best energy + AS profit per day with full knowledge of the day's prices, $ per MW per day (no LP).

    The arena's `oracle_days` DP plus an AS reward. After choosing an energy action a (units per interval, +discharge) the
    battery sits at state of charge s'. Its discharge headroom is 1 - 0.5 max(a,0) MW and its charge headroom
    1 - 0.5 max(-a,0) MW. A discharge-side product p can then take q_p = min(headroom, (s'-R) U / D_p) MW (it must be
    deliverable for D_p hours from the energy above the reserve); Reg Down takes min(headroom, (E-s') U / D_dn) MW. The
    interval earns 0.25 h x [max_p q_p price_p  +  q_dn price_dn]. Energy prices and AS prices are known in advance, so this is
    a ceiling like the energy oracle. With every AS price zero it equals oracle_days exactly (tested).
    stack_regdn=False drops the Reg Down term (one product at a time).
    P: (days, 96). Returns (days,)."""
    P = np.asarray(P, float)
    dates = np.asarray(dates).astype(str)
    sp = _spec_obj(spec)
    g = grid or AsGrid.load(market)
    ap, has_day = g.for_dates(dates)
    dur = durations_on(dates)
    out = np.zeros(len(dates))
    for a in range(0, len(dates), day_block):
        sl = slice(a, a + day_block)
        pr = {p: np.nan_to_num(ap[p][sl], nan=0.0) for p in PRODUCTS}
        out[sl] = _oracle_as_block(P[sl], pr, {p: dur[p][sl] for p in PRODUCTS}, sp, stack_regdn)
    return out


def _oracle_as_block(P, pr, dur, sp, stack_regdn):
    D = P.shape[0]
    R, S, Cc = sp.R, sp.E + 1, sp.cap + 1
    V = np.full((D, S, Cc), NEG)
    V[:, R, 0] = 0.0
    s = np.arange(S)
    u = np.maximum(s - R, 0) * C.U                       # MWh above the reserve at s'
    v = np.maximum(sp.E - s, 0) * C.U                    # MWh of room to the top at s'
    for t in range(C.NI):
        p = P[:, t][:, None, None]
        ras = {}
        for a in (-2, -1, 0, 1, 2):
            hr_up = 1.0 - max(a, 0) * POWER_PER_UNIT_MW
            hr_dn = 1.0 - max(-a, 0) * POWER_PER_UNIT_MW
            up = np.zeros((D, S))
            for prod in UP_PRODUCTS:
                q = np.minimum(hr_up, u[None, :] / dur[prod][:, None])
                up = np.maximum(up, q * pr[prod][:, t][:, None])
            tot = up
            if stack_regdn:
                tot = up + np.minimum(hr_dn, v[None, :] / dur["REGDN"][:, None]) * pr["REGDN"][:, t][:, None]
            ras[a] = DT_H * tot
        new = V + ras[0][:, :, None]
        for k in (1, 2):
            cand = V[:, R:S - k, :] - p * k * C.U / sp.rte + ras[-k][:, R + k:S][:, :, None]
            new[:, R + k:S, :] = np.maximum(new[:, R + k:S, :], cand)
            cand = V[:, R + k:S, :Cc - k] + (p - sp.deg) * k * C.U + ras[k][:, R:S - k][:, :, None]
            new[:, R:S - k, k:] = np.maximum(new[:, R:S - k, k:], cand)
        V = new
    return V.reshape(D, -1).max(axis=1)


def as_oracle_uplift(P, dates, spec="grid", market="DAM", *, stack_regdn=True, grid=None):
    """Perfect-foresight energy-only ceiling, energy + AS ceiling and the AS uplift per calendar year, $/kW-yr.

    P (days, 96) for one lane. Only days with AS prices are counted (so the two ceilings cover the same days)."""
    dates = np.asarray(dates).astype(str)
    g = grid or AsGrid.load(market)
    _, has = g.for_dates(dates)
    P, dates = np.asarray(P)[has], dates[has]
    sp = _spec_obj(spec)
    e0 = oracle_days(P, sp)
    e1 = oracle_with_as(P, dates, sp, market, stack_regdn=stack_regdn, grid=g)
    years = np.array([int(d[:4]) for d in dates])
    out = {}
    for y in sorted(set(years)):
        s = years == y
        n = int(s.sum())
        k = (365.0 / n if n < 360 else 1.0) / 1000.0
        out[int(y)] = {"energy_oracle": float(e0[s].sum() * k), "energy_plus_as_oracle": float(e1[s].sum() * k),
                       "as_uplift": float((e1[s] - e0[s]).sum() * k), "days": n, "partial": n < 360}
    return out


# --------------------------------------------------------------------------- the data/as_revenue.csv table
CSV_COLUMNS = ["year", "market", "product", "n_hours", "coverage", "mean_mcpc_usd_mw_h", "p95_mcpc_usd_mw_h", "duration_h",
               "elig_2h", "elig_4h", "ub_2h_usd_kw_yr", "ub_4h_usd_kw_yr", "partial_year", "note"]
BEST = "BEST_EACH_INTERVAL"
BEST_STACKED = "BEST_PLUS_REGDN"
YEARS = {"DAM": range(2010, 2027), "RT": range(2025, 2027)}   # DERIVED: years with any price in as_prices.parquet (DAM from Dec 2010, RT from 5 Dec 2025)


def as_revenue_rows():
    """One row per (year, market, product) plus the two best-product bounds; see the labels sidecar for the columns."""
    rows = []
    h2, h4 = _hours_of("grid"), _hours_of("grid4h")
    for market, years in YEARS.items():
        g = AsGrid.load(market)
        for y in years:
            dates = np.array([d for d in g.dates if d[:4] == str(y)])
            if len(dates) == 0:
                continue
            prices, _ = g.for_dates(dates)
            dur_now = AS_DURATION_H_RTCB if f"{y}-07-01" >= RTCB_GO_LIVE else AS_DURATION_H_PRE      # in force mid-year
            for p in PRODUCTS:
                x = prices[p]
                m = np.isfinite(x)
                if not m.any():
                    continue
                hours = m.sum() * DT_H
                year_h = (366 if y % 4 == 0 else 365) * 24
                mean = float(x[m].mean())
                d_h = float(dur_now[p])
                e2, e4 = float(eligible_fraction(h2, d_h)), float(eligible_fraction(h4, d_h))
                rows.append(dict(year=y, market=market, product=p, n_hours=hours, coverage=hours / year_h,
                                 mean_mcpc_usd_mw_h=mean, p95_mcpc_usd_mw_h=float(np.percentile(x[m], 95)),
                                 duration_h=d_h, elig_2h=e2, elig_4h=e4,
                                 ub_2h_usd_kw_yr=mean * e2 * HOURS_PER_YEAR / 1000.0,
                                 ub_4h_usd_kw_yr=mean * e4 * HOURS_PER_YEAR / 1000.0,
                                 partial_year=hours < 0.98 * year_h, note="all capacity in this one product, all hours"))
            for name, stack in ((BEST, False), (BEST_STACKED, True)):
                u2 = as_upper_bound(y, "grid", market, stack_regdn=stack, grid=g)
                u4 = as_upper_bound(y, "grid4h", market, stack_regdn=stack, grid=g)
                rows.append(dict(year=y, market=market, product=name, n_hours=u2["n_hours"], coverage=u2["coverage"],
                                 mean_mcpc_usd_mw_h=u4["mean_best_usd_mw_h"],
                                 p95_mcpc_usd_mw_h=float("nan"), duration_h=float("nan"), elig_2h=float("nan"),
                                 elig_4h=float("nan"), ub_2h_usd_kw_yr=u2["usd_kw_yr"], ub_4h_usd_kw_yr=u4["usd_kw_yr"],
                                 partial_year=u2["partial"],
                                 note=("best discharge-side product each interval + Reg Down on the charge side (loose bound)"
                                       if stack else "best eligible product each interval (the as_upper_bound definition)")))
    return rows


_SRC_PRICES = "ERCOT DAM / RT clearing prices for capacity (MCPC), research-data/grid/derived/as_prices.parquet (agent 1a)"
LABELS = [
    ("year", "REAL", _SRC_PRICES, "calendar year (CPT). 2026 is partial (to 10 Oct 2026); the RT market starts 5 Dec 2025, so RT 2025 covers 27 days"),
    ("market", "REAL", _SRC_PRICES, "DAM = day-ahead hourly price; RT = 15-minute settlement-interval price (RTC+B era only)"),
    ("product", "REAL", _SRC_PRICES, "REGUP REGDN RRS ECRS NSPIN, or a best-product bound: BEST_EACH_INTERVAL = best eligible product in every interval; BEST_PLUS_REGDN = the same plus Reg Down on the charge side"),
    ("n_hours", "DERIVED", "count of priced intervals x 0.25 h", "hours with a price in the file for this product (ECRS exists from 10 Jun 2023)"),
    ("coverage", "DERIVED", "n_hours / hours in the calendar year", "below 1 for partial years and for products that started mid-year"),
    ("mean_mcpc_usd_mw_h", "REAL", _SRC_PRICES, "mean clearing price, $/MW-h of capacity. BEST rows: mean of the per-interval maximum over products for the 4 h battery (all products eligible)"),
    ("p95_mcpc_usd_mw_h", "REAL", _SRC_PRICES, "95th percentile of the clearing price; compare with the mean to see how spike-driven the product is"),
    ("duration_h", "REAL", "NPRR1282 (Board 24 Jun 2025, PUCT 31 Jul 2025); notes/rules.md (b)", "hours the product must be sustained, in force on 1 Jul of the year: Reg and RRS 15 min before RTC+B and 30 min after, ECRS 2 h then 1 h, Non-Spin 4 h (the final Non-Spin number is UNVERIFIED)"),
    ("elig_2h", "DERIVED", "min(1, 2 MWh / (1 MW x duration_h))", "share of a 1 MW / 2 MWh battery that may sell the product"),
    ("elig_4h", "DERIVED", "min(1, 4 MWh / (1 MW x duration_h))", "share of a 1 MW / 4 MWh battery that may sell the product"),
    ("ub_2h_usd_kw_yr", "DERIVED", "mean x eligibility x 8,760 h / 1000", "UPPER BOUND for a 2 h battery in $/kW-yr, annualised for partial years. Not a forecast: no state-of-charge conflicts with energy trading, no price impact from the battery's own awards, no saturation by other batteries"),
    ("ub_4h_usd_kw_yr", "DERIVED", "same", "UPPER BOUND for a 4 h battery in $/kW-yr, same caveats"),
    ("partial_year", "DERIVED", "coverage < 98%", "annualised from a part of the year (seasonal bias possible)"),
    ("note", "DERIVED", "ancillary.py", "what the row bounds"),
]


def write_as_revenue_csv(path=None):
    import pandas as pd
    path = path or (paths.REPO_DATA / CSV_NAME)
    df = pd.DataFrame(as_revenue_rows(), columns=CSV_COLUMNS)
    for c in ("n_hours", "coverage", "mean_mcpc_usd_mw_h", "p95_mcpc_usd_mw_h", "elig_2h", "elig_4h", "ub_2h_usd_kw_yr",
              "ub_4h_usd_kw_yr"):
        df[c] = df[c].round(4)
    df.to_csv(path, index=False)
    lab = pd.DataFrame(LABELS, columns=["column", "label", "source", "note"])
    lab.to_csv(Path_with_labels(path), index=False)
    return df


def Path_with_labels(path):
    from pathlib import Path
    p = Path(path)
    return p.with_name(p.stem + ".labels.csv")
