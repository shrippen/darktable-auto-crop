#!/usr/bin/env python3
"""Auswertung des Feedbacks aus der Companion-UI (Roadmap Phase 5).

Die Web-UI schreibt pro Sitzung ``state.json`` (erkannter Crop, manuelle Korrektur,
Gruppe) und ``feedback.jsonl`` (jede Korrektur mit erkanntem Crop, Konfidenz und den
Einzelfaktoren). Dieses Skript wertet mehrere Sitzungen gemeinsam aus:

  * Korrekturrate je automatischer Gruppe (gruen/gelb/rot): wie oft musste der Nutzer den
    Crop von Hand korrigieren - und wie stark (Toleranz wie tools/eval.py)?
  * "Falsches Gruen": als gruen eingestufte Bilder, deren Korrektur ueber der Toleranz lag
  * welcher Konfidenz-Faktor bei den Fehlern schwach war (size_agree, edge_score, ...)
  * Gruppenwechsel des Nutzers (z. B. gruen -> rot)
  * optional ein Export der Korrekturen als Ground Truth im Format von
    review_data/reviews.json (mit Zusatzfeldern), um sie fuer Kalibrierung zu nutzen

Annahme, die man kennen muss: Bilder ohne Korrektur zaehlen als "vom Nutzer akzeptiert".
Das ist nur so belastbar, wie der Nutzer sie tatsaechlich angesehen hat. Die Ausgabe nennt
deshalb Korrekturraten, keine "Precision".

Sitzungen loescht die UI nach 14 Tagen; diesen Bericht (oder ``--export-gt``) vorher laufen lassen.

Beispiele:
  tools/feedback_report.py                       # alle Sitzungen in ~/.cache/auto-crop-negative
  tools/feedback_report.py --session DIR ...     # bestimmte Sitzungen
  tools/feedback_report.py --export-gt review_data/feedback_gt.json
"""
import argparse
import json
import os
import sys
from collections import Counter, defaultdict

PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_DIR)

from companion import session as sess   # noqa: E402

TOL_PX = 60                      # wie tools/eval.py, gilt dort fuer Bilder mit ~2000 px langer Kante
REF_EDGE = 2000                  # Referenzgroesse der Toleranz; die Exporte sind viel groesser


def tolerance(size):
    """Toleranz in Export-Pixeln: 60 px bei 2000 px langer Kante, proportional skaliert.
    (Ohne Skalierung waere die Toleranz auf 5520x8280-Exporten viermal zu streng.)"""
    return TOL_PX * max(size) / float(REF_EDGE) if size else TOL_PX
PHASE_RANK = {"applied": 3, "locked": 2, "reviewing": 1}
FACTORS = ["size_agree", "edge_score", "film_trust", "exposure_factor", "roll_factor"]


def auto_group(state, det):
    """Automatische Gruppe aus Konfidenz und den Schwellen der Sitzung."""
    if not det:
        return "red"
    s = state.get("settings", {})
    conf = det.get("confidence", 0.0)
    if conf >= s.get("t_green", 0.5):
        return "green"
    if conf >= s.get("t_yellow", 0.3):
        return "yellow"
    return "red"


def load_sessions(dirs):
    out = []
    for d in dirs:
        state = sess.read_json(os.path.join(d, "state.json"))
        if not state or "images" not in state:
            continue
        fb = []
        p = os.path.join(d, "feedback.jsonl")
        if os.path.exists(p):
            with open(p, encoding="utf-8") as f:
                for line in f:
                    try:
                        fb.append(json.loads(line))
                    except ValueError:
                        pass
        out.append({"dir": d, "name": os.path.basename(d.rstrip("/")), "state": state,
                    "feedback": fb})
    return out


def collect(sessions):
    """Pro Bild (Pfad) den aussagekraeftigsten Stand: bevorzugt abgeschlossene Sitzungen,
    bei Gleichstand die neueste. Liefert Zeilen mit Abweichungen in Export-Pixeln."""
    best = {}
    for s in sessions:
        rank = (PHASE_RANK.get(s["state"].get("phase"), 0), s["name"])
        for img in s["state"]["images"].values():
            key = img.get("path") or f"{img.get('film')}/{img.get('filename')}"
            if key not in best or rank > best[key][0]:
                best[key] = (rank, s, img)
    rows = []
    for key, (_, s, img) in sorted(best.items()):
        det = img.get("detected")
        size = img.get("export_size")
        row = {"path": key, "film": img.get("film"), "file": img.get("filename"),
               "session": s["name"], "has_detection": bool(det), "size": size,
               "export_path": (os.path.join(s["dir"], img["export"])
                               if img.get("export") and not os.path.isabs(img["export"])
                               else img.get("export")),
               "will_apply": _will_apply(s["state"], img),
               "phase": s["state"].get("phase"),
               "auto": auto_group(s["state"], det),
               "conf": (det or {}).get("confidence"),
               "parts": (det or {}).get("conf_parts"),
               "method": (det or {}).get("method"),
               "final": img.get("group_override") or auto_group(s["state"], det),
               "moved": bool(img.get("group_override")),
               "corrected": bool(img.get("manual")), "skip": img.get("decision") == "skip",
               "error": img.get("status") == "error"}
        if row["corrected"] and det and size:
            d = sess.crop_to_pixels(det["crop"], *size)
            m = sess.crop_to_pixels(img["manual"]["crop"], *size)
            row.update({"dx": d["x"] - m["x"], "dy": d["y"] - m["y"],
                        "dw": d["width"] - m["width"], "dh": d["height"] - m["height"],
                        "manual_px": m})
            tol = row["tol"] = tolerance(size)
            row["size_hit"] = abs(row["dw"]) < tol and abs(row["dh"]) < tol   # wie eval.py
            row["strict_hit"] = row["size_hit"] and abs(row["dx"]) < tol and abs(row["dy"]) < tol
            row["symptom"] = symptom(row)
        rows.append(row)
    return rows


def _will_apply(state, img):
    """Wurde/wird der Crop uebernommen? (wie Session.will_apply, ohne Session-Objekt)"""
    if img.get("decision") == "skip" or img.get("status") == "error":
        return False
    if not img.get("detected") and not img.get("manual"):
        return False
    grp = img.get("group_override") or auto_group(state, img.get("detected"))
    return not (grp == "red" and not img.get("manual") and img.get("decision") != "accept")


def symptom(row):
    """Roadmap Phase 0: welches Fehlermuster? Automatisch unterscheidbar sind Groesse und
    Position; "Nachbarframe" oder "Filmhalterkante" erkennt man nur am Bild selbst."""
    if row.get("strict_hit"):
        return "ok"
    size_bad = not row["size_hit"]
    pos_bad = abs(row["dx"]) >= row["tol"] or abs(row["dy"]) >= row["tol"]
    if size_bad and pos_bad:
        return "Groesse+Position"
    return "Groesse" if size_bad else "Position bei richtiger Groesse"


def summarize(rows):
    out = {"total": len(rows), "groups": {}, "false_green": [], "moves": Counter(),
           "weak_factor": Counter(), "symptoms": Counter()}
    for g in ("green", "yellow", "red"):
        rs = [r for r in rows if r["auto"] == g and r["has_detection"]]
        cor = [r for r in rs if r["corrected"]]
        bad = [r for r in cor if not r.get("strict_hit", True)]
        out["groups"][g] = {"n": len(rs), "corrected": len(cor), "bad": len(bad),
                            "median_conf": _median([r["conf"] for r in rs if r["conf"] is not None])}
    for r in rows:
        if r["auto"] != r["final"]:
            out["moves"][f"{r['auto']}->{r['final']}"] += 1
        if r["auto"] == "green" and r["corrected"] and not r.get("strict_hit", True):
            out["false_green"].append(r)
            out["symptoms"][r["symptom"]] += 1
        if r["corrected"] and not r.get("strict_hit", True) and r.get("parts"):
            vals = {k: r["parts"][k] for k in FACTORS if isinstance(r["parts"].get(k), (int, float))}
            if vals:
                out["weak_factor"][min(vals, key=vals.get)] += 1
    return out


def _median(xs):
    xs = sorted(xs)
    if not xs:
        return None
    n = len(xs)
    return xs[n // 2] if n % 2 else (xs[n // 2 - 1] + xs[n // 2]) / 2


def print_report(rows, summary, sessions):
    print(f"Sitzungen: {len(sessions)}  |  Bilder (dedupliziert): {summary['total']}")
    print(f"Toleranz: {TOL_PX} px bei {REF_EDGE} px langer Kante, auf die Exportgroesse skaliert "
          f"(Breite/Hoehe wie tools/eval.py; 'streng' zusaetzlich x/y)\n")
    print(f"{'Gruppe':8} {'Bilder':>6} {'korrigiert':>10} {'davon zu weit':>14} {'Korrekturrate':>14} {'Median-Konf':>12}")
    for g, v in summary["groups"].items():
        rate = f"{100 * v['corrected'] / v['n']:.0f} %" if v["n"] else "-"
        mc = f"{v['median_conf']:.2f}" if v["median_conf"] is not None else "-"
        print(f"{g:8} {v['n']:>6} {v['corrected']:>10} {v['bad']:>14} {rate:>14} {mc:>12}")
    fg = summary["false_green"]
    print(f"\nFalsches Gruen (gruen erkannt, Korrektur ueber Toleranz): {len(fg)}")
    for r in fg:
        k = REF_EDGE / float(max(r["size"]))
        print(f"  {r['film']}/{r['file']}  conf={r['conf']:.2f}  dx={r['dx']:+d} dy={r['dy']:+d} "
              f"dw={r['dw']:+d} dh={r['dh']:+d} (auf {REF_EDGE} px: {r['dx']*k:+.0f} {r['dy']*k:+.0f} "
              f"{r['dw']*k:+.0f} {r['dh']*k:+.0f})  {r['symptom']}  methode={r['method']}")
    if summary["symptoms"]:
        print("\nSymptome beim falschen Gruen: " + ", ".join(
            f"{k} {n}x" for k, n in summary["symptoms"].most_common()))
    if summary["weak_factor"]:
        print("\nSchwaechster Konfidenz-Faktor bei den zu weit korrigierten Bildern:")
        for k, n in summary["weak_factor"].most_common():
            print(f"  {k:16} {n}x")
    elif any(r["corrected"] and not r.get("strict_hit", True) for r in rows):
        print("\n(Keine Konfidenz-Faktoren gespeichert: Sitzungen stammen aus einem aelteren Lauf.)")
    if summary["moves"]:
        print("\nGruppenwechsel durch den Nutzer (automatisch -> Endstand):")
        for k, n in summary["moves"].most_common():
            print(f"  {k:14} {n}x")
    corrected = [r for r in rows if r["corrected"] and "dw" in r]
    if corrected:
        def med(key):   # auf REF_EDGE normiert, damit Rollen mit verschiedenen Exportgroessen vergleichbar sind
            return _median([abs(r[key]) * REF_EDGE / float(max(r["size"])) for r in corrected])
        print(f"\nKorrekturen: {len(corrected)}  |  mediane Abweichung (auf {REF_EDGE} px) "
              f"|dx|={med('dx'):.0f} |dy|={med('dy'):.0f} |dw|={med('dw'):.0f} |dh|={med('dh'):.0f} px")


def export_gt(rows, path):
    """Korrigierte Crops als Ground Truth im Format von review_data/reviews.json.
    Pixel beziehen sich auf den darktable-Export (Zusatzfelder 'export_size', 'path')."""
    gt = {}
    for r in rows:
        if not r["corrected"] or "manual_px" not in r:
            continue
        gt[f"{r['film']}/{r['file']}"] = {
            "manual_crop": r["manual_px"], "export_size": r["size"], "path": r["path"],
            "source": f"companion:{r['session']}"}
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(gt, f, indent=2, ensure_ascii=False)
    os.replace(tmp, path)
    return len(gt)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", default=sess.DEFAULT_ROOT, help="Sitzungsordner")
    ap.add_argument("--session", nargs="+", help="nur diese Sitzungsordner auswerten")
    ap.add_argument("--json", help="Kennzahlen als JSON schreiben")
    ap.add_argument("--export-gt", metavar="DATEI", help="Korrekturen als Ground Truth exportieren")
    args = ap.parse_args()
    dirs = args.session or sess.list_sessions(args.root)
    sessions = load_sessions(dirs)
    if not sessions:
        print("keine Sitzungen gefunden", file=sys.stderr)
        return 1
    rows = collect(sessions)
    summary = summarize(rows)
    print_report(rows, summary, sessions)
    if args.json:
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump({"summary": {**summary, "moves": dict(summary["moves"]),
                                   "weak_factor": dict(summary["weak_factor"]),
                                   "symptoms": dict(summary["symptoms"]),
                                   "false_green": [{k: v for k, v in r.items() if k != "parts"}
                                                   for r in summary["false_green"]]},
                       "rows": rows}, f, indent=1, ensure_ascii=False, default=str)
    if args.export_gt:
        n = export_gt(rows, args.export_gt)
        print(f"\n{n} korrigierte Crops nach {args.export_gt} exportiert")
    return 0


if __name__ == "__main__":
    sys.exit(main())
