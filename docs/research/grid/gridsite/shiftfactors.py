"""Phase 2: the constraint table, shift factors fitted from prices, validation, and the physics-proxy shift factors.

Sign convention (PLAN.md): cong[b,t] = LMP[b,t] - lambda[t] = - sum_k SF[b,k] * mu[k,t], mu = published shadow price (>= 0).
So SF < 0 is the import side (cong > 0 while the constraint binds) and SF > 0 the export side.

Unverified constants are the UPPER_CASE names below, each labelled REAL / DERIVED / ASSUMPTION / SIM.
"""
from __future__ import annotations

import json
import re
from collections import defaultdict

import numpy as np
import pandas as pd

from . import network as N
from . import paths

# ----------------------------------------------------------------------------------------------------------------
# Constants
# ----------------------------------------------------------------------------------------------------------------
SCED_DT_H = 5.0 / 60.0            # ASSUMPTION: one SCED run covers 5 minutes (same convention as notes/phase1-market.md)
DAM_DT_H = 1.0                    # REAL: DAM prices are hourly

# --- priority score ---------------------------------------------------------------------------------------------
W_STRUCT = 0.5                    # ASSUMPTION: weight of the structural term (2025 IMM annual rent)
W_CURRENT = 0.5                   # ASSUMPTION: weight of the current term (rents seen in the window)
W_CURRENT_RT = 0.5                # ASSUMPTION: within the current term, share given to the real-time week
W_CURRENT_DAM = 0.5               # ASSUMPTION: and to the 31 day-ahead days
ELEMENT_MAX_KM_LOW = 80.0         # ASSUMPTION: a line below 230 kV whose two placed ends are farther apart than this is a placement error
ELEMENT_MAX_KM_HIGH = 130.0       # ASSUMPTION: same for lines at 230 kV and above
PRIORITY_TOP_SF = 80              # ASSUMPTION: constraints kept in data/shift_factors.csv
PRIORITY_TOP_HOPS = 40            # ASSUMPTION: constraints in data/hops.csv

# --- regression --------------------------------------------------------------------------------------------------
MIN_BINDING_RUNS = 10             # ASSUMPTION: RT constraints that bind in fewer SCED runs are not fitted (SF = NaN)
MIN_BINDING_HOURS_DAM = 12        # ASSUMPTION: same for DAM, in hours
MIN_TRAIN_BINDING = 3             # ASSUMPTION: a constraint must bind this often in the training part to be fitted in the validation fit
HOLDOUT_RT_HOURS = 24             # ASSUMPTION: RT holdout = the last 24 h of aligned SCED runs ("last RT day"; 10 Oct is a partial CPT day)
HOLDOUT_DAM_DAYS = 5              # REAL: brief asks for the last 5 DAM days
RIDGE_GRID = (1e1, 1e2, 1e3, 1e4, 3e4, 1e5)   # ASSUMPTION: ridge penalties tried, absolute (units: ($/MWh)^2 of shadow price); absolute rather than per-column scaled so a constraint with little binding evidence is shrunk toward SF = 0 (a column-scaled penalty was tried and gave out-of-sample R2 near 0 for the day-ahead fit)
CV_BLOCKS = 5                     # ASSUMPTION: contiguous time blocks for choosing the ridge penalty inside the training part
COLLINEAR_CORR = 0.90             # ASSUMPTION: shadow-price series with |corr| >= this are merged into one regressor group
COLLINEAR_WARN_CORR = 0.80        # ASSUMPTION: pairs above this are reported (not merged)
VIF_MAX = 1000.0                  # ASSUMPTION: numerical guard only: a regressor group with a VIF above this gets SF = NaN. Collinearity is handled by merging groups with shadow-price corr >= COLLINEAR_CORR and by tagging VIF > VIF_FLAG; a VIF of 100 would drop key constraints such as Twin Buttes-Hargrove (the day-ahead design has 477 regressors for 744 hours)
VIF_FLAG = 10.0                   # ASSUMPTION: VIF above this tags the SF "+collinear" in the quality column
RELAX_ALPHA = 1e3                 # ASSUMPTION: ridge penalty for the focal (top-priority) regressor groups; every other group keeps the cross-validated penalty. Lowers the attenuation of the SFs we publish
SHRINK_MIN = 0.8                  # ASSUMPTION: ridge shrinkage factor ss/(ss+lambda), ss = sum of squared shadow prices; below this the SF magnitude is attenuated and tagged "+shrunk"
SF_MAX_ABS = 2.0                  # ASSUMPTION: a bus-level |SF| above this is not physical for a single element; tagged "+implausible"
T_MIN = 2.0                       # ASSUMPTION: |SF|/se below this marks the SF "weak" (se ignores autocorrelation, so it is optimistic)
SF_STORE_MIN = 0.005              # ASSUMPTION: |SF| below this is not stored in sf_long (about the noise floor; treat absent as 0)
SF_CSV_MIN = 0.02                 # ASSUMPTION: |SF| threshold for data/shift_factors.csv
AT_CAP_TOL = 0.5                  # ASSUMPTION: a shadow price within this ($/MWh) of max_shadow_price counts as "at cap" (capped prices are stored a few 1e-4 below the cap, e.g. 3499.99976 vs 3500)
R2_OK = 0.5                       # ASSUMPTION: a bus counts as "well explained" out of sample above this R2

# --- proxy calibration -----------------------------------------------------------------------------------------
PROXY_MIN_PAIRS = 5               # ASSUMPTION: need this many placed substations to compare proxy and regression
PROXY_POOR_CORR = 0.5             # REAL (from the brief): median correlation below this = "poor"
PROXY_LOW_CONF = "low"            # ASSUMPTION: label written to sf_long.quality for proxy rows when the proxy is poor
PROXY_SF_MIN = 0.01               # ASSUMPTION: |PTDF| below this is not stored

# --- planned-upgrade matching ----------------------------------------------------------------------------------
UPGRADE_TYPES = ("reconductor", "new_line", "new_xfmr", "upgrade_sub")   # REAL: from the brief
MATCH_CONF = {"pair_name": 0.90, "pair_title": 0.80, "pair_bus": 0.70, "pair_bus_many": 0.45, "xfmr_sub": 0.60, "one_end": 0.30, "gtc_doc": 0.70}   # ASSUMPTION
UPGRADE_CONF_MIN = 0.5            # ASSUMPTION: a planned-upgrade match at or above this confidence counts as "has a planned upgrade" in hops.csv
PAIR_BUS_FEW = 4                  # ASSUMPTION: a project whose PSS/E buses resolve to more substations than this is a broad project; pair_bus match gets the lower weight
PROJ_LINE_WORDS = re.compile(r"\bLINE\b|\bCKT\b|CIRCUIT|CONDUCT|REBUILD|\bTO\b| - |[\u2013\u2014\ufffd]|\bBETWEEN\b", re.I)   # ASSUMPTION: a reconductor/new_line project must read like a line project (the dash class also covers a mis-decoded en dash in TPIT titles)
PROJ_NAME_EXCLUDE = re.compile(r"BREAKER|CAPACITOR|REACTOR|RELAY|SERIES COMP", re.I)   # ASSUMPTION: equipment-only projects do not relieve a thermal limit
PROJ_NAME_DROP = {"SWITCH", "SWITCHING", "STATION", "SUBSTATION", "SUB", "TAP", "POI", "KV", "TNP", "TNMP", "UPGRADE", "STEC", "LINE", "BUS"}  # ASSUMPTION
GTC_UPGRADES = {                  # ASSUMPTION: GTC code -> projects the public documents say relieve it (data/gtc.csv status text, notes/phase1-planning.md)
    "WESTEX": ("PBRP-IMPORT1", "PBRP-IMPORT2", "PBRP-IMPORT3"),
    "PNHNDL": ("RPG-26RPG008",),
    "MCCAMY": ("CN2025-01",),
    "NELRIO": ("CN2025-04",), "NE_LOB": ("CN2025-04",), "VALIMP": ("CN2025-04",), "VALEXP": ("CN2025-04",),
    "PASP_PATA": ("CN2025-06",), "E_PASP": ("CN2025-06",), "E_PATA": ("CN2025-06",), "I_PASP": ("CN2025-06",),
}

CONSTRAINTS_CSV = paths.REPO_DATA / "constraints.csv"
SF_LONG = paths.DERIVED / "sf_long.parquet"
CACHE = paths.DERIVED / "_p2_cache"
CACHE.mkdir(parents=True, exist_ok=True)


# ----------------------------------------------------------------------------------------------------------------
# Constraint table
# ----------------------------------------------------------------------------------------------------------------
def _mode(x: pd.Series):
    x = x.dropna()
    x = x[x.astype(str) != ""]
    return x.mode().iloc[0] if len(x) else ""


def _agg_window(df: pd.DataFrame, dt_h: float, tag: str, ts_col: str) -> pd.DataFrame:
    """Per-cid stats for one market window from a shadow-price table."""
    d = df.copy()
    d = d.groupby([ts_col, "cid"], as_index=False).agg(shadow_price=("shadow_price", "max"), limit=("limit", "max"), flow=("flow", "max"),
                                                       max_shadow_price=("max_shadow_price", "max"))
    b = d[d["shadow_price"] > 0].copy()
    b["rent"] = b["shadow_price"] * dt_h
    b["rent_usd"] = b["shadow_price"] * b["flow"].abs() * dt_h
    if "max_shadow_price" in b and tag == "rt":
        b["at_cap"] = (b["shadow_price"] >= b["max_shadow_price"] - AT_CAP_TOL) & (b["max_shadow_price"] > 0)
    else:
        b["at_cap"] = False
    g = b.groupby("cid")
    out = pd.DataFrame({
        f"{tag}_n": g.size(),
        f"{tag}_hours_binding": g.size() * dt_h,
        f"{tag}_mean_shadow": g["shadow_price"].mean(),
        f"{tag}_p95_shadow": g["shadow_price"].quantile(0.95),
        f"{tag}_max_shadow": g["shadow_price"].max(),
        f"{tag}_rent_proxy": g["rent"].sum(),
        f"{tag}_rent_est_musd": g["rent_usd"].sum() / 1e6,
        f"{tag}_median_limit": g["limit"].median(),
        f"{tag}_median_flow": g["flow"].median(),
        f"{tag}_pct_at_cap": g["at_cap"].mean() * 100.0,
    })
    return out


def load_shadow():
    sh = pd.read_parquet(paths.DERIVED / "shadow_sced.parquet")
    dm = pd.read_parquet(paths.DERIVED / "shadow_dam.parquet")
    return sh, dm


def _element_type(fs: str, ts: str, fk: float, tk: float) -> str:
    if fs == "" and ts == "":
        return "gtc"
    if fs == "" or ts == "":
        return "other"
    if fs == ts:
        return "xfmr"
    if np.isfinite(fk) and np.isfinite(tk) and abs(fk - tk) > 0.5:
        return "xfmr"
    if np.isfinite(fk) and np.isfinite(tk):
        return "line"
    return "other"


def _norm_project_name(x: str) -> str:
    x = re.sub(r"\(.*?\)", " ", str(x).upper())
    x = re.sub(r"[^A-Z0-9 ]", " ", x)
    toks = [t for t in x.split() if t not in PROJ_NAME_DROP and not t.isdigit()]
    return "".join(toks)


class ProjectIndex:
    """Planned projects (TPIT/RTP/RPG) of the upgrade types, resolved to NP4-160 substations."""

    def __init__(self, subs: pd.DataFrame, sp: pd.DataFrame):
        p = pd.read_csv(paths.REPO_DATA / "planned_projects.csv", keep_default_na=False, low_memory=False)
        self.all = p.set_index("project_id", drop=False)
        p = p[p["project_type"].isin(UPGRADE_TYPES)].copy()
        bus2sub = dict(zip(sp["PSSE_BUS_NUMBER"], sp["SUBSTATION"]))
        names: dict[str, set[str]] = defaultdict(set)
        kvs = sp.groupby("SUBSTATION")["kv"].agg(lambda x: set(float(v) for v in x.dropna()))
        self.kvs = kvs
        psse = N._psse_stems(sp)
        for sub in subs.index[subs["in_np4160"]]:
            for nm in {N._clean(sub)} | psse.get(sub, set()):
                names[nm].add(sub)
            ng = subs.loc[sub, "name_guess"]
            if ng:
                names[_norm_project_name(ng)].add(sub)
        names.pop("", None)

        def name_subs(nm: str, kv: float):
            key = _norm_project_name(nm)
            if len(key) < 3:
                return set()
            cs = names.get(key, set())
            if not cs:
                return set()
            ok = {c for c in cs if not np.isfinite(kv) or any(abs(k - kv) <= 1 for k in kvs.get(c, set()))}
            return ok

        rows = []
        for r in p.itertuples(index=False):
            kv = pd.to_numeric(r.kv, errors="coerce")
            bus = set()
            for tok in re.split(r"[,;\s]+", str(r.sswg_bus_numbers)):
                if tok and tok in bus2sub:
                    bus.add(bus2sub[tok])
            if PROJ_NAME_EXCLUDE.search(str(r.name)):
                continue
            line_like = bool(PROJ_LINE_WORDS.search(str(r.name)))
            title = set()
            for chunk in re.split(r"\s+to\s+|\s+-\s+|[\u2013\u2014\ufffd]|,|&|\band\b", str(r.name), flags=re.I):
                title |= name_subs(chunk, kv)
            rows.append(dict(project_id=r.project_id, name=r.name, kv=kv, project_type=r.project_type, in_service_date=r.in_service_date,
                             status=r.status, A=name_subs(r.from_sub_name, kv), B=name_subs(r.to_sub_name, kv), bus=bus, title=title,
                             record_type=r.record_type, line_like=line_like))
        self.proj = rows

    def match(self, a: str, b: str, etype: str, kv: float):
        """Best project touching the element. Returns dict or None."""
        best = None
        n = 0
        for pr in self.proj:
            conf, how = 0.0, ""
            if etype == "line" and a and b and a != b:
                if not pr["line_like"] and pr["project_type"] in ("reconductor", "new_line"):
                    continue
                if (a in pr["A"] and b in pr["B"]) or (b in pr["A"] and a in pr["B"]):
                    conf, how = MATCH_CONF["pair_name"], "pair_name"
                elif {a, b} <= pr["title"] and pr["line_like"]:
                    conf, how = MATCH_CONF["pair_title"], "pair_title"
                elif {a, b} <= pr["bus"] and pr["project_type"] in ("reconductor", "new_line"):
                    few = len(pr["bus"]) <= PAIR_BUS_FEW
                    conf, how = MATCH_CONF["pair_bus" if few else "pair_bus_many"], "pair_bus" if few else "pair_bus_many"
                elif pr["project_type"] in ("reconductor", "new_line") and ({a, b} & (pr["A"] | pr["B"])):
                    conf, how = MATCH_CONF["one_end"], "one_end"
            elif etype in ("xfmr", "other") and a:
                if pr["project_type"] in ("new_xfmr", "upgrade_sub") and (a in pr["A"] or a in pr["B"] or (a in pr["bus"] and len(pr["bus"]) <= 4)):
                    conf, how = MATCH_CONF["xfmr_sub"], "xfmr_sub"
            if conf <= 0:
                continue
            n += 1
            pri = {"under_construction": 0, "planned": 1, "in_service": 3}.get(pr["status"], 2)
            key = (-conf, pri, str(pr["in_service_date"]) or "9999")
            if best is None or key < best[0]:
                best = (key, pr, conf, how)
        if best is None:
            return None
        _, pr, conf, how = best
        return dict(project_id=pr["project_id"], project_name=pr["name"], in_service_date=pr["in_service_date"], status=pr["status"],
                    match_conf=conf, match_method=how, n_matches=n)

    def gtc_match(self, code: str):
        ids = GTC_UPGRADES.get(code)
        if not ids:
            return None
        best = None
        for i in ids:
            if i in self.all.index:
                r = self.all.loc[i]
                if best is None or str(r["in_service_date"]) < str(best["in_service_date"]):
                    best = r
        if best is None:
            return None
        return dict(project_id=";".join(i for i in ids if i in self.all.index), project_name=best["name"], in_service_date=best["in_service_date"],
                    status=best["status"], match_conf=MATCH_CONF["gtc_doc"], match_method="gtc_doc", n_matches=len(ids))


def build_constraints() -> pd.DataFrame:
    """data/constraints.csv: one row per cid seen in RT or DAM, plus structural IMM elements that did not bind in the window."""
    sh, dm = load_shadow()
    sh["limit"] = sh["limit"].astype(float)
    # element attributes per cid (first/modal over both markets)
    keep = ["cid", "constraint_name", "contingency_name", "from_station", "to_station", "from_kv", "to_kv"]
    att = pd.concat([sh[keep], dm[keep]]).groupby("cid").agg(
        constraint_name=("constraint_name", _mode), contingency_name=("contingency_name", _mode), from_station=("from_station", _mode),
        to_station=("to_station", _mode), from_kv=("from_kv", "median"), to_kv=("to_kv", "median"))
    rt = _agg_window(sh[["sced_ts", "cid", "shadow_price", "limit", "flow", "max_shadow_price"]], SCED_DT_H, "rt", "sced_ts")
    d2 = dm.rename(columns={"interval_start_utc": "ts"})
    d2["max_shadow_price"] = np.nan
    dam = _agg_window(d2[["ts", "cid", "shadow_price", "limit", "flow", "max_shadow_price"]], DAM_DT_H, "dam", "ts")
    c = att.join(rt, how="left").join(dam, how="left")
    # listed-but-never-binding RT/DAM cids stay (they were "seen"); counts are zero
    for col in [x for x in c.columns if x.endswith("_n") or x.endswith("hours_binding") or x.endswith("rent_proxy") or x.endswith("rent_est_musd")]:
        c[col] = c[col].fillna(0.0)
    c["rt_n"] = c["rt_n"].astype(int)
    c["dam_n"] = c["dam_n"].astype(int)
    c = c.rename(columns={"rt_n": "rt_n_runs_binding", "dam_n": "dam_n_hours"})
    c["window_binding"] = (c["rt_n_runs_binding"] > 0) | (c["dam_n_hours"] > 0)
    c["rt_binding"] = c["rt_n_runs_binding"] > 0
    c["dam_binding"] = c["dam_n_hours"] > 0
    c["median_limit_mw"] = c["rt_median_limit"].fillna(c["dam_median_limit"])
    c["median_flow_mw"] = c["rt_median_flow"].fillna(c["dam_median_flow"])
    c = c.reset_index()
    c["source"] = "window"
    # ---- structural elements from the IMM annual tables that never appear in the window
    ca = pd.read_csv(paths.REPO_DATA / "constraints_annual.csv", keep_default_na=False)
    ca = ca[ca["market"].isin(["RT", "DA"])].copy()
    ca["code"] = ca["constraint_name"].str.replace(r"\s*\(.*\)\s*$", "", regex=True)
    ca["paren"] = ca["constraint_name"].str.extract(r"\((.*)\)\s*$")[0].fillna("")
    for col in ("rent_musd", "binding_hours", "kv"):
        ca[col] = pd.to_numeric(ca[col], errors="coerce")
    sub_all = N.load_substations()
    sp = N.load_settlement_points()
    alias_df = pd.read_csv(N.STATION_ALIAS_CSV, keep_default_na=False, dtype=str) if N.STATION_ALIAS_CSV.exists() else None
    res = N.Resolver(sub_all, alias_df)
    # ---- annual join (element level): code first, then station pair + kV, then GTC code
    cn_set = set(c["constraint_name"])
    pair_idx = defaultdict(list)
    for r in c.itertuples():
        if r.from_station and r.to_station:
            pair_idx[frozenset((r.from_station, r.to_station))].append(r.cid)
    ann_rows = []   # one per IMM element (code), choose year 2025 (RT, else DA); 2024 kept as a separate column
    for code, g in ca.groupby("code", sort=False):
        g25 = g[g["year"].astype(str) == "2025"]
        g24 = g[g["year"].astype(str) == "2024"]

        def pick(gg, mk):
            x = gg[gg["market"] == mk]
            return x.iloc[0] if len(x) else None

        r25, d25, r24, d24 = pick(g25, "RT"), pick(g25, "DA"), pick(g24, "RT"), pick(g24, "DA")
        ref = next((x for x in (r25, d25, r24, d24) if x is not None), None)
        rent25, mk25 = (r25["rent_musd"], "RT") if r25 is not None and np.isfinite(r25["rent_musd"]) else \
            ((d25["rent_musd"], "DA") if d25 is not None and np.isfinite(d25["rent_musd"]) else (np.nan, ""))
        rent24 = r24["rent_musd"] if r24 is not None and np.isfinite(r24["rent_musd"]) else (d24["rent_musd"] if d24 is not None and np.isfinite(d24["rent_musd"]) else np.nan)
        hrs = np.nan
        for x in (r25, d25):
            if x is not None and np.isfinite(x["binding_hours"]):
                hrs = x["binding_hours"]
                break
        ann_rows.append(dict(code=code, paren=ref["paren"], from_station=ref["from_station"], to_station=ref["to_station"], kv=ref["kv"],
                             element_type=ref["element_type"], rent25=rent25, rent25_market=mk25, rent24=rent24, hours25=hrs,
                             rt_rent25=(r25["rent_musd"] if r25 is not None else np.nan), da_rent25=(d25["rent_musd"] if d25 is not None else np.nan)))
    ann = pd.DataFrame(ann_rows)
    c["annual_element"] = ""
    c["annual_join"] = ""
    ann_by_code = ann.set_index("code")
    for r in ann.itertuples():
        cids = []
        how = ""
        if r.code in cn_set:
            cids = list(c.loc[c["constraint_name"] == r.code, "cid"])
            how = "code"
        elif r.paren and r.paren in cn_set:         # GTC code in parentheses, e.g. WESTEX
            cids = list(c.loc[c["constraint_name"] == r.paren, "cid"])
            how = "gtc_code"
        elif r.from_station and r.to_station:
            cand = pair_idx.get(frozenset((r.from_station, r.to_station)), [])
            if np.isfinite(r.kv):
                cand = [x for x in cand if abs(float(c.loc[c["cid"] == x, "from_kv"].iloc[0]) - r.kv) < 1.5 or abs(float(c.loc[c["cid"] == x, "to_kv"].iloc[0]) - r.kv) < 1.5]
            cids, how = cand, "station_pair_kv"
        if cids:
            c.loc[c["cid"].isin(cids), "annual_element"] = r.code
            c.loc[c["cid"].isin(cids), "annual_join"] = how
    # structural-only rows: IMM elements with no cid in the window
    seen_codes = set(c["annual_element"])
    extra = []
    for r in ann.itertuples():
        if r.code in seen_codes:
            continue
        if not (np.isfinite(r.rent25) or np.isfinite(r.rent24)):
            continue
        fs, ts = r.from_station, r.to_station
        etype = r.element_type if r.element_type in ("line", "xfmr", "gtc") else "other"
        if fs and ts and fs == ts:
            etype = "xfmr"
        extra.append(dict(cid=f"{r.paren or r.code}|UNSEEN", constraint_name=r.paren if etype == "gtc" and r.paren else r.code, contingency_name="UNSEEN",
                          from_station=fs, to_station=ts, from_kv=r.kv, to_kv=r.kv, window_binding=False, rt_binding=False, dam_binding=False,
                          rt_n_runs_binding=0, dam_n_hours=0, annual_element=r.code, annual_join="structural_only", source="imm_structural",
                          median_limit_mw=np.nan, median_flow_mw=np.nan))
    if extra:
        c = pd.concat([c, pd.DataFrame(extra)], ignore_index=True)
    for col in [x for x in c.columns if x.endswith("hours_binding") or x.endswith("rent_proxy") or x.endswith("rent_est_musd")]:
        c[col] = c[col].fillna(0.0)
    # ---- element description, type, endpoints
    c["from_station"] = c["from_station"].fillna("")
    c["to_station"] = c["to_station"].fillna("")
    c["element_type"] = [_element_type(a, b, fk, tk) for a, b, fk, tk in zip(c["from_station"], c["to_station"], c["from_kv"], c["to_kv"])]
    c["kv"] = [(max(a, b) if np.isfinite(a) and np.isfinite(b) else (a if np.isfinite(a) else b)) for a, b in zip(c["from_kv"], c["to_kv"])]
    c["kv"] = c["kv"].where(c["kv"] > 1)
    kvs_by_sub = sp.groupby("SUBSTATION")["kv"].agg(lambda x: sorted(set(float(v) for v in x.dropna() if v >= 69)))
    for i in c.index[c["kv"].isna() & c["element_type"].isin(["other", "line"])]:
        ka = set(kvs_by_sub.get(res.resolve(c.at[i, "from_station"])["sub"] or c.at[i, "from_station"], []))
        kb = set(kvs_by_sub.get(res.resolve(c.at[i, "to_station"])["sub"] or c.at[i, "to_station"], []))
        common = sorted(ka & kb)
        if common:
            c.at[i, "kv"] = common[0]          # lowest voltage both ends share (a line joins buses of one voltage)
            c.at[i, "from_kv"] = c.at[i, "to_kv"] = common[0]
            c.at[i, "element_type"] = "line"

    def elem(r):
        if r.element_type == "gtc":
            return f"{r.constraint_name} interface"
        kvs = [f"{k:g}" for k in (r.from_kv, r.to_kv) if np.isfinite(k) and k > 1]
        kv_txt = "/".join(dict.fromkeys(kvs))
        return f"{r.from_station}-{r.to_station} {kv_txt} kV".replace("  ", " ")

    c["element"] = [elem(r) for r in c.itertuples()]
    fr = [res.resolve(x) for x in c["from_station"]]
    to = [res.resolve(x) for x in c["to_station"]]
    c["from_sub"] = [x["sub"] for x in fr]
    c["to_sub"] = [x["sub"] for x in to]
    c["from_node"] = [x["osm_id"] for x in fr]
    c["to_node"] = [x["osm_id"] for x in to]
    c["endpoint_conf"] = [min(a["conf"], b["conf"]) if (a["osm_id"] and b["osm_id"]) else 0.0 for a, b in zip(fr, to)]
    c["endpoint_how"] = [f"{a['how']}|{b['how']}" for a, b in zip(fr, to)]
    # reject element ends that cannot be one line: a 138 kV element longer than ELEMENT_MAX_KM_LOW, or any element longer than ELEMENT_MAX_KM_HIGH,
    # means at least one of the two placements (agent 1c or the alias step) is wrong; neither end is then used for hops or the proxy
    for i, (a, b) in enumerate(zip(fr, to)):
        if a["osm_id"] and b["osm_id"] and np.isfinite(a["lat"]) and np.isfinite(b["lat"]) and c.at[i, "element_type"] == "line":
            km = float(N.haversine_km(a["lat"], a["lon"], b["lat"], b["lon"]))
            lim = ELEMENT_MAX_KM_HIGH if (np.isfinite(c.at[i, "kv"]) and c.at[i, "kv"] >= 230) else ELEMENT_MAX_KM_LOW
            if km > lim:
                c.at[i, "from_node"] = c.at[i, "to_node"] = ""
                c.at[i, "endpoint_conf"] = 0.0
                c.at[i, "endpoint_how"] = f"rejected_{km:.0f}km|" + c.at[i, "endpoint_how"]
    # ---- GTC flag
    gtc = pd.read_csv(paths.REPO_DATA / "gtc.csv", keep_default_na=False)
    gtc_name = dict(zip(gtc["code"], gtc["name"]))
    c["gtc"] = c["element_type"].eq("gtc") | c["constraint_name"].isin(gtc_name)
    c["gtc_name"] = c["constraint_name"].map(gtc_name).fillna("")
    # ---- annual rent, element level, and allocation across the element's cids
    ann_i = ann.set_index("code")
    c["annual_rent_2025_musd"] = c["annual_element"].map(ann_i["rent25"])
    c["annual_rent_market"] = c["annual_element"].map(ann_i["rent25_market"]).replace("", np.nan)
    c["annual_rent_2024_musd"] = c["annual_element"].map(ann_i["rent24"])
    c["annual_binding_hours"] = c["annual_element"].map(ann_i["hours25"])
    # window rent used for the allocation share: RT first, DAM otherwise, structural rows 1
    w_rt = c["rt_rent_est_musd"].where(c["rt_rent_est_musd"] > 0, 0.0)
    w_dm = c["dam_rent_est_musd"].where(c["dam_rent_est_musd"] > 0, 0.0)
    el_rt = w_rt.groupby(c["annual_element"]).transform("sum")
    el_dm = w_dm.groupby(c["annual_element"]).transform("sum")
    n_el = c.groupby("annual_element")["cid"].transform("size")
    share = np.where(el_rt > 0, w_rt / el_rt.replace(0, np.nan), np.where(el_dm > 0, w_dm / el_dm.replace(0, np.nan), 1.0 / n_el))
    c["annual_share"] = np.where(c["annual_element"] == "", np.nan, share)
    c["annual_rent_alloc_musd"] = (c["annual_rent_2025_musd"] * c["annual_share"]).fillna(0.0)
    # ---- planned upgrades
    pidx = ProjectIndex(sub_all, sp)
    prow = []
    for r in c.itertuples():
        m = None
        if r.gtc:
            m = pidx.gtc_match(r.constraint_name)
        else:
            a, b = r.from_sub, r.to_sub
            if not a and r.from_station:
                a = r.from_station if r.from_station in sub_all.index else ""
            if not b and r.to_station:
                b = r.to_station if r.to_station in sub_all.index else ""
            m = pidx.match(a, b, r.element_type, r.kv)
        prow.append(m or {})
    pm = pd.DataFrame(prow).reindex(columns=["project_id", "project_name", "in_service_date", "status", "match_conf", "match_method", "n_matches"])
    pm.index = c.index
    c["upgrade_project_id"] = pm["project_id"].fillna("")
    c["upgrade_project_name"] = pm["project_name"].fillna("")
    c["upgrade_in_service_date"] = pm["in_service_date"].fillna("")
    c["upgrade_status"] = pm["status"].fillna("")
    c["upgrade_match_conf"] = pm["match_conf"]
    c["upgrade_match_method"] = pm["match_method"].fillna("")
    c["upgrade_n_matches"] = pm["n_matches"].fillna(0).astype(int)
    # ---- priority score
    S_REF = float(c["annual_rent_alloc_musd"].max()) or 1.0
    R_REF = float(c["rt_rent_est_musd"].max()) or 1.0
    D_REF = float(c["dam_rent_est_musd"].max()) or 1.0
    c["priority_struct"] = (c["annual_rent_alloc_musd"] / S_REF).clip(0, 1)
    c["priority_current"] = W_CURRENT_RT * (c["rt_rent_est_musd"] / R_REF).clip(0, 1) + W_CURRENT_DAM * (c["dam_rent_est_musd"] / D_REF).clip(0, 1)
    c["priority_score"] = 100.0 * (W_STRUCT * c["priority_struct"] + W_CURRENT * c["priority_current"])
    c = c.sort_values(["priority_score", "rt_rent_est_musd", "dam_rent_est_musd", "cid"], ascending=[False, False, False, True]).reset_index(drop=True)
    c["priority_rank"] = np.arange(1, len(c) + 1)
    return c


CONSTRAINT_COLUMNS = [
    "cid", "constraint_name", "contingency_name", "element", "element_type", "from_station", "to_station", "kv", "from_sub", "to_sub", "endpoint_conf",
    "endpoint_how", "gtc", "gtc_name", "source", "window_binding", "rt_binding", "dam_binding",
    "rt_n_runs_binding", "rt_hours_binding", "rt_mean_shadow", "rt_p95_shadow", "rt_max_shadow", "rt_rent_proxy", "rt_rent_est_musd", "rt_pct_at_cap",
    "dam_n_hours", "dam_hours_binding", "dam_mean_shadow", "dam_p95_shadow", "dam_max_shadow", "dam_rent_proxy", "dam_rent_est_musd",
    "median_limit_mw", "median_flow_mw",
    "annual_element", "annual_join", "annual_rent_2025_musd", "annual_rent_market", "annual_rent_2024_musd", "annual_binding_hours", "annual_share",
    "annual_rent_alloc_musd",
    "upgrade_project_id", "upgrade_project_name", "upgrade_in_service_date", "upgrade_status", "upgrade_match_conf", "upgrade_match_method", "upgrade_n_matches",
    "priority_struct", "priority_current", "priority_score", "priority_rank", "from_node", "to_node",
]


# ----------------------------------------------------------------------------------------------------------------
# Price matrices
# ----------------------------------------------------------------------------------------------------------------
def _stamp(*files) -> str:
    return json.dumps([[str(f.name), f.stat().st_size, int(f.stat().st_mtime)] for f in files])


def _read_bus_matrix(path, ts_col: str, ts_keep: np.ndarray) -> tuple[np.ndarray, pd.Index, np.ndarray]:
    """Dense (runs x buses) float32 matrix of LMP from a long parquet, streaming by row group (the table has 14-31M rows)."""
    import pyarrow.parquet as pq

    pf = pq.ParquetFile(path)
    ti = pd.Index(ts_keep)
    bl = []
    for g in range(pf.metadata.num_row_groups):
        bl.append(pd.Series(pf.read_row_group(g, columns=["bus"]).column("bus").to_pylist()).unique())
    buses = pd.Index(pd.unique(np.concatenate(bl)))
    Y = np.full((len(ts_keep), len(buses)), np.nan, dtype=np.float32)
    for g in range(pf.metadata.num_row_groups):
        t = pf.read_row_group(g, columns=[ts_col, "bus", "lmp"])
        r = ti.get_indexer(t.column(ts_col).to_numpy().astype("datetime64[us]").astype("int64"))
        c = buses.get_indexer(t.column("bus").to_pylist())
        m = r >= 0
        Y[r[m], c[m]] = t.column("lmp").to_numpy()[m]
    return ts_keep, buses, Y


def _all_ts(path, ts_col: str) -> np.ndarray:
    import pyarrow.parquet as pq

    pf = pq.ParquetFile(path)
    out = []
    for g in range(pf.metadata.num_row_groups):
        out.append(np.unique(pf.read_row_group(g, columns=[ts_col]).column(ts_col).to_numpy().astype("datetime64[us]").astype("int64")))
    return np.unique(np.concatenate(out))


class Market:
    """One market's aligned data: congestion matrix (runs x buses) and shadow-price design table (runs x cids)."""

    def __init__(self, tag, ts, buses, cong, shadow, at_cap=None):
        self.tag, self.ts, self.buses, self.cong, self.shadow, self.at_cap = tag, ts, buses, cong, shadow, at_cap


def load_market(tag: str, refresh: bool = False) -> Market:
    """tag 'rt' (SCED runs, lmp_bus - lambda vs shadow_sced) or 'dam' (hourly, lmp_dam_bus - lambda_dam vs shadow_dam).

    Only runs present in the price file, the lambda file and the shadow table are kept; a run with no shadow rows would
    otherwise look like "nothing binding" (agent 1a: shadow rows lag the prices by about an hour).
    """
    D = paths.DERIVED
    if tag == "rt":
        pf_, lam_f, sh_f, ts_col = D / "lmp_bus.parquet", D / "lambda.parquet", D / "shadow_sced.parquet", "sced_ts"
    else:
        pf_, lam_f, sh_f, ts_col = D / "lmp_dam_bus.parquet", D / "lambda_dam.parquet", D / "shadow_dam.parquet", "interval_start_utc"
    stamp = _stamp(pf_, lam_f, sh_f)
    cf = CACHE / f"{tag}_meta.json"
    if not refresh and cf.exists() and json.loads(cf.read_text())["stamp"] == stamp:
        ts = np.load(CACHE / f"{tag}_ts.npy")
        buses = pd.Index(json.loads((CACHE / f"{tag}_buses.json").read_text()))
        cong = np.load(CACHE / f"{tag}_cong.npy", mmap_mode="r")
        shadow = pd.read_parquet(CACHE / f"{tag}_shadow.parquet")
        shadow.index = ts
        cap = None
        if (CACHE / f"{tag}_cap.parquet").exists():
            cap = pd.read_parquet(CACHE / f"{tag}_cap.parquet")
            cap.index = ts
        return Market(tag, ts, buses, cong, shadow, cap)
    lam = pd.read_parquet(lam_f)
    sh = pd.read_parquet(sh_f, columns=[ts_col, "cid", "shadow_price"] + (["max_shadow_price"] if tag == "rt" else []))
    ts_all = _all_ts(pf_, ts_col)
    lam_ts = N._us(lam[ts_col])
    sh["t"] = N._us(sh[ts_col])
    ok = np.isin(ts_all, lam_ts) & np.isin(ts_all, sh["t"].unique())
    ts = ts_all[ok]
    _, buses, Y = _read_bus_matrix(pf_, ts_col, ts)
    lamv = lam.set_index(lam_ts)["lambda"].reindex(ts).to_numpy().astype(np.float32)
    cong = Y - lamv[:, None]
    del Y
    sh = sh[sh["t"].isin(ts)]
    shadow = sh.groupby(["t", "cid"])["shadow_price"].max().unstack(fill_value=0.0).reindex(ts, fill_value=0.0).astype(np.float64)
    cap = None
    if tag == "rt":
        sh["cap"] = (sh["shadow_price"] > 0) & (sh["shadow_price"] >= sh["max_shadow_price"] - AT_CAP_TOL)
        cap = sh.groupby(["t", "cid"])["cap"].max().unstack(fill_value=False).reindex(ts, fill_value=False).reindex(columns=shadow.columns, fill_value=False)
    np.save(CACHE / f"{tag}_ts.npy", ts)
    np.save(CACHE / f"{tag}_cong.npy", cong)
    (CACHE / f"{tag}_buses.json").write_text(json.dumps(list(buses)))
    shadow.reset_index(drop=True).to_parquet(CACHE / f"{tag}_shadow.parquet")
    if cap is not None:
        cap.reset_index(drop=True).to_parquet(CACHE / f"{tag}_cap.parquet")
    cf.write_text(json.dumps({"stamp": stamp}))
    shadow.index = ts
    return Market(tag, ts, buses, cong, shadow, cap)


# ----------------------------------------------------------------------------------------------------------------
# Regression
# ----------------------------------------------------------------------------------------------------------------
R2_MIN_STD = 0.1                  # ASSUMPTION: a bus whose holdout congestion has std below this ($/MWh) has no meaningful R2 (NaN)


def collinear_groups(X: pd.DataFrame, thresh: float) -> list[list[str]]:
    """Connected components of cids whose shadow-price series have corr >= thresh."""
    C = np.corrcoef(X.to_numpy().T)
    np.fill_diagonal(C, 0.0)
    K = C.shape[0]
    seen, groups = set(), []
    for i in range(K):
        if i in seen:
            continue
        stack, comp = [i], []
        seen.add(i)
        while stack:
            j = stack.pop()
            comp.append(j)
            for k in np.where(C[j] >= thresh)[0]:
                if int(k) not in seen:
                    seen.add(int(k))
                    stack.append(int(k))
        groups.append(sorted(X.columns[comp]))
    return groups


def high_corr_pairs(X: pd.DataFrame, lo: float, hi: float) -> pd.DataFrame:
    C = np.corrcoef(X.to_numpy().T)
    rows = []
    K = C.shape[0]
    for i in range(K):
        for j in range(i + 1, K):
            if lo <= C[i, j] < hi:
                rows.append((X.columns[i], X.columns[j], float(C[i, j])))
    return pd.DataFrame(rows, columns=["cid_a", "cid_b", "corr"])


def ridge_solve(X: np.ndarray, Y: np.ndarray, rows: np.ndarray, alpha) -> np.ndarray:
    """beta (K x B) of Y[rows] ~ X[rows] @ beta, no intercept, ridge penalty alpha.

    Y may hold NaN (buses that did not exist for all runs): those buses are solved per availability pattern.
    """
    K = X.shape[1]
    pen = np.diag(np.broadcast_to(np.asarray(alpha, dtype=float), (K,)))     # scalar or one penalty per regressor
    Xr = X[rows]
    Yr = np.asarray(Y[rows])
    nan = np.isnan(Yr)
    out = np.empty((K, Yr.shape[1]), dtype=np.float64)
    full = ~nan.any(0)
    if full.any():
        out[:, full] = np.linalg.solve(Xr.T @ Xr + pen, Xr.T @ Yr[:, full].astype(np.float64))
    pats: dict[bytes, list[int]] = defaultdict(list)
    for j in np.where(~full)[0]:
        pats[nan[:, j].tobytes()].append(int(j))
    for _, js in pats.items():
        m = ~nan[:, js[0]]
        Xm = Xr[m]
        out[:, js] = np.linalg.solve(Xm.T @ Xm + pen, Xm.T @ Yr[m][:, js].astype(np.float64))
    return out


def choose_alpha(X: np.ndarray, Y: np.ndarray, train: np.ndarray, focal: np.ndarray | None = None) -> tuple[float, dict]:
    """Blocked time-series CV inside the training rows (CV_BLOCKS contiguous blocks); focal groups keep RELAX_ALPHA throughout."""
    idx = np.where(train)[0]
    blocks = np.array_split(idx, CV_BLOCKS)
    scores = {}
    for a in RIDGE_GRID:
        sse, n = 0.0, 0
        for b in blocks:
            tr = train.copy()
            tr[b] = False
            te = np.zeros_like(train)
            te[b] = True
            beta = ridge_solve(X, Y, tr, a if focal is None else np.where(focal, RELAX_ALPHA, a))
            res = np.asarray(Y[te]).astype(np.float64) - X[te] @ beta
            sse += float(np.nansum(res**2))
            n += int((~np.isnan(res)).sum())
        scores[a] = float(np.sqrt(sse / max(n, 1)))
    best = min(scores, key=scores.get)
    return best, scores


def per_bus_metrics(Y: np.ndarray, pred: np.ndarray, rows: np.ndarray) -> pd.DataFrame:
    y = np.asarray(Y[rows]).astype(np.float64)
    p = pred[rows]
    res = y - p
    n = (~np.isnan(y)).sum(0)
    sse = np.nansum(res**2, axis=0)
    ym = np.nanmean(y, axis=0)
    sst = np.nansum((y - ym) ** 2, axis=0)
    sst0 = np.nansum(y**2, axis=0)
    std = np.sqrt(sst / np.maximum(n, 1))
    with np.errstate(divide="ignore", invalid="ignore"):
        r2 = np.where(std >= R2_MIN_STD, 1.0 - sse / np.where(sst > 0, sst, np.nan), np.nan)
    return pd.DataFrame({"n": n, "rmse": np.sqrt(sse / np.maximum(n, 1)), "rmse_null": np.sqrt(sst0 / np.maximum(n, 1)), "std": std, "r2": r2})


def design(mk: Market, min_bind: int, exclude: set[str] | None = None) -> pd.DataFrame:
    nb = (mk.shadow > 0).sum()
    cols = [c for c in mk.shadow.columns if nb[c] >= min_bind and not (exclude and c in exclude)]
    return mk.shadow[cols]


class Fit:
    """Result of one market's fit."""

    def __init__(self, tag=""):
        self.tag = tag
        self.alpha = np.nan
        self.alpha_cv = {}
        self.cids = []
        self.groups = []
        self.group_of = {}
        self.SF = None
        self.se = None
        self.vif = None
        self.sigma = None
        self.val = None
        self.val_alpha = np.nan
        self.val_alpha_cv = {}
        self.holdout_rows = None
        self.ngroups = 0
        self.nobs = 0
        self.pairs = None
        self.unseen = None
        self.Xg = None
        self.val_beta = None
        self.val_act = None
        self.shrink = None
        self.focal = None


def fit_market(mk: Market, min_bind: int, holdout: np.ndarray, label: str = "", exclude_runs: np.ndarray | None = None,
               exclude_cids: set[str] | None = None, validate: bool = True, focal_cids: set[str] | None = None) -> Fit:
    """Collinear-group merge, ridge-penalty choice by blocked CV, validation fit on the training part, final fit on everything."""
    f = Fit(mk.tag + label)
    keep_rows = np.ones(len(mk.ts), dtype=bool) if exclude_runs is None else ~exclude_runs
    Xdf = design(mk, min_bind, exclude_cids)
    if exclude_runs is not None:                      # binding counts must be counted on the rows actually used
        Xdf = Xdf.loc[:, (Xdf.loc[keep_rows] > 0).sum() >= min_bind]
    groups = collinear_groups(Xdf.loc[keep_rows], COLLINEAR_CORR) if Xdf.shape[1] > 1 else [[c] for c in Xdf.columns]
    f.groups = groups
    f.pairs = high_corr_pairs(Xdf.loc[keep_rows], COLLINEAR_WARN_CORR, 1.01) if Xdf.shape[1] > 1 else pd.DataFrame(columns=["cid_a", "cid_b", "corr"])
    Xg = pd.DataFrame({f"G{i:03d}": Xdf[g].sum(axis=1) for i, g in enumerate(groups)}, index=Xdf.index)
    f.group_of = {c: f"G{i:03d}" for i, g in enumerate(groups) for c in g}
    f.cids = list(Xdf.columns)
    focal = np.array([bool(focal_cids) and any(c in focal_cids for c in g) for g in groups])
    f.focal = focal
    X = Xg.to_numpy(dtype=np.float64)
    Y = mk.cong
    train = keep_rows & ~holdout
    f.holdout_rows = holdout & keep_rows
    if validate:
        act = (X[train] > 0).sum(0) >= MIN_TRAIN_BINDING
        Xv = X[:, act]
        fv = focal[act] if focal.any() else None
        a_v, sc = choose_alpha(Xv, Y, train, fv)
        beta = ridge_solve(Xv, Y, train, a_v if fv is None else np.where(fv, RELAX_ALPHA, a_v))
        pred = Xv @ beta
        f.val_alpha, f.val_alpha_cv = a_v, sc
        f.val_beta, f.val_act = beta.astype(np.float32), act
        mt = per_bus_metrics(Y, pred, f.holdout_rows)
        mi = per_bus_metrics(Y, pred, train)
        f.val = pd.DataFrame({"n_test": mt["n"].to_numpy(), "rmse_oos": mt["rmse"].to_numpy(), "rmse_null": mt["rmse_null"].to_numpy(),
                              "std_test": mt["std"].to_numpy(), "r2_oos": mt["r2"].to_numpy(), "rmse_in": mi["rmse"].to_numpy(),
                              "r2_in": mi["r2"].to_numpy()}, index=mk.buses)
        mass = X[f.holdout_rows].sum(0)
        f.unseen = dict(n_groups_holdout=int((mass > 0).sum()), n_unseen=int(((mass > 0) & ~act).sum()),
                        mass_share_unseen=float(mass[~act].sum() / max(mass.sum(), 1e-9)), n_groups_fitted=int(act.sum()))
    ff = focal if focal.any() else None
    a_f, sc_f = choose_alpha(X, Y, keep_rows, ff)
    f.alpha, f.alpha_cv = a_f, sc_f
    pen_f = a_f if ff is None else np.where(ff, RELAX_ALPHA, a_f)
    beta = ridge_solve(X, Y, keep_rows, pen_f)
    f.SF = (-beta).astype(np.float32)                    # SF = -beta  (cong = -X SF)
    Xk = X[keep_rows]
    XtX = Xk.T @ Xk
    M = np.linalg.inv(XtX + np.diag(np.broadcast_to(np.asarray(pen_f, dtype=float), (X.shape[1],))))
    d = np.einsum("ij,jk,ki->i", M, XtX, M)
    res = np.asarray(Y[keep_rows]).astype(np.float64) - Xk @ beta
    n_eff = np.maximum((~np.isnan(res)).sum(0) - X.shape[1], 1)
    f.sigma = np.sqrt(np.nansum(res**2, axis=0) / n_eff)
    f.se = (np.sqrt(np.maximum(d, 0.0))[:, None] * f.sigma[None, :]).astype(np.float32)
    Cx = np.corrcoef(X[keep_rows].T) if X.shape[1] > 1 else np.eye(1)
    try:
        f.vif = np.diag(np.linalg.inv(Cx + 1e-9 * np.eye(Cx.shape[0])))
    except np.linalg.LinAlgError:
        f.vif = np.full(X.shape[1], np.inf)
    ss = (Xk**2).sum(0)
    f.shrink = ss / (ss + pen_f)
    f.ngroups, f.nobs = X.shape[1], int(keep_rows.sum())
    f.Xg = Xg
    return f


def rt_holdout_mask(ts: np.ndarray) -> np.ndarray:
    return ts > ts[-1] - int(HOLDOUT_RT_HOURS * 3600 * 1e6)


def dam_holdout_mask(ts: np.ndarray) -> np.ndarray:
    return ts > ts[-1] - int(HOLDOUT_DAM_DAYS * 24 * 3600 * 1e6)


# ----------------------------------------------------------------------------------------------------------------
# Outputs of the price regressions
# ----------------------------------------------------------------------------------------------------------------
SF_SUB = CACHE / "sf_sub.parquet"
SUMMARY_JSON = paths.DERIVED / "p2_summary.json"
SF_VALIDATION_CSV = paths.REPO_DATA / "sf_validation.csv"
SF_RT_VS_DAM_CSV = paths.REPO_DATA / "sf_rt_vs_dam.csv"
SF_GROUPS_CSV = paths.REPO_DATA / "sf_groups.csv"
SF_PROXY_VALIDATION_CSV = paths.REPO_DATA / "sf_proxy_validation.csv"
SHIFT_FACTORS_CSV = paths.REPO_DATA / "shift_factors.csv"
TOP_BIG_SF = 0.05                 # ASSUMPTION: |SF| at or above this counts as "large" when comparing RT and DAM
TOP_BUS_OVERLAP_N = 20            # ASSUMPTION: compare the N buses with the largest |SF| in RT and DAM
SENS_MIN_BIND = (5, 20)           # ASSUMPTION: alternative MIN_BINDING_RUNS values reported as a sensitivity


def _bus_tables(buses: pd.Index):
    sp = N.load_settlement_points()
    info = sp.drop_duplicates("ELECTRICAL_BUS").set_index("ELECTRICAL_BUS")
    sub = info["SUBSTATION"].reindex(buses)
    kv = info["kv"].reindex(buses)
    rn = info["RESOURCE_NODE"].reindex(buses)
    return sub, kv, rn


def cid_status(f: Fit, mk: Market, method: str) -> pd.DataFrame:
    """One row per fitted cid: group, vif, n binding, and whether the SF is kept (NaN rule)."""
    nb = (mk.shadow > 0).sum()
    rows = []
    for gi, g in enumerate(f.groups):
        gname = f"G{gi:03d}"
        vif = float(f.vif[gi])
        keep = vif <= VIF_MAX
        for cid in g:
            rows.append(dict(cid=cid, method=method, group=gname, group_size=len(g), vif=vif, n_binding=int(nb[cid]),
                             shrink=float(f.shrink[gi]), status="ok" if keep else "nan_collinear"))
    return pd.DataFrame(rows)


def long_rows(mk: Market, f: Fit, method: str, st: pd.DataFrame) -> pd.DataFrame:
    keep_g = {r.group for r in st.itertuples() if r.status == "ok"}
    parts = []
    for gi, g in enumerate(f.groups):
        gname = f"G{gi:03d}"
        if gname not in keep_g:
            continue
        sf, se = f.SF[gi], f.se[gi]
        idx = np.where(np.abs(sf) >= SF_STORE_MIN)[0]
        weak = np.abs(sf[idx]) < T_MIN * se[idx]
        qual = np.where(weak, "weak", "ok").astype("U24")
        if len(g) > 1:
            qual = np.char.add(qual, "+grouped")
        if f.vif[gi] > VIF_FLAG:
            qual = np.char.add(qual, "+collinear")
        if f.shrink[gi] < SHRINK_MIN:
            qual = np.char.add(qual, "+shrunk")
        qual = np.where(np.abs(sf[idx]) > SF_MAX_ABS, np.char.add(qual, "+implausible"), qual)
        for cid in g:
            parts.append(pd.DataFrame({"bus": mk.buses.to_numpy()[idx], "cid": cid, "sf": sf[idx], "method": method, "se": se[idx], "quality": qual}))
    out = pd.concat(parts, ignore_index=True) if parts else pd.DataFrame(columns=["bus", "cid", "sf", "method", "se", "quality"])
    for c in ("bus", "cid", "method", "quality"):
        out[c] = out[c].astype("category")
    out["sf"] = out["sf"].astype("float32")
    out["se"] = out["se"].astype("float32")
    return out


def sub_table(mk: Market, f: Fit, method: str, st: pd.DataFrame) -> pd.DataFrame:
    """Substation-level SF: median over the substation's highest-kV buses; range over all its buses."""
    sub, kv, _ = _bus_tables(mk.buses)
    ok_g = {r.group for r in st.itertuples() if r.status == "ok"}
    gi_ok = [i for i in range(len(f.groups)) if f"G{i:03d}" in ok_g]
    df = pd.DataFrame(f.SF[gi_ok].T, columns=[f"G{i:03d}" for i in gi_ok])
    sef = pd.DataFrame(f.se[gi_ok].T, columns=df.columns)
    df["sub"] = sub.to_numpy()
    df["kv"] = kv.to_numpy()
    sef["sub"] = df["sub"]
    df = df[df["sub"].notna()]
    sef = sef.loc[df.index]
    topkv = df.groupby("sub")["kv"].transform("max")
    top = df[df["kv"] >= topkv - 0.5].drop(columns=["kv"])
    med = top.groupby("sub").median()
    sef_med = sef.loc[top.index].groupby("sub").median()
    allb = df.drop(columns=["kv"]).groupby("sub")
    mn, mx = allb.min(), allb.max()
    parts = []
    for gname in med.columns:
        gi = int(gname[1:])
        for cid in f.groups[gi]:
            q = np.where(np.abs(med[gname]) < T_MIN * sef_med[gname], "weak", "ok").astype("U24")
            if len(f.groups[gi]) > 1:
                q = np.char.add(q, "+grouped")
            if f.vif[gi] > VIF_FLAG:
                q = np.char.add(q, "+collinear")
            if f.shrink[gi] < SHRINK_MIN:
                q = np.char.add(q, "+shrunk")
            q = np.where(np.abs(med[gname].to_numpy()) > SF_MAX_ABS, np.char.add(q, "+implausible"), q)
            parts.append(pd.DataFrame({"sub": med.index, "cid": cid, "method": method, "sf": med[gname].to_numpy(), "se": sef_med[gname].to_numpy(),
                                       "sf_min": mn[gname].to_numpy(), "sf_max": mx[gname].to_numpy(), "quality": q}))
    return pd.concat(parts, ignore_index=True)


def rt_vs_dam(rt: tuple, dam: tuple, rt_st: pd.DataFrame, dam_st: pd.DataFrame, cons: pd.DataFrame) -> pd.DataFrame:
    (mk_r, f_r), (mk_d, f_d) = rt, dam
    common_buses = mk_r.buses.intersection(mk_d.buses)
    ir, idd = mk_r.buses.get_indexer(common_buses), mk_d.buses.get_indexer(common_buses)
    gr = {c: int(g[1:]) for c, g in zip(rt_st["cid"], rt_st["group"]) if (rt_st.set_index("cid").loc[c, "status"] == "ok")}
    gd = {c: int(g[1:]) for c, g in zip(dam_st["cid"], dam_st["group"]) if (dam_st.set_index("cid").loc[c, "status"] == "ok")}
    nbr = (mk_r.shadow > 0).sum()
    nbd = (mk_d.shadow > 0).sum()
    shr = rt_st.set_index("cid")["shrink"]
    shd = dam_st.set_index("cid")["shrink"]
    rows = []
    for cid in sorted(set(gr) & set(gd)):
        a, b = f_r.SF[gr[cid]][ir].astype(np.float64), f_d.SF[gd[cid]][idd].astype(np.float64)
        big = (np.abs(a) >= TOP_BIG_SF) | (np.abs(b) >= TOP_BIG_SF)
        corr = float(np.corrcoef(a, b)[0, 1]) if a.std() > 0 and b.std() > 0 else np.nan
        slope = float((a @ b) / (a @ a)) if (a @ a) > 0 else np.nan
        ta, tb = set(np.argsort(-np.abs(a))[:TOP_BUS_OVERLAP_N]), set(np.argsort(-np.abs(b))[:TOP_BUS_OVERLAP_N])
        rows.append(dict(cid=cid, n_runs_rt=int(nbr[cid]), n_hours_dam=int(nbd[cid]), shrink_rt=float(shr[cid]), shrink_dam=float(shd[cid]), corr=corr, slope_dam_on_rt=slope,
                         n_big=int(big.sum()), sign_agree_big=float((np.sign(a[big]) == np.sign(b[big])).mean()) if big.any() else np.nan,
                         top_bus_overlap=len(ta & tb) / TOP_BUS_OVERLAP_N, max_abs_rt=float(np.abs(a).max()), max_abs_dam=float(np.abs(b).max())))
    return pd.DataFrame(rows)


def resource_node_check(mk: Market, f: Fit) -> dict:
    """Do the validation-fit SFs reproduce observed congestion at resource nodes?  Uses lmp_node (NP6-788), not lmp_bus."""
    sp = N.load_settlement_points()
    rn = sp[sp["RESOURCE_NODE"] != ""].drop_duplicates("RESOURCE_NODE").set_index("RESOURCE_NODE")["ELECTRICAL_BUS"]
    cols = mk.buses.get_indexer(rn.to_numpy())
    ok = cols >= 0
    rn, cols = rn[ok], cols[ok]
    ln = pd.read_parquet(paths.DERIVED / "lmp_node.parquet")
    ln = ln[ln["settlement_point"].isin(rn.index)]
    ln["t"] = N._us(ln["sced_ts"])
    hold_ts = mk.ts[f.holdout_rows]
    piv = ln[ln["t"].isin(hold_ts)].pivot_table(index="t", columns="settlement_point", values="lmp", aggfunc="first").reindex(hold_ts)
    lam = pd.read_parquet(paths.DERIVED / "lambda.parquet")
    lam = lam.set_index(N._us(lam["sced_ts"]))["lambda"].reindex(hold_ts)
    obs = piv.sub(lam, axis=0)
    Xv = f.Xg.to_numpy()[:, f.val_act][f.holdout_rows]
    pred = Xv @ f.val_beta[:, cols].astype(np.float64)
    pred = pd.DataFrame(pred, index=hold_ts, columns=rn.index)
    obs = obs.reindex(columns=rn.index)
    # identity check: node LMP equals the LMP of its single bus
    bus_cong = np.asarray(mk.cong[f.holdout_rows][:, cols]).astype(np.float64)
    ident = float(np.nanmax(np.abs(obs.to_numpy() - bus_cong)))
    res = obs.to_numpy() - pred.to_numpy()
    n = (~np.isnan(res)).sum(0)
    rmse = np.sqrt(np.nansum(res**2, 0) / np.maximum(n, 1))
    sst = np.nansum((obs.to_numpy() - np.nanmean(obs.to_numpy(), 0)) ** 2, 0)
    with np.errstate(divide="ignore", invalid="ignore"):
        r2 = 1 - np.nansum(res**2, 0) / np.where(sst > 0, sst, np.nan)
    null = np.sqrt(np.nansum(obs.to_numpy() ** 2, 0) / np.maximum(n, 1))
    return dict(n_resource_nodes=int(len(rn)), n_holdout_runs=int(len(hold_ts)), median_r2=float(np.nanmedian(r2)), p10_r2=float(np.nanquantile(r2, 0.1)),
                median_rmse=float(np.median(rmse)), median_rmse_null=float(np.median(null)), max_abs_node_vs_bus_cong=ident,
                share_r2_above_ok=float(np.nanmean(r2 > R2_OK)))


def _fit_summary(tag, mk, f, st) -> dict:
    v = f.val
    return dict(tag=tag, n_obs=int(len(mk.ts)), n_buses=int(len(mk.buses)), n_cids_candidates=int(mk.shadow.shape[1]), n_cids_fitted=int(len(f.cids)),
                n_groups=int(f.ngroups), n_merged_groups=int(sum(len(g) > 1 for g in f.groups)), ridge_lambda=float(f.alpha), ridge_lambda_validation=float(f.val_alpha),
                n_focal_groups=int(f.focal.sum()) if f.focal is not None else 0, relax_alpha=RELAX_ALPHA, n_shrink_ge_min=int((f.shrink >= SHRINK_MIN).sum()),
                cv_rmse={str(k): round(x, 3) for k, x in f.val_alpha_cv.items()}, n_nan_collinear=int((st["status"] != "ok").sum()),
                vif_median=float(np.median(f.vif)), vif_max=float(np.max(f.vif)),
                median_r2_oos=float(v["r2_oos"].median()), p10_r2_oos=float(v["r2_oos"].quantile(0.1)), p90_r2_oos=float(v["r2_oos"].quantile(0.9)),
                share_r2_oos_above_ok=float((v["r2_oos"] > R2_OK).sum() / max(v["r2_oos"].notna().sum(), 1)),
                median_rmse_oos=float(v["rmse_oos"].median()), median_rmse_null=float(v["rmse_null"].median()),
                rms_rmse_oos=float(np.sqrt((v["rmse_oos"] ** 2).mean())), rms_rmse_null=float(np.sqrt((v["rmse_null"] ** 2).mean())),
                median_r2_in=float(v["r2_in"].median()), n_test=int(v["n_test"].max()), unseen=f.unseen)


def run_shift_factors(*args):
    """Fit RT and DAM shift factors, validate, and write sf_long.parquet, sf_validation.csv and friends."""
    import time

    fast = "fast" in args
    t0 = time.time()
    if not CONSTRAINTS_CSV.exists():
        run_constraints()
    summary: dict = {}
    res = {}
    cons0 = pd.read_csv(CONSTRAINTS_CSV, keep_default_na=False)
    focal = set(cons0.sort_values("priority_rank").head(PRIORITY_TOP_SF)["cid"])      # priority constraints get the lighter penalty RELAX_ALPHA
    for tag, mb, hm in (("rt", MIN_BINDING_RUNS, rt_holdout_mask), ("dam", MIN_BINDING_HOURS_DAM, dam_holdout_mask)):
        mk = load_market(tag)
        f = fit_market(mk, mb, hm(mk.ts), focal_cids=focal)
        st = cid_status(f, mk, tag + "_reg")
        res[tag] = (mk, f, st)
        summary[tag] = _fit_summary(tag, mk, f, st)
        print(f"[{tag}] fitted {f.ngroups} regressor groups, lambda={f.alpha:g}, median OOS R2={summary[tag]['median_r2_oos']:.3f} ({time.time() - t0:.0f}s)")
    (mk_r, f_r, st_r), (mk_d, f_d, st_d) = res["rt"], res["dam"]
    # ---- sf_long and substation table
    lr = long_rows(mk_r, f_r, "rt_reg", st_r)
    ld = long_rows(mk_d, f_d, "dam_reg", st_d)
    SF_LONG.parent.mkdir(parents=True, exist_ok=True)
    pd.concat([lr, ld], ignore_index=True).to_parquet(SF_LONG, index=False)
    sub_tbl = pd.concat([sub_table(mk_r, f_r, "rt_reg", st_r), sub_table(mk_d, f_d, "dam_reg", st_d)], ignore_index=True)
    sub_tbl.to_parquet(SF_SUB, index=False)
    print(f"sf_long rows: RT {len(lr):,}  DAM {len(ld):,}")
    # ---- validation csv (per bus)
    sub_r, kv_r, rn_r = _bus_tables(mk_r.buses)
    sub_d, kv_d, rn_d = _bus_tables(mk_d.buses)
    vr = f_r.val.add_prefix("rt_")
    vd = f_d.val.add_prefix("dam_")
    val = vr.join(vd, how="outer")
    sub_all = pd.concat([sub_r, sub_d]).groupby(level=0).first().reindex(val.index)
    kv_all = pd.concat([kv_r, kv_d]).groupby(level=0).first().reindex(val.index)
    rn_all = pd.concat([rn_r, rn_d]).groupby(level=0).first().reindex(val.index)
    val.insert(0, "sub", sub_all.to_numpy())
    val.insert(1, "kv", kv_all.to_numpy())
    val.insert(2, "resource_node", rn_all.fillna("").to_numpy())
    val.index.name = "bus"
    val.round(4).reset_index().to_csv(SF_VALIDATION_CSV, index=False)
    # ---- RT vs DAM, resource nodes, groups
    cons = pd.read_csv(CONSTRAINTS_CSV, keep_default_na=False)
    cmp_ = rt_vs_dam((mk_r, f_r), (mk_d, f_d), st_r, st_d, cons)
    cmp_.round(4).to_csv(SF_RT_VS_DAM_CSV, index=False)
    both = cmp_[(cmp_["shrink_rt"] >= SHRINK_MIN) & (cmp_["shrink_dam"] >= SHRINK_MIN)]
    summary["rt_vs_dam"] = dict(n_cids=int(len(cmp_)), median_corr=float(cmp_["corr"].median()), p10_corr=float(cmp_["corr"].quantile(0.1)),
                                share_corr_above_0p8=float((cmp_["corr"] > 0.8).mean()), median_slope=float(cmp_["slope_dam_on_rt"].median()),
                                median_sign_agree_big=float(cmp_["sign_agree_big"].median()), median_top_bus_overlap=float(cmp_["top_bus_overlap"].median()),
                                n_both_unshrunk=int(len(both)), median_corr_both_unshrunk=float(both["corr"].median()) if len(both) else None,
                                median_slope_both_unshrunk=float(both["slope_dam_on_rt"].median()) if len(both) else None,
                                median_sign_agree_both_unshrunk=float(both["sign_agree_big"].median()) if len(both) else None,
                                median_top_bus_overlap_both_unshrunk=float(both["top_bus_overlap"].median()) if len(both) else None)
    summary["resource_nodes_rt"] = resource_node_check(mk_r, f_r)
    sub_r_, kv_r_, rn_r_ = sub_r, kv_r, rn_r
    for tag, (mk, f, st) in res.items():
        isr = rn_all.reindex(f.val.index).fillna("") != ""
        summary[tag]["median_r2_oos_resource_node_buses"] = float(f.val.loc[isr.to_numpy(), "r2_oos"].median())
        summary[tag]["median_rmse_oos_resource_node_buses"] = float(f.val.loc[isr.to_numpy(), "rmse_oos"].median())
    grp = []
    for tag, (mk, f, st) in res.items():
        for gi, g in enumerate(f.groups):
            if len(g) > 1:
                grp.append(dict(market=tag, group=f"G{gi:03d}", n_members=len(g), cids=";".join(g), vif=float(f.vif[gi])))
        for r in f.pairs.itertuples():
            grp.append(dict(market=tag, group="pair>=%.2f" % COLLINEAR_WARN_CORR, n_members=2, cids=f"{r.cid_a};{r.cid_b}", vif=float(r.corr)))
    pd.DataFrame(grp, columns=["market", "group", "n_members", "cids", "vif"]).to_csv(SF_GROUPS_CSV, index=False)
    summary["collinear"] = dict(rt_merged=int(sum(len(g) > 1 for g in f_r.groups)), dam_merged=int(sum(len(g) > 1 for g in f_d.groups)),
                                rt_pairs_warn=int(len(f_r.pairs)), dam_pairs_warn=int(len(f_d.pairs)))
    # ---- sensitivity (RT only)
    if not fast:
        sens = []
        base_sf = {c: f_r.SF[int(g[1:])] for c, g in zip(st_r["cid"], st_r["group"])}
        hold = rt_holdout_mask(mk_r.ts)
        variants = [(f"min_binding_runs={m}", dict(min_bind=m)) for m in SENS_MIN_BIND]
        variants.append(("drop runs with an at-cap shadow price", dict(min_bind=MIN_BINDING_RUNS, drop_cap=True)))
        for name, kw in variants:
            ex = None
            if kw.get("drop_cap") and mk_r.at_cap is not None:
                ex = mk_r.at_cap.any(axis=1).to_numpy()
            fv = fit_market(mk_r, kw["min_bind"], hold, label="_" + name, exclude_runs=ex, focal_cids=focal)
            sv = _fit_summary("rt", mk_r, fv, cid_status(fv, mk_r, "rt_reg"))
            cors = []
            for gi, g in enumerate(fv.groups):
                for c in g:
                    if c in base_sf:
                        a, b = base_sf[c], fv.SF[gi]
                        if a.std() > 0 and b.std() > 0:
                            cors.append(float(np.corrcoef(a, b)[0, 1]))
            sens.append(dict(variant=name, n_runs_used=int(len(mk_r.ts) - (0 if ex is None else ex.sum())), n_groups=sv["n_groups"], median_r2_oos=sv["median_r2_oos"],
                             rms_rmse_oos=sv["rms_rmse_oos"], median_sf_corr_with_base=float(np.median(cors)) if cors else np.nan))
            print(f"[sens] {name}: R2={sv['median_r2_oos']:.3f} corr_with_base={sens[-1]['median_sf_corr_with_base']:.3f}")
        summary["sensitivity_rt"] = sens
    # ---- constraint table: SF status columns
    update_constraints_sf_status(res, cmp_)
    write_shift_factors_csv()
    if fast and SUMMARY_JSON.exists():
        old = json.loads(SUMMARY_JSON.read_text())
        if "sensitivity_rt" in old:
            summary["sensitivity_rt"] = old["sensitivity_rt"]       # not recomputed in a fast run
    SUMMARY_JSON.write_text(json.dumps(_clean_json(summary), indent=1))
    print(f"shift-factors done in {time.time() - t0:.0f}s: {SF_LONG.name}, {SF_VALIDATION_CSV.name}, {SF_RT_VS_DAM_CSV.name}, {SF_GROUPS_CSV.name}")


def _clean_json(o):
    if isinstance(o, dict):
        return {str(k): _clean_json(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_clean_json(v) for v in o]
    if isinstance(o, (np.floating, float)):
        return None if not np.isfinite(o) else float(o)
    if isinstance(o, (np.integer,)):
        return int(o)
    return o


SF_STATUS = CACHE / "sf_status.parquet"
SF_RT_DAM_CORR = CACHE / "sf_rt_dam_corr.parquet"


def update_constraints_sf_status(res: dict | None = None, cmp_: pd.DataFrame | None = None):
    """Add per-cid regression status columns (sf_rt*, sf_dam*) to data/constraints.csv. Idempotent.

    With `res` (just fitted) the status tables are saved to the cache; without it they are read back, so `constraints` can be
    re-run without refitting.
    """
    if res is not None:
        st = pd.concat([x[2].assign(market=t, n_binding_total=0) for t, x in res.items()], ignore_index=True)
        st.to_parquet(SF_STATUS, index=False)
        if cmp_ is not None:
            cmp_[["cid", "corr"]].to_parquet(SF_RT_DAM_CORR, index=False)
    if not SF_STATUS.exists():
        return
    st = pd.read_parquet(SF_STATUS)
    c = pd.read_csv(CONSTRAINTS_CSV, keep_default_na=False)
    c = c.drop(columns=[x for x in c.columns if x.startswith("sf_")])
    for tag in ("rt", "dam"):
        s_ = st[st["market"] == tag].set_index("cid")
        sh = pd.read_parquet(CACHE / f"{tag}_shadow.parquet")
        nb = (sh > 0).sum()
        def status(x):
            if x in s_.index:
                return "fit" if s_.at[x, "status"] == "ok" else "nan_collinear"
            if x in nb.index:
                return "not_fitted_rare" if nb[x] > 0 else "no_binding"
            return "absent"
        c[f"sf_{tag}"] = c["cid"].map(status)
        c[f"sf_{tag}_group"] = c["cid"].map(lambda x: s_.at[x, "group"] if x in s_.index else "")
        c[f"sf_{tag}_group_size"] = c["cid"].map(lambda x: int(s_.at[x, "group_size"]) if x in s_.index else 0)
        c[f"sf_{tag}_vif"] = c["cid"].map(lambda x: round(float(s_.at[x, "vif"]), 2) if x in s_.index else np.nan)
        c[f"sf_{tag}_shrink"] = c["cid"].map(lambda x: round(float(s_.at[x, "shrink"]), 3) if x in s_.index else np.nan)
    if SF_RT_DAM_CORR.exists():
        m = pd.read_parquet(SF_RT_DAM_CORR).set_index("cid")["corr"]
        c["sf_rt_dam_corr"] = c["cid"].map(lambda x: round(float(m[x]), 3) if x in m.index else np.nan)
    c.to_csv(CONSTRAINTS_CSV, index=False)
    write_labels_constraints()


def run_constraints(*args):
    c = build_constraints()
    keep = [x for x in CONSTRAINT_COLUMNS if x in c.columns]
    c[keep].to_csv(CONSTRAINTS_CSV, index=False, float_format="%.6g")
    write_labels_constraints()
    update_constraints_sf_status()      # re-attach regression status columns if a fit exists
    print(f"constraints: {len(c)} rows ({int(c['window_binding'].sum())} bind in the window, {int((~c['window_binding']).sum())} structural-only) -> {CONSTRAINTS_CSV}")


def method_rank_table(cids=None) -> pd.DataFrame:
    """cid x method with a preference rank: rt_reg 0, dam_reg 1, dcpf_proxy 2, a shrunk dam_reg or rt_reg 3 (attenuated magnitude; used only if nothing else exists)."""
    t = pd.read_parquet(SF_SUB, columns=["cid", "method", "quality"])
    if cids is not None:
        t = t[t["cid"].isin(cids)]
    t = t.drop_duplicates(["cid", "method"])
    base = t["method"].map({"rt_reg": 0, "dam_reg": 1, "dcpf_proxy": 2})
    t["rank"] = np.where(t["quality"].astype(str).str.contains("shrunk") & (t["method"] != "dcpf_proxy"), 3, base)
    return t


def _best_method_table(cons: pd.DataFrame) -> pd.Series:
    """cid -> best SF method available in sf_sub (see method_rank_table)."""
    t = method_rank_table()
    return t.sort_values("rank").drop_duplicates("cid").set_index("cid")["method"]


def write_shift_factors_csv():
    """data/shift_factors.csv: substation x top-PRIORITY_TOP_SF constraints (best method per cid), |SF| >= SF_CSV_MIN."""
    cons = pd.read_csv(CONSTRAINTS_CSV, keep_default_na=False)
    sub_tbl = pd.read_parquet(SF_SUB)
    best = _best_method_table(cons)
    cons = cons[cons["cid"].isin(best.index)].sort_values("priority_rank")
    top = cons.head(PRIORITY_TOP_SF)
    bm = best.reindex(top["cid"])
    sel = sub_tbl[sub_tbl["cid"].isin(top["cid"])]
    piv = sel.pivot_table(index=["sub", "cid"], columns="method", values="sf", aggfunc="first").reset_index()
    for m in ("rt_reg", "dam_reg", "dcpf_proxy"):
        if m not in piv:
            piv[m] = np.nan
    piv["method"] = piv["cid"].map(bm)
    piv["sf"] = np.where(piv["method"] == "rt_reg", piv["rt_reg"], np.where(piv["method"] == "dam_reg", piv["dam_reg"], piv["dcpf_proxy"]))
    meta = sel.drop_duplicates(["sub", "cid", "method"]).set_index(["sub", "cid", "method"])[["se", "sf_min", "sf_max", "quality"]]
    idx = pd.MultiIndex.from_frame(piv[["sub", "cid", "method"]])
    m = meta.reindex(idx)
    piv["se"], piv["sf_min"], piv["sf_max"], piv["quality"] = m["se"].to_numpy(), m["sf_min"].to_numpy(), m["sf_max"].to_numpy(), m["quality"].to_numpy()
    piv = piv[np.abs(piv["sf"]) >= SF_CSV_MIN]
    sub_info = N.load_substations()
    piv["kv_max"] = piv["sub"].map(sub_info["kv_max"])
    piv["zone"] = piv["sub"].map(sub_info["zone"])
    piv["match_conf"] = piv["sub"].map(sub_info["match_conf"])
    piv["side"] = [N.side_of(x) for x in piv["sf"]]
    piv["priority_rank"] = piv["cid"].map(top.set_index("cid")["priority_rank"])
    piv = piv.rename(columns={"rt_reg": "sf_rt", "dam_reg": "sf_dam", "dcpf_proxy": "sf_proxy"})
    cols = ["sub", "cid", "priority_rank", "sf", "method", "quality", "side", "se", "sf_min", "sf_max", "sf_rt", "sf_dam", "sf_proxy", "kv_max", "zone", "match_conf"]
    out = piv[cols].sort_values(["priority_rank", "sf"]).reset_index(drop=True)
    for c_ in ("sf", "se", "sf_min", "sf_max", "sf_rt", "sf_dam", "sf_proxy"):
        out[c_] = out[c_].round(4)
    out.to_csv(SHIFT_FACTORS_CSV, index=False)
    write_labels_sf()
    print(f"shift_factors.csv: {len(out):,} rows, {out['cid'].nunique()} constraints, {out['sub'].nunique()} substations, {SHIFT_FACTORS_CSV.stat().st_size / 1e6:.1f} MB")


# ----------------------------------------------------------------------------------------------------------------
# Physics proxy
# ----------------------------------------------------------------------------------------------------------------
PROXY_SCALE = 1.0                 # ASSUMPTION: multiplier applied to proxy PTDFs; left at 1.0 because the regression-on-proxy slope varies more across constraints than its median differs from 1
X_MULT_GRID = (0.5, 1.0, 2.0)     # ASSUMPTION: multipliers tried on the reactance of <=115 kV lines and of >=230 kV lines relative to 138 kV (138 kV = 1)
CAL_MIN_GAIN = 0.03               # ASSUMPTION: keep the default reactances unless a grid point raises the median proxy-vs-regression correlation by more than this
PROXY_LONG_TOP = 200              # ASSUMPTION: proxy rows are added to sf_long (bus level) for this many top-priority constraints that lack a regression SF
PROXY_PEAK_TOP = 10               # ASSUMPTION: "peak match" = the substation with the largest |regression SF| is among the top-N |proxy| substations
PROXY_SIGN_MIN_REG = 0.10         # ASSUMPTION: sign agreement is scored on substations with |regression SF| at or above this
PROXY_SIGN_MIN_PROXY = 0.02       # ASSUMPTION: ... and |proxy| at or above this


def _spearman(a: np.ndarray, b: np.ndarray) -> float:
    ra, rb = pd.Series(a).rank().to_numpy(), pd.Series(b).rank().to_numpy()
    if ra.std() == 0 or rb.std() == 0:
        return np.nan
    return float(np.corrcoef(ra, rb)[0, 1])


def run_physics_proxy(*args):
    """DC power-flow PTDFs for the monitored lines of the constraint table, calibrated against the regression SFs."""
    cons = pd.read_csv(CONSTRAINTS_CSV, keep_default_na=False)
    G = N.build_graph() if "rebuild-graph" in args else N.load_graph()
    subs = N.load_substations()
    sn = N.sub_nodes(G, subs)
    dc0 = N.DCPF(G)
    node_idx = dc0.idx
    sub_list = sn.index[sn["osm_id"] != ""]
    sub_idx = np.array([node_idx[sn.at[s, "osm_id"]] for s in sub_list])
    lines = cons[(cons["element_type"] == "line") & (cons["from_node"].astype(str) != "") & (cons["to_node"].astype(str) != "") & (cons["from_node"] != cons["to_node"])]
    lines = lines[lines["from_node"].isin(G.index) & lines["to_node"].isin(G.index)]
    # ---- calibrate the reactance classes against the unshrunk regression SFs (the only free physical constants of the proxy)
    sub_tbl0 = pd.read_parquet(SF_SUB)
    reg0 = sub_tbl0[sub_tbl0["method"].isin(["rt_reg", "dam_reg"]) & ~sub_tbl0["quality"].astype(str).str.contains("shrunk")]
    reg0 = reg0.assign(r=reg0["method"].map({"rt_reg": 0, "dam_reg": 1})).sort_values("r").drop_duplicates(["sub", "cid"])
    reg0_g = {cid: g.set_index("sub")["sf"] for cid, g in reg0.groupby("cid")}
    ref = lines[(lines["endpoint_conf"].astype(float) >= N.ANCHOR_CONF) & lines["cid"].isin(reg0_g)]

    def median_corr(dcx):
        cache, cs = {}, []
        for r in ref.itertuples():
            key = (r.from_node, r.to_node)
            if key not in cache:
                try:
                    cache[key] = pd.Series(dcx.element_ptdf(*key)[0][sub_idx], index=sub_list)
                except KeyError:
                    cache[key] = None
            v = cache[key]
            if v is None:
                continue
            y = reg0_g[r.cid]
            common = y.index.intersection(v.index)
            if len(common) < PROXY_MIN_PAIRS:
                continue
            xs, ys = v.loc[common].to_numpy(), y.loc[common].to_numpy()
            if xs.std() > 0 and ys.std() > 0:
                cs.append(float(np.corrcoef(xs, ys)[0, 1]))
        return (float(np.median(cs)) if cs else np.nan), len(cs)

    cal = []
    for m69 in X_MULT_GRID:
        for m345 in X_MULT_GRID:
            mc, nn = median_corr(N.DCPF(G.scaled(m69, m345)))
            cal.append(dict(m_le115kv=m69, m_ge230kv=m345, median_corr=mc, n=nn))
    cal = pd.DataFrame(cal)
    base_corr = float(cal[(cal["m_le115kv"] == 1.0) & (cal["m_ge230kv"] == 1.0)]["median_corr"].iloc[0])
    best_row = cal.sort_values("median_corr", ascending=False).iloc[0]
    use = best_row if (best_row["median_corr"] - base_corr) > CAL_MIN_GAIN else cal[(cal["m_le115kv"] == 1.0) & (cal["m_ge230kv"] == 1.0)].iloc[0]
    m69, m345 = float(use["m_le115kv"]), float(use["m_ge230kv"])
    print(f"proxy calibration: median corr {base_corr:.3f} at default reactances; best grid point (<=115 kV x{best_row['m_le115kv']}, >=230 kV x{best_row['m_ge230kv']}) "
          f"{best_row['median_corr']:.3f}; using x{m69} / x{m345}")
    dc = N.DCPF(G.scaled(m69, m345))
    # ---- one PTDF per monitored element (shared by every contingency of that element)
    elems = {}
    notes = {}
    for a, b in sorted(set(zip(lines["from_node"], lines["to_node"]))):
        try:
            p, note = dc.element_ptdf(a, b)
        except KeyError as e:
            notes[(a, b)] = f"no PTDF: {e}"
            continue
        elems[(a, b)] = p * PROXY_SCALE
        notes[(a, b)] = note
    rows = []
    for (a, b), p in elems.items():
        v = p[sub_idx]
        keep = np.abs(v) >= PROXY_SF_MIN
        rows.append(pd.DataFrame({"sub": sub_list[keep], "elem": f"{a}|{b}", "ptdf": v[keep].astype("float32")}))
    pt = pd.concat(rows, ignore_index=True) if rows else pd.DataFrame(columns=["sub", "elem", "ptdf"])
    pt.to_parquet(N.PTDF_PROXY, index=False)
    cons["proxy_elem"] = [f"{a}|{b}" if (a, b) in elems else "" for a, b in zip(cons["from_node"], cons["to_node"])]
    cons["proxy_note"] = [notes.get((a, b), "") if (a, b) in notes else "" for a, b in zip(cons["from_node"], cons["to_node"])]
    # ---- calibration against regression SFs (substation level)
    sub_tbl = pd.read_parquet(SF_SUB)
    reg = sub_tbl[sub_tbl["method"].isin(["rt_reg", "dam_reg"])]
    reg_best = reg.assign(r=reg["method"].map({"rt_reg": 0, "dam_reg": 1}), reliable=~reg["quality"].astype(str).str.contains("shrunk")).sort_values("r").drop_duplicates(["sub", "cid"])
    reg_groups = {cid: g.set_index("sub") for cid, g in reg_best.groupby("cid")}
    # PTDFs with |v| < PROXY_SF_MIN were dropped from pt; restore full vectors for the comparison
    full = {k: v[sub_idx] for k, v in elems.items()}
    out = []
    for r in cons[cons["proxy_elem"] != ""].itertuples():
        if r.cid not in reg_groups:
            continue
        rr = reg_groups[r.cid]
        a, b = r.from_node, r.to_node
        pv = pd.Series(full[(a, b)], index=sub_list)
        common = rr.index.intersection(pv.index)
        if len(common) < PROXY_MIN_PAIRS:
            continue
        x, y = pv.loc[common].to_numpy(), rr.loc[common, "sf"].to_numpy()
        corr = float(np.corrcoef(x, y)[0, 1]) if x.std() > 0 and y.std() > 0 else np.nan
        slope = float(np.cov(x, y)[0, 1] / x.var()) if x.var() > 0 else np.nan
        big = (np.abs(y) >= PROXY_SIGN_MIN_REG) & (np.abs(x) >= PROXY_SIGN_MIN_PROXY)
        topp = set(common[np.argsort(-np.abs(x))[:PROXY_PEAK_TOP]])
        peak_sub = common[int(np.argmax(np.abs(y)))]
        out.append(dict(cid=r.cid, method=rr["method"].iloc[0], element=r.element, n_subs=len(common), corr=corr, spearman=_spearman(x, y), slope_reg_on_proxy=slope,
                        n_sign_scored=int(big.sum()), sign_agree=float((np.sign(x[big]) == np.sign(y[big])).mean()) if big.any() else np.nan,
                        peak_in_proxy_top=bool(peak_sub in topp), max_abs_reg=float(np.abs(y).max()), max_abs_proxy=float(np.abs(x).max()),
                        reliable=bool(rr["reliable"].iloc[0]), endpoint_conf=r.endpoint_conf, proxy_note=r.proxy_note))
    val = pd.DataFrame(out)
    val.round(4).to_csv(SF_PROXY_VALIDATION_CSV, index=False)
    summary = json.loads(SUMMARY_JSON.read_text()) if SUMMARY_JSON.exists() else {}
    if len(val):
        def block(v):
            if not len(v):
                return dict(n=0)
            w = v[v["endpoint_conf"] >= N.ANCHOR_CONF]
            return dict(n=int(len(v)), median_corr=float(v["corr"].median()), median_spearman=float(v["spearman"].median()),
                        share_corr_above_0p5=float((v["corr"] > 0.5).mean()), median_slope=float(v["slope_reg_on_proxy"].median()),
                        median_sign_agree=float(v["sign_agree"].median()), share_peak_in_top=float(v["peak_in_proxy_top"].mean()),
                        median_corr_endpoint_conf_ge_0p5=float(w["corr"].median()) if len(w) else None, n_endpoint_conf_ge_0p5=int(len(w)))
        rel = val[val["reliable"]]
        primary = rel if len(rel) else val
        med = float(primary["corr"].median())
        poor = med < PROXY_POOR_CORR
        summary["proxy"] = dict(n_elements=len(elems), n_cids_with_proxy=int((cons["proxy_elem"] != "").sum()), poor=bool(poor),
                                n_graph_nodes=len(G.nodes), n_graph_edges=len(G.edges), n_load_nodes=dc.n_load_nodes, load_ref=N.LOAD_REF_RULE,
                                reliable_regressions=block(rel), rt_reg=block(val[val["method"] == "rt_reg"]), dam_reg_reliable=block(rel[rel["method"] == "dam_reg"]),
                                all_regressions_incl_shrunk=block(val), median_corr=med, n_compared=int(len(primary)),
                                x_mult=dict(le115kv=m69, ge230kv=m345), calibration=cal.round(4).to_dict("records"), median_corr_default_reactances=base_corr)
    else:
        poor = True
        summary["proxy"] = dict(n_elements=len(elems), n_compared=0, poor=True)
    # ---- proxy rows into sf_sub and sf_long
    cons_idx = cons.set_index("cid")
    sub_rows = []
    for cid, r in cons_idx[cons_idx["proxy_elem"] != ""].iterrows():
        key = (r["from_node"], r["to_node"])
        v = elems[key][sub_idx]
        keep = np.abs(v) >= PROXY_SF_MIN
        sub_rows.append(pd.DataFrame({"sub": sub_list[keep], "cid": cid, "method": "dcpf_proxy", "sf": v[keep], "se": np.nan, "sf_min": np.nan, "sf_max": np.nan,
                                      "quality": PROXY_LOW_CONF if (poor or float(r["endpoint_conf"]) < N.ANCHOR_CONF) else "proxy"}))
    keep_tbl = sub_tbl[sub_tbl["method"] != "dcpf_proxy"]
    pd.concat([keep_tbl] + sub_rows, ignore_index=True).to_parquet(SF_SUB, index=False)
    # bus-level proxy rows for top constraints with no regression SF
    sf_long = pd.read_parquet(SF_LONG)
    sf_long = sf_long[sf_long["method"] != "dcpf_proxy"]
    have_reg = set(reg["cid"])
    cand = cons[(cons["proxy_elem"] != "") & (~cons["cid"].isin(have_reg))].sort_values("priority_rank").head(PROXY_LONG_TOP)
    sp = N.load_settlement_points().drop_duplicates("ELECTRICAL_BUS")
    bus_sub = sp.set_index("ELECTRICAL_BUS")["SUBSTATION"]
    node_of_sub = {s: node_idx[sn.at[s, "osm_id"]] for s in sub_list}
    bus_arr = bus_sub[bus_sub.isin(node_of_sub)]
    bus_node = np.array([node_of_sub[s] for s in bus_arr.to_numpy()])
    prow = []
    for r in cand.itertuples():
        v = elems[(r.from_node, r.to_node)][bus_node]
        k = np.abs(v) >= PROXY_SF_MIN
        prow.append(pd.DataFrame({"bus": bus_arr.index.to_numpy()[k], "cid": r.cid, "sf": v[k].astype("float32"), "method": "dcpf_proxy", "se": np.float32(np.nan),
                                  "quality": PROXY_LOW_CONF if (poor or float(r.endpoint_conf) < N.ANCHOR_CONF) else "proxy"}))
    if prow:
        pr = pd.concat(prow, ignore_index=True)
        for c_ in ("bus", "cid", "method", "quality"):
            pr[c_] = pr[c_].astype("category")
        sf_long = pd.concat([sf_long.astype({c_: "object" for c_ in ("bus", "cid", "method", "quality")}), pr.astype({c_: "object" for c_ in ("bus", "cid", "method", "quality")})], ignore_index=True)
        for c_ in ("bus", "cid", "method", "quality"):
            sf_long[c_] = sf_long[c_].astype("category")
    sf_long.to_parquet(SF_LONG, index=False)
    # ---- constraint table: proxy and best-method columns
    sub_tbl2 = pd.read_parquet(SF_SUB, columns=["cid", "method"]).drop_duplicates()
    methods = sub_tbl2.groupby("cid")["method"].agg(lambda x: sorted(set(x)))
    cons["sf_proxy"] = ["proxy" if x else "none" for x in (cons["proxy_elem"] != "")]
    low = poor | (cons["endpoint_conf"].astype(float) < N.ANCHOR_CONF)
    cons["sf_proxy"] = np.where(cons["proxy_elem"] != "", np.where(low, "proxy_low_conf", "proxy"), "none")
    cons["sf_best"] = [("rt_reg" if "rt_reg" in methods.get(c, []) else "dam_reg" if "dam_reg" in methods.get(c, []) else "dcpf_proxy" if "dcpf_proxy" in methods.get(c, []) else "none") for c in cons["cid"]]
    cons.to_csv(CONSTRAINTS_CSV, index=False)
    write_labels_constraints()
    SUMMARY_JSON.write_text(json.dumps(_clean_json(summary), indent=1))
    write_shift_factors_csv()
    print(f"physics-proxy: {len(elems)} monitored elements, {int((cons['proxy_elem'] != '').sum())} constraints with a proxy, "
          f"{len(val)} compared with regression SFs ({int(val['reliable'].sum()) if len(val) else 0} unshrunk); median corr vs unshrunk regressions {summary['proxy'].get('median_corr', float('nan')):.3f}; poor={poor}")
    write_labels_proxy()


# ----------------------------------------------------------------------------------------------------------------
# Hops and electrical distance
# ----------------------------------------------------------------------------------------------------------------
HOPS_CSV = paths.REPO_DATA / "hops.csv"
HOPS_SUMMARY_CSV = paths.REPO_DATA / "hops_summary.csv"


def best_sf_table(cids: list[str]) -> pd.DataFrame:
    """sub x cid table of the best available SF: columns sub, cid, sf, method, quality, se."""
    t = pd.read_parquet(SF_SUB)
    t = t[t["cid"].isin(cids)]
    t = t.drop(columns=["rank"], errors="ignore")
    mr = method_rank_table(cids)
    # per cid choose the best method that exists, then keep all subs of that method
    best = mr.sort_values("rank").drop_duplicates("cid").set_index("cid")["method"]
    t = t[t["method"] == t["cid"].map(best)]
    return t[["sub", "cid", "sf", "method", "quality", "se"]]


def run_hops(*args):
    cons = pd.read_csv(CONSTRAINTS_CSV, keep_default_na=False)
    G = N.load_graph()
    subs = N.load_substations()
    sn = N.sub_nodes(G, subs)
    cons = cons[cons["sf_best"] != "none"].sort_values("priority_rank").head(PRIORITY_TOP_HOPS) if "sf_best" in cons else cons.head(PRIORITY_TOP_HOPS)
    top = cons.reset_index(drop=True)
    sft = best_sf_table(list(top["cid"]))
    sf_by = {cid: g.set_index("sub") for cid, g in sft.groupby("cid")}
    placed = sn.copy()
    rows = []
    for r in top.itertuples():
        sources = [x for x in (r.from_node, r.to_node) if isinstance(x, str) and x and x in G.index]
        hops, xd = N.hop_and_x_distance(G, sources) if sources else ({}, {})
        sfd = sf_by.get(r.cid)
        method = sfd["method"].iloc[0] if sfd is not None and len(sfd) else ""
        for sub, nrow in placed.iterrows():
            node = nrow["osm_id"]
            if sources and node:
                h = hops.get(node, N.HOPS_UNREACHED)
                x = xd.get(node, np.nan)
            else:
                h, x = (N.HOPS_UNREACHED if sources else np.nan), np.nan
            sf, q = np.nan, ""
            if sfd is not None:
                if sub in sfd.index:
                    sf, q = float(sfd.at[sub, "sf"]), sfd.at[sub, "quality"]
                elif method == "dcpf_proxy" and node:
                    sf, q = 0.0, "proxy_below_min"
            side = N.side_of(sf)
            rows.append((sub, r.cid, int(r.priority_rank), h, x, sf, method, q, side, bool(side == "export" and sf >= N.SF_CURTAIL_MIN)))
    hp = pd.DataFrame(rows, columns=["sub", "cid", "priority_rank", "hops", "x_dist_pu", "sf", "sf_method", "sf_quality", "side", "curtail_flag"])
    hp["kv_max"] = hp["sub"].map(subs["kv_max"])
    hp["zone"] = hp["sub"].map(subs["zone"])
    hp["match_conf"] = hp["sub"].map(subs["match_conf"])
    hp["element"] = hp["cid"].map(top.set_index("cid")["element"])
    uc = pd.to_numeric(top.set_index("cid")["upgrade_match_conf"], errors="coerce")
    hp["has_upgrade"] = hp["cid"].map((top.set_index("cid")["upgrade_project_id"].astype(str) != "") & (uc >= UPGRADE_CONF_MIN))
    hp["upgrade_match_conf"] = hp["cid"].map(uc)
    hp["upgrade_project_id"] = hp["cid"].map(top.set_index("cid")["upgrade_project_id"])
    hp["upgrade_in_service_date"] = hp["cid"].map(top.set_index("cid")["upgrade_in_service_date"])
    hp["upgrade_status"] = hp["cid"].map(top.set_index("cid")["upgrade_status"])
    hp["x_dist_pu"] = hp["x_dist_pu"].round(5)
    hp["sf"] = hp["sf"].round(4)
    # element, upgrade and substation attributes repeat on every row; they live in constraints.csv / substations.csv (join on cid / sub) to keep this file small
    cols = ["sub", "cid", "priority_rank", "hops", "x_dist_pu", "sf", "sf_method", "sf_quality", "side", "curtail_flag", "has_upgrade"]
    hp["hops"] = hp["hops"].astype("Int64")
    hp[cols].to_csv(HOPS_CSV, index=False)
    # ---- per-substation summary
    out = []
    for sub, g in hp.groupby("sub", sort=False):
        gh = g[g["hops"].notna() & (g["hops"] < N.HOPS_UNREACHED)]
        near = None
        if len(gh):
            near = gh.assign(a=gh["sf"].abs().fillna(0)).sort_values(["hops", "a", "priority_rank"], ascending=[True, False, True]).iloc[0]
        gs = g[g["sf"].notna()]
        best = gs.assign(a=gs["sf"].abs()).sort_values(["a", "priority_rank"], ascending=[False, True]).iloc[0] if len(gs) else None
        srow = subs.loc[sub]
        rec = dict(sub=sub, kv_max=srow["kv_max"], zone=srow["zone"], county=srow["county"], lat=srow["lat"], lon=srow["lon"], match_conf=srow["match_conf"],
                   graph_node=bool(sn.at[sub, "osm_id"] != ""))
        if near is not None:
            rec.update(nearest_cid=near["cid"], nearest_element=near["element"], nearest_hops=int(near["hops"]), nearest_x_dist_pu=near["x_dist_pu"], nearest_sf=near["sf"],
                       nearest_sf_method=near["sf_method"], nearest_side=near["side"], nearest_has_upgrade=bool(near["has_upgrade"]),
                       nearest_upgrade_project_id=near["upgrade_project_id"], nearest_upgrade_in_service_date=near["upgrade_in_service_date"])
        if best is not None and abs(best["sf"]) >= 1e-9:
            rec.update(best_cid=best["cid"], best_element=best["element"], best_abs_sf=round(abs(best["sf"]), 4), best_sf=best["sf"], best_sf_method=best["sf_method"],
                       best_side=best["side"], best_hops=(int(best["hops"]) if pd.notna(best["hops"]) else np.nan), best_curtail_flag=bool(best["curtail_flag"]),
                       best_has_upgrade=bool(best["has_upgrade"]), best_upgrade_project_id=best["upgrade_project_id"],
                       best_upgrade_in_service_date=best["upgrade_in_service_date"])
        big = gs[gs["sf"].abs() >= N.SF_SIDE_MIN]
        rec.update(n_constraints_scored=int(len(gs)), n_import_side=int((big["sf"] < 0).sum()), n_export_side=int((big["sf"] > 0).sum()),
                   n_curtail_flags=int(g["curtail_flag"].sum()))
        out.append(rec)
    hs = pd.DataFrame(out)
    for c_ in ("nearest_hops", "best_hops"):
        if c_ in hs:
            hs[c_] = hs[c_].astype("Int64")
    hs.to_csv(HOPS_SUMMARY_CSV, index=False)
    write_labels_hops()
    print(f"hops.csv: {len(hp):,} rows ({hp['sub'].nunique()} substations x {hp['cid'].nunique()} constraints); hops_summary.csv: {len(hs)} rows")


# ----------------------------------------------------------------------------------------------------------------
# Label sidecars (column,label,source,note)
# ----------------------------------------------------------------------------------------------------------------
_R, _D, _A, _S = "REAL", "DERIVED", "ASSUMPTION", "SIM"
_RT = "ERCOT NP6-86-CD via derived/shadow_sced.parquet"
_DAM = "ERCOT NP4-191-CD via derived/shadow_dam.parquet"


def _write_labels(csv_path, spec: dict, prefix_spec: dict | None = None):
    cols = list(pd.read_csv(csv_path, nrows=0).columns)
    rows = []
    for c in cols:
        if c in spec:
            lab, src, note = spec[c]
        else:
            hit = [k for k in (prefix_spec or {}) if c.startswith(k)]
            if not hit:
                raise KeyError(f"no label for {csv_path.name}:{c}")
            lab, src, note = prefix_spec[hit[0]]
        rows.append((c, lab, src, note))
    pd.DataFrame(rows, columns=["column", "label", "source", "note"]).to_csv(csv_path.with_suffix("").as_posix() + ".labels.csv", index=False)


def write_labels_constraints():
    spec = {
        "cid": (_R, "ERCOT NP6-86-CD / NP4-191-CD", "CONSTRAINT_NAME|CONTINGENCY_NAME as published; structural-only rows use <IMM element>|UNSEEN (no contingency is public)"),
        "constraint_name": (_R, "ERCOT NP6-86-CD / NP4-191-CD", "monitored element code; for structural-only rows the IMM code or GTC code"),
        "contingency_name": (_R, "ERCOT NP6-86-CD / NP4-191-CD", "ERCOT contingency code (not decodable from public data); UNSEEN for structural-only rows"),
        "element": (_D, "gridsite.shiftfactors", "from-to station and kV, as ERCOT names them"),
        "element_type": (_D, "gridsite.shiftfactors._element_type", "gtc = no stations; xfmr = same station or different end kV; line = two stations at one kV; other = kV unknown"),
        "from_station": (_R, "ERCOT NP6-86-CD / NP4-191-CD; IMM tables for structural rows", "station name as published (may be a tap or switch that is not an NP4-160 code)"),
        "to_station": (_R, "ERCOT NP6-86-CD / NP4-191-CD; IMM tables for structural rows", ""),
        "kv": (_R, "ERCOT NP6-86-CD / NP4-191-CD", "larger end voltage; inferred from the end substations' common kV where the IMM table is blank (DERIVED)"),
        "from_sub": (_D, "data/station_alias.csv + data/substations.csv", "NP4-160 SUBSTATION used downstream after the alias step; blank if none"),
        "to_sub": (_D, "data/station_alias.csv + data/substations.csv", ""),
        "endpoint_conf": (_A, "gridsite.network.Resolver", "min over the two ends of the placement confidence (0 if either end has no graph node); not a probability"),
        "endpoint_how": (_D, "gridsite.network.Resolver", "how each end was resolved: np4160 | alias:<method> | own_osm | graph_neighbor | none"),
        "gtc": (_D, "gridsite.shiftfactors", "True for interface constraints (no stations) or codes listed in data/gtc.csv"),
        "gtc_name": (_R, "data/gtc.csv (ERCOT/IMM names)", "blank for interfaces not in gtc.csv"),
        "source": (_D, "gridsite.shiftfactors", "window = seen in NP6-86/NP4-191; imm_structural = IMM top-10 element that never appeared in the window"),
        "window_binding": (_D, _RT, "True if the cid had shadow price > 0 in any SCED run or DAM hour of the window"),
        "rt_binding": (_D, _RT, ""),
        "dam_binding": (_D, _DAM, ""),
        "rt_n_runs_binding": (_R, _RT, "SCED runs with shadow price > 0, 25 Sep archive day and 3-10 Oct"),
        "rt_hours_binding": (_A, _RT, "runs x SCED_DT_H (5 minutes per run)"),
        "rt_mean_shadow": (_R, _RT, "$/MWh, over binding runs"),
        "rt_p95_shadow": (_R, _RT, "$/MWh, over binding runs"),
        "rt_max_shadow": (_R, _RT, "$/MWh"),
        "rt_rent_proxy": (_A, _RT, "sum of shadow price x SCED_DT_H, $/MW of relief over the window (5 minutes per run)"),
        "rt_rent_est_musd": (_A, _RT, "sum of shadow price x |flow MW| x SCED_DT_H / 1e6; a congestion-rent estimate for the window, not a settlement number"),
        "rt_pct_at_cap": (_D, _RT, "share of binding runs where the shadow price equals max_shadow_price (SCED could not relieve the element)"),
        "dam_n_hours": (_R, _DAM, "DAM hours with shadow price > 0 (10 Sep - 10 Oct)"),
        "dam_hours_binding": (_R, _DAM, ""),
        "dam_mean_shadow": (_R, _DAM, "$/MWh, over binding hours"),
        "dam_p95_shadow": (_R, _DAM, ""),
        "dam_max_shadow": (_R, _DAM, ""),
        "dam_rent_proxy": (_D, _DAM, "sum of shadow price x 1 h, $/MW over the 31 days"),
        "dam_rent_est_musd": (_D, _DAM, "sum of shadow price x |flow MW| x 1 h / 1e6"),
        "median_limit_mw": (_R, _RT + " / " + _DAM, "median over binding rows (RT first, else DAM)"),
        "median_flow_mw": (_R, _RT + " / " + _DAM, ""),
        "annual_element": (_D, "data/constraints_annual.csv", "IMM element code this cid belongs to; blank if no IMM match"),
        "annual_join": (_D, "gridsite.shiftfactors.build_constraints", "code = IMM code equals CONSTRAINT_NAME | gtc_code = IMM parenthetical equals CONSTRAINT_NAME | station_pair_kv | structural_only"),
        "annual_rent_2025_musd": (_R, "Potomac Economics IMM monthly report, Dec 2025 (data/constraints_annual.csv)", "element level: the same value repeats on every cid of the element; do not sum. RT market where listed, else DA (annual_rent_market)"),
        "annual_rent_market": (_R, "data/constraints_annual.csv", "RT or DA; the two markets describe the same elements and are never added"),
        "annual_rent_2024_musd": (_R, "Potomac Economics IMM monthly report, Dec 2024", "element level, RT where listed else DA"),
        "annual_binding_hours": (_R, "data/constraints_annual.csv", "IMM 2025 binding hours (SCED intervals / 12) where the element is in the IMM frequency top 10"),
        "annual_share": (_A, "gridsite.shiftfactors", "this cid's share of the element's window rent (RT, else DAM, else equal split) used to allocate the annual rent"),
        "annual_rent_alloc_musd": (_A, "gridsite.shiftfactors", "annual_rent_2025_musd x annual_share; used in the priority score so an element is not counted once per contingency"),
        "upgrade_project_id": (_D, "data/planned_projects.csv", "TPIT/RTP/RPG project touching the element; ';' list for GTC documents"),
        "upgrade_project_name": (_R, "data/planned_projects.csv", ""),
        "upgrade_in_service_date": (_R, "data/planned_projects.csv", ""),
        "upgrade_status": (_R, "data/planned_projects.csv", ""),
        "upgrade_match_conf": (_A, "gridsite.shiftfactors.MATCH_CONF", "pair_name 0.9 > pair_title 0.8 > pair_bus 0.7 > xfmr_sub 0.6 > pair_bus_many 0.45 > one_end 0.3; a match is a name/bus overlap, not proof of relief"),
        "upgrade_match_method": (_D, "gridsite.shiftfactors.ProjectIndex.match", ""),
        "upgrade_n_matches": (_D, "gridsite.shiftfactors.ProjectIndex.match", "number of projects that matched; the best is reported"),
        "priority_struct": (_A, "gridsite.shiftfactors", "annual_rent_alloc_musd / max over the table, 0-1"),
        "priority_current": (_A, "gridsite.shiftfactors", "W_CURRENT_RT x rt_rent_est/max + W_CURRENT_DAM x dam_rent_est/max, 0-1"),
        "priority_score": (_A, "gridsite.shiftfactors", "100 x (W_STRUCT x priority_struct + W_CURRENT x priority_current)"),
        "priority_rank": (_D, "gridsite.shiftfactors", "1 = highest priority_score"),
        "from_node": (_D, "gridsite.network.Resolver", "OSM substation id of the end in the line graph; blank if not placed"),
        "to_node": (_D, "gridsite.network.Resolver", ""),
        "proxy_elem": (_D, "gridsite.shiftfactors.run_physics_proxy", "key of the monitored element for the DC power-flow proxy (blank if none)"),
        "proxy_note": (_D, "gridsite.network.DCPF.element_ptdf", ""),
        "sf_best": (_D, "gridsite.shiftfactors", "best SF method available: rt_reg > dam_reg > dcpf_proxy > none"),
        "sf_proxy": (_D, "gridsite.shiftfactors", "proxy | proxy_low_conf (an end was placed only as a graph neighbour, or the proxy failed the PROXY_POOR_CORR check) | none"),
        "sf_rt_dam_corr": (_D, "data/sf_rt_vs_dam.csv", "correlation across buses of the RT and DAM SF of this cid"),
    }
    pre = {
        "sf_rt": (_D, "gridsite.shiftfactors", "RT regression status: fit | nan_collinear | not_fitted_rare | no_binding | absent"),
        "sf_dam": (_D, "gridsite.shiftfactors", "DAM regression status; sf_<mkt>_shrink = ss/(ss+lambda), the ridge attenuation of the SF magnitude (1 = none; < SHRINK_MIN = tagged +shrunk)"),
    }
    _write_labels(CONSTRAINTS_CSV, spec, pre)


def write_labels_sf():
    spec = {
        "sub": (_R, "ERCOT NP4-160 SUBSTATION", "join key"),
        "cid": (_R, "ERCOT NP6-86-CD / NP4-191-CD", ""),
        "priority_rank": (_D, "data/constraints.csv", ""),
        "sf": (_D, "gridsite.shiftfactors", "best method for the cid; convention cong = LMP - lambda = - sum SF x shadow price; SF < 0 import side, SF > 0 export side. Median over the substation's highest-kV buses"),
        "method": (_D, "gridsite.shiftfactors", "rt_reg (SCED regression) | dam_reg (day-ahead regression) | dcpf_proxy (DC power flow on the OSM/HIFLD graph, ASSUMPTION reactances)"),
        "quality": (_D, "gridsite.shiftfactors", "ok | weak (|SF| < T_MIN x se) | +grouped (merged near-identical cids) | +collinear (VIF > VIF_FLAG) | +shrunk (ridge attenuation < SHRINK_MIN: magnitude understated, sign usually right) | +implausible (|SF| > SF_MAX_ABS) | low (proxy judged poor) | proxy"),
        "side": (_D, "gridsite.network.side_of", "import (SF <= -SF_SIDE_MIN) | export (SF >= SF_SIDE_MIN) | neutral"),
        "se": (_D, "gridsite.shiftfactors", "ridge standard error ignoring autocorrelation, so optimistic; blank for the proxy"),
        "sf_min": (_D, "gridsite.shiftfactors", "minimum over all buses of the substation (differs from sf when transformers separate voltage levels)"),
        "sf_max": (_D, "gridsite.shiftfactors", ""),
        "sf_rt": (_D, "gridsite.shiftfactors", "RT regression value, if fitted"),
        "sf_dam": (_D, "gridsite.shiftfactors", "DAM regression value, if fitted"),
        "sf_proxy": (_A, "gridsite.network.DCPF", "proxy value, if computed; reactances and load reference are ASSUMPTION"),
        "kv_max": (_R, "data/substations.csv", ""),
        "zone": (_R, "data/substations.csv", ""),
        "match_conf": (_A, "data/substations.csv", "agent 1c placement confidence (not a probability)"),
    }
    _write_labels(SHIFT_FACTORS_CSV, spec)


def write_labels_alias():
    spec = {
        "station": (_R, "ERCOT NP6-86-CD / NP4-191-CD station names", "name as published; not an NP4-160 SUBSTATION code unless method = graph_neighbor"),
        "sub": (_D, "gridsite.network.build_station_alias", "NP4-160 SUBSTATION judged to be the same yard; blank = no alias found"),
        "method": (_D, "gridsite.network.build_station_alias", "stem_exact | stem_short | psse_name | osm_shared | prefix | fuzzy (joined with +) | graph_neighbor | none"),
        "conf": (_A, "gridsite.network.ALIAS_W", "noisy-OR of evidence weights x kV and graph checks; not a probability"),
        "osm_id": (_D, "gridsite.network.build_station_alias", "OSM substation id when the station was placed as the only unclaimed graph neighbour of its partner"),
        "lat": (_D, "gridsite.network.build_station_alias", ""),
        "lon": (_D, "gridsite.network.build_station_alias", ""),
        "evidence": (_D, "gridsite.network.build_station_alias", "text trace of the evidence"),
    }
    _write_labels(N.STATION_ALIAS_CSV, spec)


def write_labels_hops():
    spec = {
        "sub": (_R, "ERCOT NP4-160 SUBSTATION", ""),
        "cid": (_R, "ERCOT NP6-86-CD / NP4-191-CD", ""),
        "element": (_D, "data/constraints.csv", ""),
        "priority_rank": (_D, "data/constraints.csv", ""),
        "hops": (_D, "gridsite.network.hop_and_x_distance", "unweighted graph distance to the nearest endpoint of the monitored element; 99 = not connected, blank = element has no endpoints in the graph (GTC)"),
        "x_dist_pu": (_A, "gridsite.network.hop_and_x_distance", "summed branch reactance (pu on 100 MVA) along the minimum-reactance path to the nearest endpoint; reactances are typical per-km values by kV (ASSUMPTION)"),
        "sf": (_D, "data/shift_factors.csv", "best method for the cid; 0.0 where a proxy value is below PROXY_SF_MIN"),
        "sf_method": (_D, "gridsite.shiftfactors", ""),
        "sf_quality": (_D, "gridsite.shiftfactors", ""),
        "side": (_D, "gridsite.network.side_of", "import = SF <= -0.02 (battery discharges into high prices); export = SF >= 0.02 (battery charges at low prices)"),
        "curtail_flag": (_A, "gridsite.network.SF_CURTAIL_MIN", "True if export side and SF >= SF_CURTAIL_MIN: injecting here loads the constraint, so discharge is limited when it binds"),
        "has_upgrade": (_D, "data/constraints.csv", "a TPIT/RTP/RPG project matched the monitored element with upgrade_match_conf >= UPGRADE_CONF_MIN"),
        "upgrade_project_id": (_D, "data/constraints.csv", ""),
        "upgrade_in_service_date": (_R, "data/planned_projects.csv", ""),
        "upgrade_status": (_R, "data/planned_projects.csv", ""),
        "upgrade_match_conf": (_A, "gridsite.shiftfactors.MATCH_CONF", "has_upgrade is True only when this is >= UPGRADE_CONF_MIN (0.5)"),
        "kv_max": (_R, "data/substations.csv", ""),
        "zone": (_R, "data/substations.csv", ""),
        "match_conf": (_A, "data/substations.csv", ""),
    }
    _write_labels(HOPS_CSV, spec)
    spec2 = {
        "sub": (_R, "ERCOT NP4-160 SUBSTATION", ""), "kv_max": (_R, "data/substations.csv", ""), "zone": (_R, "data/substations.csv", ""),
        "county": (_D, "data/substations.csv", ""), "lat": (_D, "data/substations.csv", ""), "lon": (_D, "data/substations.csv", ""),
        "match_conf": (_A, "data/substations.csv", ""), "graph_node": (_D, "gridsite.network.sub_nodes", "False if the substation has no node in the line graph, so hops are undefined"),
        "n_constraints_scored": (_D, "data/hops.csv", "top-priority constraints with an SF at this substation"),
        "n_import_side": (_D, "data/hops.csv", "constraints with SF <= -SF_SIDE_MIN here"),
        "n_export_side": (_D, "data/hops.csv", "constraints with SF >= SF_SIDE_MIN here"),
        "n_curtail_flags": (_A, "data/hops.csv", ""),
    }
    pre2 = {
        "nearest_": (_D, "data/hops.csv", "the priority constraint with the fewest hops to this substation (ties: larger |SF|, then priority)"),
        "best_": (_D, "data/hops.csv", "the priority constraint with the largest |SF| at this substation"),
    }
    _write_labels(HOPS_SUMMARY_CSV, spec2, pre2)


def write_labels_proxy():
    spec = {
        "cid": (_R, "ERCOT NP6-86-CD / NP4-191-CD", ""), "method": (_D, "gridsite.shiftfactors", "regression the proxy is compared with"),
        "element": (_D, "data/constraints.csv", ""), "n_subs": (_D, "gridsite.shiftfactors.run_physics_proxy", "substations with both a regression SF and a graph node"),
        "corr": (_D, "gridsite.shiftfactors.run_physics_proxy", "Pearson correlation across those substations (invariant to the withdrawal reference)"),
        "spearman": (_D, "gridsite.shiftfactors.run_physics_proxy", ""),
        "slope_reg_on_proxy": (_D, "gridsite.shiftfactors.run_physics_proxy", "OLS slope of regression SF on proxy PTDF; 1 = same scale"),
        "n_sign_scored": (_D, "gridsite.shiftfactors.run_physics_proxy", "substations with |regression SF| >= PROXY_SIGN_MIN_REG and |proxy| >= PROXY_SIGN_MIN_PROXY"),
        "sign_agree": (_D, "gridsite.shiftfactors.run_physics_proxy", "share of those with the same sign"),
        "reliable": (_D, "gridsite.shiftfactors.run_physics_proxy", "False when the regression SF is attenuated by ridge (+shrunk): its magnitude is not a fair reference"),
        "peak_in_proxy_top": (_D, "gridsite.shiftfactors.run_physics_proxy", "the substation with the largest |regression SF| is among the PROXY_PEAK_TOP largest |proxy|"),
        "max_abs_reg": (_D, "gridsite.shiftfactors.run_physics_proxy", ""), "max_abs_proxy": (_D, "gridsite.shiftfactors.run_physics_proxy", ""),
        "endpoint_conf": (_A, "data/constraints.csv", ""), "proxy_note": (_D, "gridsite.network.DCPF.element_ptdf", ""),
    }
    _write_labels(SF_PROXY_VALIDATION_CSV, spec)


def write_labels_validation():
    spec = {
        "bus": (_R, "ERCOT NP4-160 ELECTRICAL_BUS", ""), "sub": (_R, "ERCOT NP4-160 SUBSTATION", ""), "kv": (_R, "ERCOT NP4-160", ""),
        "resource_node": (_R, "ERCOT NP4-160", ""),
    }
    pre = {
        "rt_": (_D, "gridsite.shiftfactors.fit_market", "real-time fit: n_test runs, rmse_oos/rmse_null in $/MWh (null = predict lambda, i.e. zero congestion), r2_oos vs the holdout mean (blank if holdout std < R2_MIN_STD), *_in = training rows"),
        "dam_": (_D, "gridsite.shiftfactors.fit_market", "day-ahead fit, last 5 DAM days held out"),
    }
    _write_labels(SF_VALIDATION_CSV, spec, pre)
    spec = {
        "cid": (_R, "ERCOT", ""), "n_runs_rt": (_R, _RT, ""), "n_hours_dam": (_R, _DAM, ""),
        "shrink_rt": (_D, "gridsite.shiftfactors", "ridge attenuation ss/(ss+lambda) of the RT fit (1 = none)"), "shrink_dam": (_D, "gridsite.shiftfactors", "same for the DAM fit"),
        "corr": (_D, "gridsite.shiftfactors.rt_vs_dam", "Pearson correlation across buses present in both fits"),
        "slope_dam_on_rt": (_D, "gridsite.shiftfactors.rt_vs_dam", "least squares through the origin"),
        "n_big": (_D, "gridsite.shiftfactors.rt_vs_dam", "buses with |SF| >= TOP_BIG_SF in either fit"),
        "sign_agree_big": (_D, "gridsite.shiftfactors.rt_vs_dam", ""), "top_bus_overlap": (_D, "gridsite.shiftfactors.rt_vs_dam", "overlap of the TOP_BUS_OVERLAP_N buses with the largest |SF|"),
        "max_abs_rt": (_D, "gridsite.shiftfactors.rt_vs_dam", ""), "max_abs_dam": (_D, "gridsite.shiftfactors.rt_vs_dam", ""),
    }
    _write_labels(SF_RT_VS_DAM_CSV, spec)
    spec = {
        "market": (_D, "gridsite.shiftfactors", "rt | dam"), "group": (_D, "gridsite.shiftfactors", "merged regressor group id, or pair>=0.80 for a reported but unmerged pair"),
        "n_members": (_D, "gridsite.shiftfactors", ""), "cids": (_R, "ERCOT", "';'-separated"), "vif": (_D, "gridsite.shiftfactors", "variance inflation of the group regressor; for pair rows the shadow-price correlation"),
    }
    _write_labels(SF_GROUPS_CSV, spec)


def write_labels_sf_long():
    rows = [
        ("bus", _R, "ERCOT NP4-160 ELECTRICAL_BUS", "bus key; DAM fits also cover buses that are not in the real-time bus list"),
        ("cid", _R, "ERCOT NP6-86-CD / NP4-191-CD", "one row per cid; members of a merged collinear group share the group's SF"),
        ("sf", _D, "gridsite.shiftfactors", "cong = LMP - lambda = - sum SF x shadow price. Rows with |SF| < SF_STORE_MIN are not stored: treat absent as 0"),
        ("method", _D, "gridsite.shiftfactors", "rt_reg | dam_reg | dcpf_proxy (proxy rows only for top-priority constraints without a regression SF)"),
        ("se", _D, "gridsite.shiftfactors", "ridge standard error ignoring autocorrelation (optimistic); NaN for the proxy"),
        ("quality", _D, "gridsite.shiftfactors", "ok | weak | +grouped | +collinear | low | proxy"),
    ]
    pd.DataFrame(rows, columns=["column", "label", "source", "note"]).to_csv(paths.DERIVED / "sf_long.labels.csv", index=False)
