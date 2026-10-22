"""Shared locations. Raw and large files live outside git in research-data/grid; small tables live in the repo."""
import os
import sys
from pathlib import Path

GRID_DIR = Path(__file__).resolve().parents[1]                      # docs/research/grid
REPO = GRID_DIR.parents[2]                                           # hugging-base
ARENA_DIR = REPO / "docs" / "research" / "arena"
RESEARCH_DATA = Path(os.environ.get("ARENA_DATA", str(REPO.parent / "research-data")))   # default: beside the repo
ROOT = RESEARCH_DATA / "grid"
RAW = ROOT / "raw"
DERIVED = ROOT / "derived"
MANIFEST = ROOT / "manifest.csv"
REPO_DATA = GRID_DIR / "data"
NOTES = GRID_DIR / "notes"
MAP = GRID_DIR / "map"
ARCHIVE_ERCOT = REPO / "previous-work/simulators/rz/research/evidence/research-20260925/ercot-live-20260925"
SP_MAPPING_SEP2026 = ARCHIVE_ERCOT / "n1-np4-160/SP_List_EB_Mapping/Settlement_Points_09022026_133322.csv"

USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) research (hugging-base grid siting)"

for _p in (RAW, DERIVED, REPO_DATA, NOTES, MAP):
    _p.mkdir(parents=True, exist_ok=True)


def use_arena():
    """Make `import arena` work (the arena package lives in docs/research/arena/arena)."""
    if str(ARENA_DIR) not in sys.path:
        sys.path.insert(0, str(ARENA_DIR))
