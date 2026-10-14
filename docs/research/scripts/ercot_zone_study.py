"""ERCOT load-zone and hub real-time price study, 2021-2026.

Source: ERCOT NP6-785-ER "Historical RTM Load Zone and Hub Prices" (report type 13061),
one yearly zip per year, unzipped into ./ercot/. Prices are Real-Time Market Settlement
Point Prices (RTM SPP), $/MWh, 15-minute settlement intervals, hour-ending clock.

Outputs (./out/):
  zone_year_stats.csv     price distribution per settlement point per year
  zone_basis.csv          separation of each load zone / hub from HB_BUSAVG (congestion signature)
  battery_value.csv       perfect-foresight 1 MW / 2 MWh arbitrage value per point per year
  event_windows.csv       prices during named grid events
  top_days.csv            the 10 highest-price days per year (HB_BUSAVG daily max) and the zone spread
  summary.json            everything above in one file

Run:  .venv/Scripts/python.exe ercot_zone_study.py
"""
import csv
import glob
import json
import os
import statistics
from collections import defaultdict
from datetime import date

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, "ercot")
OUT = os.path.join(HERE, "out")
YEARS = [2021, 2022, 2023, 2024, 2025, 2026]
REF = "HB_BUSAVG"  # ERCOT-wide average of all electrical buses; the "system" reference

# Battery model (upper bound): 1 MW / 2 MWh, 85% round trip, one full cycle per day,
# empty at midnight, perfect foresight of RTM prices. Loss is charged on the charge side.
P_MW, E_STEPS, RTE, DT_H = 1.0, 8, 0.85, 0.25

EVENTS = [
    ("Winter Storm Uri", "2021-02-14", "2021-02-20"),
    ("Sep 6 2023 EEA2", "2023-09-06", "2023-09-06"),
    ("Winter Storm Heather", "2024-01-15", "2024-01-17"),
    ("Houston derecho", "2024-05-16", "2024-05-17"),
    ("Hurricane Beryl", "2024-07-08", "2024-07-10"),
]


def cache_csv(year):
    """xlsx -> csv once (openpyxl is slow); LZEW rows (energy-weighted variant) are dropped."""
    path = os.path.join(SRC, f"rtm{year}.csv")
    if os.path.exists(path):
        return path
    import openpyxl
    xlsx = glob.glob(os.path.join(SRC, f"*RTMLZHBSPP_{year}.xlsx"))[0]
    wb = openpyxl.load_workbook(xlsx, read_only=True)
    with open(path + ".tmp", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["date", "hour", "interval", "rep", "sp", "sptype", "price"])
        for ws in wb.worksheets:
            for r in ws.iter_rows(min_row=2, values_only=True):
                if len(r) < 7 or r[0] is None or r[6] is None or r[5] == "LZEW":
                    continue
                if isinstance(r[0], str):  # "MM/DD/YYYY"
                    m, d, y = r[0].split("/")
                    iso = f"{y}-{m}-{d}"
                else:  # some sheets store a real date cell
                    iso = r[0].date().isoformat()
                w.writerow([iso, r[1], r[2], r[3], r[4], r[5], r[6]])
    os.replace(path + ".tmp", path)
    return path


def load(year):
    """{sp: {date: [(hour, interval, rep, price), ...]}}"""
    data = defaultdict(lambda: defaultdict(list))
    with open(cache_csv(year), encoding="utf-8") as f:
        for row in csv.DictReader(f):
            data[row["sp"]][row["date"]].append(
                (int(row["hour"]), int(row["interval"]), row["rep"], float(row["price"])))
    for sp in data:
        for d in data[sp]:
            data[sp][d].sort(key=lambda t: (t[0], t[2] == "Y", t[1]))
    return data


def pct(xs, q):
    return float(np.percentile(xs, q))


def zone_stats(prices):
    a = np.array(prices)
    return {
        "n": int(a.size),
        "mean": round(float(a.mean()), 2),
        "median": round(float(np.median(a)), 2),
        "p95": round(pct(a, 95), 2),
        "p99": round(pct(a, 99), 2),
        "max": round(float(a.max()), 2),
        "min": round(float(a.min()), 2),
        "n_ge_100": int((a >= 100).sum()),
        "n_ge_1000": int((a >= 1000).sum()),
        "n_ge_3000": int((a >= 3000).sum()),
        "n_lt_0": int((a < 0).sum()),
        "pct_hours_negative": round(100 * float((a < 0).mean()), 2),
        # share of the year's total $ (price x 0.25 MWh for a flat 1 MW load) earned in the top 1% of intervals
        "top1pct_share_of_cost": round(100 * float(np.sort(a)[-max(1, a.size // 100):].sum() / a.sum()), 1)
        if a.sum() > 0 else None,
    }


def battery_daily(days):
    """Vectorised DP over days. days: list of price lists (92/96/100 intervals). Returns $ per day."""
    L = max(len(p) for p in days)
    P = np.full((len(days), L), np.nan)
    for i, p in enumerate(days):
        P[i, :len(p)] = p
    S, C = E_STEPS + 1, E_STEPS + 1  # soc 0..8, discharge-intervals used 0..8
    NEG = -1e18
    V = np.full((len(days), S, C), NEG)
    V[:, 0, 0] = 0.0
    for t in range(L):
        price = P[:, t][:, None, None]
        valid = ~np.isnan(price)
        new = V.copy()  # idle
        # charge: soc s-1 -> s, cost price * energy drawn
        ch = V[:, :-1, :] - np.where(valid, price, np.inf) * P_MW * DT_H / RTE
        new[:, 1:, :] = np.maximum(new[:, 1:, :], ch)
        # discharge: soc s+1 -> s, count c-1 -> c
        dis = V[:, 1:, :-1] + np.where(valid, price, -np.inf) * P_MW * DT_H
        new[:, :-1, 1:] = np.maximum(new[:, :-1, 1:], dis)
        V = new
    return V[:, 0, :].max(axis=1)  # end empty


def main():
    os.makedirs(OUT, exist_ok=True)
    stats, basis, batt, events, tops = [], [], [], [], []
    for year in YEARS:
        data = load(year)
        sps = sorted(data)
        dates = sorted(data[REF])
        ref_series = [p for d in dates for (_, _, _, p) in data[REF][d]]
        print(year, len(dates), "days", len(sps), "points", flush=True)
        for sp in sps:
            series = [p for d in dates for (_, _, _, p) in data[sp].get(d, [])]
            row = {"year": year, "sp": sp, "days": len(dates), **zone_stats(series)}
            stats.append(row)
            if sp != REF and len(series) == len(ref_series):
                diff = np.array(series) - np.array(ref_series)
                basis.append({
                    "year": year, "sp": sp,
                    "mean_basis": round(float(diff.mean()), 2),
                    "n_above_ref_100": int((diff > 100).sum()),
                    "n_below_ref_100": int((diff < -100).sum()),
                    "n_above_ref_20": int((diff > 20).sum()),
                    "n_below_ref_20": int((diff < -20).sum()),
                    "max_above_ref": round(float(diff.max()), 2),
                    "max_below_ref": round(float(diff.min()), 2),
                })
            daily = battery_daily([[p for (_, _, _, p) in data[sp][d]] for d in dates])
            srt = np.sort(daily)[::-1]
            batt.append({
                "year": year, "sp": sp, "days": len(dates),
                "usd_per_mw": round(float(daily.sum()), 0),
                "usd_per_kw_yr_annualised": round(float(daily.sum()) / len(dates) * 365 / 1000, 2),
                "top10_days_share_pct": round(100 * float(srt[:10].sum() / daily.sum()), 1),
                "median_day_usd": round(float(np.median(daily)), 0),
                "best_day": dates[int(np.argmax(daily))],
                "best_day_usd": round(float(daily.max()), 0),
            })
        for name, a, b in EVENTS:
            if int(a[:4]) != year:
                continue
            for sp in sps:
                win = [p for d in dates if a <= d <= b for (_, _, _, p) in data[sp][d]]
                events.append({"event": name, "start": a, "end": b, "sp": sp,
                               "max": round(max(win), 2), "mean": round(statistics.fmean(win), 2),
                               "n_ge_1000": sum(p >= 1000 for p in win)})
        # top 10 days by system (HB_BUSAVG) daily max, with the zone that peaked highest
        daymax = sorted(((max(p for (_, _, _, p) in data[REF][d]), d) for d in dates), reverse=True)[:10]
        for mx, d in daymax:
            z = {sp: max(p for (_, _, _, p) in data[sp][d]) for sp in sps if sp.startswith("LZ_")}
            hi = max(z, key=z.get); lo = min(z, key=z.get)
            tops.append({"year": year, "date": d, "busavg_max": round(mx, 2),
                         "highest_zone": hi, "highest_zone_max": round(z[hi], 2),
                         "lowest_zone": lo, "lowest_zone_max": round(z[lo], 2)})

    def dump(name, rows):
        with open(os.path.join(OUT, name), "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=list(rows[0]))
            w.writeheader(); w.writerows(rows)

    dump("zone_year_stats.csv", stats); dump("zone_basis.csv", basis)
    dump("battery_value.csv", batt); dump("event_windows.csv", events); dump("top_days.csv", tops)
    with open(os.path.join(OUT, "summary.json"), "w", encoding="utf-8") as f:
        json.dump({"source": "ERCOT NP6-785-ER RTM SPP (report 13061), yearly files 2021-2026",
                   "generated": date.today().isoformat(),
                   "battery_model": {"mw": P_MW, "mwh": P_MW * E_STEPS * DT_H, "rte": RTE,
                                     "cycles_per_day": 1, "foresight": "perfect (upper bound)"},
                   "stats": stats, "basis": basis, "battery": batt, "events": events, "top_days": tops},
                  f, indent=1)
    print("done ->", OUT)


if __name__ == "__main__":
    main()
