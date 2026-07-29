// plugin-marketplace-ui — the rich Marketplace pane, shipped as a plugin.
// Discover design ported from the hosted marketplace (catalog.html /
// plugin_detail.html), with native install / update / settings actions.
// This plugin is UI only: all actions go to core plugin-marketplace APIs.
//
// Data comes from two places:
//   1. Luna's own catalog API (same-origin, authoritative for installed state,
//      upgrades, and install/upgrade actions).
//   2. The marketplace's public JSON (cross-origin, GET-only CORS): discover
//      sections, categories, media, ratings, reviews. Best-effort — when it
//      can't be reached the pane falls back to a flat card list built from (1).

// Catalog API stays root-absolute — it's proxied by the control plane using the
// session, and the iframe token is only valid there.
const API = '/api/p/plugin-marketplace';
// Agent base path (e.g. "/a/owner-abc123") derived from this iframe's URL.
const BASE = window.location.pathname.split('/api/p/plugin-marketplace-ui')[0];
// 008.995/008.996/035: token handling — see git history; unchanged behavior.
let TOKEN = localStorage.getItem('luna.token' + BASE) || (!BASE ? localStorage.getItem('luna.token') : '') || '';

window.addEventListener('message', (e) => {
  if (e.data && e.data.type === 'luna-auth' && e.data.token) {
    const first = !TOKEN;
    TOKEN = e.data.token;
    if (first) load();
  }
  if (e.data && e.data.type === 'luna-plugin-event' && e.data.event === 'ui.section.reclick') {
    load();
  }
});

function requestFreshToken(prev, timeoutMs = 1500) {
  return new Promise((resolve) => {
    let timer;
    const onMsg = (e) => {
      if (e.data && e.data.type === 'luna-auth' && e.data.token && e.data.token !== prev) {
        cleanup();
        resolve(true);
      }
    };
    const cleanup = () => { window.removeEventListener('message', onMsg); clearTimeout(timer); };
    window.addEventListener('message', onMsg);
    timer = setTimeout(() => { cleanup(); resolve(false); }, timeoutMs);
    try {
      window.parent.postMessage({ type: 'luna-request-auth' }, window.location.origin);
    } catch {
      cleanup();
      resolve(false);
    }
  });
}

async function api(method, path, body, _retried) {
  const opts = { method, headers: {} };
  if (TOKEN) opts.headers.Authorization = `Bearer ${TOKEN}`;
  if (body) {
    opts.headers['Content-Type'] = 'application/json';
    opts.body = JSON.stringify(body);
  }
  const res = await fetch(`${API}${path}`, opts);
  if (res.status === 401 && !_retried) {
    const prev = TOKEN;
    if (method === 'GET') {
      TOKEN = '';
      requestFreshToken(prev);
      return api(method, path, body, true);
    }
    const refreshed = await requestFreshToken(prev);
    if (refreshed) return api(method, path, body, true);
  }
  if (!res.ok) {
    let err;
    try {
      const j = await res.json();
      const d = j && j.detail;
      if (d && typeof d === 'object') {
        err = new Error(d.message || `${res.status} ${res.statusText}`);
        err.code = d.code;
        err.stage = d.stage;
        err.hint = d.hint;
        err.traceId = d.trace_id;
        err.technical = d.technical;
      } else {
        err = new Error((typeof d === 'string' && d) || `${res.status} ${res.statusText}`);
      }
    } catch {
      err = new Error(`${res.status} ${res.statusText}`);
    }
    err.httpStatus = res.status;
    throw err;
  }
  return res.json();
}

function setStatus(text) {
  document.getElementById('status-text').textContent = text;
}

function esc(s) {
  return String(s == null ? '' : s).replace(/[&<>"']/g, (c) =>
    ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}

// ---- In-pane notices (unchanged from the old pane) ----

function noticeHost() {
  let host = document.getElementById('notices');
  if (!host) {
    host = document.createElement('div');
    host.id = 'notices';
    host.className = 'notices';
    const main = document.getElementById('main');
    main.parentNode.insertBefore(host, main);
  }
  return host;
}

function dismissNotice(el) {
  el.classList.add('notice-leaving');
  setTimeout(() => el.remove(), 180);
}

function showNotice(level, opts) {
  const host = noticeHost();
  const card = document.createElement('div');
  card.className = `notice notice-${level}`;
  card.dataset.testid = `mp-notice-${level}`;

  const title = opts.title || (level === 'success' ? 'Done' : level === 'error' ? 'Update failed' : '');
  let html = '<button class="notice-close" title="Dismiss" aria-label="Dismiss">×</button>';
  html += `<div class="notice-title">${esc(title)}</div>`;
  if (opts.message) html += `<div class="notice-msg">${esc(opts.message)}</div>`;
  if (opts.hint) html += `<div class="notice-hint">${esc(opts.hint)}</div>`;

  if (opts.items && opts.items.length) {
    html += '<ul class="notice-items">';
    for (const it of opts.items) {
      const trace = it.traceId ? ` <span class="notice-trace">${esc(it.traceId)}</span>` : '';
      const hint = it.hint ? `<div class="notice-item-hint">${esc(it.hint)}</div>` : '';
      html += `<li><strong>${esc(it.name)}</strong>: ${esc(it.error)}${trace}${hint}</li>`;
    }
    html += '</ul>';
  }

  const diag = buildDiagnostics(level, opts);
  if (diag) {
    html += '<div class="notice-actions">';
    if (opts.traceId || (opts.items && opts.items.some((i) => i.traceId))) {
      const tid = opts.traceId || (opts.items.find((i) => i.traceId) || {}).traceId;
      html += `<span class="notice-trace-label">trace <span class="notice-trace">${esc(tid)}</span></span>`;
    }
    html += '<button class="notice-btn notice-copy">Copy diagnostics</button>';
    if (opts.technical) html += '<button class="notice-btn notice-toggle">Details</button>';
    html += '</div>';
    if (opts.technical) html += `<pre class="notice-tech hidden">${esc(opts.technical)}</pre>`;
  }

  card.innerHTML = html;
  card._diag = diag;
  card.querySelector('.notice-close').addEventListener('click', () => dismissNotice(card));
  const copyBtn = card.querySelector('.notice-copy');
  if (copyBtn) {
    copyBtn.addEventListener('click', () => {
      copyText(card._diag).then(() => {
        copyBtn.textContent = 'Copied';
        setTimeout(() => { copyBtn.textContent = 'Copy diagnostics'; }, 1500);
      });
    });
  }
  const toggle = card.querySelector('.notice-toggle');
  if (toggle) {
    toggle.addEventListener('click', () => {
      card.querySelector('.notice-tech').classList.toggle('hidden');
    });
  }

  host.prepend(card);
  if (level === 'success') setTimeout(() => dismissNotice(card), 4500);
  return card;
}

function buildDiagnostics(level, opts) {
  if (level === 'success') return null;
  const lines = ['Luna plugin update diagnostics', '------------------------------'];
  if (opts.plugin) lines.push(`plugin: ${opts.plugin}`);
  if (opts.version) lines.push(`version: ${opts.version}`);
  if (opts.stage) lines.push(`stage: ${opts.stage}`);
  if (opts.traceId) lines.push(`trace id: ${opts.traceId}`);
  if (opts.message) lines.push(`error: ${opts.message}`);
  if (opts.technical && opts.technical !== opts.message) lines.push(`technical: ${opts.technical}`);
  if (opts.items && opts.items.length) {
    lines.push('failed items:');
    for (const it of opts.items) {
      lines.push(`  - ${it.name}: ${it.error}${it.traceId ? ` (trace ${it.traceId})` : ''}`);
    }
  }
  lines.push(`page: ${window.location.href}`);
  lines.push(`time: ${new Date().toISOString()}`);
  return lines.join('\n');
}

function copyText(text) {
  if (navigator.clipboard && navigator.clipboard.writeText) {
    return navigator.clipboard.writeText(text).catch(() => fallbackCopy(text));
  }
  return Promise.resolve(fallbackCopy(text));
}

function fallbackCopy(text) {
  try {
    const ta = document.createElement('textarea');
    ta.value = text;
    ta.style.position = 'fixed';
    ta.style.opacity = '0';
    document.body.appendChild(ta);
    ta.select();
    document.execCommand('copy');
    document.body.removeChild(ta);
  } catch {}
}

// ---- caches ----
// Luna catalog: stale-while-revalidate (008.995), unchanged.
const CATALOG_CACHE_KEY = 'luna.cache.marketplace-catalog' + BASE;
const CATALOG_CACHE_TTL_MS = 24 * 60 * 60 * 1000;
// Marketplace discover payloads: short TTL, keyed by source URL.
const RICH_TTL_MS = 15 * 60 * 1000;

function readCache(key, ttl) {
  try {
    const entry = JSON.parse(localStorage.getItem(key) || 'null');
    if (!entry || Date.now() - entry.at > ttl) return null;
    return entry.data;
  } catch {
    return null;
  }
}

function writeCache(key, data) {
  try {
    localStorage.setItem(key, JSON.stringify({ at: Date.now(), data }));
  } catch { /* quota / private mode — cache is best-effort */ }
}

// ---- design constants (identical to the hosted marketplace) ----
const TINTS = {
  connectivity: '139,92,246', ability: '56,189,248', knowledge: '96,165,250',
  automation: '251,191,36', media: '232,121,249', development: '45,212,191',
  games: '251,113,133', communication: '52,211,153', security: '161,161,170',
};
const COVERS = {
  connectivity: 'linear-gradient(135deg,#1e1b4b,#312e81 55%,#4c1d95)',
  ability: 'linear-gradient(135deg,#2e1065,#4c1d95 55%,#6d28d9)',
  knowledge: 'linear-gradient(135deg,#172554,#1e3a8a 60%,#312e81)',
  automation: 'linear-gradient(135deg,#3b0764,#581c87 60%,#7c3aed)',
  media: 'linear-gradient(135deg,#4a044e,#701a75 55%,#86198f)',
  development: 'linear-gradient(135deg,#082f49,#0c4a6e 60%,#312e81)',
  games: 'linear-gradient(135deg,#4c0519,#881337 60%,#9d174d)',
  communication: 'linear-gradient(135deg,#042f2e,#134e4a 60%,#1e3a8a)',
  security: 'linear-gradient(135deg,#1c1917,#292524 60%,#3f3f46)',
};
const tint = (c) => `radial-gradient(130% 130% at 0% 0%, rgba(${TINTS[c] || '139,92,246'},.07), transparent 55%), rgba(255,255,255,.03)`;

const ICON_CHECK = '<svg viewBox="0 0 24 24" fill="none" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"><path d="M4 12.5l5 5L20 6.5"/></svg>';
const ICON_GEAR = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 1 1-4 0v-.09a1.65 1.65 0 0 0-1-1.51 1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 1 1 0-4h.09a1.65 1.65 0 0 0 1.51-1 1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06a1.65 1.65 0 0 0 1.82.33h.09a1.65 1.65 0 0 0 1-1.51V3a2 2 0 1 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82v.09a1.65 1.65 0 0 0 1.51 1H21a2 2 0 1 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z"/></svg>';

// ---- state ----
let LCAT = [];          // Luna catalog (installed/upgrade truth + actions)
let CUR = 0;            // selected source index in LCAT
let RICH = {};          // source url → discover payload (or null when unavailable)
let VIEW = 'discover';  // 'discover' | 'catpage' | 'detail'
let DETAIL = null;      // {plugin, reviews, versions, origin, slug}
let reviewSort = 'helpful';
let catRows = [], catShown = 0;
const PAGE = 24;

// "https://host/mp/official" → {origin, slug}; anything else → null (no rich data).
function srcInfo(mp) {
  const m = /^(https?:\/\/[^/]+)\/mp\/([^/?#]+)/.exec(mp.marketplace.url || '');
  return m ? { origin: m[1], slug: m[2] } : null;
}

function curMp() { return LCAT[CUR] || null; }
function curRich() { const mp = curMp(); return mp ? RICH[mp.marketplace.url] || null : null; }

// Luna-state lookup for the CURRENT source: name → catalog entry.
function lunaEntry(name) {
  const mp = curMp();
  if (!mp || mp.error) return null;
  return (mp.plugins || []).find((p) => p.name === name) || null;
}

function absUrl(origin, u) {
  if (!u) return null;
  return /^https?:\/\//.test(u) ? u : origin + u;
}

const displayName = (n) => esc((n || '').replace(/^plugin-/, '').replace(/-/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase()));
const catLabel = (c) => (c ? c.charAt(0).toUpperCase() + c.slice(1) : '');

function starsHtml(avg) {
  const full = Math.round(avg || 0);
  return `<span class="stars">${'★'.repeat(full)}${'☆'.repeat(5 - full)}</span>`;
}

function iconHtml(p, origin, size) {
  const icon = (p.media || []).find((m) => m.kind === 'icon');
  let url = icon ? absUrl(origin, icon.url) : null;
  if (!url) {
    const le = lunaEntry(p.name);
    if (le && le.icon) url = le.icon;
  }
  if (url) return `<img src="${esc(url)}" alt="" loading="lazy">`;
  const letter = (p.name || '?').replace(/^plugin-/, '').charAt(0).toUpperCase();
  return `<div class="lettertile" style="background:${COVERS[p.category] || COVERS.connectivity};font-size:${size || 30}px">${esc(letter)}</div>`;
}

function coverBg(p, origin) {
  const cover = (p.media || []).find((m) => m.kind === 'cover');
  if (cover) return `<img class="bg" src="${esc(absUrl(origin, cover.url))}" alt=""><div class="scrim"></div>`;
  return `<div class="cbg" style="background:radial-gradient(120% 170% at 85% 15%, rgba(${TINTS[p.category] || '139,92,246'},.28), transparent 60%), rgba(255,255,255,.02)"></div><div class="scrim"></div>`;
}

// ---- action UI (Install / Update / Installed + gear) ----
// Driven entirely by Luna's catalog entry — the marketplace only decorates.
function actionUi(name, { compact } = {}) {
  const mp = curMp();
  const le = lunaEntry(name);
  if (!le || !mp) return '';
  const url = esc(mp.marketplace.url);
  const mpid = esc(mp.marketplace.marketplace_id || '');
  if (!le.installed) {
    return `<button class="get" data-act="install" data-name="${esc(name)}" data-version="${esc(le.version)}" data-url="${url}" data-mpid="${mpid}" data-testid="mp-install-${esc(name)}">Install</button>`;
  }
  let h = '';
  if (le.upgrade && le.upgrade.compatible && le.upgrade.available) {
    const from = le.installed_version || le.version;
    const label = compact ? 'Update' : `Update ${esc(from)} → ${esc(le.upgrade.available)}`;
    const notes = le.release_notes ? ` title="${esc(le.release_notes)}"` : '';
    h += `<button class="upd" data-act="upgrade"${notes} data-name="${esc(name)}" data-version="${esc(le.upgrade.available)}" data-url="${url}" data-mpid="${mpid}" data-testid="mp-upgrade-${esc(name)}">${label}</button>`;
    if (le.upgrade.override) h += `<span class="plugin-override-note" title="A newer version overrides the deployment-baked copy. The baked copy stays in the image until you upgrade again.">overrides baked</span>`;
  } else if (le.upgrade && !le.upgrade.compatible) {
    h += `<span class="badge-disabled" title="${esc(le.upgrade.reason || 'incompatible update')}" data-testid="mp-upgrade-blocked-${esc(name)}">Update available</span>`;
  } else {
    const iv = (le.installed_version && le.installed_version !== le.version)
      ? `<span class="plugin-installed-version" data-testid="mp-installed-version-${esc(name)}">v${esc(le.installed_version)}</span>` : '';
    h += `<span class="installed" data-testid="mp-installed-${esc(name)}">${ICON_CHECK}Installed${iv}</span>`;
  }
  // Settings deep-link into the Luna shell (same origin — navigate the top frame).
  h += `<a class="gear" title="Plugin settings in Luna" target="_top" href="${esc(BASE)}/settings/plugins/${encodeURIComponent(name)}" data-testid="mp-gear-${esc(name)}">${ICON_GEAR}</a>`;
  return h;
}

// ---- card builders ----
function heroCard(p, origin, cls) {
  const kicker = p.kicker || (p.category ? catLabel(p.category) : 'Featured');
  return `<div class="hero${cls ? ' ' + cls : ''}" data-open="${esc(p.name)}">
    ${coverBg(p, origin)}
    <div class="tx"><span class="kicker">${esc(kicker)}</span><h2>${p.hero_title ? esc(p.hero_title) : displayName(p.name)}</h2><p>${esc(p.hero_sub || p.description || '')}</p></div>
  </div>`;
}

function card(p, origin) {
  return `<div class="card" style="background:${tint(p.category)}" data-open="${esc(p.name)}" data-testid="mp-plugin-${esc(p.name)}">
    <div class="top">
      <div class="ic">${iconHtml(p, origin)}</div>
      <div><div class="nm">${displayName(p.name)}</div><div class="ct">${p.latest_version ? 'v' + esc(p.latest_version) : ''}</div></div>
    </div>
    <div class="sb">${esc(p.description || '')}</div>
    ${fallbackNote(p.name)}
    <div class="foot">${actionUi(p.name, { compact: true })}</div>
  </div>`;
}

function fallbackNote(name) {
  const le = lunaEntry(name);
  return (le && le.fallback)
    ? `<div class="plugin-fallback" data-testid="mp-fallback-${esc(name)}">⚠ Installed copy failed to load — using baked v${esc(le.fallback.active_version)}.</div>`
    : '';
}

function section(title, plugins, origin, more) {
  if (!plugins || !plugins.length) return '';
  return `<div class="shead"><h2>${esc(title)}</h2>${more || ''}</div>
    <div class="cards">${plugins.map((p) => card(p, origin)).join('')}</div>`;
}

function rowHtml(p, origin) {
  return `<div class="row" data-open="${esc(p.name)}">
    <div class="ic">${iconHtml(p, origin, 24)}</div>
    <div class="tx"><div class="nm">${displayName(p.name)}</div><div class="sb">${esc(p.description || '')}</div></div>
    ${actionUi(p.name, { compact: true })}
  </div>`;
}

// ---- loading ----
function showSkeleton() {
  document.getElementById('heroes').innerHTML =
    '<div class="skeleton skeleton-hero"></div><div class="skeleton skeleton-hero"></div>';
  document.getElementById('essentials').innerHTML =
    `<div class="shead"><h2>&nbsp;</h2></div><div class="cards" data-testid="mp-skeleton">${'<div class="skeleton skeleton-card"></div>'.repeat(3)}</div>`;
  ['bigheroes', 'toppicks', 'catsections', 'flatlist'].forEach((id) => { document.getElementById(id).innerHTML = ''; });
}

async function load(force) {
  const cached = force ? null : readCache(CATALOG_CACHE_KEY, CATALOG_CACHE_TTL_MS);
  if (cached) {
    LCAT = cached;
    setStatus('Refreshing…');
    await hydrateRich(force);
    renderAll();
  } else {
    showSkeleton();
    setStatus('Loading…');
  }
  let catalog;
  try {
    catalog = await api('GET', '/catalog');
  } catch (e) {
    if (cached) { setStatus(`Refresh failed: ${e.message}`); return; }
    ['heroes', 'essentials', 'bigheroes', 'toppicks', 'catsections', 'flatlist'].forEach((id) => { document.getElementById(id).innerHTML = ''; });
    setStatus(`Error: ${e.message}`);
    showNotice('error', {
      title: "Couldn't load the marketplace",
      message: e.message, hint: e.hint, stage: e.stage, traceId: e.traceId, technical: e.technical,
    });
    return;
  }
  writeCache(CATALOG_CACHE_KEY, catalog);
  LCAT = catalog;
  if (CUR >= LCAT.length) CUR = 0;
  await hydrateRich(force);
  renderAll();
}

// Fetch discover payloads for every /mp/ source (best-effort, cached).
async function hydrateRich(force) {
  await Promise.all(LCAT.map(async (mp) => {
    const url = mp.marketplace.url;
    if (mp.error) { RICH[url] = null; return; }
    const info = srcInfo(mp);
    if (!info) { RICH[url] = null; return; }
    const key = 'luna.cache.mp-rich:' + url;
    if (!force) {
      const hit = readCache(key, RICH_TTL_MS);
      if (hit) { RICH[url] = hit; return; }
    }
    try {
      const r = await fetch(`${info.origin}/api/catalog/${encodeURIComponent(info.slug)}/discover`);
      if (!r.ok) { RICH[url] = RICH[url] || null; return; }
      const d = await r.json();
      RICH[url] = d;
      writeCache(key, d);
    } catch {
      RICH[url] = RICH[url] || null; // offline / no CORS → flat fallback
    }
  }));
}

// ---- rendering ----
function renderAll() {
  renderTabs();
  renderBanner();
  const anyPlugins = LCAT.some((mp) => !mp.error && (mp.plugins || []).length);
  document.getElementById('empty').classList.toggle('hidden', LCAT.length > 0);
  if (VIEW === 'discover') renderDiscover();
  else if (VIEW === 'catpage') renderRows();
  else if (VIEW === 'detail' && DETAIL) renderDetail();
  const total = LCAT.reduce((n, mp) => n + (mp.error ? 0 : (mp.plugins || []).length), 0);
  setStatus(LCAT.length ? `${total} plugin(s) across ${LCAT.length} marketplace(s)` : 'No marketplaces configured');
  if (!anyPlugins && LCAT.length) setStatus(`${LCAT.length} marketplace(s), no plugins listed`);
}

function renderTabs() {
  const el = document.getElementById('srctabs');
  if (LCAT.length < 2) { el.classList.add('hidden'); el.innerHTML = ''; return; }
  el.classList.remove('hidden');
  el.innerHTML = LCAT.map((mp, i) =>
    `<button class="srctab${i === CUR ? ' on' : ''}" data-act="src" data-i="${i}" data-testid="mp-src-tab-${i}" title="${esc(mp.marketplace.url)}">${esc(mp.marketplace.name || 'Marketplace')}</button>`).join('');
}

function renderBanner() {
  const slot = document.getElementById('banner-slot');
  let n = 0;
  for (const mp of LCAT) {
    if (mp.error) continue;
    for (const p of mp.plugins || []) {
      if (p.installed && p.upgrade && p.upgrade.compatible && p.upgrade.available) n++;
    }
  }
  if (!n) { slot.innerHTML = ''; return; }
  slot.innerHTML = `<div class="upgrade-banner" data-testid="mp-upgrade-banner">
    <span class="upgrade-banner-text">${n} update${n === 1 ? '' : 's'} available</span>
    <button class="upd" id="btn-update-all" data-act="update-all" data-testid="mp-update-all">Update all (${n})</button>
  </div>`;
}

function renderDiscover() {
  page('discover');
  const mp = curMp();
  const rich = curRich();
  const nameEl = document.getElementById('mp-name');
  if (rich && rich.marketplace && rich.marketplace.slug !== 'official') nameEl.textContent = ' ' + rich.marketplace.name;
  else if (!rich && mp && LCAT.length > 1) nameEl.textContent = ' ' + (mp.marketplace.name || '-Marketplace');
  else nameEl.textContent = '-Marketplace';

  const errBox = mp && mp.error
    ? `<div class="mp-error">${esc(mp.marketplace.name || 'Marketplace')}: could not load — ${esc(mp.error)}</div>` : '';

  if (rich) {
    const origin = srcInfo(mp).origin;
    document.getElementById('heroes').innerHTML = errBox + rich.heroes.map((p) => heroCard(p, origin)).join('');
    document.getElementById('essentials').innerHTML = section('Essentials', rich.essentials, origin);
    document.getElementById('bigheroes').innerHTML = rich.features.map((p) => heroCard(p, origin, 'big')).join('');
    document.getElementById('toppicks').innerHTML = section('Top picks', rich.top_picks, origin);
    document.getElementById('catsections').innerHTML = (rich.categories || []).map((c) => {
      const more = c.total >= 4 ? `<a data-act="opencat" data-slug="${esc(c.slug)}" data-label="${esc(c.label)}">More ${esc(c.label)} »</a>` : '';
      return section(c.label, c.plugins, origin, more);
    }).join('');
    document.getElementById('flatlist').innerHTML = '';
  } else {
    // Flat fallback: cards straight from Luna's catalog (no marketplace JSON).
    ['heroes', 'essentials', 'bigheroes', 'toppicks', 'catsections'].forEach((id) => { document.getElementById(id).innerHTML = ''; });
    document.getElementById('heroes').innerHTML = errBox;
    const plugins = mp && !mp.error ? (mp.plugins || []) : [];
    document.getElementById('flatlist').innerHTML = plugins.length
      ? `<div class="cards" style="margin-top:4px">${plugins.map((p) => card({ name: p.name, description: p.description, latest_version: p.version, media: [] }, '')).join('')}</div>`
      : (mp && !mp.error ? '<div class="emptymsg">Nothing here yet.</div>' : '');
  }
}

function page(id) {
  for (const v of ['discover', 'catpage', 'detail'])
    document.getElementById(v).classList.toggle('hidden', v !== id);
  document.getElementById('main').scrollTo(0, 0);
}

// ---- category page / search results ----
function renderRows() {
  page('catpage');
  catShown = Math.min(catRows.length, catShown + PAGE);
  const origin = (srcInfo(curMp()) || {}).origin || '';
  document.getElementById('cat-rows').innerHTML = catRows.slice(0, catShown).map((p) => rowHtml(p, origin)).join('');
  document.getElementById('cat-more').classList.toggle('hidden', catShown >= catRows.length);
  document.getElementById('cat-empty').classList.toggle('hidden', catRows.length > 0);
}

async function openCat(slug, label) {
  document.getElementById('cat-title').textContent = label;
  const info = srcInfo(curMp());
  catRows = [];
  if (info) {
    try {
      const r = await fetch(`${info.origin}/api/catalog/${encodeURIComponent(info.slug)}?category=${encodeURIComponent(slug)}&sort=downloads`);
      if (r.ok) catRows = await r.json();
    } catch {}
  }
  catShown = 0;
  VIEW = 'catpage';
  renderRows();
}

let ALL = null, qTimer = null;
document.getElementById('q').addEventListener('input', (e) => {
  clearTimeout(qTimer);
  qTimer = setTimeout(() => doSearch(e.target.value.trim()), 180);
});

async function doSearch(q) {
  if (!q) { goDiscover(); return; }
  const mp = curMp();
  const info = mp ? srcInfo(mp) : null;
  if (info) {
    if (!ALL) {
      try {
        const r = await fetch(`${info.origin}/api/catalog/${encodeURIComponent(info.slug)}?per_page=200`);
        ALL = r.ok ? await r.json() : [];
      } catch { ALL = []; }
    }
    const needle = q.toLowerCase();
    catRows = ALL.filter((p) =>
      p.name.toLowerCase().includes(needle) ||
      (p.description || '').toLowerCase().includes(needle) ||
      (p.tags || []).some((t) => t.toLowerCase().includes(needle)));
  } else {
    const needle = q.toLowerCase();
    catRows = (mp && !mp.error ? mp.plugins || [] : [])
      .filter((p) => p.name.toLowerCase().includes(needle) || (p.description || '').toLowerCase().includes(needle))
      .map((p) => ({ name: p.name, description: p.description, latest_version: p.version, media: [] }));
  }
  document.getElementById('cat-title').textContent = `Results for "${q}"`;
  catShown = 0;
  VIEW = 'catpage';
  renderRows();
}

function goDiscover() {
  document.getElementById('q').value = '';
  ALL = null;
  VIEW = 'discover';
  renderDiscover();
}

// ---- detail page ----
async function openDetail(name) {
  const mp = curMp();
  const info = mp ? srcInfo(mp) : null;
  const le = lunaEntry(name);
  if (!info) {
    // No marketplace JSON — minimal detail from Luna's catalog entry.
    DETAIL = le ? {
      plugin: { name: le.name, description: le.description, latest_version: le.version, media: [] },
      reviews: null, versions: [], origin: '', slug: '',
    } : null;
    if (DETAIL) { VIEW = 'detail'; renderDetail(); }
    return;
  }
  setStatus('Loading plugin…');
  try {
    const base = `${info.origin}/api/catalog/${encodeURIComponent(info.slug)}/${encodeURIComponent(name)}`;
    const pR = await fetch(base);
    if (!pR.ok) throw new Error('Plugin not found');
    const plugin = await pR.json();
    const [rR, vR] = await Promise.all([
      fetch(`${base}/reviews?sort=${reviewSort}`).catch(() => null),
      fetch(`${base}/versions`).catch(() => null),
    ]);
    DETAIL = {
      plugin,
      reviews: rR && rR.ok ? await rR.json() : null,
      versions: vR && vR.ok ? await vR.json() : [],
      origin: info.origin,
      slug: info.slug,
    };
    VIEW = 'detail';
    renderDetail();
  } catch (e) {
    setStatus(`Error: ${e.message}`);
  } finally {
    const total = LCAT.reduce((n, m) => n + (m.error ? 0 : (m.plugins || []).length), 0);
    if (VIEW === 'detail') setStatus(`${total} plugin(s) across ${LCAT.length} marketplace(s)`);
  }
}

function fmtDate(ts) { return ts ? new Date(ts * 1000).toLocaleDateString('en-US', { year: 'numeric', month: 'short', day: 'numeric' }) : '—'; }
function relDate(ts) {
  const d = Math.floor((Date.now() / 1000 - ts) / 86400);
  if (d < 1) return 'today'; if (d < 30) return d + 'd ago';
  if (d < 365) return Math.floor(d / 30) + 'mo ago'; return Math.floor(d / 365) + 'y ago';
}

function prose(md) {
  return esc(md || '').split('\n').map((line) => {
    const m = line.match(/^#{1,3}\s+(.*)/);
    return m ? `<span class="h">${m[1]}</span>` : line;
  }).join('\n')
    .replace(/\*\*([^*\n]+)\*\*/g, '<b>$1</b>')
    .replace(/`([^`\n]+)`/g, '<code style="background:rgba(255,255,255,.06);border-radius:4px;padding:0 5px;font-size:13px">$1</code>');
}

function accessHtml(p) {
  const cells = [];
  cells.push(`<div class="acc"><div class="k">Tools</div><div class="v">${p.tool_count ? p.tool_count + ' agent tool' + (p.tool_count > 1 ? 's' : '') : 'None'}</div></div>`);
  cells.push(`<div class="acc"><div class="k">Vault access</div><div class="v">${p.requires_vault_access ? '<span class="pill warn">Reads stored credentials</span>' : 'No'}</div></div>`);
  const eg = p.requires_egress || [];
  cells.push(`<div class="acc"><div class="k">Network</div><div class="v">${eg.length ? eg.map((h) => `<span class="pill">${esc(h)}</span>`).join('') : 'No outbound access declared'}</div></div>`);
  cells.push(`<div class="acc"><div class="k">UI</div><div class="v">${[p.requires_ui_iframe ? 'Embedded panel' : null, p.requires_settings_tab ? 'Settings tab' : null].filter(Boolean).join(' · ') || 'None'}</div></div>`);
  const pols = (p.tool_policies || []).map((t) => {
    const pol = t.approval || t.policy || 'ask';
    return `<span class="pill${pol === 'auto' || pol === 'allow' ? '' : ' warn'}">${esc(t.name || '')} · ${esc(pol)}</span>`;
  }).join('');
  if (pols) cells.push(`<div class="acc" style="grid-column:1/-1"><div class="k">Tool approval</div><div class="v">${pols}</div></div>`);
  return `<div class="access">${cells.join('')}</div>`;
}

function reviewsHtml() {
  const R = DETAIL.reviews;
  if (!R) return '';
  const s = R.summary;
  const maxH = Math.max(1, ...Object.values(s.histogram));
  const histo = [5, 4, 3, 2, 1].map((star) =>
    `<div class="hrow"><span style="width:10px">${star}</span><div class="hbarr"><i style="width:${Math.round((s.histogram[star] || 0) / maxH * 100)}%"></i></div><span style="width:26px;text-align:right">${s.histogram[star] || 0}</span></div>`).join('');

  const mpUrl = `${DETAIL.origin}/browse/${encodeURIComponent(DETAIL.slug)}/plugin/${encodeURIComponent(DETAIL.plugin.name)}`;
  const note = `<div class="reviewnote">Reviews come from certified Luna owners. <a href="${esc(mpUrl)}" target="_blank" rel="noopener">Write yours on the marketplace ↗</a></div>`;

  const list = R.reviews.map((r) => `<div class="review">
      <div class="rh">${starsHtml(r.rating)}<span class="rt">${esc(r.title)}</span></div>
      <div class="ra">${esc(r.author)} · ${relDate(r.created_at)}${r.edited ? ' · edited' : ''}${r.plugin_version ? ' · v' + esc(r.plugin_version) : ''}</div>
      ${r.body ? `<div class="rb">${esc(r.body)}</div>` : ''}
      <div class="rf"><span>Helpful (${r.helpful_count})</span></div>
      ${r.response_body ? `<div class="presp"><div class="ph">Response from the publisher</div><div class="pb">${esc(r.response_body)}</div></div>` : ''}
    </div>`).join('') || '<div style="color:var(--dim);padding:16px 0;font-size:14px">No reviews yet.</div>';

  return `<div class="sect"><h2>Ratings &amp; Reviews</h2>
    <div class="ratrow">
      <div class="ratbig"><div class="n">${(s.average || 0).toFixed(1)}</div><div class="of">out of 5 · ${s.count} rating${s.count === 1 ? '' : 's'}</div></div>
      <div class="histo">${histo}</div>
    </div>
    ${note}
    <div style="display:flex;justify-content:flex-end;margin-top:16px">
      <select class="hbtn" id="rv-sort">
        <option value="helpful"${reviewSort === 'helpful' ? ' selected' : ''}>Most helpful</option>
        <option value="recent"${reviewSort === 'recent' ? ' selected' : ''}>Most recent</option>
        <option value="critical"${reviewSort === 'critical' ? ' selected' : ''}>Most critical</option>
      </select>
    </div>
    <div id="rv-list">${list}</div>
  </div>`;
}

function renderDetail() {
  page('detail');
  const p = DETAIL.plugin;
  const origin = DETAIL.origin;
  const shots = (p.media || []).filter((m) => m.kind === 'screenshot');
  const latest = DETAIL.versions[0];

  const infoRows = [
    ['Publisher', esc(p.namespace || '—')],
    ['Version', esc(p.latest_version || '—')],
    ['Category', esc(catLabel(p.category) || '—')],
    ['Downloads', String(p.download_count || 0)],
    ['License', esc(p.license || '—')],
    ['First published', fmtDate(p.created_at)],
    ['Updated', fmtDate(p.updated_at)],
  ];
  if (p.source_url) infoRows.push(['Source', `<a href="${esc(p.source_url)}" target="_blank" rel="noopener">Repository</a>`]);

  const hasRating = p.rating_count != null;

  document.getElementById('detail-content').innerHTML = `
    <div class="dhead">
      <div class="ic">${iconHtml(p, origin, 44)}</div>
      <div style="flex:1;min-width:0">
        <h1>${displayName(p.name)}</h1>
        <div class="sub">${esc(p.description || '')}</div>
        <div class="metaline">
          ${hasRating ? `${starsHtml(p.rating_average)} <span>${(p.rating_average || 0).toFixed(1)} (${p.rating_count})</span><span>·</span>` : ''}
          <span>${esc(catLabel(p.category) || 'Plugin')}</span>
          ${p.download_count != null ? `<span>·</span><span>${p.download_count} downloads</span>` : ''}
        </div>
        <div class="actions">${actionUi(p.name)}</div>
        ${fallbackNote(p.name)}
      </div>
    </div>

    ${shots.length ? `<div class="shots">${shots.map((m) =>
      (m.content_type || '').startsWith('video')
        ? `<video src="${esc(absUrl(origin, m.url))}" controls muted></video>`
        : `<img src="${esc(absUrl(origin, m.url))}" alt="${esc(m.caption)}" loading="lazy">`).join('')}</div>` : ''}

    ${p.tool_count != null ? `<div class="sect"><h2>Agent Access</h2>${accessHtml(p)}</div>` : ''}

    ${p.readme ? `<div class="sect"><h2>About</h2><div class="prose">${prose(p.readme)}</div></div>` : ''}

    ${latest ? `<div class="sect"><h2>What's New</h2>
      <div style="font-size:13px;color:var(--dim);margin-bottom:6px">Version ${esc(latest.version)} · ${fmtDate(latest.published_at)}</div>
      ${DETAIL.versions.length > 1 ? `<a style="font-size:13px;cursor:pointer" data-act="verlist">Version history (${DETAIL.versions.length})</a>
      <div class="verlist hidden" id="verlist">${DETAIL.versions.map((v) =>
        `<div class="verrow"><span style="min-width:70px">${esc(v.version)}</span><span style="color:var(--dim)">${fmtDate(v.published_at)}</span>${v.yanked ? '<span class="yanked">yanked</span>' : ''}</div>`).join('')}</div>` : ''}
    </div>` : ''}

    ${reviewsHtml()}

    <div class="sect"><h2>Information</h2>
      <div class="info">${infoRows.map(([k, v]) => `<div class="inforow"><span class="k">${k}</span><span class="v">${v}</span></div>`).join('')}</div>
    </div>
  `;

  const sortSel = document.getElementById('rv-sort');
  if (sortSel) {
    sortSel.addEventListener('change', async () => {
      reviewSort = sortSel.value;
      try {
        const r = await fetch(`${origin}/api/catalog/${encodeURIComponent(DETAIL.slug)}/${encodeURIComponent(p.name)}/reviews?sort=${reviewSort}`);
        if (r.ok) { DETAIL.reviews = await r.json(); renderDetail(); }
      } catch {}
    });
  }
}

// ---- actions ----
async function doInstall(btn) {
  const name = btn.dataset.name;
  const version = btn.dataset.version;
  btn.disabled = true;
  btn.textContent = 'Installing…';
  setStatus(`Installing ${name}…`);
  try {
    await api('POST', '/install', {
      marketplace_url: btn.dataset.url, name, version,
      marketplace_id: btn.dataset.mpid || null,
    });
    setStatus(`Installed ${name}`);
    try { window.parent.postMessage({ type: 'luna-plugin-changed' }, window.location.origin); } catch {}
    showNotice('success', { title: 'Plugin installed', message: `${name} v${version} is ready.` });
    await load(true);
    if (VIEW === 'detail' && DETAIL && DETAIL.plugin.name === name) renderDetail();
  } catch (e) {
    btn.disabled = false;
    btn.textContent = 'Install';
    setStatus(`Install failed: ${e.message}`);
    showNotice('error', {
      title: `Couldn't install ${name}`, message: e.message, hint: e.hint,
      stage: e.stage, traceId: e.traceId, technical: e.technical, plugin: name, version,
    });
  }
}

async function doUpgrade(btn) {
  const name = btn.dataset.name;
  const version = btn.dataset.version;
  const label = btn.textContent;
  btn.disabled = true;
  btn.textContent = 'Updating…';
  setStatus(`Updating ${name}…`);
  try {
    await api('POST', '/upgrade', {
      marketplace_url: btn.dataset.url, name, version,
      marketplace_id: btn.dataset.mpid || null,
    });
    setStatus(`Updated ${name} to v${version}`);
    try { window.parent.postMessage({ type: 'luna-plugin-changed' }, window.location.origin); } catch {}
    showNotice('success', { title: 'Plugin updated', message: `${name} is now v${version}.` });
    if (name === 'plugin-marketplace-ui') { setTimeout(() => window.location.reload(), 1200); return; }
    await load(true);
    if (VIEW === 'detail' && DETAIL && DETAIL.plugin.name === name) renderDetail();
  } catch (e) {
    btn.disabled = false;
    btn.textContent = label;
    setStatus(`Update failed: ${e.message}`);
    showNotice('error', {
      title: `Couldn't update ${name}`, message: e.message, hint: e.hint,
      stage: e.stage, traceId: e.traceId, technical: e.technical, plugin: name, version,
    });
  }
}

async function upgradeAll() {
  const btn = document.getElementById('btn-update-all');
  if (btn) { btn.disabled = true; btn.textContent = 'Updating…'; }
  setStatus('Updating all plugins… (each update is staged and rolled back on failure)');
  try {
    const res = await api('POST', '/upgrade-all');
    const okN = (res.upgraded || []).length;
    const errs = res.errors || [];
    setStatus(errs.length ? `Updated ${okN}, ${errs.length} failed` : `Updated ${okN} plugin(s)`);
    try { window.parent.postMessage({ type: 'luna-plugin-changed' }, window.location.origin); } catch {}
    if (errs.length) {
      showNotice('error', {
        title: `${errs.length} update${errs.length === 1 ? '' : 's'} failed`,
        message: okN ? `${okN} updated successfully, but these couldn't be updated:` : "These plugins couldn't be updated:",
        items: errs.map((e) => ({ name: e.name, error: e.error, hint: e.hint, traceId: e.trace_id })),
        technical: errs.map((e) => `${e.name}: ${e.error}${e.trace_id ? ` (trace ${e.trace_id})` : ''}`).join('\n'),
      });
    } else if (okN) {
      showNotice('success', { title: 'All plugins updated', message: `Updated ${okN} plugin(s).` });
    }
    if ((res.upgraded || []).some((u) => (u.name || u) === 'plugin-marketplace-ui')) {
      setTimeout(() => window.location.reload(), 1200);
      return;
    }
    await load(true);
  } catch (e) {
    if (btn) { btn.disabled = false; btn.textContent = 'Update all'; }
    setStatus(`Update all failed: ${e.message}`);
    showNotice('error', {
      title: 'Update all failed', message: e.message, hint: e.hint,
      stage: e.stage, traceId: e.traceId, technical: e.technical,
    });
  }
}

// ---- event delegation ----
document.getElementById('main').addEventListener('click', (e) => {
  const act = e.target.closest('[data-act]');
  if (act) {
    e.stopPropagation();
    const a = act.dataset.act;
    if (a === 'install') return doInstall(act);
    if (a === 'upgrade') return doUpgrade(act);
    if (a === 'update-all') return upgradeAll();
    if (a === 'opencat') return openCat(act.dataset.slug, act.dataset.label);
    if (a === 'src') { CUR = Number(act.dataset.i) || 0; ALL = null; goDiscover(); renderTabs(); return; }
    if (a === 'verlist') { const el = document.getElementById('verlist'); if (el) el.classList.toggle('hidden'); return; }
    return;
  }
  if (e.target.closest('a')) return; // gear / external links
  const open = e.target.closest('[data-open]');
  if (open) openDetail(open.dataset.open);
});
document.getElementById('cat-back').addEventListener('click', goDiscover);
document.getElementById('detail-back').addEventListener('click', () => {
  // Back to wherever we came from: a live category/search page or discover.
  if (catRows.length) { VIEW = 'catpage'; catShown = Math.max(catShown, PAGE) - PAGE; renderRows(); }
  else goDiscover();
});
document.getElementById('cat-more').addEventListener('click', renderRows);
document.getElementById('logo').addEventListener('click', goDiscover);
document.getElementById('btn-refresh').addEventListener('click', () => load(true));

// E12 handshake: declare this pane ready so the shell forwards luna-plugin-event.
try { window.parent.postMessage({ type: 'luna-ui-ready' }, window.location.origin); } catch {}

if (!TOKEN) {
  try { window.parent.postMessage({ type: 'luna-request-auth' }, window.location.origin); } catch {}
}
load();

/* Reflow (not shrink) down to 640px, then scale the whole page — same behavior
   as the hosted marketplace, tuned for the pane living in a split view. */
const MIN_W = 640;
function fitScale() { document.documentElement.style.zoom = Math.min(1, window.innerWidth / MIN_W) || 1; }
addEventListener('resize', fitScale);
fitScale();
