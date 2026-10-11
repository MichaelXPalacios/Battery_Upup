"""Phase 4 (upgrades, non-wires alternatives, first-order rerank): hand-calculated cases, then invariants on the real tables.

Run (from docs/research/grid):  python tests/test_4_upgrades.py [name-substring ...]
The research venv has no pytest; each test is a plain function and the module ends with a tiny runner.
"""
import math
import sys
import time
import traceback
from pathlib import Path

GRID_DIR = Path(__file__).resolve().parents[1]
if str(GRID_DIR) not in sys.path:
    sys.path.insert(0, str(GRID_DIR))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from gridsite import upgrades as U  # noqa: E402


def close(a, b, tol=1e-6):
    return abs(a - b) <= tol * max(1.0, abs(b))


# ---------------------------------------------------------------------------------------------------- episodes and coverage
def test_coverage_hand_cases():
    e = np.array([1.0, 3.0])                                    # total 4 h
    assert close(U.coverage_at(e, 1.0), 0.5)
    assert close(U.duration_at_coverage(e, 0.5), 1.0)
    assert close(U.duration_at_coverage(e, 0.8), 2.2)           # (1 + D) / 4 = 0.8
    assert close(U.duration_at_coverage(e, 0.95), 2.8)
    assert close(U.duration_at_coverage(e, 1.0), 3.0)
    four = np.ones(4)
    assert close(U.duration_at_coverage(four, 0.5), 0.5) and close(U.duration_at_coverage(four, 0.8), 0.8)
    assert math.isnan(U.duration_at_coverage(np.zeros(0), 0.5))


def test_coverage_round_trip_random():
    rng = np.random.default_rng(7)
    for _ in range(50):
        e = rng.exponential(3.0, size=rng.integers(1, 30)) + 0.083
        for x in U.COVERAGE_TARGETS:
            d = U.duration_at_coverage(e, x)
            assert close(U.coverage_at(e, d), x, 1e-9), (e, x, d)
            assert U.coverage_at(e, d * 0.99) < x + 1e-9


def test_episode_hours_gap_rule():
    t0 = pd.Timestamp("2026-10-05 00:00", tz="UTC")
    mins = [0, 5, 10, 50, 55, 60, 240]                          # gap of 40 min merges, gap of 180 min does not
    ts = [t0 + pd.Timedelta(minutes=m) for m in mins]
    ep = U.episode_hours(ts, U.SCED_DT_MIN)
    assert len(ep) == 2 and close(ep[0], 6 * 5 / 60.0) and close(ep[1], 5 / 60.0)
    hrs = [t0 + pd.Timedelta(hours=h) for h in (0, 1, 2, 4, 7)]  # one missing hour merges, two missing hours do not
    ep = U.episode_hours(hrs, U.DAM_DT_MIN)
    assert list(np.round(ep, 6)) == [4.0, 1.0]
    assert U.episode_hours([], 5.0).size == 0


# ---------------------------------------------------------------------------------------------------- money
def test_battery_cost_lines():
    assert close(U.battery_usd_per_kw(4, 1.0), 4 * U.BATT_ENERGY_USD_KWH + U.BATT_POWER_USD_KW)
    assert close(U.battery_usd_per_kw(4, 1.0) / 4, 334.0, 1e-3)              # NREL: $334/kWh for the 4 h system
    assert close(U.battery_capex_musd(100, 4, 1.0), 133.6)                   # 100 MW x $1,336/kW
    assert U.battery_usd_per_kw(2, 1.0) < U.battery_usd_per_kw(4, 1.0)
    assert U.battery_usd_per_kw(2, 1.0) / 2 > U.battery_usd_per_kw(4, 1.0) / 4   # $/kWh falls with duration
    assert close(U.crf(0.08, 15), 0.116830, 1e-4)


def test_wires_option_arithmetic():
    el = pd.Series(dict(upgrade_id="T01", element_type="line", kv_class=138, element_key="T", kv=138, from_sub="", to_sub=""))
    opts = {o["option"]: o for o in U.wires_options(el, 10.0, 200.0, float("nan"))}
    rec = U.uc("reconductor per circuit", "138")
    bay = U.uc("substation: add one bay (ring bus)", "138")
    mid = 10.0 * rec.mid / 1e6 + 2 * U.TERMINAL_BAY_FRACTION * bay.mid / 1e6
    assert close(opts["reconductor"]["mid_musd"], mid)
    for o in opts.values():
        assert o["low_musd"] <= o["mid_musd"] <= o["high_musd"]
        assert o["low_musd"] <= o["mid_musd"] * (1 - U.ESTIMATE_BAND_LOW) + 1e-9 and o["high_musd"] >= o["mid_musd"] * (1 + U.ESTIMATE_BAND_HIGH) - 1e-9
    assert opts["dlr_aar"]["mid_musd"] < opts["reconductor"]["mid_musd"] < opts["rebuild"]["mid_musd"] < opts["new_parallel_line"]["mid_musd"]
    assert opts["new_parallel_line"]["ccn_needed"] and not opts["reconductor"]["ccn_needed"]
    x = pd.Series(dict(upgrade_id="T02", element_type="xfmr", kv_class=345, element_key="X", kv=345, from_sub="", to_sub=""))
    ox = {o["option"]: o for o in U.wires_options(x, 0, 430.0, float("nan"))}
    assert "autotransformer" in ox and "450 MVA" in ox["autotransformer"]["label"]          # smallest standard step >= 430 MVA is 450
    rate = U.uc("power transformer (autotransformer) 345/138 kV", "345/138")
    b345, b138 = U.uc("substation: add one bay (ring bus)", "345"), U.uc("substation: add one bay (ring bus)", "138")
    assert close(ox["autotransformer"]["mid_musd"], (450 * rate.mid + b345.mid + b138.mid) / 1e6)
    assert ox["autotransformer"]["lead_low"] >= U.LPT_LEAD_MONTHS[0] + U.XFMR_INSTALL_MONTHS - 1e-9


# ---------------------------------------------------------------------------------------------------- sizing
def _sites(abs_sf, cap):
    return pd.DataFrame(dict(sub=[f"S{i}" for i in range(len(abs_sf))], abs_sf=abs_sf, cap_mw=cap, adj=[-a for a in abs_sf], kv_max=[138.0] * len(abs_sf),
                             zone=["LZ_WEST"] * len(abs_sf), basis=["rt_reg"] * len(abs_sf)))


def test_size_for_relief_hand_cases():
    s = _sites([0.5, 0.25], [100.0, 100.0])
    r = U.size_for_relief(20.0, s)
    assert close(r["mw_required"], 40.0) and r["n_sites"] == 1 and close(r["mw_best_site"], 40.0)
    r = U.size_for_relief(60.0, s)                               # best site capped at 100 MW (relief 50), the rest from the second site: 10 / 0.25 = 40 MW
    assert close(r["mw_required"], 140.0) and r["n_sites"] == 2
    r = U.size_for_relief(80.0, s)                               # 100 x 0.5 + 100 x 0.25 = 75 < 80: not deliverable with these two sites
    assert math.isnan(r["mw_required"]) and close(r["shortfall_mw"], 5.0)


def test_site_caps_and_sf_cap():
    assert U.site_cap_mw(69) == U.SITE_MAX_MW[69] and U.site_cap_mw(138) == U.SITE_MAX_MW[138] and U.site_cap_mw(345) == U.SITE_MAX_MW[345]
    assert U.kv_class(138.1) == 138 and U.kv_class(345.1) == 345 and U.kv_class(69) == 69 and U.kv_class(float("nan")) is None


def test_parse_isd():
    assert U.parse_isd("2027-05-15") == pd.Timestamp("2027-05-15")
    assert U.parse_isd("2029-09") == pd.Timestamp("2029-09-15")
    assert U.parse_isd("2031") == pd.Timestamp("2031-06-30")
    assert U.parse_isd("summer 2030") == pd.Timestamp("2030-07-15")
    assert pd.isna(U.parse_isd("9999-01-01")) and pd.isna(U.parse_isd(float("nan"))) and pd.isna(U.parse_isd("tbd"))


# ---------------------------------------------------------------------------------------------------- the interface
def test_apply_upgrade_semantics():
    c = U.load_constraints()
    cid = "6437__F|DMTSCOS5"
    sibs = list(c.loc[c["constraint_name"] == "6437__F", "cid"])
    a = U.apply_upgrade([cid])
    assert set(a) == set(sibs) and set(a.values()) == {U.RESIDUAL_MU_SCALE_WIRES}
    assert U.apply_upgrade([cid], expand_element=False) == {cid: U.RESIDUAL_MU_SCALE_WIRES}
    assert set(U.apply_upgrade("6437__F", residual=0.0).values()) == {0.0}
    assert U.apply_upgrade(["U01"]) == a
    g = U.apply_upgrade(["WESTEX"])
    assert list(g.values()) == [U.RESIDUAL_MU_SCALE_GTC]
    both = U.apply_upgrade(["U01", cid], residual=0.3)
    assert set(both.values()) == {0.3}
    try:
        U.apply_upgrade(["not-a-constraint"])
        raise AssertionError("expected ValueError")
    except ValueError:
        pass
    assert all(0.0 <= v <= 1.0 for v in U.apply_upgrade(["U01", "U03", "U04"]).values())


def test_basis_half_life_hand_case():
    sched = pd.DataFrame(dict(cid=["a", "b"], isd=[pd.Timestamp("2027-10-10"), pd.Timestamp("2030-10-10")], status=["planned", "planned"],
                              residual=[0.0, 0.0], element_type=["line", "line"]))
    r = U.basis_half_life({"a": 10.0, "b": 10.0}, schedule=sched, t0=pd.Timestamp("2026-10-10"))
    assert close(r["v0"], 20.0) and close(r["half_life_years"], 365 / 365.25, 1e-6)       # V falls to 10 = half when a is relieved
    r = U.basis_half_life({"a": 4.0, "b": 10.0}, schedule=sched, t0=pd.Timestamp("2026-10-10"))
    assert close(r["half_life_years"], (pd.Timestamp("2030-10-10") - pd.Timestamp("2026-10-10")).days / 365.25, 1e-6)
    r = U.basis_half_life({"c": 5.0}, schedule=sched)
    assert r["half_life_years"] == float("inf")
    s2 = sched.assign(status=["in_service", "planned"])                                     # in service = already in the data: ignored
    assert U.basis_half_life({"a": 10.0}, schedule=s2, t0=pd.Timestamp("2026-10-10"))["half_life_years"] == float("inf")


# ---------------------------------------------------------------------------------------------------- real tables
REQUIRED = ["15060__B", "HARGRO_TWINBU1_1", "LNGSW-CONSW LINES", "1710__C", "WESTEX", "6520__E", "6437__F", "LARDVN_LASCRU1_1", "STPWAP39_1", "587__A"]


def test_selection_covers_required_structural_elements():
    S = U.select_elements()
    keys = set(S["element_key"])
    missing = [k for k in REQUIRED if k not in keys]
    assert not missing, missing
    assert S["upgrade_id"].is_unique and len(S) >= 20
    top = U.all_elements().sort_values("best_rank").head(U.TOP_PRIORITY_ELEMENTS)["element_key"]
    assert set(top) <= keys
    assert S.loc[S["element_key"] == "15060__B", "window_binding"].iloc[0] == False  # noqa: E712  (never priced, still included)


def test_upgrades_table_invariants():
    up, opt = U.build_upgrades()
    assert len(up) == len(U.select_elements())
    prim = up.dropna(subset=["cost_mid_musd"])
    assert ((prim["cost_low_musd"] <= prim["cost_mid_musd"]) & (prim["cost_mid_musd"] <= prim["cost_high_musd"])).all()
    assert ((prim["lead_time_low_months"] <= prim["lead_time_mid_months"] + 1e-9) & (prim["lead_time_mid_months"] <= prim["lead_time_high_months"] + 1e-9)).all()
    nz = up.dropna(subset=["nwa_central_mw"])
    assert ((nz["nwa_mw_min"] <= nz["nwa_central_mw"] + 1e-9) & (nz["nwa_central_mw"] <= nz["nwa_mw_max"] + 1e-9)).all()
    chk = nz.dropna(subset=["nwa_vs_wires_ratio", "cost_mid_musd"])
    assert np.allclose(chk["nwa_cost_central_musd"] / chk["cost_mid_musd"], chk["nwa_vs_wires_ratio"], rtol=1e-9)
    assert set(up["planned"]) <= {"yes", "likely", "identified", "adjacent", "hint", "done", "no"}
    assert (opt.groupby("upgrade_id")["is_primary"].sum() <= 1).all()
    assert up["decider"].str.len().min() > 40 and up["citation"].str.len().min() > 20


def test_nwa_invariants():
    n = U.build_nwa()
    assert {"import_discharge", "export_charge"} >= set(n["side"])
    g = n.dropna(subset=["mw_installed"])
    # MW is linear in the relief target while one site carries it; the energy duration is monotone in coverage
    one = g[g["n_sites"] == 1]
    for (uid, side, cov), d in one.groupby(["upgrade_id", "side", "coverage_pct"]):
        d = d.sort_values("relief_pct")
        if len(d) == 3:
            assert np.allclose(d["mw_installed"].values / d["relief_pct"].values, d["mw_installed"].values[0] / d["relief_pct"].values[0], rtol=1e-9)
    for (uid, side, rel), d in g.groupby(["upgrade_id", "side", "relief_pct"]):
        d = d.sort_values("coverage_pct")
        assert (np.diff(d["episode_hours"].values) >= -1e-9).all()
        assert (np.diff(d["battery_cost_mid_musd"].values) >= -1e-9).all()
        assert (d["battery_hours"] >= U.BATT_MIN_HOURS - 1e-9).all() and (d["battery_hours"] <= U.BATT_MAX_HOURS + 1e-9).all()
        assert (d["coverage_achieved"] <= 1.0 + 1e-9).all()
    assert (g["mw_installed"] >= g["mw_required"] - 1e-9).all()
    assert (g["battery_cost_low_musd"] < g["battery_cost_mid_musd"]).all() and (g["battery_cost_mid_musd"] < g["battery_cost_high_musd"]).all()
    # a measured SF means a regression method; an assumed one is flagged
    assert set(g.loc[g["sf_confidence"] == "assumed", "sf_basis"]) <= {"fallback_assumed"}
    assert set(g.loc[g["sf_confidence"] == "measured", "sf_basis"]) <= {"rt_reg", "dam_reg"}
    # sign convention: discharging relief sits on the import side (SF < 0), charging relief on the export side
    imp = g[(g["side"] == "import_discharge") & (g["sf_basis"] != "fallback_assumed")]
    exp = g[(g["side"] == "export_charge") & (g["sf_basis"] != "fallback_assumed")]
    assert (imp["best_site_sf"] < 0).all() and (exp["best_site_sf"] > 0).all()


def test_value_table_signs_and_rerank_consistency():
    vt = U.value_table()
    assert (vt["v_2h"] >= 0).all() and (vt["v_4h"] > vt["v_2h"]).all()
    assert (vt.loc[vt["side"] == "import", "dsf"] < 0).all() and (vt.loc[vt["side"] == "export", "dsf"] >= 0).all()
    assert set(vt["method"]) <= {"rt_reg", "dam_reg"}                          # never the physics proxy
    assert not vt["quality"].str.contains("weak|implausible|low").any()
    rr = U.build_rerank()
    lose = rr[rr["group"] == "loser"]
    assert (lose["v1_usd_kw_yr"] <= lose["v0_usd_kw_yr"] + 1e-9).all() and (lose["delta_usd_kw_yr"] <= 1e-9).all()
    up = rr[rr["group"] == "riser"]
    assert (up["rank_change"] > 0).all() and (up["rank1"] <= U.RISER_UNIVERSE).all()
    assert set(rr.loc[rr["group"] == "not_rankable", "scenario"]) >= {"U02", "U04"}   # proxy-only and attenuated-only elements are not ranked


def test_sph_methods_and_cap():
    sph = U.sph_table().set_index("cid")
    assert sph.loc["6437__F|DMTSCOS5", "sph_basis"] == "imm_rent_alloc"
    rent = U.load_constraints().set_index("cid").loc["6437__F|DMTSCOS5", "annual_rent_alloc_musd"]
    assert close(sph.loc["6437__F|DMTSCOS5", "sph_usd_mw_yr"], rent * 1e6 / sph.loc["6437__F|DMTSCOS5", "limit_mw"], 1e-9)
    w = sph[sph["sph_basis"].str.startswith("window")]
    assert w["sph_usd_mw_yr"].notna().all()
    # no window-annualised element may exceed the 10th-ranked IMM rent over its limit (summed over the element's contingencies)
    c = U.load_constraints().set_index("cid")
    tot = w["sph_usd_mw_yr"].groupby(c.loc[w.index, "constraint_name"]).sum()
    lim = w["limit_mw"].groupby(c.loc[w.index, "constraint_name"]).median()
    assert (tot <= U.IMM_FLOOR_RANK_RENT_MUSD * 1e6 / lim * (1 + 1e-9)).all()


def test_named_constants_match_the_window():
    cv = U.derive_cover()
    for d, name in ((2, "COVER_2H"), (4, "COVER_4H")):
        mean = (cv["rt"][d] + cv["dam"][d]) / 2
        assert abs(mean - getattr(U, name)) < 0.03, (name, mean, getattr(U, name))
    assert abs(U.CAPTURE_FACTOR["2h"] - U.ARENA_LEARNER_SHARE * U.COVER_2H) < 1e-12


def run_module(ns, argv=None):
    argv = sys.argv[1:] if argv is None else argv
    tests = [(k, v) for k, v in ns.items() if k.startswith("test_") and callable(v)]
    if argv:
        tests = [(k, v) for k, v in tests if any(a in k for a in argv)]
    failed, t_all = [], time.time()
    for name, fn in tests:
        t0 = time.time()
        try:
            fn()
            print(f"PASS  {name}  ({time.time() - t0:.1f}s)", flush=True)
        except Exception:
            failed.append(name)
            print(f"FAIL  {name}  ({time.time() - t0:.1f}s)", flush=True)
            traceback.print_exc()
    print(f"{len(tests) - len(failed)}/{len(tests)} passed in {time.time() - t_all:.1f}s", flush=True)
    if failed:
        print("FAILED:", ", ".join(failed))
        sys.exit(1)


if __name__ == "__main__":
    run_module(globals())
