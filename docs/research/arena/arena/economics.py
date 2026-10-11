"""Revenue beyond energy arbitrage, and the cost of hosting batteries where the wires are tight.

Three layers sit on top of the dispatch results:
  1. Capacity (NYISO only): locality price x capacity accreditation factor (CAF).
  2. Local programs: payments for being available in a utility's or co-op's peak window. Each program forces the
     battery to be full at the window start and to discharge through the window on event days, so its
     opportunity cost comes out of the simulation instead of being assumed.
  3. Distribution upgrades: program sites need batteries concentrated on particular feeders. On the repo's
     SMART-DS feeder (376 transformers), concentrating one Core per home overloads small transformers; each
     upgrade costs $10,000 (Base to the PUCT) and adds one standard size.
Everything forward-looking is labelled ASSUMPTION unless a source is given.
"""
import csv
import math

import numpy as np

from . import config as C


# ----------------------------------------------------------------------------------- feeder hosting & upgrades
def feeder_upgrade_cost():
    """Cost of hosting one Core in every home on the repo feeder (data/planner/assets.sim.csv).

    Hosting rule: Cores on a transformer <= floor(kVA / 20 kW) (nameplate screen, REAL, PLAN_SCREEN).
    Upgrade: one standard size up (PLAN_UP_SIZES), $10,000 each (PLAN_UPGRADE_USD, REAL).
    Returns a dict with free slots, upgraded slots, upgrade count, and $ per kW of concentrated placement.
    """
    up = {10: 25, 25: 50, 50: 75, 75: 100}
    path = C.REPO / "data" / "planner" / "assets.sim.csv"
    free = need = upgrades = placed = homes = 0
    with open(path, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            kva = int(float(r["kva"]))
            h = int(r["homes_served"] or 0)
            homes += h
            host = math.floor(kva / C.CORE_KW)
            got = min(h, host)
            free += got
            short = h - got
            if short > 0 and kva in up:
                add = math.floor(up[kva] / C.CORE_KW) - host
                n_up = 1 if add > 0 else 0
                extra = min(short, add * n_up)
                need += extra
                upgrades += n_up
                placed += got + extra
            else:
                placed += got
    usd = upgrades * C.UPGRADE_USD
    return {"homes": homes, "free_slots": free, "upgraded_slots": need, "upgrades": upgrades,
            "placed": placed, "share_needing_upgrade": need / max(placed, 1),
            "usd_per_kw_concentrated": usd / max(placed * C.CORE_KW, 1),
            "usd_per_kw_of_upgraded_slots": usd / max(need * C.CORE_KW, 1)}


FEEDER = None


def feeder():
    global FEEDER
    if FEEDER is None:
        FEEDER = feeder_upgrade_cost()
    return FEEDER


def upgrade_usd_per_kw_yr():
    """Concentrated placement: the feeder's upgrade bill spread over every Core placed, annualised (DERIVED)."""
    return feeder()["usd_per_kw_concentrated"] * C.crf()


# ----------------------------------------------------------------------------------- NYISO capacity
NY_LOCALITY = {"N.Y.C.": "NYC", "LONGIL": "LI", "HUD VL": "GJ", "MILLWD": "GJ", "DUNWOD": "GJ"}   # rest: NYCA
# $/kW-month annual average UCAP price by scenario family (ASSUMPTION, anchored on Potomac's 2025/26 spot
# averages: NYC 10.98, NYCA/G-J/LI 4.28, and the May 2026 NYC record of 32.6 - Modo).
NY_CAP_PRICE = {"relief": {"NYC": 12.0, "LI": 5.0, "GJ": 4.0, "NYCA": 3.5},
                "base": {"NYC": 18.0, "LI": 7.0, "GJ": 5.5, "NYCA": 4.5},
                "tight": {"NYC": 25.0, "LI": 10.0, "GJ": 8.0, "NYCA": 7.0}}
# 2-hour storage CAFs 2026/27 with CHPE (REAL: NYC 63.8%, LI 55.1%); G-J and NYCA ASSUMPTION;
# a 1.85-hour home battery is credited 92% of a 2-hour one (ASSUMPTION).
NY_CAF_2H = {"NYC": 0.638, "LI": 0.551, "GJ": 0.60, "NYCA": 0.55}
HOME_CAF_FACTOR = 0.92


def ny_capacity_usd_kw_yr(zone, family, spec_key):
    loc = NY_LOCALITY.get(zone, "NYCA")
    caf = NY_CAF_2H[loc] * (HOME_CAF_FACTOR if spec_key == "home" else 1.0)
    return NY_CAP_PRICE[family][loc] * 12 * caf


# ----------------------------------------------------------------------------------- local programs
# Each program: zones, $/kW-yr payment, event-day rule, window (interval indices), first year, chance it exists,
# MW cap across the fleet, whether sites must be concentrated (upgrade cost), whether we keep merchant revenue,
# and an event energy payment ($/MWh on top of the price) where one exists.
PROGRAMS = [
    {"id": "TX co-op peak", "market": "ERCOT", "zones": ["LZ_LCRA", "LZ_RAYBN"], "usd_kw_yr": 50.0,
     "months": [6, 7, 8, 9], "weekdays_only": True, "events": 20, "window": (60, 72), "start": 2027, "p_exists": 1.0,
     "cap_mw": 40, "concentrated": False, "merchant": True, "event_usd_mwh": 0.0,
     "source": "ASSUMPTION: half of Austin Energy's up-to $8.50/kW-month (City of Austin RCA, 2026); co-ops already "
               "buy capacity from Base (GVEC, CoServ); 4CP-style 3-6 pm window on the 20 hottest weekdays"},
    {"id": "Austin Energy tolling", "market": "ERCOT", "zones": ["LZ_AEN"], "usd_kw_yr": 102.0, "months": [],
     "weekdays_only": False, "events": 0, "window": None, "start": 2027, "p_exists": 0.5, "cap_mw": 20,
     "concentrated": False, "merchant": False, "event_usd_mwh": 0.0,
     "source": "DERIVED: up to $4.08M/yr for 40 MW = $8.50/kW-month (City of Austin RCA 26-1526); the utility "
               "dispatches, so no merchant revenue; a new tranche is uncertain (50%)"},
    {"id": "SB 415 feeder contract", "market": "ERCOT", "zones": ["LZ_NORTH", "LZ_HOUSTON"], "usd_kw_yr": 50.0,
     "months": [6, 7, 8, 9], "weekdays_only": False, "events": 15, "window": (68, 84), "start": 2028, "p_exists": 0.5,
     "cap_mw": 50, "concentrated": True, "merchant": True, "event_usd_mwh": 0.0,
     "source": "ASSUMPTION: rule proposed Apr 2026 (16 TAC 25.58), 100 MW statewide; payment inside the $43-60/kW-yr "
               "deferral range (PA PUC 2026); 5-9 pm on the 15 hottest days"},
    {"id": "Data-center flexibility", "market": "ERCOT", "zones": ["LZ_NORTH"], "usd_kw_yr": 60.0,
     "months": [5, 6, 7, 8, 9], "weekdays_only": False, "events": 30, "window": (56, 80), "start": 2028,
     "p_exists": 0.35, "cap_mw": 80, "concentrated": True, "merchant": True, "event_usd_mwh": 0.0,
     "source": "ASSUMPTION: modelled on the Piq Energy / Base study (80 MW of home batteries for a hypothetical "
               "100 MW data centre near Burleson, Oncor); 2-8 pm on the 30 hottest days"},
    {"id": "Con Ed load relief", "market": "NYISO", "zones": ["N.Y.C."], "usd_kw_yr": 90.0, "months": [5, 6, 7, 8, 9],
     "weekdays_only": True, "events": 10, "window": (56, 72), "start": 2027, "p_exists": 1.0, "cap_mw": 100,
     "concentrated": True, "merchant": True, "event_usd_mwh": 1000.0,
     "source": "REAL rates: CSRP/DLRP $18/kW-month (Tier 1) x 5 months + $1/kWh event energy (Con Ed 2025); "
               "ASSUMPTION: 10 events, 2-6 pm"},
    {"id": "Con Ed Westchester relief", "market": "NYISO", "zones": ["MILLWD", "DUNWOD"], "usd_kw_yr": 30.0,
     "months": [5, 6, 7, 8, 9], "weekdays_only": True, "events": 10, "window": (56, 72), "start": 2027,
     "p_exists": 1.0, "cap_mw": 50, "concentrated": True, "merchant": True, "event_usd_mwh": 1000.0,
     "source": "REAL rates: CSRP $6/kW-month in Westchester x 5 months + $1/kWh; ASSUMPTION: 10 events, 2-6 pm"},
    {"id": "Utility battery program", "market": "NYISO",
     "zones": ["WEST", "GENESE", "CENTRL", "NORTH", "MHK VL", "CAPITL", "HUD VL", "LONGIL"], "usd_kw_yr": 50.0,
     "months": [6, 7, 8, 9], "weekdays_only": True, "events": 10, "window": (56, 76), "start": 2027,
     "p_exists": 1.0, "cap_mw": 100, "concentrated": False, "merchant": True, "event_usd_mwh": 0.0,
     "source": "REAL rate: $50/kW-yr bring-your-own-battery (National Grid, NYSEG/RG&E 2026); PSEG-LI's "
               "aggregator-set payment assumed equal; ASSUMPTION: 10 events, 2-7 pm"},
]


def programs_for(market_name):
    return [p for p in PROGRAMS if p["market"] == market_name]


def pick_events(tmax_year, month_year, weekend_year, prog):
    """Event days for one program-year: the program's n hottest qualifying days (by local Tmax)."""
    if not prog["events"] or prog["window"] is None:
        return np.zeros(len(tmax_year), bool)
    ok = np.isin(month_year, prog["months"])
    if prog["weekdays_only"]:
        ok &= ~weekend_year
    idx = np.where(ok)[0]
    if len(idx) == 0:
        return np.zeros(len(tmax_year), bool)
    top = idx[np.argsort(-tmax_year[idx])[:prog["events"]]]
    mask = np.zeros(len(tmax_year), bool)
    mask[top] = True
    return mask
