"""Forecasts for replay and shadow runs (D1b), and for the service (D2): one place that turns a Forecaster into the
`forecast_fn(issued_utc) -> ForecastFrame` callable the attribution engine's shadow optimizer takes.

    from dispatch.registry import load
    from dispatch.models.replay import make_forecast_fn
    fc = load("research")
    forecast_fn = make_forecast_fn(fc, "HB_NORTH")                  # point-in-time features are built per call
    ff = forecast_fn(pd.Timestamp("2026-08-01 14:00", tz="UTC"))    # ForecastFrame, 96 intervals ahead

HONESTY GUARD. A registered model is trained on data before its `holdout_start` (2025-01-01 for the first release). Asking
it for forecasts issued before then returns in-sample forecasts, which flatter any attribution or backtest built on them.
`make_forecast_fn` refuses unless `allow_in_sample=True`, so the mistake needs an explicit opt-in.
"""
from __future__ import annotations

from typing import Callable, Optional

import pandas as pd

from ..contracts import ForecastFrame
from .quantile import ForecastError, to_utc


class InSampleError(ForecastError):
    """The issue time is inside the period the model was trained on."""


def default_features_fn(feature_set: str) -> Callable:
    """Point-in-time features for a model's feature set: D0's builder for `rt_v1`, the interim builder otherwise."""
    if str(feature_set).startswith("rt_v1"):
        from ..features import build as d0
        return lambda sp, asof, horizon=96: d0.point_in_time(sp, asof, horizon)
    from . import interim_features as F
    return lambda sp, asof, horizon=96: F.point_in_time(sp, asof, horizon)


def trained_through(forecaster) -> Optional[pd.Timestamp]:
    """First instant the model has NOT seen (its holdout start), when the bundle records it."""
    hs = (getattr(forecaster, "meta", None) or {}).get("holdout_start")
    return to_utc(pd.Timestamp(hs, tz="UTC") if pd.Timestamp(hs).tzinfo is None else hs) if hs else None


def make_forecast_fn(forecaster, settlement_point: str, *, features_fn: Optional[Callable] = None, horizon: int = 96,
                     allow_in_sample: bool = False) -> Callable[[pd.Timestamp], ForecastFrame]:
    """`forecast_fn(issued_utc) -> ForecastFrame`: build the features known at `issued_utc`, then predict.

    features_fn(sp, asof_utc, horizon) -> DataFrame; default per `forecaster.feature_set`. Fails closed: a missing feature,
    a stale price history or a leaking row raises (ForecastError / StaleHistoryError / FeatureDataError)."""
    ff = features_fn or default_features_fn(getattr(forecaster, "feature_set", ""))
    cutoff = trained_through(forecaster)

    def forecast_fn(issued_utc) -> ForecastFrame:
        issued = to_utc(issued_utc, "issued_utc")
        if cutoff is not None and issued < cutoff and not allow_in_sample:
            raise InSampleError(f"model {forecaster.version} was trained on data before {cutoff}; forecasts issued at {issued} "
                                f"would be in-sample (pass allow_in_sample=True to replay anyway)")
        feats = ff(settlement_point, issued, horizon)
        feats.attrs["settlement_point"] = settlement_point
        return forecaster.predict(feats, issued, settlement_point=settlement_point)

    return forecast_fn


def table_forecast_fn(table: pd.DataFrame, settlement_point: str, model_version: str = "table") -> Callable:
    """`forecast_fn` backed by a precomputed forecast table (the `holdout_forecasts` artifact of a training run): looks the
    issue time up exactly. For replays on the training run's own decision grid; any other issue time raises KeyError."""
    t = table.copy()
    t["decision_utc"] = pd.DatetimeIndex(t["decision_utc"])
    groups = {d: g for d, g in t.groupby("decision_utc")}

    def forecast_fn(issued_utc) -> ForecastFrame:
        issued = to_utc(issued_utc, "issued_utc")
        g = groups.get(issued)
        if g is None:
            raise KeyError(f"no precomputed forecast issued at {issued}")
        frame = g[["rt_p10", "rt_p50", "rt_p90"]].sort_index()
        mp = to_utc(g["max_published_utc"].max()) if "max_published_utc" in g.columns else issued
        return ForecastFrame(issued_utc=issued, settlement_point=settlement_point, model_version=model_version, frame=frame,
                             max_feature_published_utc=mp)

    return forecast_fn
