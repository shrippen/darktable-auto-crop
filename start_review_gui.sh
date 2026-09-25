#!/usr/bin/env bash
# Testlauf fuer den Algorithmus: analysiert die Bilder in Testphotos/ mit DERSELBEN Pipeline wie
# spaeter in darktable (kader.compute_batch) und zeigt sie in der Web-UI.
#
# Dort siehst du je Bild den erkannten Crop, die Konfidenz samt Faktoren und - wo eine Referenz
# existiert - die Abweichung davon ("Referenz-Test"). Du kannst Crops korrigieren oder mit
# "Akzeptieren" (Taste A) als geprueft bestaetigen. Mit "Fertig" landet alles in
# review_data/reviews.json und dient als Ground Truth fuer tools/eval.py und tools/calibrate.py.
#
# Nutzung:
#   ./start_review_gui.sh                 # neue Analyse aller Rollen, dann Web-UI
#   ./start_review_gui.sh --films 33 34   # nur diese Rollen (z. B. noch ungelabelte)
#   ./start_review_gui.sh --resume        # letzte Ordner-Sitzung fortsetzen (keine Neuanalyse)
#   ./start_review_gui.sh --tk            # altes Tk-GUI (veraltet, andere Pipeline)
#   ./start_review_gui.sh -h
#
# Hinweise: review_data/reviews.json ist in git; "Fertig" ergaenzt/ueberschreibt Eintraege, mit
# `git diff review_data/reviews.json` siehst du, was sich geaendert hat. Der Server beendet sich
# nach 4 Stunden ohne Aktivitaet (die Sitzung bleibt, --resume setzt sie fort).
set -euo pipefail
cd "$(dirname "$0")"

PY=".venv/bin/python"
FILMS=()
RESUME=0
while (( $# )); do
  case "$1" in
    --films)   shift
               while (( $# )) && [[ "$1" != --* ]]; do FILMS+=("$1"); shift; done ;;
    --resume)  RESUME=1; shift ;;
    --tk)      shift; exec ./start_review_gui_tk.sh "$@" ;;
    -h|--help) sed -n '2,/^set -euo/p' "$0" | sed '$d' | sed 's/^# \{0,1\}//'; exit 0 ;;
    *)         echo "Unbekannte Option: $1 (siehe -h)" >&2; exit 2 ;;
  esac
done

if [[ ! -x "$PY" ]]; then
  echo "FEHLER: $PY fehlt. Bitte die Projekt-Venv anlegen (python -m venv .venv; .venv/bin/pip install opencv-python-headless numpy pillow)." >&2
  exit 1
fi

CMD=("$PY" -m companion serve --open --idle-minutes 240)
if (( RESUME )); then
  CMD+=(--latest-folder)
else
  REVIEWS=(review_data/reviews.json)
  [[ -f review_data/feedback_gt.json ]] && REVIEWS+=(review_data/feedback_gt.json)   # Referenzen aus Companion-Sitzungen (nur lesen)
  CMD+=(--folder Testphotos --reviews "${REVIEWS[@]}")
  (( ${#FILMS[@]} )) && CMD+=(--films "${FILMS[@]}")
  echo "Analysiere Testphotos mit der Produktions-Pipeline (das dauert einige Minuten; der Fortschritt steht in der Web-UI) ..."
fi
exec "${CMD[@]}"
