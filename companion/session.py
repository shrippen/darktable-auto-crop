"""Sitzungszustand der Companion-UI.

Eine Sitzung liegt komplett auf der Platte (``<root>/<session-id>/``), damit sie
einen Neustart des Servers ueberlebt ("Pruefung oeffnen" in darktable):

    job.json        Eingabe (Bilder, Einstellungen), von Lua bzw. der CLI
    state.json      veraenderlicher Zustand (Phase, Revision, Bilder, Korrekturen)
    plan.json       Uebergabe an darktable (nur in der Phase "locked")
    result.json     Rueckmeldung von darktable nach "Plan anwenden"
    applied.json    zuletzt angewendete Crops/Labels (Grundlage fuer Differenzen)
    feedback.jsonl  Protokoll aller Korrekturen (Kalibrierdaten)
    server.json     laufender Server (pid, port, token, url)
    exports/        darktable-Exporte (voller Aufloesung, JPEG)
    thumbs/         abgeleitete Vorschauen

Alle Crops sind normalisiert ``[links, oben, rechts, unten]`` (0..1) und beziehen
sich auf den (bereits gedrehten) Export-Rahmen. Das ist exakt das
Koordinatensystem von darktables Crop-Modul (siehe companion-ui-plan.md, Abschnitt 12).
"""
import copy
import hashlib
import json
import os
import shutil
import threading
import time
import uuid
from datetime import datetime, timezone

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
        "mode"?: "darktable"|"folder", ...}``."""
        root = root or DEFAULT_ROOT
        sid = job.get("session") or new_session_id()
        directory = os.path.join(root, sid)
        os.makedirs(os.path.join(directory, "exports"), exist_ok=True)
        os.makedirs(os.path.join(directory, "thumbs"), exist_ok=True)
        settings = dict(DEFAULT_SETTINGS)
        settings.update(job.get("settings") or {})
        mode = job.get("mode", "darktable")
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

    def effective_crop(self, img):
        if img.get("manual"):
            return img["manual"]["crop"]
        det = img.get("detected")
        return det["crop"] if det else None

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
            "detected_crop": det.get("crop"),
            "manual_crop": (img.get("manual") or {}).get("crop"),
            "crop": self.effective_crop(img),
            "decision": img.get("decision"),
            "proposal": self._public_proposal(img),
            "apply": self.will_apply(img),
            "export_size": img.get("export_size"),
            "has_export": bool(self.export_path(img)
                               and os.path.exists(self.export_path(img))),
        }

    @staticmethod
    def _public_proposal(img):
        p = img.get("proposal")
        if not p:
            return None
        if p.get("error"):
            return {"error": p["error"]}
        return {"crop": p["crop"], "confidence": p.get("confidence"),
                "method": p.get("method"), "reasons": p.get("reasons", [])}

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
        for img in self.state["images"].values():
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
                "decision": "accept|skip" | None}
        Nur vorhandene Schluessel werden angewendet."""
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
            op = {"ids": {}, "t": time.time()}
            for iid in ids:
                img = self.image(iid)
                before, events = {}, []
                if "crop" in patch:
                    before["manual"] = copy.deepcopy(img.get("manual"))
                    new = ({"crop": patch["crop"], "at": now_iso()}
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

    def apply_detection(self, results, film_aspects=None):
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
                    "at": now_iso(),
                }
                img["status"] = "done"
                img["error"] = None
            if film_aspects:
                self.state.setdefault("film_aspects", {}).update(film_aspects)
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
        return (entry.get("apply"), tuple(entry.get("crop") or ()), entry.get("label"))

    def _entry_changed(self, img, applied_entry):
        """Weicht der aktuelle Stand vom zuletzt angewendeten ab?"""
        cur = {"apply": self.will_apply(img), "crop": self.effective_crop(img),
               "label": self.group_of(img)}
        if applied_entry is None:
            return True                # nie angewendet (auch das Label zaehlt)
        return self._entry_key(cur) != self._entry_key(applied_entry)

    def build_plan(self):
        applied = (read_json(self.path("applied.json"), {}) or {}).get("images", {})
        entries, warnings = [], []
        for img in self.state["images"].values():
            fp_now = fingerprint(img["path"])
            stale = (self.mode == "darktable"
                     and (fp_now is None or fp_now != img.get("fingerprint")))
            entry = {
                "id": img["id"], "path": img["path"],
                "size": (img.get("fingerprint") or {}).get("size"),
                "crop": self.effective_crop(img),
                "label": self.group_of(img),
                "apply": self.will_apply(img) and not stale,
            }
            if stale:
                entry["skip_reason"] = "changed"
                warnings.append({"id": img["id"], "filename": img["filename"],
                                 "reason": "changed"})
            prev = applied.get(str(img["id"])) or {}
            entry["was_applied"] = bool(prev.get("apply"))   # Lua schaltet dann den Crop wieder aus
            entry["changed"] = self._entry_changed(img, applied.get(str(img["id"])))
            if stale:
                entry["changed"] = False
            entries.append(entry)
        return entries, warnings

    def finish(self):
        """Fertig: sperrt die Sitzung und schreibt plan.json (atomar)."""
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
            self.save()
            summary = self._summary()
            if self.mode == "folder":
                self._finish_folder()
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
                            "label": e["label"]}
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

    def _finish_folder(self):
        """Ordnermodus (Kalibrierung ohne darktable): Korrekturen im Format von
        review_data/reviews.json ablegen und die Sitzung als angewendet fuehren."""
        src = self.state.get("source", {})
        reviews_path = src.get("reviews")
        if reviews_path:
            reviews = read_json(reviews_path, {}) or {}
            for img in self.state["images"].values():
                size = img.get("export_size")
                key = f"{img['film']}/{img['filename']}"
                man = img.get("manual")
                if man and size:
                    rev = reviews.get(key, {})
                    rev["manual_crop"] = crop_to_pixels(man["crop"], *size)
                    if self.group_of(img) == "red":
                        rev["is_problem"] = True
                    reviews[key] = rev
                elif self.group_of(img) == "red" and img.get("group_override") == "red":
                    rev = reviews.get(key, {"manual_crop": None})
                    rev["is_problem"] = True
                    reviews[key] = rev
            tmp = reviews_path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(reviews, f, indent=2, ensure_ascii=False)
            os.replace(tmp, reviews_path)
        self.state["phase"] = "applied"
        applied = {"revision": self.revision, "images": {}}
        for e in read_json(self.path("plan.json"), {}).get("images", []):
            applied["images"][str(e["id"])] = {
                "apply": e["apply"], "crop": e["crop"], "label": e["label"]}
        atomic_write_json(self.path("applied.json"), applied)
        atomic_write_json(self.path("result.json"), {
            "revision": self.revision, "status": "ok", "images": {}})
        self.save()


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
