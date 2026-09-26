"""darktable ohne Plugin: Crop, Drehung und Farblabel direkt in die Sidecar-XMP ``<datei>.xmp``.

Dieselben History-Eintraege, die das Lua-Plugin per Style anhaengt (Module "crop" und
"ashift", Kodierung wie in kader.lua, gegen darktable 5.6 per darktable-cli geprueft), nur ohne
laufendes darktable. Vorhandene Sidecars werden ergaenzt: die bisherige History bleibt stehen,
die neuen Schritte kommen oben drauf (wie jede Bearbeitung in darktable), Farblabel blau/lila
bleiben. Ohne Sidecar entsteht eine neue XMP mit nur diesen Schritten; darktable wendet beim
Import seine Standardbearbeitung trotzdem an.

Grenzen (siehe README):
- darktable liest eine Sidecar-XMP nur beim Import. Schon importierte Bilder sehen die neue XMP
  erst nach "beim Start nach aktualisierten XMP-Dateien suchen" (Einstellungen > Speicher) oder
  einem erneuten Import; solche Bilder werden im Ergebnis genannt.
- Laeuft darktable waehrenddessen, kann es die XMP mit seinem Stand ueberschreiben.
- Mit einem anderen Konverter als darktable kann der analysierte Rahmen um einige Pixel von
  darktables eigenem abweichen.
"""
import os
import re
import struct
import xml.etree.ElementTree as ET

from . import Target, skip_message
from ..export import is_raw

NS_DT = "http://darktable.sf.net/"
LABEL_CODES = {"red": 0, "yellow": 1, "green": 2}        # darktable: 3 blau, 4 lila bleiben
CROP_MODVERSION = 1
ASHIFT_MODVERSION = 5
ASHIFT_PARAMS_SIZE = 892
BLENDOP_VERSION = 14
BLENDOP_PARAMS = "gz11eJxjYIAACQYYOOHEgAZY0QWAgBGLGANDgz0Ej1Q+dcF/IADRAGpyHQU="   # "normal", keine Maske

TEMPLATE = (
    '<?xml version="1.0" encoding="UTF-8"?>\n'
    '<x:xmpmeta xmlns:x="adobe:ns:meta/" x:xmptk="Kader">\n'
    ' <rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">\n'
    '  <rdf:Description rdf:about=""\n'
    '    xmlns:darktable="http://darktable.sf.net/"\n'
    '   darktable:xmp_version="5">\n'
    '  </rdf:Description>\n'
    ' </rdf:RDF>\n'
    '</x:xmpmeta>\n')

_DESC = re.compile(r"<rdf:Description\b[^>]*?(/?)>", re.S)
_DESC_END = "</rdf:Description>"
_LI = re.compile(r"<rdf:li\b[^>]*?/>", re.S)
_HISTORY = re.compile(r"[ \t]*<darktable:history>.*?</darktable:history>[ \t]*\n?|"
                      r"[ \t]*<darktable:history\s*/>[ \t]*\n?", re.S)
_MASKS = re.compile(r"<darktable:masks_history>.*?</darktable:masks_history>", re.S)
_LABELS = re.compile(r"[ \t]*<darktable:colorlabels>.*?</darktable:colorlabels>[ \t]*\n?", re.S)


def sidecar_path(image_path):
    """darktable legt die XMP mit vollem Dateinamen an: ``IMG_0001.NEF.xmp``."""
    return image_path + ".xmp"


def has_darktable_xmp(image_path):
    p = sidecar_path(image_path)
    if not os.path.isfile(p):
        return False
    try:
        with open(p, encoding="utf-8", errors="replace") as f:
            return NS_DT in f.read(4096)
    except OSError:
        return False


def pack_crop(left, top, right, bottom):
    """crop-Parameter: Kanten 0..1, Seitenverhaeltnis frei (ratio 0/0, wie im Plugin)."""
    left, top = _clamp(left), _clamp(top)
    right, bottom = max(left + 0.001, _clamp(right)), max(top + 0.001, _clamp(bottom))
    return struct.pack("<ffffii", left, top, right, bottom, 0, 0).hex()


def pack_ashift(rotation_deg):
    """ashift-Parameter (Version 5): nur die Drehung, keine Perspektive, kein Auto-Zuschnitt."""
    raw = struct.pack("<ffffffffiiffffi", rotation_deg, 0, 0, 0, 28.0, 1.0, 100.0, 1.0,
                      0, 0, 0, 1, 0, 1, 0)
    return (raw + b"\0" * (ASHIFT_PARAMS_SIZE - len(raw))).hex()


def _clamp(v):
    return max(0.0, min(1.0, float(v)))


def history_steps(entry):
    """Die History-Schritte fuer einen Planeintrag, wie ``companion_apply`` in kader.lua.

    Liefert [(operation, modversion, params_hex, enabled)]."""
    steps = []
    angle, was_angle = entry.get("angle"), entry.get("was_angle")
    if entry["apply"] and entry.get("crop"):
        if angle:
            steps.append(("ashift", ASHIFT_MODVERSION, pack_ashift(angle), True))
        elif was_angle:
            steps.append(("ashift", ASHIFT_MODVERSION, pack_ashift(0), False))
        steps.append(("crop", CROP_MODVERSION, pack_crop(*entry["crop"]), True))
    elif entry.get("was_applied"):
        if was_angle:
            steps.append(("ashift", ASHIFT_MODVERSION, pack_ashift(0), False))
        steps.append(("crop", CROP_MODVERSION, pack_crop(0, 0, 1, 1), False))
    return steps


def _li(num, operation, modversion, params, enabled):
    return ("     <rdf:li\n"
            f'      darktable:num="{num}"\n'
            f'      darktable:operation="{operation}"\n'
            f'      darktable:enabled="{1 if enabled else 0}"\n'
            f'      darktable:modversion="{modversion}"\n'
            f'      darktable:params="{params}"\n'
            '      darktable:multi_name=""\n'
            '      darktable:multi_name_hand_edited="0"\n'
            '      darktable:multi_priority="0"\n'
            f'      darktable:blendop_version="{BLENDOP_VERSION}"\n'
            f'      darktable:blendop_params="{BLENDOP_PARAMS}"/>\n')


def _attr(tag, name):
    m = re.search(r'\s%s="([^"]*)"' % re.escape(name), tag)
    return m.group(1) if m else None


def _set_attr(tag, name, value):
    """Attribut im Start-Tag setzen oder anhaengen (vor dem schliessenden ``>``)."""
    pat = re.compile(r'(\s%s=")[^"]*(")' % re.escape(name))
    if pat.search(tag):
        return pat.sub(lambda m: m.group(1) + value + m.group(2), tag, count=1)
    end = -2 if tag.endswith("/>") else -1
    return f'{tag[:end]}\n   {name}="{value}"{tag[end:]}'


def _drop_attr(tag, name):
    return re.sub(r'\s+%s="[^"]*"' % re.escape(name), "", tag)


def update_xmp(text, steps, label):
    """Haengt ``steps`` an die History an und setzt das Farblabel; der Rest bleibt stehen."""
    text = text or TEMPLATE
    m = _DESC.search(text)
    if not m:
        raise ValueError("kein rdf:Description gefunden")
    tag = m.group(0)
    if m.group(1):                                        # <rdf:Description .../> ohne Inhalt
        tag = tag[:-2].rstrip() + ">"
        text = text[:m.start()] + tag + "\n  " + _DESC_END + text[m.end():]
        m = _DESC.search(text)
    head, rest = text[:m.start()], text[m.end():]
    end = rest.find(_DESC_END)
    if end < 0:
        raise ValueError("rdf:Description nicht geschlossen")
    body, tail = rest[:end], rest[end:]

    if "xmlns:darktable=" not in tag and "xmlns:darktable=" not in head:
        tag = _set_attr(tag, "xmlns:darktable", NS_DT)
    if _attr(tag, "darktable:xmp_version") is None:
        tag = _set_attr(tag, "darktable:xmp_version", "5")

    # History: rueckgaengig gemachte Schritte (ab history_end) verwirft darktable beim naechsten
    # Bearbeitungsschritt ebenfalls; die neuen Schritte folgen direkt auf den aktiven Stand
    hm = _HISTORY.search(body)
    items = _LI.findall(hm.group(0)) if hm else []
    if hm and hm.group(0).count("<rdf:li") != len(items):
        raise ValueError("History in unbekannter Form (nicht angefasst)")
    nums = [int(_attr(li, "darktable:num") or i) for i, li in enumerate(items)]
    try:
        hist_end = int(_attr(tag, "darktable:history_end"))
    except (TypeError, ValueError):
        hist_end = len(items)
    keep = [li for li, n in zip(items, nums) if n < hist_end]
    dropped = {n for n in nums if n >= hist_end}
    nxt = max([n for n in nums if n < hist_end], default=-1) + 1
    new_items = ["     " + li.strip() + "\n" for li in keep]
    for i, step in enumerate(steps):
        new_items.append(_li(nxt + i, *step))
    block = ("   <darktable:history>\n    <rdf:Seq>\n" + "".join(new_items)
             + "    </rdf:Seq>\n   </darktable:history>\n") if new_items else ""
    if hm:
        body = body[:hm.start()] + block + body[hm.end():]
    else:
        body = body.rstrip(" ") + block + "  "
    if dropped:
        body = _MASKS.sub(lambda mm: _LI.sub(
            lambda li: "" if int(_attr(li.group(0), "darktable:mask_num") or -1) in dropped else li.group(0),
            mm.group(0)), body)
    tag = _set_attr(tag, "darktable:history_end", str(nxt + len(steps)))
    tag = _drop_attr(tag, "darktable:history_current_hash")     # darktable rechnet ihn neu

    # Farblabel: rot/gelb/gruen ersetzen, blau/lila behalten
    lm = _LABELS.search(body)
    codes = [int(c) for c in re.findall(r"<rdf:li>\s*(\d+)\s*</rdf:li>", lm.group(0))] if lm else []
    codes = sorted({c for c in codes if c not in LABEL_CODES.values()} | {LABEL_CODES[label]})
    labels = ("   <darktable:colorlabels>\n    <rdf:Seq>\n"
              + "".join(f"     <rdf:li>{c}</rdf:li>\n" for c in codes)
              + "    </rdf:Seq>\n   </darktable:colorlabels>\n")
    if lm:
        body = body[:lm.start()] + labels + body[lm.end():]
    else:
        body = "\n" + labels + body.lstrip("\n")

    new = head + tag + body + tail
    ET.fromstring(new.encode("utf-8"))                   # wohlgeformt? sonst ParseError, Datei bleibt
    return new


def write_sidecar(path, steps, label):
    text = None
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            text = f.read()
    new = update_xmp(text, steps, label)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        f.write(new)
    os.replace(tmp, path)


class DarktableXmpTarget(Target):
    name = "darktable_xmp"
    straightens = True      # Modul "Drehen und Perspektive" (ashift), wie das Plugin

    def apply(self, session, plan):
        from .. import dtconfig
        paths = [session.image(e["id"])["path"] for e in plan["images"] if e.get("changed")]
        self._imported = dtconfig.imported(paths)
        self._running = dtconfig.is_running()
        return {str(e["id"]): self._one(session, e) for e in plan["images"]}

    def _one(self, session, e):
        img = session.image(e["id"])
        side = sidecar_path(img["path"])
        if e.get("skip_reason"):
            return {"status": "skipped", "message": skip_message(e)}
        if not e.get("changed") and os.path.exists(side):
            return {"status": "ok", "message": "unveraendert"}

        steps = history_steps(e)
        notes = []
        if not e["apply"] and e.get("was_applied"):
            notes.append("Crop entfernt")
        if e["apply"] and is_raw(img["path"]) and session.converter_name not in (None, "darktable"):
            notes.append(f"Rahmen aus {session.converter_name}-Export")
        if img["path"] in self._imported:
            notes.append("schon in darktable importiert")
        try:
            write_sidecar(side, steps, e["label"])
        except (OSError, ValueError, ET.ParseError) as ex:
            return {"status": "error", "message": f"{os.path.basename(side)}: {ex}"}
        status = "ok" if e["apply"] else "skipped"
        return {"status": status, **({"message": "; ".join(notes)} if notes else {})}

    def summary_message(self, session, report):
        parts = []
        n = len(getattr(self, "_imported", ()))
        if n:
            parts.append(f"{n} {'Bild ist' if n == 1 else 'Bilder sind'} schon in darktable importiert: "
                         "darktable liest die neuen XMP-Dateien erst nach „Einstellungen → Speicher → beim "
                         "Start nach aktualisierten XMP-Dateien suchen“ und einem Neustart.")
        if getattr(self, "_running", False):
            parts.append("darktable läuft gerade und kann die XMP-Dateien mit seinem Stand überschreiben – "
                         "darktable am besten vor „Fertig“ schließen.")
        if not parts:
            parts.append("XMP-Dateien liegen neben den Bildern: den Ordner jetzt in darktable importieren.")
        return " ".join(parts)
