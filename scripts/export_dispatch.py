#!/usr/bin/env python3
"""Export the ERCOT battery dispatch platform (dispatch/) to the story app's Dispatch page: ui/data/dispatch/index.json.

    <python with numpy> scripts/export_dispatch.py          (repo Python: ~/hb-overnight/.venv/Scripts/python.exe)

Reads (never writes) what the platform produced, preferring the machine-readable file over the prose note:
  dispatch/reports/case_study/*.csv, summary.json          the public case study (PAD1_ESS_ESR1, 1 Jul to 11 Aug 2026)
  $DISPATCH_I1_RUN  (default ../research-data/dispatch_i1_live/final-run)
      replay_runtime/{audit,ledger,forecasts}               I1's replay: 96 paper decisions, 9 to 10 Oct 2026
  $DISPATCH_HOME    (default ../research-data/dispatch)
      lake/catalog.jsonl                                    the publish time of every raw ERCOT file
      mlflow/mlflow.db                                      the model registry (opened read-only with sqlite3)
      runtime/audit                                         the real audit log (a fail-closed event and its recovery)
  dispatch/dispatch/{config.py,guard/guard.py,monitoring/checks.py,service/cycle.py,execution/audit.py}
                                                            named constants and reason codes, read with `ast`, never imported
  dispatch/README.md, dispatch/notes/*.md                   gate statuses and the few figures that exist only in prose
                                                            (found by pattern; the export stops if a pattern no longer matches)

Parquet is not read: the realised-price frames and the model's holdout tables are parquet-only, so the money of the replayed day
(paper, backtest, shadow, perfect foresight) is the one set of figures taken from notes/integration.md, by pattern.
Writes one file in the sim.contracts envelope (schema hb.dispatch.v1). Headline numbers are labelled dicts; bulk arrays are labelled once
in `series`. Deterministic: the same inputs give byte-identical output. No language model produces any number here: the forecaster
(LightGBM) forecasts prices, the LP chooses, the guard checks, and this script only reads and sums.
"""
import ast
import bisect
import csv
import datetime as dt
import hashlib
import json
import os
import re
import sqlite3
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from sim.contracts import envelope, labelled, write_json  # noqa: E402

DISP = ROOT / "dispatch"
PKG = DISP / "dispatch"
CASE = DISP / "reports" / "case_study"
NOTES = DISP / "notes"
RESEARCH = ROOT.parent / "research-data"
HOME = Path(os.environ.get("DISPATCH_HOME", str(RESEARCH / "dispatch")))
RUN = Path(os.environ.get("DISPATCH_I1_RUN", str(RESEARCH / "dispatch_i1_live" / "final-run")))
OUT = ROOT / "ui" / "data" / "dispatch" / "index.json"

PRODUCTS = ["REGUP", "REGDN", "RRS", "ECRS", "NSPIN"]
TOPN = 100                    # the "largest hundred intervals" of the concentration sentence
GENESIS = "0" * 64

C_CASE = "DERIVED: the case study (dispatch/reports/case_study), the asset's REAL telemetry and day-ahead awards valued at hub proxy prices"
C_REPLAY = "SIM: I1's replay of 96 paper decisions on the real stack, 9 to 10 Oct 2026 (research-data/dispatch_i1_live/final-run/replay_runtime)"
C_CFG = "dispatch/dispatch/config.py"


# ---------------------------------------------------------------------------------------------------------- small helpers
def r1(x):
    return round(float(x), 1)


def r2(x):
    return round(float(x), 2)


def r3(x):
    return round(float(x), 3)


def L(v, label, cite=None, nd=None):
    """A labelled headline number; floats are rounded so the file is stable."""
    if isinstance(v, (float, np.floating)):
        v = int(round(float(v))) if nd == 0 else round(float(v), nd) if nd is not None else float(v)
    elif isinstance(v, (np.integer,)):
        v = int(v)
    return labelled(v, label, cite)


def read_text(p):
    return Path(p).read_text(encoding="utf-8")


def read_jsonl(p):
    out = []
    for line in read_text(p).splitlines():
        if line.strip():
            out.append(json.loads(line))
    return out


def read_csv(p):
    with open(p, encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def sha256_file(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def nearest_rank(a, q):
    """The q-th percentile by the nearest-rank rule (what a histogram-based p99 reports): the ceil(q n)-th smallest value."""
    v = np.sort(np.asarray(a, dtype=float))
    return float(v[int(np.ceil(q / 100 * len(v))) - 1])


def num(s):
    """'1,285 s' / '**2.07%**' / '$1,333.27' / '-3.6e-12' -> float."""
    m = re.search(r"-?\d[\d,]*\.?\d*(?:e-?\d+)?", str(s).replace("−", "-"))
    if not m:
        raise ValueError(f"no number in {s!r}")
    return float(m.group(0).replace(",", ""))


def md_table(text, header_has, nth=0):
    """Rows (list of cell strings) of the nth markdown table whose header row contains `header_has`; header and rule row dropped."""
    lines = text.splitlines()
    found = -1
    for i, ln in enumerate(lines):
        if ln.lstrip().startswith("|") and header_has in ln:
            found += 1
            if found == nth:
                rows = []
                j = i + 2
                while j < len(lines) and lines[j].lstrip().startswith("|"):
                    cells = [c.strip() for c in lines[j].strip().strip("|").split("|")]
                    rows.append(cells)
                    j += 1
                return rows
    raise SystemExit(f"export_dispatch: no markdown table with a header containing {header_has!r} (the note changed?)")


def find(pattern, text, what, flags=0):
    m = re.search(pattern, text, flags)
    if not m:
        raise SystemExit(f"export_dispatch: pattern for {what} no longer matches ({pattern!r})")
    return m


def eval_node(n, env):
    """A small, safe evaluator for module-level constant expressions (numbers, strings, tuples, dicts, sets, frozenset(...), + - * /)."""
    if isinstance(n, ast.Constant):
        return n.value
    if isinstance(n, ast.Name):
        if n.id in env:
            return env[n.id]
        raise KeyError(n.id)
    if isinstance(n, ast.UnaryOp) and isinstance(n.op, ast.USub):
        return -eval_node(n.operand, env)
    if isinstance(n, ast.BinOp):
        a, b = eval_node(n.left, env), eval_node(n.right, env)
        return {ast.Add: lambda: a + b, ast.Sub: lambda: a - b, ast.Mult: lambda: a * b, ast.Div: lambda: a / b,
                ast.Pow: lambda: a ** b}[type(n.op)]()
    if isinstance(n, ast.Tuple):
        return tuple(eval_node(e, env) for e in n.elts)
    if isinstance(n, ast.List):
        return [eval_node(e, env) for e in n.elts]
    if isinstance(n, ast.Set):
        return {eval_node(e, env) for e in n.elts}
    if isinstance(n, ast.Dict):
        return {eval_node(k, env): eval_node(v, env) for k, v in zip(n.keys, n.values)}
    if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "frozenset" and len(n.args) == 1:
        return frozenset(eval_node(n.args[0], env))
    raise ValueError(type(n).__name__)


def consts(path, env=None):
    """Module-level NAME = <constant expression> assignments of a Python file, evaluated with `ast` (the file is never imported)."""
    env = dict(env or {})
    tree = ast.parse(read_text(path))
    out = {}
    for st in tree.body:
        if isinstance(st, ast.Assign) and len(st.targets) == 1 and isinstance(st.targets[0], ast.Name):
            try:
                val = eval_node(st.value, {**env, **out})
            except (KeyError, ValueError, TypeError):
                continue
            out[st.targets[0].id] = val
    return out


def parse_utc(s):
    return dt.datetime.fromisoformat(s.replace("Z", "+00:00"))


# ---------------------------------------------------------------------------------------------------------- audit chain
def canonical(rec):
    return json.dumps(rec, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False).encode("ascii")


def record_hash(rec):
    return hashlib.sha256(canonical({k: v for k, v in rec.items() if k != "hash"})).hexdigest()


def verify_chain(records):
    """(ok, first bad 1-based line, reason): the chain of audit.py, recomputed here with hashlib."""
    prev = GENESIS
    for i, r in enumerate(records):
        try:
            h = record_hash(r)
        except ValueError:
            return False, i + 1, "content cannot be hashed"
        if r.get("seq") != i:
            return False, i + 1, "seq out of order"
        if r.get("prev_hash") != prev:
            return False, i + 1, "prev_hash does not match the record before"
        if h != r.get("hash"):
            return False, i + 1, "hash does not match the content"
        prev = r["hash"]
    return True, None, "ok"


def read_audit(dirpath):
    recs = []
    for f in sorted(Path(dirpath).glob("audit-*.jsonl")):
        recs += read_jsonl(f)
    return recs


# ---------------------------------------------------------------------------------------------------------- 1. one cycle
def build_cycle(cfg, assets):
    rt = RUN / "replay_runtime"
    audit = read_audit(rt / "audit")
    ok, bad, why = verify_chain(audit)
    assert ok, f"the replay audit chain does not verify at line {bad}: {why}"
    by = {k: [r for r in audit if r["type"] == k] for k in ("signal", "guard_decision", "setpoint", "cycle")}
    n = len(by["signal"])
    assert all(len(v) == n for v in by.values()) and n == 96, {k: len(v) for k, v in by.items()}
    ledger = [r for r in read_jsonl(rt / "ledger" / "ledger-PILOT_HB_NORTH.jsonl") if r["type"] == "setpoint"]
    fcs = []
    for f in sorted((rt / "forecasts").glob("forecasts-*.jsonl")):
        fcs += read_jsonl(f)
    fcs.sort(key=lambda r: r["issued_utc"])
    assert len(ledger) == len(fcs) == n
    for i in range(n):
        pid = by["signal"][i]["proposed"]["plan_id"]
        assert by["guard_decision"][i]["plan_id"] == by["cycle"][i]["plan_id"] == ledger[i]["plan_id"] == pid, i
        assert fcs[i]["issued_utc"] == by["signal"][i]["now_utc"] == ledger[i]["issued_utc"], i
    # the newest input each decision saw: the catalog record published at (decision time - feature age). Almost always the 15-minute
    # price file NP6-905-CD; once a day it is the day-ahead results (NP4-188-CD, which the served model reads).
    cat = read_jsonl(HOME / "lake" / "catalog.jsonl")
    cat.sort(key=lambda r: r["published_utc"])
    pubs = [parse_utc(r["published_utc"]).timestamp() for r in cat]
    prio = ["NP6-905-CD", "NP4-188-CD", "NP4-190-CD"]
    pub, stamp, rpt, fsha = [], [], [], []
    for s in by["signal"]:
        target = parse_utc(s["now_utc"]).timestamp() - s["feature_age_s"]
        j0 = bisect.bisect_left(pubs, target - 1.0)
        j1 = bisect.bisect_right(pubs, target + 1.0)
        hits = [cat[j] for j in range(j0, j1)]
        assert hits, f"no lake file was published at {s['now_utc']} minus {s['feature_age_s']} s"
        hits.sort(key=lambda r: prio.index(r["report_id"]) if r["report_id"] in prio else 99)
        rec = hits[0]
        pub.append(rec["published_utc"])
        rpt.append(rec["report_id"])
        m = re.search(r"SPPHLZNP6905_\d{8}_(\d{4})_csv", rec["path"])
        stamp.append(m.group(1) if m else "")
        fsha.append(rec["sha256"][:12])

    lat = np.array([c["latency_s"] for c in by["cycle"]])
    age = np.array([s["feature_age_s"] for s in by["signal"]])
    net = np.array([r["net_mw"] for r in ledger])
    dis = np.clip(net, 0, None) * 0.25
    chg = np.clip(-net, 0, None) * 0.25
    awards = {p: np.array([r["as_mw"][p] for r in ledger]) for p in PRODUCTS}
    accepted = sum(1 for g in by["guard_decision"] if g["accepted"])

    # the money of the replayed day exists only in the notes (the price frames are parquet)
    integ = read_text(NOTES / "integration.md")
    m1 = find(r"paper\s+([\d,]+\.\d+)\s*=\s*backtest\s+([\d,]+\.\d+),\s*slippage\s+([\d.]+)", integ, "paper = backtest")
    m2 = find(r"actual\s+([\d,]+\.\d+)\s+shadow\s+([\d,]+\.\d+)\s+perfect foresight\s+([\d,]+\.\d+)\s+gap\s+([\d,]+\.\d+)\s*=\s*"
              r"forecast\s+([\d,]+\.\d+)\s*\+\s*soc\s+([\d,]+\.\d+)\s*\+\s*ancillary\s+([\d,]+\.\d+)", integ, "replayed-day attribution")
    paper, backtest, slip = num(m1.group(1)), num(m1.group(2)), num(m1.group(3))
    a_act, a_sh, a_pf, a_gap, a_fc, a_soc, a_as = (num(m2.group(i)) for i in range(1, 8))
    C_NOTE = "SIM: paper ledger and D1b's backtest of the same 96 decisions, dispatch/notes/integration.md section 6 (the price frames are parquet-only)"

    model = by["signal"][0]["model_version"]
    assert all(s["model_version"] == model for s in by["signal"])
    spec = assets["assets"][0]
    summary = {
        "latencyP50": L(nearest_rank(lat, 50), "SIM", C_REPLAY + "; server-side latency_s of each cycle record, nearest rank", 3),
        "latencyP99": L(nearest_rank(lat, 99), "SIM", C_REPLAY + "; nearest rank: with 96 samples the p99 is the slowest cycle", 3),
        "latencyMax": L(lat.max(), "SIM", C_REPLAY, 3),
        "latencyTarget": L(cfg["CYCLE_P99_TARGET_S"], "ASSUMPTION", "PRD target, " + C_CFG + " CYCLE_P99_TARGET_S"),
        "headroom": L(cfg["CYCLE_P99_TARGET_S"] / nearest_rank(lat, 99), "DERIVED", "DERIVED: target ÷ p99", 0),
        "cycles": L(n, "SIM", C_REPLAY),
        "dispatched": L(sum(1 for c in by["cycle"] if c["status"] == "dispatched"), "SIM", C_REPLAY),
        "accepted": L(accepted, "SIM", C_REPLAY + "; guard_decision records with accepted = true"),
        "featureAgeMax": L(age.max(), "DERIVED", "DERIVED: decision time minus the publish time of the newest price file, the audit's feature_age_s", 0),
        "featureAgeLimit": L(cfg["MAX_FEATURE_AGE_S"], "ASSUMPTION", C_CFG + " MAX_FEATURE_AGE_S (D0: ERCOT posts files up to about 8.5 minutes late)"),
        "paper": L(paper, "SIM", C_NOTE),
        "backtest": L(backtest, "SIM", C_NOTE),
        "slippage": L(slip, "SIM", C_NOTE),
        "slippageLimit": L(cfg["SLIPPAGE_TOL"] * 100, "ASSUMPTION", C_CFG + " SLIPPAGE_TOL (Gate 3)", 1),
        "dayActual": L(a_act, "SIM", C_NOTE + "; /attribution for the paper asset"),
        "dayShadow": L(a_sh, "SIM", C_NOTE),
        "dayPf": L(a_pf, "SIM", C_NOTE),
        "dayGap": L(a_gap, "DERIVED", "DERIVED: perfect foresight minus actual, " + C_NOTE),
        "dayCapture": L(a_act / a_pf * 100, "DERIVED", "DERIVED: actual ÷ perfect foresight on the replayed day; one day is not a capture rate", 1),
        "dayAncillary": L(a_as, "DERIVED", C_NOTE),
        "dayForecast": L(a_fc, "DERIVED", C_NOTE),
        "dayDischargedMwh": L(dis.sum(), "DERIVED", "DERIVED: sum of discharge setpoints × 0.25 h over the 96 ledger lines", 1),
        "dayChargedMwh": L(chg.sum(), "DERIVED", "DERIVED: sum of charge setpoints × 0.25 h over the 96 ledger lines", 1),
        "auditRecords": L(len(audit), "DERIVED", "DERIVED: records in the replay audit log, each hash recomputed with SHA-256 by this exporter"),
        "powerMw": L(spec["power_mw"], "ASSUMPTION", "PROPOSED pilot size, dispatch/ops/assets.example.json"),
        "energyMwh": L(spec["energy_mwh"], "ASSUMPTION", "PROPOSED, dispatch/ops/assets.example.json"),
        "exposureCap": L(spec["exposure_cap_usd"], "ASSUMPTION", "dispatch/ops/assets.example.json exposure_cap_usd (the cap definition is an open PRD question)"),
        "eventTypes": L(len(extract_event_types()), "REAL", "dispatch/dispatch/execution/audit.py EVENT_TYPES"),
    }
    for p in PRODUCTS:
        assert all(abs(x) < 1e9 for x in awards[p])
    cyc = {
        "asset": spec["asset_id"], "hub": spec["settlement_point"], "modelVersion": str(model), "modelAlias": "paper",
        "n": n, "firstUtc": by["signal"][0]["now_utc"], "lastUtc": by["signal"][-1]["now_utc"],
        "summary": summary,
        "t": [s["now_utc"] for s in by["signal"]],
        "age": [r1(a) for a in age],
        "pub": pub, "file": stamp, "rpt": rpt, "fileSha": fsha,
        "net": [r3(x) for x in net],
        "soc0": [r3(r["soc_start_mwh"]) for r in ledger], "soc1": [r3(r["soc_end_mwh"]) for r in ledger],
        "award": {p: [r3(x) for x in awards[p]] for p in PRODUCTS},
        "basis": [r1(r["cost_basis_end_usd"]) for r in ledger],
        "ref": [r1(r["ref_price_usd_per_mwh"]) for r in ledger],
        "obj": [r1(s["plan"]["objective_usd"]) for s in by["signal"]],
        "solver": by["signal"][0]["plan"]["solver"], "status": [s["plan"]["status"] for s in by["signal"]],
        "lat": [r3(x) for x in lat],
        "plan": [s["proposed"]["plan_id"] for s in by["signal"]],
        "exposure": [r1(g["state"]["exposure_usd"]) for g in by["guard_decision"]],
        "ok": [bool(g["accepted"]) for g in by["guard_decision"]],
        "reasons": [g["reasons"] for g in by["guard_decision"]],
        "seq": [s["seq"] for s in by["signal"]], "ledgerSeq": [r["seq"] for r in ledger],
        "hash": {"signal": [s["hash"] for s in by["signal"]], "guard": [g["hash"] for g in by["guard_decision"]],
                 "setpoint": [s["hash"] for s in by["setpoint"]], "cycle": [c["hash"] for c in by["cycle"]],
                 "prev": [s["prev_hash"] for s in by["signal"]]},
        "featuresSha": [s["features_sha256"][:12] for s in by["signal"]],
        "fan": {k: [[r1(x) for x in f[k]] for f in fcs] for k in ("p10", "p50", "p90")},
        "prof": [[r1(x) for x in f["rt_prof7_med"]] for f in fcs],
        "fanStart": [f["start_utc"] for f in fcs],
    }
    return cyc, audit, by


def extract_event_types():
    c = consts(PKG / "execution" / "audit.py")
    return list(c["EVENT_TYPES"])


# ---------------------------------------------------------------------------------------------------------- 2. what it earned
def build_earned():
    s = json.loads(read_text(CASE / "summary.json"))
    daily = read_csv(CASE / "attribution_daily.csv")
    inter = read_csv(CASE / "attribution_intervals.csv")
    top = read_csv(CASE / "top20_missed_intervals.csv")
    prox = read_csv(CASE / "proxy_sensitivity.csv")
    wear = read_csv(CASE / "wear_sensitivity.csv")
    pilots = read_csv(CASE / "pilot_selection.csv")
    scopes = read_csv(CASE / "scope_variants.csv")
    f = lambda rows, k: [float(r[k]) for r in rows]  # noqa: E731

    c = s["components_usd"]
    sh = s["components_share_of_gap"]
    stages = s["stage_totals_usd"]
    gap = s["gap_usd"]
    comp_sum = c["forecast_usd"] + c["soc_usd"] + c["as_usd"] + c["availability_usd"] + c["unexplained_usd"]
    assert abs(comp_sum - gap) < 0.01, (comp_sum, gap)
    # the daily file adds up to the headline, to the cent
    for k_csv, k_sum in (("actual_usd", "actual_usd"), ("shadow_usd", "shadow_usd"), ("pf_usd", "pf_usd"), ("gap_usd", "gap_usd")):
        assert abs(sum(f(daily, k_csv)) - s[k_sum]) < 0.01, k_csv
    assert len(daily) == 42 and len(inter) == 4032

    gaps = np.array(f(inter, "gap_usd"))
    srt = np.sort(gaps)[::-1]
    top20 = srt[:20].sum()
    top100 = srt[:TOPN].sum()
    assert abs(top20 - sum(f(top, "gap_usd"))) < 0.01
    n_neg = int((gaps < 0).sum())
    tot_gap = gaps.sum()

    # pilot funnel, counted from the selection table (rules were fixed before the choice)
    n_all = len(pilots)
    yes = lambda r, k: r[k] == "True"  # noqa: E731
    n_size = sum(1 for r in pilots if yes(r, "ok_size"))
    n_basis = sum(1 for r in pilots if yes(r, "ok_basis"))
    n_pass = sum(1 for r in pilots if yes(r, "passes_all"))
    finalists = [r for r in pilots if yes(r, "passes_all")]
    finalists.sort(key=lambda r: float(r["rmse"]))

    hs = s["meta"]["hub_stats"]
    dam = s["dam_proxy_check"]
    spec = s["spec"]
    sc_names = {"all_AS": "All five ancillary products", "no_regulation": "Regulation excluded", "energy_only": "Energy only"}
    sc_keys = ["all_AS", "no_regulation", "energy_only"]
    sc = []
    for k in sc_keys:
        v = s["variants"][k]
        row = next(r for r in scopes if r["scope"] == k)
        sc.append({
            "id": k, "name": sc_names[k], "desc": row["description"],
            "actual": L(v["actual_usd"], "DERIVED", C_CASE, 2), "pf": L(v["pf_usd"], "SIM", C_CASE + "; perfect-foresight LP", 2),
            "shadow": L(v["shadow_usd"], "SIM", C_CASE + "; the shadow optimizer (seasonal-naive forecast, hourly re-plan)", 2),
            "gap": L(v["gap_usd"], "DERIVED", C_CASE, 2),
            "captureActual": L(v["capture_actual"] * 100, "DERIVED", "DERIVED: actual ÷ perfect foresight", 1),
            "captureShadow": L(v["capture_shadow"] * 100, "DERIVED", "DERIVED: shadow ÷ perfect foresight", 1),
            "recovers": L(v["shadow_recovers_share_of_gap"] * 100, "DERIVED", "DERIVED: (shadow − actual) ÷ gap", 0),
            "as": L(v["components_usd"]["as_usd"], "DERIVED", C_CASE, 2),
            "position": L(float(row["position_usd"]), "DERIVED", "DERIVED: REAL day-ahead awards and prices from ERCOT's 60-day disclosure, settled at proxy real-time prices, in this scope", 2),
        })
    comps = [
        ("as", "Ancillary awards", "as_usd", "Revenue a price-taking operator earns by selling ancillary capacity the asset did not hold in real time."),
        ("forecast", "Forecast and decision", "forecast_usd", "The best response to the realised prices from the same start, awards and derates, minus what the asset did."),
        ("soc", "State-of-charge position", "soc_usd", "The cost of the charge the asset carried into each market day, against a free path."),
        ("availability", "Availability", "availability_usd", "Power derates and reduced state-of-charge limits the asset itself reported."),
        ("unexplained", "Unexplained", "unexplained_usd", "Data gaps and any difference to reported revenue. None was supplied, so this is zero by construction."),
    ]
    components = [{"id": k, "name": nm, "what": what,
                   "usd": L(c[ck], "DERIVED", C_CASE + "; sequential order: availability, awards, SOC, then the rest", 2),
                   "share": L(sh[ck] * 100, "DERIVED", "DERIVED: component ÷ gap", 1)} for k, nm, ck, what in comps]
    waterfall = [
        {"id": "pf", "name": "Perfect foresight", "v": L(stages["pf"], "SIM", C_CASE + "; the most any operator could earn with this battery", 0)},
        {"id": "availability", "name": "Derates", "v": L(stages["availability"], "DERIVED", C_CASE + "; after the asset's own derates", 0)},
        {"id": "as", "name": "Awards held", "v": L(stages["as"], "DERIVED", C_CASE + "; after the asset's real-time ancillary awards", 0)},
        {"id": "soc", "name": "Daily charge", "v": L(stages["soc"], "DERIVED", C_CASE + "; after the telemetered state of charge at each day's start", 0)},
        {"id": "actual", "name": "Actual", "v": L(s["actual_usd"], "DERIVED", C_CASE, 0)},
    ]
    summary = {
        "actual": L(s["actual_usd"], "DERIVED", C_CASE + "; not settled revenue", 2),
        "pf": L(s["pf_usd"], "SIM", C_CASE + "; perfect-foresight LP net of $20/MWh wear", 2),
        "shadow": L(s["shadow_usd"], "SIM", C_CASE + "; shadow optimizer", 2),
        "gap": L(gap, "DERIVED", C_CASE, 2),
        "captureActual": L(s["capture_actual"] * 100, "DERIVED", "DERIVED: actual ÷ perfect foresight, net of the wear charge", 1),
        "captureShadow": L(s["capture_shadow"] * 100, "DERIVED", "DERIVED: shadow ÷ perfect foresight", 1),
        "recovers": L(s["shadow_recovers_share_of_gap"] * 100, "DERIVED", "DERIVED: (shadow − actual) ÷ gap", 0),
        "energyActual": L(s["variants"]["energy_only"]["actual_usd"], "DERIVED", C_CASE + "; energy-only scope", 2),
        "energyShadow": L(s["variants"]["energy_only"]["shadow_usd"], "SIM", C_CASE + "; energy-only scope", 2),
        "position": L(s["position_usd"], "DERIVED", "DERIVED: REAL day-ahead awards and prices from ERCOT's 60-day disclosure, settled at proxy real-time prices", 2),
        "orderSpread": L(s["order_spread_usd"], "DERIVED", "DERIVED: largest difference between the sequential and the order-averaged (Shapley) split", 2),
        "orderSpreadShare": L(s["order_spread_share_of_gap"] * 100, "DERIVED", "DERIVED: order spread ÷ gap", 1),
        "top20n": L(20, "DERIVED", "DERIVED: rows of top20_missed_intervals.csv"),
        "top100n": L(TOPN, "DERIVED", "DERIVED: the largest intervals added for top100Share"),
        "top20": L(top20, "DERIVED", "DERIVED: the 20 largest per-interval gaps added", 2),
        "top20Share": L(top20 / tot_gap * 100, "DERIVED", "DERIVED: top 20 intervals ÷ gap", 1),
        "top100Share": L(top100 / tot_gap * 100, "DERIVED", "DERIVED: top 100 intervals ÷ gap", 1),
        "negIntervals": L(n_neg, "DERIVED", "DERIVED: intervals where the asset out-earned the perfect-foresight plan"),
        "intervals": L(len(inter), "REAL", "REAL: 15-minute intervals of ERCOT's 60-day disclosure, 42 operating days"),
        "days": L(len(daily), "REAL", "REAL: operating days in the study"),
        "unexplained": L(s["closure"] * 100, "DERIVED", "DERIVED: |sum of unexplained| ÷ |sum of gap|; zero by construction on public data", 1),
        "proxyStress": L(s["proxy_stress_closure"] * 100, "DERIVED", "DERIVED: the largest share of the gap the hub-for-node price proxy alone could move (worst of seven October basis days)", 1),
        "damStress": L(dam["d_actual_usd"] / gap * 100, "DERIVED", "DERIVED: the same-period day-ahead basis check, actual moved ÷ gap", 1),
        "powerMw": L(spec["power_mw"], "REAL", "REAL: maximum telemetered HSL in the disclosure", 0),
        "energyMwh": L(spec["energy_mwh"], "REAL", "REAL: maximum telemetered Maximum SOC", 0),
        "rte": L(spec["rte"], "DERIVED", "DERIVED: energy balance of the whole period, losses on charge", 3),
        "wear": L(spec["wear_usd_per_mwh"], "ASSUMPTION", "the research arena's wear cost per MWh discharged"),
        "auxMw": L(s["meta"]["aux_load_mw"], "DERIVED", "DERIVED: median telemetered output at a zero base point", 1),
        "basisRmse": L(hs["rmse"], "REAL", "REAL: node PAD1_ESS_RN minus hub HB_SOUTH, 717 fifteen-minute prices of the 7-day nodal window, 3 to 10 Oct 2026", 2),
        "basisBias": L(hs["bias"], "REAL", "REAL: same window", 2),
        "basisMae": L(hs["mae"], "REAL", "REAL: same window", 2),
        "basisCorr": L(hs["corr"], "REAL", "REAL: same window", 4),
        "basisN": L(int(hs["n"]), "REAL", "REAL: same window"),
        "damRmse": L(dam["rmse"], "REAL", "REAL: node minus hub day-ahead price at 1008 hours of the study period", 2),
        "nAll": L(n_all, "REAL", "REAL: energy storage resources in ERCOT's disclosure"),
        "nSize": L(n_size, "DERIVED", "DERIVED: counted from pilot_selection.csv, 10 to 100 MW"),
        "nBasis": L(n_basis, "DERIVED", "DERIVED: counted from pilot_selection.csv, node-minus-hub RMSE at most $5/MWh and absolute bias at most $2/MWh"),
        "nPass": L(n_pass, "DERIVED", "DERIVED: counted from pilot_selection.csv, pass every rule"),
    }
    return {
        "asset": s["asset"], "node": s["node"], "hub": s["hub"], "firstDay": s["first_day"], "lastDay": s["last_day"],
        "summary": summary,
        "components": components, "waterfall": waterfall, "scopes": sc,
        "days": {
            "date": [r["day"] for r in daily],
            "actual": [r2(x) for x in f(daily, "actual_usd")], "shadow": [r2(x) for x in f(daily, "shadow_usd")],
            "pf": [r2(x) for x in f(daily, "pf_usd")], "gap": [r2(x) for x in f(daily, "gap_usd")],
            "forecast": [r2(x) for x in f(daily, "forecast_usd")], "soc": [r2(x) for x in f(daily, "soc_usd")],
            "as": [r2(x) for x in f(daily, "as_usd")], "avail": [r2(x) for x in f(daily, "availability_usd")],
        },
        "top": [{
            "utc": r["interval_start_utc"].replace(" ", "T").replace("+00:00", "Z"), "gap": r2(float(r["gap_usd"])),
            "forecast": r2(float(r["forecast_usd"])), "as": r2(float(r["as_usd"])), "avail": r2(float(r["availability_usd"])),
            "soc": r2(float(r["soc_usd"])), "dominant": r["dominant"], "price": r2(float(r["rt_usd_per_mwh"])),
            "netActual": r1(float(r["net_actual_mw"])), "netPf": r1(float(r["net_pf_mw"])),
            "awardActual": r1(float(r["award_mw_actual"])), "awardPf": r1(float(r["award_mw_pf"])),
        } for r in top],
        "proxy": [{
            "day": r["basis_day"], "basis": r2(float(r["mean_basis"])), "actual": r0(float(r["d_actual_usd"])), "pf": r0(float(r["d_pf_usd"])),
            "shadow": r0(float(r["d_shadow_usd"])), "gap": r0(float(r["d_gap_usd"])),
        } for r in prox],
        "wear": [{
            "wear": int(float(r["wear_usd_per_mwh"])), "capture": r1(float(r["capture_actual"]) * 100), "pf": r0(float(r["pf_usd"])),
            "actual": r0(float(r["actual_usd"])), "gap": r0(float(r["gap_usd"])),
        } for r in wear],
        "pilots": [{
            "resource": r["resource"], "mw": r1(float(r["hsl_max_mw"])), "mwh": r1(float(r["max_soc_mwh"])), "node": r["node"], "hub": r["hub"],
            "rmse": r2(float(r["rmse"])), "bias": r2(float(r["bias"])), "corr": r3(float(r["corr"])), "cycles": r2(float(r["cycles_per_day"])),
            "award": r0(float(r["award_share"]) * 100),
        } for r in finalists],
        "pilotId": s["asset"],
    }


def r0(x):
    return int(round(float(x)))


# ---------------------------------------------------------------------------------------------------------- 3. safe by design
GUARD_GROUPS = [
    ("State and identity", ["halted", "model_version_missing", "asset_mismatch"],
     "A halted service, a setpoint with no model behind it, or one for another battery is refused before any physics is checked."),
    ("Sane numbers and time", ["non_finite", "malformed", "interval_invalid", "interval_out_of_window"],
     "A NaN, a wrong type, or an interval that is not the current or the next one stops the checks; arithmetic on a NaN would pass silently."),
    ("Fresh telemetry", ["soc_stale", "soc_telemetry_invalid"],
     "A state of charge older than one SCED interval, or outside the physical range, cannot be trusted to bound a setpoint."),
    ("Power", ["power_limit", "as_negative", "as_product_unknown", "as_up_power", "as_dn_power"],
     "Net power within the battery's rating, awards that are real products, and discharge plus up awards (or charge plus down awards) within the rating."),
    ("Energy to deliver", ["soc_above_max", "soc_below_min", "as_up_energy", "as_dn_headroom"],
     "The projected end-of-interval charge stays inside its bounds, and holds enough energy for every award it sold, and room to absorb a down award."),
    ("Exposure", ["exposure_cap"], "The cost basis of the energy in the battery after the trade may not exceed the cap. The least settled limit: a business decision."),
    ("The guard itself", ["guard_error"], "Any exception inside the guard is a rejection, never a pass."),
]


def build_safe(cfg, cycle_by, audit_replay):
    guard_py = PKG / "guard" / "guard.py"
    gc = consts(guard_py)
    reason_codes = list(gc["REASON_CODES"])
    grouped = [c for _, cs, _ in GUARD_GROUPS for c in cs]
    assert sorted(grouped) == sorted(reason_codes), ("the guard's reason codes changed", set(grouped) ^ set(reason_codes))

    d2 = read_text(NOTES / "d2.md")
    props = md_table(d2, "| Property | Examples |")
    # plain names, in the order of the table; each row is checked against a phrase of d2's own wording so a reordered table stops the export
    PROP_NAMES = [("every accepted setpoint", "Every accepted setpoint satisfies every limit; comfortable ones are accepted; breaches are rejected"),
                  ("a set halt flag", "A set halt flag always rejects"),
                  ("the halt flag is fail closed", "The halt flag fails closed for anything but a plain False"),
                  ("garbage", "Garbage input never raises and is never accepted"),
                  ("well-typed", "Well-typed NaN, infinity and huge numbers are rejected for a reason"),
                  ("tightening any", "Tightening any limit never turns a rejection into an acceptance")]
    assert len(props) == len(PROP_NAMES), "d2.md property table changed"
    for row, (key, _) in zip(props, PROP_NAMES):
        assert key in row[0].lower().replace("`", ""), f"d2.md property row {row[0]!r} no longer starts with {key!r}"
    prop_rows = [{"name": nm, "examples": int(num(p[1]))} for p, (_, nm) in zip(props, PROP_NAMES)]
    total_examples = sum(p["examples"] for p in prop_rows)
    n_guard_tests = int(num(find(r"(\d+) guard", d2, "guard test count").group(1)))
    i1 = read_text(NOTES / "integration.md")
    m = find(r"(\d+) acceptances,\s*([\d,]+) rejections,\s*(\d+) reason codes", i1, "generator reach")
    acc_n, rej_n, codes_n = int(num(m.group(1))), int(num(m.group(2))), int(num(m.group(3)))
    C_D2 = "dispatch/notes/d2.md section 3 (tests/test_guard_props.py at 20,000 examples per main property)"

    # fail-closed paths: the status and reason codes are checked against d2's table and the cycle's own constants
    fc_table = md_table(d2, "| Condition | status | reasons |")
    cyc_consts = consts(PKG / "service" / "cycle.py")
    cyc_codes = {v for k, v in cyc_consts.items() if isinstance(v, str) and k.isupper() and re.fullmatch(r"[a-z_]+", v)}
    table_codes = set()
    for row in fc_table:
        for tok in re.split(r"[,/ ]+", row[2]):
            if tok:
                table_codes.add(tok)
    fail_paths = [
        ("Redis down or the halt flag set", "halted", ["halted"], "An unreachable or unreadable flag counts as halted. A kill that lands mid-cycle stops it before it executes."),
        ("Features missing", "stale", ["features_missing"], "No features for the settlement point, or an empty frame."),
        ("Features too old", "stale", ["features_stale"], "The newest input is older than the age limit; ERCOT's files are late, or the feed stopped."),
        ("No model", "error", ["no_model", "model_stale"], "Nothing holds the paper alias, or the alias moved after the model was loaded."),
        ("Forecast failed", "error", ["forecast_failed", "forecast_invalid", "forecast_leak"], "The forecaster raised, returned a NaN, or used a feature published after the decision."),
        ("LP not optimal", "error", ["plan_not_optimal", "optimizer_failed", "optimizer_unavailable"], "Infeasible, inaccurate or failed solves are not dispatched; the cycle idles."),
        ("Guard rejects", "rejected", ["power_limit", "soc_below_min", "exposure_cap"], "Any one reason code of the guard: the setpoint is not edited, it is dropped."),
        ("Audit or ledger refuses", "error", ["audit_failed", "ledger_refused"], "No audit record means no execution; the ledger re-checks the limits itself."),
    ]
    for _, _, codes, _ in fail_paths:
        for c in codes:
            assert c in table_codes or c in cyc_codes or c in reason_codes, f"unknown reason code {c}"

    # the real audit log: one real fail-closed event and its recovery
    real = read_audit(HOME / "runtime" / "audit")
    ok_real, bad_real, _ = verify_chain(real)
    errs = [r for r in real if r["type"] == "cycle" and r["status"] != "dispatched"]
    real_event = None
    if errs:
        e = errs[0]
        after = next(r for r in real if r["type"] == "cycle" and r["status"] == "dispatched" and r["seq"] > e["seq"])
        real_event = {
            "t": e["now_utc"], "status": e["status"], "reasons": e["reasons"], "error": e["detail"].get("error", ""),
            "latency": r3(e["latency_s"]), "age": r1(e["feature_age_s"]), "seq": e["seq"], "hash": e["hash"],
            "thenT": after["now_utc"], "thenStatus": after["status"], "thenModel": after["model_version"], "thenLatency": r3(after["latency_s"]),
        }

    # tamper demonstration, computed: edit one number in one replay record and verify again
    tamper_i = 99
    tampered = [dict(r) for r in audit_replay]
    victim = json.loads(json.dumps(tampered[tamper_i]))
    assert victim["type"] == "guard_decision"
    victim["setpoint"]["net_mw"] = victim["setpoint"]["net_mw"] + 1.0       # someone edits the setpoint the guard approved
    tampered[tamper_i] = victim
    okt, bad_line, why_t = verify_chain(tampered)
    assert not okt

    chain = []
    for j in range(tamper_i - 2, tamper_i + 3):
        rec = {"i": j, "type": tampered[j]["type"], "seq": tampered[j]["seq"], "hash": tampered[j]["hash"][:12], "prev": tampered[j]["prev_hash"][:12]}
        if j == tamper_i:
            rec["recomputed"] = record_hash(tampered[j])[:12]
        chain.append(rec)

    # monitoring numbers
    checks = PKG / "monitoring" / "checks.py"
    kc = consts(checks)
    cadence, extra, halt_set = kc["REPORT_CADENCE_S"], kc["REPORT_EXTRA_ALLOWANCE_S"], kc["HALT_ELIGIBLE_REPORTS"]
    fresh = cfg["DATA_FRESHNESS_TARGET_S"]
    integ = read_text(NOTES / "integration.md")
    allow = md_table(integ, "| Report | cadence | extra | allowance |")
    reports = []
    for row in allow:
        rid = re.match(r"(NP\d-\d+-CD)", row[0]).group(1)
        name = re.search(r"\(([^)]*)\)", row[0]).group(1)
        assert rid in cadence
        allowance = cadence[rid] + extra.get(rid, 0) + fresh
        reports.append({"id": rid, "name": name, "halts": rid in halt_set,
                        "allowance": L(allowance, "DERIVED", "DERIVED: cadence + extra allowance + data freshness target, dispatch/dispatch/monitoring/checks.py", 0),
                        "longest": L(num(row[4]), "REAL", "REAL: the longest gap between two postings D0 saw, 3 to 10 Oct 2026 (dispatch/notes/integration.md 2.3)", 0)})
    for rid, nm in (("GHCND", "weather, fetched daily"), ("RNGWHHD", "Henry Hub gas, fetched weekly")):
        reports.append({"id": rid, "name": nm, "halts": rid in halt_set,
                        "allowance": L(cadence[rid] + extra.get(rid, 0) + fresh, "DERIVED", "DERIVED: cadence + extra allowance + data freshness target", 0), "longest": None})
    # a model-read report is halt eligible: record which, from the code
    fb = md_table(integ, "| hub | daily evaluations | false-breach rate |")
    d1a = read_text(NOTES / "d1a.md")
    rule_a = next(r for r in md_table(d1a, "| rule | HB_NORTH | HB_WEST | HB_HOUSTON | HB_SOUTH |") if r[0].startswith("**A**"))
    rule_d = next(r for r in md_table(d1a, "| rule | HB_NORTH | HB_WEST | HB_HOUSTON | HB_SOUTH |") if r[0].startswith("**D**"))
    faults = md_table(d1a, "| injected into the holdout forecasts |")
    hubs = ["HB_NORTH", "HB_WEST", "HB_HOUSTON", "HB_SOUTH"]
    C_FB = "SIM: D1a's holdout 1 Jan 2025 to 26 Sep 2026, 628 daily evaluations per hub, replayed by the platform's own monitor (dispatch/notes/integration.md section 3, d1a.md 7.2)"
    false_breach = []
    for i, h in enumerate(hubs):
        row = next(r for r in fb if r[0] == h)
        false_breach.append({
            "hub": h,
            "platform": L(num(row[2]), "SIM", C_FB, 2),
            "ruleD": L(num(rule_d[1 + i]), "SIM", C_FB + "; rule D in D1a's study", 1),
            "ruleA": L(num(rule_a[1 + i]), "SIM", C_FB + "; the first-draft rule A (tolerance 0.10, pinball +25%)", 1),
            "halts": L(num(row[5]), "SIM", C_FB + "; separate episodes of two or more consecutive bad days", 0),
        })
    inj = {}
    for r in faults:
        key = re.sub(r"[^a-z0-9$+ -]", "", r[0].lower()).strip()
        inj[key] = [num(x.split("/")[0]) for x in r[1:5]]
    def inj_row(prefix):
        k = next(k for k in inj if k.startswith(prefix))
        return [L(v, "SIM", C_FB + "; fault injected into the holdout forecasts, rule D", 0) for v in inj[k]]
    catches = {
        "bias10": inj_row("+$10"), "narrow": inj_row("bands shrunk"), "stale": inj_row("forecasts a day stale"),
    }
    psi = find(r"(\d+)-(\d+)% of features\s+#?\s*exceeded PSI", read_text(PKG / "config.py"), "PSI share")

    monitor = {
        "psiAlert": L(cfg["DRIFT_PSI_ALERT"], "ASSUMPTION", C_CFG + " DRIFT_PSI_ALERT", 1),
        "psiHalts": cfg["DRIFT_PSI_HALTS"],
        "psiLow": L(int(psi.group(1)), "DERIVED", "DERIVED: D1a, share of features above PSI 0.2 on in-distribution HB_NORTH windows (config.py comment)"),
        "psiHigh": L(int(psi.group(2)), "DERIVED", "DERIVED: D1a, the other end of the range of features above PSI 0.2 (config.py comment)"),
        "windowDays": L(cfg["CALIBRATION_WINDOW_INTERVALS"] / 96, "ASSUMPTION", C_CFG + " CALIBRATION_WINDOW_INTERVALS ÷ 96 intervals a day", 0),
        "hitTol": L(cfg["CALIBRATION_HIT_TOL"], "ASSUMPTION", C_CFG + " CALIBRATION_HIT_TOL (D1a's false-breach study)", 2),
        "skill": L(cfg["SKILL_DEGRADATION_MAX"] * 100, "ASSUMPTION", C_CFG + " SKILL_DEGRADATION_MAX: P50 MAE worse than the 7-day profile median by more than this", 0),
        "haltAfter": L(cfg["CALIBRATION_HALT_AFTER"], "ASSUMPTION", C_CFG + " CALIBRATION_HALT_AFTER: consecutive daily breaches"),
        "haltAfterOthers": L(cfg["HALT_AFTER_BREACHES"], "ASSUMPTION", C_CFG + " HALT_AFTER_BREACHES: consecutive passes for stale data, capture decay and slippage"),
        "evalHours": L(cfg["CALIBRATION_EVAL_EVERY_S"] / 3600, "ASSUMPTION", C_CFG + " CALIBRATION_EVAL_EVERY_S", 0),
        "slippageTol": L(cfg["SLIPPAGE_TOL"] * 100, "ASSUMPTION", C_CFG + " SLIPPAGE_TOL", 0),
        "captureDecay": L(kc["CAPTURE_DECAY_TOL"] * 100, "ASSUMPTION", "dispatch/dispatch/monitoring/checks.py CAPTURE_DECAY_TOL", 0),
        "evals": L(int(num(fb[0][1])), "SIM", C_FB),
        "freshness": L(fresh, "ASSUMPTION", C_CFG + " DATA_FRESHNESS_TARGET_S (PRD proposed)", 0),
        "biasUsd": L(int(find(r"\+\$(\d+)/MWh", d1a, "injected bias").group(1)), "ASSUMPTION", "the fault injected into every quantile in D1a's study (dispatch/notes/d1a.md 7.2)"),
        "falseBar": L(num(fb[0][3]), "ASSUMPTION", "D1a's bar: a halt rule must breach under this share of in-distribution windows (dispatch/notes/integration.md section 3)", 0),
        "firstRuleLow": L(min(num(x) for x in rule_a[1:5]), "SIM", C_FB, 1),
        "firstRuleHigh": L(max(num(x) for x in rule_a[1:5]), "SIM", C_FB, 1),
    }
    fp = [num(f["platform"]["v"]) for f in false_breach]
    monitor["platformLow"] = L(min(fp), "SIM", C_FB, 2)
    monitor["platformHigh"] = L(max(fp), "SIM", C_FB, 2)

    safe_summary = {
        "reasonCodes": L(len(reason_codes), "REAL", "dispatch/dispatch/guard/guard.py REASON_CODES"),
        "examples": L(total_examples, "DERIVED", "DERIVED: the six property runs added, " + C_D2),
        "tests": L(n_guard_tests, "DERIVED", "tests/test_guard_props.py, " + C_D2),
        "genAccepted": L(acc_n, "DERIVED", "DERIVED: the derandomised generator reaches both outcomes, dispatch/notes/integration.md 2.6"),
        "genRejected": L(rej_n, "DERIVED", "DERIVED: setpoints the derandomised generator has the guard reject, dispatch/notes/integration.md 2.6"),
        "genCodes": L(codes_n, "DERIVED", "DERIVED: distinct reason codes the derandomised generator reaches, dispatch/notes/integration.md 2.6"),
        "socAge": L(gc["MAX_SOC_AGE_S"], "ASSUMPTION", "dispatch/dispatch/guard/guard.py MAX_SOC_AGE_S", 0),
        "eps": L(cfg["GUARD_EPS_MW"], "ASSUMPTION", C_CFG + " GUARD_EPS_MW: the numeric tolerance the guard may clip"),
        "replayAccepted": L(sum(1 for g in cycle_by["guard_decision"] if g["accepted"]), "SIM", C_REPLAY),
        "replayRejected": L(sum(1 for g in cycle_by["guard_decision"] if not g["accepted"]), "SIM", C_REPLAY),
        "chainRecords": L(len(audit_replay), "DERIVED", "DERIVED: records in the replay audit log, hash recomputed by this exporter"),
        "realRecords": L(len(real), "DERIVED", "DERIVED: records in the real runtime audit log, hash recomputed by this exporter"),
        "tamperLine": L(bad_line, "DERIVED", "DERIVED: first line at which verification fails after one number in one guard decision is edited"),
        "tamperEdited": L(tamper_i + 1, "DERIVED", "DERIVED: the line (1-based) of the record that was edited"),
        "events": L(len(extract_event_types()), "REAL", "dispatch/dispatch/execution/audit.py EVENT_TYPES"),
        "exposure": L(json.loads(read_text(DISP / "ops" / "assets.example.json"))["assets"][0]["exposure_cap_usd"], "ASSUMPTION", "dispatch/ops/assets.example.json exposure_cap_usd"),
    }
    return {
        "summary": safe_summary,
        "guard": {
            "groups": [{"name": nm, "codes": cs, "what": what} for nm, cs, what in GUARD_GROUPS],
            "props": [{"name": p["name"], "examples": L(p["examples"], "DERIVED", C_D2)} for p in prop_rows],
        },
        "failPaths": [{"name": nm, "status": st, "codes": cs, "what": what} for nm, st, cs, what in fail_paths],
        "realEvent": real_event,
        "realChainOk": bool(ok_real),
        "replayChainOk": True, "chain": chain,
        "tamper": {"edited": tamper_i, "firstBad": bad_line - 1, "reason": why_t, "type": audit_replay[tamper_i]["type"],
                   "field": "the approved setpoint's net_mw", "from": r3(audit_replay[tamper_i]["setpoint"]["net_mw"]),
                   "to": r3(victim["setpoint"]["net_mw"])},
        "monitor": monitor, "reports": reports, "falseBreach": false_breach, "catches": catches,
        "events": extract_event_types(),
        "haltKey": cfg["HALT_KEY"],
    }


# ---------------------------------------------------------------------------------------------------------- 4. ready for what
def build_ready(cfg, assets, n_cycles):
    readme = read_text(DISP / "README.md")
    gate_rows = md_table(readme, "| Gate | Pass when | Status |")
    assert len(gate_rows) == 3
    gates = []
    for row in gate_rows:
        gno = int(re.match(r"\*\*(\d)\*\*", row[0]).group(1))
        gname = re.sub(r"^\*\*\d\*\*\s*", "", row[0])
        st = re.match(r"\*\*(.+?)\*\*", row[2])
        status = st.group(1)
        gates.append({"n": gno, "name": gname, "status": status,
                      "tone": "pass" if status.startswith("PASS") else "partial" if status.startswith("Mechanically") else "open"})
    g1 = find(r"(\d+) of (\d+) parquet files identical on (\d+) real raw files", gate_rows[0][2], "gate 1 rebuild")
    g1b = find(r"on the real lake (\d+) of (\d+) identical", gate_rows[0][2], "gate 1 real lake")
    case = json.loads(read_text(CASE / "summary.json"))
    gm = find(r"over at least a week of \*\*real-time\*\* paper", gate_rows[2][1], "gate 3 week")
    C_G = "dispatch/README.md gate status table"
    gate_nums = {
        "g1Files": L(int(g1.group(1)), "DERIVED", C_G + "; D0's rebuild test"), "g1FilesOf": L(int(g1.group(2)), "DERIVED", C_G),
        "g1Raw": L(int(g1.group(3)), "DERIVED", C_G), "g1Lake": L(int(g1b.group(1)), "DERIVED", C_G), "g1LakeOf": L(int(g1b.group(2)), "DERIVED", C_G),
        "g2Target": L(cfg["ATTRIBUTION_CLOSURE_MAX"] * 100, "ASSUMPTION", C_CFG + " ATTRIBUTION_CLOSURE_MAX (PRD proposed)", 0),
        "g2Unexplained": L(case["closure"] * 100, "DERIVED", "DERIVED: |sum of unexplained| ÷ |sum of gap|, zero by construction on public data", 1),
        "g2Stress": L(case["proxy_stress_closure"] * 100, "DERIVED", "DERIVED: reports/case_study/summary.json proxy_stress_closure", 1),
        "g3Slippage": L(cfg["SLIPPAGE_TOL"] * 100, "ASSUMPTION", C_CFG + " SLIPPAGE_TOL", 0),
        "g3Days": L(7, "ASSUMPTION", C_G + ": at least a week of real-time paper operation (PRD)"),
        "g3Replayed": L(round(n_cycles * 0.25 / 24), "SIM", "DERIVED: replayed decisions × 15 minutes ÷ 24 hours; dispatch/notes/integration.md section 6"),
    }

    # the registry, read-only
    db = HOME / "mlflow" / "mlflow.db"
    con = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True)
    try:
        vers = con.execute("select version, run_id, creation_time from model_versions order by cast(version as int)").fetchall()
        aliases = dict(con.execute("select alias, version from registered_model_aliases").fetchall())
        tags = {}
        for v, k, val in con.execute("select version, key, value from model_version_tags"):
            tags.setdefault(int(v), {})[k] = val
        met = {}
        rid6 = next(r for v, r, _ in vers if int(v) == 6)
        for k, val in con.execute("select key, value from latest_metrics where run_uuid = ?", (rid6,)):
            met[k] = val
        gate3_tags = con.execute("select count(*) from model_version_tags where key = 'gate3.pass'").fetchone()[0]
        n_runs = con.execute("select count(*) from runs").fetchone()[0]
    finally:
        con.close()
    C_REG = "MLflow registry, research-data/dispatch/mlflow/mlflow.db, read with sqlite3"
    versions = []
    for v, _, ct in vers:
        v = int(v)
        t = tags[v]
        als = sorted([a for a, vv in aliases.items() if int(vv) == v], key=lambda a: ["research", "paper", "live"].index(a))
        hist = json.loads(t.get("stage_history", "[]"))
        pol = json.loads(t.get("promotion.paper.policy", "{}")) if t.get("promotion.paper.policy") else {}
        versions.append({
            "ver": v, "kind": t["kind"], "featureSet": t["feature_set"], "registered": t["registered_utc"], "aliases": als,
            "by": (hist[-1]["by"] if hist else ""), "checks": pol.get("checks_passed"), "dataKind": t["data.kind"],
            "bundle": t["bundle.sha256"][:12], "note": (hist[-1]["note"] if hist else ""),
        })
    served = int(aliases["paper"])
    assert int(aliases["research"]) == served
    assert "live" not in aliases, "live is set; the page says it is locked"
    pol6 = json.loads(tags[served]["promotion.paper.policy"])
    registry = {
        "served": served, "rollback": 1, "alias": {"research": int(aliases["research"]), "paper": int(aliases["paper"]), "live": None},
        "versions": versions,
        "headline": {
            "served": L(served, "REAL", C_REG + "; aliases research and paper"),
            "checks": L(pol6["checks_passed"], "REAL", C_REG + "; promotion.paper.policy checks_passed (every hub beats every baseline on pinball and P50 MAE; coverage in band)"),
            "minGain": L(pol6["paper_min_pinball_gain"] * 100, "ASSUMPTION", C_REG + "; PAPER_MIN_PINBALL_GAIN, a named constant of mlflow_registry.py", 0),
            "bandLow": L(pol6["paper_coverage_band"][0], "ASSUMPTION", C_REG + "; PAPER_COVERAGE_BAND", 2),
            "bandHigh": L(pol6["paper_coverage_band"][1], "ASSUMPTION", C_REG + "; PAPER_COVERAGE_BAND", 2),
            "gate3Tags": L(gate3_tags, "REAL", C_REG + "; model_version_tags with key gate3.pass"),
            "runs": L(n_runs, "REAL", C_REG + "; runs in the store"),
            "versions": L(len(vers), "REAL", C_REG),
        },
    }
    # forecast metrics: the served release run's holdout, model against the four baselines
    hubs = ["HB_NORTH", "HB_WEST", "HB_HOUSTON", "HB_SOUTH"]
    base_names = [("profile_median_7d", "Seven-day profile median"), ("arena_profile", "Arena profile"),
                  ("seasonal_naive_1d", "Same time yesterday"), ("seasonal_naive_7d", "Same time last week")]
    C_M = "SIM: walk-forward holdout 1 Jan 2025 to 26 Sep 2026, 364,704 forecast rows per hub, decisions every 4 hours, Winter Storm Fern inside; " + C_REG
    def m(prefix, hub, key):
        return met[f"{prefix}holdout.{hub}.{key}"]
    v1 = json.loads(tags[1]["holdout.metrics"])
    forecast = {
        "hubs": hubs, "baselines": [{"id": b, "name": n} for b, n in base_names],
        "model": {h: {"pinball": r3(m("", h, "pinball_mean")), "coverage": r3(m("", h, "coverage_p10_p90")), "mae": r2(m("", h, "mae_p50")),
                      "capture": r3(met[f"value.{h}.model"]), "pinballV1": r3(v1[h]["pooled"]["pinball_mean"]), "coverageV1": r3(v1[h]["pooled"]["coverage_p10_p90"])}
                  for h in hubs},
        "base": {b: {h: {"pinball": r3(m(f"baseline.{b}.", h, "pinball_mean")), "coverage": r3(m(f"baseline.{b}.", h, "coverage_p10_p90")),
                         "mae": r2(m(f"baseline.{b}.", h, "mae_p50")), "capture": r3(met[f"value.{h}.{b}"])} for h in hubs} for b, _ in base_names},
        "headline": {
            "rows": L(int(m("", "HB_NORTH", "n")), "SIM", C_M),
            "nominal": L(round((0.9 - 0.1) * 100), "ASSUMPTION", "P10 to P90 is an 80% band by construction", 0),
            "gainLow": L(min(1 - m("", h, "pinball_mean") / min(met[f"baseline.{b}.holdout.{h}.pinball_mean"] for b, _ in base_names) for h in hubs) * 100, "DERIVED",
                         "DERIVED: pinball gain over the strongest baseline at each hub, smallest hub", 1),
            "gainHigh": L(max(1 - m("", h, "pinball_mean") / min(met[f"baseline.{b}.holdout.{h}.pinball_mean"] for b, _ in base_names) for h in hubs) * 100, "DERIVED",
                          "DERIVED: pinball gain over the strongest baseline at each hub, largest hub", 1),
        },
    }
    # the LP against the research arena's dynamic programme
    dp = read_csv(CASE / "dp_crosscheck.csv")
    dpv = np.array([float(r["dp_usd"]) for r in dp])
    lpa = np.array([float(r["lp_arena_usd"]) for r in dp])
    lpc = np.array([float(r["lp_ac_usd"]) for r in dp])
    d_arena = lpa - dpv
    dpcheck = {
        "headline": {
            "days": L(len(dp), "SIM", "dispatch/reports/case_study/dp_crosscheck.csv: historical zone-days 2011 to 2026"),
            "maxAbs": L(float(np.abs(d_arena).max()), "SIM", "SIM: largest |LP − DP| in $ per MW-day over the 300 days, arena basis"),
            "totalDp": L(dpv.sum(), "SIM", "SIM: the arena's dynamic programme, total over the 300 days", 0),
            "totalLp": L(lpa.sum(), "SIM", "SIM: the platform LP on the arena's basis, total over the 300 days", 0),
            "exact": L(int((d_arena == 0).sum()), "SIM", "SIM: days on which the LP and the DP agree to the last bit, dp_crosscheck.csv"),
            "tolerance": L(1e-6, "ASSUMPTION", "tests/test_lp.py asserts the cross-check to this tolerance"),
            "gridBasis": L((lpc.sum() / dpv.sum() - 1) * 100, "DERIVED", "DERIVED: the LP with a grid-side charge limit earns this much less than the DP in total", 2),
        },
        # the 300 differences counted by decade of |LP - DP| (a log scale): "zero" are exactly equal; every other day sits in its decade
        "hist": dp_hist(d_arena),
    }
    # open questions: text from README's list, kept verbatim in meaning
    questions = [
        {"q": "Audit-log retention", "state": "Undecided. Nothing is ever deleted.", "who": "Business and legal"},
        {"q": "The exposure cap", "state": "The guard enforces the cost basis of stored energy against a pilot cap. Whether inventory cost, a daily loss or an ancillary shortfall is the right measure is open.", "who": "Business"},
        {"q": "Pricing", "state": "Per megawatt, or a share of value recovered.", "who": "Business"},
        {"q": "The first design partner", "state": "A small owner, a lender, or an ADER aggregator. Gate 2 closes for real only against their settlement statement.", "who": "Business"},
        {"q": "Employment and data rules", "state": "The PRD's first risk: what rules apply before this touches a market participant's data.", "who": "Business and legal"},
    ]
    notbuilt = [
        {"what": "A live bid", "state": "The exchange adapter is disabled by design: this version writes a paper ledger and nothing else."},
        {"what": "Fleet dispatch", "state": "Waits on ERCOT's Board vote on ADER Phase 4. Only an interface stub exists."},
        {"what": "Prometheus, Grafana, Docker", "state": "The configs are checked by tests for the metrics they name; the programs were never run."},
        {"what": "A scheduler", "state": "Nothing starts the paper loop. The ledger holds a replayed day and two live setpoints."},
    ]
    return {"gates": gates, "gateNums": gate_nums, "registry": registry, "forecast": forecast, "dpcheck": dpcheck, "questions": questions, "notBuilt": notbuilt}


def dp_hist(d):
    a = np.abs(np.asarray(d, dtype=float))
    assert a.max() < 1e-6, "an LP-DP difference exceeds the tolerance"
    out = {"zero": int((a == 0).sum()), "decades": []}
    nz = a[a > 0]
    ex = np.floor(np.log10(nz)).astype(int)
    for e in range(-13, -6):
        out["decades"].append({"e": e, "n": int(((ex == e) | ((e == -13) & (ex < -13))).sum())})
    assert out["zero"] + sum(x["n"] for x in out["decades"]) == len(a)
    return out


# ---------------------------------------------------------------------------------------------------------- main

_CITE_LABEL = re.compile(r"^(REAL|SIM|DERIVED|ASSUMPTION)(?=[:\s/])")


def align_cites(o):
    """Make every cite that opens with a label word open with the value's own label (lead, 14 Oct 2026): a shared cite
    string must not tell the reader DERIVED next to a SIM tag. The rest of the cite is kept."""
    if isinstance(o, dict):
        lab, key = o.get("label"), ("cite" if isinstance(o.get("cite"), str) else "text" if isinstance(o.get("text"), str) else None)
        if lab in ("REAL", "SIM", "DERIVED", "ASSUMPTION") and key:
            o[key] = _CITE_LABEL.sub(lab, o[key], count=1)
        for v in o.values():
            align_cites(v)
    elif isinstance(o, list):
        for v in o:
            align_cites(v)
    return o


def main():
    cfg = consts(PKG / "config.py", env={"MLFLOW": Path("mlflow")})
    for k in ("CYCLE_P99_TARGET_S", "MAX_FEATURE_AGE_S", "SLIPPAGE_TOL", "DRIFT_PSI_ALERT", "DRIFT_PSI_HALTS", "CALIBRATION_HIT_TOL",
              "SKILL_DEGRADATION_MAX", "CALIBRATION_HALT_AFTER", "HALT_AFTER_BREACHES", "CALIBRATION_EVAL_EVERY_S", "CALIBRATION_WINDOW_INTERVALS",
              "DATA_FRESHNESS_TARGET_S", "GUARD_EPS_MW", "ATTRIBUTION_CLOSURE_MAX", "HALT_KEY"):
        assert k in cfg, f"config.py no longer defines {k}"
    assets = json.loads(read_text(DISP / "ops" / "assets.example.json"))

    cycle, audit, by = build_cycle(cfg, assets)
    earned = build_earned()
    safe = build_safe(cfg, by, audit)
    ready = build_ready(cfg, assets, cycle["n"])

    fp = {
        "case_study_summary": sha256_file(CASE / "summary.json"), "attribution_intervals": sha256_file(CASE / "attribution_intervals.csv"),
        "replay_audit_head": audit[-1]["hash"], "replay_ledger": sha256_file(RUN / "replay_runtime" / "ledger" / "ledger-PILOT_HB_NORTH.jsonl"),
        "lake_catalog": sha256_file(HOME / "lake" / "catalog.jsonl"),
    }
    S = lambda label, unit, text: {"label": label, "unit": unit, "text": text}  # noqa: E731
    doc = envelope(
        "dispatch", "scripts.export_dispatch",
        inputs={"prices_sha256": None, "loads_sha256": None, "topology_sha256": None},
        constants={
            "ASSET_PILOT": {"value": "PAD1_ESS_ESR1", "label": "REAL", "cite": "the public pilot battery of the case study, ERCOT 60-day SCED disclosure (NP3-965-ER)"},
            "ASSET_PAPER": {"value": "PILOT_HB_NORTH", "label": "ASSUMPTION", "cite": "the paper asset of the replay, dispatch/ops/assets.example.json (10 MW / 20 MWh, a PROPOSED pilot size)"},
            "WEAR_USD_MWH": {"value": 20, "label": "ASSUMPTION", "cite": "wear cost per MWh discharged, the research arena's value; applied identically to every column of the case study"},
            "CYCLE_P99_TARGET_S": {"value": cfg["CYCLE_P99_TARGET_S"], "label": "ASSUMPTION", "cite": "PRD target, dispatch/dispatch/config.py"},
            "MAX_FEATURE_AGE_S": {"value": cfg["MAX_FEATURE_AGE_S"], "label": "ASSUMPTION", "cite": "dispatch/dispatch/config.py (D0 measured ERCOT posting price files up to about 8.5 minutes late)"},
            "SLIPPAGE_TOL": {"value": cfg["SLIPPAGE_TOL"], "label": "ASSUMPTION", "cite": "Gate 3, dispatch/dispatch/config.py"},
        },
        sources={
            "ercot": {"label": "REAL", "text": "ERCOT MIS: NP6-905-CD settlement point prices (publish times from the lake catalog), the 60-day SCED and DAM disclosures NP3-965-ER and NP3-966-ER, NP6-331 ancillary prices; ERCOT data may be redistributed in analyses, not its logo"},
            "casestudy": {"label": "DERIVED", "text": "dispatch/reports/case_study/: the pilot's telemetry and awards valued at hub proxy prices, interval-by-interval attribution against perfect foresight"},
            "replay": {"label": "SIM", "text": "dispatch/notes/integration.md section 6: 96 paper decisions replayed on the real stack (registered model, real LP, guard, ledger, audit), 9 to 10 Oct 2026"},
            "registry": {"label": "DERIVED", "text": "MLflow registry of the platform (research-data/dispatch/mlflow/mlflow.db), read with sqlite3"},
            "platform": {"label": "SIM", "text": "dispatch/: the platform's code, tests and notes. Models forecast prices; the LP chooses; the guard checks."},
        },
        series={
            "cycle.fan": S("SIM", "$/MWh", "SIM: LightGBM P10, P50 and P90 real-time price forecasts for the next 96 fifteen-minute intervals, from the forecast log; the dotted line is the 7-day profile median the model is anchored on"),
            "cycle.age": S("DERIVED", "s", "DERIVED: decision time minus the publish time of the newest ERCOT price file (the audit's feature_age_s)"),
            "cycle.pub": S("REAL", "UTC", "REAL: the publish stamp of the NP6-905-CD price file, from the lake catalog (ERCOT's own file name)"),
            "cycle.plan": S("SIM", "MW, MWh", "SIM: the paper ledger's setpoint and state of charge for each decision: the first interval of one LP plan"),
            "cycle.latency": S("SIM", "s", "SIM: server-side latency of each replayed cycle, from the audit log"),
            "cycle.hash": S("DERIVED", "sha256", "DERIVED: SHA-256 of the canonical JSON of each audit record, recomputed by the exporter"),
            "earned.days": S("DERIVED", "$", "DERIVED: daily actual, shadow and perfect-foresight value at hub proxy prices (attribution_daily.csv); the shadow and perfect foresight are SIM, the actual is the asset's REAL dispatch revalued"),
            "earned.top": S("DERIVED", "$", "DERIVED: the 20 largest per-interval gaps, top20_missed_intervals.csv"),
            "earned.proxy": S("DERIVED", "$", "DERIVED: first-order revaluation under each October day's node-minus-hub basis (REAL), proxy_sensitivity.csv"),
            "earned.wear": S("DERIVED", "$", "DERIVED: the case study re-run at a different wear charge, wear_sensitivity.csv"),
            "ready.forecast": S("SIM", "$/MWh", C_M_SERIES()),
            "ready.dp": S("SIM", "log10 $", "SIM: log10 of |LP − DP| for each of 300 historical zone-days, dp_crosscheck.csv"),
            "ready.versions": S("DERIVED", "", "DERIVED: model versions and tags from the MLflow registry"),
        },
    )
    doc["meta"] = {
        "title": "Dispatch",
        "question": "How do you run a battery safely and prove what it earned?",
        "framing": "Paper dispatch only. No live bid, no settled revenue, no ERCOT API key.",
        "replayDay": cycle["firstUtc"][:10],
        "fingerprints": fp,
        "sourcesUsed": [
            "dispatch/README.md", "dispatch/DESIGN.md", "dispatch/notes/{d1a,d2,integration}.md", "dispatch/reports/case_study/",
            "research-data/dispatch_i1_live/final-run/replay_runtime", "research-data/dispatch/{lake/catalog.jsonl,mlflow/mlflow.db,runtime/audit}",
        ],
    }
    doc["cycle"] = cycle
    doc["earned"] = earned
    doc["safe"] = safe
    doc["ready"] = ready
    n = write_json(OUT, align_cites(doc))
    print(f"wrote {OUT.relative_to(ROOT).as_posix()} ({n / 1024:.1f} KB)")


def C_M_SERIES():
    return ("SIM: LightGBM P10/P50/P90 against four baselines on a walk-forward holdout, 1 Jan 2025 to 26 Sep 2026; pinball loss is the mean of the "
            "P10, P50 and P90 losses in $/MWh, coverage the share of prices between P10 and P90, capture the revenue of a rolling plan from the P50 view "
            "over perfect foresight (one reference battery, no interval)")


if __name__ == "__main__":
    main()
