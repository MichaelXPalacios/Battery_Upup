"""Revision 2 (agent R2): what Phases 3b, 4 and 6 see as the congestion structure.

Revision 1 replayed 260 constraints, each with a 2025 target (IMM rent or the persistence-discounted window). Two inputs arrived afterwards:

  1. R1's structural rows (gridsite.coverage): shift factors for WESTEX, NELRIO, E_PASP, the Far West import pattern, the Valley export pattern and the Bell County pocket
     (the Panhandle pattern, grade D, only on request), measured on the 60-day day-ahead disclosure. Installed with `coverage.install_into_nodal_lanes()`.
  2. The IMM's 2026 year-to-date top-10 lists (data/imm_2026_anchors.csv, January-August). Where an element is in them with a congestion rent, the annual shadow-price-hours
     target comes from that rent (the most recent evidence); otherwise it stays on the 2025 IMM rent, and otherwise on the persistence-discounted window.

`install()` is called once, at the bottom of gridsite/nodal_lanes.py, so every command, test and import of Phase 3b/4/6 sees the same structure. Environment switches (read at
import; each changes the structure hash, so every cache key reacts):

  GRIDSITE_REVISION=1     revision-1 behaviour: nothing is installed (2025 targets, 260 constraints, geography as G1 left it)
  GRIDSITE_ANCHORS=2025   the 2025-only sensitivity: no 2026 re-anchoring (R1's rows still go in)
  GRIDSITE_ANCHORS=2026da also anchor on a day-ahead rent where an element has no real-time rent row (SEA_AAT1, MDOPHR99_A) - sensitivity only, see below
  GRIDSITE_PANHANDLE=1    include R1_PANHANDLE_P10 (grade D: the published PNHNDL series is not tracked) - sensitivity only
  GRIDSITE_FAR_WEST=6945  keep 6945__A (re-anchored on its 2026 rent) and leave out R1's I_FW_N pattern, which duplicates it (SF correlation 0.92) - sensitivity only

The annualisation of a 2026 anchor (ASSUMPTION, documented in notes/phase8-revision2.md): year-to-date rent x 12 / 8 (January-August are in the IMM report), allocated to the
element's replayed contingencies in proportion to their current rent (sph x limit). Hours: the same market's 2026 binding count annualised in the same way (RT 5-minute
intervals x 5/60, DA hours), else the 2025 IMM hours of the element if it has them, else rent x the median hours per $M of the 2025 RT rents (the Phase 3b rule), and an element
that is in the rent list but not in the frequency list binds less than the 10th-ranked element of that list. Market: the lanes are real-time prices, so the primary run anchors on the RT rent rows only. The two replayed elements that have only a day-ahead rent row (SEA_AAT1 $33.8M,
MDOPHR99_A $31.9M) are NOT re-anchored in the primary: a day-ahead rent is not a real-time shadow price, the direction of the flow is not in a rent, and the measured summer day-ahead prices
near SEA_AAT1 (regional_basis.csv) show no separation of the size its rent would need (notes/phase8-revision2.md); `GRIDSITE_ANCHORS=2026da` applies them (sensitivity S2).

Unverified constants are single named values at the top, each with a label (REAL / DERIVED / SIM / ASSUMPTION).
"""
from __future__ import annotations

import os

import numpy as np
import pandas as pd

from . import paths

REPO_DATA = paths.REPO_DATA

# =================================================================================================================
# Constants
# =================================================================================================================
YTD_MONTHS = 8                      # REAL: the IMM's August 2026 report holds January-August (coverage.IMM_YTD_MONTHS)
YEAR_MONTHS = 12                    # REAL
SCED_MINUTES = 5.0                  # REAL: one RT binding count is one 5-minute SCED interval
ANCHORS_CSV = "imm_2026_anchors.csv"
ANCHOR_BASIS = {"RT": "window_imm2026_rt_rent", "DA": "window_imm2026_da_rent"}   # DERIVED: sph_basis of a re-anchored cid. "window_" so that saturation and the upgrade clock treat
                                    #   it like a window target: storage and projects in service before the data end are already in the rent
MEAN_SHADOW_CAP_FRAC = 0.5          # ASSUMPTION: same rule as nodal_lanes: the mean shadow price while binding is at most this share of the cap, else the hours are raised
ONE_MONTH_DOMINATES = 0.5           # ASSUMPTION: an anchor is flagged if one month holds more than this share of the year-to-date rent (annualising a single event overstates the year)

REVISION = os.environ.get("GRIDSITE_REVISION", "2")
ANCHORS = os.environ.get("GRIDSITE_ANCHORS", "2026")           # "2026" (RT rent rows), "2026da" (RT rows and DA-only rows) or "2025" (none)
ANCHORS_ON = ANCHORS in ("2026", "2026da")
PANHANDLE = os.environ.get("GRIDSITE_PANHANDLE", "0") == "1"
FAR_WEST = os.environ.get("GRIDSITE_FAR_WEST", "I_FW_N")
VALEXP_CID = "VALEXP|BASE CASE"         # DERIVED: R1's Valley export pattern
VALEXP_PROJECT = "CN2025-04 (Lower Rio Grande Valley project, as NELRIO)"   # ASSUMPTION: R1's instruction (notes/phase7-coverage.md section 6 step 4 iv): the Valley GTCs are retired in planning before summer 2027
VALEXP_ISD = "2027-06-30"               # ASSUMPTION: the same date the schedule gives NELRIO (a year-only 2027 is read as 30 June)
VALEXP_CONF = 0.7                        # ASSUMPTION: NELRIO's match confidence
PANHANDLE_CID = "R1_PANHANDLE_P10|BASE CASE"   # DERIVED: R1's grade-D pattern (coverage.TARGETS)
FAR_WEST_CID = "I_FW_N|BASE CASE"              # DERIVED: R1's Far West import pattern, which duplicates 6945__A|SRGRMGS5
FAR_WEST_ALT = "6945__A|SRGRMGS5"


def config() -> dict:
    """The switches in force (written into notes and the effective-structure table)."""
    return dict(revision=REVISION, anchors=ANCHORS, panhandle=PANHANDLE, far_west=FAR_WEST)


def tag() -> str:
    """Short name of the configuration: 'primary', or what differs."""
    if REVISION == "1":
        return "rev1"
    parts = []
    if ANCHORS != "2026":
        parts.append(f"anchors{ANCHORS}")
    if PANHANDLE:
        parts.append("panhandle")
    if FAR_WEST != "I_FW_N":
        parts.append("fw6945")
    return "+".join(parts) if parts else "primary"


# =================================================================================================================
# The 2026 anchors
# =================================================================================================================
def _read_anchors() -> pd.DataFrame:
    return pd.read_csv(REPO_DATA / ANCHORS_CSV, keep_default_na=False, na_values=[""], low_memory=False)


def anchor_elements() -> pd.DataFrame:
    """One row per constraint_name with a 2026 anchor: market, year-to-date rent, annualised rent and hours, the month shares.

    RT rent is preferred; an element with only a DA rent row takes that. Elements whose IMM code never appeared in the Sep-Oct windows (constraint_name blank) are skipped:
    they have no shift factors, so nothing could be replayed for them."""
    allrows = _read_anchors()
    a = allrows[allrows["constraint_name"].notna()].copy()
    months = ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug"]
    k_ann = YEAR_MONTHS / YTD_MONTHS
    rent = a[a["metric"] == "rent_musd"]
    cnt = a[a["metric"] == "count"]
    # the 10th-ranked element of each frequency list (all ten, also those with no shift factors) bounds an element that is in the rent list but not in the frequency list
    cap = {}
    for m in ("RT", "DA"):
        c = allrows[(allrows["metric"] == "count") & (allrows["market"] == m)]
        if len(c):
            conv = SCED_MINUTES / 60.0 if m == "RT" else 1.0
            cap[m] = float(c["ytd"].min()) * conv * k_ann
    rows = []
    for el in sorted(rent["constraint_name"].unique()):
        r_el = rent[rent["constraint_name"] == el]
        mk = "RT" if (r_el["market"] == "RT").any() else "DA"
        if mk == "DA" and ANCHORS != "2026da":
            continue                                        # a day-ahead rent alone does not anchor a real-time lane in the primary run
        r = r_el[r_el["market"] == mk].iloc[0]
        mvals = [float(r[m]) for m in months]
        ytd = float(r["ytd"])
        c_el = cnt[(cnt["constraint_name"] == el) & (cnt["market"] == mk)]
        if len(c_el):
            conv = SCED_MINUTES / 60.0 if mk == "RT" else 1.0
            hours, hb = float(c_el["ytd"].iloc[0]) * conv * k_ann, f"imm2026_{mk.lower()}_count"
        else:
            hours, hb = np.nan, ""
        rows.append(dict(constraint_name=el, code=r["code"], market=mk, ytd_musd=ytd, annualised_musd=ytd * k_ann, months=YTD_MONTHS, limit_mw=float(r["limit_mw"]),
                         largest_month=months[int(np.argmax(mvals))], largest_month_share=(max(mvals) / ytd if ytd > 0 else np.nan),
                         last3_share=(sum(mvals[-3:]) / ytd if ytd > 0 else np.nan), hours_annualised=hours, hours_basis=hb,
                         hours_bound=cap.get(mk, np.nan), n_rows_other_market=int((r_el["market"] != mk).sum())))
    return pd.DataFrame(rows)


def apply_anchors(struct: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Re-anchor the elements of `struct` that have a 2026 anchor. Returns (new structure, log with one row per re-anchored cid).

    Columns changed: sph_usd_mw_yr, sph_basis, H_hours, H_basis, mean_shadow_usd_mwh. Nothing else (drivers, caps, shift-factor methods and upgrade matches stay)."""
    from . import nodal_lanes as L

    an = anchor_elements().set_index("constraint_name")
    s = struct.copy()
    log = []
    for el, a in an.iterrows():
        ix = s.index[s["constraint_name"] == el]
        if len(ix) == 0:
            continue
        g = s.loc[ix]
        lim = g["median_limit_mw"].astype(float)
        rent_now = (g["sph_usd_mw_yr"] * lim).astype(float)
        share = rent_now / rent_now.sum() if rent_now.sum() > 0 else pd.Series(1.0 / len(g), index=g.index)
        sph_new = a["annualised_musd"] * 1e6 * share / lim
        k_h = float(g["k_hours_per_musd"].iloc[0]) if "k_hours_per_musd" in g and pd.notna(g["k_hours_per_musd"].iloc[0]) else L.K_HOURS_PER_MUSD_FALLBACK
        imm_h = g.loc[g["H_basis"] == "imm_hours", "H_hours"]
        if pd.notna(a["hours_annualised"]):
            h_el, hb = float(a["hours_annualised"]), a["hours_basis"]
        elif len(imm_h):
            h_el, hb = float(imm_h.max()), "imm_hours"
        else:
            h_el, hb = a["annualised_musd"] * k_h, "imm2026_rent_x_hours_per_musd"
            if pd.notna(a["hours_bound"]):
                h_el = min(h_el, float(a["hours_bound"]))
                hb += "_bounded_by_10th_ranked"
        h_el = float(np.clip(h_el, L.H_MIN, L.H_MAX))
        cap = g["cap_usd_mwh"].astype(float)
        H = pd.Series(h_el, index=g.index)
        need = sph_new / (MEAN_SHADOW_CAP_FRAC * cap)
        raised = (sph_new / H) > MEAN_SHADOW_CAP_FRAC * cap
        H = pd.Series(np.where(raised, np.minimum(need, L.H_MAX), H), index=g.index)
        for i in ix:
            log.append(dict(cid=s.at[i, "cid"], constraint_name=el, market=a["market"], ytd_musd=a["ytd_musd"], months=a["months"], annualised_musd=a["annualised_musd"],
                            limit_mw=float(s.at[i, "median_limit_mw"]), sph_before=float(s.at[i, "sph_usd_mw_yr"]), sph_after=float(sph_new[i]), basis_before=s.at[i, "sph_basis"],
                            H_before=float(s.at[i, "H_hours"]), H_after=float(H[i]), H_basis_before=s.at[i, "H_basis"], H_basis_after=("imm_hours_capped_up" if raised[i] and hb == "imm_hours" else hb),
                            mean_shadow_after=float(sph_new[i] / H[i]), cap_usd_mwh=float(cap[i]), raised_by_cap_rule=bool(raised[i]),
                            largest_month=a["largest_month"], largest_month_share=a["largest_month_share"], one_month_dominates=bool(a["largest_month_share"] > ONE_MONTH_DOMINATES),
                            last3_share=a["last3_share"]))
        s.loc[ix, "sph_usd_mw_yr"] = sph_new
        s.loc[ix, "sph_basis"] = ANCHOR_BASIS[a["market"]]
        s.loc[ix, "H_hours"] = H
        s.loc[ix, "H_basis"] = hb
        s.loc[ix, "mean_shadow_usd_mwh"] = sph_new / H
    return s, pd.DataFrame(log)


def anchor_sph_table(df: pd.DataFrame) -> pd.DataFrame:
    """The same re-anchoring for Phase 4's `sph_table()` (columns cid, sph_usd_mw_yr, sph_basis, limit_mw), so the first-order rerank and the lanes share their targets."""
    if REVISION == "1" or not ANCHORS_ON:
        return df
    an = anchor_elements().set_index("constraint_name")
    out = df.copy()
    el = out["cid"].astype(str).str.split("|").str[0]
    for name, a in an.iterrows():
        ix = out.index[(el == name) & out["sph_usd_mw_yr"].notna() & (out["limit_mw"] > 0)]
        if len(ix) == 0:
            continue
        rent_now = out.loc[ix, "sph_usd_mw_yr"] * out.loc[ix, "limit_mw"]
        share = rent_now / rent_now.sum() if rent_now.sum() > 0 else pd.Series(1.0 / len(ix), index=ix)
        out.loc[ix, "sph_usd_mw_yr"] = a["annualised_musd"] * 1e6 * share / out.loc[ix, "limit_mw"]
        out.loc[ix, "sph_basis"] = ANCHOR_BASIS[a["market"]]
    return out


# =================================================================================================================
# The install
# =================================================================================================================
def install() -> dict:
    """Patch gridsite.nodal_lanes in place (once per process). Called at the bottom of nodal_lanes.py."""
    from . import nodal_lanes as L

    if REVISION == "1":
        return dict(installed=False, reason="GRIDSITE_REVISION=1")
    if getattr(L, "_r2_installed", False):
        return dict(already=True)
    import sys

    cov = sys.modules.get("gridsite.coverage")
    if cov is not None and not hasattr(cov, "install_into_nodal_lanes"):
        L._r2_pending = True                                  # coverage.py is the module being imported (it imports nodal_lanes at its top): its last statement calls install() again
        return dict(pending=True)
    from . import coverage

    L._r2_installed = True
    orig_struct = L.constraint_structure
    L._r2_base_structure = orig_struct                    # the Phase 3b structure before the 2026 anchors and the structural rows (for the effective-structure table)
    if ANCHORS_ON:
        def anchored_structure(*a, **k):
            return apply_anchors(orig_struct(*a, **k))[0]

        L.constraint_structure = anchored_structure
    exclude, keep = [], []
    if not PANHANDLE:
        exclude.append(PANHANDLE_CID)
    if FAR_WEST == "6945":
        exclude.append(FAR_WEST_CID)
        keep.append(FAR_WEST_ALT)
    info = coverage.install_into_nodal_lanes(exclude_extra=tuple(exclude), keep_replaced=tuple(keep))
    # bus-level shift factors of the structural rows (the validation against resource nodes reads buses): the substation's value
    orig_bus = L.bus_sf_matrix

    def bus_sf_matrix(buses, struct):
        out = orig_bus(buses, struct)
        meth = np.asarray(struct["sf_method"])
        cols = np.where(meth == "dam60_factor")[0]
        if len(cols):
            import pyarrow.parquet as pq

            sp = L._read_csv("settlement_points.csv").drop_duplicates("ELECTRICAL_BUS").set_index("ELECTRICAL_BUS")["SUBSTATION"]
            subs = [sp.get(b) for b in buses]
            cids = list(struct["cid"].iloc[cols])
            t = pq.read_table(L.SF_SUB, columns=["sub", "cid", "method", "sf"], filters=[("cid", "in", cids), ("method", "=", "dam60_factor")]).to_pandas()
            piv = t.pivot_table(index="sub", columns="cid", values="sf", aggfunc="first").reindex(index=subs, columns=cids).fillna(0.0)
            out = out.copy()
            out[:, cols] = np.clip(piv.to_numpy(float), -L.SF_ABS_CAP, L.SF_ABS_CAP)
        return out

    L.bus_sf_matrix = bus_sf_matrix
    # the Valley export interface (R1's VALEXP) is retired in planning with the rest of the Lower Rio Grande Valley GTCs: give it the dated residual scale NELRIO already has
    from . import saturation as SA

    orig_planned = SA.planned_upgrades

    def planned_upgrades(struct):
        sched, src = orig_planned(struct)
        if VALEXP_CID in set(struct["cid"]) and not (sched["cid"] == VALEXP_CID).any():
            row = pd.DataFrame({"cid": [VALEXP_CID], "isd": [pd.Timestamp(VALEXP_ISD)], "project_id": [VALEXP_PROJECT], "conf": [VALEXP_CONF], "element_type": ["gtc"],
                                "residual": [SA.UPGRADE_RESIDUAL_GTC]})
            sched = pd.concat([sched, row[sched.columns]], ignore_index=True)
        return sched, src

    SA.planned_upgrades = planned_upgrades
    SA.clear_cache()
    L.mu_tilde.cache_clear()
    info.update(installed=True, config=config())
    return info


# =================================================================================================================
# Outputs
# =================================================================================================================
EFFECTIVE_CSV = "constraint_structure_rev2.csv"
ANCHOR_LOG_CSV = "anchor_2026_applied.csv"

EFFECTIVE_LABELS_EXTRA = [
    ("rev2_source", "DERIVED", "gridsite.rev2", "phase3b_base (unchanged), phase3b_anchored_2026 (target re-anchored on the IMM 2026 year-to-date rent) or r1_structural (a row of data/sf_structural_constraints.csv)"),
    ("sph_before_anchor_usd_mw_yr", "DERIVED", "data/constraint_synth.csv", "the Phase 3b target ($/MW-yr) before the 2026 re-anchoring; equal to sph_usd_mw_yr for rows that were not re-anchored"),
    ("H_before_anchor", "DERIVED", "data/constraint_synth.csv", "the binding hours before the 2026 re-anchoring"),
]

ANCHOR_LOG_LABELS = [
    ("cid", "REAL", "NP6-86-CD / NP4-191-CD", "CONSTRAINT_NAME|CONTINGENCY_NAME of a replayed contingency of the element"),
    ("constraint_name", "REAL", "NP6-86-CD / NP4-191-CD", "the monitored element's code"),
    ("market", "REAL", "IMM monthly report, August 2026", "RT where the element has a real-time rent row in the 2026 top-10 list, else DA"),
    ("ytd_musd", "REAL", "data/imm_2026_anchors.csv (IMM monthly report, August 2026)", "January-August 2026 congestion rent of the element, $M"),
    ("months", "REAL", "IMM monthly report", "months in the year-to-date figure"),
    ("annualised_musd", "DERIVED", "ytd_musd x 12 / months", "the annual rent the lanes are anchored on, $M (ASSUMPTION: the other months resemble the average of these)"),
    ("limit_mw", "REAL", "NP6-86-CD / NP4-191-CD", "median limit of the cid"),
    ("sph_before", "DERIVED", "data/constraint_synth.csv", "annual shadow-price-hours target before ($/MW-yr; 2025 IMM rent or persistence-discounted window)"),
    ("sph_after", "DERIVED", "annualised_musd x share of the element's rent / limit", "target after the 2026 re-anchoring ($/MW-yr)"),
    ("basis_before", "DERIVED", "data/constraint_synth.csv", ""),
    ("H_before", "DERIVED", "data/constraint_synth.csv", "annual binding hours before"),
    ("H_after", "DERIVED", "gridsite.rev2", "annual binding hours after (2026 count annualised, 2025 IMM hours, or rent x hours per $M)"),
    ("H_basis_before", "DERIVED", "data/constraint_synth.csv", ""),
    ("H_basis_after", "DERIVED", "gridsite.rev2", ""),
    ("mean_shadow_after", "DERIVED", "sph_after / H_after", "mean synthetic shadow price while binding, $/MWh"),
    ("cap_usd_mwh", "REAL", "SCED max shadow price per cid, else IMM caps by kV class", ""),
    ("raised_by_cap_rule", "DERIVED", "gridsite.rev2", "True if the hours were raised because the mean shadow price would exceed half the cap"),
    ("largest_month", "REAL", "IMM monthly report", "month with the largest rent"),
    ("largest_month_share", "DERIVED", "largest month / ytd", "share of the year-to-date rent earned in that month"),
    ("one_month_dominates", "DERIVED", "gridsite.rev2", "True if one month holds more than half of the year-to-date rent: annualising it overstates the year"),
    ("last3_share", "DERIVED", "June-August / ytd", "share of the year-to-date rent earned in June-August"),
    ("used_in_primary", "DERIVED", "gridsite.rev2", "False if R1's structural rows replace this cid (so its re-anchoring never reaches a lane)"),
]


def effective_structure_table(struct: pd.DataFrame, base: pd.DataFrame, log: pd.DataFrame) -> pd.DataFrame:
    """The structure the lanes use, with where each row came from."""
    out = struct.copy()
    b = base.set_index("cid")
    anchored = set(log["cid"]) if len(log) else set()
    out["rev2_source"] = ["r1_structural" if m == "dam60_factor" else ("phase3b_anchored_2026" if c in anchored else "phase3b_base") for m, c in zip(out["sf_method"], out["cid"])]
    out["sph_before_anchor_usd_mw_yr"] = [float(b.at[c, "sph_usd_mw_yr"]) if c in b.index else float(s) for c, s in zip(out["cid"], out["sph_usd_mw_yr"])]
    out["H_before_anchor"] = [float(b.at[c, "H_hours"]) if c in b.index else float(h) for c, h in zip(out["cid"], out["H_hours"])]
    return out


def write_tables() -> dict:
    """data/constraint_structure_rev2.csv and data/anchor_2026_applied.csv (+ labels) for the configuration in force. Called by `lanes`."""
    from . import nodal_lanes as L

    if REVISION == "1":
        return {}
    base = L._r2_base_structure()
    eff, _, _ = L.mu_tilde()
    if "R1_PANHANDLE_P10|BASE CASE" in set(eff["cid"]) and not PANHANDLE:
        raise RuntimeError("the grade-D Panhandle row is in the primary structure")
    anchored, log = apply_anchors(base) if ANCHORS_ON else (base, pd.DataFrame(columns=[c[0] for c in ANCHOR_LOG_LABELS]))
    eff_t = effective_structure_table(eff, base, log)
    eff_t.to_csv(REPO_DATA / EFFECTIVE_CSV, index=False)
    spec = [x for x in L.STRUCT_LABELS] + EFFECTIVE_LABELS_EXTRA
    L.write_labels(REPO_DATA / EFFECTIVE_CSV, spec, eff_t)
    if len(log):
        log = log.copy()
        log["used_in_primary"] = log["cid"].isin(set(eff["cid"]))
        num = log.select_dtypes("number").columns
        log[num] = log[num].round(4)
    log.to_csv(REPO_DATA / ANCHOR_LOG_CSV, index=False)
    L.write_labels(REPO_DATA / ANCHOR_LOG_CSV, ANCHOR_LOG_LABELS, log)
    return dict(n_structure=len(eff_t), n_anchored=int(log["cid"].nunique()) if len(log) else 0)
