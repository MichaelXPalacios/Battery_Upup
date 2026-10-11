"""Synthetic prices, a synthetic D0 lake, and fake forecasters for tests (D1a). Nothing here touches ERCOT data or the real MLflow store.

`SyntheticLake` writes synthetic prices into a throw-away parsed lake (the real dataset schemas, the real parquet writer, publish times by D0's
own assumption) and builds frames with D0's point-in-time builder, `features.build`, over it. That replaced D1a's interim builder as the test
data source when the interim builder was retired (11 Oct 2026): every model test now trains and serves on the columns production uses.

    lake = SyntheticLake("HB_TEST", synthetic_prices(days=150))
    X, y, decision = lake.training_frame(start, end, "4h")          # D0's training_frame
    frame = lake.point_in_time(asof, 96)                            # D0's point_in_time (the frame the online store holds)

`ConstantForecaster` is a deterministic `contracts.Forecaster` other agents can use as a fake in their own tests:
    from dispatch.models.synthetic import ConstantForecaster
"""
from __future__ import annotations

import contextlib
import tempfile
from pathlib import Path
from typing import Mapping, Optional, Union

import numpy as np
import pandas as pd

from .. import paths
from ..contracts import RT, ForecastFrame
from ..data import assumptions as A
from ..data import parse
from ..data.times import CPT
from ..features import build
from .quantile import ASPriceModel, ModelSpec, QuantileForecaster, SPModel, _max_published, to_utc

STEP = pd.Timedelta(minutes=15)
WARMUP_STEPS = 16 * 96                  # D0 reads 15 days behind a decision (features/sets.py LOOKBACK_DAYS): the first decision comes later


def synthetic_prices(start="2024-01-07", days: int = 150, seed: int = 0, level: float = 30.0,
                     spike_rate: float = 0.004, amplitude: float = 1.0) -> pd.Series:
    """15-minute UTC prices with a daily shape (morning ramp, midday solar dip, evening peak), a weekend dip, an
    autocorrelated daily level, lognormal noise and rare spikes. Starts away from a DST change on purpose."""
    rng = np.random.default_rng(seed)
    idx = pd.date_range(pd.Timestamp(start, tz="UTC"), periods=days * 96, freq="15min", name="interval_start_utc")
    loc = idx.tz_convert("America/Chicago")
    hod = loc.hour.to_numpy() + loc.minute.to_numpy() / 60.0
    dow = loc.dayofweek.to_numpy()
    shape = (6.0 * np.exp(-0.5 * ((hod - 19.5) / 1.8) ** 2) + 3.0 * np.exp(-0.5 * ((hod - 7.5) / 1.5) ** 2)
             - 7.0 * np.exp(-0.5 * ((hod - 13.0) / 2.5) ** 2))
    day_level = np.repeat(level * np.exp(np.cumsum(rng.normal(0, 0.05, days)) * 0.3), 96)
    weekend = np.where(dow >= 5, 0.85, 1.0)
    noise = rng.lognormal(0.0, 0.12, len(idx))
    p = (day_level + amplitude * shape * day_level / level) * weekend * noise
    spikes = rng.random(len(idx)) < spike_rate
    p = np.where(spikes, p + rng.exponential(250.0, len(idx)), p)
    return pd.Series(np.round(p, 2), index=idx, name="rt_usd_per_mwh")


# ================================================================================================ a synthetic D0 lake
@contextlib.contextmanager
def _parsed_dir(path: Path):
    """Point `paths.PARSED` (what `data.parse` reads and writes) at `path` for the duration of the block. Restores it on exit."""
    old = paths.PARSED
    paths.PARSED = Path(path)
    try:
        yield
    finally:
        paths.PARSED = old


def _utc_us(values) -> pd.Series:
    return pd.Series(pd.DatetimeIndex(values)).dt.tz_convert("UTC").astype("datetime64[us, UTC]")


def write_parsed(parsed_dir: Path, name: str, df: pd.DataFrame) -> None:
    """Write rows into a bulk dataset of the parsed lake under `parsed_dir`, one partition per Central month of the interval
    (the layout `data.parse` gives the archive datasets). `df` has the dataset's columns except `pub_basis` and `raw_file`."""
    ds = parse.DATASETS[name]
    df = df.assign(pub_basis="ASSUMPTION", raw_file="synthetic")[[f.name for f in ds.schema]]
    keys = pd.DatetimeIndex(df["interval_start_utc"]).tz_convert(CPT).strftime("%Y-%m-01")
    with _parsed_dir(parsed_dir):
        for key, part in df.groupby(keys):
            part = part.sort_values(list(ds.sort_keys), kind="stable", ignore_index=True)
            parse.write_table(parse.partition_path(name, key), parse._to_table(part, ds, {"dispatch.dataset": name, "dispatch.group": key}))


def rt_rows(sp: str, prices: pd.Series, *, sp_type: str = "HU") -> pd.DataFrame:
    """`rt_spp_hist` rows for a price series on the 15-minute UTC grid, published the way the archive parser assumes: interval end plus
    `RT_PRICE_PUBLISH_LAG`."""
    s = prices.dropna()
    start = pd.DatetimeIndex(s.index)
    return pd.DataFrame({"published_utc": _utc_us(start + STEP + A.RT_PRICE_PUBLISH_LAG), "interval_start_utc": _utc_us(start),
                         "settlement_point": sp, "sp_type": sp_type, "price_usd_per_mwh": s.to_numpy(dtype="float64")})


def dam_as_rows(frame: pd.DataFrame) -> pd.DataFrame:
    """`dam_as_hist` rows for a wide frame (hourly UTC index, one column per product), published at D-1 14:00 Central (D0's assumption)."""
    h, m = A.DAM_PUBLISH_LOCAL_HOUR_MINUTE
    parts = []
    for col in frame.columns:
        s = frame[col].dropna()
        start = pd.DatetimeIndex(s.index).tz_convert("UTC")
        day = start.tz_convert(CPT).tz_localize(None).normalize()
        pub = (day - pd.Timedelta(days=A.DAM_PUBLISH_DAYS_AHEAD) + pd.Timedelta(hours=h, minutes=m)).tz_localize(CPT, ambiguous=False, nonexistent="shift_forward")
        parts.append(pd.DataFrame({"published_utc": _utc_us(pub), "interval_start_utc": _utc_us(start), "product": str(col).upper(),
                                   "mcpc_usd_per_mw_h": s.to_numpy(dtype="float64")}))
    return pd.concat(parts, ignore_index=True)


class SyntheticLake:
    """Synthetic 15-minute prices (and optionally day-ahead AS prices) in a parsed lake of their own, with D0's builder over it.

    `prices` is one Series (then `sp` names its settlement point) or a mapping settlement point -> Series. The lake lives in a temporary
    directory that goes away with the object; the real `DISPATCH_HOME` is never touched (only `paths.PARSED` is pointed elsewhere, and only
    while the lake is written and read once into a `features.build.Context`; after that no call needs the files).

    Publish times are D0's: a price is public 25 minutes after its interval starts (`RT_PRICE_PUBLISH_LAG` after it ends), day-ahead
    results at 14:00 Central on the day before. Weather, gas and load forecasts do not exist here: their columns are NaN, as D0 defines.
    """

    def __init__(self, sp: Union[str, Mapping[str, pd.Series]] = "HB_TEST", prices: Optional[pd.Series] = None, dam_as: Optional[pd.DataFrame] = None):
        series = dict(sp) if isinstance(sp, Mapping) else {str(sp): prices}
        if any(v is None for v in series.values()):
            raise ValueError("prices are required")
        self.sp = next(iter(series))
        self._tmp = tempfile.TemporaryDirectory(prefix="dispatch_synth_")
        self.parsed = Path(self._tmp.name) / "parsed"
        self._series = {k: self._grid(v) for k, v in series.items()}
        self.dam_as = dam_as
        write_parsed(self.parsed, "rt_spp_hist", pd.concat([rt_rows(k, v) for k, v in self._series.items()], ignore_index=True))
        if dam_as is not None:
            write_parsed(self.parsed, "dam_as_hist", dam_as_rows(dam_as))
        lo = min(v.index[0] for v in self._series.values())
        hi = max(v.index[-1] for v in self._series.values()) + STEP
        self._ctx = {}
        with _parsed_dir(self.parsed):
            for k in self._series:
                self._ctx[k] = build.load_context(k, lo, hi + pd.Timedelta(days=1))

    @staticmethod
    def _grid(s: pd.Series) -> pd.Series:
        s = s[~s.index.duplicated(keep="first")].sort_index()
        idx = pd.date_range(s.index[0], s.index[-1], freq="15min", name="interval_start_utc")
        return s.reindex(idx).rename(RT)

    # ---- the price grid (what D1a's PriceHistory offered the tests)
    @property
    def start(self) -> pd.Timestamp:
        return self._series[self.sp].index[0]

    @property
    def end(self) -> pd.Timestamp:
        """End of the last interval."""
        return self._series[self.sp].index[-1] + STEP

    @property
    def values(self) -> np.ndarray:
        return self._series[self.sp].to_numpy(dtype="float64")

    def __len__(self) -> int:
        return len(self._series[self.sp])

    def series(self, sp: Optional[str] = None) -> pd.Series:
        return self._series[sp or self.sp].copy()

    def ts(self, pos) -> pd.Timestamp:
        return self.start + int(pos) * STEP

    def pos(self, ts) -> int:
        t = pd.Timestamp(ts)
        t = t.tz_localize("UTC") if t.tzinfo is None else t.tz_convert("UTC")
        return int((t - self.start) // STEP)

    # ---- D0's builder over the lake
    def point_in_time(self, asof, horizon: int = 96, *, sp: Optional[str] = None) -> pd.DataFrame:
        """`features.build.point_in_time` at `asof` (tz-aware or a string read as UTC): the frame the online store would hold."""
        t = pd.Timestamp(asof)
        t = t.tz_localize("UTC") if t.tzinfo is None else t.tz_convert("UTC")
        k = sp or self.sp
        return build.point_in_time(k, t, horizon, context=self._ctx[k])

    def raw_training_frame(self, sp: str, start, end, every: str = "4h", offset: str = "0min"):
        """`features.build.training_frame` exactly as D0 returns it: (X, y, decision_utc) on a RangeIndex."""
        return build.training_frame(sp, _ts(start), _ts(end), every, context=self._ctx[sp], offset=offset)

    def training_frame(self, start=None, end=None, every: str = "4h", *, sp: Optional[str] = None, offset: str = "0min"):
        """(X, y, decision_utc) laid out for tests: X indexed by the target interval (not unique: several decisions share a target), y a Series
        of the realised price of that interval, decision_utc a Series of the decision times, all three on the same index."""
        k = sp or self.sp
        start = self.ts(WARMUP_STEPS) if start is None else start
        end = self.end - pd.Timedelta(days=1) if end is None else end
        X, y, d = self.raw_training_frame(k, start, end, every, offset)
        idx = pd.DatetimeIndex(X["interval_start_utc"]).rename("interval_start_utc")
        X = X.set_index(idx, drop=False)
        X.attrs["settlement_point"] = k
        return X, pd.Series(y.to_numpy(), index=idx, name=RT), pd.Series(pd.DatetimeIndex(d), index=idx, name="decision_utc")

    # ---- hooks for models.train.TrainConfig
    def frames_provider(self, sp, start, end, every, offset):
        return self.raw_training_frame(sp, start, end, every, offset)

    def prices_provider(self, sp):
        return self.series(sp)

    def as_provider(self):
        return self.dam_as


def _ts(x) -> pd.Timestamp:
    t = pd.Timestamp(x)
    return t.tz_localize("UTC") if t.tzinfo is None else t.tz_convert("UTC")


def synthetic_history(sp: str = "HB_TEST", **kw) -> SyntheticLake:
    """A `SyntheticLake` over `synthetic_prices(**kw)` (the name D1a's tests used for the price-history object)."""
    return SyntheticLake(sp, synthetic_prices(**kw))


def synthetic_training(sp: str = "HB_TEST", days: int = 150, every: str = "4h", seed: int = 0):
    """(X, y, decision_utc, lake) from D0's builder on synthetic prices."""
    lake = synthetic_history(sp, days=days, seed=seed)
    X, y, d = lake.training_frame(lake.ts(WARMUP_STEPS + 8), lake.end - pd.Timedelta(days=1), every)
    return X, y, d, lake


def hetero_regression(n: int = 20000, seed: int = 0):
    """y = signal(x) + sigma(x) * noise with sigma growing in `vol` and `horizon`: for quantile-coverage tests.

    Returns (features frame indexed by 15-minute UTC timestamps, y). Columns: x1, x2, vol, horizon."""
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2025-02-01", periods=n, freq="15min", tz="UTC", name="interval_start_utc")
    x1 = rng.uniform(0, 1, n)
    x2 = rng.normal(0, 1, n)
    vol = rng.uniform(1.0, 5.0, n)
    horizon = rng.integers(1, 97, n)
    sigma = vol * (1.0 + horizon / 96.0)
    y = 20.0 + 15.0 * x1 + 5.0 * x2 + sigma * rng.normal(0, 1, n)
    X = pd.DataFrame({"x1": x1, "x2": x2, "vol": vol, "horizon": horizon}, index=idx)
    return X, pd.Series(y, index=idx, name="y")


def fit_tiny_forecaster(sp: str = "HB_TEST", *, n_estimators: int = 40, days: int = 150, seed: int = 0,
                        with_as: bool = True, normalised: bool = True) -> tuple:
    """A small real QuantileForecaster on synthetic rt_v1 features (D0's builder). Returns (forecaster, X, y, decision_utc, lake)."""
    from .train import FEATURE_SET_LABEL, RT_FEATURES
    X, y, d, lake = synthetic_training(sp, days=days, seed=seed)
    spec = ModelSpec(feature_names=tuple(RT_FEATURES), anchor_col="rt_prof7_med" if normalised else None,
                     scale_cols=("rt_q90_672", "rt_q10_672") if normalised else (), weight_pow=0.5 if normalised else 0.0,
                     required_nonnull=("rt_last", "rt_mean_24h"), n_estimators=n_estimators,
                     params=dict(num_leaves=15, min_data_in_leaf=50, max_bin=63))
    m = SPModel.fit(X, y.to_numpy(), spec, num_threads=4, info={"train_rows": len(X)})
    m.set_reference(X, n=500)
    as_model = None
    if with_as:
        loc = lake.series().index
        hist_as = pd.DataFrame({p: 1.0 + 0.2 * np.sin(np.arange(len(loc)) / 40.0) + (i * 0.1)
                                for i, p in enumerate(["regup", "regdn", "rrs", "ecrs", "nspin"])}, index=loc)
        as_model = ASPriceModel.fit(hist_as, lake.end)
    fc = QuantileForecaster({sp: m}, feature_set=FEATURE_SET_LABEL, as_model=as_model, meta={"synthetic": True})
    return fc, X, y, d, lake


class ConstantForecaster:
    """A trivially predictable Forecaster for other agents' tests: p50 = `level` $/MWh, p10/p90 = level -/+ `spread`.

    It applies the same fail-closed checks as the real model (tz-aware index, max_published_utc <= issued_utc)."""
    kind = "constant"

    def __init__(self, level: float = 30.0, spread: float = 10.0, version: str = "constant-1", settlement_point: str = "HB_NORTH"):
        self.level, self.spread, self.version, self.settlement_point = level, spread, version, settlement_point

    def predict(self, features: pd.DataFrame, issued_utc, *, settlement_point: Optional[str] = None) -> ForecastFrame:
        issued = to_utc(issued_utc, "issued_utc")
        max_pub = _max_published(features)
        if max_pub > issued:
            raise ValueError("features published after the issue time")
        idx = features.index.tz_convert("UTC")
        frame = pd.DataFrame({"rt_p10": self.level - self.spread, "rt_p50": self.level, "rt_p90": self.level + self.spread},
                             index=idx)
        return ForecastFrame(issued_utc=issued, settlement_point=settlement_point or self.settlement_point,
                             model_version=self.version, frame=frame,
                             max_feature_published_utc=max_pub)
