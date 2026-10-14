"""Phase 2: station aliases, the substation line graph, a DC power-flow proxy and hop counts.

Everything here is deterministic. Unverified constants are the UPPER_CASE names at the top, each with a label:
REAL (from a named source), DERIVED (computed from REAL numbers), ASSUMPTION (a judgement), SIM (model output).

Graph model (one node per OSM substation, so every ERCOT substation that shares an OSM substation shares a node):
  * edges  = OSM substation-to-substation lines >= GRAPH_MIN_KV, plus HIFLD segments where OSM has no corridor
  * x_pu   = XPRIME_OHM_PER_KM[kV] * km / (kV^2 / SBASE_MVA) / circuits   (transformers are collapsed away)
  * PTDF   = DC power flow, injection at a node, withdrawal spread over the load reference (LOAD_REF_RULE)
Sign convention for shift factors (PLAN.md): cong[b,t] = LMP - lambda = - sum_k SF[b,k] * mu[k,t].
SF < 0 is the import side (price above lambda while the constraint binds); SF > 0 is the export side.
"""
from __future__ import annotations

import difflib
import re
from collections import defaultdict

import networkx as nx
import numpy as np
import pandas as pd
import scipy.sparse as sps
import scipy.sparse.linalg as spla

from . import paths

# ----------------------------------------------------------------------------------------------------------------
# Constants. Label in the comment: REAL / DERIVED / ASSUMPTION / SIM.
# ----------------------------------------------------------------------------------------------------------------
# --- station aliases -------------------------------------------------------------------------------------------
ALIAS_SUFFIXES = ("SWTCH", "SWITCH", "SWY", "TAP", "POI", "STA", "SUB", "SS", "TP", "SW")  # ASSUMPTION: tap/switch endings ERCOT appends to a yard name
ALIAS_STEM_MIN_LEN = 4        # ASSUMPTION: a stem shorter than this is ambiguous (PG, EC, REN) and gets the "short" weight
ALIAS_PREFIX_MIN_LEN = 5      # ASSUMPTION: shortest stem allowed for a prefix match
ALIAS_W = {                   # ASSUMPTION: evidence weights, combined by noisy-OR
    "stem_exact": 0.75,       # stem of the station equals an NP4-160 code (length >= ALIAS_STEM_MIN_LEN)
    "stem_short": 0.40,       # same, but a 2-3 letter stem
    "psse_name": 0.60,        # the station name is the stem of a PSS/E bus name of exactly one NP4-160 sub
    "osm_shared": 0.60,       # agent 1c linked the station and an NP4-160 sub to the same OSM substation
    "prefix": 0.45,           # one code is the prefix of the other
    "fuzzy": 0.40,            # difflib ratio >= ALIAS_FUZZY_MIN
}
ALIAS_FUZZY_MIN = 0.85        # ASSUMPTION
ALIAS_SHARED_NAME_MIN = 0.5   # ASSUMPTION: a shared OSM substation counts in full only if the two codes also look alike
ALIAS_SHARED_NO_NAME_FACTOR = 0.4   # ASSUMPTION: weight multiplier for a shared OSM substation with unlike codes (co-located yards)
ALIAS_KV_TOL = 1.0            # ASSUMPTION: kV match tolerance (kV)
ALIAS_KV_MISMATCH_FACTOR = 0.5   # ASSUMPTION: confidence multiplier when the candidate has no bus at the station's kV
ALIAS_GRAPH_NEAR_HOPS = 3     # ASSUMPTION: candidate within this many graph hops of the station's partner confirms it
ALIAS_GRAPH_BONUS = 1.15      # ASSUMPTION
ALIAS_FAR_KM = 60.0           # ASSUMPTION: candidate this far from a placed partner is contradicted
ALIAS_FAR_FACTOR = 0.4        # ASSUMPTION
ALIAS_MIN_CONF = 0.35         # ASSUMPTION: aliases below this are not written (sub left blank)
ALIAS_CONF_CAP = 0.95         # ASSUMPTION
NEIGHBOR_PLACE_CONF = 0.40    # ASSUMPTION: confidence of a station placed as the only unclaimed graph neighbour of its partner
ANCHOR_CONF = 0.5             # ASSUMPTION: substations at or above this match_conf are trusted graph anchors (agent 1c: "use >= 0.5")

# --- graph -----------------------------------------------------------------------------------------------------
GRAPH_MIN_KV = 69.0           # REAL: scope in PROMPT.md (>= 69 kV)
ERCOT_REGION_KM = 30.0        # ASSUMPTION: keep OSM nodes within this distance of an anchored ERCOT substation (drops SPP/MISO/WECC)
HIFLD_DETOUR = 2.0            # ASSUMPTION: a HIFLD segment is added only if OSM has no path shorter than this multiple of its length
HIFLD_MIN_KM = 0.5            # ASSUMPTION: ignore HIFLD segments shorter than this (snapping noise)
SNAP_NODE_KM = 5.0            # ASSUMPTION: an ERCOT sub with coordinates but no OSM link snaps to a graph node within this distance
SBASE_MVA = 100.0             # ASSUMPTION: per-unit base; PTDFs do not depend on it
XPRIME_OHM_PER_KM = {         # ASSUMPTION: typical overhead-line positive-sequence reactance by voltage class, ohm/km.
    69.0: 0.45, 115.0: 0.42, 138.0: 0.40, 161.0: 0.40, 230.0: 0.35, 345.0: 0.32, 500.0: 0.28, 765.0: 0.26,
}                             # Source: typical values in Grainger & Stevenson, Power System Analysis, line-parameter tables, and the PyPSA /
                              # pandapower standard overhead line types (0.25-0.30 ohm/km at 380 kV, ~0.40 at 110 kV). Not verified against
                              # ERCOT data, which is ECEII. Only the ratios between classes matter for PTDFs.
X_MIN_PU = 5e-4               # ASSUMPTION: floor on branch reactance (about 0.05 km of 138 kV line); stops zero-length edges from dominating B
MAX_CIRCUITS = 6              # REAL: lines.csv caps n_circuits at 6
LOAD_REF_RULE = ("uniform weight over graph nodes that host at least one anchored ERCOT substation with no resource node "
                 "(a load-substation proxy; no public load-by-substation data)")  # ASSUMPTION
PATH_MAX_HOPS = 4             # ASSUMPTION: a monitored line whose ends are not adjacent in the graph is treated as the series path if <= this many hops

# --- shift factor sides and hops -----------------------------------------------------------------------------
SF_SIDE_MIN = 0.02            # ASSUMPTION: |SF| below this is "neutral" (about twice the regression noise floor)
SF_CURTAIL_MIN = 0.05         # ASSUMPTION: export-side SF at or above this flags battery curtailment risk when the constraint binds
HOPS_UNREACHED = 99           # ASSUMPTION: hop value written when a substation is not connected to the graph

STATION_ALIAS_CSV = paths.REPO_DATA / "station_alias.csv"
GRAPH_EDGES = paths.DERIVED / "graph.edges.parquet"
GRAPH_NODES = paths.DERIVED / "graph.nodes.parquet"
PTDF_PROXY = paths.DERIVED / "ptdf_proxy.parquet"


# ----------------------------------------------------------------------------------------------------------------
# Loaders
# ----------------------------------------------------------------------------------------------------------------
def load_substations() -> pd.DataFrame:
    """data/substations.csv indexed by sub, with numeric lat/lon/conf and a bool in_np4160."""
    s = pd.read_csv(paths.REPO_DATA / "substations.csv", keep_default_na=False, dtype=str)
    for c in ("lat", "lon", "match_conf", "kv_max"):
        s[c] = pd.to_numeric(s[c], errors="coerce")
    s["in_np4160"] = s["in_np4160"].astype(str).eq("True")
    s["has_resource_node"] = s["has_resource_node"].astype(str).eq("True")
    return s.set_index("sub", drop=False)


def load_settlement_points() -> pd.DataFrame:
    sp = pd.read_csv(paths.REPO_DATA / "settlement_points.csv", dtype=str, keep_default_na=False)
    sp["kv"] = pd.to_numeric(sp["VOLTAGE_LEVEL"], errors="coerce")
    return sp


def haversine_km(lat1, lon1, lat2, lon2):
    lat1, lon1, lat2, lon2 = (np.radians(np.asarray(v, dtype=float)) for v in (lat1, lon1, lat2, lon2))
    a = np.sin((lat2 - lat1) / 2) ** 2 + np.cos(lat1) * np.cos(lat2) * np.sin((lon2 - lon1) / 2) ** 2
    return 6371.0 * 2 * np.arcsin(np.sqrt(a))


def _us(series: pd.Series) -> np.ndarray:
    """tz-aware pandas timestamps to int64 microseconds since epoch (UTC)."""
    return series.dt.tz_convert("UTC").dt.tz_localize(None).astype("datetime64[us]").astype("int64").to_numpy()


def constraint_stations() -> pd.DataFrame:
    """Every station name in RT/DAM constraint records and the IMM annual table, with its modal kV and partner stations."""
    cols = ["cid", "from_station", "to_station", "from_kv", "to_kv"]
    sh = pd.read_parquet(paths.DERIVED / "shadow_sced.parquet", columns=cols)
    dm = pd.read_parquet(paths.DERIVED / "shadow_dam.parquet", columns=cols)
    al = pd.concat([sh, dm]).drop_duplicates()
    rows = []
    for r in al.itertuples(index=False):
        rows.append((r.from_station, r.from_kv, r.to_station))
        rows.append((r.to_station, r.to_kv, r.from_station))
    ca = pd.read_csv(paths.REPO_DATA / "constraints_annual.csv", keep_default_na=False, dtype=str)
    ca = ca[ca["market"].isin(["RT", "DA"])]   # Potomac rows use SCED short names; the ERCOT C&N rows spell names out
    for r in ca.itertuples(index=False):
        kv = pd.to_numeric(r.kv, errors="coerce")
        rows.append((r.from_station, kv, r.to_station))
        rows.append((r.to_station, kv, r.from_station))
    st = pd.DataFrame(rows, columns=["station", "kv", "partner"])
    st = st[st["station"].astype(str) != ""]
    st["kv"] = pd.to_numeric(st["kv"], errors="coerce")
    st.loc[st["kv"] <= 1, "kv"] = np.nan
    out = st.groupby("station").agg(
        kv=("kv", lambda x: x.dropna().mode().iloc[0] if x.notna().any() else np.nan),
        partners=("partner", lambda x: sorted(set(p for p in x if p))),
    )
    return out


# ----------------------------------------------------------------------------------------------------------------
# Station aliases
# ----------------------------------------------------------------------------------------------------------------
def _clean(x: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", str(x).upper())


def _stem(x: str) -> str:
    """Station code without trailing digits and without tap/switch endings."""
    x = _clean(x)
    changed = True
    while changed:
        changed = False
        x2 = re.sub(r"\d+$", "", x)
        if x2 != x and len(x2) >= 2:
            x, changed = x2, True
        for sfx in ALIAS_SUFFIXES:
            if x.endswith(sfx) and len(x) - len(sfx) >= 2:
                x, changed = x[: -len(sfx)], True
                break
    return x


def _psse_stems(sp: pd.DataFrame, extended: bool = False) -> dict[str, set[str]]:
    """sub -> set of cleaned PSS/E bus-name stems (trailing digits and one-letter bus tags removed)."""
    out: dict[str, set[str]] = defaultdict(set)
    for sub, name in zip(sp["SUBSTATION"], sp["PSSE_BUS_NAME"]):
        n = _clean(name)
        n = re.sub(r"\d+[A-Z]?$", "", n) if len(n) > 5 else n
        if len(n) >= 3:
            out[sub].add(n)
            out[sub].add(_clean(name))
    # ELECTRICAL_BUS and NODE_NAME carry yard names too (SAND_MCPOI, EB_BLES_8955): keep their alphabetic tokens of 4+ letters
    if extended:
        for sub, eb, nn in zip(sp["SUBSTATION"], sp["ELECTRICAL_BUS"], sp["NODE_NAME"]):
            for txt in (eb, nn):
                for tok in re.split(r"[^A-Za-z]+", str(txt)):
                    if len(tok) >= 4:
                        out[sub].add(tok.upper())
    return out


class Resolver:
    """Resolves a constraint station name to a graph node, with alias and neighbour placement.

    Priority: (1) station_alias.csv row with an osm_id (graph-neighbour placement), (2) an NP4-160 sub with an anchored
    osm_id, (3) the alias target's osm_id, (4) the station's own osm_id (the extra station rows of substations.csv).
    """

    def __init__(self, subs: pd.DataFrame | None = None, alias: pd.DataFrame | None = None):
        self.subs = load_substations() if subs is None else subs
        if alias is None and STATION_ALIAS_CSV.exists():
            alias = pd.read_csv(STATION_ALIAS_CSV, keep_default_na=False, dtype=str)
        self.alias = alias if alias is not None else pd.DataFrame(columns=["station", "sub", "method", "conf", "osm_id"])
        self.alias = self.alias.set_index("station", drop=False)

    def resolve(self, station: str) -> dict:
        """-> dict(station, sub, osm_id, lat, lon, conf, how). `sub` is the NP4-160 code used downstream ('' if none)."""
        none = dict(station=station, sub="", osm_id="", lat=np.nan, lon=np.nan, conf=0.0, how="none")
        if not station:
            return none
        s = self.subs
        a = self.alias.loc[station] if station in self.alias.index else None
        if a is not None and str(a.get("osm_id", "")) != "" and str(a.get("method", "")) == "graph_neighbor":
            sub = station if station in s.index else ""
            return dict(station=station, sub=sub, osm_id=a["osm_id"], lat=float(a.get("lat") or np.nan), lon=float(a.get("lon") or np.nan),
                        conf=float(a["conf"]), how="graph_neighbor")
        if station in s.index and s.loc[station, "in_np4160"]:
            r = s.loc[station]
            if r["osm_id"] and r["match_conf"] >= ANCHOR_CONF:
                return dict(station=station, sub=station, osm_id=r["osm_id"], lat=r["lat"], lon=r["lon"], conf=float(r["match_conf"]), how="np4160")
            return dict(station=station, sub=station, osm_id="", lat=r["lat"], lon=r["lon"], conf=float(r["match_conf"]), how="np4160_unanchored")
        if a is not None and str(a.get("sub", "")) != "" and float(a["conf"]) >= ALIAS_MIN_CONF:
            t = s.loc[a["sub"]]
            if t["osm_id"]:
                return dict(station=station, sub=a["sub"], osm_id=t["osm_id"], lat=t["lat"], lon=t["lon"],
                            conf=float(a["conf"]) * float(max(t["match_conf"], 0.0)) ** 0.5, how="alias:" + str(a["method"]))
        if station in s.index:
            r = s.loc[station]
            if r["osm_id"]:
                return dict(station=station, sub="", osm_id=r["osm_id"], lat=r["lat"], lon=r["lon"], conf=float(r["match_conf"]), how="own_osm")
        return none


def build_station_alias(graph: "Graph | None" = None) -> pd.DataFrame:
    """Alias table for every constraint station that is not an NP4-160 SUBSTATION code, plus graph-neighbour placements.

    Evidence layers (all deterministic, weights in ALIAS_W): stem match after stripping tap/switch endings, PSS/E bus-name
    stems from NP4-160, a shared OSM substation (agent 1c's link), prefix match and fuzzy name match; each is checked against
    the station's kV and, when its partner station is placed, against graph distance and geography.
    Unplaced NP4-160 stations that sit on a priority constraint are placed as the single unclaimed graph neighbour of their
    placed partner (method graph_neighbor), when that neighbour is unique.
    """
    subs = load_substations()
    sp = load_settlement_points()
    st = constraint_stations()
    np_subs = subs[subs["in_np4160"]]
    codes = list(np_subs.index)
    code_clean = {c: _clean(c) for c in codes}
    kvs = sp.groupby("SUBSTATION")["kv"].agg(lambda x: set(float(v) for v in x.dropna()))
    psse = _psse_stems(sp, extended=True)
    stem_to_subs: dict[str, set[str]] = defaultdict(set)
    for sub, stems in psse.items():
        for sm in stems:
            stem_to_subs[sm].add(sub)
    osm_to_np: dict[str, list[str]] = defaultdict(list)
    for sub, r in np_subs.iterrows():
        if r["osm_id"] and r["match_conf"] >= 0.3:
            osm_to_np[r["osm_id"]].append(sub)
    G = graph or load_graph()
    res = Resolver(subs, alias=pd.DataFrame(columns=["station", "sub", "method", "conf", "osm_id"]))

    def kv_ok(cand: str, kv: float) -> bool:
        if not np.isfinite(kv):
            return True
        ks = kvs.get(cand, set())
        return any(abs(k - kv) <= ALIAS_KV_TOL for k in ks)

    rows = []
    targets = [s for s in st.index if not (s in np_subs.index)]
    for e in sorted(targets):
        kv = st.loc[e, "kv"]
        partners = st.loc[e, "partners"]
        stem = _stem(e)
        ev: dict[str, list[tuple[str, float, str]]] = defaultdict(list)
        # stem / prefix
        for c in codes:
            cc = code_clean[c]
            if cc == stem and len(stem) >= ALIAS_STEM_MIN_LEN:
                ev[c].append(("stem_exact", ALIAS_W["stem_exact"], f"stem {stem} = code"))
            elif cc == stem:
                ev[c].append(("stem_short", ALIAS_W["stem_short"], f"short stem {stem} = code"))
            elif len(stem) >= ALIAS_PREFIX_MIN_LEN and cc.startswith(stem):
                ev[c].append(("prefix", ALIAS_W["prefix"], f"code {c} starts with {stem}"))
            elif len(cc) >= ALIAS_PREFIX_MIN_LEN and stem.startswith(cc) and len(cc) >= len(stem) - 2:
                ev[c].append(("prefix", ALIAS_W["prefix"], f"{stem} starts with code {c}"))
        # PSS/E bus-name stems
        for sm in {_clean(e), stem}:
            if len(sm) >= 4 and sm in stem_to_subs:
                cs = stem_to_subs[sm]
                if len(cs) == 1:
                    c = next(iter(cs))
                    ev[c].append(("psse_name", ALIAS_W["psse_name"], f"PSS/E bus-name stem {sm}"))
        # shared OSM substation (agent 1c)
        own = subs.loc[e] if e in subs.index else None
        if own is not None and own["osm_id"] and own["match_conf"] >= 0.3:
            for c in osm_to_np.get(own["osm_id"], []):
                names = {code_clean[c]} | psse.get(c, set())
                sim = max(difflib.SequenceMatcher(None, stem, n).ratio() for n in names)
                w = ALIAS_W["osm_shared"] * (1.0 if sim >= ALIAS_SHARED_NAME_MIN else ALIAS_SHARED_NO_NAME_FACTOR)
                ev[c].append(("osm_shared", w, f"same OSM substation {own['osm_id']} ({own['name_guess']}), name sim {sim:.2f}"))
        # fuzzy code similarity (needs a letters-only stem of at least 4)
        if len(stem) >= 4:
            for c in codes:
                cc = code_clean[c]
                if abs(len(cc) - len(stem)) <= 3 and difflib.SequenceMatcher(None, stem, cc).ratio() >= ALIAS_FUZZY_MIN and c not in ev:
                    ev[c].append(("fuzzy", ALIAS_W["fuzzy"], f"fuzzy {stem}~{cc}"))
        # partner node for graph checks
        pn = None
        for p in partners:
            r = res.resolve(p)
            if r["osm_id"] and r["osm_id"] in G.index:
                pn = r
                break
        best = None
        for c, items in ev.items():
            miss = 1.0
            for _, w, _ in items:
                miss *= 1 - w
            score = 1 - miss
            if not kv_ok(c, kv):
                score *= ALIAS_KV_MISMATCH_FACTOR
            note = "; ".join(i[2] for i in items)
            if pn is not None:
                cr = res.resolve(c)
                if cr["osm_id"] and cr["osm_id"] in G.index:
                    h = G.hops_between(pn["osm_id"], cr["osm_id"])
                    if h is not None and h <= ALIAS_GRAPH_NEAR_HOPS:
                        score = min(ALIAS_CONF_CAP, score * ALIAS_GRAPH_BONUS)
                        note += f"; {h} hop(s) from partner {pn['station']}"
                    elif np.isfinite(cr["lat"]) and np.isfinite(pn["lat"]) and haversine_km(cr["lat"], cr["lon"], pn["lat"], pn["lon"]) > ALIAS_FAR_KM:
                        score *= ALIAS_FAR_FACTOR
                        note += f"; far from partner {pn['station']}"
            score = min(score, ALIAS_CONF_CAP)
            method = "+".join(sorted({i[0] for i in items}))
            if best is None or score > best[0]:
                best = (score, c, method, note)
        if best is not None and best[0] >= ALIAS_MIN_CONF:
            rows.append(dict(station=e, sub=best[1], method=best[2], conf=round(best[0], 3), osm_id="", lat=np.nan, lon=np.nan, evidence=best[3]))
        else:
            rows.append(dict(station=e, sub="", method="none", conf=0.0, osm_id="", lat=np.nan, lon=np.nan,
                             evidence="no candidate above ALIAS_MIN_CONF" if best is None else f"best {best[1]} {best[0]:.2f}: {best[3]}"))
    alias = pd.DataFrame(rows)

    # graph-neighbour placement of unplaced stations (NP4-160 or not) that sit next to a placed partner on a constraint line
    claimed = {r["osm_id"] for _, r in np_subs.iterrows() if r["osm_id"] and r["match_conf"] >= ANCHOR_CONF}
    res2 = Resolver(subs, alias=alias)
    extra = []
    for e in sorted(st.index):
        r0 = res2.resolve(e)
        if r0["osm_id"]:
            continue  # already has a node
        kv = st.loc[e, "kv"]
        partners = st.loc[e, "partners"]
        cands = None
        for p in partners:
            if p == e:
                continue
            pr = res2.resolve(p)
            if not pr["osm_id"] or pr["osm_id"] not in G.index:
                continue
            nb = set(G.neighbors_at_kv(pr["osm_id"], kv)) - claimed
            cands = nb if cands is None else cands & nb
        if cands is not None and len(cands) == 1:
            nid = next(iter(cands))
            lat, lon = G.latlon(nid)
            row = dict(station=e, sub=e if e in np_subs.index else "", method="graph_neighbor", conf=NEIGHBOR_PLACE_CONF, osm_id=nid,
                       lat=lat, lon=lon, evidence=f"only unclaimed {kv:g} kV graph neighbour of partner(s) {','.join(partners[:3])}")
            extra.append(row)
            claimed.add(nid)
    if extra:
        ex = pd.DataFrame(extra)
        alias = pd.concat([alias[~alias["station"].isin(ex["station"])], ex], ignore_index=True)
    alias = alias.sort_values("station").reset_index(drop=True)
    return alias


# ----------------------------------------------------------------------------------------------------------------
# Graph
# ----------------------------------------------------------------------------------------------------------------
def _xprime(kv: float) -> float:
    ks = sorted(XPRIME_OHM_PER_KM)
    k = min(ks, key=lambda v: abs(v - kv))
    return XPRIME_OHM_PER_KM[k]


class Graph:
    """The substation graph with node metadata. Edges carry kv, km, circuits, x_pu, source."""

    def __init__(self, nodes: pd.DataFrame, edges: pd.DataFrame):
        self.nodes = nodes.reset_index(drop=True)
        self.index = pd.Index(self.nodes["osm_id"])
        self.edges = edges.reset_index(drop=True)
        self.g = nx.Graph()
        for r in self.nodes.itertuples(index=False):
            self.g.add_node(r.osm_id)
        # parallel edges between the same node pair combine as parallel susceptances
        acc: dict[tuple[str, str], dict] = {}
        for r in self.edges.itertuples(index=False):
            k = tuple(sorted((r.u, r.v)))
            d = acc.setdefault(k, dict(b=0.0, kv=0.0, km=r.km, n=0))
            d["b"] += 1.0 / r.x_pu
            d["kv"] = max(d["kv"], r.kv)
            d["km"] = min(d["km"], r.km)
            d["n"] += 1
        for (u, v), d in acc.items():
            self.g.add_edge(u, v, x=1.0 / d["b"], kv=d["kv"], km=d["km"], n=d["n"])
        self._ll = self.nodes.set_index("osm_id")[["lat", "lon"]]

    def latlon(self, nid: str):
        r = self._ll.loc[nid]
        return float(r["lat"]), float(r["lon"])

    def hops_between(self, a: str, b: str):
        try:
            return nx.shortest_path_length(self.g, a, b)
        except (nx.NetworkXNoPath, nx.NodeNotFound):
            return None

    def neighbors_at_kv(self, nid: str, kv: float):
        out = []
        for n in self.g.neighbors(nid):
            ekv = self.g[nid][n]["kv"]
            if not np.isfinite(kv) or abs(ekv - kv) <= 1.0:
                out.append(n)
        return out

    def scaled(self, m_le115: float, m_ge230: float) -> "Graph":
        """Copy with branch reactance multiplied by m_le115 for lines <= 115 kV and by m_ge230 for lines >= 230 kV (138/161 kV unchanged)."""
        e = self.edges.copy()
        mult = np.where(e["kv"] <= 115.5, m_le115, np.where(e["kv"] >= 229.5, m_ge230, 1.0))
        e["x_pu"] = e["x_pu"] * mult
        return Graph(self.nodes, e)

    def save(self):
        self.nodes.to_parquet(GRAPH_NODES, index=False)
        self.edges.to_parquet(GRAPH_EDGES, index=False)


def load_graph() -> Graph:
    if GRAPH_NODES.exists() and GRAPH_EDGES.exists():
        return Graph(pd.read_parquet(GRAPH_NODES), pd.read_parquet(GRAPH_EDGES))
    return build_graph()


def build_graph(save: bool = True) -> Graph:
    """OSM edges (+ HIFLD where OSM lacks a corridor) >= GRAPH_MIN_KV, ERCOT region only, largest component."""
    lines = pd.read_csv(paths.REPO_DATA / "lines.csv", keep_default_na=False, low_memory=False)
    osm_sub = pd.read_csv(paths.REPO_DATA / "osm_substations.csv", keep_default_na=False)
    osm_sub["lat"] = pd.to_numeric(osm_sub["lat"], errors="coerce")
    osm_sub["lon"] = pd.to_numeric(osm_sub["lon"], errors="coerce")
    subs = load_substations()
    lines["voltage_kv"] = pd.to_numeric(lines["voltage_kv"], errors="coerce")
    lines["length_km"] = pd.to_numeric(lines["length_km"], errors="coerce")
    lines["n_circuits"] = pd.to_numeric(lines["n_circuits"], errors="coerce").fillna(1).clip(1, MAX_CIRCUITS)
    lines = lines[(lines["voltage_kv"] >= GRAPH_MIN_KV) & (lines["from_osm_sub"] != "") & (lines["to_osm_sub"] != "") & (lines["from_osm_sub"] != lines["to_osm_sub"])]
    osm = lines[lines["source"] == "osm"]
    hif = lines[(lines["source"] == "hifld") & (lines["in_osm_pair"].astype(str) != "True") & (lines["length_km"] >= HIFLD_MIN_KM)]

    def edge_rows(df, src):
        out = []
        for r in df.itertuples(index=False):
            km = max(float(r.length_km), 1e-3)
            kv = float(r.voltage_kv)
            x = _xprime(kv) * km * SBASE_MVA / kv**2 / float(r.n_circuits)
            out.append((r.from_osm_sub, r.to_osm_sub, kv, km, float(r.n_circuits), max(x, X_MIN_PU), src))
        return pd.DataFrame(out, columns=["u", "v", "kv", "km", "circuits", "x_pu", "source"])

    e_osm = edge_rows(osm, "osm")
    g0 = nx.Graph()
    for r in e_osm.itertuples(index=False):
        if g0.has_edge(r.u, r.v):
            g0[r.u][r.v]["km"] = min(g0[r.u][r.v]["km"], r.km)
        else:
            g0.add_edge(r.u, r.v, km=r.km)
    # HIFLD only where OSM has no corridor between the two snapped ends
    keep = []
    e_h = edge_rows(hif, "hifld")
    for r in e_h.itertuples(index=True):
        if r.u in g0 and r.v in g0:
            try:
                d = nx.shortest_path_length(g0, r.u, r.v, weight="km")
            except nx.NetworkXNoPath:
                d = np.inf
            if d <= HIFLD_DETOUR * r.km:
                continue
        keep.append(r.Index)
    e_h = e_h.loc[keep]
    edges = pd.concat([e_osm, e_h], ignore_index=True)
    # nodes with coordinates
    ll = osm_sub.set_index("osm_id")[["lat", "lon"]]
    nodes_all = pd.Index(pd.unique(pd.concat([edges["u"], edges["v"]])))
    nodes = pd.DataFrame({"osm_id": nodes_all})
    nodes = nodes.join(ll, on="osm_id")
    nodes = nodes[nodes["lat"].notna()]
    edges = edges[edges["u"].isin(nodes["osm_id"]) & edges["v"].isin(nodes["osm_id"])]
    # ERCOT region: keep nodes within ERCOT_REGION_KM of an anchored ERCOT substation's OSM node
    anchors = subs[(subs["in_np4160"]) & (subs["match_conf"] >= ANCHOR_CONF) & (subs["osm_id"] != "")]["osm_id"].unique()
    a_ll = ll.loc[[a for a in anchors if a in ll.index]]
    from scipy.spatial import cKDTree

    def km_xy(df):
        return np.c_[df["lat"].to_numpy() * 111.0, df["lon"].to_numpy() * 111.0 * np.cos(np.radians(31.0))]

    d, _ = cKDTree(km_xy(a_ll)).query(km_xy(nodes))
    nodes = nodes[d <= ERCOT_REGION_KM]
    edges = edges[edges["u"].isin(nodes["osm_id"]) & edges["v"].isin(nodes["osm_id"])]
    g = nx.Graph()
    g.add_edges_from(zip(edges["u"], edges["v"]))
    comp = max(nx.connected_components(g), key=len)
    nodes = nodes[nodes["osm_id"].isin(comp)].reset_index(drop=True)
    edges = edges[edges["u"].isin(comp) & edges["v"].isin(comp)].reset_index(drop=True)
    # ERCOT substation labels for the nodes
    ercot = subs[(subs["in_np4160"]) & (subs["osm_id"] != "") & (subs["match_conf"] >= ANCHOR_CONF)]
    lab = ercot.groupby("osm_id")["sub"].agg(lambda x: ";".join(sorted(x)))
    nodes["ercot_subs"] = nodes["osm_id"].map(lab).fillna("")
    G = Graph(nodes, edges)
    if save:
        G.save()
    return G


# ----------------------------------------------------------------------------------------------------------------
# DC power flow proxy
# ----------------------------------------------------------------------------------------------------------------
class DCPF:
    """DC power flow on the graph. One sparse factorisation, one solve per monitored element."""

    def __init__(self, G: Graph, subs: pd.DataFrame | None = None):
        self.G = G
        n = len(G.nodes)
        idx = {o: i for i, o in enumerate(G.nodes["osm_id"])}
        self.idx = idx
        rows, cols, vals = [], [], []
        for u, v, d in G.g.edges(data=True):
            b = 1.0 / d["x"]
            i, j = idx[u], idx[v]
            rows += [i, j, i, j]
            cols += [i, j, j, i]
            vals += [b, b, -b, -b]
        B = sps.csc_matrix((vals, (rows, cols)), shape=(n, n))
        self.n = n
        self.slack = int(np.argmax(np.asarray(B.diagonal())))  # highest-degree node as slack; PTDFs are re-referenced below
        keep = np.array([i for i in range(n) if i != self.slack])
        self.keep = keep
        self.lu = spla.splu(B[keep][:, keep].tocsc())
        subs = load_substations() if subs is None else subs
        # load reference weights (LOAD_REF_RULE)
        w = np.zeros(n)
        loadish = subs[(subs["in_np4160"]) & (subs["match_conf"] >= ANCHOR_CONF) & (subs["osm_id"] != "") & (~subs["has_resource_node"])]
        for o in loadish["osm_id"]:
            if o in idx:
                w[idx[o]] = 1.0
        if w.sum() == 0:
            w[:] = 1.0
        self.w = w / w.sum()
        self.n_load_nodes = int((w > 0).sum())

    def _theta(self, rhs: np.ndarray) -> np.ndarray:
        z = np.zeros(self.n)
        z[self.keep] = self.lu.solve(rhs[self.keep])
        return z

    def branch_ptdf(self, u: str, v: str) -> np.ndarray:
        """Flow on the corridor u->v (per unit injection at every node, withdrawn at the load reference), length n.

        For a monitored element that is a single graph edge the corridor is that edge (all parallel circuits together; the
        caller divides by the circuit count for one circuit). Positive = injection raises the u->v flow.
        """
        x = self.G.g[u][v]["x"]
        rhs = np.zeros(self.n)
        rhs[self.idx[u]] += 1.0
        rhs[self.idx[v]] -= 1.0
        z = self._theta(rhs)           # z = B^-1 (e_u - e_v) with the slack grounded; symmetric B, so z[b] = theta_u - theta_v for unit injection at b
        sf = z / x
        return sf - float(self.w @ sf)  # re-reference to the distributed withdrawal

    def element_ptdf(self, a: str, b: str) -> tuple[np.ndarray, str]:
        """PTDF row for a monitored line between graph nodes a and b. Returns (ptdf over nodes, note).

        Adjacent nodes: that corridor. Otherwise, if the nodes are within PATH_MAX_HOPS, the mean of the PTDFs of the edges on
        the shortest-reactance path (a series chain carries the same flow when no injection sits between its segments).
        """
        g = self.G.g
        if a == b:
            raise ValueError("same node")
        if g.has_edge(a, b):
            return self.branch_ptdf(a, b), "direct edge"
        try:
            path = nx.shortest_path(g, a, b, weight="x")
        except nx.NetworkXNoPath:
            raise KeyError("no path")
        if len(path) - 1 > PATH_MAX_HOPS:
            raise KeyError(f"ends are {len(path) - 1} hops apart")
        rows = [self.branch_ptdf(path[i], path[i + 1]) for i in range(len(path) - 1)]
        return np.mean(rows, axis=0), f"series path of {len(path) - 1} edges"


def element_endpoints(res: Resolver, from_station: str, to_station: str) -> tuple[dict, dict]:
    return res.resolve(from_station), res.resolve(to_station)


# ----------------------------------------------------------------------------------------------------------------
# Hops
# ----------------------------------------------------------------------------------------------------------------
def hop_and_x_distance(G: Graph, sources: list[str]) -> tuple[dict, dict]:
    """Unweighted hop distance and summed reactance (pu) from every node to the nearest of the source nodes."""
    sources = [s for s in sources if s in G.g]
    if not sources:
        return {}, {}
    hops = nx.multi_source_dijkstra_path_length(G.g, sources, weight=lambda u, v, d: 1.0)
    xd = nx.multi_source_dijkstra_path_length(G.g, sources, weight="x")
    return hops, xd


def side_of(sf: float) -> str:
    if not np.isfinite(sf):
        return ""
    if sf <= -SF_SIDE_MIN:
        return "import"
    if sf >= SF_SIDE_MIN:
        return "export"
    return "neutral"


def sub_nodes(G: Graph, subs: pd.DataFrame | None = None) -> pd.DataFrame:
    """NP4-160 substations with match_conf >= ANCHOR_CONF -> graph node (own OSM link, else nearest node within SNAP_NODE_KM)."""
    from scipy.spatial import cKDTree

    subs = load_substations() if subs is None else subs
    s = subs[(subs["in_np4160"]) & (subs["match_conf"] >= ANCHOR_CONF)]
    nodes = G.nodes
    xy = np.c_[nodes["lat"].to_numpy() * 111.0, nodes["lon"].to_numpy() * 111.0 * np.cos(np.radians(31.0))]
    tree = cKDTree(xy)
    out = []
    for sub, r in s.iterrows():
        if r["osm_id"] and r["osm_id"] in G.index:
            out.append((sub, r["osm_id"], "anchor", 0.0))
        elif np.isfinite(r["lat"]):
            d, i = tree.query([r["lat"] * 111.0, r["lon"] * 111.0 * np.cos(np.radians(31.0))])
            if d <= SNAP_NODE_KM:
                out.append((sub, nodes.at[int(i), "osm_id"], "snap", float(d)))
            else:
                out.append((sub, "", "far", float(d)))
        else:
            out.append((sub, "", "unplaced", np.nan))
    return pd.DataFrame(out, columns=["sub", "osm_id", "how", "dist_km"]).set_index("sub", drop=False)
