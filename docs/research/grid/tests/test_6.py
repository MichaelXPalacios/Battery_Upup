"""Phase 6 (full upgrade rerank, Tier 2 valuation, map, report checks): invariants on the real tables and a few hand-built cases.

Run (from docs/research/grid):  python tests/test_6.py [name-substring ...]
The research venv has no pytest; each test is a plain function and the module ends with the harness runner of test_3a.
Takes about 30 seconds: the dispatch results are in the value cache, so nothing is recomputed.
"""
import json
import shutil
import subprocess
import sys
from pathlib import Path

GRID_DIR = Path(__file__).resolve().parents[1]
if str(GRID_DIR) not in sys.path:
    sys.path.insert(0, str(GRID_DIR))
if str(GRID_DIR / "tests") not in sys.path:
    sys.path.insert(0, str(GRID_DIR / "tests"))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from _harness_3a import run_module  # noqa: E402
from gridsite import check_labels, paths, report_map  # noqa: E402
from gridsite import sites as S  # noqa: E402
from gridsite import upgrade_rerank as R  # noqa: E402
from gridsite import upgrades as U  # noqa: E402

DATA = paths.REPO_DATA


def _read(name):
    return pd.read_csv(DATA / name, keep_default_na=False, na_values=[""], low_memory=False)


# ---------------------------------------------------------------------------------------------------- scenarios
def test_scenario_list_has_the_twelve_scenarios():
    sc = R.scenario_list()
    assert [s["scenario"] for s in sc] == list(R.UPGRADE_IDS) + [R.BUNDLE_BY_2031, R.BUNDLE_ALL]
    assert all(set(s["mu_scale"].values()) <= {U.RESIDUAL_MU_SCALE_WIRES, U.RESIDUAL_MU_SCALE_GTC} for s in sc), "residuals are the Phase 4 constants"
    from gridsite import nodal_lanes as L

    struct, _, _ = L.mu_tilde()
    cids = set(struct["cid"])
    replayed = {s["scenario"]: len(set(s["mu_scale"]) & cids) for s in sc}
    for sid in ("U02", "U07"):                                                  # R2: R1 measured no pocket for Vealmoor-Koch Tap and Longshore-Consavvy in summer 2026: still no shift factor
        assert replayed[sid] == 0, f"{sid} is not in the replayed structure (no shift factor)"
    for sid in ("U01", "U03", "U04", "U05", "U06", "U08", "U09", "U10"):      # R2: WESTEX (U04) and Bell County-Salado (U08) are in through R1's structural rows
        assert replayed[sid] > 0, sid


def test_planned_bundle_rules():
    from gridsite import nodal_lanes as L
    from gridsite import saturation as SA

    ms31, rows31 = R.planned_bundle(2031)
    msall, rowsall = R.planned_bundle(None)
    assert set(ms31) <= set(msall) and len(msall) > len(ms31)
    assert (rows31["isd"] > pd.Timestamp(f"{SA.BASE_YEAR}-12-31")).all(), "a project in service by 2025 is already in the replay year"
    assert (rows31["isd"] <= pd.Timestamp("2031-12-31")).all()
    struct, _, _ = L.mu_tilde()
    windowed = set(struct.loc[struct["sph_basis"].astype(str).str.startswith("window"), "cid"])
    assert not ((rows31["cid"].isin(windowed)) & (rows31["isd"] <= SA.AS_OF)).any(), "a window-anchored constraint already contains a project in service by October 2026"
    extra = set(msall) - set(ms31)
    assert extra and all(c.split("|")[0] in ("587__A", "D5_V4TP_1") for c in extra), "the 2032+ projects are Argyle-Lewisville and one more"
    assert any(c.startswith("587__A|") for c in extra) and not any(c.startswith("587__A|") for c in ms31)


# ---------------------------------------------------------------------------------------------------- the rerank table
def test_value_sites_base_equals_sites_csv():
    cand = S.candidate_subs()
    vs = S.value_sites(cand, years=(R.YEAR,), baseline="none", workers=1).set_index("lane")
    s = _read("sites.csv").set_index("sub")
    assert np.abs(vs.loc[s.index, "learner_usd_kw_yr"] - s["fwd_learner_2025_replay"]).max() < 1e-3
    assert np.abs(vs.loc[s.index, "oracle_usd_kw_yr"] - s["fwd_oracle_2025_replay"]).max() < 1e-3


def test_rerank_csv_invariants():
    rr = _read("upgrade_rerank.csv")
    assert len(rr) == 12 * 40 and rr["scenario"].nunique() == 12
    assert rr.groupby("scenario")["sub"].nunique().eq(40).all()
    assert (rr["value_after_usd_kw_yr"] - rr["value_before_usd_kw_yr"] - rr["delta_usd_kw_yr"]).abs().max() < 2e-3
    assert rr["rank_before"].between(1, 40).all() and rr["rank_after"].between(1, 40).all()
    assert ((rr["rank_before"] - rr["rank_after"]) == rr["rank_change"]).all()
    for sid, g in rr.groupby("scenario"):                                   # ranks: 1 is the best, ties share the lower number
        order = g.sort_values("rank_after")
        assert (np.diff(order["value_after_usd_kw_yr"].to_numpy()) <= 1e-9).all(), sid
    nr = rr[rr["scenario_effect"] == "not_replayed"]
    assert set(nr["scenario"]) == {"U02", "U07"} and nr["delta_usd_kw_yr"].abs().max() == 0
    # an upgrade only removes node-minus-zone value from the sites on that element: they lose, and never more than their whole uplift
    on = rr[(rr["scenario"].isin(["U01", "U03", "U05", "U06"])) & rr["on_upgraded_element"] & (rr["dominant_element"] == rr["element_key"])]
    assert len(on) > 0 and (on["delta_usd_kw_yr"] < 0).all()
    assert (on["value_after_usd_kw_yr"] >= on["zone_control_usd_kw_yr"] - 0.25).all(), "after the upgrade a site still earns about its zone (a site can keep a small exposure to another element: CHAPHI is $0.06 under after U05)"
    # the headline cases quoted in the README
    g = rr[(rr["scenario"] == "U03") & (rr["sub"] == "MIDW")].iloc[0]
    assert abs(g.value_before_usd_kw_yr - 247.9) < 0.06 and abs(g.value_after_usd_kw_yr - 50.6) < 0.06
    assert rr[rr["scenario"] == "PLANNED_2031"]["delta_usd_kw_yr"].abs().max() < 3.5


def test_first_order_recomputation_reproduces_phase4():
    cand = S.candidate_subs()
    fo = R.first_order([s for s in R.scenario_list() if s["scenario"] in ("U01", "U03", "U05", "U06", "U09")], cand)
    chk = R.check_first_order(fo)
    assert chk["n"] >= 15 and chk["max_abs_diff_v0"] < 1e-3 and chk["max_abs_diff_v1"] < 1e-3, chk


def test_pooled_agreement_numbers():
    pa = R.pooled_agreement(_read("upgrade_rerank.csv"))
    assert 0.80 < pa["rho_baseline_rank"] < 0.93
    assert pa["n_moved"] > 50 and pa["n_sign_flips"] < pa["n_moved"]


# ---------------------------------------------------------------------------------------------------- Tier 2
def test_tier2_valued_invariants():
    t0, t = _read("tier2.csv"), _read("tier2_valued.csv")
    assert len(t) == len(t0) == 2581 and list(t.columns[: len(t0.columns)]) == list(t0.columns)
    assert (t["cand_id"] == t0["cand_id"]).all()
    for c in ("reliability_need_score", "deferral_usd_kw_yr_low", "deferral_usd_kw_yr_mid", "deferral_usd_kw_yr_high", "voll_usd_yr_duration", "home_fleet_merchant_usd_kw_yr"):
        assert ((t[c] == t0[c]) | (t[c].isna() & t0[c].isna())).all(), c
    valued = t["nodal_value_screened"].astype(str).str.lower().eq("true")
    assert t.loc[valued, "route_desr_nodal_sub"].nunique() <= 150
    assert set(t["nodal_value_source"]) <= {"value_sites_2025_replay_own_ratio", "value_sites_2025_replay_zone_ratio", "zone_value_no_usable_sf", "zone_value_below_screen_cap", "zone_value_no_desr_route"}
    z = t[~valued]
    assert (z["nodal_vs_zone_uplift_usd_kw_yr"] == 0).all()
    assert (t["nodal_value_screened"].astype(str).str.lower().eq("true") == t["nodal_value_source"].str.startswith("value_sites")).all()
    # a valued node's ADER comparison is the same battery at the zone price: the uplift is the difference
    v = t[valued]
    assert ((v["nodal_value_usd_kw_yr"] - v["nodal_vs_zone_uplift_usd_kw_yr"]) - v["ader_zone_value_usd_kw_yr"]).abs().max() < 0.01
    # the phase 3 candidates that are Tier 2 buses carry their own backtest ratio and the value of sites.csv
    s = pd.concat([_read("sites.csv"), _read("extra_sites.csv")]).set_index("sub")      # revision 2: the sites that left the 40 (NOVICE, CRNTH) still have a lane of their own
    own = v[v["nodal_ratio_basis"] == "own"]
    assert len(own) > 0 and (own["nodal_value_usd_kw_yr"] - s.loc[own["route_desr_nodal_sub"], "fwd_learner_2025_replay"].to_numpy()).abs().max() < 1e-2
    assert t["nodal_value_p10_usd_kw_yr"].isna().all(), "no distribution of futures exists for a node lane"


def test_tier2_distance_columns():
    from gridsite import tier2_value as T

    assert abs(float(T._haversine_km(30.0, -100.0, 31.0, -100.0)) - 111.19) < 0.05, "one degree of latitude is 111.19 km"
    assert float(T._haversine_km(31.0, -99.0, 31.0, -99.0)) == 0.0
    t = _read("tier2_valued.csv")
    d, c = t["nodal_distance_to_element_km"], t["nodal_distance_class"]
    assert set(c) == {T.CLASS_NEAR, T.CLASS_FAR, T.CLASS_UNPLACED}
    assert (d[c == T.CLASS_NEAR] <= T.FAR_KM).all() and (d[c == T.CLASS_FAR] > T.FAR_KM).all() and d[c == T.CLASS_UNPLACED].isna().all()
    ends = T.element_end_points()
    assert len(ends["HARGRO_TWINBU1_1"]) == 2, "Twin Buttes and Hargrove both have a graph node"
    m = t[t["route_desr_nodal_sub"] == "MIDW"].iloc[0]
    assert 40 < m["nodal_distance_to_element_km"] < 80, "MIDW is about 60 km from Twin Buttes-Hargrove"


def test_tier2_screen_order_follows_the_score():
    t = _read("tier2_valued.csv")
    u = t.drop_duplicates("route_desr_nodal_sub")
    u = u[u["nodal_screen_rank"].notna()].sort_values("nodal_screen_rank")
    assert u["nodal_screen_score_usd_mw_yr"].is_monotonic_decreasing
    assert (u["nodal_value_screened"].astype(str).str.lower().eq("true") == (u["nodal_screen_rank"] <= 150)).all()


# ---------------------------------------------------------------------------------------------------- map and labels
def test_map_payload():
    p = report_map.build_payload()
    assert len(p["sites"]["rows"]) == 40 and len(p["sites"]["cols"]) == 150 and len(p["sites"]["labels"]) == 150
    assert all(l in check_labels.VALID_LABELS for l in p["sites"]["labels"])
    assert len(p["reliability"]) == 100 and len(p["excluded"]) == 148 and len(p["upgrades"]) == 10
    assert len(p["subs"]["sub"]) == len(p["subs"]["lat"]) == len(p["subs"]["conf"]) > 3000 and "NA" in p["subs"]["sub"], "the substation NA must survive"
    drawn = [c for c in p["constraints"] if c["drawn"]]
    assert 20 < len(drawn) <= len(p["constraints"]) and all(len(c["pts"]) == 2 for c in drawn)
    assert {u["id"] for u in p["upgrades"] if u["where"] == "none"} == {"U04"}, "only the interface has no place"
    js = json.dumps(report_map._clean(p), separators=(",", ":"), allow_nan=False)
    assert len(js) < 5e6
    assert len(p["tier2"]) <= report_map.TIER2_MAX


def test_map_files_and_page():
    h = report_map.OUT_HTML.read_text(encoding="utf-8")
    assert "leaflet/1.9.4/leaflet.min.js" in h and "integrity=" in h and "tile.openstreetmap.org" in h and "prefers-color-scheme: dark" in h
    assert "fetch(" not in h and "XMLHttpRequest" not in h, "must open as a local file"
    size = (report_map.OUT_HTML.stat().st_size + report_map.OUT_DATA.stat().st_size) / 1e6
    assert size < 5.0


def test_map_javascript_parses():
    node = shutil.which("node")
    if node is None:
        print("SKIP-NOTE  node is not on the PATH: the page script was not syntax-checked")
        return
    h = report_map.OUT_HTML.read_text(encoding="utf-8")
    body = h[h.index("(function(){"): h.rindex("})();") + len("})();")]
    tmp = Path(paths.DERIVED) / "runs6" / "_page_check.js"
    tmp.parent.mkdir(parents=True, exist_ok=True)
    tmp.write_text(body, encoding="utf-8")
    for f in (tmp, report_map.OUT_DATA):
        r = subprocess.run([node, "--check", str(f)], capture_output=True, text=True)
        assert r.returncode == 0, f"{f.name}: {r.stderr[:500]}"


def test_every_table_has_labels():
    res = check_labels.check_all()
    bad = [r for r in res if not r["ok"]]
    assert not bad, bad[:3]
    names = {r["file"] for r in res}
    assert {"upgrade_rerank.csv", "tier2_valued.csv"} <= names


def test_label_checker_catches_problems():
    import tempfile

    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        (d / "a.csv").write_text("x,y\n1,2\n", encoding="utf-8")
        (d / "a.labels.csv").write_text("column,label,source,note\nx,REAL,s,\ny,GUESS,s,\n", encoding="utf-8")
        (d / "b.csv").write_text("x,y\n1,2\n", encoding="utf-8")
        (d / "b.labels.csv").write_text("column,label,source,note\nx,REAL,s,\nz,SIM,s,\n", encoding="utf-8")
        (d / "c.csv").write_text("x\n1\n", encoding="utf-8")
        (d / "d.csv").write_text("x\n1\n", encoding="utf-8")
        (d / "d.labels.csv").write_text("column,label,source,note\nx,DERIVED,s,\n", encoding="utf-8")
        res = {r["file"]: r for r in check_labels.check_all(d)}
        assert res["a.csv"]["invalid_label"] == [("y", "GUESS")] and not res["a.csv"]["ok"]
        assert res["b.csv"]["missing"] == ["y"] and res["b.csv"]["stale"] == ["z"] and not res["b.csv"]["ok"]
        assert not res["c.csv"]["sidecar"] and not res["c.csv"]["ok"]
        assert res["d.csv"]["ok"]


# ---------------------------------------------------------------------------------------------------- commands
def test_cli_6_commands_and_no_collisions():
    import importlib

    from gridsite import cli_6

    assert set(cli_6.COMMANDS) == {"rerank", "tier2-value", "map", "report-check"}
    seen = {}
    for f in sorted((GRID_DIR / "gridsite").glob("cli_*.py")):
        mod = importlib.import_module(f"gridsite.{f.stem}")
        for k in getattr(mod, "COMMANDS", {}):
            seen.setdefault(k, []).append(f.stem)
    dup = {k: v for k, v in seen.items() if len(v) > 1}
    assert dup == {"map": ["cli_2", "cli_6"]}, f"only the documented `map` name is shared: {dup}"


def test_report_check_passes():
    from gridsite import report_check

    try:
        report_check.run()
    except SystemExit as ex:
        raise AssertionError("report-check reported failures (run it for the list)") from ex


if __name__ == "__main__":
    run_module(globals())
