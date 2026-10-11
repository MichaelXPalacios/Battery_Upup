"""Price forecasting models (D1a): LightGBM quantile regressors, baselines, purged walk-forward validation.

    quantile.py         QuantileForecaster (implements contracts.Forecaster), ASPriceModel
    baselines.py        seasonal-naive and arena-profile forecasters (also Forecasters)
    cv.py               purged walk-forward folds, metrics, decision_value (capture rate)
    train.py            Optuna tuning + MLflow logging + registration
    interim_features.py point-in-time features over the NP6-785-ER history (fallback until D0's builder is used)
    bundle.py           save / load of a model bundle directory

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
