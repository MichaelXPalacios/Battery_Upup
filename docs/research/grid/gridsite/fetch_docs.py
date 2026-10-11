"""Phase 1b downloader: planning, queue, constraint and rules documents (public, no login, no keys).

Everything lands in research-data/grid/raw/docs/ and is recorded in the shared manifest (method `mis` for ERCOT MIS
report files, `web` for everything else). A file already in the manifest AND on disk is never fetched again.

Run:  python run_grid.py fetch-docs [key ...]   (no key = every entry of WEB, WEB_EXTRA and MIS; `fetch-docs list` prints the
      keys; `fetch-docs rpg` downloads this year's RPG meeting decks into raw/docs/rpg/)

Downloaded content is treated as data. Nothing here logs in, creates an account or sends a credential.
"""
import re
import sys
import time

import requests

from . import manifest
from .paths import RAW, USER_AGENT

DOCS = RAW / "docs"
MIS_LIST = "https://www.ercot.com/misapp/GetReports.do?reportTypeId={rid}"
MIS_FILE = "https://www.ercot.com/misdownload/servlets/mirDownload?doclookupId={doc}"
PAUSE_S = 1.0          # ASSUMPTION: polite delay between requests to the same host
TIMEOUT_S = 180        # ASSUMPTION: generous timeout for the 9 MB PDFs
HEADERS = {"User-Agent": USER_AGENT}

# --- plain web documents -------------------------------------------------------------------------------------------
# key -> (source label, report id / short tag, url, file name on disk)
WEB = {
    "tpit": ("ERCOT planning page (gridinfo/planning)", "TPIT",
             "https://www.ercot.com/files/docs/2022/03/02/ERCOT-July-Ad-Hoc-TPIT-No-Cost-071326-UPDATE.xlsx",
             "TPIT_Transmission_Project_and_Information_Tracking_2026-07.xlsx"),
    "cn2025": ("ERCOT", "NP3-773-M",
               "https://www.ercot.com/files/docs/2025/12/23/2025-Report-on-Existing-and-Potential-Electric-System-Constraints-and-Needs.pdf",
               "ERCOT_2025_Report_on_Existing_and_Potential_Electric_System_Constraints_and_Needs.pdf"),
    "cn2024": ("ERCOT", "NP3-773-M",
               "https://www.ercot.com/files/docs/2024/12/20/2024-report-on-existing-and-potential-electric-system-constraints-and-needs.pdf",
               "ERCOT_2024_Report_on_Existing_and_Potential_Electric_System_Constraints_and_Needs.pdf"),
    "rtp2024_765": ("ERCOT", "PG7-048-M",
                    "https://www.ercot.com/files/docs/2025/01/27/2024-regional-transmission-plan-rtp-345-kv-plan-and-texas-765-kv-strategic-transmission-expans.pdf",
                    "ERCOT_2024_RTP_345kV_Plan_and_765kV_STEP.pdf"),
    "rtp2025_stability": ("ERCOT", "RTP2025-StabilityLimits",
                          "https://www.ercot.com/files/docs/2025/07/29/2025-Regional-Transmission-Plan-RTP-Economic-Study-Stability-Interface-Limits-Update.pdf",
                          "ERCOT_2025_RTP_Economic_Study_Stability_Interface_Limits_Update.pdf"),
    "permian_study": ("ERCOT", "PermianBasinReliabilityPlan",
                      "https://www.ercot.com/files/docs/2024/07/25/2024-ERCOT-Permian-Basin-Reliability-Plan-Study-Report-and-Addendum.zip",
                      "ERCOT_2024_Permian_Basin_Reliability_Plan_Study_Report_and_Addendum.zip"),
    "batchzero_sep2026": ("ERCOT Board", "BatchZero-Update",
                          "https://www.ercot.com/files/docs/2026/09/11/14-Batch-Zero-Update.pdf",
                          "ERCOT_Batch_Zero_Update_2026-09-11.pdf"),
    "planning_guide": ("ERCOT", "PlanningGuide",
                       "https://www.ercot.com/files/docs/2025/11/26/December-1-2025-Planning-Guide.pdf",
                       "ERCOT_Planning_Guide_2025-12-01.pdf"),
    "som2025": ("Potomac Economics (IMM)", "SOM2025",
                "https://www.potomaceconomics.com/wp-content/uploads/2026/06/2025-State-of-the-Market-Report-for-ERCOT.pdf",
                "Potomac_2025_State_of_the_Market_Report_ERCOT.pdf"),
}

# --- ERCOT MIS report types (report id, pattern selecting the file(s) to take, how many newest to take) ------------
MIS = {
    "gis": ("PG7-200-ER", 15933, re.compile(r"GIS_Report_[A-Za-z]+\d{4}\.xlsx$"), 1),
    "gis_colocated": ("PG7-200-ER", 15933, re.compile(r"Co-located_Battery_Identification_Report_.*\.xlsx$"), 1),
    "rtp2025": ("PG7-048-M", 24735, re.compile(r"2025_Regional_Transmission_Plan_Report_Public\.zip$"), 1),
}


def _get(url, stream_to=None):
    for attempt in range(3):
        try:
            r = requests.get(url, headers=HEADERS, timeout=TIMEOUT_S)
            if r.status_code == 200:
                return r
            print(f"  HTTP {r.status_code} for {url}")
        except requests.RequestException as e:
            print(f"  retry {attempt + 1}: {e}")
        time.sleep(2 * (attempt + 1))
    return None


def _save(source, rid, url, fname, method, force=False):
    DOCS.mkdir(parents=True, exist_ok=True)
    path = DOCS / fname
    if path.exists() and manifest.already_have(url) and not force:
        print(f"  have  {fname}")
        return path
    r = _get(url)
    if r is None:
        print(f"  FAILED {url}")
        return None
    path.write_bytes(r.content)
    manifest.record(source, rid, url, path, method)
    print(f"  got   {fname}  ({len(r.content):,} bytes)")
    time.sleep(PAUSE_S)
    return path


def mis_listing(report_type_id):
    """[(file name, doclookupId)] newest first, parsed from the public MIS listing page."""
    r = _get(MIS_LIST.format(rid=report_type_id))
    if r is None:
        return []
    out = []
    for name, rest in re.findall(r"<td class='labelOptional_ind'>([^<]+)</td>(.*?)</tr>", r.text, re.S):
        m = re.search(r"doclookupId=(\d+)", rest)
        if m:
            out.append((name.strip(), m.group(1)))
    return out


def fetch_mis(key):
    rid, rtype, pat, n = MIS[key]
    got = 0
    for name, doc in mis_listing(rtype):
        if pat.search(name):
            _save("ERCOT MIS", rid, MIS_FILE.format(doc=doc), name, "mis")
            got += 1
            if got >= n:
                break


# documents added while researching (IMM monthly reports, rule texts, revision requests, GTC notices, unit-cost guides,
# large-load decks, PUCT orders). Everything is listed here so a rerun reproduces the same set.
WEB_EXTRA = {
    "som2024": ("Potomac Economics (IMM)", "SOM2024",
                "https://potomaceconomics.com/wp-content/uploads/2025/06/2024-State-of-the-Market-Report.pdf",
                "Potomac_2024_State_of_the_Market_Report_ERCOT.pdf"),
    "nodal_monthly_2025_12": ("Potomac Economics (IMM)", "NodalMonthly2025-12",
                              "https://www.potomaceconomics.com/wp-content/uploads/2026/01/2025-12_Nodal_Monthly_Report.pdf",
                              "Potomac_2025-12_Nodal_Monthly_Report.pdf"),
    "nodal_monthly_2024_12": ("Potomac Economics (IMM)", "NodalMonthly2024-12",
                              "https://www.potomaceconomics.com/wp-content/uploads/2025/01/2024-12_Nodal_Monthly_Report.pdf",
                              "Potomac_2024-12_Nodal_Monthly_Report.pdf"),
}

# ERCOT rule documents (docx), links taken from the "current" pages on 10 Oct 2026:
#   https://www.ercot.com/mktrules/nprotocols/current   https://www.ercot.com/mktrules/guides/planning/current
#   https://www.ercot.com/mktrules/guides/noperating/current
_RULES = {
    "proto_s02": ("Nodal Protocols", "Sec2-Definitions", "https://www.ercot.com/files/docs/2024/06/28/02-080126_Nodal.docx", "ERCOT_Nodal_Protocols_Sec02_Definitions.docx"),
    "proto_s03": ("Nodal Protocols", "Sec3-Management", "https://www.ercot.com/files/docs/2025/09/01/03-080126_Nodal.docx", "ERCOT_Nodal_Protocols_Sec03_Management_Activities.docx"),
    "proto_s04": ("Nodal Protocols", "Sec4-DayAhead", "https://www.ercot.com/files/docs/2024/06/28/04-080126_Nodal.docx", "ERCOT_Nodal_Protocols_Sec04_Day-Ahead_Operations.docx"),
    "proto_s05": ("Nodal Protocols", "Sec5-TSA-RUC", "https://www.ercot.com/files/docs/2024/06/28/05-080126_Nodal.docx", "ERCOT_Nodal_Protocols_Sec05_TSA_and_RUC.docx"),
    "proto_s06": ("Nodal Protocols", "Sec6-RealTime", "https://www.ercot.com/files/docs/2024/06/28/06-082826_Nodal.docx", "ERCOT_Nodal_Protocols_Sec06_Adjustment_Period_and_Real-Time.docx"),
    "proto_s07": ("Nodal Protocols", "Sec7-CRR", "https://www.ercot.com/files/docs/2021/08/01/07-120525_Nodal.docx", "ERCOT_Nodal_Protocols_Sec07_CRR.docx"),
    "proto_s09": ("Nodal Protocols", "Sec9-Settlement", "https://www.ercot.com/files/docs/2023/06/01/09-080126_Nodal.docx", "ERCOT_Nodal_Protocols_Sec09_Settlement_and_Billing.docx"),
    "proto_s16": ("Nodal Protocols", "Sec16-Registration", "https://www.ercot.com/files/docs/2024/07/01/16-080126_Nodal.docx", "ERCOT_Nodal_Protocols_Sec16_Registration.docx"),
    "proto_s22p": ("Nodal Protocols", "Sec22AttP-ShadowPriceCaps", "https://www.ercot.com/files/docs/2024/04/30/22P-120525_Nodal.docx", "ERCOT_Nodal_Protocols_Sec22_AttP_Max_Shadow_Prices.docx"),
    "pg_s03": ("Planning Guide", "PG-Sec3", "https://www.ercot.com/files/docs/2025/12/01/03-071126.docx", "ERCOT_Planning_Guide_Sec03_Regional_Planning.docx"),
    "pg_s04": ("Planning Guide", "PG-Sec4", "https://www.ercot.com/files/docs/2022/06/01/04-121525.docx", "ERCOT_Planning_Guide_Sec04_Transmission_Planning_Criteria.docx"),
    "pg_s05": ("Planning Guide", "PG-Sec5", "https://www.ercot.com/files/docs/2025/12/01/05-080126.docx", "ERCOT_Planning_Guide_Sec05_Generator_Interconnection.docx"),
    "pg_s09": ("Planning Guide", "PG-Sec9", "https://www.ercot.com/files/docs/2011/04/28/09-071126.docx", "ERCOT_Planning_Guide_Sec09_Large_Load_Interconnection.docx"),
    "nog_s02": ("Nodal Operating Guide", "NOG-Sec2", "https://www.ercot.com/files/docs/2022/11/01/02-080126.docx", "ERCOT_Nodal_Operating_Guide_Sec02_System_Operations.docx"),
    "nog_s03": ("Nodal Operating Guide", "NOG-Sec3", "https://www.ercot.com/files/docs/2022/03/01/03-120525.docx", "ERCOT_Nodal_Operating_Guide_Sec03_Responsibilities.docx"),
    "nog_s11": ("Nodal Operating Guide", "NOG-Sec11", "https://www.ercot.com/files/docs/2020/10/01/11-080126.docx", "ERCOT_Nodal_Operating_Guide_Sec11_CMP_and_RAS.docx"),
}
WEB_EXTRA.update(_RULES)

_RULES2 = {
    "proto_s22r": ("Nodal Protocols", "Sec22AttR-ResourceNodes", "https://www.ercot.com/files/docs/2026/04/01/22R-040126_Nodal.docx", "ERCOT_Nodal_Protocols_Sec22_AttR_Identifying_Resource_Nodes.docx"),
    "ader_gov_33": ("ERCOT ADER pilot page", "ADER-Phase3.3", "https://www.ercot.com/files/docs/2026/03/02/ADER-Pilot-Project-Governing-Document-Phase-3.3.docx", "ERCOT_ADER_Pilot_Governing_Document_Phase_3.3.docx"),
    "ader_gov_32": ("ERCOT ADER pilot page", "ADER-Phase3.2", "https://www.ercot.com/files/docs/2026/06/03/ADER-Pilot-Project-Governing-DocumentPhase-3.2.docx", "ERCOT_ADER_Pilot_Governing_Document_Phase_3.2.docx"),
    "ader_mkt_notice_limits": ("ERCOT market notice", "M-A030226-01", "https://www.ercot.com/services/comm/mkt_notices/M-A030226-01", "ERCOT_Market_Notice_M-A030226-01_ADER_limits.html"),
}
WEB_EXTRA.update(_RULES2)

_COSTS = {
    "miso_tcg_mtep24": ("MISO", "TCEG-MTEP24", "https://cdn.misoenergy.org/20240501%20PSC%20Item%2004%20MISO%20Transmission%20Cost%20Estimation%20Guide%20for%20MTEP24632680.pdf", "MISO_Transmission_Cost_Estimation_Guide_MTEP24.pdf"),
    "miso_tcg_mtep25_redline": ("MISO", "TCEG-MTEP25-redline", "https://cdn.misoenergy.org/20250306%20PSC%20Item%2004d%20Transmission%20Cost%20Estimation%20Guide%20for%20MTEP25_Redline682745.pdf", "MISO_Transmission_Cost_Estimation_Guide_MTEP25_redline.pdf"),
    "miso_tcg_mtep26_slides": ("MISO", "TCEG-MTEP26-slides", "https://cdn.misoenergy.org/20260311%20PSC%20Item%2004%20Transmission%20Cost%20Estimation%20Guide%20for%20MTEP26_Presentation744889.pdf", "MISO_Transmission_Cost_Estimation_Guide_MTEP26_slides.pdf"),
}
WEB_EXTRA.update(_COSTS)

_COSTS2 = {
    "doe_lpt_2024": ("DOE", "LPT-Resilience-2024", "https://www.energy.gov/sites/default/files/2024-10/EXEC-2022-001242%20-%20Large%20Power%20Transformer%20Resilience%20Report%20signed%20by%20Secretary%20Granholm%20on%207-10-24.pdf", "DOE_Large_Power_Transformer_Resilience_Report_2024-07.pdf"),
    "nlr_lpt_2026": ("NLR (formerly NREL)", "LPT-Gap-Analysis-2026", "https://docs.nlr.gov/docs/fy26osti/96742.pdf", "NLR_Large_Power_Transformer_Supply_Chain_Gap_Analysis_2026.pdf"),
    "doe_dlr_2019": ("DOE", "DLR-Congressional-2019", "https://www.energy.gov/sites/prod/files/2019/08/f66/Congressional_DLR_Report_June2019_final_508_0.pdf", "DOE_Dynamic_Line_Rating_Report_to_Congress_2019-06.pdf"),
}
WEB_EXTRA.update(_COSTS2)

_GTC = {
    "gtc_whitepaper_2020": ("ERCOT", "Use-of-GTCs-2020", "https://www.ercot.com/files/docs/2020/11/27/The_Use_of_GTCs_in_ERCOT_July_2020.pdf", "ERCOT_The_Use_of_GTCs_in_ERCOT_2020-07.pdf"),
    "gtc_ros_2024": ("ERCOT ROS", "GTC-Update-ROS-2024-07", "https://ercot.com/files/docs/2024/07/10/05-gtcupdate_ros_july2024.pdf", "ERCOT_GTC_Update_ROS_2024-07.pdf"),
    "gtc_notice_valley_2026": ("ERCOT market notice", "W-A091426-01", "https://www.ercot.com/services/comm/mkt_notices/W-A091426-01", "ERCOT_Market_Notice_W-A091426-01_Greater_Valley_GTC.html"),
    "gtc_notice_valley_2025": ("ERCOT market notice", "W-A062625-01", "https://www.ercot.com/services/comm/mkt_notices/W-A062625-01", "ERCOT_Market_Notice_W-A062625-01_Greater_Valley_GTC.html"),
    "gtc_notice_valley_2024": ("ERCOT market notice", "M-B062824-01", "https://www.ercot.com/services/comm/mkt_notices/M-B062824-01", "ERCOT_Market_Notice_M-B062824-01_Valley_GTC.html"),
    "gtc_notice_pnhndl_2024": ("ERCOT market notice", "W-A022124-01", "https://www.ercot.com/services/comm/mkt_notices/W-A022124-01", "ERCOT_Market_Notice_W-A022124-01_Panhandle_GTC.html"),
}
WEB_EXTRA.update(_GTC)

_LL = {
    "ll_tac_2026_08": ("ERCOT TAC 26 Aug 2026", "TAC-LargeLoadIssues", "https://www.ercot.com/files/docs/2026/08/25/10.-Large-Load-Issues.zip", "ERCOT_TAC_2026-08-26_10_Large_Load_Issues.zip"),
    "ll_senate_2026_04": ("ERCOT Senate hearing", "LL-Update-2026-04", "https://www.ercot.com/files/docs/2026/04/01/ERCOT_LargeLoad_Update_April2026_B-C_-Hearing.pdf", "ERCOT_LargeLoad_Update_2026-04_Senate_B-C_Hearing.pdf"),
    "ll_board_2025_12": ("ERCOT Board 2 Dec 2025", "SysPlanning-2025-12", "https://www.ercot.com/files/docs/2025/12/02/16.2-System-Planning-and-Weatherization-Update_Revised.pdf", "ERCOT_Board_2025-12-02_System_Planning_and_Weatherization_Update.pdf"),
    "ll_senate_2026_07": ("ERCOT Senate panel 29 Jul 2026", "Senate-Panel1-2026-07", "https://www.ercot.com/files/docs/2026/07/29/ERCOT-Senate-July-29-Panel-1-Assessing-The-Grid.pdf", "ERCOT_Senate_2026-07-29_Panel1_Assessing_The_Grid.pdf"),
}
WEB_EXTRA.update(_LL)

WEB_EXTRA["ll_tac_reports_2026_08"] = ("ERCOT TAC 26 Aug 2026", "TAC-ERCOT-Reports", "https://www.ercot.com/files/docs/2026/08/26/11.-ERCOT-Reports.zip", "ERCOT_TAC_2026-08-26_11_ERCOT_Reports.zip")


# --- RPG (Regional Planning Group) meeting documents for the current year -------------------------------------------
RPG_INDEX = "https://www.ercot.com/committees/other/rpg"
RPG_DIR = DOCS / "rpg"


def fetch_rpg():
    """Scrape the RPG index page for this year's meeting pages and download every PDF linked from them.

    The project recommendation / status-update decks carry each Tier 1-3 project's cost estimate, in-service date and
    CCN need, which the TPIT 'no cost' file leaves out. Files go to raw/docs/rpg/ and into the manifest."""
    RPG_DIR.mkdir(parents=True, exist_ok=True)
    r = _get(RPG_INDEX)
    if r is None:
        return
    meetings = sorted(set(re.findall(r"https://www\.ercot\.com/calendar/\d{8}-RPG[^\"']*", r.text)))
    seen = {}
    for m in meetings:
        page = _get(m)
        if page is None:
            continue
        for h, t in re.findall(r'<a [^>]*href="(https://www\.ercot\.com/files/docs/[^"]+\.pdf)"[^>]*>\s*(.{0,250}?)\s*</a>', page.text, re.S):
            seen.setdefault(h, m.rsplit("/", 1)[1])
        time.sleep(PAUSE_S)
    print(f"  {len(meetings)} RPG meetings, {len(seen)} PDFs")
    for url, meeting in seen.items():
        fname = f"{meeting[:8]}_{url.rsplit('/', 1)[1]}"
        path = RPG_DIR / fname
        if path.exists() and manifest.already_have(url):
            continue
        rr = _get(url)
        if rr is None:
            continue
        path.write_bytes(rr.content)
        manifest.record("ERCOT RPG meeting " + meeting, "RPG", url, path, "web")
        time.sleep(0.3)

WEB_EXTRA["gtc_notice_farwest_2025"] = ("ERCOT market notice", "W-A090425-01", "https://www.ercot.com/services/comm/mkt_notices/W-A090425-01", "ERCOT_Market_Notice_W-A090425-01_Far_West_GTCs.html")

WEB_EXTRA["iurc_att_dlr"] = ("Indiana IURC (Duke Energy Indiana comments, cites EPRI)", "ATT-DLR-cost", "https://secure.in.gov/iurc/files/ATT-Report_Duke-Energy-Indiana-Comments_Stakeholder.pdf", "IURC_ATT_Report_Duke_Energy_Indiana_Comments.pdf")


def fetch_docs(*keys):
    if keys and keys[0] == "list":
        for k in list(WEB) + list(MIS) + list(WEB_EXTRA):
            print(k)
        return
    if keys and keys[0] == "rpg":
        fetch_rpg()
        return
    want = set(keys)
    for k, (src, rid, url, fname) in {**WEB, **WEB_EXTRA}.items():
        if not want or k in want:
            print(k)
            _save(src, rid, url, fname, "web")
    for k in MIS:
        if not want or k in want:
            print(k)
            fetch_mis(k)


if __name__ == "__main__":
    fetch_docs(*sys.argv[1:])


# RTC+B (NPRR1010 and NPRR1282), added while writing notes/rules.md
WEB_EXTRA.update({
    "nprr1282_board": ("ERCOT NPRR1282", "NPRR1282", "https://www.ercot.com/files/docs/2025/06/24/1282NPRR-19-Board-Report-062425.docx", "ERCOT_NPRR1282_Board_Report_2025-06-24.docx"),
    "nprr1282_text": ("ERCOT NPRR1282", "NPRR1282", "https://www.ercot.com/files/docs/2025/04/29/1282NPRR-01-Ancillary-Service-Duration-Under-Real-Time-Co-optimization-042925.docx", "ERCOT_NPRR1282_Text_2025-04-29.docx"),
    "nprr1010_board": ("ERCOT NPRR1010", "NPRR1010", "https://www.ercot.com/files/docs/2020/12/09/1010NPRR-12_Board_Report_120820.docx", "ERCOT_NPRR1010_Board_Report_2020-12-08.docx"),
    "rtcb_golive_release": ("ERCOT news release", "RTC+B-golive", "https://www.ercot.com/news/release/12052025-ercot-goes-live", "ERCOT_News_Release_2025-12-05_RTC+B_goes_live.html"),
})
WEB_EXTRA["nprr1282_puct"] = ("ERCOT NPRR1282", "NPRR1282", "https://www.ercot.com/files/docs/2025/08/04/1282NPRR-20-PUCT-Report-073125.docx", "ERCOT_NPRR1282_PUCT_Report_2025-07-31.docx")

# 16 TAC rules (Cornell LII mirror of the Texas Administrative Code; the Secretary of State site has moved)
for _rl, _nm in (("192", "Transmission_Rates"), ("195", "Terms_and_Conditions_for_Transmission_Service"), ("501", "Wholesale_Market_Design_ERCOT"),
                 ("211", "Interconnection_of_OnSite_DG")):
    WEB_EXTRA[f"tac_25_{_rl}"] = ("Cornell LII, 16 TAC", f"16TAC25.{_rl}",
                                  f"https://www.law.cornell.edu/regulations/texas/16-Tex-Admin-Code-SS-25-{_rl}",
                                  f"16TAC_25.{_rl}_{_nm}_LII.html")

# revision-request Board/PUCT reports used for notes/rules.md (system strength, ride-through, large loads)
for _k, _u, _f in (
    ("nogrr272_board", "https://www.ercot.com/files/docs/2025/09/24/272NOGRR-30-Board-Report-092225.docx", "ERCOT_NOGRR272_Board_Report_2025-09-22.docx"),
    ("nogrr272_puct", "https://www.ercot.com/files/docs/2025/11/10/272NOGRR-31-PUCT-Report-110625.docx", "ERCOT_NOGRR272_PUCT_Report_2025-11-06.docx"),
    ("pgrr121_board", "https://www.ercot.com/files/docs/2025/09/24/121PGRR-14-Board-Report-092225.docx", "ERCOT_PGRR121_Board_Report_2025-09-22.docx"),
    ("nogrr282_board", "https://www.ercot.com/files/docs/2026/06/03/282NOGRR-33-Board-Report-060226.docx", "ERCOT_NOGRR282_Board_Report_2026-06-02.docx"),
    ("nogrr282_puct", "https://www.ercot.com/files/docs/2026/07/11/282NOGRR-45-PUCT-Report-070926.docx", "ERCOT_NOGRR282_PUCT_Report_2026-07-09.docx"),
    ("nogrr245_board", "https://www.ercot.com/files/docs/2024/08/22/245NOGRR-110%20Board%20Report%20082024_1.docx", "ERCOT_NOGRR245_Board_Report_2024-08-20.docx"),
    ("nprr1234_board", "https://www.ercot.com/files/docs/2025/04/08/1234NPRR-25-Board-Report-040825.docx", "ERCOT_NPRR1234_Board_Report_2025-04-08.docx"),
    ("pgrr115_board", "https://www.ercot.com/files/docs/2025/04/08/115PGRR-29-Board-Report-040825.docx", "ERCOT_PGRR115_Board_Report_2025-04-08.docx"),
):
    WEB_EXTRA[_k] = ("ERCOT revision request", _k, _u, _f)
WEB_EXTRA["nogrr267_board"] = ("ERCOT NOGRR267", "NOGRR267", "https://www.ercot.com/files/docs/2024/12/05/267NOGRR-13%20Board%20Report%20120324.docx", "ERCOT_NOGRR267_Board_Report_2024-12-03.docx")
WEB_EXTRA["nogrr267_puct"] = ("ERCOT NOGRR267", "NOGRR267", "https://www.ercot.com/files/docs/2025/01/22/267NOGRR-14%20PUCT%20Report%20011625.docx", "ERCOT_NOGRR267_PUCT_Report_2025-01-16.docx")
WEB_EXTRA["nogrr267_text"] = ("ERCOT NOGRR267", "NOGRR267", "https://www.ercot.com/files/docs/2024/07/02/267NOGRR-01%20Related%20to%20NPRR1240%20Access%20to%20Transmission%20Planning%20Information%20070224.docx", "ERCOT_NOGRR267_Text_2024-07-02.docx")
for _rl, _nm in (("198", "Initiating_Transmission_Service"), ("101", "Certification_Criteria_CCN"), ("200", "Load_Shedding_Curtailments_Redispatch")):
    WEB_EXTRA[f"tac_25_{_rl}"] = ("Cornell LII, 16 TAC", f"16TAC25.{_rl}",
                                  f"https://www.law.cornell.edu/regulations/texas/16-Tex-Admin-Code-SS-25-{_rl}",
                                  f"16TAC_25.{_rl}_{_nm}_LII.html")

# PUCT Interchange documents (public PDFs): Permian Basin Reliability Plan, Project 55718
WEB_EXTRA["puct_55718_order2"] = ("PUCT Interchange Project 55718", "55718-doc109", "https://interchange.puc.texas.gov/Documents/55718_109_1492424.PDF", "PUCT_55718_Second_Order_Approving_Permian_Basin_Reliability_Plan.pdf")
