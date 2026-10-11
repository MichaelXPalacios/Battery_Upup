"""MLflow 'model from code' entry point: MLflow copies this file into every registered version and executes it on load.

`mlflow.pyfunc.load_model(...).unwrap_python_model().forecaster` is the Forecaster (a QuantileForecaster for LightGBM
versions); `registry.load` returns that object. `predict` exists so the version also works as a generic pyfunc: pass the
feature frame as the input and `params={"issued_utc": "<iso>", "settlement_point": "HB_NORTH"}`.
"""
import pandas as pd
import mlflow
from mlflow.pyfunc import PythonModel

from dispatch.models.bundle import load_forecaster


class ForecasterPyfunc(PythonModel):
    def load_context(self, context):
        self.forecaster = load_forecaster(context.artifacts["bundle"])

    def predict(self, context, model_input, params=None):
        params = dict(params or {})
        issued = params.get("issued_utc")
        if issued is None:
            raise ValueError("params['issued_utc'] is required")
        kw = {"settlement_point": params["settlement_point"]} if params.get("settlement_point") else {}
        fc = self.forecaster.predict(pd.DataFrame(model_input), pd.Timestamp(issued), **kw)
        return fc.frame


mlflow.models.set_model(ForecasterPyfunc())
