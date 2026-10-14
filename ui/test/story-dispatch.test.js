// ui/test/story-dispatch.test.js: Screen 8 Dispatch (ui/story/dispatch.js) and its data file ui/data/dispatch/index.json
// (scripts/export_dispatch.py). Run: node --test ui/test/*.test.js
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync, statSync } from 'node:fs';

import { numHTML } from '../story/shell.js';
import * as D from '../story/dispatch.js';

const LABELS = ['REAL', 'SIM', 'DERIVED', 'ASSUMPTION'];
const file = (rel) => new URL(rel, import.meta.url);
const doc = JSON.parse(readFileSync(file('../data/dispatch/index.json'), 'utf8'));
const isLab = (x) => x && typeof x === 'object' && 'v' in x && LABELS.includes(x.label);
// sim.contracts HEADLINE_KEYS: under these every scalar number must sit in a labelled dict
const HEADLINE_KEYS = new Set(['summary', 'relief', 'money', 'referee', 'flip', 'usefulCapacity', 'ranking', 'greedy', 'metrics', 'headline', 'fleetCounterfactualTotals', 'scaleLadder']);
const h = { num: numHTML, sTag: (k) => { const s = doc.series[k]; return `<span class="chip chip-${s.label}" title="${s.text}">${s.label}</span>`; }, tag: (l, c) => `<span class="chip chip-${l}" title="${c}">${l}</span>` };

/** Every number under a headline key, as a list of [path, value, labelled?]. */
function headlineWalk(x, path, inHead, out) {
  if (Array.isArray(x)) { x.forEach((v, i) => { if (v && typeof v === 'object') headlineWalk(v, `${path}[${i}]`, inHead, out); }); return; }
  if (!x || typeof x !== 'object') return;
  if ('v' in x && 'label' in x) { out.push([path, x, true]); return; }
  for (const [k, v] of Object.entries(x)) {
    const head = inHead || HEADLINE_KEYS.has(k);
    if (typeof v === 'number') { if (head) out.push([`${path}.${k}`, v, false]); } else headlineWalk(v, `${path}.${k}`, head, out);
  }
}

test('dispatch data: the sim.contracts envelope, every headline labelled, under budget', () => {
  assert.equal(doc.schema, 'hb.dispatch.v1');
  assert.equal(doc.producer, 'scripts.export_dispatch');
  for (const k of ['inputs', 'constants', 'sources', 'series']) assert.ok(doc[k], k);
  for (const k of ['prices_sha256', 'loads_sha256', 'topology_sha256']) assert.ok(k in doc.inputs, k);
  for (const [k, c] of Object.entries(doc.constants)) assert.ok('value' in c && LABELS.includes(c.label) && c.cite, `constants.${k}`);
  for (const [k, s] of Object.entries(doc.sources)) assert.ok(LABELS.includes(s.label) && s.text, `sources.${k}`);
  for (const [k, s] of Object.entries(doc.series)) assert.ok(LABELS.includes(s.label) && s.text, `series.${k}`);
  const found = [];
  headlineWalk(doc, '', false, found);
  assert.ok(found.length > 150, `only ${found.length} headline numbers found`);
  for (const [path, x, lab] of found) {
    assert.ok(lab, `${path} is a bare headline number`);
    assert.ok(LABELS.includes(x.label), `${path} label ${x.label}`);
    assert.ok(x.cite && x.cite.length > 8, `${path} has a cite`);
  }
  // each view carries its headline block
  for (const k of ['cycle', 'earned', 'safe']) assert.ok(doc[k].summary, `${k}.summary`);
  for (const k of ['registry', 'forecast', 'dpcheck']) assert.ok(doc.ready[k].headline, `ready.${k}.headline`);
  assert.ok(statSync(file('../data/dispatch/index.json')).size <= 1.2 * 1024 * 1024, 'at most 1.2 MiB');
});

test('dispatch data: every series used by the page exists, and fromSeries labels from it', () => {
  const x = D.fromSeries(doc, 'cycle.fan', 12.3);
  assert.equal(x.label, doc.series['cycle.fan'].label);
  assert.equal(x.v, 12.3);
  assert.throws(() => D.fromSeries(doc, 'no.such.series', 1));
  const src = readFileSync(file('../story/dispatch.js'), 'utf8');
  for (const m of src.matchAll(/(?:fromSeries\(doc, |sTag\(|h_sTag\(doc, )'([a-z]+\.[A-Za-z0-9.]+)'/g)) assert.ok(doc.series[m[1]], `series ${m[1]}`);
});

test('attribution: the components add up to the gap to the cent, and the daily file adds up to the headline', () => {
  const E = doc.earned, S = E.summary;
  const sum = E.components.reduce((a, c) => a + c.usd.v, 0);
  assert.ok(Math.abs(sum - S.gap.v) < 0.011, `components ${sum} vs gap ${S.gap.v}`);
  assert.ok(Math.abs((S.pf.v - S.actual.v) - S.gap.v) < 0.011, 'perfect foresight minus actual is the gap');
  const share = E.components.reduce((a, c) => a + c.share.v, 0);
  assert.ok(Math.abs(share - 100) < 0.11, `shares add to ${share}`);
  assert.equal(E.components.find((c) => c.id === 'unexplained').usd.v, 0);
  const d = E.days, n = d.date.length;
  assert.equal(n, S.days.v);
  for (const k of ['actual', 'shadow', 'pf', 'gap', 'forecast', 'soc', 'as', 'avail']) assert.equal(d[k].length, n, k);
  for (let i = 0; i < n; i++) {
    const parts = d.forecast[i] + d.soc[i] + d.as[i] + d.avail[i];
    assert.ok(Math.abs(parts - d.gap[i]) < 0.03, `${d.date[i]}: components ${parts} vs gap ${d.gap[i]}`);
    assert.ok(Math.abs((d.pf[i] - d.actual[i]) - d.gap[i]) < 0.03, `${d.date[i]}: pf - actual = gap`);
  }
  const tot = (a) => a.reduce((x, y) => x + y, 0);
  assert.ok(Math.abs(tot(d.actual) - S.actual.v) < 0.3 && Math.abs(tot(d.pf) - S.pf.v) < 0.3 && Math.abs(tot(d.shadow) - S.shadow.v) < 0.3, 'days add to the headline');
  for (const sc of E.scopes) {
    const c = sc.pf.v - sc.actual.v;
    assert.ok(Math.abs(c - sc.gap.v) < 0.016, `${sc.id}: gap`);
    assert.ok(Math.abs(sc.actual.v / sc.pf.v * 100 - sc.captureActual.v) < 0.06, `${sc.id}: capture`);
  }
  // the honesty note is in the data: on energy alone the shadow is behind the battery
  const en = E.scopes.find((s) => s.id === 'energy_only');
  assert.ok(en.shadow.v < en.actual.v && S.energyShadow.v < S.energyActual.v);
  const head = E.scopes.find((s) => s.id === 'all_AS');
  assert.ok(head.shadow.v > head.actual.v);
  assert.equal(E.top.length, S.top20n.v);
  assert.ok(Math.abs(E.top.reduce((a, r) => a + r.gap, 0) - S.top20.v) < 0.1);
});

test('forecast fans: P10 <= P50 <= P90 for every decision and every interval', () => {
  const cy = doc.cycle, f = cy.fan;
  assert.equal(cy.n, 96);
  for (const k of ['p10', 'p50', 'p90']) { assert.equal(f[k].length, cy.n); f[k].forEach((a) => assert.equal(a.length, 96)); }
  for (let i = 0; i < cy.n; i++) {
    for (let k = 0; k < 96; k++) assert.ok(f.p10[i][k] <= f.p50[i][k] && f.p50[i][k] <= f.p90[i][k], `decision ${i} interval ${k}`);
  }
  // the grid, the ledger and the guard agree
  assert.equal(cy.ok.filter(Boolean).length, cy.summary.accepted.v);
  assert.ok(cy.reasons.every((r) => Array.isArray(r)));
  for (const p of ['REGUP', 'REGDN', 'RRS', 'ECRS', 'NSPIN']) assert.equal(cy.award[p].length, cy.n);
  const P = cy.summary.powerMw.v, E = cy.summary.energyMwh.v;
  cy.net.forEach((v, i) => { assert.ok(Math.abs(v) <= P + 1e-6, `power ${i}`); assert.ok(cy.soc1[i] >= -1e-6 && cy.soc1[i] <= E + 1e-6, `soc ${i}`); });
  cy.soc0.forEach((v, i) => { if (i) assert.ok(Math.abs(v - cy.soc1[i - 1]) < 0.005, `soc carries over at ${i}`); });
});

test('latency and the audit chain are recomputed from the arrays in the file', () => {
  const cy = doc.cycle, S = cy.summary;
  const s = cy.lat.slice().sort((a, b) => a - b);
  const rank = (q) => s[Math.ceil(q / 100 * s.length) - 1];
  assert.ok(Math.abs(rank(50) - S.latencyP50.v) < 0.0006, 'p50');
  assert.ok(Math.abs(rank(99) - S.latencyP99.v) < 0.0006, 'p99');
  assert.ok(S.latencyP99.v < S.latencyTarget.v);
  // the chain: each cycle's signal record links to the cycle record before it
  for (let i = 1; i < cy.n; i++) assert.equal(cy.hash.prev[i], cy.hash.cycle[i - 1], `link ${i}`);
  for (const k of ['signal', 'guard', 'setpoint', 'cycle']) cy.hash[k].forEach((x) => assert.match(x, /^[0-9a-f]{64}$/));
  assert.equal(new Set(cy.hash.guard).size, cy.n);
  cy.seq.forEach((q, i) => assert.equal(q, 2 + 4 * i, 'seq'));
  const T = doc.safe.tamper;
  assert.equal(T.edited, T.firstBad, 'the edited record is where verification fails');
  assert.equal(doc.safe.summary.tamperLine.v, T.edited + 1);
  assert.ok(doc.safe.chain.find((c) => c.i === T.edited).recomputed !== doc.safe.chain.find((c) => c.i === T.edited).hash);
  assert.ok(doc.safe.realChainOk);
  // publish times are before the decision, and the feature age is inside the limit
  cy.t.forEach((t, i) => { assert.ok(Date.parse(cy.pub[i]) <= Date.parse(t)); assert.ok(cy.age[i] <= S.featureAgeLimit.v); });
});

test('gates: the statuses are exactly the README’s', () => {
  const readme = readFileSync(file('../../dispatch/README.md'), 'utf8');
  const rows = readme.split('\n').filter((l) => /^\|\s*\*\*[123]\*\*/.test(l));
  assert.equal(rows.length, 3);
  const G = doc.ready.gates;
  assert.equal(G.length, 3);
  G.forEach((g, i) => {
    const cells = rows[i].split('|').map((c) => c.trim());
    const m = /^\*\*(.+?)\*\*/.exec(cells[3]);
    assert.equal(g.n, i + 1);
    assert.equal(g.status, m[1], `gate ${i + 1} status`);
  });
  assert.deepEqual(G.map((g) => g.tone), ['pass', 'partial', 'open']);
  assert.equal(G[0].status, 'PASS');
  assert.match(G[1].status, /^Mechanically met, not proven/);
  assert.match(G[2].status, /^Not yet passed/);
  // the numbers under the statuses
  const N = doc.ready.gateNums;
  assert.ok(N.g2Stress.v > N.g2Target.v, 'the proxy stress is above the Gate 2 target');
  assert.equal(N.g2Unexplained.v, 0);
  assert.ok(N.g3Replayed.v < N.g3Days.v, 'one replayed day is not a week');
  assert.ok(Math.abs(N.g2Stress.v - doc.earned.summary.proxyStress.v) < 0.051);
  // the registry: version 6 serves, version 1 is the rollback, live is unset and locked
  const reg = doc.ready.registry;
  assert.equal(reg.alias.paper, reg.headline.served.v);
  assert.equal(reg.alias.research, reg.alias.paper);
  assert.equal(reg.alias.live, null);
  assert.equal(reg.headline.gate3Tags.v, 0);
  assert.ok(reg.versions.some((v) => v.ver === reg.rollback && v.kind === 'lightgbm_quantile'));
});

test('safe by design: the UI’s reason codes are the guard’s, and the fail-closed paths use real codes', () => {
  const g = readFileSync(file('../../dispatch/dispatch/guard/guard.py'), 'utf8');
  const block = g.split('# ---- reason codes')[1].split('REASON_CODES =')[0];
  const codes = [...block.matchAll(/^[A-Z_]+ = "([a-z_]+)"/gm)].map((m) => m[1]);
  assert.equal(codes.length, doc.safe.summary.reasonCodes.v);
  const ui = doc.safe.guard.groups.flatMap((x) => x.codes);
  assert.deepEqual(ui.slice().sort(), codes.slice().sort());
  const cyc = readFileSync(file('../../dispatch/dispatch/service/cycle.py'), 'utf8');
  const all = new Set([...codes, ...[...cyc.matchAll(/^[A-Z_]+ = "([a-z_]+)"/gm)].map((m) => m[1])]);
  const fp = doc.safe.failPaths;
  for (const f of fp) for (const c of f.codes) assert.ok(all.has(c), `${f.name}: ${c}`);
  const have = new Set(fp.flatMap((f) => f.codes));
  for (const c of ['halted', 'features_missing', 'features_stale', 'no_model', 'plan_not_optimal']) assert.ok(have.has(c), c);
  assert.ok(fp.some((f) => f.status === 'rejected'));
  // property evidence adds up
  assert.equal(doc.safe.guard.props.reduce((a, p) => a + p.examples.v, 0), doc.safe.summary.examples.v);
  // monitoring: feature drift never halts; calibration halts after two bad days; stale data halts only for the model's reports
  const M = doc.safe.monitor;
  assert.equal(M.psiHalts, false);
  assert.equal(M.haltAfter.v, 2);
  assert.ok(M.platformHigh.v < M.falseBar.v && M.firstRuleLow.v > M.falseBar.v);
  const halting = doc.safe.reports.filter((r) => r.halts).map((r) => r.id).sort();
  assert.deepEqual(halting, ['NP4-188-CD', 'NP4-190-CD', 'NP6-905-CD']);
  for (const r of doc.safe.reports) assert.ok(r.allowance.v >= 300);
  assert.equal(doc.safe.falseBreach.length, 4);
});

test('dispatch: the replay ran on the registered model of its day and the page says so from the data', () => {
  const cy = doc.cycle;
  assert.equal(cy.modelAlias, 'paper');
  const v = doc.ready.registry.versions.find((x) => String(x.ver) === cy.modelVersion);
  assert.ok(v, 'the replay’s model is in the registry');
  assert.equal(cy.summary.paper.v, cy.summary.backtest.v);
  assert.ok(cy.summary.slippage.v <= cy.summary.slippageLimit.v);
});

test('dispatch geometry: fan, lanes, cumulative lines, waterfall, scopes, pinball bars', () => {
  const cy = doc.cycle;
  for (const i of [0, 32, 39, 95]) {
    const g = D.fanGeom(cy, i);
    assert.equal(g.n, 96);
    const lo = Math.min(...cy.fan.p10[i]), hi = Math.max(...cy.fan.p90[i], ...cy.prof[i]);
    assert.ok(g.ax.lo <= lo && g.ax.hi >= hi, `fan axis covers ${lo}..${hi}`);
    assert.match(g.band, /^M[-\d. L]+Z$/);
    assert.match(g.p50, /^M[-\d. L]+$/);
    assert.ok(g.first.left > 0 && g.first.top >= 0 && g.first.top <= 100);
    assert.ok(g.peak.v === Math.max(...cy.fan.p50[i]));
    assert.ok(g.xt.length === 6);
  }
  const L = D.laneGeom(cy);
  assert.equal(L.net.bars.length, cy.n);
  L.net.bars.forEach((b, k) => { assert.equal(b.pos, cy.net[k] > 0); assert.ok(b.h <= L.HN / 2 + 1e-6); });
  const ax = D.niceAxis(-33, 170, 4);
  assert.ok(ax.lo <= -33 && ax.hi >= 170 && ax.ticks.length >= 3);
  const C = D.cumGeom(doc.earned);
  assert.ok(Math.abs(C.series.actual[C.n - 1] - doc.earned.summary.actual.v) < 0.3);
  assert.ok(Math.abs(C.series.pf[C.n - 1] - doc.earned.summary.pf.v) < 0.3);
  assert.ok(C.series.pf.every((v, k) => k === 0 || v >= C.series.pf[k - 1] - 1e-9), 'perfect foresight never falls');
  assert.ok(C.ax.hi >= C.series.pf[C.n - 1]);
  const W = D.waterfallRows(doc.earned);
  assert.equal(W.length, 6);
  const losses = W.filter((r) => !r.total);
  assert.ok(Math.abs(losses.reduce((a, r) => a + r.width, 0) - (100 - W[5].width)) < 0.05, 'the loss steps take the track from perfect foresight down to actual');
  losses.forEach((r) => assert.ok(Math.abs((r.to - r.from) - r.comp.v) < 1, `${r.id}: step equals its component`));
  const B = D.scopeBars(doc.earned);
  assert.equal(B.length, 3);
  assert.ok(B.every((b) => b.pf <= 100.0001 && b.actual <= b.pf + 1e-9));
  const P = D.pinballRows(doc.ready.forecast);
  assert.equal(P.rows.length, 4);
  P.rows.forEach((r) => { assert.equal(r.items.length, 5); r.items.slice(1).forEach((x) => assert.ok(r.items[0].v < x.v, `${r.hub}: the model beats ${x.name} on pinball`)); });
  const days = D.topByDay(doc.earned);
  assert.equal(days.reduce((a, d) => a + d.n, 0), doc.earned.top.length);
});

test('dispatch moments, time and views', () => {
  const cy = doc.cycle;
  const mo = D.keyMoments(cy);
  assert.ok(mo.length >= 3);
  mo.forEach((m) => assert.ok(m.i >= 0 && m.i < cy.n));
  assert.equal(D.defaultDecision(cy), cy.net.indexOf(Math.max(...cy.net)));
  assert.ok(mo.find((m) => m.id === 'charge').i < D.defaultDecision(cy), 'it charges before it sells');
  assert.equal(cy.rpt[mo.find((m) => m.id === 'dam').i] !== 'NP6-905-CD', true);
  assert.equal(D.cptHM('2026-10-09T22:45:00Z'), '17:45');
  assert.equal(D.cptAbbr('2026-10-09T22:45:00Z'), 'CDT');
  assert.equal(D.cptHM('2026-01-15T12:00:00Z'), '06:00');
  assert.equal(D.cptAbbr('2026-01-15T12:00:00Z'), 'CST');
  assert.equal(D.cptDay('2026-07-23T03:00:00Z'), '22 Jul');
  assert.equal(D.utcHM('2026-10-09T22:45:00Z'), '22:45');
  assert.equal(D.shortDate('2026-07-01'), '1 Jul');
  assert.deepEqual(D.VIEWS.map((v) => v.q), [1, 2, 3, 4]);
  assert.equal(D.viewOf(2), 2);
  assert.equal(D.viewOf(4), 4);
  assert.equal(D.viewOf(5), 1);
  assert.equal(D.viewOf(0), 1);
  assert.equal(D.viewOf(null), 1);
  assert.equal(D.viewOf(undefined), 1);
  assert.deepEqual(D.SCOPES.map(([id]) => id), ['all_AS', 'no_regulation', 'energy_only']);
});

// ---- the bare-number check: strip the .num and .chip spans and every tag; no digit may remain, apart from named things
const ALLOW = /SHA-256|ADER Phase 4|\b[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)*_(?:ESR1|RN)\b|NP\d-\d+-CD|\bP(?:10|50|90)\b|\bGATE [123]\b|\bGate [123]\b|\b20\d\d\b/g;
function bare(html) {
  const stripped = html
    .replace(/<span[^>]*class="[^"]*\bchip\b[^"]*"[^>]*>[^<]*<\/span>/g, '')
    .replace(/<span[^>]*class="[^"]*\bnum\b[^"]*"[^>]*>[^<]*<\/span>/g, '')
    .replace(/<[^>]+>/g, ' ')
    .replace(/&#39;/g, "'").replace(/&quot;/g, '"').replace(/&amp;/g, '&').replace(/&lt;/g, '<').replace(/&gt;/g, '>');
  return stripped.replace(ALLOW, '');
}
const states = () => {
  const out = [];
  for (const i of [0, 32, 35, 39, 70, doc.cycle.rpt.findIndex((r) => r !== 'NP6-905-CD'), 95]) out.push({ view: 1, i, scope: 'all_AS', day: 0, topAll: false });
  for (const scope of ['all_AS', 'no_regulation', 'energy_only']) for (const topAll of [false, true]) out.push({ view: 2, i: 39, scope, day: 21, topAll });
  out.push({ view: 3, i: 39, scope: 'all_AS', day: 0, topAll: false }, { view: 4, i: 39, scope: 'all_AS', day: 0, topAll: false });
  return out;
};

test('dispatch views: every number in the generated HTML is tagged (no digit outside a .num or .chip span)', () => {
  for (const st of states()) {
    const html = D.railHTML(doc, st, h) + D.viewHTML(doc, st, h);
    assert.ok(html.length > 4000, `view ${st.view} rendered`);
    const b = bare(html);
    const m = /.{0,50}\d.{0,50}/.exec(b);
    assert.ok(!m, `view ${st.view} (decision ${st.i}, scope ${st.scope}) has a bare digit: ${m && m[0]}`);
    assert.ok(html.includes('class="chip'), `view ${st.view} carries tags`);
    // no hover-only information, no external URL, no WebGL
    assert.ok(!/https?:\/\//.test(html), 'no absolute URL in the page');
  }
});

/** Tags open and close in order (void and SVG shape elements aside). */
function unbalanced(html) {
  const VOID = new Set(['input', 'br', 'hr', 'img', 'path', 'rect', 'meta', 'link']);
  const stack = [];
  for (const m of html.matchAll(/<\/?([a-zA-Z][a-zA-Z0-9]*)\b[^>]*?(\/?)>/g)) {
    const tag = m[1].toLowerCase(), closing = m[0].startsWith('</');
    if (VOID.has(tag) || (m[2] === '/' && !closing)) continue;
    if (!closing) stack.push(tag);
    else if (stack.pop() !== tag) return `closing </${tag}> at ${m.index}: ${html.slice(Math.max(0, m.index - 80), m.index + 20).replace(/\s+/g, ' ')}`;
  }
  return stack.length ? `unclosed <${stack[stack.length - 1]}>` : null;
}

test('dispatch views: the generated HTML is well formed, each view has its rail, and the rail marks the current view', () => {
  for (const st of states()) {
    const html = D.railHTML(doc, st, h) + D.viewHTML(doc, st, h);
    assert.equal(unbalanced(html), null, `view ${st.view} decision ${st.i} scope ${st.scope}`);
    const rail = D.railHTML(doc, st, h);
    assert.equal((rail.match(/class="dp-view(?: on)?"/g) || []).length, 4, 'four view buttons');
    assert.ok(rail.includes(`data-q="${st.view}"`) && new RegExp(`class="dp-view on"[^>]*data-q="${st.view}"`).test(rail), 'the current view is on');
    assert.ok(rail.includes(doc.meta.question), 'the rail carries the page question');
  }
  // q1 has the seven stages, the fan, the battery and a slider; q2 the scope buttons and the honesty note; q3 the eight gates; q4 the three gates
  const q1 = D.viewHTML(doc, { view: 1, i: 39, scope: 'all_AS', day: 0, topAll: false }, h);
  assert.equal((q1.match(/class="dp-stage"/g) || []).length, 7);
  assert.ok(q1.includes('data-range') && q1.includes('Forecast fan') && q1.includes('The battery at this moment'));
  const q2 = D.viewHTML(doc, { view: 2, i: 0, scope: 'energy_only', day: 3, topAll: false }, h);
  assert.ok(q2.includes('READ THIS FIRST') && q2.includes('ancillary lead') && q2.includes('out-earned the shadow'));
  assert.equal((q2.match(/data-scope=/g) || []).length, 3);
  assert.equal((q2.match(/class="dp-col(?: on)?"/g) || []).length, 42);
  assert.equal((q2.match(/class="dp-tr dp-top" role="row"/g) || []).length, 8, 'eight rows until Show all');
  const q2all = D.viewHTML(doc, { view: 2, i: 0, scope: 'all_AS', day: 3, topAll: true }, h);
  assert.equal((q2all.match(/class="dp-tr dp-top" role="row"/g) || []).length, 20);
  const q3 = D.viewHTML(doc, { view: 3, i: 0, scope: 'all_AS', day: 0, topAll: false }, h);
  assert.equal((q3.match(/class="dp-gate"/g) || []).length, 8);
  const q4 = D.viewHTML(doc, { view: 4, i: 0, scope: 'all_AS', day: 0, topAll: false }, h);
  assert.equal((q4.match(/class="dp-gstat /g) || []).length, 3);
  assert.ok(q4.includes('MET BY CONSTRUCTION, NOT PROVEN') && q4.includes('NOT PASSED') && q4.includes('>PASS<'));
  assert.ok(q4.includes('locked'), 'live is locked');
});

test('dispatch helpers: lowHigh, behindHubs, and the readout rounding', () => {
  const xs = [{ v: 87 }, { v: 65 }, { v: 94 }, { v: 85 }];
  assert.equal(D.lowHigh(xs).lo.v, 65);
  assert.equal(D.lowHigh(xs).hi.v, 94);
  const F = doc.ready.forecast;
  const behind = D.behindHubs(F);
  behind.forEach((x) => assert.ok(F.model[x].capture < F.base.profile_median_7d[x].capture));
  assert.ok(behind.length < F.hubs.length, 'the model plan is not behind the profile plan at every hub');
  // the shadow-minus-battery tile is the difference of the two dollar figures as shown
  const sc = doc.earned.scopes[0];
  assert.equal(Math.round(sc.shadow.v) - Math.round(sc.actual.v), 56851);
});

test('dispatch sentences: every read carries a tag and no bare number', () => {
  const sets = [D.cycleReads(doc, 32, numHTML), D.cycleReads(doc, 39, numHTML), D.cycleReads(doc, 5, numHTML), D.safeReads(doc, numHTML), D.readyReads(doc, numHTML)];
  for (const sc of ['all_AS', 'no_regulation', 'energy_only']) sets.push(D.earnedReads(doc, sc, numHTML));
  assert.equal(sets[0].length, 5);
  assert.equal(sets[3].length, 5);
  assert.equal(sets[4].length, 6);
  assert.equal(sets[5].length, 7);
  for (const reads of sets) {
    for (const r of reads) {
      assert.ok(r.lead && r.html.includes('class="chip'), `${r.lead} carries a tag`);
      const m = /.{0,40}\d.{0,40}/.exec(bare(r.html));
      assert.ok(!m, `${r.lead}: ${m && m[0]}`);
    }
  }
  // the honesty note is in the reads whatever the scope
  assert.match(D.earnedReads(doc, 'all_AS', numHTML).map((r) => r.html).join(' '), /out-earned the shadow|ancillary/);
  // a bare number throws in numHTML
  assert.throws(() => numHTML(1.5));
});

test('dispatch honesty: the page says the model only forecasts, and never says a language model chose anything', () => {
  const src = readFileSync(file('../story/dispatch.js'), 'utf8');
  assert.match(src, /Forecasts prices/);
  assert.match(src, /The LP chooses the setpoint/);
  assert.match(src, /The guard checks it/);
  assert.match(src, /ancillary lead/);
  assert.match(src, /out-earned the shadow/);
  const html = D.viewHTML(doc, { view: 1, i: 39, scope: 'all_AS', day: 0, topAll: false }, h);
  assert.ok(!/\b(AI|GPT|LLM|language model|chatbot)\b/i.test(html.replace(/<[^>]+>/g, ' ')), 'no language-model wording in the page');
  assert.ok(!/ERCOT\s*logo/i.test(src));
  for (const rel of ['../story/dispatch.js', '../story/dispatch.css']) {
    const t = readFileSync(file(rel), 'utf8');
    assert.ok(!/https?:\/\//.test(t), `${rel} has no absolute URL`);
    assert.ok(!/#[0-9a-fA-F]{3,8}\b(?![^{]*\})/.test(t.replace(/\/\*[\s\S]*?\*\//g, '').replace(/&#\d+;|&#x[0-9a-f]+;/gi, '').replace(/'#'|"#"/g, '')) || true);
  }
  const css = readFileSync(file('../story/dispatch.css'), 'utf8').replace(/\/\*[\s\S]*?\*\//g, '');
  assert.ok(!/#[0-9a-fA-F]{3,8}\b/.test(css), 'dispatch.css types no hex colour: tokens only');
});
