"""Werkzeugneutrale Ausgabe: crops.json und crops.csv im Ausgabeordner.

Koordinaten (auch in der Datei beschrieben):
  crop           normiert [links, oben, rechts, unten] im Bezugsrahmen; bei ``angle`` im um diesen
                 Winkel gegen den Uhrzeigersinn gedrehten Bild (Flaeche = Bounding-Box)
  crop_original  derselbe Crop ohne Drehung im Originalrahmen (achsparallel, gleiche Groesse)
  crop_px        crop_original in Pixeln von ``size``
  size           Bezugsrahmen: das analysierte Bild nach EXIF-Orientierung (bei RAWs der Export)
"""
import csv
import io
import os

from . import Target, OUTPUT_MARKER
from ..session import atomic_write_json, crop_to_pixels, now_iso

JSON_NAME = "crops.json"
CSV_NAME = "crops.csv"
CSV_COLUMNS = ("file", "film", "label", "apply", "confidence", "angle",
               "left", "top", "right", "bottom", "x", "y", "width", "height", "copy")
COORDINATES = ("crop: normalized [left, top, right, bottom]; if angle is set, relative to the image "
               "rotated counter-clockwise by angle degrees (canvas = bounding box). crop_original: "
               "the same crop without rotation in the original frame. crop_px: crop_original in "
               "pixels of size (image after EXIF orientation; for RAW files the converted export).")


class ManifestTarget(Target):
    name = "json"
    straightens = True      # Winkel steht in der Datei; der Verbraucher entscheidet
    writes_out = True

    def apply(self, session, plan):
        write_manifest(session, plan)
        return {str(e["id"]): ({"status": "ok"} if e["apply"]
                               else _skipped(e)) for e in plan["images"]}


def _skipped(entry):
    from . import skip_message
    msg = skip_message(entry)
    return {"status": "skipped", **({"message": msg} if msg else {})}


def ensure_out(session):
    out = session.out_dir
    os.makedirs(out, exist_ok=True)
    marker = os.path.join(out, OUTPUT_MARKER)
    if not os.path.exists(marker):
        with open(marker, "w", encoding="utf-8") as f:
            f.write("kader\n")
    return out


def write_manifest(session, plan, copies=None):
    """crops.json + crops.csv; ``copies`` = {id: relativer Pfad der Kopie}."""
    out = ensure_out(session)
    copies = copies or {}
    rows = []
    for e in plan["images"]:
        img = session.image(e["id"])
        size = img.get("export_size")
        orig = session.orig_crop(img)
        det = img.get("detected") or {}
        rows.append({
            "id": e["id"], "file": img["path"], "film": img["film"], "filename": img["filename"],
            "label": e["label"], "apply": e["apply"], "confidence": det.get("confidence"),
            "size": size, "angle": e.get("angle"), "crop": e.get("crop"), "crop_original": orig,
            "crop_px": crop_to_pixels(orig, *size) if orig and size else None,
            "copy": copies.get(str(e["id"])),
        })
    atomic_write_json(os.path.join(out, JSON_NAME), {
        "generator": "kader", "session": session.state["session"],
        "revision": plan["revision"], "written": now_iso(), "target": session.target_name,
        "coordinates": COORDINATES, "images": rows})

    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(CSV_COLUMNS)
    for r in rows:
        c = r["crop_original"] or [None] * 4
        px = r["crop_px"] or {}
        w.writerow([r["file"], r["film"], r["label"], int(bool(r["apply"])), r["confidence"], r["angle"] or "",
                    *c, px.get("x"), px.get("y"), px.get("width"), px.get("height"), r["copy"] or ""])
    tmp = os.path.join(out, CSV_NAME + ".tmp")
    with open(tmp, "w", encoding="utf-8", newline="") as f:
        f.write(buf.getvalue())
    os.replace(tmp, os.path.join(out, CSV_NAME))
    return out
