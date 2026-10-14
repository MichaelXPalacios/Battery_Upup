"""Queue and EIA storage -> ERCOT substation mapping: parsing, matching on a tiny synthetic model, and invariants on the
real table (coverage, key uniqueness, MW conservation, every mapped sub is a real NP4-160 substation).

Run:  python tests/test_3a_queue_map.py
"""
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _harness_3a as H  # noqa: E402

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from gridsite import queue_map as Q  # noqa: E402

_CACHE = {}


def _real():
    if "data" not in _CACHE:
        _CACHE["data"] = Q.load_inputs()
        _CACHE["L"] = Q.Lookups(_CACHE["data"])
        _CACHE["df"] = Q.build(_CACHE["data"], verbose=False)
    return _CACHE["data"], _CACHE["L"], _CACHE["df"]


def test_bus_number_extraction():
    f = Q.poi_numbers
    assert f("3114 Paltalco 138kV", "138") == ["3114"]
    assert f("Tap 345 kV Savana 43180 to Seabreeze 43020 - Ckt 27", "345") == ["43180", "43020"]
    assert f("Tap 138kV 3281 TENNCLNY_8 - 3499 BLACKFTAP_8", "138") == ["3281", "3499"]
    assert f("Andrews North – McKenzie Draw Switch 138 kV", "138") == []
    assert f("Tap to CKT#27 from Savana (Bus# 43180) to Oasis (Bus# 43035) – 345 kV Line", "345") == ["43180", "43035"]
    assert f("(CEHESub) looped either 1) on 138 kV circuit 82 between RIWOOD (#43150) and Angleton (#42110), or 2) on 345 kV circuit 18 between DOW (#42500)",
             "138") == ["43150", "42110", "42500"]
    assert f("Tap 345kV 44645 Singleton - 44900 Zenith Ckt #99", "345") == ["44645", "44900"]
    assert f("TAP EDITHCLA7B [60505] -- TESLA7A [60501] 345kV ckt2", "345") == ["60505", "60501"]
    assert f("Tap 138kV 25 North Zulch - 47 Hilltop Lakes", "138") == ["25", "47"]       # short numbers are kept here, checked later
    assert f("Substation Transmission kV - 138", "138") == []
    assert f("Bus number - 7258 138KV", "138") == ["7258"]


def test_name_helpers():
    assert Q.stem_bus_name("L_BUCKRA8_1Y") == "BUCKRA" and Q.stem_bus_name("ELM_MOTT__8") == "ELMMOTT"
    assert Q.stem_bus_name("TENNCLNY_8") == "TENNCLNY" and Q.stem_bus_name("HARRIS___B69") == "HARRIS"
    assert Q.stem_bus_name("TNCHOLLA__1") == "TNCHOLLA" or Q.stem_bus_name("TNCHOLLA__1") == "CHOLLA"
    assert Q.phrases("Anna 345 kV Bus# 2373.") == ["ANNA"]
    assert Q.phrases("Tap 138 KV Bois Pump (1835) - Brookston (1833)") == ["BOISPUMP", "BROOKSTON"]
    assert Q.phrases("3114 Paltalco 138kV") == ["PALTALCO"]
    assert Q.phrases("Natural Dam 138kV Substation (23836)") == ["NATURALDAM"]
    assert Q.fuzzy("MULESHOE", "MULESHOE") == 1.0 and Q.fuzzy("VALLEYVIEW", "VALLEY") >= 0.5
    assert Q.fuzzy("ABCDEFG", "ABCDEFGHI") == 0.9                # long prefix
    assert Q.fuzzy("ABCD", "WXYZ") < 0.3


def _tiny():
    sp = pd.DataFrame({
        "PSSE_BUS_NUMBER": [1001, 1002, 1003, 1004, 5, 2001],
        "SUBSTATION": ["ALPHA", "BRAVO", "CHARLIE", "ALPHA", "LOWNUM", "DELTA"],
        "PSSE_BUS_NAME": ["ALPHA_8", "BRAVO_8", "CHARLIE_8", "ALPHA_5", "LOWNUM_8", "DELTA_8"],
        "VOLTAGE_LEVEL": [138.0, 138.0, 345.0, 69.0, 138.0, 138.0],
        "ELECTRICAL_BUS": ["a", "b", "c", "d", "e", "f"]})
    subs = pd.DataFrame({
        "sub": ["ALPHA", "BRAVO", "CHARLIE", "LOWNUM", "DELTA", "DELTAVL"], "kv_max": [138.0, 138.0, 345.0, 138.0, 138.0, 138.0],
        "county": ["BELL", "BELL", "TRAVIS", "BELL", "HARRIS", "BELL"], "name_guess": ["Alpha Substation", None, "Charlie Switch", None, None, None],
        "match_conf": [0.9] * 6, "lat": [30.0] * 6, "lon": [-97.0] * 6, "in_np4160": [True] * 6, "eia_plant_codes": [None] * 6,
        "kv_list": ["138;69", "138", "345", "138", "138", "138"]})
    osm = pd.DataFrame({"osm_id": ["w1"], "name": ["Deltaville Substation"], "ref": [None], "ercot_sub": ["DELTAVL"], "county": ["BELL"]})
    return dict(sp=sp, subs=subs, osm=osm)


def _row(text, county="Bell", kv="138"):
    return pd.Series({"poi_text": text, "county": county, "poi_kv": kv})


def test_mapping_rules_on_a_tiny_model():
    L = Q.Lookups(_tiny())
    # one existing station, voltage and county agree
    m = Q.map_queue_row(_row("1001 Alpha 138kV"), L)
    assert (m["sub"], m["method"], m["conf"], m["is_tap"]) == ("ALPHA", "psse_bus", Q.CONF_BUS_SINGLE, False)
    # voltage disagrees with the bus voltage (a 345 kV POI text on a 138 kV bus)
    m = Q.map_queue_row(_row("1001 Alpha 345kV", kv="345"), L)
    assert m["sub"] == "ALPHA" and m["conf"] == Q.CONF_BUS_KV_MISMATCH
    # county differs from the station's county: small penalty
    m = Q.map_queue_row(_row("1002 Bravo 138kV", county="Coryell"), L)
    assert m["sub"] == "BRAVO" and abs(m["conf"] - (Q.CONF_BUS_SINGLE - Q.COUNTY_MISMATCH_PENALTY)) < 1e-9
    # a tap between two known stations: both ends recorded, the end in the project's county is the primary one
    m = Q.map_queue_row(_row("Tap 138kV 1002 Bravo - 1003 Charlie", county="Travis", kv="138"), L)
    assert m["is_tap"] and m["method"] == "psse_bus_tap" and (m["sub"], m["sub_alt"]) == ("CHARLIE", "BRAVO") and m["conf"] == Q.CONF_TAP_BOTH_ENDS
    # a tap with one end not in the model
    m = Q.map_queue_row(_row("Tap 138kV 1002 Bravo - 9999 Newville"), L)
    assert m["method"] == "psse_bus_tap_one_end" and m["sub"] == "BRAVO" and m["sub_alt"] == "" and m["conf"] == Q.CONF_TAP_ONE_END
    # two numbers of the same station are one station, not a tap
    m = Q.map_queue_row(_row("1001 and 1004 Alpha 138kV"), L)
    assert m["sub"] == "ALPHA" and m["method"] == "psse_bus" and not m["is_tap"]
    # a small number is a bus only with name evidence: "Ckt 5" must not map to LOWNUM, "5 Lownum" does
    assert Q.map_queue_row(_row("1002 Bravo 138kV 1) on circuit 5"), L)["sub"] == "BRAVO"
    assert Q.map_queue_row(_row("Something unrelated #5"), L)["method"] in ("unmapped", "name_county")
    assert Q.map_queue_row(_row("5 Lownum 138kV"), L)["sub"] == "LOWNUM"
    # no usable number: the name decides, with county agreement
    m = Q.map_queue_row(_row("Charlie Switch 345kV", county="Travis", kv="345"), L)
    assert m["method"] == "name_county" and m["sub"] == "CHARLIE" and m["conf"] == Q.CALIBRATION["name_conf_county_ok"]
    # the name matches a station in another county: not accepted
    m = Q.map_queue_row(_row("Charlie Switch 345kV", county="Harris", kv="345"), L)
    assert m["method"] == "unmapped" and m["sub"] == "" and "another county" in m["note"]
    # a name that is in the model only through an OSM name
    m = Q.map_queue_row(_row("Deltaville Substation 138 kV"), L)
    assert m["sub"] == "DELTAVL"
    # nothing to go on
    m = Q.map_queue_row(_row("TBD"), L)
    assert m["method"] == "unmapped" and m["conf"] == 0.0


def test_stage_labels():
    assert Q._stage("SS Completed, FIS Completed, IA", "x") == "IA_signed"
    assert Q._stage("SS Completed, FIS Started, IA", "x") == "IA_signed"
    assert Q._stage("SS Completed, FIS Started, No IA", "") == "FIS_started_no_IA"
    assert Q._stage("SS Completed, FIS Completed, No IA", "") == "FIS_completed_no_IA"
    assert Q._stage("Small Gen, model ready", "") == "small_gen_model_ready"


def test_real_table_invariants():
    data, L, df = _real()
    q, g = df[df["source"] == "queue"], df[df["source"] == "eia"]
    assert len(q) == 869 and len(g) == 416 and df["inr_or_plant"].is_unique
    assert abs(q["mw"].sum() - data["q"]["mw"].sum()) < 1e-6                       # no MW lost or invented
    assert abs(g["mw"].sum() - data["gen"][data["gen"]["class"] == "battery"]["mw"].sum()) < 1e-6
    real_subs = set(data["subs"]["sub"])
    mapped = df[df["conf"] > 0]
    assert (mapped["sub"] != "").all() and mapped["sub"].isin(real_subs).all()
    assert (df[df["conf"] == 0]["sub"] == "").all() and (df[df["sub"] == ""]["conf"] == 0).all()
    assert df["conf"].between(0, 1).all()
    assert set(df["method"]) <= {"psse_bus", "psse_bus_tap", "psse_bus_tap_one_end", "name_county", "eia_plant_link", "nearest_sub", "unmapped"}
    assert set(q["method"]) <= {"psse_bus", "psse_bus_tap", "psse_bus_tap_one_end", "name_county", "unmapped"}
    assert set(g["method"]) <= {"eia_plant_link", "nearest_sub", "unmapped"}
    both = q[q["method"] == "psse_bus_tap"]
    assert (both["sub_alt"] != "").all() and (both["sub"] != both["sub_alt"]).all() and both["is_tap"].all()
    # independent re-derivation of the bus-number rows
    sp = data["sp"]
    bus2sub = dict(zip(sp["PSSE_BUS_NUMBER"].astype(str), sp["SUBSTATION"]))
    for _, r in q[q["method"] == "psse_bus"].sample(40, random_state=5).iterrows():
        nums = [n for n in Q.poi_numbers(r["poi_text"], r["poi_kv"]) if n in bus2sub]
        assert r["sub"] in {bus2sub[n] for n in nums}, r["inr_or_plant"]
    # coverage: MW mapped at confidence >= 0.5
    tot_q = q["mw"].sum()
    assert q[q["conf"] >= 0.5]["mw"].sum() / tot_q > 0.85
    op = g[(g["stage"] == "operating") & (g["note"].str.contains("outside ERCOT") == False)]    # noqa: E712
    assert op[op["conf"] >= 0.5]["mw"].sum() / op["mw"].sum() > 0.80
    # plants outside ERCOT are not mapped
    ext = data["gen"][(data["gen"]["class"] == "battery") & (data["gen"]["balancing_authority"] != "ERCO")]
    ext_ids = {f"{int(a)}:{b}" for a, b in zip(ext["plant_code"], ext["gen_id"])}
    assert (df[df["inr_or_plant"].isin(ext_ids) & (df["method"] == "nearest_sub")].empty)
    # operating units keep their stage and in-service month
    assert set(g["stage"]) <= {"operating", "planned"} and g[g["stage"] == "operating"]["cod"].str.match(r"^\d{4}-\d{2}$").all()
    print("      " + Q.summary(df).to_string().replace("\n", "\n      "), flush=True)


def test_nearest_rule_is_tie_aware():
    data, _, _ = _real()
    mp = Q.EiaMapper(data)
    r = data["subs"][data["subs"]["lat"].notna() & (data["subs"]["kv_max"] >= 138) & (data["subs"]["match_conf"] >= 0.5)].iloc[10]
    nb = mp.nearest(float(r["lat"]) + 0.0003, float(r["lon"]), None)
    assert nb is not None and nb[1] < 0.2 and nb[3] >= 1
    assert Q.nearest_conf(0.4, 1) == 0.70 and Q.nearest_conf(0.4, 3) == 0.35 and Q.nearest_conf(2.0, 1) == 0.60
    assert Q.nearest_conf(5.0, 1) == 0.15 and Q.nearest_conf(9.0, 1) == 0.0


def test_calibration_constants_match_the_measurements():
    out = Q.calibrate(verbose=False)
    h = out["name_hit_rate_by_county_state"]
    assert h["ok"]["n"] >= 150 and h["ok"]["hit"] >= 0.90 and h["unknown"]["hit"] >= 0.85
    fa = out["name_false_accept_by_county_state"]
    assert 0.3 <= fa["ok"]["rate"] <= 0.6 and fa["unknown"]["rate"] <= 0.15
    prec = h["ok"]["hit"] * 0.5 / (h["ok"]["hit"] * 0.5 + fa["ok"]["rate"] * 0.5)        # at a 50% chance the station exists
    assert prec >= Q.CALIBRATION["name_conf_county_ok"] - 1e-9
    near = out["nearest_hit_rate_by_km"]
    assert near["unique (-1.0, 1.0]"]["hit"] >= Q.CALIBRATION["nearest_conf_by_km"][0][1] - 0.05
    assert near["ambiguous (-1.0, 1.0]"]["hit"] <= 0.5
    for k, v in near.items():
        if "(8.0" in k or "(15.0" in k:
            assert v["hit"] <= 0.2, k


def test_csv_and_labels_written():
    data, L, _ = _real()
    with tempfile.TemporaryDirectory() as td:
        out = Path(td) / "queue_sub_map.csv"
        df = Q.write_queue_sub_map(out, verbose=False)
        back = pd.read_csv(out, keep_default_na=False)
        lab = pd.read_csv(Path(td) / "queue_sub_map.labels.csv")
    assert len(back) == len(df) == 869 + 416 and set(lab["column"]) == set(back.columns)
    assert set(lab["label"]) <= {"REAL", "DERIVED", "SIM", "ASSUMPTION"}
    for c in ("inr_or_plant", "source", "status", "mw", "cod", "sub", "method", "conf"):
        assert c in back.columns


if __name__ == "__main__":
    H.run_module(globals())
