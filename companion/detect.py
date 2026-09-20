"""Analyse und Neu-Erkennung fuer eine Sitzung.

Die eigentliche Erkennung bleibt in ``auto_crop_negative.py``; dieses Modul
- exportiert Raws (``export.py``) in einen Ordner je Filmrolle, damit der
  Film-Konsens (ein Ordner = eine Rolle) weiter funktioniert,
- ruft ``compute_batch`` fuer die Standardanalyse auf,
- bietet eine Einzelbild-Erkennung mit frei waehlbaren Parametern
  (Seitenverhaeltnis, Filmrand-Schwelle, Aspekt-Strafe, Verfeinerung) fuer die
  Neu-Erkennung und liefert Kandidaten fuer den Crop-Editor.
"""
import collections
import concurrent.futures as cf
import os
import re
import sys
import threading

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from . import export as exp          # noqa: E402
from .session import SessionError, crop_from_pixels   # noqa: E402
from .thumbs import image_size       # noqa: E402


def _acn():
    import auto_crop_negative as acn   # importiert cv2/numpy erst bei Bedarf
    return acn


def _single_worker(item):
    """Einzelbild-Erkennung mit Parametern (laeuft im Prozesspool)."""
    path, aspect, fbl, no_penalty, skip_refine = item
    acn = _acn()
    img = acn.load_image(path)
    full, small, scale = acn._prepare_detect_gray(img)
    r = acn.find_best_crop(small, target_ratio=aspect, film_border_level=fbl,
                           no_aspect_penalty=no_penalty, skip_refine=skip_refine)
    if scale < 1.0:
        for k in ("x", "y", "width", "height"):
            if r.get(k) is not None:
                r[k] = int(round(r[k] / scale))
    r["input_file"] = os.path.abspath(path)
    r["_img_w"], r["_img_h"] = full.shape[1], full.shape[0]
    return r


def _skew_worker(item):
    """Schraeglage des Filmrahmens am erkannten Crop messen (Grad, + = im Uhrzeigersinn)."""
    path, x, y, w, h = item
    acn = _acn()
    _, small, scale = acn._prepare_detect_gray(acn.load_image(path))
    return acn.measure_skew(small, int(x * scale), int(y * scale),
                            max(8, int(w * scale)), max(8, int(h * scale)))


def measure_skews(results, paths, workers=None):
    """Haengt jedem Ergebnis ``_skew`` an. ``paths`` = {abs_pfad: id}, ``results`` = {id: result}.
    Fehler bei der Messung machen die Erkennung nicht ungueltig (dann fehlt nur die Angabe)."""
    items = {}
    for path, iid in paths.items():
        r = results.get(iid)
        if r and not r.get("error") and r.get("x") is not None and r.get("width"):
            items[iid] = (path, r["x"], r["y"], r["width"], r["height"])
    if not items:
        return
    with cf.ThreadPoolExecutor(max_workers=workers or min(4, os.cpu_count() or 2)) as ex:
        futs = {ex.submit(_skew_worker, it): iid for iid, it in items.items()}
        for fut in cf.as_completed(futs):
            try:
                results[futs[fut]]["_skew"] = fut.result()
            except Exception:      # noqa: BLE001 - Diagnose, nie fatal
                pass


def _candidates_worker(item):
    path, aspect = item
    acn = _acn()
    img = acn.load_image(path)
    full, small, scale = acn._prepare_detect_gray(img)
    dump = acn.find_best_crop(small, target_ratio=aspect, dump_all=True)
    h, w = small.shape
    out = []
    for c in dump.get("candidates", []):
        out.append({"crop": [c["x"] / w, c["y"] / h,
                             (c["x"] + c["width"]) / w, (c["y"] + c["height"]) / h],
                    "confidence": c.get("confidence"), "method": c.get("method")})
    b = dump.get("best_refined")
    if b:
        out.append({"crop": [b["x"] / w, b["y"] / h,
                             (b["x"] + b["width"]) / w, (b["y"] + b["height"]) / h],
                    "confidence": b.get("confidence"), "method": "refined"})
    return out


def film_slug(name, used):
    """Ordnername je Filmrolle; zwei verschiedene Rollen mit gleichem Namen bleiben getrennt."""
    base = re.sub(r"[^\w.\- ]+", "_", name).strip() or "film"
    slug, n = base, 1
    while used.get(slug, name) != name:
        n += 1
        slug = f"{base}_{n}"
    used[slug] = name
    return slug


class Busy(SessionError):
    def __init__(self):
        super().__init__("es laeuft bereits eine Analyse", 409, busy=True)


class Analyzer:
    """Fuehrt Analysen im Hintergrund aus (immer nur eine gleichzeitig)."""

    def __init__(self, session, emit=None):
        self.session = session
        self.emit = emit or (lambda e: None)
        self.log = collections.deque(maxlen=500)
        self.progress = {"stage": "", "done": 0, "total": 0, "busy": False}
        self._busy = threading.Lock()
        self._thread = None
        self._cand_cache = {}

    # -- Statusmeldungen -----------------------------------------------------

    def report(self, msg):
        msg = str(msg)
        if msg.startswith("PROGRESS "):
            try:
                a, b = msg.split()[1].split("/")
                self.progress.update(done=int(a), total=int(b))
                self.emit({"type": "progress", **self.progress})
            except ValueError:
                pass
            return
        self.log.append(msg)
        self.emit({"type": "log", "line": msg})

    def _stage(self, name, done=0, total=0):
        self.progress.update(stage=name, done=done, total=total)
        self.emit({"type": "progress", **self.progress})

    # -- Start ---------------------------------------------------------------

    def start_analysis(self):
        """Analysiert alle Bilder ohne Ergebnis (Neustart-fest: bereits erkannte bleiben)."""
        ids = [i["id"] for i in self.session.state["images"].values()
               if i.get("status") == "pending"]
        self._run(self._analyze, ids)

    def start_redetect(self, ids, settings):
        if not ids:
            raise SessionError("keine Bilder ausgewaehlt")
        self._run(self._redetect, ids, settings or {})

    def _run(self, fn, *args):
        if not self._busy.acquire(blocking=False):
            raise Busy()
        self.progress["busy"] = True

        def target():
            try:
                fn(*args)
            except Exception as e:      # noqa: BLE001 - Fehler sichtbar machen, nicht sterben
                self.report(f"FEHLER: {e}")
            finally:
                self.progress.update(busy=False, stage="")
                self.emit({"type": "progress", **self.progress})
                self._busy.release()
                self.emit({"type": "state"})

        self._thread = threading.Thread(target=target, daemon=True)
        self._thread.start()

    def wait(self, timeout=None):
        if self._thread:
            self._thread.join(timeout)

    # -- Analyse --------------------------------------------------------------

    def _prepare_paths(self, ids):
        """Export (falls noetig) und Liefert {abs_export_path: id}."""
        s = self.session
        used, jobs, paths = {}, [], {}
        for iid in ids:
            img = s.image(iid)
            if exp.is_raw(img["path"]):
                if not img.get("export") or not os.path.exists(s.export_path(img) or ""):
                    slug = film_slug(img["film"], used)
                    rel = os.path.join("exports", slug, exp.export_name(
                        os.path.splitext(img["filename"])[0], img["id"]))
                    jobs.append((iid, img["path"], s.path(rel)))
                    s.set_export(iid, rel)
            else:
                s.set_export(iid, os.path.abspath(img["path"]))
        if jobs:
            self._stage("export", 0, len(jobs))
            done = [0]
            failed = {}

            def on_done(iid, out, err):
                done[0] += 1
                self.progress.update(done=done[0])
                self.emit({"type": "progress", **self.progress})
                if err:
                    failed[iid] = err

            exp.export_many(jobs, on_done)
            for iid, err in failed.items():
                s.image(iid)["export"] = None
                s.apply_detection({iid: {"error": f"Export: {err}"}})
        for iid in ids:
            img = s.image(iid)
            p = s.export_path(img)
            if p and os.path.exists(p):
                paths[os.path.abspath(p)] = iid
        return paths

    def _analyze(self, ids):
        s = self.session
        if not ids:
            s.mark_analysis_done()
            return
        acn = _acn()
        paths = self._prepare_paths(ids)
        self._stage("detect", 0, 2 * max(len(paths), 1))
        st = s.state["settings"]
        batch = acn.compute_batch(list(paths), st["t_yellow"], False,
                                  st.get("format", "35mm"), report=self.report)
        results = {}
        for r in batch["results"]:
            iid = paths.get(os.path.abspath(r["input_file"]))
            if iid is not None:
                results[iid] = r
        for path, iid in paths.items():
            results.setdefault(iid, {"error": "Erkennung lieferte kein Ergebnis"})
        aspects = {k: v["aspect_ratio"] for k, v in batch["film_aspects"].items()}
        self._stage("skew", 0, 0)
        measure_skews(results, paths)
        s.apply_detection(results, aspects)
        s.mark_analysis_done()

    # -- Neu-Erkennung ---------------------------------------------------------

    def _redetect(self, ids, settings):
        s = self.session
        acn = _acn()
        paths = self._prepare_paths(ids)
        overrides = any(settings.get(k) not in (None, False, "")
                        for k in ("aspect_ratio", "film_border_level",
                                  "no_aspect_penalty", "skip_refine"))
        self._stage("detect", 0, max(len(paths), 1))
        results = {}
        if overrides:
            fmt = settings.get("format") or s.state["settings"].get("format", "35mm")
            items = []
            for path, iid in paths.items():
                img = s.image(iid)
                aspect = (float(settings["aspect_ratio"])
                          if settings.get("aspect_ratio")
                          else s.state.get("film_aspects", {}).get(img["film"])
                          or acn.ASPECT_RATIOS.get(fmt, 1.5))
                fbl = (float(settings["film_border_level"])
                       if settings.get("film_border_level") not in (None, "") else None)
                items.append((path, aspect, fbl, bool(settings.get("no_aspect_penalty")),
                              bool(settings.get("skip_refine"))))
            done = 0
            with cf.ProcessPoolExecutor(max_workers=max(1, min(os.cpu_count() or 2, len(items)))) as ex:
                futs = {ex.submit(_single_worker, it): it[0] for it in items}
                for fut in cf.as_completed(futs):
                    done += 1
                    self.report(f"PROGRESS {done}/{len(items)}")
                    path = futs[fut]
                    try:
                        results[paths[path]] = fut.result()
                    except Exception as e:      # noqa: BLE001
                        results[paths[path]] = {"error": str(e)}
        else:
            fmt = settings.get("format") or s.state["settings"].get("format", "35mm")
            batch = acn.compute_batch(list(paths), s.state["settings"]["t_yellow"],
                                      False, fmt, report=self.report)
            for r in batch["results"]:
                iid = paths.get(os.path.abspath(r["input_file"]))
                if iid is not None:
                    results[iid] = r
        measure_skews(results, paths)
        s.set_proposals(results, settings)

    # -- Kandidaten fuer den Editor ------------------------------------------

    def candidates(self, iid):
        """Alle Kandidaten der Strategien (normalisiert), fuer die Overlays im Editor."""
        if iid in self._cand_cache:
            return self._cand_cache[iid]
        s = self.session
        img = s.image(iid)
        path = s.export_path(img)
        if not path or not os.path.exists(path):
            raise SessionError("kein Export vorhanden", 404)
        acn = _acn()
        fmt = s.state["settings"].get("format", "35mm")
        aspect = (s.state.get("film_aspects", {}).get(img["film"])
                  or acn.ASPECT_RATIOS.get(fmt, 1.5))
        out = _candidates_worker((path, aspect))
        out = sorted((c for c in out if c["confidence"] is not None),
                     key=lambda c: -c["confidence"])[:8]
        for c in out:
            c["crop"] = [round(min(1.0, max(0.0, v)), 5) for v in c["crop"]]
        self._cand_cache[iid] = out
        return out


def measure_size(path):
    """Groesse eines Exports wie die Erkennung sie sieht."""
    return image_size(path)
