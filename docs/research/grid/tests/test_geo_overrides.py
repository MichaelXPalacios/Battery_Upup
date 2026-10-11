"""G1 geography overrides (data/geo_overrides.csv, gridsite/geo_overrides.py, the hook in geomatch.match_geo): the table is valid, the module applies it
as documented, it is idempotent, the hook survives a re-run of `match-geo`, and the committed placements still reflect it.

Run (from docs/research/grid):   python tests/test_geo_overrides.py [name-substring ...]
The research venv has no pytest; each test is a plain function and the module ends with the harness runner of test_3a.
The default run takes about 20 seconds. The re-run test (about 3 minutes, writes only to a temporary folder) is opt-in:
    python tests/test_geo_overrides.py rerun          (or set GEO_RERUN=1)
"""
import copy
import inspect
import os
import shutil
import sys
import tempfile
from pathlib import Path

GRID_DIR = Path(__file__).resolve().parents[1]
if str(GRID_DIR) not in sys.path:
    sys.path.insert(0, str(GRID_DIR))
if str(GRID_DIR / "tests") not in sys.path:
    sys.path.insert(0, str(GRID_DIR / "tests"))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from _harness_3a import run_module  # noqa: E402
from gridsite import check_labels, geo_overrides as GO, geomatch as GM, paths  # noqa: E402

DATA = paths.REPO_DATA
# Over-limit constraint pairs that the audit looked at and left alone, each with the reason (notes/phase7-geo.md section 3):
EXPLAINED_LONG_PAIRS = {
    frozenset(("COCS", "FTST")): "69 kV Trans-Pecos tie, 3 line hops / 113 km in the OSM-HIFLD graph",
    frozenset(("BELDTAP", "COCS")): "69 kV Trans-Pecos tie, 4 line hops / 137 km",
    frozenset(("ENTPR", "TRSES")): "345 kV, 2 line hops / 149 km; the 130 km limit is a Phase 2 ASSUMPTION",
    frozenset(("PALOUSE", "WOLFCAMP")): "138 kV Upton-Pecos tie, no mapped line; Palouse has independent county support, Wolfcamp is a 2025 TNMP station",
    frozenset(("MURRAY", "PAINTCRE")): "86 km against an 80 km limit; MURRAY was demoted to a hint (0.30)",
}


def _read(name):
    return pd.read_csv(DATA / name, keep_default_na=False, na_values=[""], low_memory=False)


def _write_table(rows, tmp):
    """Write a minimal override table with the real column order to a temp file and return its path."""
    base = dict(sub="X1", lat=31.0, lon=-100.0, county="TOM GREEN", method="alt_site", evidence="test", conf=0.7, replaces_method="osm_name",
                action="place", osm_id="", matched_name="t", old_county="", old_conf=0.2)
    df = pd.DataFrame([{**base, **r} for r in rows])[GO.COLUMNS]
    p = Path(tmp) / "ov.csv"
    df.to_csv(p, index=False)
    return p


def _fake_subs(names):
    return pd.DataFrame({"sub": names, "kv_set": [[138.0]] * len(names)}, index=names)


def _fake_res(sub, conf=0.8, lat=30.0, lon=-97.0, method="osm_name"):
    pick = dict(method=method, osm_id="w1", hifld_id="", ref_name="old", lat=lat, lon=lon, evidence="matcher")
    site = dict(lat=lat, lon=lon, conf=conf, methods=[method], members=[pick], pick=pick)
    return {sub: dict(sites=[site], chosen=0)}


# ---------------------------------------------------------------------------------------------------- the table
def test_table_is_valid_and_labelled():
    ov = GO.load_overrides()
    assert len(ov) >= 60, "the audit table should have its 70-odd rows"
    assert ov["sub"].is_unique and set(ov["action"]) <= set(GO.ACTIONS) and set(ov["method"]) <= set(GO.METHODS)
    side = check_labels.check_table(DATA / "geo_overrides.csv")
    assert side["ok"], side
    side2 = check_labels.check_table(DATA / "geo_changes.csv")
    assert side2["ok"], side2
    assert (ov["evidence"].str.len() > 40).all(), "every row carries a real reason"
    pl = ov[ov["action"] == "place"]
    assert pl["conf"].between(0.4, 0.9).all() and pl["osm_id"].notna().all()
    assert ov.loc[ov["action"] == "unplace", "lat"].isna().all()


def test_place_rows_are_where_they_say_and_in_the_county_they_name():
    ov = GO.load_overrides()
    pl = ov[ov["action"] == "place"].set_index("sub")
    osm = _read("osm_substations.csv").set_index("osm_id")
    cnt = GM.Counties()
    got = cnt.lookup(pl["lat"].to_numpy(), pl["lon"].to_numpy())
    assert list(got) == list(pl["county"]), "county column must equal the polygon lookup"
    for sub, r in pl.iterrows():
        assert r["osm_id"] in osm.index, f"{sub}: OSM node {r['osm_id']} is not in osm_substations.csv"
        o = osm.loc[r["osm_id"]]
        km = float(GM.haversine_km(r["lat"], r["lon"], o["lat"], o["lon"]))
        assert km <= 1.0, f"{sub}: pinned point is {km:.1f} km from its OSM node"


def test_malformed_tables_are_rejected():
    bad_cases = {
        "duplicate sub": [dict(sub="A"), dict(sub="A")],
        "bad action": [dict(action="delete")],
        "no evidence": [dict(evidence="")],
        "unknown method": [dict(method="vibes")],
        "outside Texas": [dict(lat=45.0)],
        "conf zero": [dict(conf=0.0)],
        "place without lat": [dict(lat=np.nan)],
    }
    with tempfile.TemporaryDirectory() as tmp:
        for name, rows in bad_cases.items():
            p = _write_table(rows, tmp)
            try:
                GO.load_overrides(p)
            except GO.OverrideError:
                continue
            raise AssertionError(f"{name}: a malformed table was accepted")
        p = _write_table([dict(sub="NA", action="place")], tmp)          # NA is a real substation code, not a missing value
        assert GO.load_overrides(p)["sub"].tolist() == ["NA"]


# ---------------------------------------------------------------------------------------------------- the module
def test_apply_place_unplace_demote_and_missing():
    subs = _fake_subs(["P", "U", "D", "Z"])
    res = {**_fake_res("P"), **_fake_res("U"), **_fake_res("D", conf=0.8)}
    with tempfile.TemporaryDirectory() as tmp:
        p = _write_table([dict(sub="P", conf=0.66, osm_id="w9", matched_name="Right Place"),
                          dict(sub="U", action="unplace", lat=np.nan, lon=np.nan, conf=np.nan, method="unplace_county"),
                          dict(sub="D", action="demote", lat=np.nan, lon=np.nan, conf=0.35, method="demote_block"),
                          dict(sub="GONE", conf=0.6)], tmp)
        out, st = GO.apply_overrides(res, subs, path=p)
    assert dict(st) == {"place": 1, "unplace": 1, "demote": 1, "missing": 1}, st
    site = out["P"]["sites"][out["P"]["chosen"]]
    assert (site["lat"], site["lon"], site["conf"]) == (31.0, -100.0, 0.66) and site["pick"]["method"] == "override" and site["pick"]["osm_id"] == "w9"
    assert site["pick"]["ref_name"] == "Right Place" and site["pick"]["evidence"].startswith("G1 override (alt_site)") and len(out["P"]["sites"]) == 1
    assert "U" not in out
    d = out["D"]["sites"][0]
    assert d["conf"] == 0.35 and d["pick"]["method"] == "osm_name" and d["pick"]["evidence"].startswith("G1 demoted") and (d["lat"], d["lon"]) == (30.0, -97.0)
    assert "GONE" not in out


def test_demote_never_raises_a_confidence():
    subs = _fake_subs(["D"])
    res = _fake_res("D", conf=0.2)
    with tempfile.TemporaryDirectory() as tmp:
        p = _write_table([dict(sub="D", action="demote", lat=np.nan, lon=np.nan, conf=0.35, method="demote_county")], tmp)
        out, _ = GO.apply_overrides(res, subs, path=p)
    assert out["D"]["sites"][0]["conf"] == 0.2


def test_link_callable_fills_a_missing_osm_id():
    subs = _fake_subs(["P"])
    seen = []

    def link(lat, lon, kv):
        seen.append((lat, lon, list(kv)))
        return "w777"

    with tempfile.TemporaryDirectory() as tmp:
        p = _write_table([dict(sub="P", osm_id="")], tmp)
        out, _ = GO.apply_overrides({}, subs, link=link, path=p)
    assert out["P"]["sites"][0]["pick"]["osm_id"] == "w777" and seen == [(31.0, -100.0, [138.0])]


def test_idempotent_and_pinned_through_the_neighbour_stage():
    subs = _fake_subs(["P", "U", "D", "F"])
    res = {**_fake_res("P"), **_fake_res("U"), **_fake_res("D")}
    with tempfile.TemporaryDirectory() as tmp:
        p = _write_table([dict(sub="P", conf=0.66), dict(sub="U", action="unplace", lat=np.nan, lon=np.nan, conf=np.nan, method="unplace_county"),
                          dict(sub="D", action="demote", lat=np.nan, lon=np.nan, conf=0.35, method="demote_block")], tmp)
        once, _ = GO.apply_overrides(copy.deepcopy(res), subs, path=p)
        twice, _ = GO.apply_overrides(copy.deepcopy(once), subs, path=p)
        assert once == twice, "applying the table twice must change nothing"
        # the neighbour stage rewrites confidences and may fill in an unplaced station: simulate both
        staged = copy.deepcopy(once)
        staged["P"]["sites"][0]["conf"] = 0.99
        staged["D"]["sites"][0]["conf"] = 0.8 * 0.9
        staged["U"] = _fake_res("U", conf=0.4, method="neighbor")["U"]
        post, st = GO.apply_overrides(staged, subs, post=True, path=p)
    assert post["P"]["sites"][0]["conf"] == 0.66, "an override confidence is final"
    assert post["D"]["sites"][0]["conf"] <= 0.35
    assert post["U"]["sites"][0]["pick"]["method"] == "neighbor", "a legitimate fill-in of an unplaced sub survives the second pass"
    assert "unplace" not in st


# ---------------------------------------------------------------------------------------------------- the hook
def test_hook_is_in_match_geo_before_and_after_the_neighbour_stage():
    src = inspect.getsource(GM.match_geo)
    first = src.index("geo_overrides.apply_overrides(res, subs, link=ov_link)")
    stage = src.index("neighbor_stage(res, pairs, edges, osm_xy, kv_sets)")
    second = src.index("geo_overrides.apply_overrides(res, subs, link=ov_link, post=True)")
    assert first < stage < second, "overrides must be pinned before the neighbour stage and again after it"
    assert "G1" in src


def test_committed_placements_reflect_the_table():
    """The committed substations.csv was written by a match-geo run with the hook: every row of the table is in it."""
    ov = GO.load_overrides().set_index("sub")
    s = _read("substations.csv").set_index("sub")
    for sub, r in ov.iterrows():
        row = s.loc[sub]
        if r["action"] == "place":
            assert abs(row["lat"] - r["lat"]) < 1e-5 and abs(row["lon"] - r["lon"]) < 1e-5, sub
            assert row["match_method"] == "override" and abs(row["match_conf"] - r["conf"]) < 1e-3, sub
            assert row["county"] == r["county"] and row["osm_id"] == r["osm_id"], sub
            assert str(row["evidence"]).startswith("G1 override"), sub
        elif r["action"] == "unplace":
            assert row["match_method"] in ("none", "neighbor"), f"{sub}: still has the matcher's site ({row['match_method']})"
        else:
            assert row["match_conf"] <= r["conf"] + 1e-6 and row["match_method"] != "override", sub
    chg = _read("geo_changes.csv")
    assert set(ov.index) <= set(chg["sub"]), "geo_changes.csv lists every overridden sub"


def test_no_unexplained_long_constraint_pair_remains():
    """Every line constraint whose two ends are placed is within 80 km (130 km at 230 kV and above), except the documented pairs."""
    c = _read("constraints.csv")
    s = _read("substations.csv").set_index("sub")
    L = c[(c["element_type"] == "line") & c["from_station"].notna() & c["to_station"].notna() & (c["from_station"] != c["to_station"])]
    pairs = L.groupby(["from_station", "to_station"])["kv"].max().reset_index()
    bad = []
    for r in pairs.itertuples():
        if r.from_station in s.index and r.to_station in s.index:
            a, b = s.loc[r.from_station], s.loc[r.to_station]
            if np.isfinite(a["lat"]) and np.isfinite(b["lat"]):
                km = float(GM.haversine_km(a["lat"], a["lon"], b["lat"], b["lon"]))
                if km > (130.0 if r.kv >= 230 else 80.0) and frozenset((r.from_station, r.to_station)) not in EXPLAINED_LONG_PAIRS:
                    bad.append((r.from_station, r.to_station, round(km)))
    assert not bad, f"unexplained long constraint pairs: {bad}"
    assert c["endpoint_how"].str.startswith("rejected_").sum() <= 1, "Phase 2 should have at most the explained ENTPR-TRSES row rejected"


def test_college_is_in_garland():
    s = _read("substations.csv").set_index("sub")
    c = s.loc["COLLEGE"]
    assert c["county"] == "DALLAS" and 32.85 < c["lat"] < 33.02 and -96.75 < c["lon"] < -96.55
    for partner in ("BRAND_C", "FAIRDALE", "JUPITER", "APOLLO"):
        p = s.loc[partner]
        assert float(GM.haversine_km(c["lat"], c["lon"], p["lat"], p["lon"])) < 8.0, partner


def test_rerun_of_match_geo_reproduces_the_committed_tables():
    """Opt-in (about 3 minutes): run match-geo again into a temporary folder and compare the files byte for byte with the committed ones."""
    if "rerun" not in " ".join(sys.argv[1:]) and os.environ.get("GEO_RERUN") != "1":
        print("      (skipped: opt-in, run with the argument `rerun`)")
        return
    import hashlib

    tmp = Path(tempfile.mkdtemp(prefix="geo_rerun_"))
    try:
        (tmp / "data").mkdir()
        (tmp / "derived").mkdir()
        shutil.copy(DATA / "settlement_points.csv", tmp / "data" / "settlement_points.csv")
        old = (GM.REPO_DATA, GM.DERIVED)
        GM.REPO_DATA, GM.DERIVED = tmp / "data", tmp / "derived"
        try:
            GM.match_geo()
        finally:
            GM.REPO_DATA, GM.DERIVED = old
        for f in ("substations.csv", "osm_substations.csv", "lines.csv"):
            a = hashlib.sha256((DATA / f).read_bytes()).hexdigest()
            b = hashlib.sha256((tmp / "data" / f).read_bytes()).hexdigest()
            assert a == b, f"{f} differs after a re-run"
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    run_module(globals())
