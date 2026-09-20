// Kleiner API-Client. Token kommt aus der URL (?t=...), wird bei jeder Anfrage mitgeschickt.
export const token = new URLSearchParams(location.search).get('t') || '';

export class ApiError extends Error {
  constructor(status, data) {
    super(data && data.error ? data.error : `HTTP ${status}`);
    this.status = status;
    this.data = data || {};
  }
}

export async function api(method, path, body) {
  const res = await fetch('/api/' + path, {
    method,
    headers: { 'X-Token': token, 'Content-Type': 'application/json' },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  let data = null;
  try { data = await res.json(); } catch (e) { /* leere Antwort */ }
  if (!res.ok) throw new ApiError(res.status, data);
  return data;
}

// Der Winkel steht nur im Link, damit der Browser-Cache nach dem Geradestellen nicht das alte Bild liefert;
// gedreht wird serverseitig nach dem Sitzungszustand.
let degOf = () => 0;
export const setDegLookup = (fn) => { degOf = fn; };
export const thumbUrl = (id, w) => `/api/thumb/${id}?w=${w}&s=${degOf(id) || 0}&t=${encodeURIComponent(token)}`;

export function events(onEvent) {
  const es = new EventSource(`/api/events?t=${encodeURIComponent(token)}`);
  es.onmessage = (e) => { try { onEvent(JSON.parse(e.data)); } catch (x) { /* ignorieren */ } };
  return es;
}
