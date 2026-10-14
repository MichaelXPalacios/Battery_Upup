"""Map queued and operating storage to ERCOT substations (NP4-160 `sub` codes) for the saturation work.

Two sources, one table (data/queue_sub_map.csv):

QUEUE  data/queue_storage.csv (869 storage projects of the Sep 2026 GIS report). The point of interconnection is free text
       (`poi_text`) plus a county. Methods, in order of strength:
         psse_bus      PSS/E bus numbers in the text, joined to NP4-160 PSSE_BUS_NUMBER -> SUBSTATION (unique: no number maps
                       to two substations). One number: an existing station. Two numbers: the project TAPS the line between
                       two stations, so it will sit at a new switchyard on that line; both ends are recorded (`sub`, `sub_alt`).
         name_county   no usable number: the station name in the text, matched to substation names (code, PSS/E bus-name stems,
                       OSM and EIA names) with fuzzy matching, restricted or boosted by county agreement.
       Confidence = precision measured on the rows where psse_bus is available (the text with its numbers removed is matched
       by name and compared with the bus-number answer), so the name method's confidence is an observed hit rate.

EIA    data/eia_generators_tx.csv battery rows (EIA-860/860M: operating and planned, with plant coordinates).
         eia_plant_link  the plant code is listed in data/substations.csv `eia_plant_codes` (agent 1c matched the sub's units
                         to that plant through ERCOT's CDR unit names);
         nearest_sub     otherwise the nearest placed substation of at least 69 kV, preferring the plant's grid voltage;
                         confidence from the observed hit rate of the same rule on plants that DO have a link.

A mapping at confidence >= 0.5 is what the saturation step should use. A row it cannot map keeps sub = '' and conf = 0.
The result is a screening mapping, not an interconnection record: it cannot tell which breaker position a project uses.
"""
from __future__ import annotations

import re
from difflib import SequenceMatcher

import numpy as np
import pandas as pd

from . import paths

# --------------------------------------------------------------------------- constants (label: PLAN.md)
REF_KV = {69, 115, 138, 161, 230, 345, 500, 765}   # REAL: nominal transmission voltages; a bare number equal to one is a voltage
KV_TOL = 1.0                          # ASSUMPTION: kV difference tolerated between the text and the bus voltage
MIN_NAME_CHARS = 4                    # ASSUMPTION: shorter name phrases are not matched (too ambiguous)
SHORT_NUMBER_MAX = 99                 # ASSUMPTION: a bus number this small is accepted only if its station's name appears in the text
SHORT_NUMBER_NAME_SCORE = 0.8         # ASSUMPTION: fuzzy score the name evidence must reach for a short number
NAME_ACCEPT_SCORE = 0.82              # ASSUMPTION: minimum fuzzy score to accept a name match (checked by validate_name_match)
NAME_AMBIGUITY_MARGIN = 0.04          # ASSUMPTION: the best candidate must beat the second by this much
OVERLAP_NAME_SCORE = 0.75             # ASSUMPTION: fuzzy score of the project names (generic words removed) to call two records the same project
OVERLAP_MW_TOL = 0.25                 # ASSUMPTION: relative MW difference tolerated when the names agree
OVERLAP_MW_TOL_TIGHT = 0.10           # ASSUMPTION: relative MW difference tolerated when only the substation agrees
COUNTY_NAME_BONUS = 0.05              # ASSUMPTION: score bonus for a candidate in the queue county, so a county match breaks an exact name tie
EIA_MAX_DIST_KM = 8.0                 # DERIVED: beyond this the nearest substation was never the linked one (validate_nearest)
EIA_MIN_KV = 69.0                     # ASSUMPTION: a transmission-connected battery connects at 69 kV or more
EIA_KV_PREFER_EXTRA_KM = 2.0          # ASSUMPTION: prefer a substation carrying the plant's grid voltage if at most this much further
EIA_TIE_KM = 0.05                     # ASSUMPTION: substations within 50 m of the nearest one are indistinguishable by distance
# Confidence of each method. psse_bus rows come straight from the PSS/E bus table, so they are near certain when voltage and
# county agree. The name and nearest-substation values are measured (see CALIBRATION and calibrate() below).
CONF_BUS_SINGLE = 0.95                # ASSUMPTION: one bus number resolves to a station and its voltage matches the text
CONF_BUS_KV_MISMATCH = 0.75           # ASSUMPTION: number resolves, but the bus voltage differs from the POI voltage in the text
CONF_TAP_BOTH_ENDS = 0.60             # ASSUMPTION: both ends of a tapped line resolve; the new switchyard sits between them
CONF_TAP_ONE_END = 0.50               # ASSUMPTION: one end resolves; the other end is a bus that is not in the model yet
COUNTY_MISMATCH_PENALTY = 0.05        # ASSUMPTION: the queue county is where the project sits, the POI can lie across the line
CONF_EIA_LINK_SINGLE = 0.85           # ASSUMPTION: plant code listed by exactly one substation (CDR unit-name match)
CONF_EIA_LINK_MULTI = 0.70            # ASSUMPTION: listed by several substations; the top-ranked one is taken
# CALIBRATION, DERIVED on 10 Oct 2026 by calibrate() (run it to see the measurements behind each number):
#  name_county: with the station present in the model, an accepted name match (score >= NAME_ACCEPT_SCORE, margin >= NAME_AMBIGUITY_MARGIN)
#    hit the bus-number answer 95% of the time when the candidate sits in the queue county (n=213) and 95% when its county is unknown (n=20);
#    a candidate in another county is never accepted. With the true station REMOVED from the index (a new station, which is what most rows
#    without a usable number are) it still accepted a wrong station 50% of the time in-county (n=94), 6% county-unknown (n=48), 14% in another
#    county (n=184, not accepted by the rule). At an assumed 50% chance that the named station exists the precision is about 0.65 in-county;
#    we take 0.55, and 0.50 for unknown county.
#  nearest_sub: on 241 plants whose linked substation was placed independently (OSM name), the nearest >= 69 kV substation was the linked one
#    73% of the time within 1 km when it was the only candidate within 50 m (n=175), 85% for 1-3 km (n=13, we take 0.60), 15% for 3-8 km
#    (n=13) and never beyond; when several substations sat within 50 m of the nearest one the hit rate was 38% (n=26) and 29% (n=7).
CALIBRATION = {
    "name_conf_county_ok": 0.55, "name_conf_county_unknown": 0.50,
    "nearest_conf_by_km": [(1.0, 0.70), (3.0, 0.60), (8.0, 0.15)],
    "nearest_conf_ambiguous": 0.35,
}

QUEUE_CSV = paths.REPO_DATA / "queue_storage.csv"
SETTLEMENT_CSV = paths.REPO_DATA / "settlement_points.csv"
SUBSTATIONS_CSV = paths.REPO_DATA / "substations.csv"
OSM_CSV = paths.REPO_DATA / "osm_substations.csv"
EIA_GEN_CSV = paths.REPO_DATA / "eia_generators_tx.csv"
EIA_PLANT_CSV = paths.REPO_DATA / "eia_plants_tx.csv"
OUT_CSV = paths.REPO_DATA / "queue_sub_map.csv"

_STOP = set("""KV KVA SUBSTATION SUBSTATIONS SUB SWITCH SWITCHING SWITCHYARD SWITCHES STATION STN SW SWITCHSTATION TAP TAPPED TAPPING
TO FROM AND OR THE OF AT BUS BUSES NUMBER NO NUM CKT CIRCUIT CCT LINE LINES BETWEEN EXISTING NEW CREATE CREATED LOOPED LOOP ON IN POI
POINT INTERCONNECTION INTERCONNECT TRANSMISSION NAME PSSE ERCOT ONCOR AEP CPS LCRA TNMP XFMR TRANSFORMER SS EXPANSION PROPOSED
PLANNED COUNTY SOUTH NORTH EAST WEST PROJECT DIGIT CODE ONCOR FOR WITH BY A AN DOUBLE SINGLE KVBUS POIB LOAD""".split())
_STOP -= {"SOUTH", "NORTH", "EAST", "WEST"}        # these are parts of station names (Andrews North, Valley South)


# --------------------------------------------------------------------------- text helpers
def _clean_dashes(t):
    return re.sub(r"[–—−�]", "-", t)


def poi_numbers(text, poi_kv=""):
    """Candidate PSS/E bus numbers in a POI text, in order, without voltages and circuit numbers."""
    t = _clean_dashes(str(text))
    t = re.sub(r"(?i)\b(?:ckt|cct|circuit)\b\.?[\s\-:#]*\d+\b", " ", t)         # circuit numbers: Ckt 27, Ckt-2, circuit 84, Ckt#65
    t = re.sub(r"\b\d\)\s", " ", t)                                             # list markers: "either 1) on ..."
    t = re.sub(r"(?i)\b\d+(?:\.\d+)?\s*-?\s*k\s*v\b", " ", t)                   # 138 kV, 345-kV, 138kV
    t = re.sub(r"(?i)\b(?:kv|volts?)\b\s*[:=]?\s*\d+", " ", t)                  # "Substation Transmission kV - 138"
    out = []
    for m in re.finditer(r"(?<![\w.])(\d{1,6})(?![\w.])", t):
        n = m.group(1)
        if int(n) in REF_KV and not re.search(rf"(?i)(?:bus|#|psse)\s*(?:number|no\.?|#)?\s*[:\-]?\s*{n}\b", t):
            continue
        out.append(n)
    return out


def stem_bus_name(name):
    """Readable stem of a PSS/E bus name: 'L_BUCKRA8_1Y' -> 'BUCKRA', 'ELM_MOTT__8' -> 'ELMMOTT', 'TENNCLNY_8' -> 'TENNCLNY'."""
    toks = [t for t in re.split(r"_+", str(name).upper()) if t]
    out = []
    for t in toks:
        t = re.sub(r"\d+[A-Z]?$", "", t)
        if len(t) >= 2:
            out.append(t)
    if len(out) > 1 and out[0] in ("L", "G", "P", "S", "TN", "SL"):
        out = out[1:]
    return "".join(out)


def compact(s):
    return re.sub(r"[^A-Z0-9]", "", str(s).upper())


def phrases(text):
    """Station-name phrases in a POI text (one per end of a tap), compact upper case, numbers and boilerplate removed."""
    t = _clean_dashes(str(text).upper())
    t = re.sub(r"\b\d+(?:\.\d+)?\s*-?\s*K\s*V\b", " ", t)
    t = re.sub(r"\b(?:CKT|CCT|CIRCUIT)\.?\s*#?\s*\d+\b", " ", t)
    t = re.sub(r"[<>\[\]]", " ", t)
    segs = re.split(r"\s+-+\s*|-{2,}|\bTO\b|;|,|\bAND\b|\bBETWEEN\b|\bFROM\b", t)
    out = []
    for seg in segs:
        words = re.findall(r"[A-Z][A-Z0-9_]*", seg)
        words = [w for w in words if w not in _STOP]
        if not words:
            continue
        stems = [stem_bus_name(w) if "_" in w or re.search(r"\d", w) else w for w in words]
        ph = compact("".join(stems))
        if len(ph) >= MIN_NAME_CHARS:
            out.append(ph)
    return list(dict.fromkeys(out))


def _trigrams(s):
    return {s[i:i + 3] for i in range(len(s) - 2)} if len(s) >= 3 else {s}


def fuzzy(a, b):
    """Similarity of two compact strings in [0, 1]: 1 for equal, 0.9 for a long prefix/containment, else the sequence ratio."""
    if a == b:
        return 1.0
    short, long_ = (a, b) if len(a) <= len(b) else (b, a)
    if len(short) >= 5 and (long_.startswith(short) or short in long_) and len(short) / len(long_) >= 0.6:
        return 0.9
    return SequenceMatcher(None, a, b).ratio()


class NameIndex:
    """Name variants of every NP4-160 substation: its code, EIA/OSM/HIFLD names, PSS/E bus-name stems and OSM names whose
    ercot_sub is the substation. A trigram index shortlists candidates; fuzzy() scores them."""

    def __init__(self, subs, sp, osm):
        self.subs = subs.reset_index(drop=True)
        self.county = dict(zip(self.subs["sub"], self.subs["county"].fillna("").astype(str).str.upper()))
        self.kvs = {}
        for s, g in sp.groupby("SUBSTATION"):
            self.kvs[s] = set(g["VOLTAGE_LEVEL"].astype(float))
        var = {}

        def add(sub, text, w):
            c = compact(text)
            if len(c) >= MIN_NAME_CHARS:
                var.setdefault(sub, {})
                var[sub][c] = max(var[sub].get(c, 0.0), w)

        for _, r in self.subs.iterrows():
            add(r["sub"], r["sub"], 0.95)
            if isinstance(r.get("name_guess"), str):
                nm = re.sub(r"(?i)\b(substation|switch(ing)?( station| yard)?|station|plant|power|bess|battery|energy storage)\b", " ", r["name_guess"])
                add(r["sub"], nm, 0.9)
        for s, g in sp.groupby("SUBSTATION"):
            for nme in g["PSSE_BUS_NAME"].dropna().unique():
                add(s, stem_bus_name(nme), 1.0)
        for _, r in osm[osm["ercot_sub"].notna()].iterrows():
            for col in ("name", "ref"):
                if isinstance(r.get(col), str):
                    nm = re.sub(r"(?i)\b(substation|switch(ing)?( station| yard)?|station)\b", " ", r[col])
                    add(r["ercot_sub"], nm, 0.9)
        self.variants = [(s, c, w) for s, d in var.items() for c, w in d.items()]
        self._by_sub = {}
        for s, c, w in self.variants:
            self._by_sub.setdefault(s, []).append((s, c, w))
        self.tri = {}
        for i, (_, c, _) in enumerate(self.variants):
            for g in _trigrams(c):
                self.tri.setdefault(g, []).append(i)

    def names_match(self, sub, phrase_list):
        """True if any name variant of `sub` reaches SHORT_NUMBER_NAME_SCORE against a phrase of the text."""
        for _, c, w in self._by_sub.get(sub, ()):
            for ph in phrase_list:
                if fuzzy(ph, c) * w >= SHORT_NUMBER_NAME_SCORE:
                    return True
        return False

    def candidates(self, phrase, top=60):
        score = {}
        for g in _trigrams(phrase):
            for i in self.tri.get(g, ()):
                score[i] = score.get(i, 0) + 1
        best = sorted(score.items(), key=lambda kv: -kv[1])[:top]
        return [i for i, _ in best]

    def match(self, phrase_list, county="", poi_kv=None):
        """Best (sub, score, margin, county_state) over the phrases. Score = fuzzy x variant weight."""
        best = {}
        for ph in phrase_list:
            for i in self.candidates(ph):
                s, c, w = self.variants[i]
                sc = fuzzy(ph, c) * w
                if sc > best.get(s, (0, ""))[0]:
                    best[s] = (sc, ph)
        if not best:
            return None
        def eff(item):                                        # fuzzy score, plus a bonus for sitting in the queue county
            s_, (sc_, _) = item
            return sc_ + (COUNTY_NAME_BONUS if county and self.county.get(s_, "") == county.upper() else 0.0)

        ranked = sorted(best.items(), key=lambda kv: -eff(kv))
        top = ranked[0]
        sub, (sc, ph) = top
        others = [eff(r) for r in ranked if r[0] != sub]
        margin = eff(top) - (others[0] if others else 0.0)
        c_sub = self.county.get(sub, "")
        state = "unknown" if (not c_sub or not county) else ("ok" if c_sub == county.upper() else "mismatch")
        return sub, sc, margin, state, ph


# --------------------------------------------------------------------------- inputs
def read_csv(path, **kw):
    """pandas default NA handling turns the real ERCOT substation code 'NA' into a missing value; only blanks are missing here."""
    return pd.read_csv(path, keep_default_na=False, na_values=[""], low_memory=False, **kw)


def load_inputs():
    q = pd.read_csv(QUEUE_CSV, dtype=str, keep_default_na=False)
    q["mw"] = pd.to_numeric(q["mw"], errors="coerce")
    q = q[q["tech"] == "storage"].reset_index(drop=True)
    sp = read_csv(SETTLEMENT_CSV)
    subs = read_csv(SUBSTATIONS_CSV)
    subs = subs[subs["in_np4160"].astype(str).str.lower().eq("true")].reset_index(drop=True)
    osm = read_csv(OSM_CSV)
    osm = osm[osm["ercot_sub"].isin(set(subs["sub"]))].reset_index(drop=True)      # only NP4-160 substations are valid targets
    gen = read_csv(EIA_GEN_CSV)
    plants = read_csv(EIA_PLANT_CSV)
    return dict(q=q, sp=sp, subs=subs, osm=osm, gen=gen, plants=plants)


class Lookups:
    def __init__(self, data):
        sp, subs = data["sp"], data["subs"]
        self.bus2sub = dict(zip(sp["PSSE_BUS_NUMBER"].astype(str), sp["SUBSTATION"]))
        self.bus2kv = dict(zip(sp["PSSE_BUS_NUMBER"].astype(str), sp["VOLTAGE_LEVEL"].astype(float)))
        self.sub_kv = dict(zip(subs["sub"], subs["kv_max"].astype(float)))
        self.sub_county = dict(zip(subs["sub"], subs["county"].fillna("").astype(str).str.upper()))
        self.sub_conf = dict(zip(subs["sub"], subs["match_conf"].astype(float)))
        self.index = NameIndex(subs, sp, data["osm"])


# --------------------------------------------------------------------------- queue
def _stage(status, ia_signed):
    s = str(status)
    if "Small Gen" in s:
        return "small_gen_model_ready"
    if "IA" in s and "No IA" not in s:
        return "IA_signed"
    if "FIS Completed" in s:
        return "FIS_completed_no_IA"
    if "FIS Started" in s:
        return "FIS_started_no_IA"
    return "other"


def map_queue_row(r, L, use_numbers=True):
    """One queue row -> dict(sub, sub_alt, method, conf, is_tap, note)."""
    text, county, kv = r["poi_text"], str(r["county"]).upper(), r["poi_kv"]
    poi_kv = float(kv) if str(kv).strip() not in ("", "nan") else None
    is_tap = bool(re.search(r"(?i)\b(tap|tapped|tapping|looped|loop)\b", str(text)))
    if use_numbers:
        res = []
        nums = poi_numbers(text, kv)
        ph_all = phrases(text) if any(int(n) <= SHORT_NUMBER_MAX for n in nums) else []
        for n in nums:
            s = L.bus2sub.get(n)
            if s is not None:
                if int(n) <= SHORT_NUMBER_MAX and not L.index.names_match(s, ph_all):
                    continue                                       # "#1", "Ckt 2": a small number is a bus only with name evidence
                ok_kv = poi_kv is None or abs(L.bus2kv[n] - poi_kv) <= KV_TOL
                if not any(s == x[0] for x in res):
                    res.append((s, ok_kv, n))
        if res:
            subs_found = [x[0] for x in res]
            if len(res) >= 2 or (is_tap and len(res) == 1):
                # tap: pick the end in the queue county, else the higher kV, else the first listed
                ranked = sorted(res, key=lambda x: (L.sub_county.get(x[0], "") != county, -L.sub_kv.get(x[0], 0), subs_found.index(x[0])))
                p = ranked[0]
                conf = CONF_TAP_BOTH_ENDS if len(res) >= 2 else CONF_TAP_ONE_END
                alt = ranked[1][0] if len(ranked) > 1 else ""
                return dict(sub=p[0], sub_alt=alt, method="psse_bus_tap" if len(res) >= 2 else "psse_bus_tap_one_end",
                            conf=conf - (COUNTY_MISMATCH_PENALTY if L.sub_county.get(p[0], "") not in ("", county) else 0.0),
                            is_tap=True, note=f"tap between {' and '.join(subs_found)}")
            s, ok_kv, n = res[0]
            c = CONF_BUS_SINGLE if ok_kv else CONF_BUS_KV_MISMATCH
            sc = L.sub_county.get(s, "")
            if sc and sc != county:
                c -= COUNTY_MISMATCH_PENALTY
            return dict(sub=s, sub_alt="", method="psse_bus", conf=c, is_tap=False,
                        note=f"bus {n}" + ("" if ok_kv else f" is {L.bus2kv[n]:.0f} kV, text says {poi_kv:.0f}"))
    ph = phrases(text)
    if ph:
        m = L.index.match(ph, county, poi_kv)
        if m is not None:
            sub, sc, margin, state, p = m
            if sc >= NAME_ACCEPT_SCORE and margin >= NAME_AMBIGUITY_MARGIN:
                if state == "mismatch":                            # the best-named station lies in another county: not accepted
                    return dict(sub="", sub_alt="", method="unmapped", conf=0.0, is_tap=is_tap,
                                note=f"name '{p}' matches {sub} (score {sc:.2f}) but that station is in another county")
                cal = CALIBRATION
                c = cal["name_conf_county_ok"] if state == "ok" else cal["name_conf_county_unknown"]
                return dict(sub=sub, sub_alt="", method="name_county", conf=c, is_tap=is_tap,
                            note=f"name '{p}' score {sc:.2f} margin {margin:.2f} county {state}")
    return dict(sub="", sub_alt="", method="unmapped", conf=0.0, is_tap=is_tap, note="no bus number in the model, no confident name match")


def map_queue(data=None, L=None):
    data = data or load_inputs()
    L = L or Lookups(data)
    rows = []
    for _, r in data["q"].iterrows():
        m = map_queue_row(r, L)
        sub = m["sub"]
        rows.append(dict(
            inr_or_plant=r["inr"], source="queue", name=r["project_name"], status=r["status"], stage=_stage(r["status"], r["ia_signed"]),
            mw=r["mw"], cod=r["projected_cod"], county=r["county"], poi_text=r["poi_text"], poi_kv=r["poi_kv"], colocation=r["colocation"],
            is_slf=r["is_slf"], sub=sub, sub_alt=m["sub_alt"], method=m["method"], conf=round(m["conf"], 3), is_tap=m["is_tap"],
            sub_kv=L.sub_kv.get(sub, np.nan), sub_county=L.sub_county.get(sub, ""), note=m["note"]))
    return pd.DataFrame(rows)


def validate_name_match(data=None, L=None):
    """Hit rate of the name method on queue rows whose bus-number answer is a single existing station (the bus number is
    ignored by the name method, which sees only the words). Returns a DataFrame by county state and score bucket."""
    data = data or load_inputs()
    L = L or Lookups(data)
    recs = []
    for _, r in data["q"].iterrows():
        truth = map_queue_row(r, L, use_numbers=True)
        if truth["method"] != "psse_bus" or truth["conf"] < 0.9:
            continue
        ph = phrases(r["poi_text"])
        m = L.index.match(ph, str(r["county"]), None) if ph else None
        if m is None:
            recs.append(dict(inr=r["inr"], truth=truth["sub"], pred="", score=0.0, margin=0.0, state="none", mw=r["mw"]))
            continue
        sub, sc, margin, state, p = m
        recs.append(dict(inr=r["inr"], truth=truth["sub"], pred=sub, score=sc, margin=margin, state=state, mw=r["mw"]))
    return pd.DataFrame(recs)


# --------------------------------------------------------------------------- EIA
def _haversine(lat1, lon1, lat2, lon2):
    la1, la2 = np.radians(lat1), np.radians(lat2)
    a = np.sin((la2 - la1) / 2) ** 2 + np.cos(la1) * np.cos(la2) * np.sin(np.radians(np.asarray(lon2) - lon1) / 2) ** 2
    return 2 * 6371.0088 * np.arcsin(np.sqrt(a))


class EiaMapper:
    def __init__(self, data):
        subs, plants = data["subs"], data["plants"]
        self.plants = plants.set_index("plant_code")
        self.links = {}
        for _, r in subs[subs["eia_plant_codes"].notna()].iterrows():
            for c in str(r["eia_plant_codes"]).split(";"):
                c = c.strip()
                if c:
                    self.links.setdefault(int(float(c)), []).append(r["sub"])
        self.subs = subs
        self.placed = subs[subs["lat"].notna() & (subs["kv_max"].astype(float) >= EIA_MIN_KV) & (subs["match_conf"].astype(float) >= 0.5)].reset_index(drop=True)
        self.sub_row = subs.set_index("sub")
        from scipy.spatial import cKDTree
        la, lo = np.radians(self.placed["lat"].astype(float).to_numpy()), np.radians(self.placed["lon"].astype(float).to_numpy())
        self.xyz = np.stack([np.cos(la) * np.cos(lo), np.cos(la) * np.sin(lo), np.sin(la)], 1)
        self.tree = cKDTree(self.xyz)
        self.kvlist = [set(float(x) for x in str(k).replace(";", ",").split(",") if x.strip()) for k in self.placed["kv_list"]]

    def link(self, plant_code):
        subs = self.links.get(int(plant_code), [])
        subs = [s for s in subs if s in self.sub_row.index and float(self.sub_row.at[s, "kv_max"]) >= EIA_MIN_KV]
        if not subs:
            return None
        # rank: placed with confidence, then voltage, then confidence
        def key(s):
            r = self.sub_row.loc[s]
            return (-float(r["match_conf"]), -float(r["kv_max"]))
        subs = sorted(subs, key=key)
        return subs

    def nearest(self, lat, lon, grid_kv=None, exclude=()):
        """(sub, km, grid_kv_matches, n_ties) of the nearest placed substation of at least EIA_MIN_KV; n_ties counts substations
        within EIA_TIE_KM of that distance (several ERCOT codes are often placed at one plant point). When the plant's grid voltage is
        known, a substation that carries that voltage wins if it is at most EIA_KV_PREFER_EXTRA_KM further than the nearest one."""
        la, lo = np.radians(lat), np.radians(lon)
        q = np.array([np.cos(la) * np.cos(lo), np.cos(la) * np.sin(lo), np.sin(la)])
        _, idx = self.tree.query(q, k=min(12, len(self.placed)))
        cands = []
        for j in np.atleast_1d(idx):
            j = int(j)
            s = self.placed.at[j, "sub"]
            if s in exclude:
                continue
            km = float(_haversine(lat, lon, float(self.placed.at[j, "lat"]), float(self.placed.at[j, "lon"])))
            ok_kv = grid_kv is not None and any(abs(k - grid_kv) <= 1.0 for k in self.kvlist[j])
            cands.append((km, s, ok_kv))
        if not cands:
            return None
        cands.sort()
        best = cands[0]
        if grid_kv is not None:
            match = [c for c in cands if c[2]]
            if match and match[0][0] <= best[0] + EIA_KV_PREFER_EXTRA_KM:
                best = match[0]
        n_ties = sum(1 for c in cands if c[0] <= best[0] + EIA_TIE_KM)
        return best[1], best[0], best[2], n_ties


def nearest_conf(km, n_ties=1):
    for lim, c in CALIBRATION["nearest_conf_by_km"]:
        if km <= lim:
            return min(c, CALIBRATION["nearest_conf_ambiguous"]) if n_ties > 1 else c
    return 0.0


def map_eia(data=None, mapper=None, only_ercot=False):
    data = data or load_inputs()
    mp = mapper or EiaMapper(data)
    g = data["gen"]
    g = g[g["class"] == "battery"].copy()
    if only_ercot:
        g = g[g["balancing_authority"] == "ERCO"]
    pl = data["plants"].set_index("plant_code")
    rows = []
    for _, r in g.iterrows():
        pc = int(r["plant_code"])
        grid_kv = float(pl.at[pc, "grid_kv"]) if pc in pl.index and pd.notna(pl.at[pc, "grid_kv"]) else None
        sub, alt, method, conf, note = "", "", "unmapped", 0.0, ""
        in_ercot = str(r["balancing_authority"]) == "ERCO"
        links = mp.link(pc)
        if links:
            sub, alt = links[0], ";".join(links[1:])
            method = "eia_plant_link"
            conf = CONF_EIA_LINK_SINGLE if len(links) == 1 else CONF_EIA_LINK_MULTI
            note = f"plant {pc} listed by {len(links)} substation(s)"
        elif in_ercot and pd.notna(r["lat"]):
            nb = mp.nearest(float(r["lat"]), float(r["lon"]), grid_kv)
            if nb is not None and nb[1] <= EIA_MAX_DIST_KM:
                sub, method, conf = nb[0], "nearest_sub", nearest_conf(nb[1], nb[3])
                note = (f"nearest >= {EIA_MIN_KV:.0f} kV placed substation {nb[1]:.1f} km" + (" (grid voltage matches)" if nb[2] and grid_kv else "")
                        + (f"; {nb[3]} substations within {EIA_TIE_KM * 1000:.0f} m of that distance, pick is ambiguous" if nb[3] > 1 else ""))
        elif not in_ercot:
            note = f"outside ERCOT (balancing authority {r['balancing_authority']})"
        sc = mp.sub_row.loc[sub] if sub in mp.sub_row.index else None
        yr = int(r["year"]) if pd.notna(r["year"]) else None
        mo = int(r["month"]) if pd.notna(r["month"]) else None
        rows.append(dict(
            inr_or_plant=f"{pc}:{r['gen_id']}", source="eia", name=r["plant_name"], status=r["status"], stage=str(r["status_group"]),
            mw=r["mw"], cod=(f"{yr:04d}-{mo:02d}" if yr and mo else (str(yr) if yr else "")), county=r["county"], poi_text="",
            poi_kv=grid_kv if grid_kv is not None else "", colocation="", is_slf="", sub=sub, sub_alt=alt, method=method,
            conf=round(conf, 3), is_tap=False, sub_kv=float(sc["kv_max"]) if sc is not None else np.nan,
            sub_county=str(sc["county"]).upper() if sc is not None and pd.notna(sc["county"]) else "",
            note=note + ("" if in_ercot else "") ))
    return pd.DataFrame(rows)


def validate_nearest(data=None):
    """Hit rate of the nearest-substation rule on plants that have an EIA plant link whose substation was placed
    independently of the plant (match_method osm_name): is the nearest substation the linked one?"""
    data = data or load_inputs()
    mp = EiaMapper(data)
    g = data["gen"]
    g = g[(g["class"] == "battery") & (g["balancing_authority"] == "ERCO")]
    pl = data["plants"].set_index("plant_code")
    recs = []
    for _, r in g.iterrows():
        pc = int(r["plant_code"])
        links = mp.link(pc)
        if not links or pd.isna(r["lat"]):
            continue
        rows = mp.sub_row.loc[links]
        ind = rows[(rows["match_method"] == "osm_name") & (rows["match_conf"].astype(float) >= 0.7)]
        if ind.empty:
            continue
        grid_kv = float(pl.at[pc, "grid_kv"]) if pc in pl.index and pd.notna(pl.at[pc, "grid_kv"]) else None
        nb = mp.nearest(float(r["lat"]), float(r["lon"]), grid_kv)
        if nb is None:
            continue
        recs.append(dict(plant=pc, truth=";".join(ind.index), pred=nb[0], km=nb[1], ties=nb[3], hit=nb[0] in set(ind.index), mw=r["mw"]))
    return pd.DataFrame(recs)


# --------------------------------------------------------------------------- overlap between EIA planned units and the queue
_NAME_DROP = re.compile(r"(?i)\b(llc|lp|inc|energy|storage|battery|bess|ess|solar|wind|hybrid|project|facility|power|center|system|systems|"
                        r"the|farm|ranch|station|phase|pv|and|i|ii|iii)\b")


def _core_name(n):
    return compact(_NAME_DROP.sub(" ", str(n)))


def flag_overlaps(df):
    """EIA-860M planned units are mostly the same projects as the GIS queue. Adds `likely_duplicate_of` (the INR) and
    `overlap_conf` to planned EIA rows, so the saturation step does not count a project twice.

    Match: the queue row maps to the same substation (primary or alternate end) and either the project names agree (fuzzy
    >= OVERLAP_NAME_SCORE on the names without generic words) with MW within OVERLAP_MW_TOL, or the MW agree within
    OVERLAP_MW_TOL_TIGHT. ASSUMPTION-grade; confidence 0.8 for name + MW, 0.5 for MW only."""
    df = df.copy()
    df["likely_duplicate_of"] = ""
    df["overlap_conf"] = 0.0
    q = df[(df["source"] == "queue") & (df["conf"] >= 0.5) & (df["mw"] > 0)]
    by_sub = {}
    for i, r in q.iterrows():
        for s_ in (r["sub"], r["sub_alt"]):
            if s_:
                by_sub.setdefault(s_, []).append(i)
    e = df[(df["source"] == "eia") & (df["stage"] == "planned") & (df["conf"] >= 0.5)]
    for i, r in e.iterrows():
        best, best_c = "", 0.0
        for j in by_sub.get(r["sub"], []):
            qr = df.loc[j]
            rel = abs(qr["mw"] - r["mw"]) / max(qr["mw"], r["mw"], 1e-9)
            name_sc = SequenceMatcher(None, _core_name(r["name"]), _core_name(qr["name"])).ratio() if _core_name(r["name"]) and _core_name(qr["name"]) else 0.0
            if name_sc >= OVERLAP_NAME_SCORE and rel <= OVERLAP_MW_TOL:
                c = 0.8
            elif rel <= OVERLAP_MW_TOL_TIGHT:
                c = 0.5
            else:
                continue
            if c > best_c:
                best, best_c = qr["inr_or_plant"], c
        df.at[i, "likely_duplicate_of"], df.at[i, "overlap_conf"] = best, best_c
    return df


# --------------------------------------------------------------------------- build
def build(data=None, verbose=True):
    data = data or load_inputs()
    L = Lookups(data)
    qm = map_queue(data, L)
    em = map_eia(data)
    out = pd.concat([qm, em], ignore_index=True)
    out["mw"] = out["mw"].astype(float)
    out = flag_overlaps(out)
    if verbose:
        print(summary(out).to_string())
    return out


def summary(df):
    rows = []
    for src, g in df.groupby("source"):
        tot = g["mw"].sum()
        for thr in (0.0001, 0.5, 0.7, 0.9):
            m = g[g["conf"] >= thr]
            rows.append(dict(source=src, min_conf=thr if thr > 0.001 else 0.0, rows=len(m), of=len(g), mw=round(m["mw"].sum(), 1), of_mw=round(tot, 1),
                             share_mw=round(m["mw"].sum() / tot, 4) if tot else np.nan))
    return pd.DataFrame(rows)


COLUMN_LABELS = [
    ("inr_or_plant", "REAL", "ERCOT GIS report INR (queue) / EIA plant_code:generator_id", "key; queue rows are the storage projects of the Sep 2026 GIS report, eia rows are EIA-860/860M batteries"),
    ("source", "REAL", "queue | eia", ""),
    ("name", "REAL", "GIS project name / EIA plant name", ""),
    ("status", "REAL", "GIS status text / EIA-860M status code text", ""),
    ("stage", "DERIVED", "status", "queue: IA_signed | FIS_started_no_IA | FIS_completed_no_IA | small_gen_model_ready | other; eia: operating | planned. IA signed is a firm commitment (rules.md (d))"),
    ("mw", "REAL", "GIS report MW / EIA nameplate MW", "0 for 64 queue rows whose battery sits inside a solar or wind nameplate (is_slf); their real size is not published"),
    ("cod", "REAL", "GIS projected COD (developer's date, slips) / EIA operating or planned operation month", ""),
    ("county", "REAL", "GIS / EIA county", ""),
    ("poi_text", "REAL", "GIS report, verbatim", "queue rows only"),
    ("poi_kv", "REAL", "GIS POI kV parsed by agent 1b / EIA plant grid voltage", ""),
    ("colocation", "DERIVED", "GIS co-location class (agent 1b)", "stand_alone | solar | wind | thermal"),
    ("is_slf", "REAL", "GIS self-limiting facility flag", ""),
    ("sub", "DERIVED", "queue_map.py", "ERCOT NP4-160 SUBSTATION; blank when unmapped. Queue: the existing station, or for a tap the end chosen by county / voltage (the other end is sub_alt). EIA: the substation of the plant's resource node unit or the nearest transmission substation"),
    ("sub_alt", "DERIVED", "queue_map.py", "queue tap: the other end of the tapped line; eia: other substations listing the plant. Split the MW between sub and sub_alt if both matter"),
    ("method", "DERIVED", "queue_map.py", "psse_bus | psse_bus_tap | psse_bus_tap_one_end | name_county | eia_plant_link | nearest_sub | unmapped"),
    ("conf", "ASSUMPTION", "queue_map.py", "0-1. psse_bus: ASSUMPTION constants (0.95 / 0.75 / tap 0.60 / 0.50, -0.05 for a county mismatch). name_county and nearest_sub: observed hit rates on rows with a known answer (notes/phase3a.md). Not a calibrated probability for the other methods"),
    ("is_tap", "DERIVED", "poi_text", "the project taps a line (a new switchyard, not an existing station)"),
    ("sub_kv", "REAL", "data/substations.csv kv_max", "highest voltage of the mapped substation"),
    ("sub_county", "DERIVED", "data/substations.csv", "county of the mapped substation's coordinates"),
    ("note", "DERIVED", "queue_map.py", "evidence for the mapping"),
    ("likely_duplicate_of", "ASSUMPTION", "queue_map.py flag_overlaps", "planned EIA rows only: the queue INR that is probably the same project (same substation, similar MW, and a similar name when overlap_conf is 0.8). Do not add both to the saturation MW"),
    ("overlap_conf", "ASSUMPTION", "queue_map.py flag_overlaps", "0.8 name and MW agree; 0.5 only the MW and substation agree; 0 no candidate"),
]


def write_queue_sub_map(path=None, verbose=True):
    path = path or OUT_CSV
    df = build(verbose=verbose)
    df.to_csv(path, index=False)
    lab = pd.DataFrame(COLUMN_LABELS, columns=["column", "label", "source", "note"])
    missing = set(df.columns) - set(lab["column"])
    if missing:
        raise RuntimeError(f"columns without a label: {sorted(missing)}")
    lab.to_csv(path.with_name(path.stem + ".labels.csv"), index=False)
    return df


def calibrate(data=None, verbose=True):
    """Re-measure the numbers behind CALIBRATION. Returns a dict of the measurements."""
    data = data or load_inputs()
    L = Lookups(data)
    v = validate_name_match(data, L)
    v["hit"] = v["pred"] == v["truth"]
    acc = v[(v["score"] >= NAME_ACCEPT_SCORE) & (v["margin"] >= NAME_AMBIGUITY_MARGIN)]
    name = {st: {"n": int(len(g)), "hit": float(g["hit"].mean())} for st, g in acc.groupby("state")}
    # false accepts: drop the true station from the index and see what is still accepted
    idx = L.index
    fp = {}
    for _, r in data["q"].iterrows():
        truth = map_queue_row(r, L, use_numbers=True)
        if truth["method"] != "psse_bus" or truth["conf"] < 0.9:
            continue
        ph = phrases(r["poi_text"])
        if not ph:
            continue
        saved_v, saved_t = idx.variants, idx.tri
        keep = [x for x in saved_v if x[0] != truth["sub"]]
        idx.variants = keep
        tri = {}
        for i, (_, c, _) in enumerate(keep):
            for g in _trigrams(c):
                tri.setdefault(g, []).append(i)
        idx.tri = tri
        m = idx.match(ph, str(r["county"]), None)
        idx.variants, idx.tri = saved_v, saved_t
        state = "none" if m is None else m[3]
        ok = m is not None and m[1] >= NAME_ACCEPT_SCORE and m[2] >= NAME_AMBIGUITY_MARGIN
        d = fp.setdefault(state, [0, 0])
        d[0] += 1
        d[1] += int(ok)
    fp = {k: {"n": a, "accepted": b, "rate": b / a} for k, (a, b) in fp.items()}
    nv = validate_nearest(data)
    nv["bucket"] = pd.cut(nv["km"], [-1, 1, 3, 8, 15, 1e9])
    nv["tie"] = np.where(nv["ties"] > 1, "ambiguous", "unique")
    near = {f"{t} {k}": {"n": int(len(g)), "hit": float(g["hit"].mean())} for (t, k), g in nv.groupby(["tie", "bucket"], observed=True)}
    out = {"name_hit_rate_by_county_state": name, "name_false_accept_by_county_state": fp, "nearest_hit_rate_by_km": near,
           "n_name_validation_rows": int(len(v)), "n_nearest_validation_plants": int(len(nv))}
    if verbose:
        import json
        print(json.dumps(out, indent=1))
    return out
