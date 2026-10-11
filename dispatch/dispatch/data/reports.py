"""Registry of the ERCOT reports the platform reads: lake id, MIS reportTypeId, retention, and the parsed datasets they feed.

reportTypeIds were checked against the live MIS catalogue (`/misapp/servlets/IceRepListJsonWS`, 183 public report types)
and the report lists on 10 Oct 2026. Retention is what the MIS list showed that day.

NP3-565-CD, "Seven-Day Load Forecast by Model and Weather Zone", is reportTypeId **14837** (file prefix `LFMODWEATHERNP3565`).
Do not confuse it with 12312, "Seven-Day Load Forecast by Weather Zone" (NP3-561-CD, `LFCWEATHERNP3561`, the in-use model only).
NP4-180-ER, "Historical DAM Load Zone and Hub Prices", is **13060**; NP6-785-ER, the RTM counterpart, is 13061.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Union


@dataclass(frozen=True)
class Report:
    report_id: str                      # id used in the lake path and the catalog
    rtid: int                           # MIS reportTypeId
    title: str
    files: str = "csv"                  # "csv": keep only *_csv.zip (each list holds csv and xml); "all": every zip
    window: str = ""                    # retention seen on 10 Oct 2026
    ingest: bool = True                 # polled by ingest_once / backfill_all
    archive: bool = False               # yearly/bulk archive, refreshed weekly under a new file name
    datasets: tuple = ()                # parsed datasets fed by this report
    emil: Optional[str] = None          # ERCOT Public API product id, where known (used only by the API path)
    source: str = "ercot"


_REPORTS = [
    # --- live CDR reports, the platform's inputs ------------------------------------------------------------------
    Report("NP6-905-CD", 12301, "Settlement Point Prices at Resource Nodes, Hubs and Load Zones (15 min)", window="7 days",
           datasets=("rt_spp",), emil="np6-905-cd"),
    Report("NP4-190-CD", 12331, "DAM Settlement Point Prices", window="~31 days", datasets=("dam_spp",), emil="np4-190-cd"),
    Report("NP4-188-CD", 12329, "DAM Clearing Prices for Capacity (ancillary services)", window="~31 days",
           datasets=("dam_as",), emil="np4-188-cd"),
    Report("NP6-331-CD", 24898, "Real-Time Clearing Prices for Capacity by 15-Minute Settlement Interval", window="7 days",
           datasets=("rt_as",), emil="np6-331-cd"),
    Report("NP6-86-CD", 12302, "SCED Shadow Prices and Binding Transmission Constraints", window="7 days (hourly files)",
           datasets=("sced_shadow",), emil="np6-86-cd"),
    Report("NP6-322-CD", 13114, "SCED System Lambda", window="5 days", datasets=("sced_lambda",), emil="np6-322-cd"),
    Report("NP4-160-SG", 10008, "Settlement Points List and Electrical Buses Mapping", files="all",
           window="3 versions, ~1 month", datasets=("sp_map",), emil="np4-160-sg"),
    Report("NP3-565-CD", 14837, "Seven-Day Load Forecast by Model and Weather Zone", window="7 days (hourly issues)",
           datasets=("load_forecast",), emil="np3-565-cd"),
    # --- free yearly archives (no API key), re-published weekly under a new file name --------------------------------
    Report("HIST-DAM-MCPC", 13091, "Historical DAM Clearing Prices for Capacity (yearly, 2010 to now)", files="all",
           window="no expiry", archive=True, datasets=("dam_as",)),
    Report("HIST-RTM-MCPC-SCED", 25569, "Historical RTM Clearing Prices for Capacity by SCED Interval (RTC+B, from 5 Dec 2025)",
           files="all", window="no expiry", archive=True, datasets=()),
    Report("HIST-RTM-MCPC-15", 25570, "Historical RTM Clearing Prices for Capacity by 15-Minute Settlement Interval (RTC+B)",
           files="all", window="no expiry", archive=True, datasets=("rt_as",)),
    Report("NP6-785-ER", 13061, "Historical RTM Load Zone and Hub Prices (yearly, 2011 to now)", files="all",
           window="no expiry", archive=True, datasets=("rt_spp_hist",), emil="np6-785-er"),
    Report("NP4-180-ER", 13060, "Historical DAM Load Zone and Hub Prices (yearly, 2010 to now)", files="all",
           window="no expiry", archive=True, datasets=("dam_spp_hist",), emil="np4-180-er"),
    # --- adopted from research-data/grid/raw (research agent 1a) and refreshed by ingest; not parsed here ----------------
    Report("NP6-787-CD", 11485, "LMPs by Electrical Bus (per SCED run)", window="5 days", ingest=False),
    Report("NP6-788-CD", 12300, "LMPs by Resource Nodes, Load Zones and Trading Hubs (per SCED run)", window="5 days", ingest=False),
    Report("NP6-332-CD", 24891, "Real-Time Clearing Prices for Capacity by SCED Interval", window="7 days", ingest=False),
    Report("NP6-323-CD", 13221, "Real-Time Price Adders by SCED Interval", window="5 days", ingest=False),
    Report("NP4-183-CD", 12328, "DAM Hourly LMPs (by electrical bus)", window="~31 days", ingest=False),
    Report("NP4-191-CD", 12332, "DAM Shadow Prices", window="~31 days", ingest=False),
    Report("NP4-523-CD", 13113, "DAM System Lambda", window="~31 days", ingest=False),
    Report("EB-TO-HUB", 10011, "Electrical Bus to Hub List", files="all", window="current + 2", ingest=False),
    # --- the 60-day disclosure reports D1b stages under DISPATCH_HOME/staging/ercot-60day (adopted by data.adopt; parsed: esr_sced, dam_spp_60d)
    Report("60D-SCED", 13052, "60-Day SCED Disclosure Reports (resource level: telemetry, base points, ESR state of charge)", files="all",
           window="60 days, one zip per operating day", ingest=False, datasets=("esr_sced",)),
    Report("60D-DAM", 13051, "60-Day DAM Disclosure Reports (resource level: awards, bids, offers)", files="all",
           window="60 days, one zip per operating day", ingest=False, datasets=("dam_spp_60d",)),
]

REPORTS: dict[str, Report] = {r.report_id: r for r in _REPORTS}
BY_RTID: dict[int, Report] = {r.rtid: r for r in _REPORTS}
INGEST_IDS = tuple(r.report_id for r in _REPORTS if r.ingest and not r.archive)       # polled every cycle
ARCHIVE_IDS = tuple(r.report_id for r in _REPORTS if r.archive)                        # refreshed weekly
ALL_IDS = tuple(REPORTS)


def canonical_id(report: Union[str, int]) -> str:
    """'NP6-905-CD' -> itself; 12301 / '12301' (the MIS reportTypeId) -> 'NP6-905-CD'; anything else is returned as given
    (non-ERCOT sources such as 'DHHNGSP' or 'GHCND' have their own ids)."""
    if isinstance(report, int) and report in BY_RTID:
        return BY_RTID[report].report_id
    if isinstance(report, str):
        s = report.strip()
        if s in REPORTS:
            return s
        if s.isdigit() and int(s) in BY_RTID:
            return BY_RTID[int(s)].report_id
        return s
    return str(report)


def get(report: Union[str, int]) -> Report:
    rid = canonical_id(report)
    if rid not in REPORTS:
        raise KeyError(f"unknown ERCOT report {report!r}; known: {', '.join(REPORTS)}")
    return REPORTS[rid]


def reports_for_dataset(dataset: str) -> tuple:
    """Report ids that feed a parsed dataset."""
    return tuple(r.report_id for r in _REPORTS if dataset in r.datasets)
