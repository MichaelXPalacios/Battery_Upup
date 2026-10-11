"""Phase 3b tests: nodal lanes, candidate screening, scale factors, value_sites, portfolio caps and the output tables.

Run:  python tests/test_3b.py [name-substring ...]      (the research venv has no pytest; the tiny runner below prints PASS / SKIP / FAIL)
Tests that need results that do not exist yet (dispatch, forward, portfolio, sites) print SKIP, never PASS. After a full build every test must PASS.
"""
import sys
import time
import traceback
from pathlib import Path

GRID_DIR = Path(__file__).resolve().parents[1]
if str(GRID_DIR) not in sys.path:
    sys.path.insert(0, str(GRID_DIR))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from gridsite import nodal_lanes as L  # noqa: E402
from gridsite import saturation as SA  # noqa: E402
from gridsite import sites as S  # noqa: E402


class Skip(Exception):
    pass


def _need(path):
    if not Path(path).exists():
        raise Skip(f"{Path(path).name} not built yet")


def run_module(ns, argv=None):
    argv = sys.argv[1:] if argv is None else argv
    tests = [(k, v) for k, v in ns.items() if k.startswith("test_") and callable(v)]
    if argv:
        tests = [(k, v) for k, v in tests if any(a in k for a in argv)]
    failed, skipped, t_all = [], [], time.time()
    for name, fn in tests:
        t0 = time.time()
        try:
            fn()
            print(f"PASS  {name}  ({time.time() - t0:.1f}s)", flush=True)
        except Skip as ex:
            skipped.append(name)
            print(f"SKIP  {name}  ({ex})", flush=True)
        except Exception:
            failed.append(name)
            print(f"FAIL  {name}  ({time.time() - t0:.1f}s)", flush=True)
            traceback.print_exc()
    print(f"{len(tests) - len(failed) - len(skipped)}/{len(tests)} passed, {len(skipped)} skipped, {len(failed)} failed in {time.time() - t_all:.1f}s", flush=True)
    if failed:
        print("FAILED:", ", ".join(failed))
        sys.exit(1)


# ------------------------------------------------------------------------------------------------ price lanes
def test_controls_are_the_arena_zone_prices():
    from arena.data import Market

    m = Market("ERCOT")
    sel = np.where(m.dates >= L.LANE_START)[0]
    h = L.price_history()
    assert np.array_equal(m.P[sel], h["P_zone"]), "zone lanes differ from the arena's Market"
    assert np.array_equal(m.ref[sel], h["ref"])
    z = S.ensure_lanes()
    assert z["lanes"][:8] == L.ZONES
    assert np.array_equal(z["P"][:, :8, :].astype(np.float32), m.P[sel].astype(np.float32)), "saved control lanes differ from the arena zone prices"


def test_lane_file_matches_its_key():
    """The stored lane file carries the key of what it was built from, and the key reacts to the inputs it must react to (so ensure_lanes would rebuild)."""
    subs = S.lane_subs()                                                       # R2: the lane file also holds the regional and left-out lanes
    meta = L.lane_file_meta(S.LANE_TAG)                                        # read as stored: no rebuild on a mismatch
    now = L.lane_signature(subs)
    assert meta["sig"] == now, "the stored lane file was built with other constants / structure / prices / shift-factor files"
    assert meta["driver_rule"] == L.DRIVER_RULE
    real_zone, real_sf, real_mu = L.zone_of_substations, L.SF_SUB, L.mu_tilde
    try:
        L.zone_of_substations = lambda: {**real_zone(), subs[0]: "LZ_SOUTH"}
        assert L.lane_signature(subs) != now, "a changed zone of a substation must change the key"
        L.zone_of_substations = real_zone
        L.SF_SUB = L.ZONE_SF_FILE                                              # another file, another stamp
        assert L.lane_signature(subs) != now, "a changed shift-factor file must change the key"
        L.SF_SUB = real_sf

        def flipped(*a, **k):
            st, Mu, stats = real_mu(*a, **k)
            st = st.copy()
            st.loc[st.index[0], "sf_method"] = "dam_reg" if st.at[st.index[0], "sf_method"] == "rt_reg" else "rt_reg"
            return st, Mu, stats

        L.mu_tilde = flipped
        assert L.lane_signature(subs) != now, "a changed shift-factor method must change the key"
    finally:
        L.zone_of_substations, L.SF_SUB, L.mu_tilde = real_zone, real_sf, real_mu
    assert L.lane_signature(subs) == now


def test_zone_sf_uses_the_phase2_operator():
    """Applying our zone-regression recipe to bus columns reproduces phase 2's published SFs (so SF_zone is on the same footing as SF)."""
    import json

    import pyarrow.parquet as pq

    from gridsite import shiftfactors as SF

    mk = SF.load_market("rt")
    X = SF.design(mk, SF.MIN_BINDING_RUNS)
    cids = list(X.columns)
    cons0 = pd.read_csv(SF.CONSTRAINTS_CSV, keep_default_na=False)
    focal = set(cons0.sort_values("priority_rank").head(SF.PRIORITY_TOP_SF)["cid"])
    summary = json.loads((L.DERIVED / "p2_summary.json").read_text())
    pen = np.where(np.array([c in focal for c in cids]), SF.RELAX_ALPHA, float(summary["rt"]["ridge_lambda"]))
    sf = pq.read_table(L.SF_LONG, filters=[("cid", "=", "6437__F|DMTSCOS5"), ("method", "=", "rt_reg")]).to_pandas()
    buses = [str(b) for b in sf["bus"].astype(str).iloc[::4000][:4]]
    j = mk.buses.get_indexer(buses)
    beta = SF.ridge_solve(X.to_numpy(float), np.asarray(mk.cong[:, j]), np.ones(len(mk.ts), bool), pen)
    mine = -beta[cids.index("6437__F|DMTSCOS5")]
    ref = sf.set_index(sf["bus"].astype(str))["sf"].reindex(buses).to_numpy(float)
    assert np.abs(mine - ref).max() < 1e-5, (mine, ref)


def test_zone_sf_matches_bus_mean():
    zs = L.zone_sf()
    assert set(zs["zone"]) == set(L.ZONES)
    for m in ("rt_reg", "dam_reg"):
        g = zs[zs["method"] == m]
        assert g["r2_zone"].min() > L.ZONE_SF_MIN_R2
        assert np.corrcoef(g["sf"], g["bus_mean"])[0, 1] > 0.9


def test_weak_substation_sf_is_not_zeroed():
    """v1 zeroed 'weak' node SFs but not SF_zone, which left DSF = -SF_zone (an artefact). Raw SFs must come through unchanged; only 'implausible' is read as zero."""
    import pyarrow.parquet as pq

    struct, _, _ = L.mu_tilde()
    ss = pq.read_table(L.SF_SUB, filters=[("cid", "in", list(struct["cid"][:40]))]).to_pandas()
    ss = ss[ss["method"] == ss["cid"].map(dict(zip(struct["cid"], struct["sf_method"])))]
    weak = ss[ss["quality"].astype(str).str.split("+").apply(lambda t: "weak" in t) & (ss["sf"].abs() > 0.01)]
    assert len(weak) > 0
    r = weak.iloc[0]
    M = L.sub_sf_matrix([r["sub"]], struct)
    k = list(struct["cid"]).index(r["cid"])
    assert abs(M["SF"][0, k] - float(np.clip(r["sf"], -L.SF_ABS_CAP, L.SF_ABS_CAP))) < 1e-9, "a weak SF was altered"
    assert "weak" not in L.SF_BAD_QUALITY


def test_structure_and_mu_tilde_hit_their_targets():
    struct, Mu, st = L.mu_tilde()
    assert Mu.shape == (len(L.price_history()["dates"]) * L.NI, len(struct))
    t = st[st["target_usd_mw"] > 0]
    assert len(t) == len(st), "a constraint-year was skipped (too few binding intervals)"
    assert ((t["realised_usd_mw"] / t["target_usd_mw"] - 1).abs() < 1e-6).all()
    assert (Mu.data >= 0).all()
    caps = struct["cap_usd_mwh"].to_numpy()
    assert (Mu.max(axis=0).toarray().ravel() <= caps + 1e-6).all()
    h = L.price_history()
    yrs = np.array([int(d[:4]) for d in h["dates"]]).repeat(L.NI)
    for r in struct[struct["sph_basis"] == "imm_rent_alloc"].itertuples():
        k = struct.index[struct["cid"] == r.cid][0]
        rent = Mu[:, k].toarray().ravel()[yrs == 2025].sum() * L.DT_H * r.median_limit_mw / 1e6
        assert abs(rent - r.annual_rent_alloc_musd) < 1e-6, (r.cid, rent, r.annual_rent_alloc_musd)


def test_an_element_binds_no_longer_than_its_imm_hours():
    """v1 gave every contingency cid of an IMM element its own driver, so the element bound twice as long as the IMM figure (Hargrove 1,851 h vs 945). One driver per element,
    nested binding sets: the union over the cids is the IMM hours."""
    struct, Mu, _ = L.mu_tilde()
    assert struct.groupby("constraint_name")["driver_location"].nunique().max() == 1
    assert struct.groupby("constraint_name")["driver_sign"].nunique().max() == 1
    yrs = np.array([int(d[:4]) for d in L.price_history()["dates"]]).repeat(L.NI)
    for el, imm in (("HARGRO_TWINBU1_1", 945.3), ("6437__F", 1099.1)):
        ks = list(struct.index[struct["constraint_name"] == el])
        union_h = (Mu[:, ks].tocsc().getnnz(axis=1) > 0)[yrs == 2025].sum() * L.DT_H
        assert abs(union_h - imm) < 1.0, (el, union_h, imm)
    allc = L.load_constraints().drop_duplicates("constraint_name").set_index("constraint_name")
    k = float(struct["k_hours_per_musd"].iloc[0])
    n_imm = n_rent = 0
    for el, g in struct.groupby("constraint_name"):                            # every element anchored on the IMM: never longer than its figure
        ks = list(g.index)
        union_h = (Mu[:, ks].tocsc().getnnz(axis=1) > 0)[yrs == 2025].sum() * L.DT_H
        if (g["H_basis"] == "imm_hours").any():
            n_imm += 1
            assert abs(union_h - g.loc[g["H_basis"] == "imm_hours", "H_hours"].max()) < 1.0, (el, union_h)
        elif (g["H_basis"] == "imm_rent_x_hours_per_musd").any():
            n_rent += 1
            assert union_h <= float(allc.at[el, "annual_rent_2025_musd"]) * k + 1.0, (el, union_h)      # may be shorter when the rent is split over several cids (caveat in the notes)
        elif g["H_basis"].astype(str).str.startswith("imm2026").any():                                       # R2: an element re-anchored on its IMM 2026 rent binds as long as its 2026 hours (count or rent-based)
            n_rent += 1
            assert union_h <= float(g["H_hours"].max()) + 1.0, (el, union_h)
    assert n_imm >= 2 and n_rent >= 1, (n_imm, n_rent)


def test_hours_per_musd_is_rt_only_and_matches_phase4():
    c = L.usable_table()
    sph = L.shadow_price_hours(c)
    _, k = L.annual_binding_hours(c, sph)
    allc = L.load_constraints()
    both = allc[(allc["annual_rent_alloc_musd"].fillna(0) > 0) & allc["annual_binding_hours"].notna()].drop_duplicates("constraint_name")
    rt = both[both["annual_rent_market"] == "RT"]
    assert len(rt) < len(both), "the table has no non-RT element: the RT-only rule would be vacuous"
    assert abs(k - float((rt["annual_binding_hours"] / rt["annual_rent_2025_musd"]).median())) < 1e-9, "k must be the median over RT elements only"
    assert 8.0 < k < 9.0, k
    from gridsite import upgrades as U                                          # agent 4's module is part of this study: an import error is a failure

    assert abs(k - U.imm_hours_per_musd()[0]) < 0.05, (k, U.imm_hours_per_musd())
    sp = U.sph_table().set_index("cid")["sph_usd_mw_yr"]
    struct, _, _ = L.mu_tilde()
    p3 = struct[struct["sf_method"] != "dam60_factor"]                                                       # R2: R1's structural rows are not in Phase 4's table (pole-scale shift factors, own targets)
    a, b = p3["sph_usd_mw_yr"].to_numpy(), sp.reindex(p3["cid"]).to_numpy()
    anch = p3["sph_basis"].astype(str).str.startswith("window_imm2026").to_numpy()                          # R2: an element re-anchored on its 2026 rent splits it over the cids of each table (0.02% apart for Samsw-Vensw)
    assert len(p3) >= 250 and np.allclose(a[~anch], b[~anch], rtol=1e-9) and np.allclose(a[anch], b[anch], rtol=1e-3), "shadow-price-hours differ from phase 4"


def test_binding_set_is_the_top_of_the_driver():
    struct, Mu, st = L.mu_tilde()
    h = L.price_history()
    yrs = np.array([int(d[:4]) for d in h["dates"]]).repeat(L.NI)
    for year in (2023, 2024, 2026):                         # a normal, a leap and the partial year
        for k in (0, 5, 100, 200):
            r = struct.iloc[k]
            d = h["spread"][r["driver_location"]].reshape(-1) * int(r["driver_sign"])
            col = Mu[:, k].toarray().ravel()
            m = yrs == year
            bind = col[m] > 0
            frac = m.sum() / L.NI / float(pd.Timestamp(f"{year}-12-31").dayofyear)
            n = int(round(L.INTERVALS_PER_HOUR * r["H_hours"] * frac))
            assert abs(int(bind.sum()) - n) <= 1, (year, r["cid"], bind.sum(), n)
            assert d[m][bind].min() >= d[m][~bind].max() - 1e-9, "a non-binding interval has a higher driver than a binding one"


def test_node_price_identities_and_sign():
    struct, Mu, _ = L.mu_tilde()
    h = L.price_history()
    K = len(struct)
    zi = {z: k for k, z in enumerate(L.ZONES)}
    P0 = L.node_prices(np.zeros((1, K)), ["LZ_WEST"], Mu, h)                  # a node with the zone's own sensitivity has exactly the zone price
    assert np.array_equal(P0[:, 0, :], h["P_zone"][:, zi["LZ_WEST"], :])
    d = np.zeros((1, K))
    d[0, 0] = -1.0                                                            # import side: price above the zone exactly when the constraint binds, by mu
    P1 = L.node_prices(d, ["LZ_WEST"], Mu, h)[:, 0, :].reshape(-1)
    zp = h["P_zone"][:, zi["LZ_WEST"], :].reshape(-1)
    mu = Mu[:, 0].toarray().ravel()
    ok = (zp + mu <= L.PRICE_CAP) & (zp + mu >= L.PRICE_FLOOR)
    assert np.allclose((P1 - zp)[ok], mu[ok], atol=1e-9)
    P2 = L.node_prices(-d, ["LZ_WEST"], Mu, h)[:, 0, :].reshape(-1)           # export side: below the zone by mu
    ok2 = (zp - mu >= L.PRICE_FLOOR) & (zp - mu <= L.PRICE_CAP)
    assert np.allclose((zp - P2)[ok2], mu[ok2], atol=1e-9)
    P3 = L.node_prices(d * 3, ["LZ_WEST"], Mu, h, mu_scale=np.zeros(K))       # a relief scale of 0 gives the zone price
    assert np.array_equal(P3[:, 0, :], h["P_zone"][:, zi["LZ_WEST"], :])


def test_basis_never_pushes_a_price_beyond_the_cap_or_floor():
    z = S.ensure_lanes()
    h = L.price_history()
    zi = {k: i for i, k in enumerate(L.ZONES)}
    for j, (ln, zn) in enumerate(zip(z["lanes"], z["zones"])):
        pz = h["P_zone"][:, zi[zn], :]
        p = z["P"][:, j, :]
        assert (p <= np.maximum(pz, L.PRICE_CAP) + 1e-3).all() and (p >= np.minimum(pz, L.PRICE_FLOOR) - 1e-3).all(), ln


def test_runs_to_15_weights_by_overlap():
    """Five-minute runs on a 15-minute grid: the mean of three runs; a run that straddles an interval boundary is split by the seconds it covers."""
    q = 900 * 10**6
    t0 = 1_000_000 * q                                                        # an exact 15-minute boundary
    ts = t0 + np.array([0, 300, 600, 900, 1200, 1500], np.int64) * 10**6
    X = np.array([[1.0], [2.0], [6.0], [10.0], [10.0], [4.0]])
    start, M, cov = L._runs_to_15(ts, X)
    assert np.allclose(M[:, 0], [3.0, 8.0]) and np.allclose(cov, [900, 600 + 300]), (M, cov)
    ts2 = t0 + np.array([20, 320, 620, 920], np.int64) * 10**6                # runs start 20 s into the interval; the last run is 300 s
    _, M2, cov2 = L._runs_to_15(ts2, np.array([[1.0], [2.0], [6.0], [10.0]]))
    assert np.allclose(cov2, [880.0, 320.0]), cov2                             # 300 + 300 + 280 s in the first interval, 20 + 300 s in the second
    assert abs(M2[0, 0] - (1 * 300 + 2 * 300 + 6 * 280) / 880.0) < 1e-9 and abs(M2[1, 0] - (6 * 20 + 10 * 300) / 320.0) < 1e-9, M2


def test_window_rebuild_with_actual_shadow_prices():
    wc = L.window_check()
    s = L._fit_stats(wc["actual"], wc["rebuilt"])
    assert s["corr_pooled"] > 0.95 and s["mae_usd_mwh"] < 0.5 * s["mae_if_basis_were_zero_usd_mwh"], s
    assert s["total_corr_across_nodes"] > 0.95, s
    # the day-ahead market, where most of the replayed constraints were fitted
    dc = L.dam_window_check()
    s2 = L._fit_stats(dc["actual"], dc["rebuilt"])
    assert s2["corr_pooled"] > 0.9 and s2["mae_usd_mwh"] < 0.5 * s2["mae_if_basis_were_zero_usd_mwh"], s2


# ------------------------------------------------------------------------------------------------ drivers
def test_geographic_driver_follows_the_zone_shift_factor():
    z = np.zeros(len(L.ZONES))
    z[L.ZONES.index("LZ_WEST")] = -0.03                                       # binding raises the West price: an import element, driver +(HB_WEST - HB_BUSAVG)
    loc, sg = L.geographic_driver(z, [])
    assert (loc, sg) == ("HB_WEST", 1), (loc, sg)
    z[L.ZONES.index("LZ_WEST")] = 0.03                                        # binding lowers it: an export element
    assert L.geographic_driver(z, [])[1] == -1
    z[:] = 0.0                                                                # no zone-level signature: own zone (none known -> LZ_NORTH), the default sign
    loc, sg = L.geographic_driver(z, ["NOT_A_SUBSTATION"])
    assert (loc, sg) == (L.GEO_HUB["LZ_NORTH"], L.GEO_DEFAULT_SIGN)
    adm = L._admissible(np.array([0, 0, 0, -0.05, 0, 0, 0.02, 0.0]), ["LZ_WEST", "LZ_LCRA", "HB_WEST", "LZ_NORTH"])
    assert adm == [(0, 1), (2, 1)], adm                                        # West and its hub; LCRA is below half of the largest |SF_zone|


def test_best_driver_picks_the_signed_correlation():
    rng = np.random.default_rng(0)
    S_ = rng.normal(size=(300, 3))
    mu = np.maximum(-S_[:, 1] + 0.3 * rng.normal(size=300), 0)               # binds when location 1 is cheap
    j, sg, cr = L._best_driver(mu, S_, [(j, s) for j in range(3) for s in (1, -1)])
    assert (j, sg) == (1, -1) and cr > 0.5


# ------------------------------------------------------------------------------------------------ candidates
def test_candidate_rules():
    c = S.select_candidates()
    cand = c[c["role"] == "candidate"].reset_index(drop=True)
    assert len(cand) == S.N_CANDIDATES
    assert cand.groupby("dominant_element").size().max() <= S.MAX_PER_CONSTRAINT
    side = cand["dominant_side"].value_counts()
    assert side.get("import", 0) >= S.MIN_PER_SIDE and side.get("export", 0) >= S.MIN_PER_SIDE
    assert (cand["match_conf"] >= S.MIN_MATCH_CONF).all() and (cand["kv_max"] >= S.MIN_KV).all()
    assert cand["max_abs_sf_top_priority"].ge(S.SF_MIN_ELIGIBLE).all()
    assert cand["dominant_sf"].abs().ge(S.SF_MIN_ELIGIBLE).all(), "the dominant element must be coupled to the site"
    rf = S.read_csv("risk_flags.csv").set_index("sub")
    assert not (rf.loc[cand["sub"], "exclude_flag"] == True).any()           # noqa: E712
    assert c[c["role"] == "control_zone"]["sub"].tolist() == L.ZONES
    assert cand["score_usd_mw_yr"].is_monotonic_decreasing, "ranks must follow the score"
    import tempfile

    stored_path = S.CANDIDATES_CSV
    with tempfile.TemporaryDirectory() as td:                                  # recompute into a temp file: the deliverable is not touched
        S.CANDIDATES_CSV = Path(td) / "site_candidates.csv"
        try:
            again = S.select_candidates(refresh=True)
        finally:
            S.CANDIDATES_CSV = stored_path
    again = again[again["role"] == "candidate"].reset_index(drop=True)
    assert again["sub"].tolist() == cand["sub"].tolist(), "data/site_candidates.csv is out of date with the structure"
    assert np.allclose(again["score_usd_mw_yr"], cand["score_usd_mw_yr"], rtol=1e-9)


def test_substation_NA_survives_the_reads():
    s = S.read_csv("substations.csv")
    assert (s["sub"] == "NA").any(), "the ERCOT substation code NA was read as missing"
    assert s["sub"].notna().all()


# ------------------------------------------------------------------------------------------------ scale factors
def test_scale_factors():
    struct, _, _ = L.mu_tilde()
    assert np.array_equal(SA.scale_vector(2025, struct), np.ones(len(struct)))
    prev = np.ones(len(struct))
    for y in SA.FORWARD_YEARS:
        s = SA.scale_vector(y, struct)
        assert (s <= prev + 1e-12).all(), "scale factors must not rise with the year"
        assert (s > 0).all() and (s <= 1).all()
        prev = s
    sat = SA.saturation_scale(2031, struct)
    assert (sat >= SA.SAT_FLOOR - 1e-12).all()
    up = SA.upgrade_scale(2031, struct)
    assert (up >= min(SA.UPGRADE_RESIDUAL, SA.UPGRADE_RESIDUAL_GTC) - 1e-12).all()
    none = SA.storage_projects().iloc[:0]
    s0 = SA.saturation_scale(2031, struct, proj=none, sf={"SF": np.zeros((1, len(struct))), "subs": ["X"]})
    assert (s0 == 1).all()


def test_storage_projects_keep_the_eia_rows():
    """v1 parsed the mixed 'YYYY-MM-DD' / 'YYYY-MM' dates with one inferred format, which turned every EIA date into NaT and dropped the 2026 batteries."""
    proj = SA.storage_projects()
    assert proj["eff_cod"].notna().all() and proj["cod"].notna().all()
    built = proj[proj["built"]]
    assert len(built) > 40 and built["mw"].sum() > 3000, (len(built), built["mw"].sum())
    assert (built["cod"] >= pd.Timestamp(f"{SA.BASE_YEAR + 1}-01-01")).all()
    q = pd.read_csv(S.REPO_DATA / "queue_sub_map.csv", keep_default_na=False, na_values=[""], low_memory=False)
    tap = q[q["sub_alt"].astype(str).str.contains(";") & (q["stage"] == "IA_signed") & (q["conf"] >= SA.SAT_MIN_CONF)]
    if len(tap):
        r = tap.iloc[0]
        rows = proj[proj["project"] == r["inr_or_plant"]]
        assert abs(rows["mw"].sum() - r["mw"]) < 1e-6, "a multi-station tap must keep all of its MW"
        assert len(rows) >= 2


def test_saturation_relief_by_hand():
    """Two cids with the same limit, one anchored on the 2025 IMM rent and one on the Oct 2026 window; one built (2026) and one future project at the same station."""
    struct = pd.DataFrame({"cid": ["a", "b"], "sph_basis": ["imm_rent_alloc", "window_rt"], "median_limit_mw": [100.0, 100.0]})
    proj = pd.DataFrame({"sub": ["X", "X"], "mw": [40.0, 50.0], "built": [True, False], "realisation": [1.0, 0.5],
                         "eff_cod": [pd.Timestamp("2026-03-01"), pd.Timestamp("2027-01-01")]})
    sf = {"SF": np.array([[-0.5, -0.5]]), "subs": ["X"]}
    s2031 = SA.saturation_scale(2031, struct, proj=proj, sf=sf, slope=1.0, floor=0.0)
    # a (IMM): both projects count: (40 + 50 x 0.5) x 0.5 / 100 = 0.325 -> 0.675; b (window): only the future project: 25 x 0.5 / 100 = 0.125 -> 0.875
    assert abs(s2031["a"] - 0.675) < 1e-9 and abs(s2031["b"] - 0.875) < 1e-9, s2031.to_dict()
    sf2 = {"SF": np.array([[0.5, 0.5]]), "subs": ["X"]}                           # export side: not counted by default
    assert (SA.saturation_scale(2031, struct, proj=proj, sf=sf2) == 1.0).all()
    assert (SA.saturation_scale(2031, struct, proj=proj, sf=sf2, sides=("import", "export"), floor=0.0) < 1.0).all()


def test_upgrade_clock_by_hand():
    struct, _, _ = L.mu_tilde()
    cid = struct["cid"].iloc[0]
    sched = pd.DataFrame({"cid": [cid], "isd": [pd.Timestamp("2027-07-01")], "residual": [0.1], "conf": [0.9], "project_id": ["x"], "element_type": ["line"]})
    f = SA.upgrade_scale(2027, struct, sched).loc[cid]
    assert abs(f - (1 - (183 / 365.0) * 0.9)) < 5e-3, f          # half a year of relief to a residual of 0.1
    assert SA.upgrade_scale(2026, struct, sched).loc[cid] == 1.0
    assert abs(SA.upgrade_scale(2028, struct, sched).loc[cid] - 0.1) < 1e-12
    old = pd.DataFrame({"cid": [cid], "isd": [pd.Timestamp("2025-04-01")], "residual": [0.1], "conf": [0.9], "project_id": ["x"], "element_type": ["line"]})
    assert SA.upgrade_scale(2031, struct, old).loc[cid] == 1.0, "a project already in service in the base year is in the 2025 data"
    sched_real, src = SA.planned_upgrades(struct)
    assert len(sched_real) > 20 and src
    st = struct.copy()                                                          # a project in service by AS_OF is already in a window-anchored target, not in an IMM-anchored one
    c1, c2 = st["cid"].iloc[0], st["cid"].iloc[1]
    st.loc[st["cid"] == c1, "sph_basis"] = "window_rt"
    st.loc[st["cid"] == c2, "sph_basis"] = "imm_rent_alloc"
    mid = pd.DataFrame({"cid": [c1, c2], "isd": [pd.Timestamp("2026-07-28")] * 2, "residual": [0.1, 0.1], "conf": [0.9, 0.9], "project_id": ["x", "y"], "element_type": ["line", "line"]})
    u = SA.upgrade_scale(2031, st, mid)
    assert u.loc[c1] == 1.0 and abs(u.loc[c2] - 0.1) < 1e-12, (u.loc[c1], u.loc[c2])
    late = mid.assign(isd=pd.Timestamp("2027-03-01"))                           # after AS_OF both are relieved
    u2 = SA.upgrade_scale(2031, st, late)
    assert abs(u2.loc[c1] - 0.1) < 1e-12 and abs(u2.loc[c2] - 0.1) < 1e-12


def test_upgrade_residuals_agree_with_phase4():
    try:
        from gridsite import upgrades as U
    except ImportError:
        raise Skip("gridsite.upgrades not importable")
    struct, _, _ = L.mu_tilde()
    sched, src = SA.planned_upgrades(struct)
    got = U.apply_upgrade(list(sched["cid"].iloc[:5]), expand_element=False)
    for cid, sc in got.items():
        assert abs(sc - float(sched.set_index("cid").at[cid, "residual"])) < 1e-12, (cid, sc)


# ------------------------------------------------------------------------------------------------ dispatch tables
def test_site_years_table_by_hand():
    from gridsite import runner

    dates = np.array([str(d.date()) for d in pd.date_range("2025-01-01", "2026-01-30")])
    D = len(dates)
    lanes = ["LZ_WEST", "KNAPP"]
    base = np.stack([np.full(D, 2.0), np.full(D, 5.0)], 1)                    # $ per MW per day
    algos = {a: {"profit": base * (i + 1), "energy": base * (i + 1) + 1.0, "wear": np.full((D, 2), 1.0), "mwh": np.full((D, 2), 2.0)} for i, a in enumerate(S.ALGOS)}
    res = runner.LaneResults(lanes, dates, "grid", "lane", algos, np.zeros((D, 2), np.int8))
    orc = base * 10.0
    cand = pd.DataFrame({"sub": ["LZ_WEST", "KNAPP"], "role": ["control_zone", "candidate"], "rank": [0, 1]})
    t = S.site_years_table(res, orc, "grid", cand, {"lanes": lanes, "zones": ["LZ_WEST", "LZ_WEST"]})
    y25 = t[(t["lane"] == "KNAPP") & (t["period"].astype(str) == "2025")].iloc[0]
    assert abs(y25["usd_kw_yr_A1"] - 5.0 * 365 / 1000) < 1e-9 and abs(y25["usd_kw_yr_A5"] - 25.0 * 365 / 1000) < 1e-9
    assert abs(y25["usd_kw_yr_oracle"] - 50.0 * 365 / 1000) < 1e-9
    assert abs(y25["uplift_usd_kw_yr_A5"] - (25.0 - 10.0) * 365 / 1000) < 1e-9
    assert abs(y25["usd_per_mwh_A5"] - 25.0 / 2.0) < 1e-9 and abs(y25["a5_over_oracle"] - 0.5) < 1e-9
    y26 = t[(t["lane"] == "KNAPP") & (t["period"].astype(str) == "2026")].iloc[0]
    assert y26["partial_year"] and y26["days"] == 30 and abs(y26["usd_kw_yr_A1"] - 5.0 * 365 / 1000) < 1e-9, "a 30-day year is annualised to 365 days"


def test_half_life_table_by_hand():
    rows = []
    for lane, vals in (("A", [10, 10, 9, 6, 4, 3, 2]), ("B", [10, 10, 10, 10, 10, 10, 10]), ("C", [-1, 0, 0, 0, 0, 0, 0])):
        for y, v in zip(range(2025, 2032), vals):
            rows.append({"lane": lane, "year": y, "baseline": "both", "uplift_learner_usd_kw_yr": float(v)})
    hl = S.half_life_table(pd.DataFrame(rows)).set_index("lane")
    assert hl.at["A", "half_life_year"] == "2029" and hl.at["B", "half_life_year"] == "> 2031" and hl.at["C", "half_life_year"].startswith("n/a")


def test_group_cap_is_whole_units_and_reported_as_such():
    assert S.group_cap_units(151.0) == 1 and S.group_cap_units(231.0) == 2 and S.group_cap_units(98.6) == 1 and S.group_cap_units(1317.0) == 13
    from arena import siting

    vals = np.array([[1.0, 2.0, 3.0]] * 4)                                    # a 1.5-unit group takes one unit: the effective cap is the floor
    p = siting.portfolio("A5", vals, vals, 1, 3, 5, None, None, np.random.default_rng(1), groups=[(np.array([0, 1, 2]), 1.5)])
    assert p["fleet"][-1].sum() == 1
    cand = S.select_candidates()
    gi = S.portfolio_groups(cand[cand["role"] == "candidate"].sort_values("rank").reset_index(drop=True))["group_info"]
    assert (gi["group_cap_mw"] % S.UNIT_MW == 0).all()
    assert all(abs(cap - S.group_cap_units(lim) * S.UNIT_MW) < 1e-9 for cap, lim in zip(gi["group_cap_mw"], gi["limit_mw"]))


def test_value_sites_matches_an_independent_oracle():
    """Rebuild one lane's 2025 prices straight from the structure, take the oracle with the runner, and compare with value_sites (no shared cache or code path)."""
    from gridsite import runner

    z = S.ensure_lanes()
    sub = z["lanes"][8]
    struct, Mu, _ = L.mu_tilde()
    M = L.sub_sf_matrix([sub], struct)
    hist = L.price_history()
    idx = [i for i, d in enumerate(hist["dates"]) if d.startswith("2025")]
    P = L.node_prices(M["DSF"], M["zone"], Mu, hist, day_slice=slice(idx[0], idx[-1] + 1))
    mine = float(runner.oracle_lanes(P, "grid", workers=1).sum() / 1000.0)
    v = S.value_sites([sub], spec="grid", years=(2025,), method="oracle")
    assert abs(v["oracle_usd_kw_yr"].iloc[0] - mine) < 1e-6, (v["oracle_usd_kw_yr"].iloc[0], mine)
    # the lane file holds the same prices (float32 storage)
    assert np.abs(z["P"][idx[0]:idx[-1] + 1, 8, :] - P[:, 0, :]).max() < 0.01


def test_value_sites_reproduces_the_dispatch_oracle():
    _need(S.SITE_YEARS_CSV)
    sy = S.read_csv("site_years.csv")
    sy = sy[(sy["spec"] == "grid") & (sy["period"].astype(str) == "2025")].set_index("lane")
    lanes = ["LZ_WEST", sy[sy["role"] == "candidate"].sort_values("rank").index[0]]
    v = S.value_sites(lanes, mu_scale=None, spec="grid", years=(2025,), method="oracle")
    for r in v.itertuples():
        assert abs(r.oracle_usd_kw_yr - sy.at[r.lane, "usd_kw_yr_oracle"]) < 5e-3, (r.lane, r.oracle_usd_kw_yr, sy.at[r.lane, "usd_kw_yr_oracle"])
    el = S.select_candidates().set_index("sub").at[lanes[1], "dominant_element"]       # relieving the dominant element lowers the value and the uplift
    v2 = S.value_sites([lanes[1]], mu_scale={el: 0.0}, spec="grid", years=(2025,), method="oracle")
    assert v2["uplift_oracle_usd_kw_yr"].iloc[0] < v["uplift_oracle_usd_kw_yr"].iloc[1]


def test_forward_table_matches_a_fresh_computation():
    _need(S.RUNS / "forward_values.parquet")
    from gridsite import runner

    fv = pd.read_parquet(S.RUNS / "forward_values.parquet")
    g = fv[(fv["spec"] == "grid") & (fv["baseline"] == "both") & (fv["role"] == "candidate") & (fv["year"] == 2029)].sort_values("lane").iloc[1]
    struct, Mu, _ = L.mu_tilde()
    M = L.sub_sf_matrix([g["lane"]], struct)
    hist = L.price_history()
    idx = [i for i, d in enumerate(hist["dates"]) if d.startswith("2025")]
    scale = SA.scale_vector(2029, struct)
    P = L.node_prices(M["DSF"], M["zone"], Mu, hist, mu_scale=scale, day_slice=slice(idx[0], idx[-1] + 1))
    fresh = float(runner.oracle_lanes(P, "grid", workers=1).sum() / 1000.0)
    assert abs(fresh - g["oracle_usd_kw_yr"]) < 1e-6, (fresh, g["oracle_usd_kw_yr"])
    ratios = S.dispatch_ratios("grid")
    assert abs(g["learner_usd_kw_yr"] - fresh * ratios.at[g["lane"], "ratio_A5"]) < 1e-6


def test_portfolio_respects_caps():
    _need(S.PORTFOLIO_CSV)
    p = pd.read_csv(S.PORTFOLIO_CSV, keep_default_na=False, na_values=[""])
    assert set(p["rule"]) == set(S.ALGOS) and set(p["year"]) <= set(S.PORTFOLIO_YEARS)
    assert (p["MW"] % S.UNIT_MW == 0).all()
    tot = p.groupby(["rule", "year"])["MW"].sum()
    assert len(tot) == len(S.ALGOS) * len(S.PORTFOLIO_YEARS) and (tot == S.UNITS_PER_YEAR * S.UNIT_MW).all(), "every unit must be placed (the zone fallback is uncapped)"
    nodes_p = p[p["option_type"] == "node"]
    last = nodes_p.sort_values("year").groupby(["rule", "sub"])["fleet_mw"].last()
    cand = S.select_candidates().set_index("sub")
    for (rule, sub), mw in last.items():
        assert mw <= S.POI_CAP_MW[S.poi_class(cand.at[sub, "kv_max"])] + 1e-9, (rule, sub, mw)
    inp = S.portfolio_inputs()
    nodes = inp["nodes"]
    for rule, g in nodes_p.groupby("rule"):
        fleet = g.sort_values("year").groupby("sub")["fleet_mw"].last().reindex(nodes).fillna(0).to_numpy()
        for idx, cap_u in inp["groups"]:
            assert fleet[idx].sum() <= cap_u * S.UNIT_MW + 1e-9, (rule, idx, cap_u)
    assert set(p[p["option_type"] == "zone_fallback"]["sub"]) <= set(L.ZONES)
    if S.SITES_CSV.exists():                                                   # the reported cap is the effective cap
        sites = pd.read_csv(S.SITES_CSV, keep_default_na=False, na_values=[""]).set_index("sub")
        gi = inp["group_info"].set_index("element")
        for sub, r in sites.iterrows():
            assert abs(r["price_taker_cap_mw"] - gi.at[r["dominant_element"], "group_cap_mw"]) < 1e-9


def test_sites_table_is_complete_and_consistent():
    _need(S.SITES_CSV)
    h = pd.read_csv(S.SITES_CSV, keep_default_na=False, na_values=[""])
    assert len(h) == S.N_CANDIDATES and h["sub"].is_unique
    for c in ("h2_mean2125_A5", "h2_y2025_A5", "h2_mean2125_oracle", "fwd_learner_2027", "fwd_learner_2029", "fwd_learner_2031", "basis_half_life", "conn_class",
              "reliability_need_score", "as_system_estimate_2h_2025"):
        assert h[c].notna().all(), c
    assert (h["h2_mean2125_A5"] <= h["h2_mean2125_oracle"]).all()
    assert h["h4_mean2425_A5"].notna().sum() == S.TOP4H
    sy = S.read_csv("site_years.csv")
    m = sy[(sy["spec"] == "grid") & (sy["period"].astype(str) == "mean_2021_25")].set_index("lane")
    assert np.allclose(h.set_index("sub")["h2_mean2125_A5"].round(3), m.loc[h["sub"], "usd_kw_yr_A5"].round(3), atol=1e-3)


def test_value_cache_keys_react_to_their_inputs():
    struct, Mu, _ = L.mu_tilde()
    hist = L.price_history()
    base = S._value_sigs("grid", struct, Mu, hist)
    s2 = struct.copy()
    s2.loc[s2.index[0], "driver_location"] = "HB_PAN" if s2.at[s2.index[0], "driver_location"] != "HB_PAN" else "HB_WEST"
    assert S._value_sigs("grid", s2, Mu, hist)[0] != base[0], "a changed driver moves the timing of the shadow prices"
    assert S._value_sigs("grid4h", struct, Mu, hist) != base, "another spec"
    spec = S._spec_def("grid")
    from arena import config as C

    try:
        C.SPECS["grid"]["rte"] = 0.8
        assert S._value_sigs("grid", struct, Mu, hist) != base, "the battery definition"
    finally:
        C.SPECS["grid"].clear()
        C.SPECS["grid"].update(spec)
    cap = L.PRICE_CAP
    try:
        L.PRICE_CAP = 1234.0
        assert S._value_sigs("grid", struct, Mu, hist)[0] != base[0], "the price clip is part of the lane"
    finally:
        L.PRICE_CAP = cap
    assert S._value_sigs("grid", struct, Mu, hist) == base


def test_forward_values_are_tied_to_their_lanes_and_ratios():
    _need(S.RUNS / "forward_values.parquet")
    S.check_provenance((S.SPEC_2H, S.SPEC_4H))
    real = S.dispatch_ratios
    try:
        S.dispatch_ratios = lambda spec: real(spec) * 1.01
        try:
            S.check_provenance((S.SPEC_2H,))
        except ValueError:
            pass
        else:
            raise AssertionError("forward values computed with other ratios must be refused")
    finally:
        S.dispatch_ratios = real


def test_learner_oracle_ratio_window():
    _need(S.SITE_YEARS_CSV)
    assert S.RATIO_PERIOD == {"grid": "mean_2023_25", "grid4h": "mean_2024_25"} and S.RATIO_YEARS == (2023, 2024, 2025)
    sy = S.read_csv("site_years.csv")
    for spec, years in (("grid", ("2023", "2024", "2025")), ("grid4h", ("2024", "2025"))):
        r = S.dispatch_ratios(spec)
        g = sy[(sy["spec"] == spec) & sy["period"].astype(str).isin(years)]
        for lane in ("LZ_WEST", "MIDW"):
            x = g[g["lane"] == lane]
            assert len(x) == len(years)
            for a in S.ALGOS:
                want = x[f"usd_kw_yr_{a}"].mean() / x["usd_kw_yr_oracle"].mean()
                assert abs(r.at[lane, f"ratio_{a}"] - want) < 2e-4, (spec, lane, a)          # site_years.csv is rounded to 4 decimals


def test_portfolio_history_and_forward_rows_line_up():
    _need(S.RUNS / "forward_values.parquet")
    inp = S.portfolio_inputs()
    sy, fv, options = inp["sy"], inp["fv"], inp["options"]
    assert S.HISTORY_YEARS == (2021, 2022, 2023, 2024, 2025, 2026) and S.PORTFOLIO_YEARS == (2027, 2028, 2029, 2030, 2031)
    for rule in S.ALGOS:
        h, f = inp["hist"][rule], inp["fwd"][rule]
        assert h.shape == (len(S.HISTORY_YEARS), len(options)) and f.shape == (len(S.PORTFOLIO_YEARS), len(options))
        assert np.isfinite(h).all() and np.isfinite(f).all()
        for i, y in enumerate(S.HISTORY_YEARS):
            d = sy[(sy["spec"] == "grid") & (sy["period"].astype(str) == str(y))].set_index("lane")
            assert np.allclose(h[i], d.loc[options, f"usd_kw_yr_{rule}"].to_numpy() * 1000.0), (rule, y)
        for j, y in enumerate(S.PORTFOLIO_YEARS):
            d = fv[(fv["spec"] == "grid") & (fv["baseline"] == "both") & (fv["year"] == y)].set_index("lane")
            assert np.allclose(f[j], d.loc[options, f"usd_kw_yr_{rule}"].to_numpy() * 1000.0), (rule, y)


def test_cost_columns_follow_their_formulas():
    _need(S.SITES_CSV)
    h = pd.read_csv(S.SITES_CSV, keep_default_na=False, na_values=[""])
    crf = S.crf()
    assert abs(crf - 0.08 * 1.08 ** 15 / (1.08 ** 15 - 1)) < 1e-12 and S.WACC == 0.08 and S.LIFE_YEARS == 15
    assert np.allclose(h["conn_usd_kw_yr"], crf * h["conn_all_in_usd_per_kw"], atol=2e-3)
    assert np.allclose(h["conn_all_in_usd_per_kw"], h["conn_all_in_musd"] * 1e6 / (S.REF_BATTERY_MW * 1000.0), atol=2e-3)
    assert np.allclose(h["net_of_connection_usd_kw_yr"], h["sat_adjusted_learner_2027_31_mean"] - h["conn_usd_kw_yr"], atol=2e-3)
    assert np.allclose(h["breakeven_installed_usd_per_kw"], h["sat_adjusted_learner_2027_31_mean"] / crf, atol=2e-3)
    assert np.allclose(h["breakeven_battery_only_usd_per_kw"], h["breakeven_installed_usd_per_kw"] - h["conn_all_in_usd_per_kw"], atol=2e-3)
    fv = pd.read_parquet(S.RUNS / "forward_values.parquet")
    f = fv[(fv["spec"] == "grid") & (fv["baseline"] == "both") & (fv["role"] == "candidate") & fv["year"].isin(S.PORTFOLIO_YEARS)]
    mean = f.groupby("lane")["learner_usd_kw_yr"].mean().reindex(h["sub"])
    assert np.allclose(h["sat_adjusted_learner_2027_31_mean"], mean.to_numpy(), atol=2e-3)


def test_tables_have_labels():
    seen = 0
    for f in (S.CANDIDATES_CSV, S.VALIDATION_CSV, S.SITE_YEARS_CSV, S.PORTFOLIO_CSV, S.SITES_CSV, L.STRUCT_FILE):
        if not Path(f).exists():
            continue
        seen += 1
        d = pd.read_csv(f, nrows=2, keep_default_na=False)
        lab = pd.read_csv(str(f).replace(".csv", ".labels.csv"), keep_default_na=False)
        assert list(lab["column"]) == list(d.columns), f.name
        assert set(lab["label"]) <= {"REAL", "DERIVED", "SIM", "ASSUMPTION"}, (f.name, set(lab["label"]))
    if seen < 6:
        raise Skip(f"only {seen} of 6 tables exist yet")


if __name__ == "__main__":
    run_module(globals())
