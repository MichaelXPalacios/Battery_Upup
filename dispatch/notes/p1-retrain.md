# P1: the served model retrained on D0's builder, and D1a's interim builder retired

Run date: 11 Oct 2026. Agent P1. Scope: `models/`, `registry/` (use only), `features/compat.py`, the model-related tests, `README.md`. Nothing is committed to git. `live` was not touched and no `gate3.pass` tag was written.

## Result

* **The served model is registry version 6**, feature set `rt_v1/1.1.0`, four hubs, 60 input columns under D0's own names (`rt_mean_1h`, `hour_local`, `qtr_hour`, `dam_as_regup`, ...). It was trained on `features.build.training_frame` and is fed `features.build.point_in_time` frames from the online store with no rename, so it sits under D0's leakage tests (`tests/test_leakage.py`).
* Aliases: **`research` = 6, `paper` = 6** (promoted 2026-10-11 17:22Z by `p1`, previous 1). `live` is not set. Version 1 is still registered for rollback (section 6). Versions 7 to 10 are the four baselines re-registered by `release` on the D0 frames (`kind=baseline`; duplicates of versions 2 to 5, whose holdout forecasts are identical to the last bit: the baselines read `rt_lag_*` and `rt_prof*`, which are the same columns in both spellings).
* `check 6 paper`: 45 of 45 pass (every hub beats every baseline on pinball and P50 MAE, gains of 31% to 40% over the best seasonal-naive, coverage 0.775 to 0.800 inside [0.70, 0.90]). Run before the research promotion it showed 44 passes and the one stage-order rule ("must be promoted to research before paper"), as designed.
* The holdout is the same one as version 1's: decisions every 4 hours at 02:00, 06:00, ... UTC from 1 Jan 2025 to 26 Sep 2026 02:00 (3,799 decisions, 364,704 forecast rows per hub), the same realised prices (label difference 0.0), the same four baselines (their P50 forecasts are identical to the last bit).

| hub | metric | version 1 (interim frames) | version 1 on D0's frames | **version 6** (D0 frames) | 6 against 1 |
|---|---|---:|---:|---:|---:|
| HB_NORTH | pinball | 4.8822 | 4.8822 | **4.8568** | -0.52% |
| | P10-P90 coverage | 0.7958 | 0.7957 | 0.8003 | +0.57% |
| | P50 MAE | 13.534 | 13.535 | 13.492 | -0.31% |
| HB_WEST | pinball | 5.6671 | 5.6669 | **5.6690** | +0.03% |
| | P10-P90 coverage | 0.7963 | 0.7963 | 0.7898 | -0.82% |
| | P50 MAE | 16.228 | 16.228 | 16.247 | +0.12% |
| HB_HOUSTON | pinball | 4.1965 | 4.1965 | **4.1841** | -0.30% |
| | P10-P90 coverage | 0.7770 | 0.7770 | 0.7872 | +1.31% |
| | P50 MAE | 11.932 | 11.932 | 11.821 | -0.93% |
| HB_SOUTH | pinball | 4.2595 | 4.2595 | **4.2861** | +0.62% |
| | P10-P90 coverage | 0.7752 | 0.7753 | 0.7754 | +0.02% |
| | P50 MAE | 11.981 | 11.982 | 12.099 | +0.98% |

Pinball is the mean of the P10, P50 and P90 losses in $/MWh; coverage is the share of realised prices between P10 and P90 (nominal 0.80). Capture rate of the holdout plan (one reference battery, no confidence interval): 0.356 / 0.432 / 0.365 / 0.389 for version 1 and 0.360 / 0.426 / 0.363 / 0.363 for version 6 (HB_NORTH / WEST / HOUSTON / SOUTH); the profile baselines are 0.35 to 0.39. On the Winter Storm Fern window (23 Jan to 3 Feb 2026) version 6 is better at HB_NORTH (pinball 37.00 against 38.31) and HB_HOUSTON (24.28 against 25.95) and worse at HB_SOUTH (24.90 against 23.98) and HB_WEST (35.01 against 34.47). It is a retrain with the same recipe, not an improvement, and not a regression: the differences are +-1% and have no sign.

## 1. The retrain

`python -m dispatch.models.train all --source d0 --tag r2 --threads 1 --holdout-end 2026-09-26T05:00:00Z --promote "" --timeout 3000`, one process, four hubs one after the other, LightGBM single-threaded, 11:08 to 12:20 CDT (72 minutes while the machine was shared; 845 to 1,303 s a hub). Checkpoints: `research-data/dispatch/mlflow/work/r2/<hub>/`, log `work/r2_train.log`. The MLflow runs are `train_<hub>` (with the 20 Optuna trials as child runs), `release_20261011_1720`, version 6.

Three arguments differ from the brief's command, none changes what is trained:

* `--holdout-end 2026-09-26T05:00:00Z` pins the holdout to version 1's. The old default ("one day before the price history ends") was computed from the csv archive, which ends 27 Sep 05:00 UTC; D0's lake has prices up to the present, so the default would have run the holdout to 10 Oct and made the two tables incomparable.
* `--promote ""` registers without moving an alias (the default moved `research` straight after `release`, before `check 6 paper` could be looked at).
* `--timeout 3000` instead of 1200 s for Optuna, so a slow shared machine cannot cut the search short (all 20 trials finished within 650 s, so it never bound).

The CLI needed these changes, all in `models/train.py` (and one line in `models/quantile.py`):

* the model's inputs are a constant list in D0's names (`RT_FEATURES` + `AS_FEATURES` = `MODEL_FEATURES`, 60 columns) instead of the interim list renamed through `D0_NAME`; `CORE_NONNULL` uses `rt_mean_24h` for the interim `rt_mean_96`;
* `build_frame` passes the decision-grid `offset` to D0's `training_frame` (it supports one; the holdout grid is the 2-hour offset one, as before);
* the holdout plan's price series (`realised_prices`) and the day-ahead AS history of the AS view (`day_ahead_as_frame`) come from D0's lake, and `run_sp` raises if the price series differs from the holdout frame's labels;
* `--source` accepts only `d0`; `TrainConfig(source="interim")` raises;
* `quantile.CALENDAR_COLUMNS` gained `doy_sin` and `doy_cos`, so the drift reference leaves the calendar out as it did under the interim names (`tgt_*`): the reference has 51 market-state columns, no calendar column.

## 2. Why the numbers are not identical

The expectation was "identical or near-identical". The result is near-identical, and the remaining differences have one cause that I can show.

**The builder is not the cause.** Version 1 scored on D0's holdout frames (renamed by `compat`, the same decisions) gives the same pinball, coverage and MAE as on the interim frames to four decimals (table, third column). Its forecasts are identical to 1e-4 on all but 975 to 994 rows per hub (0.3%, 12 decisions), described below.

**The retrain differs from version 1 in one respect: the hour of day.** The interim model had `tgt_hod`, one number (hour + quarter/4); rt_v1 carries `hour_local` and `qtr_hour`, two integers, so version 6 has 60 inputs and version 1 had 59. The effect, measured:

* *Tuning and cross-validation are bit-identical to version 1*, at all four hubs: the 20 Optuna trials have the same values, the best trial and the chosen parameters are the same, and the purged walk-forward CV pooled pinball is equal to the last digit (9.424667, 10.501062, 10.034192, 9.039684 at NORTH, WEST, HOUSTON, SOUTH). Reason: the tuning and CV frames keep every 8th or 4th horizon, a spacing of 2 hours or 1 hour, so `qtr_hour` is constant there, LightGBM drops a constant column, and `hour_local` is exactly `floor(tgt_hod)`.
* *The final fit differs.* It keeps every 3rd horizon (45-minute spacing), where `qtr_hour` takes all four values; with the same parameters, the same rows and the same seed, the 60-column and the 59-column fits are different models (on a 3-month sample the two fits differ by up to $43.67/MWh in-sample at the extremes; the held-out pooled differences are the +-1% above). The calibration offsets differ for the same reason (HB_NORTH P90 +0.141 against +0.154). `qtr_hour` carries less than 0.1% of the gain in the fitted models and `hour_local` 1.1% to 2.2%, where `tgt_hod` had 1.8% to 2.3%: the information is the same, the trees are not.
* The holdout decisions, the realised prices, the baselines, the conformal slice (the last 120 days, 11,328 rows) and the Optuna seed are the same.

I did not try to make version 6 reproduce version 1 bit for bit: that needs a `tgt_hod` column in rt_v1 (a feature-set version bump, D0's file) and would only reproduce a model whose point is that it can be trained and served from D0's builder.

**Twelve decisions differ for a reason that favours D0.** For each hub, 975 to 994 holdout rows (12 decisions: 9, 10, 12, 13, 16, 17, 18, 19, 21, 22, 24 and 25 Sep 2026, each at 18:00 UTC = 13:00 CDT) have version-1 forecasts that differ by more than 1e-3 between the two builders (largest difference $16 to $47/MWh in the P10). On those days ERCOT posted the day-ahead results before 13:00 CDT (D0 uses the real posting time where the lake has it, 12:32 to 13:55 CPT); the interim builder assumed 13:30 and hid tomorrow's ancillary prices that were already public. D0 is right, the interim builder was conservative. The new model's holdout includes the same rows with D0's visibility, which is how it is served. This is the "13:30 against posting time" difference recorded in `notes/d0.md` section 15; it was expected to be absent from the training grid and it is present in the last month of the holdout because the holdout grid has a decision at 13:00 CDT in summer.

**The ECRS price view differs, not the RT model.** The AS view's ECRS P50 uses a 14-date median of the day-ahead price when tomorrow's price is not yet public; rt_v1 has no `dam_as_ecrs_med14` (ECRS exists since June 2023; `sets.AS_REL_PRODUCTS` excludes it), so the AS view falls back to its stored table. Before June 2023 the interim median was a defect (its matrix index clipped to the first ECRS date, so a 2019 decision got the median of June 2023: $59/MWh-h). It does not move any RT quantile and the other four products are equal to 1e-4.

## 3. Promotion and what the monitor reads

`registry promote 6 research --by p1`, then `check 6 paper` (45 of 45), then `promote 6 paper --by p1`. `status` afterwards: `paper` and `research` hold version 6, `live` holds nothing, `gate3_pass` is empty. Version 6's tags: `feature_set=rt_v1/1.1.0`, `data.kind=lake_catalog` (`data.sha256` 57d642da...), `bundle.sha256` d9d09a03..., `code.sha256` 20c9a4e5..., `code.git_dirty=true`.

For the monitors, as D1a did: the version tag **`holdout.metrics`** carries the per-hub reference (pooled and by horizon bucket, window 2025-01-01 to 2026-09-27, 364,704 rows, decisions every 4 h, horizons 1 to 96); `Registry.holdout_metrics("6")` and `load("paper").holdout_metrics` read it (HB_NORTH pooled pinball 4.8568, equal to the run's). The bundle's **`reference_frame`** is 12,000 rows (3,000 per hub, from the last training year) by 51 market-state columns in D0's names; `reference_for(hub)` gives each hub's own sample. The AS view is fitted to the last 14 days of day-ahead prices in the lake (as of 2026-10-11 17:00Z). The calibration monitor starts afresh with the new version, as `notes/integration.md` says: it scores only the loaded version and needs 3.5 days of settled forecasts before its first verdict; the false-breach test on the new holdout table (`test_calibration.py::test_the_false_breach_rate_on_the_holdout_forecasts_is_under_5_percent`, which reads the registry's `research` version) passes.

**What `code.sha256` identifies.** It is the hash of `dispatch/dispatch/**/*.py` when the run was logged. The training path was frozen at launch (sha256 at 16:08Z: `models/train.py` 1b9bb722..., `models/quantile.py` 6c4f534a..., `models/cv.py` 01ce0bea..., `features/build.py` d7dd652f..., `features/sets.py` ed5c5be6..., `features/windows.py` 7c660127...; `models/baselines.py` has since changed in a docstring only), and the retirement edits that followed (deleting `interim_features.py`, `synthetic.py`, `replay.py`, `compat.py` docstrings) are not on that path. The hash is therefore the final tree's, not the training tree's, and the table above is the statement of what trained the model.

## 4. Verification

**The full suite, twice** (`.venv/Scripts/python.exe -m pytest -q tests`, after the promotion, so the registry tests read the real store with version 6 as `research`): **669 passed, 1 skipped** (the opt-in Gate 1 run on the real lake) in 13 min 17 s, and again 669 passed, 1 skipped in 10 min 52 s. 670 tests are collected: 621 before, minus the interim builder's tests, plus the converted and new ones (`test_models.py` 39, `test_rt_v1_1_equivalence.py` 15), plus 53 of P2's (`test_weather_gas_fetch.py`). One transient failure is worth knowing: in an earlier, partial run made while the retrain and other agents' jobs loaded the machine, `test_guard_props.py::test_generator_reaches_both_outcomes` (a Hypothesis statistic that needs 200 acceptances) drew 193. It passes alone, in its own file, and in both full runs; nothing of mine touches the guard, and I1 had already met this test being load-sensitive (`notes/integration.md` section 2.6). I did not investigate further.

**Live, on the real stack** (`ops/live_e2e.py --phase live`: the platform's own Redis on 127.0.0.1:6390 started for the check and shut down after it; `:6379` not touched; uvicorn on 127.0.0.1:8765 with alias `paper`, started and stopped by the script, port closed afterwards):

* First attempt, 17:38Z: `prepare_signal` ran (ERCOT's public MIS, 11.7 s), then `POST /signal` for `PILOT_HB_NORTH` returned `error / forecast_failed` with `MissingFeaturesError: required features null: ['rt_mean_24h']`, and nothing was dispatched. The lake's real-time prices ended at 11 Oct 00:15Z, so the last 24 hours had a 14-hour hole (`prepare_signal` fetches only the last 3 hours), and `rt_mean_24h` needs at least half of its 96 slots. This is the fail-closed rule doing its job, and it is not specific to the new model: version 1 requires the same column under its interim name (`rt_mean_96`). Cure: `python -m dispatch.data.flows backfill --reports NP6-905-CD,NP4-188-CD,NP4-190-CD` (48 s; afterwards 0 missing slots of 96 in the last 24 hours). **Operational note: a lake that has been idle for more than three hours needs a `backfill` before the first signal, or the service will not dispatch.**
* Second attempt, 17:41Z: `prepare_signal` 4.8 s (frame of 96 rows, newest price published 17:32:02Z), `POST /signal` **`dispatched`**, `model_version` **6**, `/health` `model_version: "6"`, `model_alias: "paper"`, all ten wiring entries ok, guard accepted, setpoint net -10.0 MW with 10 MW of REGUP for the interval starting 17:30Z, `feature_age_s` 563.6 (D0's publish time of the newest price, 17:32:02Z, to the decision), cycle latency 0.214 s, forecast-log errors 0, Prometheus `dispatch_model_info{alias="paper",version="6"} 1`. No process or server is left running.
* Side effects in the real runtime, as the script documents: two cycles in the real audit log (the error and the dispatch), one entry in the real paper ledger (the -10 MW setpoint), one forecast-log line set. The audit chain verifies (`audit_ok: true` in `/health`).

## 5. What was retired, and where its coverage went

Deleted: `dispatch/models/interim_features.py` (768 lines; git commit 8ce9649, sha256 5e120505...c10c2). Its role had three parts, each with a replacement:

| Interim builder | Replaced by |
|---|---|
| point-in-time features and training frames over the NP6-785-ER csv (`point_in_time`, `training_frame`, `build_features`) | D0's `features.build` (`rt_v1` 1.1.0), which is what `train.py` now calls and the service serves |
| the price history (`PriceHistory`, `load_history`) the holdout plan was settled at, and where the history ends | `train.realised_prices`: D0's lake through `data.reader.read_prices`, the same labels `training_frame` uses (latest published wins). `run_sp` checks that it equals the holdout frame's labels, or raises |
| the day-ahead AS history the AS view (`ASPriceModel`) is fitted to (`load_as_history`) | `train.day_ahead_as_frame`: D0's lake (`dam_as`, `dam_as_hist`) |
| `data_snapshot` (the hash of the csv files) | `registry.catalog_snapshot()` (the hash of the lake catalog), which the D0 path used already |
| a synthetic price history for the tests (`models/synthetic.py`) | `SyntheticLake`: synthetic prices written into a throw-away parsed lake with the real schemas and D0's publish times, and D0's builder over it |
| `reference_row`, `reference_as_row` (the executable specification of notes/d1a.md section 10) | D0's `test_the_41_new_columns_match_an_independent_oracle` (written independently of the interim code) and `test_d1a_worked_example_of_section_10_3_is_exact`; the functions themselves are in git (commit 8ce9649) |

Also removed from `train.py`: `--source auto|interim`, `choose_source`, `D0_NAME`, `D0_EXPAND`, `d0_feature_names`, `history_provider`. `--source d0` stays (it is the only value, so every documented command still works); `TrainConfig(source="interim")` raises. `compat.INTERIM_TO_D0` is now the one copy of the name map.

### The golden fixture

Before anything was deleted, `tests/fixtures/make_interim_golden.py` ran the interim builder on the real data and froze, for four hubs and five decisions each (2019-03-10 spring forward, 2025-11-02 fall back, 2026-01-26 Winter Storm Fern, 2026-07-03 observed holiday, 2026-09-24 the newest data):

* `interim_golden_features.parquet` (171 KB): 1,920 rows (480 per hub), 71 columns: the 59 inputs of version 1 in the interim names, the day-ahead ECRS price, and what version 1 forecast from them.
* `interim_golden_inputs.parquet` (201 KB): the raw prices those frames came from, 51,232 values (15-minute RT prices of each hub from 16 days before each decision to 26 hours after it; hourly day-ahead AS prices from 30 days before to 2 days after), so the test can rebuild a lake without the real one.
* `interim_golden.sha256`: `22343f17...445d` (features), `f13fbd98...fb4` (inputs). A test checks it.

`tests/test_rt_v1_1_equivalence.py` (8 tests before, 15 now) holds D0's builder to the fixture: `point_in_time` and `training_frame` over a lake rebuilt from the frozen inputs (4 hubs x 5 decisions x 96 horizons x 60 columns: largest absolute difference 0.0, no NaN mismatch, on the rebuilt lake and on the real one), the same with a lake cut at the decision time (prices published after the decision are in the inputs; no column moves), version 1 through `compat` against its frozen forecasts, and the real lake against the fixture. What the old file did that this one does not: the opt-in 1.6-million-rows-per-hub comparison over 2019 to 2026 (its result, 0.0, is recorded in notes/d0.md section 15, and the holdout comparison above repeats it for 2025 to 2026 at 364,704 rows per hub).

### Tests retired because the code they tested is gone

All were in `tests/test_models.py` and tested the interim builder or its loader. Each row says where the behaviour is covered now.

| Retired test | Covered by |
|---|---|
| `test_csv_to_utc_spring_forward_skips_hour_three`, `..._fall_back_repeats_hour_two` (the csv loader) | `test_rebuild.py::test_fall_back_day_has_25_hours_and_spring_forward_has_23`, `::test_yearly_workbook_dataset_..._fall_back_day`, `test_lake.py` (spring forward); the fixture's 2019-03-10 and 2025-11-02 decisions |
| `test_publish_lag_blocks_and_boundaries`, `test_features_never_read_data_published_after_the_decision` | `test_leakage.py` (edge decisions, canaries in every input, `test_rows_published_after_the_decision_change_no_column`, `test_cutting_the_lake_at_the_decision_time_changes_no_column`) and the new `test_nothing_published_after_the_decision_reaches_a_feature` on real prices |
| `test_training_frame_is_point_in_time_and_labels_are_realised_prices` | `test_leakage.py::test_training_frame_rows_never_postdate_their_decision`, `::test_price_source_uses_the_label_the_training_frame_uses`; new `test_synthetic_lake_gives_point_in_time_frames_and_realised_labels`, `test_realised_prices_are_the_labels_the_frames_carry_...` |
| `test_lag_features_follow_publication`, `test_stale_history_fails_closed` | `test_builder_matches_the_brute_force_reference`, the 41-column oracle, `test_nothing_known_yet_is_an_error`; the service's age gate (`test_api.py`) |
| `test_day_ahead_as_features_follow_the_1330_publication_rule` | ported: `test_day_ahead_as_features_follow_the_1400_publication_rule` (same planted numbers, D0's rule) |
| `test_history_cache_...`, `test_decision_grid_and_calendar_features` | `test_leakage.py::test_decision_times_are_deterministic_and_anchored_to_the_epoch`, `::test_decision_grid_offset_...`, `::test_us_federal_holidays_are_pandas_observed_days` |
| `test_rt_v2_worked_example_in_the_notes_is_exact`, `test_reference_row_is_the_oracle_...`, `test_reference_as_row_is_the_oracle_...` | `test_leakage.py::test_d1a_worked_example_of_section_10_3_is_exact`, `::test_the_41_new_columns_match_an_independent_oracle`, `::test_a_late_day_ahead_date_hides_the_newer_dates_until_it_is_published` |
| `test_choose_source_...`, `test_d0_feature_names_map_the_model_onto_rt_v1_plus_rt_v2` | `test_check_columns_refuses_a_frame_that_lacks_a_column_the_model_needs`, `test_model_features_are_the_interim_ones_renamed` |

Converted rather than retired: `test_arena_profile_feature_matches_arena_history_profile` (D0's `rt_prof14_dt` against the arena's own `History.profile`), the whole training-pipeline tests (`_synthetic_cfg`, now `frames_provider`/`prices_provider` hooks on a `SyntheticLake`), the replay test, `test_registry.py`, `test_integration_cycle.py` and `test_api.py` (their tiny model is trained on rt_v1 columns and served from D0 frames with no rename, which is how production now runs). One expectation moved with the data: the cycle's `feature_age_s` is now measured from D0's publish time of the newest price (B-30 min + 25 min = B - 5 min), five minutes more than the interim assumption gave.

Small code changes the retirement needed: `quantile.CALENDAR_COLUMNS` gained `doy_sin` and `doy_cos` (D1a's `tgt_doy_*` were excluded from the drift reference by the `tgt_` prefix; under D0's names they would have been compared as market state); `replay.default_features_fn` builds D0's frame for every model and renames it only for an interim-trained one; `report.importance_table` reads both spellings.

## 6. Rollback

`python -m dispatch.registry.mlflow_registry promote 1 paper --by <you>` (and `research`) puts version 1 back. The service renames D0's columns for it through `features/compat.py` (`for_forecaster` keys on the bundle's `feature_set` starting with `interim`), which `test_version_1_still_forecasts_the_frozen_numbers` and the earlier integration tests keep honest. Restart the service after moving an alias (it loads the model at start). When version 1 stops being a rollback target, delete `features/compat.py`, the `adapt_features` wiring and the compat tests.

## 7. Caveats and open items

* **Not bit-for-bit version 1.** Version 6 is a retrain of the same recipe on the same rows: tuning and CV reproduce version 1 exactly, the final fit does not (section 2). The metric differences are +-1% and unsigned; nobody should read an improvement or a regression into them. Capture rate differs more at HB_SOUTH (0.389 to 0.363) but it is one plan of one reference battery without a confidence interval.
* **Twelve holdout decisions** (section 2) were scored with different information than version 1 saw (D0's earlier day-ahead visibility). That is D0 being right, but it means the two holdout tables are the same rows and not quite the same inputs on 0.3% of them.
* **The AS view's ECRS P50** has no 14-date median fallback under rt_v1 (it uses the stored table instead). Not an RT-model input; a D0 feature request (`dam_as_ecrs_med14`) if it matters.
* **`code.sha256` of version 6** is the final tree, not the training tree; the training path's hashes are in section 3.
* **What the new tests do not keep.** The 1.6-million-rows-per-hub comparison over 2019 to 2026 (opt-in, 3 minutes a hub) is gone with the interim builder; its result is in `notes/d0.md` section 15, and the fixture (1,920 rows, five hard days) plus the version-1 holdout comparison above (364,704 rows per hub) are what remains executable. The two oracle functions `reference_row` and `reference_as_row` are in git (commit 8ce9649) only, not in the tree; D0's independent oracle replaces them.
* **Historical text left as it was.** `notes/d0.md` section 15 (one pointer line added) and `notes/integration.md` section 5 describe commands that no longer exist (`tests/test_rt_v1_1_equivalence.py compare ...`, `DISPATCH_EQUIV_FULL=1`) and `notes/d1a.md` sections 3, 9, 10.4 and 10.5 describe the deleted module (it has a banner pointing here). D0-owned comments in `features/build.py`, `sets.py` and `windows.py` say that a convention is "as the interim builder does"; that is history, still true, and I did not touch D0's files. DESIGN.md never mentioned the interim builder or version 1 as served, so it is unchanged. In the real MLflow home, `mlflow/interim_cache/` and `mlflow/inputs/as_prices_*.parquet` (the inputs version 1 was trained on, hashed in its data snapshot) stay; nothing reads them now.
* **Registry clutter.** `release` registers the four baselines again for every release (versions 7 to 10, duplicates of 2 to 5 on the new frames). Harmless; a future release could skip them when the baseline bands have not changed.
* **Operational.** After a lake idle for more than three hours, run `flows backfill` before the first signal (section 4). After an alias move, restart the service so it loads the new bundle (`ops/README.md`).
* **Files not mine.** `dispatch/data/{assumptions,catalog,flows,parse,reports,weather}.py`, `monitoring/checks.py` and `tests/test_weather_gas_fetch.py`, `tests/fixtures/weather_gas/` are P2's; the suite runs above include them as they were at the time. `SyntheticLake` reads `data/parse.DATASETS` and `data/assumptions`, so a change to those schemas would show up first in `tests/test_models.py` and `tests/test_rt_v1_1_equivalence.py`.
