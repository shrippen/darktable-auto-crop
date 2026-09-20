# Auto Crop Negative – Darktable-Plugin

Automatisches Zuschneiden digitaler Ablichtungsbilder auf den analogen Filmrahmen.

## Problem

Wenn man analoge Negative mit einer Kamera digitalisiert, bleibt der Filmstreifen und der umgebende Tisch im Bild. Das manuelle Zuschneiden jedes einzelnen Bildes ist zeitaufwendig.

## Lösung

Dieses Plugin erkennt automatisch den Filmrahmen im Bild und schneidet es passgenau zu. Bilder mit unsicherer Erkennung werden mit einem roten Farblabel markiert, sodass du sie manuell überprüfen kannst.

### Erkannte Filmformate

| Format | Seitenverhältnis |
|--------|-----------------|
| **Kleinbild 135** (Standard) | 2:3 |
| Mittelformat 6x6 | 1:1 |
| Mittelformat 6x4.5 | 4:5 |
| Mittelformat 6x7 | 6:7 |
| Mittelformat 6x9 | 2:3 |

## Architektur

```
┌─────────────────┐      JSON       ┌───────────────────────┐
│  Lua-Frontend   │ ──────────────→ │  Python-Backend       │
│  (Darktable)    │                 │  (OpenCV + NumPy)     │
│                 │ ←────────────── │                       │
│  • Button/Icon  │   Crop-Koords   │  • 4 Erkennungs-      │
│  • Crop-Modul   │   + Konfidenz   │    strategien         │
│  • Label-Set    │                 │  • Konfidenz-Scoring  │
└─────────────────┘                 └───────────────────────┘
```

### Erkennungsstrategien

1. **Konturenanalyse** – Sucht Rechteck-Konturen nach Schwellwert-Binarisierung
2. **Canny + Hough** – Kantenerkennung und Liniensuche für klare Filmränder
3. **Gradient-Übergänge** – Analysiert mittlere Helligkeitsänderungen pro Zeile/Spalte
4. **Helligkeitsmaske** – Findet den größten zusammenhängenden Bereich (für unterbelichtete Bilder)

Alle Strategien laufen parallel, und die Ergebnisse werden zusammengeführt. Übereinstimmungen erhalten einen Konfidenz-Boost.

### Unsicherheitsbehandlung

- **Konfidenzwert** (0.0–1.0) wird für jeden Kandidaten berechnet
- Faktoren: Seitenverhältnis, Fläche, Randabstand, Rand-Kontrast, Strategie-Übereinstimmung
- Bei Konfidenz < Schwellenwert (Standard: 0.7):
  - Crop wird trotzdem angewendet
  - Bild bekommt ein **rotes Farblabel** → "Zur Kontrolle markiert"
- Bei Konfidenz ≥ Schwellenwert:
  - Bild bekommt ein **grünes Farblabel** → "Automatisch zugeschnitten"

## Installation

### Voraussetzungen

- Darktable (4.0+) mit Lua-Unterstützung
- Python 3.8+
- OpenCV (`pip install opencv-python-headless`)
- NumPy (`pip install numpy`)

### Schritte

```bash
cd /home/arian/Hacking/darktable-automatic-negative-cropping
chmod +x install.sh
./install.sh
```

Danach Darktable neu starten. Das Plugin erscheint als Panel "Auto Crop Negative" in der rechten Seitenleiste der Dunkelkammer.

### Manuelle Installation

1. `auto_crop_negative.lua` nach `~/.config/darktable/lua/` kopieren
2. `auto_crop_negative.py` nach `~/.config/darktable/lua/` kopieren und `chmod +x`
3. Falls `~/.config/darktable/lua/init.lua` existiert: `require "auto_crop_negative"` anfügen
4. Falls nicht: `init.lua` mit diesem Inhalt erstellen:
   ```lua
   require "auto_crop_negative"
   ```

## Nutzung

### Mit Web-UI (Companion)
1. Bilder in der Hellkammer auswählen → **Review starten**. Darktable exportiert die Raws
   (volle Auflösung, ohne vorhandenen Crop), die Erkennung läuft, die Web-UI öffnet sich im Browser.
2. In der Web-UI prüfen: Kacheln nach 🟢 Grün / 🟡 Gelb / 🔴 Rot, Umsortieren per Drag-and-drop,
   Crop-Editor mit Ziehgriffen und Kandidaten, „Auswahl neu erkennen“ mit anderen Einstellungen.
3. **Fertig** drücken: Die Ansicht wird gesperrt und der Plan übergeben.
4. In darktable **Plan anwenden** (wirkt erst nach „Fertig“). Nicht zufrieden? **Prüfung öffnen** und in der
   Web-UI **Zurück zur Prüfung**; erneutes Anwenden ändert nur, was sich geändert hat.

**Algorithmus testen und Ground Truth erzeugen (ohne darktable):**

```bash
./start_review_gui.sh                 # analysiert Testphotos/ mit derselben Pipeline wie in darktable
./start_review_gui.sh --films 33 34   # nur diese Rollen (z. B. noch ungelabelte)
./start_review_gui.sh --resume        # letzte Ordner-Sitzung fortsetzen (keine Neuanalyse)
```

In der Web-UI zeigt der **Referenz-Test**, wie viele erkannte Crops innerhalb der Toleranz (60 px bei 2000 px langer
Kante) von deiner Referenz liegen (gesamt und je Gruppe), und Kacheln tragen die Marke *Treffer*/*Abweichung*
(sortierbar nach Abweichung). Korrigiere falsche Crops im Editor oder bestätige richtige mit **Akzeptieren** (Taste `A`;
nur akzeptieren, was du gesehen hast). **Fertig** schreibt Korrekturen und bestätigte Crops nach `review_data/reviews.json`
(`git diff` zeigt die Änderungen). Danach messen: `tools/eval.py`. Das alte Tk-GUI läuft nur noch als `./start_review_gui.sh --tk`.

Im Plugin-Bereich von darktable zeigt eine große Statuszeile, ob der Server **startet**, **läuft** oder **gestoppt** ist. Die URL steht als Knopf darunter (Klick öffnet den Browser), dazu ein **Server stoppen**-Knopf. Der Server beendet sich außerdem selbst, wenn darktable geschlossen wird oder 30 Minuten lang keine Aktivität in der Web-UI war (die UI warnt fünf Minuten vorher). Die Sitzung bleibt dabei erhalten: **Prüfung öffnen** startet ihn wieder.

**Zurücksetzen:** Der Knopf **Crop & Farben zurücksetzen** im Plugin-Bereich schaltet bei allen selektierten Bildern das Crop-Modul aus und leert die Farblabels rot/gelb/grün (blau/lila bleiben unangetastet). Er verlangt zwei Klicks binnen 6 Sekunden. Die History behält einen zusätzlichen Schritt „Crop aus“ (die Lua-API kann keine History-Einträge löschen).

Sitzungen liegen in `~/.cache/auto-crop-negative/` und werden nach 14 Tagen aufgeräumt
(`python -m companion cleanup`). Details und Entwurf: [`companion-ui-plan.md`](companion-ui-plan.md).
Messen und kalibrieren (Roadmap): `tools/eval.py` (Trefferquote, Leave-One-Film-Out, Tuning/Holdout), `tools/calibrate.py --loo --from-json <eval.json>` (Formelvergleich), `tools/signal_probe.py` (Trennschärfe einzelner Signale), `tools/build_feedback_gt.py` (Companion-Sitzungen als Referenzen; die Bilder bleiben lokal).
Feedback auswerten (Roadmap Phase 5): `.venv/bin/python tools/feedback_report.py` (vor dem Aufräumen nach 14 Tagen).
Tests: `.venv/bin/python -m unittest discover -s tests` (Browser-Test: `tests/ui_smoke.py`, benötigt Playwright).

#### Schräglage

Der Rahmen einer Aufnahme kann leicht gekippt sein. Die Erkennung liefert weiter ein achsparalleles Rechteck, misst
aber zusätzlich die Schräglage (in Grad, + = Inhalt im Uhrzeigersinn) und zeigt sie an. Im Editor lässt sich das Bild
per Knopf **Tilt anwenden** (Taste T) **geradestellen**: darktable dreht dann mit dem Modul „Drehen und Perspektive“ um den Messwert (der Winkel
ist änderbar), der Crop sitzt auf dem gedrehten Bild. „Auswahl geradestellen“ in der Aktionsleiste macht das für mehrere
Bilder. Die Messung ist bis etwa ±9° ausgelegt; bei niedriger Sicherheit bitte per Auge prüfen.

### Einzelbild
1. Bild in der Dunkelkammer öffnen
2. Auf "Einzelbild zuschneiden" klicken
3. Oder Tastenkürzel verwenden (in Voreinstellungen → Tastatur zuweisen)

### Mehrere Bilder
1. Bilder in der Hellkammer auswählen (Strg/Klick)
2. In der Dunkelkammer "Alle ausgewählten zuschneiden" klicken
3. Ergebnis prüfen: 🔴 Rot = Kontrolle nötig, 🟢 Grün = OK

### Voreinstellungen
Unter **Voreinstellungen → Lua → Auto Crop Negative**:

| Einstellung | Beschreibung | Standard |
|-------------|-------------|----------|
| Pfad zum Python-Skript | Pfad zu `auto_crop_negative.py` | `auto_crop_negative.py` |
| Konfidenz-Schwelle | Schwelle für Markierung (0.0–1.0) | `0.7` |
| Filmformat | 35mm, 6x6, 6x4.5, 6x7, 6x9 | `35mm` |

## Testen (ohne Darktable)

```bash
python3 auto_crop_negative.py --debug \
    -o /tmp/debug_crop.jpg \
    '/path/to/ablichtung.jpg'
```

Die JSON-Ausgabe enthält x, y, width, height, confidence, needs_review.

## Dateien

| Datei | Beschreibung |
|-------|-------------|
| `auto_crop_negative.py` | Python-Backend: Bildanalyse und Crop-Erkennung |
| `auto_crop_negative.lua` | Lua-Frontend: Darktable-Integration |
| `companion/` | Companion-UI: lokaler Server, Sitzungen, Export, Web-Oberfläche (`static/`) |
| `tests/` | Unit-/Integrationstests, Lua-Stub, Browser-Smoke-Test |
| `install.sh` | Installations-Skript |
| `README.md` | Diese Dokumentation |

## License

[MIT](LICENSE)
