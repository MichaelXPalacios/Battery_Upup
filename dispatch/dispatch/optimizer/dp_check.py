"""Cross-check of the platform LP against the research arena's perfect-foresight dynamic programme.

The arena (docs/research/arena/arena/battery.py, `oracle_days`) solves an energy-only day exactly by dynamic programming
over a state of charge discretised in 0.125 MWh units, with five actions per interval (-2..+2 units), losses on the
charge side and a $20/MWh wear cost on discharge. For the same battery and the same prices, the LP in
`dispatch.optimizer.lp` must earn at least as much (the DP's action set is a subset of the LP's), and as the LP's vertices
sit on the same grid it should earn almost exactly the same. `compare` measures that on historical zonal days.

Two LP variants are compared with the DP, because the arena and the platform define the power limit differently:

* ``arena_basis``  charge limit = P / rte (grid side), so the STORED energy per interval is capped at P*dt exactly as in
  the arena (the arena counts its action in stored units: -2 stores 0.25 MWh and draws 0.25/rte MWh from the grid).
  This is the like-for-like check and the one the test asserts.
* ``ac_basis``     charge limit = P on the grid side, which is what the platform guard enforces (|net_mw| <= power_mw).
  It earns slightly less on days when the charge rate binds; the gap is reported, not hidden.

Run ``python -m dispatch.optimizer.dp_check`` from hugging-base/dispatch (prints the distribution and writes a CSV to
reports/case_study/dp_crosscheck.csv).
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Optional

import numpy as np
import pandas as pd

from .. import paths
from ..contracts import BatterySpec
from . import lp

TZ_MARKET = "America/Chicago"


def _arena():
    paths.use_arena()
    import arena.battery as battery
    import arena.config as config
    import arena.data as data
    return battery, config, data


def arena_battery_spec(key: str = "grid", settlement_point: str = "HB_BUSAVG") -> BatterySpec:
    """The arena's SPECS[key] as a platform BatterySpec, per MW of inverter power (1 MW), energy only."""
    battery, config, _ = _arena()
    d = config.SPECS[key]
    return BatterySpec(asset_id=f"arena-{key}", settlement_point=settlement_point, power_mw=1.0,
                       energy_mwh=d["energy_units"] * config.U, soc_min_mwh=d["reserve_units"] * config.U,
                       rte=d["rte"], loss_side="charge", wear_usd_per_mwh=d["deg_usd_mwh"],
                       max_cycles_per_day=d["cycles_per_day"], as_products=())


def load_zonal_days():
    """(dates 'YYYY-MM-DD', zone names, P[days, zones, 96] $/MWh) from the arena's cached ERCOT RT zonal SPP."""
    _, _, data = _arena()
    dates, zones, P = data.ercot_prices()
    return [str(x) for x in dates], [str(z) for z in zones], np.asarray(P, float)


def day_index(date: str) -> pd.DatetimeIndex:
    """96 consecutive 15-minute intervals starting at 00:00 Central on `date`, as UTC."""
    start = pd.Timestamp(str(date)).tz_localize(TZ_MARKET).tz_convert("UTC")
    return pd.date_range(start, periods=96, freq="15min")


def sample_days(n: int, seed: int = 20261010, include_extremes: bool = True):
    """n (date index, zone index) pairs: uniform over all days and zones, plus the highest-value and the negative-price
    extremes when asked (they stress the cycle cap, the SOC bounds and the negative-price logic)."""
    dates, zones, P = load_zonal_days()
    rng = np.random.default_rng(seed)
    picks = []
    if include_extremes:
        span = P.max(axis=2) - P.min(axis=2)                          # (days, zones) intraday spread
        flat = np.argsort(span, axis=None)[::-1][: max(n // 10, 2)]
        picks += [tuple(np.unravel_index(i, span.shape)) for i in flat]
        neg = np.argwhere(P.min(axis=2) < -50)
        if len(neg):
            sel = neg[rng.choice(len(neg), size=min(max(n // 10, 2), len(neg)), replace=False)]
            picks += [tuple(x) for x in sel]
    seen = set(picks)
    while len(picks) < n:
        k = (int(rng.integers(len(dates))), int(rng.integers(len(zones) - 1)))   # not HB_BUSAVG, a reference series
        if k not in seen:
            seen.add(k)
            picks.append(k)
    return dates, zones, P, picks[:n]


@dataclass
class DayResult:
    date: str
    zone: str
    dp_usd: float
    lp_arena_usd: float
    lp_ac_usd: float
    overlap_intervals: int
    milp: bool
    min_price: float
    solve_s: float


def lp_day(spec: BatterySpec, prices96: np.ndarray, date: str, charge_limit_mw: Optional[float] = None):
    idx = day_index(date)
    pf = pd.DataFrame({"rt_usd_per_mwh": np.asarray(prices96, float)}, index=idx)
    return lp.solve(spec, pf, spec.soc_min_mwh, mode="pf", allow_as=False, charge_limit_mw=charge_limit_mw)


def compare(n_days: int = 60, seed: int = 20261010, key: str = "grid") -> pd.DataFrame:
    """LP vs DP revenue per day ($ per MW-day) for n_days sampled zone-days."""
    battery, config, _ = _arena()
    aspec = battery.Spec(key)
    spec = arena_battery_spec(key)
    dates, zones, P, picks = sample_days(n_days, seed)
    block = np.stack([P[d, z] for d, z in picks])                     # (n, 96)
    dp = battery.oracle_days(block, aspec)                            # exact DP, vectorised over days
    rows = []
    for (d, z), dp_usd, p96 in zip(picks, dp, block):
        t0 = time.perf_counter()
        a = lp_day(spec, p96, dates[d], charge_limit_mw=spec.power_mw / spec.rte)
        b = lp_day(spec, p96, dates[d])
        if not (a.ok and b.ok):
            raise RuntimeError(f"LP not optimal on {dates[d]} {zones[z]}: {a.status} / {b.status}")
        dg = a.frame.attrs["diagnostics"]
        rows.append(DayResult(dates[d], zones[z], float(dp_usd), a.objective_usd, b.objective_usd,
                              int(dg["overlap_intervals"]), bool(dg["milp"]), float(p96.min()),
                              time.perf_counter() - t0))
    return pd.DataFrame([r.__dict__ for r in rows])


def summarize(df: pd.DataFrame, tol_usd: float = 1e-6) -> dict:
    """Distribution of LP minus DP (arena basis and AC basis), in $ per MW-day and as a share of the DP value."""
    d_a = df["lp_arena_usd"] - df["dp_usd"]
    d_b = df["lp_ac_usd"] - df["dp_usd"]
    rel_a = d_a / df["dp_usd"].where(df["dp_usd"] > 1e-9)
    rel_b = d_b / df["dp_usd"].where(df["dp_usd"] > 1e-9)
    q = [0, 0.01, 0.05, 0.5, 0.95, 0.99, 1.0]
    return dict(
        n_days=len(df), n_zones=df["zone"].nunique(),
        dp_total_usd=float(df["dp_usd"].sum()), lp_arena_total_usd=float(df["lp_arena_usd"].sum()),
        lp_ac_total_usd=float(df["lp_ac_usd"].sum()),
        arena_min_diff=float(d_a.min()), arena_max_diff=float(d_a.max()), arena_mean_diff=float(d_a.mean()),
        arena_quantiles={f"q{int(x * 100)}": float(d_a.quantile(x)) for x in q},
        arena_rel_quantiles={f"q{int(x * 100)}": float(rel_a.quantile(x)) for x in q},
        arena_share_below_dp=float((d_a < -tol_usd).mean()), arena_share_equal=float((d_a.abs() <= tol_usd).mean()),
        arena_total_rel_diff=float((df["lp_arena_usd"].sum() - df["dp_usd"].sum()) / df["dp_usd"].sum()),
        ac_min_diff=float(d_b.min()), ac_max_diff=float(d_b.max()),
        ac_quantiles={f"q{int(x * 100)}": float(d_b.quantile(x)) for x in q},
        ac_rel_quantiles={f"q{int(x * 100)}": float(rel_b.quantile(x)) for x in q},
        ac_total_rel_diff=float((df["lp_ac_usd"].sum() - df["dp_usd"].sum()) / df["dp_usd"].sum()),
        days_with_overlap=int((df["overlap_intervals"] > 0).sum()), days_with_milp=int(df["milp"].sum()),
        median_solve_s=float(df["solve_s"].median() / 2.0),
    )


def overlap_scan(spec: Optional[BatterySpec] = None, *, charge_limit_mw: Optional[float] = None) -> dict:
    """How often can the relaxed LP profit from simultaneous charge and discharge? Counts the historical intervals
    below p* and solves every day that has one (all 9 zonal series, 2011 - 26 Sep 2026), energy only, from empty.

    `spec` defaults to the arena battery on the arena power basis (charge limit P / rte), p* = -$113.33/MWh. Pass another
    BatterySpec (the pilot: p* = -w rte / (1 - rte) = -$182/MWh) to get its own count; its charge limit is then the grid-side
    P unless `charge_limit_mw` says otherwise. The per-zone and per-year counts and the minimum price are included."""
    _, _, _ = _arena()
    dates, zones, P = load_zonal_days()
    arena = spec is None
    spec = arena_battery_spec() if arena else spec
    if charge_limit_mw is None and arena:
        charge_limit_mw = spec.power_mw / spec.rte
    pstar = lp.overlap_price_threshold(spec)
    hits = np.argwhere(P.min(axis=2) < pstar)
    out = []
    for d, z in hits:
        plan = lp_day(spec, P[d, z], dates[d], charge_limit_mw=charge_limit_mw)
        dg = plan.frame.attrs["diagnostics"]
        out.append(dict(date=dates[d], zone=zones[z], min_price=float(P[d, z].min()),
                        n_below=int((P[d, z] < pstar).sum()), overlap_intervals=dg["overlap_intervals"],
                        milp=dg["milp"], status=plan.status))
    below = P < pstar
    years = np.array([int(x[:4]) for x in dates])
    return dict(threshold_usd_per_mwh=float(pstar), intervals_total=int(P.size),
                intervals_below=int(below.sum()), days_total=int(P.shape[0] * P.shape[1]),
                days_with_interval_below=int(len(hits)), solved=out,
                days_overlap_in_relaxation=int(sum(1 for r in out if r["overlap_intervals"] > 0)),
                days_milp=int(sum(1 for r in out if r["milp"])),
                min_price_usd_per_mwh=float(P.min()), min_price_zone=zones[int(np.unravel_index(P.argmin(), P.shape)[1])],
                min_price_date=dates[int(np.unravel_index(P.argmin(), P.shape)[0])],
                intervals_below_by_zone={z: int(below[:, i].sum()) for i, z in enumerate(zones) if below[:, i].any()},
                intervals_below_by_year={int(y): int(below[years == y].sum()) for y in np.unique(years) if below[years == y].any()},
                first_date=dates[0], last_date=dates[-1])


def main(n_days: int = 300):
    df = compare(n_days)
    s = summarize(df)
    sc = overlap_scan()
    out = paths.REPORTS / "case_study"
    out.mkdir(parents=True, exist_ok=True)
    df.to_csv(out / "dp_crosscheck.csv", index=False)
    print(f"{s['n_days']} zone-days over {s['n_zones']} series; DP total ${s['dp_total_usd']:.1f}/MW, "
          f"LP(arena basis) ${s['lp_arena_total_usd']:.1f}, LP(AC basis) ${s['lp_ac_total_usd']:.1f}")
    print("LP - DP, arena basis ($/MW-day):", s["arena_quantiles"])
    print("   relative:", {k: f"{v:.2e}" for k, v in s["arena_rel_quantiles"].items()})
    print(f"   days with LP < DP: {s['arena_share_below_dp']:.1%};  days equal within $1e-6: {s['arena_share_equal']:.1%}")
    print("LP - DP, AC basis ($/MW-day):", s["ac_quantiles"], f"total {s['ac_total_rel_diff']:.2%}")
    print("overlap scan:", {k: v for k, v in sc.items() if k != "solved"})
    return df, s, sc


if __name__ == "__main__":
    main()
