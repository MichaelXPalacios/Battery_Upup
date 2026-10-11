# Where to put batteries on the Texas grid

*Batter Up research, ERCOT physical-grid siting study. Built 10–11 Oct 2026 from public data only. Brief: [PROMPT.md](PROMPT.md); contracts: [PLAN.md](PLAN.md); map: [map/index.html](map/index.html). Phases 1–5 (market data, planning documents, geography, network and shift factors, site value, upgrades, reliability and Tier 2) are written up in [notes/](notes/); this page is Phase 6.*

**How to read the numbers.** Every number carries one of four labels. **REAL** is copied from a named public source. **DERIVED** is computed from real numbers. **SIM** is a model output. **ASSUMPTION** is our choice, a named constant in the code. In tables the label sits in the column header; in text it follows the number in brackets. Dollars are **$/kW-yr** of a 1 MW battery, energy only, after a $20/MWh wear cost, dispatched by the arena's Adaptive learner (A5) unless stated. "2 h" is a 1 MW / 2 MWh grid battery, "4 h" a 1 MW / 4 MWh one. Market value and reliability value are kept in separate columns and never added.

**Node prices are a model.** ERCOT publishes one week of real-time nodal prices and 31 days of day-ahead ones without an API key, so the price at a substation over 2019–2026 is rebuilt from the real zone price plus a replayed congestion structure. Everything about a node's price is SIM: levels are good to about a factor of two, rankings are steadier (section 8).

## TL;DR

1. **Siting is worth about twice the zone price, and it sits in a few small pockets.** A 2 h battery at one of the 40 congested substations earns $78.3/kW-yr against $44.4 at the eight load-zone prices (2021–25 mean) and $54.6 against $17.8 in 2025 [SIM]; 37 of the 40 beat their zone. The top five are MIDW $269.7, STRS $249.5, POWELLFI $201.5, KNAPP $98.2 and MILES $96.9 [SIM]. The first three sit 7–8 hops behind Twin Buttes–Hargrove 138 kV in West Texas, and the 40 sites sit behind 11 constraints.
2. **It is a first-10-MW result.** Nodes behind one line share its congestion, so each constraint's group is capped at 10% of its limit [ASSUMPTION]. That leaves room for about 200 MW of the first 1,000 MW at the nodes. With the caps, siting adds $3–8/kW-yr to a 1,000 MW fleet's average (the learner rule earns $34.9 against $27.8 at the zones only), not the $20–74 an uncapped "best site" ranking promises [SIM].
3. **Electrical distance decides, not hop count.** A substation's shift factor (how many dollars its price moves per dollar of congestion on a line) is only loosely related to how many lines away it is: over 95,948 substation–line pairs the rank correlation is −0.41, and 47% of the pairs with a shift factor of 0.10 or more are 8 or more hops away [DERIVED]. MIDW is 8 hops from its line and has the largest shift factor in the study (−1.00). SCRCV, at the end of Scurry Chevron–Knapp (0 hops), is on the export side (+0.26) and earns $69.3 against $98.2 at KNAPP, the other end [SIM].
4. **Wires beat batteries on cost, and only the transmission owner can make them happen.** A battery that relieves 10% of a line's limit in 80% of its binding hours costs a median 21 times the wires fix, and 192 times the cheapest rating change [DERIVED]. The transmission owner (the TSP) files an upgrade through ERCOT's Regional Planning Group, plus a state permit (a CCN) for a new line; a battery owner can propose one and pay its own interconnection, not order one. Revalued with the full dispatch model, reconductoring Twin Buttes–Hargrove takes MIDW from $247.7 to $51.0 and the 40-site total down 31% [SIM]. Phase 4's first-order estimate ranks the 40 sites the same way (rank correlation 0.89) but understates MIDW's loss by 31% and misses second exposures. Every planned upgrade in service by 2031 moves no candidate by more than $2.1 [SIM]; the one dated project that hits candidates is Argyle–Lewisville in Dec 2032 (−$34 to −$40 at HILND, CRNSO and CRNTH).
5. **The premium fades as storage arrives.** With 26 GW of effective storage online by 2031 (41 GW listed; 60% of unbuilt MW realised [ASSUMPTION]) the mean gain over the zone falls from $38.4 (2025 replay) to $33.1 (2027), $24.6 (2028) and $19.3 (2029–31) [SIM]. The five Twin Buttes sites drop below half of their 2025 gain by 2028, but that rests on 15 substations with a shift factor of about −0.065 and is the most fragile result here.
6. **At a node the Forecaster beats the learner, and ancillary services no longer matter much.** A2 earns more than A5 at 36 of the 40 nodes ($81.0 against $78.3); the Spike hunter gains most from siting (×2.04; $225 to $445 per MWh) yet still earns $18 less than the learner [SIM]. Since RTC+B went live on 5 Dec 2025 ancillary prices have collapsed (day-ahead Reg Up $26.79/MW-h in 2023, $2.11 in 2026 to 10 Oct [REAL]), so the ancillary line is $15.9/kW-yr in 2025 and $11.9 in 2026 for a 2 h battery [SIM], the same at any site.
7. **Reliability, distribution batteries and the dispatch platform.** Only four of the 40 sites, the Houston PK–DT substations (MID #10, BG #11, GT #62, PK #64), are also in the top 100 by reliability need [ASSUMPTION weights]; the Twin Buttes pocket is a market case only. A distribution battery that registers as a DESR is paid the node price (+$27.0/kW-yr on average at 172 valued Tier 2 candidates, +$38.8 for the 76 within 150 km of the line they depend on, falling to +$13.0 by 2029); a home-battery fleet (ADER) is paid the zone price and gets none of it [SIM]. `data/sites.csv` is the ordered queue of settlement points for the dispatch platform in [`dispatch/`](../../../dispatch/README.md). An ERCOT API key would replace the rebuilt prices at the top sites with real ones.

---

## 1. Where to put batteries

### 1.1 The 15 best transmission sites

Transmission-connected, 69–345 kV, ranked by the 2 h learner's 2021–25 mean. All 40 candidates are in Appendix A and in [`data/sites.csv`](data/sites.csv) (150 columns, one label per column in `sites.labels.csv`). "Gain" is the site minus the same battery at its load-zone price. "SF" is the shift factor (section 2.3): negative means the **import side**, where the price rises when the line binds and a battery sells; positive is the **export side**, where it falls and a battery charges.

| # | Sub (kV) | County, zone | Constraint: side, SF, hops [DERIVED] | 2 h learner 2021–25 / 2025 [SIM] | 4 h learner 2024–25 [SIM] | Gain over zone [SIM] | Basis half-life [SIM] | Connection, lead time [DERIVED] | Risk flags [DERIVED] | Reliability rank [ASSUMPTION] |
|---|---|---|---|---|---|---|---|---|---|---|
| 1 | MIDW (69) | Crockett, WEST | HARGRO_TWINBU1_1 (import, −1.00, 8) | 269.7 / 237.6 | 339.3 | 220.0 | 2028 | new bay, 38.5 mo | flood unmapped, wildfire moderate | – |
| 2 | STRS (69) | Crockett, WEST | HARGRO_TWINBU1_1 (import, −0.92, 7) | 249.5 / 214.7 | 311.6 | 199.7 | 2028 | new bay, 38.5 mo | flood unmapped, wildfire moderate | – |
| 3 | POWELLFI (69) | Crockett, WEST | HARGRO_TWINBU1_1 (import, −0.72, 8) | 201.5 / 165.7 | 242.6 | 151.8 | 2028 | new bay, 38.5 mo | flood unmapped | – |
| 4 | KNAPP (138) | Scurry, WEST | 6437__F (import, −0.46, 0) | 98.2 / 53.9 | 111.4 | 48.5 | > 2031 | new bay, 56.5 mo | flood unmapped, seismic | 2,401 |
| 5 | MILES (69) | Runnels, WEST | HARGRO_TWINBU1_1 (export, +0.50, 6) | 96.9 / 101.4 | 123.6 | 47.2 | 2028 | new bay, 38.5 mo | flood unmapped | 2,051 |
| 6 | SHRNE (138) | Scurry, WEST | 6437__F (import, −0.33, 2) | 89.7 / 47.3 | 99.1 | 40.0 | > 2031 | new bay, 56.5 mo | flood unmapped, wildfire high | 2,436 |
| 7 | NOVICE (69) | Coleman, WEST | HARGRO_TWINBU1_1 (export, +0.36, 5) | 83.0 / 83.7 | 109.0 | 33.2 | 2028 | new bay, 38.5 mo | flood unmapped | 2,451 |
| 8 | SEGOVI (69) | Kimble, LCRA | FORTMA_YELWJC1_1 (import, −0.50, 3) | 79.0 / 42.6 | 85.7 | 31.8 | 2028 | new bay, 38.5 mo | flood unmapped | – |
| 9 | HILND (138) | Denton, NORTH | 587__A (import, −0.55, 2) | 78.6 / 53.2 | 86.4 | 37.1 | > 2031 | new bay, 56.5 mo | none | 619 |
| 10 | CRNSO (138) | Denton, NORTH | 587__A (import, −0.55, 2) | 78.6 / 53.6 | 86.2 | 37.1 | > 2031 | new bay, 56.5 mo | none | 1,244 |
| 11 | BULLCRK (138) | Borden, WEST | 6437__F (import, −0.26, 4) | 74.7 / 38.6 | 80.6 | 25.0 | > 2031 | new switchyard, 56.5 mo | flood unmapped, wildfire high | 371 |
| 12 | BG (138) | Harris, HOUSTON | DT_PK_91_A (import, −1.00, 3) | 74.0 / 62.6 | 80.1 | 26.8 | > 2031 | new bay, 56.5 mo | none | **11** |
| 13 | GT (138) | Harris, HOUSTON | DT_PK_91_A (import, −1.00, 2) | 72.8 / 60.8 | 77.1 | 25.7 | > 2031 | new bay, 56.5 mo | none | **62** |
| 14 | CRNTH (138) | Denton, NORTH | 587__A (import, −0.47, 2) | 72.5 / 48.3 | 76.4 | 31.0 | > 2031 | new bay, 56.5 mo | none | 733 |
| 15 | PK (138) | Harris, HOUSTON | DT_PK_91_A (import, −1.00, 0) | 72.3 / 63.4 | 72.3 | 25.1 | > 2031 | new bay, 56.5 mo | none | **64** |

Units: values $/kW-yr; hop counts are graph edges to the nearest end of the monitored element; lead time is months from a request today to energisation (a 69 kV site needs a 38.5-month medium-size step-up transformer, a 138 kV site a large power transformer, 56.5 months). "Reliability rank" is the rank among 2,554 substations by need (1 = most need); – means no rank: the county's outage data is unusable (reports exceed its customers), so the substation has a need score from the other components (MIDW, STRS and POWELLFI 5.6 of 100, SEGOVI 35.5) but is not ranked. A half-life of 2028 means the site's gain falls below half of its 2025 value by 2028; "> 2031" means it does not by 2031.

**What the top of the list is.** Eleven constraints sit behind the 40 candidates (limits and 2025 rents are the replayed values, SIM):

| Element | Limit MW [REAL] | Replayed 2025 rent $M / binding hours [SIM] | Nodes behind it | Mean learner / gain [SIM] |
|---|---|---|---|---|
| Twin Buttes–Hargrove 138 kV | 151 | 137.0 / 945 | MIDW, STRS, POWELLFI (import); MILES, NOVICE (export) | 180.1 / 130.4 |
| Scurry Chevron–Knapp 138 kV | 214 | 68.3 / 1,099 | KNAPP, SHRNE, BULLCRK, MPPOI (import); SCRCV (export) | 80.8 / 31.1 |
| Fort Mason–Yellow Jacket 138 kV | 145 | 22.9 / 328 | SEGOVI, SHELLLON, JUNCTION, MASONSW, EOLA | 70.4 / 28.6 |
| Argyle–Lewisville 138 kV | 216 | 51.0 / 431 | HILND, CRNSO, CRNTH (import); LWSVW (export) | 67.6 / 26.1 |
| PK–DT 138 kV (Houston) | 293 | 50.8 / 543 | BG, GT, PK, MID | 72.8 / 25.6 |
| Lakena–Samathis 69 kV | 38 | 5.9 / 862 | TANK | 64.8 / 15.0 |
| HIGH36–Brenno 138 kV | 238 | 26.0 / 575 | BRENNO, SANDHI, GAYHIL, LYONS, CHAPHI | 60.8 / 13.6 |
| SEA 138/69 kV transformer | 99 | 9.5 / 438 | SEA, TMPTV, PGE | 51.6 / 10.1 |
| College–Jupiter 138 kV | 272 | 35.7 / 290 | APOLLO (import); MILLERRD, WYNJOYCE, LYONS_C, CENTRVIL (export) | 49.3 / 7.8 |
| Brand–College 138 kV | 272 | 28.3 / 213 | COLLEGE | 49.2 / 7.7 |
| Encrt–Brand 138 kV | 410 | 47.9 / 595 | ECRSW, ROUGH (export) | 47.6 / −2.2 |

The 30 import-side nodes gain +$40.1 over their zone on average and the 10 export-side nodes +$10.9 [SIM]; 14 nodes at 69 kV earn $105.2 against $63.9 for 26 nodes at 138 kV [SIM]. The gain is almost the same every year ($29–38), so siting matters most when the zone itself earns little: 3.1 times the zone in 2025 ($54.6 against $17.8) and 1.8 times over 2021–25 [SIM]. Three sites do not beat their zone (ECRSW, ROUGH, LWSVW), all export-side and far from their line.

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
| SEA_AAT1 | 99 | TMPTV, PGE, SEA | 10 |
| FORTMA_YELWJC1_1 | 145 | EOLA, SEGOVI, SHELLLON, JUNCTION, MASONSW | 10 |
| HARGRO_TWINBU1_1 | 151 | MIDW, STRS, MILES, POWELLFI, NOVICE | 10 |
| 6437__F | 214 | KNAPP, SCRCV, SHRNE, BULLCRK, MPPOI | 20 |
| 587__A | 216 | HILND, CRNSO, CRNTH, LWSVW | 20 |
| 215T215_1 | 238 | SANDHI, BRENNO, GAYHIL, LYONS, CHAPHI | 20 |
| COL_BRAN_1 | 272 | COLLEGE | 20 |
| COLLE_JUPIT_1 | 272 | APOLLO, MILLERRD, WYNJOYCE, LYONS_C, CENTRVIL | 20 |
| DT_PK_91_A | 293 | BG, PK, MID, GT | 20 |
| 6429__D | 410 | ECRSW, ROUGH | 40 |
| **Total** | | | **200** |

**Group caps leave about 200 MW of high-value nodal room out of 1,000 MW** (2027–31, 200 MW a year). The rest of the fleet falls back to the zone price. Result by siting rule, $/kW-yr of the whole fleet [SIM]:

| Rule | With the caps | MW at nodes / at zones | No caps at all | Zones only |
|---|---|---|---|---|
| A1 Clockwork | 28.5 | 160 / 840 | 45.3 | 25.2 |
| A2 Forecaster | 33.9 | 200 / 800 | 100.0 | 25.6 |
| A3 Weather-regime | 28.6 | 200 / 800 | 67.6 | 22.4 |
| A4 Spike hunter | 23.9 | 200 / 800 | 80.8 | 16.9 |
| **A5 Adaptive learner** | **34.9** | 200 / 800 | 101.3 | 27.8 |

With the caps, siting at the nodes adds $3–8/kW-yr to the fleet average (A1 +3.3, A2 +8.3, A3 +6.2, A4 +7.0, A5 +7.2). Without caps every rule puts all 1,000 MW on one to three nodes (the learner rule: all of it at MIDW, $101.3), which is the number a naive "best site" ranking gives and is wrong by a factor of three. The learner and Forecaster rules come first and second in all three columns. The learner rule's 2027 placement is MIDW 10 MW, KNAPP 20, HILND 20, BG 20, LYONS 20, APOLLO 20, COLLEGE 20, SEGOVI 10, SEA 10, TANK 10 and ECRSW 40: 200 MW at eleven nodes.

### 1.4 Net of connection

Connection is $192–194/kW for 39 sites (one new bay each; step-up transformer included) and $287/kW for BULLCRK (a new switchyard) [DERIVED]; the step-up is 88% of it and is an unverified proxy ($16.95M per 100 MW). At 8% over 15 years that is $22.5/kW-yr. Over the 40 sites the median value after connection is **$21.9/kW-yr** and the median break-even installed cost **$389/kW** (a zone price alone carries $197/kW) [SIM, ASSUMPTION rates]; MIDW, STRS and POWELLFI reach $1,026, $946 and $769/kW for their first 10 MW. Ancillary revenue, capacity payments and O&M are not in these numbers; for most sites the energy value alone does not carry a typical installed cost.

---

## 2. The physical-constraints map

The brief lists 32 physical constraints. For each: what public data showed, how it caps or raises battery value, and how it is modelled (or why it cannot be). "Not public" usually means CEII, ERCOT's term for critical energy infrastructure information.

### 2.1 All 32 items

| # | Item | What the data showed | Effect on battery value | How it is modelled, or why not |
|---|---|---|---|---|
| 1 | Line thermal limits (normal, emergency, 15-min, ambient-adjusted, dynamic) | SCED reports the limit it used each run [REAL]: Scurry Chevron–Knapp swings 17% across a day, 238 MW early morning to 201 MW from 16:00–19:00 [DERIVED]. Emergency and 15-minute ratings are not public. Dynamic ratings cost $23–60k per mile and lift a line 10–15% [REAL, DOE 2019] | The limit sets when and how hard a line binds. A rating change is the cheap rival to a battery: it is 192 times cheaper at the median [DERIVED] | Median SCED limit per element and its daily swing in `upgrades.csv`; dynamic ratings are an option in the wires table. Normal / emergency / 15-minute split not modelled (no data) |
| 2 | 345/138 kV autotransformers | 231 of the 1,386 constraints are transformers [DERIVED]; 230 bind in the data window, e.g. SEA 138/69 kV, SGVSW and BLESSING 345/138 kV | A transformer-limited pocket is small and sharp (SEA, TMPTV, PGE earn $50–53 [SIM]) | Treated like any element: shift factors fitted from prices. No physics proxy for transformers (collapsed in the graph). Upgrade option: next standard autotransformer at $6,880/MVA (345/138) or $5,050/MVA (138/69), 36–69 months [DERIVED] |
| 3 | GTCs and stability interfaces | 14 GTCs named (WESTEX, Panhandle, Hamilton, McCamey, North-to-Houston, Valley, Far West...); element lists are CEII. WESTEX earned $89.1M of 2025 rent [REAL] | The largest rents are diffuse (many nodes, small shift factors), so the per-MW pocket premium is small | Fitted shift factors where an interface bound. WESTEX only has an attenuated day-ahead fit (sign only), so it is **not replayed** and no WESTEX site is a candidate. Planned fix: the 765 kV import paths, $9.06B, 2031 |
| 4 | Contingency pairs (N-1, N-1-1, beyond) | A binding constraint is "this element if that one is lost": 1,386 constraints, 12 for Scurry Chevron–Knapp, 6 for Twin Buttes–Hargrove. Contingency names are ERCOT codes, not decoded. N-1-1 lists are not public. Uri (Feb 2021), the Houston derecho (May 2024) and Beryl (Jul 2024) are in the outage data | Shift factors are post-contingency; relieving an element relieves all of its contingencies | Shift factors and shadow prices per constraint; `apply_upgrade` scales every contingency of an element. The three named events enter reliability (unserved energy), not congestion |
| 5 | Voltage stability and reactive limits (Valley, Houston import, Far West) | Valley, Houston and Far West limits change by market notice and are not public; the 2025 plan retired the Valley interfaces because of the Lower Rio Grande Valley project (before summer 2027) [REAL] | Binds as interface shadow prices, not as a line: price separation without a thermal line to fix | Only through observed shadow prices of interfaces that bound. No Valley candidate exists (no usable regression shift factor) |
| 6 | Low system strength; ride-through and grid-forming rules | ERCOT manages it by curtailing inverter output and adding synchronous condensers (six in West Texas, in service 2027). Agreements signed on or after 1 Apr 2026 need grid-forming capability (NOG 2.14) and a series-capacitor screen (PG 5.2.10). No numeric short-circuit-ratio minimum is public [UNVERIFIED] | A weak-grid site in the Far West or Panhandle sees lower interface limits and curtailment; a grid-forming battery can raise them through the study | Not modelled. No candidate sits in the Far West or Panhandle (those constraints cannot be replayed) |
| 7 | Remedial action schemes and mitigation plans | ESRs can be the objects of a scheme (NOG 11.2(g)); a scheme cannot be planned as a fix unless the need ends within five years (PG 4.1.1.2). Element lists are not public | A scheme can trip or limit a battery in a pocket, or stand in for an upgrade | Not modelled. The curtailment flag (export side, SF at least 0.05) marks 6,881 substation–constraint pairs where injecting loads the line |
| 8 | Planned and forced outages; maintenance seasons | The element-level outage list is in the MIS secure area; only monthly counts are public (NOGRR267). Our one real-time week (3–10 Oct) is a shoulder, outage-driven week: Scurry Chevron–Knapp's window rent annualises to 6.3 times its 2025 rent [DERIVED] | Short-lived pockets overstate a year if annualised | A persistence factor of 0.15 [ASSUMPTION] discounts window-anchored constraints; the biggest ones are anchored on 2025 market-monitor rents instead. No outage calendar |
| 9 | DC ties and Mexico | Laredo VFT North–Las Cruces earned $60.7M in 2025 over 1,053 hours [REAL]. Railroad, Eagle Pass, East and North are not edges in the line graph | Congestion beside a tie can be large but needs a tie model | No regression shift factor exists, so nothing is replayed there; sized with an assumed class shift factor in the upgrades table |
| 10 | Radial load pockets | 618 of 3,505 graph-linked substations (18%) sit behind a bridge in the line graph, 472 after a HIFLD cross-check [DERIVED]; West Texas has the highest share. Largest pockets 7–8 substations | Islanding and load-shed exposure: reliability value, not market value | Pocket sub-score (weight 0.25 [ASSUMPTION]) in the reliability table |
| 11 | Power-transformer MVA and firm capacity | Not public per substation (NP4-160 has none; the network model is CEII) | Caps how much a substation can import or export; reverse flow | Step-up transformer at 110% of battery MW and $16.95M per 100 MW [UNVERIFIED proxy]. Firm capacity not modelled. Tier 2 uses the service-transformer nameplate rule |
| 12 | Free bays, breaker positions, bus configuration, room | Not public | No free bay means a new switchyard or gen-tie: more dollars and months | Connection class: new bay if the station is a through station with two or more line terminations in the line graph, else switchyard or gen-tie; 43% of 5,033 stations take a default class (confidence 0.3), which assumes a free position |
| 13 | Breaker duty, protection coordination, transfer trip | Not public. A transmission POI needs breakers that can interrupt fault current and be controlled remotely (PG 5.2.11; effective date unverified) | Can add cost and months | Not modelled. Tier 2: fault-current screen assumed not binding [ASSUMPTION] |
| 14 | Equipment age, spares, transformer lead time and cost | Large power transformers take 30–60 months, bushings up to 130 weeks [REAL, DOE 2024, NLR 2026]; prices up about 77% since 2019 [secondary, UNVERIFIED] | The step-up transformer sets the schedule: 56.5 months at 138 kV and above, 38.5 below [DERIVED] | Lead time = study 20.5 months + max(build, transformer delivery 30 months + 6 to install). Age is not public |
| 15 | Gen-tie distance and right of way | 227 of 5,033 stations of 69 kV or more would need a gen-tie: median 4.8 km, 90th percentile 18.2 km, longest 82 km [DERIVED] | Adds cost beyond about 8.6 km and a CCN for a long tie | Nearest through station × 1.25 routing, MISO line costs. None of the 40 candidates needs one |
| 16 | Feeder hosting capacity (thermal, voltage, protection, reverse flow, regulators) | No Texas utility publishes it (Oncor argued against a hosting-capacity map in PUCT 58306) | Caps a distribution battery | Defaults [ASSUMPTION]: 4 feeders × 1,050 customers × 5 kW. Oncor's feeder counts for 344 candidates [REAL]. Thermal, voltage, protection and reverse flow not evaluated; a five-step scale-up from a real feeder model is in `notes/phase5.md` |
| 17 | Service-transformer kVA | The repo's transformer logic (`sim/planner.py`) | Sets homes per transformer | Batteries per 25 / 50 / 75 kVA unit: 1, 2, 3 under both screens [DERIVED] |
| 18 | Texas screens: 16 TAC §25.211 and Austin Energy 90% | A pre-certified unit of 500 kW or less exporting at most 15% of feeder load and 25% of fault current pays no study fee; Austin Energy: DG at most 90% of transformer rating [REAL] | A fast path, not a refusal; larger needs a study | Applied as screens in `tier2.csv` (median fast-path cap 3.15 MW per substation at the defaults) |
| 19 | Feeder ties, switching, overhead versus underground | Not public | Alternative supply and outage duration | Not modelled |
| 20 | Where each connection type settles | Transmission ESR: its resource node. DESR: a resource node at one bus. Settlement-only storage of 10 MW or less: load zone while storage is over 50% of the site (nodal after NPRR995). Separately metered storage load: nodal at the bus. ADER fleets: load-zone price (`notes/rules.md`, NP §6.6, ADER Governing Document 3.3) [REAL] | Decides whether congestion value is paid to the battery at all | DESR = node price; ADER = zone control (section 6). Which bus a feeder maps to is in registration data and not public [UNVERIFIED] |
| 21 | Basis between node, hub, zone; day-ahead versus real time; CRRs | In the real week the mean node-minus-zone gap is $8.20/MWh over 1,043 nodes (median 5.68, 90th percentile 15.99) [REAL]. Real-time and day-ahead shift factors correlate at a median 0.64 | The basis is the whole premium. A congestion revenue right from pocket to hub could hedge it | Rebuilt lanes reproduce the real node basis with correlation 0.994 and a mean error of $1.85/MWh when fed the published shadow prices [SIM]. Real-time lanes only; CRR auction prices not in the data |
| 22 | RTC+B (live 5 Dec 2025): ancillary prices and state of charge | Energy and ancillary services are bought together every 5 minutes; awards are bound by state of charge (NP 6.5.7.3). Durations: Reg and RRS 30 min, ECRS 1 h, Non-Spin 4 h [Non-Spin UNVERIFIED]. Day-ahead Reg Up fell from $26.79 (2023) to $2.11/MW-h (2026) [REAL]; real-time prices are lower still | Ancillary services are no longer a second business | Separate layer: $15.9/kW-yr (2025) and $11.9 (2026), upper bound $31, 2 h [SIM]; never added to energy |
| 23 | Charging: energy and 4CP | Separately metered storage pays the nodal energy price and no 4CP charge (16 TAC §25.192, §25.501(m)(2)) [REAL]. SB 6 orders a review of 4CP by 31 Dec 2026 | The exemption is worth money on the charging side | Charging at the lane's own price, no 4CP term. Do not rely on the exemption past 2026 |
| 24 | Saturation; large loads | 41.2 GW of storage is listed behind constraints (4.1 GW operating, 34.4 GW of queue with agreements, 2.7 GW firm planned) [REAL/DERIVED]. Large loads (Batch Zero: 66.4 GW base load, 127.9 GW studied) move congestion | Storage behind a line erodes its shadow price: the premium halves by 2029 | Per-constraint scale factor from effective MW × shift factor (60% of unbuilt MW realised [ASSUMPTION]). Large loads only enter the reliability score (county data; most counties unknown) |
| 25 | Price-taker limit | A 100 MW battery at MIDW offsets 66% of the Twin Buttes limit [DERIVED] | The first-MW price is not the 100th-MW price | Group caps of 10% of the limit [ASSUMPTION]: 200 MW of room for 1,000 MW; per-node caps by voltage |
| 26 | Interconnection process and cost | For agreements signed after 31 Dec 2025 the generator pays interconnection facilities above $14M (138 kV and below) or $20M (above) and the TSP pays network upgrades (16 TAC §25.195(f)); the risk left is curtailment. Median 20.5 months from study request to agreement, 35.6 months to commercial operation [REAL] | Cost and delay, not a bill for upgrades | `connection_costs.csv`: class, cost range, owner share, lead time for 5,033 stations |
| 27 | Flood, hurricane, wildfire, extreme cold and heat | Of 2,869 site-assessed substations 147 (5.1%) are excluded: 129 in a FEMA flood hazard area, 11 in a floodway, 7 at very high wildfire hazard [DERIVED]. 80% of West Texas has no digital flood polygon. None of the 40 is excluded; SHRNE, BULLCRK and ECRSW sit at high wildfire hazard | Exclusion, or a mitigation cost | Flags and exclusion in `risk_flags.csv`. Heat and cold derating (HVAC, line sag) not modelled; the days at or above 100 F and at or below 20 F are carried as a coarse marker |
| 28 | Land: parcel, slope, roads, easements, ordinances, NFPA 855 setbacks | No public data at this scale | A site that cannot be built | Not modelled; the new-bay assumption presumes room |
| 29 | Telemetry path; environmental and cultural review | Not public. ERCOT requires real-time telemetry of state of charge and limits (NP 6.5.5.2) | Time and cost | Not modelled |
| 30 | Induced seismicity (Permian) | Magnitude 3 or larger events since 2015 within 25 km flag 453 substations, in the Delaware Basin, Midland, Scurry and also the Irving–Azle cluster [REAL, USGS] | A risk flag | Flag only, never excludes. KNAPP, SCRCV, APOLLO and ECRSW are flagged |
| 31 | Load-shed exposure | Radial pockets (item 10); outage history by county (EAGLE-I: 254 counties, 215 usable); Oncor SAIFI/SAIDI for 3,279 feeders; OSM hospitals (735), water works (1,238) and emergency stations (2,567). Under-frequency load-shed feeders are not public | Reliability value to customers | Five sub-scores combined into a need score 0–100 [ASSUMPTION weights]; under-frequency load shed not modelled |
| 32 | Backup value | A 1 MW / 4 MWh battery on a 1 MW critical load would have avoided 3.7 MWh a year of unserved energy in the median Texas county (Harris 1.1, Bexar 0.5) [DERIVED]; Beryl dominates in Harris (3.3 MWh) | At $35,685/MWh [REAL, Brattle 2024] that is about $131k a battery-year ($74k duration-adjusted) in the median county: customer value, not revenue | Kept apart from market value; bounded above (full charge, always-present load) and below (small events omitted) |

### 2.2 Your three examples

**Transformers to upgrade.** Transformers are 231 of 1,386 binding-constraint rows [DERIVED]. Three are in the upgrade table: SEA 138/69 kV (99 MW; $4.2M, 36–69 months; a battery for 10% relief costs 5.6 times as much), La Palma 138 kV (630 MW; $11.8M; 25 times) and SGVSW 345/138 kV (746 MW; $11.8M; 27 times) [DERIVED]. Lead time is the transformer: 36 months commonly quoted, up to 60 [REAL, DOE 2024]. For siting, the SEA pocket is the one that matters: SEA, TMPTV and PGE earn $52.9, $52.1 and $49.9 (rank 30–32 of 40), and TMPTV and PGE lose half their gain by 2029 [SIM].

**Lines to upgrade.** Ten are in the table in section 3: the lines that matter most to siting are Twin Buttes–Hargrove (−31% of the 40-site total if reconductored), PK–DT (−5.6%), Scurry Chevron–Knapp (−4.7%) and HIGH36–Brenno (−3.6%) [SIM]; four of the ten are not in the replayed structure, so the full valuation cannot move any site for them. Cost, lead time and what a battery would take instead are in section 3.

**Substations several hops from a cap.** This is the sweet spot of the brief, and the data show it: MIDW (8 hops), POWELLFI (8) and STRS (7) are the most valuable sites we found, because their electrical distance to Twin Buttes–Hargrove is short (SF −1.00, −0.72, −0.92), whatever the line count. Section 2.3 explains why.

### 2.3 Hops versus shift factor: electrical distance decides

For every placed substation the study computes two distances to each frequently binding element: the **hop count** on the line graph (substation to nearest end of the monitored element) and the **shift factor** (SF), the estimated electrical distance: how many dollars a $1/MWh shadow price on the element moves the price at the substation. SF is fitted from real prices (median out-of-sample R² 0.978 in real time, 0.756 day-ahead [DERIVED]); hops come from an OSM/HIFLD line graph with assumed reactances.

- **Hops explain little.** Over 95,948 substation–constraint pairs the rank correlation of hops with |SF| is −0.41 (−0.43 for the real-time fits alone, −0.22 for reactance distance) [DERIVED]. Of the 3,803 pairs with |SF| of 0.10 or more, 1,787 (47%) are 8 or more hops from the element (40% for the real-time fits) [DERIVED]. Among pairs within 3 hops, 34% have |SF| below 0.05: nearby but electrically weak.
- **Far can be strong.** MIDW is 8 hops from Twin Buttes–Hargrove with SF −1.00 and a $269.7 learner value; POWELLFI is 8 hops with −0.72 ($201.5); STRS 7 hops with −0.92 ($249.5) [SIM]. On the Houston PK–DT line, BG at 3 hops has the same SF (−1.00) as PK at 0 hops, and earns $74.0 against $72.3 [SIM].
- **Near can be wrong-sided.** SCRCV is at the end of Scurry Chevron–Knapp (0 hops) but on the export side (+0.26, curtailment flag): it earns $69.3 against KNAPP's $98.2 at the import end, and loses less if the line is fixed ($18.1 against $32.6) [SIM].
- **On a radial chain, SF decays with hops.** On Scurry Chevron–Knapp: KNAPP 0 hops (−0.46, $98.2), SHRNE 2 (−0.33, $89.7), BULLCRK 4 (−0.26, $74.7), MPPOI 4 (−0.25, $72.0) [SIM]. Where the graph is a chain, hops are a fair proxy; where it is meshed they are not.
- **Far and weak earns nothing extra.** ECRSW (16 hops, +0.28) and ROUGH (30 hops, +0.19) are export-side nodes that earn about their zone price (gain −$1.5 and −$2.9) [SIM].
- **Across the 40 candidates** the gain over the zone correlates with |SF| at +0.63 and with hops at −0.38 (33 sites with a hop count; descriptive only, since the candidates were selected on SF) [DERIVED].

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
| U05 | HIGH36–BRENNO 138 kV | reconductor | 4.9 mi, 241 MW | 2.3 / **2.9** / 6.5 | 26 / **48** / 61 | none found | 35 MW / 319 MWh (9.1 h) | 28.2× | – |
| U06 | PK–DT 138 kV | reconductor | 0.8 mi, 293 MW | 1.1 / **1.3** / 2.3 | 26 / **48** / 61 | hint: TPIT-64499, Dec 2026 | 62 MW / 337 MWh (5.4 h) | 72.3× | – |
| U07 | LNGSW–CONSW 345 kV | rebuild | 66.8 mi, 1,197 MW | 178 / **222.5** / 334 | 34 / **52** / 75 | yes: TPIT-81268, May 2028 | 311 MW / 973 MWh (3.1 h) | 1.4× | $94.6M |
| U08 | BELCNTY–SALSW 138 kV | rebuild | 7.8 mi, 237 MW | 12.0 / **15.1** / 22.6 | 26 / **48** / 61 | yes: TPIT-87673, May 2027 | 45 MW / 269 MWh (6.0 h) | 5.0× | $94.2M |
| U09 | ENCRT–BRAND 138 kV | reconductor | 4.9 mi, 410 MW | 2.3 / **2.9** / 6.5 | 26 / **48** / 61 | none found | 74 MW / 564 MWh (7.6 h) | 51.3× | – |
| U10 | SALDS–SONTERRA 138 kV | reconductor | 14.4 mi, 232 MW | 5.3 / **6.6** / 16.0 | 26 / **48** / 61 | yes: TPIT-80546D, May 2027 | 59 MW / 392 MWh (6.7 h) | 16.1× | – |

**Battery or wires?** For the 24 elements that could be sized, the battery that relieves 10% of the limit in 80% of the element's binding hours costs a median **21 times** the primary wires fix (range 0.5 to 72) and a median **192 times** the cheapest wires option whose rating uplift covers the relief, usually a dynamic or ambient-adjusted rating [DERIVED]. Only WESTEX is below 1 (0.49: 2,696 MW and 16 GWh against the $9.06B Permian plan), which is a sizing exercise: the plan also serves load growth and the interface is stability-limited. Long rebuilds are the close calls (Longshore–Consavvy 1.4 times). Persistent binding defeats an energy-limited resource: Scurry Chevron–Knapp needs 3.2, 7.8 or 13.2 hours of storage to cover 50, 80 or 95% of its binding hours, and Twin Buttes–Hargrove, which binds day-ahead in 53% of the window's hours in 24 long episodes, is covered only 41% of the time by a 10-hour battery [DERIVED]. For the elements with a 2025 rent and no project, one year of rent is 10 to 452 times the yearly cost of the reconductor (Vealmoor–Koch Tap 452, Koch Tap–Buzzard Switch 346, Laredo VFT 180, Scurry Chevron–Knapp 101): the economics of a fix are not the obstacle [DERIVED]. Rents do not vanish fast: of the 2024 top ten, four still earn in the 2025 top ten.

**Who can make an upgrade happen** (`notes/phase4.md` section 6 has the citations):

- **The TSP decides, builds, owns and recovers the cost** from load through its transmission cost of service (4CP-based rates); wholesale storage does not pay it (16 TAC §25.192). A new line needs a PUCT CCN; rebuilding on the existing right of way or reconductoring does not (16 TAC §25.101).
- **Review path** (Nodal Protocols 3.11.4): Tier 1 projects of $200M or more need ERCOT Board endorsement; Tier 2 projects under $200M with a CCN get a 120-day review; Tier 3 is $50–200M without a CCN, or less if it is a 345 kV reconductor over a mile, added 345/138 kV autotransformer capacity or a new 345 kV substation; everything else, including a ratings change, is a neutral Tier 4 project that ERCOT does not review. A facility ratings update is not a project at all.
- **What a battery owner can do:** propose a project to the Regional Planning Group in any tier (NP 3.11.4.1); pay its own interconnection facilities above the allowance ($14M or $20M, 2024 dollars); offer to fund a project the TSP chooses to pursue (NP 3.11.4.11). **What it cannot do:** order, own, schedule or fund a network upgrade on its own.
- **Storage is not a transmission alternative in ERCOT today.** Planning treats it as a dispatchable resource; a project is reliability-driven only if no dispatch of generation and storage meets the criteria (PG 3.1.3.1). SB 415 (PURA 35.153) lets a utility contract for storage, but only to serve distribution customers, capped at 100 MW, and the utility may not own it.

### 3.2 The full rerank: what each upgrade does to the 40 sites

For each of U01–U10 and for two bundles of planned upgrades, the shadow prices of the replayed congestion structure were scaled by `gridsite.upgrades.apply_upgrade` (0.10 of their size left after a line or transformer upgrade, 0.50 after an interface upgrade [ASSUMPTION]), all 40 price lanes were rebuilt, and every site was valued again by the dispatch model (`gridsite.sites.value_sites`: perfect-foresight oracle on the rebuilt 2025 lane × the lane's own learner/oracle ratio, 2 h). Zone prices stay at their real values, so an upgrade removes only the node-minus-zone part. The table is [`data/upgrade_rerank.csv`](data/upgrade_rerank.csv): one row per scenario and site (480 rows) with value, rank, gain over zone, first-order comparison and the portfolio effect before and after.

| Scenario | Sites that lose more than $1 (top three: before → after, rank) [SIM] | Sites that gain [SIM] | Largest loss | Sum of the 40 sites | The 200 MW the caps allow |
|---|---|---|---|---|---|
| **U03** Twin Buttes–Hargrove | 28 sites. MIDW 247.7 → 51.0 (#1 → #9); STRS 226.8 → 48.2 (#2 → #14); POWELLFI 178.6 → 43.0 (#3 → #21); also MILES −59.5, NOVICE −48.8, and the Scurry–Knapp sites −11 to −22 | TANK +8.2 (#29 → #12). Rank gains: CRNSO #8 → #1, HILND #7 → #2 | MIDW −196.7 | 2,495 → 1,715 (−31.2%) | −23.3% |
| **U06** PK–DT | 4 sites. PK 58.0 → 22.6 (#13 → #39); BG 59.8 → 24.5 (#11 → #37); MID 57.3 → 22.5 (#15 → #40); GT −34.7 | none | PK −35.4 | 2,495 → 2,354 (−5.6%) | −5.9% |
| **U01** Scurry Chevron–Knapp | 8 sites. KNAPP 73.3 → 40.7 (#6 → #27); SHRNE 65.7 → 42.4 (#9 → #25); SCRCV 64.1 → 46.0 (#10 → #20); BULLCRK −18.0, MPPOI −16.8 | none (Twin Buttes sites +0.4) | KNAPP −32.6 | 2,495 → 2,376 (−4.7%) | −6.7% |
| **PLANNED_ALL** every dated planned upgrade incl. 2032+ | 6 sites. CRNSO 65.9 → 25.8 (#8 → #38); HILND 65.9 → 26.0 (#7 → #37); CRNTH 58.8 → 25.0 (#12 → #40) | TMPTV +2.5, PGE +2.3, SEA +1.5 | CRNSO −40.0 | 2,495 → 2,389 (−4.2%) | −6.4% |
| **U05** HIGH36–Brenno | 5 sites. BRENNO 50.3 → 30.4 (#21 → #32); GAYHIL 49.0 → 30.2; LYONS 48.2 → 29.6 (#24 → #36); SANDHI, CHAPHI | SEGOVI +1.0 | BRENNO −19.9 | 2,495 → 2,404 (−3.6%) | −3.2% |
| **U09** Encrt–Brand | 4 sites. ECRSW 34.8 → 27.2 (#34 → #39); ROUGH 31.0 → 25.6 (#35 → #40); SCRCV −2.8; EOLA −1.1 | 4 sites: KNAPP +4.4, SHRNE +3.8, BULLCRK +2.9, MPPOI +2.7 | ECRSW −7.5 | 2,495 → 2,493 (−0.1%) | −1.7% |
| **U10** Salds–Sonterra | none | none above $1 | EOLA −0.1 | 2,495 → 2,498 (+0.1%) | +0.1% |
| **PLANNED_2031** every planned upgrade in service by 2031 | 2 sites: COLLEGE −1.3, EOLA −1.1 | 5 sites: TMPTV +2.1, PGE +2.0, BRENNO +1.3, CHAPHI +1.2, SEA +1.0 | COLLEGE −1.3 | 2,495 → 2,500 (+0.2%) | −0.1% |
| U02 Vealmoor–Koch Tap, U04 WESTEX, U07 Longshore–Consavvy, U08 Bell County–Salado | **cannot be evaluated**: none of these elements is in the 260 replayed constraints (no regression shift factor), so no site moves | | 0 | 2,495 → 2,495 | 0 |

How to read it:

- **Twin Buttes–Hargrove is the upgrade that matters, and it would reorder the list.** After it the best five are CRNSO ($65.4), HILND ($65.3), BG ($58.9), CRNTH ($58.4) and PK ($57.9): the Twin Buttes pocket falls out of the top five and the Argyle and Houston pockets take over [SIM]. It is a $13.1M (mid) reconductor with a 48-month lead time and an RTP entry with no date [DERIVED]. This is the fix whose timing the Twin Buttes sites are most exposed to.
- **Gains are small and come from two mechanisms.** First, rank gains: sites that do not lose move up because others fall. Second, value gains at sites exposed to the upgraded element with the opposite sign to their main constraint: removing that term removes an offset. TANK gains $8.2 (+20%) when Twin Buttes is fixed; KNAPP, SHRNE, BULLCRK and MPPOI gain $2.7–4.4 when Encrt–Brand is fixed; both lines bind on the same contingency (DMTSCOS5) and follow the same West price driver in the replay, a likely reason their terms offset (not tested).
- **The planned wires by 2031 do not reach the candidates.** The bundle (28 projects, 52 constraints, new against the 2025 replay) moves no site by more than $2.1; the only dated project on a candidate's dominant element is Argyle–Lewisville (Dec 2032, match confidence 0.6), and the three RTP or TPIT matches on other candidate elements (Fort Mason–Yellow Jacket, PK–DT, HIGH36–Brenno) are at confidence 0.3, below the 0.5 the schedule needs. Include the 2032 project (PLANNED_ALL) and the three Argyle sites lose $34–40 each.
- **The caps change the stakes.** The 200 MW the caps allow is worth $11.96M a year at 2025 replay values [SIM]; MIDW's 10 MW is $2.5M of that. U03 takes $2.8M off it (−23%) whether the owner re-sites or not, because every candidate group holds some exposure to the line.

### 3.3 First order against the full valuation

Phase 4 ranked the effect of an upgrade to first order ([`data/upgrade_rerank_first_order.csv`](data/upgrade_rerank_first_order.csv): sum over constraints of |SF − zone mean SF| × shadow-price-hours × a fixed capture factor, no dispatch). The same formula, recomputed for the 40 candidates, reproduces that file's rows to 0.0005 $/kW-yr (22 candidate rows checked). Against the full valuation:

- **Baseline ranking agrees:** rank correlation 0.89 over the 40 sites [DERIVED]. Post-upgrade ranks agree at 0.80–0.93 per upgrade.
- **Who loses agrees; how much does not:** over the 76 (upgrade, site) pairs that move by more than $1 in either valuation the rank correlation of the change is 0.46. Both say "loses" for 29 pairs; only the full valuation says it for 28; only first order for 9; the two disagree on sign for 9 [DERIVED].
- **First order understates the biggest on-element losses and overstates export-side ones.** U03: MIDW −196.7 (full) against −135.4 (first order), STRS −178.6 against −122.3, POWELLFI −135.7 against −95.2, but MILES −59.5 against −101.4. The two agree on the share of the premium that goes (MIDW 89% of its gain in both); they differ on how large the premium was, because first order uses one capture factor (0.205 of shadow-price-hours) where the dispatch model gives MIDW 0.51 of its ceiling.
- **First order is blind to second exposures.** Under U03 the Scurry–Knapp sites SCRCV, SHRNE, KNAPP and BULLCRK lose $12–22 each in the full valuation and exactly 0 in first order: their Twin Buttes exposure is small per MW but the line's shadow price is large, and first order measures a zone's sensitivity as an unweighted bus mean where the lanes regress the zone's own price (a likely cause, not tested).
- **Sign flips are real:** under U09, KNAPP gains $4.4 in the full valuation and loses $2.8 in first order.

The ten largest disagreements, by the difference in the change of a site's value ($/kW-yr; ranks are among the 40 sites):

| Upgrade | Site | Full valuation, change [SIM] | First order, change [ASSUMPTION] | Full minus first order | Rank before → after, full | Rank before → after, first order |
|---|---|---|---|---|---|---|
| U03 | MIDW | −196.7 | −135.4 | −61.3 | #1 → #9 | #1 → #19 |
| U03 | STRS | −178.6 | −122.3 | −56.2 | #2 → #14 | #2 → #26 |
| U03 | MILES | −59.5 | −101.4 | +41.9 | #4 → #28 | #3 → #16 |
| U03 | POWELLFI | −135.7 | −95.2 | −40.5 | #3 → #21 | #4 → #30 |
| U03 | SCRCV | −21.7 | 0.0 | −21.7 | #10 → #22 | #16 → #11 |
| U03 | NOVICE | −48.8 | −69.8 | +21.0 | #5 → #30 | #5 → #31 |
| U03 | SHRNE | −18.8 | 0.0 | −18.8 | #9 → #18 | #14 → #9 |
| U03 | KNAPP | −17.7 | 0.0 | −17.7 | #6 → #8 | #6 → #1 |
| PLANNED_ALL | CRNSO | −40.0 | −23.1 | −16.9 | #8 → #38 | #11 → #39 |
| PLANNED_ALL | HILND | −39.9 | −23.1 | −16.8 | #7 → #37 | #12 → #38 |

Eight of the ten are the Twin Buttes upgrade: the largest by far, and spread over the most sites, so the simplifications of first order matter most there. On the Argyle–Lewisville pair first order has the right sign and rank but about 60% of the size.

### 3.4 Basis half-life

The basis half-life is the first year from 2026 in which a site's gain over its zone falls below half of its 2025 value, with planned upgrades and storage saturation applied (section 4.4). Nine sites reach it in 2028 (MIDW, STRS, POWELLFI, MILES and NOVICE behind Twin Buttes; SEGOVI and EOLA behind Fort Mason; ROUGH and LWSVW), six in 2029 and 25 not by 2031 [SIM]. All of the fade is storage saturation: with only planned upgrades the 2031 mean learner value moves by +$0.05, with only saturation by −$19.2 [SIM]. For the three sites with a small 2025 gain (ROUGH $3.7, ECRSW $7.4, LWSVW $7.7) the 50% line is a few dollars and the year is noise.

---

## 4. Trading after placement

### 4.1 Which algorithm wins at a node

A1–A5 dispatched on every rebuilt lane, walk-forward (prices up to yesterday), energy only; the oracle is perfect foresight. 2021–25 mean, 2 h, $/kW-yr [SIM]:

| Algorithm | 8 zones | 40 nodes | Gain | Nodes / zones | Share of oracle: zones → nodes | $/MWh discharged: zones → nodes | Best at (nodes / zones) |
|---|---|---|---|---|---|---|---|
| A1 Clockwork | 39.8 | 50.1 | +10.3 | 1.26 | 0.384 → 0.284 | 54 → 69 | 0 / 0 |
| A2 Forecaster | 42.9 | 81.0 | +38.0 | 1.89 | 0.414 → 0.460 | 81 → 110 | 36 / 1 |
| A3 Weather-regime | 37.6 | 69.4 | +31.8 | 1.85 | 0.362 → 0.394 | 69 → 97 | 0 / 0 |
| A4 Spike hunter | 29.6 | 60.3 | +30.7 | **2.04** | 0.286 → 0.342 | 225 → 445 | 0 / 0 |
| A5 Adaptive learner | 44.4 | 78.3 | +33.9 | 1.76 | 0.428 → 0.444 | 90 → 134 | 4 / 7 |
| Perfect foresight | 103.7 | 176.2 | +72.6 | 1.70 | | | |

- **The Forecaster, not the learner, earns the most at 36 of the 40 nodes** ($81.0 against $78.3); at the zones the learner wins 7 of 8. The learner follows whichever of six experts earned most recently, and at the nodes that is the Forecaster on most days (25% of days against 15% at the zones; 45% at the Twin Buttes five). A plausible reason, not tested: when one expert is persistently best, tracking it with a lag costs a little. **Caveat:** the synthetic basis is a function of hub spreads the Forecaster can read, so it may be more predictable than a real one. This needs a check against real nodal prices.
- **The Spike hunter gains most from siting, relatively but not absolutely.** It improves ×2.04 (the learner ×1.76) and its margin per MWh doubles, because a battery that keeps itself full and sells only into the top few percent earns more when the top few percent are higher. It is still $18 below the learner at all 40 nodes over 2021–25, falls further behind the stronger the pocket is (correlation of the gap with the oracle uplift −0.85), and is the best algorithm only at the three Houston PK–DT nodes in the 4 h run.
- **Clockwork loses ground in pockets** (38% of the oracle at the zones, 28% at the nodes): a fixed daily schedule cannot follow a basis that binds on a few days.
- **No algorithm closes the gap to the ceiling in a pocket:** the oracle is $176 at the nodes against $104 at the zones; every algorithm captures 28–46% of it.
- **4 h batteries earn 1.3–1.7 times a 2 h one at the nodes** (top 15: $132 against $94 over 2024–25; 1.38 at the zones), with a higher share of the oracle (0.58 against 0.48). The longer battery is worth most where the pocket is wide (SEGOVI ×1.67, KNAPP ×1.59, BULLCRK ×1.55) and least at the short Houston spikes (BG ×1.33, PK ×1.28).

### 4.2 Margin per MWh

At the zones the learner earns $90 for each MWh it discharges; at the nodes $134 [SIM]. The Spike hunter's margin per MWh is the highest of the five ($225 at the zones, $445 at the nodes) because it cycles about a tenth as often. MIDW's 4 h learner earns $298 per MWh discharged and the three Houston nodes (BG, GT, PK) $127–155 [SIM].

### 4.3 The ancillary layer after RTC+B

Ancillary-service prices have collapsed. Mean day-ahead clearing price, $/MW-h [REAL]:

| | 2021 | 2022 | 2023 | 2024 | 2025 | 2026 to 10 Oct |
|---|---|---|---|---|---|---|
| Reg Up | 244.37 | 21.67 | 26.79 | 6.01 | 2.98 | 2.11 |
| Reg Down | 95.57 | 8.46 | 13.63 | 3.44 | 1.62 | 1.31 |
| RRS | 312.24 | 20.31 | 21.26 | 5.67 | 2.76 | 1.64 |
| ECRS | | | 58.16 (from 10 Jun) | 8.23 | 2.46 | 2.01 |
| Non-Spin | 134.00 | 22.49 | 19.05 | 6.22 | 3.05 | 4.21 |

Real-time prices since RTC+B are below day-ahead for every product (2026 to 10 Oct: Reg Up 1.48, RRS 0.57, ECRS 0.95, Non-Spin 2.71). The ceiling for a 2 h battery (all capacity in the best product every interval) fell from $328/kW-yr in 2023 to $79 (2024), $31 (2025) and $31 (2026 annualised) [DERIVED]. What the arena's dispatchers could realistically add is **$15.9/kW-yr in 2025 and $11.9 in 2026** for a 2 h battery (4 h: $16.3 and $15.5; upper bounds $34.8 and $44.4) [SIM, ASSUMPTION-grade], about as large as the whole zone energy value in 2025 ($17.8) and 2026 ($11.2), and a minor part of a pocket's ($54.6 at the nodes in 2025, $237.6 at MIDW). It is **system-wide**, the same at every site, so it sits in its own column and is never added to the energy value. Awards are bound by state of charge, so the real number lies between that estimate and the ceiling; more storage dilutes rather than adds, so do not scale it by fleet size.

### 4.4 Forward saturation: 2027–2031

From the 2025 congestion replay, scaled each year by planned upgrades and by the storage that arrives: 41.2 GW listed (4.1 GW operating in 2026, 34.4 GW of queue with an interconnection agreement signed, 2.7 GW firm planned), of which 26 GW is effective by 2031 at 60% realisation of unbuilt MW [ASSUMPTION]. Mean over the 40 candidates, 2 h learner [SIM]:

| Year | Learner at the 40 nodes | Zone controls | Gain over the zone | Gain relative to 2025 | Effective storage online (GW) |
|---|---|---|---|---|---|
| 2025 replay | 62.4 | 23.1 | **38.4** | 1.00 | |
| 2026 | 61.1 | 23.1 | 37.1 | 0.97 | 3.5 |
| 2027 | 57.1 | 23.1 | 33.1 | 0.86 | 10.4 |
| 2028 | 48.5 | 23.1 | 24.6 | 0.64 | 18.6 |
| 2029 | 43.3 | 23.1 | 19.3 | 0.50 | 24.6 |
| 2030 | 43.3 | 23.1 | 19.3 | 0.50 | 25.9 |
| 2031 | 43.2 | 23.1 | **19.3** | 0.50 | 26.1 |

The pocket premium halves by 2029 and then holds, and **all of the fade is storage saturation**. Zone prices are held at their 2025 values (no zone forecast is attempted), so only the node-minus-zone part moves; forward values are an oracle-ratio method on a 2025 price year, a different footing from the 2021–26 dispatch rows (the 2025 replay is $62.4 against $54.6 by dispatch). **The Twin Buttes decline is the fragile part**: MIDW falls from $248 to $87 by 2029 because 15 storage-hosting substations with a shift factor of about −0.065 (t about 7) add up to 111 MW of relief on a 151 MW line. A minimum shift factor of 0.075 instead of 0.05 removes the effect (MIDW $247.0 in 2031) and with it the half-life of the five sites. Read their half-life as "2028 if the cluster counts". The Houston PK–DT nodes (BG, GT, PK, MID, $57–60) do not fade at all.

---

## 5. Reliability and physical risk (kept apart from market value)

Phase 5 scores every substation for reliability **need** in its own units and, through a named value-of-lost-load constant, in customer dollars. It is not revenue and is never added to the market columns.

- **Backup value.** A 1 MW / 4 MWh battery on a 1 MW critical load would have avoided about 3.7 MWh of unserved energy a year in the median Texas county (Harris 1.1 MWh, Bexar 0.5) if it started every outage full; with only a 20% reserve, 1.2 MWh [DERIVED]. At Brattle's one-hour, ERCOT-wide $35,685/MWh [REAL] that is about $131k a battery-year in the median county ($74k with the duration-adjusted prices). In Harris the battery would have served 0.9 MWh in Uri, 1.0 in the derecho and 3.3 in Beryl: Beryl is the event that matters for a 4 h battery in Houston.
- **Radial exposure is modest and concentrated in West Texas.** 618 of 3,505 graph-linked substations (18%) sit behind a bridge; West Texas has the highest share. The largest pockets are 7–8 substations; a single station on a single line is the usual pattern.
- **The need ranking is a screen.** The top 20 is 15 Harris substations (BU, LK, OA, JES, MAR, FAN, HP, MID, BG...) plus Jones, Brazoria, Jefferson, Taylor and Haskell counties, because county-level outage data cannot separate substations inside a county. It is not robust to the weights: with equal weights 17 of 20 stay, without the large-load component 8, with outage data only 0 [ASSUMPTION]. Use `need_rank` as a screen, not an exact ranking.
- **Overlap with market value.** Only four of the 40 sites are in the top 100: MID (#10), BG (#11), GT (#62) and PK (#64), all on the Houston PK–DT line. The Twin Buttes pocket is a low-need area: MIDW, STRS and POWELLFI (Crockett County, whose outage data is unusable) carry a need score of 5.6 of 100 and no rank, and MILES is #2,051. Houston is both a market case and a reliability case; Twin Buttes is a market case only. **The Houston sites are also the ones that do not fade** (section 4.4), but a PK–DT reconductor would take $35 off each (section 3.2).
- **Physical-risk exclusions.** 147 of 2,869 site-assessed substations (5.1%) are excluded: 129 in a FEMA special flood hazard area, 11 in a regulatory floodway, 7 at very high wildfire hazard. The flood check is blind in West Texas (80% have no digital polygon): "unmapped" is not "low risk". None of the 40 candidates is excluded; seismic flags (M3 or more within 25 km since 2015) mark KNAPP, SCRCV, APOLLO and ECRSW and never exclude. Coastal, heat and cold markers are carried as columns.

The full table is [`data/reliability.csv`](data/reliability.csv) (5,035 substations), [`data/risk_flags.csv`](data/risk_flags.csv), [`data/eaglei_county.csv`](data/eaglei_county.csv) and [`data/critical_facilities.csv`](data/critical_facilities.csv); the map shows the top 100 and the excluded substations as layers.

---

## 6. Tier 2: distribution batteries and home fleets

Tier 2 is storage under 10 MW on distribution substations, and home-battery fleets. [`data/tier2_valued.csv`](data/tier2_valued.csv) adds market value to Phase 5's 2,591 candidates (2,024 NP4-160 substations at 138 kV or less with evidence of load, 567 OSM-only distribution substations near a load centre).

**Which price each route is paid** (`notes/rules.md`, NP §6.6, ADER Governing Document 3.3) [REAL]:

| Route | Settles at | Gets nodal congestion value? |
|---|---|---|
| **DESR**: distribution-connected storage that registers as a resource | A resource node fixed at one bus, the highest-voltage bus of the substation (or of the nearest placed substation) | **Yes** |
| **ADER**: aggregation of home batteries (1 MW or less each, pilot capped at 500 MW) | The load-zone price (the NOIE zone for Austin Energy, CPS, LCRA and Rayburn) | **No** |
| Settlement-only storage of 10 MW or less | Load zone while storage is over 50% of the site; nodal after NPRR995 | Not today |

**Method.** A candidate's DESR settlement bus is screened on a cheap score, the sum over replayed constraints of |SF − zone SF| × shadow-price-hours; of 2,125 settlement buses, 2,059 have a usable shift factor of 0.10 or more to a replayed constraint (2,503 candidates). The best 150 buses (the compute cap; score cut-off $99,674 per MW-year of exposure) were valued with `value_sites` for a 2 h battery in 2025 and in 2029 (planned upgrades and saturation applied). Candidates at buses that were not valued carry their zone value, labelled in `nodal_value_source` (2,331 below the cap, 88 with no usable shift factor): **their nodal column is the zone value, not a finding that there is no nodal premium.** In Phase 3's calibration of the same score on its pool, below the 90th percentile the mean 2025 gain was under $7/kW-yr. The ADER route carries the load-zone value of the same 2 h battery ($17.7–28.4/kW-yr by zone) [SIM]. Each candidate also carries the distance from its settlement bus to the line behind its value (`nodal_distance_to_element_km`, `nodal_distance_class`, threshold 150 km [ASSUMPTION]).

**Results at the 172 valued candidates** (150 buses; 146 NP4-160 candidates and 26 OSM-only; 31 of the buses are Tier 1 candidates too). The last three columns split them by how far the settlement bus is from the nearest placed end of the line its value comes from (`nodal_distance_class`) [SIM]:

| | All 172 | Within 150 km of the line (76) | Beyond 150 km (79) | Line not placed (17) |
|---|---|---|---|---|
| DESR nodal value, 2025 replay, mean | $51.0 | $63.1 | $40.7 | $44.5 |
| ADER zone value of the same battery, mean | $23.9 | $24.4 | $23.3 | $24.9 |
| **Gain of the nodal route, 2025: mean (median)** | **+$27.0** (+$23.0) | **+$38.8** (+$32.3) | +$17.3 (+$14.3) | +$19.6 (+$19.8) |
| Gain of the nodal route, 2029 after saturation: mean (median) | +$13.0 (+$11.4) | +$16.1 (+$13.6) | +$9.0 (+$6.0) | +$17.9 (+$19.3) |

- **DESR captures the nodal premium; ADER does not.** 170 of the 172 gain over the zone, 77% by more than $10. A home fleet gets the zone value however congested its feeder is.
- **Distance says how far to trust a value.** 79 of the 172 sit more than 150 km from the line their value comes from (up to 519 km), where a shift factor of 0.10 or more is physically unlikely [DERIVED]. Two causes: a mis-placed line end (College–Jupiter, 10 candidates: COLLEGE sits in Brazos County; Encrt–Brand, 4: its BRAND end was matched to BRAND_C in Dallas by a weak alias) and, for the 58 behind Twin Buttes, day-ahead fits. They gain +$17.3 against +$38.8 within 150 km. The flag is a caution, not a correction.
- **Most of the valued buses sit behind Twin Buttes–Hargrove** (94 of 172), on the day-ahead fits that are the weakest in the study. Of these 58 are more than 150 km from the line (Dallas, Matagorda, Nueces, Burleson and other counties; gain +$17.0, +$6.6 by 2029); the 34 within 150 km gain +$47.3, falling to +$8.1 by 2029. The strongest are the Tier 1 sites and their neighbours (BUSBY $152.5, KETCHUM $142.5, RCKHOLW $112.9). The other pockets hold up better, mean gain 2025 → 2029: Argyle–Lewisville (28 candidates) +$30.5 → +$22.0, Fort Mason (14) +$23.7 → +$11.3, College–Jupiter (13) +$25.2 → +$22.3, HIGH36–Brenno (9) +$21.6 → +$20.9, PK–DT (4) +$39.6 → +$39.6. Placement and county of some distribution substations come from name matching (53 of the 172 have a match confidence below 0.5); the confidence is a column.
- **The pocket's room is shared.** A distribution battery behind Twin Buttes–Hargrove uses the same 10 MW of price-taker room as the Tier 1 sites (`nodal_group_cap_mw`).
- **Deferral value and reliability are separate columns.** The distribution deferral value is $43 / $48 / $60 per kW-yr (low / mean / high; 25 non-wires studies [REAL]) and applies only where a utility documents a need and runs a request for proposals (SB 415, proposed 16 TAC 25.58); the reliability need score (median 38.7 of 100 [ASSUMPTION]) and the unserved-energy dollars are as in section 5. None is added to a market value.
- **Home fleets** earn by zone, energy only (arena home spec, SIM): $24.5 (West) to $32.5 (Houston) per kW-yr merchant; the best programmes modelled are Austin Energy tolling ($102, if a tranche opens), data-centre flexibility in DFW ($82), co-op peak in LCRA and Rayburn ($77–82) and SB 415 feeder contracts in Houston ($61); contract terms are arena assumptions.
- **Hosting.** The no-study fast path (16 TAC §25.211) is 0.79 MW per feeder and 3.15 MW per substation at the default feeder peak (median); no Texas utility publishes feeder ratings, so 87% of candidates use the default [ASSUMPTION]. A shared feeder model would replace those columns; the method (sweep every service transformer, add a feeder layer judged by OpenDSS, turn the upgrade into a ladder, roll up to the substation) is in `notes/phase5.md`.

---

## 7. How this feeds the dispatch platform

[`dispatch/`](../../../dispatch/README.md) is the operate layer of Batter Up: a shadow optimizer and attribution engine for ERCOT batteries and a paper-dispatch service behind a deterministic risk guard. Its `DESIGN.md` names this study as the source of "which settlement points to forecast and attribute". Today it forecasts and attributes at the **hubs** (HB_NORTH, HB_WEST, HB_HOUSTON, HB_SOUTH). Its public case study, a 50 MW battery settling near HB_SOUTH, uses a hub price as a proxy for the node (node minus hub: mean +$1.01/MWh, RMSE $2.93/MWh), and the proxy alone could move 11.1% of the attribution gap against a 5% target. A real nodal history closes that.

**The contract is a table.** [`data/sites.csv`](data/sites.csv) (read-only, not imported) is the **ordered queue of settlement points to add** once nodal history exists. Each row gives the electrical bus a resource node would sit on (`node_bus`), whether a resource node exists there today (`existing_resource_node`: two of the 40, KNAPP_RN and JUNCTION_RN), the zone and hub to fall back on, and the value that sets the order. Appendix A lists all 40 in that order. The first ten:

| Queue | Sub | Bus a node would sit on | Hub proxy until there is history [ASSUMPTION] | Why first |
|---|---|---|---|---|
| 1 | MIDW | MIDW_L_A | HB_WEST | Twin Buttes, import, $269.7 [SIM]; half-life 2028 |
| 2 | STRS | STRS_L_A | HB_WEST | Twin Buttes, import, $249.5 |
| 3 | POWELLFI | POWF_L_A | HB_WEST | Twin Buttes, import, $201.5 |
| 4 | KNAPP | EB2_KNAPP (resource node KNAPP_RN exists) | HB_WEST | Scurry–Knapp, import, $98.2; no project on file |
| 5 | MILES | MILES_B114 | HB_WEST | Twin Buttes, export (charging side), $96.9 |
| 6 | SHRNE | EXSHARNR_8 | HB_WEST | Scurry–Knapp, import, $89.7 |
| 7 | NOVICE | NOVC_L_A | HB_WEST | Twin Buttes, export, $83.0 |
| 8 | SEGOVI | L_SEGOVI9_1Y | HB_SOUTH | Fort Mason, import, $79.0; LCRA zone |
| 9 | HILND | HIGHLAND | HB_NORTH | Argyle–Lewisville, import, $78.6; does not fade |
| 10 | CRNSO | EB_CRNSOX2 | HB_NORTH | Argyle–Lewisville, import, $78.6 |

**What the platform needs per entry.** The 15-minute settlement-point price and the 5-minute bus price at the bus (reports NP6-905 and NP6-787, whose public windows are 7 and 5 days), the day-ahead price (NP4-190 / NP4-183), the binding constraints and shadow prices (NP6-86, NP4-191) and the ancillary clearing prices; the node price of a resource node equals the bus price to a cent in 99.98% of runs, so the bus price is a valid stand-in before a battery registers. **What the study gives it:** the order, the constraint and side behind each node (`constraint1`..`constraint3`), the shift factors that let the platform model a node as hub plus basis, the price-taker room (`price_taker_cap_mw`) and the risk flags. **What it needs from ERCOT:** an API key (section 8), because the public windows are a week long.

---

## 8. Confidence and limits

- **The nodal rebuild is SIM.** The formula (zone price minus the node's shift factors times shadow prices) reproduces the real node basis of 3–10 Oct with correlation 0.994 and a mean error of $1.85/MWh when fed the published shadow prices (on 3–5 Oct, before the fit window: 0.984 and $1.52; day-ahead month 0.969 and $1.14) [SIM]. What is not validated is the timing of the synthetic shadow prices: 120 of the 260 replayed constraints (46%) use a geographic rule for when they bind. **Levels are good to about a factor of two** (across six alternative SIM choices the mean 2025 gain moves from −16% to +25%, the median by −46% to +72%); **rankings are steadier** (rank correlation 0.90 to 1.00 across the variants; the screening score ranks a stratified pool sample at 0.94) [SIM].
- **One week of real-time shift factors.** The real-time window is 5–10 Oct plus the 25 Sep archive day, about 8.5 days: a shoulder, outage-driven week full of local 138 kV and 69 kV constraints. Shift factors are topology, so they transfer in principle; which constraints bind does not. **Structural constraints are replayed from the 2025 market-monitor rents**, not measured: only 260 of 1,386 constraints (with usable regression shift factors, $889M of replayed rent) enter. Absent are Vealmoor–Koch Tap, WESTEX, Longshore–Consavvy, Bell County–Salado, Laredo VFT and Odessa EHV–Yarbrough, so no Panhandle, Valley or Far West site appears and the rerank cannot move any site for four of the ten upgrades.
- **The Twin Buttes pocket is a different kind of number.** Its level is anchored on a published rent ($145M) and hours (945), but its shift factors are day-ahead fits with variance inflation above 100 (day-ahead-fitted constraints rebuild the day-ahead month at correlation 0.84 and the holdout days at 0.62). Every value is a first-MW price.
- **Gate A (after Phase 1) passed with limits:** no ERCOT API key, so real-time nodal history is one week and day-ahead 31 days; the network graph has no ratings or impedances; the MISO unit-cost cells read from garbled text are UNVERIFIED. **Gate B (after Phase 2) passed for Phase 3 with limits:** real-time shift-factor fit median out-of-sample R² 0.978 over 19,199 buses, day-ahead 0.756; the sign convention was recomputed from raw parquets (the 30 most import-side buses of the most-binding constraint average +$315.84/MWh of congestion, the 30 most export-side −$111.12); 496 constraints have regression shift factors, 476 only a physics proxy (median correlation 0.38 with the regressions, so never ranked on) and 414 none; structural constraints are under-represented. Both gates and the Phase 3–6 checks are in [`notes/gates.md`](notes/gates.md).
- **Placement.** 72% of the 5,035 NP4-160 substations have coordinates and 57% a confidence of 0.5 or more (90% of those with a resource node are placed); everything that ranks sites uses 0.5 or more. Coordinates for plant-matched substations are the plant, not the fence. One known suspect among the 40: COLLEGE is placed in Brazos County, but BRAND_C, the other end of its Brand–College line, and every site behind College–Jupiter are in Dallas County, and Phase 2 rejected the Brand–College pair on length (261 km). Its value does not depend on its position; its map position and connection class may.
- **Costs are screening-level**: MISO 2024 rows, no escalation (about 4% a year), the step-up transformer is an unverified proxy and 88% of connection cost, and the break-even cost leaves out ancillary revenue, capacity payments, O&M and tax.
- **Forward values are a different kind of number** from the history: zone prices stay at 2025, the structure is the 2025 replay, a pocket that does not exist today is invisible, and the saturation constants are assumptions (`SAT_MIN_SF` is a cliff for Twin Buttes).
- **The Forecaster's win at nodes, the learner's relative loss and the Spike hunter's relative gain are results on these lanes** and need a check against real nodal prices.

**What an ERCOT API key would change.** The public API archives (2023 to 2026) hold what the public windows do not: bus prices and shadow prices for whole years. With them (1) the shift factors could be refitted across seasons and the one-week bias removed; (2) the structural constraints that never priced in our window could be measured instead of replayed, and Vealmoor–Koch Tap, WESTEX, Longshore–Consavvy and Bell County–Salado would get real shift factors and enter the rerank; (3) the top sites' price lanes would be REAL, and the dispatch algorithms could be backtested on them, answering whether the Forecaster really beats the learner; (4) the timing of the synthetic shadow prices, now 46% geographic rule, would be replaced. ERCOT allows three downloads of the same report a year through the API, so the first pull should be one month of one report.

---

## 9. Reproduce

Run from `docs/research/grid` with the research venv (`C:/Users/Javier/Repo/BaseP/research-data/.venv/Scripts/python.exe run_grid.py <command>`); raw data and caches live outside git in `research-data/grid/`. At most 4 workers; the machine is shared.

| Phase | Commands (run in this order) | Time |
|---|---|---|
| 1a market data | `fetch-ercot all`, `parse-ercot`, `collect-daily` (at least every 4 days), `report-ercot` | minutes to tens of minutes |
| 1b planning, queue, rules | `fetch-docs`, `parse-docs` | minutes |
| 1c geography | `fetch-geo all`, `match-geo` | 2 min match |
| 2 network, shift factors | `constraints`, `shift-factors`, `physics-proxy`, `hops`, `map substations` | 25 s, 7.5 min, 40 s, 80 s, 5 s |
| 3a infrastructure | `compat-test`, `as-layer`, `costs`, `queue-map` | minutes |
| 3b site value | `candidates`, `lanes`, `dispatch spec=all workers=4`, `forward spec=all workers=4`, `portfolio`, `sites` | about 28 min after the lanes |
| 4 upgrades | `upgrades`, `nwa`, `rerank-first-order` | 40–50 s each |
| 5 reliability, Tier 2 | `fetch-reliability all`, `risk all`, `reliability`, `tier2` | about 3 min from cached downloads |
| 6 report | `rerank workers=3`, `tier2-value workers=3 max-nodes=150`, `map`, `report-check`; `python gridsite/check_labels.py` | 5.5 min, 4 min, 5 s, 1 min, 1 s |

Tests are plain scripts (the venv has no pytest): `python tests/test_6.py`, `test_3b.py`, `test_4_upgrades.py`, `test_arena_compat.py`. The bare `map` command now builds this page; the Phase 2 substation map is `map substations`.

**Files.**

| Path | What |
|---|---|
| `data/sites.csv` | The 40 candidate sites (150 columns): value by algorithm, 2 h and 4 h, forward values, half-life, caps, connection, risk, reliability |
| `data/upgrade_rerank.csv`, `upgrade_rerank_first_order.csv` | Full and first-order reranks |
| `data/upgrades.csv`, `upgrade_options.csv`, `nwa_sizing.csv` | Upgrade table, wires options, battery sizing |
| `data/tier2_valued.csv` | Tier 2 candidates with market, deferral and reliability in separate columns |
| `data/reliability.csv`, `risk_flags.csv`, `portfolio.csv`, `site_years.csv`, `constraints.csv`, `shift_factors.csv`, `hops.csv` | Supporting tables; every table has a `.labels.csv` sidecar (REAL / DERIVED / SIM / ASSUMPTION per column) |
| `map/index.html`, `map/index_data.js` | The interactive map (Leaflet 1.9.4, OpenStreetMap tiles; opens as a local file; about 0.6 MB) |
| `gridsite/` | The code, one module per phase; `cli_6.py` registers this phase's commands |

---

## Appendix A. All 40 sites, in queue order

2 h learner, $/kW-yr, 2021–25 mean and 2025 [SIM]; "Gain" over the zone; "2031" the forward learner value [SIM]; connection class and lead time in months [DERIVED]; "Net of conn." = mean 2027–31 learner value minus annualised connection cost [SIM]. Constraint: dominant element (side, SF, hops).

| # | Sub | Zone | kV | Bus (existing resource node) | Constraint (side, SF, hops) | 2 h 21–25 | 2025 | Gain | 2031 | Half-life | Connection | Net of conn. |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | MIDW | WEST | 69 | MIDW_L_A | HARGRO_TWINBU1_1 (import, −1.00, 8) | 269.7 | 237.6 | 220.0 | 87.3 | 2028 | bay 38.5 | 97.3 |
| 2 | STRS | WEST | 69 | STRS_L_A | HARGRO_TWINBU1_1 (import, −0.92, 7) | 249.5 | 214.7 | 199.7 | 81.0 | 2028 | bay 38.5 | 88.0 |
| 3 | POWELLFI | WEST | 69 | POWF_L_A | HARGRO_TWINBU1_1 (import, −0.72, 8) | 201.5 | 165.7 | 151.8 | 67.4 | 2028 | bay 38.5 | 67.4 |
| 4 | KNAPP | WEST | 138 | EB2_KNAPP (KNAPP_RN) | 6437__F (import, −0.46, 0) | 98.2 | 53.9 | 48.5 | 59.1 | > 2031 | bay 56.5 | 39.4 |
| 5 | MILES | WEST | 69 | MILES_B114 | HARGRO_TWINBU1_1 (export, +0.50, 6) | 96.9 | 101.4 | 47.2 | 32.9 | 2028 | bay 38.5 | 24.5 |
| 6 | SHRNE | WEST | 138 | EXSHARNR_8 | 6437__F (import, −0.33, 2) | 89.7 | 47.3 | 40.0 | 50.9 | > 2031 | bay 56.5 | 31.4 |
| 7 | NOVICE | WEST | 69 | NOVC_L_A | HARGRO_TWINBU1_1 (export, +0.36, 5) | 83.0 | 83.7 | 33.2 | 30.6 | 2028 | bay 38.5 | 18.9 |
| 8 | SEGOVI | LCRA | 69 | L_SEGOVI9_1Y | FORTMA_YELWJC1_1 (import, −0.50, 3) | 79.0 | 42.6 | 31.8 | 38.9 | 2028 | bay 38.5 | 18.7 |
| 9 | HILND | NORTH | 138 | HIGHLAND | 587__A (import, −0.55, 2) | 78.6 | 53.2 | 37.1 | 54.1 | > 2031 | bay 56.5 | 35.1 |
| 10 | CRNSO | NORTH | 138 | EB_CRNSOX2 | 587__A (import, −0.55, 2) | 78.6 | 53.6 | 37.1 | 54.2 | > 2031 | bay 56.5 | 35.2 |
| 11 | BULLCRK | WEST | 138 | BULLCRK_1381 | 6437__F (import, −0.26, 4) | 74.7 | 38.6 | 25.0 | 44.3 | > 2031 | switchyard 56.5 | 12.9 |
| 12 | BG | HOUSTON | 138 | BG_138A | DT_PK_91_A (import, −1.00, 3) | 74.0 | 62.6 | 26.8 | 59.4 | > 2031 | bay 56.5 | 36.8 |
| 13 | GT | HOUSTON | 138 | GT_8065 | DT_PK_91_A (import, −1.00, 2) | 72.8 | 60.8 | 25.7 | 57.5 | > 2031 | bay 56.5 | 34.8 |
| 14 | CRNTH | NORTH | 138 | CORINTH1_8T | 587__A (import, −0.47, 2) | 72.5 | 48.3 | 31.0 | 48.8 | > 2031 | bay 56.5 | 29.3 |
| 15 | PK | HOUSTON | 138 | PK_8060 | DT_PK_91_A (import, −1.00, 0) | 72.3 | 63.4 | 25.1 | 58.2 | > 2031 | bay 56.5 | 35.5 |
| 16 | MID | HOUSTON | 138 | MID_138B | DT_PK_91_A (import, −1.00, 1) | 72.1 | 61.0 | 25.0 | 57.4 | > 2031 | bay 56.5 | 34.7 |
| 17 | MPPOI | WEST | 138 | MPPOI_K | 6437__F (import, −0.25, 4) | 72.0 | 35.9 | 22.2 | 42.7 | > 2031 | bay 56.5 | 21.8 |
| 18 | SCRCV | WEST | 138 | SCURYCHV_2 | 6437__F (export, +0.26, 0) | 69.3 | 65.7 | 19.6 | 47.2 | > 2031 | bay 56.5 | 28.2 |
| 19 | SHELLLON | SOUTH | 69 | SHELLLON_L_A | FORTMA_YELWJC1_1 (import, −0.50, 3) | 69.0 | 37.5 | 31.6 | 31.0 | 2029 | bay 38.5 | 10.4 |
| 20 | JUNCTION | SOUTH | 69 | JUNCTION_LD1 (JUNCTION_RN) | FORTMA_YELWJC1_1 (import, −0.50, 2) | 68.3 | 35.9 | 30.9 | 30.8 | 2029 | bay 38.5 | 10.1 |
| 21 | MASONSW | SOUTH | 69 | MASW_V_A | FORTMA_YELWJC1_1 (import, −0.51, 2) | 67.9 | 37.0 | 30.6 | 30.7 | 2029 | bay 38.5 | 10.0 |
| 22 | EOLA | WEST | 69 | EOLA_L_A | FORTMA_YELWJC1_1 (import, −0.50, 2) | 67.7 | 39.3 | 18.0 | 32.8 | 2028 | bay 38.5 | 12.7 |
| 23 | APOLLO | NORTH | 138 | EB_AP_0022 | COLLE_JUPIT_1 (import, −0.37, 13) | 66.7 | 49.8 | 25.2 | 49.6 | > 2031 | bay 56.5 | 27.7 |
| 24 | TANK | WEST | 69 | TANK_L_A | LAKENA_SAMATH1_1 (import, −0.61, n/a) | 64.8 | 22.2 | 15.0 | 45.7 | > 2031 | bay 38.5 | 21.9 |
| 25 | BRENNO | LCRA | 138 | L_BRENNO8_1Y | 215T215_1 (import, −0.73, n/a) | 62.6 | 38.7 | 15.4 | 48.8 | > 2031 | bay 56.5 | 26.1 |
| 26 | SANDHI | LCRA | 138 | L_SANDHI8_1Y | 215T215_1 (import, −0.71, n/a) | 61.3 | 36.7 | 14.0 | 47.8 | > 2031 | bay 56.5 | 25.2 |
| 27 | GAYHIL | LCRA | 138 | L_GAYHIL8_1Y | 215T215_1 (import, −0.71, n/a) | 60.6 | 37.8 | 13.4 | 48.5 | > 2031 | bay 56.5 | 25.9 |
| 28 | LYONS | LCRA | 138 | L_LYONS_8_1Y | 215T215_1 (import, −0.71, n/a) | 60.0 | 37.6 | 12.7 | 47.8 | > 2031 | bay 56.5 | 25.1 |
| 29 | CHAPHI | LCRA | 138 | L_CHAPHI8_1Y | 215T215_1 (import, −0.63, n/a) | 59.7 | 35.9 | 12.5 | 44.5 | > 2031 | bay 56.5 | 21.8 |
| 30 | SEA | NORTH | 69 | SEATON9 | SEA_AAT1 (import, −0.72, 0) | 52.9 | 28.3 | 11.4 | 30.6 | > 2031 | bay 38.5 | 10.0 |
| 31 | TMPTV | NORTH | 69 | TEMPTAYV1_9X | SEA_AAT1 (import, −0.36, 4) | 52.1 | 24.5 | 10.6 | 27.4 | 2029 | bay 38.5 | 6.8 |
| 32 | PGE | NORTH | 69 | POAGE | SEA_AAT1 (import, −0.42, 5) | 49.9 | 27.1 | 8.4 | 25.7 | 2029 | bay 38.5 | 5.2 |
| 33 | COLLEGE | NORTH | 138 | COLLEGE_AG1 | COL_BRAN_1 (import, −0.54, n/a) | 49.2 | 32.9 | 7.7 | 29.7 | > 2031 | bay 56.5 | 8.3 |
| 34 | ECRSW | WEST | 138 | ECRSW_17962 | 6429__D (export, +0.28, 16) | 48.3 | 30.5 | −1.5 | 29.8 | 2029 | bay 56.5 | 8.2 |
| 35 | ROUGH | WEST | 138 | EB1_ROUGH | 6429__D (export, +0.19, 30) | 46.9 | 26.8 | −2.9 | 26.8 | 2028 | bay 56.5 | 5.0 |
| 36 | MILLERRD | NORTH | 138 | EB_0002X | COLLE_JUPIT_1 (export, +0.18, 13) | 45.6 | 23.3 | 4.1 | 27.7 | > 2031 | bay 56.5 | 5.3 |
| 37 | WYNJOYCE | NORTH | 138 | EB0001_WJ | COLLE_JUPIT_1 (export, +0.16, 13) | 45.2 | 22.6 | 3.7 | 27.5 | > 2031 | bay 56.5 | 5.0 |
| 38 | LYONS_C | NORTH | 138 | LYONS | COLLE_JUPIT_1 (export, +0.15, 15) | 44.9 | 21.9 | 3.4 | 26.9 | > 2031 | bay 56.5 | 4.5 |
| 39 | CENTRVIL | NORTH | 138 | CENTRVIL_L_A | COLLE_JUPIT_1 (export, +0.16, 14) | 44.3 | 21.9 | 2.7 | 27.0 | > 2031 | bay 56.5 | 4.4 |
| 40 | LWSVW | NORTH | 138 | TNLWVLWEST1S | 587__A (export, +0.16, 19) | 40.7 | 26.8 | −0.8 | 20.9 | 2028 | bay 56.5 | 0.2 |

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
