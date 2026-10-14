// ui/story/siting.js: Screen 7 Siting, "Where on the physical grid should a battery sit?" (docs/research/grid/README.md).
// Two files, ui/data/siting/index.json (hb.siting.v1) and texas.json (the Census state outline), both written by scripts/export_siting.py.
// Left rail: view (?q=1..4), and on q=3 the upgrade to test. Four views:
//   q=1 Where to put batteries   a Texas map of the 40 candidate nodes (size = 2 h learner value, shade = gain over the zone), the
//                                constraint lines, a ranked list and a click-to-select site panel; the first-10-MW caveat
//   q=2 Why there                shift factor against hops (electrical distance beats line count), import and export side,
//                                nodes against zones for each of the five algorithms
//   q=3 What moves it            one upgrade at a time: the sites before and after, battery against wires, the saturation fade, who can act
//   q=4 Regions, risk, Tier 2    Panhandle, Valley and Far West from the summer 2026 measured basis, risk exclusions, reliability kept
//                                apart from market value, and what a distribution battery (DESR) or a home fleet (ADER) is paid
// Every value carries its tag: headline numbers come labelled from the file, bulk columns take their label from the file's `series`, and a
// number this page computes is DERIVED with its arithmetic in the cite. Node prices are SIM: levels good to about 2x, rankings steadier.
// No language model chooses a site or a rank here: the study's deterministic code did.

import { tagHTML } from './shell.js';

export const PAGE = 'siting';
export const DATA = 'siting/index.json';
export const TEXAS = 'siting/texas.json';
export const VIEWS = [
  { q: 1, title: 'Where to put batteries', sub: 'The 40 best nodes, on the map' },
  { q: 2, title: 'Why there', sub: 'Electrical distance beats hop count' },
  { q: 3, title: 'What moves it', sub: 'Upgrades, wires and a crowding grid' },
  { q: 4, title: 'Regions, risk, distribution', sub: 'Beyond the 40, and who is paid what' },
];
const STORE = 'hb-siting';
const KIND_WORDS = { reconductor: 'reconductor', rebuild: 'rebuild', plan_765kv: 'interface plan' };
const MAP_W = 1000;
const PX_TO_UNITS = 1000 / 760;        // pin sizes are drawn for a map about 760 px wide, in SVG units

const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const fin = (x) => typeof x === 'number' && Number.isFinite(x);
const avg = (a) => (a.length ? a.reduce((s, x) => s + x, 0) / a.length : 0);
const MONEY = { money: true, digits: 1 };
const MONEY0 = { money: true, digits: 0 };
const PCT0 = { unit: '%', digits: 0 };
const PCT1 = { unit: '%', digits: 1 };
const X1 = { unit: '×', digits: 1 };
const X0 = { unit: '×', digits: 0 };
const D0 = { digits: 0 };
const D1 = { digits: 1 };
const D2 = { digits: 2 };
const MM = { money: true, digits: 1, unit: 'M' };
const MW0 = { digits: 0, unit: ' MW' };
const MINUS = '−';

export const viewOf = (q) => ([1, 2, 3, 4].includes(q) ? q : 1);

/** A bulk value from the file's `series` as a labelled number (label and text from the series entry). */
export function fromSeries(doc, key, v) {
  const s = doc && doc.series && doc.series[key];
  if (!s) throw new Error(`siting: no series ${key}`);
  return { v, label: s.label, cite: s.text };
}
/** A number this page computes from labelled inputs: DERIVED, with the arithmetic in the cite. */
export const derived = (v, cite) => ({ v, label: 'DERIVED', cite: `DERIVED: ${cite}` });
/** A file constant as a labelled number. */
export function constant(doc, name) {
  const c = doc.constants && doc.constants[name];
  if (!c) throw new Error(`siting: no constant ${name}`);
  return { v: c.value, label: c.label, cite: c.cite };
}
const cunit = (doc, name) => (doc.constants[name] && doc.constants[name].unit) || '';
/** A constant with its own unit, ready for num(): {$M: money, digits 0, unit M}. */
export function constOpts(doc, name) {
  const u = cunit(doc, name);
  if (u === '$M') return { money: true, digits: 0, unit: 'M' };
  if (u === '$/MWh') return { money: true, digits: 0, unit: '/MWh' };
  if (u === '%') return { digits: 0, unit: '%' };
  if (u === 'x') return { digits: 0, unit: '×' };
  return { digits: Number.isInteger(doc.constants[name].value) ? 0 : 1, unit: u ? ` ${u}` : '' };
}
/** "{NAME}" in a file text replaced by that constant as a tagged number. */
export function fillText(doc, text, num) {
  return esc(text).replace(/\[\[([^\]]+)\]\]/g, '<span class="si-id">$1</span>')
    .replace(/\{([A-Z0-9_]+)\}/g, (m, name) => (doc.constants[name] ? num(constant(doc, name), constOpts(doc, name)) : m));
}

/** Digits left in an HTML string once every tagged value is removed (ids, years and durations excepted). Pure (tested). */
export function bareDigits(html) {
  return String(html)
    .replace(/<(span|b) class="si-id">[^<]*<\/\1>/g, '').replace(/<span class="num[^"]*">[^<]*<\/span>/g, '').replace(/<span class="chip[^>]*>[^<]*<\/span>/g, '')
    .replace(/<svg[\s\S]*?<\/svg>/g, '').replace(/<[^>]+>/g, ' ')
    .replace(/\b[24] h\b/g, '').replace(/\bU\d\d\b/g, '').replace(/\b20\d\d(?:[–-]\d{2})?\b/g, '').replace(/\bA[1-5]\b/g, '').replace(/HIGH36/g, '').replace(/#\d+/g, '')
    .replace(/&[a-z#0-9]+;/g, '');
}

// ---------------------------------------------------------------------------------------------------- map geometry
const ALBERS = { phi1: 27.5, phi2: 35, phi0: 31.2, lam0: -100 };
const D2R = Math.PI / 180;
/** Albers equal-area conic on the unit sphere: [x east, y north]. */
export function albers(lon, lat, p = ALBERS) {
  const n = (Math.sin(p.phi1 * D2R) + Math.sin(p.phi2 * D2R)) / 2;
  const C = Math.cos(p.phi1 * D2R) ** 2 + 2 * n * Math.sin(p.phi1 * D2R);
  const rho = (phi) => Math.sqrt(C - 2 * n * Math.sin(phi * D2R)) / n;
  const th = n * (lon - p.lam0) * D2R;
  return [rho(lat) * Math.sin(th), rho(p.phi0) - rho(lat) * Math.cos(th)];
}
/** The hand-made projection of the Texas map: SVG units W x H with the outline's box inside a padding. Pure (tested). */
export function projection(outline, W = MAP_W, pad = 0.03) {
  const pts = outline.map(([lo, la]) => albers(lo, la));
  const x0 = Math.min(...pts.map((p) => p[0])), x1 = Math.max(...pts.map((p) => p[0]));
  const y0 = Math.min(...pts.map((p) => p[1])), y1 = Math.max(...pts.map((p) => p[1]));
  const k = W * (1 - 2 * pad) / (x1 - x0);
  const H = Math.round((y1 - y0) * k + 2 * pad * W);
  const ox = pad * W - x0 * k, oy = pad * W + y1 * k;
  const xy = (lon, lat) => { const [x, y] = albers(lon, lat); return [x * k + ox, oy - y * k]; };
  const pct = (lon, lat) => { const [x, y] = xy(lon, lat); return { left: x / W * 100, top: y / H * 100 }; };
  const path = (ll, close = true) => `M${ll.map(([lo, la]) => xy(lo, la).map((v) => v.toFixed(1)).join(' ')).join('L')}${close ? 'Z' : ''}`;
  return { W, H, vb: `0 0 ${W} ${H}`, xy, pct, path, box: { x0: pad * W, y0: pad * W, x1: W - pad * W, y1: pad * W + (y1 - y0) * k } };
}

/** Push overlapping pins apart (deterministic); each keeps a spring to its true position. points: [{id, x, y, r}] -> {id: [x, y]}. Pure (tested). */
export function declutter(points, { iters = 120, gap = 0.92, spring = 0.04 } = {}) {
  const p = points.map((q) => ({ ...q, tx: q.x, ty: q.y }));
  for (let it = 0; it < iters; it++) {
    for (let i = 0; i < p.length; i++) {
      for (let j = i + 1; j < p.length; j++) {
        let dx = p[j].x - p[i].x, dy = p[j].y - p[i].y;
        let d = Math.hypot(dx, dy);
        const want = (p[i].r + p[j].r) * gap;
        if (d >= want) continue;
        if (d < 1e-6) { dx = Math.cos(i * 2.4 + j); dy = Math.sin(i * 2.4 + j); d = 1; }
        const push = (want - d) / 2 / d;
        p[i].x -= dx * push; p[i].y -= dy * push; p[j].x += dx * push; p[j].y += dy * push;
      }
    }
    for (const q of p) { q.x += (q.tx - q.x) * spring; q.y += (q.ty - q.y) * spring; }
  }
  return Object.fromEntries(p.map((q) => [q.id, [q.x, q.y]]));
}

export const upClass = (u) => (u >= 100 ? 4 : u >= 50 ? 3 : u >= 25 ? 2 : u >= 10 ? 1 : 0);
export const UP_BREAKS = ['under $10', '$10–25', '$25–50', '$50–100', '$100 and over'];
/** Pin diameter in px at a 760 px map: area-true (the diameter grows with the square root of the value). */
export const pinPx = (v, vmax) => 7 + 27 * Math.sqrt(Math.max(0, v) / vmax);

/** "2027-11-30" -> "30 Nov 2027" (anything else as it is). */
export function shortDate(iso) {
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(String(iso || ''));
  return m ? `${Number(m[3])} ${['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'][Number(m[2]) - 1]} ${m[1]}` : String(iso || '');
}

export const shortName = (n) => String(n).replace(/ \d+(\/\d+)? kV.*$/, '').replace(/ \(Houston\)$/, '');

// ---------------------------------------------------------------------------------------------------- pure data helpers
/** The 12 pockets: each constraint line with the sites behind it, best first. */
export function pockets(doc) {
  return doc.lines.map((l) => ({ line: l, sites: l.sites.map((s) => doc.sites.find((x) => x.sub === s)).sort((a, b) => b.v2A5 - a.v2A5) }));
}
export const siteOf = (doc, sub) => doc.sites.find((s) => s.sub === sub) || doc.sites[0];
export const lineOf = (doc, s) => doc.lines.find((l) => l.key === s.el);
const zoneValue = (s) => s.v2A5 - s.up;

/** Sites with a hop count to their dominant constraint: [{s, hops, sf}] for the scatter (x hops, y |SF|). Pure (tested). */
export function scatterPoints(doc) {
  return doc.sites.filter((s) => s.cons[0] && fin(s.cons[0].hops) && fin(s.cons[0].sf)).map((s) => ({ s, hops: s.cons[0].hops, sf: s.cons[0].sf }));
}

/** For each pocket with sites on both ends, its best import-side and best export-side site. Pure (tested). */
export function sidePairs(doc) {
  return pockets(doc).map((p) => ({ line: p.line, imp: p.sites.find((s) => s.side === 'import'), exp: p.sites.find((s) => s.side === 'export') }))
    .filter((p) => p.imp && p.exp).sort((a, b) => b.imp.v2A5 - a.imp.v2A5);
}

/** The near-and-wrong-sided example: an export-side site at the end of its line (0 hops) next to the import-end site of the same line. */
export function wrongSided(doc) {
  const pr = sidePairs(doc).filter((p) => p.exp.cons[0].hops === 0 && p.imp.cons[0].hops === 0);
  return pr.sort((a, b) => b.exp.v2A5 - a.exp.v2A5)[0] || null;
}

/** One upgrade scenario ready to draw: every row with its delta, the visible rows (moved by more than $1, plus the top five), and the
 *  sums computed from the rows (checked in the tests against the file's own total). Pure (tested). */
export function scenarioView(doc, id) {
  const sc = doc.rerank.scenarios.find((x) => x.id === id) || doc.rerank.scenarios[0];
  const rows = sc.sites.map((r) => ({ ...r, delta: +(r.after - r.before).toFixed(4) }));
  const before = rows.reduce((a, r) => a + r.before, 0), after = rows.reduce((a, r) => a + r.after, 0);
  const losers = rows.filter((r) => r.delta < -1), gainers = rows.filter((r) => r.delta > 1);
  const byBefore = (a, b) => b.before - a.before || a.rb - b.rb;
  const moved = rows.filter((r) => Math.abs(r.delta) > 1);
  const top5 = rows.filter((r) => r.rb <= 5);
  const biggest10 = moved.slice().sort((a, b) => Math.abs(b.delta) - Math.abs(a.delta)).slice(0, 10);
  const union = (a, b) => [...new Set([...a, ...b])].sort(byBefore);
  const shown = union(biggest10, top5), shownAll = union(moved, top5);
  return { sc, rows, before, after, change: after - before, pct: before ? (after - before) / before * 100 : 0, losers, gainers, shown, shownAll, moved,
    biggest: rows.slice().sort((a, b) => a.delta - b.delta)[0] };
}
export const scenarioAxisMax = (doc) => {
  const m = Math.max(...doc.rerank.scenarios.flatMap((s) => s.sites.map((r) => Math.max(r.before, r.after))));
  return Math.ceil(m / 50) * 50;
};

/** The saturation fade as shares of each site's 2025 gain: the 40-site mean (every year) and individual sites (2025, 2027, 2029, 2031). */
export function fadeLines(doc) {
  const f = doc.fade;
  const mean = f.years.map((y, i) => ({ year: y, v: f.meanUp[i] / f.meanUp[0] }));
  const at = [2025, 2027, 2029, 2031], keys = ['f25', 'f27', 'f29', 'f31'];
  const site = (s) => ({ s, pts: keys.map((k, i) => ({ year: at[i], v: s[k] / s.f25 })) });
  const tb = doc.sites.filter((s) => s.el === 'HARGRO_TWINBU1_1').map(site);
  const houston = doc.sites.filter((s) => s.el === 'DT_PK_91_A').map(site);
  return { mean, tb, houston };
}

/** Regional picks aggregated by region and side (DERIVED means of the bulk columns). Pure (tested). */
export function pickGroups(doc) {
  const keys = [];
  for (const p of doc.picks) { const k = `${p.region}|${p.side}`; if (!keys.includes(k)) keys.push(k); }
  return keys.map((k) => {
    const rows = doc.picks.filter((p) => `${p.region}|${p.side}` === k);
    const obs = rows.filter((p) => fin(p.dam));
    return { region: rows[0].region, side: rows[0].side, n: rows.length, gain: avg(rows.map((p) => p.gain)), dam: obs.length ? avg(obs.map((p) => p.dam)) : null,
      above: rows.filter((p) => p.gain > 1).length, far: rows.filter((p) => p.cls === 'beyond_150km').length };
  });
}

/** Sites against reliability need (two axes, two labels, never added). */
export function reliabilityPoints(doc) {
  return doc.sites.filter((s) => fin(s.rel)).map((s) => ({ s, value: s.v2A5, need: s.rel, top100: fin(s.relRank) && s.relRank <= 100 }));
}

/** Battery-against-wires bars for U01..U10 on a log axis (0.3x to 100x). */
export function wiresBars(doc) {
  const lo = Math.log10(0.3), hi = Math.log10(100);
  const L = (r) => (Math.log10(r) - lo) / (hi - lo) * 100;
  return { one: L(1), ticks: [0.5, 1, 2, 5, 10, 20, 50].map((v) => ({ v, left: L(v) })),
    rows: doc.rerank.scenarios.filter((s) => /^U\d\d$/.test(s.id) && fin(s.ratio)).map((s) => ({ s, left: 0, width: Math.max(0.8, L(s.ratio)) })) };
}

// ---------------------------------------------------------------------------------------------------- the computed sentences
const sMoney = (num, x) => `${x.v > 0 ? '+' : ''}${num(x, MONEY)}`;
const sgn = (num, x) => num(x, { digits: 2, signed: true });

/** q=1: five sentences, each number tagged. `num` = ctx.num. Pure apart from `num` (tested). */
export function readsWhere(doc, num) {
  const h = doc.headline, sites = doc.sites, top = sites[0], S = (k, v) => fromSeries(doc, `site.${k}`, v);
  const pk = pockets(doc).find((p) => p.line.key === top.el);
  const zv = derived(+zoneValue(top).toFixed(1), `${top.sub} learner value minus its gain over the zone`);
  const mult = derived(+(top.v2A5 / zoneValue(top)).toFixed(1), `${top.sub} value ÷ the same battery at its zone price`);
  const top3 = sites.slice(0, 3).map((s) => s.sub);
  const allOnLine = top3.every((sub) => pk.sites.some((x) => x.sub === sub));
  const imp = h.gainImport, exp = h.gainExport;
  const lift = derived(+(doc.portfolio.rules[4].capped.v - doc.portfolio.rules[4].zonesOnly.v).toFixed(1), 'learner rule with the caps minus the learner rule at the zones only (1,000 MW fleet)');
  const hopsTop = top.cons[0].hops;
  return [
    { lead: 'The best site.', html: `${esc(top.sub)} (${esc(top.county)} County) earns ${num(S('v2A5', top.v2A5), MONEY)} a kW-year for a 2 h battery: ${num(mult, X1)} the ${num(zv, MONEY)} the same battery earns at its zone price.` },
    { lead: 'Siting beats the zone.', html: `At the ${num(h.sites)} candidate nodes the learner averages ${num(h.nodesMean, MONEY)} against ${num(h.zonesMean, MONEY)} at the ${num(h.zones)} zone prices (2021–25); ${num(h.beatZone)} of them beat their zone. In 2025 the gap was ${num(h.nodes25, MONEY)} against ${num(h.zones25, MONEY)}.` },
    { lead: 'It sits in a few pockets.', html: `The ${num(h.sites)} sites sit behind ${num(h.constraints)} constraints. ${esc(shortName(pk.line.name))} holds ${num(derived(pk.sites.length, 'sites behind the line of the best site'))} of them${allOnLine ? ', including the top three' : ''}${hopsTop != null ? `, and ${esc(top.sub)} is ${num(S('cons.hops', hopsTop))} hops from the line` : ''}: the value belongs to the constraint, not to a substation.` },
    { lead: 'The side matters.', html: `The ${num(h.nImport)} import-side sites gain ${sMoney(num, imp)} over their zone on average and the ${num(h.nExport)} export-side sites ${sMoney(num, exp)}: at the export end the battery charges cheaply, but its injection loads the line.` },
    { lead: 'It is a first-10-MW result.', html: `The nodes behind one constraint share its congestion, so each pocket is capped at ${num(constant(doc, 'PRICE_TAKER_SHARE'), PCT0)} of its limit in whole ${num(constant(doc, 'UNIT_MW'), MW0)} units. That leaves room for ${num(h.roomMw, MW0)} of a ${num(h.fleetMw, MW0)} fleet; with the caps a siting rule lifts the fleet average ${sMoney(num, lift)} (${num(doc.portfolio.rules[4].zonesOnly, MONEY)} to ${num(doc.portfolio.rules[4].capped, MONEY)}). A best-site ranking that ignores the caps promises ${num(doc.portfolio.rules[4].uncapped, MONEY)}.` },
  ];
}

/** q=2: five sentences. */
export function readsWhy(doc, num) {
  const st = doc.hopsSf.stats, S = (k, v) => fromSeries(doc, `site.${k}`, v), C = (n) => constant(doc, n);
  const sv = (sub) => siteOf(doc, sub), mid = sv('MIDW'), pow = sv('POWELLFI');
  const ws = wrongSided(doc), bg = sv('BG'), pk = sv('PK');
  const A = doc.algorithms.algos, a2 = A.find((a) => a.id === 'A2'), a5 = A.find((a) => a.id === 'A5');
  const cons = (s) => s.cons[0];
  const out = [
    { lead: 'Far can be strong.', html: `${esc(mid.sub)} is ${num(S('cons.hops', cons(mid).hops))} hops from ${esc(shortName(lineOf(doc, mid).name))} yet has the largest shift factor in the study (${num(S('cons.sf', cons(mid).sf), D2)}), and earns ${num(S('v2A5', mid.v2A5), MONEY)}. ${esc(pow.sub)} is ${num(S('cons.hops', cons(pow).hops))} hops away with ${num(S('cons.sf', cons(pow).sf), D2)}.` },
    { lead: 'Hops explain little.', html: `Across ${num(st.pairs)} substation–line pairs the rank correlation of hops with the size of the shift factor is only ${num(st.rho, D2)}. Of the ${num(st.strong)} pairs with a shift factor of ${num(C('STRONG_SF'), D2)} or more, ${num(st.strongFarShare, PCT0)} are ${num(C('FAR_HOPS'))} or more hops from the line.` },
  ];
  if (ws) {
    out.push({ lead: 'Near can be wrong-sided.', html: `${esc(ws.exp.sub)} sits at the end of ${esc(shortName(ws.line.name))} (${num(S('cons.hops', ws.exp.cons[0].hops))} hops) but on the export side (${num(S('cons.sf', ws.exp.cons[0].sf), { digits: 2, signed: true })}). It earns ${num(S('v2A5', ws.exp.v2A5), MONEY)} against ${num(S('v2A5', ws.imp.v2A5), MONEY)} at ${esc(ws.imp.sub)}, the other end.` });
  }
  out.push({ lead: 'On a mesh, hops mislead.', html: `On the Houston PK–DT line ${esc(bg.sub)} is ${num(S('cons.hops', cons(bg).hops))} hops from the line and ${esc(pk.sub)} is ${num(S('cons.hops', cons(pk).hops))}, with the same shift factor (${num(S('cons.sf', cons(bg).sf), D2)}); they earn ${num(S('v2A5', bg.v2A5), MONEY)} and ${num(S('v2A5', pk.v2A5), MONEY)}. Where the grid is a chain, hops are a fair proxy; where it is meshed they are not.` });
  out.push({ lead: 'At a node the Forecaster wins.', html: `The Forecaster earns more than the learner at ${num(a2.bestNodes)} of the ${num(doc.headline.sites)} nodes (${num(a2.nodes, MONEY)} against ${num(a5.nodes, MONEY)}), while at the zones the learner wins ${num(a5.bestZones)} of ${num(doc.headline.zones)}. The node prices are rebuilt from hub spreads the Forecaster can read, so this needs a check against real nodal prices.` });
  return out;
}

/** q=3: the sentence for the chosen upgrade, then three that hold for every upgrade. */
export function readsMoves(doc, id, num) {
  const v = scenarioView(doc, id), sc = v.sc, W = doc.rerank.wires, f = doc.fade, C = (n) => constant(doc, n);
  const R = (x) => fromSeries(doc, 'rerank.rank', x), V = (x) => fromSeries(doc, 'rerank.value', x);
  const planned = /^PLANNED/.test(sc.id);
  const sum = (x, w) => derived(+x.toFixed(1), w);
  let first;
  if (sc.effect !== 'evaluated') {
    first = { lead: `${esc(sc.id)} moves nothing.`, html: `${esc(shortName(sc.name))} did not bind in the summer 2026 prices (${num(C('DAM_DAYS'))} delivery days of measured day-ahead prices), so there is no shift factor to replay and no site changes. It is listed, not valued.` };
  } else if (!v.losers.length && !v.gainers.length) {
    first = { lead: `${esc(sc.id)} moves no site.`, html: `${esc(shortName(sc.name))} changes the sum of the ${num(doc.headline.sites)} sites from ${num(sum(v.before, 'sum of the before values'), MONEY)} to ${num(sum(v.after, 'sum of the after values'), MONEY)}, under ${num(C('MOVED_USD'), MONEY0)} for any one site.` };
  } else {
    const b = v.biggest, verb = sc.kind === 'reconductor' ? 'Reconductoring' : sc.kind === 'rebuild' ? 'Rebuilding' : 'Upgrading';
    const cost = fin(sc.cost) ? ` (about ${num(fromSeries(doc, 'rerank.cost', sc.cost), { money: true, digits: 1, unit: 'M' })}, ${num(fromSeries(doc, 'rerank.lead', sc.lead), { digits: 0, unit: ' months' })})` : '';
    first = { lead: planned ? 'The planned wires.' : `${esc(sc.id)}.`, html: `${planned ? esc(sc.name) : `${verb} ${esc(shortName(sc.name))}`}${cost} moves the sum of the ${num(doc.headline.sites)} sites from ${num(sum(v.before, 'sum of the before values'), MONEY)} to ${num(sum(v.after, 'sum of the after values'), MONEY)} (${num(derived(+v.pct.toFixed(1), 'after ÷ before − 1'), { digits: 1, unit: '%', signed: true })}). ${num(derived(v.losers.length, 'sites that lose more than the moved threshold'))} sites lose and ${num(derived(v.gainers.length, 'sites that gain more than the moved threshold'))} gain; the largest change is ${esc(b.sub)}, from ${num(V(b.before), MONEY)} to ${num(V(b.after), MONEY)} (rank ${num(R(b.rb))} to ${num(R(b.ra))}).` };
  }
  const half = f.halfLife, last = f.storageGw.length - 1;
  return [
    first,
    { lead: 'Battery or wires.', html: `A battery that relieves ${num(C('RELIEF_SHARE'), PCT0)} of a line's limit in ${num(C('RELIEF_HOURS'), PCT0)} of its binding hours costs a median ${num(W.medianWires, X0)} the wires fix and ${num(W.medianCheapest, X0)} the cheapest rating change (${num(W.n)} elements sized). Only the WESTEX interface is below ${num(derived(1, 'a ratio of 1 is equal cost'), X0)}, and that is a sizing exercise.` },
    { lead: 'Crowding fades the premium.', html: `With ${num(fromSeries(doc, 'fade.storageGw', f.storageGw[last]), { digits: 0, unit: ' GW' })} of effective storage online by 2031, the mean gain over the zone falls from ${num(fromSeries(doc, 'fade.meanUp', f.meanUp[0]), MONEY)} (2025 replay) to ${num(fromSeries(doc, 'fade.meanUp', f.meanUp[4]), MONEY)} by 2029. ${num(half['2028'])} sites lose half of their 2025 gain by 2028, ${num(half['2029'])} by 2029, and ${num(half['> 2031'])} not by 2031.` },
    { lead: 'Who can act.', html: `Only the transmission owner can make an upgrade happen. A battery owner can propose one and pay its own connection, not order a line. The Twin Buttes decline rests on ${num(C('SAT_SUBS'))} substations with a shift factor of about ${num(C('SAT_SF'), { digits: 3 })}, the most fragile result here.` },
  ];
}

/** q=4: six sentences. */
export function readsBeyond(doc, num) {
  const R = Object.fromEntries(doc.regions.map((r) => [r.name, r])), Rg = (key, v) => fromSeries(doc, `region.${key}`, v);
  const val = R['Rio Grande Valley'], pan = R.Panhandle, fw = R['Far West'];
  const pm = avg(doc.picks.map((p) => p.gain)), best = doc.picks.slice().sort((a, b) => b.gain - a.gain)[0];
  const t = doc.tier2.groups, all = t[0], within = t[1];
  const risk = doc.risk, sites = doc.sites, C = (n) => constant(doc, n);
  const unmapped = sites.filter((s) => s.flood === 'unmapped').length;
  const dm = { money: true, digits: 2 };
  return [
    { lead: 'The Valley is a discount.', html: `The Rio Grande Valley's summer congestion is an export discount, not an import premium: ${num(Rg('neg10', val.neg10), PCT1)} of node-hours trade more than ${num(C('BASIS_USD'), MONEY0)} below the zone and ${num(Rg('pos10', val.pos10), PCT1)} above. A battery there shows no uplift over its zone (${num(Rg('uplift', val.uplift), dm)} a kW over ${num(C('DAM_DAYS'))} days).` },
    { lead: 'Panhandle and Far West.', html: `The Panhandle has the same shape (${num(Rg('neg10', pan.neg10), PCT1)} below, ${num(Rg('pos10', pan.pos10), PCT1)} above; ${num(Rg('uplift', pan.uplift), dm)} a kW). The Far West has no premium against its own zone (${num(Rg('mean', fw.mean), dm)} a MWh on average), because the zone is mostly Far West.` },
    { lead: 'No second pocket.', html: `The ${num(derived(doc.picks.length, 'regional picks in data/extra_sites.csv'))} regional picks average a modelled gain of ${sMoney(num, derived(+pm.toFixed(1), 'mean of the picks’ 2 h gain over the zone'))} against ${sMoney(num, doc.headline.gainMean)} for the ${num(doc.headline.sites)}; the best, ${esc(best.sub)}, is ${sMoney(num, fromSeries(doc, 'pick.gain', best.gain))}${fin(best.km) ? ` and rests on a line ${num(fromSeries(doc, 'pick.km', best.km), D0)} km away` : ''}.` },
    { lead: 'Risk excludes few.', html: `${num(risk.excluded)} of ${num(risk.assessed)} assessed substations are excluded by physical risk (${num(risk.flood)} in a flood hazard area, ${num(risk.floodway)} in a floodway, ${num(risk.wildfire)} at very high wildfire hazard). None of the ${num(doc.headline.sites)} sites is excluded, but ${num(derived(unmapped, 'sites with no digital flood map'))} of them have no flood map at all: unmapped is not low risk.` },
    { lead: 'Reliability is a separate column.', html: `Only ${num(doc.reliability.inTop100)} of the ${num(doc.headline.sites)} sites are among the ${num(C('RELIABILITY_TOP'))} substations with the most need, the Houston PK–DT sites. Twin Buttes is a market case only. Reliability value is in its own units and is never added to market value.` },
    { lead: 'Distribution batteries.', html: `A distribution battery registered as a DESR is paid the node price and gains ${sMoney(num, all.gain)} over its zone at the ${num(all.n)} valued candidates (${sMoney(num, within.gain)} within ${num(C('FAR_KM'), { digits: 0, unit: ' km' })} of its line), falling to ${sMoney(num, all.gain29)} by 2029. A home-battery fleet in an ADER is paid the zone price and gets none of it.` },
  ];
}

/** The selected-site panel as HTML: every number tagged (numHTML throws on a bare one). Pure (tested for all 40 sites). */
export function sitePanel(doc, s, num) {
  const S = (k, v) => fromSeries(doc, k, v);
  const ln = lineOf(doc, s), c0 = s.cons[0], zv = zoneValue(s);
  const mult = derived(+(s.v2A5 / zv).toFixed(1), `${s.sub} value ÷ the same battery at its zone price`);
  const upg = s.upProj ? `A fix is on file, <b class="si-id">${esc(s.upProj)}</b>${s.upDate ? ` (in service ${num(S('site.upConf', shortDate(s.upDate)))})` : ' with no in-service date'}, matched with confidence ${num(S('site.upConf', s.upConf), D2)}.` : 'No project on file for this element.';
  const fwd = [['2025 replay', s.f25], ['2027', s.f27], ['2029', s.f29], ['2031', s.f31]].map(([y, v]) => `<div><span class="si-sub">${y}</span><span class="si-fwv">${sMoney(num, S('site.f25', v))}</span></div>`).join('');
  const risk = [];
  risk.push(s.flood === 'unmapped' ? 'flood: no digital map (unmapped is not low risk)' : `flood: ${esc(s.flood)}`);
  risk.push(`wildfire: ${esc(String(s.fire).replace('_', ' '))}`);
  if (s.quake) risk.push('seismic flag');
  if (s.coast) risk.push('coastal');
  risk.push(s.excl ? 'excluded' : 'not excluded');
  const lead = s.kv >= 138 ? 'a large power transformer' : 'a medium-size step-up transformer';
  const others = s.cons.slice(1).map((c) => `<div class="si-krow"><span>Also: <span class="si-id">${esc(c.el)}</span></span><span>${sideWord(c.side)} ${num(S('site.cons.sf', c.sf), { digits: 2, signed: true })}</span></div>`).join('');
  const h4 = fin(s.v4) ? `<span class="si-sub">4 h, 2024–25: ${num(S('site.v4', s.v4), MONEY)}</span>` : '<span class="si-sub">4 h not run for this site</span>';
  const halfWords = s.half === '> 2031' ? 'holds past 2031' : `halved by ${esc(s.half)}`;
  return `<section class="si-card si-panel" aria-label="Selected site: ${esc(s.sub)}" aria-live="polite">
    <div class="si-ph"><div><span class="si-pname">${esc(s.sub)}</span><span class="si-psub">${num(S('site.kv', s.kv), { unit: ' kV' })} · ${esc(s.county)} County · ${esc(s.zone)}</span></div><span class="si-prank">#${s.rank} of ${num(doc.headline.sites)}</span></div>
    <div class="si-pval"><span class="si-big">${num(S('site.v2A5', s.v2A5), MONEY)}</span><span class="si-sub">a kW-year · 2 h learner, 2021–25</span></div>
    <div class="si-pvals"><span class="si-sub">2025: ${num(S('site.y25', s.y25), MONEY)}</span>${h4}</div>
    <div class="si-pgain"><div><span class="si-sub">Gain over its zone</span><b>${sMoney(num, S('site.up', s.up))}</b></div><div><span class="si-sub">Against its zone price</span><b>${num(mult, X1)}</b></div>
      <div><span class="si-sub">Gain, as the grid fills</span><b>${halfWords}</b></div></div>
    <div class="si-psec"><span class="si-sub">GAIN OVER THE ZONE, $/KW-YR, AS STORAGE ARRIVES</span><div class="si-fwd">${fwd}</div></div>
    <div class="si-psec"><span class="si-sub">CONSTRAINT BEHIND IT</span>
      <div class="si-krow strong"><span><span class="si-id">${esc(ln.name)}</span></span><span>${sideWord(s.side)}</span></div>
      <div class="si-krow"><span>Shift factor</span><span>${num(S('site.cons.sf', c0.sf), { digits: 2, signed: true })}</span></div>
      <div class="si-krow"><span>Hops from the line</span><span>${fin(c0.hops) ? num(S('site.cons.hops', c0.hops)) : 'not on the graph'}</span></div>
      <div class="si-krow"><span>Price-taker room, first units</span><span>${num(S('site.cap', s.cap), MW0)}</span></div>
      <div class="si-krow"><span>Placement confidence</span><span>${num(S('site.conf', s.conf), D2)}</span></div>
      ${others}<div class="si-note">${upg}${s.conf < 0.6 ? ' The position of this substation is an inference: its dollar values do not depend on it, but its county and risk flags do.' : ''}${c0.curtail ? ' Export side: injecting here loads the line, so discharge is limited when it binds.' : ''}</div></div>
    <div class="si-psec"><span class="si-sub">CONNECTION</span>
      <div class="si-krow"><span>${esc(String(s.conn).replace('_', ' '))}${s.connKm ? ` · gen-tie ${num(S('site.connKm', s.connKm), { digits: 1, unit: ' km' })}` : ''}</span><span>${num(S('site.lead', s.lead), { digits: 1, unit: ' months' })}</span></div>
      <div class="si-krow"><span>All-in cost, with ${lead}</span><span>${num(S('site.connKw', s.connKw), { money: true, digits: 0, unit: '/kW' })}</span></div>
      <div class="si-krow"><span>Value net of connection, a year</span><span>${num(S('site.net', s.net), MONEY)}</span></div></div>
    <div class="si-psec"><span class="si-sub">RISK FLAGS AND RELIABILITY, KEPT SEPARATE</span><div class="si-note">${risk.join(' · ')}</div>
      <div class="si-krow"><span>Reliability need, own units</span><span>${fin(s.relRank) ? `rank ${num(S('site.relRank', s.relRank))}, ` : 'no rank, '}score ${num(S('site.rel', s.rel), D1)}</span></div></div>
  </section>`;
}

const sideWord = (s) => `<span class="si-side ${s}">${s}</span>`;

// ---------------------------------------------------------------------------------------------------- the DOM half
function ensureCss() {
  if (typeof document === 'undefined') return Promise.resolve();
  if (document.querySelector('link[data-siting-css]')) return Promise.resolve();
  return new Promise((resolve) => {
    const l = document.createElement('link');
    l.rel = 'stylesheet';
    l.href = new URL('./siting.css', import.meta.url).href;
    l.dataset.sitingCss = '1';
    l.onload = l.onerror = () => resolve();
    document.head.appendChild(l);
  });
}
function loadState() { try { return JSON.parse(localStorage.getItem(STORE) || 'null') || {}; } catch (e) { return {}; } }
function saveState(s) { try { localStorage.setItem(STORE, JSON.stringify(s)); } catch (e) { /* private mode */ } }

export async function mount(root, ctx) {
  const num = (x, o) => ctx.num(x, o);
  const saved = loadState();
  const st = { view: viewOf(ctx.params && ctx.params.q), site: saved.site || null, scen: saved.scen || 'U03', pick: null, all: false };
  root.innerHTML = '<div class="si"><div class="si-loading">Loading the siting study…</div></div>';
  let doc, tx;
  try {
    [doc, tx] = await Promise.all([ctx.getJSON(DATA), ctx.getJSON(TEXAS), ensureCss()]);
  } catch (e) {
    root.innerHTML = `<div class="si"><div class="st-placeholder"><div class="st-eyebrow">SITING</div><div class="st-ph-title">The siting study is not built yet</div>
      <p>ui/data/${DATA} did not load. Build it with <code>scripts/export_siting.py</code> (it reads the study in docs/research/grid/).</p></div></div>`;
    return { dispose() {} };
  }
  if (!doc.sites.some((s) => s.sub === st.site)) st.site = doc.sites[0].sub;
  st.pick = doc.picks.slice().sort((a, b) => b.gain - a.gain)[0].sub;      // the pick with the best modelled gain opens as the example
  if (!doc.rerank.scenarios.some((s) => s.id === st.scen)) st.scen = 'U03';

  const el = document.createElement('div');
  el.className = 'si';
  root.replaceChildren(el);
  const proj = projection(tx.outline);
  const sTag = (key) => { const s = doc.series[key]; return tagHTML(s.label, s.text); };
  const S = (key, v) => fromSeries(doc, key, v);
  const fmtV = (v, d = 1) => (fin(v) ? Number(v).toLocaleString('en-US', { minimumFractionDigits: d, maximumFractionDigits: d }).replace('-', MINUS) : '—');
  const sf2 = (v) => (fin(v) ? (v > 0 ? '+' : v < 0 ? MINUS : '') + Math.abs(v).toFixed(2) : '—');
  const gain = (v) => (fin(v) ? `${v < 0 ? MINUS : '+'}$${Math.abs(v).toFixed(1)}` : '—');
  const vmax = Math.max(...doc.sites.map((s) => s.v2A5));
  const cur = () => siteOf(doc, st.site);
  const caveat = () => `<div class="si-caveat" role="note"><b>Node prices are a model</b> ${tagHTML('SIM', doc.sources.study.text)} The price at a substation is rebuilt from the real zone price plus a replayed congestion structure, so levels are good to about ${num(constant(doc, 'LEVEL_FACTOR'), { digits: 0, unit: '×' })} and rankings are steadier. There is no ERCOT API key: a key would replace the rebuilt prices at the top sites with real ones.</div>`;

  // ---------------------------------------------------------------- shared pieces
  function railHTML() {
    const views = VIEWS.map((v) => `<button type="button" class="si-view${v.q === st.view ? ' on' : ''}" data-q="${v.q}" aria-pressed="${v.q === st.view}">
      <span class="si-qn">${v.q}</span><span><span class="si-qt">${esc(v.title)}</span><span class="si-qs">${esc(v.sub)}</span></span></button>`).join('');
    const ups = st.view !== 3 ? '' : `<div class="si-rail-h st-eyebrow sm">UPGRADE TO TEST</div><div class="si-ups" role="group" aria-label="Upgrade">${doc.rerank.scenarios.map((s) => {
      const planned = /^PLANNED/.test(s.id);
      return `<button type="button" class="si-up${s.id === st.scen ? ' on' : ''}" data-scen="${s.id}" aria-pressed="${s.id === st.scen}">
        <span class="si-up-t"><span class="si-up-id">${planned ? 'Planned' : esc(s.id)}</span><span>${esc(planned ? (s.id === 'PLANNED_2031' ? 'in service by 2031' : 'all dated, to 2032+') : shortName(s.name))}</span></span>
        ${s.effect !== 'evaluated' ? '<span class="si-up-s">did not bind in summer 2026</span>' : planned ? '<span class="si-up-s">bundle of projects</span>' : s.kind !== 'reconductor' ? `<span class="si-up-s">${esc(KIND_WORDS[s.kind] || s.kind)}</span>` : ''}</button>`;
    }).join('')}</div>`;
    return `<aside class="si-rail"><div class="st-eyebrow">SITING</div>
      <div class="si-q">Where on the physical grid should a battery sit?</div>
      <nav class="si-views" aria-label="Views">${views}</nav>${ups}
      <div class="si-rail-note" role="note"><b>Node prices are SIM</b> ${tagHTML('SIM', doc.sources.study.text)}: levels good to about ${num(constant(doc, 'LEVEL_FACTOR'), { digits: 0, unit: '×' })}, rankings steadier. No ERCOT API key.
        Market value and reliability value are never added. Every site, rank and number comes from the study’s deterministic code, not from a language model.</div></aside>`;
  }

  function headHTML(eyebrow, title, answer) {
    return `<div class="si-head"><div><div class="st-eyebrow">${esc(eyebrow)}</div><div class="st-title si-h1">${esc(title)}</div>
        <div class="si-answer">${answer}</div></div>
      </div>${caveat()}`;
  }

  const readsHTML = (reads, label) => `<section class="si-card si-reads" aria-label="${esc(label)}"><span class="si-ct">${esc(label)}</span>
      <ol class="si-readlist">${reads.map((r, i) => `<li><span class="si-rn num">0${i + 1}</span><span><b>${esc(r.lead)}</b> ${r.html}</span></li>`).join('')}</ol></section>`;
  const kpiHTML = (items) => `<section class="si-kpis" aria-label="Key numbers">${items.map(([k, v, s]) => `<div class="si-card si-kpi"><span class="si-sub">${esc(k)}</span><span class="si-kpi-v">${v}</span><span class="si-note">${s}</span></div>`).join('')}</section>`;

  // ---------------------------------------------------------------- the map
  function pinLayout(items) {
    return declutter(items.map((q) => ({ id: q.id, x: q.x, y: q.y, r: q.d * PX_TO_UNITS / 2 })));
  }
  // where each named pocket is labelled, and on which side of its best nodes; a pocket with one or two nodes is left to the list and the table
  const POCKET_LABEL = { HARGRO_TWINBU1_1: ['Twin Buttes', 'l'], '6437__F': ['Scurry–Knapp', 'l'], LPLMK_LPLNE_1: ['Lubbock', 't'], FORTMA_YELWJC1_1: ['Fort Mason', 'b'],
    COLLE_JUPIT_1: ['College–Jupiter', 'r'], '587__A': ['Argyle', 't'], '215T215_1': ['HIGH36–Brenno', 'b'], DT_PK_91_A: ['Houston PK–DT', 'b'] };
  const REGION_SIDE = { 'Rio Grande Valley': 'l', Panhandle: 'b', 'Far West': 'l', 'Laredo/South': 'l', 'Bell County': 'r' };

  function mapHTML({ mode, legend }) {
    const lines = doc.lines.filter((l) => l.kind !== 'unplaced').map((l) => {
      if (l.kind === 'transformer') { const [x, y] = proj.xy(l.pts[0][1], l.pts[0][0]); return `<rect x="${(x - 4).toFixed(1)}" y="${(y - 4).toFixed(1)}" width="8" height="8" class="si-xf"></rect>`; }
      const [a, b] = l.pts.map((p) => proj.xy(p[1], p[0]));
      const end = (pt, side) => `<circle cx="${pt[0].toFixed(1)}" cy="${pt[1].toFixed(1)}" r="3.2" class="si-end ${side === 'import' ? 'imp' : 'exp'}"></circle>`;
      return `<path d="M${a.map((v) => v.toFixed(1)).join(' ')}L${b.map((v) => v.toFixed(1)).join(' ')}" class="si-line"></path>${end(a, l.ends[0])}${end(b, l.ends[1])}`;
    }).join('');
    let pins = '', leaders = '', labels = '', near = [];
    if (mode === 'sites') {
      const items = doc.sites.map((s) => { const [x, y] = proj.xy(s.lon, s.lat); return { id: s.sub, x, y, d: pinPx(s.v2A5, vmax) }; });
      const pos = pinLayout(items);
      near = items.map((q) => pos[q.id]);
      for (const q of items) {
        const [px, py] = pos[q.id];
        if (Math.hypot(px - q.x, py - q.y) > 3) leaders += `<path d="M${q.x.toFixed(1)} ${q.y.toFixed(1)}L${px.toFixed(1)} ${py.toFixed(1)}" class="si-leader"></path>`;
        leaders += `<circle cx="${q.x.toFixed(1)}" cy="${q.y.toFixed(1)}" r="1.8" class="si-true"></circle>`;
      }
      const ordered = doc.sites.slice().sort((a, b) => (a.sub === st.site) - (b.sub === st.site) || b.v2A5 - a.v2A5);
      pins = ordered.map((s) => {
        const q = items.find((i) => i.id === s.sub), [px, py] = pos[s.sub];
        const d = q.d / 760 * 100;
        const on = s.sub === st.site;
        return `<button type="button" tabindex="-1" class="si-pin u${upClass(s.up)}${s.side === 'export' ? ' exp' : ''}${on ? ' on' : ''}" data-site="${esc(s.sub)}" aria-pressed="${on}"
          style="left:${(px / proj.W * 100).toFixed(2)}%;top:${(py / proj.H * 100).toFixed(2)}%;width:${d.toFixed(2)}%"
          data-tip="${esc(`${s.sub}: ${fmtV(s.v2A5)} $/kW-yr, ${gain(s.up)} over its zone, ${s.side} side`)}"
          aria-label="${esc(`${s.sub}, number ${s.rank} of ${doc.sites.length}, ${fmtV(s.v2A5)} dollars per kilowatt-year, ${s.side} side`)}"></button>`;
      }).join('');
      labels = pockets(doc).filter((p) => POCKET_LABEL[p.line.key]).map((p) => {
        const take = p.sites.slice(0, Math.max(1, Math.ceil(p.sites.length / 2)));        // the best nodes: a far outlier does not drag the label
        const cx = avg(take.map((s) => pos[s.sub][0])), cy = avg(take.map((s) => pos[s.sub][1]));
        const [text, side] = POCKET_LABEL[p.line.key];
        return `<span class="si-plab" data-s="${side}" style="left:${(cx / proj.W * 100).toFixed(2)}%;top:${(cy / proj.H * 100).toFixed(2)}%">${esc(text)}</span>`;
      }).join('');
    } else {
      const ctxItems = doc.sites.map((s) => { const [x, y] = proj.xy(s.lon, s.lat); return { x, y }; });
      leaders = ctxItems.map((q) => `<circle cx="${q.x.toFixed(1)}" cy="${q.y.toFixed(1)}" r="3.4" class="si-ctxdot"></circle>`).join('');
      const items = doc.picks.map((p) => { const [x, y] = proj.xy(p.lon, p.lat); return { id: p.sub, x, y, d: 11 + Math.min(14, Math.abs(p.gain) * 1.3) }; });
      const pos = pinLayout(items);
      near = items.map((q) => pos[q.id]).concat(ctxItems.map((q) => [q.x, q.y]));
      for (const q of items) { const [px, py] = pos[q.id]; if (Math.hypot(px - q.x, py - q.y) > 3) leaders += `<path d="M${q.x.toFixed(1)} ${q.y.toFixed(1)}L${px.toFixed(1)} ${py.toFixed(1)}" class="si-leader"></path>`; }
      pins = doc.picks.map((p) => {
        const q = items.find((i) => i.id === p.sub), [px, py] = pos[p.sub], on = st.pick === p.sub;
        return `<button type="button" class="si-pick${p.gain > 1 ? ' pos' : ''}${on ? ' on' : ''}" data-pick="${esc(p.sub)}" aria-pressed="${on}"
          style="left:${(px / proj.W * 100).toFixed(2)}%;top:${(py / proj.H * 100).toFixed(2)}%;width:${(q.d / 760 * 100).toFixed(2)}%"
          data-tip="${esc(`${p.sub} (${p.region}, ${p.side} side): modelled ${gain(p.gain)} a kW-year`)}" aria-label="${esc(`${p.sub}, ${p.region}, ${p.side} side, modelled gain ${gain(p.gain)} dollars per kilowatt-year`)}"></button>`;
      }).join('');
      labels = [...new Set(doc.picks.map((p) => p.region))].map((name) => {
        const pc = doc.picks.filter((p) => p.region === name).map((p) => pos[p.sub]);
        const shown = name.startsWith('Central') ? 'Bell County' : name;
        return `<span class="si-plab region" data-s="${REGION_SIDE[shown] || 'l'}" style="left:${(avg(pc.map((q) => q[0])) / proj.W * 100).toFixed(2)}%;top:${(avg(pc.map((q) => q[1])) / proj.H * 100).toFixed(2)}%">${esc(shown)}</span>`;
      }).join('');
    }
    // a city is named only where no pin sits on top of its name
    const places = doc.places.filter((p) => { const [cx, cy] = proj.xy(p.lon, p.lat); return !near.some(([x, y]) => Math.abs(x - cx) < 85 && Math.abs(y - cy) < 28); })
      .map((p) => { const q = proj.pct(p.lon, p.lat); return `<span class="si-city" style="left:${q.left.toFixed(2)}%;top:${q.top.toFixed(2)}%"><i></i>${esc(p.name)}</span>`; }).join('');
    return `<div class="si-mapwrap" style="aspect-ratio:${proj.W}/${proj.H}">
      <svg viewBox="${proj.vb}" preserveAspectRatio="none" role="img" aria-label="Map of Texas with ${mode === 'sites' ? 'the 40 candidate nodes and the constraint lines behind them' : 'the 41 regional picks and the 40 candidate nodes'}">
        <path d="${proj.path(tx.outline)}" class="si-tx"></path>${mode === 'sites' ? lines : ''}${leaders}</svg>
      ${places}${labels}${pins}</div>${legend || ''}`;
  }

  function mapLegend() {
    const sw = UP_BREAKS.map((t, i) => `<span><i class="si-sw u${i}"></i>${esc(t)}</span>`).join('');
    const sz = (v) => { const d = pinPx(v, vmax); return `<span><i class="si-szdot" style="width:${d.toFixed(0)}px;height:${d.toFixed(0)}px"></i>$${v}</span>`; };
    return `<div class="si-maplegend over"><span class="si-sub">Size: 2 h value, $/kW-yr ${sTag('site.v2A5')}</span><span class="si-szrow">${sz(50)}${sz(150)}${sz(270)}</span>
      <span class="si-sub">Shade: gain over zone, $/kW-yr ${sTag('site.up')}</span><span class="si-swrow">${sw}</span>
</div>`;
  }

  // ---------------------------------------------------------------- the site panel
  const panelHTML = (s) => sitePanel(doc, s, num);

  function listHTML() {
    return `<section class="si-card si-listcard" aria-label="Ranked sites">
      <div class="si-card-h"><span class="si-ct">The 40 sites, best first</span></div>
      <span class="si-sub">$/kW-yr of a 1 MW / 2 MWh battery, 2021–25 mean ${sTag('site.v2A5')}. Gain = value minus the same battery at its zone ${sTag('site.up')}.</span>
      <div class="si-list"><div class="si-lr si-lh" aria-hidden="true"><span>#</span><span>Site</span><span>Value</span><span>Gain</span><span>Side</span></div>
        <div class="si-lbody" role="group" aria-label="Candidate sites, best first">${doc.sites.map((s) => `<button type="button" class="si-lr${s.sub === st.site ? ' on' : ''}" data-site="${esc(s.sub)}" aria-pressed="${s.sub === st.site}"
          aria-label="${esc(`${s.rank}. ${s.sub}, ${s.county} County, ${fmtV(s.v2A5)} dollars per kilowatt-year, gain ${gain(s.up)}, ${s.side} side`)}">
          <span class="num">${s.rank}</span><span><b>${esc(s.sub)}</b><i>${esc(s.county.toLowerCase())}</i></span>
          <span class="num">${fmtV(s.v2A5)}</span><span class="num">${gain(s.up)}</span><span>${sideWord(s.side)}</span></button>`).join('')}</div></div></section>`;
  }

  // ---------------------------------------------------------------- q=1
  function portfolioHTML() {
    const R = doc.portfolio.rules, max = Math.max(...R.map((r) => r.uncapped.v)), w = (x) => (x / max * 100).toFixed(1);
    const tg = (x) => tagHTML(x.label, x.cite);
    const rows = R.map((r) => `<div class="si-fr${r.id === 'A5' ? ' lead' : ''}" role="row"><span role="cell"><b>${esc(r.id)}</b> ${esc(r.name)}</span>
      <span role="cell" class="si-fbars"><i class="z" style="width:${w(r.zonesOnly.v)}%"></i><i class="c" style="width:${w(r.capped.v)}%"></i><i class="u" style="width:${w(r.uncapped.v)}%"></i></span>
      <span role="cell" class="num">$${fmtV(r.zonesOnly.v)}</span><span role="cell" class="num">$${fmtV(r.capped.v)}</span><span role="cell" class="num">$${fmtV(r.uncapped.v)}</span><span role="cell" class="num">${r.mwNodes.v} / ${r.mwZones.v}</span></div>`).join('');
    const A = R[4];
    return `<section class="si-card" aria-label="The first 10 MW and the portfolio result"><div class="si-card-h"><span class="si-ct">It is a first-MW result: what siting adds to a fleet</span>
        <span class="si-legend"><span><i class="si-fk z"></i>every unit at its zone price</span><span><i class="si-fk c"></i>with the pocket caps</span><span><i class="si-fk u"></i>no caps: a best-site ranking</span></span></div>
      <span class="si-sub">Fleet average, $/kW-yr, for ${num(doc.headline.fleetMw, MW0)} added ${num(constant(doc, 'FLEET_ADD_MW_YR'), MW0)} a year over 2027–31. A pocket's nodes share a cap of ${num(constant(doc, 'PRICE_TAKER_SHARE'), PCT0)} of its limit, in whole ${num(constant(doc, 'UNIT_MW'), MW0)} units. The caps leave room for ${num(doc.headline.roomMw, MW0)} at the nodes; the rest falls back to the zone price. Without caps every rule piles its whole fleet onto a few nodes; the learner rule then promises ${num(derived(+(A.uncapped.v / A.capped.v).toFixed(1), 'learner rule without caps ÷ with caps'), X1)} what the caps allow.</span>
      <div class="si-table si-fleet" role="table" aria-label="Fleet average by siting rule"><div class="si-fr si-th" role="row"><span role="columnheader">Siting rule</span><span role="columnheader"></span>
        <span role="columnheader">Zones only ${tg(A.zonesOnly)}</span><span role="columnheader">With caps ${tg(A.capped)}</span><span role="columnheader">No caps ${tg(A.uncapped)}</span><span role="columnheader">MW at nodes / zones ${tg(A.mwNodes)}</span></div>${rows}</div></section>`;
  }

  function whereHTML() {
    const h = doc.headline, top = doc.sites[0], rl = doc.portfolio.rules[4];
    const lift = derived(+(rl.capped.v - rl.zonesOnly.v).toFixed(1), 'learner rule with the caps minus the learner rule at the zones only');
    const kpis = [
      ['The 40 nodes', num(h.nodesMean, MONEY), `/kW-yr, 2 h learner, 2021–25. In 2025: ${num(h.nodes25, MONEY)}.`],
      ['The same battery at the zones', num(h.zonesMean, MONEY), `/kW-yr, the eight load-zone prices. In 2025: ${num(h.zones25, MONEY)}.`],
      ['Sites beating their zone', `${num(h.beatZone)} <span class="si-of">of ${num(h.sites)}</span>`, `Mean gain ${sMoney(num, h.gainMean)} /kW-yr.`],
      ['Best site', `${esc(top.sub)}`, `${num(S('site.v2A5', top.v2A5), MONEY)} /kW-yr behind ${esc(shortName(lineOf(doc, top).name))}.`],
      ['Room at the nodes', num(h.roomMw, MW0), `of the first ${num(h.fleetMw, MW0)}: each pocket is capped.`],
      ['Fleet, learner rule', num(rl.capped, MONEY), `/kW-yr with the caps, ${sMoney(num, lift)} over the zones only.`],
    ];
    const pk = pockets(doc);
    const rows = pk.map((p) => {
      const l = p.line, best = p.sites[0], g = avg(p.sites.map((s) => s.up));
      return `<div class="si-tr" role="row"><span role="cell"><b>${esc(shortName(l.name))}</b><i>${esc(l.kv)} kV${l.kind === 'unplaced' ? ' · line ends not placed' : ''}</i></span>
        <span role="cell" class="num">${fmtV(l.limit, 0)}</span><span role="cell" class="num">${fmtV(l.rent)}</span><span role="cell" class="num">${fmtV(l.hours, 0)}</span>
        <span role="cell">${p.sites.map((s) => `<button type="button" class="si-chipbtn${s.sub === st.site ? ' on' : ''}" data-site="${esc(s.sub)}">${esc(s.sub)}</button>`).join('')}</span>
        <span role="cell" class="num">${fmtV(best.v2A5)}</span><span role="cell" class="num">${gain(g)}</span><span role="cell" class="num">${l.cap}</span></div>`;
    }).join('');
    return `${headHTML('SITING · WHERE', 'Where to put batteries', `${esc(top.sub)}, behind ${esc(shortName(lineOf(doc, top).name))} in West Texas, earns ${num(S('site.v2A5', top.v2A5), MONEY)} a kW-year against ${num(h.zonesMean, MONEY)} at the zone prices, and ${num(h.beatZone)} of the ${num(h.sites)} candidates beat their zone. The value sits in a few small pockets.`)}
      <div class="si-main">
        <section class="si-card si-mapcard" aria-label="Map of the 40 candidate nodes"><div class="si-card-h"><span class="si-ct">Candidate nodes and the lines behind them</span>
          <span class="si-sub">Click a node, or pick one from the list.</span></div>
          <span class="si-sub"><i class="si-key"></i>Dashed ring: export side. Lines: import end filled, export end open. Green: the selected node. Outline: US Census Bureau, public domain ${sTag('outline')}</span>${mapHTML({ mode: 'sites', legend: mapLegend() })}</section>
        ${listHTML()}${panelHTML(cur())}
      </div>
      ${kpiHTML(kpis)}
      ${portfolioHTML()}
      ${readsHTML(readsWhere(doc, num), 'What the map says, in five sentences')}
      <section class="si-card" aria-label="The pockets"><span class="si-ct">The 12 pockets: one constraint, the nodes behind it, and the room</span>
        <span class="si-sub">The first-MW price is not the 100th-MW price: the nodes behind one line share one cap of ${num(constant(doc, 'PRICE_TAKER_SHARE'), PCT0)} of its limit.</span>
        <div class="si-table" role="table" aria-label="Pockets">
          <div class="si-tr si-th" role="row"><span role="columnheader">Constraint</span><span role="columnheader">Limit MW ${sTag('line.limit')}</span><span role="columnheader">2025 rent $M ${sTag('line.rent')}</span>
            <span role="columnheader">Binding hours ${sTag('line.hours')}</span><span role="columnheader">Nodes behind it (click to select)</span><span role="columnheader">Best node $/kW-yr ${sTag('site.v2A5')}</span>
            <span role="columnheader">Mean gain ${sTag('site.up')}</span><span role="columnheader">Cap MW ${sTag('line.cap')}</span></div>${rows}</div>
        <div class="si-foot">${esc(doc.meta.battery)}. ${esc(doc.meta.units)}. ${num(derived(doc.lines.filter((l) => l.kind === 'unplaced').length, 'constraints whose line ends could not be placed'))} of the ${num(h.constraints)} constraints have a line end that could not be placed, so their lines are not drawn; their nodes are. A first-MW result: never scale a site by fleet size.</div>
      </section>`;
  }

  // ---------------------------------------------------------------- q=2
  function scatterHTML() {
    const pts = scatterPoints(doc), HM = 31, W = 1000, H = 440, SFMAX = 1.05;
    const X = (h) => Math.min(h, HM) / HM * 100, Y = (sf) => 100 - Math.abs(sf) / SFMAX * 100;
    const cx = (hops) => (Math.min(hops, HM) / HM * W).toFixed(1), cy = (sf) => ((1 - sf / SFMAX) * H).toFixed(1);
    // the cloud: every substation–line pair with a strong shift factor, binned; three dot sizes by how many pairs share a cell
    const cloud = [[1, 1, 2.6], [2, 4, 4], [5, 1e9, 6]].map(([lo, hi, w]) => `<path d="${doc.hopsSf.pairs.filter(([, , n]) => n >= lo && n <= hi).map(([hops, sf]) => `M${cx(hops)} ${cy(sf)}h0`).join('')}" class="si-cloud" stroke-width="${w}"></path>`).join('');
    const strongY = Y(constant(doc, 'STRONG_SF').v) / 100 * H, farX = constant(doc, 'FAR_HOPS').v / HM * W;
    const callouts = { MIDW: 'tr', POWELLFI: 'br', SCRCV: 'r', KNAPP: 'r', ROUGH: 'tl', LWSVW: 'tl', PK: 'b', BG: 'r', MILES: 'r' };
    const dots = pts.map(({ s, hops, sf }) => {
      const on = s.sub === st.site, call = callouts[s.sub];
      return `<button type="button" class="si-sdot${s.side === 'export' ? ' exp' : ''}${on ? ' on' : ''}" data-site="${esc(s.sub)}" aria-pressed="${on}" style="left:${X(hops).toFixed(2)}%;top:${Y(sf).toFixed(2)}%"
        data-tip="${esc(`${s.sub}: ${hops} hops, shift factor ${sf2(sf)}, ${fmtV(s.v2A5)} $/kW-yr`)}" aria-label="${esc(`${s.sub}, ${hops} hops, shift factor ${sf2(sf)}, ${fmtV(s.v2A5)} dollars per kilowatt-year`)}"></button>${call || on ? `<span class="si-dlab" data-s="${call || 'r'}" style="left:${X(hops).toFixed(2)}%;top:${Y(sf).toFixed(2)}%">${esc(s.sub)}</span>` : ''}`;
    }).join('');
    const xt = [0, 5, 10, 15, 20, 25, 30].map((h) => `<span class="si-tick" style="left:${X(h)}%">${h === 30 ? '30+' : h}</span>`).join('');
    const yt = [0, 0.25, 0.5, 0.75, 1].map((v) => `<span class="si-ytick" style="top:${Y(v)}%">${v.toFixed(2)}</span>`).join('');
    return `<div class="si-scatter">
      <div class="si-splot"><svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" aria-hidden="true">
        <path d="${[0, 0.25, 0.5, 0.75, 1].map((v) => `M0 ${cy(v)}H${W}`).join('')}" class="si-grid"></path>
        <path d="M0 ${strongY.toFixed(1)}H${W}M${farX.toFixed(1)} 0V${H}" class="si-ref"></path>${cloud}</svg>
        ${yt}<span class="si-reflab" style="top:${(strongY / H * 100).toFixed(1)}%">strong: |SF| ≥ ${constant(doc, 'STRONG_SF').v.toFixed(2)}</span><span class="si-reflab v" style="left:${(farX / W * 100).toFixed(1)}%">far: ${constant(doc, 'FAR_HOPS').v}+ hops</span>${dots}</div>
      <div class="si-sx">${xt}</div></div>`;
  }

  function algoHTML() {
    const A = doc.algorithms.algos, top = Math.max(...A.map((a) => Math.max(a.nodes.v, a.zones.v)));
    const tg = (x) => tagHTML(x.label, x.cite);
    const ref = A[0];
    const rows = A.map((a) => {
      const ratio = a.nodes.v / a.zones.v;
      const orc = a.id === 'orc';
      return `<div class="si-arow${a.id === 'A5' ? ' lead' : ''}${orc ? ' orc' : ''}" role="row"><span role="cell"><b>${orc ? 'Ceiling' : esc(a.id)}</b> ${esc(a.name)}</span>
        <span role="cell" class="si-abars"><i class="z" style="width:${(a.zones.v / top * 100).toFixed(1)}%"></i><i class="n" style="width:${(a.nodes.v / top * 100).toFixed(1)}%"></i></span>
        <span role="cell" class="num">$${fmtV(a.zones.v)}</span><span role="cell" class="num">$${fmtV(a.nodes.v)}</span><span role="cell" class="num">${orc ? '' : `${ratio.toFixed(2)}×`}</span>
        <span role="cell" class="num">${orc ? '' : `${a.bestNodes.v} / ${a.bestZones.v}`}</span></div>`;
    }).join('');
    return `<div class="si-table si-algo" role="table" aria-label="Nodes against zones for each algorithm"><div class="si-arow si-th" role="row"><span role="columnheader">Algorithm</span><span role="columnheader" class="si-lgd"><i class="z"></i>8 zone prices <i class="n"></i>40 nodes</span>
      <span role="columnheader">Zones ${tg(ref.zones)}</span><span role="columnheader">Nodes ${tg(ref.nodes)}</span><span role="columnheader">Nodes ÷ zones ${tagHTML('DERIVED', 'DERIVED: the algorithm at the 40 nodes ÷ the same algorithm at the 8 zones')}</span><span role="columnheader">Best at, nodes / zones ${tg(A[1].bestNodes)}</span></div>${rows}</div>`;
  }

  function sidesHTML() {
    const pr = sidePairs(doc), top = Math.max(...pr.map((p) => p.imp.v2A5));
    const rows = pr.map((p) => `<div class="si-prow" role="row"><span role="cell"><b>${esc(shortName(p.line.name))}</b></span>
      <span role="cell" class="si-pbars"><span class="si-pb imp"><span class="tr"><i style="width:${(p.imp.v2A5 / top * 100).toFixed(1)}%"></i></span><em><button type="button" class="si-chipbtn" data-site="${esc(p.imp.sub)}">${esc(p.imp.sub)}</button> ${fmtV(p.imp.v2A5)}</em></span>
        <span class="si-pb exp"><span class="tr"><i style="width:${(p.exp.v2A5 / top * 100).toFixed(1)}%"></i></span><em><button type="button" class="si-chipbtn" data-site="${esc(p.exp.sub)}">${esc(p.exp.sub)}</button> ${fmtV(p.exp.v2A5)}</em></span></span></div>`).join('');
    return `<div class="si-table" role="table" aria-label="Best import-side and export-side node on each line">${rows}</div>`;
  }

  function whyHTML() {
    const st2 = doc.hopsSf.stats, h = doc.headline, A = doc.algorithms.algos, a2 = A.find((a) => a.id === 'A2'), a5 = A.find((a) => a.id === 'A5');
    const kpis = [
      ['Hops against shift factor', num(st2.rho, D2), `rank correlation over ${num(st2.pairs)} substation–line pairs.`],
      ['Strong pairs that are far', num(st2.strongFarShare, PCT0), `of the ${num(st2.strong)} strong pairs lie far from their line.`],
      ['Nearby but weak', num(st2.nearWeakShare, PCT0), 'of the pairs next to a line are electrically weak.'],
      ['Import side gains', sMoney(num, h.gainImport), `/kW-yr over the zone at ${num(h.nImport)} sites; export side ${sMoney(num, h.gainExport)} at ${num(h.nExport)}.`],
      ['Forecaster beats learner', `${num(a2.bestNodes)} <span class="si-of">of ${num(h.sites)}</span>`, `nodes: ${num(a2.nodes, MONEY)} against ${num(a5.nodes, MONEY)}.`],
    ];
    const s = cur();
    const far = scatterPoints(doc).filter((p) => p.hops >= constant(doc, 'FAR_HOPS').v).sort((a, b) => Math.abs(b.sf) - Math.abs(a.sf))[0];
    return `${headHTML('SITING · WHY', 'Why there', `Electrical distance decides, not the number of lines away. ${esc(far.s.sub)} is ${num(S('site.cons.hops', far.hops))} hops from its line yet has the largest shift factor in the study (${num(S('site.cons.sf', far.sf), D2)}); the shift factor says which end of the line a node is on and how hard the line’s price reaches it.`)}
      ${kpiHTML(kpis)}
      <div class="si-two2">
        <section class="si-card" aria-label="Shift factor against hops"><div class="si-card-h"><span class="si-ct">Shift factor against hops, for every node</span>
          <span class="si-legend"><span><i class="si-ld"></i>Import side</span><span><i class="si-ld exp"></i>Export side</span><span><i class="si-lc"></i>Strong substation–line pairs ${sTag('hops.pairs')}</span></span></div>
          <span class="si-sub">Height = size of the shift factor ${sTag('site.cons.sf')}; across = hops from the line ${sTag('site.cons.hops')}. A shift factor is how many dollars the price at a node moves per dollar of congestion on the line. Strong: |SF| of ${num(constant(doc, 'STRONG_SF'), D2)} or more. Far: ${num(constant(doc, 'FAR_HOPS'))} or more hops. Near: ${num(constant(doc, 'NEAR_HOPS'))} or fewer. Weak: |SF| under ${num(constant(doc, 'WEAK_SF'), D2)}.</span>
          ${scatterHTML()}
          <div class="si-note">Dot area in the grey cloud = how many pairs. Click a node to read it on the right.</div></section>
        <div class="si-stack">${panelHTML(s)}</div>
      </div>
      ${readsHTML(readsWhy(doc, num), 'Why electrical distance, in five sentences')}
      <div class="si-two2">
        <section class="si-card" aria-label="Import and export side"><span class="si-ct">Which end of the line? The best node on each side</span>
          <div class="si-sides"><div class="si-sidebox imp"><b>Import side</b><span>The price <em>rises</em> when the line binds, so the battery sells into the spike. A shift factor below zero.</span></div>
            <div class="si-wire" aria-hidden="true"><i></i><em>line</em><i></i></div>
            <div class="si-sidebox exp"><b>Export side</b><span>The price <em>falls</em> when the line binds, so the battery charges cheaply, but its injection loads the line. A shift factor above zero.</span></div></div>
          <span class="si-sub">$/kW-yr, 2 h learner, 2021–25 ${sTag('site.v2A5')}. Dark = import-side node, light = export-side node.</span>${sidesHTML()}</section>
        <section class="si-card" aria-label="Nodes against zones"><span class="si-ct">At a node, who earns most?</span>
          <span class="si-sub">The five algorithms from the Arena page, dispatched on the eight zone prices and on the 40 rebuilt node prices, 2 h, $/kW-yr, 2021–25 ${tagHTML('SIM', doc.algorithms.algos[0].nodes.cite)}. The Forecaster is the best at most nodes; the learner at most zones.</span>
          ${algoHTML()}<div class="si-note">The synthetic basis is a function of hub spreads the Forecaster can read, so it may be more predictable than a real one: this needs a check against real nodal prices.</div></section>
      </div>`;
  }

  // ---------------------------------------------------------------- q=3
  function dumbbellHTML(v) {
    const max = scenarioAxisMax(doc), X = (x) => x / max * 100;
    const ticks = [0, 50, 100, 150, 200, 250].filter((t) => t <= max).map((t) => `<span class="si-tick" style="left:${X(t)}%">$${t}</span>`).join('');
    const rows = (st.all ? v.shownAll : v.shown).map((r) => {
      const cls = r.delta < -1 ? 'loss' : r.delta > 1 ? 'gain' : 'same';
      const lo = Math.min(r.before, r.after), hi = Math.max(r.before, r.after);
      return `<div class="si-dr ${cls}" role="row"><span role="cell"><button type="button" class="si-chipbtn${r.sub === st.site ? ' on' : ''}" data-site="${esc(r.sub)}">${esc(r.sub)}</button></span>
        <span role="cell" class="si-dplot" aria-label="${esc(`${r.sub}: ${fmtV(r.before)} before, ${fmtV(r.after)} after`)}"><i class="seg" style="left:${X(lo).toFixed(2)}%;width:${Math.max(0.2, X(hi) - X(lo)).toFixed(2)}%"></i>
          <i class="b" style="left:${X(r.before).toFixed(2)}%"></i><i class="a" style="left:${X(r.after).toFixed(2)}%"></i></span>
        <span role="cell" class="num">${fmtV(r.before)}</span><span role="cell" class="num">${fmtV(r.after)}</span><span role="cell" class="num">${gain(r.delta)}</span>
        <span role="cell" class="num">#${r.rb} → #${r.ra}</span></div>`;
    }).join('');
    return `<div class="si-dumb" role="table" aria-label="Sites before and after the upgrade">
      <div class="si-dr si-th" role="row"><span role="columnheader">Site</span><span role="columnheader" class="si-daxis">${ticks}</span><span role="columnheader">Before ${sTag('rerank.value')}</span><span role="columnheader">After</span><span role="columnheader">Change</span><span role="columnheader">Rank ${sTag('rerank.rank')}</span></div>${rows}</div>
      ${v.shownAll.length > v.shown.length || st.all ? `<button type="button" class="st-btn-ghost si-more" data-all="${st.all ? '0' : '1'}" aria-pressed="${st.all}">${st.all ? 'Show only the biggest changes' : `Show all ${v.moved.length} sites that move`}</button>` : ''}`;
  }

  function wiresHTML() {
    const w = wiresBars(doc), W = doc.rerank.wires;
    const rows = w.rows.map(({ s, width }) => `<div class="si-wr${s.id === st.scen ? ' on' : ''}" role="row"><span role="cell"><b>${esc(s.id)}</b> ${esc(shortName(s.name))}</span>
      <span role="cell" class="si-wbar"><i class="${s.ratio < 1 ? 'cheap' : ''}" style="width:${width.toFixed(1)}%"></i></span><span role="cell" class="num">${fmtV(s.ratio, 1)}×</span>
      <span role="cell" class="num">${fin(s.nwaMw) ? `${fmtV(s.nwaMw, 0)} MW / ${fmtV(s.nwaMwh, 0)} MWh` : '—'}</span></div>`).join('');
    return `<div class="si-wires" role="table" aria-label="Battery cost against wires cost"><div class="si-wr si-th" role="row"><span role="columnheader">Upgrade</span>
      <span role="columnheader" class="si-wax">${w.ticks.map((t) => `<span class="si-tick" style="left:${t.left.toFixed(1)}%">${t.v}×</span>`).join('')}</span><span role="columnheader">Ratio ${sTag('rerank.ratio')}</span><span role="columnheader">Battery that relieves it ${sTag('upgrade.nwaMw')}</span></div>
      <div class="si-wlines" style="--one:${w.one.toFixed(2)}%">${rows}</div></div>
      <div class="si-note">Cost of the battery that relieves ${num(constant(doc, 'RELIEF_SHARE'), PCT0)} of the limit in ${num(constant(doc, 'RELIEF_HOURS'), PCT0)} of the element’s binding hours, over the wires fix. Median over the ${num(W.n)} sized elements: ${num(W.medianWires, X0)} the primary fix (range ${num(W.minWires, X1)} to ${num(W.maxWires, X0)}) and ${num(W.medianCheapest, X0)} the cheapest rating change.</div>`;
  }

  function fadeHTML() {
    const f = doc.fade, L = fadeLines(doc), YW = 100, X = (y) => (y - 2025) / 6 * 100, Y = (v) => 100 - Math.min(1.05, Math.max(0, v)) / 1.05 * 100;
    const path = (pts) => `M${pts.map((p) => `${X(p.year).toFixed(2)} ${Y(p.v).toFixed(2)}`).join('L')}`;
    const lines = [...L.houston.map((l) => `<path d="${path(l.pts)}" class="si-fl hou"></path>`), ...L.tb.map((l) => `<path d="${path(l.pts)}" class="si-fl tb"></path>`), `<path d="${path(L.mean)}" class="si-fl mean"></path>`].join('');
    const mean = L.mean.map((p) => `<span class="si-fdot" style="left:${X(p.year).toFixed(2)}%;top:${Y(p.v).toFixed(2)}%"></span>`).join('');
    const yt = [0, 0.25, 0.5, 0.75, 1].map((v) => `<span class="si-ytick" style="top:${Y(v).toFixed(2)}%">${Math.round(v * 100)}%</span>`).join('');
    const xt = f.years.map((y) => `<span class="si-tick" style="left:${X(y).toFixed(2)}%">${y === 2025 ? '2025 replay' : y}</span>`).join('');
    const gmax = Math.max(...f.storageGw.filter(fin));
    const gb = f.years.map((y, i) => `<div class="si-gb"><span class="num">${fin(f.storageGw[i]) ? fmtV(f.storageGw[i]) : '—'}</span><div class="bar"><i style="height:${fin(f.storageGw[i]) ? (f.storageGw[i] / gmax * 100).toFixed(1) : 0}%"></i></div><span>${y === 2025 ? 'replay' : y}</span></div>`).join('');
    const hl = f.halfLife;
    return `<div class="si-fplot"><div class="si-fchart"><svg viewBox="0 0 100 100" preserveAspectRatio="none" aria-hidden="true"><path d="M0 ${Y(0.5)}H100" class="si-ref"></path>${lines}</svg>${yt}${mean}
        <span class="si-flab mean" style="left:${X(2031) - 1}%;top:${Y(L.mean[6].v) - 6}%">40-site mean</span>
        <span class="si-flab tb" style="left:${X(2029)}%;top:${Y(L.tb[0].pts[2].v) + 7}%">Twin Buttes sites</span>
        <span class="si-flab hou" style="left:${X(2030.6)}%;top:${Y(1) + 2}%">Houston PK–DT sites</span></div>
      <div class="si-fx">${xt}</div></div>
      <div class="si-storage"><span class="si-sub">Effective storage online behind the constraints, GW ${sTag('fade.storageGw')} (${num(constant(doc, 'SAT_REALISATION'), PCT0)} of unbuilt MW realised; ${num(constant(doc, 'STORAGE_LISTED_GW'), D1)} GW listed)</span><div class="si-gbs">${gb}</div></div>
      <div class="si-halfs"><div><span class="si-sub">Half of the 2025 gain gone by 2028</span><b>${num(hl['2028'])} sites</b></div><div><span class="si-sub">by 2029</span><b>${num(hl['2029'])} sites</b></div><div><span class="si-sub">Not by 2031</span><b>${num(hl['> 2031'])} sites</b></div></div>`;
  }

  function whoHTML() {
    return `<div class="si-who">${doc.who.map((w) => `<div class="si-whocard"><b>${esc(w.lead)}</b> ${tagHTML(w.label, 'Rules cited in docs/research/grid/README.md section 3.1 and data/upgrades.csv (decider, battery_owner_role)')}<p>${fillText(doc, w.text, num)}</p></div>`).join('')}</div>`;
  }

  function movesHTML() {
    const v = scenarioView(doc, st.scen), sc = v.sc, planned = /^PLANNED/.test(sc.id);
    const ok = sc.effect === 'evaluated';
    const kpis = [
      ['Cost of the wires fix', fin(sc.cost) ? num(S('rerank.cost', sc.cost), MM) : '—', fin(sc.costLow) ? `mid case; ${num(S('upgrade.costLow', sc.costLow), MM)} to ${num(S('upgrade.costHigh', sc.costHigh), MM)}, ${num(S('upgrade.miles', sc.miles), { digits: 1, unit: ' miles' })}.` : planned ? 'a bundle of projects.' : 'an interface plan, not a line.'],
      ['Lead time', fin(sc.lead) ? num(S('rerank.lead', sc.lead), { digits: 0, unit: ' months' }) : '—', 'request to in service, mid case.'],
      ['Sum of the 40 sites', ok ? num(derived(+v.pct.toFixed(1), 'after ÷ before − 1, summed over the 40 sites'), { digits: 1, unit: '%', signed: true }) : '0%', ok ? `${num(derived(+v.before.toFixed(0), 'sum of before values'), MONEY0)} to ${num(derived(+v.after.toFixed(0), 'sum of after values'), MONEY0)} a kW-year.` : 'no site moves: the constraint did not bind in summer 2026.'],
      ['Sites that move', `${num(derived(v.losers.length + v.gainers.length, 'sites that change by more than $1'))} <span class="si-of">of ${num(doc.headline.sites)}</span>`, `${num(derived(v.losers.length, 'sites that lose more than $1'))} lose, ${num(derived(v.gainers.length, 'sites that gain more than $1'))} gain.`],
      ['Battery against wires', fin(sc.ratio) ? num(S('rerank.ratio', sc.ratio), X1) : '—', sc.id === 'U04' ? 'a sizing exercise: the plan also serves load growth.' : 'cost of the battery that would relieve it, over the wires fix.'],
    ];
    const status = planned ? 'A bundle of dated planned projects.' : sc.planned === 'yes' ? `A project is on file${sc.plannedIsd ? ` (in service ${num(S('upgrade.isd', shortDate(sc.plannedIsd)))})` : ''}.` : sc.planned === 'identified' ? 'Identified in the regional plan, with no date.' : sc.planned === 'hint' || sc.planned === 'adjacent' ? `A related project is on file${sc.plannedIsd ? ` (in service ${num(S('upgrade.isd', shortDate(sc.plannedIsd)))})` : ''}.` : 'No project on file.';
    const resid = fin(sc.residual) ? `${num(S('rerank.residual', sc.residual), D2)} of the shadow prices left after the upgrade` : '';
    const answer = !ok ? `${esc(sc.name)} cannot be valued: it did not bind in summer 2026, so there is no shift factor to replay and no site moves.`
      : (!v.losers.length && !v.gainers.length) ? `${esc(sc.name)} moves no site by more than ${num(derived(1, 'the study counts a site as moved above this'), MONEY0)}.`
      : `${planned ? 'The planned wires' : `${esc(shortName(sc.name))}`} would change the sum of the ${num(doc.headline.sites)} sites by ${num(derived(+v.pct.toFixed(1), 'after ÷ before − 1'), { digits: 1, unit: '%', signed: true })}; the biggest move is ${esc(v.biggest.sub)}, ${num(S('rerank.value', v.biggest.before), MONEY)} to ${num(S('rerank.value', v.biggest.after), MONEY)}.`;
    return `${headHTML('SITING · WHAT MOVES IT', 'What moves it', `Only the transmission owner can make an upgrade happen, and an upgrade changes the list only where its line binds. ${answer}`)}
      <section class="si-card" aria-label="Sites before and after ${esc(sc.id)}"><div class="si-card-h"><span class="si-ct">${planned ? esc(sc.name) : `${esc(sc.id)} · ${esc(sc.name)}`}</span>
        <span class="si-legend"><span><i class="si-dl loss"></i>loses</span><span><i class="si-dl gain"></i>gains</span><span><i class="si-dl b"></i>before</span><span><i class="si-dl a"></i>after</span></span></div>
        <span class="si-sub">${esc(status)} ${resid ? `Modelled with ${resid} ${sTag('rerank.residual')}.` : ''} Value is the 2 h learner on the 2025 replay prices ($/kW-yr), so MIDW is ${fmtV(doc.rerank.scenarios[0].sites[0].before)} here against ${fmtV(doc.sites[0].v2A5)} in the 2021–25 list. The sites that move most, and the top five for context.</span>
        ${ok && v.shown.length ? dumbbellHTML(v) : '<div class="si-empty">No site changes: the constraint did not bind in the summer 2026 prices, so it cannot be replayed.</div>'}</section>
      ${kpiHTML(kpis)}
      ${readsHTML(readsMoves(doc, st.scen, num), 'What the test says, in four sentences')}
      <div class="si-two2">
        <section class="si-card" aria-label="Battery against wires"><span class="si-ct">Wires beat batteries on cost</span><span class="si-sub">How many times the wires fix a battery would cost for the same relief, log scale. The 1× line is equal cost.</span>${wiresHTML()}</section>
        <section class="si-card" aria-label="The fade"><span class="si-ct">The premium fades as storage arrives, 2025 to 2031</span><span class="si-sub">Share of each site’s 2025 gain over the zone that is left ${sTag('fade.meanUp')}. All of the fade is storage saturation; planned upgrades alone move no candidate by more than a few dollars.</span>${fadeHTML()}</section>
      </div>
      <section class="si-card" aria-label="Who can make an upgrade happen"><span class="si-ct">Who can make an upgrade happen</span>${whoHTML()}</section>`;
  }

  // ---------------------------------------------------------------- q=4
  function regionsHTML() {
    const focus = ['Rio Grande Valley', 'Panhandle', 'Far West'];
    const max = 12, umax = 0.6;
    const rows = doc.regions.map((r) => {
      const f = focus.includes(r.name);
      const vd = r.verdict;
      const w = (x) => Math.min(100, x / max * 100);
      const up = r.uplift;
      return `<div class="si-rr${f ? ' focus' : ''}" role="row"><span role="cell"><b>${esc(r.name)}</b><i>${num(S('region.n', r.n), { unit: ' nodes' })}</i></span>
        <span role="cell" class="si-rbars"><span class="si-rb neg"><i style="width:${w(r.neg10).toFixed(1)}%"></i><em class="num">${fmtV(r.neg10)}%</em></span><span class="si-rb pos"><i style="width:${w(r.pos10).toFixed(1)}%"></i><em class="num">${fmtV(r.pos10)}%</em></span></span>
        <span role="cell" class="si-rup"><span class="tr"><span class="si-rzero"></span><i class="${up < 0 ? 'neg' : 'pos'}" style="${up < 0 ? `right:50%;width:${Math.min(50, Math.abs(up) / umax * 50).toFixed(1)}%` : `left:50%;width:${Math.min(50, up / umax * 50).toFixed(1)}%`}"></i></span><em class="num">${up > 0 ? '+' : up < 0 ? MINUS : ''}$${Math.abs(up).toFixed(2)}</em></span>
        <span role="cell">${esc(vd)}</span></div>`;
    }).join('');
    return `<div class="si-regions" role="table" aria-label="Measured summer 2026 basis by region"><div class="si-rr si-th" role="row"><span role="columnheader">Region</span>
      <span role="columnheader">Node-hours more than ${num(constant(doc, 'BASIS_USD'), MONEY0)} below / above the zone, % ${sTag('region.neg10')}</span>
      <span role="columnheader">2 h battery, node minus zone, $ per kW over ${num(constant(doc, 'DAM_DAYS'))} days ${sTag('region.uplift')}</span><span role="columnheader">Reading ${tagHTML('ASSUMPTION', 'VERDICT_TAIL_SHARE / VERDICT_HOUR_MEAN rules in the study (data/regional_basis_summary.csv)')}</span></div>${rows}</div>`;
  }

  function picksTableHTML() {
    const rows = pickGroups(doc).map((g) => `<div class="si-tr" role="row"><span role="cell"><b>${esc(g.region)}</b><i>${g.side} side</i></span><span role="cell" class="num">${g.n}</span>
      <span role="cell" class="num">${g.dam == null ? '—' : `${g.dam < 0 ? MINUS : '+'}$${Math.abs(g.dam).toFixed(2)}`}</span><span role="cell" class="num">${gain(g.gain)}</span><span role="cell" class="num">${g.above} of ${g.n}</span><span role="cell" class="num">${g.far} of ${g.n}</span></div>`).join('');
    return `<div class="si-table si-picks" role="table" aria-label="Regional picks by region and side"><div class="si-tr si-th" role="row"><span role="columnheader">Region and side</span><span role="columnheader">Picks</span>
      <span role="columnheader">Observed, node minus zone, $ per kW over ${num(constant(doc, 'DAM_DAYS'))} days ${sTag('pick.dam')}</span><span role="columnheader">Modelled gain, $/kW-yr ${sTag('pick.gain')}</span>
      <span role="columnheader">Gain above $1</span><span role="columnheader">Line more than ${num(constant(doc, 'FAR_KM'), D0)} km away ${sTag('pick.km')}</span></div>${rows}</div>`;
  }

  function pickCardHTML() {
    const p = doc.picks.find((x) => x.sub === st.pick);
    if (!p) return '<div class="si-note">Click a pick on the map to read its measured and modelled numbers.</div>';
    return `<div class="si-pickcard" aria-live="polite"><b>${esc(p.sub)}</b> <span class="si-dim">${esc(p.county)} County · ${esc(p.region)} · ${p.side} side</span>
      <div class="si-krow"><span>Modelled gain over the zone, 2021–25</span><span>${sMoney(num, S('pick.gain', p.gain))} a kW-year</span></div>
      <div class="si-krow"><span>Measured: node minus zone, summer 2026</span><span>${fin(p.obs) ? `${p.obs > 0 ? '+' : ''}${num(S('pick.obs', p.obs), { money: true, digits: 2 })} a MWh` : '—'}</span></div>
      <div class="si-krow"><span>Observed 2 h value, node minus zone</span><span>${fin(p.dam) ? `${p.dam > 0 ? '+' : ''}${num(S('pick.dam', p.dam), { money: true, digits: 2 })} a kW over ${num(constant(doc, 'DAM_DAYS'))} days` : '—'}</span></div>
      <div class="si-krow"><span>Dominant line: ${esc(p.el)}</span><span>${fin(p.km) ? `${num(S('pick.km', p.km), D0)} km away` : 'ends not placed'}</span></div></div>`;
  }

  function riskHTML() {
    const r = doc.risk, max = r.flood.v;
    const bar = (label, x) => `<div class="si-xrow"><span>${esc(label)}</span><span class="si-xbar"><i style="width:${Math.max(1.5, x.v / max * 100).toFixed(1)}%"></i></span><span>${num(x)}</span></div>`;
    const s = doc.sites;
    const flags = [['Flood map missing (unmapped)', s.filter((x) => x.flood === 'unmapped').length], ['Seismic flag', s.filter((x) => x.quake).length],
      ['High wildfire hazard', s.filter((x) => x.fire === 'high').length], ['Excluded', s.filter((x) => x.excl).length]];
    return `<div class="si-xbars">${bar('In a FEMA flood hazard area', r.flood)}${bar('In a regulatory floodway', r.floodway)}${bar('At very high wildfire hazard', r.wildfire)}</div>
      <div class="si-note">${num(r.excluded)} of ${num(r.assessed)} site-assessed substations are excluded, ${num(derived(+(r.excluded.v / r.assessed.v * 100).toFixed(1), 'excluded ÷ assessed'), PCT1)}. The flood check is blind in West Texas: ${num(r.unmapped)} assessed substations have no digital flood polygon.</div>
      <div class="si-flags"><span class="si-sub">THE 40 SITES</span>${flags.map(([k, n]) => `<div class="si-krow"><span>${esc(k)}</span><span>${num(derived(n, `of the 40 sites in data/sites.csv`))} <span class="si-dim">of ${num(doc.headline.sites)}</span></span></div>`).join('')}</div>`;
  }

  function relScatterHTML() {
    const pts = reliabilityPoints(doc), XM = 280, X = (v) => v / XM * 100, Y = (n) => 100 - n / 100 * 100;
    const seen = {};
    const dots = pts.map(({ s, value, need, top100 }) => { const k = `${Math.round(value)}|${Math.round(need)}`; const n = seen[k] = (seen[k] ?? -1) + 1; const shift = n * 20; return `<button type="button" class="si-rdot${top100 ? ' top' : ''}${s.sub === st.site ? ' on' : ''}" data-site="${esc(s.sub)}" aria-pressed="${s.sub === st.site}" style="left:${X(value).toFixed(2)}%;top:${Y(need).toFixed(2)}%;margin-left:${shift}px"
        data-tip="${esc(`${s.sub}: ${fmtV(value)} $/kW-yr market value, reliability need ${need.toFixed(1)} of 100${top100 ? `, rank ${s.relRank}` : ''}`)}" aria-label="${esc(`${s.sub}, market value ${fmtV(value)}, reliability need ${need.toFixed(1)}`)}"></button>
      ${top100 && s.sub === 'MID' ? `<span class="si-dlab" data-s="tr" style="left:${X(value).toFixed(2)}%;top:${Y(need).toFixed(2)}%">Houston PK–DT sites</span>` : ''}${s.sub === 'MIDW' ? `<span class="si-dlab" data-s="tl" style="left:${X(value).toFixed(2)}%;top:${Y(need).toFixed(2)}%">MIDW, STRS, POWELLFI: no rank</span>` : ''}`; }).join('');
    const xt = [0, 50, 100, 150, 200, 250].map((v) => `<span class="si-tick" style="left:${X(v)}%">$${v}</span>`).join('');
    const yt = [0, 25, 50, 75, 100].map((v) => `<span class="si-ytick" style="top:${Y(v)}%">${v}</span>`).join('');
    return `<div class="si-scatter rel"><div class="si-splot"><svg viewBox="0 0 100 100" preserveAspectRatio="none" aria-hidden="true"><path d="${[0, 25, 50, 75, 100].map((v) => `M0 ${Y(v)}H100`).join('')}" class="si-grid"></path></svg>${yt}${dots}</div><div class="si-sx">${xt}</div></div>`;
  }

  function tier2HTML() {
    const t = doc.tier2.groups, max = Math.max(...t.map((g) => Math.max(g.desr.v, g.ader.v)));
    const rows = t.map((g) => `<div class="si-t2" role="row"><span role="cell"><b>${esc(g.name)}</b><i>${num(g.n)} candidates</i></span>
      <span role="cell" class="si-t2bars"><span class="si-t2b desr"><i style="width:${(g.desr.v / max * 100).toFixed(1)}%"></i><em>${num(g.desr, MONEY)}</em></span><span class="si-t2b ader"><i style="width:${(g.ader.v / max * 100).toFixed(1)}%"></i><em>${num(g.ader, MONEY)}</em></span></span>
      <span role="cell">${sMoney(num, g.gain)}</span><span role="cell">${sMoney(num, g.gain29)}</span></div>`).join('');
    const rules = doc.tier2Rules.map((r) => `<div class="si-rule${r.nodal ? ' nodal' : ''}" role="row"><span role="cell"><b>${esc(r.route)}</b></span><span role="cell">${fillText(doc, r.what, num)}</span><span role="cell">${fillText(doc, r.settles, num)}</span><span role="cell">${r.nodal ? 'Yes' : 'No'}</span></div>`).join('');
    return `<div class="si-tier2"><div class="si-table" role="table" aria-label="DESR against ADER value"><div class="si-t2 si-th" role="row"><span role="columnheader">Group (valued candidates)</span>
        <span role="columnheader" class="si-lgd"><i class="d"></i>DESR: node price <i class="a"></i>ADER: zone price, $/kW-yr, 2025 replay</span><span role="columnheader">DESR gain over the zone</span><span role="columnheader">Gain in 2029</span></div>${rows}</div>
      <div class="si-table si-rules" role="table" aria-label="Which price each route is paid"><div class="si-rule si-th" role="row"><span role="columnheader">Route</span><span role="columnheader">What it is</span><span role="columnheader">Settles at</span><span role="columnheader">Gets nodal value?</span></div>${rules}</div></div>`;
  }

  function beyondHTML() {
    const R = Object.fromEntries(doc.regions.map((r) => [r.name, r])), h = doc.headline, val = R['Rio Grande Valley'];
    const pm = avg(doc.picks.map((p) => p.gain)), t0 = doc.tier2.groups[0];
    const kpis = [
      ['Valley, below the zone', num(S('region.neg10', val.neg10), PCT1), `of node-hours far below the zone; ${num(S('region.pos10', val.pos10), PCT1)} far above.`],
      ['Valley battery, summer window', num(S('region.uplift', val.uplift), { money: true, digits: 2 }), 'a kW, node minus zone: no uplift.'],
      ['Regional picks, modelled', sMoney(num, derived(+pm.toFixed(1), 'mean of the 41 picks')), `/kW-yr, against ${sMoney(num, h.gainMean)} for the 40.`],
      ['Excluded by physical risk', `${num(doc.risk.excluded)}`, `of ${num(doc.risk.assessed)} assessed substations.`],
      ['In the reliability top group', `${num(doc.reliability.inTop100)} <span class="si-of">of ${num(h.sites)}</span>`, 'all on the Houston PK–DT line.'],
      ['DESR against ADER', sMoney(num, t0.gain), `/kW-yr: a home fleet is paid the zone price.`],
    ];
    return `${headHTML('SITING · BEYOND THE 40', 'Regions, reliability, distribution', `The Panhandle, the Valley and the Far West do not offer a second pocket in the summer prices: the Valley’s congestion is an export discount and a battery there shows no uplift. A distribution battery that registers as a DESR is paid the node price; a home fleet in an ADER is paid the zone price.`)}
      <div class="si-main two">
        <section class="si-card si-mapcard" aria-label="Regional picks on the map"><div class="si-card-h"><span class="si-ct">Where the study looked beyond the 40</span><span class="si-sub">${num(derived(doc.picks.length, 'regional picks in data/extra_sites.csv'))} picks in four regions and Bell County. Click one to read it. Outline: US Census Bureau, public domain ${sTag('outline')}</span></div>
          ${mapHTML({ mode: 'picks', legend: `<div class="si-maplegend over"><span class="si-sub">Filled: modelled gain above ${num(constant(doc, 'PICK_USD'), MONEY0)} a kW-year ${sTag('pick.gain')}; hollow: at or below. Size: the gain. Grey dots: the 40 sites.</span></div>` })}</section>
        <div class="si-stack"><section class="si-card" aria-label="Measured basis by region"><span class="si-ct">What the summer 2026 prices show</span>
          <span class="si-sub">Measured day-ahead prices at ${num(constant(doc, 'DAM_POINTS'))} settlement points over ${num(constant(doc, 'DAM_DAYS'))} delivery days (1 Jul to 11 Aug 2026). One summer, day-ahead only. Dark bar: below the zone. Light bar: above.</span>
          ${regionsHTML()}<div class="si-note">The Valley’s evening discount sits on the hours a battery wants to sell. Valley and Panhandle batteries show no uplift over their zone. Vealmoor–Koch Tap, Longshore–Consavvy and Odessa–Yarbrough did not bind in summer 2026.</div></section>
        <section class="si-card" aria-label="A regional pick"><span class="si-ct">A regional pick</span>${pickCardHTML()}</section></div>
      </div>
      ${kpiHTML(kpis)}
      ${readsHTML(readsBeyond(doc, num), 'What this means, in six sentences')}
      <section class="si-card" aria-label="Regional picks"><span class="si-ct">The 41 regional picks, measured against modelled</span>${picksTableHTML()}
        <div class="si-note">Measured and modelled agree on the sign for most picks. The positive modelled gains in the Far West draw on Twin Buttes shift factors at stations hundreds of kilometres from the line, where the measured prices do not support them: treat those as an artefact of the day-ahead fit.</div></section>
      <div class="si-two2">
        <section class="si-card" aria-label="Physical risk"><span class="si-ct">Physical-risk exclusions</span>${riskHTML()}</section>
        <section class="si-card" aria-label="Reliability against market value"><span class="si-ct">Reliability, kept apart from market value</span>
          <span class="si-sub">Across: 2 h learner value, $/kW-yr ${sTag('site.v2A5')}. Up: reliability need score, 0 to 100 ${sTag('site.rel')}. Two units, two labels: they are never added.</span>${relScatterHTML()}
          <div class="si-note">Houston is both a market case and a reliability case (the four ringed nodes). Twin Buttes is a market case only; its county’s outage data cannot rank it.</div></section>
      </div>
      <section class="si-card" aria-label="Tier 2"><span class="si-ct">Tier 2: distribution batteries and home fleets</span>
        <span class="si-sub">Same 2 h battery, two routes. A DESR captures the nodal value; an ADER aggregation of home batteries is paid the load-zone price. Mean over the ${num(t0.n)} candidates valued with the dispatch model.</span>${tier2HTML()}
        <div class="si-note">${num(doc.tier2.gaining)} of ${num(t0.n)} gain over the zone, ${num(doc.tier2.gainingTen, PCT0)} by more than $10. Candidates more than ${num(constant(doc, 'FAR_KM'), D0)} km from their line gain less and deserve less trust.</div></section>`;
  }

  // ---------------------------------------------------------------- render + events
  function render() {
    const body = st.view === 1 ? whereHTML() : st.view === 2 ? whyHTML() : st.view === 3 ? movesHTML() : beyondHTML();
    const b0 = el.querySelector('.si-body'), top = b0 ? b0.scrollTop : 0;
    const l0 = el.querySelector('.si-lbody'), ltop = l0 ? l0.scrollTop : 0;
    const a = document.activeElement, focus = a && a.dataset ? ['site', 'pick', 'scen', 'q', 'all'].map((k) => (a.dataset[k] != null ? [k, a.dataset[k]] : null)).find(Boolean) : null;
    el.innerHTML = `${railHTML()}<div class="si-body">${body}</div>`;
    const nb = el.querySelector('.si-body');
    if (nb) nb.scrollTop = top;
    const nl = el.querySelector('.si-lbody');
    if (nl) {
      nl.scrollTop = ltop;
      const on = nl.querySelector('.on');
      if (on) {       // keep the selected row in view, with its neighbours around it
        const r = on.getBoundingClientRect(), c = nl.getBoundingClientRect();
        if (r.top < c.top || r.bottom > c.bottom) nl.scrollTop += (r.top - c.top) - (nl.clientHeight - r.height) / 2;
      }
    }
    if (focus) { const t = el.querySelector(`[data-${focus[0]}="${CSS.escape(focus[1])}"]`); if (t) t.focus({ preventScroll: true }); }
    saveState({ site: st.site, scen: st.scen });
  }
  const onClick = (ev) => {
    const t = ev.target.closest('button');
    if (!t || !el.contains(t)) return;
    if (t.dataset.q) {
      st.view = viewOf(Number(t.dataset.q));
      history.replaceState(history.state, '', ctx.link({ page: PAGE, q: st.view }));
      const b = el.querySelector('.si-body');
      if (b) b.scrollTop = 0;
      render();
      return;
    }
    if (t.dataset.site) { st.site = t.dataset.site; render(); return; }
    if (t.dataset.scen) { st.scen = t.dataset.scen; st.all = false; render(); return; }
    if (t.dataset.all) { st.all = t.dataset.all === '1'; render(); return; }
    if (t.dataset.pick) { st.pick = st.pick === t.dataset.pick ? null : t.dataset.pick; render(); }
  };
  el.addEventListener('click', onClick);
  render();
  return { dispose() { el.removeEventListener('click', onClick); } };
}
