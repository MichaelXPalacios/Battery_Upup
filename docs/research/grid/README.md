# Where to put batteries on the Texas grid

*Batter Up research, ERCOT physical-grid siting study. Built 10–11 Oct 2026 from public data only; this is revision 2 (11 Oct 2026, "What changed in this revision" below). Brief: [PROMPT.md](PROMPT.md); contracts: [PLAN.md](PLAN.md); map: [map/index.html](map/index.html). Phases 1–5 (market data, planning documents, geography, network and shift factors, site value, upgrades, reliability and Tier 2), the geography audit and regional coverage of Phase 7 and the integration of revision 2 (Phase 8, [notes/phase8-revision2.md](notes/phase8-revision2.md)) are written up in [notes/](notes/); this page is the report of Phase 6, rebuilt.*

**How to read the numbers.** Every number carries one of four labels. **REAL** is copied from a named public source. **DERIVED** is computed from real numbers. **SIM** is a model output. **ASSUMPTION** is our choice, a named constant in the code. In tables the label sits in the column header; in text it follows the number in brackets. Dollars are **$/kW-yr** of a 1 MW battery, energy only, after a $20/MWh wear cost, dispatched by the arena's Adaptive learner (A5) unless stated. "2 h" is a 1 MW / 2 MWh grid battery, "4 h" a 1 MW / 4 MWh one. Market value and reliability value are kept in separate columns and never added.

**Node prices are a model.** ERCOT publishes one week of real-time nodal prices and 31 days of day-ahead ones without an API key, so the price at a substation over 2019–2026 is rebuilt from the real zone price plus a replayed congestion structure. Everything about a node's price is SIM: levels are good to about a factor of two, rankings are steadier (section 9).

## TL;DR

1. **Siting is worth about twice the zone price, and it sits in a few small pockets.** A 2 h battery at one of the 40 congested substations earns $79.0/kW-yr against $44.4 at the eight load-zone prices (2021–25 mean) and $55.3 against $17.8 in 2025; 38 of the 40 beat their zone [SIM]. The top five are MIDW $270.4, STRS $250.2, POWELLFI $200.7, KNAPP $98.1 and MILES $97.0 [SIM]. The first three sit 7–8 hops behind Twin Buttes–Hargrove 138 kV in West Texas, and the 40 sites sit behind 12 constraints. The top 15 is the same whether the model is anchored on 2025 or on 2026 market-monitor rents; the anchor year moves the three Lubbock sites (by $9–21) and, if day-ahead rents are counted too, two sites of the SEA pocket (by about $40) (section "What changed").
2. **The Panhandle, the Valley and the Far West do not offer a second pocket in the summer prices.** ERCOT's 60-day disclosure gives measured day-ahead prices for 1 Jul–11 Aug 2026 [REAL]. The Valley's congestion is an **export discount, not an import premium** (17.2% of node-hours more than $10 below the system, 1.1% above); the Panhandle has the same shape; and a battery at a Valley or Panhandle node shows **no uplift over its zone** (−$0.39 and −$0.31 per kW over the 42 days) [DERIVED]. Vealmoor–Koch Tap, Longshore–Consavvy and Odessa–Yarbrough did not bind in summer 2026. Of 41 regional picks valued like the 40, the best has a modelled gain of $12.4/kW-yr against an average of $33.0 for the 40, and most positive ones rest on a line hundreds of kilometres away; the places to watch are the Hill Country side of Laredo/South and Williamson County (section 7).
3. **It is a first-10-MW result.** Nodes behind one line share its congestion, so each constraint's group is capped at 10% of its limit [ASSUMPTION]. That leaves room for about 220 MW of the first 1,000 MW at the nodes. With the caps, siting adds $4–9/kW-yr to a 1,000 MW fleet's average (the learner rule earns $35.4 against $27.8 at the zones only), not the $20–75 an uncapped "best site" ranking promises [SIM].
4. **Electrical distance decides, not hop count.** A substation's shift factor (how many dollars its price moves per dollar of congestion on a line) is only loosely related to how many lines away it is: over 104,969 substation–line pairs the rank correlation is −0.42, and 46% of the pairs with a shift factor of 0.10 or more are 8 or more hops away [DERIVED]. MIDW is 8 hops from its line and has the largest shift factor in the study (−1.00). SCRCV, at the end of Scurry Chevron–Knapp (0 hops), is on the export side (+0.26) and earns $70.9 against $98.1 at KNAPP, the other end [SIM].
5. **Wires beat batteries on cost, and only the transmission owner can make them happen.** A battery that relieves 10% of a line's limit in 80% of its binding hours costs a median 21 times the wires fix, and 192 times the cheapest rating change [DERIVED]. The transmission owner (the TSP) files an upgrade through ERCOT's Regional Planning Group, plus a state permit (a CCN) for a new line; a battery owner can propose one and pay its own interconnection, not order one. Revalued with the full dispatch model, reconductoring Twin Buttes–Hargrove takes MIDW from $247.9 to $50.6 and the 40-site total down 31%, and seven of the ten top upgrades now move at least one site (revision 1: five) [SIM]. Phase 4's first-order estimate ranks the 40 sites the same way (rank correlation 0.83) but understates MIDW's loss by 31% and misses second exposures. Every planned upgrade in service by 2031 moves no candidate by more than $3.4; the one dated project that hits candidates is Argyle–Lewisville in Dec 2032 (−$39 at HILND and CRNSO).
6. **The premium fades as storage arrives.** With 26 GW of effective storage online by 2031 (41 GW listed; 60% of unbuilt MW realised [ASSUMPTION]) the mean gain over the zone falls from $38.5 (2025 replay) to $33.4 (2027), $25.0 (2028) and $20.0 (2029–31) [SIM]. The five Twin Buttes sites drop below half of their 2025 gain by 2028, but that rests on 15 substations with a shift factor of about −0.065 and is the most fragile result here.
7. **At a node the Forecaster beats the learner; reliability, distribution batteries and the dispatch platform.** A2 earns more than A5 at 34 of the 40 nodes ($81.9 against $79.0), and the Spike hunter gains most from siting (×2.05; $225 to $449 per MWh) yet still earns $18 less than the learner [SIM]. Ancillary prices have collapsed since RTC+B went live on 5 Dec 2025 (day-ahead Reg Up $26.79/MW-h in 2023, $2.11 in 2026 to 10 Oct [REAL]), so the ancillary line is $15.9/kW-yr in 2025 and $11.9 in 2026 for a 2 h battery, the same at any site [SIM]. Only four of the 40 sites, the Houston PK–DT substations (MID #10, BG #11, GT #62, PK #65), are also in the top 100 by reliability need [ASSUMPTION weights]; the Twin Buttes pocket is a market case only. A distribution battery that registers as a DESR is paid the node price (+$26.4/kW-yr on average at 167 valued Tier 2 candidates, +$35.6 for the 84 within 150 km of the line they depend on, falling to +$11.8 by 2029); a home-battery fleet (ADER) is paid the zone price and gets none of it [SIM]. `data/sites.csv` is the ordered queue of settlement points for the dispatch platform in [`dispatch/`](../../../dispatch/README.md). An ERCOT API key would replace the rebuilt prices at the top sites with real ones.

## What changed in this revision

Revision 1 valued 40 substations on a congestion structure anchored mostly on 2025 rents. It found one real pocket, Twin Buttes–Hargrove in West Texas, and nothing in the Panhandle, the Valley or the Far West. Revision 2 carries two audits through the whole pipeline (candidates, price lanes, dispatch, forward years, portfolio, upgrades, reliability, Tier 2, this page and the map) and re-anchors the congestion on the market monitor's 2026 rents. **The top five did not change. The list below them did, because of three Lubbock sites and the anchor choice, and the regions the brief asked about offer no second pocket.**

1. **Geography audit.** COLLEGE is a Garland Power & Light substation in north Garland, Dallas County, not College Station. The audit overrode 38 placements (72 overrides in all, `data/geo_overrides.csv`) and 19 of the 20 constraint rows that had lost both line ends are back on the line graph; ATBR and JUPITER now pass the 0.5 placement gate and three Lubbock Power & Light stations are in Lubbock. Risk flags were refreshed for the 38 moved stations with new FEMA, USFS and NOAA point queries: COLLEGE gains a seismic flag, 148 of 2,884 substations are excluded (was 147 of 2,869), and reliability, Tier 2 and the connection-cost classes were rebuilt.
2. **Regional coverage.** ERCOT's 60-day day-ahead disclosure gives measured prices for summer 2026 at 1,003 settlement points. Section 7 reports what they show. Three results matter for the rest of this page: the Valley's congestion is an export discount, not an import premium; six regional patterns could be tied to named constraints and are now replayed (WESTEX, Nelson Sharpe–Rio Hondo, the South Texas export, the Far West import, the Valley export and Bell County–Salado; a seventh, the Panhandle, is grade D and only in sensitivity S3); and Vealmoor–Koch Tap, Longshore–Consavvy and Odessa–Yarbrough did not bind.
3. **Anchors.** A replayed element's shadow-price scale is anchored on a market-monitor rent. Revision 1 used the 2025 rent where there was one and a persistence-discounted window otherwise; R1 found that the 2026 lists put some elements far above their replayed value, and named the 0.15 persistence factor as the largest single reason revision 1 found nothing outside Twin Buttes. The primary run now anchors an element on its **2026 real-time rent** (January–August, ×12/8) where it is in the 2026 top-10 lists, else on its 2025 rent, else on the window. Six elements qualify and five reach a lane (the sixth, Mgses–Catsw, is replaced by R1's Far West pattern, which duplicates it):

| Element | Market | 2026 rent so far, $M [REAL] | Annualised, $M [DERIVED] | Replayed annual rent before → after, $M [SIM] | Ratio | Binding hours a year before → after [DERIVED] | Used in the primary run |
|---|---|---|---|---|---|---|---|
| Samsw–Vensw 345 kV | RT | 35.4 (Jan–Aug) | 53.1 | 1.7 → 53.1 | 31.0× | 123 → 450 | yes |
| Argyle–Lewisville 138 kV | RT | 37.3 (Jan–Aug) | 56.0 | 51.2 → 56.0 | 1.1× | 431 → 661 | yes |
| Mgses–Catsw 345 kV | RT | 57.6 (Jan–Aug) | 86.4 | 4.6 → 86.4 | 18.6× | 97 → 617 | no: replaced by R1's Far West pattern |
| EASTEX interface | RT | 37.9 (Jan–Aug, Apr 56%) | 56.8 | 6.0 → 56.8 | 9.5× | 299 → 482 | yes |
| Lubbock LPLMK–LPLNE 115 kV | RT | 30.1 (Jan–Aug) | 45.1 | 0.7 → 45.1 | 64.3× | 43 → 382 | yes |
| STP–Wap 345 kV | RT | 60.9 (Jan–Aug) | 91.3 | 52.5 → 91.3 | 1.7× | 288 → 688 | yes |

**Deviation from the brief (D1): two 2026 rents were not used in the primary run.** SEA 138/69 kV ($33.8M) and MDO–PHR ($31.9M) appear only in the market monitor's *day-ahead* 2026 list. I did not anchor on them: (1) the lanes are real-time prices, and a day-ahead rent is not a real-time shadow price; (2) 92% of the SEA rent falls in July and August, so annualising January–August by 12/8 treats a two-month summer pocket as a year-round one; (3) the 60-day disclosure cannot confirm the SEA transformer, because no priced node has a shift factor of 0.25 or more to it (the eight stations that do are unpriced). It does show a summer pocket in Bell County, with the generation nodes within about 25 km of TMPTV and PGE trading $4 to $8 below LZ_NORTH on average and more than $10 below it in 133 to 177 of 1,008 hours, but R1 attributes that to Bell County–Salado (grade C), which the primary replays, and anchoring the transformer as well may count one pocket twice; (4) applied literally, the rule puts five sites of the SEA pocket at ranks 6 to 10 of the screen, on one pair of months of one day-ahead rent. It is sensitivity S2 below, in full. Switching it on in the primary is one environment variable (`GRIDSITE_ANCHORS=2026da`) and a re-run of dispatch, forward and the tables, about 90 minutes.

### Which sites entered and left the 40

The selection rule is unchanged (greedy by screening score, at most five per dominant element, at least ten on each side) and was run on the structure the lanes use. **Five sites enter the list and five leave it.** The leavers are still valued as a group ("left out") so that leaving the list can be told apart from losing value; their values barely moved.

| Site | Change | Dominant element (side) | Revision 1: learner 2021–25 [SIM] | Revision 2: learner 2021–25, rank [SIM] | Why [DERIVED] |
|---|---|---|---|---|---|
| ATBR | enters | Twin Buttes–Hargrove 138 kV (export) | – | 84.8, #7 of 40 | placed by the geography audit (Coke County, confidence 0.60), so it passes the 0.5 gate; screening rank 5 |
| JUPITER | enters | College–Jupiter 138 kV (import) | – | 79.4, #9 of 40 | placed by the geography audit (Dallas County, 0.50) |
| LPLNE | enters | Lubbock LPLMK–LPLNE 115 kV (import) | – | 62.1, #25 of 40 | a Lubbock Power & Light station the audit moved from the wrong county (Hardeman); the Lubbock rent re-anchored on 2026 (64 times) lifts its screening score |
| LPLMK | enters | Lubbock LPLMK–LPLNE 115 kV (export) | – | 56.3, #31 of 40 | as LPLNE (export side) |
| LPLNW | enters | Lubbock LPLMK–LPLNE 115 kV (export) | – | 52.4, #33 of 40 | as LPLNE (export side); its screening score, 121,355, is the 40th |
| NOVICE | leaves | Twin Buttes–Hargrove 138 kV (export) | 83.0 | 85.5, outside the 40 | the sixth Twin Buttes site: the cap of five per element removes it once ATBR is eligible |
| CRNTH | leaves | Argyle–Lewisville 138 kV (import) | 72.5 | 72.8, outside the 40 | the rule of at least 10 export-side sites drops the lowest-scored import sites |
| SEA | leaves | SEA 138/69 kV transformer (import) | 52.9 | 51.0, outside the 40 | as CRNTH |
| ECRSW | leaves | Encrt–Brand 138 kV (export) | 48.3 | 49.9, outside the 40 | its screening score is below the new cut-off (the 40th scores 121,355) |
| CENTRVIL | leaves | College–Jupiter 138 kV (export) | 44.3 | 44.3, outside the 40 | as ECRSW |

### How much the anchor year matters

Every row of the next two tables is on one footing, the perfect-foresight value on the rebuilt lane times the lane's own learner/oracle ratio (a labelled estimate, not a dispatch run; against the primary dispatch it has a mean absolute error of $11.0/kW-yr and a rank correlation of 0.99, so its levels sit above the dispatch column by $6–17, a systematic offset: compare rows with each other, not with the dispatch column). The first table is the top 10 of the 40 under 2025-only anchors and under the 2026 anchors:

| # | Sub | Dominant element | Revision 1, learner 2021–25 [SIM] | Revision 2, learner 2021–25 [SIM] | 2025-only anchors [SIM, oracle × ratio] | 2026 anchors [SIM, oracle × ratio] | Change, 2026 minus 2025 | Rank of the 40, oracle × ratio: 2025 anchors → 2026 anchors | Top 15 depends on the anchor |
|---|---|---|---|---|---|---|---|---|---|
| 1 | MIDW | Twin Buttes–Hargrove 138 kV | 269.7 | 270.4 | 277.4 | 276.9 | −0.6 | #1 → #1 | no |
| 2 | STRS | Twin Buttes–Hargrove 138 kV | 249.5 | 250.2 | 257.0 | 256.6 | −0.3 | #2 → #2 | no |
| 3 | POWELLFI | Twin Buttes–Hargrove 138 kV | 201.5 | 200.7 | 208.3 | 208.1 | −0.2 | #3 → #3 | no |
| 4 | KNAPP | Scurry Chevron–Knapp 138 kV | 98.2 | 98.1 | 103.5 | 104.6 | +1.1 | #5 → #5 | no |
| 5 | MILES | Twin Buttes–Hargrove 138 kV | 96.9 | 97.0 | 105.9 | 108.5 | +2.6 | #4 → #4 | no |
| 6 | SHRNE | Scurry Chevron–Knapp 138 kV | 89.7 | 85.9 | 95.0 | 95.4 | +0.4 | #7 → #7 | no |
| 7 | ATBR | Twin Buttes–Hargrove 138 kV | – | 84.8 | 96.2 | 98.3 | +2.1 | #6 → #6 | no |
| 8 | SEGOVI | Fort Mason–Yellow Jacket 138 kV | 79.0 | 79.5 | 88.0 | 87.9 | −0.1 | #11 → #13 | no |
| 9 | JUPITER | College–Jupiter 138 kV | – | 79.4 | 93.6 | 93.4 | −0.2 | #8 → #8 | no |
| 10 | HILND | Argyle–Lewisville 138 kV | 78.6 | 78.0 | 88.6 | 89.7 | +1.2 | #10 → #10 | no |

The second compares every sensitivity run with the primary:

| Variant (against the primary run) | Sites of the 40 that move by more than $1/kW-yr | Sites that move by more than $5 [SIM] | Rank correlation with the primary | Primary top 15 kept (of 15) | Enters the top 15 | Leaves the top 15 |
|---|---|---|---|---|---|---|
| S0: revision-1 structure on G1's geography and the refreshed risk flags (no R1 rows, no 2026 anchors) | 21 | LPLNE −21.0, LPLMK −12.2, LPLNW −8.8 | 0.98 | 15 | none | none |
| S1: 2025-only anchors (no 2026 re-anchoring) | 14 | LPLNE −21.3, LPLMK −12.2, LPLNW −8.9 | 0.98 | 15 | none | none |
| S2: also anchor on the day-ahead-only 2026 rents (SEA_AAT1, MDOPHR99_A) | 6 | PGE +41.0, TMPTV +37.8 | 0.85 | 13 | PGE, TMPTV | GT, PK |
| S3: with R1's grade-D Panhandle pattern | 1 | none | 1.00 | 15 | none | none |

- **The top of the list does not depend on the anchor year.** The top 15 is the same under 2025-only anchors (S1) and under revision 1's structure on the new geography (S0), and no site of the top 10 moves by more than $2.6 between 2025 and 2026 anchors. Only the three Lubbock sites depend on the 2026 anchor (LPLNE −$21.3, LPLMK −$12.2 and LPLNW −$8.9 under 2025 anchors; the list itself was selected under the 2026 anchors).
- **Four sites' top-15 membership depends on the anchor choice: GT, PGE, PK and TMPTV.** All four depend on the day-ahead rents of deviation D1 (S2): PGE and TMPTV would enter the top 15 (+$41.0 and +$37.8) and PK and GT would leave. On the oracle-ratio footing the primary's top 15 differs from the dispatch top 15 by one site (GT where the dispatch list has BULLCRK), because the method is an approximation; the flags refer to the oracle-ratio list.
- **The R1 structural rows and the grade-D Panhandle row barely move the 40.** S3 moves one site by more than $1 (LPLNE −$2.2), and S0 and S1 differ by at most $1.9 at any site, so the structural rows matter for the regions (section 7), not for the 40. A fifth run (S4: keep Mgses–Catsw and leave out the Far West pattern) is coded and was screened but not run.

### Upgrades

Seven of the ten top upgrades now move at least one site (revision 1: five), with the full dispatch valuation for eight of them; the table is in section 3.2. The three that move nothing are U02 Vealmoor–Koch Tap and U07 Longshore–Consavvy (they did not bind in summer 2026) and U04 WESTEX (the interface is already in the West zone price).

### Headline numbers, revision 1 and revision 2

| | Revision 1 | Revision 2 |
|---|---|---|
| 2 h learner at the 40 sites, 2021–25 mean / 2025 [SIM] | $78.3 / $54.6 | $79.0 / $55.3 |
| The same battery at the eight zone prices [SIM] | $44.4 / $17.8 | $44.4 / $17.8 |
| Sites that beat their zone [SIM] | 37 of 40 | 38 of 40 |
| Top five [SIM] | MIDW 269.7, STRS 249.5, POWELLFI 201.5, KNAPP 98.2, MILES 96.9 | MIDW 270.4, STRS 250.2, POWELLFI 200.7, KNAPP 98.1, MILES 97.0 |
| Mean gain over the zone, 2025 replay → 2029 [SIM] | $38.4 → $19.3 | $38.5 → $20.0 |
| Learner rule in a 1,000 MW fleet, with caps / zones only [SIM] | $34.9 / $27.8 | $35.4 / $27.8 |
| Nodal room the group caps allow [ASSUMPTION] | 200 MW | 220 MW |
| Twin Buttes–Hargrove reconductor: MIDW, sum of the 40 [SIM] | $247.7 → $51.0, −31.2% | $247.9 → $50.6, −30.7% |
| Top-10 upgrades that move at least one site [SIM] | 5 | 7 |
| Constraints replayed, replayed annual rent [SIM] | 260, $889M | 263, $1,443M |
| Real-time lane rebuild against the week 3–10 Oct: correlation, mean error [SIM] | 0.994, $1.85/MWh | 0.994, $1.96/MWh |
| Substations excluded by physical risk [DERIVED] | 147 of 2,869 | 148 of 2,884 |
| Tier 2: nodal gain at the valued candidates, 2025 [SIM] | +$27.0 (172) | +$26.4 (167) |
| Sites outside the 40 that are valued [SIM] | none | 46 (41 regional picks, 5 left out) |

`report-check` is now read-only: it recomputes the congestion structure in memory, compares it with the committed tables and writes nothing. The code changes to earlier phases and the tests that were changed are listed in [`notes/phase8-revision2.md`](notes/phase8-revision2.md).

---

## 1. Where to put batteries

### 1.1 The 15 best transmission sites

Transmission-connected, 69–345 kV, ranked by the 2 h learner's 2021–25 mean. All 40 candidates are in Appendix A and in [`data/sites.csv`](data/sites.csv) (150 columns, one label per column in `sites.labels.csv`). "Gain" is the site minus the same battery at its load-zone price. "SF" is the shift factor (section 2.3): negative means the **import side**, where the price rises when the line binds and a battery sells; positive is the **export side**, where it falls and a battery charges.

| # | Sub (kV) | County, zone | Constraint: side, SF, hops [DERIVED] | 2 h learner 2021–25 / 2025 [SIM] | 4 h learner 2024–25 [SIM] | Gain over zone [SIM] | Basis half-life [SIM] | Connection, lead time [DERIVED] | Risk flags [DERIVED] | Reliability rank [ASSUMPTION] |
|---|---|---|---|---|---|---|---|---|---|---|
| 1 | MIDW (69) | Crockett, WEST | HARGRO_TWINBU1_1 (import, −1.00, 8) | 270.4 / 238.2 | 339.8 | 220.6 | 2028 | new bay, 38.5 mo | flood unmapped, wildfire moderate | – |
| 2 | STRS (69) | Crockett, WEST | HARGRO_TWINBU1_1 (import, −0.92, 7) | 250.2 / 216.4 | 311.3 | 200.5 | 2028 | new bay, 38.5 mo | flood unmapped, wildfire moderate | – |
| 3 | POWELLFI (69) | Crockett, WEST | HARGRO_TWINBU1_1 (import, −0.72, 8) | 200.7 / 165.6 | 244.7 | 150.9 | 2028 | new bay, 38.5 mo | flood unmapped | – |
| 4 | KNAPP (138) | Scurry, WEST | 6437__F (import, −0.46, 0) | 98.1 / 54.0 | 112.4 | 48.3 | > 2031 | new bay, 56.5 mo | flood unmapped, seismic | 2,418 |
| 5 | MILES (69) | Runnels, WEST | HARGRO_TWINBU1_1 (export, +0.50, 6) | 97.0 / 101.7 | 124.1 | 47.3 | 2028 | new bay, 38.5 mo | flood unmapped | 2,056 |
| 6 | SHRNE (138) | Scurry, WEST | 6437__F (import, −0.33, 2) | 85.9 / 46.8 | 96.7 | 36.2 | > 2031 | new bay, 56.5 mo | flood unmapped, wildfire high | 2,452 |
| 7 | ATBR (69) | Coke, WEST | HARGRO_TWINBU1_1 (export, +0.37, 7) | 84.8 / 86.7 | 106.7 | 35.1 | 2028 | new bay, 38.5 mo | flood unmapped, wildfire moderate | 772 |
| 8 | SEGOVI (69) | Kimble, LCRA | FORTMA_YELWJC1_1 (import, −0.50, 3) | 79.5 / 43.2 | 87.0 | 32.2 | 2028 | new bay, 38.5 mo | flood unmapped | – |
| 9 | JUPITER (138) | Dallas, NORTH | COLLE_JUPIT_1 (import, −0.51, 0) | 79.4 / 61.6 | 94.7 | 37.9 | > 2031 | new bay, 56.5 mo | seismic | 367 |
| 10 | HILND (138) | Denton, NORTH | 587__A (import, −0.55, 2) | 78.0 / 46.7 | 89.7 | 36.5 | > 2031 | new bay, 56.5 mo | none | 625 |
| 11 | CRNSO (138) | Denton, NORTH | 587__A (import, −0.55, 2) | 78.0 / 47.3 | 87.7 | 36.5 | > 2031 | new bay, 56.5 mo | none | 1,253 |
| 12 | BULLCRK (138) | Borden, WEST | 6437__F (import, −0.26, 4) | 73.1 / 37.7 | 78.7 | 23.4 | > 2031 | new switchyard, 56.5 mo | flood unmapped, wildfire high | 374 |
| 13 | BG (138) | Harris, HOUSTON | DT_PK_91_A (import, −1.00, 3) | 72.3 / 64.5 | 77.7 | 25.1 | > 2031 | new bay, 56.5 mo | none | **11** |
| 14 | MID (138) | Harris, HOUSTON | DT_PK_91_A (import, −1.00, 1) | 71.8 / 60.7 | 76.3 | 24.6 | > 2031 | new bay, 56.5 mo | none | **10** |
| 15 | PK (138) | Harris, HOUSTON | DT_PK_91_A (import, −1.00, 0) | 71.3 / 60.7 | 73.2 | 24.1 | > 2031 | new bay, 56.5 mo | none | **65** |

Units: values $/kW-yr; hop counts are graph edges to the nearest end of the monitored element; lead time is months from a request today to energisation (a 69 kV site needs a 38.5-month medium-size step-up transformer, a 138 kV site a large power transformer, 56.5 months). "Reliability rank" is the rank among 2,570 substations by need (1 = most need); – means no rank: the county's outage data is unusable (reports exceed its customers), so the substation has a need score from the other components (MIDW, STRS and POWELLFI 5.6 of 100, SEGOVI 35.5) but is not ranked. A half-life of 2028 means the site's gain falls below half of its 2025 value by 2028; "> 2031" means it does not by 2031.

**What the top of the list is.** Twelve constraints sit behind the 40 candidates (limits are the median SCED limits; rents are the replayed annual rent summed over each element's contingencies, and hours the hours it binds in the replay year, SIM. Argyle–Lewisville and the Lubbock line are anchored on the market monitor's 2026 real-time rents, annualised; Twin Buttes–Hargrove and Scurry Chevron–Knapp on their 2025 rents; the rest on the persistence-discounted window):

| Element | Limit MW [REAL] | Replayed 2025 rent $M / binding hours [SIM] | Nodes behind it | Mean learner / gain [SIM] |
|---|---|---|---|---|
| Twin Buttes–Hargrove 138 kV | 151 | 137.0 / 945 | MIDW, STRS, POWELLFI (import); MILES, ATBR (export) | 180.6 / 130.9 |
| Scurry Chevron–Knapp 138 kV | 214 | 73.1 / 1,099 | KNAPP, SHRNE, BULLCRK, MPPOI (import); SCRCV (export) | 79.8 / 30.1 |
| PK–DT 138 kV (Houston) | 293 | 50.8 / 543 | BG, MID, PK, GT (import) | 71.6 / 24.5 |
| Fort Mason–Yellow Jacket 138 kV | 145 | 22.9 / 328 | SEGOVI, MASONSW, SHELLLON, JUNCTION, EOLA (import) | 71.0 / 29.1 |
| Lakena–Samathis 69 kV | 38 | 5.9 / 862 | TANK (import) | 64.7 / 15.0 |
| Argyle–Lewisville 138 kV | 216 | 56.0 / 662 | HILND, CRNSO (import); LWSVW (export) | 64.6 / 23.1 |
| HIGH36–Brenno 138 kV | 238 | 26.0 / 574 | LYONS, BRENNO, GAYHIL, SANDHI, CHAPHI (import) | 61.3 / 14.0 |
| Lubbock LPLMK–LPLNE 115 kV | 218 | 45.1 / 382 | LPLNE (import); LPLMK, LPLNW (export) | 57.0 / 7.2 |
| College–Jupiter 138 kV | 272 | 35.7 / 290 | JUPITER, APOLLO (import); MILLERRD, WYNJOYCE, LYONS_C (export) | 56.1 / 14.6 |
| SEA 138/69 kV transformer | 99 | 9.5 / 438 | TMPTV, PGE (import) | 50.5 / 9.0 |
| Encrt–Brand 138 kV | 410 | 48.1 / 595 | ROUGH (export) | 48.6 / −1.1 |
| Brand–College 138 kV | 272 | 28.3 / 212 | COLLEGE (import) | 48.3 / 6.8 |

The 30 import-side nodes gain +$40.1 over their zone on average and the 10 export-side nodes +$11.9 [SIM]; 16 nodes at 69 or 115 kV earn $99.7 against $65.2 for 24 nodes at 138 kV [SIM]. The gain is almost the same every year ($29–36), so siting matters most when the zone itself earns little: 3.1 times the zone in 2025 ($55.3 against $17.8) and 1.8 times over 2021–25 [SIM]. Two sites do not beat their zone (ROUGH, LWSVW), both export-side and far from their line.

**Two positions are inferred.** ATBR (Atlantic Bronte, Coke County, match confidence 0.60) and JUPITER (Dallas County, 0.50, the far end of the 2.8-mile College–Jupiter line) were placed by the geography audit of revision 2 from names, operators and the line graph; their dollar values do not depend on the coordinate, but the 0.5 eligibility gate, the county, the risk flags and the connection class do. COLLEGE, placed at 0.55 at an unnamed Garland Power & Light node, is the same kind of inference (section 'What changed').

### 1.2 A placement checklist

1. **Pick a pocket, not a substation.** The value belongs to a constraint: a high rent on a small limit (Twin Buttes–Hargrove: $145M of 2025 rent on 151 MW [REAL]) and no fix on file. Then take the import-side node with the largest shift factor.
2. **Be on the right side.** Import side sells into the spike; export side charges cheaply but its injection loads the line, so discharge is limited when the constraint binds. The export-side nodes gain a quarter of what the import side does.
3. **Size for the first 10 MW.** One hundred MW at MIDW would offset 66% of the Twin Buttes limit [DERIVED]; the price-taker cap for the whole pocket is 10 MW (section 1.3).
4. **Check the clock.** A lead time of 38.5–56.5 months [DERIVED] means a request filed in Oct 2026 energises in late 2029 or mid-2031. The premium has to last that long and then repay the battery: look at the basis half-life and at the storage already queued behind the same line.
5. **Check the wires.** If the TSP can fix the line for a few million dollars (section 3), the premium is a bet on a decision you cannot make. Twin Buttes–Hargrove is identified in the regional plan with no date; Scurry Chevron–Knapp has no project on file.
6. **Add reliability separately.** If the same substation is in the reliability top 100 (the Houston PK–DT sites), that is a second, separately paid reason; it is not extra market dollars (section 5).

### 1.3 The first ~10 MW only, and the portfolio result

The per-site values above are **first-MW prices**. A battery behind a congested line relieves it, and the shadow price falls as storage fills the gap. Phase 3 caps the nodes behind each constraint at 10% of its median limit, in whole 10 MW units, so they share one cap [ASSUMPTION]. Each node also has a point-of-interconnection cap (69 kV 50 MW, 138 kV 150 MW, 345 kV 300 MW [ASSUMPTION]) that never binds.

| Constraint | Median limit MW [REAL] | Nodes behind it | Group cap MW [ASSUMPTION] |
|---|---|---|---|
| LAKENA_SAMATH1_1 | 38 | TANK | 10 |
| SEA_AAT1 | 99 | TMPTV, PGE | 10 |
| FORTMA_YELWJC1_1 | 145 | EOLA, SEGOVI, SHELLLON, JUNCTION, MASONSW | 10 |
| HARGRO_TWINBU1_1 | 151 | MIDW, MILES, STRS, POWELLFI, ATBR | 10 |
| 6437__F | 214 | KNAPP, SCRCV, SHRNE, BULLCRK, MPPOI | 20 |
| 587__A | 216 | HILND, CRNSO, LWSVW | 20 |
| LPLMK_LPLNE_1 | 218 | LPLNE, LPLMK, LPLNW | 20 |
| 215T215_1 | 238 | SANDHI, GAYHIL, BRENNO, LYONS, CHAPHI | 20 |
| COLLE_JUPIT_1 | 272 | JUPITER, APOLLO, MILLERRD, WYNJOYCE, LYONS_C | 20 |
| COL_BRAN_1 | 272 | COLLEGE | 20 |
| DT_PK_91_A | 293 | BG, PK, MID, GT | 20 |
| 6429__D | 410 | ROUGH | 40 |
| **Total** | | | **220** |

**Group caps leave about 220 MW of high-value nodal room out of 1,000 MW** (2027–31, 200 MW a year). The rest of the fleet falls back to the zone price. Result by siting rule, $/kW-yr of the whole fleet [SIM]:

| Rule | With the caps | MW at nodes / at zones | No caps at all | Zones only |
|---|---|---|---|---|
| A1 Clockwork | 28.9 | 180 / 820 | 45.0 | 25.2 |
| A2 Forecaster | 34.4 | 220 / 780 | 100.4 | 25.6 |
| A3 Weather-regime | 29.3 | 220 / 780 | 68.1 | 22.4 |
| A4 Spike hunter | 24.7 | 220 / 780 | 80.9 | 16.9 |
| **A5 Adaptive learner** | **35.4** | 220 / 780 | 101.7 | 27.8 |

With the caps, siting at the nodes adds $4–9/kW-yr to the fleet average (A1 +3.8, A2 +8.8, A3 +6.9, A4 +7.8, A5 +7.6). Without caps every rule puts all 1,000 MW on one to three nodes (the learner rule: all of it at MIDW, $101.7), which is the number a naive "best site" ranking gives and is wrong by a factor of three. The learner and Forecaster rules come first and second in all three columns. The learner rule's 2027 placement is MIDW 10 MW, KNAPP 20, COLLEGE 20, JUPITER 20, GAYHIL 20, TANK 10, BG 20, SEGOVI 10, CRNSO 20, PGE 10, LPLMK 20 and ROUGH 20: 220 MW at twelve nodes. The regional picks of section 7 are not in the portfolio.

### 1.4 Net of connection

Connection is $192–194/kW for 37 sites (one new bay each; step-up transformer included), $232 and $261/kW for LPLNW and LPLNE (a gen-tie of 3.8 and 5.2 km) and $287/kW for BULLCRK (a new switchyard) [DERIVED]; the step-up is 87–88% of it for the new-bay sites and is an unverified proxy ($16.95M per 100 MW). At 8% over 15 years that is $22.5/kW-yr. Over the 40 sites the median value after connection is **$22.0/kW-yr** and the median break-even installed cost **$390/kW** (a zone price alone carries $197/kW) [SIM, ASSUMPTION rates]; MIDW, STRS and POWELLFI reach $1,030, $951 and $770/kW for their first 10 MW. Ancillary revenue, capacity payments and O&M are not in these numbers; for most sites the energy value alone does not carry a typical installed cost.

---

## 2. The physical-constraints map

The brief lists 32 physical constraints. For each: what public data showed, how it caps or raises battery value, and how it is modelled (or why it cannot be). "Not public" usually means CEII, ERCOT's term for critical energy infrastructure information.

### 2.1 All 32 items

| # | Item | What the data showed | Effect on battery value | How it is modelled, or why not |
|---|---|---|---|---|
| 1 | Line thermal limits (normal, emergency, 15-min, ambient-adjusted, dynamic) | SCED reports the limit it used each run [REAL]: Scurry Chevron–Knapp swings 17% across a day, 238 MW early morning to 201 MW from 16:00–19:00 [DERIVED]. Emergency and 15-minute ratings are not public. Dynamic ratings cost $23–60k per mile and lift a line 10–15% [REAL, DOE 2019] | The limit sets when and how hard a line binds. A rating change is the cheap rival to a battery: it is 192 times cheaper at the median [DERIVED] | Median SCED limit per element and its daily swing in `upgrades.csv`; dynamic ratings are an option in the wires table. Normal / emergency / 15-minute split not modelled (no data) |
| 2 | 345/138 kV autotransformers | 231 of the 1,386 constraints are transformers [DERIVED]; 230 bind in the data window, e.g. SEA 138/69 kV, SGVSW and BLESSING 345/138 kV | A transformer-limited pocket is small and sharp (TMPTV and PGE earn $53.3 and $47.7, and SEA, which left the list, $51.0 [SIM]) | Treated like any element: shift factors fitted from prices. No physics proxy for transformers (collapsed in the graph). Upgrade option: next standard autotransformer at $6,880/MVA (345/138) or $5,050/MVA (138/69), 36–69 months [DERIVED] |
| 3 | GTCs and stability interfaces | 14 GTCs named (WESTEX, Panhandle, Hamilton, McCamey, North-to-Houston, Valley, Far West...); element lists are CEII. WESTEX earned $89.1M of 2025 rent and $84.8M of real-time rent in January–August 2026 [REAL] | The largest rents are diffuse (many nodes, small shift factors), so the per-MW pocket premium is small | Fitted shift factors where an interface bound. Revision 1 had only an attenuated day-ahead fit for WESTEX (sign only) and did not replay it. Revision 2 replays it from shift factors measured in the 60-day day-ahead disclosure (grade B, section 7): a West node's shift factor minus its zone's is near zero, because the zone price already carries the interface, so it moves no West candidate. Planned fix: the 765 kV import paths, $9.06B, 2031 (a scale of 0.75 in 2031) |
| 4 | Contingency pairs (N-1, N-1-1, beyond) | A binding constraint is "this element if that one is lost": 1,386 constraints, 12 for Scurry Chevron–Knapp, 6 for Twin Buttes–Hargrove. Contingency names are ERCOT codes, not decoded. N-1-1 lists are not public. Uri (Feb 2021), the Houston derecho (May 2024) and Beryl (Jul 2024) are in the outage data | Shift factors are post-contingency; relieving an element relieves all of its contingencies | Shift factors and shadow prices per constraint; `apply_upgrade` scales every contingency of an element. The three named events enter reliability (unserved energy), not congestion |
| 5 | Voltage stability and reactive limits (Valley, Houston import, Far West) | Valley, Houston and Far West limits change by market notice and are not public; the 2025 plan retired the Valley interfaces because of the Lower Rio Grande Valley project (before summer 2027) [REAL] | Binds as interface shadow prices, not as a line: price separation without a thermal line to fix | Only through shadow prices of interfaces that bound. Revision 2 replays the Valley export pattern (grade C) and Nelson Sharpe–Rio Hondo (grade A), measured in the summer prices, with a dated residual scale of 0.50 at the Lower Rio Grande Valley project (mid 2027). Valley candidates exist now, all on the export side: the measured Valley trades below its zone and has no import premium (section 7) |
| 6 | Low system strength; ride-through and grid-forming rules | ERCOT manages it by curtailing inverter output and adding synchronous condensers (six in West Texas, in service 2027). Agreements signed on or after 1 Apr 2026 need grid-forming capability (NOG 2.14) and a series-capacitor screen (PG 5.2.10). No numeric short-circuit-ratio minimum is public [UNVERIFIED] | A weak-grid site in the Far West or Panhandle sees lower interface limits and curtailment; a grid-forming battery can raise them through the study | Not modelled. Far West and Panhandle candidates exist in revision 2 (section 7); curtailment and weak-grid derating are not in their values |
| 7 | Remedial action schemes and mitigation plans | ESRs can be the objects of a scheme (NOG 11.2(g)); a scheme cannot be planned as a fix unless the need ends within five years (PG 4.1.1.2). Element lists are not public | A scheme can trip or limit a battery in a pocket, or stand in for an upgrade | Not modelled. The curtailment flag (export side, SF at least 0.05) marks 6,915 substation–constraint pairs where injecting loads the line |
| 8 | Planned and forced outages; maintenance seasons | The element-level outage list is in the MIS secure area; only monthly counts are public (NOGRR267). Our one real-time week (3–10 Oct) is a shoulder, outage-driven week: Scurry Chevron–Knapp's window rent annualises to 6.3 times its 2025 rent [DERIVED] | Short-lived pockets overstate a year if annualised | A persistence factor of 0.15 [ASSUMPTION] discounts window-anchored constraints; the biggest ones are anchored on market-monitor rents instead: the 2026 year-to-date real-time rent (annualised ×12/8) where the element is in the 2026 top-10 lists, else the 2025 rent. For the five replayed elements that are re-anchored the 2026 rent is 1.1 to 64 times the replayed 2025 figure (a sixth, Mgses–Catsw, is replaced by R1's Far West pattern); R1 names the persistence factor as the largest single reason revision 1 found nothing outside its Twin Buttes pocket. No outage calendar |
| 9 | DC ties and Mexico | Laredo VFT North–Las Cruces earned $60.7M in 2025 over 1,053 hours [REAL]. Railroad, Eagle Pass, East and North are not edges in the line graph | Congestion beside a tie can be large but needs a tie model | No regression shift factor exists, so nothing is replayed there; sized with an assumed class shift factor in the upgrades table. R1 found no pocket at the five stations near Laredo North in the summer prices (22 hours beyond $10 against the 24 it needs) |
| 10 | Radial load pockets | 616 of 3,498 graph-linked substations (18%) sit behind a bridge in the line graph, 467 after a HIFLD cross-check [DERIVED]; West Texas has the highest share. Largest pockets 7–8 substations | Islanding and load-shed exposure: reliability value, not market value | Pocket sub-score (weight 0.25 [ASSUMPTION]) in the reliability table |
| 11 | Power-transformer MVA and firm capacity | Not public per substation (NP4-160 has none; the network model is CEII) | Caps how much a substation can import or export; reverse flow | Step-up transformer at 110% of battery MW and $16.95M per 100 MW [UNVERIFIED proxy]. Firm capacity not modelled. Tier 2 uses the service-transformer nameplate rule |
| 12 | Free bays, breaker positions, bus configuration, room | Not public | No free bay means a new switchyard or gen-tie: more dollars and months | Connection class: new bay if the station is a through station with two or more line terminations in the line graph, else switchyard or gen-tie; 43% of 5,033 stations take a default class (confidence 0.3), which assumes a free position |
| 13 | Breaker duty, protection coordination, transfer trip | Not public. A transmission POI needs breakers that can interrupt fault current and be controlled remotely (PG 5.2.11; effective date unverified) | Can add cost and months | Not modelled. Tier 2: fault-current screen assumed not binding [ASSUMPTION] |
| 14 | Equipment age, spares, transformer lead time and cost | Large power transformers take 30–60 months, bushings up to 130 weeks [REAL, DOE 2024, NLR 2026]; prices up about 77% since 2019 [secondary, UNVERIFIED] | The step-up transformer sets the schedule: 56.5 months at 138 kV and above, 38.5 below [DERIVED] | Lead time = study 20.5 months + max(build, transformer delivery 30 months + 6 to install). Age is not public |
| 15 | Gen-tie distance and right of way | 226 of 5,033 stations of 69 kV or more would need a gen-tie: median 4.8 km, 90th percentile 17.6 km, longest 82 km [DERIVED] | Adds cost beyond about 8.6 km and a CCN for a long tie | Nearest through station × 1.25 routing, MISO line costs. Two of the 40 candidates need one: LPLNE (5.2 km) and LPLNW (3.8 km), both Lubbock Power & Light stations |
| 16 | Feeder hosting capacity (thermal, voltage, protection, reverse flow, regulators) | No Texas utility publishes it (Oncor argued against a hosting-capacity map in PUCT 58306) | Caps a distribution battery | Defaults [ASSUMPTION]: 4 feeders × 1,050 customers × 5 kW. Oncor's feeder counts for 344 candidates [REAL]. Thermal, voltage, protection and reverse flow not evaluated; a five-step scale-up from a real feeder model is in `notes/phase5.md` |
| 17 | Service-transformer kVA | The repo's transformer logic (`sim/planner.py`) | Sets homes per transformer | Batteries per 25 / 50 / 75 kVA unit: 1, 2, 3 under both screens [DERIVED] |
| 18 | Texas screens: 16 TAC §25.211 and Austin Energy 90% | A pre-certified unit of 500 kW or less exporting at most 15% of feeder load and 25% of fault current pays no study fee; Austin Energy: DG at most 90% of transformer rating [REAL] | A fast path, not a refusal; larger needs a study | Applied as screens in `tier2.csv` (median fast-path cap 3.15 MW per substation at the defaults) |
| 19 | Feeder ties, switching, overhead versus underground | Not public | Alternative supply and outage duration | Not modelled |
| 20 | Where each connection type settles | Transmission ESR: its resource node. DESR: a resource node at one bus. Settlement-only storage of 10 MW or less: load zone while storage is over 50% of the site (nodal after NPRR995). Separately metered storage load: nodal at the bus. ADER fleets: load-zone price (`notes/rules.md`, NP §6.6, ADER Governing Document 3.3) [REAL] | Decides whether congestion value is paid to the battery at all | DESR = node price; ADER = zone control (section 6). Which bus a feeder maps to is in registration data and not public [UNVERIFIED] |
| 21 | Basis between node, hub, zone; day-ahead versus real time; CRRs | In the real week the mean node-minus-zone gap is $8.20/MWh over 1,043 nodes (median 5.68, 90th percentile 15.99) [REAL]. Real-time and day-ahead shift factors correlate at a median 0.64 | The basis is the whole premium. A congestion revenue right from pocket to hub could hedge it | Rebuilt lanes reproduce the real node basis with correlation 0.994 and a mean error of $1.96/MWh when fed the published shadow prices [SIM]. Real-time lanes only; CRR auction prices not in the data |
| 22 | RTC+B (live 5 Dec 2025): ancillary prices and state of charge | Energy and ancillary services are bought together every 5 minutes; awards are bound by state of charge (NP 6.5.7.3). Durations: Reg and RRS 30 min, ECRS 1 h, Non-Spin 4 h [Non-Spin UNVERIFIED]. Day-ahead Reg Up fell from $26.79 (2023) to $2.11/MW-h (2026) [REAL]; real-time prices are lower still | Ancillary services are no longer a second business | Separate layer: $15.9/kW-yr (2025) and $11.9 (2026), upper bound $31, 2 h [SIM]; never added to energy |
| 23 | Charging: energy and 4CP | Separately metered storage pays the nodal energy price and no 4CP charge (16 TAC §25.192, §25.501(m)(2)) [REAL]. SB 6 orders a review of 4CP by 31 Dec 2026 | The exemption is worth money on the charging side | Charging at the lane's own price, no 4CP term. Do not rely on the exemption past 2026 |
| 24 | Saturation; large loads | 41.0 GW of storage is listed behind constraints (3.9 GW operating, 34.4 GW of queue with agreements, 2.7 GW firm planned) [REAL/DERIVED]. Large loads (Batch Zero: 66.4 GW base load, 127.9 GW studied) move congestion | Storage behind a line erodes its shadow price: the premium falls to about half by 2029 | Per-constraint scale factor from effective MW × shift factor (60% of unbuilt MW realised [ASSUMPTION]). Large loads only enter the reliability score (county data; most counties unknown) |
| 25 | Price-taker limit | A 100 MW battery at MIDW offsets 66% of the Twin Buttes limit [DERIVED] | The first-MW price is not the 100th-MW price | Group caps of 10% of the limit [ASSUMPTION]: 220 MW of room for 1,000 MW; per-node caps by voltage |
| 26 | Interconnection process and cost | For agreements signed after 31 Dec 2025 the generator pays interconnection facilities above $14M (138 kV and below) or $20M (above) and the TSP pays network upgrades (16 TAC §25.195(f)); the risk left is curtailment. Median 20.5 months from study request to agreement, 35.6 months to commercial operation [REAL] | Cost and delay, not a bill for upgrades | `connection_costs.csv`: class, cost range, owner share, lead time for 5,033 stations |
| 27 | Flood, hurricane, wildfire, extreme cold and heat | Of 2,884 site-assessed substations 148 (5.1%) are excluded: 131 in a FEMA flood hazard area, 10 in a floodway, 7 at very high wildfire hazard [DERIVED]. 78% of West Texas has no digital flood polygon. None of the 40 is excluded; SHRNE and BULLCRK sit at high wildfire hazard | Exclusion, or a mitigation cost | Flags and exclusion in `risk_flags.csv`. Heat and cold derating (HVAC, line sag) not modelled; the days at or above 100 F and at or below 20 F are carried as a coarse marker |
| 28 | Land: parcel, slope, roads, easements, ordinances, NFPA 855 setbacks | No public data at this scale | A site that cannot be built | Not modelled; the new-bay assumption presumes room |
| 29 | Telemetry path; environmental and cultural review | Not public. ERCOT requires real-time telemetry of state of charge and limits (NP 6.5.5.2) | Time and cost | Not modelled |
| 30 | Induced seismicity (Permian) | Magnitude 3 or larger events since 2015 within 25 km flag 453 substations, in the Delaware Basin, Midland, Scurry and also the Irving–Azle cluster [REAL, USGS] | A risk flag | Flag only, never excludes. KNAPP, SCRCV, APOLLO, COLLEGE and JUPITER are flagged (the last two since the geography fix put them in Garland, within 25 km of the Irving events of 2015, magnitude 3.6 at most) |
| 31 | Load-shed exposure | Radial pockets (item 10); outage history by county (EAGLE-I: 254 counties, 215 usable); Oncor SAIFI/SAIDI for 3,279 feeders; OSM hospitals (735), water works (1,238) and emergency stations (2,567). Under-frequency load-shed feeders are not public | Reliability value to customers | Five sub-scores combined into a need score 0–100 [ASSUMPTION weights]; under-frequency load shed not modelled |
| 32 | Backup value | A 1 MW / 4 MWh battery on a 1 MW critical load would have avoided 3.7 MWh a year of unserved energy in the median Texas county (Harris 1.1, Bexar 0.5) [DERIVED]; Beryl dominates in Harris (3.3 MWh) | At $35,685/MWh [REAL, Brattle 2024] that is about $131k a battery-year ($74k duration-adjusted) in the median county: customer value, not revenue | Kept apart from market value; bounded above (full charge, always-present load) and below (small events omitted) |

### 2.2 Your three examples

**Transformers to upgrade.** Transformers are 231 of 1,386 binding-constraint rows [DERIVED]. Three are in the upgrade table: SEA 138/69 kV (99 MW; $4.2M, 36–69 months; a battery for 10% relief costs 5.6 times as much), La Palma 138 kV (630 MW; $11.8M; 25 times) and SGVSW 345/138 kV (746 MW; $11.8M; 27 times) [DERIVED]. Lead time is the transformer: 36 months commonly quoted, up to 60 [REAL, DOE 2024]. For siting, the SEA pocket is the one that matters: TMPTV and PGE earn $53.3 and $47.7 (rank 32 and 36 of 40), SEA $51.0 (outside the 40 since revision 2), and TMPTV and PGE lose half their gain by 2029 [SIM].

**Lines to upgrade.** Ten are in the table in section 3: the lines that matter most to siting are Twin Buttes–Hargrove (−31% of the 40-site total if reconductored), PK–DT (−5.4%), Scurry Chevron–Knapp (−4.6%) and HIGH36–Brenno (−3.5%) [SIM]; two of the ten (Vealmoor–Koch Tap, Longshore–Consavvy) did not bind in summer 2026, so there is no shift factor to value, and WESTEX and Bell County–Salado move at most one site by $1. Cost, lead time and what a battery would take instead are in section 3.

**Substations several hops from a cap.** This is the sweet spot of the brief, and the data show it: MIDW (8 hops), POWELLFI (8) and STRS (7) are the most valuable sites we found, because their electrical distance to Twin Buttes–Hargrove is short (SF −1.00, −0.72, −0.92), whatever the line count. Section 2.3 explains why.

### 2.3 Hops versus shift factor: electrical distance decides

For every placed substation the study computes two distances to each frequently binding element: the **hop count** on the line graph (substation to nearest end of the monitored element) and the **shift factor** (SF), the estimated electrical distance: how many dollars a $1/MWh shadow price on the element moves the price at the substation. SF is fitted from real prices (median out-of-sample R² 0.978 in real time, 0.756 day-ahead [DERIVED]); hops come from an OSM/HIFLD line graph with assumed reactances.

- **Hops explain little.** Over 104,969 substation–constraint pairs the rank correlation of hops with |SF| is −0.42 (−0.48 for the real-time fits alone, −0.22 for reactance distance) [DERIVED]. Of the 3,833 pairs with |SF| of 0.10 or more, 1,760 (46%) are 8 or more hops from the element (38% for the real-time fits) [DERIVED]. Among pairs within 3 hops, 33% have |SF| below 0.05: nearby but electrically weak.
- **Far can be strong.** MIDW is 8 hops from Twin Buttes–Hargrove with SF −1.00 and a $270.4 learner value; POWELLFI is 8 hops with −0.72 ($200.7); STRS 7 hops with −0.92 ($250.2) [SIM]. On the Houston PK–DT line, BG at 3 hops has the same SF (−1.00) as PK at 0 hops, and earns $72.3 against $71.3 [SIM].
- **Near can be wrong-sided.** SCRCV is at the end of Scurry Chevron–Knapp (0 hops) but on the export side (+0.26, curtailment flag): it earns $70.9 against KNAPP's $98.1 at the import end, and loses less if the line is fixed ($18.0 against $32.2) [SIM].
- **On a radial chain, SF decays with hops.** On Scurry Chevron–Knapp: KNAPP 0 hops (−0.46, $98.1), SHRNE 2 (−0.33, $85.9), BULLCRK 4 (−0.26, $73.1), MPPOI 4 (−0.25, $71.2) [SIM]. Where the graph is a chain, hops are a fair proxy; where it is meshed they are not.
- **Far and weak earns nothing extra.** LWSVW (19 hops, +0.16) and ROUGH (30 hops, +0.19) are export-side nodes that earn about their zone price (gain −$3.5 and −$1.1) [SIM].
- **Across the 40 candidates** the rank correlation of the gain over the zone with |SF| is +0.48, and with hops +0.03 (39 sites with a hop count; descriptive only, since the candidates were selected on SF) [DERIVED].

A site is a sweet spot when it is on the import side, has a large |SF|, its own injection is not curtailed by the same constraint, and the element has no upgrade scheduled. That is what the table in 1.1 ranks; hops are reported next to SF so the reader can see how little they add.

---

## 3. Upgrades

### 3.1 What each upgrade costs and who can make it happen

The ten highest-priority elements of [`data/upgrades.csv`](data/upgrades.csv) (25 elements in all, with 108 priced wires options in `upgrade_options.csv`). Costs are 2024 dollars from the MISO MTEP24 Texas unit-cost rows, screening level (-20% / +50% bands), lead times are RPG submittal to in service. Some MISO cells are read from a garbled table layout and carry UNVERIFIED in `unit_costs.csv`.

| Id | Element | Fix | Length, limit | Cost low / **mid** / high ($M) [DERIVED] | Lead time low / **mid** / high (months) [DERIVED] | Project on file | Battery for 10% relief in 80% of binding hours [DERIVED] | Battery cost / wires cost | 2025 rent [REAL] |
|---|---|---|---|---|---|---|---|---|---|
| U01 | SCRCV–KNAPP 138 kV | reconductor | 12.4 mi, 216 MW | 4.7 / **5.9** / 14.0 | 26 / **48** / 61 | none found | 50 MW / 388 MWh (7.8 h) | 17.6× | $68.1M |
| U02 | VEALMOOR–KOCHTAP 138 kV | reconductor | 7.8 mi, 237 MW | 3.3 / **4.1** / 9.4 | 26 / **48** / 61 | adjacent: TPIT-81305A/B, Sep 2026 | 45 MW / 269 MWh (6.0 h) | 18.5× | $211.4M |
| U03 | TWINBU–HARGROVE 138 kV | reconductor | 30.9 mi, 151 MW | 10.5 / **13.1** / 32.4 | 26 / **48** / 61 | identified in the RTP (RTP2025-2022-WFW01), no date | 16 MW / 159 MWh (10 h, capped) | 3.1× | $145.0M |
| U04 | WESTEX interface | 765 kV plan | – , 10,629 MW | 7,248 / **9,060** / 13,590 | 51 / **57** / 75 | yes: PBRP import paths 1–3, 2031 | 2,696 MW / 15,999 MWh (5.9 h) | 0.5× | $89.1M |
| U05 | HIGH36–BRENNO 138 kV | reconductor | 4.9 mi, 241 MW | 2.3 / **2.9** / 6.4 | 26 / **48** / 61 | hint: TPIT-103835, May 2029 | 35 MW / 319 MWh (9.1 h) | 28.3× | – |
| U06 | PK–DT 138 kV | reconductor | 0.8 mi, 293 MW | 1.1 / **1.3** / 2.3 | 26 / **48** / 61 | hint: TPIT-64499, Dec 2026 | 62 MW / 337 MWh (5.4 h) | 72.3× | – |
| U07 | LNGSW–CONSW 345 kV | rebuild | 66.8 mi, 1,197 MW | 178 / **222.5** / 334 | 34 / **52** / 75 | yes: TPIT-81268, May 2028 | 311 MW / 973 MWh (3.1 h) | 1.4× | $94.6M |
| U08 | BELCNTY–SALSW 138 kV | rebuild | 7.8 mi, 237 MW | 12.0 / **15.1** / 22.6 | 26 / **48** / 61 | yes: TPIT-87673, May 2027 | 45 MW / 269 MWh (6.0 h) | 5.0× | $94.2M |
| U09 | ENCRT–BRAND 138 kV | reconductor | 4.9 mi, 410 MW | 2.3 / **2.9** / 6.4 | 26 / **48** / 61 | none found | 74 MW / 564 MWh (7.6 h) | 51.6× | – |
| U10 | SALDS–SONTERRA 138 kV | reconductor | 14.4 mi, 232 MW | 5.3 / **6.6** / 16.0 | 26 / **48** / 61 | yes: TPIT-80546D, May 2027 | 59 MW / 392 MWh (6.7 h) | 16.1× | – |

**Battery or wires?** For the 24 elements that could be sized, the battery that relieves 10% of the limit in 80% of the element's binding hours costs a median **21 times** the primary wires fix (range 0.5 to 72) and a median **192 times** the cheapest wires option whose rating uplift covers the relief, usually a dynamic or ambient-adjusted rating [DERIVED]. Only WESTEX is below 1 (0.49: 2,696 MW and 16 GWh against the $9.06B Permian plan), which is a sizing exercise: the plan also serves load growth and the interface is stability-limited. Long rebuilds are the close calls (Longshore–Consavvy 1.4 times). Persistent binding defeats an energy-limited resource: Scurry Chevron–Knapp needs 3.2, 7.8 or 13.2 hours of storage to cover 50, 80 or 95% of its binding hours, and Twin Buttes–Hargrove, which binds day-ahead in 53% of the window's hours in 24 long episodes, is covered only 41% of the time by a 10-hour battery [DERIVED]. For the elements with a 2025 rent and no project, one year of rent is 10 to 452 times the yearly cost of the reconductor (Vealmoor–Koch Tap 452, Koch Tap–Buzzard Switch 346, Laredo VFT 180, Scurry Chevron–Knapp 101): the economics of a fix are not the obstacle [DERIVED]. Rents do not vanish fast: of the 2024 top ten, four still earn in the 2025 top ten.

**Who can make an upgrade happen** (`notes/phase4.md` section 6 has the citations):

- **The TSP decides, builds, owns and recovers the cost** from load through its transmission cost of service (4CP-based rates); wholesale storage does not pay it (16 TAC §25.192). A new line needs a PUCT CCN; rebuilding on the existing right of way or reconductoring does not (16 TAC §25.101).
- **Review path** (Nodal Protocols 3.11.4): Tier 1 projects of $200M or more need ERCOT Board endorsement; Tier 2 projects under $200M with a CCN get a 120-day review; Tier 3 is $50–200M without a CCN, or less if it is a 345 kV reconductor over a mile, added 345/138 kV autotransformer capacity or a new 345 kV substation; everything else, including a ratings change, is a neutral Tier 4 project that ERCOT does not review. A facility ratings update is not a project at all.
- **What a battery owner can do:** propose a project to the Regional Planning Group in any tier (NP 3.11.4.1); pay its own interconnection facilities above the allowance ($14M or $20M, 2024 dollars); offer to fund a project the TSP chooses to pursue (NP 3.11.4.11). **What it cannot do:** order, own, schedule or fund a network upgrade on its own.
- **Storage is not a transmission alternative in ERCOT today.** Planning treats it as a dispatchable resource; a project is reliability-driven only if no dispatch of generation and storage meets the criteria (PG 3.1.3.1). SB 415 (PURA 35.153) lets a utility contract for storage, but only to serve distribution customers, capped at 100 MW, and the utility may not own it.

### 3.2 The full rerank: what each upgrade does to the sites

For each of U01–U10 and for two bundles of planned upgrades, the shadow prices of the replayed congestion structure were scaled by `gridsite.upgrades.apply_upgrade` (0.10 of their size left after a line or transformer upgrade, 0.50 after an interface upgrade [ASSUMPTION]), all price lanes were rebuilt, and every site was valued again by the dispatch model (`gridsite.sites.value_sites`: perfect-foresight oracle on the rebuilt 2025 lane × the lane's own learner/oracle ratio, 2 h). Zone prices stay at their real values, so an upgrade removes only the node-minus-zone part. The table is [`data/upgrade_rerank.csv`](data/upgrade_rerank.csv): one row per scenario and site (480 rows for the 40 sites) with value, rank, gain over zone, first-order comparison and the portfolio effect before and after. Revision 2 adds [`data/upgrade_rerank_extra.csv`](data/upgrade_rerank_extra.csv): the same scenarios on the 41 regional picks and the five sites that left the list (552 rows).

**Seven of the ten upgrades now move at least one site by more than $1/kW-yr** (revision 1: five of ten, because four could not be evaluated). U04 WESTEX and U08 Bell County–Salado are evaluated for the first time. WESTEX moves no site: the shift factor of a West node minus its zone's is near zero, because the zone price already carries the interface. Bell County–Salado moves one candidate (TMPTV, +$1.2) and one regional pick (GEORSO, −$1.4). U02 Vealmoor–Koch Tap and U07 Longshore–Consavvy still move nothing, and not for want of data: they did not bind in summer 2026 (section 7.2). Largest effect on any of the 86 valued sites (the 40, the 41 regional picks and the 5 left out):

| Upgrade | Moves at least one site | Sites moved by more than $1/kW-yr: the 40; regional and left-out | Largest effect, $/kW-yr | Evaluated by |
|---|---|---|---|---|
| **U01** Scurry Chevron–Knapp | yes | 10 of 40; 8 of 46 | KNAPP −32.2 | full dispatch valuation |
| **U02** Vealmoor–Koch Tap | no | – | – | did not bind in summer 2026 (R1); no shift factor |
| **U03** Twin Buttes–Hargrove | yes | 31 of 40; 27 of 46 | MIDW −197.3 | full dispatch valuation |
| **U04** WESTEX interface | no | 0 of 40; 0 of 46 | none above $1 | full dispatch valuation |
| **U05** HIGH36–Brenno | yes | 6 of 40; 1 of 46 | BRENNO −19.7 | full dispatch valuation |
| **U06** PK–DT | yes | 4 of 40; 0 of 46 | MID −34.2 | full dispatch valuation |
| **U07** Longshore–Consavvy | no | – | – | did not bind in summer 2026 (R1); no shift factor |
| **U08** Bell County–Salado | yes | 1 of 40; 1 of 46 | GEORSO −1.4 | full dispatch valuation |
| **U09** Encrt–Brand | yes | 10 of 40; 6 of 46 | ECRSW −7.6 | full dispatch valuation |
| **U10** Salds–Sonterra | yes | 0 of 40; 2 of 46 | JREST −5.0 | full dispatch valuation |

For the 40 sites (a site "loses" or "gains" when its value changes by more than $1; ranks are among the 40):

| Scenario | Sites that lose (top three: before → after, rank) [SIM] | Sites that gain [SIM] | Largest loss | Sum of the 40 sites | The 220 MW the caps allow |
|---|---|---|---|---|---|
| **U03** Twin Buttes–Hargrove | 29 sites. MIDW 247.9 → 50.6 (#1 → #10); STRS 227.3 → 48.2 (#2 → #14); POWELLFI 178.1 → 42.8 (#3 → #23); also MILES −55.6, ATBR −47.0, and the Scurry–Knapp sites −11 to −21 | TANK +7.9 (#30 → #16), LPLNE +6.1 (#31 → #19) | MIDW −197.3 | 2,513 → 1,741 (−30.7%) | −22.4% |
| **U06** PK–DT | 4 sites. MID 57.3 → 23.1 (#13 → #38); PK 56.8 → 22.7 (#14 → #40); GT 56.6 → 23.0 (#15 → #39); BG −33.4 | none | MID −34.2 | 2,513 → 2,377 (−5.4%) | −5.2% |
| **U01** Scurry Chevron–Knapp | 9 sites. KNAPP 73.8 → 41.6 (#6 → #25); SHRNE 64.5 → 41.3 (#9 → #28); BULLCRK 52.8 → 34.8 (#18 → #32); SCRCV −18.0, MPPOI −16.7 | LPLNE +1.8 | KNAPP −32.2 | 2,513 → 2,397 (−4.6%) | −6.5% |
| **PLANNED_ALL** every dated planned upgrade incl. 2032+ | 8 sites. CRNSO 63.7 → 24.6 (#10 → #39); HILND 63.6 → 24.7 (#11 → #38); LWSVW −4.2, MILES −2.8, ATBR −2.3, SANDHI −2.1 | 7 sites: PGE +3.7, TMPTV +2.9, BRENNO +2.8, CHAPHI +2.7 | CRNSO −39.1 | 2,513 → 2,437 (−3.0%) | −5.8% |
| **U05** HIGH36–Brenno | 5 sites. BRENNO 48.4 → 28.7 (#24 → #38); LYONS 50.0 → 31.6 (#20 → #32); GAYHIL 49.5 → 31.5 (#22 → #33); SANDHI −17.6, CHAPHI −16.9 | SEGOVI +1.0 | BRENNO −19.7 | 2,513 → 2,424 (−3.5%) | −2.8% |
| **U09** Encrt–Brand | 5 sites. ROUGH 32.5 → 26.9 (#36 → #40); SCRCV −2.6; LPLNW −1.2 | 5 sites: KNAPP +4.4, SHRNE +3.8, BULLCRK +2.9, MPPOI +2.7, LPLNE +1.6 | ROUGH −5.6 | 2,513 → 2,517 (+0.2%) | −1.2% |
| **PLANNED_2031** every planned upgrade in service by 2031 | 6 sites: MILES −2.7, ATBR −2.3, SANDHI −2.0, GAYHIL −1.7, LYONS −1.4, LWSVW −1.2 | 4 sites: PGE +3.4, BRENNO +2.8, CHAPHI +2.7, TMPTV +2.4 | MILES −2.7 | 2,513 → 2,513 (0.0%) | 0.0% |
| **U08** Bell County–Salado | none | TMPTV +1.2 | EOLA −0.1 | 2,513 → 2,518 (+0.2%) | +0.2% |
| **U10** Salds–Sonterra | none | none above $1 (TMPTV +1.0) | EOLA −0.1 | 2,513 → 2,516 (+0.1%) | +0.1% |
| **U04** WESTEX interface | none | none | MIDW −0.1 | 2,513 → 2,513 (0.0%) | 0.0% |
| U02 Vealmoor–Koch Tap, U07 Longshore–Consavvy | **cannot be evaluated**: the constraint did not bind in summer 2026, so there is no shift factor to replay and no site moves | | 0 | 2,513 → 2,513 | 0 |

How to read it:

- **Twin Buttes–Hargrove is the upgrade that matters, and it would reorder the list.** After it the best five are JUPITER ($63.9), CRNSO ($63.3), HILND ($63.1), BG ($57.1) and MID ($57.1): the Twin Buttes pocket falls out of the top five and the College–Jupiter, Argyle and Houston pockets take over [SIM]. It is a $13.1M (mid) reconductor with a 48-month lead time and an RTP entry with no date [DERIVED]. This is the fix whose timing the Twin Buttes sites are most exposed to. It reaches the other groups too: 27 of the 46 regional and left-out sites move by more than $1, the largest NOVICE (−$48.6, a Twin Buttes site that left the list) and CHERRYCR (−$24.8, a Far West pick whose modelled value rests on Twin Buttes shift factors 260 km away, section 7.4).
- **Gains are small and come from two mechanisms.** First, rank gains: sites that do not lose move up because others fall. Second, value gains at sites exposed to the upgraded element with the opposite sign to their main constraint: removing that term removes an offset. TANK gains $7.9 (+20%) and LPLNE $6.1 when Twin Buttes is fixed; KNAPP, SHRNE, BULLCRK and MPPOI gain $2.7–4.4 when Encrt–Brand is fixed; Encrt–Brand and Scurry Chevron–Knapp bind on the same contingency (DMTSCOS5) and follow the same West price driver in the replay, a likely reason their terms offset (not tested).
- **The planned wires by 2031 do not reach the candidates.** The bundle (31 projects, 55 replayed constraints) moves no site of the 40 by more than $3.4; the only dated project on a candidate's dominant element is Argyle–Lewisville (Dec 2032, match confidence 0.6), and the RTP or TPIT matches on the other candidate elements (Fort Mason–Yellow Jacket, PK–DT, HIGH36–Brenno) are at confidence 0.3, below the 0.5 the schedule needs. Include the 2032 project (PLANNED_ALL) and the two import-side Argyle sites lose $39 each (CRNSO and HILND; LWSVW on the export side loses $4.2).
- **The caps change the stakes.** The 220 MW the caps allow is worth $12.86M a year at 2025 replay values [SIM]; MIDW's 10 MW is $2.5M of that. U03 takes $2.9M off it (−22% if the owner stays, −21% if they re-site) because every candidate group holds some exposure to the line.

### 3.3 First order against the full valuation

Phase 4 ranked the effect of an upgrade to first order ([`data/upgrade_rerank_first_order.csv`](data/upgrade_rerank_first_order.csv): sum over constraints of |SF − zone mean SF| × shadow-price-hours × a fixed capture factor, no dispatch). The same formula, recomputed for the 40 candidates, reproduces that file's rows to 0.0005 $/kW-yr (21 candidate rows checked). Against the full valuation:

- **Baseline ranking agrees:** rank correlation 0.83 over the 40 sites (0.89 in revision 1) [DERIVED]. Post-upgrade ranks agree at 0.80–0.86 per upgrade.
- **Who loses agrees; how much does not:** over the 87 (upgrade, site) pairs that move by more than $1 in either valuation the rank correlation of the change is 0.42. Both say "loses" for 33 pairs; only the full valuation says it for 33; only first order for 14; the two disagree on sign for 14 [DERIVED].
- **First order understates the biggest on-element losses and overstates export-side ones.** U03: MIDW −197.3 (full) against −135.4 (first order), STRS −179.1 against −122.3, POWELLFI −135.3 against −95.2, but MILES −55.6 against −101.4 and ATBR −47.0 against −76.1. The two agree on the share of the premium that goes (MIDW 89% of its gain in the full valuation, 88% in first order); they differ on how large the premium was, because first order uses one capture factor (0.205 of shadow-price-hours) where the dispatch model gives MIDW 0.51 of its ceiling.
- **First order is blind to second exposures.** Under U03 the Scurry–Knapp sites SCRCV, SHRNE, KNAPP, BULLCRK and MPPOI lose $11–21 each in the full valuation and exactly 0 in first order: their Twin Buttes exposure is small per MW but the line's shadow price is large, and first order measures a zone's sensitivity as an unweighted bus mean where the lanes regress the zone's own price (a likely cause, not tested).
- **Sign flips are real:** under U09, KNAPP gains $4.4 in the full valuation and loses $2.8 in first order.

The ten largest disagreements, by the difference in the change of a site's value ($/kW-yr; ranks are among the 40 sites):

| Upgrade | Site | Full valuation, change [SIM] | First order, change [ASSUMPTION] | Full minus first order | Rank before → after, full | Rank before → after, first order |
|---|---|---|---|---|---|---|
| U03 | MIDW | −197.3 | −135.4 | −61.9 | #1 → #10 | #1 → #16 |
| U03 | STRS | −179.1 | −122.3 | −56.8 | #2 → #14 | #2 → #23 |
| U03 | MILES | −55.6 | −101.4 | +45.8 | #4 → #28 | #3 → #17 |
| U03 | POWELLFI | −135.3 | −95.2 | −40.1 | #3 → #23 | #4 → #33 |
| U03 | ATBR | −47.0 | −76.1 | +29.1 | #5 → #31 | #5 → #32 |
| U03 | SCRCV | −21.3 | 0.0 | −21.3 | #8 → #20 | #16 → #10 |
| U03 | SHRNE | −18.8 | 0.0 | −18.8 | #9 → #18 | #17 → #12 |
| U03 | KNAPP | −17.5 | 0.0 | −17.5 | #6 → #8 | #6 → #1 |
| PLANNED_ALL | CRNSO | −39.1 | −25.3 | −13.7 | #10 → #39 | #9 → #40 |
| PLANNED_ALL | HILND | −38.9 | −25.4 | −13.5 | #11 → #38 | #8 → #39 |

Eight of the ten are the Twin Buttes upgrade: the largest by far, and spread over the most sites, so the simplifications of first order matter most there. On the Argyle–Lewisville pair first order has the right sign and rank but about two thirds of the size.

### 3.4 Basis half-life

The basis half-life is the first year from 2026 in which a site's gain over its zone falls below half of its 2025 value, with planned upgrades and storage saturation applied (section 4.4). Nine sites reach it in 2028 (MIDW, STRS, POWELLFI, MILES and ATBR behind Twin Buttes; SEGOVI and EOLA behind Fort Mason; ROUGH and LWSVW), five in 2029 (TMPTV, PGE, SHELLLON, JUNCTION, MASONSW) and 26 not by 2031 [SIM]. All of the fade is storage saturation: with only planned upgrades the 2031 mean learner value moves by −$0.04, with only saturation by −$18.5 [SIM]. For the two sites with a small 2025 gain (ROUGH $5.1, LWSVW $6.5) the 50% line is a few dollars and the year is noise.

---

## 4. Trading after placement

### 4.1 Which algorithm wins at a node

A1–A5 dispatched on every rebuilt lane, walk-forward (prices up to yesterday), energy only; the oracle is perfect foresight. 2021–25 mean, 2 h, $/kW-yr [SIM]:

| Algorithm | 8 zones | 40 nodes | Gain | Nodes / zones | Share of oracle: zones → nodes | $/MWh discharged: zones → nodes | Best at (nodes / zones) |
|---|---|---|---|---|---|---|---|
| A1 Clockwork | 39.8 | 49.9 | +10.1 | 1.25 | 0.384 → 0.280 | 54 → 68 | 0 / 0 |
| A2 Forecaster | 42.9 | 81.9 | +38.9 | 1.91 | 0.414 → 0.460 | 81 → 110 | 34 / 1 |
| A3 Weather-regime | 37.6 | 70.0 | +32.4 | 1.86 | 0.362 → 0.393 | 69 → 97 | 0 / 0 |
| A4 Spike hunter | 29.6 | 60.8 | +31.2 | **2.05** | 0.286 → 0.342 | 225 → 449 | 0 / 0 |
| A5 Adaptive learner | 44.4 | 79.0 | +34.6 | 1.78 | 0.428 → 0.444 | 90 → 134 | 6 / 7 |
| Perfect foresight | 103.7 | 177.9 | +74.3 | 1.72 | | | |

- **The Forecaster, not the learner, earns the most at 34 of the 40 nodes** ($81.9 against $79.0); at the zones the learner wins 7 of 8. The learner follows whichever of six experts earned most recently, and at the nodes that is the Forecaster on most days (25% of days against 15% at the zones; 41% at the five Twin Buttes sites). A plausible reason, not tested: when one expert is persistently best, tracking it with a lag costs a little. **Caveat:** the synthetic basis is a function of hub spreads the Forecaster can read, so it may be more predictable than a real one. This needs a check against real nodal prices.
- **The Spike hunter gains most from siting, relatively but not absolutely.** It improves ×2.05 (the learner ×1.78) and its margin per MWh doubles, because a battery that keeps itself full and sells only into the top few percent earns more when the top few percent are higher. It is still $18 below the learner at all 40 nodes over 2021–25, falls further behind the stronger the pocket is (correlation of the gap with the oracle uplift −0.81), and is the best algorithm only at the three Houston PK–DT nodes (BG, MID, PK) in the 4 h run.
- **Clockwork loses ground in pockets** (38% of the oracle at the zones, 28% at the nodes): a fixed daily schedule cannot follow a basis that binds on a few days.
- **No algorithm closes the gap to the ceiling in a pocket:** the oracle is $178 at the nodes against $104 at the zones; every algorithm captures 28–46% of it.
- **4 h batteries earn 1.3–1.7 times a 2 h one at the nodes** (top 15: $133 against $93 over 2024–25; 1.38 at the zones), with a higher share of the oracle (0.58 against 0.48). The longer battery is worth most where the pocket is wide (SEGOVI ×1.69, KNAPP ×1.59, HILND ×1.59) and least at the short Houston spikes (BG ×1.34, PK ×1.33).

### 4.2 Margin per MWh

At the zones the learner earns $90 for each MWh it discharges; at the nodes $134 [SIM]. The Spike hunter's margin per MWh is the highest of the five ($225 at the zones, $449 at the nodes) because it cycles about a tenth as often. MIDW's 4 h learner earns $303 per MWh discharged and the three Houston nodes (BG, MID, PK) $136–151 [SIM].

### 4.3 The ancillary layer after RTC+B

Ancillary-service prices have collapsed. Mean day-ahead clearing price, $/MW-h [REAL]:

| | 2021 | 2022 | 2023 | 2024 | 2025 | 2026 to 10 Oct |
|---|---|---|---|---|---|---|
| Reg Up | 244.37 | 21.67 | 26.79 | 6.01 | 2.98 | 2.11 |
| Reg Down | 95.57 | 8.46 | 13.63 | 3.44 | 1.62 | 1.31 |
| RRS | 312.24 | 20.31 | 21.26 | 5.67 | 2.76 | 1.64 |
| ECRS | | | 58.16 (from 10 Jun) | 8.23 | 2.46 | 2.01 |
| Non-Spin | 134.00 | 22.49 | 19.05 | 6.22 | 3.05 | 4.21 |

Real-time prices since RTC+B are below day-ahead for every product (2026 to 10 Oct: Reg Up 1.48, RRS 0.57, ECRS 0.95, Non-Spin 2.71). The ceiling for a 2 h battery (all capacity in the best product every interval) fell from $328/kW-yr in 2023 to $79 (2024), $31 (2025) and $31 (2026 annualised) [DERIVED]. What the arena's dispatchers could realistically add is **$15.9/kW-yr in 2025 and $11.9 in 2026** for a 2 h battery (4 h: $16.3 and $15.5; upper bounds $34.8 and $44.4) [SIM, ASSUMPTION-grade], about as large as the whole zone energy value in 2025 ($17.8) and 2026 ($11.2), and a minor part of a pocket's ($55.3 at the nodes in 2025, $238.2 at MIDW). It is **system-wide**, the same at every site, so it sits in its own column and is never added to the energy value. Awards are bound by state of charge, so the real number lies between that estimate and the ceiling; more storage dilutes rather than adds, so do not scale it by fleet size.

### 4.4 Forward saturation: 2027–2031

From the 2025 congestion replay, scaled each year by planned upgrades and by the storage that arrives: 41.0 GW listed (3.9 GW operating in 2026, 34.4 GW of queue with an interconnection agreement signed, 2.7 GW firm planned), of which 26 GW is effective by 2031 at 60% realisation of unbuilt MW [ASSUMPTION]. Mean over the 40 candidates, 2 h learner [SIM]:

| Year | Learner at the 40 nodes | Zone controls | Gain over the zone | Gain relative to 2025 | Effective storage online (GW) |
|---|---|---|---|---|---|
| 2025 replay | 62.8 | 23.1 | **38.5** | 1.00 |  |
| 2026 | 61.5 | 23.1 | 37.3 | 0.97 | 3.4 |
| 2027 | 57.6 | 23.1 | 33.4 | 0.87 | 10.2 |
| 2028 | 49.3 | 23.1 | 25.0 | 0.65 | 18.4 |
| 2029 | 44.2 | 23.1 | 20.0 | 0.52 | 24.4 |
| 2030 | 44.2 | 23.1 | 20.0 | 0.52 | 25.7 |
| 2031 | 44.2 | 23.1 | **20.0** | 0.52 | 25.9 |

The pocket premium falls to about half by 2029 (0.52 of its 2025 value) and then holds, and **all of the fade is storage saturation** (planned upgrades alone move no candidate's 2031 value by more than $2.8). Zone prices are held at their 2025 values (no zone forecast is attempted), so only the node-minus-zone part moves; forward values are an oracle-ratio method on a 2025 price year, a different footing from the 2021–26 dispatch rows (the 2025 replay is $62.8 against $55.3 by dispatch). **The Twin Buttes decline is the fragile part**: MIDW falls from $248 to $88 by 2029 because 15 storage-hosting substations with a shift factor of about −0.065 (t about 7) add up to 111 MW of relief on a 151 MW line. A minimum shift factor of 0.075 instead of 0.05 removes the effect (re-run for this revision: MIDW $247.8 in 2031) and with it the half-life of the five sites (MIDW, STRS, POWELLFI, MILES, ATBR). Read their half-life as "2028 if the cluster counts". The Houston PK–DT nodes (BG, MID, PK, GT, $57–58) do not fade at all.

---

## 5. Reliability and physical risk (kept apart from market value)

Phase 5 scores every substation for reliability **need** in its own units and, through a named value-of-lost-load constant, in customer dollars. It is not revenue and is never added to the market columns.

- **Backup value.** A 1 MW / 4 MWh battery on a 1 MW critical load would have avoided about 3.7 MWh of unserved energy a year in the median Texas county (Harris 1.1 MWh, Bexar 0.5) if it started every outage full; with only a 20% reserve, 1.2 MWh [DERIVED]. At Brattle's one-hour, ERCOT-wide $35,685/MWh [REAL] that is about $131k a battery-year in the median county ($74k with the duration-adjusted prices). In Harris the battery would have served 0.9 MWh in Uri, 1.0 in the derecho and 3.3 in Beryl: Beryl is the event that matters for a 4 h battery in Houston.
- **Radial exposure is modest and concentrated in West Texas.** 616 of 3,498 graph-linked substations (18%) sit behind a bridge; West Texas has the highest share. The largest pockets are 7–8 substations; a single station on a single line is the usual pattern.
- **The need ranking is a screen.** The top 20 is 15 Harris substations (BU, LK, OA, JES, MAR, FAN, HP, MID, BG...) plus Jones, Brazoria, Jefferson, Taylor and Haskell counties, because county-level outage data cannot separate substations inside a county. It is not robust to the weights: with equal weights 17 of 20 stay, without the large-load component 8, with outage data only 0 [ASSUMPTION]. Use `need_rank` as a screen, not an exact ranking.
- **Overlap with market value.** Only four of the 40 sites are in the top 100: MID (#10), BG (#11), GT (#62) and PK (#65), all on the Houston PK–DT line. The Twin Buttes pocket is a low-need area: MIDW, STRS and POWELLFI (Crockett County, whose outage data is unusable) carry a need score of 5.6 of 100 and no rank, and MILES is #2,056. Houston is both a market case and a reliability case; Twin Buttes is a market case only. **The Houston sites are also the ones that do not fade** (section 4.4), but a PK–DT reconductor would take $33–34 off each (section 3.2).
- **Physical-risk exclusions.** 148 of 2,884 site-assessed substations (5.1%) are excluded: 131 in a FEMA special flood hazard area, 10 in a regulatory floodway, 7 at very high wildfire hazard. The flood check is blind in West Texas (78% have no digital polygon): "unmapped" is not "low risk". None of the 40 candidates is excluded; seismic flags (M3 or more within 25 km since 2015) mark KNAPP, SCRCV, APOLLO, COLLEGE and JUPITER and never exclude. Coastal, heat and cold markers are carried as columns.

The full table is [`data/reliability.csv`](data/reliability.csv) (5,035 substations), [`data/risk_flags.csv`](data/risk_flags.csv), [`data/eaglei_county.csv`](data/eaglei_county.csv) and [`data/critical_facilities.csv`](data/critical_facilities.csv); the map shows the top 100 and the excluded substations as layers.

---

## 6. Tier 2: distribution batteries and home fleets

Tier 2 is storage under 10 MW on distribution substations, and home-battery fleets. [`data/tier2_valued.csv`](data/tier2_valued.csv) adds market value to Phase 5's 2,581 candidates (2,026 NP4-160 substations at 138 kV or less with evidence of load, 555 OSM-only distribution substations near a load centre).

**Which price each route is paid** (`notes/rules.md`, NP §6.6, ADER Governing Document 3.3) [REAL]:

| Route | Settles at | Gets nodal congestion value? |
|---|---|---|
| **DESR**: distribution-connected storage that registers as a resource | A resource node fixed at one bus, the highest-voltage bus of the substation (or of the nearest placed substation) | **Yes** |
| **ADER**: aggregation of home batteries (1 MW or less each, pilot capped at 500 MW) | The load-zone price (the NOIE zone for Austin Energy, CPS, LCRA and Rayburn) | **No** |
| Settlement-only storage of 10 MW or less | Load zone while storage is over 50% of the site; nodal after NPRR995 | Not today |

**Method.** A candidate's DESR settlement bus is screened on a cheap score, the sum over replayed constraints of |SF − zone SF| × shadow-price-hours; of 2,128 settlement buses, 2,082 have a usable shift factor of 0.10 or more to a replayed constraint (2,533 candidates). The best 150 buses (the compute cap; score cut-off $118,621 per MW-year of exposure) were valued with `value_sites` for a 2 h battery in 2025 and in 2029 (planned upgrades and saturation applied). Candidates at buses that were not valued carry their zone value, labelled in `nodal_value_source` (2,366 below the cap, 48 with no usable shift factor): **their nodal column is the zone value, not a finding that there is no nodal premium.** In Phase 3's calibration of the same score on its pool, below the 90th percentile the mean 2025 gain was under $7/kW-yr. The ADER route carries the load-zone value of the same 2 h battery ($17.7–28.4/kW-yr by zone) [SIM]. Each candidate also carries the distance from its settlement bus to the line behind its value (`nodal_distance_to_element_km`, `nodal_distance_class`, threshold 150 km [ASSUMPTION]).

**Results at the 167 valued candidates** (150 buses; 146 NP4-160 candidates and 21 OSM-only; 33 of the buses are Tier 1 candidates too). The last three columns split them by how far the settlement bus is from the nearest placed end of the line its value comes from (`nodal_distance_class`) [SIM]:

| | All 167 | Within 150 km of the line (84) | Beyond 150 km (74) | Line not placed (9) |
|---|---|---|---|---|
| DESR nodal value, 2025 replay, mean | $50.5 | $60.5 | $40.1 | $43.3 |
| ADER zone value of the same battery, mean | $24.2 | $24.9 | $23.5 | $22.5 |
| **Gain of the nodal route, 2025: mean (median)** | **+$26.4** (+$21.8) | **+$35.6** (+$25.6) | +$16.5 (+$13.3) | +$20.8 (+$24.4) |
| Gain of the nodal route, 2029 after saturation: mean (median) | +$11.8 (+$8.5) | +$15.3 (+$14.1) | +$7.1 (+$6.0) | +$17.5 (+$16.5) |

- **DESR captures the nodal premium; ADER does not.** 166 of the 167 gain over the zone, 75% by more than $10. A home fleet gets the zone value however congested its feeder is.
- **Distance says how far to trust a value.** 74 of the 167 sit more than 150 km from the line their value comes from (up to 612 km), where a shift factor of 0.10 or more is physically unlikely [DERIVED]. Two causes: a mis-placed line end (Encrt–Brand, 4 candidates: its BRAND end was matched to BRAND_C in Dallas by a weak alias; the College–Jupiter case, 10 candidates in revision 1 because COLLEGE sat in Brazos County, is fixed by the geography audit and 2 remain) and, for the 60 behind Twin Buttes, day-ahead fits. They gain +$16.5 against +$35.6 within 150 km. The flag is a caution, not a correction.
- **Most of the valued buses sit behind Twin Buttes–Hargrove** (97 of 167), on the day-ahead fits that are the weakest in the study. Of these 60 are more than 150 km from the line (gain +$17.0, +$5.9 by 2029); the 35 within 150 km gain +$47.1, falling to +$7.5 by 2029. The strongest are the Tier 1 sites and their neighbours (BUSBY $152.5, KETCHUM $143.2, RCKHOLW $113.6). The other pockets hold up better, mean gain 2025 → 2029: Argyle–Lewisville (17 candidates) +$29.6 → +$21.2, Fort Mason (14) +$24.6 → +$11.6, College–Jupiter (11) +$26.7 → +$24.6, HIGH36–Brenno (9) +$22.3 → +$20.7, PK–DT (4) +$38.6 → +$38.7, Lubbock (5) +$10.1 → +$11.8. Placement and county of some distribution substations come from name matching (25 of the 167 have a match confidence below 0.5); the confidence is a column.
- **The pocket's room is shared.** A distribution battery behind Twin Buttes–Hargrove uses the same 10 MW of price-taker room as the Tier 1 sites (`nodal_group_cap_mw`).
- **Deferral value and reliability are separate columns.** The distribution deferral value is $43 / $48 / $60 per kW-yr (low / mean / high; 25 non-wires studies [REAL]) and applies only where a utility documents a need and runs a request for proposals (SB 415, proposed 16 TAC 25.58); the reliability need score (median 38.4 of 100 [ASSUMPTION]) and the unserved-energy dollars are as in section 5. None is added to a market value.
- **Home fleets** earn by zone, energy only (arena home spec, SIM): $24.5 (West) to $32.5 (Houston) per kW-yr merchant; the best programmes modelled are Austin Energy tolling ($102, if a tranche opens), data-centre flexibility in DFW ($82), co-op peak in LCRA and Rayburn ($77–82) and SB 415 feeder contracts in Houston ($61); contract terms are arena assumptions.
- **Hosting.** The no-study fast path (16 TAC §25.211) is 0.79 MW per feeder and 3.15 MW per substation at the default feeder peak (median); no Texas utility publishes feeder ratings, so 87% of candidates use the default [ASSUMPTION]. A shared feeder model would replace those columns; the method (sweep every service transformer, add a feeder layer judged by OpenDSS, turn the upgrade into a ladder, roll up to the substation) is in `notes/phase5.md`.

---

## 7. Regions: Panhandle, Valley, Far West

Revision 1 had no candidate site in the Panhandle, the Rio Grande Valley or the Far West. The cause was the data, not the grid: shift factors came from one real-time week (3–10 Oct 2026) and 31 day-ahead days, a shoulder period in which the Valley, Panhandle and Far West interfaces barely bound. ERCOT's 60-day day-ahead disclosure, which the dispatch platform keeps in its lake, holds **measured** prices for peak summer: hourly day-ahead prices at 1,003 settlement points on 42 delivery days (1 Jul–11 Aug 2026) [REAL]. It holds no shadow prices and no constraint data, so every constraint behind a pattern below is inferred, never read. Day-ahead only: real-time spikes are not in it, and it is one summer. Method and tests are in `notes/phase7-coverage.md`; the map has a layer for the regional candidates and one for the measured basis.

### 7.1 What the summer prices show

Basis is the price at a node minus the price of its load zone (and, in brackets, minus the system reference HB_BUSAVG), hour by hour. Hour-beginning, Central time.

| Region | Nodes | Mean basis against the load zone (against the system) $/MWh [REAL] | 5th percentile of node-hours [REAL] | Node-hours below −$10 | Node-hours above +$10 | Worst hour of the day, mean against the zone [REAL] | 2 h day-ahead value, node minus zone, $ per kW over the 42 days [DERIVED] | Reading |
|---|---|---|---|---|---|---|---|---|
| Rio Grande Valley | 66 | −3.36 (−4.71) | −14.4 (−20.3) | 9.9% | 0.5% | −11.8 (hour 20) | −0.39 | discount (export side) |
| Panhandle | 20 | −3.58 (−2.71) | −17.0 (−13.6) | 9.6% | 0.0% | −5.9 (hour 20) | −0.31 | discount (export side) |
| Far West | 115 | −0.11 (0.78) | −4.0 (−11.8) | 0.5% | 0.4% | −2.0 (hour 17) | +0.00 | little |
| Laredo/South | 46 | −1.20 (−2.58) | −16.4 (−20.5) | 10.5% | 7.3% | −5.8 (hour 16) | +0.45 | two-sided |
| Central | 121 | 1.27 (0.84) | −4.3 (−5.8) | 2.6% | 5.3% | 0.1 (hour 8) | +0.39 | premium (import side) |

- **The Valley's summer congestion is an export discount, not an import premium [REAL].** 17.2% of Valley node-hours are more than $10 below the system and 1.1% more than $10 above; against the node's own load zone 9.9% and 0.5%. Its best hour of the day is −$0.02 against the system: there is no hour in which a Valley node trades at a premium. Only 0.9% of Valley node-hours are beyond −$50. The Panhandle is the same shape (10.1% below −$10 against the system, 9.6% against its zone, never beyond −$50); its basis against HB_PAN is about zero by construction because the hub averages the Panhandle, so read it against the zone or the system.
- **The Far West has no premium against its own zone, because the zone is mostly Far West.** Against the system it is two-sided and daily: a night premium and a midday discount (6.9% of node-hours above +$10, 6.6% below −$10). Laredo/South is a discount overall (−$1.20 against the zone) with a premium tail in the Hill Country part of LZ_SOUTH (Frio, Uvalde, Dimmit and Val Verde counties; the 95th percentile of node-hours is +$13.9). Central Texas shows a small premium (+$1.27).
- **A battery does not gain from these discounts in this window [DERIVED].** The 2 h perfect-foresight day-ahead value at the node minus at its zone (1 MW / 2 MWh, 85% round trip, $20/MWh wear), per kW over the 42 days and not annualised, averages **−$0.39 in the Valley, −$0.31 in the Panhandle, +$0.00 in the Far West, +$0.45 in Laredo/South** (90th percentile +$1.38) and +$0.39 in Central, against $0.9 to $1.9 for the zones themselves. The evening discount sits on the hours a battery wants to sell. **Valley and Panhandle batteries show no uplift over their zone.**

### 7.2 Constraints that did not bind in summer 2026

Two of the four top-10 upgrades that revision 1 could not value, and a third element outside the top 10, are not missing a shift factor: the constraint was quiet [REAL].

- **Vealmoor–Koch Tap (U02)** earned $211.4M in 2025, but nothing in the market monitor's 2026 year-to-date lists (January–August) comes near it: the tenth-ranked real-time rent is $27.1M, and the element is in none of the four top-10 lists (rent and frequency, real time and day-ahead). In the summer prices the stations around the placed ends deviate from their surroundings by more than $10 in 8 of 1,008 hours (24 are needed for a pocket).
- **Longshore–Consavvy (U07)**'s neighbour LNGSW–PRLSW earned $48.9M in 2026 but only in January–June: 35 binding intervals in July and 1 in August. The pocket test finds 0 hours.
- **Odessa EHV–Yarbrough** (not in the top 10 upgrades) is not in the 2026 lists either, and the pocket test finds 0 hours; Laredo VFT–Las Cruces finds 22 hours against the 24 it needs.

Their 2025 structural rents describe a system that no longer looks like this; public data do not say why. Caveat: Consavvy, Yarbrough and Las Cruces have no placed coordinates, so one end of each of those lines was tested. The other two unevaluated upgrades are different: **WESTEX (U04)** and **Bell County–Salado (U08)** left patterns in the prices (section 7.3), are replayed, and are now valued in the rerank (section 3.2).

### 7.3 Shift factors for the structural constraints, and which ones are good enough to replay

A non-negative decomposition of the hourly basis matrix (669 substations with prices in at least 90% of hours, 32 components; 8 components explain 91% of the standardised variance, 32 explain 98.6%) finds the regional patterns. Hold-out (patterns from days 1–32, test days 33–42): R² 0.93 using all stations, 0.82 predicting half the stations from the other half. Each pattern is matched to a named constraint by geography, side and, out of period (10 Sep–10 Oct, published day-ahead shadow prices), by how well its activity tracks the constraint's published shadow price. Grades: A the published series is tracked (r of 0.6 or more), B moderately (0.4–0.6), C geography and side only, D the name is not supported, X not identified [DERIVED].

| Target | Constraint | Side | Grade | Evidence [DERIVED] | Replayed |
|---|---|---|---|---|---|
| `WESTEX` | West Texas Export GTC | export | B | tracks the published Sep–Oct shadow price, r = 0.47 (67 bound hours) | primary |
| `I_FW_N` | Far West import from the North GTC | import | C | geography and side only; too few published bound hours to test (9) | primary |
| `I_FW_S` | Far West import from the South GTC | import | C | geography and side only; too few published bound hours to test (1) | via I_FW_N |
| `PNHNDL` | Panhandle GTC | export | D | does not track the published Sep–Oct shadow price, r = −0.01 (130 bound hours) | sensitivity S3 only |
| `NELRIO` | Nelson Sharpe–Rio Hondo GTC | export | A | tracks the published Sep–Oct shadow price, r = 0.66 (192 bound hours) | primary |
| `NE_LOB` | North Edinburg–Lobo GTC | export | D | does not track the published Sep–Oct shadow price, r = 0.01 (54 bound hours) | via VALEXP |
| `VALEXP` | Valley Export GTC | export | C | geography and side only; no published series | primary |
| `VALIMP` | Valley Import GTC | import | X | no pattern in its domain on its side | no |
| `PASP_PATA` | South Texas Export GTC (E_PASP / PASP_PATA) | export | A | tracks the published Sep–Oct shadow price, r = 0.78 (118 bound hours) | primary (as E_PASP) |
| `LARDVFT` | Laredo VFT North–Las Cruces 138 kV | either | X | price pocket: 22 of 1,008 hours beyond $10 (24 needed) | no |
| `ODEHV_YARBR` | Odessa EHV–Yarbrough 138 kV | either | X | price pocket: 0 of 1,008 hours beyond $10 (24 needed) | no |
| `VEALMOOR_KOCH` | Vealmoor–Koch Tap 138 kV | either | X | price pocket: 8 of 1,008 hours beyond $10 (24 needed) | no |
| `LNGSW_CONSW` | Longshore–Consavvy 345 kV | either | X | price pocket: 0 of 1,008 hours beyond $10 (24 needed) | no |
| `BELCNTY_SALSW` | Bell County–Salado 138 kV | either | C | price pocket: 124 of 1,008 hours beyond $10; the pattern tracks it, r = 0.78 | primary |

Six patterns are replayed in the congestion structure of Phase 3b (`WESTEX`, `NELRIO`, `E_PASP`, `I_FW_N`, `VALEXP`, `BELCNTY-SALSW|UNSEEN`); the grade-D Panhandle pattern only in a sensitivity run (S3, section 'What changed'). Their targets are the summer window scaled by the market monitor's 2026 monthly series where one exists; shift factors for substations without a priced node are the inverse-distance weighted median of the four nearest priced stations within 60 km, labelled `interpolated`. Two cautions travel with them. The scale of a pattern is known for E_PASP only (a physical slope of 0.099 against 0.157 from the rent route and about 0.3 from Phase 2's fit: a factor of three); the others are on the pole scale (shift factor 1 at the pattern's pole), so the saturation of relief against a limit overstates relief on NELRIO, WESTEX and the Far West until a scale is chosen. And the loadings explain only 40% of the standardised September–October basis: a summer pattern set is a partial map of the autumn market.

### 7.4 The 41 regional picks, measured against modelled

R1 screened every eligible substation in the four regions and Bell County on its exposure to the structural patterns and on its measured basis, and picked up to five per region and side (37 of the 41 measured at a priced resource node, 4 interpolated). Revision 2 values each pick with the same code as the 40 (a rebuilt price lane, the arena's five algorithms, 2 h, 2021–25, and the forward years); they are never ranked with the 40 and are not in the portfolio. [`data/extra_sites.csv`](data/extra_sites.csv) holds them, with the measured columns beside the modelled ones.

| Region | Side | Picks | Measured: node minus zone, mean $/MWh [REAL] | Observed 2 h day-ahead value, node minus zone, $ per kW over 42 days [DERIVED] | Modelled gain over the zone, 2021–25 mean, $/kW-yr: mean (range) [SIM] | Modelled gain in 2025 [SIM] | Picks with a modelled gain above $1 | Picks whose dominant element is more than 150 km away |
|---|---|---|---|---|---|---|---|---|
| Rio Grande Valley | import | 0 | none | none | none | none | none | none |
| Rio Grande Valley | export | 5 | −4.75 | −0.34 | −3.2 (−4.2 to −1.2) | +0.4 | 0 of 5 | 0 of 5 |
| Panhandle | import | 1 | −0.39 | +0.08 | +3.2 (+3.2 to +3.2) | +1.2 | 1 of 1 | 0 of 1 |
| Panhandle | export | 5 | −3.69 | −0.32 | −4.4 (−5.0 to −3.8) | +4.9 | 0 of 5 | 3 of 5 |
| Far West | import | 5 | 0.28 | +0.17 | +6.5 (+3.6 to +12.4) | +7.1 | 5 of 5 | 5 of 5 |
| Far West | export | 5 | −1.29 | +0.02 | −4.0 (−6.9 to −2.0) | −1.6 | 0 of 5 | 0 of 5 |
| Laredo/South | import | 5 | 2.75 | +1.41 | +3.4 (+2.3 to +4.1) | +5.6 | 5 of 5 | 1 of 5 |
| Laredo/South | export | 5 | −6.56 | −0.23 | −4.4 (−6.1 to −2.7) | +1.8 | 0 of 5 | 0 of 5 |
| Central (Bell County) | import | 5 | 4.81 | +0.84 | +3.8 (−1.1 to +10.2) | +3.2 | 3 of 5 | 0 of 5 |
| Central (Bell County) | export | 5 | −4.89 | +0.55 | −2.1 (−4.1 to −0.6) | −1.3 | 0 of 5 | 0 of 5 |

The eight picks with the largest modelled gain:

| Pick (county) | Region, side | Dominant element (side, distance to nearest end) | Measured: node minus zone, mean $/MWh [REAL] | Observed 2 h day-ahead value, node minus zone, $ per kW over 42 days [DERIVED] | Modelled gain over the zone: 2021–25 mean / 2025 [SIM] | Modelled gain in 2029 [SIM] | Distance reading [DERIVED] |
|---|---|---|---|---|---|---|---|
| LMESA (Dawson) | Far West, import | Twin Buttes–Hargrove 138 kV (import, 183 km) | 1.21 | +0.24 | +12.4 / +6.1 | +6.2 | line more than 150 km away |
| JREST (Williamson) | Central (Bell County), import | 1710__A (import, 1 km) | 5.01 | +0.70 | +10.2 / +8.1 | +6.0 | line within 150 km |
| CHERRYCR (Reeves) | Far West, import | Twin Buttes–Hargrove 138 kV (export, 260 km) | 0.03 | +0.16 | +8.1 / +26.3 | −1.2 | line more than 150 km away |
| GEORSO (Williamson) | Central (Bell County), import | Bell County–Salado 138 kV (import) | 12.03 | +2.18 | +7.7 / +5.1 | +3.8 | structural pattern, no line ends |
| PIGCREEK (Pecos) | Far West, import | Twin Buttes–Hargrove 138 kV (import, 209 km) | 0.11 | +0.18 | +4.2 / +1.2 | +0.5 | line more than 150 km away |
| CARRIZO (Dimmit) | Laredo/South, import | NLARSW_PILONC1_1 (import, 34 km) | 4.90 | +1.87 | +4.1 / +8.3 | +0.9 | line within 150 km |
| FLAT_TOP (Reeves) | Far West, import | Twin Buttes–Hargrove 138 kV (import, 230 km) | 0.10 | +0.15 | +4.1 / +0.7 | +0.5 | line more than 150 km away |
| DOWNIES (Uvalde) | Laredo/South, import | Valley export pattern (import) | 1.50 | +1.20 | +3.7 / +4.5 | +1.4 | structural pattern, no line ends |

- **No regional pick resembles the Twin Buttes pocket.** The 41 average a modelled gain of −$0.5/kW-yr (largest +$12.4) against +$33.0 for the 40; 14 of the 41 are above $1 [SIM].
- **Measured and modelled agree on the sign for 28 of the 41** (rank correlation 0.65). Both are negative on the export sides of the Valley (observed −$0.34 per kW over 42 days, modelled −$3.2/kW-yr), the Panhandle (−$0.32, −$4.4) and Laredo/South (−$0.23, −$4.4), which is the finding of section 7.1; the Bell County export side is the exception (observed +$0.55, modelled −$2.1).
- **The positive modelled gains are mostly not supported by the measured prices.** Of the 14, six have a dominant element more than 150 km from the pick and five are structural patterns with no line ends. The five Far West import picks (LMESA, CHERRYCR, PIGCREEK, FLAT_TOP, LINTERNA) draw most of a modelled gain of +$3.6 to +$12.4 from Twin Buttes–Hargrove, through shift factors of 0.05 to 0.12 at stations 183–260 km from the line, where the measured premium is −$0.06 to +$1.21 per MWh and the observed value only +$0.12 to +$0.24 per kW over 42 days. Treat those modelled gains as an artefact of the day-ahead fit, not a finding.
- **Where measured and modelled both say positive** are the import side of Laredo/South (ELARA_SL, DOWNIES, CARRIZO, ASHERTON, COTULLA: observed +$0.9 to +$1.9 per kW over 42 days, modelled +$2.3 to +$4.1/kW-yr) and Williamson County in Bell County's region (GEORSO, JREST). GEORSO trades $12 a MWh above its zone on average, yet earns only +$2.18 per kW over 42 days: a flat premium is a level, and a battery earns spreads.
- **Half-lives**: for the 23 picks with a positive 2025 gain, 20 reach half of it by 2028 [SIM]; the modelled gains are small and fade like the others.

### 7.5 What this means for siting

1. **The Twin Buttes pocket is still the only large one.** Nothing in the summer prices of the Panhandle, Valley or Far West replaces it; the best regional picks are an order of magnitude smaller than the 40's average and rest on one summer of day-ahead prices.
2. **Do not site a battery in the Valley or the Panhandle for congestion.** The measured discount is on the export side, where a battery charges cheaply; in a 2 h window it does not turn into more value than the zone gives.
3. **Laredo/South's Hill Country import side and Williamson County are the places to watch**: a measured premium and a positive modelled gain agree there. They need real-time and day-ahead history across seasons (an API key, section 9) before they enter the queue.
4. **Upgrade planning**: Vealmoor–Koch Tap and Longshore–Consavvy have no case from this data (they did not bind); WESTEX moves no site; Bell County–Salado moves one candidate by $1.2.

---

## 8. How this feeds the dispatch platform

[`dispatch/`](../../../dispatch/README.md) is the operate layer of Batter Up: a shadow optimizer and attribution engine for ERCOT batteries and a paper-dispatch service behind a deterministic risk guard. Its `DESIGN.md` names this study as the source of "which settlement points to forecast and attribute". Today it forecasts and attributes at the **hubs** (HB_NORTH, HB_WEST, HB_HOUSTON, HB_SOUTH). Its public case study, a 50 MW battery settling near HB_SOUTH, uses a hub price as a proxy for the node (node minus hub: mean +$1.01/MWh, RMSE $2.93/MWh), and the proxy alone could move 11.1% of the attribution gap against a 5% target. A real nodal history closes that.

**The contract is a table.** [`data/sites.csv`](data/sites.csv) (read-only, not imported) is the **ordered queue of settlement points to add** once nodal history exists. Each row gives the electrical bus a resource node would sit on (`node_bus`), whether a resource node exists there today (`existing_resource_node`: three of the 40, KNAPP_RN, JUNCTION_RN and MASSENGL_G8 at LPLMK), the zone and hub to fall back on, and the value that sets the order. Appendix A lists all 40 in that order. The first ten:

| Queue | Sub | Bus a node would sit on | Hub proxy until there is history [ASSUMPTION] | Why first |
|---|---|---|---|---|
| 1 | MIDW | MIDW_L_A | HB_WEST | Twin Buttes, import, $270.4 [SIM]; half-life 2028 |
| 2 | STRS | STRS_L_A | HB_WEST | Twin Buttes, import, $250.2 |
| 3 | POWELLFI | POWF_L_A | HB_WEST | Twin Buttes, import, $200.7 |
| 4 | KNAPP | EB2_KNAPP (resource node KNAPP_RN exists) | HB_WEST | Scurry–Knapp, import, $98.1; no project on file |
| 5 | MILES | MILES_B114 | HB_WEST | Twin Buttes, export (charging side), $97.0 |
| 6 | SHRNE | EXSHARNR_8 | HB_WEST | Scurry–Knapp, import, $85.9 |
| 7 | ATBR | ATBR_KP | HB_WEST | Twin Buttes, export, $84.8; position inferred (confidence 0.60) |
| 8 | SEGOVI | L_SEGOVI9_1Y | HB_SOUTH | Fort Mason, import, $79.5; LCRA zone |
| 9 | JUPITER | JUPITER_AG1 | HB_NORTH | College–Jupiter, import, $79.4; position inferred (0.50) |
| 10 | HILND | HIGHLAND | HB_NORTH | Argyle–Lewisville, import, $78.0; does not fade |

Changes against revision 1: ATBR and JUPITER enter the first ten, NOVICE (Twin Buttes, export) left the list and CRNSO, which ties HILND at $78.0, is #11. The 41 regional picks of section 7 are not in the queue.

**What the platform needs per entry.** The 15-minute settlement-point price and the 5-minute bus price at the bus (reports NP6-905 and NP6-787, whose public windows are 7 and 5 days), the day-ahead price (NP4-190 / NP4-183), the binding constraints and shadow prices (NP6-86, NP4-191) and the ancillary clearing prices; the node price of a resource node equals the bus price to a cent in 99.98% of runs, so the bus price is a valid stand-in before a battery registers. **What the study gives it:** the order, the constraint and side behind each node (`constraint1`..`constraint3`), the shift factors that let the platform model a node as hub plus basis, the price-taker room (`price_taker_cap_mw`) and the risk flags. **What it needs from ERCOT:** an API key (section 9), because the public windows are a week long.

---

## 9. Confidence and limits

- **The nodal rebuild is SIM.** The formula (zone price minus the node's shift factors times shadow prices) reproduces the real node basis of 3–10 Oct with correlation 0.994 and a mean error of $1.96/MWh when fed the published shadow prices (on 3–5 Oct, before the fit window: 0.984 and $1.53; day-ahead month 0.965 and $1.29) [SIM]; revision 1 gave 0.994 and $1.85, so the six structural rows and the new anchors cost a little at the nodes of the window. What is not validated is the timing of the synthetic shadow prices: 119 of the 263 replayed constraints (45%) use a geographic rule for when they bind. **Levels are good to about a factor of two** (across six alternative SIM choices of Phase 3b, run on revision 1's structure and not repeated, the mean 2025 gain moves from −16% to +25%, the median by −46% to +72%; the anchor year is one more choice, section 'What changed'); **rankings are steadier** (rank correlation 0.90 to 1.00 across those variants; the screening score ranks a stratified pool sample at 0.94) [SIM].
- **One week of real-time shift factors.** The real-time window is 5–10 Oct plus the 25 Sep archive day, about 8.5 days: a shoulder, outage-driven week full of local 138 kV and 69 kV constraints. Shift factors are topology, so they transfer in principle; which constraints bind does not. **Structural constraints are replayed from market-monitor rents** (the 2026 year-to-date real-time rent where the element is in the 2026 lists, else the 2025 rent), not measured: 263 of 1,386 constraints enter (257 with regression shift factors and six structural rows whose shift factors come from the 60-day day-ahead disclosure; $1,443M of replayed rent, against $889M in revision 1). Still absent are Vealmoor–Koch Tap, Longshore–Consavvy, Odessa EHV–Yarbrough and Laredo VFT: they left no pocket in the summer prices (section 7.2), so the rerank cannot move any site for two of the ten upgrades. WESTEX, Nelson Sharpe–Rio Hondo, the South Texas export, Far West and Valley patterns and Bell County–Salado are replayed from inferred patterns, grades A to C (section 7.3): the constraint behind each is inferred, never read, because the disclosure holds no shadow prices.
- **The Twin Buttes pocket is a different kind of number.** Its level is anchored on a published rent ($145M) and hours (945), but its shift factors are day-ahead fits with variance inflation above 100 (day-ahead-fitted constraints rebuild the day-ahead month at correlation 0.84 and the holdout days at 0.62). Every value is a first-MW price.
- **Gate A (after Phase 1) passed with limits:** no ERCOT API key, so real-time nodal history is one week and day-ahead 31 days; the network graph has no ratings or impedances; the MISO unit-cost cells read from garbled text are UNVERIFIED. **Gate B (after Phase 2) passed for Phase 3 with limits:** real-time shift-factor fit median out-of-sample R² 0.978 over 19,199 buses, day-ahead 0.756; the sign convention was recomputed from raw parquets (the 30 most import-side buses of the most-binding constraint average +$315.84/MWh of congestion, the 30 most export-side −$111.12); 496 constraints have regression shift factors, 476 only a physics proxy (median correlation 0.38 with the regressions, so never ranked on) and 414 none; structural constraints are under-represented. Both gates and the Phase 3–6 checks are in [`notes/gates.md`](notes/gates.md).
- **Placement.** 72% of the 5,035 NP4-160 substations have coordinates and 57% a confidence of 0.5 or more (90% of those with a resource node are placed); everything that ranks sites uses 0.5 or more. Coordinates for plant-matched substations are the plant, not the fence. The geography audit of revision 2 overrode 38 placements (17 re-placed, 20 unplaced, 1 demoted) and restored 19 of the 20 constraint rows that had lost both ends; 28 placements that independent evidence contradicts have no candidate site, and 34 stations at 0.5 or more are still 40 km or more from the county that weaker evidence names (`notes/phase7-geo.md`). Three of the 40 sites rest on an inferred position: COLLEGE (0.55), JUPITER (0.50) and ATBR (0.60), described in section 1.1. Their values do not depend on the coordinate; the 0.5 eligibility gate, the county, the risk flags and the connection class do.
- **Costs are screening-level**: MISO 2024 rows, no escalation (about 4% a year), the step-up transformer is an unverified proxy and 88% of connection cost, and the break-even cost leaves out ancillary revenue, capacity payments, O&M and tax.
- **Forward values are a different kind of number** from the history: zone prices stay at 2025, the structure is the 2025 replay, a pocket that does not exist today is invisible, and the saturation constants are assumptions (`SAT_MIN_SF` is a cliff for Twin Buttes).
- **The anchor year changes the ranking below the top five** (section 'What changed'). Two day-ahead-only 2026 rents, SEA 138/69 kV ($33.8M) and MDO–PHR ($31.9M), are not used in the primary run: a day-ahead rent is not a real-time shadow price, a rent has no direction, and no priced node has a shift factor to the SEA transformer. Applied literally they would put five SEA-pocket sites in ranks 6 to 10 of the screen (sensitivity S2).
- **The regional lanes are weaker than the 40.** The 41 regional picks are valued with the same code, but their shift factors for Twin Buttes–Hargrove are day-ahead fits, with values of 0.05 to 0.12 at stations hundreds of kilometres from the line; where a pick's dominant element is more than 150 km away its modelled gain is not supported, and the measured day-ahead basis (section 7) is the better guide. The Panhandle pattern (grade D: the name is not supported) is in a sensitivity run only.
- **The Forecaster's win at nodes, the learner's relative loss and the Spike hunter's relative gain are results on these lanes** and need a check against real nodal prices.

**What an ERCOT API key would change.** The public API archives (2023 to 2026) hold what the public windows do not: bus prices and shadow prices for whole years. With them (1) the shift factors could be refitted across seasons and the one-week bias removed; (2) the structural constraints could be measured instead of inferred from the 60-day day-ahead basis (WESTEX, Nelson Sharpe–Rio Hondo, the South Texas export, Far West and Valley patterns, Bell County–Salado), and whether Vealmoor–Koch Tap, Longshore–Consavvy and Odessa–Yarbrough bound in other seasons would be answered; (3) the top sites' price lanes would be REAL, and the dispatch algorithms could be backtested on them, answering whether the Forecaster really beats the learner; (4) the timing of the synthetic shadow prices, now 45% geographic rule, would be replaced. ERCOT allows three downloads of the same report a year through the API, so the first pull should be one month of one report.

---

## 10. Reproduce

Run from `docs/research/grid` with the research venv (`../research-data/.venv/Scripts/python.exe run_grid.py <command>`); raw data and caches live outside git in `research-data/grid/`. At most 4 workers; the machine is shared.

| Phase | Commands (run in this order) | Time |
|---|---|---|
| 1a market data | `fetch-ercot all`, `parse-ercot`, `collect-daily` (at least every 4 days), `report-ercot` | minutes to tens of minutes |
| 1b planning, queue, rules | `fetch-docs`, `parse-docs` | minutes |
| 1c geography | `fetch-geo all`, `match-geo` (applies `data/geo_overrides.csv`) | 2 min match |
| 2 network, shift factors | `constraints`, `shift-factors`, `physics-proxy`, `hops`, `map substations` | 25 s, 7.5 min, 40 s, 80 s, 5 s |
| 3a infrastructure | `compat-test`, `as-layer`, `costs`, `queue-map` | minutes |
| 3b site value | `candidates`, `lanes`, `dispatch spec=all workers=4`, `forward spec=all workers=4`, `portfolio`, `sites` | about 80 min after the lanes (94 lanes in revision 2) |
| 4 upgrades | `upgrades`, `nwa`, `rerank-first-order` | 20–50 s each |
| 5 reliability, Tier 2 | `fetch-reliability all`, `risk all` (`risk fetch` queries only the stations not yet sampled), `reliability`, `tier2` | about 3 min from cached downloads |
| 6 report | `rerank workers=4`, `tier2-value workers=4 max-nodes=150`, `map`, `report-check`; `python gridsite/check_labels.py` | 13 min, 3.5 min, 10 s, 1 min, 1 s |
| 7 regions | `regional-basis`, `sf-structural [skip-validation]`, `regional-candidates` (run it again after `sites`) | 14 s, 3 min, 25 s |
| 8 revision 2 | `anchor-sensitivity variants=anchors2025,panhandle,anchors2026da,geo_only workers=4`, `regional-sites` | about 17 min, 10 s |

Environment switches (read when `gridsite` is imported; the primary run sets none): `GRIDSITE_REVISION=1` (revision-1 structure), `GRIDSITE_ANCHORS=2025` or `2026da` (2025-only anchors, or also the day-ahead-only 2026 rents), `GRIDSITE_PANHANDLE=1` (the grade-D Panhandle row), `GRIDSITE_FAR_WEST=6945` (Mgses–Catsw instead of the Far West pattern). `report-check` is read-only: it recomputes the congestion structure in memory, compares it with the committed tables and writes nothing.

Tests are plain scripts (the venv has no pytest), run from this folder: `python tests/test_6.py`, `test_3b.py`, `test_4_upgrades.py`, `test_7_coverage.py`, `test_geo_overrides.py`, `test_arena_compat.py`. The bare `map` command builds this page's map; the Phase 2 substation map is `map substations`.

**Files.**

| Path | What |
|---|---|
| `data/sites.csv` | The 40 candidate sites in queue order (150 columns): value by algorithm, 2 h and 4 h, forward values, half-life, caps, connection, risk, reliability |
| `data/extra_sites.csv` | The 41 regional picks and the 5 sites that left the list (159 columns): the same values plus the measured basis and the distance to the dominant element |
| `data/upgrade_rerank.csv`, `upgrade_rerank_extra.csv`, `upgrade_rerank_first_order.csv` | Full rerank of the 40, full rerank of the 46 others, first-order rerank |
| `data/anchor_2026_applied.csv`, `constraint_structure_rev2.csv`, `anchor_sensitivity.csv`, `anchor_sensitivity_summary.csv` | The 2026 anchors as applied, the congestion structure the lanes use, and the sensitivity runs |
| `data/regional_basis*.csv`, `sf_structural*.csv`, `regional_candidates.csv`, `regional_upgrade_reach.csv`, `geo_overrides.csv`, `geo_changes.csv`, `site_candidates_rev1.csv` | Measured basis and structural shift factors (R1), regional picks, the geography overrides (G1), and revision 1's 40 for comparison |
| `data/upgrades.csv`, `upgrade_options.csv`, `nwa_sizing.csv` | Upgrade table, wires options, battery sizing |
| `data/tier2_valued.csv` | Tier 2 candidates with market, deferral and reliability in separate columns |
| `data/reliability.csv`, `risk_flags.csv`, `portfolio.csv`, `site_years.csv`, `constraints.csv`, `shift_factors.csv`, `hops.csv` | Supporting tables; every table has a `.labels.csv` sidecar (REAL / DERIVED / SIM / ASSUMPTION per column) |
| `map/index.html`, `map/index_data.js` | The interactive map (Leaflet 1.9.4, OpenStreetMap tiles; opens as a local file; about 0.8 MB) |
| `gridsite/` | The code, one module per phase; `cli_6.py` registers this phase's commands |

---

## Appendix A. All 40 sites, in queue order

2 h learner, $/kW-yr, 2021–25 mean and 2025 [SIM]; "Gain" over the zone; "2031" the forward learner value [SIM]; connection class and lead time in months [DERIVED]; "Net of conn." = mean 2027–31 learner value minus annualised connection cost [SIM]. Constraint: dominant element (side, SF, hops).

| # | Sub | Zone | kV | Bus (existing resource node) | Constraint (side, SF, hops) | 2 h 21–25 | 2025 | Gain | 2031 | Half-life | Connection | Net of conn. |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | MIDW | WEST | 69 | MIDW_L_A | HARGRO_TWINBU1_1 (import, −1.00, 8) | 270.4 | 238.2 | 220.6 | 87.7 | 2028 | bay 38.5 | 97.8 |
| 2 | STRS | WEST | 69 | STRS_L_A | HARGRO_TWINBU1_1 (import, −0.92, 7) | 250.2 | 216.4 | 200.5 | 81.5 | 2028 | bay 38.5 | 88.6 |
| 3 | POWELLFI | WEST | 69 | POWF_L_A | HARGRO_TWINBU1_1 (import, −0.72, 8) | 200.7 | 165.6 | 150.9 | 67.6 | 2028 | bay 38.5 | 67.5 |
| 4 | KNAPP | WEST | 138 | EB2_KNAPP (KNAPP_RN) | 6437__F (import, −0.46, 0) | 98.1 | 54.0 | 48.3 | 59.2 | > 2031 | bay 56.5 | 39.6 |
| 5 | MILES | WEST | 69 | MILES_B114 | HARGRO_TWINBU1_1 (export, +0.50, 6) | 97.0 | 101.7 | 47.3 | 33.5 | 2028 | bay 38.5 | 24.7 |
| 6 | SHRNE | WEST | 138 | EXSHARNR_8 | 6437__F (import, −0.33, 2) | 85.9 | 46.8 | 36.2 | 50.8 | > 2031 | bay 56.5 | 31.0 |
| 7 | ATBR | WEST | 69 | ATBR_KP | HARGRO_TWINBU1_1 (export, +0.37, 7) | 84.8 | 86.7 | 35.1 | 30.5 | 2028 | bay 38.5 | 18.6 |
| 8 | SEGOVI | LCRA | 69 | L_SEGOVI9_1Y | FORTMA_YELWJC1_1 (import, −0.50, 3) | 79.5 | 43.2 | 32.2 | 39.7 | 2028 | bay 38.5 | 19.5 |
| 9 | JUPITER | NORTH | 138 | JUPITER_AG1 | COLLE_JUPIT_1 (import, −0.51, 0) | 79.4 | 61.6 | 37.9 | 63.1 | > 2031 | bay 56.5 | 41.2 |
| 10 | HILND | NORTH | 138 | HIGHLAND | 587__A (import, −0.55, 2) | 78.0 | 46.7 | 36.5 | 52.6 | > 2031 | bay 56.5 | 33.5 |
| 11 | CRNSO | NORTH | 138 | EB_CRNSOX2 | 587__A (import, −0.55, 2) | 78.0 | 47.3 | 36.5 | 52.7 | > 2031 | bay 56.5 | 33.6 |
| 12 | BULLCRK | WEST | 138 | BULLCRK_1381 | 6437__F (import, −0.26, 4) | 73.1 | 37.7 | 23.4 | 44.1 | > 2031 | switchyard 56.5 | 12.5 |
| 13 | BG | HOUSTON | 138 | BG_138A | DT_PK_91_A (import, −1.00, 3) | 72.3 | 64.5 | 25.1 | 57.8 | > 2031 | bay 56.5 | 35.1 |
| 14 | MID | HOUSTON | 138 | MID_138B | DT_PK_91_A (import, −1.00, 1) | 71.8 | 60.7 | 24.6 | 57.4 | > 2031 | bay 56.5 | 34.7 |
| 15 | PK | HOUSTON | 138 | PK_8060 | DT_PK_91_A (import, −1.00, 0) | 71.3 | 60.7 | 24.1 | 57.0 | > 2031 | bay 56.5 | 34.3 |
| 16 | GT | HOUSTON | 138 | GT_8065 | DT_PK_91_A (import, −1.00, 2) | 71.2 | 61.1 | 24.0 | 56.7 | > 2031 | bay 56.5 | 34.0 |
| 17 | MPPOI | WEST | 138 | MPPOI_K | 6437__F (import, −0.25, 4) | 71.2 | 35.5 | 21.4 | 42.5 | > 2031 | bay 56.5 | 21.4 |
| 18 | SCRCV | WEST | 138 | SCURYCHV_2 | 6437__F (export, +0.26, 0) | 70.9 | 66.4 | 21.2 | 48.2 | > 2031 | bay 56.5 | 29.4 |
| 19 | MASONSW | SOUTH | 69 | MASW_V_A | FORTMA_YELWJC1_1 (import, −0.51, 2) | 69.7 | 35.7 | 32.4 | 31.7 | 2029 | bay 38.5 | 11.2 |
| 20 | SHELLLON | SOUTH | 69 | SHELLLON_L_A | FORTMA_YELWJC1_1 (import, −0.50, 3) | 69.4 | 37.0 | 32.0 | 31.8 | 2029 | bay 38.5 | 11.3 |
| 21 | JUNCTION | SOUTH | 69 | JUNCTION_LD1 (JUNCTION_RN) | FORTMA_YELWJC1_1 (import, −0.50, 2) | 68.7 | 36.2 | 31.3 | 31.6 | 2029 | bay 38.5 | 11.0 |
| 22 | EOLA | WEST | 69 | EOLA_L_A | FORTMA_YELWJC1_1 (import, −0.50, 2) | 67.6 | 41.2 | 17.8 | 32.6 | 2028 | bay 38.5 | 12.7 |
| 23 | APOLLO | NORTH | 138 | EB_AP_0022 | COLLE_JUPIT_1 (import, −0.37, 1) | 66.2 | 48.9 | 24.7 | 50.3 | > 2031 | bay 56.5 | 28.3 |
| 24 | TANK | WEST | 69 | TANK_L_A | LAKENA_SAMATH1_1 (import, −0.61, n/a) | 64.7 | 22.4 | 15.0 | 45.2 | > 2031 | bay 38.5 | 21.4 |
| 25 | LPLNE | WEST | 115 | NE1EB | LPLMK_LPLNE_1 (import, −0.52, 0) | 62.1 | 26.2 | 12.4 | 45.8 | > 2031 | gen_tie 40.1 | 13.9 |
| 26 | LYONS | LCRA | 138 | L_LYONS_8_1Y | 215T215_1 (import, −0.71, 3) | 62.0 | 40.4 | 14.7 | 47.6 | > 2031 | bay 56.5 | 25.1 |
| 27 | BRENNO | LCRA | 138 | L_BRENNO8_1Y | 215T215_1 (import, −0.73, 0) | 61.5 | 36.6 | 14.2 | 48.3 | > 2031 | bay 56.5 | 25.5 |
| 28 | GAYHIL | LCRA | 138 | L_GAYHIL8_1Y | 215T215_1 (import, −0.71, 2) | 61.5 | 38.3 | 14.2 | 46.8 | > 2031 | bay 56.5 | 24.3 |
| 29 | SANDHI | LCRA | 138 | L_SANDHI8_1Y | 215T215_1 (import, −0.71, 1) | 61.2 | 35.5 | 13.9 | 45.9 | > 2031 | bay 56.5 | 23.5 |
| 30 | CHAPHI | LCRA | 138 | L_CHAPHI8_1Y | 215T215_1 (import, −0.63, 1) | 60.2 | 34.1 | 13.0 | 45.5 | > 2031 | bay 56.5 | 22.7 |
| 31 | LPLMK | WEST | 115 | MK8EB (MASSENGL_G8) | LPLMK_LPLNE_1 (export, +0.30, 0) | 56.3 | 41.1 | 6.6 | 38.5 | > 2031 | bay 38.5 | 16.7 |
| 32 | TMPTV | NORTH | 69 | TEMPTAYV1_9X | SEA_AAT1 (import, −0.36, 4) | 53.3 | 26.0 | 11.8 | 27.5 | 2029 | bay 38.5 | 6.8 |
| 33 | LPLNW | WEST | 115 | NW13 | LPLMK_LPLNE_1 (export, +0.19, 2) | 52.4 | 35.2 | 2.7 | 33.1 | > 2031 | gen_tie 39.6 | 6.9 |
| 34 | ROUGH | WEST | 138 | EB1_ROUGH | 6429__D (export, +0.19, 30) | 48.6 | 30.1 | −1.1 | 27.5 | 2028 | bay 56.5 | 5.8 |
| 35 | COLLEGE | NORTH | 138 | COLLEGE_AG1 | COL_BRAN_1 (import, −0.54, 0) | 48.3 | 30.4 | 6.8 | 29.6 | > 2031 | bay 56.5 | 8.0 |
| 36 | PGE | NORTH | 69 | POAGE | SEA_AAT1 (import, −0.42, 5) | 47.7 | 28.6 | 6.2 | 25.5 | 2029 | bay 38.5 | 4.9 |
| 37 | MILLERRD | NORTH | 138 | EB_0002X | COLLE_JUPIT_1 (export, +0.18, 3) | 45.4 | 23.9 | 3.9 | 28.0 | > 2031 | bay 56.5 | 5.6 |
| 38 | WYNJOYCE | NORTH | 138 | EB0001_WJ | COLLE_JUPIT_1 (export, +0.16, 3) | 44.8 | 22.2 | 3.3 | 27.6 | > 2031 | bay 56.5 | 5.0 |
| 39 | LYONS_C | NORTH | 138 | LYONS | COLLE_JUPIT_1 (export, +0.15, 5) | 44.8 | 22.3 | 3.3 | 27.3 | > 2031 | bay 56.5 | 4.8 |
| 40 | LWSVW | NORTH | 138 | TNLWVLWEST1S | 587__A (export, +0.16, 19) | 38.0 | 25.7 | −3.5 | 19.1 | 2028 | bay 56.5 | −1.6 |

## Appendix B. Words used here

- **Substation, bus, node.** A substation (`sub`) holds one or more electrical buses; a node is a bus at which a battery settles (a resource node is exactly one bus).
- **Constraint.** A limit on one line, transformer or group of lines ("monitored element") if another element is lost ("contingency"). When it binds, SCED puts a **shadow price** on it in $/MWh, and prices separate across the grid.
- **Shift factor (SF).** How much a $1/MWh shadow price on the constraint moves the price at a bus. Negative: the price rises (import side). Positive: it falls (export side).
- **Hop.** One line between two substations on the line graph.
- **Learner (A5), Forecaster (A2), Spike hunter (A4), Clockwork (A1), Weather-regime (A3).** The arena's five dispatch algorithms; the oracle sees the future and is the ceiling.
- **Gain / uplift.** A node's value minus the same battery's value at its load-zone price.
- **TSP, RPG, CCN, TPIT.** Transmission service provider; Regional Planning Group; certificate of convenience and necessity; the transmission project tracker.
- **DESR, ADER, WSL.** A distribution-connected storage resource that settles at a node; an aggregation of home batteries that settles at the load zone; the separately metered charging side of a storage resource.
- **GTC, IMM, RTC+B, 4CP.** A generic transmission constraint (a stability interface); the independent market monitor; real-time co-optimisation plus batteries; the four-coincident-peak transmission charge.
- **Anchor.** The annual figure a replayed constraint's synthetic shadow prices are scaled to (shadow-price-hours, $ per MW of limit a year). Revision 1 used the 2025 market-monitor rent where the element had one, else a persistence-discounted window figure; revision 2 uses the 2026 year-to-date real-time rent (annualised) where the element is in the 2026 lists.
- **Oracle-ratio.** A year's learner value estimated as the perfect-foresight value on the rebuilt lane times the lane's own learner-to-oracle ratio of 2023–25. Used for the forward years and for the sensitivity runs; not a dispatch run.
- **Pole scale.** A shift factor normalised so that it is 1 at the pattern's strongest stations; the shadow price is then the price at the pole, not the constraint's own shadow price (R1's patterns, section 7).
- **Measured basis.** The day-ahead price at a node minus the price of its load zone (or the system reference HB_BUSAVG), from ERCOT's 60-day disclosure, 1 Jul–11 Aug 2026. Real prices, one summer window.
