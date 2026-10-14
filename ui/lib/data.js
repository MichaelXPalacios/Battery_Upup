// ui/lib/data.js (L0): deep-link parsing and the data loaders, with the fixture fallback.
// Real data lives in ui/data/<path>; fixtures in ui/data/fixtures/<path>. A loader tries the real file first and
// falls back to the fixture on 404; any fixture load marks the page (body data-fixture="1" + the FIXTURE banner).
// Everything is fetched from the same origin by relative URL: no network at view time.

import { dateLabel } from './format.js';

export const VIEWS = ['p1', 'p2', 'more'];
export const BRANCHES = ['none', 'naive', 'aware', 'aware_faults'];
export const CAMS = ['feeder', 'street', 't240'];
// Round 2 (UX_SPEC_R2 4.2.4). The P1 evening every link means unless it names another real ERCOT day (&date=).
export const DEFAULT_DATE = '2026-08-23';
// Playback speeds a link may ask for (&speed=). l4's transport offers the same list; 8x is dropped in round 2.
export const SPEEDS = [0.1, 0.25, 0.5, 1, 2, 4];
// A link is "bare" (the first open: intro card on P1) only when it names none of these.
const NOT_BARE = ['branch', 't', 'beat', 'date'];

/** Parse a deep link (location.search). Unknown or malformed values fall back to defaults.
 *  `bare` is NON-enumerable: spreads and patches ({...link, ...patch}) drop it, so a derived link is never bare,
 *  and round trips (parseLink(linkQuery(l)) deep-equals l) ignore it. */
export function parseLink(search) {
  const q = new URLSearchParams(search || '');
  const pick = (k, list, dflt) => (list.includes(q.get(k)) ? q.get(k) : dflt);
  const t = q.get('t');
  const n = parseInt(q.get('n'), 10);
  const sp = q.get('speed') === null || q.get('speed') === '' ? NaN : Number(q.get('speed'));
  const link = {
    view: pick('view', VIEWS, 'p1'),
    branch: pick('branch', BRANCHES, 'aware'),
    t: /^\d{1,2}:\d{2}$/.test(t || '') ? t : null,
    cam: pick('cam', CAMS, null),
    combo: /^[a-z]+-[a-z]+-[a-z0-9]+-g\d+$/.test(q.get('combo') || '') ? q.get('combo') : null,
    home: q.get('home') || null,
    n: Number.isFinite(n) && n >= 1 && n <= 10 ? n : null,
    beat: q.get('beat') || null,
    nowebgl: q.get('nowebgl') === '1',
    smoke: q.get('smoke') === 'dump' ? 'dump' : null,
    date: /^\d{4}-\d{2}-\d{2}$/.test(q.get('date') || '') ? q.get('date') : DEFAULT_DATE,
    speed: SPEEDS.includes(sp) ? sp : null,
    hold: q.get('hold') !== '0',
    cap: q.get('cap') !== '0',
  };
  Object.defineProperty(link, 'bare', { value: NOT_BARE.every((k) => !q.has(k)), enumerable: false });
  return link;
}

/** Build a query string for a link object (the inverse of parseLink for the keys that are set).
 *  Defaults of the round-2 keys are never written: no date=2026-08-23, no hold=1, no cap=1. */
export function linkQuery(link) {
  const q = new URLSearchParams();
  for (const k of ['view', 'branch', 't', 'cam', 'combo', 'home', 'n', 'beat']) if (link[k] != null) q.set(k, String(link[k]));
  if (link.date && link.date !== DEFAULT_DATE) q.set('date', link.date);
  if (link.speed != null) q.set('speed', String(link.speed));
  if (link.hold === false) q.set('hold', '0');
  if (link.cap === false) q.set('cap', '0');
  if (link.nowebgl) q.set('nowebgl', '1');
  return '?' + q.toString();
}

export class HttpError extends Error {
  constructor(status, path) { super(`HTTP ${status} for ${path}`); this.status = status; this.path = path; }
}

const state = { base: 'data/', fixture: false, listeners: [], cache: new Map(), fetch: (...a) => fetch(...a) };

/** For tests: swap the fetch function and base path. */
export function _configure({ base, fetchFn } = {}) {
  if (base) state.base = base;
  if (fetchFn) state.fetch = fetchFn;
  state.cache.clear();
  state.fixture = false;
}

export function isFixture() { return state.fixture; }
export function onFixture(cb) { state.listeners.push(cb); if (state.fixture) cb(); }
function markFixture() {
  if (state.fixture) return;
  state.fixture = true;
  for (const cb of state.listeners) { try { cb(); } catch (e) { /* the shell counts listener errors */ throw e; } }
}

export async function getJSON(path) {
  if (state.cache.has(path)) return state.cache.get(path);
  const p = (async () => {
    const r = await state.fetch(state.base + path);
    if (!r.ok) throw new HttpError(r.status, path);
    return r.json();
  })();
  state.cache.set(path, p);
  p.catch(() => state.cache.delete(path));
  return p;
}

/** Real file, else its fixture (marks the page as fixture). Other HTTP errors propagate. */
export async function getWithFixture(path) {
  try {
    return await getJSON(path);
  } catch (e) {
    if (!(e instanceof HttpError) || e.status !== 404) throw e;
    const doc = await getJSON('fixtures/' + path);
    markFixture();
    return doc;
  }
}

/** A gzipped JSON file (history days, `*.json.gz`). Bytes starting 1f 8b are gunzipped with DecompressionStream;
 *  otherwise (a host that set Content-Encoding: gzip already decoded them) they are parsed as plain JSON. Cached. */
export async function getGz(path) {
  if (state.cache.has(path)) return state.cache.get(path);
  const p = (async () => {
    const r = await state.fetch(state.base + path);
    if (!r.ok) throw new HttpError(r.status, path);
    const buf = new Uint8Array(await r.arrayBuffer());
    if (buf.length >= 2 && buf[0] === 0x1f && buf[1] === 0x8b) {
      return new Response(new Blob([buf]).stream().pipeThrough(new DecompressionStream('gzip'))).json();
    }
    return JSON.parse(new TextDecoder().decode(buf));
  })();
  state.cache.set(path, p);
  p.catch(() => state.cache.delete(path));
  return p;
}

/** Optional file: null on 404 (no fixture). */
export async function getOptional(path) {
  try { return await getJSON(path); } catch (e) { if (e instanceof HttpError && e.status === 404) return null; throw e; }
}

export const loadTopology = () => getJSON('topology.json');
export const loadFootprints = () => getOptional('footprints.json');       // L4; missing homes draw as 12 m squares
export const loadBeats = () => getOptional('beats.json');                 // L5
export const loadEngine = () => getOptional('engine.json');               // L2
export const loadP1Meta = () => getWithFixture('p1/meta.json');           // L2
export const loadP1Branch = (b) => getWithFixture(`p1/${b}.json`);        // L2
export const loadP2Index = () => getWithFixture('p2/index.json');         // L3
// ---- history days (HIST-R2; docs/contracts.md A.5h, A.6h, A.9h, A.10). The default date is 23 Aug under p1/ with its
// fixture fallback, as before; every other date lives under p1/days/<date>/ and NEVER falls back to a fixture.
export const loadP1Days = () => getOptional('p1/days/index.json');        // L2 (A.10)
export const loadCalendar = () => getOptional('p1/days/calendar.json');   // L2 (A.9h, Should)
export const loadP1MetaFor = (date) => (!date || date === DEFAULT_DATE ? loadP1Meta() : getJSON(`p1/days/${date}/meta.json`));
export const loadP1BranchFor = (date, b) => (!date || date === DEFAULT_DATE ? loadP1Branch(b) : getGz(`p1/days/${date}/${b}.json.gz`));

/** Which evening a P1 link can show. {date, simulated, row, notice}: the default date is always simulated; another
 *  date is simulated only when p1/days/index.json lists it. Anything else is a visible notice plus 23 Aug (never a
 *  fixture, never a silent fallback). `faults` is false on history days (failures are scripted for 23 Aug only). */
export async function resolveP1Date(date) {
  if (!date || date === DEFAULT_DATE) return { date: DEFAULT_DATE, simulated: true, row: null, notice: null, faults: true };
  let idx = null;
  try { idx = await loadP1Days(); } catch (e) { idx = null; }
  const row = idx && Array.isArray(idx.days) ? idx.days.find((d) => d.date === date) : null;
  if (row) return { date, simulated: true, row, notice: null, faults: false };
  return { date: DEFAULT_DATE, simulated: false, row: null, faults: true,
    notice: `${dateLabel(date) || date} is not simulated; showing ${dateLabel(DEFAULT_DATE)}.` };
}
export const loadP2Combo = (id) => getWithFixture(`p2/${id}.json`);       // L3

/** Apply a beat from beats.json over a link: the beat's `link` (a query string or object) sets every key it names. */
export function applyBeat(link, beats) {
  if (!link.beat || !beats) return link;
  const list = Array.isArray(beats) ? beats : (beats.beats || []);
  const b = list.find((x) => x.id === link.beat);
  if (!b) return link;
  const src = typeof b.link === 'string' ? parseLink(b.link) : { ...link, ...(b.link || {}) };
  const explicit = typeof b.link === 'string' ? new URLSearchParams(b.link) : null;
  const out = { ...link };
  for (const k of Object.keys(src)) {
    if (explicit ? explicit.has(k) : (b.link && k in b.link)) out[k] = src[k];
  }
  return out;
}

// ---- the story app (ui/index.html + ui/story/, docs/story-contract.md "URL scheme") ---------------------------------
// ui/index.html?page=configure|running|run|results|learnings|markets&s=<scenario id>&k=<step>&speed=<x>&q=<1-4>&tf=<index>&n=<0-50>
// plus two dev keys: &cat=<catalogue path under ui/data> (default story/index.json) and &nowebgl=1 (the 2D scene).
// Defaults are omitted when a link is written: page=configure, k=0, speed=0.25, and s when it equals the catalogue default.
export const STORY_PAGES = ['configure', 'running', 'run', 'results', 'learnings', 'markets'];
export const STORY_DEFAULT_PAGE = 'configure';
export const STORY_DEFAULT_SPEED = 0.25;           // 0.25x = 2.5 simulated minutes per second (story contract ruling 5)
export const STORY_CATALOGUE = 'story/index.json';
const STORY_ID_RE = /^\d{4}-\d{2}-\d{2}(\/[a-z_]+){1,2}(\/[a-z0-9_]+=[a-z0-9_.]+)?$/;
const CAT_RE = /^[A-Za-z0-9_\-/]+\.json(\.gz)?$/;

/** Parse a story link. Unknown or malformed values become null (the page's default); `s` is checked against the
 *  catalogue by the app (an unknown id falls back to the catalogue default with a notice). */
export function parseStoryLink(search) {
  const q = new URLSearchParams(search || '');
  const int = (k, lo, hi) => {
    const raw = q.get(k);
    if (raw === null || !/^\d+$/.test(raw)) return null;
    const n = Number(raw);
    return n >= lo && n <= hi ? n : null;
  };
  const sp = q.get('speed') === null || q.get('speed') === '' ? NaN : Number(q.get('speed'));
  const s = q.get('s');
  const cat = q.get('cat');
  return {
    page: STORY_PAGES.includes(q.get('page')) ? q.get('page') : STORY_DEFAULT_PAGE,
    s: s && s.length <= 120 && STORY_ID_RE.test(s) ? s : null,
    k: int('k', 0, 100000),
    speed: SPEEDS.includes(sp) ? sp : null,
    q: int('q', 1, 4),
    tf: int('tf', 0, 100000),
    n: int('n', 0, 50),
    cat: cat && CAT_RE.test(cat) && !cat.includes('..') ? cat : null,
    nowebgl: q.get('nowebgl') === '1',
  };
}

/** A story link as a query string ("?page=run&s=..."), defaults omitted. `defaults.s` is the catalogue default id. */
export function storyLinkQuery(link, defaults = {}) {
  const q = new URLSearchParams();
  if (link.page && link.page !== STORY_DEFAULT_PAGE) q.set('page', link.page);
  if (link.s && link.s !== defaults.s) q.set('s', link.s);
  if (Number.isInteger(link.k) && link.k > 0) q.set('k', String(link.k));
  if (link.speed != null && link.speed !== STORY_DEFAULT_SPEED) q.set('speed', String(link.speed));
  for (const k of ['q', 'tf', 'n']) if (Number.isInteger(link[k])) q.set(k, String(link[k]));
  if (link.cat && link.cat !== STORY_CATALOGUE) q.set('cat', link.cat);
  if (link.nowebgl) q.set('nowebgl', '1');
  // '/' and '=' are legal inside a query value: keep scenario ids readable (2026-08-23/naive/fleet=192)
  return '?' + q.toString().replace(/%2F/g, '/').replace(/%3D/g, '=');
}

/** Any data file by path under ui/data: `*.json.gz` through getGz, anything else through getJSON. Cached. */
export const getAny = (path) => (/\.gz$/.test(path) ? getGz(path) : getJSON(path));

/** True when a file's fetch has already started (the Running screen shows it as loading or loaded). */
export function isCached(path) { return state.cache.has(path); }
