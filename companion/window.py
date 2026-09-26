"""Kleines Statusfenster (tkinter) fuer den Doppelklick-Start ohne Terminal.

Gleicher Vertrag wie ``tui.run``: blockiert im Hauptthread, bis der Server endet (Knopf
Beenden, Fenster schliessen, "Server beenden" in der Web-UI, Leerlauf). Waehrend der Analyse
zeigt es einen Filmstreifen als Ladeleiste, danach die Kennzahlen (Farben wie die Web-UI).
"""
import os
import subprocess
import sys
import webbrowser

from . import programs
from .session import read_json

POLL_MS = 500
MAX_CELLS = 60                 # mehr Bilder: ein Feld steht fuer mehrere

COL = {
    "bg": "#282828", "bar": "#1d2021", "line": "#3c3836", "border": "#504945",
    "fg0": "#fbf1c7", "fg1": "#ebdbb2", "fg2": "#d5c4a1", "fg3": "#a89984",
    "blue": "#83a598", "blue_h": "#6d9181", "aqua": "#8ec07c", "aqua_h": "#79a869",
    "yellow": "#fabd2f", "red": "#fb4934",
}
STAGE_TEXT = {"export": "RAW-Entwicklung", "detect": "Erkennung", "skew": "Schräglage wird gemessen"}
STOP_TEXT = {"quit": "Beendet.", "idle": "Wegen Leerlauf beendet.", "signal": "Beendet."}
# Ziele, deren Ergebnis ein Programm beim Oeffnen des Ordners liest: Knopf "In ... oeffnen"
EDITORS = {"darktable_xmp": (programs.DARKTABLE, "darktable öffnen"),
           "rawtherapee": (programs.RAWTHERAPEE, "RawTherapee öffnen")}
FONT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static", "vendor", "fonts")


def unavailable_reason():
    try:
        import tkinter  # noqa: F401
    except ImportError:
        return "tkinter fehlt (unter Linux meist das Paket python3-tk)"
    return None


def snapshot(app):
    """Was das Fenster zeigt, ohne Tk: ``mode`` analysis | ready | done | failed."""
    s = app.session
    prog = dict(app.analyzer.progress)
    with s.lock:
        groups = [s.group_of(img) for img in s.state["images"].values()]
        phase = s.phase
    total = len(groups)
    snap = {"total": total, "folder": (s.state.get("source") or {}).get("folder") or ""}
    if prog.get("busy") or phase == "analyzing":
        stage = prog.get("stage") or ""
        frac = prog["done"] / prog["total"] if prog.get("total") else (1.0 if stage == "skew" else 0.0)
        frac = max(0.0, min(1.0, frac))
        snap.update(mode="analysis", stage=stage, frac=frac, images_done=round(frac * total))
        return snap
    if phase in ("applied", "apply_failed"):
        res = read_json(s.path("result.json"), {}) or {}
        st = [i.get("status") for i in (res.get("images") or {}).values()]
        out = s.target.describe(s).get("out") or snap["folder"]
        snap.update(mode="done" if phase == "applied" else "failed", out=out, message=res.get("message"),
                    target=s.target.name,
                    ok=st.count("ok"), skipped=st.count("skipped"), error=st.count("error"))
        return snap
    snap.update(mode="ready", **{g: groups.count(g) for g in ("green", "yellow", "red")})
    return snap


def editor_for(target, find_gui=None):
    """(Knopftext, argv) fuer das Programm, das das Ergebnis dieses Ziels liest, oder None."""
    if target not in EDITORS:
        return None
    name, label = EDITORS[target]
    argv = (find_gui or programs.find_gui)(name)
    return (label, argv) if argv else None


def strip_cells(snap):
    """Felder des Filmstreifens: True = schon verarbeitet."""
    n = max(1, min(snap["total"], MAX_CELLS))
    filled = int(snap.get("frac", 1.0) * n + 1e-9)
    return [i < filled for i in range(n)]


def _register_fonts():
    """Rajdhani/JetBrains Mono aus dem Designsystem fuer Tk verfuegbar machen (vor ``Tk()``)."""
    try:
        files = [os.path.join(FONT_DIR, f) for f in os.listdir(FONT_DIR) if f.endswith(".ttf")]
    except OSError:
        return
    try:
        import ctypes
        if sys.platform == "win32":
            for f in files:
                ctypes.windll.gdi32.AddFontResourceExW(f, 0x10, 0)      # FR_PRIVATE
        else:
            import ctypes.util
            lib = ctypes.CDLL(ctypes.util.find_library("fontconfig") or "libfontconfig.so.1")
            lib.FcConfigAppFontAddFile.argtypes = [ctypes.c_void_p, ctypes.c_char_p]
            for f in files:
                lib.FcConfigAppFontAddFile(None, f.encode())
    except (OSError, AttributeError):
        pass                    # dann eben die Systemschrift


def _dark_titlebar(root):
    if sys.platform != "win32":
        return
    try:
        import ctypes
        hwnd = ctypes.windll.user32.GetParent(root.winfo_id())
        on = ctypes.c_int(1)
        ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, 20, ctypes.byref(on), ctypes.sizeof(on))
    except (OSError, AttributeError):
        pass


def open_folder(path):
    if sys.platform == "win32":
        os.startfile(path)      # noqa: S606
    else:
        subprocess.Popen(["open" if sys.platform == "darwin" else "xdg-open", path], env=programs.clean_env())


class Window:
    def __init__(self, app):
        import tkinter as tk
        from tkinter import font as tkfont

        self.app, self.tk = app, tk
        _register_fonts()
        self.root = root = tk.Tk()
        root.title("Kader")
        root.configure(bg=COL["bg"])
        root.resizable(False, False)
        fams = set(tkfont.families(root))
        head = "Rajdhani" if "Rajdhani" in fams else tkfont.nametofont("TkDefaultFont").actual()["family"]
        mono = "JetBrains Mono" if "JetBrains Mono" in fams else tkfont.nametofont("TkFixedFont").actual()["family"]
        body = tkfont.nametofont("TkDefaultFont").actual()["family"]
        self.f = {
            "name": (head, 17, "bold"), "num": (head, 26, "bold"), "btn": (head, 12, "bold"),
            "badge": (mono, 8), "mono": (mono, 8), "cap": (mono, 7), "body": (body, 10),
        }
        self.unit = tkfont.Font(root, font=self.f["body"]).measure("0")
        self.width = self.unit * 50
        self.mode = None

        body_f = tk.Frame(root, bg=COL["bg"], padx=self.unit * 3, pady=self.unit * 2)
        body_f.pack(fill="both", expand=True)
        tk.Frame(body_f, width=self.width, height=0, bg=COL["bg"]).pack()   # feste Breite fuer alle Zustaende
        top = tk.Frame(body_f, bg=COL["bg"])
        top.pack(fill="x")
        folder = (app.session.state.get("source") or {}).get("folder") or ""
        tk.Label(top, text=os.path.basename(folder.rstrip("\\/")) or folder, font=self.f["name"],
                 bg=COL["bg"], fg=COL["fg0"]).pack(side="left")
        self.badge = tk.Label(top, font=self.f["badge"], bg=COL["bg"], padx=6, pady=1)
        self.path = tk.Label(body_f, font=self.f["mono"], bg=COL["bg"], fg=COL["fg3"], justify="left",
                             anchor="w", wraplength=self.width)
        self.path.pack(fill="x", pady=(2, self.unit * 2))

        self.content = tk.Frame(body_f, bg=COL["bg"])
        self.content.pack(fill="x")
        self._build_strip()
        self._build_facts()
        if not app.loopback_only:
            tk.Label(body_f, text=f"Im Netzwerk erreichbar ({app.bind}) – nur der Token schützt den Zugriff.",
                     font=self.f["body"], bg=COL["bg"], fg=COL["red"], wraplength=self.width,
                     justify="left").pack(fill="x", pady=(self.unit, 0))

        btns = tk.Frame(body_f, bg=COL["bg"])
        btns.pack(fill="x", pady=(self.unit * 3, 0))
        self.primary = tk.Button(btns, font=self.f["btn"], relief="flat", bd=0, highlightthickness=0,
                                 padx=self.unit * 2, pady=self.unit // 2, cursor="hand2", fg=COL["bar"],
                                 activeforeground=COL["bar"])
        self.primary.pack(side="left")
        self.quit_btn = self._outline(btns, "Beenden", self.quit)
        self.quit_btn.pack(side="right")
        self.browser_btn = self._outline(btns, "Browser", self.open_browser)
        self.editor = None
        root.protocol("WM_DELETE_WINDOW", self.quit)
        root.update_idletasks()
        _dark_titlebar(root)

    def _outline(self, parent, text, cmd):
        tk = self.tk
        wrap = tk.Frame(parent, bg=COL["border"], padx=1, pady=1)
        tk.Button(wrap, text=text, command=cmd, font=self.f["btn"], relief="flat", bd=0, highlightthickness=0,
                  padx=self.unit * 2 - 1, pady=self.unit // 2 - 1, cursor="hand2", bg=COL["bg"], fg=COL["fg1"],
                  activebackground=COL["line"], activeforeground=COL["fg0"]).pack()
        return wrap

    # -- Variante Filmstreifen (Analyse) --------------------------------------

    def _build_strip(self):
        tk = self.tk
        u = self.unit
        self.strip = tk.Frame(self.content, bg=COL["bg"])
        self.canvas = tk.Canvas(self.strip, width=self.width, height=int(u * 5.2), bg=COL["bar"],
                                highlightthickness=0, bd=0)
        self.canvas.pack(fill="x")
        row = tk.Frame(self.strip, bg=COL["bg"])
        row.pack(fill="x", pady=(int(u * 1.5), 0))
        self.stage = tk.Label(row, font=self.f["body"], bg=COL["bg"], fg=COL["fg1"])
        self.stage.pack(side="left")
        self.count = tk.Label(row, font=self.f["mono"], bg=COL["bg"], fg=COL["fg3"])
        self.count.pack(side="right")

    def _draw_strip(self, cells):
        c, u, w = self.canvas, self.unit, self.width
        c.delete("all")
        pad, gap = u, max(1, u // 4)
        ph, cell_h = max(2, int(u * 0.7)), int(u * 2.6)
        holes = 22
        hole_w = (w - 2 * pad) / (holes * 1.9)
        step = (w - 2 * pad - hole_w) / (holes - 1)
        for y in (pad * 0.6, pad * 0.6 + ph + gap * 2 + cell_h + gap * 2):
            for i in range(holes):
                x = pad + i * step
                c.create_rectangle(x, y, x + hole_w, y + ph, fill=COL["line"], outline="")
        y0 = pad * 0.6 + ph + gap * 2
        cw = (w - 2 * pad - gap * (len(cells) - 1)) / len(cells)
        for i, done in enumerate(cells):
            x = pad + i * (cw + gap)
            c.create_rectangle(x, y0, x + cw, y0 + cell_h, fill=COL["yellow"] if done else COL["line"], outline="")

    # -- Variante Kennzahlen (Bereit, Fertig) ---------------------------------

    def _build_facts(self):
        tk = self.tk
        self.facts = tk.Frame(self.content, bg=COL["bg"])
        grid = tk.Frame(self.facts, bg=COL["bg"])
        grid.pack(fill="x")
        self.fact_w = []
        for i in range(4):
            grid.columnconfigure(i, weight=1, uniform="fact")
            cell = tk.Frame(grid, bg=COL["bg"])
            cell.grid(row=0, column=i, sticky="we", padx=(0 if i == 0 else self.unit, 0))
            rule = tk.Frame(cell, height=2, bg=COL["border"])
            rule.pack(fill="x")
            num = tk.Label(cell, font=self.f["num"], bg=COL["bg"], anchor="w")
            num.pack(fill="x")
            cap = tk.Label(cell, font=self.f["cap"], bg=COL["bg"], fg=COL["fg3"], anchor="w")
            cap.pack(fill="x")
            self.fact_w.append((rule, num, cap))
        self.hint = tk.Label(self.facts, font=self.f["body"], bg=COL["bg"], fg=COL["fg2"], anchor="w",
                             justify="left", wraplength=self.width)

    def _set_facts(self, facts, hint, hint_color=COL["fg2"]):
        for (rule, num, cap), (value, label, color) in zip(self.fact_w, facts):
            rule.configure(bg=COL["border"] if color == COL["fg0"] else color)
            num.configure(text=str(value), fg=color)
            cap.configure(text=label.upper())
        self.hint.configure(text=hint, fg=hint_color)
        if hint:
            self.hint.pack(fill="x", pady=(int(self.unit * 1.5), 0))
        else:
            self.hint.pack_forget()

    # -- Zustand --------------------------------------------------------------

    def _switch(self, mode):
        if mode == self.mode:
            return
        self.mode = mode
        analysis = mode == "analysis"
        (self.facts if analysis else self.strip).pack_forget()
        (self.strip if analysis else self.facts).pack(fill="x")
        if analysis:
            self.badge.pack_forget()
        else:
            self.badge.pack(side="right")
        if mode == "done":
            self.editor = editor_for(snapshot(self.app).get("target"))
            if self.editor:
                self.primary.configure(text=self.editor[0], bg=COL["aqua"], activebackground=COL["aqua_h"],
                                       command=self.open_editor)
            else:
                self.primary.configure(text="Ausgabeordner öffnen", bg=COL["aqua"],
                                       activebackground=COL["aqua_h"], command=self.open_out)
            self.browser_btn.pack(side="right", padx=(0, self.unit))
        else:
            self.primary.configure(text="Im Browser öffnen", bg=COL["blue"], activebackground=COL["blue_h"],
                                   command=self.open_browser)
            self.browser_btn.pack_forget()

    def _badge(self, text, color):
        self.badge.configure(text=text.upper(), fg=color, highlightbackground=color,
                             highlightcolor=color, highlightthickness=1, bd=0)

    def show(self, snap):
        self._switch(snap["mode"])
        mode = snap["mode"]
        if mode == "analysis":
            self.path.configure(text=snap["folder"])
            stage = STAGE_TEXT.get(snap["stage"], "Analyse")
            self.stage.configure(text=stage if snap["stage"] == "skew"
                                 else f"{stage} · {snap['images_done']} von {snap['total']}")
            self.count.configure(text=f"{snap['total']} Bilder")
            self._draw_strip(strip_cells(snap))
        elif mode == "ready":
            self.path.configure(text=snap["folder"])
            self._badge("Bereit", COL["blue"])
            look = snap["yellow"] + snap["red"]
            hint = (f"{look} {'Bild braucht' if look == 1 else 'Bilder brauchen'} einen Blick im Browser."
                    if look else "Alle Bilder sicher erkannt – im Browser mit Fertig abschließen.")
            def tone(n, color):
                return color if n else COL["fg3"]                     # Null ist kein Signal
            self._set_facts([(snap["green"], "grün", tone(snap["green"], COL["aqua"])),
                             (snap["yellow"], "gelb", tone(snap["yellow"], COL["yellow"])),
                             (snap["red"], "rot", tone(snap["red"], COL["red"])),
                             (snap["total"], "gesamt", COL["fg0"])], hint)
        else:
            self.path.configure(text="→ " + snap["out"])
            failed = mode == "failed" or bool(snap["error"])
            self._badge("Fehler" if failed else "Fertig", COL["red"] if failed else COL["aqua"])
            self._set_facts([(snap["ok"], "geschrieben", COL["aqua"]),
                             (snap["skipped"], "übersprungen", COL["red"] if snap["skipped"] else COL["fg3"]),
                             (snap["error"], "Fehler", COL["red"] if snap["error"] else COL["fg3"]),
                             (snap["total"], "gesamt", COL["fg0"])],
                            snap.get("message") or ("Details in der Web-UI." if failed else ""),
                            COL["red"] if failed else COL["fg2"])

    # -- Aktionen -------------------------------------------------------------

    def open_browser(self):
        webbrowser.open(self.app.url)

    def open_out(self):
        snap = snapshot(self.app)
        path = snap.get("out") or snap["folder"]
        try:
            open_folder(path if os.path.isdir(path) else snap["folder"])
        except OSError:
            self.open_browser()

    def open_editor(self):
        folder = snapshot(self.app)["folder"]
        try:
            programs.launch(self.editor[1] + [folder])
        except OSError:
            self.open_out()

    def quit(self):
        self.app.stop("quit")

    def poll(self):
        if self.app.stop_reason is not None:
            text = STOP_TEXT.get(self.app.stop_reason, "Beendet.")
            self.hint.configure(text=text)
            self.hint.pack(fill="x", pady=(int(self.unit * 1.5), 0))
            self.stage.configure(text=text)
            self.root.after(600, self.root.destroy)
            return
        try:
            self.show(snapshot(self.app))
        except Exception:       # noqa: BLE001 - Anzeige darf den Server nie mitreissen
            pass
        self.root.after(POLL_MS, self.poll)


def run(app):
    w = Window(app)
    w.poll()
    w.root.mainloop()
