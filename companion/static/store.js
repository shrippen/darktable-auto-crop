// Gemeinsamer Zustand der Oberflaeche (ein Objekt, kein Framework).
const LS = (k, d) => { try { return localStorage.getItem(k) ?? d; } catch (e) { return d; } };
export const setLS = (k, v) => { try { localStorage.setItem(k, v); } catch (e) { /* privat/gesperrt */ } };

export const store = {
  s: null,                       // letzter /api/session-Stand
  analysis: { busy: false, stage: '', done: 0, total: 0 },
  result: null,
  offline: null,                 // null oder Grund des Serverendes (quit/idle/parent/signal/lost)
  selected: new Set(),           // ausgewaehlte Bild-IDs (als Strings)
  anchor: null,                  // Anker fuer Shift-Bereichsauswahl
  sort: LS('acn-sort', 'name'),
  view: LS('acn-view', 'columns'),
  size: LS('acn-size', 'm'),
};

export const byId = (id) => store.s && store.s.images.find((i) => String(i.id) === String(id));
export const isEditable = () => !!store.s && ['analyzing', 'reviewing'].includes(store.s.phase);
export const selectedIds = () => [...store.selected].filter((id) => byId(id));

const NAME = (a, b) => (a.film || '').localeCompare(b.film || '', undefined, { numeric: true })
  || (a.filename || '').localeCompare(b.filename || '', undefined, { numeric: true });
export function sorted(images) {
  const list = [...images];
  if (store.sort === 'conf_asc') list.sort((a, b) => (a.confidence ?? -1) - (b.confidence ?? -1) || NAME(a, b));
  else if (store.sort === 'conf_desc') list.sort((a, b) => (b.confidence ?? -1) - (a.confidence ?? -1) || NAME(a, b));
  else list.sort(NAME);
  return list;
}
export const nameSorted = (images) => [...images].sort(NAME);

// Reihenfolge wie in der Galerie: erst nach Gruppe (Gruen, Gelb, Rot), darin nach der
// gewaehlten Sortierung. Der Crop-Editor blaettert in genau dieser Reihenfolge.
export function galleryOrder(images) {
  const out = [];
  for (const g of ['green', 'yellow', 'red']) out.push(...sorted(images.filter((i) => i.group === g)));
  return out;
}
