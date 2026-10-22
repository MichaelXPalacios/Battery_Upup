#!/usr/bin/env python3
"""Export the algorithm arena (docs/research/arena/) to the story app's Arena page: ui/data/arena/index.json.

    ARENA_DATA=<research-data dir> <python with numpy> scripts/export_arena.py

Reads (never writes) the arena's committed results and its caches outside the repo:
  docs/research/arena/results/arena_results.json   siting race, forward tournament, site ranking, transmission effects
  $ARENA_DATA/arena_cache/backtest_<M>_{home,grid}.npz   daily P&L of A1-A5 and the ceiling on real prices, 2015 to Sep 2026,
                                                       and which expert the adaptive learner followed each day
  $ARENA_DATA/arena_cache/{prices,analogs_past}_<M>.npz  the real 15-minute zone prices and the weather-regime analog days
  $ARENA_DATA/{weather,gas}/                             the arena's own inputs, needed to replay one real day
$ARENA_DATA defaults to ../research-data beside the repo (docs/research/arena/arena/config.py says the same).

What it writes (one file in the sim.contracts envelope, schema hb.arena.v1):
  - per market, per zone and for the market mean: $/kW-yr per year for A1-A5 and the perfect-foresight ceiling, for the
    home battery and the grid-scale battery (q1 head to head, q2 who wins when)
  - the adaptive learner's expert mix by month, switches per year and days per expert (q2)
  - the siting rules, the backtest and forward tournaments, the site ranking with its revenue stack, the transmission
    events and the program terms (q3, straight from arena_results.json)
  - cycles a day, $ per MWh and the share of days traded per rule, and one REAL day replayed through the arena's own
    algorithms (arena.algorithms) with the exact cached profit checked on the way (q4)
Every figure the arena also publishes (the 5-year league, the learner's days per expert, $ per MWh) is recomputed here
from the caches and ASSERTED equal to arena_results.json, so the file cannot drift from the report. Deterministic: the
same inputs give byte-identical output. No language model produces any number here: the arena's deterministic code did.
"""
import inspect
import json
import os
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from sim.contracts import envelope, labelled, write_json  # noqa: E402

ARENA = ROOT / "docs" / "research" / "arena"
DATA = Path(os.environ.get("ARENA_DATA", str(ROOT.parent / "research-data")))
os.environ["ARENA_DATA"] = str(DATA)          # arena.config reads it at import
CACHE = DATA / "arena_cache"
OUT = ROOT / "ui" / "data" / "arena" / "index.json"
REPORT = "docs/research/arena/README.md"
sys.path.insert(0, str(ARENA))
from arena import algorithms as ALG_MOD, backtest as BT_MOD, config as CFG, economics as ECON  # noqa: E402
from arena.algorithms import (Adaptive, Clockwork, Forecaster, History, SpikeHunter, WeatherRegime,  # noqa: E402
                              _book)
from arena.battery import Ledger, Spec, feasible  # noqa: E402
from arena.data import Market  # noqa: E402
from arena.models import RegionModels  # noqa: E402

YEARS = list(range(2015, 2027))
WINDOW = (2021, 2025)                          # the scoring window of the report's 5-year league
MARKETS = ("ERCOT", "NYISO")
SPECS = ("home", "grid")
ALGOS = ["A1", "A2", "A3", "A4", "A5"]
NAMES = {"A1": "Clockwork", "A2": "Forecaster", "A3": "Weather-regime", "A4": "Spike hunter", "A5": "Adaptive learner"}
# the learner's six experts (Adaptive.expert_names) fall into the four rule families
FAMILY_OF = [0, 1, 2, 3, 3, 1]
# how the learner's six experts are named on the page (the arena's own names carry the percentile: Spike hunter q97 / q90)
SHOWN = {"Spike hunter q97": "Spike hunter (strict)", "Spike hunter q90": "Spike hunter (looser)",
         "Forecaster (low carry)": "Forecaster (low carry)"}
# same short names as scripts/export_markets.py
LABEL = {"LZ_NORTH": "DFW", "LZ_HOUSTON": "Houston", "LZ_SOUTH": "South / Valley", "LZ_WEST": "West / Permian",
         "LZ_AEN": "Austin Energy", "LZ_CPS": "San Antonio", "LZ_LCRA": "LCRA co-ops", "LZ_RAYBN": "Rayburn co-ops",
         "WEST": "West (A)", "GENESE": "Genesee (B)", "CENTRL": "Central (C)", "NORTH": "North Country (D)",
         "MHK VL": "Mohawk Valley (E)", "CAPITL": "Capital (F)", "HUD VL": "Hudson Valley (G)", "MILLWD": "Millwood (H)",
         "DUNWOD": "Dunwoodie (I)", "N.Y.C.": "NYC (J)", "LONGIL": "Long Island (K)"}
DAY_ZONE = {"ERCOT": "LZ_NORTH", "NYISO": "N.Y.C."}      # where the real days are replayed
LEAD_DAYS = 8                                            # the replay starts this many days early so the battery state converges
TOL_USD = 0.5                                            # a replayed day must match the cached profit to within $0.5 per MW

C_Y = ("SIM: one dispatch rule traded a battery walk-forward on the ISO's real prices, no look-ahead; "
       "profit per kW of battery per calendar year; " + REPORT)
C_CEIL = "SIM: perfect-foresight dispatch on the same real prices, the most any rule could earn; " + REPORT
C_MIX = "DERIVED: share of days the adaptive learner followed each rule family, by calendar month (shadow batteries ran all of them)"
C_FW = "SIM: 20 futures per market built from real weather years with gas, saturation, transmission and outage adjustments (ASSUMPTION inputs); " + REPORT
C_STAT = "DERIVED: arithmetic on the SIM forward results (20 futures per market); " + REPORT


def r1(x):
    return round(float(x), 1)


def r2(x):
    return round(float(x), 2)


def lab(v, label, cite):
    return labelled(r1(v) if isinstance(v, (float, np.floating)) else v, label, cite)


def src_has(path, *snippets):
    """The arena's code still says what this export says it says."""
    text = (ARENA / "arena" / path).read_text(encoding="utf-8")
    for s in snippets:
        assert s in text, f"arena/{path} no longer contains {s!r}: update scripts/export_arena.py"


def default(fn, name):
    return inspect.signature(fn).parameters[name].default


# ------------------------------------------------------------------------------------------------ backtest tables
def load_bt(mk, spec):
    z = np.load(CACHE / f"backtest_{mk}_{spec}.npz", allow_pickle=True)
    dates = [str(d) for d in z["dates"]]
    return {"dates": dates, "zones": [str(x) for x in z["zones"]], "yrs": np.array([int(d[:4]) for d in dates]),
            "mon": np.array([int(d[5:7]) for d in dates]), "z": z}


def yearly(bt):
    """{algo or 'CEIL': (zones, years) $/kW-yr}."""
    z, yrs = bt["z"], bt["yrs"]
    out = {}
    for a in ALGOS + ["ORACLE"]:
        prof = z[f"{a}_profit"]
        out["CEIL" if a == "ORACLE" else a] = np.stack([prof[yrs == y].sum(0) / 1000 for y in YEARS], 1)
    return out


def mix_tables(bt):
    """Month x rule-family shares, switches per year and days per expert for every zone, from the learner's cached picks."""
    pk, yrs, mon = bt["z"]["A5_picks"], bt["yrs"], bt["mon"]
    D, Z = pk.shape
    midx = (yrs - YEARS[0]) * 12 + (mon - 1)
    n_m = int(midx.max()) + 1
    fam = np.array(FAMILY_OF)[pk]                                   # (D, Z)
    share = np.zeros((Z, 4, n_m))
    for m in range(n_m):
        sel = midx == m
        for f in range(4):
            share[:, f, m] = (fam[sel] == f).mean(0)
    sw = np.zeros((Z, len(YEARS)))
    chg = np.zeros((D, Z), bool)
    chg[1:] = pk[1:] != pk[:-1]
    for i, y in enumerate(YEARS):
        sw[:, i] = chg[yrs == y].sum(0)
    days = np.stack([(pk == k).mean(0) for k in range(6)], 1)       # (Z, 6) over every day
    return share, sw, days, n_m


def stat_tables(bt, spec_key):
    """Cycles a day, $ per MWh discharged and the share of days traded, 2021-25, over all zones: (5,) each."""
    z, yrs = bt["z"], bt["yrs"]
    m = (yrs >= WINDOW[0]) & (yrs <= WINDOW[1])
    spec = Spec(spec_key)
    cyc, per, td = [], [], []
    for a in ALGOS:
        mwh, prof = z[f"{a}_mwh"][m], z[f"{a}_profit"][m]
        cyc.append(mwh.mean() / spec.usable_mwh)
        per.append(prof.sum() / max(mwh.sum(), 1e-9))
        td.append((mwh > 1e-9).mean() * 100)
    return cyc, per, td


# ------------------------------------------------------------------------------------------------ one real day, replayed
def replay_day(mkt, models, analogs, bt, lane, date, lead=LEAD_DAYS, spec_key="home"):
    """The arena's own experts on one real day, one zone, with the learner following the cached picks.

    Mirrors algorithms.run_combined for a single lane, recording every interval's action and state of charge. The
    experts' price history is exactly the previous 63 real days (History is a rolling buffer), so only the battery's
    starting charge needs a few days of lead-in. The target day's profit is checked against the cached backtest.
    """
    spec = Spec(spec_key)
    cd, z = bt["dates"], bt["z"]
    ti = cd.index(date)
    pk = z["A5_picks"]
    days = np.where(mkt.years >= BT_MOD.SIM_START)[0]
    ctxb = BT_MOD.BacktestContext(mkt, days, models, analogs)
    d0 = ti - lead
    abs0 = days[d0]
    warm, warm_wk = mkt.P[abs0 - 63:abs0, lane:lane + 1, :], mkt.weekend[abs0 - 63:abs0]
    hf = lambda: History(warm, warm_wk)                                              # noqa: E731
    experts = [Clockwork(spec, hf()), Forecaster(spec, hf()), WeatherRegime(spec, hf()), SpikeHunter(spec, hf(), 0.97),
               SpikeHunter(spec, hf(), 0.90), Forecaster(spec, hf(), terminal=0.7)]
    n_e = len(experts)
    socs = [np.full(1, spec.R, int) for _ in range(n_e + 1)]
    rec = None
    for di in range(d0, ti + 1):
        ctx = dict(ctxb[di])
        ctx["storm"] = ctx["storm"][lane:lane + 1]
        if "analog_prices" in ctx:
            ctx["analog_prices"], ctx["analog_w"] = ctx["analog_prices"][lane:lane + 1], ctx["analog_w"][lane:lane + 1]
        pd = mkt.P[days[di], lane:lane + 1, :]
        discs = [np.zeros(1, int) for _ in range(n_e + 1)]
        for k, e in enumerate(experts):
            e.begin_day(di, ctx, socs[k])
        res = [e.reserve_today if e.reserve_today is not None else np.full(1, spec.R) for e in experts]
        choice = np.array([pk[di, lane]])
        res_l = np.choose(choice, res)
        leds = [Ledger(1, 1) for _ in range(n_e + 1)]
        act = [[] for _ in range(n_e + 1)]
        soc = [[] for _ in range(n_e + 1)]
        for t in range(96):
            p = pd[:, t]
            al = []
            for k, e in enumerate(experts):
                a = e.act(t, p, socs[k], discs[k])
                al.append(e.act(t, p, socs[-1], discs[-1]))
                a = feasible(a, socs[k], discs[k], spec, np.where(a < 0, spec.R, res[k]))
                _book(leds[k], 0, p, a, spec)
                socs[k] = socs[k] - a
                discs[k] = discs[k] + np.maximum(a, 0)
                act[k].append(int(a[0]))
                soc[k].append(int(socs[k][0]))
            a = np.choose(choice, np.stack(al, 0))
            a = feasible(a, socs[-1], discs[-1], spec, np.where(a < 0, spec.R, res_l))
            _book(leds[-1], 0, p, a, spec)
            socs[-1] = socs[-1] - a
            discs[-1] = discs[-1] + np.maximum(a, 0)
            act[-1].append(int(a[0]))
            soc[-1].append(int(socs[-1][0]))
            for e in experts:
                e.observe(t, p)
        for e in experts:
            e.end_day(di, pd)
        if di == ti:
            prof = [float(leds[k].profit[0, 0]) for k in range(4)] + [float(leds[-1].profit[0, 0])]
            cached = [float(z[f"{a}_profit"][ti, lane]) for a in ALGOS]
            for a, mine, ref in zip(ALGOS, prof, cached):
                assert abs(mine - ref) <= TOL_USD, f"{date} lane {lane} {a}: replay {mine:.2f} vs cached {ref:.2f}"
            rec = {"price": [r1(v) for v in pd[0]],
                   "act": {a: act[i] for a, i in zip(ALGOS, (0, 1, 2, 3, n_e))},
                   "soc": {a: soc[i] for a, i in zip(ALGOS, (0, 1, 2, 3, n_e))},
                   "profit": [r2(v / 1000) for v in prof] + [r2(float(z["ORACLE_profit"][ti, lane]) / 1000)],
                   "followed": int(choice[0])}
    return rec


def pick_days(bt, lane):
    """For each of A1-A4: the day in 2021-25 it beat the best of the other three by the most (ceiling >= every rule that
    day, so the ceiling is a fair yardstick). Deterministic: the first date wins a tie."""
    z, yrs, cd = bt["z"], bt["yrs"], bt["dates"]
    P = np.stack([z[f"A{k}_profit"][:, lane] for k in range(1, 6)], 1)            # (D, 5), A5 last
    O = z["ORACLE_profit"][:, lane]
    ok = (yrs >= WINDOW[0]) & (yrs <= WINDOW[1]) & (O > 0) & (P.max(1) <= O + 1e-6)
    out = []
    for k in range(4):
        others = np.delete(P[:, :4], k, 1).max(1)
        margin = np.where(ok, P[:, k] - others, -np.inf)
        j = int(np.argmax(margin))
        out.append((ALGOS[k], cd[j], float(margin[j]) / 1000))
    return out


# ------------------------------------------------------------------------------------------------ one market
def market(mk, res, learner_names):
    bts = {s: load_bt(mk, s) for s in SPECS}
    zones = bts["home"]["zones"]
    assert zones == bts["grid"]["zones"] and bts["home"]["dates"] == bts["grid"]["dates"]
    lg = {s: res[f"backtest_{s}"]["markets"][mk]["league"] for s in SPECS}
    Z = len(zones)
    inw = [i for i, y in enumerate(YEARS) if WINDOW[0] <= y <= WINDOW[1]]
    ybs = {s: yearly(bts[s]) for s in SPECS}
    mixes = {s: mix_tables(bts[s]) for s in SPECS}

    # ---- the report's own 5-year league must equal what we recomputed from the caches
    for s in SPECS:
        for a in ALGOS + ["ORACLE"]:
            ours = ybs[s]["CEIL" if a == "ORACLE" else a][:, inw].mean(1)
            for i, zz in enumerate(zones):
                ref = lg[s][a]["5y"]["usd_per_mw_yr_by_region"][zz] / 1000
                assert abs(ours[i] - ref) < 0.002, f"{mk} {s} {a} {zz}: {ours[i]:.4f} vs report {ref:.4f}"
        share, sw, days, _ = mixes[s]
        for i, zz in enumerate(zones):
            ref = lg[s]["A5_expert_share"][zz]
            for k, nm in enumerate(learner_names):
                assert abs(days[i, k] - ref[nm]) < 0.0011, f"{mk} {s} {zz} {nm} days followed"

    def entry(sel, zid, label):
        """sel: zone indices (list) averaged for the market mean, or a single index."""
        e = {"id": zid, "label": label}
        for s in SPECS:
            y = {a: [r1(v) for v in ybs[s][a][sel].mean(0)] for a in ALGOS + ["CEIL"]}
            share, sw, days, n_m = mixes[s]
            e[s] = {"y": y,
                    "fam": [[int(round(v * 100)) for v in share[sel, f, :].mean(0)] for f in range(4)],
                    "switches": [r1(v) for v in sw[sel].mean(0)],
                    "days": [int(round(v * 100)) for v in days[sel].mean(0)]}
        return e

    zone_entries = [entry([i], zz, LABEL[zz]) for i, zz in enumerate(zones)]
    all_entry = entry(list(range(Z)), "ALL", "All zones")

    # ---- per-rule statistics for the q4 cards (home and grid, all zones, 2021-25)
    stats = {}
    for s in SPECS:
        cyc, per, td = stat_tables(bts[s], s)
        for k, a in enumerate(ALGOS):          # $ per MWh must equal the report's league column
            assert abs(per[k] - lg[s][a]["5y"]["usd_per_mwh"]) < 0.05, f"{mk} {s} {a} $/MWh {per[k]} vs {lg[s][a]['5y']['usd_per_mwh']}"
        stats[s] = {"cycles": [r2(v) for v in cyc], "perMwh": [r1(v) for v in per], "tradeDays": [int(round(v)) for v in td]}

    # ---- headline numbers: the arena's own 5-year league (published), labelled
    def hl(s):
        d = {a: lab(lg[s][a]["5y"]["usd_per_mw_yr_mean"] / 1000, "SIM", C_Y + "; mean of 2021-25, all zones") for a in ALGOS}
        d["ceiling"] = lab(lg[s]["ORACLE"]["5y"]["usd_per_mw_yr_mean"] / 1000, "SIM", C_CEIL + "; mean of 2021-25, all zones")
        d["capture"] = labelled(round(lg[s]["A5"]["5y"]["capture_vs_oracle"] * 100), "DERIVED",
                                "DERIVED: learner's 2021-25 mean ÷ perfect-foresight ceiling (both SIM), " + REPORT)
        return d

    headline = {s: hl(s) for s in SPECS}

    # ---- one real day per rule, replayed through the arena's own code
    zid = DAY_ZONE[mk]
    lane = zones.index(zid)
    mkt = Market(mk)
    assert [str(z) for z in mkt.zones] == zones
    assert [str(d) for d in mkt.dates[np.where(mkt.years >= BT_MOD.SIM_START)[0]]] == bts["home"]["dates"]
    models = RegionModels(mkt)
    an = np.load(CACHE / f"analogs_past_{mk}.npz")
    items = []
    for algo, date, margin in pick_days(bts["home"], lane):
        rec = replay_day(mkt, models, (an["idx"], an["dist"]), bts["home"], lane, date)
        items.append({"algo": algo, "date": date, "margin": r2(margin), **rec})
    days_out = {"zone": zid, "label": LABEL[zid], "items": items}

    fwd = res["forward"]["dispatch_forward"][mk]
    backup = [r1(fwd[a]["backup_hours"]) for a in ALGOS]
    notes = EVENTS[mk]
    pk0 = bts["home"]
    return {"years": YEARS, "partial": [bool((pk0["yrs"] == y).sum() < 360) for y in YEARS],
            "lastDate": pk0["dates"][-1], "ribbonStart": f"{YEARS[0]}-01", "months": len(zone_entries[0]["home"]["fam"][0]),
            "events": notes, "all": all_entry, "zones": zone_entries, "stats": stats, "backup": backup,
            "headline": headline, "days": days_out}


EVENTS = {
    "ERCOT": [
        {"year": 2021, "name": "Winter Storm Uri", "note": "Generation froze and prices sat at the cap for days: little spread to trade.",
         "label": "REAL", "cite": "docs/research/ercot.md, Uri 14-20 Feb 2021 (prices at the $9,000 cap)"},
        {"year": 2023, "name": "Evening spikes", "note": "Most spikes came in the evening, in August and September, and a grid emergency alert was called on a September evening.",
         "label": "REAL", "cite": "docs/research/ercot.md 2.4 (54% of LZ_NORTH spikes 7-9 pm in 2023) and section 7 (6 Sep 2023 EEA2); " + REPORT},
        {"year": 2024, "name": "Batteries crowd in", "note": "New solar and storage halved prices; the evening peak became regular.",
         "label": "REAL", "cite": "docs/research/ercot.md (hub average about $63 to $29/MWh, 2023 to 2024); " + REPORT},
        {"year": 2026, "name": "Spikes after dark", "note": "Spikes have moved to nights and winter mornings; the data run to late September.",
         "label": "REAL", "cite": "docs/research/ercot.md (spike hours 2025-26)"},
    ],
    "NYISO": [
        {"year": 2021, "name": "Indian Point closes", "note": "Downstate prices rose after the last unit shut.",
         "label": "REAL", "cite": "docs/research/nyiso.md section 2 (Indian Point closed Apr 2020 and Apr 2021)"},
        {"year": 2022, "name": "Gas price spike", "note": "Fuel drove prices up across the state.",
         "label": "REAL", "cite": "docs/research/nyiso.md section 2 (2022 gas crisis)"},
        {"year": 2025, "name": "Prices jump", "note": "Delivered gas prices rose sharply and transmission outages added to the jump.",
         "label": "REAL", "cite": "docs/research/nyiso.md (NYC average $39.4 to $67.0/MWh, 2024 to 2025; market monitor: gas and transmission outages)"},
        {"year": 2026, "name": "New cable from Quebec", "note": "CHPE began operating in May; New York City's premium over upstate prices fell to its lowest of the study.",
         "label": "REAL", "cite": "docs/research/nyiso.md (CHPE began commercial operation 13 May 2026; NYC price ratio 1.11 in 2026, the lowest in 24 years)"},
    ],
}


# ------------------------------------------------------------------------------------------------ siting + forward
def opt_parts(opt):
    """'ERCOT:LZ_LCRA [TX co-op peak]' -> (market, zone, program or None)."""
    mk, rest = opt.split(":", 1)
    z, prog = (rest.split(" [", 1) + [""])[:2]
    return mk, z, prog.rstrip("]") or None


def siting(res):
    bt = res["backtest_home"]["portfolios"]
    fw = res["forward"]
    opts = {}                                                   # every option id used, in a stable order

    def oid(o):
        if o not in opts:
            opts[o] = len(opts)
        return opts[o]

    def mw(v):
        v = float(v)
        return int(v) if v == int(v) else round(v, 1)

    def fleet(d):
        return [[oid(o), mw(m)] for o, m in sorted(d.items(), key=lambda x: (-x[1], x[0]))]

    out = {"backtest": {}, "forward": {}, "sites": {}}
    for sc in ("ERCOT", "NYISO", "BOTH"):
        b = {"y": {}, "fleet": {}, "next": {}}
        for a in ALGOS + ["A5ts"]:
            r = bt[sc][a]
            b["y"][a] = [r1(r[h]["usd_per_mw_yr"] / 1000) for h in ("1y", "3y", "5y")]
            b["fleet"][a] = fleet(r["5y"]["final_fleet"])
            b["next"][a] = fleet(r["next_10mw"])
        out["backtest"][sc] = b
        board = fw["markets"][sc]["board"]
        f = {"mean": {}, "p10": {}, "p50": {}, "p90": {}, "win": {}, "all": {}, "first": {}, "fleet": {}}
        for a in ALGOS:
            for key, src in (("mean", "mean"), ("p10", "p10"), ("p50", "p50"), ("p90", "p90")):
                f[key][a] = [r1(board[a][h][src] / 1000) for h in ("1y", "3y", "5y")]
            f["win"][a] = [int(round(board[a][h]["win_rate"] * 100)) for h in ("1y", "3y", "5y")]
            f["all"][a] = [[r1(v / 1000) for v in board[a][h]["all"]] for h in ("1y", "3y", "5y")]
            f["first"][a] = fleet(board[a]["first_10mw_2027"])
            f["fleet"][a] = fleet(board[a]["fleet_2031_mean_mw"])
        out["forward"][sc] = f
        # the site ranking (the learner's dispatch), with the revenue stack of the median future 2027-29
        dec = {d["option"]: d for d in fw["decomposition"]}
        rk = fw["markets"][sc]["ranking"]["A5"]
        s = {"opt": [], "median": [], "p10": [], "energy": [], "capacity": [], "program": [], "event": [], "upgrades": [], "total": []}
        for o, med, p10 in rk:
            d = dec[o]
            s["opt"].append(oid(o))
            s["median"].append(r1(med / 1000))
            s["p10"].append(r1(p10 / 1000))
            for k, kk in (("energy", "energy"), ("capacity", "capacity"), ("program", "program"), ("event", "event_energy"),
                          ("upgrades", "upgrades"), ("total", "total")):
                s[k].append(r1(d[kk] / 1000))
        out["sites"][sc] = s

    names = [None] * len(opts)
    for o, i in opts.items():
        mk, z, prog = opt_parts(o)
        names[i] = {"market": mk, "zone": z, "label": LABEL[z], "program": prog}
    out["options"] = names
    out["start"] = int(CFG.FORWARD_START)
    out["horizons"] = list(CFG.HORIZONS)
    out["backtestEnd"] = int(CFG.BACKTEST_END)
    return out


PROGRAM_PAY = {      # label of the $/kW-yr figure, per the report's program table
    "TX co-op peak": ("ASSUMPTION", "half of Austin Energy's up-to $8.50/kW-month; co-ops already buy capacity from Base"),
    "Austin Energy tolling": ("DERIVED", "up to $4.08M/yr for 40 MW = $8.50/kW-month (City of Austin RCA 26-1526); an upper bound"),
    "SB 415 feeder contract": ("ASSUMPTION", "SB 415 is only a proposed rule (16 TAC 25.58); payment inside the $43-60/kW-yr deferral range"),
    "Data-center flexibility": ("ASSUMPTION", "modelled on the Piq Energy / Base study of a hypothetical data centre"),
    "Con Ed load relief": ("DERIVED", "REAL rate $18/kW-month x 5 months (Con Ed CSRP/DLRP 2025)"),
    "Con Ed Westchester relief": ("DERIVED", "REAL rate $6/kW-month x 5 months (Con Ed CSRP, Westchester)"),
    "Utility battery program": ("REAL", "$50/kW-yr bring-your-own-battery (National Grid, NYSEG/RG&E 2026)"),
}


def programs():
    out = []
    for p in ECON.PROGRAMS:
        label, why = PROGRAM_PAY[p["id"]]
        w = p["window"]
        out.append({
            "name": p["id"], "market": p["market"], "zones": [LABEL[z] for z in p["zones"]],
            "pays": labelled(p["usd_kw_yr"], label, "docs/research/arena/arena/economics.py PROGRAMS: " + why),
            "events": labelled(p["events"], "ASSUMPTION", "event days a year; " + p["source"]),
            "window": None if w is None else [w[0] / 4, w[1] / 4],
            "starts": p["start"],
            "chance": labelled(int(round(p["p_exists"] * 100)), "ASSUMPTION", "chance the program exists in a future; economics.py PROGRAMS"),
            "needsUpgrades": bool(p["concentrated"]),
            "eventPays": labelled(p["event_usd_mwh"], "REAL" if p["event_usd_mwh"] else "ASSUMPTION",
                                  "extra $/MWh paid for event energy; economics.py PROGRAMS"),
        })
    return out


def infrastructure(res):
    c = "SIM: difference-in-differences on the zone's trading value, futures with vs without the event; " + REPORT
    return [{"event": d["event"], "market": d["market"], "zone": LABEL[d["zone"]],
             "effect": lab(d["effect"] / 1000, "SIM", c),
             "with": labelled(d["scenarios_with"], "SIM", "futures (of 20) that contain the event; " + REPORT),
             "without": labelled(d["scenarios_without"], "SIM", "futures (of 20) that do not; " + REPORT),
             "beforeAfterWith": lab(d["after_minus_before_with"] / 1000, "SIM", c),
             "beforeAfterWithout": lab(d["after_minus_before_without"] / 1000, "SIM", c)}
            for d in res["forward"]["infrastructure"]]


# ------------------------------------------------------------------------------------------------ text, constants, main
RULES = {
    "A1": {"short": "A fixed daily schedule.",
           "rule": "Charges in the cheapest and discharges in the dearest fixed window of the last {CLOCK_DAYS} days' average day, every day, whatever the weather.",
           "sees": "Prices up to yesterday.",
           "ignores": "Weather, gas and today's real prices."},
    "A2": {"short": "Forecasts, then plans.",
           "rule": "Forecasts tomorrow's price for every quarter hour from recent days, rescales the swings to a typical day, and plans with a dynamic programme: what is a stored kWh worth in each interval. Then it acts on each real price as it arrives.",
           "sees": "Prices up to yesterday and the real price now.",
           "ignores": "Weather and gas."},
    "A3": {"short": "Plans for days like today.",
           "rule": "Finds {ANALOGS} past days with weather, gas and recent prices like today's, plans for each as a full price path, and re-weights the paths through the day as real prices arrive. Holds a bigger backup before wind, flood-rain and hard-freeze days, and leaves heat days for selling.",
           "sees": "Prices, today's weather, yesterday's gas price and the real price now.",
           "ignores": "Nothing the others see."},
    "A4": {"short": "Waits for the spike.",
           "rule": "Keeps the battery full. Buys only below the {SPIKE_LO} percentile of the last {SPIKE_WIN} days of prices and sells only above the {SPIKE_HI}. It trades rarely and earns the most per MWh sold.",
           "sees": "Prices up to yesterday and the real price now.",
           "ignores": "Weather, gas and the time of day."},
    "A5": {"short": "Follows whichever rule is working.",
           "rule": "Runs every rule above, plus two variants, as shadow batteries and follows, zone by zone, whichever has earned the most lately, weighting each day further back by {LEARNER_GAMMA}. It switches only on a clear lead.",
           "sees": "Everything the others see, and how each of them has done.",
           "ignores": "Nothing; it decides only which rule to follow."},
}
SITING = {
    "A1": {"name": "Last year's best", "owner": "Clockwork",
           "rule": "Puts all of the next {SITE_MW} into last year's best option."},
    "A2": {"name": "Trend", "owner": "Forecaster",
           "rule": "Scores each option by its {SITE_A2_YEARS}-year mean plus half the trend, and puts no more than {SITE_A2_CAP} into one option in a year."},
    "A3": {"name": "Risk-adjusted", "owner": "Weather-regime",
           "rule": "Scores each option by its {SITE_A3_YEARS}-year mean minus half its standard deviation, and splits the {SITE_MW} between the top three in shares of {SITE_A3_SHARES}."},
    "A4": {"name": "Best year", "owner": "Spike hunter",
           "rule": "Scores each option by its single best year of the last {SITE_A4_YEARS}, and splits the {SITE_MW} between the top two in shares of {SITE_A4_SHARES}."},
    "A5": {"name": "Recency-weighted posterior", "owner": "Adaptive learner",
           "rule": "Weights the last {SITE_A5_YEARS} years, each older year counting {SITE_A5_DECAY} as much, scores by mean less a quarter of the standard deviation, and places one MW at a time. Trusts the latest year after a structural break."},
}


def constants():
    """Parameters the rules say in words: read from the arena's own code, and checked against its source."""
    src_has("algorithms.py", 'self.hist.profile(ctx["weekend"], 28)', "class SpikeHunter", "self.gamma = gamma",
            'Forecaster(spec, hist_factory(), terminal=0.7)')
    src_has("siting.py", "shares = {\"A1\": (1.0,), \"A3\": (0.5, 0.3, 0.2), \"A4\": (0.6, 0.4)}", "wts = 0.6 ** np.arange(k)[::-1]",
            "_hist(values, y_idx, 6)", "alloc < mw / 2", "h3, h5 = _hist(values, y_idx, 3), _hist(values, y_idx, 5)",
            "mu - 0.25 * sd", "h5.mean(0) - 0.5 * (h5.std(0)")
    hi, lo, win = (default(SpikeHunter.__init__, k) for k in ("q_hi", "q_lo", "window"))
    gamma = default(ALG_MOD.run_combined, "gamma")
    assert default(Adaptive.__init__, "gamma") == gamma
    home, grid = CFG.SPECS["home"], CFG.SPECS["grid"]
    E, R, U = home["energy_units"], home["reserve_units"], CFG.U
    cite = "docs/research/arena/arena/"
    A = "ASSUMPTION"
    return {
        "BATTERY_KW": {"value": 20, "label": "REAL", "cite": "Base Core 20 kW; sim/constants.py CORE_POWER_KW"},
        "RESERVE_FLOOR": {"value": 20, "label": "REAL", "cite": "20% member backup reserve, a hard floor; sim/constants.py RESERVE_FLOOR (percent)"},
        "ROUND_TRIP": {"value": home["rte"], "label": A, "cite": "CORE_RTE; " + cite + "config.py SPECS['home']"},
        "WEAR_USD_MWH": {"value": int(home["deg_usd_mwh"]), "label": A, "cite": "wear cost per MWh discharged; " + cite + "config.py"},
        "GRID_MW": {"value": 1, "label": A, "cite": "grid-scale merchant battery of the price studies; " + cite + "config.py SPECS['grid']"},
        "GRID_MWH": {"value": grid["energy_units"] * U, "label": A, "cite": cite + "config.py SPECS['grid']"},
        "GRID_ROUND_TRIP": {"value": grid["rte"], "label": A, "cite": cite + "config.py SPECS['grid']"},
        "FULL_UNITS": {"value": E, "label": A, "cite": "state of charge counted in units of 0.125 MWh per MW; " + cite + "config.py SPECS['home']"},
        "RESERVE_UNITS": {"value": R, "label": "REAL", "cite": "the 20% reserve in the same units; " + cite + "config.py SPECS['home']"},
        "UNIT_MWH": {"value": U, "label": A, "cite": "MWh per state-of-charge unit per MW of inverter; " + cite + "config.py U"},
        "CLOCK_DAYS": {"value": 28, "label": A, "cite": "days of average price shape; " + cite + "algorithms.py Clockwork"},
        "SPIKE_LO": {"value": int(round(default(SpikeHunter.__init__, "q_lo") * 100)), "label": A, "cite": "buy percentile; " + cite + "algorithms.py SpikeHunter"},
        "SPIKE_HI": {"value": int(round(hi * 100)), "label": A, "cite": "sell percentile; " + cite + "algorithms.py SpikeHunter"},
        "SPIKE_WIN": {"value": win, "label": A, "cite": "days of price history; " + cite + "algorithms.py SpikeHunter"},
        "ANALOGS": {"value": BT_MOD.K_ANALOG, "label": A, "cite": "analog days per zone; " + cite + "backtest.py K_ANALOG"},
        "LEARNER_GAMMA": {"value": gamma, "label": A, "cite": "daily discount of an expert's past earnings; " + cite + "algorithms.py"},
        "SITE_MW": {"value": CFG.FLEET_MW_PER_YEAR, "label": A, "cite": "MW each rule places each January; " + cite + "config.py FLEET_MW_PER_YEAR"},
        "SITE_A2_YEARS": {"value": 3, "label": A, "cite": cite + "siting.py"},
        "SITE_A2_CAP": {"value": CFG.FLEET_MW_PER_YEAR // 2, "label": A, "cite": "half of SITE_MW per option a year; " + cite + "siting.py (alloc < mw / 2)"},
        "SITE_A3_YEARS": {"value": 5, "label": A, "cite": cite + "siting.py"},
        "SITE_A3_SHARES": {"value": "50 / 30 / 20 %", "label": A, "cite": cite + "siting.py shares"},
        "SITE_A4_YEARS": {"value": 3, "label": A, "cite": cite + "siting.py"},
        "SITE_A4_SHARES": {"value": "60 / 40 %", "label": A, "cite": cite + "siting.py shares"},
        "SITE_A5_YEARS": {"value": 6, "label": A, "cite": cite + "siting.py"},
        "SITE_A5_DECAY": {"value": "0.6 times", "label": A, "cite": "weight 0.6 per year of age; " + cite + "siting.py"},
        "FUTURES": {"value": 20, "label": A, "cite": "futures per market; " + cite + "forward.py"},
    }


def main():
    for need in (CACHE, DATA / "weather", DATA / "gas" / "DHHNGSP.csv"):
        if not need.exists():
            sys.exit(f"export_arena: {need} is missing; set ARENA_DATA to the research-data folder (see the docstring)")
    res = json.loads((ARENA / "results" / "arena_results.json").read_text(encoding="utf-8"))
    hf = lambda: History(np.zeros((63, 1, 96)), np.zeros(63, bool))          # noqa: E731
    learner = Adaptive(Spec("home"), hf)
    learner_names = list(learner.expert_names)
    assert learner_names == ["Clockwork", "Forecaster", "Weather-regime", "Spike hunter q97", "Spike hunter q90",
                             "Forecaster (low carry)"], f"the learner's experts changed: update FAMILY_OF and SHOWN ({learner_names})"
    cons = constants()
    doc = envelope(
        "arena", "scripts.export_arena",
        inputs={"prices_sha256": None, "loads_sha256": None, "topology_sha256": None},
        constants=cons,
        sources={
            "ercot": {"label": "REAL", "text": "ERCOT NP6-785-ER Settlement Point Prices, 15-minute, 2011 to 26 Sep 2026 (hashes: docs/research/data/ercot/SOURCE-SHA256SUMS.txt)"},
            "nyiso": {"label": "REAL", "text": "NYISO real-time zonal LBMP archive, 2003 to Sep 2026, time-weighted to 15 minutes (docs/research/data/nyiso/SOURCE-SHA256SUMS.txt)"},
            "weather": {"label": "REAL", "text": "NOAA GHCN-Daily, 18 airport stations (NCEI access service)"},
            "gas": {"label": "REAL", "text": "EIA Henry Hub daily spot (rngwhhdD)"},
            "arena": {"label": "SIM", "text": REPORT + ": five dispatch algorithms and five siting rules, walk-forward backtest and the 2027-31 tournament"},
        },
        series={
            "y": {"label": "SIM", "unit": "$/kW-yr", "text": C_Y},
            "ceiling": {"label": "SIM", "unit": "$/kW-yr", "text": C_CEIL},
            "fam": {"label": "DERIVED", "unit": "%", "text": C_MIX},
            "switches": {"label": "DERIVED", "unit": "per year", "text": "DERIVED: days on which the learner changed which expert it follows, per calendar year (6 experts)"},
            "days": {"label": "DERIVED", "unit": "%", "text": "DERIVED: share of all days (2015 to Sep 2026) the learner followed each of its six experts"},
            "cycles": {"label": "DERIVED", "unit": "cycles/day", "text": "DERIVED: MWh discharged per day ÷ usable energy, mean of 2021-25, all zones"},
            "perMwh": {"label": "DERIVED", "unit": "$/MWh", "text": "DERIVED: profit ÷ MWh discharged, 2021-25, all zones; equals the report's league column"},
            "tradeDays": {"label": "DERIVED", "unit": "%", "text": "DERIVED: share of days (2021-25, all zones) the battery discharged at all"},
            "backup": {"label": "SIM", "unit": "hours", "text": "SIM: charge at the start of an outage, as hours of home load, forward futures; " + REPORT},
            "day.price": {"label": "REAL", "unit": "$/MWh", "text": "REAL: the zone's 15-minute settlement price on that day"},
            "day.act": {"label": "SIM", "unit": "units", "text": "SIM: the rule's action each quarter hour (positive discharges, negative charges, 2 = full power), replayed through the arena's own algorithms"},
            "day.soc": {"label": "SIM", "unit": "units", "text": "SIM: state of charge after each quarter hour in 0.125 MWh units per MW"},
            "day.profit": {"label": "SIM", "unit": "$/kW", "text": "SIM: the day's profit per kW of battery, replay checked against the cached backtest; the last value is the perfect-foresight ceiling"},
            "bt.y": {"label": "SIM", "unit": "$/kW-yr", "text": "SIM: siting race on real prices, 10 MW a year, home fleet, energy only (1, 3 and 5 years to 2025); " + REPORT},
            "bt.fleet": {"label": "SIM", "unit": "MW", "text": "SIM: MW each siting rule had placed in each option after five years of the backtest; " + REPORT},
            "fw.mean": {"label": "SIM", "unit": "$/kW-yr", "text": C_FW + "; mean across futures"},
            "fw.all": {"label": "SIM", "unit": "$/kW-yr", "text": C_FW + "; each future's mean $/kW-yr over the horizon"},
            "fw.p10": {"label": "DERIVED", "unit": "$/kW-yr", "text": C_STAT + "; 10th percentile"},
            "fw.p50": {"label": "DERIVED", "unit": "$/kW-yr", "text": C_STAT + "; median"},
            "fw.p90": {"label": "DERIVED", "unit": "$/kW-yr", "text": C_STAT + "; 90th percentile"},
            "fw.win": {"label": "DERIVED", "unit": "%", "text": "DERIVED: share of the 20 futures in which this rule's fleet earned the most (ties share the win); " + REPORT},
            "fw.fleet": {"label": "SIM", "unit": "MW", "text": "SIM: MW placed in the option, mean across futures (2031 fleet) or in 2027; " + REPORT},
            "sites.median": {"label": "SIM", "unit": "$/kW-yr", "text": "SIM: median future, 2027-29, the learner's dispatch, home fleet, full revenue stack; " + REPORT},
            "sites.p10": {"label": "DERIVED", "unit": "$/kW-yr", "text": "DERIVED: bad case (10th percentile) of the same; " + REPORT},
            "sites.stack": {"label": "SIM", "unit": "$/kW-yr", "text": "SIM/ASSUMPTION: components in the median future, 2027-29; capacity, program and event payments are ASSUMPTION or REAL rates, upgrades DERIVED from the repo feeder; " + REPORT},
        },
    )
    doc["meta"] = {
        "report": REPORT,
        "window": list(WINDOW),
        "learner": {"experts": [{"name": n, "shown": SHOWN.get(n, n), "family": FAMILY_OF[i]} for i, n in enumerate(learner_names)],
                    "families": [NAMES[a] for a in ("A1", "A2", "A3", "A4")]},
        "caveats": [
            "This is a model, not a forecast: the futures re-use real weather years with labelled adjustments.",
            "Backtests score energy trading only. Ancillary services, ERCOT's ADER caps and NYISO's reserves are not modelled.",
            "Today's observed weather stands in for a day-ahead forecast.",
            "New York's lead rests on a capacity price that is an assumption; ERCOT contract values are assumptions too.",
        ],
    }
    doc["algorithms"] = [{"id": a, "name": NAMES[a], **RULES[a]} for a in ALGOS]
    doc["sitingRules"] = [{"id": a, **SITING[a]} for a in ALGOS]
    doc["markets"] = {mk: market(mk, res, learner_names) for mk in MARKETS}
    doc["siting"] = siting(res)
    doc["programs"] = programs()
    doc["infrastructure"] = infrastructure(res)
    fd = res["forward"]["feeder"]
    c = "DERIVED: the repo's SMART-DS feeder (an Oncor-suburb stand-in) under the capacity planner's nameplate screen; " + REPORT
    doc["feeder"] = {
        "homes": labelled(fd["homes"], "REAL", "homes on the SMART-DS feeder (NREL synthetic data); " + REPORT),
        "free": labelled(fd["free_slots"], "DERIVED", c),
        "upgraded": labelled(fd["upgraded_slots"], "DERIVED", c),
        "upgrades": labelled(fd["upgrades"], "DERIVED", c),
        "sharePct": labelled(round(fd["share_needing_upgrade"] * 100), "DERIVED", c),
        "usdPerKwYr": lab(res["forward"]["upgrade_usd_per_kw_yr"], "DERIVED", c + "; $10,000 per upgrade (REAL), 12 years at 8% (ASSUMPTION)"),
    }
    n = write_json(OUT, doc)
    print(f"wrote {OUT.relative_to(ROOT).as_posix()} ({n / 1024:.1f} KB)")


if __name__ == "__main__":
    main()
