"""Raw-Export ueber darktable-cli (volle Aufloesung, ohne vorhandenen Crop).

Warum ohne Crop: ``darktable-cli`` wendet die Historie der XMP an. Waere dort
schon ein Crop drin (etwa aus einem frueheren Lauf), wuerde die Erkennung auf
einem bereits zugeschnittenen Rahmen laufen. Wir kopieren deshalb die XMP in den
Sitzungsordner, setzen alle Crop-Eintraege auf ``enabled="0"`` und uebergeben die
Kopie explizit. Das Original bleibt unberuehrt (siehe companion-ui-plan.md,
Abschnitt 12).
"""
import glob
import os
import re
import shutil
import subprocess
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor, as_completed

from . import programs

RAW_EXTENSIONS = {
    ".nef", ".cr2", ".cr3", ".arw", ".raf", ".orf", ".rw2", ".dng",
    ".pef", ".srw", ".erf", ".kdc", ".3fr", ".mos", ".iiq", ".x3f",
}
CROP_OPERATIONS = ("crop", "clipping")   # "clipping" = aelteres Modul "Crop und Drehen"
JPEG_QUALITY = 95

_LI = re.compile(r"<rdf:li\b[^>]*?/>", re.DOTALL)
_OP = re.compile(r'darktable:operation="([^"]*)"')


def find_darktable_cli():
    return programs.find(programs.DARKTABLE_CLI)


def find_xmp(image_path):
    """Sidecar neben der Bilddatei: ``IMG.ARW.xmp`` (darktable) oder ``IMG.xmp``."""
    for cand in (image_path + ".xmp", os.path.splitext(image_path)[0] + ".xmp"):
        if os.path.isfile(cand):
            return cand
    return None


def xmp_without_crop(text):
    """Setzt alle Crop-Historieneintraege auf enabled=0. Liefert (text, anzahl)."""
    count = 0

    def fix(m):
        nonlocal count
        block = m.group(0)
        op = _OP.search(block)
        if not op or op.group(1) not in CROP_OPERATIONS:
            return block
        new = block.replace('darktable:enabled="1"', 'darktable:enabled="0"')
        if new != block:
            count += 1
        return new

    return _LI.sub(fix, text), count


def is_raw(path):
    return os.path.splitext(path)[1].lower() in RAW_EXTENSIONS


def export_name(stem, iid):
    safe = re.sub(r"[^\w.\-]+", "_", stem)
    return f"{safe}__{iid}.jpg"


def _up_to_date(out, sources):
    try:
        t = os.path.getmtime(out)
    except OSError:
        return False
    return all(os.path.getmtime(s) <= t for s in sources if s and os.path.exists(s))


def export_one(dt_cli, raw, out, work_dir):
    """Exportiert ein Raw nach ``out`` (JPEG, volle Aufloesung). Wirft RuntimeError."""
    os.makedirs(os.path.dirname(out), exist_ok=True)
    xmp = find_xmp(raw)
    if _up_to_date(out, [raw, xmp]):
        return out
    cfg = tempfile.mkdtemp(prefix="cfg_", dir=work_dir)
    try:
        kw = programs.run_kwargs(dict(os.environ, XDG_CONFIG_HOME=cfg,
                                      XDG_CACHE_HOME=os.path.join(cfg, "cache")))
        cmd = [dt_cli, raw]
        if xmp:
            with open(xmp, encoding="utf-8", errors="replace") as f:
                stripped, _ = xmp_without_crop(f.read())
            xcopy = os.path.join(cfg, "sidecar.xmp")
            with open(xcopy, "w", encoding="utf-8") as f:
                f.write(stripped)
            cmd.append(xcopy)
        # KEIN --library (siehe kader.export_raws_via_darktable):
        # sonst wuerde der Standard-Modulstapel statt der echten Historie
        # verwendet. write_sidecar_files=never schuetzt vor jedem Schreiben.
        cmd += [out, "--out-ext", "jpg", "--core",
                "--conf", "write_sidecar_files=never",
                "--conf", f"plugins/imageio/format/jpeg/quality={JPEG_QUALITY}"]
        if os.path.exists(out):
            os.remove(out)
        proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              text=True, timeout=600, **kw)
        if not os.path.exists(out):
            # darktable-cli haengt manchmal einen Zaehler an (out_01.jpg)
            base = os.path.splitext(out)[0]
            cands = sorted(glob.glob(base + "_*.jpg"))
            if cands:
                os.replace(cands[0], out)
            else:
                raise RuntimeError(
                    f"darktable-cli Export fehlgeschlagen ({proc.returncode}): "
                    f"{(proc.stderr or proc.stdout).strip()[-200:]}")
        return out
    finally:
        shutil.rmtree(cfg, ignore_errors=True)


def export_many(jobs, on_done, workers=4):
    """jobs: [(id, raw_path, out_path)]. ``on_done(id, out_path|None, error|None)``
    wird je Bild aufgerufen (aus einem Arbeitsthread)."""
    dt_cli = find_darktable_cli()
    if not dt_cli:
        for iid, _, _ in jobs:
            on_done(iid, None, "darktable-cli nicht gefunden")
        return
    work = tempfile.mkdtemp(prefix="autocrop_export_")
    try:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futs = {pool.submit(export_one, dt_cli, raw, out, work): iid
                    for iid, raw, out in jobs}
            for fut in as_completed(futs):
                iid = futs[fut]
                try:
                    on_done(iid, fut.result(), None)
                except Exception as e:  # noqa: BLE001 - je Bild melden, nicht abbrechen
                    print(f"WARN: Export {iid}: {e}", file=sys.stderr, flush=True)
                    on_done(iid, None, str(e))
    finally:
        shutil.rmtree(work, ignore_errors=True)
