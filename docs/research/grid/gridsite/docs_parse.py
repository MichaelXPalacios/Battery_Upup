"""Phase 1b parsers: build the planning, queue, constraint and unit-cost tables from the downloaded documents.

Inputs live in research-data/grid/raw/docs/ (see fetch_docs.py). Outputs go to docs/research/grid/data/.

Every table gets a sidecar `<name>.labels.csv` (column,label,source,note) with label in REAL / DERIVED / SIM / ASSUMPTION.
Hand-transcribed PDF values live in small CSVs under data/ (transcribed_*.csv) with the document and page for each row;
this module reads them, it never invents numbers. Unverified constants are named values at the top of this file.

Run:  python run_grid.py parse-docs [table ...]     tables: queue planned large_loads constraints gtc unit_costs
"""
import csv
import glob
import io
import pathlib
import re
import sys
import zipfile
from datetime import date, datetime

import openpyxl

from .paths import RAW, REPO_DATA

DOCS = RAW / "docs"

# --- constants (label, source) -----------------------------------------------------------------------------------
SCED_INTERVALS_PER_HOUR = 12      # DERIVED: SCED runs every 5 minutes, so IMM "SCED intervals" / 12 = binding hours
PLANNED_FIRST_YEAR = 2026         # ASSUMPTION: first in-service year counted as "planned" in summaries
PLANNED_LAST_YEAR = 2031          # ASSUMPTION: last in-service year counted (RTP six-year horizon)
STORAGE_TECH_CODES = {"BA", "EN", "CE"}   # REAL: ERCOT GIS acronyms tab (BA battery, EN energy storage, CE compressed air)

LABEL_NOTE = {"REAL": "copied or transcribed from the named source",
              "DERIVED": "computed here from REAL values",
              "SIM": "simulation output",
              "ASSUMPTION": "analyst assumption, not from a source"}


# --- small helpers -----------------------------------------------------------------------------------------------
def _glob1(pattern):
    hits = sorted(glob.glob(str(DOCS / pattern)))
    if not hits:
        raise FileNotFoundError(f"{pattern} not in {DOCS}; run `fetch-docs` first")
    return hits[-1]


def _iso(v):
    if isinstance(v, datetime):
        return v.date().isoformat() if v.year > 1901 else ""
    if isinstance(v, date):
        return v.isoformat()
    return "" if v is None else str(v)


def _clean(v):
    if v is None:
        return ""
    s = str(v).replace("_x000D_", " ").replace("\r", " ").replace("\xa0", " ")
    return re.sub(r"\s+", " ", s).strip()


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def write_csv(name, rows, columns, labels):
    """Write data/<name>.csv and data/<name>.labels.csv. labels: {column: (label, source, note)}."""
    REPO_DATA.mkdir(parents=True, exist_ok=True)
    path = REPO_DATA / f"{name}.csv"
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=columns, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow({c: ("" if r.get(c) is None else r.get(c)) for c in columns})
    missing = [c for c in columns if c not in labels]
    if missing:
        raise ValueError(f"{name}: no label for {missing}")
    with open(REPO_DATA / f"{name}.labels.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["column", "label", "source", "note"])
        for c in columns:
            lab, src, note = labels[c]
            assert lab in LABEL_NOTE, lab
            w.writerow([c, lab, src, note])
    print(f"  wrote {path.name}: {len(rows)} rows")
    return path


def read_csv(name):
    with open(REPO_DATA / f"{name}.csv", newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def docx_text(path):
    """Plain text of a .docx (zip of xml); used to quote and check rule documents."""
    import html
    x = zipfile.ZipFile(path).read("word/document.xml").decode("utf8", "ignore")
    x = re.sub(r"</w:p>", "\n", x)
    x = re.sub(r"<w:tab/>", "\t", x)
    return html.unescape(re.sub(r"<[^>]+>", "", x))


# ===================================================================================================================
# 1. Queue: ERCOT Generation Interconnection Status report (PG7-200-ER)
# ===================================================================================================================
QUEUE_COLS = ["inr", "project_name", "tech", "mw", "county", "poi_text", "zone", "status", "projected_cod",
              "fuel_code", "tech_code", "gen_class", "colocation", "is_slf", "interconnecting_entity", "poi_kv",
              "poi_bus_no", "poi_station_code", "fis_requested", "ia_signed", "financial_security",
              "approved_energization", "approved_sync", "gis_month"]

_TECH_CLASS = {
    ("OTH", "BA"): "storage", ("OTH", "EN"): "storage", ("GAS", "CE"): "storage", ("OTH", "CE"): "storage",
    ("SOL", "PV"): "solar", ("SOL", "CP"): "solar", ("WIN", "WT"): "wind",
    ("GAS", "CC"): "gas", ("GAS", "GT"): "gas", ("GAS", "IC"): "gas", ("GAS", "ST"): "gas", ("GAS", "FC"): "gas",
    ("NUC", "ST"): "nuclear", ("COA", "ST"): "coal", ("BIO", "ST"): "biomass", ("HYD", "FC"): "hydrogen",
}


def tech_class(fuel, tech):
    if tech in STORAGE_TECH_CODES:
        return "storage"
    if (fuel, tech) in _TECH_CLASS:
        return _TECH_CLASS[(fuel, tech)]
    return {"SOL": "solar", "WIN": "wind", "GAS": "gas", "COA": "coal", "NUC": "nuclear", "BIO": "biomass",
            "HYD": "hydro", "WAT": "hydro", "GEO": "geothermal"}.get(fuel, "other")


def parse_poi(text):
    """Pull kV, PSS/E bus number and ERCOT station code out of the free-text POI (the text itself is kept as written)."""
    t = _clean(text)
    kv = [k for k in re.findall(r"(\d{2,3})\s*-?\s*kV", t, re.I) if int(k) in (69, 115, 138, 161, 230, 345, 500, 765)]
    # bus numbers, in order of appearance: 'Bus# 178', 'bus(3181)', '#8805 NAME', 'NAME (11050)', '(741) NAME', '5362 RIONOG 345kV'
    found = []
    for m in re.finditer(r"Bus\s*(?:No\.?|Number|#)?\s*[:#(]?\s*#?\s*(\d{2,6})|#\s*(\d{2,6})|\((\d{2,6})\)|(?:^|\s)(\d{3,6})(?=\s+[A-Za-z])", t, re.I):
        n = next(g for g in m.groups() if g)
        if n not in found and not (int(n) in (69, 115, 138, 161, 230, 345, 500, 765) and re.match(r"\s*-?\s*kV", t[m.end():], re.I)):
            found.append(n)
    s = re.search(r"Station\s*code\s*[:\-]?\s*([A-Z0-9_]{3,8})", t, re.I)
    return (kv[0] if kv else ""), ";".join(found), (s.group(1).upper() if s else "")


def _sheet_rows(wb, name, header_first_cell="INR"):
    rows = list(wb[name].iter_rows(values_only=True))
    h = next(i for i, r in enumerate(rows) if r[0] == header_first_cell)
    return rows[h], rows[h + 1:]


def build_queue():
    gis_path = _glob1("RPT.00015933.*GIS_Report_*.xlsx")
    month = re.search(r"GIS_Report_([A-Za-z]+\d{4})", gis_path).group(1)
    wb = openpyxl.load_workbook(gis_path, read_only=True, data_only=True)
    out = {}

    # co-location class for storage projects, from the companion battery report (same month)
    colo = {}
    try:
        bp = _glob1("RPT.00015933.*Co-located_Battery_Identification_Report_*.xlsx")
        bwb = openpyxl.load_workbook(bp, read_only=True, data_only=True)
        for sheet, tag in (("Stand-Alone", "stand_alone"), ("Co-located with Solar", "solar"),
                           ("Co-located with Wind", "wind"), ("Co-located with Thermal", "thermal")):
            hdr, body = _sheet_rows(bwb, sheet)
            for r in body:
                if r[0] and r[9] in STORAGE_TECH_CODES:
                    colo[str(r[0]).strip()] = tag
    except FileNotFoundError:
        pass

    hdr, body = _sheet_rows(wb, "Project Details - Large Gen")
    ix = {h.strip(): i for i, h in enumerate(hdr) if h}
    for r in body:
        inr = _clean(r[ix["INR"]])
        if not inr or not re.match(r"\d{2}INR", inr):
            continue
        out[inr] = dict(
            inr=inr, project_name=_clean(r[ix["Project Name"]]), mw=_num(r[ix["Capacity (MW)"]]),
            county=_clean(r[ix["County"]]), poi_text=_clean(r[ix["POI Location"]]),
            zone=_clean(r[ix["CDR Reporting Zone"]]), status=_clean(r[ix["GIM Study Phase"]]),
            projected_cod=_iso(r[ix["Projected COD"]]), fuel_code=_clean(r[ix["Fuel"]]),
            tech_code=_clean(r[ix["Technology"]]), gen_class="large",
            interconnecting_entity=_clean(r[ix["Interconnecting Entity"]]),
            fis_requested=_iso(r[ix["FIS Requested"]]), ia_signed=_iso(r[ix["IA Signed"]]),
            financial_security=_clean(r[hdr.index("IA Signed") + 1]) if "IA Signed" in hdr else "",
            approved_energization=_iso(r[ix["Approved for Energization"]]),
            approved_sync=_iso(r[ix["Approved for Synchronization"]]))

    hdr, body = _sheet_rows(wb, "Project Details - Small Gen")
    ix = {h.strip(): i for i, h in enumerate(hdr) if h}
    for r in body:
        inr = _clean(r[ix["INR"]])
        if not inr or not re.match(r"\d{2}INR", inr) or inr in out:
            continue
        mr = _iso(r[ix["Model Ready Date"]])
        out[inr] = dict(
            inr=inr, project_name=_clean(r[ix["Project Name"]]), mw=_num(r[ix["Capacity (MW)"]]),
            county=_clean(r[ix["County"]]), poi_text=_clean(r[ix["POI Location"]]),
            zone=_clean(r[ix["CDR Reporting Zone"]]),
            status="Small Gen, model ready" if mr else "Small Gen, not model ready",
            projected_cod=_iso(r[ix["Projected COD"]]), fuel_code=_clean(r[ix["Fuel"]]),
            tech_code=_clean(r[ix["Technology"]]), gen_class="small",
            interconnecting_entity=_clean(r[ix["Interconnecting Entity"]]), fis_requested="",
            ia_signed=_iso(r[ix["IA Signed"]]), financial_security=_clean(r[ix["Financial Security"]]),
            approved_energization=_iso(r[ix["Approved for Energization"]]),
            approved_sync=_iso(r[ix["Approved for Synchronization"]]))

    rows = []
    for inr, d in out.items():
        d["tech"] = tech_class(d["fuel_code"], d["tech_code"])
        d["poi_kv"], d["poi_bus_no"], d["poi_station_code"] = parse_poi(d["poi_text"])
        d["colocation"] = colo.get(inr, "") if d["tech"] == "storage" else ""
        d["is_slf"] = "Y" if re.search(r"\bSLF\b", d["project_name"]) or (d["tech"] == "storage" and d["mw"] == 0) else ""
        d["gis_month"] = month
        rows.append(d)
    rows.sort(key=lambda d: (d["tech"] != "storage", d["county"], d["inr"]))

    src = f"ERCOT PG7-200-ER GIS Report {month}"
    lab = {c: ("REAL", src, "as published") for c in QUEUE_COLS}
    lab.update({
        "tech": ("DERIVED", src, "storage/solar/wind/gas/... class from the report's Fuel and Technology codes (BA/EN/CE = storage)"),
        "poi_text": ("REAL", src, "point of interconnection exactly as written (newlines collapsed to spaces)"),
        "poi_kv": ("DERIVED", src, "first 'NNN kV' found in poi_text; blank if none"),
        "poi_bus_no": ("DERIVED", src, "PSS/E bus number(s) parsed from poi_text, semicolon-separated in order of appearance (a tap lists both ends); blank if none; may include a false hit, check against poi_text"),
        "poi_station_code": ("DERIVED", src, "text after 'Station code:' in poi_text; usually blank"),
        "colocation": ("REAL", f"ERCOT Co-located Battery Identification Report ({month})", "storage rows only: stand_alone / solar / wind / thermal"),
        "is_slf": ("DERIVED", src, "Self-Limiting Facility flag: storage reported at 0 MW or name ends SLF; MW is then 0 because the battery is inside another unit's nameplate"),
        "gen_class": ("DERIVED", src, "large = sheet 'Project Details - Large Gen' (FIS requested); small = 'Small Gen' (model ready)"),
        "status": ("REAL", src, "GIM Study Phase for large gen (SS = screening study, FIS = full interconnection study, IA = interconnection agreement); for small gen the model-ready flag"),
        "mw": ("REAL", src, "net MW; repowering projects are net change and SLF batteries are 0"),
    })
    write_csv("queue_storage", rows, QUEUE_COLS, lab)
    return rows


# ===================================================================================================================
# 2. Planned transmission: TPIT + RTP appendix E + RPG decks + transcribed PBRP/765-kV rows
# ===================================================================================================================
PLANNED_COLS = ["project_id", "name", "tsp", "kv", "project_type", "from_sub_name", "to_sub_name", "counties",
                "in_service_date", "status", "cost_musd", "source",
                "record_type", "description", "circuit_miles_new", "circuit_miles_rebuilt", "xfmr_mva", "rpg_number",
                "rtp_number", "planning_tier", "ccn_required", "in_sswg_base_case", "sswg_bus_numbers", "in_service_kind",
                "terminal_source", "tpit_tab", "doc", "page", "note"]

_STATUS = {"planned": "planned", "under construction": "under_construction", "conceptual": "conceptual",
           "in-service": "in_service"}
_XFMR = re.compile(r"auto\s*-?\s*transformer|\bxfmr\b|\btransformer", re.I)
_NEWSUB = re.compile(r"\b(new|establish|construct|add(?:ing)?)\b[^.]{0,60}\b(substation|station|switch(?:ing)?(?: station)?|sub)\b", re.I)
_UPSUB = re.compile(r"terminal|breaker|ring bus|expan|capacitor|reactor|reactive|mvar|\bbus\b|series cap|synchronous condenser|statcom|relay|fault (?:duty|upgrade)", re.I)
_RECON = re.compile(r"reconductor|rebuild|upgrade|uprate|re-?conductor|conversion|convert", re.I)
_NEWLINE_WORDS = re.compile(r"\b(?:build|construct(?:ion)?|new|add(?:ing)?)\b[^.]{0,80}\b(?:line|circuit)\b", re.I)
_RATING_UPGRADE = re.compile(r"\bupgrade\b[^.]{0,80}(?:rating|mva|conductor|ckt|circuit|line)", re.I)


def classify_project(text, new_miles, reb_miles, mva, has_terminals):
    """project_type from the TPIT numbers and words: new_line | reconductor | new_xfmr | new_sub | upgrade_sub | other.
    Order: transformer MVA > new line miles/words > rebuilt/reconductored miles or rating-upgrade words > new-substation words
    > substation-equipment words."""
    t = text or ""
    if (mva or 0) > 0 or (_XFMR.search(t) and not (new_miles or 0) > 0):
        return "new_xfmr"
    if (new_miles or 0) > 0 and (has_terminals or re.search(r"\bline\b|circuit|gen-?tie", t, re.I)):
        return "new_line"
    if not re.search(r"rebuild|reconductor", t, re.I) and _NEWLINE_WORDS.search(t):
        return "new_line"
    if (reb_miles or 0) > 0 or (has_terminals and _RECON.search(t)) or _RATING_UPGRADE.search(t):
        return "reconductor"
    if _NEWSUB.search(t):
        return "new_sub"
    if _UPSUB.search(t):
        return "upgrade_sub"
    return "other"


def _title_terminals(title):
    """'A to B 138-kV ...' or 'A - B 138 kV ...' -> (A, B) as written; used only when the TPIT terminals are blank."""
    t = _clean(title)
    m = re.match(r"^(.+?)\s+(?:to|-|–|—)\s+(.+?)(?:\s+to\s+(.+?))?\s*(?::|,|\d{2,3}\s*-?\s*kV|\(|$)", t, re.I)
    if m and len(m.group(1)) < 60 and len(m.group(2)) < 60:
        a, b = m.group(1).strip(), (m.group(3) or m.group(2)).strip()
        if a and b and not re.search(r"\d{2,3}\s*-?\s*kV", a):
            return a, b
    return "", ""


def _tpit_rows():
    path = _glob1("TPIT_*.xlsx")
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    tab_rank = {"CompletedTPIT": 0, "PlannedTPIT": 1, "FutureTPIT": 2, "CancelledTPIT": 9}
    seen, cancelled = {}, 0
    for tab in wb.sheetnames:
        key = next((k for k in tab_rank if tab.startswith(k)), None)
        if key is None:
            continue
        rows = list(wb[tab].iter_rows(values_only=True))
        hdr = [_clean(h) for h in rows[1]]

        def col(prefix, hdr=hdr):
            for i, h in enumerate(hdr):
                if h.startswith(prefix):
                    return i
            raise KeyError(prefix)
        ix = dict(num=col("ERCOT Project Number"), title=col("Project Title"), desc=col("Project Description"),
                  com=col("Comments"), frm=col('Terminal "from"'), to=col('Terminal "to"'), st=col("Transmission Status"),
                  tso=col("Transmission Owner (text)"), proj=col("Projected In-Service"),
                  act=col("Actual In-Service"), kv=col("Service Level kV"), mnew=col("Trans Circuit Miles New"),
                  mreb=col("Trans Circuit Miles Rebuilt"), mva=col("Autotransformer Capacity"),
                  c1=col("County Location for Substation"), c2=col("County Location for Ending"),
                  tier=col("Planning Charter Tier"), rpg=col("RPG Number"), bus=col("SSWG Base Case Related"),
                  inb=col("Is the project reflected"), rtp=col("RTP Project Number"))
        for r in rows[2:]:
            num = _clean(r[ix["num"]])
            if not num:
                continue
            if key == "CancelledTPIT":
                cancelled += 1
                continue
            if num in seen and tab_rank[seen[num]["_k"]] <= tab_rank[key]:
                continue
            title, desc = _clean(r[ix["title"]]), _clean(r[ix["desc"]])
            frm, to = _clean(r[ix["frm"]]), _clean(r[ix["to"]])
            tsrc = "tpit_terminal" if (frm or to) else "none"
            if not frm and not to:
                a, b = _title_terminals(title or desc)
                if a:
                    frm, to, tsrc = a, b, "title_parse"
            if frm and to and frm == to:
                to = ""      # substation project listed with the same terminal twice
            kv = _num(r[ix["kv"]])
            mnew, mreb, mva = _num(r[ix["mnew"]]) or 0, _num(r[ix["mreb"]]) or 0, _num(r[ix["mva"]]) or 0
            actual = r[ix["act"]]
            st_raw = _clean(r[ix["st"]]).lower()
            status = "in_service" if (actual or st_raw == "in-service") else _STATUS.get(st_raw, "unspecified")
            when = actual or r[ix["proj"]]
            counties = "; ".join(dict.fromkeys(c for c in (_clean(r[ix["c1"]]), _clean(r[ix["c2"]])) if c))
            seen[num] = dict(
                _k=key, project_id=f"TPIT-{num}", name=title or (desc[:120] if desc else f"{frm} {to}".strip()),
                tsp=_clean(r[ix["tso"]]), kv=int(kv) if kv else "", from_sub_name=frm, to_sub_name=to, counties=counties,
                in_service_date=_iso(when), status=status, cost_musd="", record_type="tpit",
                description=desc, circuit_miles_new=mnew or "", circuit_miles_rebuilt=mreb or "", xfmr_mva=mva or "",
                rpg_number=_clean(r[ix["rpg"]]), rtp_number=_clean(r[ix["rtp"]]), planning_tier=_clean(r[ix["tier"]]),
                in_sswg_base_case=_clean(r[ix["inb"]]), sswg_bus_numbers=_clean(r[ix["bus"]]),
                in_service_kind="actual" if actual else "projected", terminal_source=tsrc, tpit_tab=tab,
                project_type=classify_project(f"{title} {desc} {frm if not (title or desc) else ''}", mnew, mreb, mva, bool(frm and to)),
                source="ERCOT TPIT, July 2026 base (sheet " + tab + ")", doc=pathlib.Path(path).name, page="",
                note=_clean(r[ix["com"]])[:200])
    out = list(seen.values())
    print(f"  TPIT: {len(out)} projects ({cancelled} cancelled rows skipped)")
    return out


def _rtp_rows(tpit_by_num):
    """RTP 2025 Appendix E (reliability projects). Adds rtp_number to matching TPIT rows; unmatched ones become RTP-only rows."""
    zpath = _glob1("rpt.00024735.*2025_Regional_Transmission_Plan_Report_Public.zip")
    z = zipfile.ZipFile(zpath)
    name = next(n for n in z.namelist() if "Appendix_E" in n and n.endswith(".xlsx"))
    wb = openpyxl.load_workbook(io.BytesIO(z.read(name)), read_only=True, data_only=True)
    rows = list(wb["Projects"].iter_rows(values_only=True))
    h = next(i for i, r in enumerate(rows) if r[1] == "Project Index")
    hdr = rows[h]
    yrs = {str(hdr[i]): i for i in range(7, 12)}
    out, matched = [], 0
    for r in rows[h + 1:]:
        idx = _clean(r[1])
        if not idx:
            continue
        desc = _clean(r[2])
        nums = re.findall(r"\d{4,6}", _clean(r[12]))
        hit = [tpit_by_num[n] for n in nums if n in tpit_by_num]
        for t in hit:
            t["rtp_number"] = "; ".join(dict.fromkeys([x for x in (t["rtp_number"], idx) if x]))
            t["description"] = (t["description"] + " | RTP: " + desc).strip(" |")[:600]
        first = next((lab for lab, i in yrs.items() if r[i] == "X"), "")
        if hit:
            matched += 1
            continue
        kv = re.findall(r"(\d{2,3})\s*-?\s*kV", desc, re.I)
        a, b = "", ""
        m = re.match(r"^(.*?)\s+(?=\d{2,3}\s*-?\s*kV)", desc)
        if m and re.search(r"\sto\s", m.group(1)):
            seg = re.split(r"\s+to\s+", m.group(1))
            a, b = seg[0].strip(), seg[-1].strip()
        out.append(dict(
            project_id=f"RTP2025-{idx}", name=desc[:160], tsp=_clean(r[4]), kv=int(kv[-1]) if kv else "", from_sub_name=a, to_sub_name=b,
            counties=_clean(r[6]).replace(", ", "; "), in_service_date="", status="rtp_identified", cost_musd="", record_type="rtp",
            description=desc, rtp_number=idx, in_service_kind="needed_by_study_year", terminal_source="desc_parse" if a else "none",
            project_type=classify_project(desc, 0, 0, 0, bool(a)),
            source="ERCOT 2025 RTP Appendix E (reliability projects), first needed in study year " + first, doc=name, page="",
            note="in_service_date left blank: the appendix gives the first study year in which the project is needed (" + first + "), not an energisation date; PSS/E bus numbers are in the name text"))
    print(f"  RTP Appendix E: {len(rows) - h - 1} projects, {matched} matched to TPIT, {len(out)} RTP-only rows")
    return out


_TSP_WORDS = [("LCRA TSC", "LCRA TSC"), ("CNP", "CNP"), ("Lubbock Power", "LP&L"), ("LP&L", "LP&L"), ("LCRA", "LCRA TSC"), ("CenterPoint", "CNP"), ("CEHE", "CNP"), ("Oncor", "ONCOR"),
              ("AEPSC", "AEP"), ("AEP", "AEP"), ("CPS Energy", "CPS"), ("STEC", "STEC"), ("TNMP", "TNMP"), ("PEC", "PEC"),
              ("LPL", "LP&L"), ("Austin Energy", "AEN"), ("AEN", "AEN"), ("DME", "DME"), ("BEC", "BEPC"), ("Brazos", "BEPC"),
              ("Cross Texas", "CTT"), ("CTT", "CTT"), ("Sharyland", "SLU"), ("ETT", "ETT")]


def _cost_musd(flat):
    for pat in (r"estimated cost of (?:approximately )?\$\s?([\d.,]+)\s*(million|billion)",
                r"estimated to cost (?:approximately )?\$\s?([\d.,]+)\s*(million|billion)",
                r"cost estimate of (?:approximately )?\$\s?([\d.,]+)\s*(million|billion)"):
        m = re.search(pat, flat, re.I)
        if m:
            v = float(m.group(1).replace(",", ""))
            return round(v * (1000 if m.group(2).lower() == "billion" else 1), 3)
    return None


def _isd(flat):
    m = re.search(r"(?:in-service dates?|ISDs?)[^.$]{0,60}?(?:is|are|of|for)\s+(?:an?\s+)?((?:[A-Z][a-z]+\.?\s+\d{1,2},\s+\d{4})|(?:[A-Z][a-z]+\s+\d{4})|(?:20\d\d))", flat)
    if not m:
        return ""
    t = m.group(1)
    for fmt, out in (("%B %d, %Y", "%Y-%m-%d"), ("%B %Y", "%Y-%m")):
        try:
            return datetime.strptime(t, fmt).strftime(out)
        except ValueError:
            pass
    return t.lower() if re.match(r"(Summer|Fall|Winter|Spring) \d{4}", t) else t


def _rpg_type(title):
    """Title-only project_type for RPG decks (no mileage fields): rebuild/hardening > new line > transformer > substation > other."""
    t = title
    if re.search(r"rebuild|reconductor|hardening", t, re.I):
        return "reconductor"
    if re.search(r"\bline\b", t, re.I) and re.search(r"addition|double-circuit|new", t, re.I) and not re.search(r"area|improvements?\b.*reliab", t, re.I):
        return "new_line"
    if re.search(r"auto\s*transformer|transformer addition", t, re.I) and not re.search(r"switching station", t, re.I):
        return "new_xfmr"
    if re.search(r"substation|switching station|switch project", t, re.I):
        return "new_sub"
    return "other"


def _rpg_rows():
    """One row per RPG project from the 2026 RPG meeting decks (latest deck wins; ISD carried from earlier decks when missing)."""
    import subprocess
    rdir = DOCS / "rpg"
    projects = {}
    for f in sorted(rdir.glob("*.pdf")) if rdir.exists() else []:
        try:
            txt = subprocess.run(["pdftotext", "-layout", "-nopgbrk", str(f), "-"], capture_output=True, timeout=120,
                                 encoding="utf-8", errors="ignore").stdout
        except (OSError, subprocess.SubprocessError):
            print("  WARNING: pdftotext not found on PATH; RPG deck rows skipped (install poppler or Xpdf tools)")
            break
        flat = re.sub(r"\s+", " ", txt)
        head = flat[:700]
        title_ids = re.findall(r"\b(\d{2}RPG\d{3})\b", head)
        cost, tier = _cost_musd(flat), re.search(r"Tier (\d)", flat)
        if not title_ids or cost is None or not tier:
            continue          # keep only decks that describe a project with a price
        date = f.name[:8]     # MMDDYYYY
        meeting = f"{date[4:]}-{date[:2]}-{date[2:4]}"
        title = re.sub(r"CONPFUIDBELNI\s*CTIAL|CONFIDENTIAL|PUBLIC|\bDraft\b", " ", head)
        title = re.split(r"\(\d{2}RPG\d{3}|ERCOT Independent Review|Regional Planning Group|RPG Meeting|\bEIR\b", title)[0]
        title = re.sub(r"\bDraft\b", " ", title)
        title = re.sub(r"(\d)\s*-\s*kV", r"\1-kV", title)
        title = re.sub(r"\s+", " ", title).strip(" -–—")
        key = title_ids[0]
        stage = "recommendation" if re.search(r"Recommendation", f.name, re.I) else ("scope" if re.search(r"Scope", f.name, re.I) else "status_update")
        needs = re.search(r"(?:will|would)\s+require\s+a\s+(?:Certificate of )?Convenience", flat)
        not_needs = re.search(r"(?:will|would)\s+not\s+require\s+a\s+(?:Certificate of )?Convenience", flat)
        d = dict(meeting=meeting, file=f.name, title=title, ids=sorted(set(title_ids)), cost=cost, tier=tier.group(1),
                 isd=_isd(flat), ccn=bool(needs) and not not_needs, stage=stage)
        if key not in projects or meeting >= projects[key]["meeting"]:
            old = projects.get(key)
            if old and not d["isd"]:
                d["isd"] = old["isd"]
            projects[key] = d
    out = []
    for key, d in projects.items():
        kvs = [int(x) for x in re.findall(r"(\d{2,3})\s*-?\s*kV", d["title"], re.I)] + \
              [int(x) for pair in re.findall(r"(\d{2,3})/(\d{2,3})\s*-?\s*kV", d["title"], re.I) for x in pair]
        tsp = next((v for k, v in _TSP_WORDS if re.search(r"\b" + re.escape(k) + r"\b", d["title"])), "")
        a, b = _title_terminals(re.sub(r"^.*?\)\s*[-–]?\s*", "", d["title"]))
        out.append(dict(
            project_id=f"RPG-{key}", name=d["title"], tsp=tsp, kv=max(kvs) if kvs else "", from_sub_name=a, to_sub_name=b, counties="",
            in_service_date=d["isd"], status="ercot_recommended" if d["stage"] == "recommendation" else "under_rpg_review",
            cost_musd=d["cost"], record_type="rpg", description="RPG ids: " + ", ".join(d["ids"]), rpg_number=", ".join(d["ids"]),
            planning_tier="Tier " + d["tier"], ccn_required="Y" if d["ccn"] else "N", in_service_kind="projected",
            terminal_source="title_parse" if a else "none",
            project_type=_rpg_type(d["title"]),
            source=f"ERCOT RPG meeting {d['meeting']}: {d['file'].split('_', 1)[1][:90]}", doc="rpg/" + d["file"], page="",
            note="project-level cost estimate from the ERCOT Independent Review deck (" + d["stage"] + "); TPIT components of the same RPG number carry no cost"))
    print(f"  RPG decks: {len(out)} priced projects")
    return out


def build_planned():
    tpit = _tpit_rows()
    by_num = {t["project_id"].split("-", 1)[1]: t for t in tpit}
    rtp = _rtp_rows(by_num)
    rpg = _rpg_rows()
    trans = read_csv("transcribed_planned")
    for t in trans:
        t["record_type"] = "transcribed"
        t["in_service_kind"] = "projected"
        t["terminal_source"] = "transcribed"
        if t.get("cost_musd"):
            t["cost_musd"] = float(t["cost_musd"])
    rows = tpit + rtp + rpg + trans
    for r in rows:
        r.pop("_k", None)
    lab = {c: ("REAL", "ERCOT TPIT July 2026 (and RTP/RPG/PBRP documents, see source)", "as published") for c in PLANNED_COLS}
    lab.update({
        "project_id": ("DERIVED", "ERCOT TPIT / RTP Appendix E / RPG decks", "TPIT-<ERCOT project number>, RTP2025-<index>, RPG-<RPG number>, PBRP-/STEP-/CN2025- for transcribed rows"),
        "name": ("REAL", "ERCOT TPIT project title (or description, or RTP/RPG title)", "substation names kept as written, not mapped to ERCOT codes"),
        "kv": ("REAL", "TPIT 'Service Level kV'; RTP/RPG rows: largest kV in the title", "RTP/RPG rows are DERIVED from the text"),
        "project_type": ("DERIVED", "classify_project() in docs_parse.py", "rule order: transformer MVA > new line miles > rebuilt/reconductor miles > new-substation words > substation-equipment words > other"),
        "from_sub_name": ("REAL", "TPIT terminal 'from' (title parse where terminal blank, see terminal_source)", "kept as written; Agent 1c maps names to ERCOT substation codes"),
        "to_sub_name": ("REAL", "TPIT terminal 'to' (title parse where blank)", "kept as written"),
        "counties": ("REAL", "TPIT county of substation or line start/end", ""),
        "in_service_date": ("REAL", "TPIT actual date if energised else projected month; RPG: estimated ISD; RTP rows blank", "TPIT dates are the first of the projected month or the exact day given"),
        "status": ("DERIVED", "TPIT status text + actual in-service date", "in_service / under_construction / planned / conceptual; RPG: under_rpg_review or ercot_recommended; RTP: rtp_identified"),
        "cost_musd": ("REAL", "RPG decks, PBRP report, 2024 RTP STEP comparison", "blank for TPIT rows: the public TPIT is the 'No Cost' version. RPG and transcribed rows are project-level; do not sum with TPIT components"),
        "source": ("REAL", "document named in the cell", ""),
        "record_type": ("DERIVED", "docs_parse.py", "tpit / rtp / rpg / transcribed"),
        "description": ("REAL", "TPIT project description (RTP description appended when matched)", ""),
        "circuit_miles_new": ("REAL", "TPIT 'Trans Circuit Miles New'", ""),
        "circuit_miles_rebuilt": ("REAL", "TPIT 'Trans Circuit Miles Rebuilt, Reconductored or Upgraded'", ""),
        "xfmr_mva": ("REAL", "TPIT 'Autotransformer Capacity (MVA)'", ""),
        "rpg_number": ("REAL", "TPIT 'RPG Number' / RPG deck id", ""),
        "rtp_number": ("REAL", "TPIT 'RTP Project Number', plus 2025 RTP Appendix E index when matched via the TPIT number", ""),
        "planning_tier": ("REAL", "TPIT 'Planning Charter Tier'", "Tier 1 is the most expensive class"),
        "ccn_required": ("REAL", "RPG deck text", "Y if the deck says the project will require a CCN"),
        "in_sswg_base_case": ("REAL", "TPIT 'Is the project reflected in SSWG Base Cases'", ""),
        "sswg_bus_numbers": ("REAL", "TPIT 'SSWG Base Case Related Bus Numbers'", "PSS/E bus numbers: useful to attach the project to network buses in Phase 2"),
        "in_service_kind": ("DERIVED", "docs_parse.py", "actual / projected / needed_by_study_year"),
        "terminal_source": ("DERIVED", "docs_parse.py", "tpit_terminal / title_parse / desc_parse / transcribed / none"),
        "tpit_tab": ("REAL", "TPIT sheet name", "CompletedTPIT, PlannedTPIT or FutureTPIT"),
        "doc": ("REAL", "file name in research-data/grid/raw/docs", ""),
        "page": ("REAL", "page in the source document", ""),
        "note": ("REAL", "TPIT comments column or analyst note on a transcribed row", ""),
    })
    write_csv("planned_projects", rows, PLANNED_COLS, lab)
    return rows


# ===================================================================================================================
# 3. Constraints, GTCs
# ===================================================================================================================
CONSTRAINT_COLS = ["year", "constraint_name", "from_station", "to_station", "kv", "element_type", "rent_musd", "binding_hours",
                   "area", "source", "market", "period", "rank_rent", "rank_freq", "outage_related", "binding_intervals_5min",
                   "element_id", "doc", "page", "note"]
# same physical element under different names in the two sources (Potomac / SCED short name -> ERCOT report wording)
ELEMENT_ALIASES = {
    "VEALMOOR-KOCHTAP": "Koch Tap - Vealmoor 138kV", "TWINBU-HARGROVE": "Hargrove - Twin Buttes 138kV",
    "ODEHV-YARBR": "Odessa EHV Switch - Yarbrough Sub 138kV", "LNGSW-CONSW": "Longshore Switch - Consavvy Switch 345kV",
    "LARDVNTH-LASCRUCE": "Laredo VFT North - Las Cruces 138kV", "BELCNTY-SALSW": "Bell County - Salado Switch 138kV",
    "SCRCV-KNAPP": "Knapp - Scurry Chevron 138kV", "LA_PALMA-HAINE_DR": "Haine Drive - La Palma 138kV",
    "PCTSW-WRPOD": "Polecat Creek Switch - Wrage Ranch Pod 138kV", "TKWSW-MGSES": "Tonkawa Switch - Morgan Creek SES 345kV",
    "MGSES-NAVIG": "Morgan Creek SES - Navigation Sub 138kV", "RIOHONDO-MV_BURNS": "Burns Sub - Rio Hondo 138kV",
    "WESTEX": "West Texas Export Interface", "PNHNDL": "Panhandle Interface", "NE_LOB": "North Edinburg to Lobo interface",
}
_ALIAS_REV = {v: k for k, v in ELEMENT_ALIASES.items()}


def build_constraints():
    src = read_csv("transcribed_constraints")
    rows = []
    for r in src:
        name = r["constraint_name"]
        m = re.search(r"\(([^()]+)\)\s*$", name)
        inner = m.group(1) if m else ""
        frm, to = r["from_station"], r["to_station"]
        if not frm and r["element_type"] != "gtc" and "-" in inner:
            frm, to = (x.strip() for x in inner.split("-", 1))
        element_id = inner if m else _ALIAS_REV.get(name, "")
        hrs = ""
        if r["binding_intervals_5min"]:
            hrs = round(float(r["binding_intervals_5min"]) / SCED_INTERVALS_PER_HOUR, 1)
        elif r["binding_hours_da"]:
            hrs = float(r["binding_hours_da"])
        rows.append(dict(
            year=r["year"], constraint_name=name, from_station=frm, to_station=to, kv=r["kv"], element_type=r["element_type"],
            rent_musd=r["rent_musd"], binding_hours=hrs, area=r["area"], source=r["source"], market=r["market"], period=r["period"],
            rank_rent=r["rank_rent"], rank_freq=r["rank_freq"], outage_related=r["outage_related"],
            binding_intervals_5min=r["binding_intervals_5min"], element_id=element_id, doc=r["doc"], page=r["page"], note=r["note"]))
    lab = {c: ("REAL", "Potomac Economics monthly reports / ERCOT Constraints and Needs reports (see source)", "transcribed by hand with page numbers in data/transcribed_constraints.csv") for c in CONSTRAINT_COLS}
    lab.update({
        "from_station": ("DERIVED", "split of the parenthetical station pair in the Potomac constraint name, or the ERCOT report wording", "SCED short names (e.g. TWINBU, HARGROVE); Phase 2 maps to NP4-160 codes and the NP6-86 constraint table"),
        "to_station": ("DERIVED", "same as from_station", ""),
        "kv": ("REAL", "ERCOT C&N reports state kV in the element name; blank where only the Potomac name is available", "Phase 2 fills blanks from NP6-86-CD"),
        "element_type": ("DERIVED", "name test: GTC/Interface = gtc, XFMR = xfmr, else line", "check against NP6-86-CD before relying on it"),
        "binding_hours": ("DERIVED", "RT: SCED 5-minute intervals / 12; DA: hours binding as published", "RT_ERCOT_CN rows carry no frequency; projected rows are blank"),
        "area": ("ASSUMPTION", "analyst grouping from the station names (and docs/research/ercot.md for the 2025 top constraints)", "blank = not assigned; do not use for geography, 1c supplies coordinates"),
        "market": ("DERIVED", "docs_parse.py", "RT / DA = Potomac actuals; RT_ERCOT_CN = ERCOT C&N top 10 (12-month window in 'period'); RTP_PROJ_BASE / RTP_PROJ_SENS = ERCOT economic-study projections. Do not sum across markets: RT and DA describe the same physical elements"),
        "element_id": ("DERIVED", "parenthetical station pair or GTC code from the Potomac name; ELEMENT_ALIASES maps ERCOT-wording rows to it", "join key to match the same element across Potomac and ERCOT-wording rows"),
        "rent_musd": ("REAL", "source named in the row", "RT/DA/RT_ERCOT_CN: realised congestion rent $M; RTP_PROJ_*: projected rent $M/yr"),
        "binding_intervals_5min": ("REAL", "Potomac 'Number of SCED Intervals'", ""),
    })
    write_csv("constraints_annual", rows, CONSTRAINT_COLS, lab)
    return rows


GTC_COLS = ["name", "region", "description", "interface_elements", "status", "source", "code", "planning_limit_mw_2027",
            "planning_limit_mw_2030", "last_public_update", "doc", "page", "note"]


def build_gtc():
    rows = read_csv("transcribed_gtc")
    lab = {c: ("REAL", "ERCOT RPG 29 Jul 2025 stability-limits deck, ERCOT market notices, 2025 RTP report", "see source column; element lists are ECEII and not public") for c in GTC_COLS}
    lab.update({
        "name": ("REAL", "ERCOT / IMM names", ""),
        "description": ("DERIVED", "analyst summary of the cited ERCOT statements", "ERCOT's GTC white paper (Jul 2020) says GTCs manage non-thermal limits (voltage, angle, oscillation, control stability) with market dispatch"),
        "interface_elements": ("REAL", "ERCOT market notices", "the element list is in the GTC Methodology (EMIL NP3-770-M), secured under ECEII; not public"),
        "status": ("DERIVED", "ERCOT notices + IMM rankings + 2025 RTP report", ""),
        "planning_limit_mw_2027": ("REAL", "ERCOT 2025 RTP stability limits deck", "9,999 = no limit needed in planning; limits are for no prior outages"),
        "planning_limit_mw_2030": ("REAL", "ERCOT 2025 RTP stability limits deck", "same"),
    })
    write_csv("gtc", rows, GTC_COLS, lab)
    return rows


# ===================================================================================================================
# 4. Large loads, unit costs
# ===================================================================================================================
LL_COLS = ["area", "area_type", "mw", "status", "date", "source", "doc", "page", "note"]


def build_large_loads():
    rows = read_csv("transcribed_large_loads")
    lab = {c: ("REAL", "ERCOT Board/Senate/TAC/RPG decks named in source", "transcribed by hand; page in the page column") for c in LL_COLS}
    lab.update({
        "area": ("REAL", "ERCOT deck", "finest geography ERCOT publishes: weather-zone region, top-5 county lists, TSP, and RPG study areas. No project-level or full county file is public"),
        "area_type": ("DERIVED", "docs_parse.py", "state / weather_zone / county / county_group / region / tsp"),
        "mw": ("REAL", "ERCOT deck", "requests are not load that will arrive: ERCOT expects roughly half of data-centre requests to materialise; Batch Zero classes are conditional"),
        "status": ("REAL", "ERCOT deck wording", ""),
    })
    write_csv("large_loads", rows, LL_COLS, lab)
    return rows


UC_COLS = ["item", "kv", "unit", "low_usd", "mid_usd", "high_usd", "year_usd", "source", "doc", "page", "note"]


def build_unit_costs():
    rows = read_csv("transcribed_unit_costs")
    lab = {c: ("REAL", "MISO Transmission Cost Estimation Guide MTEP24, ERCOT PBRP/RTP, DOE, NLR (see source)", "transcribed by hand; page in the page column") for c in UC_COLS}
    lab.update({
        "low_usd": ("REAL", "see source/note", "rows marked DERIVED/ASSUMPTION/UNVERIFIED in the note are not plain copies; low/mid/high may come from different sources, the note says which"),
        "mid_usd": ("REAL", "see source/note", ""),
        "high_usd": ("REAL", "see source/note", ""),
        "year_usd": ("REAL", "source", "MISO MTEP25 and MTEP26 escalate most costs about 4%/yr (MTEP26 slides p.4); apply that to bring 2024 USD to 2026 (ASSUMPTION, not done here)"),
    })
    write_csv("unit_costs", rows, UC_COLS, lab)
    return rows


# ===================================================================================================================
# main entry
# ===================================================================================================================
TABLES = {"queue": build_queue, "planned": build_planned, "constraints": build_constraints, "gtc": build_gtc,
          "large_loads": build_large_loads, "unit_costs": build_unit_costs}


def transcribed_labels():
    """Label sidecars for the hand-transcribed CSVs (data/transcribed_*.csv). Everything is REAL (copied by hand from the
    page named in `doc` and `page`) except the columns listed below."""
    special = {
        "transcribed_constraints": {
            "area": ("ASSUMPTION", "analyst grouping from station names", "not a source statement"),
            "element_type": ("DERIVED", "name test (GTC/Interface, XFMR, else line)", ""),
            "from_station": ("REAL", "ERCOT report wording (C&N rows); blank for Potomac rows, parsed later", ""),
            "to_station": ("REAL", "ERCOT report wording (C&N rows)", ""),
            "kv": ("REAL", "stated in the ERCOT C&N element name; blank when not stated", ""),
            "outage_related": ("REAL", "ERCOT C&N 'Outage Related?' column", "row alignment read from text order, see note (UNVERIFIED for 2024)"),
        },
        "transcribed_gtc": {
            "description": ("DERIVED", "analyst summary of the cited ERCOT statements", ""),
            "status": ("DERIVED", "ERCOT notices + IMM rankings + RTP report", ""),
        },
        "transcribed_large_loads": {
            "area_type": ("DERIVED", "analyst classification", ""),
        },
        "transcribed_unit_costs": {
            "unit": ("DERIVED", "analyst", "unit of low/mid/high"),
            "note": ("DERIVED", "analyst note: says which source gives low/mid/high and flags UNVERIFIED mappings", ""),
        },
        "transcribed_planned": {
            "project_type": ("DERIVED", "analyst reading of the project title", ""),
            "kv": ("DERIVED", "stated in the project title where present", ""),
            "in_service_date": ("REAL", "ERCOT C&N Dec 2025 p.8 (year only); PBRP import paths use the C&N year for the whole programme", "component dates in TPIT range 2028-2031"),
        },
    }
    for stem, spec in special.items():
        path = REPO_DATA / f"{stem}.csv"
        if not path.exists():
            continue
        with open(path, newline="", encoding="utf-8") as f:
            cols = next(csv.reader(f))
        with open(REPO_DATA / f"{stem}.labels.csv", "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["column", "label", "source", "note"])
            for c in cols:
                lab, src, note = spec.get(c, ("REAL", "hand-transcribed from the document and page named in the 'doc' and 'page' (or 'source') columns", ""))
                w.writerow([c, lab, src, note])
        print(f"  wrote {stem}.labels.csv")


def parse_docs(*which):
    todo = list(which) or list(TABLES)
    for t in todo:
        if t not in TABLES:
            raise SystemExit(f"unknown table {t}; choose from {sorted(TABLES)}")
        print(t)
        TABLES[t]()
    if not which:
        transcribed_labels()


if __name__ == "__main__":
    parse_docs(*sys.argv[1:])
