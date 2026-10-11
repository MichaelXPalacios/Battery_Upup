"""Phase 1b commands: planning documents, interconnection queue, constraints, unit costs.

  fetch-docs [key ...]   download the public planning/queue/rules documents into research-data/grid/raw/docs
                         (`fetch-docs list` prints the keys, `fetch-docs rpg` downloads this year's RPG decks)
  parse-docs [table ...] rebuild data/planned_projects.csv, queue_storage.csv, large_loads.csv, constraints_annual.csv,
                         gtc.csv and unit_costs.csv (+ .labels.csv sidecars); no argument = all six
"""
from .docs_parse import parse_docs
from .fetch_docs import fetch_docs

COMMANDS = {"fetch-docs": fetch_docs, "parse-docs": parse_docs}
