# Phase 3a: site-value infrastructure (10 Oct 2026)

Agent 3a. This phase builds the pieces agent 3b needs to rebuild nodal price lanes and value candidate sites: a group-cap hook
in the arena's siting rules, a 4-hour battery spec, a `NodalMarket` that runs arbitrary lanes through the arena's dispatch with no
arena edits, a parallel runner, a separate ancillary-service (AS) layer, connection cost classes per substation, and a map from
queued and operating storage to substations. None of it needs shift factors. Every table has a `.labels.csv` sidecar; every
constant is a named value with a label (the full list is in section 9).

Run everything from `docs/research/grid` with `../research-data/.venv/Scripts/python.exe`.

## Read this first

- **The only arena edit** is the optional `groups` argument of `siting.allocate` and `siting.portfolio`. With `groups=None` the
  result is bit-identical to the backup copy: 1,680 randomised cases (960 `allocate`, 720 `portfolio`; every rule A1, A2, A3, A4,
  A5, A5ts; with and without `avail` and `caps`; fixed seeds) compared with `np.array_equal`, no tolerance. No other arena file
  changed (checked byte for byte against `research-data/grid/_backup_docs_research_20261010`).
- **A `NodalMarket` built from the 8 zone lanes reproduces the arena exactly.** For all of 2024 (366 days, 8 lanes, A1 to A5, profit,
  energy, wear, MWh, stored energy and the learner's picks) the ledgers are identical to the arena's own `simulate_market` steps.
  A 30-day slice is also identical to the arena's stored backtest file.
- **The runner's chunked results equal its single-process results exactly**, lane by lane. To make that true the runner's default
  context is lane-safe: the arena couples lanes in two places (see section 2), and the lane-safe version moves the 2024 zone-lane
  results by at most $0.05/kW-yr (A3 only; A1, A2, A4, A5 unchanged to the cent).
- **Throughput:** **53 lane-days/s** (8 workers, 16 lanes a chunk, spec `grid`; 11.0 in one process; 37.6 on the 4 h spec), measured on a 4-core laptop shared with other jobs. 1,000 lanes over 3.7 years is about 7 hours for one 2 h spec. Section 5.
- **AS prices have collapsed**, so the AS line is now small. DAM Reg Up averaged $26.79/MW-h in 2023 and $2.11 in 2026 to 10 Oct.
  The most a 2 h battery could earn from AS (all capacity in the best product every interval) fell from $328/kW-yr in 2023 to
  $79 (2024), $31 (2025) and $31 (2026, annualised); a 4 h battery gets $34.8 and $44.4 in 2025 and 2026. Section 6.
- **Connection costs:** 5,033 substations of 69 kV or more. New bay 4,648 (2,484 on graph evidence, 2,164 by default), new switchyard
  158, gen-tie 227. Almost every class fits inside the 16 TAC 25.195 allowance; only long gen-ties and 345 kV switchyards exceed it.
  Lead time is about 56.5 months almost everywhere because a large power transformer governs. Section 7.
- **Queue map:** 89.9% of queued storage MW (141.4 of 157.3 GW) and 86.4% of EIA battery MW (38.1 of 44.1 GW) map to a substation at
  confidence >= 0.5. About half of the planned EIA MW duplicates a queue project: drop those before summing. Section 8.

## 1. What each module does

| File | What it does |
|---|---|
| `arena/arena/siting.py` (the hook) | `groups=[(option_indices, cap_mw), ...]` for `allocate` and `portfolio`. Options in a group share one MW cap; a full group blocks every member. The cap is cumulative over the fleet, like `caps`, never per year. |
| `gridsite/specs.py` | Registers `grid4h` (1 MW / 4 MWh: 32 energy units, no reserve, RTE 0.85, 4 cycles a day, wear $20/MWh) in `arena.config.SPECS` at import. Runtime only. |
| `gridsite/nodal_market.py` | `NodalMarket` (a `Market` for any lanes), `run_slice` (the arena's `simulate_market` steps for any market), the lane-safe analog search and context, `zone_lanes()` and `arena_zone_of_substations()` to build inputs. |
| `gridsite/runner.py` | `run_lanes` (chunked, parallel, resumable), `oracle_lanes` (chunked perfect-foresight ceiling), `LaneResults`, lane providers, a benchmark provider. |
| `gridsite/ancillary.py` | The AS layer: `as_upper_bound`, `as_vs_energy`, `oracle_with_as`, `as_oracle_uplift`, `AsGrid`, and `data/as_revenue.csv`. Separate from the energy-only arena and labelled as such. |
| `gridsite/costs.py` | Connection class, cost range, allowance, owner share and lead time per substation: `data/connection_costs.csv`. |
| `gridsite/queue_map.py` | Queued (GIS report) and operating or planned (EIA-860/860M) storage mapped to NP4-160 substations: `data/queue_sub_map.csv`. |
| `gridsite/cli_3a.py` | `compat-test`, `as-layer`, `costs`, `queue-map`, `runner-bench` for `run_grid.py`. |
| `tests/test_arena_compat.py`, `tests/test_3a_*.py`, `tests/_harness_3a.py` | The tests. The research venv has no pytest, so each file runs as a script: `python tests/test_3a_runner.py [name-substring ...]`. |

## 2. How `NodalMarket` fits the arena, and the two lane couplings

`NodalMarket(lane_ids, P, ref, dates, lane_zone)` carries exactly the attributes the arena reads from `Market`: `name` ("ERCOT", which keys
the hot/cold/spike thresholds, storm rules and the analog spike level), `zones` (the lane ids), `dates`, `P` (float64, days x lanes x 96),
`ref`, `years`, `month`, `doy`, `weekend`, `W` (weather) and `gas`. A lane's weather is the column of its load zone in the arena's
`weather("ERCOT", dates)`; gas is the arena's `gas(dates)`. Lane ids are added to `arena.config.LABEL` at run time (never overwriting
an existing label), because `RegionModels.coef_table` and the report tools look labels up by zone name.

With the arena's own classes (no gridsite code in the path) a `NodalMarket` of the 8 zone lanes gives:

- identical `RegionModels` (features, every ridge and logistic coefficient, spike probabilities);
- `find_analogs(nodal)` equal, bit for bit, to the arena's stored cache `analogs_past_ERCOT.npz`;
- identical `run_combined` ledgers.

**The arena couples lanes in two places**, which matters once lanes are processed in chunks:

1. `analog_matrix` standardises the analog features with the mean and standard deviation pooled over all lanes of the market.
2. `BacktestContext` weights analog days with a kernel whose bandwidth is the median distance pooled over all lanes of the day.

`run_combined` itself is lane-independent (verified: every policy acts elementwise on lanes). So with the arena's context a lane's numbers depend
a little on which lanes share its chunk. The runner's default `context="lane"` removes both couplings: fixed standardisation (the arena's own
8-zone statistics, cached in `research-data/grid/derived/nodal_analog_ref_stats.npz`) and a per-lane bandwidth. `context="arena"` keeps the
arena's pooled behaviour and is exact only when the lanes are the arena's 8 zones. The two contexts differ by at most $0.05/kW-yr on the 2024
zone lanes (A3: -0.02, 0.01, 0.05, 0.02, -0.01, 0.02, -0.02, 0.00; the other algorithms to the cent). Over a short window the annualised difference is noisier:
in the 30-day January 2015 slice A3 on LZ_CPS differs by $1.2/kW-yr (the other 39 algorithm-lane cells by 0.01 or less).

## 3. APIs for agent 3b

Add the arena to the path with `gridsite.paths.use_arena()`; `import gridsite.specs` registers `grid4h`.

### 3.1 Group caps (arena hook)

```python
from gridsite import paths; paths.use_arena()
from arena import siting
siting.allocate(rule, values, y_idx, mw=10, avail=None, caps_left=None, rng=siting.RNG, groups=None)
siting.portfolio(rule, values_known, values_real, y0_idx, n_years, mw_per_year=10, avail=None, caps=None, rng=siting.RNG, groups=None)
```

`groups` is a list of `(option_indices, cap_mw)`; indices may also be a boolean mask. In `portfolio` the cap limits the fleet's total MW
over the group's members across the whole build (cumulative, the same semantics as `caps`); a full group blocks all its members, which then
overflow to the next best option under each rule. An option may sit in several groups (all must have room). In `allocate` the number is the MW still
free (`caps_left` convention); `portfolio` passes `cap - fleet[group].sum()` each year. A group with less than 1 MW left is full (MW are whole).
Example, nodes behind the same constraint sharing 600 MW:

```python
groups = [(np.flatnonzero(behind_constraint_k), cap_mw_k) for k in constraints]
siting.portfolio("A5", V, V, y0, 5, 10, avail, node_caps, rng, groups=groups)
```

### 3.2 The 4-hour spec

```python
from gridsite import specs
specs.register()                    # idempotent; also done at import
from arena.battery import Spec, oracle_days
Spec("grid4h")                      # E=32 units, R=0, rte 0.85, cap 128 units/day, usable 4 MWh
specs.hours("grid4h")               # 4.0
```

Worker processes started with spawn begin with a fresh `arena.config`; `runner.run_lanes` registers the spec in every worker. If you start your
own processes, import `gridsite.specs` there.

### 3.3 Lanes and the runner

```python
from gridsite import nodal_market as N, runner
dates, zone_ids, Pz, ref = N.zone_lanes()                  # arena's 8 zone lanes + HB_BUSAVG, bit-identical to Market
sub_zone = N.arena_zone_of_substations()                   # {sub: arena zone for weather}; NOIE zone first, else load zone
nm = N.NodalMarket(lane_ids, P, ref, dates, lane_zone)     # P (days, lanes, 96) $/MWh; lane_zone dict lane -> arena zone

res = runner.run_lanes(lane_ids, P, ref=ref, dates=dates, lane_zone=lane_zone, spec="grid4h",
                       start="2023-01-01", end=None, workers=8, chunk=16, context="lane",
                       cache_dir="C:/.../research-data/grid/derived/_lane_cache", progress=True)
res.usd_kw_yr("A5", 2025)        # (lanes,) $/kW-yr; a year with < 360 simulated days is scaled to 365 like arena.yearly()
res.usd_per_mwh("A5", 2025)      # profit after wear per MWh discharged
res.algos["A5"]["profit"]        # (days, lanes) $ per MW per day; fields profit, energy, wear, mwh for A1..A5
res.picks                        # (days, lanes) int8, the learner's expert (runner.EXPERT_NAMES)
res.pick_shares("NODE_X"); res.table(2025); res.lane("NODE_X"); res.save(path); runner.LaneResults.load(path)
runner.LaneResults.merge_lanes([r1, r2])                   # separate runs, same dates / spec / context, different lanes
ceiling = runner.oracle_lanes(P[day_idx], "grid4h", workers=8)          # (days, lanes) perfect-foresight $ per MW per day
```

Inputs and rules of thumb:

- `dates` is the whole history the models and analogs may use (contiguous ISO days, strictly increasing); `start` and `end` pick the simulated days.
  Walk-forward models need more than 365 days before a year, and `find_analogs` needs earlier days within +-30 days of the same calendar date: give at
  least two years of history before `start`, or the early simulated days run without a spike model and with few analogs.
- Prices must be on the arena clock: 96 fifteen-minute intervals a day, the repeated fall-back hour dropped and a skipped hour repeating the hour before (PLAN.md).
- **Include the 8 zone lanes (and HB_BUSAVG as a lane if you want a hub) in the same call** as the nodes. Node minus zone is then like for like, and the results do not
  depend on what else is in the call (lane-safe context). The 2024 zone-lane numbers from the runner equal the arena's to within $0.05/kW-yr.
- Many lanes: pass `provider` instead of `P`, a picklable callable `provider(lane_ids) -> (days, len(lane_ids), 96)` that each worker calls for its own chunk
  (`runner.NpyProvider(path, all_lane_ids)` reads columns of an `.npy` through mmap). The parent then never holds all prices. A provider run with `cache_dir`
  needs a `cache_token` string that changes when the provider's data does.
- `cache_dir` makes a run resumable: each finished chunk is stored under a hash of lanes, zones, spec, context, dates, reference and prices; a rerun
  loads the finished chunks and computes the rest.
- A lane with NaN prices raises (`fill_gaps=True` forward-fills, the arena's rule for isolated gaps). `spec="home"` also runs (smoke-tested on 2 lanes for 15 days; not in the suite).

### 3.4 Ancillary-service layer

```python
from gridsite import ancillary as A
A.as_upper_bound(2025, "grid4h", market="DAM", stack_regdn=False)
#   -> {"usd_kw_yr", "usd_kw_covered", "n_hours", "coverage", "partial", "by_product", "best_share", "mean_best_usd_mw_h", "top1pct_share", ...}
A.as_vs_energy(prices, energy, wear, mwh, dates, spec="grid", market="DAM", soc_aware=True, stack_regdn=False)
#   prices (days, 96) the lane's energy prices; energy, wear, mwh (days,) one lane and algorithm from res.algos[algo]
#   -> {"by_year": {year: {"energy", "as_idle", "as_swap", "as_total", "total", "coverage", "partial"}}, "days": {...}}   $/kW-yr
A.oracle_with_as(P, dates, spec, market, stack_regdn=True)           # (days,) perfect-foresight energy + AS, $ per MW per day
A.as_oracle_uplift(P, dates, spec, market)                           # {year: {"energy_oracle", "energy_plus_as_oracle", "as_uplift", ...}}
```

`as_vs_energy` takes columns of a `LaneResults`: `A.as_vs_energy(P[:, i, :], res.algos["A5"]["energy"][:, i], res.algos["A5"]["wear"][:, i], res.algos["A5"]["mwh"][:, i], res.dates, "grid4h")`.
The AS value does not vary by site except through what the site's energy dispatch leaves free, so report the AS line as its own column, never inside the energy $/kW-yr.

### 3.5 Connection costs and the queue map

```python
import pandas as pd
cc = pd.read_csv("data/connection_costs.csv", keep_default_na=False, na_values=[""])      # key: sub
q  = pd.read_csv("data/queue_sub_map.csv",    keep_default_na=False, na_values=[""])      # key: inr_or_plant
from gridsite import costs as K
K.connection_costs(mw=250.0)       # recompute the table for another battery size (step-up cost and the LPT rule depend on MW)
sat = q[(q.conf >= 0.5) & ~((q.source == "eia") & (q.stage == "planned") & (q.overlap_conf > 0))]
mw_by_sub = sat.groupby("sub").mw.sum()          # queue + operating storage per substation, planned EIA duplicates of queue rows dropped
```

**Always read the substation tables with `keep_default_na=False, na_values=[""]`.** ERCOT has a substation whose code is the string `NA` (17 buses in
`settlement_points.csv`); pandas turns it into a missing value by default and the sub silently drops out of joins.

## 4. Tests (all pass)

| File | What it proves | Result |
|---|---|---|
| `tests/test_arena_compat.py` | `groups=None` is bit-identical to the backup `siting.py` imported by file path: 960 `allocate` + 720 `portfolio` cases (A1 A2 A3 A4 A5 A5ts, avail on/off, caps on/off, fixed seeds), the default-RNG path in lockstep, the run_arena.py call shapes; the signature is backward compatible; no other arena file differs from the backup; a mutated copy of the backup per rule is detected (the comparison has power); group caps bind for every rule, are cumulative, block all members, handle overlaps, per-option caps, `avail`, all-blocked, validation, boolean masks, and a hand-traced A3 case | 18/18 pass (14 s) |
| `tests/test_3a_specs.py` | `grid4h` values, `Spec("grid4h")` with `oracle_days` (closed-form day: 4 MWh earns exactly twice the 2 MWh battery) and `run_combined` (14 days, 3 lanes, all five algorithms) | 5/5 pass (50 s) |
| `tests/test_3a_nodal.py` | every attribute equals `Market`'s; identical `RegionModels`; the arena's `find_analogs` on the nodal market equals the stored cache; lane-safe helpers equal the arena's on the zone lanes; the pooled context equals `BacktestContext`; per-lane bandwidth does not depend on other lanes; **30 days x 8 zones x A1-A5 identical to `simulate_market` steps and to `arena_cache/backtest_ERCOT_grid.npz`**; input validation, labels and zone weather for new lanes | 10/10 pass (136 s) |
| `tests/test_3a_runner.py` | **chunked (3 and 2 workers, uneven chunks) == single process, exactly**; reversed lane order, new chunk-mates and a lane alone give the same numbers; provider paths (array, npy mmap) equal the array path; results container; resumable cache; chunked `oracle_days` equals `oracle_days` lane by lane (2 h and 4 h); a bad lane raises in the parent | 8/8 pass (571 s while the machine was shared) |
| `tests/test_3a_ancillary.py` | eligibility by duration; upper bound by hand on a synthetic grid; real-data reproduction of the documented REGUP means; DST handling of the price grid; the DP equals `oracle_days` with zero AS prices and matches closed forms (RRS 238.75, Reg Down 240, both 473.75); bounds; footprint and state-of-charge logic; `as_vs_energy` accounting by hand; ledger estimate <= DP ceiling; CSV and labels | 11/11 pass (32 s) |
| `tests/test_3a_costs.py` | unit-cost lookups; classes, costs and owner shares by hand on a 7-substation synthetic grid; lead-time rules; real-table invariants (ordering of low/mid/high, shares, allowance, geometry only where the placement is reliable, a 25-row recomputation); CSV and labels | 5/5 pass (14 s) |
| `tests/test_3a_queue_map.py` | bus-number extraction, name helpers, the mapping rules on a tiny model, stage labels, real-table invariants (MW conserved, every mapped sub is an NP4-160 substation, bus-number rows re-derived independently, coverage), the tie-aware nearest rule, the calibration numbers, CSV and labels | 8/8 pass (132 s) |

`run_grid.py compat-test` runs the siting tests and the NodalMarket equivalence (30-day slice by default, `year=2024` for the full year in three
processes, about 7.5 minutes). The 2024 run, 10 Oct 2026:

```
days 366  lanes 8  wall 444s
NodalMarket ledgers == arena ledgers (A1-A5 profit/energy/wear/mwh, backup energy, learner picks): IDENTICAL
```

## 5. Throughput of the runner

Machine: AMD Ryzen 7 3700U (4 physical cores, 8 threads), 9.9 GB of RAM, Windows 11, Python 3.14. **It was shared**: during these runs two other agents' Python jobs each kept about one core busy, so read the numbers as a floor. The brief says 8 cores; there are 8 hardware threads on 4 cores.

Workload (`run_grid.py runner-bench lanes=128 days=60 matrix`): 128 synthetic nodes (a zone price lane scaled, shifted by a daily wiggle and given noise; SIM, for timing only), 60 simulated days (29 Jul to 26 Sep 2026), the full 2011 to 26 Sep 2026 history (5,748 days) behind every lane for the models and analogs, spec `grid`, all of A1 to A5 and the learner. Throughput = lanes x simulated days / wall time, including process start, region models, analog search and dispatch.

| workers | lanes per chunk | lanes | wall, s | **lane-days/s** | setup per chunk, s | dispatch per chunk, s | peak RSS of a worker, MB |
|---:|---:|---:|---:|---:|---:|---:|---:|
| 1 (in process) | 16 | 16 | 87 | 11.0 | 6.5 | 74 | 380 (the parent, with its own Market) |
| 4 | 16 | 128 | 203 | 37.8 | 7.6 | 79 | 236 |
| 8 | 8 | 128 | 214 | 35.8 | 5.2 | 82 | 155 |
| **8** | **16** | 128 | 144 | **53.4** | 8.9 | 84 | 235 |
| 8 | 32 (4 chunks: only 4 workers busy) | 128 | 210 | 36.7 | 20.0 | 145 | 399 |
| 8 | 16, spec `grid4h` | 128 | 204 | **37.6** | 14 | 125 | 235 |

- **53 lane-days/s** with 8 workers and 16 lanes a chunk, 4.9 times the single process (11.0). The 4-worker run gets 3.4 times; the last 40% comes from using the second hardware thread of each core.
- Dispatch time per chunk is nearly flat from 8 to 16 lanes (82 s and 84 s per 60 days): `run_combined` spends its time on Python and small-array overhead per interval, not on the lanes, so a bigger chunk is cheaper per lane.
  At 32 lanes a chunk takes 145 s (about 14% cheaper per lane than at 16) but a worker needs 400 MB; eight such workers do not fit in the RAM this machine has free, and 128 lanes make only 4 chunks. 16 is the default (`runner.DEFAULT_CHUNK_LANES`).
- Cost planning: 53 lane-days/s is 6.9 s of wall time per lane-year on the 2 h spec. 1,000 lanes x 3.7 years (Jan 2023 to Sep 2026) = 1.35 million lane-days, about 7 hours for one spec. Screen first (zone and hub controls plus the top few hundred candidates), keep `cache_dir` on, and extend.
- Memory: a worker with 16 lanes over the whole 2011 to 2026 history peaks at about 235 MB, so 8 workers need about 1.9 GB.
- The chunked oracle (`oracle_lanes`) takes about 5.4 s per lane-year on the 2 h spec and 21 s on the 4 h spec in one process; it parallelises the same way.
- The 4 h spec costs about 40% more per lane-day than the 2 h spec (37.6 against 53.4 lane-days/s): its dynamic programmes carry 33 states instead of 17. The 4 h run overlapped the end of a queue-map job, so it is if anything slightly pessimistic.

## 6. The AS layer

Source: `research-data/grid/derived/as_prices.parquet` (agent 1a): DAM hourly clearing prices for capacity from Dec 2010; RT (RTC+B, 15-minute settlement-interval
prices) from 5 Dec 2025. Sustain times (hours) are `AS_DURATION_H_*` (NPRR1282: Reg and RRS 30 min, ECRS 1 h, Non-Spin 4 h; the final Non-Spin number is UNVERIFIED).
A product that must be sustained for D hours can take `min(1, E / (P D))` of the capacity: a 2 h battery can sell 100% of Reg, RRS and ECRS but only 50% of Non-Spin; a
4 h battery can sell everything.

**The collapse (REAL).** Mean DAM clearing price, $/MW-h:

| | 2021 | 2022 | 2023 | 2024 | 2025 | 2026 (to 10 Oct) |
|---|---:|---:|---:|---:|---:|---:|
| Reg Up | 244.37 | 21.67 | 26.79 | 6.01 | 2.98 | 2.11 |
| Reg Down | 95.57 | 8.46 | 13.63 | 3.44 | 1.62 | 1.31 |
| RRS | 312.24 | 20.31 | 21.26 | 5.67 | 2.76 | 1.64 |
| ECRS | | | 58.16 (10 Jun to 31 Dec) | 8.23 | 2.46 | 2.01 |
| Non-Spin | 134.00 | 22.49 | 19.05 | 6.22 | 3.05 | 4.21 |

Real-time clearing prices since RTC+B (2026 to 10 Oct): Reg Up 1.48, Reg Down 1.09, RRS 0.57, ECRS 0.95, Non-Spin 2.71: below day-ahead for every product.

**Upper bound** (`as_upper_bound`, all capacity in the best eligible product in every 15-minute interval), $/kW-yr, DAM:

| year | 2 h battery | 4 h battery | + Reg Down stacked (2 h / 4 h) | share earned in the top 1% of intervals |
|---|---:|---:|---:|---:|
| 2022 | 210.7 | 238.2 | 279.5 / 307.3 | 0.29 |
| 2023 | 328.3 | 336.4 | 440.5 / 448.8 | 0.50 |
| 2024 | 79.4 | 82.1 | 108.0 / 110.7 | 0.42 |
| 2025 | 31.1 | 34.8 | 43.7 / 47.5 | 0.17 |
| 2026 (annualised from 78% of the year) | 31.0 | 44.4 | 37.7 / 51.6 | 0.27 |

RT (2026, 78% of the year, annualised): 22.8 (2 h) and 32.2 (4 h). RT 2025 covers 27 days and is not meaningful.
`data/as_revenue.csv` holds the same per year, market and product (120 rows) with eligibility, coverage and a `partial_year` flag. ECRS 2023 exists only from 10 Jun, so its
single-product row is annualised over that part of the year.

**Co-optimisation estimates** (`as_vs_energy`, `oracle_with_as`), 2 h, $/kW-yr, mean of the 8 zone lanes with the arena's stored ledgers and DAM AS prices:

| algorithm | year | energy (ledger) | AS in idle intervals | AS from swaps | total |
|---|---|---:|---:|---:|---:|
| A5 learner | 2023 | 108.3 | 130.3 | 63.1 | 301.7 |
| A5 learner | 2024 | 29.1 | 31.1 | 18.4 | 78.6 |
| A5 learner | 2025 | 17.9 | 13.4 | 2.5 | 33.8 |
| A5 learner | 2026* | 11.1 | 10.4 | 1.6 | 23.1 |

Perfect-foresight ceilings for LZ_NORTH, 2 h (4 h), energy-only oracle -> energy + AS oracle (Reg Down stacked): 2024 52.1 -> 132.6 (59.4 -> 141.1); 2025 39.3 -> 71.0 (50.4 -> 81.5);
2026* 41.0 -> 62.0 (56.6 -> 78.4).

How `as_vs_energy` works (ASSUMPTION-grade, no LP): the day's busy intervals are the dearest `mwh/0.25` intervals (discharge) and the same number of cheapest (charge), the arena's
energy convention; everything else is idle. Idle intervals sell the best product the state of charge allows: after a charge block the battery holds energy and may sell any
discharge-side product, after a discharge block it is empty and may sell only Reg Down (`soc_aware=False` lets every idle interval sell the best product, an optimistic bound). A busy
interval swaps to AS when AS pays more than the ledger's profit per busy interval (floored at zero, so a losing ledger does not make AS look better). The ledger profit is never reduced.
`oracle_with_as` is exact under perfect foresight: the arena's `oracle_days` dynamic programme with an AS reward that depends on the state of charge after each action (a product needs
`(SOC - reserve) U / D` MW of stored energy, Reg Down needs room, and full-power discharge or charge leaves no headroom on that side). It equals `oracle_days` when AS prices are zero (tested).

**What the collapse implies.**

1. AS is no longer a second business next to energy. In 2023 the AS ceiling was 1.8 times the energy-only ceiling (329.6 vs 180.9 on LZ_NORTH); in 2025 it is 0.8 times (31.7 vs 39.3) and what the arena's
   dispatchers could realistically add is $16-17/kW-yr in 2025 ($13 from idle intervals, $2-4 from swaps), about the same as their entire energy margin ($17.9 for the learner). In 2026 the estimate is $12/kW-yr.
2. AS prices are system-wide. The AS line is nearly the same at every site; it moves site rankings only through the energy dispatch it displaces. Keep it a separate column.
3. The bound is spike-driven in the good years (the top 1% of intervals earned 50% of the 2023 bound, 62% in 2021) and flat in 2025 (17%). Do not extrapolate 2023.
4. Duration matters again: Non-Spin is now the best-paid product (DAM $4.21 in 2026, RT $2.71) and needs 4 hours, so a 4 h battery's bound is 43% higher than a 2 h battery's in 2026
   ($44.4 vs $31.0) and 12% higher in 2025.
5. The market is thin: the fall from $27 to $2 came with the build-out of storage (18.2 GW of operating batteries in ERCOT in EIA-860M, `data/eia_generators_tx.csv`). More MW dilute, not add:
   do not scale a per-MW AS number by fleet size. The size of the AS requirement is not in our data (UNVERIFIED).
6. RTC+B moved prices lower in real time than in the day-ahead market, and awards are now bound by state of charge: the real number sits between the idle-interval estimate and the ceiling.

## 7. Connection cost classes (`data/connection_costs.csv`)

Per NP4-160 substation of 69 kV or more (5,033 rows; the 13 kV and 17.5 kV generator buses are out of scope). The cheapest feasible class by mid cost:

- **new_bay** (4,648): a through station (placed with match_conf >= 0.5 and at least two line terminations in the OSM line graph), 2,435 rows at class_conf 0.8; 49 stations that sit within
  0.5 km of such a station under another ERCOT code (class_conf 0.5, `colocated`); 2,164 rows by default (class_conf 0.3: 1,401 unplaced, 763 with match_conf < 0.5).
  The default is optimistic: it assumes a free position. Filter `class_conf >= 0.5` for a ranking.
- **new_switchyard** (158): not a through station, a line of 138 kV or more within 3 km, cheaper than a gen-tie.
- **gen_tie** (227): the nearest through station by straight line x 1.25. Median 4.8 km, 90th percentile 18.2 km, longest 82 km (one row flagged `long_tie`).

Costs are 2024 dollars from the MISO MTEP24 rows in `data/unit_costs.csv`; `cost_low/mid/high` are the three columns of each cited row. Medians (100 MW reference battery):

| class | cost mid, $M | owner share above the allowance, $M | all-in with step-up, $M |
|---|---:|---:|---:|
| new_bay | 2.42 (138 kV), 6.20 (345 kV) | 0 | 19.4 |
| new_switchyard | 11.7 (138 kV), 21.1 (345 kV) | 0 (58 of 158 above 0, up to 1.1 at 345 kV) | 28.7 |
| gen_tie | 9.1 | 0 (65 of 227 above 0) | 26.0 |

- The allowance of 16 TAC 25.195(f)(3) ($14 M up to 138 kV, $20 M above, 2024 dollars, agreements signed after 31 Dec 2025) covers every through-station bay with room to spare. It is
  exceeded in 123 substations: 65 gen-ties (a 138 kV tie costs more than $14 M beyond about 8.6 km) and 58 switchyards on lines above 138 kV ($21.1 M against $20 M).
  The allowance is applied to the sum of the facilities (bay or switchyard plus the tie): ASSUMPTION; if it applies only to TSP-built facilities the generator's share is higher for gen-ties.
- **Step-up transformer.** The battery's own step-up is the generator's cost (16 TAC 25.195(f)(1)) and is kept apart (`gsu_cost_musd`, $16.95 M for 100 MW): the MISO battery row
  gives $169.5k/MW to step up to 69 kV, applied at any voltage, an UNVERIFIED proxy. It dominates the all-in figure (about $194/kW all-in against $24/kW for the facilities). Do not add it to the battery capex twice.
- **Lead time** = FIS (median 20.5 months, quartiles 16 and 28; GIS report, storage with an agreement) + the longer of the class construction and the step-up delivery plus installation. A step-up of 110 MVA
  at 138 kV or more is a large power transformer: 30 months (NLR 2026 average; range 12 to 36) + 6 months to install, which governs everywhere. Result: 56.5 months mid (34 low, 70 high) for 4,104
  substations with a large transformer, 38.5 (31, 52) for the 929 below 138 kV. The queue's own median from FIS request to projected COD is 20.5 + 35.6 = 56.1 months, so the formula
  reproduces the observed schedule; that agreement is partly by construction (a 30-month transformer plus 6 months is close to 35.6) and says the transformer is the schedule.
- **Not modelled:** free bays, bus configuration, breaker duty and transfer-trip needs (PROMPT items 12 and 13; no public data), land and right of way, CCN for long ties, and CPI indexing of the allowance
  (MISO escalates about 4% a year; `costs.ESCALATION_PER_YEAR` is exported, not applied).

## 8. Queue and EIA storage mapped to substations (`data/queue_sub_map.csv`)

1,285 rows: 869 queue storage projects (157,257.5 MW) and 416 EIA-860/860M batteries (44,121.3 MW; 408 in ERCOT).

| source | method | rows | MW |
|---|---|---:|---:|
| queue | psse_bus (one existing station) | 367 | 65,442 |
| queue | psse_bus_tap (both ends of a tapped line resolve) | 346 | 65,060 |
| queue | psse_bus_tap_one_end | 82 | 12,973 |
| queue | name_county | 20 | 2,617 |
| queue | unmapped (new stations, buses not in the model) | 54 | 11,167 |
| eia | eia_plant_link | 314 | 35,954 |
| eia | nearest_sub | 81 | 5,436 |
| eia | unmapped (outside ERCOT or no usable coordinates) | 21 | 2,732 |

**MW mapped at confidence >= 0.5:** queue 141,358 MW of 157,258 (**89.9%**); EIA 38,098 MW of 44,121 (86.4%). At >= 0.7: queue 65,442 MW (41.6%, the single-station rows; a tap is 0.60), EIA 37,274 MW (84.5%).
By stage, queued MW mapped at >= 0.5: IA signed 34.4 of 38.1 GW, FIS started without an agreement 96.1 of 107.3 GW, FIS completed without an agreement 10.7 of 11.6 GW.

How it works:

- **psse_bus**: PSS/E bus numbers found in the POI text (not trusting the `poi_bus_no` column, which picks up voltages and circuit numbers) are joined to NP4-160 `PSSE_BUS_NUMBER` -> `SUBSTATION`; no number
  maps to two substations. Circuit numbers (`Ckt 27`, `Ckt-2`), voltages and list markers are dropped; a number below 100 counts only if the station's name appears in the text. A tap lists two stations: `sub` is
  the end in the project's county (else the higher-voltage end), `sub_alt` the other. Split MW between them if both matter. Confidence 0.95 (0.75 if the bus voltage differs from the POI voltage), minus 0.05 when the
  station's county differs, 0.60 for a tap with both ends, 0.50 with one end.
- **name_county**: the station name in the text matched to substation code, PSS/E bus-name stems, EIA/OSM/HIFLD names and OSM names (trigram shortlist, fuzzy score >= 0.82, margin >= 0.04 over the runner-up, a county
  bonus). **Validated on the 332 rows where the bus number gives the answer** (the name method sees only the words): when the station is in the model it hit 95% in the project's county (n = 213) and 95% when the
  candidate's county is unknown (n = 20); a candidate in another county is never accepted. With the true station removed from the index (a new station) it still accepted a wrong one
  50% of the time in county (n = 94), 6% county-unknown (n = 48), 14% other county (n = 184, not accepted by the rule). At an assumed 50% chance the named station exists the precision is about 0.65; we take **0.55**
  (in county) and 0.50 (county unknown). `queue_map.calibrate()` repeats the measurement.
- **eia_plant_link**: the EIA plant code is listed in `data/substations.csv` `eia_plant_codes` (agent 1c matched the substation's units to the plant through ERCOT's CDR unit names): 0.85 for a single link, 0.70
  when several substations list the plant (the one with the best placement is `sub`, the others `sub_alt`; the battery's own `_ESS` collector substation and the transmission station both appear).
- **nearest_sub**: otherwise the nearest placed substation of 69 kV or more (preferring one that carries the plant's grid voltage if at most 2 km further), 8 km maximum. On 241 plants whose linked substation
  was placed independently of the plant, the nearest substation was the linked one 73% of the time within 1 km when it was the only candidate within 50 m (n = 175), 85% at 1-3 km (n = 13), 15% at 3-8 km (n = 13),
  never beyond; when several substations sat within 50 m of each other it was 38% (n = 26). Confidence 0.70 / 0.60 / 0.15, and at most 0.35 when ambiguous.

**Double counting.** EIA-860M planned units are mostly the same projects as the GIS queue. `likely_duplicate_of` (and `overlap_conf` 0.8 name and MW agree, 0.5 only substation and MW agree) flags them:
of 154 planned EIA rows (25.7 GW), 62 rows (9.0 GW) are likely queue projects at 0.8 and 27 rows (4.9 GW) at 0.5. Operating EIA units are not in the queue (they leave the GIS report at commercial operation);
use `queue + EIA operating` for saturation and drop flagged planned rows. Self-limiting facilities (64 queue rows with 0 MW, a battery inside a solar or wind nameplate) have no published size.

## 9. Named constants and their labels

Unverified constants are single named values at the top of the module that uses them, each with a one-line label comment. Tables inside the modules (`GRID4H_PROVENANCE`, the label lists for the three CSVs) carry their own labels.

| module | constant | value | label | meaning / source |
|---|---|---|---|---|
| specs | `GRID4H_POWER_MW` | `1.0` | DERIVED | spec values are per MW of inverter power (as in arena SPECS) |
| specs | `GRID4H_ENERGY_MWH` | `4.0` | ASSUMPTION | the 4-hour product the prompt asks for (PROMPT.md section 3, Phase 3) |
| specs | `GRID4H_ENERGY_UNITS` | `int(round(GRID4H_ENERGY_MWH / C.U))` | DERIVED | 4 MWh / 0.125 MWh per unit = 32 |
| specs | `GRID4H_RESERVE_UNITS` | `0` | ASSUMPTION | no member backup reserve for a merchant battery (as the arena "grid" spec) |
| specs | `GRID4H_RTE` | `0.85` | ASSUMPTION | round-trip efficiency, the same 85% as the arena "grid" spec |
| specs | `GRID4H_CYCLES_PER_DAY` | `4.0` | ASSUMPTION | physical sanity limit only (as the arena specs); wear is what prices cycling |
| specs | `GRID4H_DEG_USD_MWH` | `20.0` | ASSUMPTION | wear cost, $ per MWh discharged (as the arena specs) |
| nodal_market | `K_ANALOG` | `24` | REAL | the arena's analog-day count (arena.backtest.K_ANALOG) |
| nodal_market | `ANALOG_WINDOW_DAYS` | `30` | REAL | arena.models.find_analogs default window (+-30 days of the same calendar date) |
| nodal_market | `WARM_DAYS` | `63` | REAL | arena.algorithms.History default buffer (63 days of price memory) |
| nodal_market | `SIM_START_DEFAULT` | `"2015-01-01"` | REAL | arena.backtest.SIM_START = 2015 |
| nodal_market | `ANALOG_REF_FILE` | `paths.DERIVED / "nodal_analog_ref_stats.npz"` | DERIVED | the arena's 8-zone standardisation, cached |
| runner | `DEFAULT_WORKERS` | `min(8, os.cpu_count() or 1)` | ASSUMPTION | the brief says 8 cores; the box has 8 logical (4 physical) |
| runner | `DEFAULT_CHUNK_LANES` | `16` | DERIVED | of 8, 16 and 32 lanes a chunk, 16 gave the best lane-days/s at 8 workers (notes/phase3a.md section 5) |
| runner | `MAX_CHUNK_LANES` | `64` | ASSUMPTION | keeps one worker under ~0.5 GB with the full 2011-2026 history |
| runner | `DEFAULT_ORACLE_CHUNK_LANES` | `2` | ASSUMPTION | oracle DP arrays are days x S x cap; keep them small |
| runner | `ORACLE_DAY_BLOCK` | `400` | ASSUMPTION | days per oracle DP block (memory); blocks are exact |
| runner | `EXPERT_NAMES` | `["Clockwork", "Forecaster", "Weather-regime", "Spike hunter q97", "...` | REAL | arena.algorithms.Adaptive.expert_names (index = learner pick) |
| runner | `C_NI` | `96` | REAL | arena intervals a day (arena.config.NI) |
| ancillary | `AS_PRICES_FILE` | `paths.DERIVED / "as_prices.parquet"` | REAL | ERCOT DAM/RT clearing prices for capacity (agent 1a) |
| ancillary | `MARKET_KEY` | `{"DAM": "DAM", "RT": "RT_15"}` | DERIVED | RT = the 15-minute settlement-interval prices |
| ancillary | `PRODUCTS` | `("REGUP", "REGDN", "RRS", "ECRS", "NSPIN")` | REAL | ERCOT AS products in the price file |
| ancillary | `UP_PRODUCTS` | `("REGUP", "RRS", "ECRS", "NSPIN")` | REAL | discharge-side products (need stored energy) |
| ancillary | `DOWN_PRODUCTS` | `("REGDN",)` | REAL | charge-side product (needs room to absorb energy) |
| ancillary | `RTCB_GO_LIVE` | `"2025-12-05"` | REAL | ERCOT news release 5 Dec 2025 (notes/rules.md (b)) |
| ancillary | `AS_DURATION_H_RTCB` | `{"REGUP": 0.5, "REGDN": 0.5, "RRS": 0.5, "ECRS": 1.0, "NSPIN": 4.0}` | REAL | Sustain durations in hours. REAL for Reg/RRS/ECRS from NPRR1282 (Board 24 Jun 2025, PUCT 31 Jul 2025): Reg and RRS 30 min (were 15), ECRS 1 h (was 2). Non-Spin "4 hours o |
| ancillary | `AS_DURATION_H_PRE` | `{"REGUP": 0.25, "REGDN": 0.25, "RRS": 0.25, "ECRS": 2.0, "NSPIN": 4.0}` | REAL | (NPRR1282 "was"); NSPIN UNVERIFIED |
| ancillary | `HOURS_PER_YEAR` | `8760.0` | DERIVED | 365 x 24, used to annualise partial years |
| ancillary | `DT_H` | `C.DT` | REAL | arena interval, 0.25 h |
| ancillary | `POWER_PER_UNIT_MW` | `C.U / C.DT` | DERIVED | one state unit per interval = 0.5 MW of a 1 MW battery |
| ancillary | `YEARS` | `{"DAM": range(2010, 2027), "RT": range(2025, 2027)}` | DERIVED | years with any price in as_prices.parquet (DAM from Dec 2010, RT from 5 Dec 2025) |
| costs | `USD_YEAR` | `2024` | REAL | price year of the MISO MTEP24 guide rows used (unit_costs.year_usd) |
| costs | `ESCALATION_PER_YEAR` | `0.04` | ASSUMPTION | MISO escalates about 4%/yr (unit_costs.labels.csv); NOT applied to the table, exported for 3b |
| costs | `REF_MW` | `100.0` | ASSUMPTION | reference battery size for the table, middle of PROMPT.md's 10-300 MW |
| costs | `MIN_KV_CANDIDATE` | `69.0` | REAL | PROMPT.md Tier 1 scope, 69-345 kV substations |
| costs | `MIN_CONF_PLACED` | `0.5` | REAL | notes/phase1-geo.md, "use match_conf >= 0.5 for anything that ranks sites" |
| costs | `MIN_LINE_DEGREE` | `2` | ASSUMPTION | a through bus (>= 2 line terminations) is taken to have room for a bay |
| costs | `TAP_MIN_KV` | `138.0` | ASSUMPTION | a new switchyard taps a line of at least 138 kV (69 kV lines are too weak for 100 MW) |
| costs | `TAP_MAX_KM` | `3.0` | ASSUMPTION | a tap is considered only if a suitable line passes within this distance |
| costs | `GEN_TIE_MAX_KM` | `50.0` | ASSUMPTION | longest gen-tie considered; beyond it the row is flagged long_tie |
| costs | `ROUTE_FACTOR` | `1.25` | ASSUMPTION | routed length / straight-line length of a new line |
| costs | `SITE_TIE_KM` | `0.5` | ASSUMPTION | tie from the battery site to a new bay or switchyard next to it |
| costs | `COLOCATED_KM` | `0.5` | ASSUMPTION | a suitable station this close is the same physical site under another ERCOT code (ERCOT splits some stations by owner / voltage) |
| costs | `KM_PER_MILE` | `1.609344` | REAL | unit conversion |
| costs | `EARTH_RADIUS_KM` | `6371.0088` | REAL | mean Earth radius |
| costs | `ALLOWANCE_LE_138_MUSD` | `14.0` | REAL | 16 TAC 25.195(f)(3), notes/rules.md (d) |
| costs | `ALLOWANCE_GT_138_MUSD` | `20.0` | REAL | same |
| costs | `ALLOWANCE_KV_SPLIT` | `138.0` | REAL | "138 kV or less" / "above 138 kV" |
| costs | `BAY_COST_KV` | `{69: 138, 115: 138, 138: 138, 161: 138, 230: 345, 345: 345, 500: 345}` | ASSUMPTION | which guide row prices a bay or substation at each kV |
| costs | `LINE_COST_KV` | `{69: 69, 115: 138, 138: 138, 161: 138, 230: 345, 345: 345, 500: 345}` | ASSUMPTION | which guide row prices a new line at each kV |
| costs | `FIS_MONTHS` | `(16.0, 20.5, 28.0)` | DERIVED | quartiles and median of FIS request -> IA signed, storage, GIS Sep 2026 (rules.md (d)) |
| costs | `IA_TO_COD_MEDIAN_MONTHS` | `35.6` | DERIVED | median IA signed -> projected COD, storage, GIS Sep 2026 (rules.md (d)); a cross-check only |
| costs | `BUILD_MONTHS_BAY` | `(12.0, 18.0, 24.0)` | ASSUMPTION | engineering, breaker delivery and construction of one bay |
| costs | `BUILD_MONTHS_SWITCHYARD` | `(18.0, 24.0, 30.0)` | ASSUMPTION | a new 4-position switchyard |
| costs | `GEN_TIE_MONTHS_BASE` | `9.0` | ASSUMPTION | design and right-of-way for a gen-tie, plus the bay at its end |
| costs | `GEN_TIE_MONTHS_PER_KM` | `0.3` | ASSUMPTION | construction time grows with length |
| costs | `XFMR_INSTALL_MONTHS` | `6.0` | ASSUMPTION | set, test and energise the step-up after delivery |
| costs | `MPT_LEAD_MONTHS` | `(9.0, 12.0, 18.0)` | ASSUMPTION | delivery of a medium-size step-up transformer (below LPT_MIN_MVA) |
| costs | `LPT_MIN_MVA` | `100.0` | ASSUMPTION | a step-up of at least 100 MVA at 138 kV or more is a large power transformer |
| costs | `GSU_MVA_PER_MW` | `1.1` | ASSUMPTION | step-up rating = 110% of the battery's MW |
| queue_map | `REF_KV` | `{69, 115, 138, 161, 230, 345, 500, 765}` | REAL | nominal transmission voltages; a bare number equal to one is a voltage |
| queue_map | `KV_TOL` | `1.0` | ASSUMPTION | kV difference tolerated between the text and the bus voltage |
| queue_map | `MIN_NAME_CHARS` | `4` | ASSUMPTION | shorter name phrases are not matched (too ambiguous) |
| queue_map | `SHORT_NUMBER_MAX` | `99` | ASSUMPTION | a bus number this small is accepted only if its station's name appears in the text |
| queue_map | `SHORT_NUMBER_NAME_SCORE` | `0.8` | ASSUMPTION | fuzzy score the name evidence must reach for a short number |
| queue_map | `NAME_ACCEPT_SCORE` | `0.82` | ASSUMPTION | minimum fuzzy score to accept a name match (checked by validate_name_match) |
| queue_map | `NAME_AMBIGUITY_MARGIN` | `0.04` | ASSUMPTION | the best candidate must beat the second by this much |
| queue_map | `OVERLAP_NAME_SCORE` | `0.75` | ASSUMPTION | fuzzy score of the project names (generic words removed) to call two records the same project |
| queue_map | `OVERLAP_MW_TOL` | `0.25` | ASSUMPTION | relative MW difference tolerated when the names agree |
| queue_map | `OVERLAP_MW_TOL_TIGHT` | `0.10` | ASSUMPTION | relative MW difference tolerated when only the substation agrees |
| queue_map | `COUNTY_NAME_BONUS` | `0.05` | ASSUMPTION | score bonus for a candidate in the queue county, so a county match breaks an exact name tie |
| queue_map | `EIA_MAX_DIST_KM` | `8.0` | DERIVED | beyond this the nearest substation was never the linked one (validate_nearest) |
| queue_map | `EIA_MIN_KV` | `69.0` | ASSUMPTION | a transmission-connected battery connects at 69 kV or more |
| queue_map | `EIA_KV_PREFER_EXTRA_KM` | `2.0` | ASSUMPTION | prefer a substation carrying the plant's grid voltage if at most this much further |
| queue_map | `EIA_TIE_KM` | `0.05` | ASSUMPTION | substations within 50 m of the nearest one are indistinguishable by distance |
| queue_map | `CONF_BUS_SINGLE` | `0.95` | ASSUMPTION | one bus number resolves to a station and its voltage matches the text |
| queue_map | `CONF_BUS_KV_MISMATCH` | `0.75` | ASSUMPTION | number resolves, but the bus voltage differs from the POI voltage in the text |
| queue_map | `CONF_TAP_BOTH_ENDS` | `0.60` | ASSUMPTION | both ends of a tapped line resolve; the new switchyard sits between them |
| queue_map | `CONF_TAP_ONE_END` | `0.50` | ASSUMPTION | one end resolves; the other end is a bus that is not in the model yet |
| queue_map | `COUNTY_MISMATCH_PENALTY` | `0.05` | ASSUMPTION | the queue county is where the project sits, the POI can lie across the line |
| queue_map | `CONF_EIA_LINK_SINGLE` | `0.85` | ASSUMPTION | plant code listed by exactly one substation (CDR unit-name match) |
| queue_map | `CONF_EIA_LINK_MULTI` | `0.70` | ASSUMPTION | listed by several substations; the top-ranked one is taken |
| queue_map | `CALIBRATION` | `{ "name_conf_county_ok": 0.55, "name_conf_county_unknown": 0.50, "n...` | DERIVED | CALIBRATION, DERIVED on 10 Oct 2026 by calibrate() (run it to see the measurements behind each number): name_county: with the station present in the model, an accepted na |

## 10. Hazards and caveats for agent 3b

1. **Substation code `NA`**: read CSVs with `keep_default_na=False, na_values=[""]` (see 3.5). `costs.py` and `queue_map.py` do.
2. **The lane-safe context is not the arena's pooled context.** Equal to it for the zone lanes to within $0.05/kW-yr in 2024; use `context="arena"` only to reproduce the arena exactly for its 8 zones.
3. **Histories.** Nodal price history is one week real-time and 31 days day-ahead (gate A); multi-year node lanes will be SIM or DERIVED. Walk-forward models and analogs assume a long daily history of the lane itself.
4. **AS numbers are bounds or estimates**, not forecasts (section 6), and system-wide.
5. **Connection class is a screening rule.** 43% of rows carry the default class (class_conf 0.3). The step-up cost is an UNVERIFIED proxy. The allowance is applied to the facilities sum (ASSUMPTION).
6. **Queue mapping** cannot tell which breaker position a project uses; 10% of queue MW (new stations, buses not yet in the model) is unmapped; taps are placed at one end with the other in `sub_alt`.
7. **Memory and cores.** The machine has 4 physical cores (8 threads) and 9.9 GB of RAM, shared with other agents. A worker with a chunk of 16 lanes over the full 2011-2026 history peaks at about 235 MB.
   Eight workers do not give eight times the speed; see section 5.
8. **Group caps are cumulative.** A cap per year would need a different rule; ask before changing `siting.py` again.
9. **`tests/test_arena_compat.py` guards the arena as 3a left it**: it fails if any arena file other than `siting.py` differs from the backup. If a later phase edits another arena
   file on purpose, update that test in the same change.
10. **Not covered.** `arena.forward` (the 2027-2031 scenario engine) keys on the arena's own zone names and does not run on a `NodalMarket`; only the walk-forward backtest tools do.
    `spec="home"` also runs through the runner (smoke-tested for 15 days on 2 lanes) but is not part of the suite.

## 11. How to run

```
run_grid.py compat-test [year=2024 | days=30] [skip-siting] [skip-nodal]
run_grid.py as-layer [skip-coopt]          # data/as_revenue.csv (+ labels) and the tables above
run_grid.py costs                          # data/connection_costs.csv (+ labels)
run_grid.py queue-map [calibrate]          # data/queue_sub_map.csv (+ labels)
run_grid.py runner-bench lanes=128 days=60 [workers=8] [chunk=16] [spec=grid|grid4h] [matrix]
python tests/test_arena_compat.py          # and tests/test_3a_{specs,nodal,runner,ancillary,costs,queue_map}.py; a name substring selects tests
```
