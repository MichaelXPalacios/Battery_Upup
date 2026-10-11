# NYISO: large loads, price geography and where batteries earn their keep

*Research for Batter Up, 3 October 2026. This repeats the [ERCOT study](ercot.md) for New York. NYISO was chosen as the second ISO because it publishes the longest keyless price history of any US market. Its zonal real-time prices go back to November 1999, and this study uses 2003 to September 2026. New York also has the most developed programs that pay batteries for local grid relief.*

**How to read the numbers.**
- **Computed** means I calculated it from NYISO's public real-time zonal price files: 5-minute LBMPs, 1 Jan 2003 to 30 Sep 2026, 285 monthly files. Each zone-day was turned into 15-minute time-weighted averages, the same resolution as the ERCOT study, so the two compare like for like.
- Every other number cites its source.
- *(unverified)* marks a single secondary source or a search snippet.
- Material that applies to both markets lives in the ERCOT report and is only linked here: national load-growth context, PJM evidence, NERC planning criteria, federal tax credits.

---

## Summary

1. **Words.** NYISO calls its price the **LBMP** (Location-Based Marginal Price), in $/MWh; the files write "$/MWHr".
   - There are **11 load zones, A–K**. Generators settle at their own bus.
   - Capacity is a separate product, priced in **$/kW-month** in four Localities: NYCA (statewide), G–J, NYC and Long Island.
2. **The upstate–downstate wall is shrinking.** Computed, as a ratio to Central NY (Zone C):
   - NYC (J) peaked at **1.46 in 2022**, after Indian Point closed and gas spiked, and fell to **1.11 in 2026**.
   - Long Island (K) fell from **1.85 (2021) to 1.11**.
   - The North Country (D) went from a 15–24% discount (2015–22) to **parity**. Its share of negative-price intervals fell from **13% to 0.5%**.
   - New transmission explains much of this: the AC Transmission upgrades, NYPA's Smart Path rebuilds, and the **Champlain Hudson Power Express (CHPE)**, which began commercial operation on 13 May 2026.
3. **Prices jumped about 70% in 2025, and data centres were not the cause.** NYC's average went from $39.4 to $67.0/MWh (computed, time-weighted). The market monitor attributes the rise to:
   - NYC delivered gas up 112%
   - Quebec swinging from exporting to New York to importing from it
   - transmission outages
   - a 10% higher peak
4. **NYC and Long Island spikes still come at 3–9 pm, because New York has few batteries.** Computed: 53–71% of NYC intervals at or above $250 fell between 3 and 9 pm in 2023–25. In ERCOT, batteries have already pushed spikes into the late night. Load pockets can price far above the $1,000 offer cap through shortage pricing: **Long Island's 5-minute peak was $7,338/MWh in 2025.**
5. **Battery money is bigger and steadier than in DFW.**
   - Computed perfect-foresight energy ceiling: **NYC $70.7 and Long Island $83.9/kW-yr in 2025**, against $51.1 at ERCOT LZ_NORTH.
   - New York adds **capacity revenue**: NYC 4-hour batteries earned $104–133/kW-yr in 2023–25, and the NYC monthly price hit a record **$32.6/kW-month in May 2026**.
   - The 10 best days produce only 9–35% of a year's value, against 21–55% at ERCOT LZ_NORTH.
6. **New York pays directly for distribution relief:**
   - Con Ed Distribution Load Relief, **$18–25/kW-month**
   - Value Stack demand-reduction and locational credits, **about $141–199/kW-yr**
   - the Brooklyn-Queens battery offering, **$2,500–3,000/kW**
   - utility bring-your-own-battery programs, $50/kW-yr
   - NYSERDA upfront incentives, $125–450/kWh

   Texas has no equivalent yet; SB 415 is still a proposed rule.
7. **Reliability margins are the thinnest in years.**
   - Summer 2026 margin was 417 MW; a 98°F heat wave would leave a **3,370 MW shortfall**.
   - NYC was deficient across 2025–30 until CHPE and Empire Wind arrive. Peakers were kept on.
   - Long Island is deficient from 2027.
   - Con Ed's NYC transmission need is 125 MW (2033) rising to 675 MW (2036). Its plan makes **storage the primary resource**; comments are due 13 Oct 2026.
8. **The large-load wave is small and mostly on paper.**
   - 12.7 GW is in the queue (May 2026), almost all upstate. NYISO's forecast counts only **2.9 GW by 2040**, including 675 MW of "flexible" load.
   - On 14 Jul 2026 an executive order **paused permits for data centres of 50 MW or more**.
   - That is about 3% of ERCOT's queue.
9. **History.**
   - *Removing* supply raised prices: Indian Point and the peaker retirements lifted NYC's capacity price by +400% in 2023/24, and NYC's price ratio to Central rose from 1.32 to 1.46.
   - *Adding* crypto load raised small-town winter bills (Plattsburgh 2018; an estimated $204M a year upstate).
   - *Adding* wires lowered the ratio (2023–26).
   - Unplanned events split the same way as in ERCOT. Fuel and weather scarcity spiked prices: the 2014 polar vortex, Elliott (NYC **$4,607**), June 2025 and Fern. Distribution disasters left prices flat: Sandy, NYC max **$245**; Isaias, $107; the 2019 Manhattan blackout, $129.

---

## 1. Vocabulary: say it the NYISO way

| What you said | What to say | Why |
|---|---|---|
| "nodal zones" | **load zones A–K** (what load pays), **buses** (where generators are priced) | A zonal LBMP is the load-weighted average of bus LBMPs. Generators settle at their own bus. |
| "MW/hr in $" | **$/MWh** for energy; **$/kW-month** for capacity | NYISO's file headers say "$/MWHr", which means $/MWh. Capacity is sold as unforced capacity (UCAP) in $/kW-month. |
| "regions sectioned off where prices go up and down" | **import-constrained** zones and pockets (J, K, NYC sub-pockets) price above the reference bus; **export-constrained** ones (North, Oswego, sometimes West) price below | Congestion and losses are measured against the **Marcy 345 kV reference bus**. |

**The pieces** ([NYISO tariff MST 17.1](https://nyisoviewer.etariff.biz/viewerdoclibrary/MasterTariffs/9TariffSections/MST%2017.1%20FID1704%20redline_27081.htm); [Potomac 2025 State of the Market](https://www.potomaceconomics.com/wp-content/uploads/2026/05/NYISO-2025-SOM-Report__5-19-2026-final.pdf)):

| Term | What it is |
|---|---|
| LBMP | Energy at the Marcy reference bus, plus marginal losses, plus congestion. **In the public files the congestion column has the opposite sign**: LBMP = energy + losses − (posted congestion). A negative posted value means the zone is *more* expensive. I checked this in the data: NYC's posted value was −$25 when NYC was $60. |
| Zones (PTID) | A WEST (61752), B GENESE, C CENTRL, D NORTH, E MHK VL, F CAPITL, G HUD VL, H MILLWD, I DUNWOD, **J N.Y.C. (61761)**, **K LONGIL (61762)**. External proxy buses: PJM, H Q (Hydro-Québec), NPX (New England), O H (Ontario). |
| Load pockets | Inside NYC: East River, Astoria West/Queensbridge, Astoria West/Queens/Vernon, Freshkills, Greenwood/Staten Island, Sprainbrook/Dunwoodie. On Long Island: Valley Stream, Elwood, Pilgrim, Far Rockaway, East End and others. Con Ed plans NYC to **N-1-1-0**: lose one element, re-dispatch, lose a second, and still serve all load. |
| Markets | Day-ahead (hourly). Real-time commitment every 15 minutes. **Real-time dispatch (RTD) every 5 minutes** sets the real-time LBMP. Corrective-action runs create irregular intervals. Load and battery charging settle at the hour's time-weighted real-time LBMP. |
| Caps | Energy offers are capped at **$1,000/MWh**, or up to $2,000 with verified costs. Reserve shortage curves and **transmission shortage pricing** (graduated steps up to $4,000/MWh since Nov 2023) can push pocket prices far above $1,000. |
| Capacity (ICAP) | Monthly spot auctions on sloped demand curves for NYCA, G–J, NYC and Long Island. The capability year runs May–April. Since 2025 the demand curves are priced off a **2-hour battery** as the reference unit. Sellable capacity is discounted by a **Capacity Accreditation Factor (CAF)** for each resource class. |
| How a home fleet participates | NYISO's distributed-resource aggregation model has been live since April 2024, but participation is still small. Most residential value runs through utility programs (§5). |

---

## 2. Where NYISO prices spike (computed, 2003–2026)

### 2.1 Average real-time price by zone

Time-weighted averages of 15-minute prices, $/MWh. 2026 runs to 30 Sep.

| Year | A West | C Central | D North | F Capital | G Hudson V. | **J NYC** | **K Long Is.** |
|---|---:|---:|---:|---:|---:|---:|---:|
| 2005 | 62.9 | 67.6 | 68.0 | 75.5 | 77.9 | 95.8 | 103.9 |
| 2008 | 57.1 | 65.5 | 61.7 | 82.3 | 85.5 | 96.9 | 97.9 |
| 2012 | 31.0 | 32.2 | 30.2 | 35.0 | 36.7 | 38.6 | 46.9 |
| 2016 | 25.5 | 21.4 | 17.3 | 28.4 | 27.8 | 29.5 | 34.4 |
| 2019 | 25.5 | 21.3 | 18.1 | 26.9 | 26.1 | 28.2 | 33.3 |
| 2020 | 17.8 | 16.4 | 13.3 | 21.4 | 20.3 | 22.0 | 28.0 |
| 2021 | 30.9 | 29.6 | 23.5 | 44.2 | 40.1 | 42.5 | 54.8 |
| 2022 | 56.5 | 59.7 | 48.8 | 96.9 | 84.5 | 87.1 | 97.5 |
| 2023 | 24.4 | 26.6 | 23.2 | 34.5 | 31.9 | 33.4 | 41.0 |
| 2024 | 31.7 | 33.6 | 32.6 | 36.7 | 36.4 | 39.4 | 42.2 |
| 2025 | 51.7 | 56.2 | 56.5 | 63.4 | 61.9 | 67.0 | 70.2 |
| 2026 | 54.5 | 63.3 | 63.5 | 66.8 | 68.5 | 70.4 | 70.5 |

Every year's file is in [`data/nyiso/zone_year_stats.csv`](data/nyiso/zone_year_stats.csv). *Cross-check:* Potomac's **load-weighted** 2025 averages are NYC $73.1, Long Island $81.0 and West $54.4. Potomac reports NYC up 75% in 2025; my time-weighted figure is +70%. The "+69%" figure that circulates refers to a different statistic. Prices follow gas: the 2005–08 highs, the 2012 shale low, the 2020 COVID low, the 2022 gas crisis and the 2025–26 rise all line up with fuel.

### 2.2 The "sectioned-off" regions: price ratio to Central NY, and congestion

Each cell is the zone's average price divided by Zone C's, with the zone's mean congestion premium against the Marcy reference in brackets ($/MWh). A ratio partly cancels gas-price swings.

| Year | **J NYC** | **K Long Is.** | F Capital | D North | A West |
|---|---|---|---|---|---|
| 2003 | 1.45 (+15.3) | 1.41 (+14.1) | 1.14 (+2.5) | 1.01 (0.0) | 0.96 (+0.2) |
| 2008 | 1.48 (+27.4) | 1.49 (+27.3) | 1.26 (+15.5) | 0.94 (−0.2) | 0.87 (−1.6) |
| 2012 | 1.20 (+4.4) | 1.46 (+12.0) | 1.09 (+1.8) | 0.94 (−0.3) | 0.96 (+0.5) |
| 2016 | 1.38 (+7.3) | 1.61 (+11.9) | 1.33 (+6.9) | **0.81** (−2.1) | **1.19** (+6.0) |
| 2019 | 1.32 (+6.0) | 1.56 (+10.7) | 1.26 (+5.3) | 0.85 (−1.3) | 1.20 (+5.8) |
| 2020 | 1.34 (+5.0) | 1.71 (+10.8) | 1.30 (+4.9) | 0.81 (−1.7) | 1.08 (+2.8) |
| **2021** | **1.44** (+12.2) | **1.85** (+24.3) | **1.49** (+14.8) | 0.80 (−3.2) | 1.04 (+3.8) |
| **2022** | **1.46** (+28.2) | 1.63 (+38.2) | **1.62** (+39.4) | 0.82 (−3.8) | 0.95 (+3.4) |
| 2023 | 1.26 (+6.8) | 1.54 (+14.1) | 1.30 (+8.5) | 0.87 (−1.6) | 0.92 (−0.1) |
| 2024 | 1.17 (+3.6) | 1.25 (+6.1) | 1.09 (+1.8) | **0.97** (−0.8) | 0.94 (−0.6) |
| 2025 | 1.19 (+7.3) | 1.25 (+10.4) | 1.13 (+4.9) | **1.00** (−0.1) | 0.92 (−1.7) |
| **2026** | **1.11** (+3.4) | **1.11** (+3.8) | 1.06 (+1.2) | **1.00** (0.0) | **0.86** (−5.2) |

What changed, and why:
- **Indian Point closed (Apr 2020, Apr 2021; about 2 GW) → the downstate premium rose.** NYC's ratio went from 1.32–1.34 to 1.44–1.46, and Capital's (Central-East) from 1.26–1.30 to 1.49–1.62.
  - The market monitor attributes part of eastern NY's 2021 rise to Central-East bottlenecks from transmission outages and Indian Point's retirement. Gas was the bigger driver ([Potomac 2021 SOM](https://www.potomaceconomics.com/wp-content/uploads/2022/05/NYISO-2021-SOM-Full-Report_5-11-2022-final.pdf)).
- **Wires were added → the wall shrank.** Central-East congestion fell after the AC Transmission projects. Quebec swung from 1.1 GW average imports (2022) to 550 MW average exports (2025).
  - CHPE (1,250 MW HVDC from Quebec into Astoria, Queens) began commercial operation on 13 May 2026. In 2026 Q1, Central-East congestion fell more than 90% and NYC more than 85% ([Potomac Q1 2026](https://www.potomaceconomics.com/wp-content/uploads/2026/07/NYISO-Quarterly-Report_2026Q1_revised_7-20-2026.pdf)).
  - NYC's 2026 ratio of 1.11 is the lowest in 24 years.
- **North Country (D): from New York's "Panhandle" to parity.** Zone D used to be export-constrained, with hydro and wind and too few lines out. It traded 15–24% below Central and went negative in 7–13% of intervals in 2016–23.
  - Since 2024 it is at parity, and negative prices are down to 0.5–1.3%.
  - Two changes line up with this, though I have not attributed it: NYPA's Smart Path and Smart Path Connect rebuilds, and the **435 MW of flexible (crypto-type) large load** that NYISO now models in Zone D.
- **West (A) flipped.** It carried an import premium in 2015–19 (ratio 1.13–1.20, congestion about +6) and is now at a discount (0.86, −5.2), as limits on the PJM interface and western export paths bind.
- **2025 congestion doubled to $638M** (day-ahead), driven by winter gas spreads and outages on Gowanus–Greenwood and Astoria Annex–E.13th St. NYC path congestion was $154M, Central→East $145M and Long Island $72M.

### 2.3 Spikes and negative prices

| Zone | Intervals ≥ $1,000 (15-min): 2014 / 18 / 21 / 22 / 23 / 24 / 25 / 26 | Highest 5-min price 2024 / 25 / 26 |
|---|---|---|
| A West | 25 / 11 / 1 / 22 / 2 / 5 / 8 / 8 | $2,411 / 2,095 / 3,769 |
| D North | 6 / 3 / 1 / 27 / 2 / 6 / 10 / 8 | 2,501 / 2,057 / 3,979 |
| F Capital | 12 / 9 / 3 / **90** / 6 / 9 / 22 / 13 | 2,593 / 3,543 / 4,006 |
| G Hudson Valley | 13 / 7 / 1 / 38 / 3 / 5 / 23 / 22 | 2,698 / 2,886 / 4,059 |
| **J NYC** | 13 / 10 / 1 / 38 / 7 / 9 / **41** / **30** | 3,725 / **5,527** / 4,670 |
| **K Long Island** | 24 / 13 / **29** / 53 / 11 / 9 / **51** / 15 | **6,753 / 7,338 / 7,262** |

Long Island is New York's sharpest pocket. Its 5-minute prices reached $6,753–7,338 in each year since graduated transmission shortage pricing began (Nov 2023). In the June 2025 heat wave, Long Island averaged about $3,500/MWh from 5 to 8 pm ([NYISO hot-weather review](https://www.nysrc.org/wp-content/uploads/2025/11/7.3.3-Summer-2025-NYISO-Hot-Weather-Operating-Conditions-EC-Nov-14-Attachment-7.3.3.pdf)).

### 2.4 When spikes happen (share of 15-minute prices ≥ $250, hour-start local time)

| Zone, year | Count | 00–06 | 06–09 | 09–15 | **15–21** | 21–24 |
|---|---:|---:|---:|---:|---:|---:|
| NYC 2022 | 895 | 14% | 14% | 18% | 46% | 7% |
| NYC 2024 | 128 | 2% | 10% | 16% | **71%** | 1% |
| NYC 2025 | 531 | 1% | 11% | 18% | **63%** | 6% |
| NYC 2026 | 983 | 11% | 16% | 21% | 44% | 8% |
| Long Island 2024 | 183 | 1% | 7% | 11% | **80%** | 2% |
| Long Island 2025 | 498 | 2% | 11% | 16% | **67%** | 4% |

New York still has the afternoon-into-evening peak that ERCOT had before its battery boom (compare [ERCOT §2.4](ercot.md#24-when-spikes-happen-lz_north-intervals--250mwh-computed)). In 2026, the January cold spell added morning spikes.

---

## 3. Where batteries relieve the grid, and what they earn

### 3.1 Relief

- **NYC and Long Island load pockets.** NYISO meets N-1-1-0 pocket needs today by committing units outside the market: 22 NYC days in 2026 Q1 and 50 days in Q2 2025. The market monitor says batteries could be cheaper, but the market does not pay for local needs; it recommends modelling local reserves.
  - Peak-shaped needs are why NYC's 4-hour CAF rose from 68.8% to about 84%.
- **Con Ed's NYC transmission need:**
  - In Feb 2026 Con Ed put it at 125 MW for 3 hours in 2032, rising to 750 MW for 9 hours in 2036. Those durations matter for battery sizing.
  - In Jul 2026 it updated this to 125 MW in 2033 rising to 675 MW in 2036.
  - Its contingency plan names **storage as the primary resource**, through a proposed **"Reliability Asset Dispatch Rights" (RADR)** contract: 15 years, utility dispatch, with charging and interconnection costs reimbursed. Comments are due 13 Oct 2026 ([DPS](https://dps.ny.gov/event/comments-due-con-edison-proposal-seeking-approval-implement-aspects-reliability-contingency)).
- **Thin statewide margins.**
  - The summer 2026 baseline margin was 417 MW. A 95°F heat wave would leave a 1,679 MW shortfall and 98°F a 3,370 MW shortfall; emergency procedures can cover up to 3,166 MW ([NYISO](https://www.nyiso.com/-/press-release-new-york-electric-grid-faces-narrow-reliability-margin-heading-into-summer)).
  - Since 2019, 4.4 GW retired and 2.9 GW was added.
  - Winter risk is rising: winter's share of loss-of-load expectation went from 14.8% to 20.2% for 2027–28.
- **Distribution networks.** Con Ed's underground networks are designed to survive any two feeder failures. Heat waves still take out several feeders at once (1999, 2006, 2019, 2026).
  - Con Ed has tightened interconnection rules because it is worried about **uncontrolled battery charging**. That raised costs for about 2.5 GW of small-battery requests and drew a March 2026 petition from NY-BEST and NYSEIA ([Canary](https://www.canarymedia.com/articles/batteries/con-edison-battery-developer-fight-nyc)).
  - **Coordinated, feeder-aware charging is exactly what Batter Up demonstrates.**

### 3.2 Money

**Capacity spot prices**, $/kW-month of UCAP (Potomac; May–June 2026 from Modo):

| Capability year | NYCA | NYC | Long Island |
|---|---:|---:|---:|
| 2022/23 | 2.71 | 2.96 | 4.40 |
| 2023/24 | 4.11 | about 15.9 (+400% after peaker retirements) | 4.11 |
| 2024/25 | 3.08 | 11.13 | 3.18 |
| 2025/26 | 4.28 | 10.98 | 4.28 |
| May/Jun 2026 | about 8 | **32.6 / 32.5 (record)** | about 11 |

The May 2026 jump came from three things: NYC's capacity surplus of about 1,050 MW nearly vanished, the NYC requirement rose from 78.5% to about 82.6%, and CHPE missed the auction deadline ([Modo](https://modoenergy.com/research/en/nyiso-capacity-prices-record-summer-2026)).

**Capacity Accreditation Factors for storage:**

| Year | NYC 4 h | Long Island 4 h | NYC 2 h | Long Island 2 h |
|---|---:|---:|---:|---:|
| 2024/25 | 68.8% | 78.9% | 55.9% | 52.8% |
| 2025/26 | 78.5% | 87.1% | 64.9% | 52.7% |
| 2026/27 (CHPE counted) | 84.4% | 82.8% | 63.8% | 55.1% |

Source: [NYISO 2026–27 CAFs](https://www.nyiso.com/documents/20142/41593818/Final-Capacity-Accreditation-Factors-for-the-2026-2027-capability-year.pdf).

**What a 4-hour battery earned**, $/kW-yr (Potomac 2025 SOM; realistic dispatch, not perfect foresight):

| Location, year | Capacity | Energy + ancillary (day-ahead reserves strategy) | NY incentive | Federal credit | Total | Cost of new entry |
|---|---:|---:|---:|---:|---:|---:|
| NYC 2023 | 133 | 62 | 32 | 53 | 280 | about 370 |
| NYC 2025 | 104 | 116 | 32 | 51 | 302 | about 360 |
| Long Island 2025 | 41 | 111 | 32 | 42 | 225 | about 239 |
| Central 2025 | 36 | 69 | 32 | 40 | 176 | about 223 |

Even with every stream counted, a new NYC battery fell short of its cost of entry in 2025. Hence the state's Index Storage Credit.

**Energy-arbitrage ceiling** (computed; same 1 MW / 2 MWh perfect-foresight model as ERCOT; $/kW-yr; 2026 annualised):

| Year | A | C | D | F | G | **J NYC** | **K LI** | J: share from 10 best days |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| 2005 | 60.1 | 65.1 | 66.8 | 76.7 | 82.8 | 102.4 | 104.9 | 18% |
| 2008 | 90.1 | 99.7 | 105.0 | 103.3 | 96.0 | 107.5 | 106.2 | 14% |
| 2012 | 35.0 | 35.0 | 33.8 | 37.5 | 44.2 | 49.8 | 78.9 | 24% |
| 2014 | 70.7 | 55.7 | 56.3 | 62.7 | 62.0 | 66.0 | 92.5 | 19% |
| 2019 | 44.4 | 24.1 | 29.9 | 25.2 | 25.5 | 29.3 | 46.9 | 16% |
| 2020 | 29.3 | 19.1 | 25.9 | 19.8 | 18.2 | 23.3 | 40.6 | 13% |
| 2021 | 42.1 | 33.6 | 49.7 | 41.1 | 33.5 | 37.7 | 88.2 | 15% |
| 2022 | 73.2 | 75.7 | 92.8 | 109.5 | 83.3 | 87.1 | 123.0 | 24% |
| 2023 | 24.3 | 26.1 | 32.4 | 37.9 | 30.0 | 34.5 | 62.4 | 26% |
| 2024 | 27.2 | 28.9 | 32.0 | 33.5 | 32.8 | 39.2 | 48.1 | 22% |
| **2025** | 48.5 | 52.1 | 58.3 | 63.4 | 58.0 | **70.7** | **83.9** | 33% |
| **2026** | 54.9 | 59.0 | 58.8 | 64.0 | 68.5 | **72.5** | **79.2** | 31% |

- Long Island has the highest ceiling in most years.
- The value is spread across many days. ERCOT LZ_NORTH's 10 best days produced 21–55% of the year in 2021–26. Across New York's zones the figure is 9–35%, and 13–33% for NYC in the years shown.
- In NYC, capacity is a larger stream than energy arbitrage. In ERCOT there is no capacity market at all.

### 3.3 Storage on the ground

As of 31 Mar 2026, New York had **528.7 MW installed** and 1,423.6 MW contracted or awarded, about a third of the **6 GW by 2030** target ([DPS State of Storage](https://documents.dps.ny.gov/public/Common/ViewDoc.aspx?DocRefId=%7BF0A94A9D-0000-CD3F-AD54-1B3672D3EA91%7D&DocTitle=State+of+Storage)).
- Residential storage was just 66.7 MW installed.
- The 2024 roadmap adds 3 GW bulk, 1.5 GW retail and 200 MW residential, with at least 30% of off-site storage in NYC.
- **Index Storage Credit, Round 1** (23 Sep 2026): 8 lithium-ion projects, 950 MW / 4,360 MWh, chosen from 46 bids totalling 6.3 GW. 54% of the megawatts are in NYC and Long Island.
- Fewer than 100 MW of batteries have entered NYISO's markets since 2019. That is why spikes still sit at 3–9 pm.

### 3.4 What this means for a home fleet in New York

1. **The fleet would be paid by the utility first and the market second.** Con Ed's demand-response programs, Value Stack credits and a NYSERDA upfront incentive (§5) are worth more per kW than wholesale arbitrage in most years.
2. **Long Island and NYC are the pockets.** They have the highest ceilings, the highest capacity prices and documented reliability needs.
3. **Duration.** Con Ed's needs run 3–9 hours and NYC capacity rewards 4 hours or more. A 2-hour home battery is credited at only 56–65% in NYC.
4. **The pitch is managed charging.** Con Ed's interconnection worries are about batteries all charging at once, the problem Batter Up's feeder-aware controller is built to prevent.

---

## 4. The large-load wave and the queue

### 4.1 Public data you can use

| Source | What it gives you |
|---|---|
| [NYISO interconnection queue](https://www.nyiso.com/interconnections) (Excel, latest 31 Aug 2026, free) | Generation and storage projects, plus a separate **Load Projects** list. Since Aug 2024 projects are studied in clusters; the 2026 cluster (C26) has 25 requests and 5.1 GW, about 2.8 GW of it batteries. |
| [Gold Book 2026](https://www.nyiso.com/documents/20142/2226333/2026-Gold-Book-Public.pdf) (Apr 2026) | Load and capacity forecasts by zone, large-load forecast, behind-the-meter storage forecast |
| [Power Trends 2026](https://www.nyiso.com/power-trends) | NYISO's annual state of the grid |
| Reliability Needs Assessment / Comprehensive Reliability Plan; Short-Term Assessment of Reliability (quarterly) | Reliability needs and margins |
| [Potomac quarterly and annual reports](https://www.potomaceconomics.com/markets-monitored/new-york-iso/) | Prices, congestion, battery net revenue |
| [NYISO public price files](https://mis.nyiso.com/public/menu.htm) | The data used in this study; no key needed |

### 4.2 Numbers

- **Large-load queue:**
  - 6 projects / 1,045 MW (2022) → 51 projects / **12,670 MW** (May 2026).
  - By zone (Gold Book): A 2,090 MW, B 600, C 2,216, D 2,647, E 2,924, F 450, G 292, H 1,200, K 244, NYC zero.
  - A September 2026 snapshot of the workbook sums to about 16.9 GW *(unverified)*.
- **What NYISO forecasts:**
  - Only about **2.9 GW** by 2040 (low case 1.2 GW, high case 4.4 GW), weighted by how likely each project is.
  - Includes 675 MW of **flexible large load** that can drop at peak (Zone A 240 MW, Zone D 435 MW).
  - Micron's fab in Clay (Zone C) is the anchor. Micron is paying the vast majority of its new 345 kV line.
- **Peak forecast** (Gold Book 2026 baseline):

  | | 2025 actual | 2027 | 2031 | 2036 |
  |---|---:|---:|---:|---:|
  | Summer peak | 31,405 MW | 31,880 MW | 32,420 MW | 33,430 MW (high 36,720) |
  | Winter peak | 24,250 MW | 25,110 MW | 26,690 MW | 29,060 MW |
  | Large-load summer MW | – | 1,075 | 1,989 | 2,717 |

  Winter grows about three times faster than summer. It overtakes summer only in 2049–50, much later than earlier editions said. The 30-year energy growth rate was cut from 2.0% to 1.1% a year.
- **Policy, 2026:**
  - The governor's "Energize NY" plan (Jan 2026) says data centres should pay their fair share of grid upgrades.
  - The PSC opened Case 26-E-0045 on large-load interconnection and "beneficiary pays" cost allocation (Feb 2026).
  - **Executive Order 62** (14 Jul 2026) paused incomplete environmental permits for data centres of 50 MW or more pending a statewide review.
  - Bill **A11560** passed in June 2026 but is unsigned. It would impose a 1-year moratorium on data centres of 20 MW or more, separate rate classes by 2030, and renewable sourcing requirements ([Foley](https://www.foley.com/insights/publications/2026/08/new-york-just-pressed-pause-on-large-data-center-permitting/)).

### 4.3 Where batteries play over 1, 5 and 10 years (New York)

| Horizon | Load | Storage | Where the money and relief will be |
|---|---|---|---|
| **1 year** (2027) | Summer 31.9 GW; large loads about 1.1 GW; thin margins | ISC Round 1 projects under way; behind-the-meter about 0.7 GW | NYC capacity at $25–33/kW-month in summer; Con Ed relief programs; utility battery programs; upfront incentives; backup for NYC and Long Island |
| **5 years** (2031) | Summer 32.4 GW (high 34.2); large loads about 2.0 GW, upstate | 6 GW goal; behind-the-meter 1.4 GW (NYC 0.6) | Long-term NYC reliability contracts (RADR) as Con Ed's need begins in 2032–33. Capacity credit for short batteries erodes as storage saturates. Distribution relief. |
| **10 years** (2036) | Summer 33.4 GW, winter 29.1 GW; winter risk rising | Behind-the-meter 2.0 GW (NYC 0.86); Con Ed need 675–750 MW | Longer duration for winter peaks and for Con Ed's 9-hour need. Relief for networks heavy with electrification. Resilience. |

---

## 5. Who pays for upgrades: deferrals, incentives and partnership routes

| Program | Who gets paid | For what | $ / status |
|---|---|---|---|
| **Con Ed Distribution Load Relief (DLRP)** | Customers or aggregators (50 kW minimum) | Contingency relief on 2 hours' notice or less | **$18 (Tier 1) / $25 (Tier 2) per kW-month** + $1/kWh. Can be stacked with CSRP for up to about $215/kW-yr. |
| Con Ed Commercial System Relief (CSRP) | Same | 4-hour network peak window, 21 hours' notice | $18/kW-month (Manhattan, Bronx, Brooklyn, Queens), $6 (Staten Island, Westchester), + $1/kWh |
| **Value Stack demand-reduction and locational credits (DRV, LSRV)** | Exporting distributed resources | Exports during network peaks; LSRV only in designated areas | Con Ed DRV **$199.40/kW-yr**, LSRV **$140.76/kW-yr** (Nov 2025). Several LSRV areas have closed as non-wires portfolios filled. |
| **Brooklyn-Queens battery offering** | Battery owners in the area | 10 summers of first right of dispatch; 4-hour discharge; peak at 9–10 pm | **$2,500–3,000/kW**; 15 MW sought |
| Con Ed RADR (proposed) | Battery developers | 15-year utility dispatch of NYC transmission-connected storage | 125 MW (2033) → 675 MW (2036); comments due 13 Oct 2026 |
| Utility bring-your-own-battery programs | Customers or aggregators | Average kW delivered in 3–4 hour summer events | **$50/kW-yr**. Live at National Grid and NYSEG/RG&E; Con Ed and Central Hudson "later in 2026". |
| NYSERDA residential incentive (≤ 25 kWh) | Homeowner | Upfront | Con Ed area $250/kWh, rest of state $200/kWh, low-income tier $450/kWh; Long Island excluded. Enrolling in a utility battery program is required. |
| NYSERDA retail incentive (≤ 5 MW) | Owner or customer | Upfront | NYC/Westchester $125/kWh, rest of state $175/kWh, critical facilities $350/kWh |
| NYSERDA Index Storage Credit | Front-of-meter storage | Contract for difference against a zonal energy + capacity reference price, 15 years | Round 1: 950 MW (Sep 2026); Round 2 RFP by end-2026 |
| National Grid non-wires RFP | Developers | 7 upstate locations with 2027 needs | More than $20M; due **3 Nov 2026** |
| PSEG Long Island Battery Storage Rewards | Aggregators | Up to 10 summer events of 4 hours | Payment set by the aggregator |
| NYC property-tax abatement | Property owners | Solar and/or storage | 30% of cost, up to $250k over 4 years (in service 2024–34) |
| Federal §48E / §25D | Storage owners | Investment credit | See [ERCOT §5.2](ercot.md#52-federal-brief). Third-party ownership is favoured now that §25D has ended. |
| Large-load cost responsibility | Paid **by** large loads | Upgrades | Case 26-E-0045 open; Micron is funding most of its line |

**Partnership angles in New York**
- Bring Con Ed a coordinated home fleet as a **network relief resource** under DLRP/CSRP, Value Stack or RADR-style contracts.
- Sell upstate host communities and data-centre or fab developers a **community-benefit battery program**. Energize NY and A11560 push large loads to fund host-community benefits.
- No New York example exists yet of a data centre funding home batteries. This is an opening, not a precedent.

---

## 6. Study 2a: load added and supply removed

- **Crypto in the North Country (2017–22).**
  - Plattsburgh's municipal utility gets cheap NYPA hydro only up to a cap. Two miners used 10–15% of its power, and winter overages bought at market prices raised average residential bills about $10 in Jan 2018. The city imposed an 18-month moratorium ([NCPR](https://www.northcountrypublicradio.org/news/story/35828/20180316/city-of-plattsburgh-says-no-to-new-bitcoin-mining-companies-at-least-for-now)).
  - The PSC then let 36 municipal utilities charge high-density loads more. One request it cited: a 5 MW miner would have raised the Village of Akron's bulk-power cost by 54% ([PSC](https://apps.cio.ny.gov/apps/mediaContact/public/view.cfm?backButton=&parm=AE8BF234-5056-907F-6F8268EEDE1A32C0)).
  - An NBER study estimates miners added about **$204M a year to upstate household bills** and $92M to small businesses ([NBER w31312](https://www.nber.org/papers/w31312)).
  - New York signed a 2-year moratorium on fossil-powered proof-of-work mining in 2022.
- **Supply removed (Indian Point and the peaker rule).**
  - Computed: the downstate price ratio rose after Indian Point closed (§2.2).
  - The 2023 peaker retirements took about 800 MW of NYC peakers out of service; NYC capacity rose about 400%, and NYISO found a 446 MW NYC need for 2025. Peakers were kept running ([NYISO](https://www.nyiso.com/-/press-release-%7C-nyiso-study-finds-reliability-need-in-2025-for-new-york-city-region)).
  - Q2 2026 capacity prices were up 83% in NYC and 106% on Long Island year on year.
- **Wires added.** The AC Transmission projects, Smart Path and CHPE coincide with the downstate ratio falling from 1.46 to 1.11 and the North Country's discount disappearing.
- **Large loads in 2023–26.** NYISO names them as the main uncertainty in its margins. Margins tightened mainly because **retirements outpaced additions**, not because load arrived.

**Supported:**
- New load at a utility with capped cheap power raised local winter bills, and regulators shifted the cost onto the new loads.
- Removing about 2 GW of supply raised the downstate premium and capacity prices.
- Adding transmission lowered the premium.

**Not supported:**
- That data-centre load has raised New York bills yet.
- That Indian Point alone drove 2021 prices. Gas dominated.

---

## 7. Study 2b: unplanned events beyond N-1

New York's bulk-system rules (NYSRC, NPCC) are stricter than NERC's and add local rules for NYC and Long Island. Con Ed plans NYC transmission to N-1-1-0 and its distribution networks for any two feeder failures. Events still beat the design:
- storm surge (Sandy's 14 ft surge beat every forecast)
- several feeders failing at once in heat
- hidden protection defects
- overlapping forced outages
- overhead radial lines, which are effectively N-0 against trees

For the planning standard itself, see [ERCOT §7.1](ercot.md#71-why-storms-beat-the-planning-rules).

**Events and computed prices** (15-minute averages; "max / window average / NYC congestion premium", $/MWh):

| Event | What failed | Lost | NYC (J) price | Long Island (K) price | Battery / backup role |
|---|---|---|---|---|---|
| **2003 Northeast blackout**, 14 Aug | Trees on FirstEnergy 345 kV lines in Ohio, plus control-room failures → cascade | **80% of New York load** (28.7 GW → 5.7 GW); NYC out about 30 h | **The real-time market stopped at 16:00 on 14 Aug.** Only hourly placeholder values were posted until 5-minute pricing resumed on 18 Aug (computed from the files). Max $159 before the stop. | – | Backup generators. The event led to the NERC vegetation standard FAC-003. |
| 2006 heat + Long Island City network failure, Jul–Aug | 10 of 22 feeders failed in heat | At least 174k people, up to 9 days *(unverified)* | 1,466 / 138 / +58 (the heat drove the spike) | **2,139** / 199 | None |
| **Superstorm Sandy**, 29 Oct 2012 | Storm-surge flooding, wind | **2.22M customers**, about 5.2 GW; 12-day restoration | **245 / 32 / +5: no spike** | 264 / 30 | NYU and Co-op City (40 MW) microgrids kept running |
| **Polar vortex**, 6–8 Jan 2014 | Gas shortage; NYC gas $13.82 → $56.59/MMBtu | None | 1,209 / **265** / +91 | 1,295 / **366** | Voluntary demand response |
| 2017–18 cold snap | Gas, up to $175/MMBtu | None | 564 / 123 / +61 | 576 / 130 | Plants switched to oil |
| **Manhattan blackout**, 13 Jul 2019 | A cable fault, then relay misoperation at W 65th St | about 73k customers, 5 h | **129 / 32: no spike** | 174 / 37 | None |
| **Tropical Storm Isaias**, 4 Aug 2020 | Wind and trees on overhead lines | About 900k at peak (1.5M in total) | **107 / 20: no spike** | 143 / 18 | None. Utilities paid $86.2M in settlements. |
| **Winter Storm Elliott**, 23–26 Dec 2022 | Cold; gas interruptions to downstate plants | None | **4,607** / 440 / +168 | **4,657** / 474 | Not needed |
| **June 2025 heat wave**, 22–26 Jun | Heat; fossil units 17.1% unavailable against 6.4% expected | 2-hour reserve emergency on 24 Jun; 1,960 MW of emergency energy bought | 2,960 / 200 / +30 | **6,197** / 263 | More than 95% of NYC storage discharged at the peak *(advocacy filing)* |
| **Winter Storm Fern / cold spell**, 24–28 Jan 2026 | Cold; oil resupply problems at 3.9 GW of plants | None found | **3,769** / 370 / 0 | 2,367 / 250 | Oil and demand response; DOE emergency order |
| July 2026 heat, 30 Jun–4 Jul | Heat plus forced outages; CHPE tripped twice | Con Ed cut about 9,800 Queens customers and reduced voltage for about 400k | Top days: 1 Jul NYC $2,656; 3 Jul Long Island **$3,663** | | NineDot batteries 34 MW *(self-reported)* |

Sources: [DOE 2003 blackout report](https://www.energy.gov/sites/prod/files/oeprod/DocumentsandMedia/BlackoutFinal-Web.pdf); [NERC Sandy report](https://www.nerc.com/globalassets/our-work/reports/event-reports/hurricane_sandy_ear_20140312_final.pdf); [FERC 2014](https://ferc.gov/sites/default/files/2020-05/01-16-14-bulk-power.pdf); [DPS 2019 report](https://documents.dps.ny.gov/public/Common/ViewDoc.aspx?DocRefId=%7BB7BC48C1-632B-439E-A363-AD7E5DE68C80%7D); [DPS Isaias](https://dps.ny.gov/system/files/documents/2022/10/governor-cuomo-announces-completion-of-tropical-storm-isaias-utility-investigation.pdf); [NYISO Elliott review](https://www.iso-ne.com/static-assets/documents/2023/05/2023-05-18-egoc-a3-2-nyiso-post-winter-2022-23-review.pdf); [Potomac Q1 2026](https://www.potomaceconomics.com/wp-content/uploads/2026/07/NYISO-Quarterly-Report_2026Q1_revised_7-20-2026.pdf); [Con Ed Jul 2026](https://www.coned.com/en/about-us/media-center/news/2026/07-03/con-edison-working-to-restore-service-to-customers-impacted-by-extreme-heat).

**The same split as ERCOT, now across 23 years.**
- **Fuel and weather scarcity** (2014, 2018, Elliott, June 2025, Fern) produced the spikes. Downstate congestion added $60–240/MWh on top.
- **Distribution and local-substation disasters** produced the customer outages, and prices stayed flat: Sandy max $245, the 2019 blackout $129, Isaias $107.
- **The 2003 cascade shut the market itself down for three days.**
- In the sources reviewed, NYISO never ordered firm load shedding for a supply shortfall. Con Ed did cut load locally in 2019 and 2026 to protect networks.

**Reliability statistics.** Excluding major storms:
- Con Ed's underground networks rarely fail but stay out long: 0.017–0.086 interruptions per customer a year, 1.8–6.7 hours each.
- Overhead radial lines fail more often for shorter spells: 0.40–0.53 a year, 1.9–2.7 hours.
- Trees cause 42% of NYSEG's interruptions.
- In 2024 New York had 42 major storms; storm customer-hours rose 161% ([DPS 2022 report](https://dps.ny.gov/system/files/documents/2023/08/electric-service-reliability-report-2022.pdf); [DPS 2024 update](https://dps.ny.gov/news/psc-receives-update-utility-electric-reliability-and-gas-safety-standards-0)).

---

## 8. What this means for Batter Up

If the team ever ports Batter Up to New York:
- the price location becomes a NYISO zone (J or K);
- the money stack becomes utility relief programs plus capacity plus energy, not ERCOT's energy plus ADER;
- the scenario to show is a Con Ed network heat event: several feeders lost, managed charging, 4-hour discharge at 9–10 pm.

The same simulator applies. In New York, the feeder-aware controller is the product Con Ed is asking for.

---

## Method and data

- **Prices:** NYISO "Real-Time Zonal LBMP" monthly zips, Jan 2003 to Sep 2026 (285 files, 314 MB), downloaded 3 Oct 2026 from `http://mis.nyiso.com/public/csv/realtime/YYYYMM01realtime_zone_csv.zip`. The hashes are in [`data/nyiso/SOURCE-SHA256SUMS.txt`](data/nyiso/SOURCE-SHA256SUMS.txt).
- **Processing:** [`scripts/nyiso_zone_study.py`](scripts/nyiso_zone_study.py).
  - Timestamps mark interval ends. Before about 2005 the intervals were irregular, so every price is time-weighted into 15-minute averages.
  - The repeated fall-back hour is shifted by one hour.
  - Congestion premium = minus the posted congestion column.
  - Only the 11 load zones are analysed.
  - The battery model is identical to the ERCOT study: 1 MW / 2 MWh, 85% round trip, at most one cycle a day, empty at midnight, perfect foresight. It is an upper bound on energy-only arbitrage and leaves out capacity and ancillary services.
- **Outputs:** [`data/nyiso/`](data/nyiso/) holds `zone_year_stats.csv`, `battery_value.csv`, `event_windows.csv`, `top_days.csv` and `hour_profile.csv`.
- **Caveats:**
  - Time-weighted averages run below the market monitor's load-weighted ones.
  - The ratio to Zone C partly, not fully, cancels gas-price swings.
  - Attributing North Country parity to transmission or flexible load is an interpretation, not a finding.
- **Research:** three research passes on 3 Oct 2026 read NYISO, the market monitor, the PSC/DPS, NYSERDA, Con Ed, NERC/FERC and DOE first, and trade press second. Some chart values were read from market-monitor PDFs and are accurate to about ±1%.
