"""Phase 7 commands (agent R1, regional coverage): measured summer basis, shift factors for the structural / regional constraints, regional candidate sites.

  run_grid.py regional-basis
        data/regional_basis.csv (+ labels): per settlement point of the 60-day day-ahead disclosure (1 Jul - 11 Aug 2026), the basis against its hub, its load zone and
        HB_BUSAVG, hours above $10 and $50, the top-3 hours of the day, and the observed 2 h day-ahead value at the node against its zone; data/regional_basis_summary.csv by
        region and reference.
  run_grid.py sf-structural [skip-validation]
        derived/sf_structural.parquet (station, zone and hub rows: sub, bus, level, cid, sf, sf_pole, se, method, quality, dist_km, n_support), data/sf_structural.csv
        (substation-level pairs), sf_structural_constraints.csv (one row per retained pattern in the schema of constraint_synth.csv, plus evidence), sf_structural_targets.csv (the 14
        constraints of the brief: identified or not, grade, reason), sf_structural_components.csv (all 32 patterns), sf_structural_validation.csv (about 2 min of refits),
        imm_2026_anchors.csv (IMM 2026 year-to-date top-10 lists against Phase 3b's annual targets) and the two merged inputs for Phase 3b in
        research-data/grid/derived/_p7_cache/ (sf_sub_plus_structural, zone_sf_plus_structural).
  run_grid.py regional-candidates
        data/regional_candidates.csv (up to 5 screened substations per region and side, plus the 40 existing candidates with their new exposures) and
        data/regional_upgrade_reach.csv (which of the ten top upgrades move at least one site). Needs the output of sf-structural.

Run from docs/research/grid with the research venv. Nothing here writes to the dispatch lake or edits an earlier phase's file. See notes/phase7-coverage.md.
"""
from __future__ import annotations


def cmd_regional_basis(*args):
    from . import coverage as C

    pts, summary = C.regional_basis()
    print(f"wrote {C.DATA / 'regional_basis.csv'} ({len(pts)} settlement points) and regional_basis_summary.csv ({len(summary)} regions), with labels")
    cols = ["region", "reference", "n_nodes", "basis_mean", "pooled_p05", "pooled_p95", "share_hours_gt10", "share_hours_gt50",
            "worst_hour_cpt", "worst_hour_mean", "best_hour_cpt", "best_hour_mean", "dam_uplift_mean", "verdict"]
    print(summary[cols].to_string(index=False))


def cmd_sf_structural(*args):
    from . import coverage as C

    C.run_sf_structural(*args)


def cmd_regional_candidates(*args):
    from . import coverage as C

    C.run_regional_candidates(*args)


COMMANDS = {
    "regional-basis": cmd_regional_basis,
    "sf-structural": cmd_sf_structural,
    "regional-candidates": cmd_regional_candidates,
}
