#!/usr/bin/env bash
# VERALTET: altes Tk-GUI mit eigener Zwei-Pass-Pipeline (batch_test.py), NICHT dieselbe Pipeline wie
# Plugin und Web-UI. Fuer Tests stattdessen ./start_review_gui.sh (Web-UI) verwenden.
# Startet die Review-GUI fuer Auto-Crop-Negative.
#
# Prueft vor dem Start, ob die Analyse-Daten (review_data/results.json)
# vorhanden und aktuell sind. Bei Bedarf wird interaktiv angeboten,
# in welchem Umfang sie neu erstellt werden:
#   1) Vollstaendig (alle Bilder, ~5 Min.)
#   2) Inkrementell (nur neue/geaenderte Bilder)
#   3) Vorhandene Daten behalten
#   4) Abbrechen
#
# Optionen:
#   --yes, -y       automatisch: fehlend/Algorithmus geaendert -> vollstaendig,
#                   neue Bilder -> inkrementell, aktuell -> direkt GUI
#   --full          ohne Nachfrage vollstaendig neu analysieren, dann GUI
#   --incremental   ohne Nachfrage inkrementell analysieren, dann GUI
#   --keep          Analyse-Daten unangetastet lassen, GUI direkt starten
#   -h, --help      Hilfe
# Alle uebrigen Argumente werden an review_gui.py weitergereicht.
set -euo pipefail
cd "$(dirname "$0")"

PY=".venv/bin/python"
DATA_DIR="$PWD/review_data"
RESULTS="$DATA_DIR/results.json"
ALGO="$PWD/auto_crop_negative.py"
TESTPHOTOS="$PWD/Testphotos"

MODE=""
GUI_ARGS=()
for arg in "$@"; do
  case "$arg" in
    --full)         MODE="full" ;;
    --incremental)  MODE="incremental" ;;
    --keep)         MODE="keep" ;;
    --yes|-y)       MODE="yes" ;;
    --help|-h)
      sed -n '2,19p' "$0" | sed 's/^# \{0,1\}//'
      exit 0 ;;
    *)              GUI_ARGS+=("$arg") ;;
  esac
done

results_ok() {
  [[ -f "$RESULTS" ]] && \
    "$PY" -c 'import json,sys; json.load(open(sys.argv[1]))' "$RESULTS" \
    >/dev/null 2>&1
}

images_newer_than_results() {
  find "$TESTPHOTOS" -type f \
    \( -iname '*.jpg' -o -iname '*.jpeg' -o -iname '*.tif' \
       -o -iname '*.tiff' -o -iname '*.png' \) \
    -newer "$RESULTS" -print -quit 2>/dev/null | grep -q .
}

status="fresh"
if ! results_ok; then
  status="missing"
elif [[ "$ALGO" -nt "$RESULTS" ]]; then
  status="stale-algo"
elif images_newer_than_results; then
  status="new-images"
fi

run_full() {
  echo "-> Vollstaendige Neuanalyse aller Bilder (dauert ca. 10 Min.)..."
  "$PY" batch_test.py --two-pass
}

run_incremental() {
  echo "-> Inkrementelle Analyse (nur neue/geaenderte Bilder)..."
  "$PY" batch_test.py --two-pass --incremental
}

start_gui() {
  if (( ${#GUI_ARGS[@]} > 0 )); then
    exec "$PY" review_gui.py "${GUI_ARGS[@]}"
  else
    exec "$PY" review_gui.py
  fi
}

case "$MODE" in
  keep)
    start_gui ;;
  full)
    run_full
    start_gui ;;
  incremental)
    run_incremental
    start_gui ;;
  yes)
    case "$status" in
      missing|stale-algo) run_full ;;
      new-images)         run_incremental ;;
    esac
    start_gui ;;
  *)
    # Interaktive Auswahl (stdin muss ein Terminal sein)
    if [[ ! -t 0 ]]; then
      echo "FEHLER: Keine interaktive Eingabe moeglich." >&2
      echo "Bitte eine Option uebergeben: --full | --incremental | --keep | --yes" >&2
      exit 1
    fi
    echo
    case "$status" in
      missing)    echo "Keine Analyse-Daten gefunden ($RESULTS)." ;;
      stale-algo) echo "auto_crop_negative.py wurde seit der letzten Analyse geaendert." ;;
      new-images) echo "Es gibt neue oder geaenderte Bilder in Testphotos/." ;;
      fresh)      echo "Analyse-Daten vorhanden." ;;
    esac
    echo
    echo "Wie sollen die Analyse-Daten erstellt werden?"
    echo "  1) Vollstaendig - alle Bilder neu analysieren (~10 Min.)"
    echo "  2) Nur neue/geaenderte Bilder (inkrementell, schnell)"
    echo "  3) Vorhandene Daten behalten (GUI mit aktuellem Stand starten)"
    echo "  4) Abbrechen"
    while true; do
      local_reply=""
      if ! read -rp "Auswahl [1-4, Enter=1]: " local_reply; then
        echo "Abgebrochen (keine Eingabe)."
        exit 1
      fi
      case "${local_reply:-1}" in
        1) run_full;    start_gui ;;
        2) run_incremental; start_gui ;;
        3) start_gui ;;
        4) echo "Abgebrochen."; exit 0 ;;
        *) echo "Bitte 1, 2, 3 oder 4 eingeben." ;;
      esac
    done ;;
esac
