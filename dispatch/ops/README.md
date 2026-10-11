# Operating the dispatch service

This folder is the operate layer of the ERCOT battery platform (PRD Phase 2): the service config, the Prometheus and Grafana
files, and a compose file for when Docker exists. The code is in `dispatch/service/`, `dispatch/guard/`,
`dispatch/execution/` and `dispatch/monitoring/`; the design notes are in `notes/d2.md`.

v1 is **paper only**. Nothing here talks to ERCOT or a QSE: an accepted setpoint is written to a paper ledger, and the QSE
adapter (`dispatch/execution/qse.py`) is disabled. Models only forecast; the LP chooses setpoints and the guard checks them.

## Run it

From `hugging-base/dispatch` (Git Bash; Python is the venv's):

```sh
export PYTHONIOENCODING=utf-8
.venv/Scripts/python.exe -m uvicorn dispatch.service.app:create_app --factory --host 127.0.0.1 --port 8000
```

| Environment variable | Default | Meaning |
|---|---|---|
| `DISPATCH_MODEL_ALIAS` | `paper` | Registry alias to serve: `paper` or `live` (a `research` model is not approved to dispatch, even on paper; the service refuses to start with it). |
| `DISPATCH_REDIS_URL` | `redis://localhost:6390/0` | Halt flag (and D0's online store). The platform's own Redis (`scripts/redis_dispatch.sh`, append-only, so a halt survives a restart); `:6379` belongs to another application and is never used. Credentials in the URL are redacted in `/health`. |
| `DISPATCH_HOME` | `C:/Users/Javier/Repo/BaseP/research-data/dispatch` | The lake (realised prices, features), MLflow in `mlflow/`, and by default the audit log in `runtime/audit/`, the paper ledger in `runtime/ledger/` and the forecast log in `runtime/forecasts/`. |
| `DISPATCH_RUNTIME_DIR` | unset | If set, the audit log, ledger and forecast log go to `<dir>/audit`, `<dir>/ledger`, `<dir>/forecasts` instead. For replays against the real lake that must not enter the live paper ledger. |
| `DISPATCH_ASSETS_FILE` | `ops/assets.example.json` | Assets the service runs cycles for (paper mode only), and the public `disclosure_assets` that `/attribution` can answer for. |
| `DISPATCH_API_TOKEN` | unset | If set, `/signal`, `/kill`, `/resume` and `/attribution` need `Authorization: Bearer <token>`. `/health` and `/metrics` stay open. **Set it on any port someone else can reach.** |
| `DISPATCH_ALLOW_REPLAY` | unset | `1` lets `/signal` take a `now_utc` more than 10 minutes from the server clock. Use it only with a separate `DISPATCH_HOME`. |
| `DISPATCH_MONITOR_INTERVAL_S` | `60` | Seconds between monitoring passes; `0` turns the monitor off. |

Secrets come from the environment only. Run one service process per `DISPATCH_HOME`: the audit log and the ledger have a single
writer.

On startup the service connects Redis, binds the other agents' modules (a missing one is reported, not fatal), loads the model
for the alias, and looks in the newest audit records for an accepted guard decision that never reached the ledger (a crash between the
two writes); each one it finds is written back to the log as an `error` record. **If no model holds the alias it still starts**, and
`/signal` fails closed with `no_model` (it retries the registry at most once a minute while it has none; it never swaps a model that is
running, a restart does that). Every 30 seconds it also compares the loaded version with what the alias names (`registry.aliases()`): if
the alias was cleared (`registry.clear_alias`) or moved to another version, nothing dispatches (`model_stale`, and `model_stale` in
`/health`) until the alias names the loaded version again or the service is restarted.

**Scheduling: `ops/run_paper_loop.py`.** Nothing in the service calls `/signal`. The loop does what the documented flow says, every 15 minutes
(15 seconds after each quarter hour, the decision instant of the training grid and of D1b's backtest, so that Gate 3's slippage check compares
like with like): `flows.prepare_signal(["HB_NORTH"])` (fetch what ERCOT just posted, parse it, rebuild the online features at "now"), then
`POST /signal` for each asset.

```sh
.venv/Scripts/python.exe ops/run_paper_loop.py --once                 # one cycle now, then exit (exit code 2 if the service is down or answers 5xx)
.venv/Scripts/python.exe ops/run_paper_loop.py                        # every 15 minutes until Ctrl-C
.venv/Scripts/python.exe ops/run_paper_loop.py --replay-from 2026-10-08T14:00:00Z --replay-count 20   # 20 past decisions (service: DISPATCH_ALLOW_REPLAY=1 and its own DISPATCH_RUNTIME_DIR)
```

If the refresh does not happen, the features age past `MAX_FEATURE_AGE_S` (20 minutes) and every cycle ends `stale`: that is the designed failure, not a bug.
Nothing starts the loop for you, and nothing leaves it running.

**Redis.** The client speaks RESP2 (`protocol=2`): redis-py 8 opens with `HELLO`, which the machine's Redis 3.0.504 rejects. `localhost` is turned into `127.0.0.1`
(`service/state.normalize_redis_url`): on this Windows machine `localhost` resolves to `::1` first, the platform's Redis listens on IPv4 only, and every new connection
waited 2.0 s for the IPv6 refusal (a real cycle took 2.2 s, 2.0 s of it that wait; it takes 0.08 s without). The service reads D0's online store through one pooled connection. The machine's other Redis (`:6379`, not ours)
was found full on 10 Oct 2026 (108 MB used of a 100 MB `maxmemory`, policy `noeviction`, 23 keys that are not ours), so every write to it fails
with `OutOfMemoryError`. Reads and `DEL` still work. See "Redis refuses writes" below.

## Endpoints

| Endpoint | Purpose |
|---|---|
| `POST /signal` `{asset_id, now_utc?}` | One cycle: halt check, features, forecast, LP, guard, paper dispatch. `now_utc` is for replays. |
| `POST /kill` `{reason, set_by}` | Set the Redis halt flag. The guard rejects every setpoint until it is cleared. |
| `POST /resume` `{reason, cleared_by}` | Clear the halt flag. **Added beyond the PRD table** (a kill switch needs a way back that is audited). |
| `GET /health` | Model version, feature age, last cycle status, halt state, Redis, wiring per module, audit head. With `DISPATCH_API_TOKEN` set, a caller without the token gets the state only (no paths, error texts or halt reasons), so a Docker healthcheck still works. |
| `GET /metrics` | Prometheus text. |
| `GET /attribution?asset_id&start&end` | D1b's attribution of the gap to perfect foresight, interval by interval. For a paper asset the actual dispatch is the paper ledger, clipped to its first executed and last settled interval; for a public pilot (`PAD1_ESS_ESR1`) it is ERCOT's 60-day telemetry from the lake, priced at the hub proxy. The shadow optimizer runs only for windows up to 3 days (`forecast_fn=None` beyond, as D1b asked). `404` with a reason when nothing has settled, `501` while the engine or its inputs are unavailable. |

`/signal` answers `200` for `dispatched`, `rejected`, `halted` and `stale` (the system decided), and `503` for `error` (it could not
complete a cycle). The body is always the cycle result. **Only `"status": "dispatched"` means a setpoint executed.**

| status | meaning | where to look |
|---|---|---|
| `dispatched` | the guard accepted the setpoint and the paper ledger recorded it | |
| `rejected` | the guard refused it; `reasons` are guard codes (`power_limit`, `soc_above_max`, `exposure_cap`, ...) | `proposed_setpoint`, the `guard_decision` audit record |
| `halted` | the halt flag is set, Redis could not be read, or a kill arrived mid-cycle | `detail.halt_reason`, `GET /health` |
| `stale` | features missing (`features_missing`) or older than `MAX_FEATURE_AGE_S`, 20 minutes (`features_stale`) | `feature_age_s`, `dispatch_data_age_seconds` |
| `error` | no model, a stale model (`model_stale`: its alias was cleared or now names another version), no optimizer, forecast or plan failed, plan not optimal, audit or ledger failure | `reasons` and `detail` |

Guard reason codes are listed in `dispatch/guard/guard.py` (`REASON_CODES`) and in `notes/d2.md`.

## Runbook: kill and resume

**Stop dispatch now.**

```sh
curl -s -X POST http://127.0.0.1:8000/kill -H "Authorization: Bearer $DISPATCH_API_TOKEN" -H "content-type: application/json" \
     -d '{"reason":"HB_NORTH price spike, feed looks wrong","set_by":"alice"}'
```

If the service is down or hung, set the flag in Redis directly: the guard reads it on every cycle, so this works from anywhere.

```sh
redis-cli SET dispatch:halt '{"reason":"manual kill","set_by":"alice","set_utc":"2026-10-10T17:00:00Z"}'
```

Check: `GET /health` shows `"halted": true` and who set it; `dispatch_halted` is `1`; the next `/signal` returns `"status": "halted"`.
Every kill is an audit record (`type: kill`).

**Why did it stop?** `GET /health` (`halt.reason`, `halt.set_by`, `halt.set_utc`); `python -m dispatch.execution.audit tail` for the
last audit records. A reason starting `auto-halt after 3 consecutive breaching checks` is the monitor (stale price feed, capture
decay or slippage; the audit has the `breach` records before it), and `auto-halt after 2 consecutive breaching checks: calibration x2` is the
daily calibration check (below). Feature drift never halts. `redis_unreachable` means the service could not read the flag:
the service treats that as a halt.

**Resume.** Fix the cause first. The monitor's breach counts are **not** reset by a resume: if the cause is still there, the next time that
check runs and breaches it halts again (a minute later for the per-pass checks; at the next daily evaluation for calibration, so after a
calibration halt a resume gives you dispatch until then). A new model version, promoted and loaded by a restart, starts the calibration streak afresh.

```sh
curl -s -X POST http://127.0.0.1:8000/resume -H "Authorization: Bearer $DISPATCH_API_TOKEN" -H "content-type: application/json" \
     -d '{"reason":"feed repaired, 3 clean cycles on paper","cleared_by":"bob"}'
```

`/resume` refuses (and the service stays halted) when
- Redis is unreachable (`503`): you cannot clear a flag you cannot read; or
- the audit log does not verify (`409`, with the first bad line): dispatch does not run on a log that cannot be trusted. See below.

Deleting the key by hand (`redis-cli DEL dispatch:halt`) also resumes, but it skips the audit record and the audit check. Use the API.

**Redis is down.** Everything is halted (fail closed), `/health` still answers with `redis_ok: false`. A `/kill` made during the outage is held
in the service's memory and written back when Redis returns; it does not survive a restart of the service while Redis is still down.

**Redis refuses writes** (`redis_writable: false` in `/health`, `dispatch_redis_writable 0`, alert `DispatchRedisReadOnly`). Reads work, so the guard
still sees the flag, but `redis-cli SET dispatch:halt ...` fails, and so does D0's online store (cycles go `stale`). `POST /kill` still halts: the
service holds the kill itself (`"persisted": false` in the reply) and writes it to Redis as soon as Redis accepts writes, but that kill does not survive a
restart of the service. `/resume` works (it only deletes the key). Fix Redis: free memory, raise `maxmemory`, or point `DISPATCH_REDIS_URL` at an
instance of its own. Do not flush keys that are not ours.

**Audit log problems.** `python -m dispatch.execution.audit verify` prints `audit OK` or the first bad line. A *torn* last line (a crash
in the middle of a write) makes appends refuse: cycles end in `error` / `audit_failed` and nothing executes. Repair by moving the
partial last line out of `runtime/audit/audit-YYYYMMDD.jsonl` into a file next to it, then verify again. Never edit records in the middle:
that breaks the hash chain, which is the point. Cutting the tail off a log is invisible to a bare hash chain, so the service also keeps
the head `(seq, hash)` in Redis (`dispatch:audit:head:<id>`); `/resume` and `/health` compare the log with it. After restoring a log from a backup,
delete that key. Retention is an open question in the PRD, so nothing is ever deleted (`AUDIT_RETENTION_DAYS = None`).

**Replays.** Replays write to the paper ledger like live cycles do. Run them against their own `DISPATCH_HOME` and set
`DISPATCH_ALLOW_REPLAY=1` only there.

## Monitoring policy

DESIGN.md "Monitoring policy" (lead decision, 10 Oct 2026), as implemented in `dispatch/monitoring/`:

| Check | Runs | Breach | What a breach does |
|---|---|---|---|
| stale data | every pass (60 s) | a live report older than its allowance (cadence + normal lateness + 5 min, `checks.py`) | **NP6-905-CD** (RT prices) and **NP4-190-CD** (day-ahead prices) halt after 3 consecutive passes. Every other watched report (NP6-331-CD, NP6-322-CD, NP6-86-CD, NP3-565-CD, NP4-188-CD) is an alert only |
| feature drift (PSI) | every pass, once 96 rows are served | PSI above 0.2 | **alert only**: the metric `dispatch_feature_drift_psi` and the alert `DispatchFeatureDrift`. It never counts toward a halt and asks for no retrain (`config.DRIFT_PSI_HALTS = False`: D1a measured 87 to 100% of features above 0.2 on in-distribution windows) |
| capture-rate decay | every pass | 20% under the baseline | 3 consecutive passes halt; the first breach records a retrain request |
| slippage (Gate 3) | every pass | paper vs backtest over 7 days above 5% | 3 consecutive passes halt |
| **calibration** | **once a day** (`CALIBRATION_EVAL_EVERY_S`) | over the last 672 settled intervals: the share of realised prices at or below the issued P10/P50/P90 is more than 0.25 off 0.1/0.5/0.9, or the P50's mean absolute error is more than 10% above the 7-day profile median's | **one breach: an alert plus a retrain request** (recorded in the audit log, nothing trains). **Two consecutive daily breaches set the halt flag.** The pinball-against-holdout rule (above +25%) is alert only |

The calibration check reads the **forecast log**: every cycle appends its issued P10/P50/P90 for the 96 intervals it planned, plus the model's
7-day profile median `rt_prof7_med`, to `runtime/forecasts/forecasts-YYYYMMDD.jsonl`. A day or a week later it joins them with the realised prices
(`data.prices.price_source`, read one UTC day at a time) and scores every settled (forecast, interval) pair of the **loaded model version** with
D1a's `models.cv.calibration_check`. It needs at least half of the window settled; before that it says why (`GET /health`, `calibration.last_skip`) and
neither counts nor resets. The date of the last evaluation and the streak live in `runtime/forecasts/calibration-state.json`, so a restart neither
forgets a breach nor evaluates twice in a day, and a new model version starts with none. Each evaluation is appended to
`calibration-evaluations.jsonl`.

False-breach rate of these thresholds on D1a's holdout (1 Jan 2025 to 26 Sep 2026, 628 daily evaluations per hub, no fault injected, so every
breach is a false one): 2.1% (HB_NORTH), 2.2% (HB_WEST), 2.1% (HB_HOUSTON), 2.2% (HB_SOUTH), against the 5% bar; the first guess (hit tolerance 0.10,
pinball +25%) breached 42 to 46%. The 4 to 10 breach episodes per hub cluster around Winter Storm Fern, where the model really loses to the profile
baseline, and 3 of them per hub were two days or longer, so 3 halts per hub in 21 months. A $10/MWh bias is caught in 93 to 95% of windows. It barely
notices bands that are too narrow or a forecast a day old (stale inputs are the feature-age gate's job). `tests/test_calibration.py` repeats this replay.

**When it breaches.** `GET /health` shows `calibration.last_evaluation` (hit rates, skill ratio, which rule). Look at the "Calibration" panels in
Grafana. A first breach is a prompt to look and to retrain (`python -m dispatch.models.train all --source d0 --tag rN --threads 1`, then
`python -m dispatch.registry.mlflow_registry check/promote`); a second halts. After a retrain and promotion, restart the service: it loads the new
version and the calibration check starts afresh. `POST /resume` does not clear the streak: if the model is still bad the next daily evaluation halts again.

## Metrics

Defined in `dispatch/monitoring/metrics.py`; `tests/test_monitoring.py` fails if an alert rule or a dashboard panel refers to one that is not.

| Metric | Type | Meaning |
|---|---|---|
| `dispatch_cycle_seconds` | histogram | wall time of one cycle; a `le="30"` bucket exists for the p99 alert |
| `dispatch_cycles_total{status}` | counter | `dispatched`, `rejected`, `halted`, `stale`, `error` |
| `dispatch_guard_rejections_total{reason}` | counter | one per reason code on a rejected setpoint |
| `dispatch_halted` | gauge | 1 while halted |
| `dispatch_data_age_seconds{report}` | gauge | from `data.catalog.freshness` |
| `dispatch_data_age_allowance_seconds{report}` | gauge | age at which that report is stale (cadence plus `DATA_FRESHNESS_TARGET_S`) |
| `dispatch_feature_drift_psi{feature}` | gauge | PSI against the reference frame stored with the model |
| `dispatch_capture_rate`, `dispatch_capture_baseline` | gauge | rolling paper / perfect-foresight revenue, and what it is compared with |
| `dispatch_slippage_ratio` | gauge | `abs(paper - backtest) / backtest` over the replayed period (Gate 3) |
| `dispatch_model_info{version,alias}` | gauge | 1 for the loaded model |
| `dispatch_feature_age_seconds{asset}` | gauge | age of the features the last cycle used |
| `dispatch_monitor_breaches_total{check}`, `dispatch_monitor_consecutive_breaches{check}` | counter, gauge | breaches seen (alert-only ones included), and the streak the halt policy counts (alert-only breaches never count) |
| `dispatch_monitor_halt_threshold{check}` | gauge | consecutive breaching evaluations that halt that check: 3, and 2 for `calibration` |
| `dispatch_calibration_hit_error{asset,quantile}` | gauge | \|share of realised prices at or below the issued quantile - its level\|, last daily evaluation (breach above 0.25) |
| `dispatch_calibration_skill_ratio{asset}`, `dispatch_calibration_pinball_ratio{asset}` | gauge | P50 MAE over the 7-day profile median's (breach above 1.10), and pinball over the model's holdout pinball (alert only above 1.25) |
| `dispatch_calibration_breach{asset}`, `dispatch_calibration_window_intervals{asset}` | gauge | 1 if the last evaluation breached a halt-eligible rule; settled intervals it scored |
| `dispatch_calibration_last_evaluation_timestamp_seconds` | gauge | unix time of the last evaluation (the service start until the first) |
| `dispatch_forecast_log_errors_total` | counter | forecast-log writes that failed (cycles still run; the calibration check starves) |
| `dispatch_audit_errors_total` | counter | audit appends that failed |
| `dispatch_redis_writable` | gauge | 1 if Redis accepts writes |

Capture rate, slippage, calibration and `/attribution` need realised prices, a backtest and the forecast log. With no arguments `create_app()` binds
them from the real modules: D0's `data.prices.price_source`, D1b's `make_backtest_fn` over D1a's point-in-time `make_forecast_fn` (built lazily
on the monitor's thread once a model is loaded; the first call after a start takes about 90 s for a week and every later one 0.05 s, so it is never
called from a request handler). `GET /health` lists each target under `wiring`; one that cannot be bound leaves its checks `skipped` and its endpoint
`501`, and does not make the service `degraded`. The providers remember a week of prices and the perfect-foresight solve for the 15 minutes the
window stays put; the ledger replay is re-read every pass, so an edited ledger is noticed within a minute. Stale data runs from the start (it watches
only the live inputs, `MONITORED_REPORTS`). Drift runs once the loaded model carries a `reference_frame`.

## Alerts

`alerts.yml`: `DispatchHalted`, `DispatchServiceDown`, `DispatchGuardRejectionRate`, `DispatchRedisReadOnly`, `DispatchAuditWriteFailures`,
`DispatchCycleLatencyP99` (> 30 s), `DispatchStaleData`, `DispatchFeatureDrift` (PSI > 0.2, alert only), `DispatchCaptureRateDecay`
(20% under baseline), `DispatchSlippageOverTolerance` (> 5%), `DispatchAutoHaltApproaching` (one short of the check's own halt threshold),
`DispatchCalibrationBreach`, `DispatchCalibrationPinball` (alert only), `DispatchCalibrationNotRunning` (no evaluation for 3 days),
`DispatchForecastLogWriteFailures`. Thresholds that mirror
a constant say which one in a comment. There is no Alertmanager in `prometheus.yml`; add one to page someone. No `promtool` was available when
these were written, so the expressions were checked for the metrics they use and for balanced brackets, not by Prometheus.

## Gate 3: paper matches backtest

1. Choose a period with realised prices. Run the backtest (D1b's walk-forward harness) with the registered model.
2. Replay the same decision times into a fresh paper ledger: `dispatch.service.cycle.replay_cycles(asset, times, deps, prepare=...)`, where
   `prepare(t)` writes the point-in-time features for `t` to the online store.
3. `dispatch.monitoring.checks.gate3(ledger, spec, prices, backtest, start_utc=..., end_utc=...)`. It replays the ledger **file**
   (SOC, limits, revenue through `dispatch.units`) and returns `passed`, `slippage_ratio`, and the ten biggest per-interval gaps.
   Pass means slippage <= `SLIPPAGE_TOL` (5%) and the ledger is internally consistent.
4. `record_gate3_result(result, version)` writes the outcome on the model version in the registry (`registry.record_gate3`: tags `gate3.pass`,
   `gate3.utc`, `gate3.by`, `gate3.evidence`), which is what D1a's promotion to `live` requires, and in the audit log (`type: gate3`).

`tests/test_monitoring.py` does this end to end: same model and prices give slippage 0; skipped cycles, a guard that interferes, a derated asset or an
edited ledger fail it.

## Files

| File | What |
|---|---|
| `assets.example.json` | The assets (one paper asset, `PILOT_HB_NORTH`: HB_NORTH, 10 MW / 20 MWh, RTE 0.85, losses on charging; and the public pilot `PAD1_ESS_ESR1` under `disclosure_assets`, for `/attribution` only) with a label on every assumption |
| `prometheus.yml`, `alerts.yml` | Scrape config and alert rules |
| `grafana/dashboards/dispatch.json` | The dashboard (state, cycles, latency p50/p99, guard rejections, data age, drift, capture, slippage, calibration hit-rate error and skill/pinball ratios) |
| `run_paper_loop.py` | The paper loop: `prepare_signal`, then `POST /signal`, every 15 minutes, or `--once`, or a `--replay-from` of past decisions. Never left running. |
| `grafana/provisioning/` | Data source and dashboard provider for Grafana |
| `manual_check.py` | The manual check D2 ran: real uvicorn, curl and Redis 3.0 (the machine's, a throwaway one, the real modules); starts and stops everything it uses. |
| `docker-compose.yml`, `Dockerfile`, `.env.example` | Redis, the service, an MLflow UI on the same sqlite file, Prometheus, Grafana. **Never run**: this machine has no Docker. |

With Docker: `cp ops/.env.example .env`, edit it (`DISPATCH_API_TOKEN` and `GRAFANA_ADMIN_PASSWORD` have no defaults on purpose), then
`docker compose -f ops/docker-compose.yml --env-file .env up --build`. Relative host paths in the compose file are relative to `ops/`;
set `DISPATCH_HOME` in `.env` to an absolute path. Ports bind to `127.0.0.1`.
