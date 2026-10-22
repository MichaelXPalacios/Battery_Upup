"""Runtime locations. Everything here lives outside git under DISPATCH_HOME (see DESIGN.md, Storage)."""
import os
from pathlib import Path

PKG_DIR = Path(__file__).resolve().parent                 # hugging-base/dispatch/dispatch
PROJECT_DIR = PKG_DIR.parent                              # hugging-base/dispatch
REPO = PROJECT_DIR.parent                                 # hugging-base
ARENA_DIR = REPO / "docs" / "research" / "arena"
GRID_DIR = REPO / "docs" / "research" / "grid"

RESEARCH_DATA = Path(os.environ.get("ARENA_DATA", str(REPO.parent / "research-data")))   # default: beside the repo
HOME = Path(os.environ.get("DISPATCH_HOME", str(RESEARCH_DATA / "dispatch")))
LAKE = HOME / "lake"
RAW = LAKE / "raw"
PARSED = LAKE / "parsed"
CATALOG = LAKE / "catalog.jsonl"
FEATURES = HOME / "features"
MLFLOW = HOME / "mlflow"
RUNTIME = HOME / "runtime"
AUDIT = RUNTIME / "audit"
LEDGER = RUNTIME / "ledger"
OWNER = HOME / "owner"
STAGING = HOME / "staging"          # downloads awaiting adoption into the lake (e.g. ercot-60day disclosures)

# Research inputs the platform may read (never write)
GRID_RAW_ERCOT = RESEARCH_DATA / "grid" / "raw" / "ercot"     # ~10k MIS files fetched by research agent 1a
GRID_MANIFEST = RESEARCH_DATA / "grid" / "manifest.csv"
GRID_DERIVED = RESEARCH_DATA / "grid" / "derived"
ZONAL_HISTORY = RESEARCH_DATA / "ercot"                      # NP6-785-ER yearly workbooks/csv, 2011-2026
WEATHER_HISTORY = RESEARCH_DATA / "weather"                  # NOAA GHCN-Daily stations used by the arena

REPORTS = PROJECT_DIR / "reports"
NOTES = PROJECT_DIR / "notes"


def ensure():
    for p in (RAW, PARSED, FEATURES, MLFLOW, AUDIT, LEDGER, OWNER, STAGING):
        p.mkdir(parents=True, exist_ok=True)


def use_arena():
    """Make `import arena` work for cross-checks against the research DP oracle."""
    import sys
    if str(ARENA_DIR) not in sys.path:
        sys.path.insert(0, str(ARENA_DIR))
