"""The paper loop: every 15 minutes, refresh the features and ask the service for a decision.

    flows.prepare_signal([settlement point])       pull what ERCOT just posted, parse it, rebuild the online features at "now"
    POST /signal {asset_id}                         one cycle: features, forecast, LP, guard, paper dispatch

Nothing in the service calls /signal, and the online features must be younger than `config.MAX_FEATURE_AGE_S` when it does (older means
`stale`, the designed failure). This script is the documented scheduler. It does not start the service: run uvicorn first (ops/README.md).

    .venv/Scripts/python.exe ops/run_paper_loop.py --once                       # one cycle now, for every configured asset, then exit
    .venv/Scripts/python.exe ops/run_paper_loop.py                              # every 15 minutes until Ctrl-C (or --max-cycles N)
    .venv/Scripts/python.exe ops/run_paper_loop.py --assets PILOT_HB_NORTH --url http://127.0.0.1:8000

When it triggers. At the start of an interval: `--offset-s` (default 15 s) after each quarter hour, once the clock has certainly ticked over and
before ERCOT has posted anything new. That is the decision instant of the training grid and of D1b's backtest (a decision at the start of every
interval), and the newest price the features can know is then the one of the interval that ended 15 minutes ago (about 13 minutes old, inside the
20-minute age gate: D0 measured 778 s at the quarter hours). Firing later in the interval (ERCOT posts an interval's real-time price file about
2 minutes after it ends: median 122 s, maximum 508 s over 7 days) would give fresher features, but the paper cycle would then know a price that the
backtest cannot, and Gate 3's slippage check, which halts at 5%, would measure that difference as if it were a fault.

Replays. `--replay-from 2026-10-08T14:00:00Z --replay-count 20` runs 20 consecutive decisions 15 minutes apart from that instant, using the
features the lake would have shown then (`prepare_signal(now_utc=t, ingest=False)`) and `now_utc=t` in the request. The service has to be
started with DISPATCH_ALLOW_REPLAY=1 and, so that replays never enter the live paper ledger, with its own DISPATCH_RUNTIME_DIR. Each cycle
prints its client-side and server-side latency.

Output: one JSON object per asset per cycle on stdout, then a summary. Exit code 0 if every cycle was answered with a decision (dispatched,
rejected, halted and stale are all HTTP 200); 2 if the service could not be reached or answered 5xx (status `error`: no model, no optimizer, ...).
The token comes from DISPATCH_API_TOKEN.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from collections import defaultdict
from pathlib import Path
from typing import Optional, Sequence

PROJECT = Path(__file__).resolve().parents[1]
if str(PROJECT) not in sys.path:
    sys.path.insert(0, str(PROJECT))

import httpx                                         # noqa: E402
import pandas as pd                                  # noqa: E402

DEFAULT_INTERVAL_S = 900.0                           # the settlement interval
DEFAULT_OFFSET_S = 15.0                              # after the quarter hour: the decision instant of the backtest and the training grid, plus clock slack
SIGNAL_TIMEOUT_S = 120.0                             # the cycle's own deadline is 120 s (service/cycle.py CYCLE_DEADLINE_S)


def emit(obj: dict) -> None:
    print(json.dumps(obj, default=str), flush=True)


def headers() -> dict:
    token = os.environ.get("DISPATCH_API_TOKEN")
    return {"Authorization": f"Bearer {token}"} if token else {}


def load_assets(only: Optional[Sequence[str]]) -> list:
    from dispatch.service.assets import AssetRegistry
    reg = AssetRegistry.load()
    ids = list(only) if only else reg.ids()
    out = []
    for a in ids:
        if a not in reg:
            raise SystemExit(f"unknown asset {a!r}; the assets file has {reg.ids()}")
        out.append((a, reg.get(a).spec.settlement_point))
    return out


def one_cycle(client: httpx.Client, url: str, assets: list, *, now_utc: Optional[pd.Timestamp] = None, ingest: bool = True,
              auth: Optional[dict] = None) -> list:
    """prepare_signal once per settlement point, then one POST /signal per asset. Returns one result dict per asset.
    `auth` is the Authorization header (default: the bearer token in DISPATCH_API_TOKEN, if set)."""
    from dispatch.data import flows
    by_sp = defaultdict(list)
    for asset, sp in assets:
        by_sp[sp].append(asset)
    t0 = time.perf_counter()
    try:
        prep = flows.prepare_signal(sorted(by_sp), now_utc=now_utc, ingest=ingest)
        errors = {sp: r["error"] for sp, r in prep.get("online", {}).items() if "error" in r}
        if isinstance(prep.get("ingest"), dict) and "error" in prep["ingest"]:
            errors["ingest"] = prep["ingest"]["error"]
        prepare = {"prepare_s": round(time.perf_counter() - t0, 2), "prepare_errors": errors or None}
    except Exception as exc:                                   # a failed refresh is not fatal: the service's age gate decides
        prepare = {"prepare_s": round(time.perf_counter() - t0, 2), "prepare_errors": {"prepare_signal": f"{type(exc).__name__}: {exc}"[:300]}}
    out = []
    for asset, sp in assets:
        body = {"asset_id": asset} if now_utc is None else {"asset_id": asset, "now_utc": now_utc.isoformat()}
        t1 = time.perf_counter()
        try:
            r = client.post(f"{url}/signal", json=body, headers=auth if auth is not None else headers(), timeout=SIGNAL_TIMEOUT_S)
            res = r.json() if r.headers.get("content-type", "").startswith("application/json") else {"raw": r.text[:300]}
            out.append({"t": (now_utc or pd.Timestamp.now(tz="UTC")).isoformat(), "asset": asset, "settlement_point": sp, "http": r.status_code,
                        "status": res.get("status"), "reasons": res.get("reasons"), "model_version": res.get("model_version"),
                        "feature_age_s": res.get("feature_age_s"), "net_mw": (res.get("setpoint") or {}).get("net_mw"),
                        "server_latency_s": res.get("latency_s"), "signal_s": round(time.perf_counter() - t1, 3), **prepare,
                        "detail": res.get("detail") if res.get("status") in ("error", "stale") else None})
        except httpx.HTTPError as exc:
            out.append({"t": (now_utc or pd.Timestamp.now(tz="UTC")).isoformat(), "asset": asset, "http": None, "status": "unreachable",
                        "reasons": [type(exc).__name__], "detail": str(exc)[:200], **prepare})
    return out


def next_trigger(now: pd.Timestamp, interval_s: float, offset_s: float) -> pd.Timestamp:
    """The first instant after `now` that is `offset_s` past a multiple of `interval_s` on the UTC clock."""
    step = pd.Timedelta(seconds=interval_s)
    t = now.floor(f"{int(interval_s)}s") + pd.Timedelta(seconds=offset_s)
    return t if t > now else t + step


def summarize(results: list) -> dict:
    lat = sorted(r["server_latency_s"] for r in results if isinstance(r.get("server_latency_s"), (int, float)))
    sig = sorted(r["signal_s"] for r in results if isinstance(r.get("signal_s"), (int, float)))
    pct = lambda xs, q: None if not xs else xs[min(int(q * len(xs)), len(xs) - 1)]
    counts: dict = defaultdict(int)
    for r in results:
        counts[r.get("status")] += 1
    return {"cycles": len(results), "statuses": dict(counts), "server_latency_s": {"p50": pct(lat, 0.5), "p99": pct(lat, 0.99), "max": lat[-1] if lat else None},
            "signal_roundtrip_s": {"p50": pct(sig, 0.5), "p99": pct(sig, 0.99), "max": sig[-1] if sig else None}}


def use_ipv4_redis() -> str:
    """`localhost` -> `127.0.0.1` in config.REDIS_URL for this process: D0's `prepare_signal` opens its own connection to the platform Redis, and on
    this Windows machine every connection to `localhost` waits ~2 s for the IPv6 refusal (see service/state.py normalize_redis_url)."""
    from dispatch import config
    from dispatch.service.state import normalize_redis_url
    config.REDIS_URL = normalize_redis_url(config.REDIS_URL)
    return config.REDIS_URL


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--url", default=os.environ.get("DISPATCH_URL", "http://127.0.0.1:8000"), help="the service (default %(default)s)")
    ap.add_argument("--assets", help="comma-separated asset ids (default: every asset in the assets file)")
    ap.add_argument("--once", action="store_true", help="run one cycle now and exit")
    ap.add_argument("--interval-s", type=float, default=DEFAULT_INTERVAL_S)
    ap.add_argument("--offset-s", type=float, default=DEFAULT_OFFSET_S, help="seconds after each quarter hour (default %(default)s)")
    ap.add_argument("--max-cycles", type=int, default=0, help="stop after this many cycles (0 = until interrupted)")
    ap.add_argument("--no-ingest", action="store_true", help="do not fetch from ERCOT, only rebuild the features from the lake")
    ap.add_argument("--replay-from", help="ISO instant: replay decisions from here instead of running live (needs DISPATCH_ALLOW_REPLAY=1 on the service)")
    ap.add_argument("--replay-count", type=int, default=20)
    ap.add_argument("--replay-step-min", type=float, default=15.0)
    a = ap.parse_args(argv)
    use_ipv4_redis()
    assets = load_assets([x for x in (a.assets or "").split(",") if x])
    results: list = []
    with httpx.Client() as client:
        if a.replay_from:
            t = pd.Timestamp(a.replay_from)
            t = t.tz_localize("UTC") if t.tzinfo is None else t.tz_convert("UTC")
            for k in range(a.replay_count):
                rows = one_cycle(client, a.url, assets, now_utc=t + pd.Timedelta(minutes=a.replay_step_min * k), ingest=False)
                for row in rows:
                    emit(row)
                results += rows
        elif a.once:
            rows = one_cycle(client, a.url, assets, ingest=not a.no_ingest)
            for row in rows:
                emit(row)
            results += rows
        else:
            n = 0
            while a.max_cycles <= 0 or n < a.max_cycles:
                wake = next_trigger(pd.Timestamp.now(tz="UTC"), a.interval_s, a.offset_s)
                print(f"next cycle at {wake:%Y-%m-%d %H:%M:%S}Z", file=sys.stderr, flush=True)
                try:
                    while (left := (wake - pd.Timestamp.now(tz="UTC")).total_seconds()) > 0:
                        time.sleep(min(left, 5.0))
                except KeyboardInterrupt:
                    break
                rows = one_cycle(client, a.url, assets, ingest=not a.no_ingest)
                for row in rows:
                    emit(row)
                results += rows
                n += 1
    summary = summarize(results)
    emit({"summary": summary})
    return 2 if any(r.get("http") is None or r["http"] >= 500 for r in results) else 0


if __name__ == "__main__":
    raise SystemExit(main())
