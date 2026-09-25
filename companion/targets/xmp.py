"""Adobe-XMP-Sidecar (Lightroom Classic, Camera Raw, Bridge): ``<name>.xmp`` neben dem RAW.

Geschrieben werden ``crs:HasCrop``, ``crs:CropLeft/Top/Right/Bottom`` (normiert), ``crs:CropAngle=0``
und das Farblabel ``xmp:Label`` (Red/Yellow/Green). Vorhandene Sidecars werden ergaenzt, alles
andere darin bleibt stehen.

Grenzen (siehe README):
- Die Crop-Werte liegen laut Annahme in der Sensorlage (vor der EXIF-Drehung); die Orientierung
  wird aus der RAW-Datei gelesen. Gegen Lightroom nicht geprueft.
- Kein Geradestellen: der Crop wird achsparallel im Originalrahmen uebertragen.
- Lightroom liest Sidecars nur fuer proprietaere RAWs, nicht fuer JPEG/TIFF/DNG (die tragen
  XMP in der Datei); solche Bilder werden uebersprungen. Lightroom uebernimmt geaenderte
  Sidecars erst mit "Metadaten aus Datei lesen".
"""
import os
import re
import xml.etree.ElementTree as ET

from . import Target, skip_message
from ..export import is_raw
from ..orientation import crop_to_stored, raw_orientation

NS = {
    "x": "adobe:ns:meta/",
    "rdf": "http://www.w3.org/1999/02/22-rdf-syntax-ns#",
    "crs": "http://ns.adobe.com/camera-raw-settings/1.0/",
    "xmp": "http://ns.adobe.com/xap/1.0/",
    "kdr": "urn:kader:1.0",
}
LABEL_NAMES = {"red": "Red", "yellow": "Yellow", "green": "Green"}
CROP_KEYS = ("HasCrop", "CropLeft", "CropTop", "CropRight", "CropBottom", "CropAngle")
TEMPLATE = (
    '<x:xmpmeta xmlns:x="adobe:ns:meta/">\n'
    ' <rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">\n'
    '  <rdf:Description rdf:about=""/>\n'
    ' </rdf:RDF>\n'
    '</x:xmpmeta>\n')
_ROOT_START = re.compile(r"<(?:[\w.-]+:)?(?:xmpmeta|RDF)\b")
_ROOT_END = re.compile(r"</(?:[\w.-]+:)?(?:xmpmeta|RDF)\s*>")


def sidecar_path(image_path):
    return os.path.splitext(image_path)[0] + ".xmp"


def has_adobe_xmp(image_path):
    p = sidecar_path(image_path)
    if not os.path.isfile(p):
        return False
    try:
        with open(p, encoding="utf-8", errors="replace") as f:
            return NS["crs"] in f.read()
    except OSError:
        return False


class XmpTarget(Target):
    name = "xmp"
    raw_only = True

    def compatible(self, img):
        path = img["path"]
        if not is_raw(path) or path.lower().endswith(".dng"):
            return False, "not_raw"
        return True, None

    def apply(self, session, plan):
        report = {}
        for e in plan["images"]:
            report[str(e["id"])] = self._one(session, e)
        return report

    def _one(self, session, e):
        img = session.image(e["id"])
        path = img["path"]
        ok, _ = self.compatible(img)
        if not ok:
            return {"status": "skipped", "message": "XMP-Sidecar nur fuer proprietaere RAWs"}
        side = sidecar_path(path)
        if e.get("skip_reason"):
            return {"status": "skipped", "message": skip_message(e)}
        if not e.get("changed") and os.path.exists(side):
            return {"status": "ok", "message": "unveraendert"}

        notes = []
        values = {("xmp", "Label"): LABEL_NAMES[e["label"]],
                  ("kdr", "Label"): e["label"], ("kdr", "Revision"): str(session.revision)}
        if e["apply"]:
            orient = raw_orientation(path)
            if orient is None:
                notes.append("Orientierung unbekannt, als normal angenommen")
            l, t, r, b = crop_to_stored(session.orig_crop(img), orient)
            values.update({("crs", "HasCrop"): "True", ("crs", "CropLeft"): _num(l),
                           ("crs", "CropTop"): _num(t), ("crs", "CropRight"): _num(r),
                           ("crs", "CropBottom"): _num(b), ("crs", "CropAngle"): "0"})
            if e.get("angle"):
                notes.append(f"Winkel {e['angle']:+.2f} Grad nicht uebertragen")
        elif e.get("was_applied"):
            values[("crs", "HasCrop")] = "False"
            notes.append("Crop entfernt")

        try:
            write_sidecar(side, values)
        except (OSError, ET.ParseError) as ex:
            return {"status": "error", "message": f"{os.path.basename(side)}: {ex}"}
        status = "ok" if e["apply"] else "skipped"
        return {"status": status, **({"message": "; ".join(notes)} if notes else {})}


def _num(v):
    return f"{v:.6f}"


def write_sidecar(path, values):
    """Setzt ``values`` ({(praefix, name): wert}) im ersten rdf:Description; der Rest bleibt."""
    text = TEMPLATE
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            text = f.read()
    new = update_xmp(text, values)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(new)
    os.replace(tmp, path)


def update_xmp(text, values):
    start, end = _ROOT_START.search(text), None
    for end in _ROOT_END.finditer(text):
        pass
    if not start or not end:
        raise ET.ParseError("kein xmpmeta/rdf:RDF gefunden")
    head, body, tail = text[:start.start()], text[start.start():end.end()], text[end.end():]

    for prefix, uri in _namespaces(body).items():
        ET.register_namespace(prefix, uri)
    for prefix, uri in NS.items():
        ET.register_namespace(prefix, uri)
    root = ET.fromstring(body)

    rdf = "{%s}" % NS["rdf"]
    descs = list(root.iter(rdf + "Description"))
    if not descs:
        rdf_root = root if root.tag == rdf + "RDF" else root.find(rdf + "RDF")
        if rdf_root is None:
            raise ET.ParseError("kein rdf:RDF gefunden")
        descs = [ET.SubElement(rdf_root, rdf + "Description", {rdf + "about": ""})]

    # vorhandene Werte (als Attribut oder als Kind-Element) entfernen, dann am ersten Description setzen
    tags = {"{%s}%s" % (NS[p], n): v for (p, n), v in values.items()}
    for d in descs:
        for tag in tags:
            d.attrib.pop(tag, None)
            for child in d.findall(tag):
                d.remove(child)
    for tag, v in tags.items():
        descs[0].set(tag, v)
    return head + ET.tostring(root, encoding="unicode") + tail


def _namespaces(body):
    """Praefixe der Datei beibehalten (ElementTree wuerde sonst ns0, ns1 ... vergeben)."""
    out = {}
    for prefix, uri in re.findall(r'xmlns:([\w.-]+)="([^"]*)"', body):
        out.setdefault(prefix, uri)
    return out
