"""Externe Programme finden und starten: darktable, RawTherapee (Oberflaeche und -cli).

Unter Linux liegen sie im PATH. Die Windows-Installer von darktable und RawTherapee tragen sich
dort nicht ein, deshalb werden zusaetzlich die ueblichen Installationsorte durchsucht
(``C:\\Program Files\\darktable\\bin``, ``C:\\Program Files\\RawTherapee\\5.11``), unter macOS die
App-Pakete in ``/Applications``.

Aus einem PyInstaller-Paket (exe/AppImage) gestartete Programme duerfen dessen Bibliothekspfad
nicht erben (``LD_LIBRARY_PATH`` zeigt sonst auf die mitgelieferten Bibliotheken), und unter
Windows soll ein Konsolenprogramm wie ``darktable-cli.exe`` kein Konsolenfenster aufreissen:
``run_kwargs()`` liefert beides.
"""
import glob
import os
import re
import shutil
import subprocess
import sys

DARKTABLE = "darktable"
DARKTABLE_CLI = "darktable-cli"
RAWTHERAPEE = "rawtherapee"
RAWTHERAPEE_CLI = "rawtherapee-cli"

_APP = {DARKTABLE: "darktable", DARKTABLE_CLI: "darktable", RAWTHERAPEE: "RawTherapee",
        RAWTHERAPEE_CLI: "RawTherapee"}
CREATE_NO_WINDOW = 0x08000000


def find(name):
    """Pfad zum Programm ``name`` (siehe Konstanten oben) oder None."""
    exe = shutil.which(name)
    if exe:
        return exe
    for cand in candidates(name):
        if os.path.isfile(cand):
            return cand
    return None


def candidates(name, platform=None, env=None):
    """Uebliche Installationsorte ausserhalb des PATH, neueste Version zuerst."""
    platform = platform or sys.platform
    env = os.environ if env is None else env
    app = _APP[name]
    if platform == "win32":
        roots = [env.get(k) for k in ("ProgramFiles", "ProgramW6432", "ProgramFiles(x86)")]
        if env.get("LOCALAPPDATA"):
            roots.append(os.path.join(env["LOCALAPPDATA"], "Programs"))
        out = []
        for root in dict.fromkeys(r for r in roots if r):
            if app == "darktable":
                out.append(os.path.join(root, "darktable", "bin", name + ".exe"))
            else:
                # RawTherapee installiert je Version in einen eigenen Unterordner
                found = glob.glob(os.path.join(root, "RawTherapee*", name + ".exe"))
                found += glob.glob(os.path.join(root, "RawTherapee*", "*", name + ".exe"))
                out += sorted(found, key=_version_key, reverse=True)
        return out
    if platform == "darwin":
        return [f"/Applications/{app}.app/Contents/MacOS/{name}"]
    return [f"/usr/bin/{name}", f"/usr/local/bin/{name}", f"/opt/{name}/bin/{name}"]


FLATPAK = {DARKTABLE: "org.darktable.Darktable", RAWTHERAPEE: "com.rawtherapee.RawTherapee"}


def find_gui(name):
    """Aufruf (argv-Liste) fuer darktable/RawTherapee mit Oberflaeche, auch als Flatpak, oder None."""
    exe = find(name)
    if exe:
        return [exe]
    app = FLATPAK.get(name)
    if app and sys.platform.startswith("linux") and shutil.which("flatpak"):
        for base in (os.path.expanduser("~/.local/share/flatpak"), "/var/lib/flatpak"):
            if os.path.isdir(os.path.join(base, "app", app)):
                return ["flatpak", "run", app]
    return None


def _version_key(path):
    return [int(n) for n in re.findall(r"\d+", path)]


def clean_env(env=None):
    """Umgebung fuer Kindprozesse ohne die Bibliothekspfade des PyInstaller-Pakets."""
    env = dict(os.environ if env is None else env)
    if getattr(sys, "frozen", False):
        for var in ("LD_LIBRARY_PATH", "DYLD_LIBRARY_PATH"):
            orig = env.pop(var + "_ORIG", None)      # PyInstaller merkt sich den urspruenglichen Wert
            if orig is not None:
                env[var] = orig
            else:
                env.pop(var, None)
    return env


def run_kwargs(env=None):
    """Zusaetzliche Argumente fuer subprocess.run/Popen externer Programme."""
    kw = {"env": clean_env(env)}
    if sys.platform == "win32":
        kw["creationflags"] = CREATE_NO_WINDOW
    return kw


def launch(argv):
    """Startet ein Programm mit Oberflaeche losgeloest von Kader (endet nicht mit Kader)."""
    kw = {"env": clean_env(), "stdin": subprocess.DEVNULL, "stdout": subprocess.DEVNULL,
          "stderr": subprocess.DEVNULL}
    if sys.platform == "win32":
        kw["creationflags"] = 0x00000008 | 0x00000200       # DETACHED_PROCESS | NEW_PROCESS_GROUP
    else:
        kw["start_new_session"] = True
    subprocess.Popen(argv, **kw)   # noqa: S603
