"""Markdown tables for notes/d1a.md, read from the training checkpoints (one result.json per settlement point).

    python -m dispatch.models.report --tag r1 [--sps HB_NORTH,HB_WEST,HB_HOUSTON,HB_SOUTH]

Every number in the notes comes from here or from the MLflow runs the same checkpoints were logged to.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Optional, Sequence

from .. import paths

NAMES = ("model", "profile_median_7d", "arena_profile", "seasonal_naive_1d", "seasonal_naive_7d")
LABEL = {"model": "LightGBM quantile", "profile_median_7d": "7-day profile median (extra)", "arena_profile": "arena profile",
         "seasonal_naive_1d": "seasonal-naive 1 d", "seasonal_naive_7d": "seasonal-naive 7 d"}


def load_results(tag: str = "latest", sps: Optional[Sequence[str]] = None, work: Optional[Path] = None) -> dict:
    base = (Path(work) if work else paths.MLFLOW / "work") / tag
    sps = list(sps) if sps else sorted(p.name for p in base.iterdir() if (p / "result.json").exists())
    return {sp: json.loads((base / sp / "result.json").read_text(encoding="utf-8")) for sp in sps}


def _f(x, nd=3, default="n/a"):
    return default if x is None else f"{x:.{nd}f}"


def holdout_table(res: dict) -> str:
    rows = ["| point | forecaster | pinball (mean of P10/P50/P90) | P10-P90 coverage | P50 MAE | P50 bias | spike MAE (>= $150) | capture | $/kW-yr |",
            "|---|---|---:|---:|---:|---:|---:|---:|---:|"]
    for sp, r in res.items():
        for n in NAMES:
            m, v = r["pooled"][n], r["value"].get(n, {})
            rows.append(f"| {sp} | {LABEL[n]} | {m['pinball_mean']:.3f} | {m['coverage_p10_p90']:.3f} | {m['mae_p50']:.2f} | "
                        f"{m['bias_p50']:+.2f} | {_f(m.get('spike_mae_p50'), 0)} | {_f(v.get('capture'))} | {_f(v.get('usd_per_kw_yr'), 1)} |")
    return "\n".join(rows)


def gain_table(res: dict) -> str:
    rows = ["| point | " + " | ".join(LABEL[n] for n in NAMES[1:]) + " |", "|---|" + "---:|" * (len(NAMES) - 1)]
    for sp, r in res.items():
        g = r["gains"]
        rows.append(f"| {sp} | " + " | ".join(f"{g['pinball'][n]:+.1%} / {g['mae'][n]:+.1%}" for n in NAMES[1:]) + " |")
    return "\n".join(rows)


def calibration_table(res: dict) -> str:
    rows = ["| point | forecaster | hit P10 | hit P50 | hit P90 | width P10-P90 |", "|---|---|---:|---:|---:|---:|"]
    for sp, r in res.items():
        for n in ("model", "profile_median_7d", "seasonal_naive_1d"):
            m = r["pooled"][n]
            rows.append(f"| {sp} | {LABEL[n]} | {m['hit_p10']:.3f} | {m['hit_p50']:.3f} | {m['hit_p90']:.3f} | {m['width_p10_p90']:.1f} |")
    return "\n".join(rows)


def cv_table(res: dict) -> str:
    rows = ["| point | forecaster | pinball | coverage | P50 MAE |", "|---|---|---:|---:|---:|"]
    for sp, r in res.items():
        for n in NAMES:
            m = r["cv_pooled"][n]
            rows.append(f"| {sp} | {LABEL[n]} | {m['pinball_mean']:.3f} | {m['coverage_p10_p90']:.3f} | {m['mae_p50']:.2f} |")
    return "\n".join(rows)


def stress_table(res: dict, window: str = "winter_storm_fern") -> str:
    rows = ["| point | forecaster | pinball | coverage | P50 MAE | spike MAE | forecast rows |", "|---|---|---:|---:|---:|---:|---:|"]
    for sp, r in res.items():
        st = r["stress"].get(window)
        if not st:
            continue
        for n in ("model", "profile_median_7d", "arena_profile", "seasonal_naive_1d"):
            m = st[n]
            rows.append(f"| {sp} | {LABEL[n]} | {m['pinball_mean']:.2f} | {m['coverage_p10_p90']:.3f} | {m['mae_p50']:.1f} | "
                        f"{_f(m.get('spike_mae_p50'), 0)} | {m['n']} |")
    return "\n".join(rows)


def optuna_table(res: dict) -> str:
    rows = ["| point | trials (complete/pruned) | best CV pinball | scale | weight_pow | trees | leaves | lr | min leaf (tuned -> fit) | calibration p10 / p50 / p90 | tune s | total s |",
            "|---|---|---:|---|---:|---:|---:|---:|---|---|---:|---:|"]
    for sp, r in res.items():
        o, c = r["optuna"], r["chosen"]
        p = c.get("params", {})
        cal = r["gains"].get("calibration") or {}
        cals = " / ".join(f"{cal.get(k, 0):+.3f}" for k in ("p10", "p50", "p90")) if cal else "none"
        ratio = (r.get("fit_rows") or 0)
        rows.append(f"| {sp} | {o.get('n_trials', 0)} ({o.get('n_complete', 0)}/{o.get('n_pruned', 0)}) | {_f(o.get('best_value'))} | "
                    f"{c.get('scale')} | {c.get('weight_pow')} | {c.get('n_estimators')} | {p.get('num_leaves')} | "
                    f"{p.get('learning_rate', 0):.3f} | {p.get('min_data_in_leaf')} | {cals} | {r['timings'].get('tune_s')} | {r['timings'].get('total_s')} |")
    return "\n".join(rows)


def importance_table(tag: str = "latest", sps: Optional[Sequence[str]] = None, work: Optional[Path] = None, top: int = 25) -> str:
    """Share of the total split gain of each feature in the fitted models (mean over the three quantiles and the points)."""
    import pandas as pd
    from .quantile import SPModel, is_calendar_feature
    from .train import d0_feature_names
    base = (Path(work) if work else paths.MLFLOW / "work") / tag
    sps = list(sps) if sps else sorted(p.name for p in base.iterdir() if (p / "model").exists())
    shares = {}
    for sp in sps:
        imp = SPModel.load(base / sp / "model").importance()
        shares[sp] = (imp / imp.sum()).mean(axis=1)
    df = pd.DataFrame(shares)
    df["mean"] = df.mean(axis=1)
    df = df.sort_values("mean", ascending=False)
    d0 = dict(zip(df.index, [n for n in (d0_feature_names([i])[0] for i in df.index)]))
    rows = ["| rank | model column | D0 name | " + " | ".join(sps) + " | mean share |", "|---:|---|---|" + "---:|" * (len(sps) + 1)]
    for r, (name, row) in enumerate(df.head(top).iterrows(), 1):
        rows.append(f"| {r} | `{name}` | `{d0[name]}` | " + " | ".join(f"{row[sp]:.1%}" for sp in sps) + f" | {row['mean']:.1%} |")
    groups = {"price statistics (rt_*)": [i for i in df.index if i.startswith("rt_") and "prof" not in i and "lag" not in i],
              "7/14-day profile and lags (rt_prof*, rt_lag*)": [i for i in df.index if i.startswith(("rt_prof", "rt_lag"))],
              "residual statistics (res_*)": [i for i in df.index if i.startswith("res_")],
              "day-ahead AS prices (as_*)": [i for i in df.index if i.startswith("as_")],
              "horizon": ["horizon"],
              "calendar (tgt_*)": [i for i in df.index if i.startswith("tgt_")]}
    rows += ["", "| group | mean share of gain |", "|---|---:|"]
    for g, cols in groups.items():
        rows.append(f"| {g} | {df.loc[[c for c in cols if c in df.index], 'mean'].sum():.1%} |")
    unused = [i for i in df.index if df.loc[i, "mean"] < 0.001]
    rows += ["", f"Columns below 0.1% of the gain: {', '.join(f'`{u}`' for u in unused) if unused else 'none'}."]
    return "\n".join(rows)


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--tag", default="latest")
    ap.add_argument("--sps", default=None)
    ap.add_argument("--work", default=None)
    a = ap.parse_args(argv)
    res = load_results(a.tag, a.sps.split(",") if a.sps else None, a.work)
    for title, fn in (("Holdout 2025-01-01 to latest (frozen model)", holdout_table), ("Gain over each baseline (pinball / P50 MAE)", gain_table),
                      ("Calibration on the holdout", calibration_table), ("Purged walk-forward CV 2022-2024", cv_table),
                      ("Winter Storm Fern window", stress_table), ("Optuna, calibration and timing", optuna_table),
                      ("Feature importance (share of split gain)", lambda r: importance_table(a.tag, list(r), a.work))):
        print(f"\n### {title}\n\n{fn(res)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
