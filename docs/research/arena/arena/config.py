"""Arena configuration: regions, weather stations, battery specs and every economic assumption.

Every number here carries a label in the repo's style:
  REAL        taken from a published source (cited)
  DERIVED     computed from REAL inputs (formula given)
  ASSUMPTION  our choice; change it and rerun
"""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]                     # docs/research/arena
DATA = Path(os.environ.get("ARENA_DATA", str(ROOT.parents[3] / "research-data")))   # default: beside the repo
CACHE = DATA / "arena_cache"
OUT = ROOT / "results"
REPO = ROOT.parents[2]                                          # hugging-base

# ---------------------------------------------------------------------------------------------
# Regions: one per price location. ERCOT load zones settle home fleets (ADER); NYISO zones A-K.
# ---------------------------------------------------------------------------------------------
ERCOT_ZONES = ["LZ_NORTH", "LZ_HOUSTON", "LZ_SOUTH", "LZ_WEST", "LZ_AEN", "LZ_CPS", "LZ_LCRA", "LZ_RAYBN"]
ERCOT_REF = "HB_BUSAVG"            # ERCOT-wide bus average: the "system" price each zone separates from
NY_ZONES = ["WEST", "GENESE", "CENTRL", "NORTH", "MHK VL", "CAPITL", "HUD VL", "MILLWD", "DUNWOD", "N.Y.C.", "LONGIL"]
NY_LETTER = dict(zip(NY_ZONES, "ABCDEFGHIJK"))
NY_REF = "CENTRL"                  # Zone C, the upstate reference used in the NYISO study

LABEL = {
    "LZ_NORTH": "DFW (LZ_NORTH)", "LZ_HOUSTON": "Houston", "LZ_SOUTH": "South / Valley", "LZ_WEST": "West / Permian",
    "LZ_AEN": "Austin Energy", "LZ_CPS": "San Antonio (CPS)", "LZ_LCRA": "LCRA co-ops", "LZ_RAYBN": "Rayburn co-ops",
    "WEST": "NY West (A)", "GENESE": "Genesee (B)", "CENTRL": "Central NY (C)", "NORTH": "North Country (D)",
    "MHK VL": "Mohawk Valley (E)", "CAPITL": "Capital (F)", "HUD VL": "Hudson Valley (G)", "MILLWD": "Millwood (H)",
    "DUNWOD": "Dunwoodie (I)", "N.Y.C.": "New York City (J)", "LONGIL": "Long Island (K)",
}

# NOAA GHCN-Daily stations (keyless NCEI service). Local weather per zone; a load-weighted system index per market.
# ASSUMPTION: station choice and weights (rough load shares); airports chosen for complete records.
ERCOT_STATIONS = {
    "LZ_NORTH": ["USW00003927"],                 # DFW Airport
    "LZ_HOUSTON": ["USW00012960"],               # Houston Intercontinental
    "LZ_SOUTH": ["USW00012924", "USW00012919"],  # Corpus Christi, Brownsville
    "LZ_WEST": ["USW00023023", "USW00013962"],   # Midland, Abilene
    "LZ_AEN": ["USW00013904"],                   # Austin-Bergstrom
    "LZ_CPS": ["USW00012921"],                   # San Antonio
    "LZ_LCRA": ["USW00013904", "USW00012921"],   # Central Texas
    "LZ_RAYBN": ["USW00003927"],                 # NE of Dallas: DFW is the nearest first-order station
}
ERCOT_SYSTEM_WEIGHTS = {"USW00003927": 0.36, "USW00012960": 0.30, "USW00012921": 0.12, "USW00013904": 0.10,
                        "USW00012924": 0.06, "USW00023023": 0.06}
NY_STATIONS = {
    "WEST": ["USW00014733"], "GENESE": ["USW00014768"], "CENTRL": ["USW00014771"], "NORTH": ["USW00094725"],
    "MHK VL": ["USW00014735"], "CAPITL": ["USW00014735"], "HUD VL": ["USW00014757"], "MILLWD": ["USW00094745"],
    "DUNWOD": ["USW00094745"], "N.Y.C.": ["USW00094728", "USW00014732"], "LONGIL": ["USW00004781"],
}
NY_SYSTEM_WEIGHTS = {"USW00094728": 0.36, "USW00004781": 0.14, "USW00094745": 0.10, "USW00014757": 0.06,
                     "USW00014735": 0.10, "USW00014771": 0.10, "USW00014733": 0.10, "USW00094725": 0.04}

# ---------------------------------------------------------------------------------------------
# Battery specs (per MW of inverter power). Energy is tracked in units of U MWh of stored energy.
# ---------------------------------------------------------------------------------------------
U = 0.125          # MWh per state unit; a full-power 15-minute interval moves 2 units (0.25 MWh)
DT = 0.25          # hours per interval
NI = 96            # intervals per day

SPECS = {
    # Batter Up's home fleet: Base Core 20 kW / 37 kWh usable (REAL power, ASSUMPTION usable, sim/constants.py),
    # 89% round trip (ASSUMPTION, CORE_RTE), 20% member backup reserve (REAL, RESERVE_FLOOR) -> per MW: 1.85 MWh.
    "home": {"label": "Batter Up home fleet (Core spec, 20% backup reserve)", "energy_units": 15, "reserve_units": 3,
             "rte": 0.89, "cycles_per_day": 4.0, "deg_usd_mwh": 20.0},
    # A grid-scale merchant battery, as in the price studies: 1 MW / 2 MWh, 85% round trip, no reserve.
    "grid": {"label": "Grid-scale merchant (1 MW / 2 MWh)", "energy_units": 16, "reserve_units": 0,
             "rte": 0.85, "cycles_per_day": 4.0, "deg_usd_mwh": 20.0},
}
# deg_usd_mwh: ASSUMPTION, a wear cost of $20 per MWh discharged. It replaces the price studies' hard
#   one-cycle-per-day cap: a hard daily cap that the planner cannot see makes forward-looking algorithms spend
#   the allowance on morning wiggles (tested: a perfect-forecast planner fell to 34-41% of the capped ceiling).
#   With $20/MWh every algorithm and the oracle optimise the same objective; average cycling lands near
#   1.1 cycles/day, about what home-battery warranties assume.
# cycles_per_day: physical sanity limit only (4 full usable cycles a day is never reached in practice).

STORM_RESERVE_UNITS = 8     # ASSUMPTION: the weather algorithm holds ~53% of a home battery before a storm day
STORM_RULES = {             # ASSUMPTION: a day counts as an outage-risk day if any local reading crosses these
    # Wind, flood rain and hard freezes break distribution lines (Beryl, the derecho, Sandy, Isaias, ice storms).
    # Heat is deliberately absent: heat waves are system scarcity events (price spikes) when the battery
    # should be discharging, not holding backup (docs/research/ercot.md section 7).
    "ERCOT": {"wsf2_mph": 45.0, "prcp_in": 3.0, "tmin_f": 20.0, "tmax_f": 999.0},
    "NYISO": {"wsf2_mph": 45.0, "prcp_in": 2.5, "tmin_f": 5.0, "tmax_f": 999.0},
}

# ---------------------------------------------------------------------------------------------
# Market caps used when building future scenarios (REAL): ERCOT system-wide offer cap $5,000/MWh since 2022.
# ---------------------------------------------------------------------------------------------
ERCOT_CAP = 5000.0
NY_CAP = 10000.0            # ASSUMPTION: NYISO has no hard cap on LBMPs; Long Island 5-min peaks reached $7,338 (2025)

# ---------------------------------------------------------------------------------------------
# Backtest and forward windows
# ---------------------------------------------------------------------------------------------
BACKTEST_END = 2025                         # last complete year in both markets
HORIZONS = [1, 3, 5]                        # years
FORWARD_START = 2027                        # forward years 2027-2031
FLEET_MW_PER_YEAR = 10                      # ASSUMPTION: each algorithm places 10 MW a year, 1 MW at a time

# ---------------------------------------------------------------------------------------------
# Distribution layer, from the repo's capacity planner (sim/planner.py) and feeder (data/planner/assets.sim.csv)
# ---------------------------------------------------------------------------------------------
CORE_KW = 20.0                              # REAL, CORE_POWER_KW
UPGRADE_USD = 10000.0                       # REAL, PLAN_UPGRADE_USD (Base to the PUCT, project 54224 item 49)
UPGRADE_LEAD_YEARS = 0.5                    # ASSUMPTION, PLAN_LEAD_MONTHS = 6
DISCOUNT = 0.08                             # ASSUMPTION, PLAN_DISCOUNT
CONTRACT_YEARS = 12                         # REAL, PLAN_CONTRACT_YEARS (Base's battery agreement term)


def crf(rate=DISCOUNT, years=CONTRACT_YEARS):
    """Capital recovery factor: $/yr per $ of upfront cost (DERIVED)."""
    return rate * (1 + rate) ** years / ((1 + rate) ** years - 1)
