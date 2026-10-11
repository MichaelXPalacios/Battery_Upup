# Phase 3b: what a battery earns at a substation, and how that changes through 2031 (11 Oct 2026)

Agent 3b. Code: `gridsite/nodal_lanes.py` (price lanes), `gridsite/saturation.py` (forward scale factors), `gridsite/sites.py` (candidates, dispatch, forward values, portfolio, costs, the final table), `gridsite/cli_3b.py` (commands).
Outputs, each with a `.labels.csv` sidecar (REAL / DERIVED / SIM / ASSUMPTION): `data/site_candidates.csv` (40 candidates + 8 zone controls), `data/lane_validation.csv` (200 rows), `data/constraint_synth.csv` (the 260 replayed constraints),
`data/site_years.csv` (476 rows), `data/portfolio.csv` (93 rows), `data/sites.csv` (40 rows x 150 columns). Tests: `tests/test_3b.py` (36 tests, 60 s). Derived data outside the repo: `research-data/grid/derived/nodal_lanes_*.npz` and `runs/` (ledgers, oracle, forward values, logs).

Run from `docs/research/grid` with the research venv (`C:/Users/Javier/Repo/BaseP/research-data/.venv/Scripts/python.exe run_grid.py <command>`); commands in section 10.
Nothing was installed or downloaded, nothing was committed, and no arena or other agent's file was edited.

**Everything about a node's price in this note is SIM.** ERCOT publishes one week of real-time and 31 days of day-ahead nodal prices, so a multi-year lane at a substation is rebuilt from the real zone price and a replayed congestion structure (section 2).
The 8 zone controls are the arena's own zone prices, bit for bit, and their dispatch matches the arena's stored backtest to $0.06/kW-yr on the 8-zone average. Dollars are $ per kW-yr of a 1 MW battery (2 h unless said), energy only, after the $20/MWh wear cost; "learner" is the arena's Adaptive learner (A5).
A battery earns a node's price only if it settles there: a registered transmission or distribution ESR does (at its resource node), an ADER aggregation of home batteries settles at the load-zone price and gets none of the nodal value (`notes/rules.md` (a)). The zone control rows are what an ADER earns.

## Findings first

1. **A 2 h battery at a congested substation earns about twice what it earns in its load zone, and almost all of the gain is import-side pockets behind a few lines.**
   The 40 candidates average **$78.3/kW-yr** (learner, 2021-25) against **$44.4** at the 8 zone controls; in 2025 **$54.6 against $17.8**, in 2026 to 26 Sep $41.3 against $11.2 (annualised). The gain over a node's own zone has mean +$32.8 and median +$23.6;
   37 of 40 beat their zone (the three that do not are export-side: ECRSW, ROUGH, LWSVW). The 30 import-side nodes gain +$40.1, the 10 export-side +$10.9; 14 nodes at 69 kV earn $105 against $64 for 26 at 138 kV.
   The uplift is about the same every year ($29-38), so siting matters most when the zone earns little. Section 4.1.
2. **The top of the list is one pocket, and it is small.** Five nodes behind Twin Buttes-Hargrove 138 kV (limit 151 MW, 945 binding hours, $137M of 2025 rent in the replay) earn $83-270 (MIDW $269.7, STRS $249.5, POWELLFI $201.5 on the import side; MILES $96.9 and NOVICE $83.0 on the export side);
   the best site outside the pocket is KNAPP at $98.2. The other 35 candidates average $63.8, a gain of +$18.9 over their zones. The price-taker cap for the whole pocket is 10 MW (10% of the limit). Sections 1 and 6.
3. **The learner is not the best algorithm at a node; the Forecaster is.** A2 earns the most at 36 of 40 nodes ($81.0 against $78.3 for A5; the learner wins at 7 of 8 zones). No algorithm closes the gap to the perfect-foresight ceiling in a pocket
   (the oracle is $176 at nodes against $104 at zones; every algorithm captures 28-46% of it). The synthetic basis may be more predictable than a real one, which would flatter the Forecaster (caveat 4). Section 4.2.
4. **The Spike hunter does better in pockets, relative to itself, but not relative to the others.** It gains the most from siting at a node (x2.04 against x1.76 for the learner; +5.7 points of the oracle against +1.6; $445 per MWh against $225 at the zones),
   and it is the best of the five at the three Houston PK-DT nodes in the 4 h run, but it is below the learner at all 40 nodes over 2021-25 (by $18) and it falls further behind the stronger the pocket is. Clockwork loses ground (38% of the oracle at zones, 28% at nodes). Section 4.2.
5. **A 4 h battery earns 1.3-1.7x the 2 h one at the nodes** (top 15: $132 against $94 over 2024-25; zones 1.38x), with a higher share of the oracle (0.58 against 0.48). Section 4.3.
6. **The pocket premium halves by 2029, and all of the fade is storage saturation.** With 26 GW of effective storage online by 2031 (41 GW listed), the 40 candidates' mean uplift over the zone falls from $38.4 (2025 replay) to $33.1 (2027), $24.6 (2028) and $19.3 (2029-31); the learner value from $62.4 to $43.2.
   No planned upgrade moves a candidate's 2031 value by more than $1.1 (none of the eleven dominant elements has a dated match at confidence 0.5 or more before 2032). **Basis half-life: 2028 for 9 sites, 2029 for 6, "> 2031" for 25.** Section 5.
7. **The Twin Buttes decline is the fragile one.** MIDW falls from $248 to $87 (2029) because 15 storage-hosting substations with a shift factor of about -0.065 to the line (t about 7) add up to 111 MW of relief on a 151 MW line.
   A minimum shift factor of 0.075 instead of 0.05 removes the effect (2031 scale 0.92 instead of 0.31) and with it the half-life of the five Twin Buttes sites. Read their half-life as "2028 if the cluster counts". Houston PK-DT nodes (BG, GT, PK, MID, $57-60) do not fade at all.
8. **With price-taker caps, siting at the nodes adds $3-8/kW-yr to a 1,000 MW fleet, not $20-74.** Group caps of 10% of each line's limit (whole 10 MW units, eleven groups) leave room for 200 MW of the 1,000 MW at the 40 nodes;
   the learner rule gets $34.9/kW-yr (zones only $27.8; uncapped $101.3, all 1,000 MW at MIDW). Rules A5 and A2 come first and second with and without caps; A4 is last with caps. Section 6.
9. **Net of connection, only a few sites carry a typical installed cost on energy alone.** Connection is $192-194/kW (step-up transformer included; one switchyard $287) = $22.5/kW-yr; the median net-of-connection value is **$21.9/kW-yr** and the median break-even installed cost **$389/kW**
   (zone control: $197). MIDW, STRS and POWELLFI reach $1,026, $946 and $769/kW (first 10 MW only). The AS line is $15.9 (2025) and $11.9 (2026) per kW-yr for 2 h (upper bound $31), the same anywhere. Section 7.
10. **The lanes pass the checks that can be made.** With the published shadow prices the formula rebuilds the real RT node basis of 3-10 Oct with correlation **0.994** and a mean absolute error of **$1.85/MWh** (**$8.20** if the basis were zero); on 3-5 Oct, before the fit window, **0.984** and $1.52 against $3.77;
    day-ahead month **0.969**, $1.14 against $4.63. The synthetic shadow prices hit their annual targets to 4e-16; the zone controls equal the arena's zone prices exactly (difference 0). What is not validated is the *timing* of the synthetic shadow prices (46% of the constraints use a geographic rule). Section 3.
11. **Levels are uncertain by a factor of about two, rankings much less.** Across six alternative SIM choices the rank correlation of 14 sites' 2025 oracle uplift with the base is 0.90-1.00, but the mean uplift moves from -16% to +25% and the window-anchored ones by up to a factor of two;
    the screening score ranks a stratified pool sample with Spearman 0.94. Section 4.4.

### The top 15 (by 2 h learner value, 2021-25)

$/kW-yr; `uplift` = the node minus its zone control (2 h learner, 2021-25); `half-life` = first year from 2026 with uplift below 50% of 2025's; `connection` = class and lead time.

| sub | zone | kV | constraint (side, SF, hops) | 2h A5 21-25 | 2h A5 2025 | uplift vs zone | 4h A5 24-25 | half-life | connection |
|---|---|---|---|---|---|---|---|---|---|
| MIDW | WEST | 69 | HARGRO_TWINBU1_1 (import, -1.00, 8 hops) | 269.7 | 237.6 | 220.0 | 339.3 | 2028 | new_bay 38 mo |
| STRS | WEST | 69 | HARGRO_TWINBU1_1 (import, -0.92, 7 hops) | 249.5 | 214.7 | 199.7 | 311.6 | 2028 | new_bay 38 mo |
| POWELLFI | WEST | 69 | HARGRO_TWINBU1_1 (import, -0.72, 8 hops) | 201.5 | 165.7 | 151.8 | 242.6 | 2028 | new_bay 38 mo |
| KNAPP | WEST | 138 | 6437__F (import, -0.46, 0 hops) | 98.2 | 53.9 | 48.5 | 111.4 | > 2031 | new_bay 56 mo |
| MILES | WEST | 69 | HARGRO_TWINBU1_1 (export, +0.50, 6 hops) | 96.9 | 101.4 | 47.2 | 123.6 | 2028 | new_bay 38 mo |
| SHRNE | WEST | 138 | 6437__F (import, -0.33, 2 hops) | 89.7 | 47.3 | 40.0 | 99.1 | > 2031 | new_bay 56 mo |
| NOVICE | WEST | 69 | HARGRO_TWINBU1_1 (export, +0.36, 5 hops) | 83.0 | 83.7 | 33.2 | 109.0 | 2028 | new_bay 38 mo |
| SEGOVI | LCRA | 69 | FORTMA_YELWJC1_1 (import, -0.50, 3 hops) | 79.0 | 42.6 | 31.8 | 85.7 | 2028 | new_bay 38 mo |
| HILND | NORTH | 138 | 587__A (import, -0.55, 2 hops) | 78.6 | 53.2 | 37.1 | 86.4 | > 2031 | new_bay 56 mo |
| CRNSO | NORTH | 138 | 587__A (import, -0.55, 2 hops) | 78.6 | 53.6 | 37.1 | 86.2 | > 2031 | new_bay 56 mo |
| BULLCRK | WEST | 138 | 6437__F (import, -0.26, 4 hops) | 74.7 | 38.6 | 25.0 | 80.6 | > 2031 | new_switchyard 56 mo |
| BG | HOUSTON | 138 | DT_PK_91_A (import, -1.00, 3 hops) | 74.0 | 62.6 | 26.8 | 80.1 | > 2031 | new_bay 56 mo |
| GT | HOUSTON | 138 | DT_PK_91_A (import, -1.00, 2 hops) | 72.8 | 60.8 | 25.7 | 77.1 | > 2031 | new_bay 56 mo |
| CRNTH | NORTH | 138 | 587__A (import, -0.47, 2 hops) | 72.5 | 48.3 | 31.0 | 76.4 | > 2031 | new_bay 56 mo |
| PK | HOUSTON | 138 | DT_PK_91_A (import, -1.00, 0 hops) | 72.3 | 63.4 | 25.1 | 72.3 | > 2031 | new_bay 56 mo |

**Why these.** *MIDW, STRS, POWELLFI* are 7-8 hops behind Twin Buttes-Hargrove with shift factors of -1.00, -0.92 and -0.72: when the line binds (945 hours a year at an average shadow price of about $960 in the replay) their price rises by roughly that and a 2 h battery sells into it.
*MILES, NOVICE* are on the export side of the same line (SF +0.50, +0.36): their price falls when it binds, so they charge cheaply. *KNAPP, SHRNE, BULLCRK* are import-side of Scurry Chevron-Knapp (KNAPP is an end of the line, SF -0.46; 1,099 hours, mean shadow price $290) and lean on Twin Buttes as a second exposure.
*HILND, CRNSO, CRNTH* are 2 hops from Lewisville-Argyle (587__A, SF -0.47 to -0.55; 431 hours at $546). *SEGOVI* is 3 hops from Fort Mason-Yellow Jacket (SF -0.50; 328 hours at $481). *BG, GT, PK* are 0-3 hops from PK-DT in Houston with SF -1.00:
short sharp spikes (543 hours, mean $320, up to $1,217 on its second contingency), which is why their learner share of the oracle is 0.69-0.74 in the 4 h run against 0.51-0.64 elsewhere.

All 40 candidates are in Appendix A.

## 1. Candidates (`data/site_candidates.csv`, 48 rows)

**Eligible pool.** NP4-160 substations with `in_np4160`, `match_conf >= 0.5`, `kv_max >= 69`, no `exclude_flag` (physical risk) and an arena zone: **2,722**. Of these, **1,250** have a usable shift factor
(`rt_reg`, or `dam_reg` that is not tagged `+shrunk`) of at least 0.10 in magnitude to one of the 40 best-priority constraints, and the *dominant* element of their score has an own |SF| of at least 0.10
(otherwise a site can rank on the zone's average effect alone). The node is the highest-kV bus (`node_bus`); only 2 of the 40 have a resource node there today, so the other 38 are new settlement points.

**Score** = sum over the replayed constraints k of |SF[n,k] - SF_zone[k]| x shadow-price-hours[k] ($ per MW-year of price exposure, DERIVED). The dominant element is the one contributing most among those the site is itself coupled to (|SF| >= 0.05).
The score is a screening number, not a value: across the pool it ranks the 2025 oracle uplift of a stratified sample of 36 non-selected substations with Spearman 0.94 (section 4.4).

**Selection.** Greedy by score with at most 5 per dominant element, then swaps so that each side (import: SF < 0 to the dominant element, the node price rises when it binds; export: SF > 0) has at least 10. Result: **30 import, 10 export**; 11 dominant elements;
14 at 69 kV and 26 at 138 kV; 14 in LZ_WEST, 13 in LZ_NORTH, 6 LZ_LCRA, 4 LZ_HOUSTON, 3 LZ_SOUTH (no AEN, CPS or Rayburn site clears the SF bar). The dominant SF is `rt_reg` for 30 sites and `dam_reg` for 10 (Twin Buttes is `dam_reg`);
33 have a hop count to the element in `data/hops.csv` (median 4). The 8 load zones are added as controls.

**What the pockets are.** Eleven elements sit behind the 40; the five with five nodes each take 25 of them.

| element | limit MW | rent $M | H h | driver | matched project (conf) | nodes (best first) | sides | mean A5 | uplift |
|---|---|---|---|---|---|---|---|---|---|
| TWINBU-HARGROVE 138 kV (`HARGRO_TWINBU1_1`) | 151 | 137.0 | 945 | LZ_WEST(+) dam | RTP2025-2022-WFW01 (0.8) | MIDW,STRS,POWELLFI,MILES,NOVICE | export/import | 180.1 | 130.4 |
| SCRCV-KNAPP 138 kV (`6437__F`) | 214 | 68.3 | 1,099 | LZ_WEST(+) rt |  | KNAPP,SHRNE,BULLCRK,MPPOI,SCRCV | export/import | 80.8 | 31.1 |
| FORTMA-YELWJCKT 138 kV (`FORTMA_YELWJC1_1`) | 145 | 22.9 | 328 | LZ_CPS(-) rt | TPIT-70710 2027-11-30 (0.3) | SEGOVI,SHELLLON,JUNCTION,MASONSW,EOLA | import | 70.4 | 28.6 |
| LWSVH-ARGYL 138 kV (`587__A`) | 216 | 51.0 | 431 | HB_NORTH(+) geo | TPIT-110519 2031-03-01 (0.3) | HILND,CRNSO,CRNTH,LWSVW | export/import | 67.6 | 26.1 |
| PK-DT 138 kV (`DT_PK_91_A`) | 293 | 50.8 | 543 | LZ_HOUSTON(+) rt | TPIT-64499 2026-12-31 (0.3) | BG,GT,PK,MID | import | 72.8 | 25.6 |
| LAKENASW-SAMATHIS 69 kV (`LAKENA_SAMATH1_1`) | 38 | 5.9 | 862 | LZ_WEST(+) dam |  | TANK | import | 64.8 | 15.0 |
| HIGH36-BRENNO 138 kV (`215T215_1`) | 238 | 26.0 | 575 | LZ_LCRA(+) dam | TPIT-78169 2026-09-04 (0.3) | BRENNO,SANDHI,GAYHIL,LYONS,CHAPHI | import | 60.8 | 13.6 |
| SEA-SEA 138/69 kV (`SEA_AAT1`) | 99 | 9.5 | 438 | LZ_AEN(+) dam |  | SEA,TMPTV,PGE | import | 51.6 | 10.1 |
| COLLEGE-JUPITER 138 kV (`COLLE_JUPIT_1`) | 272 | 35.7 | 290 | LZ_RAYBN(-) rt | RTP2025-2025-NC06 (0.9) | APOLLO,MILLERRD,WYNJOYCE,LYONS_C,CENTRVIL | export/import | 49.3 | 7.8 |
| BRAND_C-COLLEGE 138 kV (`COL_BRAN_1`) | 272 | 28.3 | 213 | LZ_RAYBN(-) rt | RTP2025-2025-NC06 (0.3) | COLLEGE | import | 49.2 | 7.7 |
| ENCRT-BRAND 138 kV (`6429__D`) | 410 | 47.9 | 595 | LZ_WEST(+) rt |  | ECRSW,ROUGH | export | 47.6 | -2.2 |

*Columns.* `rent` is the replayed 2025 rent (shadow-price-hours x limit; IMM-anchored for Hargrove-Twin Buttes, Scurry Chevron-Knapp and 587__A, window-anchored for the rest), `H` its binding hours, `driver` the hub or zone spread that times it
(`+` price above HB_BUSAVG when it binds) and how it was chosen, `matched project (conf)` the planned project matched in `constraints.csv` (the forward clock uses agent 4's schedule, which needs confidence 0.5 or more and a date: only LWSVH-ARGYL is on it, dated 1 Dec 2032, so the upgrade clock moves no candidate before 2032, section 5).
`mean A5` and `uplift` are the 2h learner over 2021-25 and its gain over the zone control, averaged over the element's candidates.

The 40 are not the 40 best substations in the pool: the cap of five per element leaves out strong Twin Buttes nodes (the pool sample in section 4.4 has HARGROVE and KETCHUM at +244 and +225 $/kW-yr of 2025 oracle uplift, above every candidate except MIDW, STRS and POWELLFI).
They are a spread over eleven structures with both signs, which is what the brief asked for.

## 2. How a substation's price lane is rebuilt (SIM)

ERCOT publishes nodal prices for one week of real time (3-10 Oct 2026) and 31 days of day-ahead (10 Sep-10 Oct), so a multi-year lane at a substation cannot be read from data. It is rebuilt
from the zone price and a replayed congestion structure (`gridsite/nodal_lanes.py`):

```
p_n(t) = clip( p_zone(t) - sum_k ( SF[n,k] - SF_zone[k] ) * mu_tilde[k,t] ,  -250 ,  5000 )        cong = -sum SF*mu, SF < 0 is the import side (PLAN.md)
```

| piece | what it is | label |
|---|---|---|
| `p_zone(t)` | the arena's 15-minute price of the node's zone (NOIE zone first, else load zone), 2019-01-01 to 2026-09-26, 96-interval DST-normalised days. The 8 control lanes are exactly this series (maximum difference to the arena's `Market`: 0). | REAL |
| `SF[n,k]` | phase 2's substation shift factor to constraint k: `rt_reg` first, else a day-ahead fit that is not attenuated (`+shrunk` is never used), `implausible` read as 0, `abs(SF) <= 1` | DERIVED (one week / 31 days of data) |
| `SF_zone[k]` | the same sensitivity of the zone price, regressed directly from the zone's own price (RT ridge, alpha 1e4 / focal 1e3; DAM 1e3, the phase-2 design). All 504 RT and 3,816 DAM constraint-zone regressions are direct (R2 at least 0.96 RT, 0.98 DAM; the fallback to the bus mean is never used). A node with the zone's own sensitivity gets exactly the zone price. | DERIVED |
| `mu_tilde[k,t]` | **synthetic** shadow prices of the 260 replayed constraints (63 `rt_reg` + 197 non-attenuated `dam_reg`, 188 elements) | SIM |

**What `mu_tilde` replays.** The 2025 congestion structure is laid on every market year from 2019 on. For each constraint:

1. *Annual shadow-price-hours target* (`sph`, $ per MW of limit per year). Where the IMM allocated a 2025 RT rent to the element (6 constraints on 4 elements) it is rent / limit; otherwise the
   window rent (RT week or DAM month) annualised x 365/days x `WINDOW_PERSISTENCE` (0.15), and an element outside the IMM top ten is capped at the tenth-largest rent ($50.83M) at element level.
   The same targets as agent 4's `sph_table` (a test compares them).
2. *Binding hours* `H_k`: the IMM's published hours where it has them (3 constraints); else IMM rent x 8.48 h per $M, the median RT ratio (3 constraints); else the window's hours annualised with the persistence split between hours and
   magnitude (`HOURS_SHARE_OF_PERSISTENCE` 0.5), at least 24 h, at most 4,000 h, raised if the mean shadow price would exceed half the cap. Contingencies of one element share one set of binding intervals, so an element
   never binds longer than its real hours.
3. *Driver*: one per element, a hub or zone spread against HB_BUSAVG. Candidates are the locations whose zone shift factor says the element moves that location's price in the right direction
   (`DRIVER_RULE = "physics"`: zone `abs(SF_zone)` at least 50% of the element's largest, sign from `SF_zone`). It is fitted on the RT week (at least 24 binding intervals) else the DAM month (at least 6 hours) and kept if the signed
   correlation with the observed shadow price is at least 0.20; otherwise a named geographic rule (the zone with the largest `abs(SF_zone)` -> its hub, Panhandle box -> HB_PAN, the element's own zone).
   Result, by constraint: 43 of the 260 take an RT-fitted driver, 97 a DAM-fitted one, 120 the geographic rule (median fitted correlation 0.37).
4. *Timing*: each year the 4 H_k intervals with the highest driver bind (scaled by the year's fraction in 2026), mu proportional to the excess over the threshold, water-filled to the target under the shadow-price cap
   (SCED's posted cap where there is one, else `SHADOW_CAP_DEFAULT`: 345 kV $4,500, 138 kV $3,500, 69 kV $2,800, interface $5,251). Annual shadow-price-hours hit the target to 1e-16 in every year (`lane_validation.csv`, check b).

The synthetic structure carries **$889M a year of replayed rent over 260 constraints**. Elements with no usable shift factor are not replayed at all (Vealmoor-Koch Tap, WESTEX, Odessa EHV-Yarbrough and others; see the caveats).
Zone prices are real and already contain each year's zonal congestion; only the node-minus-zone part is synthetic.

## 3. Validation (`data/lane_validation.csv`, 200 rows)

The only real nodal data are the RT week and the DAM month. Three checks, as the brief asked; the first is the one that tests the formula itself.

**(a) The formula with the actual shadow prices against the actual node basis.** For the 1,043 resource nodes (RT) and 1,036 (DAM) the basis is node price minus its zone price, and the rebuild uses the *published*
shadow prices of the replayed constraints (so only the formula and the shift factors are tested, not the synthetic timing).

| test | corr (pooled) | corr (node median) | MAE $/MWh | MAE if basis = 0 | bias |
|---|---|---|---|---|---|
| RT 3-10 Oct (705 intervals x 1,043 nodes) | 0.994 | 0.957 | 1.85 | 8.20 | +0.84 |
| RT 3-5 Oct, before the fit window (183 x 1,043) | 0.984 | 0.882 | 1.52 | 3.77 | -0.06 |
| DAM 10 Sep-10 Oct (744 h x 1,036 nodes) | 0.969 | 0.973 | 1.14 | 4.63 | +0.23 |
| DAM, day-ahead-fitted constraints only | 0.843 | 0.820 | 2.47 | 4.63 | +0.81 |
| DAM last 5 days (phase 2 holdout) | 0.955 | 0.963 | 1.49 | 6.14 | +0.38 |
| DAM last 5 days, day-ahead-fitted only | 0.625 | 0.720 | 4.53 | 6.14 | +1.59 |

The shift factors were fitted on 5-9 Oct, so the RT week is mostly in sample. The fair test is the slice before the fit window (3-5 Oct) and the DAM holdout (last five days): the rebuild still
removes 60% (RT) and 76% (DAM) of the absolute error of "no basis at all" there. What fails is the day-ahead constraints on their own: the constraints whose shift factors come from the day-ahead fit explain the DAM month
with correlation 0.84 and the holdout days only 0.62; these are the day-ahead regressions that are collinear in the Twin Buttes pocket (see the caveats).

**(b) The synthetic shadow prices against their targets.** 260 constraints x 8 years (2019-2026): annual shadow-price-hours match the target to 4e-16 in every year; 2025 binding 15-minute intervals match 4 H_k to 0.4%;
no constraint-year falls short of its target because of the cap (mean 0.2-0.3% of binding intervals sit at the cap); the IMM-anchored elements reproduce their 2025 rent (Hargrove-Twin Buttes $110.8M and $26.2M on its two contingencies, $137M of the IMM's $145M for the element;
Scurry Chevron-Knapp $68.1M, 587__A $50.8M, STPWAP39_1 $17.7M and $34.0M).

**(c) The control lanes are the arena's.** The 8 zone lanes equal the arena `Market` zone prices exactly (maximum difference 0 over 2,826 days; HB_BUSAVG identical), and the dispatch on them reproduces the arena's own stored zone backtest: the oracle is identical, the 8-zone average of every algorithm is within
$0.06/kW-yr, and a single zone differs by at most $2.8 (A3, LCRA) or $0.9 (A5). The prices are identical, so the difference sits in the learned models: the lane run starts its history in 2019, the arena's stored backtest in 2011,
and agent 3a's lane-safe context moves zone results by up to $0.05 (A3). The shorter warm-up is the likely cause (not tested).

**Scale check (d).** The mean absolute node-minus-zone gap in the real RT week is $8.20/MWh over the 1,043 nodes (median node 5.68, 90th percentile 15.99). Rebuilt with the actual shadow prices it is $8.78;
rebuilt with the *synthetic* 2025 replay it is **$2.73** (median 2.14, 90th percentile 5.37). One week cannot calibrate an annual mean, so this is a size check, not a fit: the synthetic basis is a third of that week's, presumably because a year's replay spreads the annualised window rent (x `WINDOW_PERSISTENCE`) and the IMM rents over 8,760 hours.

Other checks in the file: (e) zone shift factors, direct regression for every constraint and zone (R2 minimum 0.96 RT / 0.98 DAM, correlation of the direct SF_zone with the bus mean 0.99 / 0.95); (f) driver rule counts and fit quality.

## 4. What a battery earns at the node (SIM, `data/site_years.csv`)

The arena's own dispatch (`gridsite.runner`, lane-safe context of agent 3a) on each rebuilt lane: A1-A5 are walk-forward (prices up to yesterday), energy only, after the $20/MWh wear cost; the oracle is perfect foresight on the same lane.
2 h = spec `grid` (1 MW / 2 MWh), all 40 candidates and the 8 controls from 1 Jan 2021 to 26 Sep 2026; 4 h = spec `grid4h`, the top 15 by 2 h learner value and the controls from 1 Jan 2024.
A year with fewer than 360 simulated days (2026) is annualised to 365; 2026 is therefore Jan-Sep only, and the missing Q4 is the cheap season. `data/site_years.csv` has 476 rows: spec x lane x period (each year, `mean_2021_25`, `mean_2023_25`, `mean_2024_25`)
with $/kW-yr for A1-A5 and the oracle, $/MWh discharged, the uplift of the node over its zone control in the same run, the learner's share of the oracle and its favourite expert.

### 4.1 Two-hour battery, 2021-25 mean ($/kW-yr)

| lanes | A1 | A2 | A3 | A4 | A5 | oracle | A5 / oracle | A4 / oracle | A1 / oracle |
|---|---|---|---|---|---|---|---|---|---|
| 8 zone controls | 39.8 | 42.9 | 37.6 | 29.6 | 44.4 | 103.7 | 0.43 | 0.29 | 0.38 |
| 40 candidates | 50.1 | 81.0 | 69.4 | 60.3 | 78.3 | 176.2 | 0.44 | 0.34 | 0.28 |
|   import side (30) | 56.6 | 88.7 | 75.0 | 67.6 | 85.6 | 190.3 | 0.45 | 0.36 | 0.30 |
|   export side (10) | 30.6 | 57.8 | 52.5 | 38.5 | 56.5 | 133.9 | 0.42 | 0.29 | 0.23 |
|   69 kV (14) | 56.1 | 108.8 | 91.3 | 80.7 | 105.2 | 227.4 | 0.46 | 0.35 | 0.25 |
|   138 kV (26) | 46.9 | 66.0 | 57.5 | 49.4 | 63.9 | 148.7 | 0.43 | 0.33 | 0.32 |

By calendar year (A5, mean over lanes):

| year | zones A5 | nodes A5 | uplift | zones oracle | nodes oracle | nodes with A2 > A5 |
|---|---|---|---|---|---|---|
| 2021 | 22.2 | 52.6 | 30.3 | 114.1 | 172.0 | 23 |
| 2022 | 44.9 | 74.3 | 29.4 | 116.8 | 187.0 | 35 |
| 2023 | 108.2 | 146.0 | 37.8 | 186.3 | 260.5 | 38 |
| 2024 | 28.9 | 64.1 | 35.2 | 58.3 | 139.1 | 22 |
| 2025 | 17.8 | 54.6 | 36.8 | 42.8 | 122.5 | 19 |
| 2026 (to 26 Sep, annualised) | 11.2 | 41.3 | 30.1 | 35.8 | 113.0 | 21 |

The zone controls reproduce the arena's own numbers (section 3(c)): the 8-zone average of the learner is $44.4 here and $44.5 in the arena's stored backtest, the oracle $103.7 in both.
The nodes are **not** a different market with higher prices, they are the same zone price plus the synthetic basis: the uplift is almost constant year to year ($29-38), while the zone value moves from $108 (2023) to $11 (2026 to date). Nodal siting therefore matters most
in the years when the zone itself earns little: 2025 is **3.1x** the zone ($54.6 against $17.8), 2021-25 is 1.8x.

### 4.2 Which algorithm wins at a node, and does the Spike hunter do better in pockets?

| algorithm | zones | nodes | gain ($) | nodes / zones | share of oracle: zones | share of oracle: nodes | change (points) | $/MWh zones -> nodes | best at (nodes / zones) |
|---|---|---|---|---|---|---|---|---|---|
| A1 Clockwork | 39.8 | 50.1 | 10.3 | 1.26 | 0.384 | 0.284 | -9.9 | 54 -> 69 | 0 / 0 |
| A2 Forecaster | 42.9 | 81.0 | 38.0 | 1.89 | 0.414 | 0.460 | +4.5 | 81 -> 110 | 36 / 1 |
| A3 Weather-regime | 37.6 | 69.4 | 31.8 | 1.85 | 0.362 | 0.394 | +3.1 | 69 -> 97 | 0 / 0 |
| A4 Spike hunter | 29.6 | 60.3 | 30.7 | 2.04 | 0.286 | 0.342 | +5.7 | 225 -> 445 | 0 / 0 |
| A5 Adaptive learner | 44.4 | 78.3 | 33.9 | 1.76 | 0.428 | 0.444 | +1.6 | 90 -> 134 | 4 / 7 |
| oracle | 103.7 | 176.2 | 72.6 | 1.70 |  |  |  |  |  |

- **The Forecaster (A2), not the learner (A5), earns the most at 36 of the 40 nodes** ($81.0 against $78.3 on average); at the zones the learner wins 7 of 8. The learner follows whichever of its six experts has earned most recently, and at the nodes that
  is the Forecaster on most days (25% of days at the nodes against 15% at the zones, 45% at the Twin Buttes five, table below). A plausible reason (not tested): when one expert is best persistently, tracking it with a lag costs the learner a little; at the zones the best expert changes from year to year, which is what the learner is for.
  Whether real pockets are as predictable as the synthetic ones is open: the synthetic basis is a function of hub spreads the Forecaster can read (caveat 4).
- **Spike hunter (A4): yes, relatively; no, absolutely.** It gains the most from siting at a node: x2.04 against x1.76 for the learner, +5.7 points of the oracle against +1.6, and its margin per MWh doubles from $225 to $445 (a battery that keeps itself full and sells only
  into the top few percent earns more when the top few percent are higher). It is still below the learner at all 40 nodes over 2021-25 (by $18 on both the import and the export side; by $24 at 69 kV and $15 at 138 kV), and above it at 5 of 40 in 2025 alone.
  In the 4 h run it is the best algorithm at the three Houston PK-DT nodes (BG, GT, PK; section 4.3). The stronger the pocket, the further behind it falls in dollars (correlation of A4 - A5 with the oracle uplift: -0.85).
- **Clockwork (A1) loses ground in pockets**: its share of the oracle falls from 38% to 28% (to 23% on the export side), because a fixed daily schedule cannot follow a basis that binds on a few days.
- The learner's share of the oracle is about the same at nodes and zones (0.44 against 0.43); no algorithm closes the gap to the ceiling in a pocket ($176 at nodes against $104 at zones for the oracle).

Share of days the learner follows each expert (A5):

| index | zone controls | candidate nodes | Twin Buttes five |
|---|---|---|---|
| Clockwork | 35% | 13% | 4% |
| Forecaster | 15% | 25% | 45% |
| Weather-regime | 18% | 20% | 21% |
| Spike hunter q97 | 18% | 21% | 11% |
| Spike hunter q90 | 10% | 11% | 7% |
| Forecaster (low carry) | 4% | 10% | 12% |

### 4.3 Four-hour battery (top 15 by 2 h learner value, 2024-25 mean, $/kW-yr)

| lane | 4h A5 | 2h A5 same years | 4h / 2h | 4h oracle | A5 / oracle | uplift vs zone | $/MWh | best of A1-A5 | fwd 2027 | fwd 2031 |
|---|---|---|---|---|---|---|---|---|---|---|
| MIDW | 339.3 | 248.1 | 1.37 | 582.7 | 0.58 | 297.3 | 298 | A2 | 266.5 | 119.4 |
| STRS | 311.6 | 228.5 | 1.36 | 539.7 | 0.58 | 269.7 | 283 | A2 | 245.1 | 111.3 |
| POWELLFI | 242.6 | 177.1 | 1.37 | 436.3 | 0.56 | 200.7 | 237 | A2 | 191.5 | 91.9 |
| MILES | 123.6 | 90.8 | 1.36 | 194.2 | 0.64 | 81.7 | 83 | A3 | 110.3 | 48.0 |
| KNAPP | 111.4 | 70.1 | 1.59 | 207.7 | 0.54 | 69.4 | 117 | A2 | 98.0 | 84.6 |
| NOVICE | 109.0 | 75.9 | 1.44 | 172.2 | 0.63 | 67.1 | 74 | A5 | 95.5 | 45.8 |
| SHRNE | 99.1 | 63.2 | 1.57 | 188.5 | 0.53 | 57.1 | 111 | A2 | 85.9 | 72.3 |
| HILND | 86.4 | 62.0 | 1.39 | 159.9 | 0.54 | 58.2 | 105 | A2 | 80.9 | 67.1 |
| CRNSO | 86.2 | 61.9 | 1.39 | 159.5 | 0.54 | 58.0 | 104 | A2 | 80.8 | 67.1 |
| SEGOVI | 85.7 | 51.4 | 1.67 | 152.0 | 0.56 | 46.3 | 80 | A2 | 72.6 | 59.0 |
| BULLCRK | 80.6 | 52.1 | 1.55 | 157.8 | 0.51 | 38.7 | 93 | A2 | 70.1 | 61.5 |
| BG | 80.1 | 60.0 | 1.33 | 108.9 | 0.74 | 52.8 | 155 | A4 | 84.7 | 84.5 |
| GT | 77.1 | 55.5 | 1.39 | 106.5 | 0.72 | 49.8 | 138 | A4 | 81.7 | 82.1 |
| CRNTH | 76.4 | 55.6 | 1.37 | 143.8 | 0.53 | 48.2 | 93 | A2 | 71.4 | 59.9 |
| PK | 72.3 | 56.7 | 1.28 | 105.5 | 0.69 | 45.0 | 127 | A4 | 77.0 | 77.4 |
| 8 zone controls (mean) | 32.2 | 23.4 | 1.38 | 62.7 | 0.51 |  | 39 |  |  |  |

A 4 h battery earns 1.3-1.7x the 2 h one at the nodes (mean $132 against $94 over the same years; 1.38x at the zones) and its learner captures more of the oracle (0.58 against 0.48 at the nodes), so the longer duration is worth most where
the pocket is wide (SEGOVI x1.67, KNAPP x1.59, BULLCRK x1.55) and least at the short Houston spikes (BG x1.33, PK x1.28). The Forecaster is best at 10 of the 15, the Spike hunter at BG, GT and PK, the Weather-regime at MILES, the learner at NOVICE.
`fwd` columns are the 4 h forward values of section 5 (oracle on the rebuilt 2025 prices x the node's A5/oracle ratio of 2024-25).

### 4.4 How far to trust the levels

*Is the screening score a value?* A stratified sample of 36 eligible substations that are not among the 40 (6 per score band), 2h oracle on the 2025 replay lane, against the zone's oracle:

| score band (pool of 1,250) | sampled | mean score $/MW-yr | mean 2025 oracle uplift $/kW-yr | median |
|---|---|---|---|---|
| bottom half | 6 | 14,035 | -0.9 | -0.8 |
| 50-75% | 6 | 33,029 | 3.5 | 3.9 |
| 75-90% | 6 | 49,869 | 6.4 | 6.1 |
| 90-95% | 6 | 89,493 | 34.4 | 26.1 |
| 95-99% | 6 | 188,262 | 64.6 | 56.9 |
| top 1% (not in the 40) | 6 | 418,223 | 126.8 | 81.7 |

Spearman correlation of the score with the oracle uplift over the 36: **0.94**. The score orders the pool well and the value sits at the top: below the 90th percentile of the score the mean 2025 oracle uplift is under $7/kW-yr, the 95-99% band has $65 and the top 1% $127.

*Does the answer depend on the SIM choices?* 2025 oracle uplift of 14 candidates (every third one) under each alternative, rebuilt from scratch (no caches):

| variant | mean uplift | median uplift | rank correlation with base |
|---|---|---|---|
| base case (as built) | 89.6 | 49.7 | 1.00 |
| WINDOW_PERSISTENCE 0.075 (half) | 74.9 | 26.7 | 0.98 |
| WINDOW_PERSISTENCE 0.30 (double) | 111.6 | 85.7 | 0.90 |
| attenuated (+shrunk) day-ahead SFs included | 91.2 | 50.5 | 0.99 |
| driver rule 'data' (best correlation over all 13 locations) | 85.8 | 47.3 | 0.95 |
| MIN_DRIVER_CORR 0.40 (more elements fall back to geography) | 81.5 | 42.2 | 0.90 |
| HOURS_SHARE_OF_PERSISTENCE 1.0 (persistence all in hours) | 90.8 | 53.7 | 1.00 |

The ranking is stable (rank correlation 0.90 or more in every variant); the **level** is not: it moves by -16% to +25% in the mean, and by -46% to +72% in the median, because every window-anchored element (all but the four IMM-anchored ones) scales with `WINDOW_PERSISTENCE`, whereas the IMM-anchored Twin Buttes pocket does not (MIDW's oracle uplift stays within
$425-438 across the persistence range). Treat every window-anchored candidate value as a factor-of-two band, and every Twin Buttes value as a different kind of number (anchored on a published rent and hours, but on collinear day-ahead shift factors).

*Replay against the dispatch.* The forward values of section 5 apply the 2023-25 learner/oracle ratio to the oracle on the rebuilt 2025 prices. For 2025 itself that gives $62.4 against $54.6 from the actual dispatch at the nodes (+14%, mean absolute error $9.0, correlation 0.99) and $23.1 against $17.8 at the zones:
the 2023-25 ratio is higher than 2025's. The oracle itself is reproduced to 5e-5 $/kW-yr (`test_value_sites_reproduces_the_dispatch_oracle`).

## 5. 2027-2031: planned upgrades, storage saturation and the basis half-life (SIM)

**Method** (`sites.value_sites`, `saturation.py`). The history stops at 26 Sep 2026; the future is the 2025 congestion replay with each constraint's synthetic shadow prices multiplied by a scale for the year.
The value is the **perfect-foresight oracle on those rebuilt 2025 prices x the lane's own learner/oracle ratio** from the backtest (A5 over oracle, 2023-25 mean for 2 h, 2024-25 for 4 h; the zone control's own ratio is used for a lane that has none).
Zone prices stay at their 2025 values in every forward year (no forecast of zone prices is attempted), so only the basis moves; the uplift of a node over its zone is the part to read.
Learner values of 2027-31 are therefore on a different footing from the 2021-26 dispatch rows (a ratio, not a walk-forward run, and a 2025 price year): 2025 itself is $62.4 by replay against $54.6 by dispatch at the nodes (section 4.4).

**Scale factors.**

1. *Planned upgrades* (`upgrade_scale`): agent 4's `planned_schedule()` (66 constraints with a matched project at confidence 0.5 or more and a date; the three dated 2025 are already in the base year, and the seven window-anchored ones in service before October 2026 are already in the window data). The scale falls linearly over the in-service year from 1 to a residual
   (`UPGRADE_RESIDUAL` 0.10 for lines and transformers, `UPGRADE_RESIDUAL_GTC` 0.50 for interfaces; the same constants as `gridsite.upgrades`, a test compares them). They touch 13% of the replayed shadow-price-hours (weighted mean scale 0.95 in 2031),
   **but none of the 40 candidates**: of the eleven dominant elements the only one on the schedule is LWSVH-ARGYL (587__A), dated 1 Dec 2032. Twin Buttes-Hargrove and College-Jupiter have a matched RTP project with no date; the three TPIT matches
   (Fort Mason-Yellow Jacket, PK-DT, HIGH36-BRENNO) are at confidence 0.3, below the 0.5 the schedule requires.
2. *Storage saturation* (`saturation_scale`): `scale = max(SAT_FLOOR, 1 - SAT_SLOPE x relief / limit)` per constraint, `relief` = sum over storage projects arriving after 2025 of effective MW online x |SF| to the constraint,
   import-side projects only (SF <= -`SAT_MIN_SF`, 0.05). Projects: `data/queue_sub_map.csv` at mapping confidence 0.5 or more, 354 project-substation rows, **41.2 GW listed**: 4.1 GW already operating in 2026 (EIA-860M), 34.4 GW of queue projects with an interconnection agreement signed and
   2.7 GW of firm EIA-planned units; the unbuilt ones count at `SAT_REALISATION` 0.6. Effective MW online (pro-rated within the year): 3.5 GW in 2026, 10.4 GW 2027, 18.6 GW 2028, 24.6 GW 2029, 25.9 GW 2030, 26.1 GW 2031. A constraint whose target is the Oct 2026 window
   already contains the storage operating by then, so only unbuilt projects count against it; a constraint anchored on the 2025 IMM rent counts all of them. Mean scale over the 260 constraints (weighted by shadow-price-hours): 0.92 in 2027, 0.70 in 2029, 0.69 in 2031
   (0.73 from saturation alone, 0.95 from upgrades alone). After 2029 the fleet stops growing, so the scale is flat.

**Result for the 40 candidates (2 h, learner, mean over the 40; zone controls stay at $23.1):**

| year | learner, 40 nodes | zone controls | uplift over zone | uplift / 2025 | mean scale (exposure-weighted) | storage online, GW |
|---|---|---|---|---|---|---|
| 2025 (replay) | 62.4 | 23.1 | 38.4 | 1.00 | 1.00 |  |
| 2026 | 61.1 | 23.1 | 37.1 | 0.97 | 0.98 | 3.5 |
| 2027 | 57.1 | 23.1 | 33.1 | 0.86 | 0.91 | 10.4 |
| 2028 | 48.5 | 23.1 | 24.6 | 0.64 | 0.76 | 18.6 |
| 2029 | 43.3 | 23.1 | 19.3 | 0.50 | 0.66 | 24.6 |
| 2030 | 43.3 | 23.1 | 19.3 | 0.50 | 0.66 | 25.9 |
| 2031 | 43.2 | 23.1 | 19.3 | 0.50 | 0.66 | 26.1 |

The pocket premium halves by 2029 and then holds: mean uplift over the zone $38.4 in the 2025 replay, $33.1 in 2027, $24.6 in 2028, $19.3 in 2029-31. **All of it is saturation**: with only upgrades the mean 2031 learner value changes by +$0.05 (no candidate by more than $1.1),
with only saturation by -$19.2 (both: -$19.1). Twin Buttes and Fort Mason are the elements that saturate; the rest of the dominant elements keep 74-100% of their shadow prices.

**Basis half-life** (first year from 2026 whose learner uplift over the zone is below 50% of its 2025 replay value): **9 sites in 2028** (MIDW, STRS, POWELLFI, MILES, NOVICE behind Twin Buttes; SEGOVI and EOLA behind Fort Mason; ROUGH, LWSVW),
**6 in 2029** (SHELLLON, JUNCTION, MASONSW, TMPTV, PGE, ECRSW) and **25 "> 2031"**. For sites with a small 2025 uplift (ROUGH $3.7, ECRSW $7.4, LWSVW $7.7) the 50% line is a few dollars and the year is noise. Top 15, with the learner values by year:

| sub | constraint | 2025 dispatch | 2025 replay | 2026 | 2027 | 2028 | 2029 | 2031 | 2031 / 2025 uplift | scale 2031 | half-life |
|---|---|---|---|---|---|---|---|---|---|---|---|
| MIDW | HARGRO_TWINBU1_1 | 237.6 | 247.7 | 235.0 | 203.4 | 133.7 | 87.4 | 87.3 | 0.27 | 0.30 | 2028 |
| STRS | HARGRO_TWINBU1_1 | 214.7 | 226.8 | 215.2 | 186.5 | 123.1 | 81.0 | 81.0 | 0.27 | 0.29 | 2028 |
| POWELLFI | HARGRO_TWINBU1_1 | 165.7 | 178.6 | 169.8 | 147.7 | 99.4 | 67.4 | 67.4 | 0.26 | 0.30 | 2028 |
| KNAPP | 6437__F | 53.9 | 73.3 | 72.2 | 69.4 | 63.4 | 59.3 | 59.1 | 0.69 | 0.76 | > 2031 |
| MILES | HARGRO_TWINBU1_1 | 101.4 | 95.9 | 92.6 | 82.9 | 53.1 | 32.9 | 32.9 | 0.08 | 0.39 | 2028 |
| SHRNE | 6437__F | 47.3 | 65.7 | 64.6 | 61.7 | 55.3 | 51.1 | 50.9 | 0.61 | 0.71 | > 2031 |
| NOVICE | HARGRO_TWINBU1_1 | 83.7 | 81.2 | 78.2 | 69.8 | 45.8 | 30.5 | 30.6 | 0.06 | 0.38 | 2028 |
| SEGOVI | FORTMA_YELWJC1_1 | 42.6 | 55.3 | 54.7 | 48.9 | 40.3 | 38.8 | 38.9 | 0.39 | 0.53 | 2028 |
| HILND | 587__A | 53.2 | 65.9 | 65.8 | 65.6 | 60.7 | 54.1 | 54.1 | 0.74 | 0.73 | > 2031 |
| CRNSO | 587__A | 53.6 | 65.9 | 65.8 | 65.6 | 60.8 | 54.2 | 54.2 | 0.74 | 0.73 | > 2031 |
| BULLCRK | 6437__F | 38.6 | 54.1 | 53.4 | 51.4 | 47.2 | 44.5 | 44.3 | 0.64 | 0.73 | > 2031 |
| BG | DT_PK_91_A | 62.6 | 59.8 | 59.7 | 59.5 | 59.4 | 59.4 | 59.4 | 0.99 | 0.90 | > 2031 |
| GT | DT_PK_91_A | 60.8 | 57.4 | 57.4 | 57.3 | 57.4 | 57.5 | 57.5 | 1.00 | 0.91 | > 2031 |
| CRNTH | 587__A | 48.3 | 58.8 | 58.8 | 58.6 | 54.5 | 48.9 | 48.8 | 0.73 | 0.72 | > 2031 |
| PK | DT_PK_91_A | 63.4 | 58.0 | 58.0 | 57.9 | 58.0 | 58.2 | 58.2 | 1.00 | 0.91 | > 2031 |

*Reading it.* `2025 dispatch` is the walk-forward result, `2025 replay` the oracle-ratio value of the same year, `scale 2031` the mean scale over all of the node's constraints weighted by its exposure. The four Houston PK-DT nodes (BG, GT, PK, MID: $57-60) hold their value: almost no planned storage has a shift factor of 0.05 or more to PK-DT (its 2031 scale is 0.99).
The Scurry Chevron-Knapp nodes keep 61-69% of their uplift although their own element keeps 99% of its shadow prices, because Twin Buttes is their second exposure (its score term is 52%, 78% and 56% of the first for KNAPP, SHRNE and BULLCRK); the Argyle-Lewisville nodes keep 73-74% (scale 0.76 on their one element).

**Why Twin Buttes falls so far, and how fragile that is.** The relief on Twin Buttes-Hargrove (limit 151 MW) is 111 MW in 2031 (scale 0.27 on its main contingency, 0.49 on the other). It comes from **15 storage-hosting substations that all have a shift factor of about -0.065 to it
(t about 7, quality `ok+collinear`)**, 2.8 GW listed and 1.8 GW realised by 2031 (`RIOPECOS` 524 MW, `ROBBSW` 410, `SGSA` 238, `SODALA` 2 x 205, `CROSSETT` 200, `COYOTSPR` 182, `HEADCAMP` 150 operating since June 2026, ...): about 0.065 x 1.8 GW against a 151 MW line.
That is a physical mechanism (a regional cluster, not noise), but it is a **cliff in `SAT_MIN_SF`**: the same cluster sits below 0.075, so raising the threshold from 0.05 to 0.075 moves the Twin Buttes 2031 scale from 0.31 to 0.92, and the five Twin Buttes sites then keep their value to 2031 (re-run with the constant changed: MIDW $247.7 in 2025, $247.0 in 2031; half-life "> 2031" for all five).
It also leans on the day-ahead fits behind those SFs (collinear), and on `SAT_REALISATION`, `SAT_SLOPE` and the import-only rule. Scale of each dominant element in 2031 from saturation alone (sph-weighted over its constraints):

| dominant element | base (slope 1.0, realisation 0.6, import side) | slope 0.5 | slope 2.0 | realisation 0.3 | realisation 1.0 | both sides |
|---|---|---|---|---|---|---|
| HARGRO_TWINBU1_1 | 0.31 | 0.65 | 0.10 | 0.62 | 0.11 | 0.15 |
| 6437__F | 0.99 | 0.99 | 0.97 | 0.99 | 0.98 | 0.99 |
| COL_BRAN_1 | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 0.97 |
| FORTMA_YELWJC1_1 | 0.58 | 0.79 | 0.16 | 0.79 | 0.30 | 0.49 |
| 215T215_1 | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 |
| COLLE_JUPIT_1 | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 0.97 |
| LAKENA_SAMATH1_1 | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 0.98 |
| DT_PK_91_A | 0.99 | 0.99 | 0.97 | 0.99 | 0.98 | 0.66 |
| SEA_AAT1 | 0.74 | 0.87 | 0.47 | 0.87 | 0.56 | 0.74 |
| 587__A | 0.76 | 0.88 | 0.51 | 0.88 | 0.59 | 0.75 |
| 6429__D | 0.99 | 1.00 | 0.98 | 1.00 | 0.98 | 0.96 |

and with the minimum shift factor changed:

| index | HARGRO_TWINBU1_1 | 6437__F | FORTMA_YELWJC1_1 | 587__A | DT_PK_91_A | SEA_AAT1 | sph-weighted all |
|---|---|---|---|---|---|---|---|
| SAT_MIN_SF 0.05 | 0.31 | 0.99 | 0.58 | 0.76 | 0.99 | 0.74 | 0.73 |
| SAT_MIN_SF 0.075 | 0.92 | 1.00 | 0.58 | 0.76 | 1.00 | 0.74 | 0.89 |
| SAT_MIN_SF 0.1 | 0.95 | 1.00 | 0.58 | 0.76 | 1.00 | 0.75 | 0.91 |
| SAT_MIN_SF 0.15 | 0.95 | 1.00 | 0.58 | 0.76 | 1.00 | 0.79 | 0.92 |
| SAT_MIN_SF 0.2 | 0.95 | 1.00 | 0.58 | 0.76 | 1.00 | 0.79 | 0.93 |

Both effects hit only a handful of elements, which is why the sph-weighted mean scale over all 260 moves from 0.73 to 0.89 with that one threshold. Take the five Twin Buttes sites' half-life as *"2028 if the cluster counts, later if it does not"*, not as a forecast.

**What a planned upgrade does to a site (the API for the next phase).** `value_sites` takes the scale vector of an upgrade through `mu_scale` ({constraint id or element name: multiplier}; `gridsite.upgrades.apply_upgrade` returns exactly that). It multiplies the year's baseline, so pass `baseline="none"` to isolate the upgrade:

```python
from gridsite import sites as S, upgrades as U
ms = U.apply_upgrade("HARGRO_TWINBU1_1")                      # the element's contingencies -> 0.10 (reconductor residual); 0.0 = full relief
S.value_sites(["MIDW", "MILES", "LZ_WEST"], years=(2025,), baseline="none")                   # the 2025 table
S.value_sites(["MIDW", "MILES", "LZ_WEST"], mu_scale=ms, years=(2025,), baseline="none")      # after the upgrade
S.value_sites(["MIDW", "MILES"], mu_scale=ms, years=(2029,), baseline="saturation")           # upgrade + the storage that arrives by 2029
```

| lane | 2025 learner, as is | upgrade only | upgrade + 2029 saturation | zone control |
|---|---|---|---|---|
| MIDW | 247.7 | 51.0 | 35.7 | 27.3 |
| MILES | 95.9 | 36.4 | 31.2 | 27.3 |

(`mu_scale=None, years=(2025,)` reproduces the 2025 oracle of `site_years.csv` to 5e-5 $/kW-yr; keys that are not in the replayed set, here four of the six contingencies `apply_upgrade` returns, are ignored and listed in `df.attrs["ignored_mu_scale_keys"]`. About 10 s for three lanes on one worker; results are cached in `derived/runs/value_cache.json`.)

## 6. Where each siting rule puts 1,000 MW in 2027-2031 (`data/portfolio.csv`, 93 rows)

`arena.siting.portfolio` per rule A1-A5 (the rules of the arena: A1 last year's best option, A2 three-year mean plus half the trend, A3 five-year mean minus half a standard deviation with 50/30/20 shares, A4 best single year of the last three,
A5 recency-weighted and risk-adjusted), in 10 MW units, `UNITS_PER_YEAR` = 20 (200 MW a year, 1,000 MW in five years). Options are the 40 candidates plus, as a fallback, the 8 load zones (an uncongested node in that zone at the zone price, no cap):
the congested nodes can only absorb a few hundred MW, and the rest of the fleet has to go somewhere.
Each rule sees, as the arena's own forward runs do, its own dispatch result at every option for 2021-2026 (the backtest rows, 2026 annualised), then the forward learner values of each year as they happen (rows 2027-2031 of `forward_values`, oracle-ratio method for the rule's own algorithm);
the realised value is the same matrix.

**Caps** (the new `groups` hook of agent 3a's `siting.portfolio`, counted over the whole fleet, never per year):

- *Nodes behind the same constraint share a price-taker cap of `PRICE_TAKER_SHARE` = 10% of the constraint's median limit*, in whole 10 MW units rounded down, at least one unit (`group_cap_units`). Eleven groups, one per dominant element:

| constraint | nodes behind it | median limit MW | 10% of limit | group cap MW (whole units) | candidates |
|---|---|---|---|---|---|
| 215T215_1 | 5 | 238.0 | 23.8 | 20 | SANDHI, BRENNO, GAYHIL, LYONS, CHAPHI |
| 587__A | 4 | 216.5 | 21.6 | 20 | HILND, CRNSO, CRNTH, LWSVW |
| 6429__D | 2 | 409.7 | 41.0 | 40 | ECRSW, ROUGH |
| 6437__F | 5 | 214.5 | 21.5 | 20 | KNAPP, SCRCV, SHRNE, BULLCRK, MPPOI |
| COLLE_JUPIT_1 | 5 | 272.0 | 27.2 | 20 | APOLLO, MILLERRD, WYNJOYCE, LYONS_C, CENTRVIL |
| COL_BRAN_1 | 1 | 272.0 | 27.2 | 20 | COLLEGE |
| DT_PK_91_A | 4 | 292.6 | 29.3 | 20 | BG, PK, MID, GT |
| FORTMA_YELWJC1_1 | 5 | 145.1 | 14.5 | 10 | EOLA, SEGOVI, SHELLLON, JUNCTION, MASONSW |
| HARGRO_TWINBU1_1 | 5 | 151.0 | 15.1 | 10 | MIDW, STRS, MILES, POWELLFI, NOVICE |
| LAKENA_SAMATH1_1 | 1 | 38.0 | 3.8 | 10 | TANK |
| SEA_AAT1 | 3 | 99.3 | 9.9 | 10 | TMPTV, PGE, SEA |

  Together the eleven caps allow 200 MW out of 1,000. A 100 MW battery at MIDW would offset 66% of the Twin Buttes limit (100 MW x |SF| 1.00 against a 151 MW limit), so a cap near 10% is the reason the numbers below are not the node values of section 4: those are first-MW values.
- *Per-node point-of-interconnection cap* by voltage class: 69 kV 50 MW, 138 kV 150 MW, 345 kV 300 MW (`POI_CAP_MW`). It never binds: the largest group cap is 40 MW.

| siting rule | capped $/kW-yr | MW at nodes / zone fallback | nodes used | uncapped $/kW-yr | uncapped: nodes used, MW at the top node | zones only $/kW-yr |
|---|---|---|---|---|---|---|
| A1 Clockwork rule | 28.5 | 160 / 840 | 10 | 45.3 | 3, 400 | 25.2 |
| A2 Forecaster rule | 33.9 | 200 / 800 | 11 | 100.0 | 2, 500 | 25.6 |
| A3 Weather-regime rule | 28.6 | 200 / 800 | 11 | 67.6 | 3, 500 | 22.4 |
| A4 Spike hunter rule | 23.9 | 200 / 800 | 11 | 80.8 | 2, 600 | 16.9 |
| A5 Adaptive learner rule | 34.9 | 200 / 800 | 11 | 101.3 | 1, 1000 | 27.8 |

*capped* = the run with both caps; *uncapped* = no cap of any kind (the arena's original behaviour: it puts everything on the best option); *zones only* = the same rule choosing among the 8 zone lanes.

- **With the caps, siting at the congested nodes adds $3-8/kW-yr to the fleet average** (A1 +3.3, A2 +8.3, A3 +6.2, A4 +7.0, A5 +7.2), against $20-74 without them: the learner rule gets $34.9 against $27.8 in the zones only (+26%), the Forecaster rule $33.9 against $25.6.
  Only 160-200 MW can be placed at a node at all: all of it in 2027 for A2, A3 and A5, in 2027-28 for A1 and A4; the later years go to the zone fallback (A5: LZ_WEST in 2028, then LZ_LCRA at $28.4).
- **Without caps each rule puts all 1,000 MW on one to three nodes, 400-1,000 MW on the top one** (the learner rule: all 1,000 MW at MIDW, $101/kW-yr). That is the number a naive "best site" ranking gives, and it is wrong by a factor of three: 1 GW cannot sit behind a 151 MW line.
- **The learner rule (A5) and the Forecaster rule (A2) are first and second in all three columns.** The Spike hunter rule (A4, best single year of the last three) is last with the caps and in the zones only ($23.9 and $16.9) and third without caps (it stacks one pocket); Clockwork and Weather-regime are in between.
- A5's 2027 allocation: MIDW 10 MW (Twin Buttes cap), KNAPP 20 (Scurry Chevron-Knapp), HILND 20 (Argyle-Lewisville), BG 20 (PK-DT), LYONS 20 (HIGH36-BRENNO), APOLLO 20 and COLLEGE 20 (College-Jupiter and Brand-College), SEGOVI 10 (Fort Mason), SEA 10, TANK 10, ECRSW 40: 200 MW at eleven nodes.
  The rule goes greedy, one unit at a time, on the recency-weighted risk-adjusted value; a full group blocks all of its members, which is what the cap is for.

The portfolio ignores connection: all 200 MW are placed in 2027, while the connection lead time is 38.5 months at 69 kV and 56.5 at 138 kV (`data/connection_costs.csv`); a request filed today (Oct 2026) is energised in late 2029 at a 69 kV site and mid-2031 at a 138 kV one.

## 7. Costs, ancillary services and the final table (`data/sites.csv`, 40 rows x 150 columns)

`sites.csv` joins everything above, one row per candidate, with a label (REAL / DERIVED / SIM / ASSUMPTION) for every column in `sites.labels.csv`. Column groups:
identity and placement (sub, kV, county, zone, lat/lon, node bus, existing resource node); the **top-3 constraints** with side, SF, SF method (`rt_reg` / `dam_reg`), hops and the curtail and upgrade flags from phase 2;
**2 h** A1-A5, oracle and learner share for 2021-25 mean, 2025 and 2026 to date (annualised), $/MWh, uplift against the zone control; **4 h** where run; the **AS line**; **forward** learner value and uplift for 2027, 2029, 2031, the 2025 replay value,
the mean of 2027-31 (`sat_adjusted_learner_2027_31_mean`), the 2031 scale and the one-effect-only values, the **basis half-life**; price-taker and POI caps, the share of the dominant element's limit that 100 MW would offset;
**connection** class, confidence, cost range, owner share, step-up, lead time; **net-of-connection value and break-even installed cost**; **risk flags** (phase 5); **reliability** score and rank (phase 5, kept in its own units).

### 7.1 Connection and break-even cost

Connection is `data/connection_costs.csv` (agent 3a, 100 MW reference battery, 2024 USD): 39 of the 40 sites are a *new bay* (facilities $2.3-3.4M, inside the 16 TAC 25.195 allowance, so the owner pays nothing extra) and one (BULLCRK) is a *new switchyard* ($9.8-13.5M);
the step-up transformer is $16.95M for 100 MW (3a's UNVERIFIED proxy) and dominates: all-in **$192-194/kW** (BULLCRK $287/kW). Lead time 38.5 months at the 69 kV sites and 56.5 at the 138 kV ones, where the step-up is a large power transformer.

`conn_usd_kw_yr` = all-in $/kW x CRF(`WACC` 8%, `LIFE_YEARS` 15) = x 0.1168 = **$22.5/kW-yr** ($33.5 for the switchyard). **Net of connection** = the 2027-31 mean learner value (after planned upgrades and storage saturation) minus that;
**break-even installed cost** = that mean value / CRF: the total installed cost, battery and connection together, that the energy value repays over 15 years at 8%. Energy only: AS, capacity payments and the battery's own O&M are not in it.

| sub | mean learner value 2027-31 | connection $/kW-yr | net of connection | break-even installed $/kW | of which for the battery after connection |
|---|---|---|---|---|---|
| MIDW | 119.8 | 22.5 | 97.3 | 1,026 | 833 |
| STRS | 110.5 | 22.5 | 88.0 | 946 | 753 |
| POWELLFI | 89.8 | 22.5 | 67.4 | 769 | 577 |
| KNAPP | 62.1 | 22.6 | 39.4 | 531 | 337 |
| MILES | 46.9 | 22.5 | 24.5 | 402 | 209 |
| SHRNE | 54.0 | 22.6 | 31.4 | 462 | 268 |
| NOVICE | 41.4 | 22.5 | 18.9 | 355 | 162 |
| SEGOVI | 41.2 | 22.5 | 18.7 | 352 | 160 |
| HILND | 57.8 | 22.6 | 35.1 | 494 | 301 |
| CRNSO | 57.8 | 22.6 | 35.2 | 495 | 301 |

Over the 40 sites the net of connection has median **$21.9/kW-yr** (mean 24.2; 22 sites above $20, 3 above $50, 11 below $10), and the break-even installed cost has median **$389/kW** (mean 403; 2 sites above $800, 5 above $500, 14 below $300).
The same formula on a zone control (forward value $23.1, no connection cost) gives **$197/kW**: nodal siting roughly doubles the installed cost that energy arbitrage can carry, and for three of the 40 sites (the Twin Buttes import nodes, first 10 MW only) it makes it large.
For the rest the energy value alone does not carry a typical installed cost; those sites need the AS line, a capacity or reliability payment, or a connection cheaper than the proxy.

### 7.2 Risk and reliability (kept apart from market value)

`risk_*` columns come from phase 5: none of the 40 is excluded; flood zone minimal at 24 (unmapped at 16), wildfire high at SHRNE, BULLCRK and ECRSW and moderate at six others, seismic flag at KNAPP, SCRCV, APOLLO and ECRSW, none coastal.
`reliability_need_score` and `_rank` (of 2,500+ substations) are in their own units (VoLL dollars) and are never added to a market value; 34 of the 40 have a rank. The four Houston PK-DT sites are the only ones in the top 100 (MID rank 10, BG 11, GT 62, PK 64, driver "large load"):
the Houston pocket is both a market-value and a reliability case, unlike the Twin Buttes pocket (MIDW, STRS and POWELLFI have no rank; MILES is 2,051).

### 7.3 Ancillary services: one system-wide line, not added to the energy columns

Phase 3a's AS layer (`gridsite.ancillary`) on the learner's ledger of the 8 zone lanes, `$/kW-yr`:

| | 2025 | 2026 to 26 Sep, annualised |
|---|---|---|
| 2 h: estimate (learner + the AS it could sell in idle intervals and by swapping; mean of the 8 zones) | 15.9 | 11.9 |
| 2 h: upper bound (all capacity in the best day-ahead product every interval) | 31.1 | 31.0 |
| 4 h: estimate | 16.3 | 15.5 |
| 4 h: upper bound | 34.8 | 44.4 |

AS prices are system-wide (day-ahead clearing prices by product, REAL), so the line does not differentiate sites; it is a flat add-on of the same size anywhere. It is as large as the whole zone energy value in 2025 ($17.8) and 2026 ($11.2),
and a minor part of a pocket's ($54.6 at the nodes in 2025, $237.6 at MIDW). The estimate is ASSUMPTION-grade: a floor on what co-optimisation would earn. AS prices have collapsed since 2023 (3a: DAM Reg Up $26.79/MW-h in 2023, $2.11 in 2026 to 10 Oct).

## 8. Caveats (read before using a number)

1. **The node prices are a model, not data.** The formula is validated with the published shadow prices (section 3); the *timing* of the synthetic shadow prices is not. 120 of the 260 constraints (46%) use the geographic driver rule, which has no data behind it, the fitted ones
   have a median signed correlation of 0.37 with the observed shadow price, and the SFs and shadow prices come from one RT week and 31 DAM days annualised with `WINDOW_PERSISTENCE` 0.15 and the IMM cap. The synthetic basis in a typical year has a mean absolute node-minus-zone gap of $2.7/MWh,
   a third of the real week's. Treat levels as good to a factor of two (section 4.4) and rankings as better than that.
2. **Only 260 of 1,386 constraints are replayed**: those with a regression shift factor that is not attenuated. Absent are elements with only an assumed or attenuated SF, including Vealmoor-Koch Tap, the WESTEX interface (attenuated day-ahead fit, sign only), Longshore-Consavvy, Bell County-Salado,
   Laredo VFT North-Las Cruces and Odessa EHV-Yarbrough (agent 4's structural list). Pockets behind them do not appear: no Panhandle candidate, none in LZ_AEN, LZ_CPS or Rayburn. The total replayed rent is $889M a year.
3. **The Twin Buttes pocket dominates and rests on collinear day-ahead shift factors.** Its SFs are day-ahead fits (variance inflation above 100, |SF| capped at 1.0, raw values up to 1.2); the day-ahead-fitted constraints rebuild the DAM month with correlation 0.84 and the holdout days 0.62.
   Its level is anchored on a published IMM rent and hours, so it is right *if* the SF is. Every value is the **first-MW price-taker value**: a cap of 10 MW (10% of 151 MW) holds for the whole pocket, and a 100 MW battery at MIDW would offset 66% of the line.
4. **The synthetic basis may be easier to forecast than a real one**, because it is a function of hub and zone spreads that the Forecaster can read. The Forecaster's win at 36 of 40 nodes, the learner's relative loss and the Spike hunter's relative gain are results *on these lanes*; they need a check against real nodal prices when more history exists.
5. **Forward values are a different kind of number from the history.** Zone prices stay at their 2025 levels; the structure is the 2025 replay with two scale effects, so a pocket that does not exist today is invisible; the learner value is oracle x a 2023-25 ratio (the 2025 replay is +14% above the 2025 dispatch at the nodes);
   2026 is January-September annualised and the cheap Q4 is missing. The step from the 2026 history row to the 2027 forward row is partly this method change.
6. **The saturation constants are assumptions** (`SAT_MIN_SF`, `SAT_SLOPE`, `SAT_FLOOR`, `SAT_REALISATION`, import side only; no public attrition history in the repo). `SAT_MIN_SF` is a cliff for Twin Buttes (section 5); relief assumes the batteries discharge into the pocket when it binds, so it is an upper bound for a given listing.
   A second effect is left out: export-side batteries charging during binding hours also relieve a line ("both sides" scale for Twin Buttes 0.15 instead of 0.31).
7. **The portfolio's cap is a rule of thumb.** 10% of the median limit, groups by *dominant* element only (KNAPP, SHRNE and BULLCRK also lean on Twin Buttes, which is not coupled to their group), whole units rounded down (151 MW -> 10 MW), connection lead times ignored (all placements in 2027), the zone fallback priced at its static 2025 value.
8. **Connection and break-even are indicative.** The step-up transformer ($16.95M per 100 MW) is agent 3a's unverified proxy and is 88% of the all-in connection cost; the net and break-even values leave out AS, capacity payments, O&M, degradation and tax, and use CRF(8%, 15 years), both ASSUMPTION.
9. **Two independent reviews.** The first complete build was reviewed and rebuilt. That review found defects in that first build ("v1" in the test docstrings): IMM binding hours counted once per contingency (an element bound twice as long as in reality), weak shift factors zeroed at the node while the zone's was not (an artefact worth 15% of candidate scores),
   noisy driver selection (a placebo clears the old 0.10 correlation bar in 85% of draws), loose cache keys, an EIA date parse that dropped planned projects, fractional group caps, candidates that ranked on the zone's average effect alone, and tests that skipped silently. All were fixed and everything in this note is from the rebuilt version.
   A second independent review of the rebuilt code recomputed lane prices, the 2025 oracle replay, annualisation, uplifts, half-lives, the portfolio inputs and the `sites.csv` formulas on its own and matched them; it found no defect that changes a reported level or ranking.
   It did find gaps that were then fixed (value-cache and lane-file keys, provenance between lanes, ratios and forward values, an upgrade clock that double-counted relief already in the October 2026 window, labels, silent fall-backs, weak tests), and the forward values, portfolio and `sites.csv` were recomputed afterwards (no learner value moved by more than $0.72/kW-yr and no half-life changed; the earlier forward outputs are in `derived/runs/pre_review2`).
   The v1 outputs are kept in `research-data/grid/derived/runs_v1` for comparison: the node-average learner value moved from $77.2 to $78.3, 28 of the 40 candidates are in both lists, and on those the levels correlate 0.96 and the ranks 0.80 (MILES fell from $149 to $97, NOVICE from $115 to $83, KNAPP rose from $73 to $98).
10. **Who gets the nodal price.** The values are the price at the substation's highest-kV bus. By `notes/rules.md` (a), a transmission- or distribution-connected ESR/DESR settles at its resource node, and a settlement-only site of 10 MW or less at the bus mapped to its load or meter;
    an ADER aggregation (home-battery fleets) settles at the load-zone price and gets none of the nodal congestion value. Which transmission bus a given distribution feeder maps to is not public (UNVERIFIED in rules.md). The 8 zone control rows are what an ADER earns.
11. **Learner/oracle ratios come from each lane's own backtest** (2023-25; 2024-25 for 4 h). The zone control's ratio is used where a lane has none (none needed here).
12. **Small inconsistencies found by the second review and left in.** None moves a reported number by more than rounding and none is the dominant element of a candidate; (a) and (c) would need a full rebuild of the lanes and the dispatch for a change of about 0.1%.
    (a) For an element with an IMM rent but no IMM hours the binding hours come from the rent allocated to each contingency, not from the element's rent, so the union of its binding intervals can be shorter than the element-rent figure:
    STPWAP39_1 binds 288 h instead of 438 h (1.0% of the replayed shadow-price-hours; at most 2.7% of any candidate's score, CENTRVIL). (b) 8 of the 63 RT regressions are attenuated (`sf_rt_shrink` 0.02-0.77) and are used at their raw SF (0.04% of the shadow-price-hours); only day-ahead attenuated fits are excluded.
    (c) CROSSO_PALOUS1_1 keeps its driver sign after the Panhandle rule moves its location to HB_PAN (0.02%). (d) An operating EIA battery (Crowned Heron, 150 MW) is also counted through its queue row (24INR0405, weight 0.6); removing the row moves four 2031 scales by at most 0.009.
    (e) For 4 of the 120 constraint slots in `sites.csv` the side (summed over the element's constraints) and the SF shown (the largest single contingency) differ in sign; never for constraint 1. (f) `dominant_upgrade_*` come from `constraints.csv`, the forward clock from agent 4's schedule.
    (g) Two forward runs at the same time overwrite each other's value-cache entries (recomputation only).

## 9. Every named constant and its label

All 105 named constants of the three modules (`nodal_lanes.py`, `saturation.py`, `sites.py`), as they are in the source at the top of each file, one value each, never inlined. The label says what kind of number it is:
REAL (a published fact, a rule or a count from the brief), DERIVED (computed from data or from other constants), ASSUMPTION (a judgement call: the thing to change in a sensitivity run). No constant here is SIM; the simulation outputs are in the tables above.
The notes of agent 3a (`notes/phase3a.md`) and agent 4 (`notes/phase4.md`) carry theirs.

| module | constant | value | label | meaning |
|---|---|---|---|---|
| nodal_lanes | `CACHE_VERSION` | `4` | DERIVED | bump when the logic that builds the structure, the shadow prices or the lanes changes (part of every cache key) |
| nodal_lanes | `REPLAY_YEAR` | `2025` | REAL | the year whose congestion structure is replayed (IMM 2025 rents and hours) |
| nodal_lanes | `LANE_START` | `"2019-01-01"` | REAL | brief, first day of the rebuilt node history (the models need two years before the 2021 start) |
| nodal_lanes | `LANE_END` | `"2026-09-26"` | REAL | last day of the arena's ERCOT price history (NP6-785-ER) |
| nodal_lanes | `NI` | `C.NI` | REAL | 96 fifteen-minute intervals a day (arena.config.NI) |
| nodal_lanes | `DT_H` | `C.DT` | REAL | 0.25 h per interval |
| nodal_lanes | `ZONES` | `list(C.ERCOT_ZONES)` | REAL | the arena's 8 load zones (also the control lanes) |
| nodal_lanes | `REF` | `C.ERCOT_REF` | REAL | HB_BUSAVG, the system reference every spread is taken against |
| nodal_lanes | `HUBS` | `("HB_WEST", "HB_NORTH", "HB_SOUTH", "HB_HOUSTON", "HB_PAN")` | REAL | ERCOT hubs with a price history in rtm20xx.csv |
| nodal_lanes | `DRIVER_LOCATIONS` | `HUBS + tuple(ZONES)` | DERIVED | 13 candidate locations; a driver is +/- (price - HB_BUSAVG) at one of them |
| nodal_lanes | `HB_PAN_FALLBACK` | `"HB_WEST"` | ASSUMPTION | HB_PAN has no price before 2019-04-05 (the hub was created then); its spread borrows HB_WEST |
| nodal_lanes | `PRICE_CAP` | `5000.0` | REAL | ERCOT system-wide offer cap since 2022 (arena.config.ERCOT_CAP); a rebuilt node price is not pushed above it |
| nodal_lanes | `PRICE_FLOOR` | `-250.0` | REAL | the brief's floor (the protocols' SPP floor is -$251, NP 6.6.1.1); a rebuilt node price is not pushed below it |
| nodal_lanes | `SHADOW_CAP_DEFAULT` | `{"gtc": 5251.0, 345: 4500.0, 138: 3500.0, 69: 2800.0}` | REAL | shadow-price caps $/MWh, IMM 2025 SOM p.62 (notes/rules.md (b)); used where SCED lists no cap |
| nodal_lanes | `MEAN_SHADOW_CAP_FRAC` | `0.5` | ASSUMPTION | the mean shadow price while binding may not exceed this share of the cap (else H_k is raised) |
| nodal_lanes | `SCED_RUN_MIN` | `5.0` | ASSUMPTION | one SCED run covers 5 minutes (phase 1a / phase 2 convention) |
| nodal_lanes | `SCED_RUN_CAP_S` | `360.0` | ASSUMPTION | a SCED run is never stretched over more than 6 minutes (gaps in the posted runs) |
| nodal_lanes | `DAM_WINDOW_DAYS` | `31.0` | REAL | DAM shadow prices 10 Sep - 10 Oct 2026 |
| nodal_lanes | `MIN_RT_RUNS_FOR_SPH` | `10` | REAL | phase 2 MIN_BINDING_RUNS; below this the RT window is too thin, the DAM window is used |
| nodal_lanes | `WINDOW_PERSISTENCE` | `0.15` | ASSUMPTION | share of the window's shadow-price-hours that recurs over a year (outage-driven shoulder week). Same value and meaning as gridsite.upgrades.WINDOW_PERSISTENCE (agent 4); measured 0.14 for Scurry Chevron-Knapp, the one usable element with both a window and an IMM rent. Applied to the target; split between hours and magnitude, see below. |
| nodal_lanes | `HOURS_SHARE_OF_PERSISTENCE` | `0.5` | ASSUMPTION | the persistence discount is split evenly (geometrically) between binding hours and the mean shadow price: H_window * persistence**0.5, mean * persistence**0.5. Scurry Chevron-Knapp: IMM hours 1099 vs 2530 annualised from the RT window (0.43), IMM mean shadow $268 vs $838 in the window (0.32), product 0.14, close to the 0.39 x 0.39 this split gives. |
| nodal_lanes | `IMM_FLOOR_RANK_RENT_MUSD` | `50.83` | REAL | 10th-largest 2025 IMM RT rent (Argyle-Lewisville); an element outside the IMM top 10 earns less than this, so it caps the annualised window rents of the elements without an IMM figure (same as gridsite.upgrades) |
| nodal_lanes | `H_MIN` | `24.0` | ASSUMPTION | a constraint's annual binding hours are at least this (a smaller window estimate is below its noise; the dollar target is kept, so the mean shadow price falls) |
| nodal_lanes | `H_MAX` | `4000.0` | ASSUMPTION | no constraint binds more than this many hours a year (the IMM's most frequent element binds 1,694 h) |
| nodal_lanes | `MIN_RT_INTERVALS_DRIVER` | `24` | ASSUMPTION | a driver is fitted on the RT window only if the constraint binds in at least this many 15-min intervals |
| nodal_lanes | `MIN_DAM_HOURS_DRIVER` | `6` | ASSUMPTION | ... else on the DAM window if it binds in at least this many hours |
| nodal_lanes | `MIN_DRIVER_CORR` | `0.20` | ASSUMPTION | a fitted driver needs at least this signed correlation with the observed shadow price, else the geographic rule. Raised from 0.10 after an independent review: a placebo (whole-day shifts of the DAM series, best of 26 candidates) clears 0.10 in 85% of draws, median 0.19 |
| nodal_lanes | `GEO_MIN_ZONE_SF` | `0.003` | ASSUMPTION | the geographic rule reads a zone's SF only if /SF_zone/ is at least this (below it the sign is noise); else the element's own zone, sign +1 |
| nodal_lanes | `GEO_DEFAULT_SIGN` | `1` | ASSUMPTION | a local pocket with no zone-level signature is taken to be an import pocket (price up when it binds) |
| nodal_lanes | `GEO_HUB` | `{"LZ_WEST": "HB_WEST", "LZ_NORTH": "HB_NORTH", "LZ_SOUTH": "HB_SOUT...` | ASSUMPTION | named geographic mapping of a zone to its hub |
| nodal_lanes | `PANHANDLE_BOX` | `(34.3, 36.6, -103.1, -99.9)` | ASSUMPTION | (lat_min, lat_max, lon_min, lon_max) of the ERCOT Panhandle; an element with an end inside maps to HB_PAN |
| nodal_lanes | `DRIVER_RULE` | `"physics"` | ASSUMPTION | both rules pool the contingency cids of an element (one driver, nested binding sets, so an element binds as long as its REAL hours). "physics" (default) = best signed correlation among the locations whose zone shift factor says the element raises (SF_zone < 0) or lowers (> 0) the price there, at least DRIVER_ZONE_REL_MIN of the largest /SF_zone/; "data" = best signed correlation over all 13 locations (brief, literal) |
| nodal_lanes | `DRIVER_ZONE_REL_MIN` | `0.5` | ASSUMPTION | physics rule: a location is admissible if its zone's /SF_zone/ is at least this share of the largest over the 8 zones |
| nodal_lanes | `ZONE_SF_MIN_R2` | `0.5` | ASSUMPTION | a direct zone regression is used if its in-sample R2 is at least this, else the bus mean within the zone |
| nodal_lanes | `BUS_MEAN_MIN_KV` | `60.0` | ASSUMPTION | buses at or above this voltage enter the within-zone bus mean (same as gridsite.upgrades.zone_mean_sf) |
| nodal_lanes | `SHRINK_MIN` | `0.8` | REAL | phase 2 SHRINK_MIN; DAM fits below this are attenuated ("+shrunk") and give sign only |
| nodal_lanes | `INCLUDE_SHRUNK_DEFAULT` | `False` | ASSUMPTION | attenuated DAM shift factors are not used in the base rebuild (brief: sign only) |
| nodal_lanes | `SF_BAD_QUALITY` | `("implausible",)` | ASSUMPTION | a substation SF with one of these tags is read as zero. "weak" (/SF/ < 2 se) is NOT zeroed: SF_zone is not zeroed either, so zeroing the node leaves DSF = -SF_zone (an artefact worth 15% of the candidates' scores in the first build); raw SFs also rebuild the observed basis better (lane_validation.csv) |
| nodal_lanes | `SF_ABS_CAP` | `1.0` | ASSUMPTION | /SF/ is limited to 1 (a node price cannot move by more than the shadow price; same as gridsite.upgrades.SF_NWA_CAP). Post-contingency sensitivities can exceed 1 in principle, but the day-ahead fits that give 1.2 here are collinear (VIF > 100) |
| nodal_lanes | `COMPLETE_INTERVAL_S` | `890.0` | ASSUMPTION | a 15-minute interval is "complete" if the SCED runs cover at least this many of its 900 seconds |
| nodal_lanes | `K_HOURS_PER_MUSD_FALLBACK` | `8.5` | DERIVED | median IMM RT binding hours per $M of 2025 rent (gridsite.upgrades.imm_hours_per_musd = 8.48); used only if the table has no such element |
| nodal_lanes | `DAYS_PER_YEAR` | `365.0` | REAL | annualisation of window figures (same as gridsite.upgrades) |
| nodal_lanes | `INTERVALS_PER_HOUR` | `1.0 / DT_H` | DERIVED | 4 fifteen-minute intervals per hour |
| nodal_lanes | `MIN_BIND_INTERVALS` | `4` | ASSUMPTION | a constraint-year with fewer binding intervals than this is not replayed |
| nodal_lanes | `FIT_ITER_MAX` | `12` | ASSUMPTION | water-filling iterations when clipping synthetic shadow prices at their cap and rescaling to the target |
| nodal_lanes | `VALID_FIT_START_CPT` | `"2026-10-05"` | REAL | the RT shift-factor fit starts with the first day of bus LMPs (phase 1a) |
| nodal_lanes | `VALID_FIT_END_CPT` | `"2026-10-09 12:00"` | REAL | end of the in-fit slice of the validation (phase 2 held out the last 24 h) |
| nodal_lanes | `HOLDOUT_RT_H` | `24` | REAL | phase 2 RT holdout, hours |
| nodal_lanes | `HOLDOUT_DAM_DAYS` | `5` | REAL | phase 2 DAM holdout, days |
| nodal_lanes | `CPT` | `"America/Chicago"` | REAL | Central Prevailing Time |
| saturation | `AS_OF` | `pd.Timestamp("2026-10-10")` | REAL | date of this build; a project dated earlier but not operating is treated as arriving now |
| saturation | `UPGRADE_RESIDUAL` | `0.10` | ASSUMPTION | share of a line / transformer constraint's shadow prices left after its planned upgrade (= gridsite.upgrades.RESIDUAL_MU_SCALE_WIRES) |
| saturation | `UPGRADE_RESIDUAL_GTC` | `0.50` | ASSUMPTION | an interface keeps half of its congestion after its planned relief (= gridsite.upgrades.RESIDUAL_MU_SCALE_GTC; the arena halves the Permian premium after the 765 kV lines) |
| saturation | `UPGRADE_CONF_MIN` | `0.5` | ASSUMPTION | a planned-upgrade match at or above this confidence counts (phase 2 hops.csv and phase 4) |
| saturation | `YEAR_ONLY_ISD_MONTH_DAY` | `"06-30"` | ASSUMPTION | an in-service date given only as a year is read as 30 June of that year |
| saturation | `SAT_MIN_SF` | `0.05` | ASSUMPTION | a project relieves a constraint only if /SF/ >= this on the import side (below it the relief is noise) |
| saturation | `SAT_SLOPE` | `1.0` | ASSUMPTION | shadow prices fall one-for-one with the share of the limit that storage offsets (relief = limit -> floor) |
| saturation | `SAT_FLOOR` | `0.10` | ASSUMPTION | shadow prices never fall below this share of the 2025 structure (the same floor as the upgrade residual) |
| saturation | `SAT_REALISATION` | `0.6` | ASSUMPTION | share of not-yet-built queue / planned MW that is actually built (no public attrition history in this repo) |
| saturation | `SAT_SIDES` | `("import",)` | ASSUMPTION | brief: only import-side storage counts (discharging into the pocket); export-side charging would add relief |
| saturation | `SAT_MIN_CONF` | `0.5` | REAL | queue_sub_map.csv, "use conf >= 0.5" |
| saturation | `SAT_SPLIT_TAP_EQUALLY` | `True` | ASSUMPTION | a tapped line's MW is split equally between the stations listed in queue_sub_map.csv sub / sub_alt (those that are NP4-160 substations) |
| saturation | `DAYS_PER_YEAR` | `365.0` | REAL | day count of the in-year ramp of a project or an upgrade (same as gridsite.upgrades) |
| saturation | `FIRM_EIA_STATUS` | `("(T)", "(U)", "(V)", "(TS)")` | ASSUMPTION | EIA-860M statuses that are firm: approvals received, under construction, built but not in service |
| saturation | `FORWARD_YEARS` | `(2026, 2027, 2028, 2029, 2030, 2031)` | DERIVED | years with a scale vector (2026 is the current year; the forward table reports 2027-2031) |
| saturation | `BASE_YEAR` | `2025` | REAL | the replay year (the structure is the 2025 one; scale 1) |
| sites | `N_CANDIDATES` | `40` | ASSUMPTION | brief ("about 40"); the dispatch cost scales with the number of lanes (about 1 min per lane) |
| sites | `MAX_PER_CONSTRAINT` | `5` | ASSUMPTION | brief ("about 5"): at most this many candidates share a dominant element |
| sites | `MIN_PER_SIDE` | `10` | ASSUMPTION | at least this many candidates on each side (import / export) of their dominant element |
| sites | `SF_MIN_ELIGIBLE` | `0.10` | ASSUMPTION | a usable /SF/ to at least one top-priority constraint at least this large (about 5x the 90th percentile /SF/ of RT constraints, phase 2) |
| sites | `ELIGIBLE_TOP_PRIORITY` | `40` | ASSUMPTION | "top ~40 priority constraints": the 40 best by constraints.csv priority among those with a usable SF magnitude |
| sites | `MIN_MATCH_CONF` | `0.5` | REAL | notes/phase1-geo.md, "use match_conf >= 0.5 for anything that ranks sites" |
| sites | `MIN_KV` | `69.0` | REAL | PROMPT.md Tier 1 scope, 69-345 kV substations |
| sites | `TOP4H` | `15` | ASSUMPTION | brief: the 4-hour spec runs the top 15 candidates by 2-hour learner value |
| sites | `TOP_ELEMENTS_REPORTED` | `3` | REAL | brief: top-3 constraints per site in sites.csv |
| sites | `SF_REPORT_MIN` | `0.05` | ASSUMPTION | an element is named for a site only if the site's own /SF/ to it is at least this (phase 2 TOP_BIG_SF: a "large" SF); the rest of its score term is the zone's average effect |
| sites | `SIM_START` | `"2021-01-01"` | REAL | brief, first simulated day of the 2 h run |
| sites | `SIM_START_4H` | `"2024-01-01"` | REAL | brief, first simulated day of the 4 h run |
| sites | `BACKTEST_YEARS` | `(2021, 2022, 2023, 2024, 2025)` | REAL | the complete years of the backtest (2026 ends 26 Sep) |
| sites | `RATIO_YEARS` | `(2023, 2024, 2025)` | REAL | brief, the learner / oracle ratio comes from the 2023-25 backtest |
| sites | `DISPATCH_WORKERS` | `4` | REAL | brief, at most 4 workers (the machine is shared) |
| sites | `DISPATCH_CHUNK` | `12` | ASSUMPTION | lanes per task; the cost of a task is nearly flat in the number of lanes (notes/phase3a.md section 5), so 48 lanes make 4 tasks |
| sites | `HOURS_PER_SPEC` | `{SPEC_2H: 2.0, SPEC_4H: 4.0}` | REAL | usable MWh per MW of the two merchant specs |
| sites | `FULL_YEAR_MIN_DAYS` | `360` | REAL | the arena's yearly() rule: a year with fewer simulated days is scaled to DAYS_PER_YEAR |
| sites | `DAYS_PER_YEAR` | `365.0` | REAL | annualisation |
| sites | `EXPERT_NAMES` | `["Clockwork", "Forecaster", "Weather-regime", "Spike hunter q97", "...` | REAL | arena.algorithms.Adaptive |
| sites | `BASE_YEAR` | `2025` | REAL | the replay year (the structure is the 2025 one) |
| sites | `FORWARD_REPORT_YEARS` | `(2027, 2029, 2031)` | REAL | brief: forward 2027 / 2029 / 2031 in sites.csv |
| sites | `RATIO_PERIOD` | `{SPEC_2H: "mean_2023_25", SPEC_4H: "mean_2024_25"}` | REAL | brief: the ratio comes from the 2023-25 backtest; the 4 h run starts in 2024, so its ratio is 2024-25 |
| sites | `HALF_LIFE_FRACTION` | `0.5` | REAL | brief: uplift below 50% of its 2025 value |
| sites | `PORTFOLIO_YEARS` | `(2027, 2028, 2029, 2030, 2031)` | REAL | brief: 2027-2031 |
| sites | `HISTORY_YEARS` | `(2021, 2022, 2023, 2024, 2025, 2026)` | REAL | the backtest years the rules see ("known"); 2026 is annualised from 1 Jan to 26 Sep |
| sites | `UNIT_MW` | `10.0` | REAL | brief: units are 10 MW (siting.allocate places one unit at a time) |
| sites | `UNITS_PER_YEAR` | `20` | ASSUMPTION | brief example: 20 units = 200 MW a year |
| sites | `PRICE_TAKER_SHARE` | `0.10` | ASSUMPTION | brief: nodes behind one constraint share a cap of this share of the constraint's median limit |
| sites | `MIN_GROUP_UNITS` | `1` | ASSUMPTION | a group cap is never below one unit (10 MW), so no element is closed to a first unit by rounding |
| sites | `POI_CAP_MW` | `{69: 50.0, 138: 150.0, 345: 300.0}` | ASSUMPTION | brief: point-of-interconnection capacity of one site by voltage class |
| sites | `POI_CLASS_KV_EDGES` | `(115.0, 230.0)` | ASSUMPTION | a site at or below 115 kV is the 69 kV class, at or below 230 kV the 138 kV class, above that the 345 kV class |
| sites | `REF_BATTERY_MW` | `K.REF_MW` | REAL | gridsite.costs.REF_MW, the reference battery of data/connection_costs.csv (100 MW) |
| sites | `PORTFOLIO_SEED` | `20261010` | ASSUMPTION | seed of the generator passed to siting.portfolio (only the Thompson variant draws numbers) |
| sites | `PORTFOLIO_RULES` | `ALGOS` | REAL | brief: rules A1-A5 |
| sites | `WACC` | `0.08` | ASSUMPTION | 8%, the rate the arena uses for break-even installed cost (arena.config.DISCOUNT) |
| sites | `LIFE_YEARS` | `15` | ASSUMPTION | 15-year battery life (NREL TP-6A40-93281 sec. 3, as gridsite.upgrades.BATT_LIFE_YR); the arena's home contract is 12 years |
| sites | `AS_YEARS` | `(2025, 2026)` | REAL | brief: report the AS estimate for 2025 and 2026 (to date, annualised) |
| sites | `SITES_FWD_YEARS` | `(2027, 2029, 2031)` | REAL | brief |

## 10. Files, commands, runtimes, hand-offs

**Commands** (`gridsite/cli_3b.py`, registered in `run_grid.py` with no name collisions; `COMMANDS = {candidates, lanes, dispatch, forward, portfolio, sites}`). Everything is resumable: dispatch chunks are cached in `derived/runs/cache_<spec>/`, forward values in `value_cache.json`, the lane file carries a key (constants, structure with each constraint's shift-factor method, prices, the shift-factor and zone-SF files, substations and their zones) and is rebuilt when it changes; the value cache is keyed on the battery definition, the constants, the structure and the prices.

| command | what it writes | wall time (this run) |
|---|---|---|
| `candidates [refresh]` | `data/site_candidates.csv` | 25 s |
| `lanes [refresh] [skip-validation] [shrunk]` | `data/constraint_synth.csv`, `derived/nodal_lanes_cand40.npz`, `data/lane_validation.csv` | 45 s + 50 s validation |
| `dispatch spec=grid\|grid4h\|all workers=4 [skip-oracle] [skip-dispatch] [skip-table]` | `derived/runs/<spec>_ledgers.npz`, `<spec>_oracle.npz`, `data/site_years.csv` | 2 h dispatch 1,382 s (4 workers, 48 lanes x 2,095 days), 2 h oracle 362 s (2 workers); 4 h dispatch 985 s (2 workers, 23 lanes x 1,000 days), 4 h oracle 360 s (2 workers) |
| `forward spec=grid\|grid4h\|all workers=4` | `derived/runs/forward_values.parquet`, `forward_halflife.parquet`, `forward_scales.parquet`, `forward_meta.json`, `value_cache.json` | 2 h 358 s, 4 h 239 s (4 workers; 368 and 68 lane-years of oracle on the rebuilt 2025 prices) |
| `portfolio` | `data/portfolio.csv`, `derived/runs/portfolio_summary.json`, `portfolio_groups.csv` | 6 s |
| `sites` | `data/sites.csv` and the printed report tables | 11 s |

Whole chain after the lanes: 28 minutes on a laptop shared with other agents (at most 4 workers, 2 when memory was tight; free RAM was 1.0-1.7 GB throughout). After the second review's fixes: lane file rebuilt under the new key in 8 s (prices bit-identical, so no new dispatch), `forward` re-run 10 minutes, `portfolio` 5 s, `sites` 8 s. Tests: `python tests/test_3b.py [name-substring]` (36 tests, 60 s, no skips).

**Reading the data.** `data/sites.csv` is the table to use (one row per candidate); `site_years.csv` has every year, period and algorithm, and the 8 zone controls (role `control_zone`); `portfolio.csv` has columns `rule, year, sub, MW` plus the value, the dominant element, side, kV and POI cap.
Substation tables must be read with `keep_default_na=False, na_values=[""]` (the substation `NA` is a real name).

**Guards.** `forward` writes the lane key and a fingerprint of the learner/oracle ratios into `derived/runs/forward_meta.json`; `portfolio` and `sites` call `sites.check_provenance()` and refuse forward values that were computed from other lanes or other ratios than `site_years.csv` holds now (run `forward` again).

**Hand-offs.**

- *Reranking after an upgrade (phase 6 / UI):* `gridsite.sites.value_sites(lane_ids, mu_scale=None, spec="grid", years=(2025,), method="oracle_ratio", baseline="both", workers=1)` returns one row per lane and year: oracle, learner and A1-A5 $/kW-yr, the zone control's learner value, the uplift, the lane's ratio and where it comes from, and the exposure-weighted scale.
  `mu_scale` is `{constraint id or element name: multiplier}` (what `gridsite.upgrades.apply_upgrade(...)` returns), multiplied into the year's baseline (`baseline` = "both" / "upgrades" / "saturation" / "none"). A lane can be any NP4-160 substation with shift factors and an arena zone, or a zone name. `method="oracle"` returns the ceiling itself.
  Example and numbers in section 5. `half_life_table(frame)` gives the half-life for a `value_sites` frame that holds 2025 and the later years.
- *Constants shared with agent 4* (kept equal, a test compares them): `UPGRADE_RESIDUAL` 0.10 and `UPGRADE_RESIDUAL_GTC` 0.50 = `RESIDUAL_MU_SCALE_WIRES` / `_GTC`; `WINDOW_PERSISTENCE` 0.15; the 8.48 h-per-$M ratio; the $50.83M IMM floor; the shadow-price targets equal agent 4's `sph_table`.
  The forward clock uses `upgrades.planned_schedule()` (66 constraints); if that function disappears, `saturation.planned_upgrades` falls back to the `upgrade_*` columns of `constraints.csv` with a warning.
- *Arena:* the only arena hook used is agent 3a's `groups` argument of `siting.portfolio`; no arena file was changed. Lane ids are added to `arena.config.LABEL` at run time only.

## 11. Notes for the next reader

- To change an assumption, edit the single named constant (section 9 lists them all), run `lanes refresh`, then `dispatch` (30-50 minutes for both durations, depending on how many jobs run side by side), `forward`, `portfolio`, `sites`. Lane files are keyed on the constants, so a stale lane set cannot be dispatched silently.
- The analysis behind sections 1, 4.2, 4.4 and 5 is not in the CLI. The scripts are in `research-data/grid/derived/runs/analysis_scripts/` (run from `docs/research/grid` with `PYTHONPATH` set to it; each prints its table and writes a CSV next to the other run outputs):
  `sens.py [workers]` (seven SIM variants, uses `nodal_lanes.sensitivity_lanes`, which rebuilds without touching any cache), `pool.py [workers]` (stratified pool sample), `satsens.py` and `satsens_minsf.py` (saturation constants, overridden in memory), `algos_pockets.py` (algorithms at nodes vs zones), `elements.py` (the pockets table), `v1v2.py` (first build against the rebuild; needs `runs_v1`).
- If a longer real nodal price history becomes available, the first thing to test is caveat 4: do real pocket prices let the Forecaster beat the learner as the synthetic ones do?

## Appendix A. All 40 candidates

Sorted by screening rank. `A5` = 2 h learner, $/kW-yr (21-25 = mean of 2021-25; 2026* = January-September annualised); `uplift` = A5 minus the zone control's A5 over 2021-25; `2031` = forward learner value; `conn` = connection class and lead time in months;
`net of conn.` = mean 2027-31 learner value minus the annualised connection cost. Constraint = dominant element, side, SF to it, hops (n/a where `hops.csv` has no row).

| rank | sub | zone | kV | dominant constraint (side, SF, hops) | A5 21-25 | A5 2025 | A5 2026* | oracle 21-25 | uplift | 2031 | half-life | conn | net of conn. |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | MIDW | WEST | 69 | HARGRO_TWINBU1_1 (import, -1.00, 8) | 269.7 | 237.6 | 157.5 | 538.9 | 220.0 | 87.3 | 2028 | bay 38 | 97.3 |
| 2 | STRS | WEST | 69 | HARGRO_TWINBU1_1 (import, -0.92, 7) | 249.5 | 214.7 | 144.6 | 503.3 | 199.7 | 81.0 | 2028 | bay 38 | 88.0 |
| 3 | MILES | WEST | 69 | HARGRO_TWINBU1_1 (export, +0.50, 6) | 96.9 | 101.4 | 61.9 | 193.3 | 47.2 | 32.9 | 2028 | bay 38 | 24.5 |
| 4 | POWELLFI | WEST | 69 | HARGRO_TWINBU1_1 (import, -0.72, 8) | 201.5 | 165.7 | 105.9 | 416.5 | 151.8 | 67.4 | 2028 | bay 38 | 67.4 |
| 5 | NOVICE | WEST | 69 | HARGRO_TWINBU1_1 (export, +0.36, 5) | 83.0 | 83.7 | 47.3 | 175.9 | 33.2 | 30.6 | 2028 | bay 38 | 18.9 |
| 6 | KNAPP | WEST | 138 | 6437__F (import, -0.46, 0) | 98.2 | 53.9 | 40.5 | 224.7 | 48.5 | 59.1 | > 2031 | bay 56 | 39.4 |
| 7 | COLLEGE | NORTH | 138 | COL_BRAN_1 (import, -0.54, n/a) | 49.2 | 32.9 | 21.4 | 120.0 | 7.7 | 29.7 | > 2031 | bay 56 | 8.3 |
| 8 | SCRCV | WEST | 138 | 6437__F (export, +0.26, 0) | 69.3 | 65.7 | 42.4 | 151.4 | 19.6 | 47.2 | > 2031 | bay 56 | 28.2 |
| 9 | SHRNE | WEST | 138 | 6437__F (import, -0.33, 2) | 89.7 | 47.3 | 34.1 | 208.7 | 40.0 | 50.9 | > 2031 | bay 56 | 31.4 |
| 10 | EOLA | WEST | 69 | FORTMA_YELWJC1_1 (import, -0.50, 2) | 67.7 | 39.3 | 31.0 | 161.9 | 18.0 | 32.8 | 2028 | bay 38 | 12.7 |
| 11 | SANDHI | LCRA | 138 | 215T215_1 (import, -0.71, n/a) | 61.3 | 36.7 | 25.3 | 144.8 | 14.0 | 47.8 | > 2031 | bay 56 | 25.2 |
| 12 | BRENNO | LCRA | 138 | 215T215_1 (import, -0.73, n/a) | 62.6 | 38.7 | 22.3 | 148.5 | 15.4 | 48.8 | > 2031 | bay 56 | 26.1 |
| 13 | GAYHIL | LCRA | 138 | 215T215_1 (import, -0.71, n/a) | 60.6 | 37.8 | 25.6 | 144.8 | 13.4 | 48.5 | > 2031 | bay 56 | 25.9 |
| 14 | APOLLO | NORTH | 138 | COLLE_JUPIT_1 (import, -0.37, 13) | 66.7 | 49.8 | 36.5 | 149.5 | 25.2 | 49.6 | > 2031 | bay 56 | 27.7 |
| 15 | TANK | WEST | 69 | LAKENA_SAMATH1_1 (import, -0.61, n/a) | 64.8 | 22.2 | 22.7 | 154.8 | 15.0 | 45.7 | > 2031 | bay 38 | 21.9 |
| 16 | LYONS | LCRA | 138 | 215T215_1 (import, -0.71, n/a) | 60.0 | 37.6 | 31.2 | 144.8 | 12.7 | 47.8 | > 2031 | bay 56 | 25.1 |
| 17 | CHAPHI | LCRA | 138 | 215T215_1 (import, -0.63, n/a) | 59.7 | 35.9 | 20.5 | 142.4 | 12.5 | 44.5 | > 2031 | bay 56 | 21.8 |
| 18 | BG | HOUSTON | 138 | DT_PK_91_A (import, -1.00, 3) | 74.0 | 62.6 | 73.3 | 151.7 | 26.8 | 59.4 | > 2031 | bay 56 | 36.8 |
| 19 | SEGOVI | LCRA | 69 | FORTMA_YELWJC1_1 (import, -0.50, 3) | 79.0 | 42.6 | 32.4 | 177.6 | 31.8 | 38.9 | 2028 | bay 38 | 18.7 |
| 20 | BULLCRK | WEST | 138 | 6437__F (import, -0.26, 4) | 74.7 | 38.6 | 28.9 | 183.0 | 25.0 | 44.3 | > 2031 | switchyard 56 | 12.9 |
| 21 | PK | HOUSTON | 138 | DT_PK_91_A (import, -1.00, 0) | 72.3 | 63.4 | 72.0 | 147.8 | 25.1 | 58.2 | > 2031 | bay 56 | 35.5 |
| 22 | MID | HOUSTON | 138 | DT_PK_91_A (import, -1.00, 1) | 72.1 | 61.0 | 72.8 | 148.8 | 25.0 | 57.4 | > 2031 | bay 56 | 34.7 |
| 23 | GT | HOUSTON | 138 | DT_PK_91_A (import, -1.00, 2) | 72.8 | 60.8 | 73.7 | 150.0 | 25.7 | 57.5 | > 2031 | bay 56 | 34.8 |
| 24 | SHELLLON | SOUTH | 69 | FORTMA_YELWJC1_1 (import, -0.50, 3) | 69.0 | 37.5 | 28.5 | 159.8 | 31.6 | 31.0 | 2029 | bay 38 | 10.4 |
| 25 | JUNCTION | SOUTH | 69 | FORTMA_YELWJC1_1 (import, -0.50, 2) | 68.3 | 35.9 | 28.9 | 158.6 | 30.9 | 30.8 | 2029 | bay 38 | 10.1 |
| 26 | TMPTV | NORTH | 69 | SEA_AAT1 (import, -0.36, 4) | 52.1 | 24.5 | 16.6 | 128.8 | 10.6 | 27.4 | 2029 | bay 38 | 6.8 |
| 27 | MASONSW | SOUTH | 69 | FORTMA_YELWJC1_1 (import, -0.51, 2) | 67.9 | 37.0 | 29.1 | 158.4 | 30.6 | 30.7 | 2029 | bay 38 | 10.0 |
| 28 | MILLERRD | NORTH | 138 | COLLE_JUPIT_1 (export, +0.18, 13) | 45.6 | 23.3 | 21.5 | 122.8 | 4.1 | 27.7 | > 2031 | bay 56 | 5.3 |
| 29 | HILND | NORTH | 138 | 587__A (import, -0.55, 2) | 78.6 | 53.2 | 39.3 | 173.7 | 37.1 | 54.1 | > 2031 | bay 56 | 35.1 |
| 30 | MPPOI | WEST | 138 | 6437__F (import, -0.25, 4) | 72.0 | 35.9 | 28.0 | 176.6 | 22.2 | 42.7 | > 2031 | bay 56 | 21.8 |
| 31 | CRNSO | NORTH | 138 | 587__A (import, -0.55, 2) | 78.6 | 53.6 | 36.3 | 173.3 | 37.1 | 54.2 | > 2031 | bay 56 | 35.2 |
| 32 | PGE | NORTH | 69 | SEA_AAT1 (import, -0.42, 5) | 49.9 | 27.1 | 23.8 | 125.3 | 8.4 | 25.7 | 2029 | bay 38 | 5.2 |
| 33 | SEA | NORTH | 69 | SEA_AAT1 (import, -0.72, 0) | 52.9 | 28.3 | 23.6 | 130.8 | 11.4 | 30.6 | > 2031 | bay 38 | 10.0 |
| 34 | WYNJOYCE | NORTH | 138 | COLLE_JUPIT_1 (export, +0.16, 13) | 45.2 | 22.6 | 20.1 | 120.2 | 3.7 | 27.5 | > 2031 | bay 56 | 5.0 |
| 35 | CRNTH | NORTH | 138 | 587__A (import, -0.47, 2) | 72.5 | 48.3 | 33.9 | 162.2 | 31.0 | 48.8 | > 2031 | bay 56 | 29.3 |
| 36 | LYONS_C | NORTH | 138 | COLLE_JUPIT_1 (export, +0.15, 15) | 44.9 | 21.9 | 19.9 | 119.8 | 3.4 | 26.9 | > 2031 | bay 56 | 4.5 |
| 37 | CENTRVIL | NORTH | 138 | COLLE_JUPIT_1 (export, +0.16, 14) | 44.3 | 21.9 | 19.3 | 118.0 | 2.7 | 27.0 | > 2031 | bay 56 | 4.4 |
| 38 | ECRSW | WEST | 138 | 6429__D (export, +0.28, 16) | 48.3 | 30.5 | 22.5 | 116.7 | -1.5 | 29.8 | 2029 | bay 56 | 8.2 |
| 39 | LWSVW | NORTH | 138 | 587__A (export, +0.16, 19) | 40.7 | 26.8 | 19.3 | 109.6 | -0.8 | 20.9 | 2028 | bay 56 | 0.2 |
| 40 | ROUGH | WEST | 138 | 6429__D (export, +0.19, 30) | 46.9 | 26.8 | 19.0 | 111.1 | -2.9 | 26.8 | 2028 | bay 56 | 5.0 |
