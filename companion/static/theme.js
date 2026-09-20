// Theme vor dem ersten Paint setzen (dunkel ist Standard); eigene Datei wegen strenger CSP.
try {
  if (localStorage.getItem('acn-theme') === 'light') document.documentElement.setAttribute('data-theme', 'light');
} catch (e) { /* localStorage gesperrt: Standard bleibt */ }
