// ui/story/shell.js (UI-A): the story app's chrome (story-flow spec v2, "Shared chrome"): a 60 px header (wordmark,
// step nav 1 Configure / 2 Run / 3 Results / 4 Learnings / 5 Markets, and per page a framing label or the run pill + continue
// button), a notice line, and a footer with the sources and the "Engine explorer" link (ui/explore.html, the old tab
// app). Also numHTML(): a labelled value + its provenance tag, the ctx.num() of app.js.
import { fmtValue, LabelError, LABELS } from '../lib/format.js';

const esc = (s) => String(s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
export const TAG_LABELS = [...LABELS, 'UNVERIFIED', 'SCREENING'];
export const STEPS = [['configure', '1 Configure'], ['run', '2 Run'], ['results', '3 Results'], ['learnings', '4 Learnings'], ['markets', '5 Markets']];
export const FRAMING = 'Oncor-suburb stand-in · LZ_NORTH';
export const STANDIN = 'Oncor-suburb stand-in on NREL\'s synthetic feeder';
/** How the feeder is tagged wherever it appears (data-truth audit #1): a REAL dataset of a synthetic feeder. Its name,
 *  licence and the long disclosure are read from topology.json (constants.FEEDER_NAME / STAND_IN, meta.license). */
export const FEEDER_TAG = 'REAL dataset · synthetic feeder';
const NEXT = { run: ['results', 'Continue to Results →'], results: ['learnings', 'Continue to Learnings →'], learnings: ['markets', 'Continue to Markets →'] };
export const MARKETS_FRAMING = 'ERCOT and NYISO · real prices 2015–2026 · forecast 2027–31';

/** A provenance tag: the label word in a 1 px tag, title = cite. SCREENING is dashed (story-flow tokens). */
/** The Engine explorer link for where the reader is now: the same evening, policy and minute (explorer views p1/p2),
 *  plus from=<this story URL> so the explorer's Back button returns to this exact page. Built at click time, because
 *  the Run page moves k as it plays. Variants the explorer has no run for (fleet size, growth, ...) open its standard
 *  run of the same policy; "faults" opens its aware_faults branch. */
export function explorerHref(search = (typeof location !== 'undefined' ? location.search : '')) {
  const q = new URLSearchParams(search);
  const page = q.get('page') || 'configure';
  const s = (q.get('s') || '').split('/');
  const out = new URLSearchParams();
  const view = page === 'learnings' ? 'p2' : 'p1';
  out.set('view', view);
  if (view === 'p1') {
    const date = /^\d{4}-\d{2}-\d{2}$/.test(s[0] || '') ? s[0] : null;
    const pol = ['none', 'naive', 'aware'].includes(s[1]) ? s[1] : null;
    const branch = pol === 'aware' && s.slice(2).includes('faults') ? 'aware_faults' : pol;
    if (branch) out.set('branch', branch);
    if (date && date !== '2026-08-23') out.set('date', date);
    const k = q.get('k');
    if (k != null && /^\d+$/.test(k)) {
      const m = 16 * 60 + Number(k);
      out.set('t', `${String(Math.floor(m / 60) % 24).padStart(2, '0')}:${String(m % 60).padStart(2, '0')}`);
    }
  }
  out.set('from', String(search).replace(/^\?/, '') || 'page=configure');
  return `explore.html?${out.toString()}`;
}

function wireExplore(root) {
  for (const a of root.querySelectorAll('a[data-explore]')) {
    const refresh = () => { a.href = explorerHref(); };
    a.addEventListener('mouseenter', refresh);
    a.addEventListener('focus', refresh);
    a.addEventListener('mousedown', refresh);
    a.addEventListener('click', refresh);
  }
}

export function tagHTML(label, cite) {
  if (!TAG_LABELS.includes(label)) throw new LabelError(`unknown label ${label}`);
  return `<span class="chip chip-${label}"${cite ? ` title="${esc(cite)}"` : ''}>${label}</span>`;
}

/** ctx.num(): HTML for a labelled value, `<span class="num">value</span>` + its tag (+ a dashed SCREENING tag when
 *  opts.screening, x.screening === true or x.label === 'SCREENING'). Throws LabelError on a bare number. */
export function numHTML(x, opts = {}) {
  if (x === null || typeof x !== 'object' || Array.isArray(x) || !('v' in x) || !TAG_LABELS.includes(x.label)) {
    throw new LabelError(`unlabelled number: ${JSON.stringify(x)} (want {"v": n, "label": "${TAG_LABELS.join('|')}"})`);
  }
  const std = LABELS.includes(x.label);
  const val = fmtValue(std ? x : { ...x, label: 'ASSUMPTION' }, opts);
  const screening = opts.screening || x.screening === true || x.label === 'SCREENING';
  const tags = (x.label !== 'SCREENING' ? tagHTML(x.label, x.cite) : '')
    + (screening ? tagHTML('SCREENING', `Screening estimate, not checked by OpenDSS.${x.cite ? ' ' + x.cite : ''}`) : '');
  return `<span class="num">${esc(val)}</span>${tags}`;
}

// ---- what a lever changed: the catalogue's `vsDefault` (only the headline values that differ from the default run) ----
/** Words for the catalogue's headline keys (`catalogue.headline`); money is the fleet's gross energy value (ruling 6). */
export const VS_WORDS = {
  batteryCausedNormal: ['battery-caused normal-rating events', {}],
  batteryCausedEmergency: ['battery-caused emergency events', {}],
  batteryCausedAmberMin: ['battery-caused minutes over nameplate', {}],
  energyValueUSD: ['fleet gross energy value, not Base\'s profit', { money: true, digits: 2 }],
  maxLoading: ['worst transformer', { unit: '%', digits: 1 }],
  normalEvents: ['normal-rating events', {}],
  emergencyTfs: ['transformers above emergency', {}],
  // the data key names 04:00; the words take the run's own end time (start + steps x stepSeconds) when known
  chargedPctBy0400: [({ end }) => (end ? `fleet charged by ${end}` : 'fleet charged by the end of the run'), { unit: '%', digits: 1 }],
  reserveBreaches: ['reserve breaches (battery-minutes)', {}],
  protectionOperated: ['protection operations', {}],
  homesDark: ['homes dark', {}],
};

/** The rows of a scenario's `vsDefault` in the catalogue's `headline` order: [{key, words, opts, now, ref, refId,
 *  refTitle}], `now` and `ref` labelled from the two runs' own summaries (a row whose label is missing is dropped:
 *  never a bare number). [] for the default run, an alias, or a scenario without `vsDefault`. `clock.end` = the run's
 *  end time ("04:00", from its meta), for the words of a time-named key. */
export function vsDefaultRows(cat, scenario, clock = {}) {
  const vs = scenario && scenario.vsDefault;
  if (!vs || typeof vs !== 'object') return [];
  const refDefault = scenario.vsDefaultRef || null;
  const order = Array.isArray(cat.headline) ? cat.headline : Object.keys(vs);
  const keys = [...order.filter((k) => k in vs), ...Object.keys(vs).filter((k) => !order.includes(k))];
  const out = [];
  for (const key of keys) {
    const d = vs[key];
    if (!d || typeof d !== 'object' || !('v' in d)) continue;
    const refId = d.refId || refDefault;
    const refS = (cat.scenarios || []).find((x) => x.id === refId) || null;
    const lab = (sc) => (sc && sc.summary && sc.summary[key] && LABELS.includes(sc.summary[key].label) ? sc.summary[key] : null);
    // this run's value: the entry's own label (catalogue), else this run's summary; the reference value: the reference
    // run's own summary label. A value without a label is dropped, never shown bare.
    const own = lab(scenario);
    const nowL = LABELS.includes(d.label) ? { label: d.label, cite: own ? own.cite : '' } : own;
    const refL = lab(refS);
    if (!nowL || !refL) continue;
    const [w, opts] = VS_WORDS[key] || [key, {}];
    const words = typeof w === 'function' ? w(clock || {}) : w;
    out.push({ key, words, opts, refId, refTitle: refS ? refS.title || refS.id : refId, refName: refS ? runName(cat, refS) : refId,
      now: { v: d.v, label: nowL.label, cite: nowL.cite }, ref: { v: d.ref, label: refL.label, cite: `${refId}: ${refL.cite || ''}` } });
  }
  return out;
}

/** Plain text of the rows ("fleet gross energy value $1,845.39 (23 Aug 2026 · Naive · default settings: $893.83)"). */
export function vsDefaultText(rows) {
  return rows.map((r) => `${r.words} ${fmtValue(r.now, r.opts)} (${r.refName}: ${fmtValue(r.ref, r.opts)})`).join('; ');
}

/** HTML of the rows, each value and its reference value with their tags; the reference run is named by the caller
 *  (vsDefaultHead). `max` rows at most (the rest in the title). */
export function vsDefaultHTML(rows, { max = Infinity } = {}) {
  if (!rows.length) return '';
  const shown = rows.slice(0, max);
  const more = rows.length - shown.length;
  return shown.map((r) => `<span class="vs-row" title="${esc(`${r.words}: ${fmtValue(r.now, r.opts)} vs ${r.refTitle} ${fmtValue(r.ref, r.opts)}`)}"><span class="tx">${esc(r.words)}</span> ${numHTML(r.now, r.opts)} <span class="vs-was">vs ${numHTML(r.ref, r.opts)}</span></span>`).join('')
    + (more > 0 ? `<span class="vs-more" title="${esc(vsDefaultText(rows.slice(max)))}">+${more} more</span>` : '');
}
/** "vs 23 Aug 2026 · Naive · default settings": the reference run of the rows, by name. */
export const vsDefaultHead = (rows) => (rows.length ? `vs ${rows[0].refName}` : '');
/** The scenario's reference run (catalogue `vsDefaultRef`) and what moved against it: {ref, refTitle, refName, rows};
 *  null for the default run itself (no reference). rows = [] when nothing in the headline moved. */
export function vsDefaultInfo(cat, scenario, clock = {}) {
  if (!scenario) return null;
  const rows = vsDefaultRows(cat, scenario, clock);
  const refId = scenario.vsDefaultRef || (rows[0] && rows[0].refId) || null;
  if (!refId || refId === scenario.id) return null;
  const ref = (cat.scenarios || []).find((x) => x.id === refId) || null;
  return { refId, ref, refTitle: ref ? ref.title || ref.id : refId, refName: ref ? runName(cat, ref) : refId, rows };
}

// ---- customers: every load bus is a customer; topology homes[].use says "residential" or "commercial" (DERIVED,
// topology meta.customerUse). A single customer keeps its "Home 0xxx" id; a commercial one is marked a small business.
export const SMALL_BUSINESS = 'small business';
/** "Home 0225 (small business)" for a commercial customer, else its label ("Home 0001"). `h` = index into homes. */
export function customerName(topology, h) {
  const x = topology && topology.homes && topology.homes[h];
  if (!x) return `Home ${h}`;
  return x.use === 'commercial' ? `${x.label} (${SMALL_BUSINESS})` : x.label;
}
/** The engine's own text with every commercial customer's "Home 0xxx" marked "(small business)"; nothing else changes. */
export function markCustomers(text, topology) {
  const com = new Set(((topology && topology.homes) || []).filter((x) => x.use === 'commercial').map((x) => x.label));
  if (!com.size || !text) return text;
  return String(text).replace(/Home \d{4}(?! \(small business\))/g, (m) => (com.has(m) ? `${m} (${SMALL_BUSINESS})` : m));
}

/** A lever option's short words: its catalogue label up to the first ": " or " (" ("Controller crash: a worker ..."
 *  -> "Controller crash"). */
export function optionWords(catalogue, key, value) {
  const L = (catalogue && catalogue.levers) || {};
  const o = ((L[key] && L[key].options) || []).find((x) => String(x.id) === String(value));
  return o ? String(o.label).split(/: | \(/)[0].trim() : String(value);
}
// the catalogue constant that holds each fleet lever's option set (its label says where the set comes from)
export const LEVER_SET = { fleet: 'STORY_FLEET_SIZES', reserve: 'STORY_RESERVES_PCT', soc0: 'STORY_SOC0_PCT', growth: 'STORY_GROWTH_PCT' };
/** The tag of a lever setting: the option's own `why` label, else its lever's catalogue constant; null when the data
 *  gives none (no tag is then shown). */
export function leverTag(catalogue, key, value) {
  const L = (catalogue && catalogue.levers) || {};
  const o = ((L[key] && L[key].options) || []).find((x) => String(x.id) === String(value));
  if (o && o.why && TAG_LABELS.includes(o.why.label)) return { label: o.why.label, cite: [o.why.text, o.why.cite].filter(Boolean).join(' · ') };
  const c = catalogue && catalogue.constants && catalogue.constants[LEVER_SET[key]];
  return c && TAG_LABELS.includes(c.label) ? { label: c.label, cite: c.cite || '' } : null;
}
/** The levers that describe a run, in reading order (no fleet levers without batteries; growth only when moved). */
function summaryKeys(catalogue, levers) {
  const L = (catalogue && catalogue.levers) || {};
  const keys = ['policy'];
  if (levers.failure && levers.failure !== 'none') keys.push('failure');
  if (levers.policy !== 'none') keys.push('fleet', 'cls', 'reserve', 'soc0');
  if (levers.growth !== undefined && String(levers.growth) !== String(L.growth && L.growth.default)) keys.push('growth');
  return keys;
}
/** Lever words for the run pill, from the catalogue's own option labels. */
export function leverSummary(catalogue, levers) {
  if (!levers) return '';
  return summaryKeys(catalogue, levers).map((k) => optionWords(catalogue, k, levers[k])).join(' · ');
}
/** The same, each setting with its tag (leverTag); the words may shrink with an ellipsis, the tag never does.
 *  `keys` limits the levers (the run pill shows the policy, the failure and the moved levers only). */
export function leverSummaryHTML(catalogue, levers, keys = null) {
  if (!levers) return '';
  return (keys || summaryKeys(catalogue, levers)).map((k) => {
    const t = leverTag(catalogue, k, levers[k]);
    return `<span class="lv"><span class="tx">${esc(optionWords(catalogue, k, levers[k]))}</span>${t ? tagHTML(t.label, t.cite) : ''}</span>`;
  }).join('<span class="sep">&nbsp;·&nbsp;</span>');
}
/** The levers of a run that are not at their default: the failure and the moved fleet levers (the policy always). */
export function movedKeys(catalogue, levers) {
  const L = (catalogue && catalogue.levers) || {};
  return ['policy', 'failure', 'fleet', 'cls', 'reserve', 'soc0', 'growth']
    .filter((k) => k === 'policy' || (L[k] && levers[k] !== undefined && String(levers[k]) !== String(L[k].default)));
}
/** The naive policy's tag when the run's meta gives none (meta.naiveLabel is the data's own: ASSUMPTION). */
export const NAIVE_CITE = "our assumption of one number, no feeder check; Base's real split is not public, and Base may not charge this way (build prompt 12 Q5)";
/** A run's title with each tag beside the words it labels (review-0927 M3), e.g. "23 Aug 2026 [REAL] · Naive: our
 *  assumption of one number, no feeder check [ASSUMPTION] · 48 batteries [ASSUMPTION]": the date (the title up to its
 *  first ": ") carries the evening's tag; the naive policy words carry `naive` (the run's meta.naiveLabel), else
 *  ASSUMPTION with NAIVE_CITE; the rest of the title carries the tags of the other moved levers. `prefix` goes before
 *  the date ("Your own settings: "). Text parts are .tx spans (they may shrink); a tag never does. */
export function runTitleHTML(catalogue, sc, { prefix = '', naive = null } = {}) {
  const title = String((sc && (sc.title || sc.id)) || '');
  const lv = sc && sc.levers;
  const tx = (s) => `<span class="tx">${esc(s)}</span>`;
  if (!lv) return tx(prefix + title);
  const tagOf = (k) => { const t = leverTag(catalogue, k, lv[k]); return t ? tagHTML(t.label, `${optionWords(catalogue, k, lv[k])}: ${t.cite}`) : ''; };
  const others = movedKeys(catalogue, lv).filter((k) => k !== 'policy').map(tagOf).join('');
  const i = title.indexOf(': ');
  const parts = [];
  let rest = title, lead = prefix;
  if (i > 0) { parts.push(tx(prefix + title.slice(0, i)) + tagOf('evening')); rest = title.slice(i + 2); lead = ''; }
  if (lv.policy === 'naive') {
    const L = (catalogue && catalogue.levers && catalogue.levers.policy) || {};
    const o = (L.options || []).find((x) => String(x.id) === 'naive');
    const words = o && rest.startsWith(o.label) ? o.label : rest;
    rest = rest.slice(words.length).replace(/^,\s*/, '');
    const nt = naive && TAG_LABELS.includes(naive.label) ? tagHTML(naive.label, [naive.text, naive.cite].filter(Boolean).join(' · ')) : tagHTML('ASSUMPTION', NAIVE_CITE);
    parts.push(tx(lead + words) + nt);
    lead = '';
  }
  const tail = others + (i > 0 ? '' : tagOf('evening'));   // a title with no date: the evening's tag goes last
  if (rest || lead || !parts.length) parts.push(tx(lead + rest) + tail);
  else parts[parts.length - 1] += tail;
  return parts.join('<span class="sep">·</span>');
}
/** A run by name: "23 Aug 2026 · Naive · default settings", or "... · 192 batteries" for a moved fleet lever. */
export function runName(catalogue, sc) {
  const L = (catalogue && catalogue.levers) || {};
  if (!sc.levers || !L.evening) return sc.title || sc.id;
  const lv = sc.levers;
  const parts = [optionWords(catalogue, 'evening', lv.evening), optionWords(catalogue, 'policy', lv.policy)];
  if (lv.failure && lv.failure !== 'none') parts.push(optionWords(catalogue, 'failure', lv.failure));
  const moved = ['fleet', 'cls', 'reserve', 'soc0', 'growth'].filter((k) => L[k] && lv[k] !== undefined && String(lv[k]) !== String(L[k].default));
  parts.push(...(moved.length ? moved.map((k) => optionWords(catalogue, k, lv[k])) : ['default settings']));
  return parts.join(' · ');
}

export function createShell(body) {
  const header = document.createElement('header');
  header.className = 'st-header';
  const notice = document.createElement('div');
  notice.className = 'st-notice';
  notice.hidden = true;
  notice.setAttribute('role', 'status');
  const banner = document.createElement('div');
  banner.className = 'st-banner';
  banner.hidden = true;
  const main = document.createElement('main');
  main.className = 'st-main';
  const footer = document.createElement('footer');
  footer.className = 'st-footer';
  footer.innerHTML = `<span class="st-standin">${STANDIN}</span>
    <span class="st-feeder">Feeder: ${FEEDER_TAG}</span><span>Prices: ERCOT real-time, LZ_NORTH</span>
    <span>Buildings: © OpenStreetMap contributors, ODbL</span><span>Power flow: OpenDSS</span>
    <span>Every number carries its tag: REAL, SIM, DERIVED, ASSUMPTION</span>
    <a class="st-explorer" href="explore.html" data-explore>Engine explorer</a>`;
  body.append(header, banner, notice, main, footer);
  wireExplore(footer);

  const api = {
    main,
    /** The footer's feeder line and stand-in note, from topology.json (constants FEEDER_NAME / STAND_IN, meta.license). */
    setFeeder(topology) {
      const c = (topology && topology.constants) || {}, m = (topology && topology.meta) || {};
      const lic = (topology && topology.sources && topology.sources.topology && topology.sources.topology.text) || m.license;
      const fe = footer.querySelector('.st-feeder');
      if (fe && lic) fe.textContent = `Feeder: ${lic} (${FEEDER_TAG})`;
      if (fe && c.FEEDER_NAME && c.FEEDER_NAME.cite) fe.title = `${c.FEEDER_NAME.value}: ${c.FEEDER_NAME.cite}`;
      const si = footer.querySelector('.st-standin');
      if (si && c.STAND_IN) si.title = `${c.STAND_IN.value} (${c.STAND_IN.label}): ${c.STAND_IN.cite || ''}`;
    },
    update({ page, scenario, catalogue, link, nav, getJSON }) {
      notice.hidden = true;
      notice.textContent = '';
      const running = page === 'running';
      const active = running ? 'run' : page;
      const steps = STEPS.map(([id, text]) => {
        if (running && (id === 'results' || id === 'learnings')) return `<span class="st-step off" aria-disabled="true">${text}</span>`;
        const on = id === active;
        return `<a class="st-step${on ? ' on' : ''}" href="${esc(link(id, {}))}" data-page="${id}"${on ? ' aria-current="page"' : ''}>${text}</a>`;
      }).join('');
      let right = '';
      if (page === 'configure') right = `<span class="st-framing" title="${esc(FRAMING)}">${FRAMING}</span>`;
      else if (page === 'learnings') right = `<span class="st-framing" data-framing="learnings"></span><a class="st-btn" href="${esc(link(NEXT.learnings[0], {}))}" data-next="${NEXT.learnings[0]}">${NEXT.learnings[1]}</a>`;
      else if (page === 'markets') right = `<span class="st-framing" title="${esc(MARKETS_FRAMING)}">${MARKETS_FRAMING}</span>`;
      else if (NEXT[page] && scenario) {
        const info = vsDefaultInfo(catalogue, scenario);
        const vs = info ? `${info.refTitle}: ${info.rows.length ? vsDefaultText(info.rows) : 'no headline value moved'}` : '';
        const ev = scenario.levers ? leverTag(catalogue, 'evening', scenario.levers.evening) : null;
        right = `<span class="st-pill" title="${esc(`${scenario.title || scenario.id} (${scenario.id}). ${leverSummary(catalogue, scenario.levers)}. A static replay of the engine's run (ui/data); no simulator runs in the page.${vs ? ` Against the reference run: ${vs}.` : ''}`)}">
            <span class="dot"></span><b>${esc(scenario.levers ? optionWords(catalogue, 'evening', scenario.levers.evening) : scenario.id)}</b>${ev ? tagHTML(ev.label, ev.cite) : ''}<span class="sum">${scenario.levers ? leverSummaryHTML(catalogue, scenario.levers, movedKeys(catalogue, scenario.levers)) : ''}</span>${scenario.levers && scenario.levers.failure === 'covert' ? '<span class="fict">Fictional attacker</span>' : ''}</span>
          <a class="st-btn" href="${esc(link(NEXT[page][0], {}))}" data-next="${NEXT[page][0]}">${NEXT[page][1]}</a>`;
      }
      header.innerHTML = `<div class="st-wordmark"><img class="st-mark" src="assets/batter-up-mark-256.png" alt="" width="29" height="36">Batter Up</div><nav class="st-steps" aria-label="Story steps">${steps}</nav>
        <a class="st-explore" href="${esc(explorerHref())}" data-explore title="Engine explorer: the full-control view of the same engine. Every branch, every transformer and every labelled number on one screen. Opens at this evening, policy and minute; its Back button returns you here.">Engine explorer</a>
        <div class="st-spacer"></div>${right}`;
      // Learnings' framing: the setting its answers were computed for, as the P2 export words it (p2/index.json scope)
      const fr = header.querySelector('[data-framing="learnings"]');
      if (fr && getJSON) {
        getJSON('p2/index.json').then((p2) => {
          const t = p2 && ((p2.usefulCapacity && p2.usefulCapacity.scopeText) || (p2.scope && p2.scope.text));
          if (t) { fr.textContent = t; fr.title = t; } else fr.remove();
        }).catch(() => fr.remove());
      } else if (fr) fr.remove();
      // in-app navigation (the hrefs still work with a middle click)
      wireExplore(header);
      for (const a of header.querySelectorAll('a[data-page], a[data-next]')) {
        a.addEventListener('click', (ev) => {
          if (ev.button !== 0 || ev.metaKey || ev.ctrlKey || ev.shiftKey || ev.altKey) return;
          ev.preventDefault();
          nav(a.dataset.page || a.dataset.next, {});
        });
      }
    },
    notice(msg) {
      notice.textContent = notice.textContent ? `${notice.textContent} ${msg}` : msg;
      notice.hidden = false;
      document.body.dataset.notice = String((Number(document.body.dataset.notice) || 0) + 1);
    },
    banner(msg) { banner.textContent = msg; banner.hidden = false; },
    error(msg) {
      let el = document.querySelector('.st-error');
      if (!el) { el = document.createElement('div'); el.className = 'st-error'; el.setAttribute('role', 'alert'); (document.querySelector('main.st-main') || footer).before(el); }
      el.textContent = msg;
    },
  };
  return api;
}
