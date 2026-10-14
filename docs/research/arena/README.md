# Battery algorithm arena: five strategies, two markets, 1, 3 and 5 years

*Batter Up research, 3 October 2026.* Five battery algorithms compete to make the most money per MW and to learn where the next battery should go. They run on real ERCOT and NYISO prices (a walk-forward backtest) and on 2027–2031 futures built from real weather-years (a forward tournament). The forward tournament includes local programs, transformer upgrades, transmission upgrades, storms and outages. It builds on the price and policy research in [`../ercot.md`](../ercot.md) and [`../nyiso.md`](../nyiso.md).

**Units.** "$/kW-yr" means dollars of profit per kW of battery per year, which is the same as thousands of dollars per MW-year. Prices are $/MWh. Profit is energy sales minus charging cost minus a $20/MWh wear charge, plus capacity and program payments where they exist, minus upgrade costs. Battery purchase cost is not subtracted; the break-even installed cost is shown instead.

---

## Results first

1. **The Adaptive learner (A5) wins the long race.** It has the best 5-year result in:
   - backtest dispatch in both markets
   - the backtest siting race in New York and across both markets
   - the 2027–29 and 2027–31 forward tournaments: 70% and 75% of ERCOT futures, every New York future

   It wins by not committing to one method. Each day, in each region, it follows whichever expert has earned the most recently. In New York that is mostly the Weather-regime algorithm (42–68% of days). In ERCOT it mixes Clockwork, Spike hunter and Weather-regime.
2. **No single dispatch rule wins every year.**
   - In ERCOT, Weather-regime won the Uri year (2021) and Forecaster won 2023.
   - Clockwork won 2024 and 2026, when batteries had made the evening peak regular.
   - In New York, Weather-regime wins most years.
   - The year's weather and the market's battery saturation decide which rule wins. That is why the learner, which adapts, takes the five-year title.
3. **Energy trading alone is thin.** Realistic dispatch on a Batter Up home battery earns $11–44/kW-yr in ERCOT and $4–20 in New York. That is after wear and the 20% backup reserve, and works out to 37–44% of the perfect-foresight ceiling for the best algorithms.
4. **Contracts and capacity decide where the next battery goes.** The best site across both markets is a New York City battery enrolled in Con Ed's load-relief program:
   - median $232/kW-yr in 2027–29
   - made up of capacity $127, program $90, event energy $15 and trading $16, less $16 of transformer upgrades
5. **In ERCOT, Batter Up's home market, the ranking is:**
   - an Austin Energy-style tolling contract ($102, if a new tranche opens)
   - DFW data-centre flexibility ($82 median, from 2028, if such a deal exists)
   - co-op peak deals in the LCRA and Rayburn zones ($77–82)
   - SB 415 feeder contracts in DFW and Houston ($59–61)
   - merchant-only sites ($27–33)
6. **Infrastructure cuts both ways.**
   - Transmission that relieves a constraint removes the battery's edge there: the Permian 765-kV lines cost a West battery about $15/kW-yr, and Propel NY costs a Long Island battery about $18/kW-yr.
   - On the distribution side, concentrating home batteries where a program pays needs transformer upgrades for 30% of Cores on the repo feeder: about $16/kW-yr. That is worth paying when a program pays $50–90.
7. **Weather equations per region** quantify the spikes:
   - A 5 °F hotter July day more than doubles ERCOT's daily price spread (+104% to +148% by zone) and roughly doubles the spike chance.
   - A 10 °F colder January day lifts ERCOT spreads 38–60%.
   - New York is more sensitive to gas: +50% gas widens spreads 18–26%. Long Island spikes on about 1 July day in 4.
8. **Storms and backup.** The battery's charge when an outage starts (as hours of home load) differs by algorithm:

   | Algorithm | Backup hours |
   |---|---:|
   | Spike hunter | 25 |
   | Weather-regime | 22–23 |
   | Forecaster | 20–21 |
   | Adaptive learner | 16–20 |
   | Clockwork | 6 (the reserve only) |

   Weather-regime raises its reserve before wind, flood-rain and hard-freeze days. It deliberately leaves heat days for discharging, because heat brings price spikes, not distribution outages.
9. **Totals.** Building 10 MW a year from 2027 (50 MW by 2031), the learner's fleet earns about **$13.5M in ERCOT** and **$35.6M in New York** over 2027–31 in the average future. Bad-case (P10) totals are $11.7M and $27.8M. These are before the cost of the batteries.

---

## How the arena works

### Regions, data and battery

| | ERCOT | NYISO |
|---|---|---|
| Regions | 8 load zones (DFW `LZ_NORTH`, Houston, South, West, Austin Energy, CPS, LCRA, Rayburn) | 11 zones A–K |
| Prices | 15-minute real-time settlement point prices, **2011 to 26 Sep 2026** (NP6-785-ER) | 5-minute zonal LBMPs time-weighted to 15 minutes, **2003 to Sep 2026** |
| Weather | NOAA daily Tmax/Tmin, peak wind, rain at 8 airport stations, plus a load-weighted system index | 10 stations, plus a system index |
| Gas | Henry Hub daily spot (EIA), 1997–2026 | same |

- **The battery** is the repo's Batter Up home fleet, scaled per MW.
  - Base Core: 20 kW / 37 kWh usable and 89% round trip, so 1.85 MWh per MW. Usable for trading: 1.5 MWh.
  - **The 20% member reserve is a hard floor.**
  - Wear is $20/MWh discharged (ASSUMPTION).
  - A 1 MW / 2 MWh grid battery is reported alongside for comparison.
- Each algorithm sees prices only up to yesterday, plus today's weather (standing in for a day-ahead forecast) and yesterday's gas price.

### The five competitors

| | Algorithm | How it dispatches | How it sites the next 10 MW each January |
|---|---|---|---|
| A1 | **Clockwork** | Charges in the cheapest and discharges in the dearest fixed window of the last 4 weeks' average day, every day | All of it into last year's best option |
| A2 | **Forecaster** | Forecasts tomorrow's 96 prices from recent days, with swings rescaled to a typical day. A dynamic programme turns the forecast into "water values" (what stored energy is worth each interval). It then acts on each real price as it arrives. | 3-year mean plus half the trend; at most 5 MW per option a year |
| A3 | **Weather-regime** | Region equations plus 24 analog days: past days with similar system and local weather, gas and recent prices, rescaled for fuel. It plans for each analog day as a full price path and re-weights the paths through the day toward those that track today's real prices (Bayesian). It holds 53% backup before wind, flood-rain or freeze days. | 5-year mean minus half the standard deviation; 50/30/20% to the top three |
| A4 | **Spike hunter** | Keeps the battery full; buys below the 25th percentile of the last 60 days and sells only above the 97th | Best single year of the last three; 60/40% to the top two |
| A5 | **Adaptive learner** | Runs all the above (plus two variants) as shadow batteries and follows, per region, whichever has earned the most over a ~33-day memory. It switches only on a clear lead. | Recency-weighted estimate of each option, less a quarter standard deviation, placed greedily per MW. It trusts the latest year after a structural break. |

A perfect-foresight **ceiling** is computed for every region-year as a yardstick. Under the earlier price studies' rules it reproduces their numbers: NYC 2025 $70.7 and Long Island $83.9 exactly, and DFW within a dime ($51.2 vs $51.1, the gap is daylight-saving handling).

### Region equations (the "multiple equations per region")

For each region, three equations are refitted every January on all earlier years:
- **level:** log of the daily mean price
- **spread:** log of the gap between the 8 dearest and 8 cheapest intervals
- **spike:** probability of a $500 (ERCOT) or $300 (NYISO) interval, a ridge logistic regression

Inputs: system and local cooling/heating degree-days, extreme heat above 100/90 °F, extreme cold below 32/15 °F, log gas, yesterday's level, spread and spike flag, season and weekend.

The spike equation beats each zone's historical spike rate out of sample in every test year (Brier skill +0.03 to +0.30). An earlier, weakly regularised version did not, and was fixed before the runs.

**ERCOT (model trained on 2011–2025):**

| Region | July spread ($/MWh) | +5 °F hotter | Spike chance → hotter | January spread | −10 °F colder | Spike chance → colder | +50% gas: price · spread | Spike chance after a spike day |
|---|---:|---:|---|---:|---:|---|---|---:|
| DFW (LZ_NORTH) | 40 | +128% | 6.1% → 12.3% | 28 | +56% | 5.2% → 8.3% | +15% · +12% | 9.6% |
| Houston | 42 | +146% | 7.8% → 16.1% | 30 | +38% | 5.3% → 6.7% | +17% · +15% | 11.3% |
| South / Valley | 41 | +148% | 7.3% → 14.6% | 33 | +60% | 5.6% → 9.0% | +14% · +9% | 12.2% |
| West / Permian | 52 | +105% | 6.1% → 12.7% | 50 | +41% | 6.6% → 9.9% | +10% · +6% | 8.8% |
| Austin Energy | 40 | +104% | 7.0% → 11.4% | 32 | +47% | 6.0% → 9.2% | +15% · +13% | 10.6% |
| San Antonio (CPS) | 38 | +137% | 6.6% → 14.5% | 33 | +47% | 6.0% → 8.2% | +14% · +12% | 9.7% |
| LCRA co-ops | 40 | +133% | 6.5% → 12.8% | 32 | +58% | 5.3% → 8.5% | +15% · +13% | 9.7% |
| Rayburn co-ops | 39 | +126% | 6.1% → 12.4% | 28 | +54% | 5.2% → 8.5% | +15% · +12% | 9.3% |

**NYISO (model trained on 2003–2025):**

| Region | July spread ($/MWh) | +5 °F hotter | Spike chance → hotter | January spread | −10 °F colder | Spike chance → colder | +50% gas: price · spread | Spike chance after a spike day |
|---|---:|---:|---|---:|---:|---|---|---:|
| NY West (A) | 55 | +36% | 11.7% → 16.2% | 47 | +9% | 8.1% → 9.3% | +12% · +20% | 19.9% |
| Genesee (B) | 42 | +27% | 4.9% → 6.5% | 48 | +10% | 7.1% → 9.2% | +13% · +26% | 7.4% |
| Central NY (C) | 44 | +26% | 5.5% → 7.3% | 50 | +9% | 7.7% → 9.9% | +13% · +25% | 8.6% |
| North Country (D) | 50 | +26% | 5.4% → 7.4% | 51 | +14% | 7.3% → 10.2% | +17% · +24% | 8.8% |
| Mohawk Valley (E) | 47 | +33% | 5.7% → 8.4% | 51 | +12% | 8.4% → 11.5% | +12% · +25% | 8.5% |
| Capital (F) | 46 | +35% | 6.1% → 8.6% | 66 | +14% | 14.3% → 18.2% | +12% · +20% | 8.9% |
| Hudson Valley (G) | 52 | +41% | 7.9% → 12.6% | 58 | +7% | 11.9% → 14.6% | +13% · +23% | 10.5% |
| Millwood (H) | 57 | +38% | 10.0% → 14.1% | 59 | +5% | 12.6% → 15.0% | +13% · +24% | 13.5% |
| Dunwoodie (I) | 58 | +38% | 10.0% → 14.1% | 59 | +5% | 12.5% → 14.8% | +13% · +25% | 13.4% |
| New York City (J) | 60 | +57% | 9.4% → 15.6% | 64 | +6% | 14.2% → 17.1% | +15% · +26% | 13.1% |
| Long Island (K) | 93 | +44% | 22.9% → 31.0% | 86 | +6% | 23.0% → 26.4% | +15% · +18% | 32.7% |

Baselines are a median July (or January) weekday of the last five years.
- ERCOT is a heat market. Its spreads more than double on a 5 °F hotter day, and spikes cluster: one day's spike lifts the next day's odds.
- New York is a gas-and-pocket market. Spreads respond more to gas than to cold, and Long Island's spike odds after a spike day reach a third.

### Siting, programs and upgrades (forward tournament)

Each January every algorithm places 10 MW, 1 MW at a time. Options are:
- a **merchant site** in any region (energy trading, plus New York capacity);
- or a **program site** that pays for availability in a utility or co-op peak window.

On event days a program forces the battery to be full at the window start and to discharge through it, so the opportunity cost comes out of the simulation. Programs (details and sources in [`arena/economics.py`](arena/economics.py)):

| Program | Regions | Pays | Window / events | Starts · chance | Concentrated (upgrades)? |
|---|---|---|---|---|---|
| Con Ed load relief | NYC | $18/kW-month × 5 + $1/kWh events (REAL rates) | 2–6 pm, 10 hottest weekdays | 2027 · 100% | yes |
| Con Ed Westchester relief | Millwood, Dunwoodie | $6/kW-month × 5 + $1/kWh (REAL rates) | 2–6 pm, 10 days | 2027 · 100% | yes |
| Utility battery program | upstate zones, Hudson Valley, Long Island | $50/kW-yr (REAL rate) | 2–7 pm, 10 days | 2027 · 100% | no |
| TX co-op peak | LCRA, Rayburn | $50/kW-yr (ASSUMPTION) | 3–6 pm, 20 hottest weekdays | 2027 · 100% | no |
| Austin Energy tolling | Austin Energy | $102/kW-yr, utility dispatches (DERIVED upper bound) | n/a | 2027 · 50% | no |
| SB 415 feeder contract | DFW, Houston | $50/kW-yr (ASSUMPTION; the rule is only proposed) | 5–9 pm, 15 hottest days | 2028 · 50% | yes |
| Data-centre flexibility | DFW | $60/kW-yr (ASSUMPTION; modelled on the Piq/Base study) | 2–8 pm, 30 hottest days | 2028 · 35% | yes |

- **Transformer upgrades** come from the repo's SMART-DS feeder, an Oncor-suburb stand-in at LZ_NORTH.
  - Of 1,007 homes, 689 can take a Core without an upgrade under the nameplate screen.
  - 298 more fit after 239 one-size-up transformer swaps at $10,000 each (Base to the PUCT).
  - Concentrated program sites therefore pay $121/kW, or $16.1/kW-yr over 12 years at 8%.
- **New York capacity** is the locality price times the storage accreditation factor. A 1.85-hour Core is credited 92% of a 2-hour battery: NYC 63.8%, Long Island 55.1%.

### Forward scenarios (2027–2031)

There are 20 futures per market. Each strings together five whole historical weather-years:
- ERCOT draws 2022–25 with 70% weight.
- New York weights 2016–25 double.
- "Surge" and "tight" futures double the weight on scarcity years from 2029.

Each drawn day's prices are then adjusted:
- **Transmission:** the zone premium is multiplied by kappa. In ERCOT, the Permian premium is held at its 2026 fade and halves again after the 765-kV lines (70% of futures). DFW's premium rises 60% if large loads land (50%). In New York, NYC's premium is halved by CHPE, and Long Island's halves after Propel NY in 2030 (70%).
- **Fuel:** Henry Hub at $3.00, $3.75 or $4.75 (±10% a year).
- **Market shape:** in ERCOT, spreads shrink 6% a year if batteries saturate and grow 4% a year after 2028 if load surges. In New York, supply relief versus tight supply.
- **Spike retention.**
- **Caps.**
- **Outages:** storms, trees and substation failures island the battery for days, at a rate set per region.

---

## Detailed results

### Backtest: dispatch on real prices (home fleet, energy only, $/kW-yr, mean across regions)

**ERCOT**

| Algorithm | 1 year (2025) | 3 years (2023–25) | 5 years (2021–25) | $ per MWh discharged (5 y) | Share of perfect foresight (5 y) |
|---|---:|---:|---:|---:|---:|
| A1 Clockwork | 13.1 | 41.6 | 34.3 | 63 | 37% |
| A2 Forecaster | 11.7 | 40.7 | 34.6 | 77 | 37% |
| A3 Weather-regime | 11.3 | 31.2 | 29.4 | 64 | 32% |
| A4 Spike hunter | 11.8 | 24.0 | 23.2 | 206 | 25% |
| **A5 Adaptive learner** | **13.8** | **44.1** | **38.1** | 95 | **41%** |
| Perfect foresight (ceiling) | 37.2 | 86.4 | 92.8 | | 100% |

**NYISO**

| Algorithm | 1 year (2025) | 3 years (2023–25) | 5 years (2021–25) | $ per MWh discharged (5 y) | Share of perfect foresight (5 y) |
|---|---:|---:|---:|---:|---:|
| A1 Clockwork | 13.3 | 4.4 | 4.6 | 8 | 11% |
| A2 Forecaster | **20.0** | **13.0** | 15.9 | 39 | 39% |
| A3 Weather-regime | 17.7 | 12.5 | 16.1 | 35 | 39% |
| A4 Spike hunter | 11.2 | 9.0 | 11.6 | 131 | 28% |
| **A5 Adaptive learner** | 19.6 | 12.8 | **16.2** | 39 | **40%** |
| Perfect foresight (ceiling) | 48.0 | 30.5 | 40.8 | | 100% |

- **Spike hunter earns the most per MWh but the least overall.** It sells only into the top 3% of prices and cycles about a tenth as often as the others, at $206/MWh in ERCOT.
- **Clockwork fails in New York.** Its peak timing moves with weather and gas, and on two-peak winter days a single fixed window loses money.
- The grid-scale battery, with more energy and no reserve, earns about 15% more and ranks the algorithms the same way. ERCOT 5-year: A5 $44.5, A2 $43.0, A1 $39.8.

**Region by region, 5 years** ($/kW-yr; best in bold):

| Region | A1 | A2 | A3 | A4 | A5 | Ceiling |
|---|---:|---:|---:|---:|---:|---:|
| DFW (LZ_NORTH) | 31.8 | 33.1 | 28.4 | 22.4 | **35.9** | 87.9 |
| Houston | 35.4 | 37.4 | 31.6 | 23.9 | **39.4** | 94.3 |
| South / Valley | 28.0 | 28.0 | 24.9 | 19.0 | **32.4** | 81.7 |
| West / Permian | 38.3 | 38.7 | 30.5 | 28.2 | **41.1** | 107.3 |
| Austin Energy | 35.6 | 36.4 | 31.3 | 23.8 | **41.1** | 95.3 |
| San Antonio (CPS) | 36.8 | 34.7 | 29.4 | 22.5 | **38.8** | 91.9 |
| LCRA co-ops | 37.3 | 35.2 | 30.7 | 23.1 | **39.4** | 95.7 |
| Rayburn co-ops | 31.2 | 33.3 | 28.4 | 22.7 | **36.7** | 88.1 |
| North Country (D) | −0.8 | **17.2** | 14.9 | 14.0 | 16.8 | 43.6 |
| Capital (F) | 4.8 | **18.8** | 18.0 | 11.6 | **18.8** | 49.2 |
| Hudson Valley (G) | 4.5 | 13.9 | **14.6** | 10.4 | **14.6** | 36.5 |
| New York City (J) | 6.8 | 16.5 | **17.4** | 11.2 | 16.3 | 42.4 |
| Long Island (K) | 16.5 | 29.2 | **31.4** | 18.6 | 31.1 | 71.4 |

The other New York zones (A, B, C, E, H, I) follow the same pattern; all 19 regions are in [`results/tables.md`](results/tables.md).

**Who wins which year** (mean across regions, $/kW-yr):

| Year | ERCOT winner | NYISO winner |
|---|---|---|
| 2019 | Learner (58.1; Clockwork 53.2) | Weather-regime |
| 2021 (Uri) | **Weather-regime (23.5 vs Forecaster 18.0)** | Learner |
| 2022 | Learner (39.2) | Weather-regime |
| 2023 | Forecaster (90.9) | Weather-regime |
| 2024 | Clockwork (28.8) | Weather-regime |
| 2025 | Learner (13.8) | Forecaster |
| 2026 to date | Clockwork | Weather-regime |

### Backtest: siting race (home fleet, energy only, 10 MW a year, $/kW-yr)

| Scope | A1 | A2 | A3 | A4 | **A5** | A5 with Thompson siting (first design) |
|---|---:|---:|---:|---:|---:|---:|
| ERCOT, 5 years (2021–25) | 32.8 | 32.9 | 26.2 | 23.9 | 35.8 | 36.9 |
| NYISO, 5 years | 16.3 | 20.3 | 19.7 | 14.1 | **27.6** | 20.4 |
| Both markets, 5 years | 25.8 | 30.3 | 24.0 | 23.9 | **35.8** | 35.3 |
| Both markets, 1 year (2025) | 14.8 | 13.1 | 14.1 | 12.9 | **17.5** | 15.0 |

The learner's first siting design used Thompson sampling, which explores by drawing from its beliefs. Every option's value is observable here, because the arena simulates every region, so exploring has no information value and mostly costs money. Its replacement places greedily on a recency-weighted, risk-adjusted estimate. Both are reported; Thompson sampling came out ahead only in the ERCOT backtest (36.9 vs 35.8).

### Forward tournament, 2027–2031 (home fleet, full revenue stack, $/kW-yr)

Each cell shows the mean across 20 futures, the P10–P90 range, and the share of futures won (ties share the win).

**ERCOT**

| Algorithm | 2027 (1 y) | 2027–29 (3 y) | 2027–31 (5 y) |
|---|---|---|---|
| A1 Clockwork | 90.1 · 66.1–102.0 · 42% | 90.6 · 71.3–104.6 · 15% | 86.5 · 70.6–97.1 · 10% |
| A2 Forecaster | 86.7 · 61.9–115.5 · 35% | 89.7 · 72.7–112.3 · 15% | 87.9 · 73.2–100.2 · 15% |
| A3 Weather-regime | 78.3 · 51.5–93.2 · 0% | 80.8 · 63.5–95.6 · 0% | 79.7 · 65.5–92.3 · 0% |
| A4 Spike hunter | 83.3 · 62.5–94.1 · 0% | 84.3 · 71.5–93.9 · 0% | 82.4 · 72.5–93.1 · 0% |
| **A5 Adaptive learner** | **91.3** · 65.6–102.0 · 22% | **93.8** · 77.2–107.5 · **70%** | **90.3** · 77.7–104.1 · **75%** |

**NYISO** (both markets together give the same result, because every algorithm builds in New York)

| Algorithm | 2027 (1 y) | 2027–29 (3 y) | 2027–31 (5 y) |
|---|---|---|---|
| A1 Clockwork | 225.7 · 177.4–272.4 · 0% | 224.9 · 175.1–272.4 · 0% | 224.7 · 175.2–272.9 · 0% |
| A2 Forecaster | 193.0 · 144.8–240.5 · 0% | 192.7 · 141.9–239.5 · 0% | 192.8 · 140.9–243.1 · 0% |
| A3 Weather-regime | 189.3 · 148.8–231.2 · 0% | 188.8 · 143.6–231.8 · 0% | 188.3 · 143.4–233.9 · 0% |
| A4 Spike hunter | 199.2 · 150.0–250.2 · 0% | 198.9 · 148.1–246.7 · 0% | 198.9 · 147.7–248.6 · 0% |
| **A5 Adaptive learner** | **237.6** · 191.5–285.6 · **100%** | **237.7** · 186.3–286.6 · **100%** | **237.6** · 185.6–288.8 · **100%** |

**Energy trading only, forward** (median future, 2027–31, $/kW-yr):
- ERCOT: the learner leads in every zone, at $32–39. Forecaster is close behind; Weather-regime and Spike hunter sit at $23–29.
- New York: the learner, Forecaster and Weather-regime earn $13–18 per zone and $29–30 on Long Island. Clockwork earns $1–11.

### Where the next battery should go

**First 10 MW in 2027 and the average fleet by 2031** (forward tournament):

| Market | Learner's first 10 MW | Learner's 2031 fleet (of 50 MW) |
|---|---|---|
| ERCOT | Austin Energy-style tolling 10 (where a tranche exists) | LCRA co-op peak 33.5, Austin Energy tolling 11.0, Rayburn co-op peak 5.5 |
| NYISO / both | NYC + Con Ed load relief 10 | NYC + Con Ed load relief 50 |

**Site ranking** (median 2027–29 $/kW-yr, learner's dispatch; P10 in brackets):

| Rank | Both markets | ERCOT only |
|---:|---|---|
| 1 | NYC + Con Ed load relief 232 (186) | Austin Energy tolling 102 (102), *if a tranche opens* |
| 2 | NYC merchant 145 (98) | DFW + data-centre flexibility 82 (54), *from 2028, if offered* |
| 3 | Long Island + utility battery program 124 (105) | LCRA co-op peak 82 (65) |
| 4 | Hudson Valley + utility battery program 103 (89) | Rayburn co-op peak 77 (62) |
| 5 | Austin Energy tolling 102 (102) | Houston + SB 415 feeder contract 61 (44) |
| 6 | NY West + utility battery program 95 (84) | DFW + SB 415 feeder contract 59 (45) |
| 7 | Capital + utility battery program 94 (83) | Houston merchant 33 (13) |
| 8 | North Country + utility battery program 93 (84) | LCRA merchant 29 (16) |
| 12 | Westchester + Con Ed relief 82–83 (69) | DFW merchant 27 (12) |

**Where the money comes from** (learner, median future, 2027–29, $/kW-yr):

| Site | Energy trading | Capacity | Program payment | Event energy | Transformer upgrades | Total |
|---|---:|---:|---:|---:|---:|---:|
| NYC + Con Ed load relief | 16.4 | 126.8 | 90.0 | 15.0 | −16.1 | 232.1 |
| NYC merchant | 17.5 | 126.8 | 0 | 0 | 0 | 144.3 |
| Long Island + utility battery program | 29.7 | 42.6 | 50.0 | 0 | 0 | 122.3 |
| Austin Energy tolling | 0 | 0 | 102.0 | 0 | 0 | 102.0 |
| Hudson Valley + utility battery program | 15.7 | 36.4 | 50.0 | 0 | 0 | 102.2 |
| LCRA + TX co-op peak | 33.3 | 0 | 50.0 | 0 | 0 | 83.3 |

**Break-even installed cost.** This is what a site's yearly value can repay over the 12-year Batter Up contract at 8% (DERIVED):
- NYC + Con Ed relief: about $1,750/kW
- an ERCOT co-op peak site: about $630/kW
- an ERCOT merchant-only site: about $220/kW

A home battery that relies on energy trading alone does not pay for itself in ERCOT. Contracts, capacity, programs and the membership fee are what make the fleet work. The membership fee is $19 a month, or $11.4/kW-yr for a 20 kW Core.

### Infrastructure upgrades

**Transmission upgrades.** Value of a battery in the region after minus before the upgrade year, compared with the same split in futures without the upgrade (energy only, learner):

| Transmission upgrade | Region | Effect ($/kW-yr) | Futures with / without |
|---|---|---:|---:|
| Permian 765-kV lines | West / Permian | −15.0 | 14 / 6 |
| Propel NY (2030) | Long Island | −17.6 | 12 / 8 |
| DFW large loads land (premium +60%) | DFW | −1.3 | 7 / 13 |
| Valley import upgrade (2029) | South / Valley | +8.8 | 6 / 14 |

- **Relieving an import constraint removes a battery's local edge.** Before betting a site on a pocket's premium, check the transmission plan for that pocket.
- **DFW's large-load premium barely moves arbitrage value,** because it shifts the price level more than the intraday spread. DFW's value case is contracts: SB 415 and data-centre deals.
- **The Valley result is surprising and rests on six futures.** One possible reason is that the Valley's wind pushes its prices down at the evening peak, so relieving the constraint lets its spread widen toward the system's. Treat it as a hypothesis.

**Distribution upgrades.** On the repo feeder, 30% of Cores in a concentrated build need a transformer swap: $121/kW, or $16.1/kW-yr. Every concentrated program site in the ranking already pays this and still beats merchant sites.

---

## What this means for Batter Up

1. **Run the Adaptive learner, not a fixed rule.** It won the 5-year race in both markets because the right rule changes with the weather year and with battery saturation. All its parts are deterministic code, as the repo requires: no language model picks a setpoint.
2. **In DFW, sell availability, not just energy.** DFW merchant trading is worth about $27/kW-yr. Contract and program sites are worth 2–3× that: co-op peak deals, SB 415 feeder contracts and data-centre flexibility. The feeder-aware controller is what makes those contracts deliverable.
3. **Weather-proof the reserve.** Raising backup before wind, flood-rain and freeze days costs little: Weather-regime still delivered 22–23 backup hours. Heat days stay open for discharging.
4. **If the company ever expands, New York City's Con Ed networks are the best site in this study.** The numbers rest on the capacity price assumption (see caveats).
5. **Check each pocket's transmission plan before siting there,** since a new line can remove a pocket's premium within a few years.

---

## Caveats and assumptions

- **This is a model, not a forecast.**
  - The forward futures re-use real weather-years with labelled adjustments. Their structural assumptions (battery saturation, load surge, gas, transmission events, program terms and chances) are research-grounded ASSUMPTIONS listed in [`arena/forward.py`](arena/forward.py) and [`arena/economics.py`](arena/economics.py).
  - Twenty futures per market give a usable range, not precise tails.
- **The New York capacity price drives New York's lead.** The base future uses $18/kW-month for NYC: the 2025/26 average was $10.98 and May 2026 set a record of $32.6. The "relief" future at $12 still keeps NYC first. Capacity accreditation for a 1.85-hour Core is an assumption.
- **ERCOT contract values are uncertain.**
  - Austin Energy's $8.50/kW-month is an upper bound ("up to").
  - The co-op value ($50/kW-yr), SB 415 terms and the data-centre deal are assumptions.
  - SB 415 is only a proposed rule, and the data-centre case is modelled on an industry-sponsored, hypothetical study.
- **Feeder cost transfer.** Transformer upgrade costs come from the Oncor-suburb stand-in feeder (overhead radial). Con Ed's underground networks differ.
- **Wear cost.** $20/MWh replaces the price studies' hard one-cycle-per-day cap. A hidden hard cap made forward-looking planners spend their allowance on morning swings: a perfect-forecast planner fell to 34–41% of the capped ceiling. Under one shared objective, a perfect-forecast planner matches the ceiling exactly.
- **What the backtests leave out.** The backtests score energy trading only. Ancillary services, ERCOT's ADER caps, NYISO's day-ahead reserves and the costs of selling into markets are not modelled.
- **Day-ahead weather.** Today's observed weather stands in for a day-ahead forecast. Real forecasts are good at one day but not perfect.
- **Bugs found and fixed before the final runs:**
  - a hidden cycle cap
  - storm reserve triggering on heat
  - an overconfident spike equation
  - flattened averaged forecasts
  - interval-independent scenarios

  Each fix is described in the code comments.

---

## How to run

```bash
cd docs/research/arena
python run_arena.py backtest     # ~50 min on 8 cores: both markets, both battery specs
python run_arena.py forward 20   # ~40 min: 20 futures per market
python run_arena.py report       # scores everything -> results/arena_results.json
python make_tables.py            # -> results/tables.md
```

Use a Python 3.12+ environment with numpy and openpyxl. `ARENA_DATA` must point to the downloaded data:
- ERCOT NP6-785-ER yearly zips 2011–2026
- NYISO monthly real-time zonal zips 2003–2026
- NOAA GHCN-Daily station files for the 18 stations in [`arena/config.py`](arena/config.py)
- Henry Hub daily spot from EIA (`gas/DHHNGSP.csv`)

The research download used is described in [`../ercot.md`](../ercot.md) and [`../nyiso.md`](../nyiso.md).

| File | What it does |
|---|---|
| [`arena/config.py`](arena/config.py) | Regions, stations, battery specs, labelled assumptions |
| [`arena/data.py`](arena/data.py) | Prices, weather and gas, aligned and cached |
| [`arena/battery.py`](arena/battery.py) | Battery physics, dynamic programmes, the day-by-day engine |
| [`arena/models.py`](arena/models.py) | Region equations, analog days, what-if effects |
| [`arena/algorithms.py`](arena/algorithms.py) | The five algorithms and the combined runner |
| [`arena/siting.py`](arena/siting.py) | Siting rules |
| [`arena/economics.py`](arena/economics.py) | Capacity, programs, feeder upgrade costs |
| [`arena/forward.py`](arena/forward.py) | Forward futures |
| [`arena/backtest.py`](arena/backtest.py) | Walk-forward backtest |
| [`results/`](results/) | Scored results (JSON) and every table (Markdown) |
