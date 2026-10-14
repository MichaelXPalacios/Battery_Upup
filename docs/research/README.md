# Grid research: large loads, price geography and where batteries pay

*Batter Up research, 3 October 2026.* Two markets, two studies each:

- **Study 1.** Where prices spike and separate. What the interconnection queues say about the large-load wave over 1, 5 and 10 years. Who pays for upgrades (deferrals, credits, partnerships).
- **Study 2.** What history shows when large load is added (or supply removed), and what happens in unplanned events beyond N-1: storms, transformer and line failures, trees on lines.

| Report | Market | Price data computed here |
|---|---|---|
| [ercot.md](ercot.md) | ERCOT (Texas); Batter Up's home market, DFW / LZ_NORTH | 15-minute Settlement Point Prices for all load zones and hubs, 2021 to 26 Sep 2026 |
| [nyiso.md](nyiso.md) | NYISO (New York); the longest keyless price history and the most developed payments for local relief | 5-minute zonal LBMPs, 2003 to Sep 2026, averaged to 15 minutes |
| [arena/](arena/README.md) | Both markets: **five battery algorithms compete** on dispatch and siting over 1, 3 and 5 years, on real prices (backtest) and 2027–31 futures that include weather, storms, programs and upgrades | ERCOT 2011–2026, NYISO 2003–2026, NOAA weather, Henry Hub gas |

The scripts are in [`scripts/`](scripts/) and the computed tables in [`data/`](data/). The raw downloads (about 80 MB from ERCOT, 314 MB from NYISO) are not in the repo; each report's Method section says how to fetch them and lists their hashes.

---

## Say it right

| Loose phrase | ERCOT | NYISO |
|---|---|---|
| "nodal zones" | Load pays its **load zone** Settlement Point Price (LZ_NORTH etc.). Prices are computed at **electrical buses** (LMPs). | Load pays its **zonal LBMP** (zones A–K). Generators are paid at their **bus**. |
| "MW/hr in $" | **$/MWh** for energy; **$/MW per hour** for ancillary services | **$/MWh** for energy (the files write "$/MWHr"); **$/kW-month** for capacity |
| "regions sectioned off" | Areas behind **binding constraints and GTCs**: West Texas export, Panhandle, Rio Grande Valley, Permian import, Houston import, and now DFW-internal constraints | **Import-constrained pockets** (NYC, Long Island, NYC sub-pockets) and **export-constrained areas** (North Country, Oswego, sometimes West), measured against the Marcy reference bus |
| "spikes" | **Scarcity pricing**, capped at $5,000/MWh; congestion can push single nodes higher | Offer cap $1,000 ($2,000 with verified costs). **Shortage pricing** reaches $4,000 in pockets; Long Island hit a 5-minute peak of $7,338 in 2025. |

---

## ERCOT and NYISO side by side

| | ERCOT | NYISO |
|---|---|---|
| Market design | Energy-only, plus ancillary services co-optimized in real time since Dec 2025 | Energy + reserves + **locational capacity market** |
| Price separation today | **DFW moving to a premium**: +$2.09/MWh against the ERCOT average in 2026, with 106 intervals more than $100 above and none more than $100 below. West's premium (+$9 to +$11, 2023–25) faded in 2026. Panhandle negative about 20% of the time. | **The wall is shrinking**: NYC price ratio to Central fell from 1.46 (2022) to 1.11 (2026) after new wires (AC Transmission, CHPE). North Country went from a 20% discount to parity. |
| Spike timing | Moved from afternoon (2022) to 7–9 pm (2023–24), then to nights and winter mornings (2025–26) as about 14–18 GW of batteries arrived | Still 3–9 pm (53–80% of spikes in 2023–25). Fewer than 100 MW of batteries in the market. |
| Energy-arbitrage ceiling, 2025 / 2026 (computed, 1 MW / 2 MWh, perfect foresight) | LZ_NORTH **$51 / $54** per kW-yr; LZ_WEST $63 / $54 | NYC **$71 / $73**; Long Island **$84 / $79** |
| Share of value from the 10 best days | 21–55% (lumpy) | 9–35% (steadier) |
| Capacity | None | NYC 4-hour battery earned **$104–133/kW-yr** in 2023–25; NYC spot hit a record **$32.6/kW-month** in May 2026 |
| Payments for local relief | **SB 415** utility storage contracts (100 MW statewide, still proposed). Co-op and municipal deals. ADER pilot (500 MW cap). | Con Ed **$18–25/kW-month**; Value Stack **$141–199/kW-yr**; Brooklyn-Queens **$2,500–3,000/kW**; utility battery programs $50/kW-yr; NYSERDA **$125–450/kWh** upfront |
| Large-load queue | **About 411–475 GW**, of which Oncor holds 259 GW. About 5.9 GW energized. Governor paused new data centres ≥ 75 MW (Aug 2026). | **12.7 GW**, mostly upstate. Forecast counts 2.9 GW by 2040. Executive order paused data-centre permits ≥ 50 MW (Jul 2026). |
| Reliability outlook | NERC rates near-term emergency risk low thanks to batteries; high risk from 2029 if load arrives | Thinnest margins in years (417 MW, summer 2026). NYC and Long Island needs. Con Ed wants storage for 125 → 675 MW (2033–36). |
| Unplanned events | Uri and 6 Sep 2023 spiked prices. The derecho and Beryl caused outages, but prices stayed flat (Houston max $54 during Beryl). | Elliott ($4,607), Fern and June 2025 spiked prices. Sandy, Isaias and the 2019 blackout caused outages with flat prices. The 2003 cascade stopped the real-time market for three days. |

---

## Answers to the questions that started this

1. **Is there public queue data?** Yes, for both. ERCOT's monthly Generation Interconnection Status report has every project. Large-load totals appear in ERCOT Board decks; there is no project-level file yet. NYISO's queue workbook includes a Load Projects list. LBNL's *Queued Up* and Interconnection.fyi cover all ISOs.
2. **Where do batteries relieve the grid?**
   - At the evening net peak and, increasingly in ERCOT, on winter mornings.
   - In pockets behind constraints: DFW, the Permian, the Rio Grande Valley, NYC and Long Island.
   - On overloaded transformers and feeders.
   - As backup power when the distribution system fails.
   - As fast response when a large load trips.
3. **Where do prices spike?**
   - ERCOT: the West and Permian (2023–25), the Rio Grande Valley, Houston (2022), and DFW and the Rayburn zone (2026).
   - NYISO: Long Island and NYC first, then the Hudson Valley and Capital.
4. **Large loads over 1, 5 and 10 years.** Each report has a table.
   - ERCOT: the wave is enormous on paper and paused in practice. Where it lands (DFW, the Panhandle, the Valley), local constraints and battery value follow.
   - New York: modest load growth, but tight reliability margins already make local storage valuable.
5. **Who pays for upgrades?**
   - New York pays directly for distribution relief today.
   - Texas is opening: SB 415 contracts, data-centre cost responsibility under SB 6 and the Governor's directives, and co-op deals.
   - The federal §48E credit still covers third-party-owned storage for projects starting by 2033.
6. **Does adding large load hurt reliability and raise prices?**
   - Where supply and wires lag, yes: PJM capacity, ERCOT West, and small-town New York crypto.
   - Where they keep up, no: ERCOT's 2024 prices fell by half.
   - Large loads add a new risk: groups of them tripping offline together during faults.
   - Removing supply (Indian Point) raised prices just as adding load does.
7. **Unplanned events beyond N-1.** In both markets and across 23 years of New York data:
   - System scarcity (cold, heat, fuel) produces the price spikes, which batteries earn from.
   - Distribution disasters (storms, trees, substation failures) produce the outages, with flat or falling prices. Only batteries at the home help customers then.
   - Batter Up's design (feeder-aware, with home backup) covers the second case and can still bid into the first.

---

## Method in one paragraph

Prices come from each ISO's own public files: ERCOT NP6-785-ER and NYISO's real-time zonal LBMP archive. They were downloaded on 3 Oct 2026, and the hashes are recorded. NYISO's 5-minute and irregular intervals were time-weighted to 15 minutes to match ERCOT. Statistics are time-weighted, so they run a little below the market monitors' load-weighted averages. The battery figure is a perfect-foresight upper bound for a 1 MW / 2 MWh battery cycling once a day; it is not a revenue forecast. Everything else comes from seven research passes on 3 Oct 2026. Those passes read primary sources first: the ISOs, market monitors, PUCT/PSC, NERC, FERC, EIA, DOE and NYSERDA. Figures from a single secondary source are marked *(unverified)*. This is research, not tax or legal advice.
