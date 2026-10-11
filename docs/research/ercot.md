# ERCOT: large loads, price geography and where batteries earn their keep

*Research for Batter Up, 3 October 2026. Two studies: (1) where ERCOT prices spike, what the interconnection queue says about the large-load wave, and who pays for grid upgrades; (2) what history shows when large load is added and when unplanned events go beyond N-1.*

**How to read the numbers.**
- **Computed** means I calculated it from ERCOT's own price files: NP6-785-ER, 15-minute real-time Settlement Point Prices for every load zone and hub, 1 Jan 2021 to 26 Sep 2026. The script and output tables are in [`scripts/`](scripts/) and [`data/ercot/`](data/ercot/).
- Every other number cites a published source.
- *(unverified)* marks a figure from a single secondary source or a search snippet.
- 2026 figures run to 26 Sep 2026 (269 days). Where a 2026 dollar value is "annualised", it is scaled to 365 days.

---

## Summary

1. **Get the words right.** ERCOT computes a price at every electrical bus (an *LMP*, every 5 minutes). Batter Up's homes are settled at the **LZ_NORTH load-zone Settlement Point Price**, every 15 minutes, in **$/MWh**. The "regions sectioned off" are areas behind **binding transmission constraints and Generic Transmission Constraints (GTCs)**. Prices separate across them.
2. **DFW is moving to the expensive side of the grid.** Computed:
   - LZ_NORTH's average gap to the ERCOT-wide bus average went from **−$0.06/MWh (2024) to +$2.09/MWh (2026)**.
   - In 2026 it sat more than $100 above that average in **106 intervals** and more than $100 below it in **none**.
   - The market monitor measured congestion inside the North zone doubling, from $173M to $335M (2024 → 2025).
3. **Spikes are rarer, and they now come at night and in winter mornings.** Computed: LZ_NORTH intervals at or above $1,000/MWh went 244 (2023) → 33 (2024) → 12 (2025) → 18 (2026 to date). Spike hours have moved from mid-afternoon (2022) to 7–9 pm (2023–24), and now to nights and winter mornings (2025–26).
4. **Battery money is smaller and lumpier.**
   - Computed upper bound: a perfect-foresight 1 MW / 2 MWh battery at LZ_NORTH could have earned **$171/kW-yr in 2023, $63 in 2024, $51 in 2025, and $54 (annualised) in 2026**.
   - Real fleets earn less. Modo's ERCOT battery index was about $29/kW-yr in 2025.
   - The 10 best days produce **21–55%** of a year's value.
   - **In 2026, LZ_NORTH is essentially tied with LZ_WEST for the best-paying competitive load zone** ($53.8 vs $54.3/kW-yr annualised), about $13/kW-yr ahead of LZ_HOUSTON.
5. **The large-load wave runs through Oncor.**
   - Oncor holds about **259 GW of ERCOT's ~411 GW** of large-load requests (March 2026).
   - Only about **5.9 GW** has actually energised.
   - The 2032 "Batch Zero" study puts **40.4 GW in North Central (DFW)**.
   - On 3 Aug 2026 the Governor paused new data-centre and crypto energisations of 75 MW or more.
6. **Where batteries help:**
   - the evening net peak and winter mornings
   - local pockets behind constraints
   - overloaded service transformers and feeders
   - backup power when a distribution line fails
   - fast response when a large load trips off
7. **Texas upgrade money is thin but opening.**
   - **SB 415** lets Oncor *contract* for (not own) storage that improves distribution reliability, capped at 100 MW statewide. The rule was proposed in April 2026.
   - **ADER**, ERCOT's home-battery aggregation pilot, now has a 500 MW cap.
   - SB 6, the PUCT's new large-load rule and the Governor's directives all push data centres to pay for their grid impact. That opens a market for selling *locational flexibility*.
   - Federal §48E still credits third-party-owned storage for projects that start construction by 2033. §25D, the homeowner credit, ended on 31 Dec 2025.
8. **History (study 2a).** Large load raised prices where supply and wires could not keep up.
   - PJM: capacity prices rose about 10×. The market monitor attributes about 46% of $63.6B in capacity costs over four auctions to data centres.
   - ERCOT's West zone: Permian and crypto load grew about 12% a year, and the zone carried a computed premium of **+$9 to +$11/MWh in 2023–25**.
   - Where supply came faster, prices fell. ERCOT's 2024 prices dropped about half even as load grew.
   - Large loads also add a new kind of reliability risk: they trip offline in groups during faults. ERCOT has recorded 26–28 such events, and Northern Virginia has had losses of 1.5–3.8 GW.
9. **Unplanned events (study 2b).** The worst events split into two kinds.
   - **System-wide scarcity**, such as Uri and 6 Sep 2023, produced the price spikes. Uri averaged a computed **$5,691/MWh** at LZ_NORTH for a week.
   - **Distribution disasters**, such as the Houston derecho and Beryl, produced the outages but no price spike: the computed LZ_HOUSTON maximum during Beryl was **$54/MWh**.
   - Grid batteries earn in the first kind. Only batteries behind the meter protect customers in the second. Batter Up's design, with home backup plus feeder awareness, is built for the second and can still bid into the first.

---

## 1. Vocabulary: say it the ERCOT way

| What you said | What to say | Why |
|---|---|---|
| "nodal zones" | **load zones** for what customers pay, **nodes / electrical buses** for where prices are computed | SCED, ERCOT's real-time dispatch engine, computes a Locational Marginal Price (LMP) at each electrical bus every 5 minutes. Load pays its **load zone's Settlement Point Price (SPP)**, which is the load-weighted average of that zone's nodal prices. A load zone is not a node. |
| "MW/hr in $" | **$/MWh** | Energy is priced per megawatt-hour. MW is power, a rate; MWh is energy, an amount. "MW per hour" would describe a ramp rate. Ancillary services are priced in **$/MW per hour**, for holding capacity in reserve. |
| "regions sectioned off where prices go up and down" | areas behind a **binding constraint** or **GTC**, which creates **price separation** (or **basis**); **import-constrained** pockets price high, **export-constrained** pockets price low | When a line or a GTC hits its limit, its **shadow price** pushes prices up on the importing side and down on the exporting side. |
| "price spikes" | **scarcity pricing** (system-wide), or **congestion spikes** (local) | System-wide scarcity is capped at **$5,000/MWh**. Congestion alone can push a single node much higher: the Rabbit Hill battery node priced near $30,000/MWh on 19 Feb 2025 ([Grid Status](https://blog.gridstatus.io/exploring-extreme-prices-in-ercot-with-grid-status/)). |

**The pieces** ([IMM 2025 ERCOT State of the Market](https://www.potomaceconomics.com/wp-content/uploads/2026/06/2025-State-of-the-Market-Report-for-ERCOT.pdf); [ERCOT NP6-905](https://www.ercot.com/mp/data-products/data-product-details?id=NP6-905-CD)):

| Term | What it is |
|---|---|
| Electrical bus / LMP | A point on the network. Its 5-minute LMP is the system energy price plus congestion. ERCOT's LMPs carry no separate loss component. |
| Resource Node | The bus where a generator or battery is settled. |
| Settlement Point / SPP | Any priced location: Resource Nodes, Load Zones and Hubs. The real-time SPP is a 15-minute value built from the 5-minute LMPs. Day-ahead SPPs are hourly. |
| **Load zones** | Four competitive zones: **LZ_NORTH, LZ_HOUSTON, LZ_SOUTH, LZ_WEST**. Four zones belong to municipal and co-op utilities (NOIEs): **LZ_AEN** (Austin), **LZ_CPS** (San Antonio), **LZ_LCRA**, **LZ_RAYBN** (Rayburn Country co-op, east of DFW). The competitive zones were drawn in 2003, and the market monitor recommends redrawing them. |
| **Trading hubs** | HB_NORTH, HB_HOUSTON, HB_SOUTH, HB_WEST, **HB_PAN** (Panhandle), HB_BUSAVG and HB_HUBAVG. Hubs exist for trading and hedging; nobody's load settles at a hub. In this study **HB_BUSAVG** stands for the ERCOT-wide average. |
| Weather zones | Eight zones (Coast, East, Far West, North, North Central, South Central, Southern, West) used **only for load forecasting**. They do not line up with load zones. DFW sits in North Central. |
| GTCs | Named limits on flow between regions: West Texas Export (WESTEX), Panhandle, Nelson Sharpe–Rio Hondo and North Edinburg–Lobo (Rio Grande Valley), Valley Import/Export, South Texas Export, Hamilton, and two new Far West import GTCs added in 2025. |
| Caps and timing | The system-wide offer cap is $5,000/MWh. Real-Time Co-optimization (RTC+B) went live on 5 Dec 2025 and now buys ancillary services in real time. |
| How a home fleet is paid | **ADER aggregations are dispatched and settled at the load zone.** For Batter Up that means LZ_NORTH. Congestion on individual DFW lines does not pay the fleet directly. |

**Loose wording in this repo, for the team to fix:**
- `docs/research-report.md` calls LZ_AEN "Austin's price node". It is Austin's **load zone**.
- `ui/panels/more.js` says ERCOT "dispatches one number per load zone". ERCOT dispatches each resource and prices each node; the load-zone price is an average of nodal prices.
- The research report's data table still says north Austin is served by Austin Energy. `docs/data-sources.md` was corrected on 26 Sep to Pedernales Electric Cooperative.

---

## 2. Where ERCOT prices spike (computed, 2021–2026)

### 2.1 Average real-time price by zone

These are time-weighted averages of 15-minute SPPs in $/MWh. They run a few dollars below the market monitor's load-weighted figures (see the note under the table).

| Location | 2021 | 2022 | 2023 | 2024 | 2025 | 2026 YTD |
|---|---:|---:|---:|---:|---:|---:|
| **LZ_NORTH** (DFW) | 150.6 | 62.9 | 48.7 | 26.4 | 32.7 | 34.0 |
| LZ_HOUSTON | 151.8 | 70.0 | 49.8 | 27.2 | 33.2 | 33.6 |
| LZ_SOUTH | 147.5 | 59.9 | 45.1 | 30.4 | 33.5 | 32.5 |
| LZ_WEST | 148.0 | 62.4 | **59.4** | **35.7** | **42.2** | 32.7 |
| LZ_AEN (Austin) | 152.1 | 66.0 | 53.8 | 29.3 | 35.9 | 33.9 |
| LZ_CPS (San Antonio) | 150.7 | 64.5 | 50.7 | 28.9 | 35.5 | 33.5 |
| LZ_LCRA | 152.1 | 65.5 | 52.7 | 29.7 | 37.5 | 34.3 |
| LZ_RAYBN | 150.2 | 63.2 | 49.0 | 26.2 | 32.7 | 34.2 |
| HB_PAN (Panhandle) | 139.4 | 52.0 | 42.2 | 19.7 | 23.8 | 25.3 |
| HB_BUSAVG (ERCOT-wide) | 148.7 | 62.2 | 48.1 | 26.5 | 32.3 | 31.9 |

*Cross-check:* the market monitor's **load-weighted** North zone averages are 221.52, 77.30, 78.01, 30.42 and 35.34 for 2021–2025. Load-weighting gives the expensive hours more weight because demand is high then ([IMM 2025, Table 1](https://www.potomaceconomics.com/wp-content/uploads/2026/06/2025-State-of-the-Market-Report-for-ERCOT.pdf)).

### 2.2 Spikes and negative prices

| Location | Intervals ≥ $1,000/MWh: 2021 / 22 / 23 / 24 / 25 / 26 | Share of intervals below $0: 2023 / 24 / 25 / 26 |
|---|---|---|
| **LZ_NORTH** | 647 / 135 / 244 / 33 / 12 / **18** | 1.6% / 2.2% / 2.5% / 3.4% |
| LZ_HOUSTON | 669 / 173 / 247 / 31 / 7 / 2 | 1.4% / 1.6% / 1.7% / 1.6% |
| LZ_SOUTH | 632 / 110 / 180 / 45 / 19 / 1 | 2.2% / 2.9% / 2.5% / 2.5% |
| LZ_WEST | 657 / 143 / 255 / 32 / 12 / 12 | 7.8% / 8.1% / 5.6% / 12.4% |
| LZ_RAYBN | 651 / 138 / 244 / 32 / 12 / **28** | 1.5% / 2.0% / 2.5% / 3.2% |
| HB_PAN | 645 / 134 / 240 / 32 / 12 / 16 | **17.8% / 23.1% / 19.9% / 18.8%** |

In 2021, Winter Storm Uri held prices at the then $9,000 cap for days, which dominates that year. The Panhandle is export-constrained and prices below zero about one interval in five. LZ_NORTH and LZ_RAYBN, the DFW area and its eastern neighbour, now have the most $1,000+ intervals of any load zone.

### 2.3 The "sectioned-off" regions: separation from the ERCOT-wide price

Basis here means the zone price minus HB_BUSAVG. Each cell shows the mean basis in $/MWh, followed by (intervals more than $100 above / more than $100 below).

| Location | 2021 | 2022 | 2023 | 2024 | 2025 | 2026 YTD |
|---|---|---|---|---|---|---|
| **LZ_NORTH** | +1.95 (142/16) | +0.71 (72/86) | +0.55 (122/6) | −0.06 (29/9) | +0.39 (37/4) | **+2.09 (106/0)** |
| LZ_RAYBN | +1.55 (91/17) | +1.03 (201/96) | +0.81 (153/6) | −0.24 (10/9) | +0.44 (44/5) | **+2.29 (109/0)** |
| LZ_WEST | −0.68 (236/29) | +0.14 (443/125) | **+11.28 (887/29)** | **+9.24 (496/19)** | **+9.91 (230/9)** | +0.84 (68/36) |
| HB_PAN | −9.25 (81/101) | −10.21 (52/213) | −5.98 (77/112) | −6.80 (20/86) | −8.44 (10/109) | −6.56 (30/83) |
| LZ_HOUSTON | +3.10 (166/77) | **+7.84 (388/1)** | +1.61 (95/9) | +0.73 (51/26) | +0.92 (29/74) | +1.74 (21/71) |
| LZ_SOUTH | −1.16 (121/187) | −2.32 (168/188) | −3.01 (173/256) | +3.96 (337/21) | +1.23 (101/39) | +0.65 (102/73) |
| LZ_LCRA | +3.48 | +3.25 | +4.58 | +3.25 | +5.19 | +2.42 |

What these mean:
- **Permian / Far West (import-constrained).** LZ_WEST carried a +$9 to +$11/MWh premium in 2023–25. West zone demand rose 12.3% in 2025, from oil and gas operations and crypto mining. Constraints there topped the 2025 congestion list: Vealmoor–Koch Tap at $211M, and Odessa EHV–Yarbrough. The premium nearly vanished in 2026 to date. My data shows the drop; the cause still needs a source.
- **Panhandle (export-constrained).** Wind and solar outrun the export lines, so HB_PAN sits $6–10/MWh below the ERCOT average and goes negative a fifth of the time.
- **Rio Grande Valley.** The Nelson Sharpe–Rio Hondo and North Edinburg–Lobo GTCs are among the most frequently binding constraints. LZ_SOUTH swings both ways because the Valley imports while South Texas wind exports.
- **Houston (import-constrained).** Its large premium in 2022 (+$7.84) has since faded. STP–W.A. Parish is still a top-10 constraint.
- **DFW (North), the new one.** The zone moved to a consistent premium in 2026. The market monitor reports more North-zone congestion, much of it managed with reliability unit commitments, and lists **Argyle–Lewisville** in Denton County among the top 10 constraints for 2025. That constraint cost $51M, mostly in Oct–Dec 2025. Under the monitor's proposed seven-zone map, North congestion rent was $173M in 2024 and $335M in 2025 ([IMM 2025](https://www.potomaceconomics.com/wp-content/uploads/2026/06/2025-State-of-the-Market-Report-for-ERCOT.pdf); [Dec 2025 monthly](https://www.potomaceconomics.com/wp-content/uploads/2026/01/2025-12_Nodal_Monthly_Report.pdf)).

**Top real-time constraints in 2025 by congestion rent** (IMM). Locations are inferred from substation names.

| # | Constraint | Rent | Area |
|---|---|---|---|
| 1 | Vealmoor–Koch Tap | $211M | Permian |
| 2 | Twin Buttes–Hargrove | $145M | San Angelo |
| 4 | Bell County–Salado | $94M | I-35, Central Texas |
| 5 | WESTEX GTC | $89M | West Texas export |
| 8 | Laredo VFT N–Las Cruces | $61M | Laredo |
| 9 | STP–W.A. Parish | $52M | Houston import |
| 10 | **Argyle–Lewisville** | **$51M** | **Denton County (DFW)** |

Real-time congestion rent was about **$2.5B in 2025, up 32%**. About 91% of it arose *within* zones, not between them. That is why the zone-level averages above understate how big the local swings are.

### 2.4 When spikes happen (LZ_NORTH, intervals ≥ $250/MWh, computed)

| Year | Count | 00–06 | 07–12 | 13–16 | 17–18 | **19–21** | 22–24 | Busiest months |
|---|---:|---:|---:|---:|---:|---:|---:|---|
| 2021 | 990 | 18% | 21% | 20% | 15% | 17% | 9% | Feb (Uri) |
| 2022 | 551 | 3% | 13% | 28% | 22% | 32% | 2% | Jul, Aug |
| 2023 | 586 | 0% | 3% | 16% | 25% | **54%** | 1% | Aug, Sep |
| 2024 | 180 | 3% | 14% | 6% | 14% | **62%** | 2% | Jan, May |
| 2025 | 116 | 6% | 37% | 0% | 0% | 36% | 21% | Feb, May |
| 2026 | 228 | **24%** | 6% | 1% | 8% | 42% | 19% | **Jan (Fern)**, Sep |

Hours are hour-ending, local time. Solar wiped out afternoon scarcity. Batteries then cut the 7–9 pm peak. What remains is late evening, overnight and cold winter mornings. Those are **longer** windows, so they favour batteries with more hours of storage (see §3).

---

## 3. Where batteries relieve the grid, and what they earn

### 3.1 Relief

- **The evening net peak.** NERC puts ERCOT's emergency risk at its highest around hour-ending 21, when solar fades.
  - On 22 Jul 2026 ERCOT set a record of about **91.1 GW**. Batteries discharged about **12 GW**, and real-time prices peaked at only about **$378/MWh**, against $5,000 in Aug 2024 ([Grid Status, Jul 2026](https://blog.gridstatus.io/ercot-record-july-2026/)).
  - NERC's 2026 summer assessment gives ERCOT just a **0.43%** chance of an energy emergency at hour-ending 21, crediting the batteries added in 2025–26 ([NERC SRA 2026](https://www.nerc.com/globalassets/our-work/assessments/nerc_sra_2026.pdf)).
- **Duration matters more each year.** ERCOT's 2024 ELCC study measured how much of a battery's capacity counts toward reliability on summer evenings, by battery duration ([ERCOT/Astrapé ELCC 2025](https://www.ercot.com/files/docs/2025/02/12/2024ERCOT_ELCC_Study_Final_Report02112025.pdf)):

  | Duration | 1 h | 2 h | 3 h | 4 h | 5 h |
  |---|---|---|---|---|---|
  | Summer-evening ELCC | 13.7% | 27.4% | 41.1% | 54.9% | 68.2% |

  Keeping a high value over the next five years takes about 5 hours of storage. A Base-style home battery (25–39 kWh at 11–20 kW) gives about **2 hours** at full power and more at partial power.
- **Pockets behind constraints:** DFW-internal constraints, the Permian, the Rio Grande Valley and Houston import.
- **Service transformers and feeders.** ERCOT dispatches by load zone and does not see distribution limits. This is the gap Batter Up's feeder-aware controller fills. Distribution transformer lead times grew from 3–6 months before 2020 to 1–2 years or more, and prices rose 78–95% since 2019 ([DOE](https://www.energy.gov/oe/distribution-transformer-webinar-text-alternative); [Wood Mackenzie](https://www.woodmac.com/news/opinion/transformer-troubles-manufacturing-and-policy-constraints-hit-us-transformer-supply/)).
- **Large-load trips.** When a data centre or crypto site trips offline, frequency jumps up. A fast battery fleet can absorb that by charging. ERCOT has recorded **26–28 events since 2023** in which large electronic loads dropped more than 100 MW each. In May 2026 it found four load groups that could each trip more than 5,000 MW ([NERC incident review](https://www.nerc.com/globalassets/our-work/reports/event-reports/incident_review_considering_voltage-sensitive_crypto_load_reductions.pdf); [mgrid, Jun 2026](https://mgrid.org/2026/06/05/ercot-says-four-data-center-and-crypto-groups-failed-a-voltage-test-each-able-to-trip-5000-mw/) *(secondary)*).

### 3.2 Money

**Installed batteries:**
- About 7.8 GW at end-2024.
- About 13.9–17 GW at end-2025, depending on who counts ([Modo](https://modoenergy.com/research/en/ercot-battery-buildout-2025-annual-report); [IMM](https://www.potomaceconomics.com/wp-content/uploads/2026/06/2025-State-of-the-Market-Report-for-ERCOT.pdf)).
- About 18 GW in Oct 2026 *(unverified)*.

Average duration is under 2 hours.

**Ancillary service prices collapsed as batteries took over** (IMM; annual average day-ahead price in $/MW per hour):

| Service | 2021 | 2022 | 2023 | 2024 | 2025 |
|---|---:|---:|---:|---:|---:|
| Regulation Up | 282.56 | 25.62 | 34.88 | 7.36 | 2.97 |
| RRS | 300.87 | 20.79 | 21.04 | 5.52 | 2.74 |
| ECRS | — | — | 73.18 | 9.56 | 2.65 |
| Non-Spin | 81.56 | 23.24 | 15.53 | 7.13 | 3.65 |

In 2025 batteries supplied 94% of Regulation Up and 51% of RRS.

**Energy-arbitrage ceiling by zone** (computed). This is a 1 MW / 2 MWh battery with 85% round-trip efficiency, one cycle a day, empty at midnight, and **perfect foresight**. Treat it as an upper bound, not a forecast. Values are $/kW-yr; 2026 is annualised.

| Location | 2021 | 2022 | 2023 | 2024 | 2025 | 2026 |
|---|---:|---:|---:|---:|---:|---:|
| **LZ_NORTH** | 107.7 | 112.6 | 170.6 | 63.0 | 51.1 | **53.8** |
| LZ_HOUSTON | 115.3 | 141.0 | 171.3 | 60.1 | 45.8 | 40.7 |
| LZ_SOUTH | 105.3 | 105.2 | 143.4 | 71.1 | 46.8 | 42.1 |
| LZ_WEST | 113.2 | 133.8 | **203.8** | **83.1** | **63.0** | 54.3 |
| LZ_RAYBN | 107.0 | 112.8 | 171.5 | 62.5 | 50.9 | **55.3** |
| LZ_LCRA | 107.7 | 124.1 | 182.9 | 70.9 | 63.8 | 46.6 |
| HB_PAN | 110.3 | 116.7 | 169.6 | 65.2 | 51.9 | 50.7 |
| *Share of LZ_NORTH value from its 10 best days* | 55% | 45% | 47% | 37% | 21% | 33% |

Reading the table:
- **Check against realised revenue.** Modo's ERCOT index, which includes ancillary services, was $193, $56 and about $29/kW-yr for 2023–25 ([Modo](https://modoenergy.com/research/en/why-were-ercot-battery-revenues-so-low-in-2025-weather-energy-arbitrage-builodout)). Modo found the 10 best days produced 38% of 2024 revenue; my LZ_NORTH figure is 37%.
- **Uri barely registers.** The battery must start each day empty, and prices sat flat at the cap for days, so there was little spread to trade. In a multi-day scarcity event a 2-hour battery sells about one full discharge. The rest of its value is reliability.
- **DFW has caught up.** In 2026 the top three are LZ_RAYBN ($55.3), LZ_WEST ($54.3) and LZ_NORTH ($53.8), with LZ_NORTH $13 ahead of Houston. In 2024 LZ_NORTH ranked below every zone except Houston and Rayburn. That matches the move to the expensive side of DFW-area constraints.

### 3.3 What this means for a DFW home fleet

1. **Prices.** Price the fleet at LZ_NORTH. Under ADER it is settled there.
2. **Size the wholesale case conservatively.** A realistic wholesale value is **$25–55/kW-yr**: realised revenue at the low end, the perfect-foresight ceiling at the high end. Model weather spikes as upside.
3. **Ancillary services are saturated.** ADER allows up to 100 MW of ancillary services.
4. **Local value is real but unpriced.** It needs a contract: SB 415, a data-centre deal, or a co-op capacity fee (see §5).

---

## 4. The large-load wave and the queue

### 4.1 Public data you can use

| Source | Publisher, cadence | What it gives you |
|---|---|---|
| [Generation Interconnection Status (PG7-200-ER)](https://www.ercot.com/mp/data-products/data-product-details?id=pg7-200-er) | ERCOT, monthly .xlsx, free | Every generation and storage project, with fuel, status, county and zone. Use it to map where batteries are being built. |
| Large-load totals | ERCOT Board and Large Load Working Group decks, about every two months | Totals by status, region and county. No project-level public file yet; ERCOT targets a Large Load Portal for end-2026. |
| [Capacity, Demand and Reserves](https://www.ercot.com/gridinfo/resource) | ERCOT, twice a year | Reserve outlook. The May 2026 edition was replaced by a capacity forecast while the load forecast is revised. |
| [Long-Term Load Forecast](https://www.ercot.com/gridinfo/load/forecast) | ERCOT / PUCT, annual | Peak and energy forecasts, now filed under PUCT rule 25.370. |
| [Queued Up](https://emp.lbl.gov/queues) | LBNL, annual, free workbook | Queues across all ISOs and many utilities. |
| [Interconnection.fyi](https://www.interconnection.fyi/) | Daily; bulk data is paid | Cross-ISO tracker, including a data-centre tracker. |
| [EIA-860M](https://www.eia.gov/electricity/data/eia860m/) | EIA, monthly | Operating and planned units. |

### 4.2 ERCOT large-load numbers

**Large-load requests over time:**

| As of | Requests | Notes |
|---|---:|---|
| Dec 2024 | 63 GW | |
| 18 Nov 2025 | 226 GW | About 73% data centres |
| 26 Mar 2026 | **410.6 GW** | 87.6% data centres; **Oncor 259 GW**, AEP 46 GW |
| Jun 2026 | 474.7 GW | A rush before the old process closed on 10 Jul 2026 |
| Batch Zero, 3 Sep 2026 | 66.4 GW base + 127.9 GW studied | 302 GW excluded |

Sources: ERCOT [Dec 2025 Board](https://www.ercot.com/files/docs/2025/12/02/16.2-System-Planning-and-Weatherization-Update_Revised.pdf), [Senate testimony Apr 2026](https://www.ercot.com/files/docs/2026/04/01/ERCOT_LargeLoad_Update_April2026_B-C_-Hearing.pdf), [Jul 2026](https://www.ercot.com/files/docs/2026/07/29/ERCOT-Senate-July-29-Panel-1-Assessing-The-Grid.pdf), [Batch Zero update](https://www.ercot.com/files/docs/2026/09/11/14-Batch-Zero-Update.pdf).

**What actually connects:**
- About **5.9 GW** has been observed energised and about 3.2 GW is approved but not yet running.
- About 3.9 GW is due in Q4 2026, of which 1.9 GW is in the North zone.
- On 3 Aug 2026 the Governor paused energisation of new data centres and crypto sites of 75 MW or more pending an audit. Reports are due 10 Dec 2026 ([Texas Tribune](https://www.texastribune.org/2026/08/03/texas-data-center-project-audit-greg-abbott/)).

**Where large loads cluster** (Batch Zero 2032, base plus studied):

| Area | Load | Notable counties |
|---|---:|---|
| **North Central (DFW)** | **40.4 GW** | Dallas has the most base load (5.7 GW); Ellis 2.3 GW |
| North (Panhandle, Childress) | 38.5 GW | Childress 6.75 GW |
| South | 24.7 GW | Cameron 12.2 GW, the largest single county |
| West (Abilene) | 22.4 GW | Jones 7.0 GW |
| Far West (Permian) | 19.0 GW | |

**ERCOT peak forecasts:**

| Forecast | Peak |
|---|---|
| ERCOT 2024 (HB 5066) | about 150 GW by 2030 |
| ERCOT-adjusted 2025 | 138 GW (2030), 145 GW (2031). Data-centre requests were cut to about half. |
| Utility-submitted 2025 | 208–218 GW |
| NERC LTRA 2025 | 94.65 GW (2026) → 154.1 GW (2035) |
| Preliminary 2026 under rule 25.370 | 278 GW (2029), 368 GW (2032). ERCOT says this is too high. |
| Actual | about 91.1 GW, 22 Jul 2026 (preliminary record) |

**Texas SB 6 (2025) for loads of 75 MW or more:**
- New loads must be curtailable or remotely disconnectable during firm load shed.
- Backup generation must be disclosed and can be called on.
- ERCOT must buy a demand-reduction service.
- Study fees and financial security are required. The PUCT's §25.194, adopted in Sep 2026, sets a $100k fee plus $50k/MW in security.
- The PUCT must review how transmission costs are allocated, including the 4CP method, by 31 Dec 2026 ([Utility Dive](https://www.utilitydive.com/news/texas-puc-adopts-data-center-interconnection-rules/830899/)).
- Separately, ERCOT's operating-guide change NOGRR282, approved by the PUCT in Jul 2026, requires loads of 75 MW or more to ride through faults. Compliance is estimated at $0.5–1M per MW ([mgrid](https://mgrid.org/2026/07/21/texas-regulators-order-data-centers-to-ride-through-grid-faults-with-compliance-costs-up-to-1-million-per-mw/) *(secondary)*).

**Generation queue (30 Jun 2026):** 462.8 GW in total, of which **storage is 170.9 GW** (about 410 GWh), solar 161.6 GW, gas 77.6 GW (up from 26 GW in Oct 2024) and wind 48.0 GW.

### 4.3 National context

- **Grid Strategies** (Nov 2025): utilities forecast +166 GW of US peak growth over 2025–30, of which about 90 GW is data centres. The authors judge that about 25 GW overstated. ERCOT accounts for more than a third of the growth ([report](https://gridstrategiesllc.com/wp-content/uploads/Grid-Strategies-National-Load-Growth-Report-2025.pdf)).
- **NERC LTRA 2025:** summer peak +224 GW over 10 years. MISO, PJM, ERCOT, SERC-Central and parts of the West are rated high risk ([NERC](https://www.nerc.com/globalassets/our-work/assessments/nerc_ltra_2025.pdf)).
- **Data centres:** 6.7–12% of US electricity by 2028 (LBNL) and 9–17% by 2030 (EPRI).
- **Duke University study (2025):** if large loads accept 0.5% curtailment, the US could add about 98 GW of load on today's system, about 10 GW of it in ERCOT ([Duke via POWER](https://www.powermag.com/duke-researchers-grid-flexibility-key-to-accommodate-load-growth/)).

### 4.4 Where batteries play over 1, 5 and 10 years (ERCOT)

| Horizon | Load | Battery build | Where the money and relief will be |
|---|---|---|---|
| **1 year** (to late 2027) | Peak about 91 GW + 2–6 GW (my estimate: 3.9 GW of large load is scheduled for Q4 2026, and data-centre energisation is paused) | About +10–13 GW | Thin arbitrage ($25–55/kW-yr). Ancillary services are saturated. **Local relief, ADER and backup** are the strongest cases for home fleets. The DFW premium is growing. |
| **5 years** (2031) | 110–145 GW peak | 25+ GW ([NERC](https://www.nerc.com/globalassets/our-work/assessments/nerc_ltra_2025.pdf)) | Scarcity returns if NERC's high-risk rating from 2029 holds. Value shifts to **4–5 hour** storage and to winter mornings. Flexible and "bring-your-own-capacity" interconnection for data centres. Transmission cost reform changes the economics of 4CP avoidance. |
| **10 years** (2035–36) | About 154 GW (NERC); my range 130–180 GW | No authoritative figure | Value tied to duration and location. §48E phases down for projects starting in 2034–35. Local constraints keep moving as large loads land. |

The main uncertainties are inflated forecasts (data-centre requests realise at about 50%), policy (the Governor's audit, SB 6 cost allocation, the foreign-entity rules on battery cells), transformer supply, and saturation.

---

## 5. Who pays for upgrades: deferrals, credits and partnership routes

### 5.1 Texas

| Program | Who gets paid | For what | Status |
|---|---|---|---|
| **SB 415 / proposed 16 TAC §25.58** | A storage owner (registered power generation company) under contract with a wires utility such as Oncor | Distribution reliability, if cheaper than the wires alternative. The utility must run an RFP. The owner can sell leftover capacity into ERCOT. | 100 MW statewide, split by load share. Proposed 17 Apr 2026 ([Texas Register](https://www.sos.state.tx.us/texreg/archive/April172026/Proposed%20Rules/16.ECONOMIC%20REGULATION.html)). Adoption *(unverified)*. |
| **ADER pilot** | Aggregators, now including third-party schedulers | Energy, Non-Spin, ECRS | 500 MW cap since 2 Mar 2026; 100 MW for ancillary services ([ERCOT notice](https://www.ercot.com/services/comm/mkt_notices/M-A030226-01)). About 108 MW qualified by Feb 2026 *(unverified)*. |
| Co-op / municipal capacity deals | Battery fleet owner | Capacity and peak (4CP) relief | Co-ops and munis may own or contract storage freely (SB 1012, 2019). Example: Base Power and GVEC's 2 MW VPP ([Utility Dive](https://www.utilitydive.com/news/base-power-gvec-texas-vpp-virtual-power-plant/752102/)). |
| Texas Backup Power Package | Critical facilities | Islanded backup (gas/propane + battery + solar, 48 h) | $1.8B, at most $500/kW. **No grid export or market participation allowed**, so it cannot be used for a VPP (adopted 7 May 2026). |
| Texas Energy Fund | Dispatchable generation ≥ 100 MW | Loans, completion bonuses | **Storage excluded** by statute. |
| Utility resiliency plans (HB 2555) | Wires utilities (through rates) | Hardening, automation, vegetation | Oncor about $3B (2025–27); CenterPoint about $2.7B (2026–28). **No storage** in either. |
| SB 6 / §25.194 | Paid **by** large loads | Interconnection studies, security | $100k fee + $50k/MW (Sep 2026) |
| Ch. 312 abatements / JETI (Ch. 403) | Projects | Property-tax relief | JETI excludes storage and data centres. Ch. 312 depends on the local government. |

**Who pays when a data centre needs a new transformer or line in Oncor territory.**
- **Dedicated facilities:** the customer pays up front (a contribution in aid of construction).
- **Shared transmission:** the cost goes into rates and is spread across ERCOT.

SB 6's cost-allocation review and the Governor's June and August 2026 directives push the second category back onto data centres. That makes "pay a battery fleet instead of a wire" an attractive trade for them.

### 5.2 Federal (brief)

- **§48E storage investment credit** survived the July 2025 OBBBA law for standalone storage.
  - Full credit for projects starting construction by 2033, 75% for 2034, 50% for 2035.
  - Base 30%, plus 10-point bonuses for domestic content and energy communities.
  - The credit can be sold to another taxpayer.
  - **Third-party-owned home batteries qualify.**
  - Foreign-entity ("FEOC") rules require a rising share of non-foreign content: 55% in 2026, rising to 75% from 2030.
- **§25D (homeowner) ended 31 Dec 2025.**
- **DOE SPARK** grants ($1.9B, 31 projects chosen 24 Sep 2026) target reconductoring and grid-enhancing technologies, not storage.
- DOE's loan office was renamed **Energy Dominance Financing** in 2025, with $250B of authority through Sep 2028.

FERC programs (Order 1920, storage as a transmission asset) do not apply inside ERCOT ([Tax Law Center](https://taxlawcenter.org/blog/navigating-obbba-phaseouts-prohibited-foreign-entity-rules-and-other-new-rules); [DOE SPARK](https://www.energy.gov/articles/energy-department-announces-speed-power-investments-across-26-states-lower-electricity)).

### 5.3 What utilities elsewhere pay to defer upgrades

| Program | What it pays | Result |
|---|---|---|
| ConEd Brooklyn-Queens Demand Management (NY) | $200M program | 52 MW deferred a $1.2B substation |
| ConEd Distribution Load Relief (NY, 2025) | $18–25/kW-month + $1/kWh | Network relief |
| Green Mountain Power BYOD (VT) | $850–950/kW up front, **+$100/kW in constrained areas** | A location-based price |
| ConnectedSolutions (MA) | $275/kW per summer | Peak reduction |
| APS Punkin Center (AZ) | 2 MW / 8 MWh battery | Deferred about 20 miles of line rebuild for 5–10 years at about half the cost |
| Portland General Electric with Aligned Data Centers (OR) | Data-centre-funded 31 MW / 62 MWh battery | About 80 MW of data centre connected years early |
| Xcel Capacity*Connect (MN) | 200 MW, $430M of utility-owned distributed batteries | Approved 2 Apr 2026 |

Typical **distribution deferral value** in NWA studies is **$43–60/kW-yr**. The 25-study average is $48, but the best sites are worth far more ([PA PUC 2026](https://www.puc.pa.gov/pcdocs/1855615.pdf)). A rule of thumb: deferring a $1M upgrade by one year is worth about $46k at an 8% discount rate with 3% cost escalation.

### 5.4 Four routes for a battery company in Oncor territory

1. **SB 415 contracts.** Once §25.58 is adopted, bid into Oncor's RFPs for feeders and substations where batteries beat a wires upgrade.
2. **Sell locational flexibility to data centres.** A data centre pays $/kW-yr for home batteries at its constrained substation. That lowers its curtailment risk and upgrade bill, and members get backup.
   - An industry-sponsored **Piq Energy / Base Power study** (Sep 2026) found that 80 MW of well-placed home batteries cleared every transmission constraint from a *hypothetical* 100 MW data centre near Burleson on Oncor's system.
3. **Co-op and municipal capacity deals**, which these utilities can sign without the restrictions that apply to Oncor (GVEC, CoServ and Austin Energy precedents).
4. **Stack everything:** ADER energy and reserves, the §48E credit, and the backup service members already pay for.

---

## 6. Study 2a: does adding large load reduce reliability and raise prices?

**ERCOT**
- **Prices fell even as load rose.** The real-time hub average dropped from about $63/MWh (2023) to about $29/MWh (2024), as about 10 GW of solar and storage arrived and the peak stayed flat ([E3](https://www.ethree.com/ercot-snapshot-2024/)).
  - In 2025 demand rose 6.1% and gas rose 61%, yet all-in power prices rose less than 14% and price spikes were 40% less frequent (IMM 2025).
- **Locally, added load did raise prices.**
  - Computed: the West zone's premium of +$9 to +$11/MWh in 2023–25 tracked Permian electrification and crypto growth (West demand +12.3% in 2025) behind import constraints.
  - DFW's 2026 premium is appearing as large loads queue there.
- **Reliability: the risk is new in kind.** No load shed has been caused by large loads. They do add the ride-through risk (§3.1), and flexible loads also earn from scarcity: Riot was paid $31.7M in Aug 2023 for curtailing ([CNBC](https://www.cnbc.com/2023/09/06/texas-paid-bitcoin-miner-riot-31point7-million-to-shut-down-in-august.html)).

**PJM (the clearest case of large load raising prices)**
- Capacity prices went from $28.92/MW-day (2024/25) to $269.92 (2025/26), then hit the cap at $329.17 (2026/27), $333.44 (2027/28) and $325 (2028/29).
- The last three auctions **fell 6.5–6.8 GW short** of the reliability requirement.
- The market monitor attributes **$29.4B of $63.6B (46%)** of capacity costs over four auctions to data-centre load, much of it forecast load not yet built ([Utility Dive](https://www.utilitydive.com/news/data-centers-pjm-capacity-auction/808951/); [mgrid, Jul 2026](https://mgrid.org/2026/07/23/data-centers-drove-6-3-billion-of-pjms-latest-capacity-auction-cost-market-monitor-says/) *(secondary)*).
- Energy prices rose 50% in 2025 ($33.74 → $50.73/MWh load-weighted), but gas rose 56%, so not all of that is load ([IMM PJM 2025](https://www.monitoringanalytics.com/reports/Market_Messages/Messages/2025-som-pjm-press-release.pdf)).
- Retail bills rose 10–20% in Maryland, New Jersey and Pennsylvania from June 2025.
- **Large-load trips in Northern Virginia:** about 1,500 MW on 10 Jul 2024 (a lightning-arrester fault with repeated reclosing), about 1,500 MW on 17 Feb 2025, and about **3,800 MW** on 22 Jul 2026 when a 230 kV line failed ([NERC](https://www.nerc.com/globalassets/our-work/reports/event-reports/incident_review_large_load_loss.pdf); [PJM](https://insidelines.pjm.com/pjm-dominion-review-large-load-transfer-event/)). NERC issued a rare **Level 3** alert in May 2026.

**Elsewhere**
- **Ireland:** data centres used 22% of electricity in 2024 and close to half in Dublin. There was a de facto connection moratorium from 2022 to Dec 2025. New sites must bring their own generation or storage.
- **Oregon:** PGE's data-centre rate class rose about 29% while residential rates fell 1.3% (POWER Act).
- **Bloomberg (2025):** nodal wholesale prices near data centres rose up to 267% over five years. This is a correlation, and wholesale prices make up only part of a bill.
- **LBNL/Brattle (2025):** states with more load growth saw slightly *lower* average retail prices in 2019–24. The authors warn this reverses where infrastructure is constrained ([Utility Dive](https://www.utilitydive.com/news/lbnl-retail-electricity-prices-rates-brattle/816321/)).

**What the evidence supports**
- Large load raises prices **where supply or transmission cannot keep up**: PJM capacity, ERCOT's West zone, and now DFW.
- Large loads create a **new reliability risk**: they trip offline together during faults.
- Batteries have **visibly reduced scarcity** in ERCOT (Fern, the July 2026 record, NERC's risk numbers).

**What it does not support**
- That load growth has caused firm load shed anywhere. Weather and generator failures did.
- That load growth raised retail prices nationally.
- That grid-scale batteries help customers during a feeder outage. They don't; only behind-the-meter storage does.

---

## 7. Study 2b: unplanned events beyond N-1

### 7.1 Why storms beat the planning rules

NERC's planning standard **TPL-001-5.1** requires the bulk system (≥ 100 kV) to survive a set of defined events:
- P1: one element lost (N-1)
- P2–P5: breaker and relay failures
- P6: two overlapping outages (N-1-1)
- P7: a double-circuit tower lost

**"Extreme events"** must be studied but carry no performance requirement. They include three or more circuits on one tower, a whole right-of-way, a whole substation, or wide-area severe weather. ERCOT adds two tests: generator out then N-1 (G-1+N-1), and a 345/138 kV transformer out then N-1 (X-1+N-1) ([TPL-001-5.1](https://www.nerc.com/globalassets/standards/reliability-standards/tpl/tpl-001-5.1.pdf); [ERCOT Planning Guide](https://www.ercot.com/files/docs/2025/11/26/December-1-2025-Planning-Guide.pdf)).

Storms cause **common-mode** failures across many elements at once. More basically, **residential feeders are radial**, so one tree on a lateral blacks out the homes beyond it however secure the transmission grid is. DOE finds that more than 90% of customer interruptions start on the distribution system.

### 7.2 Events, prices and batteries

The ERCOT price columns are computed (LZ zone 15-minute SPP). Other columns come from the cited sources.

| Event | What failed | Lost | Price | Battery role |
|---|---|---|---|---|
| **Uri**, 14–20 Feb 2021 | Generation froze; gas supply failed | 20 GW of load shed; 4.5M people without power; 210+ deaths | **LZ_NORTH max $9,314, week average $5,691**; 494 of 672 intervals ≥ $1,000 | Negligible: about 0.2–1.6 GW installed. A 2 h battery sells about one discharge into a multi-day event. |
| **6 Sep 2023 EEA2** | Heat, solar ramp-down, low wind, South Texas constraint | No load shed; frequency fell to 59.77 Hz | LZ_NORTH max **$5,300**, day average $805; LZ_CPS max $5,602 | Batteries discharged early and ran short on duration (about 2.2 GW *(unverified)*). |
| Winter Storm Heather, 15–17 Jan 2024 | Cold; winter record 78.3 GW | None | Max about $1,175; LZ_NORTH average $107 | About 1.2 GW; more than $6,000/MW earned in two days ([Modo](https://modoenergy.com/research/en/battery-energy-storage-revenues-storm-heather)) |
| **Houston derecho**, 16 May 2024 | 100 mph winds toppled transmission towers and distribution lines | About 922,000 customers | LZ_HOUSTON max **$133**, average $25: **no spike** | Grid batteries irrelevant; only home backup helped |
| **Hurricane Beryl**, 8–10 Jul 2024 | Trees and debris on distribution lines | 2.26M CenterPoint customers, some out more than 10 days | LZ_HOUSTON max **$54**, average **$16**: prices *fell* because demand disappeared | None at grid scale. CenterPoint's $800M of leased mobile generators largely went unused. Home batteries backed up homes. |
| Winter Storm Fern, 24–28 Jan 2026 | Ice and cold | About 40k Texas customers out (most outages were in TN, MS and LA) | Computed top days: 25 Jan (LZ_CPS $1,347) and 28 Jan (LZ_RAYBN $1,720) | About 6.7 GW of battery discharge pushed real-time prices below day-ahead ([Latitude](https://www.latitudemedia.com/news/armed-with-new-batteries-and-winterized-plants-ercot-survives-fern/)) |
| ERCOT record peak, 22 Jul 2026 | Heat; about 91.1 GW | None | Real-time peak about $378 | Record of about 12 GW battery discharge |
| Northern Virginia, 22 Jul 2026 (PJM) | A 230 kV line failed mechanically | About 3,800 MW of data-centre load moved to backup generators | No price event | n/a. Shows a large-load trip hazard. |
| Winter Storm Elliott, Dec 2022 (PJM, MISO, SERC) | Cold; 90.5 GW of unplanned generator outages | More than 5 GW load shed (TVA, Duke) | ERCOT $3,000. Some PJM zones exceeded the $3,700 cap through congestion. | Minimal |
| CAISO heat wave, 6 Sep 2022 | Record 52 GW peak | No load shed; EEA3 | Max $2,000 | More than 4 GW of batteries; CAISO credited them |

Two patterns stand out from the computed prices:
- **System-wide scarcity and local disasters are different animals.** Uri and 6 Sep 2023 produced the spikes. The Houston derecho and Beryl produced the outages, and their prices stayed flat or fell.
- **Localised spikes do happen and are getting more common in North Texas.** On 15 Jan 2025 LZ_RAYBN hit **$2,749** while LZ_HOUSTON peaked at $215. On 24 Apr 2026, **LZ_NORTH was ERCOT's highest-priced zone at $1,232**.

### 7.3 Reliability statistics

- **EIA:** in 2024 the average US customer was without power for 11 hours, of which about 9 came from major events, mostly hurricanes. Excluding major events, SAIDI (average outage minutes per customer) is roughly flat at about 106–126 minutes a year since 2013. **Major-event outages are what is growing** ([EIA](https://www.eia.gov/todayinenergy/detail.php?id=66744)).
- **Vegetation** causes about 23% of distribution outages ([T&D World](https://www.tdworld.com/vegetation-management/article/21169246/more-than-20-of-outages-attributed-to-vegetation-uvm-survey)). About 80% of major outages in 2000–23 were weather-related ([Climate Central](https://www.climatecentral.org/climate-matters/weather-related-power-outages-rising)).
- **Oncor's** non-storm SAIDI was 74.7 minutes in 2024 and 78.1 in 2025 ([Oncor](https://www.prnewswire.com/news-releases/oncor-reports-2025-results-announces-47-5-billion-2026-2030-base-capital-plan-302697739.html)).

### 7.4 Your scenarios: grid effect, price effect, where batteries help

| Scenario | What happens to power flow | Wholesale price | Battery relief | Battery money |
|---|---|---|---|---|
| **Tree on a lateral or feeder** | A fuse or recloser locks out; homes downstream go dark. A radial feeder has no N-1. | None | **Home backup** (Batter Up's 20% reserve and islanding) | Unpriced in the market. Paid only as a member backup service, or by a utility reliability contract (SB 415). |
| **Service or substation transformer failure** | Load transfers to neighbouring feeders until they hit thermal limits | None system-wide | Shave load so the transfer fits; avoid rolling outages; extend the life of overloaded units | Deferral value ($43–60/kW-yr typical; much higher at constrained sites), through a contract |
| **Transmission line or tower down** (derecho) | Flows reroute. If a constraint binds, nodal prices separate. | **Local** congestion spikes. Nodes can exceed $5,000; zones move less. | Discharge on the import side | ADER is paid the zone price, so local relief is only partly captured |
| **System scarcity** (heat or cold with low wind) | Reserves thin; EEA levels declared | **$1,000–5,000** | Discharge into the evening and morning peaks | The year's revenue in a few days: 21–55% of value from 10 days |
| **Large-load trip** (data centre fails to ride through a fault) | Sudden load loss of 0.1–5 GW; frequency rises | Brief | Charge fast to absorb surplus; support voltage | Fast-response services. Ride-through rules shift the burden to the load. |
| **Multi-day storm with feeder damage** (Beryl-like) | Days of outage; no grid charging | Flat or falling | Backup until the reserve runs out; solar recharge if available | Resilience value to members. Value of lost load: ERCOT system $35,685/MWh; residential about $4,000/MWh for a 1-hour outage ([PUCT](http://www.energychoicematters.com/stories/20240822e.html)). |

### 7.5 What a home battery is worth when the feeder fails

LBNL modelled three-day outages: standard PV-plus-battery systems met **86%** of critical load including heating and cooling, and **96%** with 30 kWh of storage ([LBNL, Applied Energy 2023](https://sciencedirect.com/science/article/pii/S0306261923005305)). California's aggregated home batteries delivered more than 580 MW from about 140,000 batteries on 9 Sep 2026 ([ESS News](https://www.ess-news.com/2026/09/28/california-home-batteries-set-vpp-dispatch-record-during-heat-wave/)). Home fleets are now big enough to matter at the system level and still protect the house when the street fails.

---

## 8. What this means for Batter Up

1. **Location.**
   - LZ_NORTH is the right price, and DFW is becoming one of ERCOT's better-paying zones.
   - The strongest local cases are Denton County (Argyle–Lewisville), Dallas and Ellis counties (large-load clusters), and Burleson (the Piq/Base study).
   - These could be the next feeders to model.
2. **Revenue model.** Add these streams beside the current "gross energy value with perfect foresight":
   - (a) ADER energy plus Non-Spin and ECRS at 2025–26 prices
   - (b) distribution deferral at $43–60/kW-yr, labelled ASSUMPTION, paid through an SB 415-style contract
   - (c) a data-centre locational-flexibility fee, as a scenario
   - (d) member backup value, priced on value of lost load, labelled ASSUMPTION
3. **Price replays.** Add the days the computed data flags:
   - 6 Sep 2023 (system scarcity)
   - 15 Jan 2025 (a North and East spike while Houston stayed calm)
   - 28 Jan 2026 (Fern)
   - 24 Apr 2026 (LZ_NORTH highest in ERCOT)
4. **Outage scenarios beyond N-1**, beside today's comms-loss, hot-transformer and controller-stall faults:
   - a lateral fault from a tree
   - a substation transformer lost, with feeder-transfer limits
   - a Beryl-like multi-day storm that tests reserve management
   - a large-load trip that raises frequency
5. **Duration.** Spikes are moving into longer late-night and winter-morning windows. Report each scenario's value per kWh of storage as well as per kW.

---

## Method and data

- **Prices:** ERCOT NP6-785-ER "Historical RTM Load Zone and Hub Prices" (report 13061), yearly files 2021–2026, downloaded 3 Oct 2026 from the [MIS listing](https://www.ercot.com/misapp/GetReports.do?reportTypeId=13061). The zip hashes are in [`data/ercot/SOURCE-SHA256SUMS.txt`](data/ercot/SOURCE-SHA256SUMS.txt). The 2026 file was published 27 Sep 2026 and covers 1 Jan to 26 Sep.
- **Processing:** [`scripts/ercot_zone_study.py`](scripts/ercot_zone_study.py).
  - Settlement point type `LZ` is used; `LZEW` rows are dropped, as in `data/ercot/SOURCE.md`.
  - Statistics are time-weighted over 15-minute intervals.
  - "Basis" is the zone price minus HB_BUSAVG.
  - The battery model is a dynamic programme over each day: 1 MW, 2 MWh, 85% round-trip efficiency with the loss charged on charging, at most one full cycle a day, empty at midnight, perfect foresight. It is an **upper bound** on energy-only arbitrage and leaves out ancillary services and capacity.
- **Outputs:** [`data/ercot/`](data/ercot/) holds `zone_year_stats.csv`, `zone_basis.csv`, `battery_value.csv`, `event_windows.csv` and `top_days.csv`.
- **Re-running:** download the six yearly zips into `ercot/` next to the script, unzip them, and run it with Python 3.12 or newer plus `openpyxl` and `numpy`. This is separate from the app's `requirements.txt`.
- **ERCOT terms:** data may be redistributed in analyses; the ERCOT logo may not be used ([terms](https://www.ercot.com/help/terms)).
- **Research method:** four research passes on 3 Oct 2026 read primary sources first (ERCOT, the PUCT, the market monitors, NERC, EIA, DOE, FERC) and trade press second. Search budgets ran out near the end of each pass, so a few items rest on search snippets and are marked *(unverified)*.
