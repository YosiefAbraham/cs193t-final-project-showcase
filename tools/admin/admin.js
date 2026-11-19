// Showcase admin UI. Talks only to the local admin server (tools/admin.py).
// Trusted Types are 'none': every node is built with createElement/textContent, never HTML strings.

const $ = (s, root = document) => root.querySelector(s);
const $$ = (s, root = document) => [...root.querySelectorAll(s)];

function el(tag, props, ...children) {
  const node = document.createElement(tag);
  if (props) {
    for (const [k, v] of Object.entries(props)) {
      if (v == null || v === false) continue;
      if (k === 'class') node.className = v;
      else if (k === 'text') node.textContent = v;
      else if (k.startsWith('on')) node.addEventListener(k.slice(2), v);
      else if (k === 'value') node.value = v;
      else if (k === 'checked') node.checked = !!v;
      else if (k === 'style' || k === 'innerHTML' || k === 'srcdoc') throw new Error('blocked prop ' + k);
      else node.setAttribute(k, v === true ? '' : String(v));
    }
  }
  for (const c of children.flat()) if (c != null && c !== false) node.append(c);
  return node;
}

const srOnly = (text) => el('span', { class: 'visually-hidden', text });

/* ---------- token + API ---------- */

const TOKEN_KEY = 'cs193t-admin:token';
let token = '';

function readToken() {
  const m = /^#t=([A-Za-z0-9_-]{20,128})$/.exec(location.hash);
  if (m) {
    try { sessionStorage.setItem(TOKEN_KEY, m[1]); } catch { /* private mode */ }
    history.replaceState(null, '', location.pathname + location.search); // keep the token out of the address bar
    return m[1];
  }
  try { return sessionStorage.getItem(TOKEN_KEY) || ''; } catch { return ''; }
}

class ApiError extends Error {
  constructor(status, message, data) { super(message); this.status = status; this.data = data; }
}

async function api(method, path, body, opts = {}) {
  const headers = { 'X-Admin-Token': token, Accept: 'application/json' };
  let payload;
  if (opts.raw) { headers['Content-Type'] = 'application/octet-stream'; payload = body; }
  else if (body !== undefined || method !== 'GET') { headers['Content-Type'] = 'application/json'; payload = JSON.stringify(body === undefined ? {} : body); }
  let r;
  try { r = await fetch(path, { method, headers, body: payload, credentials: 'omit', cache: 'no-store' }); }
  catch { throw new ApiError(0, 'The admin server is not responding. In the terminal, run python3 tools/admin.py again and open the new link it prints.'); }
  let data = null;
  if ((r.headers.get('content-type') || '').includes('application/json')) data = await r.json().catch(() => null);
  if (!r.ok) {
    if (r.status === 401) lock();
    throw new ApiError(r.status, (data && data.error) || `Request failed (HTTP ${r.status})`, data);
  }
  return data;
}

function lock() {
  $('#locked').hidden = false;
  $$('.view').forEach((v) => { v.hidden = true; });
  $('#tabs').hidden = true;
}

/* ---------- state ---------- */

const state = {
  data: null,          // /api/state
  sel: null,           // selected submission_id
  draft: null,         // editable copy of the selected row
  dirty: false,
  saving: false,
  filter: '',
  seg: 'all',
  copyDirty: false,
  topicsDirty: false,
};

const rows = () => (state.data && state.data.review.rows) || [];
const rev = () => (state.data ? state.data.review.rev : 'none');
const limits = () => (state.data && state.data.limits) || {};
const YES = /^(yes|y|true|1)$/i;
const isYes = (v) => YES.test(String(v || '').trim());
const splitList = (cell) => String(cell || '').split(/[;\n]/).map((s) => s.trim()).filter(Boolean);
const joinList = (list) => list.join('; ');
const plural = (n, word) => `${n} ${word}${n === 1 ? '' : 's'}`;

function slugify(text, max = 48) {
  return String(text || '').normalize('NFKD').replace(/\p{M}/gu, '').toLowerCase()
    .replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '').slice(0, max).replace(/-+$/g, '');
}

function uniqueSlug(base, taken) {
  if (!base) return '';
  let s = base; let n = 2;
  while (taken.has(s)) s = `${base.slice(0, 44).replace(/-+$/g, '')}-${n++}`;
  return s;
}

function parseLinks(cell) {
  return String(cell || '').split(/\r?\n/).map((l) => l.trim()).filter(Boolean).map((line) => {
    const parts = line.split('|').map((p) => p.trim());
    const kinds = state.data.linkKinds || [];
    if (parts.length >= 2 && kinds.includes(parts[0].toLowerCase())) return { kind: parts[0].toLowerCase(), url: parts[1], label: parts.slice(2).join(' / ') };
    return { kind: '', url: parts[0], label: parts.slice(1).join(' / ') };
  });
}

function formatLinks(links) {
  // "|" separates the cell's parts, so it can't appear inside a URL or label.
  const clean = (s) => s.trim().replace(/\|/g, '/');
  return links.filter((l) => l.url.trim()).map((l) => {
    const url = l.url.trim().replace(/\|/g, '%7C');
    return (l.kind ? `${l.kind} | ${url} | ${clean(l.label)}` : `${url} | ${clean(l.label)}`).replace(/ \| $/, '');
  }).join('\n');
}

function rowStatus(r) {
  const errs = (r._check && r._check.errors) || [];
  if (isYes(r.publish) && errs.length) return { key: 'fix', text: `Fix ${errs.length}`, cls: 'pill--fix' };
  if (isYes(r.publish)) return { key: 'yes', text: 'Will publish', cls: 'pill--yes' };
  return { key: 'no', text: 'Not published', cls: 'pill--no' };
}

/* Re-rendering replaces nodes; keep the user's place (focus, caret, scroll). */
function keepFocus(render) {
  const a = document.activeElement;
  const id = a && a.id;
  const caret = a && typeof a.selectionStart === 'number' ? [a.selectionStart, a.selectionEnd] : null;
  const y = window.scrollY;
  render();
  window.scrollTo(0, y);
  const n = id && document.getElementById(id);
  if (n && n !== document.activeElement) {
    n.focus({ preventScroll: true });
    if (caret && typeof n.setSelectionRange === 'function') { try { n.setSelectionRange(...caret); } catch { /* not a text field */ } }
  }
}

/* ---------- toast + modal ---------- */

let toastTimer;
function toast(msg, opts = {}) {
  const t = $('#toast');
  clearTimeout(toastTimer);
  const kids = [el('span', { class: 'toast__msg', text: msg })];
  if (opts.action) kids.push(el('button', { type: 'button', text: opts.action.label, onclick: () => { hideToast(); opts.action.run(); } }));
  if (opts.error) kids.push(el('button', { type: 'button', text: 'Dismiss', onclick: hideToast }));
  t.replaceChildren(...kids);
  t.classList.toggle('toast--error', !!opts.error);
  t.setAttribute('role', opts.error ? 'alert' : 'status');
  t.classList.add('is-shown');
  // Errors stay until dismissed; other messages fade, but never while hovered or focused.
  if (!opts.error) schedHide(opts.action ? 9000 : 4500);
}
function schedHide(ms) {
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => {
    const t = $('#toast');
    if (t.matches(':hover, :focus-within')) schedHide(1500); else hideToast();
  }, ms);
}
function hideToast() { clearTimeout(toastTimer); $('#toast').classList.remove('is-shown'); }

// Resolves with the value of the clicked button, or 'cancel' on Esc. The choice is taken at click time,
// so a slow 'close' event can never report the wrong answer, and a newer dialog cancels an older one.
let askResolve = null;
let askOpener = null;
function settleAsk(value) {
  const r = askResolve;
  askResolve = null;
  const d = $('#modal');
  if (d.open) d.close();
  if (askOpener && askOpener.isConnected && typeof askOpener.focus === 'function') askOpener.focus({ preventScroll: true });
  if (r) r(value);
}

function ask(title, body, buttons) {
  const d = $('#modal');
  if (askResolve) { const r = askResolve; askResolve = null; r('cancel'); }
  askOpener = document.activeElement;
  $('#modal-title').textContent = title;
  const b = $('#modal-body');
  if (typeof body === 'string') b.replaceChildren(body); else b.replaceChildren(...[body].flat());
  $('#modal-actions').replaceChildren(...buttons.map((x) => el('button', { type: 'button', class: `btn ${x.cls || 'btn--ghost'}`, value: x.value, text: x.label, onclick: () => settleAsk(x.value) })));
  return new Promise((resolve) => {
    askResolve = resolve;
    if (!d.open) d.showModal();
    // Destructive choices start on Cancel; otherwise on the primary action.
    const danger = $('#modal-actions .btn--danger');
    const first = danger ? $('#modal-actions button[value="cancel"]') : $('#modal-actions .btn--primary');
    (first || $('#modal-actions button')).focus();
  });
}

let conflictOpen = false;
function conflict(what) {
  if (conflictOpen) return Promise.resolve();
  conflictOpen = true;
  return ask(`${what} changed on disk`, `Someone (or a spreadsheet app) changed it after this page loaded. Reload to get the latest version. You can keep the edits you have open here and save them on top.`, [
    { label: 'Discard my edits', value: 'discard' }, { label: 'Reload, keep my edits', value: 'keep', cls: 'btn--primary' },
  ]).then(async (v) => {
    conflictOpen = false;
    if (v === 'cancel') return;
    if (v === 'discard') { state.dirty = false; state.copyDirty = false; state.topicsDirty = false; }
    try { await loadState(true); toast(v === 'keep' ? 'Reloaded. Your edits are still here; save again.' : 'Reloaded the latest version.'); }
    catch (e) { showError(e); }
  });
}

function showError(err, what = 'The review sheet') {
  if (err instanceof ApiError && err.status === 409) { conflict(what); return; }
  toast(err && err.message ? err.message : 'Something went wrong.', { error: true });
  console.error(err);
}

/* ---------- tabs ---------- */

function selectTab(id, focus) {
  $$('.tab').forEach((t) => {
    const on = t.id === id;
    t.setAttribute('aria-selected', String(on));
    t.tabIndex = on ? 0 : -1;
    $('#' + t.getAttribute('aria-controls')).hidden = !on;
    if (on && focus) t.focus();
  });
  if (id === 'tab-publish') { renderTally(); loadBackups(); }
}

function markTab(id, dirty) {
  const t = $('#' + id);
  const dot = $('.tab__dot', t);
  if (dirty && !dot) t.append(el('span', { class: 'tab__dot', 'aria-hidden': 'true', text: '●' }), el('span', { class: 'visually-hidden tab__sr', text: ' (unsaved)' }));
  if (!dirty && dot) { dot.remove(); $('.tab__sr', t)?.remove(); }
}

function wireTabs() {
  const tabs = $$('.tab');
  tabs.forEach((t, i) => {
    t.addEventListener('click', () => selectTab(t.id));
    t.addEventListener('keydown', (e) => {
      const d = { ArrowRight: 1, ArrowLeft: -1 }[e.key];
      if (d) { e.preventDefault(); selectTab(tabs[(i + d + tabs.length) % tabs.length].id, true); }
      if (e.key === 'Home') { e.preventDefault(); selectTab(tabs[0].id, true); }
      if (e.key === 'End') { e.preventDefault(); selectTab(tabs[tabs.length - 1].id, true); }
    });
  });
}

/* ---------- projects: list ---------- */

// kind 'thumbnail' = the student's card image; 'poster' = the poster PDF or image.
function previewUrl(r, kind = 'poster') {
  const route = kind === 'thumbnail' ? 'thumbnail-preview' : 'preview';
  const file = kind === 'thumbnail' ? r.thumbnail_file : r.poster_file;
  return `/api/rows/${encodeURIComponent(r.submission_id)}/${route}?t=${encodeURIComponent(token)}&v=${encodeURIComponent(file || '')}`;
}

function thumb(r, cls) {
  // The list shows what the public card will show: the card image, else the generated cover's initial.
  if (!r.thumbnail_file) return el('span', { class: `${cls} ${cls}--blank`, 'aria-hidden': 'true', text: (r.title || '?').trim().charAt(0).toUpperCase() || '?' });
  const img = el('img', { class: cls, src: previewUrl(r, 'thumbnail'), alt: '', loading: 'lazy', decoding: 'async' });
  img.addEventListener('error', () => img.replaceWith(el('span', { class: `${cls} ${cls}--blank`, 'aria-hidden': 'true', text: '–' })), { once: true });
  return img;
}

function filteredRows() {
  const q = state.filter.trim().toLowerCase();
  return rows().filter((r) => {
    if (state.seg !== 'all' && rowStatus(r).key !== state.seg) return false;
    if (!q) return true;
    return `${r.title} ${r.students} ${r.id}`.toLowerCase().includes(q);
  }).sort((a, b) => (a.title || '~').localeCompare(b.title || '~', undefined, { sensitivity: 'base' }));
}

const dirtyDot = () => el('span', { class: 'row__dirty' }, el('span', { 'aria-hidden': 'true', text: '● ' }), srOnly('Unsaved: '));

function renderList() {
  const all = rows();
  const exists = state.data.review.exists;
  $('#row-count').textContent = exists ? `· ${all.length}` : '';
  const list = filteredRows();
  $('#rows-status').textContent = exists ? `${list.length} of ${plural(all.length, 'project')} shown` : '';
  $('#rows').replaceChildren(...list.map((r) => {
    const st = rowStatus(r);
    const current = r.submission_id === state.sel;
    const title = (current && state.draft ? state.draft.title : r.title) || 'Untitled project';
    const errs = (r._check && r._check.errors.length) || 0;
    const btn = el('button', { type: 'button', class: 'row', id: `row-${r.submission_id}`, 'aria-current': current ? 'true' : null, 'data-sid': r.submission_id },
      thumb(r, 'row__thumb'),
      el('span', { class: 'row__text' },
        el('span', { class: 'row__title' }, current && state.dirty ? dirtyDot() : null, title),
        el('span', { class: 'row__who', text: (splitList(r.students).join(', ') || 'No names yet') + (!isYes(r.publish) && errs ? ` · ${errs} to fix` : '') })),
      el('span', { class: `pill ${st.cls}`, text: st.text }));
    btn.addEventListener('click', () => choose(r.submission_id));
    return el('li', null, btn);
  }));
  const empty = $('#rows-empty');
  if (!exists) {
    const demo = state.data.published && state.data.published.demo;
    empty.replaceChildren(
      el('p', { class: 'list__lead', text: 'No review sheet yet.' }),
      el('ol', { class: 'firstrun' },
        el('li', { text: 'Import the Gradescope submissions zip.' }),
        el('li', { text: 'Turn on Publish for students who agreed.' }),
        el('li', { text: 'Fix anything flagged, then build.' }),
        el('li', { text: 'Copy showcase/ to the course site.' })),
      el('div', { class: 'row-actions' },
        el('button', { type: 'button', class: 'btn btn--primary btn--sm', text: 'Import from Gradescope', onclick: () => selectTab('tab-publish', true) }),
        el('button', { type: 'button', class: 'btn btn--ghost btn--sm', text: 'Copy in the live projects', onclick: seed })),
      el('p', { class: 'field__hint', text: demo ? 'The live page shows sample projects. Copying them in is a safe way to try the editor; they are marked “sample data”.' : 'Copying in the live projects lets you edit what is on the site now.' }));
    empty.hidden = false;
  } else if (!list.length) {
    empty.replaceChildren(el('p', { class: 'muted', text: all.length ? 'Nothing matches this filter.' : 'No projects yet. Add one, or import from Gradescope.' }));
    empty.hidden = false;
  } else empty.hidden = true;
}

function markListDirty() {
  const b = $(`.row[data-sid="${CSS.escape(state.sel || '')}"] .row__title`);
  if (!b) return;
  const dot = $('.row__dirty', b);
  if (state.dirty && !dot) b.prepend(dirtyDot());
  if (!state.dirty && dot) dot.remove();
  markTab('tab-projects', state.dirty);
}

async function seed() {
  try {
    await api('POST', '/api/seed', {});
    toast('Copied the live projects into the review sheet.');
    await loadState(true);
  } catch (e) { showError(e); }
}

function resetDraft() {
  const r = rows().find((x) => x.submission_id === state.sel);
  state.draft = r ? { ...r } : null;
  state.dirty = false;
  markListDirty();
}

async function guardDirty() {
  if (!state.dirty) return true;
  const v = await ask('Save your changes?', `“${state.draft.title || 'Untitled project'}” has unsaved edits.`, [
    { label: 'Cancel', value: 'cancel' }, { label: 'Discard', value: 'discard' }, { label: 'Save', value: 'save', cls: 'btn--primary' },
  ]);
  if (v === 'save') return saveRow();
  if (v === 'discard') { resetDraft(); renderEditor(); return true; }
  return false;
}

async function choose(sid) {
  if (sid === state.sel) return;
  if (!(await guardDirty())) return;
  state.sel = sid;
  resetDraft();
  renderList();
  renderEditor();
  const narrow = matchMedia('(max-width: 860px)').matches;
  if (narrow) $('#editor').scrollIntoView({ block: 'start' });
  $('#editor-title')?.focus({ preventScroll: !narrow });
}

async function addRow() {
  if (!(await guardDirty())) return;
  try {
    const res = await api('POST', '/api/rows', { rev: rev(), row: { publish: 'no' } });
    applyRow(res);
    state.sel = res.row.submission_id;
    state.draft = { ...res.row };
    state.dirty = false;
    state.seg = 'all'; syncSeg();
    renderList();
    renderEditor();
    $('#f-title')?.focus();
  } catch (e) { showError(e); }
}

function applyRow(res) {
  state.data.review.rev = res.rev;
  state.data.review.exists = true;
  const list = state.data.review.rows;
  const i = list.findIndex((x) => x.submission_id === res.row.submission_id);
  if (i >= 0) list[i] = res.row; else list.push(res.row);
}

/* ---------- projects: editor ---------- */

function setDraft(key, value) {
  state.draft[key] = value;
  if (!state.dirty) { state.dirty = true; markListDirty(); }
  const s = $('#row-state');
  if (s) { s.textContent = 'Unsaved changes'; s.classList.add('is-dirty'); }
}

function counter(input, max) {
  const c = el('span', { class: 'field__count', 'aria-hidden': 'true' });
  const sr = el('span', { class: 'visually-hidden', id: `${input.id}-over` });
  const upd = () => {
    const n = input.value.length;
    c.textContent = `${n}/${max}`;
    c.classList.toggle('is-over', n > max);
    input.setAttribute('aria-invalid', String(n > max));
    sr.textContent = n > max ? `${n - max} characters over the ${max} limit.` : '';
  };
  input.addEventListener('input', upd);
  upd();
  return [c, sr];
}

function field({ id, label, hint, input, max }) {
  input.id = id;
  const desc = [hint ? `${id}-hint` : null, max ? `${id}-over` : null].filter(Boolean).join(' ');
  if (desc) input.setAttribute('aria-describedby', desc);
  const [count, over] = max ? counter(input, max) : [null, null];
  return el('div', { class: 'field' },
    el('div', { class: 'field__top' }, el('label', { class: 'label', for: id, text: label }), count),
    input, over,
    hint ? el('p', { class: 'field__hint', id: `${id}-hint`, text: hint }) : null);
}

function textInput(key, attrs = {}) {
  const i = el('input', { class: 'input', type: 'text', value: state.draft[key] || '', autocomplete: 'off', ...attrs });
  i.addEventListener('input', () => setDraft(key, i.value));
  return i;
}

function textArea(key, rowsN, attrs = {}) {
  const t = el('textarea', { class: 'input', rows: String(rowsN), ...attrs });
  t.value = state.draft[key] || '';
  t.addEventListener('input', () => setDraft(key, t.value));
  return t;
}

function checksCallout(r) {
  const c = r._check || { errors: [], warnings: [] };
  const out = [];
  if (c.errors.length) {
    out.push(el('div', { class: 'callout callout--bad' },
      el('strong', { text: isYes(r.publish) ? 'Fix before publishing' : 'Needs fixes before it can be published' }),
      el('ul', null, c.errors.map((e) => el('li', { text: e })))));
  }
  if (c.warnings.length) {
    out.push(el('div', { class: 'callout callout--warn' }, el('strong', { text: 'Heads up' }), el('ul', null, c.warnings.map((w) => el('li', { text: w })))));
  }
  return out;
}

function linksEditor() {
  const max = limits().links || 6;
  const kinds = state.data.linkKinds || [];
  const links = parseLinks(state.draft.links);
  const box = el('div', { class: 'links' });
  const add = el('button', { type: 'button', class: 'btn btn--ghost btn--sm', id: 'link-add', text: 'Add link' });
  const commit = () => setDraft('links', formatLinks(links));
  const draw = () => {
    box.replaceChildren(...links.map((l, i) => {
      const n = i + 1;
      const kind = el('select', { class: 'input', id: `link-${i}-kind`, 'aria-label': `Link ${n} kind` },
        el('option', { value: '', text: 'Auto' }), kinds.map((k) => el('option', { value: k, text: k })));
      kind.value = l.kind;
      const url = el('input', { class: 'input link__url', id: `link-${i}-url`, type: 'url', inputmode: 'url', value: l.url, placeholder: 'https://…', 'aria-label': `Link ${n} address`, spellcheck: 'false', autocomplete: 'off' });
      const label = el('input', { class: 'input', id: `link-${i}-label`, type: 'text', value: l.label, placeholder: 'Label', maxlength: String(limits().label || 60), 'aria-label': `Link ${n} label` });
      const del = el('button', { type: 'button', class: 'btn btn--ghost btn--icon', 'aria-label': `Remove link ${n}`, title: 'Remove link', text: '×' });
      const flag = () => url.setAttribute('aria-invalid', String(!!url.value.trim() && !/^https:\/\/[^\s]+$/i.test(url.value.trim())));
      kind.addEventListener('change', () => { l.kind = kind.value; commit(); });
      url.addEventListener('input', () => { l.url = url.value; flag(); commit(); });
      label.addEventListener('input', () => { l.label = label.value; commit(); });
      del.addEventListener('click', () => { links.splice(i, 1); commit(); draw(); ($(`#link-${Math.min(i, links.length - 1)}-url`) || add).focus(); });
      flag();
      return el('div', { class: 'link', role: 'group', 'aria-label': `Link ${n}` }, kind, url, label, del);
    }));
    add.disabled = links.length >= max;
  };
  add.addEventListener('click', () => { links.push({ kind: '', url: '', label: '' }); draw(); $(`#link-${links.length - 1}-url`)?.focus(); });
  draw();
  return el('div', { class: 'field', role: 'group', 'aria-labelledby': 'links-label' },
    el('p', { class: 'label', id: 'links-label', text: `Links (up to ${max})` }), box, add,
    el('p', { class: 'field__hint', text: 'https:// only. Empty rows are ignored. Kind “Auto” guesses from the address (GitHub → Code, claude.ai → Artifact).' }));
}

function chipGroup({ legend, options, selected, max, name, onchange }) {
  const valid = new Set(options.map((o) => o.value));
  const set = new Set(selected.filter((v) => valid.has(v)));
  const dropped = selected.filter((v) => !valid.has(v));
  const wrap = el('div', { class: 'chips' });
  const count = max ? el('span', { class: 'field__count', text: '' }) : null;
  const hint = max ? el('p', { class: 'field__hint', id: `${name}-hint` }) : null;
  const sync = () => {
    $$('input', wrap).forEach((i) => { i.disabled = !!max && !i.checked && set.size >= max; });
    if (count) count.textContent = `${set.size} of ${max}`;
    if (hint) hint.textContent = set.size >= max ? `That's the limit of ${max}. Unselect one to pick another.` : '';
  };
  for (const o of options) {
    const input = el('input', { type: 'checkbox', id: `chip-${name}-${o.value}`, name, value: o.value, checked: set.has(o.value), 'aria-describedby': hint ? `${name}-hint` : null });
    input.addEventListener('change', () => { if (input.checked) set.add(o.value); else set.delete(o.value); sync(); onchange([...set]); });
    wrap.append(el('label', { class: 'chip' }, input, el('span', { text: o.label })));
  }
  sync();
  return el('fieldset', { class: 'field' },
    el('legend', { class: 'label legend' }, el('span', { text: legend }), count),
    wrap, hint,
    dropped.length ? el('p', { class: 'field__hint field__hint--warn', text: `Removed topic${dropped.length > 1 ? 's' : ''} ${dropped.join(', ')} will be dropped when you save.` }) : null);
}

function cardImageBox(r) {
  const has = !!state.draft.thumbnail_file;
  const file = el('input', { type: 'file', id: 'thumb-file', accept: '.png,.jpg,.jpeg,.webp,.gif,image/png,image/jpeg,image/webp,image/gif' });
  file.addEventListener('change', () => { if (file.files[0]) uploadImage('thumbnail', file.files[0]); file.value = ''; });
  let img = null;
  if (has) {
    img = el('img', { class: 'poster__img', src: previewUrl(r, 'thumbnail'), alt: `Card image for ${r.title || 'this project'}` });
    img.addEventListener('error', () => img.replaceWith(el('div', { class: 'poster__none', text: 'No preview: this image could not be read. Upload a PNG or JPG.' })), { once: true });
  }
  const remove = has ? el('button', { type: 'button', class: 'btn btn--ghost btn--sm', text: 'Remove', onclick: removeCardImage }) : null;
  return el('div', { class: 'panel poster' },
    el('p', { class: 'label', text: 'Card image' }),
    img || el('div', { class: 'poster__none poster__none--gen', text: 'No card image. The card uses a generated cover with the title’s initials.' }),
    has ? el('p', { class: 'poster__file', text: state.draft.thumbnail_file }) : null,
    el('div', { class: 'row-actions' },
      el('label', { class: 'btn btn--ghost btn--sm file' }, file, el('span', { text: has ? 'Replace…' : 'Upload card image…' })),
      remove),
    el('p', { class: 'field__hint', text: 'Shown on the project’s card, cropped like this preview. A landscape screenshot, at least 1200×800, works best. Check it for emails, IDs or private details first.' }));
}

async function removeCardImage() {
  const v = await ask('Remove the card image?', 'The card goes back to a generated cover at the next build. The uploaded file is deleted unless another project uses it.', [
    { label: 'Cancel', value: 'cancel' }, { label: 'Remove', value: 'rm', cls: 'btn--danger' },
  ]);
  if (v !== 'rm') return;
  if (state.dirty && !(await saveRow())) return;
  try {
    const res = await api('DELETE', `/api/rows/${encodeURIComponent(state.sel)}/thumbnail?rev=${encodeURIComponent(rev())}`);
    applyRow(res);
    state.draft = { ...state.draft, thumbnail_file: res.row.thumbnail_file, _check: res.row._check };
    keepFocus(() => { renderList(); renderEditor(); });
    $('#thumb-file')?.focus();
    toast('Card image removed.');
  } catch (e) { showError(e); }
}

function posterBox(r) {
  const has = !!state.draft.poster_file;
  const file = el('input', { type: 'file', id: 'poster-file', accept: '.pdf,.png,.jpg,.jpeg,.webp,.gif,application/pdf,image/*' });
  file.addEventListener('change', () => { if (file.files[0]) uploadImage('poster', file.files[0]); file.value = ''; });
  const include = el('input', { type: 'checkbox', id: 'f-include-poster', checked: isYes(state.draft.include_poster) });
  include.addEventListener('change', () => setDraft('include_poster', include.checked ? 'yes' : 'no'));
  let img = null;
  if (has) {
    img = el('img', { class: 'poster__img', src: previewUrl(r), alt: `Poster preview for ${r.title || 'this project'}` });
    img.addEventListener('error', () => img.replaceWith(el('div', { class: 'poster__none', text: 'No preview (the poster file could not be rendered).' })), { once: true });
  }
  return el('div', { class: 'panel poster' },
    el('p', { class: 'label', text: 'Poster' }),
    img || el('div', { class: 'poster__none', text: 'No poster yet. Upload the poster PDF (or an image of it).' }),
    has ? el('p', { class: 'poster__file', text: state.draft.poster_file }) : null,
    el('label', { class: 'btn btn--ghost btn--sm file' }, file, el('span', { text: has ? 'Replace poster…' : 'Upload poster…' })),
    el('label', { class: 'check' }, include, el('span', { text: 'Publish the poster as an image (a “Poster” link)' })),
    has ? el('p', { class: 'field__hint', text: 'The poster is published as an image. Check it for emails, phone numbers or student IDs first.' }) : null);
}

function renderEditor() {
  const ed = $('#editor');
  const r = rows().find((x) => x.submission_id === state.sel);
  if (!r || !state.draft) {
    const n = rows().length;
    ed.replaceChildren(el('div', { class: 'panel announce' },
      el('h1', { text: n ? 'Pick a project to edit' : 'Showcase admin' }),
      el('p', { text: n ? 'Choose a project from the list. Turn on Publish only for projects whose students agreed to be featured.' : 'Projects, consent, page copy and topics all live here. Nothing reaches the public site until you build it and copy it to the course site.' })));
    return;
  }
  const d = state.draft;
  const L = limits();
  const interests = state.data.interests.interests || [];
  const knownTools = state.data.interests.tools || [];
  const toolsNow = splitList(d.tools);
  const otherTools = toolsNow.filter((t) => !knownTools.some((k) => k.toLowerCase() === t.toLowerCase()));

  const on = isYes(d.publish);
  const pub = el('input', { type: 'checkbox', id: 'f-publish', role: 'switch', checked: on, 'aria-describedby': 'f-publish-help' });
  const pubText = el('span', { class: 'switch__text', 'aria-hidden': 'true', text: on ? 'On' : 'Off' });
  const consent = el('div', { class: `panel announce consent ${on ? '' : 'is-off'}` },
    el('div', { class: 'switch-wrap' }, el('span', { class: 'switch' }, pub, el('span')), pubText),
    el('div', null,
      el('label', { class: 'consent__title', for: 'f-publish', text: 'Publish on the showcase' }),
      el('p', { class: 'consent__body', id: 'f-publish-help', text: 'Turn on only if every student on this project agreed to have it featured publicly. Takes effect when you save and build; turning it off removes the project at the next build.' })));
  pub.addEventListener('change', () => { setDraft('publish', pub.checked ? 'yes' : 'no'); consent.classList.toggle('is-off', !pub.checked); pubText.textContent = pub.checked ? 'On' : 'Off'; });

  const featured = el('input', { type: 'checkbox', id: 'f-featured', checked: isYes(d.featured) });
  featured.addEventListener('change', () => setDraft('featured', featured.checked ? 'yes' : 'no'));

  const students = el('textarea', { class: 'input', rows: '3', spellcheck: 'false' });
  students.value = splitList(d.students).join('\n');
  students.addEventListener('input', () => setDraft('students', joinList(splitList(students.value))));

  const keywords = el('input', { class: 'input', type: 'text', value: splitList(d.keywords).join(', '), autocomplete: 'off' });
  keywords.addEventListener('input', () => setDraft('keywords', joinList(keywords.value.split(',').map((s) => s.trim()).filter(Boolean))));

  let toolSel = toolsNow.filter((t) => !otherTools.includes(t)).map((t) => knownTools.find((k) => k.toLowerCase() === t.toLowerCase()) || t);
  let toolOther = otherTools;
  const other = el('input', { class: 'input', type: 'text', id: 'f-tools-other', value: otherTools.join(', '), placeholder: 'Other tools, comma separated', 'aria-label': 'Other AI tools, comma separated', autocomplete: 'off' });
  const commitTools = () => setDraft('tools', joinList([...toolSel, ...toolOther]));
  other.addEventListener('input', () => { toolOther = other.value.split(',').map((s) => s.trim()).filter(Boolean); commitTools(); });

  const idInput = textInput('id', { spellcheck: 'false', class: 'input topic__id' });

  const save = el('button', { class: 'btn btn--primary', type: 'submit', id: 'row-save', text: 'Save' });
  const discard = el('button', { class: 'btn btn--ghost', type: 'button', text: 'Discard' });
  discard.addEventListener('click', () => { resetDraft(); renderEditor(); $('#editor-title')?.focus(); toast('Changes discarded.'); });
  const del = el('button', { class: 'btn btn--danger btn--sm', type: 'button', text: 'Delete project…' });
  del.addEventListener('click', deleteRow);
  const back = el('button', { class: 'btn btn--ghost btn--sm editor__back', type: 'button', text: '← All projects' });
  back.addEventListener('click', () => { const b = $(`#row-${CSS.escape(state.sel)}`); (b || $('#row-filter')).scrollIntoView({ block: 'center' }); (b || $('#row-filter')).focus({ preventScroll: true }); });

  const form = el('form', { class: 'editor__form', novalidate: true, 'aria-labelledby': 'editor-title' },
    back,
    el('div', { class: 'editor__head' },
      el('div', null,
        el('h1', { id: 'editor-title', tabindex: '-1', text: d.title || 'Untitled project' }),
        el('p', { class: 'editor__meta', text: r.submission_id.startsWith('manual-') ? 'Added by staff' : `Gradescope submission ${r.submission_id}` })),
      el('a', { class: 'btn btn--ghost btn--sm', href: d.id ? `/site/#p=${encodeURIComponent(d.id)}` : '/site/', target: '_blank', rel: 'noopener', text: 'View on site' })),
    checksCallout(r),
    el('div', { class: 'editor__grid' },
      el('div', null,
        consent,
        field({ id: 'f-title', label: 'Title', input: textInput('title'), max: L.title || 90 }),
        field({ id: 'f-students', label: 'Students', hint: 'One display name per line, as they want to be credited. Never emails or SUNet IDs.', input: students }),
        field({ id: 'f-summary', label: 'Summary', hint: 'One or two sentences. Shown on the card.', input: textArea('summary', 3), max: L.summary || 280 }),
        field({ id: 'f-description', label: 'Description', hint: 'Optional. Shown when someone opens the project. Blank line = new paragraph.', input: textArea('description', 6), max: L.description || 1500 }),
        linksEditor(),
        chipGroup({ legend: 'Topics', name: 'tags', max: L.tags || 4, options: interests.map((i) => ({ value: i.id, label: i.label })), selected: splitList(d.tags).map((t) => t.toLowerCase()), onchange: (v) => setDraft('tags', joinList(v)) }),
        chipGroup({ legend: 'AI tools used', name: 'tools', options: knownTools.map((t) => ({ value: t, label: t })), selected: toolSel, onchange: (v) => { toolSel = v; commitTools(); } }),
        el('div', { class: 'field' }, other),
        field({ id: 'f-keywords', label: 'Extra search words', hint: 'Comma separated, e.g. meal prep, grocery list. Helps people find it.', input: keywords })),
      el('div', { class: 'editor__side' },
        cardImageBox(r),
        posterBox(r),
        el('div', { class: 'panel' },
          el('label', { class: 'check' }, featured, el('span', { text: 'Feature in the carousel' })),
          field({ id: 'f-id', label: 'Link ID', hint: 'Used in share links (#p=…). Lowercase letters, numbers and dashes. Changing it breaks links people already shared.', input: idInput })),
        el('div', { class: 'panel' },
          field({ id: 'f-notes', label: 'Staff notes', hint: 'Private. Never published.', input: textArea('notes', 4) }),
          el('div', { class: 'danger-zone' }, del)))),
    el('div', { class: 'savebar' },
      el('span', { class: 'savebar__state', id: 'row-state', role: 'status', text: state.dirty ? 'Unsaved changes' : 'All changes saved' }),
      discard, save));
  if (state.dirty) $('#row-state', form).classList.add('is-dirty');
  form.addEventListener('submit', (e) => { e.preventDefault(); saveRow(); });
  ed.replaceChildren(form);
}

async function saveRow() {
  if (!state.draft) return true;
  if (state.saving) return false;
  const d = { ...state.draft };
  delete d._check;
  delete d.submission_id;
  let noId = false;
  if (!d.id && d.title) {
    const taken = new Set(rows().filter((x) => x.submission_id !== state.sel).map((x) => x.id).filter(Boolean));
    d.id = uniqueSlug(slugify(d.title), taken);
    noId = !d.id;
  }
  const sent = JSON.stringify(state.draft);
  const sid = state.sel;
  state.saving = true;
  $('#row-save')?.setAttribute('aria-disabled', 'true');
  try {
    const res = await api('PUT', `/api/rows/${encodeURIComponent(sid)}`, { rev: rev(), row: d });
    applyRow(res);
    if (state.sel === sid) {
      // Keep anything typed while the request was in flight.
      if (JSON.stringify(state.draft) === sent) { state.draft = { ...res.row }; state.dirty = false; }
      else { state.draft = { ...state.draft, id: res.row.id }; }
    }
    keepFocus(() => { renderList(); renderEditor(); });
    markTab('tab-projects', state.dirty);
    const errs = (res.row._check && res.row._check.errors) || [];
    if (noId) { toast('Saved. Add a Link ID (lowercase letters and numbers); the title has none to use.', { error: true }); $('#f-id')?.focus(); }
    else toast(errs.length ? `Saved. ${plural(errs.length, 'thing')} to fix before it can publish.` : 'Saved.');
    return true;
  } catch (e) { showError(e); return false; }
  finally { state.saving = false; $('#row-save')?.removeAttribute('aria-disabled'); }
}

async function uploadImage(kind, file) {
  const card = kind === 'thumbnail';
  const maxMb = card ? 25 : 100;
  if (file.size > maxMb * 1024 * 1024) { toast(`That file is over ${maxMb} MB.`, { error: true }); return; }
  if (state.dirty && !(await saveRow())) return;
  toast(card ? 'Uploading card image…' : 'Uploading poster…');
  try {
    const res = await api('POST', `/api/rows/${encodeURIComponent(state.sel)}/${kind}?rev=${encodeURIComponent(rev())}`, file, { raw: true });
    applyRow(res);
    const keep = card ? { thumbnail_file: res.row.thumbnail_file } : { poster_file: res.row.poster_file, include_poster: res.row.include_poster };
    state.draft = { ...state.draft, ...keep, _check: res.row._check };
    keepFocus(() => { renderList(); renderEditor(); });
    toast(card ? 'Card image uploaded. It shows on the card after the next build.' : 'Poster uploaded.');
  } catch (e) {
    if (e.status === 415) toast(card ? 'Card images must be PNG, JPG, WebP or GIF.' : 'Posters must be a PDF or an image.', { error: true });
    else showError(e);
  }
}

async function deleteRow() {
  const r = rows().find((x) => x.submission_id === state.sel);
  if (!r) return;
  const fromGs = !r.submission_id.startsWith('manual-');
  const v = await ask(`Delete “${r.title || 'Untitled project'}”?`, [
    el('span', { text: 'It is removed from the review sheet. A backup is kept on the Publish tab. ' }),
    el('span', { text: fromGs ? 'It came from Gradescope, so a re-import brings it back (not published). To just take it off the site, turn off Publish instead.' : 'To just take it off the site, turn off Publish instead.' }),
  ], [{ label: 'Cancel', value: 'cancel' }, { label: 'Delete', value: 'delete', cls: 'btn--danger' }]);
  if (v !== 'delete') return;
  try {
    const res = await api('DELETE', `/api/rows/${encodeURIComponent(r.submission_id)}?rev=${encodeURIComponent(rev())}`);
    state.data.review.rev = res.rev;
    state.data.review.rows = rows().filter((x) => x.submission_id !== r.submission_id);
    state.sel = null; state.draft = null; state.dirty = false;
    markTab('tab-projects', false);
    renderList(); renderEditor();
    $('#row-filter').focus();
    toast('Project deleted. Restore it from Backups if needed.');
  } catch (e) { showError(e); }
}

function syncSeg() {
  $$('#row-seg button').forEach((b) => { const on = b.dataset.v === state.seg; b.setAttribute('aria-checked', String(on)); b.tabIndex = on ? 0 : -1; });
}

function wireProjects() {
  $('#add-row').addEventListener('click', addRow);
  $('#row-filter').addEventListener('input', (e) => { state.filter = e.target.value; renderList(); });
  const segs = $$('#row-seg button');
  syncSeg();
  segs.forEach((b, i) => {
    b.addEventListener('click', () => { state.seg = b.dataset.v; syncSeg(); renderList(); });
    b.addEventListener('keydown', (e) => {
      const d = { ArrowRight: 1, ArrowDown: 1, ArrowLeft: -1, ArrowUp: -1 }[e.key];
      if (!d) return;
      e.preventDefault();
      const n = segs[(i + d + segs.length) % segs.length];
      n.focus(); n.click();
    });
  });
}

/* ---------- page copy ---------- */

const COPY_FIELDS = [
  { key: 'headline', label: 'Headline', max: 90, hint: 'Wrap one word in *asterisks* to set it in italics, like “What students built *with* AI.”' },
  { key: 'lede', label: 'Intro', max: 280, area: 3 },
  { key: 'eyebrow', label: 'Label above the headline', max: 60 },
  { key: 'term', label: 'Term', max: 40, hint: 'Shown next to the label. Used when you build.' },
  { key: 'searchPlaceholder', label: 'Search box hint', max: 60 },
  { key: 'trySearches', label: '“Try” searches', area: 4, list: true, hint: 'One per line: up to 8, each up to 40 characters. Only ones with results are shown (first 6).' },
  { key: 'footerNote', label: 'Footer note', max: 200, area: 2, hint: 'The “Contact course staff” link is added after it.' },
];
let copyDraft = null;

function renderCopy() {
  copyDraft = { ...state.data.site, trySearches: [...(state.data.site.trySearches || [])] };
  $('#copy-fields').replaceChildren(...COPY_FIELDS.map((f) => {
    const val = f.list ? copyDraft[f.key].join('\n') : (copyDraft[f.key] || '');
    const input = f.area ? el('textarea', { class: 'input', rows: String(f.area) }) : el('input', { class: 'input', type: 'text', autocomplete: 'off' });
    input.value = val;
    input.addEventListener('input', () => {
      copyDraft[f.key] = f.list ? input.value.split('\n').map((s) => s.trim()).filter(Boolean) : input.value;
      setCopyDirty(true);
      renderCopyPreview();
    });
    return field({ id: `c-${f.key}`, label: f.label, hint: f.hint, input, max: f.max });
  }));
  setCopyDirty(false);
  $('#copy-errors').hidden = true;
  renderCopyPreview();
}

function setCopyDirty(v) {
  state.copyDirty = v;
  const s = $('#copy-state');
  s.textContent = v ? 'Unsaved changes' : 'All changes saved';
  s.classList.toggle('is-dirty', v);
  markTab('tab-copy', v);
}

function renderCopyPreview() {
  const c = copyDraft;
  $('#pv-eyebrow').textContent = `${c.eyebrow || ''} / ${c.term || ''}`;
  const m = /^([^*]*)\*([^*]+)\*([^*]*)$/.exec(c.headline || '');
  $('#pv-title').replaceChildren(...(m ? [m[1], el('em', { text: m[2] }), m[3]] : [(c.headline || '').replace(/\*/g, '')]));
  $('#pv-lede').textContent = c.lede || '';
  $('#pv-search').textContent = c.searchPlaceholder || '';
  $('#pv-try').replaceChildren(...(c.trySearches || []).slice(0, 6).map((t) => el('span', { text: t })));
  $('#pv-footer').replaceChildren(`${c.footerNote || ''} `, el('span', { class: 'preview__link', text: 'Contact course staff' }), '.');
}

function formErrors(box, err) {
  const items = String(err.message).split(/\s*•\s*/).map((s) => s.replace(/^Invalid [a-z ]+:\s*/i, '')).filter(Boolean);
  box.replaceChildren(el('div', { class: 'callout callout--bad', role: 'alert' }, el('strong', { text: 'Not saved. Fix these first:' }), el('ul', null, items.map((x) => el('li', { text: x })))));
  box.hidden = false;
}

function wireCopy() {
  $('#copy-form').addEventListener('submit', async (e) => {
    e.preventDefault();
    try {
      const res = await api('PUT', '/api/site', { rev: state.data.siteRev, site: { ...copyDraft } });
      state.data.site = res.site;
      if (res.rev) state.data.siteRev = res.rev;
      keepFocus(renderCopy);
      toast('Page copy saved. It shows in Preview site now; copy showcase/ to the course site to publish it.');
    } catch (err) {
      if (err.status === 400) formErrors($('#copy-errors'), err);
      else showError(err, 'The page copy');
    }
  });
  $('#copy-reset').addEventListener('click', () => { keepFocus(renderCopy); toast('Changes discarded.'); });
}

/* ---------- topics ---------- */

let topicsDraft = null;

function tagUse(id) {
  return rows().filter((r) => splitList(r.tags).map((t) => t.toLowerCase()).includes(id)).length;
}

function renderTopics() {
  const src = state.data.interests;
  topicsDraft = { interests: (src.interests || []).map((i) => ({ ...i, terms: [...i.terms], _existing: true, _idTouched: true })), tools: [...(src.tools || [])] };
  drawTopics();
  $('#tools-list').value = topicsDraft.tools.join('\n');
  setTopicsDirty(false);
  $('#topics-errors').hidden = true;
}

function drawTopics() {
  $('#topics').replaceChildren(...topicsDraft.interests.map((t, i) => {
    const name = () => t.label || `Topic ${i + 1}`;
    const label = el('input', { class: 'input', type: 'text', id: `t-${i}-label`, value: t.label, maxlength: '32', autocomplete: 'off' });
    const id = el('input', { class: 'input topic__id', type: 'text', id: `t-${i}-id`, value: t.id, maxlength: '32', spellcheck: 'false', readonly: t._existing ? true : null, 'aria-describedby': `t-${i}-idhint` });
    const terms = el('textarea', { class: 'input', id: `t-${i}-terms`, rows: '4', spellcheck: 'false' });
    terms.value = t.terms.join(', ');
    const del = el('button', { type: 'button', class: 'btn btn--ghost btn--icon', 'aria-label': `Remove ${name()}`, title: 'Remove topic', text: '×' });
    const group = el('div', { class: 'panel topic', role: 'group', 'aria-label': name() });
    const use = t._existing ? tagUse(t.id) : 0;
    const meta = el('p', { class: 'topic__use' });
    const updMeta = () => { meta.textContent = `${plural(t.terms.length, 'word')} · ${plural(use, 'project')} tagged`; };
    label.addEventListener('input', () => {
      t.label = label.value;
      if (!t._idTouched) { t.id = slugify(label.value, 32); id.value = t.id; }
      group.setAttribute('aria-label', name()); del.setAttribute('aria-label', `Remove ${name()}`);
      setTopicsDirty(true);
    });
    id.addEventListener('input', () => { t._idTouched = true; t.id = id.value.trim().toLowerCase(); setTopicsDirty(true); });
    terms.addEventListener('input', () => { t.terms = terms.value.split(/[,\n]/).map((s) => s.trim().toLowerCase()).filter(Boolean); updMeta(); setTopicsDirty(true); });
    del.addEventListener('click', async () => {
      if (use) {
        const v = await ask(`Remove “${name()}”?`, `${plural(use, 'project')} ${use > 1 ? 'are' : 'is'} tagged with it. The tag is dropped from them the next time each is saved.`, [{ label: 'Cancel', value: 'cancel' }, { label: 'Remove', value: 'rm', cls: 'btn--danger' }]);
        if (v !== 'rm') return;
      }
      topicsDraft.interests.splice(i, 1); setTopicsDirty(true); drawTopics();
      ($(`#t-${Math.min(i, topicsDraft.interests.length - 1)}-label`) || $('#add-topic')).focus();
      toast(`Removed ${name()}. Not saved yet.`);
    });
    updMeta();
    group.append(
      el('div', { class: 'topic__head' },
        el('div', null, el('label', { class: 'label', for: `t-${i}-label`, text: 'Topic' }), label),
        el('div', null, el('label', { class: 'label', for: `t-${i}-id`, text: 'ID' }), id),
        del),
      el('p', { class: 'visually-hidden', id: `t-${i}-idhint`, text: t._existing ? 'Fixed, because projects are tagged with it.' : 'Lowercase letters, numbers and dashes.' }),
      el('div', null, el('label', { class: 'label', for: `t-${i}-terms`, text: 'Search words' }), terms),
      meta);
    return group;
  }));
}

function setTopicsDirty(v) {
  state.topicsDirty = v;
  const s = $('#topics-state');
  s.textContent = v ? 'Unsaved changes' : 'All changes saved';
  s.classList.toggle('is-dirty', v);
  markTab('tab-topics', v);
}

function topicProblems() {
  const out = []; const seen = new Set();
  topicsDraft.interests.forEach((t, i) => {
    const n = t.label.trim() || `Topic ${i + 1}`;
    if (!t.label.trim()) out.push(`${n}: needs a name`);
    if (!/^[a-z0-9][a-z0-9-]{0,31}$/.test(t.id)) out.push(`${n}: ID must be lowercase letters, numbers and dashes`);
    else if (seen.has(t.id)) out.push(`${n}: ID “${t.id}” is already used by another topic`);
    seen.add(t.id);
    if (!t.terms.length) out.push(`${n}: add at least one search word`);
  });
  return out;
}

function wireTopics() {
  $('#add-topic').addEventListener('click', () => {
    topicsDraft.interests.push({ id: '', label: '', terms: [], _idTouched: false });
    setTopicsDirty(true); drawTopics();
    $(`#t-${topicsDraft.interests.length - 1}-label`)?.focus();
  });
  $('#tools-list').addEventListener('input', (e) => { topicsDraft.tools = e.target.value.split('\n').map((s) => s.trim()).filter(Boolean); setTopicsDirty(true); });
  $('#topics-reset').addEventListener('click', () => { keepFocus(renderTopics); toast('Changes discarded.'); });
  $('#topics-form').addEventListener('submit', async (e) => {
    e.preventDefault();
    const box = $('#topics-errors');
    const probs = topicProblems();
    if (probs.length) { formErrors(box, new Error(probs.join(' • '))); return; }
    const body = { interests: topicsDraft.interests.map(({ id, label, terms }) => ({ id, label, terms })), tools: topicsDraft.tools };
    try {
      const res = await api('PUT', '/api/interests', { rev: state.data.interestsRev, interests: body });
      state.data.interests = res.interests;
      if (res.rev) state.data.interestsRev = res.rev;
      keepFocus(renderTopics);
      const w = res.warnings || [];
      if (w.length) { box.replaceChildren(el('div', { class: 'callout callout--warn', role: 'status' }, el('strong', { text: 'Saved, but check these projects' }), el('ul', null, w.map((x) => el('li', { text: x }))))); box.hidden = false; }
      toast('Topics saved.');
      if (state.sel && !state.dirty) keepFocus(renderEditor);
    } catch (err) {
      if (err.status === 400) formErrors(box, err);
      else showError(err, 'The topics file');
    }
  });
}

/* ---------- publish ---------- */

function renderTally() {
  const all = rows();
  const pub = all.filter((r) => isYes(r.publish));
  const fix = pub.filter((r) => r._check && r._check.errors.length);
  const live = state.data.published || {};
  $('#tally').replaceChildren(...[
    ['In review sheet', all.length], ['Will publish', pub.length], ['Need fixes', fix.length],
    [live.demo ? 'Built now (samples)' : 'Built now', live.count || 0],
  ].map(([k, v]) => el('div', null, el('dt', { text: k }), el('dd', { text: String(v) }))));
}

function showLog(id, lines, ok) {
  const pre = $(id);
  pre.textContent = (lines || []).join('\n') || (ok ? 'Done.' : 'Failed.');
  pre.hidden = false;
}

// aria-disabled keeps the button focusable (disabling it would drop keyboard focus to <body>).
function busy(btn, on, text) {
  if (on) { btn.dataset.label = btn.textContent; btn.textContent = text; btn.setAttribute('aria-disabled', 'true'); }
  else { btn.textContent = btn.dataset.label || btn.textContent; btn.removeAttribute('aria-disabled'); }
}
const isBusy = (btn) => btn.getAttribute('aria-disabled') === 'true';

function wirePublish() {
  const file = $('#import-file');
  const go = $('#import-go');
  file.addEventListener('change', () => {
    const f = file.files[0];
    $('#import-name').textContent = f ? f.name : 'Choose export zip…';
    go.disabled = !f;
  });
  go.addEventListener('click', async () => {
    const f = file.files[0];
    if (!f || isBusy(go)) return;
    if (!(await guardDirty())) return;
    busy(go, true, 'Importing… this can take a minute');
    try {
      const res = await api('POST', '/api/import', f, { raw: true });
      showLog('#import-log', res.log, res.ok);
      const s = res.summary || {};
      toast(res.ok ? `Import finished: ${s.added || 0} new, ${s.updated || 0} updated. Review consent on the Projects tab.` : 'Import finished with problems. See the log.', res.ok ? { action: { label: 'Review projects', run: () => { state.seg = 'no'; syncSeg(); renderList(); selectTab('tab-projects', true); } } } : { error: true });
      file.value = ''; $('#import-name').textContent = 'Choose export zip…';
      await loadState(true);
    } catch (e) { showLog('#import-log', [e.message], false); showError(e); }
    finally { busy(go, false); go.disabled = !file.files[0]; }
  });

  const build = $('#build-go');
  build.addEventListener('click', async () => {
    if (isBusy(build)) return;
    if (state.dirty && !(await guardDirty())) return;
    if (state.copyDirty || state.topicsDirty) {
      const v = await ask('Unsaved page copy or topics', 'The build uses what is saved. Unsaved edits on the Page copy or Topics tab are not included.', [{ label: 'Cancel', value: 'cancel' }, { label: 'Build anyway', value: 'go', cls: 'btn--primary' }]);
      if (v !== 'go') return;
    }
    const samples = rows().filter((r) => isYes(r.publish) && /sample data/i.test(r.notes || '')).length;
    if (samples) {
      const v = await ask('Sample projects are marked Publish', `${plural(samples, 'project')} copied from the sample data will appear on the showcase as if they were real student work. Turn off Publish on them first, unless this build is only for trying things out.`, [{ label: 'Cancel', value: 'cancel' }, { label: 'Build anyway', value: 'go', cls: 'btn--primary' }]);
      if (v !== 'go') return;
    }
    busy(build, true, 'Building…');
    $('#build-preview').hidden = true;
    try {
      const res = await api('POST', '/api/build', { skipInvalid: $('#opt-skip').checked, publishPdfs: $('#opt-pdfs').checked });
      showLog('#build-log', res.log, res.ok);
      if (res.ok) { $('#build-preview').hidden = false; toast(`Built. ${plural(res.published, 'project')} in the showcase. Next: copy it to the course site (step 4).`); }
      else toast('Nothing was published. Fix the projects listed in the log, or tick “Skip projects that need fixes”.', { error: true });
      await loadState(true);
    } catch (e) { showLog('#build-log', [e.message], false); showError(e); }
    finally { busy(build, false); }
  });

  $('#deploy-copy').addEventListener('click', async () => {
    try { await navigator.clipboard.writeText($('#deploy-cmd').textContent); toast('Command copied.'); }
    catch { toast('Could not copy. Select the command and copy it by hand.', { error: true }); }
  });
}

async function loadBackups() {
  const ul = $('#backups');
  try {
    const list = await api('GET', '/api/backups');
    if (!list.length) { ul.replaceChildren(el('li', null, el('span', { class: 'muted', text: 'No backups yet. One is made every time the review sheet is saved.' }))); return; }
    ul.replaceChildren(...list.slice(0, 10).map((b) => {
      const when = new Date(b.modified);
      const label = Number.isNaN(when.valueOf()) ? b.name : when.toLocaleString();
      const btn = el('button', { type: 'button', class: 'btn btn--ghost btn--sm', text: 'Restore', 'aria-label': `Restore backup from ${label}` });
      btn.addEventListener('click', async () => {
        if (!(await guardDirty())) return;
        const v = await ask('Restore this backup?', `The review sheet goes back to how it was before the save at ${label}. The current sheet is backed up first, so you can undo this.`, [{ label: 'Cancel', value: 'cancel' }, { label: 'Restore', value: 'go', cls: 'btn--primary' }]);
        if (v !== 'go') return;
        try { await api('POST', '/api/backups/restore', { name: b.name }); state.dirty = false; await loadState(true); loadBackups(); toast('Backup restored.'); }
        catch (e) { showError(e); }
      });
      return el('li', null, el('span', { text: label }), btn);
    }));
  } catch (e) { ul.replaceChildren(el('li', null, el('span', { class: 'muted', text: e.message }))); }
}

/* ---------- boot ---------- */

async function loadState(keepSelection) {
  const data = await api('GET', '/api/state');
  state.data = data;
  if (!keepSelection || !rows().some((r) => r.submission_id === state.sel)) { state.sel = null; state.draft = null; state.dirty = false; }
  else if (!state.dirty) state.draft = { ...rows().find((r) => r.submission_id === state.sel) };
  else state.draft = { ...state.draft, _check: rows().find((r) => r.submission_id === state.sel)._check };
  keepFocus(() => {
    renderList();
    renderEditor();
    if (!state.copyDirty) renderCopy();
    if (!state.topicsDirty) renderTopics();
    renderTally();
  });
  markTab('tab-projects', state.dirty);
}

function wireGlobal() {
  window.addEventListener('beforeunload', (e) => {
    if (state.dirty || state.copyDirty || state.topicsDirty) { e.preventDefault(); e.returnValue = ''; }
  });
  document.addEventListener('keydown', (e) => {
    if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 's') {
      e.preventDefault();
      if ($('#modal').open || state.saving) return;
      const tab = $('.tab[aria-selected="true"]').id;
      if (tab === 'tab-projects' && state.draft) saveRow();
      if (tab === 'tab-copy') $('#copy-form').requestSubmit();
      if (tab === 'tab-topics') $('#topics-form').requestSubmit();
    }
  });
  $('#modal').addEventListener('close', () => { if (askResolve) settleAsk('cancel'); }); // Esc
  const t = $('#toast');
  t.addEventListener('mouseleave', () => { if (!t.classList.contains('toast--error') && t.classList.contains('is-shown')) schedHide(1500); });
}

async function boot() {
  // Pasting the terminal link into an already-open admin tab only changes the hash; start over with the new token.
  window.addEventListener('hashchange', () => { if (/^#t=/.test(location.hash)) location.reload(); });
  token = readToken();
  if (!token) { lock(); return; }
  wireTabs(); wireProjects(); wireCopy(); wireTopics(); wirePublish(); wireGlobal();
  try { await loadState(false); }
  catch (e) { if (e.status !== 401) { showError(e); } }
}

boot();
