# Phase 8 (R2): revision 2, the geography and coverage fixes carried through the whole study

Agent R2, 11 Oct 2026. Takes G1's geography fixes (`notes/phase7-geo.md`) and R1's regional coverage (`notes/phase7-coverage.md`) through risk flags, Phase 3b (candidates, lanes, dispatch, forward years, portfolio, sites), Phase 4 (upgrades), Phase 6 (rerank, Tier 2 values, map, report) and the checks, and re-anchors the congestion structure on the market monitor's 2026 rents.
Code: `gridsite/rev2.py`, `anchor_sens.py`, `report_tables.py`, `cli_8.py` (new) and the edits listed in section 2. Data: `data/constraint_structure_rev2.csv`, `anchor_2026_applied.csv`, `site_candidates_rev1.csv`, `extra_sites.csv`, `upgrade_rerank_extra.csv`, `anchor_sensitivity.csv`, `anchor_sensitivity_summary.csv`, and every table that the re-run changed (section 4). Tests: the six test files of the brief, run in section 7. Report: `README.md`, `map/index.html`. Nothing was committed. No file under `dispatch/` was touched.
Labels as everywhere: REAL, DERIVED, SIM, ASSUMPTION. Everything about a node's price is SIM.

## 0. Deviation from the brief (D1)

The brief (lead decision 2) says the annual target comes from the 2026 IMM anchor wherever `data/imm_2026_anchors.csv` has one. **Two elements have a 2026 rent only in the day-ahead list, and the primary run does not anchor on them: SEA 138/69 kV (`SEA_AAT1`, $33.8M year to date) and MDO–PHR (`MDOPHR99_A`, $31.9M).** Six elements have a real-time rent and are anchored (Samsw–Vensw, Argyle–Lewisville, EASTEX, Lubbock LPLMK–LPLNE, STP–Wap, Mgses–Catsw; the last is then replaced by R1's Far West pattern, see section 3), so five reach a lane.

Why the two day-ahead rents were left out:

1. The lanes are real-time prices (the rebuild is validated against real-time prices), and a day-ahead rent is not a real-time shadow price.
2. 92% of the SEA rent falls in July and August (13.8 and 17.4 of 33.8). Annualising January to August by 12/8 treats a two-month summer pocket as a year-round one.
3. The 60-day disclosure cannot confirm the transformer: no priced node has a shift factor of 0.25 or more to it (the eight stations that do are unpriced). It does show a summer pocket in Bell County. The resource nodes within about 25 km of TMPTV and PGE (the Panda Temple plant and the solar plants around it) trade $3.9 to $7.9 below LZ_NORTH on average, and are more than $10 below it in 133 to 177 of 1,008 hours (`regional_basis.csv`). That is the export side of the pocket; the import side (TMPTV, PGE) has no priced node. R1 attributes the pocket to Bell County–Salado (grade C), which the primary replays, and anchoring the transformer as well may count one pocket twice.
4. Applied literally it puts five sites of the SEA pocket at ranks 6 to 10 of the 40 by screening score, on one pair of months of one day-ahead rent.

It is sensitivity S2 (`GRIDSITE_ANCHORS=2026da`), reported in full in section 5.3: PGE and TMPTV would rise by $41.0 and $37.8 and enter the top 15, and PK and GT would leave it. **Flipping it in the primary is one environment variable and a re-run of `candidates`, `lanes`, `dispatch`, `forward`, `portfolio`, `sites`, `rerank`, `tier2-value` and the report, about 90 minutes of compute.** If the lead decides the SEA transformer should count, that is the run to make.

## 1. Findings

1. **The top five did not change** (MIDW $270.4, STRS $250.2, POWELLFI $200.7, KNAPP $98.1, MILES $97.0; revision 1: 269.7, 249.5, 201.5, 98.2, 96.9 [SIM]). Mean 2 h learner at the 40 sites $79.0 (revision 1 $78.3), at the zones $44.4 (unchanged, the control lanes are identical to revision 1: 96 rows, maximum difference 0.0); 38 of the 40 beat their zone (37).
2. **Five sites enter the 40 and five leave** with the selection rule unchanged: ATBR, JUPITER (both placed by G1) and LPLNE, LPLMK, LPLNW (Lubbock Power & Light stations placed by G1 and lifted by the 2026 Lubbock anchor) enter; NOVICE (cap of five Twin Buttes sites), CRNTH and SEA (the export-side balance step) and ECRSW and CENTRVIL (below the new cut-off) leave. The leavers are still valued (`role = rev1_only`) and barely moved ($0.1 to $2.5), so leaving the list is a selection effect, not a loss of value.
3. **The anchor year does not move the top of the list.** With 2025-only anchors (S1) the top 15 is the same, and no site of the top 10 moves by more than $2.6. Only the three Lubbock sites depend on the 2026 anchor (LPLNE −$21.3, LPLMK −$12.2, LPLNW −$8.9 without it). Four sites' top-15 membership depends on the anchor choice, all through the day-ahead rents of D1 (S2): GT, PGE, PK and TMPTV.
4. **Regions.** The Valley's summer congestion is an export discount, not an import premium (17.2% of node-hours more than $10 below the system, 1.1% above), the Panhandle is the same, and a battery at a Valley or Panhandle node shows no uplift over its zone (−$0.39 and −$0.31 per kW over the 42 days). Vealmoor–Koch Tap (U02), Longshore–Consavvy (U07) and Odessa–Yarbrough did not bind in summer 2026. The 41 regional picks, valued with the same code, average a modelled gain of −$0.5/kW-yr (largest +$12.4) against +$33.0 for the 40; the positive ones are mostly Far West import picks whose value rests on Twin Buttes shift factors at stations 183 to 260 km from the line, which the measured prices do not support.
5. **Upgrades.** Seven of the ten top upgrades now move at least one site (revision 1: five): U01, U03, U05, U06, U09 (as before, with new numbers), U08 Bell County–Salado (TMPTV +$1.2; the regional pick GEORSO −$1.4) and U10 Salds–Sonterra (the regional picks JREST −$5.0 and GEORSO −$1.9; none of the 40 by more than $1). U04 WESTEX is evaluated and moves no site (largest $0.13): the shift factor of a West node minus its zone's is near zero. U02 and U07 move nothing because the constraints did not bind. Twin Buttes–Hargrove still dominates: MIDW $247.9 → $50.6, the 40-site total −30.7% (revision 1: −31.2%).
6. **Phase 4's first-order estimate agrees less with the full valuation than before:** baseline rank correlation 0.83 (0.89), 87 (upgrade, site) pairs move by more than $1 in either valuation (76), rank correlation of the change 0.42 (0.46), 14 sign flips (9).
7. **Tier 2.** 167 candidates at 150 buses valued (172): mean DESR gain +$26.4 (+$27.0), +$35.6 within 150 km of the line (+$38.8), +$11.8 in 2029 (+$13.0). 74 of the 167 are more than 150 km from the line their value depends on (79).
8. **Risk.** 148 of 2,884 site-assessed substations are excluded (147 of 2,869). COLLEGE gains a seismic flag (the Irving events of 2015 are within 25 km of Garland); none of the 40 is excluded.
9. **The report is consistent and read-only.** `report-check` passes 84 of 84 checks, recomputes the congestion structure in memory, finds `constraint_synth.csv` and `constraint_structure_rev2.csv` equal to a clean recomputation, and writes nothing (it did rewrite `constraint_synth.csv` and two lane caches before this revision, as G1 found).

## 2. Code changed in earlier phases (all of it, and why)

Run from `docs/research/grid`. Nothing was installed. The existing contracts are in `PLAN.md` ("Revision 2: contracts that changed").

| File | Change | Why |
|---|---|---|
| `gridsite/nodal_lanes.py` | `READ_ONLY` flag (no cache or table is written when true: `hub_history`, `zone_sf`, `constraint_structure`, `mu_tilde`); last statements `from . import rev2 as _rev2` and `_rev2.install()` | report-check must not rewrite data (decision 4); the structure is installed once for every consumer (decision 3) |
| `gridsite/rev2.py` (new) | `install()`: 2026 anchors on `constraint_structure`, `coverage.install_into_nodal_lanes()` (grade D left out unless asked), a dated residual scale for the Valley export pattern (0.50 from 30 Jun 2027, as NELRIO), `bus_sf_matrix` for the structural rows, `planned_upgrades` with the VALEXP row; `anchor_sph_table()` for Phase 4; the switches `GRIDSITE_REVISION`, `GRIDSITE_ANCHORS`, `GRIDSITE_PANHANDLE`, `GRIDSITE_FAR_WEST`; writes `constraint_structure_rev2.csv` and `anchor_2026_applied.csv` | decisions 2 and 3 |
| `gridsite/coverage.py` (R1's) | `structural_replay_rows`, `structural_replaced_cids`, `install_into_nodal_lanes` take `exclude_extra` and `keep_replaced`; the last statements finish a deferred install | the primary leaves out the grade-D Panhandle row; importing `coverage` first made `nodal_lanes` install while `coverage` was half defined (a circular import) |
| `gridsite/sites.py` | `extra_groups`, `extra_subs`, `lane_subs`, `role_of`; `select_candidates` writes the two extra groups after the controls (the 40-site rule itself is untouched); `ensure_lanes`, `site_years_table`, `run_forward` cover all lanes; `write_sites(roles=...)` builds `extra_sites.csv` (R1's measured columns and the distance to the dominant element added); lane tag `rev2`; `sites.csv` is written in queue order (2 h learner, 2021–25, descending) | decision 1; the report says "in queue order" |
| `gridsite/cli_3b.py` | dispatch lanes are controls plus `lane_subs()`, one chunk per worker; the 4 h chunk is split over the workers; `sites` also writes `extra_sites.csv`; `lanes` also writes the two structure tables | same |
| `gridsite/upgrades.py` | `sph_table()` returns `rev2.anchor_sph_table(...)` | the first-order rerank and the lanes share their targets |
| `gridsite/upgrade_rerank.py` | `planned_bundle` applies the VALEXP row's residual by hand; `build_extra()` writes `upgrade_rerank_extra.csv` (the same upgrades on the 46 regional and left-out lanes; only lanes with a non-zero shift-factor difference to a scaled constraint are valued again); the label text of `scn_portfolio200_*` and the docstring say 220 MW (the column names keep the revision-1 figure) | each upgrade's effect on the new groups; the caps now sum to 220 MW |
| `gridsite/risk.py` | `fetch_whp` is incremental (queries the stations without cached samples), like `fetch_fema` | the cache is keyed by `sub`, so a moved station could not be refreshed |
| `gridsite/report_check.py` | read-only guard and snapshot, `check_recomputation`, `check_revision2`, new section list (TL;DR, What changed, 1–10), tables that are generated must appear verbatim, spot-checks updated, rerank rows of U04 and U08, `_sp` for signed percentages, map counts computed | decision 4; the README of revision 2 |
| `gridsite/report_tables.py` (new) | the README tables that are new or rebuilt, as markdown lines generated from the CSVs: anchors, regions, targets, regional picks, churn, sensitivities, upgrade effects | the README is pasted from them and `report-check` finds each line verbatim |
| `gridsite/report_map.py` | regional candidates layer (diamonds), measured basis layer, region summary panel and legends; negative numbers show as −$4.1 rather than $-4.1 | task 4 |
| `gridsite/anchor_sens.py`, `cli_8.py` (new) | `anchor-sensitivity`, `regional-sites` | task 2 |
| `PLAN.md` | contracts of revision 2 | the plan says to update it when a contract changes |

Phase 3a was re-run although it was not on the brief's list, because it reads G1's tables: `costs` (35 of 5,033 rows changed: ATBR and JUPITER gain class confidence; LPLNE and LPLNW become gen-tie classes at 5.2 and 3.8 km, $9.2M and $6.2M, 40.1 and 39.6 months; HKBRY, RRISW and REFORZAR fall back to a default class) and `queue-map` (3 confident mappings removed, 2 added; 26INR0417 moves from WCOLMAIN to FM524).

## 3. Decisions, and where I deviated from the brief

The four lead decisions were applied. Where the brief left a choice, or where applying it literally would have put an unverified number in the headline, the choice and the reason are here, with the switch to undo it.

1. **Candidates (decision 1).** The rule (`sites.select_candidates`: greedy by screening score, at most 5 per dominant element, at least 10 per side) was run on the structure the lanes use, not on revision 1's. G1's dry run (ATBR in, NOVICE out) was made on revision 1's structure; on the revision-2 structure the same rule also swaps JUPITER, LPLMK, LPLNE and LPLNW in and SEA, CRNTH, CENTRVIL and ECRSW out. R1's 41 picks are a separate group (`role = regional`), dispatched and valued with the same code and never ranked with the 40 or put in the portfolio. I added a third group, `rev1_only` (the five revision-1 sites the rule no longer selects), so that "a site left the list" can be compared with "its value fell". Dispatch: 94 lanes (8 controls, 40, 41, 5); 4 h for the top 15 of the 40. The rule recomputed read-only on the structure in force gives exactly the 40 of `sites.csv`.
2. **Anchors (decision 2).** The primary run re-anchors an element's annual shadow-price-hours on its 2026 IMM **real-time** rent (January–August × 12/8, allocated to its contingencies in proportion to their current rent; hours from the same list's binding count, else the 2025 IMM hours, else rent × 8.48 h per $M bounded by the tenth-ranked element of the frequency list; the new basis name is `window_imm2026_rt_rent`, so saturation and the upgrade clock treat it as window-like). Samsw–Vensw rises 31 times, Argyle–Lewisville 1.1, EASTEX 9.5, Lubbock LPLMK–LPLNE 64, STP–Wap 1.7 and Mgses–Catsw 18.6. Elements without a 2026 rent stay on the 2025 IMM rent (Twin Buttes, Scurry Chevron–Knapp, ...), and the rest on the persistence-discounted window. The deviation is section 0. The 2025-only sensitivity is S1.
   **Mgses–Catsw (`6945__A`) is anchored but not used**: R1's Far West import pattern replaces it (shift-factor correlation 0.92), as R1 recommends. Keeping it instead is S4 (`GRIDSITE_FAR_WEST=6945`), coded and screened but not run.
3. **Structural rows (decision 3).** Installed through `coverage.install_into_nodal_lanes()`: `WESTEX`, `NELRIO`, `E_PASP`, `I_FW_N`, `VALEXP` and `BELCNTY-SALSW|UNSEEN` (NELRIO and E_PASP replace their Phase 2 fits; `6945__A|SRGRMGS5` is dropped for `I_FW_N`). `R1_PANHANDLE_P10` (grade D) is in sensitivity S3 only. Two additions that R1's section 6 asked for: the Valley export pattern gets the same dated residual scale as NELRIO (0.50 from 30 June 2027; ASSUMPTION), and the lane validation reads the structural rows' shift factors at substation level.
4. **report-check read-only (decision 4).** `nodal_lanes.READ_ONLY`; `report_check.run` sets it, snapshots every table, the map, the README, the lane and structure caches and the value cache before and after, and fails if anything changed. It recomputes the structure in memory (`check_recomputation`).
5. **Sensitivity method.** The variants use an oracle-ratio estimate (the perfect-foresight value on the rebuilt lane times the lane's own learner/oracle ratio of 2023–25), not a dispatch run, and a lane is recomputed only if the variant changes its first-order exposure by at least $0.25/kW-yr (`FO_MIN_USD`). Against the primary dispatch the method has a mean absolute error of $11.0/kW-yr (it is $6 to $17 above the dispatch value at every site) and a rank correlation of 0.986, so rows are compared with each other, never with the dispatch column. Per variant: `derived/runs/anchor_sens_<variant>.csv` and `_screen.csv`; the combined tables are `data/anchor_sensitivity*.csv`.
6. **Not done.** The 4 h sensitivities; a Tier 2 re-rank under the sensitivities; the portfolio over the regional group; S4. If the 2025 anchors were preferred for the primary, the candidate rule would have to be re-run on that structure (the three Lubbock sites would probably not enter), which the sensitivities do not do.

## 4. What was run, in order

At most 4 workers throughout; no process was left running (checked with `Get-Process`); the one local web server of the map check was stopped and its port closed.

| Step | Command | Time |
|---|---|---|
| Risk refresh (network, throttled, 2 workers) | removed the 63 stale stations from `raw/hazard/fema_nfhl_sites.csv` and `whp_samples_sites.csv` (backups `*.pre_r2`), `risk fetch` (USFS samples for 38 stations, 38 FEMA queries, no errors), `risk build`, `reliability`, `tier2` | 19 s for FEMA; 40 s; 6 s |
| 3a | `costs`, `queue-map` | seconds |
| 3b | `candidates` (refresh), `lanes` | minutes |
| 3b | `dispatch spec=all workers=4`: 2 h on 94 lanes, then the oracle, then 4 h for the controls and the top 15 | 2 h dispatch 1,616 s; everything through `site_years` 2,891 s |
| 3b | `forward spec=all workers=4`, `portfolio`, `sites` | 669 s (2 h) and 856 s (4 h); 4 s; 11 s |
| 4 | `upgrades`, `nwa`, `rerank-first-order` | 22 s each |
| 6 | `rerank workers=4` (12 scenarios on the 40, then on the 46 others) | 13 min |
| 6 | `tier2-value workers=4 max-nodes=150` | 3.5 min |
| 8 | `anchor-sensitivity variants=anchors2025,panhandle,anchors2026da,geo_only workers=4` | 391 s, 47 s, 201 s, 377 s (17 min) |
| 7, 6 | `sites` again (queue order), `regional-candidates`, `map`, `report-check`, the six test files, `python gridsite/check_labels.py` | under 5 min |

The 2 h dispatch re-ran every lane because the structure changed (the runner cache is keyed on the structure hash); the 8 control lanes are identical to revision 1.

## 5. Results

### 5.1 The 40 and the sites that left

`data/sites.csv` (queue order) and Appendix A of the README. The README table "Which sites entered and left the 40" is generated from `site_candidates_rev1.csv`, `sites.csv` and `extra_sites.csv` (`report_tables.churn_table`). Values (2 h learner, 2021–25 mean): ATBR 84.8 (#7), JUPITER 79.4 (#9), LPLNE 62.1 (#25), LPLMK 56.3 (#31), LPLNW 52.4 (#33); leavers NOVICE 83.0 → 85.5, CRNTH 72.5 → 72.8, SEA 52.9 → 51.0, ECRSW 48.3 → 49.9, CENTRVIL 44.3 → 44.3. The 35 sites that stayed moved by −$3.8 (SHRNE, 89.7 → 85.9) to +$2.1 (LYONS) in 2021–25 value, 14 of them by less than $0.5. Half-lives: 9 sites in 2028, 5 in 2029, 26 not by 2031 (the same counts as revision 1).

### 5.2 Anchors

`data/anchor_2026_applied.csv` (one row per re-anchored contingency; the README table is generated from it). EASTEX's January–August rent is 56% April; the other five are spread over the months. No other element changed its basis.

### 5.3 Sensitivities (oracle-ratio footing, 2021–25 mean, $/kW-yr)

| Variant | Sites of the 40 that move by more than $1 | Moves by more than $5 | Rank correlation | Top 15 kept | Enters / leaves |
|---|---|---|---|---|---|
| S0: revision-1 structure on G1's geography | 21 | LPLNE −21.0, LPLMK −12.2, LPLNW −8.8 | 0.98 | 15 | none |
| S1: 2025-only anchors | 14 | LPLNE −21.3, LPLMK −12.2, LPLNW −8.9 | 0.98 | 15 | none |
| S2: also the day-ahead-only rents (D1) | 6 | PGE +41.0, TMPTV +37.8 | 0.85 | 13 | PGE, TMPTV / GT, PK |
| S3: with the grade-D Panhandle pattern | 1 | none | 1.00 | 15 | none |

S0 and S1 differ by at most $1.9 at any site, so R1's six structural rows hardly move the 40 (they matter for the regions). The primary's top 15 on this footing differs from the dispatch top 15 by one site (GT where the dispatch list has BULLCRK), which is the method's error, not an anchor effect; the flags in the README refer to the oracle-ratio list.

### 5.4 Upgrades

`data/upgrade_rerank.csv` (480 rows, 12 scenarios × 40 sites) and `upgrade_rerank_extra.csv` (552 rows, 12 × 46). Sites that move by more than $1: U01 10 of the 40 and 8 of the 46, KNAPP −$32.2; U03 31 and 27, MIDW −$197.3 (NOVICE −$48.6 and CHERRYCR −$24.8 among the others); U04 none; U05 6 and 1, BRENNO −$19.7; U06 4 and 0, MID −$34.2; U08 1 (TMPTV +$1.2) and 1 (GEORSO −$1.4); U09 10 and 6, ROUGH −$5.6 (ECRSW −$7.6); U10 0 and 2 (JREST −$5.0, GEORSO −$1.9); U02 and U07 not replayed. The planned bundle moves no site of the 40 by more than $3.4 by 2031; with the 2032 Argyle–Lewisville project CRNSO and HILND lose $39 each (CRNTH, now outside the 40, −$33.4).
A test invariant changed with the 46 extra lanes and the structural rows: after an upgrade a site on that element may sit up to $0.06 under its zone (CHAPHI after U05), not exactly at or above it.

### 5.5 Regions

Section 7 of the README. R1's finding that "three of the four unevaluable top-10 upgrades did not bind" is two in the data: U02 Vealmoor–Koch Tap and U07 Longshore–Consavvy; the third element, Odessa EHV–Yarbrough, is not a top-10 upgrade, and the other two unevaluated upgrades (U04 WESTEX, U08 Bell County–Salado) left patterns in the prices and are evaluated now. `regional_upgrade_reach.csv` was regenerated by `regional-candidates` after the rerank, so U04 and U08 are now `phase6_full` rather than first order. The 41 picks did not change after the refresh (the picks do not depend on the list of the 40; only R1's "existing" rows do).
Regional lanes against measured basis (2025 rebuilt mean basis against the zone, then measured): Valley export −2.1 / −4.75, Laredo export −3.2 / −6.6, Far West export −2.8 / −1.3, Panhandle export −9.6 / −3.7. The five Far West import picks borrow Twin Buttes shift factors of 0.05 to 0.12 at stations 183 to 260 km from the line, and ASHERTON, FAGUSSLR and MIAM1 do the same at 320 to 457 km (`dominant_distance_km`, `dominant_distance_class` in `extra_sites.csv`). A minimum shift factor of 0.075 for storage saturation instead of 0.05 (`SAT_MIN_SF`), re-run for the five Twin Buttes sites, removes their fade: MIDW $247.8 in 2031, no half-life.

### 5.6 Risk refresh (task 1)

`geo_changes.csv` has 63 stations with stale risk flags, 38 of them site-level in the new placement. The raw caches are keyed by station; I removed the 63 stations' rows (23 of them were cached), made `fetch_whp` incremental and re-queried (FEMA: 38 queries, all answered; USFS: 38 samples). Site-level assessed stations 2,884 (2,869); exclusions 148 (131 flood hazard area, 10 floodway, 7 very high wildfire) against 147 (129, 11, 7). Flood flags: not assessed 2,151, minimal 1,770, unmapped 825, moderate 148, high 141. 65 stations changed a flag (including five county-fallback wildfire changes: CLKPM, FRNKF, MURPH, PLATE, WOOD). COLLEGE: wildfire low → moderate, seismic false → true (6 events of M3 or more within 25 km, largest M3.6, from the Irving cluster of Jan–Feb 2015 and one near Farmers Branch in 2017), flood unchanged. FM524 had been excluded for flood and floodway through a wrong Harris County placement and is now minimal; LPLCO and LPLWA are now excluded for flood (Lubbock). Reliability and Tier 2 were rebuilt: the top 20 is the same set of counties (15 Harris stations, Jones, Brazoria, Jefferson, Taylor, Haskell), the weight sensitivity is unchanged (17 of 20 with equal weights, 8 without the large-load component, 0 with outage data only); Tier 2 exclusions: none 1,559, not assessed 383, flood 72, floodway 8, wildfire 4 (555 OSM-only candidates have no assessment).

## 6. Tests changed, and why

Stale numbers and assumptions only; no test was weakened without a reason.

| Test | Change | Why |
|---|---|---|
| `test_3b.py` `test_an_element_binds_no_longer_than_its_imm_hours` | an `imm2026*` branch: an element anchored on its 2026 rent is bounded by the 2026 hours | the anchors are new |
| `test_3b.py` `test_hours_per_musd_is_rt_only_and_matches_phase4` | the shadow-price-hours comparison skips R1's pole-scale rows and allows 0.1% on re-anchored rows | R1's rows have no Phase 4 counterpart; an element's 2026 rent is split over the contingencies of each table (0.02% apart for Samsw–Vensw) |
| `test_3b.py` `test_lane_file_matches_its_key` | key computed over `lane_subs()` | the lane file holds the 94 lanes |
| `test_4_upgrades.py` `test_sph_methods_and_cap` | the window-cap test excludes `imm2026` rows and a check that re-anchored rows exceed the 2025 floor on purpose is added | the anchors are new |
| `test_6.py` | two upgrades not replayed ({U02, U07}, 80 rows; U04, U08 replayed); MIDW 247.9 → 50.6; the planned bundle's largest move < $3.5; baseline rank correlation 0.80–0.93; Tier 2 rows 2,581; excluded 148; own-ratio check reads `sites.csv` and `extra_sites.csv`; "after an upgrade a site earns at least its zone" now allows $0.25 | numbers of revision 2; NOVICE and CRNTH still have lanes; CHAPHI sits $0.06 under its zone after U05 |
| `test_7_coverage.py` | `test_upgrade_reach...` accepts `phase6_full` for U04 and U08 | they are evaluated now |

`test_7_coverage.py::test_regional_candidates_are_eligible_and_spread_out` failed after the lanes were rebuilt because R1's "existing" rows were the revision-1 40; re-running `regional-candidates` fixed it (no code change). `test_geo_overrides.py` and `test_arena_compat.py` needed nothing.

## 7. Checks

| Check | Result |
|---|---|
| `run_grid.py report-check` | 84 of 84 pass, including "report-check wrote nothing" |
| `tests/test_6.py`, `test_3b.py`, `test_4_upgrades.py`, `test_7_coverage.py`, `test_geo_overrides.py`, `test_arena_compat.py` | 16 of 16, 36 of 36, 16 of 16, 27 of 27, 12 of 12, 18 of 18 |
| `python gridsite/check_labels.py` | 65 of 65 tables |
| Lane validation against the real week 3–10 Oct (published shadow prices) | correlation 0.9935, mean error $1.96/MWh (revision 1: 0.9943, $1.85); 3–5 Oct 0.984, $1.53; day-ahead month 0.965, $1.29 |
| Control lanes against revision 1 | 96 rows, maximum difference 0.0 |
| Candidate rule recomputed read-only on the structure in force | the 40 of `sites.csv`, no difference |
| First-order recomputation against Phase 4's file | 21 candidate rows, maximum difference 0.0005 $/kW-yr |
| Map in the browser (a local server, stopped afterwards) | 10 layers, regional popups with the measured and the modelled column, light and dark chrome, 375 px width without horizontal scroll, no console messages |

## 8. Limits and open items for the lead

- **D1** (section 0) is the one decision that needs a yes or a no.
- The sensitivities are on the oracle-ratio footing (levels $6 to $17 above dispatch); 4 h, Tier 2 and the portfolio were not re-run under them; S4 was not run.
- The three Lubbock sites enter because of the 2026 Lubbock anchor (64 times the replayed 2025 rent on one line). Their gain over the zone is small ($2.7 to $12.4).
- The regional lanes are weaker than the 40: Twin Buttes shift factors far from the line (flagged by `dominant_distance_class`), grade C and D patterns, pole scale for all but E_PASP, one summer of day-ahead prices only.
- `data/upgrade_rerank.csv` column names `scn_portfolio200_*` keep the revision-1 figure; the label text says 220 MW.
- The one mismatch between Phase 4's `sph_table` and the lanes is 0.02% for one contingency of Samsw–Vensw.
- G1's open items stand (28 unplaced stations with contradicting evidence, 34 stations 40 km or more from the county that weaker evidence names, five pairs left over the length limit).
- 9 of 10 upgrades are drawn on the map; U04 (the interface) has no place.

## 9. Files

New: `gridsite/rev2.py`, `anchor_sens.py`, `report_tables.py`, `cli_8.py`; `data/constraint_structure_rev2.csv`, `anchor_2026_applied.csv`, `site_candidates_rev1.csv`, `extra_sites.csv`, `upgrade_rerank_extra.csv`, `anchor_sensitivity.csv`, `anchor_sensitivity_summary.csv` (each with a `.labels.csv`). Re-generated: `site_candidates.csv`, `lane_validation.csv`, `site_years.csv`, `portfolio.csv`, `sites.csv`, `connection_costs.csv`, `queue_sub_map.csv`, `risk_flags.csv`, `reliability.csv`, `tier2.csv`, `tier2_valued.csv`, `upgrades.csv`, `upgrade_options.csv`, `nwa_sizing.csv`, `upgrade_rerank.csv`, `upgrade_rerank_first_order.csv`, `regional_candidates.csv`, `regional_upgrade_reach.csv`, `map/index.html`, `map/index_data.js`. Outside git: `derived/nodal_lanes_rev2.npz`, `derived/runs/*` (ledgers, oracle, forward values, portfolio summary, `*_rev2.log`, `anchor_sens_*.csv`), backups of the two raw hazard caches (`*.pre_r2`).
