"""Sitzungszustand der Companion-UI.

Eine Sitzung liegt komplett auf der Platte (``<root>/<session-id>/``), damit sie
einen Neustart des Servers ueberlebt ("Pruefung oeffnen" in darktable):

    job.json        Eingabe (Bilder, Einstellungen, Ziel), von Lua bzw. der CLI
    state.json      veraenderlicher Zustand (Phase, Revision, Bilder, Korrekturen)
    plan.json       Uebergabe an das Ziel (nur ab der Phase "locked"), siehe targets/
    result.json     Rueckmeldung des Ziels (darktable: nach "Plan anwenden")
    applied.json    zuletzt angewendete Crops/Labels (Grundlage fuer Differenzen)
    feedback.jsonl  Protokoll aller Korrekturen (Kalibrierdaten)
    server.json     laufender Server (pid, port, token, url)
    exports/        RAW-Exporte des Konverters (voller Aufloesung, JPEG), siehe converters.py
    thumbs/         abgeleitete Vorschauen

Alle Crops sind normalisiert ``[links, oben, rechts, unten]`` (0..1) und beziehen
sich auf den (bereits gedrehten) Export-Rahmen. Das ist exakt das
Koordinatensystem von darktables Crop-Modul (siehe companion-ui-plan.md, Abschnitt 12).
"""
import copy
import hashlib
import json
import math
import os
import shutil
import threading
import time
import uuid
from datetime import datetime, timezone

from . import targets

DEFAULT_ROOT = os.environ.get("AUTOCROP_CACHE") or os.path.join(
    os.path.expanduser("~"), ".cache", "auto-crop-negative")
CACHE_DAYS = 14

PHASES = ("analyzing", "reviewing", "locked", "applied", "apply_failed")
EDITABLE = ("analyzing", "reviewing")
REOPENABLE = ("locked", "applied", "apply_failed")
GROUPS = ("green", "yellow", "red")

DEFAULT_SETTINGS = {
    "format": "35mm",          # Fallback-Format der Erkennung
    "t_green": 0.5,            # ab hier gruen
    "t_yellow": 0.3,           # ab hier gelb, darunter rot
}


REF_TOL_PX = 60          # wie tools/eval.py
REF_TOL_EDGE = 2000      # ... bei dieser langen Bildkante; auf andere Groessen proportional skaliert


class SessionError(Exception):
    """Fachlicher Fehler mit HTTP-Status (409 gesperrt, 400 ungueltig, 404)."""

    def __init__(self, message, status=400, **extra):
        super().__init__(message)
        self.status = status
        self.extra = extra


# ── Dateihilfen ──────────────────────────────────────────────────────────────

def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def atomic_write_json(path, obj):
    """Schreibt JSON atomar (temporaere Datei + Umbenennen), damit ein Leser
    (Lua, Neustart) nie eine halbe Datei sieht."""
    tmp = f"{path}.tmp.{os.getpid()}.{threading.get_ident()}"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def read_json(path, default=None):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def content_hash(obj):
    """SHA-256 ueber die kanonische JSON-Darstellung (fuer plan.json)."""
    raw = json.dumps(obj, sort_keys=True, ensure_ascii=False,
                     separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def fingerprint(path):
    """Grobe Identitaet einer Bilddatei: Groesse + mtime (ein SHA ueber Raws
    waere unnoetig langsam)."""
    try:
        st = os.stat(path)
    except OSError:
        return None
    return {"size": st.st_size, "mtime": round(st.st_mtime, 3)}


def clamp_crop(crop):
    """Prueft/normalisiert ``[l, t, r, b]``; wirft SessionError bei Unsinn."""
    try:
        l, t, r, b = (float(v) for v in crop)
    except (TypeError, ValueError):
        raise SessionError("crop muss vier Zahlen [l,t,r,b] enthalten")
    l, r = max(0.0, min(1.0, l)), max(0.0, min(1.0, r))
    t, b = max(0.0, min(1.0, t)), max(0.0, min(1.0, b))
    if r - l < 0.02 or b - t < 0.02:
        raise SessionError("crop ist zu klein oder verdreht")
    return [round(l, 5), round(t, 5), round(r, 5), round(b, 5)]


def crop_from_pixels(x, y, w, h, img_w, img_h):
    return clamp_crop([x / img_w, y / img_h, (x + w) / img_w, (y + h) / img_h])


def crop_to_pixels(crop, img_w, img_h):
    l, t, r, b = crop
    x, y = int(round(l * img_w)), int(round(t * img_h))
    return {"x": x, "y": y, "width": int(round(r * img_w)) - x,
            "height": int(round(b * img_h)) - y}


PITCH_MM = 4.7625                  # Lochabstand 35-mm-Film (siehe film_scale.py)
SCALE_AGREE_MAX = 0.02             # Takt-Uebereinstimmung der zwei Rollenhaelften (wie SCALE_CFG["agree_max"])
MAX_STRAIGHTEN_DEG = 10.0
AUTO_STRAIGHTEN_MIN_DEG = 0.3      # ab diesem gemessenen Tilt wird standardmaessig geradegestellt
AUTO_STRAIGHTEN_MIN_CONF = 0.4


def straight_size(size, deg):
    """Groesse der geradegestellten Flaeche = Bounding-Box des gedrehten Bildes.
    Entspricht der Ausgabe von darktables Modul "Drehen und Perspektive" (ashift) ohne
    Zuschnitt (Rotation 5 Grad auf 1333x2000 ergibt dort 1502x2108)."""
    w, h = size
    t = math.radians(abs(deg))
    return (w * math.cos(t) + h * math.sin(t), h * math.cos(t) + w * math.sin(t))


def _rot(dx, dy, deg_cw):
    t = math.radians(deg_cw)
    return dx * math.cos(t) - dy * math.sin(t), dx * math.sin(t) + dy * math.cos(t)


def crop_to_straight(crop, size, deg):
    """Crop [l,t,r,b] im Original -> im geradegestellten Bild (Mitte mitdrehen, Groesse behalten).

    ``deg`` = gemessene Schraeglage (+ = Bildinhalt im Uhrzeigersinn verdreht); die
    Korrektur dreht den Inhalt um ``deg`` gegen den Uhrzeigersinn."""
    w, h = size
    W, H = straight_size(size, deg)
    l, t, r, b = crop
    dx, dy = _rot((l + r) / 2 * w - w / 2, (t + b) / 2 * h - h / 2, -deg)
    cw, ch = (r - l) * w, (b - t) * h
    return _fit_box(W / 2 + dx, H / 2 + dy, cw, ch, W, H)


def crop_from_straight(crop, size, deg):
    """Umkehrung von :func:`crop_to_straight`."""
    w, h = size
    W, H = straight_size(size, deg)
    l, t, r, b = crop
    dx, dy = _rot((l + r) / 2 * W - W / 2, (t + b) / 2 * H - H / 2, deg)
    cw, ch = (r - l) * W, (b - t) * H
    return _fit_box(w / 2 + dx, h / 2 + dy, cw, ch, w, h)


def point_to_straight(pt, size, deg):
    """Punkt [x, y] (normiert im Original) -> normiert im geradegestellten Bild."""
    w, h = size
    W, H = straight_size(size, deg)
    dx, dy = _rot(pt[0] * w - w / 2, pt[1] * h - h / 2, -deg)
    return [round((W / 2 + dx) / W, 5), round((H / 2 + dy) / H, 5)]


def _fit_box(cx, cy, cw, ch, W, H):
    cw, ch = min(cw, W), min(ch, H)
    x0 = min(max(cx - cw / 2, 0.0), W - cw)
    y0 = min(max(cy - ch / 2, 0.0), H - ch)
    return [round(x0 / W, 5), round(y0 / H, 5), round((x0 + cw) / W, 5), round((y0 + ch) / H, 5)]


def new_session_id():
    return datetime.now().strftime("%Y-%m-%dT%H-%M-%S") + "-" + uuid.uuid4().hex[:4]


# ── Sitzung ──────────────────────────────────────────────────────────────────

class Session:
    """Threadsicherer Zustand einer Sitzung. Jede Mutation speichert atomar."""

    def __init__(self, directory):
        self.dir = directory
        self.lock = threading.RLock()
        self.state = read_json(self.path("state.json"))
        if self.state is None:
            raise SessionError(f"keine Sitzung in {directory}", 404)
        self.history = []          # Undo-Stapel (nur im Speicher)
        self._listeners = []
        self._result_mtime = None

    # -- Erzeugung -----------------------------------------------------------

    @classmethod
    def create(cls, job, root=None):
        """Neue Sitzung aus einem Job: ``{"images":[{id,path,film?}], "settings"?,
        "target"?: Name aus targets/, "converter"?, "out"?, "mode"?: "darktable"|"folder", ...}``.

        Ohne ``target`` folgt es aus ``mode`` (darktable -> darktable, folder -> reviews)."""
        root = root or DEFAULT_ROOT
        sid = job.get("session") or new_session_id()
        directory = os.path.join(root, sid)
        os.makedirs(os.path.join(directory, "exports"), exist_ok=True)
        os.makedirs(os.path.join(directory, "thumbs"), exist_ok=True)
        settings = dict(DEFAULT_SETTINGS)
        settings.update(job.get("settings") or {})
        target = job.get("target") or _target_for_mode(job.get("mode", "darktable"))
        mode = targets.mode_for(target)
        images = {}
        for i, item in enumerate(job.get("images") or []):
            iid = str(item.get("id", i + 1))
            path = item["path"]
            images[iid] = {
                "id": iid if not iid.isdigit() else int(iid),
                "path": path,
                "filename": os.path.basename(path),
                "film": item.get("film") or os.path.basename(os.path.dirname(path)),
                "fingerprint": fingerprint(path),
                "export": item.get("export"),      # relativ zur Sitzung oder absolut
                "export_size": item.get("export_size"),
                "detected": item.get("detected"),
                "manual": item.get("manual"),      # {"crop": [...]}
                "group_override": item.get("group_override"),
                "decision": None,
                "status": "done" if item.get("detected") else "pending",
                "error": None,
            }
        state = {
            "v": 1, "session": sid, "mode": mode, "phase": "analyzing",
            "target": target, "converter": job.get("converter"), "out": job.get("out"),
            "revision": 1, "created": now_iso(), "settings": settings,
            "source": {k: job[k] for k in ("folder", "results", "reviews")
                       if k in job},
            "images": images, "film_aspects": {},
        }
        atomic_write_json(os.path.join(directory, "job.json"), job)
        atomic_write_json(os.path.join(directory, "state.json"), state)
        return cls(directory)

    # -- Pfade / Speichern ---------------------------------------------------

    def path(self, *parts):
        return os.path.join(self.dir, *parts)

    def save(self):
        atomic_write_json(self.path("state.json"), self.state)
        self._notify({"type": "state"})

    def subscribe(self, fn):
        self._listeners.append(fn)

    def _notify(self, event):
        for fn in list(self._listeners):
            try:
                fn(event)
            except Exception:
                pass

    # -- Abfragen ------------------------------------------------------------

    @property
    def phase(self):
        return self.state["phase"]

    @property
    def revision(self):
        return self.state["revision"]

    @property
    def mode(self):
        return self.state.get("mode", "darktable")

    @property
    def target_name(self):
        return self.state.get("target") or _target_for_mode(self.mode)

    @property
    def target(self):
        return targets.get(self.target_name)

    @property
    def converter_name(self):
        """RAW-Konverter (converters.py); aeltere Sitzungen und darktable-Jobs: darktable."""
        return self.state.get("converter") or "darktable"

    @property
    def out_dir(self):
        return self.state.get("out")

    def image(self, iid):
        key = str(iid)
        for k, img in self.state["images"].items():
            if k == key:
                return img
        raise SessionError(f"Bild {iid} unbekannt", 404)

    def export_path(self, img):
        exp = img.get("export")
        if not exp:
            return None
        return exp if os.path.isabs(exp) else self.path(exp)

    def straight_deg(self, img):
        """Winkel, um den geradegestellt wird (None = aus): Vorschau hier, Drehung in darktable."""
        st = img.get("straighten")
        if st is False:                 # ausdruecklich aus
            return None
        if st:
            deg = float(st.get("deg") or 0.0)
            return deg if abs(deg) >= 0.01 else None
        # Standard: gemessenen Tilt anwenden, wenn er merklich und verlaesslich ist
        sk = (img.get("detected") or {}).get("skew") or {}
        if sk.get("deg") is not None and abs(sk["deg"]) >= AUTO_STRAIGHTEN_MIN_DEG \
                and (sk.get("conf") or 0.0) >= AUTO_STRAIGHTEN_MIN_CONF:
            return float(sk["deg"])
        return None

    def effective_crop(self, img):
        """Aktueller Crop im aktuellen Bezugsrahmen (Original oder geradegestellt).

        Ein manueller Crop merkt sich den Winkel, in dem er gesetzt wurde (``deg``, None = Original)
        und wird bei anderem Winkel umgerechnet; so bleibt er richtig, wenn der Tilt an- oder
        ausgeschaltet wird."""
        man = img.get("manual")
        if man:
            cur, was, size = self.straight_deg(img), man.get("deg"), img.get("export_size")
            if size and abs((cur or 0.0) - (was or 0.0)) > 0.005:
                c = man["crop"]
                if was:
                    c = crop_from_straight(c, size, was)
                if cur:
                    c = crop_to_straight(c, size, cur)
                return c
            return man["crop"]
        return self.detected_crop(img)

    def detected_crop(self, img):
        det = img.get("detected")
        if not det:
            return None
        deg, size = self.straight_deg(img), img.get("export_size")
        if deg and size:
            # korrigierte Erkennung (Pipeline-Schritt 3), sofern fuer diesen Winkel gerechnet
            st = (det.get("skew") or {}).get("straight")
            if st and abs(st["deg"] - deg) < 0.15:
                return st["crop"]
            return crop_to_straight(det["crop"], size, deg)
        return det["crop"]

    def orig_crop(self, img):
        """Aktueller Crop im Originalrahmen (fuer Referenzen: dort liegt die Erkennung)."""
        crop, deg, size = self.effective_crop(img), self.straight_deg(img), img.get("export_size")
        if crop and deg and size:
            return crop_from_straight(crop, size, deg)
        return crop

    def group_of(self, img):
        """Gruppe: Nutzerentscheid, sonst aus der Konfidenz (Schwellen aus den Einstellungen)."""
        if img.get("group_override") in GROUPS:
            return img["group_override"]
        det = img.get("detected")
        if not det:
            return "red"
        s = self.state["settings"]
        conf = det.get("confidence", 0.0)
        if conf >= s["t_green"]:
            return "green"
        if conf >= s["t_yellow"]:
            return "yellow"
        return "red"

    def will_apply(self, img):
        """Wird der Crop in darktable angewendet? (skip / ungeprueftes Rot: nein)"""
        if img.get("decision") == "skip" or img.get("status") == "error":
            return False
        if self.effective_crop(img) is None:
            return False
        if self.group_of(img) == "red" and not img.get("manual") \
                and img.get("decision") != "accept":
            return False
        return True

    def public_image(self, img):
        det = img.get("detected") or {}
        return {
            "id": img["id"], "filename": img["filename"], "film": img["film"],
            "status": img.get("status"), "error": img.get("error"),
            "group": self.group_of(img),
            "auto_group": self._auto_group(img),
            "confidence": det.get("confidence"),
            "method": det.get("method"), "reasons": det.get("reasons", []),
            "conf_parts": det.get("conf_parts"),
            "skew": det.get("skew"),
            "detected_crop": self.detected_crop(img),
            "straighten": self.straight_deg(img),
            "view_size": self._view_size(img),
            "skew_lines": self._skew_lines(img),
            "manual_crop": (img.get("manual") or {}).get("crop"),
            "crop": self.effective_crop(img),
            "decision": img.get("decision"),
            "proposal": self._public_proposal(img, self.straight_deg(img)),
            "ref": self.ref_deviation(img),
            "apply": self.will_apply(img),
            "export_size": img.get("export_size"),
            "has_export": bool(self.export_path(img)
                               and os.path.exists(self.export_path(img))),
        }

    def _skew_lines(self, img):
        """Gemessene Rahmenkanten im aktuellen Bezugsrahmen (nach dem Geradestellen also gedreht)."""
        lines = ((img.get("detected") or {}).get("skew") or {}).get("lines")
        if not lines:
            return None
        deg, size = self.straight_deg(img), img.get("export_size")
        if deg and size:
            return {k: [point_to_straight(p, size, deg) for p in v] for k, v in lines.items()}
        return lines

    def _view_size(self, img):
        size, deg = img.get("export_size"), self.straight_deg(img)
        if size and deg:
            return [round(v, 1) for v in straight_size(size, deg)]
        return size

    @staticmethod
    def _public_proposal(img, deg=None):
        p = img.get("proposal")
        if not p:
            return None
        if p.get("error"):
            return {"error": p["error"]}
        crop = p["crop"]
        if deg and img.get("export_size"):
            crop = crop_to_straight(crop, img["export_size"], deg)
        return {"crop": crop, "confidence": p.get("confidence"),
                "method": p.get("method"), "reasons": p.get("reasons", [])}

    def ref_deviation(self, img):
        """Abweichung der automatischen Erkennung von der Referenz (manueller/bestaetigter Crop).

        Liefert None ohne beides. Pixel beziehen sich auf das analysierte Bild; ``hit`` wie in
        tools/eval.py (|dW|,|dH| < Toleranz), ``strict`` zusaetzlich |dX|,|dY|."""
        det, man, size = img.get("detected"), img.get("manual"), img.get("export_size")
        if not (det and man and size):
            return None
        d = crop_to_pixels(det["crop"], *size)
        m = crop_to_pixels(self.orig_crop(img), *size)
        tol = REF_TOL_PX * max(size) / float(REF_TOL_EDGE)
        dev = {"dx": d["x"] - m["x"], "dy": d["y"] - m["y"],
               "dw": d["width"] - m["width"], "dh": d["height"] - m["height"], "tol": round(tol, 1)}
        dev["hit"] = abs(dev["dw"]) < tol and abs(dev["dh"]) < tol
        dev["strict"] = dev["hit"] and abs(dev["dx"]) < tol and abs(dev["dy"]) < tol
        dev["score"] = round(max(abs(dev[k]) for k in ("dx", "dy", "dw", "dh")) / tol, 2)
        return dev

    def _auto_group(self, img):
        keep = img.get("group_override")
        img["group_override"] = None
        try:
            return self.group_of(img)
        finally:
            img["group_override"] = keep

    def public_state(self):
        with self.lock:
            imgs = [self.public_image(i) for i in self.state["images"].values()]
            return {
                "session": self.state["session"], "mode": self.mode,
                "target": self.target.describe(self), "converter": self.converter_name,
                "phase": self.phase, "revision": self.revision,
                "settings": self.state["settings"],
                "film_aspects": self.state.get("film_aspects", {}),
                "images": imgs, "summary": self._summary(),
                "can_undo": bool(self.history),
                "applied_revision": (read_json(self.path("applied.json"), {})
                                     or {}).get("revision"),
            }

    def _summary(self):
        n = {"total": 0, "apply": 0, "red": 0, "skipped": 0, "yellow": 0,
             "green": 0, "pending": 0, "error": 0, "changed": 0}
        applied = (read_json(self.path("applied.json"), {}) or {}).get("images", {})
        ref = {"n": 0, "hits": 0, "by_group": {g: {"n": 0, "hits": 0} for g in GROUPS}}
        for img in self.state["images"].values():
            dev = self.ref_deviation(img)
            if dev:
                g0 = self._auto_group(img)
                ref["n"] += 1
                ref["hits"] += int(dev["hit"])
                ref["by_group"][g0]["n"] += 1
                ref["by_group"][g0]["hits"] += int(dev["hit"])
            n["total"] += 1
            g = self.group_of(img)
            n[g] += 1
            if img.get("status") == "pending":
                n["pending"] += 1
            if img.get("status") == "error":
                n["error"] += 1
            if img.get("decision") == "skip":
                n["skipped"] += 1
            if self.will_apply(img):
                n["apply"] += 1
            if self._entry_changed(img, applied.get(str(img["id"]))):
                n["changed"] += 1
        n["ref"] = ref
        return n

    # -- Mutationen (nur in editierbaren Phasen) -----------------------------

    def _require_editable(self):
        if self.phase not in EDITABLE:
            raise SessionError("Sitzung ist gesperrt", 409, phase=self.phase)

    def set_settings(self, patch):
        with self.lock:
            self._require_editable()
            s = self.state["settings"]
            for k in ("t_green", "t_yellow"):
                if k in patch:
                    s[k] = round(float(patch[k]), 3)
            if "format" in patch:
                s["format"] = str(patch["format"])
            if s["t_yellow"] > s["t_green"]:
                s["t_yellow"] = s["t_green"]
            self.save()

    def patch_images(self, ids, patch, log=True):
        """Aendert Crop / Gruppe / Entscheidung fuer mehrere Bilder als EIN Undo-Schritt.

        patch: {"crop": [l,t,r,b] | None (= Korrektur zuruecknehmen),
                "group": "green|yellow|red" | None (= automatisch),
                "decision": "accept|skip" | None,
                "straighten": {"deg": float} | None (= Schraeglage nicht korrigieren)}
        Nur vorhandene Schluessel werden angewendet. Ein Crop im Patch bezieht sich auf den
        Rahmen nach dem Patch (bei "straighten" also auf das geradegestellte Bild)."""
        with self.lock:
            self._require_editable()
            if not ids:
                raise SessionError("keine Bilder angegeben")
            if "crop" in patch and patch["crop"] is not None:
                patch = dict(patch, crop=clamp_crop(patch["crop"]))
            if patch.get("group") not in (None, *GROUPS):
                raise SessionError("ungueltige Gruppe")
            if patch.get("decision") not in (None, "accept", "skip"):
                raise SessionError("ungueltige Entscheidung")
            if patch.get("straighten") is not None:
                if patch["straighten"].get("deg") != "auto":     # "auto" = je Bild der gemessene Wert
                    try:
                        deg = float(patch["straighten"].get("deg"))
                    except (TypeError, ValueError):
                        raise SessionError("straighten.deg muss eine Zahl sein")
                    if abs(deg) > MAX_STRAIGHTEN_DEG:
                        raise SessionError(f"Winkel ausserhalb +/-{MAX_STRAIGHTEN_DEG:g} Grad")
                    patch = dict(patch, straighten={"deg": round(deg, 2)})
            op = {"ids": {}, "t": time.time()}
            for iid in ids:
                img = self.image(iid)
                before, events = {}, []
                if "straighten" in patch:
                    want = patch["straighten"]
                    if want and want.get("deg") == "auto":
                        sk = ((img.get("detected") or {}).get("skew") or {})
                        want = {"deg": sk["deg"]} if sk.get("deg") is not None else None
                        if want is None:
                            op["ids"][str(img["id"])] = before     # nichts zu tun fuer dieses Bild
                            continue
                    before["straighten"] = copy.deepcopy(img.get("straighten"))
                    old_deg = self.straight_deg(img)
                    new_deg = want.get("deg") if want else None
                    new_deg = new_deg if new_deg and abs(new_deg) >= 0.01 else None
                    events.append(("straighten", old_deg, new_deg))
                    img["straighten"] = want if want else False
                if "crop" in patch:
                    if "manual" not in before:
                        before["manual"] = copy.deepcopy(img.get("manual"))
                    new = ({"crop": patch["crop"], "deg": self.straight_deg(img), "at": now_iso()}
                           if patch["crop"] is not None else None)
                    events.append(("crop", (before["manual"] or {}).get("crop"),
                                   patch["crop"]))
                    img["manual"] = new
                if "group" in patch:
                    before["group_override"] = img.get("group_override")
                    events.append(("group", self.group_of(img), patch["group"]))
                    img["group_override"] = patch["group"]
                if "decision" in patch:
                    before["decision"] = img.get("decision")
                    events.append(("decision", img.get("decision"), patch["decision"]))
                    img["decision"] = patch["decision"]
                op["ids"][str(img["id"])] = before
                if log:
                    for kind, old, new in events:
                        self._feedback(img, kind, old, new)
            self.history.append(op)
            del self.history[:-200]
            self.save()

    def apply_roll_size(self, ref_id):
        """Uebertraegt die Groesse des Crops von ``ref_id`` auf die uebrigen Bilder derselben Rolle.

        Die Rollengroesse ist der wirksamste Hebel (ein Wert je Rolle erklaert etwa 95 % der Treffer). Jedes Bild behaelt
        seine erkannte Mitte, nur Breite und Hoehe werden angeglichen (Orientierung je Bild beibehalten). Bilder mit
        eigener manueller Korrektur bleiben unangetastet. Ein Undo-Schritt; es entsteht kein Feedback-Eintrag, denn die
        abgeleiteten Crops sind keine Handarbeit.
        Rueckgabe: Zahl der geaenderten Bilder."""
        with self.lock:
            self._require_editable()
            ref = self.image(ref_id)
            size = ref.get("export_size")
            crop = self.effective_crop(ref)
            if not size or not crop:
                raise SessionError("Referenzbild hat keinen Crop")
            W0, H0 = size
            rw, rh = (crop[2] - crop[0]) * W0, (crop[3] - crop[1]) * H0
            long_px, short_px = max(rw, rh), min(rw, rh)
            op, n = {"ids": {}, "t": time.time()}, 0
            for img in self.state["images"].values():
                if img["film"] != ref["film"] or img["id"] == ref["id"] or img.get("manual"):
                    continue
                cur, isz = self.effective_crop(img), img.get("export_size")
                if not cur or not isz:
                    continue
                W, H = isz
                cw, ch = (cur[2] - cur[0]) * W, (cur[3] - cur[1]) * H
                landscape = cw >= ch
                w, h = (long_px, short_px) if landscape else (short_px, long_px)
                new = _fit_box((cur[0] + cur[2]) / 2 * W, (cur[1] + cur[3]) / 2 * H, w, h, W, H)
                op["ids"][str(img["id"])] = {"manual": copy.deepcopy(img.get("manual"))}
                img["manual"] = {"crop": new, "deg": self.straight_deg(img), "at": now_iso(), "derived": "roll"}
                n += 1
            if n:
                self.history.append(op)
                del self.history[:-200]
                self.save()
            return n

    def _learn_convention(self):
        """Handcrops dieser Sitzung in die gespeicherte Konvention (mm) einfliessen lassen, hoechstens einmal je Stand."""
        try:
            import film_scale
            conv = self.learned_convention()
            if conv and conv != self.state.get("convention_learned"):
                if film_scale.update_convention(conv):
                    self.state["convention_learned"] = conv
        except Exception:      # noqa: BLE001 - Lernen darf das Abschliessen nie verhindern
            pass

    def learned_convention(self):
        """Crop-Groesse in mm aus den von Hand gesetzten Crops dieser Sitzung (Median), oder None.

        Nur Rollen mit verlaesslich gemessenem Perforationstakt und nur echte Handarbeit (keine abgeleiteten Crops).
        Verlaesslich heisst: die Rolle hat ihren Takt auf beiden Haelften bestaetigt (``agree``, siehe
        ``film_scale.measure_roll_pitch``); eine Fehlmessung wuerde die gelernte mm-Konvention sonst um ihren
        eigenen Fehler verschieben. Wo keine Uebereinstimmung vorliegt (aeltere Sitzungen), zaehlt der Score.
        Ergebnis: {"long_mm", "short_mm", "n"}; wird von ``auto_crop_negative`` als Voreinstellung gelesen."""
        longs, shorts = [], []
        for img in self.state["images"].values():
            man, size = img.get("manual"), img.get("export_size")
            sc = (self.state.get("film_scales") or {}).get(img["film"])
            if not man or man.get("derived") or not size or not sc:
                continue
            agree = sc.get("agree")
            if agree is None:
                if sc.get("score", 0) < 0.4:
                    continue
            elif agree > SCALE_AGREE_MAX:
                continue
            mm_px = sc["pitch_frac"] * max(size) / PITCH_MM
            c = man["crop"]
            if man.get("deg"):
                c = crop_from_straight(c, size, man["deg"])
            w, h = (c[2] - c[0]) * size[0] / mm_px, (c[3] - c[1]) * size[1] / mm_px
            if 0.6 < max(w, h) / max(min(w, h), 1e-6) < 2.0 and max(w, h) > 30:   # nur 35-mm-artige Rahmen
                longs.append(max(w, h))
                shorts.append(min(w, h))
        if len(longs) < 3:
            return None
        longs.sort()
        shorts.sort()
        return {"long_mm": round(longs[len(longs) // 2], 2), "short_mm": round(shorts[len(shorts) // 2], 2),
                "n": len(longs)}

    def undo(self, scope="session", ids=None):
        """Macht die letzte Aenderung rueckgaengig: 'session' = letzte Aktion,
        'selection' = letzte Aktion, die eines der Bilder in ``ids`` betraf
        (nur fuer diese Bilder)."""
        with self.lock:
            self._require_editable()
            target = None
            if scope == "selection":
                sel = {str(i) for i in (ids or [])}
                for op in reversed(self.history):
                    if sel & set(op["ids"]):
                        target = op
                        break
            elif self.history:
                target = self.history[-1]
            if target is None:
                return False
            only = {str(i) for i in ids} if scope == "selection" else None
            for iid, before in list(target["ids"].items()):
                if only is not None and iid not in only:
                    continue
                img = self.image(iid)
                for k, v in before.items():
                    img[k] = v
                del target["ids"][iid]
            if not target["ids"]:
                self.history.remove(target)
            self.save()
            return True

    def apply_detection(self, results, film_aspects=None, film_scales=None):
        """Uebernimmt Erkennungsergebnisse ``{id: result_dict}`` (Pixel -> normalisiert).

        result: Ausgabe der Erkennung (x,y,width,height,_img_w,_img_h,confidence,...)
        oder {"error": "..."}. Vorhandene manuelle Korrekturen bleiben erhalten."""
        with self.lock:
            for iid, r in results.items():
                img = self.image(iid)
                if r.get("error"):
                    img["status"] = "error"
                    img["error"] = str(r["error"])
                    continue
                w, h = r["_img_w"], r["_img_h"]
                img["export_size"] = [w, h]
                if r.get("x") is None or not r.get("width"):
                    img["status"] = "error"
                    img["error"] = "kein Rahmen erkannt"
                    continue
                img["detected"] = {
                    "crop": crop_from_pixels(r["x"], r["y"], r["width"],
                                             r["height"], w, h),
                    "confidence": round(float(r.get("confidence", 0.0)), 3),
                    "method": r.get("method"),
                    "reasons": r.get("reasons", []),
                    "orientation": r.get("orientation"),
                    "conf_parts": r.get("_conf_parts"),
                    "skew": r.get("_skew"),
                    "at": now_iso(),
                }
                img["status"] = "done"
                img["error"] = None
            if film_aspects:
                self.state.setdefault("film_aspects", {}).update(film_aspects)
            if film_scales:
                # nur was fuer die Konvention gebraucht wird: Takt als Bruchteil der langen Bildkante und seine Guete
                self.state.setdefault("film_scales", {}).update(
                    {f: {"pitch_frac": v["pitch_frac"], "score": v.get("score", 0.0), "agree": v.get("agree")}
                     for f, v in film_scales.items() if v.get("pitch_frac")})
            if self.phase == "analyzing" and not any(
                    i.get("status") == "pending" for i in self.state["images"].values()):
                self.state["phase"] = "reviewing"
            self.save()

    def set_proposals(self, results, settings=None):
        """Ergebnisse der Neu-Erkennung als Vorschlag ablegen (ueberschreibt nichts)."""
        with self.lock:
            for iid, r in results.items():
                img = self.image(iid)
                if r.get("error") or r.get("x") is None or not r.get("width"):
                    img["proposal"] = {"error": str(r.get("error") or "kein Rahmen erkannt")}
                    continue
                w, h = r["_img_w"], r["_img_h"]
                img["proposal"] = {
                    "crop": crop_from_pixels(r["x"], r["y"], r["width"], r["height"], w, h),
                    "confidence": round(float(r.get("confidence", 0.0)), 3),
                    "method": r.get("method"), "reasons": r.get("reasons", []),
                    "orientation": r.get("orientation"),
                    "conf_parts": r.get("_conf_parts"),
                    "skew": r.get("_skew"),
                    "settings": settings or {}, "at": now_iso(),
                }
                img["export_size"] = [w, h]
            self.save()

    def accept_proposals(self, ids):
        """Uebernimmt Vorschlaege der Neu-Erkennung als neue automatische Erkennung."""
        with self.lock:
            self._require_editable()
            op = {"ids": {}, "t": time.time()}
            for iid in ids:
                img = self.image(iid)
                p = img.get("proposal")
                if not p or p.get("error"):
                    continue
                op["ids"][str(img["id"])] = {
                    "detected": copy.deepcopy(img.get("detected")),
                    "proposal": copy.deepcopy(p), "status": img.get("status"),
                    "error": img.get("error")}
                self._feedback(img, "redetect_accept",
                               (img.get("detected") or {}).get("crop"), p["crop"])
                img["detected"] = {k: v for k, v in p.items() if k != "settings"}
                img["proposal"] = None
                img["status"], img["error"] = "done", None
            if op["ids"]:
                self.history.append(op)
            self.save()

    def discard_proposals(self, ids):
        with self.lock:
            self._require_editable()
            for iid in ids:
                self.image(iid)["proposal"] = None
            self.save()

    def set_export(self, iid, rel_path):
        with self.lock:
            self.image(iid)["export"] = rel_path

    def mark_analysis_done(self):
        with self.lock:
            if self.phase == "analyzing":
                for img in self.state["images"].values():
                    if img.get("status") == "pending":
                        img["status"] = "error"
                        img["error"] = img.get("error") or "nicht analysiert"
                self.state["phase"] = "reviewing"
                self.save()

    # -- Fertig / Plan / Rueckkehr -------------------------------------------

    @staticmethod
    def _entry_key(entry):
        return (entry.get("apply"), tuple(entry.get("crop") or ()), entry.get("label"),
                entry.get("angle") or 0.0)

    def _entry_changed(self, img, applied_entry):
        """Weicht der aktuelle Stand vom zuletzt angewendeten ab?"""
        cur = {"apply": self.will_apply(img), "crop": self.effective_crop(img),
               "label": self.group_of(img), "angle": self.straight_deg(img)}
        if applied_entry is None:
            return True                # nie angewendet (auch das Label zaehlt)
        return self._entry_key(cur) != self._entry_key(applied_entry)

    def build_plan(self):
        applied = (read_json(self.path("applied.json"), {}) or {}).get("images", {})
        entries, warnings = [], []
        for img in self.state["images"].values():
            fp_now = fingerprint(img["path"])
            stale = (self.mode != "folder"
                     and (fp_now is None or fp_now != img.get("fingerprint")))
            entry = {
                "id": img["id"], "path": img["path"],
                "size": (img.get("fingerprint") or {}).get("size"),
                "crop": self.effective_crop(img),
                "angle": self.straight_deg(img),   # Grad; darktable-Rotation = dieser Wert
                "label": self.group_of(img),
                "apply": self.will_apply(img) and not stale,
            }
            if stale:
                entry["skip_reason"] = "changed"
                warnings.append({"id": img["id"], "filename": img["filename"],
                                 "reason": "changed"})
            prev = applied.get(str(img["id"])) or {}
            entry["was_applied"] = bool(prev.get("apply"))   # das Ziel nimmt den Crop dann zurueck
            entry["was_angle"] = prev.get("angle")           # ... und ggf. die Drehung
            entry["changed"] = self._entry_changed(img, applied.get(str(img["id"])))
            if stale:
                entry["changed"] = False
            entries.append(entry)
        return entries, warnings

    def finish(self):
        """Fertig: sperrt die Sitzung und schreibt plan.json (atomar). Ein lokales Ziel
        (targets/) wendet den Plan sofort an; darktable wartet auf "Plan anwenden"."""
        with self.lock:
            if self.phase != "reviewing":
                raise SessionError("Fertig ist nur in der Pruefung moeglich", 409,
                                   phase=self.phase)
            entries, warnings = self.build_plan()
            plan = {
                "v": 1, "session": self.state["session"], "phase": "locked",
                "revision": self.revision, "locked_at": now_iso(),
                "plan_sha256": content_hash(entries), "images": entries,
                "complete": True,
            }
            atomic_write_json(self.path("plan.json"), plan)
            self.state["phase"] = "locked"
            self.history.clear()
            self._learn_convention()
            self.save()
            summary = self._summary()
            target = self.target
            if not target.external:
                self._apply_local(target, plan)
            return {"plan": {k: plan[k] for k in ("revision", "locked_at")},
                    "warnings": warnings, "summary": summary}

    def reopen(self):
        """Zurueck zur Pruefung: entsperrt, Revision + 1 (aus locked/applied/apply_failed)."""
        with self.lock:
            if self.phase not in REOPENABLE:
                raise SessionError("nichts zum Wiederoeffnen", 409, phase=self.phase)
            old = self.revision
            for name in ("plan.json", "result.json"):
                p = self.path(name)
                if os.path.exists(p):
                    os.replace(p, self.path(f"{name[:-5]}-rev{old}.json"))
            self.state["revision"] = old + 1
            self.state["phase"] = "reviewing"
            self._result_mtime = None
            self.save()

    def refresh_from_disk(self):
        """Liest result.json (von Lua) und setzt die Phase auf applied/apply_failed."""
        with self.lock:
            if self.phase != "locked":
                return
            p = self.path("result.json")
            try:
                mtime = os.stat(p).st_mtime
            except OSError:
                return
            if mtime == self._result_mtime:
                return
            self._result_mtime = mtime
            res = read_json(p)
            if not isinstance(res, dict) or res.get("revision") != self.revision:
                return
            status = res.get("status", "ok")
            imgs = res.get("images") or {}
            if status != "failed":
                plan = read_json(self.path("plan.json"), {}) or {}
                applied = read_json(self.path("applied.json"),
                                    {"images": {}}) or {"images": {}}
                by_id = {str(e["id"]): e for e in plan.get("images", [])}
                for iid, r in imgs.items():
                    e = by_id.get(str(iid))
                    if e and r.get("status") in ("ok", "skipped"):
                        applied["images"][str(iid)] = {
                            "apply": e["apply"], "crop": e["crop"],
                            "label": e["label"], "angle": e.get("angle")}
                applied["revision"] = self.revision
                atomic_write_json(self.path("applied.json"), applied)
            self.state["phase"] = "apply_failed" if status == "failed" else "applied"
            self.save()

    # -- Feedback / Ordnermodus ---------------------------------------------

    def _feedback(self, img, kind, old, new):
        det = img.get("detected") or {}
        line = {"t": now_iso(), "rev": self.revision, "id": img["id"],
                "film": img["film"], "file": img["filename"], "event": kind,
                "from": old, "to": new,
                "detected_crop": det.get("crop"),
                "confidence": det.get("confidence"),
                "conf_parts": det.get("conf_parts"),
                "export_size": img.get("export_size"),
                "method": det.get("method")}
        try:
            with open(self.path("feedback.jsonl"), "a", encoding="utf-8") as f:
                f.write(json.dumps(line, ensure_ascii=False) + "\n")
        except OSError:
            pass

    def _apply_local(self, target, plan):
        """Lokales Ziel anwenden und wie bei darktable result.json/applied.json fuehren.

        Nur Bilder mit Status ok/skipped zaehlen als angewendet; ein Fehler bleibt "geaendert"
        und wird beim naechsten Fertig erneut versucht."""
        try:
            report = target.apply(self, plan)
        except Exception as e:     # noqa: BLE001 - Fehler sichtbar machen, Sitzung bleibt bedienbar
            atomic_write_json(self.path("result.json"), {
                "revision": self.revision, "status": "failed", "message": str(e), "images": {}})
            self.state["phase"] = "apply_failed"
            self.save()
            return
        applied = read_json(self.path("applied.json"), {"images": {}}) or {"images": {}}
        applied.setdefault("images", {})
        for e in plan["images"]:
            r = (report or {}).get(str(e["id"]), {"status": "ok"})
            if r.get("status") in ("ok", "skipped"):
                applied["images"][str(e["id"])] = {
                    "apply": e["apply"], "crop": e["crop"], "label": e["label"],
                    "angle": e.get("angle")}
        applied["revision"] = self.revision
        atomic_write_json(self.path("applied.json"), applied)
        atomic_write_json(self.path("result.json"), {
            "revision": self.revision, "status": "ok", "target": target.name,
            "images": report or {}})
        self.state["phase"] = "applied"
        self.save()


def _target_for_mode(mode):
    return {targets.MODE_DARKTABLE: targets.DARKTABLE, targets.MODE_FOLDER: targets.REVIEWS}.get(mode, "json")


# ── Aufraeumen / Auffinden ───────────────────────────────────────────────────

def list_sessions(root=None):
    root = root or DEFAULT_ROOT
    if not os.path.isdir(root):
        return []
    out = []
    for name in sorted(os.listdir(root)):
        d = os.path.join(root, name)
        if os.path.isfile(os.path.join(d, "state.json")):
            out.append(d)
    return out


def cleanup_old(root=None, days=CACHE_DAYS, now=None):
    """Loescht Sitzungen, die seit ``days`` Tagen unberuehrt sind. Gesperrte
    (locked) Sitzungen bleiben, denn ihr Plan wartet auf "Plan anwenden"."""
    now = now or time.time()
    removed = []
    for d in list_sessions(root):
        try:
            mtime = os.stat(os.path.join(d, "state.json")).st_mtime
            state = read_json(os.path.join(d, "state.json"), {}) or {}
        except OSError:
            continue
        if state.get("phase") == "locked":
            continue
        if now - mtime > days * 86400:
            shutil.rmtree(d, ignore_errors=True)
            removed.append(d)
    return removed
