# Phase 5: reliability, physical risk and Tier 2 (distribution)

Agent 5, 10 Oct 2026. Code: `gridsite/reliability.py`, `risk.py`, `tier2.py`, `cli_5.py`. Run from `docs/research/grid/` with the research venv:

```
run_grid.py fetch-reliability [eaglei|oncor|osm|voll|all] [force]   # EAGLE-I (Texas rows), Oncor xlsx, OSM critical facilities
run_grid.py risk [fetch|build|all] [force]                          # FEMA, USFS, NOAA, USGS, OSM coastline, then data/risk_flags.csv
run_grid.py reliability [fresh]                                     # eaglei_county.csv, critical_facilities.csv, reliability.csv
run_grid.py tier2                                                   # tier2.csv (needs reliability.csv and risk_flags.csv)
```

`run_grid.py` rejects bare `--flags`, so options are plain words (`fresh`, `force`) or come after `--`.
The full rebuild from cached downloads takes about three minutes; two consecutive runs gave identical files (sha256 checked).

## Outputs

| File | Rows | Key | What it is |
|---|---:|---|---|
| `data/eaglei_county.csv` | 254 | `county`, `fips` | Outage history per Texas county from EAGLE-I, the three named events, and the 1 MW / 4 MWh battery's avoided unserved energy |
| `data/critical_facilities.csv` | 3,633 | `sub` | Hospitals, water/wastewater works and emergency stations around every placed substation (OSM) |
| `data/reliability.csv` | 5,035 | `sub` | Five sub-scores, the combined need score and rank, network-weakness columns, Oncor feeder stats, EUE and VoLL dollars |
| `data/risk_flags.csv` | 5,035 | `sub` | Flood, wildfire, storm history, coast, seismic, heat/cold and the exclusion flag |
| `data/tier2.csv` | 2,591 | `cand_id` | Distribution candidates: settlement routes, reliability, hosting screen, deferral value, home-fleet value |

Every CSV has a `<name>.labels.csv` sidecar (`column,label,source,note`); a check confirmed each sidecar covers exactly the CSV's columns with labels from REAL / DERIVED / SIM / ASSUMPTION.
**Reading the CSVs:** the ERCOT substation code `NA` (a CenterPoint station in Harris County) is a real key. Read `sub` with `keep_default_na=False, na_values=[""]` or pandas turns it into NaN.

## Findings

1. **A 1 MW / 4 MWh battery would have avoided about 3.7 MWh of unserved energy a year in the median Texas county** (Harris 1.1 MWh, Bexar 0.5), if it sat on a feeder with a 1 MW critical load and started every outage full. With only a 20% reserve it is 1.2 MWh. At the Brattle one-hour VoLL of $35,685/MWh that is about $131k per battery-year in the median county ($74k with Brattle's duration-adjusted prices). These dollars are reliability value to customers. They are kept apart from market value and never added to it.
2. **Beryl is the event that matters for a 4-hour battery in Houston.** In Harris County the battery would have served 0.9 MWh in Uri, 1.0 MWh in the derecho and 3.3 MWh in Beryl (EAGLE-I peaks of 25%, 27% and 91% of county customers). Uri is a rolling-blackout event, so a battery of this size sees less of it per customer than a long storm outage.
3. **Radial exposure is modest and concentrated in West Texas and the Panhandle.** Of 3,505 substations on the line graph, 618 (18%) sit behind a bridge in the OSM graph, and 472 of those stay bridges when HIFLD-only lines are added. LZ_WEST has the highest share (29% of its graph-linked subs, 23% after the HIFLD check; LZ_HOUSTON 17%, LZ_NORTH 14%, LZ_SOUTH 13%). The largest pockets are 7 to 8 substations (Wheeler, Randall, Reeves, Potter, Kimble, Mason and Deaf Smith counties). A single station on a single line is the usual pattern, not a deep pocket.
4. **Physical-risk exclusions remove 147 of 2,869 site-assessed substations (5.1%):** 129 in a FEMA special flood hazard area, 11 in a regulatory floodway, 7 at very high wildfire hazard potential. The flood check is blind in West Texas: 80% of LZ_WEST subs have no digital NFHL polygon.
5. **Tier 2 has 2,591 candidates** (2,024 NP4-160 subs at 138 kV or less with load evidence, 567 OSM-only distribution substations near load centres). Every one has an ADER route at its load-zone price. A nodal DESR route exists at the sub's own highest-voltage bus for 2,024 and at the nearest placed sub (within 15 km) for 567.
6. **The top-20 reliability list is not robust to the weights** (section "Top 20" below). It is fixed by county-level outage data, so it cannot rank substations inside a county except through pocket and facility counts.

## Sources and coverage

| Source | What was pulled | Coverage | Licence / access |
|---|---|---|---|
| EAGLE-I, ORNL (figshare 24237376 v4) | 12 yearly national files 2014-2025 (11.6 GB streamed), Texas rows kept: 22.3 M 15-minute county records; `MCC.csv`, `DQI.csv`, `coverage_history.csv` | 254 of 254 counties have records. Window 2018-2025 (DQI shows 85-98% customer coverage from 2018). Share metrics valid for 215 counties: 194 `ok`, 21 `inflated`; 39 `artifact` counties (reports exceed the county's customers) have share, EUE and VoLL columns blank. Valid counties hold 99.2% of customers. | CC BY 4.0, no key. Only the Texas rows are stored; the SHA-256 of each full national stream is in `raw/reliability/eaglei/eaglei_fullstream.json`. |
| Oncor 2023 PUCT Service Quality Report (Project 56005 item 8) | 3,279 feeders with 3.91 M customers: SAIFI, SAIDI, customers, per feeder, forced interruptions | 774 Oncor substation codes; **736 match an NP4-160 sub by exact code**; 347 of those are placed at confidence >= 0.5 (344 are Tier 2 candidates) | Public filing, no key |
| OSM Overpass | Hospitals (760 objects), water/wastewater works (1,337), fire/police/ambulance stations (2,949) | After merging same-kind objects within 150 m: 735 hospitals, 1,238 water works, 2,567 emergency stations. Counts exist for the 3,633 placed substations | ODbL |
| Line graph (`data/lines.csv`, phase 1c) | OSM edges >= 69 kV inside ERCOT counties, collapsed to one corridor per substation pair | 5,244 nodes, 7,621 corridors, 682 backbone (>= 345 kV) nodes; 1,406 HIFLD-only edges used as a mapping-gap check. 3,505 of 5,035 NP4-160 subs have a graph position (3,417 linked, 88 by nearest node within 3 km); 2,813 of them at confidence >= 0.5 | OSM ODbL, HIFLD public |
| FEMA NFHL (ArcGIS REST, layer 28) | Flood zone at 2,869 site-level subs (confidence >= 0.5, >= 69 kV), point query plus a 100 m buffer | 2,045 mapped (1,758 zone X minimal, 147 X moderate, 140 high), 824 with no NFHL polygon | Public, no key, 2 workers |
| USFS Wildfire Hazard Potential 2023 (classified, 270 m, IIPP ImageServer) | Class at the site and at 8 ring points 600 m out, 2,869 subs | 733 further subs get the county's most severe site class. The old `apps.fs.usda.gov` service answers 403 ("migrated to IIPP"). | Public, no key |
| NOAA Storm Events 2000-2025 | 26 yearly files; 115,861 Texas county/zone records | 98.0% resolve to a county by name; the rest are mountain and island zones | Public |
| USGS ComCat | M >= 3 events since 2015 in a box around Texas (3,563 returned), counted within 25 km of each placed sub | 3,633 placed subs | Public |
| OSM coastline | `natural=coastline` for the Texas Gulf, distance from each placed sub | 3,633 placed subs | ODbL |
| Arena NOAA GHCN stations | 8 airports, 2003-2025: days a year at or above 100 F and at or below 20 F | Nearest of eight station to each placed sub (coarse; can be several hundred km away) | Public |
| Brattle "Value of Lost Load Study for the ERCOT Region" (PUCT Project 55837, Aug 2024) | The PDF, for the VoLL and the duration table | Read directly; see VoLL below | brattle.com answers 403 to scripts, so the PDF was captured through the web-fetch tool and registered in the manifest (method `web`) |

Not pulled: the CenterPoint, AEP Texas and TNMP service quality reports (same PUCT filing, other items); the Census ACS API (it now requires a key, and no account was created); the LBNL ICE calculator.

**Manifest note.** The first EAGLE-I attempt used a browser User-Agent and figshare answered HTTP 202 with an empty body. Fifteen zero-byte rows from that attempt sit in `manifest.csv` (`eaglei:*` and `eaglei_outages_*#TX-rows-only`, 17:19-17:21 UTC). The manifest is append-only, so they stay; the later rows for the same files carry the real sizes and hashes.

## Method and constants

Unverified constants are single named values at the top of each module, each with a label comment. The ones that decide a number:

| Constant | Value | Label | What it does |
|---|---|---|---|
| `VOLL_USD_MWH` | 35,685 $/MWh | REAL | Brattle one-hour, ERCOT-wide VoLL (see below) |
| `VOLL_BY_DURATION_H` | 1 h $35,685, 2 h $21,326, 4 h $13,340 | REAL | Brattle duration table, used for the duration-adjusted dollars |
| `BATT_MW`, `BATT_MWH` | 1, 4 | REAL | The battery the task fixes |
| `BATT_USABLE_FRAC` | 0.90 | ASSUMPTION | Usable share of 4 MWh |
| `SOC_FULL`, `SOC_MARKET_RESERVE` | 1.0, 0.20 | ASSUMPTION | The two bounds on charge at outage start (dedicated backup vs a fleet that keeps only its 20% reserve) |
| `CRITICAL_LOAD_MW` | 1.0 | ASSUMPTION | The battery always has a 1 MW critical load to serve |
| `EAGLEI_WINDOW_START/END` | 2018 / 2025 | ASSUMPTION / REAL | Years used |
| `EVENT_MIN_PCT`, `EVENT_MIN_CUSTOMERS`, `EVENT_GAP_H` | 1%, 25, 2 h | ASSUMPTION | What counts as an event |
| `DEN_OK_RATIO`, `DEN_ARTIFACT_RATIO`, `DEN_QUANTILE` | 1.2, 1.5, 0.999 | ASSUMPTION | Denominator sanity check on EAGLE-I counties |
| `NA_FILL_ROWS` | 12 | ASSUMPTION | Explicit NA snapshots carry the last value up to 3 h |
| `NAMED_EVENTS` | Uri 13-23 Feb 2021, derecho 16-23 May 2024, Beryl 8-22 Jul 2024 (UTC) | REAL | Dates from `ercot.md` 7.2, windows widened |
| `RADIUS_HOSPITAL_KM`, `RADIUS_WATER_KM`, `RADIUS_EMERGENCY_KM` | 10, 10, 5 | ASSUMPTION | Facility radii |
| `FACILITY_WEIGHTS` | hospital 5, water 3, emergency 1 | ASSUMPTION | Criticality weights |
| `MAX_PATHS_COUNTED`, `POCKET_SATURATION` | 3, 8 | ASSUMPTION | Connectivity cap; pocket size that gives the size part 1.0 |
| `POCKET_MIX`, `BRIDGE_DOUBLE_CIRCUIT_FACTOR`, `BRIDGE_UNCONFIRMED_FACTOR` | 0.5/0.5, 0.5, 0.5 | ASSUMPTION | Pocket sub-score recipe |
| `SAIDI_SATURATION_MIN`, `SAIFI_SATURATION` | 160 min, 1.75 | ASSUMPTION | About the Oncor feeder p90 |
| `LARGE_LOAD_RADIUS_KM`, `LARGE_LOAD_SATURATION_MW`, `LARGE_LOAD_NEARBY_WEIGHT` | 50, 5,000, 0.5 | ASSUMPTION | Large-load component |
| `OUTAGE_MIX` | hours .5, p95 .25, named .25 | ASSUMPTION | Blend inside the outage sub-score (each part a percentile rank across counties) |
| `SCORE_WEIGHTS` | outage .30, pocket .25, facilities .20, Oncor .10, large load .15 | ASSUMPTION | Combined score weights |
| `FEMA_BUFFER_M`, `WHP_RING_M` | 100, 600 | ASSUMPTION | Allowance for coordinate error |
| `COASTAL_KM`, `SEISMIC_FLAG_MIN_EVENTS` | 25, 3 | ASSUMPTION | Coastal flag distance; events before the seismic flag |
| `EXCLUDE_*` | SFHA zones and floodway excluded; WHP class 5 excluded; seismic and coastal never exclude | ASSUMPTION (seismic: REAL, the task says flag only) | Exclusion rules |
| `CAND_MAX_KV`, `CAND_MIN_KV`, `LOW_BUS_MAX_KV`, `OSM_ONLY_MAX_KM`, `LOAD_CENTRE_MIN_FACILITIES` | 138, 60, 34.5, 15, 10 | REAL / ASSUMPTION | Tier 2 candidate rules |
| `FEEDER_KW_PER_CUSTOMER` | 5.0 kW | ASSUMPTION | Coincident feeder peak per customer (bracket 3-8) |
| `DEFAULT_FAULT_SCREEN_BINDING`, `DEFAULT_REVERSE_FLOW_BINDING` | False, False | ASSUMPTION | No fault-current or transformer data: assumed not binding / not evaluated |
| `SCREEN_FEEDER_SHARE`, `SCREEN_FAULT_SHARE`, `SCREEN_SHARE_AE90`, `SCREEN_SHARE_NAMEPLATE` | 15%, 25%, 90%, 100% | REAL | 16 TAC 25.211(g)(1); Austin Energy DG guide rev 14 p.13; planner nameplate rule |
| `DEFERRAL_USD_KW_YR_LOW/MID/HIGH` | 43 / 48 / 60 | REAL | `ercot.md` 5.3 (25 non-wires studies, PA PUC 2026) |

### VoLL

`VOLL_USD_MWH = 35,685 $/MWh` is the one-hour, ERCOT-wide value of lost load from the Brattle study "Value of Lost Load Study for the ERCOT Region" (filed in PUCT Project 55837, Aug 2024). I read the PDF: footnote 1 states "The one-hour, system-wide VOLL for the ERCOT Region yielded by the VOLL survey is $35,685 per MWh".
- ERCOT recommended using about $35,000/MWh for planning, including the reliability standard. Utility Dive reports that the PUCT adopted the Brattle analysis at its 29 Aug 2024 open meeting. I did not read the order (UNVERIFIED).
- The study values longer outages lower per MWh: 1 h $35,685, 2 h $21,326, 4 h $13,340, 8 h $10,435, 16 h $13,581 (system-wide); residential $3,964 for 1 h falling to $2,039 for 4 h.
- `ercot.md` quotes "$35,685/MWh system, about $4,000 residential"; the study confirms both.
- `voll_usd_yr_headline` prices every avoided MWh at the one-hour value (an upper bound). `voll_usd_yr_duration` prices it at the duration the battery actually covers (1 to 4 h). Show both.

### EAGLE-I and the unserved-energy estimate

- **Series.** `customers_out` every 15 minutes per county (the 2023+ files call the column `sum`). Timestamps are UTC; the derecho peak at Waller County reads 01:45 UTC on 17 May, which is 20:45 CDT on the 16th. Missing intervals count as zero, and explicit NA snapshots (2017-2021) carry the last value up to 3 h.
- **Events.** A run of snapshots with at least max(25 customers, 1% of the county) out, gaps under 2 h merged. Per event: peak customers, customer-hours, duration.
- **Per county:** `cust_hours_yr` (customer-hours out per year, 2018-2025 mean), `cust_hours_per_cust_yr` (not IEEE SAIDI: no cause screening, reported outages only), `p95_event_peak_customers/pct`, `events_per_yr`, and for each named event the peak, customer-hours and mean outage hours per affected customer (customer-hours / peak).
- **Denominator check.** EAGLE-I reports exceed MCC customers in many counties because some utilities report at system level or under another county (for example 121k customers out in Waller County, which has 18.5k). A county is `artifact` when the 99.9th percentile of its reports exceeds 1.5 times its customers. In the others reports are clipped at max(customers, p99.9). The artifact also means Houston-area county shares in the derecho and Beryl are noisy; Harris (91% in Beryl) reads plausibly.
- **Check against known totals.** Summing county peaks over the valid counties gives 4.58 M customers for Uri (ercot.md: 4.5 M), 0.91 M for the derecho (about 0.92 M) and 2.86 M for Beryl (2.26 M CenterPoint; peaks are not simultaneous and some counties double count).
- **Avoided energy per event (DERIVED).** A random site in the county is out with probability f = peak / customers. Its outage lasts d = customer-hours / peak. A 1 MW / 4 MWh battery serving a 1 MW critical load covers min(1 MW x d, 4 MWh x 0.9 x SOC). Avoided energy = f x that. The annual value is the sum over events divided by 8 years. Events below 1% of customers are left out, so the figure understates exposure. Full charge and an always-present 1 MW load overstate what a market-dispatched battery would do, which is why the `lowsoc` bound is given.

### Network weakness

- **Graph.** OSM edges >= 69 kV from `lines.csv` whose endpoints lie in counties that contain an ERCOT-matched substation (phase 1c warned the OSM graph also holds SPP, WECC and MISO lines). Parallel lines between one pair become one corridor; circuits are kept as an attribute.
- **Backbone and core.** Backbone = nodes touching a >= 345 kV edge (682). The core is the 2-edge-connected block holding the most backbone nodes.
- **Per substation:** `paths_345` (edge-disjoint corridors to the backbone, capped at 3, by max-flow), `paths_345_circuits` (each corridor counted as min(circuits, 3)), `paths_138` (to the >= 138 kV network), `behind_bridge`, `n_bridges_to_core`, `bridge_circuits`, `pocket_first_nodes` and `pocket_outer_nodes` (substations cut off if the first or the outermost bridge fails), `pocket_outer_subs` (ERCOT subs at confidence >= 0.5 in that pocket).
- **Mapping-gap check.** `behind_bridge_with_hifld` repeats the analysis after adding HIFLD lines OSM lacks. 146 of 618 bridges disappear. The pocket score halves the size part of an unconfirmed bridge, and halves it again for a double-circuit bridge (survives the loss of one line).
- **Islands.** 16 nodes sit in a piece of the graph with no route to the core. That is a mapping or footprint gap, so they get no pocket score.
- **Score.** `pocket_score` = 0.5 x thinness (1 circuit-path to the backbone = 1, 2 = 0.5, 3+ = 0) + 0.5 x min(1, pocket size / 8) x the two bridge factors.

### Critical facilities

Counts within the three radii and the distance to the nearest of each, from OSM objects merged within 150 m. `facility_score` is the percentile rank of the weighted count among placed subs, so it spreads rather than saturating. Dense cities score near 1. OSM undercounts rural clinics and water plants.

### Combined score

`reliability_need_score` (0-100) is the weighted mean of the sub-scores that exist, with the weights renormalised:

| Sub-score | Weight | Built from |
|---|---:|---|
| `outage_score` | 0.30 | Percentile ranks across the 215 valid counties: hours per customer (.5), p95 event share (.25), worst named-event share (.25) |
| `pocket_score` | 0.25 | Network weakness above |
| `facility_score` | 0.20 | Critical facilities above |
| `oncor_score` | 0.10 | Customer-weighted SAIDI and SAIFI of the sub's Oncor feeders, each over the Oncor p90 (160 min, 1.75); Oncor subs only |
| `large_load_score` | 0.15 | Batch Zero MW in the county (own county plus half of other listed counties within 50 km), over 5 GW |

All weights are ASSUMPTIONs. The five sub-scores sit in their own columns so a reader can re-weight. `need_rank` ranks the 2,554 `rank_eligible` subs (site-level, >= 69 kV, with outage, pocket and facility components). `main_driver` is the component whose weighted contribution most exceeds the typical contribution, not simply the largest weight.
Large-load data is county-level only for the top-5 and RPG study counties, so most counties read 0, meaning unknown rather than no growth. The Batch Zero deck's weather-zone totals are not used because the repo has no county-to-weather-zone map.

## Top 20 by reliability need

Sub-scores are outage / pocket / facilities / Oncor / large load. All Harris rows share a county outage score (0.66) and county large-load score (1.00), so only pocket and facilities separate them. Harris hospital, water-works and emergency counts: BU 3/14/8, LK 5/16/7, OA 2/16/7, JES 8/54/3, MAR 8/47/5, FAN 23/11/9, HP 26/10/20, MID 25/10/21.

| Rank | Sub | County | Zone | kV | Score | Sub-scores | Why |
|---:|---|---|---|---:|---:|---|---|
| 1 | BU | Harris | LZ_HOUSTON | 138 | 75.4 | .66 / .59 / .90 / - / 1.0 | Behind 1 bridge, 3-sub pocket, single circuit (not confirmed by HIFLD); 3 hospitals; Beryl took out 91% of county customers; 6.9 GW Batch Zero load in county |
| 2 | LK | Harris | LZ_HOUSTON | 138 | 74.3 | .66 / .53 / .93 / - / 1.0 | Behind 1 bridge, single circuit (not confirmed by HIFLD); 5 hospitals |
| 3 | OA | Harris | LZ_HOUSTON | 138 | 74.1 | .66 / .55 / .90 / - / 1.0 | Behind 2 bridges, 3-sub pocket (not confirmed by HIFLD); 2 hospitals |
| 4 | HRTN | Jones | LZ_WEST | 138 | 69.1 | .70 / .56 / .60 / - / 1.0 | Behind 1 bridge, single circuit; 90% of county out in the worst named event; 7 GW of large load in county |
| 5 | JES | Harris | LZ_HOUSTON | 138 | 68.7 | .66 / .28 / 1.0 / - / 1.0 | Double-circuit bridge (confirmed by HIFLD); 8 hospitals, 54 water works within 10 km |
| 6 | TORT_ESS | Brazoria | LZ_HOUSTON | 138 | 68.4 | .71 / .56 / .56 / - / 1.0 | Single-circuit bridge; county 100% out in a named event, 28 customer-hours per customer-year; 2.4 GW large load |
| 7 | MAR | Harris | LZ_HOUSTON | 138 | 68.3 | .66 / .27 / 1.0 / - / 1.0 | Double-circuit bridge (not confirmed by HIFLD); 8 hospitals, 47 water works |
| 8 | FAN | Harris | LZ_HOUSTON | 138 | 68.2 | .66 / .27 / .99 / - / 1.0 | Double-circuit bridge (not confirmed); 23 hospitals within 10 km |
| 9 | HP | Harris | LZ_HOUSTON | 69 | 67.9 | .66 / .25 / .99 / - / 1.0 | 2 circuit-paths to the backbone; 26 hospitals, 20 emergency stations |
| 10 | MID | Harris | LZ_HOUSTON | 138 | 67.9 | .66 / .25 / .99 / - / 1.0 | 2 circuit-paths; 25 hospitals, 21 emergency stations |
| 11 | BG | Harris | LZ_HOUSTON | 138 | 67.8 | .66 / .25 / .99 / - / 1.0 | 2 circuit-paths; 21 hospitals |
| 12 | BH | Harris | LZ_HOUSTON | 69 | 67.5 | .66 / .25 / .98 / - / 1.0 | 2 circuit-paths; 18 hospitals |
| 13 | DH | Harris | LZ_HOUSTON | 138 | 67.0 | .66 / .25 / .96 / - / 1.0 | 2 circuit-paths; 6 hospitals, 23 water works |
| 14 | TRV | Jefferson | LZ_HOUSTON | 138 | 66.6 | .69 / .88 / .86 / - / 0 | Behind 3 bridges, 6-sub pocket, single circuit; 34.5 customer-hours per customer-year |
| 15 | WALKER | Taylor | LZ_WEST | 138 | 66.3 | .75 / .56 / .63 / - / .70 | Single-circuit bridge; county 100% out in a named event |
| 16 | TLR | Harris | LZ_HOUSTON | 138 | 66.2 | .66 / .25 / .92 / - / 1.0 | 2 circuit-paths; 21 water works |
| 17 | DPO | Harris | LZ_HOUSTON | 138 | 66.1 | .66 / .25 / .92 / - / 1.0 | 2 circuit-paths; 20 water works |
| 18 | NA | Harris | LZ_HOUSTON | 138 | 66.1 | .66 / .27 / .90 / - / 1.0 | Double-circuit bridge (not confirmed); 8 hospitals |
| 19 | SOLARA | Haskell | LZ_WEST | 138 | 66.0 | .85 / .62 / .17 / - / 1.0 | Behind 2 bridges, single circuit; county 97% out in a named event; 2.7 GW large load; no facilities mapped nearby |
| 20 | BFM | Harris | LZ_HOUSTON | 138 | 65.2 | .66 / .27 / .86 / - / 1.0 | Double-circuit bridge (not confirmed); 3 hospitals |

The list is Harris-heavy (15 of 20) because Harris carries high outage, facility and large-load scores at once, and county-level data cannot separate its substations.
**Sensitivity of the top 20** (overlap with the base list; Spearman correlation of the scores over the 2,554 eligible subs):
- equal weights: 17 of 20 overlap, rho 0.96;
- no large-load component: 8 of 20, rho 0.90;
- outage only: 0 of 20, rho 0.61;
- network only: 1 of 20, rho 0.25.

So the broad ordering is stable but the exact top 20 is not. Use `need_rank` as a screen, not an exact ranking.
For a more varied shortlist, sort `reliability.csv` by `need_rank` within a load zone, or by `pocket_outer_nodes` where `behind_bridge_with_hifld` is True (the confirmed radial pockets).

## Physical risk flags

- **Site-level checks** run for the 2,869 NP4-160 subs with `match_conf >= 0.5`, a coordinate and `kv_max >= 69` (resource-node subs first). `risk_basis` is `site` (2,869), `county` (764: storm counts only) or `none` (1,402 with no county).
- **Flood.** Zone at the point; if the 100 m buffer returns several zones the point is queried again for the true one. `flood_flag`: high (SFHA zones A, AE, AH, AO, AR, A99, V, VE; 140 subs), moderate (shaded X; 147), minimal (1,758), unmapped (824: no NFHL polygon, 80% of LZ_WEST), undetermined. `flood_sfha_within_100m` flags 393 subs with a special flood hazard area within 100 m (including the 140 inside one).
- **Wildfire.** Class at the site and the maximum burnable class on an 8-point 600 m ring, so a city substation reads its surroundings rather than "non-burnable". The legend (1 very low to 5 very high, 6 non-burnable, 7 water) was checked against a lake (7), city centres (6) and Palo Duro (5). 733 subs without a site check take the most severe class among their county's site-level subs (`wildfire_basis = county_site_max`).
- **Storm history.** County counts 2000-2025 of hurricane/tropical/surge, tornado, winter storm and ice, flood, thunderstorm and high wind, extreme cold/heat, wildfire records, and total property damage. Harris: 14 hurricane records, 79 tornadoes, 201 floods, $20.6 B damage.
- **Coast.** Distance to the OSM coastline (bays included); `coastal_flag` within 25 km (374 subs).
- **Seismic.** Events M >= 3 within 25 km since 2015 and the maximum magnitude; `seismic_flag` at 3 or more events (453 subs). It is not only Permian. Of the 3,562 M >= 3 events in the box, about 994 lie in the Delaware Basin (Reeves, Pecos, Culberson, Ward, Loving) and 197 around Midland, Scurry and Howard; a cluster of 15 events near Irving and Azle (largest M4.0) flags 121 Dallas County subs. Subs flagged: Dallas 121, Reeves 35, Scurry 34, Midland 27. It never excludes.
- **Heat and cold.** Days a year at or above 100 F and at or below 20 F at the nearest of the arena's eight airports. Coarse: use as a regional marker.
- **Exclusions** (first rule that trips): floodway 11, flood_sfha 129, wildfire_very_high 7. Total 147 of 2,869 (5.1%). Subs not site-assessed get `exclude_flag` blank and `exclude_reason = not_assessed` (not "allowed").

## Tier 2

### Candidates

1. **NP4-160 subs** with `kv_max` between 60 and 138 kV and evidence they serve load. NP4-160 has no load flag (every bus carries a load zone), so evidence is any of: a PSS/E `L_` bus, a non-generator bus at 34.5 kV or less, or an OSM `substation=distribution` match. A 34.5 kV bus at a site with a resource node does not count (it is a collector bus). 2,024 candidates, 1,632 of them at confidence >= 0.5.
2. **OSM-only distribution substations** not linked to any ERCOT sub, within 15 km of a placed sub with a settlement bus, with weighted critical-facility count >= 10 as the "near a load centre" test: 567 candidates. They carry county-level outage and facility scores only.

### Settlement routes (from `notes/rules.md` section (a))

| Route | Settles at | Column | Candidates |
|---|---|---|---:|
| DESR / separately metered storage (nodal) | Nearest transmission bus. For an NP4-160 candidate its own highest-voltage bus (`own_hv_bus`); for OSM-only, the nearest placed sub within 15 km (`nearest_placed_sub`) | `route_desr_nodal_sub`, `_bus`, `_kv`, `_dist_km` | 2,024 own bus + 567 nearest sub = 2,591 |
| ADER fleet (home batteries) | Load-zone price; NOIE zone where the bus belongs to a municipal or co-op (LZ_AEN, LZ_CPS, LZ_LCRA, LZ_RAYBN) | `route_ader_zone` | 2,591 (304 in NOIE zones) |
| Settlement-only distribution storage, 10 MW or less | Load-zone price while storage is over 50% of nameplate; nodal after NPRR995 | `route_sodg_storage_zone` | 2,591 |

By settlement zone: LZ_NORTH 852, LZ_SOUTH 616, LZ_WEST 510, LZ_HOUSTON 309, LZ_LCRA 154, LZ_CPS 85, LZ_AEN 54, LZ_RAYBN 11.
Separately metered storage is exempt from 4CP transmission charges and pays the nodal energy price at the bus (rules.md (c); 16 TAC 25.192, 25.501(m)(2)). SB 6 requires the PUCT to review 4CP by 31 Dec 2026, so do not rely on the exemption past 2026.
**UNVERIFIED:** which transmission bus a given feeder maps to; it sits in each DESR's registration data (rules.md). The nodal-value columns (`nodal_value_usd_kw_yr`, `nodal_value_p10_usd_kw_yr`, `nodal_vs_zone_uplift_usd_kw_yr`, `nodal_value_source`) are empty on purpose; Phase 6 fills them by joining `route_desr_nodal_sub` to Phase 3's `data/sites.csv`.

### Hosting screen

- **Rules applied.** 16 TAC 25.211(g)(1): a pre-certified unit of 500 kW or less that exports at most 15% of feeder load and contributes at most 25% of fault current pays no study fee. Austin Energy: all DG kW AC at most 90% of the transformer rating (applied in LZ_AEN; elsewhere the planner's nameplate rule). Neither is a refusal threshold: a larger battery needs a study.
- **Inputs that exist.** Oncor feeders and customers per feeder for 344 candidates (REAL).
- **Defaults (ASSUMPTION, named).** For the other 2,247: 4 feeders per substation and 1,050 customers per feeder (the Oncor medians), 5 kW coincident peak per customer. That gives a 5.25 MW feeder, a 0.79 MW per-feeder screen and 3.15 MW at the substation. Fault current is assumed not binding; reverse flow at the substation transformer is not evaluated. For scale, PG&E's published feeder ratings have a median of 11.7 MW (DATA-GRID-ASSETS.md), so the default feeder peak is probably low and the screen conservative.
- **Service transformers.** `cores_per_25kva/50/75` (floor of share x kVA / 20 kW): 1, 2, 3 for a 25, 50 and 75 kVA unit under both rules.

### Deferral value and home-fleet value

- Deferral: $43 / $48 / $60 per kW-yr (low / mean / high, REAL, `ercot.md` 5.3). It applies only where a utility documents a need and runs a request for proposals (SB 415 / proposed 16 TAC 25.58). It is not a site quote.
- Home-fleet value comes from the arena's forward tournament (A5 dispatch, median 2027-29, $/kW-yr, label SIM; parsed from `arena_results.json`). Contract terms behind the programme values are arena ASSUMPTIONs.

| Zone | Merchant | Best programme |
|---|---:|---|
| LZ_AEN | 27.6 | Austin Energy tolling 102.0 |
| LZ_NORTH | 26.6 | Data-center flexibility 82.0; SB 415 feeder contract 59.3 |
| LZ_LCRA | 29.3 | TX co-op peak 81.9 |
| LZ_RAYBN | 29.1 | TX co-op peak 76.5 |
| LZ_HOUSTON | 32.5 | SB 415 feeder contract 60.7 |
| LZ_SOUTH | 28.3 | none modelled |
| LZ_CPS | 25.1 | none modelled |
| LZ_WEST | 24.5 | none modelled |

### From transformer to feeder: how `sim/planner.py` would scale if a utility shared its feeder model

`sim/planner.py` works one service transformer at a time. For each it computes a paper cap (floor of share x kVA / Core kW, share 100% nameplate or 90% Austin Energy), a naive cap (the largest number of Cores, all following the same price signal, with no battery-caused loading above 110% for 30 minutes), a feeder-aware cap (each Core still earns at least 90% of an unconstrained Core and no battery-caused event), a one-size-up cap, and `upgrade_ranking` (members unlocked in 5 years x member value against the $10,000 swap). A fast surrogate screens and OpenDSS judges (`sim/referee.py`). I did not run OpenDSS for this phase.

With a real feeder model (a CYME, Synergi or OpenDSS export, or the TDSP's own pre-screen results) the same method scales in five steps:
1. **Map and replace the SIM inputs.** Link customers to service transformers, sections, feeder and substation transformer (the meter-to-transformer map is the TDSP's). Replace SMART-DS kVA, phase and age with real values; the transformer layer needs no new physics.
2. **Keep the per-transformer sweep.** Run it on every transformer. It is cheap and cached, and it also yields each transformer's battery schedule.
3. **Add the feeder layer, judged by OpenDSS.** Run the schedules for the representative days through OpenDSS on the real topology and test: conductor ampacity and voltage drop per section, regulator and tap state, the protection limit (25% of fault current), reverse flow through the substation transformer, and the 15% of feeder load fast-path test. Limits interact, so the feeder cap is not the sum of transformer caps. Use the greedy "aware order" that `sim.siting.simulate` already uses on SMART-DS (add homes in order of marginal headroom until the first feeder-level violation; the feeder-head cap there is 370 A).
4. **Turn `up` into a ladder.** Rungs: service transformer swap ($10,000 Base figure; NREL 2017 installed $3.9-6.1k), regulator or recloser, reconductor (per mile, from the unit-cost database), substation transformer. `upgrade_ranking` then ranks rungs by MW unlocked per dollar. Convert to $/kW-yr as the arena does ($121/kW over 12 years at 8% is $16.1/kW-yr).
5. **Roll up to the substation.** Sum feeder caps under the substation reverse-flow and 15% rules. That replaces `screen_15pct_mw_substation` in `tier2.csv`, and the ASSUMPTION columns (`feeder_peak_mw_est`, fault current, reverse flow) become computed.

Nothing in 1-5 needs a language model. The binding gap is data: no Texas utility publishes feeder loading, ratings or fault current (Oncor argued against publishing a hosting-capacity map in PUCT 58306), so `tier2.csv` carries defaults that a shared model would overwrite column by column.

## Caveats

1. **County resolution.** EAGLE-I is county-level, so every substation in a county shares the outage score. Within a county only pocket and facility counts separate subs.
2. **EAGLE-I quality.** 39 counties are blank (artifact reports) and 21 are `inflated`. Houston-area derecho and Beryl shares in neighbouring counties are noisy because CenterPoint-area reports are partly misallocated (Brazoria reports reach 2.2x its customers). EAGLE-I counts reported outages only, with no cause or planned/forced split.
3. **EUE and dollars are upper and lower bounds at once.** Upper: full charge, a 1 MW critical load that is always present, the one-hour VoLL on every MWh. Lower: events under 1% of customers are omitted, and one outage per affected customer is assumed. The duration-adjusted dollars are roughly half the headline. These are rough DERIVED figures for ranking and for sizing the question, not a benefit-cost result.
4. **Radial pockets come from OSM.** No ratings or impedances exist, and OSM misses some lines (146 of 618 apparent bridges disappear with HIFLD). A degree-1 node may be a real radial tap or a mapping gap. Coordinates for plant-matched subs are the plant, not the fence (phase 1c). The county footprint filter keeps border counties that may include non-ERCOT lines.
5. **Critical facilities** are OSM objects, so rural coverage is thin and cities saturate.
6. **Oncor data** is 2023, forced interruptions only, Oncor substations only (347 placed subs), joined by exact code (736 of 774 codes match; a reused code in another TDSP would be a false join).
7. **Large-load component** uses only the counties ERCOT publishes; most counties read 0 for "unknown".
8. **Flood.** Missing NFHL data is not "low risk". A point query misses flooding next to the fence; the 100 m buffer flag helps. Plant-matched coordinates can be kilometres from the substation.
9. **Seismic flag** reflects events within 25 km, so it also flags the DFW and Scurry clusters, not only the Permian.
10. **Tier 2 candidates are a proxy.** NP4-160 gives no load flag, and OSM `distribution` is volunteer-tagged. Hosting numbers for 87% of candidates come from defaults. Home-fleet values are arena SIM outputs with ASSUMPTION contract terms; the programmes are not offered in every zone.
11. **VoLL adoption.** The study figure is REAL (read in the PDF). That the PUCT adopted it on 29 Aug 2024 comes from Utility Dive via search; the order was not read.
12. **Top-20 stability** is low under alternative weights (above).

## Hand-off

- **Phase 3 (site value)** can use `risk_flags.exclude_flag` / `exclude_reason`, the flags per `sub`, and `reliability.reliability_need_score`, its five sub-scores and `voll_usd_yr_*` for the reliability column. Keep market and reliability value separate.
- **Phase 6** joins `tier2.route_desr_nodal_sub` to `sites.csv` to fill the nodal columns.
- Join keys: `sub` is the NP4-160 `SUBSTATION` code (blank for 567 OSM-only Tier 2 rows, which use `cand_id` = OSM id).
