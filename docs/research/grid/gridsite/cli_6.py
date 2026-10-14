"""Phase 6 commands: the full upgrade rerank, Tier 2 valuation, the interactive map and the report checks.

  run_grid.py rerank [workers=3] [skip-first-order]
        data/upgrade_rerank.csv (+ labels): the 40 candidate sites valued again with gridsite.sites.value_sites after each of the top-10 upgrades (U01..U10) and
        two bundles of planned upgrades; value, rank, first-order comparison, portfolio effect. About 6 minutes on 3 workers the first time (every lane value is cached in
        derived/runs/value_cache.json, so a rerun takes seconds).
  run_grid.py tier2-value [workers=3] [max-nodes=150] [skip-2029]
        data/tier2_valued.csv (+ labels): Tier 2 candidates with the nodal 2 h value of the DESR route (value_sites, at most max-nodes screened buses), the
        load-zone value of the ADER route, and the deferral value and reliability score as separate columns (never summed). About 4 minutes the first time.
  run_grid.py map [index | substations]
        map/index.html + map/index_data.js (Leaflet, opens as a local file). `map substations` runs the Phase 2 builder (map/substations.html), which also
        answers to a bare `map` in earlier notes: that name now belongs to this phase because the brief asks for it.
  run_grid.py report-check
        spot-checks the numbers quoted in README.md against the CSVs, the 32-item constraint table, the label sidecars and the map size; prints a pass/fail list.

Run from docs/research/grid with the research venv. The label sidecar check alone: python gridsite/check_labels.py
"""
from __future__ import annotations


def cmd_rerank(*args):
    from . import upgrade_rerank as R

    df = R.run(*args)
    s = df.attrs["summaries"]
    print(f"wrote {R.OUT_CSV} ({len(df)} rows) and its labels")
    print("first-order reproduction check:", df.attrs.get("first_order_check"))
    for sid, v in s.items():
        print(f"{sid:13s} {v['effect']:12s} losers {v['n_losers']:2d} gainers {v['n_gainers']:2d}  largest loss {v['largest_loss_site']:9s} {v['largest_loss']:7.1f}  "
              f"sum40 {v['sum40_before']:7.1f}->{v['sum40_after']:7.1f}  portfolio200 {v['port0']:.2f} static {v['port_static']:.2f} reopt {v['port_reopt']:.2f} $M/yr  "
              f"rho(loss) {v['rho_loss']:.2f} rho(rank) {v['rho_rank']:.2f} n={v['n_cmp']}")


def cmd_tier2_value(*args):
    from . import tier2_value as T

    T.run(*args)


def cmd_map(*args):
    if args and args[0] == "substations":
        from . import cli_2

        return cli_2.cmd_map(*args[1:])
    from . import report_map as M

    return M.run(*args)


def cmd_report_check(*args):
    from . import report_check as C

    return C.run(*args)


COMMANDS = {
    "rerank": cmd_rerank,
    "tier2-value": cmd_tier2_value,
    "map": cmd_map,
    "report-check": cmd_report_check,
}
