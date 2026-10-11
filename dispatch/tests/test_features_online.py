"""The online feature store (fakeredis): one JSON value per settlement point, Redis 3.0 commands only, fail closed."""
from __future__ import annotations

import json

import fakeredis
import numpy as np
import pandas as pd
import pytest

from dispatch import config
from dispatch.features import online, sets

ASOF = pd.Timestamp("2026-10-10T16:05:00Z")


def make_frame(sp="HB_NORTH", asof=ASOF, n=96, seed=0) -> pd.DataFrame:
    """A frame shaped like build.point_in_time output."""
    rng = np.random.default_rng(seed)
    idx = pd.date_range(ASOF.floor("15min"), periods=n, freq="15min", name="interval_start_utc")
    data = {}
    for f in sets.FEATURE_TABLE:
        if f.dtype == "float32":
            data[f.name] = rng.normal(40, 15, n).astype("float32")
        else:
            data[f.name] = (np.arange(n) % 5).astype(f.dtype)
    df = pd.DataFrame(data, index=idx)
    df.loc[df.index[3], "dam_spp"] = np.nan                                  # NaN must survive the round trip
    df.loc[df.index[-3:], "rt_lag_1d"] = np.nan
    df["settlement_point"] = sp
    df["asof_utc"] = asof
    df["max_published_utc"] = pd.Timestamp("2026-10-10T16:02:02.117Z")
    df.loc[df.index[10], "max_published_utc"] = pd.Timestamp("2026-10-10T15:30:00.500Z")
    return df


@pytest.fixture()
def r():
    client = fakeredis.FakeStrictRedis()
    yield client
    client.flushall()


def test_round_trip_keeps_values_nans_dtypes_and_index(r):
    df = make_frame()
    online.write_latest("HB_NORTH", df, client=r)
    back, asof, mp = online.read_latest("HB_NORTH", client=r)
    assert asof == ASOF and mp == pd.Timestamp("2026-10-10T16:02:02.117Z")
    assert list(back.index) == list(df.index) and back.index.name == "interval_start_utc" and back.index.tz is not None
    for f in sets.FEATURE_TABLE:
        assert str(back[f.name].dtype) == f.dtype, f.name
        np.testing.assert_array_equal(back[f.name].to_numpy(), df[f.name].to_numpy())      # NaN == NaN here: same positions
    assert back["dam_spp"].isna().sum() == 1 and back["rt_lag_1d"].isna().sum() == 3
    assert (back["asof_utc"] == ASOF).all() and back["settlement_point"].eq("HB_NORTH").all()
    pd.testing.assert_series_equal(back["max_published_utc"].reset_index(drop=True), df["max_published_utc"].reset_index(drop=True),
                                   check_names=False, check_dtype=False)


def test_one_json_value_under_the_documented_key(r):
    online.write_latest("HB_WEST", make_frame("HB_WEST"), client=r)
    key = config.FEATURES_LATEST_KEY.format(sp="HB_WEST")
    assert key == "dispatch:features:latest:HB_WEST"
    assert r.keys("*") == [key.encode()]
    assert r.type(key) == b"string"
    payload = json.loads(r.get(key))                                          # strict JSON: allow_nan is off, NaN became null
    assert payload["sp"] == "HB_WEST" and payload["feature_set"] == sets.FEATURE_SET and payload["schema_hash"] == sets.schema_hash()
    assert payload["asof_utc"].startswith("2026-10-10T16:05:00")
    assert len(payload["index"]) == 96 and set(sets.FEATURES) <= set(payload["columns"])


def test_only_redis_3_0_commands_are_used(r):
    seen = []
    orig = r.execute_command

    def spy(*args, **kw):
        seen.append(str(args[0]).upper())
        return orig(*args, **kw)
    r.execute_command = spy
    online.write_latest("HB_NORTH", make_frame(), client=r, ttl_s=600)
    online.read_latest("HB_NORTH", client=r)
    online.OnlineStore(r).delete("HB_NORTH")
    assert set(seen) <= {"SET", "GET", "DEL", "EXPIRE"}, seen                 # SET ... EX is a SET; Redis 3.0 has all of these
    assert "HSET" not in seen and "HELLO" not in seen and "HMSET" not in seen


def test_write_replaces_the_previous_frame(r):
    online.write_latest("HB_NORTH", make_frame(seed=1), client=r)
    new = make_frame(asof=ASOF + pd.Timedelta(minutes=5), seed=2)
    online.write_latest("HB_NORTH", new, client=r)
    back, asof, _ = online.read_latest("HB_NORTH", client=r)
    assert asof == ASOF + pd.Timedelta(minutes=5)
    np.testing.assert_array_equal(back["rt_last"].to_numpy(), new["rt_last"].to_numpy())
    assert len(r.keys("*")) == 1


def test_settlement_points_are_independent(r):
    online.write_latest("HB_NORTH", make_frame("HB_NORTH", seed=1), client=r)
    online.write_latest("HB_SOUTH", make_frame("HB_SOUTH", seed=2), client=r)
    a, _, _ = online.read_latest("HB_NORTH", client=r)
    b, _, _ = online.read_latest("HB_SOUTH", client=r)
    assert a["settlement_point"].iloc[0] == "HB_NORTH" and b["settlement_point"].iloc[0] == "HB_SOUTH"
    assert not np.array_equal(a["rt_last"].to_numpy(), b["rt_last"].to_numpy())


def test_ttl_is_optional_and_applies(r):
    online.write_latest("HB_NORTH", make_frame(), client=r)
    assert r.ttl(config.FEATURES_LATEST_KEY.format(sp="HB_NORTH")) == -1
    online.write_latest("HB_NORTH", make_frame(), client=r, ttl_s=300)
    assert 0 < r.ttl(config.FEATURES_LATEST_KEY.format(sp="HB_NORTH")) <= 300


def test_missing_key_is_a_clear_error_not_an_empty_frame(r):
    with pytest.raises(online.FeaturesMissing, match="HB_NORTH.*fail closed"):
        online.read_latest("HB_NORTH", client=r)


def test_expired_features_are_missing(r):
    import time
    online.write_latest("HB_NORTH", make_frame(), client=r, ttl_s=1)
    online.read_latest("HB_NORTH", client=r)                                 # still there
    time.sleep(1.3)
    with pytest.raises(online.FeaturesMissing):
        online.read_latest("HB_NORTH", client=r)


def test_features_missing_is_a_lookup_error():
    """D2's cycle treats LookupError as 'there are no features' (status stale), not as a platform error."""
    assert issubclass(online.FeaturesMissing, LookupError) and issubclass(online.FeaturesMissing, online.OnlineStoreError)
    try:
        online.read_latest("HB_NORTH", client=fakeredis.FakeStrictRedis())
    except LookupError as e:
        assert isinstance(e, online.FeaturesMissing) and "fail closed" in str(e)
    else:
        raise AssertionError("expected a LookupError")


@pytest.mark.parametrize("garbage", [b"not json", b"{}", b'{"v": 99}', b'{"v": 1, "index": [1]}', b""])
def test_unreadable_payloads_fail_closed(r, garbage):
    r.set(config.FEATURES_LATEST_KEY.format(sp="HB_NORTH"), garbage)
    with pytest.raises(online.OnlineStoreError):
        online.read_latest("HB_NORTH", client=r)


def test_redis_errors_become_online_store_errors():
    class Broken:
        def get(self, *a, **k):
            raise ConnectionError("connection refused")

        def set(self, *a, **k):
            raise RuntimeError("OOM command not allowed when used memory > 'maxmemory'")
    with pytest.raises(online.OnlineStoreError, match="connection refused"):
        online.read_latest("HB_NORTH", client=Broken())
    with pytest.raises(online.OnlineStoreError, match="maxmemory"):
        online.write_latest("HB_NORTH", make_frame(), client=Broken())


def test_write_validates_its_input(r):
    good = make_frame()
    with pytest.raises(ValueError):
        online.write_latest("HB_NORTH", good.iloc[:0], client=r)                              # empty
    with pytest.raises(ValueError):
        online.write_latest("HB_NORTH", good.drop(columns=["asof_utc"]), client=r)
    with pytest.raises(ValueError):
        online.write_latest("HB_NORTH", good.reset_index(drop=True), client=r)                # not indexed by interval
    mixed = good.copy()
    mixed.loc[mixed.index[0], "asof_utc"] = ASOF + pd.Timedelta(minutes=1)
    with pytest.raises(ValueError):
        online.write_latest("HB_NORTH", mixed, client=r)                                      # two decision times in one frame
    assert r.keys("*") == []                                                                  # nothing half-written


def test_class_implements_the_contract_protocol(r):
    import inspect
    from dispatch.contracts import OnlineStore as Contract
    store = online.OnlineStore(r)
    for name in ("write_latest", "read_latest"):
        want = [p for p in inspect.signature(getattr(Contract, name)).parameters if p != "self"]
        got = list(inspect.signature(getattr(store, name)).parameters)
        assert got == want, (name, got, want)
    store.write_latest("HB_HOUSTON", make_frame("HB_HOUSTON"))
    df, asof, mp = store.read_latest("HB_HOUSTON")
    assert asof == ASOF and len(df) == 96


def test_file_store_has_the_same_behaviour(dispatch_home):
    store = online.FileStore()
    with pytest.raises(online.FeaturesMissing):
        store.read_latest("HB_NORTH")
    store.write_latest("HB_NORTH", make_frame())
    back, asof, mp = store.read_latest("HB_NORTH")
    assert asof == ASOF and len(back) == 96 and mp == pd.Timestamp("2026-10-10T16:02:02.117Z")
    store.delete("HB_NORTH")
    with pytest.raises(online.FeaturesMissing):
        store.read_latest("HB_NORTH")


def test_connect_forces_resp2_for_redis_3_and_defaults_to_the_platform_redis():
    """redis-py 8 defaults to RESP3 and opens with HELLO, which Redis 3.0 rejects ('unknown command HELLO'). The platform
    Redis is the dedicated instance on :6390 (config.REDIS_URL), not the shared one on :6379."""
    c = online.connect()
    assert c.connection_pool.connection_kwargs.get("protocol") == 2
    assert c.connection_pool.connection_kwargs.get("port") == 6390 and config.REDIS_URL.endswith(":6390/0")
    assert online.connect("redis://localhost:6379/0").connection_pool.connection_kwargs.get("protocol") == 2


def test_refresh_latest_keeps_going_when_one_point_fails(r, monkeypatch):
    from dispatch.features import build

    def fake_pit(sp, asof, *a, **k):
        if sp == "BAD_SP":
            raise build.FeatureDataError("no RT SPP for 'BAD_SP'")
        return make_frame(sp, asof=utc_asof(asof))
    monkeypatch.setattr(build, "point_in_time", fake_pit)
    out = online.refresh_latest(["HB_NORTH", "BAD_SP", "HB_WEST"], ASOF, store=online.OnlineStore(r))
    assert "error" in out["BAD_SP"] and "no RT SPP" in out["BAD_SP"]["error"]
    assert out["HB_NORTH"]["rows"] == 96 and out["HB_WEST"]["rows"] == 96
    assert online.read_latest("HB_NORTH", client=r)[1] == ASOF
    with pytest.raises(online.FeaturesMissing):
        online.read_latest("BAD_SP", client=r)


def utc_asof(a):
    return pd.Timestamp(a).tz_convert("UTC")
