# Submission sprint contract (26 Sep 2026, integration lead: Michael)

One branch, `submission`, becomes what we submit. The **root app** is the app: root `sim/` is the one engine,
root `ui/` becomes Connor's four story pages. Every number on every page comes from a data file the engine wrote,
with its label (REAL / SIM / DERIVED / ASSUMPTION). No scripted series, no "nearest run" fallback, no live server.

Visual authority: `previous-work/docs-history/design-handoff/story-flow/README.md` (v2: Configure → Running → Run → Results → Learnings)
and `previous-work/docs-history/design-handoff/README.md` (tokens). Its `.dc.html` files and `ui/story.js` are **references**: rebuild,
don't copy their scripted parts (`SYS`, `buses()`/`busV()`, `fitModel()`, `resolve()` nearest-run, fake progress).

## Rulings (final)

1. **Page 1 levers.** Evening (2026-08-23 default, 2026-07-22, 2026-08-14, 2026-08-26) × policy (none, naive,
   aware) × failure (none | `faults` = silent battery + EV + controller stall | `worker_kill` | `covert`).
   Failures exist on 23 Aug only (policy aware). Fleet levers, each a real engine run on 23 Aug, **one lever away from
   the default at a time**, policies naive and aware (none is shared):
   fleet size {48, **96**, 144, 192} · class {**core**, legacy} · reserve {**20**, 30, 40, 50}% (never below 20) ·
   start charge {60, 75, **90**, 100}% · home-load growth {**0**, 20, 50}%.
   Anything not run is disabled with its reason. Presets: "The demo evening", "Record demand", "Priciest evening",
   "Pieces fail", "Controller crash", "Hidden attacker" (all catalogue ids).
2. **Running screen** is honest: it shows the files loading and the engine's measured cost for that run
   (`engine.json`, the run's `buildSeconds`/solves). No fake progress.
3. **Results:** drop the five ERCOT cards. Voltage by bus and reactive power come from engine exports (below).
   Only the `covert` scenario shows ERCOT context: the REAL 25 Sep 2026 frequency (`ui/data/ems/freq-series.json`),
   dated "a different day", beside the 3–17 mHz band for a 1,000-battery hijack. Never "3–5 mHz", never one value.
4. **Learnings:** Q1 = `p2/index.json usefulCapacity`: aware 1,007 vs **naiveOpenDSS 100** (not 383). Q2 and Q3 =
   `p2/planner.json` (RZ's binding three-layer scope, `previous-work/simulators/rz/research/capacity-planner/`). Q4 = P2 ranking.
5. **Look:** tier 0 is sage, never bright green (use root `ui/lib/scene-model.js` round-2 `TIER_RGB`). Speeds
   0.1/0.25/0.5/1/2/4×, default 0.25× (0.25× = 2.5 simulated minutes per second, as root `ui/panels/p1.js`).
6. **Words:** "Naive: our assumption of one number, no feeder check" (never "how Base charges today");
   "Oncor-suburb stand-in on NREL's synthetic feeder"; "Gross energy value, not Base's profit"; "Fictional
   attacker"; "because of batteries" on every no-violation claim; "screening" wherever not OpenDSS-checked.
7. The old tab app is kept at `ui/explore.html` ("Engine explorer" link in the footer).

## Owners (do not edit outside your paths; report anything else you need in your final message)

| Agent | Owns |
|---|---|
| ENGINE | `sim/p1_build.py` (parameters only, defaults byte-identical), `sim/feeder.py` (readouts), `sim/scenarios.py` (new), `sim/contracts.py`, `sim/verify*.py`, `sim/tests/test_scenarios.py` (new), `ui/data/story/**`, `ui/data/p1/variants/**`, `ui/data/p1/extras/**`, `ui/data/p1/worker_kill.json`, `ui/data/p3/**`, `docs/contracts.md` (append a section) |
| PLANNER | `sim/planner.py` (new), `sim/tests/test_planner.py`, `data/planner/**`, `ui/data/p2/planner.json`, `ui/lib/planner.js`, `ui/test/planner.test.js`, `docs/contracts-planner.md` (new) |
| UI-A | `ui/index.html`, `ui/explore.html`, `ui/story/app.js`, `ui/story/shell.js`, `ui/story/configure.js`, `ui/story/run.js`, `ui/story/story.css`, `ui/lib/data.js`, `ui/test/core.test.js`, `ui/test/story-*.test.js`, `scripts/deeplinks.txt`, `scripts/smoke_*` |
| UI-B | `ui/story/results.js`, `ui/story/learnings.js`, `ui/story/pages-b.css`, `ui/story/dev-b.html`, `ui/test/pagesb-*.test.js` |
| TRUTH | `sim/constants.py`, `sim/calibrate.py`, `sim/bench.py`, `sim/tests/test_contracts.py`, `sim/tests/test_verify.py`, `docs/*.md` except `contracts*.md`/`story-contract.md`, `ui/data/beats.json`, `README.md`, `previous-work/handoff/README.md`, `docs/NUMBERS.md` (new) |

## Data contracts (new files; all use the standard envelope and labels of `docs/contracts.md` A.1/A.2)

**`ui/data/story/index.json`** (`schema: "hb.story.v1"`, producer `sim.scenarios`):
```
default: "<scenario id>"
levers: { evening|policy|failure|fleet|cls|reserve|soc0|growth: { label, default, options: [{id, label, tag?, why?{text,label,cite}}] } }
scenarios: [{ id, title, preset?, levers:{evening, policy, failure, fleet, cls, reserve, soc0, growth},
              meta: "<path under ui/data>", branch: "<path>", extras: "<path>", compare: {none?, naive?, aware?: "<branch path>"},
              gz: bool, summary: {<labelled headline values, same keys as meta.summary.<branch>>},
              engine: {buildSeconds{v,label,cite}, solves{v,label}} }]
unavailable: [{ levers:{partial}, reason }]
```
Scenario ids: `<evening>/<policy>[/<failure>][/<lever>=<value>]`, e.g. `2026-08-23/aware`, `2026-08-23/aware/faults`,
`2026-08-23/naive/fleet=192`. Paths are relative to `ui/data/`. The 23 Aug base branches stay the committed plain
`p1/<branch>.json` (byte-identical). Other evenings are the committed `p1/days/<date>/<branch>.json.gz`.
Variants: `p1/variants/<lever>=<value>/{meta.json, naive.json.gz, aware.json.gz}` in exactly the A.5/A.6 shapes.
`worker_kill`: `p1/worker_kill.json` (copy of `resilience/out/p1/worker_kill.json`, A.6b). `covert`:
`p3/covert.json` (copy of `resilience/out/p3/covert.json`, A.11); its scenario plays the 23 Aug aware branch.

**`ui/data/p1/extras/<scenario id with / → _>.json.gz`** (`schema: "hb.p1extras.v1"`), one per playable scenario:
```
steps, start, stepSeconds
vTfMilli[steps][379]   int, round(1000 × lowest home voltage in pu) per transformer; 0 = isolated          SIM
busOrder[379]          transformer indices ordered by path distance from the substation                   DERIVED
busDistKm[379]         that distance along the SMART-DS lines, km                                          DERIVED
headKW[steps], headKVAr[steps]   feeder-head P and Q (tenths)                                              SIM
capKVAr[steps]         capacitor bank output (tenths)                                                       SIM
feederLoadKW[steps]    all home load (tenths)                                                                SIM
worstPct[steps], worstTf[steps]  worst transformer loading (tenths) and its index                           SIM
moments[{k, t, rule, text, label}]   the rule log (first over 100%, most at once, first normal-rating event,
                                     first above 150%, protection, fleet starts discharging, fleet lowest,
                                     fleet starts recharging, each fault, end of run)                        SIM
failures[{kind, where, k0, k1, text, label}]  scripted faults + network-limit intervals (codes 3/4/5,
                                     merged when gaps ≤ 5 min) + stale/expired batteries                    SIM
```
**`ui/data/p2/planner.json`**: `DESIGN-CAPACITY-PLANNER.md` §2.3 (`hb.planner.v1`), plus `perK.g20`, `perK.g50`
(home-load growth levels for Learnings Q3). Documented in `docs/contracts-planner.md`.

## URL scheme (UI-A implements; TRUTH writes beat links with it)
`ui/index.html?page=configure|running|run|results|learnings|markets&s=<scenario id>&k=<step>&speed=<x>&q=<1-4>&tf=<index>&n=<0-50>`
Defaults are omitted. Unknown `s` falls back to `default` with a notice.

## Windows machine (Git Bash)
`PY=~/hb-overnight/.venv/Scripts/python.exe`, `HB_LOCK_HELD=1`, `NODE_OPTIONS=--experimental-websocket`,
`CHROME="/c/Program Files/Google/Chrome/Application/chrome.exe"`. Python `read_text()` needs `encoding="utf-8"`.
Tests: `$PY -m unittest discover -s sim/tests -t .` (~4 min) and `node --test ui/test/*.test.js`. Known Windows-only
reds (being fixed by TRUTH): `os.getloadavg`, three path-separator asserts. `resilience/` imports root `sim/` and
must keep passing: `$PY -m unittest discover -s resilience/tests -t .`. Never `cd` into subfolders without a subshell.
