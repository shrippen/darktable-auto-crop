# Changelog

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
