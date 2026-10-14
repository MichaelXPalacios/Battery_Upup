"""Connection cost classes: hand-calculated cases on a tiny synthetic grid, then invariants on the real table.

Run:  python tests/test_3a_costs.py
"""
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _harness_3a as H  # noqa: E402

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from gridsite import costs as K  # noqa: E402

MI = K.KM_PER_MILE


class StubLines:
    """Stands in for LineIndex: per point, distance to the nearest >= 138 kV line, its id and kV."""

    def __init__(self, table):
        self.table = table                                          # {(lat, lon): (km, id, kv)}

    def nearest(self, lat, lon):
        rows = [self.table[(round(float(a), 4), round(float(b), 4))] for a, b in zip(lat, lon)]
        return np.array([r[0] for r in rows]), [r[1] for r in rows], np.array([r[2] for r in rows])


def _subs(rows):
    cols = ["sub", "zone", "county", "lat", "lon", "match_conf", "kv_max", "osm_id", "in_np4160"]
    return pd.DataFrame(rows, columns=cols)


def _synthetic():
    # Two through stations 0.9 deg of longitude apart (about 86 km); a 138 kV stub 20 km east of the first; a 345 kV stub
    # next to a 345 kV line, far from any station; an unplaced station; a badly placed one; a 13 kV generator bus.
    lat0, lon0 = 31.0, -101.0
    dlon20 = 20.0 / (111.195 * np.cos(np.radians(lat0)))
    subs = _subs([
        ["THRU1", "LZ_WEST", "X", lat0, lon0, 0.9, 138.0, "w1", "True"],
        ["THRU2", "LZ_WEST", "X", lat0, lon0 - 0.9, 0.9, 345.0, "w2", "True"],
        ["STUB138", "LZ_WEST", "X", lat0, lon0 + dlon20, 0.8, 138.0, "w3", "True"],
        ["STUB345", "LZ_WEST", "X", lat0 + 1.5, lon0 - 0.45, 0.8, 345.0, "w4", "True"],
        ["NOPLACE", "LZ_WEST", "X", np.nan, np.nan, 0.0, 138.0, None, "True"],
        ["BADPLACE", "LZ_WEST", "X", lat0 + 0.2, lon0 + 3.0, 0.2, 138.0, "w5", "True"],
        ["GENBUS", "LZ_WEST", "X", lat0, lon0, 0.9, 13.0, "w1", "True"],
    ])
    deg = pd.Series({"w1": 3, "w2": 4, "w3": 1, "w4": 1, "w5": 2})
    lines = StubLines({(round(lat0, 4), round(lon0, 4)): (0.0, "L0", 138.0),
                       (round(lat0, 4), round(lon0 - 0.9, 4)): (0.0, "L0", 345.0),
                       (round(lat0, 4), round(lon0 + dlon20, 4)): (9.0, "L9", 138.0),            # too far for a tap
                       (round(lat0 + 1.5, 4), round(lon0 - 0.45, 4)): (0.8, "L345", 345.0)})    # 0.8 km from a 345 kV line
    return subs, deg, lines, 20.0


def test_unit_cost_rows_and_lookups():
    uc = K.load_unit_costs()
    assert (K.ITEM_BAY, "138") in uc and (K.ITEM_LINE, "345") in uc and (K.ITEM_SUBSTATION, "345") in uc
    b = uc[(K.ITEM_BAY, "138")]
    assert (b.low, b.mid, b.high) == (1.7e6, 1.7e6, 2.6e6)
    assert K.bay_cost_musd(uc, 69.0)[:3] == (1.7, 1.7, 2.6)                      # 69 kV takes the 138 kV bay row
    assert K.bay_cost_musd(uc, 230.0)[:3] == (3.4, 4.9, 5.4)                     # 230 kV takes the 345 kV row
    lo, mid, hi, _ = K.line_cost_musd(uc, 10 * MI, 138.0)                        # 10 miles of 138 kV
    assert (round(lo, 6), round(mid, 6), round(hi, 6)) == (23.0, 23.0, 25.0)
    assert K.line_cost_musd(uc, MI, 69.0)[1] == 1.9 and K.line_cost_musd(uc, MI, 345.0)[:3] == (4.0, 4.2, 6.3)
    assert K.switchyard_cost_musd(uc, 345.0)[:3] == (15.8, 19.8, 23.7)
    gsu, _ = K.gsu_cost_musd(uc, 100.0)
    assert abs(gsu - 16.95) < 1e-9                                                # (241,000 - 71,500) $/MW x 100 MW
    assert K.allowance_musd(138.0) == 14.0 and K.allowance_musd(69.0) == 14.0 and K.allowance_musd(345.0) == 20.0


def test_hand_calculated_classes_on_a_synthetic_grid():
    subs, deg, lines, _ = _synthetic()
    df = K.connection_costs(subs=subs, lines=lines, deg=deg).set_index("sub")
    assert "GENBUS" not in df.index                                               # 13 kV is below the 69 kV scope
    tie138 = K.SITE_TIE_KM / MI * 2.3                                             # 0.5 km of 138 kV line, $M
    tie345 = K.SITE_TIE_KM / MI * 4.2
    # through stations: one bay and a short tie
    a = df.loc["THRU1"]
    assert a.connection_class == "new_bay" and a.class_conf == 0.8 and a.kv == 138.0 and a.gen_tie_km == K.SITE_TIE_KM
    assert abs(a.cost_mid_musd - (1.7 + tie138)) < 1e-3 and a.owner_share_musd == 0.0 and a.allowance_musd == 14.0
    b = df.loc["THRU2"]
    assert b.connection_class == "new_bay" and b.kv == 345.0 and abs(b.cost_mid_musd - (4.9 + tie345)) < 1e-3
    assert abs(b.cost_low_musd - (3.4 + K.SITE_TIE_KM / MI * 4.0)) < 1e-3 and abs(b.cost_high_musd - (5.4 + K.SITE_TIE_KM / MI * 6.3)) < 1e-3
    # a 138 kV stub 20 km from THRU1, no line within TAP_MAX_KM -> gen-tie: 20 km x 1.25 of 138 kV line + a bay at THRU1
    c = df.loc["STUB138"]
    km = c.dist_station_km * K.ROUTE_FACTOR
    assert c.connection_class == "gen_tie" and c.poi_sub == "THRU1" and abs(c.dist_station_km - 20.0) < 0.05
    assert abs(c.gen_tie_km - km) < 1e-2 and abs(c.cost_mid_musd - (km / MI * 2.3 + 1.7)) < 5e-3
    assert abs(c.owner_share_musd - max(0.0, c.cost_mid_musd - 14.0)) < 1e-9 and c.owner_share_musd > 20
    # a 345 kV stub next to a 345 kV line, far from any station -> new switchyard on that line
    d = df.loc["STUB345"]
    assert d.connection_class == "new_switchyard" and d.poi_line == "L345" and d.kv == 345.0
    assert abs(d.cost_mid_musd - (19.8 + tie345)) < 1e-3 and abs(d.owner_share_musd - (19.8 + tie345 - 20.0)) < 1e-3
    # unplaced and badly placed stations get the default bay, flagged, with no geometry
    for sub, flag in (("NOPLACE", "unplaced"), ("BADPLACE", "low_conf_placement")):
        e = df.loc[sub]
        assert e.connection_class == "new_bay" and e.class_conf == 0.3 and flag in e["flags"] and "default_class" in e["flags"]
        assert abs(e.cost_mid_musd - (1.7 + tie138)) < 1e-3 and np.isnan(e.dist_station_km)


def test_lead_time_rules():
    uc = K.load_unit_costs()
    (lo, mid, hi), lpt = K.lead_time_months("new_bay", 138.0, 0.5, 100.0, uc)
    assert lpt and mid == 20.5 + 30.0 + 6.0 and lo == 16.0 + 12.0 + 6.0 and hi == 28.0 + 36.0 + 6.0
    # below 138 kV the step-up is a medium transformer: the 18-month bay build governs over 12 + 6 months of delivery
    (lo, mid, hi), lpt = K.lead_time_months("new_bay", 69.0, 0.5, 100.0, uc)
    assert not lpt and mid == 20.5 + max(18.0, 12.0 + 6.0)
    # a small battery has no large power transformer even at 345 kV
    _, lpt = K.lead_time_months("new_bay", 345.0, 0.5, 20.0, uc)
    assert not lpt
    # a long gen-tie can outlast the transformer: 9 + 0.3 x 100 km + 9 = 48 months > 36
    (lo, mid, hi), _ = K.lead_time_months("gen_tie", 138.0, 100.0, 100.0, uc)
    assert mid == 20.5 + 9.0 + 0.3 * 100 + 9.0
    # the queue's own median IA -> COD (35.6 months) is what a 30-month transformer plus 6 months of installation gives
    assert abs((20.5 + 30.0 + 6.0) - (20.5 + K.IA_TO_COD_MEDIAN_MONTHS)) < 1.0


def test_real_table_invariants():
    df = K.connection_costs()
    assert df["sub"].is_unique and (df["kv_max"] >= 69).all() and len(df) > 4900
    assert set(df["connection_class"]) == {"new_bay", "new_switchyard", "gen_tie"}
    assert (df["cost_low_musd"] <= df["cost_mid_musd"] + 1e-9).all() and (df["cost_mid_musd"] <= df["cost_high_musd"] + 1e-9).all()
    assert (df["owner_share_musd"] >= 0).all() and (df["owner_share_low_musd"] <= df["owner_share_musd"] + 1e-9).all()
    assert (df["owner_share_musd"] <= df["owner_share_high_musd"] + 1e-9).all()
    assert np.allclose(df["owner_share_musd"], np.maximum(0.0, df["cost_mid_musd"] - df["allowance_musd"]))
    assert np.allclose(df["all_in_mid_musd"], df["cost_mid_musd"] + df["gsu_cost_musd"])
    assert set(df["allowance_musd"]) <= {14.0, 20.0}
    assert (df["lead_low_months"] <= df["lead_time_months"] + 1e-9).all() and (df["lead_time_months"] <= df["lead_high_months"] + 1e-9).all()
    # geometry is used only where the placement is trustworthy
    geo = df[df["connection_class"] != "new_bay"]
    assert (geo["match_conf"] >= K.MIN_CONF_PLACED).all() and geo["lat"].notna().all()
    low = df[(df["match_conf"] < K.MIN_CONF_PLACED) | df["lat"].isna()]
    assert (low["connection_class"] == "new_bay").all() and (low["class_conf"] == 0.3).all()
    through = df[df["class_conf"] == 0.8]
    assert (through["n_line_terminations"] >= K.MIN_LINE_DEGREE).all() and (through["gen_tie_km"] == K.SITE_TIE_KM).all()
    gt = df[df["connection_class"] == "gen_tie"]
    assert (gt["gen_tie_km"] > K.COLOCATED_KM).all() and (gt["poi_sub"] != "").all() and (gt["poi_sub"] != gt["sub"]).all()
    colo = df[df["flags"].str.contains("colocated")]
    assert (colo["connection_class"] == "new_bay").all() and (colo["poi_sub"] != colo["sub"]).all()
    assert (colo["dist_station_km"] <= K.COLOCATED_KM + 1e-9).all() and (colo["class_conf"] == 0.5).all()
    sw = df[df["connection_class"] == "new_switchyard"]
    assert (sw["dist_line_km"] <= K.TAP_MAX_KM + 1e-9).all() and (sw["poi_line"] != "").all()
    # the cheaper of tap and tie was taken: where a line was within reach, a gen-tie row costs no more than the dearest switchyard (345 kV + tie)
    uc0 = K.load_unit_costs()
    dearest_switchyard = K.switchyard_cost_musd(uc0, 345.0)[1] + K.line_cost_musd(uc0, K.SITE_TIE_KM, 345.0)[1]
    both = gt[gt["dist_line_km"] <= K.TAP_MAX_KM]
    assert len(both) == 0 or (both["cost_mid_musd"] <= dearest_switchyard + 1e-6).all()
    # a hand check of recomputed mid cost for new_bay through stations
    uc = K.load_unit_costs()
    for _, r in through.sample(25, random_state=3).iterrows():
        b = K.bay_cost_musd(uc, r["kv"])[1]
        t = K.line_cost_musd(uc, K.SITE_TIE_KM, r["kv"])[1]
        assert abs(r["cost_mid_musd"] - (b + t)) < 2e-3, r["sub"]
    # the post-2025 allowance covers a through-station bay in every case
    assert (through["owner_share_musd"] == 0).all()
    assert (df["needs_lpt"] == (df["kv"] >= 138)).all()


def test_csv_and_labels_written():
    with tempfile.TemporaryDirectory() as td:
        out = Path(td) / "connection_costs.csv"
        df = K.write_connection_costs(out, verbose=False)
        back = pd.read_csv(out, keep_default_na=False)
        lab = pd.read_csv(Path(td) / "connection_costs.labels.csv")
    assert len(back) == len(df) and set(lab["column"]) == set(back.columns)
    assert set(lab["label"]) <= {"REAL", "DERIVED", "SIM", "ASSUMPTION"}
    required = ["sub", "connection_class", "kv", "gen_tie_km", "cost_low_musd", "cost_mid_musd", "cost_high_musd",
                "allowance_musd", "owner_share_musd", "lead_time_months", "provenance"]
    assert all(c in back.columns for c in required)


if __name__ == "__main__":
    H.run_module(globals())
