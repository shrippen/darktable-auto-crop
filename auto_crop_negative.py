#!/usr/bin/env python3
"""
Auto Crop Negative – Python-Backend
Erkennt automatisch den Filmrahmen auf einer digitalen Ablichtung
und liefert die Crop-Koordinaten als JSON aus.
"""

import argparse
import glob
import json
import shutil
import subprocess
import sys
import os
import math
import tempfile
from concurrent.futures import ProcessPoolExecutor, as_completed

import cv2
import numpy as np

ASPECT_RATIOS = {
    "35mm": 3 / 2,
    "6x6": 1 / 1,
    "6x4.5": 4.5 / 6,
    "6x7": 7 / 6,
    "6x9": 9 / 6,
}
DEFAULT_FORMAT = "35mm"


def load_image(path: str) -> np.ndarray:
    img = cv2.imread(path, cv2.IMREAD_COLOR)
    if img is None:
        raise FileNotFoundError(
            f"Bild konnte nicht geladen werden: {path} "
            f"(RAW-Formate werden vorher via darktable-cli konvertiert)")
    return img


RAW_EXTENSIONS = {
    ".nef", ".cr2", ".cr3", ".arw", ".raf", ".orf", ".rw2", ".dng",
    ".pef", ".srw", ".erf", ".kdc", ".3fr", ".mos", ".iiq", ".x3f",
}

# Erkennung laeuft auf verkleinerten Kopien (getesteter Bereich ~1-2 MP),
# Koordinaten werden exakt auf die Aufloesung des Originals zurueckgerechnet
DETECT_MAX_PIXELS = 2_600_000


def find_darktable_cli():
    """Pfad zu darktable-cli (RAW-Entwicklung mit darktable selbst)."""
    exe = shutil.which("darktable-cli")
    if exe:
        return exe
    for cand in ("/usr/bin/darktable-cli", "/usr/local/bin/darktable-cli"):
        if os.path.exists(cand):
            return cand
    return None


def export_raws_via_darktable(dt_cli, raw_paths, export_dir, total_units):
    """Konvertiert RAW-Bilder mit darktable-cli in JPEGs (lesbar fuer cv2).

    darktable-cli wendet vorhandene XMP-Entwicklungen an, das exportierte
    Bild entspricht damit dem Stand in darktable. Jeder Aufruf bekommt eine
    eigene temporaere Library und kollidiert nicht mit der laufenden
    darktable-Instanz. Liefert Mapping {original: export_jpeg} zurueck.
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed

    def convert(path):
        base = os.path.splitext(os.path.basename(path))[0]
        out = os.path.join(export_dir, base + "_conv.jpg")
        cfg = out + ".cfg"  # eigenes Config-Verzeichnis -> eigene library.db,
        env = dict(os.environ)  # kollidiert nicht mit der laufenden GUI
        env["XDG_CONFIG_HOME"] = cfg
        # WICHTIG: KEIN --library hier. --library laesst darktable-cli laut
        # "--help" die History aus der (hier leeren, frisch erzeugten)
        # Library-DB lesen STATT aus dem XMP-Sidecar - das Bild wird dann mit
        # dem darktable-Standard-Modulstapel importiert, nicht mit dem
        # tatsaechlichen Bearbeitungsstand (z.B. Negadoctor). Schlimmer: die
        # lokale darktablerc hat write_sidecar_files=on import, d.h. der
        # Import in die frische Library loest einen Sidecar-Schreibvorgang
        # aus, der genau diesen (falschen) Standard-Stapel in die ECHTE
        # <raw>.xmp neben der Originaldatei zurueckschreibt und damit
        # vorhandene manuelle Bearbeitungen ueberschreibt. Ohne --library
        # liest darktable-cli stattdessen ganz normal die vorhandene
        # Sidecar-XMP (das war ohnehin die Absicht laut Docstring oben).
        # --conf write_sidecar_files=never verhindert zusaetzlich JEDEN
        # Schreibvorgang durch diesen Wegwerf-Export, als Sicherheitsnetz
        # falls doch einmal ein "Import" in der isolierten Library passiert.
        cmd = [dt_cli, path, out,
               "--out-ext", "jpg",
               "--core", "--conf", "write_sidecar_files=never"]
        proc = subprocess.run(cmd, stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, text=True, env=env)
        if not os.path.exists(out):
            # darktable-cli haengt bei manchen Konfigurationen einen Suffix an
            cands = sorted(glob.glob(os.path.join(
                export_dir, base + "_conv*.jpg")))
            if cands:
                return path, cands[0]
            raise RuntimeError(
                f"darktable-cli Export fehlgeschlagen ({proc.returncode}): "
                f"{(proc.stderr or proc.stdout).strip()[-200:]}")
        return path, out

    mapping = {}
    done = 0
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = {pool.submit(convert, p): p for p in raw_paths}
        for fut in as_completed(futures):
            path = futures[fut]
            try:
                orig, conv = fut.result()
                mapping[orig] = conv
            except Exception as e:
                print(f"WARN: {path}: {e}", file=sys.stderr, flush=True)
            done += 1
            print(f"PROGRESS {done}/{total_units}",
                  file=sys.stderr, flush=True)
    return mapping


def _prepare_detect_gray(img):
    """Graustufen + verkleinerte Kopie fuer die Rahmenerkennung.
    Liefert (full_gray, small_gray, scale) mit scale = small/full."""
    full = to_gray(img)
    h, w = full.shape[:2]
    if h * w <= DETECT_MAX_PIXELS:
        return full, full, 1.0
    scale = math.sqrt(DETECT_MAX_PIXELS / float(h * w))
    small = cv2.resize(full, None, fx=scale, fy=scale,
                       interpolation=cv2.INTER_AREA)
    return full, small, scale


def to_gray(img: np.ndarray) -> np.ndarray:
    if len(img.shape) == 2:
        return img
    return cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)


def enhance_contrast(gray: np.ndarray, clip_limit: float = 3.0) -> np.ndarray:
    clahe = cv2.createCLAHE(clipLimit=clip_limit, tileGridSize=(8, 8))
    return clahe.apply(gray)


def order_points(pts: np.ndarray) -> np.ndarray:
    rect = np.zeros((4, 2), dtype=float)
    s = pts.sum(axis=1)
    d = np.diff(pts, axis=1).flatten()
    rect[0] = pts[np.argmin(s)]
    rect[2] = pts[np.argmax(s)]
    rect[1] = pts[np.argmin(d)]
    rect[3] = pts[np.argmax(d)]
    return rect


def bounding_rect_from_points(rect: np.ndarray):
    x = min(rect[:, 0])
    y = min(rect[:, 1])
    w = max(rect[:, 0]) - x
    h = max(rect[:, 1]) - y
    return (x, y, w, h)


def cluster_lines(lines, axis="y", threshold=20):
    if not lines:
        return []
    values = [((l[0] + l[1]) / 2, l) for l in lines]
    values.sort(key=lambda v: v[0])
    groups = [[values[0]]]
    for v in values[1:]:
        if v[0] - groups[-1][-1][0] < threshold:
            groups[-1].append(v)
        else:
            groups.append([v])
    return sorted([sum(g[0] for g in grp) / len(grp) for grp in groups])


# ─── Strategie 1: Konturen ───────────────────────────────────────────────────

def detect_by_contours(gray: np.ndarray) -> list[dict]:
    candidates = []
    enhanced = enhance_contrast(gray, clip_limit=4.0)

    for method_name, proc_img in [
        ("otsu", cv2.threshold(enhanced, 0, 255,
                               cv2.THRESH_BINARY + cv2.THRESH_OTSU)[1]),
        ("adaptive_mean", cv2.adaptiveThreshold(
            enhanced, 255, cv2.ADAPTIVE_THRESH_MEAN_C,
            cv2.THRESH_BINARY, 51, 10)),
        ("adaptive_gauss", cv2.adaptiveThreshold(
            enhanced, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
            cv2.THRESH_BINARY, 51, 10)),
    ]:
        kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (7, 7))
        closed = cv2.morphologyEx(proc_img, cv2.MORPH_CLOSE, kernel, iterations=3)
        closed = cv2.morphologyEx(closed, cv2.MORPH_OPEN, kernel, iterations=2)

        contours, _ = cv2.findContours(
            closed, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

        for cnt in contours:
            area = cv2.contourArea(cnt)
            if area < (gray.shape[0] * gray.shape[1] * 0.05):
                continue
            peri = cv2.arcLength(cnt, True)
            approx = cv2.approxPolyDP(cnt, 0.02 * peri, True)
            if len(approx) == 4:
                pts = approx.reshape(4, 2).astype(float)
                rect = order_points(pts)
                (x, y, w, h) = bounding_rect_from_points(rect)
                candidates.append({
                    "x": int(x), "y": int(y),
                    "width": int(w), "height": int(h),
                    "area": float(w * h),
                    "method": f"contour_{method_name}",
                    "contour_area": float(area),
                    "num_vertices": len(approx),
                })
    return candidates


# ─── Strategie 2: Canny + Hough ──────────────────────────────────────────────

def detect_by_canny_hough(gray: np.ndarray) -> list[dict]:
    candidates = []
    h, w = gray.shape
    enhanced = enhance_contrast(gray, clip_limit=3.0)

    for low, high in [(30, 90), (50, 150), (20, 60), (80, 200)]:
        edges = cv2.Canny(enhanced, low, high)
        kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3))
        edges = cv2.dilate(edges, kernel, iterations=1)

        lines = cv2.HoughLinesP(edges, 1, np.pi / 180, threshold=100,
                                minLineLength=min(h, w) // 4, maxLineGap=20)
        if lines is None:
            continue

        horizontal, vertical = [], []
        for line in lines:
            # OpenCV 5.x: shape (N, 4) direkt, nicht (N, 1, 4)
            coords = line if line.ndim == 1 else line[0]
            x1, y1, x2, y2 = [int(v) for v in coords]
            angle = abs(math.atan2(y2 - y1, x2 - x1) * 180 / math.pi)
            length = math.sqrt((x2 - x1) ** 2 + (y2 - y1) ** 2)
            if angle < 10 or angle > 170:
                horizontal.append((min(y1, y2), max(y1, y2),
                                   min(x1, x2), max(x1, x2), length))
            elif 80 < angle < 100:
                vertical.append((min(x1, x2), max(x1, x2),
                                 min(y1, y2), max(y1, y2), length))

        if len(horizontal) < 2 or len(vertical) < 2:
            continue

        horizontal.sort(key=lambda l: (l[0] + l[1]) / 2)
        vertical.sort(key=lambda l: (l[0] + l[1]) / 2)

        h_groups = cluster_lines(horizontal, axis="y", threshold=h * 0.05)
        v_groups = cluster_lines(vertical, axis="x", threshold=w * 0.05)

        if len(h_groups) < 2 or len(v_groups) < 2:
            continue

        y_top = int(h_groups[0])
        y_bottom = int(h_groups[-1])
        x_left = int(v_groups[0])
        x_right = int(v_groups[-1])

        rw = x_right - x_left
        rh = y_bottom - y_top
        if rw > 0 and rh > 0:
            candidates.append({
                "x": max(0, x_left), "y": max(0, y_top),
                "width": rw, "height": rh,
                "area": float(rw * rh),
                "method": f"hough_canny_{low}_{high}",
                "contour_area": float(rw * rh),
                "num_vertices": 4,
            })
    return candidates


# ─── Strategie 3: Gradient-Übergänge ─────────────────────────────────────────

def detect_by_gradient_transitions(gray: np.ndarray) -> list[dict]:
    candidates = []
    h, w = gray.shape
    enhanced = enhance_contrast(gray, clip_limit=5.0)

    row_means = np.mean(enhanced, axis=1)
    col_means = np.mean(enhanced, axis=0)
    row_grad = np.abs(np.diff(row_means))
    col_grad = np.abs(np.diff(col_means))

    row_thresh = np.max(row_grad) * 0.3 if np.max(row_grad) > 0 else 0
    col_thresh = np.max(col_grad) * 0.3 if np.max(col_grad) > 0 else 0

    row_peaks = np.where(row_grad > row_thresh)[0]
    col_peaks = np.where(col_grad > col_thresh)[0]

    if len(row_peaks) >= 2 and len(col_peaks) >= 2:
        y_top = int(row_peaks[0])
        y_bottom = int(row_peaks[-1]) + 1
        x_left = int(col_peaks[0])
        x_right = int(col_peaks[-1]) + 1
        rw = x_right - x_left
        rh = y_bottom - y_top
        if rw > 0 and rh > 0:
            candidates.append({
                "x": max(0, x_left), "y": max(0, y_top),
                "width": rw, "height": rh,
                "area": float(rw * rh),
                "method": "gradient_transitions",
                "contour_area": float(rw * rh),
                "num_vertices": 4,
            })
    return candidates


# ─── Strategie 4: Helligkeitsmaske ───────────────────────────────────────────

def detect_by_brightness_mask(gray: np.ndarray) -> list[dict]:
    candidates = []
    h, w = gray.shape
    enhanced = enhance_contrast(gray, clip_limit=6.0)
    _, binary = cv2.threshold(enhanced, 0, 255,
                              cv2.THRESH_BINARY + cv2.THRESH_OTSU)

    for use_mask in [binary, cv2.bitwise_not(binary)]:
        kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (15, 15))
        cleaned = cv2.morphologyEx(use_mask, cv2.MORPH_CLOSE, kernel, iterations=3)
        contours, _ = cv2.findContours(
            cleaned, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

        for cnt in contours:
            area = cv2.contourArea(cnt)
            if area < (h * w * 0.05):
                continue
            x, y, rw, rh = cv2.boundingRect(cnt)
            if rw < 50 or rh < 50:
                continue
            candidates.append({
                "x": int(x), "y": int(y),
                "width": int(rw), "height": int(rh),
                "area": float(rw * rh),
                "method": "brightness_mask",
                "contour_area": float(area),
                "num_vertices": 4,
            })
    return candidates


def measure_image_aspect(gray: np.ndarray) -> dict:
    """Misst das Bildverhaeltnis OHNE Ziel-Vorurteil (Pass A).

    Nur Dark-Span (Halter vs Film) + Bright-Span (Bild vs Filmrand).
    Kein Scoring, kein Merge, kein Stage-2-Refinement.
    Fuer dunkle Negative (Bild dunkler als Rand) wird valid=False
    zurueckgegeben – der Film-Median ueber alle Bilder kompensiert das.
    """
    h, w = gray.shape
    row_med = np.median(gray, axis=1).astype(float)
    col_med = np.median(gray, axis=0).astype(float)

    holder_level = 100
    top = next((i for i in range(h) if row_med[i] < holder_level), 0)
    bottom = next((i for i in range(h - 1, -1, -1)
                   if row_med[i] < holder_level), h)
    left = next((i for i in range(w) if col_med[i] < holder_level), 0)
    right = next((i for i in range(w - 1, -1, -1)
                  if col_med[i] < holder_level), w)

    fh, fw = bottom - top, right - left
    if fh < 100 or fw < 100:
        return {"valid": False, "reason": "dark_span zu klein"}

    strip = gray[top:bottom, left:right]
    strip_row = np.median(strip, axis=1).astype(float)
    strip_col = np.median(strip, axis=0).astype(float)

    border_level = min(
        np.percentile(strip_row[:max(5, fh // 20)], 25),
        np.percentile(strip_row[-max(5, fh // 20):], 25))
    content_level = float(np.median(strip_row[fh // 4: 3 * fh // 4]))

    # Polaritaet bestimmen: Ist die Bildmitte heller oder dunkler
    # als die Randbereiche? (Negative koennen beide Richtungen haben)
    center_zone = strip_row[fh // 4: 3 * fh // 4]
    center_mean = float(np.median(center_zone))
    edge_mean = border_level
    polarity_dark = center_mean < edge_mean  # Bild dunkler als Rand

    # Kontrast muss in EINER Richtung vorhanden sein
    contrast = abs(center_mean - edge_mean)
    if contrast < 8:
        return {"valid": False, "reason": "zu wenig Kontrast"}

    # Threshold: 25% des Weges von Rand Richtung Bildmitte
    # (bei dunkler Polaritaet: von Rand ABWAERTS)
    direction = -1 if polarity_dark else 1
    thresh = edge_mean + direction * contrast * 0.25

    def _span(profile, thr, bright, min_run=10):
        best_s, best_e, best_len = 0, 0, 0
        start = None
        for i, v in enumerate(profile):
            hit = (v > thr) if bright else (v < thr)
            if hit and start is None:
                start = i
            elif not hit and start is not None:
                if i - start > best_len:
                    best_s, best_e, best_len = start, i, i - start
                start = None
        if start is not None and len(profile) - start > best_len:
            best_s, best_e, best_len = start, len(profile), len(profile) - start
        return best_s, best_e, best_len

    row_s, row_e, row_len = _span(strip_row, thresh, not polarity_dark)
    col_s, col_e, col_len = _span(strip_col, thresh, not polarity_dark)

    bh = row_e - row_s
    bw = col_e - col_s
    if bh < 50 or bw < 50:
        return {"valid": False, "reason": "span zu klein"}

    aspect = bw / bh
    return {
        "valid": True,
        "strip": {"x": left, "y": top, "w": fw, "h": fh},
        "image": {"x": left + col_s, "y": top + row_s,
                  "w": bw, "h": bh},
        "aspect": round(aspect, 4),
        "contrast": round(content_level - border_level, 1),
    }


# ─── Bewertung ────────────────────────────────────────────────────────────────

def measure_crop_evidence(gray: np.ndarray, x: int, y: int,
                          w: int, h: int) -> dict:
    """Misst Evidenz dafuer, dass ein Crop die tatsaechliche Bildegrenze trifft.

    Gibt 3 Scores zurueck (je 0..1):
      edge:     Helligkeitssprung ueber die 4 Crop-Kanten (innen vs. aussen)
      interior: Anteil heller Pixel im Crop (Bildinhalt vs. Filmrand)
      exterior: Dunkelheit direkt ausserhalb des Crops (Filmrand)
    """
    fh, fw = gray.shape
    x1, y1 = max(0, x), max(0, y)
    x2, y2 = min(fw, x + w), min(fh, y + h)
    if x2 <= x1 or y2 <= y1:
        return {"edge": 0.0, "interior": 0.0, "exterior": 0.0}

    band = 5

    # ── 1. Kanten-Evidenz: Helligkeitssprung ueber jede Kante ──
    edge_scores = []

    # Obere/untere Kante (horizontale Kanten, Sample ueber gesamte Breite)
    for edge_y, is_bottom in [(y1, False), (y2, True)]:
        if is_bottom:
            out_slice = gray[min(edge_y, fh - 1):min(edge_y + band, fh),
                             x1:x2]
            in_slice = gray[max(edge_y - band, 0):edge_y, x1:x2]
        else:
            out_slice = gray[max(edge_y - band, 0):edge_y, x1:x2]
            in_slice = gray[edge_y:min(edge_y + band, fh), x1:x2]
        if out_slice.size == 0 or in_slice.size == 0:
            continue
        step = float(np.mean(in_slice)) - float(np.mean(out_slice))
        edge_scores.append(1.0 / (1.0 + math.exp(-step / 15)))

    # Linke/rechte Kante (vertikale Kanten, Sample ueber gesamte Hoehe)
    for edge_x, is_right in [(x1, False), (x2, True)]:
        if is_right:
            out_slice = gray[y1:y2,
                             min(edge_x, fw - 1):min(edge_x + band, fw)]
            in_slice = gray[y1:y2, max(edge_x - band, 0):edge_x]
        else:
            out_slice = gray[y1:y2, max(edge_x - band, 0):edge_x]
            in_slice = gray[y1:y2, edge_x:min(edge_x + band, fw)]
        if out_slice.size == 0 or in_slice.size == 0:
            continue
        step = float(np.mean(in_slice)) - float(np.mean(out_slice))
        edge_scores.append(1.0 / (1.0 + math.exp(-step / 15)))

    edge_evidence = (sum(edge_scores) / len(edge_scores)) if edge_scores else 0.5

    # ── 2. Innen-Evidenz: Anteil heller Pixel im Crop-Zentrum ──
    mx = max(1, int(w * 0.2))
    my = max(1, int(h * 0.2))
    crop_center = gray[y + my:y + h - my, x + mx:x + w - mx]
    if crop_center.size > 0:
        border_thresh = 60
        interior_bright = float(np.mean(crop_center > border_thresh))
    else:
        interior_bright = 0.5

    # ── 3. Aussen-Evidenz: direkt ausserhalb sollte dunkel sein ──
    ext_band = 10
    ext_samples = []
    if y1 >= ext_band:
        ext_samples.append(float(np.mean(gray[y1 - ext_band:y1, x1:x2])))
    if y2 + ext_band <= fh:
        ext_samples.append(float(np.mean(gray[y2:y2 + ext_band, x1:x2])))
    if x1 >= ext_band:
        ext_samples.append(float(np.mean(gray[y1:y2, x1 - ext_band:x1])))
    if x2 + ext_band <= fw:
        ext_samples.append(float(np.mean(gray[y1:y2, x2:x2 + ext_band])))

    if ext_samples:
        ext_mean = sum(ext_samples) / len(ext_samples)
        exterior_evidence = max(0.0, 1.0 - ext_mean / 120.0)
    else:
        exterior_evidence = 0.5

    return {
        "edge": round(edge_evidence, 3),
        "interior": round(interior_bright, 3),
        "exterior": round(exterior_evidence, 3),
    }


def score_candidate(candidate: dict, img_h: int, img_w: int,
                     target_ratio: float,
                     no_aspect_penalty: bool = False) -> dict:
    x, y = candidate["x"], candidate["y"]
    rw, rh = candidate["width"], candidate["height"]
    if rw <= 0 or rh <= 0:
        return {**candidate, "confidence": 0.0, "reasons": ["Ungültige Dimensionen"]}

    img_area = img_h * img_w
    rect_area = rw * rh
    reasons = []
    score = 1.0

    # Seitenverhältnis – prüfe sowohl Landscape (target) als auch Portrait (1/target)
    aspect = rw / rh
    portrait_ratio = 1.0 / target_ratio  # z.B. 0.667 für 35mm

    # Bestes Verhältnis: das von den beiden Zielwerten, das näher dran ist
    diff_landscape = abs(aspect - target_ratio) / target_ratio
    diff_portrait = abs(aspect - portrait_ratio) / portrait_ratio

    if diff_landscape <= diff_portrait:
        # Landscape-Frame
        aspect_diff = diff_landscape
        orientation = "Landscape"
    else:
        # Portrait-Frame
        aspect_diff = diff_portrait
        orientation = "Portrait"

    if no_aspect_penalty:
        aspect_diff = 0.0  # Aspect-Strafe fuer Film-Analyse deaktivieren

    if aspect_diff < 0.05:
        reasons.append(f"Seitenverhältnis perfekt ({aspect:.2f} ~ {target_ratio:.2f} {orientation})")
    elif aspect_diff < 0.15:
        reasons.append(f"Seitenverhältnis ok ({aspect:.2f} ~ {target_ratio:.2f} {orientation})")
        score *= 0.90
    elif aspect_diff < 0.30:
        reasons.append(f"Seitenverhältnis abweichend ({aspect:.2f} vs {target_ratio:.2f} {orientation})")
        score *= 0.65
    else:
        reasons.append(f"Seitenverhältnis schlecht ({aspect:.2f} vs {target_ratio:.2f} {orientation})")
        score *= 0.3

    # Fläche – bei sehr gutem Seitenverhältnis großzügiger bewerten
    area_frac = rect_area / img_area
    if aspect_diff < 0.10:
        # Sehr gutes Seitenverhältnis → volle Fläche ist OK
        if 0.20 < area_frac < 0.99:
            reasons.append(f"Fläche gut ({area_frac:.0%} des Bildes)")
        elif area_frac >= 0.99:
            reasons.append(f"Fläche voll ({area_frac:.0%} – aber Verhältnis passt)")
            score *= 0.85
        else:
            reasons.append(f"Fläche klein ({area_frac:.0%})")
            score *= 0.75
    else:
        if 0.30 < area_frac < 0.92:
            reasons.append(f"Fläche gut ({area_frac:.0%} des Bildes)")
        elif 0.15 < area_frac < 0.95:
            reasons.append(f"Fläche akzeptabel ({area_frac:.0%})")
            score *= 0.75
        else:
            reasons.append(f"Fläche ungewöhnlich ({area_frac:.0%})")
            score *= 0.4

    # Rand-Check
    margin_x = min(x, img_w - (x + rw))
    margin_y = min(y, img_h - (y + rh))
    if margin_x > img_w * 0.01 and margin_y > img_h * 0.01:
        reasons.append("Randabstand OK")
    elif aspect_diff < 0.10 and area_frac > 0.90:
        # Gutes Verhältnis + große Fläche → kleiner Rand ist normal
        reasons.append("Randabstand klein (aber Verhältnis passt)")
        score *= 0.90
    else:
        reasons.append("Randabstand sehr klein")
        score *= 0.8

    reasons.append(f"Methode: {candidate['method']}")

    geom = round(min(1.0, max(0.0, score)), 3)
    return {
        "x": x, "y": y, "width": rw, "height": rh,
        "orientation": orientation,
        "geom_score": geom,
        "confidence": geom,  # vorlaeufig, wird in find_best_crop ueberschrieben
        "reasons": reasons,
        "method": candidate["method"],
    }


# ─── Zusammenführung und Hauptfunktion ────────────────────────────────────────

# ─── Stufe 2: Bildinhalt innerhalb des Filmstreifens ─────────────────────────

def refine_to_image_content(gray: np.ndarray, x: int, y: int, w: int, h: int,
                            debug: bool = False,
                            film_border_level: float = None,
                            target_aspect: float = None
                            ) -> tuple[int, int, int, int]:
    """Stufe 2: Verfeinert den Filmstreifen-Ausschnitt zur tatsaechlichen Bilddflaeche.

    Strategie: Finde den groessten hellen Bereich im Filmstreifen.
    Der Filmstreifen kann am Rand noch weissen Halter enthalten.
    Das Bild ist der groesste zusammenhaengende helle Bereich.
    """
    film = gray[y:y + h, x:x + w]
    fh, fw = film.shape

    # 1. Helligkeitsprofile
    col_median = np.median(film, axis=0).astype(float)
    row_median = np.median(film, axis=1).astype(float)

    # 2. Filmrand-Schwelle
    # Filmrand ist immer sehr dunkel (~35-45).
    # Bildinhalt ist typischerweise >70.
    border_rows = max(5, fh // 20)
    border_top_val = np.percentile(row_median[:border_rows], 25)
    border_bot_val = np.percentile(row_median[-border_rows:], 25)
    border_level = min(border_top_val, border_bot_val)
    # Filmweite Border-Schwaetze uebergeben fuer bessere Erkennung
    # bei dunklen Bildern
    if film_border_level is not None:
        border_level = film_border_level
    content_level = np.median(row_median[fh // 4:fh * 3 // 4])
    # Schwelle zwischen Rand und Bild
    row_border_thresh = max(border_level + 25,
                            (border_level + content_level) / 2)

    col_border_rows = max(5, fw // 20)
    col_border_left = np.percentile(col_median[:col_border_rows], 25)
    col_border_right = np.percentile(col_median[-col_border_rows:], 25)
    col_border_level = min(col_border_left, col_border_right)
    col_content_level = np.median(col_median[fw // 4:fw * 3 // 4])
    col_border_thresh = max(col_border_level + 25,
                            (col_border_level + col_content_level) / 2)

    if debug:
        print(f"[DEBUG Stage2] Border: row_border={border_level:.0f} "
              f"content={content_level:.0f} thresh={row_border_thresh:.0f}",
              file=sys.stderr)

    # 3. Hilfsfunktion: Groessten hellen Bereich in einem Profil finden
    def _find_bright_span(median_profile, threshold, min_run=5):
        """Findet den groessten zusammenhaengenden Bereich
        der ueber threshold liegt. """
        bright = median_profile > threshold
        best_start, best_end = 0, 0
        best_len = 0
        in_run = False
        run_start = 0
        for i in range(len(bright)):
            if bright[i]:
                if not in_run:
                    run_start = i
                    in_run = True
            else:
                if in_run:
                    run_len = i - run_start
                    if run_len >= min_run and run_len > best_len:
                        best_start = run_start
                        best_end = i
                        best_len = run_len
                    in_run = False
        if in_run:
            run_len = len(bright) - run_start
            if run_len >= min_run and run_len > best_len:
                best_start = run_start
                best_end = len(bright)
                best_len = run_len
        return best_start, best_end

    # 4. Groessten Bildbereich finden
    top_edge, bottom_edge = _find_bright_span(row_median, row_border_thresh)
    left_edge, right_edge = _find_bright_span(col_median, col_border_thresh)

    # Fallback: Wenn der gefundene Bereich zu klein ist (<50% des Films),
    # Schwelle senken und erneut versuchen (fuer dunkle Fotos)
    film_area = fh * fw
    found_area = (bottom_edge - top_edge) * (right_edge - left_edge)
    if found_area < film_area * 0.40:
        looser_thresh = border_level + 10
        looser_col_thresh = col_border_level + 10
        top_edge2, bottom_edge2 = _find_bright_span(
            row_median, looser_thresh)
        left_edge2, right_edge2 = _find_bright_span(
            col_median, looser_col_thresh)
        new_area = (bottom_edge2 - top_edge2) * (right_edge2 - left_edge2)
        if new_area > found_area:
            top_edge, bottom_edge = top_edge2, bottom_edge2
            left_edge, right_edge = left_edge2, right_edge2
            if debug:
                print(f"[DEBUG Stage2] Fallback: Schwelle "
                      f"{row_border_thresh:.0f}->{looser_thresh:.0f}",
                      file=sys.stderr)

    # 5. Aspect-Anpassung an Film-Seitenverhaeltnis
    # Zuerst: Dark-Span fuer Geometry-Fallback (unabhaengig von Threshold)
    # Berechne dark span aus STRIP (row/col_median des Filmstreifens)
    holder_level = 100
    dark_top, dark_bot = 0, fh
    for i in range(fh):
        if row_median[i] < holder_level:
            dark_top = i
            break
    for i in range(fh - 1, -1, -1):
        if row_median[i] < holder_level:
            dark_bot = i + 1
            break
    dark_left, dark_right = 0, fw
    for i in range(fw):
        if col_median[i] < holder_level:
            dark_left = i
            break
    for i in range(fw - 1, -1, -1):
        if col_median[i] < holder_level:
            dark_right = i + 1
            break
    dark_h = dark_bot - dark_top
    dark_w = dark_right - dark_left

    if target_aspect is not None and dark_h > 50 and dark_w > 50:
        span_w = right_edge - left_edge
        span_h = bottom_edge - top_edge
        if span_w > 0 and span_h > 0:
            span_aspect = span_w / span_h
            ta_landscape = target_aspect if target_aspect >= 1 else 1/target_aspect
            dev = abs(span_aspect - ta_landscape) / ta_landscape

            if dev > 0.15:
                if debug:
                    print(f"[DEBUG Stage2] Aspect {span_aspect:.2f} vs "
                          f"Ziel {ta_landscape:.2f} (dev={dev:.0%})",
                          file=sys.stderr)

                if span_aspect > ta_landscape * 1.15:
                    # Zu breit -> Height expandieren
                    center_y = (top_edge + bottom_edge) // 2
                    target_h = int(span_w / ta_landscape)
                    looser = border_level + 15
                    new_top = max(0, center_y - target_h // 2)
                    new_bot = min(fh, center_y + target_h // 2)
                    top_rows = [row_median[i] for i in range(new_top, top_edge + 1) if i < fh]
                    bot_rows = [row_median[i] for i in range(bottom_edge, new_bot) if i < fh]
                    ok_top = (not top_rows) or sum(1 for v in top_rows if v > looser) / len(top_rows) >= 0.90
                    ok_bot = (not bot_rows) or sum(1 for v in bot_rows if v > looser) / len(bot_rows) >= 0.90
                    if ok_top:
                        top_edge = new_top
                    if ok_bot:
                        bottom_edge = new_bot
                    if debug:
                        print(f"  Expand Height: {span_h} -> "
                              f"{bottom_edge - top_edge}", file=sys.stderr)

                # Pruefe ob Aspect immer noch abweicht (fuer beide Faelle)
                new_h = bottom_edge - top_edge
                new_w = right_edge - left_edge
                new_dev = abs(new_w / max(new_h, 1) - ta_landscape) / ta_landscape \
                    if new_h > 0 else 1.0

                if new_dev > 0.15:
                        # Geometry-Fallback: Dark-Span × Ziel-Aspect
                        expected_w = int(dark_h * ta_landscape)

                        center_x = (dark_left + dark_right) // 2
                        new_left = max(0,
                                       center_x - expected_w // 2)
                        new_right = min(fw,
                                        center_x + expected_w // 2)
                        if new_left < center_x \
                                and new_right > center_x \
                                and new_right - new_left > 100:
                            if debug:
                                print(f"  Geometry-Fallback: "
                                      f"{dark_h}h x "
                                      f"{ta_landscape:.2f} = "
                                      f"{expected_w}w",
                                      file=sys.stderr)
                            left_edge = new_left
                            right_edge = new_right
                            if dark_h > new_h:
                                top_edge = dark_top
                                bottom_edge = dark_bot

    # 6. Koordinaten umrechnen
    new_x = x + left_edge
    new_y = y + top_edge
    new_w = right_edge - left_edge
    new_h = bottom_edge - top_edge

    if new_w <= 0 or new_h <= 0:
        return x, y, w, h
    if new_w * new_h < w * h * 0.3:
        return x, y, w, h

    if debug:
        print(f"[DEBUG Stage2] Film ({x},{y}) {w}x{h} -> "
              f"Bild ({new_x},{new_y}) {new_w}x{new_h}", file=sys.stderr)
        print(f"  Ausgeschlossen: links={left_edge}px  rechts={fw-right_edge}px"
              f"  oben={top_edge}px  unten={fh-bottom_edge}px", file=sys.stderr)

    return int(new_x), int(new_y), int(new_w), int(new_h)


def merge_nearby_candidates(candidates: list[dict], tolerance: float = 30) -> list[dict]:
    if not candidates:
        return []
    candidates.sort(key=lambda c: c["confidence"], reverse=True)
    merged = []
    used = set()

    for i, c1 in enumerate(candidates):
        if i in used:
            continue
        group = [c1]
        used.add(i)
        for j, c2 in enumerate(candidates):
            if j in used:
                continue
            dx = abs(c1["x"] - c2["x"])
            dy = abs(c1["y"] - c2["y"])
            dw = abs(c1["width"] - c2["width"])
            dh = abs(c1["height"] - c2["height"])
            if dx < tolerance and dy < tolerance and dw < tolerance and dh < tolerance:
                group.append(c2)
                used.add(j)

        best = group[0]
        if len(group) > 1:
            best["reasons"].append(
                f"{len(group)} Strategien stimmen überein")
        merged.append(best)
    return merged


def find_best_crop(gray: np.ndarray, target_ratio: float = 1.5,
                   debug: bool = False, dump_all: bool = False,
                   skip_refine: bool = False,
                   film_border_level: float = None,
                   no_aspect_penalty: bool = False) -> dict:
    h, w = gray.shape

    all_candidates = []
    all_candidates.extend(detect_by_contours(gray))
    all_candidates.extend(detect_by_canny_hough(gray))
    all_candidates.extend(detect_by_gradient_transitions(gray))
    all_candidates.extend(detect_by_brightness_mask(gray))

    if debug:
        print(f"[DEBUG] {len(all_candidates)} Kandidaten gefunden", file=sys.stderr)
        for c in all_candidates:
            print(f"  -> {c['method']}: ({c['x']},{c['y']}) "
                  f"{c['width']}x{c['height']}", file=sys.stderr)

    if not all_candidates:
        return {
            "x": 0, "y": 0, "width": w, "height": h,
            "confidence": 0.0,
            "reasons": ["Kein Kandidat gefunden – Bild wird nicht beschnitten."],
            "method": "none",
        }

    scored = []
    for c in all_candidates:
        scored.append(score_candidate(c, h, w, target_ratio,
                                       no_aspect_penalty))

    merged = merge_nearby_candidates(scored, tolerance=max(h, w) * 0.05)

    # ── Evidenz-basierte Konfidenz berechnen ──
    for s in merged:
        ev = measure_crop_evidence(
            gray, s["x"], s["y"], s["width"], s["height"])
        evidence = (0.50 * ev["edge"]
                    + 0.20 * ev["interior"]
                    + 0.15 * ev["exterior"])
        geom = s.get("geom_score", s["confidence"])

        # Film-Konsistenz: Abweichung vom Film-Seitenverhaeltnis
        aspect = s["width"] / max(s["height"], 1)
        portrait_ratio = 1.0 / target_ratio
        diff_landscape = abs(aspect - target_ratio) / target_ratio
        diff_portrait = abs(aspect - portrait_ratio) / portrait_ratio
        film_dev = min(diff_landscape, diff_portrait)
        film_penalty = max(0.7, 1.0 - film_dev * 3)

        s["confidence"] = round(
            (0.35 * geom + 0.65 * evidence) * film_penalty, 3)
        s["evidence"] = ev

    # Dump-Modus: alle Kandidaten + verfeinertes Ergebnis zurueckgeben
    if dump_all:
        # Besten Kandidaten finden UND Stage 2 ausfuehren
        def _best_key(c):
            ob = 0.001 if c.get("orientation") == "Landscape" else 0.0
            return (round(c["confidence"], 2),
                    c["width"] * c["height"] + ob)
        best = max(merged, key=_best_key)
        rx, ry, rw, rh = refine_to_image_content(
            gray, best["x"], best["y"], best["width"], best["height"],
            target_aspect=target_ratio)
        best_refined = dict(best)
        best_refined["x"] = rx
        best_refined["y"] = ry
        best_refined["width"] = rw
        best_refined["height"] = rh
        best_refined["aspect"] = round(rw / max(rh, 1), 4)
        # Alle Kandidaten mit aspect-Feld ergaenzen
        for c in merged:
            c["aspect"] = round(c["width"] / max(c["height"], 1), 4)
        return {"candidates": merged, "best_refined": best_refined}

    # Bei ähnlicher Konfidenz: bevorzuge größere Fläche (voller Filmrahmen)
    # und bevorzuge Landscape bei gleicher Konfidenz (Default-Orientierung)
    def best_key(c):
        orient_bonus = 0.001 if c.get("orientation") == "Landscape" else 0.0
        return (round(c["confidence"], 2), c["width"] * c["height"] + orient_bonus)
    best = max(merged, key=best_key)

    if debug:
        print(f"\n[DEBUG] Stufe 1 – Bester Filmstreifen: ({best['x']},{best['y']}) "
              f"{best['width']}x{best['height']} conf={best['confidence']:.3f}",
              file=sys.stderr)
        for r in best["reasons"]:
            print(f"    - {r}", file=sys.stderr)

    # ─── Stufe 2: Verfeinerung zur tatsächlichen Bilddfläche ───
    if not skip_refine:
        rx, ry, rw, rh = refine_to_image_content(
            gray, best["x"], best["y"], best["width"], best["height"],
            debug=debug, film_border_level=film_border_level,
            target_aspect=target_ratio)

        if (rx, ry, rw, rh) != (best["x"], best["y"], best["width"], best["height"]):
            # Bild wurde verfeinert – Koordinaten aktualisieren
            area_before = best["width"] * best["height"]
            area_after = rw * rh
            removed_pct = (area_before - area_after) / area_before * 100

            best["x"] = rx
            best["y"] = ry
            best["width"] = rw
            best["height"] = rh
            best["area"] = float(rw * rh)
            best["orientation"] = "Portrait" if rw < rh else "Landscape"
            best["reasons"].append(
                f"Stufe 2: {removed_pct:.0f}% des Filmstreifens "
                f"ausgeschlossen (Sprossenlöcher/Ränder)")
            # Evidenz fuer verfeinertes Ergebnis neu berechnen
            ev2 = measure_crop_evidence(
                gray, best["x"], best["y"], best["width"], best["height"])
            evidence2 = (0.50 * ev2["edge"] + 0.20 * ev2["interior"]
                         + 0.15 * ev2["exterior"])
            geom2 = best.get("geom_score", 0.5)
            best["confidence"] = round(0.35 * geom2 + 0.65 * evidence2, 3)
            best["evidence"] = ev2

    if debug:
        print(f"\n[DEBUG] Endergebnis: ({best['x']},{best['y']}) "
              f"{best['width']}x{best['height']} conf={best['confidence']:.3f}",
              file=sys.stderr)
        for r in best["reasons"]:
            print(f"    - {r}", file=sys.stderr)

    return best


# ─── Film-Konsens (Batch) ────────────────────────────────────────────────────

SKEW_MAX_DEG = 9.0      # Suchbereich +/- Grad (erwartet werden bis ca. 7)


def _line_fit_theil_sen(us, vs, tol):
    """Robuste Gerade v = a + s*u (Theil-Sen). Liefert (slope, inlier_ratio, intercept)."""
    us = np.asarray(us, float)
    vs = np.asarray(vs, float)
    n = len(us)
    if n < 6:
        return None, 0.0, 0.0
    iu, ju = np.triu_indices(n, 1)
    du = us[ju] - us[iu]
    ok = np.abs(du) > 1e-6
    slopes = (vs[ju] - vs[iu])[ok] / du[ok]
    if len(slopes) == 0:
        return None, 0.0, 0.0
    s = float(np.median(slopes))
    a = float(np.median(vs - s * us))
    res = np.abs(vs - (a + s * us))
    return s, float(np.mean(res < tol)), a


SKEW_NEAR_FRAC = 0.03    # Kante darf hoechstens so weit (Anteil der kurzen Crop-Seite) vom Crop entfernt liegen
SKEW_NEAR_MIN_PX = 10


def _search_side_line(edge_map, x, y, w, h, side):
    """Sucht die staerkste gerade Kante *in der Naehe der Crop-Kante* (Hough-artig):
    Geraden durch die Kantenmitte +/- ``near`` Pixel, Winkel +/- SKEW_MAX_DEG. Weiter
    entfernte Kanten (Filmhalter, Rahmen der Aufnahmevorrichtung) kommen so gar nicht erst
    in Frage. Liefert (u, v, score, dominance) der besten Geraden oder None.

    u = Position entlang der Kante, v = Position quer dazu; edge_map = Betrag des Gradienten
    quer zur Kante (fuer waagerechte Seiten in y, fuer senkrechte in x)."""
    horizontal = side in ("top", "bottom")
    ih, iw = edge_map.shape
    length, start = (w, x) if horizontal else (h, y)
    c = (y if side == "top" else y + h) if horizontal else (x if side == "left" else x + w)
    near = max(SKEW_NEAR_MIN_PX, int(SKEW_NEAR_FRAC * min(w, h)))
    angles = np.arange(-SKEW_MAX_DEG, SKEW_MAX_DEG + 1e-6, 0.25)
    offs = np.arange(-near, near + 1, 2)
    us = start + np.linspace(0.06, 0.94, 48) * length
    umid = start + length / 2.0
    slopes = np.tan(np.radians(angles))
    v = c + offs[None, :, None] + slopes[:, None, None] * (us[None, None, :] - umid)
    ui = np.broadcast_to(np.rint(us).astype(int)[None, None, :], v.shape)
    vi = np.rint(v).astype(int)
    hi_v, hi_u = (ih, iw) if horizontal else (iw, ih)
    ok = (vi >= 0) & (vi < hi_v) & (ui >= 0) & (ui < hi_u)
    vi_c, ui_c = np.clip(vi, 0, hi_v - 1), np.clip(ui, 0, hi_u - 1)
    vals = edge_map[vi_c, ui_c] if horizontal else edge_map[ui_c, vi_c]
    vals = np.where(ok, np.minimum(vals, 60.0), 0.0)
    score = vals.sum(axis=2) / np.maximum(ok.sum(axis=2), 1)
    ai, oi = np.unravel_index(int(np.argmax(score)), score.shape)
    best = float(score[ai, oi])
    base = float(np.median(score))
    if best < 5.0:
        return None
    return {"slope": float(slopes[ai]), "off": float(offs[oi]), "c": c, "umid": umid,
            "score": best, "dominance": best / max(base, 1e-6), "near": near}


def _refine_side_line(edge_map, x, y, w, h, side, found, band=4):
    """Feinanpassung: entlang der gefundenen Geraden je Stuetzstelle die staerkste Kante in
    +/- band Pixel nehmen und robust (Theil-Sen) anpassen. Liefert (slope, intercept, inlier)."""
    horizontal = side in ("top", "bottom")
    ih, iw = edge_map.shape
    length, start = (w, x) if horizontal else (h, y)
    us, vs = [], []
    for u in np.linspace(start + 0.06 * length, start + 0.94 * length, 40):
        v0 = found["c"] + found["off"] + found["slope"] * (u - found["umid"])
        lo, hi = int(round(v0)) - band, int(round(v0)) + band + 1
        ui = int(round(u))
        if horizontal:
            if not (0 <= ui < iw) or lo < 0 or hi > ih:
                continue
            col = edge_map[lo:hi, ui]
        else:
            if not (0 <= ui < ih) or lo < 0 or hi > iw:
                continue
            col = edge_map[ui, lo:hi]
        if col.max() < 4:
            continue
        us.append(u)
        vs.append(lo + int(np.argmax(col)))
    slope, inl, icpt = _line_fit_theil_sen(us, vs, tol=2.5)
    if slope is None:
        return None
    return slope, icpt, inl


def measure_skew(gray, x, y, w, h):
    """Schaetzt die Schraeglage des Filmrahmens in Grad aus den vier Kanten des
    (ungefaehren) Crops. Positiv = Bildinhalt im Uhrzeigersinn verdreht.

    Jede Seite: erst Suche nach der staerksten geraden Kante *in der Naehe der Crop-Kante*
    (siehe _search_side_line), dann Feinanpassung. Die Winkel der Seiten werden gewichtet
    gemittelt. 'conf' ist der Anteil uebereinstimmender Kantenpunkte, 'spread' die Streuung der
    Seitenwinkel (Grad), 'lines' die angepassten Kanten (normiert auf das Bild)."""
    g = cv2.GaussianBlur(gray.astype(np.float32), (0, 0), 1.5)
    gy = cv2.blur(np.abs(cv2.Sobel(g, cv2.CV_32F, 0, 1, ksize=3)) / 4.0, (5, 5))
    gx = cv2.blur(np.abs(cv2.Sobel(g, cv2.CV_32F, 1, 0, ksize=3)) / 4.0, (5, 5))
    ih, iw = gray.shape
    sides, lines = {}, {}
    for side in ("top", "bottom", "left", "right"):
        horizontal = side in ("top", "bottom")
        emap = gy if horizontal else gx
        found = _search_side_line(emap, x, y, w, h, side)
        if not found or found["dominance"] < 1.8:
            continue
        fit = _refine_side_line(emap, x, y, w, h, side, found)
        if not fit:
            continue
        slope, icpt, inl = fit
        if inl < 0.5 or abs(slope) > math.tan(math.radians(SKEW_MAX_DEG + 1)):
            continue
        # muss weiterhin nahe der Crop-Kante liegen (Mitte der Kante)
        umid = found["umid"]
        if abs(icpt + slope * umid - found["c"]) > found["near"] + 4:
            continue
        ang = math.degrees(math.atan(slope))
        sides[side] = (ang if horizontal else -ang, inl)
        u0, u1 = (x, x + w) if horizontal else (y, y + h)
        p0, p1 = (u0, icpt + slope * u0), (u1, icpt + slope * u1)
        pts = ([[p0[0], p0[1]], [p1[0], p1[1]]] if horizontal
               else [[p0[1], p0[0]], [p1[1], p1[0]]])
        lines[side] = [[round(px / iw, 5), round(py / ih, 5)] for px, py in pts]
    if len(sides) < 2:
        return {"deg": None, "conf": 0.0, "spread": None, "sides": {}, "lines": {}}
    def wmedian(items):
        angs = np.array([v[0] for v in items])
        wts = np.array([v[1] for v in items])
        order = np.argsort(angs)
        cw = np.cumsum(wts[order])
        return float(angs[order][np.searchsorted(cw, cw[-1] / 2)])

    # Ausreisser-Seiten (eine Kante hat etwas anderes erwischt) verwerfen: nur Seiten, die mit dem
    # Median uebereinstimmen, zaehlen; mindestens zwei muessen sich einig sein
    deg = wmedian(list(sides.values()))
    kept = {k: v for k, v in sides.items() if abs(v[0] - deg) <= 0.8}
    if len(kept) < 2:
        return {"deg": None, "conf": 0.0, "spread": None,
                "sides": {k: round(v[0], 2) for k, v in sides.items()}, "lines": {}}
    deg = wmedian(list(kept.values()))
    angs = np.array([v[0] for v in kept.values()])
    spread = float(np.max(np.abs(angs - deg)))
    if len(kept) == 2 and spread > 0.35:      # zwei uneinige Seiten: lieber nichts melden
        return {"deg": None, "conf": 0.0, "spread": round(spread, 2),
                "sides": {k: round(v[0], 2) for k, v in sides.items()}, "lines": {}}
    wts = np.array([v[1] for v in kept.values()])
    return {"deg": round(deg, 2), "conf": round(float(wts.mean()) * len(kept) / 4, 3),
            "spread": round(spread, 2),
            "sides": {k: round(v[0], 2) for k, v in kept.items()},
            "lines": {k: lines[k] for k in kept}}


def _median(xs):
    xs = sorted(xs)
    n = len(xs)
    if n == 0:
        return 0.0
    m = n // 2
    return xs[m] if n % 2 else 0.5 * (xs[m - 1] + xs[m])


def _cluster_center(vals, rel_band=0.035):
    """Median des groessten eng beieinander liegenden Clusters.

    Robuster als der Median, wenn die Fehler einseitig streuen: eine
    fehldetektierte Teilfläche macht den Crop fast immer *kleiner*, nie
    groesser. Der echte Frame liegt als dichter Haufen vor (GT-MAD ~7px),
    die Fehldetektionen als breiter Schweif darunter.
    """
    vals = sorted(vals)
    if len(vals) <= 2:
        return _median(vals)
    band = rel_band * _median(vals)
    best_i, best_j = 0, 0
    j = 0
    for i in range(len(vals)):
        if j < i:
            j = i
        while j + 1 < len(vals) and vals[j + 1] - vals[i] <= band:
            j += 1
        if j - i > best_j - best_i:
            best_i, best_j = i, j
    return _median(vals[best_i:best_j + 1])


def _detect_polarity(gray, x, y, w, h, margin=40):
    """Polaritaet aus grossflaechigen Mittelwerten bestimmen (robuster als
    der Kantenkontrast an der noch ungenauen Start-Box): Bildmitte vs. ein
    Streifen deutlich ausserhalb der Box. +1 = Bild heller als Umgebung
    (typische Belichtung), -1 = Bild dunkler (dichtes Negativ)."""
    ih, iw = gray.shape
    cy0, cy1 = y + int(h * 0.25), y + int(h * 0.75)
    cx0, cx1 = x + int(w * 0.25), x + int(w * 0.75)
    if cy1 <= cy0 or cx1 <= cx0:
        return 1
    interior = float(np.mean(gray[cy0:cy1, cx0:cx1]))
    samples = []
    if y - margin >= 0:
        samples.append(float(np.mean(gray[y - margin:y, x:x + w])))
    if y + h + margin <= ih:
        samples.append(float(np.mean(gray[y + h:y + h + margin, x:x + w])))
    if x - margin >= 0:
        samples.append(float(np.mean(gray[y:y + h, x - margin:x])))
    if x + w + margin <= iw:
        samples.append(float(np.mean(gray[y:y + h, x + w:x + w + margin])))
    if not samples:
        return 1
    exterior = sum(samples) / len(samples)
    return 1 if interior >= exterior else -1


def _edge_fit(gray, x, y, w, h, polarity=1, band=6):
    """Kantenpassung: Summe (innen - aussen) ueber alle 4 Kanten, vorzeichen-
    korrigiert mit polarity (siehe _edge_step), unnormiert. Hoehere Werte =
    Boxkante liegt auf der Bild/Filmrand-Grenze.
    """
    ih, iw = gray.shape
    x2, y2 = x + w, y + h
    if x < band or y < band or x2 + band > iw or y2 + band > ih:
        return -1e9  # Box (mit Rand) ausserhalb des Bildes
    top_in = float(np.mean(gray[y:y + band, x:x2]))
    top_out = float(np.mean(gray[y - band:y, x:x2]))
    bot_in = float(np.mean(gray[y2 - band:y2, x:x2]))
    bot_out = float(np.mean(gray[y2:y2 + band, x:x2]))
    lft_in = float(np.mean(gray[y:y2, x:x + band]))
    lft_out = float(np.mean(gray[y:y2, x - band:x]))
    rgt_in = float(np.mean(gray[y:y2, x2 - band:x2]))
    rgt_out = float(np.mean(gray[y:y2, x2:x2 + band]))
    raw = ((top_in - top_out) + (bot_in - bot_out)
           + (lft_in - lft_out) + (rgt_in - rgt_out))
    return polarity * raw


def _edge_step(profile_in, profile_out, polarity=1):
    """Kontrast innen-aussen fuer eine Kante, vorzeichenkorrigiert.

    Negative koennen beide Polaritaeten haben (Bildinhalt heller ODER
    dunkler als der Filmrand, je nach Belichtung). polarity=+1 erwartet
    "innen heller", polarity=-1 "innen dunkler" - wird einmal pro Bild aus
    der Start-Box bestimmt (siehe _refine_box) und hier nur angewendet.
    """
    return polarity * (float(np.mean(profile_in)) - float(np.mean(profile_out)))


def _refine_edge(gray, lo, hi, fixed_a, fixed_b, axis, is_far, polarity=1,
                 band=6, reach=28, step=4):
    """Eine Kante entlang ihrer Normalen +/-reach verschieben und den
    (polaritaetskorrigierten) Kantenkontrast maximieren. axis 0 = vertikale
    Kante (x), 1 = horizontale (y). is_far = rechte/untere Kante. Liefert
    die neue Kantenposition.
    """
    ih, iw = gray.shape
    base = hi if is_far else lo
    best_pos, best = base, -1e9
    for d in range(-reach, reach + 1, step):
        p = base + d
        if axis == 0:
            if p < band or p > iw - band:
                continue
            if is_far:
                s_in = gray[fixed_a:fixed_b, p - band:p]
                s_out = gray[fixed_a:fixed_b, p:p + band]
            else:
                s_in = gray[fixed_a:fixed_b, p:p + band]
                s_out = gray[fixed_a:fixed_b, p - band:p]
        else:
            if p < band or p > ih - band:
                continue
            if is_far:
                s_in = gray[p - band:p, fixed_a:fixed_b]
                s_out = gray[p:p + band, fixed_a:fixed_b]
            else:
                s_in = gray[p:p + band, fixed_a:fixed_b]
                s_out = gray[p - band:p, fixed_a:fixed_b]
        if s_in.size == 0 or s_out.size == 0:
            continue
        val = _edge_step(s_in, s_out, polarity)
        if val > best + 1e-6:
            best, best_pos = val, p
    return best_pos


REFINE_SIZE_SLACK = float(os.environ.get("ACN_REFINE_SLACK", 10))   # Sweep 10/15/30/45 auf 209 Referenzen: 194/192/191/192 Treffer


def _refine_box(gray, x, y, w, h, pos_reach=48, size_slack=None):
    """Konsens-Box lokal einpassen: erst Position grob (Kantenpassung),
    dann jede Kante einzeln (max +/-size_slack von der Konsens-Groesse).

    Konsens liefert die robuste Start-Groesse; dieser Schritt korrigiert
    den systematischen Detektions-Offset pro Bild, ohne dass die Groesse
    weglaufen kann. Liefert (x, y, w, h, score01).
    """
    ih, iw = gray.shape
    x0, y0 = x, y
    if size_slack is None:
        size_slack = REFINE_SIZE_SLACK

    # Polaritaet einmal aus der Start-Box bestimmen: Negative koennen beide
    # Richtungen haben (Bildinhalt heller ODER dunkler als der Filmrand, je
    # nach Belichtung). Ohne das laeuft die Suche bei dunklen Bildern zur
    # falschen Kante (Richtung Filmstreifenrand statt Bildrand).
    # Per-Bild-Polaritaet ("Bild dunkler als Rand") wurde getestet und hat
    # die Trefferquote gesenkt (zu leicht durch interne Bildkontraste
    # abgelenkt) - fest auf "Bild heller als Rand" belassen, was fuer die
    # Mehrheit der Testbilder zutrifft. _detect_polarity() bleibt fuer
    # spaetere, gezieltere Versuche (siehe eval Film 28/31).
    polarity = 1

    # 1. Position grob
    bx, by, bf = x, y, _edge_fit(gray, x, y, w, h, polarity)
    for dy in range(-pos_reach, pos_reach + 1, 8):
        for dx in range(-pos_reach, pos_reach + 1, 8):
            nx = max(0, min(iw - w, x0 + dx))
            ny = max(0, min(ih - h, y0 + dy))
            s = _edge_fit(gray, nx, ny, w, h, polarity)
            closer = abs(nx - x0) + abs(ny - y0) < abs(bx - x0) + abs(by - y0)
            if s > bf + 1.0 or (s > bf - 1.0 and closer):
                bf, bx, by = max(s, bf), nx, ny
    x, y = bx, by
    l, t, rgt, bot = x, y, x + w, y + h

    # 2. Kanten einzeln, begrenzt auf Konsens-Groesse +/- size_slack
    l = _refine_edge(gray, l, rgt, t, bot, axis=0, is_far=False,
                     polarity=polarity)
    rgt = _refine_edge(gray, l, rgt, t, bot, axis=0, is_far=True,
                       polarity=polarity)
    t = _refine_edge(gray, t, bot, l, rgt, axis=1, is_far=False,
                     polarity=polarity)
    bot = _refine_edge(gray, t, bot, l, rgt, axis=1, is_far=True,
                       polarity=polarity)

    cx, cy = (x + w / 2), (y + h / 2)
    nw = max(w - size_slack, min(w + size_slack, rgt - l))
    nh = max(h - size_slack, min(h + size_slack, bot - t))
    nx = int(round(max(0, min(iw - nw, (l + rgt) / 2 - nw / 2))))
    ny = int(round(max(0, min(ih - nh, (t + bot) / 2 - nh / 2))))
    nw, nh = int(round(nw)), int(round(nh))

    final_fit = _edge_fit(gray, nx, ny, nw, nh, polarity)
    score01 = max(0.0, min(1.0, final_fit / 60.0))
    return nx, ny, nw, nh, score01


# ─── Parallelisierung ─────────────────────────────────────────────────────
# Jedes Bild ist unabhaengig: Pass A, Pass B und der Konsens-Refine
# parallelisieren ueber Bilder (Prozesse, nicht Threads - die NumPy/Python-
# Schleifen in find_best_crop/_refine_box halten die GIL).

def _worker_count(n_items):
    return max(1, min(os.cpu_count() or 4, n_items))


def _pass_a_worker(item):
    """Ein Bild fuer Pass A verarbeiten. item = (path, load_path)."""
    path, load_path = item
    img = load_image(load_path)
    _, small, _ = _prepare_detect_gray(img)
    m = measure_image_aspect(small)
    film = os.path.basename(os.path.dirname(os.path.abspath(path)))
    return {"film": film, "path": os.path.abspath(path), "measurement": m}


def _pass_b_worker(item):
    """Ein Bild fuer Pass B verarbeiten.
    item = (film, path, load_path, aspect, debug, exposure_contrast)."""
    film, path, load_path, aspect, debug, exposure_contrast = item
    img = load_image(load_path)
    full, small, scale = _prepare_detect_gray(img)
    result = find_best_crop(small, target_ratio=aspect, debug=debug,
                            film_border_level=None)
    if scale < 1.0:
        for k in ("x", "y", "width", "height"):
            if result.get(k) is not None:
                result[k] = int(round(result[k] / scale))
    result["input_file"] = os.path.abspath(path)
    result["film"] = film
    result["filename"] = os.path.basename(path)
    result["film_aspect"] = aspect
    result["target_aspect_ratio"] = aspect
    result["_img_w"] = full.shape[1]
    result["_img_h"] = full.shape[0]
    result["_exposure_contrast"] = exposure_contrast
    result["_raw"] = {"x": result.get("x"), "y": result.get("y"),
                      "width": result.get("width"),
                      "height": result.get("height")}
    return result


def _consensus_worker(item):
    """Ein Bild lokal auf die Konsens-Box einpassen.
    item = (load_path, x, y, w, h)."""
    load_path, x, y, w, h = item
    g = to_gray(load_image(load_path))
    return _refine_box(g, x, y, w, h)


def apply_film_consensus(results, load_paths, report=None):
    """Fixiert die Crop-Groesse pro Filmrolle auf den robusten Median und
    loest je Bild nur noch die Position.

    Grundannahme: ein Ordner = eine Rolle, gleiche Kamera/gleicher Abstand
    -> lange und kurze Bildkante sind ueber die Rolle nahezu konstant
    (gemessen MAD ~10px). Der Median ueberlebt >50% Fehldetektion; damit
    verschwinden die Totalausfaelle (halber Frame, Nachbarframe).
    """
    from collections import defaultdict

    if report is None:
        def report(_):
            return None

    by_film = defaultdict(list)
    for r in results:
        raw = r.get("_raw") or {}
        if raw.get("width") and raw.get("height"):
            by_film[r["film"]].append(r)

    # --- Pass 1: pro Film die rohe Konsens-Groesse bestimmen ---
    film_stats = {}
    for film, rs in by_film.items():
        n = len(rs)
        if n < 3:
            continue  # zu wenig Bilder fuer einen verlaesslichen Median

        # Lange/kurze Kante getrennt: eine Rolle kann Hoch- UND Querformat
        # mischen (Kamera pro Aufnahme gedreht).
        longs = [max(r["_raw"]["width"], r["_raw"]["height"]) for r in rs]
        shorts = [min(r["_raw"]["width"], r["_raw"]["height"]) for r in rs]
        long_px = _cluster_center(longs)
        short_px = _cluster_center(shorts)
        aspect = rs[0].get("film_aspect") or (long_px / max(short_px, 1))
        film_stats[film] = {
            "rs": rs, "n": n, "longs": longs, "shorts": shorts,
            "long_px": long_px, "short_px": short_px,
            "bucket_aspect": max(aspect, 1.0 / aspect),
        }

    # Format-Gruppen bilden: Filme nach normiertem Seitenverhaeltnis sortiert
    # zu Ketten zusammenfassen, statt in ein festes Raster zu runden (ein
    # festes Raster kann zwei fast gleiche Aspects auf verschiedene Bucket-
    # Grenzen verteilen, z.B. 1.027 und 1.081 bei 0.1-Rasterung).
    BUCKET_TOL = 0.08
    ordered = sorted(film_stats.items(), key=lambda kv: kv[1]["bucket_aspect"])
    bucket_id, prev_aspect = -1, None
    for _film, st_ in ordered:
        a = st_["bucket_aspect"]
        if prev_aspect is None or abs(a - prev_aspect) / prev_aspect > BUCKET_TOL:
            bucket_id += 1
        st_["bucket"] = bucket_id
        prev_aspect = a

    # --- Cross-Film-Sanity: Format-Gruppen ueber die gesamte Charge poolen ---
    # Grundannahme (Nutzer): dieselbe Digitalisierungs-Rigg fuer alle Rollen
    # -> Filme mit gleichem Seitenverhaeltnis sollten (fast) dieselbe
    # physische Bildgroesse in Pixel haben. Weicht der Konsens eines
    # einzelnen Films deutlich (>3%) nach OBEN vom Konsens der uebrigen
    # Filme im selben Format ab, ist seine Rohverteilung vermutlich
    # systematisch zu gross (beobachtet: die Kantensuche rutscht auf
    # manchen Rollen auf eine staerkere, aber falsche Kante - Nachbarframe/
    # Filmhalter statt Bildrand). Nur nach OBEN wird gedeckelt: ein
    # kleinerer eigener Wert bleibt unangetastet, da _cluster_center()
    # Fehldetektionen typischerweise verkleinert, nicht vergroessert -
    # dieser Fall ist bereits durch den robusten Median abgedeckt.
    MIN_POOL_SUPPORT = 10  # Bilder aus ANDEREN Filmen, damit der Pool zaehlt
    CLAMP_TOL = 1.03
    for film, st_ in film_stats.items():
        pool_longs, pool_shorts, pool_films = [], [], set()
        for f2, st2 in film_stats.items():
            if f2 != film and st2["bucket"] == st_["bucket"]:
                pool_longs.extend(st2["longs"])
                pool_shorts.extend(st2["shorts"])
                pool_films.add(f2)
        if len(pool_longs) < MIN_POOL_SUPPORT:
            continue
        pool_long = _cluster_center(pool_longs)
        pool_short = _cluster_center(pool_shorts)
        if st_["long_px"] > pool_long * CLAMP_TOL:
            report(f"BATCHINFO consensus_clamp film={film!r} long "
                  f"{st_['long_px']:.0f}->{pool_long:.0f} "
                  f"(Pool {len(pool_longs)} Bilder aus {sorted(pool_films)})")
            st_["long_px"] = pool_long
        if st_["short_px"] > pool_short * CLAMP_TOL:
            report(f"BATCHINFO consensus_clamp film={film!r} short "
                  f"{st_['short_px']:.0f}->{pool_short:.0f} "
                  f"(Pool {len(pool_shorts)} Bilder aus {sorted(pool_films)})")
            st_["short_px"] = pool_short

    plans = []  # Start-Box je Bild, gesammelt ueber alle Filme (fuer Phase 2)
    for film, st_ in film_stats.items():
        rs = st_["rs"]
        n = st_["n"]
        long_px = st_["long_px"]
        short_px = st_["short_px"]
        mad_long = _median([abs(v - long_px) for v in st_["longs"]])
        mad_short = _median([abs(v - short_px) for v in st_["shorts"]])

        # Dominante Orientierung der Rolle (Mehrheit der Roh-Detektionen).
        # Fuer Ausreisser, deren Groesse weit vom Konsens liegt, ist die
        # Orientierung der Einzeldetektion nicht vertrauenswuerdig.
        n_landscape = sum(1 for r in rs
                          if r["_raw"]["width"] >= r["_raw"]["height"])
        film_landscape = n_landscape * 2 >= n

        # Relativ-Zentrum der "guten" Detektionen (Groesse nahe Konsens) als
        # Positions-Prior fuer die Ausreisser.
        good_cx, good_cy = [], []
        for r in rs:
            rw, rh = r["_raw"]["width"], r["_raw"]["height"]
            if (abs(max(rw, rh) - long_px) <= 0.15 * long_px
                    and abs(min(rw, rh) - short_px) <= 0.15 * short_px):
                iw, ih = r["_img_w"], r["_img_h"]
                good_cx.append((r["_raw"]["x"] + rw / 2) / iw)
                good_cy.append((r["_raw"]["y"] + rh / 2) / ih)
        cx_rel = _median(good_cx) if good_cx else None
        cy_rel = _median(good_cy) if good_cy else None

        spread = mad_long / max(long_px, 1) + mad_short / max(short_px, 1)
        film_trust = max(0.4, 1.0 - spread * 2) * min(1.0, n / 8.0)

        for r in rs:
            iw, ih = r["_img_w"], r["_img_h"]
            rw, rh = r["_raw"]["width"], r["_raw"]["height"]
            size_ok = (abs(max(rw, rh) - long_px) <= 0.20 * long_px
                       and abs(min(rw, rh) - short_px) <= 0.20 * short_px)

            # Orientierung: aus der Einzeldetektion, wenn deren Groesse passt,
            # sonst aus der Rolle.
            # Bild-Orientierung entscheidet: auf 209 Referenzbildern stimmt die Crop-Orientierung
            # in 208 Faellen mit der des Bildes ueberein (die Einzeldetektion lag in 12 Faellen
            # falsch: Querformat-Box in Hochformatbild). Nur bei fast quadratischem Bild
            # (Mittelformat 6x6) zaehlt weiter die Detektion bzw. die Rolle.
            if abs(iw - ih) > 0.1 * max(iw, ih):
                portrait = ih > iw
            else:
                portrait = (rh > rw) if size_ok else (not film_landscape)
            w = min(int(round(short_px if portrait else long_px)), iw)
            h = min(int(round(long_px if portrait else short_px)), ih)

            raw_cx = r["_raw"]["x"] + rw / 2
            raw_cy = r["_raw"]["y"] + rh / 2
            pos_ok = (cx_rel is None
                      or (abs(raw_cx / iw - cx_rel) < 0.12
                          and abs(raw_cy / ih - cy_rel) < 0.12))
            if size_ok and pos_ok or cx_rel is None:
                cx, cy = raw_cx, raw_cy
            else:
                cx, cy = cx_rel * iw, cy_rel * ih

            x = int(round(max(0, min(iw - w, cx - w / 2))))
            y = int(round(max(0, min(ih - h, cy - h / 2))))

            # Teure lokale Einpassung (Bildladen + Kantensuche) erst nach
            # der Filmschleife und parallel ueber alle Bilder ausfuehren -
            # hier nur die Start-Box + die Werte fuer die Konfidenz merken.
            plans.append({
                "r": r, "x": x, "y": y, "w": w, "h": h, "portrait": portrait,
                "long_px": long_px, "short_px": short_px, "rw": rw, "rh": rh,
                "film_trust": film_trust, "n": n,
                "mad_long": mad_long, "mad_short": mad_short,
            })

    if not plans:
        return

    items = [(load_paths.get(p["r"]["input_file"], p["r"]["input_file"]),
             p["x"], p["y"], p["w"], p["h"]) for p in plans]
    report(f"BATCHINFO consensus_refine={len(items)}")
    with ProcessPoolExecutor(max_workers=_worker_count(len(items))) as ex:
        refined = list(ex.map(_consensus_worker, items, chunksize=1))

    for p, res in zip(plans, refined):
        r = p["r"]
        try:
            x, y, w, h, edge_score = res
        except Exception as e:
            report(f"WARN consensus {r.get('filename')}: {e}")
            x, y, w, h, edge_score = p["x"], p["y"], p["w"], p["h"], 0.0

        long_px, short_px = p["long_px"], p["short_px"]
        r["x"], r["y"], r["width"], r["height"] = x, y, w, h
        r["orientation"] = "Portrait" if p["portrait"] else "Landscape"

        size_dev = (abs(max(p["rw"], p["rh"]) - long_px) / max(long_px, 1)
                    + abs(min(p["rw"], p["rh"]) - short_px) / max(short_px, 1))
        size_agree = max(0.0, 1.0 - size_dev)
        # Konfidenz: Groessenuebereinstimmung mit dem Rollen-Konsens und Kantenklarheit, gedeckelt
        # durch den Belichtungsfaktor (kontrastarme/unterbelichtete Bilder: die Kantensuche ist dort
        # unzuverlaessig, auch bei hohem Groessen-/Kanten-Score). film_trust bleibt nur Diagnose.
        # Geschichte: Mit 98 Referenzen (7 Filme) drueckte der Belichtungsdeckel richtige Crops
        # nach gelb/rot ohne Praezisionsgewinn und wurde entfernt (Version size+edge-v2). Mit allen
        # 209 Testfotos (9 Filme, alle von Hand gecroppt) kehrt sich das um: bei den Produktions-
        # schwellen (gruen >= 0.5) sinken die falschen Gruenen von 11 auf 1 (139 statt 183 Gruene,
        # Praezision 94.0 % -> 99.3 %; AUC 0.813 -> 0.848, ohne Film 34 0.802 -> 0.871). Nachvoll-
        # ziehbar mit: tools/calibrate.py --loo --from-json <eval-Rohdaten>
        base_conf = 0.5 * size_agree + 0.5 * edge_score

        # Unterbelichtete/kontrastarme Aufnahmen: Pass A findet dort kaum
        # oder keine Bild/Rand-Grenze (siehe measure_image_aspect), was auch
        # die Kantensuche in Pass B/Konsens unzuverlaessig macht - selbst bei
        # hohem Groessen-/Kanten-Score. Ohne Bezug zu einer Referenz gibt
        # es kein anderes Signal dafuer; die Konfidenz wird daher gedeckelt,
        # statt den (moeglicherweise falschen) Crop als sicher zu markieren.
        contrast = r.get("_exposure_contrast")
        if contrast is None:
            exposure_factor = 0.45
        else:
            contrast = float(contrast)
            exposure_factor = min(1.0, max(0.45, 0.45 + 0.55 * (contrast - 8) / 22))
        conf = base_conf * exposure_factor

        r["confidence"] = float(round(min(1.0, max(0.0, conf)), 3))
        # Fuer die Konfidenz-Kalibrierung (tools/calibrate.py): die
        # Einzelfaktoren unveraendert mitgeben, statt sie nur zur fertigen
        # Zahl zu verrechnen.
        r["_conf_parts"] = {
            "size_agree": round(size_agree, 4),
            "edge_score": round(float(edge_score), 4),
            "film_trust": round(p["film_trust"], 4),
            "exposure_factor": round(exposure_factor, 4),
        }
        if exposure_factor < 1.0:
            r.setdefault("reasons", []).append(
                f"Kontrastarm/unterbelichtet (Kontrast "
                f"{'n/a' if contrast is None else int(contrast)}) "
                f"- Konfidenz gedeckelt (x{exposure_factor:.2f})")
        r["conf_formula"] = "size+edge*exposure-v3"
        r.setdefault("reasons", []).append(
            f"Film-Konsens {int(long_px)}x{int(short_px)} "
            f"(n={p['n']}, MAD {int(p['mad_long'])}/{int(p['mad_short'])})")


def compute_batch(image_paths, confidence_threshold, debug, default_format,
                  report=None):
    """Reine Rechen-Pipeline: Pass A -> Film-Aspect -> Pass B -> Film-Konsens.

    Ohne Seiteneffekte (keine Queue, kein stdout). ``report(msg)`` erhaelt
    Fortschritts-/Info-Zeilen; Default schreibt sie nach stderr, wie es das
    Lua-Backend erwartet. Liefert
    ``{"film_aspects": {film: {...}}, "results": [...]}``.
    """
    import statistics as st
    from collections import defaultdict

    if report is None:
        def report(msg):
            print(msg, file=sys.stderr, flush=True)

    # RAW-Vorkonvertierung mit darktable-cli (kein zusaetzliches Python-Paket)
    raw_paths = [p for p in image_paths
                 if os.path.splitext(p)[1].lower() in RAW_EXTENSIONS]
    n_export = 0
    export_dir = None
    load_paths = {}
    if raw_paths:
        dt_cli = find_darktable_cli()
        if dt_cli:
            n_export = len(raw_paths)
            export_dir = tempfile.mkdtemp(prefix="autocrop_dt_")
        else:
            report("WARN: RAW-Dateien vorhanden, aber darktable-cli nicht "
                   "im PATH gefunden")

    # Fortschritt fuer Darktable-Lua-Job (stderr -> io.popen-Pipe):
    # RAW-Export + Pass A + Pass B
    total_units = 2 * max(len(image_paths), 1) + n_export

    if export_dir:
        report(f"BATCHINFO raw_export={n_export}")
        load_paths = export_raws_via_darktable(
            dt_cli, raw_paths, export_dir, total_units)

    done_units = n_export
    report(f"BATCHINFO total={len(image_paths)}")

    # === Pass A: Aspekte messen (parallel ueber Bilder) ===
    measurements = []
    pass_a_items = []
    for path in image_paths:
        if not os.path.exists(path):
            done_units += 1
            report(f"PROGRESS {done_units}/{total_units}")
            report(f"WARN: missing: {path}")
            continue
        pass_a_items.append((path, load_paths.get(path, path)))

    if pass_a_items:
        with ProcessPoolExecutor(max_workers=_worker_count(len(pass_a_items))) as ex:
            futures = {ex.submit(_pass_a_worker, it): it for it in pass_a_items}
            for fut in as_completed(futures):
                done_units += 1
                report(f"PROGRESS {done_units}/{total_units}")
                try:
                    measurements.append(fut.result())
                except Exception as e:
                    report(f"WARN: {futures[fut][0]}: {e}")

    # === Film-Aspekte bestimmen ===
    by_film = defaultdict(list)
    for meas in measurements:
        m = meas["measurement"]
        if m.get("valid"):
            a = m["aspect"]
            by_film[meas["film"]].append({
                "aspect": a if a >= 1 else 1 / a,
                "contrast": abs(m.get("contrast", 0)),
            })

    film_aspects = {}
    MIN_VALIDE = 3
    CONSENSUS_TOL = 0.10
    for film, items in by_film.items():
        if len(items) >= MIN_VALIDE:
            items.sort(key=lambda x: -x["contrast"])
            best = items[0]["aspect"]
            consensus = [x["aspect"] for x in items
                         if abs(x["aspect"] - best) / best <= CONSENSUS_TOL]
            med = st.median(consensus) if consensus else best
            film_aspects[film] = {
                "aspect_ratio": round(med, 4),
                "n_valid": len(items),
                "source": "measured",
            }
        else:
            film_aspects[film] = {
                "aspect_ratio": ASPECT_RATIOS.get(default_format, 1.5),
                "n_valid": len(items),
                "source": "fallback",
            }

    # === Pass B: Volle Erkennung mit Film-Aspects (parallel ueber Bilder) ===
    results = []
    pass_b_items = []
    for meas in measurements:
        film = meas["film"]
        fa = film_aspects.get(film, {})
        aspect = fa.get("aspect_ratio", ASPECT_RATIOS.get(default_format, 1.5))
        m = meas["measurement"]
        exposure_contrast = m.get("contrast") if m.get("valid") else None
        pass_b_items.append((film, meas["path"],
                             load_paths.get(meas["path"], meas["path"]),
                             aspect, debug, exposure_contrast))

    if pass_b_items:
        with ProcessPoolExecutor(max_workers=_worker_count(len(pass_b_items))) as ex:
            futures = {ex.submit(_pass_b_worker, it): it for it in pass_b_items}
            for fut in as_completed(futures):
                done_units += 1
                report(f"PROGRESS {done_units}/{total_units}")
                try:
                    results.append(fut.result())
                except Exception as e:
                    report(f"WARN: {futures[fut][1]}: {e}")

    # === Film-Konsens: konstante Bildgroesse je Rolle nutzen ===
    # Annahme: ein Ordner = eine Filmrolle, gleiche Kamera/Abstand -> die
    # Crop-Groesse (lange/kurze Kante) ist ueber die Rolle nahezu konstant
    # (gemessen: MAD ~10px). Der robuste Median ueberlebt >50% Fehldetektion.
    apply_film_consensus(results, load_paths, report=report)

    for r in results:
        r["needs_review"] = r["confidence"] < confidence_threshold

    # Temporaere darktable-Exporte aufraeumen
    if export_dir:
        shutil.rmtree(export_dir, ignore_errors=True)

    return {"film_aspects": film_aspects, "results": results}


def _run_batch_pipeline(image_paths, t_yellow, t_green, debug, default_format):
    """Batch-Einstieg fuers Darktable-Lua-Backend: ruft compute_batch und
    schreibt zusaetzlich die crop_queue.json + BATCHDONE.

    Python ist hier die alleinige Quelle fuer die Gruen/Gelb/Rot-Einstufung
    und den Queue-Inhalt (nicht Lua): Lua pollt nur ereignisgesteuert
    (selection-changed, mouse-over, ...) und kann daher verzoegert oder gar
    nicht mehr laufen, wenn der Nutzer waehrend der Erkennung nichts
    anfasst - der fertige Batch MUSS trotzdem eine nutzbare Queue
    hinterlassen. Lua liest zur Finalisierung nur noch das Ergebnis-JSON
    fuer die Colorlabels, schreibt die Queue selbst nicht mehr."""
    # PID fuer den Abbruch-Knopf im Lua-Backend (kill -TERM <pid>)
    print(f"BATCHINFO pid={os.getpid()}", file=sys.stderr, flush=True)

    batch = compute_batch(image_paths, t_yellow, debug, default_format)
    results = batch["results"]
    film_aspects = batch["film_aspects"]

    # === Queue + BATCHDONE: atomar, band-basiert (rot wird NICHT gequeued) ===
    try:
        qpath = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                             "crop_queue.json")
        existing = {}
        try:
            with open(qpath, "r") as f:
                existing = json.load(f)
        except Exception:
            existing = {}
        if not isinstance(existing, dict):
            existing = {}
        n_green = n_yellow = n_red = 0
        for r in results:
            if r.get("x") is None or r.get("width") is None:
                continue
            conf = r.get("confidence", 0.0)
            if conf >= t_green:
                band = "green"
            elif conf >= t_yellow:
                band = "yellow"
            else:
                band = "red"
            if band == "red":
                existing.pop(r["filename"], None)
                n_red += 1
                continue
            existing[r["filename"]] = {
                "x": round(r["x"], 4), "y": round(r["y"], 4),
                "w": round(r["width"], 4), "h": round(r["height"], 4),
                "band": band,
            }
            if band == "green":
                n_green += 1
            else:
                n_yellow += 1
        parts = []
        for fn, c in existing.items():
            parts.append('"%s":{"x":%.4f,"y":%.4f,"w":%.4f,"h":%.4f,'
                         '"band":"%s"}'
                         % (fn.replace('\\', '\\\\').replace('"', '\\"'),
                            c["x"], c["y"], c["w"], c["h"], c["band"]))
        tmp_q = qpath + ".tmp"
        with open(tmp_q, "w") as f:
            f.write("{" + ",".join(parts) + "}")
        os.replace(tmp_q, qpath)
        queue_msg = f"BATCHDONE green={n_green} yellow={n_yellow} red={n_red}"
        queue_info_msg = f"BATCHINFO queue={qpath}"
    except Exception as e:
        queue_msg = "BATCHDONE green=0 yellow=0 red=0"
        queue_info_msg = f"BATCHERROR queue_write: {e}"

    # Reihenfolge wichtig: Lua pollt die Fortschrittsdatei (stderr) und
    # liest, sobald dort BATCHDONE auftaucht, sofort die JSON-Datei
    # (stdout). print() auf eine in eine Datei umgeleitete stdout ist
    # NICHT zeilengepuffert wie ein Terminal, sondern voll gepuffert -
    # ohne explizites flush() konnte die JSON-Datei zum Zeitpunkt von
    # BATCHDONE noch leer sein (Race, beobachtet als "BATCHDONE, aber
    # JSON unlesbar" im Lua-Log). Deshalb: JSON zuerst schreiben+flushen,
    # BATCHDONE als allerletztes Signal danach.
    output = {
        "film_aspects": {k: v["aspect_ratio"]
                         for k, v in film_aspects.items()},
        "results": results,
    }
    print(json.dumps(output, indent=2, ensure_ascii=False), flush=True)
    print(queue_msg, file=sys.stderr, flush=True)
    print(queue_info_msg, file=sys.stderr, flush=True)





def main():
    parser = argparse.ArgumentParser(
        description="Erkennt den Filmrahmen auf einer digitalen Ablichtung.")
    parser.add_argument("image", nargs="?", default=None,
                        help="Pfad zum Ablichtungsbild (Einzelmodus)")
    parser.add_argument("--batch", nargs="+", metavar="FILE",
                        help="Batch-Modus: Mehrere Bilder auf einmal verarbeiten")
    parser.add_argument("--format", default=DEFAULT_FORMAT,
                        choices=list(ASPECT_RATIOS.keys()),
                        help=f"Filmformat (Standard: {DEFAULT_FORMAT})")
    parser.add_argument("--aspect-ratio", type=float, default=None,
                        help="Seitenverhältnis überschreiben (z.B. 1.0 für "
                             "1:1, 1.5 für 3:2)")
    # 0.50: kalibriert via tools/calibrate.py auf den 98 Referenz-Crops -
    # ab hier 100% Precision (keine Fehltreffer unter den bekannten Faellen)
    # bei deutlich mehr Abdeckung als der alte Default 0.70 (Recall 65%->76%).
    parser.add_argument("--confidence-threshold", type=float, default=0.5)
    # Nur im --batch-Modus relevant: zweite (obere) Schwelle fuer die
    # gruen/gelb/rot-Queue-Einstufung (--confidence-threshold ist dort die
    # gelb/rot-Grenze). Default ebenfalls aus tools/calibrate.py.
    parser.add_argument("--t-green", type=float, default=0.5)
    parser.add_argument("--debug", action="store_true")
    parser.add_argument("--output", "-o", help="Debug-Visualisierung speichern")
    parser.add_argument("--dump-candidates", action="store_true",
                        help="Alle Kandidaten ausgeben (fuer Film-Analyse)")
    parser.add_argument("--skip-refine", action="store_true",
                        help="Stufe 2 ueberspringen")
    parser.add_argument("--film-border-level", type=float, default=None,
                        help="Filmweite Border-Schwelle (fuer Pass 2)")
    parser.add_argument("--no-aspect-penalty", action="store_true",
                        help="Aspect-Strafe deaktivieren (fuer Film-Analyse)")
    parser.add_argument("--measure-aspect", action="store_true",
                        help="Nur Bild-Aspect messen (Pass A fuer Film-Analyse)")

    args = parser.parse_args()
    target_ratio = args.aspect_ratio if args.aspect_ratio is not None \
        else ASPECT_RATIOS[args.format]

    if not args.batch and args.image is None:
        parser.error("Entweder --batch oder ein Bild-Pfad erforderlich")

    if args.batch:
        # Batch-Modus: Alle Bilder in einem Durchlauf verarbeiten
        _run_batch_pipeline(args.batch, args.confidence_threshold,
                            args.t_green, args.debug, args.format)
        return

    if args.image is None:
        parser.error("Kein Bild-Pfad angegeben")

    img = load_image(args.image)
    gray = to_gray(img)

    target_ratio = args.aspect_ratio if args.aspect_ratio is not None \
        else ASPECT_RATIOS[args.format]

    if args.measure_aspect:
        # Pass A: Nur Bild-Aspect messen (fuer Film-Analyse)
        m = measure_image_aspect(gray)
        film = os.path.basename(os.path.dirname(os.path.abspath(args.image)))
        output = {
            "film": film,
            "filename": os.path.basename(args.image),
            "measurement": m,
        }
        print(json.dumps(output, indent=2, ensure_ascii=False))
        return

    if args.dump_candidates:
        # Dump-Modus: alle Kandidaten + verfeinertes Ergebnis als JSON
        dump = find_best_crop(gray, target_ratio=target_ratio,
                              debug=args.debug, dump_all=True,
                              no_aspect_penalty=args.no_aspect_penalty)
        film = os.path.basename(os.path.dirname(os.path.abspath(args.image)))
        output = {
            "film": film,
            "filename": os.path.basename(args.image),
            "candidates": dump["candidates"],
            "best_refined": dump["best_refined"],
        }
        # Zusaetzlich Stage 2 mit target_aspect=1.0 fuer Film-Analyse
        if abs(target_ratio - 1.0) > 0.05:
            dump2 = find_best_crop(gray, target_ratio=1.0,
                                   debug=False, dump_all=False,
                                   skip_refine=False)
            output["best_refined_square"] = {
                "width": dump2["width"], "height": dump2["height"],
                "aspect": round(dump2["width"] / max(dump2["height"], 1), 4)
            }
        print(json.dumps(output, indent=2, ensure_ascii=False))
        return

    result = find_best_crop(gray, target_ratio=target_ratio,
                            debug=args.debug,
                            film_border_level=args.film_border_level,
                            no_aspect_penalty=args.no_aspect_penalty)

    result["input_file"] = os.path.abspath(args.image)
    result["_img_w"] = gray.shape[1]
    result["_img_h"] = gray.shape[0]
    result["format"] = args.format
    result["target_aspect_ratio"] = target_ratio
    result["needs_review"] = result["confidence"] < args.confidence_threshold

    print(json.dumps(result, indent=2, ensure_ascii=False))

    if args.output:
        debug_img = img.copy()
        x, y, rw, rh = result["x"], result["y"], result["width"], result["height"]
        color = (0, 0, 255) if result["needs_review"] else (0, 255, 0)
        cv2.rectangle(debug_img, (x, y), (x + rw, y + rh), color, 3)
        label = f"conf={result['confidence']:.2f}"
        orientation = result.get("orientation", "")
        if orientation:
            label += f" {orientation}"
        if result["needs_review"]:
            label += " REVIEW!"
        cv2.putText(debug_img, label, (x, y - 10),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.8, color, 2)
        cv2.imwrite(args.output, debug_img)

    sys.exit(0 if result["confidence"] >= args.confidence_threshold else 1)


if __name__ == "__main__":
    main()

