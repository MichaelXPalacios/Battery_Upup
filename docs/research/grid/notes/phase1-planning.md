# Phase 1b: planning, queue, constraints, unit costs and rules

Agent 1b, 10 Oct 2026. Everything here comes from public documents downloaded without a login or key. Files are in
`research-data/grid/raw/docs/` (166 MB, 180 manifest rows: 177 `web`, 3 `mis`). Rebuild all tables with
`python run_grid.py parse-docs`; refetch with `python run_grid.py fetch-docs` (idempotent: a file in the manifest and on disk is not fetched again).
Run both from `docs/research/grid/` with the research venv.

No packages were installed. The PDF text comes from `pdftotext` (Xpdf 4.00, `/mingw64/bin` on this machine), which `docs_parse.py`
calls only for the RPG decks; if it is missing, those rows are skipped with a warning.

Label legend for the sidecars: REAL = copied from a named source, DERIVED = computed here from REAL values, ASSUMPTION = my
judgement, SIM = model output (none here).

## Tables

| File | Rows | What it holds | Key columns |
|---|---:|---|---|
| `data/planned_projects.csv` | 2,259 | TPIT (2,044) + 2025 RTP Appendix E projects not in TPIT (179) + 2026 RPG decks with a price (20) + transcribed PBRP/765-kV/C&N rows (16) | `project_id, name, tsp, kv, project_type, from_sub_name, to_sub_name, counties, in_service_date, status, cost_musd, source`, plus mileage, MVA, RPG/RTP numbers, SSWG bus numbers |
| `data/queue_storage.csv` | 1,789 | Every project in the Sep 2026 GIS report (all fuels): 869 storage, 610 solar, 159 wind, 139 gas, 12 other | `inr, project_name, tech, mw, county, poi_text, zone, status, projected_cod`, plus POI kV and PSS/E bus parsed from the text, co-location class, IA and energisation dates |
| `data/large_loads.csv` | 47 | Large-load totals by status, by weather-zone region, top counties, TSP and RPG study area | `area, area_type, mw, status, date, source` |
| `data/constraints_annual.csv` | 134 | IMM top-10 constraints by rent and by frequency (RT and DA, 2024 and 2025), ERCOT C&N top-10 (two 12-month windows), ERCOT economic-study projected top-10 (2026-2030) | `year, constraint_name, from_station, to_station, kv, element_type, rent_musd, binding_hours, area, source`, plus `market`, `period`, ranks, `element_id` |
| `data/gtc.csv` | 14 | Generic Transmission Constraints named in public ERCOT/IMM documents | `name, region, description, interface_elements, status, source`, plus code and planning limits |
| `data/unit_costs.csv` | 36 | MISO MTEP24 guide tables for Texas, ERCOT PBRP/RTP generic costs, DOE reconductoring and DLR figures, transformer lead times | `item, kv, unit, low_usd, mid_usd, high_usd, year_usd, source` |
| `data/transcribed_*.csv` (5 files) | 14 to 134 | The hand-transcribed inputs behind the last four tables and the transcribed planned rows, each row with `doc` and `page` | see sidecars |

Every CSV has a `<name>.labels.csv` sidecar (`column,label,source,note`). `notes/rules.md` holds the market-rule write-up.

Extra columns sit after the contract columns, so the contract layout holds.

## What to know before using each table

**planned_projects.** Use `record_type` and `status`.
- `tpit` rows are the July 2026 TPIT base (sheets Future, Planned, Completed; 78 cancelled rows skipped). The public file is the "No Cost" version, so TPIT rows have **no cost**. Project-level costs exist only on `rpg` and `transcribed` rows. Never add the two kinds.
- ERCOT's summary sheet in the same file gives cost totals by year and kV for all projects: **$59.3 B for 2025-2033** (765 kV $17.8 B; 345 kV $22.2 B; 138 kV $16.8 B; 69 kV $2.5 B), $49.0 B for 2027-2033, $6.5 B planned for 2026 (`ImprovementCostSummary07172026`).
- Rows with an in-service date in 2026-2031 that are not yet in service: **1,643 TPIT rows** (planned, under construction, conceptual or unspecified), of which **1,347 are planned or under construction**; plus 14 RPG decks with an ISD in that window. TPIT year counts for open rows: 2025: 44 (already late), 2026: 293, 2027: 454, 2028: 402, 2029: 249, 2030: 149, 2031: 96, 2032: 42, 2033: 13. By voltage (2026-2031): 138 kV 974, 345 kV 495, 69 kV 136, 765 kV 35.
- `project_type` is a rule-based hint (see `classify_project`). An eyeball check of 40 random TPIT rows found about 85% plausible; treat it as a filter, not a fact.
- Substation names are as TPIT wrote them (many are codes or abbreviations). `sswg_bus_numbers` (1,684 TPIT rows) carries the PSS/E bus numbers of the project, a better join key for Phase 2 than the names. `from_sub_name`/`to_sub_name` come from TPIT terminals (1,764 rows), a title parse (59) or are blank (221).
- RPG decks: 20 projects, **$24.2 B** in total; the three largest are the AEP Panhandle 765-kV project ($11.5 B, 2031, 26RPG008), the AEP Rio Grande Valley system improvements ($4.9 B, 26RPG009) and Oncor's Set 1 North and Central Texas projects ($1.8 B). Only 2026 meetings were scraped.
- The PBRP rows reproduce the three 765-kV import paths ($3,221.3 M, $3,157.4 M, $2,560.4 M; $9.06 B total, about 1,255 miles), approved by the PUCT in Project 55718 (second order; date 24 Apr 2025 per C&N). The STEP aggregate ($32.99 B) is flagged as an aggregate.
- Leads for Phase 2/4 (name matches only, not verified as relief): Expanse-Vealmoor 138 kV rebuild (TPIT-81305A/B, Sep 2026) and the Longshore-Consavvy 345 kV double-circuit rebuild (TPIT-81268, May 2028); Salado-Bell County 138 kV rebuild (TPIT-87673) and Salado 345/138 kV autotransformers #1 and #2 (TPIT-87707), both May 2027; Reiter-Yarbrough and Yarbrough-Pecos Trail 138 kV rebuilds near Odessa EHV-Yarbrough (TPIT-81418, TPIT-81440); Argyle-area 138 kV reconductors (2031-2032); nothing found for Knapp-Scurry Chevron.

**queue_storage.** One row per INR. Large-generator rows list only projects with a **full interconnection study requested** (ERCOT withholds screening-only projects), small-generator rows list those with a model-ready date.
- **Storage: 869 projects, 157,258 MW** (846 large at 157,044 MW, 23 small at 214 MW). The GIS Summary tab totals 162,580 MW of battery under study, including projects that have only a screening study; the Senate deck of 29 Jul 2026 gave 170,945 MW at 30 Jun 2026. 64 storage rows are self-limiting facilities with 0 MW because the battery sits inside a solar or wind nameplate; their real size is not in the report.
- By co-location: 579 stand-alone, 271 with solar, 10 with wind, 2 with thermal, 7 small units not classified. By status: 551 "SS completed, FIS started, no IA", 115 "FIS completed, IA signed", 102 "FIS started, IA signed", 69 "FIS completed, no IA", 23 small, 9 other. 218 have an IA (38.1 GW). 30 have approval to energise (2.8 GW).
- Projected COD of storage MW: 2025: 0.3 GW, 2026: 7.2, 2027: 31.5, 2028: 61.2, 2029: 46.6, 2030: 9.5 (dates are developers' and slip).
- By CDR zone: North 45.5 GW, South 41.5, West 39.0, Coastal 15.1, Houston 11.1, Panhandle 5.1.
- **Top 10 counties by queued storage (MW):** Harris 6,243; Brazoria 5,747; Bexar 4,983; Wise 3,826; Mitchell 3,824; Ellis 3,278; Wharton 3,099; Cameron 2,942; Milam 2,757; Jack 2,638.
- `poi_text` is verbatim. `poi_bus_no` holds the PSS/E bus number(s) found in the text (96% of rows; a tap lists both ends separated by `;`; about 7 rows pick up a voltage or node id by mistake, so check against `poi_text`). `poi_kv` is filled for 94% of rows. `poi_station_code` is rare.
- Operating batteries are not in this file (they leave the GIS report at commercial operation). The companion report lists co-located operating units without POIs. Agent 1c/EIA-860M cover operating storage.
- Typical timeline from this report is in `rules.md` (d): median 20.5 months from FIS request to IA for storage with an IA.

**large_loads.** ERCOT publishes no project- or county-level large-load file. The finest public cuts are:
- Batch Zero conditional classification of 3 Sep 2026 by **weather-zone region** (base and studied load for 2032): North 14.2 + 24.3 GW; North Central 14.6 + 25.8; East 1.1 + 6.6; Far West 8.5 + 10.5; West 6.0 + 16.4; South Central 6.7 + 14.8; Coast 11.0 + 6.5; South 4.3 + 20.4 GW. Totals 66.4 GW base (204 projects), 125.4 GW studied (127.9 GW in the headline), 302.2 GW excluded.
- Top-5 counties: base load Dallas 5,713 MW, Harris 5,672, Haskell 2,689, Brazoria 2,357, Ellis 2,330; base plus studied Cameron 12,235, Dallas 7,637, Jones 7,000, Harris 6,899, Childress 6,750.
- RPG study areas: Panhandle (over 78 GW provided by TSPs, not validated), Lower Rio Grande Valley (6.9 GW with letters of agreement, over 12 GW provided), Bexar and Medina counties (about 2 GW by 2035), Cameron County (250 MW confirmed).
- Statewide totals over time: 63 GW (Dec 2024), 226 GW (Nov 2025), 238.6 GW (Dec 2025), about 410 GW (Mar 2026), about 474 GW (Jun 2026). Oncor 259 GW and AEP 46 GW (Mar 2026) are **UNVERIFIED** (chart-only in the source; copied from `docs/research/ercot.md`).
- Requests are not load: ERCOT scales officer-letter loads to 55.4%, and data-centre letters a further 49.8% (C&N Dec 2025 p.12).

**constraints_annual.** Do not sum across `market` values: RT and DA describe the same physical elements.
- **Top 10 by 2025 real-time congestion rent** (IMM Dec 2025 monthly report; total RT rent $2,499.8 M; `market=RT`):
  1. Vealmoor-Koch Tap 138 kV, $211.4 M (1,578 binding hours)
  2. Twin Buttes-Hargrove 138 kV, $145.0 M (945 h)
  3. Longshore-Consavvy 345 kV, $94.6 M (803 h)
  4. Bell County-Salado 138 kV, $94.2 M
  5. West Texas Export GTC (WESTEX), $89.1 M
  6. Odessa EHV-Yarbrough 138 kV, $70.2 M
  7. Scurry Chevron-Knapp 138 kV, $68.1 M (1,099 h)
  8. Laredo VFT North-Las Cruces 138 kV, $60.7 M (1,053 h)
  9. STP-W.A. Parish, $51.7 M
  10. Argyle-Lewisville, $50.8 M
  Binding hours = SCED 5-minute intervals divided by 12, available only for elements that were also in the IMM's top-10 by frequency. The most frequently binding RT element was the **Hamilton GTC** (20,326 intervals, about 1,694 h), then Vealmoor-Koch Tap, Nelson Sharpe-Rio Hondo GTC and Haine-La Palma.
- ERCOT's own top 10 for Oct 2024 to Sep 2025 gives different rents (Hargrove-Twin Buttes $176 M; Odessa EHV-Yarbrough $151 M; WESTEX $127 M) because of the window and method; both sets are kept. 2024 data (IMM, Dec 2024): WESTEX $130.5 M, MGSES-Navigation $110.2 M, Tonkawa-Morgan Creek $102.1 M (total $1,970.3 M).
- Projected congestion for 2027 and 2030 (2025 RTP, base and sensitivity cases) puts the West Texas Export interface first ($130 M to $245 M a year), then Houdini-W.A. Parish, Bell County East-Voss Lake, the Panhandle and North-to-Houston interfaces. The 2025 RTP retired the Rio Grande Valley and South Texas interfaces from its planning model because of the LRGV and San Antonio South projects.
- The 2025 annual IMM report (`Potomac_2025_State_of_the_Market_Report_ERCOT.pdf`) has no constraint table; the figures come from the December monthly reports, which have the Top-10 tables.
- `area` is my grouping and is labelled ASSUMPTION; 1c supplies coordinates. `kv` is blank for Potomac-only names (Phase 2 fills it from NP6-86-CD).
- **Not available publicly:** contingency names. The IMM tables key on the monitored element only (for example `15060__B`), so the monitored-element-and-contingency pair that your `cid` needs must come from NP6-86-CD / NP4-191-CD (agent 1a).

**gtc.** 14 GTCs: WESTEX, PNHNDL, HMLTN, MCCAMY, N_TO_H, WHARTN, NELRIO, NE_LOB, VALEXP, VALIMP, PASP_PATA (South Texas Export), ZAPSTR, I_FW_S, I_FW_N (the last two new on 15 Sep 2025). Element lists are ECEII (EMIL NP3-770-M) and **not public**, so `interface_elements` says so. Planning limits (MW) are from the 29 Jul 2025 RTP stability deck: WESTEX 12,240 (2027) / 16,200 (2030), the final RTP report says 12,700 for 2027; Panhandle 4,440; Hamilton 70; Wharton 1,285; North-to-Houston 5,595 base; McCamey and the Valley interfaces 9,999 (no limit needed). Operational limits change by market notice (Valley: 3 Jul 2024, 2 Jul 2025, 16 Sep 2026) and are not public.

**unit_costs.** Sources: MISO Transmission Cost Estimation Guide for MTEP24 (Texas row; 2024 USD; includes 30% contingency and 7.5% AFUDC), ERCOT PBRP Table 2.7 and 2024 RTP STEP comparison, DOE 2019 DLR report, DOE 2024 LPT report, NLR 2026 LPT report.
- The **MTEP25 and MTEP26 guides were not usable**: MTEP25's final PDF returned 403, and the unit-cost tables of both sit in a companion workbook I did not find. I used the MTEP24 guide, which prints the tables, and note that MISO escalates about 4% a year (MTEP26 slides p.4). Apply that yourself, and record it as an ASSUMPTION.
- Headline Texas numbers (2024 USD, MISO): 345 kV new single circuit $4.0 M/mile (ERCOT's generic $4.2 M); 138 kV $2.3 M/mile; 138 kV reconductor $0.39 M/mile per circuit and 345 kV $0.62 M/mile; add one 345 kV bay $3.4 M (ring) to $4.9 M (breaker-and-a-half); 345/138 kV transformer $6,880/MVA; 765 kV single circuit $6.10 M/mile, substation $97.3 M and transformer $27.2 M (ERCOT).
- Dynamic line rating: $23k to $60k per mile (a DOE simulation, ACORE/EPRI via a utility filing); the "per line" row assumes 20 miles.
- Large power transformer lead time: DOE (Jul 2024) says 36 months is commonly quoted and up to 60 months; NLR (2026) gives 30 months on average in H1 2025 and bushings up to 130 weeks.
- Rows tagged UNVERIFIED in `note` rest on text-layout inference (MISO's tables lose their column alignment in the PDF text) or on secondary summaries.

## Gaps and checks that failed

- **TPIT costs:** not public. Project costs exist for RPG/RTP projects only.
- **RPG project list:** I found no single "RPG project list" file on ercot.com. I rebuilt it from the 101 PDFs on the 17 RPG meeting pages of 2026 (Jan to Oct). 2025 and earlier projects appear only through TPIT. The RPG decks label some $116 M to $200 M projects "Tier 1", while the 1 Aug 2026 Protocols set Tier 1 at $200 M or more (**UNVERIFIED** which threshold applied).
- **PUCT dockets:** I downloaded the Project 55718 second order (765-kV import paths; its signature date is illegible). LCRA lists CCN Docket 59475 for Bell County East to Big Hill 765 kV, energisation by end of 2030 (LCRA page via search, not fetched, **UNVERIFIED**). I did not read the CCN dockets for the other 765-kV lines, nor PUCT Project 58481 (the large-load rule).
- **Large loads:** only top-5 county lists; no complete county table. The 30 Jun 2026 and Mar 2026 TSP splits are charts.
- **Constraints:** only top-10 lists exist publicly; no contingency pairs; 2024 SOM and 2025 SOM annual PDFs do not carry the tables.
- **Operating-limit values of GTCs** are not public.
- **ERCOT API:** not used. The MIS gave everything needed (GIS report 15933, RTP 24735). I downloaded each report once and recorded it.
- Wood Mackenzie's transformer lead-time survey is paywalled; the only numbers I have are secondary (marked UNVERIFIED).

## Source URLs (primary documents used)

- GIS report and co-located battery report: https://www.ercot.com/misapp/GetReports.do?reportTypeId=15933
- TPIT: https://www.ercot.com/gridinfo/planning (file `ERCOT-July-Ad-Hoc-TPIT-No-Cost-071326-UPDATE.xlsx`, updated 17 Jul 2026)
- 2025 RTP (zip with Appendix E): https://www.ercot.com/misapp/GetReports.do?reportTypeId=24735
- 2025 and 2024 Reports on Existing and Potential Electric System Constraints and Needs: https://www.ercot.com/files/docs/2025/12/23/2025-Report-on-Existing-and-Potential-Electric-System-Constraints-and-Needs.pdf and https://www.ercot.com/files/docs/2024/12/20/2024-report-on-existing-and-potential-electric-system-constraints-and-needs.pdf
- 2024 RTP 345-kV vs 765-kV comparison: https://www.ercot.com/files/docs/2025/01/27/2024-regional-transmission-plan-rtp-345-kv-plan-and-texas-765-kv-strategic-transmission-expans.pdf
- Permian Basin Reliability Plan Study and Addendum: https://www.ercot.com/files/docs/2024/07/25/2024-ERCOT-Permian-Basin-Reliability-Plan-Study-Report-and-Addendum.zip
- PUCT Project 55718 second order: https://interchange.puc.texas.gov/Documents/55718_109_1492424.PDF
- RPG meeting decks: https://www.ercot.com/committees/other/rpg (calendar pages linked from it)
- 2025 RTP stability limits deck: https://www.ercot.com/files/docs/2025/07/29/2025-Regional-Transmission-Plan-RTP-Economic-Study-Stability-Interface-Limits-Update.pdf
- Potomac Economics December 2025 and December 2024 monthly reports: https://www.potomaceconomics.com/wp-content/uploads/2026/01/2025-12_Nodal_Monthly_Report.pdf and https://www.potomaceconomics.com/wp-content/uploads/2025/01/2024-12_Nodal_Monthly_Report.pdf
- Potomac 2025 State of the Market: https://www.potomaceconomics.com/wp-content/uploads/2026/06/2025-State-of-the-Market-Report-for-ERCOT.pdf
- Batch Zero update (Board, Sep 2026): https://www.ercot.com/files/docs/2026/09/11/14-Batch-Zero-Update.pdf
- MISO guide for MTEP24: https://cdn.misoenergy.org/20240501%20PSC%20Item%2004%20MISO%20Transmission%20Cost%20Estimation%20Guide%20for%20MTEP24632680.pdf
- DOE LPT report (Jul 2024): https://www.energy.gov/sites/default/files/2024-10/EXEC-2022-001242%20-%20Large%20Power%20Transformer%20Resilience%20Report%20signed%20by%20Secretary%20Granholm%20on%207-10-24.pdf
- NLR LPT gap analysis: https://docs.nlr.gov/docs/fy26osti/96742.pdf
- DOE DLR report (2019): https://www.energy.gov/sites/prod/files/2019/08/f66/Congressional_DLR_Report_June2019_final_508_0.pdf
- Rule documents: see the source table in `notes/rules.md`.

## Hand-offs

- **Phase 2:** join keys. TPIT `sswg_bus_numbers` and the queue's `poi_bus_no` are PSS/E bus numbers; `constraints_annual.element_id` holds SCED short names (such as TWINBU-HARGROVE) and `ELEMENT_ALIASES` in `docs_parse.py` maps 15 of them to ERCOT's report wording. Contingencies must come from NP6-86-CD.
- **Phase 3:** queue MW by county and POI (`queue_storage.csv`), with the FIS/IA stage as a saturation weight (IA signed means a firm commitment).
- **Phase 4:** `unit_costs.csv` and the lead-time rows; the TPIT rows tell you whether a constraint has a scheduled fix; the C&N reports list the RTP economic-study projects (White River-Long Draw 345 kV tested at $196 M to $203 M break-even; North-to-Houston options at $116 M to $135 M, 2025 RTP Appendix P, not endorsed).
