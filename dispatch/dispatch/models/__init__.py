"""Price forecasting models (D1a): LightGBM quantile regressors, baselines, purged walk-forward validation.

    quantile.py         QuantileForecaster (implements contracts.Forecaster), ASPriceModel
    baselines.py        seasonal-naive and arena-profile forecasters (also Forecasters)
    cv.py               purged walk-forward folds, metrics, decision_value (capture rate)
    train.py            Optuna tuning + MLflow logging + registration, on D0's point-in-time builder (features/build.py, rt_v1)
    bundle.py           save / load of a model bundle directory
    replay.py           forecast_fn for the shadow run, the backtest and /attribution
    synthetic.py        synthetic prices, a synthetic D0 lake, a tiny real model and a constant forecaster, for tests

D1a's own feature builder (interim_features.py, which trained version 1) was retired on 11 Oct 2026; notes/p1-retrain.md.

Models only forecast. Nothing here produces a setpoint (DESIGN.md).
"""
_EXPORTS = {
    "QuantileForecaster": "quantile", "ASPriceModel": "quantile", "ForecastError": "quantile",
    "MissingFeaturesError": "quantile", "LeakageError": "quantile",
    "SeasonalNaive": "baselines", "ArenaProfile": "baselines", "make_baseline": "baselines",
    "walk_forward_folds": "cv", "decision_value": "cv", "quantile_metrics": "cv",
    "reference_metrics": "cv", "calibration_check": "cv", "false_breach_rate": "cv",
    "load_forecaster": "bundle",
}


def __getattr__(name):
    mod = _EXPORTS.get(name)
    if mod is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    import importlib
    return getattr(importlib.import_module(f".{mod}", __name__), name)
