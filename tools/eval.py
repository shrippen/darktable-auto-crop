#!/usr/bin/env python3
"""Regressions-Harness fuer den Auto-Crop-Detektor.

Faehrt die Batch-Pipeline (auto_crop_negative.py --batch) ueber die Film-
ordner mit manuellen Referenz-Crops und vergleicht das Ergebnis mit
review_data/reviews.json.

Ausgabe:
  - Trefferquote gesamt + pro Film (Kriterium: |dW| < TOL und |dH| < TOL)
  - systematischer Bias (Median dx/dy/dw/dh)
  - 3-Wege-Klassifikation gruen/gelb/rot gegen "Treffer"
  - ROC-Sweep fuer die gruen-Schwelle

Exit-Code 1, wenn die Gesamt-Trefferzahl unter der Baseline
(tools/eval_baseline.json) liegt.

Beispiele:
  tools/eval.py                      # Standardlauf, Vergleich mit Baseline
  tools/eval.py --update-baseline    # aktuelles Ergebnis als neue Baseline
  tools/eval.py --holdout            # Kontaktabzug fuer Film 33/34 (ohne GT)
"""
import argparse
import json
import os
import statistics as st
import subprocess
import sys
import tempfile
from collections import defaultdict

PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TESTPHOTOS = os.path.join(PROJECT_DIR, "Testphotos")
SCRIPT = os.path.join(PROJECT_DIR, "auto_crop_negative.py")
REVIEWS = os.path.join(PROJECT_DIR, "review_data", "reviews.json")
BASELINE = os.path.join(PROJECT_DIR, "tools", "eval_baseline.json")

# Ein automatischer Crop gilt als Treffer, wenn Breite UND Hoehe weniger
# als TOL_PX von der manuellen Referenz abweichen (Nutzer-Vorgabe).
TOL_PX = 60

IMG_EXT = (".jpg", ".jpeg", ".tif", ".tiff", ".png")


def python_bin():
    """Interpreter mit cv2/numpy: bevorzugt die Projekt-Venv."""
    venv = os.path.join(PROJECT_DIR, ".venv", "bin", "python")
    return venv if os.path.exists(venv) else sys.executable


def film_images(film):
    d = os.path.join(TESTPHOTOS, film)
    if not os.path.isdir(d):
        return []
    return [os.path.join(d, f) for f in sorted(os.listdir(d))
            if f.lower().endswith(IMG_EXT)]


def run_pipeline(image_paths):
    """auto_crop_negative.py --batch aufrufen, JSON-Ergebnis liefern.

    Laeuft in einem Temp-Verzeichnis, damit die crop_queue.json des Skripts
    das Repo nicht anfasst. Fortschritt (stderr) wird durchgereicht.
    """
    # auto_crop_negative.py schreibt seine crop_queue.json neben das Skript
    # (nicht ins cwd). Fuer die Eval nicht erwuenscht -> hinterher entfernen,
    # aber eine echte Queue eines laufenden Plugins nicht anfassen.
    queue_path = os.path.join(PROJECT_DIR, "crop_queue.json")
    queue_pre = os.path.exists(queue_path)

    # stdout (grosses JSON) in eine Datei umleiten, nicht in eine Pipe:
    # sonst laeuft der Pipe-Puffer voll und der Kindprozess blockiert,
    # waehrend wir nur stderr lesen -> Deadlock.
    with tempfile.TemporaryDirectory(prefix="autocrop_eval_") as tmp:
        out_path = os.path.join(tmp, "out.json")
        cmd = [python_bin(), SCRIPT, "--batch", *image_paths,
               "--confidence-threshold", "0.0"]
        tty = sys.stderr.isatty()
        with open(out_path, "w") as out_f:
            proc = subprocess.Popen(cmd, cwd=tmp, stdout=out_f,
                                    stderr=subprocess.PIPE, text=True)
            for line in proc.stderr:
                if not line.startswith("PROGRESS"):
                    continue
                done = line.split()[1].split("/")[0]
                if tty:
                    print("  " + line.strip() + "   ", end="\r", file=sys.stderr)
                elif done.isdigit() and int(done) % 25 == 0:
                    print("  " + line.strip(), file=sys.stderr)
            proc.wait()
        if tty:
            print(" " * 40, end="\r", file=sys.stderr)

        if not queue_pre and os.path.exists(queue_path):
            os.remove(queue_path)
        if proc.returncode != 0:
            sys.exit(f"Pipeline fehlgeschlagen (rc={proc.returncode})")
        with open(out_path) as f:
            return json.load(f)


def collect_deltas(results, reviews):
    """Pro Referenzbild die Abweichung Auto-Crop <-> manuell berechnen."""
    by_name = {r["filename"]: r for r in results}
    rows = []
    for key, rev in reviews.items():
        mc = rev.get("manual_crop")
        if not mc:
            continue
        film, fname = key.split("/", 1)
        r = by_name.get(fname)
        if not r or r.get("x") is None:
            rows.append({"key": key, "film": film, "missing": True})
            continue
        rows.append({
            "key": key, "film": film, "missing": False,
            "dx": r["x"] - mc["x"], "dy": r["y"] - mc["y"],
            "dw": r["width"] - mc["width"], "dh": r["height"] - mc["height"],
            "conf": r["confidence"],
        })
    return rows


def is_hit(row):
    return (not row["missing"]
            and abs(row["dw"]) < TOL_PX and abs(row["dh"]) < TOL_PX)


def print_per_film(rows):
    print(f"\n=== Treffer pro Film (|dW|,|dH| < {TOL_PX}px) ===")
    by_film = defaultdict(list)
    for row in rows:
        by_film[row["film"]].append(row)
    per_film = {}
    total_hit = 0
    for film in sorted(by_film):
        items = by_film[film]
        hits = sum(is_hit(r) for r in items)
        per_film[film] = hits
        total_hit += hits
        miss_keys = [r["key"].split("/")[1] for r in items if not is_hit(r)]
        tail = ("  " + " ".join(miss_keys)) if miss_keys else ""
        print(f"  {film:<10} {hits:2d}/{len(items):<2d}{tail}")
    print(f"  {'GESAMT':<10} {total_hit:2d}/{len(rows)}")
    return total_hit, per_film


def print_bias(rows):
    ok = [r for r in rows if not r["missing"]]
    if not ok:
        return
    print("\n=== Systematischer Bias (Median, alle Referenzbilder) ===")
    for ax in ("dx", "dy", "dw", "dh"):
        vals = [r[ax] for r in ok]
        print(f"  {ax}: median={st.median(vals):+6.1f}  "
              f"mean={st.mean(vals):+7.1f}  "
              f"[{min(vals):+d}, {max(vals):+d}]")


def print_three_way(rows, t_green, t_yellow):
    """Konfusion gruen/gelb/rot gegen Treffer.

    gruen  = auto-crop, als sicher markiert  -> Miss hier ist der teure Fehler
    gelb   = auto-crop, zur Kontrolle        -> Miss hier ist tolerierbar
    rot    = nicht gecroppt                  -> Miss hier ist korrekt erkannt
    """
    ok = [r for r in rows if not r["missing"]]
    cells = {("green", 1): 0, ("green", 0): 0, ("yellow", 1): 0,
             ("yellow", 0): 0, ("red", 1): 0, ("red", 0): 0}
    for r in ok:
        band = ("green" if r["conf"] >= t_green
                else "yellow" if r["conf"] >= t_yellow else "red")
        cells[(band, int(is_hit(r)))] += 1
    print(f"\n=== 3-Wege  (gruen>={t_green:.2f}, gelb>={t_yellow:.2f}) ===")
    print(f"  {'Band':<8} {'Treffer':>8} {'Miss':>6}")
    for band in ("green", "yellow", "red"):
        print(f"  {band:<8} {cells[(band, 1)]:>8} {cells[(band, 0)]:>6}")
    g_total = cells[("green", 1)] + cells[("green", 0)]
    if g_total:
        prec = cells[("green", 1)] / g_total
        print(f"  -> gruen-Precision {prec:.1%} ({g_total} Bilder automatisch "
              f"'sicher')")


def print_roc(rows):
    """Sweep der gruen-Schwelle: Precision und Abdeckung."""
    ok = [r for r in rows if not r["missing"]]
    total_hits = sum(is_hit(r) for r in ok)
    print("\n=== ROC gruen-Schwelle ===")
    print(f"  {'t':>5} {'gruen':>6} {'davon Tr.':>10} {'Precision':>10} "
          f"{'Recall':>8}")
    suggestion = None
    for t in [i / 20 for i in range(20, 4, -1)]:
        greens = [r for r in ok if r["conf"] >= t]
        if not greens:
            continue
        hits = sum(is_hit(r) for r in greens)
        prec = hits / len(greens)
        recall = hits / total_hits if total_hits else 0
        mark = ""
        if suggestion is None and prec >= 0.95 and len(greens) >= 5:
            suggestion = t
            mark = "  <- Vorschlag (Precision >= 95%)"
        print(f"  {t:>5.2f} {len(greens):>6} {hits:>10} {prec:>9.1%} "
              f"{recall:>7.1%}{mark}")
    if suggestion is None:
        print("  (keine Schwelle erreicht 95% Precision)")
    return suggestion


def check_baseline(total_hit, total, per_film, update):
    current = {"total_hits": total_hit, "total": total,
               "tolerance_px": TOL_PX, "per_film": per_film}
    if update:
        with open(BASELINE, "w") as f:
            json.dump(current, f, indent=2)
        print(f"\nBaseline aktualisiert: {total_hit}/{total}")
        return 0
    if not os.path.exists(BASELINE):
        print("\nKeine Baseline vorhanden. Mit --update-baseline anlegen.")
        return 0
    with open(BASELINE) as f:
        base = json.load(f)

    # Nur die tatsaechlich gelaufenen Filme mit der Baseline vergleichen,
    # damit Teillaeufe (--films) nicht faelschlich als Regression zaehlen.
    base_pf = base.get("per_film", {})
    base_hit = sum(base_pf.get(f, 0) for f in per_film)
    print(f"\nBaseline (gelaufene Filme): {base_hit}   jetzt: {total_hit}")
    for f in sorted(per_film):
        delta = per_film[f] - base_pf.get(f, 0)
        mark = "" if delta == 0 else f"  ({delta:+d})"
        print(f"  {f:<10} {base_pf.get(f, 0)} -> {per_film[f]}{mark}")
    if total_hit < base_hit:
        print(f"REGRESSION: {base_hit - total_hit} Treffer weniger.")
        return 1
    if total_hit > base_hit:
        print("Verbesserung. Mit --update-baseline festschreiben.")
    return 0


def holdout_contact_sheet(out_path):
    """Overlay-Kontaktabzug fuer Filme ohne Referenz (visuelle Kontrolle)."""
    import cv2
    import numpy as np

    films = [f for f in sorted(os.listdir(TESTPHOTOS))
             if os.path.isdir(os.path.join(TESTPHOTOS, f))
             and f not in _films_with_gt()]
    paths = [p for f in films for p in film_images(f)]
    if not paths:
        print("Keine Holdout-Filme gefunden.")
        return
    print(f"Holdout: {len(paths)} Bilder aus {', '.join(films)}")
    data = run_pipeline(paths)
    by_name = {r["filename"]: r for r in data["results"]}

    thumbs = []
    tw = 320
    for p in paths:
        img = cv2.imread(p)
        if img is None:
            continue
        r = by_name.get(os.path.basename(p))
        h, w = img.shape[:2]
        s = tw / w
        small = cv2.resize(img, (tw, int(h * s)))
        if r and r.get("x") is not None:
            x, y = int(r["x"] * s), int(r["y"] * s)
            x2 = int((r["x"] + r["width"]) * s)
            y2 = int((r["y"] + r["height"]) * s)
            col = (0, 200, 0) if r["confidence"] >= 0.7 else (0, 165, 255)
            cv2.rectangle(small, (x, y), (x2, y2), col, 2)
            cv2.putText(small, f"{r['confidence']:.2f}", (4, 18),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.6, col, 2)
        thumbs.append(small)

    th = max(t.shape[0] for t in thumbs)
    thumbs = [cv2.copyMakeBorder(t, 0, th - t.shape[0], 0, 0,
                                 cv2.BORDER_CONSTANT, value=(30, 30, 30))
              for t in thumbs]
    cols = 6
    rows_img = []
    for i in range(0, len(thumbs), cols):
        chunk = thumbs[i:i + cols]
        while len(chunk) < cols:
            chunk.append(np.full_like(thumbs[0], 30))
        rows_img.append(np.hstack(chunk))
    cv2.imwrite(out_path, np.vstack(rows_img))
    print(f"Kontaktabzug: {out_path}")


def _films_with_gt():
    with open(REVIEWS) as f:
        return {k.split("/")[0] for k in json.load(f)}


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--films", help="Kommaliste, z.B. '27,30' (Default: alle "
                                    "mit Referenz-Crops)")
    ap.add_argument("--t-green", type=float, default=0.75,
                    help="Schwelle gruen (Default 0.75)")
    ap.add_argument("--t-yellow", type=float, default=0.50,
                    help="Schwelle gelb (Default 0.50)")
    ap.add_argument("--update-baseline", action="store_true")
    ap.add_argument("--holdout", action="store_true",
                    help="Kontaktabzug fuer Filme ohne Referenz")
    ap.add_argument("--json", help="Ergebnis-Rohdaten hierhin schreiben")
    args = ap.parse_args()

    if args.holdout:
        holdout_contact_sheet(args.holdout if isinstance(args.holdout, str)
                              else "/tmp/autocrop_holdout.png")
        return

    with open(REVIEWS) as f:
        reviews = json.load(f)

    gt_films = sorted({k.split("/")[0] for k in reviews})
    if args.films:
        want = {f"Film {n.strip()}" for n in args.films.split(",")}
        gt_films = [f for f in gt_films if f in want]
        reviews = {k: v for k, v in reviews.items()
                   if k.split("/")[0] in gt_films}

    # Ganze Filmrollen einspeisen (Film-Aspect-Konsens braucht alle Bilder),
    # bewertet werden nur die Bilder mit Referenz-Crop.
    paths = [p for f in gt_films for p in film_images(f)]
    print(f"Filme: {', '.join(gt_films)}  ({len(paths)} Bilder, "
          f"{len(reviews)} mit Referenz)")

    data = run_pipeline(paths)
    if args.json:
        with open(args.json, "w") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

    print(f"\nFilm-Aspects: {data.get('film_aspects', {})}")
    rows = collect_deltas(data["results"], reviews)

    missing = [r["key"] for r in rows if r["missing"]]
    if missing:
        print(f"\nWARNUNG: {len(missing)} Referenzbilder ohne Ergebnis: "
              f"{', '.join(missing)}")

    total_hit, per_film = print_per_film(rows)
    print_bias(rows)
    print_three_way(rows, args.t_green, args.t_yellow)
    print_roc(rows)

    rc = check_baseline(total_hit, len(rows), per_film, args.update_baseline)
    sys.exit(rc)


if __name__ == "__main__":
    main()
