"""Phase 3b: nodal price lanes rebuilt from zone prices and a replayed congestion structure. Everything here is SIM / DERIVED.

Why a rebuild. ERCOT's public nodal history is one week of real-time prices and 31 days of day-ahead prices (gate A), so a
multi-year, 15-minute price lane at a substation cannot be read from data. It is built instead:

    p_n(t) = p_zone(n)(t) - sum_k (SF[n,k] - SF_zone[k]) * mu_tilde[k,t]                       (PLAN.md sign convention)

  p_zone(n)   the arena's 15-minute load-zone price (REAL), the zone the node belongs to (NOIE zone first, else load zone);
  SF[n,k]     the substation's shift factor to constraint k (phase 2, rt_reg first, then day-ahead fits that are not attenuated);
  SF_zone[k]  the same sensitivity of the zone price, regressed directly from zone prices (`zone_sf`), so that a node with the
              zone's own sensitivity gets exactly the zone price;
  mu_tilde    SYNTHETIC shadow prices: "the 2025 congestion structure replayed on each market year" (`constraint_structure`,
              `build_mu_tilde`). Each constraint gets a driver series (a hub or zone spread against HB_BUSAVG), a number of
              binding hours H_k and an annual shadow-price-hours target. In every year the 4*H_k fifteen-minute intervals with the
              highest driver bind, with mu proportional to the driver's excess over the threshold, scaled to the target.

Sign convention (PLAN.md): cong[b,t] = LMP - lambda = - sum_k SF[b,k] * mu[k,t]; SF < 0 is the import side.

Honest limits, repeated in notes/phase3.md: the structure is one week of RT and 31 days of DAM data annualised (and discounted);
constraints whose shift factors are only a physics proxy are never used and attenuated day-ahead fits (+shrunk) are excluded from the base rebuild (8 RT fits with sf_rt_shrink below SHRINK_MIN are used as fitted: 0.04% of the replayed shadow-price-hours); zone prices
carry the real zonal congestion of each year, the node-minus-zone part is what is synthetic.

Unverified constants are single named values at the top, each with a label (REAL / DERIVED / SIM / ASSUMPTION).
"""
from __future__ import annotations

import fnmatch
import hashlib
import json
import time
import warnings
from functools import lru_cache
from pathlib import Path

import numpy as np
import pandas as pd
import scipy.sparse as sps

from . import paths

paths.use_arena()
from arena import config as C  # noqa: E402

DERIVED = paths.DERIVED
REPO_DATA = paths.REPO_DATA

# =================================================================================================================
# Constants
# =================================================================================================================
CACHE_VERSION = 4                    # DERIVED: bump when the logic that builds the structure, the shadow prices or the lanes changes (part of every cache key)
REPLAY_YEAR = 2025                   # REAL: the year whose congestion structure is replayed (IMM 2025 rents and hours)
LANE_START = "2019-01-01"            # REAL: brief, first day of the rebuilt node history (the models need two years before the 2021 start)
LANE_END = "2026-09-26"              # REAL: last day of the arena's ERCOT price history (NP6-785-ER)
NI = C.NI                            # REAL: 96 fifteen-minute intervals a day (arena.config.NI)
DT_H = C.DT                          # REAL: 0.25 h per interval
ZONES = list(C.ERCOT_ZONES)          # REAL: the arena's 8 load zones (also the control lanes)
REF = C.ERCOT_REF                    # REAL: HB_BUSAVG, the system reference every spread is taken against
HUBS = ("HB_WEST", "HB_NORTH", "HB_SOUTH", "HB_HOUSTON", "HB_PAN")   # REAL: ERCOT hubs with a price history in rtm20xx.csv
DRIVER_LOCATIONS = HUBS + tuple(ZONES)   # DERIVED: 13 candidate locations; a driver is +/- (price - HB_BUSAVG) at one of them
HB_PAN_FALLBACK = "HB_WEST"          # ASSUMPTION: HB_PAN has no price before 2019-04-05 (the hub was created then); its spread borrows HB_WEST

PRICE_CAP = 5000.0                   # REAL: ERCOT system-wide offer cap since 2022 (arena.config.ERCOT_CAP); a rebuilt node price is not pushed above it
PRICE_FLOOR = -250.0                 # REAL: the brief's floor (the protocols' SPP floor is -$251, NP 6.6.1.1); a rebuilt node price is not pushed below it
SHADOW_CAP_DEFAULT = {"gtc": 5251.0, 345: 4500.0, 138: 3500.0, 69: 2800.0}   # REAL: shadow-price caps $/MWh, IMM 2025 SOM p.62 (notes/rules.md (b)); used where SCED lists no cap
MEAN_SHADOW_CAP_FRAC = 0.5           # ASSUMPTION: the mean shadow price while binding may not exceed this share of the cap (else H_k is raised)

SCED_RUN_MIN = 5.0                   # ASSUMPTION: one SCED run covers 5 minutes (phase 1a / phase 2 convention)
SCED_RUN_CAP_S = 360.0               # ASSUMPTION: a SCED run is never stretched over more than 6 minutes (gaps in the posted runs)
DAM_WINDOW_DAYS = 31.0               # REAL: DAM shadow prices 10 Sep - 10 Oct 2026
MIN_RT_RUNS_FOR_SPH = 10             # REAL: phase 2 MIN_BINDING_RUNS; below this the RT window is too thin, the DAM window is used
WINDOW_PERSISTENCE = 0.15            # ASSUMPTION: share of the window's shadow-price-hours that recurs over a year (outage-driven shoulder week).
                                     #   Same value and meaning as gridsite.upgrades.WINDOW_PERSISTENCE (agent 4); measured 0.14 for Scurry Chevron-Knapp, the one
                                     #   usable element with both a window and an IMM rent. Applied to the target; split between hours and magnitude, see below.
HOURS_SHARE_OF_PERSISTENCE = 0.5     # ASSUMPTION: the persistence discount is split evenly (geometrically) between binding hours and the mean shadow price:
                                     #   H_window * persistence**0.5, mean * persistence**0.5. Scurry Chevron-Knapp: IMM hours 1099 vs 2530 annualised from the RT window
                                     #   (0.43), IMM mean shadow $268 vs $838 in the window (0.32), product 0.14, close to the 0.39 x 0.39 this split gives.
IMM_FLOOR_RANK_RENT_MUSD = 50.83     # REAL: 10th-largest 2025 IMM RT rent (Argyle-Lewisville); an element outside the IMM top 10 earns less than this,
                                     #   so it caps the annualised window rents of the elements without an IMM figure (same as gridsite.upgrades)
H_MIN = 24.0                         # ASSUMPTION: a constraint's annual binding hours are at least this (a smaller window estimate is below its noise; the dollar target is kept, so the mean shadow price falls)
H_MAX = 4000.0                       # ASSUMPTION: no constraint binds more than this many hours a year (the IMM's most frequent element binds 1,694 h)

MIN_RT_INTERVALS_DRIVER = 24         # ASSUMPTION: a driver is fitted on the RT window only if the constraint binds in at least this many 15-min intervals
MIN_DAM_HOURS_DRIVER = 6             # ASSUMPTION: ... else on the DAM window if it binds in at least this many hours
MIN_DRIVER_CORR = 0.20               # ASSUMPTION: a fitted driver needs at least this signed correlation with the observed shadow price, else the geographic rule.
                                     #   Raised from 0.10 after an independent review: a placebo (whole-day shifts of the DAM series, best of 26 candidates) clears 0.10 in 85% of draws, median 0.19
GEO_MIN_ZONE_SF = 0.003              # ASSUMPTION: the geographic rule reads a zone's SF only if |SF_zone| is at least this (below it the sign is noise); else the element's own zone, sign +1
GEO_DEFAULT_SIGN = 1                 # ASSUMPTION: a local pocket with no zone-level signature is taken to be an import pocket (price up when it binds)
GEO_HUB = {"LZ_WEST": "HB_WEST", "LZ_NORTH": "HB_NORTH", "LZ_SOUTH": "HB_SOUTH", "LZ_HOUSTON": "HB_HOUSTON"}   # ASSUMPTION: named geographic mapping of a zone to its hub
PANHANDLE_BOX = (34.3, 36.6, -103.1, -99.9)   # ASSUMPTION: (lat_min, lat_max, lon_min, lon_max) of the ERCOT Panhandle; an element with an end inside maps to HB_PAN

DRIVER_RULE = "physics"              # ASSUMPTION: both rules pool the contingency cids of an element (one driver, nested binding sets, so an element binds as long as its REAL hours).
                                     #   "physics" (default) = best signed correlation among the locations whose zone shift factor says the element raises (SF_zone < 0) or lowers
                                     #   (> 0) the price there, at least DRIVER_ZONE_REL_MIN of the largest |SF_zone|; "data" = best signed correlation over all 13 locations (brief, literal)
DRIVER_ZONE_REL_MIN = 0.5            # ASSUMPTION: physics rule: a location is admissible if its zone's |SF_zone| is at least this share of the largest over the 8 zones
LOC_ZONE = {"HB_WEST": "LZ_WEST", "HB_NORTH": "LZ_NORTH", "HB_SOUTH": "LZ_SOUTH", "HB_HOUSTON": "LZ_HOUSTON", "HB_PAN": "LZ_WEST",
            "LZ_WEST": "LZ_WEST", "LZ_NORTH": "LZ_NORTH", "LZ_SOUTH": "LZ_SOUTH", "LZ_HOUSTON": "LZ_HOUSTON", "LZ_AEN": "LZ_AEN", "LZ_CPS": "LZ_CPS",
            "LZ_LCRA": "LZ_LCRA", "LZ_RAYBN": "LZ_RAYBN"}   # ASSUMPTION: the zone whose shift factor stands for a hub (the Panhandle sits in the West settlement zone)
ZONE_SF_MIN_R2 = 0.5                 # ASSUMPTION: a direct zone regression is used if its in-sample R2 is at least this, else the bus mean within the zone
BUS_MEAN_MIN_KV = 60.0               # ASSUMPTION: buses at or above this voltage enter the within-zone bus mean (same as gridsite.upgrades.zone_mean_sf)
SHRINK_MIN = 0.8                     # REAL: phase 2 SHRINK_MIN; DAM fits below this are attenuated ("+shrunk") and give sign only
INCLUDE_SHRUNK_DEFAULT = False       # ASSUMPTION: attenuated DAM shift factors are not used in the base rebuild (brief: sign only)
SF_BAD_QUALITY = ("implausible",)    # ASSUMPTION: a substation SF with one of these tags is read as zero. "weak" (|SF| < 2 se) is NOT zeroed: SF_zone is not zeroed either, so zeroing
                                     #   the node leaves DSF = -SF_zone (an artefact worth 15% of the candidates' scores in the first build); raw SFs also rebuild the observed basis better (lane_validation.csv)
SF_ABS_CAP = 1.0                     # ASSUMPTION: |SF| is limited to 1 (a node price cannot move by more than the shadow price; same as gridsite.upgrades.SF_NWA_CAP).
                                     #   Post-contingency sensitivities can exceed 1 in principle, but the day-ahead fits that give 1.2 here are collinear (VIF > 100)

COMPLETE_INTERVAL_S = 890.0          # ASSUMPTION: a 15-minute interval is "complete" if the SCED runs cover at least this many of its 900 seconds
K_HOURS_PER_MUSD_FALLBACK = 8.5      # DERIVED: median IMM RT binding hours per $M of 2025 rent (gridsite.upgrades.imm_hours_per_musd = 8.48); used only if the table has no such element
DAYS_PER_YEAR = 365.0                # REAL: annualisation of window figures (same as gridsite.upgrades)
INTERVALS_PER_HOUR = 1.0 / DT_H      # DERIVED: 4 fifteen-minute intervals per hour
MIN_BIND_INTERVALS = 4               # ASSUMPTION: a constraint-year with fewer binding intervals than this is not replayed
FIT_ITER_MAX = 12                    # ASSUMPTION: water-filling iterations when clipping synthetic shadow prices at their cap and rescaling to the target
VALID_FIT_START_CPT = "2026-10-05"   # REAL: the RT shift-factor fit starts with the first day of bus LMPs (phase 1a)
VALID_FIT_END_CPT = "2026-10-09 12:00"   # REAL: end of the in-fit slice of the validation (phase 2 held out the last 24 h)
HOLDOUT_RT_H = 24                    # REAL: phase 2 RT holdout, hours
HOLDOUT_DAM_DAYS = 5                 # REAL: phase 2 DAM holdout, days
CPT = "America/Chicago"              # REAL: Central Prevailing Time
READ_ONLY = False                    # DERIVED: R2. True in a process that must not write (report-check): a stale cache is recomputed in memory and nothing is saved, not even data/constraint_synth.csv

# derived-file names (all under research-data/grid/derived/, owned by 3b)
HUB_FILE = DERIVED / "nodal_lanes_hubs.npz"
ZONE_SF_FILE = DERIVED / "nodal_lanes_zone_sf.parquet"
MU_FILE = DERIVED / "nodal_lanes_mu_tilde.npz"
STRUCT_FILE = REPO_DATA / "constraint_synth.csv"
SF_LONG = DERIVED / "sf_long.parquet"
SF_SUB = DERIVED / "_p2_cache" / "sf_sub.parquet"


def _read_csv(name: str) -> pd.DataFrame:
    """Substation tables must be read with keep_default_na=False: the ERCOT substation 'NA' is a real code."""
    return pd.read_csv(REPO_DATA / name, keep_default_na=False, na_values=[""], low_memory=False)


def _us(series: pd.Series) -> np.ndarray:
    """tz-aware pandas timestamps to int64 microseconds since epoch (UTC)."""
    return series.dt.tz_convert("UTC").dt.tz_localize(None).astype("datetime64[us]").astype("int64").to_numpy()


def _stamp(*files) -> list:
    """Name, size and modification time of input files: part of a cache key, so a cache built from other inputs is rebuilt."""
    out = []
    for f in files:
        f = Path(f)
        out.append([f.name, f.stat().st_size, int(f.stat().st_mtime)] if f.exists() else [f.name, None, None])
    return out


def _norm(o):
    """JSON-safe, order-independent form of constants and parts of a cache key (dict keys of mixed types become strings)."""
    if isinstance(o, dict):
        return {str(k): _norm(v) for k, v in o.items()}
    if isinstance(o, (list, tuple, set)):
        return [_norm(v) for v in (sorted(o, key=str) if isinstance(o, set) else o)]
    if isinstance(o, (np.floating, np.integer)):
        return o.item()
    return o


def _sig(*parts) -> str:
    return hashlib.md5(json.dumps(_norm(parts), sort_keys=True, default=str).encode()).hexdigest()


def _read_if_signed(path: Path, sigfile: Path, sig: str, reader):
    """Return reader(path) if `path` exists and its signature file holds `sig`, else None."""
    if path.exists() and sigfile.exists() and sigfile.read_text() == sig:
        return reader(path)
    return None


# =================================================================================================================
# Price history: arena zone lanes, HB_BUSAVG, hubs
# =================================================================================================================
def _hub_history_build() -> dict:
    """Hub price lanes on the arena clock (96 intervals, DST rule of arena.data._to96_ercot) for every arena date, from rtm20xx.csv."""
    from arena.data import _to96_ercot
    from arena.data import ercot_prices

    dates, _, _ = ercot_prices()
    years = sorted({int(d[:4]) for d in dates})
    rows = {h: {} for h in HUBS}
    for y in years:
        f = C.DATA / "ercot" / f"rtm{y}.csv"
        d = pd.read_csv(f, usecols=["date", "hour", "interval", "rep", "sp", "price"], dtype={"rep": str, "sp": str})
        d = d[d["sp"].isin(HUBS)]
        for (sp, date), g in d.groupby(["sp", "date"], sort=False):
            rows[sp][date] = _to96_ercot(list(zip(g["hour"].astype(int), g["interval"].astype(int), g["rep"], g["price"].astype(float))))
    out = {"dates": np.asarray(dates).astype(str)}
    for h in HUBS:
        arr = np.full((len(dates), NI), np.nan, np.float32)
        for k, dt in enumerate(dates):
            v = rows[h].get(str(dt))
            if v is not None:
                arr[k] = v
        out[h] = arr
    return out


@lru_cache(maxsize=1)
def hub_history() -> dict:
    """{'dates': (D,), 'HB_WEST': (D,96) float32, ...} for every arena date 2011-01-01 .. 2026-09-26. HB_PAN is NaN before 2019-04-05."""
    sig = _sig(CACHE_VERSION, _stamp(*[C.DATA / "ercot" / f"rtm{y}.csv" for y in range(2011, 2027)], C.CACHE / "prices_ERCOT.npz"))
    sigf = HUB_FILE.with_suffix(".sig")

    def read(f):
        z = np.load(f, allow_pickle=False)
        return {k: z[k] for k in z.files}

    got = _read_if_signed(HUB_FILE, sigf, sig, read)
    if got is not None:
        return got
    out = _hub_history_build()
    if not READ_ONLY:
        np.savez_compressed(HUB_FILE, **out)
        sigf.write_text(sig)
    return out


@lru_cache(maxsize=1)
def price_history() -> dict:
    """The lane clock and every price series the rebuild needs, restricted to LANE_START..LANE_END.

    Returns {'dates': (D,), 'zones': list, 'P_zone': (D,8,96) float64 (bit-identical to the arena's Market), 'ref': (D,96) HB_BUSAVG,
             'hubs': {name: (D,96)}, 'spread': {location: (D,96) price - HB_BUSAVG}}."""
    from arena.data import ercot_prices

    all_dates, zones, P = ercot_prices()
    all_dates = np.asarray(all_dates).astype(str)
    sel = np.where((all_dates >= LANE_START) & (all_dates <= LANE_END))[0]
    zi = {z: k for k, z in enumerate(zones)}
    Pz = P[sel][:, [zi[z] for z in ZONES], :].astype(np.float64)
    ref = P[sel][:, zi[REF], :].astype(np.float64)
    hh = hub_history()
    if not np.array_equal(hh["dates"], all_dates):
        raise ValueError("hub history and arena price history have different dates; delete derived/nodal_lanes_hubs.npz")
    hubs = {h: hh[h][sel].astype(np.float64) for h in HUBS}
    spread = {}
    for h in HUBS:
        spread[h] = hubs[h] - ref
    miss = ~np.isfinite(spread["HB_PAN"])
    spread["HB_PAN"] = np.where(miss, spread[HB_PAN_FALLBACK], spread["HB_PAN"])
    for k, z in enumerate(ZONES):
        spread[z] = Pz[:, k, :] - ref
    return {"dates": all_dates[sel], "zones": list(ZONES), "P_zone": Pz, "ref": ref, "hubs": hubs, "spread": spread}


# =================================================================================================================
# Window data (real-time 3-10 Oct 2026, day-ahead 10 Sep - 10 Oct 2026)
# =================================================================================================================
def _runs_to_15(ts_us: np.ndarray, X: np.ndarray):
    """Overlap-weighted 15-minute means of per-SCED-run values (the convention that reproduces the SPP identity, notes/phase1-market.md 10).

    A run applies from its timestamp to the next run's timestamp (at most SCED_RUN_CAP_S). Returns (starts_us (J,), M (J,K), covered_s (J,))."""
    q = 900 * 10**6
    ts_us = np.asarray(ts_us, np.int64)
    nxt = np.append(ts_us[1:], ts_us[-1] + int(SCED_RUN_MIN * 60 * 10**6))
    end = np.minimum(nxt, ts_us + int(SCED_RUN_CAP_S * 10**6))
    base = (ts_us[0] // q) * q
    j0 = (ts_us - base) // q
    j1 = (end - 1 - base) // q
    J = int(j1.max()) + 1
    w0 = (np.minimum(end, base + (j0 + 1) * q) - ts_us).astype(np.float64)
    w1 = np.where(j1 > j0, end - np.maximum(ts_us, base + j1 * q), 0).astype(np.float64)
    num = np.zeros((J, X.shape[1]))
    den = np.zeros(J)
    np.add.at(num, j0, w0[:, None] * X)
    np.add.at(den, j0, w0)
    m = j1 > j0
    np.add.at(num, j1[m], w1[m, None] * X[m])
    np.add.at(den, j1[m], w1[m])
    M = num / np.maximum(den, 1.0)[:, None]
    return base + np.arange(J, dtype=np.int64) * q, M, den / 1e6


@lru_cache(maxsize=1)
def rt_window_days() -> float:
    """Days covered by the RT shadow-price window (every SCED run in shadow_sced x 5 min; includes the archived 25 Sep day, as constraints.csv does)."""
    t = pd.read_parquet(DERIVED / "shadow_sced.parquet", columns=["sced_ts"])
    return float(t["sced_ts"].nunique() * SCED_RUN_MIN / 60.0 / 24.0)


def rt_shadow_15(cids) -> dict:
    """15-minute mean real shadow price per cid over the MIS real-time window (3-10 Oct 2026), zeros where listed-but-not-binding.

    A run with no shadow rows at all is dropped (never read as zero), as in phase 2. Returns {'start_us', 'cids', 'M' (J,K), 'covered_s'}."""
    cids = list(cids)
    sh = pd.read_parquet(DERIVED / "shadow_sced.parquet", columns=["sced_ts", "cid", "shadow_price", "source"])
    sh = sh[sh["source"] == "mis"]
    sh["t"] = _us(sh["sced_ts"])
    runs = np.sort(sh["t"].unique())
    k = sh[sh["cid"].isin(cids)].groupby(["t", "cid"], as_index=False)["shadow_price"].max()
    piv = k.pivot(index="t", columns="cid", values="shadow_price").reindex(index=runs, columns=cids).fillna(0.0)
    starts, M, cov = _runs_to_15(runs, piv.to_numpy(float))
    return {"start_us": starts, "cids": cids, "M": M, "covered_s": cov}


def dam_shadow_hourly(cids) -> dict:
    """Hourly day-ahead shadow price per cid over the DAM window (zeros where the hour has rows but not this cid)."""
    cids = list(cids)
    sh = pd.read_parquet(DERIVED / "shadow_dam.parquet", columns=["interval_start_utc", "cid", "shadow_price", "dst_rep"])
    sh = sh[~sh["dst_rep"]]
    sh["t"] = _us(sh["interval_start_utc"])
    hours = np.sort(sh["t"].unique())
    k = sh[sh["cid"].isin(cids)].groupby(["t", "cid"], as_index=False)["shadow_price"].max()
    piv = k.pivot(index="t", columns="cid", values="shadow_price").reindex(index=hours, columns=cids).fillna(0.0)
    return {"start_us": hours, "cids": cids, "M": piv.to_numpy(float)}


def window_spreads(kind: str, locations=DRIVER_LOCATIONS) -> dict:
    """Window price spreads against HB_BUSAVG: kind 'rt' = 15-minute SPP (spp_node, 3-10 Oct), 'dam' = hourly DAM SPP (spp_dam).
    Returns {'start_us': (J,), 'S': (J, L)} for the locations that have a price."""
    f = DERIVED / ("spp_node.parquet" if kind == "rt" else "spp_dam.parquet")
    d = pd.read_parquet(f)
    d = d[d["settlement_point"].isin(list(locations) + [REF]) & ~d["dst_rep"]]
    d["t"] = _us(d["interval_start_utc"])
    piv = d.groupby(["t", "settlement_point"])["price"].mean().unstack()
    S = piv[list(locations)].sub(piv[REF], axis=0)
    return {"start_us": piv.index.to_numpy(np.int64), "S": S.to_numpy(float), "locations": list(locations)}


# =================================================================================================================
# Zone shift factors: SF_zone[k], regressed directly from zone prices (the same operator as the bus fit in phase 2)
# =================================================================================================================
def _zone_cong(tag: str, ts: np.ndarray) -> np.ndarray:
    """Zone congestion (zone price - system lambda) on the runs / hours of the phase 2 market `tag`. RT: 5-minute zone LMP (NP6-788) - lambda
    (the zone SPP differs from the time-weighted LMP by the RTRDPA adder and by up to 35 cents); DAM: zone SPP - DAM lambda."""
    if tag == "rt":
        ln = pd.read_parquet(DERIVED / "lmp_node.parquet")
        ln = ln[ln["settlement_point"].isin(ZONES)]
        ln["t"] = _us(ln["sced_ts"])
        z = ln.pivot_table(index="t", columns="settlement_point", values="lmp", aggfunc="mean").reindex(ts)
        lam = pd.read_parquet(DERIVED / "lambda.parquet")
        lam["t"] = _us(lam["sced_ts"])
        lamv = lam.set_index("t")["lambda"].reindex(ts).to_numpy()
    else:
        sp = pd.read_parquet(DERIVED / "spp_dam.parquet")
        sp = sp[sp["settlement_point"].isin(ZONES)]
        sp["t"] = _us(sp["interval_start_utc"])
        z = sp.pivot_table(index="t", columns="settlement_point", values="price", aggfunc="mean").reindex(ts)
        lam = pd.read_parquet(DERIVED / "lambda_dam.parquet")
        lam["t"] = _us(lam["interval_start_utc"])
        lamv = lam.set_index("t")["lambda"].reindex(ts).to_numpy()
    return z[ZONES].to_numpy(float) - lamv[:, None]


def _bus_mean_sf() -> pd.DataFrame:
    """Unweighted mean SF over the buses of >= BUS_MEAN_MIN_KV in each arena zone (absent = 0), per cid and method: the fallback and a check."""
    sp = _read_csv("settlement_points.csv")
    sp = sp.drop_duplicates("ELECTRICAL_BUS").set_index("ELECTRICAL_BUS")
    zone = sp["NOIE_ZONE"].where(sp["NOIE_ZONE"].notna(), sp["SETTLEMENT_LOAD_ZONE"])
    kv = pd.to_numeric(sp["VOLTAGE_LEVEL"], errors="coerce")
    n_by_zone = zone[kv >= BUS_MEAN_MIN_KV].value_counts()
    sf = pd.read_parquet(SF_LONG, columns=["bus", "cid", "sf", "method"])
    for col in ("bus", "cid", "method"):
        sf[col] = sf[col].astype(str)
    sf = sf[sf["method"].isin(["rt_reg", "dam_reg"])]
    sf["zone"] = sf["bus"].map(zone)
    sf["kv"] = sf["bus"].map(kv)
    sf = sf[sf["zone"].isin(ZONES) & (sf["kv"] >= BUS_MEAN_MIN_KV)]
    g = sf.groupby(["cid", "method", "zone"])["sf"].sum().reset_index()
    g["bus_mean"] = g["sf"] / g["zone"].map(n_by_zone)
    return g[["cid", "method", "zone", "bus_mean"]]


def zone_sf(refresh: bool = False) -> pd.DataFrame:
    """SF_zone[k] for the 8 load zones and every cid fitted in phase 2: columns cid, method (rt_reg / dam_reg), zone, sf, bus_mean, r2_zone, source.

    source = 'direct' (zone price regressed on the shadow prices with the phase 2 design, ridge penalty and focal relaxation) or 'bus_mean'
    (when the zone regression R2 is below ZONE_SF_MIN_R2). Cached in derived/nodal_lanes_zone_sf.parquet."""
    sig = _sig(CACHE_VERSION, ZONE_SF_MIN_R2, BUS_MEAN_MIN_KV,
               _stamp(DERIVED / "lmp_node.parquet", DERIVED / "lambda.parquet", DERIVED / "spp_dam.parquet", DERIVED / "lambda_dam.parquet", DERIVED / "p2_summary.json",
                      DERIVED / "_p2_cache" / "rt_cong.npy", DERIVED / "_p2_cache" / "dam_cong.npy", DERIVED / "_p2_cache" / "rt_shadow.parquet",
                      DERIVED / "_p2_cache" / "dam_shadow.parquet", SF_LONG, REPO_DATA / "constraints.csv"))
    sigf = ZONE_SF_FILE.with_suffix(".sig")
    if not refresh:
        got = _read_if_signed(ZONE_SF_FILE, sigf, sig, pd.read_parquet)
        if got is not None:
            return got
    from . import shiftfactors as S

    summary = json.loads((DERIVED / "p2_summary.json").read_text())
    cons0 = pd.read_csv(S.CONSTRAINTS_CSV, keep_default_na=False)
    focal = set(cons0.sort_values("priority_rank").head(S.PRIORITY_TOP_SF)["cid"])
    parts = []
    for tag, mb, method in (("rt", S.MIN_BINDING_RUNS, "rt_reg"), ("dam", S.MIN_BINDING_HOURS_DAM, "dam_reg")):
        mk = S.load_market(tag)
        X = S.design(mk, mb)
        cids = list(X.columns)
        lam_cv = float(summary[tag]["ridge_lambda"])
        pen = np.where(np.array([c in focal for c in cids]), S.RELAX_ALPHA, lam_cv)
        Y = _zone_cong(tag, mk.ts)
        ok = np.isfinite(Y).all(1)
        rows = np.where(ok, True, False)
        beta = S.ridge_solve(X.to_numpy(float), Y, rows, pen)
        pred = X.to_numpy(float)[ok] @ beta
        res = Y[ok] - pred
        r2 = 1.0 - (res ** 2).sum(0) / np.maximum(((Y[ok] - Y[ok].mean(0)) ** 2).sum(0), 1e-12)
        sfz = -beta
        for j, z in enumerate(ZONES):
            parts.append(pd.DataFrame({"cid": cids, "method": method, "zone": z, "sf": sfz[:, j], "r2_zone": float(r2[j])}))
        print(f"  zone_sf[{tag}]: {len(cids)} cids x {len(ZONES)} zones, zone R2 min {r2.min():.3f} median {np.median(r2):.3f}", flush=True)
    d = pd.concat(parts, ignore_index=True)
    bm = _bus_mean_sf()
    d = d.merge(bm, on=["cid", "method", "zone"], how="left")
    d["bus_mean"] = d["bus_mean"].fillna(0.0)
    use_direct = d["r2_zone"] >= ZONE_SF_MIN_R2
    d["source"] = np.where(use_direct, "direct", "bus_mean")
    d["sf"] = np.where(use_direct, d["sf"], d["bus_mean"])
    if not READ_ONLY:
        d.to_parquet(ZONE_SF_FILE, index=False)
        sigf.write_text(sig)
    return d


# =================================================================================================================
# Constraint structure: which constraints, binding hours, shadow-price-hours target, driver
# =================================================================================================================
def load_constraints() -> pd.DataFrame:
    return _read_csv("constraints.csv")


def usable_table(include_shrunk: bool = INCLUDE_SHRUNK_DEFAULT) -> pd.DataFrame:
    """Constraints whose shift-factor MAGNITUDES may be used: rt_reg first, then DAM fits that are not attenuated (shrink >= SHRINK_MIN).
    Attenuated DAM fits give sign only ('dam_shrunk'); proxy and absent shift factors are never used. Adds columns sf_method / sf_use."""
    c = load_constraints()
    rt = c["sf_rt"].eq("fit")
    dam = c["sf_dam"].eq("fit")
    shr = c["sf_dam_shrink"].astype(float)
    ok_dam = dam & (shr >= SHRINK_MIN)
    c["sf_use"] = np.where(rt, "rt", np.where(ok_dam, "dam", np.where(dam, "dam_shrunk", "none")))
    c["sf_method"] = np.where(c["sf_use"] == "rt", "rt_reg", np.where(c["sf_use"].isin(["dam", "dam_shrunk"]), "dam_reg", ""))
    keep = c["sf_use"].isin(["rt", "dam"] + (["dam_shrunk"] if include_shrunk else []))
    return c[keep].reset_index(drop=True)


def _sced_caps() -> pd.Series:
    sh = pd.read_parquet(DERIVED / "shadow_sced.parquet", columns=["cid", "max_shadow_price"])
    return sh.groupby("cid")["max_shadow_price"].max()


def _shadow_cap(row, sced_cap: pd.Series) -> float:
    cap = sced_cap.get(row["cid"], np.nan)
    if np.isfinite(cap) and cap > 0:
        return float(cap)
    if row["element_type"] == "gtc":
        return SHADOW_CAP_DEFAULT["gtc"]
    kv = float(row["kv"]) if pd.notna(row["kv"]) else 138.0
    return SHADOW_CAP_DEFAULT[345] if kv >= 345 else SHADOW_CAP_DEFAULT[138] if kv >= 138 else SHADOW_CAP_DEFAULT[69]


def shadow_price_hours(c: pd.DataFrame) -> pd.DataFrame:
    """Annual shadow-price-hours target per cid, $/MW-yr (the sum over a year of the shadow price x hours), and the method.

    Same logic and constants as gridsite.upgrades.sph_table (agent 4) so the two phases agree:
      imm_rent_alloc : IMM 2025 rent allocated to the cid / the cid's median limit (REAL rent, DERIVED);
      window_rt / window_dam : window rent ($/MW) x 365 / window days x WINDOW_PERSISTENCE, RT if the cid has >= MIN_RT_RUNS_FOR_SPH binding
                               runs, else DAM; the sum over an element's window cids is capped at IMM_FLOOR_RANK_RENT_MUSD / limit."""
    lim = c["median_limit_mw"].astype(float)
    alloc = c["annual_rent_alloc_musd"].fillna(0).astype(float)
    sph = pd.Series(np.nan, index=c.index)
    basis = pd.Series("none", index=c.index, dtype=object)
    has_imm = (alloc > 0) & (lim > 0)
    sph[has_imm] = alloc[has_imm] * 1e6 / lim[has_imm]
    basis[has_imm] = "imm_rent_alloc"
    win_rt = c["rt_rent_proxy"].astype(float) * DAYS_PER_YEAR / rt_window_days() * WINDOW_PERSISTENCE
    win_dam = c["dam_rent_proxy"].astype(float) * DAYS_PER_YEAR / DAM_WINDOW_DAYS * WINDOW_PERSISTENCE
    use_rt = c["rt_n_runs_binding"].astype(float) >= MIN_RT_RUNS_FOR_SPH
    use_dam = ~use_rt & (c["dam_hours_binding"].astype(float) > 0)
    raw = pd.Series(np.nan, index=c.index)
    raw[use_rt] = win_rt[use_rt]
    raw[use_dam] = win_dam[use_dam]
    todo = ~has_imm & raw.notna() & (lim > 0)
    cap = IMM_FLOOR_RANK_RENT_MUSD * 1e6 / c["constraint_name"].map(c.groupby("constraint_name")["median_limit_mw"].median())
    tot = raw.where(todo).groupby(c["constraint_name"]).transform("sum")
    scale = np.minimum(1.0, cap / tot).where(todo)
    sph[todo] = (raw * scale)[todo]
    basis[todo & use_rt] = "window_rt"
    basis[todo & use_dam] = "window_dam"
    basis[todo & (scale < 1)] = basis[todo & (scale < 1)] + "_capped"
    return pd.DataFrame({"sph_usd_mw_yr": sph, "sph_basis": basis})


def annual_binding_hours(c: pd.DataFrame, sph: pd.DataFrame) -> pd.DataFrame:
    """Binding hours a year H_k per cid, the basis, and the mean shadow price while binding (sph / H).

    1. IMM 2025 binding hours of the element (REAL) when the cid carries an IMM rent;
    2. IMM rent x the median hours per $M of rent over the elements that have both (DERIVED);
    3. window hours x DAYS_PER_YEAR / window days x WINDOW_PERSISTENCE**HOURS_SHARE_OF_PERSISTENCE (RT window if >= MIN_RT_RUNS_FOR_SPH binding runs, else DAM).
    H is then limited to [H_MIN, H_MAX] and raised if the mean shadow price while binding would exceed MEAN_SHADOW_CAP_FRAC of the cap."""
    allc = load_constraints()
    both = allc[(allc["annual_rent_alloc_musd"].fillna(0) > 0) & allc["annual_binding_hours"].notna() & (allc["annual_rent_market"] == "RT")].drop_duplicates("constraint_name")
    k_h_per_musd = float((both["annual_binding_hours"] / both["annual_rent_2025_musd"]).median()) if len(both) else K_HOURS_PER_MUSD_FALLBACK
    rt_d = rt_window_days()
    h_share = WINDOW_PERSISTENCE ** HOURS_SHARE_OF_PERSISTENCE
    alloc = c["annual_rent_alloc_musd"].fillna(0).astype(float)
    imm_h = c["annual_binding_hours"].astype(float)
    use_rt = c["rt_n_runs_binding"].astype(float) >= MIN_RT_RUNS_FOR_SPH
    h_rt = c["rt_hours_binding"].astype(float) * DAYS_PER_YEAR / rt_d * h_share
    h_dam = c["dam_hours_binding"].astype(float) * DAYS_PER_YEAR / DAM_WINDOW_DAYS * h_share
    H = pd.Series(np.nan, index=c.index)
    basis = pd.Series("none", index=c.index, dtype=object)
    m3 = use_rt & (h_rt > 0)
    H[m3] = h_rt[m3]
    basis[m3] = "window_rt_hours"
    m3b = ~use_rt & (h_dam > 0)
    H[m3b] = h_dam[m3b]
    basis[m3b] = "window_dam_hours"
    m2 = (alloc > 0) & imm_h.isna()
    H[m2] = alloc[m2] * k_h_per_musd
    basis[m2] = "imm_rent_x_hours_per_musd"
    m1 = (alloc > 0) & imm_h.notna()
    H[m1] = imm_h[m1]
    basis[m1] = "imm_hours"
    H = H.clip(lower=H_MIN, upper=H_MAX)
    # an element with an IMM binding-hours figure binds no longer than that in total: a sibling contingency cannot exceed it (all cids of an element share one driver)
    imm_el = H.where(basis == "imm_hours").groupby(c["constraint_name"]).transform("max")
    H = pd.Series(np.where(imm_el.notna(), np.minimum(H, imm_el), H), index=H.index)
    return pd.DataFrame({"H_hours": H, "H_basis": basis}), k_h_per_musd


@lru_cache(maxsize=1)
def _substation_table() -> pd.DataFrame:
    return _read_csv("substations.csv").set_index("sub")


def _panhandle(sub_names) -> bool:
    s_ = _substation_table()
    la0, la1, lo0, lo1 = PANHANDLE_BOX
    for n in sub_names:
        if isinstance(n, str) and n in s_.index and pd.notna(s_.at[n, "lat"]) and la0 <= s_.at[n, "lat"] <= la1 and lo0 <= s_.at[n, "lon"] <= lo1:
            return True
    return False


def geographic_driver(zvec: np.ndarray, sub_names):
    """Named geographic mapping for an element whose window shows no usable driver: the zone with the largest |SF_zone| decides.

    If binding raises that zone's price (SF_zone < 0, an import element) the driver is +(price - HB_BUSAVG) there, else -(...) (an export element). The zone maps to
    its hub (GEO_HUB); an element with an end in the Panhandle box maps to HB_PAN. If every |SF_zone| is below GEO_MIN_ZONE_SF the element has no zone-level signature:
    its own zone (that of its end substations) with sign GEO_DEFAULT_SIGN."""
    zmax = float(np.abs(zvec).max()) if len(zvec) else 0.0
    if zmax >= GEO_MIN_ZONE_SF:
        z = ZONES[int(np.abs(zvec).argmax())]
        sign = 1 if zvec[ZONES.index(z)] < 0 else -1
    else:
        zmap = zone_of_substations()
        z = next((zmap[x] for x in sub_names if isinstance(x, str) and x in zmap), "LZ_NORTH")
        sign = GEO_DEFAULT_SIGN
    loc = GEO_HUB.get(z, z)
    if _panhandle(sub_names):
        loc = "HB_PAN"
    return loc, sign


def _admissible(zvec: np.ndarray, locs) -> list:
    """Locations (index, sign) whose zone's shift factor says the element moves the price there: sign +1 if binding raises it (SF_zone < 0), -1 if it lowers it."""
    zmax = float(np.abs(zvec).max())
    out = []
    for j, loc_ in enumerate(locs):
        sfl = zvec[ZONES.index(LOC_ZONE[loc_])]
        if abs(sfl) >= max(DRIVER_ZONE_REL_MIN * zmax, GEO_MIN_ZONE_SF):
            out.append((j, 1 if sfl < 0 else -1))
    return out


def _best_driver(mu: np.ndarray, S: np.ndarray, cand: list):
    """Best signed correlation between a window shadow-price series (J,) and the candidate signed spread series S[:, j] * sign. Returns (j, sign, corr) or None."""
    ok = np.isfinite(S).all(1)
    m = mu[ok]
    if len(m) < 3 or m.std() == 0:
        return None
    mz = (m - m.mean()) / m.std()
    best = None
    for j, sg in cand:
        sv = S[ok][:, j] * sg
        sd = sv.std()
        if sd == 0:
            continue
        cr = float((mz * (sv - sv.mean()) / sd).mean())
        if best is None or cr > best[2]:
            best = (j, sg, cr)
    return best


def element_drivers(c: pd.DataFrame, rtw: dict, rs: dict, rt_idx, rt_ok, damw: dict, ds: dict, dm_idx, dm_ok, zsf: pd.DataFrame, rule: str) -> dict:
    """One driver per ELEMENT (constraint_name), shared by all its replayed contingency cids: {element: (location, sign, corr, basis, n_binding)}.

    The element's window shadow price is the sum over its cids (so thin contingencies pool their evidence), fitted on the RT window if the element binds in at least
    MIN_RT_INTERVALS_DRIVER intervals, else on the DAM window if it binds in at least MIN_DAM_HOURS_DRIVER hours. Candidates: rule "data" = all 13 locations with
    both signs; rule "physics" = the admissible locations only (_admissible). A fit needs a signed correlation of MIN_DRIVER_CORR, else `geographic_driver`."""
    locs = list(rs["locations"])
    pos = {cid: i for i, cid in enumerate(rtw["cids"])}
    zpiv = zsf.pivot_table(index=["cid", "method"], columns="zone", values="sf", aggfunc="first").reindex(columns=ZONES)
    Srt, Sdm = rs["S"][rt_idx[rt_ok]], ds["S"][dm_idx[dm_ok]]
    Mrt, Mdm = rtw["M"][rt_ok], damw["M"][dm_ok]
    out = {}
    for el, g in c.groupby("constraint_name", sort=False):
        ks = [pos[x] for x in g["cid"]]
        w = g["sph_usd_mw_yr"].to_numpy(float)
        w = w / max(w.sum(), 1e-12)
        zvec = np.zeros(len(ZONES))
        for x, wt in zip(g.itertuples(), w):
            if (x.cid, x.sf_method) in zpiv.index:
                zvec += wt * zpiv.loc[(x.cid, x.sf_method)].fillna(0.0).to_numpy(float)
        mu_rt, mu_dm = Mrt[:, ks].sum(1), Mdm[:, ks].sum(1)
        n_rt, n_dm = int((mu_rt > 0).sum()), int((mu_dm > 0).sum())
        cand = [(j, sg) for j in range(len(locs)) for sg in (1, -1)] if rule == "data" else _admissible(zvec, locs)
        best = None
        for kind, mu_, S_, n_, min_n in (("fit_rt", mu_rt, Srt, n_rt, MIN_RT_INTERVALS_DRIVER), ("fit_dam", mu_dm, Sdm, n_dm, MIN_DAM_HOURS_DRIVER)):
            if n_ < min_n or not cand:
                continue
            bd = _best_driver(mu_, S_, cand)
            if bd is not None and bd[2] >= MIN_DRIVER_CORR:
                best = (locs[bd[0]], bd[1], bd[2], kind, n_)
                break                                          # an RT fit that clears the bar is used; the DAM window is only a fallback
        if best is None:
            loc, sg = geographic_driver(zvec, [g["from_sub"].iloc[0], g["to_sub"].iloc[0]])
            best = (loc, sg, np.nan, "geo", 0)
        out[el] = best
    return out


def _struct_hash(struct: pd.DataFrame) -> str:
    cols = struct[["cid", "sph_usd_mw_yr", "H_hours", "cap_usd_mwh", "driver_location", "driver_sign"]]
    return hashlib.md5(pd.util.hash_pandas_object(cols, index=False).to_numpy().tobytes()).hexdigest()


def constraint_structure(include_shrunk: bool = INCLUDE_SHRUNK_DEFAULT, refresh: bool = False, write: bool = True, driver_rule: str | None = None,
                         use_cache: bool = True) -> pd.DataFrame:
    """The synthetic congestion structure, one row per replayed constraint (cid): see module docstring.

    Columns: cid, constraint_name, element, element_type, kv, limit_mw, sf_use / sf_method, sph target and basis, H and basis, mean shadow while binding, cap,
    driver (location, sign, correlation, basis; one per element), planned-upgrade fields from constraints.csv. The cache is keyed on the named constants and the
    input files, so a change in either rebuilds it."""
    driver_rule = driver_rule or DRIVER_RULE
    if driver_rule not in ("data", "physics"):
        raise ValueError("driver_rule must be 'data' or 'physics'")
    zsf = zone_sf()
    cache = DERIVED / f"nodal_lanes_structure{'_shrunk' if include_shrunk else ''}_{driver_rule}.parquet"
    sigf = cache.with_suffix(".sig")
    sig = _sig(CACHE_VERSION, lane_constants(), include_shrunk, driver_rule,
               _stamp(REPO_DATA / "constraints.csv", ZONE_SF_FILE, DERIVED / "shadow_sced.parquet", DERIVED / "shadow_dam.parquet", DERIVED / "spp_node.parquet",
                      DERIVED / "spp_dam.parquet"))
    if use_cache and not refresh:
        got = _read_if_signed(cache, sigf, sig, pd.read_parquet)
        if got is not None:
            return got
    c = usable_table(include_shrunk).copy()
    sph = shadow_price_hours(c)
    Hdf, k_h = annual_binding_hours(c, sph)
    c = pd.concat([c, sph, Hdf], axis=1)
    c = c[c["sph_usd_mw_yr"].notna() & (c["sph_usd_mw_yr"] > 0) & c["H_hours"].notna()].copy()
    sced_cap = _sced_caps()
    c["cap_usd_mwh"] = c.apply(lambda r: _shadow_cap(r, sced_cap), axis=1)
    # mean shadow while binding must stay a fraction of the cap: raise H when the target is large
    mean0 = c["sph_usd_mw_yr"] / c["H_hours"]
    need = c["sph_usd_mw_yr"] / (MEAN_SHADOW_CAP_FRAC * c["cap_usd_mwh"])
    c["H_hours"] = np.where(mean0 > MEAN_SHADOW_CAP_FRAC * c["cap_usd_mwh"], np.minimum(need, H_MAX), c["H_hours"])
    c["mean_shadow_usd_mwh"] = c["sph_usd_mw_yr"] / c["H_hours"]
    c = c.reset_index(drop=True)
    cids = list(c["cid"])
    # ---- one driver per element
    rtw = rt_shadow_15(cids)
    rs = window_spreads("rt")
    damw = dam_shadow_hourly(cids)
    ds = window_spreads("dam")
    rt_idx = pd.Index(rs["start_us"]).get_indexer(rtw["start_us"])
    rt_ok = (rt_idx >= 0) & (rtw["covered_s"] >= COMPLETE_INTERVAL_S)       # complete 15-minute intervals only
    dm_idx = pd.Index(ds["start_us"]).get_indexer(damw["start_us"])
    dm_ok = dm_idx >= 0
    drivers = element_drivers(c, rtw, rs, rt_idx, rt_ok, damw, ds, dm_idx, dm_ok, zsf, driver_rule)
    c["driver_location"] = c["constraint_name"].map(lambda e: drivers[e][0])
    c["driver_sign"] = c["constraint_name"].map(lambda e: drivers[e][1])
    c["driver_corr"] = c["constraint_name"].map(lambda e: drivers[e][2])
    c["driver_basis"] = c["constraint_name"].map(lambda e: drivers[e][3])
    c["driver_n_binding"] = c["constraint_name"].map(lambda e: drivers[e][4])
    c["k_hours_per_musd"] = k_h
    keep = ["cid", "constraint_name", "element", "element_type", "kv", "from_sub", "to_sub", "median_limit_mw", "sf_use", "sf_method", "sf_rt_shrink",
            "sf_dam_shrink", "priority_rank", "annual_rent_alloc_musd", "annual_binding_hours", "rt_hours_binding", "dam_hours_binding",
            "rt_mean_shadow", "dam_mean_shadow", "sph_usd_mw_yr", "sph_basis", "H_hours", "H_basis", "mean_shadow_usd_mwh", "cap_usd_mwh",
            "driver_location", "driver_sign", "driver_corr", "driver_basis", "driver_n_binding", "k_hours_per_musd",
            "upgrade_project_id", "upgrade_in_service_date", "upgrade_match_conf", "gtc"]
    out = c[keep].copy()
    if not use_cache or READ_ONLY:
        return out                                            # sensitivity runs (and a READ_ONLY process, e.g. report-check) never touch the caches or the CSV
    out.to_parquet(cache, index=False)
    sigf.write_text(sig)
    if write and not include_shrunk and driver_rule == DRIVER_RULE:
        out.to_csv(STRUCT_FILE, index=False)
        write_labels(STRUCT_FILE, STRUCT_LABELS, out)
    return out


def write_labels(csv_path, spec: list, df: pd.DataFrame) -> None:
    """Sidecar `<name>.labels.csv` (column,label,source,note); spec entries are (fnmatch pattern, label, source, note), first match wins."""
    rows = []
    for col in df.columns:
        hit = next((x for x in spec if fnmatch.fnmatchcase(col, x[0])), None)
        if hit is None:
            raise KeyError(f"no label for column {col!r} of {csv_path}")
        rows.append({"column": col, "label": hit[1], "source": hit[2], "note": hit[3]})
    pd.DataFrame(rows).to_csv(str(csv_path).replace(".csv", ".labels.csv"), index=False)


STRUCT_LABELS = [
    ("cid", "REAL", "NP6-86-CD / NP4-191-CD", "CONSTRAINT_NAME|CONTINGENCY_NAME"),
    ("constraint_name", "REAL", "NP6-86-CD / NP4-191-CD", "the monitored element's code"),
    ("element", "DERIVED", "data/constraints.csv (phase 2)", "readable element name"),
    ("element_type", "DERIVED", "data/constraints.csv (phase 2)", "line, xfmr, gtc or other (heuristic)"),
    ("kv", "REAL", "NP6-86-CD", ""),
    ("from_sub", "DERIVED", "data/constraints.csv (phase 2 aliases)", ""),
    ("to_sub", "DERIVED", "data/constraints.csv (phase 2 aliases)", ""),
    ("median_limit_mw", "REAL", "NP6-86-CD / NP4-191-CD", "median limit over the window"),
    ("sf_use", "DERIVED", "phase 2", "rt = rt_reg shift factors; dam = day-ahead fit that is not attenuated (shrink >= 0.8); only these magnitudes are used"),
    ("sf_method", "DERIVED", "phase 2", "rt_reg or dam_reg"),
    ("sf_rt_shrink", "DERIVED", "phase 2", "ridge shrinkage factor of the RT fit"),
    ("sf_dam_shrink", "DERIVED", "phase 2", "ridge shrinkage factor of the DAM fit; below 0.8 the SF is sign-only"),
    ("priority_rank", "DERIVED", "data/constraints.csv (phase 2)", "50% structural IMM rent + 50% window rent"),
    ("annual_rent_alloc_musd", "REAL", "IMM 2025 State of the Market monthly reports", "2025 RT rent allocated to the cid, $M; blank/0 if the element is not in the IMM top 10"),
    ("annual_binding_hours", "REAL", "IMM 2025 (frequency top 10)", "binding hours of the element in 2025"),
    ("rt_hours_binding", "REAL", "NP6-86-CD", "hours binding in the real-time window (8.5 days)"),
    ("dam_hours_binding", "REAL", "NP4-191-CD", "hours binding in the day-ahead window (31 days)"),
    ("rt_mean_shadow", "REAL", "NP6-86-CD", "mean shadow price while binding, $/MWh"),
    ("dam_mean_shadow", "REAL", "NP4-191-CD", ""),
    ("sph_usd_mw_yr", "DERIVED", "IMM rent / limit, else window rent x 365/days x WINDOW_PERSISTENCE, capped at the IMM top-10 floor", "annual shadow-price-hours, $/MW-yr: the target the synthetic shadow prices are scaled to (same as gridsite.upgrades.sph_table)"),
    ("sph_basis", "DERIVED", "this module", "imm_rent_alloc, window_rt or window_dam (_capped when the element-level IMM floor applies)"),
    ("H_hours", "DERIVED", "IMM hours (REAL), else IMM rent x median hours per $M, else window hours x 365/days x persistence^0.5", "binding hours a year used for the synthetic shadow prices (4 H fifteen-minute intervals)"),
    ("H_basis", "DERIVED", "this module", ""),
    ("mean_shadow_usd_mwh", "DERIVED", "sph / H", "mean synthetic shadow price while binding"),
    ("cap_usd_mwh", "REAL", "SCED max shadow price per cid, else IMM 2025 caps by kV class", "synthetic shadow prices are limited to it"),
    ("driver_location", "DERIVED", "spp_node / spp_dam spreads against HB_BUSAVG", "the hub or zone whose price spread drives when the constraint binds"),
    ("driver_sign", "DERIVED", "sign of the best correlation", "+1: binds when the location is dear against HB_BUSAVG; -1: when it is cheap"),
    ("driver_corr", "DERIVED", "Pearson, window shadow price vs spread", "blank when no window fit; below MIN_DRIVER_CORR the geographic mapping is used"),
    ("driver_basis", "DERIVED", "this module", "fit_rt, fit_dam or geo (named geographic mapping)"),
    ("driver_n_binding", "DERIVED", "this module", "binding intervals / hours the fit saw"),
    ("k_hours_per_musd", "DERIVED", "median IMM hours per $M over elements with both", "used where only the IMM rent is known"),
    ("upgrade_project_id", "DERIVED", "data/constraints.csv (phase 2 match of TPIT / RTP projects)", "a name or bus overlap, not proof of relief"),
    ("upgrade_in_service_date", "DERIVED", "data/constraints.csv", ""),
    ("upgrade_match_conf", "DERIVED", "data/constraints.csv", ""),
    ("gtc", "DERIVED", "data/constraints.csv", ""),
]


# =================================================================================================================
# mu_tilde: replay the structure on every year
# =================================================================================================================
def _year_blocks(dates: np.ndarray):
    yrs = np.array([int(d[:4]) for d in dates])
    out = []
    for y in sorted(set(yrs)):
        idx = np.where(yrs == y)[0]
        n_days_full = int(pd.Timestamp(f"{y}-12-31").dayofyear)
        out.append((int(y), int(idx[0]), int(idx[-1]) + 1, len(idx) / n_days_full))
    return out


def _fit_to_target(e: np.ndarray, target: float, cap: float):
    """mu = s*e with sum(mu)*DT_H = target and mu <= cap (clipped values stay at the cap, the rest is rescaled). Returns (mu, shortfall_share)."""
    mu = np.zeros_like(e)
    free = np.ones(len(e), bool)
    need = target / DT_H
    for _ in range(FIT_ITER_MAX):
        tot = e[free].sum()
        if tot <= 0 or not free.any():
            break
        s = (need - mu[~free].sum()) / tot
        cand = s * e
        over = free & (cand > cap)
        if not over.any():
            mu[free] = cand[free]
            break
        mu[over] = cap
        free &= ~over
    else:
        mu[free] = np.minimum(s * e[free], cap)
    short = max(0.0, 1.0 - mu.sum() * DT_H / target) if target > 0 else 0.0
    return mu, short


def build_mu_tilde(struct: pd.DataFrame, hist: dict | None = None, save: str | None = None):
    """Synthetic shadow prices on the lane clock: scipy CSR (T, K) in $/MWh (T = days x 96, K = rows of `struct`), plus per-year statistics.

    Per year: the top n_k = round(4 * H_k * year fraction) intervals of the driver (+/- spread against HB_BUSAVG) bind, mu proportional to the
    excess over the (n_k + 1)-th value, scaled to the annual shadow-price-hours target (times the year fraction), limited to the cap."""
    hist = hist or price_history()
    dates = hist["dates"]
    T = len(dates) * NI
    blocks = _year_blocks(dates)
    rows, cols, vals = [], [], []
    stats = []
    spread_flat = {k: v.reshape(-1) for k, v in hist["spread"].items()}
    for kk, r in struct.reset_index(drop=True).iterrows():
        d_all = spread_flat[r["driver_location"]] * int(r["driver_sign"])
        for (y, a, b, frac) in blocks:
            a0, b0 = a * NI, b * NI
            n_int = b0 - a0
            n_k = int(round(INTERVALS_PER_HOUR * r["H_hours"] * frac))
            if n_k < MIN_BIND_INTERVALS or n_k >= n_int - 1:
                stats.append((r["cid"], y, 0, 0.0, 0.0, 0.0, 0.0))
                continue
            d = d_all[a0:b0]
            part = np.argpartition(d, n_int - n_k - 1)
            theta = d[part[n_int - n_k - 1]]
            top = part[n_int - n_k:]
            e = d[top] - theta
            if not (e > 0).any():
                e = np.ones_like(e)
            tgt = float(r["sph_usd_mw_yr"]) * frac
            mu, short = _fit_to_target(e.astype(float), tgt, float(r["cap_usd_mwh"]))
            rows.append(top + a0)
            cols.append(np.full(n_k, kk, np.int32))
            vals.append(mu)
            stats.append((r["cid"], y, n_k, tgt, float(mu.sum() * DT_H), short, float((mu >= r["cap_usd_mwh"] - 1e-9).mean())))
    rows = np.concatenate(rows) if rows else np.array([], np.int64)
    cols = np.concatenate(cols) if cols else np.array([], np.int32)
    vals = np.concatenate(vals) if vals else np.array([], float)
    Mu = sps.csr_matrix((vals, (rows, cols)), shape=(T, len(struct)))
    st = pd.DataFrame(stats, columns=["cid", "year", "n_intervals", "target_usd_mw", "realised_usd_mw", "shortfall_share", "share_at_cap"])
    if save:
        sps.save_npz(save, Mu)
    return Mu, st


def _price_stamp() -> list:
    return _stamp(C.CACHE / "prices_ERCOT.npz", HUB_FILE)


@lru_cache(maxsize=4)
def mu_tilde(include_shrunk: bool = INCLUDE_SHRUNK_DEFAULT, driver_rule: str | None = None):
    """(struct, Mu, stats) cached on disk. The key holds the named constants, the structure (targets, hours, caps, drivers) and the price history."""
    driver_rule = driver_rule or DRIVER_RULE
    struct = constraint_structure(include_shrunk, driver_rule=driver_rule)
    tag = ("_shrunk" if include_shrunk else "") + "_" + driver_rule
    f = DERIVED / f"nodal_lanes_mu_tilde{tag}.npz"
    g = DERIVED / f"nodal_lanes_mu_tilde{tag}_stats.parquet"
    sig = _sig(CACHE_VERSION, lane_constants(), _struct_hash(struct), _price_stamp())
    sigf = f.with_suffix(".sig")
    if g.exists():
        got = _read_if_signed(f, sigf, sig, lambda x: sps.load_npz(x).tocsr())
        if got is not None:
            return struct, got, pd.read_parquet(g)
    Mu, st = build_mu_tilde(struct)
    if not READ_ONLY:
        sps.save_npz(f, Mu)
        st.to_parquet(g, index=False)
        sigf.write_text(sig)
    return struct, Mu, st


# =================================================================================================================
# Node shift factors and the lanes
# =================================================================================================================
def zone_of_substations() -> dict:
    """{sub: arena load zone}: the municipal / co-op zone (LZ_AEN, LZ_CPS, LZ_LCRA, LZ_RAYBN) when the substation serves load there, else its
    NP4-160 settlement load zone. Gives the zone price a node is measured against, its SF_zone, and its weather in the dispatch."""
    from .nodal_market import arena_zone_of_substations

    return arena_zone_of_substations()


def sub_sf_matrix(subs, struct: pd.DataFrame, zones: dict | None = None) -> dict:
    """Shift factors of substations to the replayed constraints.

    Returns {'SF': (N,K) substation SF (implausible read as 0, absent as 0, |SF| capped at SF_ABS_CAP), 'ZSF': (N,K) SF_zone of the substation's zone,
             'DSF': SF - ZSF, 'zone': list, 'n_nonzero': (N,)}. Substation SF = phase 2 sf_sub (median over the highest-kV buses)."""
    subs = list(subs)
    zones = zones or zone_of_substations()
    cids = list(struct["cid"])
    meth = dict(zip(struct["cid"], struct["sf_method"]))
    import pyarrow.parquet as pq

    ss = pq.read_table(SF_SUB, columns=["sub", "cid", "method", "sf", "quality"], filters=[("sub", "in", subs), ("cid", "in", cids)]).to_pandas()
    ss = ss[ss["method"] == ss["cid"].map(meth)]
    bad = ss["quality"].astype(str).apply(lambda q: any(t in q.split("+") for t in SF_BAD_QUALITY))
    ss = ss[~bad]
    piv = ss.pivot_table(index="sub", columns="cid", values="sf", aggfunc="first").reindex(index=subs, columns=cids).fillna(0.0)
    SF = np.clip(piv.to_numpy(float), -SF_ABS_CAP, SF_ABS_CAP)
    zs = zone_sf()
    zs = zs[zs["cid"].isin(cids)]
    zs = zs[zs["method"] == zs["cid"].map(meth)]
    zp = zs.pivot_table(index="zone", columns="cid", values="sf", aggfunc="first").reindex(index=ZONES, columns=cids).fillna(0.0)
    zlist = [zones.get(s, "") for s in subs]
    if any(z not in ZONES for z in zlist):
        bad_s = [s for s, z in zip(subs, zlist) if z not in ZONES]
        raise ValueError(f"substations without an arena zone: {bad_s[:5]}")
    ZSF = np.stack([zp.loc[z].to_numpy(float) for z in zlist], 0)
    return {"SF": SF, "ZSF": ZSF, "DSF": SF - ZSF, "zone": zlist, "subs": subs, "cids": cids, "n_nonzero": (np.abs(SF) > 0).sum(1)}


def node_prices(DSF: np.ndarray, zone_list, Mu: sps.csr_matrix, hist: dict | None = None, mu_scale: np.ndarray | None = None,
                day_slice: slice | None = None) -> np.ndarray:
    """Rebuilt node prices (days, N, 96): zone price - (Mu * mu_scale) @ DSF.T, never pushed beyond [PRICE_FLOOR, PRICE_CAP] by the basis.

    mu_scale: (K,) multipliers on the shadow prices (planned upgrades, saturation, a what-if); None = 1. day_slice: rows of the lane clock."""
    hist = hist or price_history()
    D = len(hist["dates"])
    ds = day_slice or slice(0, D)
    d0, d1, _ = ds.indices(D)
    Mu_s = Mu[d0 * NI:d1 * NI]
    if mu_scale is not None:
        Mu_s = Mu_s @ sps.diags(np.asarray(mu_scale, float))
    zi = {z: k for k, z in enumerate(ZONES)}
    N = DSF.shape[0]
    basis = np.asarray(Mu_s @ np.asarray(DSF, float).T)                    # (T, N): sum_k DSF * mu
    Pz = np.stack([hist["P_zone"][d0:d1, zi[z], :] for z in zone_list], 1)  # (days, N, 96)
    B = basis.reshape(d1 - d0, NI, N).transpose(0, 2, 1)
    P = Pz - B
    lo = np.minimum(Pz, PRICE_FLOOR)
    hi = np.maximum(Pz, PRICE_CAP)
    return np.minimum(np.maximum(P, lo), hi)


def lane_constants() -> dict:
    """The named constants that define a rebuild (written into the lane file and into every cache key, so a lane set can be traced to its settings)."""
    keys = ["LANE_START", "LANE_END", "PRICE_CAP", "PRICE_FLOOR", "SHADOW_CAP_DEFAULT", "MEAN_SHADOW_CAP_FRAC", "SCED_RUN_MIN", "SCED_RUN_CAP_S", "DAM_WINDOW_DAYS",
            "MIN_RT_RUNS_FOR_SPH", "WINDOW_PERSISTENCE", "HOURS_SHARE_OF_PERSISTENCE", "IMM_FLOOR_RANK_RENT_MUSD", "H_MIN", "H_MAX", "MIN_RT_INTERVALS_DRIVER",
            "MIN_DAM_HOURS_DRIVER", "MIN_DRIVER_CORR", "GEO_MIN_ZONE_SF", "GEO_DEFAULT_SIGN", "GEO_HUB", "PANHANDLE_BOX", "DRIVER_RULE", "DRIVER_ZONE_REL_MIN", "LOC_ZONE",
            "ZONE_SF_MIN_R2", "BUS_MEAN_MIN_KV", "SHRINK_MIN", "INCLUDE_SHRUNK_DEFAULT", "SF_BAD_QUALITY", "SF_ABS_CAP", "HB_PAN_FALLBACK", "COMPLETE_INTERVAL_S",
            "K_HOURS_PER_MUSD_FALLBACK", "DAYS_PER_YEAR", "MIN_BIND_INTERVALS", "FIT_ITER_MAX"]
    return {k: globals()[k] for k in keys}


def lane_signature(subs, include_shrunk: bool = INCLUDE_SHRUNK_DEFAULT, driver_rule: str | None = None) -> str:
    """Key of a lane set: constants, structure (with the shift-factor method of every constraint), price history, the shift-factor and zone-SF files, the substations
    and their zones. `sites.ensure_lanes` rebuilds a stored lane file whose key differs."""
    struct, _, _ = mu_tilde(include_shrunk, driver_rule)
    subs = list(subs)
    zmap = zone_of_substations()
    return _sig(CACHE_VERSION, lane_constants(), _struct_hash(struct), _price_stamp(), subs, struct["sf_method"].tolist(), _stamp(SF_SUB, ZONE_SF_FILE),
                [zmap.get(s, "") for s in subs])


def lane_file_meta(tag: str) -> dict:
    """The `meta` block of a stored lane file (constants, key, build time) without loading the prices."""
    z = np.load(DERIVED / f"nodal_lanes_{tag}.npz", allow_pickle=False)
    return json.loads(str(z["meta"]))


def build_lanes(subs, tag: str, include_controls: bool = True, include_shrunk: bool = INCLUDE_SHRUNK_DEFAULT, save: bool = True) -> dict:
    """Price lanes for the substations `subs` (the node = the substation's highest-kV buses) and, first, the 8 arena zone lanes as controls.

    Returns the dict of `load_lanes` (dates, lanes, zones, P float64 (days, lanes, 96), ref, meta) and saves derived/nodal_lanes_<tag>.npz."""
    t0 = time.time()
    struct, Mu, stats = mu_tilde(include_shrunk)
    hist = price_history()
    subs = list(subs)
    M = sub_sf_matrix(subs, struct)
    Pn = node_prices(M["DSF"], M["zone"], Mu, hist)
    if include_controls:
        P = np.concatenate([hist["P_zone"], Pn], axis=1)
        lanes = list(ZONES) + subs
        zones = list(ZONES) + list(M["zone"])
    else:
        P, lanes, zones = Pn, subs, list(M["zone"])
    meta = {"label": "SIM/DERIVED: zone price minus (SF - SF_zone) x synthetic shadow prices (gridsite.nodal_lanes)", "constants": lane_constants(),
            "n_replayed_constraints": int(len(struct)), "controls": list(ZONES) if include_controls else [], "built_s": round(time.time() - t0, 1),
            "sig": lane_signature(subs, include_shrunk), "driver_rule": DRIVER_RULE}
    out = {"dates": hist["dates"], "lanes": lanes, "zones": zones, "P": P, "ref": hist["ref"], "meta": meta, "DSF": M["DSF"], "SF": M["SF"], "ZSF": M["ZSF"],
           "cids": M["cids"], "subs": subs}
    if save:
        save_lanes(tag, lanes, zones, P, meta, hist)
    return out


def lane_summary(P: np.ndarray, lanes, zones, hist: dict | None = None, year: int = 2025) -> pd.DataFrame:
    """Per lane, one year: mean and std of the basis against the zone price, mean |basis|, share of intervals with a basis, max / min basis."""
    hist = hist or price_history()
    zi = {z: k for k, z in enumerate(ZONES)}
    sel = np.array([d.startswith(str(year)) for d in hist["dates"]])
    rows = []
    for j, (ln, z) in enumerate(zip(lanes, zones)):
        d = P[sel][:, j, :] - hist["P_zone"][sel][:, zi[z], :]
        rows.append({"lane": ln, "zone": z, "mean_basis": d.mean(), "std_basis": d.std(), "mean_abs_basis": np.abs(d).mean(), "share_nonzero": (np.abs(d) > 1e-6).mean(),
                     "max_basis": d.max(), "min_basis": d.min(), "mean_price": P[sel][:, j, :].mean()})
    return pd.DataFrame(rows)


def save_lanes(tag: str, lane_ids, zone_list, P, meta: dict, hist: dict | None = None) -> str:
    hist = hist or price_history()
    f = DERIVED / f"nodal_lanes_{tag}.npz"
    np.savez_compressed(f, dates=hist["dates"], lanes=np.array(lane_ids), zones=np.array(zone_list), P=np.asarray(P, np.float32),
                        ref=hist["ref"].astype(np.float32), meta=json.dumps(meta))
    return str(f)


def load_lanes(tag: str) -> dict:
    z = np.load(DERIVED / f"nodal_lanes_{tag}.npz", allow_pickle=False)
    return {"dates": z["dates"].astype(str), "lanes": list(z["lanes"].astype(str)), "zones": list(z["zones"].astype(str)),
            "P": z["P"].astype(np.float64), "ref": z["ref"].astype(np.float64), "meta": json.loads(str(z["meta"]))}


# =================================================================================================================
# Validation
# =================================================================================================================
def resource_node_table() -> pd.DataFrame:
    """One row per resource node: node name, bus, substation, kV, zone (bus-level NOIE zone first, else load zone)."""
    sp = _read_csv("settlement_points.csv")
    rn = sp[sp["RESOURCE_NODE"].notna()].drop_duplicates("RESOURCE_NODE").copy()
    rn["zone"] = rn["NOIE_ZONE"].where(rn["NOIE_ZONE"].notna(), rn["SETTLEMENT_LOAD_ZONE"])
    rn = rn.rename(columns={"RESOURCE_NODE": "node", "ELECTRICAL_BUS": "bus", "SUBSTATION": "sub", "VOLTAGE_LEVEL": "kv"})
    return rn[["node", "bus", "sub", "kv", "zone"]].reset_index(drop=True)


def bus_sf_matrix(buses, struct: pd.DataFrame) -> np.ndarray:
    """Bus-level SF (N,K) for the replayed constraints: phase 2 sf_long with the same method choice as `sub_sf_matrix` (weak / implausible read as 0)."""
    import pyarrow.parquet as pq

    buses = list(buses)
    cids = list(struct["cid"])
    meth = dict(zip(struct["cid"], struct["sf_method"]))
    t = pq.read_table(SF_LONG, columns=["bus", "cid", "sf", "method", "quality"], filters=[("cid", "in", cids), ("bus", "in", buses)]).to_pandas()
    for col in ("bus", "cid", "method", "quality"):
        t[col] = t[col].astype(str)
    t = t[t["method"] == t["cid"].map(meth)]
    t = t[~t["quality"].apply(lambda q: any(x in q.split("+") for x in SF_BAD_QUALITY))]
    piv = t.pivot_table(index="bus", columns="cid", values="sf", aggfunc="first").reindex(index=buses, columns=cids).fillna(0.0)
    return np.clip(piv.to_numpy(float), -SF_ABS_CAP, SF_ABS_CAP)


def _zone_sf_matrix(zone_list, struct: pd.DataFrame) -> np.ndarray:
    cids = list(struct["cid"])
    meth = dict(zip(struct["cid"], struct["sf_method"]))
    zs = zone_sf()
    zs = zs[zs["cid"].isin(cids)]
    zs = zs[zs["method"] == zs["cid"].map(meth)]
    zp = zs.pivot_table(index="zone", columns="cid", values="sf", aggfunc="first").reindex(index=ZONES, columns=cids).fillna(0.0)
    return np.stack([zp.loc[z].to_numpy(float) for z in zone_list], 0)


def window_check(struct: pd.DataFrame | None = None) -> dict:
    """(a) The rebuilt node basis against the actual node basis in the real-time window, with the ACTUAL shadow prices.

    basis_actual[n,t]  = SPP_node - SPP_zone(n)                     (15-minute SPP, 3-10 Oct 2026; the RTRDPA adder cancels)
    basis_rebuilt[n,t] = - sum_k (SF[n,k] - SF_zone[k]) * mu15[k,t]  (mu15 = the 15-minute mean of the published SCED shadow price)
    over the resource nodes priced in spp_node."""
    struct = struct if struct is not None else constraint_structure()
    cids = list(struct["cid"])
    rn = resource_node_table()
    spp = pd.read_parquet(DERIVED / "spp_node.parquet")
    spp = spp[~spp["dst_rep"]]
    spp["t"] = _us(spp["interval_start_utc"])
    names = set(rn["node"]) | set(ZONES)
    spp = spp[spp["settlement_point"].isin(names)]
    piv = spp.groupby(["t", "settlement_point"])["price"].mean().unstack()
    w = rt_shadow_15(cids)
    ok = w["covered_s"] >= COMPLETE_INTERVAL_S
    t_all = w["start_us"][ok]
    keep = np.isin(t_all, piv.index.to_numpy())
    t_use = t_all[keep]
    M = w["M"][ok][keep]
    piv = piv.reindex(t_use)
    rn = rn[rn["node"].isin(piv.columns) & rn["zone"].isin(ZONES)].reset_index(drop=True)
    node_p = piv[list(rn["node"])].to_numpy(float)
    zone_p = np.stack([piv[z].to_numpy(float) for z in rn["zone"]], 1)
    actual = node_p - zone_p
    SF = bus_sf_matrix(rn["bus"], struct)
    ZSF = _zone_sf_matrix(rn["zone"], struct)
    DSF = SF - ZSF
    # nodes that exist for only part of the window (new in the Oct model) are left out so every node has every interval
    cols = np.isfinite(actual).all(0)
    actual, rn, SF, ZSF, DSF = actual[:, cols], rn[cols].reset_index(drop=True), SF[cols], ZSF[cols], DSF[cols]
    rebuilt = -(M @ DSF.T)
    use_rt = (struct["sf_use"] == "rt").to_numpy()
    rebuilt_rt = -(M[:, use_rt] @ DSF[:, use_rt].T)
    good = np.isfinite(actual).all(1)
    return {"t": t_use[good], "rn": rn, "actual": actual[good], "rebuilt": rebuilt[good], "rebuilt_rt": rebuilt_rt[good], "M": M[good], "DSF": DSF,
            "SF": SF, "ZSF": ZSF}


def dam_window_check(struct: pd.DataFrame | None = None) -> dict:
    """(a2) The same rebuild in the day-ahead market: hourly DAM node basis (SPP node - SPP zone, 10 Sep - 10 Oct 2026) against
    - sum_k (SF - SF_zone) x hourly DAM shadow price, at the resource nodes. Most replayed constraints (197 of 260) have a day-ahead fit, so this is the
    check of the shift factors that carry most of the structure. Cross-market caveat: the 63 rt_reg constraints apply their real-time SF to day-ahead shadow prices."""
    struct = struct if struct is not None else constraint_structure()
    cids = list(struct["cid"])
    rn = resource_node_table()
    spp = pd.read_parquet(DERIVED / "spp_dam.parquet")
    spp = spp[~spp["dst_rep"]]
    spp["t"] = _us(spp["interval_start_utc"])
    names = set(rn["node"]) | set(ZONES)
    spp = spp[spp["settlement_point"].isin(names)]
    piv = spp.groupby(["t", "settlement_point"])["price"].mean().unstack()
    w = dam_shadow_hourly(cids)
    keep = np.isin(w["start_us"], piv.index.to_numpy())
    t_use = w["start_us"][keep]
    M = w["M"][keep]
    piv = piv.reindex(t_use)
    rn = rn[rn["node"].isin(piv.columns) & rn["zone"].isin(ZONES)].reset_index(drop=True)
    actual = piv[list(rn["node"])].to_numpy(float) - np.stack([piv[z].to_numpy(float) for z in rn["zone"]], 1)
    cols = np.isfinite(actual).all(0)                         # nodes that exist for only part of the month are left out
    actual, rn = actual[:, cols], rn[cols].reset_index(drop=True)
    DSF = bus_sf_matrix(rn["bus"], struct) - _zone_sf_matrix(rn["zone"], struct)
    use_rt = (struct["sf_use"] == "rt").to_numpy()
    out = {"t": t_use, "rn": rn, "actual": actual}
    out["rebuilt"] = -(M @ DSF.T)
    out["rebuilt_dam"] = -(M[:, ~use_rt] @ DSF[:, ~use_rt].T)
    out["rebuilt_rt"] = -(M[:, use_rt] @ DSF[:, use_rt].T)
    good = np.isfinite(actual).all(1)
    return {k: (v[good] if isinstance(v, np.ndarray) and v.ndim >= 1 and len(v) == len(good) else v) for k, v in out.items()}


def _fit_stats(a: np.ndarray, b: np.ndarray) -> dict:
    """Pooled statistics of rebuilt (b) against actual (a), both (T, N): correlation, MAE, RMSE, bias, per-node correlation and window totals."""
    af, bf = a.reshape(-1), b.reshape(-1)
    corr = float(np.corrcoef(af, bf)[0, 1]) if af.std() > 0 and bf.std() > 0 else np.nan
    nc = []
    for j in range(a.shape[1]):
        if a[:, j].std() > 1e-6 and b[:, j].std() > 1e-6:
            nc.append(np.corrcoef(a[:, j], b[:, j])[0, 1])
    ta, tb = a.sum(0) * DT_H, b.sum(0) * DT_H                       # $/MW over the window, per node
    return {"corr_pooled": corr, "corr_node_median": float(np.median(nc)) if nc else np.nan, "mae_usd_mwh": float(np.abs(af - bf).mean()),
            "rmse_usd_mwh": float(np.sqrt(((af - bf) ** 2).mean())), "bias_usd_mwh": float((bf - af).mean()),
            "mae_if_basis_were_zero_usd_mwh": float(np.abs(af).mean()),
            "total_actual_mean_usd_per_mw": float(ta.mean()), "total_rebuilt_mean_usd_per_mw": float(tb.mean()),
            "total_abs_actual_mean_usd_per_mw": float(np.abs(ta).mean()),
            "total_corr_across_nodes": float(np.corrcoef(ta, tb)[0, 1]) if ta.std() > 0 and tb.std() > 0 else np.nan,
            "n_intervals": int(a.shape[0]), "n_nodes": int(a.shape[1])}


def validation_table(struct: pd.DataFrame | None = None, Mu=None, stats=None, with_window: bool = True) -> pd.DataFrame:
    """Rows (check, scope, metric, value, n, note) for data/lane_validation.csv."""
    if struct is None:
        struct, Mu, stats = mu_tilde()
    rows = []

    def add(check, scope, metric, value, n="", note=""):
        rows.append({"check": check, "scope": scope, "metric": metric, "value": value, "n": n, "note": note})

    hist = price_history()
    # ---- (a) actual-mu rebuild vs actual node basis
    if with_window:
        wc = window_check(struct)
        T = wc["t"]
        tt = pd.to_datetime(T, unit="us", utc=True)
        cpt = tt.tz_convert(CPT)
        d5 = pd.Timestamp(VALID_FIT_START_CPT, tz=CPT)
        d9 = pd.Timestamp(VALID_FIT_END_CPT, tz=CPT)
        splits = {"3-10 Oct, all intervals": np.ones(len(T), bool),
                  "3-5 Oct (before the 5 Oct start of the fit window: out of sample in time)": np.asarray(cpt < d5),
                  "5 Oct - 9 Oct noon (inside the fit window)": np.asarray((cpt >= d5) & (cpt < d9)),
                  "last 24 h of the RT fit (phase 2 holdout)": np.asarray(tt > (tt.max() - pd.Timedelta(hours=HOLDOUT_RT_H)))}
        for nm, msk in splits.items():
            if msk.sum() < 8:
                continue
            for lab, key in (("all replayed constraints", "rebuilt"), ("rt_reg constraints only", "rebuilt_rt")):
                s = _fit_stats(wc["actual"][msk], wc[key][msk])
                for m_, v in s.items():
                    if m_ in ("n_intervals", "n_nodes"):
                        continue
                    add("a_window_rebuild", f"{nm} | {lab}", m_, round(v, 4) if np.isfinite(v) else v, f"{s['n_intervals']} intervals x {s['n_nodes']} nodes",
                        "resource nodes; node basis = node SPP - zone SPP; rebuilt with the actual shadow prices")
        dc = dam_window_check(struct)
        td = pd.to_datetime(dc["t"], unit="us", utc=True)
        dsplits = {"10 Sep - 10 Oct, all hours": np.ones(len(td), bool), "last 5 DAM days (phase 2 holdout)": np.asarray(td > (td.max() - pd.Timedelta(days=HOLDOUT_DAM_DAYS)))}
        for nm, msk in dsplits.items():
            for lab, key in (("all replayed constraints", "rebuilt"), ("day-ahead-fitted constraints only", "rebuilt_dam"), ("rt_reg constraints only (cross-market)", "rebuilt_rt")):
                s_ = _fit_stats(dc["actual"][msk], dc[key][msk])
                for m_, v in s_.items():
                    if m_ in ("n_intervals", "n_nodes"):
                        continue
                    add("a2_dam_rebuild", f"{nm} | {lab}", m_, round(v, 4) if np.isfinite(v) else v, f"{s_['n_intervals']} hours x {s_['n_nodes']} nodes",
                        "resource nodes; hourly day-ahead SPP basis vs the rebuild with the actual day-ahead shadow prices")
        # scale check: the SIM structure's annual |node - zone| against the window's actual |node - zone|
        rn = wc["rn"]
        d25 = [i for i, d in enumerate(hist["dates"]) if d.startswith(str(REPLAY_YEAR))]
        sl = slice(d25[0], d25[-1] + 1)
        P25 = node_prices(wc["DSF"], list(rn["zone"]), Mu, hist, day_slice=sl)
        zi = {z: k for k, z in enumerate(ZONES)}
        Pz = np.stack([hist["P_zone"][sl, zi[z], :] for z in rn["zone"]], 1)
        syn = np.abs(P25 - Pz).transpose(0, 2, 1).reshape(-1, len(rn))
        act = np.abs(wc["actual"])
        reb = np.abs(wc["rebuilt"])
        add("d_scale", "resource nodes", "mean |node - zone|, actual, RT window 3-10 Oct ($/MWh)", round(float(act.mean()), 3), len(rn), "an outage-heavy shoulder week")
        add("d_scale", "resource nodes", "mean |node - zone|, rebuilt with the actual mu, same window ($/MWh)", round(float(reb.mean()), 3), len(rn), "")
        add("d_scale", "resource nodes", "mean |node - zone|, synthetic 2025 replay ($/MWh)", round(float(syn.mean()), 3), len(rn), "annual mean of the SIM structure")
        add("d_scale", "resource nodes", "median over nodes of mean |node - zone|: actual window", round(float(np.median(act.mean(0))), 3), len(rn), "")
        add("d_scale", "resource nodes", "median over nodes of mean |node - zone|: synthetic 2025", round(float(np.median(syn.mean(0))), 3), len(rn), "")
        add("d_scale", "resource nodes", "90th percentile over nodes of mean |node - zone|: actual window", round(float(np.quantile(act.mean(0), 0.9)), 3), len(rn), "")
        add("d_scale", "resource nodes", "90th percentile over nodes of mean |node - zone|: synthetic 2025", round(float(np.quantile(syn.mean(0), 0.9)), 3), len(rn), "")
    # ---- (b) mu_tilde against its targets
    st = stats
    t_ = st[st["target_usd_mw"] > 0]
    for y, g in t_.groupby("year"):
        e = (g["realised_usd_mw"] / g["target_usd_mw"] - 1.0).abs()
        add("b_mu_tilde", f"year {y}", "constraints replayed", int(len(g)), int(len(g)), "")
        add("b_mu_tilde", f"year {y}", "max |annual shadow-price-hours / target - 1|", float(f"{e.max():.2e}"), int(len(g)),
            "target = IMM 2025 rent / limit, or the persistence-discounted window figure (x year fraction in 2026)")
        add("b_mu_tilde", f"year {y}", "share of constraint-years that fall short of the target (shadow-price cap binds)", round(float((g["shortfall_share"] > 0.01).mean()), 4), int(len(g)), "")
        add("b_mu_tilde", f"year {y}", "mean share of binding intervals sitting at the shadow-price cap", round(float(g["share_at_cap"].mean()), 4), int(len(g)), "")
    full = st[(st["year"] == REPLAY_YEAR) & (st["n_intervals"] > 0)].merge(struct[["cid", "H_hours"]], on="cid")
    add("b_mu_tilde", "2025", "max |binding 15-min intervals / (4 H_k) - 1|", float(f"{((full['n_intervals'] / (INTERVALS_PER_HOUR * full['H_hours'])) - 1).abs().max():.2e}"), len(full),
        "rounding of 4 H_k to whole intervals")
    for r in struct[struct["sph_basis"] == "imm_rent_alloc"].itertuples():
        g = st[(st["cid"] == r.cid) & (st["year"] == REPLAY_YEAR)].iloc[0]
        add("b_mu_tilde", f"IMM-anchored element {r.cid}", "2025 realised rent, $M (shadow-price-hours x median limit)",
            round(float(g["realised_usd_mw"]) * float(r.median_limit_mw) / 1e6, 3), 1, f"IMM 2025 rent allocated to this cid: {r.annual_rent_alloc_musd} $M; H_k {r.H_hours:.0f} h")
    # ---- (c) control lanes are exactly the arena zone prices
    try:
        from arena.data import Market

        m = Market("ERCOT")
        sel = np.where(m.dates >= LANE_START)[0]
        diff = float(np.abs(m.P[sel] - hist["P_zone"]).max())
        add("c_controls", "8 zone lanes", "max |lane price - arena Market price| ($/MWh)", diff, int(len(sel)), "0 = bit-identical: the control lanes ARE the arena's zone prices")
        add("c_controls", "HB_BUSAVG", "reference series identical to the arena's (1 = yes)", int(np.array_equal(m.ref[sel], hist["ref"])), int(len(sel)), "")
    except Exception as ex:  # pragma: no cover
        add("c_controls", "8 zone lanes", "comparison failed", str(ex)[:100], "", "")
    # ---- (e) zone shift factors
    zs = zone_sf()
    for m_ in ("rt_reg", "dam_reg"):
        g = zs[zs["method"] == m_]
        add("e_zone_sf", m_, "zone regression R2 (in sample): minimum", round(float(g["r2_zone"].min()), 4), int(g["cid"].nunique()), "8 load zones; the same operator as the bus fit of phase 2")
        add("e_zone_sf", m_, "zone regression R2 (in sample): median", round(float(g["r2_zone"].median()), 4), int(g["cid"].nunique()), "")
        add("e_zone_sf", m_, "share of cid-zones using the direct regression (else the bus mean)", round(float((g["source"] == "direct").mean()), 4), int(len(g)),
            f"bus mean is used when the zone R2 is below {ZONE_SF_MIN_R2}")
        add("e_zone_sf", m_, "correlation of direct SF_zone with the unweighted bus mean in the zone", round(float(np.corrcoef(g["sf"], g["bus_mean"])[0, 1]), 4), int(len(g)),
            "the bus mean is unweighted; the zone price is load-weighted")
    # ---- (f) drivers
    nf = int((struct["driver_basis"] != "geo").sum())
    add("f_driver", "replayed constraints", "driver fitted on the RT window", int((struct["driver_basis"] == "fit_rt").sum()), len(struct), "")
    add("f_driver", "replayed constraints", "driver fitted on the DAM window", int((struct["driver_basis"] == "fit_dam").sum()), len(struct), "")
    add("f_driver", "replayed constraints", "named geographic mapping (no usable fit)", int((struct["driver_basis"] == "geo").sum()), len(struct), "")
    add("f_driver", "fitted drivers", "median signed correlation with the observed shadow price", round(float(struct.loc[struct["driver_basis"] != "geo", "driver_corr"].median()), 3), nf, "")
    return pd.DataFrame(rows)


def sensitivity_lanes(subs, overrides: dict, include_shrunk: bool = False, driver_rule: str | None = None, hist: dict | None = None):
    """Rebuild node prices for `subs` with some module constants changed (e.g. {"WINDOW_PERSISTENCE": 0.30}), without touching any cache or file.

    Returns (P (days, N, 96), zones list, struct). Used only for the sensitivity runs reported in notes/phase3.md."""
    g = globals()
    saved = {k: g[k] for k in overrides}
    try:
        g.update(overrides)
        struct = constraint_structure(include_shrunk, driver_rule=driver_rule, use_cache=False)
        hist = hist or price_history()
        Mu, _ = build_mu_tilde(struct, hist)
        M = sub_sf_matrix(subs, struct)
        return node_prices(M["DSF"], M["zone"], Mu, hist), M["zone"], struct
    finally:
        g.update(saved)


# =================================================================================================================
# R2 (revision 2): install the structural rows of R1 (gridsite.coverage) and the IMM 2026 anchors (gridsite.rev2) into this module. Keep this the last statement.
# GRIDSITE_REVISION=1 in the environment leaves the module as revision 1 had it.
# =================================================================================================================
from . import rev2 as _rev2  # noqa: E402

_rev2.install()
