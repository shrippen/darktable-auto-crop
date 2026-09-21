#!/usr/bin/env python3
"""Vergleicht den Algorithmus mit den Crops, die du frueher in darktable gesetzt hast.

Quelle der Referenz: review_data/darktable_crops.json (erzeugt von tools/convert_testphotos.py aus den
Sidecar-XMPs). Der Crop steht dort als Bruchteil der Zeichenflaeche NACH `ashift`; bei Bildern mit Drehung wird er
mit denselben Formeln wie in der Web-UI (companion/session.py) in das ungedrehte Bild zurueckgerechnet
(Mitte zurueckdrehen, Groesse behalten). Kriterium wie in tools/eval.py: |dW| und |dH| < 60 px bei 2000 px langer Kante.

Achtung: Das sind aeltere Handarbeiten, keine bestaetigte Wahrheit (die liegt in review_data/reviews.json). Ein
Fehltreffer kann daher auch eine Referenz sein, die anders geschnitten wurde. Bilder, die schon in reviews.json stehen
(Film 27-35), werden hier ausgelassen, damit die Zahlen unabhaengig von der Abstimmung bleiben.

Beispiele:
  tools/eval_darktable_crops.py                       # alle Rollen mit darktable-Crops
  tools/eval_darktable_crops.py --rolls "Film 14" "Film 60"
  tools/eval_darktable_crops.py --no-rotated          # nur Bilder ohne Drehung (exakte Referenz)
  tools/eval_darktable_crops.py --json out.json       # Rohdaten sichern
  tools/eval_darktable_crops.py --from-json out.json  # ohne neue Berechnung auswerten
  tools/eval_darktable_crops.py --single              # jede Rolle einzeln verarbeiten (kein Stapel)

Die Erkennung nutzt die anderen Rollen eines Stapels (Cross-Film-Abgleich der Groesse). Mit --single zeigt sich, wie gut
sie ohne diese Stuetze ist, etwa wenn nur eine Rolle in darktable markiert wird. Stand 2026-09-21 auf 1871 Referenzen:
alle 71 Rollen im Stapel 73.1 % Treffer, jede Rolle einzeln 55.9 %; Stapel aus 5 Zufallsrollen streuen zwischen 1 % und 89 %.
"""
import argparse
import importlib.util
import json
import os
import sys
from collections import defaultdict

PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_DIR)
LABELS = os.path.join(PROJECT_DIR, "review_data", "darktable_crops.json")
REVIEWS = os.path.join(PROJECT_DIR, "review_data", "reviews.json")


def load_eval_module():
    spec = importlib.util.spec_from_file_location("acn_eval", os.path.join(PROJECT_DIR, "tools", "eval.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def image_size(path):
    from PIL import Image
    with Image.open(path) as im:
        return im.size


def reference_box(entry, size):
    """Referenz-Crop im ungedrehten Bild in Pixeln: {x, y, width, height}."""
    W, H = size
    crop = entry["crop"]
    rot = entry.get("rotation")
    if rot and abs(rot) > 1e-3:
        from companion.session import crop_from_straight
        crop = crop_from_straight(crop, (W, H), rot)
    l, t, r, b = crop
    return {"x": round(l * W), "y": round(t * H), "width": round((r - l) * W), "height": round((b - t) * H)}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--rolls", nargs="*", help="nur diese Rollen (Ordnernamen in Testphotos/)")
    ap.add_argument("--no-rotated", action="store_true", help="Bilder mit Drehung (ashift) auslassen")
    ap.add_argument("--no-complex", action="store_true", help="Bilder mit Perspektive/Shear in ashift auslassen")
    ap.add_argument("--t-green", type=float, default=0.5)
    ap.add_argument("--t-yellow", type=float, default=0.30)
    ap.add_argument("--target", type=float, default=0.98)
    ap.add_argument("--single", action="store_true", help="jede Rolle einzeln durch die Pipeline schicken (ohne Cross-Film-Abgleich)")
    ap.add_argument("--json", help="Pipeline-Rohdaten hierhin schreiben")
    ap.add_argument("--from-json", help="Pipeline-Rohdaten von hier lesen")
    args = ap.parse_args()

    ev = load_eval_module()
    labels = json.load(open(LABELS))
    known = set(json.load(open(REVIEWS)))
    refs, films = {}, defaultdict(list)
    for key, entry in labels.items():
        roll, fname = key.split("/", 1)
        if key in known or (args.rolls and roll not in args.rolls):
            continue
        if args.no_rotated and entry.get("rotation") and abs(entry["rotation"]) > 1e-3:
            continue
        if args.no_complex and entry.get("ashift_complex"):
            continue
        path = os.path.join(ev.TESTPHOTOS, key)
        if not os.path.exists(path):
            continue
        refs[key] = {"manual_crop": reference_box(entry, image_size(path))}
        films[roll].append(key)
    rolls = sorted(films)
    if not refs:
        sys.exit("keine Referenzen (erst tools/convert_testphotos.py laufen lassen?)")
    paths = [p for r in rolls for p in ev.film_images(r)]
    print(f"{len(rolls)} Rollen, {len(paths)} Bilder eingespeist, {len(refs)} mit darktable-Crop bewertet")

    if args.from_json:
        data = json.load(open(args.from_json))
    elif args.single:
        data = {"film_aspects": {}, "results": []}
        for i, roll in enumerate(rolls, 1):
            print(f"  [{i}/{len(rolls)}] {roll}", file=sys.stderr)
            part = ev.run_pipeline(ev.film_images(roll))
            data["film_aspects"].update(part.get("film_aspects", {}))
            data["results"].extend(part["results"])
        if args.json:
            with open(args.json, "w") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
    else:
        data = ev.run_pipeline(paths)
        if args.json:
            with open(args.json, "w") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)

    rows = ev.collect_deltas(data["results"], refs)
    total_hit, _per = ev.print_per_film(rows)
    ev.print_bias(rows)
    ev.print_three_way(rows, args.t_green, args.t_yellow)
    ev.print_roc(rows)
    ev.print_loo(rows, args.target)


if __name__ == "__main__":
    main()
