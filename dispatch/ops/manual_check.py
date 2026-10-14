"""Manual check of the dispatch service with real uvicorn, real curl and real Redis 3.0 (the check D2 ran on 10 Oct 2026).

    cd dispatch && .venv/Scripts/python.exe ops/manual_check.py

Scenario A  the machine's own Redis (localhost:6379), fake market and model: the state it is really in
Scenario B  a throwaway Redis 3.0.504 on port 6390 (same binary, no persistence), fake market and model: the full happy path,
            then a Redis outage
Scenario C  the same throwaway Redis, the REAL modules (whatever D0, D1a and D1b have delivered so far)
Everything it starts it stops. The demo uses its own DISPATCH_HOME (research-data/d2_manual_check) so the real paper ledger and audit
log are not touched. Needs curl and, for B and C, C:/Program Files/Redis/redis-server.exe. The transcript goes to the temp directory.
"""
import json
import os
import secrets
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]                       # hugging-base/dispatch
PY = sys.executable
SCRATCH = Path(tempfile.mkdtemp(prefix="d2_manual_check_"))
HOMES = Path(os.environ.get("ARENA_DATA", "C:/Users/Javier/Repo/BaseP/research-data")) / "d2_manual_check"
REDIS_SERVER = r"C:\Program Files\Redis\redis-server.exe"
TOKEN = secrets.token_hex(12)
OUT = []
RUNNING = []


def say(text=""):
    print(text, flush=True)
    OUT.append(text)


def trim(text, n=900):
    text = text.strip()
    return text if len(text) <= n else text[:n] + " ...[trimmed]"


def curl(method, url, body=None, auth=True, show=None):
    cmd = ["curl", "-s", "-S", "-m", "60", "-w", "\n[HTTP %{http_code}]", "-X", method, url]
    shown = f"curl -s -X {method} {url}"
    if auth:
        cmd += ["-H", f"Authorization: Bearer {TOKEN}"]
        shown += ' -H "Authorization: Bearer $DISPATCH_API_TOKEN"'
    if body is not None:
        cmd += ["-H", "content-type: application/json", "-d", json.dumps(body)]
        shown += f" -H 'content-type: application/json' -d '{json.dumps(body)}'"
    out = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8").stdout
    say(f"$ {shown}")
    text, _, code = out.rpartition("\n[HTTP ")
    code = code.rstrip("]")
    try:
        obj = json.loads(text)
        if show:
            obj = show(obj)
        text = json.dumps(obj, separators=(",", ":"))
    except ValueError:
        pass
    say(f"  [{code}] {trim(text)}")
    return code, text


def pick(*keys):
    def f(obj):
        return {k: obj.get(k) for k in keys if k in obj}
    return f


def wait_port(port, timeout=60):
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=1):
                return True
        except OSError:
            time.sleep(0.3)
    return False


def start_server(port, redis_url, home, target, label):
    env = dict(os.environ, PYTHONIOENCODING="utf-8", MLFLOW_DISABLE_AGENT_HINT="1", DISPATCH_HOME=str(home),
               DISPATCH_REDIS_URL=redis_url, DISPATCH_API_TOKEN=TOKEN, DISPATCH_MONITOR_INTERVAL_S="0")
    Path(home).mkdir(parents=True, exist_ok=True)
    log = open(SCRATCH / f"uvicorn_{label}.log", "w", encoding="utf-8")
    cmd = [PY, "-m", "uvicorn", *target, "--factory", "--host", "127.0.0.1", "--port", str(port), "--log-level", "warning"]
    say(f"$ DISPATCH_HOME={home} DISPATCH_REDIS_URL={redis_url} DISPATCH_API_TOKEN=<random> \\")
    say(f"    python -m uvicorn {' '.join(target)} --factory --host 127.0.0.1 --port {port}")
    proc = subprocess.Popen(cmd, cwd=PROJECT, env=env, stdout=log, stderr=subprocess.STDOUT)
    t0 = time.time()
    up = wait_port(port, 240)
    if up:
        for _ in range(240):                       # the lifespan hook (modules, model) finishes before /health answers
            code = subprocess.run(["curl", "-s", "-o", "NUL", "-w", "%{http_code}", f"http://127.0.0.1:{port}/health"],
                                  capture_output=True, text=True).stdout
            if code == "200":
                break
            time.sleep(0.5)
    say(f"  server up in {time.time() - t0:.1f} s" if up else "  SERVER DID NOT START")
    RUNNING.append((proc, log, port))
    return proc, log


def stop_server(proc, log, port):
    RUNNING[:] = [r for r in RUNNING if r[0] is not proc]
    proc.terminate()
    try:
        proc.wait(timeout=20)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()
    log.close()
    time.sleep(0.5)
    closed = not wait_port(port, 1)
    say(f"  uvicorn stopped (exit {proc.returncode}); port {port} {'closed' if closed else 'STILL OPEN'}")
    return closed


def redis_client(port):
    import redis
    return redis.Redis(host="localhost", port=port, protocol=2, decode_responses=True, socket_timeout=3)


def audit_cli(home, cmd):
    env = dict(os.environ, PYTHONIOENCODING="utf-8", DISPATCH_HOME=str(home))
    out = subprocess.run([PY, "-m", "dispatch.execution.audit", cmd], cwd=PROJECT, env=env, capture_output=True, text=True,
                         encoding="utf-8").stdout
    say(f"$ DISPATCH_HOME=... python -m dispatch.execution.audit {cmd}")
    lines = out.strip().splitlines()
    if cmd == "tail":
        for ln in lines[-8:]:
            rec = json.loads(ln)
            extra = {k: rec[k] for k in ("status", "reasons", "accepted", "reason", "set_by", "cleared_by", "model_alias") if k in rec}
            say(f"  seq {rec['seq']:>2} {rec['type']:<15} {json.dumps(extra, separators=(',', ':'))}  hash {rec['hash'][:12]}")
    else:
        say("  " + out.strip())


def main():
    results = {}
    sys.path.insert(0, str(PROJECT))
    if not Path(REDIS_SERVER).exists():
        say(f"{REDIS_SERVER} not found: scenarios B and C need a throwaway Redis 3.0, only A will run")

    # ------------------------------------------------------------------------------------------ A
    say("=" * 100)
    say("A. The machine's Redis (localhost:6379) with the fake market and model")
    say("=" * 100)
    r = redis_client(6379)
    mem = r.info("memory")
    say(f"redis 3.0 at 6379: version {r.info('server')['redis_version']}, used_memory {mem['used_memory_human']}, "
        f"maxmemory {r.config_get('maxmemory')['maxmemory']} bytes, policy {r.config_get('maxmemory-policy')['maxmemory-policy']}, "
        f"{r.dbsize()} keys (none of them ours: {r.keys('dispatch:*')})")
    try:
        r.set("dispatch:d2_probe", "x")
        say("a SET of dispatch:d2_probe: accepted")
        r.delete("dispatch:d2_probe")
    except Exception as exc:
        say(f"a SET of dispatch:d2_probe: refused ({type(exc).__name__}: {exc})")
    proc, log = start_server(8765, "redis://localhost:6379/0", HOMES / "A_localhost_6379", ["--app-dir", "tests", "fakes:make_demo_app"], "A")
    base = "http://127.0.0.1:8765"
    curl("GET", f"{base}/health", auth=False, show=pick("status", "model_version", "halted", "redis_ok", "redis_writable", "last_cycle_status"))
    curl("POST", f"{base}/signal", {"asset_id": "PILOT_HB_NORTH"}, show=pick("status", "reasons", "model_version", "feature_age_s", "latency_s"))
    curl("POST", f"{base}/kill", {"reason": "manual check: pretend the feed looks wrong", "set_by": "d2"}, show=pick("halted", "persisted", "warning"))
    curl("POST", f"{base}/signal", {"asset_id": "PILOT_HB_NORTH"}, show=pick("status", "reasons", "detail"))
    say(f"redis GET dispatch:halt -> {r.get('dispatch:halt')!r}   (Redis refused the SET; the service holds the kill itself)")
    curl("POST", f"{base}/resume", {"reason": "manual check done", "cleared_by": "d2"}, show=pick("halted", "was_halted"))
    curl("POST", f"{base}/signal", {"asset_id": "PILOT_HB_NORTH"}, show=pick("status", "reasons"))
    results["A"] = stop_server(proc, log, 8765)
    say(f"redis keys dispatch:* afterwards -> {r.keys('dispatch:*')}")
    if not Path(REDIS_SERVER).exists():
        return finish(results)

    # ------------------------------------------------------------------------------------------ B
    say("")
    say("=" * 100)
    say("B. A throwaway Redis 3.0.504 on port 6390 (same binary, no persistence), fake market and model")
    say("=" * 100)
    rproc = subprocess.Popen([REDIS_SERVER, "--port", "6390", "--bind", "127.0.0.1", "--save", "", "--appendonly", "no"],
                             cwd=SCRATCH, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        assert wait_port(6390, 30), "throwaway redis did not start"
        r2 = redis_client(6390)
        say(f"redis at 6390: version {r2.info('server')['redis_version']}, writable: {r2.set('dispatch:d2_probe', 'x')}")
        r2.delete("dispatch:d2_probe")
        home_b = HOMES / "B_throwaway_6390"
        proc, log = start_server(8766, "redis://localhost:6390/0", home_b, ["--app-dir", "tests", "fakes:make_demo_app"], "B")
        base = "http://127.0.0.1:8766"
        curl("GET", f"{base}/health", auth=False, show=lambda o: {k: o[k] for k in ("status", "model_version", "model_alias", "halted", "redis_ok", "redis_writable", "last_cycle_status", "assets", "auth") if k in o} | {"wiring": {k: v["ok"] for k, v in o["wiring"].items()}})
        curl("POST", f"{base}/signal", {"asset_id": "PILOT_HB_NORTH"}, auth=False)
        curl("POST", f"{base}/signal", {"asset_id": "PILOT_HB_NORTH"}, show=lambda o: {k: o.get(k) for k in ("status", "reasons", "model_version", "feature_age_s", "latency_s")} | {"net_mw": (o.get("setpoint") or {}).get("net_mw"), "interval": (o.get("setpoint") or {}).get("interval_start_utc"), "guard": o.get("guard")})
        curl("POST", f"{base}/kill", {"reason": "manual check: price spike on HB_NORTH", "set_by": "d2"}, show=pick("halted", "reason", "set_by", "persisted", "set_utc"))
        say(f"redis GET dispatch:halt -> {r2.get('dispatch:halt')}")
        curl("POST", f"{base}/signal", {"asset_id": "PILOT_HB_NORTH"}, show=pick("status", "reasons", "detail", "setpoint"))
        curl("GET", f"{base}/health", auth=False, show=pick("status", "halted", "halt", "last_cycle_status"))
        curl("POST", f"{base}/resume", {"reason": "spike over, feeds checked", "cleared_by": "d2"}, show=pick("halted", "was_halted", "cleared_by"))
        say(f"redis GET dispatch:halt -> {r2.get('dispatch:halt')}")
        curl("POST", f"{base}/signal", {"asset_id": "PILOT_HB_NORTH"}, show=pick("status", "reasons"))
        curl("POST", f"{base}/signal", {"asset_id": "NOPE"}, show=pick("detail"))
        curl("GET", f"{base}/attribution?asset_id=PILOT_HB_NORTH&start=2026-10-01&end=2026-10-02", show=pick("reason", "detail", "target"))
        say(f"$ curl -s -X GET {base}/metrics")
        body = subprocess.run(["curl", "-s", f"{base}/metrics"], capture_output=True, text=True, encoding="utf-8").stdout
        keep = [ln for ln in body.splitlines() if ln.startswith(("dispatch_cycles_total", "dispatch_halted", "dispatch_model_info", "dispatch_redis_writable",
                                                              "dispatch_cycle_seconds_count", "dispatch_cycle_seconds_bucket{le=\"30.0\"}", "dispatch_feature_age_seconds"))
                and not ln.endswith(" 0.0") or ln.startswith(("dispatch_halted", "dispatch_model_info"))]
        say("  (metrics, non-zero series only)")
        for ln in keep:
            say("  " + ln)
        from prometheus_client.parser import text_string_to_metric_families
        fams = list(text_string_to_metric_families(body))
        say(f"  /metrics parses as Prometheus text: {len(fams)} families, {sum(len(f.samples) for f in fams)} samples")
        say(f"redis keys dispatch:* -> {sorted(r2.keys('dispatch:*'))}")
        audit_cli(home_b, "verify")
        audit_cli(home_b, "tail")
        say("-- Redis outage: the throwaway Redis is stopped while the service keeps running")
        rproc.terminate()
        rproc.wait(timeout=10)
        say(f"  redis at 6390 {'down' if not wait_port(6390, 1) else 'STILL UP'}")
        curl("POST", f"{base}/signal", {"asset_id": "PILOT_HB_NORTH"}, show=pick("status", "reasons", "detail"))
        curl("GET", f"{base}/health", auth=False, show=pick("status", "halted", "redis_ok", "redis_writable", "last_cycle_status"))
        curl("POST", f"{base}/resume", {"reason": "try to resume blind", "cleared_by": "d2"}, show=pick("detail"))
        results["B"] = stop_server(proc, log, 8766)
        rproc = subprocess.Popen([REDIS_SERVER, "--port", "6390", "--bind", "127.0.0.1", "--save", "", "--appendonly", "no"],
                                 cwd=SCRATCH, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        assert wait_port(6390, 30), "throwaway redis did not restart"

        # -------------------------------------------------------------------------------------- C
        say("")
        say("=" * 100)
        say("C. The same throwaway Redis, the REAL modules (what D0, D1a and D1b have delivered so far)")
        say("=" * 100)
        proc, log = start_server(8767, "redis://localhost:6390/0", HOMES / "C_real_modules", ["dispatch.service.app:create_app"], "C")
        base = "http://127.0.0.1:8767"
        curl("GET", f"{base}/health", auth=True, show=lambda o: {k: o[k] for k in ("status", "model_version", "model_error") if k in o} | {"wiring": {k: (v["ok"], v["detail"][:90]) for k, v in o["wiring"].items()}})
        curl("POST", f"{base}/signal", {"asset_id": "PILOT_HB_NORTH"}, show=pick("status", "reasons", "detail"))
        curl("GET", f"{base}/attribution?asset_id=PILOT_HB_NORTH&start=2026-10-01&end=2026-10-02", show=pick("reason", "detail"))
        results["C"] = stop_server(proc, log, 8767)
    finally:
        for p_, l_, port_ in list(RUNNING):
            stop_server(p_, l_, port_)
        rproc.terminate()
        try:
            rproc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            rproc.kill()
        say(f"throwaway redis stopped; port 6390 {'closed' if not wait_port(6390, 1) else 'STILL OPEN'}")
    finish(results)


def finish(results):
    say("")
    say(f"all servers stopped: {results}")
    (SCRATCH / "manual_check_transcript.txt").write_text("\n".join(OUT) + "\n", encoding="utf-8")
    print(f"transcript: {SCRATCH / 'manual_check_transcript.txt'}")


if __name__ == "__main__":
    main()
