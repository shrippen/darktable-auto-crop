"""Kommandozeile von Kader (Companion-UI).

Eigenstaendig, ohne darktable:
  kader ORDNER                     = open ORDNER
  kader open ORDNER [--target auto|json|copies|xmp|rawtherapee]
                            [--out DIR] [--converter auto|darktable|rawtherapee|rawpy]
                            [--films 33 34] [--new] [--no-browser] [--tui] [--bind ADRESSE]
  kader check [ORDNER]             Konverter, Ziele, Vorschlag fuer ORDNER

  --tui zeigt statt der einmaligen URL-Ausgabe eine laufende Statusanzeige im Terminal
  (Fortschritt, Zähler, URL, Taste O öffnet den Browser, Q beendet den Server) - gedacht für
  SSH/NAS-Sitzungen ohne lokalen Browser. Braucht das Paket "rich": pip install "kader[tui]".

  --bind ADRESSE macht den Server im Netzwerk erreichbar (Standard 127.0.0.1: nur diese Maschine).
  Beispiel: --bind 0.0.0.0 auf einem NAS, dann die angezeigte URL/den Token von einem anderen
  Geraet im selben Netz öffnen. Der Token wird bei jedem Start neu ausgewürfelt und ist dann die
  einzige Zugriffskontrolle (siehe README, Abschnitt "Terminal-Statusanzeige").

  Beide Optionen nur für diesen eigenständigen Weg; über darktable/serve bleibt es wie bisher
  (nur 127.0.0.1, kein Terminal-UI).

darktable und Kalibrierung:
  python -m companion serve --job job.json       neue Sitzung aus einem Job (Lua)
  python -m companion serve --session DIR        bestehende Sitzung fortsetzen
  python -m companion serve --folder Testphotos  Kalibrierung (reviews.json)
                            [--results review_data/results.json]
                            [--reviews review_data/reviews.json]
  python -m companion cleanup [--days 14]        alte Sitzungen loeschen
"""
import argparse
import json
import os
import sys
import webbrowser

from . import converters
from . import session as sess
from . import targets
from . import tui
from .export import is_raw
from .server import acquire_lock, make_server, serve
from .sources import folder_job, standalone_job

COMMANDS = ("open", "check", "serve", "cleanup")


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] not in COMMANDS and not argv[0].startswith("-"):
        argv.insert(0, "open")          # Kurzform: nur der Ordner

    ap = argparse.ArgumentParser(prog="kader", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    op = sub.add_parser("open", help="Ordner analysieren und in der Web-UI pruefen (ohne darktable)")
    op.add_argument("folder")
    op.add_argument("--target", default=targets.AUTO, choices=[targets.AUTO, *targets.standalone_names()],
                    help="was Fertig schreibt (Standard: aus dem Ordnerinhalt)")
    op.add_argument("--out", help=f"Ausgabeordner fuer json/copies (Standard: ORDNER/{targets.DEFAULT_OUT_DIR})")
    op.add_argument("--converter", default=converters.AUTO, choices=[converters.AUTO, *converters.NAMES],
                    help="RAW-Konverter (Standard: nach vorhandenen Sidecars, sonst der erste verfuegbare)")
    op.add_argument("--films", nargs="+", help="nur diese Rollen (Unterordner, z. B. 33 34)")
    op.add_argument("--new", action="store_true", help="neu analysieren statt die letzte Sitzung fortzusetzen")
    op.add_argument("--no-browser", action="store_true", help="Browser nicht oeffnen")
    op.add_argument("--tui", action="store_true",
                    help="Statusanzeige im Terminal statt nur der URL (braucht 'rich'; fuer SSH/NAS)")
    op.add_argument("--bind", default="127.0.0.1",
                    help="Lauschadresse; 0.0.0.0 oder eine LAN-IP macht den Server im Netzwerk "
                         "erreichbar (Standard: nur diese Maschine, siehe Sicherheitshinweis in der README)")
    _server_args(op)

    cp = sub.add_parser("check", help="verfuegbare Konverter und Ziele anzeigen")
    cp.add_argument("folder", nargs="?")

    sp = sub.add_parser("serve")
    g = sp.add_mutually_exclusive_group(required=True)
    g.add_argument("--job")
    g.add_argument("--session")
    g.add_argument("--folder")
    g.add_argument("--latest-folder", action="store_true",
                   help="neueste Ordner-Sitzung fortsetzen (keine Neuanalyse)")
    sp.add_argument("--films", nargs="+", help="Ordnermodus: nur diese Rollen (z. B. 33 34)")
    sp.add_argument("--results")
    sp.add_argument("--reviews", nargs="+", help="Referenzdateien; die erste wird von \"Fertig\" geschrieben")
    sp.add_argument("--open", action="store_true", help="Browser oeffnen")
    sp.add_argument("--watch-pid", type=int, default=None,
                    help="beenden, sobald dieser Prozess (darktable) nicht mehr laeuft")
    _server_args(sp)

    clp = sub.add_parser("cleanup")
    clp.add_argument("--days", type=int, default=sess.CACHE_DAYS)
    clp.add_argument("--root", default=sess.DEFAULT_ROOT)
    args = ap.parse_args(argv)

    if args.cmd == "cleanup":
        removed = sess.cleanup_old(args.root, args.days)
        print(f"{len(removed)} Sitzung(en) geloescht")
        return 0
    if args.cmd == "check":
        return _check(args.folder)
    if args.cmd == "open":
        return _open(args)
    return _serve(args)


def _server_args(p):
    p.add_argument("--root", default=sess.DEFAULT_ROOT)
    p.add_argument("--port", type=int, default=0)
    p.add_argument("--idle-minutes", type=float, default=30.0,
                   help="beenden nach so vielen Minuten ohne Aktivitaet in der Web-UI")


def _serve(args):
    if args.latest_folder:
        folders = [d for d in sess.list_sessions(args.root)
                   if (sess.read_json(os.path.join(d, "state.json"), {}) or {}).get("mode") == "folder"]
        if not folders:
            print("keine Ordner-Sitzung gefunden; erst ohne --latest-folder starten", file=sys.stderr)
            return 1
        s = sess.Session(folders[-1])
    elif args.session:
        s = sess.Session(os.path.abspath(args.session))
    elif args.job:
        with open(args.job, encoding="utf-8") as f:
            job = json.load(f)
        s = sess.Session.create(job, args.root)
    else:
        job = folder_job(args.folder, args.results, args.reviews, films=args.films)
        s = sess.Session.create(job, args.root)
    return _run(s, args, args.open, args.watch_pid)


def _open(args):
    """Eigenstaendig: Ordner -> Sitzung (neu oder fortgesetzt) -> Web-UI."""
    if args.tui:
        reason = tui.unavailable_reason()
        if reason:
            print(f"Fehler: --tui geht hier nicht: {reason}.", file=sys.stderr)
            return 1
    try:
        job = standalone_job(args.folder, films=args.films)
    except sess.SessionError as e:
        print(f"Fehler: {e}", file=sys.stderr)
        return 1
    folder = job["folder"]
    paths = [i["path"] for i in job["images"]]
    target = targets.suggest(paths) if args.target == targets.AUTO else args.target
    raws = [p for p in paths if is_raw(p)]
    converter = converters.resolve(args.converter, raws)
    if raws:
        avail = converters.available()
        if not converter or not avail.get(converter):
            bad = converter or args.converter
            print(f"Fehler: RAW-Konverter '{bad}' nicht verfuegbar. Moeglich: darktable-cli, "
                  "rawtherapee-cli oder 'pip install rawpy'.", file=sys.stderr)
            return 1
    out = os.path.abspath(args.out or os.path.join(folder, targets.DEFAULT_OUT_DIR))
    job.update(target=target, converter=converter, out=out)

    s = None if args.new else _find_standalone(args.root, folder, target, job["images"])
    resumed, added, lock = s is not None, 0, None
    if resumed:
        # Erst sperren, dann ergaenzen: laeuft schon ein Server auf dieser Sitzung, darf dieser
        # Prozess state.json nicht anfassen - der laufende Server kennt die neuen Bilder nicht
        # und wuerde sie beim naechsten Speichern wieder ueberschreiben.
        lock = acquire_lock(s.dir)
        if lock is None:
            return _report_running(s, not args.no_browser)
        added = s.add_images(job["images"])
    else:
        s = sess.Session.create(job, args.root)

    films = len({i["film"] for i in job["images"]})
    print(f"Ordner:     {folder} ({len(paths)} Bilder, {films} Rolle(n), davon {len(raws)} RAW)")
    print(f"Ziel:       {target}" + (f" -> {out}" if targets.get(target).writes_out else ""))
    print("            (in der Web-UI jederzeit aenderbar)")
    if raws:
        print(f"Konverter:  {converter}")
    if resumed:
        extra = f", davon {added} neu" if added else ""
        print(f"Sitzung:    {s.state['session']} (fortgesetzt{extra}; --new fuer Neuanalyse)")
    else:
        print(f"Sitzung:    {s.state['session']}")
    return _run(s, args, not args.no_browser, None, use_tui=args.tui, bind=args.bind, lock=lock)


def _find_standalone(root, folder, target, items):
    """Juengste Sitzung fuer denselben Ordner und dasselbe Ziel, deren Bilder eine Teilmenge der
    jetzt gefundenen sind (sonst: fehlen ihr Bilder, die es jetzt nicht mehr gibt, passt sie nicht
    mehr -> neue Sitzung). Aendert nichts; neue Bilder ergaenzt der Aufrufer erst unter der
    Sitzungssperre (``Session.add_images``). Rueckgabe: Session oder None."""
    have = {i["path"] for i in items}
    for d in reversed(sess.list_sessions(root)):
        st = sess.read_json(os.path.join(d, "state.json"), {}) or {}
        if st.get("mode") != targets.MODE_STANDALONE or st.get("target") != target:
            continue
        if (st.get("source") or {}).get("folder") != folder:
            continue
        known = {i["path"] for i in st.get("images", {}).values()}
        if not known or not known.issubset(have):
            continue
        return sess.Session(d)
    return None


def _report_running(s, open_browser):
    """Ein anderer Prozess bedient die Sitzung schon: dessen URL nennen statt mitzubedienen."""
    info = sess.read_json(os.path.join(s.dir, "server.json"))
    url = info and info.get("url")
    if url:
        print(f"Sitzung wird bereits bedient: {url}", flush=True)
        if open_browser:
            webbrowser.open(url)
    else:
        print("Sitzung wird bereits von einem anderen Prozess bedient.", file=sys.stderr)
    return 0


def _run(s, args, open_browser, watch_pid, use_tui=False, bind="127.0.0.1", lock=None):
    lock = lock or acquire_lock(s.dir)           # gehalten, solange dieser Prozess laeuft
    if lock is None:
        return _report_running(s, open_browser)
    sess.cleanup_old(args.root)                  # 14-Tage-Regel, nebenbei
    try:
        app = make_server(s, args.port, watch_pid=watch_pid,
                          idle_seconds=args.idle_minutes * 60, bind=bind)
    except OSError as e:                         # Adresse fremd, Port belegt, kein IPv6 ...
        print(f"Fehler: Server kann nicht auf {bind}:{args.port or 'auto'} lauschen: {e}", file=sys.stderr)
        return 1
    if args.port and app.port != args.port:
        print(f"Hinweis: Port {args.port} belegt, nutze {app.port}.", file=sys.stderr)
    if not app.loopback_only:
        # Erscheint bei --tui zusaetzlich dauerhaft im Statuspanel (der Alt-Screen verdeckt sonst
        # jede Ausgabe von hier); ohne --tui bleibt es einfach im Terminal stehen.
        print(f"Achtung: im Netzwerk erreichbar ({bind}). Nur der Token in der URL schuetzt "
              "den Zugriff - nicht in unsicheren/fremden Netzen freigeben.")
    if use_tui:
        serve(app, tui=True)                     # eigene Anzeige; URL/Browser regelt sie selbst
        return 0
    print(app.url, flush=True)
    if open_browser:
        webbrowser.open(app.url)
    serve(app)
    return 0


def _check(folder):
    avail = converters.available()
    print("RAW-Konverter:")
    for name in converters.NAMES:
        print(f"  {name:12} {'ja' if avail[name] else 'nein'}")
    print("Ziele: " + ", ".join(targets.standalone_names()) + " (darktable: Lua-Plugin)")
    tui_reason = tui.unavailable_reason()
    print("Terminal-Statusanzeige (--tui): " + ("ja" if not tui_reason else f"nein ({tui_reason})"))
    if not folder:
        return 0
    try:
        job = standalone_job(folder)
    except sess.SessionError as e:
        print(f"Fehler: {e}", file=sys.stderr)
        return 1
    paths = [i["path"] for i in job["images"]]
    raws = [p for p in paths if is_raw(p)]
    print(f"{job['folder']}: {len(paths)} Bilder, davon {len(raws)} RAW")
    print(f"  Vorschlag Ziel:      {targets.suggest(paths)}")
    if raws:
        print(f"  Vorschlag Konverter: {converters.resolve(converters.AUTO, raws) or 'keiner verfuegbar'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
