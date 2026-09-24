# Auto Crop Negative

Automatisches Zuschneiden digitaler Ablichtungsbilder auf den analogen Filmrahmen – als eigenständiges
Werkzeug mit Web-Oberfläche. darktable, RawTherapee und Lightroom/Camera Raw lassen sich optional anbinden.

## Problem

Wenn man analoge Negative mit einer Kamera digitalisiert, bleibt der Filmstreifen und der umgebende Tisch im Bild. Das manuelle Zuschneiden jedes einzelnen Bildes ist zeitaufwendig.

## Lösung

Die Erkennung findet den Filmrahmen im Bild und schlägt einen Crop vor. In einer lokalen Web-Oberfläche prüfst und
korrigierst du die Vorschläge, sortiert nach Sicherheit (🟢 grün / 🟡 gelb / 🔴 rot). **Fertig** übergibt das Ergebnis an ein
**Ziel**: zugeschnittene Kopien, eine Crop-Liste (JSON/CSV), Sidecars für RawTherapee oder Lightroom oder – über das
Lua-Plugin – direkt darktable.

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
   Ordner / darktable-Auswahl
            │
   ┌────────▼─────────┐   RAW?   ┌─────────────────────────────────────────────┐
   │  Eingabe          │ ───────▶ │ RAW-Konverter (companion/converters.py)     │
   │  JPEG/TIFF/PNG    │          │ darktable-cli · rawtherapee-cli · rawpy     │
   └────────┬─────────┘          └──────────────────────┬──────────────────────┘
            └──────────────────────┬─────────────────────┘
                          ┌────────▼─────────┐
                          │ Erkennung        │  auto_crop_negative.py, film_scale.py
                          │ (OpenCV + NumPy) │  Crop + Konfidenz je Bild, Konsens je Rolle
                          └────────┬─────────┘
                          ┌────────▼─────────┐
                          │ Companion        │  lokaler Server + Web-UI (companion/)
                          │ Prüfen, Fertig   │  Sitzung, Editor, Schräglage, Feedback
                          └────────┬─────────┘
                                   │ plan.json
   ┌──────────┬──────────┬─────────┴─┬───────────┬─────────────┬────────────┐
   │ darktable│ copies   │ json      │ xmp       │ rawtherapee │ reviews    │  Ziele (companion/targets/)
   │ (Lua)    │ Kopien   │ crops.json│ Lightroom │ .pp3        │ Kalibrierung│
   └──────────┴──────────┴───────────┴───────────┴─────────────┴────────────┘
```

Die Erkennung und die Web-UI sind werkzeugneutral. Nur die beiden Enden sind austauschbare Adapter: ein
**RAW-Konverter** auf der Eingabeseite (nur für RAW-Dateien) und ein **Ziel** auf der Ausgabeseite.

### Erkennungsstrategien

1. **Konturenanalyse** – Sucht Rechteck-Konturen nach Schwellwert-Binarisierung
2. **Canny + Hough** – Kantenerkennung und Liniensuche für klare Filmränder
3. **Gradient-Übergänge** – Analysiert mittlere Helligkeitsänderungen pro Zeile/Spalte
4. **Helligkeitsmaske** – Findet den größten zusammenhängenden Bereich (für unterbelichtete Bilder)

Alle Strategien laufen parallel, und die Ergebnisse werden zusammengeführt. Übereinstimmungen erhalten einen Konfidenz-Boost.
Bilder im selben Ordner gelten als eine **Filmrolle**; die Rolle liefert Größe und Maßstab (Perforation) für alle ihre Bilder.

### Unsicherheitsbehandlung

- **Konfidenzwert** (0.0–1.0) je Bild; die Web-UI zeigt, aus welchen Faktoren er sich zusammensetzt.
- Gruppen: 🟢 grün ab 0.5 (sicher), 🟡 gelb ab 0.3 (prüfen), 🔴 rot darunter. Die Schwellen sind in der Web-UI einstellbar.
- Rote Bilder werden **nicht** zugeschnitten, außer du korrigierst oder akzeptierst sie.
- Die Gruppe wird ans Ziel weitergegeben: in darktable, RawTherapee und Lightroom als **Farblabel** (rot/gelb/grün),
  in `crops.json`/`crops.csv` als Spalte `label`.

## Installation

Voraussetzungen: Python 3.8+, OpenCV, NumPy, Pillow (werden mitinstalliert).

### Ohne darktable

```bash
git clone https://github.com/shrippen/darktable-auto-crop.git
cd darktable-auto-crop
./install.sh --standalone
```

Legt eine eigene Python-Umgebung unter `~/.local/share/auto-crop-negative/venv` an, installiert das Paket samt `rawpy`
(RAW-Entwicklung ohne externes Programm, optional) und verlinkt den Befehl `auto-crop-negative` nach `~/.local/bin`.
Kein Lua-Schritt, keine darktable-Dateien.

Alternativ von Hand: `pip install ".[raw]"` (ohne `[raw]`: RAWs brauchen dann `darktable-cli` oder `rawtherapee-cli`).

### Als darktable-Plugin

- Darktable (4.0+) mit Lua-Unterstützung

```bash
cd darktable-auto-crop
./install.sh
```

Danach darktable neu starten und im Script Manager `contrib → Auto Crop Negative` einschalten. Das Plugin erscheint als
Panel „Auto Crop Negative“.

Manuelle Installation:

1. `auto_crop_negative.lua` nach `~/.config/darktable/lua/` kopieren
2. `auto_crop_negative.py` nach `~/.config/darktable/lua/` kopieren und `chmod +x`
3. Falls `~/.config/darktable/lua/init.lua` existiert: `require "auto_crop_negative"` anfügen
4. Falls nicht: `init.lua` mit diesem Inhalt erstellen:
   ```lua
   require "auto_crop_negative"
   ```

## Nutzung ohne darktable

```bash
auto-crop-negative ~/Scans/2026-09            # = auto-crop-negative open ~/Scans/2026-09
```

1. Der Ordner wird durchsucht: Bilder direkt darin oder in Unterordnern (eine Ebene; **jeder Unterordner = eine
   Filmrolle**). Gelesen werden JPEG, TIFF, PNG und RAWs (NEF, CR2, CR3, ARW, RAF, ORF, RW2, DNG, …). Liegen RAW und
   JPEG gleichen Namens nebeneinander (RAW+JPEG der Kamera), zählt nur das RAW.
2. RAWs werden mit einem **RAW-Konverter** in voller Auflösung entwickelt, ohne einen vorhandenen Crop; dann läuft die
   Erkennung und die Web-UI öffnet sich im Browser.
3. In der Web-UI prüfen und korrigieren wie gewohnt (siehe [Web-UI](#web-ui)).
4. **Fertig**: die Ansicht wird gesperrt und der Plan **sofort** auf das Ziel angewendet. Das Ergebnis (OK /
   übersprungen / Fehler, Hinweise je Bild) steht oben in der Web-UI.
5. Nicht zufrieden? **Zurück zur Prüfung**, ändern, erneut **Fertig**: geschrieben wird nur, was sich geändert hat.
   Übersprungene Bilder, die vorher zugeschnitten waren, werden zurückgenommen (Kopie gelöscht, Crop im Sidecar aus).

Erneutes Starten mit demselben Ordner und Ziel **setzt die letzte Sitzung fort** (keine Neuanalyse). `--new` erzwingt eine
neue Analyse; haben sich die Bilder im Ordner geändert, entsteht ohnehin eine neue Sitzung.

```bash
auto-crop-negative check ~/Scans/2026-09      # verfügbare Konverter, vorgeschlagenes Ziel
auto-crop-negative open ~/Scans --target xmp --films 33 34 --converter rawpy --out ~/Export --no-browser
```

| Option | Bedeutung | Standard |
|--------|-----------|----------|
| `--target` | Ziel, siehe unten | aus dem Ordnerinhalt |
| `--out` | Ausgabeordner für `copies`/`json` | `ORDNER/autocrop` |
| `--converter` | `darktable`, `rawtherapee`, `rawpy` | nach vorhandenen Sidecars, sonst der erste verfügbare |
| `--films` | nur diese Rollen (Unterordner, `33` passt auf `Film 33`) | alle |
| `--new` | neu analysieren statt fortsetzen | aus |
| `--no-browser` | Browser nicht öffnen (URL steht in der Ausgabe) | aus |
| `--idle-minutes` | Server endet nach so viel Leerlauf | 30 |

### Ziele

| Ziel | Schreibt | Geradestellen | Automatisch gewählt, wenn |
|------|----------|---------------|---------------------------|
| `copies` | zugeschnittene Kopien `OUT/<Rolle>/<Name>` + `crops.json`/`crops.csv` | ja, beim Schneiden | nur JPEG/TIFF/PNG im Ordner |
| `json` | `OUT/crops.json` und `OUT/crops.csv` (Crop normiert und in Pixeln, Winkel, Gruppe) | Winkel in der Datei | RAWs ohne bekannte Sidecars |
| `xmp` | Adobe-Sidecar `<Name>.xmp`: `crs:HasCrop`, `crs:Crop*`, Farblabel `xmp:Label` | nein | eine Adobe-XMP (`crs:`) liegt neben einem RAW |
| `rawtherapee` | Profil `<Datei>.pp3`: `[Crop]`, `[General] ColorLabel` | nein | ein `.pp3` liegt im Ordner |
| `darktable` | über das Lua-Plugin, siehe [Nutzung mit darktable](#nutzung-mit-darktable) | ja | – (nur aus darktable) |
| `reviews` | Kalibrierung, siehe [Kalibrierung](#kalibrierung-und-ground-truth) | Winkel als Referenz | – |

Die Originale werden nie verändert. Sidecars werden **ergänzt**: vorhandene Einstellungen bleiben stehen, nur Crop und
Farblabel werden gesetzt. Ausgabeordner tragen die Markierung `.autocrop-output` und werden bei der nächsten Suche übersprungen.

Grenzen der Ziele (ehrlicher Stand):
- **`copies`**: Quelle ist das analysierte Bild, bei RAWs also der 8-Bit-JPEG-Export des Konverters. 8-Bit-Bilder behalten
  ICC-Profil und EXIF; 16-Bit-TIFF/PNG behalten die Bittiefe, aber ohne ICC-Profil und EXIF.
- **`xmp`**: nur für proprietäre RAWs (Lightroom liest für JPEG/TIFF/DNG keine Sidecars; diese Bilder werden
  übersprungen). Die Crop-Werte werden in die Sensorlage der RAW-Datei umgerechnet (Orientierung aus dem RAW-Kopf bzw.
  über rawpy); diese Annahme über Adobes Koordinaten ist **nicht gegen Lightroom geprüft**. Lightroom übernimmt geänderte
  Sidecars erst mit „Metadaten aus Datei lesen“. Ein Schräglagen-Winkel wird nicht übertragen; der Crop geht achsparallel
  im Originalrahmen hinüber (Hinweis im Ergebnis).
- **`rawtherapee`**: Crop in Pixeln des analysierten Rahmens. Mit `--converter rawtherapee` ist das RawTherapees eigener
  Rahmen; mit einem anderen Konverter kann er um einige Pixel abweichen (Hinweis im Ergebnis). Kein Winkel. Ein neu
  angelegtes `.pp3` enthält nur Crop und Label – RawTherapee nimmt für den Rest seine eingebauten Standards, nicht dein
  Standardprofil. Besser: Bilder vorher einmal in RawTherapee öffnen. Gegen RawTherapee selbst nicht getestet.
- **Capture One** wird nicht unterstützt (zurückgestellt, siehe [`roadmap-standalone.md`](roadmap-standalone.md)).

### RAW-Konverter

| Konverter | Programm | Bearbeitung fließt ein | Hinweis |
|-----------|----------|------------------------|---------|
| `darktable` | `darktable-cli` | XMP-Historie (`<Datei>.xmp`), Crop ausgeschaltet | wie im Plugin; getestet |
| `rawtherapee` | `rawtherapee-cli` | Standardprofil + `<Datei>.pp3`, Crop ausgeschaltet | nicht getestet (kein RawTherapee in der Testumgebung) |
| `rawpy` | Python-Paket `rawpy` (LibRaw) | keine (Kamera-Weißabgleich, nicht invertiert) | kein externes Programm; mit einer DNG getestet |

`auto` wählt das Werkzeug, dessen Sidecars neben den RAWs liegen, sonst den ersten verfügbaren in der Reihenfolge der
Tabelle. Eine Sitzung nutzt genau einen Konverter, damit alle Bilder einer Rolle denselben Rahmen haben. Die Erkennung
funktioniert auch auf nicht invertierten Negativen, ist dort aber etwas schwächer (Trefferquote 84 % gegen 87 % auf
entwickelten Bildern, siehe `roadmap.md`, Nachtrag 2026-09-22).

## Nutzung mit darktable

### Mit Web-UI (Companion)
1. Bilder in der Hellkammer auswählen → **Review starten**. Darktable exportiert die Raws
   (volle Auflösung, ohne vorhandenen Crop), die Erkennung läuft, die Web-UI öffnet sich im Browser.
2. In der Web-UI prüfen (siehe [Web-UI](#web-ui)).
3. **Fertig** drücken: Die Ansicht wird gesperrt und der Plan übergeben.
4. In darktable **Plan anwenden** (wirkt erst nach „Fertig“). Nicht zufrieden? **Prüfung öffnen** und in der
   Web-UI **Zurück zur Prüfung**; erneutes Anwenden ändert nur, was sich geändert hat.

Im Plugin-Bereich von darktable zeigt eine große Statuszeile, ob der Server **startet**, **läuft** oder **gestoppt** ist. Die URL steht als Knopf darunter (Klick öffnet den Browser), dazu ein **Server stoppen**-Knopf. Der Server beendet sich außerdem selbst, wenn darktable geschlossen wird oder 30 Minuten lang keine Aktivität in der Web-UI war (die UI warnt fünf Minuten vorher). Die Sitzung bleibt dabei erhalten: **Prüfung öffnen** startet ihn wieder.

**Zurücksetzen:** Der Knopf **Crop & Farben zurücksetzen** im Plugin-Bereich schaltet bei allen selektierten Bildern das Crop-Modul aus und leert die Farblabels rot/gelb/grün (blau/lila bleiben unangetastet). Er verlangt zwei Klicks binnen 6 Sekunden. Die History behält einen zusätzlichen Schritt „Crop aus“ (die Lua-API kann keine History-Einträge löschen).

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

## Web-UI

Gilt für alle Ziele. Kacheln nach 🟢 Grün / 🟡 Gelb / 🔴 Rot, Umsortieren per Drag-and-drop, Crop-Editor mit Ziehgriffen
und Kandidaten, „Größe auf Rolle“, „Auswahl neu erkennen“ mit anderen Einstellungen, Akzeptieren (`A`) und Überspringen (`S`).
Kopf und Ablaufleiste zeigen das aktive Ziel. Der Server beendet sich nach 30 Minuten ohne Aktivität; die Sitzung bleibt
erhalten (ohne darktable: denselben Befehl erneut starten).

Sitzungen liegen in `~/.cache/auto-crop-negative/` und werden nach 14 Tagen aufgeräumt
(`auto-crop-negative cleanup`). Details und Entwurf: [`companion-ui-plan.md`](companion-ui-plan.md).

### Schräglage

Der Rahmen einer Aufnahme kann leicht gekippt sein. Die Erkennung liefert weiter ein achsparalleles Rechteck, misst
aber zusätzlich die Schräglage (in Grad, + = Inhalt im Uhrzeigersinn) und zeigt sie an. Im Editor lässt sich das Bild
per Knopf **Tilt anwenden** (Taste T) **geradestellen**; der Winkel ist änderbar, der Crop sitzt auf dem gedrehten Bild.
„Auswahl geradestellen“ in der Aktionsleiste macht das für mehrere Bilder. Die Messung ist bis etwa ±9° ausgelegt; bei
niedriger Sicherheit bitte per Auge prüfen.

Wer dreht: **darktable** mit dem Modul „Drehen und Perspektive“, **`copies`** beim Schneiden, **`json`** schreibt den Winkel
in die Datei. **`xmp`** und **`rawtherapee`** können nicht drehen; der Editor weist darauf hin, und der Crop wird ohne
Drehung im Originalrahmen übertragen.

## Kalibrierung und Ground Truth

**Algorithmus testen und Ground Truth erzeugen (ohne darktable):**

```bash
./start_review_gui.sh                 # analysiert Testphotos/ mit derselben Pipeline wie in darktable
./start_review_gui.sh --films 33 34   # nur diese Rollen (z. B. noch ungelabelte)
./start_review_gui.sh --resume        # letzte Ordner-Sitzung fortsetzen (keine Neuanalyse)
```

Das ist das Ziel `reviews`. In der Web-UI zeigt der **Referenz-Test**, wie viele erkannte Crops innerhalb der Toleranz (60 px bei 2000 px langer
Kante) von deiner Referenz liegen (gesamt und je Gruppe), und Kacheln tragen die Marke *Treffer*/*Abweichung*
(sortierbar nach Abweichung). Korrigiere falsche Crops im Editor oder bestätige richtige mit **Akzeptieren** (Taste `A`;
nur akzeptieren, was du gesehen hast). **Fertig** schreibt Korrekturen und bestätigte Crops nach `review_data/reviews.json`
(`git diff` zeigt die Änderungen). Danach messen: `tools/eval.py`. Das alte Tk-GUI läuft nur noch als `./start_review_gui.sh --tk`.

Messen und kalibrieren (Roadmap): `tools/eval.py` (Trefferquote, Leave-One-Film-Out, Tuning/Holdout), `tools/calibrate.py --loo --from-json <eval.json>` (Formelvergleich), `tools/signal_probe.py` (Trennschärfe einzelner Signale), `tools/build_feedback_gt.py` (Companion-Sitzungen als Referenzen; die Bilder bleiben lokal).
Feedback auswerten (Roadmap Phase 5): `.venv/bin/python tools/feedback_report.py` (vor dem Aufräumen nach 14 Tagen).

## Testen

```bash
python3 auto_crop_negative.py --debug -o /tmp/debug_crop.jpg '/path/to/ablichtung.jpg'   # Erkennung allein, JSON-Ausgabe
.venv/bin/python -m unittest discover -s tests                                             # Unit-/Integrationstests
```

Die JSON-Ausgabe enthält x, y, width, height, confidence, needs_review. Browser-Test: `tests/ui_smoke.py` (benötigt
Playwright und `Testphotos/`). `tests/test_standalone.py` deckt Ziele, Konverter-Wahl, Bildsuche und CLI ab; der
rawpy-Test erzeugt dafür eine DNG (benötigt `rawpy` und `tifffile`, sonst übersprungen).

## Dateien

| Datei | Beschreibung |
|-------|-------------|
| `auto_crop_negative.py` | Erkennung: Bildanalyse und Crop-Erkennung (Kern) |
| `film_scale.py` | Maßstab einer Rolle aus der Perforation |
| `companion/` | Companion: lokaler Server, Sitzungen, Web-Oberfläche (`static/`), CLI (`__main__.py`) |
| `companion/converters.py` | RAW-Konverter (Eingabe-Adapter): darktable, RawTherapee, rawpy |
| `companion/targets/` | Ziele (Ausgabe-Adapter): darktable, reviews, json, copies, xmp, rawtherapee |
| `auto_crop_negative.lua` | darktable-Integration (Lua-Plugin) |
| `pyproject.toml` | Python-Paket, Befehl `auto-crop-negative` |
| `tests/` | Unit-/Integrationstests, Lua-Stub, Browser-Smoke-Test |
| `install.sh` | Installation (`--standalone`: ohne darktable) |
| `roadmap-standalone.md` | Roadmap zur Unabhängigkeit von darktable, Stand und Namensvorschläge |
| `scan-howto.md` / `scan-howto.en.md` | Wie man Negative scannt, damit die Erkennung gut funktioniert (deutsch/englisch) |

## Name

Das Projekt heißt „Auto Crop Negative“; Paket, Befehl, Cache- und Konfigurationsordner tragen diesen Namen bereits. Nur das
Repository heißt noch `darktable-auto-crop`. Vorschläge und Empfehlung zur Umbenennung:
[`roadmap-standalone.md`, Phase 4](roadmap-standalone.md#namensvorschläge).

## License

[MIT](LICENSE)
