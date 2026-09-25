"""Lokaler HTTP-Server der Companion-UI (nur Stdlib).

- bindet standardmaessig nur an 127.0.0.1, prueft einen zufaelligen Token (bei jedem Start neu)
  und den Host-Header (Schutz gegen fremde Webseiten und DNS-Rebinding), kein CORS. Mit
  ``--bind`` (siehe ``companion/__main__.py``, nur eigenstaendige Nutzung) laesst sich der Server
  im Netzwerk erreichbar machen; dann bleibt allein der Token die Zugriffskontrolle, die enge
  Host-Pruefung entfaellt (siehe ``App.loopback_only``).
- liefert die statische Oberflaeche, eine JSON-API und Server-Sent-Events
- nach "Fertig" antworten alle Schreibzugriffe mit 409 (Sitzung gesperrt)
"""
import errno
import json
import mimetypes
import os
import queue
import re
import secrets
import signal
import socket
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
LOOPBACK_BINDS = ("127.0.0.1", "localhost", "::1")
WILDCARD_BINDS = ("0.0.0.0", "::")
DEFAULT_HTTP_PORT = 80        # Browser lassen diesen Port im Host-Header weg
PORT_SEARCH_SPAN = 20         # belegter Port: so viele Nachfolger probieren, dann das BS waehlen lassen
PORT_UNAVAILABLE = (errno.EADDRINUSE, errno.EACCES)   # belegt / Port < 1024 ohne Rechte
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
    (z. B. zweimal ``kader ORDNER`` gestartet, oder zweimal "Pruefung oeffnen").

    Gibt das offene Dateiobjekt zurueck (muss vom Aufrufer gehalten werden, solange der Server
    laeuft) oder ``None``, wenn schon ein anderer Prozess die Sperre haelt. Der Prozess gibt die
    Sperre beim Beenden automatisch frei (auch bei Absturz), ohne Aufraeum-Code noetig ist.
    Unter Windows ueber ``msvcrt.locking`` (ebenfalls prozessgebunden)."""
    try:
        import fcntl
    except ImportError:            # pragma: no cover - Windows
        return _acquire_lock_windows(session_dir)
    fh = open(os.path.join(session_dir, "server.lock"), "a")
    try:
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        fh.close()
        return None
    return fh


def _acquire_lock_windows(session_dir):  # pragma: no cover - nur Windows
    import msvcrt
    fh = open(os.path.join(session_dir, "server.lock"), "a+")
    try:
        fh.seek(0)
        msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
    except OSError:
        fh.close()
        return None
    return fh


_VIRTUAL_IFACE_PREFIXES = (          # Docker/Container/Bruecken/VPN - nachrangig, nicht ausgeschlossen
    "lo", "docker", "br-", "veth", "cni", "flannel", "virbr", "vmnet", "vboxnet",
    "tun", "tap", "wg", "zt", "utun", "awdl", "llw", "gif", "stf", "bridge", "ifb",
)
SIOCGIFADDR = 0x8915


def _iface_ipv4(name):
    """IPv4-Adresse einer Netzwerkschnittstelle per ``ioctl`` (nur Linux; anderswo/ohne
    zugewiesene Adresse liefert der Aufruf einen OSError, die Schnittstelle wird dann
    uebersprungen statt das Raten scheitern zu lassen). Ohne ``fcntl`` (Windows): None."""
    import struct
    try:
        import fcntl
    except ImportError:
        return None
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        packed = struct.pack("256s", name[:15].encode())
        return socket.inet_ntoa(fcntl.ioctl(s.fileno(), SIOCGIFADDR, packed)[20:24])
    except OSError:
        return None
    finally:
        s.close()


PHYSICAL_SCORE = 100      # eine physische Schnittstelle schlaegt jede virtuelle, egal welcher Adressbereich
PRIVATE_LAN_SCORE = 5
DOCKER_RANGE_PENALTY = 8
ROUTE_BONUS = 3


def _score_candidate(name, ip):
    """Hoeher = eher die richtige, von aussen erreichbare LAN-Adresse. Der Interface-Name
    entscheidet zuerst: eine physische Schnittstelle gewinnt immer gegen Docker/Bruecke/VPN, auch
    wenn das echte LAN selbst in 172.16.0.0/12 liegt. Innerhalb gleicher Art zaehlen privates LAN
    (192.168/16, 10/8) positiv und Dockers ueblicher Bereich (172.16-31) negativ. Virtuelle
    Interfaces bleiben Kandidaten: ohne echtes LAN ist "wahrscheinlich falsch" besser als nichts."""
    octets = [int(o) for o in ip.split(".")]
    virtual_name = name.lower().startswith(_VIRTUAL_IFACE_PREFIXES)
    docker_range = octets[0] == 172 and 16 <= octets[1] <= 31
    private_lan = (octets[0] == 192 and octets[1] == 168) or octets[0] == 10
    score = 0 if virtual_name else PHYSICAL_SCORE
    score += PRIVATE_LAN_SCORE if private_lan else 0
    score -= DOCKER_RANGE_PENALTY if docker_range else 0
    return score


def _route_ip():
    """Welche eigene Adresse das Betriebssystem fuer eine ausgehende Verbindung waehlen wuerde
    (verbindet ohne Daten zu senden). Funktioniert auch, wo ``_iface_ipv4`` nicht geht (kein
    Linux); ``None`` ganz ohne Route (z. B. offline und ohne jedes Interface)."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except OSError:
        return None
    finally:
        s.close()


def guess_lan_ip():
    """Beste Vermutung fuer eine im LAN erreichbare eigene Adresse, nur fuer die Anzeige bei
    ``--bind 0.0.0.0`` (das Lauschen selbst betrifft das nicht, das laeuft auf allen Interfaces).
    Bevorzugt eine physische Netzwerkschnittstelle gegenueber Docker-/Bruecken-/VPN-Interfaces.
    Die vom Betriebssystem fuer eine ausgehende Verbindung gewaehlte Adresse bekommt einen kleinen
    Bonus (sie spiegelt echtes Routing) - bewertet mit dem Namen der Schnittstelle, der sie gehoert,
    damit eine Route ueber docker0 nicht als "physisch" durchgeht. Ohne Aufzaehlung (kein Linux)
    bleibt sie der einzige Kandidat. ``None`` ganz ohne Kandidaten."""
    by_ip = {}                                   # ip -> Name der Schnittstelle
    try:
        for _, name in socket.if_nameindex():
            ip = _iface_ipv4(name)
            if ip and not ip.startswith("127.") and not ip.startswith("169.254."):
                by_ip.setdefault(ip, name)
    except (OSError, AttributeError):
        pass          # kein POSIX-if_nameindex (z. B. Windows) - route_ip bleibt als Kandidat
    route_ip = _route_ip()
    if route_ip and route_ip not in by_ip and not route_ip.startswith("127."):
        by_ip[route_ip] = ""                     # Schnittstelle unbekannt: neutral bewerten
    if not by_ip:
        return None
    return max(by_ip, key=lambda ip: _score_candidate(by_ip[ip], ip) + (ROUTE_BONUS if ip == route_ip else 0))


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
    def __init__(self, session, token=None, watch_pid=None, idle_seconds=IDLE_SECONDS, bind="127.0.0.1"):
        self.session = session
        self.token = token or secrets.token_urlsafe(16)      # neu bei jedem Start, siehe url()
        self.broker = Broker()
        self.analyzer = Analyzer(session, self.broker.publish)
        session.subscribe(self.broker.publish)
        self.last_activity = time.time()   # letzte Aktivitaet des Nutzers in der Web-UI
        self.watch_pid = watch_pid         # Elternprozess (darktable): Ende dort = Ende hier
        self.idle_seconds = idle_seconds
        self.stop_reason = None
        self.httpd = None
        self.port = None
        self.bind = bind
        # nur an 127.0.0.1/localhost gilt die enge Host-Pruefung (siehe Handler._host_ok);
        # bei jeder anderen Bind-Adresse ist der Token die einzige Zugriffskontrolle
        self.loopback_only = bind in LOOPBACK_BINDS
        self.display_host = (guess_lan_ip() or "127.0.0.1") if bind in WILDCARD_BINDS else bind

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
        host = f"[{self.display_host}]" if ":" in self.display_host else self.display_host
        return f"http://{host}:{self.port}/?t={self.token}"


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
        name, port = split_host(self.headers.get("Host") or "")
        if not name:
            return False
        if port is None:                  # Browser schicken Port 80 nicht mit
            port = DEFAULT_HTTP_PORT
        if port != self.app.port:
            return False
        if self.app.loopback_only:
            return name in ("127.0.0.1", "localhost", "::1")
        # im Netzwerk erreichbar (--bind): der Hostname variiert (mehrere Interfaces, 0.0.0.0),
        # eine feste Zuordnung waere bruechig. Nur der Port wird noch geprueft; der Token bleibt
        # die eigentliche Zugriffskontrolle (siehe App.loopback_only, README "Terminal-Statusanzeige").
        return True

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
        # zaehlen nicht als Aktivitaet des Nutzers - und abgewiesene Anfragen (fremder Host, kein
        # Token, statische Dateien ohne Token) auch nicht, sonst haelt ein Scanner im Netz den
        # Server bei --bind ewig am Leben.
        passive = path == "/api/events" or (self.command == "GET" and path == "/api/session")
        try:
            if not self._host_ok():
                return self._error(403, "Host nicht erlaubt")
            if path.startswith("/static/"):
                return self._static(path[len("/static/"):])
            if not self._token_ok(query):
                return self._error(403, "Token fehlt oder ist falsch")
            if not passive:
                self.app.last_activity = time.time()
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
                if a.progress.get("busy"):         # erst pruefen: sonst bliebe die Phase "analyzing"
                    raise Busy()
                s.retry(b.get("ids") or [])
                try:
                    a.start_analysis()
                except Busy:                        # Rennen mit einer gerade gestarteten Neu-Erkennung
                    s.mark_analysis_done()
                    raise
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


def split_host(value):
    """Host-Header -> (name, port oder None), auch fuer IPv6 in Klammern: "[::1]:8080"."""
    value = value.strip().lower()
    if value.startswith("["):
        end = value.find("]")
        if end < 0:
            return "", None
        name, rest = value[1:end], value[end + 1:]
        port = rest[1:] if rest.startswith(":") else ""
    else:
        name, _, port = value.partition(":")
    if not port:
        return name, None
    return (name, int(port)) if port.isdigit() else ("", None)


def _server_class(bind):
    """IPv4-Server, bei einer IPv6-Adresse (enthaelt ":") die IPv6-Variante."""
    if ":" not in bind:
        return ThreadingHTTPServer
    return type("ThreadingHTTPServerV6", (ThreadingHTTPServer,), {"address_family": socket.AF_INET6})


def _bind_free_port(server_class, bind, port, handler):
    """Lauscht auf ``port``; ist er nicht verfuegbar, auf dem naechsten freien.

    Beispiel ``--port 8080`` belegt: 8081, 8082, ... bis ``PORT_SEARCH_SPAN``, danach ein
    beliebiger freier Port (0). Andere Fehler (Adresse fremd, kein IPv6) gehen durch."""
    if port == 0:
        return server_class((bind, 0), handler)

    candidates = [p for p in range(port, port + PORT_SEARCH_SPAN) if p <= 65535] + [0]
    for candidate in candidates:
        try:
            return server_class((bind, candidate), handler)
        except OSError as e:
            if e.errno not in PORT_UNAVAILABLE:
                raise
    raise OSError(errno.EADDRINUSE, "kein freier Port")


def make_server(session, port=0, token=None, watch_pid=None, idle_seconds=IDLE_SECONDS, bind="127.0.0.1"):
    """``bind``: Adresse zum Lauschen. Standard ``127.0.0.1`` (nur diese Maschine, engste
    Host-Pruefung). Jede andere Adresse (eine LAN-IP oder ``0.0.0.0`` fuer alle Interfaces) macht
    den Server im Netzwerk erreichbar; dann ist allein der Token (in der URL, bei jedem Start neu
    ausgewuerfelt) die Zugriffskontrolle, siehe ``App.loopback_only``/``Handler._host_ok``."""
    app = App(session, token, watch_pid, idle_seconds, bind)
    handler = type("BoundHandler", (Handler,), {"app": app})
    httpd = _bind_free_port(_server_class(bind), bind, port, handler)
    httpd.daemon_threads = True
    app.httpd = httpd
    app.port = httpd.server_address[1]
    return app


def write_server_info(app):
    sess.atomic_write_json(app.session.path("server.json"), {
        "pid": os.getpid(), "port": app.port, "token": app.token,
        "url": app.url, "started": sess.now_iso()})


def serve(app, start_analysis=True, tui=False, window=False):
    """Startet Watcher-Threads und blockiert, bis der Server beendet wird.

    Automatisches Ende: Web-UI 30 Minuten ohne Aktivitaet, Elternprozess (darktable)
    beendet, SIGTERM/SIGINT (Stop-Knopf in darktable), "Server beenden" in der UI.

    ``tui=True``/``window=True`` (nur eigenstaendige Nutzung, siehe ``companion/tui.py`` und
    ``companion/window.py``): der HTTP-Server laeuft dann in einem Hintergrund-Thread, der
    Hauptthread gehoert der Anzeige."""
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
        if tui or window:
            server_thread = threading.Thread(target=app.httpd.serve_forever, daemon=True)
            server_thread.start()
            if tui:
                from . import tui as front
            else:
                from . import window as front
            try:
                front.run(app)
            finally:
                if app.stop_reason is None:
                    app.stop("quit")
                app.httpd.shutdown()
                server_thread.join(timeout=5)
        else:
            app.httpd.serve_forever()
    finally:
        try:
            os.remove(app.session.path("server.json"))
        except OSError:
            pass
        app.httpd.server_close()
