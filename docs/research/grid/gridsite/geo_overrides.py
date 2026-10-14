"""G1 geography overrides: audited corrections to the substation placement (data/geo_overrides.csv).

Why this exists. Phase 1c places every NP4-160 substation by name, EIA plant and constraint-neighbour evidence. Phase 2 then found 20 binding-constraint rows
whose two ends sat more than 80 km apart (one end must be wrong), and the Phase 6 note found COLLEGE in Brazos County while its partners are in Dallas County.
Agent G1 audited those rows and every other placed station that has a constraint partner (notes/phase7-geo.md). The result is one hand-checked table of corrections,
applied inside `geomatch.match_geo`, so a re-run of `run_grid.py match-geo` reproduces them instead of undoing them.

Actions (column `action`):
  place    pin the substation at (lat, lon) [and OSM substation `osm_id`] with confidence `conf`; `match_method` becomes "override".
  unplace  the matcher's site is contradicted by independent evidence and no better site was found: the substation has no coordinates (the Phase 1c neighbour
           fill-in may still place it from its constraint partners, with its own method and confidence).
  demote   keep the matcher's site but cap its confidence at `conf` (the evidence is suspicious but not conclusive).

The override confidence is final: the neighbour stage never changes it (the module is applied before the stage, so the stage sees the corrected positions,
and again after it, which restores the pinned confidence). Applying the table twice gives the same result (idempotent), and it never touches a substation that
is not listed. Every constant below is a single named value with a label (REAL / DERIVED / ASSUMPTION).
"""
from __future__ import annotations

from collections import Counter

import numpy as np
import pandas as pd

from .paths import REPO_DATA

OVERRIDES_CSV = REPO_DATA / "geo_overrides.csv"
ACTIONS = ("place", "unplace", "demote")                # REAL: the three things an override can do
COLUMNS = ["sub", "lat", "lon", "county", "method", "evidence", "conf", "replaces_method",      # the eight columns of the brief ...
           "action", "osm_id", "matched_name", "old_county", "old_conf"]                         # ... plus the audit trail
TX_BBOX = (25.5, 36.7, -106.8, -93.4)                   # REAL: south, north, west, east edge of Texas (a typo guard for hand-entered coordinates)
OVERRIDE_METHOD = "override"                            # REAL: value written to substations.csv match_method for a placed override
METHODS = ("alt_site", "name_partner", "family_block", "graph_adjacent", "tpit_name",                                    # ASSUMPTION: how a place row was decided
           "unplace_partner", "unplace_county", "demote_partner", "demote_county", "demote_block")                      # ... and why an unplace / demote row was written


class OverrideError(ValueError):
    """The override table is malformed (the message names the row)."""


def load_overrides(path=None) -> pd.DataFrame:
    """Read and validate data/geo_overrides.csv. `NA` is a real substation code, so nothing is parsed as missing except an empty cell."""
    path = OVERRIDES_CSV if path is None else path
    df = pd.read_csv(path, keep_default_na=False, na_values=[""], dtype={"sub": str, "osm_id": str, "matched_name": str})
    missing = [c for c in COLUMNS if c not in df.columns]
    if missing:
        raise OverrideError(f"{path}: missing columns {missing}")
    if df["sub"].isna().any() or df["sub"].duplicated().any():
        raise OverrideError(f"{path}: `sub` must be unique and non-empty (duplicates: {sorted(df.loc[df['sub'].duplicated(), 'sub'].astype(str))})")
    for r in df.itertuples():
        if r.action not in ACTIONS:
            raise OverrideError(f"{r.sub}: action {r.action!r} is not one of {ACTIONS}")
        if not isinstance(r.evidence, str) or not r.evidence.strip() or not isinstance(r.method, str) or not r.method.strip():
            raise OverrideError(f"{r.sub}: method and evidence are required (an override without a reason is not allowed)")
        if r.method not in METHODS:
            raise OverrideError(f"{r.sub}: method {r.method!r} is not one of {METHODS}")
        if r.action in ("place", "demote") and not (0.0 < float(r.conf) <= 1.0):
            raise OverrideError(f"{r.sub}: conf {r.conf} must be in (0, 1]")
        if r.action == "place":
            if not (np.isfinite(r.lat) and np.isfinite(r.lon)):
                raise OverrideError(f"{r.sub}: a place row needs lat and lon")
            s, n, w, e = TX_BBOX
            if not (s <= r.lat <= n and w <= r.lon <= e):
                raise OverrideError(f"{r.sub}: ({r.lat}, {r.lon}) is outside Texas")
    return df


def _site_of(res, sub):
    r = res[sub]
    return r["sites"][r["chosen"]]


def apply_overrides(res, subs, link=None, post=False, path=None, overrides=None):
    """Apply the override table to the matcher's results `res` ({sub: dict(sites=[...], chosen=i)}); returns (res, Counter of what was done).

    subs     the ERCOT substation table (index = sub, column kv_set), used to know which rows exist and, when `link` is given, the kV of a placed sub.
    link     optional callable (lat, lon, kv_set) -> OSM substation id or '', used for a `place` row that has no osm_id of its own.
    post     True on the second call (after the neighbour stage): `unplace` is skipped, so a fill-in the stage legitimately made for an unplaced station survives;
             `place` and `demote` are applied again because the stage changes confidences.
    A row for a substation that is not in `subs` is counted under "missing" and ignored (a refreshed NP4-160 file may drop a code).
    """
    ov = load_overrides(path) if overrides is None else overrides
    stats = Counter()
    for r in ov.itertuples():
        if r.sub not in subs.index:
            stats["missing"] += 1
            continue
        if r.action == "unplace":
            if not post and res.pop(r.sub, None) is not None:
                stats["unplace"] += 1
            continue
        if r.action == "demote":
            if r.sub in res:
                site = _site_of(res, r.sub)
                site["conf"] = min(float(site["conf"]), float(r.conf))
                kept = site["pick"].get("g1_matcher_evidence", site["pick"].get("evidence", ""))      # the matcher's own text, kept once so a second pass is a no-op
                site["pick"] = dict(site["pick"], g1_matcher_evidence=kept, evidence=f"G1 demoted ({r.method}): {r.evidence} | matcher: {kept}")
                site["demoted"] = True
                stats["demote"] += 1
            continue
        osm_id = r.osm_id if isinstance(r.osm_id, str) else ""
        if not osm_id and link is not None:
            osm_id = link(float(r.lat), float(r.lon), subs.at[r.sub, "kv_set"]) or ""
        pick = dict(method=OVERRIDE_METHOD, osm_id=osm_id, hifld_id="", ref_name=(r.matched_name if isinstance(r.matched_name, str) else ""),
                    lat=float(r.lat), lon=float(r.lon), osm_link="override" if osm_id else "", is_plant=False,
                    evidence=f"G1 override ({r.method}): {r.evidence}")
        site = dict(lat=float(r.lat), lon=float(r.lon), conf=float(r.conf), methods=[f"override_{r.method}"], neighbor_ok="", members=[], pick=pick)
        res[r.sub] = dict(sites=[site], chosen=0)
        stats["place"] += 1
    return res, stats


def pinned_subs(path=None, overrides=None) -> dict:
    """{action: sorted list of subs} of the table, for tests and notes."""
    ov = load_overrides(path) if overrides is None else overrides
    return {a: sorted(ov.loc[ov["action"] == a, "sub"]) for a in ACTIONS}
