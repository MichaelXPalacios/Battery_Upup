# D1b notes: optimizer and attribution (PRD Phase 1, the v1 product)

Written 10 Oct 2026. Owner: D1b. Everything below was measured on this machine on that date. Labels follow the repo's
style: REAL (read from a named source), DERIVED (computed from REAL inputs by a stated rule), ASSUMPTION (our choice).

## 1. What exists

| Piece | File | State |
|---|---|---|
| LP, one code path for perfect foresight, forecast and fixed-action valuation (FR5) | `dispatch/optimizer/lp.py` | done, 46 tests in `test_lp.py` |
| Shadow optimizer (receding horizon, injected forecaster, seasonal-naive default) | `dispatch/optimizer/rolling.py` | done |
| Gate 3 backtest: `backtest_fn(spec, start_utc, end_utc) -> revenue_usd` over `run_shadow(step_intervals=1)`, with a memo | `dispatch/optimizer/backtest.py` | done, 26 tests in `test_backtest.py` (section 5.1) |
| Cross-check against the research arena DP | `dispatch/optimizer/dp_check.py`, `tests/test_lp.py` | done |
| Attribution engine (FR7) | `dispatch/attribution/engine.py` | done |
| 60-day disclosure download, readers, pilot selection, `ActualDispatch` builder | `dispatch/attribution/disclosure.py` | done |
| Case-study report | `dispatch/attribution/report.py` -> `reports/case_study/` | done, run on real data |
| Tests | `tests/test_lp.py` (46), `tests/test_attribution.py` (24), `tests/test_backtest.py` (26) | 96 tests green, 106 with `test_units.py` (section 11) |

## 2. The LP (FR5)

15-minute grid, dt = 0.25 h, intervals t = 1..T, the guard's own formulas (`guard/guard.py` states the same ones).

```
variables   c_t >= 0 grid-side charge (MW)    d_t >= 0 discharge (MW)    s_t SOC at the END of interval t (MWh)
            r_kt >= 0 award (MW) of ancillary product k, for k in spec.as_products that have a price column

maximise    sum_t [ lam_t (d_t - c_t) dt  +  sum_k pi_kt r_kt dt  -  w d_t dt ]
s.t.        s_t = s_{t-1} + eta_c c_t dt - d_t dt / eta_d                   s_0 = soc0
            s_lo,t <= s_t <= s_hi,t                                          s_T >= terminal (optional)
            c_t <= Pc a^c_t
            d_t + sum_{k in UP} r_kt <= P a_t         UP = {REGUP, RRS, ECRS, NSPIN}
            c_t + r_REGDN,t          <= Pc a^c_t
            sum_{k in UP} dur_k r_kt / eta_d <= s_t - s_lo,t                 (energy to deliver every up product)
            eta_c dur_REGDN r_REGDN,t        <= s_hi,t - s_t                 (headroom to absorb REGDN)
            sum_{t in CPT day} d_t dt <= N (s_hi - s_lo)                     (only when spec.max_cycles_per_day = N)
```

* `lam_t` = `prices["rt_usd_per_mwh"]` (realised for `pf`, the P50 view of a ForecastFrame for `forecast`); `pi_kt` =
  `prices["as_<k>_usd_per_mw_h"]`; `dur_k` = `config.AS_DURATION_H` (REAL, NPRR1282); `eta_c`, `eta_d`, `w`, `P`, soc
  bounds from the `BatterySpec`. Durations are additive across products (ERCOT's own awards fit this: one ESR held
  exactly (SOC - MinSOC)/4 MW of NSPIN).
* `a_t` is availability as a FRACTION of `power_mw` in [0, 1] (not MW; a value above 1 raises). Optional extensions of the
  `Optimizer` Protocol, all keyword-only with `None` defaults: `fixed_energy` (mode `fixed`), `charge_availability`,
  `charge_limit_mw` (ESRs often have |LSL| != HSL: `Pc`), `soc_min_series` / `soc_max_series` (reported Min/Max SOC, an
  energy derate: `s_lo,t`, `s_hi,t`).
* The power constraints are conservative (no netting of charging against up-AS headroom), so every plan is inside the
  guard's checks. DAM energy positions are not modelled (v1): only the RT price enters the objective.
* **AS energy and headroom are on the END-of-interval SOC `s_t`, and that is unchanged** (the lead asked to keep it). It is
  the convention of `guard/guard.py` (rule 12: `sum_up dur_k r_k / eta_d <= soc_end - soc_min`, `eta_c dur_REGDN r_REGDN <=
  soc_max - soc_end`, with `soc_end` projected from the setpoint), so a plan the LP calls feasible is a plan the guard
  accepts; D2 ran 60 real plans with awards through the guard with no rejection and `tests/test_backtest.py` records a
  backtest's own plans through the real `PaperLedger` (which re-checks the same limits) and replays them clean. Nothing in
  the LP's constraints, `check_plan`, `lp.account` or `LP_VERSION` ("lp-1.2") changed in this round.
* **One accounting function.** `lp.account` computes `energy_usd` (signed), `as_usd`, `wear_usd` (a POSITIVE cost) with
  `dispatch.units`; the LP result, `mode="fixed"`, the shadow and the attribution all go through it, and the tests check
  that a plan re-valued as fixed actions equals its own objective to 1e-9. 1 MW for 15 minutes at $100 is $25.
* **Plan.** `Plan.frame` has the contract columns (`charge_mw, discharge_mw, as_<p>_mw, soc_end_mwh, energy_usd, as_usd,
  wear_usd`) on the price index (UTC). `Plan.mode` is `pf`, `forecast` or `fixed`; `Plan.solver` is `HIGHS`, `CLARABEL`
  or `HIGHS-MILP`; `Plan.inputs_sha256` hashes the spec, prices, availability, fixed awards and flags (plus `LP_VERSION`).
  `plan.frame.attrs["diagnostics"]` carries solve seconds, per-constraint maximum violations, overlap counts.
* **Fail closed.** Status other than `optimal` (including `optimal_inaccurate`, infeasible, solver error) means
  `Plan.ok` is False; the finished plan is re-checked against every constraint (tolerance 1e-6) and downgraded to
  `numerical_violation` if it fails; the solver's values are clipped to the power limits so the guard (which rejects, never
  clips) cannot see a 1e-9 overshoot. Invalid inputs raise `ValueError` (NaN or infinite prices, a gap or duplicate in
  the 15-minute grid, a naive index, availability outside [0, 1], unknown mode). `lp.first_setpoint(plan, ...)` raises on
  a plan that is not ok.
* **Solver.** cvxpy with HiGHS; CLARABEL is the fallback if HiGHS raises or returns something other than
  optimal/infeasible/unbounded. The compiled problem is cached per thread and per (spec, horizon, products) and re-solved
  by changing Parameter values (DPP). A cross-check against an independent dense `scipy.linprog` formulation agrees to 1e-11
  on 12 random scenarios (asymmetric limits, derates, split losses, all five products).

### Solve time (HiGHS via cvxpy, 5 products, idle machine, 10 Oct 2026)

| Horizon | n | p50 | p95 | p99 | max |
|---|---|---|---|---|---|
| 1 day (96 intervals), perfect foresight | 300 | 40 ms | 49 ms | 58 ms | 68 ms |
| 1 day, forecast mode | 300 | 33 ms | 40 ms | 46 ms | 47 ms |
| 1 day, energy only | 300 | 17 ms | 19 ms | 19 ms | 20 ms |
| 2 days (192 intervals) | 300 | 56 ms | 67 ms | 72 ms | 81 ms |
| 7 days (672 intervals) | 20 | 197 ms | 225 ms | 235 ms | 238 ms |

A 42-day horizon (4032 intervals) takes about 2 s. The FIRST solve in a process takes about 7 s (imports cvxpy and
HiGHS, compiles the model): call `lp.warmup(spec)` once at service start. A one-day solve is about 0.15% of D2's 30-second
p99 budget.

## 3. Simultaneous charge and discharge

With rte < 1 and wear > 0 charging and discharging in the same interval only burns energy, so the LP does not do it
unless the price is deep below the threshold p* = -w rte / (1 - rte) ($-113.33/MWh for the arena battery, 85% and $20).
Proof: replace (c, d) by (c - delta, d - rte delta). SOC is unchanged, every constraint gets weaker (power, AS headroom,
cycle cap), and revenue changes by delta dt (p (1 - rte) + w rte), which is >= 0 exactly when p >= p*. So an
overlap-free optimum always exists for p >= p* and any overlap the solver returns there is netted away at no cost
(`overlap_netted` in the diagnostics); only at p < p* can the relaxation profit from burning energy, and there `solve`
re-solves a MILP (HiGHS) with a charge-mode binary on exactly the intervals with p < p* (`solver == "HIGHS-MILP"`).

How often (REAL history, all 9 zonal series 2011 to 26 Sep 2026, `dp_check.overlap_scan`): 13 of 4,966,272 intervals
(0.0003%) fall below p*, on 11 of 51,732 zone-days; the relaxed LP produced no overlap on any of them and the MILP was
never needed. The path is exercised by a toy day with $-250 prices and a full battery, where the relaxation overlaps
and `solve` matches an independent scipy MILP to 1e-6 (`tests/test_lp.py`).

**The pilot's own threshold, and what D2 asked (net setpoints cannot express an overlap).** D2's "about -$113/MWh" is the
ARENA battery's p* (85% and $20). The pilot (rte 0.901 from the energy balance, wear $20) has
p* = -20 x 0.901 / 0.099 = **-$182.02/MWh**, and any asset's p* is `lp.overlap_price_threshold(spec)`.
`dp_check.overlap_scan(spec)` now takes a spec. Same history, same method (energy only, each day from empty):

| threshold | intervals below (of 4,966,272) | zone-days | relaxed LP overlaps on | MILP needed on |
|---|---|---|---|---|
| arena p* = -$113.33 (D2's number) | 13 (2011: 5, 2016: 1, 2017: 1, 2018: 1, 2020: 1, 2022: 2, 2023: 1, 2024: 1) | 11 | 0 days | 0 days |
| pilot p* = -$182.02 | 6 (2011: 2, 2017: 1, 2022: 2, 2024: 1) | 5 | 0 days | 0 days |

None of the 13 is in 2025 or 2026; the lowest price in 15 years is -$277.14 (LZ_LCRA, 8 Sep 2011). I also re-solved the
pilot on the five days below its p* from an empty, half-full and full battery (15 solves): the relaxation never overlapped
(no day has more than two intervals below p*; an overlap needs a battery that cannot absorb the dip, full or at its charge
limit for several intervals, as the synthetic cases below show; the 15 solves are in `reports/case_study/overlap_pilot_days.csv`). The pilot's own node (PAD1_ESS_RN) is more volatile than a zone
and is not in this history (the staged nodal window is 7 days), so the right reading is "very rare at zonal level, not
measured at the node".

Where it does occur the plan still never contains it. Synthetic 48-hour forecast-mode horizons on the pilot spec with all five
products: -$150 anywhere, or -$250 ten hours out: no overlap, plain LP (0.06 to 0.2 s). **-$250 for the first hour with a
full battery: the relaxation overlaps on 3 intervals, `solve` re-solves a MILP with 4 binaries in 0.8 s, and the returned
plan has no interval with both charge and discharge. -$250 for four hours: the relaxation overlaps on 7 (empty) to 11 (full)
intervals, 16 binaries, 0.6 to 1.1 s, again no overlap in the plan.** So yes, the MILP mode removes it, automatically and
only where p < p*; it is on by default (nothing to switch on). Consequences for D2: `Plan.frame` from `lp.solve` is
overlap-free whatever the prices (netting is free above p*, the MILP handles below), so `net_mw = discharge - charge` loses
nothing and the setpoint, the guard and the ledger are exact; if the MILP fails or times out (30 s cap, ASSUMPTION) the plan
is not ok and the cycle idles (fail closed). A plan from the relaxation alone would not be dispatchable below p*, and `solve`
never returns one.

## 4. Cross-check against the research arena DP

Setup as asked: energy only, `loss_side="charge"`, the arena `grid` spec (1 MW / 2 MWh, RTE 0.85, $20 wear, 4 cycles/day cap),
each day starts empty and ends free, historical zonal RT prices from `research-data/ercot` (the arena's own cache).
Two LP variants are compared with `arena.battery.oracle_days`, because the two codes define the power limit differently:
the arena caps STORED energy per interval at P dt (its charge action draws stored/rte from the grid, up to 1.18 MW),
while the guard caps GRID power at P.

| 300 zone-days (8 zones + HB_BUSAVG, 2011-2026, includes the 30 highest-spread and the negative-price days) | LP minus DP, $ per MW-day |
|---|---|
| arena basis (charge limit P / rte): min, p50, max | about -4e-12, 0, +7e-12 (relative 4e-15; all 300 days within 1e-6) |
| arena basis, total | DP $428,221.4, LP $428,221.4 (relative 0) |
| production basis (grid-side charge limit P): p5, p50, p95, min | -9.6, -0.21, 0, -1,479 (-15%, the whole 1 - rte) |
| production basis, total | -0.59% (-1.8% on the first 60-day sample) |

So the LP is the DP's exact continuous relaxation and sits on the same vertices; the arena slightly overstates revenue
relative to what a grid-limited asset can do. `tests/test_lp.py` asserts the 50-day version (`dp_check.compare(50)`) with
tolerance 1e-6. The 300-day table is `reports/case_study/dp_crosscheck.csv`.

## 5. Shadow optimizer (`rolling.py`)

`run_shadow(spec, realised, forecast_fn, soc0_mwh=..., step_intervals=4, ...)`: at each decision time (the start of the
first interval to be executed) it calls `forecast_fn(issued_utc) -> ForecastFrame`, solves `lp.solve(mode="forecast")`,
executes the first `step_intervals` intervals (1 = every 15 minutes, 4 = hourly; a parameter), updates SOC, repeats, and
values the executed dispatch on realised prices with `lp.account`. A solve that is not optimal, an exception, or a forecast
that does not start at the decision time idles that step and is recorded (fail closed). A tightened SOC limit forces the
state inside it.

`SeasonalNaiveForecaster(history)`: same time of day on the most recent day already published at the decision time
(interval end + 20 minutes), falling back one day further if missing, then persistence; p10 and p90 are empirical over the
trailing 7 days; all `as_*` columns in the history are forecast the same way. A test perturbs every price from the
decision time on and checks the forecast does not move. `registry_forecast_fn(alias, features_fn)` wraps D1a's
`mlflow_registry.load(alias)` (the frame it returns must start at `issued_utc`); it is not exercised in tests because no
promoted model exists on this machine. `perfect_forecast_fn` is a test double that leaks the future on purpose.

Cost: about 100 ms per plan with a 48-hour horizon and five products; the 42-day case-study shadow is 1,008 hourly plans
(about 100 s), with zero failed solves.

`rolling.assemble_frame(...)` is the last step of `run_shadow` (valuing the executed actions on realised prices through
`lp.account`) pulled out so that the Gate 3 backtest ends in the same function; `RollingResult` gained `n_cached` (0 here).

### 5.1 The Gate 3 backtest (`backtest.py`)

`Backtest(price_source, forecast_fn, *, ledger=None, ...)` is the `backtest_fn(spec, start_utc, end_utc) -> revenue_usd`
that D2's `slippage_provider` calls (`make_backtest_fn(...)` is the same constructor). It is
`rolling.run_shadow(step_intervals=1)`: a decision at the start of every interval, a point-in-time forecast,
`lp.solve(mode="forecast")` exactly as the paper cycle calls it, only the first interval of each plan executed, the actions
valued on the REALISED prices by `rolling.assemble_frame` (the tail of `run_shadow`, shared, so the two cannot value the
same actions differently). `bt.run(spec, start, end)` returns the `RollingResult` (a per-interval frame with `decision_utc`,
usable as the per-interval backtest that `gate3` accepts for its largest-gaps table); `bt.last` keeps the statistics
(solved, memo hits, failures, seconds).

```python
ledger = PaperLedger()
bt = Backtest(price_source, forecast_fn, ledger=ledger)     # forecast_fn: POINT-IN-TIME, or {asset_id: forecast_fn}
app = create_app(ledger=ledger, price_source=price_source, forecast_fn=forecast_fn, backtest_fn=bt)
```

* **Equal to `run_shadow(step_intervals=1)`.** `tests/test_backtest.py` compares the frames with `check_exact=True` (every
  column: `decision_utc` equal to the interval start, SOC, awards, revenue), with scalar options (`allow_as`,
  `terminal_soc_mwh`, `charge_limit_mw`) and with a derate series (`availability`, `charge_availability`, `soc_min_series`,
  `soc_max_series`: those runs go through one whole-window `run_shadow` call so the derate lookahead is the same, and skip
  the memo). On the pilot's real prices (5 to 12 Aug 2026, 672 decisions, 192-interval horizon, seasonal-naive forecaster,
  five products) the backtest is **$15,631.43 and `run_shadow(step_intervals=1)` is $15,631.43, a difference of
  0.000e+00**. Through D2's real `PaperLedger` and `slippage_provider`, a ledger that executes the backtest's own decisions
  replays clean and gives paper = backtest to 1e-6 (slippage 0); a ledger that idled gives slippage 100%.
* **Start state.** `ledger=` (or `soc0=ledger_soc0(ledger)`) starts the backtest at the paper ledger's SOC at the window
  start, so paper and backtest begin in the same state. Without it the start is mid-window (an offline ASSUMPTION), or pass
  `soc0_mwh=` per call. A window that begins before the ledger's first executed interval is clipped to it (the monitor asks
  for seven days from the first day of paper trading; seven days of backtest against two hours of paper would halt a healthy
  service). Intervals with no ledger entry INSIDE the window (an outage, a setpoint the guard rejected) are not masked: the
  backtest decided there and paper did not, which is slippage by design and is what the check exists to find.
* **Cost and the memo.** A cold seven-day window is 672 decisions, 672 forecasts and 672 LP solves: **87 s on the pilot's
  data** (0.13 s per decision with a 48-hour horizon, measured while the machine was busy with other jobs; the idle-machine
  solve times in section 2 put it nearer 55 s), the same as `run_shadow` itself (86.7 s; the chain overhead is below the noise). The monitor asks
  every minute for a window that moves every 15, and consecutive windows share 671 of 672 decisions, so the backtest
  remembers the first interval of every solved plan under the key (asset and spec, solver options, forecast version,
  interval start, SOC rounded to 1e-6 MWh). A repeated call took **0.03 s** (672 hits) and the window one interval later
  **0.05 s** (671 hits); in steady state a pass costs one solve (about 0.15 s) plus a price read. Only plans that solved
  are remembered (a failed step is retried); realised prices are not in the key (a decision never sees them), so revised
  prices change the valuation and not the decisions; two SOCs within 0.5 Wh share a key (a revenue effect below a cent per
  step); a different SOC, spec, option or forecast `version` misses and the chain is recomputed from the new state, which
  is the right answer (paper diverged from the backtest). The first call after a start or a model swap pays the full 87 s
  once, on the monitor's thread. `memo=False` turns it off, `clear()` empties it, `memo_entries` bounds it (200,000 by
  default, a few MB). The memo assumes `forecast_fn(issued)` is a function of the issue time alone, which a point-in-time
  forecaster is by definition; a forecaster without a `version` attribute is keyed by object identity.
* **Failure handling matches the cycle.** A forecast or solve that fails idles that interval (no charge, no discharge, no
  award; SOC holds) and is counted in `bt.last["failed"]` and logged. The paper cycle idles on the same failures, so a shared
  data gap cancels and a one-sided gap shows up as slippage. It raises `BacktestError` (the monitor records a raise as an
  error breach, a doubt, never as a slippage number) for missing or NaN realised prices in the window, a starting SOC outside
  the asset's limits, an asset with no forecast function, a ledger with nothing executed in the window, and, only when asked
  (`max_failed_share=0.2`, say), too many failed decisions; `ValueError` for a window off the 15-minute grid or empty. A
  `price_source` that raises propagates (the provider has already treated that as "no data" before it calls the backtest).
* **What can still make paper and backtest differ.** (1) The backtest's `forecast_fn` is not what the cycle used: a
  different model, features loaded after the decision time, or a model swap inside the window (the backtest uses one
  forecaster for the whole window). (2) The cycle runs some minutes into the interval with the features as of then; the
  backtest decides at the interval start with the features as of then. (3) The guard rejects or alters a plan. (4)
  Downtime. Only (3) and (4) should trip the 5% tolerance; D2's replay harness, which removes (1), (2) and (4), gives exactly 0.

## 6. Attribution (FR7)

`engine.attribute(spec, actual, prices, forecast_fn, **kw) -> DataFrame` with `interval_start_utc` (a column, UTC) plus
`ATTRIBUTION_COLUMNS`; `df.attrs["summary"]` holds totals, capture rates, closure and notes. `run_attribution` returns the
full `AttributionResult` (stage frames, Shapley, the shadow run); `explain_top(result, 20)` gives the largest gaps with a
sentence built from measured dispatch, awards, SOC and derates.

**Chain of counterfactuals** (one restriction added per stage, all on the same LP; per-interval revenue r_i):

| Stage | Held to the actual | Revenue |
|---|---|---|
| S0 perfect foresight | nothing: full power, free awards, free SOC path from the actual start SOC; ends no lower than the actual end SOC | r0 |
| S1 | reported power derates (HSL/LSL as a fraction of nameplate) and reported Min/Max SOC | r1 |
| S2 | ancillary awards fixed to the actual awards | r2 |
| S3 | SOC anchored to the telemetered SOC at the start of each CPT market day, ending no lower than its end SOC | r3 |
| S4 | the actual dispatch revalued with `mode="fixed"` (same accounting) | r4 |

```
availability_usd = r0 - r1      as_usd = r1 - r2      soc_usd = r2 - r3      forecast_usd = r3 - r4 - position_usd
actual_usd = r4 + position_usd  (or the supplied reported_usd)          gap_usd = pf_usd - actual_usd
gap_usd = availability + as + soc + forecast + unexplained     EXACTLY, interval by interval (max error 2e-13 USD)
```

* `forecast_usd` is everything the actual dispatch did worse than the best response to realised prices from its own start
  SOC, awards and derates: forecast error, decision rules, risk limits. It includes `position_usd`, the settlement value of
  day-ahead positions relative to real time (`dam award x dt x (DAM SPP - RT price)` plus the same for each product's
  MCPC), which the case study builds from ERCOT's own DAM disclosure.
* `unexplained_usd` is the only term not made by a counterfactual: (a) intervals with no actual data (a data gap) carry
  their whole pf value there and zero in the four components; (b) with data it is `r4 + position - reported_usd` when the
  caller supplies reported or settled revenue (an owner export). Without `reported_usd` the actual IS the revaluation
  and the term is zero BY CONSTRUCTION: the PRD's closure then measures coverage, not accuracy. `summary["closure_basis"]`
  says which case applies (`revaluation` or `reported`). Closure = |sum unexplained| / |sum gap|; `closure_l1` (sum of
  absolute values) is the stricter version.
* **Absent rows.** `absent_rows="gap"` (telemetry, owner exports) or `"idle"` (net 0, no awards, SOC unchanged). Default:
  inferred from `actual.source`: a source starting with "paper" (D2's `ledger.as_actual`, which records only executed
  intervals) means idle; anything else means gap.
* **Order dependence.** The split depends on the order availability -> awards -> SOC. `shapley=True` computes all eight
  constraint subsets and averages the six orderings per interval; on the case study the largest difference to the
  sequential split is $1,750 (1.6% of the gap). The forecast term is always last, so it is order-free.
* Long periods (over 70 days) are solved in 30-day blocks with a one-day lookahead (tested equal to one LP).
* Diagnostics in the summary: `stage_monotone` (r0 >= r1 >= r2 >= r3 in total), `actual_model_violation` (the actual flows
  run through the LP dynamics from the telemetered start SOC of each day and checked against every LP constraint, plus the
  end-of-day SOC residual), awards clipped to what the LP can represent, SOC readings interpolated (gaps of at most two
  intervals), days without a SOC anchor.

Measured cost, 42 days, five products: the five stages about 25 s including the first compile, plus about 100 s for the
shadow and 40 s for Shapley.

## 7. Disclosure data, the pilot, and provenance

**Reports** (MIS `IceDocListJsonWS`, no key): NP3-965-ER "60-Day SCED Disclosure Reports" is reportTypeId **13052**
(938 daily zips posted 24 Mar 2024 to 10 Oct 2026, 23.4 GB listed, 55 MB each now) and NP3-966-ER "60-Day DAM Disclosure
Reports" is **13051** (931 zips, 7.7 GB). A file posted on day D holds operating day D - 60 (verified in the contents), so on
10 Oct the newest operating day is 11 Aug 2026. Inside the SCED zip, `60d_ESR_Data_in_SCED` has, per ESR and SCED run
(288 a day): HSL, LSL, base point, telemetered net output, **State of Charge, Minimum SOC, Maximum SOC**, AS capability and
RT AS awards per product. (`60d_SCED_SMNE_GEN_RES` has settlement-metered energy but NOT for ESRs, so there is no
metered-energy check.) Inside the DAM zip, `60d_DAM_ESR_Data` has hourly energy and AS awards, the ESR's settlement point
name, the DAM nodal SPP and the MCPCs; `60d_DAM_EnergyBidAwards` carries the DAM SPP of every hub and zone.

**Download.** Per the brief I staged 1 Jul to 11 Aug 2026 (the "about Jul-Aug" window): 42 SCED + 42 DAM zips, 2.77 GB, in
`DISPATCH_HOME/staging/ercot-60day/{sced,dam}/` with `manifest.csv` (columns `report_key, report_type_id, doc_id, url,
file, sha256, bytes, posted_utc, operating_day, downloaded_utc`; append only; same name and sha256 is a no-op, different
bytes under one name raises). At most 4 concurrent requests, 0.25 s spacing, exponential backoff on 429/5xx, browser-like
User-Agent; MIS answered 200 with an HTML page for 4 of 84 files under load, now retried (4 attempts). Nothing downloaded
is executed: zips are read in memory with `zipfile`/`pandas`, and every Python process that read them ran with `-I`.
Parsed caches are under `staging/ercot-60day/derived/` (rebuildable). The MIS window actually holds back to Jan 2024;
`disclosure.list_docs` and `download` fetch any other span.

**Pilot rules** (`disclosure.PilotCriteria`, written before looking at the result): 10 to 100 MW; rows on at least 40
of the 42 days; resource node a RESOURCE_NODE in NP4-160 (`settlement_points.csv`, Oct ML1) and priced in the 7-day nodal
window; best-hub RMSE <= $5/MWh and |bias| <= $2/MWh on that window; >= 0.3 discharge cycles/day and RT awards in >= 10%
of SCED runs; the node holds only this unit. Of 339 ESRs: 124 in the size range, 22 pass the basis rule, 3 pass all
(PAD1_ESS_ESR1, BEXAR_ES_ESR1, X4_ESR1). The lowest basis RMSE is **PAD1_ESS_ESR1**.

**The pilot (REAL unless stated).** `PAD1_ESS_ESR1`, resource node **PAD1_ESS_RN** (LZ_SOUTH; also the DAM disclosure's
`Settlement Point Name`), hub **HB_SOUTH**, 50 MW discharge and charge, 0 to 122 MWh, 12,163 SCED runs in the period
(100% of intervals covered). Round-trip efficiency 0.901 with losses on charge (DERIVED: energy balance of the whole period;
per-run medians 0.938, least squares 0.910). Standing auxiliary draw 0.4 MW = $11.0k over the period (DERIVED: median
telemetered output at zero base point), removed from dispatch because it does not charge the battery (SOC does not move) and
cancels in the gap. Events in the period: HSL/LSL cut to 27 MW on 18 Jul and 30 MW on 23 Jul, to 30/0 MW on the afternoon of 3 Aug,
Maximum SOC cut from 122 to 89 MWh and Minimum SOC raised to 9 MWh from 4 Aug.

**Prices.** Real-time energy: hub HB_SOUTH 15-minute SPP from `research-data/ercot/rtm2026.csv` (through 26 Sep), a
labelled PROXY for the node. Measured on the 7-day nodal window (717 intervals, 3 to 10 Oct, REAL): node minus hub mean
+$1.01, MAE $1.78, 95th percentile $5.22, RMSE $2.93 per MWh, correlation 0.9989. Same-period check (REAL): the DAM SPP at
the node against HB_SOUTH over the 1,008 hours of the case study: mean +$2.95, MAE $2.96, RMSE $6.59, correlation 0.965.
Real-time AS: RT_15 MCPC from `as_prices.parquet` (since 5 Dec 2025).

## 8. Gate 2: attribution closes on a pilot asset

Case study: 1 Jul to 11 Aug 2026 (42 operating days), net of a $20/MWh wear charge (ASSUMPTION) unless noted. Full report:
`reports/case_study/case_study.md`.

| | USD | Share of perfect foresight |
|---|---|---|
| Perfect foresight (free awards) | 149,553 | 100% |
| Shadow optimizer (seasonal-naive, hourly) | 100,117 | 66.9% |
| Actual, real-time dispatch and awards | 21,062 | 14.1% |
| Actual, day-ahead positions | 22,204 | 14.8% |
| **Actual, total** | **43,266** | **28.9%** (37.6% with no wear charge) |
| Gap | 106,286 | 71.1% |

Gap split (sequential): ancillary awards $74,566 (70.2%), forecast and decision $17,597 (16.6%), SOC position $9,018 (8.5%),
availability $5,106 (4.8%), unexplained $0 (0.0%). Shapley average of the first three: ancillary $76,316, SOC $7,803,
availability $4,571 (forecast unchanged).

* **Closure on the PRD definition: 0.0%, PASS (target < 5%), on 100% of intervals; components sum to the gap to 2e-13 USD.**
  Qualification, stated plainly: this is zero by construction because public data has no settled revenue (`closure_basis` =
  `revaluation`). It becomes a real test when an owner export is passed as `reported_usd`.
* **Stress test (the honest substitute): FAIL.** The share of the gap the hub-for-node proxy alone could move is up to
  11.1% with the October real-time basis (seven daily draws, from -2.0% to +11.1%) and 6.0% with the same-period day-ahead
  basis; the gap itself moves by 0.8% (DAM basis) to 9.2% (worst October day). Both are above 5%, so closure below 5% against
  SETTLED revenue is not demonstrated until the node price or a settlement statement is used.
* **What the numbers do and do not say.** (1) Ancillary services are 70% of the gap and that rests on the benchmark's
  assumptions (price-taker, every offered MW awarded, no deployment energy or wear, regulation up and down held together,
  about 44 MW of each): against energy + RRS/ECRS/NSPIN the actual's capture is 42.9%, energy only 41.0%. (2) The shadow beats
  the actual only because it sells AS: energy-only it earns $24,152 against the actual's $32,454 (the actual's day-ahead energy
  positions, $18,757, are not available to the real-time-only shadow); without regulation $37,440 against $38,710. "Our optimizer
  would have recovered 53% of the gap" is therefore a statement about ancillary capacity under a price-taking assumption.
  (3) Capture moves with the wear assumption: 37.6% at $0, 37.0% at $10, 28.9% at $20, 14.5% at $30 per MWh discharged.
* The tests in `tests/test_attribution.py` check, on synthetic assets, that actual = perfect foresight gives no gap, idle
  gives gap = perfect foresight, injected derates, SOC-limit cuts and award differences land in their own components, an SOC
  drain lands in the SOC component of the next day, data gaps are unexplained, closure is below 5% on clean data (with and
  without an owner export), and the real summary file passes the same check.

## 9. How D2 calls `solve` and `attribute`

```python
from dispatch.optimizer import lp
from dispatch.contracts import BatterySpec

lp.warmup(spec)                      # once at startup: imports cvxpy/HiGHS and compiles (about 7 s); then ~40 ms per solve

# per cycle (the cycle already does this):
view = forecast_frame.price_view("p50")        # tz-aware UTC 15-minute index, contiguous; rt_usd_per_mwh (+ as_*_usd_per_mw_h)
plan = lp.solve(spec, view, soc0_mwh, mode="forecast",
                availability=None,             # or a Series of FRACTIONS of power_mw in [0, 1] (MW raises ValueError)
                terminal_soc_mwh=None)
if not plan.ok:                                # plan.status != "optimal": no setpoint; audit plan.status, plan.solver and
    ...                                        # plan.frame.attrs["diagnostics"]
sp = lp.first_setpoint(plan, model_version=forecaster.version, issued_utc=now)   # Setpoint(net_mw, as_mw, plan_id=sha[:16])
```

* Exceptions (`ValueError`) mean invalid inputs: treat as fail closed (error status), as the cycle already does.
* The plan frame starts at `view.index[0]` (the cycle checks this). `net_mw = discharge - charge`; AS awards are `as_<p>_mw`
  with products in `spec.as_products`; `wear_usd` is a positive cost; revenue = `energy_usd + as_usd - wear_usd`.
* Thread safety: compiled models are cached per thread; each new thread pays the first-compile cost once.
* Gate 3: `backtest.Backtest(price_source, forecast_fn, ledger=ledger)` is the `backtest_fn` for `create_app(backtest_fn=...)`
  (section 5.1). The Redis move to localhost:6390 needs nothing from D1b: nothing in `optimizer/` or `attribution/` talks to Redis.

```python
from dispatch.attribution import engine
df = engine.attribute(spec, actual, prices, forecast_fn)       # what wiring.attribute already calls
df.attrs["summary"]                                            # totals, capture, closure, closure_basis, notes
res = engine.run_attribution(spec, actual, prices, forecast_fn, position_usd=None, reported_usd=None,
                             shapley=False, shadow_step_intervals=4)       # stages, Shapley, shadow frame
engine.explain_top(res, 20)
```

* `actual`: `ActualDispatch` whose frame has `net_mw` (+ discharge) and optionally `as_<p>_mw`, `available_mw` (derate-only
  MW), `available_charge_mw`, `soc_mwh` (END of interval), `soc_min_mwh`, `soc_max_mwh`; NaN means unknown. `ledger.as_actual`
  (source `"paper-ledger"`, executed rows only) works as is: absent rows are idle.
* `prices` must be complete on the same 15-minute UTC grid (NaN real-time prices raise); AS columns optional.
* `forecast_fn(issued_utc) -> ForecastFrame` whose frame STARTS at `issued_utc`. If it starts later every shadow step idles and
  `summary["shadow_failed_solves"]` says so. Pass `None` to skip the shadow.
* Cost: without the shadow a month takes about 15 to 25 s; the shadow adds about 2.5 s per day of the period (five
  products). `/attribution` allows 93 days: with a forecast function that is several minutes, so run it as a background job
  or pass `forecast_fn=None` (see REQUESTs).
* The returned frame has `interval_start_utc` as a COLUMN and a RangeIndex (the endpoint's `df.reset_index()` then adds a
  harmless `index` field).

## 10. REQUESTs

* **REQUEST (D1b -> lead): document the optional kwargs in `contracts.Optimizer` and DESIGN.md.** `lp.solve` also accepts
  `fixed_energy`, `charge_availability`, `charge_limit_mw`, `soc_min_series`, `soc_max_series` (all keyword-only, default
  `None`, so the Protocol is still satisfied: a test checks the signature). State in DESIGN.md that `availability` is a
  FRACTION of `power_mw`, that `Plan.frame.wear_usd` is a positive cost, and that `frame.attrs["diagnostics"]` exists.
* **REQUEST (D1b -> lead): list the optional `ActualDispatch.frame` columns** in `contracts.py`: `available_charge_mw`,
  `soc_min_mwh`, `soc_max_mwh` (and that `available_mw` means derates only, not SOC-limited HSL), plus the new
  `absent_rows` rule keyed on `source` starting with "paper".
* **REQUEST (D1b -> lead): add `STAGING = HOME / "staging"` to `paths.py`** (and to `ensure()`); `disclosure.staging_dir()`
  currently builds `paths.HOME / "staging" / "ercot-60day"` itself.
* **REQUEST (D1b -> lead): add `matplotlib` to `dispatch/requirements.txt`** (report charts; imported lazily and skipped
  with a log line if missing; 3.11.2 is installed in the venv).
* **REQUEST (D1b -> lead): DESIGN.md Gate 2 wording.** Add that on public data the PRD closure is zero by construction
  and the pass criterion for a design partner is closure against their settlement statement (`reported_usd`); until then
  the report also states the price-proxy stress (11.1% here, above 5%).
* **REQUEST (D1b -> D0 via lead): adopt `DISPATCH_HOME/staging/ercot-60day/{sced,dam}/*.zip`** into the lake (sources
  `ercot_60day_sced` 13052 and `ercot_60day_dam` 13051; `manifest.csv` has url, sha256, bytes, posted and downloaded time,
  operating day). `60d_ESR_Data_in_SCED` is also a source of real ESR state-of-charge and Min/Max SOC per SCED run, which no
  other public report has; and `60d_DAM_EnergyBidAwards` has the DAM SPP of every hub and zone back to Jan 2024.
* **REQUEST (D1b -> D2 via lead): `/attribution`** should call `attribute(..., forecast_fn=None)` for requests over a few
  days, or run the shadow as a background job and cache by `summary` inputs; and `ledger.as_actual` frames are handled (idle
  rows) but `price_source` must return complete prices.
* **REQUEST (D1b -> D2): wire the backtest as in section 5.1** (`create_app(ledger=ledger, backtest_fn=Backtest(price_source,
  forecast_fn, ledger=ledger))`), with the SAME point-in-time `forecast_fn` the cycle uses (the `create_app(forecast_fn=...)`
  you already take). The first call after a start or a model swap takes about 90 s for a week of one asset (then 0.05 s), so
  keep it on the monitor's own thread (it is) and do not call it from a request handler. If a model swap inside the window
  should not count as slippage, pass a forecaster that routes by issue time; `Backtest` uses one per asset.
* **REQUEST (D1b -> D1a via lead):** nothing blocking. `rolling.registry_forecast_fn(alias, features_fn)` needs a features
  function whose first target is the interval being decided; if D0's `point_in_time` starts at the next interval, pass
  `asof = decision - 15 min` inside `features_fn`.

## 11. Limits, open items, how to rerun

* The benchmark's AS assumptions drive 70% of the case-study gap (section 8); real SCED, deployment energy and offer
  strategy would lower it. Treat the energy-only and no-regulation rows as the more conservative reading.
* v1 has no day-ahead bidding in the optimizer; the actual's day-ahead positions are valued but cannot be matched by the shadow.
* One asset, 42 days, hub proxy, a $20/MWh wear assumption: not a fleet statistic. The October nodal window is thin for July
  and August.
* In 44 intervals (1.1%) the asset's discharge plus up awards exceeded its HSL by up to 15 MW and in 78 (1.9%) charge plus
  REGDN exceeded the charge limit by up to 18 MW (ERCOT counts headroom against the base point, not telemetry); the LP's
  conservative constraints are not applied to the actual, only reported. End-of-day SOC from the LP dynamics is within 1.6 MWh
  (mean absolute, worst 6.0) of the telemetered one over 42 days.
* A shadow with a trained D1a model is the obvious next run (`registry_forecast_fn`); so is the same case study on the other
  two pilots that pass the rules.
* **Test status after the Gate 3 round (10 Oct 2026).** Mine: 106 passed (`test_lp` 46, `test_attribution` 24,
  `test_backtest` 26, `test_units` 10). Others that touch the optimizer, run after the `rolling.py` refactor: D2's
  `test_monitoring` + `test_audit` 101 passed (Gate 3 on the real LP and `run_shadow`, slippage 0); `test_api` +
  `test_registry` + `test_rebuild` 128 passed; D1a's LP-dependent `test_models` tests 4 passed. An earlier full run had
  `test_models.py::test_decision_value_runs_on_the_real_lp_and_shares_the_perfect_foresight_run` fail once while the machine
  was saturated; it passes alone and with the whole of `test_models.py`, and `lp.py` has not changed since. A later single
  full-suite run stalled for 40 minutes at `test_models.py::test_batched_arena_path_equals_calling_the_planner_decision_by_decision`
  (arena DP only, no LP) with the machine's memory nearly exhausted by D1a's training jobs (two `dispatch.models.train`
  processes); it was stopped, and that test passes alone in 18 s. D2's `test_guard_props.py::test_generator_reaches_both_outcomes`
  (a Hypothesis statistic, 198 of 200 needed) failed once in the earlier full run and is not mine.

Rerun (from `hugging-base/dispatch`, `PYTHONIOENCODING=utf-8`):

```
.venv/Scripts/python.exe -m pytest -q tests/test_lp.py tests/test_attribution.py tests/test_backtest.py
.venv/Scripts/python.exe -m dispatch.optimizer.dp_check          # 300-day cross-check -> reports/case_study/dp_crosscheck.csv
.venv/Scripts/python.exe -m dispatch.attribution.report          # about 6 minutes -> reports/case_study/
```

Data downloads for other windows: `disclosure.list_docs("sced")`, `disclosure.select_days(...)`, `disclosure.download(...)`.
