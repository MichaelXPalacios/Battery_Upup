"""Connection cost class, cost range, allowance and lead time for a battery at each ERCOT substation.

For every NP4-160 substation of 69 kV or more this module picks how a transmission-connected battery would reach it
and prices that, in 2024 dollars, from the MISO Transmission Cost Estimation Guide rows in data/unit_costs.csv:

  new_bay         one more breaker position at the existing station (the station is a through bus: at least
                  MIN_LINE_DEGREE line terminations in the line graph, placed with match_conf >= MIN_CONF_PLACED, or a
                  suitable station within COLOCATED_KM under another ERCOT code); plus a short tie from the battery
                  site to the new bay;
  new_switchyard  a new 4-position ring-bus switchyard that taps a passing line of at least TAP_MIN_KV within TAP_MAX_KM,
                  plus a short tie;
  gen_tie         a line of X km from the battery site to the nearest suitable station, plus a new bay there.

The cheapest feasible option by mid cost is taken. A station that cannot host a bay (a stub, an unlinked or unplaced
station) is priced as `new_bay` only when nothing else can be computed; that default is flagged in class_conf and basis.

Money. cost_* are the interconnection facilities (bay / switchyard / gen-tie) in 2024 dollars; `allowance_musd` is the
interconnection-facilities allowance of 16 TAC 25.195(f)(3) for agreements signed after 31 Dec 2025 ($14 M up to 138 kV,
$20 M above, 2024 dollars; notes/rules.md (d)); `owner_share_*` is the part above it that the generator pays. The
battery's own step-up transformer is the generator's cost under 25.195(f)(1) and is reported apart (`gsu_cost_musd`).
Lead time = FIS (request to interconnection agreement) + the longer of the construction of the class and the delivery and
installation of the step-up transformer.

Every constant is a named value with its label; the cost numbers themselves are read from data/unit_costs.csv (REAL rows
from MISO MTEP24, some cells UNVERIFIED there; a row used here that carries UNVERIFIED in its note sets `uses_unverified`).
Nothing here is a quote: it is a screening-level class and range.
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass

import numpy as np
import pandas as pd

from . import paths

# --------------------------------------------------------------------------- constants (label: PLAN.md)
USD_YEAR = 2024                       # REAL: price year of the MISO MTEP24 guide rows used (unit_costs.year_usd)
ESCALATION_PER_YEAR = 0.04            # ASSUMPTION: MISO escalates about 4%/yr (unit_costs.labels.csv); NOT applied to the table, exported for 3b
REF_MW = 100.0                        # ASSUMPTION: reference battery size for the table, middle of PROMPT.md's 10-300 MW
MIN_KV_CANDIDATE = 69.0               # REAL: PROMPT.md Tier 1 scope, 69-345 kV substations
MIN_CONF_PLACED = 0.5                 # REAL: notes/phase1-geo.md, "use match_conf >= 0.5 for anything that ranks sites"
MIN_LINE_DEGREE = 2                   # ASSUMPTION: a through bus (>= 2 line terminations) is taken to have room for a bay
TAP_MIN_KV = 138.0                    # ASSUMPTION: a new switchyard taps a line of at least 138 kV (69 kV lines are too weak for 100 MW)
TAP_MAX_KM = 3.0                      # ASSUMPTION: a tap is considered only if a suitable line passes within this distance
GEN_TIE_MAX_KM = 50.0                 # ASSUMPTION: longest gen-tie considered; beyond it the row is flagged long_tie
ROUTE_FACTOR = 1.25                   # ASSUMPTION: routed length / straight-line length of a new line
SITE_TIE_KM = 0.5                     # ASSUMPTION: tie from the battery site to a new bay or switchyard next to it
COLOCATED_KM = 0.5                    # ASSUMPTION: a suitable station this close is the same physical site under another ERCOT code (ERCOT splits some stations by owner / voltage)
KM_PER_MILE = 1.609344                # REAL: unit conversion
EARTH_RADIUS_KM = 6371.0088           # REAL: mean Earth radius
# Allowance of 16 TAC 25.195(f)(3)(A)-(B) for storage (only the allowance, no load allowances), in 2024 dollars.
ALLOWANCE_LE_138_MUSD = 14.0          # REAL: 16 TAC 25.195(f)(3), notes/rules.md (d)
ALLOWANCE_GT_138_MUSD = 20.0          # REAL: same
ALLOWANCE_KV_SPLIT = 138.0            # REAL: "138 kV or less" / "above 138 kV"
# Voltage class used to look up a cost row. The guide has bay and substation rows for 138 and 345 kV and line rows for 69,
# 138 and 345 kV. ASSUMPTION: 69, 115 and 161 kV take the 138 kV bay/substation cost (conservative), 230 and 500 kV the 345 kV
# one; lines take their own row where it exists and the next class up otherwise.
BAY_COST_KV = {69: 138, 115: 138, 138: 138, 161: 138, 230: 345, 345: 345, 500: 345}   # ASSUMPTION: which guide row prices a bay or substation at each kV
LINE_COST_KV = {69: 69, 115: 138, 138: 138, 161: 138, 230: 345, 345: 345, 500: 345}   # ASSUMPTION: which guide row prices a new line at each kV
# Lead time, months.
FIS_MONTHS = (16.0, 20.5, 28.0)       # DERIVED: quartiles and median of FIS request -> IA signed, storage, GIS Sep 2026 (rules.md (d))
IA_TO_COD_MEDIAN_MONTHS = 35.6        # DERIVED: median IA signed -> projected COD, storage, GIS Sep 2026 (rules.md (d)); a cross-check only
BUILD_MONTHS_BAY = (12.0, 18.0, 24.0)           # ASSUMPTION: engineering, breaker delivery and construction of one bay
BUILD_MONTHS_SWITCHYARD = (18.0, 24.0, 30.0)    # ASSUMPTION: a new 4-position switchyard
GEN_TIE_MONTHS_BASE = 9.0             # ASSUMPTION: design and right-of-way for a gen-tie, plus the bay at its end
GEN_TIE_MONTHS_PER_KM = 0.3           # ASSUMPTION: construction time grows with length
XFMR_INSTALL_MONTHS = 6.0             # ASSUMPTION: set, test and energise the step-up after delivery
MPT_LEAD_MONTHS = (9.0, 12.0, 18.0)   # ASSUMPTION: delivery of a medium-size step-up transformer (below LPT_MIN_MVA)
LPT_MIN_MVA = 100.0                   # ASSUMPTION: a step-up of at least 100 MVA at 138 kV or more is a large power transformer
GSU_MVA_PER_MW = 1.1                  # ASSUMPTION: step-up rating = 110% of the battery's MW

UNIT_COSTS_CSV = paths.REPO_DATA / "unit_costs.csv"
SUBSTATIONS_CSV = paths.REPO_DATA / "substations.csv"
LINES_CSV = paths.REPO_DATA / "lines.csv"
LINES_GEOJSON = paths.DERIVED / "lines.geojson"
OUT_CSV = paths.REPO_DATA / "connection_costs.csv"

ITEM_LINE = "new single-circuit line"
ITEM_BAY = "substation: add one bay (ring bus)"
ITEM_SUBSTATION = "substation: new 4-position substation (ring bus)"
ITEM_GSU = "battery energy storage for transmission use: inverter + step-up"
ITEM_LPT_AVG = "large power transformer lead time, average"
ITEM_LPT_QUOTED = "large power transformer lead time, commonly quoted"


# --------------------------------------------------------------------------- unit costs
@dataclass(frozen=True)
class UnitCost:
    low: float
    mid: float
    high: float
    unverified: bool
    key: str


def load_unit_costs(path=None):
    """{(item, kv string): UnitCost} from data/unit_costs.csv. Blank cells fall back to the mid cell."""
    u = pd.read_csv(path or UNIT_COSTS_CSV, dtype=str, keep_default_na=False)
    out = {}
    for _, r in u.iterrows():
        def num(c, default):
            return float(r[c]) if r[c] != "" else default
        mid = num("mid_usd", float("nan"))
        low, high = num("low_usd", mid), num("high_usd", mid)
        mid = mid if not math.isnan(mid) else low
        out[(r["item"], r["kv"])] = UnitCost(low, mid, high, "UNVERIFIED" in r["note"], f"{r['item']} | {r['kv']}")
    return out


def _kv_class(table, kv):
    """Nearest key of a kV-class table at or below the voltage (so 120 kV takes the 115 kV class)."""
    ks = sorted(table)
    best = ks[0]
    for k in ks:
        if kv >= k - 1e-9:
            best = k
    return table[best]


# --------------------------------------------------------------------------- geometry
def _xyz(lat, lon):
    la, lo = np.radians(np.asarray(lat, float)), np.radians(np.asarray(lon, float))
    return EARTH_RADIUS_KM * np.stack([np.cos(la) * np.cos(lo), np.cos(la) * np.sin(lo), np.sin(la)], -1)


def haversine_km(lat1, lon1, lat2, lon2):
    la1, la2 = np.radians(lat1), np.radians(lat2)
    dphi, dl = la2 - la1, np.radians(np.asarray(lon2, float) - np.asarray(lon1, float))
    a = np.sin(dphi / 2) ** 2 + np.cos(la1) * np.cos(la2) * np.sin(dl / 2) ** 2
    return 2 * EARTH_RADIUS_KM * np.arcsin(np.sqrt(a))


class LineIndex:
    """Nearest-line queries over the OSM line geometry (derived/lines.geojson): KD-tree on vertices, refined on the two
    segments next to the nearest vertex. Only lines of at least `min_kv` are indexed."""

    def __init__(self, path=None, min_kv=TAP_MIN_KV):
        from scipy.spatial import cKDTree
        gj = json.loads(open(path or LINES_GEOJSON, encoding="utf-8").read())
        pts, line_id, kv, pos = [], [], [], []
        self.ids = []
        for f in gj["features"]:
            k = f["properties"].get("kv") or 0.0
            if k < min_kv:
                continue
            c = np.asarray(f["geometry"]["coordinates"], float)           # (n, 2) lon, lat
            self.ids.append(f["properties"]["line_id"])
            pts.append(c)
            line_id.append(np.full(len(c), len(self.ids) - 1))
            kv.append(np.full(len(c), float(k)))
            pos.append(np.arange(len(c)))
        self.coords = np.concatenate(pts, 0)
        self.line_of = np.concatenate(line_id)
        self.kv_of = np.concatenate(kv)
        self.pos_of = np.concatenate(pos)
        self.tree = cKDTree(_xyz(self.coords[:, 1], self.coords[:, 0]))

    def nearest(self, lat, lon):
        """Per point: (distance km to the nearest line, its line_id, its kV). Distance refined onto the segments."""
        lat, lon = np.asarray(lat, float), np.asarray(lon, float)
        d, k = self.tree.query(_xyz(lat, lon), k=1)
        out_d, out_id, out_kv = np.empty(len(lat)), [], np.empty(len(lat))
        for i in range(len(lat)):
            j = int(k[i])
            best = haversine_km(lat[i], lon[i], self.coords[j, 1], self.coords[j, 0])
            for jn in (j - 1, j + 1):
                if 0 <= jn < len(self.coords) and self.line_of[jn] == self.line_of[j]:
                    best = min(best, self._seg_km(lat[i], lon[i], self.coords[j], self.coords[jn]))
            out_d[i], out_kv[i] = best, self.kv_of[j]
            out_id.append(self.ids[self.line_of[j]])
        return out_d, out_id, out_kv

    @staticmethod
    def _seg_km(lat, lon, a, b):
        """Distance from a point to the segment a-b (lon, lat), in a local equirectangular plane."""
        c = math.cos(math.radians(lat))
        p = np.array([0.0, 0.0])
        ax, ay = (a[0] - lon) * c * 111.195, (a[1] - lat) * 111.195
        bx, by = (b[0] - lon) * c * 111.195, (b[1] - lat) * 111.195
        dx, dy = bx - ax, by - ay
        L2 = dx * dx + dy * dy
        t = 0.0 if L2 == 0 else max(0.0, min(1.0, -(ax * dx + ay * dy) / L2))
        return float(math.hypot(ax + t * dx, ay + t * dy))


# --------------------------------------------------------------------------- the model
def allowance_musd(kv):
    return ALLOWANCE_LE_138_MUSD if kv <= ALLOWANCE_KV_SPLIT + 1e-9 else ALLOWANCE_GT_138_MUSD


def line_cost_musd(uc, km, kv):
    """(low, mid, high, unverified_flag) $M for a new single-circuit line of `km` kilometres at voltage class of `kv`."""
    row = uc[(ITEM_LINE, str(_kv_class(LINE_COST_KV, kv)))]
    miles = km / KM_PER_MILE
    return row.low * miles / 1e6, row.mid * miles / 1e6, row.high * miles / 1e6, row.unverified


def bay_cost_musd(uc, kv):
    row = uc[(ITEM_BAY, str(_kv_class(BAY_COST_KV, kv)))]
    return row.low / 1e6, row.mid / 1e6, row.high / 1e6, row.unverified


def switchyard_cost_musd(uc, kv):
    row = uc[(ITEM_SUBSTATION, str(_kv_class(BAY_COST_KV, kv)))]
    return row.low / 1e6, row.mid / 1e6, row.high / 1e6, row.unverified


def gsu_cost_musd(uc, mw=REF_MW):
    """Step-up cost: the guide's (inverter + step-up) minus (inverter) = $169.5k per MW (to 69 kV), times the MW. UNVERIFIED as a
    proxy at 138/345 kV, which the guide does not give; kept apart from the interconnection facilities."""
    row = uc[(ITEM_GSU, "")]
    return (row.mid - row.low) * mw / 1e6, row.unverified


def _default_bay(uc, kv):
    """(low, mid, high) $M of one bay plus the site tie, and its UNVERIFIED flag: the price used when no geometry can decide."""
    b = bay_cost_musd(uc, kv)
    t = line_cost_musd(uc, SITE_TIE_KM, kv)
    return tuple(b[k] + t[k] for k in range(3)), (b[3] or t[3])


def lead_time_months(connection_class, kv, gen_tie_km, mw, uc=None):
    """(low, mid, high) months = FIS + max(class construction, step-up delivery + installation), and whether an LPT governs."""
    mva = mw * GSU_MVA_PER_MW
    lpt = kv >= 138.0 - 1e-9 and mva >= LPT_MIN_MVA - 1e-9
    if lpt:
        a, q = (uc or load_unit_costs())[(ITEM_LPT_AVG, "")], (uc or load_unit_costs())[(ITEM_LPT_QUOTED, "")]
        xf = (a.low, a.mid, max(a.high, q.mid))                    # 12 / 30 / 36 months (average row; quoted row mid = 36)
    else:
        xf = MPT_LEAD_MONTHS
    xf = tuple(x + XFMR_INSTALL_MONTHS for x in xf)
    if connection_class == "new_bay":
        build = BUILD_MONTHS_BAY
    elif connection_class == "new_switchyard":
        build = BUILD_MONTHS_SWITCHYARD
    else:
        t = GEN_TIE_MONTHS_BASE + GEN_TIE_MONTHS_PER_KM * gen_tie_km
        build = (t + 3.0, t + 9.0, t + 15.0)                       # base + bay at the far end (ASSUMPTION: 3/9/15 months)
    return tuple(FIS_MONTHS[i] + max(build[i], xf[i]) for i in range(3)), lpt


def load_substations(path=None):
    # keep_default_na=False: ERCOT has a substation coded "NA", which pandas would otherwise read as missing
    s = pd.read_csv(path or SUBSTATIONS_CSV, low_memory=False, keep_default_na=False, na_values=[""])
    s = s[s["in_np4160"].astype(str).str.lower().eq("true")].copy()
    return s.reset_index(drop=True)


def line_degree(lines_path=None):
    """Distinct neighbours per OSM substation id, from the OSM edges of data/lines.csv."""
    L = pd.read_csv(lines_path or LINES_CSV, low_memory=False, usecols=["source", "from_osm_sub", "to_osm_sub"])
    L = L[L["source"] == "osm"]
    pairs = pd.concat([L[["from_osm_sub", "to_osm_sub"]].rename(columns={"from_osm_sub": "a", "to_osm_sub": "b"}),
                       L[["to_osm_sub", "from_osm_sub"]].rename(columns={"to_osm_sub": "a", "from_osm_sub": "b"})])
    pairs = pairs.dropna().drop_duplicates()
    return pairs.groupby("a")["b"].nunique()


def connection_costs(mw=REF_MW, subs=None, uc=None, lines=None, deg=None, verbose=False):
    """DataFrame with one row per NP4-160 substation of at least 69 kV: class, cost range, allowance, owner share, lead time."""
    uc = uc or load_unit_costs()
    S = load_substations() if subs is None else subs
    S = S[S["kv_max"].astype(float) >= MIN_KV_CANDIDATE].reset_index(drop=True)
    deg = line_degree() if deg is None else deg
    S["n_term"] = S["osm_id"].map(deg).fillna(0).astype(int)
    placed = S["lat"].notna().to_numpy()
    conf = S["match_conf"].astype(float).fillna(0.0).to_numpy()
    reliable = placed & (conf >= MIN_CONF_PLACED)               # geometry is used only where the placement is trustworthy
    suitable = reliable & (S["n_term"].to_numpy() >= MIN_LINE_DEGREE)
    lat, lon = S["lat"].astype(float).to_numpy(), S["lon"].astype(float).to_numpy()
    # nearest suitable station (other than itself) and nearest tappable line, for every placed substation
    from scipy.spatial import cKDTree
    idx_s = np.flatnonzero(suitable)
    tree_s = cKDTree(_xyz(lat[idx_s], lon[idx_s]))
    li = lines or LineIndex()
    d_line, id_line, kv_line = np.full(len(S), np.nan), [""] * len(S), np.full(len(S), np.nan)
    pi = np.flatnonzero(reliable)
    dl, il, kl = li.nearest(lat[pi], lon[pi])
    d_line[pi], kv_line[pi] = dl, kl
    for a, b in zip(pi, il):
        id_line[a] = b
    d_stn, nn_stn = np.full(len(S), np.nan), np.full(len(S), -1)
    dd, jj = tree_s.query(_xyz(lat[pi], lon[pi]), k=2)
    for row, i in enumerate(pi):
        j = int(jj[row, 0])
        if idx_s[j] == i:                                          # the nearest suitable station is itself: take the next
            j = int(jj[row, 1])
        nn_stn[i] = idx_s[j]
        d_stn[i] = haversine_km(lat[i], lon[i], lat[idx_s[j]], lon[idx_s[j]])
    rows = []
    gsu, _ = gsu_cost_musd(uc, mw)
    for i, r in S.iterrows():
        kv_sub = float(r["kv_max"])
        unv = False
        basis = ""
        poi_sub, poi_line = "", ""
        tie_km = 0.0
        if suitable[i]:
            cls, kv, class_conf = "new_bay", kv_sub, 0.8
            b = bay_cost_musd(uc, kv)
            t = line_cost_musd(uc, SITE_TIE_KM, kv)
            tie_km = SITE_TIE_KM
            cost = tuple(b[k] + t[k] for k in range(3))
            unv = b[3] or t[3]
            poi_sub = r["sub"]
            basis = f"through station: {int(r['n_term'])} line terminations, match_conf {conf[i]:.2f}"
        elif reliable[i]:
            options = []
            # gen-tie to the nearest suitable station
            if nn_stn[i] >= 0:
                kv_t = float(S.at[nn_stn[i], "kv_max"])
                bb = bay_cost_musd(uc, kv_t)
                if d_stn[i] <= COLOCATED_KM:                        # same physical site: a bay at that station
                    ln = line_cost_musd(uc, SITE_TIE_KM, kv_t)
                    c = tuple(ln[k] + bb[k] for k in range(3))
                    options.append(("new_bay", c, kv_t, SITE_TIE_KM, ln[3] or bb[3], S.at[nn_stn[i], "sub"], "", 0.5))
                else:
                    km = d_stn[i] * ROUTE_FACTOR
                    ln = line_cost_musd(uc, km, kv_t)
                    c = tuple(ln[k] + bb[k] for k in range(3))
                    options.append(("gen_tie", c, kv_t, km, ln[3] or bb[3], S.at[nn_stn[i], "sub"], "", 0.5))
            # new switchyard on a nearby line
            if d_line[i] <= TAP_MAX_KM and kv_line[i] >= TAP_MIN_KV:
                kv_l = float(kv_line[i])
                sw = switchyard_cost_musd(uc, kv_l)
                t = line_cost_musd(uc, SITE_TIE_KM, kv_l)
                c = tuple(sw[k] + t[k] for k in range(3))
                options.append(("new_switchyard", c, kv_l, SITE_TIE_KM, sw[3] or t[3], "", id_line[i], 0.5))
            if options:
                cls, cost, kv, tie_km, unv, poi_sub, poi_line, class_conf = min(options, key=lambda o: o[1][1])
                basis = (f"not a through station (terminations {int(r['n_term'])}, match_conf {conf[i]:.2f}); "
                         f"nearest suitable station {d_stn[i]:.1f} km, nearest >= {TAP_MIN_KV:.0f} kV line {d_line[i]:.1f} km")
            else:                                                  # nothing suitable at all: price the bay, flag it
                cls, kv, class_conf = "new_bay", kv_sub, 0.3
                cost, unv = _default_bay(uc, kv)
                poi_sub, tie_km = r["sub"], SITE_TIE_KM
                basis = "no suitable station or line found; default new bay"
        else:
            cls, kv, class_conf = "new_bay", kv_sub, 0.3
            cost, unv = _default_bay(uc, kv)
            poi_sub, tie_km = r["sub"], SITE_TIE_KM
            basis = ("not placed: default new bay, no geometry" if not placed[i] else
                     f"placement confidence {conf[i]:.2f} < {MIN_CONF_PLACED}: default new bay, geometry not used")
        flags = []
        if cls == "gen_tie" and tie_km > GEN_TIE_MAX_KM:
            flags.append("long_tie")
        if cls == "new_bay" and class_conf < 0.5:
            flags.append("default_class")
        if cls == "new_bay" and poi_sub != r["sub"]:
            flags.append("colocated")
        if not placed[i]:
            flags.append("unplaced")
        elif conf[i] < MIN_CONF_PLACED:
            flags.append("low_conf_placement")
        allow = allowance_musd(kv)
        (lm, mm, hm), lpt = lead_time_months(cls, kv, tie_km, mw, uc)
        rows.append(dict(
            sub=r["sub"], zone=r.get("zone", ""), county=r.get("county", ""), lat=r["lat"], lon=r["lon"],
            match_conf=conf[i], kv_max=kv_sub, connection_class=cls, class_conf=class_conf, basis=basis,
            kv=kv, poi_sub=poi_sub, poi_line=poi_line, gen_tie_km=round(tie_km, 3),
            dist_station_km=d_stn[i], dist_line_km=d_line[i], n_line_terminations=int(r["n_term"]),
            cost_low_musd=cost[0], cost_mid_musd=cost[1], cost_high_musd=cost[2], allowance_musd=allow,
            owner_share_low_musd=max(0.0, cost[0] - allow), owner_share_musd=max(0.0, cost[1] - allow),
            owner_share_high_musd=max(0.0, cost[2] - allow),
            gsu_cost_musd=gsu, all_in_mid_musd=cost[1] + gsu, needs_lpt=lpt,
            lead_time_months=mm, lead_low_months=lm, lead_high_months=hm, cost_usd_year=USD_YEAR,
            uses_unverified=bool(unv), flags=";".join(flags),
            provenance="DERIVED; unit costs REAL (MISO MTEP24, 2024 USD); class rules and lead-time build times ASSUMPTION"))
    df = pd.DataFrame(rows)
    if verbose:
        print(df["connection_class"].value_counts().to_string())
    return df


COLUMN_LABELS = [
    ("sub", "REAL", "ERCOT NP4-160 SUBSTATION", "key; one row per NP4-160 substation with kv_max >= 69"),
    ("zone", "REAL", "data/substations.csv", "modal load zone"),
    ("county", "DERIVED", "data/substations.csv", "county of the matched coordinates"),
    ("lat", "DERIVED", "data/substations.csv", "blank when unplaced"),
    ("lon", "DERIVED", "data/substations.csv", "blank when unplaced"),
    ("match_conf", "ASSUMPTION", "data/substations.csv", "placement confidence 0-1 (a heuristic score, not a probability)"),
    ("kv_max", "REAL", "ERCOT NP4-160 VOLTAGE_LEVEL", "highest bus voltage of the substation"),
    ("connection_class", "DERIVED", "costs.py rules", "new_bay | new_switchyard | gen_tie; the cheapest feasible by mid cost; rules in the module docstring (class rules are ASSUMPTION)"),
    ("class_conf", "ASSUMPTION", "costs.py", "0.8 through station with a confident placement; 0.5 priced from geometry; 0.3 default because nothing could be computed"),
    ("basis", "DERIVED", "costs.py", "why this class"),
    ("kv", "DERIVED", "costs.py", "voltage of the point of interconnection: the station (new_bay), the tapped line (new_switchyard) or the far station (gen_tie)"),
    ("poi_sub", "DERIVED", "costs.py", "station where the bay is added (the sub itself for new_bay; the nearest suitable station for gen_tie); blank for new_switchyard"),
    ("poi_line", "DERIVED", "data/lines.csv / derived/lines.geojson", "OSM line id tapped by a new switchyard"),
    ("gen_tie_km", "DERIVED", "costs.py", "routed length of the tie: SITE_TIE_KM (ASSUMPTION) next to a new bay or switchyard, straight line x ROUTE_FACTOR (ASSUMPTION) for gen_tie"),
    ("dist_station_km", "DERIVED", "haversine on data/substations.csv", "straight-line km to the nearest suitable station other than itself; blank when unplaced"),
    ("dist_line_km", "DERIVED", "derived/lines.geojson", "km to the nearest OSM line of at least TAP_MIN_KV; blank when unplaced"),
    ("n_line_terminations", "DERIVED", "data/lines.csv (OSM edges)", "distinct neighbouring substations in the OSM line graph"),
    ("cost_low_musd", "REAL", "data/unit_costs.csv (MISO MTEP24 Tables 4.1-1, 4.2-1, 4.2-2)", "interconnection facilities, $M 2024; low column of each cited row; includes MISO's 30% contingency and 7.5% AFUDC; some source cells UNVERIFIED (see uses_unverified)"),
    ("cost_mid_musd", "REAL", "data/unit_costs.csv", "same, mid column"),
    ("cost_high_musd", "REAL", "data/unit_costs.csv", "same, high column"),
    ("allowance_musd", "REAL", "16 TAC 25.195(f)(3); notes/rules.md (d)", "$14 M for a point of interconnection up to 138 kV, $20 M above, 2024 dollars, agreements signed after 31 Dec 2025; CPI indexing from 2026 not applied"),
    ("owner_share_low_musd", "DERIVED", "cost_low - allowance", "paid by the generator; floor 0"),
    ("owner_share_musd", "DERIVED", "cost_mid - allowance", "the part of the mid cost above the allowance that the generator pays; floor 0. ASSUMPTION: the allowance is applied to the sum of the facilities (bay/switchyard + tie)"),
    ("owner_share_high_musd", "DERIVED", "cost_high - allowance", "paid by the generator; floor 0"),
    ("gsu_cost_musd", "ASSUMPTION", "data/unit_costs.csv battery row (inverter + step-up) minus (inverter)", "the battery's own step-up transformer, the generator's cost under 25.195(f)(1); $169.5k/MW from MISO's step-up to 69 kV, applied at any voltage (UNVERIFIED proxy); at REF_MW"),
    ("all_in_mid_musd", "DERIVED", "cost_mid + gsu_cost", "interconnection facilities plus the step-up, before the allowance"),
    ("needs_lpt", "DERIVED", "costs.py", "True when the step-up (1.1 x MW) is at least LPT_MIN_MVA and the point of interconnection is 138 kV or more: a large power transformer governs the schedule"),
    ("lead_time_months", "DERIVED", "FIS median 20.5 months (GIS Sep 2026, rules.md (d)) + max(class construction, transformer delivery + install)", "construction times are ASSUMPTION; LPT delivery 30 months is REAL (NLR 2026 average, unit_costs row)"),
    ("lead_low_months", "DERIVED", "same with FIS Q1 16 months and the low delivery and build times", ""),
    ("lead_high_months", "DERIVED", "same with FIS Q3 28 months and the high delivery and build times (LPT 36 months)", ""),
    ("cost_usd_year", "REAL", "MISO MTEP24 price year", "all $ columns are 2024 dollars; MISO escalates about 4%/yr (ASSUMPTION, not applied)"),
    ("uses_unverified", "DERIVED", "unit_costs.csv notes", "True if a cost row used here carries UNVERIFIED in its note"),
    ("flags", "DERIVED", "costs.py", "long_tie | unplaced | low_conf_placement | default_class (class_conf < 0.5: nothing could be computed, new bay assumed) | colocated (new bay at another ERCOT code of the same physical station, see poi_sub)"),
    ("provenance", "DERIVED", "costs.py", "summary label"),
]


def write_connection_costs(path=None, mw=REF_MW, verbose=True):
    path = path or OUT_CSV
    df = connection_costs(mw=mw, verbose=verbose)
    num = [c for c in df.columns if c.endswith("_musd") or c.endswith("_months") or c.endswith("_km")]
    df[num] = df[num].astype(float).round(3)
    df.to_csv(path, index=False)
    lab = pd.DataFrame(COLUMN_LABELS, columns=["column", "label", "source", "note"])
    missing = set(df.columns) - set(lab["column"])
    if missing:
        raise RuntimeError(f"columns without a label: {sorted(missing)}")
    lab.to_csv(path.with_name(path.stem + ".labels.csv"), index=False)
    return df
