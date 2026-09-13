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
| `install.sh` | Installations-Skript |
| `README.md` | Diese Dokumentation |
