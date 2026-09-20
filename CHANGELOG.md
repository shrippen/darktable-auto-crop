# Changelog

## Unreleased

### Neu
- Editor zeigt die vier Faktoren der Konfidenz (Größenübereinstimmung, Kantenklarheit, Film-Vertrauen,
  Belichtung) mit dem schwächsten hervorgehoben; sie stehen auch im `feedback.jsonl`.
- `tools/feedback_report.py`: Auswertung des Feedbacks aus den Sitzungen (Korrekturrate je Gruppe,
  falsches Grün mit Symptom, schwächster Faktor, Gruppenwechsel, Export als Ground Truth).

### Geändert (Verhalten!)
- **Konfidenz neu:** `0.5·size_agree + 0.5·edge_score` (Version `size+edge-v2`). `film_trust` und der Belichtungsdeckel
  gehen nicht mehr ein (bleiben als Hinweise sichtbar). Leave-One-Film-Out: AUC 0.885 → 0.982, Abdeckung sicherer
  Treffer bei 98 % Precision 84.7 % → 99 %. Schwellen bleiben grün ≥ 0.5, gelb ≥ 0.3. Auf den Referenzdaten sind
  jetzt 91 statt 76 Bilder grün, weiterhin ohne Fehltreffer. Die Zahlen stützen sich auf nur 5 Fehltreffer.
- `tools/eval.py`: Leave-One-Film-Out, Tuning/Holdout (`tools/splits.json`), Referenzen aus den Companion-Sitzungen
  (`review_data/feedback_gt.json`, erzeugt mit `tools/build_feedback_gt.py`); Baseline 98/103 über 7 Filme.
- `tools/calibrate.py --loo`: Formelvergleich per Leave-One-Film-Out. `tools/signal_probe.py`: Trennschärfe einzelner Signale.
- Korrektur: `tools/feedback_report.py` skaliert die Toleranz jetzt auf die Exportgröße (60 px gelten bei 2000 px langer Kante).

### Neu: Algorithmus-Testlauf
- `./start_review_gui.sh` startet jetzt die Web-UI im Ordnermodus auf `Testphotos/` und analysiert mit derselben Pipeline
  wie in darktable (`--films`, `--resume`; das alte Tk-GUI mit eigener Pipeline nur noch mit `--tk`).
- **Referenz-Test** in der UI: Treffer gegen die Referenz (gesamt und je Gruppe), Marken auf den Kacheln, Sortierung
  nach Abweichung. „Akzeptieren“ speichert den erkannten Crop als bestätigte Referenz (`confirmed`), „Fertig“ schreibt
  Korrekturen und Bestätigungen nach `review_data/reviews.json`. Mehrere Referenzdateien möglich (`--reviews a.json b.json`).

### Bestätigt (Praxistest)
- Ablauf mit 36 Raw-Fotos inkl. Übergabe von Crops und Farben, erneutem Korrigieren, „Server stoppen“
  und automatischem Stopp beim Schließen von darktable.

## 0.1 (2026-09-20)

Erste Version mit Companion-UI (Web-Oberfläche) vor der Übergabe an darktable.

### Neu
- **Web-UI** (`companion/`, lokaler Server auf `127.0.0.1`, kein Framework):
  Galerie nach Grün/Gelb/Rot mit Crop-Overlay, Drag-and-drop, Mehrfachauswahl, Tastenkürzel,
  Crop-Editor (Ziehgriffe, Seitenverhältnis-Sperre, Kandidaten, Filmstreifen in Galerie-Reihenfolge,
  Vorladen der Nachbarbilder), Neu-Erkennung für die Auswahl als Vorschlag, zwei Undo-Ebenen,
  Fortschritt und Log, Deutsch/Englisch, dunkles und helles Theme (Designsystem „shrippen“).
- **Ablauf mit Übergabe:** „Fertig“ sperrt die Ansicht und schreibt `plan.json`; erst dann wirkt
  „Plan anwenden“ in darktable. „Zurück zur Prüfung“ entsperrt wieder (Revisionen); erneutes Anwenden
  ändert nur die Differenz.
- **Export aus darktable** (`darktable-cli`, volle Auflösung, ohne vorhandenen Crop, ohne die XMP zu ändern).
- **darktable-Plugin:** Knöpfe *Review starten*, *Plan anwenden*, *Prüfung öffnen*, *Server stoppen*,
  *Crop & Farben zurücksetzen*; Serveranzeige (startet/läuft/gestoppt) mit klickbarer URL.
- **Serverlebensdauer:** stoppt beim Schließen von darktable (`exit`-Ereignis und PID-Überwachung) und nach
  30 Minuten ohne Aktivität in der Web-UI.
- **Ordnermodus** ohne darktable (`python -m companion serve --folder …`), ersetzt langfristig `review_gui.py`.
- Sitzungen in `~/.cache/auto-crop-negative/`, Aufräumen nach 14 Tagen; Korrekturen als `feedback.jsonl`.
- Tests: Unit-/Integrationstests (`tests/test_companion.py`), Lua-Stub, Browser-Smoke-Test (`tests/ui_smoke.py`).

### Behoben
- Statusanzeige im Plugin traf bisher den Undo-Knopf (`children[10]`); jetzt ein benanntes Widget.
- Keine Markup-Tags mehr in Lua-Labels (darktable rendert sie nicht).

### Bekannte Einschränkungen
- Noch nicht in einer echten darktable-GUI abschließend geprüft: das Verhalten von `dt.styles.apply()`,
  „Plan anwenden“ in der Praxis und das `exit`-Ereignis. Der Lua-Teil ist gegen einen Stub der API getestet.
- Kein Test mit echten Raws in einer echten Bibliothek.
- `review_gui.py` bleibt vorerst bestehen.
