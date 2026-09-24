"""Ausgabe-Adapter: was "Fertig" mit dem Plan macht.

    Web-UI ── Fertig ──> Session.finish() ── plan.json ──┬── extern:  darktable (Lua liest plan.json,
                                                         │            schreibt result.json)
                                                         └── lokal:   Target.apply(session, plan)
                                                                      -> result.json, Phase "applied"

    darktable     Crop, Drehung, Farblabel ueber das Lua-Plugin (dt.styles)
    reviews       Kalibrierung: Korrekturen als Referenz nach review_data/reviews.json
    json          crops.json + crops.csv im Ausgabeordner (werkzeugneutral)
    copies        zugeschnittene, ggf. geradegestellte Kopien + crops.json/.csv
    xmp           Adobe-XMP-Sidecar (Lightroom, Camera Raw): crs:Crop*, xmp:Label
    rawtherapee   RawTherapee-Profil (.pp3): [Crop], ColorLabel

Ein lokaler Adapter liefert je Bild ``{"status": "ok|skipped|error", "message"?: str}`` oder
``None`` (alle ok, keine Einzelmeldung). Wirft er, gilt der ganze Plan als fehlgeschlagen.
"""
import os

from ..export import is_raw
from .. import pp3

DARKTABLE = "darktable"
REVIEWS = "reviews"
AUTO = "auto"
OUTPUT_MARKER = ".autocrop-output"      # kennzeichnet Ausgabeordner; die Bildsuche ueberspringt sie
DEFAULT_OUT_DIR = "autocrop"

MODE_DARKTABLE = "darktable"            # Sitzung aus darktable (Lua)
MODE_FOLDER = "folder"                  # Kalibrierung mit reviews.json
MODE_STANDALONE = "standalone"          # eigenstaendig, Ziel = ein lokaler Adapter

LABELS = ("green", "yellow", "red")


class Target:
    """Basis der Ausgabe-Adapter."""
    name = ""
    external = False        # angewendet von einem anderen Programm (liest plan.json)
    straightens = False     # setzt einen Drehwinkel um; sonst wird im Originalrahmen ausgegeben
    calibration = False     # Referenz-Test in der UI
    writes_out = False      # nutzt den Ausgabeordner

    def apply(self, session, plan):
        raise NotImplementedError

    def describe(self, session):
        return {"name": self.name, "external": self.external, "straightens": self.straightens,
                "calibration": self.calibration,
                "out": session.out_dir if self.writes_out else None}


def _registry():
    from .darktable import DarktableTarget
    from .reviews import ReviewsTarget
    from .manifest import ManifestTarget
    from .copies import CopiesTarget
    from .xmp import XmpTarget
    from .rawtherapee import RawTherapeeTarget
    return {t.name: t for t in (DarktableTarget(), ReviewsTarget(), ManifestTarget(),
                                CopiesTarget(), XmpTarget(), RawTherapeeTarget())}


_TARGETS = None


def all_targets():
    global _TARGETS
    if _TARGETS is None:
        _TARGETS = _registry()
    return _TARGETS


def get(name):
    t = all_targets().get(name)
    if t is None:
        from ..session import SessionError
        raise SessionError(f"unbekanntes Ziel: {name}")
    return t


def standalone_names():
    return [n for n in all_targets() if n not in (DARKTABLE, REVIEWS)]


def mode_for(name):
    if name == DARKTABLE:
        return MODE_DARKTABLE
    if name == REVIEWS:
        return MODE_FOLDER
    return MODE_STANDALONE


def suggest(paths):
    """Ziel aus dem Ordnerinhalt: vorhandene Sidecars verraten den RAW-Entwickler.

    .pp3 -> rawtherapee, Adobe-XMP -> xmp, sonst RAWs -> json, nur JPEG/TIFF -> copies."""
    raws = [p for p in paths if is_raw(p)]
    if any(os.path.isfile(pp3.sidecar_path(p)) for p in paths):
        return "rawtherapee"
    from .xmp import has_adobe_xmp
    if any(has_adobe_xmp(p) for p in raws):
        return "xmp"
    return "json" if raws else "copies"


def file_action(entry, exists):
    """Was ein Datei-Adapter mit einem Planeintrag tut: write, keep, remove oder skip.

    Nur Geaendertes wird neu geschrieben (Differenz-Anwenden wie bei darktable); was frueher
    angewendet war und es nicht mehr ist, wird zurueckgenommen."""
    if entry["apply"]:
        return "write" if entry.get("changed") or not exists else "keep"
    if entry.get("was_applied") and exists:
        return "remove"
    return "skip"


def skip_message(entry):
    if entry.get("skip_reason") == "changed":
        return "Datei seit der Analyse geaendert"
    return None
