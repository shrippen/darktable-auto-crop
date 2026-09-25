"""Zugeschnittene Kopien: ``<ausgabe>/<rolle>/<name>.<ext>`` plus crops.json/.csv.

Quelle ist das analysierte Bild (JPEG/TIFF/PNG selbst, bei RAWs der Export des Konverters).
Die Originale bleiben unberuehrt. 8-Bit-Bilder behalten ICC-Profil und EXIF (Orientierung auf
"normal", weil die Drehung schon angewendet ist); 16-Bit-TIFF/PNG behalten die Bittiefe, aber
ohne ICC/EXIF (OpenCV schreibt beides nicht).
"""
import os
import re

from . import Target, file_action, skip_message
from .manifest import ensure_out, write_manifest
from ..export import is_raw
from ..session import crop_to_pixels

KEEP_EXT = (".jpg", ".jpeg", ".png", ".tif", ".tiff")
JPEG_QUALITY = 95
TAG_ORIENTATION = 0x0112


class CopiesTarget(Target):
    name = "copies"
    straightens = True
    writes_out = True

    def apply(self, session, plan):
        out = ensure_out(session)
        report, copies, used = {}, {}, set()

        for e in plan["images"]:
            iid = str(e["id"])
            img = session.image(e["id"])
            rel = _copy_rel(img, used)
            dst = os.path.join(out, rel)
            action = file_action(e, os.path.exists(dst))

            if action == "skip":
                msg = skip_message(e)
                report[iid] = {"status": "skipped", **({"message": msg} if msg else {})}
                continue
            if action == "remove":
                os.remove(dst)
                report[iid] = {"status": "skipped", "message": "Kopie entfernt"}
                continue
            copies[iid] = rel
            if action == "keep":
                report[iid] = {"status": "ok", "message": "unveraendert"}
                continue

            src = session.export_path(img)
            try:
                note = write_copy(src, dst, e["crop"], e.get("angle"))
            except Exception as ex:      # noqa: BLE001 - je Bild melden
                copies.pop(iid, None)
                report[iid] = {"status": "error", "message": str(ex)}
                continue
            report[iid] = {"status": "ok", **({"message": note} if note else {})}

        write_manifest(session, plan, copies)
        return report


def _copy_rel(img, used):
    """Relativer Zielpfad; RAWs werden zu JPEG (der Export ist JPEG), Kollisionen erhalten die ID."""
    film = re.sub(r"[^\w.\- ]+", "_", img["film"]).strip() or "film"
    stem, ext = os.path.splitext(img["filename"])
    ext = ext.lower() if ext.lower() in KEEP_EXT and not is_raw(img["path"]) else ".jpg"
    rel = os.path.join(film, stem + ext)
    if rel in used:
        rel = os.path.join(film, f"{stem}__{img['id']}{ext}")
    used.add(rel)
    return rel


def rotate_expand(img, deg):
    """Um ``deg`` gegen den Uhrzeigersinn drehen, Flaeche = Bounding-Box (wie die Vorschau)."""
    import math
    import cv2
    h, w = img.shape[:2]
    t = math.radians(abs(deg))
    W, H = int(round(w * math.cos(t) + h * math.sin(t))), int(round(h * math.cos(t) + w * math.sin(t)))
    M = cv2.getRotationMatrix2D((w / 2.0, h / 2.0), deg, 1.0)
    M[0, 2] += W / 2.0 - w / 2.0
    M[1, 2] += H / 2.0 - h / 2.0
    return cv2.warpAffine(img, M, (W, H), flags=cv2.INTER_LANCZOS4, borderValue=0)


def write_copy(src, dst, crop, angle=None):
    """Schneidet ``src`` auf ``crop`` (normiert, nach Drehung um ``angle``) und schreibt ``dst``.
    Rueckgabe: Hinweis oder None."""
    import cv2
    if not src or not os.path.exists(src):
        raise RuntimeError("kein analysiertes Bild vorhanden")
    img = cv2.imread(src, cv2.IMREAD_ANYDEPTH | cv2.IMREAD_ANYCOLOR)    # wendet EXIF-Orientierung an
    if img is None:
        raise RuntimeError("Bild nicht lesbar")
    if angle:
        img = rotate_expand(img, angle)
    h, w = img.shape[:2]
    px = crop_to_pixels(crop, w, h)
    part = img[px["y"]:px["y"] + px["height"], px["x"]:px["x"] + px["width"]]

    os.makedirs(os.path.dirname(dst), exist_ok=True)
    ext = os.path.splitext(dst)[1].lower()
    tmp = dst + ".tmp" + ext
    if part.dtype.itemsize > 1:
        if not cv2.imwrite(tmp, part):
            raise RuntimeError("Schreiben fehlgeschlagen")
        os.replace(tmp, dst)
        return "16 Bit: ohne ICC-Profil/EXIF"
    _save_8bit(part, src, tmp, ext)
    os.replace(tmp, dst)
    return None


def _save_8bit(part, src, dst, ext):
    """Ueber Pillow speichern, damit ICC-Profil und EXIF der Quelle erhalten bleiben."""
    import cv2
    from PIL import Image

    if part.ndim == 3:
        part = cv2.cvtColor(part, cv2.COLOR_BGRA2RGBA if part.shape[2] == 4 else cv2.COLOR_BGR2RGB)
    out = Image.fromarray(part)
    kw = {}
    try:
        with Image.open(src) as im:
            if im.info.get("icc_profile"):
                kw["icc_profile"] = im.info["icc_profile"]
            exif = im.getexif()
            if exif:
                exif[TAG_ORIENTATION] = 1
                kw["exif"] = exif.tobytes()
    except Exception:       # noqa: BLE001 - Metadaten sind Beiwerk
        pass
    if ext in (".jpg", ".jpeg"):
        out.save(dst, "JPEG", quality=JPEG_QUALITY, **kw)
    elif ext in (".tif", ".tiff"):
        out.save(dst, "TIFF", compression="tiff_lzw", **kw)
    else:
        out.save(dst, "PNG", **kw)
