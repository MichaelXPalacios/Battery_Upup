# ERCOT physical-grid battery siting: build prompt

Extend the battery arena in `docs/research/arena/` from load zones to the physical ERCOT grid.

**Goal.** Find where on the Texas grid batteries earn the most after placement and add the most reliability, given the
physical limits of transformers, lines, substations and transmission interfaces. Earnings mean $/kW-yr and $/MWh
discharged, with our five algorithms doing the dispatch. Also find which grid upgrades would change those answers.

**Scope.**
- Tier 1: transmission-connected batteries of 10–300 MW, 2 h and 4 h, at 69–345 kV substations and resource nodes.
- Tier 2: distribution-connected batteries under 10 MW, and home-battery fleets, at distribution substations and feeders.
- Public data only.

Work in `C:\Users\Javier\Repo\BaseP\hugging-base`. Start in plan mode and stop at each gate.

## 1. What already exists (reuse it, don't rebuild it)

### Arena (zone level)

| Path under `docs/research/arena/` | What it holds |
|---|---|
| `arena/config.py` | The 19 regions: ERCOT LZ_NORTH, LZ_HOUSTON, LZ_SOUTH, LZ_WEST, LZ_AEN, LZ_CPS, LZ_LCRA, LZ_RAYBN, plus 11 NYISO zones. ERCOT reference is HB_BUSAVG. Also weather stations and battery `SPECS`: home is 1.875 MWh/MW with a 20% hard reserve and 0.89 round trip; grid is 2 MWh/MW with 0.85. Wear is $20/MWh. Intervals are 15 min, 96 a day, with actions from −2 to +2. |
| `arena/algorithms.py` | The `Policy` interface (`begin_day`, `act`, `observe`, `end_day`) and the algorithms A1 Clockwork, A2 Forecaster, A3 WeatherRegime, A4 SpikeHunter and A5 Adaptive. `run_combined` is the production path. |
| `arena/data.py` | `Market` (`P` is days × lanes × 96, plus `ref`). New nodal lanes plug in here. |
| `arena/siting.py` | `allocate` and `portfolio`: 10 MW a year, 1 MW at a time. Caps are per option only. |
| `arena/forward.py` | `kappa_schedule` and `Futures.adjust` model transmission projects as zone-premium multipliers (Permian 765 kV, DFW large loads, Valley import). Also the per-zone `OUTAGES` table and the `forced` hook. |
| `arena/economics.py` | `PROGRAMS` and `feeder_upgrade_cost`, a single flat $16.1/kW-yr. |
| `README.md` | Results: the learner wins. The ERCOT backtest is $38.1/kW-yr energy only and the 2027–31 forward run is $90.3/kW-yr. Caveats: no ancillary services, nothing nodal. |

Also read `docs/research/ercot.md`:
- the GTC list;
- the top 2025 constraints by congestion rent (Vealmoor–Koch Tap, Twin Buttes–Hargrove, Bell County–Salado, WESTEX,
  Laredo VFT, STP–W.A. Parish, Argyle–Lewisville);
- the facts on queues and large loads;
- the §8 to-do list.

Raw data and the venv live in `../research-data/`. The venv is `.venv` on Python 3.14. The folder
holds zonal prices, weather, gas and `arena_cache/`.

### Physical-grid pieces (distribution, plus archived transmission material)

- **`sim/feeder.py`, `sim/caps.py`, `sim/surrogate.py`, `sim/tiers.py`.** The OpenDSS SMART-DS feeder, which is synthetic
  and labelled as an Oncor-suburb stand-in at LZ_NORTH. Also transformer headroom and the 100/110/150% tiers.
- **`sim/planner.py`.** Per-transformer hosting capacity (naive, aware, paper, up), upgrade cost and `upgrade_ranking`.
- **`sim/referee.py`.** The pattern where a fast screen proposes and OpenDSS judges.
- **`previous-work/simulators/rz/research/evidence/research-20260925/ercot-live-20260925/n1-np4-160/SP_List_EB_Mapping/Settlement_Points_09022026_133322.csv`.**
  The ERCOT NP4-160 mapping: electrical bus → node, PSS/E bus, kV, SUBSTATION, load zone, resource node and hub. It has
  19,376 rows and no coordinates. The sibling files are `NOIE_Mapping_*.csv` and `Resource_Node_to_Unit_*.csv`.
- **`previous-work/simulators/rz/research/site/ems/n1-build.py`.** A parser for NP6-86-CD SCED binding constraints. Its
  data, one day (25 Sep 2026), is in `.../ercot-live-20260925/n1-np686-raw/`, next to the mapping folder above: from/to
  station, kV, limit, flow and shadow price. Its paths are hardcoded for macOS.
- **`previous-work/simulators/rz/research/capacity-planner/DATA-GRID-ASSETS.md` and `DATA-INTERCONNECTION.md`.** Source
  surveys: Oncor SAIFI/SAIDI for 3,279 feeders, the 16 TAC §25.211 screens and Austin Energy's 90% transformer rule.
- **`previous-work/docs-history/headroom/research_notes/simulator_public_datasets_and_tools.md`.** Notes on OSM Overpass,
  HIFLD mirrors and TAMU ACTIVSg2000.

Nothing in the repo models ERCOT transmission. There is no bus or line network, no shift factors, no nodal prices, no
substation inventory and no GIS. That is the gap this work fills.

## 2. Metrics

Report these for every candidate site:
- $/kW-yr, the arena's unit, for each of A1–A5 and the learner;
- $/MWh discharged (margin per MWh);
- ancillary-service $/kW-yr as its own line;
- value after saturation and after planned upgrades;
- all-in connection cost and lead time;
- a reliability score.

Market value and reliability value are kept separate and never added unless a named VoLL constant converts one to the
other.

## 3. Physical constraints to map

Add to this list as you find more. For each item, record three things: what public data shows it, how it caps or raises
battery value, and how you model it (or why you can't).

**Transmission (69–765 kV)**
1. Line thermal limits: normal, emergency and 15-minute ratings, including ambient-adjusted and dynamic ratings.
2. 345/138 kV autotransformers. They are frequent binding elements, especially when one of a parallel pair is out.
3. Generic Transmission Constraints and stability interfaces: WESTEX, Panhandle, Valley Import/Export, South Texas
   Export, Nelson Sharpe–Rio Hondo, North Edinburg–Lobo, Hamilton and the 2025 Far West import GTCs.
4. Contingency pairs. A binding constraint means "this monitored element, if that element is lost". Cover N-1, N-1-1
   and beyond-N-1 events: Uri (Feb 2021), the Houston derecho (May 2024) and Beryl (Jul 2024).
5. Voltage-stability and reactive-power limits in the Valley, Houston import and Far West oilfield load.
6. Low system strength (short-circuit ratio) in West Texas and the Panhandle, and the ride-through and grid-forming
   rules for new inverter-based resources.
7. Remedial action schemes and constraint mitigation plans that trip or limit resources.
8. Planned and forced transmission outages, including the spring and fall maintenance seasons that create short-lived
   congestion.
9. DC ties and Mexico: Laredo VFT, Railroad, Eagle Pass, East and North.
10. Radial load pockets fed by one or two paths, which raises islanding and load-shed exposure.

**Substation and equipment**
11. Power-transformer MVA and firm capacity, meaning capacity with the largest unit out.
12. Free bays and breaker positions, bus configuration (ring bus, breaker-and-a-half) and room inside or beside the fence.
13. Breaker interrupting duty, protection coordination and transfer-trip needs.
14. Equipment age, spare availability, and the lead time and cost of large power transformers (often 2–4 years).
15. Gen-tie distance and right of way to the point of interconnection.

**Distribution (Tier 2)**
16. Feeder hosting capacity: thermal, voltage, protection, reverse flow through the substation transformer, and the
    tap changer and regulators.
17. Service-transformer kVA, using the existing `sim/planner.py` logic.
18. Texas screens: 16 TAC §25.211 (15% of feeder peak, 25% of fault current) and Austin Energy's 90% transformer rule.
19. Feeder ties and switching; overhead versus underground.

**Market rules at the site**
20. Where each connection type settles:
    - a transmission storage resource settles at its resource node;
    - distribution resources (DESR, DGR, SODG) have their own rules;
    - ADER fleets are paid the load-zone price.
    Cite the protocol section for each. This decides whether a site's congestion value is paid to the battery at all.
21. Basis between node, hub and load zone, day-ahead versus real-time, and CRRs as a hedge.
22. RTC+B, live since 5 Dec 2025: ancillary services are now co-optimised in real time. Cover how saturated each
    ancillary price is and the state-of-charge rules for carrying ancillary services.
23. How storage charging is charged for energy and for transmission (4CP). Confirm the current rules.
24. Saturation:
    - operating and queued storage behind the same constraint cannibalises the spread (sources: GIS report
      PG7-200-ER, EIA-860M);
    - large loads move congestion (Batch Zero, SB 6).
25. Price-taker limit. A 200 MW battery in a thin pocket moves its own price, so cap MW per node by the constraint's
    usual headroom.
26. Interconnection process and cost. Under ERCOT's connect-and-manage model, the generator pays for its direct
    interconnection facilities, the TSP builds network upgrades, and the risk is curtailment rather than an upgrade
    bill. Verify this against 16 TAC §25.195 and Planning Guide §5. Also cover the FIS timeline.

**Site and environment**
27. Flood (FEMA NFHL), hurricane and storm surge on the coast, wildfire (Panhandle, Hill Country), and extreme cold and
    heat (HVAC derating, line sag).
28. Land: parcel size, slope, road access and easements. Also city ordinances and NFPA 855 setbacks.
29. A communications path for ERCOT telemetry, and environmental and cultural review such as endangered-species habitat.
30. Induced seismicity in the Permian. Flag it only.

**Reliability (the second goal)**
31. Load-shed exposure, from:
    - radial pockets;
    - under-frequency load-shed feeders;
    - past outages by county (EAGLE-I, CC BY);
    - Oncor feeder SAIFI/SAIDI;
    - critical facilities such as hospitals and water plants.
32. Backup value: MWh of unserved energy avoided in historical events, and hours of critical-load backup.

**"Several hops from a cap".** For every substation, compute two distances to each frequently binding element:
- the topological hop count;
- the electrical distance, as an estimated shift factor.

A site is a sweet spot when all of these hold:
- It is on the side of the constraint that captures the price separation. On the import side the battery sells high;
  on the export side it charges low.
- Its shift factor is large.
- The same constraint doesn't curtail the battery's own injection.
- The element has no upgrade already scheduled.

## 4. Data to pull (public)

Cache everything under `../research-data/grid/`, outside git. Keep a manifest of every download
with report ID, date and SHA-256, because ERCOT allows only 3 downloads of the same report per 12 months through its
API. Check each report ID on the ERCOT MIS page before downloading. If an ERCOT Public API key is needed, stop and ask
the user. Never put a key in a URL or commit one.

**ERCOT**
- **NP4-160-SG**, the settlement point to electrical bus mapping. We have a Sep 2026 copy; get the current one.
- **Binding constraints:** NP6-86-CD (SCED shadow prices and binding constraints) and NP4-191 (DAM shadow prices).
- **Prices:**
  - NP6-905-CD, settlement point prices at resource nodes (15 min);
  - NP6-787, LMPs by electrical bus (5 min), for a smaller window;
  - the SCED System Lambda report (look up its ID on the Real-Time Market page);
  - NP4-183, DAM hourly LMPs.
  Go back as far as the public archive allows, aiming for 2023–2026.
- **Ancillary-service prices,** including real-time ancillary prices after RTC+B.
- **Queues:** PG7-200-ER (generation interconnection status) and the large-load interconnection status reports.
- **Planned transmission:**
  - Transmission Project and Information Tracking (TPIT);
  - the Regional Transmission Plan;
  - the annual Constraints and Needs report;
  - the RPG project list;
  - the Permian Basin reliability plan and the 765 kV plan.
- **Outages:** public transmission outage reports.
- **Market monitor:** the Potomac Economics State of the Market, for top constraints.

**Geography and assets**
- Substations and lines: HIFLD mirrors (HIFLD Open shut in Aug 2025) and OSM Overpass (`power=substation|line|transformer`
  with voltage tags).
- EIA-860 and 860M: generators and storage with coordinates, including planned units.
- PUCT CCN filings, which give routes and in-service dates for new lines and substations.
- FEMA NFHL, USFS wildfire hazard potential, NOAA storm events, EAGLE-I county outages, and Oncor feeder SAIFI/SAIDI.

**Placing ERCOT buses on a map.** NP4-160 has substation codes but no coordinates. Join on:
- (a) resource node → unit (`Resource_Node_to_Unit`) → EIA-860 plant coordinates;
- (b) substation name, kV and county against HIFLD and OSM;
- (c) the station names in binding-constraint records.

Record the match method and a confidence for each substation.

## 5. Method

### Phase 0: orient (no downloads)
1. Read every file in §1.
2. `docs/research/` is untracked in git. Ask the user whether to commit it on a branch first.
3. Write `docs/research/grid/PLAN.md`: data list, estimated download sizes, and what you will reuse.

**Gate A:** show the user the plan and the download list. Wait for a go-ahead.

### Phase 1: data and network graph
- Pull the data in §4.
- Build a substation graph: nodes are substations (kV, coordinates, zone, match confidence); edges are lines (kV,
  length).
- Attach ERCOT electrical buses and binding constraints to graph elements.
- Build a constraint table with one row per monitored element and contingency:
  - hours binding;
  - mean and p95 shadow price;
  - congestion rent;
  - which side imports and which exports;
  - upgrade status in TPIT.

### Phase 2: electrical distance from public data
- ERCOT LMPs have no loss component, so the congestion component is about LMP minus system lambda.
- Estimate a shift factor per bus per constraint:
  - regress the congestion component on constraint shadow prices;
  - use a sparse or regularised fit, on constraints that bind often enough;
  - hold out a month to validate;
  - report fit quality per bus.
- Compute graph hop counts alongside the shift factors.
- TAMU ACTIVSg2000 is synthetic. Use it only to check the method, never to rank real sites.

**Gate B:** show the constraint table, the validation results and a map of matched substations. Wait for a go-ahead
before any backtest.

### Phase 3: site value (Tier 1)
- **Candidates:** substations and resource nodes at 69 kV or above with a usable shift factor to at least one frequently
  binding constraint, plus hub and zone controls.
- **Dispatch:** build 15-minute nodal price lanes and feed them through `Market` into `run_combined`, so A1–A5 dispatch
  at every node. Reuse the walk-forward backtest for the years that have nodal data. Add a 4-hour spec next to the
  2-hour `grid` spec.
- **Ancillary services:** add an RTC+B-era revenue line as a separate, clearly labelled layer. The arena is energy-only.
- **Saturation:** shrink each constraint's shadow prices as queued storage behind it comes online.
- **Planned upgrades:** generalise `kappa_schedule` from zones to constraints. Each TPIT or RPG project with an in-service
  date relieves named constraints and shrinks their shadow prices. Report each site's "basis half-life".
- **Caps:**
  - add group caps to `siting.allocate`, so nodes behind the same constraint share one MW cap, since they compete for the
    same congestion;
  - add per-node caps from the price-taker limit and from point-of-interconnection capacity.
- **Cost:**
  - connection class: new bay, new substation, or gen-tie with its length in km;
  - lead time;
  - exclusions for physical risk.

### Phase 4: upgrades
- Take the top ~15 binding elements. For each, record:
  - the upgrade type: reconductor, new or parallel autotransformer, dynamic ratings, or new line;
  - a cost range from a public unit-cost guide (MISO Transmission Cost Estimation Guide, ERCOT RTP figures);
  - lead time;
  - whether it is already planned.
- For each upgrade, compute two things:
  - (a) the battery MW and MWh needed to relieve the element in X% of binding hours, compared with the cost of the wires
    upgrade (the non-wires alternative);
  - (b) how site rankings move if the upgrade happens: who gains and who loses.
- State plainly who can make each upgrade happen in ERCOT (the TSP through RPG and CCN), and what a battery owner can and
  cannot pay for.

### Phase 5: reliability and Tier 2
- Score reliability per site from §3 items 31–32. Report it in its own units, plus $ through a named VoLL constant.
- For Tier 2, use the hosting-capacity and upgrade-ranking logic in `sim/planner.py` as the template, for distribution
  substations where public data exists.
- Settle Tier 2 at the load zone unless the resource type settles at a node (§3 item 20).
- Home fleets keep the 20% member reserve as a hard constraint.

### Phase 6: report
Write these under `docs/research/grid/`:
- `README.md`, findings first;
- `data/`: the site, constraint and upgrade tables as CSV;
- `scripts/`;
- an interactive map page (Leaflet). Publish it as a private artifact only if the user asks.

Site table columns:
- substation, kV, county, zone;
- lat/lon and match confidence;
- constraints and which side of each;
- estimated shift factor and hop count;
- $/kW-yr per algorithm and for the learner;
- $/MWh discharged;
- ancillary $/kW-yr;
- saturation-adjusted value;
- basis half-life;
- connection cost class;
- risk flags;
- reliability score;
- provenance labels.

## 6. Rules
- Deterministic code produces every rank and number. No language-model output ranks a site, sets a dispatch or picks an
  upgrade.
- Every number carries a label:
  - REAL: from a named source;
  - DERIVED: computed from REAL numbers;
  - SIM;
  - ASSUMPTION.
  Unverified constants are single named values, never inline literals.
- Public data only. ERCOT data may be redistributed in analyses, but not its logo. Respect the 3-per-12-months download
  limit. Never commit raw downloads or keys.
- Use the research venv (`research-data/.venv`). Add only packages that install cleanly on Windows with Python 3.14, such
  as pyarrow and networkx. Follow the Windows run recipe in memory.
- Leave the existing arena results unchanged.
  - New code goes in `docs/research/grid/` and imports `arena`.
  - Any hook added to `arena` (group caps, node lanes, a 4-hour spec) must be backward-compatible, and you must show that
    the old backtest numbers still reproduce.
- Stop at Gates A and B. Ask before long runs: the arena backtest takes about 50 minutes and the forward run about 40,
  on 8 cores.
- Commit early on a branch. Push only to `origin` (Battery_Upup), and only when asked.
