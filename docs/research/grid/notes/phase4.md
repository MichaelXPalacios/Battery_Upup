# Phase 4: transmission upgrades, non-wires alternatives and who can make them happen (10 Oct 2026)

Agent 4. Code: `gridsite/upgrades.py` (everything), `gridsite/cli_4.py` (commands). Outputs, each with a `.labels.csv` sidecar (REAL / DERIVED / SIM / ASSUMPTION):
`data/upgrades.csv` (25 elements, 75 columns), `data/upgrade_options.csv` (108 wires options with their cost formulas), `data/nwa_sizing.csv` (423 rows: element x side x
3 relief targets x 3 coverage targets), `data/upgrade_rerank_first_order.csv` (724 rows). Tests: `tests/test_4_upgrades.py` (16 tests, about 1 minute).

Run from `docs/research/grid` with the research venv: `run_grid.py upgrades`, `nwa`, `rerank-first-order` (each 40-50 s on an idle machine, several minutes when other phases are running), `upgrades check` (recomputes the derived constants and prints them next to the named ones).
Nothing was installed. One file was downloaded (NREL/TP-6A40-93281, 0.7 MB, recorded in `manifest.csv`); everything else was already in `raw/docs`.

## Findings first

1. **A battery does not beat the wires fix on cost for almost any of these elements.** For the 24 elements that could be sized, the battery that relieves 10% of the limit in 80% of binding hours (best side, mid cost) costs a
   median **21x** the primary wires fix (range 0.5x to 72x). On a 138 kV line that is 45 MW and
   317 MWh (median), against a wires fix of $1.3-50M (reconductor or rebuild); on a 345 kV line 188-502 MW.
   Only one element is below 1x at the central scenario, and the close calls are the ones where the wires fix is very large:
   - **WESTEX** (the interface): 2,696 MW / 15,999 MWh costs $4,470M, **0.49x the $9,060M Permian 765 kV plan**. The plan also
     serves load growth and the interface is stability-limited, so this is a sizing exercise, not an alternative ERCOT could adopt; the SFs behind it are attenuated day-ahead fits divided by their shrink factor (confidence "attenuated").
   - **Long rebuilds (close calls)**: Longshore-Consavvy ($223M rebuild against $322M of battery at the central scenario, 1.45x) and Odessa EHV-Yarbrough
     ($50M against $75M, 1.50x). Both drop below 1x at 5% relief or 50% coverage. Their shift factors are the assumed class values (no regression SF exists),
     so treat them as indicative.
2. **The real competitor is a rating change, not a new line.** Dynamic or ambient-adjusted ratings cost $0.25-3.4M per element (DOE 2019: 10-20% typical uplift, 6-14% over ambient-adjusted ratings) and a reconductor $1.3-45M.
   Against the cheapest wires option whose typical uplift covers the relief, the battery costs a median 192x as much. ERCOT's SCED limits already follow ambient temperature on some lines
   (Scurry Chevron-Knapp swings 17% across the day, from 238 MW in the early morning to 201 MW between 16:00 and 19:00), and the 5-20% relief scenarios are the same size as that swing.
3. **Persistent binding defeats an energy-limited resource.** Scurry Chevron-Knapp needs 3.2 / 7.8 / 13.2 hours of storage to cover 50 / 80 / 95% of its binding hours (its episodes are long);
   a 4-hour battery covers 0.58 of them. Twin Buttes-Hargrove binds in the day-ahead market in 53% of the window's hours in 24 episodes
   (the longest runs for days); a 10-hour battery, the limit of the cost data, covers only 0.41 of its binding hours, so the table caps the duration at 10 h and reports the coverage reached.
4. **Lead times do not favour the battery.** A reconductor or rebuild takes 26 / 48 / 61 months (completed-project median / pipeline median / third quartile, RPG submittal
   to in service, TPIT); an autotransformer 36 / 48 / 66 (floored by the large-power-transformer delivery of 30-60 months plus 6 to install).
   A battery takes 36 months from interconnection request when it is under about 90 MW per site (FIS median 20.5 months plus 15 of construction) and 56 when its step-up is a large power transformer (100 MVA or more at 138 kV and above, as in Phase 3a).
5. **8 of 25 elements have a planned fix we can name** (match 0.5 or above, or reviewed): WESTEX interface (PBRP-IMPORT1, 2031); LNGSW-CONSW 345 kV (TPIT-81268, 2028-05-15); BELCNTY-SALSW 138 kV (TPIT-87673, 2027-05-15); SALDS-SONTERRA 138 kV (TPIT-80546D, 2027-05-15); PKRSW-HCKSW 345 kV (TPIT-105326, 2028-12-15); ODEHV-YARBR 138 kV (TPIT-81418, 2026-12-15); PASP_PATA interface (CN2025-06, 2027); LWSVH-ARGYL 138 kV (TPIT-110248, 2032-12-01).
   Another 3 are in the RTP as "identified" with no date or cost (Twin Buttes-Hargrove, College-Jupiter, Simpson Stuart-Buntin Drive) and 6 have only a nearby project.
   **No project was found for Scurry Chevron-Knapp, ENCRT-BRAND, Koch Tap-Buzzard Switch, the La Palma transformer or HIGH36-BRENNO.**
6. **The unfixed structural constraints are cheap to fix.** For the elements with a 2025 IMM rent and no firm project, one year of 2025 rent is 10-452 times the yearly cost of the reconductor
   (Vealmoor-Koch Tap 452x, Koch Tap-Buzzard Switch 346x, Laredo VFT North-Las Cruces 180x, Scurry Chevron-Knapp 101x).
   Rent is not consumer savings, but the PUCT's congestion test compares a saving with the annual revenue requirement (16 TAC 25.101(b)(3)(A)(i)), so the economics of a fix are not the obstacle. Rents do not vanish quickly either:
   of the 2024 real-time top ten, 6 are absent from the 2025 top ten (fixed, reconfigured or relieved by outages ending) and 4 persisted (WESTEX $130M to $89M, ODEHV-YARBR $91M to $70M, TWINBU-HARGROVE $80M to $145M, BELCNTY-SALSW $52M to $94M).
   Treat a pocket premium as lasting about one lead time (3-4 years) unless a dated project says sooner.
7. **Where an upgrade would move siting (first order, no dispatch).** The big movers are the import-side nodes of the constraints with the largest rent per MW of limit:
   Twin Buttes-Hargrove (MIDW, PHBL, STRS fall from about $138-153 to $16-17 per kW-yr and from ranks 1-3 to 53-91), Scurry Chevron-Knapp (KNAPP $31 to $6), the Highlands-Argyle corridor (ARGYL and its neighbours $26 to $3, planned for 2032),
   PK-DT in Houston ($27 to $4), FORTMA-YELWJCKT around Mason ($17 to $2) and HIGH36-BRENNO ($17-18 to $3-4). A 2 h battery captures about 0.20 of the shadow-price-hours, a 4 h battery 0.31
   (0.41 learner share x the cover a best-2-hours-a-day or best-4-hours-a-day schedule can reach). **Each per-kW value holds only for the first `sat_mw` of batteries behind the constraint**
   (the MW that already relieves 20% of the limit: 26 MW at MIDW, 105 MW at KNAPP). 8 of the 25 upgrades cannot be ranked because no usable regression SF exists (see section 4).
8. **Who decides.** The TSP, through the RPG for anything above a ratings change, and the PUCT through a CCN for a new line. Costs go into the TSP's transmission cost of service and are recovered from load by the 4CP method. A battery owner can propose a project to the RPG,
   can pay for a project the TSP elects to take (NP 3.11.4.11), and pays its own interconnection facilities above the $14M / $20M allowance, but cannot order, own or fund a network upgrade on its own. **No PUCT or ERCOT rule today lets a TSP buy transmission-level storage
   in place of a line**; the only statute is distribution-only (PURA 35.153, SB 415) and caps contracts at 100 MW. Details and citations in section 6.

## The table

Costs are 2024 dollars from the MISO MTEP24 Texas rows in `data/unit_costs.csv` times a quantity, widened to at least -20% / +50% around the mid value where the source cells are narrower. Lead times run from RPG submittal to in service.
"Battery / wires" is the battery's mid cost at the central scenario over the primary option's mid cost; "rent / yearly wires cost" is the 2025 IMM rent over mid cost x 11.5%. Rows marked "not sized" have no limit in any public source.

| id | element | type, kV | length (mi) | limit (MW) | primary wires option | cost low / **mid** / high ($M, 2024$) | lead time low / mid / high (months) | planned | battery at 10% relief, 80% coverage | battery / wires cost | 2025 IMM rent / yearly wires cost |
|---|---|---|---|---|---|---|---|---|---|---|---|
| U01 | SCRCV-KNAPP 138 kV | line 138 | 12.4 | 216 | reconductor | 4.7 / **5.9** / 14.0 | 26 / 48 / 61 | no | 50 MW / 388 MWh (7.8 h) | 17.6 | 101 |
| U02 | VEALMOOR-KOCHTAP 138 kV | line 138 | 7.8 | 237 | reconductor | 3.3 / **4.1** / 9.4 | 26 / 48 / 61 | adjacent TPIT-81305A, 2026-09-30 | 45 MW / 269 MWh (6.0 h) | 18.5 | 452 |
| U03 | TWINBU-HARGROVE 138 kV | line 138 | 30.9 | 151 | reconductor | 10.5 / **13.1** / 32.4 | 26 / 48 / 61 | identified RTP2025-2022-WFW01 | 16 MW / 159 MWh (10.0 h, capped) | 3.1 | 96 |
| U04 | WESTEX interface | gtc | - | 10,629 | plan_765kv | 7,248.0 / **9,060.0** / 13,590.0 | 51 / 57 / 75 | yes PBRP-IMPORT1, 2031 | 2,696 MW / 15,999 MWh (5.9 h) | 0.5 | 0.1 |
| U05 | HIGH36-BRENNO 138 kV | line 138 | 4.9 | 241 | reconductor | 2.3 / **2.9** / 6.5 | 26 / 48 / 61 | no | 35 MW / 319 MWh (9.1 h) | 28.2 | - |
| U06 | PK-DT 138 kV | line 138 | 0.8 | 293 | reconductor | 1.1 / **1.3** / 2.3 | 26 / 48 / 61 | hint TPIT-64499, 2026-12-31 | 62 MW / 337 MWh (5.4 h) | 72.3 | - |
| U07 | LNGSW-CONSW 345 kV | line 345 | 66.8 | 1,197 | rebuild | 178.0 / **222.5** / 333.8 | 34 / 52 / 75 | yes TPIT-81268, 2028-05-15 | 311 MW / 973 MWh (3.1 h) | 1.4 | 3.7 |
| U08 | BELCNTY-SALSW 138 kV | line 138 | 7.8 | 237 | rebuild | 12.0 / **15.1** / 22.6 | 26 / 48 / 61 | yes TPIT-87673, 2027-05-15 | 45 MW / 269 MWh (6.0 h) | 5.0 | 54 |
| U09 | ENCRT-BRAND 138 kV | line 138 | 4.9 | 410 | reconductor | 2.3 / **2.9** / 6.5 | 26 / 48 / 61 | no | 74 MW / 564 MWh (7.6 h) | 51.3 | - |
| U10 | SALDS-SONTERRA 138 kV | line 138 | 14.4 | 232 | reconductor | 5.3 / **6.6** / 16.0 | 26 / 48 / 61 | yes TPIT-80546D, 2027-05-15 | 59 MW / 392 MWh (6.7 h) | 16.1 | - |
| U11 | COLLEGE-JUPITER 138 kV | line 138 | 4.9 | 295 | reconductor | 2.3 / **2.9** / 6.5 | 26 / 48 / 61 | identified RTP2025-2025-NC06 | 61 MW / 362 MWh (6.0 h) | 34.4 | - |
| U12 | PKRSW-HCKSW 345 kV | line 345 | 23.0 | 1,743 | reconductor | 13.8 / **17.2** / 26.2 | 34 / 52 / 75 | yes TPIT-105326, 2028-12-15 | 499 MW / 4,315 MWh (8.7 h) | 65.6 | - |
| U13 | LA_PALMA-LA_PALMA 138 kV | xfmr 138 | - | 630 | autotransformer | 9.4 / **11.8** / 17.6 | 36 / 48 / 66 | no | 116 MW / 1,155 MWh (10.0 h, capped) | 25.1 | - |
| U14 | ODEHV-YARBR 138 kV | line 138 | 27.2 | 237 | rebuild | 40.0 / **50.0** / 75.0 | 26 / 48 / 61 | likely TPIT-81418, 2026-12-15 | 45 MW / 269 MWh (6.0 h) | 1.5 | 12 |
| U15 | KOCHTAP-BUZSW 138 kV | line 138 | 1.8 | 237 | reconductor | 1.4 / **1.7** / 3.4 | 26 / 48 / 61 | no | 45 MW / 269 MWh (6.0 h) | 43.4 | 346 |
| U16 | PASP_PATA interface | gtc | - | - | plan_unpriced | - | - | likely CN2025-06, 2027 | not sized | - | - |
| U17 | LARDVNTH-LASCRUCE 138 kV | line 138 | 4.9 | 237 | reconductor | 2.3 / **2.9** / 6.5 | 26 / 48 / 61 | adjacent RPG-26RPG004, 2029-09 | 45 MW / 269 MWh (6.0 h) | 25.6 | 180 |
| U18 | STP-WAP 345 kV | line 345 | 68.5 | 1,317 | reconductor | 36.3 / **45.4** / 71.7 | 34 / 52 / 75 | hint TPIT-81238, 2028-05-31 | 502 MW / 3,014 MWh (6.0 h) | 18.5 | 9.9 |
| U19 | LWSVH-ARGYL 138 kV | line 138 | 20.0 | 216 | reconductor | 7.1 / **8.8** / 21.6 | 26 / 48 / 61 | likely TPIT-110248, 2032-12-01 | 41 MW / 346 MWh (8.4 h) | 10.3 | 50 |
| U20 | SMPST-BNTDR 138 kV | line 138 | 4.9 | 232 | reconductor | 2.3 / **2.9** / 6.5 | 26 / 48 / 61 | identified RTP2025-2025-NC41 | 86 MW / 544 MWh (6.3 h) | 51.1 | - |
| U21 | SGVSW-SGVSW 345 kV | xfmr 345 | - | 746 | autotransformer | 9.4 / **11.8** / 17.6 | 36 / 48 / 66 | no | 235 MW / 1,093 MWh (4.7 h) | 27.4 | - |
| U22 | BRAND_C-COLLEGE 138 kV | line 138 | 4.9 | 272 | reconductor | 2.3 / **2.9** / 6.5 | 26 / 48 / 61 | no | 53 MW / 315 MWh (6.0 h) | 29.9 | - |
| U23 | FORTMA-YELWJCKT 138 kV | line 138 | 37.6 | 151 | reconductor | 12.5 / **15.7** / 39.1 | 26 / 48 / 61 | hint TPIT-70710, 2027-11-30 | 31 MW / 292 MWh (9.3 h) | 4.8 | - |
| U24 | WLY-WAP 345 kV | line 345 | 8.8 | 1,790 | reconductor | 6.7 / **8.4** / 12.6 | 34 / 52 / 75 | hint TPIT-103344G, 2028-12-30 | 188 MW / 625 MWh (3.3 h) | 24.3 | - |
| U25 | SEA-SEA 138/69 kV | xfmr 138 | - | 99 | autotransformer | 3.3 / **4.2** / 6.2 | 36 / 54 / 69 | no | 15 MW / 83 MWh (5.7 h) | 5.6 | - |

Blank length = transformer or interface. `planned`: yes (match 0.8 or above, or reviewed), likely (0.5 or above), identified (in the RTP, no date), adjacent / hint (a nearby project that is not the element), no.
Lengths come from `lines.csv` where the pair exists, else TPIT circuit-miles of a strongly matched or reviewed project, else station distance x 1.25, else the class median (`length_basis` in `upgrades.csv`).

## 1. Which elements

`select_elements()`: the top 15 distinct monitored elements by `priority_rank` in `constraints.csv` (an element is a `constraint_name`; its contingencies are its constraints), plus every element with a 2025 IMM top-10 rent
even if it never bound in the window (13 of the 25 did not bind in the real-time week; 7 never priced at all, real time or day-ahead: VEALMOOR-KOCHTAP, LNGSW-CONSW, BELCNTY-SALSW, ODEHV-YARBR, KOCHTAP-BUZSW, PASP_PATA, LARDVNTH-LASCRUCE; the others bound day-ahead only: TWINBU-HARGROVE, WESTEX, HIGH36-BRENNO, PKRSW-HCKSW, LA_PALMA-LA_PALMA, SMPST-BNTDR; all ten
required structural elements are in, `tests/test_4_upgrades.py` checks it), plus the 6 best remaining local window constraints by the window part of the priority score
(SMPST-BNTDR, the SGVSW transformer, BRAND_C-COLLEGE, FORTMA-YELWJCKT, WLY-WAP, the SEA transformer). Result: 25 elements, 20 lines, 3 transformers, 2 interfaces.
Ids U01.. follow priority rank; `apply_upgrade` accepts them.

## 2. What an upgrade costs and how long it takes

For every element `wires_options()` builds the options a TSP could file, each with a cost formula, a typical rating uplift (an option can serve a relief target only if its uplift is at least the target) and a lead time. Example, Scurry Chevron-Knapp (U01, 12.4 miles):

| option | low / mid / high ($M) | uplift | lead time (months) | CCN | formula (mid) |
|---|---|---|---|---|---|
| reconductor (primary) | 4.7 / 5.9 / 14.0 | 40% | 26 / 48 / 61 | no | 12.4 mi x $0.39M/mi (reconductor per circuit [138]) + 2 line ends x 0.3 x $1.7M bay |
| rebuild | 18.7 / 23.3 / 35.0 | 60% | 26 / 48 / 61 | no | 12.4 mi x $1.80M/mi (rebuild of existing single-circuit line [138]) + 2 line ends x 0.3 x $1.7M bay |
| new_parallel_line | 25.5 / 31.9 / 47.9 | 100% | 44 / 54 / 57 | yes | 12.4 mi x $2.30M/mi (new single-circuit line [138]) + 2 x $1.7M bay |
| dlr_aar | 0.3 / 0.6 / 0.9 | 10% | 6 / 12 / 18 | no | 12.4 mi x $0.050M/mi (dynamic line rating, sensors and software [69-345]), floor $0.25M; uplift 10% (SCED limit already follows ambient temperature, swing 17%) |
| substation_rebuild | 8.8 / 11.0 / 16.5 | 30% | 36 / 40 / 51 | no | 1 x $11.0M (substation: new 4-position substation (ring bus) [138]); helps only if the limit is substation equipment, which public data cannot show |

Rules behind the arithmetic:
- Reconductor / rebuild: miles x the per-circuit row, plus two line ends at 0.3 of a new bay each (`TERMINAL_BAY_FRACTION`). A 345 kV rebuild has no row, so the 138 kV rebuild-to-new ratio (1.8 / 2.3) is applied to the 345 kV new-line row. Where the matched planned project is a rebuild,
  the rebuild is the primary option (Bell County-Salado, Longshore-Consavvy, Odessa EHV-Yarbrough); otherwise the primary is the reconductor.
- New parallel line: miles x the new single-circuit row plus two bays; needs a CCN.
- Autotransformer: the smallest standard size (150, 300, 450, 600, 750, 900, 1200 MVA) at or above the element's rating, times $6,880 / MVA (345/138 kV; $5,050 for 138/69 kV), plus one bay on each side.
- DLR / AAR: miles x $23k / $50k / $60k per mile, floor $0.25M.
- Substation rebuild: the new 4-position ring-bus row; it helps only if the limit is substation equipment, which public data cannot show.
- WESTEX: the three 765 kV import paths, $9,060M (ERCOT Permian Basin Reliability Plan study, approved in PUCT Project 55718).
- Lead time: quartiles of (projected in service - RPG submittal) over the TPIT July 2026 pipeline for the same project class and kV class (n = 87 for 138 kV line rebuilds / reconductors,
  25 for 345 kV autotransformers); the low value is the median of completed projects (actual in-service dates) when there are at least 10. TPIT lead times include the slack of a pipeline sized for load growth,
  so they are longer than a minimal build; completed 138 kV line projects took a median of 26 months. Autotransformers are floored by transformer delivery (DOE 2024: 36 months commonly quoted, up to 60; NLR 2026: 30 months average in H1 2025; 345 kV bushings alone 130 weeks).
- The MISO "reconductor per circuit" and "rebuild" cells are read from a garbled PDF layout and carry UNVERIFIED in `unit_costs.csv`; every row that uses one has `cost_uses_unverified_cell = True`.
  A cross-check against a TSP number: Oncor's Rockhound-Longshore 345 kV double-circuit line (RPG-26RPG015) is priced at $144M for 51 miles, $2.8M per mile, against MISO's $4.0-6.3M, so MISO new-line rows may run high for ERCOT.

## 3. Could a battery do the job? (`data/nwa_sizing.csv`)

Market data cannot say how much relief an element needs, because SCED keeps flow at or below the limit. So relief is a named scenario: 5, 10 and 20% of the median limit (ASSUMPTION). Coverage is 50, 80 and 95% of binding hours.

- **Energy.** Binding episodes come from the RT window (5-minute SCED runs, 3-10 Oct plus 25 Sep, 8.5 days) or the DAM window (31 days), as the union over the element's contingencies with shadow price at least $1/MWh.
  Binding periods less than 60 minutes apart merge into one episode (no time to recharge). The duration D(x) is the smallest duration such that a battery that sustains relief for at most D hours from the start of each episode covers x of all binding hours;
  `coverage(D) = sum(min(e_i, D)) / sum(e_i)`, solved exactly. MWh = MW x D (at least 1 h, at most 10 h; beyond that the coverage reached is reported). An element with fewer than 6 window episodes (the ones that never bound) uses the pooled episodes of its class.
- **Power.** MW = relief / |SF| at the best site on the side that relieves: sites with SF < 0 (import side, the battery discharges) and sites with SF > 0 (export side, it charges). The SF is `rt_reg` first, then the non-attenuated `dam_reg`;
  an attenuated day-ahead SF (WESTEX, shrink 0.65) is divided by its shrink factor and the row is marked "attenuated"; the physics proxy is never used for a magnitude. Where nothing but the proxy exists
  (Vealmoor-Koch Tap, Longshore-Consavvy, Bell County-Salado, Koch Tap-Buzzard Switch) or there is no SF at all (Odessa EHV-Yarbrough, Laredo VFT North-Las Cruces) the SF is the class median of the best-site |SF| of the window-fitted RT lines
  (138 kV: import 0.55, export 0.26; 345 kV: import 0.39, export 0.41) and the row is marked "assumed". |SF| is capped at 1, sites below 0.05 are not used,
  each site is capped (50 MW at 69 kV, 300 MW at 138 kV and above) and the MW is spread greedily over sites by |SF|. Installed MW = required MW / 0.95.
- **Cost.** NREL's June 2025 update (Cole, Ramasamy, Turan, NREL/TP-6A40-93281, the cost source of the ATB 2025 utility-scale battery page): total $/kW = $241/kWh x hours + $372/kW (2024$; 4 h = $334/kWh = $1,336/kW), multiplied by 0.77 / 0.92 / 1.10
  for the low / mid / high 2026 projection. A 2 h battery is $786/kW and a 4 h one $1,229/kW at the mid case. Fixed O&M is 4%/yr of capital, life 15 years. The wires fix is the primary option; the table also gives the cheapest option whose uplift covers the relief
  (`ratio_vs_cheapest_mid`) and an annualised ratio (battery: 8% over 15 years plus 4% O&M; wires: 11.5% fixed charge).
- **Market revenue is a reference, never netted.** `ref_zone_energy_usd_kw_yr` is the arena's ERCOT 5-year learner value for a 2 h grid battery ($44.5/kW-yr) and `ref_congestion_usd_kw_yr` the first-order congestion value at the best site (section 4);
  `ref_market_rev_musd_yr` multiplies their sum by the installed MW. For Scurry Chevron-Knapp that is 4.2 $M a year against $103M of capex.

Scurry Chevron-Knapp, all nine scenarios (best side):

| relief | coverage | side | site, SF | D(x) h | battery h | covers | MW | MWh | battery $M (low / mid / high) | wires $M | ratio |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 5% (11 MW) | 50% | import discharge | KNAPP, -0.46 | 3.2 | 3.2 | 0.50 | 25 | 79 | 22 / 26 / 31 | 5.9 | 4.4 |
| 5% (11 MW) | 80% | import discharge | KNAPP, -0.46 | 7.8 | 7.8 | 0.80 | 25 | 194 | 43 / 51 / 62 | 5.9 | 8.8 |
| 5% (11 MW) | 95% | import discharge | KNAPP, -0.46 | 13.2 | 10.0 | 0.88 | 25 | 248 | 53 / 63 / 76 | 5.9 | 10.8 |
| 10% (22 MW) | 50% | import discharge | KNAPP, -0.46 | 3.2 | 3.2 | 0.50 | 50 | 158 | 44 / 52 / 62 | 5.9 | 8.9 |
| 10% (22 MW) | 80% | import discharge | KNAPP, -0.46 | 7.8 | 7.8 | 0.80 | 50 | 388 | 86 / 103 / 123 | 5.9 | 17.6 |
| 10% (22 MW) | 95% | import discharge | KNAPP, -0.46 | 13.2 | 10.0 | 0.88 | 50 | 496 | 106 / 127 / 152 | 5.9 | 21.7 |
| 20% (43 MW) | 50% | import discharge | KNAPP, -0.46 | 3.2 | 3.2 | 0.50 | 99 | 316 | 87 / 104 / 124 | 5.9 | 17.8 |
| 20% (43 MW) | 80% | import discharge | KNAPP, -0.46 | 7.8 | 7.8 | 0.80 | 99 | 776 | 172 / 206 / 246 | 5.9 | 35.2 |
| 20% (43 MW) | 95% | import discharge | KNAPP, -0.46 | 13.2 | 10.0 | 0.88 | 99 | 992 | 212 / 254 / 303 | 5.9 | 43.3 |

Twin Buttes-Hargrove, all nine scenarios (the duration is capped at 10 h; "covers" is the share of binding hours that duration actually reaches):

| relief | coverage | side | site, SF | D(x) h | battery h | covers | MW | MWh | battery $M (low / mid / high) | wires $M | ratio |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 5% (8 MW) | 50% | import discharge | MIDW, -1.19 | 17.4 | 10.0 | 0.41 | 8 | 79 | 17 / 20 / 24 | 13.1 | 1.6 |
| 5% (8 MW) | 80% | import discharge | MIDW, -1.19 | 71.7 | 10.0 | 0.41 | 8 | 79 | 17 / 20 / 24 | 13.1 | 1.6 |
| 5% (8 MW) | 95% | import discharge | MIDW, -1.19 | 104.3 | 10.0 | 0.41 | 8 | 79 | 17 / 20 / 24 | 13.1 | 1.6 |
| 10% (15 MW) | 50% | import discharge | MIDW, -1.19 | 17.4 | 10.0 | 0.41 | 16 | 159 | 34 / 41 / 49 | 13.1 | 3.1 |
| 10% (15 MW) | 80% | import discharge | MIDW, -1.19 | 71.7 | 10.0 | 0.41 | 16 | 159 | 34 / 41 / 49 | 13.1 | 3.1 |
| 10% (15 MW) | 95% | import discharge | MIDW, -1.19 | 104.3 | 10.0 | 0.41 | 16 | 159 | 34 / 41 / 49 | 13.1 | 3.1 |
| 20% (30 MW) | 50% | import discharge | MIDW, -1.19 | 17.4 | 10.0 | 0.41 | 32 | 318 | 68 / 81 / 97 | 13.1 | 6.2 |
| 20% (30 MW) | 80% | import discharge | MIDW, -1.19 | 71.7 | 10.0 | 0.41 | 32 | 318 | 68 / 81 / 97 | 13.1 | 6.2 |
| 20% (30 MW) | 95% | import discharge | MIDW, -1.19 | 104.3 | 10.0 | 0.41 | 32 | 318 | 68 / 81 / 97 | 13.1 | 6.2 |

## 4. First-order rerank (`data/upgrade_rerank_first_order.csv`)

For every constraint k with a usable SF and every substation n (69 kV and above):

    V[n,k] = min(|SF[n,k] - zone_mean_SF[k]|, 1) x annual_shadow_price_hours[k] / 1000 x CAPTURE_FACTOR(spec)       ($/kW-yr)

- `SF[n,k]`: substation SF from `data/shift_factors.csv`; `rt_reg`, or `dam_reg` that is not attenuated (shrink at least 0.8). Attenuated and proxy SFs are excluded from magnitudes. 72 constraints qualify.
- `zone_mean_SF[k]`: the unweighted mean of the SF over the buses of 60 kV and above of the site's arena load zone (the NOIE zone where there is one), from `sf_long.parquet`; absent = 0. No load by bus is public.
- `annual_shadow_price_hours[k]` ($/MW-yr, the sum over a year of the shadow price in $/MWh): `imm_rent_alloc` = the 2025 IMM rent allocated to the contingency by its window share, over the element's median limit (6 constraints);
  otherwise `window_rt` / `window_dam` = window rent per MW x 365 / window days x 0.15, with each element's total capped at the 10th-ranked 2025 IMM rent ($50.83 M) over its limit (an element outside the IMM top 10 earns less than that).
  `0.15` is an ASSUMPTION; Scurry Chevron-Knapp, the one element with both, measures 0.16 (IMM RT rent $68.1M over the window annualised $429.9M).
- `CAPTURE_FACTOR` = 0.41 (the arena learner reaches 41% of the perfect-foresight ceiling in ERCOT over 5 years; 37% in 2025, 51% over 3 years) x the cover: a battery that cycles once a day with perfect foresight reaches
  0.50 of a constraint's shadow-price-hours at 2 h and 0.76 at 4 h (the best 2 or 4 hours of each day, value-weighted over the constraints with more than $200/MW of shadow-price-hours in the window; RT 0.55 / 0.81, DAM 0.45 / 0.71). So 0.205 for 2 h and 0.312 for 4 h.
  Using the learner's 37% or 51% instead scales every value by 0.90 or 1.24.
- Sides: SF - zone mean < 0 is the import side (the node price rises above the zone's when k binds: sell there); SF - zone mean > 0 is the export side (the price falls: charge there). Both lose when k is relieved.
- An upgrade scales its constraints' shadow prices by `apply_upgrade` (default residual 0.1 for a line or transformer, 0.5 for an interface). A site's value is the sum over constraints, so V1 = sum V[n,k] x scale[k].
  Sites with V0 of at least $0.5/kW-yr are ranked (4,405 sites). **Losers**: the 10 sites with the biggest loss. **Risers**: sites inside the post-upgrade best 200 whose rank improves most (their own value is unchanged
  unless they share a constraint; the loss is concentrated, so the gains in rank are small and diffuse). `PLANNED_ALL` applies every dated, not-yet-in-service planned match at once.
- **The values hold for the first MW only.** A battery behind a constraint relieves it; `sat_mw` = 20% of the limit / |dSF| is the battery size at which a fifth of the constraint is gone and the premium is eroding (PROMPT item 25, the price-taker limit).

Biggest movers (2 h spec; 3 losers and 3 risers each):

| upgrade | group | site | zone | side | $/kW-yr before | after | rank before | after | MW where it holds (sat_mw) |
|---|---|---|---|---|---|---|---|---|---|
| U01 SCRCV-KNAPP 138 kV | loser | KNAPP | LZ_WEST | import | 31.3 | 6.1 | 57 | 661 | 105 |
| U01 SCRCV-KNAPP 138 kV | loser | BCKSW | LZ_WEST | import | 24.7 | 5.1 | 72 | 918 | 136 |
| U01 SCRCV-KNAPP 138 kV | loser | SHRNE | LZ_WEST | import | 22.8 | 4.7 | 80 | 1146 | 148 |
| U01 SCRCV-KNAPP 138 kV | riser | ALISN | LZ_NORTH | - | 13.8 | 13.8 | 202 | 190 | - |
| U01 SCRCV-KNAPP 138 kV | riser | MERTZON | LZ_WEST | - | 13.6 | 13.6 | 207 | 196 | - |
| U01 SCRCV-KNAPP 138 kV | riser | BLAGG | LZ_NORTH | - | 16.7 | 16.7 | 139 | 129 | - |
| U03 TWINBU-HARGROVE 138 kV | loser | MIDW | LZ_WEST | import | 152.6 | 17.2 | 1 | 53 | 26 |
| U03 TWINBU-HARGROVE 138 kV | loser | PHBL | LZ_WEST | import | 138.0 | 15.7 | 2 | 90 | 33 |
| U03 TWINBU-HARGROVE 138 kV | loser | STRS | LZ_WEST | import | 138.0 | 15.7 | 3 | 91 | 33 |
| U03 TWINBU-HARGROVE 138 kV | riser | MECPLNVW | LZ_WEST | - | 9.8 | 9.8 | 353 | 198 | - |
| U03 TWINBU-HARGROVE 138 kV | riser | SNTX1 | LZ_WEST | - | 9.8 | 9.8 | 354 | 199 | - |
| U03 TWINBU-HARGROVE 138 kV | riser | SNYDR | LZ_WEST | - | 9.8 | 9.8 | 355 | 200 | - |
| U06 PK-DT 138 kV | loser | PK | LZ_HOUSTON | import | 27.3 | 3.9 | 62 | 1492 | 60 |
| U06 PK-DT 138 kV | loser | MID | LZ_HOUSTON | import | 26.9 | 4.0 | 64 | 1461 | 60 |
| U06 PK-DT 138 kV | loser | GT | LZ_HOUSTON | import | 26.4 | 4.0 | 65 | 1452 | 60 |
| U06 PK-DT 138 kV | riser | KERRLE | LZ_SOUTH | - | 14.2 | 14.2 | 191 | 184 | - |
| U06 PK-DT 138 kV | riser | GOODLETT | LZ_WEST | - | 14.2 | 14.2 | 192 | 185 | - |
| U06 PK-DT 138 kV | riser | SASOUTH | LZ_WEST | - | 14.1 | 14.1 | 193 | 186 | - |
| U19 LWSVH-ARGYL 138 kV | loser | CRNSO | LZ_NORTH | import | 26.1 | 3.1 | 68 | 1700 | 82 |
| U19 LWSVH-ARGYL 138 kV | loser | ARGYL | LZ_NORTH | import | 26.1 | 3.1 | 69 | 1704 | 82 |
| U19 LWSVH-ARGYL 138 kV | loser | CANYN | LZ_NORTH | import | 26.1 | 3.1 | 70 | 1705 | 82 |
| U19 LWSVH-ARGYL 138 kV | riser | REAST | LZ_NORTH | - | 12.8 | 12.8 | 241 | 197 | - |
| U19 LWSVH-ARGYL 138 kV | riser | FAIRDALE | LZ_NORTH | - | 12.7 | 12.7 | 242 | 198 | - |
| U19 LWSVH-ARGYL 138 kV | riser | KILBY | LZ_WEST | - | 12.7 | 12.7 | 243 | 199 | - |
| U23 FORTMA-YELWJCKT 138 kV | loser | MASONPHI | LZ_SOUTH | import | 16.5 | 2.0 | 148 | 2478 | 56 |
| U23 FORTMA-YELWJCKT 138 kV | loser | PUNCHER | LZ_SOUTH | import | 16.5 | 2.0 | 149 | 2479 | 56 |
| U23 FORTMA-YELWJCKT 138 kV | loser | MASONSW | LZ_SOUTH | import | 16.6 | 2.1 | 140 | 2403 | 56 |
| U23 FORTMA-YELWJCKT 138 kV | riser | CNLY_ESS | LZ_NORTH | - | 13.4 | 13.4 | 209 | 175 | - |
| U23 FORTMA-YELWJCKT 138 kV | riser | DCRSW | LZ_NORTH | - | 13.4 | 13.4 | 210 | 176 | - |
| U23 FORTMA-YELWJCKT 138 kV | riser | SCHWRTNR | LZ_NORTH | - | 13.4 | 13.4 | 211 | 177 | - |
| PLANNED_ALL every constraint with a p… | loser | ARGYL | LZ_NORTH | import | 26.1 | 2.9 | 69 | 1657 | 82 |
| PLANNED_ALL every constraint with a p… | loser | CRNSO | LZ_NORTH | import | 26.1 | 2.9 | 68 | 1656 | 82 |
| PLANNED_ALL every constraint with a p… | loser | CANYN | LZ_NORTH | import | 26.1 | 2.9 | 70 | 1655 | 82 |
| PLANNED_ALL every constraint with a p… | riser | KILBY | LZ_WEST | - | 12.7 | 12.6 | 243 | 196 | - |
| PLANNED_ALL every constraint with a p… | riser | INRT_W | LZ_WEST | - | 12.6 | 12.6 | 245 | 198 | - |
| PLANNED_ALL every constraint with a p… | riser | PENDULO | LZ_WEST | - | 12.6 | 12.6 | 246 | 199 | - |

Not rankable (no usable regression SF, so no V; the NWA table still sizes them with the assumed class SF):

- U02 VEALMOOR-KOCHTAP 138 kV: only the DC power-flow proxy exists (never rank on proxy magnitudes, phase 2 gate B); the element never bound in the real-time week
- U04 WESTEX interface: only an attenuated (+shrunk) day-ahead fit exists: sign-only, magnitude not used
- U07 LNGSW-CONSW 345 kV: only the DC power-flow proxy exists (never rank on proxy magnitudes, phase 2 gate B); the element never bound in the real-time week
- U08 BELCNTY-SALSW 138 kV: only the DC power-flow proxy exists (never rank on proxy magnitudes, phase 2 gate B); the element never bound in the real-time week
- U14 ODEHV-YARBR 138 kV: no shift factor of any kind for this element's constraints (never priced in the window, or not fitted)
- U15 KOCHTAP-BUZSW 138 kV: only the DC power-flow proxy exists (never rank on proxy magnitudes, phase 2 gate B); the element never bound in the real-time week
- U16 PASP_PATA interface: no shift factor of any kind for this element's constraints (never priced in the window, or not fitted)
- U17 LARDVNTH-LASCRUCE 138 kV: no shift factor of any kind for this element's constraints (never priced in the window, or not fitted)

Check on the largest numbers. Twin Buttes-Hargrove carries the largest values because $145M of 2025 rent sits on a 151 MW line: **734 $/kW-yr of shadow-price-hours per unit of SF** (the 2025 average shadow price while binding was about $1,000/MWh).
The Sep-Oct 2026 window shows far less: no real-time binding in the week, and day-ahead rent of $0.84M in 31 days, $9.8M annualised, 15 times below the 2025 figure. Scurry Chevron-Knapp goes the other way (window annualised 6.3 times the IMM figure, an outage week). Annual values from one week are uncertain by an order of
magnitude in both directions; treat the Twin Buttes pocket as an upper bound and rely on 3b's dispatch for the final numbers.

## 5. Interface for Phase 3b and the final report

    from gridsite.upgrades import apply_upgrade, planned_schedule, basis_half_life
    apply_upgrade(cid_list, residual=None, expand_element=True) -> {cid: mu_scale}      # 0.0 = fully relieved

- `cid_list` items may be a `cid` ("6437__F|DMTSCOS5"), an element (the `constraint_name`, "6437__F") or an upgrade id ("U01"). With `expand_element=True` every contingency constraint of the element is scaled, because the upgrade is physical: all
  12 contingencies of Scurry Chevron-Knapp, all 6 of Twin Buttes-Hargrove. With `False` only the cids given. Unknown items raise `ValueError`.
- `residual` overrides the default: `RESIDUAL_MU_SCALE_WIRES = 0.1` for a line or transformer (re-binding at the higher limit, other contingencies), `RESIDUAL_MU_SCALE_GTC = 0.5` for an interface (the arena halves the Permian premium after the 765 kV lines). Pass `0.0` for a full relief. Both are ASSUMPTIONS.
- `planned_schedule(min_conf=0.5)`: one row per constraint with a planned-project match: `cid, element_key, project_id, isd` (Timestamp, NaT if the project has no date), `status, conf, residual, source`. 335 rows; 48 are already in service
  (their effect is in the market data) and `basis_half_life` ignores them by default. A match is a name or bus overlap, not proof of relief (phase 2); 8 elements were reviewed by hand in `PLANNED_REVIEW` (each entry names the TPIT / RPG ids it rests on and what it is).
- **Basis half-life.** For site n with value V[n,k] on each constraint, and the schedule S of (k, ISD_u, residual s_u): `m_k(t) = product of s_u over upgrades u of k with ISD_u <= t` (an ISD before t0 counts as t0), `V_n(t) = sum_k V[n,k] m_k(t)`, and the half-life is the first
  t - t0 with `V_n(t) <= 0.5 V_n(t0)`, or infinity within the horizon (15 years). Example from the rerank: ARGYL (Highlands-Argyle, TPIT-110248 in Dec 2032) half-life 6.1 years;
  KNAPP and the Twin Buttes pocket have none because no project is dated. If the undated RTP project for Twin Buttes-Hargrove lands at the pipeline median lead time (`undated_lead_months=48`), the pocket's half-life is 4.0 years.
  This is the DERIVED quantity 3b is asked to compute with dispatch values; the helper is a reference implementation on first-order values.

## 6. Who can make it happen in ERCOT, with citations

Short names: NP = Nodal Protocols, PG = Planning Guide, NOG = Nodal Operating Guide (versions of 1 Aug 2026, in `raw/docs`); TAC = Texas Administrative Code; PURA = Public Utility Regulatory Act.

**Who decides and pays.**
- The TSP plans, builds, owns and operates the facilities "needed to relieve transmission constraints, as recommended by ERCOT and approved by the commission"; a CCN is subject to PUCT approval (16 TAC 25.195(d)). ERCOT designates the TSP that owns the end points (NP 3.11.4.8) and, with Board concurrence,
  can re-designate one that does not pursue an endorsed project. The TSP builds the transmission facilities needed for service unless ERCOT identifies a less costly, operationally sound and equally effective alternative (16 TAC 25.195(e)); I found no case where ERCOT identified storage as that alternative (not a proof that none exists).
- Review path (NP 3.11.4.3): Tier 1 is $200M or more (ERCOT Board endorsement, NP 3.11.4.7); Tier 2 is under $200M with a CCN (ERCOT review in 120 days or less, NP 3.11.4.6(1)(c)); Tier 3 is $50-200M without a CCN, or under $50M if it is a 345 kV reconductor of more than one mile,
  added 345/138 kV autotransformer capacity or a new 345 kV substation; everything else, including "equipment upgrades resulting in only ratings changes", is a neutral Tier 4 project that ERCOT does not review (NP 3.11.4.3(1)(f)(viii), 3.11.4.4). Facility ratings updates are not a project at all (NP 3.11.4.1(4)).
  The RPG endorsement supports the PUCT's finding of need for a CCN (PG 3.1.1.3; NP 3.11.1).
- Permits (16 TAC 25.101): a new line needs a CCN (decision within a year of a complete application; 80 days if uncontested; 180 days if designated critical to reliability); a new substation or switching station does not (c)(2); rebuilding along an existing route, reconductoring or bundling needs none if no additional right of way is required (c)(5)(B).
  An economic line must pass a congestion-cost-savings test (levelized ERCOT-wide annual savings at least the average of the first three years' annual revenue requirement) or a production-cost test (b)(3)(A)(i); reliability and resiliency lines have their own tests (A)(ii)-(iii).
- Cost recovery: a TSP's transmission rate is its approved transmission cost of service divided by the average June-September 4CP demand, excluding wholesale storage load; the tariff does not apply to wholesale storage (16 TAC 25.192(a), (b)(1), (c)). So the upgrade is paid by load, not by the battery that benefits.

**What a battery owner can do.**
- Propose: any stakeholder may submit a project to the RPG in any tier (NP 3.11.4.1(1)-(2)); additional upgrades that might be cost-effective as a result of new or modified generation (storage is a generation resource in the protocols) may be initiated by any stakeholder (NP 3.11.6(3)). A submission must list the feasible alternatives and the analysis of rejected ones (PG 3.1.2.1(1)(a), (i));
  "both transmission and distribution solutions to performance deficiencies may be considered" (PG 3.1.2.1(2)); the lowest-life-cost alternative is generally recommended (PG 3.1.3(1)).
- Pay for its own connection: under an SGIA executed after 31 Dec 2025 the generator pays interconnection facilities above an allowance of $14M (138 kV or less) or $20M (above), 2024 dollars indexed to CPI from 2026, and a storage resource gets only that allowance; the TSP pays "any transmission system upgrades deemed necessary"
  and keeps ownership and control of what the generator pays for (16 TAC 25.195(f)(3)(A)-(D)). So under connect-and-manage the owner's risk is curtailment, not an upgrade bill.
- Fund a network upgrade only if the TSP agrees: "if an affected TSP elects to pursue a Customer or Resource Entity funded transmission project", it gets a reliability impact assessment and RPG review by tier, ERCOT does not endorse a Tier 1 or 2 funded project, and it ends as a Tier 4 neutral project (NP 3.11.4.11). The owner cannot compel it, own it or schedule it.
  (Phase 1b marked third-party contributions as unverified; this section is the rule text.)

**Is storage a transmission alternative in ERCOT today? No.**
- ERCOT's planning treats storage as a resource to be dispatched, not as a transmission alternative: a project is "reliability-driven" only if no dispatch of Generation Resources and ESRs can meet the criteria (PG 3.1.3.1(2)); storage above a duration threshold must be deliverable like thermal units (minimum deliverability, PG 4.1.1.7);
  a RAS or constraint management plan cannot be planned as a fix unless the need ends within five years (PG 4.1.1.2(2)(a)), though ESRs can be the objects of a RAS (NOG 11.2(g)).
- PUCT: in 2018 the commission rejected AEP Texas's request to own two batteries as an alternative to wires upgrades and opened Project 48023; generators argued PURA bars utility-owned storage, Oncor and Tesla argued it is allowed for the utility's own reliability needs and ERCOT took no position (Utility Dive, 6 Nov 2018).
  The legislature answered in 2021 with **SB 415 (PURA 35.153, effective 1 Sep 2021)**: a transmission and distribution utility may, with prior commission approval, contract with a power generation company for storage "to ensure reliable service to distribution customers", after a competitive process and only if storage is more cost-effective than building
  or modifying distribution facilities; the utility may not own the storage; total reserved capacity is capped at 100 MW. It is **distribution-only**. The implementing rule, 16 TAC 25.58 (Project 59523), was proposed on 17 Apr 2026 (comments to 18 May); adoption is unverified, and the proposal bars allocating the costs to transmission customers.
  A 2010 precedent (AEP's two sodium-sulfur batteries at Presidio, treated as a transmission asset by the PUCT but not as precedent) is known only from a 2009 ERCOT presentation summary and was not re-read.
- FERC's storage-as-transmission programmes do not apply inside ERCOT (`docs/research/ercot.md` section 5).

**Per upgrade type**

| type | who decides | review and permit | who pays | what a battery owner can do |
|---|---|---|---|---|
| ratings change (DLR / AAR, terminal equipment) | TSP | none (not a project); neutral Tier 4 if it is equipment | load, through TCOS | ask the TSP; nothing else |
| reconductor, rebuild on existing right of way | TSP | Tier 4, or Tier 3 for 345 kV over a mile; no CCN | load, through TCOS | propose to the RPG; offer to fund if the TSP elects |
| new parallel line | TSP | RPG Tier 1 or 2; ERCOT Board endorsement if Tier 1; PUCT CCN | load, through TCOS | propose; comment in the RPG and CCN dockets |
| 345/138 kV autotransformer | TSP | Tier 3 (under $50M); no CCN; transformer lead time governs | load, through TCOS | propose; connect where a unit is planned |
| 765 kV plan / interface (WESTEX) | ERCOT plan, PUCT approval (Project 55718), TSPs build under CCNs | RTP / STEP, Board, CCNs | load, through TCOS | grid-forming capability can raise stability limits through the FIS and the quarterly stability assessment (PG 5.3.2.4.3, 5.3.5); nothing contractual |

## 7. Named constants

Every unverified value is a single named constant at the top of `gridsite/upgrades.py` with its label. Values below are generated from the module.

| name | value | label | meaning and source |
|---|---|---|---|
| `AS_OF` | `Timestamp('2026-10-10 00:00:00')` | REAL | date of this build (system date); planned dates before it are "imminent" |
| `USD_YEAR` | `2024` | REAL | price year of the MISO MTEP24 rows (unit_costs.csv) and of the NREL 2025 battery update |
| `ESCALATION_PER_YEAR` | `0.04` | ASSUMPTION | MISO escalates ~4%/yr (unit_costs.labels.csv); NOT applied, all dollars stay 2024$ |
| `KM_PER_MILE` | `1.609344` | REAL | unit conversion |
| `EARTH_RADIUS_KM` | `6371.0088` | REAL | mean Earth radius |
| `ROUTE_FACTOR` | `1.25` | ASSUMPTION | routed length / straight-line length (same value as gridsite.costs.ROUTE_FACTOR) |
| `SCED_DT_MIN` | `5.0` | ASSUMPTION | one SCED run covers 5 minutes (same convention as phase 1a / phase 2) |
| `DAM_DT_MIN` | `60.0` | REAL | DAM prices are hourly |
| `UPGRADE_CONF_MIN` | `0.5` | ASSUMPTION | a planned-upgrade match at or above this counts as "has a planned upgrade" (phase 2) |
| `PLANNED_YES_CONF` | `0.8` | ASSUMPTION | match confidence at or above this is called "yes" (pair_name 0.9, pair_title 0.8) |
| `PLANNED_HINT_CONF` | `0.3` | ASSUMPTION | a match at or above this (phase 2 one_end, 0.30) is a "hint": a nearby project, not the element |
| `ANCHOR_CONF` | `0.5` | ASSUMPTION | substation placement confidence needed for a station-distance length (phase 2 / 1c) |
| `TOP_PRIORITY_ELEMENTS` | `15` | ASSUMPTION | brief says "top ~15 binding elements by constraints.csv priority" |
| `WINDOW_TOP_ELEMENTS` | `6` | ASSUMPTION | extra local constraints, by the window part of the priority score, not yet chosen |
| `LENGTH_FALLBACK_QUANTILE` | `0.5` | ASSUMPTION | class median line length (lines.csv, OSM, both ends ERCOT substations) when nothing better |
| `LIMIT_FALLBACK_QUANTILE` | `0.5` | ASSUMPTION | class median of median_limit_mw over window-binding elements of the same type and kV class |
| `WINDOW_PERSISTENCE` | `0.15` | ASSUMPTION | share of the window's binding that recurs over a year (outage-driven shoulder week); Scurry Chevron-Knapp, the one element with both, measures 0.17 (`upgrades check`) |
| `IMM_FLOOR_RANK_RENT_MUSD` | `50.83` | REAL | 10th-largest 2025 IMM RT rent (Argyle-Lewisville, constraints_annual.csv); an element outside the IMM top 10 has less annual rent than this, so it caps window-annualised rents |
| `EPISODE_MIN_SHADOW` | `1.0` | ASSUMPTION | $/MWh; a shadow price below this is not material (<0.5% of rent in the window; 33% of DAM rows) |
| `EPISODE_GAP_MIN` | `60.0` | ASSUMPTION | binding periods separated by at most this many minutes belong to one episode (no time to recharge) |
| `EPISODE_TOL_MIN` | `1.5` | ASSUMPTION | SCED runs are 295-305 s apart; this much slack when testing run adjacency |
| `MIN_BASIS_EPISODES` | `6` | ASSUMPTION | an element needs this many window episodes to use its own episode lengths |
| `MIN_POOL_EPISODES` | `20` | ASSUMPTION | a pooled reference class needs this many episodes, else all lines are pooled |
| `COVERAGE_TARGETS` | `(0.5, 0.8, 0.95)` | REAL | from the brief (50 / 80 / 95 % of binding hours) |
| `RELIEF_TARGETS` | `(0.05, 0.1, 0.2)` | ASSUMPTION | from the brief (named scenarios: 5 / 10 / 20 % of the median limit) |
| `CENTRAL_RELIEF` | `0.1` | ASSUMPTION | the central NWA scenario quoted in upgrades.csv |
| `CENTRAL_COVERAGE` | `0.8` | ASSUMPTION | the central NWA scenario quoted in upgrades.csv |
| `SF_NWA_CAP` | `1.0` | ASSUMPTION | /SF/ above 1 is not credited (a battery cannot relieve more than its own MW) |
| `SF_SITE_MIN` | `0.05` | ASSUMPTION | sites with a smaller /SF/ are not used to relieve (MW would explode) |
| `SHRINK_MIN` | `0.8` | REAL | phase 2 SHRINK_MIN; DAM SFs below this are attenuated ("+shrunk") |
| `UNSHRINK_FLOOR` | `0.3` | ASSUMPTION | an attenuated DAM SF is divided by its shrink factor if shrink >= this, else sign-only |
| `SITE_MAX_MW` | `{69: 50.0, 138: 300.0, 345: 300.0}` | ASSUMPTION | MW cap per battery site by voltage class (brief: 10-300 MW at 69-345 kV; 69 kV lines are rated ~40 MW at the median in the window, so 50 MW there) |
| `BAD_SF_QUALITY` | `('weak', 'implausible', 'low')` | ASSUMPTION | a site SF with any of these quality tags is not used to size relief |
| `FALLBACK_SF_QUANTILE` | `0.5` | ASSUMPTION | no regression SF -> use this quantile of the best-site /SF/ of window-fitted RT lines of the same kV class |
| `BATT_ENERGY_USD_KWH` | `241.0` | REAL | NREL/TP-6A40-93281 Fig. 2 (fit slope 240.8), 2024$ |
| `BATT_POWER_USD_KW` | `372.0` | REAL | NREL/TP-6A40-93281 Fig. 2 (fit intercept 379.2 less the 7 $/kW adjustment to hit $334/kWh at 4 h), 2024$ |
| `BATT_COST_MULT` | `{'low': 0.77, 'mid': 0.92, 'high': 1.1}` | REAL | NREL/TP-6A40-93281 Table 2, normalised 2026 column (low / mid / high), applied to both lines |
| `BATT_FOM_FRAC` | `0.04` | REAL | NREL/TP-6A40-93281 sec. 3: fixed O&M = 4 %/yr of capital cost (includes augmentation) |
| `BATT_LIFE_YR` | `15` | REAL | NREL/TP-6A40-93281 sec. 3 (15 years) |
| `BATT_RTE` | `0.85` | REAL | NREL/TP-6A40-93281 sec. 3 (round trip 85 %); informational, usable MWh is quoted |
| `BATT_AVAILABILITY` | `0.95` | ASSUMPTION | installed MW = required MW / availability (forced outages; relief is only worth what is available) |
| `BATT_MIN_HOURS` | `1.0` | ASSUMPTION | floor on duration (NREL cautions against < 1 h; ERCOT ECRS needs 1 h) |
| `BATT_MAX_HOURS` | `10.0` | REAL | the NREL energy / power fit and the ATB cover 1-10 h; beyond it the table caps the duration and reports the coverage reached |
| `DISCOUNT_RATE` | `0.08` | ASSUMPTION | 8 %, the rate the arena uses for break-even installed cost (arena README) |
| `WIRES_FIXED_CHARGE_RATE` | `0.115` | ASSUMPTION | levelised annual revenue requirement / installed cost (return, depreciation, taxes, O&M) |
| `REF_ZONE_ENERGY_USD_KW_YR` | `44.5` | REAL | arena README, ERCOT 5-year (2021-25) Adaptive learner, grid-scale 2 h battery; reference only, never netted |
| `ARENA_LEARNER_SHARE` | `0.41` | REAL | arena README, ERCOT learner = 41 % of the perfect-foresight ceiling (5 y; 37 % in 2025, 51 % over 3 y) |
| `TERMINAL_BAY_FRACTION` | `0.3` | ASSUMPTION | terminal equipment (breakers, CTs, switches) per line end = this x the cost of one new bay |
| `NEW_LINE_BAYS` | `2` | ASSUMPTION | a new line needs one new bay at each end |
| `REBUILD_345_FROM_NEW` | `0.782608695652174` | DERIVED | no 345 kV rebuild row; 138 kV rebuild / 138 kV new single-circuit ratio (1.8 / 2.3 $M/mi) applied to 345 kV new |
| `ESTIMATE_BAND_LOW` | `0.2` | ASSUMPTION | screening-level estimate: low >= mid x (1 - 0.20) ... |
| `ESTIMATE_BAND_HIGH` | `0.5` | ASSUMPTION | ... and high >= mid x (1 + 0.50), widened only where the source cells are narrower |
| `XFMR_STEPS_MVA` | `(150, 300, 450, 600, 750, 900, 1200)` | ASSUMPTION | standard autotransformer sizes; a parallel unit is the smallest size >= the existing rating |
| `XFMR_138_69_USD_PER_MVA` | `5050.0` | REAL (UNVERIFIED layout) | MISO MTEP24 Table 2.3-7, 138 kV row of the 69 kV column; 345/138 kV is $6,880/MVA in unit_costs.csv |
| `DLR_MIN_COST_MUSD` | `0.25` | ASSUMPTION | floor on a DLR / AAR installation (weather stations, integration), whatever the line length |
| `SUBSTATION_REBUILD_KV_ROW` | `{69: 138, 138: 138, 345: 345}` | ASSUMPTION | which unit-cost row prices a substation rebuild at each voltage class |
| `UPLIFT` | `{'dlr_aar_static': 0.15, 'dlr_aar_after_aar': 0.1, 'reconductor': 0...` | ASSUMPTION | typical rating uplift of each option; an option can serve a relief target only if uplift >= target |
| `AMBIENT_SWING_MIN` | `0.05` | ASSUMPTION | if the SCED limit of an element swings >= 5 % (q95-q05)/median it is already ambient-adjusted |
| `SWING_QUANTILES` | `(0.05, 0.95)` | ASSUMPTION | quantiles of the SCED limit that define the swing |
| `SWING_MIN_ROWS` | `20` | ASSUMPTION | with fewer listed SCED rows than this the swing is not computed |
| `LEAD_MIN_N` | `10` | ASSUMPTION | a TPIT class needs this many rows for its own quartiles, else pooled over kV |
| `DLR_LEAD_MONTHS` | `(6.0, 12.0, 18.0)` | ASSUMPTION | sensors and software, no CCN, a ratings change is not an RPG project (NP 3.11.4.1(4)) |
| `LPT_LEAD_MONTHS` | `(30.0, 36.0, 60.0)` | REAL | unit_costs.csv: NLR 2026 average 30 months (H1 2025), DOE 2024 commonly quoted 36, up to 60 |
| `XFMR_INSTALL_MONTHS` | `6.0` | ASSUMPTION | set, test and energise after delivery (same as gridsite.costs.XFMR_INSTALL_MONTHS) |
| `BATT_FIS_MONTHS` | `(16.0, 20.5, 28.0)` | DERIVED | quartiles / median of FIS request -> IA signed for storage (notes/rules.md (d), GIS Sep 2026) |
| `BATT_BUILD_MONTHS` | `(12.0, 15.0, 24.0)` | ASSUMPTION | engineering, procurement and construction after the interconnection agreement |
| `BATT_LPT_MIN_MVA` | `100.0` | ASSUMPTION | a step-up of at least this size at 138 kV or more is a large power transformer (same as gridsite.costs.LPT_MIN_MVA) |
| `BATT_GSU_MVA_PER_MW` | `1.1` | ASSUMPTION | step-up rating = 110 % of the battery MW (same as gridsite.costs.GSU_MVA_PER_MW) |
| `PLAN_765_ISD` | `('2030-12-31', '2031-06-30', '2032-12-31')` | REAL | PBRP 765-kV import paths "2031" (C&N Dec 2025 p.8; data/planned_projects.csv); early / mid / late |
| `DAM_WINDOW_DAYS` | `31.0` | REAL | DAM shadow prices 10 Sep - 10 Oct 2026 |
| `MIN_RT_RUNS_FOR_SPH` | `10` | REAL | phase 2 MIN_BINDING_RUNS; below this the RT window is too thin, use DAM |
| `COVER_MIN_SPH` | `200.0` | ASSUMPTION | constraints with less shadow-price-hours than this ($/MW over the window) are left out of the cover check |
| `COVER_2H` | `0.5` | DERIVED/ASSUMPTION | share of a constraint's shadow-price-hours a 2 h battery that cycles once a day can reach with perfect foresight (best 2 h of each day). Window values: RT 0.55, DAM 0.45 (value-weighted), `upgrades check` |
| `COVER_4H` | `0.76` | DERIVED/ASSUMPTION | same for 4 h (RT 0.81, DAM 0.71) |
| `CAPTURE_FACTOR` | `{'2h': 0.205, '4h': 0.3116}` | ASSUMPTION | cover x learner share of the oracle (0.205 / 0.312) |
| `V_MIN_USD_KW_YR` | `0.5` | ASSUMPTION | sites with less than this total value are not ranked (rank among noise is meaningless) |
| `N_LOSERS` | `10` | ASSUMPTION | rows kept per upgrade and spec: biggest losers |
| `N_RISERS` | `10` | ASSUMPTION | ... and the biggest rank gainers inside the post-upgrade top set |
| `TOP_K` | `50` | ASSUMPTION | size of the "top set" whose membership is tracked (a site that enters or leaves the best 50 is a mover) |
| `RISER_UNIVERSE` | `200` | ASSUMPTION | a "riser" is a site inside the post-upgrade best 200 whose rank improves; deeper ranks sit in a flat tail where a few dollars move a site hundreds of places |
| `SATURATION_RELIEF` | `0.2` | ASSUMPTION | the per-kW values hold for the first MW only; sat_mw is the battery MW that already relieves 20 % of the limit |
| `RESIDUAL_MU_SCALE_WIRES` | `0.1` | ASSUMPTION | share of shadow-price-hours left after a line / transformer upgrade (re-binding at the higher limit, other contingencies); 0 = fully relieved |
| `RESIDUAL_MU_SCALE_GTC` | `0.5` | ASSUMPTION | an interface keeps half its congestion after its planned relief (the arena halves the Permian premium after the 765 kV lines, arena/forward.py) |

## 8. Caveats

- **One week of real-time data.** The RT window is an autumn shoulder week (5-10 Oct plus 25 Sep) full of local outage constraints; the structural elements barely appear in it, and 13 of 25 elements never bound in real time in it. Their episodes, limits and lengths come from classes (pooled episodes,
  class median limit and length), marked in the `*_basis` columns. The annual shadow-price-hours mix a 2025 rent with one week; section 4 shows the two disagree by 6 to 15 times for the two elements that have both.
- **Shift factors.** 8 of 25 upgrades have no usable regression SF (section 4); the NWA table sizes 6 of them with the class median and 1 with an attenuated day-ahead SF; one interface is not sized. DAM SFs predict a new day with R2 0.76 (phase 2). SFs above 1 (Twin Buttes MIDW -1.19, WLY-WAP BRAZ_WND -1.24) are capped at 1.
- **Relief and coverage are scenarios.** The relief a constraint needs is unobservable; 5-20% of the limit is the same size as the daily ambient swing of a rating. A battery relieves in preventive mode only (SCED re-dispatches before the contingency); the table says nothing about a battery armed for a post-contingency response.
- **Costs.** Screening level: MISO's single Texas row per item (some cells UNVERIFIED), 2024 dollars, no escalation (MISO uses about 4% a year), no land or right-of-way beyond the unit rows, power-transformer prices have risen about 77% since 2019 (Wood Mackenzie, secondary) and may sit above MISO's 2024 table. The battery cost is for a 60 MW system at Q1 2024 market prices and
  the 2026 projection; it excludes the connection facilities above the allowance (Phase 3a's `connection_costs.csv` has them), and the 2025-26 tariff changes are not in NREL's January-February 2025 work.
- **What the comparison leaves out.** The wires also serve load growth, outages and voltage; the battery also earns market revenue and can serve several constraints; neither is credited. The reference revenue is shown, not netted.
- **Planned-upgrade matches** are name and bus overlaps. The 8 hand reviews are listed in `PLANNED_REVIEW`. TPIT carries no costs; the RPG cost list is only the 2026 decks.
- **Placement.** 6 elements have a class-median length because an end has no placement; HIGH36-BRENNO and BRAND_C-COLLEGE had their ends rejected in phase 2 and their automatic project matches are ignored.
- **Additivity.** The rerank sums V over constraints; constraints that bind at the same hours compete for the same battery hours, so a site with many constraints is overstated. 3b's dispatch settles it.

## 9. Hand-offs

- **Phase 3b:** `apply_upgrade`, `planned_schedule`, `basis_half_life` (section 5); `data/upgrade_rerank_first_order.csv` for a first guess; `value_table()` (site x constraint $/kW-yr and SF, with the zone mean) if you want to reuse the first-order values as a prior.
- **Final report:** the table above, findings 1-8, section 6.
- **Agent 1c:** the elements whose ends have no placement (ENCRT, CONSW, JUPITER, LASCRUCE, LWSVH, SGVSW, YARBR) drive the class-median lengths; placing them would sharpen those lengths and costs.
- **Open item for the lead:** the only download was NREL/TP-6A40-93281 (0.7 MB, in the manifest). The ATB web page prints no base-year $/kW (its figures are images), so the report's own fit is the source; the ATB workbook download is large and was not fetched.
