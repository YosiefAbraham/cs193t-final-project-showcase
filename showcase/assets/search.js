// Client-side "smart" search. No network, no dependencies.
// 1. Understands topics: "work out", "gym", "recipes", "fitnes" (typo) all resolve to an interest.
// 2. Ranks projects by weighted fields (title > topic > keywords/tools > summary > description)
//    with exact, prefix and typo-tolerant matches, scaled by how rare each word is.

const STOP = new Set(('a an the and or of for to in on at by with from into about my me i we our you your ' +
  'is are be it its this that these those some something anything stuff thing things way ways ' +
  'help helps helping helper want need make makes making using use uses used can could would ' +
  'how what which who where when show find get give like just more best good new ' +
  'ai app apps tool tools project projects system systems built build student students').split(/\s+/));

const FIELD_WEIGHTS = { title: 6, topic: 4, keywords: 3, tools: 3, students: 2.5, summary: 2, description: 1 };

export function normalize(s) {
  return String(s).normalize('NFKD').replace(/[\u0300-\u036f]/g, '').toLowerCase();
}

export function tokenize(s) {
  return normalize(s).split(/[^a-z0-9]+/).filter(Boolean);
}

export function stem(t) {
  if (t.length <= 3) return t;
  if (t.endsWith('ies') && t.length > 4) return t.slice(0, -3) + 'y';
  if (t.endsWith('ing') && t.length > 5) {
    let b = t.slice(0, -3);
    if (/([b-df-hj-np-tv-z])\1$/.test(b)) b = b.slice(0, -1); // running -> run, planning -> plan
    return b;
  }
  if (t.endsWith('ed') && t.length > 4) return t.slice(0, -2);
  if (/(ss|us|is)$/.test(t)) return t; // fitness, analysis, campus
  if (/(x|z|ch|sh)es$/.test(t) && t.length > 4) return t.slice(0, -2);
  if (t.endsWith('s') && t.length > 3) return t.slice(0, -1);
  return t;
}

// Bounded Levenshtein: returns max+1 as soon as the distance must exceed max.
export function editDistance(a, b, max) {
  if (a === b) return 0;
  if (Math.abs(a.length - b.length) > max) return max + 1;
  let prev = new Array(b.length + 1);
  for (let j = 0; j <= b.length; j++) prev[j] = j;
  for (let i = 1; i <= a.length; i++) {
    const cur = [i];
    let rowMin = i;
    for (let j = 1; j <= b.length; j++) {
      const cost = a[i - 1] === b[j - 1] ? 0 : 1;
      cur[j] = Math.min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + cost);
      if (cur[j] < rowMin) rowMin = cur[j];
    }
    if (rowMin > max) return max + 1;
    prev = cur;
  }
  return prev[b.length];
}

function typoBudget(len) { return len >= 8 ? 2 : len >= 5 ? 1 : 0; }

export function createEngine(projects, interests, toolNames = []) {
  const byId = new Map(interests.map((i) => [i.id, i]));

  // Topic phrase tables (stemmed token arrays).
  const multi = []; // { id, toks }
  const single = new Map(); // stem -> Set(interest ids)
  for (const it of interests) {
    const terms = [...it.terms, it.label, it.id];
    for (const term of terms) {
      const toks = tokenize(term).filter((t) => !STOP.has(t) || tokenize(term).length > 1).map(stem);
      if (!toks.length) continue;
      if (toks.length > 1) multi.push({ id: it.id, toks });
      else {
        if (!single.has(toks[0])) single.set(toks[0], new Set());
        single.get(toks[0]).add(it.id);
      }
    }
  }
  multi.sort((a, b) => b.toks.length - a.toks.length);
  // Unstemmed single-word terms, for typo matching ("cookng" -> "cooking", "fitnes" -> "fitness").
  const rawSingle = new Map();
  for (const it of interests) for (const term of [...it.terms, it.label]) {
    const toks = tokenize(term);
    if (toks.length !== 1 || toks[0].length < 4) continue;
    if (!rawSingle.has(toks[0])) rawSingle.set(toks[0], new Set());
    rawSingle.get(toks[0]).add(it.id);
  }
  const rawKeys = [...rawSingle.keys()];
  // Tool names ("Claude Code") are searched as text but never read as topics ("code" -> Software).
  const toolPhrases = toolNames.map((t) => tokenize(t).map(stem)).filter((t) => t.length).sort((a, b) => b.length - a.length);

  // Document index.
  const docs = projects.map((p) => {
    const fields = {
      title: p.title,
      topic: (p.tags || []).map((t) => (byId.get(t) || {}).label || t).join(' '),
      keywords: (p.keywords || []).join(' '),
      tools: (p.tools || []).join(' '),
      students: (p.students || []).join(' '),
      summary: p.summary || '',
      description: p.description || '',
    };
    const weights = new Map(); // stem -> best field weight
    for (const [field, text] of Object.entries(fields)) {
      const w = FIELD_WEIGHTS[field];
      for (const tok of tokenize(text)) {
        const s = stem(tok);
        if ((weights.get(s) || 0) < w) weights.set(s, w);
      }
    }
    return { p, weights, tags: new Set(p.tags || []) };
  });

  const df = new Map();
  for (const d of docs) for (const s of d.weights.keys()) df.set(s, (df.get(s) || 0) + 1);
  const vocab = [...df.keys()];
  const N = docs.length || 1;
  const idf = (s) => Math.log(1 + N / (df.get(s) || 1));

  function detectTopics(stems, raw) {
    const topics = new Map(); // id -> { id, via }
    const consumed = new Set();
    const protectedIdx = new Set();
    for (const toks of toolPhrases) {
      for (let i = 0; i + toks.length <= stems.length; i++) {
        if (toks.every((t, k) => stems[i + k] === t)) for (let k = 0; k < toks.length; k++) protectedIdx.add(i + k);
      }
    }
    for (const { id, toks } of multi) {
      for (let i = 0; i + toks.length <= stems.length; i++) {
        let ok = true;
        for (let k = 0; k < toks.length; k++) if (stems[i + k] !== toks[k] || consumed.has(i + k) || protectedIdx.has(i + k)) { ok = false; break; }
        if (ok) {
          if (!topics.has(id)) topics.set(id, { id, via: raw.slice(i, i + toks.length).join(' '), exact: true });
          for (let k = 0; k < toks.length; k++) consumed.add(i + k);
        }
      }
    }
    stems.forEach((s, i) => {
      if (consumed.has(i) || protectedIdx.has(i) || STOP.has(raw[i])) return;
      const hit = single.get(s);
      if (hit) { for (const id of hit) if (!topics.has(id)) topics.set(id, { id, via: raw[i], exact: true }); return; }
      const word = raw[i];
      const budget = typoBudget(word.length);
      if (!budget) return;
      let best = null; let bestD = budget + 1;
      for (const k of rawKeys) {
        if (k[0] !== word[0]) continue;
        const d = editDistance(word, k, budget);
        if (d < bestD) { bestD = d; best = k; }
      }
      if (best) for (const id of rawSingle.get(best)) if (!topics.has(id)) topics.set(id, { id, via: word, exact: false });
    });
    return { topics: [...topics.values()], consumed };
  }

  // Best match strength of one query stem inside a document, with which vocabulary word matched.
  function expandTerm(s, isLast) {
    const out = [[s, 1]];
    if (s.length >= 3) {
      for (const v of vocab) if (v !== s && v.startsWith(s)) out.push([v, isLast ? 0.85 : 0.7]);
    }
    const budget = typoBudget(s.length);
    if (budget && !df.has(s)) {
      for (const v of vocab) {
        if (v[0] !== s[0] || v.startsWith(s)) continue;
        if (editDistance(s, v, budget) <= budget) out.push([v, 0.55]);
      }
    }
    return out;
  }

  function search(query) {
    const raw = tokenize(query).slice(0, 12);
    if (!raw.length) return null;
    const stems = raw.map(stem);
    const { topics, consumed } = detectTopics(stems, raw);
    const terms = [];
    stems.forEach((s, i) => { if (!consumed.has(i) && !STOP.has(raw[i])) terms.push({ s, last: i === stems.length - 1 }); });
    if (!terms.length && !topics.length) return null;

    const expanded = terms.map((t) => expandTerm(t.s, t.last));
    const matchWords = new Set();
    const topicIds = new Set(topics.map((t) => t.id));
    const topicStems = new Set();
    for (const id of topicIds) for (const term of byId.get(id).terms) for (const t of tokenize(term)) topicStems.add(stem(t));

    const results = [];
    for (const d of docs) {
      let score = 0; let matched = 0;
      expanded.forEach((cands) => {
        let best = 0; let bestWord = null;
        for (const [v, strength] of cands) {
          const w = d.weights.get(v);
          if (!w) continue;
          const val = w * strength * idf(v);
          if (val > best) { best = val; bestWord = v; }
        }
        if (best) { score += best; matched++; matchWords.add(bestWord); }
      });
      if (terms.length) score *= Math.pow(matched / terms.length, 1.5);

      let topicHit = 0;
      for (const id of topicIds) if (d.tags.has(id)) topicHit++;
      if (topicHit) score += 9 * topicHit;
      else if (topicIds.size) {
        // Untagged projects that still talk about the topic a lot get a small nudge.
        let soft = 0;
        for (const s of topicStems) if ((d.weights.get(s) || 0) >= FIELD_WEIGHTS.summary) soft++;
        if (soft >= 2) score += Math.min(soft, 3) * 1.2;
      }
      if (score > 0) results.push({ p: d.p, score, topicHit });
    }

    results.sort((a, b) => b.score - a.score || (b.p.featured === true) - (a.p.featured === true) || a.p.title.localeCompare(b.p.title));
    // Drop the long tail of weak matches once there are strong ones.
    const top = results.length ? results[0].score : 0;
    const floor = terms.length ? 0.15 : 0.3;
    const kept = results.filter((r) => r.score >= top * floor);
    for (const id of topicIds) for (const t of byId.get(id).terms) { const w = tokenize(t); if (w.length === 1) matchWords.add(stem(w[0])); }
    return { results: kept, topics, matchWords, terms: terms.map((t) => t.s) };
  }

  function topicCounts() {
    const c = new Map(interests.map((i) => [i.id, 0]));
    for (const d of docs) for (const t of d.tags) if (c.has(t)) c.set(t, c.get(t) + 1);
    return c;
  }

  return { search, topicCounts, interestsById: byId };
}

// Split text into plain and highlighted runs for safe DOM rendering (no HTML strings).
export function highlightRuns(text, matchWords, prefixTerms) {
  if (!matchWords || !matchWords.size) return [[text, false]];
  const runs = [];
  const re = /[\p{L}\p{N}]+/gu;
  let last = 0; let m;
  while ((m = re.exec(text))) {
    const s = stem(normalize(m[0]));
    const hit = matchWords.has(s) || (prefixTerms || []).some((p) => p.length >= 3 && s.startsWith(p));
    if (!hit) continue;
    if (m.index > last) runs.push([text.slice(last, m.index), false]);
    runs.push([m[0], true]);
    last = m.index + m[0].length;
  }
  if (last < text.length) runs.push([text.slice(last), false]);
  return runs;
}
