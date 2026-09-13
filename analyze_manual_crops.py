#!/usr/bin/env python3
"""Analysiert manuelle Crop-Korrekturen aus reviews.json.

Vergleicht auto-detect mit manuellen Korrekturen und zeigt
Statistiken zur Verbesserung der Erkennung.
"""
import json
import os
import sys
import numpy as np

REVIEW_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           "review_data", "reviews.json")
RESULTS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "review_data", "results.json")


def main():
    if not os.path.exists(REVIEW_FILE):
        print("Keine reviews.json gefunden. Erst Review-GUI nutzen.")
        sys.exit(1)

    with open(REVIEW_FILE) as f:
        reviews = json.load(f)
    with open(RESULTS_FILE) as f:
        results = json.load(f)

    results_map = {}
    for r in results:
        key = f"{r.get('film', '')}/{r.get('filename', '')}"
        results_map[key] = r

    manual_crops = {}
    for key, rev in reviews.items():
        mc = rev.get("manual_crop")
        if mc and key in results_map:
            auto = results_map[key]
            manual_crops[key] = {
                "auto": {"x": auto["x"], "y": auto["y"],
                         "w": auto["width"], "h": auto["height"]},
                "manual": mc,
                "method": auto.get("method", "?"),
                "confidence": auto.get("confidence", 0),
            }

    if not manual_crops:
        print("Keine manuellen Crop-Korrekturen gefunden.")
        print("Nutze die Review-GUI um Crop zu korrigieren (Ecke/Seite ziehen).")
        sys.exit(0)

    print(f"=== {len(manual_crops)} Manuelle Crop-Korrekturen ===\n")

    # Differenzen berechnen
    diffs = []
    for key, data in manual_crops.items():
        a = data["auto"]
        m = data["manual"]
        diff_x = m["x"] - a["x"]
        diff_y = m["y"] - a["y"]
        diff_w = m["width"] - a["w"]
        diff_h = m["height"] - a["h"]
        area_auto = a["w"] * a["h"]
        area_manual = m["width"] * m["height"]
        area_diff_pct = (area_manual - area_auto) / area_auto * 100 if area_auto else 0

        # Seitenverhältnis
        ratio_auto = a["w"] / max(a["h"], 1)
        ratio_manual = m["width"] / max(m["height"], 1)

        diffs.append({
            "key": key,
            "diff_x": diff_x, "diff_y": diff_y,
            "diff_w": diff_w, "diff_h": diff_h,
            "area_diff_pct": area_diff_pct,
            "ratio_auto": ratio_auto,
            "ratio_manual": ratio_manual,
            "method": data["method"],
            "confidence": data["confidence"],
            "auto": a, "manual": m,
        })

    # Einzelne Korrekturen anzeigen
    print("Details:")
    print(f"{'Bild':<50} {'Auto':>14} {'Manuell':>14} {'Delta':>14} {'Flaeche'}")
    print("-" * 110)
    for d in diffs:
        a = d["auto"]
        m = d["manual"]
        name = d["key"][-45:]
        auto_s = f"{a['w']}x{a['h']}"
        man_s = f"{m['width']}x{m['height']}"
        delta_s = f"{d['diff_w']:+d}x{d['diff_h']:+d}"
        area_s = f"{d['area_diff_pct']:+.1f}%"
        print(f"  {name:<48} {auto_s:>14} {man_s:>14} {delta_s:>14} {area_s:>8}")

    # Aggregierte Statistiken
    print(f"\n=== Aggregierte Statistiken ===")
    diff_xs = [d["diff_x"] for d in diffs]
    diff_ys = [d["diff_y"] for d in diffs]
    diff_ws = [d["diff_w"] for d in diffs]
    diff_hs = [d["diff_h"] for d in diffs]
    area_diffs = [d["area_diff_pct"] for d in diffs]

    print(f"\n  Position-Offset (Bild-Koordinaten):")
    print(f"    X: min={min(diff_xs):+d} max={max(diff_xs):+d} "
          f"mean={np.mean(diff_xs):+.1f} median={np.median(diff_xs):+.1f}")
    print(f"    Y: min={min(diff_ys):+d} max={max(diff_ys):+d} "
          f"mean={np.mean(diff_ys):+.1f} median={np.median(diff_ys):+.1f}")

    print(f"\n  Groessen-Aenderung:")
    print(f"    W: min={min(diff_ws):+d} max={max(diff_ws):+d} "
          f"mean={np.mean(diff_ws):+.1f} median={np.median(diff_ws):+.1f}")
    print(f"    H: min={min(diff_hs):+d} max={max(diff_hs):+d} "
          f"mean={np.mean(diff_hs):+.1f} median={np.median(diff_hs):+.1f}")

    print(f"\n  Flaeche: mean={np.mean(area_diffs):+.1f}% "
          f"median={np.median(area_diffs):+.1f}%")

    # Bias-Richtung
    print(f"\n  Systematischer Bias:")
    x_bias = np.mean(diff_xs)
    y_bias = np.mean(diff_ys)
    w_bias = np.mean(diff_ws)
    h_bias = np.mean(diff_hs)
    print(f"    X: {x_bias:+.1f}px ({'links verschieben' if x_bias < -2 else 'rechts verschieben' if x_bias > 2 else 'ok'})")
    print(f"    Y: {y_bias:+.1f}px ({'oben verschieben' if y_bias < -2 else 'unten verschieben' if y_bias > 2 else 'ok'})")
    print(f"    W: {w_bias:+.1f}px ({'breiter machen' if w_bias > 2 else 'schmaler machen' if w_bias < -2 else 'ok'})")
    print(f"    H: {h_bias:+.1f}px ({'hoeher machen' if h_bias > 2 else 'kleiner machen' if h_bias < -2 else 'ok'})")

    # Empfehlung
    print(f"\n=== Empfehlung fuer Algorithmus ===")
    if abs(w_bias) > 5 or abs(h_bias) > 5:
        print(f"  - Filmstreifen-Erkennung schneidet systematisch zu "
              f"{'breit' if w_bias > 0 else 'schmal'} / "
              f"{'hoch' if h_bias > 0 else 'niedrig'}")
    if abs(x_bias) > 5 or abs(y_bias) > 5:
        print(f"  - Position passt nicht: {x_bias:+.0f}px X, {y_bias:+.0f}px Y")

    # JSON-Export
    export_path = os.path.join(os.path.dirname(REVIEW_FILE),
                               "manual_crop_analysis.json")
    with open(export_path, "w") as f:
        json.dump(diffs, f, indent=2, ensure_ascii=False, default=str)
    print(f"\n  Detaillierte Analyse: {export_path}")


if __name__ == "__main__":
    main()
