"""Numbers behind notes/phase1-market.md, computed from the derived parquet files (`run_grid.py report-ercot`).

Prints markdown tables; the notes quote them.  Nothing here ranks with a language model: every rank is a sort."""
import numpy as np
import pandas as pd
import pyarrow.compute as pc
import pyarrow.parquet as pq

from . import ercot_parse as ep
from .paths import DERIVED, REPO_DATA

SCED_INTERVAL_H = ep.SCED_INTERVAL_H           # ASSUMPTION: one SCED run = 5 minutes
FLAT_TOL = 0.005                               # ASSUMPTION: |LMP - lambda| below this counts as "no congestion" (prices have 2 decimals, float32 noise)
SEP_THRESHOLDS = (1, 10, 100)                  # ASSUMPTION: $/MWh levels used to describe how widely prices separate from lambda
CAP_TOL = 0.999                                # ASSUMPTION: shadow price >= 99.9% of MaxShadowPrice counts as "at the cap"
VIOLATION_TOL_MW = 0.05                        # ASSUMPTION: same threshold the earlier n1-build.py used for ViolatedMW
TOP_N = 25                                     # brief: top 25 constraints


def md(df, floatfmt=2):
    """Minimal markdown table (tabulate is not installed)."""
    cols = list(df.columns)
    out = ["| " + " | ".join(map(str, cols)) + " |", "|" + "|".join("---" for _ in cols) + "|"]
    for r in df.itertuples(index=False):
        out.append("| " + " | ".join(f"{v:,.{floatfmt}f}" if isinstance(v, (float, np.floating)) else str(v) for v in r) + " |")
    return "\n".join(out)


def _ts_unique(path, col):
    return np.sort(pc.unique(pq.read_table(path, columns=[col]).column(0)).to_numpy(zero_copy_only=False)
                   .astype("datetime64[us]").astype("int64"))


def coverage_table():
    cov = ep.coverage()
    cpt = lambda x: pd.Timestamp(x).tz_convert(ep.CPT).strftime("%Y-%m-%d %H:%M") if x is not None else ""
    cov["first_cpt"], cov["last_cpt"] = cov["first"].map(cpt), cov["last"].map(cpt)
    return cov[["table", "rows", "first_cpt", "last_cpt", "mb"]]


def counts():
    sp = pd.read_csv(REPO_DATA / "settlement_points.csv", dtype=str, keep_default_na=False)
    out = {}
    f = DERIVED / "lmp_bus.parquet"
    if f.exists():
        buses = pc.unique(pq.read_table(f, columns=["bus"]).column(0)).to_pylist()
        out["buses in lmp_bus"] = len(buses)
        out["of which in settlement_points.csv"] = len(set(buses) & set(sp["ELECTRICAL_BUS"]))
        out["mapped buses never priced in lmp_bus"] = len(set(sp["ELECTRICAL_BUS"]) - set(buses))
    spp = pd.read_parquet(DERIVED / "spp_node.parquet", columns=["settlement_point", "sp_type"]).drop_duplicates()
    for t, n in spp["sp_type"].value_counts().items():
        out[f"settlement points of type {t} in spp_node"] = int(n)
    out["resource nodes in NP4-160 (current model)"] = int((sp["RESOURCE_NODE"].replace("", np.nan).dropna().nunique()))
    sh = pd.read_parquet(DERIVED / "shadow_sced.parquet", columns=["cid", "shadow_price", "source"])
    for src, g in sh.groupby("source"):
        out[f"distinct constraints listed ({src})"] = int(g["cid"].nunique())
        out[f"distinct binding constraints, shadow price > 0 ({src})"] = int(g.loc[g["shadow_price"] > 0, "cid"].nunique())
    out["distinct binding constraints (all sources)"] = int(sh.loc[sh["shadow_price"] > 0, "cid"].nunique())
    d = pd.read_parquet(DERIVED / "shadow_dam.parquet", columns=["cid", "shadow_price"])
    out["distinct binding DAM constraints (NP4-191)"] = int(d.loc[d["shadow_price"] > 0, "cid"].nunique())
    return out


def join_check():
    """Do SCED timestamps line up across lmp_bus, lambda and shadow_sced?"""
    lam = pd.read_parquet(DERIVED / "lambda.parquet", columns=["sced_ts"])["sced_ts"]
    sh = pd.read_parquet(DERIVED / "shadow_sced.parquet", columns=["sced_ts", "source"])
    sh = sh[sh["source"] == "mis"]["sced_ts"]
    L = set(lam.astype("int64"))
    S = set(sh.astype("int64"))
    B = set(_ts_unique(DERIVED / "lmp_bus.parquet", "sced_ts"))
    lo = min(B | L)                                   # shadow rows before the first lambda/bus run cannot match by construction
    S_in = {t for t in S if t >= lo}
    rows = [("lambda runs", len(L)), ("lmp_bus runs", len(B)), ("shadow_sced runs (MIS)", len(S)),
            ("shadow_sced runs inside the lambda/lmp_bus window", len(S_in)),
            ("lmp_bus runs that are in lambda", len(B & L)), ("lambda runs that are in lmp_bus", len(L & B)),
            ("shadow runs (in window) that are in lambda", len(S_in & L)), ("shadow runs (in window) that are in lmp_bus", len(S_in & B))]
    rate = lambda a, b: f"{100 * a / b:.1f}%" if b else "n/a"
    out = pd.DataFrame(rows, columns=["item", "count"])
    out["match rate"] = [""] * 4 + [rate(len(B & L), len(B)), rate(len(L & B), len(L)), rate(len(S_in & L), len(S_in)), rate(len(S_in & B), len(S_in))]
    return out, dict(L=L, S=S, B=B, S_in=S_in)


def binding_share(sets):
    """Share of SCED runs with at least one binding constraint (shadow price > 0), over the runs we have a full list of."""
    sh = pd.read_parquet(DERIVED / "shadow_sced.parquet", columns=["sced_ts", "shadow_price", "source"])
    sh = sh[sh["source"] == "mis"]
    bind = set(sh.loc[sh["shadow_price"] > 0, "sced_ts"].astype("int64"))
    hi = max(sets["S"])                                   # the newest NP6-86 file is posted ~1 h behind the price files
    window = {t for t in (sets["L"] | sets["B"]) if t <= hi}
    share = len(window & bind) / len(window)
    listed = len(set(sh["sced_ts"].astype("int64")) & window) / len(window)
    return share, listed, len(window)


def top_constraints(n=TOP_N):
    """Top-n binding constraints in the MIS window by sum(shadow_price) x run hours (ASSUMPTION: 5 min per run)."""
    sh = pd.read_parquet(DERIVED / "shadow_sced.parquet")
    sh = sh[sh["source"] == "mis"]
    b = sh[sh["shadow_price"] > 0].copy()
    b["at_cap"] = b["shadow_price"] >= b["max_shadow_price"] * CAP_TOL      # priced at the constraint's price cap: SCED could not relieve it
    b["violated"] = b["violated_mw"] > VIOLATION_TOL_MW
    mode = lambda s: s.mode().iat[0] if len(s.mode()) else ""
    g = b.groupby("cid").agg(constraint=("constraint_name", "first"), contingency=("contingency_name", "first"),
                             from_station=("from_station", mode), to_station=("to_station", mode),
                             from_kv=("from_kv", "max"), to_kv=("to_kv", "max"), element=("element_type", mode),
                             limit_mw=("limit", "median"), binding_runs=("shadow_price", "size"),
                             mean_sp=("shadow_price", "mean"), max_sp=("shadow_price", "max"),
                             cap=("max_shadow_price", "max"), at_cap_runs=("at_cap", "sum"),
                             violated_runs=("violated", "sum"), sum_sp=("shadow_price", "sum"))
    g["at_cap_pct"] = 100 * g["at_cap_runs"] / g["binding_runs"]
    g["hours_binding"] = g["binding_runs"] * SCED_INTERVAL_H
    g["sp_x_hours"] = g["sum_sp"] * SCED_INTERVAL_H           # $/MW of relief summed over the window
    g["share_pct"] = 100 * g["sp_x_hours"] / g["sp_x_hours"].sum()
    g = g.sort_values("sp_x_hours", ascending=False)
    top = g.head(n).reset_index()
    top.insert(0, "rank", range(1, len(top) + 1))
    return top, g


def lmp_vs_lambda(sets):
    """Stream lmp_bus by row group and compare LMP with system lambda.  Returns summary rows and a per-bus table."""
    lam = pd.read_parquet(DERIVED / "lambda.parquet", columns=["sced_ts", "lambda"])
    lam_t = lam["sced_ts"].astype("int64").to_numpy()
    order = np.argsort(lam_t)
    lam_t, lam_v = lam_t[order], lam["lambda"].to_numpy()[order]
    sh = pd.read_parquet(DERIVED / "shadow_sced.parquet", columns=["sced_ts", "shadow_price", "source"])
    sh = sh[sh["source"] == "mis"]
    bind_t = np.sort(sh.loc[sh["shadow_price"] > 0, "sced_ts"].astype("int64").unique())
    hi = max(sets["S"])                                   # runs after the last NP6-86 file have no constraint list yet
    pf = pq.ParquetFile(DERIVED / "lmp_bus.parquet")
    buses = None
    flat_n = np.zeros(0)
    acc = dict(free_rows=0, free_flat=0, free_absmax=0.0, free_abssum=0.0, bind_rows=0, bind_flat=0, nolam=0)
    for rg in range(pf.num_row_groups):
        df = pf.read_row_group(rg).to_pandas()
        if buses is None:
            buses = pd.Index(df["bus"].unique())
            flat_n, runs_n = np.zeros(len(buses)), np.zeros(len(buses))
        t = df["sced_ts"].astype("int64").to_numpy()
        i = np.clip(np.searchsorted(lam_t, t), 0, len(lam_t) - 1)
        ok = (lam_t[i] == t) & (t <= hi)
        acc["nolam"] += int((lam_t[i] != t).sum())
        acc["after_last_shadow"] = acc.get("after_last_shadow", 0) + int((t > hi).sum())
        d = np.abs(df["lmp"].to_numpy().astype("float64") - lam_v[i])
        binding = np.isin(t, bind_t)
        use = ok & ~binding
        acc["free_rows"] += int(use.sum())
        acc["free_flat"] += int((d[use] <= FLAT_TOL).sum())
        acc["free_absmax"] = max(acc["free_absmax"], float(d[use].max()) if use.any() else 0.0)
        acc["free_abssum"] += float(d[use].sum())
        ub = ok & binding
        acc["bind_rows"] += int(ub.sum())
        acc["bind_flat"] += int((d[ub] <= FLAT_TOL).sum())
        for thr in SEP_THRESHOLDS:
            acc[f"bind_gt{thr}"] = acc.get(f"bind_gt{thr}", 0) + int((d[ub] > thr).sum())
        code = buses.get_indexer(df["bus"])
        m = ub & (code >= 0)
        runs_n += np.bincount(code[m], minlength=len(buses))
        flat_n += np.bincount(code[m & (d <= FLAT_TOL)], minlength=len(buses))
    per_bus = pd.DataFrame({"bus": buses, "runs_binding": runs_n, "flat_share": flat_n / np.maximum(runs_n, 1)})
    return acc, per_bus


def spp_identity(points=("HB_NORTH", "LZ_HOUSTON", "LZ_WEST", "ALGOD_ALL_RN", "7RNCHSLR_ALL", "ADL_RN")):
    """Check SPP(15 min) = time-weighted mean of SCED LMP over the interval + time-weighted mean of RTRDPA (the real-time
    reliability deployment price adder).  Uses lmp_node (NP6-788), price_adders (NP6-323) and spp_node (NP6-905)."""
    spp = pd.read_parquet(DERIVED / "spp_node.parquet")
    spp = spp[spp["settlement_point"].isin(points) & ~spp["dst_rep"]]
    ln = pd.read_parquet(DERIVED / "lmp_node.parquet")
    pa_ = pd.read_parquet(DERIVED / "price_adders.parquet", columns=["sced_ts", "rtrdpa"])
    rows = []
    for sp_, g in ln[ln["settlement_point"].isin(points)].groupby("settlement_point"):
        g = g.merge(pa_, on="sced_ts", how="left").sort_values("sced_ts").reset_index(drop=True)
        g["end"] = g["sced_ts"].shift(-1)
        g = g.dropna(subset=["end"])
        t0, t1 = g["sced_ts"].min(), g["end"].max()
        starts = g["sced_ts"].to_numpy()
        for st, price in spp[spp["settlement_point"] == sp_].set_index("interval_start_utc")["price"].items():
            en = st + pd.Timedelta(minutes=15)
            if st < t0 or en > t1:
                continue
            w = g[(g["end"] > st) & (g["sced_ts"] < en)]
            ov = (np.minimum(w["end"], en) - np.maximum(w["sced_ts"], st)).dt.total_seconds()
            rows.append((sp_, price, (w["lmp"] * ov).sum() / ov.sum(), (w["rtrdpa"] * ov).sum() / ov.sum()))
    o = pd.DataFrame(rows, columns=["point", "spp", "lmp_avg", "adder_avg"])
    o["spp_minus_lmp"] = o["spp"] - o["lmp_avg"]
    o["residual"] = o["spp"] - o["lmp_avg"] - o["adder_avg"]
    return o.groupby("point").agg(intervals=("spp", "size"), mean_spp_minus_lmp=("spp_minus_lmp", "mean"),
                                  max_spp_minus_lmp=("spp_minus_lmp", "max"), mean_adder=("adder_avg", "mean"),
                                  median_abs_residual=("residual", lambda x: x.abs().median()),
                                  p99_abs_residual=("residual", lambda x: x.abs().quantile(0.99)),
                                  intervals_residual_over_1c=("residual", lambda x: int((x.abs() > 0.01).sum())),
                                  max_abs_residual=("residual", lambda x: x.abs().max())).reset_index()


def as_summary(since="2025-12-05"):
    """Mean / p95 / max MCPC ($/MW-h) per market and product since RTC+B went live (5 Dec 2025)."""
    d = pd.read_parquet(DERIVED / "as_prices.parquet", columns=["market", "product", "date_cpt", "mcpc", "dst_rep"])
    d = d[(d["date_cpt"] >= since) & ~d["dst_rep"]]
    g = d.groupby(["market", "product"])["mcpc"].agg(rows="size", mean="mean", p95=lambda x: x.quantile(0.95), max="max").reset_index()
    return g


def as_dam_by_year(first_year=2022):
    """Mean DAM MCPC ($/MW-h) by year and product: shows how far AS prices have fallen as storage came in."""
    d = pd.read_parquet(DERIVED / "as_prices.parquet", columns=["market", "product", "date_cpt", "mcpc", "dst_rep"])
    d = d[(d["market"] == "DAM") & ~d["dst_rep"] & (d["date_cpt"] >= f"{first_year}-01-01")]
    d["year"] = d["date_cpt"].str[:4]
    t = d.pivot_table(index="year", columns="product", values="mcpc", aggfunc="mean").reset_index()
    return t


def report(log=print):
    out = []
    P = out.append
    P("## Coverage\n" + md(coverage_table()))
    P("## Counts\n" + md(pd.DataFrame(list(counts().items()), columns=["item", "count"])))
    jc, sets = join_check()
    P("## SCED timestamp joins\n" + md(jc))
    share, listed, n = binding_share(sets)
    P(f"## Binding share\nSCED runs with at least one binding constraint (shadow price > 0): **{100 * share:.1f}%** of {n} runs; "
      f"{100 * listed:.1f}% of runs list any constraint at all (binding or near-binding).")
    top, g = top_constraints()
    show = top[["rank", "constraint", "contingency", "from_station", "to_station", "from_kv", "to_kv", "element", "limit_mw",
                "binding_runs", "hours_binding", "mean_sp", "max_sp", "at_cap_pct", "sp_x_hours", "share_pct"]]
    P(f"## Top {TOP_N} constraints (MIS window)\n" + md(show))
    P("## Mapping models vs priced buses\n" + md(ep.check_model_vs_prices()))
    P("## Ancillary-service prices since RTC+B (5 Dec 2025)\n" + md(as_summary()))
    P("## DAM ancillary prices by year\n" + md(as_dam_by_year()))
    P("## SPP (15 min) = mean SCED LMP + mean RTRDPA\n" + md(spp_identity(), 4))
    acc, pb = lmp_vs_lambda(sets)
    fr, br = acc["free_rows"], acc["bind_rows"]
    sep = ", ".join(f"> ${t}: {100 * acc.get(f'bind_gt{t}', 0) / max(br, 1):.1f}%" for t in SEP_THRESHOLDS)
    P("## LMP vs lambda\n"
      f"- Runs with no binding constraint: {fr:,} bus-rows, {100 * acc['free_flat'] / max(fr, 1):.3f}% within {FLAT_TOL} $/MWh of lambda; "
      f"mean |LMP-lambda| {acc['free_abssum'] / max(fr, 1):.5f}, max {acc['free_absmax']:.3f}.\n"
      f"- Runs with a binding constraint: {br:,} bus-rows, {100 * acc['bind_flat'] / max(br, 1):.1f}% of them still equal lambda; "
      f"share separated from lambda by {sep}.\n"
      f"- Bus-rows with no lambda for their timestamp: {acc['nolam']:,}; bus-rows after the last NP6-86 file (excluded): "
      f"{acc.get('after_last_shadow', 0):,}.\n"
      f"- Buses that equal lambda in every binding run: {(pb['flat_share'] >= 1).sum():,} of {len(pb):,}; "
      f"buses separated from lambda in over half of binding runs: {(pb['flat_share'] < 0.5).sum():,}.")
    text = "\n\n".join(out)
    log(text)
    return text, dict(top=top, constraints=g, per_bus=pb, join=jc, acc=acc)


if __name__ == "__main__":
    report()
