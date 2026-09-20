"""Vorschauen aus den darktable-Exporten (Pillow), mit Cache je Datei/Groesse."""
import io
import os
import threading

from PIL import Image, ImageOps

MIN_W, MAX_W = 64, 4096
_lock = threading.Lock()


def clamp_width(w):
    try:
        w = int(w)
    except (TypeError, ValueError):
        w = 320
    return max(MIN_W, min(MAX_W, w))


def thumb_bytes(src_path, cache_dir, key, width, straighten_deg=None):
    """JPEG-Bytes der Vorschau (lange Kante = ``width``). Exif-Orientierung wird
    angewendet, damit Anzeige und Erkennung (cv2 dreht ebenfalls) dieselben
    Koordinaten teilen. ``straighten_deg`` dreht den Inhalt um diesen Winkel gegen den Uhr-
    zeigersinn (Schraeglage geradestellen); die Flaeche waechst auf die Bounding-Box wie bei
    darktables "Drehen und Perspektive" ohne Zuschnitt, Ecken werden schwarz."""
    width = clamp_width(width)
    mtime = int(os.path.getmtime(src_path))
    tag = f"_s{straighten_deg:+.2f}" if straighten_deg else ""
    cache = os.path.join(cache_dir, f"{key}_{width}{tag}_{mtime}.jpg")
    if os.path.exists(cache):
        with open(cache, "rb") as f:
            return f.read()
    with Image.open(src_path) as im:
        im.draft("RGB", (width * 2, width * 2))    # schnelleres JPEG-Dekodieren
        im = ImageOps.exif_transpose(im).convert("RGB")
        im.thumbnail((width, width), Image.LANCZOS)
        if straighten_deg:
            im = im.rotate(straighten_deg, resample=Image.BICUBIC, expand=True, fillcolor=(0, 0, 0))
        buf = io.BytesIO()
        im.save(buf, "JPEG", quality=88)
    data = buf.getvalue()
    with _lock:
        os.makedirs(cache_dir, exist_ok=True)
        tmp = cache + ".tmp"
        with open(tmp, "wb") as f:
            f.write(data)
        os.replace(tmp, cache)
    return data


def image_size(path):
    """Groesse des Bildes so, wie sie die Erkennung sieht (Exif angewendet)."""
    with Image.open(path) as im:
        im = ImageOps.exif_transpose(im)
        return list(im.size)
