# Gates (self-checks by the lead)

The user asked for all phases to run without stopping, so the lead runs each gate as a self-check. Each gate is
recorded here, and the final report repeats both.

## Gate A: after Phase 1 (10 Oct 2026). PASS, with limits

**1a, market data.** The lead re-read the parquets.
- `lmp_bus`: 31.1M rows, 19,493 buses, 5–10 Oct.
- `shadow_sced`: 24,742 rows, 152 distinct constraints, 3–10 Oct, plus the 25 Sep archive day.
- DAM files (bus LMPs, shadow prices, SPPs) cover 10 Sep to 10 Oct.
- The SCED joins between `lmp_bus`, `lambda` and `shadow_sced` are 100%.
- With no constraint binding, LMP minus lambda is under $0.005 at every bus.

**1b, planning.** The lead re-read the tables.
- `queue_storage` holds 877 storage-type rows, about 158 GW.
- `constraints_annual` keeps its markets separate. RT and RT_ERCOT_CN duplicate elements, so never sum across them.
- `unit_costs` comes from MISO MTEP24, in 2024 dollars.

**1c, geography.** The lead spot-checked these against known places, and all are correct:
- ELMOT → McLennan
- BLESSING → Matagorda
- VEALMOOR → Borden
- TWINBU → Tom Green
- HARGROVE → Irion
- SCRCV and KNAPP → Scurry

Coverage:
- 72% of NP4-160 substations are placed.
- 90% of substations with resource nodes are placed, 71% at confidence ≥ 0.5.

**Limits carried forward:**
1. **No ERCOT API key.** Real-time nodal history is one week and DAM history 31 days.
   - The week is dominated by outages and local 138 kV constraints. The structural constraints (WESTEX, Vealmoor–Koch
     Tap) are absent from it.
   - Annual rents come from the market monitor, not from interval data.
   - Multi-year nodal value must therefore be reconstructed (SIM/DERIVED), not measured.
2. **The network graph has no ratings or impedances.** Any physics proxy uses named typical values per kV (ASSUMPTION).
3. **The MISO unit-cost cells read from garbled text are UNVERIFIED.**

## Gate B: after Phase 2 (10 Oct 2026). PASS for Phase 3, with the limits below

**Checks by the lead.**

| Check | Result |
|---|---|
| RT shift-factor fit, held out | `data/sf_validation.csv`: median out-of-sample R² **0.978** over 19,199 buses; 97.8% are above 0.5 |
| DAM shift-factor fit, held out | Median R² 0.756 |
| Sign convention, recomputed from raw parquets for the most-binding RT constraint (`6437__F`, 530 binding SCED runs) | The 30 most negative-SF buses average congestion **+$315.84/MWh** and the 30 most positive-SF buses **−$111.12/MWh**. This matches PLAN.md: SF < 0 is the import side, where price rises. |

**Coverage of the 1,386 constraints.**
- 496 have regression SFs, 476 have only the physics proxy, and 414 have nothing.
- Of the top 80 by priority, 71 have regression SFs.
- The physics proxy is poor: its median correlation with the regression SFs is 0.38. All proxy SFs are therefore marked low confidence, and Phase 3 must not rank sites on proxy magnitudes.

**Limits carried into Phase 3.**
1. Use `rt_reg` SFs first. Treat `dam_reg` SFs tagged `+shrunk` as sign-only. Never rank on `dcpf_proxy` magnitudes.
2. **Structural constraints are under-represented.** 5 of the 10 largest 2025 rents never priced in the window, and 2
   (Twin Buttes–Hargrove, WESTEX) priced only day-ahead. The nodal value rebuild must weight the structural IMM rents,
   not only this week.
3. **Placement errors:** 20 constraint rows have their two ends more than 80 km apart, so both ends were dropped. The
   list is in `notes/phase2.md` §8 for a later geography pass.
4. **Planned upgrades matched (top 10):**

   | Element | Project | In service |
   |---|---|---|
   | Bell County–Salado | TPIT-87673 | May 2027 |
   | Longshore–Consavvy | TPIT-81268 | May 2028 |
   | SALDS–Sonterra | TPIT-80546D | May 2027 |
   | WESTEX | PBRP 765 kV | 2031 |
   | Twin Buttes–Hargrove | Identified in the RTP | No date |
   | Scurry Chevron–Knapp | None | — |

## Phases 3–6 checks (11 Oct 2026). PASS, with limits

Recorded by agent 6 for the lead. Agent 6 re-ran the quick test suites of the earlier phases on 11 Oct (nothing was recomputed or edited), checked the numbers it quotes from each phase against the tables on disk (the README spot-checks and `notes/phase6.md` list them), and ran the Phase 6 checks. The longer 3a suites (runner, NodalMarket, AS layer, costs, queue map; about 13 minutes) were not re-run: their results below are the ones in `notes/phase3a.md`. The sign-off of this section belongs to the lead.

| Phase | Check | Result |
|---|---|---|
| 3a | `tests/test_arena_compat.py` re-run | **18/18** in 3 s. The group-cap hook is bit-identical to the backup arena over 960 `allocate` and 720 `portfolio` randomised cases (every rule, with and without `avail` and `caps`); no arena file other than `siting.py` differs from `_backup_docs_research_20261010` |
| 3a | From `notes/phase3a.md` (not re-run) | A `NodalMarket` of the 8 zone lanes reproduces the arena's ledgers exactly over all of 2024 (366 days); chunked runner results equal the single-process ones; the AS layer matches closed forms; queue MW mapped at confidence 0.5 or more: 89.9% of queued storage |
| 3b | `tests/test_3b.py` re-run | **36/36** in 60 s, no skips. `value_sites` at 2025 reproduces `sites.csv fwd_learner_2025_replay` to 5e-5 $/kW-yr; the forward table, the portfolio inputs and `sites.csv` agree with fresh computations |
| 3b | Lane validation (`data/lane_validation.csv`) | The formula with the published shadow prices rebuilds the real node basis of 3–10 Oct at correlation 0.994 and mean error $1.85/MWh (0.984 and $1.52 before the fit window; day-ahead 0.969 and $1.14). The 8 zone controls equal the arena's zone prices exactly. Two independent reviews: the first build was rebuilt after defects were found, and the second review matched the rebuilt numbers |
| 3b | Sensitivity | Rank correlation of 14 sites' 2025 gain with the base 0.90 to 1.00 over six alternative SIM choices; the mean gain moves −16% to +25%; the screening score ranks a stratified pool sample at 0.94 |
| 4 | `tests/test_4_upgrades.py` re-run | **16/16** in 16 s. The Phase 6 recomputation of the first-order values reproduces the rows of `upgrade_rerank_first_order.csv` for the candidates it lists (22 rows) to 0.0005 $/kW-yr |
| 5 | From `notes/phase5.md` | Two consecutive full rebuilds from cached downloads gave identical files (sha256); every sidecar covers exactly its table's columns |
| 6 | `tests/test_6.py` | **16/16** in 14 s: scenarios and planned-bundle rules, rerank invariants, Tier 2 invariants and distance columns, map payload and page, `node --check` of the page script, the label checker on three broken tables, the command registry |
| 6 | `run_grid.py report-check` | **58/58**: sections, TL;DR (7 bullets), the 32-item table, the top-15 table and Appendix A against `sites.csv` field by field, the rerank / disagreement / Tier 2 tables against their CSVs, 15 spot-checks of numbers in the text (listed in `notes/phase6.md` section 4), invariants of the two new tables, the map |
| 6 | `python gridsite/check_labels.py` | **46/46 tables** have a sidecar covering every column with REAL / DERIVED / SIM / ASSUMPTION; no stale rows, no orphan sidecars. No earlier phase's table needed a fix |
| 6 | Map in the browser pane (over HTTP) | Leaflet 1.9.4 and its integrity hashes load; no script error; all 40 site popups and the upgrade, constraint, excluded, reliability and Tier 2 popups render; every layer toggles; light and dark palettes; no horizontal scroll at 558 px. **Not seen:** a real `file://` open and a real phone width (the pane cannot do either) |
| 6 | Nothing earlier was changed | The files modified since the start of Phase 6 are only the ones it owns (`README.md`, `data/upgrade_rerank*`, `data/tier2_valued*`, `map/index*`, `notes/phase6.md`, this section, `gridsite/{report_map,report_check,check_labels,cli_6,upgrade_rerank,tier2_value}.py`, `tests/test_6.py`); the value cache kept all 441 earlier entries unchanged |

**Limits carried into the final report.**
1. **Node prices are SIM.** Levels are good to about a factor of two and rankings are steadier; the timing of the synthetic shadow prices (46% of the replayed constraints use a geographic rule) is not validated; one real-time week and 31 day-ahead days are all the data there is without an API key.
2. **Only 260 of 1,386 constraints are replayed.** Four of the ten top upgrades (Vealmoor–Koch Tap, WESTEX, Longshore–Consavvy, Bell County–Salado) cannot be evaluated by the full rerank, and no Panhandle, Valley or Far West site exists.
3. **The Twin Buttes pocket** (the top three sites, and 94 of the 172 valued Tier 2 candidates) rests on collinear day-ahead shift factors; its saturation fade is a cliff in `SAT_MIN_SF`.
4. **Tier 2 values 150 buses**; 2,331 candidates with a usable shift factor carry the zone value (labelled), and 79 of the 172 valued ones are more than 150 km from the line their value depends on (flagged, not corrected).
5. **Reliability** is county-level (EAGLE-I) with 39 unusable counties; the top 20 changes with the weights.
6. **Not independently verified:** the regulatory citations (taken from `notes/rules.md` and `notes/phase4.md`), the MISO unit-cost cells flagged UNVERIFIED, the $16.95M per 100 MW step-up proxy (88% of connection cost), and the README's forward table rows for 2026, 2028 and 2030, which come from `notes/phase3.md`.

### Lead sign-off of Phases 3–6 (11 Oct 2026)

**Accepted, with the limits above.** Two independent checks by the lead:

**1. Zone controls.**
- The 8 control lanes carry the arena's zone prices exactly, as agent 6 recorded.
- Their dispatch values differ slightly from the arena's own backtest (`arena_cache/backtest_ERCOT_grid.npz`). The learner's 2021–25 means:

  | Zone | 3b control | Arena | Difference |
  |---|---|---|---|
  | LZ_WEST | 49.74 | 49.64 | +0.10 |
  | LZ_NORTH | 41.50 | 41.48 | +0.02 |
  | LZ_HOUSTON | 47.16 | 46.78 | +0.38 |

- The cause is the runner's lane-safe analog context (`notes/phase3a.md` caveat 1; `context="arena"` reproduces the arena exactly).
- **The node-vs-zone uplift therefore compares like with like (both lanes use the same context), to within about $0.4/kW-yr.**

**2. Map under `file://`.**
- `map/index.html` loads its data as a classic `<script src="index_data.js">` (no `fetch`), so it works opened as a local file.
- Leaflet (cdnjs, SRI-pinned) and the OSM tiles need a network connection.
- A real phone check is still pending.

## Revision 2 checks (11 Oct 2026). PASS, with one deviation for the lead to confirm

Agent R2 (`notes/phase8-revision2.md`). The geography fixes (G1), the regional coverage (R1) and a re-anchoring on the market monitor's 2026 rents were carried through risk flags, Phase 3b, Phase 4, Phase 6, the map and the report. Run from `docs/research/grid`.

| Check | Result |
|---|---|
| Risk flags refreshed for the 38 moved stations (network: FEMA and USFS point queries, throttled), reliability and Tier 2 rebuilt | 38 of 38 answered, no errors; 148 of 2,884 excluded (147 of 2,869); none of the 40 excluded |
| Control lanes against revision 1 (`site_years.csv`, 96 rows, 12 value columns) | maximum difference 0.0 |
| Lane rebuild against the real week 3–10 Oct, published shadow prices | correlation 0.9935, mean error $1.96/MWh (revision 1: 0.9943, $1.85) |
| Candidate rule recomputed read-only on the structure in force | the 40 sites of `sites.csv`; ATBR, JUPITER, LPLNE, LPLMK, LPLNW in, NOVICE, CRNTH, SEA, ECRSW, CENTRVIL out |
| `report-check` | 84 of 84; recomputes `constraint_synth.csv` and `constraint_structure_rev2.csv` in memory and finds them equal to the committed tables; **writes nothing** (a before and after snapshot of every table, the map, the README, the lane and structure caches and the value cache) |
| Generated README tables equal their CSVs, row by row | anchors, regions, targets, regional picks (two tables), churn, sensitivities (two), upgrade effects |
| Tests | `test_6` 16 of 16, `test_3b` 36 of 36, `test_4_upgrades` 16 of 16, `test_7_coverage` 27 of 27, `test_geo_overrides` 12 of 12, `test_arena_compat` 18 of 18 (stale numbers fixed: listed in `notes/phase8-revision2.md` section 6) |
| `python gridsite/check_labels.py` | 65 of 65 tables |
| Map in a browser (local server, stopped afterwards) | regional candidates and measured basis layers, popups show measured and modelled values side by side, light and dark, 375 px without horizontal scroll, no console messages |
| No process left running, at most 4 workers, nothing committed | confirmed with `Get-Process` |

**To confirm (D1).** The primary run does not anchor on the two day-ahead-only 2026 rents (SEA 138/69 kV $33.8M, MDO–PHR $31.9M); the brief said to use the 2026 anchor wherever one exists. Reasons and the sensitivity (S2) are in `notes/phase8-revision2.md` section 0; flipping it is `GRIDSITE_ANCHORS=2026da` and about 90 minutes of re-running. With it, PGE and TMPTV enter the top 15 (+$41 and +$38) and PK and GT leave.

**Limits carried.** The sensitivities use the oracle-ratio method (levels $6 to $17 above dispatch, rank correlation 0.986); 4 h, Tier 2 and the portfolio were not re-run under them; S4 (keep Mgses–Catsw) was not run. The regional lanes are weaker than the 40 (Twin Buttes shift factors far from the line; grade C and D patterns; one summer of day-ahead prices).
