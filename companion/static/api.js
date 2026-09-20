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

export const thumbUrl = (id, w) => `/api/thumb/${id}?w=${w}&t=${encodeURIComponent(token)}`;

export function events(onEvent) {
  const es = new EventSource(`/api/events?t=${encodeURIComponent(token)}`);
  es.onmessage = (e) => { try { onEvent(JSON.parse(e.data)); } catch (x) { /* ignorieren */ } };
  return es;
}
