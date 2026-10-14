# Phase 6: the full upgrade rerank, Tier 2 valuation, the report and the map (11 Oct 2026)

Agent 6. Code: `gridsite/upgrade_rerank.py` (the rerank), `tier2_value.py` (Tier 2), `report_map.py` (the map), `report_check.py` (the report checks), `check_labels.py` (the label check), `cli_6.py` (commands). Tests: `tests/test_6.py` (16 tests, 14 s).
Outputs, each with a `.labels.csv` sidecar (REAL / DERIVED / SIM / ASSUMPTION): `data/upgrade_rerank.csv` (480 rows x 57 columns), `data/tier2_valued.csv` (2,591 rows x 95 columns). Also `README.md` (the report), `map/index.html` and `map/index_data.js` (0.64 MB), this note and the appended section of `notes/gates.md`.
Run from `docs/research/grid` with the research venv. Nothing was installed, downloaded or committed. No earlier phase's code, table or note was edited (a listing of files changed since the start of the session shows only the files above plus `README.md`).
The only derived data touched is `research-data/grid/derived/runs/value_cache.json`, which `value_sites` itself appends to: the 441 entries that existed are unchanged (checked against a backup) and 556 were added. Logs and a scratch check script are in `research-data/grid/derived/runs6/`.

```
run_grid.py rerank [workers=3] [skip-first-order]       # data/upgrade_rerank.csv
run_grid.py tier2-value [workers=3] [max-nodes=150] [skip-2029]   # data/tier2_valued.csv
run_grid.py map [substations]                           # map/index.html + index_data.js; `map substations` is the Phase 2 map
run_grid.py report-check                                # 58 checks; exit status 1 if one fails
python gridsite/check_labels.py                         # label sidecars only
python tests/test_6.py [name-substring ...]
```

## Findings first

The report is `README.md`; these are the results that are new in this phase (all SIM unless stated).

1. **Twin Buttes–Hargrove is the upgrade that matters.** Reconductoring it takes MIDW from $247.7 to $51.0 (rank 1 to 9), STRS from $226.8 to $48.2 and POWELLFI from $178.6 to $43.0, and takes the sum of the 40 sites down 31.2% and the 200 MW the price-taker caps allow down 23.3%. 28 of the 40 sites lose more than $1 (the Scurry–Knapp sites by $11–22 through their second exposure). After it the best five sites are CRNSO, HILND, BG, CRNTH, PK ($57.9–65.4): the Argyle and Houston pockets take over.
2. **PK–DT takes $35 off each of the four Houston sites** (rank 11–15 to 37–40; sum −5.6%); **Scurry Chevron–Knapp** takes $17–33 off five sites (KNAPP −32.6; sum −4.7%); **HIGH36–Brenno** $16–20 off five LCRA sites (−3.6%); **Encrt–Brand and Salds–Sonterra** move nothing material (−0.1%, +0.1%).
3. **Every planned upgrade in service by 2031 together moves no candidate by more than $2.1** (sum +0.2%): 28 projects on 52 replayed constraints, none on a candidate's dominant element. The one dated project that reaches candidates is Argyle–Lewisville (Dec 2032; PLANNED_ALL: HILND, CRNSO, CRNTH lose $34–40; sum −4.2%).
4. **Four of the ten upgrades cannot be evaluated** (Vealmoor–Koch Tap, WESTEX, Longshore–Consavvy, Bell County–Salado): none of their constraints is among the 260 replayed ones (no regression shift factor). The rows exist and say `not_replayed`; this is a limit of the data, not a result. They carry some of the largest 2025 rents ($211M, $89M, $95M, $94M).
5. **First order against full:** baseline rank correlation of the 40 sites 0.89; over the 76 (upgrade, site) pairs that move by more than $1 in either valuation the rank correlation of the change is 0.46 (29 pairs both lose, 28 only the full valuation, 9 only first order, 9 sign flips). First order understates MIDW's loss by 31% (−135.4 against −196.7), overstates MILES's (−101.4 against −59.5), and is blind to second exposures (SCRCV, SHRNE, KNAPP, BULLCRK lose $12–22 under U03, exactly 0 in first order). On the share of the premium that goes the two agree (MIDW 89% in both).
6. **Tier 2:** of 2,125 settlement buses 2,059 have a usable shift factor; the best 150 (172 candidates) were valued: mean DESR value $51.0 against $23.9 for the same battery at the zone price (ADER), a mean gain of +$27.0 (median +$23.0) falling to +$13.0 by 2029. 79 of the 172 are more than 150 km from the line their value comes from (gain +$17.3, against +$38.8 within 150 km): a mis-placed line end for College–Jupiter and Encrt–Brand, day-ahead fits for 58 Twin Buttes lookalikes.

## 1. Rerank method (`gridsite/upgrade_rerank.py`)

- **Scenarios (12).** U01–U10 (`gridsite.upgrades.apply_upgrade("U03")` expands to every contingency of the element, residual 0.10 for a line or transformer, 0.50 for an interface; both ASSUMPTIONs of Phase 4), plus `PLANNED_2031` and `PLANNED_ALL`.
- **Planned bundle.** `planned_bundle(last_year)`: start from `gridsite.saturation.planned_upgrades(struct)` (which is `upgrades.planned_schedule()` at confidence 0.5 or more, restricted to the 260 replayed constraints and to rows with a date: 66 constraints), then apply the two rules of `saturation.upgrade_scale`: a project in service by 31 Dec 2025 is already in the 2025 replay, and for a window-anchored constraint a project in service by 10 Oct 2026 is already in the window data. `PLANNED_2031` keeps projects in service by 31 Dec 2031 (52 constraints, 28 projects); `PLANNED_ALL` adds the two dated 2032 projects (Argyle–Lewisville, D5_V4TP_1; 56 constraints, 30 projects). Each takes its residual in full at once (the "all done" state, not the ramp of the forward run).
- **Valuation.** `gridsite.sites.value_sites(candidates, mu_scale=..., spec="grid", years=(2025,), baseline="none", workers=3)`: the perfect-foresight oracle on the lane rebuilt with the scaled shadow prices x the lane's own learner/oracle ratio of 2023–25 (Phase 3b's method). The baseline reproduces `sites.csv fwd_learner_2025_replay` to 5e-5 $/kW-yr (a test). Zone prices are real and are not scaled, so an upgrade removes only the node-minus-zone part.
- **Ranks** are among the 40 by learner value (1 = best, ties share the lower number). `value_effect` is loses / gains / unchanged against `MATERIAL_USD_KW_YR` = $1/kW-yr (ASSUMPTION; first built at $0.25, which called $0.3 changes "gains"; sites off the upgraded element still move by cents through their zone terms). `rank_effect` is separate because a site can move up without gaining value (others fall).
- **Portfolio effect.** "The 200 MW the caps allow": each constraint group's cap (Phase 3b's `portfolio_groups`: 10% of the median limit, whole 10 MW units, 200 MW in all) placed at its best node (POI cap permitting). `portfolio200_before` is $11.96M a year at 2025 replay values; `static_after` keeps those MW at the same nodes, `reopt_after` re-sites after the upgrade. The two differ by 0.1 point or less except for U01 (−6.7% static, −5.8% re-sited). `sum40` is the plain sum of the 40 learner values (1 kW at each site).
- **First order.** `first_order()` recomputes Phase 4's formula for the 40 sites from `upgrades.value_table()` with the same `apply_upgrade` scales. It reproduces the published `upgrade_rerank_first_order.csv` rows for the candidates it lists (22 rows) to 0.0005 $/kW-yr, so the comparison uses the recomputed values (all 40 sites, not only the ten losers the file lists). `pooled_agreement()` and `disagreements()` give the numbers in the README.
- **Runtime.** 8 evaluated scenarios x 38–43 s (40 lanes, 3 workers) + the four `not_replayed` ones (0–1 s, the cache key is the same scale vector as the baseline) = 322 s; the first-order assembly adds about 20 s; a rerun is 5 s because every lane value is cached. The parent process held about 700 MB and each worker 46 MB; free RAM stayed above 1.1 GB.

## 2. Tier 2 method (`gridsite/tier2_value.py`)

- **Settlement bus.** `route_desr_nodal_sub` of `tier2.csv` (the candidate's own highest-voltage bus, or the nearest placed substation for OSM-only candidates): 2,125 unique substations, all with an arena zone.
- **Screen.** `sub_sf_matrix` gives the substation shift factors to the 260 replayed constraints (rt_reg, or dam_reg not attenuated; implausible read as 0). Usable = largest |SF| of 0.10 or more (`SF_MIN_USABLE`, Phase 3b's `SF_MIN_ELIGIBLE`): 2,059 buses (2,503 candidates). Score = sum over constraints of |SF − SF_zone| x shadow-price-hours (Phase 3b's score). The best `MAX_NODES` = 150 buses are valued. Score cut-off $99,674 per MW-year of exposure; Phase 3b's calibration on its own pool puts the mean 2025 gain below the 90th percentile of the score under $7/kW-yr.
- **Valuation.** `value_sites(zones + 150 buses, years=(2025, 2029), baseline="both")`, 2 h: the learner ratio of a bus that is not a Phase 3b candidate is its zone control's (`nodal_ratio_basis` says which: 36 candidates "own", 136 "zone_control"). 2029 applies the planned upgrades and the storage saturation of 2029 (the year the pocket premium has halved in Phase 3b); the zone's own value in 2029 is the same as in 2025 (zone prices stay at 2025).
- **Columns.** All 77 columns of `tier2.csv` come first, unchanged (reliability, deferral, home-fleet, hosting, risk). The four nodal columns Phase 5 left empty are filled (`nodal_value_usd_kw_yr`, `nodal_vs_zone_uplift_usd_kw_yr`, `nodal_value_source`); `nodal_value_p10_usd_kw_yr` stays empty on purpose (a rebuilt node lane has no distribution of futures; the forward case is `nodal_value_2029_usd_kw_yr`). New columns: screen (`nodal_screen_*`), dominant element, side, SF and method, `nodal_group_cap_mw` (10% of the element's limit: the room is shared with Tier 1), `nodal_zone`, `nodal_ratio_basis`, 2029 value and gain, `ader_zone_value_usd_kw_yr`, `desr_minus_ader_usd_kw_yr`, `nodal_value_screened`, and the distance caution below.
- **Sources of the value.** `nodal_value_source`: valued own ratio 36, valued zone ratio 136, `zone_value_below_screen_cap` 2,331 (usable shift factor, outside the best 150), `zone_value_no_usable_sf` 88. For the unscreened rows the nodal column is the zone value and the gain is 0: it is not a finding that there is no premium.
- **Separate columns.** Market value (`nodal_*`, `ader_*`, `home_fleet_*`), deferral value (`deferral_usd_kw_yr_low/mid/high`, REAL) and reliability (`reliability_need_score`, `need_rank`, `voll_usd_yr_*`) are different columns; a test checks they equal `tier2.csv` and that no column name adds them.
- **Distance caution (found while checking the results).** 58 of the 94 valued candidates behind Twin Buttes–Hargrove are more than 150 km from the line (up to 519 km: Dallas, Matagorda, Nueces, Burleson...). `nodal_distance_to_element_km` and `nodal_distance_class` (`within_150km`, `beyond_150km`, `element_not_placed`; `FAR_KM` = 150 [ASSUMPTION]) flag them. The value is not changed. Causes: for Twin Buttes the day-ahead fits (variance inflation above 100); for College–Jupiter (10 candidates) COLLEGE, the one placed end, sits in Brazos County while the sites behind the line are in Dallas County; for Encrt–Brand (4) the BRAND end was matched to BRAND_C by a 0.45-confidence prefix alias. Of the 172 valued: 76 within 150 km (gain +$38.8, +$16.1 in 2029), 79 beyond (+$17.3, +$9.0), 17 with the line not placed (+$19.6, +$17.9).
- **Runtime.** 233 s for 150 nodes x 2 years (300 lane-years, 3 workers); 5 s from the cache.

## 3. The map (`gridsite/report_map.py`)

`map/index.html` is a template in the module; `map/index_data.js` (`window.GRID_REPORT_MAP = {...}`, 0.58 MB) comes in through a `<script>` tag, so the page opens as a local file. Leaflet 1.9.4 from cdnjs with subresource-integrity hashes (the sha512 of `leaflet.min.js` and `.css`, computed from the cdnjs files on 11 Oct 2026), OpenStreetMap tiles with attribution, Hanken Grotesk from Google Fonts with a system fallback (the repo's design tokens: cream and forest green, flat cards, tabular figures; a dark palette under `prefers-color-scheme: dark` because the design system defines none), a 16 px gutter, `overflow-x: hidden`, a stacked layout under 760 px and a Hide/Show panel button.

| Layer | What | Default |
|---|---|---|
| Candidate sites (40) | circle size = 2 h learner value (4 + 0.8 x sqrt(value) px), colour = gain over the zone in six classes; popup = key results by section, the effect of each upgrade on the site, and all 150 fields with their labels | on |
| Priority constraint lines | top 60 priority cids collapsed to 52 elements; 31 drawn (both ends have a graph node), width by priority score, dashed if it did not bind in the window; ends blue (import) or amber (export) from the shift factor of the end substation where it is fitted | on |
| Upgrades U01–U10 | halo + dashed violet line and a badge; popup: cost low/mid/high, lead time, project on file, battery size and cost ratio, 2025 rent, and the rerank result. 9 of 10 drawn: U04 (WESTEX) is an interface; U05 and U09 get a badge at the one end that is placed | on |
| Placed substations | 3,633 on canvas, four confidence bands (0.85 / 0.7 / 0.5), the lowest band behind a checkbox | on (0.5 and up) |
| Risk-excluded substations (147) | red crosses, reason, flood zone, wildfire class | off |
| Top 100 by reliability need | teal rings; popup: sub-scores, bridge, hospitals, avoided unserved energy and its VoLL dollars labelled as customer value | off |
| Tier 2 (488 of 2,591) | diamonds; every valued candidate (166), the 150 with the highest need, and the highest-need candidate in each 0.5-degree cell; popup: DESR and ADER values, distance caution, deferral and reliability under "not added to the above" | off |

Plus a legend, a "how to read this map" panel, a "jump to a site" list (needed on a phone, where a small circle is hard to tap) and the labels REAL / DERIVED / SIM / ASSUMPTION on every number.

**What was tested.** Over HTTP in the built-in browser pane (a plain `http.server` on a free port, stopped afterwards): Leaflet and the integrity hashes load, the page raised no error; all 40 site popups open (shortest 1,531 characters); the upgrade, constraint, excluded, reliability and Tier 2 popups render; every layer toggle works; the dark and light palettes both render (the legend swatches got a light outline in dark mode after the first look); at the pane's width of 558 px the stacked layout has no horizontal scroll (scroll width 543). `node --check` parses the page script and the data file (a test). **Not tested:** a real `file://` open (the pane renders local files as static snapshots without running scripts) and a real phone width (the pane's emulated sizes did not resize the page); the design relies on no `fetch`, so a local file should work as the Phase 2 page's note says. The pane is also too small to judge the desktop layout.

## 4. Checks (`gridsite/report_check.py`, `gridsite/check_labels.py`)

`run_grid.py report-check` runs 58 checks and exits 1 on a failure: the label sidecars (46 tables, 0 problems, no orphan sidecar); the README structure (sections in order, 7 TL;DR bullets, the 32 numbered items, each label word at least 20 times, the terms the brief names); the top-15 table and Appendix A against `sites.csv` field by field (15 and 40 rows); the rerank, disagreement and Tier 2 tables of the README against their CSVs; the invariants of `upgrade_rerank.csv` and `tier2_valued.csv`; the map (files, size, Leaflet version, attribution, palettes, viewport, no fetch, payload parses and has every layer). `check_labels.py` needs only the standard library: a sidecar must have `column,label,source,note`, cover every CSV column once, use only REAL / DERIVED / SIM / ASSUMPTION, and have no stale row; a test builds three broken tables to prove it catches them.

**Result on 11 Oct 2026: 46 of 46 tables pass, 58 of 58 report checks pass, 16 of 16 Phase 6 tests pass.** Re-run the same day: `test_arena_compat.py` 18/18 (3 s), `test_3b.py` 36/36 (60 s), `test_4_upgrades.py` 16/16 (16 s). No label problem was found in an earlier phase's table, so none was left unfixed.

**The numbers spot-checked against the CSVs** (README text, source, recomputed value; all agree). Every README quote is found in the text and equals the recomputation:

| # | README says | Source | Recomputed |
|---|---|---|---|
| 1 | MIDW 2 h learner 2021–25 $269.7, half-life 2028 | `sites.csv` | 269.7, 2028 |
| 2 | KNAPP 4 h learner $111.4; 2021–25 $98.2 | `sites.csv` | 111.4, 98.2 |
| 3 | $78.3 at the 40 nodes, $44.4 at the 8 zones | `site_years.csv` (grid, mean_2021_25) | 78.3, 44.4 |
| 4 | Forecaster beats the learner at 36 of 40 nodes; $81.0 | `site_years.csv` | 36, 81.0 |
| 5 | Mean gain $38.4 (2025 replay), $33.1 (2027), $19.3 (2029) | `sites.csv fwd_uplift_*` | 38.4, 33.1, 19.3 |
| 6 | Battery 21 times the wires fix, 192 times the cheapest rating change | `upgrades.csv` medians (n = 24) | 21.4, 192.3 |
| 7 | MIDW $247.7 to $51.0 under U03, rank 1 to 9, sum −31.2% | `upgrade_rerank.csv` | same |
| 8 | First-order baseline rank correlation 0.89; 0.46 over 76 pairs | `upgrade_rerank.csv` | 0.891, 0.456, 76 |
| 9 | 172 valued candidates at 150 buses; +$27.0, +$13.0; ADER $23.9 | `tier2_valued.csv` | same |
| 10 | MID #10, BG #11, GT #62, PK #64; four of the 40 in the top 100 | `sites.csv` | same |
| 11 | 147 of 2,869 substations excluded | `risk_flags.csv` | 147, 2,869 |
| 12 | Group caps total 200 MW; learner rule with caps $34.9 | `upgrade_rerank.portfolio_groups_table`, `derived/runs/portfolio_summary.json` | 200, 34.9 |
| 13 | 95,948 pairs, rank correlation −0.41, 1,787 pairs (47%) at 8 or more hops | `hops.csv` | same |
| 14 | Net of connection $21.9/kW-yr, break-even $389/kW | `sites.csv` medians | 21.88, 388.6 |
| 15 | 37 of 40 beat their zone; import +$40.1, export +$10.9 | `sites.csv` | same |

## 5. Decisions and deviations

- **`map` collides with Phase 2's `map`.** `run_grid.py` merges the commands of every `cli_*.py` in file order, so `cli_6`'s `map` (which the brief asks for) would replace `cli_2`'s. `cli_6.map` therefore builds this page, and `map substations` calls Phase 2's builder unchanged. The earlier notes' `run_grid.py map` for the substation map now needs the word `substations` (the README says so; `tests/test_6.py` asserts that `map` is the only shared name).
- **Planned bundle by 2031** excludes projects already in the 2025 replay or in the October 2026 window (rules of `saturation.upgrade_scale`), and a second bundle `PLANNED_ALL` (adding the 2032 projects) was added so that the comparison with Phase 4's `PLANNED_ALL` is like for like.
- **Rerank threshold $1** instead of $0.25 (see section 1).
- **`nodal_value_p10_usd_kw_yr` left empty**, and the 2029 saturation case added instead.
- **Tier 2: the screen is the plain top 150 by score**, as the brief says, and 94 of the 172 valued candidates are behind one element (Twin Buttes) whose shift factors are the weakest in the study; I added the distance flag rather than diversify the screen.
- **File ownership.** I created `gridsite/upgrade_rerank.py`, `tier2_value.py` and `tests/test_6.py` besides the files the brief names (helpers of mine).

## 6. Observations about earlier phases (documented, not fixed)

1. **`sites.value_sites` cache key includes the whole scale vector**, so an upgrade that touches 2 of 260 constraints recomputes all 40 lanes, including lanes whose only link to the element is a tiny shift factor (their value moves by cents). Performance only.
2. **First order's zone sensitivity is the unweighted mean of bus shift factors** (`upgrades.zone_mean_sf`), while the lanes use a regression of the zone's own price on shadow prices (`nodal_lanes.zone_sf`). This is the likely reason first order misses the second exposure of the Scurry–Knapp sites to Twin Buttes (shown in the rerank; the cause itself is untested).
3. **COLLEGE (Phase 3b candidate 33) is placed in Brazos County**, but BRAND_C and every site behind College–Jupiter are in Dallas County, and Phase 2 rejected the Brand–College pair on length (261 km); Phase 2's list of 12 rejected pairs asked Phase 1c to look at them. Its value does not depend on position; its map position and connection class may.
4. **`BRAND` is aliased to `BRAND_C` at confidence 0.45** (`station_alias.csv`), and Encrt–Brand's candidates (ECRSW in Scurry County, ROUGH in Val Verde County) are hundreds of km from BRAND_C.
5. **MIDW, STRS, POWELLFI and SEGOVI have no reliability rank** because their counties' EAGLE-I reports exceed the county's customers (`denominator_flag = artifact`), so the outage component is blank and the substation is not rank-eligible; they do have a need score from the other components (5.6 and 35.5 of 100). The README says so.
6. **Four of the ten top upgrades are not in the replayed structure** (Phase 3b caveat 2): the full rerank cannot move a site for them.
7. **`derived/runs/portfolio_summary.json` lives outside the repo**, and the README's learner-rule figures ($34.9 with caps, 28.5–34.9 by rule) are read from it by `report-check` (a skipped check would print NaN and fail).

## 7. Hand-offs and open items

- **Dispatch platform.** `data/sites.csv` in the order of README Appendix A is the queue of settlement points; the README section 7 gives the first ten with the bus a node would sit on and a hub proxy (an ASSUMPTION by zone). Nothing in `dispatch/` was edited.
- **ERCOT API key.** The first request should be one month of bus LMPs and shadow prices (3 downloads of a report per year). It would turn the top sites' lanes into REAL, let Vealmoor–Koch Tap, WESTEX, Longshore–Consavvy and Bell County–Salado be fitted (so the rerank can move a site for all ten upgrades), and settle whether the Forecaster really beats the learner at a node.
- **4 h rerank and a Tier 2 rerank** were not run (the brief asks for 2 h, 2025). `value_sites(spec="grid4h")` takes the same `mu_scale`.
- **A real phone and a real `file://` open** have not been seen by me; open `map/index.html` once on each before sharing. Publishing the page as an artifact was not requested and was not done.
- **Unchecked claims in the README that rest on earlier notes, not on a CSV in this session:** the Phase 3b forward table by year (2026, 2028, 2030 rows), the saturation sensitivity to `SAT_MIN_SF`, the algorithm tables' share-of-oracle and $/MWh columns, the 4 h to 2 h ratios, the ancillary figures, and every REAL regulatory citation (they come from `notes/rules.md` and `notes/phase4.md` and were not re-read in the primary texts).
