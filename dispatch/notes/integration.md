# I1 notes: platform integration (wiring, hardening, the live run)

Written 10 Oct 2026 by I1, after D0, D1a, D1b and D2 had finished and while D0 was adding D1a's 41 columns to `rt_v1` as 1.1.0. Everything below was
measured on this machine on that date, on a machine shared with a research agent, D0's resumed work and another agent's test runs (free memory fell to
1.3 GB of 9.9 GB at times). Labels follow the repo's style: REAL (measured or read from a named source), DERIVED, ASSUMPTION. I did not touch D0's, D1a's or
D1b's logic. Outside my own folders I made one code fix, in D2's audit log (section 2.6), and test-only edits in D1a's `test_models.py` (one stale assertion, section 2.6), D2's `test_guard_props.py`
(derandomised) and `test_audit.py` (a regression test), plus the `mlflow_registry` fixture in the lead's `tests/conftest.py`. Nothing is committed to git; no server or poller is left running.

## 1. Result

| Item | Result |
|---|---|
| Monitoring policy (task 1) | Feature PSI alert-only; the daily calibration check with its forecast log (one breach alerts and asks for a retrain, two consecutive daily breaches halt); D0's stale-data allowances, and only NP6-905-CD and NP4-190-CD can halt for staleness. **False-breach rate on D1a's holdout: 2.07% HB_NORTH, 2.23% HB_WEST, 2.07% HB_HOUSTON, 2.23% HB_SOUTH** (bar 5%; section 3) |
| Wiring (task 2) | All ten targets bind to the real modules (D0 `price_source`, D1b `make_backtest_fn` over D1a's `make_forecast_fn` through D0's features, `attribute`, D0's `compat` adapter, ...). `/attribution` works for the paper asset (ledger) and for the public pilot (D0's `esr_sced`): **the pilot over its 42 days reproduces the case study to the cent in 6.8 s** (actual $43,266.39, perfect foresight $149,552.74, gap $106,286.34, every component equal). `ops/run_paper_loop.py` with `--once` and `--replay-from` |
| Full suite, twice (task 3) | Run A: **618 passed, 3 skipped (opt-in), 0 failed in 641.98 s (10 min 42 s)**. Run B, straight after the live run on the final tree: **618 passed, 3 skipped, 0 failed in 623.07 s (10 min 23 s)**. The slowest tests are D0's (leakage 55 s, equivalence 46 s, the 41-column oracle 34 s), then D1a's training pipeline (25 s). Collected 621 tests in 18 files; the skips are the real-lake Gate 1 and two full-history equivalence runs, opt-in by D0 |
| Integration test (task 5) | `tests/test_integration_cycle.py`, 15 tests, 46 to 50 s: dispatch, kill, resume, attribution rows, calibration halt after two bad daily evaluations, the lazy backtest equal to the ledger, fail-closed reasons, the audit chain verifying at the end of every scenario |
| Model and promotion (task 6) | D0's `rt_v1` 1.1.0 equals D1a's interim columns (differences 0.0), so **no retrain**: version 1 is served as registered. `registry check 1 paper`: 45 of 45 checks pass. **Version 1 promoted to `paper` at 2026-10-11T00:20:07Z by i1**; `research` stays on version 1, `live` untouched, no `gate3.pass` tag written |
| Live end to end (task 6) | Real uvicorn, the platform Redis, the real lake, the registered model, the real LP: `prepare_signal(["HB_NORTH"])` then `POST /signal` for `PILOT_HB_NORTH` is **`dispatched`** (net +10 MW with a 10 MW REGDN award, guard accepted, feature age 102 s, 0.17 s). `/health` ok, `/metrics` 30 families, `/attribution` correctly `no_data` (nothing has settled yet). 96 replayed decisions (a day, 9 to 10 Oct): **96 of 96 dispatched, server latency p50 0.107 s, p99 0.194 s** against the 30 s target. Server stopped each time; port 8765 closed |
| Gate 3 on the real stack | The replayed day, paper ledger against D1b's backtest of the same window with the same model, prices and code: **paper $1,333.27, backtest $1,333.27, slippage 0.0%** (limit 5%), 96 intervals, no replay problems |
| Gates | **1 PASS** (D0); **2 mechanically met, not proven** (D1b: 0.0% unexplained by construction, 11.1% proxy stress); **3 met on a replayed day** (above), evidence only: nothing recorded for `live` |

## 2. What I changed, and why

### 2.1 Monitoring policy (DESIGN.md "Monitoring policy", lead decision of 10 Oct)

| Rule | Where | What it does now |
|---|---|---|
| Feature PSI is alert-only | `monitoring/checks.py` (`Breach.halts`, `check_halts`), `halt_policy.py` | `config.DRIFT_PSI_HALTS = False`. The metric `dispatch_feature_drift_psi` and the alert `DispatchFeatureDrift` stay. A PSI breach is recorded (first of a streak, then one per 360 passes) and never counts toward a halt, **also if a caller builds a drift `Breach` that claims it can halt**: the policy asks `check_halts(check)` itself. It also no longer asks for a retrain: D1a measured 87 to 100% of the features above 0.2 on in-distribution windows, so a request per streak would be a request per pass |
| Daily calibration check | `monitoring/calibration.py`, `checks.evaluate_calibration` | Once a day over the last `CALIBRATION_WINDOW_INTERVALS` (672) settled intervals, D1a's `models.cv.calibration_check` with `CALIBRATION_HIT_TOL` (0.25) and `SKILL_DEGRADATION_MAX` (0.10). The pinball rule runs separately (`PINBALL_DEGRADATION_MAX`) and can only set an alert |
| One breach: alert plus retrain request | `halt_policy.py` | The first breach of a streak writes a `breach` and a `retrain_request` audit record (`trains: false`) and moves `dispatch_calibration_breach`. Nothing trains |
| Two consecutive daily breaches halt | `halt_policy.py` | Per-check thresholds: 3 passes for stale data, capture decay and slippage; `CALIBRATION_HALT_AFTER` = 2 for calibration. The reason reads `auto-halt after 2 consecutive breaching checks: calibration x2: ...` |
| Stale data: who can halt | `monitoring/checks.py` | Only NP6-905-CD and NP4-190-CD (section 2.3) |

Design points that are mine, with the reason:
* **The check runs once a day, not once a pass.** The monitor passes every 60 s, so the check is a provider that answers "next daily evaluation in 19.0 h"
  between evaluations; the runner marks those passes `skipped`, which neither counts nor resets. The date of the last evaluation and the streak live in
  `runtime/forecasts/calibration-state.json` (and every evaluation in `calibration-evaluations.jsonl`): without that, two restarts in a day would be
  "two consecutive daily breaches", and a restart would forget a breach. The halt policy's count is restored from it at start (`HaltPolicy.restore`).
* **Only the loaded model version is scored, and a new version starts clean.** Last week's forecasts say nothing about a model promoted today; after a
  retrain, promotion and restart the streak is zero and the schedule restarts (`CalibrationMonitor.sync_model`).
* **A thin window is not judged** (`CALIBRATION_MIN_COVERAGE = 0.5` of the window's intervals settled, `CALIBRATION_MIN_ROWS = 96`; both ASSUMPTIONS in
  `checks.py`, REQUEST to move them to `config.py`). D1a's false-breach rate was measured on full 7-day windows; a window of one day is one regime. Before the
  threshold is reached the check says why (`GET /health` `calibration.last_skip`) and looks again in an hour, not every pass.
* **An evaluation that crashes counts once, for the day** (a check that cannot run is a doubt, but a daily check doubts once a day): two consecutive daily
  crashes halt, a pass-by-pass crash would have halted in two minutes.
* **Realised prices are read one UTC day at a time.** `price_source` is complete-or-nothing for a window, so a gap in the lake would have cost the whole week;
  now it costs the day. One call is 0.2 to 0.4 s on the real lake.

### 2.2 The forecast log

`monitoring/forecast_log.py`. Every cycle appends one line to `runtime/forecasts/forecasts-YYYYMMDD.jsonl` (UTC day of the issue time): asset, settlement point,
model version, issue time, the first interval, and for each of the planned intervals the issued P10, P50, P90 and the model's `rt_prof7_med` (the 7-day
profile median it anchors on, taken from the feature frame, aligned on the interval). About 3 KB a line, 96 lines a day. Append only; a torn last line is
skipped by the reader and the next record starts on a new line; a write that fails is counted (`dispatch_forecast_log_errors_total`, alert
`DispatchForecastLogWriteFailures`) and never stops a cycle, because dispatch safety does not depend on this file, only the calibration check does. By default the
log lives next to the audit log (`<audit dir>/../forecasts`), so every test that injects an audit directory keeps its forecast log in the same temp folder.

### 2.3 Stale-data allowances and who can halt (D0's REQUEST, `notes/d0.md` section 7)

Allowance = cadence + `REPORT_EXTRA_ALLOWANCE_S` + `DATA_FRESHNESS_TARGET_S` (300 s). Applied exactly as D0 tabulated, for both id forms (NP code and reportTypeId):

| Report | cadence | extra | allowance | longest gap D0 saw | can halt |
|---|---|---|---|---|---|
| NP6-905-CD (RT prices) | 900 | 900 | 2,100 s | 1,285 s | **yes** |
| NP6-331-CD (RT ancillary prices) | 900 | 900 | 2,100 s | 1,287 s | alert only |
| NP6-322-CD (SCED lambda) | 300 | 900 | 1,500 s | 905 s | alert only |
| NP6-86-CD (SCED shadow prices) | 3,600 | 3,600 | 7,500 s | 3,601 s | alert only |
| NP3-565-CD (load forecast) | 3,600 | 3,600 | 7,500 s | 3,600 s | alert only |
| NP4-190-CD (DAM prices) | 86,400 | 14,400 | 101,100 s | 90,153 s | **yes** |
| NP4-188-CD (DAM ancillary prices) | 86,400 | 14,400 | 101,100 s | 90,154 s | alert only |

The lead said "NP6-905-CD, and NP4-190-CD if D0's evidence supports it". It does: 32 days, the longest gap (25 h 2 min) sits 3 h inside an allowance of 28 h 5 min, so
a breach means a day was really missed. One finding to hand back: **the served model reads NP4-188-CD (day-ahead ancillary prices, 19.6% of its gain) and not
NP4-190-CD** (D1a's model has no day-ahead energy price). I kept the lead's literal list because the two reports post together (identical gaps in D0's measurements),
so a missed day in one is a missed day in the other and the NP4-190-CD halt covers both in practice. If the lead wants the halt on the report the model reads, it is
one line in `HALT_ELIGIBLE_REPORTS`. Alert-only breaches reach the metrics (`dispatch_data_age_seconds` against its allowance), the Prometheus alert and one audit
record per streak.

### 2.4 Wiring of the real modules

`service/wiring.py` gained five optional targets, `app.py` uses them when `create_app()` is given nothing:

| Target | Is | Used for |
|---|---|---|
| `price_source` | D0 `data.prices.price_source` | capture rate, Gate 3 slippage, the calibration check, `/attribution` |
| `make_backtest_fn` | D1b `optimizer.backtest.make_backtest_fn` | `LazyBacktest`: built on the monitor's thread on first use, once a model is loaded; **never in a request handler** (a cold week is ~90 s; later calls ~0.05 s from the memo). With no model it raises `LookupError`, which the slippage provider reads as "no data" |
| `make_forecast_fn` | `wiring.pit_forecast_fn` = D1a `models.replay.make_forecast_fn` over D0's `features.build.point_in_time` and D0's `compat.for_forecaster` | the shadow optimizer, the backtest, `/attribution`: the same model and feature code as a live cycle. D1a's own default for a model trained on its interim builder reads a static archive that ends on 26 Sep 2026 |
| `adapt_features` | D0 `features.compat.for_forecaster` | the cycle renames D0's `rt_v1` columns to the names an interim-trained model reads before `predict`; it does nothing for a model trained on D0's names. A failing adapter is `forecast_failed` |
| `attribute` | D1b `attribution.engine.attribute` | `/attribution` |

None of the five makes `/health` "degraded" when absent (a service without them dispatches as before and says which checks are skipped). All ten targets bind on this
machine. The optimizer is also warmed up at start (`lp.warmup`: the first solve in a process is 3 s, the next ones 30 ms; a new thread costs another 0.1 s once).

`/attribution` (`service/app.py`, `service/disclosure.py`):
* a **paper asset**: actual dispatch = the paper ledger (`as_actual`, absent rows are idle). With the lake's price source the window is clipped to the first executed
  interval (before it there was no paper asset, and "idle" would count the whole market as missed) and to the last interval whose prices are final (one hour after it ends); the shadow
  optimizer runs only for windows up to 3 days (`forecast_fn=None` beyond, as D1b asked);
* the **public pilot** `PAD1_ESS_ESR1` (under `disclosure_assets` in `ops/assets.example.json`; not a paper asset: `/signal` answers 404): actual dispatch = D0's `esr_sced`
  telemetry through D1b's `aggregate_runs`, prices = D0's `price_source` at the hub proxy, day-ahead position from D1b's DAM cache. **Over the 42 days it reproduces the case
  study to the cent** (section 4).

Also: `DISPATCH_RUNTIME_DIR` (audit log, paper ledger and forecast log elsewhere than `DISPATCH_HOME/runtime`, for replays against the real lake that must not enter the live
ledger); the capture-rate and slippage providers remember a week of prices and the perfect-foresight solve for the 15 minutes the window stays put (the ledger replay is still
re-read every pass, so an edited ledger is noticed within a minute); `/attribution` snaps `start` and `end` to the 15-minute grid.

### 2.5 The paper loop

`ops/run_paper_loop.py`: `flows.prepare_signal([sp])` once per settlement point, then `POST /signal` per asset, every 15 minutes, or `--once`, or `--replay-from ISO --replay-count N`
(past decisions: features as of each instant, `now_utc` in the request). One JSON line per cycle with the client and server latency, and a summary. Exit code 2 if the service is
down or answers 5xx. It never starts the service and is never left running.

**When it fires is a decision, and I chose the start of the interval (15 s after the quarter hour).** ERCOT posts an interval's price file about 2 minutes after it ends. A cycle at
:04 would know a price that the backtest, which decides at every interval start, cannot, and Gate 3's slippage check (which halts at 5%) would count that as a fault. The training
grid is also on the boundary. The price is that the newest price the features know is 13 minutes old (D0: 778 s at the quarter hours, inside the 20-minute gate). REQUEST to D1b / D1a:
if fresher decisions are wanted, `Backtest` needs a decision offset and the model needs training at that age (D0's `--offset 7min30s`).

### 2.6 Bugs found and flaky tests

| What | Before | After |
|---|---|---|
| `execution/audit.py` `verify_report` (**D2's file, a minimal fix**) | A tampered number that parses to infinity (one digit of `262144.9998005644` changed to `e`: `262144.9998e05644`) made `record_hash` raise `ValueError: Out of range float values are not JSON compliant: inf`, so `verify()` raised instead of reporting, and `/resume` would have answered 500. Found by `test_any_change_to_the_content_of_the_file_is_detected` (Hypothesis) in my first full run, 1 failure in 207 | The record is reported as edited ("its content cannot be hashed") with its line number. Regression test `test_a_tampered_number_that_overflows_is_reported_as_an_edited_record_not_raised` |
| `test_guard_props.py::test_generator_reaches_both_outcomes` (D2) | Drew 1,500 fresh random cases each run and needed at least 200 acceptances. Over seeds 0 to 7 the acceptances were 203, 242, 246, 252, 255, 287, 336, 342: the bar sat inside the noise (once 198, failed). Bars unchanged | `derandomize=True`: the same 1,500 cases on every run, 268 acceptances, 1,232 rejections, 16 reason codes. The property is not weakened (same bars, same generator) |
| `config.REDIS_URL` = `redis://localhost:6390/0` (lead's file; fixed in the service, REQUEST below) | Found by profiling the first real cycle: it took 2.19 s, and 2.03 s of that was `socket.connect`. On this Windows machine `localhost` resolves to `::1` first; the platform's Redis listens on 127.0.0.1 only; each new connection waited for the IPv6 refusal (measured 2.011 s to `localhost:6390`, 0.001 s to `127.0.0.1:6390`), and D0's `online.read_latest(sp)` opens a new connection per call | `state.normalize_redis_url` turns `localhost` into `127.0.0.1` for the halt flag, and the service reads D0's store through one pooled client (`ModuleOnlineStore(client=)`, D0's function already takes `client=`). The same cycle takes **0.08 s** (first call 0.85 s). The scripts do the same for D0's `prepare_signal`. Regression tests (`test_integration_cycle.py`): `test_localhost_becomes_127_0_0_1_so_a_new_connection_does_not_wait_for_the_ipv6_refusal`, `test_the_real_online_store_function_gets_one_long_lived_client_and_fakes_keep_their_own_transport` |
| `HaltPolicy.evaluate` (`monitoring/halt_policy.py`) | `out.tripped` listed every check whose count was at its threshold, whether or not it ran in this pass. For the daily calibration check that meant a halt it had caused was set again by the very next minute-pass after `POST /resume` (the count stayed at 2 until the next daily evaluation), contradicting "an operator clears it with /resume" | Only checks that ran in the pass can trip. A resume gives dispatch back until the next daily evaluation, which halts again if the model is still bad; per-pass checks (stale data, slippage) still halt again at the next pass if the cause is still there. Regression tests: `test_a_resume_after_a_calibration_halt_holds_until_the_next_daily_evaluation` and `test_a_per_pass_check_that_still_breaches_halts_again_at_the_very_next_pass_after_a_resume` (`test_calibration.py`), and the resume step of `test_two_bad_daily_calibration_evaluations_halt_dispatch_and_one_only_alerts` (`test_integration_cycle.py`) |
| `test_models.py::test_decision_value_runs_on_the_real_lp_and_shares_the_perfect_foresight_run` (D1a) | D1b saw it fail once while the machine was saturated; the brief says its timeout or deadline needs fixing. **It has none**: no pytest-timeout is installed, the test has no `@given` (every Hypothesis test in the suite has `deadline=None`), neither `cv.decision_value` nor `lp.solve` reads a clock (the only time limit in `lp.py` is the 30 s cap on the rare MILP path), and the test's data is deterministic. I could not make it fail: alone 3.9 s; under 10 busy-loop processes on 8 cores, 6 runs of 6 passed, taking 8 to 54 s; in both final full runs, with other agents' full runs alongside and free memory down to 1.3 GB of 9.9 GB, it passed and never appeared in the 30 slowest | **Unchanged.** There is nothing to fix in a timeout or deadline, and changing the assertions or `lp.py` would not be a fix for a failure I cannot see. The only cause left that fits D1b's own note (a second run stalled with the machine's memory nearly exhausted by two training processes) is memory pressure on the machine, which no test setting cures. If it fails again, the traceback decides |
| `test_models.py::test_d0_feature_names_map_the_model_onto_rt_v1_plus_rt_v2` (D1a; not flaky, **made stale by D0's 1.1.0**) | Written when `rt_v1` lacked the 41 columns of section 10 and asserted the gap ("rt_v1 alone is refused"); the only failure of my first full run (610 passed, 1 failed). D0 left it to D1a or the lead (`notes/d0.md` section 13) | Inverted, same names and same two choices: nothing is missing from `sets.FEATURES` (82 columns), `choose_source` answers `d0` for a frame that has them, and still answers `interim` for a frame without the added columns. The docstring says what changed and why |

### 2.7 Tests I added or changed

`tests/test_calibration.py` (41 tests, about 40 s of which 25 s is the holdout replay): a breach for each rule (hit rate below P10, P50 and P90, the skill rule), the exact tolerance, the pinball rule alerting and never halting,
one breach alerting and asking for a retrain, two consecutive daily breaches halting through the real cycle, a clean day resetting, PSI never halting in 1,010 passes (also when a
Breach claims it can), the forecast log (round trip, duplicates, a torn line, a failing disk), the daily schedule, the loaded model only, a restart, days without prices, a crashing
evaluation, and **the replay over D1a's holdout forecasts (`registry.holdout_forecasts`): the false-breach rate per hub is under 5%** (section 3) and a $10/MWh bias is still seen.
`tests/test_integration_cycle.py` (15 tests, 46 to 50 s): section 4. `tests/conftest.py`: the `mlflow_registry` fixture only (a temp sqlite store that the module-level `registry.load /
promote / aliases` follow, restored afterwards), at D1a's request. `tests/test_monitoring.py`, `test_api.py`, `test_audit.py`: updated for the new allowances, the alert-only
semantics, the wiring set, plus the regression test above.

### 2.8 Not done, on purpose

* No change to D0's, D1a's or D1b's logic. D0's `compat.py`, `windows.py` and the 41 columns are D0's work in progress; I only call `compat.for_forecaster`.
* No retrain unless section 5 says so, no promotion beyond what section 5 records, nothing committed.

## 3. The calibration check against D1a's holdout

`tests/test_calibration.py` replays the monitor over `registry.holdout_forecasts(version, hub)` for the version that holds `research` (version 1): D1a's walk-forward forecasts of
1 Jan 2025 to 26 Sep 2026 (364,704 rows per hub, decisions every 4 h at +2 h, all 96 horizons, Winter Storm Fern inside), one evaluation per day over a 7-day window, config thresholds
(`monitoring.calibration.replay_windows`, the same `evaluate_calibration` the service calls). The model never saw this period and no fault is injected, so every breach is a false one.

| hub | daily evaluations | false-breach rate | bar | breach episodes | would have halted (2+ days in a row) | pinball alert rate (alert only) | +$10/MWh bias: windows in breach |
|---|---:|---:|---|---:|---:|---:|---:|
| HB_NORTH | 628 | **2.07%** | < 5% | 5 | 3 | 16.9% | 93% |
| HB_WEST | 628 | **2.23%** | < 5% | 10 | 3 | 15.0% | 67% |
| HB_HOUSTON | 628 | **2.07%** | < 5% | 4 | 3 | 16.2% | 95% |
| HB_SOUTH | 628 | **2.23%** | < 5% | 6 | 3 | 17.0% | 96% |

These are D1a's numbers (2.1 to 2.5%), reproduced by the platform's own code path rather than by D1a's `false_breach_rate`. What the three halts per hub are (REAL dates of the evaluations): the week of
31 Mar to 3 Apr 2025 (HB_NORTH 4 days, HB_SOUTH 28 Mar to 2 Apr, HB_HOUSTON 1 to 3 Apr), the Winter Storm Fern window (26 to 27 Jan and 2 to 6 Feb 2026 at HB_NORTH; 25 to 27 Jan at HB_SOUTH;
25 to 26 Jan and 28 Jan to 3 Feb at HB_HOUSTON), and at HB_WEST 26 to 27 Jan, 22 to 23 Feb and 19 to 21 May 2026; HB_SOUTH also 28 to 29 Mar 2026. In each, prices ran above the forecast for days
(D1a, `notes/d1a.md` section 7.2): a halt a person would probably want, **but 3 per hub in 21 months is a halt every 7 months, and each needs a human to resume it**. Single-day breaches
(HB_NORTH 8 Feb and 7 Jun 2026, most of HB_WEST's) are only alerts plus a retrain request. A fault of +$10/MWh (about three quarters of the P50 MAE) is in breach in 67 to 96% of windows, which is
what the check is for; it barely notices bands that are too narrow or a forecast a day old (D1a's table; stale inputs are the feature-age gate's job).

Not tested: a monitor that sees the model's forecasts as they were issued (the replay uses the walk-forward table, D1a's stand-in); the first real window will be 3.5 days before the check can evaluate at all.

## 4. The integration test

`tests/test_integration_cycle.py`, 15 tests, about 50 s. A real two-second LightGBM model on synthetic prices (D1a's `fit_tiny_forecaster`) is **registered and promoted to `research` and `paper` in a temp
MLflow registry** (`served` for the module, the shared `mlflow_registry` fixture for one test); the service loads it through the alias like the real one does. D0's online store runs on fakeredis (the same
`OnlineStore` code that talks to the platform Redis), the LP is D1b's `lp.solve` (HiGHS, with awards), the guard, cycle, paper ledger, audit log, halt flag (the Redis key), API, monitor, forecast log and
calibration check are the real ones, and `/attribution` is D1b's engine. Only the market is synthetic: the prices, and the feed, which the test replaces by writing point-in-time features into the store before
each cycle, as `flows.prepare_signal` does.

| Test | Shows |
|---|---|
| `test_a_signal_runs_the_whole_chain_and_the_paper_ledger_agrees` | a signal **dispatches**; the audit trail is `startup, model_load, signal, guard_decision, setpoint, cycle`; the ledger holds the reported setpoint and carries SOC from cycle to cycle over 7 cycles; the forecast log holds 96 intervals with `rt_prof7_med`; **the chain verifies** |
| `test_a_kill_halts_the_next_cycle_and_a_resume_brings_it_back` | `/kill` halts the next cycle (`halted`, nothing executed, the key is in Redis), `/resume` brings dispatch back, both audited |
| `test_attribution_returns_the_engines_rows_for_the_paper_asset_after_a_few_cycles` | after 6 cycles with realised prices `/attribution` returns 6 rows of D1b's engine with the shadow run; components add up to the gap interval by interval; the engine's `actual` equals the ledger's revenue; the window is clipped to the first executed interval; a window nobody traded in is `no_data`; a disclosure asset never runs a cycle |
| `test_two_bad_daily_calibration_evaluations_halt_dispatch_and_one_only_alerts` | 20 cycles, then prices land far above the P90: **day 1 is an alert plus a retrain request** (no halt, dispatch carries on, the same-day passes do nothing); **day 2 sets the halt flag** (`auto-halt after 2 consecutive breaching checks: calibration x2`); a resume gives dispatch back and the passes in between do not re-halt; the next bad evaluation halts again; `/attribution` over the 3-day ledger skips the shadow (`forecast_fn=None`) |
| `test_feature_psi_flags_but_never_halts_inside_the_running_service` | the PSI metric is exported and feature drift never counts or halts in the running service |
| `test_the_lazily_wired_backtest_reproduces_the_paper_ledger` | D1b's backtest, built on first use from the loaded model's forecasts, equals the paper ledger's revenue to $0.0001 over 8 cycles; the second call is 8 memo lookups; the monitor's slippage check reads it |
| `test_a_forecast_that_cannot_be_made_is_no_dispatch_with_a_reason` | a `ForecastError` (the anchor column missing from the stored frame) is `error / forecast_failed`, nothing executes, the next good cycle recovers |
| `test_no_model_holding_the_alias_is_no_dispatch_with_a_reason` | a registry where nothing holds `paper` (`NoModelError`): `error / no_model`, `/health` degraded |
| `test_the_shared_registry_fixture_serves_a_promoted_model_through_the_module_level_api` | the `conftest.mlflow_registry` fixture and `registry.load / aliases` |
| 2 paper-loop tests, 2 adapter tests, 2 Redis tests | `ops/run_paper_loop.py` against the running service, the trigger times and the summary; the cycle's feature adapter and `pit_forecast_fn` (D0's frame, renamed for the model); `localhost` to `127.0.0.1` and the pooled client |

Every scenario ends with `audit.verify() == (True, None)`.

## 5. D0's 1.1.0, the model, promotion

The lead's go came at about 19:25 CDT, after `notes/d0.md` section 15 reported the work done. What I checked myself before promoting, read-only:

* `sets.FEATURES` has 82 columns at version 1.1.0 (the 41 of 1.0.0 and the 41 of `notes/d1a.md` section 10), the offline store is `features/rt_v1/1.1.0` for the five hubs, and the frames in Redis are 96 rows x 85 columns (61 to 64 KB).
* **The registered version 1 gives the same forecast from both builders.** `wiring.pit_forecast_fn` (D0's `point_in_time`, renamed by `compat.for_forecaster`, then D1a's `predict`) at 2026-09-20 12:07Z against the interim builder at the same instant: the largest absolute difference over P10, P50 and P90 and all 96 horizons is **0.0**. At 2026-10-09 14:00Z, after the interim builder's static archive ends (26 Sep), only D0's path can forecast, and it does (0.57 s). D0's own `test_rt_v1_1_equivalence.py` (1.6 million rows per hub, 59 columns, difference 0.0) says the same for the whole history.
* So the brief's first branch applies: **serve D1a's version 1 as it is, no retrain**.
* `python -m dispatch.registry.mlflow_registry check 1 paper`: **45 of 45 checks PASS** (every hub beats every baseline on pinball and P50 MAE; gains over the best seasonal-naive 24 to 40%; P10-P90 coverage 0.775 to 0.796 inside [0.70, 0.90]).
* `python -m dispatch.registry.mlflow_registry promote 1 paper --by i1 --note "..."` -> **version 1 now holds `paper`** (promoted 2026-10-11T00:20:07Z by i1; previous: none). `research` still names version 1; `live` was not touched; the tag `gate3.pass` was **not** written (the lead: Gate 3 on the real stack only records evidence). One command writes it, when someone wants `live` considered: `python -m dispatch.registry.mlflow_registry gate3 1 pass --evidence "..." --by NAME`.

What the shared service does with a model that has `feature_set = interim_d1a/v1`: the cycle passes the Redis frame through `adapt_features` (D0's `compat.for_forecaster`), logs the frame's own `rt_prof7_med`, and gives the drift window the renamed frame.

## 6. Live end to end on the real stack

`ops/live_e2e.py --phase all --replay-count 96` (port 8765, token random and never printed, Redis `127.0.0.1:6390`, `DISPATCH_HOME` the real lake). It starts the real uvicorn itself, runs the
phases and stops it in a `finally`; I checked afterwards that no uvicorn process exists and nothing listens on 8765, and removed my scratch audit anchors from Redis. Logs and the replay's own audit log,
ledger and forecast log are kept in `research-data/dispatch_i1_live/final-run/`. The transcript, trimmed to the fields that matter (one JSON line per step in the original; the 96 `replay_cycle` lines left out):

```
start               redis redis://127.0.0.1:6390/0   now_utc 2026-10-11 00:28:27Z (Sat 10 Oct 19:28 CDT)
--- phase pilot: service with the paper alias, scratch DISPATCH_RUNTIME_DIR ---
service_started     14.7 s
health              status ok, model 1 (alias paper), redis_ok/redis_writable true, all 10 wiring targets ok, audit ok
attribution_pilot   PAD1_ESS_ESR1 2026-07-06..07-13: 200 in 2.0 s, 672 intervals, gap 15,192.97, closure 0.0 (basis revaluation)
attribution_pilot   42 days (4,032 intervals): 200 in 6.8 s  actual 43,266.39  perfect foresight 149,552.74  gap 106,286.34
                    forecast 17,596.59  soc 9,017.59  ancillary 74,566.10  availability 5,106.06  unexplained 0.0
compared_with_case_study_summary_json   differences vs reports/case_study/summary.json: 0.00 on every line
not_a_paper_asset   POST /signal PAD1_ESS_ESR1 -> 404
service_stopped     port_closed true
--- phase replay: DISPATCH_ALLOW_REPLAY=1, scratch runtime; 96 decisions 15 minutes apart from 2026-10-09 14:00Z ---
replay_cycle x96    prepare_signal(now_utc=t, ingest=False) then POST /signal {now_utc=t}: http 200, status dispatched every time
latency             cycles 96, dispatched 96; server latency_s p50 0.107, p99 0.194, max 0.194; round trip p50 0.118, p99 0.211;
                    prepare_s (features from the lake, Redis write) p50 0.51, max 1.03; target p99 < 30 s: met
health_after        status ok, last cycle dispatched 2026-10-10T13:45Z in 0.106 s
attribution_paper   PILOT_HB_NORTH 2026-10-09 14:00Z..10-10 14:00Z: 200, 96 intervals, shadow ran, closure 0.0 (revaluation)
                    actual 1,333.27  shadow 1,334.56  perfect foresight 1,752.45  gap 419.18 = forecast 1.75 + soc 0.00 + ancillary 417.43 + availability 0 + unexplained 0
metrics             30 families, 102 samples; dispatch_cycles_total{dispatched} 96; model_info{alias=paper,version=1}; halt thresholds 3,3,3,3 and calibration 2
gate3               replayed ledger vs D1b backtest (96 decisions solved, 0 failed, 45.3 s): paper 1,333.27 = backtest 1,333.27, slippage 0.0, pass, 0 replay problems
--- phase live: service on the REAL runtime dirs ---
prepare_signal      HB_NORTH, 8.7 s (8.2 s of it the ingest of what ERCOT had just posted), asof 00:31:41Z, newest input published 00:30:00Z, 96 rows
signal              http 200 in 0.184 s: status dispatched, model 1, feature_age_s 101.6, latency_s 0.171, plan b803aa962034c1f1
                    setpoint PILOT_HB_NORTH interval 2026-10-11T00:30Z: net +10.0 MW (discharge), ancillary REGDN 10.0 MW, guard accepted, no reasons
health_after        status ok, last_cycle dispatched, halted false
metrics             dispatch_cycles_total{dispatched} 1, dispatch_feature_age_seconds 101.6, dispatch_redis_writable 1, dispatch_forecast_log_errors 0
attribution         PILOT_HB_NORTH last 3 h: 404 no_data (the one executed interval has not settled; prices are final one hour after it ends)
service_stopped     port_closed true
```

Afterwards, in the REAL runtime: the audit log has 6 records (`startup, model_load paper v1, signal, guard_decision, setpoint, cycle`) and verifies (`audit OK: 6 records, head seq 5`, anchored in Redis);
the paper ledger holds the `init` line (SOC 10 MWh) and the one setpoint above; the forecast log holds one line (3 KB). Nothing else was written to the real runtime: the replay and the pilot ran on
scratch runtime directories.

Reading it honestly:
* **The live cycle `dispatched`, but "dispatched" is a paper setpoint**: the ledger line is not an order. Whether +10 MW with a 10 MW REGDN award at the 19:30 CDT interval is a good decision cannot be judged until that interval settles
  (about 20:30 CDT); `GET /attribution` will answer then.
* The replay is not a performance claim: it is a day of paper decisions on a price history the model has not been trained on (holdout from 2025), with the shadow close to the actual ($1,334.56 against $1,333.27) and a perfect-foresight ceiling
  of $1,752.45 for a 10 MW / 20 MWh battery, i.e. 76% captured on that day. One day is not a capture rate.
* `prepare_signal` is where the wall clock goes in a live loop (8.7 s with the ingest, 0.5 s without), not the cycle.

## 7. Latency

The PRD target is a p99 of one `POST /signal` cycle under 30 s (`CYCLE_P99_TARGET_S`). Measured on the real stack (registered model, real LP with awards, real guard, ledger and audit with fsync on, D0's frames from Redis),
on a machine shared with a research agent and other agents' test runs:

| What | n | p50 | p99 | max |
|---|---:|---:|---:|---:|
| **Replayed cycles**, server-side `latency_s` (`dispatch_cycle_seconds`) | 96 | **0.107 s** | **0.194 s** | 0.194 s |
| Same, client round trip over HTTP | 96 | 0.118 s | 0.211 s | 0.211 s |
| The live cycle on the real clock | 1 | | | 0.171 s (round trip 0.184 s) |
| `prepare_signal` before each replayed cycle (features from the lake as of that instant, Redis write; no ingest) | 96 | 0.51 s | | 1.03 s |
| `prepare_signal` live (8.2 s of its 8.7 s is the ingest of what ERCOT had just posted) | 1 | | | 8.7 s |
| Test harness with fakes, fsync on (`test_latency_of_200_cycles`) | 200 | 0.030 s | 0.045 s | 0.048 s |

**The target is met by a factor of about 150** (0.19 s against 30 s), and with 96 samples "p99" is the maximum, so the tail is shown by the spread: the slowest cycle is 1.8 times the median. Where the time goes in a cycle
(cProfile of one real cycle, after the fix below): D0's frame decode 21 ms, `compat` rename 32 ms, `predict` 25 ms, the forecast log 16 ms, the LP about 30 ms warm, the rest audit fsyncs and the ledger.

Two things were not obvious:
* **Before the fix in section 2.6 the same cycle took 2.19 s, and 2.03 s of it was a TCP connect to `localhost`.** The first cycle of the live run would have looked like a slow model.
* The LP: the first solve in a process is 3.2 s (imports and compilation); the service now warms it up at start, so the first cycle on a freshly started service was 0.194 s (the maximum of the 96; the fastest was 0.090 s). A dry run
  without the warm-up (`build_deps` by hand) had a first cycle of 0.85 s. Later solves take 30 ms, and a new worker thread costs 0.1 s once. Feature age at the replayed quarter hours was 778 s (D0's number) for most and 315 s at the shortest, inside the 20-minute gate.

What this does **not** cover: the first slippage check after a restart, when the paper ledger already spans days. D1b's backtest decides at every interval start with a real point-in-time forecast, and with D0's builder a decision costs
about 0.5 s (96 decisions took 45 s; a full 7-day window would be about 7 minutes, against the 90 s D1b measured with the seasonal-naive forecaster). It runs on the monitor's thread, never in a request handler, but it shares the
process (and the GIL for the pandas part) with the cycles; its memo makes every later pass one decision. If that matters, set the window (`providers.WINDOW`) shorter or run Gate 3 offline with `ops/live_e2e.py --phase replay`.

## 8. Final gate status

| Gate | Status | Evidence, and what it is not |
|---|---|---|
| **1** Phase 0, data: features rebuild exactly from raw | **PASS** | D0, re-run on `rt_v1` 1.1.0: 59 of 59 parquet files identical on 481 real raw files (in process and in a fresh process); on the real lake 724 of 724 identical (`notes/d0.md` section 8). The leakage test passes with the 41 new columns against an independent oracle. I did not re-run the 6-minute real-lake test; `tests/test_gate1_real_lake.py` is opt-in |
| **2** Phase 1, shadow optimizer: attribution closes on a pilot asset | **Mechanically met, not proven** | Unexplained is 0.0% of the gap on 100% of intervals, **by construction** (public data has no settled revenue, so the actual is revalued with the same accounting; D1b). The honest test is closure against a design partner's settlement statement (`reported_usd`); until then the price-proxy stress says the hub-for-node proxy alone could move **11.1%** of the gap, above the 5% target. New in this round: the platform's own `/attribution` for the pilot reproduces the case study to the cent over 42 days (it is the same engine on D0's lake data), so Gate 2's evidence now reaches the API and not only D1b's report |
| **3** Phase 2, paper: paper matches backtest, slippage under 5% | **Met on a replayed day with the real stack; evidence only** | 96 replayed decisions (9 to 10 Oct) with the registered model, the real LP, guard, ledger and audit: paper $1,333.27, D1b's backtest $1,333.27, slippage 0.0%, no ledger replay problems. Earlier evidence: `test_monitoring.py` (paper = D1b's shadow run to the cent over 60 cycles, injected faults all fail) and the integration test (paper = lazily wired backtest to $0.0001). **Not** shown: any divergence, because the replay shares code and inputs with the backtest by design. Divergence would come from guard rejections, outages, and the decision-time convention (section 2.5). Nothing is recorded for `live`: no `gate3.pass` tag, `live` untouched, as the lead asked |
| Phase 3, fleet dispatch | waits | ERCOT Board vote on ADER Phase 4, December 2026; `dispatch/fleet/` is still an interface stub |

PRD success metrics: guard integrity zero (D2's property tests; 48 of them pass, about 70,000 examples at the recorded depth); dispatch latency p99 under 30 s (0.19 s); capture rate: set after the first baseline (D1a: 0.356 to 0.432 on the
holdout, one run, no interval); data freshness, ERCOT publish to online store under one 5-minute SCED interval: **not met by the paper loop alone**. The loop rebuilds the features right before each decision (the live `prepare_signal` took 8.7 s, 8.2 s of
it the ingest), so at a decision the store is as fresh as ERCOT's posting allows (the newest input was 102 s old), but between decisions the stored frame ages up to 15 minutes. Keeping the store warm needs D0's `python -m dispatch.data.flows ingest --online`
every minute next to the loop; it was not run here.

## 9. Open items and REQUESTs

**Still open, and not mine to close**
* *Gate 2 is not proven.* It needs a design partner's settlement statement (`reported_usd`) or the node's own prices; 70% of the case-study gap is an ancillary benchmark under a price-taker assumption.
* *PRD open questions*: audit-log retention (nothing is deleted; `AUDIT_RETENTION_DAYS = None`), the exposure-cap definition (D2's conservative cost-basis rule, $25,000 for the pilot), pricing, the first design partner, and the PRD's first
  risk (employment and data rules before this touches a market participant).
* *`live` is unreachable on purpose*: it needs `gate3.pass` on the version, and nobody wrote it. The lead decides whether this replayed day is enough evidence; the command is in section 5.
* *The paper loop is not running* (nothing starts it, as asked). The paper ledger holds one real setpoint, for the interval 00:30Z on 11 Oct.
* *The calibration check cannot evaluate until half of a 7-day window has settled forecasts* (3.5 days of the loop running), and it starts afresh with every new model version. Until then capture rate and slippage are the only model-health halts.
* *The first slippage check after a restart with a week-long ledger costs about 7 minutes of CPU inside the service process* (section 7).
* *Data freshness* (publish to online store under 5 minutes) needs D0's `flows ingest --online` every minute; not run here. Weather and Henry Hub are 10 to 11 days stale (no fetcher); the served model does not read them.
* *Docker, Prometheus and Grafana were never run*; the compose file, the PromQL and the dashboard JSON are checked by tests for the metrics they use, brackets and panel overlap only.
* *The served model is the interim-trained version 1* (served from D0's frames after a rename that D0 proved exact). A retrain on D0's builder (`python -m dispatch.models.train all --source d0 --tag r2 --threads 1`, about 80 minutes) is optional (D1a): it would put
  the served model under D0's leakage test and let `models/interim_features.py` go. I did not run it.
* *Paper SOC has age 0*, so `soc_stale` is exercised only by unit tests; real telemetry needs a real source behind `GuardState`.
* *Not run by me*: the opt-in real-lake Gate 1 (6 minutes) and the full-history equivalence (3 minutes a hub); D0 ran them.

**REQUEST (I1 -> lead)**
1. `DESIGN.md` "Monitoring policy" should record what the code now does: (a) stale-data halt eligibility = NP6-905-CD and NP4-190-CD, the allowances of section 2.3, alert-only for the rest; (b) PSI asks for no retrain either; (c) the forecast log
   (`runtime/forecasts/forecasts-YYYYMMDD.jsonl`) and the calibration schedule/state files; (d) the evaluation needs half a window settled, scores only the loaded model version, and an evaluation that crashes counts once a day; (e) a resume after a
   calibration halt holds until the next daily evaluation; (f) `DISPATCH_RUNTIME_DIR`; (g) the decision instant of the paper loop is the start of the interval (section 2.5). Gate 3's text could say "met on a replayed period" with the evidence of section 8.
2. `config.py`: move `CALIBRATION_MIN_COVERAGE` (0.5) and `CALIBRATION_MIN_ROWS` (96) from `monitoring/checks.py`, and the report allowances and `HALT_ELIGIBLE_REPORTS`, if you want every threshold in one file. (You have already changed the `REDIS_URL` default to 127.0.0.1; the service
   normalises `localhost` anyway.)
3. Decide whether the stale-data halt should sit on NP4-188-CD (what the model reads) instead of, or as well as, NP4-190-CD (section 2.3).
4. Decide whether to write `gate3.pass` on version 1 from the replayed day of section 8.

**REQUEST (I1 -> D0)**: `online.read_latest(sp)` and `online.refresh_latest` open a new Redis connection on every call; with the pooled client the service passes in, a cycle is 0.08 s, without it it was 2.2 s on this machine. Consider a module-level pooled client by default
(and `127.0.0.1` instead of `localhost` in `connect()`). `prepare_signal` still uses the default connection (about 2 s of its time unless `config.REDIS_URL` is already `127.0.0.1`, which it now is).
**REQUEST (I1 -> D1b)**: if fresher-than-interval-start decisions are ever wanted, `Backtest` needs a decision offset (and a model trained at that age); today the paper loop decides where the backtest does.
**REQUEST (I1 -> D2)**: none outstanding; the audit-log fix of section 2.6 is in your file and has a regression test.

## 10. How to rerun

```
# from hugging-base/dispatch, Git Bash
export PYTHONIOENCODING=utf-8 MLFLOW_DISABLE_AGENT_HINT=1
.venv/Scripts/python.exe -m pytest -q tests                                          # 621 collected: 618 run, 3 opt-in skips; about 11 minutes here
.venv/Scripts/python.exe -m pytest -q tests/test_calibration.py -s                   # the false-breach replay prints one line per hub
.venv/Scripts/python.exe ops/live_e2e.py --phase pilot                               # no model needed
.venv/Scripts/python.exe ops/live_e2e.py --phase replay --replay-count 96            # a day of replayed decisions, latency, /attribution, Gate 3
.venv/Scripts/python.exe ops/live_e2e.py --phase live                                # ONE real paper setpoint into the real ledger
.venv/Scripts/python.exe ops/run_paper_loop.py --once                                # what the scheduler does (the service must be running)
```
