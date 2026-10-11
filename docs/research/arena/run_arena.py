"""Run the battery algorithm arena.

  python run_arena.py backtest   # walk-forward dispatch on real prices (both markets, both battery specs)
  python run_arena.py forward    # 2027-2031 scenario futures (home fleet)
  python run_arena.py report     # score everything, write results/*.json and results/*.csv
  python run_arena.py all

Data: ARENA_DATA (default C:/Users/Javier/Repo/BaseP/research-data) must hold the downloaded price files,
NOAA weather and FRED gas (see README.md). Runs use the research venv (numpy, openpyxl).
"""
import json
import pickle
import sys
import time
from concurrent.futures import ProcessPoolExecutor

import numpy as np

from arena import config as C
from arena import economics as E
from arena import siting
from arena.algorithms import ALGORITHMS
from arena.backtest import load_backtest, simulate_market
from arena.data import Market
from arena.models import RegionModels

ALGOS = [a for a, _ in ALGORITHMS]
NAMES = dict(ALGORITHMS)


# ----------------------------------------------------------------------------------- workers
def _bt_worker(args):
    market, spec = args
    simulate_market(market, spec)
    return market, spec


def _fw_worker(args):
    market, n = args
    from arena.forward import simulate_forward
    res, meta = simulate_forward(market, "home", n)
    with open(C.CACHE / f"forward_{market}.pkl", "wb") as f:
        pickle.dump({"res": res, "meta": meta}, f)
    return market


def cmd_backtest():
    jobs = [("ERCOT", "home"), ("NYISO", "home"), ("ERCOT", "grid"), ("NYISO", "grid")]
    with ProcessPoolExecutor(max_workers=4) as ex:
        for m, s in ex.map(_bt_worker, jobs):
            print("finished backtest", m, s, flush=True)


def cmd_forward(n=40):
    with ProcessPoolExecutor(max_workers=2) as ex:
        for m in ex.map(_fw_worker, [("ERCOT", n), ("NYISO", n)]):
            print("finished forward", m, flush=True)


# ----------------------------------------------------------------------------------- scoring helpers
def yearly(bt, algo, field="profit"):
    """(years, regions) $/MW per calendar year; partial 2026 annualised."""
    yrs = sorted(set(bt["years"]))
    arr = bt["algos"][algo][field]
    out = np.zeros((len(yrs), arr.shape[1]))
    for i, y in enumerate(yrs):
        sel = bt["years"] == y
        out[i] = arr[sel].sum(0) * (365.0 / sel.sum() if sel.sum() < 360 else 1.0)
    return yrs, out


def window_rows(yrs, vals, end, n):
    idx = [yrs.index(y) for y in range(end - n + 1, end + 1)]
    return vals[idx]


def score_backtests(spec="home"):
    out = {"spec": spec, "markets": {}}
    for mk in ("ERCOT", "NYISO"):
        bt = load_backtest(mk, spec)
        zones = bt["zones"]
        yrs, orc = yearly(bt, "ORACLE")
        league = {}
        for a in ALGOS:
            _, v = yearly(bt, a)
            _, mwh = yearly(bt, a, "mwh")
            row = {}
            for n in C.HORIZONS:
                w = window_rows(yrs, v, C.BACKTEST_END, n)
                wm = window_rows(yrs, mwh, C.BACKTEST_END, n)
                wo = window_rows(yrs, orc, C.BACKTEST_END, n)
                row[f"{n}y"] = {
                    "usd_per_mw_yr_by_region": dict(zip(zones, (w.mean(0)).round(0).tolist())),
                    "usd_per_mw_yr_mean": float(w.mean()),
                    "usd_per_mw_total_mean": float(w.sum(0).mean()),
                    "usd_per_mwh": float(w.sum() / max(wm.sum(), 1e-9)),
                    "capture_vs_oracle": float(w.sum() / max(wo.sum(), 1e-9)),
                }
            row["by_year"] = {str(y): dict(zip(zones, v[i].round(0).tolist())) for i, y in enumerate(yrs)}
            league[a] = row
        league["ORACLE"] = {f"{n}y": {"usd_per_mw_yr_mean": float(window_rows(yrs, orc, C.BACKTEST_END, n).mean()),
                                      "usd_per_mw_yr_by_region": dict(zip(zones, window_rows(yrs, orc, C.BACKTEST_END, n)
                                                                         .mean(0).round(0).tolist()))}
                            for n in C.HORIZONS}
        # expert usage of the adaptive learner
        picks = bt["algos"]["A5"].get("picks")
        if picks is not None:
            names = ["Clockwork", "Forecaster", "Weather-regime", "Spike hunter q97", "Spike hunter q90",
                     "Forecaster (low carry)"]
            use = {}
            for r, z in enumerate(zones):
                cnt = np.bincount(picks[:, r], minlength=len(names))
                use[z] = dict(zip(names, (cnt / cnt.sum()).round(3).tolist()))
            league["A5_expert_share"] = use
        out["markets"][mk] = {"zones": zones, "years": yrs, "league": league}
    # siting portfolios: each market alone, and both markets together (2021-2025 overlap)
    out["portfolios"] = {}
    for scope in ("ERCOT", "NYISO", "BOTH"):
        mks = ["ERCOT", "NYISO"] if scope == "BOTH" else [scope]
        res = {}
        for a in ALGOS + ["A5ts"]:
            src = "A5" if a == "A5ts" else a
            vals, opts, yrs_common = [], [], None
            for mk in mks:
                bt = load_backtest(mk, spec)
                yrs, v = yearly(bt, src)
                vals.append((yrs, v))
                opts += [f"{mk}:{z}" for z in bt["zones"]]
            common = sorted(set.intersection(*[set(y for y in yv[0] if y <= C.BACKTEST_END) for yv in vals]))
            V = np.concatenate([yv[1][[yv[0].index(y) for y in common]] for yv in vals], 1)
            res[a] = {}
            for n in C.HORIZONS:
                y0 = common.index(C.BACKTEST_END - n + 1)
                p = siting.portfolio(a, V, V, y0, n, C.FLEET_MW_PER_YEAR, rng=np.random.default_rng(11))
                last = p["fleet"][-1]
                res[a][f"{n}y"] = {"usd_per_mw_yr": p["usd_per_mw_yr"], "profit_usd": float(p["profit"].sum()),
                                   "final_fleet": {opts[k]: float(last[k]) for k in np.nonzero(last)[0]}}
            # where would each algorithm put the next 10 MW, using everything through 2025?
            nxt = siting.allocate(a, V, len(common), C.FLEET_MW_PER_YEAR, rng=np.random.default_rng(12))
            res[a]["next_10mw"] = {opts[k]: float(nxt[k]) for k in np.nonzero(nxt)[0]}
        out["portfolios"][scope] = res
    return out


def region_equations():
    """What-if effects per region from the walk-forward equations, plus out-of-sample spike-model skill."""
    from arena.models import effects_table
    tables = {}
    for mk in ("ERCOT", "NYISO"):
        m = Market(mk)
        rm = RegionModels(m)
        skill = {}
        for y in (2019, 2021, 2023, 2024, 2025):
            if y not in rm.models:
                continue
            sel = np.where(m.years == y)[0]
            ph = np.array([[rm.spike_prob(d, r) for r in range(len(m.zones))] for d in sel], float)
            act = rm.y3[sel]
            clim = rm.y3[m.years < y].mean(0)
            skill[str(y)] = float(1 - np.mean((ph - act) ** 2) / np.mean((clim - act) ** 2))
        tables[mk] = {"effects": effects_table(m, rm), "spike_skill_vs_climatology": skill}
    return tables


# ----------------------------------------------------------------------------------- forward scoring
def forward_options(mk, res, meta):
    """Per algorithm: values (scenarios, years, options) $/MW-yr, availability, caps, option names."""
    zones = meta["zones"]
    years = meta["years"]
    lane_kind = meta["lane_kind"]
    N = max(n for n, *_ in lane_kind) + 1
    t_year = meta["t_year"]
    progs = meta["programs"]
    lanes_per = len(lane_kind) // N
    layout = lane_kind[:lanes_per]                 # (n=0, kind, r, prog)
    opts, caps = [], []
    for _, kind, r, prog in layout:
        opts.append(f"{mk}:{zones[r]}" if kind == "merchant" else f"{mk}:{zones[r]} [{prog}]")
        caps.append(np.inf if kind == "merchant" else progs[prog]["cap_mw"])
    no_lane = [p for p in progs.values() if p["window"] is None]
    for p in no_lane:
        for z in p["zones"]:
            opts.append(f"{mk}:{z} [{p['id']}]")
            caps.append(p["cap_mw"])
    up_kw_yr = E.upgrade_usd_per_kw_yr()
    spec_usable = (C.SPECS["home"]["energy_units"] - C.SPECS["home"]["reserve_units"]) * C.U
    out = {}
    for a in ALGOS:
        prof = res[a]["profit"]                    # (D, L)
        V = np.zeros((N, len(years), len(opts)))
        A = np.zeros((N, len(years), len(opts)), bool)
        for n in range(N):
            fam = meta["family"][n]
            for k, (_, kind, r, prog) in enumerate(layout):
                li = n * lanes_per + k
                for yi, y in enumerate(years):
                    sel = t_year == y
                    v = prof[sel, li].sum()
                    if mk == "NYISO":
                        v += E.ny_capacity_usd_kw_yr(zones[r], fam, "home") * 1000
                    ok = True
                    if kind == "program":
                        p = progs[prog]
                        ok = meta["exists"][(n, prog)] and y >= p["start"]
                        if ok:
                            n_ev = int(meta["event"][sel, li].sum())
                            v += p["usd_kw_yr"] * 1000 + n_ev * spec_usable * p["event_usd_mwh"]
                            if p["concentrated"]:
                                v -= up_kw_yr * 1000
                    V[n, yi, k] = v
                    A[n, yi, k] = ok
            base = len(layout)
            j = 0
            for p in no_lane:
                for z in p["zones"]:
                    for yi, y in enumerate(years):
                        ok = meta["exists"][(n, p["id"])] and y >= p["start"]
                        V[n, yi, base + j] = p["usd_kw_yr"] * 1000 if ok else 0.0
                        A[n, yi, base + j] = ok
                    j += 1
        out[a] = (V, A)
    return opts, np.array(caps, float), out


def history_rows(mk, a, opts, spec="home"):
    """Backtest values 2021-2025 mapped onto forward options (programs: merchant + payment - upgrade)."""
    bt = load_backtest(mk, spec)
    yrs, v = yearly(bt, a)
    rows = v[[yrs.index(y) for y in range(2021, 2026)]]
    zones = bt["zones"]
    H = np.zeros((5, len(opts)))
    progs = {p["id"]: p for p in E.programs_for(mk)}
    for k, o in enumerate(opts):
        body = o.split(":", 1)[1]
        z = body.split(" [")[0]
        prog = body.split(" [")[1][:-1] if " [" in body else None
        base = rows[:, zones.index(z)].copy()
        if mk == "NYISO":
            base += E.ny_capacity_usd_kw_yr(z, "base", "home") * 1000
        if prog:
            p = progs[prog]
            base = (base * (0.9 if p["merchant"] else 0.0)) + p["usd_kw_yr"] * 1000
            if p["concentrated"]:
                base -= E.upgrade_usd_per_kw_yr() * 1000
        H[:, k] = base
    return H


def score_forward():
    out = {"markets": {}}
    per_market = {}
    for mk in ("ERCOT", "NYISO"):
        with open(C.CACHE / f"forward_{mk}.pkl", "rb") as f:
            blob = pickle.load(f)
        res, meta = blob["res"], blob["meta"]
        opts, caps, vals = forward_options(mk, res, meta)
        per_market[mk] = (opts, caps, vals, meta, res)
    for scope in ("ERCOT", "NYISO", "BOTH"):
        mks = ["ERCOT", "NYISO"] if scope == "BOTH" else [scope]
        opts = sum([per_market[m][0] for m in mks], [])
        caps = np.concatenate([per_market[m][1] for m in mks])
        N = min(len(per_market[m][3]["family"]) for m in mks)
        board = {}
        for a in ALGOS + ["A5ts"]:
            src = "A5" if a == "A5ts" else a
            V = np.concatenate([per_market[m][2][src][0][:N] for m in mks], 2)
            Av = np.concatenate([per_market[m][2][src][1][:N] for m in mks], 2)
            H = np.concatenate([history_rows(m, src, per_market[m][0]) for m in mks], 1)
            per_h = {f"{n}y": [] for n in C.HORIZONS}
            first_alloc = None
            fleets = []
            for s in range(N):
                known = np.concatenate([H, V[s]], 0)          # 2021-2025 history then 2027-2031
                avail = np.concatenate([np.ones((5, len(opts)), bool), Av[s]], 0)
                rng = np.random.default_rng(100 + s)
                for n in C.HORIZONS:
                    p = siting.portfolio(a, known, known, 5, n, C.FLEET_MW_PER_YEAR, avail, caps, rng)
                    per_h[f"{n}y"].append(p["usd_per_mw_yr"])
                    if n == 5:
                        fleets.append(p["fleet"][-1])
                if first_alloc is None:
                    first_alloc = siting.allocate(a, known, 5, C.FLEET_MW_PER_YEAR, avail[5], caps,
                                                  np.random.default_rng(1))
            stats = {}
            for key, xs in per_h.items():
                xs = np.array(xs)
                stats[key] = {"mean": float(xs.mean()), "p10": float(np.percentile(xs, 10)),
                              "p50": float(np.percentile(xs, 50)), "p90": float(np.percentile(xs, 90)),
                              "all": xs.round(0).tolist()}
            mean_fleet = np.mean(fleets, 0)
            stats["fleet_2031_mean_mw"] = {opts[k]: round(float(mean_fleet[k]), 1) for k in np.argsort(-mean_fleet)
                                           if mean_fleet[k] > 0.05}
            stats["first_10mw_2027"] = {opts[k]: float(first_alloc[k]) for k in np.nonzero(first_alloc)[0]}
            board[a] = stats
        # win rates per horizon
        for key in [f"{n}y" for n in C.HORIZONS]:
            M = np.array([board[a][key]["all"] for a in ALGOS])     # (algos, N); the A5ts comparison row is not ranked
            # ties (identical portfolios, e.g. two algorithms filling the same fixed-price contract) share the win
            top = M >= M.max(0, keepdims=True) - 1.0
            wins = (top / top.sum(0, keepdims=True)).mean(1)
            for i, a in enumerate(ALGOS):
                board[a][key]["win_rate"] = float(wins[i])
        # option ranking: median 2027-2029 value per option, per algorithm and best-of
        ranking = {}
        for a in ALGOS:
            V = np.concatenate([per_market[m][2][a][0][:N] for m in mks], 2)
            Av = np.concatenate([per_market[m][2][a][1][:N] for m in mks], 2)
            v3 = np.where(Av[:, :3], V[:, :3], np.nan)
            med = np.nanmedian(v3.reshape(-1, v3.shape[2]), 0)
            p10 = np.nanpercentile(v3.reshape(-1, v3.shape[2]), 10, 0)
            ranking[a] = sorted([(opts[k], float(med[k]), float(p10[k])) for k in range(len(opts))
                                 if np.isfinite(med[k])], key=lambda x: -x[1])
        out["markets"][scope] = {"board": board, "ranking": ranking, "options": opts}
    # dispatch-only forward league: energy margin of the merchant lanes (no capacity, no program payments)
    disp = {}
    for mk in ("ERCOT", "NYISO"):
        opts, caps, vals, meta, res = per_market[mk]
        zones = meta["zones"]
        lanes_per = len(meta["lane_kind"]) // len(meta["family"])
        N = len(meta["family"])
        disp[mk] = {}
        for a in ALGOS:
            prof = res[a]["profit"]                                     # (D, L)
            ann = prof.sum(0) / len(meta["years"])                      # $/MW-yr per lane
            disp[mk][a] = {zones[r]: float(np.median([ann[n * lanes_per + r] for n in range(N)]))
                           for r in range(len(zones))}
            disp[mk][a]["backup_hours"] = res[a]["backup_hours"]
    out["dispatch_forward"] = disp
    # transmission upgrades: difference-in-differences on the zone's merchant value (A5 dispatch, energy only),
    # after-minus-before the event year within each scenario, versus the same split in scenarios without it
    infra = []
    for mk, z, ev, split in (("ERCOT", "LZ_WEST", "Permian 765-kV lines", None),
                             ("ERCOT", "LZ_NORTH", "DFW large loads land", 2029),
                             ("ERCOT", "LZ_SOUTH", "Valley import upgrade 2029", 2029),
                             ("NYISO", "LONGIL", "Propel NY 2030", 2030)):
        opts, caps, vals, meta, res = per_market[mk]
        zones = meta["zones"]
        r = zones.index(z)
        lanes_per = len(meta["lane_kind"]) // len(meta["family"])
        prof = res["A5"]["profit"]
        t_year = meta["t_year"]
        yrs = meta["years"]
        ann = np.array([[prof[t_year == y, n * lanes_per + r].sum() for y in yrs] for n in range(len(meta["family"]))])
        d_with, d_without = [], []
        for n, evs in enumerate(meta["events"]):
            hit = [e for e in evs if e.startswith(ev.split(" 20")[0])]
            y_ev = int(hit[0].split()[-1]) if hit and hit[0].split()[-1].isdigit() else split
            y_ev = y_ev or 2030
            pre = [i for i, y in enumerate(yrs) if y < y_ev]
            post = [i for i, y in enumerate(yrs) if y >= y_ev]
            if not pre or not post:
                continue
            (d_with if hit else d_without).append(ann[n, post].mean() - ann[n, pre].mean())
        if d_with and d_without:
            infra.append({"market": mk, "zone": z, "event": ev, "after_minus_before_with": float(np.mean(d_with)),
                          "after_minus_before_without": float(np.mean(d_without)),
                          "effect": float(np.mean(d_with) - np.mean(d_without)),
                          "scenarios_with": len(d_with), "scenarios_without": len(d_without)})
    out["infrastructure"] = infra
    # where the money comes from at the top sites (A5 dispatch, median scenario, 2027-29)
    decomp = []
    up = E.upgrade_usd_per_kw_yr() * 1000
    usable = (C.SPECS["home"]["energy_units"] - C.SPECS["home"]["reserve_units"]) * C.U
    for mk in ("ERCOT", "NYISO"):
        opts, caps, vals, meta, res = per_market[mk]
        zones, progs = meta["zones"], meta["programs"]
        lanes_per = len(meta["lane_kind"]) // len(meta["family"])
        layout = meta["lane_kind"][:lanes_per]
        prof = res["A5"]["profit"]
        sel = np.isin(meta["t_year"], [2027, 2028, 2029])
        for k, (_, kind, r, prog) in enumerate(layout):
            p = progs.get(prog) if prog else None
            rows = []
            for n in range(len(meta["family"])):
                if p and not meta["exists"][(n, prog)]:
                    continue
                li = n * lanes_per + k
                energy = prof[sel, li].sum() / 3
                cap = E.ny_capacity_usd_kw_yr(zones[r], meta["family"][n], "home") * 1000 if mk == "NYISO" else 0.0
                pay = ev_pay = upg = 0.0
                if p:
                    yrs_on = [y for y in (2027, 2028, 2029) if y >= p["start"]]
                    frac = len(yrs_on) / 3
                    pay = p["usd_kw_yr"] * 1000 * frac
                    n_ev = meta["event"][sel, li].sum() / 3
                    ev_pay = n_ev * usable * p["event_usd_mwh"]
                    upg = -up * frac if p["concentrated"] else 0.0
                rows.append((energy, cap, pay, ev_pay, upg))
            if not rows:
                continue
            m_ = np.median(np.array(rows), 0)
            decomp.append({"option": opts[k], "energy": float(m_[0]), "capacity": float(m_[1]), "program": float(m_[2]),
                           "event_energy": float(m_[3]), "upgrades": float(m_[4]), "total": float(m_[:5].sum())})
        for p in [p for p in progs.values() if p["window"] is None]:
            for z in p["zones"]:
                decomp.append({"option": f"{mk}:{z} [{p['id']}]", "energy": 0.0, "capacity": 0.0,
                               "program": p["usd_kw_yr"] * 1000, "event_energy": 0.0, "upgrades": 0.0,
                               "total": p["usd_kw_yr"] * 1000})
    out["decomposition"] = sorted(decomp, key=lambda d: -d["total"])
    out["feeder"] = E.feeder()
    out["upgrade_usd_per_kw_yr"] = E.upgrade_usd_per_kw_yr()
    return out


def cmd_report():
    t = time.time()
    C.OUT.mkdir(parents=True, exist_ok=True)
    rep = {"backtest_home": score_backtests("home"), "backtest_grid": score_backtests("grid")}
    try:
        rep["forward"] = score_forward()
    except FileNotFoundError as e:
        print("forward results missing:", e)
    rep["equations"] = region_equations()
    with open(C.OUT / "arena_results.json", "w", encoding="utf-8") as f:
        json.dump(rep, f, indent=1, default=float)
    print(f"report written in {time.time() - t:.0f}s ->", C.OUT / "arena_results.json")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "all"
    if cmd in ("backtest", "all"):
        cmd_backtest()
    if cmd in ("forward", "all"):
        cmd_forward(int(sys.argv[2]) if len(sys.argv) > 2 else 40)
    if cmd in ("report", "all"):
        cmd_report()
