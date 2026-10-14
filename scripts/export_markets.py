#!/usr/bin/env python3
"""Export the grid-research arena (docs/research/arena/) to the story app's Markets page: ui/data/markets/index.json.

    ARENA_DATA=<research-data dir> <python with numpy> scripts/export_markets.py

Reads (never writes) the arena's committed results and its caches outside the repo:
  docs/research/arena/results/arena_results.json   equations, revenue stacks, site ranking, transmission effects
  $ARENA_DATA/arena_cache/backtest_<M>_home.npz     daily P&L of every algorithm on real prices, 2015 to Sep 2026
  $ARENA_DATA/arena_cache/prices_<M>.npz            the 15-minute zone prices the backtest traded
  $ARENA_DATA/arena_cache/forward_<M>.pkl           daily P&L on the 2027-31 futures (20 per market)
$ARENA_DATA defaults to ../research-data beside the repo (docs/research/arena/arena/config.py says the same).

Writes one file in the sim.contracts envelope (schema hb.markets.v1). Headline numbers are labelled dicts; the
per-year and per-month arrays are bulk data labelled once in `series`. Deterministic: the same caches give
byte-identical output. No language model produces any number here: the arena's deterministic code did.
"""
import datetime as dt
import json
import os
import pickle
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from sim.contracts import envelope, labelled, write_json  # noqa: E402

ARENA = ROOT / "docs" / "research" / "arena"
CACHE = Path(os.environ.get("ARENA_DATA", str(ROOT.parent / "research-data"))) / "arena_cache"
OUT = ROOT / "ui" / "data" / "markets" / "index.json"
REPORT = "docs/research/arena/README.md"

HIST_YEARS = list(range(2015, 2027))
SPIKE_USD = 250.0            # ASSUMPTION: one "spike hour" threshold for both markets
LABEL = {"LZ_NORTH": "DFW", "LZ_HOUSTON": "Houston", "LZ_SOUTH": "South / Valley", "LZ_WEST": "West / Permian",
         "LZ_AEN": "Austin Energy", "LZ_CPS": "San Antonio", "LZ_LCRA": "LCRA co-ops", "LZ_RAYBN": "Rayburn co-ops",
         "WEST": "West (A)", "GENESE": "Genesee (B)", "CENTRL": "Central (C)", "NORTH": "North Country (D)",
         "MHK VL": "Mohawk Valley (E)", "CAPITL": "Capital (F)", "HUD VL": "Hudson Valley (G)", "MILLWD": "Millwood (H)",
         "DUNWOD": "Dunwoodie (I)", "N.Y.C.": "NYC (J)", "LONGIL": "Long Island (K)"}
STATION = {"LZ_NORTH": "DFW Airport", "LZ_HOUSTON": "Houston IAH", "LZ_SOUTH": "Corpus Christi · Brownsville",
           "LZ_WEST": "Midland · Abilene", "LZ_AEN": "Austin-Bergstrom", "LZ_CPS": "San Antonio",
           "LZ_LCRA": "Austin · San Antonio", "LZ_RAYBN": "DFW Airport (nearest)", "WEST": "Buffalo",
           "GENESE": "Rochester", "CENTRL": "Syracuse", "NORTH": "Massena", "MHK VL": "Albany", "CAPITL": "Albany",
           "HUD VL": "Poughkeepsie", "MILLWD": "White Plains", "DUNWOD": "White Plains",
           "N.Y.C.": "Central Park · LaGuardia", "LONGIL": "Islip"}
# ASSUMPTION: zones grouped by the weather that drives them (ERCOT's weather-zone names; NYISO by shared station)
WEATHER = {
    "ERCOT": [("North Central", "DFW Airport", ["LZ_NORTH", "LZ_RAYBN"]),
              ("Coast", "Houston IAH", ["LZ_HOUSTON"]),
              ("South Central", "Austin · San Antonio", ["LZ_AEN", "LZ_CPS", "LZ_LCRA"]),
              ("Southern", "Corpus Christi · Brownsville", ["LZ_SOUTH"]),
              ("Far West", "Midland · Abilene", ["LZ_WEST"])],
    "NYISO": [("Lake Erie", "Buffalo · Rochester", ["WEST", "GENESE"]),
              ("Central & North", "Syracuse · Massena", ["CENTRL", "NORTH"]),
              ("Capital", "Albany", ["MHK VL", "CAPITL"]),
              ("Lower Hudson", "Poughkeepsie · White Plains", ["HUD VL", "MILLWD", "DUNWOD"]),
              ("New York City", "Central Park · LaGuardia", ["N.Y.C."]),
              ("Long Island", "Islip", ["LONGIL"])],
}
FACTORS = ["summer_tmax_f", "summer_spread_usd", "summer_spike_pct", "summer_shock_spread_pct", "summer_shock_spike_pct",
           "winter_tmax_f", "winter_spread_usd", "winter_spike_pct", "winter_shock_spread_pct", "winter_shock_spike_pct",
           "gas50_level_pct", "gas50_spread_pct", "spike_after_spike_pct"]

C_REAL = "SIM: the adaptive learner (A5) traded walk-forward on the ISO's real prices, no look-ahead; " + REPORT
C_CEIL = "SIM: perfect-foresight dispatch on the same real prices, the most any strategy could earn; " + REPORT
C_FC = "SIM: the adaptive learner on 20 futures per market built from real weather years with gas, saturation, " \
       "transmission and outage adjustments (ASSUMPTION inputs); " + REPORT
C_STAT = "DERIVED: arithmetic on the SIM forward results (100 future-years per zone)"
C_FACT = "DERIVED: the zone's fitted ridge equations on REAL prices, NOAA weather and Henry Hub gas, out of sample; " + REPORT


def r1(x):
    return round(float(x), 1)


def q(a, p):
    return float(np.percentile(a, p))


def lab(v, label, cite):
    return labelled(r1(v) if isinstance(v, (float, np.floating)) else v, label, cite)


def market(mk, res):
    bt = np.load(CACHE / f"backtest_{mk}_home.npz", allow_pickle=True)
    dates = [str(d) for d in bt["dates"]]
    zones = [str(z) for z in bt["zones"]]
    yrs = np.array([int(d[:4]) for d in dates])
    mon = np.array([int(d[5:7]) for d in dates])
    pz = np.load(CACHE / f"prices_{mk}.npz", allow_pickle=True)
    pzones = [str(z) for z in pz["zones"]]
    P = pz["P"]
    pyrs = np.array([int(str(d)[:4]) for d in pz["dates"]])
    with open(CACHE / f"forward_{mk}.pkl", "rb") as f:
        fw = pickle.load(f)
    meta = fw["meta"]
    lk = meta["lane_kind"]
    ty = np.asarray(meta["t_year"])
    fyears = [int(y) for y in meta["years"]]
    nscen = len(meta["family"])
    d0 = dt.date(fyears[0], 1, 1)
    fmon = np.array([(d0 + dt.timedelta(i)).month for i in range(len(ty))])
    A5, A1 = fw["res"]["A5"]["profit"], fw["res"]["A1"]["profit"]
    eq = {e["zone"]: e for e in res["equations"][mk]["effects"]}

    def lanes(zi):
        return [i for i, (n, kind, r, prog) in enumerate(lk) if kind == "merchant" and r == zi]

    out_z = []
    for zi, z in enumerate(zones):
        H = {k: [] for k in ("realised", "clockwork", "ceiling", "price", "spread", "spikeHours", "top10Pct")}
        partial = []
        for y in HIST_YEARS:
            m = yrs == y
            pr = P[pyrs == y, pzones.index(z), :]
            prof = bt["A5_profit"][m, zi]
            H["realised"].append(r1(prof.sum() / 1000))
            H["clockwork"].append(r1(bt["A1_profit"][m, zi].sum() / 1000))
            H["ceiling"].append(r1(bt["ORACLE_profit"][m, zi].sum() / 1000))
            H["price"].append(r1(np.nanmean(pr)))
            H["spread"].append(r1(np.nanmean(np.nanmax(pr, 1) - np.nanmin(pr, 1))))
            H["spikeHours"].append(r1((pr >= SPIKE_USD).sum() / 4))
            H["top10Pct"].append(round(float(np.sort(prof)[::-1][:10].sum() / max(prof.sum(), 1e-9)) * 100))
            partial.append(bool(m.sum() < 360))
        hm = (yrs >= 2021) & (yrs <= 2025)
        hmon = [bt["A5_profit"][hm & (mon == k), zi].sum() for k in range(1, 13)]
        L = lanes(zi)
        F = {k: [] for k in ("p10", "p50", "p90", "mean", "clockwork")}
        allv, t10 = [], []
        for y in fyears:
            m = ty == y
            v = A5[m][:, L].sum(0) / 1000
            allv += list(v)
            F["p10"].append(r1(q(v, 10)))
            F["p50"].append(r1(q(v, 50)))
            F["p90"].append(r1(q(v, 90)))
            F["mean"].append(r1(v.mean()))
            F["clockwork"].append(r1(np.median(A1[m][:, L].sum(0) / 1000)))
            for j in L:
                s = A5[m, j].sum()
                if s > 0:
                    t10.append(np.sort(A5[m, j])[::-1][:10].sum() / s)
        allv = np.array(allv)
        fmonv = [A5[:, L][fmon == k].sum() for k in range(1, 13)]
        h5 = [(H["realised"][i], H["ceiling"][i]) for i, y in enumerate(HIST_YEARS) if 2021 <= y <= 2025]
        e = eq[z]
        out_z.append({
            "id": z, "label": LABEL[z], "station": STATION[z],
            "weather": next(w[0] for w in WEATHER[mk] if z in w[2]),
            "hist": H, "partial": partial, "fc": F,
            "season": {"realised": [r1(v / sum(hmon) * 100) for v in hmon],
                       "forecast": [r1(v / sum(fmonv) * 100) for v in fmonv]},
            "headline": {
                "mean": lab(allv.mean(), "SIM", C_FC), "median": lab(np.mean(F["p50"]), "DERIVED", C_STAT),
                "std": lab(allv.std(), "DERIVED", C_STAT), "p10": lab(q(allv, 10), "DERIVED", C_STAT),
                "p90": lab(q(allv, 90), "DERIVED", C_STAT),
                "meanOverStd": labelled(round(float(allv.mean() / allv.std()), 2), "DERIVED", C_STAT + "; not a time-series Sharpe"),
                "top10Pct": labelled(round(float(np.median(t10)) * 100), "DERIVED", C_STAT + "; median share of a year's P&L from its 10 best days"),
                "below15Pct": labelled(round(float((allv < 15).mean()) * 100), "DERIVED", C_STAT),
                "capturePct": labelled(round(sum(a for a, _ in h5) / sum(c for _, c in h5) * 100), "DERIVED",
                                       "DERIVED: realised ÷ perfect-foresight ceiling, 2021-25 (both SIM)"),
                "edge": lab(np.mean(F["p50"]) - np.mean(F["clockwork"]), "DERIVED",
                            C_STAT + "; median learner minus median fixed schedule (A1 Clockwork)"),
            },
            "factors": {k: lab(e[k], "DERIVED", C_FACT) for k in FACTORS},
        })

    out_w = []
    for name, stn, members in WEATHER[mk]:
        idx = [zones.index(z) for z in members]
        F = {k: [] for k in ("p10", "p50", "p90")}
        allv = []
        for y in fyears:
            m = ty == y
            v = np.mean([A5[m][:, lanes(zi)].sum(0) / 1000 for zi in idx], 0)
            allv += list(v)
            F["p10"].append(r1(q(v, 10)))
            F["p50"].append(r1(q(v, 50)))
            F["p90"].append(r1(q(v, 90)))
        allv = np.array(allv)
        zs = [x for x in out_z if x["id"] in members]
        realised = [r1(np.mean([x["hist"]["realised"][i] for x in zs])) for i, y in enumerate(HIST_YEARS) if 2021 <= y <= 2025]
        effs = [eq[z] for z in members]

        def avg(k):
            return lab(np.mean([ef[k] for ef in effs]), "DERIVED", C_FACT + "; mean of the region's zones")

        out_w.append({
            "name": name, "station": stn, "zones": members, "labels": [LABEL[z] for z in members],
            "realised2021to2025": realised, "fc": F,
            "season": [r1(np.mean([x["season"]["forecast"][k] for x in zs])) for k in range(12)],
            "headline": {"mean": lab(allv.mean(), "SIM", C_FC + "; zones averaged within each future"),
                         "std": lab(allv.std(), "DERIVED", C_STAT), "p10": lab(q(allv, 10), "DERIVED", C_STAT),
                         "p90": lab(q(allv, 90), "DERIVED", C_STAT)},
            "factors": {k: avg(k) for k in FACTORS},
        })

    fam = list(meta["family"])
    # [[family, count]]: a list, because "relief" (a NY family) is also a headline key name in sim.contracts
    return {"lastDate": dates[-1], "futures": nscen, "families": [[f, fam.count(f)] for f in sorted(set(fam))],
            "histYears": HIST_YEARS, "fcYears": fyears, "zones": out_z, "weather": out_w}


def stacks(res):
    words = {"Con Ed load relief": "Con Ed load relief", "Austin Energy tolling": "Austin Energy tolling",
             "TX co-op peak": "co-op peak deal", "Utility battery program": "utility battery program"}
    pick = ["NYISO:N.Y.C. [Con Ed load relief]", "NYISO:LONGIL [Utility battery program]",
            "ERCOT:LZ_AEN [Austin Energy tolling]", "ERCOT:LZ_LCRA [TX co-op peak]"]
    by = {d["option"]: d for d in res["forward"]["decomposition"]}
    out = []
    for o in pick:
        d = by[o]
        mk, rest = o.split(":", 1)
        z, prog = (rest.split(" [", 1) + [""])[:2]
        prog = prog.rstrip("]")
        c = "SIM/ASSUMPTION: " + REPORT + " (median future, 2027-29); program payments and NY capacity prices are ASSUMPTION"
        out.append({"market": mk, "zone": z, "site": f"{LABEL[z]} + {words.get(prog, prog)}",
                    "energy": lab(d["energy"] / 1000, "SIM", c), "capacity": lab(d["capacity"] / 1000, "ASSUMPTION", c),
                    "program": lab(d["program"] / 1000, "ASSUMPTION", c), "event": lab(d["event_energy"] / 1000, "ASSUMPTION", c),
                    "upgrades": lab(d["upgrades"] / 1000, "DERIVED", "DERIVED: feeder transformer upgrades, $10,000 each, "
                                    "12 years at 8% (sim/planner data)"),
                    "total": lab(d["total"] / 1000, "SIM", c)})
    # the trading-only baseline the stacks are measured against: DFW merchant, median 2027-29 (site ranking)
    rk = {o: (med, p10) for o, med, p10 in res["forward"]["markets"]["ERCOT"]["ranking"]["A5"]}
    med = rk["ERCOT:LZ_NORTH"][0]
    c = "SIM: " + REPORT + " site ranking, median 2027-29"
    out.append({"market": "ERCOT", "zone": "LZ_NORTH", "site": "DFW, trading only",
                "energy": lab(med / 1000, "SIM", c), "capacity": lab(0.0, "SIM", c), "program": lab(0.0, "SIM", c),
                "event": lab(0.0, "SIM", c), "upgrades": lab(0.0, "SIM", c), "total": lab(med / 1000, "SIM", c)})
    return out


def main():
    res = json.loads((ARENA / "results" / "arena_results.json").read_text(encoding="utf-8"))
    cites = {"core": "sim/constants.py CORE_POWER_KW, RESERVE_FLOOR; docs/research/arena/arena/config.py SPECS"}
    doc = envelope(
        "markets", "scripts.export_markets",
        inputs={"prices_sha256": None, "loads_sha256": None, "topology_sha256": None},
        constants={
            "BATTERY_KW": {"value": 20, "label": "REAL", "cite": "Base Core 20 kW; " + cites["core"]},
            "RESERVE_FLOOR": {"value": 0.20, "label": "REAL", "cite": "20% member backup reserve; " + cites["core"]},
            "ROUND_TRIP": {"value": 0.89, "label": "ASSUMPTION", "cite": "CORE_RTE; " + cites["core"]},
            "WEAR_USD_MWH": {"value": 20, "label": "ASSUMPTION", "cite": "wear cost per MWh discharged; docs/research/arena/arena/config.py"},
            "SPIKE_USD_MWH": {"value": SPIKE_USD, "label": "ASSUMPTION", "cite": "one spike-hour threshold for both markets; scripts/export_markets.py"},
            "FUTURES": {"value": 20, "label": "ASSUMPTION", "cite": "futures per market; docs/research/arena/arena/forward.py"},
            "NY_CAPACITY_NYC": {"value": 18, "label": "ASSUMPTION", "cite": "$/kW-month NYC base case; docs/research/arena/arena/economics.py NY_CAP_PRICE"},
        },
        sources={
            "ercot": {"label": "REAL", "text": "ERCOT NP6-785-ER Settlement Point Prices, 15-minute, 2011 to 26 Sep 2026 (hashes: docs/research/data/ercot/SOURCE-SHA256SUMS.txt)"},
            "nyiso": {"label": "REAL", "text": "NYISO real-time zonal LBMP archive, 2003 to Sep 2026, time-weighted to 15 minutes (docs/research/data/nyiso/SOURCE-SHA256SUMS.txt)"},
            "weather": {"label": "REAL", "text": "NOAA GHCN-Daily, 18 airport stations (NCEI access service)"},
            "gas": {"label": "REAL", "text": "EIA Henry Hub daily spot (rngwhhdD)"},
            "arena": {"label": "SIM", "text": REPORT + ": five algorithms, walk-forward backtest and the 2027-31 tournament"},
        },
        series={
            "hist.realised": {"label": "SIM", "unit": "$/kW-yr", "text": C_REAL},
            "hist.clockwork": {"label": "SIM", "unit": "$/kW-yr", "text": "SIM: A1 Clockwork, a fixed daily schedule from the last four weeks"},
            "hist.ceiling": {"label": "SIM", "unit": "$/kW-yr", "text": C_CEIL},
            "hist.price": {"label": "DERIVED", "unit": "$/MWh", "text": "DERIVED: time-weighted mean of the REAL zone price"},
            "hist.spread": {"label": "DERIVED", "unit": "$/MWh", "text": "DERIVED: mean of each day's highest minus lowest 15-minute REAL price"},
            "hist.spikeHours": {"label": "DERIVED", "unit": "hours", "text": "DERIVED: hours at or above SPIKE_USD_MWH"},
            "hist.top10Pct": {"label": "DERIVED", "unit": "%", "text": "DERIVED: share of the year's realised P&L from its 10 best days"},
            "fc": {"label": "SIM", "unit": "$/kW-yr", "text": C_FC + "; P10, median, P90 and mean across futures per year"},
            "fc.clockwork": {"label": "SIM", "unit": "$/kW-yr", "text": "SIM: A1 Clockwork on the same futures, median"},
            "season": {"label": "DERIVED", "unit": "%", "text": "DERIVED: share of realised (2021-25) or forecast (2027-31) P&L by calendar month"},
            "weather.realised2021to2025": {"label": "SIM", "unit": "$/kW-yr", "text": C_REAL + "; mean of the region's zones"},
        },
    )
    doc["meta"] = {
        "battery": "20 kW Core-class home battery, 20% backup reserve, 89% round trip, $20/MWh wear; energy trading only",
        "money": "Gross trading value per kW of battery per year, not Base's profit",
        "report": REPORT,
        "weatherNote": {"ERCOT": "ERCOT forecasts load by weather zone but settles home batteries by load zone. "
                                 "These groups use ERCOT's weather-zone names; the station choice is an ASSUMPTION.",
                        "NYISO": "NYISO's zones A-K are both settlement and forecast areas; here they are grouped "
                                 "by shared weather station (ASSUMPTION)."},
    }
    doc["markets"] = {mk: market(mk, res) for mk in ("ERCOT", "NYISO")}
    c_inf = "SIM: difference-in-differences on the zone's trading value, futures with vs without the event; " + REPORT
    doc["infrastructure"] = [{"event": d["event"], "zone": d["zone"], "label": LABEL[d["zone"]],
                              "effect": lab(d["effect"] / 1000, "SIM", c_inf)} for d in res["forward"]["infrastructure"]]
    doc["stacks"] = stacks(res)
    n = write_json(OUT, doc)
    print(f"wrote {OUT.relative_to(ROOT).as_posix()} ({n / 1024:.1f} KB)")


if __name__ == "__main__":
    main()
