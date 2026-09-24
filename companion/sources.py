"""Eingabequellen ohne darktable: ein Ordner mit Bildern.

- ``folder_job``:     Kalibrierung (JPEG/TIFF/PNG, Referenzen aus reviews.json)
- ``standalone_job``: eigenstaendige Nutzung (zusaetzlich RAWs; Ziel siehe targets/)
"""
import os

from .export import RAW_EXTENSIONS
from .session import crop_from_pixels, read_json, SessionError
from .targets import OUTPUT_MARKER
from .thumbs import image_size

IMG_EXT = (".jpg", ".jpeg", ".tif", ".tiff", ".png")
RAW_EXT = tuple(sorted(RAW_EXTENSIONS))


def _images_in(folder, exts=IMG_EXT):
    """Bilder direkt im Ordner oder in dessen Unterordnern (eine Ebene = Filmrollen).
    Ausgabeordner (mit OUTPUT_MARKER) werden uebersprungen, sonst kaemen Kopien wieder hinein."""
    found = []
    entries = sorted(os.listdir(folder))
    subdirs = [e for e in entries if os.path.isdir(os.path.join(folder, e))
               and not os.path.exists(os.path.join(folder, e, OUTPUT_MARKER))]
    dirs = [os.path.join(folder, d) for d in subdirs] or [folder]
    if any(e.lower().endswith(exts) for e in entries) and subdirs:
        dirs.insert(0, folder)
    for d in dirs:
        for f in sorted(os.listdir(d)):
            if f.lower().endswith(exts) and not f.startswith("."):
                found.append(os.path.join(d, f))
    return found


def _drop_raw_twins(paths):
    """RAW+JPEG der Kamera: nur das RAW behalten (gleicher Name im selben Ordner)."""
    raws = {os.path.splitext(p)[0].lower() for p in paths if p.lower().endswith(RAW_EXT)}
    return [p for p in paths if p.lower().endswith(RAW_EXT) or os.path.splitext(p)[0].lower() not in raws]


def standalone_job(folder, films=None, settings=None):
    """Job fuer die eigenstaendige Nutzung: alle Bilder und RAWs im Ordner (eine Ebene Rollen).
    Ziel, Konverter und Ausgabeordner setzt der Aufrufer."""
    folder = os.path.abspath(folder)
    if not os.path.isdir(folder):
        raise SessionError(f"Ordner nicht gefunden: {folder}", 404)
    paths = _drop_raw_twins(_images_in(folder, IMG_EXT + RAW_EXT))
    if films:
        paths = [p for p in paths if _film_matches(os.path.basename(os.path.dirname(p)), films)]
    if not paths:
        raise SessionError(f"keine Bilder in {folder}", 404)
    images = [{"id": i, "path": p, "film": os.path.basename(os.path.dirname(p))}
              for i, p in enumerate(paths, start=1)]
    return {"mode": "standalone", "folder": folder, "settings": settings or {}, "images": images}


def _film_matches(film, wanted):
    """'33' passt auf 'Film 33'; ein voller Name passt exakt."""
    return any(film == w or film.endswith(" " + w) for w in wanted)


def folder_job(folder, results=None, reviews=None, settings=None, films=None):
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
    # ``reviews``: eine Datei oder eine Liste. Die ERSTE ist die Schreib-Datei ("Fertig" schreibt dorthin);
    # weitere (z. B. feedback_gt.json) werden nur gelesen. Bei doppelten Bildern gewinnt die erste.
    review_files = [reviews] if isinstance(reviews, str) else list(reviews or [])
    revs = {}
    for rf in reversed(review_files):
        if os.path.exists(rf):
            for k, v in (read_json(rf, {}) or {}).items():
                if isinstance(v, dict) and (v.get("manual_crop") or v.get("is_problem")):
                    revs[k] = v
    images = []
    paths = _images_in(folder)
    if films:
        paths = [p for p in paths if _film_matches(os.path.basename(os.path.dirname(p)), films)]
        if not paths:
            raise SessionError(f"keine Bilder fuer Rollen {films} in {folder}", 404)
    for i, path in enumerate(paths, start=1):
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
                    "reasons": r.get("reasons", []), "orientation": r.get("orientation"),
                    "conf_parts": r.get("_conf_parts")}
            mc = (rev or {}).get("manual_crop")
            if mc:
                item["manual"] = {"crop": crop_from_pixels(
                    mc["x"], mc["y"], mc["width"], mc["height"], *size)}
            if (rev or {}).get("is_problem"):
                item["group_override"] = "red"
        images.append(item)
    return {"mode": "folder", "folder": folder, "results": results,
            "reviews": review_files[0] if review_files else None,
            "settings": settings or {}, "images": images}
