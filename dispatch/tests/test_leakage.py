"""FR3, the build-breaker: no feature may postdate its decision time.

A synthetic lake (written through the real parquet writer and the real dataset schemas) stands in for the parsed datasets.
Four kinds of check:

1. every row of `point_in_time` and `training_frame` has max_published_utc <= asof_utc, at random and at edge decision times;
2. an independent brute-force reference (pandas filters on `published_utc <= asof`, written separately from the builder)
   gives the same feature values, so `max_published_utc` cannot be right for the wrong reason;
3. CANARIES: rows published one minute AFTER the decision time, with absurd values, are injected into every input
   (RT price, DAM price, DAM ancillary price, load forecast, gas, weather). The builder must not use any of them;
4. the canary test is itself tested: if the builder's two visibility functions are broken, the canary check fails.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from dispatch.data import parse
from dispatch.data.times import CPT
from dispatch.features import build, sets

SP = "HB_NORTH"
STATION = "USW00003927"          # DFW, the station of LZ_NORTH / HB_NORTH
T0 = pd.Timestamp("2026-08-17T05:00:00Z")
T1 = pd.Timestamp("2026-10-13T05:00:00Z")      # 57 days of 15-minute prices: 14 days of lags + a 7-day window + the anchor behind it
WILD = 99999.0
V100_FEATURES = ("rt_last", "rt_last_age_min", "rt_mean_1h", "rt_mean_24h", "rt_std_24h", "rt_mean_7d", "rt_std_7d", "rt_lag_1d", "rt_lag_7d",
                 "hb_last", "hb_lag_1h", "hb_lag_24h", "hb_mean_24h", "hb_lag_1d", "basis_last", "dam_spp", "dam_spp_minus_rt_last",
                 "dam_as_regup", "dam_as_regdn", "dam_as_rrs", "dam_as_ecrs", "dam_as_nspin", "horizon", "hour_local", "qtr_hour", "dow", "month",
                 "is_holiday", "is_weekend", "gas_hh", "gas_hh_age_days", "wx_tmax_f", "wx_tmin_f", "wx_prcp_in", "wx_wsf2_mph", "wx_cdd", "wx_hdd",
                 "wx_age_days", "lf_system_mw", "lf_zone_mw", "lf_age_h")
# the 41 columns of notes/d1a.md section 10.2, in the spec's names
D1A_SPEC = ("rt_prof7_med", "rt_q10_672", "rt_q90_672", "res_q10_672", "res_q90_672", "res_last", "res_mad_672", "rt_frac_hi_96",
            "rt_frac_hi_672", "rt_frac_neg_672", "rt_q50_672", "res_q50_672", "res_mean_4", "res_mean_16", "res_q10_96", "rt_min_96", "rt_prof14_dt",
            "rt_lag_14d", "doy_sin", "doy_cos", "dam_as_regup_rel", "dam_as_regdn_rel", "dam_as_rrs_rel", "dam_as_nspin_rel", "dam_as_regup_med14",
            "dam_as_regdn_med14", "dam_as_rrs_med14", "dam_as_nspin_med14", "res_mean_96", "res_q50_96", "res_q90_96", "rt_prof7_mean", "rt_prof7_min",
            "rt_prof7_max", "rt_prof7_std", "rt_lag_2d", "rt_lag_3d", "rt_mean_16", "rt_max_96", "rt_spread_96", "rt_frac_neg_96")
DAM_HOUR = pd.Timestamp("2026-10-07T08:00:00Z")           # 03:00 CDT on Oct 7, inside the 24 h horizon of ASOF


# ------------------------------------------------------------------------------------------------ synthetic lake
def _write(name: str, df: pd.DataFrame) -> None:
    """Write rows into the parsed dataset's partitions (appending to a partition that exists)."""
    ds = parse.DATASETS[name]
    df = df.copy()
    df["pub_basis"] = df.get("pub_basis", "REAL")
    df["raw_file"] = df.get("raw_file", "synthetic")
    df = df[[f.name for f in ds.schema]]
    if ds.mode == "file":
        keys = df["published_utc"].dt.tz_convert(CPT).dt.strftime("%Y-%m-%d")
    else:
        keys = df["interval_start_utc"].dt.tz_convert(CPT).dt.strftime("%Y-%m-01")
    for key, part in df.groupby(keys):
        path = parse.partition_path(name, key)
        if path.exists():
            part = pd.concat([parse.read(name, date_from=key, date_to=key), part], ignore_index=True)
        part = part.sort_values(list(ds.sort_keys), kind="stable", ignore_index=True)
        parse.write_table(path, parse._to_table(part, ds, {"dispatch.dataset": name, "dispatch.group": key}))


def _ts(values) -> pd.Series:
    return pd.Series(pd.DatetimeIndex(values)).dt.tz_convert("UTC").astype("datetime64[us, UTC]")


def _local(naive: str) -> pd.Timestamp:
    return pd.Timestamp(naive).tz_localize(CPT).tz_convert("UTC")


def make_world(seed: int = 7) -> dict[str, pd.DataFrame]:
    """The synthetic truth, as DataFrames. Publish times follow the real patterns:
    RT price of an interval: end + 2 min (+ up to 20 s); DAM for delivery day D: D-1 13:10 CPT; load forecast issue: HH:30 CPT,
    covering the issue day and the next 7; gas for date D: D + 7 d 12:00 CPT; weather for day D: D + 1 d 18:00 CPT."""
    rng = np.random.default_rng(seed)
    grid = pd.date_range(T0, T1, freq="15min", inclusive="left")
    rt = []
    for sp, base in ((SP, 30.0), ("HB_BUSAVG", 28.0)):
        hours = (grid - T0).total_seconds() / 3600
        price = np.round(base + 12 * np.sin(2 * np.pi * hours / 24) + rng.normal(0, 3, len(grid)), 2)
        pub = grid + pd.Timedelta(minutes=17) + pd.to_timedelta(rng.integers(0, 20, len(grid)), unit="s")
        rt.append(pd.DataFrame({"published_utc": _ts(pub), "interval_start_utc": _ts(grid), "settlement_point": sp,
                                "sp_type": "SH" if sp == "HB_BUSAVG" else "HU", "price_usd_per_mwh": price}))
    world = {"rt_spp": pd.concat(rt, ignore_index=True)}

    hours = pd.date_range(T0 - pd.Timedelta(hours=5), T1, freq="1h", inclusive="left")          # local midnight .. (UTC 05:00 start is 00:00 CDT)
    deliv_day = hours.tz_convert(CPT).normalize()
    dam_pub = pd.DatetimeIndex([(d - pd.Timedelta(days=1) + pd.Timedelta(hours=13, minutes=10)).tz_localize(None).tz_localize(CPT).tz_convert("UTC")
                                for d in deliv_day.tz_localize(None)])
    dam_price = np.round(25 + 10 * np.sin(2 * np.pi * (hours.hour) / 24) + rng.normal(0, 2, len(hours)), 2)
    world["dam_spp"] = pd.DataFrame({"published_utc": _ts(dam_pub), "interval_start_utc": _ts(hours), "settlement_point": SP,
                                     "price_usd_per_mwh": dam_price})
    as_rows = []
    for prod in sets.DAM_AS_PRODUCTS:
        if prod == "ECRS":
            continue
        as_rows.append(pd.DataFrame({"published_utc": _ts(dam_pub), "interval_start_utc": _ts(hours), "product": prod,
                                     "mcpc_usd_per_mw_h": np.round(rng.uniform(0.1, 5, len(hours)), 2)}))
    world["dam_as"] = pd.concat(as_rows, ignore_index=True)

    issues = pd.date_range(T0.tz_convert(CPT).normalize() + pd.Timedelta(minutes=30), T1.tz_convert(CPT), freq="2h", inclusive="left")
    lf = []
    for iss in issues:
        start = iss.normalize()
        tgt = pd.date_range(start, start + pd.Timedelta(days=3), freq="1h", inclusive="left")
        base = 45000 + 8000 * np.sin(2 * np.pi * tgt.hour / 24) + rng.normal(0, 300, len(tgt))
        d = pd.DataFrame({"published_utc": _ts([iss.tz_convert("UTC")] * len(tgt)), "interval_start_utc": _ts(tgt.tz_convert("UTC")),
                          "model": "E", "in_use": True, "system_total_mw": np.round(base, 1)})
        d["north_central_mw"], d["north_mw"] = np.round(base * 0.27, 1), np.round(base * 0.03, 1)
        for c in ("coast_mw", "east_mw", "far_west_mw", "south_central_mw", "southern_mw", "west_mw"):
            d[c] = np.round(base * 0.1, 1)
        lf.append(d)
        if iss.hour == 12:                                                        # a model that is not in use must be ignored
            other = d.copy()
            other["model"], other["in_use"], other["system_total_mw"] = "A3", False, other["system_total_mw"] + 5000
            lf.append(other)
    world["load_forecast"] = pd.concat(lf, ignore_index=True)

    days = pd.date_range("2026-07-01", "2026-10-12", freq="D")
    day_start = _ts(days.tz_localize(CPT).tz_convert("UTC"))
    gas_pub = (days + pd.Timedelta(days=7, hours=12)).tz_localize(CPT).tz_convert("UTC")
    world["gas_hh"] = pd.DataFrame({"published_utc": _ts(gas_pub), "interval_start_utc": day_start, "obs_date": days.strftime("%Y-%m-%d"),
                                    "price_usd_per_mmbtu": np.round(3 + 0.05 * np.arange(len(days)) + rng.normal(0, 0.05, len(days)), 3)})
    wx_pub = (days + pd.Timedelta(days=1, hours=18)).tz_localize(CPT).tz_convert("UTC")
    tmax = np.round(90 + rng.normal(0, 3, len(days)), 1)
    world["weather_obs"] = pd.DataFrame({
        "published_utc": _ts(wx_pub), "interval_start_utc": day_start, "station": STATION, "obs_date": days.strftime("%Y-%m-%d"),
        "tmax_f": tmax, "tmin_f": tmax - 15, "prcp_in": np.round(rng.uniform(0, 0.3, len(days)), 2), "wsf2_mph": np.round(rng.uniform(10, 30, len(days)), 1),
        "awnd_mph": 8.0})
    return world


@pytest.fixture(scope="module")
def _world_dir(tmp_path_factory):
    """Build the synthetic parsed lake once; each test gets its own copy (the canary tests change it)."""
    import importlib
    import os
    from dispatch import paths
    home = tmp_path_factory.mktemp("leakhome") / "home"
    old = os.environ.get("DISPATCH_HOME")
    os.environ["DISPATCH_HOME"] = str(home)
    importlib.reload(paths)
    try:
        w = make_world()
        for name, df in w.items():
            _write(name, df)
    finally:
        if old is None:
            os.environ.pop("DISPATCH_HOME", None)
        else:
            os.environ["DISPATCH_HOME"] = old
        importlib.reload(paths)
    return home, w


@pytest.fixture()
def world(dispatch_home, _world_dir):
    import shutil
    home, w = _world_dir
    shutil.copytree(home / "lake" / "parsed", dispatch_home.PARSED, dirs_exist_ok=True)
    return w


def _ctx():
    return build.load_context(SP, T0, T1 - pd.Timedelta(days=1))


def _asofs(n=40, seed=1) -> list[pd.Timestamp]:
    rng = np.random.default_rng(seed)
    lo, hi = pd.Timestamp("2026-09-12T00:00:00Z").value // 10**9, pd.Timestamp("2026-10-10T00:00:00Z").value // 10**9
    random = [pd.Timestamp(int(s), unit="s", tz="UTC") for s in rng.integers(lo, hi, n)]
    # edges: the second before and after a DAM publication (13:10 CPT), an LF issue (HH:30 CPT) and an RT interval publication
    edge = []
    for base in ("2026-09-20 13:10:00", "2026-10-03 13:10:00", "2026-09-25 10:30:00", "2026-10-05 17:30:00"):
        t = _local(base)
        edge += [t - pd.Timedelta(seconds=1), t, t + pd.Timedelta(seconds=1)]
    rt_pub = pd.Timestamp("2026-09-30T12:15:00Z") + pd.Timedelta(minutes=17)
    edge += [rt_pub - pd.Timedelta(seconds=1), rt_pub + pd.Timedelta(seconds=21)]
    return random + edge


# ------------------------------------------------------------------------------------------------ 1. max_published <= asof
def test_every_row_published_by_the_decision_time(world):
    ctx = _ctx()
    for asof in _asofs():
        df = build.point_in_time(SP, asof, context=ctx)
        assert len(df) == 96 and df.index.is_monotonic_increasing
        assert df["max_published_utc"].notna().all()
        assert (df["max_published_utc"] <= asof).all(), f"leak at asof {asof}: {df['max_published_utc'].max()}"
        assert (df["asof_utc"] == asof).all()
        assert df.index[0] == asof.floor("15min") and (df["horizon"] == range(1, 97)).all()      # horizon 1 = the interval containing the decision


def test_training_frame_rows_never_postdate_their_decision(world):
    X, y, dec = build.training_frame(SP, pd.Timestamp("2026-09-12T00:00:00Z"), pd.Timestamp("2026-10-08T00:00:00Z"), "4h")
    assert len(X) == len(y) == len(dec) and len(X) > 10_000
    assert (X["max_published_utc"] <= X["asof_utc"]).all()
    assert (dec == X["asof_utc"]).all()
    assert (X["interval_start_utc"] >= X["asof_utc"]).all()                                  # targets are in the future of the decision
    assert y.notna().all() and X["rt_last"].notna().all()


def test_training_frame_equals_point_in_time_row_for_row(world):
    """One routine builds both, so a model sees identical features in training and at the service."""
    X, y, dec = build.training_frame(SP, pd.Timestamp("2026-10-01T00:00:00Z"), pd.Timestamp("2026-10-03T00:00:00Z"), "4h")
    ctx = _ctx()
    for asof in dec.drop_duplicates().iloc[[0, 3, 7]]:
        a = X[X["asof_utc"] == asof].set_index("interval_start_utc")[list(sets.FEATURES)]
        b = build.point_in_time(SP, asof, context=ctx).loc[a.index, list(sets.FEATURES)]
        pd.testing.assert_frame_equal(a, b, check_exact=True)


def test_features_do_not_depend_on_the_range_that_was_loaded(world):
    """Same (settlement point, decision time) -> same bits, whatever range the context covers (a training run over a few days,
    a run over months, or the service's 9-day window). pandas' running-sum rolling statistics would differ in the last bits."""
    narrow, _, _ = build.training_frame(SP, pd.Timestamp("2026-10-02T00:00:00Z"), pd.Timestamp("2026-10-04T00:00:00Z"), "4h")
    wide, _, _ = build.training_frame(SP, pd.Timestamp("2026-09-15T00:00:00Z"), pd.Timestamp("2026-10-08T00:00:00Z"), "4h")
    key = ["asof_utc", "interval_start_utc"]
    both = narrow.merge(wide[key], on=key)
    assert len(both) == len(narrow) > 500
    a = narrow.set_index(key).sort_index()
    b = wide.set_index(key).loc[a.index]
    pd.testing.assert_frame_equal(a[list(sets.FEATURES)], b[list(sets.FEATURES)], check_exact=True)
    assert a["rt_std_7d"].notna().any() and a["rt_mean_7d"].notna().any()                       # the statistics under test were computed


# ------------------------------------------------------------------------------------------------ 2. brute-force reference
def reference(world, asof: pd.Timestamp, h: int) -> dict:
    """Features of (asof, horizon h) by plain pandas filtering on published_utc <= asof. Deliberately independent of build.py."""
    t_target = asof.floor("15min") + pd.Timedelta(minutes=15 * (h - 1))
    out = {}
    rt = world["rt_spp"]
    rt = rt[(rt.settlement_point == SP) & (rt.published_utc <= asof)].sort_values("interval_start_utc")
    last = rt.iloc[-1]
    out["rt_last"] = last.price_usd_per_mwh
    out["rt_last_age_min"] = (asof - (last.interval_start_utc + pd.Timedelta(minutes=15))).total_seconds() / 60
    window = rt[rt.interval_start_utc > last.interval_start_utc - pd.Timedelta(hours=24)]
    out["rt_mean_24h"] = window.price_usd_per_mwh.mean()
    for name, lag in (("rt_lag_1d", pd.Timedelta(days=1)), ("rt_lag_7d", pd.Timedelta(days=7))):
        m = rt[rt.interval_start_utc == t_target - lag]
        out[name] = m.price_usd_per_mwh.iloc[0] if len(m) else np.nan
    hb = world["rt_spp"]
    hb = hb[(hb.settlement_point == "HB_BUSAVG") & (hb.published_utc <= asof)].sort_values("interval_start_utc")
    out["hb_last"] = hb.iloc[-1].price_usd_per_mwh
    hour = t_target.floor("1h")
    dam = world["dam_spp"]
    m = dam[(dam.interval_start_utc == hour) & (dam.published_utc <= asof)]
    out["dam_spp"] = m.price_usd_per_mwh.iloc[0] if len(m) else np.nan
    das = world["dam_as"]
    for prod in ("REGUP", "RRS"):
        m = das[(das["product"] == prod) & (das.interval_start_utc == hour) & (das.published_utc <= asof)]
        out[f"dam_as_{prod.lower()}"] = m.mcpc_usd_per_mw_h.iloc[0] if len(m) else np.nan
    gas = world["gas_hh"]
    gas = gas[gas.published_utc <= asof].sort_values("interval_start_utc")
    out["gas_hh"] = gas.iloc[-1].price_usd_per_mmbtu if len(gas) else np.nan
    out["gas_hh_age_days"] = (asof - gas.iloc[-1].interval_start_utc).total_seconds() / 86400 if len(gas) else np.nan
    wx = world["weather_obs"]
    wx = wx[(wx.published_utc <= asof) & (wx.station == STATION)].sort_values("interval_start_utc")
    out["wx_tmax_f"] = wx.iloc[-1].tmax_f if len(wx) else np.nan
    lf = world["load_forecast"]
    lf = lf[lf.in_use & (lf.published_utc <= asof)]
    if len(lf):
        issue = lf[lf.published_utc == lf.published_utc.max()]
        m = issue[issue.interval_start_utc == hour]
        out["lf_system_mw"] = m.system_total_mw.iloc[0] if len(m) else np.nan
        out["lf_age_h"] = (asof - issue.published_utc.iloc[0]).total_seconds() / 3600
    else:
        out["lf_system_mw"], out["lf_age_h"] = np.nan, np.nan
    return out


def test_builder_matches_the_brute_force_reference(world):
    ctx = _ctx()
    checked = 0
    for asof in _asofs(25, seed=3):
        df = build.point_in_time(SP, asof, context=ctx)
        for h in (1, 2, 17, 48, 95, 96):
            ref = reference(world, asof, h)
            row = df.iloc[h - 1]
            for k, v in ref.items():
                got = float(row[k])
                if np.isnan(v):
                    assert np.isnan(got), f"{k} at asof {asof} h={h}: builder {got}, reference NaN (used data it must not have seen)"
                else:
                    assert got == pytest.approx(v, rel=2e-5, abs=1e-4), f"{k} at asof {asof} h={h}: builder {got}, reference {v}"
                checked += 1
    assert checked > 1000


# ------------------------------------------------------------------------------------------------ 3. canaries
ASOF = pd.Timestamp("2026-10-06T15:07:30Z")                       # 10:07:30 CDT: tomorrow's DAM (Oct 7) is not published until 13:10 CDT


def _canary_rows(world, asof):
    late = asof + pd.Timedelta(minutes=1)
    last_interval = asof.floor("15min")                                           # the interval in progress: its real price is not public yet
    tomorrow_noon = DAM_HOUR                                                       # an hour of tomorrow: its DAM is not yet published at ASOF
    rows = {
        "rt_spp": pd.DataFrame({"published_utc": _ts([late]), "interval_start_utc": _ts([last_interval]), "settlement_point": SP,
                                "sp_type": "HU", "price_usd_per_mwh": WILD}),
        "dam_spp": pd.DataFrame({"published_utc": _ts([late]), "interval_start_utc": _ts([tomorrow_noon]), "settlement_point": SP,
                                 "price_usd_per_mwh": WILD}),
        "dam_as": pd.DataFrame({"published_utc": _ts([late]), "interval_start_utc": _ts([tomorrow_noon]), "product": "REGUP",
                                "mcpc_usd_per_mw_h": WILD}),
        "gas_hh": pd.DataFrame({"published_utc": _ts([late]), "interval_start_utc": _ts([_local("2026-10-06 00:00")]), "obs_date": "2026-10-06",
                                "price_usd_per_mmbtu": WILD}),
        "weather_obs": pd.DataFrame({"published_utc": _ts([late]), "interval_start_utc": _ts([_local("2026-10-06 00:00")]), "station": STATION,
                                     "obs_date": "2026-10-06", "tmax_f": WILD, "tmin_f": WILD, "prcp_in": WILD, "wsf2_mph": WILD, "awnd_mph": WILD}),
    }
    tgt = pd.date_range("2026-10-06", periods=24 * 8, freq="1h", tz=CPT).tz_convert("UTC")
    lf = pd.DataFrame({"published_utc": _ts([late] * len(tgt)), "interval_start_utc": _ts(tgt), "model": "E", "in_use": True,
                       "system_total_mw": WILD})
    for c in ("coast_mw", "east_mw", "far_west_mw", "north_mw", "north_central_mw", "south_central_mw", "southern_mw", "west_mw"):
        lf[c] = WILD
    rows["load_forecast"] = lf
    return rows


def _assert_no_canary(world, asof, df):
    """The builder's row at `asof` carries nothing from the canary rows."""
    assert df["max_published_utc"].max() <= asof
    vals = df[list(sets.FEATURES)].astype("float64")
    bad = vals.columns[(vals >= 90_000).any()].tolist()
    assert not bad, f"a canary value (99999) reached the features: {bad}"
    ref_before = reference(world, asof, 1)
    r = df.iloc[0]
    assert float(r["rt_last"]) == pytest.approx(ref_before["rt_last"], rel=1e-5)
    assert float(r["gas_hh"]) == pytest.approx(ref_before["gas_hh"], rel=1e-5)
    assert float(r["wx_tmax_f"]) == pytest.approx(ref_before["wx_tmax_f"], rel=1e-5)
    assert float(r["lf_system_mw"]) == pytest.approx(ref_before["lf_system_mw"], rel=1e-5)
    tomorrow = df.loc[DAM_HOUR]
    assert np.isnan(float(tomorrow["dam_spp"])) and np.isnan(float(tomorrow["dam_as_regup"]))


def test_canary_rows_published_after_the_decision_are_excluded(world):
    for name, rows in _canary_rows(world, ASOF).items():
        _write(name, rows)
    # the world the reference sees does not contain the canaries: they are future data by definition
    df = build.point_in_time(SP, ASOF)
    assert df.index[0] == pd.Timestamp("2026-10-06T15:00:00Z")
    _assert_no_canary(world, ASOF, df)
    # the canaries are really in the lake: once their publish time has passed they are used
    after = build.point_in_time(SP, ASOF + pd.Timedelta(minutes=2))
    assert float(after.iloc[0]["rt_last"]) == WILD
    assert float(after.iloc[0]["lf_system_mw"]) == WILD
    assert after.loc[DAM_HOUR, "dam_spp"] == pytest.approx(WILD, rel=1e-5) and after.loc[DAM_HOUR, "dam_as_regup"] == pytest.approx(WILD, rel=1e-5)
    # gas and weather arrive as ordered series (a day is known only once the days before it are): check them at later decisions
    assert float(build.point_in_time(SP, pd.Timestamp("2026-10-07T19:00:00Z")).iloc[0]["wx_tmax_f"]) == WILD
    assert float(build.point_in_time(SP, pd.Timestamp("2026-10-13T18:00:00Z")).iloc[0]["gas_hh"]) == WILD


def test_canary_check_fails_if_the_visibility_rule_is_broken(world, monkeypatch):
    """The test of the test: with `_known_prefix` and `_visible` ignoring the decision time, the canary check must raise."""
    for name, rows in _canary_rows(world, ASOF).items():
        _write(name, rows)
    monkeypatch.setattr(build, "_known_prefix", lambda pcm, asof_us: np.full(np.shape(asof_us), len(pcm) - 1))
    monkeypatch.setattr(build, "_visible", lambda pub, asof_us: np.ones(np.shape(pub), dtype=bool))
    try:
        df = build.point_in_time(SP, ASOF)
    except Exception:
        return                                                                       # a builder that blows up is also not leaking silently
    with pytest.raises(AssertionError):
        _assert_no_canary(world, ASOF, df)


def test_breaking_only_the_prefix_rule_leaks_prices(world, monkeypatch):
    for name, rows in _canary_rows(world, ASOF).items():
        _write(name, rows)
    monkeypatch.setattr(build, "_known_prefix", lambda pcm, asof_us: np.full(np.shape(asof_us), len(pcm) - 1))
    df = build.point_in_time(SP, ASOF)
    with pytest.raises(AssertionError):
        _assert_no_canary(world, ASOF, df)


def test_breaking_only_the_row_rule_leaks_dam_prices(world, monkeypatch):
    for name, rows in _canary_rows(world, ASOF).items():
        _write(name, rows)
    monkeypatch.setattr(build, "_visible", lambda pub, asof_us: np.ones(np.shape(pub), dtype=bool))
    df = build.point_in_time(SP, ASOF)
    with pytest.raises(AssertionError):
        _assert_no_canary(world, ASOF, df)


# ------------------------------------------------------------------------------------------------ misc guarantees
def test_a_late_row_hides_what_follows_it_until_it_is_published(world):
    """RT prices are read as a prefix: if interval k is published late, intervals after it (published on time) are not 'known'
    either, so a gap can never be filled from the future and a rolling window never straddles an unpublished interval."""
    ds = parse.DATASETS["rt_spp"]
    k = (ASOF - pd.Timedelta(minutes=75)).floor("15min")                          # four intervals before the one in progress
    key = k.tz_convert(CPT).strftime("%Y-%m-%d")
    part = parse.read("rt_spp", date_from=key, date_to=key)
    hit = (part["settlement_point"] == SP) & (part["interval_start_utc"] == k)
    assert hit.sum() == 1
    part.loc[hit, "published_utc"] = ASOF + pd.Timedelta(hours=3)                 # this one interval is published three hours late
    parse.write_table(parse.partition_path("rt_spp", key), parse._to_table(part, ds, {"dispatch.dataset": "rt_spp"}))
    df = build.point_in_time(SP, ASOF)
    expected_last = world["rt_spp"]
    expected_last = expected_last[(expected_last.settlement_point == SP) & (expected_last.interval_start_utc == k - pd.Timedelta(minutes=15))]
    assert float(df.iloc[0]["rt_last"]) == pytest.approx(float(expected_last.price_usd_per_mwh.iloc[0]), rel=1e-6)
    assert float(df.iloc[0]["rt_last_age_min"]) == pytest.approx((ASOF - k) / pd.Timedelta(minutes=1), abs=1e-6)   # age counted from the end of k - 1
    assert df["max_published_utc"].max() <= ASOF
    # three hours later the late row is public and everything after it comes back
    later = build.point_in_time(SP, ASOF + pd.Timedelta(hours=3, minutes=1))
    assert later.iloc[0]["rt_last_age_min"] < 30


def test_no_data_for_a_settlement_point_is_an_error_not_nans(world):
    with pytest.raises(build.FeatureDataError, match="NO_SUCH_POINT"):
        build.point_in_time("NO_SUCH_POINT", ASOF)


def test_nothing_known_yet_is_an_error(world):
    with pytest.raises(build.FeatureDataError):
        build.point_in_time(SP, T0 + pd.Timedelta(minutes=5))


def test_decision_times_are_deterministic_and_anchored_to_the_epoch():
    a = build.decision_times(pd.Timestamp("2026-10-01T01:00:00Z"), pd.Timestamp("2026-10-02T00:00:00Z"), "4h")
    assert list(a.hour) == [4, 8, 12, 16, 20] and (a[1:] - a[:-1] == pd.Timedelta(hours=4)).all()
    b = build.decision_times(pd.Timestamp("2026-09-30T23:00:00Z"), pd.Timestamp("2026-10-02T00:00:00Z"), "4h")
    assert set(a) <= set(b)                                           # the same grid whatever `start` is
    assert len(build.decision_times(pd.Timestamp("2026-10-01T00:00:00Z"), pd.Timestamp("2026-10-01T00:00:00Z"), "4h")) == 0


def test_decision_grid_offset_shifts_every_decision_and_keeps_the_leakage_guarantee(world):
    a = build.decision_times(pd.Timestamp("2026-10-01T00:00:00Z"), pd.Timestamp("2026-10-02T00:00:00Z"), "4h", "7min30s")
    assert [str(x.time()) for x in a] == ["00:07:30", "04:07:30", "08:07:30", "12:07:30", "16:07:30", "20:07:30"]
    for bad in ("-1min", "4h", "5h"):
        with pytest.raises(ValueError):
            build.decision_times(pd.Timestamp("2026-10-01T00:00:00Z"), pd.Timestamp("2026-10-02T00:00:00Z"), "4h", bad)
    start, end = pd.Timestamp("2026-10-01T00:00:00Z"), pd.Timestamp("2026-10-02T00:00:00Z")
    X, y, dec = build.training_frame(SP, start, end, "4h", offset="7min30s")
    assert set(dec) == set(a) and (X["max_published_utc"] <= X["asof_utc"]).all()
    aligned, _, _ = build.training_frame(SP, start, end, "4h")
    assert (aligned["rt_last_age_min"] == 15.0).all()                                   # on the grid the newest price is always 15 min old
    assert X["rt_last_age_min"].between(0, 15, inclusive="left").all() and (X["rt_last_age_min"] != 15.0).all()   # 7.5 min into an interval it is younger
    assert (X["horizon"].iloc[:96] == range(1, 97)).all()
    assert X["interval_start_utc"].iloc[0] == pd.Timestamp("2026-10-01T00:00:00Z")      # h=1 is the interval containing 00:07:30


def test_feature_definition_is_stable_and_complete():
    assert len(sets.FEATURES) == len(set(sets.FEATURES)) == len(sets.FEATURE_TABLE) == 82 and sets.VERSION == "1.1.0"
    assert set(D1A_SPEC) <= set(sets.FEATURES) and len(D1A_SPEC) == 41 and sets.FEATURES[:41] == V100_FEATURES     # 1.0.0 columns keep their place
    assert sets.schema_hash() == sets.schema_hash() and len(sets.schema_hash()) == 16
    assert set(sets.GROUPS) == {"rt", "hb", "dam", "cal", "gas", "wx", "lf"}
    assert sets.HORIZON == 96 and sets.LABEL not in sets.FEATURES
    assert not any(c in sets.FEATURES for c in sets.META_COLUMNS)


def test_us_federal_holidays_are_pandas_observed_days():
    """1.1.0: the OBSERVED federal holidays, exactly pandas' USFederalHolidayCalendar (what D1a's trained models were fitted on)."""
    from pandas.tseries.holiday import USFederalHolidayCalendar
    cal = USFederalHolidayCalendar()
    for year in range(2019, 2030):
        pandas_days = {d.date() for d in cal.holidays(f"{year}-01-01", f"{year}-12-31")}
        mine = {d for d in sets.holiday_dates([year]) if d.year == year}          # includes Dec 31 when Jan 1 of next year is a Saturday
        assert mine == pandas_days, (year, sorted(mine ^ pandas_days))
    assert list(sets.is_holiday(np.array(["2026-07-03", "2026-07-04", "2026-07-06"], dtype="datetime64[D]"))) == [True, False, False]   # Saturday 4 July: the Friday is observed
    assert list(sets.is_holiday(np.array(["2026-12-25", "2026-12-26"], dtype="datetime64[D]"))) == [True, False]


# ------------------------------------------------------------------------------------------------ DuckDB views and the as-of join
def test_duckdb_views_exist_for_every_dataset_even_when_empty(world):
    from dispatch.data import duck
    con = duck.connect()
    assert con.sql("select count(*) from rt_spp").fetchone()[0] == len(world["rt_spp"])
    for name, ds in parse.DATASETS.items():                                       # empty datasets still have their columns
        assert con.sql(f"select * from {name} limit 0").columns == [f.name for f in ds.schema], name
    assert con.sql("select count(*) from rt_as").fetchone()[0] == 0
    assert set(duck.UNIFIED) == {"rt_spp_all", "dam_spp_all", "dam_as_all", "rt_as_all"}
    assert con.sql("select count(*) from dam_spp_all").fetchone()[0] == len(world["dam_spp"])


def test_unified_views_prefer_the_real_row_over_the_assumed_one(world):
    from dispatch.data import duck
    t = pd.Timestamp("2026-09-20T12:00:00Z")
    hist = pd.DataFrame({"published_utc": _ts([t - pd.Timedelta(days=1, hours=-2)]), "interval_start_utc": _ts([t]), "settlement_point": SP,
                         "price_usd_per_mwh": 123.45, "pub_basis": "ASSUMPTION"})
    _write("dam_spp_hist", hist)                                                    # the archive's copy of an hour the live window also has
    con = duck.connect()
    rows = con.sql(f"select price_usd_per_mwh, pub_basis from dam_spp_all where settlement_point='{SP}' and interval_start_utc = TIMESTAMPTZ '2026-09-20 12:00:00+00'").fetchall()
    assert len(rows) == 1 and rows[0][1] == "REAL" and rows[0][0] != 123.45
    only_hist = pd.DataFrame({"published_utc": _ts([pd.Timestamp("2010-12-01T19:00:00Z")]), "interval_start_utc": _ts([pd.Timestamp("2010-12-02T06:00:00Z")]),
                              "settlement_point": SP, "price_usd_per_mwh": 7.0, "pub_basis": "ASSUMPTION"})
    _write("dam_spp_hist", only_hist)
    con = duck.connect()
    assert con.sql("select count(*) from dam_spp_all where interval_start_utc = TIMESTAMPTZ '2010-12-02 06:00:00+00'").fetchone()[0] == 1


def test_asof_join_never_attaches_a_row_published_after_the_decision(world):
    from dispatch.data import duck
    con = duck.connect()
    pub = world["dam_spp"][world["dam_spp"].interval_start_utc == pd.Timestamp("2026-10-07T08:00:00Z")].published_utc.iloc[0]
    dec = pd.DataFrame({"asof_utc": _ts([pub - pd.Timedelta(seconds=1), pub, pub + pd.Timedelta(hours=3)]),
                        "settlement_point": SP, "interval_start_utc": _ts([pd.Timestamp("2026-10-07T08:00:00Z")] * 3)})
    out = duck.asof_join(con, dec, "dam_spp", by=["settlement_point", "interval_start_utc"], right_cols=["price_usd_per_mwh", "published_utc"])
    assert out["price_usd_per_mwh"].isna().tolist() == [True, False, False]          # nothing before the publication, the price from then on
    assert (out["published_utc"].dropna() <= out.loc[out["published_utc"].notna(), "asof_utc"]).all()
    expect = world["dam_spp"][(world["dam_spp"].interval_start_utc == pd.Timestamp("2026-10-07T08:00:00Z"))].price_usd_per_mwh.iloc[0]
    assert out["price_usd_per_mwh"].iloc[1] == pytest.approx(expect)
    inner = duck.asof_join(con, dec, "dam_spp", by=["settlement_point", "interval_start_utc"], right_cols=["price_usd_per_mwh"], how="inner")
    assert len(inner) == 2


def test_asof_join_picks_the_newest_published_row_of_a_series(world):
    from dispatch.data import duck
    con = duck.connect()
    asof = pd.Timestamp("2026-10-01T12:34:56Z")
    dec = pd.DataFrame({"asof_utc": _ts([asof]), "settlement_point": SP})
    out = duck.asof_join(con, dec, "rt_spp", by=["settlement_point"], right_cols=["price_usd_per_mwh", "published_utc", "interval_start_utc"])
    rt = world["rt_spp"]
    rt = rt[(rt.settlement_point == SP) & (rt.published_utc <= asof)].sort_values("published_utc")
    assert out["published_utc"].iloc[0] == rt.published_utc.iloc[-1] and out["price_usd_per_mwh"].iloc[0] == pytest.approx(rt.price_usd_per_mwh.iloc[-1])


# ------------------------------------------------------------------------------------------------ the realised-price source (D2)
def _spec(products=None):
    from dispatch.contracts import BatterySpec
    kw = {"as_products": tuple(products)} if products else {}
    return BatterySpec("A1", SP, 10.0, 20.0, **kw)


def _rt_as_rows(start, end, products=("REGUP", "REGDN", "RRS", "ECRS", "NSPIN"), seed=5) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    grid = pd.date_range(start, end, freq="15min", inclusive="left")
    rows = []
    for p in products:
        rows.append(pd.DataFrame({"published_utc": _ts(grid + pd.Timedelta(minutes=17)), "interval_start_utc": _ts(grid), "product": p,
                                  "mcpc_usd_per_mw_h": np.round(rng.uniform(0.2, 3, len(grid)), 2)}))
    return pd.concat(rows, ignore_index=True)


def test_price_source_returns_a_complete_15_minute_frame(world):
    from dispatch.data import prices
    start, end = pd.Timestamp("2026-10-01T05:00:00Z"), pd.Timestamp("2026-10-03T05:00:00Z")
    rt_as = _rt_as_rows(start, end)
    _write("rt_as", rt_as)
    spec = _spec()
    df = prices.price_source(spec, start, end, asof_utc=pd.Timestamp("2026-10-10T00:00:00Z"))
    assert list(df.columns) == ["rt_usd_per_mwh", "dam_usd_per_mwh"] + [f"as_{p.lower()}_usd_per_mw_h" for p in spec.as_products]
    assert df.index.name == "interval_start_utc" and df.index.tz is not None and len(df) == 192 and (df.dtypes == "float64").all()
    assert (df.index[1:] - df.index[:-1] == pd.Timedelta(minutes=15)).all() and df.index[0] == start and df.index[-1] == end - pd.Timedelta(minutes=15)
    assert not df.isna().any().any()
    rt = world["rt_spp"]
    rt = rt[(rt.settlement_point == SP)].set_index("interval_start_utc")["price_usd_per_mwh"]
    np.testing.assert_allclose(df["rt_usd_per_mwh"].to_numpy(), rt.reindex(df.index).to_numpy())
    dam = world["dam_spp"].set_index("interval_start_utc")["price_usd_per_mwh"]
    np.testing.assert_allclose(df["dam_usd_per_mwh"].to_numpy(), dam.reindex(df.index.floor("h")).to_numpy())        # an hour's price on its four intervals
    reg = rt_as[rt_as["product"] == "REGUP"].set_index("interval_start_utc")["mcpc_usd_per_mw_h"]
    np.testing.assert_allclose(df["as_regup_usd_per_mw_h"].to_numpy(), reg.reindex(df.index).to_numpy())            # RTC+B: the 15-minute MCPC
    assert df.attrs["as_sources"]["REGUP"] == {"rt_15min": 192, "dam_hourly": 0, "not_yet_a_product": 0}
    assert prices.as_price_col("REGUP") == "as_regup_usd_per_mw_h"


def test_price_source_uses_the_label_the_training_frame_uses(world):
    """The realised price a monitor sees and the label a model trains on come from the same rule (REAL first, latest published)."""
    from dispatch.data import prices
    start, end = pd.Timestamp("2026-09-20T05:00:00Z"), pd.Timestamp("2026-09-20T17:00:00Z")
    _write("rt_spp", pd.DataFrame({"published_utc": _ts([pd.Timestamp("2026-09-21T00:00:00Z")]), "interval_start_utc": _ts([pd.Timestamp("2026-09-20T09:00:00Z")]),
                                   "settlement_point": SP, "sp_type": "HU", "price_usd_per_mwh": 777.0}))                    # a price correction, published later
    df = prices.price_source(_spec(["REGUP"]), start, end, asof_utc=pd.Timestamp("2026-10-10T00:00:00Z"))
    assert df.loc[pd.Timestamp("2026-09-20T09:00:00Z"), "rt_usd_per_mwh"] == 777.0                                        # the latest published version
    before_fix = prices.price_source(_spec(["REGUP"]), start, pd.Timestamp("2026-09-20T12:00:00Z"), asof_utc=pd.Timestamp("2026-09-20T14:00:00Z"))
    assert before_fix.loc[pd.Timestamp("2026-09-20T09:00:00Z"), "rt_usd_per_mwh"] != 777.0                               # not yet published at that asof
    X, y, dec = build.training_frame(SP, pd.Timestamp("2026-09-20T08:00:00Z"), pd.Timestamp("2026-09-20T09:00:00Z"), "1h")
    row = X["interval_start_utc"] == pd.Timestamp("2026-09-20T09:00:00Z")
    assert y[row].iloc[0] == 777.0                                                                                       # the label agrees
    assert float(X.loc[row, "rt_lag_1d"].iloc[0]) != 777.0                                                               # features never see the correction early


def test_price_source_raises_while_prices_are_not_published(world):
    from dispatch.data import prices
    spec = _spec(["REGUP"])
    with pytest.raises(prices.PricesNotPublished) as ei:
        prices.price_source(spec, pd.Timestamp("2026-10-12T00:00:00Z"), pd.Timestamp("2026-10-14T00:00:00Z"), asof_utc=pd.Timestamp("2026-10-20T00:00:00Z"))
    assert isinstance(ei.value, LookupError)                                                                            # D2 treats LookupError as "no data"
    assert "rt_usd_per_mwh" in str(ei.value) and "HB_NORTH" in str(ei.value)
    # a window that is complete except for its last interval is still incomplete
    start, end = pd.Timestamp("2026-10-12T00:00:00Z"), pd.Timestamp("2026-10-13T05:15:00Z")                              # the world ends 2026-10-13 05:00 UTC
    with pytest.raises(prices.PricesNotPublished, match="1 of "):
        prices.price_source(spec, start, end, asof_utc=pd.Timestamp("2026-10-20T00:00:00Z"), include_dam=False)
    with pytest.raises(prices.PricesNotPublished):
        prices.price_source(_spec(["ECRS"]), start, pd.Timestamp("2026-10-12T06:00:00Z"), asof_utc=pd.Timestamp("2026-10-20T00:00:00Z"))   # RT fine, no ECRS price anywhere


def test_price_source_only_sees_what_was_public_at_asof(world):
    from dispatch.data import prices
    spec = _spec(["REGUP"])
    start, end = pd.Timestamp("2026-09-20T05:00:00Z"), pd.Timestamp("2026-09-20T07:00:00Z")
    done = prices.price_source(spec, start, end, asof_utc=pd.Timestamp("2026-09-20T07:30:00Z"))                         # RT published by 07:17 + jitter
    assert len(done) == 8
    with pytest.raises(prices.PricesNotPublished, match="rt_usd_per_mwh"):
        prices.price_source(spec, start, end, asof_utc=pd.Timestamp("2026-09-20T06:30:00Z"))                           # the last intervals are not out yet


def test_price_source_falls_back_to_the_day_ahead_ancillary_price(world):
    from dispatch.data import prices
    spec = _spec(["REGUP", "RRS"])
    start, end = pd.Timestamp("2026-09-20T05:00:00Z"), pd.Timestamp("2026-09-20T09:00:00Z")                              # no rt_as rows in this window
    df = prices.price_source(spec, start, end, asof_utc=pd.Timestamp("2026-10-10T00:00:00Z"))
    dam_as = world["dam_as"]
    want = dam_as[dam_as["product"] == "REGUP"].set_index("interval_start_utc")["mcpc_usd_per_mw_h"].reindex(df.index.floor("h")).to_numpy()
    np.testing.assert_allclose(df["as_regup_usd_per_mw_h"].to_numpy(), want)
    assert df.attrs["as_sources"]["REGUP"] == {"rt_15min": 0, "dam_hourly": 16, "not_yet_a_product": 0}


def test_a_product_that_did_not_exist_yet_prices_at_zero(world):
    from dispatch.data import prices
    first = pd.Timestamp("2026-10-05T05:00:00Z")
    ecrs = pd.DataFrame({"published_utc": _ts([first - pd.Timedelta(hours=12)]), "interval_start_utc": _ts([first]), "product": "ECRS", "mcpc_usd_per_mw_h": 2.0})
    _write("dam_as_hist", ecrs)
    spec = _spec(["ECRS"])
    before = prices.price_source(spec, pd.Timestamp("2026-09-20T05:00:00Z"), pd.Timestamp("2026-09-20T07:00:00Z"), asof_utc=pd.Timestamp("2026-10-10T00:00:00Z"))
    assert (before["as_ecrs_usd_per_mw_h"] == 0.0).all() and before.attrs["as_sources"]["ECRS"]["not_yet_a_product"] == 8
    with pytest.raises(prices.PricesNotPublished, match="as_ecrs"):                                                    # after its first price a gap is a gap
        prices.price_source(spec, pd.Timestamp("2026-10-05T05:00:00Z"), pd.Timestamp("2026-10-05T09:00:00Z"), asof_utc=pd.Timestamp("2026-10-10T00:00:00Z"))
    never = _spec(["ECRS"])
    # with no ECRS price anywhere in the lake the product is unknown, not "not yet": that is an error too
    import shutil
    shutil.rmtree(parse.dataset_dir("dam_as_hist"))
    with pytest.raises(prices.PricesNotPublished):
        prices.price_source(never, pd.Timestamp("2026-09-20T05:00:00Z"), pd.Timestamp("2026-09-20T07:00:00Z"), asof_utc=pd.Timestamp("2026-10-10T00:00:00Z"))


def test_price_source_checks_its_arguments(world):
    from dispatch.data import prices
    spec = _spec(["REGUP"])
    with pytest.raises(ValueError):
        prices.price_source(spec, pd.Timestamp("2026-09-20T05:07:00Z"), pd.Timestamp("2026-09-20T07:00:00Z"))
    with pytest.raises(ValueError):
        prices.price_source(spec, pd.Timestamp("2026-09-20T07:00:00Z"), pd.Timestamp("2026-09-20T05:00:00Z"))
    with pytest.raises(ValueError):
        prices.price_source(spec, pd.Timestamp("2026-09-20 05:00:00"), pd.Timestamp("2026-09-20T07:00:00Z"))            # naive times are refused


# ------------------------------------------------------------------------------------------------ prepare_signal (D2: refresh right before POST /signal)
def test_prepare_signal_writes_the_frame_the_service_reads(world):
    import fakeredis
    from dispatch.data import flows
    from dispatch.features import online
    r = fakeredis.FakeStrictRedis()
    now = pd.Timestamp("2026-10-06T15:07:30Z")
    out = flows.prepare_signal([SP, "HB_BUSAVG"], now_utc=now, ingest=False, store=online.OnlineStore(r))
    assert set(out["online"]) == {SP, "HB_BUSAVG"} and all("error" not in v for v in out["online"].values()), out
    df, asof, mp = online.read_latest(SP, client=r)
    assert asof == now and mp <= now and mp > now - pd.Timedelta(minutes=30)
    assert df.index[0] == pd.Timestamp("2026-10-06T15:00:00Z") and list(df["horizon"]) == list(range(1, 97))       # h=1: the interval containing now
    assert (df["max_published_utc"] <= asof).all() and df["settlement_point"].eq(SP).all()
    # the frame is exactly what point_in_time gives (nothing is lost on the way through Redis)
    ref = build.point_in_time(SP, now)
    for c in sets.FEATURES:
        np.testing.assert_array_equal(df[c].to_numpy(), ref[c].to_numpy())


def test_prepare_signal_survives_a_failed_fetch_and_still_refreshes(world):
    import fakeredis
    import httpx
    from dispatch.data import ercot, flows
    from dispatch.features import online
    flows._LAST_INGEST["t"] = -1e9
    f = ercot.Fetcher(httpx.Client(transport=httpx.MockTransport(lambda req: httpx.Response(503))), retries=0, min_spacing_s=0)
    r = fakeredis.FakeStrictRedis()
    out = flows.prepare_signal([SP], now_utc=pd.Timestamp("2026-10-06T15:07:30Z"), ingest=True, fetcher=f, store=online.OnlineStore(r))
    assert "error" in out["ingest"] and "FetchError" in out["ingest"]["error"]
    assert out["online"][SP]["rows"] == 96 and online.read_latest(SP, client=r)[1] == pd.Timestamp("2026-10-06T15:07:30Z")
    again = flows.prepare_signal([SP], now_utc=pd.Timestamp("2026-10-06T15:08:00Z"), ingest=True, fetcher=f, store=online.OnlineStore(r))
    assert "skipped" in again["ingest"]                                                                              # a burst of signals does not hammer ERCOT
    flows._LAST_INGEST["t"] = -1e9


def test_prepare_signal_ingests_every_live_report_unless_told_otherwise(world, monkeypatch):
    """D2's stale-data monitor watches NP6-331-CD, NP6-322-CD and NP6-86-CD too: a deployment that only calls prepare_signal must keep
    them fresh, so the default is every live report, not just the four the features read."""
    import fakeredis
    from dispatch.data import flows, reports
    from dispatch.features import online
    seen = []
    monkeypatch.setattr(flows, "_ingest_once", lambda ids, **kw: seen.append(list(ids)) or {"ids": list(ids)})
    store = online.OnlineStore(fakeredis.FakeStrictRedis())
    now = pd.Timestamp("2026-10-06T15:07:30Z")
    flows._LAST_INGEST["t"] = -1e9
    flows.prepare_signal([SP], now_utc=now, store=store)
    assert seen[-1] == list(reports.INGEST_IDS) and {"NP6-331-CD", "NP6-322-CD", "NP6-86-CD", "NP6-905-CD"} <= set(seen[-1])
    flows._LAST_INGEST["t"] = -1e9
    flows.prepare_signal([SP], now_utc=now, store=store, report_ids=flows.FEATURE_REPORTS)
    assert seen[-1] == list(flows.FEATURE_REPORTS) and len(seen[-1]) == 4
    flows._LAST_INGEST["t"] = -1e9


# ================================================================================================ rt_v1 1.1.0: the 41 columns of notes/d1a.md section 10
# windows.py is the arithmetic under them; Oracle is a second, plain implementation written from the spec; the late-row tests put rows
# published after the decision where each of the 41 columns could read them; the worked examples are the numbers printed in the spec.
def _rand_windows(seed=0, d=80, w=672, nan_share=0.05):
    rng = np.random.default_rng(seed)
    x = np.round(rng.lognormal(3, 1.2, (d, w)), 2)
    x[rng.random((d, w)) < 0.01] = 9000.0                                  # spikes: where a running sum loses digits
    x[rng.random((d, w)) < nan_share] = np.nan
    x[3, : w // 2 + 10] = np.nan                                           # one window with too few valid values
    return x


def test_windows_agree_with_numpy_and_need_half_a_window():
    from dispatch.features import windows as W
    x = _rand_windows()
    for w, minp in ((4, 2), (16, 8), (96, 48), (672, 336)):
        win = W.tail(x, w)
        s, n = W.sort_valid(win)

        def rows(fn):
            return np.array([fn(r[~np.isnan(r)]) if (~np.isnan(r)).sum() >= minp else np.nan for r in win])
        for got, ref in ((W.mean_of(win, minp), rows(np.mean)), (W.std_of(win, minp), rows(np.std)),
                         (W.minimum(s, n, minp), rows(np.min)), (W.maximum(s, n, minp), rows(np.max)),
                         (W.median(s, n, minp), rows(np.median)), (W.mean_abs(win, minp), rows(lambda v: np.abs(v).mean())),
                         (W.share(win, lambda v: v >= 100, minp), rows(lambda v: (v >= 100).mean())),
                         (W.share(win, lambda v: v < 0, minp), rows(lambda v: (v < 0).mean()))):
            np.testing.assert_allclose(got, ref, rtol=1e-12, atol=1e-12, equal_nan=True)
        for q in (0.1, 0.5, 0.9):
            np.testing.assert_allclose(W.quantile(s, n, q, minp), rows(lambda v: np.quantile(v, q)), rtol=1e-12, atol=1e-12, equal_nan=True)
        mean = W.mean_of(win, minp)
        assert np.isnan(mean[n < minp]).all() and not np.isnan(mean[n >= minp]).any()
        if w == 672:
            assert (n < minp).any()                                              # the window with too few valid values is NaN
    win = W.tail(x, 96)
    s, n = W.sort_valid(win)
    top8 = lambda v: np.sort(v)[-8:].mean() - np.sort(v)[:8].mean()
    np.testing.assert_allclose(W.spread(s, n, 48), [top8(r[~np.isnan(r)]) if (~np.isnan(r)).sum() >= 48 else np.nan for r in win],
                               rtol=1e-12, equal_nan=True)


def test_window_arithmetic_does_not_depend_on_the_array_the_window_sits_in():
    """The same 672 values give the same bits whether they come out of a short array or a long one (Gate 1 and the train/serve parity
    rely on it); pandas' rolling does not have that property."""
    from dispatch.features import windows as W
    rng = np.random.default_rng(5)
    long = np.round(rng.lognormal(3, 1.2, 20_000), 2)
    long[rng.random(20_000) < 0.01] = 9000.0
    long[rng.random(20_000) < 0.02] = np.nan
    end = np.array([19_000, 15_123, 9_999])
    a = W.window_ending(long, end, 672)
    for cut in (1, 7, 1000, 8_000):
        b = W.window_ending(long[cut:], end - cut, 672)
        assert np.array_equal(a, b, equal_nan=True)
        for fn in (lambda v: W.mean_of(v, 336), lambda v: W.std_of(v, 336), lambda v: W.mean_abs(v, 336)):
            assert np.array_equal(fn(a), fn(b), equal_nan=True)
        sa, na = W.sort_valid(a)
        sb, nb = W.sort_valid(b)
        assert np.array_equal(W.quantile(sa, na, 0.9, 336), W.quantile(sb, nb, 0.9, 336), equal_nan=True)
    edge = W.window_ending(long, np.array([2, 20_005, -1]), 5)                  # a window may start before the array or end after it
    assert np.isnan(edge[0, :2]).all() and np.array_equal(edge[0, 2:], long[:3], equal_nan=True)
    assert np.isnan(edge[1]).all() and np.isnan(edge[2]).all()
    assert np.isnan(W.window_ending(long, np.array([100]), 5, ok=np.array([False]))).all()


def test_the_anchor_is_the_median_of_the_same_time_of_day_over_seven_days():
    from dispatch.features import windows as W
    rng = np.random.default_rng(9)
    p = np.round(rng.lognormal(3, 1, 96 * 12), 2)
    p[rng.random(len(p)) < 0.03] = np.nan
    a = W.anchor_grid(p, 7, 96)
    for i in list(range(0, 96 * 9, 29)) + [len(p) - 1]:
        vals = [p[i - 96 * m] for m in range(1, 8) if i - 96 * m >= 0 and not np.isnan(p[i - 96 * m])]
        ref = np.median(vals) if vals else np.nan
        assert (np.isnan(a[i]) and np.isnan(ref)) or a[i] == ref, (i, a[i], ref)
    assert np.isnan(a[:96]).all()                                              # the first day has no earlier day


# ---- an independent oracle for the 41 columns (plain pandas and loops, written from the spec; shares nothing with build.py)
STEP15 = pd.Timedelta(minutes=15)
DAY = pd.Timedelta(days=1)


class Oracle:
    """The 41 columns for one decision, from the world's DataFrames: keep the rows with published_utc <= asof, then follow notes/d1a.md
    section 10 one value at a time."""

    def __init__(self, world: dict, asof: pd.Timestamp, sp: str = SP):
        self.asof = asof
        rt = world["rt_spp"]
        rt = rt[(rt.settlement_point == sp) & (rt.published_utc <= asof)].sort_values("published_utc").drop_duplicates("interval_start_utc")
        s = pd.Series(rt.price_usd_per_mwh.to_numpy(float), index=pd.DatetimeIndex(rt.interval_start_utc)).sort_index()
        self.P = s.reindex(pd.date_range(s.index[0] - pd.Timedelta(days=30), s.index[-1], freq="15min"))
        self.kv = self.P.last_valid_index()
        das = world["dam_as"]
        self.das = das[das.published_utc <= asof]
        self._res = {}

    def p(self, t):
        return float(self.P.get(t, np.nan))

    def anchor(self, t):
        v = [self.p(t - m * DAY) for m in range(1, 8)]
        v = [x for x in v if not np.isnan(x)]
        return float(np.median(v)) if v else np.nan

    def r(self, t):
        if t not in self._res:
            self._res[t] = self.p(t) - self.anchor(t)
        return self._res[t]

    @staticmethod
    def _stat(vals, w, fn):
        v = np.asarray(vals, float)
        v = v[~np.isnan(v)]
        return float(fn(v)) if len(v) >= max(w // 2, 1) else np.nan

    def decision(self) -> dict:
        kv, o = self.kv, {}
        last = lambda w: [self.p(kv - i * STEP15) for i in range(w - 1, -1, -1)]
        lastr = lambda w: [self.r(kv - i * STEP15) for i in range(w - 1, -1, -1)]
        o["rt_mean_16"] = self._stat(last(16), 16, np.mean)
        o["rt_min_96"], o["rt_max_96"] = self._stat(last(96), 96, np.min), self._stat(last(96), 96, np.max)
        o["rt_spread_96"] = self._stat(last(96), 96, lambda v: np.sort(v)[-8:].mean() - np.sort(v)[:8].mean())
        for q, nm in ((0.1, "q10"), (0.5, "q50"), (0.9, "q90")):
            o[f"rt_{nm}_672"] = self._stat(last(672), 672, lambda v: np.quantile(v, q))
            o[f"res_{nm}_672"] = self._stat(lastr(672), 672, lambda v: np.quantile(v, q))
            o[f"res_{nm}_96"] = self._stat(lastr(96), 96, lambda v: np.quantile(v, q))
        for w in (96, 672):
            o[f"rt_frac_hi_{w}"] = self._stat(last(w), w, lambda v: (v >= 100.0).mean())
            o[f"rt_frac_neg_{w}"] = self._stat(last(w), w, lambda v: (v < 0.0).mean())
        o["res_last"] = self.r(kv)
        for w in (4, 16, 96):
            o[f"res_mean_{w}"] = self._stat(lastr(w), w, np.mean)
        o["res_mad_672"] = self._stat(lastr(672), 672, lambda v: np.abs(v).mean())
        return o

    def target(self, h: int) -> dict:
        t = self.asof.floor("15min") + (h - 1) * STEP15
        o = {}
        for d in (2, 3, 14):
            o[f"rt_lag_{d}d"] = self.p(t - d * DAY)
        prof = [self.p(t - m * DAY) for m in range(1, 8)]
        prof = [x for x in prof if not np.isnan(x)]
        for nm, fn in (("mean", np.mean), ("med", np.median), ("min", np.min), ("max", np.max), ("std", np.std)):
            o[f"rt_prof7_{nm}"] = float(fn(prof)) if prof else np.nan
        weekend = lambda x: x.tz_convert(CPT).dayofweek >= 5
        known = [(m, self.p(t - m * DAY)) for m in range(1, 15)]
        known = [(m, v) for m, v in known if not np.isnan(v)]
        same = [v for m, v in known if weekend(t - m * DAY) == weekend(t)]
        pool = same if len(same) >= 2 else [v for _, v in known]
        o["rt_prof14_dt"] = float(np.mean(pool)) if pool else np.nan
        doy = t.tz_convert(CPT).dayofyear
        o["doy_sin"], o["doy_cos"] = float(np.sin(2 * np.pi * (doy - 1) / 365.25)), float(np.cos(2 * np.pi * (doy - 1) / 365.25))
        loc = t.tz_convert(CPT)
        for prod in ("REGUP", "REGDN", "RRS", "NSPIN"):
            d = self.das[self.das["product"] == prod]
            local = pd.DatetimeIndex(d.interval_start_utc).tz_convert(CPT)
            dates = pd.Series(local.tz_localize(None).normalize(), index=d.index)
            newest = dates.max()                                            # the newest operating date with results public at asof
            vals = []
            for back in range(14):
                row = d[(dates == newest - back * DAY) & (local.hour == loc.hour)]
                if len(row):
                    vals.append(float(row.mcpc_usd_per_mw_h.iloc[0]))
            med = float(np.median(vals)) if vals else np.nan
            hour_row = d[d.interval_start_utc == t.floor("1h")]
            dam = float(hour_row.mcpc_usd_per_mw_h.iloc[0]) if len(hour_row) else np.nan
            o[f"dam_as_{prod.lower()}_med14"] = med
            o[f"dam_as_{prod.lower()}_rel"] = dam / max(med, 0.1) if not (np.isnan(dam) or np.isnan(med)) else np.nan
        return o


def test_the_41_new_columns_match_an_independent_oracle(world):
    ctx = _ctx()
    checked, nan_checked = 0, 0
    for asof in _asofs(25, seed=11):
        df = build.point_in_time(SP, asof, context=ctx)
        oracle = Oracle(world, asof)
        dec = oracle.decision()
        for h in (1, 2, 17, 48, 95, 96):
            ref = {**dec, **oracle.target(h)}
            row = df.iloc[h - 1]
            for k, v in ref.items():
                got = float(row[k])
                if np.isnan(v):
                    assert np.isnan(got), f"{k} at asof {asof} h={h}: builder {got}, oracle NaN (it used data the oracle must not see)"
                    nan_checked += 1
                else:
                    assert got == pytest.approx(v, rel=3e-6, abs=3e-6), f"{k} at asof {asof} h={h}: builder {got}, oracle {v}"
                checked += 1
    assert checked > 5000 and nan_checked > 20                                  # NaN cases (a DAM date not public yet, lags of the last horizons) are compared too
    assert set(ref) == set(D1A_SPEC) and len(ref) == 41                         # the oracle covers exactly the 41 columns of the spec


def test_gaps_are_skipped_not_filled_and_a_window_counts_slots_not_rows(world):
    """A missing interval inside a window is left out (the window needs at least half its slots), never forward-filled; the window is the last w
    SLOTS before the newest known price, so two holes mean 14 values in a window of 16."""
    kv = (ASOF - pd.Timedelta(minutes=17)).floor("15min")                               # the newest published interval (published start + 17 min)
    holes = [kv, kv - 3 * STEP15, kv - 4 * STEP15, kv - 50 * STEP15]
    rt = world["rt_spp"]
    _rewrite("rt_spp", rt[~((rt.settlement_point == SP) & rt.interval_start_utc.isin(holes))])
    df = build.point_in_time(SP, ASOF)
    have = rt[(rt.settlement_point == SP) & (rt.published_utc <= ASOF) & (~rt.interval_start_utc.isin(holes))].sort_values("interval_start_utc")
    kv2 = kv - STEP15                                                                   # the removed newest interval makes this the newest known one
    assert have.interval_start_utc.iloc[-1] == kv2 and float(df.iloc[0]["rt_last"]) == pytest.approx(have.price_usd_per_mwh.iloc[-1], rel=1e-6)
    in16 = have[(have.interval_start_utc > kv2 - 16 * STEP15) & (have.interval_start_utc <= kv2)]
    assert len(in16) == 14 and float(df.iloc[0]["rt_mean_16"]) == pytest.approx(in16.price_usd_per_mwh.mean(), rel=1e-6)
    last16_rows = have.price_usd_per_mwh.to_numpy()[-16:]
    assert abs(float(df.iloc[0]["rt_mean_16"]) - last16_rows.mean()) > 1e-6          # not "the last 16 published rows"


# ---- rows published after the decision, where each of the 41 columns could read them
def _rewrite(name: str, df: pd.DataFrame) -> None:
    import shutil
    shutil.rmtree(parse.dataset_dir(name), ignore_errors=True)
    _write(name, df)


def _cut_lake_at(world: dict, asof: pd.Timestamp) -> None:
    """Rewrite every dataset of the lake with only the rows published at or before `asof`: the lake as the live system had it."""
    for name, df in world.items():
        _rewrite(name, df[df["published_utc"] <= asof])


def test_cutting_the_lake_at_the_decision_time_changes_no_column(world):
    """The strongest leakage test: a lake that contains the future (every row published after the decision: ~25 days of RT prices, the next
    day-ahead dates, later load forecasts, gas, weather) gives, for all 82 columns and all 96 horizons, EXACTLY the frame of a lake that was cut
    at the decision time. Nothing published later reaches any feature, the 41 new columns included (each is populated at every decision here)."""
    asofs = _asofs()[:3] + _asofs()[40:46] + [ASOF]                                   # random times, the edges around a DAM / LF / RT publication
    full = {a: build.point_in_time(SP, a) for a in asofs}
    for a in asofs:
        assert (full[a][list(D1A_SPEC)].notna().mean() > 0.3).all()                       # all 41 new columns carry values at these decisions
        _cut_lake_at(world, a)
        pd.testing.assert_frame_equal(full[a], build.point_in_time(SP, a), check_exact=True, obj=f"frame at {a}")


def _canaries(world: dict, asof: pd.Timestamp = ASOF) -> dict:
    """99999 where a window, a lag, a profile or a 14-date median could overshoot: the twelve RT intervals after the newest published one
    (in progress or not yet public) and every hour of tomorrow's day-ahead ancillary prices, all published one minute after `asof`."""
    late = asof + pd.Timedelta(minutes=1)
    kv = (asof - pd.Timedelta(minutes=17)).floor("15min")                                 # the newest published interval (published start + 17 min)
    ahead = [kv + i * STEP15 for i in range(1, 13)]
    today = asof.tz_convert(CPT).normalize().tz_localize(None)
    tomorrow = [pd.Timestamp(today + DAY + pd.Timedelta(hours=h)).tz_localize(CPT).tz_convert("UTC") for h in range(24)]
    rt = pd.concat([pd.DataFrame({"published_utc": _ts([late] * len(ahead)), "interval_start_utc": _ts(ahead), "settlement_point": sp,
                                  "sp_type": "HU", "price_usd_per_mwh": WILD}) for sp in (SP, "HB_BUSAVG")], ignore_index=True)
    das = pd.concat([pd.DataFrame({"published_utc": _ts([late] * 24), "interval_start_utc": _ts(tomorrow), "product": prod, "mcpc_usd_per_mw_h": WILD})
                     for prod in ("REGUP", "REGDN", "RRS", "NSPIN")], ignore_index=True)
    return {"rt_spp": rt, "dam_as": das}


ASOF_EARLY = ASOF.floor("15min") + pd.Timedelta(seconds=30)           # 30 s into an interval: the interval before it is not public yet (kv is two back)


@pytest.mark.parametrize("asof", [ASOF, ASOF_EARLY], ids=["mid-interval", "30s-into-interval"])
def test_rows_published_after_the_decision_change_no_column(world, asof):
    """All 82 columns, 96 horizons: a frame built with the canaries in the lake is IDENTICAL to one built without them, and the canaries are
    really there (they move the columns once public). At 30 s into an interval the previous interval is still unpublished, so the lags and
    profiles of the LAST horizon point at a canary: the mask `slot <= known prefix` is what keeps them out."""
    base = build.point_in_time(SP, asof)
    for name, rows in _canaries(world, asof).items():
        _write(name, rows)
    pd.testing.assert_frame_equal(base, build.point_in_time(SP, asof), check_exact=True)
    after = build.point_in_time(SP, asof + pd.Timedelta(minutes=2))
    moved = {c for c in sets.FEATURES if not np.array_equal(base[c].to_numpy(), after[c].to_numpy(), equal_nan=True)}
    must = ["rt_last", "rt_max_96", "rt_mean_16", "rt_q90_672", "res_last", "res_mad_672", "dam_as_regup", "dam_as_regup_med14", "dam_as_regup_rel",
            "dam_as_nspin_med14", "dam_as_rrs_rel", "dam_as_regdn_med14"]
    if asof == ASOF_EARLY:
        must += ["rt_lag_1d", "rt_prof7_max", "rt_prof7_mean", "rt_prof14_dt"]
    for c in must:
        assert c in moved, f"{c} did not move when the canaries became public: the test does not reach it"
    assert float(after.iloc[0]["rt_max_96"]) == WILD


@pytest.mark.parametrize("broken", ["both", "prefix", "rows"])
def test_the_canary_test_fails_when_a_visibility_rule_is_broken(world, monkeypatch, broken):
    """The test of the test: with `_known_prefix` and/or `_visible` ignoring the decision time, the frame DOES change."""
    base = build.point_in_time(SP, ASOF)
    for name, rows in _canaries(world).items():
        _write(name, rows)
    if broken in ("both", "prefix"):
        monkeypatch.setattr(build, "_known_prefix", lambda pcm, asof_us: np.full(np.shape(asof_us), len(pcm) - 1))
    if broken in ("both", "rows"):
        monkeypatch.setattr(build, "_visible", lambda pub, asof_us: np.ones(np.shape(pub), dtype=bool))
    try:
        leaky = build.point_in_time(SP, ASOF)
    except Exception:
        return                                                                       # a builder that blows up is not leaking silently either
    changed = {c for c in sets.FEATURES if not np.array_equal(base[c].to_numpy(), leaky[c].to_numpy(), equal_nan=True)}
    assert changed, "broken visibility rules went unnoticed"
    if broken == "rows":                                                             # the row rule alone is what keeps tomorrow's day-ahead prices out
        assert {"dam_as_regup", "dam_as_regup_rel"} <= changed
    if broken == "prefix":                                                           # the prefix rule alone is what keeps the in-progress prices out
        assert {"rt_last", "rt_max_96"} <= changed


def test_a_late_day_ahead_date_hides_the_newer_dates_until_it_is_published(world):
    """The 14-date median reads the newest operating date whose results are public, as a prefix: if the results of one date are published late,
    the dates after it are not 'newest' either (the same rule as for late RT prices)."""
    today = ASOF.tz_convert(CPT).normalize().tz_localize(None)
    older_hours = [pd.Timestamp(today - 2 * DAY + pd.Timedelta(hours=h)).tz_localize(CPT).tz_convert("UTC") for h in range(24)]
    das = world["dam_as"].copy()
    das.loc[das["interval_start_utc"].isin(older_hours), "published_utc"] = ASOF + pd.Timedelta(hours=3)         # two days ago: published late
    _rewrite("dam_as", das)
    df = build.point_in_time(SP, ASOF)
    target = pd.Timestamp("2026-10-06T19:00:00Z")                                      # 14:00 local: a target both frames contain
    hh = target.tz_convert(CPT).hour
    expect = []
    for back in range(14):                                                           # newest date with public results is 3 days ago (the late date hides today and yesterday)
        day = today - 3 * DAY - back * DAY
        t = pd.Timestamp(day + pd.Timedelta(hours=hh)).tz_localize(CPT).tz_convert("UTC")
        row = world["dam_as"][(world["dam_as"]["product"] == "REGUP") & (world["dam_as"]["interval_start_utc"] == t)]
        expect.append(float(row.mcpc_usd_per_mw_h.iloc[0]))
    assert float(df.loc[target, "dam_as_regup_med14"]) == pytest.approx(float(np.median(expect)), rel=1e-6)
    assert df["max_published_utc"].max() <= ASOF
    later = build.point_in_time(SP, ASOF + pd.Timedelta(hours=3, minutes=1))          # once published, today and yesterday are newest again
    assert float(later.loc[target, "dam_as_regup_med14"]) != pytest.approx(float(np.median(expect)), rel=1e-9)


# ---- the numbers printed in notes/d1a.md section 10.3
def test_d1a_worked_example_of_section_10_3_is_exact(dispatch_home):
    """Linear prices P[i] = 30 + 2 day + slot/4 on a grid starting at local midnight Monday 2025-01-06, decision block day 21 slot 41,
    horizon 5; D1a's rule that a price is public 30 minutes after its interval starts. Every number of the spec's table."""
    start = pd.Timestamp("2025-01-06 00:00").tz_localize(CPT).tz_convert("UTC")
    n = 24 * 96
    grid = pd.date_range(start, periods=n, freq="15min")
    price = 30.0 + 2.0 * (np.arange(n) // 96) + (np.arange(n) % 96) / 4.0
    _write("rt_spp", pd.DataFrame({"published_utc": _ts(grid + pd.Timedelta(minutes=30)), "interval_start_utc": _ts(grid), "settlement_point": SP,
                                   "sp_type": "HU", "price_usd_per_mwh": price}))
    asof = start + (21 * 96 + 41) * STEP15                                          # Monday 2025-01-27 10:15 local
    df = build.point_in_time(SP, asof)
    row = df.iloc[4]
    assert df.index[4] == start + (21 * 96 + 45) * STEP15                           # 11:15 local
    expect = {"rt_last": 81.75, "rt_mean_1h": 81.375, "rt_lag_1d": 81.25, "rt_lag_2d": 79.25, "rt_lag_3d": 77.25, "rt_lag_7d": 69.25, "rt_lag_14d": 55.25,
              "rt_prof7_med": 75.25, "rt_prof7_mean": 75.25, "rt_prof7_min": 69.25, "rt_prof7_max": 81.25, "rt_prof7_std": 4.0, "rt_prof14_dt": 66.25,
              "rt_min_96": 72.0, "rt_max_96": 93.75, "rt_spread_96": 20.0, "rt_frac_hi_96": 0.0, "rt_frac_neg_96": 0.0, "rt_frac_hi_672": 0.0,
              "rt_frac_neg_672": 0.0, "res_last": 8.0, "res_mean_4": 8.0, "res_mean_16": 8.0, "res_mean_96": 8.0, "res_q10_96": 8.0, "res_q50_96": 8.0,
              "res_q90_96": 8.0, "res_q10_672": 8.0, "res_q50_672": 8.0, "res_q90_672": 8.0, "res_mad_672": 8.0}
    for k, v in expect.items():
        assert float(row[k]) == pytest.approx(v, abs=1e-4), (k, float(row[k]), v)
    assert float(row["hour_local"]) + float(row["qtr_hour"]) / 4 == 11.25
    assert df["max_published_utc"].max() == asof                                    # the publish time of the newest price used = the block start
    # day-ahead example: price = 10 day + 0.1 hour + 1 by local date counted from 2024-12-01, results of D public at 13:30 on D-1
    rows = []
    for d in pd.date_range("2024-12-01", "2025-01-24", freq="D"):
        local = [(d + pd.Timedelta(hours=h)).tz_localize(CPT) for h in range(24)]
        pub = (d - DAY + pd.Timedelta(hours=13, minutes=30)).tz_localize(CPT).tz_convert("UTC")
        for prod in ("REGUP", "REGDN", "RRS", "NSPIN"):
            rows.append(pd.DataFrame({"published_utc": _ts([pub] * 24), "interval_start_utc": _ts([t.tz_convert("UTC") for t in local]), "product": prod,
                                      "mcpc_usd_per_mw_h": [10.0 * (d - pd.Timestamp("2024-12-01")).days + 0.1 * h + 1 for h in range(24)]}))
    _write("dam_as", pd.concat(rows, ignore_index=True))
    target = pd.Timestamp("2025-01-21 08:00").tz_localize(CPT).tz_convert("UTC")
    before = build.point_in_time(SP, pd.Timestamp("2025-01-20 12:00").tz_localize(CPT).tz_convert("UTC")).loc[target]
    assert np.isnan(before["dam_as_regup"]) and np.isnan(before["dam_as_regup_rel"]) and float(before["dam_as_regup_med14"]) == pytest.approx(436.8, abs=1e-3)
    after = build.point_in_time(SP, pd.Timestamp("2025-01-20 14:00").tz_localize(CPT).tz_convert("UTC")).loc[target]
    assert float(after["dam_as_regup"]) == pytest.approx(511.8, abs=1e-3) and float(after["dam_as_regup_med14"]) == pytest.approx(446.8, abs=1e-3)
    assert float(after["dam_as_regup_rel"]) == pytest.approx(511.8 / 446.8, rel=1e-6)
