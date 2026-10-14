"""Phase 5 commands: reliability, physical risk and Tier 2 (distribution).

  run_grid.py fetch-reliability [eaglei|oncor|osm|voll|all] [force]
                                   EAGLE-I Texas (ORNL, CC BY 4.0), Oncor SAIFI/SAIDI xlsx (PUCT 56005), OSM critical facilities,
                                   into research-data/grid/raw/reliability/. Every download is in manifest.csv.
  run_grid.py risk [fetch|build|all] [force]
                                   FEMA NFHL, USFS wildfire hazard, NOAA Storm Events, USGS ComCat, OSM coastline into raw/hazard/,
                                   then data/risk_flags.csv
  run_grid.py reliability [fresh]
                                   data/eaglei_county.csv, critical_facilities.csv, reliability.csv (+ .labels.csv sidecars)
  run_grid.py tier2                data/tier2.csv (run reliability and risk first)
"""
from . import reliability, risk, tier2

COMMANDS = {
    "fetch-reliability": reliability.fetch_reliability,
    "risk": risk.risk,
    "reliability": reliability.reliability,
    "tier2": tier2.tier2,
}
