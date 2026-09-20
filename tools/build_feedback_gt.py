#!/usr/bin/env python3
"""Baut aus Companion-Sitzungen einen Ground-Truth-Datensatz fuer tools/eval.py.

Fuer jede Rolle (Film) der Sitzungen werden ALLE Bilder verkleinert nach
``<images-dir>/<Film>/<name>.jpg`` geschrieben (lange Kante 2000 px, wie die vorhandenen
Testfotos; der Film-Konsens braucht die ganze Rolle) und die Referenzen als
``review_data/feedback_gt.json`` abgelegt, Pixel bezogen auf die verkleinerten Bilder:

  manual_crop    vom Nutzer korrigierter Crop            (starke Referenz)
  accepted_crop  angewendeter, unkorrigierter Crop       (schwache Referenz: "akzeptiert",
                 nur so belastbar, wie der Nutzer das Bild angesehen hat)

Die Bilder sind private Fotos: nicht committen (siehe .git/info/exclude). Das JSON enthaelt
nur Koordinaten und Dateinamen.

  tools/build_feedback_gt.py                       # alle Sitzungen in ~/.cache/auto-crop-negative
  tools/build_feedback_gt.py --films "Film 35"
"""
import argparse
import json
import os
import sys

from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.dirname(HERE)
sys.path.insert(0, HERE)
sys.path.insert(0, PROJECT_DIR)

import feedback_report as fr           # noqa: E402
from companion import session as sess   # noqa: E402

LONG_EDGE = 2000


def build(sessions, images_dir, out_path, films=None, long_edge=LONG_EDGE):
    rows = [r for r in fr.collect(sessions) if r["export_path"] and os.path.exists(r["export_path"])]
    if films:
        rows = [r for r in rows if r["film"] in films]
    gt, n_img = {}, 0
    for r in rows:
        w, h = r["size"] or Image.open(r["export_path"]).size
        k = long_edge / float(max(w, h))
        stem = os.path.splitext(r["file"])[0]
        dst_dir = os.path.join(images_dir, r["film"])
        os.makedirs(dst_dir, exist_ok=True)
        dst = os.path.join(dst_dir, stem + ".jpg")
        if not os.path.exists(dst):
            with Image.open(r["export_path"]) as im:
                im.convert("RGB").resize((round(w * k), round(h * k)), Image.LANCZOS).save(
                    dst, quality=92)
        n_img += 1
        key = f"{r['film']}/{stem}.jpg"
        size = [round(w * k), round(h * k)]

        def scaled(crop_px):
            return {kk: int(round(v * k)) for kk, v in crop_px.items()}
        if r["corrected"] and "manual_px" in r:
            gt[key] = {"manual_crop": scaled(r["manual_px"]), "size": size,
                       "is_problem": r["final"] == "red" and r["moved"],
                       "source": f"companion:{r['session']}"}
        elif r["will_apply"] and r["has_detection"] and r["phase"] in ("applied", "locked"):
            det = _detected_px(r, w, h)
            gt[key] = {"accepted_crop": scaled(det), "size": size,
                       "source": f"companion:{r['session']}"}
    tmp = out_path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(gt, f, indent=1, ensure_ascii=False, sort_keys=True)
    os.replace(tmp, out_path)
    return n_img, gt


def _detected_px(r, w, h):
    # der erkannte Crop steht nur in state.json; collect() liefert ihn nicht direkt -> nachladen
    st = sess.read_json(os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(r["export_path"]))),
                                     "state.json"))
    for img in st["images"].values():
        if img.get("path") == r["path"]:
            return sess.crop_to_pixels(img["detected"]["crop"], w, h)
    raise KeyError(r["path"])


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", default=sess.DEFAULT_ROOT)
    ap.add_argument("--session", nargs="+")
    ap.add_argument("--films", nargs="+")
    ap.add_argument("--images-dir", default=os.path.join(PROJECT_DIR, "Testphotos"))
    ap.add_argument("--out", default=os.path.join(PROJECT_DIR, "review_data", "feedback_gt.json"))
    args = ap.parse_args()
    sessions = fr.load_sessions(args.session or sess.list_sessions(args.root))
    n_img, gt = build(sessions, args.images_dir, args.out, args.films)
    strong = sum(1 for v in gt.values() if "manual_crop" in v)
    print(f"{n_img} Bilder nach {args.images_dir}, {strong} starke + {len(gt) - strong} schwache "
          f"Referenzen -> {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
