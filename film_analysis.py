#!/usr/bin/env python3
"""Film-Ebenen-Analyse: Bestimmt Seitenverhältnis, Bildgroesse und
Filmrand-Helligkeit pro Film anhand der Pass-1 Kandidaten.

Eingabe: pass1.json (Liste von {film, filename, candidates})
Ausgabe: film_metadata.json {film: {aspect_ratio, image_size, border_level, ...}}
"""
import json
import sys
import os
import statistics
from collections import defaultdict


def normalize_aspect(aspect: float) -> float:
    """Normalisiere Aspekt auf Landscape (a < 1 -> 1/a).
    Portrait und Landscape desselben Formats sind dann gleich."""
    return aspect if aspect >= 1.0 else 1.0 / aspect


def cluster_mode(values: list[float], bin_width: float = 0.05) -> tuple:
    """Finde den Mode durch Clustering.
    Gibt (median_des_groessten_clusters, cluster_groesse) zurueck."""
    if not values:
        return None, 0
    sorted_vals = sorted(values)
    best_cluster = [sorted_vals[0]]
    current_cluster = [sorted_vals[0]]
    for i in range(1, len(sorted_vals)):
        if sorted_vals[i] - current_cluster[-1] <= bin_width:
            current_cluster.append(sorted_vals[i])
        else:
            if len(current_cluster) > len(best_cluster):
                best_cluster = current_cluster
            current_cluster = [sorted_vals[i]]
    if len(current_cluster) > len(best_cluster):
        best_cluster = current_cluster
    return statistics.median(best_cluster), len(best_cluster)


def find_all_clusters(values: list[float], bin_width: float = 0.05,
                      min_size: int = 5) -> list[tuple[float, int]]:
    """Findet ALLE Cluster ueber min_size.
    Gibt [(median, groesse), ...] zurueck, sortiert nach Groesse."""
    if not values:
        return []
    sorted_vals = sorted(values)
    clusters = []
    current = [sorted_vals[0]]
    for i in range(1, len(sorted_vals)):
        if sorted_vals[i] - current[-1] <= bin_width:
            current.append(sorted_vals[i])
        else:
            if len(current) >= min_size:
                clusters.append((statistics.median(current), len(current)))
            current = [sorted_vals[i]]
    if len(current) >= min_size:
        clusters.append((statistics.median(current), len(current)))
    clusters.sort(key=lambda c: c[1], reverse=True)
    return clusters


def analyze_film(film_name: str,
                 images: dict[str, dict]) -> dict:
    """Analysiere alle Kandidaten eines Films.

    images: {filename: {"candidates": [...], "best_refined": {...}}}
    """
    # --- 1. Aspekte aus best_refined (post Stage 2) ---
    raw_aspects = []

    for fname, data in images.items():
        refined = data.get("best_refined", {})
        rw, rh = refined.get("width", 0), refined.get("height", 0)
        if rw > 0 and rh > 0:
            raw_aspects.append(normalize_aspect(rw / rh))
        for c in data.get("candidates", []):
            cw, ch = c.get("width", 0), c.get("height", 0)
            if cw > 0 and ch > 0:
                raw_aspects.append(normalize_aspect(cw / ch))

    # --- 2. Bildgroesse im Filmstreifen (aus best_refined) ---
    norm_sizes = []  # [(w, h), ...] wo w >= h
    for fname, data in images.items():
        refined = data.get('best_refined', {})
        rw, rh = refined.get('width', 0), refined.get('height', 0)
        if rw > 0 and rh > 0:
            if rw < rh:
                norm_sizes.append((rh, rw))
            else:
                norm_sizes.append((rw, rh))

    # --- 3. Cluster-Analyse fuer Seitenverhaeltnis ---
    if not raw_aspects:
        return {
            'aspect_ratio': None,
            'image_size': {'width': None, 'height': None},
            'n_images': len(images),
            'n_candidates': 0,
            'source': 'auto',
        }

    # Verschiedene Cluster-Breiten ausprobieren
    best_aspect = None
    best_cluster_size = 0
    for bw in [0.03, 0.05, 0.10, 0.15]:
        mode, cs = cluster_mode(raw_aspects, bin_width=bw)
        if mode is not None and cs > best_cluster_size:
            best_aspect = mode
            best_cluster_size = cs

    # Fallback: Median aller Aspekte
    if best_aspect is None and raw_aspects:
        best_aspect = statistics.median(raw_aspects)
        best_cluster_size = len(raw_aspects)

    # --- 4. Bildgroesse: Median der best_refined Kandidaten ---
    # Nur Eintraege die nahe am Cluster-Mode liegen (innerhalb 15%)
    size_candidates = []
    if best_aspect is not None:
        for fname, data in images.items():
            refined = data.get("best_refined", {})
            rw, rh = refined.get("width", 0), refined.get("height", 0)
            if rw > 0 and rh > 0:
                norm = normalize_aspect(rw / rh)
                if abs(norm - best_aspect) / max(best_aspect, 0.1) < 0.15:
                    size_candidates.append((rw, rh))
    if not size_candidates:
        size_candidates = norm_sizes

    if size_candidates:
        median_w = int(statistics.median([s[0] for s in size_candidates]))
        median_h = int(statistics.median([s[1] for s in size_candidates]))
    else:
        median_w, median_h = None, None

    # --- 5. Konfidenz ---
    coverage = best_cluster_size / max(len(raw_aspects), 1)

    return {
        'aspect_ratio': round(best_aspect, 4) if best_aspect else None,
        'image_size': {
            'width': median_w,
            'height': median_h,
        },
        'n_images': len(images),
        'n_candidates': len(raw_aspects),
        'cluster_size': best_cluster_size,
        'cluster_coverage': round(coverage, 2),
        'source': 'auto',
    }


def main():
    """Liest pass1.json (via Argument oder stdin) und schreibt film_metadata.json."""
    if len(sys.argv) > 1:
        pass1_file = sys.argv[1]
        output_file = sys.argv[2] if len(sys.argv) > 2 else None
    else:
        pass1_file = None
        output_file = None

    if pass1_file:
        with open(pass1_file) as f:
            pass1_data = json.load(f)
    else:
        pass1_data = json.load(sys.stdin)

    # Gruppiere nach Film
    by_film = defaultdict(dict)
    for entry in pass1_data:
        film = entry.get('film', '?')
        fname = entry.get('filename', '?')
        if entry.get('candidates') or entry.get('best_refined'):
            by_film[film][fname] = {
                "candidates": entry.get('candidates', []),
                "best_refined": entry.get('best_refined', {}),
            }

    result = {}
    for film in sorted(by_film):
        result[film] = analyze_film(film, by_film[film])
        r = result[film]
        print(f"  {film}: aspect={r['aspect_ratio']} "
              f"size={r['image_size']['width']}x{r['image_size']['height']} "
              f"(n={r['n_images']}, cluster={r['cluster_size']}/{r['n_candidates']})")

    if output_file:
        os.makedirs(os.path.dirname(output_file) or '.', exist_ok=True)
        with open(output_file, 'w') as f:
            json.dump(result, f, indent=2, ensure_ascii=False)
        print(f"\nFilm-Metadaten: {output_file}")
    else:
        print()
        print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == '__main__':
    main()
