"""MLflow tracking and model registry (D1a, PRD FR6). See DESIGN.md and mlflow_registry.py.

    from dispatch.registry import load, promote          # DESIGN.md: registry.load(alias), registry.promote(version, alias)
    fc = load("paper")                                    # a Forecaster; raises NoModelError when the alias is unset

The names are resolved lazily so `import dispatch.registry` stays cheap (importing mlflow takes about 10 s here).
"""
_EXPORTS = (
    "load", "promote", "register", "log_run", "record_gate3", "check_promotion", "aliases", "configure", "default",
    "Registry", "Run", "RegistryError", "NoModelError", "IntegrityError", "PromotionRefused", "PromotionResult",
    "PromotionCheck", "STAGES", "EXPERIMENT", "code_version", "catalog_snapshot", "file_snapshot",
    "holdout_metrics_payload", "merge_payloads", "holdout_forecasts", "holdout_metrics", "status",
)


def __getattr__(name):
    if name in _EXPORTS:
        from . import mlflow_registry
        return getattr(mlflow_registry, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__():
    return sorted(list(globals()) + list(_EXPORTS))
