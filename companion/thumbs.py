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


def thumb_bytes(src_path, cache_dir, key, width):
    """JPEG-Bytes der Vorschau (lange Kante = ``width``). Exif-Orientierung wird
    angewendet, damit Anzeige und Erkennung (cv2 dreht ebenfalls) dieselben
    Koordinaten teilen."""
    width = clamp_width(width)
    mtime = int(os.path.getmtime(src_path))
    cache = os.path.join(cache_dir, f"{key}_{width}_{mtime}.jpg")
    if os.path.exists(cache):
        with open(cache, "rb") as f:
            return f.read()
    with Image.open(src_path) as im:
        im.draft("RGB", (width * 2, width * 2))    # schnelleres JPEG-Dekodieren
        im = ImageOps.exif_transpose(im).convert("RGB")
        im.thumbnail((width, width), Image.LANCZOS)
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
