"""Einstieg der gebuendelten Builds (exe/AppImage): Doppelklick ohne Terminal.

Ordner kommt per Drag&Drop auf die Datei (erstes Argument) oder aus einem Ordner-Dialog; danach
wie ``kader open ORDNER --window``. Ohne Konsole landet Textausgabe im Nichts, deshalb wird sie
gesammelt und bei einem Fehler als Meldungsfenster gezeigt.
"""
import contextlib
import io
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
        from . import __main__ as cli      # echte Kommandozeile, z. B. "kader.exe check ORDNER"
        return cli.main(argv)
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
