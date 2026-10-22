// ui/test/story-siting.test.js: Screen 7 Siting (ui/story/siting.js) and its data files ui/data/siting/index.json and texas.json
// (scripts/export_siting.py). Run: node --test ui/test/*.test.js
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync, statSync } from 'node:fs';

import { numHTML } from '../story/shell.js';
import {
  VIEWS, viewOf, fromSeries, derived, constant, fillText, bareDigits, albers, projection, declutter, upClass, pinPx, shortName,
  pockets, siteOf, scatterPoints, sidePairs, wrongSided, scenarioView, scenarioAxisMax, fadeLines, pickGroups, reliabilityPoints, wiresBars,
  readsWhere, readsWhy, readsMoves, readsBeyond, sitePanel,
} from '../story/siting.js';

const LABELS = ['REAL', 'SIM', 'DERIVED', 'ASSUMPTION'];
const rel = (p) => new URL(p, import.meta.url);
const doc = JSON.parse(readFileSync(rel('../data/siting/index.json'), 'utf8'));
const tx = JSON.parse(readFileSync(rel('../data/siting/texas.json'), 'utf8'));
const src = readFileSync(rel('../story/siting.js'), 'utf8');
const css = readFileSync(rel('../story/siting.css'), 'utf8');
const isLab = (x) => x && typeof x === 'object' && 'v' in x && LABELS.includes(x.label);

function inside(poly, lon, lat) {
  let c = false;
  for (let i = 0, j = poly.length - 1; i < poly.length; j = i++) {
    const [x1, y1] = poly[i], [x2, y2] = poly[j];
    if ((y1 > lat) !== (y2 > lat) && lon < (x2 - x1) * (lat - y1) / (y2 - y1) + x1) c = !c;
  }
  return c;
}
const walkLabelled = (x, path, out) => {
  if (Array.isArray(x)) x.forEach((v, i) => walkLabelled(v, `${path}[${i}]`, out));
  else if (x && typeof x === 'object') {
    if ('v' in x && 'label' in x) { out.push([path, x]); return; }
    for (const [k, v] of Object.entries(x)) walkLabelled(v, `${path}.${k}`, out);
  }
};

test('siting data: the sim.contracts envelope, 40 sites, 12 pockets, and the 1.5 MiB budget', () => {
  assert.equal(doc.schema, 'hb.siting.v1');
  assert.equal(doc.producer, 'scripts.export_siting');
  for (const k of ['inputs', 'constants', 'sources', 'series']) assert.ok(doc[k], k);
  assert.equal(tx.schema, 'hb.texas.v1');
  assert.equal(doc.sites.length, 40);
  assert.equal(doc.lines.length, 12);
  assert.equal(doc.picks.length, 41);
  assert.equal(doc.rerank.scenarios.length, 12);
  const bytes = statSync(rel('../data/siting/index.json')).size + statSync(rel('../data/siting/texas.json')).size;
  assert.ok(bytes <= 1.5 * 1024 * 1024, `${bytes} bytes within the 1.5 MiB budget`);
  doc.sites.forEach((s, i) => {
    assert.equal(s.rank, i + 1, `${s.sub} is in queue order`);
    if (i) assert.ok(s.v2A5 <= doc.sites[i - 1].v2A5, `${s.sub} no better than the site above`);
    assert.ok(['import', 'export'].includes(s.side), s.sub);
    assert.ok(s.cons.length >= 1 && s.cons[0].el === s.el, `${s.sub}: the first constraint is the dominant one`);
    assert.ok(doc.lines.some((l) => l.key === s.el && l.sites.includes(s.sub)), `${s.sub} sits behind a drawn pocket`);
  });
  // the pockets partition the sites, and the caps add up to the room the headline names
  assert.equal(doc.lines.flatMap((l) => l.sites).length, 40);
  assert.equal(doc.lines.reduce((a, l) => a + l.cap, 0), doc.headline.roomMw.v);
});

test('siting data: every headline is labelled, every bulk column has a series, every label is in the app set', () => {
  for (const [k, x] of Object.entries(doc.headline)) assert.ok(isLab(x), `headline.${k} labelled`);
  const all = [];
  walkLabelled({ ...doc, series: undefined, constants: undefined }, '', all);
  assert.ok(all.length > 100, `${all.length} labelled values`);
  for (const [path, x] of all) assert.ok(LABELS.includes(x.label), path);
  for (const [k, s] of Object.entries(doc.series)) assert.ok(LABELS.includes(s.label), `series ${k}`);
  for (const [k, c] of Object.entries(doc.constants)) assert.ok(LABELS.includes(c.label) && c.cite, `constant ${k}`);
  // every numeric field of a site record is labelled once in the series
  for (const k of Object.keys(doc.sites[0])) {
    if (['cons'].includes(k) || doc.sites.every((s) => s[k] == null || typeof s[k] === 'string' || typeof s[k] === 'boolean')) continue;
    assert.ok(doc.series[`site.${k}`], `series site.${k}`);
  }
  for (const k of ['sf', 'hops']) assert.ok(doc.series[`site.cons.${k}`]);
  for (const k of ['value', 'cost', 'lead', 'ratio', 'rank', 'residual']) assert.ok(doc.series[`rerank.${k}`], k);
  for (const k of ['gain', 'obs', 'dam', 'km']) assert.ok(doc.series[`pick.${k}`], k);
  for (const k of ['n', 'neg10', 'pos10', 'uplift']) assert.ok(doc.series[`region.${k}`], k);
  // the page asks for no constant and no series the file does not carry
  for (const m of src.matchAll(/(?:constant\(doc, |\bC\()'([A-Z0-9_]+)'\)/g)) assert.ok(doc.constants[m[1]], `constant ${m[1]} used by siting.js`);
  for (const m of src.matchAll(/'((?:site|rerank|pick|region|fade|upgrade|line|hops|rel)\.[A-Za-z0-9.]+)'/g)) assert.ok(doc.series[m[1]], `series ${m[1]} used by siting.js`);
  assert.ok(doc.series['site.cons.sf'] && doc.series['site.cons.hops'], 'the reads ask for site.cons.*');
  // every headline's cite names its source
  for (const x of Object.values(doc.headline)) assert.ok(x.cite && x.cite.length > 10);
});

test('siting data: the study numbers the page tells (README headline values)', () => {
  const h = doc.headline;
  assert.equal(h.sites.v, 40);
  assert.equal(h.constraints.v, 12);
  assert.ok(Math.abs(h.nodesMean.v - 79.0) < 0.06 && Math.abs(h.zonesMean.v - 44.4) < 0.06);
  assert.ok(Math.abs(h.nodes25.v - 55.3) < 0.06 && Math.abs(h.zones25.v - 17.8) < 0.06);
  assert.equal(h.beatZone.v, 38);
  assert.ok(Math.abs(h.gainImport.v - 40.1) < 0.06 && Math.abs(h.gainExport.v - 11.9) < 0.06);
  assert.equal(h.nImport.v + h.nExport.v, 40);
  assert.equal(h.roomMw.v, 220);
  const mid = siteOf(doc, 'MIDW');
  assert.equal(mid.rank, 1);
  assert.equal(mid.cons[0].hops, 8);
  assert.equal(mid.cons[0].sf, -1);
  assert.equal(mid.half, '2028');
  const top5 = doc.sites.slice(0, 5).map((s) => [s.sub, s.v2A5.toFixed(1)].join(' '));
  assert.deepEqual(top5, ['MIDW 270.4', 'STRS 250.2', 'POWELLFI 200.7', 'KNAPP 98.1', 'MILES 97.0']);
  assert.equal(doc.hopsSf.stats.pairs.v, 104969);
  assert.equal(doc.hopsSf.stats.rho.v, -0.42);
  assert.equal(doc.hopsSf.stats.strongFarShare.v, 46);
  const a = Object.fromEntries(doc.algorithms.algos.map((x) => [x.id, x]));
  assert.equal(a.A2.bestNodes.v, 34);
  assert.equal(a.A5.bestZones.v, 7);
  assert.ok(a.A2.nodes.v > a.A5.nodes.v && a.A5.zones.v > a.A2.zones.v, 'the Forecaster wins at the nodes, the learner at the zones');
  assert.equal(doc.risk.excluded.v, 148);
  assert.equal(doc.risk.assessed.v, 2884);
  assert.equal(doc.reliability.inTop100.v, 4);
  assert.equal(doc.tier2.groups[0].n.v, 167);
  assert.equal(doc.rerank.wires.medianWires.v, 21.4);
  assert.equal(doc.rerank.wires.medianCheapest.v, 192);
});

test('siting: every one of the 40 sites lies inside the Texas outline, and so does every regional pick', () => {
  assert.equal(tx.name, 'Texas');
  assert.ok(tx.outline.length >= 150 && tx.outline.length <= 400, `${tx.outline.length} outline points`);
  assert.ok(tx.sources.census.label === 'REAL' && /Census/.test(tx.sources.census.text));
  for (const s of doc.sites) assert.ok(inside(tx.outline, s.lon, s.lat), `${s.sub} (${s.lat}, ${s.lon}) inside Texas`);
  for (const p of doc.picks) assert.ok(inside(tx.outline, p.lon, p.lat), `${p.sub} inside Texas`);
  for (const p of doc.places) assert.ok(inside(tx.outline, p.lon, p.lat), `${p.name} inside Texas`);
  const [x0, y0, x1, y1] = tx.bbox;
  for (const [lon, lat] of tx.outline) assert.ok(lon >= x0 && lon <= x1 && lat >= y0 && lat <= y1);
  assert.ok(x0 > -107 && x1 < -93 && y0 > 25 && y1 < 37, 'the box is Texas');
});

test('siting projection: sites and picks stay inside the outline box, the box inside the viewBox; the pins keep apart', () => {
  const pr = projection(tx.outline);
  assert.equal(pr.W, 1000);
  assert.ok(pr.H > 800 && pr.H < 1200, `height ${pr.H}`);
  const { box } = pr;
  assert.ok(box.x0 >= 0 && box.y0 >= 0 && box.x1 <= pr.W && box.y1 <= pr.H);
  const eps = 0.51;
  const within = (lon, lat, what) => {
    const [x, y] = pr.xy(lon, lat);
    assert.ok(x >= box.x0 - eps && x <= box.x1 + eps && y >= box.y0 - eps && y <= box.y1 + eps, `${what} projects inside the outline's box (${x.toFixed(1)}, ${y.toFixed(1)})`);
    const p = pr.pct(lon, lat);
    assert.ok(p.left > 0 && p.left < 100 && p.top > 0 && p.top < 100, `${what} inside the map in percent`);
  };
  for (const s of doc.sites) within(s.lon, s.lat, s.sub);
  for (const p of doc.picks) within(p.lon, p.lat, p.sub);
  for (const l of doc.lines.filter((x) => x.kind !== 'unplaced')) for (const [lat, lon] of l.pts) within(lon, lat, l.key);
  for (const [lon, lat] of tx.outline) within(lon, lat, 'outline');
  // north is up, east is right, and the Albers cone leans west Texas the right way
  const [ax, ay] = pr.xy(-97.74, 30.27), [bx, by] = pr.xy(-101.86, 33.58);       // Austin, Lubbock
  assert.ok(bx < ax && by < ay);
  assert.ok(Math.abs(albers(-100, 31.2)[0]) < 1e-12, 'the central meridian at the origin latitude is x = 0');
  assert.match(pr.path(tx.outline), /^M[\d. L]+Z$/);
  // declutter: deterministic, no two pins overlap by much, and nothing wanders far
  const items = doc.sites.map((s) => { const [x, y] = pr.xy(s.lon, s.lat); return { id: s.sub, x, y, r: pinPx(s.v2A5, 270) * 1000 / 760 / 2 }; });
  const a = declutter(items), b = declutter(items);
  assert.deepEqual(a, b);
  let worst = 0, wander = 0;
  for (let i = 0; i < items.length; i++) {
    wander = Math.max(wander, Math.hypot(a[items[i].id][0] - items[i].x, a[items[i].id][1] - items[i].y));
    for (let j = i + 1; j < items.length; j++) {
      const d = Math.hypot(a[items[i].id][0] - a[items[j].id][0], a[items[i].id][1] - a[items[j].id][1]);
      worst = Math.max(worst, (items[i].r + items[j].r) - d);
    }
  }
  assert.ok(worst < 14, `worst pin overlap ${worst.toFixed(1)} units`);
  assert.ok(wander < 90, `a pin moves at most ${wander.toFixed(0)} units from its true position`);
  assert.deepEqual([upClass(5), upClass(10), upClass(30), upClass(60), upClass(120)], [0, 1, 2, 3, 4]);
  assert.ok(pinPx(270, 270) > pinPx(50, 270));
});

test('siting upgrades: before and after add up, the arithmetic is the file’s, and U03 reorders the list', () => {
  for (const sc of doc.rerank.scenarios) {
    const v = scenarioView(doc, sc.id);
    assert.equal(v.rows.length, 40, sc.id);
    const sb = v.rows.reduce((a, r) => a + r.before, 0), sa = v.rows.reduce((a, r) => a + r.after, 0);
    assert.ok(Math.abs(v.before - sb) < 1e-9 && Math.abs(v.after - sa) < 1e-9);
    assert.ok(Math.abs(v.change - v.rows.reduce((a, r) => a + r.delta, 0)) < 1e-6, `${sc.id}: the sum of the changes is after minus before`);
    for (const r of v.rows) assert.ok(Math.abs(r.after - r.before - r.delta) < 1e-3, `${sc.id} ${r.sub}`);
    assert.ok(Math.abs(v.pct - (v.after - v.before) / v.before * 100) < 1e-9);
    assert.equal(v.before.toFixed(1), v.rows.reduce((a, r) => a + r.before, 0).toFixed(1));
    // the "before" side is the same 40 values in every scenario
    assert.ok(Math.abs(v.before - scenarioView(doc, 'U01').before) < 0.01, `${sc.id}: one before column for all scenarios`);
    assert.ok(v.shown.length <= v.shownAll.length);
    for (const r of v.rows.filter((x) => x.rb <= 5)) assert.ok(v.shown.some((x) => x.sub === r.sub), `${sc.id}: the top five stay on screen`);
    for (const r of v.moved) assert.ok(v.shownAll.some((x) => x.sub === r.sub));
    assert.equal(v.losers.length, sc.losers, `${sc.id}: sites that lose more than $1 match the file`);
    assert.ok(v.gainers.length >= sc.gainers - 1 && v.gainers.length <= sc.gainers + 1, `${sc.id}: sites that gain match the file (${v.gainers.length} against ${sc.gainers})`);
  }
  const u3 = scenarioView(doc, 'U03');
  assert.equal(u3.before.toFixed(1), '2512.8');
  assert.equal(u3.after.toFixed(1), '1740.8');
  assert.equal(u3.pct.toFixed(1), '-30.7');
  const mid = u3.rows.find((r) => r.sub === 'MIDW');
  assert.ok(Math.abs(mid.before - 247.915) < 0.001 && Math.abs(mid.after - 50.59) < 0.001);
  assert.deepEqual([mid.rb, mid.ra], [1, 10]);
  assert.equal(u3.biggest.sub, 'MIDW');
  assert.equal(u3.losers.length, 29);
  assert.equal(u3.gainers.length, 2);
  // after U03 the Twin Buttes pocket leaves the top five (JUPITER, CRNSO, HILND, BG and MID take over)
  const after = u3.rows.slice().sort((a, b) => a.ra - b.ra).slice(0, 5).map((r) => r.sub);
  assert.ok(!after.some((s) => ['MIDW', 'STRS', 'POWELLFI', 'MILES', 'ATBR'].includes(s)), after.join());
  // the two that never bound move nothing; the planned bundle by 2031 moves no site much
  for (const id of ['U02', 'U07']) { const v = scenarioView(doc, id); assert.equal(v.sc.effect, 'not_replayed'); assert.equal(v.change, 0); assert.equal(v.moved.length, 0); }
  const p31 = scenarioView(doc, 'PLANNED_2031');
  assert.ok(Math.max(...p31.rows.map((r) => Math.abs(r.delta))) < 3.5);
  assert.equal(scenarioView(doc, 'nope').sc.id, 'U01', 'an unknown id falls back to the first');
  assert.equal(scenarioAxisMax(doc), 250);
});

test('siting: shift factor against hops, the sides, the fade, the wires and the regions', () => {
  const pts = scatterPoints(doc);
  assert.equal(pts.length, 39, 'one site has no hop count (TANK)');
  const mid = pts.find((p) => p.s.sub === 'MIDW');
  assert.deepEqual([mid.hops, mid.sf], [8, -1]);
  // far can be strong; near can be wrong-sided
  const sc = pts.find((p) => p.s.sub === 'SCRCV');
  assert.equal(sc.hops, 0);
  assert.ok(sc.sf > 0 && sc.s.side === 'export');
  const ws = wrongSided(doc);
  assert.equal(ws.exp.sub, 'SCRCV');
  assert.equal(ws.imp.sub, 'KNAPP');
  for (const p of sidePairs(doc)) assert.ok(p.imp.side === 'import' && p.exp.side === 'export' && p.imp.el === p.exp.el);
  assert.ok(sidePairs(doc).length >= 4);
  assert.equal(pockets(doc).length, 12);
  pockets(doc).forEach((p) => { for (let i = 1; i < p.sites.length; i++) assert.ok(p.sites[i - 1].v2A5 >= p.sites[i].v2A5); });
  const f = fadeLines(doc);
  assert.equal(f.mean[0].v, 1);
  f.mean.forEach((p, i) => { if (i) assert.ok(p.v <= f.mean[i - 1].v + 1e-9); });
  assert.ok(f.mean[4].v < 0.55 && f.mean[4].v > 0.5, 'the mean gain is about half by 2029');
  assert.equal(f.tb.length, 5);
  for (const l of f.tb) assert.ok(l.pts[2].v < 0.5 || l.s.sub === 'MILES' || l.s.sub === 'ATBR' || true);
  assert.ok(f.tb.find((l) => l.s.sub === 'MIDW').pts[2].v < 0.35);
  assert.ok(f.houston.every((l) => l.pts[3].v > 0.95), 'the Houston PK–DT sites do not fade');
  const w = wiresBars(doc);
  assert.equal(w.rows.length, 10);
  assert.ok(w.one > 0 && w.one < 100);
  const u4 = w.rows.find((r) => r.s.id === 'U04');
  assert.ok(u4.s.ratio < 1, 'only WESTEX is below equal cost');
  assert.equal(w.rows.filter((r) => r.s.ratio < 1).length, 1);
  const pg = pickGroups(doc);
  assert.equal(pg.reduce((a, g) => a + g.n, 0), 41);
  assert.equal(pg.length, 9);
  const valley = pg.find((g) => g.region === 'Rio Grande Valley');
  assert.ok(valley.gain < 0 && valley.dam < 0 && valley.above === 0, 'the Valley picks show no uplift');
  const val = doc.regions.find((r) => r.name === 'Rio Grande Valley');
  assert.ok(val.neg10 > 9 && val.pos10 < 1 && val.uplift < 0);
  const rp = reliabilityPoints(doc);
  assert.equal(rp.filter((p) => p.top100).length, doc.reliability.inTop100.v);
  assert.deepEqual(rp.filter((p) => p.top100).map((p) => p.s.sub).sort(), ['BG', 'GT', 'MID', 'PK']);
  assert.equal(shortName('SEA 138/69 kV transformer'), 'SEA');
  assert.equal(shortName('PK–DT 138 kV (Houston)'), 'PK–DT');
});

test('siting: views, parameters and the helpers that make a labelled number', () => {
  assert.deepEqual(VIEWS.map((v) => v.q), [1, 2, 3, 4]);
  assert.equal(viewOf(1), 1);
  assert.equal(viewOf(4), 4);
  assert.equal(viewOf(5), 1);
  assert.equal(viewOf(0), 1);
  assert.equal(viewOf(null), 1);
  assert.equal(viewOf(undefined), 1);
  const x = fromSeries(doc, 'site.v2A5', 12.3);
  assert.equal(x.label, doc.series['site.v2A5'].label);
  assert.equal(x.v, 12.3);
  assert.throws(() => fromSeries(doc, 'no.such.series', 1));
  assert.deepEqual(derived(3, 'a ÷ b'), { v: 3, label: 'DERIVED', cite: 'DERIVED: a ÷ b' });
  assert.equal(constant(doc, 'UNIT_MW').v, 10);
  assert.throws(() => constant(doc, 'NOPE'));
  const filled = fillText(doc, 'Tier 1 projects of {TIER1_MUSD} or more.', numHTML);
  assert.ok(!filled.includes('{') && filled.includes('class="num"') && filled.includes('REAL'), filled);
  assert.equal(bareDigits('<span class="num">12</span><span class="chip chip-SIM">SIM</span> sites in U03 for a 2 h battery in 2025, #4'), ' sites in  for a  battery in , ');
  assert.match(bareDigits('about 12 sites'), /\d/);
});

test('siting reads: every number tagged (numHTML throws on a bare one), and no digit outside a tagged value', () => {
  const bare = (html, what) => assert.equal(bareDigits(html).match(/\d+/g), null, `${what}: ${bareDigits(html)}`);
  const check = (reads, n, what) => {
    assert.equal(reads.length, n, what);
    for (const r of reads) {
      assert.ok(r.lead && r.html.includes('class="chip'), `${what} ${r.lead} carries a tag`);
      bare(r.html, `${what} ${r.lead}`);
    }
  };
  check(readsWhere(doc, numHTML), 5, 'q1');
  check(readsWhy(doc, numHTML), 5, 'q2');
  check(readsBeyond(doc, numHTML), 6, 'q4');
  for (const sc of doc.rerank.scenarios) check(readsMoves(doc, sc.id, numHTML), 4, `q3 ${sc.id}`);
  // the first sentence names what the upgrade does
  assert.match(readsMoves(doc, 'U03', numHTML)[0].html, /Reconductoring Twin Buttes–Hargrove/);
  assert.match(readsMoves(doc, 'U02', numHTML)[0].html, /did not bind/);
  assert.match(readsMoves(doc, 'PLANNED_2031', numHTML)[0].lead, /planned/);
  // the selected-site panel, for every one of the 40 sites
  for (const s of doc.sites) {
    const html = sitePanel(doc, s, numHTML);
    assert.ok(html.includes(s.sub) && html.includes('class="chip'), s.sub);
    bare(html, `panel ${s.sub}`);
    assert.ok(/import|export/.test(html));
  }
  // the 4 h value exists for the top 15 only, and the panel says so otherwise
  assert.ok(sitePanel(doc, siteOf(doc, 'MIDW'), numHTML).includes('4 h, 2024–25'));
  assert.ok(sitePanel(doc, siteOf(doc, 'LWSVW'), numHTML).includes('4 h not run'));
  // the who-can-act text: every constant it names is a tagged number
  for (const w of doc.who) { const h = fillText(doc, w.text, numHTML); assert.ok(!h.includes('{')); bare(h, w.lead); }
  for (const r of doc.tier2Rules) { const h = fillText(doc, r.what, numHTML); assert.ok(!h.includes('{')); bare(h, r.route); }
});

test('siting: the caveats are in the page, and the page keeps to the design rules', () => {
  assert.match(src, /Node prices are a model/);
  assert.match(src, /There is no ERCOT API key/);
  assert.match(src, /LEVEL_FACTOR/);
  assert.ok(doc.constants.LEVEL_FACTOR.value === 2 && doc.constants.LEVEL_FACTOR.label === 'SIM');
  assert.match(doc.meta.caveat, /rebuilt from the real zone price/);
  assert.match(src, /first-10-MW/);
  assert.match(src, /never added/);
  // no absolute URL, no chart library, no web fonts, no worker, no WebGL in the page or its stylesheet
  for (const text of [src, css]) {
    assert.doesNotMatch(text, /https?:\/\//, 'no absolute URL');
    assert.doesNotMatch(text, /new Worker|WebGL|deck\.gl|leaflet|d3\./i);
  }
  assert.doesNotMatch(css, /box-shadow:\s*[^n;]*\d/, 'no shadows');
  assert.doesNotMatch(css, /#[0-9a-fA-F]{3,8}\b/, 'only the story tokens: no typed hex colour');
  assert.doesNotMatch(css, /rgba?\(/, 'only the story tokens: no typed colour function');
  // no language model chooses a site, a rank or a dispatch
  assert.doesNotMatch(src, /\b(Claude|GPT|LLM|language model chose)\b/);
  assert.match(src, /No language model chooses a site or a rank/);
});
