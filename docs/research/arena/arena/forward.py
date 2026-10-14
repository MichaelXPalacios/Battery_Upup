"""Forward competition, 2027-2031: scenario futures built from real history, then the same five algorithms.

A scenario is a sequence of five "weather-years" drawn from history (whole years, so seasons, heat waves, cold
snaps and storms stay intact and every region in a market moves together). Each drawn day's prices are then
adjusted to the future market:

  1. transmission    zone premium over the market reference x kappa (a line built, or large loads landing)
  2. fuel            prices in the gas-driven band (-$50..$200) x (scenario gas / gas on the source day)
  3. market shape    deviations from the daily median x sigma (batteries flatten spreads; tight supply widens
                     them) x a vintage factor that maps older ERCOT years onto the 2025 spread level
  4. scarcity        spike days kept with probability psi (psi > 1 stretches spikes instead)
  5. caps            ERCOT $5,000/MWh offer cap; NYISO clipped at $10,000
plus distribution outages (storms, trees, substation failures) that island the battery for days, and local
programs whose event days force the battery to be full and to discharge through the window.
Every parameter below is an ASSUMPTION grounded in docs/research (ercot.md, nyiso.md).
"""
import time
from datetime import date, timedelta

import numpy as np

from . import config as C
from . import economics as E
from .algorithms import ALGORITHMS, History, make, run_combined
from .backtest import analog_weights, storm_flags
from .battery import Spec, run
from .data import Market
from .models import RegionModels, daily_targets, find_analogs

YEARS = list(range(C.FORWARD_START, C.FORWARD_START + 5))
SPIKE = {"ERCOT": 300.0, "NYISO": 250.0}

FAMILIES = {
    "ERCOT": [("saturate", 0.40), ("base", 0.35), ("surge", 0.25)],
    "NYISO": [("relief", 0.35), ("base", 0.40), ("tight", 0.25)],
}
GAS_LEVELS = [(3.00, 0.30), (3.75, 0.45), (4.75, 0.25)]          # Henry Hub $/MMBtu (ASSUMPTION)
OUTAGES = {  # events per year, duration range (days)  - ASSUMPTION from the research's storm history
    "LZ_NORTH": (0.20, 0.5, 2), "LZ_HOUSTON": (0.35, 1, 6), "LZ_SOUTH": (0.30, 1, 4), "LZ_WEST": (0.10, 0.5, 2),
    "LZ_AEN": (0.15, 0.5, 3), "LZ_CPS": (0.15, 0.5, 3), "LZ_LCRA": (0.15, 0.5, 3), "LZ_RAYBN": (0.20, 0.5, 2),
    "WEST": (0.15, 0.5, 2), "GENESE": (0.15, 0.5, 2), "CENTRL": (0.15, 0.5, 2), "NORTH": (0.15, 0.5, 2),
    "MHK VL": (0.15, 0.5, 2), "CAPITL": (0.15, 0.5, 2), "HUD VL": (0.20, 1, 4), "MILLWD": (0.25, 1, 5),
    "DUNWOD": (0.25, 1, 5), "N.Y.C.": (0.10, 0.5, 2), "LONGIL": (0.25, 1, 6),
}
HOME_LOAD_MW = 0.06       # ASSUMPTION: 50 homes per MW of Cores x ~1.2 kW average home load


def shape_params(market, family, year):
    """sigma (spread scale) and psi (spike retention) for a family and target year."""
    k = year - 2026
    if market == "ERCOT":
        if family == "saturate":
            return 0.94 ** k, 0.85 ** k
        if family == "base":
            return 0.97 ** k, 0.93 ** k
        early = min(year, 2028) - 2026
        late = max(0, year - 2028)
        return 0.97 ** early * 1.04 ** late, 0.93 ** early * 1.20 ** late
    if family == "relief":
        return 0.90, 0.85
    if family == "base":
        return 1.00, 1.00
    return 1.10, 1.30


def kappa_schedule(market, zones, rng):
    """Premium multipliers per (target year, zone) for one scenario, plus a description of the events."""
    K = np.ones((len(YEARS), len(zones)))
    events = []
    zi = {z: k for k, z in enumerate(zones)}
    if market == "ERCOT":
        K[:, zi["LZ_WEST"]] = 0.4                       # the 2026 fade of the Permian premium persists
        if rng.random() < 0.7:
            y = rng.choice([2029, 2030, 2031])
            K[[i for i, yy in enumerate(YEARS) if yy >= y], zi["LZ_WEST"]] = 0.2
            events.append(f"Permian 765-kV lines {y}")
        if rng.random() < 0.5:
            ramp = np.clip((np.array(YEARS) - 2026) / 3, 0, 1)
            K[:, zi["LZ_NORTH"]] = 1.0 + 0.6 * ramp
            K[:, zi["LZ_RAYBN"]] = 1.0 + 0.4 * ramp
            events.append("DFW large loads land")
        if rng.random() < 0.4:
            K[[i for i, yy in enumerate(YEARS) if yy >= 2029], zi["LZ_SOUTH"]] = 0.6
            events.append("Valley import upgrade 2029")
    else:
        K[:, zi["N.Y.C."]] = 0.5                         # CHPE in service since May 2026
        for z in ("HUD VL", "MILLWD", "DUNWOD"):
            K[:, zi[z]] = 0.7                            # AC Transmission relieved Central-East
        K[:, zi["CAPITL"]] = 0.5
        K[:, zi["NORTH"]] = 0.1                          # North Country at parity since 2024
        K[:, zi["WEST"]] = 0.5
        K[:, zi["LONGIL"]] = 0.8
        if rng.random() < 0.7:
            K[[i for i, yy in enumerate(YEARS) if yy >= 2030], zi["LONGIL"]] = 0.5
            events.append("Propel NY 2030")
    return K, events


class Futures:
    def __init__(self, market, n_scen=40, seed=7):
        self.m = market
        rng = np.random.default_rng(seed + (0 if market.name == "ERCOT" else 1000))
        self.rng = rng
        self.N = n_scen
        R = len(market.zones)
        # target calendar
        days = []
        d = date(YEARS[0], 1, 1)
        while d.year <= YEARS[-1]:
            days.append(d)
            d += timedelta(days=1)
        self.tdays = days
        self.t_year = np.array([x.year for x in days])
        self.t_month = np.array([x.month for x in days])
        self.t_weekend = np.array([x.weekday() >= 5 for x in days])
        # source years and weights
        hist_years = sorted(set(market.years[market.years <= C.BACKTEST_END]))
        if market.name == "ERCOT":
            base_w = np.array([0.175 if y >= 2022 else 0.3 / 11 for y in hist_years])
            scarce = {2011, 2018, 2019, 2021, 2022, 2023}
        else:
            base_w = np.array([2.0 if y >= 2016 else 1.0 for y in hist_years])
            scarce = {2014, 2018, 2022, 2025}
        base_w = base_w / base_w.sum()
        fam_names, fam_p = zip(*FAMILIES[market.name])
        self.family = rng.choice(fam_names, size=n_scen, p=fam_p)
        lv, lp = zip(*GAS_LEVELS)
        self.gas_level = rng.choice(lv, size=n_scen, p=lp)
        self.src_year = np.zeros((n_scen, len(YEARS)), int)
        self.gas_y = np.zeros((n_scen, len(YEARS)))
        self.kappa = np.zeros((n_scen, len(YEARS), R))
        self.events = []
        for n in range(n_scen):
            K, ev = kappa_schedule(market.name, market.zones, rng)
            self.kappa[n] = K
            self.events.append(ev)
            for i, y in enumerate(YEARS):
                w = base_w.copy()
                if self.family[n] in ("surge", "tight") and y >= 2029:
                    w = w * np.array([2.0 if hy in scarce else 1.0 for hy in hist_years])
                    w = w / w.sum()
                self.src_year[n, i] = rng.choice(hist_years, p=w)
                self.gas_y[n, i] = self.gas_level[n] * np.exp(rng.normal(0, 0.10))
        # day mapping: same month/day in the source year (Feb 29 -> Feb 28)
        idx_of = {dd: k for k, dd in enumerate(market.dates)}
        self.src_day = np.zeros((n_scen, len(days)), int)
        for n in range(n_scen):
            for j, x in enumerate(days):
                sy = self.src_year[n, YEARS.index(x.year)]
                md = (x.month, x.day if not (x.month == 2 and x.day == 29) else 28)
                key = f"{sy:04d}-{md[0]:02d}-{md[1]:02d}"
                k = idx_of.get(key)
                if k is None:      # missing source day: nearest available
                    k = int(np.searchsorted(market.dates, key))
                    k = min(max(k, 0), len(market.dates) - 1)
                self.src_day[n, j] = k
        # vintage factors (ERCOT): map older years' spreads onto the 2025 spread level, per region
        _, _, _, _, spread = daily_targets(market.P, market)
        self.vintage = {}
        for y in sorted(set(market.years)):          # every year, since analog days may come from 2026 too
            sel = market.years == y
            if market.name == "ERCOT":
                ref = np.median(spread[market.years == 2025], 0)
                self.vintage[y] = np.clip(ref / np.maximum(np.median(spread[sel], 0), 1e-6), 0.5, 1.5)
            else:
                self.vintage[y] = np.ones(R)
        # outages
        self.outage = np.zeros((n_scen, len(days), R), bool)
        for n in range(n_scen):
            for r, z in enumerate(market.zones):
                lam, a, b = OUTAGES[z]
                for i, y in enumerate(YEARS):
                    yi = np.where(self.t_year == y)[0]
                    for _ in range(rng.poisson(lam)):
                        start = rng.choice(yi[(self.t_month[yi] >= 5) & (self.t_month[yi] <= 10)]) \
                            if z in ("LZ_HOUSTON", "LZ_SOUTH", "LONGIL", "N.Y.C.") else rng.choice(yi)
                        dur = int(np.ceil(rng.uniform(a, b)))
                        self.outage[n, start:start + dur, r] = True
        self.warned = self.outage & (rng.random(self.outage.shape) < 0.7)   # 70% of outages forecast a day ahead
        self.spike_keep = rng.random((n_scen, len(days), R))

    # ------------------------------------------------------------------ price construction
    def adjust(self, P_src, ref_src, kappa, rho, sigma, psi, vint, keep_draw):
        """Vectorised day adjustment. P_src, ref_src: (L, 96); the rest (L,)."""
        cap = C.ERCOT_CAP if self.m.name == "ERCOT" else C.NY_CAP
        thr = SPIKE[self.m.name]
        p = ref_src + (P_src - ref_src) * kappa[:, None]
        band = (p > -50) & (p < 200)
        p = np.where(band, p * rho[:, None], p)
        med = np.median(p, 1, keepdims=True)
        spike = p >= thr
        p = np.where(spike, p, med + (p - med) * (sigma * vint)[:, None])
        exc = np.maximum(p - thr, 0)
        keep = (keep_draw < np.minimum(psi, 1.0))[:, None]
        stretch = np.maximum(psi, 1.0)[:, None]
        p = np.where(spike, np.where(keep, thr + exc * stretch, np.minimum(p, thr * 0.4)), p)
        return np.clip(p, -250, cap)

    def lane_prices(self, j, lanes):
        """Prices for target day j on all lanes. lanes: list of (scenario n, region r)."""
        m = self.m
        n_arr = np.array([n for n, _ in lanes])
        r_arr = np.array([r for _, r in lanes])
        yi = YEARS.index(self.tdays[j].year)
        d_src = self.src_day[n_arr, j]
        P_src = m.P[d_src, r_arr, :]
        ref = m.ref[d_src, :]
        kappa = self.kappa[n_arr, yi, r_arr]
        rho = self.gas_y[n_arr, yi] / m.gas[np.maximum(d_src - 1, 0)]
        sig_psi = np.array([shape_params(m.name, self.family[n], self.tdays[j].year) for n in n_arr])
        vint = np.array([self.vintage[m.years[d]][r] for d, r in zip(d_src, r_arr)])
        keep = self.spike_keep[n_arr, j, r_arr]
        return self.adjust(P_src, ref, kappa, np.clip(rho, 0.5, 2.0), sig_psi[:, 0], sig_psi[:, 1], vint, keep), d_src


class ForwardContext:
    """Per-day context for the algorithms in a scenario run (analogs come from other historical years)."""

    def __init__(self, fut, lanes, models, analogs, storm_hist, K=12):
        self.f, self.lanes, self.models = fut, lanes, models
        self.idx, self.dist = analogs
        self.storm_hist = storm_hist
        self.K = K
        self.n_arr = np.array([n for n, _ in lanes])
        self.r_arr = np.array([r for _, r in lanes])
        self.cache = {}

    def __len__(self):
        return len(self.f.tdays)

    def prices(self, j):
        if j not in self.cache:
            self.cache = {j: self.f.lane_prices(j, self.lanes)}
        return self.cache[j]

    def __getitem__(self, j):
        f, m = self.f, self.f.m
        P, d_src = self.prices(j)
        L = len(self.lanes)
        yi = YEARS.index(f.tdays[j].year)
        storm = self.storm_hist[d_src, self.r_arr] | f.warned[self.n_arr, j, self.r_arr]
        if j + 1 < len(f.tdays):
            storm = storm | f.warned[self.n_arr, j + 1, self.r_arr]
        ctx = {"weekend": bool(f.t_weekend[j]), "month": int(f.t_month[j]), "storm": storm}
        idx = self.idx[d_src, self.r_arr, :self.K]                    # (L, K)
        if (idx < 0).any():
            return ctx
        dist = self.dist[d_src, self.r_arr, :self.K]
        w = analog_weights(dist)
        A = m.P[idx, self.r_arr[:, None], :]                          # (L, K, 96)
        Aref = m.ref[idx, :]
        kappa = f.kappa[self.n_arr, yi, self.r_arr]
        gas_now = f.gas_y[self.n_arr, yi]
        sig_psi = np.array([shape_params(m.name, f.family[n], f.tdays[j].year) for n in self.n_arr])
        out = np.empty_like(A)
        ones, zeros = np.ones(L), np.zeros(L)
        for k in range(self.K):
            d_an = idx[:, k]
            rho = np.clip(gas_now / m.gas[np.maximum(d_an - 1, 0)], 0.5, 2.0)
            vint = np.array([f.vintage[m.years[d]][r] for d, r in zip(d_an, self.r_arr)])
            # same adjustment as the real day, but spikes are kept (psi = 1) and then scaled to their
            # expected size under the scenario's psi, so the analogs carry the spike risk without random thinning
            adj = f.adjust(A[:, k, :], Aref[:, k, :], kappa, rho, sig_psi[:, 0], ones, vint, zeros)
            exc = np.maximum(adj - SPIKE[m.name], 0)
            out[:, k, :] = adj - exc + exc * sig_psi[:, 1][:, None]
        ctx["analog_prices"], ctx["analog_w"] = out, w / w.sum(1, keepdims=True)
        return ctx


def lane_layout(market):
    """Merchant lanes (one per region) and program lanes (one per program-zone with an event window)."""
    progs = [p for p in E.programs_for(market.name) if p["window"] is not None]
    layout = [("merchant", r, None) for r in range(len(market.zones))]
    for p in progs:
        for z in p["zones"]:
            layout.append(("program", market.zones.index(z), p["id"]))
    return layout, progs


def simulate_forward(market_name, spec_key="home", n_scen=40, verbose=True):
    t0 = time.time()
    m = Market(market_name)
    spec = Spec(spec_key)
    fut = Futures(m, n_scen)
    cache = C.CACHE / f"analogs_other_years_{market_name}.npz"
    if cache.exists():
        z = np.load(cache)
        analogs = (z["idx"], z["dist"])
    else:
        analogs = find_analogs(m, K=24, mode="other_years")
        np.savez_compressed(cache, idx=analogs[0], dist=analogs[1])
    models = RegionModels(m)
    storm_hist = storm_flags(m)
    layout, progs = lane_layout(m)
    pid = {p["id"]: p for p in progs}
    lanes, lane_kind = [], []
    for n in range(n_scen):
        for kind, r, prog in layout:
            lanes.append((n, r))
            lane_kind.append((n, kind, r, prog))
    L = len(lanes)
    D = len(fut.tdays)
    # program existence and event days per lane
    exists = {}
    for n in range(n_scen):
        for p in progs + [p for p in E.programs_for(market_name) if p["window"] is None]:
            exists[(n, p["id"])] = fut.rng.random() < p["p_exists"]
    event = np.zeros((D, L), bool)
    window = np.zeros((L, 2), int)
    for li, (n, kind, r, prog) in enumerate(lane_kind):
        if kind != "program" or not exists[(n, prog)]:
            continue
        p = pid[prog]
        window[li] = p["window"]
        for y in YEARS:
            if y < p["start"]:
                continue
            yi = np.where(fut.t_year == y)[0]
            tmax = m.W["tmax"][fut.src_day[n, yi], r]
            ev = E.pick_events(tmax, fut.t_month[yi], fut.t_weekend[yi], p)
            event[yi[ev], li] = True
    outage = np.stack([fut.outage[n, :, r] for (n, r) in lanes], 1)          # (D, L)
    w0, w1 = window[:, 0], window[:, 1]

    def forced(d, t, soc):
        ev = event[d]
        out = outage[d]
        if not ev.any() and not out.any():
            return None, None
        act = np.zeros(L, int)
        mask = out.copy()                                # islanded: no market actions
        pre = ev & (t >= w0 - 8) & (t < w0)
        act = np.where(pre, -2, act)
        win = ev & (t >= w0) & (t < w1)
        act = np.where(win, 2, act)
        mask = mask | pre | win
        act = np.where(out, 0, act)
        return mask, act

    # warm start: the last 63 days of real history for each lane's region
    warm = np.stack([m.P[-63:, r, :] for (_, r) in lanes], 1)
    warm_wk = m.weekend[-63:]

    def hist_factory():
        return History(warm, warm_wk)

    class LazyP:
        """Prices are generated day by day (they do not fit in memory for every lane at once)."""
        shape = (D, L, C.NI)

        def __init__(self, ctx):
            self.ctx = ctx

        def __getitem__(self, j):
            return self.ctx.prices(j)[0]

    ctx = ForwardContext(fut, lanes, models, analogs, storm_hist, K=8)
    t1 = time.time()
    leds, picks = run_combined(spec, hist_factory, LazyP(ctx), ctx, forced=forced)
    results = {}
    for short, name in ALGORITHMS:
        led = leds[short]
        backup = []
        for li, (n, r) in enumerate(lanes):
            starts = np.where(outage[1:, li] & ~outage[:-1, li])[0] + 1
            for s in starts:
                backup.append(led.backup[s, li] / HOME_LOAD_MW)
        results[short] = {"profit": led.profit, "mwh": led.mwh_out,
                          "backup_hours": float(np.mean(backup)) if backup else float("nan")}
        if verbose:
            print(f"  forward {market_name} {short} {name:16s} "
                  f"mean merchant-lane $/kW-yr {led.profit.sum(0).mean() / len(YEARS) / 1000:6.1f}  "
                  f"backup h {results[short]['backup_hours']:.1f}", flush=True)
    results["A5"]["picks"] = picks
    if verbose:
        print(f"  forward {market_name} combined pass {time.time() - t1:.0f}s", flush=True)
    meta = {"lane_kind": lane_kind, "exists": exists, "family": fut.family.tolist(), "gas": fut.gas_level.tolist(),
            "events": fut.events, "years": YEARS, "t_year": fut.t_year, "event": event,
            "programs": {p["id"]: p for p in E.programs_for(market_name)}, "zones": m.zones,
            "src_year": fut.src_year.tolist()}
    if verbose:
        print(f"  forward {market_name} done in {time.time() - t0:.0f}s", flush=True)
    return results, meta
