# ERCOT battery dispatch platform: design and contracts

This is the build plan for the PRD *ERCOT Battery Dispatch Platform: Discovery and PRD* (10 Oct 2026,
@ForgeMichael). It was written on 10 Oct 2026 by the lead, and every agent reads it before writing code. If you change
a contract, change this file in the same edit.

## Where this fits in the repo

The PRD's open question is: "How does this connect to Batter UpUp: same repo, same product, or a separate track?"
**The answer here is that it goes in the same repo, as the operate layer.** It is a new top-level Python package,
`dispatch/`, sitting next to `sim/` and `resilience/`. Each existing part feeds it:

| Existing part | What it already does | What the platform takes from it |
|---|---|---|
| `docs/research/arena/` | Five dispatch algorithms and walk-forward backtests. `battery.oracle_days` is a perfect-foresight dynamic program. | The baseline policies, and a cross-check for the LP: LP perfect foresight must match the DP oracle on energy-only days. |
| `docs/research/grid/` | ERCOT MIS ingestion (`gridsite/fetch_ercot.py`, report IDs, SCED joins), nodal prices, shift factors, and site ranking. | Which settlement points to forecast and attribute, plus the ingestion know-how. Port it; don't import it. |
| `sim/` (`caps.py`, `orchestrator.py`, `surrogate.py`) | Transformer headroom and feeder-aware fleet allocation. This is the "transformer headroom modeling from the Base Power hackathon" the PRD names. | Phase 3, fleet dispatch, reuses it behind `dispatch/fleet/`. |
| `resilience/` | Controller-crash survival, worker leases, physics checks. | Fail-closed patterns for the guard and the kill switch. |

The repo already has a rule that matches the PRD's main non-goal: **no model produces a setpoint, a base point or a rank;
deterministic code does.** Here, models (LightGBM) only forecast prices. The LP chooses setpoints, and the guard checks
them.

The root `requirements.txt` stays minimal, which is a submission rule. The platform has its own `dispatch/requirements.txt`
and venv `dispatch/.venv` (Python 3.13). `.venv/` is git-ignored.

## Layout and ownership

Every agent owns its files. Don't edit someone else's file: if you need a change there, write it in your notes and the
lead integrates it.

### Lead
- `DESIGN.md`, `requirements.txt`, `README.md`
- `dispatch/{__init__,config,contracts,units,paths}.py`
- `dispatch/fleet/` (an interface stub only)
- `tests/conftest.py`, `tests/test_units.py`, `tests/test_integration_cycle.py`

### D0, data foundation (PRD Phase 0)
- `dispatch/data/`: `lake.py`, `ercot.py`, `weather.py`, `catalog.py`, `duck.py`, `flows.py`
- `dispatch/features/`: `build.py`, `online.py`, `sets.py`
- `tests/test_lake.py`, `tests/test_leakage.py`, `tests/test_rebuild.py` (Gate 1)
- `notes/d0.md`

### D1a, forecasts and registry (PRD Phase 0–1)
- `dispatch/models/`: `quantile.py`, `cv.py`, `train.py`, `baselines.py`
- `dispatch/registry/`: `mlflow_registry.py`
- `tests/test_models.py`, `tests/test_registry.py`
- `notes/d1a.md`

### D1b, optimizer and attribution (PRD Phase 1, the v1 product)
- `dispatch/optimizer/`: `lp.py`, `dp_check.py`
- `dispatch/attribution/`: `engine.py`, `disclosure.py`, `report.py`
- `tests/test_lp.py`, `tests/test_attribution.py`
- `notes/d1b.md`, `reports/case_study/`

### D2, service, guard, execution, monitoring (PRD Phase 2)
- `dispatch/guard/`: `guard.py`
- `dispatch/execution/`: `ledger.py`, `audit.py`, `qse.py` (disabled)
- `dispatch/service/`: `app.py`, `cycle.py`, `state.py`
- `dispatch/monitoring/`: `checks.py`, `metrics.py`, `halt_policy.py`
- `ops/`: Prometheus config, alert rules, Grafana dashboards, `docker-compose.yml`
- `tests/test_guard_props.py`, `tests/test_api.py`, `tests/test_audit.py`, `tests/test_monitoring.py`
- `notes/d2.md`

All paths are under `hugging-base/dispatch/`. The Python package is `dispatch` (inner folder), so run Python from
`hugging-base/dispatch/`. Tests use pytest: `.venv/Scripts/python.exe -m pytest -q tests`.

## Storage

Everything runtime lives outside git, under `DISPATCH_HOME`. The default is
`C:/Users/Javier/Repo/BaseP/research-data/dispatch`. See `dispatch/paths.py`.

| Path | Contents | Rule |
|---|---|---|
| `lake/raw/<source>/<report_id>/<YYYY-MM-DD of publish>/<original file name>` | Raw bytes as published | Immutable. Never overwrite and never delete. A re-download with the same name and sha256 is a no-op. Different bytes under the same name raise an error. |
| `lake/catalog.jsonl` | One JSON line per raw file: `source, report_id, published_utc, ingested_utc, path, bytes, sha256, origin` | Append only. `origin` is `mis`, `api`, `web`, or `adopt:<path>` for files adopted from `research-data/grid/raw`. |
| `lake/parsed/<dataset>/date=YYYY-MM-DD/*.parquet` | Every row carries `published_utc` and `interval_start_utc` (UTC) | Can be rebuilt from raw at any time. |
| `features/<feature_set>/<version>/*.parquet` | The offline store | Rebuildable. Gate 1 checks this. |
| `mlflow/mlflow.db`, `mlflow/artifacts/` | MLflow tracking and the registry (sqlite) | |
| `runtime/audit/*.jsonl` | Append-only audit log, hash-chained | |
| `runtime/ledger/*.jsonl` | Paper ledger | |
| `owner/<customer_id>/` | Owner telemetry and settlement exports | Isolated per customer. Never commit. |

**Publish time.** ERCOT MIS file names carry it, e.g.
`cdr.00012301.0000000000000000.20261010.101702135.SPPHLZNP6905_20261010_1015_csv.zip` was posted
2026-10-10 10:17:02.135 Central Prevailing Time. Convert it to UTC.

**Adopting existing files.** About 10,000 MIS files that research agent 1a fetched sit in
`research-data/grid/raw/ercot/<REPORT_ID>/`, listed in `research-data/grid/manifest.csv`. D0 adopts them into the lake
by copying them. Never move or edit the originals.

**API limit.** ERCOT's API allows 3 downloads of the same report per 12 months. Before any API download, check both
`lake/catalog.jsonl` and `research-data/grid/manifest.csv`.

## Contracts

The authoritative definitions are in `dispatch/contracts.py`, `dispatch/units.py` and `dispatch/config.py`. A summary:

**Time**
- Timestamps are tz-aware UTC pandas Timestamps.
- Settlement intervals are 15 minutes (`DT_RT_H = 0.25`) and are the optimizer grid.
- SCED runs every 5 minutes. DAM is hourly, forward-filled to 15 minutes when combined.
- `asof_utc` is a decision time.

**Point in time**
- A row may feed a decision at `asof_utc` only if its `published_utc <= asof_utc`.
- Features carry `max_published_utc`, and the leakage test fails the build if that is later than the decision time.

**Units**
- Column suffixes: `_mw`, `_mwh`, `_usd`, `_usd_per_mwh`, `_usd_per_mw_h`.
- Energy = MW × hours. Energy revenue = $/MWh × MWh. AS revenue = $/MW-h × MW × hours.
- Use `dispatch.units`, never ad-hoc arithmetic. For example, 1 MW discharged for one 15-minute interval at
  $100/MWh earns **$25**.

**Sign**
- `net_mw` is positive for discharge (injection) and negative for charge.
- The optimizer frames keep `charge_mw >= 0` and `discharge_mw >= 0` as separate columns.

**Objects**
- `BatterySpec`, frozen:
  - asset_id, settlement_point, power_mw, energy_mwh, soc_min_mwh, soc_max_mwh
  - rte, plus `loss_side`: `"charge"` matches the arena, `"split"` puts the square root on each side
  - wear_usd_per_mwh (charged on discharged MWh, as the arena does), max_cycles_per_day
  - as_products
  - Ancillary durations come from `config.AS_DURATION_H`.
- `PriceFrame`: a DataFrame on the 15-minute index with `rt_usd_per_mwh`, and optionally `dam_usd_per_mwh` and
  `as_<product>_usd_per_mw_h`.
- `ForecastFrame`: issued_utc, settlement_point, model_version, `frame` (columns `{series}_p10/_p50/_p90`, where series
  is `rt`, `dam` or `as_<product>`) and `max_feature_published_utc`.
- `Plan`: asset_id, mode (`pf`, `forecast` or `fixed`), solver, status, objective_usd, inputs_sha256, and `frame` with
  `charge_mw, discharge_mw, as_<product>_mw, soc_end_mwh, energy_usd, as_usd, wear_usd`.
- `Setpoint`, the first interval of a plan: asset_id, interval_start_utc, net_mw, as_mw (dict), plan_id,
  model_version, issued_utc.
- `GuardDecision`: accepted, setpoint (passed through unchanged when accepted), reasons, checked_utc.
  - **The guard rejects; it never clips.** The one exception is clipping within the numeric tolerance
    `config.GUARD_EPS_MW`.
- `AttributionRow`: interval_start_utc, actual_usd, shadow_usd, pf_usd, gap_usd = pf − actual, and the split
  forecast_usd, soc_usd, as_usd, availability_usd, unexplained_usd.
  - Closure is |Σ unexplained| / |Σ gap|. The target is < 5%, PRD proposed.
- `CycleResult`: asset_id, started_utc, finished_utc, status (`dispatched`, `rejected`, `halted`, `stale`, `error`),
  setpoint, guard decision, reasons, model_version, feature_age_s.

**Interfaces each owner implements.** The signatures live in `contracts.py` as `Protocol`s. Code against them, and use
fakes in tests until the real module exists.
- **D0**:
  - `features.build.point_in_time(sp, asof_utc, horizon) -> DataFrame` (one row per target interval)
  - `features.build.training_frame(sp, start_utc, end_utc) -> (X, y, decision_utc)`
  - `features.online.write_latest(sp, df)` and `read_latest(sp) -> (df, asof_utc, max_published_utc)`
  - `data.catalog.freshness(now_utc) -> {report_id: age_s}`
- **D1a**:
  - `registry.load(alias) -> Forecaster`, where a Forecaster has `.version` and
    `.predict(features_df, issued_utc) -> ForecastFrame`
  - `registry.promote(version, alias)`, where alias is `research`, `paper` or `live`
- **D1b**:
  - `optimizer.lp.solve(spec, prices, soc0_mwh, *, mode, allow_as=True, availability=None, fixed_as=None, terminal_soc_mwh=None) -> Plan`
    - `prices` is a PriceFrame (perfect foresight) or a P50 view of a ForecastFrame.
    - The same function runs both modes.
  - `attribution.engine.attribute(spec, actual, prices, forecasts) -> DataFrame[AttributionRow]`
- **D2** (confirmed by the lead after D2's build, 10 Oct):
  - `guard.guard.check(spec, setpoint, state, *, now_utc) -> GuardDecision`. `now_utc` is required.
  - `service.cycle.run_cycle(asset_id, now_utc=None, deps=None) -> CycleResult`
  - `execution.ledger.PaperLedger.record(spec, setpoint, ...)` and `settle(spec, prices)`
  - `execution.audit.AuditLog.append(event) -> hash`
  - The clipping clause means a tolerance. A violation within `GUARD_EPS_MW` counts as met, and the setpoint is
    passed through unedited. An accepted setpoint is never changed.
  - The exposure cap is D2's conservative definition (`notes/d2.md` §12), pending a business decision.
  - Audit-log retention is still the PRD's open question. It is a named, undecided constant.

## Fail closed

When data, the model or the guard is in doubt, nothing dispatches. Each of these produces no setpoint, plus an audit
record explaining why:
- missing features
- a feature age above `config.MAX_FEATURE_AGE_S`
- no model with the `live` or `paper` alias
- a solver status that is not optimal
- any exception
- the halt flag being set

**Data rules still open (11 Oct 2026, P2)**
- The weather and gas history publish rules are optimistic. Weather is assumed published 1 day after the day, but the
  measured lag is about 3.5 days. Gas is assumed published after 7 days, but holiday weeks run up to 8.
- `features/build.py` uses the first-published version of an observation, not a later revision.
- The served model (version 6) reads neither weather nor gas. **Both must be fixed before a model may use them.**

## Monitoring policy (lead decision, 10 Oct 2026)

D1a measured that population-stability (PSI) drift on this model's features flags nearly every feature, even on
in-distribution data. The features are 24-hour to 7-day rolling statistics, so a short window is one regime against a
year-long reference. With `HALT_AFTER_BREACHES = 3`, feature PSI would halt dispatch within minutes.

So the policy is:
- **Feature PSI is alert-only.** `config.DRIFT_PSI_HALTS = False`. It is shown in Grafana and the alerts, and never
  counts toward the halt.
- **Halt-eligible model health is output calibration.** It is evaluated once a day (`CALIBRATION_EVAL_EVERY_S`) over
  the last `CALIBRATION_WINDOW_INTERVALS` settled intervals with `models.cv.calibration_check`. A breach is either of:
  - the realised-price hit rate below P10/P50/P90 is off by more than `CALIBRATION_HIT_TOL` (0.25);
  - rolling P50 MAE exceeds the 7-day profile-median baseline (`rt_prof7_med`) by more than `SKILL_DEGRADATION_MAX`
    (10%).

  The pinball-vs-holdout rule is alert-only.
- **What a breach does.** One daily breach raises an alert plus a retrain request. `CALIBRATION_HALT_AFTER = 2`
  consecutive daily breaches set the halt flag, which an operator clears with `/resume`.
- **Measured false breaches** (D1a, holdout from 1 Jan 2025 to 26 Sep 2026): 2.1–2.5% of in-distribution windows. The
  0.10 tolerance gave 42–46%.
  - Across 21 months that is 4–10 breach episodes per hub, clustered around Winter Storm Fern, where the model really
    loses to the profile baseline.
  - The rule catches a $10/MWh bias in 65–95% of windows, but bands shrunk to half width in only 18–27%. It barely
    notices a stale forecast; the stale-feature check covers that.
- **This needs a forecast log:** every cycle stores its issued P10/P50/P90 for the intervals it planned, plus
  `rt_prof7_med`.

**As built** (I1 integration, 11 Oct; details in `notes/integration.md` §2):
- **Stale data.**
  - The allowance is cadence + `REPORT_EXTRA_ALLOWANCE_S` + `DATA_FRESHNESS_TARGET_S`, per report.
  - **Halt-eligible:** NP6-905-CD (RT prices), NP4-188-CD (day-ahead AS prices, which the served model reads) and
    NP4-190-CD (posts with it). Every other report only alerts. The rule is: a report the served model reads can halt.
- **Drift.** PSI requests neither a halt nor a retrain.
- **Calibration logs and state.** The forecast log is `runtime/forecasts/forecasts-YYYYMMDD.jsonl`. The calibration
  schedule and state files sit next to it, and `DISPATCH_RUNTIME_DIR` overrides the runtime folder.
- **Calibration evaluation.** It needs half a window of settled forecasts (3.5 days) before its first verdict. It scores
  only the loaded model version, and an evaluation that crashes counts once a day.
- **Resume.** A `/resume` after a calibration halt holds until the next daily evaluation.
- **Paper loop timing.** The paper loop (`ops/run_paper_loop.py`) decides at the start of each interval, the same instant
  the Gate 3 backtest decides.

The other halt-eligible checks are unchanged: stale data, capture-rate decay, and slippage.

## Gates (from the PRD)

| Gate | After | Pass when | Check |
|---|---|---|---|
| 1 | Phase 0, data | Features rebuild exactly from raw. Delete `parsed/` and `features/`, rebuild, and the sha256 of every partition matches. | `tests/test_rebuild.py` |
| 2 | Phase 1, shadow optimizer | Attribution closes on a pilot asset: unexplained < 5% of the gap. | `tests/test_attribution.py` plus the case-study report |

**How to read Gate 2 on public data.** The attribution revalues the actual dispatch with the same accounting, so on
public data unexplained is zero by construction. The real pass criterion is closure against a design partner's
settlement statement (`reported_usd`). Until a partner provides one, the report also states the price-proxy stress
test: 11.1% for the PAD1 pilot, above the 5% target. Gate 2 is therefore **mechanically met but not yet proven**.
| 3 | Phase 2, paper | Paper matches backtest with slippage under `config.SLIPPAGE_TOL` over **real-time paper operation**: the paper loop runs against live ERCOT publications for at least a week. "A partner signs" is a business step. | `tests/test_monitoring.py` slippage test plus the D2 and I1 notes |

**Gate 3 status (lead decision, 11 Oct).** On one replayed day on the real stack, paper equalled backtest ($1,333.27 vs
$1,333.27). That is recorded as evidence only. A replay shares code and inputs with the backtest, so zero slippage
there is expected, not proof. `gate3.pass` is **not** written on any version, so `live` stays unreachable until real-time
paper operation passes.

Phase 3, fleet dispatch, waits on the ERCOT Board's December 2026 vote on ADER Phase 4. Until then it is only an
adapter stub in `dispatch/fleet/` that names `sim/caps.py` as its headroom source.

## Known constraints on 10 Oct 2026

**ERCOT data**
- There is no ERCOT Public API key, so only the public MIS windows are available: bus LMPs 5 days, nodal SPPs and SCED
  shadow prices 7 days, DAM reports about 31 days.
- Two free yearly archives exist: DAM AS prices from 2010 (13091) and RT AS prices since RTC+B (25569, 25570).
- The yearly zonal and hub RT history from 2011 (NP6-785-ER) is in `research-data/ercot/`.
- The 60-day disclosure reports (resource-level telemetry and awards) are on MIS. D1b checks what is reachable.

**Machine**
- **Redis.** The Redis 3.0.504 at `localhost:6379` belongs to another application. It is full (108 MB against its
  `maxmemory`, `noeviction`), holds that application's keys, and refuses writes.
  - The platform never touches it. It runs its own instance on **`localhost:6390`** (`scripts/redis_dispatch.sh`),
    with append-only persistence under `DISPATCH_HOME/runtime/redis`, so a halt flag survives a Redis restart.
    `config.REDIS_URL` defaults to it.
  - redis-py 8 needs `protocol=2` to talk to Redis 3.0.
  - Tests use `fakeredis`.
- There is no Docker, Grafana or Prometheus server. D2 writes their configs and verifies `/metrics` with the
  Prometheus text parser.
- Prefect and Evidently are optional. If they won't install, keep plain-function flows with httpx retries, and an
  in-house PSI/KS drift check, behind the same interfaces.
