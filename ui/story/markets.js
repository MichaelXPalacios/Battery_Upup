// ui/story/markets.js: Screen 5 Markets, the grid-research desk (docs/research/arena/README.md).
// One file, ui/data/markets/index.json (hb.markets.v1, written by scripts/export_markets.py from the arena's results).
// Left rail: market (ERCOT / NYISO), view (?q=1..3) and, on the desk, the load zone. Three views:
//   q=1 Zone desk        one zone: realised 2015-2026 (walk-forward, real prices) then the 2027-31 forecast fan,
//                        risk tiles, six computed sentences, factor sensitivities, seasonality, the year-by-year tape
//   q=2 Weather regions  the forecast split by the weather that drives each group of zones
//   q=3 Trader's read    position, distribution, factor, regime, carry, worked for DFW against New York
// Every value shown carries its tag: headline numbers come labelled from the file; per-year and per-month arrays take
// their label from the file's `series`. Money is gross trading value per kW of battery per year, not Base's profit.
// No language model produces a number or a rank here: the arena's deterministic code did.

import { tagHTML } from './shell.js';

export const PAGE = 'markets';
export const DATA = 'markets/index.json';
export const MARKETS = [['ERCOT', 'ERCOT · Texas'], ['NYISO', 'NYISO · New York']];
export const VIEWS = [
  { q: 1, title: 'Zone desk', sub: 'One load zone: realised, then forecast' },
  { q: 2, title: 'Weather regions', sub: 'The 2027–31 forecast split by weather' },
  { q: 3, title: 'The trader’s read', sub: 'Position, distribution, factor, regime, carry' },
];
const DEFAULT_ZONE = { ERCOT: 'LZ_NORTH', NYISO: 'N.Y.C.' };
const MONTHS = ['January', 'February', 'March', 'April', 'May', 'June', 'July', 'August', 'September', 'October', 'November', 'December'];
const MON3 = MONTHS.map((m) => m.slice(0, 3));
const STORE = 'hb-markets';
const FIRST_FC_DEFAULT = 2027;

const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const fin = (x) => typeof x === 'number' && Number.isFinite(x);
const avg = (a) => (a.length ? a.reduce((s, x) => s + x, 0) / a.length : 0);
const MONEY = { money: true, digits: 1 };
const PCT0 = { unit: '%', digits: 0 };
const PCT1 = { unit: '%', digits: 1 };

export const viewOf = (q) => ([1, 2, 3].includes(q) ? q : 1);

/** A bulk value from the file's `series` as a labelled number (label and text from the series entry). */
export function fromSeries(doc, key, v) {
  const s = doc && doc.series && doc.series[key];
  if (!s) throw new Error(`markets: no series ${key}`);
  return { v, label: s.label, cite: s.text };
}
/** A number this page computes from labelled inputs: DERIVED, with the arithmetic in the cite. */
export const derived = (v, cite) => ({ v, label: 'DERIVED', cite: `DERIVED: ${cite}` });

export function niceMax(v) {
  for (const s of [20, 30, 40, 60, 80, 100, 120, 160, 200, 240, 300, 400]) if (v <= s) return s;
  return Math.ceil(v / 100) * 100;
}

/** "2026-09-26" -> "26 Sep 2026". */
export function shortDate(iso) {
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(String(iso || ''));
  return m ? `${Number(m[3])} ${MON3[Number(m[2]) - 1]} ${m[1]}` : String(iso || '');
}

/** Geometry of the desk chart in a W x H viewBox: one equal slot per year from the first realised year to the last
 *  forecast year. Bars = realised (pale when the year is partial), ticks = ceiling, band = P10..P90, lines = median
 *  and the fixed schedule. Pure (tested). */
export function deskChart(z, histYears, fcYears, W = 1000, H = 300) {
  const y0 = histYears[0], y1 = fcYears[fcYears.length - 1];
  const n = y1 - y0 + 1, sw = W / n;
  let vmax = 0;
  histYears.forEach((y, i) => { vmax = Math.max(vmax, z.hist.realised[i], z.hist.ceiling[i]); });
  z.fc.p90.forEach((v) => { vmax = Math.max(vmax, v); });
  const ymax = niceMax(vmax * 1.04);
  const Y = (v) => H - Math.max(0, v) / ymax * H;
  const CX = (yr) => (yr - y0 + 0.5) * sw;
  const pt = (x, y) => `${x.toFixed(1)} ${y.toFixed(1)}`;
  let bars = '', partial = '', ceil = '';
  histYears.forEach((yr, i) => {
    const x0 = CX(yr) - sw * 0.3, x1 = CX(yr) + sw * 0.3;
    const seg = `M${pt(x0, H)}V${Y(z.hist.realised[i]).toFixed(1)}H${x1.toFixed(1)}V${H}Z`;
    if (z.partial[i]) partial += seg; else bars += seg;
    ceil += `M${pt(CX(yr) - sw * 0.4, Y(z.hist.ceiling[i]))}H${(CX(yr) + sw * 0.4).toFixed(1)}`;
  });
  const line = (arr) => 'M' + fcYears.map((yr, i) => pt(CX(yr), Y(arr[i]))).join('L');
  const top = fcYears.map((yr, i) => pt(CX(yr), Y(z.fc.p90[i])));
  const bot = fcYears.map((yr, i) => pt(CX(yr), Y(z.fc.p10[i]))).reverse();
  const ticks = [0, 1, 2, 3, 4].map((i) => ({ v: ymax * i / 4, top: 100 - i * 25 }));
  const grid = ticks.map((t) => `M0 ${Y(t.v).toFixed(1)}H${W}`).join('');
  const divX = (fcYears[0] - y0) * sw;
  return {
    W, H, sw, ymax, ticks, grid, bars, partial, ceil, band: `M${top.join('L')}L${bot.join('L')}Z`,
    p50: line(z.fc.p50), clock: line(z.fc.clockwork), divX,
    dots: fcYears.map((yr, i) => ({ left: CX(yr) / W * 100, top: Y(z.fc.p50[i]) / H * 100 })),
    years: Array.from({ length: n }, (_, i) => y0 + i),
  };
}

/** Index of a year in a list, or -1. */
const at = (years, y) => years.indexOf(y);

/** The six sentences of the desk, each number tagged. `num` = ctx.num. Pure apart from `num` (tested). */
export function deskReads(doc, mk, z, num) {
  const M = doc.markets[mk], h = z.headline, f = z.factors, ys = M.histYears;
  const c23 = fromSeries(doc, 'hist.ceiling', z.hist.ceiling[at(ys, 2023)]);
  const c25 = fromSeries(doc, 'hist.ceiling', z.hist.ceiling[at(ys, 2025)]);
  const wear = doc.constants.WEAR_USD_MWH;
  const skew = derived(h.mean.v - h.median.v, 'forecast mean minus the mean of the yearly medians');
  const ratio = derived(Math.round((h.top10Pct.v / 10) / ((100 - h.top10Pct.v) / 355)), 'one top-10 day ÷ one of the other 355 days, by share of the year');
  const base = derived((f.summer_spike_pct.v + f.winter_spike_pct.v) / 2, 'mean of the July and January spike-day odds');
  const ch = derived(Math.round((c25.v / c23.v - 1) * 100), 'ceiling 2025 ÷ ceiling 2023 − 1');
  const crowd = mk === 'ERCOT'
    ? `That is crowding: Texas added about ${num({ v: '14–18 GW', label: 'REAL', cite: 'docs/research/ercot.md: batteries in ERCOT, 2023–26' })} of batteries chasing the same evening peak.`
    : `New York has under ${num({ v: '100 MW', label: 'REAL', cite: 'docs/research/nyiso.md: batteries in the NYISO market' })} of batteries, so the edge moves with gas and weather, not with competitors.`;
  const gas = f.gas50_spread_pct.v > f.gas50_level_pct.v
    ? `Gas +50% widens the spread ${num(f.gas50_spread_pct, { ...PCT0, signed: true })} more than it lifts the price level (${num(f.gas50_level_pct, { ...PCT0, signed: true })}).`
    : `Gas +50% lifts the price level ${num(f.gas50_level_pct, { ...PCT0, signed: true })} but the spread only ${num(f.gas50_spread_pct, { ...PCT0, signed: true })}: gas moves the level more than the shape.`;
  return [
    { lead: 'The position.', html: `A battery here is long a strip of daily spread options on the ${esc(z.label)} price. Each day it owns the gap between the cheapest and dearest hours, less round-trip losses and ${num({ v: wear.value, label: wear.label, cite: wear.cite }, { money: true, digits: 0, unit: '/MWh' })} of wear.` },
    { lead: 'The regime.', html: `The perfect-foresight ceiling went from ${num(c23, MONEY)} in 2023 to ${num(c25, MONEY)} in 2025 (${num(ch, { ...PCT0, signed: true })}). ${crowd}` },
    { lead: 'The distribution.', html: `Expect ${num(h.mean, MONEY)} a year with σ ${num(h.std, MONEY)}. The mean sits ${num(skew, MONEY)} above the median, so the payoff is right-skewed: a few scarcity years carry the book.` },
    { lead: 'The tail.', html: `${num(h.top10Pct, PCT0)} of a typical year comes from its 10 best days; one of those days is worth about ${num(ratio, { digits: 0 })} ordinary days. Being charged and available on spike days matters more than precision on normal ones.` },
    { lead: 'The factor.', html: `Weather is the main driver: a +5°F July day lifts the expected daily spread ${num(f.summer_shock_spread_pct, { ...PCT0, signed: true })} and spike odds from ${num(f.summer_spike_pct, PCT1)} to ${num(f.summer_shock_spike_pct, PCT1)}. ${gas}` },
    { lead: 'The clustering.', html: `After a spike day the chance of another is ${num(f.spike_after_spike_pct, PCT1)}, against about ${num(base, PCT1)} on a typical day. Spikes come in runs, so keep charge for the second day.` },
  ];
}

/** Weather regions on one shared axis: P10..P90 bar and mean dot, sorted by mean ÷ σ (best first). Pure (tested). */
export function stripLayout(regions) {
  const lo = Math.min(...regions.map((x) => x.headline.p10.v));
  const hi = Math.max(...regions.map((x) => x.headline.p90.v));
  const step = hi - lo > 50 ? 20 : hi - lo > 25 ? 10 : 5;
  const a0 = Math.floor(lo / step) * step, a1 = Math.ceil(hi / step) * step;
  const L = (v) => (v - a0) / (a1 - a0) * 100;
  const ticks = [];
  for (let v = a0; v <= a1 + 1e-9; v += step) ticks.push({ v, left: L(v) });
  const rows = regions.slice().sort((a, b) => b.headline.mean.v / b.headline.std.v - a.headline.mean.v / a.headline.std.v)
    .map((x) => ({ x, lo: L(x.headline.p10.v), span: L(x.headline.p90.v) - L(x.headline.p10.v), mid: L(x.headline.mean.v),
      ratio: x.headline.mean.v / x.headline.std.v }));
  return { a0, a1, ticks, rows };
}

/** "A heat book" / "A gas book" / ...: what moves a weather region most, from its factor table. */
export function bookOf(f) {
  const heat = f.summer_shock_spread_pct.v, cold = f.winter_shock_spread_pct.v, gas = f.gas50_spread_pct.v;
  if (gas > heat * 0.6) return 'A gas book';
  if (heat >= cold * 1.5) return 'A heat book';
  return cold >= heat * 0.8 ? 'A two-season book' : 'A summer-led book';
}

/** Link ui/story/markets.css once (resolves when it has loaded, or failed: the page still renders). */
function ensureCss() {
  if (typeof document === 'undefined') return Promise.resolve();
  const have = document.querySelector('link[data-markets-css]');
  if (have) return Promise.resolve();
  return new Promise((resolve) => {
    const l = document.createElement('link');
    l.rel = 'stylesheet';
    l.href = new URL('./markets.css', import.meta.url).href;
    l.dataset.marketsCss = '1';
    l.onload = l.onerror = () => resolve();
    document.head.appendChild(l);
  });
}

function loadState() {
  try { return JSON.parse(localStorage.getItem(STORE) || 'null') || {}; } catch (e) { return {}; }
}
function saveState(s) {
  try { localStorage.setItem(STORE, JSON.stringify(s)); } catch (e) { /* private mode */ }
}

export async function mount(root, ctx) {
  const num = (x, o) => ctx.num(x, o);
  // a signed dollar amount: format.js writes "-$3.1" for a loss; a gain gets its "+" here
  const sMoney = (x) => `${x.v > 0 ? '+' : ''}${num(x, MONEY)}`;
  const saved = loadState();
  const st = {
    view: viewOf(ctx.params && ctx.params.q),
    mk: MARKETS.some(([id]) => id === saved.mk) ? saved.mk : 'ERCOT',
    zone: { ...DEFAULT_ZONE, ...(saved.zone || {}) },
    year: FIRST_FC_DEFAULT,
  };
  root.innerHTML = '<div class="mk"><div class="mk-loading">Loading the market desk…</div></div>';
  let doc;
  try {
    [doc] = await Promise.all([ctx.getJSON(DATA), ensureCss()]);
  } catch (e) {
    root.innerHTML = `<div class="mk"><div class="st-placeholder"><div class="st-eyebrow">MARKETS</div><div class="st-ph-title">The market desk is not built yet</div>
      <p>ui/data/${DATA} did not load. Build it with <code>scripts/export_markets.py</code> (it reads the arena in docs/research/arena/).</p></div></div>`;
    return { dispose() {} };
  }
  for (const [id] of MARKETS) {
    const ids = doc.markets[id].zones.map((z) => z.id);
    if (!ids.includes(st.zone[id])) st.zone[id] = ids[0];
  }

  const el = document.createElement('div');
  el.className = 'mk';
  root.replaceChildren(el);
  const zoneOf = () => doc.markets[st.mk].zones.find((z) => z.id === st.zone[st.mk]);
  const sTag = (key) => { const s = doc.series[key]; return tagHTML(s.label, s.text); };
  const fmtV = (v, d = 1) => (fin(v) ? Number(v).toLocaleString('en-US', { minimumFractionDigits: d, maximumFractionDigits: d }) : '—');

  // ---------------------------------------------------------------- rail
  function railHTML() {
    const M = doc.markets[st.mk];
    const maxMean = Math.max(...M.zones.map((z) => z.headline.mean.v));
    const seg = MARKETS.map(([id, t]) => `<button type="button" data-mk="${id}" class="${id === st.mk ? 'on' : ''}" aria-pressed="${id === st.mk}">${esc(t)}</button>`).join('');
    const views = VIEWS.map((v) => `<button type="button" class="mk-view${v.q === st.view ? ' on' : ''}" data-q="${v.q}" aria-pressed="${v.q === st.view}">
      <span class="mk-qn">${v.q}</span><span><span class="mk-qt">${esc(v.title)}</span><span class="mk-qs">${esc(v.sub)}</span></span></button>`).join('');
    const zones = st.view !== 1 ? '' : `<div class="mk-rail-h st-eyebrow sm">${st.mk === 'ERCOT' ? 'LOAD ZONES' : 'ZONES A–K'} · EXPECTED 2027–31</div>
      <div class="mk-zones">${M.zones.map((z) => `<button type="button" class="mk-zone${z.id === st.zone[st.mk] ? ' on' : ''}" data-zone="${esc(z.id)}" aria-pressed="${z.id === st.zone[st.mk]}">
        <span class="mk-zone-t"><span>${esc(z.label)}</span><span class="num">$${fmtV(z.headline.mean.v)}</span></span>
        <span class="mk-bar"><i style="width:${(z.headline.mean.v / maxMean * 100).toFixed(1)}%"></i></span>
        <span class="mk-zone-s">${esc(st.mk === 'ERCOT' ? z.id : z.weather)} · σ $${fmtV(z.headline.std.v)}</span></button>`).join('')}</div>
      <div class="mk-note mk-rail-note">Bar and $ = expected trading value 2027–31, mean of ${M.futures * M.fcYears.length} future-years ${tagHTML(M.zones[0].headline.mean.label, M.zones[0].headline.mean.cite)}. σ = how far a year typically lands from it.</div>`;
    return `<aside class="mk-rail"><div class="st-eyebrow">MARKETS</div><div class="cfg-seg mk-seg" role="group" aria-label="Market">${seg}</div>
      <nav class="mk-views" aria-label="Views">${views}</nav>${zones}</aside>`;
  }

  // ---------------------------------------------------------------- q=1 desk
  function readoutHTML(z, M) {
    const hi = at(M.histYears, st.year), fi = at(M.fcYears, st.year);
    if (hi >= 0) {
      const S = (k, v) => fromSeries(doc, k, v);
      const cap = z.hist.ceiling[hi] > 0 ? derived(Math.round(z.hist.realised[hi] / z.hist.ceiling[hi] * 100), 'realised ÷ ceiling') : null;
      const rows = [['Earned', num(S('hist.realised', z.hist.realised[hi]), MONEY)], ['Ceiling', num(S('hist.ceiling', z.hist.ceiling[hi]), MONEY)],
        ['Capture', cap ? num(cap, PCT0) : '—'], ['Avg price', num(S('hist.price', z.hist.price[hi]), { ...MONEY, unit: '/MWh' })],
        ['Daily high–low', num(S('hist.spread', z.hist.spread[hi]), { ...MONEY, unit: '/MWh' })],
        ['Hours ≥ $250', num(S('hist.spikeHours', z.hist.spikeHours[hi]), { digits: 0 })],
        ['From 10 best days', num(S('hist.top10Pct', z.hist.top10Pct[hi]), PCT0)]];
      return { title: `${st.year} · ${z.partial[hi] ? `to ${shortDate(M.lastDate)}` : 'realised'}`, sub: 'Adaptive learner, walk-forward on real prices.', rows };
    }
    if (fi >= 0) {
      const F = (v) => fromSeries(doc, 'fc', v);
      const edge = derived(z.fc.p50[fi] - z.fc.clockwork[fi], 'median learner minus median fixed schedule');
      const rows = [['P10 bad year', num(F(z.fc.p10[fi]), MONEY)], ['Median', num(F(z.fc.p50[fi]), MONEY)], ['Mean', num(F(z.fc.mean[fi]), MONEY)],
        ['P90 good year', num(F(z.fc.p90[fi]), MONEY)], ['Fixed schedule', num(fromSeries(doc, 'fc.clockwork', z.fc.clockwork[fi]), MONEY)],
        ['Edge vs schedule', sMoney(edge)]];
      return { title: `${st.year} · forecast`, sub: `Distribution across ${M.futures} futures.`, rows };
    }
    return { title: String(st.year), sub: 'No data for this year.', rows: [] };
  }

  function deskHTML() {
    const M = doc.markets[st.mk], z = zoneOf(), h = z.headline, f = z.factors;
    const g = deskChart(z, M.histYears, M.fcYears);
    const fams = M.families.map(([k, n]) => `${k} ${n}`).join(' · ');
    const ro = readoutHTML(z, M);
    const kpis = [
      ['Expected, 2027–31', num(h.mean, MONEY), `/kW-yr. Mean of ${M.futures * M.fcYears.length} future-years; median ${num(h.median, MONEY)}.`],
      ['Volatility (σ)', num(h.std, MONEY), '/kW-yr. Typical distance of a year from the mean.'],
      ['Bad year (P10)', num(h.p10, MONEY), '/kW-yr. One year in ten earns less.'],
      ['Mean ÷ σ', num(h.meanOverStd, { digits: 2 }), 'Return per unit of risk across futures, not a time-series Sharpe.'],
      ['Tail share', num(h.top10Pct, PCT0), 'Of a typical year’s value from its 10 best days.'],
      ['Capture, 2021–25', num(h.capturePct, PCT0), 'Realised ÷ perfect-foresight ceiling.'],
      ['Edge over fixed schedule', sMoney(h.edge), '/kW-yr. Median learner minus median clockwork.'],
    ];
    const reads = deskReads(doc, st.mk, z, num);
    const bmax = Math.max(150, f.summer_shock_spread_pct.v, f.winter_shock_spread_pct.v);
    const betas = [['+5°F on a July day', 'daily spread', f.summer_shock_spread_pct, 'mk-b-heat'], ['−10°F on a January day', 'daily spread', f.winter_shock_spread_pct, 'mk-b-cold'],
      ['+50% Henry Hub gas', 'daily spread', f.gas50_spread_pct, 'mk-b-gas'], ['+50% Henry Hub gas', 'average price', f.gas50_level_pct, 'mk-b-gas']];
    const smax = Math.max(...z.season.realised, ...z.season.forecast);
    let pk = 0;
    z.season.forecast.forEach((v, i) => { if (v > z.season.forecast[pk]) pk = i; });
    const summer = derived(z.season.forecast.slice(5, 9).reduce((a, b) => a + b, 0), 'June to September shares added');
    const hy = M.histYears.map((y, i) => i).reverse();
    return `<div class="mk-head">
        <div><div class="st-eyebrow">${st.mk} · ${esc(st.mk === 'ERCOT' ? z.id : `ZONE ${z.label.slice(-2, -1)}`)}</div>
          <div class="st-title mk-h1">${esc(z.label)}</div>
          <div class="mk-headsub">Weather region ${esc(z.weather)} · station ${esc(z.station)} · ${st.mk === 'ERCOT' ? 'settles at the load-zone price' : 'settles at the zonal LBMP'}</div></div>
        <div class="mk-headr">History through ${esc(shortDate(M.lastDate))}<br>Forecast: ${M.futures} futures (${esc(fams)}) ${tagHTML(doc.series.fc.label, doc.series.fc.text)}</div></div>

      <section class="mk-panel mk-card" aria-label="Value per year">
        <div class="mk-card-h"><span class="mk-ct">Trading value per year, $/kW-yr</span>
          <span class="mk-legend"><span><i class="mk-sw-bar"></i>Realised ${sTag('hist.realised')}</span><span><i class="mk-sw-ceil"></i>Perfect-foresight ceiling ${sTag('hist.ceiling')}</span>
          <span><i class="mk-sw-band"></i>Forecast P10–P90 ${sTag('fc')}</span><span><i class="mk-sw-p50"></i>Forecast median</span><span><i class="mk-sw-clock"></i>Fixed schedule ${sTag('fc.clockwork')}</span></span></div>
        <div class="mk-plotwrap">
          ${g.ticks.map((t) => `<span class="mk-ytick num" style="top:${t.top}%">$${Math.round(t.v)}</span>`).join('')}
          <div class="mk-plot">
            <svg viewBox="0 0 ${g.W} ${g.H}" preserveAspectRatio="none" aria-hidden="true">
              <rect x="${g.divX.toFixed(1)}" y="0" width="${(g.W - g.divX).toFixed(1)}" height="${g.H}" class="mk-fczone"></rect>
              <path d="${g.grid}" class="mk-grid"></path>
              <rect x="${((st.year - g.years[0]) * g.sw).toFixed(1)}" y="0" width="${g.sw.toFixed(1)}" height="${g.H}" class="mk-sel"></rect>
              <path d="${g.bars || 'M0 0'}" class="mk-bars"></path><path d="${g.partial || 'M0 0'}" class="mk-bars mk-partial"></path>
              <path d="${g.ceil}" class="mk-ceil"></path><path d="${g.band}" class="mk-band"></path>
              <path d="${g.clock}" class="mk-clock"></path><path d="${g.p50}" class="mk-p50"></path>
              <path d="M${g.divX.toFixed(1)} 0V${g.H}" class="mk-div"></path>
            </svg>
            ${g.dots.map((d) => `<span class="mk-dot" style="left:${d.left.toFixed(2)}%;top:${d.top.toFixed(2)}%"></span>`).join('')}
            <span class="mk-divlab l" style="left:${(g.divX / g.W * 100).toFixed(2)}%">realised</span><span class="mk-divlab r" style="left:${(g.divX / g.W * 100).toFixed(2)}%">forecast</span>
            <div class="mk-cols">${g.years.map((y) => `<button type="button" class="mk-col${y === st.year ? ' on' : ''}" data-year="${y}" aria-label="Show ${y}" aria-pressed="${y === st.year}"></button>`).join('')}</div>
          </div>
        </div>
        <div class="mk-xlabels">${g.years.map((y) => `<span class="num${y === st.year ? ' on' : ''}${y >= M.fcYears[0] ? ' fc' : ''}">’${String(y).slice(2)}</span>`).join('')}</div>
        <div class="mk-readout"><div class="mk-ro-h"><b>${esc(ro.title)}</b><span class="mk-sub">${esc(ro.sub)}</span></div>
          ${ro.rows.map(([k, v]) => `<div class="mk-ro"><span class="mk-sub">${esc(k)}</span><span class="mk-ro-v">${v}</span></div>`).join('')}</div>
        <div class="mk-note">Pick a year (click, tab or hover) to read it. Pale bar = year to date.</div>
      </section>

      <section class="mk-kpis" aria-label="Risk read-out">${kpis.map(([k, v, s]) => `<div class="mk-panel mk-kpi"><span class="mk-sub">${esc(k)}</span><span class="mk-kpi-v">${v}</span><span class="mk-note">${s}</span></div>`).join('')}</section>

      <div class="mk-two">
        <section class="mk-panel mk-card" aria-label="Trader's read"><span class="mk-ct">The trader’s read</span><span class="mk-sub">Six sentences, each computed from this zone’s numbers.</span>
          <ol class="mk-reads">${reads.map((r, i) => `<li><span class="mk-rn num">0${i + 1}</span><span><b>${esc(r.lead)}</b> ${r.html}</span></li>`).join('')}</ol></section>
        <section class="mk-panel mk-card" aria-label="Factor sensitivities"><span class="mk-ct">Factor sensitivities</span><span class="mk-sub">What one shock does to a typical day, from this zone’s fitted price equations.</span>
          ${betas.map(([k, s, x, cls]) => `<div class="mk-beta"><div class="mk-kv"><span>${esc(k)} <span class="mk-dim">· ${esc(s)}</span></span><span>${num(x, { ...PCT0, signed: true })}</span></div>
            <div class="mk-track"><i class="${cls}" style="width:${(x.v / bmax * 100).toFixed(1)}%"></i></div></div>`).join('')}
          <div class="mk-odds">
            <div><span class="mk-sub">July spike odds</span><span>${num(f.summer_spike_pct, PCT1)} → ${num(f.summer_shock_spike_pct, PCT1)}</span><span class="mk-note">typical day → +5°F</span></div>
            <div><span class="mk-sub">January spike odds</span><span>${num(f.winter_spike_pct, PCT1)} → ${num(f.winter_shock_spike_pct, PCT1)}</span><span class="mk-note">typical day → −10°F</span></div>
            <div><span class="mk-sub">After a spike day</span><span>${num(f.spike_after_spike_pct, PCT1)}</span><span class="mk-note">chance the next day spikes</span></div></div>
          <div class="mk-note">Baselines: a typical July day (${num(f.summer_tmax_f, { digits: 0, unit: '°F' })} high) has a ${num(f.summer_spread_usd, { money: true, digits: 0, unit: '/MWh' })} spread; a typical January day (${num(f.winter_tmax_f, { digits: 0, unit: '°F' })}) ${num(f.winter_spread_usd, { money: true, digits: 0, unit: '/MWh' })}.</div>
        </section>
      </div>

      <section class="mk-panel mk-card" aria-label="Seasonality">
        <div class="mk-card-h"><span class="mk-ct">When the year’s value is made</span>
          <span class="mk-legend"><span><i class="mk-sw-bar"></i>2021–25 realised</span><span><i class="mk-sw-fc"></i>2027–31 forecast</span><span>${sTag('season')}</span></span></div>
        <span class="mk-sub">Share of a year by month. Forecast peak ${MONTHS[pk]}, ${num(fromSeries(doc, 'season', z.season.forecast[pk]), PCT0)}; June–September ${num(summer, PCT0)} of the year.</span>
        <div class="mk-season">${MONTHS.map((m, i) => `<div class="mk-mo" role="img" aria-label="${m}: ${fmtV(z.season.realised[i])}% realised, ${fmtV(z.season.forecast[i])}% forecast">
          <div class="mk-mo-bars"><i class="h" style="height:${(z.season.realised[i] / smax * 100).toFixed(1)}%"></i><i class="f" style="height:${(z.season.forecast[i] / smax * 100).toFixed(1)}%"></i></div>
          <span class="num">${m[0]}</span></div>`).join('')}</div>
      </section>

      <section class="mk-panel mk-card" aria-label="The tape">
        <span class="mk-ct">The tape: what the zone did, and what the futures say</span>
        <div class="mk-tables">
          <div class="mk-table" role="table" aria-label="Realised by year">
            <div class="mk-tr mk-th" role="row"><span role="columnheader">Year</span><span role="columnheader">Avg price $/MWh ${sTag('hist.price')}</span><span role="columnheader">Daily high–low ${sTag('hist.spread')}</span>
              <span role="columnheader">Hours ≥ $250 ${sTag('hist.spikeHours')}</span><span role="columnheader">Earned ${sTag('hist.realised')}</span><span role="columnheader">Ceiling ${sTag('hist.ceiling')}</span>
              <span role="columnheader">Capture ${tagHTML('DERIVED', 'DERIVED: earned ÷ ceiling')}</span><span role="columnheader">10 best days ${sTag('hist.top10Pct')}</span></div>
            ${hy.map((i) => `<div class="mk-tr num" role="row"><span role="cell">${M.histYears[i]}${z.partial[i] ? ' YTD' : ''}</span><span role="cell">${fmtV(z.hist.price[i])}</span><span role="cell">${fmtV(z.hist.spread[i])}</span>
              <span role="cell">${fmtV(z.hist.spikeHours[i], 0)}</span><span role="cell">${fmtV(z.hist.realised[i])}</span><span role="cell">${fmtV(z.hist.ceiling[i])}</span>
              <span role="cell">${z.hist.ceiling[i] > 0 ? Math.round(z.hist.realised[i] / z.hist.ceiling[i] * 100) + '%' : '—'}</span><span role="cell">${z.hist.top10Pct[i]}%</span></div>`).join('')}
          </div>
          <div class="mk-table mk-fct" role="table" aria-label="Forecast by year">
            <div class="mk-tr mk-th" role="row"><span role="columnheader">Year</span><span role="columnheader">P10 ${sTag('fc')}</span><span role="columnheader">Median</span><span role="columnheader">Mean</span><span role="columnheader">P90</span><span role="columnheader">Fixed schedule ${sTag('fc.clockwork')}</span></div>
            ${M.fcYears.map((y, i) => `<div class="mk-tr num" role="row"><span role="cell">${y}</span><span role="cell">${fmtV(z.fc.p10[i])}</span><span role="cell">${fmtV(z.fc.p50[i])}</span><span role="cell">${fmtV(z.fc.mean[i])}</span><span role="cell">${fmtV(z.fc.p90[i])}</span><span role="cell">${fmtV(z.fc.clockwork[i])}</span></div>`).join('')}
          </div>
        </div>
        <div class="mk-foot">${esc(doc.meta.battery)}. ${esc(doc.meta.money)}. Earned, ceiling and forecast in $/kW-yr. Capacity payments and utility programs are not in these numbers; see “The trader’s read”, Carry.</div>
      </section>`;
  }

  // ---------------------------------------------------------------- q=2 weather regions
  function weatherHTML() {
    const M = doc.markets[st.mk], W = M.weather;
    const s = stripLayout(W);
    let vmax = 0;
    W.forEach((x) => { x.fc.p90.forEach((v) => { vmax = Math.max(vmax, v); }); x.realised2021to2025.forEach((v) => { vmax = Math.max(vmax, v); }); });
    const gmax = niceMax(vmax * 1.04), w = 440, H = 200, sw = 40;
    const Y = (v) => H - Math.max(0, v) / gmax * H, CX = (y) => (y - 2021 + 0.5) * sw;
    const pt = (x, y) => `${x.toFixed(1)} ${y.toFixed(1)}`;
    const heatMax = Math.max(160, ...W.map((x) => Math.max(x.factors.summer_shock_spread_pct.v, x.factors.winter_shock_spread_pct.v)));
    const all = W.flatMap((x) => x.season), smin = Math.min(...all), smax = Math.max(...all);
    const card = (x) => {
      const bars = x.realised2021to2025.map((v, i) => { const x0 = CX(2021 + i) - sw * 0.3; return `M${pt(x0, H)}V${Y(v).toFixed(1)}H${(x0 + sw * 0.6).toFixed(1)}V${H}Z`; }).join('');
      const top = M.fcYears.map((y, i) => pt(CX(y), Y(x.fc.p90[i]))), bot = M.fcYears.map((y, i) => pt(CX(y), Y(x.fc.p10[i]))).reverse();
      const f = x.factors, hd = x.headline;
      let pk = 0;
      x.season.forEach((v, i) => { if (v > x.season[pk]) pk = i; });
      const drivers = [['+5°F July day', f.summer_shock_spread_pct, 'mk-b-heat', heatMax], ['−10°F January day', f.winter_shock_spread_pct, 'mk-b-cold', heatMax],
        ['+50% gas', f.gas50_spread_pct, 'mk-b-gas', heatMax], ['Spike after spike', f.spike_after_spike_pct, 'mk-b-gas', 40]];
      return `<article class="mk-panel mk-wcard"><div class="mk-wh"><div><span class="mk-wt">${esc(x.name)}</span><span class="mk-sub">${esc(x.labels.join(' · '))}</span><span class="mk-note">Station: ${esc(x.station)}</span></div>
          <div class="mk-wv">${num(hd.mean, MONEY)}<span class="mk-note">expected /kW-yr</span></div></div>
        <div class="mk-mini"><span class="mk-ytick num" style="top:0">$${gmax}</span><span class="mk-ytick num" style="top:50%">$${gmax / 2}</span><span class="mk-ytick num" style="top:100%">$0</span>
          <div class="mk-plot" role="img" aria-label="${esc(`${x.name}: realised 2021–25 ${x.realised2021to2025.map((v) => '$' + fmtV(v)).join(', ')}; forecast median 2027–31 ${x.fc.p50.map((v) => '$' + fmtV(v)).join(', ')} per kW-year`)}">
            <svg viewBox="0 0 ${w} ${H}" preserveAspectRatio="none" aria-hidden="true"><path d="M0 ${Y(gmax / 2).toFixed(1)}H${w}M0 ${Y(gmax).toFixed(1)}H${w}" class="mk-grid"></path>
              <path d="${bars}" class="mk-bars"></path><path d="M${top.join('L')}L${bot.join('L')}Z" class="mk-band"></path>
              <path d="M${M.fcYears.map((y, i) => pt(CX(y), Y(x.fc.p50[i]))).join('L')}" class="mk-p50"></path><path d="M240 0V${H}" class="mk-div"></path></svg>
            ${M.fcYears.map((y, i) => `<span class="mk-dot sm" style="left:${(CX(y) / w * 100).toFixed(2)}%;top:${(Y(x.fc.p50[i]) / H * 100).toFixed(2)}%"></span>`).join('')}</div></div>
        <div class="mk-xlabels mini">${Array.from({ length: 11 }, (_, i) => 2021 + i).map((y) => `<span class="num${y >= 2027 ? ' fc' : ''}">${y === 2026 ? '' : '’' + String(y).slice(2)}</span>`).join('')}</div>
        <div class="mk-wstats"><div><span class="mk-sub">σ</span>${num(hd.std, MONEY)}</div><div><span class="mk-sub">P10</span>${num(hd.p10, MONEY)}</div><div><span class="mk-sub">P90</span>${num(hd.p90, MONEY)}</div>
          <div><span class="mk-sub">2025 realised</span>${num(fromSeries(doc, 'weather.realised2021to2025', x.realised2021to2025[4]), MONEY)}</div></div>
        ${drivers.map(([k, v, cls, mx]) => `<div class="mk-drv"><span class="mk-sub">${esc(k)}</span><span class="mk-track"><i class="${cls}" style="width:${Math.min(100, v.v / mx * 100).toFixed(1)}%"></i></span><span>${num(v, k.startsWith('Spike') ? PCT1 : { ...PCT0, signed: true })}</span></div>`).join('')}
        <div class="mk-text">${esc(bookOf(f))}: July spike odds ${num(f.summer_spike_pct, PCT1)} → ${num(f.summer_shock_spike_pct, PCT1)} on a +5°F day; January ${num(f.winter_spike_pct, PCT1)} → ${num(f.winter_shock_spike_pct, PCT1)} on a −10°F day. Busiest month ${MONTHS[pk]} (${num(fromSeries(doc, 'season', x.season[pk]), PCT0)}).</div>
      </article>`;
    };
    return `<div class="mk-head"><div><div class="st-eyebrow">${st.mk} · WEATHER SPLIT</div><div class="st-title mk-h1">2027–31 forecast by weather region</div>
        <div class="mk-headsub">${esc(doc.meta.weatherNote[st.mk])}</div></div></div>
      <section class="mk-panel mk-card" aria-label="Where the outcomes land">
        <div class="mk-card-h"><span class="mk-ct">Where the outcomes land</span><span class="mk-legend"><span><i class="mk-sw-band"></i>P10–P90</span><span><i class="mk-sw-dot"></i>Mean</span><span>${tagHTML(W[0].headline.mean.label, W[0].headline.mean.cite)}</span></span></div>
        <span class="mk-sub">Bad year to good year across ${M.futures * M.fcYears.length} future-years; the dot is the mean. Sorted by mean ÷ σ, best first. $/kW-yr, trading only.</span>
        <div class="mk-strips">
          <div class="mk-srow mk-sh"><span>Weather region</span><span class="mk-saxis">${s.ticks.map((t) => `<span class="num" style="left:${t.left.toFixed(2)}%">$${t.v}</span>`).join('')}</span><span class="mk-snums"><span>Mean</span><span>σ</span><span>P10</span><span>Mean ÷ σ</span></span></div>
          ${s.rows.map((r) => `<div class="mk-srow"><span><b>${esc(r.x.name)}</b><span class="mk-note">${esc(r.x.labels.join(' · '))}</span></span>
            <span class="mk-strip" role="img" aria-label="${esc(`${r.x.name}: P10 $${fmtV(r.x.headline.p10.v)}, mean $${fmtV(r.x.headline.mean.v)}, P90 $${fmtV(r.x.headline.p90.v)} per kW-year`)}">
              ${s.ticks.map((t) => `<i class="g" style="left:${t.left.toFixed(2)}%"></i>`).join('')}<i class="b" style="left:${r.lo.toFixed(2)}%;width:${r.span.toFixed(2)}%"></i><i class="d" style="left:${r.mid.toFixed(2)}%"></i></span>
            <span class="mk-snums num"><span>${fmtV(r.x.headline.mean.v)}</span><span>${fmtV(r.x.headline.std.v)}</span><span>${fmtV(r.x.headline.p10.v)}</span><span><b>${r.ratio.toFixed(2)}</b></span></span></div>`).join('')}
        </div>
        <div class="mk-note">A weather region averages its zones within each future, so a shared heat wave stays shared.</div>
      </section>
      <section class="mk-wgrid" aria-label="Weather region cards">${W.map(card).join('')}</section>
      <section class="mk-panel mk-card" aria-label="Which months pay">
        <div class="mk-card-h"><span class="mk-ct">Which months pay, by weather region</span><span class="mk-legend"><span>Share of a forecast year ${sTag('season')}</span></span></div>
        <div class="mk-heat" role="table" aria-label="Share of forecast value by month">
          <div class="mk-hr mk-th" role="row"><span role="columnheader">Weather region</span>${MON3.map((m) => `<span role="columnheader">${m}</span>`).join('')}</div>
          ${W.map((x) => `<div class="mk-hr" role="row"><span role="rowheader">${esc(x.name)}</span>${x.season.map((v) => { const a = 0.06 + 0.84 * (v - smin) / Math.max(1e-9, smax - smin); return `<span role="cell" class="num${a > 0.5 ? ' dark' : ''}" style="background:rgba(16,35,26,${a.toFixed(3)})">${Math.round(v)}%</span>`; }).join('')}</div>`).join('')}
        </div>
      </section>
      <section class="mk-panel mk-card" aria-label="How zones map to weather"><span class="mk-ct">How zones map to weather</span>
        <div class="mk-table" role="table" aria-label="Zones by weather region">
          <div class="mk-tr mk-th mk-map" role="row"><span role="columnheader">Weather region</span><span role="columnheader">Zones that settle there</span><span role="columnheader">NOAA station</span><span role="columnheader">Typical July high ${tagHTML('DERIVED', W[0].factors.summer_tmax_f.cite)}</span><span role="columnheader">Typical January high</span></div>
          ${W.map((x) => `<div class="mk-tr mk-map" role="row"><span role="cell">${esc(x.name)}</span><span role="cell">${esc(x.labels.join(' · '))}</span><span role="cell">${esc(x.station)}</span><span role="cell" class="num">${fmtV(x.factors.summer_tmax_f.v, 0)}°F</span><span role="cell" class="num">${fmtV(x.factors.winter_tmax_f.v, 0)}°F</span></div>`).join('')}
        </div></section>`;
  }

  // ---------------------------------------------------------------- q=3 trader's read (worked for DFW, NYC, Long Island)
  function readHTML() {
    const E = doc.markets.ERCOT, N = doc.markets.NYISO;
    const dfw = E.zones.find((z) => z.id === 'LZ_NORTH'), nyc = N.zones.find((z) => z.id === 'N.Y.C.'), li = N.zones.find((z) => z.id === 'LONGIL');
    if (!dfw || !nyc || !li) return '<div class="mk-loading">The worked example needs DFW, NYC and Long Island in the file.</div>';
    const cols = [dfw, nyc, li];
    const row = (k, f) => `<div class="mk-tr mk-rd" role="row"><span role="cell">${esc(k)}</span>${cols.map((z) => `<span role="cell">${f(z)}</span>`).join('')}</div>`;
    const ys = E.histYears, yi = [2021, 2022, 2023, 2024, 2025, 2026].map((y) => at(ys, y));
    const cmax = Math.max(...yi.map((i) => dfw.hist.ceiling[i]));
    const cap5 = dfw.headline.capturePct;
    const sumR = derived(yi.slice(0, 5).reduce((a, i) => a + dfw.hist.realised[i], 0), 'realised 2021–25 added');
    const sumC = derived(yi.slice(0, 5).reduce((a, i) => a + dfw.hist.ceiling[i], 0), 'ceiling 2021–25 added');
    const S = doc.stacks, smax = 250;
    const seg = (x, cls) => (x.v > 0 ? `<i class="${cls}" style="width:${(x.v / smax * 100).toFixed(1)}%" title="${esc(`${x.label}: ${x.cite || ''}`)}"></i>` : '');
    const f1 = dfw.factors, f2 = nyc.factors;
    const fb = (a, b, k) => `<div class="mk-fbrow"><span>${esc(k)}</span><span class="mk-fb"><i class="mk-b-heat" style="width:${(a.v / 150 * 100).toFixed(1)}%"></i><i class="mk-b-cold" style="width:${(b.v / 150 * 100).toFixed(1)}%"></i></span><span>${num(a, { ...PCT0, signed: true })}</span><span>${num(b, { ...PCT0, signed: true })}</span></div>`;
    return `<div class="mk-head"><div><div class="st-eyebrow">THE READ</div><div class="st-title mk-h1">How a trader reads this desk</div>
        <div class="mk-headsub">Five words carry the conversation: position, distribution, factor, regime, carry. Worked for DFW (ERCOT LZ_NORTH), with New York City and Long Island as the contrast. All values in $ per kW of battery per year.</div></div></div>
      <section class="mk-panel mk-card mk-say"><span class="st-eyebrow sm">SAY IT IN 20 SECONDS</span>
        <p>“In DFW a home battery is long a strip of daily spread options worth about ${num(dfw.headline.mean, MONEY)} a year in expectation, σ ${num(dfw.headline.std, MONEY)}, right-skewed, with ${num(dfw.headline.top10Pct, PCT0)} of a year made on ten days. Weather is the factor: a July day 5°F hotter lifts the spread ${num(f1.summer_shock_spread_pct, { ...PCT0, signed: true })}. The edge is decaying as batteries crowd in, so trading is the floor and a utility contract is the carry.”</p></section>
      <div class="mk-two">
        <section class="mk-panel mk-card"><span class="mk-step num">01 · Position</span><span class="mk-ct mk-big">Long a strip of daily spread options</span>
          <div class="mk-text">Each day the battery buys its cheapest hours and sells its dearest. It never has to trade, so a flat day costs nothing and a wild day pays a lot: the shape of a long option, long volatility.</div>
          <div class="mk-formula num"><span>daily value = E·P<sub>high</sub> − (E ÷ η)·P<sub>low</sub> − wear·E</span><span class="mk-note">E = MWh sold · η = ${num({ v: doc.constants.ROUND_TRIP.value, label: doc.constants.ROUND_TRIP.label, cite: doc.constants.ROUND_TRIP.cite }, { digits: 2 })} round trip · wear ${num({ v: doc.constants.WEAR_USD_MWH.value, label: doc.constants.WEAR_USD_MWH.label, cite: doc.constants.WEAR_USD_MWH.cite }, { money: true, digits: 0, unit: '/MWh' })}</span></div>
          <div class="mk-text">The perfect-foresight ceiling is the option’s value with hindsight. What a real strategy keeps is the capture ratio: ${num(cap5, PCT0)} in DFW over 2021–25 (${num(sumR, { money: true, digits: 0 })} of ${num(sumC, { money: true, digits: 0 })} cumulative).</div></section>
        <section class="mk-panel mk-card"><span class="mk-step num">02 · Distribution</span><span class="mk-ct mk-big">Quote the distribution, not a number</span>
          <div class="mk-table" role="table" aria-label="DFW, NYC and Long Island, 2027–31">
            <div class="mk-tr mk-th mk-rd" role="row"><span role="columnheader">2027–31, trading only</span><span role="columnheader">DFW</span><span role="columnheader">NYC (J)</span><span role="columnheader">Long Island</span></div>
            ${row('Expected (mean)', (z) => num(z.headline.mean, MONEY))}${row('Volatility (σ)', (z) => num(z.headline.std, MONEY))}
            ${row('Bad year (P10)', (z) => num(z.headline.p10, MONEY))}${row('Good year (P90)', (z) => num(z.headline.p90, MONEY))}
            ${row('Mean ÷ σ', (z) => num(z.headline.meanOverStd, { digits: 2 }))}${row('From the 10 best days', (z) => num(z.headline.top10Pct, PCT0))}
          </div>
          <div class="mk-text">Texas pays more but lumpier; New York pays less but steadier. DFW’s mean sits above its median (${num(dfw.headline.median, MONEY)}): the payoff is right-skewed, so a few scarcity years carry the book. Mean ÷ σ is across simulated future-years, not a time-series Sharpe.</div></section>
        <section class="mk-panel mk-card"><span class="mk-step num">03 · Factor</span><span class="mk-ct mk-big">Weather is the factor; gas is the second</span>
          <div class="mk-text">Each zone has fitted equations for the daily spread and the chance of a spike day, driven by local temperature, Henry Hub gas and yesterday’s prices. One shock’s effect on a typical day’s spread:</div>
          <div class="mk-fbrow mk-fbh"><span></span><span class="mk-legend"><span><i class="mk-b-heat"></i>DFW</span><span><i class="mk-b-cold"></i>NYC</span></span><span>DFW</span><span>NYC</span></div>
          ${fb(f1.summer_shock_spread_pct, f2.summer_shock_spread_pct, '+5°F July day')}${fb(f1.winter_shock_spread_pct, f2.winter_shock_spread_pct, '−10°F January day')}${fb(f1.gas50_spread_pct, f2.gas50_spread_pct, '+50% gas')}
          <div class="mk-text">A +5°F July day takes DFW’s spike odds from ${num(f1.summer_spike_pct, PCT1)} to ${num(f1.summer_shock_spike_pct, PCT1)}. Spikes cluster: after a spike day the next spikes ${num(f1.spike_after_spike_pct, PCT1)} of the time in DFW, ${num(f2.spike_after_spike_pct, PCT1)} in NYC. Texas is a weather book; New York is partly a gas book.</div></section>
        <section class="mk-panel mk-card"><span class="mk-step num">04 · Regime</span><span class="mk-ct mk-big">The edge decays when others crowd in</span>
          <div class="mk-regime" role="img" aria-label="${esc(`DFW ceiling and realised by year: ${yi.map((i) => `${ys[i]} $${fmtV(dfw.hist.ceiling[i])} and $${fmtV(dfw.hist.realised[i])}`).join('; ')}`)}">
            ${yi.map((i) => `<div class="mk-mo"><div class="mk-mo-bars${dfw.partial[i] ? ' ytd' : ''}"><i class="c" style="height:${(dfw.hist.ceiling[i] / cmax * 100).toFixed(1)}%"></i><i class="h" style="height:${(dfw.hist.realised[i] / cmax * 100).toFixed(1)}%"></i></div><span class="num">’${String(ys[i]).slice(2)}${dfw.partial[i] ? ' YTD' : ''}</span></div>`).join('')}</div>
          <span class="mk-legend"><span><i class="mk-sw-ceilbar"></i>Ceiling ${sTag('hist.ceiling')}</span><span><i class="mk-sw-bar"></i>Realised ${sTag('hist.realised')}</span></span>
          <div class="mk-text">DFW’s ceiling went from ${num(fromSeries(doc, 'hist.ceiling', dfw.hist.ceiling[at(ys, 2023)]), { money: true, digits: 0 })} (2023) to ${num(fromSeries(doc, 'hist.ceiling', dfw.hist.ceiling[at(ys, 2025)]), { money: true, digits: 0 })} (2025) as Texas filled with batteries chasing the same evening peak; spikes moved from afternoons to 7–9 pm, then to nights and winter mornings. Same strategy, shrinking opportunity: alpha decay. No single rule won every year, which is why the learner that follows whichever rule is working wins over time.</div></section>
      </div>
      <section class="mk-panel mk-card"><span class="mk-step num">05 · Carry</span>
        <div class="mk-card-h"><span class="mk-ct mk-big">Trading is the floor; the contract is the carry</span>
          <span class="mk-legend"><span><i class="mk-s-energy"></i>Energy trading</span><span><i class="mk-s-cap"></i>Capacity</span><span><i class="mk-s-prog"></i>Program payment</span><span><i class="mk-s-event"></i>Event energy</span></span></div>
        <div class="mk-text">Median 2027–29, $/kW-yr. A fixed payment does not depend on spikes, so it raises the mean and cuts the variance at once. That is why the best sites are contracts, not cleverer trading. NY capacity is an assumption (${num({ v: doc.constants.NY_CAPACITY_NYC.value, label: doc.constants.NY_CAPACITY_NYC.label, cite: doc.constants.NY_CAPACITY_NYC.cite }, { money: true, digits: 0, unit: '/kW-month' })} in NYC).</div>
        <div class="mk-stacks">${S.map((x) => `<div class="mk-stack"><span>${esc(x.site)}</span><span class="mk-sbar">${seg(x.energy, 'mk-s-energy')}${seg(x.capacity, 'mk-s-cap')}${seg(x.program, 'mk-s-prog')}${seg(x.event, 'mk-s-event')}</span>
          <span class="mk-stot">${num(x.total, { money: true, digits: 0 })}</span><span class="mk-note">${x.upgrades.v < 0 ? `after ${num(x.upgrades, { money: true, digits: 1 })} of transformer upgrades` : ''}</span></div>`).join('')}</div>
        <div class="mk-note">Full width = $${smax}/kW-yr. Hover a segment for its source.</div>
        <div class="mk-risks"><b>What would change the call.</b> New wires remove a pocket’s edge: ${doc.infrastructure.map((x) => `${esc(x.event)} (${esc(x.label)}) ${sMoney(x.effect)}`).join('; ')}. Texas contract values are not published prices. Faster battery build-out would compress spreads again, as it did in 2024–26.</div>
      </section>`;
  }

  // ---------------------------------------------------------------- render + events
  function render() {
    const body = st.view === 1 ? deskHTML() : st.view === 2 ? weatherHTML() : readHTML();
    const scroll = el.querySelector('.mk-body');
    const top = scroll ? scroll.scrollTop : 0;
    el.innerHTML = `${railHTML()}<div class="mk-body">${body}</div>`;
    const nb = el.querySelector('.mk-body');
    if (nb) nb.scrollTop = top;
    saveState({ mk: st.mk, zone: st.zone });
  }
  function setYear(y) {
    if (y === st.year) return;
    st.year = y;
    const focused = document.activeElement && document.activeElement.dataset ? document.activeElement.dataset.year : null;
    render();
    if (focused) { const b = el.querySelector(`.mk-col[data-year="${focused}"]`); if (b) b.focus(); }
  }
  const onClick = (ev) => {
    const t = ev.target.closest('button');
    if (!t || !el.contains(t)) return;
    if (t.dataset.mk) { st.mk = t.dataset.mk; render(); return; }
    if (t.dataset.q) {
      st.view = viewOf(Number(t.dataset.q));
      history.replaceState(history.state, '', ctx.link({ page: PAGE, q: st.view }));
      el.querySelector('.mk-body') && (el.querySelector('.mk-body').scrollTop = 0);
      render();
      return;
    }
    if (t.dataset.zone) { st.zone[st.mk] = t.dataset.zone; render(); return; }
    if (t.dataset.year) setYear(Number(t.dataset.year));
  };
  const onOver = (ev) => { const t = ev.target.closest && ev.target.closest('.mk-col'); if (t) setYear(Number(t.dataset.year)); };
  const onFocus = (ev) => { const t = ev.target.closest && ev.target.closest('.mk-col'); if (t) setYear(Number(t.dataset.year)); };
  el.addEventListener('click', onClick);
  el.addEventListener('mouseover', onOver);
  el.addEventListener('focusin', onFocus);
  render();
  return {
    dispose() {
      el.removeEventListener('click', onClick);
      el.removeEventListener('mouseover', onOver);
      el.removeEventListener('focusin', onFocus);
    },
  };
}
