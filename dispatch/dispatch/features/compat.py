"""Serve a model trained on D1a's interim feature names from rt_v1 frames. ROLLBACK ONLY since 11 Oct 2026.

Registered version 1 (bundle feature_set "interim_d1a/v1") was trained on D1a's interim builder, which names its inputs `rt_mean_4`,
`tgt_hod`, `as_regup_dam`, ... rt_v1 1.1.0 carries exactly the same numbers under D0's names: `rt_mean_1h`, `hour_local` + `qtr_hour`,
`dam_as_regup`, ... (tests/test_rt_v1_1_equivalence.py holds D0's builder to the numbers the interim builder gave, frozen as a fixture). The
online store and the offline store keep D0's names. A model trained on the interim names finds what it needs after a rename at the edge:

    features, asof, max_pub = online.read_latest(sp)
    forecast = forecaster.predict(compat.for_forecaster(features, forecaster), asof)

`for_forecaster` does NOTHING (returns the very same frame, no copy) for a model trained on D0's names (feature_set "rt_v1/..."): version 2
on, retrained on D0's builder (notes/p1-retrain.md), needs no rename, and the service passes every frame through this line anyway so that
moving an alias back to version 1 (`registry promote 1 paper`, or `research`) serves it correctly without a code change. Delete this module
when version 1 is no longer a rollback target. `INTERIM_TO_D0` is the map between the two spellings; `train.MODEL_FEATURES` is the retrained
model's list under D0's names, and a test holds both to the 59 inputs of version 1.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

# interim (D1a) column -> rt_v1 column holding the same numbers
INTERIM_TO_D0 = {
    "rt_mean_4": "rt_mean_1h", "rt_mean_96": "rt_mean_24h", "rt_mean_672": "rt_mean_7d",
    "rt_std_96": "rt_std_24h", "rt_std_672": "rt_std_7d",
    "tgt_dow": "dow", "tgt_is_weekend": "is_weekend", "tgt_is_holiday": "is_holiday", "tgt_month": "month",
    "tgt_doy_sin": "doy_sin", "tgt_doy_cos": "doy_cos",
}
for _p in ("regup", "regdn", "rrs", "ecrs", "nspin"):
    INTERIM_TO_D0.update({f"as_{_p}_dam": f"dam_as_{_p}", f"as_{_p}_med14": f"dam_as_{_p}_med14", f"as_{_p}_rel": f"dam_as_{_p}_rel"})
# interim `tgt_hod` is one number (hour + quarter/4); rt_v1 keeps the hour and the quarter hour as two integers
HOD = "tgt_hod"


def to_model_names(df: pd.DataFrame, names=None) -> pd.DataFrame:
    """A copy of an rt_v1 frame with the interim-named columns added. `names` limits them to what a model needs (its
    `spec.needed_columns()`); the D0-named columns stay, so the result also works for a model trained on D0's names.
    A source column that is missing is skipped, not invented: `predict` then names what is absent (fail closed)."""
    want = None if names is None else set(names)
    cols = {}
    for interim, d0 in INTERIM_TO_D0.items():
        if (want is None or interim in want) and d0 in df.columns and interim not in df.columns:
            cols[interim] = df[d0]
    if (want is None or HOD in want) and HOD not in df.columns and {"hour_local", "qtr_hour"} <= set(df.columns):
        cols[HOD] = (df["hour_local"].astype("float32") + df["qtr_hour"].astype("float32") / np.float32(4.0)).astype("float32")
    out = df.assign(**cols) if cols else df.copy()
    out.attrs.update(df.attrs)
    return out


def for_forecaster(df: pd.DataFrame, forecaster) -> pd.DataFrame:
    """`df` ready for `forecaster.predict`: renamed to the interim names when the model was trained on them, untouched otherwise."""
    if not str(getattr(forecaster, "feature_set", "") or "").startswith("interim"):
        return df
    needed = set()
    for m in getattr(forecaster, "models", {}).values():
        needed.update(m.spec.needed_columns())
    return to_model_names(df, needed or None)
