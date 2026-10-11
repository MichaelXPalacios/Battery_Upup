"""Phase 7 (agent R1, regional coverage): unit tests on synthetic cases and invariants on the real tables.

Run (from docs/research/grid):  python tests/test_7_coverage.py [name-substring ...]
The research venv has no pytest; each test is a plain function and the module ends with the harness runner of test_3a.
The synthetic tests take seconds; the table tests read the outputs of `run_grid.py regional-basis`, `sf-structural` and `regional-candidates` (run them first) and
the lake (read-only). Nothing here recomputes the factor model on the real panel.
"""
import re
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
from gridsite import check_labels, paths  # noqa: E402
from gridsite import coverage as C  # noqa: E402

DATA = paths.REPO_DATA
MY_TABLES = ["regional_basis", "regional_basis_summary", "sf_structural", "sf_structural_constraints", "sf_structural_targets", "sf_structural_components",
             "sf_structural_validation", "regional_candidates", "regional_upgrade_reach", "imm_2026_anchors"]


def _read(name):
    return pd.read_csv(DATA / name, keep_default_na=False, na_values=[""], low_memory=False)


# ---------------------------------------------------------------------------------------------------- regions and basis
def test_region_of_rules():
    la0, la1, lo0, lo1 = C.PANHANDLE_BOX
    assert C.region_of((la0 + la1) / 2, (lo0 + lo1) / 2, "POTTER", "LZ_WEST")[0] == "Panhandle"
    assert C.region_of(26.2, -98.2, "HIDALGO", "LZ_SOUTH")[0] == "Rio Grande Valley"
    assert C.region_of(None, None, "CAMERON", "LZ_SOUTH")[0] == "Rio Grande Valley", "county alone decides the Valley"
    assert C.region_of(31.9, -102.1, "MIDLAND", "LZ_WEST")[0] == "Far West"
    assert C.region_of(32.0, -100.0, "NOLAN", "LZ_WEST")[0] == "Other", "east of the Far West longitude"
    assert C.region_of(33.58, -101.85, "LUBBOCK", "LZ_WEST")[0] == "Other", "Lubbock is north of the Far West latitude cap"
    assert C.region_of(27.6, -99.5, "WEBB", "LZ_SOUTH")[0] == "Laredo/South"
    assert C.region_of(29.7, -95.3, "HARRIS", "LZ_HOUSTON")[0] == "Houston"
    assert C.region_of(32.8, -96.8, "DALLAS", "LZ_NORTH")[0] == "DFW"
    assert C.region_of(30.3, -97.7, "TRAVIS", "LZ_AEN")[0] == "Central"
    assert C.region_of(None, None, None, "LZ_NORTH") == ("Other", "none")
    # the Panhandle box is Phase 3b's, not a copy
    from gridsite import nodal_lanes as L

    assert C.PANHANDLE_BOX == L.PANHANDLE_BOX


def test_basis_stats_thresholds_and_hours():
    hours = np.tile(np.arange(24), 42)
    b = np.zeros(24 * 42)
    b[hours == 20] = -60.0                     # one hour of the day at -$60 every day
    b[hours == 19] = 12.0
    s = C._stats(b, hours)
    assert s["n"] == 1008 and s["n10"] == 84 and s["n50"] == 42 and s["n10_pos"] == 42 and s["n10_neg"] == 42
    assert s["top3"].split(";")[0] == "20" and s["top3"].split(";")[1] == "19"
    assert abs(float(s["top3_mean"].split(";")[0]) + 60.0) < 1e-9
    assert np.isnan(C._stats(b[:100], hours[:100])["mean"]), "fewer than MIN_HOURS_STATS hours gives no statistics"


def test_dam_oracle_value_closed_form():
    # flat prices: nothing to earn; wear cost makes a small spread worthless
    assert np.allclose(C.dam_oracle_value(np.full((2, 3, 24), 40.0)), 0.0)
    assert np.allclose(C.dam_oracle_value(np.tile(np.linspace(30, 35, 24), (1, 1, 1))), 0.0, atol=1e-9)
    # two cheap hours then two dear hours: charge at most floor(eta*1MW/0.25) = 3 steps (0.75 MWh stored) an hour, discharge at most 4 steps (1 MWh stored) an hour
    p = np.full((1, 1, 24), 80.0)               # 80 is above the break-even buy price 0.85 x (110 - 20) = 76.5, so no extra energy is bought, and it is spent only on the dear hours
    p[0, 0, 2:4] = 10.0
    p[0, 0, 10:12] = 110.0
    eta = np.sqrt(C.BAT_RTE)
    stored = 1.5                               # 2 hours x 0.75 MWh
    expected = (-stored / eta * 10.0 + (110.0 - C.BAT_WEAR) * stored * eta) / 1000.0
    got = C.dam_oracle_value(p)[0, 0]
    assert abs(got - expected) < 1e-9, (got, expected)
    # a longer, deeper spread cannot earn less
    p2 = p.copy()
    p2[0, 0, 10:12] = 150.0
    assert C.dam_oracle_value(p2)[0, 0] > got


def test_varimax_keeps_communalities_and_rotation_orthogonal():
    rng = np.random.default_rng(1)
    phi = rng.normal(size=(60, 4))
    out, R = C.varimax(phi)
    assert np.allclose(R.T @ R, np.eye(4), atol=1e-8)
    assert np.allclose((out ** 2).sum(1), (phi ** 2).sum(1), atol=1e-8)


# ---------------------------------------------------------------------------------------------------- factor model on synthetic data
def _synthetic(T=300, N=60, seed=2):
    """Two sparse non-negative shadow-price series acting on disjoint station groups with opposite signs; PLAN.md: b = -SF mu."""
    rng = np.random.default_rng(seed)
    mu = np.zeros((T, 2))
    mu[:, 0] = np.where(rng.random(T) < 0.25, rng.uniform(5, 40, T), 0.0)
    mu[:, 1] = np.where(rng.random(T) < 0.20, rng.uniform(5, 30, T), 0.0)
    SF = np.zeros((N, 2))
    SF[:20, 0] = rng.uniform(0.6, 1.0, 20)        # export side of constraint 0
    SF[20:30, 0] = -rng.uniform(0.3, 0.6, 10)     # import side of constraint 0
    SF[35:55, 1] = -rng.uniform(0.7, 1.0, 20)     # import side of constraint 1
    b = -(mu @ SF.T) + rng.normal(0, 0.3, (T, N))
    return mu, SF, b


def test_station_sf_recovers_known_shift_factors_and_sign():
    mu, SF, b = _synthetic()
    got, se = C.station_sf(b, mu)
    assert np.abs(got - SF).max() < 0.05
    assert (got[:20, 0] > 0).all() and (got[35:55, 1] < 0).all(), "SF > 0 is the export side: its price falls when the constraint binds"
    assert (se < 0.05).all()


def test_nmf_recovers_two_patterns_on_synthetic_panel():
    mu, SF, b = _synthetic()
    B = pd.DataFrame(b, index=pd.date_range("2026-07-01", periods=len(b), freq="h", tz="UTC"), columns=[f"S{i}" for i in range(b.shape[1])])
    m = C.fit_nmf(B, k=2, tau=0.5)
    sig = m["sig"]                                  # + = discount (export side)
    cols = np.array(m["cols"])
    idx = [int(c[1:]) for c in cols]
    found = []
    for j in range(2):
        v = np.zeros(SF.shape[0])
        v[idx] = sig[:, j]
        found.append(v)
    for true in (SF[:, 0], SF[:, 1]):
        r = max(abs(np.corrcoef(true, f)[0, 1]) for f in found)
        assert r > 0.9, r
    # the recovered export-side pattern has discount (positive) loadings on stations 0-19
    j0 = int(np.argmax([np.corrcoef(SF[:, 0], f)[0, 1] for f in found]))
    assert found[j0][:20].mean() > 0 and found[j0][20:30].mean() < 0


def test_weighted_median_ignores_one_odd_neighbour():
    v = np.array([[0.0, 0.0, 0.0, 0.7], [0.8, 0.9, 0.8, 0.0], [0.5, -0.5, 0.0, 0.0]])
    w = np.array([[1.0, 1.0, 1.0, 1.0], [1.0, 1.0, 1.0, 1.0], [0.0, 0.0, 0.0, 0.0]])
    out = C.weighted_median(v, w)
    assert out[0] == 0.0 and out[1] == 0.8 and np.isnan(out[2]), "an inverse-distance mean would give 0.175 and 0.625"
    heavy = C.weighted_median(np.array([[0.0, 1.0]]), np.array([[1.0, 5.0]]))
    assert heavy[0] == 1.0, "the nearer, heavier neighbour decides"


def test_pole_scale_is_robust_to_one_wild_station():
    v = np.array([1.0, 0.9, 0.9, 0.8, 0.8, 0.7] + [0.0] * 20 + [30.0])
    assert C.pole_scale(v) < 10.0 and C.pole_scale(v[:-1]) == np.mean(np.sort(np.abs(v[:-1]))[::-1][:C.POLE_TOP_N])


def test_rereference_and_clip_in_physical_sf():
    mu, SF, b = _synthetic()
    got, se = C.station_sf(b, mu)
    R = dict(SF=got, SE=se, SFz=got[:8], st=pd.DataFrame(index=[f"S{i}" for i in range(got.shape[0])]), cols=[f"S{i}" for i in range(got.shape[0])])
    asg = pd.DataFrame([dict(pattern=0, mask_to_domain=False), dict(pattern=1, mask_to_domain=False)])
    cal = {0: dict(scale=0.5), 1: dict(scale=1.0)}
    phys = C.physical_sf(R, asg, cal)
    assert abs(np.median(phys[0]["sf_pole"])) < 1e-9, "median station is the reference"
    assert np.allclose(phys[0]["sf"], np.clip(phys[0]["sf_pole"] * 0.5, -1, 1))
    assert np.abs(phys[1]["sf"]).max() <= C.SF_ABS_CAP
    small = np.abs(phys[0]["sf_pole"]) < C.SF_FOOTPRINT_MIN
    assert (phys[0]["sf"][small] == 0).all(), "loadings below the footprint threshold are read as zero"


# ---------------------------------------------------------------------------------------------------- IMM constants
IMM_JULY_TOTALS = {   # REAL: the "Value" column printed in the July 2026 report (January-July totals)
    ("RT", "intervals", "NELRIO"): 12536, ("RT", "intervals", "NE_LOB"): 6653, ("RT", "intervals", "WESTEX"): 6109, ("RT", "intervals", "6965__A"): 6586,
    ("RT", "rent_musd", "WESTEX"): 82.73, ("RT", "rent_musd", "6965__A"): 48.94, ("DA", "hours", "NELRIO"): 1975, ("DA", "rent_musd", "WESTEX"): 97.94,
    ("DA", "rent_musd", "6965__A"): 49.47}


def test_imm_constants_sum_to_the_printed_totals():
    for (mk, me, key), want in IMM_JULY_TOTALS.items():
        v = C.IMM_2026[(mk, me)][key]
        assert abs(sum(v[:7]) - want) < (0.06 if me == "rent_musd" else 1.5), (mk, me, key, sum(v[:7]), want)     # the report rounds its totals; a count can be off by one
    assert C.imm_july_share("NELRIO")["DA"] == 278 / 744


def test_imm_parser_matches_the_typed_constants():
    if shutil.which("pdftotext") is None or not C.IMM_PDF_AUG.exists():
        print("  (skipped: pdftotext or the raw August report is not available)")
        return
    t = C.parse_imm_report(C.IMM_PDF_AUG)
    assert len(t) == 40 and set(t["market"]) == {"RT", "DA"}
    for (mk, me), d in C.IMM_2026.items():
        for key, v in d.items():
            row = t[(t["market"] == mk) & (t["metric"] == ("rent_musd" if me == "rent_musd" else "count")) & (t["code"] == key)]
            assert len(row) == 1, (mk, me, key)
            r = row.iloc[0]
            assert np.allclose([r.jan, r.feb, r.mar, r.apr, r.may, r.jun, r.jul, r.aug], v, atol=0.006), (mk, me, key)
    # the lists are ranked 1..10 in each of the four lists
    for (mk, me), g in t.groupby(["market", "metric"]):
        assert sorted(g["rank"]) == list(range(1, 11)), (mk, me)


# ---------------------------------------------------------------------------------------------------- the lake and the regional basis table
def test_lake_panel_is_read_only_and_complete():
    panel = C.load_panel()
    assert panel.shape == (1008, 1003) or panel.shape[0] == 1008
    assert panel.index.min() == pd.Timestamp("2026-07-01 05:00", tz="UTC") and panel.index.max() == pd.Timestamp("2026-08-12 04:00", tz="UTC")
    assert "HB_BUSAVG" in panel.columns and panel["HB_BUSAVG"].notna().all()
    src = Path(C.__file__).read_text(encoding="utf-8")
    for line in src.splitlines():
        if "LAKE_DAM60" in line:
            assert not re.search(r"to_parquet|to_csv|write_|unlink|rmtree|shutil", line), line


def test_regional_basis_table_matches_the_lake():
    d = _read("regional_basis.csv")
    panel = C.load_panel()
    assert len(d) == panel.shape[1]
    hubs = d[d["settlement_point"] == "HB_BUSAVG"].iloc[0]
    assert hubs["basis_sys_mean"] == 0
    rn = d[(d["sp_type"] == "resource_node") & d["basis_zone_mean"].notna()]
    rng = np.random.default_rng(3)
    for sp in rng.choice(rn["settlement_point"].to_numpy(), 6, replace=False):
        r = rn[rn["settlement_point"] == sp].iloc[0]
        want = float(np.nanmean(panel[sp].to_numpy(float) - panel[r["zone"]].to_numpy(float)))
        assert abs(r["basis_zone_mean"] - want) < 5e-3, (sp, r["basis_zone_mean"], want)
        n10 = int((np.abs(panel[sp] - panel[r["zone"]]) > 10).sum())
        assert r["hours_gt10_zone"] == n10
    # nothing is both 'hub' typed and given a region
    assert d.loc[d["sp_type"] != "resource_node", "region"].isna().all()
    assert set(d["region"].dropna()) <= set(C.REGIONS)


def test_regional_summary_reproduces_the_node_table():
    d = _read("regional_basis.csv")
    s = _read("regional_basis_summary.csv")
    rn = d[(d["sp_type"] == "resource_node") & (d["n_hours"] >= C.MIN_HOURS_STATS) & d["hub"].notna() & d["zone"].notna()]
    for rg in C.REGIONS:
        g = rn[rn["region"] == rg]
        row = s[(s["region"] == rg) & (s["reference"] == "zone")]
        if g.empty:
            continue
        assert int(row["n_nodes"].iloc[0]) == len(g)
        assert abs(row["basis_mean"].iloc[0] - g["basis_zone_mean"].mean()) < 5e-3
    assert set(s["reference"]) == {"hub", "zone", "system"}
    # the Valley is a discount region in this window: the brief's question
    v = s[(s["region"] == "Rio Grande Valley") & (s["reference"] == "system")].iloc[0]
    assert v["basis_mean"] < 0 and v["worst_hour_mean"] < -5 and v["share_hours_neg10"] > v["share_hours_pos10"]


# ---------------------------------------------------------------------------------------------------- shift-factor outputs
def test_sf_structural_parquet_schema_and_conventions():
    t = pd.read_parquet(C.SF_STRUCTURAL_PARQUET)
    assert list(t.columns) == ["sub", "bus", "level", "cid", "sf", "sf_pole", "se", "method", "quality", "dist_km", "n_support"]
    assert set(t["level"]) <= {"station", "zone", "hub"} and set(t["method"]) <= {"dam60_factor", "dam60_factor_interp"}
    assert t["sf"].abs().max() <= C.SF_ABS_CAP + 1e-6 and t["sf_pole"].abs().max() <= C.SF_ABS_CAP + 1e-6
    assert t["sf"].notna().all() and t["quality"].str[0].isin(list("ABCDU")).all()
    st = t[t["level"] == "station"]
    assert (st["sf_pole"].abs() >= C.SF_NOISE_FLOOR - 1e-6).all(), "sub-threshold stations are absent (read as 0), not written"
    cons = _read("sf_structural_constraints.csv")
    assert set(t["cid"]) == set(cons["cid"])
    for cid, g in t[t["level"] == "zone"].groupby("cid"):
        assert set(g[g["sub"].str.startswith("LZ_")]["sub"]) == {"LZ_AEN", "LZ_CPS", "LZ_HOUSTON", "LZ_LCRA", "LZ_NORTH", "LZ_RAYBN", "LZ_SOUTH", "LZ_WEST"}
    # interpolated rows say how far the nearest priced station is
    ip = st[st["method"] == "dam60_factor_interp"]
    assert (ip["dist_km"] <= C.INTERP_MAX_KM).all() and (ip["n_support"] >= 2).all() and ip["quality"].str.contains(r"\+interp").all()


def test_sign_convention_against_observed_basis():
    """SF > 0 (export side) must mean a price below the system when the pattern binds. Mean basis over the window tests the always-discounted patterns (E_PASP, NELRIO);
    the two-sided Far West pattern is tested on its import side (SF < 0 trades at a premium), and WESTEX through the sign agreement with the shift factors implied by its
    published Sep-Oct shadow price (validation table)."""
    t = pd.read_parquet(C.SF_STRUCTURAL_PARQUET)
    rb = _read("regional_basis.csv")
    rb = rb[rb["sp_type"] == "resource_node"].groupby("substation")["basis_sys_mean"].mean()
    for cid, thr in (("E_PASP|BASE CASE", 0.5), ("NELRIO|BASE CASE", 0.5)):
        g = t[(t["cid"] == cid) & (t["level"] == "station") & (t["method"] == "dam60_factor") & (t["sf_pole"] > thr)]
        b = rb.reindex(g["sub"]).dropna()
        assert len(b) >= 4
        assert (b < 0).mean() > 0.8, (cid, (b < 0).mean())
    g = t[(t["cid"] == "I_FW_N|BASE CASE") & (t["level"] == "station") & (t["method"] == "dam60_factor") & (t["sf_pole"] < -0.5)]
    b = rb.reindex(g["sub"]).dropna()
    assert len(b) >= 20 and b.mean() > 0, "import side (SF < 0): the Far West trades at a premium on average over the window"
    v = _read("sf_structural_validation.csv")
    g = v[(v["cid"] == "WESTEX|BASE CASE") & (v["metric"] == "sf_sign_agreement")]
    assert len(g) == 1 and g["value"].iloc[0] >= 0.7


def test_constraint_table_has_the_phase3b_schema():
    cons = _read("sf_structural_constraints.csv")
    synth = _read("constraint_synth.csv")
    assert list(synth.columns) == list(cons.columns[: len(synth.columns)]), "first columns equal constraint_synth.csv so rows can be concatenated"
    assert cons["cid"].is_unique
    assert (cons["sph_usd_mw_yr"] > 0).all() and (cons["H_hours"] > 0).all() and (cons["mean_shadow_usd_mwh"] < 0.5 * cons["cap_usd_mwh"]).all()
    assert set(cons["driver_location"].dropna()) <= set(C.DRIVER_LOCS) and set(cons["driver_sign"]) <= {-1, 1}
    assert set(cons["sf_method"]) == {"dam60_factor"}
    # the pole-scale pair and the chosen-scale pair carry the same shift-factor x shadow-price product
    ratio = cons["sph_usd_mw_yr"] * cons["scale"] / cons["sph_pole_usd_mw_yr"]
    assert np.allclose(ratio, 1.0, atol=1e-3)
    # names: an identified pattern keeps its constraint id, an unsupported one gets an R1_ id
    assert cons.loc[cons["status"] == "identified", "cid"].map(lambda c: not c.startswith("R1_")).all()
    assert cons.loc[cons["status"] != "identified", "cid"].map(lambda c: c.startswith("R1_")).all()


def test_phase3b_input_files_have_the_phase2_and_zone_schemas():
    base = pd.read_parquet(C.SEP_OCT_SF_SUB)
    plus = pd.read_parquet(C.SF_SUB_PLUS)
    assert list(plus.columns) == list(base.columns)
    ours = plus[plus["method"] == "dam60_factor"]
    cons = _read("sf_structural_constraints.csv")
    assert set(ours["cid"]) == set(cons.loc[cons["replay"] == True, "cid"])    # noqa: E712
    assert len(plus) - len(ours) == len(base), "all phase 2 rows are kept"
    z0 = pd.read_parquet(C.ZONE_SF_FILE)
    z1 = pd.read_parquet(C.ZONE_SF_PLUS)
    assert list(z1.columns) == list(z0.columns) and len(z1) - len(z1[z1["method"] == "dam60_factor"]) == len(z0)


def test_targets_table_covers_the_brief_and_is_self_consistent():
    t = _read("sf_structural_targets.csv")
    assert len(t) == 14 and set(t["target"]) == {x["key"] for x in C.TARGETS}
    assert set(t["status"]) <= {"identified", "pattern_only", "not_identified"}
    idn = t[t["status"] == "identified"]
    assert idn["uses_cid"].notna().all() and (idn["pattern"] >= 0).all()
    assert t.loc[t["grade"] == "X", "uses_cid"].isna().all()
    a = t[t["grade"] == "A"]
    assert (a["oop_r"] >= C.OOP_GRADE_A).all()
    b = t[t["grade"] == "B"]
    assert ((b["oop_r"] >= C.OOP_GRADE_B) & (b["oop_r"] < C.OOP_GRADE_A)).all()
    d = t[t["grade"] == "D"]
    assert (d["oop_r"] < C.OOP_GRADE_B).all() and (d["status"] == "pattern_only").all()
    # a line target without a pocket is not identified, and the brief's four upgrade elements are all in the table
    for k in ("VEALMOOR_KOCH", "LNGSW_CONSW", "WESTEX", "BELCNTY_SALSW"):
        assert k in set(t["target"])
    tg = t.set_index("target")
    assert tg.at["VEALMOOR_KOCH", "status"] == "not_identified" and tg.at["LNGSW_CONSW", "status"] == "not_identified"
    # sf_structural has no rows for a target that was not identified
    rows = pd.read_parquet(C.SF_STRUCTURAL_PARQUET)
    for cid in t.loc[t["status"] == "not_identified", "cid"]:
        assert cid not in set(rows["cid"])


def test_validation_table_has_the_required_checks():
    v = _read("sf_structural_validation.csv")
    assert {"holdout_split", "stability", "sepoct_published", "sepoct_recovery", "k_sweep", "imm_timing", "interp_loo", "lane_formula"} <= set(v["check"])
    corr = v[v["metric"].str.contains("corr|_r$|match_r|r2_|agreement|pca_cumulative", regex=True)]["value"].dropna()
    assert corr.between(-1.0001, 1.0001).all()
    all_ = v[v["cid"] == "ALL"].set_index(["check", "metric", "scope"])["value"]
    assert all_.loc[("holdout_split", "r2_all_stations", "last 10 days")] > 0.8
    assert 0.0 < all_.loc[("sepoct_recovery", "r2_all_patterns", "10 Sep-10 Oct")] < 0.8, "loadings from summer transfer only partly to Sep-Oct; do not oversell them"
    cons = _read("sf_structural_constraints.csv")
    for cid in cons["cid"]:
        g = v[v["cid"] == cid]
        assert {"holdout_split", "stability"} <= set(g["check"]), cid


def test_grade_a_patterns_track_their_published_shadow_price_out_of_period():
    v = _read("sf_structural_validation.csv")
    t = _read("sf_structural_targets.csv")
    for _, r in t[t["grade"] == "A"].iterrows():
        g = v[(v["cid"] == r["uses_cid"]) & (v["check"] == "sepoct_published") & (v["metric"] == "corr_deseasonalised") & (v["scope"] == r["cid"])]
        assert len(g) == 1 and g["value"].iloc[0] >= C.OOP_GRADE_A


# ---------------------------------------------------------------------------------------------------- candidates
def test_regional_candidates_are_eligible_and_spread_out():
    from gridsite import sites as S

    c = _read("regional_candidates.csv")
    reg = c[c["role"] == "regional"]
    el = S.eligible_substations()
    assert set(reg["sub"]) <= set(el["sub"]), "same eligibility as Phase 3b: NP4-160, placed >= 0.5, >= 69 kV, not risk-excluded"
    assert (reg["kv_max"] >= 69).all() and (reg["match_conf"] >= 0.5).all()
    assert set(reg["region"]) <= set(C.CAND_REGIONS) and set(reg["side"]) <= {"import", "export"}
    for (rgn, side), g in reg.groupby(["region", "side"]):
        assert len(g) <= C.CAND_PER_SIDE
        assert sorted(g["rank_in_group"]) == list(range(1, len(g) + 1))
        for i in range(len(g)):
            for j in range(i + 1, len(g)):
                d = C.km_between(g["lat"].iloc[i], g["lon"].iloc[i], g["lat"].iloc[j], g["lon"].iloc[j])
                assert d >= C.CAND_MIN_SEP_KM, (g["sub"].iloc[i], g["sub"].iloc[j], d)
    # measured stations are listed before interpolated ones in each group
    for _, g in reg.groupby(["region", "side"]):
        src = list(g.sort_values("rank_in_group")["basis_source"])
        assert src == sorted(src, key=lambda x: x != "measured")
    assert (reg["screen_score"].between(0, 100)).all()
    ex = c[c["role"] == "existing"]
    sc = _read("site_candidates.csv")
    assert set(ex["sub"]) == set(sc.loc[sc["role"] == "candidate", "sub"]) and len(ex) == 40


def test_upgrade_reach_matches_phase6_and_flags_unidentified_elements():
    r = _read("regional_upgrade_reach.csv").set_index("upgrade_id")
    rr = _read("upgrade_rerank.csv")
    # R2: with R1's structural rows installed in nodal_lanes, Phase 6 also evaluates U04 (WESTEX) and U08 (Bell County-Salado) with the full valuation
    # (the table of R1 said first_order for them); every upgrade that the rerank evaluates must be reported as phase6_full with the rerank's own counts
    evaluated = [u for u in r.index if (rr["scenario"] == u).any() and rr.loc[rr["scenario"] == u, "scenario_effect"].iloc[0] == "evaluated"]
    assert {"U01", "U03", "U05", "U06", "U09", "U10"} <= set(evaluated)
    for u in evaluated:
        d = rr[rr["scenario"] == u]["delta_usd_kw_yr"].abs()
        assert r.at[u, "n_sites_above_1usd"] == int((d > 1).sum()) and r.at[u, "evaluated_by"] == "phase6_full", u
    t = _read("sf_structural_targets.csv").set_index("target")
    assert r.at["U02", "evaluated_by"] == "none" and r.at["U07", "evaluated_by"] == "none" and not r.at["U02", "moves_a_site"]
    assert t.at["VEALMOOR_KOCH", "status"] == "not_identified"
    assert all(r.at[u, "evaluated_by"] in ("first_order", "phase6_full") for u in ("U04", "U08"))
    assert list(r.index) == [f"U{i:02d}" for i in range(1, 11)]



# ---------------------------------------------------------------------------------------------------- hand-off to Phase 3b
def test_structural_rows_feed_nodal_lanes_sub_sf_matrix():
    """Phase 3b's own reader, pointed at the merged substation file, returns our shift factors (checked against the parquet) for priced stations; the zone table is faked
    so that nothing of Phase 3b is recomputed or overwritten."""
    from gridsite import nodal_lanes as L

    extra = C.structural_replay_rows()
    zrows = pd.read_parquet(C.ZONE_SF_PLUS)
    zrows = zrows[zrows["method"] == "dam60_factor"]
    t = pd.read_parquet(C.SF_STRUCTURAL_PARQUET)
    st = t[(t["level"] == "station") & (t["method"] == "dam60_factor")]
    subs = list(st.sort_values("sf_pole").drop_duplicates("sub")["sub"].iloc[[0, -1]])
    old_sub, old_zone = L.SF_SUB, L.zone_sf
    try:
        L.SF_SUB = C.SF_SUB_PLUS
        L.zone_sf = lambda refresh=False: zrows
        M = L.sub_sf_matrix(subs, extra)
    finally:
        L.SF_SUB, L.zone_sf = old_sub, old_zone
    assert M["SF"].shape == (2, len(extra)) and np.abs(M["SF"]).max() > 0.2
    for i, s_ in enumerate(subs):
        for k, cid in enumerate(extra["cid"]):
            g = st[(st["sub"] == s_) & (st["cid"] == cid)]
            want = float(np.clip(g["sf"].iloc[0], -1, 1)) if len(g) else 0.0
            assert abs(M["SF"][i, k] - want) < 1e-5, (s_, cid, M["SF"][i, k], want)
    assert (np.abs(M["ZSF"]).max(0) > 0).any(), "zone shift factors of the new constraints are read"


def test_install_into_nodal_lanes_wraps_without_touching_files():
    """With Phase 3b's structure and zone table replaced by stubs, the patch appends our constraint rows, drops the replaced ones and repoints SF_SUB; everything is restored."""
    from gridsite import nodal_lanes as L

    extra = C.structural_replay_rows()
    replaced = C.structural_replaced_cids()
    assert "NELRIO|BASE CASE" in replaced and "E_PASP|BASE CASE" in replaced, "the phase 2 day-ahead fits of NELRIO and E_PASP are replaced"
    stub = pd.DataFrame(columns=list(extra.columns))
    stub.loc[0] = extra.iloc[0]
    stub.loc[0, "cid"] = "NELRIO|BASE CASE"
    stub.loc[1] = extra.iloc[0]
    stub.loc[1, "cid"] = "SOME_OTHER|X"
    z0 = pd.DataFrame({"cid": ["SOME_OTHER|X"], "method": ["rt_reg"], "zone": ["LZ_WEST"], "sf": [0.1], "bus_mean": [0.0], "r2_zone": [0.99], "source": ["direct"]})
    saved = (L.SF_SUB, L.zone_sf, L.constraint_structure, getattr(L, "_p7_installed", None))
    try:
        L.zone_sf = lambda refresh=False: z0
        L.constraint_structure = lambda *a, **k: stub.copy()
        if hasattr(L, "_p7_installed"):
            del L._p7_installed
        info = C.install_into_nodal_lanes()
        assert info["n_added"] == len(extra) and L.SF_SUB == C.SF_SUB_PLUS
        s = L.constraint_structure()
        assert set(s["cid"]) == set(extra["cid"]) | {"SOME_OTHER|X"} and s["cid"].is_unique
        z = L.zone_sf()
        assert (z["method"] == "dam60_factor").sum() == 8 * len(extra) and (z["cid"] == "SOME_OTHER|X").sum() == 1
        assert C.install_into_nodal_lanes() == {"already": True}
    finally:
        L.SF_SUB, L.zone_sf, L.constraint_structure = saved[:3]
        if hasattr(L, "_p7_installed"):
            del L._p7_installed
        if saved[3] is not None:
            L._p7_installed = saved[3]
        L.mu_tilde.cache_clear()


# ---------------------------------------------------------------------------------------------------- labels and the command registry
def test_label_sidecars_cover_every_column_of_my_tables():
    for name in MY_TABLES:
        res = check_labels.check_table(DATA / f"{name}.csv")
        assert res["ok"], (name, res)
    assert check_labels.main(["--quiet"]) == 0


def test_commands_are_registered_and_do_not_collide():
    sys.path.insert(0, str(GRID_DIR))
    import importlib

    owners = {}
    for f in sorted((GRID_DIR / "gridsite").glob("cli_*.py")):
        mod = importlib.import_module(f"gridsite.{f.stem}")
        for k in getattr(mod, "COMMANDS", {}):
            owners.setdefault(k, []).append(f.stem)
    mine = ("regional-basis", "sf-structural", "regional-candidates")
    assert all(owners.get(k) == ["cli_7"] for k in mine), {k: owners.get(k) for k in mine}     # (earlier phases register `map` twice; not ours)


if __name__ == "__main__":
    run_module(globals())
