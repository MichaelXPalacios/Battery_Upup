"""Interim point-in-time feature builder over the NP6-785-ER hub / load-zone real-time price history (D1a).

WHY THIS EXISTS. D0 owns the real builder (`dispatch.features.build`). This module is the fallback D1a trains on when
D0's `training_frame` is not available or cannot cover 2019-2024. It exposes the same two entry points, so switching is a
one-line change in `train.get_frame`:

    point_in_time(sp, asof_utc, horizon=96)                          -> DataFrame, one row per target interval
    training_frame(sp, start_utc, end_utc, decision_every="4h")      -> (X, y, decision_utc)

SOURCE. `research-data/ercot/rtm20xx.csv` (columns date, hour, interval, rep, sp, sptype, price; 2011 to 26 Sep 2026).
`hour` is the ERCOT delivery hour (1-24) in Central Prevailing Time, `interval` is 1-4 within the hour and
`rep == "Y"` flags the repeated fall-back hour. Timestamps are converted to UTC; an interval's `interval_start_utc` is the
start of the 15-minute settlement interval.

POINT IN TIME. ASSUMPTION `PRICE_PUBLISH_LAG`: an interval's price is public 15 minutes after the interval ends
(ERCOT posts the 15-minute SPP shortly after the interval; 15 minutes is a conservative round number, and the archive
itself carries no publish time). So the price of the interval that starts at `s` is published at `s + 30 min`. A decision
issued at `t0` may use an interval only when its published time is <= t0. Hence the latest usable interval at any `t0` in
the block [B, B + 15 min) is the one starting at `B - 30 min` (two steps before B), whatever minute inside the block t0
falls on. Every feature below is a function of the price positions <= that interval and of the calendar, never of anything
later. `max_published_utc` records the publish time of the newest price used and is checked by the leakage tests.

HORIZON. Target interval h (1..96) starts at `B + (h - 1) * 15 min`, with `B = floor_15min(t0)`. So h = 1 is the interval
containing the decision and h = 96 starts 23.75 h later.

SERVING LIMIT. This builder reads static archive files, so `point_in_time` is only as fresh as the archive (26 Sep 2026 here) and
raises StaleHistoryError for anything later. It serves research, replays and shadow runs. Live dispatch needs D0's ingestion and
online store (`features.online`); a model trained on these features is served from D0 once D0's `rt_v1` carries the same columns.

Wear: nothing here touches battery economics; it is a price-history feature set.
"""
from __future__ import annotations

import hashlib
import json
import warnings
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Sequence

import numpy as np
import pandas as pd

from .. import config, paths

FEATURE_SET = "interim_d1a"
FEATURE_VERSION = "v1"

STEP = pd.Timedelta(minutes=15)
STEPS_PER_DAY = 96
H_MAX = 96                                        # forecast horizon in 15-minute intervals (24 h)
PRICE_PUBLISH_LAG = pd.Timedelta(minutes=15)      # ASSUMPTION: an interval's price is public 15 min after it ends
DAM_AS_PUBLISH_LOCAL = "13:30"                    # ASSUMPTION: DAM AS prices for day D are public at 13:30 CPT on D-1
SPIKE_HI_USD = 100.0                              # ASSUMPTION: "high price" threshold for the frequency features ($/MWh)
LAG_DAYS = (1, 2, 3, 7, 14)
MIN_HISTORY_STEPS = 2 * 672 + 1                   # 7 days of profile plus a 7-day window of residuals behind the newest known interval
TZ = config.TZ_MARKET

# feature columns this builder emits (the model chooses its own subset by name)
DECISION_FEATURES = (
    "rt_last", "rt_mean_4", "rt_mean_16", "rt_mean_96", "rt_mean_672", "rt_std_96", "rt_std_672",
    "rt_min_96", "rt_max_96", "rt_q10_672", "rt_q50_672", "rt_q90_672", "rt_spread_96",
    "rt_frac_hi_96", "rt_frac_hi_672", "rt_frac_neg_96", "rt_frac_neg_672",
    # how far prices have recently sat from their own 7-day same-time-of-day median (the anchor): level and spread of
    # the residual, which is what a normalised target needs as its scale
    "res_last", "res_mean_4", "res_mean_16", "res_mean_96", "res_q10_96", "res_q50_96", "res_q90_96",
    "res_q10_672", "res_q50_672", "res_q90_672", "res_mad_672",
)
TARGET_FEATURES = (
    "horizon", "tgt_hod", "tgt_dow", "tgt_is_weekend", "tgt_is_holiday", "tgt_doy_sin", "tgt_doy_cos", "tgt_month",
    "rt_lag_1d", "rt_lag_2d", "rt_lag_3d", "rt_lag_7d", "rt_lag_14d",
    "rt_prof7_mean", "rt_prof7_med", "rt_prof7_min", "rt_prof7_max", "rt_prof7_std", "rt_prof14_dt",
)
RT_FEATURES = DECISION_FEATURES + TARGET_FEATURES
AS_FEATURE_SUFFIXES = ("dam", "med14", "rel")     # a daily-max variant was tried and did not help (notes/d1a.md)
AS_REL_FLOOR = 0.1                                # ASSUMPTION: $/MW-h floor under the 14-day median in the dam/med14 ratio


class StaleHistoryError(RuntimeError):
    """The price history ends before the newest interval a decision at `asof_utc` should have. Fail closed."""


# ------------------------------------------------------------------------------------------------ time helpers
def utc(ts) -> pd.Timestamp:
    """A pandas UTC Timestamp. A naive input (e.g. the string '2024-01-01') is read as UTC, the platform's one clock."""
    t = pd.Timestamp(ts)
    return t.tz_localize("UTC") if t.tzinfo is None else t.tz_convert("UTC")


def floor15(ts) -> pd.Timestamp:
    return utc(ts).floor("15min")


def parse_every(every) -> pd.Timedelta:
    d = pd.Timedelta(every)
    if d <= pd.Timedelta(0) or d % STEP:
        raise ValueError(f"decision_every={every!r} must be a positive multiple of 15 minutes")
    return d


# ------------------------------------------------------------------------------------------------ price history
@dataclass
class PriceHistory:
    """A regular 15-minute UTC price grid. `values[i]` is the price of the interval starting at `start + i * 15 min`;
    NaN marks a missing interval. `sources` lists the input files (name, bytes, sha256) for the data-snapshot hash."""
    sp: str
    start: pd.Timestamp
    values: np.ndarray
    sources: tuple = field(default_factory=tuple)

    def __post_init__(self):
        self.start = utc(self.start)
        self.values = np.asarray(self.values, dtype=np.float64)

    def __len__(self) -> int:
        return len(self.values)

    @property
    def end(self) -> pd.Timestamp:
        """End of the last interval."""
        return self.start + len(self) * STEP

    def pos(self, ts) -> int:
        """Grid position of the interval that starts at or contains `ts` (can be negative or past the end)."""
        return int((utc(ts) - self.start) // STEP)

    def ts(self, pos) -> pd.Timestamp:
        return self.start + int(pos) * STEP

    def published_utc(self, pos) -> pd.Timestamp:
        return self.ts(pos) + STEP + PRICE_PUBLISH_LAG

    def last_known_pos(self, asof_utc) -> int:
        """Position of the newest interval published at or before `asof_utc`."""
        return int((utc(asof_utc) - (STEP + PRICE_PUBLISH_LAG) - self.start) // STEP)

    def residual_grid(self) -> np.ndarray:
        """price - median of the same time of day over the previous 7 days, for every grid position (NaN until 7 days of
        history exist). A function of past prices only: position j needs positions j-96, ..., j-672."""
        g = getattr(self, "_resid", None)
        if g is None:
            P = self.values
            n = len(P)
            stack = np.full((n, 7), np.nan)
            for k in range(1, 8):
                stack[STEPS_PER_DAY * k:, k - 1] = P[:n - STEPS_PER_DAY * k]
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", RuntimeWarning)
                prof = np.nanmedian(stack, axis=1)
            g = P - prof
            self._resid = g
        return g

    def series(self) -> pd.Series:
        idx = pd.date_range(self.start, periods=len(self), freq="15min", name="interval_start_utc")
        return pd.Series(self.values, index=idx, name=config_rt_col())

    def truncated(self, asof_utc) -> "PriceHistory":
        """The history as it was at `asof_utc`: everything published later is removed."""
        k = self.last_known_pos(asof_utc)
        return PriceHistory(self.sp, self.start, self.values[: max(k + 1, 0)].copy(), self.sources)

    @classmethod
    def from_series(cls, sp: str, s: pd.Series, sources: Sequence[dict] = ()) -> "PriceHistory":
        """Build from a Series on a (possibly gappy) 15-minute tz-aware UTC index."""
        s = s[~s.index.duplicated(keep="first")].sort_index()
        start, end = utc(s.index[0]), utc(s.index[-1])
        idx = pd.date_range(start, end, freq="15min")
        vals = s.reindex(idx).to_numpy(dtype=np.float64)
        return cls(sp, start, vals, tuple(sources))


def config_rt_col() -> str:
    from ..contracts import RT
    return RT


def _read_year(path: Path, sp: str) -> pd.DataFrame:
    df = pd.read_csv(path, usecols=["date", "hour", "interval", "rep", "sp", "price"], engine="pyarrow")
    df = df[df["sp"] == sp]
    return df


def _to_utc(df: pd.DataFrame) -> pd.DatetimeIndex:
    """(date, hour, interval, rep) in Central Prevailing Time -> UTC interval start. The skipped spring hour has no rows;
    the repeated fall-back hour is the `rep == 'Y'` second pass (standard time)."""
    base = pd.to_datetime(df["date"], format="%Y-%m-%d").to_numpy().astype("datetime64[ns]")
    mins = ((df["hour"].to_numpy(dtype="int64") - 1) * 60 + (df["interval"].to_numpy(dtype="int64") - 1) * 15)
    local = pd.DatetimeIndex(base + mins.astype("timedelta64[m]").astype("timedelta64[ns]"))
    is_dst = (df["rep"].to_numpy() == "N")
    return local.tz_localize(TZ, ambiguous=is_dst, nonexistent="shift_forward").tz_convert("UTC")


def file_sha256(path: Path, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            b = f.read(chunk)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def _source_record(path: Path) -> dict:
    return {"name": path.name, "bytes": path.stat().st_size, "sha256": file_sha256(path)}


def history_files(data_dir: Optional[Path] = None, years: Optional[Sequence[int]] = None) -> list[Path]:
    d = Path(data_dir) if data_dir is not None else paths.ZONAL_HISTORY
    files = sorted(d.glob("rtm[0-9][0-9][0-9][0-9].csv"))
    if years is not None:
        want = {f"rtm{int(y)}.csv" for y in years}
        files = [f for f in files if f.name in want]
    return files


def load_history(sp: str, *, data_dir: Optional[Path] = None, years: Optional[Sequence[int]] = None,
                 cache: bool = True) -> PriceHistory:
    """Read the yearly csvs for one settlement point onto a regular UTC grid.

    A parquet cache keyed by the input files (name, bytes, mtime) lives under DISPATCH_HOME/mlflow/interim_cache, so a
    changed input file invalidates it. The sha256 of each input file goes into `sources` for the MLflow data snapshot.
    """
    files = history_files(data_dir, years)
    if not files:
        raise FileNotFoundError(f"no rtm*.csv price files under {data_dir or paths.ZONAL_HISTORY}")
    key = hashlib.sha256(json.dumps([(f.name, f.stat().st_size, f.stat().st_mtime_ns) for f in files]).encode()).hexdigest()[:16]
    cdir = paths.MLFLOW / "interim_cache"
    cfile = cdir / f"prices_{sp}_{key}.parquet"
    sfile = cdir / f"prices_{sp}_{key}.sources.json"
    if cache and cfile.exists() and sfile.exists():
        s = pd.read_parquet(cfile)["price"]
        sources = json.loads(sfile.read_text(encoding="utf-8"))
        return PriceHistory.from_series(sp, s, sources)
    parts = []
    for f in files:
        df = _read_year(f, sp)
        if len(df):
            parts.append(pd.Series(df["price"].to_numpy(dtype=np.float64), index=_to_utc(df)))
    if not parts:
        raise KeyError(f"settlement point {sp!r} not found in {[f.name for f in files]}")
    s = pd.concat(parts)
    s.index.name = "interval_start_utc"
    s = s[~s.index.duplicated(keep="first")].sort_index()
    sources = [_source_record(f) for f in files]
    hist = PriceHistory.from_series(sp, s, sources)
    if cache:
        cdir.mkdir(parents=True, exist_ok=True)
        hist.series().rename("price").to_frame().to_parquet(cfile)
        sfile.write_text(json.dumps(sources), encoding="utf-8")
    return hist


def data_snapshot(hist: PriceHistory) -> dict:
    """The data version of an interim run: the input files and one hash over them."""
    items = sorted(hist.sources, key=lambda r: r["name"])
    top = hashlib.sha256(json.dumps(items, sort_keys=True).encode()).hexdigest()
    return {"kind": "interim_files", "feature_set": f"{FEATURE_SET}/{FEATURE_VERSION}", "sha256": top,
            "n_items": len(items), "items": items, "publish_lag_min": PRICE_PUBLISH_LAG.total_seconds() / 60}


# ------------------------------------------------------------------------------------------------ calendar
def _holidays(lo: pd.Timestamp, hi: pd.Timestamp) -> pd.DatetimeIndex:
    from pandas.tseries.holiday import USFederalHolidayCalendar
    return USFederalHolidayCalendar().holidays(lo.tz_localize(None) - pd.Timedelta(days=2), hi.tz_localize(None) + pd.Timedelta(days=2))


def calendar_features(ts: pd.DatetimeIndex) -> pd.DataFrame:
    """Local-clock calendar features of interval start times (tz-aware UTC in). A pure function of the timestamp."""
    ts = pd.DatetimeIndex(ts)
    loc = ts.tz_convert(TZ)
    hod = loc.hour.to_numpy() + loc.minute.to_numpy() / 60.0
    dow = loc.dayofweek.to_numpy()
    doy = loc.dayofyear.to_numpy()
    day = loc.tz_localize(None).normalize()
    hol = day.isin(_holidays(day.min(), day.max())) if len(ts) else np.zeros(0, bool)
    ang = 2 * np.pi * (doy - 1) / 365.25
    return pd.DataFrame({
        "tgt_hod": hod.astype(np.float32), "tgt_dow": dow.astype(np.int8),
        "tgt_is_weekend": (dow >= 5).astype(np.int8), "tgt_is_holiday": np.asarray(hol, dtype=np.int8),
        "tgt_doy_sin": np.sin(ang).astype(np.float32), "tgt_doy_cos": np.cos(ang).astype(np.float32),
        "tgt_month": loc.month.to_numpy().astype(np.int8),
    }, index=ts)


# ------------------------------------------------------------------------------------------------ core
def _ffill(v: np.ndarray) -> np.ndarray:
    """Forward fill (past values only); a leading gap takes the first valid value."""
    s = pd.Series(v)
    return s.ffill().bfill().to_numpy()


def _decision_block(Pf: np.ndarray, K: np.ndarray, R: Optional[np.ndarray] = None) -> dict:
    """Trailing-window statistics of the price positions <= K (the newest published interval), per decision."""
    out = {"rt_last": Pf[K]}
    offs = {w: np.arange(-w + 1, 1) for w in (4, 16, 96, 672)}
    W4 = Pf[np.maximum(K[:, None] + offs[4][None, :], 0)]
    W16 = Pf[np.maximum(K[:, None] + offs[16][None, :], 0)]
    W96 = Pf[np.maximum(K[:, None] + offs[96][None, :], 0)]
    W672 = Pf[np.maximum(K[:, None] + offs[672][None, :], 0)]
    out["rt_mean_4"] = W4.mean(1)
    out["rt_mean_16"] = W16.mean(1)
    out["rt_mean_96"] = W96.mean(1)
    out["rt_mean_672"] = W672.mean(1)
    out["rt_std_96"] = W96.std(1)
    out["rt_std_672"] = W672.std(1)
    out["rt_min_96"] = W96.min(1)
    out["rt_max_96"] = W96.max(1)
    q = np.quantile(W672, [0.1, 0.5, 0.9], axis=1)
    out["rt_q10_672"], out["rt_q50_672"], out["rt_q90_672"] = q[0], q[1], q[2]
    srt = np.sort(W96, axis=1)
    out["rt_spread_96"] = srt[:, -8:].mean(1) - srt[:, :8].mean(1)       # the arena's daily top-8 minus bottom-8 spread
    out["rt_frac_hi_96"] = (W96 >= SPIKE_HI_USD).mean(1)
    out["rt_frac_hi_672"] = (W672 >= SPIKE_HI_USD).mean(1)
    out["rt_frac_neg_96"] = (W96 < 0).mean(1)
    out["rt_frac_neg_672"] = (W672 < 0).mean(1)
    if R is not None:
        r4, r16 = R[np.maximum(K[:, None] + offs[4][None, :], 0)], R[np.maximum(K[:, None] + offs[16][None, :], 0)]
        r96, r672 = R[np.maximum(K[:, None] + offs[96][None, :], 0)], R[np.maximum(K[:, None] + offs[672][None, :], 0)]
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)
            out["res_last"] = R[K]
            out["res_mean_4"] = np.nanmean(r4, axis=1)
            out["res_mean_16"] = np.nanmean(r16, axis=1)
            out["res_mean_96"] = np.nanmean(r96, axis=1)
            q96 = np.nanquantile(r96, [0.1, 0.5, 0.9], axis=1)
            q672 = np.nanquantile(r672, [0.1, 0.5, 0.9], axis=1)
            out["res_q10_96"], out["res_q50_96"], out["res_q90_96"] = q96[0], q96[1], q96[2]
            out["res_q10_672"], out["res_q50_672"], out["res_q90_672"] = q672[0], q672[1], q672[2]
            out["res_mad_672"] = np.nanmean(np.abs(r672), axis=1)
    return out


def _target_block(P: np.ndarray, K: np.ndarray, B: np.ndarray, H: int, weekend_grid: np.ndarray) -> dict:
    """Per-(decision, horizon) features. P is the raw grid (NaN gaps); positions > K are 'unknown' and masked."""
    n = len(K)
    h = np.arange(1, H + 1)
    J = B[:, None] + (h[None, :] - 1)                                  # (n, H) target grid positions
    out = {"horizon": np.broadcast_to(h[None, :], (n, H)).astype(np.int16).reshape(-1)}
    Kc = K[:, None, None]

    def gather(days: np.ndarray):
        pos = J[:, :, None] - STEPS_PER_DAY * days[None, None, :]         # (n, H, D)
        known = (pos <= Kc) & (pos >= 0)
        val = np.where(known, P[np.clip(pos, 0, len(P) - 1)], np.nan)
        return pos, known, val

    days14 = np.arange(1, 15)
    pos, known, val = gather(days14)
    for d in LAG_DAYS:
        out[f"rt_lag_{d}d"] = val[:, :, d - 1].reshape(-1)
    v7 = val[:, :, :7]
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        out["rt_prof7_mean"] = np.nanmean(v7, axis=2).reshape(-1)
        out["rt_prof7_med"] = np.nanmedian(v7, axis=2).reshape(-1)
        out["rt_prof7_min"] = np.nanmin(v7, axis=2).reshape(-1)
        out["rt_prof7_max"] = np.nanmax(v7, axis=2).reshape(-1)
        out["rt_prof7_std"] = np.nanstd(v7, axis=2).reshape(-1)
        # the arena's History.profile(weekend, 14): mean of the last 14 days at this time of day, restricted to days of
        # the same weekend/weekday type when at least two such days exist, otherwise all days
        wk_t = weekend_grid[J][:, :, None]                                 # (n, H, 1) target is weekend
        wk_l = weekend_grid[np.clip(pos, 0, len(weekend_grid) - 1)]        # (n, H, 14)
        same = known & np.isfinite(val) & (wk_l == wk_t)
        n_same = same.sum(2)
        m_same = np.nanmean(np.where(same, val, np.nan), axis=2)
        m_all = np.nanmean(val, axis=2)
        out["rt_prof14_dt"] = np.where(n_same >= 2, m_same, m_all).reshape(-1)
    return out


def _calendar_grid(hist: PriceHistory, upto_pos: int) -> pd.DataFrame:
    """Calendar features for every grid position 0..upto_pos (positions past the history's end are extrapolated)."""
    n = max(len(hist), int(upto_pos) + 1) + 2
    return calendar_features(pd.date_range(hist.start, periods=n, freq="15min"))


LAG_STEPS = int(np.ceil((STEP + PRICE_PUBLISH_LAG) / STEP))     # newest usable interval is this many steps before B (= 2)


def build_features(hist: PriceHistory, decisions_B: Sequence[pd.Timestamp], horizon: int = H_MAX,
                   *, allow_stale_steps: int = 10 ** 9, chunk: int = 800, as_hist: Optional["ASHistory"] = None):
    """Feature rows for decision blocks `decisions_B` (each the floor-15-minute start of a decision).

    Returns (features, J, dec_i): `features` is a DataFrame of float32/int columns plus `max_published_utc` (int64 ns), in
    decision-major, horizon-minor order; J is each row's target grid position and dec_i its decision number.
    Only price positions <= K = B - LAG_STEPS are read (the rest of `hist` is masked), so nothing published after the
    decision can reach a feature."""
    if horizon < 1 or horizon > H_MAX:
        raise ValueError(f"horizon must be 1..{H_MAX}")
    B = np.array([hist.pos(b) for b in decisions_B], dtype=np.int64)
    K = B - LAG_STEPS
    if (K < 0).any():
        raise ValueError("decision before the start of the price history")
    last_avail = len(hist) - 1
    Kc = np.minimum(K, last_avail)
    if ((K - Kc) > allow_stale_steps).any():
        raise StaleHistoryError(f"price history ends at {hist.ts(last_avail)}, "
                                f"newest interval expected at {hist.ts(int(K.max()))}")
    if (Kc < MIN_HISTORY_STEPS - 1).any():
        raise ValueError("not enough history behind the decision (need 7 days)")
    P = hist.values
    Pf = _ffill(P)
    Rg = hist.residual_grid()
    cal_full = _calendar_grid(hist, int(B.max()) + horizon)
    weekend_grid = cal_full["tgt_is_weekend"].to_numpy()
    start64 = np.datetime64(hist.start.tz_convert("UTC").tz_localize(None), "ns")
    lag_min = int(PRICE_PUBLISH_LAG / pd.Timedelta(minutes=1)) + 15
    parts, Js, decs = [], [], []
    n = len(B)
    for lo in range(0, n, chunk):
        hi = min(lo + chunk, n)
        k, b = Kc[lo:hi], B[lo:hi]
        dec = _decision_block(Pf, k, Rg)
        tgt = _target_block(P, k, b, horizon, weekend_grid)
        cols = {name: np.repeat(arr.astype(np.float32), horizon) for name, arr in dec.items()}
        cols.update({name: (arr.astype(np.float32) if arr.dtype.kind == "f" else arr) for name, arr in tgt.items()})
        J = (b[:, None] + np.arange(horizon)[None, :]).reshape(-1)
        cal = cal_full.iloc[J]
        for name in cal.columns:
            cols[name] = cal[name].to_numpy()
        pub = start64 + (k * 15 + lag_min).astype("timedelta64[m]")              # publish time of the newest price used
        max_pub = pub.astype("datetime64[ns]").astype("int64")
        if as_hist is not None:
            blk = pd.DatetimeIndex(hist.start + pd.to_timedelta(b * 15, unit="m"))
            tts = pd.DatetimeIndex(hist.start + pd.to_timedelta(J * 15, unit="m"))
            asf = _as_block(as_hist, blk, tts, horizon, AS_MODEL_PRODUCTS + ("ecrs",))
            cols.update(asf)
            # the day-ahead rows used are public at 13:30 the day before, and the decision is after that by construction
            pubs = as_hist.publish_utc(tts.tz_convert(TZ).tz_localize(None).normalize()).as_unit("ns").asi8.reshape(len(b), horizon)
            used = np.zeros((len(b), horizon), bool)
            for name, v in asf.items():
                if name.endswith("_dam"):
                    used |= np.isfinite(v.reshape(len(b), horizon))
            pub_used = np.where(used, pubs, np.iinfo("int64").min)
            max_pub = np.maximum(max_pub[:, None], pub_used).reshape(-1)
            cols["max_published_utc"] = max_pub
        else:
            cols["max_published_utc"] = np.repeat(max_pub, horizon)
        parts.append(pd.DataFrame(cols))
        Js.append(J)
        decs.append(np.repeat(np.arange(lo, hi), horizon))
    return pd.concat(parts, ignore_index=True), np.concatenate(Js), np.concatenate(decs)


def _finish(df: pd.DataFrame, hist: PriceHistory, J: np.ndarray, sp: str) -> pd.DataFrame:
    df = df.copy()
    df["max_published_utc"] = pd.to_datetime(df["max_published_utc"].to_numpy(), unit="ns", utc=True)
    df.index = pd.DatetimeIndex(hist.start + pd.to_timedelta(J * 15, unit="m"), name="interval_start_utc")
    df.attrs["settlement_point"] = sp
    df.attrs["feature_set"] = f"{FEATURE_SET}/{FEATURE_VERSION}"
    return df


# ------------------------------------------------------------------------------------------------ ancillary-service prices
AS_MODEL_PRODUCTS = ("regup", "regdn", "rrs", "nspin")    # ECRS only exists since 2023-06, so it is not an RT-model feature
AS_ALL_PRODUCTS = ("regup", "regdn", "rrs", "ecrs", "nspin")
AS_MED_DAYS = 14


@dataclass
class ASHistory:
    """Hourly day-ahead ancillary-service clearing prices (MCPC, $/MW-h), index = UTC hour start, columns = products.

    ASSUMPTION `DAM_AS_PUBLISH_LOCAL`: the day-ahead results for operating day D are public at 13:30 Central time on
    D-1. Source: ERCOT DAM MCPC history (report 13091) through the research agents' derived `as_prices.parquet`."""
    frame: pd.DataFrame
    sources: tuple = field(default_factory=tuple)
    _daily: dict = field(default_factory=dict, repr=False, compare=False)

    def publish_utc(self, local_dates: pd.DatetimeIndex) -> pd.DatetimeIndex:
        """Publish time of the day-ahead results of each operating date (naive local dates in)."""
        h, m = (int(x) for x in DAM_AS_PUBLISH_LOCAL.split(":"))
        t = (pd.DatetimeIndex(local_dates) - pd.Timedelta(days=1)) + pd.Timedelta(hours=h, minutes=m)
        return t.tz_localize(TZ, ambiguous=True, nonexistent="shift_forward").tz_convert("UTC")

    def med_matrix(self, product: str, days: int = AS_MED_DAYS):
        """(first_date, M) with M[d, h] the median of local hour h over the `days` operating dates ending at first_date + d."""
        key = (product, "med", days)
        if key in self._daily:
            return self._daily[key]
        d0, A = self.daily_hour_matrix(product)
        pad = np.vstack([np.full((days - 1, 24), np.nan), A])
        win = np.lib.stride_tricks.sliding_window_view(pad, days, axis=0)          # (n_days, 24, days)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)
            M = np.nanmedian(win, axis=2)
        self._daily[key] = (d0, M)
        return d0, M

    def daily_hour_matrix(self, product: str):
        """(first_date, A) with A[d, h] the price of local hour h on date first_date + d (NaN when absent)."""
        if product in self._daily:
            return self._daily[product]
        s = self.frame[product].dropna()
        loc = s.index.tz_convert(TZ)
        dates = loc.tz_localize(None).normalize()
        d0 = dates.min()
        di = ((dates - d0) / pd.Timedelta(days=1)).astype(int).to_numpy()
        A = np.full((di.max() + 1, 24), np.nan)
        first = ~pd.Series(list(zip(di, loc.hour))).duplicated().to_numpy()      # fall-back day: keep the first pass
        A[di[first], loc.hour.to_numpy()[first]] = s.to_numpy()[first]
        self._daily[product] = (d0, A)
        return d0, A


def load_as_history(path: Optional[Path] = None, *, freeze: bool = True) -> ASHistory:
    """DAM MCPC history from the research derived parquet (columns market, product, interval_start_utc, mcpc).

    The research agents rewrite that file as they collect more data, so by default it is copied once to
    DISPATCH_HOME/mlflow/inputs/ under a name that carries its sha256, and read from the copy: a training run then always has
    the exact bytes its data snapshot names."""
    path = Path(path) if path is not None else paths.GRID_DERIVED / "as_prices.parquet"
    src = _source_record(path)
    if freeze:
        import shutil
        dst = paths.MLFLOW / "inputs" / f"{path.stem}_{src['sha256'][:12]}{path.suffix}"
        if not dst.exists():
            dst.parent.mkdir(parents=True, exist_ok=True)
            tmp = dst.with_suffix(".tmp")
            shutil.copyfile(path, tmp)
            if file_sha256(tmp) != src["sha256"]:                        # the source changed while copying: try again next call
                tmp.unlink()
                raise RuntimeError(f"{path} changed while being copied; retry")
            tmp.replace(dst)
        path = dst
    df = pd.read_parquet(path, columns=["market", "product", "interval_start_utc", "mcpc"])
    df = df[df["market"] == "DAM"]
    wide = df.pivot_table(index="interval_start_utc", columns="product", values="mcpc", aggfunc="first").sort_index()
    wide.columns = [str(c).lower() for c in wide.columns]
    wide.index = pd.DatetimeIndex(wide.index).tz_convert("UTC")
    return ASHistory(wide, (src,))


def as_feature_columns(products: Sequence[str] = AS_MODEL_PRODUCTS, suffixes: Sequence[str] = AS_FEATURE_SUFFIXES) -> tuple:
    return tuple(f"as_{p}_{sfx}" for p in products for sfx in suffixes)


def _as_block(as_hist: ASHistory, decisions: pd.DatetimeIndex, target_ts: pd.DatetimeIndex, horizon: int,
              products: Sequence[str]) -> dict:
    """as_<p>_dam: day-ahead price of the target's hour when published by the decision, else NaN.
    as_<p>_med14: median of the same local hour over the last 14 operating days that were published by the decision."""
    n = len(decisions)
    loc = target_ts.tz_convert(TZ)
    tdate = loc.tz_localize(None).normalize()
    thour = loc.hour.to_numpy()
    pub = as_hist.publish_utc(tdate).as_unit("ns").asi8.reshape(n, horizon)
    dec = decisions.as_unit("ns").asi8[:, None]
    known = pub <= dec
    hour_start = target_ts.floor("1h").tz_convert("UTC")
    # latest operating date whose results are public at each decision
    dloc = decisions.tz_convert(TZ)
    after_pub = (dloc.hour.to_numpy() * 60 + dloc.minute.to_numpy()) >= (int(DAM_AS_PUBLISH_LOCAL[:2]) * 60 + int(DAM_AS_PUBLISH_LOCAL[3:]))
    latest = dloc.tz_localize(None).normalize() + pd.to_timedelta(after_pub.astype(int), unit="D")
    out = {}
    got = as_hist.frame.index.get_indexer(hour_start)                                   # -1 where the hour is absent
    for p in products:
        if p not in as_hist.frame.columns:
            continue
        col = as_hist.frame[p].to_numpy(dtype=np.float64)
        dam = np.where(got >= 0, col[np.maximum(got, 0)], np.nan).reshape(n, horizon)
        dam_k = np.where(known, dam, np.nan)
        out[f"as_{p}_dam"] = dam_k.astype(np.float32)
        d0, M = as_hist.med_matrix(p)
        li = np.clip(((latest - d0) / pd.Timedelta(days=1)).astype(int).to_numpy(), 0, M.shape[0] - 1)   # latest public date
        med = M[li][np.arange(n)[:, None], thour.reshape(n, horizon)]                       # (n, horizon)
        out[f"as_{p}_med14"] = med.astype(np.float32)
        # the day-ahead price relative to the recent median: stationary when the AS price level shifts (it collapsed in 2025)
        out[f"as_{p}_rel"] = (dam_k / np.maximum(med, AS_REL_FLOOR)).astype(np.float32)
    return {k: v.reshape(-1) for k, v in out.items()}


# ------------------------------------------------------------------------------------------------ executable specification
def reference_row(hist: PriceHistory, B: int, h: int) -> dict:
    """The RT feature definitions written out one value at a time: no vectorisation, no shared helpers.

    This is the SPECIFICATION that notes/d1a.md ("REQUEST (D1a -> D0): features for rt_v2") points to, and the oracle
    `tests/test_models.py` holds `build_features` to. `B` is the grid position of the decision block (floor_15min of the
    decision time), `h` the horizon (1..96), so the target interval is `j = B + h - 1`. Only grid positions <= K = B - 2 are
    visible: that is the point-in-time rule (a price is public 15 min after its interval ends). Slow on purpose."""
    P = hist.values
    K = B - LAG_STEPS
    j = B + h - 1

    def p(i):                                              # a price, if it is visible at the decision
        return float(P[i]) if 0 <= i <= K else float("nan")

    ff = pd.Series(P[: K + 1]).ffill().bfill().to_numpy()   # window statistics: a missing price repeats the previous one
    last = lambda w: ff[K - w + 1: K + 1]                    # the w newest visible prices

    out = {"horizon": h, "rt_last": float(ff[K])}
    for w in (4, 16, 96, 672):
        out[f"rt_mean_{w}"] = float(last(w).mean())
    out["rt_std_96"], out["rt_std_672"] = float(last(96).std()), float(last(672).std())          # population std (ddof 0)
    out["rt_min_96"], out["rt_max_96"] = float(last(96).min()), float(last(96).max())
    for q, name in ((0.1, "q10"), (0.5, "q50"), (0.9, "q90")):
        out[f"rt_{name}_672"] = float(np.quantile(last(672), q))                                  # linear interpolation
    srt = np.sort(last(96))
    out["rt_spread_96"] = float(srt[-8:].mean() - srt[:8].mean())
    for w in (96, 672):
        out[f"rt_frac_hi_{w}"] = float((last(w) >= SPIKE_HI_USD).mean())
        out[f"rt_frac_neg_{w}"] = float((last(w) < 0).mean())

    def anchor(i):                                         # median of the same time of day over the 7 previous days
        vals = [float(P[i - STEPS_PER_DAY * k]) for k in range(1, 8) if i - STEPS_PER_DAY * k >= 0]
        vals = [v for v in vals if not np.isnan(v)]
        return float(np.median(vals)) if vals else float("nan")

    res = np.array([float(P[i]) - anchor(i) if i >= 0 else float("nan") for i in range(K - 671, K + 1)])   # residual series
    r = lambda w: res[-w:]
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        out["res_last"] = float(res[-1])
        for w in (4, 16, 96):
            out[f"res_mean_{w}"] = float(np.nanmean(r(w)))
        for w in (96, 672):
            for q, name in ((0.1, "q10"), (0.5, "q50"), (0.9, "q90")):
                out[f"res_{name}_{w}"] = float(np.nanquantile(r(w), q))
        out["res_mad_672"] = float(np.nanmean(np.abs(r(672))))

    for d in LAG_DAYS:
        out[f"rt_lag_{d}d"] = p(j - STEPS_PER_DAY * d)
    prof = [p(j - STEPS_PER_DAY * k) for k in range(1, 8)]
    prof = [v for v in prof if not np.isnan(v)]
    nan = float("nan")
    out["rt_prof7_mean"] = float(np.mean(prof)) if prof else nan
    out["rt_prof7_med"] = float(np.median(prof)) if prof else nan
    out["rt_prof7_min"] = float(np.min(prof)) if prof else nan
    out["rt_prof7_max"] = float(np.max(prof)) if prof else nan
    out["rt_prof7_std"] = float(np.std(prof)) if prof else nan

    def weekend(i):                                        # Saturday or Sunday on the local (Central) calendar
        return bool(calendar_features(pd.DatetimeIndex([hist.start + int(i) * STEP]))["tgt_is_weekend"].iloc[0])

    known = [(k, p(j - STEPS_PER_DAY * k)) for k in range(1, 15)]
    known = [(k, v) for k, v in known if not np.isnan(v)]
    same = [v for k, v in known if weekend(j - STEPS_PER_DAY * k) == weekend(j)]
    pool = same if len(same) >= 2 else [v for _, v in known]                                    # the arena's History.profile rule
    out["rt_prof14_dt"] = float(np.mean(pool)) if pool else nan

    cal = calendar_features(pd.DatetimeIndex([hist.start + j * STEP])).iloc[0]
    out.update({k: float(cal[k]) for k in ("tgt_hod", "tgt_dow", "tgt_is_weekend", "tgt_is_holiday", "tgt_doy_sin",
                                           "tgt_doy_cos", "tgt_month")})
    out["max_published_utc"] = hist.start + (K + 1) * STEP + PRICE_PUBLISH_LAG
    return out


def reference_as_row(as_hist: "ASHistory", decision_utc, target_utc, products: Sequence[str] = AS_MODEL_PRODUCTS) -> dict:
    """The day-ahead AS feature definitions, one value at a time (the oracle for `_as_block`).

    as_<p>_dam   the day-ahead price (MCPC) of the local hour that contains the target, if the results of the target's local
                 operating date are public at the decision (13:30 Central on the day before, DAM_AS_PUBLISH_LOCAL), else NaN
    as_<p>_med14 the median, over the 14 most recent operating dates whose results are public at the decision, of the day-ahead
                 price at the target's local hour of day (missing hours skipped)
    as_<p>_rel   as_<p>_dam / max(as_<p>_med14, AS_REL_FLOOR)"""
    t0, ts = utc(decision_utc), utc(target_utc)
    loc = ts.tz_convert(TZ)
    pub = as_hist.publish_utc(pd.DatetimeIndex([loc.tz_localize(None).normalize()]))[0]
    d = t0.tz_convert(TZ)
    h, m = (int(x) for x in DAM_AS_PUBLISH_LOCAL.split(":"))
    latest = d.tz_localize(None).normalize() + pd.Timedelta(days=1 if (d.hour * 60 + d.minute) >= h * 60 + m else 0)
    out = {}
    for prod in products:
        s = as_hist.frame[prod]
        dam = float(s.get(ts.floor("1h"), float("nan"))) if pub <= t0 else float("nan")
        vals = []
        for back in range(14):
            day = latest - pd.Timedelta(days=back)
            when = (day + pd.Timedelta(hours=loc.hour)).tz_localize(TZ, ambiguous=True, nonexistent="NaT")
            if pd.isna(when):
                continue
            v = s.get(when.tz_convert("UTC"), float("nan"))
            if not np.isnan(v):
                vals.append(float(v))
        med = float(np.median(vals)) if vals else float("nan")
        out[f"as_{prod}_dam"], out[f"as_{prod}_med14"] = dam, med
        out[f"as_{prod}_rel"] = dam / max(med, AS_REL_FLOOR) if not (np.isnan(dam) or np.isnan(med)) else float("nan")
    return out


# ------------------------------------------------------------------------------------------------ public API (D0 shape)
_HIST_CACHE: dict = {}


def get_history(sp: str, history: Optional[PriceHistory] = None) -> PriceHistory:
    if history is not None:
        return history
    h = _HIST_CACHE.get((sp, str(paths.ZONAL_HISTORY)))
    if h is None:
        h = load_history(sp)
        _HIST_CACHE[(sp, str(paths.ZONAL_HISTORY))] = h
    return h


_AS_DEFAULT: dict = {}


def default_as_history() -> Optional[ASHistory]:
    """The day-ahead AS history the registered models were trained with (cached); None if the file is not there."""
    key = str(paths.GRID_DERIVED)
    if key not in _AS_DEFAULT:
        try:
            _AS_DEFAULT[key] = load_as_history()
        except (FileNotFoundError, OSError):
            _AS_DEFAULT[key] = None
    return _AS_DEFAULT[key]


def point_in_time(sp: str, asof_utc, horizon: int = H_MAX, *, history: Optional[PriceHistory] = None,
                  allow_stale_steps: int = 0, as_hist="auto") -> pd.DataFrame:
    """Features for one decision at `asof_utc`: one row per target interval (index `interval_start_utc`).

    Uses only prices published at or before `asof_utc`. Raises StaleHistoryError when the history does not reach the
    newest interval a decision at `asof_utc` would have (the caller then must not dispatch).
    `as_hist`: 'auto' adds the day-ahead AS columns from the default AS history when the real price history is used (so a
    served model finds the columns it was trained on) and none when an explicit `history` is passed; pass an ASHistory to add
    them, or False for none."""
    if isinstance(as_hist, str) and as_hist == "auto":
        as_hist = default_as_history() if history is None else None
    elif as_hist is False:
        as_hist = None
    hist = get_history(sp, history)
    asof = utc(asof_utc)
    B = floor15(asof)
    df, J, _ = build_features(hist, [B], horizon, allow_stale_steps=allow_stale_steps, as_hist=as_hist)
    out = _finish(df, hist, J, sp)
    out.attrs["asof_utc"] = asof
    return out


def decision_blocks(start_utc, end_utc, every="4h", offset="0h") -> pd.DatetimeIndex:
    """Decision times on a UTC grid: multiples of `every` (plus `offset`) in [start_utc, end_utc)."""
    step = parse_every(every)
    off = pd.Timedelta(offset)
    if off % STEP:
        raise ValueError("offset must be a multiple of 15 minutes")
    t0, t1 = utc(start_utc), utc(end_utc)
    first = t0.floor(step) + off
    while first < t0:
        first += step
    while first - step >= t0:
        first -= step
    return pd.date_range(first, t1, freq=step, inclusive="left")


def training_frame(sp: str, start_utc, end_utc, decision_every="4h", *, horizon: int = H_MAX, offset="0h",
                   history: Optional[PriceHistory] = None, as_hist: Optional[ASHistory] = None):
    """Training rows for decisions in [start_utc, end_utc): (X, y, decision_utc), all aligned row for row.

    X is indexed by `interval_start_utc` of the target (not unique: several decisions share a target). y is the realised
    15-minute price of that target; rows whose target has no price are dropped. `decision_utc` is the issue time (block
    start) of each row. Labels may lie up to 24 h after `end_utc`; purged cross-validation (cv.py) handles that."""
    hist = get_history(sp, history)
    blocks = decision_blocks(start_utc, end_utc, decision_every, offset)
    blocks = blocks[(blocks >= hist.ts(MIN_HISTORY_STEPS + LAG_STEPS)) & (blocks < hist.end)]
    if len(blocks) == 0:
        raise ValueError(f"no decisions between {start_utc} and {end_utc} inside the history {hist.start} .. {hist.end}")
    df, J, dec_i = build_features(hist, blocks, horizon, as_hist=as_hist)
    safeJ = np.minimum(J, len(hist) - 1)
    y_all = hist.values[safeJ]
    ok = (J < len(hist)) & np.isfinite(y_all)
    X = _finish(df, hist, J, sp)[ok]
    X.attrs["settlement_point"] = sp
    X.attrs["feature_set"] = f"{FEATURE_SET}/{FEATURE_VERSION}"
    ys = pd.Series(y_all[ok], index=X.index, name=config_rt_col())
    dec = pd.Series(blocks[dec_i[ok]], index=X.index, name="decision_utc")
    return X, ys, dec
