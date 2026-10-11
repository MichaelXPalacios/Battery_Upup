"""rt_v1 1.1.0 against D1a's interim feature builder (`dispatch/models/interim_features.py`), on REAL data.

Question: does D0's builder give the numbers D1a's registered models were trained on? If it does, those models can be served from D0's
online store WITHOUT retraining (after the rename in `features/compat.py`, because the models name their inputs the interim way).

What is compared. For HB_NORTH and HB_HOUSTON, the decisions of the 4-hour epoch grid (the grid D1a trained and evaluated on; on it both
builders see the same newest price, so the comparison is about the DEFINITIONS, not about publication timing), every row of both frames,
every one of the 59 model columns (19 that rt_v1 had under other names, 40 of the 41 new ones: `rt_prof7_med` etc. are among the 59; the
41st, `ecrs`, is not a model input). Per column: max absolute difference, max relative difference, number of rows where one frame has
NaN and the other does not.

Where the two builders differ BY DESIGN (and which is right) is in notes/d0.md. Nothing is written to the real lake or the real registry:
the interim history is read with cache=False and the day-ahead history with freeze=False.

* `test_new_columns_equal_the_interim_builder_on_dst_and_holiday_windows`  seven windows around DST changes, holidays and the newest data
* `test_full_history_equivalence` (opt-in, DISPATCH_EQUIV_FULL=1)           2019-01-01 to 2026-09-20, about 1.6 million rows per point
* `test_the_registered_model_forecasts_the_same_from_both_builders`         the registry's `research` model, predict() on both frames
* `test_name_map_*`, `test_compat_*`                                         the rename, no real data needed

Run the report by hand:  python tests/test_rt_v1_1_equivalence.py compare HB_NORTH 2024-01-01 2026-09-20
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

PROJECT = Path(__file__).resolve().parents[1]
RESEARCH = Path(os.environ.get("ARENA_DATA", "C:/Users/Javier/Repo/BaseP/research-data"))
REAL_HOME = Path(os.environ.get("DISPATCH_REAL_HOME", str(RESEARCH / "dispatch")))
TOL_ABS = 1e-4                         # float32 columns: the observed difference is exactly 0.0, this is 1e-4 of a cent of a price
WINDOWS = [("2019-03-08", "2019-03-13"), ("2019-11-01", "2019-11-06"),             # DST changes of 2019
           ("2025-03-07", "2025-03-12"), ("2025-10-31", "2025-11-05"),             # DST changes of 2025
           ("2025-12-23", "2026-01-03"),                                           # Christmas and New Year's Day
           ("2026-06-30", "2026-07-07"),                                           # 4 July is a Saturday: the Friday is the observed holiday
           ("2026-09-10", "2026-09-26")]                                           # the newest data, real day-ahead publication times
POINTS = ("HB_NORTH", "HB_HOUSTON")


def _real_data_available() -> bool:
    return ((REAL_HOME / "lake" / "parsed" / "rt_spp_hist").exists() and list((RESEARCH / "ercot").glob("rtm20??.csv"))
            and (RESEARCH / "grid" / "derived" / "as_prices.parquet").exists())


needs_real = pytest.mark.skipif(not _real_data_available(), reason="needs the real lake and research-data/ercot (D1a's interim inputs)")


# ------------------------------------------------------------------------------------------------ the comparison (runs in a clean subprocess)
def _model_columns():
    from dispatch.models import interim_features as F
    return tuple(F.RT_FEATURES) + tuple(F.as_feature_columns(F.AS_MODEL_PRODUCTS))


def compare(sp: str, windows) -> dict:
    """Per-column differences between D0's training_frame and the interim builder's over the decision windows [(start, end), ...]."""
    from dispatch.features import build, compat
    from dispatch.models import interim_features as F
    hist = F.load_history(sp, cache=False)
    as_hist = F.load_as_history(freeze=False)
    names = _model_columns()
    acc = {n: {"max_abs": 0.0, "max_rel": 0.0, "nan_mismatch": 0, "nan_both": 0, "rows": 0} for n in names}
    rows_i = rows_d = common = 0
    for start, end in windows:
        s, e = pd.Timestamp(start, tz="UTC"), pd.Timestamp(end, tz="UTC")
        Xi, _, di = F.training_frame(sp, s, e, "4h", history=hist, as_hist=as_hist)
        Xd, _, _ = build.training_frame(sp, s, e, "4h")
        Xi = Xi.assign(decision=di.to_numpy(), target=Xi.index).reset_index(drop=True)
        Xd = compat.to_model_names(Xd).rename(columns={"asof_utc": "decision", "interval_start_utc": "target"})
        m = Xi.merge(Xd, on=["decision", "target"], suffixes=("_i", "_d"), how="inner")
        rows_i, rows_d, common = rows_i + len(Xi), rows_d + len(Xd), common + len(m)
        for n in names:
            a = m[n + "_i"].to_numpy(dtype=float) if n + "_i" in m.columns else m[n].to_numpy(dtype=float)
            b = m[n + "_d"].to_numpy(dtype=float) if n + "_d" in m.columns else m[n].to_numpy(dtype=float)
            both, one = np.isnan(a) & np.isnan(b), np.isnan(a) ^ np.isnan(b)
            d = np.abs(a - b)
            ok = ~(both | one)
            r = acc[n]
            r["rows"] += int(len(m))
            r["nan_both"] += int(both.sum())
            r["nan_mismatch"] += int(one.sum())
            if ok.any():
                r["max_abs"] = max(r["max_abs"], float(d[ok].max()))
                r["max_rel"] = max(r["max_rel"], float((d[ok] / np.maximum(np.abs(a[ok]), 1.0)).max()))
    return {"sp": sp, "windows": list(windows), "rows_interim": rows_i, "rows_d0": rows_d, "rows_common": common, "columns": acc}


def predictions(sp: str, asofs) -> dict:
    """The registered `research` model's forecasts from the interim frame and from D0's frame (renamed), same decisions."""
    from dispatch.features import build, compat
    from dispatch.models import interim_features as F
    from dispatch.registry import mlflow_registry as reg
    fc = reg.load("research")
    hist = F.load_history(sp, cache=False)
    as_hist = F.load_as_history(freeze=False)
    worst = {"rt_p10": 0.0, "rt_p50": 0.0, "rt_p90": 0.0}
    n = 0
    for a in asofs:
        asof = pd.Timestamp(a, tz="UTC")
        fi = fc.predict(F.point_in_time(sp, asof, history=hist, as_hist=as_hist), asof, settlement_point=sp)
        fd = fc.predict(compat.for_forecaster(build.point_in_time(sp, asof), fc), asof, settlement_point=sp)
        for k in worst:
            worst[k] = max(worst[k], float(np.abs(fi.frame[k].to_numpy() - fd.frame[k].to_numpy()).max()))
        n += 1
    return {"sp": sp, "decisions": n, "model_version": str(fc.version), "feature_set": fc.feature_set, "max_abs_diff": worst}


def _run(*args, timeout=3600) -> dict:
    env = {k: v for k, v in os.environ.items() if k != "PYTEST_CURRENT_TEST"}
    env.update(PYTHONIOENCODING="utf-8", MLFLOW_DISABLE_AGENT_HINT="1", DISPATCH_HOME=str(REAL_HOME))        # the REAL lake, never a test's temp home
    r = subprocess.run([sys.executable, str(Path(__file__).resolve()), *map(str, args)], capture_output=True, text=True, env=env, cwd=str(PROJECT), timeout=timeout)
    assert r.returncode == 0, (r.stderr or r.stdout)[-3000:]
    line = next(x for x in r.stdout.splitlines() if x.startswith("REPORT="))
    return json.loads(line[len("REPORT="):])


def _check(rep: dict, label: str) -> None:
    assert rep["rows_common"] > 1000 and rep["rows_common"] == rep["rows_interim"] == rep["rows_d0"], (label, rep["rows_interim"], rep["rows_d0"], rep["rows_common"])
    bad = {n: r for n, r in rep["columns"].items() if r["max_abs"] > TOL_ABS or r["nan_mismatch"]}
    assert not bad, f"{label}: columns that differ: {bad}"


# ------------------------------------------------------------------------------------------------ tests
@needs_real
@pytest.mark.parametrize("sp", POINTS)
def test_new_columns_equal_the_interim_builder_on_dst_and_holiday_windows(sp):
    rep = _run("compare", sp, json.dumps(WINDOWS))
    _check(rep, sp)
    assert len(rep["columns"]) == 59
    print(f"\n{sp}: {rep['rows_common']:,} rows, max abs difference over all 59 model columns = {max(r['max_abs'] for r in rep['columns'].values())}")


@needs_real
@pytest.mark.skipif(os.environ.get("DISPATCH_EQUIV_FULL") != "1", reason="opt-in: DISPATCH_EQUIV_FULL=1 (about 3 minutes per point)")
@pytest.mark.parametrize("sp", POINTS)
def test_full_history_equivalence(sp):
    rep = _run("compare", sp, json.dumps([("2019-01-01", "2026-09-20")]))
    _check(rep, sp)
    print("\n" + json.dumps({"sp": sp, "rows": rep["rows_common"], "columns": {n: [r["max_abs"], r["max_rel"], r["nan_mismatch"]] for n, r in rep["columns"].items()}}))


@needs_real
def test_the_registered_model_forecasts_the_same_from_both_builders():
    try:
        out = [_run("predict", sp, json.dumps(["2025-11-02T12:00:00Z", "2026-06-15T00:00:00Z", "2026-07-03T16:00:00Z", "2026-09-10T08:00:00Z",
                                                "2026-09-18T20:00:00Z", "2026-09-24T12:00:00Z"])) for sp in POINTS]
    except AssertionError as e:                                                    # no model under the alias on this machine
        if "NoModelError" in str(e) or "alias" in str(e).lower():
            pytest.skip("no registered 'research' model here")
        raise
    for rep in out:
        assert all(v <= 1e-6 for v in rep["max_abs_diff"].values()), rep
        assert rep["feature_set"].startswith("interim"), rep
    print("\n" + json.dumps(out))


# ---- the rename (no real data)
def test_name_map_is_d1as_d0_name():
    """compat.INTERIM_TO_D0 is `models.train.D0_NAME` (interim name -> rt_v1 name), and `tgt_hod` is the hour plus the quarter hour."""
    from dispatch.features import compat, sets
    from dispatch.models import train
    assert compat.INTERIM_TO_D0 == train.D0_NAME
    assert train.D0_EXPAND == {"tgt_hod": ("hour_local", "qtr_hour")} and compat.HOD == "tgt_hod"
    model = ("rt_last", "rt_mean_4", "rt_std_672", "tgt_hod", "tgt_is_holiday", "tgt_doy_sin", "as_rrs_rel", "as_nspin_med14", "rt_prof7_med", "res_q90_672")
    assert set(train.d0_feature_names(model)) <= set(sets.FEATURES)                    # every name D1a's models map to exists in rt_v1 1.1.0


def _frame(n=4):
    from dispatch.features import sets
    idx = pd.date_range("2026-10-06T15:00:00Z", periods=n, freq="15min", name="interval_start_utc")
    df = pd.DataFrame({f.name: np.arange(n, dtype="float64").astype(f.dtype) + 1 for f in sets.FEATURE_TABLE}, index=idx)
    df["hour_local"] = np.array([10, 10, 10, 11], dtype="int8")[:n]
    df["qtr_hour"] = np.array([0, 1, 2, 3], dtype="int8")[:n]
    df["settlement_point"], df["asof_utc"], df["max_published_utc"] = "HB_NORTH", idx[0], idx[0]
    df.attrs["settlement_point"] = "HB_NORTH"
    return df


def test_compat_adds_the_interim_names_and_keeps_the_d0_ones():
    from dispatch.features import compat
    df = _frame()
    out = compat.to_model_names(df)
    assert set(df.columns) < set(out.columns) and out.attrs == df.attrs
    pd.testing.assert_series_equal(out["rt_mean_4"], df["rt_mean_1h"], check_names=False)
    pd.testing.assert_series_equal(out["as_rrs_med14"], df["dam_as_rrs_med14"], check_names=False)
    assert list(out["tgt_hod"]) == [10.0, 10.25, 10.5, 11.75] and str(out["tgt_hod"].dtype) == "float32"
    only = compat.to_model_names(df, {"tgt_dow", "rt_last"})                       # only what a model needs
    assert set(only.columns) - set(df.columns) == {"tgt_dow"}
    partial = compat.to_model_names(df.drop(columns=["rt_mean_1h", "qtr_hour"]))   # a missing source is skipped, not invented
    assert "rt_mean_4" not in partial.columns and "tgt_hod" not in partial.columns


def test_compat_renames_for_interim_models_only():
    from dispatch.features import compat
    df = _frame()
    spec = SimpleNamespace(needed_columns=lambda: ["rt_mean_4", "tgt_hod", "as_regup_rel", "rt_last"])
    interim = SimpleNamespace(feature_set="interim_d1a/v1", models={"HB_NORTH": SimpleNamespace(spec=spec)})
    out = compat.for_forecaster(df, interim)
    assert {"rt_mean_4", "tgt_hod", "as_regup_rel"} <= set(out.columns) and "tgt_dow" not in out.columns
    native = SimpleNamespace(feature_set="rt_v1/1.1.0", models={})
    assert compat.for_forecaster(df, native) is df                                  # a model trained on D0's names gets the frame untouched


if __name__ == "__main__":                                  # the subprocess side of the real-data tests
    sys.path.insert(0, str(PROJECT))
    if sys.argv[1] == "compare":
        wins = json.loads(sys.argv[3]) if sys.argv[3].startswith("[") else [(sys.argv[3], sys.argv[4])]
        print("REPORT=" + json.dumps(compare(sys.argv[2], wins)))
    elif sys.argv[1] == "predict":
        print("REPORT=" + json.dumps(predictions(sys.argv[2], json.loads(sys.argv[3]))))
