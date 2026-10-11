"""Phase 1c commands: geography (OSM / HIFLD / EIA / Census downloads and ERCOT substation placement).

  run_grid.py fetch-geo [osm|eia|geo|hifld|all]   download raw geography into research-data/grid/raw/{osm,eia,geo}
  run_grid.py match-geo [--fresh]                 match ERCOT subs to coordinates, build lines, write data/*.csv + geojson
"""
from . import fetch_geo, geomatch

COMMANDS = {
    "fetch-geo": fetch_geo.fetch_geo,
    "match-geo": geomatch.match_geo,
}
