# Phase 1a: ERCOT market data (10 Oct 2026)

Agent 1a. Everything here was fetched from public ERCOT MIS lists on 10 Oct 2026 with no key. The ERCOT Public API
archive (2023 to 2026) was **not** reachable because no credentials exist yet, so the nodal price history is one week long, not
three years. That is the main limit of this phase and it is stated again at the end.

## Read this first

- **What we have.** Seven days of binding constraints (NP6-86-CD) plus the archived 25 Sep day, 5.4 days of prices at all
  19,493 electrical buses (NP6-787) and system lambda for every SCED run (NP6-322), 7 days of 15-minute prices at 1,130
  distinct settlement point names (NP6-905), 31 days of day-ahead bus LMPs, SPPs and shadow prices, and ancillary-service clearing
  prices for the day-ahead market since 2010 and for real time since RTC+B went live on 5 Dec 2025.
- **SCED timestamps join.** `sced_ts` matches across `lmp_bus`, `lambda` and `shadow_sced` (rates in the join table below).
- **LMP minus lambda behaves.** In the 14 SCED runs where no constraint binds, every one of the 19,493 buses is within half a cent
  of lambda (section 9). In the runs where something binds, 98% of bus-rows differ from lambda by more than half a cent, 88% by more than
  $1 and 39% by more than $10: congestion is **not** sparse in this window, so the shift-factor fit in Phase 2 cannot assume most buses are unaffected.
- **The 15-minute SPP at a resource node or hub is the time-weighted mean of the 5-minute LMP plus the time-weighted mean of the
  real-time reliability deployment price adder (RTRDPA)**, to within a cent (section 10). A nodal 15-minute lane that is to be
  compared with the arena's zonal SPP lanes has to add the adder, which is in `price_adders.parquet`. Load-zone SPPs follow the same rule
  only approximately (up to about 35 cents in a few intervals).
- **Ancillary-service prices have collapsed.** DAM REGUP averaged $26.8/MW-h in 2023, $6.0 in 2024, $3.0 in 2025 and $2.1 in 2026 to date;
  real-time RTC+B prices since 5 Dec 2025 average $0.5 to $2.5 by product (section 12).
- **The RT bus list already follows the Oct ML1 mapping**, although ERCOT dates that file 13 Oct. All 19,493 priced buses are
  in it. `data/settlement_points.csv` is therefore built from Oct ML1 (details under "Mapping refresh").
- **Everything is resumable.** `run_grid.py collect-daily` fetches what is new and parses only what changed. The windows are
  short (5 days for the bus LMPs), so it must run at least every 4 days if we want the history to be gap-free. Nothing is scheduled.

## 1. Reports, reportTypeIds and MIS windows

reportTypeIds were read from the MIS catalogue (`/misapp/servlets/IceRepListJsonWS`, 183 public report types) and each
list was fetched from `/misapp/servlets/IceDocListJsonWS?reportTypeId=<id>` (same rows as `GetReports.do`, plus file size and
publish time; the HTML page is the fallback in the code and was tested). Retention is the gap between a file's publish time and
its `ExpiredDate` in the list, and the span of publish dates seen on 10 Oct 2026. Each list holds `_csv.zip` and `_xml.zip`; only csv was taken
except where noted.

| Report | ERCOT product | reportTypeId | Files per list (csv) | Retention seen | Raw folder |
|---|---|---|---|---|---|
| Settlement points and electrical bus mapping | NP4-160-SG | **10008** | 3 versions (posted 16 Sep, 23 Sep, 1 Oct; `Sep_ML2`, `Sep_ML3`, `Oct_ML1`) | 31 days | `NP4-160-SG` |
| SCED shadow prices and binding constraints | NP6-86-CD | **12302** | 178 hourly files | 7 days (3 Oct to 10 Oct) | `NP6-86-CD` |
| Settlement point prices, resource nodes, hubs, zones (15 min) | NP6-905-CD | **12301** | 715 (one per 15-min interval) | 7 days | `NP6-905-CD` |
| LMPs by electrical bus (per SCED run) | NP6-787-CD | **11485** | 1,588 (one per SCED run) | 5 days (5 Oct to 10 Oct) | `NP6-787-CD` |
| SCED system lambda | NP6-322-CD | **13114** | 1,588 | 5 days | `NP6-322-CD` |
| DAM hourly LMPs by bus | NP4-183-CD | **12328** | 31 (one per delivery day) | 31 days | `NP4-183-CD` |
| DAM shadow prices | NP4-191-CD | **12332** | 31 | 31 days | `NP4-191-CD` |
| DAM settlement point prices | NP4-190-CD | **12331** | 31 | 31 days | `NP4-190-CD` |
| DAM clearing prices for capacity (ancillary services) | NP4-188-CD | **12329** | 31 | 31 days | `NP4-188-CD` |
| Real-time clearing prices for capacity, per SCED run (RTC+B AS prices) | NP6-332-CD | **24891** | 2,167 | 7 days | `NP6-332-CD` |
| Real-time clearing prices for capacity, 15-min settlement interval | NP6-331-CD | **24898** | 716 | 7 days | `NP6-331-CD` |
| Historical DAM clearing prices for capacity, yearly | (MIS "Historical DAM Clearing Prices for Capacity") | **13091** | 17 yearly zips, 2010 to 2026 | no expiry | `HIST-DAM-MCPC` |
| Historical RT clearing prices for capacity, per SCED run | (MIS "Historical RTM ... by SCED Interval") | **25569** | 2 (2025: from 5 Dec; 2026: Jan to 3 Oct), xlsx | no expiry | `HIST-RTM-MCPC-SCED` |
| Historical RT clearing prices for capacity, 15-min | (MIS "Historical RTM ... 15-Minute Settlement Interval") | **25570** | 2, xlsx | no expiry | `HIST-RTM-MCPC-15` |
| *Extra:* LMPs at resource nodes, zones, hubs (per SCED run) | NP6-788-CD | 12300 | 1,588 | 5 days | `NP6-788-CD` |
| *Extra:* real-time price adders per SCED run | NP6-323-CD | 13221 | 1,585 | 5 days | `NP6-323-CD` |
| *Extra:* DAM system lambda | NP4-523-CD | 13113 | 31 | 31 days | `NP4-523-CD` |
| *Extra:* electrical bus to hub list | (Electrical Bus to Hub List) | 10011 | 3 versions | current + 2 | `EB-TO-HUB` |

**Real-time ancillary prices after RTC+B.** They exist and are public: 24891 and 24898 above, plus the yearly archives 25569 and 25570,
which start at 00:00 on 5 Dec 2025, the go-live. Other RTC+B-era reports in the catalogue, not fetched: 24889 RTD indicative
real-time MCPC, 24893 DAM and SCED ancillary-service demand curves, 13226 reserve discount factors, 25568 historical total
capability available for ancillary services, 13222 RTD indicative price adders, 13231 / 13240 historical price adders.

**Other catalogue entries Phase 1b or later may want** (not fetched here): 15933 GIS report, 27292 GIS report for
transmission interconnection costs, 24735 Regional Transmission Plan (public), 12338 / 12336 / 12337 RUC binding constraints,
13060 / 13061 historical DAM and RTM load zone and hub prices.

**Not obtainable without the API:** shadow prices and bus LMPs before the MIS windows above. Searching the 183 public
report types found no yearly archive for NP6-86-CD, NP6-787, NP6-322 or NP4-191.

## 2. What was downloaded

| raw folder / report | reportTypeId | files | MB |
|---|---|---|---|
| NP4-160-SG | 10008 | 3 | 0.8 |
| NP6-86-CD | 12302 | 179 | 0.4 |
| NP6-905-CD | 12301 | 718 | 6.0 |
| NP6-787-CD | 11485 | 1,595 | 165.8 |
| NP6-322-CD | 13114 | 1,594 | 0.6 |
| NP4-183-CD | 12328 | 31 | 72.6 |
| NP4-191-CD | 12332 | 31 | 0.6 |
| NP4-190-CD | 12331 | 31 | 5.6 |
| NP4-188-CD | 12329 | 31 | 0.0 |
| NP4-523-CD | 13113 | 31 | 0.0 |
| NP6-332-CD | 24891 | 2,173 | 0.8 |
| NP6-331-CD | 24898 | 718 | 0.3 |
| NP6-788-CD | 12300 | 1,595 | 13.5 |
| NP6-323-CD | 13221 | 1,592 | 0.7 |
| HIST-DAM-MCPC | 13091 | 17 | 1.5 |
| HIST-RTM-MCPC-SCED | 25569 | 2 | 6.1 |
| HIST-RTM-MCPC-15 | 25570 | 2 | 2.9 |
| EB-TO-HUB | 10011 | 3 | 0.2 |

Total: **10,346 files, 278.4 MB** from the MIS lists, all through `gridsite.fetch_ercot`, all in
`research-data/grid/manifest.csv` (`source=ercot`, `method=mis`). No failures: every listed csv was fetched and every file
was a valid zip. NP6-787 came to 165 MB compressed, far under the 4 GB limit, so nothing was cut to 7 days.
Raw folders use the report id above; the four reports with no NP id of their own use `HIST-DAM-MCPC`, `HIST-RTM-MCPC-SCED`,
`HIST-RTM-MCPC-15` and `EB-TO-HUB`.

## 3. Derived tables (`research-data/grid/derived/`)

Conventions (PLAN.md): prices in $/MWh. `sced_ts` and every `*_utc` column are UTC instants (`datetime64[us, UTC]`).
`date_cpt` is the Central Prevailing Time date as `YYYY-MM-DD` and `i15` the 15-minute wall-clock index of that day (0 to 95).
`dst_rep` flags the repeated fall-back hour. Rows keep their true instant; `ercot_parse.to96()` applies the arena rule (drop
the repeat, a skipped hour repeats the hour before). No DST change falls inside any window (the next is 1 Nov 2026), so that
path is tested on synthetic days only.

| Table | Source | Columns | Notes |
|---|---|---|---|
| `spp_node` | NP6-905-CD | settlement_point, sp_type, interval_start_utc, date_cpt, i15, price, dst_rep | 15-min SPP at every resource node, hub and load zone. `sp_type`: RN, PCCRN, LCCRN, PUN, LZ, LZEW, LZ_DC, LZ_DCEW, HU, SH, AH. |
| `lmp_bus` | NP6-787-CD | sced_ts, bus, lmp | **Capped LMP**, float32, 19,493 buses per SCED run. Uncapped LMP was not kept. Row groups hold about 1M rows, ordered by posting day then file. |
| `lambda` | NP6-322-CD | sced_ts, lambda, lambda_uncapped, date_cpt, i15, dst_rep | `lambda` is the **capped** system lambda. |
| `shadow_sced` | NP6-86-CD, plus the archived 25 Sep file set | sced_ts, date_cpt, i15, dst_rep, sced_ts_raw, repeated_hour_flag, constraint_id, constraint_name, contingency_name, **cid**, from_station, to_station, from_kv, to_kv, limit, value, flow, shadow_price, max_shadow_price, violated_mw, cct_status, element_type, **source** | Every ERCOT column, renamed. `flow` = `value` (alias). `cid` = `constraint_name\|contingency_name`. `source` is `mis` or `archive`. `element_type` is a **DERIVED heuristic** (no stations = `gtc`; different end voltages = `xfmr`; same voltage = `line`), not an ERCOT field. Rows with `shadow_price` 0 are constraints SCED listed but did not bind; filter `shadow_price > 0` for binding. |
| `shadow_dam` | NP4-191-CD | interval_start_utc, date_cpt, h, dst_rep, delivery_date, hour_ending, constraint_id, constraint_name, contingency_name, cid, from_station, to_station, from_kv, to_kv, limit, value, flow, violation_amount, shadow_price, delivery_time, dst_flag, element_type, source | `h` is the 0-based CPT hour. ERCOT pads names with a leading space; stripped. No max shadow price column in the DAM report. |
| `lmp_dam_bus` | NP4-183-CD | bus, interval_start_utc, date_cpt, h, lmp, dst_rep | 14.4M rows. The DAM bus list is not the RT list (see findings). |
| `spp_dam` | NP4-190-CD | settlement_point, interval_start_utc, date_cpt, h, price, dst_rep | |
| `as_prices` | 13091, 25569, 25570, NP4-188, NP6-331, NP6-332 | market, product, interval_start_utc, date_cpt, i15, dur_min, mcpc, mcpc_uncapped, dst_rep, source | `market`: `DAM` (hourly, from 1 Dec 2010), `RT_SCED` (per SCED run) and `RT_15` (15-min), both RT markets from 5 Dec 2025. Products: REGUP, REGDN, RRS, NSPIN, ECRS (ECRS from 2023). `mcpc` is the capped price. `source` is `hist` (yearly archive) or `mis`; where both cover an instant the MIS row wins, and they agree to the cent over the overlap (checked: 2,880 DAM, 1,450 RT-SCED, 480 RT-15 rows, zero differences). |
| *extra* `lmp_node` | NP6-788-CD | sced_ts, settlement_point, lmp | 5-min LMP at resource nodes, zones and hubs. |
| *extra* `price_adders` | NP6-323-CD | sced_ts, system_lambda, rtrdpa, rtrdparus, ... | `rtrdpa` is the energy reliability deployment adder that enters the SPP. |
| *extra* `lambda_dam` | NP4-523-CD | interval_start_utc, date_cpt, h, lambda, dst_rep | |

Read the bus tables with pyarrow if memory matters: `pq.read_table(f, columns=[...], read_dictionary=["bus"])`.

## 4. Coverage

| table | rows | first_cpt | last_cpt | mb |
|---|---|---|---|---|
| spp_node | 810210 | 2026-10-02 23:45 | 2026-10-10 10:45 | 1.80 |
| lambda | 1594 | 2026-10-05 00:00 | 2026-10-10 11:00 | 0.00 |
| shadow_sced | 24742 | 2026-09-24 23:00 | 2026-10-10 10:55 | 0.40 |
| shadow_dam | 34249 | 2026-09-10 00:00 | 2026-10-10 23:00 | 0.60 |
| spp_dam | 837216 | 2026-09-10 00:00 | 2026-10-10 23:00 | 1.80 |
| lambda_dam | 744 | 2026-09-10 00:00 | 2026-10-10 23:00 | 0.00 |
| as_prices | 1184585 | 2010-12-01 00:00 | 2026-10-10 23:00 | 9.70 |
| price_adders | 1592 | 2026-10-05 00:00 | 2026-10-10 11:05 | 0.10 |
| lmp_node | 1802350 | 2026-10-05 00:00 | 2026-10-10 11:05 | 4.00 |
| lmp_dam_bus | 14444016 | 2026-09-10 00:00 | 2026-10-10 23:00 | 45.80 |
| lmp_bus | 31091335 | 2026-10-05 00:00 | 2026-10-10 11:05 | 105.10 |

## 5. Counts

| item | count |
|---|---|
| buses in lmp_bus | 19493 |
| of which in settlement_points.csv | 19493 |
| mapped buses never priced in lmp_bus | 0 |
| settlement points of type RN in spp_node | 831 |
| settlement points of type PCCRN in spp_node | 160 |
| settlement points of type LCCRN in spp_node | 68 |
| settlement points of type PUN in spp_node | 52 |
| settlement points of type LZ in spp_node | 8 |
| settlement points of type LZEW in spp_node | 8 |
| settlement points of type HU in spp_node | 5 |
| settlement points of type LZ_DC in spp_node | 4 |
| settlement points of type LZ_DCEW in spp_node | 4 |
| settlement points of type SH in spp_node | 1 |
| settlement points of type AH in spp_node | 1 |
| resource nodes in NP4-160 (current model) | 1043 |
| distinct constraints listed (archive) | 68 |
| distinct binding constraints, shadow price > 0 (archive) | 46 |
| distinct constraints listed (mis) | 119 |
| distinct binding constraints, shadow price > 0 (mis) | 98 |
| distinct binding constraints (all sources) | 120 |
| distinct binding DAM constraints (NP4-191) | 1346 |

How the settlement-point types line up with the mapping: all 831 `RN`, 160 `PCCRN` and 52 `PUN` points in `spp_node` are
`RESOURCE_NODE` values in NP4-160, and every NP4-160 resource node is priced in `spp_node`. The 68 `LCCRN` points (controllable
load resources) are not in the NP4-160 `RESOURCE_NODE` column. Hubs and zones in `spp_node`: HB_BUSAVG, HB_HUBAVG, HB_HOUSTON,
HB_NORTH, HB_PAN, HB_SOUTH, HB_WEST and LZ_AEN, LZ_CPS, LZ_HOUSTON, LZ_LCRA, LZ_NORTH, LZ_RAYBN, LZ_SOUTH, LZ_WEST.

Constraint stations: 145 of the 156 distinct from/to station names in `shadow_sced` are NP4-160 `SUBSTATION` codes. The 11 that
are not (BRAND, BROOKTAP, CATSW, ENCRT, FGLSW, FRYSW, GANSO, LAKENASW, PGTP1, THSTP, V4_TAP) are switch stations or taps named
differently in the constraint report; Phase 2 needs an alias step for them. The three constraints with no stations at all are
interface limits: `EASTEX`, `E_PASP` and `NELRIO`, each with contingency `BASE CASE`.

## 6. Do the SCED timestamps join?

| item | count | match rate |
|---|---|---|
| lambda runs | 1594 |  |
| lmp_bus runs | 1595 |  |
| shadow_sced runs (MIS) | 2150 |  |
| shadow_sced runs inside the lambda/lmp_bus window | 1593 |  |
| lmp_bus runs that are in lambda | 1594 | 99.9% |
| lambda runs that are in lmp_bus | 1594 | 100.0% |
| shadow runs (in window) that are in lambda | 1593 | 100.0% |
| shadow runs (in window) that are in lmp_bus | 1593 | 100.0% |

The one `lmp_bus` run with no lambda row is the newest run: its lambda file had not been listed yet when the lambda list was fetched. Shadow rows run
about an hour behind the price files (NP6-86 is an hourly report), which is why the shadow window ends before the price window.

## 7. Binding share

SCED runs with at least one binding constraint (shadow price > 0): **99.1%** of 1593 runs; 100.0% of runs list any constraint at all (binding or near-binding).

A run "lists" a constraint when NP6-86 has a row for it, binding or not. Almost every five-minute SCED run in this week has at
least one constraint with a positive shadow price.

## 8. Top 25 binding constraints, 3 to 10 Oct 2026 (MIS window)

Ranked by the sum over SCED runs of `shadow_price` x 5/60 hours, in $/MW of relief (ASSUMPTION: one run covers 5 minutes).
`at_cap_pct` is the share of binding runs where the shadow price sat at the constraint's `max_shadow_price`; that means SCED had run
out of ways to relieve the element and the price is the cap, not a true marginal value. Sign convention for later phases:
`cong[b,t] = LMP[b,t] - lambda[t] = - sum_k SF[b,k] * mu[k,t]`, with `mu` the published (positive) shadow price.

| # | constraint | contingency | from - to | kV | type | limit MW | runs | hours | mean $ | max $ | at cap % | $ x h per MW | share % |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | 6437__F | DMTSCOS5 | SCRCV - KNAPP | 138 | line | 231 | 530 | 44.2 | 937 | 3500 | 3 | 41377 | 16.2 |
| 2 | FORTMA_YELWJC1_1 | DBIGKEN5 | FORTMA - YELWJCKT | 138 | line | 145 | 231 | 19.2 | 1224 | 3500 | 2 | 23566 | 9.2 |
| 3 | COLLE_JUPIT_1 | DRYSALN5 | COLLEGE - JUPITER | 138 | line | 272 | 209 | 17.4 | 1169 | 3500 | 1 | 20351 | 8.0 |
| 4 | 6429__D | DMTSCOS5 | ENCRT - BRAND | 138 | line | 412 | 428 | 35.7 | 495 | 2231 | 0 | 17649 | 6.9 |
| 5 | LAKENA_SAMATH1_1 | DBWNAMO5 | LAKENASW - SAMATHIS | 69 | line | 38 | 144 | 12.0 | 1438 | 2800 | 21 | 17256 | 6.7 |
| 6 | COL_BRAN_1 | DRYSALN5 | BRAND_C - COLLEGE | 138 | line | 272 | 153 | 12.8 | 1265 | 3500 | 18 | 16135 | 6.3 |
| 7 | DT_PK_91_A | DWO5_EU8 | PK - DT | 138 | line | 292 | 383 | 31.9 | 471 | 3500 | 1 | 15018 | 5.9 |
| 8 | DT_PK_91_A | DBI_GT_8 | DT - PK | 138 | line | 293 | 51 | 4.2 | 3500 | 3500 | 100 | 14875 | 5.8 |
| 9 | KOCH_UP_69A1 | SVALVAL9 | KOCH_UP - KOCH_UP | 138/69 | xfmr | 67 | 32 | 2.7 | 3452 | 3500 | 97 | 9207 | 3.6 |
| 10 | 1710__A | DSALHUT5 | SALDS - SONTERRA | 138 | line | 235 | 95 | 7.9 | 796 | 2184 | 0 | 6300 | 2.5 |
| 11 | NLARSW_PILONC1_1 | DFOAVLO5 | NLARSW - PILONCIL | 138 | line | 143 | 241 | 20.1 | 240 | 597 | 0 | 4820 | 1.9 |
| 12 | SGVSW_MR2H | DWTRTRC5 | SGVSW - SGVSW | 345/1 | xfmr | 746 | 80 | 6.7 | 702 | 2122 | 0 | 4680 | 1.8 |
| 13 | BCVPSA03_A | DGBY_KG5 | PSA - BCV | 138 | line | 478 | 229 | 19.1 | 232 | 1984 | 0 | 4433 | 1.7 |
| 14 | BCVPSA03_A | SMDOOAS5 | PSA - BCV | 138 | line | 478 | 286 | 23.8 | 183 | 1211 | 0 | 4362 | 1.7 |
| 15 | SEA_AAT1 | DFRYBC58 | SEA - SEA | 138/69 | xfmr | 99 | 48 | 4.0 | 1039 | 3005 | 0 | 4158 | 1.6 |
| 16 | LAKENA_SAMATH1_1 | DBAKCED5 | LAKENASW - SAMATHIS | 69 | line | 38 | 38 | 3.2 | 1208 | 2800 | 11 | 3826 | 1.5 |
| 17 | 194T172_1 | DAUSLOS5 | GIDEON - BASTCI | 138 | line | 244 | 125 | 10.4 | 329 | 2073 | 0 | 3424 | 1.3 |
| 18 | SNDSW_MR1H | MSNDWCR5 | SNDSW - SNDSW | 345/1 | xfmr | 739 | 309 | 25.8 | 129 | 384 | 0 | 3324 | 1.3 |
| 19 | ASHERT_CATARI1_1 | MLOFOAV5 | ASHERTON - CATARINA | 138 | line | 139 | 246 | 20.5 | 158 | 472 | 0 | 3239 | 1.3 |
| 20 | BLESSING_1382 | DSTPSTA5 | BLESSING - BLESSING | 345/138 | xfmr | 424 | 374 | 31.2 | 100 | 325 | 0 | 3109 | 1.2 |
| 21 | CONCHO_HARI1_A | SGARBAL8 | CONCHO - HARI | 69 | line | 30 | 17 | 1.4 | 2122 | 2800 | 41 | 3006 | 1.2 |
| 22 | BLESSING_1382 | SANGSTA5 | BLESSING - BLESSING | 345/138 | xfmr | 416 | 202 | 16.8 | 165 | 998 | 0 | 2782 | 1.1 |
| 23 | 196T171_1 | DAUSLOS5 | GIDEON - SETTLE | 138 | line | 266 | 391 | 32.6 | 77 | 509 | 0 | 2507 | 1.0 |
| 24 | GN_PZ_08_A | SMDOOAS5 | GN - PZ | 138 | line | 381 | 271 | 22.6 | 108 | 626 | 0 | 2445 | 1.0 |
| 25 | BCVPSA03_A | DBT_SRB8 | PSA - BCV | 138 | line | 472 | 180 | 15.0 | 140 | 893 | 0 | 2097 | 0.8 |

Reading it: this is one week in the autumn shoulder season (PROMPT item 8), so it is outage-driven and local. The leaders are
138 kV and 69 kV lines and a few transformers (the contingency codes such as `DMTSCOS5` are ERCOT's names and are not decoded here). Almost none of the station names in the top 25 appear in `docs/research/ercot.md`; the 2025 list there (Vealmoor-Koch Tap, Twin
Buttes-Hargrove, WESTEX, Laredo VFT, STP-W.A. Parish, Argyle-Lewisville) does not show up this week. The only resemblance is `KOCH_UP_69A1` (a 138/69 kV transformer) against "Vealmoor-Koch Tap";
I have not checked whether that is the same site. Do not rank sites on this week alone.
Only #8 (`DT_PK_91_A` for `DBI_GT_8`) and #9 (`KOCH_UP_69A1`) sit at the cap almost all the time; #5, #6, #21 and #16 reach it in 10 to 40% of their runs; the rest almost never.
`SGVSW_MR2H` and `SNDSW_MR1H` show a 1 kV second end (probably the transformer's tertiary winding; not verified), so the heuristic types them `xfmr`.

## 9. LMP minus lambda

- Runs with no binding constraint: 272,902 bus-rows, 100.000% within 0.005 $/MWh of lambda; mean |LMP-lambda| 0.00236, max 0.005.
- Runs with a binding constraint: 30,779,447 bus-rows, 1.8% of them still equal lambda; share separated from lambda by > $1: 88.4%, > $10: 38.8%, > $100: 1.6%.
- Bus-rows with no lambda for their timestamp: 19,493; bus-rows after the last NP6-86 file (excluded): 38,986.
- Buses that equal lambda in every binding run: 0 of 19,493; buses separated from lambda in over half of binding runs: 19,183.

## 10. SPP identity (what the arena's price lane is made of)

| point | intervals | mean_spp_minus_lmp | max_spp_minus_lmp | mean_adder | median_abs_residual | p99_abs_residual | intervals_residual_over_1c | max_abs_residual |
|---|---|---|---|---|---|---|---|---|
| 7RNCHSLR_ALL | 523 | 0.3828 | 18.5690 | 0.3827 | 0.0027 | 0.0081 | 1 | 0.0107 |
| ADL_RN | 523 | 0.3828 | 18.5770 | 0.3827 | 0.0025 | 0.0074 | 0 | 0.0081 |
| ALGOD_ALL_RN | 523 | 0.3829 | 18.5759 | 0.3827 | 0.0024 | 0.0077 | 0 | 0.0097 |
| HB_NORTH | 523 | 0.3825 | 18.5758 | 0.3827 | 0.0026 | 0.0073 | 0 | 0.0076 |
| LZ_HOUSTON | 523 | 0.3831 | 18.5715 | 0.3827 | 0.0029 | 0.0121 | 9 | 0.0506 |
| LZ_WEST | 523 | 0.3836 | 18.6540 | 0.3827 | 0.0030 | 0.0516 | 40 | 0.3446 |

Checked with `lmp_node`, `price_adders` and `spp_node` over every full 15-minute interval in the window (523 per point). For three resource nodes and the HB_NORTH hub the SPP equals the
SCED-run-duration-weighted mean of the 5-minute LMP **plus** the same-weighted mean of `rtrdpa`; the median residual is 0.25 cents and the worst is 1.1 cents (rounding).
Two load zones do not match exactly: LZ_HOUSTON has 9 intervals off by more than a cent (worst 5 cents) and LZ_WEST 40 (worst 34 cents). My guess,
**not verified**, is that the zone SPP is a load-weighted average of bus prices recomputed per interval, while the 5-minute zone LMP uses the SCED-run weights.
This also confirms the interval convention used for `interval_start_utc` and `i15` (delivery hour 1, interval 1 = 00:00 to 00:15 CPT).

## 11. Mapping refresh (NP4-160-SG)

Three versions are on the list: `Sep_ML2` (posted 16 Sep, file dated 22 Sep), `Sep_ML3` (23 Sep, 30 Sep) and `Oct_ML1` (1 Oct, 13 Oct).
The Sep 2026 copy the repo already held has exactly the same 19,376 rows as `Sep_ML2` (compared cell by cell).

**Which one is current?** ERCOT's file name date says Oct ML1 starts on 13 Oct, but the real-time LMP file lists exactly
Oct ML1's 19,493 buses in every SCED run from 5 Oct (the first day we have), and the DAM LMP file already carries the 116 buses that Oct ML1
adds over ML3 from delivery day 2 Oct. The check against the prices:

| model | file_date | buses | priced | priced_buses_missing_from_model | model_buses_never_priced |
|---|---|---|---|---|---|
| CIM_Sep_ML2_1_09222026 | 2026-09-22 | 19376 | 19364 | 129 | 12 |
| CIM_Sep_ML3_1_09302026 | 2026-09-30 | 19389 | 19377 | 116 | 12 |
| CIM_Oct_ML1_1_10132026 | 2026-10-13 | 19493 | 19493 | 0 | 0 |

So `data/settlement_points.csv` is built from **Oct ML1** (19,493 buses), with `IN_SEP2026` and `IN_PREV_MODEL` flags and an
`NOIE_ZONE` column. The sibling files of that version (`Resource_Node_to_Unit`, `NOIE_Mapping`, `Hub_Name_AND_DC_Ties`, `CCP_Resource_Names`,
`Settlement_Points`) are in `raw/ercot/NP4-160-SG/`; each version is also unpacked in its own subfolder. The 12 buses Oct ML1 removes
(DUBLIN1_8X, EB_MBLLDEI, LN_REDGATE, LN_REDGATETA, MABANK_8W, MABANK_8X, MABANK_8Y, MDSC_L_A, MOBILE1, PARIS_E_8, PHNTMHL_9,
VERH_LD01) are mapped in the older versions and never priced in this window.

What changed:

- **Sep2026 copy -> CIM_Sep_ML2_1_09222026**: buses 19,376 to 19,376 (+0, -0); substations 5,020 to 5,020 (+0, -0); resource nodes +0, -0
- **Sep2026 copy -> CIM_Sep_ML3_1_09302026**: buses 19,376 to 19,389 (+13, -0); substations 5,020 to 5,022 (+2, -0); resource nodes +1, -0
- **Sep2026 copy -> CIM_Oct_ML1_1_10132026**: buses 19,376 to 19,493 (+129, -12); substations 5,020 to 5,035 (+16, -1); resource nodes +7, -0; attribute changes on kept buses {'NODE_NAME': 4, 'PSSE_BUS_NAME': 2, 'RESOURCE_NODE': 1, 'PSSE_BUS_NUMBER': 2}
- **CIM_Sep_ML2_1_09222026 -> CIM_Sep_ML3_1_09302026**: buses 19,376 to 19,389 (+13, -0); substations 5,020 to 5,022 (+2, -0); resource nodes +1, -0
- **CIM_Sep_ML3_1_09302026 -> CIM_Oct_ML1_1_10132026**: buses 19,389 to 19,493 (+116, -12); substations 5,022 to 5,035 (+14, -1); resource nodes +6, -0; attribute changes on kept buses {'NODE_NAME': 4, 'PSSE_BUS_NAME': 2, 'RESOURCE_NODE': 1, 'PSSE_BUS_NUMBER': 2}

Against the Sep 2026 copy, the current model (Oct ML1) has **129 new buses and 12 removed**, **16 new substations and 1 removed**, and 7 new resource nodes.

- New substations (16): CLWTR, COWPEN, CRUCE, DUF, EGRET, FUNS_SLR, KIL, MERRI, MOSQUTLK, PECN_SLR, QUTERNRY, RIOMED, SHALE, STAC_BES, TIGR_SLR, WALL_SLR
- Removed substation (1): MABNK
- New resource nodes (7): DUF_RN, FUNS_SLR_ALL, PECN_SLR_RN, STAC_BES_RN, STAFHI_1_RN, TIGR_SLR_RN, WALL_SLR_RN
- New buses (129): ANASE_XFMR2, B341, B342, B343, B344, BCPSW_BB2N, BCPSW_BB2S, BRNWD_95, BRNWD_B67, BUS001, CBY_5105, CLATNVL, CLEARC_EB2, CLEAR_EB3, CLWTR_EB_LD1, CLWTR_EB_LD2, CN13, CN15, CN16, CN22, CVN_8346, CVN_8347, DUF_0005, DUF_ESR1, DUF_LD1, DUF_LN1, EB018, EB033, EB047, EB1_CN13, ... (+99 more)
- Attribute changes on buses present in both: {'NODE_NAME': 4, 'PSSE_BUS_NAME': 2, 'RESOURCE_NODE': 1, 'PSSE_BUS_NUMBER': 2}. The two BUFF* generator buses swapped node names, PSS/E names and numbers; STAFHI_LD01 gained resource node STAFHI_1_RN.

## 12. Findings that matter downstream

1. **Price separation is dense this week.** See the LMP vs lambda table. A shift-factor fit has to treat all binding constraints
   jointly; it cannot assume a bus only feels one or two of them.
2. **A few shadow prices sit at the cap.** About 2% of the 8,221 binding rows in the MIS window are priced at `max_shadow_price`, and about 2% show a positive
   violation. The caps seen are 3,500 $/MWh (77% of binding rows), 4,500 (18%), 2,800 (4%) and 5,251 (EASTEX). The pinned rows come mainly from #8, #9, #5 and #6 in the table above.
   A price at the cap means SCED could not clear the violation, so it is the cap, not a marginal value. Phase 2 should fit with and without those rows.
3. **The DAM and RT bus lists differ.** `lmp_dam_bus` has 19,513 distinct buses over 31 days against 19,493 in RT. 124 DAM buses are not in
   the Sep ML3 mapping (8 of them until 16 Sep, 116 from 2 Oct); 20 are not in Oct ML1 either.
4. **DAM shadow prices are broad.** 1,346 distinct constraints bind in the day-ahead market over 31 days (about 1,100 rows a day), against
   98 in real time over the MIS week (120 with the archived day). 84 of the 98 real-time `cid`s also bind in the DAM at some point in those 31 days. 724 of 808 DAM constraint stations are NP4-160 substations.
5. **Hub and zone prices.** `spp_node` and `lmp_node` both carry HB_* and LZ_* points, so zone and hub lanes can be rebuilt from the same files as the nodes.
6. **Ancillary-service prices are small and still falling.** Mean DAM clearing price by year ($/MW-h):

| year | ECRS | NSPIN | REGDN | REGUP | RRS |
|---|---|---|---|---|---|
| 2022 | - | 22.49 | 8.46 | 21.67 | 20.31 |
| 2023 | 58.16 | 19.05 | 13.63 | 26.80 | 21.27 |
| 2024 | 8.23 | 6.22 | 3.44 | 6.01 | 5.67 |
| 2025 | 2.46 | 3.05 | 1.62 | 2.98 | 2.76 |
| 2026 | 2.01 | 4.21 | 1.31 | 2.11 | 1.64 |

   Since RTC+B went live (5 Dec 2025), per market and product:

| market | product | rows | mean | p95 | max |
|---|---|---|---|---|---|
| DAM | ECRS | 7439 | 2.00 | 6.87 | 1,000.65 |
| DAM | NSPIN | 7439 | 4.27 | 15.00 | 432.13 |
| DAM | REGDN | 7439 | 1.34 | 4.71 | 26.71 |
| DAM | REGUP | 7439 | 2.15 | 6.04 | 896.96 |
| DAM | RRS | 7439 | 1.68 | 5.00 | 999.02 |
| RT_15 | ECRS | 29704 | 0.90 | 3.48 | 706.91 |
| RT_15 | NSPIN | 29704 | 2.59 | 13.02 | 723.90 |
| RT_15 | REGDN | 29704 | 1.04 | 3.63 | 67.31 |
| RT_15 | REGUP | 29704 | 1.38 | 3.72 | 364.42 |
| RT_15 | RRS | 29704 | 0.55 | 1.88 | 353.50 |
| RT_SCED | ECRS | 90137 | 0.85 | 3.35 | 821.83 |
| RT_SCED | NSPIN | 90137 | 2.49 | 12.68 | 844.97 |
| RT_SCED | REGDN | 90137 | 1.05 | 3.86 | 71.75 |
| RT_SCED | REGUP | 90137 | 1.35 | 3.70 | 420.34 |
| RT_SCED | RRS | 90137 | 0.51 | 1.88 | 410.96 |

   Real-time prices sit below day-ahead prices for every product, with rare spikes of several hundred dollars. Phase 3's
   ancillary layer should start from these, not from 2023 prices.

## 13. What failed, was blocked, or is untested

- **ERCOT Public API: blocked, untested.** `fetch_ercot.fetch_api_archive` is written (token by POST, key in a header, archive listing,
  batched download of the zip of zips, manifest rows with `method=api`, a cap of 5 batches per call for the
  3-downloads-per-12-months rule). It has **not been run**: no credentials. The endpoint paths and the token flow come from ERCOT's
  public documentation as I recall it and are unverified. Without it there is no bus LMP before 5 Oct, no shadow price before 3 Oct (plus the archived 25 Sep day),
  and no gap-free history between 26 Sep and 3 Oct.
- **No DST change inside any window.** The repeated-hour and skipped-hour handling is checked on synthetic days only.
- **NP6-86-CD lags the price files by about an hour**, so the newest hour of bus LMPs has no constraint list yet; those runs are excluded from the binding share and the LMP vs lambda check.
- **Capped values only** in `lmp_bus` and `lambda`; the uncapped LMP was not stored. They differ only in scarcity.
- **Bugs I hit and fixed on the way** (no data affected): a posting-date regex that matched the report id instead of the date; the mapping zip nests its csv
  files in a folder; the yearly xlsx archives carry timestamps like `00:00:28.999` that must be rounded, not cut, or the join to the MIS
  files misses by a second; `pandas` has no `.dt.utcoffset`.
- **Not verified against ERCOT documentation:** the `element_type` heuristic, and the choice of the capped LMP and lambda as "the" price.
- No account was created, nothing was submitted to any ERCOT form, and no key or token was read, printed or stored.

## 14. How to run

From `docs/research/grid` with `C:/Users/Javier/Repo/BaseP/research-data/.venv/Scripts/python.exe`:

```
run_grid.py fetch-ercot all dry-run        # list and estimate, download nothing
run_grid.py fetch-ercot NP6-86-CD NP6-787-CD   # fetch whatever is new for those reports
run_grid.py parse-ercot [table ...]        # rebuild parquet incrementally (default: all)
run_grid.py collect-daily                  # fetch everything new, then parse; appends a line to research-data/grid/collect_daily.log
run_grid.py report-ercot                   # regenerate the coverage / join / top-constraint / LMP-vs-lambda tables above
```

**To get the deeper history the user has to register.** The Public API needs an ERCOT API Explorer account, a subscription
to the public-reports product, and then three environment variables: `ERCOT_API_USERNAME`, `ERCOT_API_PASSWORD`,
`ERCOT_API_SUBSCRIPTION_KEY`. With them set, `run_grid.py fetch-ercot NP6-86-CD api start=2023-01-01 end=2023-02-01` tries the archive
for one month. Run it on one month first and check the manifest before going wider: ERCOT allows 3 downloads of the same report per
12 months through the API, and I have not been able to test that path.
