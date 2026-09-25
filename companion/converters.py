"""Eingabe-Adapter: RAW -> JPEG in voller Aufloesung, ohne vorhandenen Crop.

    RAW ──┬── darktable     darktable-cli + XMP-Historie (Crop aus)      export.py
          ├── rawtherapee   rawtherapee-cli + .pp3-Profil (Crop aus)
          └── rawpy         LibRaw ueber das Python-Paket rawpy
          │                 (kein externes Programm, keine Bearbeitung)
          ▼
    exports/<rolle>/<name>__<id>.jpg  ->  Erkennung

JPEG/TIFF/PNG werden nicht konvertiert. Eine Sitzung nutzt genau einen Konverter, damit alle
Bilder einer Rolle denselben Rahmen haben (der Rollen-Konsens vergleicht Groessen).
"""
import os
import shutil
import subprocess
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor, as_completed

from . import export as dt_export
from . import pp3

DARKTABLE = "darktable"
RAWTHERAPEE = "rawtherapee"
RAWPY = "rawpy"
NAMES = (DARKTABLE, RAWTHERAPEE, RAWPY)
AUTO = "auto"
JPEG_QUALITY = dt_export.JPEG_QUALITY
TIMEOUT_S = 600


def find_rawtherapee_cli():
    return shutil.which("rawtherapee-cli")


def has_rawpy():
    try:
        import rawpy  # noqa: F401
    except ImportError:
        return False
    return True


def available():
    """{name: True/False}: welche Konverter auf diesem Rechner laufen."""
    return {DARKTABLE: bool(dt_export.find_darktable_cli()),
            RAWTHERAPEE: bool(find_rawtherapee_cli()),
            RAWPY: has_rawpy()}


def resolve(preferred, raw_paths):
    """Konkreter Konverter fuer eine Sitzung, oder None (keine RAWs / keiner verfuegbar).

    ``auto``: das Werkzeug, dessen Sidecars am haeufigsten neben den RAWs liegen (dessen
    Bearbeitung soll in die Erkennung eingehen), sonst der erste verfuegbare in NAMES."""
    if not raw_paths:
        return None
    if preferred and preferred != AUTO:
        return preferred
    avail = available()
    votes = {DARKTABLE: 0, RAWTHERAPEE: 0}
    for p in raw_paths:
        if os.path.isfile(pp3.sidecar_path(p)):
            votes[RAWTHERAPEE] += 1
        if _has_darktable_xmp(p):
            votes[DARKTABLE] += 1
    for name in sorted(votes, key=lambda n: -votes[n]):
        if votes[name] and avail[name]:
            return name
    return next((n for n in NAMES if avail[n]), None)


def export_many(jobs, on_done, name, workers=4):
    """jobs: [(id, raw_path, out_path)]; ``on_done(id, out|None, fehler|None)`` je Bild."""
    if name in (None, DARKTABLE):
        dt_export.export_many(jobs, on_done, workers)
        return
    if name == RAWTHERAPEE:
        cli = find_rawtherapee_cli()
        if not cli:
            _fail_all(jobs, on_done, "rawtherapee-cli nicht gefunden")
            return
        fn = lambda raw, out, work: _export_rawtherapee(cli, raw, out, work)   # noqa: E731
    elif name == RAWPY:
        if not has_rawpy():
            _fail_all(jobs, on_done, "rawpy nicht installiert (pip install rawpy)")
            return
        fn = lambda raw, out, work: _export_rawpy(raw, out)                    # noqa: E731
    else:
        _fail_all(jobs, on_done, f"unbekannter Konverter: {name}")
        return

    work = tempfile.mkdtemp(prefix="autocrop_export_")
    try:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futs = {pool.submit(fn, raw, out, work): iid for iid, raw, out in jobs}
            for fut in as_completed(futs):
                iid = futs[fut]
                try:
                    on_done(iid, fut.result(), None)
                except Exception as e:  # noqa: BLE001 - je Bild melden, nicht abbrechen
                    print(f"WARN: Export {iid}: {e}", file=sys.stderr, flush=True)
                    on_done(iid, None, str(e))
    finally:
        shutil.rmtree(work, ignore_errors=True)


def _fail_all(jobs, on_done, message):
    for iid, _, _ in jobs:
        on_done(iid, None, message)


def _has_darktable_xmp(raw):
    xmp = dt_export.find_xmp(raw)
    if not xmp:
        return False
    try:
        with open(xmp, encoding="utf-8", errors="replace") as f:
            return "darktable:" in f.read(4096)
    except OSError:
        return False


def _export_rawtherapee(cli, raw, out, work_dir):
    """rawtherapee-cli mit dem Sidecar-Profil, dessen Crop ausgeschaltet ist (wie beim darktable-Export)."""
    os.makedirs(os.path.dirname(out), exist_ok=True)
    side = pp3.sidecar_path(raw)
    if dt_export._up_to_date(out, [raw, side]):
        return out

    # -d: Standardprofil des Nutzers als Basis, -p: Sidecar ohne Crop darueber
    cmd = [cli, "-o", out, f"-j{JPEG_QUALITY}", "-Y", "-d"]
    if os.path.isfile(side):
        with open(side, encoding="utf-8", errors="replace") as f:
            stripped = pp3.set_values(f.read(), "Crop", {"Enabled": False})
        prof = tempfile.NamedTemporaryFile("w", suffix=".pp3", dir=work_dir, delete=False, encoding="utf-8")
        with prof:
            prof.write(stripped)
        cmd += ["-p", prof.name]
    cmd += ["-c", raw]

    proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=TIMEOUT_S)
    if not os.path.exists(out):
        raise RuntimeError(f"rawtherapee-cli Export fehlgeschlagen ({proc.returncode}): "
                           f"{(proc.stderr or proc.stdout).strip()[-200:]}")
    return out


def _export_rawpy(raw, out):
    """Entwickelt mit LibRaw-Standardwerten (Kamera-Weissabgleich, Orientierung angewendet)."""
    import rawpy
    from PIL import Image

    os.makedirs(os.path.dirname(out), exist_ok=True)
    if dt_export._up_to_date(out, [raw]):
        return out
    with rawpy.imread(raw) as r:
        rgb = r.postprocess(use_camera_wb=True, output_bps=8)
    tmp = out + ".tmp.jpg"
    Image.fromarray(rgb).save(tmp, "JPEG", quality=JPEG_QUALITY)
    os.replace(tmp, out)
    return out
