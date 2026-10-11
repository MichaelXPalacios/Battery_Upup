"""The public case study (PRD Phase 1, Gate 2): one ERCOT battery, 60-day disclosure data, interval-level attribution.

`run()` selects the pilot ESR (rules written down in `disclosure.PilotCriteria`), builds its 15-minute dispatch from
ERCOT's 60-day SCED and DAM disclosure reports, prices it with a hub proxy for the nodal price, runs the attribution
engine and the shadow optimizer, and writes to ``reports/case_study/``:

    case_study.md                     the report (every number labelled REAL, DERIVED or ASSUMPTION)
    summary.json                      machine-readable totals, closure, sensitivities, provenance
    attribution_intervals.csv         one row per 15-minute interval: ATTRIBUTION_COLUMNS + the stage dispatch
    attribution_daily.csv             the same by market day
    top20_missed_intervals.csv        the 20 largest gaps, each with its measured explanation
    scope_variants.csv                headline vs no-regulation vs energy-only perfect foresight
    wear_sensitivity.csv              the $/MWh wear assumption varied
    proxy_sensitivity.csv             hub-for-node price proxy: first-order effect of the measured basis
    pilot_selection.csv               every ESR in the disclosure with the pass/fail of each selection rule
    provenance_files.csv              the staged ERCOT files used (url, file, sha256, bytes, utc time)
    fig_*.png                         charts (when matplotlib is importable)

Run from hugging-base/dispatch:  python -m dispatch.attribution.report
"""
from __future__ import annotations

import dataclasses
import hashlib
import json
import time
from pathlib import Path

import numpy as np
import pandas as pd

from .. import paths
from ..config import ATTRIBUTION_CLOSURE_MAX, DT_RT_H
from ..contracts import ATTRIBUTION_COLUMNS, as_award_col
from ..optimizer import rolling
from . import disclosure as D
from . import engine

TZ = "America/Chicago"
COMPONENT_LABEL = {"forecast_usd": "Forecast and decision", "soc_usd": "State-of-charge position",
                   "as_usd": "Ancillary awards", "availability_usd": "Availability (derates)",
                   "unexplained_usd": "Unexplained"}
SCOPES = {  # name -> (AS products in the perfect-foresight benchmark and in the actual's valuation, description)
    "all_AS": (("REGUP", "REGDN", "RRS", "ECRS", "NSPIN"), "energy + all five ancillary products (headline)"),
    "no_regulation": (("RRS", "ECRS", "NSPIN"), "energy + RRS, ECRS, NSPIN (regulation excluded)"),
    "energy_only": ((), "energy only"),
}


# ------------------------------------------------------------------------------------------------ helpers
def out_dir() -> Path:
    d = paths.REPORTS / "case_study"
    d.mkdir(parents=True, exist_ok=True)
    return d


def jsonable(o):
    if isinstance(o, dict):
        return {str(k): jsonable(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [jsonable(v) for v in o]
    if isinstance(o, (np.floating,)):
        return None if not np.isfinite(o) else float(o)
    if isinstance(o, float):
        return None if not np.isfinite(o) else o
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, (np.bool_,)):
        return bool(o)
    if isinstance(o, (pd.Timestamp,)):
        return o.isoformat()
    if isinstance(o, (pd.Series, pd.DataFrame)):
        return None
    return o


def usd(x, d=0):
    if x is None or (isinstance(x, float) and not np.isfinite(x)):
        return "n/a"
    x = 0.0 if abs(x) < 0.5 * 10 ** (-d) else x
    return f"-${abs(x):,.{d}f}" if x < 0 else f"${x:,.{d}f}"


def pct(x, d=1):
    return "n/a" if x is None or not np.isfinite(x) else f"{100 * x:.{d}f}%"


def md_table(headers, rows) -> str:
    out = ["| " + " | ".join(headers) + " |", "|" + "|".join(["---"] * len(headers)) + "|"]
    for r in rows:
        out.append("| " + " | ".join(str(c) for c in r) + " |")
    return "\n".join(out)


def position_for(pil: D.PilotData, products) -> pd.Series:
    """Day-ahead position value in scope: the energy part plus the parts of the ancillary products in `products`."""
    parts = pil.position_parts
    cols = ["energy"] + [p.lower() for p in products]
    return parts[cols].sum(axis=1).rename("position_usd")


def history_prices(pil: D.PilotData, days: int = 15) -> pd.DataFrame:
    idx = pil.prices.index
    return D.build_prices(pil.meta["hub"], idx[0] - pd.Timedelta(days=days), idx[-1] + pd.Timedelta(minutes=15))


# ------------------------------------------------------------------------------------------------ sensitivities
def run_scope_variants(pil: D.PilotData, forecaster, shadow_step: int, log=print) -> dict:
    """Headline (all five products) plus two narrower perfect-foresight benchmarks. The actual's awards and the day-ahead
    ancillary positions outside the scope are left out on both sides, so each variant compares like with like."""
    results = {}
    for name, (prods, _) in SCOPES.items():
        spec = dataclasses.replace(pil.spec, as_products=prods)
        t0 = time.time()
        log(f"scope {name}: attribution + shadow")
        results[name] = engine.run_attribution(
            spec, pil.actual, pil.prices, forecaster, position_usd=position_for(pil, prods),
            charge_limit_mw=pil.charge_limit_mw,
            shadow_step_intervals=shadow_step, shapley=(name == "all_AS"),
            progress=(lambda m, n=name: log(f"   [{n}] {m}")))
        log(f"scope {name} done in {time.time() - t0:.0f}s")
    return results


def run_wear_sensitivity(pil: D.PilotData, grid=(0.0, 10.0, 30.0), log=print) -> pd.DataFrame:
    rows = []
    for w in grid:
        spec = dataclasses.replace(pil.spec, wear_usd_per_mwh=w)
        r = engine.run_attribution(spec, pil.actual, pil.prices, None, position_usd=pil.position_usd,
                                   charge_limit_mw=pil.charge_limit_mw)
        s = r.summary
        c = s["components_usd"]
        rows.append(dict(wear_usd_per_mwh=w, pf_usd=s["pf_usd"], actual_usd=s["actual_usd"], gap_usd=s["gap_usd"],
                         capture_actual=s["capture_actual"], forecast_usd=c["forecast_usd"], soc_usd=c["soc_usd"],
                         as_usd=c["as_usd"], availability_usd=c["availability_usd"], closure=s["closure"]))
        log(f"wear ${w:g}/MWh: capture {pct(s['capture_actual'])}")
    return pd.DataFrame(rows)


def basis_profiles(node: str, hub: str) -> dict:
    """Node minus hub, 15-minute, by full Central day, from the 7-day nodal window (REAL)."""
    w = D.read_spp_window()
    b = (w[node] - w[hub]).dropna()
    local = b.index.tz_convert(TZ)
    out = {}
    for day, g in b.groupby(local.date):
        if len(g) == 96:
            out[str(day)] = g.to_numpy()
    return out


def proxy_sensitivity(pil: D.PilotData, res: engine.AttributionResult) -> pd.DataFrame:
    """First-order effect of replacing the hub proxy with the node: revalue the dispatches at hub + basis, where the
    basis is each full day of the 7-day nodal window applied by time of day to every day of the period (DERIVED)."""
    idx = pil.prices.index
    loc = idx.tz_convert(TZ)
    slot = (loc.hour * 4 + loc.minute // 15).to_numpy()
    st = res.stages
    dam_mw = pil.dam.set_index("hour_utc")["dam_energy_mw"]
    dam_mw = dam_mw[~dam_mw.index.duplicated(keep="last")].reindex(idx.floor("h")).fillna(0.0).to_numpy()
    nets = {"actual": st["net_actual"].to_numpy(), "pf": st["net_pf"].to_numpy(), "shadow": st["net_shadow"].to_numpy()}
    rows = []
    for day, prof in basis_profiles(pil.spec.settlement_point, pil.meta["hub"]).items():
        b = prof[slot]
        d_act_rt = float((nets["actual"] * DT_RT_H * b).sum())
        d_pos = float(-(dam_mw * DT_RT_H * b).sum())          # the day-ahead award settles against the (node) RT price
        d_pf = float((nets["pf"] * DT_RT_H * b).sum())
        d_sh = float(np.nansum(nets["shadow"] * DT_RT_H * b))
        rows.append(dict(basis_day=day, mean_basis=float(b.mean()), d_actual_usd=d_act_rt + d_pos, d_actual_rt_usd=d_act_rt,
                         d_position_usd=d_pos, d_pf_usd=d_pf, d_shadow_usd=d_sh, d_gap_usd=d_pf - (d_act_rt + d_pos)))
    return pd.DataFrame(rows)


def dam_basis(pil: D.PilotData) -> pd.Series:
    """Hourly node minus hub DAM settlement point price over the case-study days (REAL: both prices are ERCOT's own, from
    the same 60-day DAM disclosure). Index: hour_utc."""
    hubs = D.load_dam_hubs()
    if hubs.empty:
        D.cache_dam_hubs(log=lambda m: None)
        hubs = D.load_dam_hubs()
    hub = hubs[hubs["settlement_point"] == pil.meta["hub"]].set_index("hour_utc")["price"]
    node = pil.dam.set_index("hour_utc")["dam_spp"]
    node = node[~node.index.duplicated(keep="last")]
    return (node - hub.reindex(node.index)).dropna().rename("dam_basis_usd_per_mwh")


def dam_proxy_check(pil: D.PilotData, res: engine.AttributionResult) -> dict:
    """Same-period check of the hub-for-node proxy with day-ahead prices: error statistics of the DAM basis over the case
    study's own hours, and the first-order effect of that basis on the revaluations (as in `proxy_sensitivity`, but the
    basis is the period's own hourly DAM basis, a stand-in for the unobserved real-time one)."""
    b = dam_basis(pil)
    hubs = D.load_dam_hubs()
    hub = hubs[hubs["settlement_point"] == pil.meta["hub"]].set_index("hour_utc")["price"]
    node = pil.dam.set_index("hour_utc")["dam_spp"]
    node = node[~node.index.duplicated(keep="last")].reindex(b.index)
    idx = pil.prices.index
    bb = b.reindex(idx.floor("h")).fillna(0.0).to_numpy()
    st = res.stages
    dam_mw = pil.dam.set_index("hour_utc")["dam_energy_mw"]
    dam_mw = dam_mw[~dam_mw.index.duplicated(keep="last")].reindex(idx.floor("h")).fillna(0.0).to_numpy()
    d_act_rt = float((st["net_actual"].to_numpy() * DT_RT_H * bb).sum())
    d_pos = float(-(dam_mw * DT_RT_H * bb).sum())
    d_pf = float((st["net_pf"].to_numpy() * DT_RT_H * bb).sum())
    d_sh = float(np.nansum(st["net_shadow"].to_numpy() * DT_RT_H * bb))
    return dict(n_hours=int(len(b)), mean=float(b.mean()), mae=float(b.abs().mean()), rmse=float(np.sqrt((b ** 2).mean())),
                p95_abs=float(b.abs().quantile(0.95)), corr=float(node.corr(hub.reindex(b.index))),
                d_actual_usd=d_act_rt + d_pos, d_actual_rt_usd=d_act_rt, d_position_usd=d_pos, d_pf_usd=d_pf,
                d_shadow_usd=d_sh, d_gap_usd=d_pf - (d_act_rt + d_pos))


# ------------------------------------------------------------------------------------------------ tables
def daily_table(res: engine.AttributionResult) -> pd.DataFrame:
    fr = res.frame.copy()
    fr["day"] = fr["interval_start_utc"].dt.tz_convert(TZ).dt.date.astype(str)
    cols = [c for c in ATTRIBUTION_COLUMNS]
    return fr.groupby("day")[cols].sum().reset_index()


def interval_table(res: engine.AttributionResult) -> pd.DataFrame:
    st = res.stages.reset_index(names="interval_start_utc")
    keep = ["interval_start_utc", "known", "rt_usd_per_mwh", "net_actual", "net_pf", "net_avail", "net_as", "net_soc",
            "net_shadow", "soc_actual", "soc_pf", "avail_dis", "avail_chg", "award_mw_actual", "award_mw_pf", "position_usd",
            "r0", "r1", "r2", "r3", "r4"]
    return res.frame.merge(st[keep], on="interval_start_utc", how="left")


def provenance_rows(pil: D.PilotData) -> pd.DataFrame:
    m = pd.DataFrame(D.read_manifest())
    days = pd.date_range(pil.meta["first_day"], pil.meta["last_day"]).strftime("%Y-%m-%d")
    m = m[m["operating_day"].isin(set(days))].copy()
    m["file"] = m["file"].map(lambda p: Path(p).name)
    return m[["report_key", "report_type_id", "operating_day", "file", "url", "sha256", "bytes", "posted_utc",
              "downloaded_utc"]].sort_values(["report_key", "operating_day"])


def fingerprints(pil: D.PilotData) -> dict:
    def h(df):
        return hashlib.sha256(pd.util.hash_pandas_object(df, index=True).to_numpy().tobytes()).hexdigest()
    return {"actual_dispatch_sha256": h(pil.actual.frame.round(6)), "price_frame_sha256": h(pil.prices.round(6)),
            "position_sha256": h(pil.position_usd.round(6).to_frame())}


# ------------------------------------------------------------------------------------------------ charts
PALETTE = {"surface": "#fcfcfb", "ink": "#0b0b0b", "ink2": "#52514e", "muted": "#898781", "grid": "#e1e0d9",
           "axis": "#c3c2b7", "blue": "#2a78d6", "orange": "#eb6834", "aqua": "#1baf7a", "yellow": "#eda100",
           "magenta": "#e87ba4"}
COMPONENT_COLOR = {"forecast_usd": PALETTE["blue"], "soc_usd": PALETTE["orange"], "as_usd": PALETTE["aqua"],
                   "availability_usd": PALETTE["yellow"], "unexplained_usd": PALETTE["magenta"]}


def _plt():
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        from matplotlib import font_manager
    except Exception:
        return None
    fam = next((f for f in ("Segoe UI", "Arial", "DejaVu Sans") if any(f == x.name for x in font_manager.fontManager.ttflist)),
               "DejaVu Sans")
    plt.rcParams.update({"font.family": fam, "font.size": 10, "axes.edgecolor": PALETTE["axis"],
                         "axes.labelcolor": PALETTE["ink2"], "xtick.color": PALETTE["muted"], "ytick.color": PALETTE["muted"],
                         "text.color": PALETTE["ink"], "figure.facecolor": PALETTE["surface"],
                         "axes.facecolor": PALETTE["surface"], "savefig.facecolor": PALETTE["surface"],
                         "axes.spines.top": False, "axes.spines.right": False, "axes.grid": True, "grid.color": PALETTE["grid"],
                         "grid.linewidth": 0.8, "grid.linestyle": "-", "axes.axisbelow": True})
    return plt


def make_charts(pil: D.PilotData, res: engine.AttributionResult, d: Path, log=print) -> list:
    plt = _plt()
    if plt is None:
        log("matplotlib not importable: charts skipped")
        return []
    import matplotlib.dates as mdates
    made = []
    fr = res.frame.set_index("interval_start_utc")
    loc = fr.index.tz_convert(TZ)
    wear = pil.spec.wear_usd_per_mwh
    fmt_usd = lambda v, _: f"${v / 1000:,.0f}k"

    # 1. cumulative revenue
    fig, ax = plt.subplots(figsize=(9, 4.6))
    series = [("Perfect foresight", fr["pf_usd"].cumsum(), PALETTE["aqua"]),
              ("Shadow optimizer", fr["shadow_usd"].cumsum(), PALETTE["orange"]),
              ("Actual (RT dispatch + day-ahead positions)", fr["actual_usd"].cumsum(), PALETTE["blue"])]
    for name, s, c in series:
        ax.plot(loc, s.to_numpy(), color=c, lw=2, label=name, solid_capstyle="round")
        ax.plot([loc[-1]], [s.iloc[-1]], "o", color=c, ms=8, mec=PALETTE["surface"], mew=2)
        ax.annotate(usd(s.iloc[-1] / 1000, 1) + "k", (loc[-1], s.iloc[-1]), xytext=(8, 0), textcoords="offset points",
                    va="center", color=PALETTE["ink"], fontsize=9)
    span = f"{loc[0]:%d %b} - {loc[-1]:%d %b %Y}"
    ax.set_title(f"Cumulative revenue net of wear, {span}", loc="left", color=PALETTE["ink"], fontsize=11)
    ax.yaxis.set_major_formatter(fmt_usd)
    ax.set_xlim(loc[0], loc[-1] + (loc[-1] - loc[0]) * 0.1)
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%d %b"))
    ax.legend(frameon=False, loc="lower right", labelcolor=PALETTE["ink2"])
    fig.tight_layout()
    f = d / "fig_cumulative.png"
    fig.savefig(f, dpi=160)
    plt.close(fig)
    made.append(f.name)

    # 2. gap by component
    comp = res.summary["components_usd"]
    order = sorted(comp, key=lambda k: -abs(comp[k]))
    fig, ax = plt.subplots(figsize=(9, 3.4))
    y = np.arange(len(order))[::-1]
    ax.barh(y, [comp[k] for k in order], height=0.5, color=[COMPONENT_COLOR[k] for k in order])
    ax.set_yticks(y)
    ax.set_yticklabels([COMPONENT_LABEL[k] for k in order], color=PALETTE["ink2"])
    gap = res.summary["gap_usd"]
    for yy, k in zip(y, order):
        v = comp[k]
        ax.annotate(f"{usd(v)}  ({pct(v / gap)})", (max(v, 0), yy), xytext=(6, 0), textcoords="offset points", va="center",
                    color=PALETTE["ink"], fontsize=9)
    ax.axvline(0, color=PALETTE["axis"], lw=1)
    ax.xaxis.set_major_formatter(fmt_usd)
    ax.set_xlim(min(0, min(comp.values())) * 1.1, max(comp.values()) * 1.32)
    ax.set_title(f"Where the {usd(gap)} gap to perfect foresight went", loc="left", color=PALETTE["ink"], fontsize=11)
    ax.grid(axis="y", visible=False)
    fig.tight_layout()
    f = d / "fig_gap_components.png"
    fig.savefig(f, dpi=160)
    plt.close(fig)
    made.append(f.name)

    # 3. daily components
    dt = daily_table(res)
    keys = ["forecast_usd", "soc_usd", "as_usd", "availability_usd"]
    fig, ax = plt.subplots(figsize=(10, 4.4))
    x = np.arange(len(dt))
    pos_base, neg_base = np.zeros(len(dt)), np.zeros(len(dt))
    for k in keys:
        v = dt[k].to_numpy()
        base = np.where(v >= 0, pos_base, neg_base)
        ax.bar(x, v, bottom=base, width=0.6, color=COMPONENT_COLOR[k], label=COMPONENT_LABEL[k],
               edgecolor=PALETTE["surface"], linewidth=1.0)
        pos_base = pos_base + np.where(v >= 0, v, 0)
        neg_base = neg_base + np.where(v < 0, v, 0)
    step = max(1, len(dt) // 8)
    ax.set_xticks(x[::step])
    ax.set_xticklabels([pd.Timestamp(v).strftime("%d %b") for v in dt["day"].iloc[::step]])
    ax.yaxis.set_major_formatter(lambda v, _: f"${v / 1000:,.0f}k")
    ax.axhline(0, color=PALETTE["axis"], lw=1)
    ax.set_title("Gap to perfect foresight by market day and component", loc="left", color=PALETTE["ink"], fontsize=11)
    ax.legend(frameon=False, ncol=4, loc="upper center", bbox_to_anchor=(0.5, -0.1), labelcolor=PALETTE["ink2"], fontsize=9)
    ax.grid(axis="x", visible=False)
    fig.tight_layout()
    f = d / "fig_daily_components.png"
    fig.savefig(f, dpi=160)
    plt.close(fig)
    made.append(f.name)

    # 4. one day in detail: the day with the largest perfect-foresight revenue
    day = fr["pf_usd"].groupby(loc.date).sum().idxmax()
    sel = [i for i, t in enumerate(loc) if t.date() == day]
    st = res.stages.iloc[sel]
    t = loc[sel]
    fig, axes = plt.subplots(3, 1, figsize=(9, 7.2), sharex=True)
    axes[0].plot(t, st["rt_usd_per_mwh"].to_numpy(), color=PALETTE["ink2"], lw=2)
    axes[0].set_ylabel("RT price, $/MWh")
    axes[0].set_title(f"{day:%d %b %Y}: price, dispatch and state of charge", loc="left", color=PALETTE["ink"], fontsize=11)
    for name, col, c in (("Perfect foresight", "net_pf", PALETTE["aqua"]), ("Shadow optimizer", "net_shadow", PALETTE["orange"]),
                         ("Actual", "net_actual", PALETTE["blue"])):
        axes[1].step(t, st[col].to_numpy(), where="post", color=c, lw=2, label=name)
    axes[1].set_ylabel("Net MW (+ discharge)")
    axes[1].legend(frameon=False, ncol=3, loc="upper left", labelcolor=PALETTE["ink2"], fontsize=9)
    axes[2].plot(t, st["soc_pf"].to_numpy(), color=PALETTE["aqua"], lw=2, label="Perfect foresight")
    axes[2].plot(t, st["soc_actual"].to_numpy(), color=PALETTE["blue"], lw=2, label="Actual (telemetered)")
    axes[2].set_ylabel("SOC, MWh")
    axes[2].legend(frameon=False, ncol=2, loc="upper left", labelcolor=PALETTE["ink2"], fontsize=9)
    axes[2].xaxis.set_major_formatter(mdates.DateFormatter("%H:%M", tz=TZ))
    fig.tight_layout()
    f = d / "fig_example_day.png"
    fig.savefig(f, dpi=160)
    plt.close(fig)
    made.append(f.name)
    return made


# ------------------------------------------------------------------------------------------------ markdown
def write_markdown(pil, results, wear_df, proxy_df, files, dp_stats, d: Path, timing: dict, dam_px: dict) -> str:
    head = results["all_AS"]
    s = head.summary
    sp = pil.spec
    meta = pil.meta
    hs = meta["hub_stats"]
    sh = s["shapley_total_usd"]
    seq = s["sequential_total_usd"]
    c = s["components_usd"]
    gap = s["gap_usd"]
    bd = s["stage_breakdown_usd"]
    act_rt = s["stage_totals_usd"]["actual"]
    pos = s.get("position_usd", 0.0)
    shadow_frame = head.shadow.frame
    sh_energy, sh_as, sh_wear = (float(shadow_frame[k].sum()) for k in ("energy_usd", "as_usd", "wear_usd"))
    days = (pd.Timestamp(meta["last_day"]) - pd.Timestamp(meta["first_day"])).days + 1
    vr = {k: v.summary for k, v in results.items()}
    px = proxy_df
    px_act = (px["d_actual_usd"].abs().max()) if len(px) else float("nan")
    px_gap = (px["d_gap_usd"].abs().max()) if len(px) else float("nan")
    stress_rt = px_act / abs(gap) if gap else float("nan")
    stress_dam = abs(dam_px["d_actual_usd"]) / abs(gap) if gap else float("nan")
    stress = max(stress_rt, stress_dam)
    gate_closure = s["closure"] < ATTRIBUTION_CLOSURE_MAX
    gate_stress = stress < ATTRIBUTION_CLOSURE_MAX
    top = engine.explain_top(head, 20)
    top_local = top.copy()
    top_local["when_cpt"] = top_local["interval_start_utc"].dt.tz_convert(TZ).dt.strftime("%d %b %H:%M")
    eff = meta["efficiency"]
    aux_mw, aux_cost = meta["aux_load_mw"], meta["aux_load_cost_usd"]
    lines = []
    A = lines.append
    A(f"# Public case study: {sp.asset_id}, {pd.Timestamp(meta['first_day']):%d %b} to {pd.Timestamp(meta['last_day']):%d %b %Y}")
    A("")
    A(f"One ERCOT battery, {days} operating days of ERCOT's own 60-day disclosure data, interval-by-interval attribution "
      "against perfect foresight. Every figure carries a label: **REAL** (read from a named ERCOT source), **DERIVED** "
      "(computed from REAL inputs by a stated rule) or **ASSUMPTION** (our choice; change it and rerun). Nothing here "
      "uses owner data or settlement statements. Generated by `python -m dispatch.attribution.report`.")
    A("")
    A("## Headline")
    A("")
    w0 = wear_df[wear_df["wear_usd_per_mwh"] == 0.0]
    cap0 = float(w0["capture_actual"].iloc[0]) if len(w0) else float("nan")
    pos_energy = float(pil.position_parts["energy"].sum())
    eo, nr = vr["energy_only"], vr["no_regulation"]
    A(f"Valued at proxy prices, the asset's dispatch and day-ahead positions over {days} days come to **{usd(s['actual_usd'])}** "
      f"(DERIVED; not settled revenue), which is **{pct(s['capture_actual'])}** of the "
      f"**{usd(s['pf_usd'])}** a perfect-foresight operator with the same battery would have earned, net of a "
      f"${sp.wear_usd_per_mwh:g}/MWh discharge wear charge (ASSUMPTION) applied identically to every column. With no wear "
      f"charge in either number the share is **{pct(cap0)}**, which is how capture rates are usually quoted. "
      f"{usd(pos)} of the asset's {usd(s['actual_usd'])} are day-ahead positions (REAL awards and prices, settled against "
      "proxy real-time prices).")
    A("")
    A(f"The shadow optimizer, which sees only public information available at each decision time, would have earned "
      f"**{usd(s['shadow_usd'])}** (**{pct(s['capture_shadow'])}** of perfect foresight) and recovered "
      f"**{pct(s['shadow_recovers_share_of_gap'], 0)}** of the {usd(gap)} gap, **but only because it sells ancillary "
      f"capacity**: restricted to energy it earns {usd(eo['shadow_usd'])} against the asset's {usd(eo['actual_usd'])}, "
      f"and without regulation {usd(nr['shadow_usd'])} against {usd(nr['actual_usd'])} (see "
      "[Scope sensitivity](#scope-sensitivity)).")
    A("")
    A(md_table(["Quantity", "Value", "Share of perfect foresight", "Label"], [
        ["Perfect foresight (same battery, realised prices, free ancillary awards)", usd(s["pf_usd"]), "100%", "DERIVED"],
        ["Shadow optimizer (seasonal-naive forecast, hourly re-plan)", usd(s["shadow_usd"]), pct(s["capture_shadow"]), "DERIVED"],
        ["Actual: real-time dispatch and awards, valued at proxy prices", usd(act_rt), pct(act_rt / s["pf_usd"]), "DERIVED"],
        ["Actual: day-ahead positions settled against real time", usd(pos), pct(pos / s["pf_usd"]), "DERIVED"],
        ["**Actual, total**", f"**{usd(s['actual_usd'])}**", f"**{pct(s['capture_actual'])}**", "DERIVED"],
        ["Gap to perfect foresight", usd(gap), pct(gap / s["pf_usd"]), "DERIVED"],
    ]))
    A("")
    A("The gap, split by cause (sequential order: availability, then ancillary awards, then SOC position, then the rest):")
    A("")
    A(md_table(["Component", "USD", "Share of gap", "What it measures", "Label"], [
        [COMPONENT_LABEL["as_usd"], usd(c["as_usd"]), pct(c["as_usd"] / gap),
         "Revenue a price-taking operator earns by selling ancillary capacity that the asset did not hold in real time", "DERIVED"],
        [COMPONENT_LABEL["forecast_usd"], usd(c["forecast_usd"]), pct(c["forecast_usd"] / gap),
         "Best response to realised prices from the same start SOC, awards and derates, minus what the asset did "
         f"(includes the {usd(pos)} day-ahead position value)", "DERIVED"],
        [COMPONENT_LABEL["soc_usd"], usd(c["soc_usd"]), pct(c["soc_usd"] / gap),
         "Cost of the state of charge the asset carried into each market day, against a free SOC path", "DERIVED"],
        [COMPONENT_LABEL["availability_usd"], usd(c["availability_usd"]), pct(c["availability_usd"] / gap),
         "Power derates (HSL/LSL) and reduced Maximum/Minimum SOC the asset reported", "DERIVED"],
        [COMPONENT_LABEL["unexplained_usd"], usd(c["unexplained_usd"]), pct(c["unexplained_usd"] / gap),
         "Data gaps, and any difference to reported revenue (none supplied)", "DERIVED"],
    ]))
    A("")
    A(f"**Closure** (|sum of unexplained| / |sum of gap|, PRD target below {pct(ATTRIBUTION_CLOSURE_MAX, 0)}) is "
      f"**{pct(s['closure'])}** on {pct(s['coverage'], 1)} of intervals. See [Gate 2](#gate-2-attribution-closure) before "
      "reading that number: it is zero by construction here.")
    A("")
    A("### Read this before quoting the split")
    A("")
    A(f"* **Ancillary services dominate, and that rests on assumptions.** The benchmark sells regulation up and down at "
      f"the same time (about {head.plans['pf'][as_award_col('REGUP')].mean():.0f} MW of each on average) at the real-time clearing price, with no "
      "deployment energy, no wear on deployed regulation, and every offered MW awarded. Its ancillary revenue is "
      f"{usd(bd['pf']['as_usd'])}; the asset's real-time awards earned {usd(bd['actual']['as_usd'])}. Against narrower "
      f"benchmarks the capture rate is {pct(nr['capture_actual'])} (regulation excluded) and "
      f"{pct(eo['capture_actual'])} (energy only).")
    A(f"* **The shadow's lead is an ancillary lead.** In the energy-only scope the shadow is behind the asset "
      f"({usd(eo['shadow_usd'])} against {usd(eo['actual_usd'])}); of the asset's revenue there, {usd(pos_energy)} is a "
      "day-ahead energy position that the v1 shadow (real-time only) does not take.")
    A(f"* **Capture depends on the wear assumption.** The same asset scores {pct(cap0)} with no wear charge, "
      + ", ".join(f"{pct(r.capture_actual)} at ${r.wear_usd_per_mwh:g}" for r in wear_df[wear_df['wear_usd_per_mwh'] > 0].itertuples())
      + " per MWh discharged: its real-time margin is thin, so a charge per MWh moves the ratio a lot.")
    A(f"* **Prices are a proxy.** Nodal real-time history before 3 Oct 2026 needs an ERCOT API key we do not have. Hub "
      f"{meta['hub']} real-time SPP stands in for node {meta['node']}. Measured on the 7-day nodal window the node-minus-hub "
      f"basis has mean {usd(hs['bias'], 2)}, MAE {usd(hs['mae'], 2)}, RMSE {usd(hs['rmse'], 2)} per MWh and correlation "
      f"{hs['corr']:.4f}; in dollars that could move the asset's revenue by about {pct(stress_rt, 0)} of the gap "
      "([Proxy price error](#proxy-price-error)).")
    A("* **Perfect foresight is a ceiling no operator reaches.** The gap is a distance to that ceiling, not money anyone "
      "could have collected.")
    A("")
    A("## The asset")
    A("")
    A(md_table(["Item", "Value", "Label", "Source or rule"], [
        ["Resource", f"{sp.asset_id}", "REAL", "ESR in the NP3-965-ER 60-day SCED disclosure"],
        ["Resource node", f"{sp.settlement_point} ({meta['load_zone']})", "REAL",
         "DAM disclosure `Settlement Point Name`; listed as a RESOURCE_NODE in NP4-160 (Oct ML1 model)"],
        ["Discharge limit (HSL, maximum)", f"{sp.power_mw:g} MW", "REAL", "max telemetered HSL in the period"],
        ["Charge limit (-LSL, maximum)", f"{meta['p_chg_mw']:g} MW", "REAL", "max telemetered -LSL in the period"],
        ["Energy window", f"0 to {sp.energy_mwh:g} MWh", "REAL", "max telemetered Maximum SOC"],
        ["Round-trip efficiency", f"{sp.rte:.3f} (losses on charge)", "DERIVED",
         f"energy balance of the whole period; per-run medians give {eff['rte_median']:.3f}, least squares {eff['rte_ls']:.3f}"],
        ["Standing auxiliary draw", f"{aux_mw:.1f} MW ({usd(aux_cost)} over the period)", "DERIVED",
         "median telemetered output at zero base point; removed from dispatch, same in every column"],
        ["Wear cost", f"${sp.wear_usd_per_mwh:g}/MWh discharged", "ASSUMPTION", "the research arena's value"],
        ["Ancillary products", ", ".join(sp.as_products), "REAL", "RTC+B products; durations from NPRR1282 (30 min regulation and RRS, "
         "1 h ECRS, 4 h NSPIN)"],
    ]))
    A("")
    A("### Why this asset")
    A("")
    sel = pil.selection
    tbl = sel.table
    crit = sel.criteria
    A(f"The rules were fixed before the choice was made: {crit.min_mw:g} to {crit.max_mw:g} MW (the PRD's first user); present "
      f"in at least {crit.min_days} of the staged days; resource node mapped in NP4-160 and priced in the 7-day nodal window; "
      f"best-hub RMSE at most ${crit.max_basis_rmse:g}/MWh and absolute bias at most ${crit.max_abs_bias:g}/MWh; at least "
      f"{crit.min_cycles_day:g} discharge cycles a day and real-time ancillary awards in at least "
      f"{pct(crit.min_award_share, 0)} of SCED runs (so it trades and the award term is exercised); and the node must hold "
      f"only this unit. Of {len(tbl)} ESRs in the disclosure, {int(tbl['ok_size'].sum())} are in the size range, "
      f"{int(tbl['ok_basis'].sum())} pass the basis rule, and {int(tbl['passes_all'].sum())} pass every rule. The lowest-RMSE "
      "one is the pilot (`pilot_selection.csv` lists all).")
    A("")
    cands = tbl[tbl["passes_all"]].sort_values("rmse")
    A(md_table(["ESR", "MW", "MWh", "Node", "Best hub", "RMSE $/MWh", "Bias $/MWh", "Corr", "Cycles/day", "Award share"], [
        [i, f"{r.hsl_max_mw:g}", f"{r.max_soc_mwh:g}", r.node, r.hub, f"{r.rmse:.2f}", f"{r.bias:+.2f}", f"{r['corr']:.4f}",
         f"{r.cycles_per_day:.2f}", pct(r.award_share, 0)] for i, r in cands.iterrows()]))
    A("")
    A("## Data and provenance")
    A("")
    A(md_table(["Data", "Source", "Window", "Label"], [
        ["Telemetry: net output, base point, HSL/LSL, SOC and limits, RT ancillary awards (288 SCED runs a day)",
         "ERCOT MIS reportTypeId 13052, NP3-965-ER `60d_ESR_Data_in_SCED`", f"{meta['first_day']} to {meta['last_day']}", "REAL"],
        ["Day-ahead energy and ancillary awards, DAM nodal SPP, MCPC",
         "ERCOT MIS reportTypeId 13051, NP3-966-ER `60d_DAM_ESR_Data`", f"{meta['first_day']} to {meta['last_day']}", "REAL"],
        ["Real-time energy price", f"Hub {meta['hub']} 15-minute SPP, NP6-785-ER yearly csv (`rtm2026.csv`)",
         "to 26 Sep 2026", "REAL as a hub price; ASSUMPTION as a stand-in for the node"],
        ["Real-time ancillary prices (MCPC)", "NP6-331 / MIS 25570 archive, `as_prices.parquet` market RT_15",
         "since 5 Dec 2025 (RTC+B)", "REAL"],
        ["Node to hub basis", "NP6-905 15-minute SPP at resource nodes and hubs (`spp_node.parquet`)",
         "7 days, 3 to 10 Oct 2026", "REAL"],
        ["Node mapping", "NP4-160 `Resource_Node_to_Unit` and settlement_points.csv (Oct ML1)", "current model", "REAL"],
    ]))
    A("")
    A(f"The {len(provenance_rows(pil))} daily zips used (one SCED and one DAM disclosure per operating day) are listed with url, file name, sha256, bytes and download time in "
      "`provenance_files.csv` (staged under `DISPATCH_HOME/staging/ercot-60day/`; D0 adopts staging into the lake). Input "
      f"fingerprints: actual dispatch `{meta['fingerprints']['actual_dispatch_sha256'][:16]}`, prices "
      f"`{meta['fingerprints']['price_frame_sha256'][:16]}`.")
    A("")
    A("## Method in one paragraph")
    A("")
    A("The same LP and accounting (`optimizer/lp.py`) values every column. Perfect foresight solves the whole period at "
      "once with free ancillary awards. Four counterfactuals then add one real restriction each: the asset's reported "
      "power derates and SOC limits; its actual ancillary awards; its telemetered SOC at the start (and no lower at the "
      "end) of each market day; and finally the actual dispatch itself, revalued with the same accounting. Subtracting "
      "consecutive stages interval by interval gives components that sum to the gap exactly. The shadow optimizer "
      "re-plans every hour over a 48-hour horizon with a seasonal-naive forecast (same time of day on the latest published "
      "day) that uses only prices published before each decision, takes the asset's real derates as known, and is revalued on "
      "realised prices. Day-ahead positions (awards against the DAM nodal SPP and MCPC, from ERCOT's own report) are part of "
      "actual revenue and are attributed to the forecast and decision component.")
    A("")
    A("### Stage totals")
    A("")
    st_tot = s["stage_totals_usd"]
    A(md_table(["Stage", "Net revenue", "Energy", "Ancillary", "Wear", "Label"], [
        ["S0 perfect foresight", usd(st_tot["pf"]), usd(bd["pf"]["energy_usd"]), usd(bd["pf"]["as_usd"]), usd(-bd["pf"]["wear_usd"]), "DERIVED"],
        ["S1 + availability", usd(st_tot["availability"]), usd(bd["availability"]["energy_usd"]), usd(bd["availability"]["as_usd"]), usd(-bd["availability"]["wear_usd"]), "DERIVED"],
        ["S2 + actual awards", usd(st_tot["as"]), usd(bd["as"]["energy_usd"]), usd(bd["as"]["as_usd"]), usd(-bd["as"]["wear_usd"]), "DERIVED"],
        ["S3 + daily SOC anchor", usd(st_tot["soc"]), usd(bd["soc"]["energy_usd"]), usd(bd["soc"]["as_usd"]), usd(-bd["soc"]["wear_usd"]), "DERIVED"],
        ["S4 actual dispatch (RT only)", usd(st_tot["actual"]), usd(bd["actual"]["energy_usd"]), usd(bd["actual"]["as_usd"]), usd(-bd["actual"]["wear_usd"]), "DERIVED"],
        ["Shadow optimizer", usd(s["shadow_usd"]), usd(sh_energy), usd(sh_as), usd(-sh_wear), "DERIVED"],
    ]))
    A("")
    A("## Results in detail")
    A("")
    A("![Cumulative revenue](fig_cumulative.png)")
    A("")
    A("![Gap by component](fig_gap_components.png)")
    A("")
    A("![Daily components](fig_daily_components.png)")
    A("")
    A("### Order dependence")
    A("")
    A("The sequential split depends on the order of the chain. The average over all six orderings of the first three "
      "stages (Shapley) gives:")
    A("")
    A(md_table(["Component", "Sequential", "Shapley average", "Difference"], [
        [COMPONENT_LABEL[k], usd(seq[k]), usd(sh[k]), usd(sh[k] - seq[k])] for k in ("availability_usd", "as_usd", "soc_usd")]))
    A("")
    A(f"The largest difference is {usd(s['order_spread_usd'])}, {pct(s['order_spread_share_of_gap'])} of the gap. The forecast "
      "and decision component does not depend on the order (it is always the last stage).")
    A("")
    A("### Scope sensitivity")
    A("")
    A("The benchmark can include every ancillary product (headline) or fewer. Each row restricts the products on both "
      "sides: the asset's awards and day-ahead positions outside the scope are left out too.")
    A("")
    rows = []
    for name, (prods, desc) in SCOPES.items():
        v = vr[name]
        cc = v["components_usd"]
        rows.append([desc, usd(v["pf_usd"]), usd(v["actual_usd"]), pct(v["capture_actual"]), pct(v["capture_shadow"]),
                     usd(cc["as_usd"]), usd(cc["forecast_usd"]), usd(cc["soc_usd"]), usd(cc["availability_usd"]),
                     pct(v["shadow_recovers_share_of_gap"], 0)])
    A(md_table(["Benchmark", "Perfect foresight", "Actual", "Actual capture", "Shadow capture", "AS", "Forecast and decision",
                "SOC", "Availability", "Shadow recovers gap"], rows))
    A("")
    A("### Wear assumption")
    A("")
    A(md_table(["Wear $/MWh", "Perfect foresight", "Actual", "Capture", "Gap", "AS", "Forecast and decision", "SOC", "Availability"], [
        [f"{r.wear_usd_per_mwh:g}", usd(r.pf_usd), usd(r.actual_usd), pct(r.capture_actual), usd(r.gap_usd), usd(r.as_usd),
         usd(r.forecast_usd), usd(r.soc_usd), usd(r.availability_usd)] for r in wear_df.itertuples()]))
    A("")
    A("### Proxy price error")
    A("")
    A(f"Hub {meta['hub']} replaces node {meta['node']} for real-time energy. On the 7-day nodal window (REAL, "
      f"{hs['n']:.0f} intervals) the node-minus-hub basis has mean {usd(hs['bias'], 2)}, MAE {usd(hs['mae'], 2)}, 95th "
      f"percentile of the absolute error {usd(hs['p95_abs'], 2)}, RMSE {usd(hs['rmse'], 2)} per MWh, correlation {hs['corr']:.4f}. "
      "To turn that into dollars, each full day of the window's basis (by time of day) is applied to every day of the "
      "period and the dispatches are revalued to first order (DERIVED; the day-ahead position settles against the "
      "real-time price, so it moves too):")
    A("")
    A(md_table(["Basis day", "Mean basis $/MWh", "Actual", "Perfect foresight", "Shadow", "Gap"], [
        [r.basis_day, f"{r.mean_basis:+.2f}", usd(r.d_actual_usd), usd(r.d_pf_usd), usd(r.d_shadow_usd), usd(r.d_gap_usd)]
        for r in px.itertuples()]))
    A("")
    A(f"Across those seven draws the actual's revenue moves by at most {usd(px_act)} ({pct(stress_rt)} of the gap) "
      f"and the gap itself by at most {usd(px_gap)} ({pct(px_gap / abs(gap))}). Seven October days are a thin sample for "
      "July and August, so there is a second check on the period's own hours.")
    A("")
    A(f"**Same-period check with day-ahead prices (REAL).** The same 60-day DAM disclosure carries ERCOT's day-ahead price "
      f"at every hub, so for the {dam_px['n_hours']} hours of the case study the node's day-ahead price can be compared "
      f"with {meta['hub']}'s: mean difference {usd(dam_px['mean'], 2)}, MAE {usd(dam_px['mae'], 2)}, 95th percentile "
      f"{usd(dam_px['p95_abs'], 2)}, RMSE {usd(dam_px['rmse'], 2)} per MWh, correlation {dam_px['corr']:.4f}. Applying that "
      f"hourly basis to the real-time revaluation moves the actual by {usd(dam_px['d_actual_usd'])} "
      f"({pct(stress_dam)} of the gap), perfect foresight by {usd(dam_px['d_pf_usd'])}, the shadow by "
      f"{usd(dam_px['d_shadow_usd'])} and the gap by {usd(dam_px['d_gap_usd'])}. Day-ahead is not real-time, so this "
      "bounds the proxy rather than measuring it.")
    A("")
    A("## Gate 2: attribution closure")
    A("")
    A(md_table(["Test", "Result", "Pass"], [
        ["Components sum to the gap in every interval (max abs error)", f"{s['identity_max_abs_error']:.1e} USD", "PASS" if s["identity_max_abs_error"] < 1e-6 else "FAIL"],
        ["Closure, PRD definition (abs of summed unexplained over abs of summed gap), target < 5%", pct(s["closure"]), "PASS" if gate_closure else "FAIL"],
        ["Strict closure (summed absolute unexplained over summed absolute gap)", pct(s["closure_l1"]), "PASS" if s["closure_l1"] < ATTRIBUTION_CLOSURE_MAX else "FAIL"],
        ["Data coverage (intervals with actual telemetry and prices)", pct(s["coverage"]), "PASS" if s["coverage"] >= 0.99 else "FAIL"],
        ["Stress: revenue the hub proxy could move, real-time basis of 7 October days (worst draw), share of the gap",
         pct(stress_rt), "PASS" if stress_rt < ATTRIBUTION_CLOSURE_MAX else "FAIL"],
        ["Stress: same, day-ahead basis of the case-study hours, share of the gap", pct(stress_dam),
         "PASS" if stress_dam < ATTRIBUTION_CLOSURE_MAX else "FAIL"],
    ]))
    A("")
    A("Separately, `tests/test_attribution.py` checks the engine on synthetic assets whose answer is known (actual equal to "
      "perfect foresight gives no gap; idle gives gap equal to perfect foresight; injected derates and award differences land "
      "in their own components; closure below 5% on clean data).")
    A("")
    gate_pass = bool(gate_closure and s["identity_max_abs_error"] < 1e-6 and s["coverage"] >= 0.99)
    A(f"**Gate 2 status: {'PASS' if gate_pass else 'FAIL'} on the PRD definition** (unexplained {pct(s['closure'])} of the gap, "
      f"target below {pct(ATTRIBUTION_CLOSURE_MAX, 0)}), **with one qualification stated plainly.** Public data holds no "
      "reported or settled revenue to compare with, so the actual revenue IS the revaluation of telemetry and the unexplained "
      "term is zero apart from data gaps: here closure measures coverage, not accuracy. It becomes a real test only when an "
      "owner supplies settlement statements (`reported_usd` in `engine.attribute`).")
    A("")
    A(f"The stress row is the honest substitute: the largest share of the gap that the hub-for-node price proxy alone could move "
      f"is {pct(stress)}, which is {'below' if gate_stress else 'ABOVE'} the {pct(ATTRIBUTION_CLOSURE_MAX, 0)} target. "
      + ("So even if the true settled revenue differed from our revaluation only through the price proxy, the split would "
         "still close." if gate_stress else
         "So with hub prices the actual revenue itself is only known to about that precision; closure below 5% against "
         "settled revenue is not demonstrated until the node price or a settlement export is used."))
    A("")
    A("## The 20 largest missed intervals")
    A("")
    A("Each row is one 15-minute interval (Central time). Components are in dollars and sum to the gap; the explanation "
      "uses only measured dispatch, awards, SOC and derates. The full interval list is `attribution_intervals.csv`.")
    A("")
    A(md_table(["#", "When (CPT)", "Gap", "Forecast/decision", "SOC", "AS", "Avail.", "Dominant", "Explanation"], [
        [i + 1, r.when_cpt, usd(r.gap_usd), usd(r.forecast_usd), usd(r.soc_usd), usd(r.as_usd), usd(r.availability_usd),
         r.dominant, r.explanation] for i, r in enumerate(top_local.itertuples())]))
    A("")
    A("![One day in detail](fig_example_day.png)")
    A("")
    A("## Limits")
    A("")
    A("* **No settled revenue.** Revenue is telemetered output times a hub price plus ERCOT's disclosed day-ahead awards; the "
      "real settlement uses metered energy at the node and may include items we cannot see.")
    A("* **Hub proxy.** See above; the proxy error moves every column, the gap less.")
    A("* **Benchmark assumptions.** Price-taking, every offered MW awarded, no deployment energy or wear for ancillary "
      "services, regulation up and down held together, conservative power limits (discharge plus up awards at most the "
      "limit). Real SCED and the asset's own offers would differ.")
    A(f"* **Actual versus the LP's physics.** On {s['actual_model_violation']['days_checked']} checked days the actual flows "
      f"run through the LP's dynamics from the telemetered start SOC end within {s['actual_model_violation']['soc_balance_resid_mean_abs_mwh']:.1f} "
      f"MWh of the telemetered end SOC on average (worst {s['actual_model_violation']['soc_balance_resid_max_abs_mwh']:.1f}). "
      f"In a few intervals the asset's discharge plus up awards exceeded its HSL by up to "
      f"{s['actual_model_violation']['discharge_plus_up_over_power']:.0f} MW (ERCOT counts headroom against base point, not "
      "telemetry); those are not clipped, only reported.")
    A(f"* **Shadow.** It takes derates as known at decision time (an ASSUMPTION), assumes full awards at the clearing price, and "
      f"uses a seasonal-naive forecaster; a trained model would change it. It ran {s['shadow_solves']} plans, "
      f"{s['shadow_failed_solves']} failed.")
    A(f"* **One asset, {days} days.** Not a fleet statistic and not a forecast.")
    A("")
    A("## Cross-check of the LP against the research arena")
    A("")
    if dp_stats:
        A(f"On {dp_stats['n_days']} historical zone-days (8 ERCOT load zones, 2011 to 2026, includes the highest-spread and "
          "negative-price days) the platform LP and the arena's dynamic programme agree: LP minus DP is between "
          f"{dp_stats['arena_min_diff']:.1e} and {dp_stats['arena_max_diff']:.1e} $ per MW-day (total relative difference "
          f"{dp_stats['arena_total_rel_diff']:.1e}). With the guard-compatible grid-side charge limit the LP earns "
          f"{pct(-dp_stats['ac_total_rel_diff'])} less than the DP in total (up to 15% on days when the charge rate binds), "
          "because the arena caps stored energy per interval where the guard caps grid power. See `notes/d1b.md`.")
    else:
        A("Run `python -m dispatch.optimizer.dp_check` to produce `dp_crosscheck.csv`.")
    A("")
    A("## Files")
    A("")
    for f in files:
        A(f"* `{f}`")
    A("")
    A(f"Run time: {timing['total_s']:.0f} s.")
    text = "\n".join(lines) + "\n"
    (d / "case_study.md").write_text(text, encoding="utf-8")
    return text


# ------------------------------------------------------------------------------------------------ driver
@dataclasses.dataclass
class Bundle:
    """Everything `compute` produces and `write_all` turns into files (kept apart so the formatting can be iterated
    without re-solving)."""
    pil: D.PilotData
    results: dict
    wear_df: pd.DataFrame
    proxy_df: pd.DataFrame
    first_day: str
    last_day: str
    compute_s: float


def compute(first_day: str = "2026-07-01", last_day: str = "2026-08-11", shadow_step: int = 4,
            wear_grid=(0.0, 10.0, 30.0), log=print) -> Bundle:
    t0 = time.time()
    for kind in ("sced", "dam"):
        D.cache_esr_days(kind, log=log)
    pil = D.build_pilot(first_day, last_day, log=log)
    pil.meta["fingerprints"] = fingerprints(pil)
    log(f"inputs ready after {time.time() - t0:.0f}s")
    fc = rolling.SeasonalNaiveForecaster(history_prices(pil), settlement_point=pil.spec.settlement_point,
                                         horizon_intervals=192)
    results = run_scope_variants(pil, fc, shadow_step, log=log)
    head = results["all_AS"].summary
    wear_df = run_wear_sensitivity(pil, wear_grid, log=log)
    headline_row = dict(wear_usd_per_mwh=pil.spec.wear_usd_per_mwh, pf_usd=head["pf_usd"], actual_usd=head["actual_usd"],
                        gap_usd=head["gap_usd"], capture_actual=head["capture_actual"], closure=head["closure"],
                        **{k: head["components_usd"][k] for k in ("forecast_usd", "soc_usd", "as_usd", "availability_usd")})
    wear_df = pd.concat([wear_df, pd.DataFrame([headline_row])]).sort_values("wear_usd_per_mwh").reset_index(drop=True)
    proxy_df = proxy_sensitivity(pil, results["all_AS"])
    return Bundle(pil, results, wear_df, proxy_df, first_day, last_day, time.time() - t0)


def write_all(b: Bundle, log=print) -> dict:
    d = out_dir()
    pil, results, wear_df, proxy_df = b.pil, b.results, b.wear_df, b.proxy_df
    head = results["all_AS"]
    files = ["case_study.md", "summary.json", "attribution_intervals.csv", "attribution_daily.csv", "top20_missed_intervals.csv",
             "scope_variants.csv", "wear_sensitivity.csv", "proxy_sensitivity.csv", "pilot_selection.csv",
             "provenance_files.csv"]
    dp_stats = None
    dp_csv = d / "dp_crosscheck.csv"
    if dp_csv.exists():
        from ..optimizer import dp_check
        dp_stats = dp_check.summarize(pd.read_csv(dp_csv))
        files.append("dp_crosscheck.csv")
    interval_table(head).to_csv(d / "attribution_intervals.csv", index=False)
    daily_table(head).to_csv(d / "attribution_daily.csv", index=False)
    top = engine.explain_top(head, 20)
    top.to_csv(d / "top20_missed_intervals.csv", index=False)
    pd.DataFrame([{**{"scope": n, "description": SCOPES[n][1]},
                   **{k: v for k, v in r.summary.items() if isinstance(v, (int, float, str, bool)) and k != "notes"},
                   **{f"comp_{k}": v for k, v in r.summary["components_usd"].items()}} for n, r in results.items()]
                 ).to_csv(d / "scope_variants.csv", index=False)
    wear_df.to_csv(d / "wear_sensitivity.csv", index=False)
    proxy_df.to_csv(d / "proxy_sensitivity.csv", index=False)
    pil.selection.table.reset_index().to_csv(d / "pilot_selection.csv", index=False)
    provenance_rows(pil).to_csv(d / "provenance_files.csv", index=False)
    files += make_charts(pil, head, d, log=log)
    timing = {"total_s": b.compute_s}
    dam_px = dam_proxy_check(pil, head)
    write_markdown(pil, results, wear_df, proxy_df, files, dp_stats, d, timing, dam_px)
    s = head.summary
    summary = dict(
        asset=pil.spec.asset_id, node=pil.spec.settlement_point, hub=pil.meta["hub"], first_day=b.first_day,
        last_day=b.last_day, spec=dataclasses.asdict(pil.spec), meta=pil.meta, **{k: s[k] for k in (
            "coverage", "closure", "closure_l1", "closure_basis", "identity_max_abs_error", "actual_usd", "pf_usd", "gap_usd",
            "capture_actual", "shadow_usd", "capture_shadow", "shadow_recovers_share_of_gap", "components_usd",
            "components_share_of_gap", "stage_totals_usd", "stage_breakdown_usd", "position_usd", "shapley_total_usd",
            "sequential_total_usd", "order_spread_usd", "order_spread_share_of_gap", "actual_model_violation", "notes")},
        variants={n: {k: v for k, v in r.summary.items() if k in (
            "pf_usd", "actual_usd", "gap_usd", "capture_actual", "shadow_usd", "capture_shadow", "components_usd",
            "shadow_recovers_share_of_gap", "closure")} for n, r in results.items()},
        wear_sensitivity=wear_df.to_dict("records"), proxy_sensitivity=proxy_df.to_dict("records"),
        proxy_stress_closure=float(max(proxy_df["d_actual_usd"].abs().max(), abs(dam_px["d_actual_usd"])) / abs(s["gap_usd"])),
        dam_proxy_check=dam_px,
        gate2=dict(closure_target=ATTRIBUTION_CLOSURE_MAX, closure_pass=bool(s["closure"] < ATTRIBUTION_CLOSURE_MAX)),
        files=files, elapsed_s=b.compute_s,
        top20=top.assign(interval_start_utc=top["interval_start_utc"].astype(str)).to_dict("records"))
    (d / "summary.json").write_text(json.dumps(jsonable(summary), indent=1), encoding="utf-8")
    log(f"report written to {d}")
    return summary


def run(first_day: str = "2026-07-01", last_day: str = "2026-08-11", shadow_step: int = 4,
        wear_grid=(0.0, 10.0, 30.0), log=print) -> dict:
    return write_all(compute(first_day, last_day, shadow_step, wear_grid, log), log)


if __name__ == "__main__":
    run()
