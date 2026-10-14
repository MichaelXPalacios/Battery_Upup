// ui/test/story-markets.test.js: Screen 5 Markets (ui/story/markets.js) and its data file ui/data/markets/index.json
// (scripts/export_markets.py). Run: node --test ui/test/*.test.js
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

import { numHTML } from '../story/shell.js';
import { deskChart, deskReads, stripLayout, bookOf, viewOf, fromSeries, niceMax, shortDate, VIEWS, MARKETS } from '../story/markets.js';

const LABELS = ['REAL', 'SIM', 'DERIVED', 'ASSUMPTION'];
const doc = JSON.parse(readFileSync(new URL('../data/markets/index.json', import.meta.url), 'utf8'));
const isLab = (x) => x && typeof x === 'object' && 'v' in x && LABELS.includes(x.label);

test('markets data: the sim.contracts envelope, both markets, every zone and weather region complete', () => {
  assert.equal(doc.schema, 'hb.markets.v1');
  assert.equal(doc.producer, 'scripts.export_markets');
  for (const k of ['inputs', 'constants', 'sources', 'series']) assert.ok(doc[k], k);
  assert.deepEqual(Object.keys(doc.markets), MARKETS.map(([id]) => id));
  assert.equal(doc.markets.ERCOT.zones.length, 8);
  assert.equal(doc.markets.NYISO.zones.length, 11);
  for (const [mk, M] of Object.entries(doc.markets)) {
    const nh = M.histYears.length, nf = M.fcYears.length;
    assert.equal(M.fcYears[0], 2027, mk);
    for (const z of M.zones) {
      for (const [k, a] of Object.entries(z.hist)) assert.equal(a.length, nh, `${mk} ${z.id} hist.${k}`);
      for (const [k, a] of Object.entries(z.fc)) assert.equal(a.length, nf, `${mk} ${z.id} fc.${k}`);
      assert.equal(z.partial.length, nh);
      assert.equal(z.season.realised.length, 12);
      assert.equal(z.season.forecast.length, 12);
      z.fc.p10.forEach((v, i) => assert.ok(v <= z.fc.p50[i] && z.fc.p50[i] <= z.fc.p90[i], `${z.id} ${M.fcYears[i]} P10 <= P50 <= P90`));
      z.hist.realised.forEach((v, i) => assert.ok(v <= z.hist.ceiling[i] + 0.11, `${z.id} ${M.histYears[i]}: realised never beats the ceiling`));
      for (const [k, x] of Object.entries(z.headline)) assert.ok(isLab(x), `${z.id} headline.${k} labelled`);
      for (const [k, x] of Object.entries(z.factors)) assert.ok(isLab(x), `${z.id} factors.${k} labelled`);
    }
    // the weather split covers every zone exactly once
    const covered = M.weather.flatMap((w) => w.zones).sort();
    assert.deepEqual(covered, M.zones.map((z) => z.id).sort(), `${mk} weather regions cover each zone once`);
    for (const w of M.weather) for (const x of Object.values(w.headline)) assert.ok(isLab(x));
  }
  for (const s of doc.stacks) for (const k of ['energy', 'capacity', 'program', 'event', 'upgrades', 'total']) assert.ok(isLab(s[k]), `${s.site} ${k}`);
  for (const s of doc.stacks) {
    const sum = s.energy.v + s.capacity.v + s.program.v + s.event.v + s.upgrades.v;
    assert.ok(Math.abs(sum - s.total.v) < 0.25, `${s.site}: the stack adds up (${sum} vs ${s.total.v})`);
  }
});

test('markets: bulk values take their label from the file series', () => {
  const x = fromSeries(doc, 'hist.realised', 12.3);
  assert.equal(x.label, doc.series['hist.realised'].label);
  assert.equal(x.v, 12.3);
  assert.throws(() => fromSeries(doc, 'no.such.series', 1));
});

test('markets desk chart: one slot per year, forecast after the divider, scale above every value', () => {
  const M = doc.markets.ERCOT, z = M.zones[0];
  const g = deskChart(z, M.histYears, M.fcYears);
  assert.equal(g.years.length, M.fcYears[M.fcYears.length - 1] - M.histYears[0] + 1);
  assert.equal(g.dots.length, M.fcYears.length);
  assert.ok(Math.abs(g.divX - (M.fcYears[0] - M.histYears[0]) * g.sw) < 1e-9);
  const top = Math.max(...z.hist.ceiling, ...z.hist.realised, ...z.fc.p90);
  assert.ok(g.ymax >= top, `ymax ${g.ymax} >= ${top}`);
  assert.equal(g.ticks.length, 5);
  for (const d of g.dots) { assert.ok(d.left > g.divX / g.W * 100); assert.ok(d.top >= 0 && d.top <= 100); }
  assert.match(g.band, /^M[\d. L]+Z$/);
  assert.equal(niceMax(17), 20);
  assert.equal(niceMax(161), 200);
});

test('markets reads: six sentences per zone, every number tagged (numHTML throws on a bare one)', () => {
  for (const [mk, M] of Object.entries(doc.markets)) {
    for (const z of M.zones) {
      const reads = deskReads(doc, mk, z, numHTML);
      assert.equal(reads.length, 6);
      for (const r of reads) {
        assert.ok(r.lead && r.html.includes('class="chip'), `${z.id} ${r.lead} carries a tag`);
        // no digits outside a tagged value, apart from the shocks named in words (+5°F, −10°F, +50%, 10 best days, years)
        const bare = r.html.replace(/<span class="num">[^<]*<\/span>/g, '').replace(/<span class="chip[^>]*>[^<]*<\/span>/g, '')
          .replace(/\+5°F|−10°F|\+50%|10 best days|2023|2025|20(?=\/MWh)/g, '');
        assert.doesNotMatch(bare.replace(/<[^>]+>/g, ''), /\d/, `${z.id} ${r.lead}: ${bare}`);
      }
    }
  }
});

test('markets weather strips: sorted by mean ÷ σ, the axis spans every region', () => {
  for (const M of Object.values(doc.markets)) {
    const s = stripLayout(M.weather);
    assert.equal(s.rows.length, M.weather.length);
    for (let i = 1; i < s.rows.length; i++) assert.ok(s.rows[i - 1].ratio >= s.rows[i].ratio);
    for (const r of s.rows) { assert.ok(r.lo >= 0 && r.lo + r.span <= 100.0001); assert.ok(r.mid >= r.lo && r.mid <= r.lo + r.span); }
    assert.ok(s.ticks[0].v <= Math.min(...M.weather.map((w) => w.headline.p10.v)));
  }
});

test('markets: views, book words and dates', () => {
  assert.deepEqual(VIEWS.map((v) => v.q), [1, 2, 3]);
  assert.equal(viewOf(2), 2);
  assert.equal(viewOf(4), 1);
  assert.equal(viewOf(null), 1);
  const f = (h, c, g) => ({ summer_shock_spread_pct: { v: h }, winter_shock_spread_pct: { v: c }, gas50_spread_pct: { v: g } });
  assert.equal(bookOf(f(128, 56, 12)), 'A heat book');
  assert.equal(bookOf(f(30, 10, 25)), 'A gas book');
  assert.equal(shortDate('2026-09-26'), '26 Sep 2026');
});
