"""darktables Konfigurationsordner: wo er liegt, ob darktable gerade laeuft, was schon importiert ist.

    Linux    $XDG_CONFIG_HOME/darktable  (meist ~/.config/darktable)
    Windows  %LOCALAPPDATA%\\darktable
    macOS    ~/.config/darktable

Nur lesend: die Bibliothek (``library.db``) wird schreibgeschuetzt geoeffnet; laeuft darktable,
haelt es ``library.db.lock`` mit seiner PID.
"""
import os
import re
import sqlite3
import sys


def config_dir(platform=None, env=None):
    platform = platform or sys.platform
    env = os.environ if env is None else env
    if platform == "win32":
        base = env.get("LOCALAPPDATA") or os.path.join(os.path.expanduser("~"), "AppData", "Local")
        return os.path.join(base, "darktable")
    base = env.get("XDG_CONFIG_HOME") if platform != "darwin" else None
    return os.path.join(base or os.path.join(os.path.expanduser("~"), ".config"), "darktable")


def is_running(cfg=None):
    """Laeuft ein darktable mit diesem Konfigurationsordner? (Sperrdatei mit lebender PID)"""
    from .server import pid_alive
    cfg = cfg or config_dir()
    for name in ("library.db.lock", "data.db.lock"):
        try:
            with open(os.path.join(cfg, name), encoding="ascii", errors="replace") as f:
                m = re.search(r"\d+", f.read())          # darktable schreibt "<pid>\0"
        except OSError:
            continue
        pid = int(m.group(0)) if m else 0
        if pid > 0 and pid_alive(pid):
            return True
    return False


def imported(paths, cfg=None):
    """Welche der Bilddateien ``paths`` stehen schon in darktables Bibliothek? (Menge der Pfade)

    darktable liest eine Sidecar-XMP nur beim Import. Fuer schon importierte Bilder gilt die
    Bibliothek; eine neu geschriebene XMP sieht darktable erst nach "beim Start nach
    aktualisierten XMP-Dateien suchen" oder einem erneuten Import. Unlesbare Bibliothek: leer."""
    db = os.path.join(cfg or config_dir(), "library.db")
    if not paths or not os.path.isfile(db):
        return set()
    wanted = {}
    for p in paths:
        wanted[_key(os.path.dirname(p), os.path.basename(p))] = p
    folders = sorted({os.path.dirname(p) for p in paths})
    found = set()
    try:
        con = sqlite3.connect(f"file:{_uri_path(db)}?mode=ro", uri=True, timeout=1)
        try:
            for chunk in range(0, len(folders), 200):
                part = folders[chunk:chunk + 200]
                variants = part + [f.replace("\\", "/") for f in part] + [f.replace("/", "\\") for f in part]
                q = ("SELECT f.folder, i.filename FROM images i JOIN film_rolls f ON i.film_id = f.id "
                     f"WHERE f.folder IN ({','.join('?' * len(variants))})")
                for folder, filename in con.execute(q, variants):
                    p = wanted.get(_key(folder, filename))
                    if p:
                        found.add(p)
        finally:
            con.close()
    except sqlite3.Error:
        return set()
    return found


def _key(folder, filename):
    k = os.path.join(folder, filename).replace("\\", "/")
    return k.lower() if sys.platform == "win32" else k


def _uri_path(path):
    from urllib.request import pathname2url
    return pathname2url(os.path.abspath(path))
