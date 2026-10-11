# Phase 2: network, shift factors, constraint table, hops, map (10 Oct 2026)

Agent 2. Code: `gridsite/network.py` (aliases, line graph, DC power flow, hops), `gridsite/shiftfactors.py` (constraint table,
regressions, validation, proxy, hops tables), `gridsite/cli_2.py` (commands). Run order from `docs/research/grid/` with the research venv:
`run_grid.py constraints`, `shift-factors`, `physics-proxy`, `hops`, `map` (flags are plain words: `shift-factors fast` skips the sensitivity runs,
`constraints rebuild-graph` rebuilds the graph). Runtimes: 25 s, 7.5 min (4 min with `fast`), 40 s, 80 s, 5 s.

Sign convention (PLAN.md), used everywhere: `cong[b,t] = LMP[b,t] - lambda[t] = - sum_k SF[b,k] * mu[k,t]`, `mu` the published shadow price.
**SF < 0 is the import side** (the price rises above lambda when the constraint binds, so a battery discharges into it).
**SF > 0 is the export side** (the price falls; injecting there loads the constraint, so discharge is limited when it binds).

## Gate B summary

1. **Constraint table:** 1,386 rows (1,362 bind in the RT week or the 31 DAM days; 24 never priced, 12 of them structural IMM elements); a 2025 IMM rent is attached to 12 elements (5 bind in the window, 7 are structural-only); priority = 50% structural rent + 50% window rent.
2. **RT shift factors fit well:** 63 constraints, 1,593 SCED runs x 19,493 buses, last 24 h held out: median out-of-sample R2 **0.978**, RMSE 1.09 vs 10.4 $/MWh for "no congestion"; 99.4% of buses R2 > 0.5; at the 1,043 resource nodes R2 0.980.
3. **DAM shift factors are weaker:** 477 constraints, last 5 days held out: median R2 **0.756**, RMSE 4.7 vs 13.0; 25% of buses below 0.5; 9.5% of the held-out shadow-price mass came from constraints unseen in training.
4. **RT vs DAM (44 constraints in both):** median correlation across buses 0.64 (0.76 where neither fit is ridge-attenuated); 0.96-0.99 for the constraints that bind a lot (`6437__F`, `6429__D`, `DT_PK_91_A`, `BLESSING_1382`).
5. **Coverage of the 1,386:** regression SF 496, proxy only 476, nothing 414. Top 80 by priority: 71 / 6 / 3. The 12 structural-only elements: 7 proxy only, 5 nothing (Odessa EHV-Yarbrough, Laredo VFT, Morgan Creek-Navigation, South Texas Export GTC, Coleto-Rosata).
6. **Physics proxy is poor:** median correlation 0.38 against 119 unattenuated regression SFs (0.65 against the 26 RT ones), sign agreement 96-98%, N-0 only, no transformers, no GTCs. Below the 0.5 line: all 718 proxy SFs are marked low confidence, none dropped.
7. **Collinearity:** no shadow-price pair reaches 0.90, so nothing was merged; 15 DAM pairs at 0.80-0.90 are reported; 28 RT and 869 DAM constraints bind too rarely to fit (SF = NaN).
8. **Hops:** 2,870 placed substations x the top 40 constraints; 2,822 are on the graph; the median substation is 5 hops from its nearest priority constraint; 6,881 pairs carry the battery-curtailment flag (export side, SF >= 0.05).
9. **Placement errors found:** 20 constraint rows have two ends more than 80 km apart (HIGH36-BRENNO, COLLEGE with BRAND_C and FAIRDALE, UNIVERSI-GATEETP, ...); both ends are dropped. Agent 1c should look at them (list in section 8).
10. **Biggest bias:** one outage-driven shoulder week. SFs are topology so they transfer; which constraints bind does not: 5 of the 10 largest 2025 IMM rents never priced in the window and 2 (Twin Buttes-Hargrove, WESTEX) priced only day-ahead.
11. **Planned upgrades matched at 0.5 or above among the top 10:** Bell County-Salado (TPIT-87673, May 2027), Longshore-Consavvy (TPIT-81268, May 2028), SALDS-Sonterra (TPIT-80546D, May 2027), Twin Buttes-Hargrove (RTP line upgrade identified, no date), WESTEX (PBRP 765 kV paths, approved, 2031); Scurry Chevron-Knapp has none.
12. **Map:** `map/substations.html` (Leaflet 1.9.4, opens as a local file): 3,633 substations by match confidence, 60 constraint lines, SF and hop layers for 40 constraints.
13. **Verdict:** usable for Phase 3 with limits: use `rt_reg` first, treat `dam_reg` tagged `+shrunk` as sign-only, never rank sites on `dcpf_proxy` magnitudes.

## 1. Station aliases (`data/station_alias.csv`, 126 rows)

**Problem.** 85 station names in the constraint reports are not NP4-160 `SUBSTATION` codes (taps, switches, POIs: `V4_TAP`, `ENCRT`, `GANSO`, `BELDTAP`, ...), and the IMM names
use five more spellings that exist nowhere in NP4-160 (`KOCHTAP`, `BLESSI`, `PHBL_TAP`, `DILLEY`, `ROSATA`). The 126 rows are those 85 + 5, plus 36 NP4-160 stations that agent 1c could not place and
that were placed by graph neighbour (below).

**Method (all deterministic; weights are named constants).** Evidence for "this station is the same yard as NP4-160 substation S", combined by noisy-OR:
- `stem_exact` / `stem_short`: the station code with trailing digits and tap/switch endings (`TAP, TP, SW, POI, SS, ...`) removed equals S (4+ letters, else the short weight).
- `psse_name`: the name is the stem of a PSS/E bus name, electrical bus name or node name of exactly one NP4-160 substation (`MCPOI` appears in `SAND_MCPOI`, a `SANDLAKE` bus).
- `osm_shared`: agent 1c linked the station and S to the same OSM substation, and the two codes also look alike (difflib >= 0.5).
- `prefix` and `fuzzy` (difflib >= 0.85).
- Each candidate is checked against kV (an S with no bus at the station's kV is halved), then against the graph: within 3 hops of the station's partner on a constraint line
  confirms it (x1.15), more than 60 km from a placed partner contradicts it (x0.4).
- `graph_neighbor`: a station with no node at all (extras, or NP4-160 stations that are unplaced) that is the only unclaimed graph neighbour, at the right kV, of its placed
  partner gets that node with confidence 0.40. Longshore-Consavvy relies on this (`CONSW`).

**Result.** Of the 85 extra stations, 31 are aliased to an NP4-160 substation (23 at confidence >= 0.5), 9 are placed by graph neighbour and 46 have no alias (`ENCRT`, `LAKENASW`, `CATSW`, ...); for those 46 the
placement agent 1c gave the extra row (39 of 85 have one) is used if it has one. Examples: `BELDTAP`->`BELD` 0.95, `DEVINESW`->`DEVINE` 0.95, `FERRISW`->`FERRIS` 0.90,
`KOCHTAP`->`KOCH` 0.86, `MCPOI`->`SANDLAKE` 0.69. The table carries an evidence string per row. Weakest rows: `BRAND`->`BRAND_C` (0.45),
`HIGH36`->`HIGHLAND` (0.60, probably wrong: the constraint it sits on fails the 80 km test), `ELARA`->`PALODURO` (0.69). Nothing was checked against a TSP station list (not public).

## 2. Constraint table (`data/constraints.csv`, 1,386 rows x 71 columns)

One row per `cid` seen in RT or DAM (1,374) plus structural IMM elements that never appeared (12, `cid = <code>|UNSEEN`; no contingency is public). Columns as briefed:
element (from/to, kV), `element_type` (line 1,137, xfmr 231, gtc 17, other 1), aliased `from_sub`/`to_sub` and graph nodes, RT window (runs binding, hours, mean/p95/max shadow,
`rt_rent_proxy` = sum of shadow x 5 min in $/MW, plus `rt_rent_est_musd` = sum of shadow x |flow| x 5 min), DAM window (same with 1 h), median limit and flow, GTC flag, annual rent,
planned upgrade, priority, and regression status columns (`sf_rt*`, `sf_dam*`, `sf_best`, `sf_proxy`). Windows: RT is every SCED run in `shadow_sced` (3-10 Oct plus the 25 Sep archive day,
2,442 runs, about 8.5 days); DAM is 10 Sep - 10 Oct. RT hours assume 5 minutes per run (named constant, same as phase 1a).

**Annual rent join (element level, never summed across markets).** Method recorded in `annual_join`, for the 28 IMM elements that reach the table (33 distinct elements over 2024-25; 21 have 2025 rows, 12 of those a 2025 rent):
`code` (IMM code equals `CONSTRAINT_NAME`, e.g. `6437__F`) 10 elements; `gtc_code` (IMM parenthetical equals the GTC code, e.g. `WESTEX`) 5; `station_pair_kv` 1 (STP-W.A. Parish is `STP-WAP LINES` in the IMM table, a different spelling from the SCED code);
`structural_only` 12 (no cid in the window; includes Vealmoor-Koch Tap `15060__B`, whose IMM code never appears in the window). `annual_rent_2025_musd` is the RT value where the element is in the RT top 10, else the DA value
(`annual_rent_market` says which). An element with several contingency cids repeats its rent on each; `annual_rent_alloc_musd` splits it by each cid's share of window rent so sums are safe
(Twin Buttes-Hargrove has 6 cids, `6437__F` 12). `annual_binding_hours` is the IMM 2025 figure where the element is in the IMM frequency top 10.

**Priority score** (named constants `W_STRUCT = W_CURRENT = 0.5`, `W_CURRENT_RT = W_CURRENT_DAM = 0.5`; label ASSUMPTION):
`priority = 100 x (W_STRUCT x S + W_CURRENT x C)`, `S = annual_rent_alloc / max over the table` (structural, REAL IMM 2025 rent),
`C = W_CURRENT_RT x rt_rent_est / max + W_CURRENT_DAM x dam_rent_est / max` (current, window). RT and DAM are normalised separately and never added in dollars. So a never-binding
structural element can score at most 50 (Vealmoor-Koch Tap scores exactly 50) and a window-only constraint at most 50.

**Top 10 by priority**

| # | constraint | element | window | 2025 IMM rent | planned upgrade (match) |
|---|---|---|---|---|---|
| 1 | `6437__F`, DMTSCOS5 | Scurry Chevron-Knapp 138 kV | RT 621 runs, DAM 455 h | $68.1 M | none found |
| 2 | Vealmoor-Koch Tap | 138 kV | did not bind | $211.4 M (RT) | none found |
| 3 | `HARGRO_TWINBU1_1`, DBAKCED5 | Twin Buttes-Hargrove 138 kV | DAM 307 h | $145.0 M (element) | RTP2025-2022-WFW01 line upgrade, rtp_identified, no date (pair_title 0.8) |
| 4 | WESTEX | West Texas Export GTC | DAM 67 h | $89.1 M | PBRP 765 kV import paths 1-3, approved, 2031 (document link 0.7) |
| 5 | `215T215_1`, SGIDLIN8 | HIGH36-Brenno 138 kV | DAM 126 h | none | TPIT-78169 planned 2026-09-04 (one_end 0.30, weak); endpoints rejected, see gate item 9 |
| 6 | `DT_PK_91_A`, DWO5_EU8 | PK-DT 138 kV | RT 391 runs | none | TPIT-64499 Hyde Park-Downtown Ckt 32A upgrades, planned 2026-12-31 (one_end 0.30, weak) |
| 7 | Longshore-Consavvy | 345 kV | did not bind | $94.6 M | TPIT-81268 345 kV rebuild, planned 2028-05-15 (pair_bus 0.70) |
| 8 | Bell County-Salado | 138 kV | did not bind | $94.2 M | TPIT-87673 rebuild, planned 2027-05-15 (pair_name 0.90) |
| 9 | `6429__D`, DMTSCOS5 | ENCRT-BRAND 138 kV (ENCRT has no placement) | RT 428 runs | none | none found |
| 10 | `1710__A`, DSALHUT5 | SALDS-SONTERRA 138 kV | RT 113 runs | none | TPIT-80546D Hutto/Round Rock-Salado 138 kV upgrade, planned 2027-05-15 (pair_name 0.90) |

**Planned-upgrade match** (`upgrade_*` columns): TPIT/RTP/RPG rows with `project_type` in reconductor, new_line, new_xfmr, upgrade_sub. Projects are resolved to NP4-160 substations by their
PSS/E bus numbers (`sswg_bus_numbers`), their terminal names and the station names in their title; equipment-only titles (breaker, capacitor, reactor) and reconductor/new-line projects that do not read like
a line are skipped. Confidence by method: `pair_name` 0.90 (both ends are the project's terminals), `pair_title` 0.80, `pair_bus` 0.70 (both ends among a project's few buses; 0.45 if the project spans more than 4 substations),
`xfmr_sub` 0.60, `one_end` 0.30, `gtc_doc` 0.70 (WESTEX, Panhandle, McCamey, Valley and South Texas GTCs linked to named projects by the status text in `gtc.csv`). **A match is a name or bus overlap, not proof of relief**;
`hops.csv` calls a constraint "upgraded" only at 0.5 or above (331 constraints). 786 constraints have some match. Scurry Chevron-Knapp has none (agree with phase 1b).

## 3. Shift factors from prices

**Data.** `cong = lmp_bus - lambda` per SCED run (RT: 1,593 of the 1,595 priced runs; the two newest runs have no shadow rows yet, and one of them no lambda either) and `lmp_dam_bus - lambda_dam` per hour (744; 19,513 buses,
154 of them exist for only part of the month and are fitted on the hours they have). `X` is the shadow price of each cid per run; a run with no shadow rows is dropped, never read as zero.

**Model.** Multi-output ridge, closed form, no intercept: `SF = -(X'X + lambda I)^-1 X'cong`, one solve for all buses. RT keeps constraints that bind in at least `MIN_BINDING_RUNS = 10` runs (63 of 112),
DAM at least `MIN_BINDING_HOURS_DAM = 12` hours (477 of 1,346). The ridge penalty is absolute (not scaled by column) so a constraint with little binding evidence is shrunk toward zero; a column-scaled version was tried and
gave DAM out-of-sample R2 near zero. The penalty comes from blocked cross-validation inside the training rows (RT: flat from 10 to 1e5, 1e4 chosen, the fit is nearly OLS; DAM: 1e3). The top 80 constraints by priority get
a lighter penalty (`RELAX_ALPHA = 1e3`) because ridge attenuates rarely binding SFs: WESTEX binds 67 DAM hours at about $3.5, and under the penalty cross-validation picks when no constraint is favoured (3e4) its SF would be
6% of its size; with the final settings it is 65%. (In the final DAM fit the cross-validated penalty also came out at 1e3, so there the relaxation changes nothing by itself; in RT it lowers the penalty on the 34 priority constraints from 1e4 to 1e3.)
The attenuation of every SF is reported as `shrink = ss/(ss+lambda)`; below 0.8 the SF is tagged `+shrunk`, which means the sign is usually right and the magnitude is understated
(RT: 55 of 63 constraints are at or above 0.8; DAM: 233 of 477).

**Validation (holdout = the last 24 h of RT runs, 291 runs; the last 5 DAM days, 120 hours).**

| | RT | DAM |
|---|---|---|
| constraints fitted / candidates | 63 / 112 | 477 / 1,346 |
| median out-of-sample R2 per bus | **0.978** | **0.756** |
| 10th / 90th percentile R2 | 0.893 / 0.999 | -0.108 / 0.930 |
| buses with R2 > 0.5 | 99.4% | 74.6% |
| median RMSE, $/MWh (null = lambda only) | 1.09 (10.4) | 4.68 (12.95) |
| RMSE over all buses, RMS | 2.77 (15.9) | 12.66 (20.4) |
| shadow-price mass in the holdout from constraints unseen in training | 0.1% | 9.5% |
| resource-node buses, median R2 / RMSE | 0.980 / 1.20 | 0.762 / 4.63 |
| median in-sample R2 | 0.997 | 0.995 |
| VIF median / max | 1.8 / 7.7 | 28.8 / 437 |

Reading the DAM column: the model is right (in-sample R2 0.995) but DAM binds 1,346 constraints in 744 hours, so a held-out
day usually brings constraints the training days barely saw. DAM SFs are less reliable than RT ones for lightly binding constraints; use them for the constraints that bind for many hours.
**Resource nodes:** each resource node in NP4-160 is exactly one electrical bus, and the 5-minute node LMP (`lmp_node`, NP6-788) equals the bus LMP to better than a cent in 99.98% of the
303,513 holdout node-runs (largest difference $0.72). The SFs fitted on `lmp_bus` reproduce the observed `lmp_node` congestion with the R2 and RMSE above, so the check holds for the prices
settlement actually uses. Per-bus numbers are in `data/sf_validation.csv`.

**RT vs DAM (`data/sf_rt_vs_dam.csv`, 44 constraints fitted in both).** Correlation across the 19,493 common buses: median 0.64 (10th percentile 0.06; 32% above 0.8).
Where neither fit is attenuated (35 constraints): median correlation 0.76, regression slope DAM on RT 0.83, sign agreement on buses with |SF| >= 0.05 83%, overlap of the 20 largest-|SF|
buses 35%. Strong agreement for the constraints that bind a lot: `6437__F` 0.99 (slope 1.01), `6429__D` 0.99, `BLESSING_1382` 0.99, `DT_PK_91_A` 0.96. Weak for a few (`NLARSW_PILONC1_1|DFOAVLO5` -0.76 although both fits are unattenuated, and
`WAPWLY72_A|DBLW2JC5` 0.33): the contingency topology differs between the RT week and the DAM month, or the fit is poorly identified; treat these two as unresolved.

**Collinearity.** Detected by the correlation of shadow-price series: no pair reaches `COLLINEAR_CORR = 0.90`, so no group was merged (`data/sf_groups.csv` lists the 15 DAM pairs at 0.80-0.90, none in RT; the largest RT
correlation is 0.79, VIF max 7.7). VIF is reported and tags `+collinear` above 10 (the DAM design has 477 regressors for 744 hours, so VIF is above 10 for most of them; a VIF cut at 100 would have thrown out Twin Buttes-Hargrove, so the
only SF-to-NaN rule left for collinearity is a numerical guard at VIF 1,000, which no group reached). Constraints below the binding thresholds are NaN: 28 RT and 869 DAM cids bind but too rarely;
21 RT cids were listed by SCED but never priced in the aligned window. Merging (`collinear_groups`) was checked on a synthetic pair of near-identical series; it never triggered on the data.

**Sensitivity (RT, same holdout).** `MIN_BINDING_RUNS` 5 (72 constraints): R2 0.978, median correlation of the SFs with the base fit 0.996. 20 (54 constraints): R2 0.975, correlation 0.988.
Phase 1a asked for fits with and without the rows where the shadow price sits at its cap ($0.5 or less below `max_shadow_price`; capped prices are stored a few 1e-4 below the cap, so an exact test finds almost none): 156 of the 1,593 runs have one
(`DT_PK_91_A|DBI_GT_8` 51, `KOCH_UP_69A1` 31, `LAKENA_SAMATH1_1` 30, `COL_BRAN_1` 27, `6437__F` 18). Dropping those runs (1,437 left, 58 constraints): R2 0.972, SF correlation with the base fit 0.987. So the cap rows move the SFs a little and are kept
(the LMPs they produce are real prices, and the model reproduces them), but a SF for a constraint that is mostly at its cap is a fit to a price jump, not to a marginal value.

**Outputs.** `derived/sf_long.parquet` (6.4 M rows, 49 MB): bus, cid, sf, method (`rt_reg`, `dam_reg`, `dcpf_proxy`), se, quality. `|SF| < 0.005` is not stored (read absent as 0). `quality`: `ok`, `weak`
(|SF| < 2 se; the se assumes independent errors so it is optimistic), `+grouped`, `+collinear`, `+shrunk`, `+implausible` (|SF| > 2 at one bus), `low` (proxy). `data/shift_factors.csv`
(102,187 rows, 11.7 MB): substation x the top 80 constraints, one best method per constraint (RT > unattenuated DAM > proxy > attenuated DAM), `|SF| >= 0.02`, with the other methods' values alongside
(`sf_rt`, `sf_dam`, `sf_proxy`), `side`, `sf_min`/`sf_max` over the substation's buses. A substation's SF is the median over its highest-voltage buses; buses of one substation share an SF unless a transformer separates
them (the range columns show where). Largest substation-level |SF|: 1.25 in RT; in DAM a few collinear low-priority constraints reach 2.7-3.5 (tagged `+implausible` above 2). `shift_factors.csv` has 2 rows tagged so (`OB_WAP99_A|SOBWA2P5`) and 8 rows above 1.5 (that one and `CKT_1027_1|DLYTCIS5`): filter on `quality`
or on |SF| <= 1.5 for a conservative set.

## 4. Physics proxy for constraints with no price data

**Graph (`derived/graph.{nodes,edges}.parquet`).** OSM substation-to-substation edges at 69 kV and above (7,695 inside the region and component below) plus 210 HIFLD segments where OSM has no corridor
(a HIFLD segment is added only if OSM has no path shorter than twice its length). Nodes are OSM substations, so ERCOT substations that share an OSM substation share a node. The graph is cut to the
ERCOT region (within 30 km of an anchored ERCOT substation, which removes SPP, MISO and WECC) and to its largest component: **5,190 nodes, 7,905 edges, 2,327 nodes with an anchored (match_conf >= 0.5) ERCOT substation.**
Edge reactance `x_pu = x'[kV] x km / (kV^2 / 100 MVA) / circuits`, with typical per-km values `x'` in ohm/km: 69 kV 0.45, 115 0.42, 138 0.40, 230 0.35, 345 0.32, 500 0.28 (ASSUMPTION; typical overhead-line values of the
kind in Grainger and Stevenson's line-parameter tables and in the PyPSA / pandapower standard line types; ERCOT's actual impedances are ECEII). Transformers are collapsed with the substation (as briefed), so there is no branch
for a 345/138 kV autotransformer and no PTDF for the 231 `xfmr` constraints.

**DC power flow.** One sparse factorisation, one solve per monitored element. Withdrawal is spread uniformly over the 1,814 graph nodes that host an anchored ERCOT substation without a resource node
(a load-substation proxy; there is no public load by substation; label ASSUMPTION). This choice shifts a constraint's PTDFs by a constant and does not change the correlations below. A line whose ends are not adjacent in the
graph is treated as the series path if it is at most 4 hops (mean PTDF of the edges on the path); parallel circuits are taken together. Contingencies are not decodable from public data, so every proxy PTDF is the
N-0 value and is shared by all contingency cids of an element: **313 monitored elements give a proxy for 718 constraints** (`derived/ptdf_proxy.parquet`: substation x element). Sub-threshold values (|PTDF| < 0.01) are not stored.

**GTCs are left out.** Their element lists are ECEII and nothing in notes/phase1-planning.md or `gtc.csv` gives the border lines, so no proxy sum is built. GTCs that bind in the window (EASTEX, WESTEX, N_TO_H, NELRIO, I_FW_N, PNHNDL, ...)
have regression SFs from prices; GTCs that did not (South Texas Export `PASP_PATA`) have nothing.

**Calibration and validation (`data/sf_proxy_validation.csv`).** Compared at substation level with the regression SFs that are not attenuated (`reliable`), 119 constraints:

| subset | n | median corr | median Spearman | sign agreement | slope (regression on proxy) | peak substation in proxy top 10 |
|---|---|---|---|---|---|---|
| RT regressions, unattenuated | 26 | **0.65** | 0.80 | 0.98 | 0.95 | 58% |
| DAM regressions, unattenuated | 93 | 0.32 | 0.25 | 0.96 | 0.72 | 33% |
| **all unattenuated (decision set)** | 119 | **0.38** | 0.29 | 0.96 | 0.75 | 39% |
| all RT regressions, incl. 5 attenuated | 31 | 0.60 | 0.79 | 0.99 | 0.76 | 52% |
| all 242, incl. attenuated | 242 | 0.14 | 0.12 | 0.99 | 0.27 | 21% |

Correlation does not depend on the withdrawal reference; sign agreement is scored on substations with |regression SF| >= 0.10 and |proxy| >= 0.02. Calibration: the only free physical constants are the reactance classes, so
a 3 x 3 grid of multipliers on the <= 115 kV and >= 230 kV reactances (138 kV fixed) was tried against the regressions. The best point raises the median correlation from 0.418 to 0.427, below the 0.03 bar, so the defaults stay
(`PROXY_SCALE = 1.0` too: the regression-on-proxy slope has 5th-95th percentiles of -0.2 and 1.9 across constraints, far wider than its distance from 1). **Verdict, stated plainly: the proxy gets the sign right (96-98%) and the
neighbourhood roughly right, but its median correlation (0.38 against all unattenuated regressions, 0.65 against the 26 cleanest, RT ones) is below the 0.5 line on the decision set, so all 718 proxy SFs carry `quality = low` / `sf_proxy = proxy_low_conf`.**
The day-ahead SFs are themselves noisy (RT and DAM SFs agree at a median correlation of only 0.76 where both are unattenuated), which caps how well any proxy can match them.
Why: it ignores the contingency (the regression SF of a cid is a post-contingency sensitivity), collapses transformers, misses lines that OSM and HIFLD do not carry, and uses typical reactances. It is useful for "which side and roughly how far"; do not rank
sites on its magnitudes. Where an end is only a graph-neighbour placement the confidence is lower again (`endpoint_conf` 0.40).

## 5. Hops and electrical distance (`data/hops.csv`, `data/hops_summary.csv`)

For each of the 2,870 placed substations (match_conf >= 0.5) and the 40 highest-priority constraints that have an SF: `hops` (unweighted graph distance to the nearest end of the monitored element; blank for GTCs, 99 if the
substation is off the graph), `x_dist_pu` (summed reactance, per unit on 100 MVA, along the minimum-reactance path to the nearest end; typical reactances, ASSUMPTION), the best available SF and its method,
`side` (import SF <= -0.02, export SF >= 0.02, else neutral), `curtail_flag` (export side and SF >= 0.05: injecting here loads the constraint) and `has_upgrade`. Totals: 114,800 rows (import 16,811, export 15,454,
neutral 82,343; 6,881 curtailment flags). `hops_summary.csv` has one row per substation: the nearest priority constraint (fewest hops, ties by |SF|), the constraint with the largest |SF|, their sides, whether each has a planned upgrade (at
match 0.5 or above), counts of import and export constraints and curtailment flags. Median nearest-constraint distance is 5 hops (10th percentile 2, 90th 9); 50 substations have 0 hops (they are an end of a priority element). 48 placed substations are off
the graph.

Five examples (best method in brackets):

| substation | constraint | hops | SF | side | note |
|---|---|---|---|---|---|
| KNAPP | Scurry Chevron-Knapp 138 kV | 0 | -0.458 (RT) | import | the import end; no planned upgrade matched |
| SCRCV (Scurry Chevron) | Scurry Chevron-Knapp 138 kV | 0 | +0.265 (RT) | export | curtailment flag: the other end of the same line |
| BLESSING | STP-W.A. Parish 345 kV (`STPWAP39_1`) | 1 | +0.254 (RT) | export | curtailment flag; TPIT-81238 matched at 0.30 only |
| HARGROVE | Twin Buttes-Hargrove 138 kV (contingency SRUSBIG8) | 0 | -0.505 (DAM) | import | RTP line upgrade identified (0.80), no date |
| A0 (Bexar) | STP-W.A. Parish 345 kV | 4 | +0.119 (RT) | export | a San Antonio 138 kV bus on the export side of a 345 kV line: far away but a sizeable SF |

## 6. Map (`map/substations.html`)

Standalone Leaflet 1.9.4 page (cdnjs, exact version) with the data in `map/substations_data.js` (the same payload is in `substations_data.json`; a `<script>` file is used because browsers block `fetch` of
local JSON from a `file://` page). It shows the 3,633 placed NP4-160 substations coloured by `match_conf` (default: only >= 0.5; a checkbox shows the rest), the top 60 priority constraints drawn between their end substations with
width by priority (dashed = did not bind in the window), and a selector that colours substations by SF to one of 40 constraints (blue import side, amber export side, grey neutral) or by hops to the element. A click on a substation shows its SF,
side and hops; a click on a line shows the element, window, IMM rent and upgrade match. Tested over HTTP in a browser pane: Leaflet, tiles and data load, the constraint selector, the confidence, SF and hops colourings and the substation card work with no console errors. Not tested: a real `file://` open (it should work, since the data
loads through a `<script>` tag); either way the page needs network access for Leaflet and tiles. Labels (REAL / DERIVED / ASSUMPTION) are on the page. The 20 constraints with rejected ends have no line.

## 7. What the numbers can and cannot say (known biases)

1. **One outage-driven week.** The RT window is 5-10 Oct (shoulder season) plus the 25 Sep archive day: the binding set is local 138 kV and 69 kV lines near outages (`6437__F`, `DT_PK_91_A`, `FORTMA_YELWJC1_1`, `COLLE_JUPIT_1`).
   None of the 2025 structural constraints that earn most of the IMM rent bind in it (Vealmoor-Koch Tap, Odessa EHV-Yarbrough, Bell County-Salado, Longshore-Consavvy, Laredo VFT never appear; Twin Buttes-Hargrove and WESTEX
   appear only in the day-ahead market; STP-W.A. Parish and Argyle-Lewisville bind a few runs). **Shift factors are topology-driven, so they transfer in principle; which constraints bind does not.**
   The shift factors fitted here are also the topology of that week (planned outages change the post-contingency SF of the survivors), and a "no outage" SF can differ.
2. **The priority score mixes an annual structural term with a one-week current term.** Rents in the window are estimated from shadow price x flow, not settlement data; the DAM term is 31 days of the same season. The weights (0.5 / 0.5 and 0.5 / 0.5) are ASSUMPTION.
   The 2025 rent is for the element, not the contingency; it is split across a constraint's contingencies by window rent.
3. **Binding set and SF quality differ by market.** RT fits are near exact; DAM fits predict a new day with R2 0.76 because DAM has a long tail of constraints that bind in a few hours each. Day-ahead SFs for those rare constraints are ridge-shrunk (tagged `+shrunk`).
4. **Station placement is the other limit.** Of the 1,137 line constraints, 648 have both ends on the graph at placement confidence >= 0.5 and 773 have both ends on it at any confidence; in 209 (from end) and 198 (to end) the end is an NP4-160
   substation with no usable placement (`np4160_unanchored`: no OSM link at >= 0.5); 20 were rejected on length. Hops and the proxy are only as good as the 1c placements (0.5 to 0.7 is the least trustworthy band)
   and the aliases (confidence 0.40 for graph-neighbour placements).
5. **No impedance, rating or contingency data.** Reactances are typical values by kV; the proxy is N-0; ratings come only from the shadow-price reports (limit and flow), so no thermal headroom is computed here.
6. **GTCs and transformers.** GTC SFs exist only where the interface bound in the window; no proxy for them or for the 231 transformer constraints.
7. **Shift factors are small away from the element.** The median substation-level |SF| is about 0.006 for RT constraints and the 90th percentile 0.04; the sizeable values sit within a few hops of the element. `se` assumes independent errors and is far below the plausible error; rely on the `weak` tag rather than on `se`.
8. **Constants in this phase are unverified** where labelled ASSUMPTION. The RT ridge penalty came from cross-validation inside the training rows and was not tuned on the holdout. **For the DAM fit the holdout was looked at while choosing:** a column-scaled ridge was rejected because
   it scored R2 near zero on the holdout, `RELAX_ALPHA` was picked between 1e2 and 1e3 after comparing holdout metrics, and `VIF_MAX` was raised from 100 to 1,000 after seeing that the first value dropped Twin Buttes-Hargrove. The DAM out-of-sample numbers are therefore mildly optimistic.
   For scale: against the fit with no focal constraints (penalty 3e4), the final DAM fit gives up 0.2 points of median R2 (0.758 to 0.756) and 4% of median RMSE (4.51 to 4.68), and its 10th-percentile R2 falls from 0.00 to -0.11; that is the price of keeping the priority SFs close to their size.

## 8. Hand-offs

- **Phase 3 (site value).** Use `data/shift_factors.csv` (top 80) or `derived/sf_long.parquet` (all fitted constraints, bus level), `data/hops_summary.csv` for nearest and best constraint, `data/constraints.csv` for priority, binding hours and planned upgrades.
  Rely on `rt_reg` first; treat `dam_reg` with `+shrunk` as sign-only; never rank sites on `dcpf_proxy` magnitudes.
- **Phase 4 (upgrades).** `upgrade_*` columns give the match and confidence (`one_end` 0.30 is a hint, not a match). For the structural constraints that did not bind, ratings and flows are not in the window: the IMM annual rent and the TPIT row are the only evidence.
- **Agent 1c.** Constraint pairs whose ends fail the length test (`endpoint_how` starts with `rejected_`, 20 rows, 12 distinct pairs): HIGH36-BRENNO, SALEM-HIGH36, BRAND_C-COLLEGE, FAIRDALE-COLLEGE, UNIVERSI-GATEETP, KB-GT, TITAN_SU-PALOALTO,
  ENTPR-TRSES, HWRDTP-WELLBR, BALLINGE-HUMBLTAP and HUMBLTAP-NOVICTAP, GRAHM-OLNEY, PADR-MATADOR, HRZN_SLR-HORIZON.
- **Extra files written** (all with `.labels.csv` where CSV): `data/sf_rt_vs_dam.csv`, `data/sf_groups.csv`, `data/sf_proxy_validation.csv`, `derived/p2_summary.json` (the fit, validation, sensitivity and proxy numbers quoted above), `derived/_p2_cache/` (aligned matrices, fit status; rebuilt automatically),
  `map/substations_data.{js,json}`.

## 9. Constants

Every unverified constant is a single UPPER_CASE value at the top of its module with a label (REAL / DERIVED / ASSUMPTION). Values as of this run (generated from the code).

**network.py**

| name | value | label | meaning |
|---|---|---|---|
| `ALIAS_SUFFIXES` | `('SWTCH', 'SWITCH', 'SWY', 'TAP', 'POI', 'STA', 'SUB', 'SS', 'TP', 'SW')` | ASSUMPTION | tap/switch endings ERCOT appends to a yard name |
| `ALIAS_STEM_MIN_LEN` | `4` | ASSUMPTION | a stem shorter than this is ambiguous (PG, EC, REN) and gets the "short" weight |
| `ALIAS_PREFIX_MIN_LEN` | `5` | ASSUMPTION | shortest stem allowed for a prefix match |
| `ALIAS_W` | `{'stem_exact': 0.75, 'stem_short': 0.4, 'psse_name': 0.6, 'osm_shared': 0.6, 'prefix': ...` | ASSUMPTION | evidence weights, combined by noisy-OR "stem_exact": 0.75, |
| `ALIAS_FUZZY_MIN` | `0.85` | ASSUMPTION |  |
| `ALIAS_SHARED_NAME_MIN` | `0.5` | ASSUMPTION | a shared OSM substation counts in full only if the two codes also look alike |
| `ALIAS_SHARED_NO_NAME_FACTOR` | `0.4` | ASSUMPTION | weight multiplier for a shared OSM substation with unlike codes (co-located yards) |
| `ALIAS_KV_TOL` | `1.0` | ASSUMPTION | kV match tolerance (kV) |
| `ALIAS_KV_MISMATCH_FACTOR` | `0.5` | ASSUMPTION | confidence multiplier when the candidate has no bus at the station's kV |
| `ALIAS_GRAPH_NEAR_HOPS` | `3` | ASSUMPTION | candidate within this many graph hops of the station's partner confirms it |
| `ALIAS_GRAPH_BONUS` | `1.15` | ASSUMPTION |  |
| `ALIAS_FAR_KM` | `60.0` | ASSUMPTION | candidate this far from a placed partner is contradicted |
| `ALIAS_FAR_FACTOR` | `0.4` | ASSUMPTION |  |
| `ALIAS_MIN_CONF` | `0.35` | ASSUMPTION | aliases below this are not written (sub left blank) |
| `ALIAS_CONF_CAP` | `0.95` | ASSUMPTION |  |
| `NEIGHBOR_PLACE_CONF` | `0.4` | ASSUMPTION | confidence of a station placed as the only unclaimed graph neighbour of its partner |
| `ANCHOR_CONF` | `0.5` | ASSUMPTION | substations at or above this match_conf are trusted graph anchors (agent 1c: "use >= 0.5") |
| `GRAPH_MIN_KV` | `69.0` | REAL | scope in PROMPT.md (>= 69 kV) |
| `ERCOT_REGION_KM` | `30.0` | ASSUMPTION | keep OSM nodes within this distance of an anchored ERCOT substation (drops SPP/MISO/WECC) |
| `HIFLD_DETOUR` | `2.0` | ASSUMPTION | a HIFLD segment is added only if OSM has no path shorter than this multiple of its length |
| `HIFLD_MIN_KM` | `0.5` | ASSUMPTION | ignore HIFLD segments shorter than this (snapping noise) |
| `SNAP_NODE_KM` | `5.0` | ASSUMPTION | an ERCOT sub with coordinates but no OSM link snaps to a graph node within this distance |
| `SBASE_MVA` | `100.0` | ASSUMPTION | per-unit base; PTDFs do not depend on it |
| `XPRIME_OHM_PER_KM` | `{69.0: 0.45, 115.0: 0.42, 138.0: 0.4, 161.0: 0.4, 230.0: 0.35, 345.0: 0.32, 500.0: 0.28...` | ASSUMPTION | typical overhead-line positive-sequence reactance by voltage class, ohm/km. 69.0: 0.45, 115.0: 0.42, 138.0: 0.40, 161.0: 0.40, 230.0: 0.35, 345.0: 0.32, 500.0: 0.28, 765.0: 0.26, } |
| `X_MIN_PU` | `0.0005` | ASSUMPTION | floor on branch reactance (about 0.05 km of 138 kV line); stops zero-length edges from dominating B |
| `MAX_CIRCUITS` | `6` | REAL | lines.csv caps n_circuits at 6 |
| `LOAD_REF_RULE` | `'uniform weight over graph nodes that host at least one anchored ERCOT substation with ...` | ASSUMPTION |  |
| `PATH_MAX_HOPS` | `4` | ASSUMPTION | a monitored line whose ends are not adjacent in the graph is treated as the series path if <= this many hops |
| `SF_SIDE_MIN` | `0.02` | ASSUMPTION | /SF/ below this is "neutral" (about twice the regression noise floor) |
| `SF_CURTAIL_MIN` | `0.05` | ASSUMPTION | export-side SF at or above this flags battery curtailment risk when the constraint binds |
| `HOPS_UNREACHED` | `99` | ASSUMPTION | hop value written when a substation is not connected to the graph |

**shiftfactors.py**

| name | value | label | meaning |
|---|---|---|---|
| `SCED_DT_H` | `0.08333333333333333` | ASSUMPTION | one SCED run covers 5 minutes (same convention as notes/phase1-market.md) |
| `DAM_DT_H` | `1.0` | REAL | DAM prices are hourly |
| `W_STRUCT` | `0.5` | ASSUMPTION | weight of the structural term (2025 IMM annual rent) |
| `W_CURRENT` | `0.5` | ASSUMPTION | weight of the current term (rents seen in the window) |
| `W_CURRENT_RT` | `0.5` | ASSUMPTION | within the current term, share given to the real-time week |
| `W_CURRENT_DAM` | `0.5` | ASSUMPTION | and to the 31 day-ahead days |
| `ELEMENT_MAX_KM_LOW` | `80.0` | ASSUMPTION | a line below 230 kV whose two placed ends are farther apart than this is a placement error |
| `ELEMENT_MAX_KM_HIGH` | `130.0` | ASSUMPTION | same for lines at 230 kV and above |
| `PRIORITY_TOP_SF` | `80` | ASSUMPTION | constraints kept in data/shift_factors.csv |
| `PRIORITY_TOP_HOPS` | `40` | ASSUMPTION | constraints in data/hops.csv |
| `MIN_BINDING_RUNS` | `10` | ASSUMPTION | RT constraints that bind in fewer SCED runs are not fitted (SF = NaN) |
| `MIN_BINDING_HOURS_DAM` | `12` | ASSUMPTION | same for DAM, in hours |
| `MIN_TRAIN_BINDING` | `3` | ASSUMPTION | a constraint must bind this often in the training part to be fitted in the validation fit |
| `HOLDOUT_RT_HOURS` | `24` | ASSUMPTION | RT holdout = the last 24 h of aligned SCED runs ("last RT day"; 10 Oct is a partial CPT day) |
| `HOLDOUT_DAM_DAYS` | `5` | REAL | brief asks for the last 5 DAM days |
| `RIDGE_GRID` | `(10.0, 100.0, 1000.0, 10000.0, 30000.0, 100000.0)` | ASSUMPTION | ridge penalties tried, absolute (units: ($/MWh)^2 of shadow price); absolute rather than per-column scaled so a constraint with little binding evidence is shrunk toward SF = 0 (a column-scaled penalty was tried and gave out-of-sam |
| `CV_BLOCKS` | `5` | ASSUMPTION | contiguous time blocks for choosing the ridge penalty inside the training part |
| `COLLINEAR_CORR` | `0.9` | ASSUMPTION | shadow-price series with /corr/ >= this are merged into one regressor group |
| `COLLINEAR_WARN_CORR` | `0.8` | ASSUMPTION | pairs above this are reported (not merged) |
| `VIF_MAX` | `1000.0` | ASSUMPTION | numerical guard only: a regressor group with a VIF above this gets SF = NaN. Collinearity is handled by merging groups with shadow-price corr >= COLLINEAR_CORR and by tagging VIF > VIF_FLAG; a VIF of 100 would drop key constraints |
| `VIF_FLAG` | `10.0` | ASSUMPTION | VIF above this tags the SF "+collinear" in the quality column |
| `RELAX_ALPHA` | `1000.0` | ASSUMPTION | ridge penalty for the focal (top-priority) regressor groups; every other group keeps the cross-validated penalty. Lowers the attenuation of the SFs we publish |
| `SHRINK_MIN` | `0.8` | ASSUMPTION | ridge shrinkage factor ss/(ss+lambda), ss = sum of squared shadow prices; below this the SF magnitude is attenuated and tagged "+shrunk" |
| `SF_MAX_ABS` | `2.0` | ASSUMPTION | a bus-level /SF/ above this is not physical for a single element; tagged "+implausible" |
| `T_MIN` | `2.0` | ASSUMPTION | /SF//se below this marks the SF "weak" (se ignores autocorrelation, so it is optimistic) |
| `SF_STORE_MIN` | `0.005` | ASSUMPTION | /SF/ below this is not stored in sf_long (about the noise floor; treat absent as 0) |
| `SF_CSV_MIN` | `0.02` | ASSUMPTION | /SF/ threshold for data/shift_factors.csv |
| `AT_CAP_TOL` | `0.5` | ASSUMPTION | a shadow price within this ($/MWh) of max_shadow_price counts as "at cap" (capped prices are stored a few 1e-4 below the cap, e.g. 3499.99976 vs 3500) |
| `R2_OK` | `0.5` | ASSUMPTION | a bus counts as "well explained" out of sample above this R2 |
| `PROXY_MIN_PAIRS` | `5` | ASSUMPTION | need this many placed substations to compare proxy and regression |
| `PROXY_POOR_CORR` | `0.5` | REAL | (from the brief): median correlation below this = "poor" |
| `PROXY_LOW_CONF` | `'low'` | ASSUMPTION | label written to sf_long.quality for proxy rows when the proxy is poor |
| `PROXY_SF_MIN` | `0.01` | ASSUMPTION | /PTDF/ below this is not stored |
| `UPGRADE_TYPES` | `('reconductor', 'new_line', 'new_xfmr', 'upgrade_sub')` | REAL | from the brief |
| `MATCH_CONF` | `{'pair_name': 0.9, 'pair_title': 0.8, 'pair_bus': 0.7, 'pair_bus_many': 0.45, 'xfmr_sub...` | ASSUMPTION |  |
| `UPGRADE_CONF_MIN` | `0.5` | ASSUMPTION | a planned-upgrade match at or above this confidence counts as "has a planned upgrade" in hops.csv |
| `PAIR_BUS_FEW` | `4` | ASSUMPTION | a project whose PSS/E buses resolve to more substations than this is a broad project; pair_bus match gets the lower weight |
| `PROJ_LINE_WORDS` | `'\\bLINE\\b/\\bCKT\\b/CIRCUIT/CONDUCT/REBUILD/\\bTO\\b/ - /[\\u2013\\u2014\\ufffd]/\\bB...` | ASSUMPTION | a reconductor/new_line project must read like a line project (the dash class also covers a mis-decoded en dash in TPIT titles) |
| `PROJ_NAME_EXCLUDE` | `'BREAKER/CAPACITOR/REACTOR/RELAY/SERIES COMP'` | ASSUMPTION | equipment-only projects do not relieve a thermal limit |
| `PROJ_NAME_DROP` | `{'STATION', 'POI', 'SUBSTATION', 'TNP', 'BUS', 'UPGRADE', 'LINE', 'KV', 'SUB', 'SWITCH'...` | ASSUMPTION |  |
| `GTC_UPGRADES` | `{'WESTEX': ('PBRP-IMPORT1', 'PBRP-IMPORT2', 'PBRP-IMPORT3'), 'PNHNDL': ('RPG-26RPG008',...` | ASSUMPTION | GTC code -> projects the public documents say relieve it (data/gtc.csv status text, notes/phase1-planning.md) "WESTEX": ("PBRP-IMPORT1", "PBRP-IMPORT2", "PBRP-IMPORT3"), "PNHNDL": ("RPG-26RPG008",), "MCCAMY": ("CN2025-01",), "NELR |
| `R2_MIN_STD` | `0.1` | ASSUMPTION | a bus whose holdout congestion has std below this ($/MWh) has no meaningful R2 (NaN) |
| `TOP_BIG_SF` | `0.05` | ASSUMPTION | /SF/ at or above this counts as "large" when comparing RT and DAM |
| `TOP_BUS_OVERLAP_N` | `20` | ASSUMPTION | compare the N buses with the largest /SF/ in RT and DAM |
| `SENS_MIN_BIND` | `(5, 20)` | ASSUMPTION | alternative MIN_BINDING_RUNS values reported as a sensitivity |
| `PROXY_SCALE` | `1.0` | ASSUMPTION | multiplier applied to proxy PTDFs; left at 1.0 because the regression-on-proxy slope varies more across constraints than its median differs from 1 |
| `X_MULT_GRID` | `(0.5, 1.0, 2.0)` | ASSUMPTION | multipliers tried on the reactance of <=115 kV lines and of >=230 kV lines relative to 138 kV (138 kV = 1) |
| `CAL_MIN_GAIN` | `0.03` | ASSUMPTION | keep the default reactances unless a grid point raises the median proxy-vs-regression correlation by more than this |
| `PROXY_LONG_TOP` | `200` | ASSUMPTION | proxy rows are added to sf_long (bus level) for this many top-priority constraints that lack a regression SF |
| `PROXY_PEAK_TOP` | `10` | ASSUMPTION | "peak match" = the substation with the largest /regression SF/ is among the top-N /proxy/ substations |
| `PROXY_SIGN_MIN_REG` | `0.1` | ASSUMPTION | sign agreement is scored on substations with /regression SF/ at or above this |
| `PROXY_SIGN_MIN_PROXY` | `0.02` | ASSUMPTION | ... and /proxy/ at or above this |

**cli_2.py**

| name | value | label | meaning |
|---|---|---|---|
| `MAP_TOP_LINES` | `60` | ASSUMPTION | priority constraints drawn as lines on the map |
| `MAP_SF_SCALE` | `1000` | ASSUMPTION | SF values are stored as integers in thousandths to keep the data file small |
| `MAP_NO_DATA` | `32767` | ASSUMPTION | integer sentinel for "no SF / no hops value" |
