#!/usr/bin/env python3
"""Wandelt digitalisierte RAWs (NEF) in Testfotos um: ungecroppte, invertierte JPEGs.

Fuer jede <raw>.xmp-Sidecar wird der darktable-Stand (Negadoctor, Weissabgleich, Drehung/Flip ...)
per darktable-cli angewendet, mit ZWEI Ausnahmen: `crop` und `ashift` (Rotate and Perspective) werden
abgeschaltet. Die Testfotos sollen den ganzen Rahmen zeigen, damit der Algorithmus ihn selbst finden muss
(wie die 209 vorhandenen Testfotos in Film 27-35).

Die Crops und Drehungen, die du frueher in darktable gesetzt hast, gehen dabei nicht verloren: sie werden
nach review_data/darktable_crops.json geschrieben. Das sind aeltere Handarbeiten, keine bestaetigte Wahrheit;
siehe tools/eval_darktable_crops.py fuer den Vergleich mit dem Algorithmus.

Sicherheit: Die Originale (NEF, XMP) werden nur gelesen. darktable-cli laeuft mit
`write_sidecar_files=never` und einer eigenen Config je Worker, damit weder Sidecars noch die Bibliothek der
laufenden darktable-Instanz veraendert werden. In Testphotos/ landen ausschliesslich <Rolle>/<name>.jpg.

Beispiele:
  tools/convert_testphotos.py --dry-run
  tools/convert_testphotos.py --limit 12 --dst /tmp/probe
  tools/convert_testphotos.py --jobs 6            # alles, bereits vorhandene JPEGs werden uebersprungen
  tools/convert_testphotos.py --nice 0            # normale Prioritaet (Default ist 19, niedrigste)
  tools/convert_testphotos.py --rolls "Film 14" "Film 15"
"""
import argparse
import base64
import binascii
import json
import os
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import threading
import time
import zlib
from concurrent.futures import ThreadPoolExecutor, as_completed

PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_SRC = os.path.expanduser("~/Bilder/Analog/Digitalisieren")
DEFAULT_DST = os.path.join(PROJECT_DIR, "Testphotos")
DEFAULT_LABELS = os.path.join(PROJECT_DIR, "review_data", "darktable_crops.json")
COLLECTIONS = ("Steinfeldts", "Eigene")   # Unterordner der Quelle; Wichmanns ist leer
RAW_EXT = (".nef",)
DISABLE_OPS = ("crop", "ashift")

ENTRY_RE = re.compile(r"<rdf:li\s[^>]*?darktable:operation=\"[^\"]*\"[^>]*?/>", re.S)


def _attr(entry, name):
    m = re.search(name + r'="([^"]*)"', entry)
    return m.group(1) if m else None


def parse_history(xmp_text):
    """Wirksame Historie: je (Modul, Instanz) der letzte Eintrag mit num < history_end."""
    m = re.search(r'darktable:history_end="(\d+)"', xmp_text)
    history_end = int(m.group(1)) if m else 10 ** 9
    last = {}
    for e in ENTRY_RE.findall(xmp_text):
        num = _attr(e, "darktable:num")
        if num is None or int(num) >= history_end:
            continue
        last[(_attr(e, "darktable:operation"), _attr(e, "darktable:multi_priority"))] = dict(
            op=_attr(e, "darktable:operation"), enabled=_attr(e, "darktable:enabled") == "1",
            params=_attr(e, "darktable:params"), version=_attr(e, "darktable:modversion"), text=e)
    return last


def decode_params(p):
    if not p:
        return None
    if p.startswith("gz"):
        return zlib.decompress(base64.b64decode(p[4:]))
    try:
        return binascii.unhexlify(p)
    except (binascii.Error, ValueError):
        return None


def read_darktable_crop(xmp_text):
    """(crop, rotation) aus der Sidecar. crop = [links, oben, rechts, unten] als Bruchteile der Zeichenflaeche
    nach ashift (= gedrehte Flaeche), oder None, wenn kein wirksamer Crop gesetzt ist. rotation in Grad oder None;
    'complex' gilt, wenn ashift mehr als eine Drehung enthaelt (Perspektive/Shear)."""
    hist = parse_history(xmp_text)
    crops = []
    for (op, _mp), e in hist.items():
        if op != "crop" or not e["enabled"]:
            continue
        b = decode_params(e["params"])
        if not b or len(b) < 16:
            continue
        l, t, r, bt = struct.unpack("<4f", b[:16])
        if abs(l) > 1e-3 or abs(t) > 1e-3 or abs(r - 1) > 1e-3 or abs(bt - 1) > 1e-3:
            crops.append([l, t, r, bt])
    rot, complex_ = None, False
    for (op, _mp), e in hist.items():
        if op != "ashift" or not e["enabled"]:
            continue
        b = decode_params(e["params"])
        if not b or len(b) < 16:
            continue
        rotation, lens_v, lens_h, shear = struct.unpack("<4f", b[:16])
        rot = (rot or 0.0) + rotation
        complex_ = complex_ or any(abs(v) > 1e-3 for v in (lens_v, lens_h, shear))
    if not crops:
        return None, rot, complex_, len(crops)
    return crops[-1], rot, complex_, len(crops)


def needs_neutralizing(xmp_text):
    return any(e["enabled"] and e["op"] in DISABLE_OPS for e in parse_history(xmp_text).values())


def neutralize_xmp(xmp_text):
    """Kopie der Sidecar, in der jeder crop-/ashift-Eintrag ausgeschaltet ist."""
    def repl(m):
        s = m.group(0)
        op = _attr(s, "darktable:operation")
        return s.replace('darktable:enabled="1"', 'darktable:enabled="0"') if op in DISABLE_OPS else s
    return ENTRY_RE.sub(repl, xmp_text)


def find_rolls(src, collections):
    """{Rollenname: [Rohdatei ...]}; bei doppeltem Namen wird der Sammlungsname vorangestellt."""
    found = []
    for col in collections:
        cdir = os.path.join(src, col)
        if not os.path.isdir(cdir):
            continue
        for roll in sorted(os.listdir(cdir)):
            rdir = os.path.join(cdir, roll)
            if not os.path.isdir(rdir):
                continue
            raws = sorted(os.path.join(rdir, f) for f in os.listdir(rdir) if f.lower().endswith(RAW_EXT))
            if raws:
                found.append((col, roll, raws))
    names = [r for _, r, _ in found]
    out = {}
    for col, roll, raws in found:
        out[roll if names.count(roll) == 1 else f"{col} {roll}"] = raws
    return out


def low_priority_prefix(nice):
    """Praefix, damit darktable-cli anderen Prozessen (z. B. einem Benchmark) nicht in die Quere kommt:
    CPU-Prioritaet per nice, Platten-Zugriffe per ionice (Klasse 'idle'), soweit die Programme vorhanden sind."""
    prefix = []
    if nice and shutil.which("nice"):
        prefix += ["nice", "-n", str(nice)]
    if nice and shutil.which("ionice"):
        prefix += ["ionice", "-c", "3"]
    return prefix


def convert_one(dt_cli, raw, dst_file, long_edge, quality, cfg_dir, tmp_dir, nice=19):
    """Ein RAW -> JPEG. Gibt (ok, Meldung) zurueck."""
    xmp = raw + ".xmp"
    xmp_arg = []
    tmp_xmp = None
    if os.path.exists(xmp):
        text = open(xmp, encoding="utf-8", errors="replace").read()
        if needs_neutralizing(text):
            tmp_xmp = os.path.join(tmp_dir, os.path.basename(raw) + ".xmp")
            with open(tmp_xmp, "w", encoding="utf-8") as fh:
                fh.write(neutralize_xmp(text))
            xmp_arg = [tmp_xmp]
    tmp_out = os.path.join(tmp_dir, os.path.splitext(os.path.basename(raw))[0] + ".jpg")
    if os.path.exists(tmp_out):
        os.remove(tmp_out)
    env = dict(os.environ, XDG_CONFIG_HOME=cfg_dir)
    cmd = low_priority_prefix(nice) + [dt_cli, raw] + xmp_arg + [tmp_out, "--width", str(long_edge), "--height", str(long_edge),
                                     "--out-ext", "jpg", "--core",
                                     "--conf", "write_sidecar_files=never",
                                     "--conf", f"plugins/imageio/format/jpeg/quality={quality}"]
    proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env)
    produced = tmp_out if os.path.exists(tmp_out) else None
    if produced is None:   # darktable-cli haengt manchmal einen Zaehler an
        base = os.path.splitext(tmp_out)[0]
        cands = sorted(f for f in os.listdir(tmp_dir) if f.startswith(os.path.basename(base)) and f.endswith(".jpg"))
        produced = os.path.join(tmp_dir, cands[0]) if cands else None
    if tmp_xmp and os.path.exists(tmp_xmp):
        os.remove(tmp_xmp)
    if not produced:
        return False, (proc.stderr or proc.stdout).strip()[-200:]
    os.makedirs(os.path.dirname(dst_file), exist_ok=True)
    shutil.move(produced, dst_file + ".part")
    os.replace(dst_file + ".part", dst_file)
    return True, ""


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--src", default=DEFAULT_SRC)
    ap.add_argument("--dst", default=DEFAULT_DST)
    ap.add_argument("--labels", default=DEFAULT_LABELS, help="Ausgabe der darktable-Crops (JSON)")
    ap.add_argument("--jobs", type=int, default=6, help="parallele darktable-cli-Prozesse (Default 6)")
    ap.add_argument("--long", type=int, default=2000, help="lange Kante in px (Default 2000, wie Film 27-35)")
    ap.add_argument("--quality", type=int, default=90, help="JPEG-Qualitaet (Default 90)")
    ap.add_argument("--nice", type=int, default=19,
                    help="CPU-Prioritaet von darktable-cli (nice 0-19, Default 19 = niedrigste; 0 = normal). "
                         "Dazu laeuft es mit ionice -c 3, damit ein Benchmark nebenbei nicht gestoert wird")
    ap.add_argument("--rolls", nargs="*", help="nur diese Rollen (Ordnernamen)")
    ap.add_argument("--limit", type=int, help="hoechstens N Bilder (zum Ausprobieren)")
    ap.add_argument("--dry-run", action="store_true", help="nur zaehlen, nichts schreiben")
    ap.add_argument("--no-labels", action="store_true", help="darktable_crops.json nicht schreiben")
    args = ap.parse_args()

    dt_cli = shutil.which("darktable-cli")
    if not dt_cli and not args.dry_run:
        sys.exit("FEHLER: darktable-cli nicht gefunden")
    rolls = find_rolls(args.src, COLLECTIONS)
    if args.rolls:
        rolls = {k: v for k, v in rolls.items() if k in args.rolls}
    jobs_list, labels, skipped, exists_n = [], {}, 0, 0
    stems = {}
    for roll, raws in rolls.items():
        for raw in raws:
            stem = os.path.splitext(os.path.basename(raw))[0]
            rel = f"{roll}/{stem}.jpg"
            if stem in stems and stems[stem] != roll:
                print(f"WARN: Dateiname {stem} kommt in {stems[stem]} und {roll} vor (kein Problem, Rolle ist Teil des Pfads)")
            stems[stem] = roll
            xmp = raw + ".xmp"
            if os.path.exists(xmp):
                text = open(xmp, encoding="utf-8", errors="replace").read()
                crop, rot, cmplx, n = read_darktable_crop(text)
                if crop:
                    labels[rel] = {"crop": [round(v, 5) for v in crop], "rotation": None if rot is None else round(rot, 3),
                                   "ashift_complex": cmplx, "crop_instances": n}
            dst_file = os.path.join(args.dst, roll, stem + ".jpg")
            if os.path.exists(dst_file):
                exists_n += 1
                continue
            jobs_list.append((raw, dst_file))
    total = len(jobs_list)
    if args.limit:
        jobs_list = jobs_list[:args.limit]
    print(f"{len(rolls)} Rollen, {sum(len(v) for v in rolls.values())} Rohdateien; vorhanden {exists_n}; "
          f"umzuwandeln {total}" + (f" (davon {len(jobs_list)} wegen --limit)" if args.limit else "")
          + f"; darktable-Crops in Sidecars: {len(labels)} (mit Drehung: {sum(1 for v in labels.values() if v['rotation'])})")
    if args.dry_run:
        return
    if not args.no_labels:
        old = {}
        if os.path.exists(args.labels):
            old = json.load(open(args.labels))
        old.update(labels)
        os.makedirs(os.path.dirname(args.labels), exist_ok=True)
        with open(args.labels, "w") as fh:
            json.dump(dict(sorted(old.items())), fh, indent=0)
        print(f"Crops geschrieben: {args.labels} ({len(old)} Eintraege)")

    work = tempfile.mkdtemp(prefix="convert_testphotos_")
    tls = threading.local()
    counter = {"n": 0}
    lock = threading.Lock()
    fails = []
    t0 = time.time()

    def worker(item):
        raw, dst_file = item
        if not hasattr(tls, "cfg"):
            with lock:
                counter["n"] += 1
                idx = counter["n"]
            tls.cfg = os.path.join(work, f"cfg{idx}")
            tls.tmp = os.path.join(work, f"tmp{idx}")
            os.makedirs(tls.tmp, exist_ok=True)
        return raw, convert_one(dt_cli, raw, dst_file, args.long, args.quality, tls.cfg, tls.tmp, args.nice)

    done = 0
    try:
        with ThreadPoolExecutor(max_workers=args.jobs) as pool:
            futs = [pool.submit(worker, it) for it in jobs_list]
            for f in as_completed(futs):
                raw, (ok, msg) = f.result()
                done += 1
                if not ok:
                    fails.append((raw, msg))
                    print(f"FEHLER {raw}: {msg}", flush=True)
                if done % 25 == 0 or done == len(jobs_list):
                    el = time.time() - t0
                    eta = el / done * (len(jobs_list) - done)
                    print(f"{done}/{len(jobs_list)}  {el / 60:.1f} min, Rest ~{eta / 60:.1f} min, Fehler {len(fails)}", flush=True)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    print(f"Fertig: {done - len(fails)} umgewandelt, {len(fails)} Fehler")
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
