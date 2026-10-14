// ui/test/story-run.test.js (UI-A): the pure half of Running and Run (ui/story/run.js) and the shell's labelled numbers
// (ui/story/shell.js). Run: node --test ui/test/*.test.js
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import zlib from 'node:zlib';

import { SPEEDS, DEFAULT_SPEED, focusLabels, runFiles, attackPath, kindWord, KIND, buildSeries, covertFailures, covertMoments,
  detectorModel, momentAt, runCostHTML, tierNames, tierConsts, tierBands, laneRange, signedMW, reserveHTML } from '../story/run.js';
import { numHTML, tagHTML, leverSummary, STEPS } from '../story/shell.js';
import { LabelError } from '../lib/format.js';

const UI = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const REPO = path.resolve(UI, '..');
const readJSON = (p) => JSON.parse(fs.readFileSync(p, 'utf8'));

test('run: the six speeds of ruling 5, default 0.25x', () => {
  assert.deepEqual(SPEEDS, [0.1, 0.25, 0.5, 1, 2, 4]);
  assert.equal(DEFAULT_SPEED, 0.25);
});

test('run: the files a scenario reads; covert adds its detector file (catalogue `attack`)', () => {
  const base = { id: '2026-08-23/aware', levers: { failure: 'none' }, meta: 'p1/meta.json', branch: 'p1/aware.json', extras: 'p1/extras/2026-08-23_aware.json.gz' };
  assert.deepEqual(runFiles(base).map((f) => f.path), ['topology.json', 'footprints.json', 'p1/meta.json', 'p1/aware.json', 'p1/extras/2026-08-23_aware.json.gz']);
  assert.equal(attackPath(base), null);
  const cov = { ...base, levers: { failure: 'covert' }, attack: 'p3/covert.json' };
  assert.equal(attackPath(cov), 'p3/covert.json');
  assert.equal(runFiles(cov).at(-1).path, 'p3/covert.json');
  assert.equal(attackPath({ ...cov, attack: undefined, covert: '../../resilience/out/p3/covert.json' }), '../../resilience/out/p3/covert.json');
  // no path typed in the page: a covert scenario without one is a missing file on Running, not a guessed path
  assert.equal(attackPath({ ...cov, attack: undefined }), null);
  assert.equal(runFiles({ ...cov, attack: undefined }).at(-1).missing, true);
  assert.equal(runFiles({ ...base, extras: null }).length, 4);
});

test('run: every failure kind the engine writes has display words', () => {
  for (const k of ['comms_lost', 'hot', 'stall', 'stale', 'normal', 'emergency', 'protection', 'worker_kill', 'covert']) assert.ok(KIND[k], k);
  assert.equal(kindWord('protection'), 'Protection open');
  assert.equal(kindWord('new_kind'), 'new kind');
});

test('run: tier thresholds come from the TIER_* constants (extras first, then meta, then meta.tiers), never typed', () => {
  const T = tierConsts(meta);
  assert.deepEqual([T.amber.v, T.normal.v, T.normalMin.v, T.emergency.v],
    [meta.constants.TIER_AMBER_PCT.value, meta.constants.TIER_NORMAL_PCT.value, meta.constants.TIER_NORMAL_MIN.value, meta.constants.TIER_EMERGENCY_PCT.value]);
  assert.equal(T.normalMin.label, meta.constants.TIER_NORMAL_MIN.label);
  const X = tierConsts(meta, { constants: { TIER_EMERGENCY_PCT: { value: 160, label: 'ASSUMPTION', cite: 'x' } } });
  assert.equal(X.emergency.v, 160);
  const bare = tierConsts({});
  assert.deepEqual(bare, { amber: null, normal: null, normalMin: null, emergency: null });
  const names = tierNames(bare);
  assert.equal(names.length, 6);
  assert.ok(names.every((x) => !/\d/.test(x)), 'no number without data');
  assert.match(tierNames(T)[3], new RegExp(`${meta.constants.TIER_NORMAL_MIN.value}\\+ min`));
});

test('run: ONE definition of the transformer counts: exclusive bands, the same text for the hero and Right now', () => {
  const T = tierConsts(meta);
  const B = tierBands([10, 6, 5, 3, 1], 379, T);
  assert.deepEqual(B.bands.map((b) => [b.key, b.n]), [['over', 10], ['above', 11], ['emergency', 3], ['open', 1]]);
  assert.equal(B.within, 379 - 25);
  assert.equal(B.text, '100%–110% 10 · 110%–150% 11 (5 past 30 min) · above 150% 3 · protection open 1');
  assert.equal(B.bands.reduce((s2, b) => s2 + b.n, 0) + B.within, 379, 'the bands and within add up: nothing counted twice');
  assert.doesNotMatch(tierBands([1, 1, 1, 1, 1], 10, tierConsts({})).text, /\d+%/);
});

test('run: lane ranges come from the data (the worst peaks are never flattened)', () => {
  assert.deepEqual(laneRange([60, 215.5], [110, 150]), [60, 230]);
  assert.ok(laneRange([60, 215.5], [110, 150])[1] > 215.5);
  assert.deepEqual(laneRange([20, 98], [100], { floor: 0 }), [0, 110]);
  assert.deepEqual(laneRange([], []), [0, 1]);
});

test('run: the fleet power is signed, never a threshold word', () => {
  assert.equal(signedMW(593.6), '+0.59');
  assert.equal(signedMW(-3840), '−3.84');
  assert.equal(signedMW(0), '0.00');
  assert.equal(signedMW(2), '+0.00');
});

test('run: the reserve note: the floor, "never breached by dispatch" and the backup use in outages, labels from the data', () => {
  const reserveC = { value: 0.2, label: 'REAL', cite: 'floor' };
  const summary = { reserveBreaches: { v: 0, label: 'SIM', cite: 'b' }, reserveUsedInOutage: { v: 1948, label: 'SIM', cite: 'o' } };
  const h = reserveHTML({ reserveC, summary, num: numHTML });
  assert.match(h, /20%<\/span><span class="chip chip-REAL" title="floor">REAL/);
  assert.match(h, /never breached by dispatch <span class="chip chip-SIM" title="b">SIM/);
  assert.match(h, /used for home backup during outages: <span class="num">1,948<\/span><span class="chip chip-SIM" title="o">SIM<\/span> battery-minutes/);
  assert.doesNotMatch(h, /never used/);
  assert.doesNotMatch(reserveHTML({ reserveC, summary: { ...summary, reserveUsedInOutage: { v: 0, label: 'SIM' } }, num: numHTML }), /outages/);
  assert.match(reserveHTML({ reserveC, summary: { reserveBreaches: { v: 3, label: 'SIM' } }, num: numHTML }), /breached by dispatch <span class="num">3/);
  assert.equal(reserveHTML({ reserveC: null, summary, num: numHTML }), '');
  // worker_kill's file has no reserveUsedInOutage: the clause is omitted, never shown as 0
  const wk = readJSON(path.join(UI, 'data', 'p1', 'worker_kill.json'));
  const hw = reserveHTML({ reserveC, summary: wk.summary, num: numHTML });
  assert.match(hw, /never breached by dispatch/);
  if (!('reserveUsedInOutage' in wk.summary)) assert.doesNotMatch(hw, /outage/);
});

test('run: the engine cost line is the catalogue\'s labelled numbers; no bare number passes', () => {
  const sc = { engine: { buildSeconds: { v: 34.8, label: 'DERIVED', cite: 'sim.scenarios' }, solves: { v: 721, label: 'SIM' } } };
  const html = runCostHTML(sc, numHTML);
  assert.match(html, /721<\/span><span class="chip chip-SIM"/);
  assert.match(html, /34\.8 s<\/span><span class="chip chip-DERIVED" title="sim.scenarios"/);
  assert.match(html, /nothing is solved in the browser/);
  assert.match(runCostHTML({}, numHTML), /no measured engine cost/);
  const cov = runCostHTML({ ...sc, producer: 'resilience.detect', attackEngine: { buildSeconds: { v: 110, label: 'DERIVED' } } }, numHTML);
  assert.match(cov, /detector replay \(resilience\.detect\) took <span class="num">110 s/);
  assert.match(cov, /^The engine already ran this evening: /, 'the feeder run is not the detector run');
  assert.match(runCostHTML({ ...sc, producer: 'resilience.runtime' }, numHTML), /^The engine already ran this evening \(resilience\.runtime\): /);
  assert.throws(() => runCostHTML({ engine: { solves: 721 } }, numHTML), LabelError);
});

test('shell: numHTML tags every value; SCREENING is its own dashed tag; unknown labels throw', () => {
  assert.equal(numHTML({ v: 197.04, label: 'SIM' }, { unit: '%' }), '<span class="num">197.0%</span><span class="chip chip-SIM">SIM</span>');
  const s = numHTML({ v: 1007, label: 'SIM', cite: 'x' }, { screening: true });
  assert.match(s, /chip-SIM" title="x">SIM/);
  assert.match(s, /chip-SCREENING" title="Screening estimate, not checked by OpenDSS\. x">SCREENING/);
  assert.match(numHTML({ v: 3, label: 'UNVERIFIED' }), /chip-UNVERIFIED/);
  assert.throws(() => numHTML(3), LabelError);
  assert.throws(() => numHTML({ v: 3, label: 'GUESS' }), LabelError);
  assert.throws(() => tagHTML('GUESS'), LabelError);
  assert.deepEqual(STEPS.map((x) => x[1]), ['1 Configure', '2 Run', '3 Results', '4 Learnings', '5 Markets', '6 Arena', '7 Siting', '8 Dispatch']);
});

test('shell: the run pill summary uses the catalogue labels, cut at ": " or " ("', () => {
  const cat = { levers: {
    policy: { options: [{ id: 'aware', label: 'Feeder-aware' }, { id: 'none', label: 'No batteries' }] },
    failure: { options: [{ id: 'worker_kill', label: 'Controller crash: a worker is killed and its lease taken over' }] },
    fleet: { options: [{ id: 96, label: '96 batteries' }] }, cls: { options: [{ id: 'core', label: 'Core (20 kW, 37 kWh)' }] },
    reserve: { options: [{ id: 20, label: '20% reserve' }] }, soc0: { options: [{ id: 90, label: '90% at 16:00' }] },
    growth: { default: 0, options: [{ id: 0, label: "Today's load" }, { id: 20, label: '+20% home load' }] } } };
  const L = { policy: 'aware', failure: 'worker_kill', fleet: 96, cls: 'core', reserve: 20, soc0: 90, growth: 0 };
  assert.equal(leverSummary(cat, L), 'Feeder-aware · Controller crash · 96 batteries · Core · 20% reserve · 90% at 16:00');
  assert.equal(leverSummary(cat, { ...L, policy: 'none', failure: 'none', growth: 20 }), 'No batteries · +20% home load');
});

// ---- the committed data ----
const meta = readJSON(path.join(UI, 'data', 'p1', 'meta.json'));
const topology = readJSON(path.join(UI, 'data', 'topology.json'));

test('run: series from the committed aware_faults branch: steps, transformers and fleet read from the data', () => {
  const doc = readJSON(path.join(UI, 'data', 'p1', 'aware_faults.json'));
  const S = buildSeries(doc, meta, topology);
  assert.equal(S.n, meta.steps);
  assert.equal(S.tfN, topology.transformers.length);
  assert.equal(S.fleetN, topology.fleet.length);
  assert.equal(S.focus.map((f) => f.name).join(','), 'A,B,C,D,T-240');
  for (let k = 0; k < S.n; k += 97) {
    assert.equal(S.worst[k], Math.max(...doc.loading[k]) / 10);
    assert.ok(S.soc[k] > 0 && S.soc[k] <= 100);
  }
  // extras' worst series wins when it has the run's length
  const x = { worstPct: doc.loading.map(() => 1234), worstTf: doc.loading.map(() => 7) };
  const SX = buildSeries(doc, meta, topology, x);
  assert.equal(SX.worst[5], 123.4);
  assert.equal(SX.wtf[5], 7);
});

test('run: every committed extras file carries the rule log and the failures the page shows (no fallback re-derives them)', () => {
  const cat = readJSON(path.join(UI, 'data', 'story', 'index.json'));
  const seen = new Set();
  for (const sc of cat.scenarios) {
    assert.ok(sc.extras, `${sc.id} names its extras`);
    if (seen.has(sc.extras)) continue;
    seen.add(sc.extras);
    const x = JSON.parse(zlib.gunzipSync(fs.readFileSync(path.join(UI, 'data', sc.extras))));
    assert.ok(Array.isArray(x.moments) && Array.isArray(x.failures), sc.extras);
    for (const f of x.failures) { assert.ok(f.k0 <= f.k1, `${sc.extras} ${JSON.stringify(f)}`); assert.ok(kindWord(f.kind)); }
  }
  const m = [{ k: 3, text: 'a' }, { k: 9, text: 'b' }];
  assert.equal(momentAt(m, 2), null);
  assert.equal(momentAt(m, 5).text, 'a');
  assert.equal(momentAt(m, 99).text, 'b');
});

test('run: the covert detector card counts flagged and quarantined units from p3/covert.json', () => {
  const cov = readJSON(path.join(REPO, 'resilience', 'out', 'p3', 'covert.json'));
  const n = cov.window.steps;
  const d = detectorModel(cov, n);
  assert.equal(d.units.length, cov.attack.shard.length);
  assert.equal(d.flaggedAt(cov.attack.step - 1), 0);
  assert.equal(d.flaggedAt(n - 1), cov.units.filter((u) => u.flaggedStep !== null).length);
  assert.equal(d.flaggedAt(n - 1), cov.summary.detected.v);
  assert.equal(d.quarantinedAt(n - 1), cov.quarantine.log.length);
  const first = Math.min(...cov.units.map((u) => u.flaggedStep).filter((x) => x !== null));
  assert.equal((first - cov.attack.step + 1) * 60, cov.summary.detectionSeconds.v, 'the minute it is flagged counts');
  assert.ok(d.w0 <= cov.attack.step && d.w1 >= Math.max(...cov.quarantine.log.map((q) => q[0])));
  assert.equal(d.x(d.w0 - 1), null);
  assert.equal(d.x(d.w0), '0.0');
  assert.match(d.flagPath, /^M0,42\.0.*H300$/);
  const cf = covertFailures(cov, n);
  assert.equal(cf.length, 1);
  assert.equal(cf[0].k0, cov.attack.step);
  // the file's own text, its ASCII "+-" shown as "±" (review-0927 S7)
  assert.equal(cf[0].text, cov.attack.text.replace('+-', '±'));
  assert.equal(covertMoments(cov)[0].text, cov.attack.text.replace('+-', '±'));
  assert.deepEqual(covertFailures(null, n), []);
});

test('shell: vsDefault rows follow the catalogue headline order, labelled from the runs\' own summaries', async () => {
  const { vsDefaultRows, vsDefaultText, vsDefaultHTML } = await import('../story/shell.js');
  const sum = (v, label = 'SIM') => ({ v, label, cite: 'c' });
  const cat = {
    headline: ['batteryCausedNormal', 'energyValueUSD', 'maxLoading'],
    scenarios: [
      { id: 'd/naive', title: 'Naive', summary: { batteryCausedNormal: sum(11), energyValueUSD: sum(893.83, 'DERIVED'), maxLoading: sum(201.2) } },
      { id: 'd/naive/fleet=192', title: 'Naive, 192', summary: { batteryCausedNormal: sum(15), energyValueUSD: sum(1789.11, 'DERIVED'), maxLoading: sum(201.3), bare: 3 },
        vsDefault: { energyValueUSD: { v: 1789.11, ref: 893.83, refId: 'd/naive' }, batteryCausedNormal: { v: 15, ref: 11, refId: 'd/naive' }, bare: { v: 3, ref: 1, refId: 'd/naive' } } },
    ],
  };
  const rows = vsDefaultRows(cat, cat.scenarios[1]);
  assert.deepEqual(rows.map((r) => r.key), ['batteryCausedNormal', 'energyValueUSD'], 'headline order; a value without a label is dropped');
  assert.deepEqual(rows[1].now, { v: 1789.11, label: 'DERIVED', cite: 'c' });
  assert.equal(rows[1].refTitle, 'Naive');
  assert.match(vsDefaultText(rows), /fleet gross energy value, not Base's profit \$1,789\.11 \(Naive: \$893\.83\)/);
  assert.match(vsDefaultHTML(rows), /vs <span class="num">\$893\.83<\/span><span class="chip chip-DERIVED"/, 'the reference value is tagged');
  assert.match(vsDefaultHTML(rows, { max: 1 }), /\+1 more/);
  assert.deepEqual(vsDefaultRows(cat, cat.scenarios[0]), []);
  assert.deepEqual(vsDefaultRows(cat, { id: 'x', vsDefault: {} }), []);
  // the catalogue's vsDefaultRef and per-entry label win
  const { vsDefaultInfo } = await import('../story/shell.js');
  const s3 = { id: 'e/naive', title: 'E', summary: { batteryCausedNormal: sum(8) }, vsDefaultRef: 'd/naive', vsDefault: { batteryCausedNormal: { v: 8, ref: 11, label: 'SIM' } } };
  const info = vsDefaultInfo({ ...cat, scenarios: [...cat.scenarios, s3] }, s3);
  assert.equal(info.refId, 'd/naive');
  assert.equal(info.refTitle, 'Naive');
  assert.deepEqual(info.rows.map((r) => [r.now.v, r.now.label, r.ref.v, r.ref.label]), [[8, 'SIM', 11, 'SIM']]);
  const moved = vsDefaultInfo(cat, { id: 'z', vsDefaultRef: 'd/naive', vsDefault: {} });
  assert.equal(moved.refTitle, 'Naive', 'nothing moved, but the reference is still named');
  assert.deepEqual(moved.rows, []);
  assert.equal(vsDefaultInfo(cat, { id: 'd/naive', vsDefault: {} }), null, 'the default run has no reference');
});

test('run: the named places come from topology (focus keys, bridge tf), or are left out', () => {
  assert.deepEqual(focusLabels(topology), { streetsLabel: `Street ${topology.focus[0].key}–${topology.focus.at(-1).key}`, bridgeLabel: `T-${topology.bridge[0].tf}` });
  assert.deepEqual(focusLabels({ focus: [{ key: 'Q' }] }), { streetsLabel: 'Street Q', bridgeLabel: null });
  assert.deepEqual(focusLabels({}), { streetsLabel: null, bridgeLabel: null });
});

test("shell: a time-named headline key takes its run's end time, or 'the end of the run'", async () => {
  const { vsDefaultRows } = await import('../story/shell.js');
  const sum = (v) => ({ v, label: 'SIM', cite: 'c' });
  const cat = { headline: ['chargedPctBy0400'], scenarios: [{ id: 'd', summary: { chargedPctBy0400: sum(100) } },
    { id: 'x', summary: { chargedPctBy0400: sum(99.2) }, vsDefault: { chargedPctBy0400: { v: 99.2, ref: 100, refId: 'd' } } }] };
  assert.equal(vsDefaultRows(cat, cat.scenarios[1], { end: '04:00' })[0].words, 'fleet charged by 04:00');
  assert.equal(vsDefaultRows(cat, cat.scenarios[1])[0].words, 'fleet charged by the end of the run');
});

test('run: a silent battery says what it does next, REAL, with our stale/expiry timings from the meta (review-0927 M7)', async () => {
  const { commsLossLine } = await import('../story/run.js');
  const { COMMS_LOSS_CITE } = await import('../panels/more.js');
  const meta = readJSON(path.join(UI, 'data', 'p1', 'meta.json'));
  const c = meta.constants;
  const h = commsLossLine(c, numHTML);
  assert.match(h, /idles in backup-only mode: it never discharges to the grid and only backs up its own home <span class="chip chip-REAL"/);
  assert.ok(h.includes(`title="${COMMS_LOSS_CITE.replace(/'/g, '&#39;')}"`));
  // the timings are the meta's own numbers and label (ASSUMPTION), never typed here
  assert.ok(h.includes(`stale after ${c.COMMS_STALE_S.value} s, expires at ${c.COMMAND_TTL_S.value} s <span class="chip chip-${c.COMMS_STALE_S.label}"`));
  assert.equal(c.COMMS_STALE_S.label, 'ASSUMPTION');
  // without the constants: the REAL behaviour only, no number
  assert.doesNotMatch(commsLossLine({}, numHTML), /\d s/);
  // the faults run's extras has a comms_lost failure (the Right-now card draws the line for it)
  const ex = JSON.parse(zlib.gunzipSync(fs.readFileSync(path.join(UI, 'data', 'p1', 'extras', '2026-08-23_aware_faults.json.gz'))));
  assert.ok(ex.failures.some((f) => f.kind === 'comms_lost'));
});

test('run: the covert text shows "±", not "+-" (review-0927 S7)', async () => {
  const { plusMinus } = await import('../story/run.js');
  assert.equal(plusMinus('a hidden +-350 W carrier'), 'a hidden ±350 W carrier');
  assert.equal(plusMinus('x + -3'), 'x + -3');
  const cov = readJSON(path.join(UI, 'data', 'p3', 'covert.json'));
  for (const t of [covertMoments(cov)[0].text, covertFailures(cov, 720)[0].text]) assert.doesNotMatch(t, /\+-/);
});
