"""Einstieg der gebuendelten Builds (exe/AppImage): Doppelklick ohne Terminal.

Ordner kommt per Drag&Drop auf die Datei (erstes Argument) oder aus einem Ordner-Dialog; danach
wie ``kader open ORDNER --window``. Ohne Konsole landet Textausgabe im Nichts, deshalb wird sie
gesammelt und bei einem Fehler als Meldungsfenster gezeigt.

Ist darktable installiert, bietet der erste Doppelklick an, das darktable-Plugin einzurichten
(``dtplugin``); jeder weitere Start traegt einen geaenderten Ort der App dort nach.
"""
import contextlib
import io
import json
import os
import sys
import traceback


class _Sink(io.StringIO):
    """Puffer, der auch als ``sys.stdout`` taugt, wenn es (Windows ohne Konsole) keins gibt."""

    def isatty(self):
        return False


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and not os.path.isdir(argv[0]):
        # echte Kommandozeile, z. B. "kader.exe check ORDNER" oder "serve --job" aus dem Plugin;
        # die exe ohne Konsole hat kein stdout/stderr
        sys.stdout = sys.stdout or _Sink()
        sys.stderr = sys.stderr or _Sink()
        from . import __main__ as cli
        return cli.main(argv)
    _refresh_plugin()
    if not argv:
        _offer_plugin()
    folder = argv[0] if argv else _ask_folder()
    if not folder:
        return 0

    out = _Sink()
    try:
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            from . import __main__ as cli
            code = cli.main(["open", folder, "--window"])
    except SystemExit as exc:
        code = exc.code if isinstance(exc.code, int) else 1
    except Exception:           # noqa: BLE001
        out.write(traceback.format_exc())
        code = 1
    if code:
        _error(out.getvalue().strip() or f"Kader wurde mit Code {code} beendet.")
    return code


def settings_path():
    if sys.platform == "win32":
        base = os.environ.get("APPDATA") or os.path.expanduser("~")
    else:
        base = os.environ.get("XDG_CONFIG_HOME") or os.path.join(os.path.expanduser("~"), ".config")
    return os.path.join(base, "kader", "launcher.json")


def _settings():
    try:
        with open(settings_path(), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def _save_settings(data):
    try:
        os.makedirs(os.path.dirname(settings_path()), exist_ok=True)
        with open(settings_path(), "w", encoding="utf-8") as f:
            json.dump(data, f)
    except OSError:
        pass


def _refresh_plugin():
    try:
        from . import dtplugin
        dtplugin.refresh()
    except Exception:           # noqa: BLE001 - nie den Start verhindern
        pass


def should_offer_plugin():
    """Plugin anbieten? Nur aus der App, mit darktable, noch nicht installiert, nicht abgelehnt."""
    from . import dtplugin
    if not dtplugin.app_command() or _settings().get("darktable_plugin") == "never":
        return False
    st = dtplugin.status()
    return st["darktable"] and not st["installed"]


def _offer_plugin():
    try:
        if not should_offer_plugin():
            return
        choice = _ask_plugin()
    except Exception:           # noqa: BLE001
        return
    if choice == "never":
        _save_settings({**_settings(), "darktable_plugin": "never"})
    if choice != "install":
        return
    from . import dtplugin
    try:
        dtplugin.install()
    except (dtplugin.PluginError, OSError) as e:
        _message("Kader", f"Das Plugin wurde nicht installiert:\n{e}", error=True)
        return
    _message("Kader", "\n\n".join([
        "Das darktable-Plugin ist installiert.",
        "darktable (neu) starten: im Leuchttisch erscheint das Modul „Kader“. Bilder auswählen, "
        "„Review starten“, im Browser prüfen und „Fertig“, dann in darktable „Plan anwenden“.",
        "Jetzt kannst du auch direkt einen Ordner prüfen – oder den nächsten Dialog abbrechen."]))


def _ask_plugin():
    """Kleiner Dialog im Stil des Statusfensters: install | later | never."""
    import tkinter as tk
    from tkinter import font as tkfont
    from .window import COL, _dark_titlebar, _register_fonts
    _register_fonts()
    root = tk.Tk()
    root.title("Kader")
    root.configure(bg=COL["bg"])
    root.resizable(False, False)
    fams = set(tkfont.families(root))
    body = tkfont.nametofont("TkDefaultFont").actual()["family"]
    head = "Rajdhani" if "Rajdhani" in fams else body
    u = tkfont.Font(root, font=(body, 10)).measure("0")
    choice = {"v": "later"}

    def pick(v):
        choice["v"] = v
        root.destroy()

    frame = tk.Frame(root, bg=COL["bg"], padx=u * 3, pady=u * 2)
    frame.pack()
    tk.Label(frame, text="darktable gefunden", font=(head, 17, "bold"), bg=COL["bg"],
             fg=COL["fg0"], anchor="w").pack(fill="x")
    tk.Label(frame, justify="left", anchor="w", wraplength=u * 48, font=(body, 10), bg=COL["bg"],
             fg=COL["fg2"], text=(
                 "Kader kann sich als Plugin in darktable einklinken. Dann prüfst du direkt aus darktable: "
                 "Bilder auswählen, „Review starten“ – der Zuschnitt landet ohne Umweg in deiner "
                 "Bearbeitung.\n\nOhne Plugin geht es auch: Kader schreibt dann XMP-Dateien, die darktable "
                 "beim Import liest.")).pack(fill="x", pady=(u, u * 3))
    row = tk.Frame(frame, bg=COL["bg"])
    row.pack(fill="x")
    btn = {"font": (head, 12, "bold"), "relief": "flat", "bd": 0, "highlightthickness": 0,
           "padx": u * 2, "pady": u // 2, "cursor": "hand2"}
    tk.Button(row, text="Plugin installieren", command=lambda: pick("install"), bg=COL["aqua"],
              fg=COL["bar"], activebackground=COL["aqua_h"], activeforeground=COL["bar"], **btn).pack(side="left")
    for text, v in (("Nie fragen", "never"), ("Nicht jetzt", "later")):
        wrap = tk.Frame(row, bg=COL["border"], padx=1, pady=1)
        tk.Button(wrap, text=text, command=lambda v=v: pick(v), bg=COL["bg"], fg=COL["fg1"],
                  activebackground=COL["line"], activeforeground=COL["fg0"], **btn).pack()
        wrap.pack(side="right", padx=(u, 0))
    root.protocol("WM_DELETE_WINDOW", lambda: pick("later"))
    root.update_idletasks()
    _dark_titlebar(root)
    root.mainloop()
    return choice["v"]


def _message(title, text, error=False):
    try:
        import tkinter as tk
        from tkinter import messagebox
        root = tk.Tk()
        root.withdraw()
        (messagebox.showerror if error else messagebox.showinfo)(title, text)
        root.destroy()
    except Exception:           # noqa: BLE001
        print(text, file=sys.__stderr__ or sys.stderr)


def _ask_folder():
    import tkinter as tk
    from tkinter import filedialog
    root = tk.Tk()
    root.withdraw()
    folder = filedialog.askdirectory(title="Kader – Ordner mit den abfotografierten Negativen wählen",
                                     mustexist=True)
    root.destroy()
    return folder or None


def _error(message):
    lines = message.splitlines()
    if len(lines) > 25:
        message = "\n".join(["…"] + lines[-25:])
    try:
        import tkinter as tk
        from tkinter import messagebox
        root = tk.Tk()
        root.withdraw()
        messagebox.showerror("Kader", message)
        root.destroy()
    except Exception:           # noqa: BLE001 - ohne Tk bleibt nur stderr
        print(message, file=sys.__stderr__ or sys.stderr)


if __name__ == "__main__":
    sys.exit(main())
