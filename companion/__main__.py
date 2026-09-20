"""Kommandozeile der Companion-UI.

  python -m companion serve --job job.json       neue Sitzung aus einem Job (Lua)
  python -m companion serve --session DIR        bestehende Sitzung fortsetzen
  python -m companion serve --folder Testphotos  Ordnermodus ohne darktable
                            [--results review_data/results.json]
                            [--reviews review_data/reviews.json]
  python -m companion cleanup [--days 14]        alte Sitzungen loeschen
"""
import argparse
import json
import os
import sys
import webbrowser

from . import session as sess
from .server import make_server, serve
from .sources import folder_job


def main(argv=None):
    ap = argparse.ArgumentParser(prog="companion", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
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
    sp.add_argument("--root", default=sess.DEFAULT_ROOT)
    sp.add_argument("--port", type=int, default=0)
    sp.add_argument("--open", action="store_true", help="Browser oeffnen")
    sp.add_argument("--watch-pid", type=int, default=None,
                    help="beenden, sobald dieser Prozess (darktable) nicht mehr laeuft")
    sp.add_argument("--idle-minutes", type=float, default=30.0,
                    help="beenden nach so vielen Minuten ohne Aktivitaet in der Web-UI")
    cp = sub.add_parser("cleanup")
    cp.add_argument("--days", type=int, default=sess.CACHE_DAYS)
    cp.add_argument("--root", default=sess.DEFAULT_ROOT)
    args = ap.parse_args(argv)

    if args.cmd == "cleanup":
        removed = sess.cleanup_old(args.root, args.days)
        print(f"{len(removed)} Sitzung(en) geloescht")
        return 0

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
    sess.cleanup_old(args.root)                  # 14-Tage-Regel, nebenbei
    app = make_server(s, args.port, watch_pid=args.watch_pid,
                      idle_seconds=args.idle_minutes * 60)
    print(app.url, flush=True)
    if args.open:
        webbrowser.open(app.url)
    serve(app)
    return 0


if __name__ == "__main__":
    sys.exit(main())
