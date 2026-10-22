// ui/test/story-arena.test.js: Screen 6 Arena (ui/story/arena.js) and its data file ui/data/arena/index.json
// (scripts/export_arena.py). Run: node --test ui/test/*.test.js
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync, statSync } from 'node:fs';

import { numHTML } from '../story/shell.js';
import {
  ALGOS, LEARNER, MARKETS, SCOPES3, SPECS, VIEWS, answer1, answer2, axisOf, dayCaption, dayLayout, derived, entryOf, fillTokens, fleetParts,
  fleetSegments, forwardLayout, fromSeries, horizonLabel, hourRange, initialState, konst, leagueRows, optName, raceRows, railHTML, ribbonLayout,
  siteRows, valueOf, viewHTML, viewOf, windowIdx, winMap, yearSpans, yLab, zoneEntries,
} from '../story/arena.js';

const LABELS = ['REAL', 'SIM', 'DERIVED', 'ASSUMPTION'];
const FILE = new URL('../data/arena/index.json', import.meta.url);
const doc = JSON.parse(readFileSync(FILE, 'utf8'));
const isLab = (x) => x && typeof x === 'object' && 'v' in x && LABELS.includes(x.label);
const sum = (a) => a.reduce((s, x) => s + x, 0);
const avg = (a) => sum(a) / a.length;
const SPEC_KEYS = SPECS.map(([k]) => k);

test('arena data: the sim.contracts envelope, both markets, every part present', () => {
  assert.equal(doc.schema, 'hb.arena.v1');
  assert.equal(doc.producer, 'scripts.export_arena');
  for (const k of ['inputs', 'constants', 'sources', 'series']) assert.ok(doc[k], k);
  for (const k of ['prices_sha256', 'loads_sha256', 'topology_sha256']) assert.ok(k in doc.inputs, k);
  assert.deepEqual(Object.keys(doc.markets), MARKETS.map(([id]) => id));
  for (const [k, c] of Object.entries(doc.constants)) { assert.ok(LABELS.includes(c.label) && c.cite && 'value' in c, `constant ${k}`); }
  for (const [k, s] of Object.entries(doc.sources)) assert.ok(LABELS.includes(s.label) && s.text, `source ${k}`);
  for (const [k, s] of Object.entries(doc.series)) assert.ok(LABELS.includes(s.label) && s.text && s.unit, `series ${k}`);
  assert.deepEqual(doc.algorithms.map((a) => a.id), ALGOS);
  assert.deepEqual(doc.sitingRules.map((a) => a.id), ALGOS);
  const bytes = statSync(FILE).size;
  assert.ok(bytes <= 1.0 * 1024 * 1024, `arena data ${bytes} B is within the 1.0 MiB budget`);
  assert.ok(bytes <= 4 * 1024 * 1024);
  // no absolute URL anywhere in the data, the page or its stylesheet (the demo loads nothing offsite)
  for (const f of ['../data/arena/index.json', '../story/arena.js', '../story/arena.css']) {
    assert.doesNotMatch(readFileSync(new URL(f, import.meta.url), 'utf8'), /https?:\/\//, f);
  }
});

test('arena data: counts and shapes', () => {
  const want = { ERCOT: 8, NYISO: 11 };
  for (const [mk, M] of Object.entries(doc.markets)) {
    assert.equal(M.zones.length, want[mk], mk);
    assert.equal(M.years.length, 12);
    assert.equal(M.partial.length, 12);
    assert.deepEqual(M.partial.slice(0, 11), Array(11).fill(false));
    assert.equal(M.partial[11], true, 'the last year is year to date');
    assert.equal(M.months, (M.years.length - 1) * 12 + 9);
    assert.ok(M.events.length >= 3 && M.events.every((e) => LABELS.includes(e.label) && e.name && e.note && e.cite && M.years.includes(e.year)));
    assert.equal(M.all.id, 'ALL');
    for (const e of [M.all, ...M.zones]) {
      for (const s of SPEC_KEYS) {
        const D = e[s];
        for (const a of [...ALGOS, 'CEIL']) assert.equal(D.y[a].length, 12, `${mk} ${e.id} ${s} y.${a}`);
        assert.equal(D.fam.length, 4);
        for (const f of D.fam) assert.equal(f.length, M.months, `${mk} ${e.id} ${s} ribbon`);
        assert.equal(D.switches.length, 12);
        assert.equal(D.days.length, 6);
        assert.ok(Math.abs(sum(D.days) - 100) <= 3, `${mk} ${e.id} ${s} days per expert add to ~100`);
        for (let m = 0; m < M.months; m++) assert.ok(Math.abs(sum(D.fam.map((f) => f[m])) - 100) <= 3, `${mk} ${e.id} ${s} month ${m}`);
      }
    }
    for (const s of SPEC_KEYS) {
      const t = M.stats[s];
      for (const k of ['cycles', 'perMwh', 'tradeDays']) assert.equal(t[k].length, 5, `${mk} ${s} ${k}`);
      assert.ok(t.cycles.every((v) => v >= 0 && v <= 4) && t.tradeDays.every((v) => v >= 0 && v <= 100));
    }
    assert.equal(M.backup.length, 5);
    // the replayed days: one per rule A1-A4, a full 24 hours of quarter hours for every rule
    assert.equal(M.days.items.length, 4);
    assert.deepEqual(M.days.items.map((d) => d.algo), ['A1', 'A2', 'A3', 'A4']);
    const E = doc.constants.FULL_UNITS.value, R = doc.constants.RESERVE_UNITS.value;
    for (const d of M.days.items) {
      assert.equal(d.price.length, 96);
      assert.equal(d.profit.length, 6);
      assert.ok(d.followed >= 0 && d.followed < doc.meta.learner.experts.length);
      for (const a of ALGOS) {
        assert.equal(d.act[a].length, 96);
        assert.equal(d.soc[a].length, 96);
        assert.ok(d.act[a].every((u) => Math.abs(u) <= 2), 'power never above full');
        assert.ok(d.soc[a].every((s) => s >= R && s <= E), `${mk} ${d.date} ${a}: the member reserve is never breached`);
      }
      assert.ok(d.profit[5] >= Math.max(...d.profit.slice(0, 5)) - 0.01, `${mk} ${d.date}: the ceiling is a fair yardstick on the replayed day`);
      assert.ok(d.margin > 0);
      const best = ALGOS.slice(0, 4).map((a, i) => ({ a, v: d.profit[i] })).sort((x, y) => y.v - x.v)[0];
      assert.equal(best.a, d.algo, `${mk} ${d.date} is ${d.algo}'s day`);
    }
  }
  // the siting tournament, the ranking and the programs
  const S = doc.siting;
  assert.deepEqual(Object.keys(S.backtest), ['ERCOT', 'NYISO', 'BOTH']);
  for (const sc of Object.keys(S.backtest)) {
    for (const a of [...ALGOS, 'A5ts']) {
      assert.equal(S.backtest[sc].y[a].length, 3);
      assert.ok(Math.abs(sum(S.backtest[sc].fleet[a].map(([, mw]) => mw)) - 50) < 1e-6 || a === 'A5ts', `${sc} ${a} built 50 MW`);
    }
    const F = S.forward[sc];
    for (const a of ALGOS) {
      for (const k of ['mean', 'p10', 'p50', 'p90', 'win']) assert.equal(F[k][a].length, 3, `${sc} ${a} ${k}`);
      assert.equal(F.all[a].length, 3);
      for (const h of F.all[a]) assert.equal(h.length, doc.constants.FUTURES.value);
      for (let h = 0; h < 3; h++) assert.ok(F.p10[a][h] <= F.p50[a][h] && F.p50[a][h] <= F.p90[a][h], `${sc} ${a} P10 <= P50 <= P90`);
      assert.ok(Math.abs(sum(F.fleet[a].map(([, mw]) => mw)) - 50) < 0.6, `${sc} ${a} 2031 fleet is ~50 MW`);
      assert.ok(Math.abs(sum(F.first[a].map(([, mw]) => mw)) - 10) < 1e-6);
    }
    for (let h = 0; h < 3; h++) assert.ok(sum(ALGOS.map((a) => F.win[a][h])) >= 100 - 3, `${sc} horizon ${h}: shares of futures won cover the futures (ties share)`);
    const K = S.sites[sc];
    const n = K.opt.length;
    for (const k of ['median', 'p10', 'energy', 'capacity', 'program', 'event', 'upgrades', 'total']) assert.equal(K[k].length, n, `${sc} sites.${k}`);
    assert.ok(K.opt.every((i) => S.options[i]), 'every site names a known option');
    for (let i = 1; i < n; i++) assert.ok(K.median[i - 1] >= K.median[i], `${sc} ranking is sorted by median`);
    for (let i = 0; i < n; i++) assert.ok(Math.abs(K.energy[i] + K.capacity[i] + K.program[i] + K.event[i] + K.upgrades[i] - K.total[i]) < 0.35, `${sc} site ${i}: the stack adds up`);
  }
  assert.equal(S.start, 2027);
  assert.deepEqual(S.horizons, [1, 3, 5]);
  assert.equal(doc.programs.length, 7);
  assert.equal(doc.infrastructure.length, 4);
});

test('arena: every headline number is labelled', () => {
  for (const [mk, M] of Object.entries(doc.markets)) {
    for (const s of SPEC_KEYS) for (const [k, x] of Object.entries(M.headline[s])) assert.ok(isLab(x), `${mk} headline.${s}.${k}`);
  }
  for (const p of doc.programs) for (const k of ['pays', 'events', 'chance', 'eventPays']) assert.ok(isLab(p[k]), `${p.name} ${k}`);
  for (const x of doc.infrastructure) for (const k of ['effect', 'with', 'without', 'beforeAfterWith', 'beforeAfterWithout']) assert.ok(isLab(x[k]), `${x.event} ${k}`);
  for (const [k, x] of Object.entries(doc.feeder)) assert.ok(isLab(x), `feeder.${k}`);
  // fromSeries gives a labelled number from the series entry, and throws on an unknown series
  const x = fromSeries(doc, 'y', 12.3);
  assert.equal(x.label, doc.series.y.label);
  assert.equal(x.v, 12.3);
  assert.throws(() => fromSeries(doc, 'no.such.series', 1));
  assert.deepEqual(konst(doc, 'WEAR_USD_MWH').label, doc.constants.WEAR_USD_MWH.label);
  assert.throws(() => konst(doc, 'NO_SUCH_CONSTANT'));
  assert.equal(derived(3, 'a + b').label, 'DERIVED');
  assert.match(derived(3, 'a + b').cite, /^DERIVED: a \+ b$/);
});

test('arena: the ceiling is at least every rule, per zone, year and battery', () => {
  for (const [mk, M] of Object.entries(doc.markets)) {
    for (const e of [M.all, ...M.zones]) {
      for (const s of SPEC_KEYS) {
        for (const a of ALGOS) {
          e[s].y[a].forEach((v, i) => assert.ok(v <= e[s].y.CEIL[i] + 0.11, `${mk} ${e.id} ${s} ${M.years[i]} ${a}: ${v} vs ceiling ${e[s].y.CEIL[i]}`));
        }
      }
    }
  }
});

test('arena: the page recomputes the arena report’s own 5-year league (README table)', () => {
  for (const [mk, M] of Object.entries(doc.markets)) {
    for (const s of SPEC_KEYS) {
      const H = M.headline[s];
      for (const a of ALGOS) assert.ok(Math.abs(valueOf(doc, mk, M.all, s, a, "mean") - H[a].v) < 0.11, `${mk} ${s} ${a}`);
      assert.ok(Math.abs(valueOf(doc, mk, M.all, s, "CEIL", "mean") - H.ceiling.v) < 0.11);
      assert.ok(Math.abs(Math.round(valueOf(doc, mk, M.all, s, LEARNER, 'mean') / valueOf(doc, mk, M.all, s, 'CEIL', 'mean') * 100) - H.capture.v) <= 1);
    }
  }
  // the README's headline numbers (home fleet, mean of 2021-25): learner 38.1 and ceiling 92.8 in ERCOT, 16.2 and 40.8 in New York
  const h = (mk, k) => doc.markets[mk].headline.home[k].v;
  assert.equal(h('ERCOT', 'A5'), 38.1);
  assert.equal(h('ERCOT', 'ceiling'), 92.8);
  assert.equal(h('NYISO', 'A5'), 16.2);
  assert.equal(h('NYISO', 'ceiling'), 40.8);
});

test('arena: the race, the league and the win map are consistent', () => {
  for (const [mk, M] of Object.entries(doc.markets)) {
    for (const s of SPEC_KEYS) for (const period of ['mean', 2023, 2026]) {
      const E = M.zones[0], r = raceRows(doc, mk, E, s, period);
      assert.equal(r.rows.length, 5);
      assert.ok(r.rows.every((x) => x.share <= 100.5), "a rule never keeps more than the ceiling");
      assert.ok(r.rows.find((x) => x.id === r.lead).v >= Math.max(...r.rows.map((x) => x.v)) - 1e-9);
      assert.notEqual(r.bestFixed, LEARNER);
      const lg = leagueRows(doc, mk, s, period);
      assert.equal(lg.length, M.zones.length + 1);
      assert.equal(lg[0].id, 'ALL');
      assert.ok(lg.every((row) => ALGOS.includes(row.lead) && row.ceil > 0));
    }
    const wm = winMap(doc, mk, M.all, 'home');
    assert.equal(wm.cols.length, 12);
    assert.equal(wm.inWin.length, 5);
    assert.ok(wm.nWinners >= 1 && wm.nWinners <= 5);
    assert.ok(wm.cols.every((c) => ALGOS.every((a) => c.v[a] <= c.v[c.win] + 1e-9)));
    assert.ok(wm.cols.find((c) => c.year === 2021).event, 'the first named event (Uri / Indian Point) sits on 2021');
  }
  // the report's story: no single rule wins every year, and the learner wins the five years together
  const E = winMap(doc, 'ERCOT', doc.markets.ERCOT.all, 'home'), N = winMap(doc, 'NYISO', doc.markets.NYISO.all, 'home');
  assert.ok(E.nWinners >= 3, 'several different rules finish first in 2021-25 in ERCOT');
  assert.equal(E.mean.win, LEARNER);
  assert.equal(N.mean.win, LEARNER);
  assert.equal(E.cols.find((c) => c.year === 2021).win, 'A3', 'Weather-regime won the Uri year');
  assert.equal(E.cols.find((c) => c.year === 2023).win, 'A2', 'Forecaster won 2023');
  assert.equal(E.cols.find((c) => c.year === 2024).win, 'A1', 'Clockwork won 2024');
});

test('arena pure layout: ribbon, spans, axes, bars, strips, ranking, day', () => {
  const M = doc.markets.NYISO, fam = M.all.home.fam;
  const rb = ribbonLayout(fam);
  assert.equal(rb.paths.length, 4);
  assert.equal(rb.n, M.months);
  for (const p of rb.paths) assert.match(p.d, /^M[\d. L-]+Z$/);
  for (let m = 0; m < rb.n; m++) assert.ok(Math.abs(rb.cum[3][m] - 1) < 1e-9, 'every month fills the full height');
  const sp = yearSpans(M.years[0], M.years, M.months);
  assert.equal(sum(sp.map((s) => s.months)), M.months);
  assert.equal(sp[sp.length - 1].months, 9);
  const ax = axisOf(3.2, 47);
  assert.ok(ax.a0 <= 3.2 && ax.a1 >= 47 && ax.ticks[0].left === 0 && Math.abs(ax.ticks.at(-1).left - 100) < 1e-6);
  const neg = axisOf(-17, 140);
  assert.ok(neg.ticks.some((t) => t.v === 0), 'an axis spanning zero has a zero tick');
  const segs = fleetSegments(doc, doc.siting.forward.ERCOT.fleet.A5);
  assert.ok(Math.abs(sum(segs.map((s) => s.pct)) - 100) < 1e-9);
  assert.ok(segs.every((s) => s.name.length > 0));
  const fp = fleetParts(doc, doc.siting.forward.ERCOT.fleet.A3, false, 4);
  assert.equal(fp.top.length, 4);
  assert.equal(fp.top.length + fp.rest.length, doc.siting.forward.ERCOT.fleet.A3.length);
  for (const sc of ['ERCOT', 'NYISO', 'BOTH']) for (let h = 0; h < 3; h++) {
    const fl = forwardLayout(doc, sc, h);
    assert.equal(fl.rows.length, 5);
    for (const r of fl.rows) {
      assert.equal(r.dots.length, doc.constants.FUTURES.value);
      assert.ok(r.dots.every((d) => d.left >= -1e-6 && d.left <= 100 + 1e-6 && d.row >= 0 && d.row < 3));
      assert.ok(r.p10 <= r.p90 && r.mean >= 0 && r.mean <= 100);
    }
    const sr = siteRows(doc, sc, 12);
    assert.ok(sr.rows.length <= 12 && sr.count === doc.siting.sites[sc].opt.length);
    assert.ok(sr.zero >= 0 && sr.zero <= 100);
    for (const r of sr.rows) {
      for (const s of r.seg) assert.ok(s.left >= sr.zero - 1e-6 && s.left + s.width <= 100 + 1e-6);
      if (r.cost) assert.ok(r.cost.left >= -1e-6 && r.cost.left + r.cost.width <= sr.zero + 1e-6, 'the upgrade cost sits left of zero');
    }
  }
  const item = doc.markets.ERCOT.days.items[0], dl = dayLayout(doc, item);
  assert.match(dl.price, /^M0 [\d.]+H1/);
  assert.equal(dl.rows.length, 5);
  for (const r of dl.rows) {
    assert.match(r.soc, /^M0 62.*Z$/);
    assert.ok(r.reserve < 62 && r.reserve > 62 - 24 - 1e-9, 'the reserve line sits in the state-of-charge band');
    assert.ok(r.soldMWh >= 0 && r.boughtMWh >= 0);
  }
  assert.equal(dl.rows[3].soldMWh > 0 || dl.rows[3].avgSell === null, true);
  assert.equal(hourRange(15, 18), '3–6 pm');
  assert.equal(hourRange(9, 14), '9 am–2 pm');
  assert.equal(hourRange(14, 24), '2–12 am');
  assert.equal(horizonLabel(doc, 0), '2027');
  assert.equal(horizonLabel(doc, 1), '2027–29');
  assert.equal(horizonLabel(doc, 2), '2027–31');
  assert.equal(optName(doc, doc.siting.sites.BOTH.opt[0]), 'NYC (J) + Con Ed load relief');
  assert.equal(optName(doc, doc.siting.sites.ERCOT.opt.at(-1)), 'West / Permian merchant');
  assert.equal(optName(doc, doc.siting.sites.ERCOT.opt.at(-1), true), 'West / Permian');
});

test('arena: views, state and words', () => {
  assert.deepEqual(VIEWS.map((v) => v.q), [1, 2, 3, 4]);
  assert.equal(viewOf(1), 1);
  assert.equal(viewOf(4), 4);
  assert.equal(viewOf(5), 1);
  assert.equal(viewOf(null), 1);
  assert.equal(viewOf('2'), 1, 'q arrives as a number from the app, never a string');
  assert.deepEqual(SCOPES3.map(([k]) => k), ['ERCOT', 'NYISO', 'BOTH']);
  const st = initialState(doc, { q: 3 }, {});
  assert.equal(st.view, 3);
  assert.equal(st.mk, 'ERCOT');
  assert.equal(st.spec, 'home');
  assert.equal(st.zone.ERCOT, 'ALL');
  assert.equal(st.hz, 2);
  assert.equal(st.year2, 2021);
  const st2 = initialState(doc, {}, { mk: 'NYISO', spec: 'grid', zone: { NYISO: 'LONGIL', ERCOT: 'NOPE' } });
  assert.equal(st2.mk, 'NYISO');
  assert.equal(st2.spec, 'grid');
  assert.equal(st2.zone.NYISO, 'LONGIL');
  assert.equal(st2.zone.ERCOT, 'ALL', 'a saved zone that does not exist falls back to the market mean');
  assert.equal(entryOf(doc, 'ERCOT', 'NOPE').id, 'ALL');
  assert.equal(zoneEntries(doc, 'NYISO').length, 12);
  assert.deepEqual(windowIdx(doc, doc.markets.ERCOT), [6, 7, 8, 9, 10]);
  // tokens in the rule sentences come from the constants; an unknown token is an error, not a typed number
  const html = fillTokens(doc, 'Buys below the {SPIKE_LO} percentile of {SPIKE_WIN} days.', numHTML, false);
  assert.match(html, /<span class="num">25th<\/span><span class="chip chip-ASSUMPTION"/);
  assert.match(fillTokens(doc, 'last {CLOCK_DAYS} days', numHTML), /^last <span class="num">28<\/span> days$/);
  assert.throws(() => fillTokens(doc, '{NO_SUCH}', numHTML));
  assert.equal(yLab(doc, 'y', 12.3, 2023).label, 'SIM');
  assert.equal(yLab(doc, 'y', 12.3, 'mean').label, 'DERIVED');
  // the answer sentences name the right rule
  const E = entryOf(doc, 'ERCOT', 'ALL');
  const race = raceRows(doc, 'ERCOT', E, 'home', 'mean');
  assert.match(answer1(doc, { ...st, period: 'mean', zone: { ERCOT: 'ALL' } }, race, numHTML), /Adaptive learner earned the most/);
  const race23 = raceRows(doc, 'ERCOT', E, 'home', 2023);
  assert.match(answer1(doc, { ...st, mk: 'ERCOT', period: 2023, zone: { ERCOT: 'ALL' } }, race23, numHTML), /Forecaster earned the most/);
  assert.match(answer2(doc, { ...st, mk: 'ERCOT' }, winMap(doc, 'ERCOT', E, 'home'), numHTML), /different rules won a year/);
  assert.match(dayCaption(doc, 'ERCOT', doc.markets.ERCOT.days.items[0], numHTML), /Clockwork earned/);
});

// ---- the bare-number check on every generated view: strip every <span class="num"> and every provenance chip, drop the
// tags and the entities, and no digit may remain. numHTML (the app's ctx.num) throws on a bare labelled number.
const stripNums = (h) => h.replace(/<(span|i|b)\b[^>]*class="[^"]*\bnum\b[^"]*"[^>]*>[^<]*<\/\1>/g, '').replace(/<span class="chip[^>]*>[^<]*<\/span>/g, '')
  .replace(/<[^>]+>/g, '').replace(/&#39;/g, "'").replace(/&amp;/g, '&').replace(/&quot;/g, '"').replace(/&lt;/g, '<').replace(/&gt;/g, '>');

function states() {
  const out = [];
  const base = (q, extra) => ({ ...initialState(doc, { q }, {}), ...extra });
  for (const mk of ['ERCOT', 'NYISO']) {
    const zs = doc.markets[mk].zones;
    for (const spec of SPEC_KEYS) for (const zone of ['ALL', zs[0].id, zs[zs.length - 1].id]) {
      for (const period of ['mean', 2021, 2026]) out.push(base(1, { mk, scope3: mk, spec, zone: { ERCOT: 'ALL', NYISO: 'ALL', [mk]: zone }, period }));
      for (const year2 of [doc.markets[mk].events[0].year, 2026, 2015]) out.push(base(2, { mk, scope3: mk, spec, zone: { ERCOT: 'ALL', NYISO: 'ALL', [mk]: zone }, year2 }));
    }
    for (let day = 0; day < 4; day++) out.push(base(4, { mk, scope3: mk, day }));
  }
  for (const scope3 of ['ERCOT', 'NYISO', 'BOTH']) for (const hz of [0, 1, 2]) for (const showAll of [false, true]) out.push(base(3, { scope3, mk: scope3 === 'BOTH' ? 'ERCOT' : scope3, hz, showAll }));
  return out;
}

test('arena: no bare number in any view or rail (every digit is inside a num span, a tag or an attribute)', () => {
  let chips = 0;
  for (const st of states()) {
    const label = JSON.stringify({ q: st.view, mk: st.mk, scope3: st.scope3, spec: st.spec, zone: st.zone[st.mk], period: st.period, y: st.year2, hz: st.hz, day: st.day, all: st.showAll });
    const body = viewHTML(doc, st, numHTML), rail = railHTML(doc, st);
    for (const [name, h] of [['view', body], ['rail', rail]]) {
      const bare = stripNums(h);
      assert.doesNotMatch(bare, /\d/, `${name} ${label}: ${(bare.match(/.{0,40}\d.{0,30}/) || [''])[0]}`);
      assert.doesNotMatch(h, /undefined|NaN|\[object|Infinity/, `${name} ${label}`);
    }
    chips += (body.match(/class="chip /g) || []).length;
    assert.ok((body.match(/class="chip /g) || []).length >= 6, `view ${label} carries provenance tags`);
    assert.equal((body.match(/<h1/g) || []).length, 1, 'one title per view');
    assert.match(body, /Prices are real; the dispatch is simulated/);
  }
  assert.ok(chips > 1000);
});

test('arena: html is balanced enough to render (every opened div closes, no stray template text)', () => {
  for (const st of states().filter((_, i) => i % 7 === 0)) {
    const h = viewHTML(doc, st, numHTML);
    const open = (h.match(/<div[ >]/g) || []).length, close = (h.match(/<\/div>/g) || []).length;
    assert.equal(open, close, `div balance in view ${st.view} ${st.mk}`);
    for (const t of ['section', 'ol', 'ul', 'table']) assert.equal((h.match(new RegExp(`<${t}[ >]`, 'g')) || []).length, (h.match(new RegExp(`</${t}>`, 'g')) || []).length, `${t} balance`);
    assert.doesNotMatch(h, /\$\{|\{[A-Z_]{4,}\}/, 'no unfilled template or token');
  }
});

test('arena: the page stays inside the design system (tokens only, no shadows, class prefix ar-)', () => {
  const css = readFileSync(new URL('../story/arena.css', import.meta.url), 'utf8');
  assert.doesNotMatch(css, /#[0-9a-fA-F]{3,8}\b/, 'no hex colours: use the story.css tokens');
  assert.doesNotMatch(css, /rgba?\(|hsla?\(/, 'no typed colours');
  assert.doesNotMatch(css, /box-shadow|text-shadow|drop-shadow/, 'no shadows');
  assert.doesNotMatch(css, /@font-face|font-family|url\(/, 'no web fonts, no offsite assets');
  const sel = css.replace(/\/\*[\s\S]*?\*\//g, '').match(/[^{}]+(?=\{)/g).flatMap((s) => s.split(',')).map((s) => s.trim());
  for (const s of sel) {
    if (s.startsWith('@')) continue;
    for (const cls of s.match(/\.[A-Za-z][\w-]*/g) || []) {
      assert.ok(cls.startsWith('.ar') || ['.num', '.chip', '.cfg-seg', '.st-eyebrow', '.st-title', '.st-placeholder'].includes(cls) || /^\.(on|all|best|ceil|who|ours|lead|top|star|win|dark|partial|mean|inw|late|r\d|g|b|d|d\d|m|z|s-\w+|ibar|neg|pos|soc|res|mid|chg|dis|grid|price|bw|hb-tip|sm)$/.test(cls), `class ${cls} in "${s}" is not ar- prefixed`);
    }
  }
  const js = readFileSync(new URL('../story/arena.js', import.meta.url), 'utf8');
  assert.doesNotMatch(js, /WebGL|new Worker|fetch\(|XMLHttpRequest|import\(/i, 'no WebGL, workers or network beyond ctx.getJSON');
});
