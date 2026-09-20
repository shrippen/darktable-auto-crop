#!/usr/bin/env python3
"""Wie gut trennt ein Signal richtige von falschen Crops? (Roadmap Phase 2/3)

Fuer jedes Referenzbild (tools/eval.py-Rohdaten + Ground Truth) wird eine Reihe von Signalen
berechnet und als AUC "Treffer > Fehltreffer" ausgegeben: 1.0 = trennt perfekt, 0.5 = Zufall,
< 0.5 = das Signal ist bei Fehltreffern sogar HOEHER als bei Treffern (irrefuehrend).

Enthalten sind die vorhandenen Faktoren der Konfidenz, Werte aus dem Detektor sowie zwei in
Phase 2 neu getestete, vom Helligkeitsgradienten unabhaengige Inhaltssignale:
  content   Textur (lokale Standardabweichung) direkt INNEN vs. AUSSEN am Crop-Rand
  agree     Uebereinstimmung der Randlage mit einer unabhaengig aus dem Texturprofil gefundenen Kante
sowie zwei Phase-3-Kandidaten (Position gegenueber dem Film-Zentrum).

  tools/eval.py --json /tmp/eval.json      # Rohdaten erzeugen
  tools/signal_probe.py /tmp/eval.json
"""
import json
import os
import re
import statistics as st
import sys

import cv2
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import eval as ev                                    # noqa: E402


def auc(pos, neg):
    if not pos or not neg:
        return float("nan")
    n = t = 0
    for p in pos:
        for q in neg:
            t += 1
            n += 1 if p > q else 0.5 if p == q else 0
    return n / t


def _activity(gray):
    g = cv2.GaussianBlur(gray.astype(np.float32), (0, 0), 1.2)
    m, m2 = cv2.blur(g, (7, 7)), cv2.blur(g * g, (7, 7))
    return np.sqrt(np.maximum(m2 - m * m, 0))


def content_signals(path, x, y, w, h, scale=0.5):
    g = cv2.imread(path, cv2.IMREAD_GRAYSCALE)
    g = cv2.resize(g, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
    A = _activity(g)
    H, W = A.shape
    x, y, w, h = [int(round(v * scale)) for v in (x, y, w, h)]
    b = max(6, int(0.04 * min(w, h)))

    def band(x0, x1, y0, y1):
        x0, x1, y0, y1 = max(0, x0), min(W, x1), max(0, y0), min(H, y1)
        return float(A[y0:y1, x0:x1].mean()) if x1 - x0 >= 3 and y1 - y0 >= 3 else None
    ins = {"L": band(x, x + b, y + b, y + h - b), "R": band(x + w - b, x + w, y + b, y + h - b),
           "T": band(x + b, x + w - b, y, y + b), "B": band(x + b, x + w - b, y + h - b, y + h)}
    out = {"L": band(x - b, x, y + b, y + h - b), "R": band(x + w, x + w + b, y + b, y + h - b),
           "T": band(x + b, x + w - b, y - b, y), "B": band(x + b, x + w - b, y + h, y + h + b)}
    ratios = [ins[k] / (ins[k] + out[k] + 1e-3) for k in "LRTB" if ins[k] is not None and out[k] is not None]
    content = float(np.mean(sorted(ratios)[:2])) if len(ratios) >= 2 else 0.5
    dev = []
    for k in "LRTB":
        if k in "LR":
            prof, pos, L = A[y + h // 10: y + h - h // 10, :].mean(axis=0), (x if k == "L" else x + w), W
        else:
            prof, pos, L = A[:, x + w // 10: x + w - w // 10].mean(axis=1), (y if k == "T" else y + h), H
        win = int(0.08 * (w if k in "LR" else h))
        a, bb = max(0, pos - win), min(L, pos + win)
        if bb - a < 8:
            continue
        seg = cv2.GaussianBlur(prof[a:bb].astype(np.float32).reshape(-1, 1), (0, 0), 2.0).ravel()
        grad = np.gradient(seg)
        idx = int(np.argmax(grad)) if k in "LT" else int(np.argmin(grad))
        dev.append(abs((a + idx) - pos))
    span = 0.03 * max(w, h)
    agree = float(np.mean([max(0.0, 1 - d / span) for d in dev])) if dev else 0.0
    return content, agree


def main():
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    with open(sys.argv[1]) as f:
        data = json.load(f)
    strong, _ = ev.load_ground_truth()
    rows = [r for r in ev.collect_deltas(data["results"], strong) if not r["missing"]]
    by = {r["filename"]: r for r in data["results"]}
    films = {}
    for r in data["results"]:
        films.setdefault(r["film"], []).append(r)
    centre = {f: (st.median([(r["x"] + r["width"] / 2) / r["_img_w"] for r in rs]),
                  st.median([(r["y"] + r["height"] / 2) / r["_img_h"] for r in rs])) for f, rs in films.items()}
    feats = []
    for d in rows:
        film, fn = d["key"].split("/", 1)
        r = by[fn]
        parts, evd = r.get("_conf_parts") or {}, r.get("evidence") or {}
        m = re.search(r"(\d+) Strategien stimmen", " ".join(r.get("reasons", [])))
        cx, cy = centre[film]
        content, agree = content_signals(os.path.join(ev.TESTPHOTOS, film, fn), r["x"], r["y"], r["width"], r["height"])
        feats.append((ev.is_hit(d), {
            "confidence": r["confidence"], "size_agree": parts.get("size_agree"),
            "edge_score": parts.get("edge_score"), "film_trust": parts.get("film_trust"),
            "exposure_factor": parts.get("exposure_factor"), "n_strategien": int(m.group(1)) if m else 0,
            "geom_score": r.get("geom_score"), "evidence.edge": evd.get("edge"),
            "evidence.interior": evd.get("interior"), "evidence.exterior": evd.get("exterior"),
            "content (Phase 2)": content, "agree (Phase 2)": agree,
            "pos_naehe_filmzentrum (Phase 3)": -max(abs((r["x"] + r["width"] / 2) / r["_img_w"] - cx),
                                                     abs((r["y"] + r["height"] / 2) / r["_img_h"] - cy)),
            "outlier_anteil_film (Phase 3)": -sum(1 for q in films[film] if (q.get("_conf_parts") or {}).get("size_agree", 1) < 0.8) / len(films[film]),
        }))
    H = [f for h, f in feats if h]
    M = [f for h, f in feats if not h]
    print(f"{len(H)} Treffer, {len(M)} Fehltreffer\n")
    print(f"{'Signal':<36} {'AUC':>6}  {'Median Treffer':>15} {'Median Fehltr.':>15}")
    for k in H[0]:
        a = [f[k] for f in H if f[k] is not None]
        b = [f[k] for f in M if f[k] is not None]
        if a and b:
            print(f"{k:<36} {auc(a, b):6.3f}  {st.median(a):15.3f} {st.median(b):15.3f}")
    print("\nAUC 0.5 = Zufall; < 0.5 = bei Fehltreffern hoeher als bei Treffern (irrefuehrend).")
    print("Achtung: sehr wenige Fehltreffer -> AUC ist grob (+-0.1).")


if __name__ == "__main__":
    main()
