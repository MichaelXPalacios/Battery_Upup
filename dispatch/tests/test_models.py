"""D1a: quantile model, baselines, purged walk-forward validation, the training pipeline, decision value.

Synthetic data only; no ERCOT files and no MLflow store are touched (the registry has its own tests). Features come from D0's point-in-time
builder over a synthetic lake (`models/synthetic.SyntheticLake`): since 11 Oct 2026 the models are trained on `rt_v1`, and D1a's interim
builder, which these tests used to exercise, is gone. What its tests checked is covered by `tests/test_leakage.py` (D0's builder: publication
rules, canaries, an independent oracle for the 41 columns D1a specified, the worked example of notes/d1a.md section 10.3) and by
`tests/test_rt_v1_1_equivalence.py` (D0's builder reproduces the numbers the interim builder gave, frozen as a fixture).
"""
import json

import numpy as np
import pandas as pd
import pytest

from dispatch import config, paths
from dispatch.contracts import ForecastFrame
from dispatch.models import baselines as B
from dispatch.models import cv
from dispatch.models import quantile as Q
from dispatch.models import synthetic as S

UTC = "UTC"


# ================================================================================================ the synthetic lake
def test_synthetic_lake_gives_point_in_time_frames_and_realised_labels():
    X, y, d, lake = S.synthetic_training(days=80)
    assert len(X) == len(y) == len(d) > 10_000
    assert (X["max_published_utc"] <= d).all()                       # D0's leakage guarantee holds on the synthetic lake
    assert (X.index >= d.dt.floor("15min")).all()                    # targets start at or after the decision block
    hz = ((X.index - d.dt.floor("15min")) // pd.Timedelta("15min") + 1).to_numpy()
    assert (hz == X["horizon"].to_numpy()).all()
    j = np.array([lake.pos(t) for t in X.index[:500]])
    np.testing.assert_allclose(lake.values[j], y.to_numpy()[:500])   # y is the price of the target interval itself
    assert X.attrs["settlement_point"] == "HB_TEST"
    frame = lake.point_in_time(lake.ts(3000) + pd.Timedelta("7min"), 96)       # the frame the online store holds: any second, 96 rows
    assert len(frame) == 96 and frame["asof_utc"].iloc[0] == lake.ts(3000) + pd.Timedelta("7min") and (frame["max_published_utc"] <= frame["asof_utc"]).all()


def test_arena_profile_feature_matches_arena_history_profile():
    """rt_prof14_dt is `arena.algorithms.History.profile(weekend, 14)` at the target time of day; the ArenaProfile baseline
    is the arena Forecaster's blend 0.5 profile + 0.3 last day + 0.2 week ago (before its swing rescale)."""
    paths.use_arena()
    from arena.algorithms import History
    rng = np.random.default_rng(11)
    n_days = 45
    # start at local midnight (06:00Z, CST, no DST change in Jan-Feb) on a Monday so each local day is 96 grid steps
    idx = pd.date_range("2025-01-06 06:00", periods=n_days * 96, freq="15min", tz=UTC)
    vals = rng.gamma(3.0, 12.0, len(idx)) + 40 * np.sin(np.arange(len(idx)) / 96 * 2 * np.pi)
    lake = S.SyntheticLake("HB_TEST", pd.Series(vals, index=idx))
    daily = vals.reshape(n_days, 96)
    weekend = np.array([(d % 7) >= 5 for d in range(n_days)])
    for day in (30, 33):                                              # a Wednesday and a Saturday
        buf = History(daily[:day, None, :], weekend[:day], H=63)
        prof = buf.profile(bool(weekend[day]), 14)[0]
        last, wk_ago = buf.last(1)[0][0], buf.week_ago()[0]
        arena_f = 0.5 * prof + 0.3 * last + 0.2 * wk_ago
        decision = lake.ts(96 * day + 1)                              # 00:15 local: all of day-1 is public, K = 23:45
        X = lake.point_in_time(decision, 90)
        tau = (96 * day + 1 - 96 * day) + np.arange(90)               # time-of-day index of each target
        np.testing.assert_allclose(X["rt_prof14_dt"].to_numpy(), prof[tau], rtol=1e-4)
        base = B.ArenaProfile()
        np.testing.assert_allclose(base.point(X), arena_f[tau], rtol=1e-4)
        assert bool(X["is_weekend"].iloc[0]) == bool(weekend[day])


# ================================================================================================ the quantile model
def _hetero_forecaster(normalised: bool, seed=0):
    X, y = S.hetero_regression(26000, seed)
    n = 20000
    spec = Q.ModelSpec(feature_names=("x1", "x2", "vol", "horizon"), scale_cols=("vol",) if normalised else (),
                       anchor_col=None, weight_pow=1.0 if normalised else 0.0, n_estimators=120,
                       params=dict(num_leaves=15, min_data_in_leaf=100, learning_rate=0.08, max_bin=63))
    m = Q.SPModel.fit(X.iloc[:n], y.iloc[:n].to_numpy(), spec, num_threads=4)
    return m, X.iloc[n:], y.iloc[n:]


@pytest.mark.parametrize("normalised", [False, True])
def test_coverage_on_heteroscedastic_data(normalised):
    m, Xte, yte = _hetero_forecaster(normalised)
    q = m.quantiles(Xte)
    met = cv.quantile_metrics(yte.to_numpy(), q)
    assert 0.76 <= met["coverage_p10_p90"] <= 0.84, met                        # nominal 80%
    assert abs(met["hit_p10"] - 0.1) < 0.03 and abs(met["hit_p50"] - 0.5) < 0.03 and abs(met["hit_p90"] - 0.9) < 0.03
    # heteroscedastic: the band is wider where the noise is bigger
    wide = (q[:, 2] - q[:, 0])
    assert wide[Xte["vol"].to_numpy() > 4].mean() > 1.8 * wide[Xte["vol"].to_numpy() < 2].mean()
    assert wide[Xte["horizon"].to_numpy() > 80].mean() > 1.2 * wide[Xte["horizon"].to_numpy() < 15].mean()


def test_quantile_ordering_is_enforced_even_when_boosters_cross():
    m, Xte, _ = _hetero_forecaster(False)
    q = m.quantiles(Xte.iloc[:2000])
    assert (q[:, 0] <= q[:, 1]).all() and (q[:, 1] <= q[:, 2]).all()
    crossed = np.array([[5.0, 3.0, 4.0], [1.0, 2.0, 0.0]])
    out = Q.fix_crossing(crossed)
    assert (out[:, 0] <= out[:, 1]).all() and (out[:, 1] <= out[:, 2]).all()
    assert np.allclose(np.sort(crossed, 1), out)                                # a rearrangement: same numbers
    wild = Xte.iloc[:50].copy()
    wild["vol"] = 1e6
    qq = m.quantiles(wild)
    assert qq.max() <= Q.PRICE_CAP_USD and qq.min() >= Q.PRICE_FLOOR_USD


@pytest.fixture(scope="module")
def tiny():
    return S.fit_tiny_forecaster()


def _one_decision(X, d, k=40):
    t = pd.DatetimeIndex(d).unique()[k]
    sub = X[d.to_numpy() == t].copy()
    sub.attrs["settlement_point"] = "HB_TEST"
    return t, sub


def test_predict_returns_a_valid_forecast_frame(tiny):
    fc, X, y, d, h = tiny
    t, sub = _one_decision(X, d)
    out = fc.predict(sub, t)
    assert isinstance(out, ForecastFrame)
    assert out.settlement_point == "HB_TEST" and out.issued_utc == t and out.model_version == "unregistered"
    f = out.frame
    assert list(f.columns[:3]) == ["rt_p10", "rt_p50", "rt_p90"]
    assert f.index.equals(sub.index) and f.index.tz is not None
    assert len(f) == 96 and np.isfinite(f.to_numpy()).all()
    assert (f["rt_p10"] <= f["rt_p50"]).all() and (f["rt_p50"] <= f["rt_p90"]).all()
    assert out.max_feature_published_utc == sub["max_published_utc"].max() <= out.issued_utc
    for p in ("regup", "regdn", "rrs", "ecrs", "nspin"):
        assert f"as_{p}_p50" in f.columns and (f[f"as_{p}_p50"] >= 0).all()
    pv = out.price_view("p50")
    assert "rt_usd_per_mwh" in pv.columns and "as_regup_usd_per_mw_h" in pv.columns
    np.testing.assert_allclose(pv["rt_usd_per_mwh"], f["rt_p50"])
    assert set(f.attrs["as_source"]) == {"table"}                      # no AS columns in the features: the stored table


def test_forecast_beats_a_flat_guess_on_synthetic_prices(tiny):
    fc, X, y, d, h = tiny
    sub = X.iloc[-9600:]
    q = fc.models["HB_TEST"].quantiles(sub)
    mae_model = np.abs(y.iloc[-9600:].to_numpy() - q[:, 1]).mean()
    mae_flat = np.abs(y.iloc[-9600:].to_numpy() - np.median(y.to_numpy())).mean()
    assert mae_model < mae_flat


def test_predict_refuses_features_published_after_the_issue_time(tiny):
    fc, X, y, d, h = tiny
    t, sub = _one_decision(X, d)
    late = sub.copy()
    late["max_published_utc"] = late["max_published_utc"] + pd.Timedelta("16min")      # newest price is after the decision
    with pytest.raises(Q.LeakageError):
        fc.predict(late, t)
    ok = fc.predict(sub, t + pd.Timedelta("1s"))                                         # issued later: fine
    assert ok.max_feature_published_utc <= ok.issued_utc
    # one stray row from the future is enough
    one = sub.copy()
    one.loc[one.index[-1], "max_published_utc"] = t + pd.Timedelta("1min")
    with pytest.raises(Q.LeakageError):
        fc.predict(one, t)
    # no proof of point-in-timeness at all -> no forecast
    bare = sub.drop(columns=["max_published_utc"])
    with pytest.raises(Q.MissingFeaturesError):
        fc.predict(bare, t)
    bare.attrs["max_published_utc"] = t - pd.Timedelta("30min")
    assert fc.predict(bare, t).max_feature_published_utc == t - pd.Timedelta("30min")


def test_predict_fails_closed(tiny):
    fc, X, y, d, h = tiny
    t, sub = _one_decision(X, d)
    with pytest.raises(Q.MissingFeaturesError):
        fc.predict(sub.drop(columns=["rt_prof7_med"]), t)
    nul = sub.copy()
    nul["rt_last"] = np.nan
    with pytest.raises(Q.MissingFeaturesError):
        fc.predict(nul, t)
    with pytest.raises(Q.UnknownSettlementPointError):
        fc.predict(sub, t, settlement_point="HB_ELSEWHERE")
    with pytest.raises(Q.MissingFeaturesError):
        fc.predict(sub.iloc[0:0], t)
    with pytest.raises(ValueError):
        fc.predict(sub, t.tz_localize(None))                                              # naive issue time
    bad = sub.copy()
    bad["horizon"] = 97
    with pytest.raises(Q.MissingFeaturesError):
        fc.predict(bad, t)
    nonsp = sub.copy()
    nonsp.attrs.clear()
    nonsp.attrs["settlement_point"] = None
    assert fc.predict(nonsp, t).settlement_point == "HB_TEST"                            # single-point bundle: unambiguous


def test_predict_accepts_d0_shaped_frames(tiny):
    """D0's point_in_time frame carries settlement_point / asof_utc / max_published_utc as COLUMNS (no attrs)."""
    fc, X, y, d, h = tiny
    t, sub = _one_decision(X, d)
    d0 = sub.copy()
    d0.attrs.clear()
    d0["settlement_point"] = "HB_TEST"
    d0["asof_utc"] = t
    out = fc.predict(d0, t)                                               # sp from the column, no keyword
    assert out.settlement_point == "HB_TEST"
    mixed = d0.copy()
    mixed.iloc[0, mixed.columns.get_loc("settlement_point")] = "HB_OTHER"
    assert Q.resolve_sp(mixed, "HB_TEST", ["HB_TEST"]) == "HB_TEST"       # explicit wins
    two = Q.QuantileForecaster({"HB_TEST": fc.models["HB_TEST"], "HB_ALSO": fc.models["HB_TEST"]})
    with pytest.raises(Q.MissingFeaturesError, match="settlement point unknown"):
        two.predict(mixed, t)                                             # two points in the frame, none requested: refuse
    assert two.predict(d0, t).settlement_point == "HB_TEST"


def test_horizon_is_derived_from_the_issue_time_when_absent(tiny):
    fc, X, y, d, h = tiny
    t, sub = _one_decision(X, d)
    a = fc.predict(sub, t).frame
    b = fc.predict(sub.drop(columns=["horizon"]), t).frame
    pd.testing.assert_frame_equal(a, b)


def test_bundle_round_trip_is_exact(tiny, tmp_path):
    from dispatch.models.bundle import bundle_sha256, load_forecaster
    fc, X, y, d, h = tiny
    t, sub = _one_decision(X, d)
    fc.save(tmp_path / "b1")
    fc2 = load_forecaster(tmp_path / "b1", "7")
    assert fc2.version == "7" and fc2.settlement_points == fc.settlement_points
    pd.testing.assert_frame_equal(fc.predict(sub, t).frame, fc2.predict(sub, t).frame)
    fc2.save(tmp_path / "b2")
    assert bundle_sha256(tmp_path / "b1") == bundle_sha256(tmp_path / "b2")             # deterministic, so the registry can verify it


def test_conformal_calibration_moves_coverage_toward_nominal():
    m, Xte, yte = _hetero_forecaster(False)
    half = len(Xte) // 2
    before = cv.quantile_metrics(yte.iloc[half:].to_numpy(), m.quantiles(Xte.iloc[half:]))
    off = m.calibrate(Xte.iloc[:half], yte.iloc[:half].to_numpy())
    after = cv.quantile_metrics(yte.iloc[half:].to_numpy(), m.quantiles(Xte.iloc[half:]))
    assert set(off) == {"p10", "p50", "p90"}
    assert abs(after["coverage_p10_p90"] - 0.8) <= abs(before["coverage_p10_p90"] - 0.8) + 0.02


def test_as_price_model_prefers_published_day_ahead_then_recent_median():
    idx = pd.date_range("2025-03-01", periods=24 * 20, freq="1h", tz=UTC)
    hist = pd.DataFrame({"regup": np.arange(len(idx)) % 24 * 0.1 + 1.0, "RRS": 2.0}, index=idx)
    m = Q.ASPriceModel.fit(hist, idx[-1] + pd.Timedelta("1h"), window_days=14)
    assert m.products == ("regup", "rrs")
    target = pd.date_range("2025-03-21", periods=4, freq="15min", tz=UTC)
    out = m.predict(target)
    assert out.attrs["as_source"] == ["table"] and (out["as_rrs_p50"] == 2.0).all()
    feats = pd.DataFrame({"as_regup_dam": [9.0, np.nan, np.nan, np.nan], "as_regup_med14": [5.0, 5.0, np.nan, np.nan]}, index=target)
    out = m.predict(target, feats)
    assert out["as_regup_p50"].iloc[0] == 9.0 and out["as_regup_p50"].iloc[1] == 5.0
    assert out["as_regup_p50"].iloc[2] == m.table["regup"][target.tz_convert("America/Chicago").hour[2]]
    d0 = pd.DataFrame({"dam_as_regup": [7.0, np.nan, np.nan, np.nan]}, index=target)               # D0's column name for the same thing
    assert m.predict(target, d0)["as_regup_p50"].iloc[0] == 7.0
    with pytest.raises(ValueError):
        Q.ASPriceModel.fit(hist, idx[0])                                                     # nothing before the first row


# ================================================================================================ baselines
def test_seasonal_naive_and_fallbacks(tiny):
    fc, X, y, d, h = tiny
    t, sub = _one_decision(X, d)
    b1, b7 = B.make_baseline("seasonal_naive_1d"), B.make_baseline("seasonal_naive_7d")
    p1, p7 = b1.point(sub), b7.point(sub)
    np.testing.assert_allclose(p1[:95], sub["rt_lag_1d"].to_numpy()[:95])
    assert p1[95] == pytest.approx(sub["rt_lag_2d"].iloc[95])                                # yesterday not public at h=96
    np.testing.assert_allclose(p7, sub["rt_lag_7d"].to_numpy())
    assert b1.version == "baseline/seasonal_naive_1d"


def test_baselines_are_forecasters_with_calibrated_bands(tiny, tmp_path):
    fc, X, y, d, h = tiny
    t, sub = _one_decision(X, d)
    for name in B.BASELINES:
        b = B.make_baseline(name).fit("HB_TEST", X, y)
        out = b.predict(sub, t, settlement_point="HB_TEST")
        f = out.frame
        assert isinstance(out, ForecastFrame) and out.max_feature_published_utc <= t
        assert (f["rt_p10"] <= f["rt_p50"]).all() and (f["rt_p50"] <= f["rt_p90"]).all()
        q = b.quantiles(X, "HB_TEST")
        cover = float(np.mean((y.to_numpy() >= q[:, 0]) & (y.to_numpy() <= q[:, 2])))
        assert 0.72 <= cover <= 0.88, (name, cover)                                         # in-sample calibration of the band
        late = sub.copy()
        late["max_published_utc"] = t + pd.Timedelta("1min")
        with pytest.raises(Q.LeakageError):
            b.predict(late, t)
        b.save(tmp_path / name)
        from dispatch.models.bundle import load_forecaster
        b2 = load_forecaster(tmp_path / name, "3")
        pd.testing.assert_frame_equal(b2.predict(sub, t, settlement_point="HB_TEST").frame, f)


# ================================================================================================ purged walk-forward CV
def _rows(start="2018-01-01", end="2021-01-01", every="4h", horizon=96):
    dec = pd.date_range(start, end, freq=every, tz=UTC, inclusive="left")
    d = np.repeat(dec.to_numpy(), horizon)
    h = np.tile(np.arange(1, horizon + 1), len(dec))
    s = pd.DatetimeIndex(pd.to_datetime(d, utc=True))
    return s, s + pd.to_timedelta((h - 1) * 15, unit="m"), h


def test_walk_forward_folds_have_no_train_validation_overlap_inside_the_purge_gap():
    d, s, h = _rows()
    folds = cv.walk_forward_folds("2020-03-01", "2020-12-01", span="60D", train_start="2018-01-01")
    assert len(folds) >= 4 and folds[0].val_start == pd.Timestamp("2020-03-01", tz=UTC)
    assert all(f2.val_start == f1.val_end for f1, f2 in zip(folds, folds[1:]))               # contiguous, forward in time
    for f in folds:
        info = cv.assert_no_overlap(f, d, s)
        tr, va = cv.fold_masks(f, d, s)
        assert not (tr & va).any()
        assert (s[tr] + cv.STEP).max() <= f.val_start - f.embargo                              # no training label inside validation
        assert d[va].min() - d[tr].max() >= f.purge + f.embargo >= pd.Timedelta(hours=48)
        assert info["gap_label"] >= f.embargo
        # the purge is what makes this true: without it labels of late training rows reach into the validation window
        naive = np.asarray(d < f.val_start)
        assert (s[naive] + cv.STEP).max() > f.val_start
        # training grows with the fold; validation rows are all inside the window
        assert d[va].min() >= f.val_start and d[va].max() < f.val_end
    sizes = [int(cv.fold_masks(f, d, s)[0].sum()) for f in folds]
    assert sizes == sorted(sizes) and len(set(sizes)) == len(sizes)


def test_purge_shorter_than_the_forecast_horizon_is_rejected():
    with pytest.raises(ValueError, match="purge"):
        cv.walk_forward_folds("2020-03-01", "2020-06-01", purge="6h", train_start="2018-01-01")
    with pytest.raises(ValueError, match="training data"):
        cv.walk_forward_folds("2018-02-01", "2018-06-01", train_start="2018-01-01", min_train="365D")


def test_cross_validate_collects_disjoint_out_of_fold_rows():
    d, s, h = _rows("2019-01-01", "2020-12-01")
    y = np.sin(np.arange(len(d)) / 500.0) * 10 + 30
    folds = cv.walk_forward_folds("2020-05-01", "2020-11-01", span="60D", train_start="2019-01-01", min_train="200D")
    seen = []

    def fit_predict(tr_i, va_i):
        assert d[tr_i].max() < d[va_i].min() - pd.Timedelta(hours=48)                          # purge respected inside the loop
        mu = float(np.median(y[tr_i]))
        seen.append(set(va_i.tolist()))
        return np.column_stack([np.full(len(va_i), mu - 5), np.full(len(va_i), mu), np.full(len(va_i), mu + 5)])

    res = cv.cross_validate(fit_predict, y, d, s, h, folds)
    assert len(res.per_fold) == len(folds) and len(res.oof) == sum(len(x) for x in seen)
    assert all(a.isdisjoint(b) for i, a in enumerate(seen) for b in seen[i + 1:])
    assert {"pinball_mean", "coverage_p10_p90", "mae_p50"} <= set(res.per_fold.columns)
    assert res.pooled["n"] == len(res.oof)


def test_metrics_known_values():
    y = np.array([0.0, 10.0, 20.0, 30.0, 200.0])
    q = np.tile(np.array([[5.0, 15.0, 25.0]]), (5, 1))
    m = cv.quantile_metrics(y, q, spike_usd=150.0)
    # pinball at alpha=0.1 for q=5: y<5 -> (1-0.1)*(5-y), else 0.1*(y-5)
    exp10 = np.mean([0.9 * 5, 0.1 * 5, 0.1 * 15, 0.1 * 25, 0.1 * 195])
    assert m["pinball_p10"] == pytest.approx(exp10)
    assert m["mae_p50"] == pytest.approx(np.mean([15, 5, 5, 15, 185]))
    assert m["coverage_p10_p90"] == pytest.approx(2 / 5)                                       # only 10 and 20 sit inside [5, 25]
    assert m["hit_p90"] == pytest.approx(3 / 5)
    assert m["spike_n"] == 1 and m["spike_mae_p50"] == pytest.approx(185.0) and m["spike_cover_p90"] == 0.0
    assert m["pinball_mean"] == pytest.approx((m["pinball_p10"] + m["pinball_p50"] + m["pinball_p90"]) / 3)
    assert cv.relative_gain(8.0, 10.0) == pytest.approx(0.2)
    by_h = cv.metrics_by_horizon(y, q, [1, 5, 20, 60, 96])
    assert list(by_h.index) == ["h1-4", "h5-16", "h17-48", "h49-96"]


# ================================================================================================ monitoring reference and calibration check
def _calib_table(days=24, sigma=10.0, seed=3, horizons=range(1, 97)):
    """A holdout-shaped table (index = target interval, decisions every 4 h, horizons 1..96) whose forecasts are the true
    conditional quantiles of the prices, i.e. an in-distribution model; `profile_median_7d_p50` is a slightly worse centre."""
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2025-01-01", periods=days * 96, freq="15min", tz="UTC")
    base = pd.Series(30 + 10 * np.sin(2 * np.pi * np.arange(len(idx)) / 96), index=idx)
    y = base + rng.normal(0, sigma, len(idx))
    rows = [(d + pd.Timedelta(minutes=15 * (h - 1)), d, h)
            for d in pd.date_range(idx[0], idx[-1] - pd.Timedelta("1D"), freq="4h") for h in horizons]
    t, d, h = zip(*rows)
    t = pd.DatetimeIndex(t)
    b = base.loc[t].to_numpy()
    tab = pd.DataFrame({"decision_utc": d, "horizon": np.array(h, dtype="int16"), "y": y.loc[t].to_numpy(),
                        "rt_p10": b - 1.2816 * sigma, "rt_p50": b, "rt_p90": b + 1.2816 * sigma,
                        "profile_median_7d_p50": b + rng.normal(0, 3, len(t))}, index=t)
    tab.index.name = "interval_start_utc"
    return tab


QC = ["rt_p10", "rt_p50", "rt_p90"]


def test_reference_metrics_pool_and_buckets_agree_and_a_horizon_mix_gets_its_own_reference():
    tab = _calib_table(days=12)
    ref = cv.reference_metrics(tab["y"], tab[QC], tab["horizon"], window={"start": "2025-01-01", "decision_every": "4h"})
    assert set(ref["pooled"]) == set(cv.REFERENCE_KEYS) and ref["window"]["decision_every"] == "4h"
    assert list(ref["by_horizon"]) == ["h1-4", "h5-16", "h17-48", "h49-96"] and set(ref["by_horizon"]["h1-4"]) == set(cv.BUCKET_KEYS)
    m = cv.quantile_metrics(tab["y"], tab[QC].to_numpy())
    assert ref["pooled"]["pinball_mean"] == pytest.approx(m["pinball_mean"], abs=1e-4) and ref["pooled"]["n"] == len(tab)
    n = {k: v["n"] for k, v in ref["by_horizon"].items()}
    assert sum(n.values()) == len(tab)
    for key in ("pinball_mean", "hit_p10", "hit_p50", "hit_p90", "mae_p50"):             # pooled = row-weighted mean of the buckets
        assert sum(ref["by_horizon"][k][key] * n[k] for k in n) / len(tab) == pytest.approx(ref["pooled"][key], abs=2e-3)
    mix = cv.reference_for_horizons(ref, tab["horizon"].to_numpy())
    assert mix["pinball_mean"] == pytest.approx(ref["pooled"]["pinball_mean"], abs=2e-3)
    assert cv.reference_for_horizons(ref, np.array([1, 2, 3]))["pinball_mean"] == ref["by_horizon"]["h1-4"]["pinball_mean"]
    assert cv.reference_for_horizons(ref)["pinball_mean"] == ref["pooled"]["pinball_mean"]                    # no horizons: pooled
    flat = cv.reference_metrics(tab["y"], tab[QC])
    assert flat["pooled"]["n"] == len(tab) and "by_horizon" not in flat


def test_calibration_check_passes_in_distribution_and_flags_bias_overconfidence_and_lost_skill():
    tab = _calib_table(days=9, seed=5)
    ref = cv.reference_metrics(tab["y"], tab[QC], tab["horizon"])
    win = tab[tab.index >= tab.index.max() - pd.Timedelta("7D")]
    q = win[QC].to_numpy()
    kw = dict(reference=ref, horizon=win["horizon"].to_numpy(), profile_p50=win["profile_median_7d_p50"].to_numpy(),
              hit_tol=0.10, pinball_max=0.25, skill_max=0.10)
    ok = cv.calibration_check(win["y"], q, **kw)
    assert ok["enough_data"] and not ok["breach"] and ok["reasons"] == [] and ok["tripped"] == [] and ok["n"] == len(win)
    assert max(ok["hit_error"].values()) < 0.05 and ok["pinball_ratio"] == pytest.approx(1.0, abs=0.1) and ok["skill_ratio"] < 1.0
    biased = cv.calibration_check(win["y"], q + 15.0, **kw)                                 # a wrong anchor: everything $15 high
    assert biased["breach"] and {"hit_p50", "pinball"} <= set(biased["tripped"]) and len(biased["reasons"]) == len(biased["tripped"])
    narrow = q.copy()                                                                      # overconfident bands: half the width
    narrow[:, 0] = q[:, 1] - 0.5 * (q[:, 1] - q[:, 0])
    narrow[:, 2] = q[:, 1] + 0.5 * (q[:, 2] - q[:, 1])
    got = cv.calibration_check(win["y"], narrow, **kw)
    assert got["breach"] and {"hit_p10", "hit_p90"} <= set(got["tripped"]) and "hit_p50" not in got["tripped"]
    off = q.copy()
    off[:, 1] += 6.0                                                                       # a centre worse than the profile median
    lost = cv.calibration_check(win["y"], off, **kw)
    assert "skill" in lost["tripped"] and lost["skill_ratio"] > 1.10
    assert "skill" not in cv.calibration_check(win["y"], off, **{**kw, "skill_max": False})["tripped"]    # rule off: not evaluated
    per_q = cv.calibration_check(win["y"], narrow, **{**kw, "hit_tol": {"p10": 0.30, "p50": 0.30, "p90": 0.02}})     # one tolerance per quantile
    assert "hit_p90" in per_q["tripped"] and "hit_p10" not in per_q["tripped"] and per_q["thresholds"]["hit_tol"]["p90"] == 0.02
    few = cv.calibration_check(win["y"][:50], q[:50], **{**kw, "horizon": win["horizon"].to_numpy()[:50],
                                                         "profile_p50": kw["profile_p50"][:50]})
    assert few["n"] == 50 and not few["enough_data"] and not few["breach"] and few["hit"] == {}
    nan = q.copy()
    nan[:100] = np.nan                                                                     # unsettled or missing rows are not scored
    assert cv.calibration_check(win["y"], nan, **kw)["n"] == len(win) - 100
    dflt = cv.calibration_check(win["y"], q)                                               # thresholds come from config
    assert dflt["thresholds"]["hit_tol"] == {k: config.CALIBRATION_HIT_TOL for k in ("p10", "p50", "p90")}
    assert dflt["thresholds"]["pinball_max"] == config.PINBALL_DEGRADATION_MAX and dflt["pinball_ratio"] is None


def test_false_breach_rate_slides_the_same_check_and_sees_a_fault_that_starts_late():
    tab = _calib_table(days=30, seed=11)
    ref = cv.reference_metrics(tab["y"], tab[QC], tab["horizon"])
    kw = dict(reference=ref, window="7D", step="1D", hit_tol=0.10, pinball_max=0.25, skill_max=0.10)
    clean = cv.false_breach_rate(tab, **kw)
    assert clean["windows"] >= 20 and clean["breach_rate"] == 0.0 and clean["episodes"] == 0 and clean["by_reason"] == {}
    late = tab.copy()
    late.loc[late.index >= pd.Timestamp("2025-01-21", tz="UTC"), QC] += 15.0                # a wrong anchor from day 20 on
    r = cv.false_breach_rate(late, **kw)
    assert r["windows"] == clean["windows"] and 0.15 < r["breach_rate"] < 0.6 and r["episodes"] == 1
    assert r["by_reason"]["hit_p50"] > 0 and r["by_reason"]["pinball"] > 0
    # the verdict of one window equals calling the check on that window's rows
    end = pd.Timestamp("2025-01-30", tz="UTC")
    rows = late[(late.index >= end - pd.Timedelta("7D")) & (late.index < end)]
    assert cv.calibration_check(rows["y"], rows[QC], reference=ref, horizon=rows["horizon"].to_numpy(), hit_tol=0.10)["breach"]
    plain = cv.false_breach_rate(tab, reference=None, window="7D", step="1D", profile_col=None, hit_tol=0.10)
    assert plain["breach_rate"] == 0.0                                                     # the hit-rate rule alone needs no reference
    off = cv.false_breach_rate(late, reference=ref, window="7D", step="1D", hit_tol=False, pinball_max=0.25, skill_max=False)
    assert 0 < off["breach_rate"] < r["breach_rate"] + 0.2 and set(off["by_reason"]) == {"pinball"}       # only the pinball rule is on


def test_forecaster_carries_holdout_metrics_per_point_and_they_survive_the_bundle(tiny, tmp_path):
    fc = tiny[0]
    tab = _calib_table(days=12)
    ref = cv.reference_metrics(tab["y"], tab[QC], tab["horizon"])
    assert fc.holdout_metrics == {}                                                       # a bundle that was not built by train.release
    with pytest.raises(KeyError):
        fc.holdout_for("HB_TEST")
    f2 = Q.QuantileForecaster(dict(fc.models), feature_set=fc.feature_set, meta={"holdout": {"HB_TEST": ref, "HB_GONE": ref}})
    hm = f2.holdout_metrics
    assert list(hm) == ["HB_TEST"] and hm["HB_TEST"]["pooled"]["pinball_mean"] == ref["pooled"]["pinball_mean"]   # only served points
    hm["HB_TEST"]["pooled"]["pinball_mean"] = -1.0
    assert f2.holdout_metrics["HB_TEST"]["pooled"]["pinball_mean"] == ref["pooled"]["pinball_mean"]            # a copy
    assert f2.holdout_for("HB_TEST")["pinball_mean"] == ref["pooled"]["pinball_mean"]
    assert f2.holdout_for("HB_TEST", np.array([2]))["hit_p50"] == ref["by_horizon"]["h1-4"]["hit_p50"]
    f2.save(tmp_path / "b")
    again = Q.QuantileForecaster.load(tmp_path / "b")
    assert again.holdout_metrics == f2.holdout_metrics and again.subset(["HB_TEST"]).holdout_metrics == f2.holdout_metrics


# ================================================================================================ decision value
def _eval_frame(prices: pd.Series, every="4h", n_days=6, forecast="perfect"):
    dec = pd.date_range(prices.index[0] + pd.Timedelta("12h"), periods=int(n_days * 24 / 4), freq=every)
    rows = []
    for t in dec:
        idx = pd.date_range(t, periods=96, freq="15min")
        p = prices.reindex(idx).to_numpy()
        if forecast == "flat":
            p = np.full(96, np.nanmedian(p))
        rows.append(pd.DataFrame({"decision_utc": t, "horizon": np.arange(1, 97), "rt_p50": p}, index=idx))
    return pd.concat(rows)


def test_decision_value_perfect_forecast_captures_everything_flat_forecast_nothing():
    prices = S.synthetic_prices(days=14, seed=2, spike_rate=0.0, amplitude=6.0)      # spread well above wear + losses
    spec = cv.default_battery()
    perfect = cv.decision_value(cv.arena_dp_plan, forecasts=_eval_frame(prices, forecast="perfect"), prices=prices, spec=spec)
    assert perfect.planner == "arena-dp" and perfect.pf_usd > 0
    assert perfect.capture == pytest.approx(1.0, abs=1e-9)                                      # same inputs, same plan
    flat = cv.decision_value(cv.arena_dp_plan, forecasts=_eval_frame(prices, forecast="flat"), prices=prices, spec=spec)
    assert flat.capture < 0.2 and flat.n_decisions == perfect.n_decisions
    assert perfect.oracle_days_usd is not None and perfect.oracle_days_usd > 0
    assert perfect.usd_per_kw_yr == pytest.approx(perfect.pf_usd_per_kw_yr)
    assert any("arena DP fallback" in n for n in perfect.notes)                                  # says which planner it used


def test_batched_arena_path_equals_calling_the_planner_decision_by_decision():
    prices = S.synthetic_prices(days=12, seed=9, spike_rate=0.002, amplitude=5.0)
    spec = cv.default_battery()
    noisy = _eval_frame(prices, n_days=4)
    noisy["rt_p50"] = noisy["rt_p50"] * (1 + 0.15 * np.sin(np.arange(len(noisy)) / 7.0))        # an imperfect forecast
    fast = cv.decision_value(cv.arena_dp_plan, forecasts=noisy, prices=prices, spec=spec)
    slow = cv.decision_value(lambda *a, **k: cv.arena_dp_plan(*a, **k), forecasts=noisy, prices=prices, spec=spec)
    assert slow.planner == "custom" and fast.planner == "arena-dp"
    assert fast.revenue_usd == pytest.approx(slow.revenue_usd, rel=1e-9, abs=1e-9)
    assert fast.pf_usd == pytest.approx(slow.pf_usd, rel=1e-9, abs=1e-9)
    assert 0.0 < fast.capture < 1.0


def test_decision_value_runs_on_the_real_lp_and_shares_the_perfect_foresight_run():
    """D1b's lp.solve is the intended planner (PRD: capture = backtest revenue / perfect-foresight revenue). Skipped if it is
    not importable; the arena DP stays the fallback."""
    lp = pytest.importorskip("dispatch.optimizer.lp")
    prices = S.synthetic_prices(days=6, seed=2, spike_rate=0.0, amplitude=6.0)
    spec = cv.default_battery()
    cache: dict = {}
    perfect = cv.decision_value(lp.solve, forecasts=_eval_frame(prices, n_days=2), prices=prices, spec=spec, pf_cache=cache)
    assert perfect.planner == "lp.solve" and perfect.pf_usd > 0 and perfect.capture == pytest.approx(1.0, abs=1e-6)
    flat = cv.decision_value(lp.solve, forecasts=_eval_frame(prices, n_days=2, forecast="flat"), prices=prices, spec=spec,
                             pf_cache=cache)
    assert len(cache) == 1 and flat.pf_usd == pytest.approx(perfect.pf_usd) and flat.capture < perfect.capture
    # the arena DP (0.125 MWh states, charging power measured on the stored side) and the continuous LP agree to a few percent
    dp = cv.decision_value(cv.arena_dp_plan, forecasts=_eval_frame(prices, n_days=2), prices=prices, spec=spec)
    assert dp.pf_usd > 0 and abs(dp.pf_usd / perfect.pf_usd - 1.0) < 0.10
    name, fn = cv.default_planner()
    assert name == "lp.solve" and fn is lp.solve


def test_decision_value_accepts_any_optimizer_shaped_planner_and_reports_the_default():
    prices = S.synthetic_prices(days=10, seed=4, spike_rate=0.0, amplitude=6.0)
    calls = []

    def idle(spec, price_frame, soc0_mwh, *, mode, allow_as=True, **kw):
        calls.append((mode, allow_as))
        from dispatch.contracts import Plan
        fr = pd.DataFrame({"charge_mw": 0.0, "discharge_mw": 0.0, "soc_end_mwh": soc0_mwh, "energy_usd": 0.0, "as_usd": 0.0,
                           "wear_usd": 0.0}, index=price_frame.index)
        return Plan(spec.asset_id, mode, "idle", "optimal", 0.0, "", fr)

    dv = cv.decision_value(idle, forecasts=_eval_frame(prices, n_days=3), prices=prices)
    assert dv.revenue_usd == 0.0 and dv.pf_usd == 0.0 and np.isnan(dv.capture)               # a planner that never trades has nothing to capture
    assert {m for m, _ in calls} == {"forecast", "pf"} and all(a is False for _, a in calls)
    name, fn = cv.default_planner()
    assert name == "lp.solve" or name.startswith("arena-dp")
    with pytest.raises(KeyError):
        cv.decision_value(idle, forecasts=_eval_frame(prices, n_days=3).drop(columns=["rt_p50"]), prices=prices)


# ================================================================================================ the training pipeline
def _synthetic_cfg(tmp_path, **kw):
    from dispatch.models import train as T
    lake = S.synthetic_history("HB_TEST", days=200, seed=1, amplitude=3.0)
    base = dict(sps=("HB_TEST",), train_start=str(lake.ts(96 * 20)), holdout_start=str(lake.ts(96 * 150)),
                holdout_end=str(lake.ts(96 * 190)), cv_start=str(lake.ts(96 * 120)), cv_span="15D", cv_min_train="20D",
                tune_folds=1, n_trials=2, tune_timeout_s=240, num_threads=4, include_as=False,
                calib_days=10, fit_every="8h", fit_horizon_stride=4, cv_every="8h", cv_horizon_stride=4, eval_every="8h",
                eval_decisions_for_value="8h", frames_provider=lake.frames_provider, prices_provider=lake.prices_provider,
                work_dir=str(tmp_path / "work"))
    base.update(kw)
    return T.TrainConfig(**base), lake


def test_train_pipeline_runs_end_to_end_and_logs_everything(tmp_path):
    from dispatch.models import train as T
    from dispatch.registry import mlflow_registry as R
    cfg, lake = _synthetic_cfg(tmp_path)
    reg = R.Registry(tracking_uri="sqlite:///" + (tmp_path / "mlflow.db").as_posix(), artifact_root=tmp_path / "art",
                     cache_dir=tmp_path / "cache")
    res = T.run_sp(cfg, "HB_TEST")
    assert res.source == T.FEATURE_SET_LABEL == "rt_v1/1.1.0" and res.model.spec.anchor_col == "rt_prof7_med"
    assert res.model.spec.feature_names == T.RT_FEATURES and res.model.spec.required_nonnull == T.CORE_NONNULL      # trained on D0's own names
    pooled = res.holdout["pooled"]
    assert set(pooled) == {"model", *T.BASELINE_NAMES}
    for n, m in pooled.items():
        assert m["n"] > 1000 and 0 < m["coverage_p10_p90"] <= 1 and m["pinball_mean"] > 0
    assert set(res.cv) == {"model", *T.BASELINE_NAMES} and len(res.cv["model"]["per_fold"]) == 2
    assert res.gains["serving_consistency"] < 1e-3            # vectorised evaluation == the real Forecaster.predict path
    assert res.gains["calibration"] and "p90" in res.gains["calibration"]
    assert set(res.holdout["value"]) == {"model", *T.BASELINE_NAMES}
    assert res.forecasts is not None and {"rt_p10", "rt_p50", "rt_p90", "y", "decision_utc", "horizon"} <= set(res.forecasts.columns)
    assert (res.forecasts["rt_p10"] <= res.forecasts["rt_p50"]).all() and (res.forecasts["rt_p50"] <= res.forecasts["rt_p90"]).all()
    # no holdout decision is inside the training window, and the purge gap is respected
    assert res.forecasts["decision_utc"].min() >= pd.Timestamp(cfg.holdout_start)
    # MLflow: parent run with identity tags, trials as child runs, tables attached
    assert T.log_result(reg, cfg, res) == res.run_id and res.run_id
    run = reg.client.get_run(res.run_id)
    assert run.info.status == "FINISHED" and run.data.tags["settlement_points"] == "HB_TEST" and run.data.tags["data.sha256"]
    assert run.data.metrics["holdout.HB_TEST.pinball_mean"] == pytest.approx(pooled["model"]["pinball_mean"])
    assert "baseline.arena_profile.holdout.HB_TEST.mae_p50" in run.data.metrics
    kids = reg.client.search_runs([reg._experiment_id()], f"tags.`mlflow.parentRunId` = '{res.run_id}'")
    assert len(kids) == 2 and all(k.data.tags["stage"] == "optuna_trial" for k in kids)
    arts = {a.path for a in reg.client.list_artifacts(res.run_id, "tables")}
    assert "tables/holdout_forecasts.parquet" in arts and "tables/optuna_trials.parquet" in arts
    # checkpoint round trip, then release: the registry's own gates decide the alias
    T.save_checkpoint(cfg, res)
    summary, model, baselines = T.load_checkpoint(cfg, "HB_TEST")
    assert summary["sp"] == "HB_TEST" and set(baselines) == set(T.BASELINE_NAMES)
    q0, q1 = res.model.quantiles(_hold_x(cfg)), model.quantiles(_hold_x(cfg))
    np.testing.assert_allclose(q0, q1)
    rep = T.release(cfg, reg, promote="research")
    if res.passes:
        assert rep["version"] and rep["promoted"] == "research" and reg.load("research").settlement_points == ("HB_TEST",)
        hm = reg.load("research").holdout_metrics["HB_TEST"]                 # the monitor's reference travels with the version
        assert hm["pooled"]["n"] == len(res.forecasts) and hm["pooled"]["pinball_mean"] == pytest.approx(pooled["model"]["pinball_mean"], rel=1e-3)
        assert set(hm["by_horizon"]) <= {"h1-4", "h5-16", "h17-48", "h49-96"} and hm["window"]["decision_every"] == cfg.eval_every
        assert reg.holdout_metrics(rep["version"])["HB_TEST"] == hm
    else:
        assert rep["version"] is None and rep["failing"] == ["HB_TEST"]


def _hold_x(cfg):
    from dispatch.models import train as T
    return T.build_frame(cfg, "HB_TEST", cfg.holdout_start, cfg.holdout_end, "24h", offset="2h").X


def test_release_registers_and_the_registry_gate_refuses_a_model_that_does_not_beat_the_baselines(tmp_path):
    from dispatch.models import train as T
    from dispatch.registry import mlflow_registry as R
    cfg, lake = _synthetic_cfg(tmp_path, params={"params": {"num_leaves": 7, "min_data_in_leaf": 50, "learning_rate": 0.1,
                                                            "max_bin": 31, "bagging_freq": 1}, "n_estimators": 8,
                                                "weight_pow": 0.0})
    reg = R.Registry(tracking_uri="sqlite:///" + (tmp_path / "mlflow.db").as_posix(), artifact_root=tmp_path / "art",
                     cache_dir=tmp_path / "cache")
    res = T.run_sp(cfg, "HB_TEST")
    assert res.optuna_info == {"skipped": True} and res.trials is None
    T.log_result(reg, cfg, res)
    T.save_checkpoint(cfg, res, "t2")
    p = T.work_dir(cfg, "t2") / "HB_TEST" / "result.json"
    d = json.loads(p.read_text(encoding="utf-8"))
    d["passes"] = True                                          # force the bundle through to the registry's own gate
    p.write_text(json.dumps(d), encoding="utf-8")
    rep = T.release(cfg, reg, tag="t2", promote="research")
    assert rep["version"] is not None and rep["passing"] == ["HB_TEST"]
    kinds = {r["kind"] for r in reg.versions()}
    assert kinds == {"lightgbm_quantile", "baseline"} and len(reg.versions()) == 1 + len(T.BASELINE_NAMES)
    tab = reg.holdout_forecasts(rep["version"], "HB_TEST")                                     # D1b's walk-forward forecasts
    assert {"decision_utc", "horizon", "y", "rt_p10", "rt_p50", "rt_p90", "arena_profile_p50"} <= set(tab.columns)
    assert len(tab) == len(res.forecasts) and tab["decision_utc"].min() >= pd.Timestamp(cfg.holdout_start)
    with pytest.raises(R.NoModelError):
        reg.holdout_forecasts(rep["version"], "HB_OTHER")
    chk = reg.check_promotion(rep["version"], "research")
    assert rep["promoted"] == ("research" if chk.ok else None)                                # the gate and the report agree
    if not chk.ok:
        assert rep["refusal"] and reg.aliases().get("research") is None
        with pytest.raises(R.NoModelError):
            reg.load("research")                                                              # fail closed


# ================================================================================================ AS features and replay
def _as_fixture():
    """40 days of prices from local midnight Mon 2025-01-06 (CST) and a day-ahead AS history with a unique value per hour, in a synthetic lake."""
    idx = pd.date_range("2025-01-06 06:00", periods=40 * 96, freq="15min", tz=UTC)
    prices = pd.Series(np.random.default_rng(0).gamma(3, 10, len(idx)), index=idx)
    hours = pd.date_range("2024-12-01 06:00", "2025-03-01 06:00", freq="1h", tz=UTC)
    loc = hours.tz_convert("America/Chicago")
    day_no = (loc.tz_localize(None).normalize() - pd.Timestamp("2024-12-01")).days.to_numpy()
    val = day_no * 10.0 + loc.hour.to_numpy() * 0.1 + 1.0
    asf = pd.DataFrame({p: val * (1 + k) for k, p in enumerate(("regup", "regdn", "rrs", "ecrs", "nspin"))}, index=hours)
    return S.SyntheticLake("HB_TEST", prices, dam_as=asf), val


def test_day_ahead_as_features_follow_the_1400_publication_rule():
    """D0 publishes the day-ahead results of operating date D at 14:00 Central on D-1 (data/assumptions.py; the interim builder said 13:30, and
    ERCOT's real posting times are 12:32 to 13:55, which D0 uses where the lake has them). The planted price of a local hour is
    10 * (days since 1 Dec 2024) + 0.1 * hour + 1, twice that for the second product."""
    lake, val = _as_fixture()
    ct = "America/Chicago"
    local = lambda s_: pd.Timestamp(s_, tz=ct).tz_convert(UTC)
    planted = lambda ts_: (ts_.tz_convert(ct).tz_localize(None).normalize() - pd.Timestamp("2024-12-01")).days * 10.0 \
        + ts_.tz_convert(ct).hour * 0.1 + 1.0
    # after 14:00 on Jan 20 the whole of Jan 21 is public: its day-ahead price is the value planted for that hour
    after = lake.point_in_time(local("2025-01-20 14:30"), 96)
    tgt21 = after.index[after.index.tz_convert(ct) >= pd.Timestamp("2025-01-21 08:00", tz=ct)][0]
    assert after.loc[tgt21, "dam_as_regup"] == pytest.approx(planted(tgt21), rel=1e-5)
    assert after.loc[tgt21, "dam_as_regdn"] == pytest.approx(2 * planted(tgt21), rel=1e-5)        # second product = 2x
    assert after["dam_as_regup"].notna().all()
    # the 14 operating dates ending Jan 21 (planted day numbers 38..51 -> median 44.5)
    assert after.loc[tgt21, "dam_as_regup_med14"] == pytest.approx(44.5 * 10.0 + tgt21.tz_convert(ct).hour * 0.1 + 1.0, rel=1e-5)
    # before 14:00 the next day is NOT public: day-ahead is NaN for it and the 14-day median fills in; today's hours are known
    before = lake.point_in_time(local("2025-01-20 12:00"), 96)
    day = before.index.tz_convert(ct).normalize()
    nxt, today = before[day == pd.Timestamp("2025-01-21", tz=ct)], before[day == pd.Timestamp("2025-01-20", tz=ct)]
    assert len(nxt) and nxt["dam_as_regup"].isna().all() and nxt["dam_as_regup_med14"].notna().all()
    assert len(today) and today["dam_as_regup"].notna().all()
    # median of the same hour over the 14 operating dates ending Jan 20 (planted day numbers 37..50 -> median 43.5)
    hr = nxt.index[8].tz_convert(ct).hour
    assert nxt["dam_as_regup_med14"].iloc[8] == pytest.approx(43.5 * 10.0 + hr * 0.1 + 1.0, rel=1e-5)
    # relative price = day-ahead over the recent median (NaN while the day-ahead price is unknown)
    assert nxt["dam_as_regup_rel"].isna().all() and (after["dam_as_regup_rel"] > 0).all()
    # point in time: every row's newest source is public at the decision, and the rows that used day-ahead data say so
    assert (after["max_published_utc"] <= local("2025-01-20 14:30")).all()
    assert (before["max_published_utc"] <= local("2025-01-20 12:00")).all()
    # the rule is exact at the minute: one minute before 14:00 the next day is hidden, at 14:00 it is visible
    d21 = lambda fr: fr[fr.index.tz_convert(ct).normalize() == pd.Timestamp("2025-01-21", tz=ct)]
    early = lake.point_in_time(local("2025-01-20 13:59"), 96)
    on_time = lake.point_in_time(local("2025-01-20 14:00"), 96)
    assert len(d21(early)) and d21(early)["dam_as_regup"].isna().all()
    assert d21(on_time)["dam_as_regup"].notna().all() and (on_time["max_published_utc"] <= local("2025-01-20 14:00")).all()


def test_replay_forecast_fn_guards_the_training_period_and_returns_forecast_frames():
    from dispatch.models import replay as RP
    fc, X, y, d, lake = S.fit_tiny_forecaster("HB_TEST", n_estimators=15, days=90)
    cut = lake.ts(96 * 70)
    fc.meta["holdout_start"] = cut.isoformat()
    ff = RP.make_forecast_fn(fc, "HB_TEST", features_fn=lambda sp, asof, hz: lake.point_in_time(asof, hz))
    out = ff(cut + pd.Timedelta("3h"))
    assert out.settlement_point == "HB_TEST" and len(out.frame) == 96 and out.max_feature_published_utc <= out.issued_utc
    with pytest.raises(RP.InSampleError, match="in-sample"):
        ff(cut - pd.Timedelta("1h"))
    ok = RP.make_forecast_fn(fc, "HB_TEST", features_fn=lambda sp, asof, hz: lake.point_in_time(asof, hz),
                             allow_in_sample=True)
    assert ok(cut - pd.Timedelta("1h")).frame.shape[0] == 96
    # a table-backed forecast_fn (the training run's holdout artifact) looks the issue time up exactly
    t = pd.DatetimeIndex(d).unique()[50]
    sub = X[d.to_numpy() == t].copy()
    sub.attrs["settlement_point"] = "HB_TEST"
    q = fc.models["HB_TEST"].quantiles(sub)
    tab = pd.DataFrame({"decision_utc": t, "horizon": sub["horizon"].to_numpy(), "max_published_utc": sub["max_published_utc"].to_numpy(),
                        "rt_p10": q[:, 0], "rt_p50": q[:, 1], "rt_p90": q[:, 2]}, index=sub.index)
    tf = RP.table_forecast_fn(tab, "HB_TEST", "t")
    got = tf(t)
    assert got.max_feature_published_utc == sub["max_published_utc"].max() and len(got.frame) == 96
    with pytest.raises(KeyError):
        tf(t + pd.Timedelta("15min"))


def test_model_spec_and_as_model_survive_json():
    spec = Q.ModelSpec(feature_names=("a", "b"), anchor_col="a", scale_cols=("b", "a"), weight_pow=0.5, n_estimators=7,
                       params={"num_leaves": 9}, calibration={"p10": -0.1, "p50": 0.0, "p90": 0.2}, required_nonnull=("a",))
    back = Q.ModelSpec.from_json(json.loads(json.dumps(spec.to_json())))
    assert back == spec and back.needed_columns() == ["a", "b"]
    X = pd.DataFrame({"a": [10.0, 20.0], "b": [5.0, 1.0]})
    np.testing.assert_allclose(spec.scale(X), [max(5.0 - 10.0, 2.0), max(1.0 - 20.0, 2.0)])           # (hi - lo) floored at $2
    asm = Q.ASPriceModel({"regup": {0: 1.5, 1: 2.5}}, 14, "2026-01-01T00:00:00+00:00")
    back2 = Q.ASPriceModel.from_json(json.loads(json.dumps(asm.to_json())))
    assert back2.table == asm.table and back2.window_days == 14 and back2.fitted_to == asm.fitted_to


def test_check_columns_refuses_a_frame_that_lacks_a_column_the_model_needs():
    """There is one source, D0's builder, and no fallback: a column it cannot supply stops the run before any tuning, by name."""
    from dispatch.models import train as T
    lake = S.synthetic_history("HB_TEST", days=200, seed=1)
    cfg = T.TrainConfig(frames_provider=lake.frames_provider)
    needed = tuple(T.MODEL_FEATURES) + T.SCALES["price"] + T.SCALES["resid"] + (T.ANCHOR_COL,)
    T.check_columns(cfg, "HB_TEST", needed)                                              # D0's frame carries all of them
    with pytest.raises(KeyError, match="lacks 2 columns"):
        T.check_columns(cfg, "HB_TEST", needed + ("rt_mean_96", "tgt_hod"))              # the interim spellings are not renamed back
    with pytest.raises(ValueError, match="retired"):
        T.TrainConfig(source="interim")
    with pytest.raises(ValueError, match="retired"):
        T.TrainConfig(source="auto")
    assert T.TrainConfig().source == "d0" and T.TrainConfig(source="d0").source == "d0"


def test_build_frame_rejects_rows_published_after_their_decision():
    from dispatch.models import train as T
    X, y, d, lake = S.synthetic_training(days=60)
    bad = X.copy()
    bad.loc[bad.index[5], "max_published_utc"] = d.iloc[5] + pd.Timedelta("1min")
    cfg = T.TrainConfig(include_as=False, frames_provider=lambda sp, start, end, every, offset: (bad, y, d))
    with pytest.raises(Q.LeakageError, match="published after their decision"):
        T.build_frame(cfg, "HB_TEST", d.min(), d.max(), "4h")
    cfg = T.TrainConfig(include_as=False, frames_provider=lambda sp, start, end, every, offset: (X, y, d))
    fr = T.build_frame(cfg, "HB_TEST", d.min(), d.max(), "4h", stride=4)
    assert len(fr) < len(X) and set(((fr.horizon - 1) % 4).tolist()) == {0} and fr.source == "rt_v1/1.1.0"


def test_realised_prices_are_the_labels_the_frames_carry_and_say_where_the_history_ends(dispatch_home):
    """The price series the holdout plan is settled at (`realised_prices`) must be what `training_frame` calls y: the REAL source before the
    archive, then the latest published, so a correction counts. Written into a lake under the temp home, read back through D0's reader."""
    from dispatch.models import train as T
    lake = S.synthetic_history("HB_TEST", days=40, seed=4)
    fix = lake.series().iloc[3000:3004]
    corr = S.rt_rows("HB_TEST", fix + 10.0).assign(published_utc=lambda df: df["published_utc"] + pd.Timedelta("1h"))        # a correction
    S.write_parsed(dispatch_home.PARSED, "rt_spp_hist", pd.concat([S.rt_rows("HB_TEST", lake.series()), corr], ignore_index=True))
    cfg = T.TrainConfig()
    s = T.realised_prices(cfg, "HB_TEST", lake.start, lake.end + pd.Timedelta("1D"))
    assert s.index[0] == lake.start and s.name == "rt_usd_per_mwh"
    np.testing.assert_allclose(s.iloc[:3000].to_numpy(), lake.values[:3000])
    np.testing.assert_allclose(s.iloc[3000:3004].to_numpy(), fix.to_numpy() + 10.0)           # the later-published row wins
    assert T.history_end(s) == lake.end and s.iloc[len(lake):].isna().all()
    with pytest.raises(ValueError):
        T.history_end(pd.Series([np.nan], index=pd.DatetimeIndex(["2025-01-01"], tz=UTC)))


def test_batch_predict_matches_the_vectorised_path_row_for_row(tiny):
    fc, X, y, d, h = tiny
    sub, dsub = X.iloc[:960], d.iloc[:960]                                   # ten decisions of 96 horizons
    out = Q.batch_predict(fc, sub, dsub, settlement_point="HB_TEST")
    assert out.index.equals(sub.index) and len(out) == 960
    assert (out["decision_utc"].to_numpy() == dsub.to_numpy()).all() and (out["horizon"].to_numpy() == sub["horizon"].to_numpy()).all()
    fast = fc.models["HB_TEST"].quantiles(sub)
    np.testing.assert_allclose(out[["rt_p10", "rt_p50", "rt_p90"]].to_numpy(), fast, rtol=1e-9, atol=1e-9)
    assert {"as_regup_p50", "as_nspin_p50"} <= set(out.columns)
    with pytest.raises(ValueError):
        Q.batch_predict(fc, sub, dsub.dt.tz_localize(None), settlement_point="HB_TEST")        # naive decision times
    late = sub.copy()
    late["max_published_utc"] = late["max_published_utc"] + pd.Timedelta("1h")
    with pytest.raises(Q.LeakageError):
        Q.batch_predict(fc, late, dsub, settlement_point="HB_TEST")                             # every row passes the live checks


def test_reference_frame_is_market_state_only_and_follows_the_points_being_served(tiny):
    """D2's drift monitor reads `forecaster.reference_frame` without naming an asset and compares it with the served rows."""
    import copy
    fc, X, y, d, h = tiny
    ref = fc.reference_frame
    assert ref is not None and 0 < len(ref) <= 500
    assert not any(Q.is_calendar_feature(c) for c in ref.columns) and "rt_prof7_med" in ref.columns and "res_q90_672" in ref.columns
    assert not {"horizon", "hour_local", "qtr_hour", "dow", "month", "is_weekend", "is_holiday", "doy_sin", "doy_cos"} & set(ref.columns)   # D0's calendar names
    assert set(ref.columns) <= set(fc.models["HB_TEST"].spec.feature_names)                 # same names as the served frame
    other = copy.deepcopy(fc.models["HB_TEST"])
    shifted = X.copy()
    for c in ("rt_last", "rt_mean_24h", "rt_prof7_med"):
        shifted[c] = shifted[c] + 100.0
    other.set_reference(shifted, n=300)
    two = Q.QuantileForecaster({"HB_TEST": fc.models["HB_TEST"], "HB_ALSO": other}, feature_set=fc.feature_set)
    stacked = two.reference_frame
    assert len(stacked) == len(two.reference_for("HB_TEST")) + len(two.reference_for("HB_ALSO"))      # nothing served yet: all points
    t, sub = _one_decision(X, d)
    two.predict(sub, t, settlement_point="HB_ALSO")
    only = two.reference_frame                                                                 # now: the point that is being served
    pd.testing.assert_frame_equal(only, two.reference_for("HB_ALSO"))
    assert only["rt_prof7_med"].median() > ref["rt_prof7_med"].median() + 50
    two.predict(sub, t, settlement_point="HB_TEST")
    assert len(two.reference_frame) == len(two.reference_for("HB_TEST")) + len(two.reference_for("HB_ALSO"))


def test_drift_reference_switch_turns_the_reference_off_without_touching_per_point_access(tiny, monkeypatch):
    """DISPATCH_DRIFT_REFERENCE=off makes the drift monitor skip its check (lead's request); nothing else changes."""
    fc = tiny[0]
    monkeypatch.delenv("DISPATCH_DRIFT_REFERENCE", raising=False)
    assert fc.reference_frame is not None
    for v in ("off", "OFF", " off ", "0", "false", "no"):
        monkeypatch.setenv("DISPATCH_DRIFT_REFERENCE", v)
        assert fc.reference_frame is None
        assert fc.reference_for("HB_TEST") is not None
    monkeypatch.setenv("DISPATCH_DRIFT_REFERENCE", "on")
    assert fc.reference_frame is not None
