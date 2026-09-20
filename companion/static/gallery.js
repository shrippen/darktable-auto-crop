// Galerie: drei Baender (Gruen/Gelb/Rot) mit Kacheln, Auswahl, Tastatur und Drag-and-drop.
import { api, thumbUrl } from './api.js';
import { T, S, esc } from './i18n.js';
import { store, byId, isEditable, selectedIds, sorted } from './store.js';
import { guard, toast } from './ui.js';

const GROUPS = ['green', 'yellow', 'red'];
const ICON = {
  green: '<path d="M5 12.5l4.5 4.5L19 7.5" stroke-linecap="square"/>',
  yellow: '<path d="M5 12h14" stroke-linecap="square"/>',
  red: '<path d="M12 5v9M12 18.5v.5" stroke-linecap="square"/>',
};
const tiles = new Map();          // id -> Element
let hooks = { refresh: async () => {}, openEditor: () => {}, onSelection: () => {} };

export function initGallery(h) {
  hooks = { ...hooks, ...h };
  const host = document.getElementById('bands');
  for (const g of GROUPS) {
    const band = document.createElement('div');
    band.className = 'band';
    band.dataset.tier = g;
    band.innerHTML = `<div class="band-head"><span>${T('band_' + g)}<span class="band-hint">${T('band_' + g + '_h')}</span></span><span data-count>0</span></div>`;
    host.appendChild(band);
  }
  host.addEventListener('click', onClick);
  host.addEventListener('dblclick', (e) => { const t = e.target.closest('.tile'); if (t) hooks.openEditor(t.dataset.id); });
  host.addEventListener('keydown', onKey);
  host.addEventListener('dragstart', onDragStart);
  host.addEventListener('dragend', clearDrag);
  host.addEventListener('dragover', onDragOver);
  host.addEventListener('dragleave', (e) => { const b = e.target.closest('.band'); if (b && !b.contains(e.relatedTarget)) b.classList.remove('is-over'); });
  host.addEventListener('drop', onDrop);
  document.addEventListener('keydown', onGlobalKey);
}

// ── Rendern ──────────────────────────────────────────────────────────────────

function insetOf(c) {
  if (!c) return '0%';
  return `${c[1] * 100}% ${(1 - c[2]) * 100}% ${(1 - c[3]) * 100}% ${c[0] * 100}%`;
}

function tileMarkup(img) {
  return `<span class="tile-img"><span class="tile-frame"><img alt="" decoding="async" loading="lazy"><span class="tile-crop"></span></span><span class="tile-noimg" hidden></span><span class="tile-check"><svg viewBox="0 0 24 24"><path d="M5 12.5l4.5 4.5L19 7.5" stroke-linecap="square"/></svg></span><span class="tile-badges"></span></span>
    <span class="tile-meta"><span class="tile-name"></span><span class="tile-conf"></span><span class="tile-sub"></span></span>`;
}

function updateTile(el, img) {
  const sig = JSON.stringify([img.group, img.confidence, img.crop, img.decision, img.status, img.error,
    img.proposal && (img.proposal.crop || img.proposal.error), img.manual_crop, img.export_size,
    img.has_export, img.apply, store.selected.has(String(img.id)), store.s.phase]);
  if (el._sig === sig) return;
  el._sig = sig;
  const sel = store.selected.has(String(img.id));
  el.dataset.tier = img.group;
  el.setAttribute('aria-selected', sel ? 'true' : 'false');
  el.draggable = isEditable();
  el.toggleAttribute('data-error', img.status === 'error');
  const frame = el.querySelector('.tile-frame');
  const ar = img.export_size ? img.export_size[0] / img.export_size[1] : 1.5;
  frame.style.setProperty('--ar', ar);
  frame.classList.toggle('is-wide', ar >= 1.5);
  frame.classList.toggle('is-tall', ar < 1.5);
  const im = frame.querySelector('img');
  const noimg = el.querySelector('.tile-noimg');
  if (img.has_export) {
    const src = thumbUrl(img.id, 420);
    if (im.getAttribute('src') !== src) im.setAttribute('src', src);
    im.hidden = false; noimg.hidden = true;
  } else {
    im.hidden = true; noimg.hidden = false; noimg.innerHTML = T('no_export');
  }
  const crop = el.querySelector('.tile-crop');
  crop.style.setProperty('--in', insetOf(img.crop));
  crop.hidden = !img.crop;
  el.querySelector('.tile-name').textContent = img.filename;
  const conf = el.querySelector('.tile-conf');
  conf.innerHTML = img.status === 'error'
    ? `<svg viewBox="0 0 24 24">${ICON.red}</svg>–`
    : `<svg viewBox="0 0 24 24">${ICON[img.group]}</svg>${img.confidence == null ? '–' : img.confidence.toFixed(2)}`;
  el.querySelector('.tile-sub').textContent = img.status === 'error' ? (img.error || S('error_tile')) : (img.method || '');
  const badges = [];
  if (img.manual_crop) badges.push(`<span class="tile-badge is-hl">${T('manual')}</span>`);
  if (img.proposal && img.proposal.crop) badges.push(`<span class="tile-badge is-hl">${T('proposal')}</span>`);
  if (img.decision === 'skip') badges.push(`<span class="tile-badge is-off">${T('skip')}</span>`);
  if (img.decision === 'accept') badges.push(`<span class="tile-badge">${T('accept')}</span>`);
  el.querySelector('.tile-badges').innerHTML = badges.join('');
  // Vorschlag der Neu-Erkennung als zweites Overlay ist im Editor; hier nur die Marke.
}

export function renderGallery() {
  const s = store.s;
  const host = document.getElementById('bands');
  host.classList.toggle('is-rows', store.view === 'rows');
  host.style.setProperty('--tile-min', { s: '110px', m: '150px', l: '210px' }[store.size] + '');
  host.classList.toggle('is-locked', !isEditable());
  const seen = new Set();
  const byGroup = { green: [], yellow: [], red: [] };
  for (const img of sorted(s.images)) byGroup[img.group].push(img);
  for (const g of GROUPS) {
    const band = host.querySelector(`.band[data-tier="${g}"]`);
    let empty = band.querySelector('.band-empty');
    let i = 1;                                     // children[0] = Kopf
    for (const img of byGroup[g]) {
      const key = String(img.id);
      seen.add(key);
      let el = tiles.get(key);
      if (!el) {
        el = document.createElement('button');
        el.type = 'button';
        el.className = 'tile';
        el.dataset.id = key;
        el.innerHTML = tileMarkup(img);
        tiles.set(key, el);
      }
      updateTile(el, img);
      const at = band.children[i];
      if (at !== el) band.insertBefore(el, at || null);
      i += 1;
    }
    band.querySelector('[data-count]').textContent = byGroup[g].length;
    if (!byGroup[g].length) {
      if (!empty) { empty = document.createElement('div'); empty.className = 'band-empty'; band.appendChild(empty); }
      empty.innerHTML = T('empty_band');
    } else if (empty) empty.remove();
  }
  for (const [key, el] of tiles) if (!seen.has(key)) { el.remove(); tiles.delete(key); }
}

// ── Auswahl ──────────────────────────────────────────────────────────────────

function visibleIds() { return [...document.querySelectorAll('#bands .tile')].map((t) => t.dataset.id); }

export function setSelection(ids) {
  store.selected = new Set(ids.map(String));
  for (const [k, el] of tiles) {
    const on = store.selected.has(k);
    el.setAttribute('aria-selected', on ? 'true' : 'false');
    if (el._sig) el._sig = null;
  }
  hooks.onSelection();
}

function onClick(e) {
  const t = e.target.closest('.tile');
  if (!t) return;
  const id = t.dataset.id;
  if (e.shiftKey && store.anchor) {
    const ids = visibleIds();
    const a = ids.indexOf(store.anchor), b = ids.indexOf(id);
    if (a >= 0 && b >= 0) setSelection(ids.slice(Math.min(a, b), Math.max(a, b) + 1));
  } else if (e.ctrlKey || e.metaKey) {
    const next = new Set(store.selected);
    next.has(id) ? next.delete(id) : next.add(id);
    setSelection([...next]);
    store.anchor = id;
  } else {
    setSelection(store.selected.size === 1 && store.selected.has(id) ? [] : [id]);
    store.anchor = id;
  }
  t.focus();
}

// ── Tastatur ─────────────────────────────────────────────────────────────────

function neighbour(cur, dir) {
  const els = [...document.querySelectorAll('#bands .tile')];
  const i = els.indexOf(cur);
  if (dir === 'left') return els[i - 1];
  if (dir === 'right') return els[i + 1];
  const r = cur.getBoundingClientRect();
  const cx = r.left + r.width / 2;
  let best = null, bd = Infinity;
  for (const el of els) {
    const q = el.getBoundingClientRect();
    const dy = q.top - r.top;
    if (dir === 'down' ? dy <= r.height / 2 : dy >= -r.height / 2) continue;
    const d = Math.abs(dy) * 4 + Math.abs(q.left + q.width / 2 - cx);
    if (d < bd) { bd = d; best = el; }
  }
  return best;
}

function onKey(e) {
  const t = e.target.closest('.tile');
  if (!t) return;
  const dir = { ArrowLeft: 'left', ArrowRight: 'right', ArrowUp: 'up', ArrowDown: 'down' }[e.key];
  if (dir) {
    e.preventDefault();
    const n = neighbour(t, dir);
    if (n) {
      n.focus();
      if (e.shiftKey) setSelection([...store.selected, n.dataset.id]);
    }
  } else if (e.key === 'Enter') { e.preventDefault(); hooks.openEditor(t.dataset.id); }
  else if (e.key === ' ') {
    e.preventDefault();
    const next = new Set(store.selected);
    next.has(t.dataset.id) ? next.delete(t.dataset.id) : next.add(t.dataset.id);
    setSelection([...next]);
  }
}

// Kuerzel arbeiten auf der Auswahl (oder dem fokussierten Bild)
async function onGlobalKey(e) {
  if (e.defaultPrevented || e.ctrlKey || e.metaKey || e.altKey) return;
  if (!document.getElementById('editor').hidden) return;
  if (document.querySelector('#dialog-host .scrim')) return;
  const tag = (e.target.tagName || '').toLowerCase();
  if (['input', 'select', 'textarea'].includes(tag)) return;
  if (!store.s || !isEditable()) return;
  const focused = document.activeElement && document.activeElement.closest && document.activeElement.closest('.tile');
  const ids = selectedIds().length ? selectedIds() : (focused ? [focused.dataset.id] : []);
  const k = e.key.toLowerCase();
  if (k === 'z') { e.preventDefault(); await guard(() => api('POST', 'undo', { scope: 'session' })); await hooks.refresh(); return; }
  if (k === 'e') { const id = ids[0]; if (id) { e.preventDefault(); hooks.openEditor(id); } return; }
  if (!ids.length) return;
  let patch = null;
  if (k === '1') patch = { group: 'green' };
  else if (k === '2') patch = { group: 'yellow' };
  else if (k === '3') patch = { group: 'red' };
  else if (k === 'a') patch = { decision: 'accept' };
  else if (k === 's') patch = { decision: 'skip' };
  else if (k === '0') patch = { group: null, decision: null };
  if (!patch) return;
  e.preventDefault();
  await guard(() => api('PATCH', 'images', { ids, ...patch }), hooks.refresh);
  await hooks.refresh();
}

// ── Drag-and-drop ────────────────────────────────────────────────────────────

let dragIds = [];
function onDragStart(e) {
  const t = e.target.closest('.tile');
  if (!t || !isEditable()) { e.preventDefault(); return; }
  if (!store.selected.has(t.dataset.id)) setSelection([t.dataset.id]);
  dragIds = selectedIds();
  dragIds.forEach((id) => tiles.get(String(id)) && tiles.get(String(id)).classList.add('is-dragging'));
  e.dataTransfer.effectAllowed = 'move';
  e.dataTransfer.setData('text/plain', 'tiles');
  if (dragIds.length > 1) {
    const g = document.createElement('div');
    g.className = 'drag-badge';
    g.style.cssText = 'position:absolute;top:-999px;left:-999px';
    g.textContent = dragIds.length;
    document.body.appendChild(g);
    e.dataTransfer.setDragImage(g, 12, 12);
    setTimeout(() => g.remove(), 0);
  }
}
function clearDrag() {
  dragIds.forEach((id) => tiles.get(String(id)) && tiles.get(String(id)).classList.remove('is-dragging'));
  dragIds = [];
  document.querySelectorAll('#bands .band').forEach((b) => b.classList.remove('is-over'));
}
function onDragOver(e) {
  const b = e.target.closest('.band');
  if (!b || !dragIds.length) return;
  e.preventDefault();
  document.querySelectorAll('#bands .band').forEach((x) => x.classList.toggle('is-over', x === b));
}
async function onDrop(e) {
  const b = e.target.closest('.band');
  if (!b || !dragIds.length) return;
  e.preventDefault();
  const ids = dragIds.slice();
  const target = b.dataset.tier;
  clearDrag();
  // Zurueck in die automatische Gruppe = Nutzerentscheid aufheben
  const auto = ids.every((id) => byId(id) && byId(id).auto_group === target);
  await guard(() => api('PATCH', 'images', { ids, group: auto ? null : target }), hooks.refresh);
  await hooks.refresh();
  toast(T('saved'), 'ok');
}
