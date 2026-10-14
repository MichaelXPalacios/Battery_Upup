# Run the demo

Batter Up is a static web app. A Python simulator (`sim/`) wrote every number the page shows into committed JSON under `ui/data/`; the browser only reads those files. No server logic, no network at view time, no language model anywhere in the numbers, and **OpenDSS is the referee** of every violation.

## Two commands

```sh
scripts/setup.sh     # once: checks (or creates) the Python venv; ends "SETUP: OK"
scripts/serve.sh     # a plain static server from the repo root on port 8765
```

Open **http://127.0.0.1:8765/ui/index.html**. Stop the server with Ctrl-C.

`serve.sh` is only a file server (`python3 -m http.server`). Any static server rooted at the repo works; the page must be served from the **repo root** so that `/ui/` (and the archived `/previous-work/demos/grid-stories/ui/dist/` and `/previous-work/four-home-simulation/`) resolve from one origin.

## The story pages

One scenario at a time, chosen on page 1 and carried through the rest. Every scenario is a run the engine already made and committed; the catalogue that lists them is `ui/data/story/index.json`.

| Page | Link | What it shows |
|---|---|---|
| **Configure** | `?page=configure` | Evening (23 Aug 2026 by default; 22 Jul, 14 Aug and 26 Aug are other real ERCOT evenings) × policy (no batteries, naive, feeder-aware) × failure (none, pieces fail, controller crash, hidden attacker: 23 Aug and feeder-aware only). The fleet levers (size 48 / 96 / 144 / 192, Core or Legacy, reserve 20–50%, start charge 60–100%, home-load growth 0 / 20 / 50%) are each a real engine run on 23 Aug, one lever away from the default. Anything not run is disabled with its reason. Presets: "The demo evening", "Record demand", "Priciest evening", "Pieces fail", "Controller crash", "Hidden attacker". |
| **Running** | `?page=running` | The files the scenario plays, loading, and the engine's measured cost for that run (build seconds and OpenDSS solves, from the catalogue and `ui/data/engine.json`). No fake progress: the run already happened. |
| **Run** | `?page=run` | The evening in 3D, 16:00 to 04:00, one-minute steps: the street, the rule-generated story line, the lanes (worst transformer, streets A–D and T-240, price, fleet charge, fleet power) and the "Right now" card, with any failure in red. |
| **Results** | `?page=results` | The verdict and four tiles (worst transformer, normal-rating events, lowest home voltage, fleet charged by 04:00), each with every other committed run beneath it; voltage by bus from the substation outwards; reactive power. Only the hidden-attacker scenario shows ERCOT context: the REAL frequency of 25 Sep 2026, dated "a different day", beside the 3–17 mHz band. |
| **Learnings** | `?page=learnings&q=1..4` | Q1 how many batteries fit (naive vs feeder-aware, OpenDSS); Q2 one transformer, 0 to 50 batteries; Q3 which transformers to upgrade as home load grows; Q4 where the next battery helps most. |
| **Markets** | `?page=markets&q=1..3` | The grid-research desk (`docs/research/arena/`), ERCOT or NYISO from the rail. Q1 one load zone: what the adaptive learner earned on real prices 2015–2026 against the perfect-foresight ceiling, then the 2027–31 forecast fan (P10, median, P90 across 20 futures), risk tiles, six computed sentences, weather and gas sensitivities, seasonality and the year-by-year tape; Q2 the forecast split by weather region; Q3 how a trader reads it (position, distribution, factor, regime, carry). Data: `ui/data/markets/index.json`, rebuilt by `scripts/export_markets.py` from the arena's caches. |

**Link parameters** (defaults are omitted): `s=<scenario id>` (for example `2026-08-23/naive`, `2026-08-23/aware/faults`, `2026-08-23/aware/worker_kill`, `2026-08-23/aware/covert`, `2026-08-23/naive/fleet=192`), `k=<step>` (minutes after 16:00: `k=390` is 22:30), `speed=0.1|0.25|0.5|1|2|4` (default 0.25×, 2.5 simulated minutes a second), `q=1..4`, `tf=<transformer index>`, `n=0..50`. An unknown `s` falls back to the default scenario with a notice. `&nowebgl=1` forces the 2D fallback.

The five-minute video is `docs/demo-script.md`; its beats are `ui/data/beats.json`, one deep link each. The audited numbers, each with its label, file and field, are in `docs/NUMBERS.md`.

## Reading the screen

- **Every number carries a tag:** **R** REAL (ERCOT prices, SMART-DS topology and ratings, OSM footprints, sourced facts), **S** SIM (our simulation: OpenDSS and our controller), **D** DERIVED (arithmetic on REAL or SIM, such as dollars), **A** ASSUMPTION (a value we chose). A dashed **SCREENING** tag marks a number from the per-transformer surrogate that OpenDSS did not re-check. Hover or tap a tag for its source.
- **The feeder** is NREL's SMART-DS Austin P1U: a REAL dataset of a **synthetic** feeder ("realistic but not real"), 1,010 customers (971 homes, 39 small businesses) and 379 service transformers, framed as an **Oncor-suburb stand-in** settled at LZ_NORTH. The 96-battery fleet is **a deliberate stress placement** (ASSUMPTION).
- **Naive** is our assumption of one number, no feeder check, all at once at the price onset. It is not how Base charges; Base's method is not public.
- **Tiers:** over nameplate (above 100%) is amber, not a failure; a normal-rating violation is above 110% for 30 minutes or more; emergency is above 150%. The fuse rule (200% for 10 minutes, 300% for 60 s) is an ASSUMPTION. Tier 0 is sage: **green never means safe**. Every no-violation claim reads "because of batteries".
- **Money** is the fleet's **gross energy value, not Base's profit**.
- **The attacker is fictional.**

## The Engine explorer

The earlier tab app (P1 · where to charge, P2 · where the next battery goes, More) is kept at **`ui/explore.html`**, linked as "Engine explorer" in the footer. Its links are `?view=p1|p2|more` (an old `?view=` or `?beat=` link to `ui/index.html` opens there). It is not in the video.

## If something looks wrong

| Symptom | Try |
|---|---|
| Blank 3D area | reload; `&nowebgl=1` forces the 2D fallback (same scene, top-down). |
| "Scenario … is not in the catalogue" | the link's `s=` is not a committed run; the page shows the default instead. |
| Page says "Could not start" | the server must be rooted at the repo, and `ui/data/story/index.json` and `ui/data/topology.json` must exist. |
| Health check | `<body>` carries `data-status` (loading/ready/error), `data-page`, `data-webgl`, `data-errors` (bare numbers refused) and `data-offsite` (other-origin loads; must be 0). |

## The gate (for builders)

```sh
scripts/check_all.sh                           # unit tests, node tests, the prototype's and four-home's tests,
                                               # contracts, verifiers, and a browser smoke; ends "ALL CHECKS: PASS"
scripts/check_all.sh --full                    # also rebuilds every artifact under the heavy-run lock and byte-compares
python -m unittest discover -s sim/tests -t .  # the engine tests alone (~4 min on Windows, ~10 with a loaded machine)
node --test ui/test/*.test.js                  # the UI tests alone
```

On Windows (Git Bash) use the venv's Python (`PY=~/hb-overnight/.venv/Scripts/python.exe`), set `HB_LOCK_HELD=1` and `NODE_OPTIONS=--experimental-websocket`, and read text files with `encoding="utf-8"`.

The prototype (`previous-work/demos/grid-stories/`) and four-home (`previous-work/four-home-simulation/`) are archived unchanged and still run from the same local server: http://127.0.0.1:8765/previous-work/demos/grid-stories/ui/dist/ and http://127.0.0.1:8765/previous-work/four-home-simulation/four-home.html (the live site serves only `ui/`).
