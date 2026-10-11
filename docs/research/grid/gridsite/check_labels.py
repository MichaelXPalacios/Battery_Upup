"""Label check: every data/*.csv must have a data/<name>.labels.csv sidecar that covers every column with a valid label.

A sidecar is valid when
  * it has the columns column, label, source, note (in that order),
  * every column of the CSV appears in it exactly once,
  * every label is one of REAL, DERIVED, SIM, ASSUMPTION (PLAN.md),
  * it has no row for a column that the CSV does not have (a stale row is reported as a problem too).

Run from docs/research/grid:   python gridsite/check_labels.py [--quiet]      or     run_grid.py report-check
Exit status 0 = every table passes, 1 = at least one table has a problem (the list says which file and what).
Only the header of each CSV is read, so it takes a second. Standard library plus pandas only (no gridsite imports), so it also runs from any folder.
"""
from __future__ import annotations

import csv
import sys
from pathlib import Path

DATA = Path(__file__).resolve().parents[1] / "data"
VALID_LABELS = ("REAL", "DERIVED", "SIM", "ASSUMPTION")      # REAL: PLAN.md "Join keys and conventions"
SIDECAR_COLUMNS = ["column", "label", "source", "note"]       # REAL: PLAN.md "Layout"


def _header(path: Path) -> list[str]:
    with open(path, newline="", encoding="utf-8-sig") as f:
        return next(csv.reader(f), [])


def check_table(csv_path: Path) -> dict:
    """Problems for one table: dict(file, n_columns, sidecar, missing, duplicate, stale, invalid_label, empty_label, bad_header, ok)."""
    side = csv_path.with_name(csv_path.stem + ".labels.csv")
    cols = _header(csv_path)
    res = dict(file=csv_path.name, n_columns=len(cols), sidecar=side.exists(), missing=[], duplicate=[], stale=[], invalid_label=[], empty_label=[], bad_header=False,
               duplicate_csv_columns=[c for c in set(cols) if cols.count(c) > 1])
    if not side.exists():
        res["ok"] = False
        return res
    with open(side, newline="", encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    hdr = _header(side)
    res["bad_header"] = hdr[:4] != SIDECAR_COLUMNS
    seen: dict[str, str] = {}
    for r in rows:
        c = r.get("column", "")
        if c in seen:
            res["duplicate"].append(c)
        seen[c] = (r.get("label") or "").strip()
    cset = set(cols)
    res["missing"] = [c for c in cols if c not in seen]
    res["stale"] = [c for c in seen if c not in cset]
    res["empty_label"] = [c for c in cols if c in seen and seen[c] == ""]
    res["invalid_label"] = [(c, seen[c]) for c in cols if c in seen and seen[c] != "" and seen[c] not in VALID_LABELS]
    res["ok"] = not (res["missing"] or res["duplicate"] or res["stale"] or res["invalid_label"] or res["empty_label"] or res["bad_header"] or res["duplicate_csv_columns"])
    return res


def check_all(data_dir: Path = DATA) -> list[dict]:
    files = sorted(p for p in data_dir.glob("*.csv") if not p.name.endswith(".labels.csv"))
    return [check_table(p) for p in files]


def orphan_sidecars(data_dir: Path = DATA) -> list[str]:
    """Sidecars whose table does not exist."""
    return sorted(p.name for p in data_dir.glob("*.labels.csv") if not p.with_name(p.name[: -len(".labels.csv")] + ".csv").exists())


def report(results: list[dict], quiet: bool = False) -> int:
    bad = [r for r in results if not r["ok"]]
    if not quiet:
        w = max(len(r["file"]) for r in results) if results else 10
        print(f"{'table':{w}s}  cols  sidecar  status")
        for r in results:
            print(f"{r['file']:{w}s}  {r['n_columns']:4d}  {'yes' if r['sidecar'] else 'NO ':7s}  {'ok' if r['ok'] else 'PROBLEM'}")
    for r in bad:
        print(f"-- {r['file']}")
        if not r["sidecar"]:
            print("   no .labels.csv sidecar")
            continue
        for k in ("missing", "duplicate", "stale", "invalid_label", "empty_label", "duplicate_csv_columns"):
            if r[k]:
                print(f"   {k}: {r[k][:8]}{' ...' if len(r[k]) > 8 else ''} ({len(r[k])})")
        if r["bad_header"]:
            print(f"   header of the sidecar is not {SIDECAR_COLUMNS}")
    orph = orphan_sidecars()
    if orph:
        print("sidecars without a table:", orph)
    print(f"{len(results) - len(bad)} of {len(results)} tables pass" + (f"; {len(bad)} with problems" if bad else "; all sidecars cover every column with a valid label"))
    return 1 if bad else 0


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    return report(check_all(), quiet="--quiet" in argv)


if __name__ == "__main__":
    raise SystemExit(main())
