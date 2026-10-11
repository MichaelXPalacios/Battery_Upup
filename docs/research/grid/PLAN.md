# Grid siting: working plan and contracts (Phase 0)

This is the build plan for [PROMPT.md](PROMPT.md), written 10 Oct 2026. Every agent reads it before starting work.
If you change a contract, update this file.

## Data access, checked 10 Oct 2026

- **ERCOT MIS public reports need no key.** `https://www.ercot.com/misapp/GetReports.do?reportTypeId=<id>` lists
  files, and each one downloads from `https://www.ercot.com/misdownload/servlets/mirDownload?doclookupId=<id>`. They
  are only kept for a short rolling window. For example, NP6-905-CD (report type 12301) goes back about 7 days
  (3 Oct to 10 Oct 2026). Use a browser-like User-Agent.
- **The ERCOT Public API needs credentials.** `https://api.ercot.com/api/public-reports/...` returns 401 without a
  subscription key, and the archives (2023 to 2026) are only reachable through it.
  - The downloader reads these environment variables if they are set: `ERCOT_API_USERNAME`, `ERCOT_API_PASSWORD`,
    `ERCOT_API_SUBSCRIPTION_KEY`.
  - Never print, log or commit those values.
  - Until the user registers, run on the MIS window plus what we already have.
- **Download limit.** ERCOT allows 3 downloads of the same report per 12 months through the API. Every download goes in
  the manifest. Check the manifest before downloading anything again.

## Layout

### Raw data, outside git: `C:/Users/Javier/Repo/BaseP/research-data/grid/`

| Path | What goes there |
|---|---|
| `raw/ercot/<REPORT_ID>/` | ERCOT files exactly as downloaded (zips). Example: `raw/ercot/NP6-905-CD/` |
| `raw/docs/` | Planning PDFs and xlsx files: TPIT, RTP, Constraints and Needs, GIS report, SOM, large loads |
| `raw/osm/`, `raw/eia/`, `raw/hazard/`, `raw/reliability/` | Geography, assets, hazards and outage history |
| `derived/` | Large parquet intermediates, such as `lmp_bus.parquet`, `spp_node.parquet`, `shadow_sced.parquet`, `lambda.parquet` and `shadow_dam.parquet` |
| `manifest.csv` | Every download, with columns `source,report_id,url,file,bytes,sha256,downloaded_at_utc,method`. `method` is `mis`, `api` or `web`. Append only. |
| `_backup_docs_research_20261010/` | A copy of `docs/research/` taken before this work. Don't touch it. |

### In the repo, small and reviewable: `docs/research/grid/`

| Path | What goes there |
|---|---|
| `gridsite/` | The Python package. Each module has one owner (see the table below). |
| `gridsite/paths.py` | Shared paths: `RAW`, `DERIVED` and `MANIFEST` under `research-data/grid`, plus `REPO_DATA = docs/research/grid/data`. |
| `gridsite/manifest.py` | `record(...)`, `already_have(url_or_docid)` and `sha256(path)`. |
| `run_grid.py` | The command line. It auto-loads `gridsite/cli_<phase>.py` modules, each exporting `COMMANDS = {name: fn}`. Never edit `run_grid.py` itself. |
| `data/*.csv` | Committed tables. Each table has a sidecar `<name>.labels.csv` with columns `column,label,source,note`, where `label` is one of `REAL`, `DERIVED`, `SIM` or `ASSUMPTION`. |
| `notes/*.md` | Per-phase findings with sources, for example `phase1-market.md`. |
| `README.md` | The final report (Phase 6). |

Run everything with `C:/Users/Javier/Repo/BaseP/research-data/.venv/Scripts/python.exe`. It is Python 3.14 with numpy,
pandas, pyarrow, networkx, scipy, scikit-learn, requests and openpyxl. **Don't pip install anything else without saying
so in your notes.**

### Running the code

Run from `docs/research/grid/`, so `import gridsite` works. To import the arena, add
`hugging-base/docs/research/arena` to `sys.path`; it exposes the `arena` package. The arena reads `ARENA_DATA`, which
defaults to `research-data`.

## Join keys and conventions

**Substation key.** `sub` is the NP4-160 `SUBSTATION` code, for example `BUBORA`. Every substation-level table carries
it.

**Bus key.** `bus` is the NP4-160 `ELECTRICAL_BUS`. The settlement-point keys are `RESOURCE_NODE`, `HUB` and
`SETTLEMENT_LOAD_ZONE`.

**Constraint key.** `cid` is `CONSTRAINT_NAME|CONTINGENCY_NAME`, as published in NP6-86-CD and NP4-191. The constraint
table also carries:
- `from_station`, `to_station`, `kv`;
- `element_type`: one of `line`, `xfmr` (autotransformer), `gtc` or `other`.

**Time.**
- Store `interval_start_utc`, plus `date_cpt` and `i15` (0–95, the 15-minute interval of the Central Prevailing Time day).
- DST days are normalised to 96 intervals the same way `arena/arena/data.py` does it: the repeated hour is dropped, and a
  skipped hour repeats the hour before.
- 5-minute SCED data is averaged to 15 minutes for dispatch. Keep the 5-minute data for the shift-factor fit.

**Prices and sign.**
- Prices are in $/MWh.
- ERCOT LMPs have no loss component, so the congestion component of bus `b` at time `t` is
  `cong[b,t] = LMP[b,t] - lambda[t]`.
- Shift-factor sign convention: `cong[b,t] = - sum_k SF[b,k] * mu[k,t]`, where `mu` is the published shadow price
  (positive when binding). Report it this way and say it in the notes.

**Units.** Value is in $/kW-yr, the arena's unit. Margin is in $/MWh discharged. Batteries are 2 h (the existing
`grid` spec, 2 MWh/MW) and 4 h (a new spec).

**Constants.** Unverified constants are single named values in UPPER_CASE at the top of the module that uses them. Each
gets a one-line comment giving its label and source. Never inline a literal.

## Phases and owners

| Phase | Agent | Owns (writes) | Needs |
|---|---|---|---|
| 1a | ERCOT market data | `gridsite/paths.py`, `manifest.py`, `fetch_ercot.py`; `derived/*.parquet`; `data/settlement_points.csv`; `notes/phase1-market.md` | — |
| 1b | Planning, queue, rules | `gridsite/fetch_docs.py`, `docs_parse.py`; `data/planned_projects.csv`, `data/queue_storage.csv`, `data/large_loads.csv`, `data/constraints_annual.csv`, `data/gtc.csv`; `notes/phase1-planning.md`, `notes/rules.md` | — |
| 1c | Geography | `gridsite/fetch_geo.py`, `geomatch.py`; `data/substations.csv` (sub, lat, lon, kv_max, county, zone, match_method, match_conf), `data/lines.csv`; `notes/phase1-geo.md` | 1a mapping file; it can start from the Sep 2026 copy |
| 2 | Network and shift factors | `gridsite/network.py`, `shiftfactors.py`; `data/constraints.csv`, `data/shift_factors.csv`, `data/hops.csv`, `data/sf_validation.csv`, `map/substations.html`; `notes/phase2.md` | 1a, 1b, 1c |
| 3 | Site value (Tier 1) | `gridsite/sites.py`, `nodal_lanes.py`, `ancillary.py`, `saturation.py`; backward-compatible hooks in `arena`; `data/sites.csv`; `notes/phase3.md` | 2 |
| 4 | Upgrades | `gridsite/upgrades.py`; `data/upgrades.csv`; `notes/phase4.md` | 2 and 1b; can run alongside 3 |
| 5 | Reliability and Tier 2 | `gridsite/reliability.py`, `tier2.py`; `data/reliability.csv`, `data/tier2.csv`; `notes/phase5.md` | 1c; can run alongside 2–4 |
| 6 | Report | `README.md`, `map/index.html`, `gridsite/cli_6.py` | all |

`run_grid.py` is shared and never edited. Each phase registers its commands in its own `gridsite/cli_<phase>.py` (for example `cli_1a.py`, `cli_2.py`).

## What we reuse

- **Arena.** `Market` from `arena/data.py`, `run_combined` from `arena/algorithms.py`, `allocate` and `portfolio` from
  `arena/siting.py`, `kappa_schedule` and `Futures` from `arena/forward.py`, and `SPECS` from `arena/config.py`. Any
  hook added must leave the old results unchanged.
- **Grid mapping files.** `previous-work/simulators/rz/research/evidence/research-20260925/ercot-live-20260925/`:
  - the Sep 2026 NP4-160 mapping under `n1-np4-160/SP_List_EB_Mapping/`;
  - one day of NP6-86-CD in `n1-np686-raw/`;
  - the parser idea in `previous-work/simulators/rz/research/site/ems/n1-build.py`.
- **Distribution logic.** `sim/planner.py` (hosting capacity and upgrade ranking) for Tier 2.
- **Context.** `docs/research/ercot.md` for GTCs, top constraints and queue context.

## Gates

The user asked for all phases to run without stopping. Gates A and B therefore become self-checks by the lead, and
each one is recorded in `notes/gates.md`. The user still sees both in the final report.

## Revision 2 (11 Oct 2026): contracts that changed

Written by agent R2 after G1 (geography) and R1 (regional coverage). Details in `notes/phase7-geo.md`, `notes/phase7-coverage.md`, `notes/phase8-revision2.md`.

- **The congestion structure is installed, not edited.** `gridsite/nodal_lanes.py` ends with `rev2.install()` (`gridsite/rev2.py`). It (1) re-anchors the annual shadow-price-hours target of every replayed element that has a 2026 IMM real-time rent
  (`data/imm_2026_anchors.csv`, year-to-date x 12/8), (2) installs R1's structural rows through `coverage.install_into_nodal_lanes()` (grade D, the Panhandle pattern, left out), (3) gives the Valley export pattern a dated residual scale and
  (4) patches `bus_sf_matrix` for the new rows. Environment switches (read at import): `GRIDSITE_REVISION=1` (revision-1 behaviour), `GRIDSITE_ANCHORS=2025|2026da`, `GRIDSITE_PANHANDLE=1`, `GRIDSITE_FAR_WEST=6945`.
  `data/constraint_synth.csv` stays the Phase 3b table; the structure the lanes use is `data/constraint_structure_rev2.csv`; what the anchors did is `data/anchor_2026_applied.csv`.
- **Lane roles.** `data/site_candidates.csv` has four roles: `candidate` (the 40-site rule, unchanged), `control_zone` (8), `regional` (R1's picks, `data/regional_candidates.csv`) and `rev1_only` (revision-1 sites the rule no longer selects,
  `data/site_candidates_rev1.csv`). The last two are dispatched and valued (`data/extra_sites.csv`, same columns as `sites.csv`) but never ranked with the 40 and are not in the portfolio. Lane file: `derived/nodal_lanes_rev2.npz` (tag `rev2`).
- **New commands** (`gridsite/cli_8.py`): `anchor-sensitivity` (oracle-ratio sensitivities, `data/anchor_sensitivity*.csv`), `regional-sites`.
- **`nodal_lanes.READ_ONLY`.** When true no cache or table is written (a stale cache is recomputed in memory). `report-check` sets it and checks that it wrote nothing.
- **Risk.** `risk fetch` is incremental for the USFS samples as it already was for FEMA (a station is refreshed by deleting its rows from `raw/hazard/*_sites.csv`).
