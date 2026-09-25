"""Kleines Statusfenster (tkinter) fuer den Doppelklick-Start ohne Terminal.

Gleicher Vertrag wie ``tui.run``: blockiert im Hauptthread, bis der Server endet (Knopf
Beenden, Fenster schliessen, "Server beenden" in der Web-UI, Leerlauf). Ohne Fenster haette ein
per Doppelklick gestarteter Server keine sichtbare Spur und liesse sich nur ueber die Web-UI
beenden.
"""
import os
import webbrowser

POLL_MS = 500

STOP_TEXT = {"quit": "Beendet.", "idle": "Wegen Leerlauf beendet.", "signal": "Beendet."}


def unavailable_reason():
    try:
        import tkinter  # noqa: F401
    except ImportError:
        return "tkinter fehlt (unter Linux meist das Paket python3-tk)"
    return None


def _status(app):
    st = app.session.public_state()
    prog = app.analyzer.progress
    n = st["summary"]
    if prog.get("busy"):
        stage = {"export": "RAW-Export", "detect": "Erkennung", "skew": "Schräglage"}.get(
            prog.get("stage"), prog.get("stage") or "Analyse")
        head = f"{stage}: {prog.get('done', 0)} von {prog.get('total', 0)} Bildern"
    elif st["phase"] == "applied":
        head = "Fertig – Ergebnis geschrieben."
    elif st["phase"] == "apply_failed":
        head = "Fertig mit Fehlern – Details in der Web-UI."
    else:
        head = "Bereit zur Prüfung im Browser."
    counts = f"{n['green']} grün · {n['yellow']} gelb · {n['red']} rot · {n['total']} gesamt"
    return head, counts


def run(app):
    import tkinter as tk
    from tkinter import font as tkfont
    from tkinter import ttk

    root = tk.Tk()
    root.title("Kader")
    wrap = tkfont.nametofont("TkDefaultFont").measure("0") * 48   # skaliert mit der echten Schrift
    frame = ttk.Frame(root, padding=16)
    frame.pack(fill="both", expand=True)

    folder = (app.session.state.get("source") or {}).get("folder") or ""
    ttk.Label(frame, text=os.path.basename(folder) or folder, font=("TkDefaultFont", 12, "bold")).pack(anchor="w")
    ttk.Label(frame, text=folder, foreground="gray", wraplength=wrap).pack(anchor="w", pady=(0, 10))
    head_var, counts_var = tk.StringVar(), tk.StringVar()
    ttk.Label(frame, textvariable=head_var).pack(anchor="w")
    ttk.Label(frame, textvariable=counts_var, foreground="gray").pack(anchor="w", pady=(0, 12))
    if not app.loopback_only:
        ttk.Label(frame, text=f"Im Netzwerk erreichbar ({app.bind}) – nur der Token schützt den Zugriff.",
                  foreground="red", wraplength=wrap).pack(anchor="w", pady=(0, 10))

    buttons = ttk.Frame(frame)
    buttons.pack(fill="x")
    ttk.Button(buttons, text="Im Browser öffnen", command=lambda: webbrowser.open(app.url)).pack(side="left")

    def quit_():
        app.stop("quit")

    ttk.Button(buttons, text="Beenden", command=quit_).pack(side="right")
    root.protocol("WM_DELETE_WINDOW", quit_)

    def poll():
        if app.stop_reason is not None:
            head_var.set(STOP_TEXT.get(app.stop_reason, "Beendet."))
            root.after(600, root.destroy)
            return
        try:
            head, counts = _status(app)
            head_var.set(head)
            counts_var.set(counts)
        except Exception:       # noqa: BLE001 - Anzeige darf den Server nie mitreissen
            pass
        root.after(POLL_MS, poll)

    poll()
    root.mainloop()
