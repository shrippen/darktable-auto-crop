#!/usr/bin/env python3
"""Konfidenz-Kalibrierung anhand der 98 manuellen Referenz-Crops.

Die Konfidenzformel in auto_crop_negative.py (0.45*size_agree +
0.35*edge_score + 0.20*film_trust, dann * exposure_factor) war
handgeschaetzt. Dieses Script fittet stattdessen eine logistische
Regression auf denselben vier Faktoren gegen das tatsaechliche Ergebnis
(Treffer/Fehltreffer bei 60px Toleranz) und schlaegt kalibrierte
Gewichte + Gruen/Gelb-Schwellen vor.

Kein sklearn im Projekt -> Gradientenabstieg von Hand (4 Features, 98
Punkte, das ist trivial).

Nutzung:
  tools/calibrate.py                 # Gewichte + Schwellen vorschlagen
  tools/calibrate.py --apply         # Vorschlag in auto_crop_negative.py
                                      # eintragen (nur die Konstanten)
"""
import argparse
import json
import os
import re
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from eval import (PROJECT_DIR, REVIEWS, TOL_PX, film_images, run_pipeline)

SCRIPT = os.path.join(PROJECT_DIR, "auto_crop_negative.py")
FEATURES = ["size_agree", "edge_score", "film_trust", "exposure_factor"]


def collect_samples():
    """(X, y) fuer alle Referenzbilder: X = Konfidenz-Einzelfaktoren,
    y = 1 wenn |dW|,|dH| < TOL_PX (echter Treffer)."""
    with open(REVIEWS) as f:
        reviews = json.load(f)
    gt_films = sorted({k.split("/", 1)[0] for k in reviews})
    paths = [p for f in gt_films for p in film_images(f)]
    data = run_pipeline(paths)
    by_name = {r["filename"]: r for r in data["results"]}

    X, y, keys = [], [], []
    for key, rev in reviews.items():
        mc = rev.get("manual_crop")
        if not mc:
            continue
        _, fname = key.split("/", 1)
        r = by_name.get(fname)
        parts = r.get("_conf_parts") if r else None
        if not r or r.get("x") is None or not parts:
            continue
        hit = (abs(r["width"] - mc["width"]) < TOL_PX
               and abs(r["height"] - mc["height"]) < TOL_PX)
        X.append([parts[f] for f in FEATURES])
        y.append(1.0 if hit else 0.0)
        keys.append(key)
    return np.array(X), np.array(y), keys


def fit_logistic(X, y, l2=0.5, lr=0.5, iters=4000):
    """Simple Batch-Gradientenabstieg, Features vorher z-standardisiert.
    l2 haelt die Gewichte klein (98 Punkte, 4 Features + Bias - ohne
    Regularisierung neigt das zum Auswendiglernen)."""
    mu, sigma = X.mean(axis=0), X.std(axis=0) + 1e-9
    Xs = (X - mu) / sigma
    n, d = Xs.shape
    w = np.zeros(d)
    b = 0.0
    for _ in range(iters):
        z = Xs @ w + b
        p = 1.0 / (1.0 + np.exp(-z))
        grad_w = Xs.T @ (p - y) / n + l2 * w / n
        grad_b = np.mean(p - y)
        w -= lr * grad_w
        b -= lr * grad_b
    return w, b, mu, sigma


def score(X, w, b, mu, sigma):
    Xs = (X - mu) / sigma
    z = Xs @ w + b
    return 1.0 / (1.0 + np.exp(-z))


def best_threshold(scores, y, min_precision):
    """Niedrigste Schwelle, die noch min_precision erreicht (maximiert
    Recall bei gegebener Precision-Untergrenze) - deckt mehr Bilder
    automatisch ab als eine zu hoch gewaehlte Schwelle."""
    order = np.argsort(-scores)
    best_t, best_recall = None, -1
    total_hits = y.sum()
    for i in range(len(order)):
        t = scores[order[i]]
        sel = scores >= t
        prec = y[sel].sum() / sel.sum() if sel.sum() else 0
        recall = y[sel].sum() / total_hits if total_hits else 0
        if prec >= min_precision and recall > best_recall:
            best_t, best_recall = t, recall
    return best_t, best_recall


def apply_to_source(w_named, bias, mu, sigma, t_green, t_yellow):
    """Ersetzt NUR die vier Gewichte + exposure_factor-Rueckgriff durch
    die kalibrierten Werte. Die Formstruktur (linear + sigmoid) bleibt
    dieselbe wie die bisherige gewichtete Summe, damit der Rest der
    Pipeline (Refine, Konsens, Clamp) unangetastet bleibt."""
    with open(SCRIPT) as f:
        src = f.read()

    old = ('        conf = 0.45 * size_agree + 0.35 * edge_score '
           '+ 0.20 * p["film_trust"]')
    if old not in src:
        sys.exit("Konnte die Konfidenz-Zeile nicht finden - manuell pruefen.")

    w = dict(zip(FEATURES, w_named))
    new = (
        "        # Kalibriert via tools/calibrate.py auf den 98 Referenz-"
        "Crops\n"
        "        _cal_z = (\n"
        f"            {w['size_agree']:+.4f} * (size_agree - {mu[0]:.4f}) / {sigma[0]:.4f}\n"
        f"            + {w['edge_score']:+.4f} * (edge_score - {mu[1]:.4f}) / {sigma[1]:.4f}\n"
        f"            + {w['film_trust']:+.4f} * (p[\"film_trust\"] - {mu[2]:.4f}) / {sigma[2]:.4f}\n"
        f"            {bias:+.4f}\n"
        "        )\n"
        "        conf = 1.0 / (1.0 + np.exp(-_cal_z))"
    )
    src = src.replace(old, new)
    with open(SCRIPT, "w") as f:
        f.write(src)
    print(f"auto_crop_negative.py aktualisiert (Gewichte einsortiert).")
    print(f"Empfohlene Schwellen: --t-green {t_green:.3f} "
          f"--t-yellow {t_yellow:.3f}")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--apply", action="store_true",
                    help="Kalibrierte Formel in auto_crop_negative.py eintragen")
    ap.add_argument("--min-green-precision", type=float, default=0.95)
    ap.add_argument("--min-yellow-precision", type=float, default=0.80)
    args = ap.parse_args()

    print("Sammle Feature/Treffer-Paare (voller Batch-Lauf)...")
    X, y, keys = collect_samples()
    print(f"{len(y)} Referenzbilder, {int(y.sum())} Treffer "
          f"({y.mean():.1%})")

    w, b, mu, sigma = fit_logistic(X, y)
    print("\nKalibrierte Gewichte (auf standardisierten Features):")
    for name, wi in zip(FEATURES, w):
        print(f"  {name:<16} {wi:+.3f}")
    print(f"  {'bias':<16} {b:+.3f}")

    scores = score(X, w, b, mu, sigma)
    t_green, r_green = best_threshold(scores, y, args.min_green_precision)
    t_yellow, r_yellow = best_threshold(scores, y, args.min_yellow_precision)

    if t_green is None:
        print(f"\nKeine Schwelle erreicht {args.min_green_precision:.0%} "
              f"Precision fuer Gruen.")
        return
    print(f"\nGruen-Schwelle (>= {args.min_green_precision:.0%} Precision): "
          f"{t_green:.3f}  (Recall {r_green:.1%})")
    if t_yellow is not None:
        print(f"Gelb-Schwelle  (>= {args.min_yellow_precision:.0%} Precision): "
              f"{t_yellow:.3f}  (Recall {r_yellow:.1%})")

    # Vergleich zur alten handgestrickten Konfidenz
    old_conf = np.array([r for r in X[:, 0]])  # Platzhalter, nicht genutzt
    print("\n--- Vergleich zur alten Formel (auf denselben Daten) ---")
    old_scores = (0.45 * X[:, 0] + 0.35 * X[:, 1] + 0.20 * X[:, 2]) * X[:, 3]
    for name, s in (("alt", old_scores), ("kalibriert", scores)):
        sel = s >= (0.75 if name == "alt" else t_green)
        prec = y[sel].sum() / sel.sum() if sel.sum() else 0
        recall = y[sel].sum() / y.sum()
        print(f"  {name:<12} n_gruen={int(sel.sum()):3d}  "
              f"precision={prec:.1%}  recall={recall:.1%}")

    if args.apply:
        apply_to_source(w, b, mu, sigma, t_green,
                        t_yellow if t_yellow is not None else 0.5)


if __name__ == "__main__":
    main()
