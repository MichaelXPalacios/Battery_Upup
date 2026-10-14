"""Phase 4 commands: transmission upgrades, non-wires (battery) alternatives and the first-order rerank.

  run_grid.py upgrades [check]      data/upgrades.csv and data/upgrade_options.csv (+ .labels.csv): the elements (top 15 by priority, every 2025 IMM
                                    top-10 element, the best local window constraints), the wires options with cost formulas, lead times, planned
                                    projects, battery sizing summary and who decides. `check` recomputes the derived constants and compares them with the
                                    named ones (capture cover, window persistence, TPIT lead times, fallback shift factors, battery cost).
  run_grid.py nwa                   data/nwa_sizing.csv (+ labels): battery MW / MWh that would relieve each element, 3 relief x 3 coverage scenarios,
                                    both sides, with the cost against the wires fix.
  run_grid.py rerank-first-order    data/upgrade_rerank_first_order.csv (+ labels): per upgrade and spec (2h / 4h) the sites that lose congestion value and
                                    the sites that move up, first order, no dispatch.

Interface for Phase 3b and the final report (import gridsite.upgrades):
  apply_upgrade(cid_list, residual=None, expand_element=True) -> {cid: mu_scale}, planned_schedule(), basis_half_life(v_by_cid, ...)

Run from docs/research/grid with the research venv. Reads data/constraints.csv, unit_costs.csv, planned_projects.csv, lines.csv, substations.csv,
shift_factors.csv, settlement_points.csv and derived/{sf_long,shadow_sced,shadow_dam}.parquet; the TPIT workbook in raw/docs for lead times.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from . import upgrades as U


def _fmt(v, nd=1):
    if v is None or (isinstance(v, float) and not np.isfinite(v)):
        return "-"
    if isinstance(v, (int, np.integer)):
        return str(v)
    if isinstance(v, (float, np.floating)):
        return f"{v:,.{nd}f}"
    return str(v)


def _table(df: pd.DataFrame, cols: list[str], nd: dict | None = None, maxw: int = 34) -> None:
    nd = nd or {}
    cell = [[(_fmt(r[c], nd.get(c, 1)) if not isinstance(r[c], str) else r[c][:maxw]) for c in cols] for _, r in df.iterrows()]
    w = [max(len(c), *(len(row[i]) for row in cell)) if cell else len(c) for i, c in enumerate(cols)]
    print("  " + "  ".join(c.rjust(w[i]) for i, c in enumerate(cols)))
    for row in cell:
        print("  " + "  ".join(row[i].rjust(w[i]) for i in range(len(cols))))


def cmd_upgrades(*args):
    if "check" in args:
        U.run_check()
        return
    up = U.run_upgrades(*args)
    print(f"wrote {U.OUT_UPGRADES} ({len(up)} rows), {U.OUT_OPTIONS} and their labels")
    sel = up["selection"].str.split("+")
    print(f"elements: {len(up)}; priority_top {sum('priority_top' in x for x in sel)}, imm_structural {sum('imm_structural' in x for x in sel)}, "
          f"window_top {sum('window_top' in x for x in sel)}; planned yes/likely {int(up['planned'].isin(['yes', 'likely']).sum())}")
    v = up.assign(len_mi=up["length_mi"], lead=up["lead_time_mid_months"])
    _table(v, ["upgrade_id", "element", "upgrade_type", "cost_mid_musd", "lead", "planned", "planned_isd", "nwa_central_mw", "nwa_central_mwh",
               "nwa_central_hours", "nwa_vs_wires_ratio", "nwa_vs_cheapest_ratio"],
           {"cost_mid_musd": 1, "lead": 0, "nwa_central_mw": 0, "nwa_central_mwh": 0, "nwa_central_hours": 1, "nwa_vs_wires_ratio": 1, "nwa_vs_cheapest_ratio": 0})
    print("  nwa_* = 10% relief, 80% coverage, best side; ratio = battery mid cost / wires mid cost (above 1: the battery costs more)")


def cmd_nwa(*args):
    nwa = U.run_nwa(*args)
    print(f"wrote {U.OUT_NWA} ({len(nwa)} rows) and its labels")
    b = U.nwa_best_side(nwa)
    cen = b[np.isclose(b["relief_pct"], U.CENTRAL_RELIEF) & np.isclose(b["coverage_pct"], U.CENTRAL_COVERAGE)]
    _table(cen, ["upgrade_id", "element", "side", "relief_mw", "episode_hours", "battery_hours", "best_site", "best_site_sf", "sf_basis", "mw_installed", "mwh_installed",
                 "battery_cost_mid_musd", "wires_primary_mid_musd", "ratio_vs_primary_mid", "coverage_achieved"],
           {"relief_mw": 0, "episode_hours": 1, "battery_hours": 1, "best_site_sf": 2, "mw_installed": 0, "mwh_installed": 0, "battery_cost_mid_musd": 0,
            "wires_primary_mid_musd": 1, "ratio_vs_primary_mid": 1, "coverage_achieved": 2})
    cheaper = b[(b["ratio_vs_primary_mid"] < 1) & b["duration_capped"].eq(False)]
    print(f"scenarios (best side) where the battery mid cost is below the primary wires mid cost and the coverage target is met: {len(cheaper)} of {len(b)}")
    if len(cheaper):
        print(cheaper.groupby("element").size().to_string())


def cmd_rerank(*args):
    rr = U.run_rerank(*args)
    print(f"wrote {U.OUT_RERANK} ({len(rr)} rows) and its labels")
    x = rr[(rr["spec"] == "2h") & (rr["order"] <= 3) & rr["group"].isin(["loser", "riser", "not_rankable", "riser_none"])]
    for sc, g in x.groupby("scenario", sort=False):
        print(f"-- {sc} {g['element'].iloc[0][:60]}")
        for grp, h in g.groupby("group", sort=False):
            if grp in ("not_rankable", "riser_none"):
                print(f"   {grp}: {h['note'].iloc[0]}")
                continue
            for _, r in h.iterrows():
                print(f"   {grp:6s} {r['sub']:9s} {r['zone']:10s} {str(r['side']):6s} v0 {r['v0_usd_kw_yr']:7.1f} -> v1 {r['v1_usd_kw_yr']:7.1f} $/kW-yr  rank {int(r['rank0'])} -> {int(r['rank1'])}")


COMMANDS = {
    "upgrades": cmd_upgrades,
    "nwa": cmd_nwa,
    "rerank-first-order": cmd_rerank,
}
