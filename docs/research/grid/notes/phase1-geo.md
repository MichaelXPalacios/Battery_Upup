# Phase 1c: geography of the ERCOT grid (substations, lines, coordinates)

Agent 1c, 10 Oct 2026. Code: `gridsite/fetch_geo.py`, `gridsite/geomatch.py`, `gridsite/cli_1c.py`
(`run_grid.py fetch-geo [osm|eia|geo|hifld|all]`, `run_grid.py match-geo [--fresh]`; `match-geo` takes about 2 minutes).
Outputs: `data/substations.csv`, `data/osm_substations.csv`, `data/lines.csv`, `data/eia_plants_tx.csv`, plus one extra table
`data/eia_generators_tx.csv` (unit-level EIA tech, MW, status and planned dates, for the saturation work), each with a `.labels.csv`.
Map layers: `research-data/grid/derived/lines.geojson` and `substations.geojson`. Validation numbers:
`derived/geo_coverage.json`, `geo_validation.json`.

**Headline.** Of 5,035 NP4-160 substations, 3,633 (72%) have coordinates and
2,870 (57%) have confidence >= 0.5. For the priority sets it is better:
977 of 1080 resource-node substations are placed (769 at >= 0.5), and
128 of 158 stations in SCED binding constraints (115 at >= 0.5).
The line graph has 8,607 OSM substation-to-substation edges and one giant component at every voltage class.
Use `match_conf >= 0.5` for anything that ranks sites; below that the coordinate is a hint, not a position.

## 1. Sources actually used (all public, no key)

| Source | What | Vintage | Notes |
|---|---|---|---|
| OSM Overpass (`overpass-api.de`) | 6,239 `power=substation` (centre + tags), 6,188 outlines, 30,322 `power=line|cable` ways >= 69 kV (4 voltage-band files, deduplicated by way id), 225 battery plants | database timestamp 2026-10-10 15:33 to 16:04 UTC | `ODbL`. Overpass answers HTTP 406 to browser-like user agents, so a plain `hugging-base-grid-research/0.1` agent is used. 504 back-offs handled by `overpass_query`. Lines without a `voltage` tag are not in the query. |
| HIFLD mirrors (ArcGIS Online) | Electric Substations (7,618 in the Texas envelope, 4,939 with state TX, 1,947 with a real name); Electric Power Transmission Lines (9,957 segments, `SUB_1`/`SUB_2`, owner, voltage) | substations layer 2022 vintage; lines layer `ElectricTransLines12012021` (1 Dec 2021) | **Found** (see section 8 for URLs). The NASA NCCS copy (`maps.nccs.nasa.gov/.../hifld_open/energy`) timed out at 60 s and was not retried. Texas names are mostly `UNKNOWN<id>`/`TAP<id>` (74%), so HIFLD adds names only for ~1,400 TX substations. |
| EIA-860 2025 final + EIA-860M August 2026 | plants (1,533 TX, with TDSP and grid kV), generators, storage MWh, planned units with dates | annual file Dec 2025; monthly Aug 2026 | The 2025 early-release zip is no longer listed. Sep to Dec 2026 workbooks are commented out on EIA's page and are skipped. |
| Census `cb_2023_us_county_500k` | county polygons | 2023 | read by a 40-line shapefile reader in `geomatch.py` (no shapely in the venv); point-in-polygon in numpy. |
| ERCOT CDR, December 2025 (`raw/docs/ERCOT_CDR_December2025.xlsx`) | 1,446 resource units with unit code, readable unit name, county, technology, MW | Dec 2025 | Downloaded by 1c into 1b's `raw/docs` folder and recorded in the manifest (source `ercot-cdr`). CDR unit code = `UNIT_SUBSTATION` + `_` + `UNIT_NAME`, which gives readable names and counties for ERCOT resource units. |
| ERCOT GIS report, September 2026 (1b's download) | 1,789 projects with county and POI text | Sep 2026 | POI text often contains PSS/E bus numbers (`Tap 345kV 1444 BROWN SWITCH - 3424 BUCKHORN SWITCH`). They join to `PSSE_BUS_NUMBER`: 2,273 (project, bus) hits resolve to 796 substations and give a readable name plus a county. |
| ERCOT NP4-160 | `data/settlement_points.csv` written by 1a (19,493 buses, 5,035 substation codes) | newer than the Sep 2026 archive copy | `sp_file()` prefers it and falls back to the archive copy. |
| Binding constraints | `derived/shadow_sced.parquet` (10 CPT days, 24,742 rows, 158 stations) and `shadow_dam.parquet` (31 days, 34,249 rows, 808 stations) from 1a | to 10 Oct 2026 | 85 station names are not NP4-160 substation codes (taps and switches such as `V4_TAP`, `ENCRT`); they are kept as extra rows with `in_np4160 = False` and `n_buses = 0`. |

## 2. How a substation gets a coordinate

Every NP4-160 `SUBSTATION` (and every extra binding-constraint station) collects candidate sites from three methods. Candidates within 6 km
of each other are one site; the site with the highest fused confidence wins; the alternative sites are kept in the run (`n_sites`) but not written.

**(a) `eia_unit`.** Resource node -> unit -> readable unit name and county -> EIA plant coordinates.
- Units come from the CDR (`UNIT_SUBSTATION_UNIT_NAME`), from GIS projects whose POI bus number belongs to the sub, and, for POI switchyards, from units registered at another sub that share the sub's resource node (`remote` records; up to 284 subs have such units).
- A plant in the same county scores 0.45 x capacity overlap by technology class + 0.45 x name similarity + 0.10 county. EIA's `RTO/ISO LMP Node Designation` equal to the ERCOT unit code or resource node is a direct hit (67 subs).
- If nothing in the county fits, any Texas plant with name similarity >= 0.8 is used (`name-only`, 19 subs).
- `conf = CLASS_PRIOR[class] x min(1, score/0.8)`, with priors thermal 0.85, nuclear 0.90, hydro 0.80, battery 0.75, solar 0.60, wind 0.55 (a wind or solar plant point is typically several km from its collector substation). Factors 0.7 for a name-only match, 0.7 for a plant reached through a gen-tie (POI sub), 0.7 when a rival plant scores within 0.1, 0.5 when EIA's coordinates fall outside EIA's own county (15 plants). A direct LMP-node hit has a floor of 0.92.
- This is the plant location, not the fence. Where a transmission-class OSM substation within 5 km has a compatible voltage, `osm_id` is set by snapping (`osm_link = snap`) so the sub is a node in the line graph; coordinates stay the plant's.

**(b) `osm_name` / `hifld_name`.** Name variants of the sub are compared with named OSM substations (3,301), HIFLD substations (1,418 named TX), and OSM battery plants (194).
- Variants: the sub code (also with `_SLR`, `_ESS`, `SW`, `TP` suffixes stripped), PSS/E bus-name stems (`ELM_MOTT__8` -> `ELM MOTT`, `MAXWELL4A` -> `MAXWELL`), GIS POI names, and CDR unit names.
- Similarity: exact letters 1.0; code is a prefix of the name 0.85; code is a subsequence of the name (`ELMOT` ~ `Elm Mott`, coverage >= 0.45, or >= 0.3 if every word initial appears) <= 0.8; word-set containment 0.8 to 0.9. Variants are weighted code 0.9, PSS/E 0.95, GIS 1.0, CDR 0.85.
- One shortcut is data, not a guess: for CenterPoint, Austin Energy and CPS the OSM `ref` tag equals the ERCOT station code (189 exact hits, e.g. `GP` = Galena Park). It counts as 0.95 only when the operator's load zone matches the sub's zone, otherwise 0.4 (CenterPoint and Austin Energy reuse short codes).
- Score = name x voltage factor (1.0 if an ERCOT transmission kV is within 12% of an OSM kV, 0.85 untagged, 0.6 disagree) x county factor (1.0 inside the CDR/GIS county, 0.9 within 12 km, 0.25 outside; 0.5 and 40 km for line-tap counties) x zone and neighbour priors x source prior (OSM 0.95, battery plant 0.85, HIFLD 0.80). OSM and HIFLD records within 3 km are one site (+0.05). If a rival site scores above 70% of the best, confidence shrinks (to half at parity).
- Zone prior (second round): the 5 nearest *verified* subs (EIA plant match, or a name match inside its CDR/GIS county; 729 anchors) vote on the load zone of a candidate; the candidate is x0.5 if none shares the sub's zone. Neighbour prior: x0.6 if the candidate is more than 120 km from every confident binding-constraint neighbour. Operators that serve one city (CenterPoint, Austin Energy, CPS) outside their zone get x0.35. These priors only down-weight; they never create a candidate.

**(c) `neighbor`.** Stations named together in a binding constraint are two ends of one line.
- Confirm: a site within 120 km and 4 OSM line hops of a confident (>= 0.7) neighbour gets noisy-OR +0.25 (365 subs); a site with no such neighbour gets x0.8 (28).
- Tie-break: an alternative site replaces the best when only it fits a confident neighbour (11 subs).
- Fill-in: an unplaced station next to a confident one takes the only compatible, unclaimed OSM substation within 2 line hops, confidence 0.40, or 0.55 with two agreeing neighbours (4 stations). Co-occurrence is never the only evidence for a sub that has any name or plant evidence.

**Fusion.** Within a site: `conf = 1 - prod(1 - w_k c_k)` over methods (w = 1, then 0.8). A conflicting site with at least half the support costs up to 30%. Cap 0.97 (name-only methods 0.95).
Constants are single named values at the top of the section that uses them, each tagged ASSUMPTION or REAL. They are guesses made before seeing outcomes, then checked against the independent tests in section 5.

`match_conf` is **not** a calibrated probability. Read it as: 0.85+ a name, county and voltage all agree (or an EIA LMP-node hit); 0.7 to 0.85 name and voltage agree, county unknown; 0.5 to 0.7 plausible, one ingredient weak; below 0.5 unverified.

## 3. Coverage

Columns: n, placed (any coordinate), confidence >= 0.5, >= 0.7; placed by method `eia_unit / osm_name / hifld_name / neighbor`;
confidence bands `none / 0-0.3 / 0.3-0.5 / 0.5-0.7 / 0.7-0.85 / 0.85-1`.

| Set | n | placed | conf >= 0.5 | conf >= 0.7 | by method | by band |
|---|---|---|---|---|---|---|
| All NP4-160 substations | 5035 | 3633 (72%) | 2870 (57%) | 1915 (38%) | 489 / 2939 / 201 / 4 | 1402 / 283 / 484 / 957 / 797 / 1112 |
| Subs with a resource node (priority 1) | 1080 | 977 (90%) | 769 (71%) | 527 (49%) | 413 / 542 / 22 / 0 | 103 / 55 / 153 / 245 / 122 / 402 |
| Stations in SCED binding constraints, 10 days (priority 2a) | 158 | 128 (81%) | 115 (73%) | 96 (61%) | 12 / 111 / 4 / 1 | 30 / 2 / 11 / 19 / 31 / 65 |
| Stations in DAM binding constraints, 31 days (priority 2b) | 808 | 639 (79%) | 555 (69%) | 416 (51%) | 79 / 532 / 24 / 4 | 169 / 29 / 55 / 143 / 156 / 256 |
| ... of which NP4-160 substations (SCED or DAM) | 731 | 606 (83%) | 532 (73%) | 405 (55%) | 79 / 502 / 21 / 4 | 125 / 27 / 47 / 131 / 148 / 253 |
| ... of which not NP4-160 codes (taps, switches) | 85 | 39 (46%) | 29 (34%) | 15 (18%) | 0 / 36 / 3 / 0 | 46 / 2 / 8 / 14 / 8 / 7 |
| Union of priority 1 and 2 | 1683 | 1420 (84%) | 1145 (68%) | 805 (48%) | 425 / 953 / 38 / 4 | 263 / 80 / 195 / 344 / 251 / 550 |

`osm_name` counts the sites where OSM supplied the coordinate; 417 of them are also corroborated by an EIA plant within 6 km (`methods_agree`).
Every placed sub has a county from the TIGER polygons; none fell outside Texas.

The 1,402 unmatched NP4-160 subs (28%) are mostly 69 and 138 kV load substations and distribution-owned stations with no named OSM or HIFLD counterpart,
for example `36POD`, `A3`, `ABPOD`, numbered/lettered codes. 103 of them carry a resource node (see section 7).

## 4. Confidence histogram (NP4-160 substations, n = 5,035)

| match_conf | subs | |
|---|---|---|
| 0 (unmatched) | 1402 | ######################################################## |
| 0-0.1 | 16 | # |
| 0.1-0.2 | 82 | ### |
| 0.2-0.3 | 185 | ####### |
| 0.3-0.4 | 202 | ######## |
| 0.4-0.5 | 282 | ########### |
| 0.5-0.6 | 462 | ################## |
| 0.6-0.7 | 495 | #################### |
| 0.7-0.8 | 605 | ######################## |
| 0.8-0.9 | 559 | ###################### |
| 0.9-1.0 | 745 | ############################## |

Resource-node subs (1080): none 103, 0-0.3 55, 0.3-0.5 153, 0.5-0.7 245, 0.7-0.85 122, 0.85-1 402.

## 5. Checks

### 5.1 Twenty spot checks (by eye)

Drawn from the committed `substations.csv` with `spot_check_sample(df, seed=101)`: NP4-160 subs, 8 at conf >= 0.85, 6 at 0.70 to 0.85, 6 at 0.50 to 0.70.
A random draw changes whenever any confidence changes, so this table was drawn after the last code change and describes exactly the committed file.
Earlier draws on earlier versions of the pipeline found real errors and drove fixes: seed 11 had `SW` (LZ_HOUSTON matched to an Austin station) and `POWEL`, `TINTOP`, `COMRC` (LZ_NORTH matched to same-named stations in Harris, Matagorda and Bexar counties), which led to the `ref` zone rule, operator-zone rule and the zone-vote prior; seed 101 had `MNTTP`, matched to an EIA plant whose county field says Franklin but whose coordinates are in Montgomery County, which led to the EIA coordinate check.
"Pass" means the place is plausible to a reader who knows the Texas map: the sub code and PSS/E names read as that town or feature, the county and the kV fit. It does **not** prove that two adjacent substations were told apart.

| sub | zone | kV | conf | method | matched to | county | verdict | note |
|---|---|---|---|---|---|---|---|---|
| WEIL_TRC | SOUTH | 138 | 0.92 | osm_name | Weil Tract Substation | Nueces | pass | Weil Tract, Corpus Christi; county from CDR |
| DPO | HOUSTON | 138 | 0.95 | osm_name | Depot Substation | Harris | pass | PSS/E DEPOT, CenterPoint area; name only |
| CTT_CROS | WEST | 345 | 0.95 | osm_name | Cross Substation | Hall | pass | CREZ 345 kV Cross, Panhandle; county from GIS (line tap) |
| AMOSCR | WEST | 345 | 0.92 | eia_unit | Galloway 1 Solar Farm (EIA, LMP-node designation) | Concho | pass | plant point; the POI sub is 'Amos Creek', gen-tie distance unknown |
| OCONNOR | SOUTH | 69 | 0.93 | osm_name | O'Conner Substation | Refugio | pass | O'Connor, Refugio County (PSS/E spelling OCONNER) |
| VERTIGO | WEST | 345;34.5 | 0.90 | osm_name | Vertigo Wind Substation | Baylor | pass | Vertigo wind farm; county from CDR |
| MRE | HOUSTON | 138 | 0.90 | osm_name | Marine Substation | Brazoria | pass | PSS/E MARINE, Freeport / Velasco area; name only |
| WATERPRT | SOUTH | 138 | 0.95 | osm_name | Waterport Substation | Cameron | pass | Port of Brownsville; county from GIS |
| BYU | HOUSTON | 138;13.8 | 0.77 | osm_name | Bayou Cogen Substation | Harris | pass | PSS/E BAYOU_POI; Pasadena cogeneration |
| SHWPD | WEST | 138 | 0.75 | eia_unit | Champaign BESS (EIA) | Glasscock | uncertain | battery plant matched on capacity and county (CDR); PSS/E name SHAW_P carries no check |
| OATES | NORTH | 138 | 0.72 | hifld_name | Oates (HIFLD) | Dallas | pass | Oates Drive, east Dallas; HIFLD name equals the code |
| ARROTT | WEST | 69 | 0.73 | osm_name | Arrot Substation | Schleicher | pass | Arrott, near Eldorado; name only |
| MCCAMPBE | SOUTH | 138 | 0.77 | osm_name | McCampbell Substation | San Patricio | pass | McCampbell plant, Ingleside |
| FLATROCK | NORTH | 138 | 0.73 | osm_name | Flat Rock Substation | Parker | pass | Flat Rock, Parker County; name exact, LZ_NORTH |
| STWF | WEST | 138;34.5 | 0.51 | osm_name | South Trent Wind Farm Substation | Nolan | pass | South Trent wind; county from CDR |
| GRAPEVIN | NORTH | 138 | 0.68 | osm_name | Grapevine Ball Street | Tarrant | pass | Grapevine |
| CHAMP | NORTH | 138 | 0.63 | osm_name | Champion Paper Mill Substation | Angelina | uncertain | PSS/E CHAMPION; the mill name matches but I cannot place it by eye |
| BUTLR_RD | HOUSTON | 138 | 0.68 | osm_name | Butler Road Substation | Galveston | pass | Butler Road, League City / Friendswood (TNMP) |
| MONTECR1 | SOUTH | 345;34.5 | 0.55 | eia_unit | Monte Cristo 1 (EIA) | Hidalgo | pass | Monte Cristo wind farm |
| ELZCK | NORTH | 138 | 0.55 | hifld_name | Elizabeth Creek (HIFLD) | Denton | pass | Elizabeth Creek, Denton County; HIFLD name |

Result: 18 pass, 2 uncertain, 0 fail. Twenty by-eye checks cannot bound the error rate (and the earlier draws show the failures were real before the fixes); they show the gross failure (a same-named station in the wrong part of Texas) is rare after the priors. The residual risk is exactly that case in a zone with no county evidence, so treat 0.5 to 0.7 as the least trustworthy band and expect single-digit percent wrong there.

### 5.2 Independent consistency tests

The EIA test is independent of the confidence. The constraint-pair test is partly circular: the neighbour stage raises the confidence of sites that fit a confident neighbour and lowers it for sites that do not, and a neighbour prior down-weights candidates more than 120 km away. Read it as a check on the spatial sanity of the final answer, not as an unbiased accuracy figure.

**Binding-constraint pairs.** Two stations of one binding line must be close and a few OSM hops apart. For constraint pairs with both ends placed (min of the two confidences):

| min conf | pairs | median km | p90 km | within 60 km | within 120 km | pairs with graph path | <= 3 hops | <= 5 hops |
|---|---|---|---|---|---|---|---|---|
| 0.0-0.5 | 61 | 143 | 510 | 34% | 49% | 45 | 24% | 27% |
| 0.5-0.7 | 85 | 12 | 91 | 86% | 91% | 78 | 86% | 90% |
| 0.7-0.85 | 120 | 10 | 34 | 96% | 100% | 120 | 95% | 100% |
| 0.85-1.0 | 74 | 9 | 38 | 96% | 100% | 73 | 92% | 100% |

Hops are on the OSM line graph, so a pair separated by an intermediate unmapped tap shows as 2 hops. The 120 km column is built in by the neighbour prior; the hop columns use only the line graph, but the swap step (11 subs) also looks at hops.

**EIA plant against name match.** For subs where an EIA plant (conf >= 0.6) and an OSM/HIFLD name match both exist, how far apart are the two independent coordinates:

| name-match conf | subs | within 10 km | within 25 km | median km |
|---|---|---|---|---|
| 0.0-0.3 | 62 | 0% | 0% | 386.9 |
| 0.3-0.5 | 53 | 77% | 96% | 2.5 |
| 0.5-0.7 | 97 | 89% | 96% | 0.3 |
| 0.7-0.85 | 64 | 86% | 97% | 0.4 |
| 0.85-1.0 | 146 | 88% | 96% | 0.5 |

The test set is plant subs, which are easier than load substations, so treat it as an upper bound for the 0.5 to 0.85 bands. The 0 to 0.3 band is right to be low: those matches are essentially all wrong.

**Snap tolerance.** Rebuilding the line graph with endpoint tolerances of 200, 350, 500, 800 and 1,200 m changed the edge count from 9,047 to 8,199 and the share of constraint pairs at <= 1 hop from 68.8% to 66.7%.
500 m (`SNAP_TOL_M`, as briefed) is kept. A mid-way vertex counts as touching a substation only if it is on the fence (30 m) or within 100 m of a substation tagged at least 90% of the line voltage; that rule raised constraint pairs at <= 3 hops from 90.9% (500 m, no voltage rule) to 94.6%.

## 6. Line graph

OSM `power=line|cable` ways >= 69 kV are cut at shared vertices and at substation fences (30 m grid inside and along each outline), then walked from every substation to the next one at the same voltage.
Parallel ways between the same two substations add circuits (cap 6); T-junctions with no substation are flagged `via_tap` (35% of edges). Gaps up to 150 m between free ends of equal voltage are bridged.
HIFLD segments are added as `source = hifld` rows with endpoints snapped within 1.5 km; 2894 of 9000 duplicate an OSM pair (`in_osm_pair`) and the rest add taps, 161 kV segments and segments whose ends did not snap to an OSM substation.

| kV | OSM edges | OSM km | HIFLD segments |
|---|---|---|---|
| 69 | 2005 | 25,265 | 2401 |
| 115 | 444 | 6,789 | 725 |
| 138 | 5129 | 50,395 | 4822 |
| 230 | 192 | 4,728 | 141 |
| 345 | 835 | 20,129 | 652 |
| 500 | 2 | 51 | 9 |

HIFLD also has 250 segments at 161 kV (not in OSM's Texas voltage bands). Only 2,382 of 8,607 OSM edges have an ERCOT sub at both ends at conf >= 0.5: the graph is complete, the labelling is not.

Connectivity (OSM edges at or above each voltage; matched = ERCOT subs with `osm_id` and conf >= 0.5):

| class | edges | nodes | components (largest sizes) | matched subs in graph | matched in largest | all linked subs in largest |
|---|---|---|---|---|---|---|
| >= 69 kV | 8607 | 5708 | 21 (5585, 74, 5, 4) | 2807 | 2801 | 3448 |
| >= 138 kV | 6158 | 4220 | 4 (4213, 3, 2, 2) | 2269 | 2269 | 2785 |
| >= 345 kV | 837 | 699 | 3 (693, 3, 3) | 517 | 517 | 638 |

There is a single giant component at every class (>= 138 kV: 4,213 of 4,220 nodes), and every matched ERCOT sub sits in it: 2,269 of 2,269 matched subs that touch the >= 138 kV graph.
That is partly an artefact: OSM carries no ISO tag, so the component also holds SPP, WECC and MISO lines (El Paso, the Panhandle, East Texas). Phase 2 should restrict to the ERCOT-matched subs plus their neighbours, not trust connectivity as proof of synchronism.
390 OSM substations are shared by more than one ERCOT sub with conf >= 0.5 (ERCOT splits some stations by owner or voltage, e.g. `SALSW`/`SALDS`); `osm_shared_by` counts them.

## 7. Known gaps

1. **Unnamed and unmapped substations.** 47% of OSM substations have no name; HIFLD names are 74% placeholders. Stations built for 2020 to 2026 generator interconnection (`XXXSW` switchyards such as `MPTSW`, `GRISW`, `DOGHS`) are mostly missing by name. 103 resource-node subs and 30 SCED binding stations are unplaced (for example `BCV`, `NLARSW`, `SETTLE`, `JUPITER`, `SGVSW`).
2. **Plant points are not substations.** 489 subs are placed at an EIA plant, 12 SCED stations among them; wind and solar points can be 5 to 15 km from the collector substation. The `eia_dist_km` column compares the final point with the plant.
3. **Binding stations that are not NP4-160 codes** (85; taps and switches such as `V4_TAP`, `ENCRT`, `GANSO`, `CATSW`, `FRYSW`): 46 unplaced, and a tap has no single coordinate anyway.
4. **Neighbour fill-in is weak.** Only 4 stations were filled; most unplaced stations have one confident neighbour and several compatible unclaimed OSM substations next to it, so no unique answer.
5. **Vintage.** CDR is December 2025 (units added since are not named), HIFLD lines are December 2021, OSM is current but volunteer-mapped. Planned 765 kV and new 345 kV lines are not in OSM or HIFLD. Lines tagged without `voltage` are not downloaded.
6. **No electrical data.** No ratings, impedances, transformer or bay counts; `n_circuits` is a tag-based count. DC ties (Railroad, Laredo VFT, Eagle Pass, East, North) are not modelled as edges.
7. **EIA data quality.** 82 plants have coordinates outside their stated county by strict polygon test (15 by more than 12 km, e.g. `Lupinus Solar 1/2` listed in FRANKLIN with Montgomery County coordinates); see `coord_county_match` in `eia_plants_tx.csv`.
8. **Label noise.** Where the same name exists in two places and no county evidence exists, the zone vote is the only guard. The 0.5 to 0.7 band is the least trustworthy.

What would close the most: (i) any TSP or ERCOT list of station coordinates (the ERCOT network-operations model carries them but is not public); (ii) PUCT CCN filings for post-2020 stations and 765 kV routes, joined through the TPIT project list 1b is building; (iii) a 2024-25 HIFLD extract from DataLumos or HSDL with real substation names; (iv) naming the unnamed OSM transmission substations from GIS POI text (`Tap 345kV <bus> A - <bus> B` already encodes their neighbours).

## 8. Reproduce and contract notes

- URLs: HIFLD substations `https://services5.arcgis.com/HDRa0B57OVrv2E1q/ArcGIS/rest/services/Electric_Substations/FeatureServer/0`; HIFLD lines `https://services2.arcgis.com/wdQEqhSQSuYA89VW/ArcGIS/rest/services/ElectricTransLines12012021/FeatureServer/0`. Every download is in `research-data/grid/manifest.csv` (source `osm-overpass`, `hifld-mirror-arcgis`, `eia-860`, `eia-860m`, `census-tiger-cb`, `ercot-cdr`).
- `match-geo` caches the slow Excel parses under `derived/_geo_cache/` keyed on input names, sizes and mtimes; `--fresh` ignores the cache. It reads `data/settlement_points.csv` and the two shadow-price parquets as they are, so re-run it after 1a refreshes them.
- Contract additions to `data/substations.csv` (all after the specified columns): `methods_agree, evidence, osm_link, neighbor_ok, n_sites, eia_dist_km, n_binding_sced, n_binding_dam, noie_zone, in_np4160, osm_shared_by`. `match_method` is `eia_unit`, `osm_name`, `hifld_name`, `neighbor` or `none`. The 85 extra station rows have `in_np4160 = False`; filter them out for a pure NP4-160 join.
- `lines.csv` has both sources in one table: filter on `source`. For a clean edge set use `source == 'osm'`, plus `hifld` rows with `in_osm_pair == False` if more coverage is wanted.
- Packages: nothing beyond the research venv (numpy, pandas, scipy, scikit-learn, networkx, requests, openpyxl). No shapely, geopandas or pyshp.
- Not done: no FEMA, wildfire, or outage geography (other phases); no Overture data.
