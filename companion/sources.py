"""Eingabequellen: Ordner mit Bildern (Kalibrier-/Entwicklungsmodus ohne darktable)."""
import os

from .session import crop_from_pixels, read_json, SessionError
from .thumbs import image_size

IMG_EXT = (".jpg", ".jpeg", ".tif", ".tiff", ".png")


def _images_in(folder):
    """Bilder direkt im Ordner oder in dessen Unterordnern (eine Ebene = Filmrollen)."""
    found = []
    entries = sorted(os.listdir(folder))
    subdirs = [e for e in entries if os.path.isdir(os.path.join(folder, e))]
    dirs = [os.path.join(folder, d) for d in subdirs] or [folder]
    if any(e.lower().endswith(IMG_EXT) for e in entries) and subdirs:
        dirs.insert(0, folder)
    for d in dirs:
        for f in sorted(os.listdir(d)):
            if f.lower().endswith(IMG_EXT):
                found.append(os.path.join(d, f))
    return found


def folder_job(folder, results=None, reviews=None, settings=None):
    """Baut einen Job aus einem Bilderordner. ``results`` (review_data/results.json)
    liefert vorhandene Erkennungen, ``reviews`` (review_data/reviews.json) die
    manuellen Referenz-Crops und Problemmarkierungen."""
    folder = os.path.abspath(folder)
    if not os.path.isdir(folder):
        raise SessionError(f"Ordner nicht gefunden: {folder}", 404)
    res_by_path = {}
    if results and os.path.exists(results):
        for r in read_json(results, []) or []:
            p = r.get("full_path") or r.get("input_file")
            if p:
                res_by_path[os.path.abspath(p)] = r
    revs = read_json(reviews, {}) if reviews and os.path.exists(reviews) else {}
    images = []
    for i, path in enumerate(_images_in(folder), start=1):
        film = os.path.basename(os.path.dirname(path))
        item = {"id": i, "path": path, "film": film, "export": path}
        r = res_by_path.get(os.path.abspath(path))
        rev = revs.get(f"{film}/{os.path.basename(path)}")
        if r or rev:
            size = image_size(path)
            item["export_size"] = size
            if r and r.get("x") is not None and r.get("width"):
                item["detected"] = {
                    "crop": crop_from_pixels(r["x"], r["y"], r["width"], r["height"], *size),
                    "confidence": r.get("confidence", 0.0), "method": r.get("method"),
                    "reasons": r.get("reasons", []), "orientation": r.get("orientation")}
            mc = (rev or {}).get("manual_crop")
            if mc:
                item["manual"] = {"crop": crop_from_pixels(
                    mc["x"], mc["y"], mc["width"], mc["height"], *size)}
            if (rev or {}).get("is_problem"):
                item["group_override"] = "red"
        images.append(item)
    return {"mode": "folder", "folder": folder, "results": results,
            "reviews": reviews, "settings": settings or {}, "images": images}
