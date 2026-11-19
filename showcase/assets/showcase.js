// CS193T Final Project Showcase.
// Security model: data is untrusted. Every value reaches the DOM through textContent or validated
// attributes; there is no innerHTML anywhere (CSP enforces Trusted Types). URLs pass an allowlist.
import { createEngine, highlightRuns, tokenize, stem } from './search.js';

const $ = (sel) => document.querySelector(sel);
const LINK_KINDS = ['website', 'pdf', 'image', 'artifact', 'dashboard', 'git', 'video', 'doc', 'other'];
const KIND_LABEL = { website: 'Website', pdf: 'PDF', image: 'Image', artifact: 'Artifact', dashboard: 'Dashboard', git: 'Code', video: 'Video', doc: 'Doc', other: 'Link' };
const TOPIC_HUE = { fitness: 14, health: 172, wellbeing: 262, food: 36, games: 292, productivity: 214, career: 228, learning: 152, research: 198, finance: 128, creative: 330, community: 4, civic: 24, environment: 140, travel: 186, software: 244 };
let TRY = ['work out', 'cooking', 'games', 'organization', 'study', 'health', 'money', 'climate'];
const STORE_KEY = 'cs193t-showcase:interests:v1';
const THEME_KEY = 'cs193t-showcase:theme';

/* ---------- safe DOM helpers ---------- */

function el(tag, props, ...children) {
  const node = document.createElement(tag);
  if (props) {
    for (const [k, v] of Object.entries(props)) {
      if (v == null || v === false) continue;
      if (k === 'class') node.className = v;
      else if (k === 'text') node.textContent = v;
      else if (k.startsWith('on')) node.addEventListener(k.slice(2), v);
      else if (k === 'style' || k === 'innerHTML' || k === 'srcdoc') throw new Error('blocked prop ' + k);
      else node.setAttribute(k, v === true ? '' : String(v));
    }
  }
  for (const c of children.flat()) if (c != null && c !== false) node.append(c);
  return node;
}

const ICONS = {
  website: ['M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18Z', 'M3.5 9h17M3.5 15h17', 'M12 3c2.5 2.6 3.6 5.6 3.6 9s-1.1 6.4-3.6 9c-2.5-2.6-3.6-5.6-3.6-9S9.5 5.6 12 3Z'],
  pdf: ['M7 3h7l5 5v13H7z', 'M14 3v5h5', 'M10 13h6M10 17h4'],
  image: ['M4 5h16v14H4z', 'm4 16 5-5 4 4 2-2 5 5', 'M15.5 9.5h.01'],
  artifact: ['M12 3.5 14 10l6.5 2-6.5 2-2 6.5-2-6.5-6.5-2 6.5-2z'],
  dashboard: ['M4 20V10M10 20V4M16 20v-7M22 20H2'],
  git: ['M6 3v12', 'M18 9a3 3 0 1 0 0-.01', 'M6 21a3 3 0 1 0 0-.01', 'M18 9c0 4-4 5-9 6'],
  video: ['M4 5h16v14H4z', 'm10 9 5 3-5 3z'],
  doc: ['M6 3h12v18H6z', 'M9 8h6M9 12h6M9 16h4'],
  other: ['M10 14 20 4', 'M14 4h6v6', 'M20 14v6H4V4h6'],
  arrow: ['M7 17 17 7', 'M8 7h9v9'],
  close: ['M6 6l12 12M18 6 6 18'],
};
const SVGNS = 'http://www.w3.org/2000/svg';
function icon(name, cls = 'icon') {
  const svg = document.createElementNS(SVGNS, 'svg');
  svg.setAttribute('viewBox', '0 0 24 24');
  svg.setAttribute('class', cls);
  svg.setAttribute('aria-hidden', 'true');
  svg.setAttribute('focusable', 'false');
  for (const d of ICONS[name] || ICONS.other) {
    const path = document.createElementNS(SVGNS, 'path');
    path.setAttribute('d', d);
    svg.append(path);
  }
  return svg;
}

function textWithMarks(text, hl) {
  if (!hl) return document.createTextNode(text);
  const frag = document.createDocumentFragment();
  for (const [chunk, hit] of highlightRuns(text, hl.words, hl.prefixes)) frag.append(hit ? el('mark', { text: chunk }) : chunk);
  return frag;
}

/* ---------- validation (mirrors SPEC.md; build step enforces the same rules) ---------- */

const CTRL = /[\s\u0000-\u001f\u007f-\u009f\u200b-\u200f\u2028\u2029\u202a-\u202e\u2066-\u2069\ufeff]/;
const LOCAL_PATH = /^(files|thumbs)\/[a-z0-9][a-z0-9-]*\.(pdf|webp)$/;
// Numeric/hex hosts (127.1, 0x7f.1, 10.1) and IPv6 literals: browsers may resolve them to private addresses.
const IP_HOST = /^(\[.*\]|(0x[0-9a-f]+|\d+)(\.(0x[0-9a-f]+|\d+))*\.?)$/i;

export function safeUrl(u, { localOnly = false } = {}) {
  if (typeof u !== 'string' || !u || u.length > 2048 || CTRL.test(u)) return null;
  if (LOCAL_PATH.test(u)) return u;
  if (localOnly) return null;
  if (!/^https:\/\//i.test(u)) return null;
  let url;
  try { url = new URL(u); } catch { return null; }
  if (url.protocol !== 'https:' || url.username || url.password || !url.hostname.includes('.') || IP_HOST.test(url.hostname) || url.port) return null;
  return url.href;
}

function str(v, max) { return typeof v === 'string' && v.trim() && v.length <= max ? v.trim() : null; }
function strList(v, maxItems, maxLen) {
  return Array.isArray(v) ? v.filter((s) => str(s, maxLen)).map((s) => s.trim()).slice(0, maxItems) : [];
}

function validateProject(raw, interestIds) {
  if (!raw || typeof raw !== 'object') return null;
  const id = typeof raw.id === 'string' && /^[a-z0-9][a-z0-9-]{0,63}$/.test(raw.id) ? raw.id : null;
  const title = str(raw.title, 90);
  const summary = str(raw.summary, 280);
  const students = strList(raw.students, 8, 60);
  if (!id || !title || !summary || !students.length) return null;
  const links = (Array.isArray(raw.links) ? raw.links : []).slice(0, 6).map((l) => {
    const href = l && safeUrl(l.url);
    if (!href) return null;
    const kind = LINK_KINDS.includes(l.kind) ? l.kind : 'other';
    return { kind, href, label: str(l.label, 60) || KIND_LABEL[kind], local: !href.startsWith('https://') };
  }).filter(Boolean);
  return {
    id, title, summary, students, links,
    description: str(raw.description, 1500) || '',
    thumbnail: raw.thumbnail ? safeUrl(raw.thumbnail, { localOnly: true }) : null,
    thumbnailAlt: str(raw.thumbnailAlt, 160) || `Poster preview for ${title}`,
    tags: strList(raw.tags, 4, 32).filter((t) => interestIds.has(t)),
    tools: strList(raw.tools, 8, 40),
    keywords: strList(raw.keywords, 12, 40),
    featured: raw.featured === true,
  };
}

/* ---------- small utilities ---------- */

function hash(s) { let h = 2166136261; for (let i = 0; i < s.length; i++) { h ^= s.charCodeAt(i); h = Math.imul(h, 16777619); } return h >>> 0; }
function seeded(seed) { return () => { seed |= 0; seed = (seed + 0x6d2b79f5) | 0; let t = Math.imul(seed ^ (seed >>> 15), 1 | seed); t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t; return ((t ^ (t >>> 14)) >>> 0) / 4294967296; }; }
function dailyShuffle(list) {
  const d = new Date();
  const rnd = seeded(hash(`${d.getFullYear()}-${d.getMonth()}-${d.getDate()}`));
  const a = list.slice();
  for (let i = a.length - 1; i > 0; i--) { const j = Math.floor(rnd() * (i + 1)); [a[i], a[j]] = [a[j], a[i]]; }
  return a;
}
function debounce(fn, ms) { let t; return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); }; }
function hostOf(href) { try { return new URL(href).hostname.replace(/^www\./, ''); } catch { return ''; } }
function plural(n, one, many = one + 's') { return `${n} ${n === 1 ? one : many}`; }
const store = {
  get(k) { try { return window.localStorage.getItem(k); } catch { return null; } },
  set(k, v) { try { window.localStorage.setItem(k, v); } catch { /* private mode */ } },
  del(k) { try { window.localStorage.removeItem(k); } catch { /* ignore */ } },
};
const reduceMotion = () => window.matchMedia('(prefers-reduced-motion: reduce)').matches;

/* ---------- state ---------- */

const state = { q: '', topic: null, sort: 'mixed', interests: {}, data: null, engine: null, list: [], sheetList: [], sheetIndex: -1, pushedSheet: false };
let interestsById = new Map();

function loadInterests() {
  try {
    const raw = JSON.parse(store.get(STORE_KEY) || '{}');
    const out = {};
    for (const [k, v] of Object.entries(raw)) if (interestsById.has(k) && typeof v === 'number' && v > 0 && v < 100) out[k] = v;
    return out;
  } catch { return {}; }
}
function saveInterests() { store.set(STORE_KEY, JSON.stringify(state.interests)); }
function learn(ids, amount = 1, announce = true) {
  if (!ids.length) return;
  const before = { ...state.interests };
  const fresh = ids.filter((id) => !(id in state.interests));
  for (const k of Object.keys(state.interests)) state.interests[k] *= 0.85;
  for (const id of ids) state.interests[id] = (state.interests[id] || 0) + amount;
  const top = Object.entries(state.interests).sort((a, b) => b[1] - a[1]).slice(0, 5);
  state.interests = Object.fromEntries(top.filter(([, w]) => w > 0.2));
  saveInterests();
  if (announce && fresh.length) {
    const names = fresh.map((id) => interestsById.get(id).label).join(' and ');
    toast(`Added ${names} to your picks`, { label: 'Undo', run: () => { state.interests = before; saveInterests(); render(); } });
  }
}

/* ---------- covers & cards ---------- */

function cover(p, variant = 'card') {
  const primary = p.tags[0] || 'software';
  const box = el('div', { class: `cover cover--${variant}` });
  if (p.thumbnail) {
    const img = el('img', { src: p.thumbnail, alt: variant === 'sheet' ? p.thumbnailAlt : '', loading: variant === 'feat' ? 'eager' : 'lazy', decoding: 'async', width: 960, height: 640, draggable: 'false' });
    img.addEventListener('error', () => { img.remove(); box.append(...generatedCover(p, primary, box)); }, { once: true });
    box.append(img);
  } else box.append(...generatedCover(p, primary, box));
  return box;
}
function generatedCover(p, primary, box) {
  box.classList.add('cover--gen', `cover--pat${hash(p.id) % 4}`);
  box.style.setProperty('--hue', String(TOPIC_HUE[primary] ?? 214));
  const words = p.title.split(/\s+/).filter(Boolean);
  const first = (w) => (w ? Array.from(w)[0] : ''); // whole characters, never half of an emoji
  const mono = (first(words[0]) + first(words[1])).toUpperCase();
  return [
    el('span', { class: 'cover__mono', 'aria-hidden': 'true', text: mono }),
    el('span', { class: 'cover__topic', 'aria-hidden': 'true', text: (interestsById.get(primary) || {}).label || '' }),
  ];
}

function topicLine(p) { return p.tags.map((t) => (interestsById.get(t) || {}).label).filter(Boolean).join(' · '); }

function kindsRow(p) {
  const kinds = [...new Set(p.links.map((l) => l.kind))].slice(0, 4);
  return el('ul', { class: 'kinds', 'aria-label': 'Includes' }, kinds.map((k) => el('li', { class: 'kind' }, icon(k), el('span', { text: KIND_LABEL[k] }))));
}

function externalLink(l, cls, children, title) {
  const a = el('a', { class: cls, href: l.href }, children);
  if (!l.local) { a.target = '_blank'; a.rel = 'noopener noreferrer external'; }
  if (title) a.setAttribute('aria-label', title);
  return a;
}

function withTitle(node, t) { node.title = t; return node; }

function card(p, list, hl, compact = false) {
  const link = el('a', { class: 'card__link', href: `#p=${p.id}` }, textWithMarks(p.title, hl));
  link.addEventListener('click', (e) => { e.preventDefault(); openSheet(p.id, list); });
  const primary = p.links[0];
  return el('article', { class: `card${compact ? ' card--compact' : ''}` },
    el('div', { class: 'card__media' }, cover(p)),
    el('div', { class: 'card__body' },
      el('p', { class: 'card__eyebrow', text: topicLine(p) }),
      el('h3', { class: 'card__title' }, link),
      el('p', { class: 'card__people', title: p.students.join(', '), text: p.students.join(', ') }),
      compact ? null : el('p', { class: 'card__summary' }, textWithMarks(p.summary, hl)),
      el('div', { class: 'card__foot' },
        kindsRow(p),
        primary ? withTitle(externalLink(primary, 'card__go', [el('span', { text: 'Open' }), icon('arrow', 'icon icon--sm')],
          `Open ${p.title}: ${primary.label}${primary.local ? '' : `, ${hostOf(primary.href)} (new tab)`}`), primary.local ? primary.label : `${primary.label} · ${hostOf(primary.href)}`) : null)));
}

function featCard(p, list) {
  const link = el('a', { class: 'feat__link', href: `#p=${p.id}` }, p.title);
  link.addEventListener('click', (e) => { e.preventDefault(); openSheet(p.id, list); });
  return el('article', { class: 'feat' },
    el('div', { class: 'feat__text' },
      el('p', { class: 'feat__eyebrow', text: topicLine(p) }),
      el('h3', { class: 'feat__title' }, link),
      el('p', { class: 'feat__summary', text: p.summary }),
      el('p', { class: 'feat__people', text: p.students.join(', ') }),
      el('span', { class: 'feat__cta', 'aria-hidden': 'true' }, 'View project', icon('arrow', 'icon icon--sm'))),
    el('div', { class: 'feat__media' }, cover(p, 'feat')));
}

/* ---------- rendering ---------- */

function computeList() {
  const all = state.data.projects;
  let list; let res = null;
  if (state.q.trim()) {
    res = state.engine.search(state.q);
    list = res ? res.results.map((r) => r.p) : all.slice();
  } else list = all.slice();
  if (state.topic) list = list.filter((p) => p.tags.includes(state.topic));
  if (!res || !state.q.trim()) {
    if (state.sort === 'az') list.sort((a, b) => a.title.localeCompare(b.title));
    else if (state.sort === 'featured') list = [...list.filter((p) => p.featured), ...dailyShuffle(list.filter((p) => !p.featured))];
    else list = dailyShuffle(list);
  }
  return { list, res };
}

function render() {
  const { list, res } = computeList();
  state.list = list;
  const searching = !!state.q.trim();
  const browsing = !searching && !state.topic;

  const hl = res ? { words: res.matchWords, prefixes: res.terms } : null;
  const grid = $('#grid');
  grid.replaceChildren(...list.map((p) => card(p, list, hl)));
  grid.setAttribute('aria-busy', 'false');
  $('#empty').hidden = list.length > 0;
  if (!list.length) renderEmptyChips();

  const topicLabel = state.topic ? interestsById.get(state.topic).label : '';
  const shownQ = state.q.trim().length > 40 ? `${state.q.trim().slice(0, 38)}…` : state.q.trim();
  $('#projects-title').textContent = searching ? `Results for “${shownQ}”` : state.topic ? topicLabel : 'All projects';
  renderJump(searching, list.length);
  $('#results-meta').textContent = `${plural(list.length, 'project')}${searching && state.topic ? ` in ${topicLabel}` : ''}`;

  renderMatchNote(res);
  renderTopics();
  $('#sort').disabled = searching;
  $('#sort').title = searching ? 'Sorted by relevance while searching' : '';
  $('#featured').hidden = !browsing || !state.data.projects.some((p) => p.featured);
  renderForYou(browsing);
  syncUrl();
}

// While searching, the "Try" row becomes a jump link to the results, which can sit below the fold.
function renderJump(searching, n) {
  const wrap = $('#try');
  if (!searching) { if (wrap.dataset.mode !== 'try') renderTry(); return; }
  wrap.dataset.mode = 'jump';
  const b = el('button', { type: 'button', class: 'jump' }, n ? `See ${plural(n, 'result')}` : 'No matches yet', n ? icon('arrow', 'icon icon--sm jump__icon') : null);
  b.disabled = !n;
  b.addEventListener('click', () => { closeSuggest(); commitSearch(); scrollToResults(); });
  wrap.replaceChildren(b);
}

function renderMatchNote(res) {
  const note = $('#match-note');
  if (!res || !res.topics.length) { note.hidden = true; note.replaceChildren(); return; }
  const parts = [el('span', { class: 'match-note__label', text: 'Showing first' })];
  res.topics.slice(0, 3).forEach((t, i) => {
    const label = interestsById.get(t.id).label;
    const b = el('button', { type: 'button', class: 'chip chip--sm', 'aria-pressed': String(state.topic === t.id), text: label });
    b.addEventListener('click', () => setTopic(state.topic === t.id ? null : t.id));
    if (i) parts.push(' ');
    parts.push(b);
  });
  const typo = res.topics.find((t) => !t.exact);
  if (typo) parts.push(el('span', { class: 'match-note__text', text: `matched from “${typo.via}”` }));
  note.replaceChildren(...parts);
  note.hidden = false;
}

function renderTopics() {
  const counts = state.engine.topicCounts();
  const wrap = $('#topics');
  const allBtn = el('button', { type: 'button', class: 'chip', 'aria-pressed': String(!state.topic), text: 'All' });
  allBtn.addEventListener('click', () => setTopic(null));
  const chips = [...interestsById.values()].filter((i) => counts.get(i.id) > 0).map((i) => {
    const b = el('button', { type: 'button', class: 'chip', 'aria-pressed': String(state.topic === i.id), 'aria-label': `${i.label}, ${plural(counts.get(i.id), 'project')}` },
      i.label, el('span', { class: 'chip__count', 'aria-hidden': 'true', text: String(counts.get(i.id)) }));
    b.addEventListener('click', () => { setTopic(state.topic === i.id ? null : i.id); if (state.topic) learn([i.id], 0.6); });
    return b;
  });
  const focusedIdx = [...wrap.children].indexOf(document.activeElement);
  const all = [allBtn, ...chips];
  const current = Math.max(0, all.findIndex((b) => b.getAttribute('aria-pressed') === 'true'));
  all.forEach((b, i) => { b.tabIndex = i === current ? 0 : -1; });
  wrap.replaceChildren(...all);
  if (focusedIdx >= 0 && wrap.children[focusedIdx]) wrap.children[focusedIdx].focus({ preventScroll: true });
}

function renderForYou(browsing) {
  const section = $('#foryou');
  const ids = Object.entries(state.interests).sort((a, b) => b[1] - a[1]).map(([k]) => k).filter((k) => interestsById.has(k));
  if (!browsing || !ids.length) { section.hidden = true; return; }
  const weight = (p) => p.tags.reduce((s, t) => s + (state.interests[t] || 0), 0);
  let pool = dailyShuffle(state.data.projects).filter((p) => weight(p) > 0).sort((a, b) => weight(b) - weight(a));
  const nonFeat = pool.filter((p) => !p.featured);
  if (nonFeat.length >= 3) pool = nonFeat;
  const picks = pool.slice(0, 4);
  if (!picks.length) { section.hidden = true; return; }
  section.hidden = false;
  $('#foryou-sub').textContent = `Based on your searches for ${ids.slice(0, 3).map((i) => interestsById.get(i).label.toLowerCase()).join(', ')}`;
  $('#interest-pills').replaceChildren(...ids.map((id) => {
    const label = interestsById.get(id).label;
    const b = el('button', { type: 'button', class: 'chip chip--removable', 'aria-label': `Remove ${label} from your interests` }, label, icon('close', 'icon icon--xs'));
    b.addEventListener('click', () => { delete state.interests[id]; saveInterests(); render(); toast(`Removed ${label}`); const next = $('#interest-pills button') || $('#q'); next.focus({ preventScroll: true }); });
    return b;
  }));
  $('#foryou-grid').replaceChildren(...picks.map((p) => card(p, picks, null, true)));
}

function renderFeatured() {
  const feats = state.data.projects.filter((p) => p.featured);
  const track = $('#feat-track');
  track.replaceChildren(...feats.map((p) => featCard(p, feats)));
  const count = $('#feat-count');
  const update = () => {
    const items = [...track.children];
    if (!items.length) return;
    const left = track.scrollLeft;
    let idx = 0; let best = Infinity;
    items.forEach((c, i) => { const d = Math.abs(c.offsetLeft - track.offsetLeft - left); if (d < best) { best = d; idx = i; } });
    count.textContent = `${idx + 1} of ${items.length}`;
    $('#feat-prev').disabled = left <= 2;
    $('#feat-next').disabled = left + track.clientWidth >= track.scrollWidth - 2;
  };
  const step = (dir) => {
    const first = track.children[0];
    const gap = parseFloat(getComputedStyle(track).columnGap) || 16;
    track.scrollBy({ left: dir * (first ? first.getBoundingClientRect().width + gap : track.clientWidth), behavior: reduceMotion() ? 'auto' : 'smooth' });
  };
  $('#feat-prev').addEventListener('click', () => step(-1));
  $('#feat-next').addEventListener('click', () => step(1));
  let raf = 0;
  track.addEventListener('scroll', () => { cancelAnimationFrame(raf); raf = requestAnimationFrame(update); }, { passive: true });
  window.addEventListener('resize', debounce(update, 150));
  update();
}

function renderStats() {
  const ps = state.data.projects;
  const students = new Set(ps.flatMap((p) => p.students)).size;
  const topics = new Set(ps.flatMap((p) => p.tags)).size;
  $('#stats').replaceChildren(
    ...[[ps.length, 'projects'], [students, 'students'], [topics, 'topics']].flatMap(([n, l]) => [el('div', { class: 'stat' }, el('dt', { text: l }), el('dd', { text: String(n) }))]));
}

function renderTry() {
  const wrap = $('#try');
  wrap.dataset.mode = 'try';
  const chips = TRY.filter((q) => { const r = state.engine.search(q); return r && r.results.length; }).slice(0, 6).map((q) => {
    const b = el('button', { type: 'button', class: 'try__chip', text: q });
    b.addEventListener('click', () => { setQuery(q); commitSearch(); scrollToResults(); });
    return b;
  });
  wrap.replaceChildren(el('span', { class: 'try__label', text: 'Try' }), ...chips);
}

function renderEmptyChips() {
  const counts = state.engine.topicCounts();
  const top = [...counts.entries()].sort((a, b) => b[1] - a[1]).slice(0, 5);
  $('#empty-chips').replaceChildren(...top.map(([id]) => {
    const b = el('button', { type: 'button', class: 'chip', text: interestsById.get(id).label });
    b.addEventListener('click', () => { setQuery(''); setTopic(id); });
    return b;
  }));
}

/* ---------- search box + suggestions (ARIA combobox) ---------- */

let activeOpt = -1;
function suggestions() {
  const q = state.q.trim();
  if (!q) return [];
  const res = state.engine.search(q);
  if (!res) return [];
  const counts = state.engine.topicCounts();
  const opts = res.topics.slice(0, 3).map((t) => ({ type: 'topic', id: t.id, label: interestsById.get(t.id).label, meta: plural(counts.get(t.id) || 0, 'project') }));
  for (const r of res.results.slice(0, 5)) opts.push({ type: 'project', id: r.p.id, label: r.p.title, meta: r.p.students.join(', '), hl: { words: res.matchWords, prefixes: res.terms } });
  return opts;
}

function renderSuggest() {
  const box = $('#suggest');
  const input = $('#q');
  const opts = suggestions();
  activeOpt = -1;
  input.removeAttribute('aria-activedescendant');
  if (!opts.length || document.activeElement !== input) {
    box.hidden = true; input.setAttribute('aria-expanded', 'false'); box.replaceChildren();
    $('#suggest-status').textContent = state.q.trim() && document.activeElement === input ? 'No suggestions' : '';
    return;
  }
  const nodes = [];
  let group = null; let lastType = null;
  opts.forEach((o, i) => {
    if (o.type !== lastType) {
      const gid = `sg-${o.type}`;
      group = el('div', { class: 'suggest__groupwrap', role: 'group', 'aria-labelledby': gid },
        el('div', { class: 'suggest__group', id: gid, role: 'presentation', text: o.type === 'topic' ? 'Topics' : 'Projects' }));
      nodes.push(group); lastType = o.type;
    }
    const opt = el('div', { class: `suggest__opt suggest__opt--${o.type}`, role: 'option', id: `opt-${i}`, 'aria-selected': 'false', 'data-index': i },
      el('span', { class: 'suggest__label' }, o.type === 'project' ? textWithMarks(o.label, o.hl) : o.label),
      el('span', { class: 'suggest__meta', text: o.meta }));
    opt.addEventListener('pointerdown', (e) => e.preventDefault());
    opt.addEventListener('click', () => chooseSuggestion(o));
    group.append(opt);
  });
  $('#suggest-status').textContent = `${plural(opts.length, 'suggestion')}. Use the arrow keys to choose.`;
  box.replaceChildren(...nodes);
  box._opts = opts;
  box.hidden = false;
  input.setAttribute('aria-expanded', 'true');
}

function closeSuggest() { const box = $('#suggest'); box.hidden = true; $('#q').setAttribute('aria-expanded', 'false'); $('#q').removeAttribute('aria-activedescendant'); activeOpt = -1; }

function moveActive(delta) {
  const box = $('#suggest');
  const opts = [...box.querySelectorAll('[role=option]')];
  if (!opts.length) return;
  activeOpt = (activeOpt + delta + opts.length) % opts.length;
  opts.forEach((o, i) => o.setAttribute('aria-selected', String(i === activeOpt)));
  $('#q').setAttribute('aria-activedescendant', opts[activeOpt].id);
  opts[activeOpt].scrollIntoView({ block: 'nearest' });
}

function chooseSuggestion(o) {
  closeSuggest();
  if (o.type === 'topic') { learn([o.id]); setQuery(''); setTopic(o.id); scrollToResults(); }
  else { commitSearch(); openSheet(o.id, state.list); }
}

function setQuery(q) {
  state.q = q.slice(0, 100);
  const input = $('#q');
  if (input.value !== state.q) input.value = state.q;
  $('#q-clear').hidden = !state.q;
  render();
}
function setTopic(id) { state.topic = id && interestsById.has(id) ? id : null; render(); }

function commitSearch() {
  const res = state.q.trim() ? state.engine.search(state.q) : null;
  if (res && res.topics.length) learn(res.topics.slice(0, 2).map((t) => t.id));
}

function scrollToResults() {
  const target = $('#projects');
  const top = target.getBoundingClientRect().top + window.scrollY - 60;
  if (Math.abs(window.scrollY - top) > 40) window.scrollTo({ top, behavior: reduceMotion() ? 'auto' : 'smooth' });
}

function wireSearch() {
  const input = $('#q');
  const onInput = debounce(() => { setQuery(input.value); renderSuggest(); }, 90);
  input.addEventListener('input', () => { $('#q-clear').hidden = !input.value; onInput(); });
  input.addEventListener('focus', renderSuggest);
  input.addEventListener('blur', () => setTimeout(closeSuggest, 0));
  input.addEventListener('keydown', (e) => {
    const open = !$('#suggest').hidden;
    if (e.key === 'ArrowDown') { e.preventDefault(); if (!open) renderSuggest(); moveActive(1); }
    else if (e.key === 'ArrowUp') { e.preventDefault(); moveActive(-1); }
    else if (e.key === 'Enter') {
      e.preventDefault();
      setQuery(input.value);
      if (open && activeOpt >= 0) chooseSuggestion($('#suggest')._opts[activeOpt]);
      else { closeSuggest(); commitSearch(); scrollToResults(); }
    } else if (e.key === 'Escape') {
      if (open) { e.preventDefault(); closeSuggest(); } else if (input.value) { e.preventDefault(); setQuery(''); }
    }
  });
  $('#q-clear').addEventListener('click', () => { setQuery(''); input.focus(); });
  document.addEventListener('keydown', (e) => {
    if (e.key !== '/' || e.metaKey || e.ctrlKey || e.altKey) return;
    const t = e.target;
    if (t instanceof HTMLElement && (t.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(t.tagName))) return;
    if ($('#sheet').open) return;
    e.preventDefault();
    input.focus();
    input.select();
  });
  $('#sort').addEventListener('change', (e) => { state.sort = ['mixed', 'az', 'featured'].includes(e.target.value) ? e.target.value : 'mixed'; render(); });
  $('#interests-clear').addEventListener('click', () => {
    const before = { ...state.interests };
    state.interests = {}; store.del(STORE_KEY); render();
    toast('Picks cleared', { label: 'Undo', run: () => { state.interests = before; saveInterests(); render(); } });
  });
  $('#topics').addEventListener('keydown', (e) => {
    if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(e.key)) return;
    const btns = [...$('#topics').children];
    const i = btns.indexOf(document.activeElement);
    if (i < 0) return;
    e.preventDefault();
    const j = e.key === 'Home' ? 0 : e.key === 'End' ? btns.length - 1 : (i + (e.key === 'ArrowRight' ? 1 : -1) + btns.length) % btns.length;
    btns.forEach((b, k) => { b.tabIndex = k === j ? 0 : -1; });
    btns[j].focus();
    btns[j].scrollIntoView({ block: 'nearest', inline: 'nearest' });
  });
}

/* ---------- project sheet (native <dialog>) ---------- */

function openSheet(id, list) {
  const p = state.data.projects.find((x) => x.id === id);
  if (!p) return;
  state.sheetList = list && list.some((x) => x.id === id) ? list : state.data.projects;
  state.sheetIndex = state.sheetList.findIndex((x) => x.id === id);
  fillSheet(p);
  const dlg = $('#sheet');
  if (!dlg.open) {
    dlg.showModal();
    document.documentElement.classList.add('is-locked');
  }
  const hashWanted = `#p=${p.id}`;
  if (location.hash !== hashWanted) {
    if (state.pushedSheet) setUrl(hashWanted);
    else { setUrl(hashWanted, true); state.pushedSheet = true; }
  }
  $('#sheet-close').focus({ preventScroll: true });
}

function fillSheet(p) {
  const hl = null;
  const body = $('#sheet-body');
  const topics = p.tags.map((t) => {
    const b = el('button', { type: 'button', class: 'chip chip--sm', text: interestsById.get(t).label });
    b.addEventListener('click', () => { closeSheet(); setQuery(''); setTopic(t); learn([t], 0.6); scrollToResults(); });
    return b;
  });
  const paras = p.description ? p.description.split(/\n{2,}/).map((t) => el('p', { text: t.trim() })) : [];
  const links = p.links.map((l) => el('li', null, externalLink(l, 'linkrow',
    [el('span', { class: 'linkrow__icon' }, icon(l.kind)),
      el('span', { class: 'linkrow__text' }, el('span', { class: 'linkrow__label', text: l.label }), el('span', { class: 'linkrow__host', text: l.local ? 'Hosted on this site' : hostOf(l.href) })),
      icon('arrow', 'icon linkrow__arrow')],
    `${l.label}${l.local ? '' : `, ${hostOf(l.href)} (opens in new tab)`}`)));
  body.replaceChildren(
    el('div', { class: 'sheet__media' }, cover(p, 'sheet')),
    el('div', { class: 'sheet__content' },
      el('p', { class: 'eyebrow eyebrow--plain', text: topicLine(p) }),
      el('h2', { class: 'sheet__title', id: 'sheet-title' }, textWithMarks(p.title, hl)),
      el('p', { class: 'sheet__people', text: p.students.join(', ') }),
      el('p', { class: 'sheet__lede', text: p.summary }),
      paras.length ? el('div', { class: 'sheet__desc' }, paras) : null,
      links.length ? el('section', { class: 'sheet__block', 'aria-label': 'Links' }, el('h3', { class: 'sheet__h', text: 'Explore' }), el('ul', { class: 'linklist' }, links)) : null,
      el('div', { class: 'sheet__meta' },
        p.tools.length ? el('div', null, el('h3', { class: 'sheet__h', text: 'Built with' }), el('ul', { class: 'tools' }, p.tools.map((t) => el('li', { text: t })))) : null,
        topics.length ? el('div', null, el('h3', { class: 'sheet__h', text: 'Topics' }), el('div', { class: 'sheet__topics' }, topics)) : null)));
  body.scrollTop = 0;
  $('#sheet').scrollTop = 0;
  const n = state.sheetList.length;
  $('#sheet-prev').disabled = n < 2;
  $('#sheet-next').disabled = n < 2;
}

function stepSheet(d) {
  const n = state.sheetList.length;
  if (n < 2) return;
  state.sheetIndex = (state.sheetIndex + d + n) % n;
  const p = state.sheetList[state.sheetIndex];
  fillSheet(p);
  setUrl(`#p=${p.id}`);
  $('#sheet-status').textContent = `${p.title}, project ${state.sheetIndex + 1} of ${n}`;
}

function closeSheet(fromPop = false) {
  const dlg = $('#sheet');
  if (dlg.open) dlg.close();
  document.documentElement.classList.remove('is-locked');
  if (!fromPop && location.hash.startsWith('#p=')) {
    if (state.pushedSheet) { state.pushedSheet = false; history.back(); return; }
    setUrl(here() + location.search);
  }
  state.pushedSheet = false;
}

function wireSheet() {
  const dlg = $('#sheet');
  $('#sheet-close').addEventListener('click', () => closeSheet());
  $('#sheet-prev').addEventListener('click', () => stepSheet(-1));
  $('#sheet-next').addEventListener('click', () => stepSheet(1));
  dlg.addEventListener('cancel', (e) => { e.preventDefault(); closeSheet(); });
  dlg.addEventListener('click', (e) => { if (e.target === dlg) closeSheet(); });
  dlg.addEventListener('keydown', (e) => {
    if (e.target instanceof HTMLElement && e.target.closest('input,textarea,select')) return;
    if (e.key === 'ArrowRight') stepSheet(1);
    else if (e.key === 'ArrowLeft') stepSheet(-1);
  });
  $('#sheet-copy').addEventListener('click', async () => {
    const url = location.href;
    try { await navigator.clipboard.writeText(url); toast('Link copied'); } catch { toast('Copy failed. Use the address bar.'); }
  });
  window.addEventListener('popstate', () => {
    const m = /^#p=([a-z0-9-]{1,64})$/.exec(location.hash);
    if (m) openSheet(m[1], state.list);
    else if (dlg.open) { state.pushedSheet = false; closeSheet(true); }
  });
}

/* ---------- theme, toast, url ---------- */

function wireTheme() {
  const btn = $('#mode-toggle');
  const current = () => document.documentElement.getAttribute('data-theme') || (window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light');
  const label = () => btn.setAttribute('aria-label', `Mode: switch to ${current() === 'dark' ? 'light' : 'dark'}`);
  btn.addEventListener('click', () => {
    const next = current() === 'dark' ? 'light' : 'dark';
    document.documentElement.setAttribute('data-theme', next);
    store.set(THEME_KEY, next);
    label();
  });
  label();
}

let toastTimer;
function toast(msg, action) {
  const t = $('#toast');
  const parts = [el('span', { text: msg })];
  if (action) {
    const b = el('button', { type: 'button', class: 'toast__action', text: action.label });
    b.addEventListener('click', () => { action.run(); t.classList.remove('is-on'); });
    parts.push(b);
  }
  t.replaceChildren(...parts);
  t.classList.add('is-on');
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => t.classList.remove('is-on'), action ? 5000 : 2200);
}

// Same-document path, safe even if the page was reached as "//index.html" (which would parse as another host).
function here() { return location.pathname.replace(/^\/{2,}/, '/'); }
function setUrl(url, push = false) {
  try { history[push ? 'pushState' : 'replaceState'](push ? { sheet: true } : history.state, '', url); } catch { /* ignore */ }
}

function syncUrl() {
  const params = new URLSearchParams();
  if (state.q.trim()) params.set('q', state.q.trim());
  if (state.topic) params.set('topic', state.topic);
  const qs = params.toString();
  const search = qs ? `?${qs}` : '';
  if (search !== location.search) setUrl(here() + search + location.hash);
}

function readUrl() {
  const params = new URLSearchParams(location.search);
  const q = (params.get('q') || '').slice(0, 100);
  const topic = params.get('topic');
  state.q = q;
  state.topic = topic && interestsById.has(topic) ? topic : null;
  $('#q').value = q;
  $('#q-clear').hidden = !q;
}

/* ---------- boot ---------- */

async function getJSON(path) {
  const r = await fetch(path, { credentials: 'same-origin', cache: 'no-cache', headers: { Accept: 'application/json' } });
  if (!r.ok) throw new Error(`${path}: HTTP ${r.status}`);
  const type = r.headers.get('content-type') || '';
  if (!/json/.test(type)) throw new Error(`${path}: unexpected content type`);
  return r.json();
}

/* Page copy edited in the admin (data/site.json). Plain text only; the HTML defaults stay if anything is off. */
function setLeadText(node, text) {
  if (!node) return;
  const t = [...node.childNodes].find((n) => n.nodeType === Node.TEXT_NODE && n.data.trim());
  if (t) t.data = `${text} `;
}

function applyCopy(site) {
  if (!site || typeof site !== 'object') return;
  const eyebrow = str(site.eyebrow, 60);
  if (eyebrow) setLeadText(document.querySelector('.hero .eyebrow'), eyebrow);
  const headline = str(site.headline, 90);
  if (headline) {
    const m = /^([^*]*)\*([^*]+)\*([^*]*)$/.exec(headline);
    const h = $('#hero-title');
    if (m) h.replaceChildren(m[1], el('em', { text: m[2] }), m[3]);
    else h.textContent = headline.replace(/\*/g, '');
  }
  const lede = str(site.lede, 280);
  if (lede) $('#hero-lede').textContent = lede;
  const ph = str(site.searchPlaceholder, 60);
  if (ph) $('#q').placeholder = ph;
  if (Array.isArray(site.trySearches)) {
    const tries = site.trySearches.map((q) => str(q, 40)).filter(Boolean).slice(0, 8);
    if (tries.length) TRY = tries;
  }
  const note = str(site.footerNote, 200);
  if (note) setLeadText(document.querySelector('.footer p'), note);
}

function showError() {
  const grid = $('#grid');
  grid.setAttribute('aria-busy', 'false');
  grid.replaceChildren(el('div', { class: 'empty' }, el('p', { class: 'empty__title', text: 'Projects could not load.' }), el('p', { class: 'empty__body', text: 'Refresh the page, or check back soon.' })));
}

async function boot() {
  wireTheme();
  let rawProjects; let rawInterests;
  // site.json is optional: the page keeps its built-in copy if it is missing or invalid.
  const sitePromise = getJSON('data/site.json').catch(() => null);
  try { [rawProjects, rawInterests] = await Promise.all([getJSON('data/projects.json'), getJSON('data/interests.json')]); }
  catch (err) { console.error(err); applyCopy(await sitePromise); showError(); return; }
  applyCopy(await sitePromise);

  const interests = (rawInterests.interests || []).filter((i) => i && /^[a-z0-9-]{1,32}$/.test(i.id) && str(i.label, 40) && Array.isArray(i.terms))
    .map((i) => ({ id: i.id, label: i.label, terms: i.terms.filter((t) => typeof t === 'string' && t.length <= 40) }));
  interestsById = new Map(interests.map((i) => [i.id, i]));
  const ids = new Set(interests.map((i) => i.id));
  const seen = new Set();
  const projects = [];
  for (const raw of (Array.isArray(rawProjects.projects) ? rawProjects.projects : []).slice(0, 1000)) {
    const p = validateProject(raw, ids);
    if (!p || seen.has(p.id)) { console.warn('Skipped invalid project', raw && raw.id); continue; }
    seen.add(p.id);
    projects.push(p);
  }
  state.data = { projects, demo: rawProjects.demo === true, term: str(rawProjects.term, 40), generated: str(rawProjects.generated, 40) };
  const toolNames = (Array.isArray(rawInterests.tools) ? rawInterests.tools : []).filter((t) => str(t, 40));
  state.engine = createEngine(projects, interests, toolNames);
  state.interests = loadInterests();

  if (state.data.term) $('#term').textContent = state.data.term;
  if (state.data.demo) {
    document.body.classList.add('is-demo');
    $('#hero-lede').after(el('p', { class: 'demo-flag', text: 'Placeholders until the Gradescope import.' }));
  }
  if (state.data.generated) {
    const d = new Date(state.data.generated);
    if (!Number.isNaN(d.valueOf())) $('#generated').textContent = `Updated ${d.toLocaleDateString(undefined, { year: 'numeric', month: 'short', day: 'numeric' })}`;
  }

  readUrl();
  renderStats();
  renderTry();
  renderFeatured();
  wireSearch();
  wireSheet();
  render();

  const m = /^#p=([a-z0-9-]{1,64})$/.exec(location.hash);
  if (m) {
    setUrl(here() + location.search);
    openSheet(m[1], state.list);
  }
  document.documentElement.classList.add('is-ready');
}

boot();

// Exposed for tests in the dev console only; harmless in production.
export { tokenize, stem };
