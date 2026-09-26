"""darktable-Plugin aus der Kader-App installieren, ohne Terminal und ohne Python.

    <darktable-Konfiguration>/lua/contrib/kader/kader.lua       das Plugin (aus der App kopiert)
    <darktable-Konfiguration>/lua/contrib/kader/kader_command   Pfad der App (exe/AppImage)
    darktablerc   lua/script_manager/contrib/kader=TRUE         im Script Manager eingeschaltet
    luarc         require-Zeile, nur wirksam ohne Script Manager (aeltere darktable-Versionen; mit
                  Script Manager laedt der das Plugin, und Ausschalten dort wirkt)

Das Plugin startet den Companion-Server dann ueber die App (``<app> serve --job ...``) statt ueber
eine Python-Installation. Wird die App verschoben, traegt jeder Start der App den neuen Ort ein
(``refresh``). darktable darf beim Installieren nicht laufen: es schreibt darktablerc beim Beenden
mit seinem Stand zurueck.
"""
import os
import shutil
import sys

from . import dtconfig

PLUGIN = ("contrib", "kader")
PREF_KEY = "lua/script_manager/contrib/kader"
OLD_PREF_KEY = "lua/script_manager/contrib/auto_crop_negative"     # Plugin vor der Umbenennung
LUARC_MARK = "-- Kader-Plugin (von der Kader-App eingetragen)"
LUARC_LINE = 'if not package.searchpath("tools/script_manager", package.path) then require "contrib/kader/kader" end'
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class PluginError(Exception):
    pass


def plugin_dir(cfg=None):
    return os.path.join(cfg or dtconfig.config_dir(), "lua", *PLUGIN)


def bundled_lua():
    """kader.lua der App (PyInstaller) oder des Repositorys, sonst None."""
    for base in (getattr(sys, "_MEIPASS", None), ROOT):
        if base and os.path.isfile(os.path.join(base, "kader.lua")):
            return os.path.join(base, "kader.lua")
    return None


def app_command():
    """Womit das Plugin Kader startet: das AppImage bzw. die exe; ohne Paket None."""
    if not getattr(sys, "frozen", False):
        return None
    return os.environ.get("APPIMAGE") or sys.executable


def darktable_present(cfg=None):
    from . import programs
    return os.path.isdir(cfg or dtconfig.config_dir()) or bool(programs.find(programs.DARKTABLE))


def status(cfg=None):
    d = plugin_dir(cfg)
    cmd_file = os.path.join(d, "kader_command")
    command = _read(cmd_file).strip() if os.path.isfile(cmd_file) else None
    return {"darktable": darktable_present(cfg), "installed": os.path.isfile(os.path.join(d, "kader.lua")),
            "command": command, "dir": d}


def install(command=None, cfg=None, lua=None):
    """Installiert das Plugin; liefert eine Liste von Hinweisen fuer den Nutzer."""
    cfg = cfg or dtconfig.config_dir()
    command = command or app_command()
    lua = lua or bundled_lua()
    if not command:
        raise PluginError("Nur aus der Kader-App (exe/AppImage) moeglich; mit Python: ./install.sh")
    if not lua:
        raise PluginError("kader.lua fehlt in diesem Paket")
    if dtconfig.is_running(cfg):
        raise PluginError("darktable läuft – bitte darktable schließen und noch einmal versuchen.")
    d = plugin_dir(cfg)
    os.makedirs(d, exist_ok=True)
    shutil.copyfile(lua, os.path.join(d, "kader.lua"))
    _write(os.path.join(d, "kader_command"), command + "\n")
    notes = []
    rc = os.path.join(cfg, "darktablerc")
    prefs = _read(rc) if os.path.isfile(rc) else ""
    prefs = set_pref(prefs, PREF_KEY, "TRUE")
    if get_pref(prefs, OLD_PREF_KEY) == "TRUE":
        prefs = set_pref(prefs, OLD_PREF_KEY, "FALSE")
        notes.append("Das alte Plugin „auto_crop_negative“ wurde im Script Manager ausgeschaltet.")
    _write(rc, prefs)
    _luarc_add(os.path.join(cfg, "luarc"))
    return notes


def uninstall(cfg=None):
    cfg = cfg or dtconfig.config_dir()
    if dtconfig.is_running(cfg):
        raise PluginError("darktable läuft – bitte darktable schließen und noch einmal versuchen.")
    d = plugin_dir(cfg)
    if os.path.isfile(os.path.join(d, "kader.lua")):
        shutil.rmtree(d)
    rc = os.path.join(cfg, "darktablerc")
    if os.path.isfile(rc):
        _write(rc, set_pref(_read(rc), PREF_KEY, "FALSE"))
    luarc = os.path.join(cfg, "luarc")
    if os.path.isfile(luarc):
        text = _read(luarc)
        new = _luarc_strip(text)
        if new != text:
            _write(luarc, new)


def refresh(cfg=None):
    """Beim Start der App: steht ein anderer Ort in kader_command, den aktuellen eintragen."""
    command = app_command()
    st = status(cfg)
    if not command or not st["installed"] or st["command"] == command:
        return False
    try:
        _write(os.path.join(st["dir"], "kader_command"), command + "\n")
    except OSError:
        return False
    return True


def get_pref(text, key):
    for line in text.splitlines():
        k, sep, v = line.partition("=")
        if sep and k == key:
            return v
    return None


def set_pref(text, key, value):
    """``key=value`` in darktablerc setzen; alle anderen Zeilen bleiben unveraendert."""
    lines, found = text.splitlines(True), False
    for i, line in enumerate(lines):
        if line.partition("=")[0] == key and "=" in line:
            lines[i] = f"{key}={value}\n"
            found = True
    if not found:
        if lines and not lines[-1].endswith("\n"):
            lines[-1] += "\n"
        lines.append(f"{key}={value}\n")
    return "".join(lines)


def _luarc_add(path):
    text = _read(path) if os.path.isfile(path) else ""
    text = _luarc_strip(text)
    if text and not text.endswith("\n"):
        text += "\n"
    _write(path, text + LUARC_MARK + "\n" + LUARC_LINE + "\n")


def _luarc_strip(text):
    """Kader-Eintrag (Markierung und die Zeile danach) entfernen, auch aeltere Fassungen."""
    out, skip = [], False
    for line in text.splitlines(True):
        if skip:
            skip = False
            if "contrib/kader/kader" in line:
                continue
        if line.strip() == LUARC_MARK:
            skip = True
            continue
        out.append(line)
    return "".join(out)


def _read(path):
    with open(path, encoding="utf-8", errors="surrogateescape") as f:
        return f.read()


def _write(path, text):
    tmp = path + ".kader-tmp"
    with open(tmp, "w", encoding="utf-8", errors="surrogateescape", newline="") as f:
        f.write(text)
    os.replace(tmp, path)
