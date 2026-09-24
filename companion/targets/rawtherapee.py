"""RawTherapee: Crop und Farblabel im Profil ``<datei>.pp3`` neben dem Bild.

Geschrieben werden ``[Crop] Enabled/X/Y/W/H`` (Pixel) und ``[General] ColorLabel``
(1 rot, 2 gelb, 3 gruen). Vorhandene Profile werden ergaenzt, alles andere bleibt.

Grenzen (siehe README):
- Die Pixel beziehen sich auf den analysierten Rahmen. Mit dem Konverter ``rawtherapee`` ist das
  RawTherapees eigener Rahmen; mit einem anderen Konverter kann er um einige Pixel abweichen.
- Kein Geradestellen: der Crop wird achsparallel im Originalrahmen uebertragen.
- Ein neu angelegtes Profil enthaelt nur diese Werte; RawTherapee nimmt fuer den Rest seine
  eingebauten Standards, nicht dein Standardprofil. Besser: Bilder vorher einmal in
  RawTherapee oeffnen (legt das Profil an).
"""
import os

from . import Target, skip_message
from .. import pp3
from ..export import is_raw
from ..session import crop_to_pixels

COLOR_LABELS = {"red": 1, "yellow": 2, "green": 3}


class RawTherapeeTarget(Target):
    name = "rawtherapee"

    def apply(self, session, plan):
        return {str(e["id"]): self._one(session, e) for e in plan["images"]}

    def _one(self, session, e):
        img = session.image(e["id"])
        side = pp3.sidecar_path(img["path"])
        if e.get("skip_reason"):
            return {"status": "skipped", "message": skip_message(e)}
        if not e.get("changed") and os.path.exists(side):
            return {"status": "ok", "message": "unveraendert"}

        notes = []
        size = img.get("export_size")
        crop_values = None
        if e["apply"]:
            if not size:
                return {"status": "error", "message": "Bildgroesse unbekannt"}
            px = crop_to_pixels(session.orig_crop(img), *size)
            crop_values = {"Enabled": True, "X": px["x"], "Y": px["y"], "W": px["width"],
                           "H": px["height"], "FixedRatio": False}
            if e.get("angle"):
                notes.append(f"Winkel {e['angle']:+.2f} Grad nicht uebertragen")
            if is_raw(img["path"]) and session.converter_name != "rawtherapee":
                notes.append(f"Rahmen aus {session.converter_name}-Export")
        elif e.get("was_applied"):
            crop_values = {"Enabled": False}
            notes.append("Crop entfernt")

        try:
            text = ""
            if os.path.exists(side):
                with open(side, encoding="utf-8") as f:
                    text = f.read()
            text = pp3.set_values(text, "General", {"ColorLabel": COLOR_LABELS[e["label"]]})
            if crop_values:
                text = pp3.set_values(text, "Crop", crop_values)
            tmp = side + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                f.write(text)
            os.replace(tmp, side)
        except OSError as ex:
            return {"status": "error", "message": str(ex)}
        status = "ok" if e["apply"] else "skipped"
        return {"status": status, **({"message": "; ".join(notes)} if notes else {})}
