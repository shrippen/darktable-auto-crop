// Detailansicht mit Crop-Editor: Ziehgriffe, Seitenverhaeltnis-Sperre, Kandidaten, Filmstreifen.
import { api, thumbUrl } from './api.js';
import { T, S, esc } from './i18n.js';
import { store, byId, isEditable, galleryOrder } from './store.js';
import { guard, toast } from './ui.js';

const MIN = 0.02;
const HANDLES = ['nw', 'n', 'ne', 'w', 'e', 'sw', 's', 'se'];
const CAND_COLORS = ['blue', 'purple', 'aqua', 'green', 'red'];
let hooks = { refresh: async () => {}, onClose: () => {} };
let ed = null;   // {id, crop, ratio, drag, cands, saveTimer}

export const isOpen = () => !!ed;
export function initEditor(h) { hooks = { ...hooks, ...h }; }

const el = () => document.getElementById('editor');

// ── Vorladen ─────────────────────────────────────────────────────────────────
// Die zwei naechsten Bilder vor und nach dem aktuellen (in Galerie-Reihenfolge) werden
// schon im Hintergrund geladen, damit das Blaettern sofort geht. Der Browser-Cache
// liefert sie danach beim Wechsel; die Image-Objekte bleiben referenziert, bis sie nicht
// mehr in der Nachbarschaft liegen.
const PREFETCH = 2;
const BIG = 1800;
const prefetched = new Map();   // url -> Image

function prefetch() {
  if (!ed || !store.s) return;
  const list = filmImages();
  const i = list.findIndex((x) => String(x.id) === ed.id);
  if (i < 0) return;
  const want = [];
  for (let d = 1; d <= PREFETCH; d += 1) {          // erst vorwaerts, dann rueckwaerts
    if (list[i + d]) want.push(list[i + d]);
    if (list[i - d]) want.push(list[i - d]);
  }
  const keep = new Set();
  for (const img of want) {
    if (!img.has_export) continue;
    const url = thumbUrl(img.id, BIG);
    keep.add(url);
    if (!prefetched.has(url)) {
      const im = new Image();
      im.decoding = 'async';
      im.src = url;
      prefetched.set(url, im);
    }
  }
  keep.add(thumbUrl(ed.id, BIG));
  for (const url of [...prefetched.keys()]) if (!keep.has(url)) prefetched.delete(url);
}
const clamp = (v, a = 0, b = 1) => Math.min(b, Math.max(a, v));

// Blaettern und Filmstreifen folgen der Galerie: Gruppe, dann gewaehlte Sortierung.
function filmImages() {
  return galleryOrder(store.s.images);
}

export function openEditor(id) {
  const img = byId(id);
  if (!img) return;
  ed = { id: String(id), crop: null, ratio: 'free', drag: null, cands: null, saveTimer: null, lastDeg: null };
  el().hidden = false;
  document.body.style.overflow = 'hidden';
  build();
  document.addEventListener('keydown', onKey, true);
  window.addEventListener('resize', layout);
}

export function closeEditor() {
  if (!ed) return;
  flushSave();
  document.removeEventListener('keydown', onKey, true);
  window.removeEventListener('resize', layout);
  ed = null;
  prefetched.clear();
  el().hidden = true;
  el().innerHTML = '';
  document.body.style.overflow = '';
  hooks.onClose();
}

export function refreshEditor() {
  if (!ed) return;
  if (!byId(ed.id)) { closeEditor(); return; }
  if (ed.drag) return;                 // waehrend des Ziehens nichts ueberschreiben
  const im = el().querySelector('#ed-img');
  const src = thumbUrl(ed.id, BIG);
  if (im && im.getAttribute('src') !== src) { im.src = src; layout(); }     // geradegestellt <-> Original
  renderSide();
  renderStrip();
  syncCrop();
  prefetch();               // Reihenfolge kann sich durch Gruppenwechsel geaendert haben
}

function cur() { return byId(ed.id); }

// ── Aufbau ───────────────────────────────────────────────────────────────────

function build() {
  const img = cur();
  el().innerHTML = `
    <div class="editor-head">
      <h2 id="ed-title"></h2>
      <div class="group" style="display:flex;gap:.5rem;align-items:center">
        <button type="button" class="btn btn-outline btn-sm" data-act="prev">${T('prev')} [</button>
        <button type="button" class="btn btn-outline btn-sm" data-act="next">${T('next')} ]</button>
        <button type="button" class="btn btn-accent btn-sm" data-act="close">${T('close')} · Esc</button>
      </div>
    </div>
    <div class="editor-main">
      <div class="toolbar" id="ed-toolbar">
        <span class="toolbar-label">${T('lock_ratio')}</span>
        <div class="seg" role="group" id="ed-ratio">
          <button type="button" data-ratio="free" aria-pressed="true">${T('ratio_free')}</button>
          <button type="button" data-ratio="film" aria-pressed="false">${T('ratio_film')}</button>
          <button type="button" data-ratio="image" aria-pressed="false">${T('ratio_image')}</button>
        </div>
        <span class="toolbar-sep"></span>
        <button type="button" class="btn btn-outline btn-sm" data-act="tilt-toggle" aria-pressed="false" title="${esc(S('tilt_apply_h'))}">${T('tilt_apply')} · T</button>
        <span class="toolbar-sep"></span>
        <button type="button" class="btn btn-outline btn-sm" data-act="cands">${T('candidates_load')}</button>
        <span id="ed-cand-chips" class="chips"></span>
        <span class="toolbar-sep"></span>
        <button type="button" class="btn btn-outline btn-sm" data-act="lines" aria-pressed="true" title="${esc(S('lines_h'))}">${T('lines')}</button>
      </div>
      <div class="stage" id="ed-stage" aria-label="Crop">
        <div class="stage-frame" id="ed-frame">
          <img alt="" id="ed-img" draggable="false">
          <div class="cand" id="ed-det" hidden><span class="cand-tag"></span></div>
          <div class="cand cand-prop" id="ed-prop" hidden><span class="cand-tag"></span></div>
          <svg class="skewlines" id="ed-skewlines" viewBox="0 0 1 1" preserveAspectRatio="none" aria-hidden="true"></svg>
          <div id="ed-cands"></div>
          <div class="cropbox" id="ed-crop">${HANDLES.map((h) => `<i class="handle" data-h="${h}"></i>`).join('')}</div>
          <span class="readout" id="ed-readout"></span>
        </div>
      </div>
    </div>
    <aside class="editor-side" id="ed-side"></aside>
    <div class="strip" id="ed-strip" aria-label="Film"></div>`;
  const image = el().querySelector('#ed-img');
  image.addEventListener('load', layout);
  image.src = thumbUrl(img.id, BIG);
  el().addEventListener('click', onClick);
  el().addEventListener('change', (e) => {
    const inp = e.target.closest('[data-deg]');
    if (!inp) return;
    const v = parseFloat(inp.value);
    if (Number.isFinite(v) && Math.abs(v) <= 10) patch({ straighten: v === 0 ? null : { deg: v } });
    else toast(T('skew_range'), 'error');
  });
  const stage = el().querySelector('#ed-stage');
  stage.addEventListener('pointerdown', onDown);
  stage.addEventListener('pointermove', onMove);
  stage.addEventListener('pointerup', onUp);
  stage.addEventListener('pointercancel', onUp);
  renderSide();
  renderStrip();
  syncCrop();
  layout();
  prefetch();
}

function layout() {
  if (!ed) return;
  const stage = el().querySelector('#ed-stage');
  const frame = el().querySelector('#ed-frame');
  const img = cur();
  const im = el().querySelector('#ed-img');
  const vs = img.view_size || img.export_size;
  const ar = vs ? vs[0] / vs[1]
    : (im.naturalWidth ? im.naturalWidth / im.naturalHeight : 1.5);
  const pad = 12;
  const w = stage.clientWidth - pad * 2, h = stage.clientHeight - pad * 2;
  if (w <= 0 || h <= 0) return;
  let fw = w, fh = w / ar;
  if (fh > h) { fh = h; fw = h * ar; }
  frame.style.width = `${Math.floor(fw)}px`;
  frame.style.height = `${Math.floor(fh)}px`;
}

function renderStrip() {
  const img = cur();
  el().querySelector('#ed-strip').innerHTML = filmImages().map((i) =>
    `<button type="button" data-id="${esc(i.id)}" data-tier="${i.group}"${String(i.id) === ed.id ? ' aria-current="true"' : ''} title="${esc(i.filename)}"><img alt="" loading="lazy" src="${thumbUrl(i.id, 200)}" style="display:block;width:100%;height:100%;object-fit:cover"></button>`).join('');
  const active = el().querySelector('#ed-strip [aria-current]');
  if (active && active.scrollIntoView) active.scrollIntoView({ inline: 'center', block: 'nearest' });
}

// Die vier Faktoren der Konfidenz (siehe roadmap.md): zeigt bei einem falschen "gruenen"
// Ergebnis sofort, welcher Faktor die Fehleinschaetzung verursacht hat.
const PARTS = ['size_agree', 'edge_score', 'film_trust', 'exposure_factor'];
function confPartsHtml(img) {
  const cp = img.conf_parts;
  if (!cp) return `<div><h3>${T('conf_parts')}</h3><p class="field-hint">${T('cp_none')}</p></div>`;
  const vals = PARTS.filter((k) => typeof cp[k] === 'number');
  const min = Math.min(...vals.map((k) => cp[k]));
  const rows = vals.map((k) => {
    const v = cp[k];
    const weak = v === min && vals.length > 1;
    return `<div class="cp-row"><span>${T('cp_' + k)}${weak ? ` <em>· ${T('cp_weakest')}</em>` : ''}</span><b>${v.toFixed(2)}</b>
      <div class="progress-bar"><i data-tier="${v >= 0.7 ? 'green' : v >= 0.4 ? 'yellow' : 'red'}" style="--p:${Math.round(clamp(v) * 100)}%"></i></div></div>`;
  }).join('');
  return `<div><h3>${T('conf_parts')}</h3><div class="cp">${rows}</div><p class="field-hint">${T('conf_parts_h')}</p></div>`;
}

function refDevHtml(img) {
  if (!img.ref || store.s.mode !== 'folder') return '';
  const r = img.ref, f = (v) => (v > 0 ? '+' : '') + v;
  return `<div class="callout ${r.hit ? 'callout-ok' : 'callout-warn'}"><strong>${T('ref_dev')}: ${T(r.hit ? 'ref_hit' : 'ref_miss')}</strong>
    <div class="notice-list">${T('ref_dev_v', f(r.dx), f(r.dy), f(r.dw), f(r.dh), r.tol)}</div></div>`;
}

const fmtDeg = (d) => `${d > 0 ? '+' : ''}${d.toFixed(2)}°`;

// Schraeglage des Filmrahmens (gemessen an den vier Crop-Kanten) und Geradestellen in darktable.
function skewHtml(img) {
  const sk = img.skew, locked = !isEditable();
  if (!sk || sk.deg == null) return `<div><h3>${T('skew')}</h3><p class="field-hint">${T('skew_none')}</p></div>`;
  const sides = Object.entries(sk.sides || {}).map(([k, v]) => `${S('side_' + k)} ${fmtDeg(v)}`).join(' · ');
  const strong = Math.abs(sk.deg) >= 0.5;
  const rows = `<dl class="kv"><dt>${T('skew_measured')}</dt><dd>${fmtDeg(sk.deg)} ${sk.deg > 0 ? T('skew_cw') : T('skew_ccw')}</dd>
    <dt>${T('skew_sure')}</dt><dd>${sk.conf.toFixed(2)}${sk.conf < 0.4 ? ' · ' + T('skew_unsure') : ''}</dd></dl>
    <p class="field-hint">${esc(sides)}</p>`;
  let ctl;
  const corrected = img.straighten != null && sk.straight && Math.abs(sk.straight.deg - img.straighten) < 0.15;
  const target = store.s.mode === 'darktable' ? 'skew_on_h' : 'skew_on_folder_h';
  if (img.straighten != null) {
    ctl = `<div class="callout callout-ok"><strong>${T(store.s.mode === 'darktable' ? 'skew_on' : 'skew_on_folder')} ${fmtDeg(img.straighten)}</strong>
      <div class="chips" style="margin-top:.5rem;align-items:center">
        <label class="toolbar-label" for="ed-deg">${T('skew_angle')}</label>
        <input class="input" id="ed-deg" data-deg type="number" step="0.1" min="-10" max="10" value="${img.straighten}" style="width:6rem"${locked ? ' disabled' : ''}>
        <button type="button" class="btn btn-outline btn-sm" data-act="straighten-off"${locked ? ' disabled' : ''}>${T('skew_off')}</button></div>
      <p class="field-hint" style="margin-top:.5rem">${T(target)}</p>
      ${!img.manual_crop ? `<p class="field-hint">${T(corrected ? 'skew_corrected' : 'skew_transformed')}</p>` : ''}</div>`;
  } else {
    ctl = `<button type="button" class="btn ${strong ? 'btn-accent' : 'btn-outline'} btn-sm" data-act="straighten-on"${locked ? ' disabled' : ''}>${S('skew_do', fmtDeg(sk.deg))}</button>
      <p class="field-hint">${T(store.s.mode === 'darktable' ? 'skew_off_h' : 'skew_off_folder_h')}</p>`;
  }
  return `<div><h3>${T('skew')}</h3>${rows}${ctl}</div>`;
}

function renderSide() {
  const img = cur();
  const list = filmImages();
  const idx = list.findIndex((i) => String(i.id) === ed.id) + 1;
  el().querySelector('#ed-title').textContent = `${img.film} / ${img.filename}  (${idx}/${list.length})`;
  const locked = !isEditable();
  const grp = (g) => `<button type="button" data-group="${g}" aria-pressed="${img.group === g}"${locked ? ' disabled' : ''}>${T(g)}</button>`;
  const dec = (d, k) => `<button type="button" data-decision="${d}" aria-pressed="${(img.decision || '') === d}"${locked ? ' disabled' : ''}>${T(k)}</button>`;
  const reasons = (img.reasons || []).map((r) => `<li>${esc(r)}</li>`).join('');
  const prop = img.proposal;
  el().querySelector('#ed-side').innerHTML = `
    <div>
      <h3>${T('conf')}</h3>
      <dl class="kv"><dt>${T('conf')}</dt><dd>${img.confidence == null ? '–' : img.confidence.toFixed(3)}</dd>
      <dt>${T('method')}</dt><dd>${esc(img.method || '–')}</dd>
      <dt>${T('group')}</dt><dd>${T(img.group)}${img.group !== img.auto_group ? ' *' : ''}</dd></dl>
    </div>
    <div><h3>${T('group')}</h3>
      <div class="seg" role="group" data-key="group">${grp('green')}${grp('yellow')}${grp('red')}</div></div>
    <div><h3>${T('decision')}</h3>
      <div class="seg" role="group" data-key="decision">${dec('', 'keep_open')}${dec('accept', 'accept')}${dec('skip', 'skip')}</div></div>
    ${prop ? `<div class="callout callout-warn"><strong>${T('proposal')}</strong>
      ${prop.error ? esc(prop.error) : `${prop.confidence != null ? prop.confidence.toFixed(3) : ''} ${esc(prop.method || '')}`}
      <div class="chips" style="margin-top:.5rem">
        ${prop.crop ? `<button type="button" class="btn btn-accent btn-sm" data-act="prop-accept"${locked ? ' disabled' : ''}>${T('accept')}</button>` : ''}
        <button type="button" class="btn btn-outline btn-sm" data-act="prop-discard"${locked ? ' disabled' : ''}>${T('discard_proposals')}</button></div></div>` : ''}
    <div class="chips">
      <button type="button" class="btn btn-outline btn-sm" data-act="reset"${locked || !img.manual_crop ? ' disabled' : ''}>${T('reset_crop')} · R</button>
      <button type="button" class="btn btn-outline btn-sm" data-act="undo"${locked ? ' disabled' : ''}>${T('undo')} · Z</button>
    </div>
    ${refDevHtml(img)}
    ${skewHtml(img)}
    ${confPartsHtml(img)}
    ${reasons ? `<div><h3>${T('reasons')}</h3><ul class="reasons">${reasons}</ul></div>` : ''}
    <p class="keyhint">${T('k_move')}<br>${T('k_group')}<br>${T('k_accept')}</p>`;
  const film = store.s.film_aspects && store.s.film_aspects[img.film];
  el().querySelector('[data-ratio="film"]').disabled = !film;
}

// ── Crop-Darstellung ─────────────────────────────────────────────────────────

function setBox(node, c) {
  node.style.setProperty('--l', `${c[0] * 100}%`);
  node.style.setProperty('--t', `${c[1] * 100}%`);
  node.style.setProperty('--r', `${(1 - c[2]) * 100}%`);
  node.style.setProperty('--b', `${(1 - c[3]) * 100}%`);
}

// Gemessene Rahmenkanten als Linien ueber dem Bild: zeigen die Schraeglage; nach dem Geradestellen
// muessen sie waagerecht/senkrecht verlaufen.
let showLines = true;
function drawSkewLines() {
  const svg = el().querySelector('#ed-skewlines');
  if (!svg) return;
  const img = cur();
  const L = img.skew_lines;
  const btn = el().querySelector('[data-act="lines"]');
  if (btn) { btn.setAttribute('aria-pressed', String(showLines)); btn.disabled = !L; }
  const seg = (cls, a, b) => `<line class="${cls}" x1="${a[0]}" y1="${a[1]}" x2="${b[0]}" y2="${b[1]}" vector-effect="non-scaling-stroke"/>`;
  svg.innerHTML = showLines && L
    ? Object.values(L).map(([a, b]) => seg('case', a, b)).join('') + Object.values(L).map(([a, b]) => seg('core', a, b)).join('')
    : '';
}

// Schalter "Tilt anwenden": zeigt das Bild geradegestellt (Crop dann im geraden Bild setzen).
function syncTiltToggle() {
  const btn = el().querySelector('[data-act="tilt-toggle"]');
  if (!btn) return;
  const img = cur();
  const on = img.straighten != null;
  btn.setAttribute('aria-pressed', String(on));
  btn.disabled = !isEditable() || (!on && !(img.skew && img.skew.deg != null) && ed.lastDeg == null);
}

function toggleTilt() {
  const img = cur();
  if (!isEditable()) return;
  if (img.straighten != null) { ed.lastDeg = img.straighten; return patch({ straighten: null }); }
  const deg = ed.lastDeg != null ? ed.lastDeg : (img.skew && img.skew.deg);
  if (deg == null) return toast(T('skew_none'), 'error');
  return patch({ straighten: { deg } });
}

function syncCrop() {
  const img = cur();
  drawSkewLines();
  syncTiltToggle();
  if (!ed.drag) ed.crop = img.crop ? img.crop.slice() : [0.05, 0.05, 0.95, 0.95];
  drawCrop();
  const det = el().querySelector('#ed-det');
  const showDet = img.manual_crop && img.detected_crop;
  det.hidden = !showDet;
  if (showDet) {
    setBox(det, img.detected_crop);
    det.querySelector('.cand-tag').textContent = `${S('detected')} ${img.confidence != null ? img.confidence.toFixed(2) : ''}`;
  }
  const pr = el().querySelector('#ed-prop');
  const p = img.proposal && img.proposal.crop;
  pr.hidden = !p;
  if (p) {
    setBox(pr, p);
    const t = pr.querySelector('.cand-tag');
    t.textContent = `${S('proposal')} ${img.proposal.confidence != null ? img.proposal.confidence.toFixed(2) : ''}`;
    t.style.left = 'auto'; t.style.right = '14px';
  }
}

function drawCrop() {
  const img = cur();
  setBox(el().querySelector('#ed-crop'), ed.crop);
  const [l, t, r, b] = ed.crop;
  const ro = el().querySelector('#ed-readout');
  if (img.export_size) {
    const w = Math.round((r - l) * img.export_size[0]), h = Math.round((b - t) * img.export_size[1]);
    ro.textContent = `${S('crop_size', w, h)} · ${(w / Math.max(h, 1)).toFixed(3)}`;
  } else ro.textContent = '';
}

// ── Ziehen ───────────────────────────────────────────────────────────────────

function ratioValue() {
  const img = cur();
  if (ed.ratio === 'free') return null;
  const [l, t, r, b] = ed.crop;
  const W = img.export_size ? img.export_size[0] : 1, H = img.export_size ? img.export_size[1] : 1;
  const landscape = (r - l) * W >= (b - t) * H;
  if (ed.ratio === 'image') return W / H;
  const film = store.s.film_aspects && store.s.film_aspects[img.film];
  if (!film) return null;
  return landscape ? film : 1 / film;
}

// Wendet eine Ziehbewegung an; dx/dy sind Anteile des Bildrahmens.
export function dragCrop(mode, c0, dx, dy, rho, W, H) {
  let [l, t, r, b] = c0;
  if (mode === 'move') {
    const w = r - l, h = b - t;
    l = clamp(l + dx, 0, 1 - w); t = clamp(t + dy, 0, 1 - h);
    return [l, t, l + w, t + h];
  }
  if (mode.includes('w')) l = clamp(l + dx, 0, r - MIN);
  if (mode.includes('e')) r = clamp(r + dx, l + MIN, 1);
  if (mode.includes('n')) t = clamp(t + dy, 0, b - MIN);
  if (mode.includes('s')) b = clamp(b + dy, t + MIN, 1);
  if (rho) {
    // Seitenverhaeltnis in Pixeln: (r-l)*W / ((b-t)*H) = rho
    const hFromW = (w) => (w * W) / rho / H;
    const wFromH = (h) => (h * H * rho) / W;
    const horiz = mode.includes('w') || mode.includes('e');
    const vert = mode.includes('n') || mode.includes('s');
    if (horiz && vert) {
      let w = r - l, h = hFromW(w);
      if (mode.includes('n')) t = b - h; else b = t + h;
      if (t < 0) { t = 0; h = b - t; w = wFromH(h); mode.includes('w') ? (l = r - w) : (r = l + w); }
      if (b > 1) { b = 1; h = b - t; w = wFromH(h); mode.includes('w') ? (l = r - w) : (r = l + w); }
    } else if (horiz) {
      const h = hFromW(r - l), cy = (c0[1] + c0[3]) / 2;
      t = cy - h / 2; b = cy + h / 2;
      if (t < 0 || b > 1) { const hh = 2 * Math.min(cy, 1 - cy), w = wFromH(hh); if (mode.includes('w')) l = r - w; else r = l + w; t = cy - hh / 2; b = cy + hh / 2; }
    } else if (vert) {
      const w = wFromH(b - t), cx = (c0[0] + c0[2]) / 2;
      l = cx - w / 2; r = cx + w / 2;
      if (l < 0 || r > 1) { const ww = 2 * Math.min(cx, 1 - cx), h = hFromW(ww); if (mode.includes('n')) t = b - h; else b = t + h; l = cx - ww / 2; r = cx + ww / 2; }
    }
  }
  return [clamp(l), clamp(t), clamp(r), clamp(b)];
}

function onDown(e) {
  if (!ed || !isEditable()) return;
  const h = e.target.closest('.handle');
  const box = e.target.closest('.cropbox');
  if (!h && !box) return;
  e.preventDefault();
  e.currentTarget.setPointerCapture(e.pointerId);
  const rect = el().querySelector('#ed-frame').getBoundingClientRect();
  ed.drag = { mode: h ? h.dataset.h : 'move', x: e.clientX, y: e.clientY, c0: ed.crop.slice(), rect, moved: false };
}
function onMove(e) {
  if (!ed || !ed.drag) return;
  const d = ed.drag;
  const dx = (e.clientX - d.x) / d.rect.width, dy = (e.clientY - d.y) / d.rect.height;
  if (Math.abs(e.clientX - d.x) + Math.abs(e.clientY - d.y) > 1) d.moved = true;
  const img = cur();
  const W = img.export_size ? img.export_size[0] : 1, H = img.export_size ? img.export_size[1] : 1;
  ed.crop = dragCrop(d.mode, d.c0, dx, dy, ratioValue(), W, H);
  drawCrop();
}
function onUp(e) {
  if (!ed || !ed.drag) return;
  const moved = ed.drag.moved;
  ed.drag = null;
  if (moved) save();
}

// ── Speichern ────────────────────────────────────────────────────────────────

function save() {
  clearTimeout(ed.saveTimer);
  const id = ed.id, crop = ed.crop.map((v) => Math.round(v * 1e5) / 1e5);
  return guard(async () => {
    await api('PATCH', 'images', { ids: [id], crop });
    await hooks.refresh();
  }, hooks.refresh);
}
function saveSoon() { clearTimeout(ed.saveTimer); ed.saveTimer = setTimeout(save, 450); }
function flushSave() { if (ed && ed.saveTimer) { clearTimeout(ed.saveTimer); ed.saveTimer = null; save(); } }

async function patch(p) {
  await guard(() => api('PATCH', 'images', { ids: [ed.id], ...p }), hooks.refresh);
  await hooks.refresh();
}

// ── Ereignisse ───────────────────────────────────────────────────────────────

function go(delta) {
  const list = filmImages();
  const i = list.findIndex((x) => String(x.id) === ed.id) + delta;
  if (i < 0 || i >= list.length) return;
  flushSave();
  const id = String(list[i].id);
  ed.id = id; ed.cands = null; ed.ratio = 'free';
  el().querySelector('#ed-cands').innerHTML = '';
  el().querySelector('#ed-cand-chips').innerHTML = '';
  el().querySelectorAll('[data-ratio]').forEach((b) => b.setAttribute('aria-pressed', String(b.dataset.ratio === 'free')));
  const im = el().querySelector('#ed-img');
  im.src = thumbUrl(id, BIG);
  renderSide(); renderStrip(); syncCrop(); layout();
  prefetch();
}

async function loadCandidates() {
  const id = ed.id;
  const res = await guard(() => api('GET', `candidates/${id}`));
  if (!res || !ed || ed.id !== id) return;
  ed.cands = res.candidates || [];
  const overlay = el().querySelector('#ed-cands');
  overlay.innerHTML = '';
  const chips = el().querySelector('#ed-cand-chips');
  chips.innerHTML = ed.cands.length ? '' : `<span class="toolbar-label">${T('none_found')}</span>`;
  ed.cands.forEach((c, i) => {
    const col = CAND_COLORS[i % CAND_COLORS.length];
    const d = document.createElement('div');
    d.className = 'cand';
    d.style.setProperty('--c', `var(--${col})`);
    setBox(d, c.crop);
    d.innerHTML = `<span class="cand-tag">${String.fromCharCode(65 + i)} · ${c.confidence.toFixed(2)}</span>`;
    overlay.appendChild(d);
    const b = document.createElement('button');
    b.type = 'button'; b.className = 'chip'; b.dataset.cand = i;
    b.style.borderColor = `var(--${col})`;
    b.textContent = `${String.fromCharCode(65 + i)} ${c.method} ${c.confidence.toFixed(2)}`;
    chips.appendChild(b);
  });
}

function onClick(e) {
  const t = e.target;
  const act = t.closest('[data-act]');
  if (act) {
    const a = act.dataset.act;
    if (a === 'close') closeEditor();
    else if (a === 'prev') go(-1);
    else if (a === 'next') go(1);
    else if (a === 'cands') loadCandidates();
    else if (a === 'lines') { showLines = !showLines; drawSkewLines(); }
    else if (a === 'tilt-toggle') toggleTilt();
    else if (a === 'reset') patch({ crop: null });
    else if (a === 'straighten-on') patch({ straighten: { deg: cur().skew.deg } });
    else if (a === 'straighten-off') patch({ straighten: null });
    else if (a === 'undo') guard(() => api('POST', 'undo', { scope: 'selection', ids: [ed.id] })).then(hooks.refresh);
    else if (a === 'prop-accept') guard(() => api('POST', 'proposals/accept', { ids: [ed.id] })).then(hooks.refresh);
    else if (a === 'prop-discard') guard(() => api('POST', 'proposals/discard', { ids: [ed.id] })).then(hooks.refresh);
    return;
  }
  const r = t.closest('[data-ratio]');
  if (r) {
    ed.ratio = r.dataset.ratio;
    el().querySelectorAll('[data-ratio]').forEach((b) => b.setAttribute('aria-pressed', String(b === r)));
    return;
  }
  const g = t.closest('[data-group]');
  if (g) return patch({ group: g.dataset.group === cur().auto_group ? null : g.dataset.group });
  const d = t.closest('[data-decision]');
  if (d) return patch({ decision: d.dataset.decision || null });
  const c = t.closest('[data-cand]');
  if (c && ed.cands) return patch({ crop: ed.cands[+c.dataset.cand].crop });
  const s = t.closest('#ed-strip button[data-id]');
  if (s) {
    flushSave();
    ed.id = String(s.dataset.id);
    go(0);                               // baut Ansicht fuer das neu gewaehlte Bild auf
  }
}

function onKey(e) {
  if (!ed) return;
  if (document.querySelector('#dialog-host .scrim')) return;
  const tag = (e.target.tagName || '').toLowerCase();
  if (['input', 'select', 'textarea'].includes(tag)) return;
  const k = e.key;
  if (k === 'Escape') { e.preventDefault(); closeEditor(); return; }
  if (k === '[') { e.preventDefault(); go(-1); return; }
  if (k === ']') { e.preventDefault(); go(1); return; }
  if (!isEditable()) return;
  const lower = k.length === 1 ? k.toLowerCase() : k;
  if (lower === 'z' && !e.ctrlKey) { e.preventDefault(); guard(() => api('POST', 'undo', { scope: 'session' })).then(hooks.refresh); return; }
  if (lower === 't') { e.preventDefault(); toggleTilt(); return; }
  if (lower === 'r') { e.preventDefault(); patch({ crop: null }); return; }
  if (lower === '1' || lower === '2' || lower === '3') { e.preventDefault(); const g = ['green', 'yellow', 'red'][+lower - 1]; patch({ group: g === cur().auto_group ? null : g }); return; }
  if (lower === 'a') { e.preventDefault(); patch({ decision: 'accept' }); return; }
  if (lower === 's') { e.preventDefault(); patch({ decision: 'skip' }); return; }
  const arrows = { ArrowLeft: [-1, 0], ArrowRight: [1, 0], ArrowUp: [0, -1], ArrowDown: [0, 1] }[k];
  if (arrows) {
    e.preventDefault();
    const img = cur();
    const W = img.export_size ? img.export_size[0] : 1000, H = img.export_size ? img.export_size[1] : 1000;
    const step = e.shiftKey ? 10 : 1;
    ed.crop = dragCrop(e.altKey ? 'se' : 'move', ed.crop, arrows[0] * step / W, arrows[1] * step / H, e.altKey ? ratioValue() : null, W, H);
    drawCrop();
    saveSoon();
  }
}
