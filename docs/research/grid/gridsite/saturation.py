"""Phase 3b: how the replayed congestion structure changes through 2031 (SIM / ASSUMPTION).

Two things shrink a constraint's shadow prices after 2025, and both are expressed as a multiplier `scale_k(year)` on the synthetic shadow prices of
constraint k (1 = the 2025 structure, 0 = no congestion left):

  planned upgrades   Each matched planned project (data/constraints.csv upgrade_* columns, match confidence >= UPGRADE_CONF_MIN; gridsite.upgrades.planned_schedule
                     when agent 4's module is available, which adds its reviewed schedule) relieves its element from its in-service date: the scale falls to
                     UPGRADE_RESIDUAL (a line or transformer re-binds a little at its higher limit) or UPGRADE_RESIDUAL_GTC (an interface keeps half its congestion).
                     In the year of the in-service date the relief counts for the fraction of the year after it.
  storage saturation Storage added after 2025 behind a constraint competes for the same congestion. Each project (queue_sub_map.csv: IA signed, or firm EIA-860M
                     status, or already operating with a 2026 date; planned EIA rows that duplicate a queue project are dropped) sitting on the import side of constraint k
                     (SF <= -SAT_MIN_SF) offsets  MW x SF  of the flow. With relief_k = sum of those offsets (MW, weighted by the share of the year the project is
                     online and by SAT_REALISATION for projects not yet built):
                         scale_k = max(SAT_FLOOR, 1 - SAT_SLOPE * relief_k / limit_k).

None of the saturation constants is measured; they are named ASSUMPTIONs with sensitivities in notes/phase3.md. The upgrade residuals equal agent 4's
(gridsite.upgrades.RESIDUAL_MU_SCALE_WIRES / _GTC) so that Phase 6 sees one set of numbers.
"""
from __future__ import annotations

import warnings

import numpy as np
import pandas as pd

from . import nodal_lanes as L
from . import paths

REPO_DATA = paths.REPO_DATA

# =================================================================================================================
# Constants
# =================================================================================================================
AS_OF = pd.Timestamp("2026-10-10")        # REAL: date of this build; a project dated earlier but not operating is treated as arriving now
UPGRADE_RESIDUAL = 0.10                   # ASSUMPTION: share of a line / transformer constraint's shadow prices left after its planned upgrade (= gridsite.upgrades.RESIDUAL_MU_SCALE_WIRES)
UPGRADE_RESIDUAL_GTC = 0.50               # ASSUMPTION: an interface keeps half of its congestion after its planned relief (= gridsite.upgrades.RESIDUAL_MU_SCALE_GTC; the arena halves the Permian premium after the 765 kV lines)
UPGRADE_CONF_MIN = 0.5                    # ASSUMPTION: a planned-upgrade match at or above this confidence counts (phase 2 hops.csv and phase 4)
YEAR_ONLY_ISD_MONTH_DAY = "06-30"         # ASSUMPTION: an in-service date given only as a year is read as 30 June of that year
SAT_MIN_SF = 0.05                         # ASSUMPTION: a project relieves a constraint only if |SF| >= this on the import side (below it the relief is noise)
SAT_SLOPE = 1.0                           # ASSUMPTION: shadow prices fall one-for-one with the share of the limit that storage offsets (relief = limit -> floor)
SAT_FLOOR = 0.10                          # ASSUMPTION: shadow prices never fall below this share of the 2025 structure (the same floor as the upgrade residual)
SAT_REALISATION = 0.6                     # ASSUMPTION: share of not-yet-built queue / planned MW that is actually built (no public attrition history in this repo)
SAT_SIDES = ("import",)                   # ASSUMPTION: brief: only import-side storage counts (discharging into the pocket); export-side charging would add relief
SAT_MIN_CONF = 0.5                        # REAL: queue_sub_map.csv, "use conf >= 0.5"
SAT_SPLIT_TAP_EQUALLY = True              # ASSUMPTION: a tapped line's MW is split equally between the stations listed in queue_sub_map.csv sub / sub_alt (those that are NP4-160 substations)
DAYS_PER_YEAR = 365.0                     # REAL: day count of the in-year ramp of a project or an upgrade (same as gridsite.upgrades)
FIRM_EIA_STATUS = ("(T)", "(U)", "(V)", "(TS)")   # ASSUMPTION: EIA-860M statuses that are firm: approvals received, under construction, built but not in service
FORWARD_YEARS = (2026, 2027, 2028, 2029, 2030, 2031)   # DERIVED: years with a scale vector (2026 is the current year; the forward table reports 2027-2031)
BASE_YEAR = 2025                          # REAL: the replay year (the structure is the 2025 one; scale 1)


# =================================================================================================================
# Planned upgrades
# =================================================================================================================
def _parse_isd(x):
    if x is None or (isinstance(x, float) and np.isnan(x)) or str(x).strip() in ("", "nan", "NaT", "None"):
        return pd.NaT
    s = str(x).strip()
    if len(s) == 4 and s.isdigit():
        return pd.Timestamp(f"{s}-{YEAR_ONLY_ISD_MONTH_DAY}")
    try:
        return pd.Timestamp(s)
    except Exception:
        return pd.NaT


def planned_upgrades(struct: pd.DataFrame) -> tuple[pd.DataFrame, str]:
    """Planned-upgrade clock for the replayed constraints: cid, isd, project_id, conf, residual, source.

    Uses gridsite.upgrades.planned_schedule() (phase 4, with its reviewed schedule) when it imports, else the upgrade_* columns of constraints.csv.
    The element's upgrade applies to every contingency cid of the element (a rebuild relieves them all)."""
    try:
        from . import upgrades as U

        s = U.planned_schedule(UPGRADE_CONF_MIN)
        s = s[s["isd"].notna()]
        out = pd.DataFrame({"cid": s["cid"], "isd": pd.to_datetime(s["isd"]), "project_id": s["project_id"].astype(str), "conf": s["conf"].astype(float),
                            "element_type": s["element_type"]})
        src = "gridsite.upgrades.planned_schedule"
    except Exception as ex:                                    # agent 4's module is another agent's work in progress: say so, then use the table it was built from
        warnings.warn(f"gridsite.upgrades.planned_schedule unavailable ({type(ex).__name__}: {ex}); using the upgrade_* columns of constraints.csv", stacklevel=2)
        c = struct[struct["upgrade_in_service_date"].notna() & (struct["upgrade_match_conf"].astype(float) >= UPGRADE_CONF_MIN)]
        out = pd.DataFrame({"cid": c["cid"], "isd": c["upgrade_in_service_date"].map(_parse_isd), "project_id": c["upgrade_project_id"].astype(str),
                            "conf": c["upgrade_match_conf"].astype(float), "element_type": c["element_type"]})
        out = out[out["isd"].notna()]
        src = "data/constraints.csv upgrade_* columns"
    out = out[out["cid"].isin(set(struct["cid"]))].copy()
    out["residual"] = np.where(out["element_type"] == "gtc", UPGRADE_RESIDUAL_GTC, UPGRADE_RESIDUAL)
    return out.reset_index(drop=True), src


def upgrade_scale(year: int, struct: pd.DataFrame, sched: pd.DataFrame | None = None) -> pd.Series:
    """Per-cid multiplier from planned upgrades: 1 - f (1 - residual), f = share of `year` after the in-service date (a product over a cid's projects).

    A project dated in or before BASE_YEAR is already in the 2025 market data and is skipped. For a constraint whose target comes from the Oct 2026 window
    (sph_basis window_*), a project in service by AS_OF is already in that window and is skipped too (the same rule as for storage already built)."""
    sched = planned_upgrades(struct)[0] if sched is None else sched
    ye = pd.Timestamp(f"{year}-12-31")
    base_end = pd.Timestamp(f"{BASE_YEAR}-12-31")
    s = pd.Series(1.0, index=struct["cid"].to_numpy())
    windowed = set(struct.loc[struct["sph_basis"].astype(str).str.startswith("window"), "cid"])
    for r in sched.itertuples():
        if r.isd <= base_end:
            continue
        if r.cid in windowed and r.isd <= AS_OF:
            continue
        f = float(np.clip((ye - r.isd).days / DAYS_PER_YEAR, 0.0, 1.0))
        s.loc[r.cid] *= 1.0 - f * (1.0 - r.residual)
    return s


# =================================================================================================================
# Storage saturation
# =================================================================================================================
def _np4160_subs() -> set:
    t = pd.read_csv(REPO_DATA / "substations.csv", keep_default_na=False, na_values=[""], low_memory=False, usecols=["sub", "in_np4160"])
    return set(t.loc[t["in_np4160"] == True, "sub"])      # noqa: E712


def storage_projects() -> pd.DataFrame:
    """Storage that arrives after 2025, one row per (project, substation): columns sub, mw, cod, eff_cod, built, source, stage, realisation.

    From data/queue_sub_map.csv at confidence >= SAT_MIN_CONF; planned EIA rows that duplicate a queue project are dropped (overlap_conf > 0). Kept: queue projects with
    an interconnection agreement signed, EIA planned units at a firm status, and EIA operating units dated after BASE_YEAR (`built`). A tapped line's MW is split equally
    between the stations listed in sub / sub_alt that are NP4-160 substations. A project dated before AS_OF that is not operating yet arrives at AS_OF.
    The cod column mixes 'YYYY-MM-DD' (queue) and 'YYYY-MM' (EIA); it is parsed with format="mixed", and a missing date on a kept EIA row is an error."""
    q = pd.read_csv(REPO_DATA / "queue_sub_map.csv", keep_default_na=False, na_values=[""], low_memory=False)
    q["cod"] = pd.to_datetime(q["cod"], errors="coerce", format="mixed")
    q = q[(q["conf"] >= SAT_MIN_CONF) & q["sub"].notna() & (q["mw"] > 0)]
    q = q[~((q["source"] == "eia") & (q["stage"] == "planned") & (q["overlap_conf"].fillna(0) > 0))]
    q_ia = (q["source"] == "queue") & (q["stage"] == "IA_signed")
    q_eia_firm = (q["source"] == "eia") & (q["stage"] == "planned") & q["status"].astype(str).str.startswith(FIRM_EIA_STATUS)
    q_eia_op = (q["source"] == "eia") & (q["stage"] == "operating") & (q["cod"] >= pd.Timestamp(f"{BASE_YEAR + 1}-01-01"))
    q = q[q_ia | q_eia_firm | q_eia_op].copy()
    if q.loc[q["source"] == "eia", "cod"].isna().any():
        raise ValueError("EIA storage rows without a parsable COD")
    q["built"] = q_eia_op[q.index]
    q["cod"] = q["cod"].fillna(AS_OF + pd.Timedelta(days=DAYS_PER_YEAR))      # a queue row with no date arrives a year from now
    q["eff_cod"] = pd.to_datetime(np.where(q["built"], q["cod"], np.maximum(q["cod"], AS_OF)))
    q["realisation"] = np.where(q["built"], 1.0, SAT_REALISATION)
    known = _np4160_subs()
    rows = []
    for r in q.itertuples():
        ends = [r.sub]
        if SAT_SPLIT_TAP_EQUALLY and isinstance(r.sub_alt, str) and r.sub_alt:
            ends += [x.strip() for x in r.sub_alt.split(";") if x.strip() in known and x.strip() != r.sub]
        for sub in ends:
            rows.append({"sub": sub, "mw": r.mw / len(ends), "cod": r.cod, "eff_cod": r.eff_cod, "built": bool(r.built), "source": r.source, "stage": r.stage,
                         "realisation": r.realisation, "project": r.inr_or_plant})
    return pd.DataFrame(rows)


def storage_online(year: int, proj: pd.DataFrame) -> pd.Series:
    """Effective MW online in `year` per project row: mw x realisation x share of the year after the (effective) COD."""
    ye = pd.Timestamp(f"{year}-12-31")
    f = ((ye - proj["eff_cod"]).dt.days / DAYS_PER_YEAR).clip(0.0, 1.0)
    return proj["mw"] * proj["realisation"] * f


def saturation_scale(year: int, struct: pd.DataFrame, proj: pd.DataFrame | None = None, sf: dict | None = None, slope: float = SAT_SLOPE,
                     floor: float = SAT_FLOOR, sides=SAT_SIDES, return_relief: bool = False):
    """Per-cid multiplier from storage saturation: max(floor, 1 - slope x relief / limit) with relief = sum MW_online x |SF| over projects on the chosen side(s)."""
    proj = storage_projects() if proj is None else proj
    subs = sorted(set(proj["sub"]))
    sf = sf or L.sub_sf_matrix(subs, struct, zones={s: "LZ_NORTH" for s in subs})      # the zone is irrelevant for the raw SF
    SF = sf["SF"]
    row = {s: i for i, s in enumerate(sf["subs"])}
    mw = storage_online(year, proj).to_numpy()
    idx = np.array([row[s] for s in proj["sub"]], dtype=int)
    S = SF[idx]                                                              # (P, K)
    side = np.zeros_like(S, bool)
    if "import" in sides:
        side |= S <= -SAT_MIN_SF
    if "export" in sides:
        side |= S >= SAT_MIN_SF
    w_all = mw[:, None] * np.abs(S) * side
    built = proj["built"].to_numpy(bool)
    # constraints anchored on the 2025 IMM rent count every project after 2025; constraints whose target is the Oct 2026 window already contain the storage operating by then
    imm = (struct["sph_basis"] == "imm_rent_alloc").to_numpy()
    relief = np.where(imm, w_all.sum(0), w_all[~built].sum(0))
    lim = struct["median_limit_mw"].astype(float).to_numpy()
    scale = np.maximum(floor, 1.0 - slope * relief / np.maximum(lim, 1.0))
    out = pd.Series(scale, index=struct["cid"].to_numpy())
    if return_relief:
        return out, pd.Series(relief, index=struct["cid"].to_numpy())
    return out


# =================================================================================================================
# Scale vectors
# =================================================================================================================
_SCALE_CACHE: dict = {}


def clear_cache() -> None:
    _SCALE_CACHE.clear()


def _constants_key() -> tuple:
    return (UPGRADE_RESIDUAL, UPGRADE_RESIDUAL_GTC, UPGRADE_CONF_MIN, SAT_MIN_SF, SAT_SLOPE, SAT_FLOOR, SAT_REALISATION, SAT_SIDES, SAT_MIN_CONF, SAT_SPLIT_TAP_EQUALLY,
            FIRM_EIA_STATUS, AS_OF.isoformat(), BASE_YEAR)


def scale_vector(year: int, struct: pd.DataFrame, upgrades: bool = True, saturation: bool = True) -> np.ndarray:
    """(K,) multipliers on the synthetic shadow prices for `year`, aligned to struct['cid']. BASE_YEAR and earlier return ones."""
    if year <= BASE_YEAR:
        return np.ones(len(struct))
    key = (year, upgrades, saturation, L._struct_hash(struct), _constants_key())
    if key in _SCALE_CACHE:
        return _SCALE_CACHE[key].copy()
    s = pd.Series(1.0, index=struct["cid"].to_numpy())
    if upgrades:
        s = s * upgrade_scale(year, struct)
    if saturation:
        s = s * saturation_scale(year, struct)
    v = s.to_numpy(float)
    _SCALE_CACHE[key] = v
    return v.copy()


def scale_table(struct: pd.DataFrame, years=FORWARD_YEARS) -> pd.DataFrame:
    """cid x year table of the combined scale, and the upgrade-only / saturation-only parts for the last year (informational)."""
    cols = {}
    for y in years:
        cols[f"scale_{y}"] = scale_vector(y, struct)
    ylast = max(years)
    cols[f"upgrade_only_{ylast}"] = scale_vector(ylast, struct, True, False)
    cols[f"saturation_only_{ylast}"] = scale_vector(ylast, struct, False, True)
    t = pd.DataFrame(cols, index=struct["cid"].to_numpy())
    t.insert(0, "constraint_name", struct["constraint_name"].to_numpy())
    return t
