"""NYISO zonal real-time price study, 2003-2026 (same method as ercot_zone_study.py).

Source: NYISO public "Real-Time Zonal LBMP" files, one monthly zip per month
(http://mis.nyiso.com/public/csv/realtime/YYYYMM01realtime_zone_csv.zip), unzipped on the fly
from ./nyiso/zips/. Each daily CSV has: Time Stamp, Name, PTID, LBMP ($/MWHr),
Marginal Cost Losses ($/MWHr), Marginal Cost Congestion ($/MWHr).

Conventions handled here:
  * Time stamps mark the END of a dispatch interval (e.g. 00:05 ... next day 00:00). Before ~2005 the
    real-time intervals were irregular (00:00:36, 00:01:35, ...), so every price is time-weighted.
  * Each zone-day is integrated into 15-minute time-weighted averages, the same resolution as
    ERCOT's 15-minute settlement point prices, so the two studies compare like for like.
  * NYISO publishes the congestion column with the opposite sign (negative where the zone is MORE
    expensive than the reference bus), so congestion premium = -column.
  * Only the 11 NYCA load zones A-K are analysed; the external proxy buses (PJM, H Q, NPX, O H) are dropped.

Outputs (./out_nyiso/): zone_year_stats.csv, battery_value.csv, event_windows.csv, top_days.csv,
hour_profile.csv, summary.json.  Cache: ./nyiso/cache/q15_YYYY.npz
"""
import csv
import glob
import io
import json
import os
import zipfile
from collections import defaultdict
from datetime import date, datetime

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ZIPS = os.path.join(HERE, "nyiso", "zips")
CACHE = os.path.join(HERE, "nyiso", "cache")
OUT = os.path.join(HERE, "out_nyiso")
YEARS = list(range(2003, 2027))
ZONES = ["WEST", "GENESE", "CENTRL", "NORTH", "MHK VL", "CAPITL", "HUD VL", "MILLWD", "DUNWOD", "N.Y.C.", "LONGIL"]
LETTER = dict(zip(ZONES, "ABCDEFGHIJK"))
BUCKET = 900  # seconds (15 minutes)

# Same battery as the ERCOT study: 1 MW / 2 MWh, 85% round trip, one cycle per day, empty at midnight,
# perfect foresight (an upper bound on energy arbitrage, not a forecast of realised revenue).
P_MW, E_STEPS, RTE, DT_H = 1.0, 8, 0.85, 0.25

EVENTS = [
    ("2003 Northeast blackout (trees on Ohio 345 kV lines)", "2003-08-14", "2003-08-16"),
    ("2006 heat wave + Queens (LIC) network failure", "2006-07-17", "2006-08-03"),
    ("Superstorm Sandy", "2012-10-28", "2012-11-03"),
    ("Polar vortex (Jan 2014)", "2014-01-06", "2014-01-08"),
    ("Polar vortex, second wave (Jan 2014)", "2014-01-21", "2014-01-28"),
    ("2017-18 cold snap (bomb cyclone)", "2017-12-26", "2018-01-08"),
    ("Manhattan blackout (protection failure)", "2019-07-13", "2019-07-13"),
    ("Tropical Storm Isaias", "2020-08-04", "2020-08-06"),
    ("Winter Storm Elliott", "2022-12-23", "2022-12-26"),
    ("June 2025 heat wave", "2025-06-22", "2025-06-26"),
    ("Winter Storm Fern", "2026-01-24", "2026-01-28"),
]


def q15_day(ts, vals):
    """Time-weighted 15-minute averages of a step series whose stamps are interval ENDS (seconds)."""
    t = np.asarray(ts, float)
    v = np.asarray(vals, float)
    end = max(86400.0, float(np.ceil(t[-1] / BUCKET) * BUCKET))
    # segments (t[i-1], t[i]] carry v[i]; the first runs from 0; after the last stamp, hold the last value
    edges = np.concatenate([[0.0], t, [end]])
    seg_v = np.concatenate([v, [v[-1]]])
    widths = np.diff(edges).clip(min=0)
    F = np.concatenate([[0.0], np.cumsum(widths * seg_v)])  # integral at each edge
    b = np.arange(0, end + 1, BUCKET)
    Fb = np.interp(b, edges, F)
    return np.diff(Fb) / BUCKET


def parse_year(year):
    """{zone: {date: (lbmp_q15, cong_q15, raw_max)}} for one year, cached."""
    cache = os.path.join(CACHE, f"q15_{year}.npz")
    if os.path.exists(cache):
        z = np.load(cache, allow_pickle=True)
        return z["data"].item()
    out = defaultdict(dict)
    for zp in sorted(glob.glob(os.path.join(ZIPS, f"{year}??01realtime_zone_csv.zip"))):
        with zipfile.ZipFile(zp) as zf:
            for name in sorted(zf.namelist()):
                day = datetime.strptime(name[:8], "%Y%m%d").date()
                rows = defaultdict(list)  # zone -> [(sec, lbmp, cong)]
                with zf.open(name) as fh:
                    rd = csv.reader(io.TextIOWrapper(fh, encoding="utf-8", errors="replace"))
                    next(rd, None)
                    for r in rd:
                        if len(r) < 6 or r[1] not in LETTER:
                            continue
                        dt = datetime.strptime(r[0], "%m/%d/%Y %H:%M:%S")
                        sec = (dt.date() - day).days * 86400 + dt.hour * 3600 + dt.minute * 60 + dt.second
                        try:
                            rows[r[1]].append((sec, float(r[3]), -float(r[5])))
                        except ValueError:
                            continue
                for zone, recs in rows.items():
                    # fall-back DST: a repeated hour shows up as stamps going backwards -> shift by 1 h
                    fixed, off, last = [], 0, -1
                    for sec, p, c in recs:
                        if sec + off < last - 1800:
                            off += 3600
                        last = sec + off
                        fixed.append((last, p, c))
                    fixed.sort(key=lambda x: x[0])
                    ts = [x[0] for x in fixed]
                    out[zone][day.isoformat()] = (
                        q15_day(ts, [x[1] for x in fixed]).astype(np.float32),
                        q15_day(ts, [x[2] for x in fixed]).astype(np.float32),
                        float(max(x[1] for x in fixed)),
                    )
    out = dict(out)
    os.makedirs(CACHE, exist_ok=True)
    np.savez_compressed(cache, data=np.array(out, dtype=object))
    return out


def battery_daily(days):
    L = max(len(p) for p in days)
    P = np.full((len(days), L), np.nan)
    for i, p in enumerate(days):
        P[i, :len(p)] = p
    S = E_STEPS + 1
    V = np.full((len(days), S, S), -1e18)
    V[:, 0, 0] = 0.0
    for t in range(L):
        price = P[:, t][:, None, None]
        valid = ~np.isnan(price)
        new = V.copy()
        new[:, 1:, :] = np.maximum(new[:, 1:, :], V[:, :-1, :] - np.where(valid, price, np.inf) * P_MW * DT_H / RTE)
        new[:, :-1, 1:] = np.maximum(new[:, :-1, 1:], V[:, 1:, :-1] + np.where(valid, price, -np.inf) * P_MW * DT_H)
        V = new
    return V[:, 0, :].max(axis=1)


def stats(a, raw_max):
    s = np.sort(a)
    k = max(1, a.size // 100)
    return {
        "n": int(a.size),
        "mean": round(float(a.mean()), 2), "median": round(float(np.median(a)), 2),
        "p95": round(float(np.percentile(a, 95)), 2), "p99": round(float(np.percentile(a, 99)), 2),
        "max_q15": round(float(a.max()), 2), "max_raw": round(raw_max, 2), "min": round(float(a.min()), 2),
        "n_ge_100": int((a >= 100).sum()), "n_ge_500": int((a >= 500).sum()), "n_ge_1000": int((a >= 1000).sum()),
        "n_lt_0": int((a < 0).sum()), "pct_negative": round(100 * float((a < 0).mean()), 2),
        "top1pct_share_of_cost": round(100 * float(s[-k:].sum() / a.sum()), 1) if a.sum() > 0 else None,
    }


def main():
    os.makedirs(OUT, exist_ok=True)
    S, B, E, T, H = [], [], [], [], []
    for year in YEARS:
        data = parse_year(year)
        if not data:
            continue
        dates = sorted(set.intersection(*[set(data[z]) for z in ZONES if z in data]))
        print(year, len(dates), "days", flush=True)
        for z in ZONES:
            lb = [data[z][d][0] for d in dates]
            cg = np.concatenate([data[z][d][1] for d in dates])
            a = np.concatenate(lb)
            row = {"year": year, "zone": z, "letter": LETTER[z], "days": len(dates),
                   **stats(a, max(data[z][d][2] for d in dates)),
                   "mean_congestion": round(float(cg.mean()), 2),
                   "n_cong_ge_50": int((cg >= 50).sum()), "n_cong_le_m50": int((cg <= -50).sum())}
            S.append(row)
            daily = battery_daily(lb)
            srt = np.sort(daily)[::-1]
            B.append({"year": year, "zone": z, "letter": LETTER[z], "days": len(dates),
                      "usd_per_mw": round(float(daily.sum())),
                      "usd_per_kw_yr_annualised": round(float(daily.sum()) / len(dates) * 365 / 1000, 2),
                      "top10_days_share_pct": round(100 * float(srt[:10].sum() / daily.sum()), 1) if daily.sum() > 0 else None,
                      "best_day": dates[int(np.argmax(daily))], "best_day_usd": round(float(daily.max()))})
            # hour-of-day profile of spikes (15-min averages >= $250), by 3-hour block
            if z in ("N.Y.C.", "LONGIL", "CAPITL", "WEST"):
                cnt = np.zeros(8, int)
                for d in dates:
                    q = data[z][d][0]
                    for i in np.nonzero(q >= 250)[0]:
                        cnt[min(7, int(i * BUCKET // 10800))] += 1
                H.append({"year": year, "zone": z, **{f"h{3*i:02d}-{3*i+3:02d}": int(c) for i, c in enumerate(cnt)}})
        for name, a0, b0 in EVENTS:
            if int(a0[:4]) != year:
                continue
            for z in ZONES:
                win = [d for d in data[z] if a0 <= d <= b0]
                if not win:
                    E.append({"event": name, "start": a0, "end": b0, "zone": z, "days_with_data": 0})
                    continue
                q = np.concatenate([data[z][d][0] for d in win])
                c = np.concatenate([data[z][d][1] for d in win])
                E.append({"event": name, "start": a0, "end": b0, "zone": z, "days_with_data": len(win),
                          "max_q15": round(float(q.max()), 2), "max_raw": round(max(data[z][d][2] for d in win), 2),
                          "mean": round(float(q.mean()), 2), "mean_congestion": round(float(c.mean()), 2),
                          "n_ge_500": int((q >= 500).sum())})
        daymax = sorted(((max(float(data[z][d][0].max()) for z in ZONES), d) for d in dates), reverse=True)[:10]
        for mx, d in daymax:
            zm = {z: float(data[z][d][0].max()) for z in ZONES}
            hi = max(zm, key=zm.get); lo = min(zm, key=zm.get)
            T.append({"year": year, "date": d, "max_any_zone": round(mx, 2), "highest_zone": hi,
                      "highest_max": round(zm[hi], 2), "lowest_zone": lo, "lowest_max": round(zm[lo], 2)})

    def dump(name, rows):
        with open(os.path.join(OUT, name), "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=list(rows[0])); w.writeheader(); w.writerows(rows)

    dump("zone_year_stats.csv", S); dump("battery_value.csv", B); dump("event_windows.csv", E)
    dump("top_days.csv", T); dump("hour_profile.csv", H)
    with open(os.path.join(OUT, "summary.json"), "w", encoding="utf-8") as f:
        json.dump({"source": "NYISO Real-Time Zonal LBMP (mis.nyiso.com/public/csv/realtime), 2003-2026",
                   "generated": date.today().isoformat(), "resolution": "15-minute time-weighted averages",
                   "battery_model": {"mw": P_MW, "mwh": P_MW * E_STEPS * DT_H, "rte": RTE, "cycles_per_day": 1,
                                     "foresight": "perfect (upper bound)"},
                   "stats": S, "battery": B, "events": E, "top_days": T, "hour_profile": H}, f, indent=1)
    print("done ->", OUT)


if __name__ == "__main__":
    main()
