"""rt_v1 1.1.0 against the numbers D1a's interim feature builder produced, frozen in `tests/fixtures/interim_golden_*.parquet`.

History. D1a trained version 1 of the registered model on its own point-in-time builder (`models/interim_features.py`). D0's `rt_v1` 1.1.0
carries the same columns under D0's names, and this file used to compare the two builders on the real data (1.6 million rows per hub,
difference 0.0). On 11 Oct 2026 the model was retrained on D0's builder (notes/p1-retrain.md) and the interim builder was retired. What it
computed is kept here as data, so D0's builder is still held to it:

    interim_golden_features.parquet   4 hubs x 5 decisions x 96 horizons: the 59 columns of version 1 (interim names), the day-ahead ECRS
                                      price, and what version 1 forecast from them (v1_rt_p10/p50/p90, v1_as_<product>_p50)
    interim_golden_inputs.parquet     the raw prices those frames were built from (15-minute RT prices of each hub, hourly day-ahead AS
                                      prices), from 16 (30) days before each decision to just over a day after it
    interim_golden.sha256             checksums of both; tests/fixtures/make_interim_golden.py says how they were made

The five decisions are the days that break time-of-day code: the spring-forward day of 2019, the fall-back day of 2025, Winter Storm Fern, the
observed holiday of 3 July 2026, and the newest data.

* `test_d0_*_reproduces_the_frozen_interim_frames`     D0's builder over a lake rebuilt from the frozen inputs: every column, every row, 0.0
* `test_nothing_published_after_the_decision_...`     the same with the lake cut at the decision time: no column moves
* `test_version_1_still_forecasts_the_frozen_numbers`  the registered version 1 on D0's frames, renamed by `features/compat.py` (the rollback
                                                       path), against what it forecast from the interim frames
* `test_the_real_lake_reproduces_the_frozen_interim_frames`  the same comparison on the real lake (skipped where there is none)
* `test_compat_*`, `test_model_features_*`             the rename, no data needed

Two things differ by design between the builders and are NOT in the fixture's decisions (notes/d0.md section 15): the interim builder assumed
the day-ahead results public at 13:30 Central, D0 uses the posting time (14:00 for the archive years, the file name's time from the MIS
window), so they differ for a decision between 13:30 and the posting; and the interim builder forward-fills a missing price inside a window
where D0 skips it. None of the five decisions is in the first window and none of the hubs has a gap.
"""
from __future__ import annotations

import hashlib
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
FIXTURES = PROJECT / "tests" / "fixtures"
RESEARCH = Path(os.environ.get("ARENA_DATA", str(PROJECT.parents[1] / "research-data")))   # default: beside the repo
REAL_HOME = Path(os.environ.get("DISPATCH_REAL_HOME", str(RESEARCH / "dispatch")))
TOL_ABS = 1e-4                         # the fixture stores float32; the observed difference is exactly 0.0
HUBS = ("HB_NORTH", "HB_WEST", "HB_HOUSTON", "HB_SOUTH")
PRODUCTS = ("regup", "regdn", "rrs", "ecrs", "nspin")
META = ("sp", "decision_utc", "interval_start_utc")


# ------------------------------------------------------------------------------------------------ the fixture
def _golden():
    feats = pd.read_parquet(FIXTURES / "interim_golden_features.parquet")
    inputs = pd.read_parquet(FIXTURES / "interim_golden_inputs.parquet")
    return feats, inputs


def _interim_columns(feats: pd.DataFrame) -> list:
    return [c for c in feats.columns if c not in META and not c.startswith("v1_")]


def _compare(frame: pd.DataFrame, gold: pd.DataFrame) -> dict:
    """Per interim-named column: max absolute difference and rows where exactly one side is NaN, D0's frame (renamed) against the fixture.
    Both are indexed by the target interval."""
    from dispatch.features import compat
    d0 = compat.to_model_names(frame)
    assert set(gold.index) <= set(d0.index), f"D0 has no row for {len(set(gold.index) - set(d0.index))} golden targets"
    d0 = d0.loc[gold.index]
    out = {}
    for c in _interim_columns(gold.reset_index()):
        assert c in d0.columns, f"D0's frame has no column for the interim {c!r}"
        a, b = gold[c].to_numpy(dtype=float), d0[c].to_numpy(dtype=float)
        both, one = np.isnan(a) & np.isnan(b), np.isnan(a) ^ np.isnan(b)
        ok = ~(both | one)
        out[c] = {"max_abs": float(np.abs(a - b)[ok].max()) if ok.any() else 0.0, "nan_mismatch": int(one.sum()), "nan_both": int(both.sum())}
    return out


def _bad(report: dict) -> dict:
    return {c: r for c, r in report.items() if r["max_abs"] > TOL_ABS or r["nan_mismatch"]}


def test_the_fixture_is_the_one_that_was_frozen():
    """A changed fixture must be a deliberate act: its sha256 is in a file next to it (and in notes/p1-retrain.md)."""
    text = (FIXTURES / "interim_golden.sha256").read_text(encoding="utf-8").splitlines()          # git may check the file out with CRLF
    want = dict(reversed(line.strip().split("  ", 1)) for line in text if line.strip())
    assert set(want) == {"interim_golden_features.parquet", "interim_golden_inputs.parquet"}
    for name, sha in want.items():
        assert hashlib.sha256((FIXTURES / name).read_bytes()).hexdigest() == sha, name
    feats, inputs = _golden()
    assert len(feats) == 4 * 5 * 96 and set(feats["sp"]) == set(HUBS) and feats["decision_utc"].nunique() == 5
    assert len(_interim_columns(feats)) == 59 + 1                       # the 59 inputs of version 1, and the day-ahead ECRS price
    assert set(inputs["series"]) == {"rt", "dam_as"} and set(inputs[inputs.series == "dam_as"]["key"]) == set(PRODUCTS)


# ------------------------------------------------------------------------------------------------ the names
def test_model_features_are_the_interim_ones_renamed():
    """The retrained model's inputs (`train.MODEL_FEATURES`, rt_v1 names) are exactly the 59 inputs of version 1 under the other spelling,
    with `tgt_hod` as the hour and the quarter hour, and every one of them is a column of rt_v1 1.1.0."""
    from dispatch.features import compat, sets
    from dispatch.models import train
    feats, _ = _golden()
    v1 = {c for c in _interim_columns(feats) if c != "as_ecrs_dam"}
    assert len(v1) == 59 and len(train.MODEL_FEATURES) == 60 and len(set(train.MODEL_FEATURES)) == 60
    back = {d0: interim for interim, d0 in compat.INTERIM_TO_D0.items()}
    renamed = {back.get(c, c) for c in train.MODEL_FEATURES if c not in ("hour_local", "qtr_hour")} | {compat.HOD}
    assert renamed == v1
    assert set(train.MODEL_FEATURES) <= set(sets.FEATURES)
    assert train.ANCHOR_COL in train.MODEL_FEATURES and set(train.SCALES["price"] + train.SCALES["resid"]) <= set(train.MODEL_FEATURES)
    assert set(train.CORE_NONNULL) <= set(train.MODEL_FEATURES)


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
    """Version 1 (feature_set interim_d1a/v1) is served through the rename; a model trained on rt_v1 names gets the frame untouched."""
    from dispatch.features import compat
    df = _frame()
    spec = SimpleNamespace(needed_columns=lambda: ["rt_mean_4", "tgt_hod", "as_regup_rel", "rt_last"])
    interim = SimpleNamespace(feature_set="interim_d1a/v1", models={"HB_NORTH": SimpleNamespace(spec=spec)})
    out = compat.for_forecaster(df, interim)
    assert {"rt_mean_4", "tgt_hod", "as_regup_rel"} <= set(out.columns) and "tgt_dow" not in out.columns
    for fs in ("rt_v1/1.1.0", "rt_v1", "", None):
        native = SimpleNamespace(feature_set=fs, models={})
        assert compat.for_forecaster(df, native) is df                              # a model trained on D0's names: no rename, no copy
    assert compat.for_forecaster(df, None) is df and compat.for_forecaster(df, object()) is df


# ------------------------------------------------------------------------------------------------ D0's builder against the fixture, no real lake
@pytest.fixture(scope="module")
def golden():
    return _golden()


@pytest.fixture(scope="module")
def lake(golden):
    """The frozen inputs written into a parsed lake of their own, with D0's builder over it (every hub; the five windows are 7 years apart
    in places, which is fine: a window only ever reads its own 16 days)."""
    from dispatch.models.synthetic import SyntheticLake
    _, inputs = golden
    rt = inputs[inputs["series"] == "rt"]
    prices = {sp: pd.Series(g["value"].to_numpy(), index=pd.DatetimeIndex(g["interval_start_utc"])) for sp, g in rt.groupby("key")}
    asp = inputs[inputs["series"] == "dam_as"].pivot(index="interval_start_utc", columns="key", values="value").sort_index()
    asp.index = pd.DatetimeIndex(asp.index)
    return SyntheticLake(prices, dam_as=asp)


@pytest.mark.parametrize("sp", HUBS)
def test_d0_point_in_time_reproduces_the_frozen_interim_frames(lake, golden, sp):
    feats, _ = golden
    g = feats[feats["sp"] == sp]
    worst = 0.0
    for decision, gd in g.groupby("decision_utc"):
        gd = gd.set_index("interval_start_utc")
        frame = lake.point_in_time(decision, 96, sp=sp)
        assert len(frame) == 96 and (frame["max_published_utc"] <= decision).all()
        rep = _compare(frame, gd)
        assert not _bad(rep), f"{sp} {decision}: {_bad(rep)}"
        worst = max(worst, max(r["max_abs"] for r in rep.values()))
    assert worst <= TOL_ABS


@pytest.mark.parametrize("sp", HUBS)
def test_d0_training_frame_reproduces_the_frozen_interim_frames(lake, golden, sp):
    """The rows a model is TRAINED on (training_frame, on the 4-hour grid) are the rows it was shown by the interim builder."""
    feats, _ = golden
    for decision, gd in feats[feats["sp"] == sp].groupby("decision_utc"):
        X, y, dec = lake.raw_training_frame(sp, decision, decision + pd.Timedelta(hours=1), "4h")
        assert (dec == decision).all() and len(X) == 96                    # all 96 targets have a realised price in the inputs
        X = X.set_index("interval_start_utc")
        rep = _compare(X, gd.set_index("interval_start_utc"))
        assert not _bad(rep), f"{sp} {decision}: {_bad(rep)}"


def test_nothing_published_after_the_decision_reaches_a_feature(golden):
    """The frozen inputs run past every decision (prices up to 26 h after it). Cut the lake at the decision, leave only what was public, and
    every column must be what it was with the future in the lake."""
    from dispatch.data import assumptions as A
    from dispatch.models.synthetic import STEP, SyntheticLake
    feats, inputs = golden
    sp = "HB_NORTH"
    rt = inputs[(inputs["series"] == "rt") & (inputs["key"] == sp)]
    asp = inputs[inputs["series"] == "dam_as"].pivot(index="interval_start_utc", columns="key", values="value").sort_index()
    asp.index = pd.DatetimeIndex(asp.index)
    moved = 0
    for decision, gd in feats[feats["sp"] == sp].groupby("decision_utc"):
        public = rt[pd.DatetimeIndex(rt["interval_start_utc"]) + STEP + A.RT_PRICE_PUBLISH_LAG <= decision]
        assert 0 < len(public) < len(rt), "the inputs must hold prices published after the decision, or this test proves nothing"
        s = pd.Series(public["value"].to_numpy(), index=pd.DatetimeIndex(public["interval_start_utc"]))
        cut = SyntheticLake({sp: s}, dam_as=asp)                                  # the day-ahead rows run past the decision too: D0 must ignore them by their own publish rule
        frame = cut.point_in_time(decision, 96)
        gold = gd.set_index("interval_start_utc")
        rep = _compare(frame, gold)
        assert not _bad(rep), f"{decision}: {_bad(rep)}"
        moved += 1
    assert moved == 5


def test_version_1_still_forecasts_the_frozen_numbers(lake, golden):
    """The rollback path: the registered version 1 (trained on the interim names) fed D0's frames through `compat.for_forecaster` forecasts
    what it forecast from the interim frames: P10, P50 and P90 of the RT price and the P50 of regup, regdn, rrs and nspin, to 1e-3. Skipped
    where the registry has no version 1.

    ECRS is compared only where the day-ahead ECRS price is known. Where it is not, the interim builder gave the AS view a 14-date median
    (`as_ecrs_med14`), which rt_v1 does not have (ECRS exists since June 2023: no median, no ratio, notes/d0.md), so the AS view falls back
    to its stored table; and before June 2023 the interim median was a defect (the matrix index clipped to the first ECRS date, so a
    decision in 2019 got the median of June 2023). ECRS is not an input of the RT model; it only prices the ECRS view."""
    from dispatch.features import compat
    from dispatch.registry import mlflow_registry as R
    try:
        v1 = R.default().load_version("1")
    except Exception as e:
        pytest.skip(f"no registered version 1 here ({type(e).__name__})")
    if not str(getattr(v1, "feature_set", "")).startswith("interim"):
        pytest.skip(f"version 1 is not the interim-trained model here: {v1.feature_set}")
    feats, _ = golden
    worst = 0.0
    for (sp, decision), gd in feats.groupby(["sp", "decision_utc"]):
        frame = lake.point_in_time(decision, 96, sp=sp)
        out = v1.predict(compat.for_forecaster(frame, v1), decision, settlement_point=sp).frame
        gd = gd.set_index("interval_start_utc")
        for k in ("rt_p10", "rt_p50", "rt_p90"):
            worst = max(worst, float(np.abs(out[k].loc[gd.index].to_numpy() - gd[f"v1_{k}"].to_numpy(dtype=float)).max()))
        for p in PRODUCTS:
            a, b = out[f"as_{p}_p50"].loc[gd.index].to_numpy(), gd[f"v1_as_{p}_p50"].to_numpy(dtype=float)
            if p == "ecrs":                       # see the docstring: only where the day-ahead ECRS price itself is known
                known = gd["as_ecrs_dam"].notna().to_numpy()
                a, b = a[known], b[known]
            if len(a):
                worst = max(worst, float(np.abs(a - b).max()))
    assert worst < 1e-3, worst


# ------------------------------------------------------------------------------------------------ D0's builder on the real lake
def _real_lake_available() -> bool:
    return (REAL_HOME / "lake" / "parsed" / "rt_spp_hist").exists() and (REAL_HOME / "lake" / "parsed" / "dam_as_hist").exists()


needs_real = pytest.mark.skipif(not _real_lake_available(), reason="needs the real lake (DISPATCH_REAL_HOME)")


def real_report() -> dict:
    """Subprocess side: D0's `point_in_time` on the REAL lake at every golden decision, against the fixture."""
    from dispatch.features import build
    feats, _ = _golden()
    rows, worst, bad = 0, 0.0, {}
    for (sp, decision), gd in feats.groupby(["sp", "decision_utc"]):
        frame = build.point_in_time(sp, decision, 96)
        rep = _compare(frame, gd.set_index("interval_start_utc"))
        rows += len(gd)
        worst = max(worst, max(r["max_abs"] for r in rep.values()))
        for c, r in _bad(rep).items():
            bad[f"{sp} {decision} {c}"] = r
    return {"rows": rows, "worst": worst, "bad": bad}


@needs_real
def test_the_real_lake_reproduces_the_frozen_interim_frames():
    env = {k: v for k, v in os.environ.items() if k != "PYTEST_CURRENT_TEST"}
    env.update(PYTHONIOENCODING="utf-8", MLFLOW_DISABLE_AGENT_HINT="1", DISPATCH_HOME=str(REAL_HOME))      # the REAL lake, never a test's temp home
    r = subprocess.run([sys.executable, str(Path(__file__).resolve()), "real"], capture_output=True, text=True, env=env, cwd=str(PROJECT), timeout=3600)
    assert r.returncode == 0, (r.stderr or r.stdout)[-3000:]
    rep = json.loads(next(x for x in r.stdout.splitlines() if x.startswith("REPORT="))[len("REPORT="):])
    assert rep["rows"] == 1920 and not rep["bad"] and rep["worst"] <= TOL_ABS, rep
    print(f"\nreal lake: {rep['rows']} rows, max abs difference over 60 columns = {rep['worst']}")


if __name__ == "__main__":                                  # the subprocess side of the real-lake test
    sys.path.insert(0, str(PROJECT))
    if sys.argv[1] == "real":
        print("REPORT=" + json.dumps(real_report()))
