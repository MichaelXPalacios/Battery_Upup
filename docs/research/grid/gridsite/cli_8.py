"""Revision 2 commands (agent R2).

  run_grid.py anchor-sensitivity [variants=anchors2025,anchors2026da,panhandle,fw6945] [workers=4] [combine-only]
        valued again, in a fresh process each, under the switches of gridsite.rev2 (oracle-ratio method, labelled): data/anchor_sensitivity.csv (one row per lane, every variant,
        ranks among the 40, top-15 membership) and data/anchor_sensitivity_summary.csv (one row per variant). Run after the primary dispatch.
  run_grid.py regional-sites
        data/extra_sites.csv again from the tables on disk (the `sites` command writes it too).

Run from docs/research/grid with the research venv. See notes/phase8-revision2.md.
"""
from __future__ import annotations


def _kv(args):
    kv, fl = {}, set()
    for a in args:
        if "=" in a:
            k, v = a.split("=", 1)
            kv[k.strip()] = v.strip()
        else:
            fl.add(a.strip())
    return kv, fl


def cmd_anchor_sensitivity(*args):
    from . import anchor_sens as A

    kv, fl = _kv(args)
    variants = [v for v in kv.get("variants", ",".join(A.VARIANTS)).split(",") if v]
    if "combine-only" not in fl:
        A.run_variants(variants, int(kv.get("workers", 4)))
    out = A.combine()
    import pandas as pd

    pd.set_option("display.width", 250)
    pd.set_option("display.max_columns", 30)
    print(pd.read_csv(A.SUMMARY_CSV).drop(columns=["description"]).to_string(index=False))
    print(f"{len(out)} lanes -> {A.OUT_CSV.name}, {A.SUMMARY_CSV.name}")


def cmd_regional_sites(*args):
    from . import sites as S

    ex = S.write_sites(roles=S.EXTRA_ROLES)
    print(f"extra_sites: {len(ex)} rows ({ex['role'].value_counts().to_dict()}) -> {S.EXTRA_SITES_CSV.name}")


COMMANDS = {"anchor-sensitivity": cmd_anchor_sensitivity, "regional-sites": cmd_regional_sites}
