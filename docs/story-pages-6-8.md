# Story pages 6 to 8: Arena, Siting, Dispatch (brief, 14 Oct 2026)

The story continues from the feeder to the market and operations. Pages 1 to 4 are one feeder's evening. Page 5, Markets,
is the trader's desk over the arena. Pages 6 to 8 answer the three questions of the research and platform work:

| Step | Page | Question it answers | Source of every number |
|---|---|---|---|
| 6 | `arena` | How should a battery trade? Five algorithms, backtests on real prices | `docs/research/arena/` and `research-data/arena_cache/` |
| 7 | `siting` | Where on the physical grid should it sit? | `docs/research/grid/` (`data/*.csv` + `.labels.csv`, README) |
| 8 | `dispatch` | How do you run it safely and prove what it earned? | `dispatch/` (case study, notes, registry) and `research-data/dispatch/` runtime |

The lead has done the wiring and nobody else edits it:
- `STORY_PAGES` (`ui/lib/data.js`), `PAGES` (`ui/story/app.js`), `STEPS`/`NEXT`/`PAGE_FRAMING` (`ui/story/shell.js`)
- the page-list asserts in `ui/test/story-link.test.js` and `story-run.test.js`
- the deep links in `scripts/deeplinks.txt` (`q=1..4` per page)
- placeholder modules `ui/story/{arena,siting,dispatch}.js`

## What each page agent owns

`ui/story/<page>.js`, `ui/story/<page>.css`, `ui/data/<page>/**`, `scripts/export_<page>.py`, `ui/test/story-<page>.test.js`.

Use a class prefix so nothing collides: `ar-` (arena), `si-` (siting), `dp-` (dispatch). Ask the lead for any change to a
shared file (shell, app, data.js, story.css, other pages, deeplinks). Never edit those yourself.

## Page anatomy: follow `ui/story/markets.js` exactly

- **Module contract.** `export async function mount(root, ctx) -> { dispose() }`. Link your CSS once (`ensureCss` as in
  Markets) and load `ctx.getJSON('<page>/index.json')`. If the data is missing, show the `st-placeholder` block the way
  Markets does.
- **Layout.** The grid is a 290 px rail plus a body that scrolls. The rail holds an eyebrow, the page question, numbered
  view buttons (24 px circles, tint when active), and any selectors (`cfg-seg`).
  - The views are the `q` sub-questions, at most 4. Switch them with `history.replaceState(..., ctx.link({page, q}))`
    and then re-render.
  - Below 900 px the rail stacks on top.
- **Body.** It holds:
  - the head: eyebrow, title, and a one-sentence answer
  - one main visual
  - KPI tiles
  - a short list of computed sentences ("reads"), each with labelled numbers
  - detail tables (div grids with ARIA table roles)

  **One story per view.**
- **Charts.** Hand-written SVG in a `viewBox`, with labels and ticks as positioned HTML (Markets' pattern). There is no
  chart library, no WebGL (`data-webgl` must stay `none`), no workers and no absolute `http(s)://` URL anywhere.
- **Maps.** Use a hand-projected SVG over a committed outline in `ui/data`. Tile basemaps and Leaflet are not allowed.

## Design rules (from `.claude/skills/hugging-base-design` and `previous-work/docs-history/design-handoff/`)

**Colour**
- Use only the tokens in `ui/story/story.css :root`: `--brand --brand-hover --brand-tint --ink --ink-2 --muted --faint
  --bg --panel --border --border-strong --healthy --tier-1..3 --fail-* --excess`. Don't type new hex values.
- Light theme only, matching the story app.
- **Green (`--brand`) means "ours" or "selected", never "safe".** Safety is sage (`--healthy`) and the absence of amber
  or red. Amber and red mean trouble or a loss.

**Type, shape and motion**
- Hanken Grotesk 400/700 only, with tabular figures. Sizes: stat 34, title 26, body 14, small 12.5, tag 10.
- Spacing 4/8/12/16/20/24/32; radii 3/6/8/10/12. No shadows.
- Motion: 300 ms `cubic-bezier(.3,.7,.2,1)`, no bounce.

**Reading and interaction**
- The page must read at 1920×1080 (16:9) after video compression.
- No hover-only information: a click or focus shows the same thing.
- Provide focus states and ARIA roles.

**Provenance**
- Every number carries its tag (REAL / SIM / DERIVED / ASSUMPTION; UNVERIFIED is UI-only).
- Never colour-code the tags, and never let them clip.
- Render a headline value with `ctx.num({v, label, cite}, opts)`, and a bulk array through its `series` label (`sTag`).
- A value the page computes is `derived(v, 'formula')`, i.e. DERIVED with the formula as its cite.
- A test strips `.num` and `.chip` spans from the generated HTML and asserts no digit remains.

**Copy**
- Plain language. "Battery", "node", "constraint" and "upgrade" are fine; explain a term the first time it appears.
- **Never imply a language model chose a dispatch, a setpoint or a ranking.** Models only forecast prices; deterministic
  code (the LP, the guard, the ranking rules) decides.
- No Base or ERCOT logos (the names in text are fine). Name the adversary nowhere.
- Carry the honest caveats into the UI. Siting: node prices are SIM, levels good to about 2×. Dispatch: Gate 2 not
  proven, Gate 3 not passed, and the shadow's edge rests on ancillary-service assumptions.

## Data files

**The export script**
- `scripts/export_<page>.py` uses **stdlib + numpy only**. Run it with the repo Python
  `~/hb-overnight/.venv/Scripts/python.exe` (3.14, numpy 2.5.3). It reads the sources above and never writes them.
- It writes `ui/data/<page>/index.json` (and any small companion file) through `sim.contracts`, the same way as
  `scripts/export_markets.py`:
  - `envelope("<page>", "scripts.export_<page>", inputs={"prices_sha256": None, "loads_sha256": None, "topology_sha256": None}, constants=..., sources=..., series=...)`
  - headline numbers as `{v, label, cite}` under headline keys (`summary`, `headline`, `metrics`, `ranking`, ...)
  - bulk arrays labelled once in `series`
  - deterministic: the same inputs give byte-identical output (`write_json`)

**Budgets.** `ui/data` holds 34.70 of 40 MiB.

| Page | Budget |
|---|---|
| Arena | ≤ 1.0 MiB |
| Siting | ≤ 1.5 MiB, including the Texas outline |
| Dispatch | ≤ 1.2 MiB |

The cap is 4 MiB per file.

**Gates the page must pass**
- `python -m sim.contracts` prints `CONTRACTS: PASS`.
- `python -m sim.verify labels` passes.
- `node --test ui/test/*.test.js` is all green.
- `scripts/smoke_cdp.mjs` reaches `status=ready` with errors=0, offsite=0, webgl=none and a non-trivial screenshot for
  your 4 deep links. `scripts/smoke_layout.mjs` shows no overflow or clipped chips at 1280×800, 1440×900 and 1920×1080.

## Running the smoke tests on this Windows machine

Node is v20, so set `NODE_OPTIONS=--experimental-websocket`. Use your own port: arena 8771, siting 8772, dispatch 8773.

```bash
~/hb-overnight/.venv/Scripts/python.exe -m http.server 8772 --bind 127.0.0.1 --directory C:/Users/Javier/Repo/BaseP/hugging-base   # in the background
NODE_OPTIONS=--experimental-websocket node scripts/smoke_cdp.mjs --base http://127.0.0.1:8772/ui/ --shots <scratch dir> "page=siting" "page=siting&q=2" "page=siting&q=3" "page=siting&q=4"
NODE_OPTIONS=--experimental-websocket node scripts/smoke_layout.mjs --base http://127.0.0.1:8772/ui/
```

Look at your screenshots (open the PNGs) and fix what reads badly. Stop your server when you are done.
