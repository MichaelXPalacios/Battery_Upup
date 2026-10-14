// ui/story/arena.js: Screen 6 Arena, "How should a battery trade?" (docs/research/arena/README.md).
// One file, ui/data/arena/index.json (hb.arena.v1, written by scripts/export_arena.py from the arena's results and caches).
// Left rail: market (ERCOT / NYISO), view (?q=1..4), the battery spec and, on q1 and q2, the zone. Four views:
//   q=1 Head to head      five dispatch rules against the perfect-foresight ceiling: a period or year, the league across zones
//   q=2 Who wins when     year x rule win map with named events; the learner's expert mix by month and how often it switches
//   q=3 Placing the next 10 MW   the five siting rules and the fleets they built, the 2027-31 tournament, transmission, the site ranking
//   q=4 How each one thinks      one card per rule, and one real day replayed through the arena's own algorithms
// Backtests run SIM dispatch on REAL prices; the 2027-31 futures are SIM. Money is gross trading value per kW of battery per
// year, not Base's profit. No language model produces a number, a setpoint or a rank here: the arena's deterministic code did.
// Pure layout and sentence helpers are exported and tested (ui/test/story-arena.test.js); each view is one pure function of
// (doc, state, num) so the whole page can be rendered and audited without a browser.

import { tagHTML } from './shell.js';
import { dateLabel, fmtValue } from '../lib/format.js';

export const PAGE = 'arena';
export const DATA = 'arena/index.json';
export const MARKETS = [['ERCOT', 'ERCOT'], ['NYISO', 'NYISO']];
export const SCOPES3 = [['ERCOT', 'ERCOT', 'ERCOT'], ['NYISO', 'NYISO', 'NYISO'], ['BOTH', 'Both', 'Both markets']];
export const SPECS = [['home', 'Home fleet'], ['grid', 'Grid-scale']];
export const VIEWS = [
  { q: 1, title: 'Head to head', sub: 'Five rules against perfect foresight' },
  { q: 2, title: 'Who wins when', sub: 'Year by year, and the learner’s picks' },
  { q: 3, title: 'Placing the next {SITE_MW}', sub: 'Siting rules and the {FORWARD} tournament' },
  { q: 4, title: 'How each one thinks', sub: 'Each rule in words, and one real day' },
];
export const ALGOS = ['A1', 'A2', 'A3', 'A4', 'A5'];
export const LEARNER = 'A5';
const STORE = 'hb-arena';
const DEFAULT_ZONE = { ERCOT: 'ALL', NYISO: 'ALL' };

const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const fin = (x) => typeof x === 'number' && Number.isFinite(x);
const avg = (a) => (a.length ? a.reduce((s, x) => s + x, 0) / a.length : 0);
const sum = (a) => a.reduce((s, x) => s + x, 0);
const MONEY = { money: true, digits: 1 };
const PCT0 = { unit: '%', digits: 0 };

export const viewOf = (q) => ([1, 2, 3, 4].includes(q) ? q : 1);

/** A bulk value from the file's `series` as a labelled number (label and text from the series entry). */
export function fromSeries(doc, key, v) {
  const s = doc && doc.series && doc.series[key];
  if (!s) throw new Error(`arena: no series ${key}`);
  return { v, label: s.label, cite: s.text };
}
/** A number this page computes from labelled inputs: DERIVED, with the arithmetic in the cite. */
export const derived = (v, cite) => ({ v, label: 'DERIVED', cite: `DERIVED: ${cite}` });
/** A constant of the file as a labelled number. */
export function konst(doc, name) {
  const c = doc && doc.constants && doc.constants[name];
  if (!c) throw new Error(`arena: no constant ${name}`);
  return { v: c.value, label: c.label, cite: c.cite };
}

const fmtN = (v, d = 1) => (fin(v) ? Number(v).toLocaleString('en-US', { minimumFractionDigits: d, maximumFractionDigits: d }) : '—');
/** A bulk number: digits inside <span class="num">, its tag sits in the heading or legend that names the series. */
const bulk = (v, d = 1) => `<span class="num">${esc(fmtN(v, d))}</span>`;
const sNum = (num, x, o = MONEY) => `${x.v > 0 ? '+' : ''}${num(x, o)}`;
const bulkU = (v, d = 1, u = '') => `<span class="num">${esc(fmtN(v, d))}${esc(u)}</span>`;
/** +$3.5 / −$1.0 (format.js writes $+3.5). */
const sUsdB = (v, d = 1) => `<span class="num">${v > 0 ? '+' : v < 0 ? '−' : ''}$${esc(fmtN(Math.abs(v), d))}</span>`;
const usdB = (v, d = 1) => `<span class="num">${v < 0 ? '−' : ''}$${esc(fmtN(Math.abs(v), d))}</span>`;
const lbl = (t) => `<span class="num">${esc(t)}</span>`;               // a year or a range used as a label
/** A name that carries an identifier (SB 415): its digits are set as figures. */
const idHTML = (t) => esc(t).replace(/\d+/g, (m) => `<span class="num">${m}</span>`);

// ---------------------------------------------------------------------------------------------- words and tokens
// how each named constant is written inside a sentence (a constant with no entry is written as it is)
export const TOKEN_OPTS = {
  SPIKE_LO: { unit: 'th' }, SPIKE_HI: { unit: 'th' }, SITE_MW: { unit: ' MW' }, SITE_A2_CAP: { unit: ' MW' }, RESERVE_FLOOR: { unit: '%' },
  LEARNER_GAMMA: { digits: 2 }, ROUND_TRIP: { digits: 2 }, GRID_ROUND_TRIP: { digits: 2 },
};
/** Numbers the rule sentences name come from the file's constants: "{SPIKE_HI}" becomes a number. In bulk mode (the default) it is a
 *  plain <span class="num"> and the rule parameters' tag sits once in the heading (paramTag); otherwise a labelled value. */
export function fillTokens(doc, text, num, bulkMode = true) {
  return esc(text).replace(/\{([A-Z0-9_]+)\}/g, (m, k) => {
    const c = konst(doc, k), o = TOKEN_OPTS[k] || {};
    return bulkMode ? `<span class="num">${esc(fmtValue(c, o))}</span>` : num(c, o);
  });
}
const paramTag = () => tagHTML('ASSUMPTION', 'The numbers inside the rules are the arena’s own parameters, chosen by its authors and listed in docs/research/arena/arena/ (change one and rerun).');
const nameOf = (doc, id) => (doc.algorithms.find((a) => a.id === id) || {}).name || id;
const ruleOf = (doc, id) => doc.algorithms.find((a) => a.id === id);
const specName = (spec) => (SPECS.find(([k]) => k === spec) || SPECS[0])[1];

// ---------------------------------------------------------------------------------------------- data access (pure)
export const zoneEntries = (doc, mk) => [doc.markets[mk].all, ...doc.markets[mk].zones];
export const entryOf = (doc, mk, zone) => zoneEntries(doc, mk).find((z) => z.id === zone) || doc.markets[mk].all;
export const yearIdx = (M, y) => M.years.indexOf(y);
export function windowIdx(doc, M) {
  const [a, b] = doc.meta.window;
  return M.years.map((y, i) => (y >= a && y <= b ? i : -1)).filter((i) => i >= 0);
}
const windowText = (doc) => `${doc.meta.window[0]}–${String(doc.meta.window[1]).slice(2)}`;

/** One rule's $/kW-yr in one period for one entry: 'mean' = the mean of the scoring-window years, else one year. */
export function valueOf(doc, mk, entry, spec, algo, period) {
  const M = doc.markets[mk], arr = entry[spec].y[algo];
  if (period === 'mean') return avg(windowIdx(doc, M).map((i) => arr[i]));
  const i = yearIdx(M, Number(period));
  return i >= 0 ? arr[i] : NaN;
}
/** The labelled number for a value from valueOf: a year is the file's SIM series, the window mean is DERIVED from it. */
export function yLab(doc, key, v, period) {
  return period === 'mean'
    ? derived(v, `mean of the ${windowText(doc)} yearly values (each SIM, ${key === 'ceiling' ? 'perfect-foresight ceiling' : 'profit per kW-year'})`)
    : fromSeries(doc, key, v);
}

/** The race of one period: every rule's value, its share of the ceiling, who led. Pure (tested). */
export function raceRows(doc, mk, entry, spec, period) {
  const ceil = valueOf(doc, mk, entry, spec, 'CEIL', period);
  const rows = ALGOS.map((id) => {
    const v = valueOf(doc, mk, entry, spec, id, period);
    return { id, name: nameOf(doc, id), v, share: ceil > 0 ? v / ceil * 100 : 0 };
  });
  const lead = rows.reduce((b, r) => (r.v > b.v ? r : b), rows[0]);
  const fixed = rows.filter((r) => r.id !== LEARNER);
  const bestFixed = fixed.reduce((b, r) => (r.v > b.v ? r : b), fixed[0]);
  return { ceil, rows, lead: lead.id, bestFixed: bestFixed.id, learner: rows.find((r) => r.id === LEARNER) };
}

/** The league across zones for one period: the market mean first, then every zone. Pure (tested). */
export function leagueRows(doc, mk, spec, period) {
  return zoneEntries(doc, mk).map((e) => {
    const r = raceRows(doc, mk, e, spec, period);
    return { id: e.id, label: e.label, ceil: r.ceil, v: Object.fromEntries(r.rows.map((x) => [x.id, x.v])), lead: r.lead, share: r.learner.share };
  });
}

/** The year x rule win map: per year every rule's value, the ceiling, the winner; plus the scoring-window column. Pure (tested). */
export function winMap(doc, mk, entry, spec) {
  const M = doc.markets[mk];
  const col = (get) => {
    const v = Object.fromEntries(ALGOS.map((a) => [a, get(entry[spec].y[a])]));
    const ceil = get(entry[spec].y.CEIL);
    const win = ALGOS.reduce((b, a) => (v[a] > v[b] ? a : b), ALGOS[0]);
    return { v, ceil, win, share: Object.fromEntries(ALGOS.map((a) => [a, ceil > 0 ? v[a] / ceil * 100 : 0])) };
  };
  const cols = M.years.map((year, i) => ({ year, partial: M.partial[i], event: M.events.find((e) => e.year === year) || null, ...col((arr) => arr[i]) }));
  const w = windowIdx(doc, M);
  const mean = col((arr) => avg(w.map((i) => arr[i])));
  const inWin = cols.filter((c) => c.year >= doc.meta.window[0] && c.year <= doc.meta.window[1]);
  const winners = [...new Set(inWin.map((c) => c.win))];
  return { cols, mean, inWin, winners, nWinners: winners.length };
}

/** Stacked areas of the learner's rule-family mix by month. fam: 4 arrays of percent per month, normalised per month so each
 *  column fills the height. Returns one polygon per family (bottom family first) and the month width. Pure (tested). */
export function ribbonLayout(fam, W = 1410, H = 200) {
  const n = fam[0].length, dx = W / n;
  const tot = Array.from({ length: n }, (_, m) => Math.max(1e-9, sum(fam.map((f) => f[m]))));
  const cum = fam.map((f, k) => f.map((_, m) => sum(fam.slice(0, k + 1).map((g) => g[m])) / tot[m]));
  const Y = (c) => H - c * H;
  const at = (m, c) => `${(m * dx).toFixed(1)} ${Y(c).toFixed(1)}`;
  // each month is one block: along the top edge to the right, back along the bottom edge to the left
  const paths = fam.map((_, k) => {
    const top = cum[k], bot = k ? cum[k - 1] : top.map(() => 0);
    const up = [], dn = [];
    for (let m = 0; m < n; m++) up.push(at(m, top[m]), at(m + 1, top[m]));
    for (let m = n - 1; m >= 0; m--) dn.push(at(m + 1, bot[m]), at(m, bot[m]));
    return { family: k, d: `M${up.join('L')}L${dn.join('L')}Z` };
  });
  return { W, H, n, dx, paths, cum };
}

/** Widths (in months) of each calendar year along the ribbon: 12 each, the last year as many as the data has. */
export function yearSpans(startYear, years, months) {
  return years.map((y, i) => ({ year: y, months: i === years.length - 1 ? months - 12 * i : 12 }));
}

/** A nice round axis covering [lo, hi]: {a0, a1, step, ticks:[{v, left}]} (left in percent). Pure (tested). */
export function axisOf(lo, hi, target = 5) {
  const span = Math.max(1e-9, hi - lo), raw = span / target;
  const mag = Math.pow(10, Math.floor(Math.log10(raw)));
  const step = [1, 2, 2.5, 5, 10].map((m) => m * mag).find((s) => s >= raw) || raw;
  const a0 = Math.floor(lo / step) * step, a1 = Math.ceil(hi / step) * step;
  const ticks = [];
  for (let v = a0; v <= a1 + step * 1e-6; v += step) ticks.push({ v: Math.round(v * 1e6) / 1e6, left: (v - a0) / (a1 - a0) * 100 });
  return { a0, a1, step, ticks };
}

/** The name of a siting option: "DFW + TX co-op peak", "NYC (J) merchant", or just the zone when plain. */
export function optName(doc, id, plain = false) {
  const o = doc.siting.options[id];
  return o.program ? `${o.label} + ${o.program}` : plain ? o.label : `${o.label} merchant`;
}
/** [[optionId, mw]] as bar segments (widths in percent of the fleet). Pure (tested). */
export function fleetSegments(doc, list, plain = false) {
  const total = sum(list.map(([, mw]) => mw)) || 1;
  return list.map(([id, mw], rank) => ({ id, rank, mw, pct: mw / total * 100, name: optName(doc, id, plain) }));
}

/** The 2027-31 tournament on one axis: per rule the 20 futures as dots, P10..P90, the mean, the share of futures won. Pure (tested). */
export function forwardLayout(doc, scope, h) {
  const F = doc.siting.forward[scope];
  const all = ALGOS.flatMap((a) => F.all[a][h]);
  const ax = axisOf(Math.min(...all, ...ALGOS.map((a) => F.p10[a][h])), Math.max(...all, ...ALGOS.map((a) => F.p90[a][h])), 5);
  const L = (v) => (v - ax.a0) / (ax.a1 - ax.a0) * 100;
  const rows = ALGOS.map((id) => {
    const sorted = F.all[id][h].map((v, i) => ({ v, i })).sort((x, y) => x.v - y.v || x.i - y.i);
    return {
      id, dots: sorted.map((d, k) => ({ left: L(d.v), row: k % 3 })), p10: L(F.p10[id][h]), p90: L(F.p90[id][h]), mean: L(F.mean[id][h]),
      vals: { mean: F.mean[id][h], p10: F.p10[id][h], p50: F.p50[id][h], p90: F.p90[id][h], win: F.win[id][h] },
    };
  });
  return { ax, rows };
}

/** The site ranking (learner's dispatch), with the stack of the median future on one axis that leaves room for the upgrade cost. */
export function siteRows(doc, scope, limit = Infinity) {
  const S = doc.siting.sites[scope];
  const rows = S.opt.map((id, i) => ({
    rank: i + 1, id, name: optName(doc, id), median: S.median[i], p10: S.p10[i], energy: S.energy[i], capacity: S.capacity[i],
    program: S.program[i], event: S.event[i], upgrades: S.upgrades[i], total: S.total[i],
    gross: S.energy[i] + S.capacity[i] + S.program[i] + S.event[i],
  }));
  // the axis runs from just past the largest upgrade cost (left of zero) to a round number past the largest gross value;
  // only the ticks from zero up are labelled
  const neg = Math.max(0, ...rows.map((r) => -r.upgrades));
  const pos = axisOf(0, Math.max(...rows.map((r) => r.gross)), 5);
  const a0 = neg > 0 ? -Math.ceil(neg / 5) * 5 : 0;
  const L = (v) => (v - a0) / (pos.a1 - a0) * 100;
  const ax = { a0, a1: pos.a1, step: pos.step, ticks: pos.ticks.map((t) => ({ v: t.v, left: L(t.v) })) };
  const shown = rows.slice(0, limit).map((r) => {
    const seg = []; let at = 0;
    for (const [k, key] of [['energy', 'energy'], ['capacity', 'capacity'], ['program', 'program'], ['event', 'event']]) {
      if (r[key] > 0) { seg.push({ k, left: L(at), width: L(at + r[key]) - L(at) }); at += r[key]; }
    }
    return { ...r, seg, cost: r.upgrades < 0 ? { left: L(r.upgrades), width: L(0) - L(r.upgrades) } : null };
  });
  return { ax, zero: L(0), rows: shown, count: rows.length };
}

/** Geometry of one replayed day (viewBox units = quarter hours): the stepped price path and per rule the action bars
 *  and the state-of-charge path. Pure (tested). */
export function dayLayout(doc, item) {
  const E = konst(doc, 'FULL_UNITS').v, R = konst(doc, 'RESERVE_UNITS').v;
  const pmax = Math.max(...item.price, 1), pmin = Math.min(...item.price, 0);
  const ax = axisOf(pmin, pmax, 4);
  const H = 100, Y = (v) => H - (v - ax.a0) / (ax.a1 - ax.a0) * H;
  const step = (arr, f) => arr.map((v, t) => `${t ? 'L' : 'M'}${t} ${f(v).toFixed(1)}H${t + 1}`).join('');
  const price = step(item.price, Y);
  const rows = ALGOS.map((id) => {
    const a = item.act[id], s = item.soc[id], bars = { chg: '', dis: '' };
    a.forEach((u, t) => {
      if (!u) return;
      const h = Math.abs(u) / 2 * 15;
      if (u > 0) bars.dis += `M${t} 17V${(17 - h).toFixed(1)}h1V17Z`; else bars.chg += `M${t} 17V${(17 + h).toFixed(1)}h1V17Z`;
    });
    const soc = `M0 62${s.map((v, t) => `V${(62 - v / E * 24).toFixed(1)}H${t + 1}`).join('')}V62Z`;
    const dis = a.reduce((x, u) => x + Math.max(u, 0), 0) * konst(doc, 'UNIT_MWH').v;
    const chg = a.reduce((x, u) => x + Math.max(-u, 0), 0) * konst(doc, 'UNIT_MWH').v;
    const sold = a.reduce((x, u, t) => x + Math.max(u, 0) * item.price[t], 0) * konst(doc, 'UNIT_MWH').v;
    const bought = a.reduce((x, u, t) => x + Math.max(-u, 0) * item.price[t], 0) * konst(doc, 'UNIT_MWH').v;
    return { id, bars, soc, reserve: 62 - R / E * 24, soldMWh: dis, boughtMWh: chg, avgSell: dis > 0 ? sold / dis : null, avgBuy: chg > 0 ? bought / chg : null };
  });
  return { ax, H, price, rows, ticks: ax.ticks.map((t) => ({ v: t.v, top: 100 - t.left })) };
}

const HOURS = [0, 6, 12, 18, 24];
/** 15 and 18 -> "3–6 pm"; 9 and 14 -> "9 am–2 pm". Pure (tested). */
export function hourRange(a, b) {
  const t = (h) => `${h % 12 || 12} ${h % 24 < 12 ? 'am' : 'pm'}`;
  return (a % 24 < 12) === (b % 24 < 12) || b === 24 ? `${(a % 12) || 12}–${t(b)}` : `${t(a)}–${t(b)}`;
}

// ---------------------------------------------------------------------------------------------- shared HTML pieces
const sTag = (doc, key) => { const s = doc.series[key]; return tagHTML(s.label, s.text); };
const dTag = (cite) => tagHTML('DERIVED', `DERIVED: ${cite}`);
const periodTag = (doc, period, key = 'y') => (period === 'mean' ? dTag(`mean of the ${windowText(doc)} yearly values, each SIM`) : sTag(doc, key));
const yr = lbl;
const periodText = (doc, period) => (period === 'mean' ? yr(windowText(doc)) : yr(period));

function headHTML(eyebrow, title, sub) {
  // eyebrow is HTML (the caller escapes; a count is a <span class="num">)
  return `<header class="ar-head"><div class="st-eyebrow">${eyebrow}</div><h1 class="st-title ar-h1">${esc(title)}</h1><p class="ar-headsub">${sub}</p></header>`;
}
const panel = (label, inner, cls = '') => `<section class="ar-panel ar-card ${cls}" aria-label="${esc(label)}">${inner}</section>`;
const tile = (k, v, s) => `<div class="ar-panel ar-kpi"><span class="ar-sub">${esc(k)}</span><span class="ar-kpi-v">${v}</span><span class="ar-note">${s}</span></div>`;

function batteryNote(doc, spec, num) {
  if (spec === 'home') {
    return `Home fleet: a ${num(konst(doc, 'BATTERY_KW'), { digits: 0, unit: ' kW' })} Core-class battery that keeps a ${num(konst(doc, 'RESERVE_FLOOR'), { digits: 0, unit: '%' })} member backup reserve at all times, `
      + `${num(konst(doc, 'ROUND_TRIP'), { digits: 2 })} round trip, ${num(konst(doc, 'WEAR_USD_MWH'), { money: true, digits: 0, unit: '/MWh' })} wear on every MWh sold.`;
  }
  return `Grid-scale: a ${num(konst(doc, 'GRID_MW'), { digits: 0, unit: ' MW' })} / ${num(konst(doc, 'GRID_MWH'), { digits: 0, unit: ' MWh' })} merchant battery with no reserve, `
    + `${num(konst(doc, 'GRID_ROUND_TRIP'), { digits: 2 })} round trip, ${num(konst(doc, 'WEAR_USD_MWH'), { money: true, digits: 0, unit: '/MWh' })} wear.`;
}
const footHTML = (doc, spec, num, extra = '') => `<footer class="ar-foot"><p>Prices are real; the dispatch is simulated (SIM). Gross trading value per kW of battery per year, not Base’s profit. ${batteryNote(doc, spec, num)} ${extra}</p>
  <ul class="ar-cav" aria-label="Caveats">${doc.meta.caveats.map((c) => `<li>${esc(c)}</li>`).join('')}</ul></footer>`;

// ---------------------------------------------------------------------------------------------- rail
export function railHTML(doc, st) {
  const M = doc.markets[st.mk];
  const num = (v, d = 1) => bulk(v, d);
  const q3 = st.view === 3;
  const seg = (q3 ? SCOPES3 : MARKETS).map(([id, t]) => {
    const on = q3 ? id === st.scope3 : id === st.mk;
    return `<button type="button" data-mk="${id}" data-fk="mk:${id}" class="${on ? 'on' : ''}" aria-pressed="${on}">${esc(t)}</button>`;
  }).join('');
  const views = VIEWS.map((v) => {
    const fill = (x) => esc(x).replace('{SITE_MW}', `${bulk(konst(doc, 'SITE_MW').v, 0)} MW`).replace('{FORWARD}', lbl(horizonLabel(doc, doc.siting.horizons.length - 1)));
    return `<button type="button" class="ar-view${v.q === st.view ? ' on' : ''}" data-q="${v.q}" data-fk="q:${v.q}" aria-pressed="${v.q === st.view}">
      <span class="ar-qn num">${v.q}</span><span><span class="ar-qt">${fill(v.title)}</span><span class="ar-qs">${fill(v.sub)}</span></span></button>`;
  }).join('');
  const specSeg = st.view <= 2 ? `<div class="ar-rail-h st-eyebrow sm">BATTERY</div><div class="cfg-seg ar-seg" role="group" aria-label="Battery">${SPECS.map(([id, t]) =>
    `<button type="button" data-spec="${id}" data-fk="spec:${id}" class="${id === st.spec ? 'on' : ''}" aria-pressed="${id === st.spec}">${esc(t)}</button>`).join('')}</div>` : '';
  let zones = '';
  if (st.view <= 2) {
    const es = zoneEntries(doc, st.mk);
    const vals = es.map((e) => valueOf(doc, st.mk, e, st.spec, LEARNER, 'mean'));
    const mx = Math.max(...vals, 1e-9);
    zones = `<div class="ar-rail-h st-eyebrow sm">${st.mk === 'ERCOT' ? 'LOAD ZONES' : 'ZONES A–K'} · LEARNER ${periodText(doc, 'mean')}</div>
      <div class="ar-zones">${es.map((e, i) => `<button type="button" class="ar-zone${e.id === st.zone[st.mk] ? ' on' : ''}${e.id === 'ALL' ? ' all' : ''}" data-zone="${esc(e.id)}" data-fk="zone:${esc(e.id)}" aria-pressed="${e.id === st.zone[st.mk]}">
        <span class="ar-zone-t"><span>${e.id === 'ALL' ? `All ${bulk(M.zones.length, 0)} zones` : esc(e.label)}</span><span class="num">$${esc(fmtN(vals[i]))}</span></span>
        <span class="ar-bar"><i style="width:${(vals[i] / mx * 100).toFixed(1)}%"></i></span></button>`).join('')}</div>
      <div class="ar-note ar-rail-note">Bar and $ = the Adaptive learner, mean of ${periodText(doc, 'mean')}, $/kW-yr ${dTag('mean of the yearly values, each SIM')}. “All zones” is the mean of the zones.</div>`;
  }
  return `<aside class="ar-rail" aria-label="Arena controls"><div class="st-eyebrow">ARENA</div>
    <div class="ar-q">How should a battery trade?</div>
    <div class="ar-qsub">Five dispatch rules raced on real ERCOT and NYISO prices. The prices are real; the dispatch is simulated.</div>
    <nav class="ar-views" aria-label="Views">${views}</nav>
    <div class="ar-rail-h st-eyebrow sm">MARKET</div><div class="cfg-seg ar-seg" role="group" aria-label="Market">${seg}</div>${specSeg}${zones}</aside>`;
}

// ---------------------------------------------------------------------------------------------- q=1 head to head
function periodButtons(doc, M, st) {
  const btn = (p, inner) => `<button type="button" data-period="${p}" data-fk="period:${p}" class="${String(st.period) === String(p) ? 'on' : ''}" aria-pressed="${String(st.period) === String(p)}">${inner}</button>`;
  return `<div class="ar-periods" role="group" aria-label="Period">${btn('mean', `${yr(windowText(doc))} mean`)}`
    + M.years.map((y, i) => btn(y, `${yr(y)}${M.partial[i] ? ' to date' : ''}`)).join('') + '</div>';
}

/** The one sentence under the q1 title. */
export function answer1(doc, st, race, num) {
  const E = entryOf(doc, st.mk, st.zone[st.mk]);
  const where = E.id === 'ALL' ? `across ${st.mk}’s zones` : `in ${E.label}`;
  const L = race.learner, c = (v, k = 'y') => yLab(doc, k, v, st.period);
  const lead = race.rows.find((r) => r.id === race.lead);
  const share = derived(Math.round(L.share), 'learner ÷ perfect-foresight ceiling');
  const tail = `${num(share, PCT0)} of the ${num(c(race.ceil, 'ceiling'), MONEY)} that a perfect-foresight battery would have made`;
  const when = st.period === 'mean' ? `Over ${periodText(doc, st.period)} ${where},` : `In ${periodText(doc, st.period)} ${where},`;
  if (race.lead === LEARNER) {
    const bf = race.rows.find((r) => r.id === race.bestFixed);
    return `${when} the Adaptive learner earned the most: ${num(c(L.v), MONEY)} per kW-year, ${tail}. The best single rule, ${esc(bf.name)}, earned ${num(c(bf.v), MONEY)}.`;
  }
  return `${when} ${esc(lead.name)} earned the most: ${num(c(lead.v), MONEY)} per kW-year. The Adaptive learner earned ${num(c(L.v), MONEY)}, ${tail}.`;
}

export function view1HTML(doc, st, num) {
  const mk = st.mk, M = doc.markets[mk], E = entryOf(doc, mk, st.zone[mk]);
  const race = raceRows(doc, mk, E, st.spec, st.period);
  const c = (v, k = 'y') => yLab(doc, k, v, st.period);
  const scope = E.id === 'ALL' ? `ALL ${bulk(M.zones.length, 0)} ZONES` : esc(E.label.toUpperCase());
  const maxv = Math.max(race.ceil, ...race.rows.map((r) => r.v), 1e-9);
  const bf = race.rows.find((r) => r.id === race.bestFixed), L = race.learner;
  const lead = derived(L.v - bf.v, `learner minus ${bf.name}, same period`);
  const rowsHTML = race.rows.map((r) => {
    const a = ruleOf(doc, r.id);
    return `<div class="ar-rrow${r.id === LEARNER ? ' ours' : ''}${r.id === race.lead ? ' lead' : ''}" role="row">
      <div class="ar-rname" role="cell"><b>${esc(a.name)}${r.id === race.lead ? ' <span class="ar-lead">best this period</span>' : ''}</b><span class="ar-note">${esc(a.short)}</span></div>
      <div class="ar-rtrack" role="cell" aria-label="${esc(`${a.name}: $${fmtN(r.v)} per kW-year, ${Math.round(r.share)}% of the ceiling`)}"><i class="ar-rbar" style="width:${Math.max(0, r.v / maxv * 100).toFixed(1)}%"></i></div>
      <div class="ar-rval" role="cell">${bulk(r.v)}</div><div class="ar-rcap" role="cell">${bulkU(r.share, 0, '%')}</div></div>`;
  }).join('');
  const rules = race.rows.map((r) => `<li><b>${esc(r.name)}.</b> ${fillTokens(doc, ruleOf(doc, r.id).rule, num)}</li>`).join('');
  const lg = leagueRows(doc, mk, st.spec, st.period);
  const best = (row, id) => row.v[id] >= Math.max(...ALGOS.map((a) => row.v[a])) - 1e-9;
  const tab = lg.map((row) => `<div class="ar-tr${row.id === st.zone[mk] ? ' on' : ''}${row.id === 'ALL' ? ' all' : ''}" role="row">
      <span role="cell"><button type="button" class="ar-zbtn" data-zone="${esc(row.id)}" data-fk="zrow:${esc(row.id)}" aria-pressed="${row.id === st.zone[mk]}">${esc(row.id === 'ALL' ? 'All zones' : row.label)}</button></span>
      ${ALGOS.map((a) => `<span role="cell" class="${best(row, a) ? 'best' : ''}">${bulk(row.v[a])}</span>`).join('')}
      <span role="cell" class="ceil">${bulk(row.ceil)}</span><span role="cell">${bulkU(row.share, 0, '%')}</span><span role="cell" class="who">${esc(nameOf(doc, row.lead))}</span></div>`).join('');
  const cap = derived(Math.round(L.share), 'learner ÷ perfect-foresight ceiling');
  const worst = race.rows.reduce((b, r) => (r.v < b.v ? r : b), race.rows[0]);
  const ruleCaps = derived(Math.round(worst.share), `${worst.name} ÷ perfect-foresight ceiling`);
  const reads = [
    ['The ceiling.', `Perfect foresight knows every price of the day before it trades. No rule can, so the ceiling is a yardstick, not a target: the best real rule keeps ${num(derived(Math.round(Math.max(...race.rows.map((r) => r.share))), 'best rule ÷ ceiling'), PCT0)} of it.`],
    ['The learner.', race.lead === LEARNER
      ? `It beat the best single rule, ${esc(bf.name)}, by ${sNum(num, lead)} per kW-year, without knowing in advance which rule would work.`
      : `It trailed ${esc(race.rows.find((r) => r.id === race.lead).name)} by ${num(derived(race.rows.find((r) => r.id === race.lead).v - L.v, 'leader minus learner'), MONEY)}. Its case is not to win every period but never to need to know in advance which rule will.`],
    ['The laggard.', `${esc(worst.name)} kept only ${num(ruleCaps, PCT0)} of the ceiling ${esc(worst.id === 'A1' ? 'because a fixed window cannot follow the weather' : worst.id === 'A4' ? 'because it waits for spikes that come rarely' : 'in this period')}.`],
  ];
  return `${headHTML(`${mk} · ${scope} · ${specName(st.spec).toUpperCase()}`, 'Five rules against perfect foresight', answer1(doc, st, race, num))}
    <section class="ar-panel ar-card" aria-label="Profit per rule">
      <div class="ar-card-h"><span class="ar-ct">Profit per kW of battery per year, $/kW-yr</span>
        <span class="ar-legend"><span><i class="ar-sw-bar"></i>Adaptive learner</span><span><i class="ar-sw-other"></i>Other rules</span><span><i class="ar-sw-ceil"></i>Perfect-foresight ceiling ${periodTag(doc, st.period, 'ceiling')}</span></span></div>
      ${periodButtons(doc, M, st)}
      <div class="ar-race" role="table" aria-label="Profit per rule against the ceiling">
        <div class="ar-rrow ar-th" role="row"><span role="columnheader">Rule</span><span role="columnheader">Bar = share of the ceiling, full width = the ceiling ${num(c(race.ceil, 'ceiling'), MONEY)}</span><span role="columnheader">Earned ${periodTag(doc, st.period)}</span><span role="columnheader">Share ${dTag('rule ÷ perfect-foresight ceiling, same period')}</span></div>
        ${rowsHTML}
      </div>
      <div class="ar-note">Click a year to see it alone. “To date” means the year so far, to ${lbl(dateLabel(M.lastDate, { weekday: false }))}.</div>
    </section>
    <section class="ar-kpis" aria-label="Headline">
      ${tile('Adaptive learner', num(c(L.v), MONEY), `/kW-yr, ${periodText(doc, st.period)}`)}
      ${tile('Perfect foresight', num(c(race.ceil, 'ceiling'), MONEY), '/kW-yr, the most any rule could earn')}
      ${tile('Learner’s share', num(cap, PCT0), 'of the ceiling')}
      ${tile(`Best single rule: ${bf.name}`, num(c(bf.v), MONEY), `/kW-yr. The learner is ${sNum(num, lead)} against it.`)}
    </section>
    <div class="ar-two">
      <section class="ar-panel ar-card" aria-label="What each rule does"><span class="ar-ct">What each rule does</span><span class="ar-sub">One sentence each. The numbers in them are the arena’s own parameters ${paramTag()}.</span><ul class="ar-rules">${rules}</ul></section>
      <section class="ar-panel ar-card" aria-label="Reads"><span class="ar-ct">Three reads</span><span class="ar-sub">Each computed from the numbers above.</span>
        <ol class="ar-reads">${reads.map((r, i) => `<li><span class="ar-rn num">0${i + 1}</span><span><b>${esc(r[0])}</b> ${r[1]}</span></li>`).join('')}</ol></section>
    </div>
    <section class="ar-panel ar-card" aria-label="League across zones">
      <div class="ar-card-h"><span class="ar-ct">The league across zones, $/kW-yr, ${periodText(doc, st.period)}</span><span class="ar-legend"><span>Bold = best rule in the zone</span><span>Click a zone to focus it</span></span></div>
      <div class="ar-table" role="table" aria-label="Profit by zone and rule">
        <div class="ar-tr ar-th" role="row"><span role="columnheader">Zone</span>${ALGOS.map((a) => `<span role="columnheader">${esc(nameOf(doc, a))}</span>`).join('')}<span role="columnheader">Ceiling ${periodTag(doc, st.period, 'ceiling')}</span><span role="columnheader">Learner share ${dTag('learner ÷ ceiling')}</span><span role="columnheader">Best rule</span></div>
        ${tab}
      </div>
      <div class="ar-note">Rule columns carry ${periodTag(doc, st.period)}. The first row is the mean of the zones. Pale-grey figures are the ceiling.</div>
    </section>
    ${footHTML(doc, st.spec, num)}`;
}

// ---------------------------------------------------------------------------------------------- q=2 who wins when
/** The sentence under the q2 title. */
export function answer2(doc, st, wm, num) {
  const M = doc.markets[st.mk], names = wm.winners.map((id) => nameOf(doc, id));
  const best = ALGOS.reduce((b, a) => (wm.mean.v[a] > wm.mean.v[b] ? a : b), ALGOS[0]);
  const n = wm.nWinners;
  const count = n === 1 ? 'One rule' : `${['Zero', 'One', 'Two', 'Three', 'Four', 'Five'][n]} different rules`;
  return `${count} won a year between ${yr(doc.meta.window[0])} and ${yr(doc.meta.window[1])} in ${st.mk} (${esc(names.join(', '))}). Over all five together, ${esc(nameOf(doc, best))} earned the most, `
    + `${num(derived(wm.mean.v[best], `mean of the ${windowText(doc)} yearly values, each SIM`), MONEY)} per kW-year.`;
}

export function view2HTML(doc, st, num) {
  const mk = st.mk, M = doc.markets[mk], E = entryOf(doc, mk, st.zone[mk]), spec = st.spec;
  const wm = winMap(doc, mk, E, spec);
  const sel = wm.cols.find((c) => c.year === st.year2) || wm.cols[0];
  const scope = E.id === 'ALL' ? `ALL ${bulk(M.zones.length, 0)} ZONES` : esc(E.label.toUpperCase());
  const W = doc.meta.window;
  const shade = (share) => Math.max(4, Math.min(86, share * 1.15));
  const cell = (c, a, win) => `<span role="cell" class="ar-wc${win ? ' win' : ''}${c.share[a] > 50 ? ' dark' : ''}${c.partial ? ' partial' : ''}" style="background:color-mix(in srgb, var(--brand) ${shade(c.share[a]).toFixed(0)}%, var(--panel))" title="${esc(`${nameOf(doc, a)}: $${fmtN(c.v[a])} per kW-year, ${Math.round(c.share[a])}% of the ceiling`)}">${bulk(c.v[a])}</span>`;
  const head = wm.cols.map((c) => `<span role="columnheader" class="ar-wh${c.year === sel.year ? ' on' : ''}${c.year >= W[0] && c.year <= W[1] ? ' inw' : ''}">
      <button type="button" data-year2="${c.year}" data-fk="y2:${c.year}" aria-pressed="${c.year === sel.year}" aria-label="Show ${c.year}${c.event ? ': ' + esc(c.event.name) : ''}"><span class="num">${c.year}</span>${c.partial ? '<i>to date</i>' : ''}</button>
      <em>${c.event ? esc(c.event.name) : ''}</em></span>`).join('');
  const rows = ALGOS.map((a) => `<div class="ar-wr${a === LEARNER ? ' ours' : ''}" role="row"><span role="rowheader">${esc(nameOf(doc, a))}</span>
      ${wm.cols.map((c) => cell(c, a, c.win === a)).join('')}<span class="ar-gap"></span>${cell(wm.mean, a, wm.mean.win === a)}</div>`).join('');
  const ceilRow = `<div class="ar-wr ceil" role="row"><span role="rowheader">Perfect foresight</span>${wm.cols.map((c) => `<span role="cell" class="ar-wc ceil">${bulk(c.ceil)}</span>`).join('')}<span class="ar-gap"></span><span role="cell" class="ar-wc ceil">${bulk(wm.mean.ceil)}</span></div>`;
  const whoRow = `<div class="ar-wr who" role="row"><span role="rowheader">Best rule</span>${wm.cols.map((c) => `<span role="cell" class="ar-who${c.win === LEARNER ? ' ours' : ''}">${esc(nameOf(doc, c.win))}</span>`).join('')}<span class="ar-gap"></span><span role="cell" class="ar-who${wm.mean.win === LEARNER ? ' ours' : ''}">${esc(nameOf(doc, wm.mean.win))}</span></div>`;
  // the selected year
  const order = ALGOS.slice().sort((x, y) => sel.v[y] - sel.v[x]);
  const ev = sel.event;
  const selHTML = `<div class="ar-sel"><div class="ar-sel-h"><b>${yr(sel.year)}${sel.partial ? ' to date' : ''}${ev ? ` · ${esc(ev.name)}` : ''}</b>${ev ? tagHTML(ev.label, ev.cite) : ''}</div>
      <div class="ar-text">${ev ? `${esc(ev.note)} ` : ''}${esc(nameOf(doc, sel.win))} led with ${num(fromSeries(doc, 'y', sel.v[sel.win]), MONEY)} per kW-year`
        + `${sel.win === LEARNER ? '.' : `; the Adaptive learner finished ${['first', 'second', 'third', 'fourth', 'fifth'][order.indexOf(LEARNER)]} with ${num(fromSeries(doc, 'y', sel.v[LEARNER]), MONEY)}.`}`
        + ` The ceiling was ${num(fromSeries(doc, 'ceiling', sel.ceil), MONEY)}.</div>
      <div class="ar-order">${order.map((a, i) => `<span class="${a === LEARNER ? 'ours' : ''}"><i class="num">${i + 1}</i>${esc(nameOf(doc, a))} ${bulk(sel.v[a])}</span>`).join('')}<span>${sTag(doc, 'y')}</span></div></div>`;
  // the ribbon
  const D = E[spec], rb = ribbonLayout(D.fam);
  const fams = doc.meta.learner.families;
  const spans = yearSpans(M.years[0], M.years, rb.n);
  const maxSw = Math.max(...D.switches, 1);
  const evCols = (M.events || []).map((e) => ({ e, left: yearSpans(M.years[0], M.years, rb.n).slice(0, M.years.indexOf(e.year)).reduce((s, x) => s + x.months, 0) / rb.n * 100 }));
  const famShares = doc.meta.learner.families.map((name, f) => ({ name, pct: sum(doc.meta.learner.experts.map((x, i) => (x.family === f ? D.days[i] : 0))) })).sort((a, b) => b.pct - a.pct);
  const swMean = avg(D.switches.slice(0, -1));
  const swLo = Math.min(...D.switches.slice(0, -1)), swHi = Math.max(...D.switches.slice(0, -1));
  const ribbon = `<div class="ar-ribwrap"><div class="ar-ribplot"><svg viewBox="0 0 ${rb.W} ${rb.H}" preserveAspectRatio="none" aria-hidden="true">
        ${rb.paths.map((p) => `<path d="${p.d}" class="ar-f${p.family}"></path>`).join('')}</svg>
        ${evCols.map((x) => `<span class="ar-evline${x.left > 80 ? ' late' : ''}" style="left:${x.left.toFixed(2)}%"><i>${esc(x.e.name)}</i></span>`).join('')}
      </div>
      <div class="ar-ribyears">${spans.map((s) => `<span style="flex:${s.months}"><span class="num">${s.year}</span></span>`).join('')}</div>
      <div class="ar-swrow" aria-label="Switches per year">${spans.map((s, i) => `<span style="flex:${s.months}"><span class="bw"><i style="height:${(D.switches[i] / maxSw * 100).toFixed(0)}%"></i></span><span class="num">${fmtN(D.switches[i], 0)}</span></span>`).join('')}</div></div>`;
  const reads = [
    ['No rule is always right.', `${['', 'One', 'Two', 'Three', 'Four', 'Five'][wm.nWinners]} of the five rules finished first in a year of ${periodText(doc, 'mean')}: ${wm.inWin.map((c) => `${yr(c.year)} ${esc(nameOf(doc, c.win))}`).join(', ')}.`],
    ['The learner does not choose; it follows.', `Over all its days it ran ${famShares.slice(0, 3).map((f) => `${esc(f.name)} on ${bulkU(f.pct, 0, '%')}`).join(', ')}: it hands the battery to whichever rule has earned the most lately. Shares carry ${sTag(doc, 'days')}.`],
    ['It switches, but not at random.', `It changed which rule it follows about ${bulk(swMean, 0)} times a year (from ${bulk(swLo, 0)} to ${bulk(swHi, 0)}), and only when another rule led clearly. Counts carry ${sTag(doc, 'switches')}.`],
    ['Five years, one winner.', wm.mean.win === LEARNER
      ? `${esc(nameOf(doc, wm.mean.win))} earned the most over ${periodText(doc, 'mean')}, although it won only ${bulk(wm.inWin.filter((c) => c.win === LEARNER).length, 0)} of those years outright. The learner wins the long race because no single rule wins every year.`
      : `${esc(nameOf(doc, wm.mean.win))} earned the most over ${periodText(doc, 'mean')} here, ${num(derived(wm.mean.v[wm.mean.win] - wm.mean.v[LEARNER], 'leader minus learner, mean of the window'), MONEY)} ahead of the Adaptive learner. The learner’s case is the market as a whole, where it leads, not every single zone.`],
  ];
  return `${headHTML(`${mk} · ${scope} · ${specName(spec).toUpperCase()}`, 'No single rule wins every year', answer2(doc, st, wm, num))}
    <section class="ar-panel ar-card" aria-label="Win map">
      <div class="ar-card-h"><span class="ar-ct">Who won each year, $/kW-yr ${sTag(doc, 'y')}</span>
        <span class="ar-legend"><span><i class="ar-sw-heat"></i>Darker = larger share of the ceiling</span><span><i class="ar-sw-win"></i>Outlined = best rule that year</span><span>Click a year to read it</span></span></div>
      <div class="ar-wmap" role="table" aria-label="Year by rule win map">
        <div class="ar-wr ar-wmh" role="row"><span role="columnheader"></span>${head}<span class="ar-gap"></span><span role="columnheader" class="ar-wh mean"><b>${yr(windowText(doc))} mean</b><em>five years together ${dTag('mean of the yearly values, each SIM')}</em></span></div>
        ${rows}${ceilRow}${whoRow}
      </div>
      <div class="ar-note">Underlined years are the ${yr(windowText(doc))} scoring window; the last year runs to ${lbl(dateLabel(M.lastDate, { weekday: false }))}. Events are named from docs/research ${tagHTML(M.events[0].label, M.events[0].cite)}.</div>
      ${selHTML}
    </section>
    <section class="ar-panel ar-card" aria-label="The learner's picks">
      <div class="ar-card-h"><span class="ar-ct">Which rule the learner followed, by month</span>
        <span class="ar-legend">${fams.map((n, i) => `<span><i class="ar-sw-f${i}"></i>${esc(n)}</span>`).join('')}<span>${sTag(doc, 'fam')}</span></span></div>
      <span class="ar-sub">Share of days each month, ${E.id === 'ALL' ? `mean of the ${bulk(M.zones.length, 0)} zones` : esc(E.label)}. The two Spike hunter variants and the two Forecaster variants are grouped with their family. Below: how many times a year it switched ${sTag(doc, 'switches')}.</span>
      ${ribbon}
    </section>
    <section class="ar-panel ar-card" aria-label="Reads"><span class="ar-ct">Four reads</span><span class="ar-sub">Each computed from the map and the ribbon.</span>
      <ol class="ar-reads">${reads.map((r, i) => `<li><span class="ar-rn num">0${i + 1}</span><span><b>${esc(r[0])}</b> ${r[1]}</span></li>`).join('')}</ol></section>
    ${footHTML(doc, spec, num)}`;
}

// ---------------------------------------------------------------------------------------------- q=3 placing the next 10 MW
export const horizonLabel = (doc, h) => {
  const y0 = doc.siting.start, n = doc.siting.horizons[h];
  return n === 1 ? String(y0) : `${y0}–${String(y0 + n - 1).slice(2)}`;
};
const fleetBar = (doc, list, ours, plain, withLabels = true) => {
  const segs = fleetSegments(doc, list, plain);
  return `<span class="ar-fbar${ours ? ' ours' : ''}" role="img" aria-label="${esc(segs.map((s) => `${s.name} ${fmtN(s.mw, Number.isInteger(s.mw) ? 0 : 1)} MW`).join(', '))}">${segs.map((s) => `<i class="r${Math.min(s.rank, 4)}" style="width:${s.pct.toFixed(2)}%">${withLabels && s.pct >= 22 ? `<span>${idHTML(s.name)}</span>` : ''}</i>`).join('')}</span>`;
};
/** The largest few options of a fleet as text, the rest summed: "A 30 · B 10 · and 3 smaller (1.5)". Pure (tested). */
export function fleetParts(doc, list, plain = false, max = 4) {
  const segs = fleetSegments(doc, list, plain);
  const top = segs.slice(0, max), rest = segs.slice(max);
  return { top, rest, restMw: sum(rest.map((s) => s.mw)) };
}
const fleetList = (doc, list, plain) => {
  const f = fleetParts(doc, list, plain), n = (v) => bulk(v, Number.isInteger(v) ? 0 : 1);
  return f.top.map((s) => `<span>${idHTML(s.name)} ${n(s.mw)}</span>`).join('') + (f.rest.length ? `<span>and ${bulk(f.rest.length, 0)} smaller, ${n(Math.round(f.restMw * 10) / 10)} in all</span>` : '');
};

export function view3HTML(doc, st, num) {
  const sc = st.scope3, h = st.hz, S = doc.siting;
  const B = S.backtest[sc], F = S.forward[sc];
  const scopeName = (SCOPES3.find(([k]) => k === sc) || SCOPES3[0])[2];
  const end = S.backtestEnd;
  const fl = forwardLayout(doc, sc, h);
  const wonBy = ALGOS.reduce((b, a) => (F.win[a][h] > F.win[b][h] ? a : b), ALGOS[0]);
  const hz = (i) => yr(horizonLabel(doc, i));
  const sr = doc.sitingRules;
  // the rules and the backtest fleets
  const rules = sr.map((r) => {
    const ours = r.id === LEARNER, y5 = B.y[r.id][2];
    return `<div class="ar-srow${ours ? ' ours' : ''}" role="row"><div role="cell" class="ar-sname"><b>${esc(r.name)}</b><span class="ar-note">${esc(r.owner)}’s siting</span></div>
      <div role="cell" class="ar-stext">${fillTokens(doc, r.rule, num)}</div>
      <div role="cell" class="ar-sval">${bulk(y5)}</div>
      <div role="cell" class="ar-sfleet">${fleetBar(doc, B.fleet[r.id], ours, true)}<span class="ar-flist">${fleetList(doc, B.fleet[r.id], true)}</span></div></div>`;
  }).join('');
  const ts = B.y.A5ts, a5 = B.y[LEARNER];
  const first = (a) => F.first[a], fleet31 = (a) => F.fleet[a];
  const strip = fl.rows.map((r) => {
    const ours = r.id === LEARNER, name = sr.find((x) => x.id === r.id);
    const ticks = fl.ax.ticks.map((t) => `<i class="g" style="left:${t.left.toFixed(2)}%"></i>`).join('');
    return `<div class="ar-frow${ours ? ' ours' : ''}" role="row"><div role="cell" class="ar-sname"><b>${esc(nameOf(doc, r.id))}</b><span class="ar-note">${esc(name.name)}</span></div>
      <div role="cell" class="ar-strip" aria-label="${esc(`${name.name}: mean $${fmtN(r.vals.mean)}, P10 $${fmtN(r.vals.p10)}, P90 $${fmtN(r.vals.p90)} per kW-year, wins ${r.vals.win}% of futures`)}">${ticks}
        <i class="b" style="left:${r.p10.toFixed(2)}%;width:${(r.p90 - r.p10).toFixed(2)}%"></i>${r.dots.map((d) => `<i class="d d${d.row}" style="left:${d.left.toFixed(2)}%"></i>`).join('')}<i class="m" style="left:${r.mean.toFixed(2)}%"></i></div>
      <div role="cell" class="ar-fnum">${bulk(r.vals.mean)}</div><div role="cell" class="ar-fnum">${bulk(r.vals.p10)}–${bulk(r.vals.p90)}</div>
      <div role="cell" class="ar-fwin"><span class="ar-wbar"><i style="width:${r.vals.win}%"></i></span>${bulkU(r.vals.win, 0, '%')}</div>
      <div role="cell" class="ar-sfleet">${fleetBar(doc, fleet31(r.id), ours, false)}<span class="ar-flist">${fleetList(doc, fleet31(r.id), false)}</span></div></div>`;
  }).join('');
  // transmission
  const inf = doc.infrastructure.filter((x) => sc === 'BOTH' || x.market === sc);
  const iax = axisOf(Math.min(0, ...inf.map((x) => x.effect.v)), Math.max(0, ...inf.map((x) => x.effect.v)), 4);
  const iL = (v) => (v - iax.a0) / (iax.a1 - iax.a0) * 100;
  const infHTML = inf.map((x) => {
    const v = x.effect.v, l = Math.min(iL(0), iL(v)), w = Math.abs(iL(v) - iL(0));
    return `<div class="ar-irow" role="row"><div role="cell" class="ar-sname"><b>${idHTML(x.event)}</b><span class="ar-note">${esc(x.zone)}</span></div>
      <div role="cell" class="ar-itrack">${iax.ticks.map((t) => `<i class="g" style="left:${t.left.toFixed(2)}%"></i>`).join('')}<i class="z" style="left:${iL(0).toFixed(2)}%"></i><i class="ibar ${v < 0 ? 'neg' : 'pos'}" style="left:${l.toFixed(2)}%;width:${w.toFixed(2)}%"></i></div>
      <div role="cell" class="ar-inum">${sUsdB(x.effect.v)}</div>
      <div role="cell" class="ar-ifut">${bulk(x.with.v, 0)} futures with it, ${bulk(x.without.v, 0)} without</div></div>`;
  }).join('');
  // the site ranking and the programs
  const sites = siteRows(doc, sc, st.showAll ? Infinity : 12);
  const siteHTML = sites.rows.map((r) => `<div class="ar-krow${r.rank === 1 ? ' top' : ''}" role="row"><span role="cell" class="ar-krank">${bulk(r.rank, 0)}</span>
      <span role="cell" class="ar-kname">${idHTML(r.name)}</span>
      <span role="cell" class="ar-ktrack">${sites.ax.ticks.map((t) => `<i class="g" style="left:${t.left.toFixed(2)}%"></i>`).join('')}<i class="z" style="left:${sites.zero.toFixed(2)}%"></i>
        ${r.seg.map((s) => `<i class="s-${s.k}" style="left:${s.left.toFixed(2)}%;width:${s.width.toFixed(2)}%"></i>`).join('')}${r.cost ? `<i class="s-cost" style="left:${r.cost.left.toFixed(2)}%;width:${r.cost.width.toFixed(2)}%"></i>` : ''}</span>
      <span role="cell" class="ar-knum">${bulk(r.median)}</span><span role="cell" class="ar-knum">${bulk(r.p10)}</span></div>`).join('');
  const progs = doc.programs.filter((p) => sc === 'BOTH' || p.market === sc);
  const progHTML = progs.map((p) => `<div class="ar-prow" role="row"><span role="cell" class="ar-pname"><b>${idHTML(p.name)}</b><span class="ar-note">${esc(p.zones.join(', '))}</span></span>
      <span role="cell">${num(p.pays, { ...MONEY, digits: 0, unit: '/kW-yr' })}</span>
      <span role="cell">${p.window ? lbl(hourRange(p.window[0], p.window[1])) : 'utility dispatches'}${p.events.v ? `, ${bulk(p.events.v, 0)} event days` : ''}</span>
      <span role="cell">from ${yr(p.starts)}, ${bulkU(p.chance.v, 0, '%')} chance</span><span role="cell">${p.needsUpgrades ? 'needs upgrades' : 'no upgrades'}</span></div>`).join('');
  const f = doc.feeder;
  const where = sc === 'ERCOT' ? 'in ERCOT' : sc === 'NYISO' ? 'in New York' : 'across both markets';
  const sub = `Every January each rule places the next ${num(konst(doc, 'SITE_MW'), { digits: 0, unit: ' MW' })}. In the ${hz(h)} tournament ${esc(nameOf(doc, wonBy))}’s rule won `
    + `${num(fromSeries(doc, 'fw.win', F.win[wonBy][h]), PCT0)} of the ${num(konst(doc, 'FUTURES'), { digits: 0 })} futures ${where}; contracts, capacity and transmission decide where the money is.`;
  return `${headHTML(esc(`${scopeName.toUpperCase()} · HOME FLEET · SITING`), 'Where each rule builds', sub)}
    <section class="ar-panel ar-card" aria-label="Siting rules and backtest fleets">
      <div class="ar-card-h"><span class="ar-ct">Five ways to choose a site, run on real prices ${yr(`${end - 4}–${String(end).slice(2)}`)}</span><span class="ar-legend"><span>Fleets after five years, ${esc(scopeName)}</span><span>Earned ${sTag(doc, 'bt.y')}</span></span></div>
      <div class="ar-stable" role="table" aria-label="Siting rules">
        <div class="ar-srow ar-th" role="row"><span role="columnheader">Rule</span><span role="columnheader">How it places the next ten MW ${paramTag()}</span><span role="columnheader">$/kW-yr, five years ${sTag(doc, 'bt.y')}</span><span role="columnheader">Fleet it built (MW) ${sTag(doc, 'bt.fleet')}</span></div>
        ${rules}
      </div>
      <div class="ar-note">Each rule only uses what it could have known in January. The learner’s first siting design (Thompson sampling) scored ${bulk(ts[2])} in this scope against the replacement’s ${bulk(a5[2])} $/kW-yr ${sTag(doc, 'bt.y')}. The arena replaced it because every option’s value is observable there, so exploring has no information value and mostly costs money; it came out ahead only in the ERCOT backtest. Energy trading only.</div>
    </section>
    <section class="ar-panel ar-card" aria-label="Forward tournament">
      <div class="ar-card-h"><span class="ar-ct">The ${yr(S.start)}–${yr(String(S.start + 4).slice(2))} tournament: ${fillTokens(doc, '{FUTURES} futures', num)}, full revenue stack, $/kW-yr</span>
        <div class="ar-periods" role="group" aria-label="Horizon">${S.horizons.map((n, i) => `<button type="button" data-hz="${i}" data-fk="hz:${i}" class="${i === h ? 'on' : ''}" aria-pressed="${i === h}">${yr(horizonLabel(doc, i))}</button>`).join('')}</div></div>
      <div class="ar-ftable" role="table" aria-label="Forward tournament by rule">
        <div class="ar-frow ar-th" role="row"><span role="columnheader">Rule</span><span role="columnheader">Each dot is one future ${sTag(doc, 'fw.all')}; the bar is ${lbl('P10–P90')} ${sTag(doc, 'fw.p10')}; the ring is the mean ${sTag(doc, 'fw.mean')}</span><span role="columnheader">Mean</span><span role="columnheader">${lbl('P10–P90')}</span><span role="columnheader">Futures won ${sTag(doc, 'fw.win')}</span><span role="columnheader">Average ${yr(S.start + 4)} fleet (MW) ${sTag(doc, 'fw.fleet')}</span></div>
        <div class="ar-frow ar-axis" role="presentation"><span></span><span class="ar-saxis">${fl.ax.ticks.map((t) => `<span class="num" style="left:${t.left.toFixed(2)}%">$${fmtN(t.v, 0)}</span>`).join('')}</span><span></span><span></span><span></span><span></span></div>
        ${strip}
      </div>
      <div class="ar-note">Ties share the win. ${sc === 'NYISO' || sc === 'BOTH' ? 'Every rule builds in New York, so the two New York scopes agree. ' : ''}Twenty futures give a usable range, not precise tails. First ${yr(S.start)} placement of the learner: ${first(LEARNER).map(([id, mw]) => `${idHTML(optName(doc, id))} ${bulk(mw, 0)} MW`).join(', ')}.</div>
    </section>
    <div class="ar-two">
      <section class="ar-panel ar-card" aria-label="Transmission events"><span class="ar-ct">What new wires do to a battery next to them</span>
        <span class="ar-sub">Value after minus before the upgrade, compared with futures without it, $/kW-yr ${tagHTML(inf[0].effect.label, inf[0].effect.cite)}. A line that relieves a constraint removes the pocket’s edge. Futures counted ${tagHTML(inf[0].with.label, inf[0].with.cite)}.</span>
        <div class="ar-itable" role="table" aria-label="Transmission events">${infHTML}</div>
        <div class="ar-axisrow">${iax.ticks.map((t) => `<span class="num" style="left:${t.left.toFixed(2)}%">${t.v > 0 ? '+' : t.v < 0 ? '−' : ''}$${fmtN(Math.abs(t.v), 0)}</span>`).join('')}</div>
        <div class="ar-note">Check a pocket’s transmission plan before siting there: a line that relieves its import constraint removes the premium within a few years.${inf.some((x) => /Valley/.test(x.event)) ? ' The Valley result is surprising and rests on few futures; treat it as a hypothesis.' : ''}${inf.some((x) => /DFW/.test(x.event)) ? ' DFW’s large loads move the price level more than the daily spread, so trading value barely changes.' : ''}</div></section>
      <section class="ar-panel ar-card" aria-label="Programs"><span class="ar-ct">The programs that pay for availability</span>
        <span class="ar-sub">A program forces the battery full at the window start and discharging through the window on event days, so its opportunity cost comes out of the simulation.</span>
        <div class="ar-ptable" role="table" aria-label="Programs"><div class="ar-prow ar-th" role="row"><span role="columnheader">Program</span><span role="columnheader">Pays</span><span role="columnheader">Window, event days ${tagHTML('ASSUMPTION', 'event days, windows, start years and chances are the arena’s assumptions; docs/research/arena/arena/economics.py PROGRAMS')}</span><span role="columnheader">Starts, chance</span><span role="columnheader">Feeder</span></div>${progHTML}</div>
        <div class="ar-note">Programs that need the battery concentrated on a feeder pay for transformer upgrades: on the repo feeder ${num(f.sharePct, PCT0)} of a concentrated build needs one, ${num(f.usdPerKwYr, { ...MONEY, unit: '/kW-yr' })}. The feeder is an Oncor-suburb stand-in.</div></section>
    </div>
    <section class="ar-panel ar-card" aria-label="Site ranking">
      <div class="ar-card-h"><span class="ar-ct">Where the next battery should go: site ranking, learner’s dispatch, median future ${yr(S.start)}–${yr(String(S.start + 2).slice(2))}</span>
        <span class="ar-legend"><span><i class="ar-k-energy"></i>Energy trading</span><span><i class="ar-k-capacity"></i>Capacity</span><span><i class="ar-k-program"></i>Program payment</span><span><i class="ar-k-event"></i>Event energy</span><span><i class="ar-k-cost"></i>Transformer upgrades</span></span></div>
      <div class="ar-ktable" role="table" aria-label="Site ranking">
        <div class="ar-krow ar-th" role="row"><span></span><span role="columnheader">Site (zone + program)</span><span role="columnheader">Where the money comes from ${sTag(doc, 'sites.stack')}</span><span role="columnheader">Median ${sTag(doc, 'sites.median')}</span><span role="columnheader">Bad case ${sTag(doc, 'sites.p10')}</span></div>
        ${siteHTML}
        <div class="ar-krow ar-axis" role="presentation"><span></span><span></span><span class="ar-saxis">${sites.ax.ticks.map((t) => `<span class="num" style="left:${t.left.toFixed(2)}%">${t.v < 0 ? '−' : ''}$${fmtN(Math.abs(t.v), 0)}</span>`).join('')}</span><span></span><span></span></div>
      </div>
      ${sites.count > 12 ? `<button type="button" class="st-btn-ghost ar-more" data-more="1" data-fk="more"><span>${st.showAll ? 'Show fewer sites' : `Show all ${bulk(sites.count, 0)} sites`}</span></button>` : ''}
      <div class="ar-note">Bars are the components in the median future; the median is across all futures, so the two differ a little. The bad case is the tenth percentile. Capacity, program and event payments are ASSUMPTION or REAL rates (see the programs); trading is SIM. Battery cost is not subtracted.</div>
    </section>
    ${footHTML(doc, 'home', num)}`;
}

// ---------------------------------------------------------------------------------------------- q=4 how each one thinks
export function dayCaption(doc, mk, item, num) {
  const D = doc.markets[mk].days;
  const others = ALGOS.slice(0, 4).filter((a) => a !== item.algo);
  const idx = (a) => ALGOS.indexOf(a);
  const bestOther = Math.max(...others.map((a) => item.profit[idx(a)]));
  const me = item.profit[idx(item.algo)];
  const P = (v) => num(fromSeries(doc, 'day.profit', v), { money: true, digits: 2 });
  return `${esc(D.label)}, ${lbl(dateLabel(item.date))}: ${esc(nameOf(doc, item.algo))} earned ${P(me)} per kW of battery, ${P(me - bestOther)} more than the best of the other three rules. A perfect-foresight battery would have made ${P(item.profit[5])}.`;
}

export function view4HTML(doc, st, num) {
  const mk = st.mk, M = doc.markets[mk], S = M.stats.home, D = M.days;
  const item = D.items[Math.min(st.day, D.items.length - 1)];
  const lay = dayLayout(doc, item);
  const cards = doc.algorithms.map((a, k) => {
    const ours = a.id === LEARNER;
    const cap = derived(Math.round(valueOf(doc, mk, M.all, 'home', a.id, 'mean') / valueOf(doc, mk, M.all, 'home', 'CEIL', 'mean') * 100), 'mean of the window ÷ ceiling');
    const stat = (t, html) => `<div><span class="ar-sub">${t}</span><span class="ar-stat">${html}</span></div>`;
    return `<article class="ar-panel ar-rcard${ours ? ' ours' : ''}"><div class="ar-rc-h"><span class="ar-rc-n num">${k + 1}</span><div><b>${esc(a.name)}</b><span class="ar-note">${esc(a.short)}</span></div></div>
      <p class="ar-rule">${fillTokens(doc, a.rule, num)}</p>
      <div class="ar-sees"><span class="ar-sub">Sees</span> ${esc(a.sees)}</div><div class="ar-sees"><span class="ar-sub">Ignores</span> ${esc(a.ignores)}</div>
      <div class="ar-stats">${stat('Cycles a day', bulk(S.cycles[k], 2))}${stat('Earned per MWh sold', usdB(S.perMwh[k], 0))}
        ${stat('Days it trades', bulkU(S.tradeDays[k], 0, '%'))}${stat('Share of ceiling', bulkU(cap.v, 0, '%'))}
        ${stat('Backup when an outage starts', bulkU(M.backup[k], 1, ' h'))}</div></article>`;
  }).join('');
  const dayBtns = D.items.map((it, i) => `<button type="button" data-day="${i}" data-fk="day:${i}" class="${i === st.day ? 'on' : ''}" aria-pressed="${i === st.day}"><b>${esc(nameOf(doc, it.algo))}’s day</b><span class="ar-note num">${esc(dateLabel(it.date, { weekday: false }))}</span></button>`).join('');
  const rowsHTML = lay.rows.map((r, i) => {
    const a = r.id, ours = a === LEARNER, prof = item.profit[i];
    const followed = doc.meta.learner.experts[item.followed];
    return `<div class="ar-drow${ours ? ' ours' : ''}${a === item.algo ? ' star' : ''}" role="row"><div role="cell" class="ar-dname"><b>${esc(nameOf(doc, a))}</b><span class="ar-note">${ours ? `followed ${esc(followed.shown)}` : a === item.algo ? 'its best day' : ''}</span></div>
      <div role="cell" class="ar-dplot"><svg viewBox="0 0 96 64" preserveAspectRatio="none" aria-label="${esc(`${nameOf(doc, a)}: sold ${fmtN(r.soldMWh, 2)} MWh per MW`)}">
        <path d="${r.soc}" class="soc"></path><path d="M0 ${r.reserve.toFixed(1)}H96" class="res"></path><path d="M0 17H96" class="mid"></path>
        <path d="${r.bars.chg || 'M0 0'}" class="chg"></path><path d="${r.bars.dis || 'M0 0'}" class="dis"></path></svg></div>
      <div role="cell" class="ar-dprof"><b>${usdB(prof, 2)}</b><span class="ar-note">${r.avgSell != null ? `sold at ${usdB(r.avgSell, 0)}/MWh` : 'did not sell'}${r.avgBuy != null ? `, bought at ${usdB(r.avgBuy, 0)}` : ''}</span></div></div>`;
  }).join('');
  const reads = [];
  const ri = (a) => lay.rows.find((r) => r.id === a);
  const idle = ALGOS.filter((a) => ri(a).soldMWh === 0);
  reads.push(['The price decides.', `The day’s highest price was ${num(fromSeries(doc, 'day.price', Math.max(...item.price)), { money: true, digits: 0, unit: '/MWh' })} and its lowest ${num(fromSeries(doc, 'day.price', Math.min(...item.price)), { money: true, digits: 0, unit: '/MWh' })}. A battery makes money on that gap, less the round-trip loss and wear, and only if it is charged when the high comes.`]);
  reads.push([`${nameOf(doc, item.algo)}’s rule.`, `${esc(ruleOf(doc, item.algo).short)} ${esc(nameOf(doc, item.algo))} ${ri(item.algo).avgSell != null ? 'sold at an average' : 'did not sell'} ${ri(item.algo).avgSell != null ? num(derived(ri(item.algo).avgSell, 'MWh-weighted mean of the prices it sold at'), { money: true, digits: 0, unit: '/MWh' }) : ''}${ri(item.algo).avgBuy != null ? ` after buying at ${num(derived(ri(item.algo).avgBuy, 'MWh-weighted mean of the prices it bought at'), { money: true, digits: 0, unit: '/MWh' })}` : ''}.`]);
  reads.push(['Sitting out is a decision.', idle.length ? `${idle.map((a) => esc(nameOf(doc, a))).join(' and ')} sold nothing today. A rule that waits for a better price earns nothing on days like this and everything on others.` : 'Every rule sold something today.']);
  reads.push(['The learner just follows.', `It followed ${esc(doc.meta.learner.experts[item.followed].shown)} today, so its row repeats that rule’s trades; its profit differs only by where its own battery stood at midnight.`]);
  return `${headHTML(esc(`${mk} · HOW EACH ONE THINKS`), 'Five rules, in plain words', 'Every rule sees the same real prices. What differs is what it does with them, and what it ignores. Models only forecast prices here; deterministic code decides every charge and discharge.')}
    <section class="ar-cards" aria-label="The five rules">${cards}</section>
    <div class="ar-note ar-cardnote">Numbers in the rules are the arena’s own parameters ${paramTag()}. Cycles a day ${sTag(doc, 'cycles')}, earned per MWh sold ${sTag(doc, 'perMwh')}, days it trades ${sTag(doc, 'tradeDays')} and share of the ceiling ${dTag('mean of the window ÷ ceiling')}: ${periodText(doc, 'mean')}, all ${bulk(M.zones.length, 0)} zones, home fleet. Backup is forward-looking: the charge when an outage starts, as hours of home load ${sTag(doc, 'backup')}.</div>
    <section class="ar-panel ar-card" aria-label="One real day">
      <div class="ar-card-h"><span class="ar-ct">One real day, replayed through the arena’s own algorithms</span><span class="ar-legend"><span><i class="ar-sw-dis"></i>Discharge</span><span><i class="ar-sw-chg"></i>Charge</span><span><i class="ar-sw-soc"></i>Stored energy</span><span><i class="ar-sw-res"></i>Member reserve (never breached)</span></span></div>
      <span class="ar-sub">${esc(D.label)}, the day in ${periodText(doc, 'mean')} each rule beat the best of the other three by the most. Pick a day. The price is real ${sTag(doc, 'day.price')}; every action is simulated ${sTag(doc, 'day.act')} and the profit matches the cached backtest to the cent ${sTag(doc, 'day.profit')}.</span>
      <div class="ar-days" role="group" aria-label="Day">${dayBtns}</div>
      <p class="ar-caption">${dayCaption(doc, mk, item, num)}</p>
      <div class="ar-dayplot">
        <div class="ar-dpl" role="img" aria-label="Zone price through the day"><span class="ar-ytl">Price, $/MWh</span>${lay.ticks.map((t) => `<span class="ar-yt num" style="top:${t.top.toFixed(1)}%">${fmtN(t.v, 0)}</span>`).join('')}
          <svg viewBox="0 0 96 100" preserveAspectRatio="none" aria-hidden="true">${lay.ax.ticks.map((t) => `<path d="M0 ${(100 - t.left).toFixed(1)}H96" class="grid"></path>`).join('')}<path d="${lay.price}" class="price"></path></svg></div>
        <div class="ar-dhours">${HOURS.map((h) => `<span class="num" style="left:${(h / 24 * 100).toFixed(2)}%">${h === 24 ? '24:00' : `${String(h).padStart(2, '0')}:00`}</span>`).join('')}</div>
        <div class="ar-drows" role="table" aria-label="Each rule through the day"><div class="ar-drow ar-th" role="row"><span role="columnheader">Rule</span><span role="columnheader">Charge and discharge, power as a share of full ${sTag(doc, 'day.act')}; stored energy below ${sTag(doc, 'day.soc')}</span><span role="columnheader">Profit per kW ${sTag(doc, 'day.profit')}</span></div>${rowsHTML}</div>
      </div>
      <ol class="ar-reads">${reads.map((r, i) => `<li><span class="ar-rn num">0${i + 1}</span><span><b>${esc(r[0])}</b> ${r[1]}</span></li>`).join('')}</ol>
    </section>
    ${footHTML(doc, 'home', num)}`;
}

/** The body of the current view as HTML. A pure function of (doc, state, num). */
export function viewHTML(doc, st, num) {
  switch (viewOf(st.view)) {
    case 2: return view2HTML(doc, st, num);
    case 3: return view3HTML(doc, st, num);
    case 4: return view4HTML(doc, st, num);
    default: return view1HTML(doc, st, num);
  }
}

// ---------------------------------------------------------------------------------------------- mount
function ensureCss() {
  if (typeof document === 'undefined') return Promise.resolve();
  if (document.querySelector('link[data-arena-css]')) return Promise.resolve();
  return new Promise((resolve) => {
    const l = document.createElement('link');
    l.rel = 'stylesheet';
    l.href = new URL('./arena.css', import.meta.url).href;
    l.dataset.arenaCss = '1';
    l.onload = l.onerror = () => resolve();
    document.head.appendChild(l);
  });
}
const loadState = () => { try { return JSON.parse(localStorage.getItem(STORE) || 'null') || {}; } catch (e) { return {}; } };
const saveState = (s) => { try { localStorage.setItem(STORE, JSON.stringify(s)); } catch (e) { /* private mode */ } };

export function initialState(doc, params, saved = {}) {
  const mk = MARKETS.some(([id]) => id === saved.mk) ? saved.mk : 'ERCOT';
  const zone = { ...DEFAULT_ZONE };
  for (const [id] of MARKETS) if (saved.zone && zoneEntries(doc, id).some((z) => z.id === saved.zone[id])) zone[id] = saved.zone[id];
  return {
    view: viewOf(params && params.q), mk, scope3: mk, spec: SPECS.some(([k]) => k === saved.spec) ? saved.spec : 'home', zone,
    period: 'mean', year2: doc.markets[mk].events[0].year, hz: doc.siting.horizons.length - 1, showAll: false, day: 0,
  };
}

export async function mount(root, ctx) {
  const num = (x, o) => ctx.num(x, o);
  root.innerHTML = '<div class="ar"><div class="ar-loading">Loading the arena…</div></div>';
  let doc;
  try {
    [doc] = await Promise.all([ctx.getJSON(DATA), ensureCss()]);
  } catch (e) {
    root.innerHTML = `<div class="ar"><div class="st-placeholder"><div class="st-eyebrow">ARENA</div><div class="st-ph-title">The arena is not built yet</div>
      <p>ui/data/${DATA} did not load. Build it with <code>scripts/export_arena.py</code> (it reads the arena in docs/research/arena/).</p></div></div>`;
    return { dispose() {} };
  }
  const st = initialState(doc, ctx.params, loadState());
  const el = document.createElement('div');
  el.className = 'ar';
  root.replaceChildren(el);

  function render() {
    const fk = document.activeElement && document.activeElement.dataset ? document.activeElement.dataset.fk : null;
    const scroll = el.querySelector('.ar-body');
    const top = scroll ? scroll.scrollTop : 0, rtop = (el.querySelector('.ar-rail') || {}).scrollTop || 0;
    el.innerHTML = `${railHTML(doc, st)}<div class="ar-body">${viewHTML(doc, st, num)}</div>`;
    const nb = el.querySelector('.ar-body'), nr = el.querySelector('.ar-rail');
    if (nb) nb.scrollTop = top;
    if (nr) nr.scrollTop = rtop;
    if (fk) { const b = [...el.querySelectorAll('[data-fk]')].find((x) => x.dataset.fk === fk); if (b) b.focus({ preventScroll: true }); }
    saveState({ mk: st.mk, spec: st.spec, zone: st.zone });
  }
  const onClick = (ev) => {
    const t = ev.target.closest('button');
    if (!t || !el.contains(t)) return;
    const d = t.dataset;
    if (d.mk) {
      if (st.view === 3) { st.scope3 = d.mk; if (d.mk !== 'BOTH') st.mk = d.mk; } else { st.mk = d.mk; st.scope3 = d.mk; }
      st.year2 = doc.markets[st.mk].events[0].year;
      render(); return;
    }
    if (d.q) {
      st.view = viewOf(Number(d.q));
      history.replaceState(history.state, '', ctx.link({ page: PAGE, q: st.view }));
      const b = el.querySelector('.ar-body'); if (b) b.scrollTop = 0;
      render(); return;
    }
    if (d.spec) { st.spec = d.spec; render(); return; }
    if (d.zone) { st.zone[st.mk] = d.zone; render(); return; }
    if (d.period) { st.period = d.period === 'mean' ? 'mean' : Number(d.period); render(); return; }
    if (d.year2) { st.year2 = Number(d.year2); render(); return; }
    if (d.hz) { st.hz = Number(d.hz); render(); return; }
    if (d.day) { st.day = Number(d.day); render(); return; }
    if (d.more) { st.showAll = !st.showAll; render(); }
  };
  el.addEventListener('click', onClick);
  render();
  return { dispose() { el.removeEventListener('click', onClick); } };
}
