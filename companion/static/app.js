// Companion-UI: Hauptmodul (Kopf, Phasen, Werkzeugleiste, Einstellungen, Fertig/Zurueck).
import { api, events, setDegLookup } from './api.js';
import { T, S, esc } from './i18n.js';
import { store, setLS, byId, isEditable, selectedIds } from './store.js';
import { guard, toast, dialog } from './ui.js';
import { initGallery, renderGallery, setSelection } from './gallery.js';
import { initEditor, openEditor, refreshEditor, isOpen } from './editor.js';

const $ = (id) => document.getElementById(id);
const PILL = { analyzing: 'analyzing', reviewing: 'reviewing', locked: 'locked', applied: 'applied', apply_failed: 'failed' };
const standalone = () => !!store.s && store.s.mode === 'standalone';
// Ziel als Elementpaar, bei Ausgabeordner mit Pfad
const targetHtml = (s) => T('tgt_' + s.target.name) + (s.target.out ? ` <code>${esc(s.target.out)}</code>` : '');
let settingsBuilt = false;
let refreshTimer = null;
let logLines = [];

// ── Laden ────────────────────────────────────────────────────────────────────

export async function refresh() {
  const s = await guard(() => api('GET', 'session'));
  if (!s) return;
  store.s = s;
  store.analysis = { ...store.analysis, ...s.analysis };
  for (const id of [...store.selected]) if (!byId(id)) store.selected.delete(id);
  if (['applied', 'apply_failed'].includes(s.phase) && !store.result) {
    store.result = await guard(() => api('GET', 'result'));
  }
  if (!['applied', 'apply_failed'].includes(s.phase)) store.result = null;
  renderAll();
}
const refreshSoon = () => { clearTimeout(refreshTimer); refreshTimer = setTimeout(refresh, 150); };

// ── Rendern ──────────────────────────────────────────────────────────────────

function renderAll() {
  const s = store.s;
  const editable = isEditable();
  document.body.classList.toggle('is-locked', !editable);
  renderHeader(); renderFlow(); renderSummary(); renderNotice(); renderProgress();
  renderToolbar(); renderSettings(); renderGallery(); renderActionbar();
  $('keyhint').innerHTML = `${T('keys')}: <kbd>1</kbd><kbd>2</kbd><kbd>3</kbd> ${T('band_green')}/${T('band_yellow')}/${T('band_red')} · <kbd>A</kbd> ${T('accept')} · <kbd>S</kbd> ${T('skip')} · <kbd>E</kbd> ${T('k_edit').replace(/^E: /, '')} · <kbd>Z</kbd> ${T('undo')}`;
  if (isOpen()) refreshEditor();
  void s;
}

function renderHeader() {
  const s = store.s;
  $('phase-pill').innerHTML = `<span class="pill" data-state="${PILL[s.phase]}">${T(s.phase)}${s.revision > 1 ? ` · r${s.revision}` : ''}</span>`;
  $('session-label').textContent = `${s.session} · ${s.target.name}`;
  $('session-label').title = `${s.session} · ${S('target')}: ${S('tgt_' + s.target.name)}${s.target.out ? ' ' + s.target.out : ''}`;
}

function renderFlow() {
  const p = store.s.phase;
  const folder = store.s.mode === 'folder';
  const step = (key, hl, done) => {
    const k = folder && (key === 'done' || key === 'apply') ? key + '_folder' : key;
    const desc = key === 'apply' && standalone() ? T('tgt_' + store.s.target.name) : T('step_' + k + '_d');
    return `<div class="flow-node${hl ? ' hl' : ''}${done ? ' is-done' : ''}"><b>${T('step_' + k)}</b><span>${desc}</span></div>`;
  };
  const arrow = '<span class="flow-arrow"></span>';
  const analyzed = p !== 'analyzing';
  $('flow').innerHTML = `<div class="flow">${step('analyze', p === 'analyzing', analyzed)}${arrow}${step('review', p === 'reviewing', ['locked', 'applied', 'apply_failed'].includes(p))}${arrow}${step('done', p === 'locked', p === 'applied')}${arrow}${step('apply', p === 'applied' || p === 'apply_failed', p === 'applied')}</div>`;
}

function renderSummary() {
  const n = store.s.summary;
  const fact = (k, v, label) => `<div class="fact" data-k="${k}"><b>${v}</b><span>${T(label)}</span></div>`;
  $('summary').innerHTML = fact('total', n.total, 'images') + fact('green', n.green, 'green') + fact('yellow', n.yellow, 'yellow') + fact('red', n.red, 'red') + fact('apply', n.apply, 'to_apply');
}

function renderNotice() {
  const s = store.s, host = $('notice');
  if (store.offline) {
    const std = standalone() ? '_std' : '';
    const key = { idle: 'server_stopped_idle' + std, parent: 'server_stopped_parent' }[store.offline] || 'server_stopped' + std;
    host.innerHTML = `<div class="callout callout-danger notice"><div><strong>${T(key)}</strong></div></div>`;
    return;
  }
  const reopen = `<button type="button" class="btn btn-outline btn-sm" data-act="reopen">${T('reopen')}</button>`;
  if (s.phase === 'locked') {
    const key = { folder: 'locked_msg_folder', standalone: 'locked_msg_standalone' }[s.mode] || 'locked_msg';
    host.innerHTML = `<div class="callout callout-ok notice"><div><strong>${T(key)}</strong></div>${reopen}</div>`;
  } else if (s.phase === 'applied' || s.phase === 'apply_failed') {
    const res = store.result || {};
    const imgs = Object.values(res.images || {});
    const cnt = (k) => imgs.filter((i) => i.status === k).length;
    // Fehler zuerst, dann Hinweise (etwa "Winkel nicht uebertragen")
    const msgs = Object.entries(res.images || {}).filter(([, v]) => v.message && (v.status === 'error' || standalone()))
      .sort(([, a], [, b]) => (b.status === 'error') - (a.status === 'error'));
    const ok = s.phase === 'applied';
    const sfx = { folder: '_folder', standalone: '_standalone' }[s.mode] || '';
    const title = ok ? 'applied_msg' + sfx : (standalone() ? 'failed_msg_standalone' : 'failed_msg');
    host.innerHTML = `<div class="callout ${ok ? 'callout-ok' : 'callout-danger'} notice"><div><strong>${T(title)}</strong>
      ${standalone() ? `<div class="notice-list">${T('target')}: ${targetHtml(s)}</div>` : ''}
      ${res.message ? `<div class="notice-list">${esc(res.message)}</div>` : ''}
      ${imgs.length ? `<div class="notice-list">${T('result_ok')}: ${cnt('ok')} · ${T('result_skipped')}: ${cnt('skipped')} · ${T('result_error')}: ${cnt('error')}</div>` : ''}
      ${msgs.length ? `<ul class="notice-list">${msgs.slice(0, 8).map(([id, v]) => `<li>${esc((byId(id) || {}).filename || id)}: ${esc(v.message)}</li>`).join('')}</ul>` : ''}
      </div>${reopen}</div>`;
  } else if (s.mode === 'folder') {
    const ref = s.summary.ref;
    const pct = ref.n ? Math.round((100 * ref.hits) / ref.n) : 0;
    const groups = ['green', 'yellow', 'red'].filter((g) => ref.by_group[g].n)
      .map((g) => T('ref_group', S('band_' + g), ref.by_group[g].hits, ref.by_group[g].n)).join(' · ');
    host.innerHTML = `<div class="callout notice"><div><strong>${T('ref_test')}</strong>
      ${ref.n ? `<div class="notice-list">${T('ref_hits', ref.hits, ref.n, pct)}${groups ? ' · ' + groups : ''}</div>` : ''}
      <div class="field-hint" style="margin-top:.4rem">${T('ref_hint')}</div></div></div>`;
  } else host.innerHTML = '';
}

function renderProgress() {
  const a = store.analysis, host = $('progress');
  if (!a.busy) { host.innerHTML = ''; return; }
  const pct = a.total ? Math.min(100, Math.round((a.done / a.total) * 100)) : 0;
  const label = a.stage === 'export' ? 'progress_export' : 'progress_detect';
  host.innerHTML = `<div class="progress"><div class="progress-head"><span>${T(label)}</span><span>${a.done} / ${a.total}</span></div><div class="progress-bar" role="progressbar" aria-valuenow="${a.done}" aria-valuemax="${a.total}"><i style="--p:${pct}%"></i></div></div>`;
}

function seg(items, current, attr) {
  return `<div class="seg" role="group">${items.map(([v, label]) => `<button type="button" ${attr}="${v}" aria-pressed="${current === v}">${label}</button>`).join('')}</div>`;
}

function renderToolbar() {
  const s = store.s, editable = isEditable();
  const dis = editable ? '' : ' disabled';
  const sel = selectedIds();
  const proposals = s.images.filter((i) => i.proposal && i.proposal.crop).length;
  const sortOpts = [['name', 'sort_name'], ['conf_asc', 'sort_conf_asc'], ['conf_desc', 'sort_conf_desc'],
    ['skew_desc', 'sort_skew_desc'],
    ...(s.mode === 'folder' ? [['ref_desc', 'sort_ref_desc']] : [])]
    .map(([v, k]) => `<option value="${v}"${store.sort === v ? ' selected' : ''}>${esc(S(k))}</option>`).join('');
  $('toolbar').innerHTML = `<div class="toolbar">
    <div class="group">
      <label class="toolbar-label" for="tb-sort">${T('sort')}</label><select class="select" id="tb-sort" style="width:auto">${sortOpts}</select>
      <span class="toolbar-sep"></span>
      ${seg([['columns', '▥'], ['rows', '☰']], store.view, 'data-view')}
      ${seg([['s', 'S'], ['m', 'M'], ['l', 'L']], store.size, 'data-size')}
    </div>
    <div class="group">
      <span class="selcount" id="selcount">${sel.length ? T('selected_n', sel.length) : T('none_selected')}</span>
      <button type="button" class="btn btn-outline btn-sm" data-act="sel-all">${T('all')}</button>
      <button type="button" class="btn btn-outline btn-sm" data-act="sel-none">${T('none')}</button>
      <button type="button" class="btn btn-outline btn-sm" data-act="accept-yellow"${dis}>${T('accept_all_yellow')}</button>
      ${proposals ? `<button type="button" class="btn btn-accent btn-sm" data-act="accept-proposals"${dis}>${T('accept_proposals', proposals)}</button>
        <button type="button" class="btn btn-outline btn-sm" data-act="discard-proposals"${dis}>${T('discard_proposals')}</button>` : ''}
    </div></div>`;
}

const FORMATS_FALLBACK = ['35mm', '6x6', '6x7', '6x9', '4x5'];
function renderSettings() {
  const s = store.s, st = s.settings;
  $('settings-summary').innerHTML = T('settings_title');
  if (!settingsBuilt) {
    settingsBuilt = true;
    const fmts = (s.formats && s.formats.length ? s.formats : FORMATS_FALLBACK);
    $('settings-body').innerHTML = `
      <div class="panel-grid" style="display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:1rem 1.4rem">
        <div class="field range" id="rg-g"><label for="st-green">${T('threshold_green')}</label><div class="range-row"><input id="st-green" type="range" min="0" max="100"><output class="range-out" id="st-green-out"></output></div></div>
        <div class="field range" id="rg-y"><label for="st-yellow">${T('threshold_yellow')}</label><div class="range-row"><input id="st-yellow" type="range" min="0" max="100"><output class="range-out" id="st-yellow-out"></output></div></div>
        <div class="field" style="grid-column:1/-1"><span class="field-hint" id="st-hint"></span></div>
        <div class="field"><span class="field-label">${T('format')}</span><div class="seg" role="group" id="st-format">${fmts.map((f) => `<button type="button" data-format="${esc(f)}" aria-pressed="false">${esc(f)}</button>`).join('')}</div></div>
        <div class="field"><label for="st-aspect">${T('aspect')}</label><input class="input" id="st-aspect" type="number" step="0.01" min="0.2" max="5" inputmode="decimal"><span class="field-hint">${T('aspect_h')}</span></div>
        <div class="field"><label for="st-fbl">${T('film_border')}</label><input class="input" id="st-fbl" type="number" min="0" max="255" inputmode="numeric"><span class="field-hint">${T('film_border_h')}</span></div>
        <div class="field"><span class="field-label">&nbsp;</span>
          <button type="button" class="switch" role="switch" aria-checked="false" id="st-nopen"><span class="switch-track"></span>${T('no_penalty')}</button>
          <button type="button" class="switch" role="switch" aria-checked="false" id="st-skip"><span class="switch-track"></span>${T('skip_refine')}</button></div>
      </div>
      <div class="settings-actions"><button type="button" class="btn btn-accent btn-sm" data-act="redetect" id="st-redetect"></button>
        <span class="field-hint">${T('redetect_hint')}</span></div>
      <div class="settings-actions"><button type="button" class="btn btn-outline btn-sm" data-act="cleanup">${T('cleanup')}</button>
        <button type="button" class="btn btn-outline btn-sm" data-act="quit">${T('quit')}</button></div>`;
  }
  const g = $('st-green'), y = $('st-yellow');
  if (document.activeElement !== g) g.value = Math.round(st.t_green * 100);
  if (document.activeElement !== y) y.value = Math.round(st.t_yellow * 100);
  updateZones();
  $('st-format').querySelectorAll('button').forEach((b) => b.setAttribute('aria-pressed', String(b.dataset.format === st.format)));
  const sel = selectedIds();
  const btn = $('st-redetect');
  btn.innerHTML = T('redetect_n', sel.length);
  const editable = isEditable();
  btn.disabled = !editable || !sel.length || store.analysis.busy;
  $('settings-body').querySelectorAll('input,button.switch,#st-format button,[data-act="cleanup"]').forEach((n) => { n.disabled = !editable && !n.matches('[data-act="cleanup"]'); });
}

function updateZones() {
  const g = +$('st-green').value, y = Math.min(+$('st-yellow').value, g);
  $('st-green-out').textContent = (g / 100).toFixed(2);
  $('st-yellow-out').textContent = (y / 100).toFixed(2);
  const zones = `linear-gradient(90deg,var(--red) 0 ${y}%,var(--yellow) ${y}% ${g}%,var(--aqua) ${g}% 100%)`;
  $('rg-g').style.setProperty('--zones', zones);
  $('rg-y').style.setProperty('--zones', zones);
  $('st-hint').innerHTML = T('thr_hint', (g / 100).toFixed(2), (y / 100).toFixed(2));
}

function renderActionbar() {
  const s = store.s, editable = isEditable();
  const n = s.summary;
  const canFinish = s.phase === 'reviewing' && !store.analysis.busy && n.pending === 0;
  const right = editable
    ? `<button type="button" class="btn btn-accent" data-act="finish"${canFinish ? '' : ' disabled'}>${T(s.mode === 'folder' ? 'finish_folder' : 'finish')}</button>`
    : `<button type="button" class="btn btn-outline" data-act="reopen">${T('reopen')}</button>`;
  $('actionbar').innerHTML = `<div class="actionbar-inner"><div class="group">
      <button type="button" class="btn btn-outline btn-sm" data-act="undo"${editable && s.can_undo ? '' : ' disabled'}>${T('undo')} · Z</button>
      <button type="button" class="btn btn-outline btn-sm" data-act="undo-sel"${editable && selectedIds().length ? '' : ' disabled'}>${T('undo_sel')}</button>
      <button type="button" class="btn btn-outline btn-sm" data-act="straighten-sel" title="${esc(S('straighten_sel_h'))}"${editable && selectedIds().length ? '' : ' disabled'}>${T('straighten_sel')}</button>
      <span class="selcount">${T('to_apply')}: ${n.apply} · ${T('band_red')}: ${n.red}</span></div>
      <div class="group">${right}</div></div>`;
}

// ── Aktionen ─────────────────────────────────────────────────────────────────

async function finish() {
  const sum = await guard(() => api('GET', 'summary'));
  if (!sum) return;
  const notes = [];
  if (sum.changed && store.s.applied_revision) notes.push(esc(S(standalone() ? 'finish_changed_standalone' : 'finish_changed', sum.changed)));
  if (sum.warnings.length) notes.push(esc(S('finish_stale', sum.warnings.length)));
  const folder = store.s.mode === 'folder';
  const imgs = store.s.images;
  const facts = folder
    ? [{ value: imgs.filter((i) => i.manual_crop).length, label: 'facts_corrected' },
       { value: imgs.filter((i) => !i.manual_crop && i.decision === 'accept').length, label: 'facts_accepted' },
       { value: sum.summary.red, label: 'facts_flagged', color: 'red' }]
    : [{ value: sum.summary.apply, label: 'apply_n' }, { value: sum.summary.red, label: 'flagged_red', color: 'red' },
       { value: sum.summary.skipped, label: 'skipped', color: 'fg3' }];
  if (folder) notes.push(T('finish_folder_note'));
  if (standalone()) notes.push(`${T('target')}: ${targetHtml(store.s)}`);
  const body = folder ? 'finish_body_folder' : standalone() ? 'finish_body_standalone' : 'finish_body';
  const ok = await dialog({
    title: folder ? 'finish_title_folder' : 'finish_title', body,
    confirm: folder ? 'finish_folder' : 'finish', cancel: 'back', notes, facts,
  });
  if (!ok) return;
  const res = await guard(() => api('POST', 'finish', {}), refresh);
  if (res) toast(T('saved'), 'ok');
  setSelection([]);
  await refresh();
}

async function reopen() {
  const ok = await dialog({ title: 'reopen_title', body: store.s.mode === 'folder' ? 'reopen_body_folder' : 'reopen_body',
    confirm: 'reopen', cancel: 'cancel' });
  if (!ok) return;
  await guard(() => api('POST', 'reopen', {}), refresh);
  await refresh();
}

async function redetect() {
  const ids = selectedIds();
  if (!ids.length) return;
  const settings = { format: store.s.settings.format };
  const asp = $('st-aspect').value, fbl = $('st-fbl').value;
  if (asp) settings.aspect_ratio = +asp;
  if (fbl !== '') settings.film_border_level = +fbl;
  if ($('st-nopen').getAttribute('aria-checked') === 'true') settings.no_aspect_penalty = true;
  if ($('st-skip').getAttribute('aria-checked') === 'true') settings.skip_refine = true;
  const r = await guard(() => api('POST', 'redetect', { ids, settings }), refresh);
  if (r) toast(T('started'), 'ok');
}

let thrTimer = null;
function saveThresholds() {
  clearTimeout(thrTimer);
  thrTimer = setTimeout(async () => {
    await guard(() => api('POST', 'settings', { t_green: +$('st-green').value / 100, t_yellow: Math.min(+$('st-yellow').value, +$('st-green').value) / 100 }), refresh);
    await refresh();
  }, 250);
}

async function onAction(act) {
  const s = store.s;
  if (act === 'finish') return finish();
  if (act === 'reopen') return reopen();
  if (act === 'redetect') return redetect();
  if (act === 'sel-all') return setSelection(s.images.map((i) => i.id));
  if (act === 'sel-none') return setSelection([]);
  if (act === 'accept-yellow') {
    const ids = s.images.filter((i) => i.group === 'yellow').map((i) => i.id);
    if (ids.length) await guard(() => api('PATCH', 'images', { ids, decision: 'accept' }), refresh);
  } else if (act === 'accept-proposals') {
    await guard(() => api('POST', 'proposals/accept', { ids: s.images.filter((i) => i.proposal && i.proposal.crop).map((i) => i.id) }), refresh);
    toast(T('proposals_accepted'), 'ok');
  } else if (act === 'discard-proposals') {
    await guard(() => api('POST', 'proposals/discard', { ids: s.images.filter((i) => i.proposal).map((i) => i.id) }), refresh);
  } else if (act === 'undo') await guard(() => api('POST', 'undo', { scope: 'session' }), refresh);
  else if (act === 'straighten-sel') {
    await guard(() => api('PATCH', 'images', { ids: selectedIds(), straighten: { deg: 'auto' } }), refresh);
    toast(T('straighten_done'), 'ok');
  } else if (act === 'undo-sel') await guard(() => api('POST', 'undo', { scope: 'selection', ids: selectedIds() }), refresh);
  else if (act === 'cleanup') { const r = await guard(() => api('POST', 'cleanup', {})); if (r) toast(`${r.removed}`, 'ok'); return; }
  else if (act === 'quit') { await guard(() => api('POST', 'quit', {})); return; }
  await refresh();
}

// ── Start ────────────────────────────────────────────────────────────────────

setDegLookup((id) => (byId(id) || {}).straighten || 0);

function wire() {
  document.addEventListener('click', async (e) => {
    const a = e.target.closest('[data-act]');
    if (a && !a.closest('#editor') && !a.disabled) return onAction(a.dataset.act);
    const v = e.target.closest('[data-view]');
    if (v) { store.view = v.dataset.view; setLS('acn-view', store.view); return renderAll(); }
    const z = e.target.closest('[data-size]');
    if (z) { store.size = z.dataset.size; setLS('acn-size', store.size); return renderAll(); }
    const f = e.target.closest('#st-format [data-format]');
    if (f && isEditable()) { await guard(() => api('POST', 'settings', { format: f.dataset.format }), refresh); return refresh(); }
    const sw = e.target.closest('.switch');
    if (sw && !sw.disabled) return sw.setAttribute('aria-checked', String(sw.getAttribute('aria-checked') !== 'true'));
    const th = e.target.closest('[data-theme-set]');
    if (th) {
      const light = th.dataset.themeSet === 'light';
      document.documentElement.toggleAttribute('data-theme', light);
      if (light) document.documentElement.setAttribute('data-theme', 'light');
      setLS('acn-theme', light ? 'light' : 'dark');
      markTheme();
    }
  });
  document.addEventListener('change', (e) => {
    if (e.target.id === 'tb-sort') { store.sort = e.target.value; setLS('acn-sort', store.sort); renderAll(); }
  });
  document.addEventListener('input', (e) => {
    if (e.target.id === 'st-green' || e.target.id === 'st-yellow') { updateZones(); saveThresholds(); }
  });
  $('log-summary').innerHTML = T('log');
  $('logbox').addEventListener('toggle', loadLog);
}

function markTheme() {
  const light = document.documentElement.getAttribute('data-theme') === 'light';
  document.querySelectorAll('[data-theme-set]').forEach((b) => b.setAttribute('aria-pressed', String((b.dataset.themeSet === 'light') === light)));
}

async function loadLog() {
  if (!$('logbox').open) return;
  const r = await guard(() => api('GET', 'log'));
  if (r) { logLines = r.lines; paintLog(); }
}
function paintLog() {
  const box = $('log');
  box.textContent = logLines.length ? logLines.slice(-300).join('\n') : S('log_empty');
  box.scrollTop = box.scrollHeight;
}

async function main() {
  wire(); markTheme();
  initGallery({ refresh, openEditor: (id) => openEditor(id), onSelection: () => { renderToolbar(); renderSettings(); renderActionbar(); } });
  initEditor({ refresh, onClose: () => renderAll() });
  await refresh();
  activityPings();
  const es = events((ev) => {
    if (ev.type === 'bye') { store.offline = ev.reason || 'quit'; renderNotice(); return; }
    if (ev.type === 'idle_warning') { toast(T('idle_warning', Math.max(1, Math.ceil(ev.seconds / 60)))); return; }
    if (ev.type === 'state') refreshSoon();
    else if (ev.type === 'progress') { store.analysis = { ...store.analysis, ...ev }; renderProgress(); renderSettings(); renderActionbar(); }
    else if (ev.type === 'log') { logLines.push(ev.line); if ($('logbox').open) paintLog(); }
  });
  es.onopen = () => { if (store.offline === 'lost') { store.offline = null; refresh(); } };
  es.onerror = () => {               // Server weg (ohne "bye"): nach kurzer Wartezeit melden
    setTimeout(() => { if (es.readyState !== 1 && !store.offline) { store.offline = 'lost'; if (store.s) renderNotice(); } }, 3000);
  };
}

// Lebenszeichen fuer den Leerlauf-Timer des Servers: Maus/Tastatur, hoechstens einmal pro Minute.
function activityPings() {
  let last = 0;
  const ping = () => {
    const now = Date.now();
    if (now - last < 60000 || store.offline) return;
    last = now;
    api('POST', 'ping', {}).catch(() => {});
  };
  ['pointerdown', 'pointermove', 'keydown', 'wheel', 'touchstart'].forEach((ev) => document.addEventListener(ev, ping, { passive: true }));
}
main();
