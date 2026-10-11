"""NodalMarket: a Market-compatible object for arbitrary price lanes (nodes), plus the lane-safe backtest pieces.

The arena's `Market` holds the 8 ERCOT load zones. Everything downstream (`RegionModels`, `find_analogs`,
`BacktestContext`, `run_combined`) only reads a handful of attributes from it, so a `NodalMarket` that carries the same
attributes for any set of lanes drops in with no arena edit:

    nm = NodalMarket(lane_ids, P, ref, dates, lane_zone)      # P: (days, lanes, 96) $/MWh, ref: (days, 96) HB_BUSAVG
    rm = RegionModels(nm)                                      # arena class, unchanged
    idx, dist = find_analogs(nm, K=24, mode="past")            # arena function, unchanged
    ctx = BacktestContext(nm, days, rm, (idx, dist))           # arena class, unchanged
    leds, picks = run_combined(spec, hist_factory, nm.P[days], ctx)

Weather for a lane is its load zone's column of the arena's `weather("ERCOT", dates)`; gas is the arena's `gas(dates)`.
Anything the arena looks up by zone name (`C.LABEL[z]`) is registered at run time from here (`register_lanes`). The backtest
tools are supported; `arena.forward` (the 2027-2031 scenario engine) keys on the arena's own zone names (OUTAGES,
kappa_schedule) and is not.

Two things in the arena couple lanes to each other, which matters as soon as lanes are processed in chunks:

  1. `analog_matrix` standardises the analog features with the mean and standard deviation pooled over every lane of
     the market it is given;
  2. `BacktestContext` weights the analog days with a kernel whose bandwidth is the median distance pooled over every
     lane of the day.

`run_combined` itself is lane-independent (every policy acts elementwise on lanes). With the arena's own context, a
lane's numbers therefore depend a little on which other lanes share its chunk. This module adds the lane-safe variant
used by the runner (`context="lane"`): fixed reference standardisation (the arena's own 8-zone statistics) and a
per-lane bandwidth, so that a lane's result does not depend on its neighbours. `context="arena"` keeps the arena's
pooled behaviour and reproduces it exactly when the lanes are the arena's 8 zones. Both are tested in
tests/test_3a_nodal.py and tests/test_3a_runner.py.
"""
from __future__ import annotations

import hashlib
import time
import warnings
from datetime import datetime

import numpy as np

from . import paths

paths.use_arena()
from arena import config as C  # noqa: E402
from arena.algorithms import History, run_combined  # noqa: E402
from arena.backtest import BacktestContext, analog_weights, gas_scale  # noqa: E402
from arena.battery import Spec  # noqa: E402
from arena.data import Market, gas, weather  # noqa: E402
from arena.models import ANALOG_W, RegionModels, daily_targets, find_analogs  # noqa: E402

from . import specs as _specs  # noqa: E402,F401  (registers grid4h in arena.config.SPECS)

# --------------------------------------------------------------------------- constants (labels: PLAN.md)
K_ANALOG = 24                 # REAL: the arena's analog-day count (arena.backtest.K_ANALOG)
ANALOG_WINDOW_DAYS = 30       # REAL: arena.models.find_analogs default window (+-30 days of the same calendar date)
WARM_DAYS = 63                # REAL: arena.algorithms.History default buffer (63 days of price memory)
SIM_START_DEFAULT = "2015-01-01"   # REAL: arena.backtest.SIM_START = 2015
ANALOG_REF_FILE = paths.DERIVED / "nodal_analog_ref_stats.npz"   # DERIVED: the arena's 8-zone standardisation, cached
ALGO_KEYS = ("A1", "A2", "A3", "A4", "A5")
LEDGER_FIELDS = ("profit", "energy", "wear", "mwh")   # same four arrays the arena saves per algorithm


# --------------------------------------------------------------------------- labels
def register_lanes(lane_ids, labels=None):
    """Add lane ids to `arena.config.LABEL` so `C.LABEL[z]` works for them (runtime only, nothing on disk).

    Existing labels are never overwritten (the 8 zone lanes keep their arena label).
    """
    labels = labels or {}
    for lane in lane_ids:
        C.LABEL.setdefault(lane, labels.get(lane) or lane)


# --------------------------------------------------------------------------- weather / gas (cached per date vector)
_WEATHER_CACHE: dict = {}
_GAS_CACHE: dict = {}


def _dates_key(dates):
    return hashlib.md5("|".join(map(str, dates)).encode()).hexdigest()


def zone_weather(dates):
    """The arena's `weather("ERCOT", dates)` for the 8 load zones, cached per date vector (CSV reads are slow)."""
    key = _dates_key(dates)
    if key not in _WEATHER_CACHE:
        _WEATHER_CACHE[key] = weather("ERCOT", list(dates))
    return _WEATHER_CACHE[key]


def henry_hub(dates):
    key = _dates_key(dates)
    if key not in _GAS_CACHE:
        _GAS_CACHE[key] = gas(list(dates))
    return _GAS_CACHE[key]


def _ffill_flat(P):
    """Forward-fill NaNs along each lane's flattened day-major time axis (leading NaNs take the first value)."""
    out = P.copy()
    for l in range(P.shape[1]):
        x = out[:, l, :].reshape(-1)
        bad = ~np.isfinite(x)
        if not bad.any():
            continue
        if bad.all():
            raise ValueError(f"lane {l} has no finite price")
        idx = np.where(~bad, np.arange(x.size), 0)
        np.maximum.accumulate(idx, out=idx)
        first = np.flatnonzero(~bad)[0]
        idx[:first] = first
        out[:, l, :] = x[idx].reshape(-1, C.NI)
    return out


def _day_index(market, start=None, end=None):
    """Indices of the days with start <= date <= end (ISO strings, inclusive; None = open). Works for any Market."""
    sel = np.ones(len(market.dates), bool)
    if start is not None:
        sel &= market.dates >= str(start)
    if end is not None:
        sel &= market.dates <= str(end)
    return np.where(sel)[0]


# --------------------------------------------------------------------------- NodalMarket
class NodalMarket:
    """Market-compatible bundle for arbitrary lanes. Attribute-for-attribute what `arena.data.Market` provides.

    Parameters
    ----------
    lane_ids : sequence of str, unique. Lane names (a node, a hub, a zone ...). They play the role of the arena's `zones`.
    P        : (days, lanes, 96) real-time 15-minute prices in $/MWh. 96 intervals a day, DST normalised the arena's way
               (the repeated hour dropped, a skipped hour repeats the hour before).
    ref      : (days, 96) the HB_BUSAVG reference series. Kept for the forward tools; the backtest does not read it.
    dates    : (days,) ISO 'YYYY-MM-DD', strictly increasing.
    lane_zone: dict lane -> arena load zone (one of arena.config.ERCOT_ZONES), a sequence parallel to lane_ids, or one
               zone name for all lanes. Weather for the lane is that zone's column.
    labels   : optional dict lane -> display label (defaults to the id).
    fill_gaps: if True, NaN prices are forward-filled (the arena's rule for isolated gaps); otherwise NaNs raise.

    `name` is "ERCOT" because the arena keys its thresholds (hot/cold/spike), storm rules and the analog spike level on
    the market name.
    """

    name = "ERCOT"

    def __init__(self, lane_ids, P, ref, dates, lane_zone, *, ref_name=None, labels=None, fill_gaps=False):
        lanes = [str(x) for x in lane_ids]
        if len(set(lanes)) != len(lanes):
            raise ValueError("duplicate lane ids")
        dates = np.asarray(dates).astype(str)
        P = np.asarray(P, np.float64)
        ref = np.asarray(ref, np.float64)
        D, L = len(dates), len(lanes)
        if P.shape != (D, L, C.NI):
            raise ValueError(f"P must be (days, lanes, {C.NI}) = {(D, L, C.NI)}, got {P.shape}")
        if ref.shape != (D, C.NI):
            raise ValueError(f"ref must be (days, {C.NI}) = {(D, C.NI)}, got {ref.shape}")
        if D > 1 and not (dates[1:] > dates[:-1]).all():
            raise ValueError("dates must be strictly increasing ISO strings")
        if not np.isfinite(P).all():
            if not fill_gaps:
                raise ValueError("P contains NaN/inf (pass fill_gaps=True to forward-fill, or fix the lane)")
            P = _ffill_flat(P)
        if not np.isfinite(ref).all():
            raise ValueError("ref contains NaN/inf")
        if isinstance(lane_zone, str):
            zones = [lane_zone] * L
        elif isinstance(lane_zone, dict):
            missing = [x for x in lanes if x not in lane_zone]
            if missing:
                raise ValueError(f"no zone for lanes {missing[:5]}")
            zones = [lane_zone[x] for x in lanes]
        else:
            zones = list(lane_zone)
            if len(zones) != L:
                raise ValueError("lane_zone sequence must be parallel to lane_ids")
        bad = sorted({z for z in zones if z not in C.ERCOT_ZONES})
        if bad:
            raise ValueError(f"zones {bad} are not arena load zones {C.ERCOT_ZONES}")

        self.lane_ids = lanes
        self.zones = lanes                                   # the arena calls its regions "zones"
        self.lane_zone = zones                               # parallel to lanes
        self.ref_name = ref_name or C.ERCOT_REF
        self.dates = dates
        self.P = P
        self.ref = ref
        self.years = np.array([int(d[:4]) for d in dates])
        dt = [datetime.strptime(d, "%Y-%m-%d") for d in dates]
        self.month = np.array([x.month for x in dt])
        self.doy = np.array([x.timetuple().tm_yday for x in dt])
        self.weekend = np.array([x.weekday() >= 5 for x in dt])
        base = zone_weather(dates)
        zi = {z: k for k, z in enumerate(C.ERCOT_ZONES)}
        cols = [zi[z] for z in zones]
        self.W = {k: np.array(base[k][:, cols]) for k in ("tmax", "tmin", "wsf2", "prcp")}
        self.W["sys_tmax"] = np.array(base["sys_tmax"])
        self.W["sys_tmin"] = np.array(base["sys_tmin"])
        self.W["stations_found"] = list(base["stations_found"])
        self.gas = np.array(henry_hub(dates))
        register_lanes(lanes, labels)
        self.labels = [C.LABEL[z] for z in lanes]

    # ---- constructors
    @classmethod
    def from_market(cls, market):
        """A NodalMarket whose lanes are the arena Market's zones (lane = zone, price = the zone price)."""
        return cls(market.zones, market.P, market.ref, market.dates, {z: z for z in market.zones},
                   ref_name=market.ref_name)

    # ---- Market API
    def year_slice(self, y0, y1=None):
        y1 = y0 if y1 is None else y1
        return np.where((self.years >= y0) & (self.years <= y1))[0]

    def day_index(self, start=None, end=None):
        """Indices of the days with start <= date <= end (ISO strings, inclusive; None = open)."""
        return _day_index(self, start, end)

    def subset(self, lanes):
        """A NodalMarket with only some lanes (indices or ids). Shares nothing mutable with the parent."""
        if len(lanes) and isinstance(lanes[0], str):
            ix = [self.lane_ids.index(x) for x in lanes]
        else:
            ix = [int(i) for i in lanes]
        new = object.__new__(NodalMarket)
        new.lane_ids = [self.lane_ids[i] for i in ix]
        new.zones = new.lane_ids
        new.lane_zone = [self.lane_zone[i] for i in ix]
        new.ref_name, new.dates, new.ref = self.ref_name, self.dates, self.ref
        new.P = np.ascontiguousarray(self.P[:, ix, :])
        new.years, new.month, new.doy, new.weekend, new.gas = self.years, self.month, self.doy, self.weekend, self.gas
        new.W = {k: (np.array(v[:, ix]) if k in ("tmax", "tmin", "wsf2", "prcp") else v) for k, v in self.W.items()}
        new.labels = [self.labels[i] for i in ix]
        return new

    def __repr__(self):
        return (f"NodalMarket(lanes={len(self.lane_ids)}, days={len(self.dates)} "
                f"{self.dates[0]}..{self.dates[-1]}, ref={self.ref_name})")


# --------------------------------------------------------------------------- analog search (lane-safe version)
def analog_raw(market):
    """(D, R, 7) un-standardised analog features; identical to the first half of arena.models.analog_matrix."""
    W = market.W
    D, R = market.P.shape[:2]
    y1, _, _, _, _ = daily_targets(market.P, market)
    lagy1 = np.concatenate([y1[:1], y1[:-1]], 0)
    cols = [np.repeat(W["sys_tmax"][:, None], R, 1), np.repeat(W["sys_tmin"][:, None], R, 1),
            W["tmax"], W["tmin"], np.repeat(np.log(market.gas)[:, None], R, 1), lagy1,
            np.repeat(market.weekend.astype(float)[:, None], R, 1)]
    return np.stack(cols, 2)


def pooled_stats(A):
    """Mean and standard deviation over every day and lane, exactly as arena.models.analog_matrix computes them."""
    flat = A.reshape(-1, A.shape[2])
    return flat.mean(0), flat.std(0) + 1e-9


def analog_matrix_stats(market, stats=None):
    """Standardised analog features. stats=None pools over this market's lanes (the arena's behaviour, bit-identical to
    arena.models.analog_matrix); stats=(mu, sd) uses fixed values, which makes a lane's features independent of the
    lanes that happen to be in the same chunk."""
    A = analog_raw(market)
    mu, sd = pooled_stats(A) if stats is None else stats
    return (A - mu) / sd * ANALOG_W


def reference_stats(refresh=False):
    """The arena's own 8-zone standardisation, (mu, sd), cached in derived/nodal_analog_ref_stats.npz.

    DERIVED: pooled over the arena's ERCOT Market (all days, 8 load zones). Chunked and single-process runs use these
    same numbers, and the 8-zone market reproduces the arena's analogs bit for bit.
    """
    if ANALOG_REF_FILE.exists() and not refresh:
        z = np.load(ANALOG_REF_FILE)
        return z["mu"], z["sd"]
    m = Market("ERCOT")
    A = analog_raw(m)
    mu, sd = pooled_stats(A)
    ANALOG_REF_FILE.parent.mkdir(parents=True, exist_ok=True)
    np.savez(ANALOG_REF_FILE, mu=mu, sd=sd, first_date=m.dates[0], last_date=m.dates[-1], n_days=len(m.dates),
             zones=np.array(m.zones))
    return mu, sd


def analog_search(market, A, K=K_ANALOG, mode="past", window=ANALOG_WINDOW_DAYS, day_idx=None):
    """arena.models.find_analogs on a ready feature matrix A (D, R, 7), optionally only for the days in `day_idx`.

    The loop is the arena's, line for line, so for the same A it returns the same indices and distances. Days outside
    `day_idx` keep -1 / inf (a backtest only reads the days it simulates)."""
    D, R, _ = A.shape
    doy, years = market.doy, market.years
    idx = np.full((D, R, K), -1, int)
    dist = np.full((D, R, K), np.inf)
    arange_d = np.arange(D)
    for d in (range(D) if day_idx is None else day_idx):
        dd = np.abs(doy - doy[d])
        dd = np.minimum(dd, 366 - dd)
        if mode == "past":
            cand = np.where((arange_d < d) & (dd <= window))[0]
        else:
            cand = np.where((years != years[d]) & (dd <= window))[0]
        if len(cand) < 3:
            continue
        diff = A[cand] - A[d][None]
        dsq = (diff ** 2).sum(2)
        k = min(K, len(cand))
        part = np.argpartition(dsq, k - 1, axis=0)[:k]
        for r in range(R):
            sel = part[:, r]
            order = np.argsort(dsq[sel, r])
            idx[d, r, :k] = cand[sel[order]]
            dist[d, r, :k] = np.sqrt(dsq[sel[order], r])
    return idx, dist


def analog_weights_lane(dist):
    """Kernel weights (R, K) with each lane's own median distance as bandwidth (the arena pools it over all lanes)."""
    fin = np.isfinite(dist)
    d = np.where(fin, dist, np.nan)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", category=RuntimeWarning)
        h = np.nanmedian(d, axis=1)
    h = np.where(np.isfinite(h), h, 1.0)[:, None]
    w = np.exp(-0.5 * (np.where(fin, dist, 1e9) / np.maximum(h, 1e-6)) ** 2)
    return w / np.maximum(w.sum(1, keepdims=True), 1e-12)


class NodalBacktestContext(BacktestContext):
    """arena.backtest.BacktestContext with a switch for the kernel bandwidth.

    bandwidth="pooled"  the arena's rule (median distance over all lanes of the day): identical output to
                        BacktestContext for the same inputs;
    bandwidth="lane"    each lane uses its own median distance, so a lane's context never depends on other lanes.
    The rest of __getitem__ is the arena's code unchanged.
    """

    def __init__(self, market, days, models, analogs, bandwidth="lane"):
        super().__init__(market, days, models, analogs)
        if bandwidth not in ("lane", "pooled"):
            raise ValueError("bandwidth must be 'lane' or 'pooled'")
        self.bandwidth = bandwidth

    def __getitem__(self, i):
        m, d = self.m, self.days[i]
        R = m.P.shape[1]
        ctx = {"weekend": bool(m.weekend[d]), "month": int(m.month[d]), "storm": self.storm[d]}
        idx = self.idx[d]                                   # (R, K)
        if (idx < 0).any():
            return ctx
        w = analog_weights_lane(self.dist[d]) if self.bandwidth == "lane" else analog_weights(self.dist[d])
        F = np.stack([m.P[idx[r], r, :] for r in range(R)], 0)          # (R, K, 96)
        g_today = m.gas[max(d - 1, 0)]
        ratio = g_today / m.gas[np.maximum(idx - 1, 0)]                   # (R, K)
        F = gas_scale(F, ratio[:, :, None])
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


# --------------------------------------------------------------------------- the backtest on a market slice
def run_slice(market, spec_key="grid", start=None, end=None, *, context="lane", K=K_ANALOG, analogs=None, models=None,
              gamma=0.97, verbose=False):
    """Walk-forward backtest of every lane of `market` for start <= date <= end, the way arena.backtest.simulate_market
    does it (same warm-up, models, analogs, context and run_combined), but for any NodalMarket/Market.

    context="lane"   lane-safe (reference standardisation + per-lane bandwidth): a lane's result does not depend on the
                     other lanes in `market`, so chunked runs equal single-process runs. Default.
    context="arena"  the arena's pooled behaviour; equals the arena exactly when the lanes are its 8 zones.
    analogs / models: optional precomputed (idx, dist) / RegionModels, to share work or to test against the arena cache.

    Returns {"dates", "lanes", "spec", "context", "algos": {A1..A5: {profit, energy, wear, mwh}}, "picks", "backup",
             "timing"}; arrays are (days, lanes), picks is the learner's chosen expert (int8).
    """
    if context not in ("lane", "arena"):
        raise ValueError("context must be 'lane' or 'arena'")
    _specs.register()
    t0 = time.time()
    spec = Spec(spec_key)
    start = SIM_START_DEFAULT if start is None else start
    days = _day_index(market, start, end)
    if len(days) == 0:
        raise ValueError(f"no days between {start} and {end} in this market")
    d0 = days[0]
    warm = market.P[max(0, d0 - WARM_DAYS):d0]
    warm_wk = market.weekend[max(0, d0 - WARM_DAYS):d0]
    t1 = time.time()
    rm = RegionModels(market) if models is None else models
    t2 = time.time()
    if analogs is None:
        if context == "lane":
            A = analog_matrix_stats(market, reference_stats())
            analogs = analog_search(market, A, K, "past", ANALOG_WINDOW_DAYS, day_idx=days)
        else:
            analogs = find_analogs(market, K=K, mode="past")
    t3 = time.time()
    ctx = NodalBacktestContext(market, days, rm, analogs, "lane" if context == "lane" else "pooled")
    leds, picks = run_combined(spec, lambda: History(warm, warm_wk), market.P[days], ctx, gamma=gamma)
    t4 = time.time()
    out = {"dates": market.dates[days], "lanes": list(market.lane_ids) if hasattr(market, "lane_ids") else list(market.zones),
           "spec": spec_key, "context": context, "algos": {}, "picks": picks,
           "backup": {k: leds[k].backup for k in ALGO_KEYS},
           "timing": {"setup_s": t1 - t0, "models_s": t2 - t1, "analogs_s": t3 - t2, "run_s": t4 - t3, "total_s": t4 - t0,
                      "n_days": int(len(days)), "n_lanes": int(market.P.shape[1])}}
    for k in ALGO_KEYS:
        led = leds[k]
        out["algos"][k] = {"profit": led.profit, "energy": led.energy, "wear": led.wear, "mwh": led.mwh_out}
    if verbose:
        tm = out["timing"]
        print(f"  run_slice {spec_key} {context}: {tm['n_lanes']} lanes x {tm['n_days']} days in {tm['total_s']:.0f}s "
              f"({tm['n_lanes'] * tm['n_days'] / tm['total_s']:.1f} lane-days/s)", flush=True)
    return out


def ledgers_equal(a, b):
    """Exact equality of two run_slice results (all ledger fields, the backup energy and the learner's picks)."""
    if list(a["dates"]) != list(b["dates"]) or a["lanes"] != b["lanes"]:
        return False
    for k in ALGO_KEYS:
        for f in LEDGER_FIELDS:
            if not np.array_equal(a["algos"][k][f], b["algos"][k][f]):
                return False
        if not np.array_equal(a["backup"][k], b["backup"][k]):
            return False
    return np.array_equal(a["picks"], b["picks"])


def arena_slice(spec_key="grid", start=None, end=None, market_name="ERCOT", K=K_ANALOG, use_cache=True):
    """Reference run: the steps of arena.backtest.simulate_market on the arena's own Market, for a date slice.

    Uses only arena classes (Market, RegionModels, BacktestContext, run_combined) and the arena's analog cache when it
    exists, exactly as simulate_market does. The result has the same layout as run_slice(), so the two can be compared
    with ledgers_equal().
    """
    from arena.backtest import BacktestContext as ArenaContext
    t0 = time.time()
    m = Market(market_name)
    spec = Spec(spec_key)
    cache = C.CACHE / f"analogs_past_{market_name}.npz"
    if use_cache and cache.exists():
        z = np.load(cache)
        analogs = (z["idx"], z["dist"])
    else:
        analogs = find_analogs(m, K=K, mode="past")
    models = RegionModels(m)
    days = _day_index(m, SIM_START_DEFAULT if start is None else start, end)
    d0 = days[0]
    warm = m.P[max(0, d0 - WARM_DAYS):d0]
    warm_wk = m.weekend[max(0, d0 - WARM_DAYS):d0]
    ctx = ArenaContext(m, days, models, analogs)
    leds, picks = run_combined(spec, lambda: History(warm, warm_wk), m.P[days], ctx)
    out = {"dates": m.dates[days], "lanes": list(m.zones), "spec": spec_key, "context": "arena-simulate_market",
           "algos": {k: {"profit": leds[k].profit, "energy": leds[k].energy, "wear": leds[k].wear,
                         "mwh": leds[k].mwh_out} for k in ALGO_KEYS},
           "picks": picks, "backup": {k: leds[k].backup for k in ALGO_KEYS},
           "timing": {"total_s": time.time() - t0, "n_days": int(len(days)), "n_lanes": int(m.P.shape[1])}}
    return out


# --------------------------------------------------------------------------- helpers for building lane inputs
def zone_lanes(dates=None):
    """The arena's 8 load-zone price lanes and the HB_BUSAVG reference, straight from the arena's price cache.

    Returns (dates, lane_ids, P, ref): P is (days, 8, 96) float64 and ref (days, 96), restricted to `dates` (ISO strings; every
    date must exist) or all of them. Values are bit-identical to arena.data.Market's. Use them as control lanes next to nodal
    lanes (a zone lane run through the same runner is the like-for-like comparison for a node)."""
    from arena.data import ercot_prices
    all_dates, zones, P = ercot_prices()
    zi = {z: k for k, z in enumerate(zones)}
    if dates is None:
        sel = np.arange(len(all_dates))
    else:
        dates = np.asarray(dates).astype(str)
        sel = np.searchsorted(all_dates, dates)
        if (sel >= len(all_dates)).any() or (all_dates[np.minimum(sel, len(all_dates) - 1)] != dates).any():
            raise ValueError("some dates are not in the arena's ERCOT price history")
    lanes = list(C.ERCOT_ZONES)
    Pz = P[sel][:, [zi[z] for z in lanes], :].astype(np.float64)
    ref = P[sel][:, zi[C.ERCOT_REF], :].astype(np.float64)
    return all_dates[sel].astype(str), lanes, Pz, ref


def arena_zone_of_substations(path=None):
    """{sub: arena load zone} for the weather of a nodal lane: the municipal / co-op zone (LZ_AEN, LZ_CPS, LZ_LCRA, LZ_RAYBN)
    when the substation serves load there (NP4-160 NOIE mapping), else its settlement load zone. Substations with neither
    (the 85 constraint stations that are not NP4-160 codes) are left out. Reads the CSV with keep_default_na=False because the
    real substation code "NA" would otherwise be read as missing."""
    import pandas as pd
    s = pd.read_csv(path or (paths.REPO_DATA / "substations.csv"), keep_default_na=False, na_values=[""], low_memory=False,
                    usecols=["sub", "zone", "noie_zone"])
    out = {}
    for sub, z, nz in zip(s["sub"], s["zone"], s["noie_zone"]):
        first = str(nz).split(";")[0].strip() if isinstance(nz, str) else ""
        if first in C.ERCOT_ZONES:
            out[sub] = first
        elif isinstance(z, str) and z in C.ERCOT_ZONES:
            out[sub] = z
    return out
