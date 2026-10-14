// ui/story/dispatch.js: Screen 8 Dispatch, "How do you run a battery safely and prove what it earned?" (dispatch/README.md).
// One file, ui/data/dispatch/index.json (hb.dispatch.v1, written by scripts/export_dispatch.py from the platform's own artifacts).
// Left rail: the four views (?q=1..4) and a view selector. Four views:
//   q=1 One cycle       a replayed cycle as a pipeline: ERCOT publishes, features, forecast fan, LP plan, guard, ledger, audit hash
//   q=2 What it earned  the public case study (PAD1_ESS_ESR1, 42 days): actual, shadow, perfect foresight, the gap split, and the honesty note
//   q=3 Safe by design  the gauntlet a setpoint runs, the guard, the kill switch, the monitoring policy, the hash-chained audit log
//   q=4 Ready for what  gates 1 to 3 with their honest status, the model registry, forecast metrics, the LP against the DP, what is open
// Every value shown carries its tag: headline numbers come labelled from the file, bulk arrays take their label from the file's `series`,
// and a value this page computes is DERIVED with its formula as the cite. Safety is sage, never the brand green (green means ours/selected).
// No language model produces a setpoint here: the forecaster only forecasts prices, the LP (code) chooses, the guard (code) checks.

import { tagHTML } from './shell.js';

export const PAGE = 'dispatch';
export const DATA = 'dispatch/index.json';
export const VIEWS = [
  { q: 1, title: 'One cycle', sub: 'ERCOT data in, a signed paper setpoint out' },
  { q: 2, title: 'What it earned', sub: 'One public battery against perfect foresight' },
  { q: 3, title: 'Safe by design', sub: 'What stops a bad setpoint, and how it halts' },
  { q: 4, title: 'Ready for what', sub: 'Three gates, the registry, what is still open' },
];
export const SCOPES = [['all_AS', 'All services'], ['no_regulation', 'No regulation'], ['energy_only', 'Energy only']];
const STORE = 'hb-dispatch';
const PRODUCTS = ['REGUP', 'REGDN', 'RRS', 'ECRS', 'NSPIN'];

const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const fin = (x) => typeof x === 'number' && Number.isFinite(x);
const MONEY0 = { money: true, digits: 0 };
const MONEY2 = { money: true, digits: 2 };
const PCT0 = { unit: '%', digits: 0 };
const PCT1 = { unit: '%', digits: 1 };
/** A computed string (a time, a hash, a count) shown as a number: the series tag of its panel or column says where it comes from. */
const T = (s) => `<span class="num">${esc(s)}</span>`;
const fmtN = (v, d = 0) => {
  if (!fin(v)) return '—';
  const out = Number(v).toLocaleString('en-US', { minimumFractionDigits: d, maximumFractionDigits: d });
  return /^-0(\.0+)?$/.test(out) ? out.slice(1) : out;
};
const money = (v, d = 0) => (Number(fmtN(Math.abs(v), d).replace(/,/g, '')) === 0 || v >= 0 ? '$' : '−$') + fmtN(Math.abs(v), d);
const sci = (v, d = 1) => Number(v).toExponential(d).replace('e-0', 'e-').replace('e+0', 'e').replace('e+', 'e');

/** The hubs where the plan built on the model's median captures less than the plan built on the plain 7-day profile. */
export const behindHubs = (F) => F.hubs.filter((x) => F.model[x].capture < F.base.profile_median_7d[x].capture);
/** The labelled numbers with the lowest and the highest value in a list. */
export const lowHigh = (xs) => xs.reduce((a, x) => ({ lo: x.v < a.lo.v ? x : a.lo, hi: x.v > a.hi.v ? x : a.hi }), { lo: xs[0], hi: xs[0] });

export const viewOf = (q) => ([1, 2, 3, 4].includes(q) ? q : 1);

/** A bulk value from the file's `series` as a labelled number (label and text from the series entry). */
export function fromSeries(doc, key, v) {
  const s = doc && doc.series && doc.series[key];
  if (!s) throw new Error(`dispatch: no series ${key}`);
  return { v, label: s.label, cite: s.text };
}
/** A number this page computes from labelled inputs: DERIVED, with the arithmetic in the cite. */
export const derived = (v, cite) => ({ v, label: 'DERIVED', cite: `DERIVED: ${cite}` });
/** A labelled number with another value (a unit change of a file value), the same label and cite. */
const rescale = (x, v) => ({ ...x, v });

// ---------------------------------------------------------------------------------------------------------------- time
const TZ = 'America/Chicago';
const F_HM = new Intl.DateTimeFormat('en-GB', { timeZone: TZ, hour: '2-digit', minute: '2-digit', hourCycle: 'h23' });
const F_HMS = new Intl.DateTimeFormat('en-GB', { timeZone: TZ, hour: '2-digit', minute: '2-digit', second: '2-digit', hourCycle: 'h23' });
const F_DAY = new Intl.DateTimeFormat('en-GB', { timeZone: TZ, day: 'numeric', month: 'short' });
const F_ABBR = new Intl.DateTimeFormat('en-US', { timeZone: TZ, timeZoneName: 'short' });
const ms = (iso) => Date.parse(iso);
/** "2026-10-09T22:45:00Z" -> "17:45" (Central Prevailing Time, ERCOT's clock). */
export const cptHM = (iso) => F_HM.format(new Date(ms(iso)));
export const cptHMS = (iso) => F_HMS.format(new Date(ms(iso)));
/** "9 Oct". */
export const cptDay = (iso) => F_DAY.format(new Date(ms(iso)));
/** "CDT" or "CST". */
export const cptAbbr = (iso) => (F_ABBR.formatToParts(new Date(ms(iso))).find((p) => p.type === 'timeZoneName') || { value: 'CT' }).value;
export const addMin = (iso, m) => new Date(ms(iso) + m * 60000).toISOString();
export const utcHM = (iso) => String(iso).slice(11, 16);
/** "2026-07-22" -> "22 Jul". */
export function shortDate(iso) {
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(String(iso || ''));
  const MON = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
  return m ? `${Number(m[3])} ${MON[Number(m[2]) - 1]}` : String(iso || '');
}

// ---------------------------------------------------------------------------------------------------------------- geometry (pure, tested)
/** A "nice" axis covering [lo, hi] in about `want` steps. */
export function niceAxis(lo, hi, want = 4) {
  const span = Math.max(hi - lo, 1e-9);
  const raw = span / want;
  const mag = 10 ** Math.floor(Math.log10(raw));
  const step = [1, 2, 2.5, 5, 10].map((m) => m * mag).find((s) => s >= raw * 0.999) || 10 * mag;
  const a = Math.floor(lo / step + 1e-9) * step;
  const b = Math.ceil(hi / step - 1e-9) * step;
  const ticks = [];
  for (let k = 0; a + k * step <= b + step * 1e-6; k++) ticks.push(Math.round((a + k * step) / step * 1e6) / 1e6 * step);
  return { lo: a, hi: b, step, ticks };
}
const pt = (x, y) => `${x.toFixed(1)} ${y.toFixed(1)}`;

/** The forecast fan of decision i in a W x H viewBox: P10..P90 band, P50, the seven-day profile median, the first interval, the peak. */
export function fanGeom(cy, i, W = 960, H = 260) {
  const p10 = cy.fan.p10[i], p50 = cy.fan.p50[i], p90 = cy.fan.p90[i], prof = cy.prof[i];
  const n = p50.length;
  const ax = niceAxis(Math.min(0, ...p10), Math.max(...p90, ...prof), 4);
  const X = (k) => (k + 0.5) / n * W;
  const Y = (v) => H - (v - ax.lo) / (ax.hi - ax.lo) * H;
  const line = (a) => 'M' + a.map((v, k) => pt(X(k), Y(v))).join('L');
  const top = p90.map((v, k) => pt(X(k), Y(v)));
  const bot = p10.map((v, k) => pt(X(k), Y(v))).reverse();
  let peak = 0;
  p50.forEach((v, k) => { if (v > p50[peak]) peak = k; });
  const t0 = cy.fanStart[i];
  const xt = [];
  for (let k = 0; k < n; k += 16) xt.push({ k, left: (k + 0.5) / n * 100, t: addMin(t0, k * 15) });
  return {
    W, H, n, ax, band: `M${top.join('L')}L${bot.join('L')}Z`, p50: line(p50), prof: line(prof),
    grid: ax.ticks.map((v) => `M0 ${Y(v).toFixed(1)}H${W}`).join(''), zeroY: Y(0),
    first: { left: 0.5 / n * 100, top: Y(p50[0]) / H * 100, w: W / n }, peak: { k: peak, v: p50[peak], left: (peak + 0.5) / n * 100, top: Y(p50[peak]) / H * 100, t: addMin(t0, peak * 15) },
    ticks: ax.ticks.map((v) => ({ v, top: Y(v) / H * 100 })), xt,
  };
}

/** The three lanes of the replayed day (price expected for the interval being decided, net setpoint, state of charge) in W x H boxes. */
export function laneGeom(cy, W = 960) {
  const n = cy.n, P = cy.summary.powerMw.v, E = cy.summary.energyMwh.v;
  const lo = cy.fan.p10.map((a) => a[0]), mid = cy.fan.p50.map((a) => a[0]), hi = cy.fan.p90.map((a) => a[0]);
  const pa = niceAxis(Math.min(0, ...lo), Math.max(...hi), 2);
  const X = (k) => (k + 0.5) / n * W;
  const bw = W / n;
  const HP = 76, HN = 66, HS = 58;
  const YP = (v) => HP - (v - pa.lo) / (pa.hi - pa.lo) * HP;
  const top = hi.map((v, k) => pt(X(k), YP(v))), bot = lo.map((v, k) => pt(X(k), YP(v))).reverse();
  const YN = (v) => HN / 2 - v / P * (HN / 2);
  const bars = cy.net.map((v, k) => ({ k, x: k * bw + bw * 0.12, w: bw * 0.76, y: Math.min(YN(v), HN / 2), h: Math.abs(YN(v) - HN / 2), pos: v > 0, zero: Math.abs(v) < 1e-9 }));
  const YS = (v) => HS - v / E * HS;
  const soc = cy.soc1.map((v, k) => pt(X(k), YS(v)));
  return {
    W, n, HP, HN, HS, price: { ax: pa, band: `M${top.join('L')}L${bot.join('L')}Z`, p50: 'M' + mid.map((v, k) => pt(X(k), YP(v))).join('L'),
      ticks: [...new Set([pa.hi, 0, pa.lo])].map((v) => ({ v, top: YP(v) / HP * 100 })), zeroY: YP(0) },
    net: { bars, ticks: [P, 0, -P].map((v) => ({ v, top: YN(v) / HN * 100 })), zeroY: HN / 2 },
    soc: { line: 'M' + soc.join('L'), area: `M${pt(X(0), HS)}L${soc.join('L')}L${pt(X(n - 1), HS)}Z`, ticks: [E, 0].map((v) => ({ v, top: YS(v) / HS * 100 })) },
    xt: Array.from({ length: Math.ceil(n / 16) }, (_, j) => ({ k: j * 16, left: (j * 16 + 0.5) / n * 100, t: cy.t[j * 16] })),
  };
}

/** The moments worth jumping to, found by rule from the 96 decisions (no typed indices). Pure (tested). */
export function keyMoments(cy) {
  const net = cy.net;
  const iSell = net.indexOf(Math.max(...net));
  const minNet = Math.min(...net);
  const iCharge = net.lastIndexOf(minNet);
  let iPeak = 0, best = -Infinity;
  cy.fan.p50.forEach((a, i) => { const m = Math.max(...a.slice(0, 16)); if (m > best) { best = m; iPeak = i; } });
  const iDam = cy.rpt.findIndex((r) => r !== 'NP6-905-CD');
  const out = [
    { id: 'charge', i: iCharge, title: 'Charges before the peak', why: 'The forecast shows a price peak ahead; the LP fills the battery.' },
    { id: 'peak', i: iPeak, title: 'Highest price ahead', why: 'The decision whose forecast reaches the highest median in the next four hours.' },
    { id: 'sell', i: iSell, title: 'Sells at full power', why: 'The largest discharge of the day.' },
  ];
  if (iDam >= 0) out.push({ id: 'dam', i: iDam, title: 'Day-ahead results arrive', why: 'The newest input is the day-ahead market, not the fifteen-minute price file.' });
  return out;
}
export const defaultDecision = (cy) => keyMoments(cy).find((m) => m.id === 'sell').i;

/** Cumulative actual, shadow and perfect foresight over the 42 days in a W x H viewBox. */
export function cumGeom(earned, W = 960, H = 300) {
  const d = earned.days, n = d.date.length;
  const cum = (a) => { let s = 0; return a.map((v) => (s += v)); };
  const series = { actual: cum(d.actual), shadow: cum(d.shadow), pf: cum(d.pf) };
  const ax = niceAxis(0, Math.max(...series.pf), 4);
  const X = (k) => (k + 1) / n * W;
  const Y = (v) => H - (v - ax.lo) / (ax.hi - ax.lo) * H;
  const path = (a) => `M${pt(0, Y(0))}L` + a.map((v, k) => pt(X(k), Y(v))).join('L');
  const xt = [];
  for (let k = 0; k < n; k += 7) xt.push({ k, left: (k + 0.5) / n * 100, date: d.date[k] });
  return {
    W, H, n, ax, series, paths: { actual: path(series.actual), shadow: path(series.shadow), pf: path(series.pf) },
    grid: ax.ticks.map((v) => `M0 ${Y(v).toFixed(1)}H${W}`).join(''), ticks: ax.ticks.map((v) => ({ v, top: Y(v) / H * 100 })),
    ends: { actual: Y(series.actual[n - 1]) / H * 100, shadow: Y(series.shadow[n - 1]) / H * 100, pf: Y(series.pf[n - 1]) / H * 100 }, xt,
    dayLeft: (k) => (k + 0.5) / n * 100,
  };
}

/** The gap waterfall as rows of {from, to} on a 0..perfect-foresight track (percent). Pure (tested). */
export function waterfallRows(earned) {
  const w = Object.fromEntries(earned.waterfall.map((x) => [x.id, x.v.v]));
  const c = Object.fromEntries(earned.components.map((x) => [x.id, x.usd]));
  const top = w.pf;
  const P = (v) => v / top * 100;
  const steps = [
    { id: 'pf', name: 'Perfect foresight', from: 0, to: w.pf, total: true },
    { id: 'availability', name: 'Derates the asset reported', from: w.availability, to: w.pf, comp: c.availability },
    { id: 'as', name: 'Ancillary awards it did not hold', from: w.as, to: w.availability, comp: c.as },
    { id: 'soc', name: 'The charge it carried into each day', from: w.soc, to: w.as, comp: c.soc },
    { id: 'forecast', name: 'Forecast and decision', from: w.actual, to: w.soc, comp: c.forecast },
    { id: 'actual', name: 'What the battery earned', from: 0, to: w.actual, total: true },
  ];
  return steps.map((s) => ({ ...s, left: P(s.from), width: P(s.to) - P(s.from) }));
}

/** Scope bars: actual, shadow and perfect foresight per scope on one shared scale (percent of the largest). */
export function scopeBars(earned) {
  const top = Math.max(...earned.scopes.map((s) => s.pf.v));
  return earned.scopes.map((s) => ({ id: s.id, name: s.name, a: s.actual.v, s: s.shadow.v, actual: s.actual.v / top * 100, shadow: s.shadow.v / top * 100, pf: s.pf.v / top * 100 }));
}

/** Small-multiples bars for the forecast comparison: pinball loss per hub, model first, on one axis. */
export function pinballRows(fc) {
  const rows = fc.hubs.map((h) => {
    const items = [{ id: 'model', name: 'This model', v: fc.model[h].pinball }].concat(fc.baselines.map((b) => ({ id: b.id, name: b.name, v: fc.base[b.id][h].pinball })));
    return { hub: h, items };
  });
  const max = Math.max(...rows.flatMap((r) => r.items.map((x) => x.v)));
  const ax = niceAxis(0, max, 4);
  rows.forEach((r) => r.items.forEach((x) => { x.pct = x.v / ax.hi * 100; }));
  return { rows, ax };
}

/** Which of the 20 largest missed intervals fall on which Central day: [{day, n}] by count, then date. Pure (tested). */
export function topByDay(earned) {
  const m = new Map();
  earned.top.forEach((r) => { const d = cptDay(r.utc); m.set(d, (m.get(d) || 0) + 1); });
  return [...m.entries()].map(([day, n]) => ({ day, n })).sort((a, b) => b.n - a.n);
}

// ---------------------------------------------------------------------------------------------------------------- tags
const sTagOf = (doc) => (key) => { const s = doc.series[key]; return tagHTML(s.label, s.text); };
const role = (kind, text) => `<span class="dp-role dp-role-${kind}">${esc(text)}</span>`;
const code = (c) => `<code class="dp-code">${esc(c)}</code>`;

// ---------------------------------------------------------------------------------------------------------------- rail
export function railHTML(doc, st, h) {
  const views = VIEWS.map((v) => `<button type="button" class="dp-view${v.q === st.view ? ' on' : ''}" data-q="${v.q}" aria-pressed="${v.q === st.view}">
      <span class="dp-qn" aria-hidden="true"></span><span><span class="dp-qt">${esc(v.title)}</span><span class="dp-qs">${esc(v.sub)}</span></span></button>`).join('');
  let extra = '';
  if (st.view === 1) {
    const mo = keyMoments(doc.cycle);
    extra = `<div class="dp-rail-h st-eyebrow sm">JUMP TO A MOMENT</div><div class="dp-moments">${mo.map((m) => `<button type="button" class="dp-moment${m.i === st.i ? ' on' : ''}" data-moment="${m.i}" aria-pressed="${m.i === st.i}">
        <span class="dp-mt">${esc(m.title)}</span><span class="dp-ms">${T(cptHM(doc.cycle.t[m.i]))} ${esc(cptAbbr(doc.cycle.t[m.i]))} · ${esc(m.why)}</span></button>`).join('')}</div>
      <div class="dp-note dp-rail-note">A replay of ${h.num(doc.cycle.summary.cycles)} paper decisions on ${T(cptDay(doc.cycle.firstUtc))} and ${T(cptDay(doc.cycle.lastUtc))}, run through the real service, LP, guard, ledger and audit log. Drag the slider or pick a moment.</div>`;
  } else if (st.view === 2) {
    extra = `<div class="dp-rail-h st-eyebrow sm">BENCHMARK SCOPE</div>
      <div class="dp-moments" role="group" aria-label="Benchmark scope">${SCOPES.map(([id, t]) => { const sc = doc.earned.scopes.find((x) => x.id === id); const d = sc ? sc.desc.replace(/ \(headline\)$/, '').replace(/^energy/, 'Energy') : '';
        return `<button type="button" class="dp-moment${id === st.scope ? ' on' : ''}" data-scope="${id}" aria-pressed="${id === st.scope}"><span class="dp-mt">${esc(t)}</span><span class="dp-ms">${esc(d)}</span></button>`; }).join('')}</div>
      <div class="dp-note dp-rail-note">Which products the perfect-foresight benchmark and the shadow optimizer may sell. The real battery is held to the same scope. Switching it changes the tiles and the sentences below, and it flips who is ahead.</div>`;
  } else if (st.view === 3) {
    extra = `<div class="dp-rail-h st-eyebrow sm">HOW TO READ THE COLOURS</div>
      <div class="dp-keys"><span><i class="dp-k-ok"></i>Sage: a check that passed or a path that stays open</span><span><i class="dp-k-stop"></i>Outlined: where a setpoint is stopped (the system working)</span>
      <span><i class="dp-k-bad"></i>Red: something actually broken or tampered with</span></div>
      <div class="dp-note dp-rail-note">Green on this page would mean ours or selected. Safe is never green.</div>`;
  } else {
    extra = `<div class="dp-rail-h st-eyebrow sm">HOW TO READ THE STATUS</div>
      <div class="dp-keys"><span><i class="dp-k-ok"></i>Sage: passed</span><span><i class="dp-k-part"></i>Sand: met, but not proven</span><span><i class="dp-k-open"></i>Dashed: not passed yet</span></div>
      <div class="dp-note dp-rail-note">Statuses are the platform's own, copied from its README. Nothing here is rounded up.</div>`;
  }
  return `<aside class="dp-rail"><div class="st-eyebrow">DISPATCH</div><div class="dp-qtext">${esc(doc.meta.question)}</div>
    <nav class="dp-views" aria-label="Views">${views}</nav>${extra}</aside>`;
}

// ---------------------------------------------------------------------------------------------------------------- q=1 one cycle
const WHO = `<section class="dp-who" aria-label="Who decides">
    <div class="dp-who-b"><span class="dp-role dp-role-model">model</span><b>Forecasts prices</b><span class="dp-sub">A gradient-boosted quantile model. It outputs a low, a middle and a high price path, and nothing else.</span></div>
    <span class="dp-arrow" aria-hidden="true">→</span>
    <div class="dp-who-b"><span class="dp-role dp-role-code">code</span><b>The LP chooses the setpoint</b><span class="dp-sub">A linear program: the same inputs always give the same answer.</span></div>
    <span class="dp-arrow" aria-hidden="true">→</span>
    <div class="dp-who-b"><span class="dp-role dp-role-code">code</span><b>The guard checks it</b><span class="dp-sub">Outside the model. It rejects; it never edits a setpoint.</span></div></section>`;

/** The seven stages of cycle i as {title, role, html}. `h.num` renders a labelled value. */
export function stagesOf(doc, i, h) {
  const cy = doc.cycle, S = cy.summary, num = h.num;
  const t = cy.t[i], pub = cy.pub[i];
  const isPrice = cy.rpt[i] === 'NP6-905-CD';
  const lim = S.featureAgeLimit;
  const age = derived(cy.age[i] / 60, 'decision time minus the file’s publish time, ÷ 60');
  const f = cy.fan, net = cy.net[i];
  const peak = fanGeom(cy, i).peak;
  const first = (k) => fromSeries(doc, 'cycle.fan', f[k][i][0]);
  const aw = PRODUCTS.map((p) => [p, cy.award[p][i]]).filter(([, v]) => v > 0.005);
  const act = Math.abs(net) < 0.005 ? 'Hold' : net > 0 ? 'Sell' : 'Buy';
  const reasons = cy.reasons[i];
  return [
    { title: 'ERCOT publishes', role: role('data', 'data'), html: `
      <div class="dp-sv">${num({ v: `${cptHMS(pub)} ${cptAbbr(pub)}`, label: 'REAL', cite: doc.series['cycle.pub'].text })}</div>
      <div class="dp-ss">${isPrice ? 'The real-time settlement point prices (' + code('NP6-905-CD') + ')' : 'The day-ahead market results (' + code('NP4-188-CD') + '), which the model reads'}.</div>
      <div class="dp-ss">The lake keeps the file as published, unchanged. Fingerprint ${T(cy.fileSha[i])}.</div>` },
    { title: 'Features as of now', role: role('data', 'data'), html: `
      <div class="dp-sv">${num(age, { digits: 1, unit: ' min' })}</div>
      <div class="dp-meter" role="img" aria-label="Feature age against its limit"><i style="width:${Math.min(100, cy.age[i] / lim.v * 100).toFixed(1)}%"></i></div>
      <div class="dp-ss">since the newest input was published.</div>
      <div class="dp-ss">Limit ${num(rescale(lim, lim.v / 60), { digits: 0, unit: ' min' })}. Older means no dispatch. Nothing published after this instant is visible.</div>
      <div class="dp-ss">Fingerprint ${T(cy.featuresSha[i])}.</div>` },
    { title: 'Price forecast', role: role('model', 'model · forecasts prices only'), html: `
      <div class="dp-sv">${num(first('p50'), { ...MONEY0, unit: '/MWh' })}</div>
      <div class="dp-ss">next interval, median. Eight in ten prices fall between ${num(first('p10'), MONEY0)} and ${num(first('p90'), MONEY0)}.</div>
      <div class="dp-ss">Peak ahead ${num(fromSeries(doc, 'cycle.fan', peak.v), MONEY0)} at ${T(cptHM(peak.t))} ${esc(cptAbbr(peak.t))}. Version ${T(cy.modelVersion)}.</div>` },
    { title: 'LP plan', role: role('code', 'code · decides'), html: `
      <div class="dp-sv">${act} ${act === 'Hold' ? '' : num(fromSeries(doc, 'cycle.plan', Math.abs(net)), { digits: 1, unit: ' MW' })}</div>
      <div class="dp-ss">${aw.length ? 'Ancillary awards held: ' + aw.map(([p, v]) => `${esc(p)} ${T(fmtN(v, 1))} MW`).join(', ') : 'No ancillary award this interval'}.</div>
      <div class="dp-ss">Plan value ${num(fromSeries(doc, 'cycle.plan', cy.obj[i]), MONEY0)} over the horizon. ${esc(cy.solver)}, ${esc(cy.status[i])}.</div>` },
    { title: 'Guard', role: role('code', 'code · checks'), html: `
      <div class="dp-sv"><span class="dp-verdict ${cy.ok[i] ? 'ok' : 'stop'}">${cy.ok[i] ? 'Accepted' : 'Rejected'}</span></div>
      <div class="dp-ss">${reasons.length ? 'Reason codes: ' + reasons.map(code).join(' ') : 'No reason codes. Power, charge, awards and exposure are inside their limits.'}</div>
      <div class="dp-ss">Exposure ${num(fromSeries(doc, 'cycle.plan', cy.exposure[i]), MONEY0)} against a cap of ${num(S.exposureCap, MONEY0)}.</div>` },
    { title: 'Paper ledger', role: role('code', 'code · records'), html: `
      <div class="dp-sv">Entry ${T(cy.ledgerSeq[i])}</div>
      <div class="dp-ss">Charge ${num(fromSeries(doc, 'cycle.plan', cy.soc0[i]), { digits: 1 })} to ${num(fromSeries(doc, 'cycle.plan', cy.soc1[i]), { digits: 1, unit: ' MWh' })}. Cost basis ${num(fromSeries(doc, 'cycle.plan', cy.basis[i]), MONEY0)}.</div>
      <div class="dp-ss">A record on a paper ledger, not an order: the exchange adapter is switched off.</div>` },
    { title: 'Audit hash', role: role('code', 'code · proves'), html: `
      <div class="dp-sv dp-mono">${T(cy.hash.guard[i].slice(0, 10) + '…')}</div>
      <div class="dp-ss">The guard’s verdict, written to disk before anything executes. It holds the hash of the record before it (${T(cy.hash.prev[i].slice(0, 8) + '…')}).</div>
      <div class="dp-ss">Chain of ${num(S.auditRecords)} records, each hash recomputed: it verifies.</div>` },
  ];
}

function fanSVG(doc, i) {
  const g = fanGeom(doc.cycle, i);
  const xl = g.xt.map((x) => `<span class="dp-xl" style="left:${x.left.toFixed(2)}%"><span class="num">${esc(cptHM(x.t))}</span></span>`).join('');
  return `<div class="dp-plotwrap dp-fanwrap">
      ${g.ticks.map((t) => `<span class="dp-ytick num" style="top:${t.top.toFixed(2)}%">${money(t.v)}</span>`).join('')}
      <div class="dp-plot" role="img" aria-label="Forecast fan for the next day: median, and the band that holds eight prices in ten">
        <svg viewBox="0 0 ${g.W} ${g.H}" preserveAspectRatio="none" aria-hidden="true">
          <path d="${g.grid}" class="dp-grid"></path>
          <rect x="0" y="0" width="${g.first.w.toFixed(1)}" height="${g.H}" class="dp-sel"></rect>
          <path d="${g.band}" class="dp-band"></path><path d="${g.prof}" class="dp-prof"></path><path d="${g.p50}" class="dp-p50"></path>
        </svg>
        <span class="dp-dot" style="left:${g.first.left.toFixed(2)}%;top:${g.first.top.toFixed(2)}%"></span>
        ${g.peak.k === 0 ? '' : `<span class="dp-dot peak" style="left:${g.peak.left.toFixed(2)}%;top:${g.peak.top.toFixed(2)}%"></span>`}
        <span class="dp-flag" style="left:${g.first.left.toFixed(2)}%">${g.peak.k === 0 ? 'decided here, and the highest median' : 'decided here'}</span>
        ${g.peak.k === 0 ? '' : `<span class="dp-flag r" style="left:${g.peak.left.toFixed(2)}%;top:${Math.max(0, g.peak.top - 12).toFixed(1)}%">peak ahead</span>`}
      </div>
    </div><div class="dp-xlabels">${xl}</div>`;
}

function battSVG(doc, i, num) {
  const cy = doc.cycle, S = cy.summary, E = S.energyMwh.v, P = S.powerMw.v;
  const s0 = cy.soc0[i], s1 = cy.soc1[i], net = cy.net[i];
  const aw = PRODUCTS.map((p) => [p, cy.award[p][i]]);
  const w = (v) => Math.min(100, Math.abs(v) / P * 50);
  return `<section class="dp-panel dp-batt" aria-label="The battery at this moment">
      <div class="dp-card-h"><span class="dp-ct">The battery at this moment</span><span class="dp-legend">${h_sTag(doc, 'cycle.plan')}</span></div>
      <div class="dp-bl"><span class="dp-sub">Charge, ${num(S.energyMwh, { digits: 0, unit: ' MWh' })} battery</span>
        <div class="dp-gauge" role="img" aria-label="State of charge before and after this interval"><i class="dp-g0" style="width:${(s0 / E * 100).toFixed(1)}%"></i>
          <i class="dp-g1" style="left:${(Math.min(s0, s1) / E * 100).toFixed(1)}%;width:${(Math.abs(s1 - s0) / E * 100).toFixed(1)}%"></i></div>
        <span class="dp-gl">${num(fromSeries(doc, 'cycle.plan', s0), { digits: 1 })} <span class="dp-dim">to</span> ${num(fromSeries(doc, 'cycle.plan', s1), { digits: 1, unit: ' MWh' })}</span></div>
      <div class="dp-bl"><span class="dp-sub">Setpoint, ${num(S.powerMw, { digits: 0, unit: ' MW' })} rating</span>
        <div class="dp-diverge" role="img" aria-label="Net setpoint, charge to the left and discharge to the right"><i class="mid"></i>
          <i class="${net >= 0 ? 'sell' : 'buy'}" style="${net >= 0 ? 'left:50%' : `right:50%`};width:${w(net).toFixed(1)}%"></i></div>
        <span class="dp-gl dp-two"><span class="dp-dim">buy</span><span>${net === 0 ? 'hold' : (net > 0 ? 'sell ' : 'buy ') + T(fmtN(Math.abs(net), 1) + ' MW')}</span><span class="dp-dim">sell</span></span></div>
      <div class="dp-bl"><span class="dp-sub">Ancillary awards held (MW)</span>
        ${aw.map(([p, v]) => `<div class="dp-awrow"><span>${esc(p)}</span><span class="dp-track"><i style="width:${Math.min(100, v / P * 100).toFixed(1)}%"></i></span><span class="num">${fmtN(v, 1)}</span></div>`).join('')}</div>
    </section>`;
}
function h_sTag(doc, key) { return sTagOf(doc)(key); }

export function cycleTopHTML(doc, st, h) {
  const cy = doc.cycle, S = cy.summary, i = st.i, g = laneGeom(cy);
  const xl = g.xt.map((x) => `<span class="dp-xl" style="left:${x.left.toFixed(2)}%"><span class="num">${esc(cptHM(x.t))}</span></span>`).join('');
  const lane = (title, ticks, svg, hgt, fmt) => `<div class="dp-lane"><span class="dp-lane-t">${esc(title)}</span><div class="dp-lane-box" style="height:${hgt}px">
      ${ticks.map((t) => `<span class="dp-ytick num" style="top:${t.top.toFixed(1)}%">${fmt(t.v)}</span>`).join('')}<div class="dp-lane-plot">${svg}</div></div></div>`;
  const priceSvg = `<svg viewBox="0 0 ${g.W} ${g.HP}" preserveAspectRatio="none" aria-hidden="true"><path d="M0 ${g.price.zeroY.toFixed(1)}H${g.W}" class="dp-grid"></path><path d="${g.price.band}" class="dp-band"></path><path d="${g.price.p50}" class="dp-p50 thin"></path></svg>`;
  const netSvg = `<svg viewBox="0 0 ${g.W} ${g.HN}" preserveAspectRatio="none" aria-hidden="true"><path d="M0 ${g.net.zeroY}H${g.W}" class="dp-grid"></path>${g.net.bars.filter((b) => !b.zero).map((b) => `<rect x="${b.x.toFixed(1)}" y="${b.y.toFixed(1)}" width="${b.w.toFixed(1)}" height="${Math.max(b.h, 0.8).toFixed(1)}" class="${b.pos ? 'dp-bar-sell' : 'dp-bar-buy'}"></rect>`).join('')}</svg>`;
  const socSvg = `<svg viewBox="0 0 ${g.W} ${g.HS}" preserveAspectRatio="none" aria-hidden="true"><path d="${g.soc.area}" class="dp-soc-area"></path><path d="${g.soc.line}" class="dp-soc-line"></path></svg>`;
  return `<div class="dp-head"><div><div class="st-eyebrow">ONE CYCLE · REPLAYED ${T(cptDay(cy.firstUtc))} TO ${T(cptDay(cy.lastUtc))} · ${esc(cy.asset)}</div>
        <div class="st-title dp-h1">From a price file to a signed paper entry</div>
        <div class="dp-headsub">Every quarter of an hour the platform takes the newest ERCOT prices, forecasts the next day, lets a linear program choose what the battery does in the next interval, lets a guard check it, and writes it to a ledger and a hash-chained log. The whole cycle takes about ${h.num(S.latencyP50, { digits: 2, unit: ' s' })} here.</div></div>
        <div class="dp-headr">${esc(doc.meta.framing)}</div></div>
      ${WHO}
      <section class="dp-panel dp-day" aria-label="The replayed day">
        <div class="dp-card-h"><span class="dp-ct">The replayed day, ${h.num(S.cycles)} decisions</span>
          <span class="dp-legend"><span><i class="dp-sw-band"></i>P10 to P90 and median ${h_sTag(doc, 'cycle.fan')}</span><span><i class="dp-sw-sell"></i>Sell</span><span><i class="dp-sw-buy"></i>Buy</span><span><i class="dp-sw-soc"></i>Charge ${h_sTag(doc, 'cycle.plan')}</span></span></div>
        <div class="dp-lanes" data-lanes tabindex="-1">
          ${lane('Price the model expected for the interval being decided, $/MWh', g.price.ticks, priceSvg, g.HP, (v) => money(v))}
          ${lane('What the LP chose: setpoint in MW (up sells, down buys)', g.net.ticks, netSvg, g.HN, (v) => (v > 0 ? '+' : v < 0 ? '−' : '') + fmtN(Math.abs(v), 0))}
          ${lane('State of charge at the end of the interval, MWh', g.soc.ticks, socSvg, g.HS, (v) => fmtN(v, 0))}
          <div class="dp-cursor" data-cursor style="left:${((i + 0.5) / cy.n * 100).toFixed(3)}%"></div>
          <div class="dp-xlabels dp-lanes-x">${xl}</div>
        </div>
        <div class="dp-scrub"><input type="range" class="dp-range" min="0" max="${cy.n - 1}" step="1" value="${i}" aria-label="Decision, one every 15 minutes" data-range>
          <span class="dp-clock" data-clock>${clockHTML(doc, i)}</span></div>
      </section>`;
}
function clockHTML(doc, i) {
  const cy = doc.cycle, t = cy.t[i];
  return `Decision ${T(i + 1)} of ${T(cy.n)} · ${T(cptHM(t))} ${esc(cptAbbr(t))} <span class="dp-dim">(${T(utcHM(t))} UTC)</span>`;
}

export function cycleDynHTML(doc, st, h) {
  const cy = doc.cycle, S = cy.summary, i = st.i, num = h.num;
  const stg = stagesOf(doc, i, h);
  const sTag = (k) => h_sTag(doc, k);
  const lat = cy.lat[i];
  const kpis = [
    ['Cycle time, this one', num(fromSeries(doc, 'cycle.latency', lat), { digits: 3, unit: ' s' }), `Median ${num(S.latencyP50, { digits: 3, unit: ' s' })}, slowest ${num(S.latencyP99, { digits: 3, unit: ' s' })}`,
      `<div class="dp-meter" role="img" aria-label="Slowest cycle against the target"><i style="width:${Math.max(0.6, S.latencyP99.v / S.latencyTarget.v * 100).toFixed(2)}%"></i></div><span class="dp-note">Target ${num(S.latencyTarget, { digits: 0, unit: ' s' })}: met about ${num(S.headroom, { digits: 0 })} times over.</span>`],
    ['Guard, over the day', `${num(S.accepted)} of ${num(S.cycles)}`, 'accepted, none rejected', `<span class="dp-note">Its rejections are proven by tests (next view), not by this one quiet day.</span>`],
    ['Paper against backtest', num(S.paper, MONEY2), `Backtest ${num(S.backtest, MONEY2)}, slippage ${num(S.slippage, PCT1)} (limit ${num(S.slippageLimit, PCT0)})`,
      `<span class="dp-note">A replay shares code and inputs with the backtest, so zero slippage shows consistency, not market performance.</span>`],
    ['Audit log', `${num(S.auditRecords)}`, 'records, every hash recomputed', `<span class="dp-note">This cycle is records ${T(cy.seq[i])} to ${T(cy.seq[i] + 3)} of the chain.</span>`],
  ];
  const reads = cycleReads(doc, i, num);
  return `<ol class="dp-pipe" aria-label="The seven steps of this cycle">${stg.map((s) => `<li class="dp-stage"><div class="dp-sh"><span class="dp-sn" aria-hidden="true"></span><span class="dp-stt">${esc(s.title)}</span></div>${s.role}${s.html}</li>`).join('')}</ol>
    <div class="dp-two dp-two-s">
      <section class="dp-panel dp-card" aria-label="Forecast fan">
        <div class="dp-card-h"><span class="dp-ct">What the model expected for the next day</span>
          <span class="dp-legend"><span><i class="dp-sw-band"></i>Eight prices in ten ${sTag('cycle.fan')}</span><span><i class="dp-sw-p50"></i>Median</span><span><i class="dp-sw-prof"></i>Seven-day profile median</span></span></div>
        ${fanSVG(doc, i)}
        <div class="dp-note">Forecast issued at ${T(cptHM(cy.t[i]))} ${esc(cptAbbr(cy.t[i]))}. The dotted line is the plain seven-day profile the model is anchored on: it is what the forecast must beat.</div>
      </section>
      ${battSVG(doc, i, num)}
    </div>
    <section class="dp-kpis" aria-label="Cycle read-out">${kpis.map(([k, v, s, x]) => `<div class="dp-panel dp-kpi"><span class="dp-sub">${esc(k)}</span><span class="dp-kpi-v">${v}</span><span class="dp-note">${s}</span>${x}</div>`).join('')}</section>
    <section class="dp-panel dp-card" aria-label="Computed sentences"><span class="dp-ct">Read this cycle</span><span class="dp-sub">Each of the ${num(derived(reads.length, 'sentences below'))} sentences is computed from the numbers above.</span>
      <ol class="dp-reads">${reads.map((r, n) => `<li><span class="dp-rn" aria-hidden="true"></span><span><b>${esc(r.lead)}</b> ${r.html}</span></li>`).join('')}</ol></section>
    <section class="dp-panel dp-card" aria-label="Audit records of this cycle"><span class="dp-ct">The four audit records of this cycle</span>
      <div class="dp-table" role="table" aria-label="Audit records of this cycle">
        <div class="dp-tr dp-th dp-audit" role="row"><span role="columnheader">Record</span><span role="columnheader">Record no.</span><span role="columnheader">Hash ${sTag('cycle.hash')}</span><span role="columnheader">Links back to</span></div>
        ${['signal', 'guard', 'setpoint', 'cycle'].map((k, j) => {
    const names = { signal: 'signal: what the LP proposed', guard: 'guard_decision: the verdict', setpoint: 'setpoint: executed on paper', cycle: 'cycle: status and latency' };
    const prev = j === 0 ? cy.hash.prev[i] : cy.hash[['signal', 'guard', 'setpoint', 'cycle'][j - 1]][i];
    return `<div class="dp-tr dp-audit" role="row"><span role="cell">${esc(names[k])}</span><span role="cell">${T(cy.seq[i] + j)}</span><span role="cell" class="num dp-mono">${esc(cy.hash[k][i].slice(0, 16))}…</span><span role="cell" class="num dp-mono">${esc(prev.slice(0, 16))}…</span></div>`;
  }).join('')}
      </div><div class="dp-note">Each record carries the SHA-256 of the one before it. Change one character anywhere and every later link breaks.</div></section>`;
}

/** The sentences of cycle i, each number tagged. Pure apart from `num` (tested). */
export function cycleReads(doc, i, num) {
  const cy = doc.cycle, S = cy.summary;
  const g = fanGeom(cy, i);
  const net = cy.net[i];
  const F = (k, j = 0) => fromSeries(doc, 'cycle.fan', cy.fan[k][i][j]);
  const aw = PRODUCTS.map((p) => [p, cy.award[p][i]]).filter(([, v]) => v > 0.005);
  const act = Math.abs(net) < 0.005 ? 'keep the battery where it was' : net > 0 ? `sell ${num(fromSeries(doc, 'cycle.plan', net), { digits: 1, unit: ' MW' })}` : `buy ${num(fromSeries(doc, 'cycle.plan', -net), { digits: 1, unit: ' MW' })}`;
  const t = cy.t[i];
  return [
    { lead: 'The model’s part.', html: `At ${T(cptHM(t))} ${esc(cptAbbr(t))} it expected ${num(F('p50'), MONEY0)}/MWh for the interval being decided (eight in ten prices between ${num(F('p10'), MONEY0)} and ${num(F('p90'), MONEY0)}) and a median peak of ${num(fromSeries(doc, 'cycle.fan', g.peak.v), MONEY0)} at ${T(cptHM(g.peak.t))}. Prices are all it produces.` },
    { lead: 'The LP’s part.', html: `Given those paths, the battery’s limits and its charge of ${num(fromSeries(doc, 'cycle.plan', cy.soc0[i]), { digits: 1, unit: ' MWh' })}, the linear program chose to ${act}${aw.length ? ` and to hold ${aw.map(([p, v]) => `${num(fromSeries(doc, 'cycle.plan', v), { digits: 1, unit: ' MW' })} of ${esc(p)}`).join(' and ')}` : ''}. It is deterministic code: the same inputs give the same plan.` },
    { lead: 'The guard’s part.', html: `A separate function, outside the model, checked that first interval against ${num(doc.safe.summary.reasonCodes)} kinds of refusal. Over the day it accepted ${num(S.accepted)} of ${num(S.cycles)} setpoints; whether it refuses what it should is shown by tests, in the next view.` },
    { lead: 'The speed.', html: `This cycle took ${num(fromSeries(doc, 'cycle.latency', cy.lat[i]), { digits: 3, unit: ' s' })}. Across the day the median was ${num(S.latencyP50, { digits: 3, unit: ' s' })} and the slowest ${num(S.latencyP99, { digits: 3, unit: ' s' })}, against a target of ${num(S.latencyTarget, { digits: 0, unit: ' s' })}.` },
    { lead: 'The day in money.', html: `The paper ledger earned ${num(S.paper, MONEY2)} on this day and the backtest of the same decisions ${num(S.backtest, MONEY2)}. A perfect-foresight operator would have earned ${num(S.dayPf, MONEY2)}, so the day captured ${num(S.dayCapture, PCT1)}, and ${num(S.dayAncillary, MONEY0)} of the ${num(S.dayGap, MONEY0)} shortfall is ancillary services the paper battery did not hold. One day is not a capture rate.` },
  ];
}

export function cycleHTML(doc, st, h) {
  return `${cycleTopHTML(doc, st, h)}<div class="dp-dyn" data-dyn>${cycleDynHTML(doc, st, h)}</div>`;
}

// ---------------------------------------------------------------------------------------------------------------- q=2 what it earned
export function earnedReads(doc, scopeId, num) {
  const E = doc.earned, S = E.summary;
  const sc = E.scopes.find((s) => s.id === scopeId) || E.scopes[0];
  const comp = Object.fromEntries(E.components.map((c) => [c.id, c]));
  const tb = topByDay(E);
  const diff = derived(Math.round(sc.shadow.v) - Math.round(sc.actual.v), 'shadow minus battery in this scope, each shown to the dollar');
  const ahead = diff.v >= 0;
  const posEnergy = E.scopes.find((s) => s.id === 'energy_only');
  const wearLo = E.wear[0], wearHi = E.wear[E.wear.length - 1], wearMid = E.wear.find((w) => w.wear === S.wear.v);
  const wcap = (w) => ({ v: w.capture, label: 'DERIVED', cite: doc.series['earned.wear'].text });
  const wearAt = (w) => ({ v: w.wear, label: 'ASSUMPTION', cite: doc.series['earned.wear'].text });
  return [
    { lead: 'The headline.', html: `Valued at proxy prices, the battery’s dispatch and day-ahead positions over ${num(S.days)} days came to ${num(S.actual, MONEY0)}, which is ${num(S.captureActual, PCT1)} of the ${num(S.pf, MONEY0)} a perfect-foresight operator would have earned with the same battery, net of ${num(S.wear, { money: true, digits: 0, unit: '/MWh' })} of wear. That ceiling is a distance, not money anyone could have collected.` },
    { lead: 'Where the gap is.', html: `Of the ${num(S.gap, MONEY0)} gap, ${num(comp.as.share, PCT1)} is ancillary awards (${num(comp.as.usd, MONEY0)}), ${num(comp.forecast.share, PCT1)} forecast and decision, ${num(comp.soc.share, PCT1)} the charge it carried into each day, ${num(comp.availability.share, PCT1)} its own derates. Unexplained is ${num(comp.unexplained.share, PCT1)}, but that is zero by construction: on public data the actual is revalued with the same accounting.` },
    { lead: 'Concentration.', html: `The ${num(S.top20n)} largest intervals carry ${num(S.top20Share, PCT1)} of the gap (${num(S.top20, MONEY0)}) and the largest ${num(S.top100n)} carry ${num(S.top100Share, PCT1)}. They cluster: ${tb.slice(0, 3).map((d) => `${num(derived(d.n, 'count of the largest intervals on this Central day'))} on ${T(d.day)}`).join(', ')}. In ${num(S.negIntervals)} of ${num(S.intervals)} intervals the real battery out-earned the perfect plan, which is possible because that plan is one optimum over the whole period, not interval by interval.` },
    { lead: ahead ? 'The shadow is ahead.' : 'The shadow is behind.', html: ahead
      ? `In this scope the shadow optimizer (a seasonal-naive forecast, re-planned hourly, public information only) earns ${num(sc.shadow, MONEY0)} against the battery’s ${num(sc.actual, MONEY0)}, ${num(derived(Math.abs(diff.v), 'shadow minus battery'), MONEY0)} more: ${num(sc.recovers, PCT0)} of the gap. In the headline scope that lead is an ancillary lead: it sells regulation up and down at the clearing price, every offered megawatt awarded, no deployment energy.`
      : `In this scope the shadow optimizer earns ${num(sc.shadow, MONEY0)} against the battery’s ${num(sc.actual, MONEY0)}, ${num(derived(Math.abs(diff.v), 'battery minus shadow'), MONEY0)} less. The real operator is ahead once the shadow may not sell the ancillary products.` },
    { lead: 'Energy alone.', html: `On energy alone the real operator out-earned the shadow, ${num(S.energyActual, MONEY0)} against ${num(S.energyShadow, MONEY0)}. Of the operator’s energy revenue, ${num(posEnergy.position, MONEY0)} is a day-ahead position that the real-time-only shadow does not take. So “our optimizer would have recovered ${num(S.recovers, PCT0)} of the gap” is a statement about ancillary capacity under a price-taking assumption, not a promise of money.` },
    { lead: 'The wear assumption.', html: `The same battery scores ${num(wcap(wearLo), PCT1)} with no wear charge, ${num(wcap(wearMid), PCT1)} at ${num(wearAt(wearMid), { money: true, digits: 0 })} per MWh and ${num(wcap(wearHi), PCT1)} at ${num(wearAt(wearHi), { money: true, digits: 0 })}: its real-time margin is thin, so a charge per MWh moves the ratio a lot.` },
    { lead: 'The prices are a proxy.', html: `Hub HB_SOUTH stands in for the battery’s own node. Measured on a ${num(S.basisN)}-interval window the node-minus-hub gap has a root-mean-square of ${num(S.basisRmse, { money: true, digits: 2, unit: '/MWh' })} (bias ${num(S.basisBias, { money: true, digits: 2 })}). In dollars, that alone could move the battery’s revenue by ${num(S.proxyStress, PCT1)} of the gap, above the ${num(doc.ready.gateNums.g2Target, PCT0)} Gate 2 asks for.` },
  ];
}

export function earnedHTML(doc, st, h) {
  const E = doc.earned, S = E.summary, num = h.num, sTag = h.sTag;
  const sc = E.scopes.find((s) => s.id === st.scope) || E.scopes[0];
  const g = cumGeom(E);
  const dayI = st.day, D = E.days;
  const dayGapParts = [['Forecast and decision', D.forecast[dayI]], ['Ancillary awards', D.as[dayI]], ['Charge carried', D.soc[dayI]], ['Derates', D.avail[dayI]]];
  const dom = dayGapParts.slice().sort((a, b) => Math.abs(b[1]) - Math.abs(a[1]))[0];
  const wf = waterfallRows(E);
  const sb = scopeBars(E);
  const reads = earnedReads(doc, st.scope, num);
  const diff = derived(Math.round(sc.shadow.v) - Math.round(sc.actual.v), 'shadow minus battery in this scope, each shown to the dollar');
  const kp = [
    ['The battery earned', num(sc.actual, MONEY0), `${num(sc.captureActual, PCT1)} of perfect foresight`],
    ['Our shadow optimizer', num(sc.shadow, MONEY0), `${num(sc.captureShadow, PCT1)} of perfect foresight`],
    ['Perfect foresight', num(sc.pf, MONEY0), 'the ceiling nobody reaches'],
    ['The gap', num(sc.gap, MONEY0), `${num(sc.as, MONEY0)} of it ancillary awards`],
    ['Shadow minus battery', `${diff.v >= 0 ? '+' : ''}${num(diff, MONEY0).replace('$-', '−$')}`, diff.v >= 0 ? `recovers ${num(sc.recovers, PCT0)} of the gap` : 'the shadow is behind the real operator'],
    ['Unexplained', num(S.unexplained, PCT1), 'zero by construction; see Gate 2'],
  ];
  const nTop = st.topAll ? E.top.length : 8;
  const dayTicks = g.xt.map((x) => `<span class="dp-xl" style="left:${x.left.toFixed(2)}%"><span class="num">${esc(shortDate(x.date))}</span></span>`).join('');
  return `<div class="dp-head"><div><div class="st-eyebrow">WHAT IT EARNED · ${esc(E.asset)} · ${num(S.powerMw, { digits: 0, unit: ' MW' })} / ${num(S.energyMwh, { digits: 0, unit: ' MWh' })} · ${T(shortDate(E.firstDay))} TO ${T(shortDate(E.lastDay))} 2026</div>
        <div class="st-title dp-h1">What a real battery earned, against the most it could have</div>
        <div class="dp-headsub">The public case study: ERCOT’s own sixty-day disclosure for one battery, valued interval by interval at proxy prices and compared with a perfect-foresight operator and with our shadow optimizer. It is not settled revenue.</div></div>
        <div class="dp-headr">Hub prices stand in for the node. ${sTag('earned.days')}</div></div>

      <section class="dp-honest" aria-label="Read this first">
        <div class="dp-honest-t"><span class="st-eyebrow">READ THIS FIRST</span>
          <p><b>The shadow’s lead is an ancillary lead.</b> Its ${num(S.captureShadow, PCT1)} comes from selling ancillary services under price-taker assumptions: every offered megawatt awarded, no deployment energy. <b>On energy alone the real operator out-earned the shadow, ${num(S.energyActual, MONEY0)} against ${num(S.energyShadow, MONEY0)}.</b></p>
          <p>The prices are a hub proxy for the battery’s own node. The node-minus-hub difference has a root mean square of ${num(S.basisRmse, { money: true, digits: 2, unit: '/MWh' })}, which alone could move up to ${num(S.proxyStress, PCT1)} of the gap in dollars.</p></div>
        <div class="dp-scope" role="group" aria-label="Actual, shadow and perfect foresight by scope">
          ${sb.map((b) => `<button type="button" class="dp-scope-g${b.id === st.scope ? ' on' : ''}" data-scope="${b.id}" aria-pressed="${b.id === st.scope}">
            <span class="dp-scope-bars"><i class="pf" style="height:${b.pf.toFixed(1)}%"></i><i class="act" style="height:${b.actual.toFixed(1)}%"></i><i class="sh" style="height:${b.shadow.toFixed(1)}%"></i></span>
            <span class="dp-scope-n">${esc(b.name)}</span><span class="dp-scope-v">Battery ${T(money(b.a / 1000) + 'k')} · shadow ${T(money(b.s / 1000) + 'k')}</span></button>`).join('')}
          <span class="dp-legend dp-scope-l"><span><i class="dp-sw-act"></i>Battery</span><span><i class="dp-sw-sh"></i>Shadow</span><span><i class="dp-sw-pf"></i>Perfect foresight</span></span></div></section>

      <div class="dp-two dp-two-b">
        <section class="dp-panel dp-card" aria-label="Cumulative revenue">
          <div class="dp-card-h"><span class="dp-ct">Cumulative value over the ${num(S.days)} days</span>
            <span class="dp-legend"><span><i class="dp-sw-act"></i>Battery ${sTag('earned.days')}</span><span><i class="dp-sw-sh"></i>Shadow</span><span><i class="dp-sw-pf"></i>Perfect foresight</span></span></div>
          <div class="dp-plotwrap dp-cumwrap">
            ${g.ticks.map((t) => `<span class="dp-ytick num" style="top:${t.top.toFixed(2)}%">${money(t.v / 1000)}k</span>`).join('')}
            <div class="dp-plot" role="img" aria-label="Cumulative value of the battery, the shadow optimizer and perfect foresight">
              <svg viewBox="0 0 ${g.W} ${g.H}" preserveAspectRatio="none" aria-hidden="true">
                <path d="${g.grid}" class="dp-grid"></path>
                <rect x="${(dayI / g.n * g.W).toFixed(1)}" y="0" width="${(g.W / g.n).toFixed(1)}" height="${g.H}" class="dp-sel"></rect>
                <path d="${g.paths.pf}" class="dp-l-pf"></path><path d="${g.paths.shadow}" class="dp-l-sh"></path><path d="${g.paths.actual}" class="dp-l-act"></path></svg>
              <span class="dp-end" style="top:${g.ends.pf.toFixed(1)}%">Perfect foresight ${num(S.pf, MONEY0)}</span>
              <span class="dp-end sh" style="top:${g.ends.shadow.toFixed(1)}%">Shadow ${num(S.shadow, MONEY0)} · ${num(S.captureShadow, PCT1)}</span>
              <span class="dp-end act" style="top:${g.ends.actual.toFixed(1)}%">Battery ${num(S.actual, MONEY0)} · ${num(S.captureActual, PCT1)}</span>
              <div class="dp-cols">${D.date.map((d, k) => `<button type="button" class="dp-col${k === dayI ? ' on' : ''}" data-day="${k}" aria-label="Show ${esc(shortDate(d))}" aria-pressed="${k === dayI}"></button>`).join('')}</div>
            </div>
          </div>
          <div class="dp-xlabels dp-cumx">${dayTicks}</div>
          <div class="dp-readout"><div class="dp-ro-h"><b>${T(shortDate(D.date[dayI]))}</b><span class="dp-sub">Pick a day (click, tab or arrow keys).</span></div>
            <div class="dp-ro"><span class="dp-sub">Battery</span><span class="dp-ro-v num">${money(D.actual[dayI])}</span></div>
            <div class="dp-ro"><span class="dp-sub">Shadow</span><span class="dp-ro-v num">${money(D.shadow[dayI])}</span></div>
            <div class="dp-ro"><span class="dp-sub">Perfect foresight</span><span class="dp-ro-v num">${money(D.pf[dayI])}</span></div>
            <div class="dp-ro"><span class="dp-sub">Gap that day</span><span class="dp-ro-v num">${money(D.gap[dayI])}</span></div>
            <div class="dp-ro"><span class="dp-sub">Mostly</span><span class="dp-ro-v">${esc(dom[0])} <span class="num">${money(dom[1])}</span></span></div></div>
          <div class="dp-note">${sTag('earned.days')} Valued at hub proxy prices, net of the wear charge.</div>
        </section>

        <div class="dp-stack">
        <section class="dp-panel dp-card" aria-label="Where the gap comes from">
          <div class="dp-card-h"><span class="dp-ct">Where the gap comes from</span><span class="dp-legend">${h.tag('DERIVED', E.summary.gap.cite)}</span></div>
          <span class="dp-sub">From perfect foresight down to what the battery earned, one real restriction at a time. The order matters a little: the largest shift between orders is ${num(S.orderSpread, MONEY0)} (${num(S.orderSpreadShare, PCT1)} of the gap).</span>
          <div class="dp-wf" role="table" aria-label="The gap, step by step">
            ${wf.map((r) => `<div class="dp-wf-r${r.total ? ' tot' : ''} ${r.id}" role="row"><span class="dp-wf-n" role="rowheader">${esc(r.name)}</span>
              <span class="dp-wf-track"><i style="left:${r.left.toFixed(2)}%;width:${Math.max(r.width, 0.4).toFixed(2)}%"></i></span>
              <span class="dp-wf-v" role="cell">${r.total ? num(r.id === 'pf' ? S.pf : S.actual, MONEY0) : '−' + num(r.comp, MONEY0)}</span></div>`).join('')}
          </div>
          <div class="dp-note">Ancillary awards are ${num(E.components[0].share, PCT1)} of the gap, and they are the part that rests on the benchmark’s price-taker assumptions. The forecast and decision term includes the battery’s ${num(S.position, MONEY0)} of day-ahead positions.</div>
        </section>
        <section class="dp-panel dp-card" aria-label="The wear assumption">
          <span class="dp-ct">The wear assumption</span>
          <span class="dp-sub">Capture is net of a charge per MWh discharged. The case study re-runs everything at ${num(derived(E.wear.length, 'rows of the wear table'))} charges.</span>
          <div class="dp-table" role="table" aria-label="Wear sensitivity">
            <div class="dp-tr dp-th dp-wear" role="row"><span role="columnheader">Wear, $/MWh</span><span role="columnheader">Perfect foresight</span><span role="columnheader">Battery</span><span role="columnheader">Capture</span><span role="columnheader">Gap</span></div>
            ${E.wear.map((w) => `<div class="dp-tr dp-wear${w.wear === S.wear.v ? ' on' : ''}" role="row"><span role="cell" class="num">${fmtN(w.wear, 0)}</span><span role="cell" class="num">${money(w.pf)}</span><span role="cell" class="num">${money(w.actual)}</span><span role="cell" class="num">${fmtN(w.capture, 1)}%</span><span role="cell" class="num">${money(w.gap)}</span></div>`).join('')}
          </div><div class="dp-note">${sTag('earned.wear')} The headline uses the arena’s ${num(S.wear, { money: true, digits: 0, unit: '/MWh' })}.</div>
        </section>
        </div>
      </div>

      <section class="dp-kpis" aria-label="Read-out for this scope">${kp.map(([k, v, s]) => `<div class="dp-panel dp-kpi"><span class="dp-sub">${esc(k)}</span><span class="dp-kpi-v">${v}</span><span class="dp-note">${s}</span></div>`).join('')}</section>

      <section class="dp-panel dp-card" aria-label="Computed sentences"><span class="dp-ct">Read the case study</span><span class="dp-sub">${num(derived(reads.length, 'sentences below'))} sentences, each computed from the case-study files. Scope: ${esc(sc.name.toLowerCase())}.</span>
        <ol class="dp-reads">${reads.map((r) => `<li><span class="dp-rn" aria-hidden="true"></span><span><b>${esc(r.lead)}</b> ${r.html}</span></li>`).join('')}</ol></section>

      <section class="dp-panel dp-card" aria-label="The largest missed intervals">
        <div class="dp-card-h"><span class="dp-ct">The ${num(derived(E.top.length, 'rows in the table'))} largest missed intervals</span><span class="dp-legend">${sTag('earned.top')}</span></div>
        <span class="dp-sub">Each row is one fifteen-minute interval (Central time). The gap is perfect foresight minus the battery; “mostly” is its largest component.</span>
        <div class="dp-table" role="table" aria-label="Largest missed intervals">
          <div class="dp-tr dp-th dp-top" role="row"><span role="columnheader">When</span><span role="columnheader">Price, $/MWh</span><span role="columnheader">Best plan, MW</span><span role="columnheader">Battery, MW</span>
            <span role="columnheader">Awards held, MW</span><span role="columnheader">Awards in best plan, MW</span><span role="columnheader">Gap</span><span role="columnheader">Mostly</span></div>
          ${E.top.slice(0, nTop).map((r) => `<div class="dp-tr dp-top" role="row"><span role="cell" class="num">${esc(cptDay(r.utc))} ${esc(cptHM(r.utc))}</span><span role="cell" class="num">${fmtN(r.price, 0)}</span>
            <span role="cell" class="num">${fmtN(r.netPf, 0)}</span><span role="cell" class="num">${fmtN(r.netActual, 0)}</span><span role="cell" class="num">${fmtN(r.awardActual, 0)}</span><span role="cell" class="num">${fmtN(r.awardPf, 0)}</span>
            <span role="cell" class="num">${money(r.gap)}</span><span role="cell">${r.dominant === 'as' ? 'ancillary awards' : r.dominant === 'forecast' ? 'forecast and decision' : esc(r.dominant)}</span></div>`).join('')}
        </div>
        <button type="button" class="st-btn-ghost dp-more" data-top="${st.topAll ? 'less' : 'more'}" aria-expanded="${st.topAll ? 'true' : 'false'}">${st.topAll ? 'Show fewer' : 'Show all of them'}</button>
      </section>

      <section class="dp-panel dp-card" aria-label="Why this battery">
          <span class="dp-ct">Why this battery</span>
          <span class="dp-sub">The rules were fixed before the choice. Of ${num(S.nAll)} storage resources in the disclosure, ${num(S.nSize)} are the right size, ${num(S.nBasis)} have a node whose prices track a hub closely, and ${num(S.nPass)} pass every rule. The lowest basis error is the pilot.</span>
          <div class="dp-table" role="table" aria-label="Pilot candidates">
            <div class="dp-tr dp-th dp-pil" role="row"><span role="columnheader">Battery</span><span role="columnheader">MW</span><span role="columnheader">MWh</span><span role="columnheader">Node-hub RMSE, $/MWh</span><span role="columnheader">Bias</span><span role="columnheader">Cycles a day</span></div>
            ${E.pilots.map((p) => `<div class="dp-tr dp-pil${p.resource === E.pilotId ? ' on' : ''}" role="row"><span role="cell">${esc(p.resource)}${p.resource === E.pilotId ? ' <span class="dp-pick">pilot</span>' : ''}</span><span role="cell" class="num">${fmtN(p.mw, 0)}</span><span role="cell" class="num">${fmtN(p.mwh, 0)}</span><span role="cell" class="num">${fmtN(p.rmse, 2)}</span><span role="cell" class="num">${fmtN(p.bias, 2)}</span><span role="cell" class="num">${fmtN(p.cycles, 2)}</span></div>`).join('')}
          </div><div class="dp-note">${sTag('earned.top')} Counts are read from the selection table; the node-hub figures are REAL, from the seven-day nodal window.</div>
      </section>

      <section class="dp-panel dp-card" aria-label="The price proxy">
        <div class="dp-card-h"><span class="dp-ct">How wrong could the price proxy make this?</span><span class="dp-legend">${sTag('earned.proxy')}</span></div>
        <span class="dp-sub">Node prices before the October window need an ERCOT API key we do not have, so a hub price stands in. The only node prices we have are ${num(derived(E.proxy.length, 'rows of the proxy table'))} October days; each day’s node-minus-hub basis is applied to every study day and the dispatches are revalued. The worst of the seven moves the battery’s revenue by ${num(S.proxyStress, PCT1)} of the gap.</span>
        <div class="dp-table" role="table" aria-label="Proxy price sensitivity">
          <div class="dp-tr dp-th dp-prox" role="row"><span role="columnheader">Basis day</span><span role="columnheader">Mean basis, $/MWh</span><span role="columnheader">Battery moves</span><span role="columnheader">Perfect foresight moves</span><span role="columnheader">Shadow moves</span><span role="columnheader">Gap moves</span></div>
          ${E.proxy.map((p) => `<div class="dp-tr dp-prox" role="row"><span role="cell" class="num">${esc(shortDate(p.day))}</span><span role="cell" class="num">${p.basis > 0 ? '+' : ''}${fmtN(p.basis, 2)}</span><span role="cell" class="num">${money(p.actual)}</span><span role="cell" class="num">${money(p.pf)}</span><span role="cell" class="num">${money(p.shadow)}</span><span role="cell" class="num">${money(p.gap)}</span></div>`).join('')}
        </div>
        <div class="dp-note">A second check on the study’s own hours, with day-ahead prices, moves the battery by ${num(S.damStress, PCT1)} of the gap (node-minus-hub root mean square ${num(S.damRmse, { money: true, digits: 2, unit: '/MWh' })}). Day-ahead is not real-time, so it bounds the proxy rather than measuring it.</div>
      </section>`;
}

// ---------------------------------------------------------------------------------------------------------------- q=3 safe by design
export function safeReads(doc, num) {
  const A = doc.safe, S = A.summary, M = A.monitor;
  const fb = A.falseBreach;
  const catches = A.catches.bias10;
  const lh = lowHigh(catches);
  const re = A.realEvent;
  return [
    { lead: 'The guard.', html: `It is one pure function with ${num(S.reasonCodes)} reason codes. It rejects and never edits: a limit exceeded by no more than ${num(rescale(S.eps, sci(S.eps.v, 0)))} MW of solver noise counts as met, anything more is refused. A generated-case test recomputes every limit from first principles; ${num(S.examples)} examples across ${num(derived(A.guard.props.length, 'properties listed in the evidence panel'))} properties, in ${num(S.tests)} tests, found no setpoint that got past a limit.` },
    { lead: 'The kill switch.', html: `A halt flag lives in the platform’s own Redis, written to disk, so a restart does not clear it. If the flag cannot be read, dispatch counts as halted. Resume refuses while Redis is down or while the audit chain (${num(S.chainRecords)} records in the replay) does not verify.` },
    { lead: 'Failing closed.', html: `Missing or stale features, no model, a forecast that raises, an LP that is not optimal, a guard rejection, an audit log that cannot be written: each produces no setpoint and an audit record that names the reason.${re ? ` It has happened for real: on ${T(cptDay(re.t))} the lake behind the features was missing recent prices, the cycle ended ${code(re.reasons[0])} in ${num({ v: re.latency, label: 'SIM', cite: 'real runtime audit log' }, { digits: 3, unit: ' s' })}, nothing dispatched, and two minutes later it dispatched normally.` : ''}` },
    { lead: 'What halts, and what only alerts.', html: `Feature drift is alert-only: ${num(M.psiLow)} to ${num(M.psiHigh, { digits: 0, unit: '%' })} of the model’s features trip a drift score of ${num(M.psiAlert, { digits: 1 })} even on ordinary weeks, so as a halt it would stop dispatch within minutes. Output calibration is checked once a day and halts after ${num(M.haltAfter)} bad days in a row; on ${T(doc.safe.falseBreach.length)} hubs of out-of-sample history it falsely breaches ${num(M.platformLow, PCT1)} to ${num(M.platformHigh, PCT1)} of the time, against ${num(M.firstRuleLow, PCT0)} to ${num(M.firstRuleHigh, PCT0)} for the first rule tried, and still catches a ${num(M.biasUsd, { money: true, digits: 0, unit: '/MWh' })} bias in ${num(lh.lo, PCT0)} to ${num(lh.hi, PCT0)} of windows. Stale data halts only for the reports the model reads.` },
    { lead: 'The audit log.', html: `Every signal, verdict, setpoint and cycle is one line holding the hash of the line before. Editing one number in one approved setpoint breaks verification at that line (line ${num(S.tamperLine)}) and every link after it. It is tamper-evident, not tamper-proof: someone who can rewrite the whole file can rebuild a chain, which is why the head hash is also anchored outside the file.` },
  ];
}

export function safeHTML(doc, st, h) {
  const A = doc.safe, S = A.summary, M = A.monitor, num = h.num, sTag = h.sTag;
  const reads = safeReads(doc, num);
  const lim = doc.cycle.summary.featureAgeLimit;
  // the gauntlet: the stages of the cycle in the order they run, each with where it stops a setpoint
  const order = ['Redis down or the halt flag set', 'Features missing', 'Features too old', 'No model', 'Forecast failed', 'LP not optimal', 'Guard rejects', 'Audit or ledger refuses'];
  const gate = (name) => A.failPaths.find((f) => f.name === name);
  const gauntlet = order.map((n) => gate(n)).filter(Boolean);
  const maxEx = Math.max(...A.guard.props.map((p) => p.examples.v));
  const fbMax = 50;
  const re = A.realEvent;
  const reps = A.reports;
  const chain = A.chain;
  return `<div class="dp-head"><div><div class="st-eyebrow">SAFE BY DESIGN</div>
        <div class="st-title dp-h1">What stops a bad setpoint, and what stops the machine</div>
        <div class="dp-headsub">Nothing here relies on the model being right. A setpoint has to get through ${num(derived(gauntlet.length, 'gates drawn below'))} gates, written in ordinary code, and any of them can say no. When anything is in doubt the answer is no setpoint, plus a record that says why.</div></div>
        <div class="dp-headr">${role('code', 'code')} every gate on this page is deterministic code</div></div>

      <section class="dp-panel dp-card dp-gauntlet" aria-label="The gauntlet a setpoint runs">
        <div class="dp-card-h"><span class="dp-ct">The gauntlet</span><span class="dp-legend"><span><i class="dp-k-ok"></i>Passes on</span><span><i class="dp-k-stop"></i>Stops here: no setpoint, an audit record</span></span></div>
        <ol class="dp-gaunt">${gauntlet.map((f) => `<li class="dp-gate"><div class="dp-gate-pass"><i></i><span class="dp-gate-n" aria-hidden="true"></span></div>
          <div class="dp-gate-b"><b>${esc(f.name)}</b><span class="dp-sub">${esc(f.what)}</span></div>
          <div class="dp-gate-stop"><span class="dp-status ${f.status}">${esc(f.status)}</span><span class="dp-codes">${f.codes.map(code).join(' ')}</span></div></li>`).join('')}</ol>
        <div class="dp-note">The age limit for features is ${num(lim, { digits: 0, unit: ' s' })}. A guard rejection can carry any of its ${num(S.reasonCodes)} reason codes; three are shown. A Redis outage is the first gate: an unreadable flag counts as halted.</div>
      </section>

      <section class="dp-kpis" aria-label="Evidence in numbers">
        <div class="dp-panel dp-kpi"><span class="dp-sub">Reason codes in the guard</span><span class="dp-kpi-v">${num(S.reasonCodes)}</span><span class="dp-note">each one tested</span></div>
        <div class="dp-panel dp-kpi"><span class="dp-sub">Property-test examples</span><span class="dp-kpi-v">${num(S.examples)}</span><span class="dp-note">across ${num(derived(A.guard.props.length, 'properties listed below'))} properties, ${num(S.tests)} guard tests, none got past a limit</span></div>
        <div class="dp-panel dp-kpi"><span class="dp-sub">Replayed day: guard verdicts</span><span class="dp-kpi-v">${num(S.replayAccepted)} <span class="dp-dim">/</span> ${num(S.replayRejected)}</span><span class="dp-note">accepted / rejected. A quiet day proves little; the tests do the proving.</span></div>
        <div class="dp-panel dp-kpi"><span class="dp-sub">Audit chains verified</span><span class="dp-kpi-v">${num(S.chainRecords)}</span><span class="dp-note">records in the replay log, and ${num(S.realRecords)} in the real one, every hash recomputed</span></div>
      </section>

      <div class="dp-two">
        <section class="dp-panel dp-card" aria-label="The guard">
          <div class="dp-card-h"><span class="dp-ct">The guard: what it checks</span><span class="dp-legend">${h.tag('DERIVED', 'dispatch/dispatch/guard/guard.py REASON_CODES, grouped by the exporter')}</span></div>
          <span class="dp-sub">It rejects; it never clips. It runs on every cycle, outside the model, with no clock and no randomness: the caller supplies the time, so a live cycle, a replay and a test reach the same verdict.</span>
          <div class="dp-groups">${A.guard.groups.map((g) => `<div class="dp-group"><b>${esc(g.name)}</b><span class="dp-codes">${g.codes.map(code).join(' ')}</span><span class="dp-sub">${esc(g.what)}</span></div>`).join('')}</div>
          <div class="dp-note">A state of charge older than ${num(S.socAge, { digits: 0, unit: ' s' })} is stale. Exposure is capped at ${num(S.exposure, MONEY0)} for the pilot: that definition is an open business question (next view).</div>
        </section>
        <section class="dp-panel dp-card" aria-label="The evidence for the guard">
          <div class="dp-card-h"><span class="dp-ct">The evidence: property tests</span><span class="dp-legend">${h.tag('DERIVED', 'dispatch/notes/d2.md section 3, tests/test_guard_props.py')}</span></div>
          <span class="dp-sub">An oracle written inside the test recomputes every invariant from first principles. The generator is checked to reach both outcomes (${num(S.genAccepted)} accepted, ${num(S.genRejected)} rejected, ${num(S.genCodes)} reason codes), so the soundness test is not vacuous.</span>
          <div class="dp-props">${A.guard.props.map((p) => `<div class="dp-prop"><span>${esc(p.name)}</span><span class="dp-track"><i style="width:${(p.examples.v / maxEx * 100).toFixed(1)}%"></i></span><span>${num(p.examples)}</span></div>`).join('')}</div>
          <div class="dp-note">Accepted setpoints satisfy every invariant; setpoints comfortably inside every limit are accepted; garbage never raises and is never accepted; tightening any limit never turns a rejection into an acceptance.</div>
        </section>
      </div>

      <section class="dp-panel dp-card" aria-label="Kill switch and resume">
        <div class="dp-card-h"><span class="dp-ct">The kill switch and the way back</span><span class="dp-legend">${role('code', 'code')}</span></div>
        <ol class="dp-flow">
          <li><b>An operator stops it</b><span class="dp-sub">${code('POST /kill')} with a reason. An automatic halt from monitoring uses the same flag.</span></li>
          <li><b>The flag is written</b><span class="dp-sub">${code(A.haltKey)} in the platform’s own Redis, persisted to disk. A restart does not clear it. If Redis is unreachable, dispatch counts as halted.</span></li>
          <li><b>Nothing runs</b><span class="dp-sub">The next cycle returns ${code('halted')}, executes nothing and writes an audit record. A kill that lands mid-cycle stops it before it executes. The guard rejects ${code('halted')} again as a second line.</span></li>
          <li><b>An operator resumes</b><span class="dp-sub">${code('POST /resume')} refuses while Redis is down and while the audit chain does not verify. It is itself audited.</span></li>
          <li><b>After a calibration halt, it waits</b><span class="dp-sub">A resume holds until the next daily check. If the model is still bad, the check halts again.</span></li>
        </ol>
      </section>

      <section class="dp-panel dp-card" aria-label="Monitoring policy">
        <div class="dp-card-h"><span class="dp-ct">The monitoring policy: what halts and what only alerts</span><span class="dp-legend">${h.tag('ASSUMPTION', 'dispatch/dispatch/config.py and monitoring/checks.py: named thresholds')}</span></div>
        <div class="dp-mon">
          <div class="dp-mon-col">
            <div class="dp-mon-c"><span class="dp-status alert">alert only</span><b>Feature drift</b>
              <span class="dp-sub">Between ${num(M.psiLow)} and ${num(M.psiHigh, { digits: 0, unit: '%' })} of the model’s features exceed a drift score of ${num(M.psiAlert, { digits: 1 })} even on in-distribution weeks, because every feature is a day-to-week rolling statistic against a year-long reference.</span>
              <div class="dp-range-bar" role="img" aria-label="Share of features flagged on ordinary weeks"><i style="left:${M.psiLow.v}%;width:${M.psiHigh.v - M.psiLow.v}%"></i></div>
              <span class="dp-note">Share of features flagged on ordinary weeks. As a halt it would stop dispatch within minutes, so it alerts and never halts.</span></div>
            <div class="dp-mon-c"><span class="dp-status halt">halts</span><b>Capture rate and slippage</b>
              <span class="dp-sub">${num(M.haltAfterOthers)} bad checks in a row halt dispatch: capture rate falling more than ${num(M.captureDecay, PCT0)} below its baseline, or paper revenue differing from the backtest by more than ${num(M.slippageTol, PCT0)}.</span></div></div>
          <div class="dp-mon-c"><span class="dp-status halt">halts</span><b>Daily calibration</b>
            <span class="dp-sub">Once a day, over the last ${num(M.windowDays)} days of settled forecasts: a hit rate off by more than ${num(M.hitTol, { digits: 2 })}, or a median error more than ${num(M.skill, PCT0)} worse than the plain seven-day profile. One bad day alerts and asks for a retrain; ${num(M.haltAfter)} in a row halt.</span>
            <div class="dp-fb" role="img" aria-label="False-breach rate by hub: the first rule tried against the chosen rule, with the five percent bar">
              ${A.falseBreach.map((f) => `<div class="dp-fb-r"><span>${esc(f.hub)}</span><span class="dp-fb-t"><i class="a" style="width:${(f.ruleA.v / fbMax * 100).toFixed(1)}%"></i><i class="d" style="width:${(f.platform.v / fbMax * 100).toFixed(2)}%"></i><i class="bar" style="left:${(M.falseBar.v / fbMax * 100).toFixed(1)}%"></i></span><span>${T(fmtN(f.ruleA.v, 0) + '%')} <span class="dp-dim">to</span> ${T(fmtN(f.platform.v, 1) + '%')}</span></div>`).join('')}
              <div class="dp-note"><span class="dp-fb-key a"></span>first rule tried <span class="dp-fb-key d"></span>chosen rule <span class="dp-fb-key bar"></span>the ${num(M.falseBar, PCT0)} bar. The share of in-distribution windows that falsely breach, over ${num(M.evals)} daily evaluations per hub ${h.tag('SIM', A.falseBreach[0].platform.cite)}</div></div>
            <span class="dp-sub">With every quantile ${num(M.biasUsd, { money: true, digits: 0, unit: '/MWh' })} off, ${num(lowHigh(A.catches.bias10).lo, PCT0)} to ${num(lowHigh(A.catches.bias10).hi, PCT0)} of windows are in breach, so it is not blind. It barely notices a day-old forecast: stale inputs are the feature-age gate’s job.</span></div>
          <div class="dp-mon-c"><span class="dp-status halt">halts, for what the model reads</span><b>Stale data</b>
            <span class="dp-sub">Each report has an allowance: its cadence, plus a lateness margin, plus ${num(M.freshness, { digits: 0, unit: ' s' })}. Only the reports the served model reads can halt dispatch; the rest alert.</span>
            <div class="dp-table dp-rep" role="table" aria-label="Report allowances">
              <div class="dp-tr dp-th dp-repr" role="row"><span role="columnheader">Report</span><span role="columnheader">Allowance ${h.tag('DERIVED', reps[0].allowance.cite)}</span><span role="columnheader">Longest gap seen ${h.tag('REAL', reps[0].longest.cite)}</span><span role="columnheader">On breach</span></div>
              ${reps.map((r) => `<div class="dp-tr dp-repr" role="row"><span role="cell"><b>${esc(r.id)}</b> <span class="dp-dim">${esc(r.name)}</span></span><span role="cell" class="num">${fmtN(r.allowance.v, 0)} s</span><span role="cell" class="num">${r.longest ? fmtN(r.longest.v, 0) + ' s' : '—'}</span><span role="cell"><span class="dp-status ${r.halts ? 'halt' : 'alert'}">${r.halts ? 'halts' : 'alert only'}</span></span></div>`).join('')}
            </div></div>
        </div>
      </section>

      <section class="dp-panel dp-card" aria-label="The audit log">
        <div class="dp-card-h"><span class="dp-ct">The audit log: a chain you can check</span><span class="dp-legend">${sTag('cycle.hash')}</span></div>
        <span class="dp-sub">One line per event, of ${num(S.events)} kinds. Each holds the SHA-256 of the line before, and is fsynced to disk before the thing it records happens: the guard’s verdict is on disk before the ledger is touched. Nothing is ever deleted; how long to keep it is undecided.</span>
        <div class="dp-chain" role="list" aria-label="Five consecutive audit records, one of them edited">
          ${chain.map((c, k) => `<div class="dp-link${c.i === A.tamper.edited ? ' bad' : c.i > A.tamper.edited ? ' broken' : ''}" role="listitem">
            <span class="dp-lt">${esc(c.type)} ${T(c.seq)}</span>
            <span class="dp-lh">hash <span class="num dp-mono">${esc(c.hash)}…</span></span><span class="dp-lh">links to <span class="num dp-mono">${esc(c.prev)}…</span></span>
            ${c.i === A.tamper.edited ? `<span class="dp-lx">edited: its content now hashes to <span class="num dp-mono">${esc(c.recomputed)}…</span></span>` : c.i > A.tamper.edited ? '<span class="dp-lx">chain broken from here</span>' : '<span class="dp-lx ok">verifies</span>'}</div>`).join('<span class="dp-arrow sm" aria-hidden="true">→</span>')}
        </div>
        <div class="dp-note">Here the setpoint in one approved guard decision was changed by a single number (${T(fmtN(A.tamper.from, 1))} MW to ${T(fmtN(A.tamper.to, 1))} MW) in a copy of the log. Verification fails at line ${num(S.tamperLine)}, the edited line itself. ${A.realChainOk ? `The real runtime log (${num(S.realRecords)} records) verifies.` : ''}</div>
        ${re ? `<div class="dp-real" role="note"><span class="dp-real-t">A real fail-closed event</span>
          <span>On ${T(cptDay(re.t))} at ${T(cptHM(re.t))} ${esc(cptAbbr(re.t))} the lake behind the features was missing the latest hours of prices, so a required feature came back empty. The cycle ended ${code(re.status)} with ${re.reasons.map(code).join(' ')} (${T(re.error)}), nothing was dispatched, and the audit log says why. After the data was backfilled it dispatched at ${T(cptHM(re.thenT))} on model version ${T(re.thenModel)} in ${num({ v: re.thenLatency, label: 'SIM', cite: 'real runtime audit log' }, { digits: 3, unit: ' s' })}.</span></div>` : ''}
      </section>

      <section class="dp-panel dp-card" aria-label="Computed sentences"><span class="dp-ct">Read the safety case</span><span class="dp-sub">${num(derived(reads.length, 'sentences below'))} sentences, each computed from the platform’s own files and tests.</span>
        <ol class="dp-reads">${reads.map((r) => `<li><span class="dp-rn" aria-hidden="true"></span><span><b>${esc(r.lead)}</b> ${r.html}</span></li>`).join('')}</ol></section>`;
}

// ---------------------------------------------------------------------------------------------------------------- q=4 ready for what
export function readyReads(doc, num) {
  const R = doc.ready, G = R.gateNums, reg = R.registry, F = R.forecast, D = R.dpcheck;
  const hubs = F.hubs;
  const covLo = Math.min(...hubs.map((x) => F.model[x].coverage)), covHi = Math.max(...hubs.map((x) => F.model[x].coverage));
  const fs = (k) => ({ v: k, label: 'SIM', cite: doc.series['ready.forecast'].text });
  const behind = behindHubs(F);
  return [
    { lead: 'Where it stands.', html: `Gate 1 (the data rebuilds exactly from raw files) passes. Gate 2 (attribution closes) is met by construction on public data and not proven: the real test needs a design partner’s settlement statement, and the price proxy alone could move ${num(G.g2Stress, PCT1)} of the gap against a target of ${num(G.g2Target, PCT0)}. Gate 3 (paper matches backtest in real time) has not passed.` },
    { lead: 'Why Gate 3 is not passed.', html: `It needs at least ${num(G.g3Days, { digits: 0, unit: ' days' })} of real-time paper operation against live ERCOT publications. The evidence so far is ${num(G.g3Replayed, { digits: 0, unit: ' replayed day' })} on the real stack, where paper equalled backtest to the cent. A replay shares code and inputs with the backtest, so that shows consistency, not market performance. Nothing is recorded for live, so live stays locked.` },
    { lead: 'The model.', html: `Version ${num(reg.headline.served)} serves the research and paper aliases; version ${T(reg.rollback)} is kept for a rollback and the live alias is empty. Promotion to paper needs ${num(reg.headline.checks)} checks to pass: every hub beating every baseline on pinball loss and median error, by at least ${num(reg.headline.minGain, PCT0)} against the best naive forecast, with coverage inside ${num(reg.headline.bandLow, { digits: 2 })} to ${num(reg.headline.bandHigh, { digits: 2 })}.` },
    { lead: 'The forecast is better; the dispatch value is not clearly better.', html: `On a walk-forward holdout of ${num(F.headline.rows)} forecasts per hub, pinball loss is ${num(F.headline.gainLow, PCT1)} to ${num(F.headline.gainHigh, PCT1)} below the strongest baseline, and the P10 to P90 band covers ${num(fs(covLo * 100), { digits: 1, unit: '%' })} to ${num(fs(covHi * 100), { digits: 1, unit: '%' })} of prices against a nominal ${num(F.headline.nominal, PCT0)}. But the revenue of a plan built on its median is level with a plain seven-day profile${behind.length ? ` (lower at ${behind.length === 4 ? 'every hub' : behind.map((x) => esc(x)).join(' and ')})` : ''}: one reference battery, no confidence interval.` },
    { lead: 'The optimizer is the optimizer.', html: `On ${num(D.headline.days)} historical zone-days the platform’s LP and the research arena’s dynamic programme differ by at most ${num(rescale(D.headline.maxAbs, sci(D.headline.maxAbs.v)), { money: true, unit: ' per MW-day' })}, against a tolerance of ${num(rescale(D.headline.tolerance, sci(D.headline.tolerance.v, 0)), { money: true })}. With a grid-side charge limit, as the guard enforces, the LP earns ${num(rescale(D.headline.gridBasis, Math.abs(D.headline.gridBasis.v)), PCT1)} less than the dynamic programme in total.` },
    { lead: 'What is open.', html: `${num(derived(R.questions.length, 'open questions listed from the PRD'))} questions in the PRD are business decisions this repository cannot make, among them the audit-log retention, the exposure-cap definition and who the first design partner is. Fleet dispatch waits for ERCOT’s Board vote on ADER Phase 4.` },
  ];
}

export function readyHTML(doc, st, h) {
  const R = doc.ready, G = R.gateNums, reg = R.registry, F = R.forecast, D = R.dpcheck, num = h.num, sTag = h.sTag;
  const S = doc.cycle.summary;
  const reads = readyReads(doc, num);
  const pb = pinballRows(F);
  const tone = { pass: 'PASS', partial: 'MET BY CONSTRUCTION, NOT PROVEN', open: 'NOT PASSED' };
  const gate = (g) => R.gates.find((x) => x.n === g);
  const g3days = Array.from({ length: G.g3Days.v }, (_, k) => k);
  const stress = G.g2Stress.v, target = G.g2Target.v, meterMax = Math.ceil(stress / 5) * 5 + 5;
  const bins = [{ label: 'equal', n: D.hist.zero }].concat(D.hist.decades.map((d) => ({ label: `below ${sci(10 ** (d.e + 1), 0)}`, n: d.n })));
  const binMax = Math.max(...bins.map((b) => b.n));
  const modelV = reg.versions.find((v) => v.ver === reg.served), rollV = reg.versions.find((v) => v.ver === reg.rollback);
  const baselines = reg.versions.filter((v) => v.kind === 'baseline');
  return `<div class="dp-head"><div><div class="st-eyebrow">READY FOR WHAT</div>
        <div class="st-title dp-h1">Ready for paper, not yet for money</div>
        <div class="dp-headsub">The platform’s three gates, as its own README states them, then the model registry, how good the forecasts are against plain baselines, and what only people can decide. Nothing on this page has been rounded up.</div></div>
        <div class="dp-headr">Gate statuses are copied from <code class="dp-code">dispatch/README.md</code></div></div>

      <section class="dp-gates" aria-label="Gates one to three">
        <article class="dp-panel dp-gatec ${gate(1).tone}"><div class="dp-gh"><span class="st-eyebrow">GATE 1 · ${esc(gate(1).name.replace(/^Phase \d, /, '').toUpperCase())}</span><span class="dp-gstat ${gate(1).tone}">${tone[gate(1).tone]}</span></div>
          <b class="dp-gq">Rebuild everything from the raw files and get the same bytes.</b>
          <div class="dp-gbig">${num(G.g1Files)} <span class="dp-dim">of</span> ${num(G.g1FilesOf)}</div><span class="dp-sub">files identical on ${num(G.g1Raw)} real raw files, in-process and in a fresh process. On the real lake ${num(G.g1Lake)} of ${num(G.g1LakeOf)} are identical. The leakage test (nothing in a feature is published after its decision) passes.</span></article>
        <article class="dp-panel dp-gatec ${gate(2).tone}"><div class="dp-gh"><span class="st-eyebrow">GATE 2 · ${esc(gate(2).name.replace(/^Phase \d, /, '').toUpperCase())}</span><span class="dp-gstat ${gate(2).tone}">${tone[gate(2).tone]}</span></div>
          <b class="dp-gq">Attribution closes: unexplained under ${num(G.g2Target, PCT0)} of the gap.</b>
          <div class="dp-gbig">${num(G.g2Unexplained, PCT1)} <span class="dp-dim">unexplained</span></div>
          <span class="dp-sub">That is zero because public data holds no settled revenue, so the actual is revalued with the same accounting. The honest test is closure against a design partner’s settlement statement. Until then, the price proxy alone could move this much of the gap:</span>
          <div class="dp-stress" role="img" aria-label="Proxy stress against the target"><i class="s" style="width:${(stress / meterMax * 100).toFixed(1)}%"></i><i class="t" style="left:${(target / meterMax * 100).toFixed(1)}%"></i></div>
          <span class="dp-gl2">${num(G.g2Stress, PCT1)} <span class="dp-dim">against a target of</span> ${num(G.g2Target, PCT0)}</span></article>
        <article class="dp-panel dp-gatec ${gate(3).tone}"><div class="dp-gh"><span class="st-eyebrow">GATE 3 · ${esc(gate(3).name.replace(/^Phase \d, /, '').toUpperCase())}</span><span class="dp-gstat ${gate(3).tone}">${tone[gate(3).tone]}</span></div>
          <b class="dp-gq">Paper matches the backtest, slippage under ${num(G.g3Slippage, PCT0)}, for at least ${num(G.g3Days, { digits: 0, unit: ' days' })} of real-time operation.</b>
          <div class="dp-days" role="img" aria-label="Days of real-time paper operation needed, and the one replayed day">${g3days.map((k) => `<span class="dp-day-s${k === 0 ? ' rep' : ''}"><span class="dp-day-l">${k === 0 ? 'replay' : 'real time'}</span></span>`).join('')}</div>
          <span class="dp-sub">One replayed day: paper ${num(S.paper, MONEY2)} equals backtest ${num(S.backtest, MONEY2)}, slippage ${num(S.slippage, PCT1)}. A replay shares code and inputs with the backtest, so that shows consistency, not market performance. No real-time day has run, so the live alias stays locked.</span></article>
      </section>

      <div class="dp-two dp-two-b dp-two-c">
        <section class="dp-panel dp-card" aria-label="The model registry">
          <div class="dp-card-h"><span class="dp-ct">The model registry</span><span class="dp-legend">${sTag('ready.versions')}</span></div>
          <span class="dp-sub">A model is promoted through three aliases, each with a gate of its own. The service loads the paper alias, and takes the model out of service if that alias moves.</span>
          <div class="dp-aliases">
            <div class="dp-alias on"><span class="dp-an">research</span><b>version ${T(reg.alias.research)}</b><span class="dp-sub">beats every baseline on every hub</span></div>
            <span class="dp-arrow" aria-hidden="true">→</span>
            <div class="dp-alias on"><span class="dp-an">paper</span><b>version ${T(reg.alias.paper)}</b><span class="dp-sub">${num(reg.headline.checks)} promotion checks passed</span></div>
            <span class="dp-arrow" aria-hidden="true">→</span>
            <div class="dp-alias locked"><span class="dp-an">live</span><b>locked</b><span class="dp-sub">needs a Gate 3 pass recorded on the version: ${num(reg.headline.gate3Tags)} recorded</span></div></div>
          <div class="dp-table" role="table" aria-label="Model versions">
            <div class="dp-tr dp-th dp-ver" role="row"><span role="columnheader">Version</span><span role="columnheader">What it is</span><span role="columnheader">Features</span><span role="columnheader">Holds</span><span role="columnheader">Last promoted by</span></div>
            <div class="dp-tr dp-ver on" role="row"><span role="cell" class="num">${T(modelV.ver)}</span><span role="cell">Served. LightGBM P10, P50, P90 for ${num(derived(F.hubs.length, 'hubs in the forecast table'))} hubs, a retrain on the data lake’s own builder.</span><span role="cell" class="num dp-mono">${esc(modelV.featureSet)}</span><span role="cell">${modelV.aliases.map((a) => `<span class="dp-pick">${esc(a)}</span>`).join(' ')}</span><span role="cell" class="num">${esc(modelV.by)}</span></div>
            <div class="dp-tr dp-ver" role="row"><span role="cell" class="num">${T(rollV.ver)}</span><span role="cell">Kept for a rollback. The first model; the replay in the first view ran on it.</span><span role="cell" class="num dp-mono">${esc(rollV.featureSet)}</span><span role="cell"><span class="dp-pick off">rollback</span></span><span role="cell" class="num">${esc(rollV.by)}</span></div>
            <div class="dp-tr dp-ver" role="row"><span role="cell" class="dp-dim">—</span><span role="cell">${T(baselines.length)} baselines, each registered as a version so every comparison is reproducible: same time yesterday, same time last week, the arena’s profile, the seven-day profile median.</span><span role="cell" class="dp-dim">same as the model</span><span role="cell"></span><span role="cell"></span></div>
          </div>
          <div class="dp-note">Rolling back is one command and a restart. Version ${T(modelV.ver)} is a retrain of the same recipe: the differences from version ${T(rollV.ver)} are about one percent in either direction, not an improvement.</div>
        </section>

        <section class="dp-panel dp-card" aria-label="Forecast against baselines">
          <div class="dp-card-h"><span class="dp-ct">Forecast quality against plain baselines</span><span class="dp-legend">${sTag('ready.forecast')}</span></div>
          <span class="dp-sub">Pinball loss (lower is better) on a walk-forward holdout of ${num(F.headline.rows)} forecasts per hub, 2025 to September 2026, Winter Storm Fern inside. The first bar is the served model.</span>
          <div class="dp-pin" role="img" aria-label="Pinball loss per hub: the model and four baselines">
            ${pb.rows.map((r) => `<div class="dp-pin-h"><b>${esc(r.hub)}</b>${r.items.map((x, j) => `<div class="dp-pin-r${j === 0 ? ' model' : ''}"><span>${esc(x.name)}</span><span class="dp-track"><i style="width:${x.pct.toFixed(1)}%"></i></span><span class="num">${fmtN(x.v, 2)}</span></div>`).join('')}</div>`).join('')}
          </div>
          <div class="dp-note">The served model is ${num(F.headline.gainLow, PCT1)} to ${num(F.headline.gainHigh, PCT1)} below the strongest baseline, hub by hub.</div>
          <div class="dp-table" role="table" aria-label="Coverage and capture by hub">
            <div class="dp-tr dp-th dp-fcm" role="row"><span role="columnheader">Hub</span><span role="columnheader">Band coverage, nominal ${num(F.headline.nominal, PCT0)}</span><span role="columnheader">Capture, model plan ${sTag('ready.forecast')}</span><span role="columnheader">Capture, seven-day profile plan</span></div>
            ${F.hubs.map((x) => `<div class="dp-tr dp-fcm" role="row"><span role="cell">${esc(x)}</span><span role="cell" class="num">${fmtN(F.model[x].coverage * 100, 1)}%</span><span role="cell" class="num">${fmtN(F.model[x].capture, 2)}</span><span role="cell" class="num">${fmtN(F.base.profile_median_7d[x].capture, 2)}</span></div>`).join('')}
          </div>
          <div class="dp-note">Capture is the revenue of a rolling plan built on the median forecast, over perfect foresight, for one reference battery with no confidence interval. A better price forecast has not clearly bought better dispatch value: level with the plain profile, and behind it at ${num(derived(behindHubs(F).length, 'hubs where the model plan captures less than the profile plan'))} of ${num(derived(F.hubs.length, 'hubs'))} hubs.</div>
        </section>
      </div>

      <div class="dp-two">
        <div class="dp-stack">
          <section class="dp-panel dp-card" aria-label="LP against the dynamic programme">
            <div class="dp-card-h"><span class="dp-ct">The LP against the research arena’s dynamic programme</span><span class="dp-legend">${sTag('ready.dp')}</span></div>
            <span class="dp-sub">The same energy-only problem, solved two independent ways, on ${num(D.headline.days)} historical zone-days from 2011 to 2026, including the highest-spread and negative-price days. Each bar counts days whose LP-minus-DP difference, in dollars per MW-day, is below its label.</span>
            <div class="dp-hist" role="img" aria-label="Days by size of the difference between the LP and the dynamic programme: all far below the tolerance">
              ${bins.map((b, k) => `<div class="dp-hb${k === 0 ? ' z' : ''}"><span class="dp-hb-n num">${b.n}</span><span class="dp-hb-bar"><i style="height:${(b.n / binMax * 100).toFixed(1)}%"></i></span><span class="dp-hb-l num">${esc(b.label)}</span></div>`).join('')}
              <span class="dp-hist-tol">the test’s tolerance is the last label: ${num(rescale(D.headline.tolerance, sci(D.headline.tolerance.v, 0)), { money: true })}</span></div>
            <div class="dp-kv"><span>Largest difference</span><span>${num(rescale(D.headline.maxAbs, sci(D.headline.maxAbs.v)), { money: true })}</span></div>
            <div class="dp-kv"><span>Days the two agree exactly</span><span>${num(D.headline.exact)}</span></div>
            <div class="dp-kv"><span>Total, dynamic programme / LP</span><span>${num(D.headline.totalDp, MONEY0)} <span class="dp-dim">/</span> ${num(D.headline.totalLp, MONEY0)}</span></div>
            <div class="dp-note">So the LP is the dynamic programme’s exact relaxation. With a grid-side charge limit, as the guard enforces, it earns ${num(rescale(D.headline.gridBasis, Math.abs(D.headline.gridBasis.v)), PCT1)} less in total.</div>
          </section>
        </div>

        <section class="dp-panel dp-card" aria-label="What is still open">
          <div class="dp-card-h"><span class="dp-ct">What is still open</span><span class="dp-legend">${h.tag('ASSUMPTION', 'dispatch/README.md, Known limits and open questions')}</span></div>
          <span class="dp-sub">The PRD’s open questions. These are business and legal decisions, and this repository cannot make them.</span>
          <ul class="dp-qs-list">${R.questions.map((q) => `<li><b>${esc(q.q)}</b><span class="dp-sub">${esc(q.state)}</span><span class="dp-who-t">${esc(q.who)}</span></li>`).join('')}</ul>
        </section>
      </div>

      <section class="dp-panel dp-card" aria-label="Built, and not running">
        <span class="dp-ct">Built, and not running</span>
        <div class="dp-notrun">${R.notBuilt.map((q) => `<div><b>${esc(q.what)}</b><span class="dp-sub">${esc(q.state)}</span></div>`).join('')}</div>
      </section>

      <section class="dp-panel dp-card" aria-label="Computed sentences"><span class="dp-ct">Read the status</span><span class="dp-sub">${num(derived(reads.length, 'sentences below'))} sentences, each computed from the platform’s files.</span>
        <ol class="dp-reads">${reads.map((r) => `<li><span class="dp-rn" aria-hidden="true"></span><span><b>${esc(r.lead)}</b> ${r.html}</span></li>`).join('')}</ol></section>`;
}

// ---------------------------------------------------------------------------------------------------------------- the page
export function viewHTML(doc, st, h) {
  return st.view === 1 ? cycleHTML(doc, st, h) : st.view === 2 ? earnedHTML(doc, st, h) : st.view === 3 ? safeHTML(doc, st, h) : readyHTML(doc, st, h);
}

function ensureCss() {
  if (typeof document === 'undefined') return Promise.resolve();
  if (document.querySelector('link[data-dispatch-css]')) return Promise.resolve();
  return new Promise((resolve) => {
    const l = document.createElement('link');
    l.rel = 'stylesheet';
    l.href = new URL('./dispatch.css', import.meta.url).href;
    l.dataset.dispatchCss = '1';
    l.onload = l.onerror = () => resolve();
    document.head.appendChild(l);
  });
}
function loadState() { try { return JSON.parse(localStorage.getItem(STORE) || 'null') || {}; } catch (e) { return {}; } }
function saveState(s) { try { localStorage.setItem(STORE, JSON.stringify(s)); } catch (e) { /* private mode */ } }

export async function mount(root, ctx) {
  const num = (x, o) => ctx.num(x, o);
  const saved = loadState();
  const st = { view: viewOf(ctx.params && ctx.params.q), scope: SCOPES.some(([id]) => id === saved.scope) ? saved.scope : 'all_AS', i: 0, day: 0, topAll: false };
  root.innerHTML = '<div class="dp"><div class="dp-loading">Loading the dispatch platform’s files…</div></div>';
  let doc;
  try {
    [doc] = await Promise.all([ctx.getJSON(DATA), ensureCss()]);
  } catch (e) {
    root.innerHTML = `<div class="dp"><div class="st-placeholder"><div class="st-eyebrow">DISPATCH</div><div class="st-ph-title">The dispatch page is not built yet</div>
      <p>ui/data/${DATA} did not load. Build it with <code>scripts/export_dispatch.py</code> (it reads dispatch/ and its runtime).</p></div></div>`;
    return { dispose() {} };
  }
  st.i = defaultDecision(doc.cycle);
  st.day = doc.earned.days.gap.indexOf(Math.max(...doc.earned.days.gap));
  const el = document.createElement('div');
  el.className = 'dp';
  root.replaceChildren(el);
  const h = { num, sTag: sTagOf(doc), tag: tagHTML };

  function render() {
    const body = el.querySelector('.dp-body');
    const top = body ? body.scrollTop : 0;
    el.innerHTML = `${railHTML(doc, st, h)}<div class="dp-body">${viewHTML(doc, st, h)}</div>`;
    const nb = el.querySelector('.dp-body');
    if (nb) nb.scrollTop = top;
    saveState({ scope: st.scope });
  }
  function setDecision(i, fromRange) {
    st.i = Math.max(0, Math.min(doc.cycle.n - 1, i));
    const dyn = el.querySelector('[data-dyn]');
    if (!dyn) return;
    dyn.innerHTML = cycleDynHTML(doc, st, h);
    const cur = el.querySelector('[data-cursor]');
    if (cur) cur.style.left = `${((st.i + 0.5) / doc.cycle.n * 100).toFixed(3)}%`;
    const clk = el.querySelector('[data-clock]');
    if (clk) clk.innerHTML = clockHTML(doc, st.i);
    const rng = el.querySelector('[data-range]');
    if (rng && !fromRange) rng.value = String(st.i);
    for (const b of el.querySelectorAll('.dp-moment')) { const on = Number(b.dataset.moment) === st.i; b.classList.toggle('on', on); b.setAttribute('aria-pressed', String(on)); }
  }
  const onClick = (ev) => {
    const t = ev.target.closest('button');
    if (!t || !el.contains(t)) {
      const lanes = ev.target.closest && ev.target.closest('[data-lanes]');
      if (lanes) {
        const plot = lanes.querySelector('.dp-lane-plot').getBoundingClientRect();
        const x = (ev.clientX - plot.left) / plot.width;
        if (x >= 0 && x <= 1) { setDecision(Math.floor(x * doc.cycle.n)); }
      }
      return;
    }
    if (t.dataset.q) {
      st.view = viewOf(Number(t.dataset.q));
      history.replaceState(history.state, '', ctx.link({ page: PAGE, q: st.view }));
      render();
      const b = el.querySelector('.dp-body');
      if (b) b.scrollTop = 0;
      return;
    }
    if (t.dataset.moment) { setDecision(Number(t.dataset.moment)); return; }
    if (t.dataset.scope) { st.scope = t.dataset.scope; render(); return; }
    if (t.dataset.day !== undefined && t.dataset.day !== '') { st.day = Number(t.dataset.day); const f = t.dataset.day; render(); const b = el.querySelector(`.dp-col[data-day="${f}"]`); if (b) b.focus(); return; }
    if (t.dataset.top) { st.topAll = t.dataset.top === 'more'; render(); const b = el.querySelector('.dp-more'); if (b) b.focus(); }
  };
  const onInput = (ev) => { if (ev.target && ev.target.matches && ev.target.matches('[data-range]')) setDecision(Number(ev.target.value), true); };
  const onFocus = (ev) => {
    const c = ev.target.closest && ev.target.closest('.dp-col');
    if (c && Number(c.dataset.day) !== st.day) { st.day = Number(c.dataset.day); const f = c.dataset.day; render(); const b = el.querySelector(`.dp-col[data-day="${f}"]`); if (b) b.focus(); }
  };
  el.addEventListener('click', onClick);
  el.addEventListener('input', onInput);
  el.addEventListener('focusin', onFocus);
  render();
  return {
    dispose() {
      el.removeEventListener('click', onClick);
      el.removeEventListener('input', onInput);
      el.removeEventListener('focusin', onFocus);
    },
  };
}
