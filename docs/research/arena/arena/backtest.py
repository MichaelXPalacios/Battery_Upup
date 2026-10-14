"""Walk-forward backtest: every algorithm dispatches a battery in every region on real prices, day by day.

Each algorithm only knows prices up to yesterday (plus today's weather and yesterday's gas). The weather
algorithm's equations and analog days are fitted on earlier years only. The simulation starts in SIM_START so
that every scoring window (1, 3 and 5 years ending BACKTEST_END) has at least five earlier years behind it
for the siting rules.
"""
import json
import time

import numpy as np

from . import config as C
from .algorithms import ALGORITHMS, History, make, run_combined
from .battery import Spec, oracle_days, run
from .data import Market
from .models import RegionModels, find_analogs

SIM_START = 2015
K_ANALOG = 24


def storm_flags(market):
    rule = C.STORM_RULES[market.name]
    W = market.W
    return ((W["wsf2"] >= rule["wsf2_mph"]) | (W["prcp"] >= rule["prcp_in"]) |
            (W["tmin"] <= rule["tmin_f"]) | (W["tmax"] >= rule["tmax_f"]))


def analog_weights(dist):
    """Kernel weights from analog distances: (R, K) -> (R, K) rows summing to 1."""
    h = np.median(dist[np.isfinite(dist)]) if np.isfinite(dist).any() else 1.0
    w = np.exp(-0.5 * (np.where(np.isfinite(dist), dist, 1e9) / max(h, 1e-6)) ** 2)
    return w / np.maximum(w.sum(1, keepdims=True), 1e-12)


def gas_scale(prices, ratio):
    """Rescale the fuel-driven part of analog prices (-$50..$200) by the gas ratio; scarcity prices stay."""
    r = np.clip(ratio, 0.5, 2.0)
    band = (prices > -50) & (prices < 200)
    return np.where(band, prices * r, prices)


class BacktestContext:
    """Lazily builds each day's context (calendar, storm flags, analog scenarios) for the simulation window."""

    def __init__(self, market, days, models, analogs):
        self.m, self.days, self.models = market, days, models
        self.idx, self.dist = analogs
        self.storm = storm_flags(market)

    def __len__(self):
        return len(self.days)

    def __getitem__(self, i):
        m, d = self.m, self.days[i]
        R = m.P.shape[1]
        ctx = {"weekend": bool(m.weekend[d]), "month": int(m.month[d]), "storm": self.storm[d]}
        idx = self.idx[d]                                   # (R, K)
        if (idx < 0).any():
            return ctx
        w = analog_weights(self.dist[d])
        F = np.stack([m.P[idx[r], r, :] for r in range(R)], 0)          # (R, K, 96)
        g_today = m.gas[max(d - 1, 0)]
        ratio = g_today / m.gas[np.maximum(idx - 1, 0)]                   # (R, K)
        F = gas_scale(F, ratio[:, :, None])
        # re-weight spike vs calm analogs to the spike probability from the region equations
        thr = 500.0 if m.name == "ERCOT" else 300.0
        is_spike = F.max(2) >= thr
        for r in range(R):
            p_hat = self.models.spike_prob(d, r)
            s, c = is_spike[r], ~is_spike[r]
            if p_hat is None or not s.any() or not c.any():
                continue
            ws, wc = w[r, s].sum(), w[r, c].sum()
            w[r, s] *= p_hat / max(ws, 1e-12)
            w[r, c] *= (1 - p_hat) / max(wc, 1e-12)
        w = w / w.sum(1, keepdims=True)
        ctx["analog_prices"], ctx["analog_w"] = F, w
        return ctx


def simulate_market(market_name, spec_key, verbose=True):
    t0 = time.time()
    m = Market(market_name)
    spec = Spec(spec_key)
    cache = C.CACHE / f"analogs_past_{market_name}.npz"
    if cache.exists():
        z = np.load(cache)
        analogs = (z["idx"], z["dist"])
    else:
        analogs = find_analogs(m, K=K_ANALOG, mode="past")
        np.savez_compressed(cache, idx=analogs[0], dist=analogs[1])
    models = RegionModels(m)
    days = np.where(m.years >= SIM_START)[0]
    d0 = days[0]
    warm = m.P[max(0, d0 - 63):d0]
    warm_wk = m.weekend[max(0, d0 - 63):d0]

    def hist_factory():
        return History(warm, warm_wk)

    ctx = BacktestContext(m, days, models, analogs)
    P = m.P[days]
    out = {"market": market_name, "spec": spec_key, "dates": list(m.dates[days]), "zones": m.zones, "algos": {}}
    t1 = time.time()
    leds, picks = run_combined(spec, hist_factory, P, ctx)      # A1-A4 standalone + A5 learner, one pass
    for short, name in ALGORITHMS:
        led = leds[short]
        rec = {"profit": led.profit, "energy": led.energy, "wear": led.wear, "mwh": led.mwh_out}
        if short == "A5":
            rec["picks"] = picks
        out["algos"][short] = rec
        if verbose:
            yr = m.years[days]
            last = led.profit[yr == C.BACKTEST_END].sum(0) / 1000
            print(f"  {market_name} {spec_key} {short} {name:16s} {C.BACKTEST_END} $/kW-yr: "
                  + " ".join(f"{v:6.1f}" for v in last), flush=True)
    if verbose:
        print(f"  {market_name} {spec_key} combined pass {time.time() - t1:.0f}s", flush=True)
    # perfect-foresight ceiling, region by region, year by year
    orc = np.zeros((len(days), len(m.zones)))
    for r in range(len(m.zones)):
        orc[:, r] = oracle_days(P[:, r, :], spec)
    out["algos"]["ORACLE"] = {"profit": orc}
    np.savez_compressed(C.CACHE / f"backtest_{market_name}_{spec_key}.npz",
                        **{f"{a}_{k}": v for a, rec in out["algos"].items() for k, v in rec.items()},
                        dates=np.array(out["dates"]), zones=np.array(m.zones))
    if verbose:
        print(f"  {market_name} {spec_key} done in {time.time() - t0:.0f}s", flush=True)
    return out


def load_backtest(market_name, spec_key):
    z = np.load(C.CACHE / f"backtest_{market_name}_{spec_key}.npz", allow_pickle=True)
    dates = z["dates"].astype(str)
    years = np.array([int(d[:4]) for d in dates])
    algos = {}
    for key in z.files:
        if "_" in key and key.split("_")[0] in ("A1", "A2", "A3", "A4", "A5", "ORACLE"):
            a, field = key.split("_", 1)
            algos.setdefault(a, {})[field] = z[key]
    return {"dates": dates, "years": years, "zones": list(z["zones"].astype(str)), "algos": algos}
