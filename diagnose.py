#!/usr/bin/env python3
"""Diagnose: Zeigt Erkennungsschritte als Bilder.
Setup: Halter (weiss) -> Film (schwarz) -> Bild (grau)"""
import cv2
import numpy as np
import sys
import os

def diagnose(path):
    img = cv2.imread(path)
    if img is None:
        print(f"Fehler: {path} nicht gefunden")
        return
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    h, w = gray.shape
    print(f"Bild: {w}x{h}")
    out = "/tmp/diagnose"
    os.makedirs(out, exist_ok=True)

    # ── Schritt 1: Filmstreifen im Halter finden ──
    _, thresh = cv2.threshold(gray, 0, 255,
                              cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    inv = cv2.bitwise_not(thresh)
    k1 = cv2.getStructuringElement(cv2.MORPH_RECT, (15, 15))
    closed = cv2.morphologyEx(inv, cv2.MORPH_CLOSE, k1, iterations=3)
    closed = cv2.morphologyEx(closed, cv2.MORPH_OPEN, k1, iterations=2)
    cv2.imwrite(f"{out}/01_thresh.jpg", closed)

    contours, _ = cv2.findContours(closed, cv2.RETR_EXTERNAL,
                                   cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        print("Kein Filmstreifen gefunden!")
        return
    cnt = max(contours, key=cv2.contourArea)
    xf, yf, wf, hf = cv2.boundingRect(cnt)
    print(f"Film: x={xf} y={yf} w={wf} h={hf} ({wf*hf/(w*h)*100:.1f}%)")

    mask = np.zeros(gray.shape, dtype=np.uint8)
    cv2.drawContours(mask, [cnt], -1, 255, -1)
    cv2.imwrite(f"{out}/02_film_mask.jpg", mask)
    d1 = img.copy()
    cv2.rectangle(d1, (xf, yf), (xf+wf, yf+hf), (0,255,0), 3)
    cv2.imwrite(f"{out}/03_film_rect.jpg", d1)

    # ── Schritt 2: Helligkeitsprofil im Film ──
    film_gray = cv2.bitwise_and(gray, gray, mask=mask)
    film_px = gray[mask > 0]
    print(f"Film-Helligkeit: min={film_px.min()} max={film_px.max()} "
          f"mean={film_px.mean():.0f}")

    # Variance-Map: Bildinhalt hat mehr Variation als Filmrand
    blur = cv2.GaussianBlur(film_gray, (21, 21), 0)
    diff = cv2.absdiff(film_gray, blur)
    cv2.imwrite(f"{out}/04_variance.jpg", np.clip(diff * 5, 0, 255).astype(np.uint8))

    # ── Schritt 3: Helligkeits-Maske ──
    f_min, f_max = float(film_px.min()), float(film_px.max())
    thresh_bright = f_min + (f_max - f_min) * 0.25
    _, bright = cv2.threshold(film_gray, int(thresh_bright), 255,
                              cv2.THRESH_BINARY)
    bright = cv2.bitwise_and(bright, mask)
    k2 = cv2.getStructuringElement(cv2.MORPH_RECT, (50, 50))
    bright = cv2.morphologyEx(bright, cv2.MORPH_CLOSE, k2, iterations=3)
    bright = cv2.morphologyEx(bright, cv2.MORPH_OPEN, k2, iterations=1)
    cv2.imwrite(f"{out}/05_bright_mask.jpg", bright)

    c3, _ = cv2.findContours(bright, cv2.RETR_EXTERNAL,
                              cv2.CHAIN_APPROX_SIMPLE)
    if c3:
        c_best = max(c3, key=cv2.contourArea)
        xb, yb, wb, hb = cv2.boundingRect(c_best)
        print(f"Heller Bereich: x={xb} y={yb} w={wb} h={hb}")
        d2 = img.copy()
        cv2.rectangle(d2, (xf, yf), (xf+wf, yf+hf), (0,255,0), 2)
        cv2.rectangle(d2, (xb, yb), (xb+wb, yb+hb), (0,0,255), 3)
        cv2.putText(d2, "Bild", (xb, yb-10),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0,0,255), 2)
        cv2.imwrite(f"{out}/06_combined.jpg", d2)

    # ── Schritt 4: Spalten-/Zeilen-Profil ──
    strip = gray[yf:yf+hf, xf:xf+wf]
    col_mean = np.mean(strip, axis=0)
    row_mean = np.mean(strip, axis=1)
    print(f"\nSpalten-Profil (links): {[f'{v:.0f}' for v in col_mean[:15]]}")
    print(f"Spalten-Profil (rechts): {[f'{v:.0f}' for v in col_mean[-15:]]}")
    print(f"Zeilen-Profil (oben): {[f'{v:.0f}' for v in row_mean[:15]]}")
    print(f"Zeilen-Profil (unten): {[f'{v:.0f}' for v in row_mean[-15:]]}")

    # ── Schritt 5: Sprossenlocherkennung ──
    # Sprossen = sehr dunkle Bereiche am Rand des Films
    dark_thresh = f_min + (f_max - f_min) * 0.15
    _, dark_mask = cv2.threshold(film_gray, int(dark_thresh), 255,
                                 cv2.THRESH_BINARY_INV)
    dark_mask = cv2.bitwise_and(dark_mask, mask)
    cv2.imwrite(f"{out}/07_dark_mask.jpg", dark_mask)

    # Spaltenweise dunkle Pixel zählen
    dark_per_col = np.sum(dark_mask > 0, axis=0)
    dark_per_row = np.sum(dark_mask > 0, axis=1)
    print(f"\nDunkel pro Spalte (links): {[int(v) for v in dark_per_col[:20]]}")
    print(f"Dunkel pro Spalte (rechts): {[int(v) for v in dark_per_col[-20:]]}")

    print(f"\nAlle Diagnose-Bilder in: {out}/")

if __name__ == "__main__":
    diagnose(sys.argv[1])
