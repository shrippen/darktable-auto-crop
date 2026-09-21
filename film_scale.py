"""Abbildungsmassstab (px/mm) aus dem Perforationsraster.

35-mm-Perforation hat seit der Normung einen Lochabstand von 4,7625 mm (0,1875 in). Der Takt ist
eine Konstante des Films, unabhaengig vom Bildinhalt. Gemessen wird er nicht ueber einzelne Loecher,
sondern ueber die Periodizitaet der Randstreifen: Autokorrelation je Streifenlage, ueber alle Bilder
einer Rolle aufsummiert (der Film liegt in allen Scans einer Rolle an derselben Stelle, der Takt
addiert sich kohaerent, Bildinhalt nicht).

Aus dem Pitch folgen Rahmengroesse (36 x 24 mm) und ein Massstab, mit dem sich Rollen vergleichen
lassen, ohne Pixelwerte zu poolen.
"""
import cv2
import numpy as np

PITCH_MM = 4.7625
FRAME_LONG_MM = 36.0
FRAME_SHORT_MM = 24.0
# Plausible Pitch-Spanne relativ zur langen Bildkante (Rahmen fuellt 0,80-0,95 der Datei)
PITCH_REL = (0.070, 0.135)
MIN_SCORE = 0.30


def _highpass(v, k):
    """Bewegten Mittelwert abziehen (entfernt Helligkeitsverlauf und Bildinhalt niedriger Frequenz)."""
    k = max(3, int(k) | 1)
    ker = np.ones(k, np.float32) / k
    return v - np.convolve(v, ker, mode="same")


WIN_FRAC = 0.008       # Fensterhoehe relativ zur kurzen Bildkante (~10 px bei 1333 px)
EDGE_FRAC = 0.24       # nur die aeusseren 24 % je Seite (dort liegt die Perforation)


def _window_starts(h, win_h):
    step = max(2, win_h // 2)
    lim = int(h * EDGE_FRAC)
    a = list(range(0, max(lim - win_h, 1), step))
    b = list(range(max(h - lim, 0), h - win_h, step))
    return a + b


def _window_acs(gray, n_win=24, lag_lo=None, lag_hi=None):
    """Autokorrelation je Streifenlage. gray wird so gelegt, dass der Transport waagerecht laeuft.

    Rueckgabe: (n_win, lags) normierte ACs, Lag-Startwert.
    """
    if gray.shape[0] > gray.shape[1]:
        gray = gray.T
    h, w = gray.shape
    lo = int(lag_lo if lag_lo else w * PITCH_REL[0])
    hi = int(lag_hi if lag_hi else w * PITCH_REL[1])
    win_h = max(4, int(round(h * WIN_FRAC)))
    starts = _window_starts(h, win_h)
    n_win = len(starts)
    out = np.zeros((n_win, 2 * hi + 4 - lo), np.float32)
    for i, y0 in enumerate(starts):
        y1 = y0 + win_h
        prof = gray[y0:y1, :].astype(np.float32).mean(axis=0)
        prof = _highpass(prof, w * 0.12)
        prof = prof[int(w * 0.03):-int(w * 0.03)]  # Randartefakte des Filters
        prof = prof - prof.mean()
        e = float(np.dot(prof, prof))
        if e < 1e-3:
            continue
        n = len(prof)
        f = np.fft.rfft(prof, 2 * n)
        ac = np.fft.irfft(f * np.conj(f))[:n]
        # unverzerrt: mit der Zahl der Ueberlappungen normieren
        ac = ac / (n - np.arange(n)) * n / e
        out[i] = ac[lo:2 * hi + 4]
    return out, lo, w, hi


def _peak(ac, lo):
    """Staerkster echter lokaler Peak, Subpixel per Parabel. -> (lag, hoehe) oder (None, 0)."""
    if len(ac) < 5:
        return None, 0.0
    best, bi = 0.0, -1
    for i in range(2, len(ac) - 2):
        if ac[i] > ac[i - 1] and ac[i] >= ac[i + 1] and ac[i] > ac[i - 2] and ac[i] >= ac[i + 2] and ac[i] > best:
            best, bi = float(ac[i]), i
    if bi < 0:
        return None, 0.0
    y0, y1, y2 = ac[bi - 1], ac[bi], ac[bi + 1]
    d = y0 - 2 * y1 + y2
    off = 0.5 * (y0 - y2) / d if abs(d) > 1e-9 else 0.0
    return lo + bi + off, best


def measure_roll_pitch(grays, n_win=24):
    """Pitch in Pixeln aus den Graubildern EINER Rolle (alle in gleicher Aufloesung).

    Liefert {"pitch": px|None, "score": 0..1, "win": Streifenlage 0..1, "n": Bilder}.
    Je Streifenlage zaehlt der staerkste Peak (Pruefung: der zweite Oberton bei 2*Pitch muss ebenfalls vorhanden sein).
    Peaks mit aehnlichem Pitch aus verschiedenen Streifenlagen werden zu einem Buendel zusammengefasst; das Buendel mit der
    groessten Score-Summe gilt. Score = hoechster Einzelwert im Buendel. Ein einzelner starker Peak in einer Streifenlage
    (Randartefakt) entscheidet damit nicht allein.
    """
    acc, lo, w, hi, n = None, None, None, None, 0
    for g in grays:
        if g is None or min(g.shape) < 200:
            continue
        acs, lo_i, w_i, hi_i = _window_acs(g, n_win)
        if acc is None:
            acc, lo, w, hi = acs.copy(), lo_i, w_i, hi_i
        elif acs.shape == acc.shape:
            acc += acs
        else:
            continue
        n += 1
    if acc is None or n == 0:
        return {"pitch": None, "pitch_frac": None, "score": 0.0, "win": None, "n": 0}
    acc /= n
    # Konsens ueber Streifenlagen: Peaks mit aehnlichem Pitch buendeln, das Buendel mit der groessten Score-Summe gewinnt
    # (ein einzelner starker Peak in einer Streifenlage, z. B. am aeussersten Bildrand, entscheidet nicht allein)
    pk = []
    for i in range(acc.shape[0]):
        p, sc = _peak(acc[i][:hi - lo], lo)
        if p is None:
            continue
        sc2 = _value_at(acc[i], lo, 2 * p)
        sc = min(sc, max(sc2 if sc2 is not None else 0.0, 0.0) * 1.6)
        if sc >= 0.15:
            pk.append((p, sc, i / max(acc.shape[0] - 1, 1)))
    best = (None, 0.0, None)
    top = None
    for p0, _, _ in pk:
        cl = [c for c in pk if abs(c[0] - p0) < 0.004 * w]
        tot = sum(c[1] for c in cl)
        if top is None or tot > top[0]:
            top = (tot, cl)
    if top:
        cl = top[1]
        pm = sum(c[0] * c[1] for c in cl) / sum(c[1] for c in cl)
        mx = max(cl, key=lambda c: c[1])
        best = (pm, mx[1], mx[2])
    pitch = None if best[0] is None else float(best[0])
    return {"pitch": pitch, "pitch_frac": None if pitch is None else pitch / w,
            "score": round(best[1], 4), "win": best[2], "n": n, "width": int(w)}


def _value_at(ac, lo, lag):
    i = int(round(lag - lo))
    return float(ac[i]) if 0 <= i < len(ac) else None


def load_gray(path, max_side=2000):
    g = cv2.imread(path, cv2.IMREAD_GRAYSCALE)
    if g is None:
        return None
    s = max_side / max(g.shape)
    if s < 1:
        g = cv2.resize(g, None, fx=s, fy=s, interpolation=cv2.INTER_AREA)
    return g


# ─── Konvention: gewuenschte Crop-Groesse in mm ─────────────────────────────────────────────────────────────────
# Die Physik liefert den Rahmen (36 x 24 mm); wie eng oder weit man ihn schneidet, ist Geschmack (gemessen: darktable-
# Crops 35.99 x 23.95 mm, Handcrops 36.81 x 24.40 mm). Die Web-UI lernt den Wert aus den von Hand gesetzten Crops und legt
# ihn hier ab; die Erkennung liest ihn als Voreinstellung. Env AUTOCROP_CONVENTION: Pfad, oder leer = ausgeschaltet.
import json as _json
import os as _os


def convention_path():
    env = _os.environ.get("AUTOCROP_CONVENTION")
    if env is not None:
        return env or None
    base = _os.environ.get("XDG_CONFIG_HOME") or _os.path.expanduser("~/.config")
    return _os.path.join(base, "auto-crop-negative", "convention.json")


def load_convention(path=None):
    """{"long_mm", "short_mm", "n"} oder None (fehlt / ungueltig / ausserhalb 33-40 x 22-27 mm)."""
    path = path or convention_path()
    if not path:
        return None
    try:
        with open(path, encoding="utf-8") as f:
            c = _json.load(f)
        if 33.0 <= c["long_mm"] <= 40.0 and 22.0 <= c["short_mm"] <= 27.0:
            return {"long_mm": float(c["long_mm"]), "short_mm": float(c["short_mm"]), "n": int(c.get("n", 1))}
    except (OSError, ValueError, KeyError, TypeError):
        pass
    return None


def update_convention(new, path=None, max_prior=60):
    """Neuen Sitzungswert mit dem gespeicherten mitteln (nach Anzahl gewichtet, alter Wert zaehlt hoechstens max_prior)."""
    path = path or convention_path()
    if not path or not new:
        return None
    old = load_convention(path)
    if old:
        n0 = min(old["n"], max_prior)
        n1 = new["n"]
        merged = {"long_mm": round((old["long_mm"] * n0 + new["long_mm"] * n1) / (n0 + n1), 2),
                  "short_mm": round((old["short_mm"] * n0 + new["short_mm"] * n1) / (n0 + n1), 2),
                  "n": n0 + n1}
    else:
        merged = dict(new)
    try:
        _os.makedirs(_os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            _json.dump(merged, f)
        _os.replace(tmp, path)
    except OSError:
        return None
    return merged
