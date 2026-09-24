"""Lokaler HTTP-Server der Companion-UI (nur Stdlib).

- bindet nur an 127.0.0.1, prueft einen zufaelligen Token und den Host-Header
  (Schutz gegen fremde Webseiten und DNS-Rebinding), kein CORS
- liefert die statische Oberflaeche, eine JSON-API und Server-Sent-Events
- nach "Fertig" antworten alle Schreibzugriffe mit 409 (Sitzung gesperrt)
"""
import json
import mimetypes
import os
import queue
import re
import secrets
import signal
import threading
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from . import session as sess
from .detect import Analyzer, Busy
from .thumbs import thumb_bytes

STATIC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
IDLE_SECONDS = 30 * 60        # ohne Aktivitaet in der Web-UI beendet sich der Server
IDLE_WARN_SECONDS = 5 * 60    # so lange vorher warnt die UI
MAX_BODY = 2 * 1024 * 1024


class Broker:
    """Verteilt Ereignisse an alle offenen SSE-Verbindungen."""

    def __init__(self):
        self.clients = []
        self.lock = threading.Lock()

    def subscribe(self):
        q = queue.Queue(maxsize=200)
        with self.lock:
            self.clients.append(q)
        return q

    def unsubscribe(self, q):
        with self.lock:
            if q in self.clients:
                self.clients.remove(q)

    def publish(self, event):
        with self.lock:
            for q in self.clients:
                try:
                    q.put_nowait(event)
                except queue.Full:
                    pass


def acquire_lock(session_dir):
    """Exklusive Dateisperre fuer eine Sitzung: verhindert zwei Server auf demselben Ordner
    (z. B. zweimal ``auto-crop-negative ORDNER`` gestartet, oder zweimal "Pruefung oeffnen").

    Gibt das offene Dateiobjekt zurueck (muss vom Aufrufer gehalten werden, solange der Server
    laeuft) oder ``None``, wenn schon ein anderer Prozess die Sperre haelt. Der Prozess gibt die
    Sperre beim Beenden automatisch frei (auch bei Absturz), ohne Aufraeum-Code noetig ist.
    Ohne ``fcntl`` (nicht-POSIX) wird nicht gesperrt (kein Fehler, nur kein Schutz)."""
    try:
        import fcntl
    except ImportError:            # pragma: no cover - kein POSIX (Windows)
        return open(os.devnull, "a")
    fh = open(os.path.join(session_dir, "server.lock"), "a")
    try:
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        fh.close()
        return None
    return fh


def pid_alive(pid):
    """Lebt der Prozess? (Linux/Unix: Signal 0)"""
    try:
        os.kill(int(pid), 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except (OSError, ValueError, TypeError):
        return False
    return True


class App:
    def __init__(self, session, token=None, watch_pid=None, idle_seconds=IDLE_SECONDS):
        self.session = session
        self.token = token or secrets.token_urlsafe(16)
        self.broker = Broker()
        self.analyzer = Analyzer(session, self.broker.publish)
        session.subscribe(self.broker.publish)
        self.last_activity = time.time()   # letzte Aktivitaet des Nutzers in der Web-UI
        self.watch_pid = watch_pid         # Elternprozess (darktable): Ende dort = Ende hier
        self.idle_seconds = idle_seconds
        self.stop_reason = None
        self.httpd = None
        self.port = None

    def stop(self, reason):
        """Faehrt den Server geordnet herunter; die UI erfaehrt vorher den Grund."""
        if self.stop_reason:
            return
        self.stop_reason = reason
        self.broker.publish({"type": "bye", "reason": reason})

        def later():
            time.sleep(0.4)            # den Ereignisstrom noch ausliefern lassen
            self.httpd.shutdown()

        threading.Thread(target=later, daemon=True).start()

    @property
    def url(self):
        return f"http://127.0.0.1:{self.port}/?t={self.token}"


class Handler(BaseHTTPRequestHandler):
    server_version = "AutoCropCompanion/1"
    protocol_version = "HTTP/1.1"

    app: App = None     # wird in make_server gesetzt

    # -- Hilfen ----------------------------------------------------------------

    def log_message(self, fmt, *args):    # ruhig; Fehler landen im Analyzer-Log
        return

    def _send(self, status, body=b"", ctype="application/json; charset=utf-8", extra=None):
        if isinstance(body, (dict, list)):
            body = json.dumps(body, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _error(self, status, message, **extra):
        self._send(status, {"error": message, **extra})

    def _host_ok(self):
        host = (self.headers.get("Host") or "").lower()
        return host in (f"127.0.0.1:{self.app.port}", f"localhost:{self.app.port}")

    def _token_ok(self, query):
        tok = self.headers.get("X-Token") or (query.get("t") or [""])[0]
        return secrets.compare_digest(tok, self.app.token)

    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        if n > MAX_BODY:
            raise sess.SessionError("Anfrage zu gross", 413)
        if n == 0:
            return {}
        try:
            data = json.loads(self.rfile.read(n).decode("utf-8"))
        except ValueError:
            raise sess.SessionError("ungueltiges JSON")
        if not isinstance(data, dict):
            raise sess.SessionError("JSON-Objekt erwartet")
        return data

    # -- Dispatch ----------------------------------------------------------------

    def do_GET(self):
        self._dispatch()

    def do_HEAD(self):
        self._dispatch()

    def do_POST(self):
        self._dispatch()

    def do_PATCH(self):
        self._dispatch()

    def _dispatch(self):
        u = urlparse(self.path)
        query = parse_qs(u.query)
        path = u.path
        # Vom Server ausgeloeste Abrufe (Ereignisstrom, Neuladen nach Serverereignissen)
        # zaehlen nicht als Aktivitaet des Nutzers.
        passive = path == "/api/events" or (self.command == "GET" and path == "/api/session")
        if not passive:
            self.app.last_activity = time.time()
        try:
            if not self._host_ok():
                return self._error(403, "Host nicht erlaubt")
            if path.startswith("/static/"):
                return self._static(path[len("/static/"):])
            if not self._token_ok(query):
                return self._error(403, "Token fehlt oder ist falsch")
            if path == "/":
                return self._static("index.html")
            if path.startswith("/api/"):
                return self._api(path[5:], query)
            return self._error(404, "nicht gefunden")
        except sess.SessionError as e:
            return self._error(e.status, str(e), **e.extra)
        except (BrokenPipeError, ConnectionResetError):
            return None
        except Exception as e:                # noqa: BLE001
            self.app.analyzer.report(f"FEHLER: {self.command} {path}: {e!r}")
            return self._error(500, "interner Fehler")

    def _static(self, rel):
        full = os.path.normpath(os.path.join(STATIC, rel))
        if not full.startswith(STATIC + os.sep) or not os.path.isfile(full):
            return self._error(404, "nicht gefunden")
        ctype = mimetypes.guess_type(full)[0] or "application/octet-stream"
        if full.endswith((".js", ".mjs")):
            ctype = "text/javascript"
        with open(full, "rb") as f:
            data = f.read()
        extra = {}
        if rel == "index.html":
            extra["Content-Security-Policy"] = (
                "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; "
                "connect-src 'self'; frame-ancestors 'none'")
        self._send(200, data, ctype + ("; charset=utf-8" if ctype.startswith("text/") else ""),
                   extra)

    # -- API ---------------------------------------------------------------------

    def _api(self, route, query):
        s, a = self.app.session, self.app.analyzer
        m = self.command

        if route == "session" and m == "GET":
            s.refresh_from_disk()
            state = s.public_state()
            state["analysis"] = dict(a.progress)
            try:
                from .detect import _acn
                state["formats"] = list(_acn().ASPECT_RATIOS)
            except Exception:       # noqa: BLE001 - ohne cv2 bleibt die Liste leer
                state["formats"] = []
            return self._send(200, state)
        if route == "events" and m == "GET":
            return self._events()
        if route == "log" and m == "GET":
            return self._send(200, {"lines": list(a.log)[-300:]})
        if route == "result" and m == "GET":
            return self._send(200, sess.read_json(s.path("result.json"), {}) or {})
        if route == "summary" and m == "GET":
            entries, warnings = s.build_plan()
            return self._send(200, {"summary": s.public_state()["summary"],
                                    "warnings": warnings,
                                    "changed": sum(1 for e in entries if e["changed"])})
        mt = re.fullmatch(r"thumb/([^/]+)", route)
        if mt and m in ("GET", "HEAD"):
            img = s.image(mt.group(1))
            src = s.export_path(img)
            if not src or not os.path.exists(src):
                return self._error(404, "kein Export")
            data = thumb_bytes(src, s.path("thumbs"), str(img["id"]),
                               (query.get("w") or ["320"])[0], s.straight_deg(img))
            return self._send(200, data, "image/jpeg",
                              {"Cache-Control": "private, max-age=600"})
        mc = re.fullmatch(r"candidates/([^/]+)", route)
        if mc and m == "GET":
            if s.straight_deg(s.image(mc.group(1))):     # Kandidaten gelten nur im Originalrahmen
                return self._send(200, {"candidates": []})
            return self._send(200, {"candidates": a.candidates(mc.group(1))})

        # ---- Schreibzugriffe ----
        if m == "PATCH" and route == "images":
            b = self._body()
            patch = {k: b[k] for k in ("crop", "group", "decision", "straighten") if k in b}
            if not patch:
                raise sess.SessionError("nichts zu aendern")
            s.patch_images(b.get("ids") or [], patch)
            return self._send(200, {"ok": True})
        if m == "POST":
            b = self._body()
            if route == "undo":
                return self._send(200, {"undone": s.undo(b.get("scope", "session"),
                                                          b.get("ids"))})
            if route == "settings":
                s.set_settings(b)
                return self._send(200, {"ok": True})
            if route == "target":
                s.set_target(b.get("name"))
                return self._send(200, {"ok": True})
            if route == "redetect":
                s._require_editable()
                a.start_redetect(b.get("ids") or [], b.get("settings") or {})
                return self._send(202, {"started": True})
            if route == "roll-size":
                return self._send(200, {"changed": s.apply_roll_size(b.get("id"))})
            if route == "proposals/accept":
                s.accept_proposals(b.get("ids") or [])
                return self._send(200, {"ok": True})
            if route == "proposals/discard":
                s.discard_proposals(b.get("ids") or [])
                return self._send(200, {"ok": True})
            if route == "retry":
                s._require_editable()
                for iid in b.get("ids") or []:
                    img = s.image(iid)
                    img["status"], img["error"] = "pending", None
                s.state["phase"] = "analyzing"
                s.save()
                a.start_analysis()
                return self._send(202, {"started": True})
            if route == "finish":
                if a.progress.get("busy"):
                    raise Busy()
                return self._send(200, s.finish())
            if route == "reopen":
                s.reopen()
                return self._send(200, {"ok": True, "revision": s.revision})
            if route == "cleanup":
                removed = [d for d in sess.cleanup_old(os.path.dirname(s.dir))
                           if os.path.abspath(d) != os.path.abspath(s.dir)]
                return self._send(200, {"removed": len(removed)})
            if route == "ping":            # Lebenszeichen der UI (Maus/Tastatur)
                return self._send(200, {"ok": True})
            if route == "quit":
                self._send(200, {"ok": True})
                self.app.stop("quit")
                return None
        return self._error(404, "unbekannte Route")

    def _events(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "keep-alive")
        self.end_headers()
        q = self.app.broker.subscribe()
        try:
            self.wfile.write(b"retry: 2000\n\n")
            self.wfile.flush()
            while True:
                try:
                    ev = q.get(timeout=15)
                    payload = json.dumps(ev, ensure_ascii=False)
                    self.wfile.write(f"data: {payload}\n\n".encode("utf-8"))
                except queue.Empty:
                    self.wfile.write(b": keepalive\n\n")
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass
        finally:
            self.app.broker.unsubscribe(q)


def make_server(session, port=0, token=None, watch_pid=None, idle_seconds=IDLE_SECONDS):
    app = App(session, token, watch_pid, idle_seconds)
    handler = type("BoundHandler", (Handler,), {"app": app})
    httpd = ThreadingHTTPServer(("127.0.0.1", port), handler)
    httpd.daemon_threads = True
    app.httpd = httpd
    app.port = httpd.server_address[1]
    return app


def write_server_info(app):
    sess.atomic_write_json(app.session.path("server.json"), {
        "pid": os.getpid(), "port": app.port, "token": app.token,
        "url": app.url, "started": sess.now_iso()})


def serve(app, start_analysis=True):
    """Startet Watcher-Threads und blockiert, bis der Server beendet wird.

    Automatisches Ende: Web-UI 30 Minuten ohne Aktivitaet, Elternprozess (darktable)
    beendet, SIGTERM/SIGINT (Stop-Knopf in darktable), "Server beenden" in der UI."""
    write_server_info(app)
    try:
        for sig in (signal.SIGTERM, signal.SIGINT):
            signal.signal(sig, lambda *_: app.stop("signal"))
    except ValueError:            # nicht im Hauptthread (Tests)
        pass

    def watcher():
        last_warn = 0.0
        while not app.stop_reason:
            time.sleep(1.0)
            try:
                app.session.refresh_from_disk()
            except Exception:       # noqa: BLE001
                pass
            if app.watch_pid and not pid_alive(app.watch_pid):
                app.stop("parent")
                return
            if app.analyzer.progress["busy"]:
                continue              # eine laufende Analyse ist Aktivitaet
            idle = time.time() - app.last_activity
            left = app.idle_seconds - idle
            if left <= 0:
                app.stop("idle")
                return
            if left <= min(IDLE_WARN_SECONDS, app.idle_seconds / 2) and time.time() - last_warn >= 30:
                last_warn = time.time()
                app.broker.publish({"type": "idle_warning", "seconds": int(left)})

    threading.Thread(target=watcher, daemon=True).start()
    if start_analysis and app.session.phase == "analyzing":
        app.analyzer.start_analysis()
    try:
        app.httpd.serve_forever()
    finally:
        try:
            os.remove(app.session.path("server.json"))
        except OSError:
            pass
        app.httpd.server_close()
