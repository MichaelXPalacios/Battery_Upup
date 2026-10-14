// ui/story/app.js (UI-A): the story app's router. ui/index.html loads it; it loads the catalogue, draws the shell
// (shell.js) and mounts one page at a time into <main>, driven by ?page= (docs/story-contract.md "URL scheme").
//
// ======================================= PAGE-MODULE INTERFACE (UI-B builds against this) =======================
// Each page is ui/story/<page>.js exporting
//     export async function mount(root, ctx) -> { setK?(k), dispose() }
// `root` is an empty <main class="st-main"> the page owns (the shell's header and footer are outside it). The app
// awaits mount(), then sets body data-status="ready". dispose() must stop timers/animation frames and free WebGL.
// setK(k), if returned, is called by the app when the cursor changes from OUTSIDE the page (browser back/forward to
// the same page with another &k=). The page's own scrubbing calls ctx.setK instead.
//
// ctx = {
//   catalogue          ui/data/story/index.json (hb.story.v1) as loaded (or &cat=<path under ui/data>, tests only;
//                      no fallback: a catalogue that does not load is an error on the page)
//   scenario           the catalogue scenario for &s= (the catalogue default when &s= is absent or unknown: notice)
//   params             the parsed link {page, s, k, speed, q, tf, n, cat, nowebgl}; a key not in the link is null
//   getJSON(path)      fetch + parse a JSON file; path relative to ui/data/ (cached per path)
//   getAny(path)       the same, and *.json.gz through data.getGz (cached)
//   k                  the shared cursor (a step index) at mount: &k=, or null when the link has none (a page picks its
//                      own start, e.g. Results opens at the evening's worst minute). Kept current by setK
//   setK(k)            move the shared cursor: sets ctx.k, writes &k= (replaceState, throttled) and calls every onK fn
//   onK(fn)            subscribe fn(k) to cursor moves; returns an unsubscribe function (dropped on page change)
//   nav(page, params)  go to another page: params merge over the current {s, k, speed, q, tf, n} (null drops a key);
//                      history.pushState, the current page is disposed, the next one mounts. A third argument
//                      {replace: true} uses replaceState (Running -> Run, so Back never lands on Running)
//   link(params)       the href of the same merge ("?page=results&s=...&k=..."), for <a href>
//   num(labelled, opts) HTML string: the formatted value + its provenance tag (title = cite). A bare number THROWS
//                      (format.js LabelError, counted in data-errors). opts = format.js fmtValue opts {digits, unit,
//                      pct10, kw10, money, signed} + {screening: true}: adds a dashed SCREENING tag (not
//                      OpenDSS-checked). Labels REAL SIM DERIVED ASSUMPTION, plus UNVERIFIED and SCREENING
//   fmt                ui/lib/format.js (the module)
// }
// Body flags the app owns (the smoke test reads them): data-status loading|ready|error, data-errors, data-offsite,
// data-page, data-placeholder (a UI-B page not merged yet). data-webgl: a page with a 3D scene sets ok|fallback
// itself; the app sets "none" before every mount.
// Results = ui/story/results.js and Learnings = ui/story/learnings.js are UI-B's; until a file exists the app shows
// "page not merged yet" (a 404 on the module), never an error.
// ==================================================================================================================
import * as data from '../lib/data.js';
import * as fmt from '../lib/format.js';
import { mountTips } from '../lib/tip.js';
import { createShell, numHTML } from './shell.js';

const body = document.body;
const health = window.__hbHealth || (window.__hbHealth = { errors: 0, messages: [], onchange: null });
const setFlag = (k, v) => { body.dataset[k] = String(v); };
const PAGES = {
  configure: { file: './configure.js', fn: 'mount' },
  running: { file: './run.js', fn: 'mountRunning' },
  run: { file: './run.js', fn: 'mount' },
  results: { file: './results.js', fn: 'mount', owner: 'UI-B' },
  learnings: { file: './learnings.js', fn: 'mount', owner: 'UI-B' },
  markets: { file: './markets.js', fn: 'mount' },     // 5 Markets: the grid-research desk (docs/research/arena/)
  arena: { file: './arena.js', fn: 'mount' },         // 6 Arena: how should a battery trade? (docs/research/arena/)
  siting: { file: './siting.js', fn: 'mount' },       // 7 Siting: where on the physical grid? (docs/research/grid/)
  dispatch: { file: './dispatch.js', fn: 'mount' },   // 8 Dispatch: run it safely, prove what it earned (dispatch/)
};
const CARRY = ['s', 'k', 'speed', 'q', 'tf', 'n', 'cat', 'nowebgl'];

export function reportError(err) {
  health.errors += 1;
  health.messages.push(String(err && err.stack ? err.stack : err));
  setFlag('errors', health.errors);
  console.error('[hb story]', err);
}
health.onchange = () => setFlag('errors', health.errors);
setFlag('errors', health.errors);

function countOffsite() {
  const here = location.origin;
  let n = 0;
  for (const e of performance.getEntriesByType('resource')) {
    try { if (new URL(e.name).origin !== here) n += 1; } catch (err) { n += 1; }
  }
  setFlag('offsite', n);
  return n;
}
try { new PerformanceObserver(() => countOffsite()).observe({ type: 'resource', buffered: true }); } catch (e) { /* counted at ready */ }

const app = { catalogue: null, params: null, scenario: null, page: null, inst: null, main: null, shell: null,
  kFns: new Set(), k: 0, seq: 0, urlTimer: 0 };

/** Merge params over the current link (null drops a key) and return the link object. */
function merged(page, params = {}) {
  // the URL is the source of truth: a page may have moved &s= (Configure) or &speed= (Run) with replaceState
  const cur = { ...(app.params || {}), ...data.parseStoryLink(location.search) };
  const out = { page: page || cur.page };
  for (const key of CARRY) out[key] = cur[key] ?? null;
  out.k = app.k || null;
  for (const [key, v] of Object.entries(params || {})) out[key] = v;
  return out;
}
const linkFor = (page, params) => data.storyLinkQuery(merged(page, params), { s: app.catalogue && app.catalogue.default });

/** The catalogue: ENGINE's ui/data/story/index.json (hb.story.v1), or &cat=<path under ui/data> (tests). Nothing
 *  else: when it does not load, the page says so (never a stand-in catalogue). */
async function loadCatalogue(params) {
  const path = params.cat || data.STORY_CATALOGUE;
  let doc;
  try {
    doc = await data.getJSON(path);
  } catch (e) {
    const why = e instanceof data.HttpError && e.status === 404 ? 'it is not built' : (e && e.message) || String(e);
    throw new Error(`the scenario catalogue ui/data/${path} did not load (${why}). Every page reads its runs from it.`);
  }
  if (!doc || !Array.isArray(doc.scenarios) || !doc.levers) throw new Error(`ui/data/${path} is not a story catalogue (hb.story.v1)`);
  return { doc, path };
}

function resolveScenario(cat, s) {
  const list = cat.scenarios || [];
  const byId = (id) => list.find((x) => x.id === id) || null;
  if (s && byId(s)) return { scenario: byId(s), notice: null };
  const d = byId(cat.default) || list[0] || null;
  return { scenario: d, notice: s ? `Scenario "${s}" is not in the catalogue; showing ${d ? d.title || d.id : 'the default'}.` : null };
}

function setK(k) {
  const n = Number(k);
  if (!Number.isFinite(n)) return;
  app.k = Math.max(0, Math.round(n));
  try { localStorage.setItem('hb-story-k', String(app.k)); } catch (e) { /* private mode */ }
  clearTimeout(app.urlTimer);
  app.urlTimer = setTimeout(() => {
    // re-read the URL first: a page may have written its own keys (speed) with replaceState
    app.params = { ...data.parseStoryLink(location.search), page: app.params.page, s: app.params.s, k: app.k || null };
    history.replaceState(history.state, '', linkFor(app.params.page, {}));
  }, 250);
  for (const fn of [...app.kFns]) { try { fn(app.k); } catch (e) { reportError(e); } }
}

async function nav(page, params = {}, opts = {}) {
  const href = linkFor(page, params);
  if (opts.replace) history.replaceState({ hb: 1 }, '', href); else history.pushState({ hb: 1 }, '', href);
  await route();
}

function makeCtx() {
  const ctx = {
    catalogue: app.catalogue, scenario: app.scenario, params: { ...app.params },
    getJSON: (p) => data.getJSON(p), getAny: (p) => data.getAny(p),
    k: app.params.k,
    setK: (k) => { setK(k); ctx.k = app.k; },
    onK: (fn) => { app.kFns.add(fn); return () => app.kFns.delete(fn); },
    nav: (page, params, opts) => nav(page, params, opts),
    link: (params = {}) => linkFor(params.page || app.params.page, params),
    num: (x, opts) => numHTML(x, opts),
    fmt,
  };
  return ctx;
}

async function loadPage(page) {
  const def = PAGES[page];
  const url = new URL(def.file, import.meta.url);
  if (def.owner) {
    // UI-B's pages: a missing file is "not merged yet", not an error
    let exists = false;
    try { exists = (await fetch(url, { method: 'HEAD', cache: 'no-store' })).ok; } catch (e) { exists = false; }
    if (!exists) return null;
    // UI-B's stylesheet (ui/story/pages-b.css), linked once when it exists (no 404 in the console when it does not)
    if (!document.querySelector('link[data-pages-b]')) {
      const css = new URL('./pages-b.css', import.meta.url);
      let ok = false;
      try { ok = (await fetch(css, { method: 'HEAD', cache: 'no-store' })).ok; } catch (e) { ok = false; }
      if (ok) {
        const l = document.createElement('link');
        l.rel = 'stylesheet'; l.href = css.href; l.dataset.pagesB = '1';
        document.head.appendChild(l);
      }
    }
  }
  const mod = await import(url.href);
  if (typeof mod[def.fn] !== 'function') throw new Error(`${def.file} does not export ${def.fn}()`);
  return mod[def.fn];
}

function placeholder(root, page) {
  setFlag('placeholder', page);
  root.innerHTML = `<div class="st-placeholder"><div class="st-eyebrow">${page.toUpperCase()}</div>
    <div class="st-ph-title">This page is not merged yet</div>
    <p>${page === 'results' ? 'Results' : 'Learnings'} is built by UI-B (<code>ui/story/${page}.js</code>). It mounts here as soon as the file exists.</p>
    <a class="st-btn-ghost" href="${linkFor('run', {})}">← Back to Run</a></div>`;
  return { dispose() {} };
}

async function route() {
  const seq = ++app.seq;
  const params = data.parseStoryLink(location.search);
  const { scenario, notice } = resolveScenario(app.catalogue, params.s);
  if (app.inst) { try { app.inst.dispose(); } catch (e) { reportError(e); } }
  app.inst = null;
  app.kFns.clear();
  app.params = params;
  app.scenario = scenario;
  app.k = params.k ?? 0;
  app.page = params.page;
  setFlag('status', 'loading');
  setFlag('webgl', 'none');
  setFlag('page', params.page);
  delete body.dataset.placeholder;
  app.shell.update({ page: params.page, scenario, catalogue: app.catalogue, link: (p, x) => linkFor(p, x), nav: (p, x) => nav(p, x), getJSON: data.getJSON });
  if (notice) app.shell.notice(notice);
  const main = document.createElement('main');
  main.className = `st-main st-page-${params.page}`;
  app.main.replaceWith(main);
  app.main = main;
  if (!scenario) throw new Error('The catalogue lists no scenarios');
  const mountFn = await loadPage(params.page);
  if (seq !== app.seq) return;
  const inst = mountFn ? await mountFn(main, makeCtx()) : placeholder(main, params.page);
  if (seq !== app.seq) { try { inst && inst.dispose && inst.dispose(); } catch (e) { reportError(e); } return; }
  app.inst = inst || { dispose() {} };
  countOffsite();
  setFlag('status', 'ready');
}

window.addEventListener('popstate', () => {
  const p = data.parseStoryLink(location.search);
  if (app.params && p.page === app.params.page && p.s === app.params.s && app.inst) {
    app.params = p;
    app.k = p.k ?? 0;
    if (typeof app.inst.setK === 'function') { try { app.inst.setK(app.k); } catch (e) { reportError(e); } }
    for (const fn of [...app.kFns]) { try { fn(app.k); } catch (e) { reportError(e); } }
    return;
  }
  route().catch(fail);
});

function fail(e) {
  reportError(e);
  if (app.shell) app.shell.error(`Could not open this page: ${e && e.message ? e.message : e}`);
  countOffsite();
  setFlag('status', 'error');
}

async function boot() {
  setFlag('status', 'loading');
  mountTips(document);
  const params = data.parseStoryLink(location.search);
  app.shell = createShell(body);
  app.main = app.shell.main;
  const cat = await loadCatalogue(params);
  app.catalogue = cat.doc;
  if (cat.path !== data.STORY_CATALOGUE) app.shell.banner(`Catalogue override: ui/data/${cat.path} (&cat=), not the engine's story/index.json.`);
  data.getJSON('topology.json').then((t) => app.shell.setFeeder(t)).catch(() => { /* the footer keeps its words */ });
  await route();
}

boot().catch(fail);
