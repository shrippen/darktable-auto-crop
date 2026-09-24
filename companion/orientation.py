"""EXIF-Orientierung einer RAW-Datei und Umrechnung von Crops zwischen Anzeige- und Sensorlage.

Die Erkennung arbeitet auf dem gedrehten (angezeigten) Bild. Manche Werkzeuge speichern den
Crop aber in der Sensorlage (Annahme fuer Adobe ``crs:Crop*``); dafuer braucht es die
Orientierung der RAW-Datei. Gelesen wird sie aus dem TIFF-Kopf (NEF, CR2, ARW, DNG, ORF,
PEF, RW2, ...) oder, falls vorhanden, ueber ``rawpy``. Unbekannt -> ``None``.

EXIF-Werte: 1 normal, 2 gespiegelt, 3 180 Grad, 4 vertikal gespiegelt, 5 transponiert,
6 zum Anzeigen 90 Grad im Uhrzeigersinn drehen, 7 transversal, 8 90 Grad gegen den Uhrzeigersinn.
"""
import struct

TAG_ORIENTATION = 0x0112
LIBRAW_FLIP_TO_EXIF = {0: 1, 3: 3, 5: 8, 6: 6}

# Anzeige (x, y) -> Sensorlage (x, y), alles normiert 0..1
_TO_STORED = {
    1: lambda x, y: (x, y),
    2: lambda x, y: (1 - x, y),
    3: lambda x, y: (1 - x, 1 - y),
    4: lambda x, y: (x, 1 - y),
    5: lambda x, y: (y, x),
    6: lambda x, y: (y, 1 - x),
    7: lambda x, y: (1 - y, 1 - x),
    8: lambda x, y: (1 - y, x),
}


def raw_orientation(path):
    """EXIF-Orientierung 1..8 oder None."""
    value = _tiff_orientation(path)
    if value:
        return value
    return _rawpy_orientation(path)


def crop_to_stored(crop, orientation):
    """Crop [l, t, r, b] in Anzeigelage -> Sensorlage (Orientierung None/unbekannt = 1)."""
    fn = _TO_STORED.get(orientation or 1, _TO_STORED[1])
    l, t, r, b = crop
    x0, y0 = fn(l, t)
    x1, y1 = fn(r, b)
    return [round(min(x0, x1), 6), round(min(y0, y1), 6), round(max(x0, x1), 6), round(max(y0, y1), 6)]


def _tiff_orientation(path):
    """Orientierung aus IFD0 eines TIFF-artigen Kopfes (II/MM, Magic wird nicht geprueft: ORF/RW2 weichen ab)."""
    try:
        with open(path, "rb") as f:
            head = f.read(8)
            if len(head) < 8 or head[:2] not in (b"II", b"MM"):
                return None
            end = "<" if head[:2] == b"II" else ">"
            f.seek(struct.unpack(end + "I", head[4:8])[0])
            count = struct.unpack(end + "H", f.read(2))[0]
            if count > 1000:
                return None
            for _ in range(count):
                entry = f.read(12)
                if len(entry) < 12:
                    return None
                tag, typ = struct.unpack(end + "HH", entry[:4])
                if tag != TAG_ORIENTATION or typ != 3:
                    continue
                value = struct.unpack(end + "H", entry[8:10])[0]
                return value if value in _TO_STORED else None
    except (OSError, struct.error):
        return None
    return None


def _rawpy_orientation(path):
    try:
        import rawpy
    except ImportError:
        return None
    try:
        with rawpy.imread(path) as raw:
            return LIBRAW_FLIP_TO_EXIF.get(raw.sizes.flip)
    except Exception:       # noqa: BLE001 - kein lesbares RAW: Orientierung bleibt unbekannt
        return None
