"""The online feature store: the latest `point_in_time` frame per settlement point, as ONE JSON value in Redis.

    write_latest(sp, df)  -> None
    read_latest(sp)       -> (df, asof_utc, max_published_utc)

Key: `config.FEATURES_LATEST_KEY.format(sp=sp)`. Only SET / GET / DEL / EXPIRE are used (Redis 3.0 compatible: the local server
is 3.0.504, which has no HELLO, so the client must speak RESP2; `connect()` sets protocol=2). Tests inject a fakeredis client.

Fail closed: if the key is missing, expired, or holds something unreadable, `read_latest` raises `FeaturesMissing` /
`OnlineStoreError` with the reason. It never returns an empty or partial frame, and it never retries on its own.
"""
from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

from .. import config, paths
from ..data.times import iso_z, utc
from . import sets

PAYLOAD_VERSION = 1


class OnlineStoreError(RuntimeError):
    """Redis (or the file store) could not be reached or answered with an error."""


class FeaturesMissing(OnlineStoreError, LookupError):
    """There are no usable online features for this settlement point. A LookupError, so callers can catch 'not there'
    without importing this module (D2's cycle does: no features is a data problem, status `stale`, not a platform error)."""


def key_for(sp: str) -> str:
    return config.FEATURES_LATEST_KEY.format(sp=sp)


def connect(url: Optional[str] = None, *, timeout_s: float = 3.0):
    """A redis-py client for the platform Redis. RESP2 is forced because Redis 3.0 does not know HELLO."""
    import redis
    return redis.Redis.from_url(url or config.REDIS_URL, protocol=2, socket_timeout=timeout_s, socket_connect_timeout=timeout_s)


# ------------------------------------------------------------------------------------------------ payload
def _num(v, dtype: str):
    if v is None or (isinstance(v, float) and math.isnan(v)) or (isinstance(v, (np.floating,)) and np.isnan(v)):
        return None
    if dtype.startswith("float"):
        return float(np.format_float_positional(np.float32(v), unique=True)) if dtype == "float32" else float(v)
    return int(v)


def encode(sp: str, df: pd.DataFrame) -> str:
    """DataFrame (as returned by `build.point_in_time`) -> one JSON string. Index = interval_start_utc."""
    for need in ("asof_utc", "max_published_utc"):
        if need not in df.columns:
            raise ValueError(f"df must carry an {need!r} column (use build.point_in_time)")
    if not isinstance(df.index, pd.DatetimeIndex) or df.index.tz is None:
        raise ValueError("df must be indexed by tz-aware interval_start_utc")
    if df.empty:
        raise ValueError("refusing to store an empty feature frame")
    asof = utc(df["asof_utc"].iloc[0])
    if df["asof_utc"].nunique() != 1:
        raise ValueError("one frame must have one asof_utc")
    mp = df["max_published_utc"].max()
    columns, kinds, data = [], [], []
    for c in df.columns:
        s = df[c]
        if c in ("asof_utc", "max_published_utc"):
            continue
        columns.append(c)
        if pd.api.types.is_datetime64_any_dtype(s):
            kinds.append("time")
            data.append([None if pd.isna(x) else iso_z(x) for x in s])
        elif pd.api.types.is_numeric_dtype(s) or pd.api.types.is_bool_dtype(s):
            kinds.append(str(s.dtype))
            data.append([_num(x, str(s.dtype)) for x in s])
        else:
            kinds.append("str")
            data.append([None if pd.isna(x) else str(x) for x in s])
    payload = {"v": PAYLOAD_VERSION, "feature_set": sets.FEATURE_SET, "version": sets.VERSION, "schema_hash": sets.schema_hash(),
               "sp": sp, "asof_utc": iso_z(asof), "max_published_utc": None if pd.isna(mp) else iso_z(mp),
               "written_utc": iso_z(pd.Timestamp.now(tz="UTC")), "index": [iso_z(t) for t in df.index], "index_name": df.index.name,
               "columns": columns, "kinds": kinds, "data": data,
               "row_max_published_utc": [None if pd.isna(x) else iso_z(x) for x in df["max_published_utc"]]}
    return json.dumps(payload, separators=(",", ":"), allow_nan=False)


def decode(raw: bytes | str) -> tuple[pd.DataFrame, pd.Timestamp, pd.Timestamp]:
    p = json.loads(raw)
    if p.get("v") != PAYLOAD_VERSION:
        raise OnlineStoreError(f"unsupported online payload version {p.get('v')!r}")
    idx = pd.DatetimeIndex([utc(t) for t in p["index"]], name=p.get("index_name") or "interval_start_utc").as_unit("us")
    cols = {}
    for name, kind, vals in zip(p["columns"], p["kinds"], p["data"]):
        if kind == "time":
            cols[name] = pd.to_datetime([None if v is None else v for v in vals], utc=True).as_unit("us")
        elif kind == "str":
            cols[name] = pd.array(vals, dtype="str")
        elif kind.startswith("float"):
            cols[name] = np.array([np.nan if v is None else v for v in vals], dtype=kind)
        else:
            cols[name] = np.array(vals, dtype=kind)
    df = pd.DataFrame(cols, index=idx)
    asof = utc(p["asof_utc"])
    df.insert(len(df.columns), "asof_utc", pd.Series([asof] * len(df), index=idx, dtype="datetime64[us, UTC]"))
    rows = p.get("row_max_published_utc")
    df["max_published_utc"] = pd.to_datetime([None if v is None else v for v in rows], utc=True).as_unit("us") if rows is not None else pd.NaT
    mp = utc(p["max_published_utc"]) if p.get("max_published_utc") else pd.NaT
    return df, asof, mp


# ------------------------------------------------------------------------------------------------ Redis store
class OnlineStore:
    """Redis-backed store (implements contracts.OnlineStore). Inject `client` in tests (fakeredis)."""

    def __init__(self, client=None, *, ttl_s: Optional[int] = None):
        self._client = client
        self.ttl_s = ttl_s

    @property
    def client(self):
        if self._client is None:
            self._client = connect()
        return self._client

    def write_latest(self, sp: str, df: pd.DataFrame) -> None:
        body = encode(sp, df)
        try:
            self.client.set(key_for(sp), body, ex=self.ttl_s) if self.ttl_s else self.client.set(key_for(sp), body)
        except Exception as e:
            raise OnlineStoreError(f"could not write online features for {sp!r}: {type(e).__name__}: {e}") from e

    def read_latest(self, sp: str) -> tuple[pd.DataFrame, pd.Timestamp, pd.Timestamp]:
        try:
            raw = self.client.get(key_for(sp))
        except Exception as e:
            raise OnlineStoreError(f"could not read online features for {sp!r}: {type(e).__name__}: {e}") from e
        if raw is None:
            raise FeaturesMissing(f"no online features for {sp!r} (key {key_for(sp)} is absent or expired): fail closed")
        try:
            return decode(raw)
        except OnlineStoreError:
            raise
        except Exception as e:
            raise FeaturesMissing(f"online features for {sp!r} are unreadable ({type(e).__name__}: {e}): fail closed") from e

    def delete(self, sp: str) -> None:
        try:
            self.client.delete(key_for(sp))
        except Exception as e:
            raise OnlineStoreError(f"could not delete online features for {sp!r}: {type(e).__name__}: {e}") from e


class FileStore(OnlineStore):
    """Same interface, one JSON file per settlement point under features/online/. For machines without a usable Redis
    (the local one is full today); NOT the default, and it keeps no TTL: callers must check `max_published_utc` age."""

    def __init__(self, root: Optional[Path] = None):
        super().__init__(client=None)
        self._root = Path(root) if root else None

    def _path(self, sp: str) -> Path:
        return (self._root or (paths.FEATURES / "online")) / f"{sp}.json"

    def write_latest(self, sp: str, df: pd.DataFrame) -> None:
        body = encode(sp, df)
        p = self._path(sp)
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".tmp")
        tmp.write_text(body, encoding="utf-8")
        tmp.replace(p)

    def read_latest(self, sp: str):
        p = self._path(sp)
        if not p.exists():
            raise FeaturesMissing(f"no online features for {sp!r} in {p}: fail closed")
        try:
            return decode(p.read_text(encoding="utf-8"))
        except Exception as e:
            raise FeaturesMissing(f"online features for {sp!r} are unreadable ({type(e).__name__}: {e}): fail closed") from e

    def delete(self, sp: str) -> None:
        self._path(sp).unlink(missing_ok=True)


# ------------------------------------------------------------------------------------------------ module-level API (DESIGN.md)
def write_latest(sp: str, df: pd.DataFrame, *, client=None, ttl_s: Optional[int] = None) -> None:
    """Store the latest frame for `sp` (replaces the previous one). Default client: the local Redis from config.REDIS_URL."""
    OnlineStore(client, ttl_s=ttl_s).write_latest(sp, df)


def read_latest(sp: str, *, client=None) -> tuple[pd.DataFrame, pd.Timestamp, pd.Timestamp]:
    """(frame, asof_utc, max_published_utc) of the latest stored features. Raises FeaturesMissing / OnlineStoreError."""
    return OnlineStore(client).read_latest(sp)


def refresh_latest(sps, asof_utc=None, *, store: Optional[OnlineStore] = None, log=lambda s: None) -> dict:
    """Build `point_in_time` at `asof_utc` (default: now) for each settlement point and store it. One failing point does not
    stop the others. Returns {sp: {'asof_utc', 'max_published_utc', 'rows'} or {'error': ...}}."""
    from . import build
    st = store or OnlineStore()
    asof = utc(asof_utc) if asof_utc is not None else pd.Timestamp.now(tz="UTC").floor("s")
    out = {}
    for sp in sps:
        try:
            df = build.point_in_time(sp, asof)
            st.write_latest(sp, df)
            out[sp] = {"asof_utc": iso_z(asof), "max_published_utc": iso_z(df["max_published_utc"].max()), "rows": len(df)}
            log(f"  online {sp}: asof {out[sp]['asof_utc']}, newest input {out[sp]['max_published_utc']}")
        except Exception as e:
            out[sp] = {"error": f"{type(e).__name__}: {e}"}
            log(f"  online {sp}: FAILED {out[sp]['error']}")
    return out
