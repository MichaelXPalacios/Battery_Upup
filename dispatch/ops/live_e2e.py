"""Live end-to-end on the real stack: real uvicorn, the platform's own Redis, the real lake, the registered `paper` model, the real LP.

    .venv/Scripts/python.exe ops/live_e2e.py --phase pilot       # no model needed: /attribution for the public pilot, from the lake
    .venv/Scripts/python.exe ops/live_e2e.py --phase live        # prepare_signal + POST /signal for PILOT_HB_NORTH on the real clock
    .venv/Scripts/python.exe ops/live_e2e.py --phase replay      # ~20 replayed cycles inside lake coverage: latency, /attribution, Gate 3
    .venv/Scripts/python.exe ops/live_e2e.py --phase all --replay-count 96     # pilot, then replay (a day of decisions), then live

What each phase does, and what it leaves behind
  pilot   starts the service (alias `paper`, its own runtime dir), GET /attribution for PAD1_ESS_ESR1 over a week and over the 42 days of the case
          study (compared with reports/case_study/summary.json), GET /health, stops the service. Reads the lake; writes nothing in it.
  live    starts the service on the REAL runtime dirs (audit log, paper ledger, forecast log under DISPATCH_HOME/runtime), runs
          `flows.prepare_signal(["HB_NORTH"])` (which fetches what ERCOT just posted: public MIS, no key, and parses it into the lake) and POST /signal
          for PILOT_HB_NORTH, then GET /health, /metrics and /attribution, and stops the service. ONE real paper setpoint enters the real ledger.
  replay  starts the service on a SCRATCH runtime dir (DISPATCH_RUNTIME_DIR, DISPATCH_ALLOW_REPLAY=1), replays `--replay-count` decisions 15 minutes
          apart (features rebuilt from the lake as of each instant, `now_utc` in the request), reports client and server latency against the 30 s
          p99 target, GET /attribution for the replayed window, and Gate 3 (the replayed paper ledger against D1b's backtest over the same window).

The service is started and stopped by this script, bound to 127.0.0.1 with a random bearer token that is never printed. Nothing is left running:
the `finally` terminates the process and checks that the port is closed. It never touches Redis :6379, which belongs to another application.
"""
from __future__ import annotations

import argparse
import json
import os
import secrets
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional

PROJECT = Path(__file__).resolve().parents[1]
if str(PROJECT) not in sys.path:
    sys.path.insert(0, str(PROJECT))

import httpx                                         # noqa: E402
import pandas as pd                                  # noqa: E402

ASSET, PILOT = "PILOT_HB_NORTH", "PAD1_ESS_ESR1"
STARTUP_TIMEOUT_S = 240.0                            # the model loads from MLflow and the LP warms up (a few seconds on a quiet machine)


def emit(step: str, **kw) -> None:
    print(json.dumps({"step": step, **kw}, default=str), flush=True)


def port_open(port: int) -> bool:
    with socket.socket() as s:
        s.settimeout(0.5)
        return s.connect_ex(("127.0.0.1", port)) == 0


class Service:
    """uvicorn in a subprocess with the real wiring. A context manager: stop() always runs."""

    def __init__(self, port: int, log_path: Path, env_extra: Optional[dict] = None):
        self.port, self.log_path, self.env_extra = port, log_path, env_extra or {}
        self.token = secrets.token_urlsafe(24)
        self.proc: Optional[subprocess.Popen] = None
        self.url = f"http://127.0.0.1:{port}"
        self.client = httpx.Client(timeout=300.0)

    def headers(self) -> dict:
        return {"Authorization": f"Bearer {self.token}"}

    def __enter__(self) -> "Service":
        if port_open(self.port):
            raise SystemExit(f"port {self.port} is already in use: refusing to start (or to talk to something that is not ours)")
        env = {**os.environ, "PYTHONIOENCODING": "utf-8", "MLFLOW_DISABLE_AGENT_HINT": "1", "DISPATCH_API_TOKEN": self.token,
               "DISPATCH_MODEL_ALIAS": "paper", "DISPATCH_MONITOR_INTERVAL_S": os.environ.get("DISPATCH_MONITOR_INTERVAL_S", "60"), **self.env_extra}
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        log = open(self.log_path, "wb")
        self.proc = subprocess.Popen([sys.executable, "-m", "uvicorn", "dispatch.service.app:create_app", "--factory", "--host", "127.0.0.1",
                                      "--port", str(self.port), "--log-level", "info"], cwd=str(PROJECT), env=env, stdout=log, stderr=subprocess.STDOUT)
        t0 = time.time()
        while time.time() - t0 < STARTUP_TIMEOUT_S:
            if self.proc.poll() is not None:
                raise SystemExit(f"the service exited with code {self.proc.returncode} while starting; see {self.log_path}")
            try:
                if self.client.get(f"{self.url}/health", timeout=3.0).status_code == 200:
                    emit("service_started", port=self.port, seconds=round(time.time() - t0, 1), log=str(self.log_path))
                    return self
            except httpx.HTTPError:
                pass
            time.sleep(0.5)
        raise SystemExit(f"the service did not answer /health within {STARTUP_TIMEOUT_S:.0f} s; see {self.log_path}")

    def __exit__(self, *exc) -> None:
        self.stop()

    def stop(self) -> None:
        self.client.close()
        if self.proc is not None and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=30)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait(timeout=30)
        time.sleep(0.5)
        emit("service_stopped", port=self.port, port_closed=not port_open(self.port))

    def get(self, path: str, **params):
        return self.client.get(f"{self.url}{path}", params=params, headers=self.headers())

    def post(self, path: str, body: dict):
        return self.client.post(f"{self.url}{path}", json=body, headers=self.headers())


def health_summary(svc: Service) -> dict:
    h = svc.get("/health").json()
    return {"status": h["status"], "model_version": h.get("model_version"), "model_alias": h.get("model_alias"), "halted": h["halted"],
            "redis": h.get("redis"), "redis_ok": h["redis_ok"], "redis_writable": h["redis_writable"],
            "wiring_ok": {k: v["ok"] for k, v in h["wiring"].items()}, "audit_ok": h["audit"]["ok"], "assets": h["assets"],
            "disclosure_assets": h.get("disclosure_assets"), "last_cycle": h.get("last_cycle"),
            "calibration": {k: v for k, v in h["calibration"].items() if k != "last_evaluation"} if h.get("calibration") else None,
            "monitoring_last_pass": (h.get("monitoring") or {}).get("last_pass")}


def metrics_summary(svc: Service) -> dict:
    from prometheus_client.parser import text_string_to_metric_families
    text = svc.client.get(f"{svc.url}/metrics").text
    fams = list(text_string_to_metric_families(text))
    keep = {"dispatch_cycles_total", "dispatch_halted", "dispatch_model_info", "dispatch_feature_age_seconds", "dispatch_redis_writable",
            "dispatch_data_age_seconds", "dispatch_monitor_halt_threshold", "dispatch_forecast_log_errors_total"}
    out = {"families": len(fams), "samples": sum(len(f.samples) for f in fams)}
    for f in fams:
        if f.name in keep or f.name + "_total" in keep:
            out[f.name] = {",".join(f"{k}={v}" for k, v in s.labels.items()) or "-": s.value for s in f.samples if not s.name.endswith("_created")}
    count = [s.value for f in fams if f.name == "dispatch_cycle_seconds" for s in f.samples if s.name.endswith("_count")]
    out["dispatch_cycle_seconds_count"] = count[0] if count else None
    return out


def attribution_summary(r: httpx.Response) -> dict:
    if r.status_code != 200:
        return {"http": r.status_code, **{k: v for k, v in r.json().items() if k in ("reason", "detail")}}
    b = r.json()
    rows = b["rows"]

    def total(col):                                   # the service writes NaN (no shadow run, say) as the string "NaN"
        vals = [x[col] for x in rows if isinstance(x.get(col), (int, float))]
        return round(sum(vals), 2) if vals else None
    sums = {c: total(c) for c in ("actual_usd", "shadow_usd", "pf_usd", "gap_usd", "forecast_usd", "soc_usd", "as_usd", "availability_usd",
                                  "unexplained_usd")}
    return {"http": 200, "kind": b["kind"], "source": b["source"], "window": b["window"], "n_intervals": b["n_intervals"], "closure": b["closure"],
            "closure_basis": (b.get("summary") or {}).get("closure_basis"), "shadow": b["shadow"], "sums_usd": sums, "notes": b.get("notes")}


# ================================================================================================ phases
def phase_pilot(a, run_dir: Path) -> None:
    ref = json.loads((PROJECT / "reports" / "case_study" / "summary.json").read_text(encoding="utf-8"))
    env = {"DISPATCH_RUNTIME_DIR": str(run_dir / "pilot_runtime"), "DISPATCH_MONITOR_INTERVAL_S": "0"}
    with Service(a.port, run_dir / "service-pilot.log", env) as svc:
        emit("health", **health_summary(svc))
        t0 = time.time()
        week = svc.get("/attribution", asset_id=PILOT, start="2026-07-06T05:00:00Z", end="2026-07-13T05:00:00Z")
        emit("attribution_pilot_week", seconds=round(time.time() - t0, 1), **attribution_summary(week))
        t0 = time.time()
        full = svc.get("/attribution", asset_id=PILOT, start="2026-07-01T05:00:00Z", end="2026-08-12T05:00:00Z")
        got = attribution_summary(full)
        emit("attribution_pilot_42_days", seconds=round(time.time() - t0, 1), **got)
        if got["http"] == 200:
            want = {"actual_usd": ref["actual_usd"], "pf_usd": ref["pf_usd"], "gap_usd": ref["gap_usd"], **ref["components_usd"]}
            emit("compared_with_case_study_summary_json", case_study=want, platform={k: got["sums_usd"][k] for k in want if k in got["sums_usd"]},
                 differences_usd={k: round(got["sums_usd"][k] - v, 2) for k, v in want.items() if got["sums_usd"].get(k) is not None}, case_study_closure=ref["closure"])
        emit("not_a_paper_asset", signal_http=svc.post("/signal", {"asset_id": PILOT}).status_code)


def phase_live(a, run_dir: Path) -> None:
    from dispatch.data import flows
    with Service(a.port, run_dir / "service-live.log") as svc:                      # the REAL runtime dirs: audit, ledger, forecast log
        emit("health_before", **health_summary(svc))
        t0 = time.time()
        prep = flows.prepare_signal([a.sp])
        emit("prepare_signal", seconds=round(time.time() - t0, 1), online=prep.get("online"), ingest={k: v for k, v in (prep.get("ingest") or {}).items()
                                                                                                 if k in ("elapsed_s", "error", "skipped", "publish_to_store_s")})
        t1 = time.time()
        r = svc.post("/signal", {"asset_id": ASSET})
        body = r.json()
        emit("signal", http=r.status_code, roundtrip_s=round(time.time() - t1, 3),
             **{k: body.get(k) for k in ("status", "reasons", "model_version", "feature_age_s", "latency_s", "plan_id", "setpoint", "guard", "detail")})
        emit("health_after", **health_summary(svc))
        emit("metrics", **metrics_summary(svc))
        now = pd.Timestamp.now(tz="UTC")
        emit("attribution_paper_now", **attribution_summary(svc.get("/attribution", asset_id=ASSET, start=(now - pd.Timedelta(hours=3)).isoformat(),
                                                                   end=now.isoformat())))


def phase_replay(a, run_dir: Path) -> None:
    import run_paper_loop as loop
    from dispatch.execution.ledger import PaperLedger
    from dispatch.monitoring.checks import gate3
    from dispatch.optimizer.backtest import make_backtest_fn
    from dispatch.data.prices import price_source
    from dispatch.service.wiring import pit_forecast_fn
    from dispatch.registry import mlflow_registry as R
    from dispatch.service.assets import AssetRegistry
    runtime = run_dir / "replay_runtime"
    start = pd.Timestamp(a.replay_from) if a.replay_from else (pd.Timestamp.now(tz="UTC").floor("D") - pd.Timedelta(days=2) + pd.Timedelta(hours=14))
    start = start.tz_localize("UTC") if start.tzinfo is None else start.tz_convert("UTC")
    env = {"DISPATCH_RUNTIME_DIR": str(runtime), "DISPATCH_ALLOW_REPLAY": "1", "DISPATCH_MONITOR_INTERVAL_S": "0"}
    assets = loop.load_assets([ASSET])
    with Service(a.port, run_dir / "service-replay.log", env) as svc:
        emit("health", **health_summary(svc))
        rows = []
        for k in range(a.replay_count):
            t = start + pd.Timedelta(minutes=15 * k)
            (row,) = loop.one_cycle(svc.client, svc.url, assets, now_utc=t, ingest=False, auth=svc.headers())      # features as of t, then POST /signal with now_utc=t
            rows.append(row)
            emit("replay_cycle", **{k_: row.get(k_) for k_ in ("t", "http", "status", "reasons", "net_mw", "feature_age_s", "server_latency_s", "signal_s", "prepare_s")})
        stats = loop.summarize(rows)
        lat = stats["server_latency_s"]
        emit("latency", cycles=stats["cycles"], statuses=stats["statuses"], server_latency_s=lat, signal_roundtrip_s=stats["signal_roundtrip_s"],
             target_p99_s=30.0, meets_target=(lat["p99"] is not None and lat["p99"] < 30.0),
             prepare_s_p50=_pct([r["prepare_s"] for r in rows], 0.5), prepare_s_max=max(r["prepare_s"] for r in rows))
        t_end = start + pd.Timedelta(minutes=15 * a.replay_count)
        emit("health_after", **health_summary(svc))
        emit("attribution_paper_replay", **attribution_summary(svc.get("/attribution", asset_id=ASSET, start=start.floor("15min").isoformat(),
                                                                      end=t_end.floor("15min").isoformat())))
        emit("metrics", **metrics_summary(svc))
    # Gate 3, offline: the replayed paper ledger against D1b's backtest of the same window, same model, same prices
    spec = AssetRegistry.load().get(ASSET).spec
    ledger = PaperLedger(runtime / "ledger")
    w0, w1 = start.floor("15min"), t_end.floor("15min")
    fc = R.load("paper")
    bt = make_backtest_fn(price_source, {ASSET: pit_forecast_fn(fc, spec.settlement_point)}, ledger=ledger)
    t0 = time.time()
    backtest = bt(spec, w0, w1)
    prices = price_source(spec, w0, w1)
    g = gate3(ledger, spec, prices, backtest, start_utc=w0, end_utc=w1)
    emit("gate3", seconds=round(time.time() - t0, 1), passed=g.passed, slippage_ratio=g.slippage_ratio, tolerance=g.tol, paper_revenue_usd=round(g.paper_revenue_usd, 2),
         backtest_revenue_usd=round(g.backtest_revenue_usd, 2), n_intervals=g.n_intervals, replay_problems=g.replay_problems[:3], model_version=fc.version,
         backtest_stats={k: v for k, v in bt.last.items() if k in ("solved", "memo_hits", "failed", "seconds")})


def _pct(xs: list, q: float):
    xs = sorted(xs)
    return None if not xs else xs[min(int(q * len(xs)), len(xs) - 1)]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--phase", choices=["pilot", "live", "replay", "all"], default="all")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--sp", default="HB_NORTH", help="settlement point whose features are refreshed in the live phase")
    ap.add_argument("--replay-from", help="ISO instant of the first replayed decision, on a quarter hour like the backtest's (default: 14:00Z two days ago, so that a day of replay is settled)")
    ap.add_argument("--replay-count", type=int, default=20)
    ap.add_argument("--run-dir", help="where logs and the replay runtime dir go (default: DISPATCH_HOME/../dispatch_i1_live/<timestamp>)")
    a = ap.parse_args(argv)
    from dispatch import config, paths
    from dispatch.service.state import normalize_redis_url
    if ":6379" in config.REDIS_URL:
        raise SystemExit("config.REDIS_URL points at :6379, which belongs to another application: refusing")
    config.REDIS_URL = normalize_redis_url(config.REDIS_URL)         # localhost -> 127.0.0.1: a new connection to `localhost` waits ~2 s for the IPv6 refusal
    stamp = time.strftime("%Y%m%d-%H%M%S")
    run_dir = Path(a.run_dir) if a.run_dir else paths.HOME.parent / "dispatch_i1_live" / stamp
    run_dir.mkdir(parents=True, exist_ok=True)
    emit("start", phases=a.phase, run_dir=str(run_dir), redis=config.REDIS_URL, dispatch_home=str(paths.HOME), now_utc=pd.Timestamp.now(tz="UTC"))
    try:
        if a.phase in ("pilot", "all"):
            phase_pilot(a, run_dir)
        if a.phase in ("replay", "all"):                  # before the live phase: a replay leaves a past frame in Redis, the live phase leaves a fresh one
            phase_replay(a, run_dir)
        if a.phase in ("live", "all"):
            phase_live(a, run_dir)
    finally:
        emit("done", port_closed=not port_open(a.port))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
