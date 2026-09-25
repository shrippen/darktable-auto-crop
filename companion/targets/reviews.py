"""Kalibrierung ohne darktable: Korrekturen im Format von review_data/reviews.json ablegen."""
import json
import os

from . import Target, REVIEWS
from ..session import crop_to_pixels, read_json, straight_size


class ReviewsTarget(Target):
    name = REVIEWS
    straightens = True      # Referenz im Originalrahmen plus Winkel
    calibration = True

    def apply(self, session, plan):
        reviews_path = session.state.get("source", {}).get("reviews")
        if not reviews_path:
            return None
        reviews = read_json(reviews_path, {}) or {}
        for img in session.state["images"].values():
            size = img.get("export_size")
            key = f"{img['film']}/{img['filename']}"
            man = img.get("manual")
            det = img.get("detected")
            deg = session.straight_deg(img)
            if man and size:
                rev = reviews.get(key, {})
                # Referenz im Originalrahmen (wie die Erkennung); bei Geradestellen zusaetzlich
                # Winkel und der Crop im geraden Bild
                rev["manual_crop"] = crop_to_pixels(session.orig_crop(img), *size)
                _tilt_fields(session, rev, img, deg, size)
                if session.group_of(img) == "red":
                    rev["is_problem"] = True
                reviews[key] = rev
            elif img.get("decision") == "accept" and det and size:
                # "Akzeptieren" = der Nutzer hat den erkannten Crop gesehen und bestaetigt ihn als
                # Referenz (Ground Truth); ohne das gingen richtige Crops fuer die Kalibrierung verloren.
                rev = reviews.get(key, {})
                rev["manual_crop"] = crop_to_pixels(session.orig_crop(img), *size)
                _tilt_fields(session, rev, img, deg, size)
                rev["confirmed"] = True
                reviews[key] = rev
            elif session.group_of(img) == "red" and img.get("group_override") == "red":
                rev = reviews.get(key, {"manual_crop": None})
                rev["is_problem"] = True
                reviews[key] = rev
        tmp = reviews_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(reviews, f, indent=2, ensure_ascii=False)
        os.replace(tmp, reviews_path)
        return None


def _tilt_fields(session, rev, img, deg, size):
    """Schraeglage in den Referenzeintrag schreiben (oder alte Angaben entfernen)."""
    if deg:
        crop = session.effective_crop(img)
        rev["tilt_deg"] = deg
        rev["manual_crop_straight"] = {"crop": crop, "size": [round(v) for v in straight_size(size, deg)]}
    else:
        rev.pop("tilt_deg", None)
        rev.pop("manual_crop_straight", None)
