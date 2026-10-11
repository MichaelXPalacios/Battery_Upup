"""GATE 1 on the REAL lake, full-size data (opt-in; takes 5 to 10 minutes): DISPATCH_GATE1_REAL=1 pytest tests/test_gate1_real_lake.py

The unit-size Gate 1 lives in test_rebuild.py (a real MIS subset, rebuilt in-process and in a fresh process). This one answers the
question "is the real lake what a clean rebuild from its raw files gives?" without rebuilding all of it:

* copy from the real lake's raw folder only the 2018 and 2019 yearly archives (NP6-785-ER, NP4-180-ER, HIST-DAM-MCPC), the Henry Hub
  file and the weather files, with their catalog lines, into a temp home (the real lake is READ, never written);
* in a fresh interpreter (different PYTHONHASHSEED) parse them and build the offline `rt_v1` features of HB_NORTH for decisions
  1 Jan to 30 Nov 2019 (the 25-hour fall-back day of 3 Nov 2019 is inside; the decision grid starts where the real build's did, so
  the loaded context is the same);
* require every parquet file produced to be byte-identical to the file of the same name in the real lake.

Identical bytes = the real lake's parsed partitions and feature files are a pure function of the raw files.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from dispatch import paths as paths_mod
from dispatch.features import sets

PROJECT = Path(__file__).resolve().parents[1]
REAL_HOME = Path(os.environ.get("DISPATCH_REAL_HOME", str(paths_mod.RESEARCH_DATA / "dispatch")))
YEARLY = ("NP6-785-ER", "NP4-180-ER", "HIST-DAM-MCPC")
BULK = ("rt_spp_hist", "dam_spp_hist", "dam_as_hist", "gas_hh", "weather_obs")

pytestmark = pytest.mark.skipif(os.environ.get("DISPATCH_GATE1_REAL") != "1",
                                reason="opt-in: set DISPATCH_GATE1_REAL=1 (reads the real lake, takes 5-10 minutes)")


def _sha(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def test_real_lake_files_equal_a_clean_rebuild_from_their_raw_files(tmp_path):
    catalog = REAL_HOME / "lake" / "catalog.jsonl"
    if not catalog.exists() or not (REAL_HOME / "features" / "rt_v1" / sets.VERSION / "sp=HB_NORTH" / "part-2019-11.parquet").exists():
        pytest.skip("the real lake has no 2019 features yet (run: python -m dispatch.data.flows adopt / backfill / parse / features)")
    entries = [json.loads(x) for x in catalog.read_text(encoding="utf-8").splitlines() if x.strip()]
    keep = [e for e in entries
            if (e["report_id"] in YEARLY and ("_2018." in e["path"] or "_2019." in e["path"])) or e["report_id"] in ("DHHNGSP", "GHCND", "RNGWHHD")]
    assert len(keep) >= 20, [e["report_id"] for e in keep]
    home = tmp_path / "home"
    for e in keep:
        dst = home / "lake" / e["path"]
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(REAL_HOME / "lake" / e["path"], dst)
    with open(home / "lake" / "catalog.jsonl", "w", encoding="utf-8", newline="\n") as f:
        for e in keep:
            f.write(json.dumps(e, separators=(",", ":"), ensure_ascii=False) + "\n")

    code = chr(10).join([
        "import pandas as pd",
        "from dispatch.data import parse",
        "from dispatch.features import build",
        f"parse.parse_all({list(BULK)!r}, strict=True)",
        "build.write_offline('HB_NORTH', pd.Timestamp('2019-01-01', tz='UTC'), pd.Timestamp('2019-12-01', tz='UTC'), '4h')"])
    env = {**os.environ, "DISPATCH_HOME": str(home), "PYTHONHASHSEED": "12345", "PYTHONIOENCODING": "utf-8"}
    r = subprocess.run([sys.executable, "-I", "-c", "import sys; sys.path.insert(0, %r); exec(%r)" % (str(PROJECT), code)],
                       capture_output=True, text=True, env=env, cwd=str(PROJECT), timeout=3600)
    assert r.returncode == 0, r.stderr[-3000:]

    made = sorted(home.rglob("*.parquet"))
    assert len(made) > 100, len(made)
    feature_files = [p for p in made if "features" in p.relative_to(home).parts[:1]]
    assert len(feature_files) == 11, [p.name for p in feature_files]              # Jan to Nov 2019
    different, missing = [], []
    for p in made:
        real = REAL_HOME / p.relative_to(home)
        if not real.exists():
            missing.append(p.relative_to(home).as_posix())
        elif _sha(real) != _sha(p):
            different.append(p.relative_to(home).as_posix())
    assert not missing, missing[:10]
    assert not different, different[:10]
