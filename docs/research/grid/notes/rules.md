# ERCOT market and interconnection rules that decide battery value at a site

Written 10 Oct 2026 by agent 1b. Rule texts are the versions posted on ercot.com on 10 Oct 2026 (Nodal Protocols and Planning Guide
dated 1 Aug 2026, Nodal Operating Guide 1 Aug 2026), downloaded to `research-data/grid/raw/docs/` and recorded in the manifest. Every
claim carries a citation; the source list at the end gives file names and URLs. Anything I could not confirm in a primary text is
marked **UNVERIFIED**. "NP" is the Nodal Protocols, "PG" the Planning Guide, "NOG" the Nodal Operating Guide, "TAC" the Texas
Administrative Code. These notes are research, not legal advice.

## Summary of what the rules mean for the siting model

1. A battery registered as an ESR on the transmission grid, or as a distribution ESR (DESR), settles at a **resource-node price**, so the
   congestion at its bus is paid to it. A battery inside an **ADER** aggregation settles at the **load-zone price** and gets no nodal
   congestion value. Small sites that register as settlement-only storage settle at the load zone today, with a nodal price pending.
2. A separately metered ("wholesale") storage load pays the **nodal energy price** to charge and is **exempt from 4CP transmission
   charges** and from load-ratio-share allocations.
3. Since RTC+B went live on **5 Dec 2025**, energy and ancillary services are bought together every five minutes and a battery's
   state of charge limits how much of each it can carry.
4. For a connection agreement signed **after 31 Dec 2025**, the generator pays interconnection-facility costs above a **$14 M (up to
   138 kV) or $20 M (above 138 kV)** allowance (2024 dollars, CPI-indexed from 2026). The TSP pays for network upgrades. The risk left
   with the generator is curtailment, not an upgrade bill.
5. New inverter storage with an agreement signed on or after **1 Apr 2026** must show grid-forming capability (NOG 2.14) and must pass
   a series-capacitor subsynchronous-oscillation screen (PG 5.2.10).

---

## (a) Where each connection type settles

| Connection type | Registered as | Settles at | Congestion value paid to the battery? | Citation |
|---|---|---|---|---|
| Transmission-connected ESR (TESR) | ESR, more than 10 MW (not a self-generator), or more than 1 MW that elects registration | Its **Resource Node**: an electrical bus. Day-ahead and real-time SPPs are computed there. Charging load is withdrawn at the same node. | **Yes, fully nodal** | NP §2 ("Resource Node", "Transmission Energy Storage Resource"); NP §4.6.1.1; NP §6.6.1.1; NP §6.6.3.1; NP §22 Att. R §2(b), §3.1(c), §3.1(h) |
| Distribution-connected ESR (DESR) and distribution generation resource (DGR) | DESR/DGR, same thresholds | Resource Node **fixed at a single electrical bus** in the network model; may be set at the resource's connectivity node. Injections and withdrawals settled at a Resource Node are removed from load-zone weights. | **Yes, nodal at that bus**. Ancillary-service eligibility is limited if the feeder is subject to load shed. | NP §2 ("DESR", "DGR"); NP §3.8.6(1), (3); NP §22 Att. R §3.1(m); NP §6.6.1.4 (definition of State Estimator Load) |
| Settlement-only distribution or transmission generator (SODG, SOTG), 10 MW or less, including storage sites | SOG | Nodal energy price at the bus mapped to the load (SODG) or at the meter location (SOTG), floor -$251/MWh, **except** (i) a site that opts out and keeps load-zone pricing, and (ii) any site where energy storage is **more than 50%** of the site's settlement-only nameplate, which settles at the **load-zone** price | Nodal for generator-type sites. **Not** for storage-dominated sites today. NPRR995 (settlement-only energy storage systems) is written into the protocols as pending "upon system implementation" and would move them to a nodal price. | NP §6.6.3.8(1)-(5), and the NPRR995 replacement text in the same section; NP §6.6.3.2 (load-zone energy imbalance definition: "MWh quantities for ESS SODGs and SOTGs ... will be included in this value") |
| Wholesale storage load (WSL), the charging side of any storage | Separately metered storage, resold at wholesale | **Nodal price at the bus connecting the facility to the transmission system, or the nearest such bus if connected at distribution voltage** | Yes, on the charging side | 16 TAC §25.501(m)(2); NP §2 ("Wholesale Storage Load") |
| ADER aggregation (home-battery fleets, premises of 1 MW or less each) | Pilot resource modelled as a Load Resource (aggregate load resource or non-controllable load resource) | **Load-zone price**. Deployment uses load-zone shift factors. All premises must sit in one load zone. | **No nodal value** | ADER Pilot Project Governing Document Phase 3.3, "Settlement and Cost Allocation" and "Deployment" sections; ERCOT market notice M-A030226-01 |

Further facts:

- The ADER pilot capacity limit rose from 200 MW to **500 MW** on 2 Mar 2026. The ancillary-service limit stays at **100 MW**, and the
  QSE share limit rose from 50% to 90% (M-A030226-01).
- The pilot's own studies cover finer settlement for ADERs, such as logical resource nodes and smaller load zones. These are
  off-line studies, not rules (ADER Phase 3.3, "Settlement and Cost Allocation").
- A resource node is placed as close as possible to the metering point. No constrainable element may lie between the node and the
  resource's connectivity node (NP §22 Att. R §3.2(f)), so a battery cannot be paid for congestion between its meter and its node.
- **UNVERIFIED:** which transmission bus a given distribution feeder maps to. NP §3.8.6(3) and Att. R §3.1(m) say the node is a single bus in
  the network model, and 16 TAC §25.501(m)(2) says "nearest bus that connects to the transmission system". The mapping is in each
  DESR's registration data (NP §23 Form R), which is not public.
- The home-fleet case in `docs/research/ercot.md` (LZ_NORTH price for ADER) agrees with the Governing Document.

## (b) RTC+B (live 5 Dec 2025)

- ERCOT announced "the successful implementation of the first operating day" of RTC+B on 5 Dec 2025. It buys energy and ancillary
  services together in real time and models battery state of charge when offering energy and ancillary services (ERCOT news
  release, 5 Dec 2025).
- **Products** (settled and cleared per product): Regulation Up and Down, Responsive Reserve (RRS, including primary and fast
  frequency response), ERCOT Contingency Reserve Service (ECRS), and Non-Spin (NP §2 definitions; NP §4.4.7.2). A QSE may offer the same ESR capacity into energy and any or all
  products at once, "inclusive or exclusive" of one another (NP §4.4.7.2(1)).
- **Day-ahead:** ancillary-service offers are due by 1000 the day before (NP §4.4.7.2(1)). **Real time:** QSEs update offers any time. SCED uses the latest offers, awards are
  based on capability and "State of Charge (SOC), SOC limits", against ancillary-service demand curves, and awards and real-time
  market clearing prices for capacity are "immediately binding upon the completion of a SCED run" (NP §6.4.9.1, §6.4.9.1.1).
- **How batteries carry them:** SCED "accounts for each ESR's State of Charge (SOC) and SOC operating limits", so Base Points and
  awards are feasible given the duration requirements and "do not violate the ESR's MinSOC and MaxSOC" (NP §6.5.7.3(1)).
- **Duration (NPRR1282):** Regulation and RRS **30 minutes** (was 15), ECRS **1 hour** (was 2), Non-Spin stays at **4 hours** or more, FFR 15 minutes. RUC studies use 1 hour for
  all products except FFR. The Board recommended approval on 24 Jun 2025; the PUCT report of 31 Jul 2025 shows status "Approved"
  (NPRR1282 Board Report; NPRR1282 PUCT Report). **UNVERIFIED:** the final Non-Spin number in the current protocol text. The 1 Aug 2026 NP files I downloaded no
  longer contain the NPRR tags, and the duration parameters sit in the RTC+B business requirements and NP §8.1.1.2 qualification
  tests, which I did not download.
- **State-of-charge rules at the QSE:**
  - the COP must carry MinSOC, MaxSOC and an hour-beginning planned SOC per hour (NP §3.9.1(5)(h));
  - planned SOC for consecutive hours must be feasible at the ESR's maximum charge and discharge rate (NP §3.9.1(3));
  - MinSOC and MaxSOC must stay within the nameplate operating limits and HBSOC between them (NP §3.9.1(17));
  - real-time telemetry must include MaxSOC, MinSOC, SOC, and the maximum operating charge and discharge power limits, and SOC must stay
    between Min and Max (NP §6.5.5.2(14), (15));
  - the QSE may update HSL and LSL in real time to reflect state-of-charge limits (NP §3.8.5(1));
  - ERCOT operators monitor ESR SOC so ancillary-service responsibilities can be met (NP §6.3.2).
- **Prices after RTC+B:** ERCOT projects wholesale savings above $1 billion a year (ERCOT VP Keith Collins, news release of 5 Dec 2025). Ancillary-service prices were already saturated before go-live: day-ahead RegUp averaged $2.97/MW-h in 2025
  against $282.56 in 2021 (`docs/research/ercot.md` §3.2, citing the IMM 2025 report). Agent 1a's price tables hold the post-RTC+B numbers.
- Shadow-price caps still bound congestion in the energy price: $5,251/MW for base-case and GTC constraints, $4,500 for 345 kV, $3,500
  for 138 kV and $2,800 for 69 kV thermal violations in 2025 (IMM 2025 State of the Market, printed p.62).

## (c) How storage charging is charged: energy and 4CP

- **Energy:** wholesale storage is "wholesale load" settled at the nodal energy price (see (a)). Real-time settlement at a resource node pays or
  charges the QSE the Resource-Node SPP for energy produced, for energy consumed as WSL, and for energy consumed as non-WSL ESR
  charging load (NP §6.6.3.1(1)(a)). The real-time SPP is the time-weighted LMP plus the reliability-deployment adder, floored at -$251/MWh (NP §6.6.1.1).
- **WSL test:** the energy must be "separately metered from all other Facilities" and used to charge a storage technology that later
  generates energy (NP §2 "Wholesale Storage Load"; 16 TAC §25.501(m)(2)).
- **4CP:** a TSP's transmission rate is its cost of service divided by the average ERCOT coincident peak for June to September, "excluding
  the portion of coincident peak demand attributable to wholesale storage load". The TSP tariff "shall not apply to any entity
  engaging in wholesale storage" (16 TAC §25.192(a), (b), (b)(1)). Result: **separately metered storage pays no 4CP transmission charge**.
- **Other load charges:** wholesale storage is "not subject to ERCOT charges and credits associated with ancillary service
  obligations, or other load ratio share or per megawatt-hour based charges and allocations" and not to retail tariffs. It must not buy
  energy to charge during an ERCOT-declared system emergency unless ERCOT directs it (16 TAC §25.501(m)(2)).
- Load-zone prices are computed with WSL and non-WSL ESR charging load excluded from the state-estimator load weights (NP §6.6.1.4).
- **UNVERIFIED:** how "non-WSL ESR charging load" (storage not separately metered) is allocated load-ratio-share charges and 4CP. The
  protocol has a separate settlement line for it (NP §6.6.3.1, "ESR Non-WSL" terms) but I did not follow its allocation formulas.
  Treat behind-the-meter charging as ordinary retail load until checked.
- **Under review:** SB 6 section 6 requires the PUCT to review the 4CP method and to amend rules by **31 Dec 2026**; the IMM argues for
  replacing 4CP (SB 6 enrolled text, section 6; IMM 2025 State of the Market, "The Case Against 4CP", PDF p.91). Do not hard-code 4CP exemption beyond 2026 without checking.

## (d) Connect-and-manage cost split, FIS steps and timeline, who decides upgrades

**Cost split (16 TAC §25.195(f), amended 6 Mar 2024).**
- Generator pays for its step-up transformers and protective devices at the POI (§25.195(f)(1)).
- If the standard generation interconnection agreement (SGIA) was executed **on or before 31 Dec 2025**, the TSP pays for "any new
  transmission facilities" (§25.195(f)(2)).
- If executed **after 31 Dec 2025**, the generator pays "all costs of installing interconnection facilities ... that exceed the allowance"; the TSP pays for transmission system upgrades
  it deems necessary (§25.195(f)(3)). Allowance from 1 Jan 2026 is based on **$14,000,000 (138 kV or less) or $20,000,000 (above 138 kV)** in 2024 dollars, moved yearly by the Q3-to-Q3
  CPI change. An energy storage resource gets **only this allowance**, not load allowances (§25.195(f)(3)(A)-(B)).
- Costs after energisation caused by the generator's own modifications fall on the owner for ten years (§25.195(f)(3)(E)). TSPs report each interconnection's cost to ERCOT, which
  publishes an annual report from April 2027 (§25.195(i)).
- A TSP may ask for a deposit or security for new facilities (§25.195(e)(1)) and a contribution in aid of construction if its tax-exempt status would be impaired (§25.195(e)(2)).
- What "connect-and-manage" means in practice: the generator is not paid for deliverability. Stability limits that can be resolved by re-dispatching generation "are not considered reliability
  violations" and "transmission upgrades ... are implemented when ERCOT economic planning criteria are satisfied" (ERCOT, Use of Generic Transmission Constraints, Jul 2020, §3.1). The risk is curtailment through GTCs and thermal constraints.

**FIS steps (PG §5.3).**
1. Request and fees through RIOO; the Security Screening Study (SS) is a steady-state review; ERCOT can waive it (PG §5.3.1(1)(b)). Under 16 TAC §25.198(c)(6)(A) ERCOT returns screening results within 90 days of the executed study agreement and the data.
2. After SS results the interconnecting entity has **180 days** to request an FIS (PG §5.3.1(5)).
3. FIS request needs the fee, registration data, proof of site control and, for stability studies, the dynamic model (PG §5.3.2). ERCOT names a lead TSP within 10 business days (PG §5.3.2.2(1)). If the entity and the TSP cannot agree the study agreement in 60 days, ERCOT mediates (PG §5.3.2.2(8)).
4. The FIS covers steady-state, short-circuit, dynamic/transient stability and a facility study with cost estimates (PG §5.3.2.4). Costs over $25 M trigger an ERCOT economic study (PG §5.3.3; NP §3.11.6(2)).
5. The final FIS report becomes complete after a 10-business-day review; the entity then has **180 days to sign the SGIA** (PG §5.3.2.5(6), (8)). ERCOT's Quarterly Stability Assessment then sets any new GTC limits before synchronisation (PG §5.3.5; GTC white paper §3.2).
6. Commissioning approvals to energise and synchronise follow (PG §5.5; the GIS report tracks both dates).

**Typical timeline (DERIVED from the Sep 2026 GIS report, storage with an IA signed, n = 217).** Median **20.5 months** from FIS request to IA signed (quartiles 16 and 28);
median 22 months from screening start to IA; median **35.6 months** from IA signed to projected COD. These are biased short because only projects that reached the milestone are counted.
Source: `data/queue_storage.csv` rebuilt from the GIS xlsx (see `notes/phase1-planning.md`).

**Who decides network upgrades.**
- The TSP "must plan, construct, operate, and maintain facilities that are needed to relieve transmission constraints, as recommended by ERCOT and approved by the commission", with a CCN where required (16 TAC §25.195(d)).
- **Any stakeholder may submit a project** to the Regional Planning Group; Tier 1, 2 and 3 projects must be submitted (NP §3.11.4.1(1)-(2)), and "additional upgrades ... that might be cost-effective as a result of new or modified generation may be initiated by any stakeholder" (NP §3.11.6(3)).
- Tiers (NP §3.11.4.3, text of 1 Aug 2026): Tier 1 is $200 M or more; Tier 2 is under $200 M with a CCN; Tier 3 is $50 M to $200 M without a CCN (or 345-kV work above set sizes). Interconnection facilities for a new storage resource are a "neutral" Tier 4 project (§3.11.4.3(1)(f)(iv)). The 2026 RPG decks still label $116 M to $200 M projects "Tier 1", which suggests the old $100 M line was in use until NPRR1274 took effect (**UNVERIFIED**).
- Economic projects must show production-cost savings that equal or exceed the annual revenue requirement (NP §3.11.2, as quoted in the GTC white paper §7).
- The PUCT grants the CCN (16 TAC §25.101).
- **What a battery owner can and cannot pay for:** it pays for its own interconnection facilities above the allowance and its own study fees. It can propose a project to the RPG. It cannot own the network upgrade; the TSP does, and a battery owner contributing to an upgrade needs a negotiated arrangement outside these rules (**UNVERIFIED**: no rule I read allows or forbids a third-party contribution toward a network upgrade other than the CIAC provisions above).

## (e) Large loads and congestion (SB 6, PUCT rule, NOGRR282)

- **Threshold:** "Large Load" is a site with aggregate peak demand of **75 MW or more** (NP §2). SB 6 sets 75 MW in PURA §37.0561(c).
- **SB 6 duties** (enrolled bill, read through a page-summarising tool, so the PURA section numbers are **UNVERIFIED** against the statute): PUCT interconnection standards with a flat study fee of at least $100,000, per-MW financial security, site control, backup-generation disclosure (§37.0561); loads
  interconnected after 31 Dec 2025 must be curtailable in firm load shed (§39.170(a)); ERCOT must procure large-load demand reduction competitively (§39.170(b)-(c)); co-location with an existing generator needs notice, an ERCOT study within 120 days and PUCT action within 60 days (§39.169); large loads contribute to interconnection cost (§35.004(c-1)).
- **PUCT rule 16 TAC §25.194:** adopted in September 2026 per White & Case and Utility Dive (flat $100k fee and $50k/MW security). **UNVERIFIED:** the adopted text and its effective date. The TAC mirror I use does not carry it yet, and I did not find the PUCT order in Project 58481.
- **Batch Zero** (PGRR145/NPRR1325, PUCT-approved 18 Jun 2026 per ERCOT's Sep 2026 Board deck): one system-wide study (PG §9.3.1) with a year-by-year MW allocation for 2028 to 2032 (ERCOT Senate panel deck, 29 Jul 2026, slide 3). ERCOT's conditional classification of 3 Sep 2026 was 204 projects (66.4 GW) as base load and 158 projects (127.9 GW) as studied load; 302.2 GW excluded. The Governor's 3 Aug 2026 directive paused energisation of data centres and crypto sites of 75 MW or more and set a verification report for 10 Dec 2026 (ERCOT Board 14-15 Sep 2026, Item 14).
- **Why it moves congestion:** under the Batch Zero text, SCED caps the energy bid curve of a Provisional Controllable Load Resource when its shift factor on a binding constraint passes a threshold (NP §6.5.7.3(1) as amended by NPRR1188/NPRR1325, pending implementation). Large loads also drive the 765-kV plans and the Panhandle and Rio Grande Valley projects (see `data/planned_projects.csv`, `data/large_loads.csv`). ERCOT scales officer-letter loads to 55.4% of maximum, and data-centre letters a further 49.8% (C&N Dec 2025, p.12).
- **NOGRR282** (ride-through for Large Computational Loads: at least 50% power-electronic load at a 75 MW site): frequency (NOG §2.6.4) and voltage (NOG §2.15, Table A: 0.15 s at under 0.35 pu up to continuous at 0.90 to 1.10 pu) ride-through. It excludes loads energised or contracted on or before **14 Nov 2025**. ERCOT's Board recommended it on 2 Jun 2026 and the PUCT report of 9 Jul 2026 approves it. A battery fleet is useful when a large load trips because load loss raises frequency (see `docs/research/ercot.md` §3.1).

## (f) System strength, grid forming and ride-through for new inverter resources

- **Advanced grid support for ESRs (NOG §2.14, from NOGRR272 and PGRR121, PUCT-approved 6 Nov 2025, effective Dec 2025 per C&N Dec 2025 p.19):** an ESR must meet the PG §6.2 modelling requirements to show it can maintain an internal voltage phasor in the sub-transient to transient timeframe and control it to stay in synchronism, when within the inverter current limit. It applies to an original SGIA **executed on or after 1 Apr 2026**; earlier SGIAs are exempt, but MW added after 1 Apr 2026 must comply (NOG §2.14(1)-(2)). ERCOT's stated reason is that more than 20 GTCs exist and grid-forming ESRs can raise GTC limits (NOGRR272 Board Report).
- **Subsynchronous oscillation:** a generator with an original SGIA on or after **1 Apr 2026** may not connect if one or fewer credible single contingencies would make it radial to a series capacitor (PG §5.2.10(1)).
- **Required POI equipment:** a transmission POI needs a station with breakers able to interrupt fault current, remotely controllable by the transmission operator (PG §5.2.11(1), from NPRR1234/PGRR115, "upon system implementation"; **UNVERIFIED** effective date).
- **Ride-through:** frequency ride-through for transmission-connected IBRs (NOG §2.6.2.1) and voltage ride-through (NOG §2.9.1, "preferred" and "legacy" curves) from NOGRR245 (Board Aug 2024). DESRs have their own sections (NOG §2.6.2.2, §2.9.2).
- **Low system strength:** ERCOT describes it as "control stability", managed by cutting IBR output, adding synchronous condensers or dynamic devices, or reinforcing the grid (GTC white paper §2.4). Six synchronous condensers in West Texas were endorsed in Jan 2024, with the project in service 2027 (C&N Dec 2025, p.8, p.15). The FIS stability study and the Quarterly Stability Assessment turn low strength into GTC limits (PG §5.3.2.4.3; PG §5.3.5). **UNVERIFIED:** I found no numeric short-circuit-ratio minimum in PG §5.3.2.4, NOG §2.14 or the C&N reports. If ERCOT or a TSP applies one it is in the FIS scope agreement, which is not public.
- **Why it matters for siting:** a weak-grid site in the Far West or the Panhandle will see lower GTC limits, and ERCOT added two Far West import GTCs (`I_FW_S`, `I_FW_N`) on 15 Sep 2025 (market notice W-A090425-01; `data/gtc.csv`).

## (g) Public transmission outage reports

- **Public today:** a report of each Generation Resource or ESR forced outage or derate, three days after the operating day (NP §3.1.4.8); the Resource Planned Outage Limit and aggregate approved resource outages, twice daily for 60 months and hourly for seven days (NP §3.1.6.13). These are generation, not transmission.
- **Transmission outage statistics:** NOGRR267 moved the monthly Outage Scheduler statistics (request counts by lead time, by type, and per-TSP percentages) from the MIS Secure Area to the public website (NOG §9.2.3; NOGRR267 Board Report 3 Dec 2024; with NPRR1240, approved by the PUCT on 16 Jan 2025). They give counts and percentages only, with no element names or dates.
- **Not public:** the element-level planned and forced transmission outage list sits in the ERCOT Outage Scheduler in the MIS Secure Area; ECEII-bearing items need a digital certificate with the ICE_M_VIEW_ECEII role (GTC market notices, e.g. W-A090425-01). **UNVERIFIED:** I did not find a public element-level outage file in the ERCOT report catalogue.
- **Indirect public evidence:** SCED binding constraints (NP6-86-CD), day-ahead shadow prices (NP4-191-CD), the IMM monthly reports, and the C&N reports, which flag constraints as outage-related (for example Odessa EHV-Yarbrough and Haine Drive-La Palma, "Planned and Forced Outages", C&N Dec 2025 p.5).
- **Usefulness:** low for forecasting a particular outage; moderate for a seasonal pattern. Spring and autumn maintenance seasons show up in the 5-minute binding-constraint history, which is the better input for the model. Outage data in the CRR auction network models is a secured input (NP §7, CRR Network Model; **UNVERIFIED** posting).

---

## Sources

All files are in `research-data/grid/raw/docs/` (names as saved) and listed in `research-data/grid/manifest.csv`.

| Tag used above | File | URL |
|---|---|---|
| NP §2 | ERCOT_Nodal_Protocols_Sec02_Definitions.docx | https://www.ercot.com/files/docs/2024/06/28/02-080126_Nodal.docx |
| NP §3 | ERCOT_Nodal_Protocols_Sec03_Management_Activities.docx | https://www.ercot.com/files/docs/2025/09/01/03-080126_Nodal.docx |
| NP §4 | ERCOT_Nodal_Protocols_Sec04_Day-Ahead_Operations.docx | https://www.ercot.com/files/docs/2024/06/28/04-080126_Nodal.docx |
| NP §6 | ERCOT_Nodal_Protocols_Sec06_Adjustment_Period_and_Real-Time.docx | https://www.ercot.com/files/docs/2024/06/28/06-082826_Nodal.docx |
| NP §7 | ERCOT_Nodal_Protocols_Sec07_CRR.docx | https://www.ercot.com/files/docs/2021/08/01/07-120525_Nodal.docx |
| NP §22 Att. R | ERCOT_Nodal_Protocols_Sec22_AttR_Identifying_Resource_Nodes.docx | https://www.ercot.com/files/docs/2026/04/01/22R-040126_Nodal.docx |
| PG §3, §4, §5, §9 | ERCOT_Planning_Guide_Sec03/04/05/09 ... .docx | https://www.ercot.com/mktrules/guides/planning/current |
| NOG §2, §3, §11 | ERCOT_Nodal_Operating_Guide_Sec02/03/11 ... .docx | https://www.ercot.com/mktrules/guides/noperating/current |
| ADER Governing Document Phase 3.3 | ERCOT_ADER_Pilot_Governing_Document_Phase_3.3.docx | https://www.ercot.com/files/docs/2026/03/02/ADER-Pilot-Project-Governing-Document-Phase-3.3.docx |
| M-A030226-01 | ERCOT_Market_Notice_M-A030226-01_ADER_limits.html | https://www.ercot.com/services/comm/mkt_notices/M-A030226-01 |
| RTC+B news release | ERCOT_News_Release_2025-12-05_RTC+B_goes_live.html | https://www.ercot.com/news/release/12052025-ercot-goes-live |
| NPRR1282 | ERCOT_NPRR1282_Board_Report_2025-06-24.docx, ERCOT_NPRR1282_PUCT_Report_2025-07-31.docx | https://www.ercot.com/mktrules/issues/NPRR1282 |
| 16 TAC §25.192, §25.195, §25.198, §25.501, §25.101 | 16TAC_25.xxx_..._LII.html | https://www.law.cornell.edu/regulations/texas/16-Tex-Admin-Code-SS-25-195 (and -192, -198, -501, -101) |
| SB 6 (89R) enrolled | not downloaded; read through a page summary | https://capitol.texas.gov/tlodocs/89R/billtext/html/SB00006F.htm |
| PUCT rule 25.194 (adopted Sep 2026) | secondary only | https://www.whitecase.com/insight-alert/puct-sets-financial-commitments-data-center-interconnection-governor-pauses-permit ; https://utilitydive.com/news/texas-puc-adopts-data-center-interconnection-rules/830899 |
| Batch Zero Board deck | ERCOT_Batch_Zero_Update_2026-09-11.pdf | https://www.ercot.com/files/docs/2026/09/11/14-Batch-Zero-Update.pdf |
| NOGRR272 / PGRR121 | ERCOT_NOGRR272_Board_Report_2025-09-22.docx, ERCOT_NOGRR272_PUCT_Report_2025-11-06.docx, ERCOT_PGRR121_Board_Report_2025-09-22.docx | https://www.ercot.com/mktrules/issues/NOGRR272 |
| NOGRR245 | ERCOT_NOGRR245_Board_Report_2024-08-20.docx | https://www.ercot.com/mktrules/issues/NOGRR245 |
| NOGRR282 | ERCOT_NOGRR282_Board_Report_2026-06-02.docx, ERCOT_NOGRR282_PUCT_Report_2026-07-09.docx | https://www.ercot.com/mktrules/issues/NOGRR282 |
| NPRR1234 / PGRR115 | ERCOT_NPRR1234_Board_Report_2025-04-08.docx, ERCOT_PGRR115_Board_Report_2025-04-08.docx | https://www.ercot.com/mktrules/issues/NPRR1234 |
| NOGRR267 / NPRR1240 | ERCOT_NOGRR267_Board_Report_2024-12-03.docx | https://www.ercot.com/mktrules/issues/NOGRR267 ; https://www.ercot.com/mktrules/issues/NPRR1240 |
| GTC white paper | ERCOT_The_Use_of_GTCs_in_ERCOT_2020-07.pdf | https://www.ercot.com/files/docs/2020/11/27/The_Use_of_GTCs_in_ERCOT_July_2020.pdf |
| C&N Dec 2025 | ERCOT_2025_Report_on_Existing_and_Potential_Electric_System_Constraints_and_Needs.pdf | https://www.ercot.com/files/docs/2025/12/23/2025-Report-on-Existing-and-Potential-Electric-System-Constraints-and-Needs.pdf |
| IMM 2025 State of the Market | Potomac_2025_State_of_the_Market_Report_ERCOT.pdf | https://www.potomaceconomics.com/wp-content/uploads/2026/06/2025-State-of-the-Market-Report-for-ERCOT.pdf |
| Far West GTC notice W-A090425-01 | ERCOT_Market_Notice_W-A090425-01_Far_West_GTCs.html | https://www.ercot.com/services/comm/mkt_notices/W-A090425-01 |
