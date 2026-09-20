#!/usr/bin/env python3
"""Review-GUI for Auto-Crop-Negative Batch results.

Hinweis: Wird langfristig durch die Web-UI ersetzt (python -m companion serve --folder
Testphotos ...), siehe companion-ui-plan.md. Bleibt bis zur praktischen Paritaet erhalten.

Tastatur: Pfeile=Navigation, O=OK, P=Problem, R=Reset,
          1-9=springe 10..90, Ctrl+S=speichern, Esc=schliessen

Crop-Editor:
  Auf die Bildecken/Seiten klicken und ziehen um den Crop anzupassen.
  Die manuelle Korrektur wird als Referenz fuer die Verbesserung gespeichert.
"""
import json
import os
import sys
import tkinter as tk
from tkinter import ttk, messagebox
from PIL import Image, ImageTk, ImageDraw

PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(PROJECT_DIR, "review_data")
RESULTS_FILE = os.path.join(DATA_DIR, "results.json")
REVIEW_FILE = os.path.join(DATA_DIR, "reviews.json")
HANDLE_R = 8  # Pixeltoleranz fuer Drag-Handles


def load_results():
    with open(RESULTS_FILE) as f:
        return json.load(f)


def load_reviews():
    if os.path.exists(REVIEW_FILE):
        with open(REVIEW_FILE) as f:
            return json.load(f)
    return {}


def save_reviews(reviews):
    with open(REVIEW_FILE, "w") as f:
        json.dump(reviews, f, indent=2, ensure_ascii=False)


def _edge_at(cx, cy, rx, ry, rw, rh, zone=HANDLE_R):
    """Prueft ob Punkt (cx,cy) nah an einer Rectangle-Kante/Datei ist.
    Gibt None oder 'n','s','e','w','nw','ne','sw','se' zurueck."""
    # Ecken zuerst pruefen
    corners = {
        "nw": (rx, ry), "ne": (rx + rw, ry),
        "sw": (rx, ry + rh), "se": (rx + rw, ry + rh),
    }
    for name, (px, py) in corners.items():
        if abs(cx - px) <= zone and abs(cy - py) <= zone:
            return name
    # Kanten
    sides = {
        "n": (rx <= cx <= rx + rw, abs(cy - ry) <= zone),
        "s": (rx <= cx <= rx + rw, abs(cy - (ry + rh)) <= zone),
        "w": (abs(cx - rx) <= zone, ry <= cy <= ry + rh),
        "e": (abs(cx - (rx + rw)) <= zone, ry <= cy <= ry + rh),
    }
    for name, (cond_x, cond_y) in sides.items():
        if cond_x and cond_y:
            return name
    return None


def _cursor_for_edge(edge):
    """Gibt Cursor-Name fuer eine Kante zurueck."""
    if edge in ("nw", "se"):
        return "size_nw_se"
    if edge in ("ne", "sw"):
        return "size_ne_sw"
    if edge in ("n", "s"):
        return "sb_v_double_arrow"
    if edge in ("e", "w"):
        return "sb_h_double_arrow"
    return ""


class ReviewGUI:
    def __init__(self, root):
        self.root = root
        self.root.title("Auto-Crop-Negative - Review GUI")
        self.root.geometry("1400x900")
        self.root.configure(bg="#2b2b2b")
        self.results = load_results()
        self.reviews = load_reviews()
        self.filtered = list(range(len(self.results)))
        self.current_idx = 0
        self._photo = None
        self._bg_photo = None
        self._updating = False
        self._orig_img = None      # Gecachtes PIL-Image fuer Resize
        self._resize_after_id = None  # Debounce-Timer fuer Resize
        # Crop-Editor State
        self._scale = 1.0
        self._ox = 0
        self._oy = 0
        self._crop = {}  # Aktueller Crop (Bild-Koordinaten)
        self._auto_crop = {}  # Automatischer Crop
        self._drag = None
        self._rect_id = None
        self._handle_ids = []
        self._label_id = None
        self._diff_id = None  # Differenz-Anzeige
        self._edge_zone = HANDLE_R
        for r in self.results:
            key = self._key(r)
            if key in self.reviews:
                r["needs_review"] = self.reviews[key].get(
                    "needs_review", r["needs_review"])
        self._build_ui()
        self._update_image()

    def _key(self, r):
        return f"{r.get('film', '')}/{r.get('filename', '')}"

    def _build_ui(self):
        style = ttk.Style()
        style.theme_use("clam")
        style.configure("Dark.TFrame", background="#2b2b2b")
        style.configure("Dark.TLabel", background="#2b2b2b",
                        foreground="#e0e0e0", font=("Segoe UI", 11))
        style.configure("Hdr.TLabel", background="#2b2b2b",
                        foreground="#fff", font=("Segoe UI", 14, "bold"))
        style.configure("St.TLabel", background="#2b2b2b",
                        foreground="#aaa", font=("Segoe UI", 10))
        style.configure("Ok.TLabel", background="#2b2b2b",
                        foreground="#4caf50", font=("Segoe UI", 10, "bold"))
        style.configure("Pr.TLabel", background="#2b2b2b",
                        foreground="#f44336", font=("Segoe UI", 10, "bold"))
        style.configure("Mod.TLabel", background="#2b2b2b",
                        foreground="#ffaa00", font=("Segoe UI", 10, "bold"))
        # top filter bar
        top = ttk.Frame(self.root, style="Dark.TFrame")
        top.pack(fill=tk.X, padx=10, pady=(10, 0))
        ttk.Label(top, text="Filter:", style="Dark.TLabel").pack(side=tk.LEFT)
        self.filter_var = tk.StringVar(value="all")
        for val, txt in [("all", "Alle"), ("ok", "OK"),
                         ("review", "Review"), ("problem", "Problem")]:
            ttk.Radiobutton(top, text=txt, variable=self.filter_var,
                            value=val, command=self._apply_filter
                            ).pack(side=tk.LEFT, padx=5)
        ttk.Label(top, text="Film:", style="Dark.TLabel").pack(
            side=tk.LEFT, padx=(20, 0))
        self.film_var = tk.StringVar(value="Alle")
        films = sorted(set(r.get("film", "") for r in self.results))
        fm = ttk.Combobox(top, textvariable=self.film_var,
                          values=["Alle"] + films, state="readonly", width=15)
        fm.pack(side=tk.LEFT, padx=5)
        fm.bind("<<ComboboxSelected>>", lambda e: self._apply_filter())
        self.stats_lbl = ttk.Label(top, text="", style="St.TLabel")
        self.stats_lbl.pack(side=tk.RIGHT, padx=10)

        # main area
        main = ttk.Frame(self.root, style="Dark.TFrame")
        main.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)
        self.canvas = tk.Canvas(main, bg="#1a1a1a", highlightthickness=0)
        self.canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        # info panel
        info = ttk.Frame(main, style="Dark.TFrame")
        info.pack(side=tk.RIGHT, fill=tk.Y, padx=(10, 0))
        self.title_lbl = ttk.Label(info, text="", style="Hdr.TLabel",
                                   wraplength=380)
        self.title_lbl.pack(anchor=tk.W, pady=(0, 10))
        self._ilbl = {}
        for k, t in [("conf", "Konfidenz:"), ("meth", "Methode:"),
                      ("ori", "Orientierung:"), ("rat", "Seitenverh:"),
                      ("area", "Flaeche:"), ("pos", "Position:"),
                      ("marea", "Manueller Crop:")]:
            f = ttk.Frame(info, style="Dark.TFrame")
            f.pack(fill=tk.X, pady=2)
            ttk.Label(f, text=t, style="Dark.TLabel", width=20).pack(
                side=tk.LEFT)
            lbl = ttk.Label(f, text="", style="Dark.TLabel")
            lbl.pack(side=tk.LEFT)
            self._ilbl[k] = lbl
        ttk.Label(info, text="\nGruende:", style="Dark.TLabel").pack(
            anchor=tk.W)
        self.reasons = tk.Text(info, height=6, bg="#1e1e1e", fg="#ccc",
                               font=("Consolas", 10), wrap=tk.WORD,
                               relief=tk.FLAT, state=tk.DISABLED)
        self.reasons.pack(fill=tk.X, pady=5)
        ttk.Label(info, text="Bewertung:", style="Dark.TLabel").pack(
            anchor=tk.W)
        self.rev_lbl = ttk.Label(info, text="-", style="Dark.TLabel")
        self.rev_lbl.pack(anchor=tk.W, pady=2)
        ttk.Label(info, text="Notiz:", style="Dark.TLabel").pack(anchor=tk.W)
        self.note_var = tk.StringVar()
        ttk.Entry(info, textvariable=self.note_var,
                  font=("Segoe UI", 10)).pack(fill=tk.X, pady=2)
        bf = ttk.Frame(info, style="Dark.TFrame")
        bf.pack(fill=tk.X, pady=8)
        ttk.Button(bf, text="OK", command=self._mark_ok).pack(
            side=tk.LEFT, padx=2)
        ttk.Button(bf, text="Problem", command=self._mark_problem).pack(
            side=tk.LEFT, padx=2)
        ttk.Button(bf, text="Reset", command=self._mark_reset).pack(
            side=tk.LEFT, padx=2)
        ttk.Button(bf, text="Crop-Reset", command=self._reset_crop).pack(
            side=tk.LEFT, padx=2)
        ttk.Button(bf, text="Speichern", command=self._save).pack(
            side=tk.RIGHT, padx=2)
        ttk.Button(bf, text="Export", command=self._export).pack(
            side=tk.RIGHT, padx=2)
        hint = ttk.Label(info,
                         text="\u2190 Crop: Ecke/Seite ziehen",
                         style="St.TLabel")
        hint.pack(anchor=tk.W, pady=(4, 0))
        # bottom nav
        bot = ttk.Frame(self.root, style="Dark.TFrame")
        bot.pack(fill=tk.X, padx=10, pady=5)
        ttk.Button(bot, text="|<", command=self._first, width=3).pack(
            side=tk.LEFT)
        ttk.Button(bot, text="<", command=self._prev, width=3).pack(
            side=tk.LEFT, padx=2)
        ttk.Button(bot, text=">", command=self._next, width=3).pack(
            side=tk.LEFT, padx=2)
        ttk.Button(bot, text=">|", command=self._last, width=3).pack(
            side=tk.LEFT)
        self.pos_nav = ttk.Label(bot, text="", style="Dark.TLabel")
        self.pos_nav.pack(side=tk.LEFT, padx=15)
        self.slider = ttk.Scale(bot, from_=0,
                                to=max(len(self.filtered) - 1, 0),
                                orient=tk.HORIZONTAL,
                                command=self._on_slider)
        self.slider.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=10)
        # keyboard
        self.root.bind("<Left>", lambda e: self._prev())
        self.root.bind("<Right>", lambda e: self._next())
        self.root.bind("<Home>", lambda e: self._first())
        self.root.bind("<End>", lambda e: self._last())
        self.root.bind("<o>", lambda e: self._mark_ok())
        self.root.bind("<p>", lambda e: self._mark_problem())
        self.root.bind("<r>", lambda e: self._mark_reset())
        self.root.bind("<Control-s>", lambda e: self._save())
        for i in range(1, 10):
            self.root.bind(str(i), lambda e, n=i: self._jump(n * 10))
        # canvas drag events
        self.canvas.bind("<ButtonPress-1>", self._on_press)
        self.canvas.bind("<B1-Motion>", self._on_drag)
        self.canvas.bind("<ButtonRelease-1>", self._on_release)
        self.canvas.bind("<Motion>", self._on_motion)
        # Bei Canvas-Resize neu zeichnen (debounced)
        self.canvas.bind("<Configure>", self._on_canvas_resize)

    # ─── Filter / Navigation ─────────────────────────────────────
    def _apply_filter(self):
        filt = self.filter_var.get()
        film = self.film_var.get()
        self.filtered = []
        for i, r in enumerate(self.results):
            key = self._key(r)
            rev = self.reviews.get(key, {})
            is_problem = rev.get("is_problem", False)
            needs_rev = r.get("needs_review", False) or is_problem
            if filt == "review" and not needs_rev:
                continue
            if filt == "ok" and needs_rev:
                continue
            if filt == "problem" and not is_problem:
                continue
            if film != "Alle" and r.get("film", "") != film:
                continue
            self.filtered.append(i)
        self.current_idx = 0
        self.slider.configure(to=max(len(self.filtered) - 1, 0))
        self._update_stats()
        self._update_image()

    def _update_stats(self):
        total = len(self.results)
        problems = sum(1 for r in self.results
                       if self.reviews.get(self._key(r), {}).get(
                           "is_problem", False))
        ok = total - problems
        self.stats_lbl.configure(
            text=f"Angezeigt: {len(self.filtered)}/{total} | "
                 f"OK: {ok} | Probleme: {problems}")

    def _nav(self, delta):
        if not self.filtered:
            return
        self.current_idx = max(0, min(self.current_idx + delta,
                                      len(self.filtered) - 1))
        self._update_image()

    def _next(self):
        self._nav(1)

    def _prev(self):
        self._nav(-1)

    def _first(self):
        self.current_idx = 0
        self._update_image()

    def _last(self):
        self.current_idx = max(len(self.filtered) - 1, 0)
        self._update_image()

    def _jump(self, n):
        self.current_idx = max(0, min(self.current_idx + n,
                                      len(self.filtered) - 1))
        self._update_image()

    def _on_slider(self, val):
        if self._updating:
            return
        self.current_idx = int(float(val))
        self._update_image()

    def _cur_key(self):
        if not self.filtered:
            return None
        return self._key(self.results[self.filtered[self.current_idx]])

    # ─── Bildanzeige ─────────────────────────────────────────────
    def _update_image(self):
        self._updating = True
        try:
            self._update_image_impl()
        finally:
            self._updating = False

    def _update_image_impl(self):
        self.canvas.delete("all")
        self._rect_id = None
        self._handle_ids = []
        self._label_id = None
        self._diff_id = None
        self._orig_img = None
        if not self.filtered:
            self.canvas.create_text(400, 300, text="Keine Bilder",
                                    fill="#666", font=("Segoe UI", 16))
            return
        idx = min(self.current_idx, len(self.filtered) - 1)
        r = self.results[self.filtered[idx]]
        self.slider.set(idx)
        self.pos_nav.configure(text=f"{idx + 1} / {len(self.filtered)}")
        path = r.get("full_path", r.get("input_file", ""))
        if not os.path.exists(path):
            return
        try:
            self._orig_img = Image.open(path)
        except Exception:
            return
        self._img_w = self._orig_img.width
        self._img_h = self._orig_img.height

        # Crop initialisieren
        key = self._key(r)
        rev = self.reviews.get(key, {})
        self._auto_crop = {"x": r["x"], "y": r["y"],
                           "w": r["width"], "h": r["height"]}
        mc = rev.get("manual_crop")
        if mc:
            self._crop = {"x": mc["x"], "y": mc["y"],
                          "w": mc["width"], "h": mc["height"]}
        else:
            self._crop = dict(self._auto_crop)

        self._render_image()
        self._update_info_panel(r, idx)

    def _render_image(self):
        """Skaliert das gecachte Bild auf die Canvas-Groesse und zeichnet
        das Crop-Rechteck.  Wird sowohl beim initialen Laden als auch
        beim Resize aufgerufen."""
        self.canvas.delete("all")
        self._rect_id = None
        self._handle_ids = []
        self._label_id = None
        self._diff_id = None
        if self._orig_img is None:
            return
        img = self._orig_img
        self.root.update_idletasks()
        cw = max(self.canvas.winfo_width(), 100)
        ch = max(self.canvas.winfo_height(), 100)
        # Kein 1.0-Cap — Bild darf aufgegroessert werden
        self._scale = min(cw / img.width, ch / img.height)
        nw, nh = int(img.width * self._scale), int(img.height * self._scale)
        self._ox = (cw - nw) // 2
        self._oy = (ch - nh) // 2

        disp = img.resize((nw, nh), Image.LANCZOS)
        self._bg_photo = ImageTk.PhotoImage(disp)
        self._canvas_bg = self.canvas.create_image(
            self._ox, self._oy, anchor=tk.NW, image=self._bg_photo)
        self._draw_crop()

    def _on_canvas_resize(self, event):
        """Debounced Resize: 80ms warten, dann Canvas neu zeichnen."""
        if self._resize_after_id is not None:
            self.root.after_cancel(self._resize_after_id)
        self._resize_after_id = self.root.after(80,
                                                self._on_canvas_resize_impl)

    def _on_canvas_resize_impl(self):
        self._resize_after_id = None
        if self._orig_img is not None:
            self._render_image()

    def _draw_crop(self):
        """Zeichnet das Crop-Rechteck und Handles auf dem Canvas."""
        # Alte Items entfernen
        if self._rect_id:
            self.canvas.delete(self._rect_id)
        for hid in self._handle_ids:
            self.canvas.delete(hid)
        self._handle_ids = []
        if self._label_id:
            self.canvas.delete(self._label_id)
        if self._diff_id:
            self.canvas.delete(self._diff_id)

        s = self._scale
        ox, oy = self._ox, self._oy
        c = self._crop
        rx = int(c["x"] * s + ox)
        ry = int(c["y"] * s + oy)
        rw = int(c["w"] * s)
        rh = int(c["h"] * s)

        # Farbe: Gruen wenn gleich auto, Orange wenn geaendert
        is_modified = (self._crop != self._auto_crop)
        color = "#ffaa00" if is_modified else "#44ff44"

        # Ausgeblendeter auto-Crop (gestrichelt, grau)
        if is_modified:
            ac = self._auto_crop
            arx = int(ac["x"] * s + ox)
            ary = int(ac["y"] * s + oy)
            arw = int(ac["w"] * s)
            arh = int(ac["h"] * s)
            self._diff_id = self.canvas.create_rectangle(
                arx, ary, arx + arw, ary + arh,
                outline="#666666", width=1, dash=(6, 4))

        # Crop-Rechteck
        self._rect_id = self.canvas.create_rectangle(
            rx, ry, rx + rw, ry + rh,
            outline=color, width=3)

        # Corner-Handles
        for cx, cy in [(rx, ry), (rx + rw, ry),
                       (rx, ry + rh), (rx + rw, ry + rh)]:
            hid = self.canvas.create_rectangle(
                cx - HANDLE_R, cy - HANDLE_R,
                cx + HANDLE_R, cy + HANDLE_R,
                fill=color, outline="#000", width=1)
            self._handle_ids.append(hid)

        # Label
        tk_font = ("DejaVu Sans", 12, "bold")
        label = f"{c['w']}x{c['h']}"
        if is_modified:
            label = f"MANUELL: {label}"
        self._label_id = self.canvas.create_text(
            rx, ry - 10, text=label, fill=color, font=tk_font, anchor=tk.SW)

    def _update_info_panel(self, r, idx):
        self.title_lbl.configure(
            text=f"{r.get('film', '')} / {r.get('filename', '')}")
        conf = r["confidence"]
        self._ilbl["conf"].configure(
            text=f"{conf:.3f}",
            style="Ok.TLabel" if conf >= 0.9 else "Pr.TLabel")
        self._ilbl["meth"].configure(text=r.get("method", "?"))
        self._ilbl["ori"].configure(text=r.get("orientation", "?"))
        aspect = r["width"] / max(r["height"], 1)
        self._ilbl["rat"].configure(
            text=f"{aspect:.3f}  (Ziel: {r.get('target_aspect_ratio', 1.5):.3f})")
        try:
            orig = Image.open(r.get("full_path", ""))
            pct = r["width"] * r["height"] / (orig.width * orig.height) * 100
        except Exception:
            pct = 0
        self._ilbl["area"].configure(
            text=f"{r['width']}x{r['height']}  ({pct:.1f}%)")
        self._ilbl["pos"].configure(text=f"{r['x']}, {r['y']}")
        # Manueller Crop
        mc = self._crop
        is_mod = (self._crop != self._auto_crop)
        if is_mod:
            mpct = mc["w"] * mc["h"] / (self._img_w * self._img_h) * 100
            m_aspect = mc["w"] / max(mc["h"], 1)
            self._ilbl["marea"].configure(
                text=f"{mc['w']}x{mc['h']} ({mpct:.1f}%) r={m_aspect:.3f}",
                style="Mod.TLabel")
        else:
            self._ilbl["marea"].configure(
                text="(wie automatisch)", style="Dark.TLabel")
        # Gruende
        self.reasons.configure(state=tk.NORMAL)
        self.reasons.delete("1.0", tk.END)
        for reason in r.get("reasons", []):
            self.reasons.insert(tk.END, f"  {reason}\n")
        self.reasons.configure(state=tk.DISABLED)
        rev2 = self.reviews.get(self._key(r), {})
        if rev2.get("is_problem"):
            self.rev_lbl.configure(text="PROBLEMATISCH", style="Pr.TLabel")
        elif r.get("needs_review"):
            self.rev_lbl.configure(text="Review noetig", style="Pr.TLabel")
        else:
            self.rev_lbl.configure(text="OK", style="Ok.TLabel")
        self.note_var.set(rev2.get("note", ""))

    # ─── Crop-Drag Events ────────────────────────────────────────
    def _canvas_to_img(self, cx, cy):
        """Canvas-Koordinaten -> Bild-Koordinaten."""
        ix = (cx - self._ox) / self._scale
        iy = (cy - self._oy) / self._scale
        return ix, iy

    def _on_motion(self, event):
        """Cursor anpassen wenn nahe an einer Kante."""
        if self._drag:
            return
        if not self._crop:
            return
        c = self._crop
        s = self._scale
        rx = c["x"] * s + self._ox
        ry = c["y"] * s + self._oy
        rw = c["w"] * s
        rh = c["h"] * s
        edge = _edge_at(event.x, event.y, rx, ry, rw, rh, self._edge_zone)
        cursor = _cursor_for_edge(edge) if edge else ""
        self.canvas.configure(cursor=cursor)

    def _on_press(self, event):
        """Drag starten wenn nahe an einer Kante."""
        if not self._crop:
            return
        c = self._crop
        s = self._scale
        rx = c["x"] * s + self._ox
        ry = c["y"] * s + self._oy
        rw = c["w"] * s
        rh = c["h"] * s
        edge = _edge_at(event.x, event.y, rx, ry, rw, rh, self._edge_zone)
        if edge:
            self._drag = {
                "edge": edge,
                "start_cx": event.x,
                "start_cy": event.y,
                "start_crop": dict(self._crop),
            }

    def _on_drag(self, event):
        """Crop-Rectangle waehrend Drag aktualisieren."""
        if not self._drag:
            return
        d = self._drag
        edge = d["edge"]
        sc = d["start_crop"]
        # Mausdelta in Bild-Koordinaten
        dx = (event.x - d["start_cx"]) / self._scale
        dy = (event.y - d["start_cy"]) / self._scale

        # Crop basierend auf gezogener Kante aktualisieren
        x, y, w, h = sc["x"], sc["y"], sc["w"], sc["h"]
        min_size = 50  # Minimale Crop-Groesse

        if "w" in edge:  # linke Seite
            new_x = max(0, min(x + dx, x + w - min_size))
            new_w = w - (new_x - x)
            x, w = new_x, new_w
        if "e" in edge:  # rechte Seite
            new_w = max(min_size, min(w + dx, self._img_w - x))
            w = new_w
        if "n" in edge:  # obere Seite
            new_y = max(0, min(y + dy, y + h - min_size))
            new_h = h - (new_y - y)
            y, h = new_y, new_h
        if "s" in edge:  # untere Seite
            new_h = max(min_size, min(h + dy, self._img_h - y))
            h = new_h

        self._crop = {"x": int(x), "y": int(y),
                      "w": int(w), "h": int(h)}
        self._draw_crop()

        # Info-Panel aktualisieren
        is_mod = (self._crop != self._auto_crop)
        mc = self._crop
        mpct = mc["w"] * mc["h"] / (self._img_w * self._img_h) * 100
        m_aspect = mc["w"] / max(mc["h"], 1)
        self._ilbl["marea"].configure(
            text=f"{mc['w']}x{mc['h']} ({mpct:.1f}%) r={m_aspect:.3f}" +
                 (" *" if is_mod else ""),
            style="Mod.TLabel" if is_mod else "Dark.TLabel")

    def _on_release(self, event):
        """Drag beenden."""
        self._drag = None

    def _reset_crop(self):
        """Crop auf automatisch erkannten Wert zuruecksetzen."""
        self._crop = dict(self._auto_crop)
        self._draw_crop()
        mc = self._crop
        mpct = mc["w"] * mc["h"] / (self._img_w * self._img_h) * 100
        self._ilbl["marea"].configure(
            text="(wie automatisch)", style="Dark.TLabel")

    # ─── Bewertung ───────────────────────────────────────────────
    def _save_manual_crop(self):
        """Speichert den manuellen Crop in den Reviews."""
        key = self._cur_key()
        if not key:
            return
        if self._crop != self._auto_crop:
            rev = self.reviews.get(key, {})
            rev["manual_crop"] = {
                "x": self._crop["x"], "y": self._crop["y"],
                "width": self._crop["w"], "height": self._crop["h"],
            }
            self.reviews[key] = rev

    def _mark_ok(self):
        key = self._cur_key()
        if not key:
            return
        self._save_manual_crop()
        rev = self.reviews.get(key, {})
        rev["is_problem"] = False
        rev["note"] = self.note_var.get()
        self.reviews[key] = rev
        for r in self.results:
            if self._key(r) == key:
                r["needs_review"] = False
        self._update_stats()
        self._next()

    def _mark_problem(self):
        key = self._cur_key()
        if not key:
            return
        self._save_manual_crop()
        rev = self.reviews.get(key, {})
        rev["is_problem"] = True
        rev["note"] = self.note_var.get()
        self.reviews[key] = rev
        self._update_stats()
        self._update_image()

    def _mark_reset(self):
        key = self._cur_key()
        if not key:
            return
        self.reviews.pop(key, None)
        self._crop = dict(self._auto_crop)
        self._draw_crop()
        for r in self.results:
            if self._key(r) == key:
                r["needs_review"] = r.get("confidence", 0) < 0.7
        self.note_var.set("")
        self._ilbl["marea"].configure(
            text="(wie automatisch)", style="Dark.TLabel")
        self._update_stats()
        self._update_image()

    def _save(self):
        # Aktuellen Crop auch speichern
        if self.filtered and self._crop != self._auto_crop:
            self._save_manual_crop()
        save_reviews(self.reviews)
        self.stats_lbl.configure(
            text=f"Gespeichert! ({len(self.reviews)} Bewertungen)")

    def _export(self):
        lines = ["# Auto-Crop-Negative Review\n",
                 f"Gesamt: {len(self.results)} Bilder\n\n",
                 "| Bild | Conf | Methode | Orient | Status | Manual-Crop | Notiz |\n",
                 "|------|------|---------|--------|--------|-------------|-------|\n"]
        cnt = 0
        for r in self.results:
            key = self._key(r)
            rev = self.reviews.get(key, {})
            if rev.get("is_problem") or rev.get("note") or \
               rev.get("manual_crop"):
                status = "Problem" if rev.get("is_problem") else (
                    "Notiz" if rev.get("note") else "Crop-Adj")
                mc = rev.get("manual_crop")
                mc_str = f"{mc['width']}x{mc['height']}" if mc else "-"
                lines.append(
                    f"| {key} | {r['confidence']:.3f} | "
                    f"{r['method']} | {r.get('orientation','')} | "
                    f"{status} | {mc_str} | {rev.get('note','')} |\n")
                cnt += 1
        if cnt == 0:
            messagebox.showinfo("Export", "Keine Probleme/Notizen/Crops vorhanden.")
            return
        path = os.path.join(DATA_DIR, "review_export.md")
        with open(path, "w") as f:
            f.writelines(lines)
        messagebox.showinfo("Export", f"Exportiert nach:\n{path}")


def main():
    if not os.path.exists(RESULTS_FILE):
        msg = (f"Keine Analyse-Daten gefunden:\n  {RESULTS_FILE}\n\n"
               "Starte das Projekt mit:\n"
               "  ./start_review_gui.sh\n"
               "Dies erstellt die Daten bei Bedarf automatisch\n"
               "(oder manuell: .venv/bin/python batch_test.py)")
        print(f"FEHLER: {RESULTS_FILE} nicht gefunden.\n"
              "Starte ./start_review_gui.sh oder "
              ".venv/bin/python batch_test.py", flush=True)
        root = tk.Tk()
        root.withdraw()
        messagebox.showerror("Auto-Crop-Negative", msg)
        root.destroy()
        sys.exit(1)
    root = tk.Tk()
    ReviewGUI(root)
    root.mainloop()


if __name__ == "__main__":
    main()
