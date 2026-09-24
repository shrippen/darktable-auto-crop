"""Kommandozeile von Auto Crop Negative (Companion-UI).

Eigenstaendig, ohne darktable:
  auto-crop-negative ORDNER                     = open ORDNER
  auto-crop-negative open ORDNER [--target auto|json|copies|xmp|rawtherapee]
                            [--out DIR] [--converter auto|darktable|rawtherapee|rawpy]
                            [--films 33 34] [--new] [--no-browser]
  auto-crop-negative check [ORDNER]             Konverter, Ziele, Vorschlag fuer ORDNER

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
from .export import is_raw
from .server import make_server, serve
from .sources import folder_job, standalone_job

COMMANDS = ("open", "check", "serve", "cleanup")


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] not in COMMANDS and not argv[0].startswith("-"):
        argv.insert(0, "open")          # Kurzform: nur der Ordner

    ap = argparse.ArgumentParser(prog="auto-crop-negative", description=__doc__,
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
    if raws and not converter:
        print("Fehler: kein RAW-Konverter gefunden. Moeglich: darktable-cli, rawtherapee-cli "
              "oder 'pip install rawpy'.", file=sys.stderr)
        return 1
    out = os.path.abspath(args.out or os.path.join(folder, targets.DEFAULT_OUT_DIR))
    job.update(target=target, converter=converter, out=out)

    s = None if args.new else _latest_standalone(args.root, folder, target, paths)
    resumed = s is not None
    if s is None:
        s = sess.Session.create(job, args.root)

    films = len({i["film"] for i in job["images"]})
    print(f"Ordner:     {folder} ({len(paths)} Bilder, {films} Rolle(n), davon {len(raws)} RAW)")
    print(f"Ziel:       {target}" + (f" -> {out}" if targets.get(target).writes_out else ""))
    if raws:
        print(f"Konverter:  {converter}")
    print(f"Sitzung:    {s.state['session']}" + (" (fortgesetzt; --new fuer Neuanalyse)" if resumed else ""))
    return _run(s, args, not args.no_browser, None)


def _latest_standalone(root, folder, target, paths):
    """Juengste Sitzung fuer denselben Ordner, dasselbe Ziel und dieselben Bilder."""
    want = set(paths)
    for d in reversed(sess.list_sessions(root)):
        st = sess.read_json(os.path.join(d, "state.json"), {}) or {}
        if st.get("mode") != targets.MODE_STANDALONE or st.get("target") != target:
            continue
        if (st.get("source") or {}).get("folder") != folder:
            continue
        if {i["path"] for i in st.get("images", {}).values()} != want:
            continue
        return sess.Session(d)
    return None


def _run(s, args, open_browser, watch_pid):
    sess.cleanup_old(args.root)                  # 14-Tage-Regel, nebenbei
    app = make_server(s, args.port, watch_pid=watch_pid,
                      idle_seconds=args.idle_minutes * 60)
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
