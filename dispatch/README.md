# ERCOT battery dispatch platform

The operate layer of Batter Up: a shadow optimizer and attribution engine for ERCOT batteries, and a paper-dispatch service behind a
deterministic risk guard. Built from the PRD *ERCOT Battery Dispatch Platform: Discovery and PRD* (10 Oct 2026). `DESIGN.md` is the build plan and
the contracts; `notes/` has what each part measured; `ops/README.md` is the operator's manual.

## What this is

The PRD's recommendation is to lead with an independent shadow optimizer: the same forecast, optimizer and risk code that would dispatch a battery
replays any ERCOT battery's history and explains, interval by interval, where revenue was missed (forecast error, state-of-charge position,
ancillary awards, availability), and then to grow into dispatch. This repository implements that stack on public ERCOT data. Reproducible ERCOT
data and point-in-time features (rebuildable from raw files) feed quantile price forecasts; a deterministic LP (cvxpy) turns the median forecast
into a plan; a risk guard that sits outside the model checks the first interval of the plan; an accepted setpoint is written to a paper ledger; every
signal, decision and setpoint goes to a hash-chained audit log; and monitoring can halt dispatch by itself. **v1 is paper only**: there is no QSE
adapter that works, no live bid, and **no model ever produces a setpoint, a base point or a rank: models forecast prices, code decides.**

## Where it fits in the repo

`dispatch/` is a new top-level Python package next to `sim/` and `resilience/` (answer to the PRD's open question "same repo, same product, or a
separate track": the same repo, as the operate layer). It has its own `requirements.txt` and venv, so the root `requirements.txt` stays minimal.

| Existing part | What the platform takes from it |
|---|---|
| `docs/research/arena/` | The baseline policies, and the cross-check of the LP: perfect-foresight LP equals the arena's dynamic-programming oracle on energy-only days (300 zone-days, difference below 1e-6) |
| `docs/research/grid/` | Which settlement points to forecast and attribute, and the ERCOT MIS ingestion know-how (ported, not imported) |
| `sim/` (`caps.py`, `orchestrator.py`) | Phase 3 fleet dispatch reuses transformer headroom behind `dispatch/fleet/` (an interface stub until ERCOT's December 2026 Board vote on ADER Phase 4) |
| `resilience/` | Fail-closed patterns for the guard and the kill switch (fencing of stale commands, one worker per group, the replay-from-the-log idea) |

## Layout

| Folder | What | Owner of the design |
|---|---|---|
| `dispatch/data/` | Immutable raw lake, catalog, parsers (17 datasets), realised prices (`prices.py`), freshness, `flows.py` (the CLI) | D0 |
| `dispatch/features/` | Feature set `rt_v1`, the point-in-time builder, the offline store, the Redis online store, `compat.py` | D0 |
| `dispatch/models/`, `dispatch/registry/` | LightGBM P10/P50/P90 forecasters, baselines, purged walk-forward CV, calibration rules, MLflow registry with research / paper / live aliases | D1a |
| `dispatch/optimizer/`, `dispatch/attribution/` | The LP (one code path for perfect foresight, forecast and fixed actions), the shadow optimizer, the Gate 3 backtest, attribution (FR7), the 60-day-disclosure pilot and the case study | D1b |
| `dispatch/guard/`, `dispatch/execution/` | The guard (rejects, never clips), the paper ledger, the audit log, the disabled QSE adapter | D2 |
| `dispatch/service/`, `dispatch/monitoring/` | The FastAPI service, cycle, halt flag, monitors, calibration check, forecast log, wiring of everything above | D2, wired by I1 |
| `ops/` | Prometheus, alert rules, Grafana, compose file, the paper loop, the live end-to-end script | D2, I1 |
| `tests/` | 621 tests (618 run by default, 3 opt-in against the real lake), including the integration test of the whole chain (`test_integration_cycle.py`) and the calibration replay over D1a's holdout (`test_calibration.py`) | all |
| `reports/case_study/` | The public case study (PAD1_ESS_ESR1, 42 days of ERCOT's 60-day disclosure) | D1b |

## Quickstart

Windows, Git Bash, from `hugging-base/dispatch/`. Python 3.13. Real data lives outside git under `DISPATCH_HOME`
(default `C:/Users/Javier/Repo/BaseP/research-data/dispatch`).

```sh
python -m venv .venv                                   # once
.venv/Scripts/python.exe -m pip install -r requirements.txt
export PYTHONIOENCODING=utf-8 MLFLOW_DISABLE_AGENT_HINT=1

# 1. The platform's own Redis on :6390, append-only (the halt flag survives a restart). Never use :6379: it belongs to another application.
scripts/redis_dispatch.sh &

# 2. Ingest: copy research data into the immutable lake, fetch the MIS windows and the free yearly archives, parse to parquet
.venv/Scripts/python.exe -m dispatch.data.flows adopt
.venv/Scripts/python.exe -m dispatch.data.flows backfill
.venv/Scripts/python.exe -m dispatch.data.flows parse

# 3. Features: the offline store for training, then the latest frame per settlement point into Redis
.venv/Scripts/python.exe -m dispatch.data.flows features --start 2019-01-01 --sps HB_NORTH,HB_WEST,HB_HOUSTON,HB_SOUTH
.venv/Scripts/python.exe -m dispatch.data.flows online --sps HB_NORTH

# 4. Train (single-threaded LightGBM, about 35 minutes a hub), then promote through the gates. Version 1, trained on D1a's interim builder, is
#    already registered and is served as it is: rt_v1 1.1.0 carries its columns (identical numbers, D0) and the service renames them (features/compat.py).
.venv/Scripts/python.exe -m dispatch.models.train all --source d0 --tag r2 --threads 1        # optional: puts the served model under D0's leakage test
.venv/Scripts/python.exe -m dispatch.registry.mlflow_registry status
.venv/Scripts/python.exe -m dispatch.registry.mlflow_registry check <version> paper         # a dry run of the gate
.venv/Scripts/python.exe -m dispatch.registry.mlflow_registry promote <version> paper --by <you>

# 5. Serve (paper alias). One service process per DISPATCH_HOME.
export DISPATCH_API_TOKEN=$(python -c "import secrets; print(secrets.token_urlsafe(24))")
.venv/Scripts/python.exe -m uvicorn dispatch.service.app:create_app --factory --host 127.0.0.1 --port 8000

# 6. The paper loop: fetch what ERCOT just posted, rebuild the features, POST /signal. Every 15 minutes (at the start of the interval), or once.
.venv/Scripts/python.exe ops/run_paper_loop.py --once
.venv/Scripts/python.exe ops/run_paper_loop.py                          # until Ctrl-C

# 7. Look at it
curl -s http://127.0.0.1:8000/health            # model, feature age, last cycle, halt state, wiring of every module
curl -s http://127.0.0.1:8000/metrics           # Prometheus text
curl -s -H "Authorization: Bearer $DISPATCH_API_TOKEN" "http://127.0.0.1:8000/attribution?asset_id=PAD1_ESS_ESR1&start=2026-07-06&end=2026-07-13"
curl -s -X POST -H "Authorization: Bearer $DISPATCH_API_TOKEN" -H "content-type: application/json" \
     -d '{"reason":"looking at it","set_by":"me"}' http://127.0.0.1:8000/kill          # stop dispatch; POST /resume brings it back
```

Monitoring: `/metrics` is scraped by `ops/prometheus.yml`, the alert rules are `ops/alerts.yml`, the dashboard is
`ops/grafana/dashboards/dispatch.json` (no Docker, Prometheus or Grafana exists on this machine, so those files are parsed by tests but were never
run by the programs). What halts dispatch, and what only alerts, is in `ops/README.md` ("Monitoring policy"). The whole stack on the real data,
with a throwaway server that it stops itself: `.venv/Scripts/python.exe ops/live_e2e.py --phase all --replay-count 96` (its `live` phase writes ONE real paper
setpoint into the real ledger; `--phase pilot` and `--phase replay` use scratch runtime directories).

Tests: `.venv/Scripts/python.exe -m pytest -q tests`: 621 collected, 618 run, 3 opt-in skips; **10 min 42 s and 10 min 23 s** in two consecutive full runs on this shared machine, all green.
`DISPATCH_GATE1_REAL=1 ... tests/test_gate1_real_lake.py` runs Gate 1 against the real lake (6 minutes, reads it and never writes it).

## Gate status

| Gate | Pass when | Status |
|---|---|---|
| **1** Phase 0, data | Delete `parsed/` and `features/`, rebuild from raw, every partition has the same sha256 | **PASS** (D0). 59 of 59 parquet files identical on 481 real raw files, in-process and in a fresh process; on the real lake 724 of 724 identical (the 2018 and 2019 archives, Henry Hub, weather, HB_NORTH features for 2019); the leakage test (nothing in a feature is published after its decision) passes |
| **2** Phase 1, shadow optimizer | Attribution closes on a pilot asset: unexplained under 5% of the gap | **Mechanically met, not proven.** On public data the actual dispatch is revalued with the same accounting, so unexplained is 0.0% by construction (D1b). The real test is closure against a design partner's settlement statement (`reported_usd`); until one exists, the price-proxy stress test says the hub-for-node proxy alone could move 11.1% of the gap, above the 5% target (D1b) |
| **3** Phase 2, paper | Paper matches the backtest, slippage under 5%, over at least a week of **real-time** paper operation against live ERCOT publications; then a partner signs | **Not yet passed (lead decision, 11 Oct).** Evidence so far: one replayed day on the real stack (registered model, real LP, guard, ledger, audit), 96 decisions, paper $1,333.27 = D1b's backtest $1,333.27, slippage 0.0%. A replay shares code and inputs with the backtest, so that shows consistency, not market performance. No `gate3.pass` is recorded, so `live` stays unreachable |

The PRD's other success metrics: guard integrity zero (`tests/test_guard_props.py`: about 70,000 passing examples across the properties, none lets a
setpoint past a limit); dispatch latency p99 under 30 s (**0.19 s** over 96 replayed cycles on the real stack, p50 0.11 s; the one live cycle took 0.17 s); attribution closure and capture rate as above and in `notes/d1a.md`.

## The case study in one paragraph

`reports/case_study/case_study.md`: one public battery (PAD1_ESS_ESR1, 50 MW / 122 MWh, resource node PAD1_ESS_RN settling near HB_SOUTH), 42 operating
days (1 Jul to 11 Aug 2026) of ERCOT's own 60-day disclosure. Valued at proxy prices, its dispatch and day-ahead positions come to **$43,266**, which is
**28.9%** of the **$149,553** a perfect-foresight operator with the same battery would have earned (net of a $20/MWh discharge wear charge; 37.6% with
none). The gap is **$106,286**: ancillary awards 70.2%, forecast and decision 16.6%, state-of-charge position 8.5%, availability 4.8%, unexplained 0.0%.
The shadow optimizer (seasonal-naive forecast, hourly re-plan, public information only) would have earned **$100,117** (66.9% of perfect foresight) and
recovered 53% of the gap. **That advantage comes from ancillary services, under price-taker, no-deployment-energy assumptions** (it sells regulation up
and down at the real-time clearing price, every offered MW awarded, no deployment energy, no wear on deployed regulation). **On energy alone the actual
operator out-earned our shadow: $32,454 against $24,152**, because $18,757 of its energy revenue is a day-ahead position that the real-time-only v1
shadow does not take. "Our optimizer would have recovered 53% of the gap" is therefore a statement about ancillary capacity under a price-taking
assumption, not a promise of money. The prices are a hub proxy for the node (node minus hub: mean +$1.01/MWh, RMSE $2.93).

## Known limits and open questions

* **No ERCOT API key.** Only the public MIS windows exist: real-time nodal prices for 7 days, the load forecast for 7 days, day-ahead for about 31
  days, plus the free yearly archives (hub and zone prices from 2010/2011, ancillary prices) and the 60-day disclosures (42 operating days are in the
  lake). Nodal history is therefore short, the load forecast is NaN for all of the training history, and the pilot's prices are a hub proxy.
  `ercot.fetch_api_archive` is written and untested against the live API.
* **The shared Redis on `:6379` is full and belongs to another application.** The platform runs its own on `:6390` with append-only persistence; redis-py
  needs `protocol=2` for this machine's Redis 3.0.504. It is never touched.
* **No Docker, Prometheus or Grafana here.** The compose file, alert rules (PromQL) and dashboard JSON are checked by tests for the metrics they name,
  balanced brackets and non-overlapping panels, not by the programs themselves.
* **Weather and Henry Hub are 10 to 11 days stale** (no fetcher for NCEI and FRED was written); the served model does not use them.
* **Gate 2 is not proven** (above), and 70% of the case-study gap is an ancillary benchmark that assumes a price-taker. Treat the energy-only and
  no-regulation rows as the conservative reading.
* **Open PRD questions.** *Audit-log retention* is undecided: nothing is ever deleted (`AUDIT_RETENTION_DAYS = None`). *Exposure cap*: the guard enforces D2's
  conservative definition (cost basis of the energy in the battery against $25,000 for the pilot), pending a business decision (inventory cost, daily
  loss, ancillary shortfall?). *Pricing* (per MW or value recovered) and the *first design partner* (small owner, lender, or ADER aggregator) are
  business decisions this repository cannot make. The PRD's first risk, employment and data rules before this touches a market participant, is open too.
* **One writer per `DISPATCH_HOME`**: the audit log and the paper ledger are single-writer.

## Documentation

`DESIGN.md` (contracts, storage, monitoring policy, gates); `notes/d0.md` data and features; `notes/d1a.md` forecasts and registry;
`notes/d1b.md` LP and attribution; `notes/d2.md` service, guard, monitoring; `notes/integration.md` how it was wired together, the live run, the
numbers; `ops/README.md` operating it; `reports/case_study/case_study.md`.
