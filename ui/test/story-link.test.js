// ui/test/story-link.test.js (UI-A): the story app's URL scheme (docs/story-contract.md "URL scheme"):
// ui/index.html?page=configure|running|run|results|learnings|markets&s=<scenario id>&k=<step>&speed=<x>&q=<1-4>&tf=<index>&n=<0-50>
// Run: node --test ui/test/*.test.js
import test from 'node:test';
import assert from 'node:assert/strict';

import { parseStoryLink, storyLinkQuery, STORY_PAGES, STORY_DEFAULT_PAGE, STORY_DEFAULT_SPEED, STORY_CATALOGUE } from '../lib/data.js';

test('story link: the bare link is Configure with every key at its default (null)', () => {
  const d = parseStoryLink('');
  assert.equal(d.page, STORY_DEFAULT_PAGE);
  assert.equal(d.page, 'configure');
  for (const k of ['s', 'k', 'speed', 'q', 'tf', 'n', 'cat']) assert.equal(d[k], null, k);
  assert.equal(d.nowebgl, false);
  assert.deepEqual(STORY_PAGES, ['configure', 'running', 'run', 'results', 'learnings', 'markets']);
  assert.equal(STORY_DEFAULT_SPEED, 0.25);
  assert.equal(STORY_CATALOGUE, 'story/index.json');
});

test('story link: every scenario id shape of the contract parses, readable in the URL', () => {
  for (const s of ['2026-08-23/aware', '2026-08-23/none', '2026-08-23/aware/faults', '2026-08-23/aware/worker_kill',
    '2026-08-23/aware/covert', '2026-08-23/naive/fleet=192', '2026-08-23/aware/cls=legacy', '2026-08-23/none/growth=20',
    '2026-08-23/aware/soc0=100', '2026-07-22/naive']) {
    const q = storyLinkQuery({ page: 'run', s });
    assert.ok(q.includes(`s=${s}`), `${q} keeps ${s} readable`);
    assert.equal(parseStoryLink(q).s, s);
  }
});

test('story link: malformed values fall back to null, never through', () => {
  const bad = parseStoryLink('?page=admin&s=../../etc/passwd&k=-3&speed=3&q=9&tf=x&n=51&cat=https://evil/x.json&nowebgl=yes');
  assert.equal(bad.page, 'configure');
  for (const k of ['s', 'k', 'speed', 'q', 'tf', 'n', 'cat']) assert.equal(bad[k], null, k);
  assert.equal(bad.nowebgl, false);
  assert.equal(parseStoryLink('?s=2026-08-23/aware/fleet=192;alert(1)').s, null);
  assert.equal(parseStoryLink('?cat=../story/index.json').cat, null);
  assert.equal(parseStoryLink('?cat=story/test-catalogue.json').cat, 'story/test-catalogue.json');
  assert.equal(parseStoryLink('?q=0').q, null);
  assert.equal(parseStoryLink('?n=50').n, 50);
});

test('story link: speeds are the six of ruling 5; the default 0.25x is omitted when written', () => {
  for (const sp of [0.1, 0.25, 0.5, 1, 2, 4]) assert.equal(parseStoryLink(`?speed=${sp}`).speed, sp);
  assert.equal(parseStoryLink('?speed=0.3').speed, null);
  assert.equal(storyLinkQuery({ page: 'run', speed: 0.25 }), '?page=run');
  assert.equal(storyLinkQuery({ page: 'run', speed: 2 }), '?page=run&speed=2');
});

test('story link: defaults are omitted (page=configure, k=0, the catalogue default s, the default catalogue)', () => {
  assert.equal(storyLinkQuery({ page: 'configure' }), '?');
  assert.equal(storyLinkQuery({ page: 'run', s: '2026-08-23/aware', k: 0 }, { s: '2026-08-23/aware' }), '?page=run');
  assert.equal(storyLinkQuery({ page: 'run', s: '2026-08-23/naive', k: 0 }, { s: '2026-08-23/aware' }), '?page=run&s=2026-08-23/naive');
  assert.equal(storyLinkQuery({ page: 'results', cat: 'story/index.json' }), '?page=results');
  assert.equal(storyLinkQuery({ page: 'results', cat: 'story/test-catalogue.json' }), '?page=results&cat=story/test-catalogue.json');
});

test('story link: a full link round-trips', () => {
  const full = { page: 'learnings', s: '2026-08-23/naive/fleet=192', k: 360, speed: 0.5, q: 3, tf: 150, n: 5, cat: 'story/test-catalogue.json', nowebgl: true };
  const q = storyLinkQuery(full);
  assert.equal(q, '?page=learnings&s=2026-08-23/naive/fleet=192&k=360&speed=0.5&q=3&tf=150&n=5&cat=story/test-catalogue.json&nowebgl=1');
  assert.deepEqual(parseStoryLink(q), full);
  const run = parseStoryLink('?page=run&s=2026-08-23/aware/faults&k=376');
  assert.deepEqual(parseStoryLink(storyLinkQuery(run)), run);
});
