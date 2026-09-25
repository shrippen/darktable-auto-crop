#!/usr/bin/env python3
"""Batch-Test: Alle Bilder in Testphotos durchlaufen.

Modi:
  (ohne Argument)   Alle Bilder vollstaendig neu analysieren
  --incremental     Nur neue/geaenderte Bilder analysieren, bestehende
                    Ergebnisse wiederverwenden. Nach Algorithmus-Aenderungen
                    wird automatisch vollstaendig neu analysiert.
  --list-missing    Nur anzeigen, was --incremental analysieren wuerde

Ergebnisse: <projekt>/review_data/results.json (+ meta.json)
"""
import subprocess, json, os, sys, time, argparse
from datetime import datetime

PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
BASE = os.path.join(PROJECT_DIR, 'Testphotos')
SCRIPT = os.path.join(PROJECT_DIR, 'kader.py')
DATA_DIR = os.path.join(PROJECT_DIR, 'review_data')
RESULTS_FILE = os.path.join(DATA_DIR, 'results.json')
META_FILE = os.path.join(DATA_DIR, 'meta.json')
FILM_SCRIPT = os.path.join(PROJECT_DIR, 'film_analysis.py')
FILM_META_FILE = os.path.join(DATA_DIR, 'film_metadata.json')
PASS1_FILE = os.path.join(DATA_DIR, 'pass1.json')
IMG_EXT = ('.jpg', '.jpeg', '.tif', '.tiff', '.png')


def find_images():
    images = []
    if not os.path.isdir(BASE):
        return images
    for film in sorted(os.listdir(BASE)):
        film_dir = os.path.join(BASE, film)
        if not os.path.isdir(film_dir):
            continue
        for f in sorted(os.listdir(film_dir)):
            if f.lower().endswith(IMG_EXT):
                images.append((film, os.path.join(film_dir, f)))
    return images


def load_existing():
    """Laedt bestehende Ergebnisse als {(film, filename): result}."""
    existing = {}
    if os.path.exists(RESULTS_FILE):
        try:
            with open(RESULTS_FILE) as f:
                for r in json.load(f):
                    existing[(r.get('film', ''), r.get('filename', ''))] = r
        except (json.JSONDecodeError, OSError) as e:
            print(f'WARNUNG: {RESULTS_FILE} unlesbar ({e}), starte leer.')
    return existing


def load_meta():
    if os.path.exists(META_FILE):
        try:
            with open(META_FILE) as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            pass
    return {}


def run_one(path, extra_args=None):
    cmd = [sys.executable, SCRIPT, path, '--format', '35mm',
           '--confidence-threshold', '0.7']
    if extra_args:
        cmd.extend(extra_args)
    out = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    if out.returncode in (0, 1):
        data = json.loads(out.stdout)
        return data, None
    return None, out.stderr[:300]


def run_pass1(path):
    """Pass 1: Alle Kandidaten ausgeben (MIT Stufe 2 Refinement).
    Ohne Aspect-Strafe, damit die Film-Analyse die echte Aspekt-Vielfalt sieht.
    """
    out = subprocess.run(
        [sys.executable, SCRIPT, path, '--dump-candidates',
         '--confidence-threshold', '0.1', '--no-aspect-penalty'],
        capture_output=True, text=True, timeout=60)
    if out.returncode in (0, 1):
        return json.loads(out.stdout), None
    return None, out.stderr[:300]


def run_measure_pass(path):
    """Pass A: Bild-Aspect messen (schnell, kein Scoring)."""
    out = subprocess.run(
        [sys.executable, SCRIPT, path, '--measure-aspect'],
        capture_output=True, text=True, timeout=60)
    if out.returncode != 0:
        return None, out.stderr[:200]
    return json.loads(out.stdout), None


def determine_film_aspects(measurements):
    """Bestimmt das Film-Aspect pro Ordner aus den Pass-A-Messungen.

    Strategie: Kontrast-gewichtetes Clustering. Messungen mit hohem
    Kontrast sind zuverlaessiger (klarere Bildgrenzen). Das groesste
    Cluster unter den Top-Kontrast-Messungen gewinnt.
    """
    import statistics
    from collections import defaultdict
    by_film = defaultdict(list)
    for m in measurements:
        meas = m.get('measurement', {})
        if meas.get('valid'):
            a = meas['aspect']
            norm = a if a >= 1 else 1 / a
            by_film[m['film']].append({
                'aspect': norm,
                'contrast': abs(meas.get('contrast', 0)),
            })

    MIN_VALIDE = 3
    CONSENSUS_TOL = 0.10  # +/-10% um die bestkontrasteste Messung
    result = {}
    for film in sorted(by_film):
        items = by_film[film]
        if len(items) >= MIN_VALIDE:
            # Beste Messung = hoechster Kontrast (klarste Bildgrenzen)
            items.sort(key=lambda x: -x['contrast'])
            best = items[0]['aspect']
            # Konsens: alle Messungen innerhalb +/-10% davon mitteln
            consensus = [x['aspect'] for x in items
                         if abs(x['aspect'] - best) / best <= CONSENSUS_TOL]
            med = statistics.median(consensus) if consensus else best
            result[film] = {
                'aspect_ratio': round(med, 4),
                'n_valid': len(items),
                'n_consensus': len(consensus),
                'n_total': len(measurements),
                'source': 'measured',
            }
        else:
            result[film] = {
                'aspect_ratio': 1.5,
                'n_valid': len(items),
                'n_total': len(measurements),
                'source': 'fallback',
            }
    return result


def _run_aspect_pass(all_images):
    """Pass A: Misst das Bild-Aspect fuer alle Bilder."""
    print('=' * 50)
    print('PASS A: Bild-Aspect messen (ohne Vorurteil)...')
    print('=' * 50)
    measurements = []
    start = time.time()
    for idx, (film, path) in enumerate(all_images):
        fname = os.path.basename(path)
        data, err = run_measure_pass(path)
        if data is not None:
            measurements.append(data)
        else:
            print(f'  FEHLER {film}/{fname}: {err[:80]}')
        if (idx + 1) % 50 == 0 or idx + 1 == len(all_images):
            elapsed = time.time() - start
            print(f'  Pass A: [{idx+1}/{len(all_images)}] {elapsed:.0f}s')
    return measurements


def _apply_film_consistency(results, film_meta=None):
    """Korrigiert Crops basierend auf der Konsistenz innerhalb eines Films.

    Wenn >=3 Bilder eines Films erkannt wurden und der Crop
    eines Bildes >15% vom Film-Median abweicht, wird der
    Film-Median-Crop uebernommen. Nur wenn der Median zum
    erwarteten Film-Aspect passt.
    """
    import statistics
    if film_meta is None:
        film_meta = {}
    by_film = {}
    for r in results:
        film = r.get('film', '')
        if film:
            by_film.setdefault(film, []).append(r)

    corrected = 0
    for film, film_results in by_film.items():
        if len(film_results) < 3:
            continue

        # Pruefe ob Median-Aspect zum Film passt
        aspects = [r['width'] / max(r['height'], 1) for r in film_results]
        norm_aspects = [a if a >= 1 else 1/a for a in aspects]
        median_aspect = statistics.median(norm_aspects)
        film_aspect = film_meta.get(film, {}).get('aspect_ratio')
        if film_aspect is not None:
            film_ta = film_aspect if film_aspect >= 1 else 1/film_aspect
            # Nur korrektur wenn der Median-Ausschnitt wirklich zum
            # Film-Aspect passt (nicht nur die Detections, sondern
            # ob der Median-Aspect abweicht)
            aspect_dev = abs(median_aspect - film_ta) / film_ta
            if aspect_dev > 0.15:
                continue
            # Zaehler wie viele Bilder zum Film-Aspect passen
            matching = sum(1 for a in norm_aspects
                          if abs(a - film_ta) / film_ta < 0.20)
            if matching < 3:
                continue

        # Relative Crop-Geometrie berechnen (0..1)
        rel_data = []
        for r in film_results:
            iw = r.get('_img_w', 0)
            ih = r.get('_img_h', 0)
            if iw <= 0 or ih <= 0:
                iw = r.get('x', 0) + r.get('width', 0) + 100
                ih = r.get('y', 0) + r.get('height', 0) + 100
            if iw > 0 and ih > 0:
                rel_data.append({
                    'result': r,
                    'rel_x': r['x'] / iw,
                    'rel_y': r['y'] / ih,
                    'rel_w': r['width'] / iw,
                    'rel_h': r['height'] / ih,
                })

        if len(rel_data) < 3:
            continue

        # Median der relativen Geometrie (alle Bilder gemeinsam)
        med_w = statistics.median(d['rel_w'] for d in rel_data)
        med_h = statistics.median(d['rel_h'] for d in rel_data)
        med_x = statistics.median(d['rel_x'] for d in rel_data)
        med_y = statistics.median(d['rel_y'] for d in rel_data)

        for d in rel_data:
            r = d['result']
            dw = abs(d['rel_w'] - med_w) / max(med_w, 0.01)
            dh = abs(d['rel_h'] - med_h) / max(med_h, 0.01)
            dx = abs(d['rel_x'] - med_x) / max(med_x, 0.01)
            dy = abs(d['rel_y'] - med_y) / max(med_y, 0.01)
            max_dev = max(dw, dh, dx, dy)

            if max_dev > 0.15:
                iw = r.get('_img_w', r.get('width', 1000))
                ih = r.get('_img_h', r.get('height', 1000))
                r['x'] = int(med_x * iw)
                r['y'] = int(med_y * ih)
                r['width'] = int(med_w * iw)
                r['height'] = int(med_h * ih)
                r['confidence'] = round(
                    max(0.3, r['confidence'] * 0.9), 3)
                r.setdefault('reasons', []).append(
                    f'Film-Konsistenz: '
                    f'Abweichung {max_dev:.0%}')
                corrected += 1

    if corrected > 0:
        print(f'  Film-Konsistenz: {corrected} Bilder korrigiert')


def _run_two_pass(all_images):
    """Two-Pass: Pass 1 sammelt Kandidaten, Film-Analyse bestimmt
    Verhaeltnis pro Film, Pass 2 analysiert mit echtem Ziel."""
    start = time.time()

    # ─── Pass A: Bild-Aspect messen (ohne Vorurteil) ───
    measurements = _run_aspect_pass(all_images)

    with open(PASS1_FILE, 'w') as f:
        json.dump(measurements, f, indent=2, ensure_ascii=False)
    print(f'Pass A: {len(measurements)} Bilder gemessen -> {PASS1_FILE}')

    # ─── Film-Aspect bestimmen ───
    print()
    print('=' * 50)
    print('FILM-ASPECT: Seitenverhaeltnis pro Film bestimmen...')
    print('=' * 50)
    film_meta = determine_film_aspects(measurements)
    for film in sorted(film_meta):
        m = film_meta[film]
        src = m.get('source', '?')
        print(f"  {film}: aspect={m['aspect_ratio']} ({src}, "
              f"{m.get('n_valid', 0)}/{m.get('n_total', 0)} valide)")

    os.makedirs(DATA_DIR, exist_ok=True)
    with open(FILM_META_FILE, 'w') as f:
        json.dump(film_meta, f, indent=2, ensure_ascii=False)

    # ─── Pass 2: Mit filmweiten Parametern analysieren ───
    print()
    print('=' * 50)
    print('PASS 2: Analyse mit filmweiten Parametern...')
    print('=' * 50)
    pass2_start = time.time()
    results = []
    errors = []
    for idx, (film, path) in enumerate(all_images):
        fname = os.path.basename(path)
        mtime = os.path.getmtime(path)
        meta = film_meta.get(film, {})
        extra_args = []
        if meta.get('aspect_ratio') is not None:
            extra_args.extend(['--aspect-ratio', str(meta['aspect_ratio'])])
        if meta.get('border_level') is not None:
            extra_args.extend(['--film-border-level',
                               str(meta['border_level'])])
        try:
            data, err = run_one(path, extra_args=extra_args)
            if data is not None:
                data['film'] = film
                data['filename'] = fname
                data['full_path'] = path
                data['img_mtime'] = mtime
                # Film-Metadaten hinzufuegen
                data['film_aspect'] = meta.get('aspect_ratio')
                data['film_image_size'] = meta.get('image_size')
                results.append(data)
            else:
                errors.append({'film': film, 'filename': fname,
                               'error': err, 'full_path': path,
                               'img_mtime': mtime})
        except Exception as e:
            errors.append({'film': film, 'filename': fname,
                           'error': str(e)[:300], 'full_path': path,
                           'img_mtime': mtime})
        if (idx + 1) % 25 == 0 or idx + 1 == len(all_images):
            elapsed = time.time() - pass2_start
            rate = elapsed / (idx + 1)
            eta = rate * (len(all_images) - idx - 1)
            print(f'  Pass 2: [{idx+1}/{len(all_images)}] '
                  f'{elapsed:.0f}s, ~{eta:.0f}s verbleibend')

    elapsed = time.time() - start

    # Film-Konsistenz: Ausreisser gegen Film-Median korrigieren
    _apply_film_consistency(results, film_meta)

    results.sort(key=lambda r: (r.get('film', ''), r.get('filename', '')))

    total = len(results)
    ok = sum(1 for r in results if not r.get('needs_review'))
    review = sum(1 for r in results if r.get('needs_review'))
    failed = len(errors)

    print(f'\n=== ERGEBNISSE (Two-Pass) ===')
    print(f'Gesamt:      {total + failed}')
    print(f'OK:          {ok}')
    print(f'Review:      {review}')
    print(f'Fehler:      {failed}')
    print(f'Zeit:        {elapsed:.1f}s')

    confs = [r['confidence'] for r in results]
    print(f'\nKonfidenz-Verteilung:')
    for lo, hi, label in [(0.9, 1.01, '0.9-1.0'), (0.7, 0.9, '0.7-0.9'),
                           (0.5, 0.7, '0.5-0.7'), (0.0, 0.5, '<0.5')]:
        c = sum(1 for v in confs if lo <= v < hi)
        bar = '#' * c
        print(f'  {label:>8}: {c:3d} {bar}')

    if review > 0:
        print(f'\n=== REVIEW BENOETIGT ({review}) ===')
        for r in sorted(results, key=lambda x: x['confidence']):
            if r.get('needs_review'):
                a = r['width'] / r['height'] if r['height'] > 0 else 0
                fa = r.get('film_aspect', '?')
                print(f"  {r['film']}/{r['filename']}: conf={r['confidence']:.3f} "
                      f"ratio={a:.2f} film_ratio={fa} meth={r['method']}")

    if errors:
        print(f'\n=== FEHLER ({failed}) ===')
        for e in errors:
            print(f"  {e['film']}/{e['filename']}: {e['error'][:120]}")

    with open(RESULTS_FILE, 'w') as f:
        json.dump(results + errors, f, indent=2, ensure_ascii=False)
    with open(META_FILE, 'w') as f:
        json.dump({'generated_at': datetime.now().isoformat(),
                   'script_mtime': os.path.getmtime(SCRIPT),
                   'images_total': len(all_images),
                   'mode': 'two-pass',
                   'film_metadata': film_meta}, f, indent=2,
                  ensure_ascii=False)
    print(f'\nErgebnisse: {RESULTS_FILE}')


def main():
    ap = argparse.ArgumentParser(description='Batch-Analyse Testphotos')
    ap.add_argument('--incremental', action='store_true',
                    help='Nur neue/geaenderte Bilder analysieren')
    ap.add_argument('--list-missing', action='store_true',
                    help='Nur auflisten, was --incremental analysieren wuerde')
    ap.add_argument('--two-pass', action='store_true',
                    help='Two-Pass: Kandidaten sammeln, Film-Analyse, '
                         'dann mit filmweitem Seitenverhaeltnis analysieren')
    args = ap.parse_args()

    images = find_images()
    if not images:
        print(f'KEINE BILDER in {BASE} gefunden.')
        sys.exit(1)

    script_mtime = os.path.getmtime(SCRIPT)
    meta = load_meta()
    prev_gen_mtime = meta.get('script_mtime', 0)
    algo_changed = args.incremental and script_mtime > prev_gen_mtime
    existing = {} if algo_changed else load_existing()

    to_run, kept = [], []
    for film, path in images:
        key = (film, os.path.basename(path))
        mtime = os.path.getmtime(path)
        old = None
        if args.incremental or args.list_missing:
            old = existing.get(key)
        if (old is not None and old.get('img_mtime') == mtime
                and 'confidence' in old):
            old = dict(old)
            old['full_path'] = path
            kept.append(old)
        else:
            to_run.append((film, path, mtime, key))

    if algo_changed:
        print('Algorithmus wurde geaendert -> vollstaendige Neuanalyse.')

    if args.list_missing:
        print(f'Inkrementell-Plan (was "--incremental" analysieren wuerde):')
        print(f'Bilder gesamt:        {len(images)}')
        print(f'  wiederverwendet:    {len(kept)}')
        print(f'  zu analysieren:     {len(to_run)}')
        if algo_changed:
            print('  (Algorithmus geaendert: alle Bilder)')
        for film, path, _, _ in to_run[:20]:
            print(f'    {film}/{os.path.basename(path)}')
        if len(to_run) > 20:
            print(f'    ... und {len(to_run) - 20} weitere')
        return

    if args.incremental and not to_run:
        print(f'Alle {len(kept)} Bilder aktuell, nichts zu tun.')
        return

    # ─── Two-Pass-Modus ─────────────────────────────────────────
    if args.two_pass:
        os.makedirs(DATA_DIR, exist_ok=True)
        all_images = [(f, p) for f, p in images]  # alle Bilder
        _run_two_pass(all_images)
        return

    print(f'Gesamt: {len(images)} Bilder in '
          f'{len(set(i[0] for i in images))} Filmrollen '
          f'({len(to_run)} neu, {len(kept)} wiederverwendet)')

    results = list(kept)
    errors = []
    start = time.time()
    for idx, (film, path, mtime, key) in enumerate(to_run):
        fname = os.path.basename(path)
        try:
            data, err = run_one(path)
            if data is not None:
                data['film'] = film
                data['filename'] = fname
                data['full_path'] = path
                data['img_mtime'] = mtime
                results.append(data)
            else:
                errors.append({'film': film, 'filename': fname,
                               'error': err, 'full_path': path,
                               'img_mtime': mtime})
        except Exception as e:
            errors.append({'film': film, 'filename': fname,
                           'error': str(e)[:300], 'full_path': path,
                           'img_mtime': mtime})
        if (idx + 1) % 25 == 0 or idx + 1 == len(to_run):
            elapsed_so_far = time.time() - start
            rate = elapsed_so_far / (idx + 1)
            eta = rate * (len(to_run) - idx - 1)
            print(f'  [{idx+1}/{len(to_run)}] {elapsed_so_far:.0f}s vergangen, '
                  f'~{eta:.0f}s verbleibend')

    elapsed = time.time() - start


    total = len(results)
    ok = sum(1 for r in results if not r.get('needs_review'))
    review = sum(1 for r in results if r.get('needs_review'))
    failed = len(errors)

    print(f'\n=== ERGEBNISSE ===')
    print(f'Gesamt:      {total + failed}')
    print(f'OK:          {ok}')
    print(f'Review:      {review}')
    print(f'Fehler:      {failed}')
    print(f'Zeit:        {elapsed:.1f}s ({elapsed/max(len(to_run),1):.2f}s/Bild)')

    confs = [r['confidence'] for r in results]
    print(f'\nKonfidenz-Verteilung:')
    for lo, hi, label in [(0.9, 1.01, '0.9-1.0'), (0.7, 0.9, '0.7-0.9'),
                           (0.5, 0.7, '0.5-0.7'), (0.0, 0.5, '<0.5')]:
        c = sum(1 for v in confs if lo <= v < hi)
        bar = '#' * c
        print(f'  {label:>8}: {c:3d} {bar}')

    if review > 0:
        print(f'\n=== REVIEW BENÖTIGT ({review}) ===')
        for r in sorted(results, key=lambda x: x['confidence']):
            if r.get('needs_review'):
                a = r['width'] / r['height'] if r['height'] > 0 else 0
                print(f"  {r['film']}/{r['filename']}: conf={r['confidence']:.3f} "
                      f"ratio={a:.2f} meth={r['method']}")

    if errors:
        print(f'\n=== FEHLER ({failed}) ===')
        for e in errors:
            print(f"  {e['film']}/{e['filename']}: {e['error'][:120]}")

    os.makedirs(DATA_DIR, exist_ok=True)
    with open(RESULTS_FILE, 'w') as f:
        json.dump(results + errors, f, indent=2, ensure_ascii=False)
    with open(META_FILE, 'w') as f:
        json.dump({'generated_at': datetime.now().isoformat(),
                   'script_mtime': script_mtime,
                   'images_total': len(images),
                   'mode': 'incremental' if args.incremental else 'full'},
                  f, indent=2, ensure_ascii=False)
    print(f'\nErgebnisse: {RESULTS_FILE}')


if __name__ == '__main__':
    main()
