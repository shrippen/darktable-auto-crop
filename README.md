# Kader

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
                          │ Erkennung        │  kader.py, film_scale.py
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

### Herunterladen (Windows, Linux)

Ohne Python und ohne Terminal: auf der [Release-Seite](https://github.com/shrippen/kader/releases/latest)
`Kader-…-windows-x64.exe` bzw. `Kader-…-x86_64.AppImage` herunterladen und doppelklicken. Ein Dialog fragt nach dem
Ordner mit den Scans, danach öffnet sich die Prüfung im Browser; ein kleines Fenster zeigt den Fortschritt und beendet
Kader. RAWs werden ohne weitere Programme entwickelt (rawpy ist enthalten). Die exe ist nicht signiert (SmartScreen:
„Weitere Informationen“ → „Trotzdem ausführen“), das AppImage muss einmal ausführbar gemacht werden. Bauen:
[packaging/README.md](packaging/README.md).

### Mit Python

Voraussetzungen: Python 3.8+, OpenCV, NumPy, Pillow (werden mitinstalliert).

### Ohne darktable

```bash
git clone https://git.arianw.de/shrippen/kader.git
cd kader
./install.sh --standalone
```

Legt eine eigene Python-Umgebung unter `~/.local/share/kader/venv` an, installiert das Paket samt `rawpy`
(RAW-Entwicklung ohne externes Programm, optional) und verlinkt den Befehl `kader` nach `~/.local/bin`.
Kein Lua-Schritt, keine darktable-Dateien.

Alternativ von Hand: `pip install ".[raw]"` (ohne `[raw]`: RAWs brauchen dann `darktable-cli` oder `rawtherapee-cli`).

### Als darktable-Plugin

- Darktable (4.0+) mit Lua-Unterstützung

```bash
cd kader
./install.sh
```

Danach darktable neu starten und im Script Manager `contrib → Kader` einschalten. Das Plugin erscheint als
Panel „Kader“.

Manuelle Installation:

1. `kader.lua` nach `~/.config/darktable/lua/` kopieren
2. `kader.py` nach `~/.config/darktable/lua/` kopieren und `chmod +x`
3. Falls `~/.config/darktable/lua/init.lua` existiert: `require "kader"` anfügen
4. Falls nicht: `init.lua` mit diesem Inhalt erstellen:
   ```lua
   require "kader"
   ```

## Nutzung ohne darktable

```bash
kader ~/Scans/2026-09            # = kader open ~/Scans/2026-09
```

1. Der Ordner wird durchsucht: Bilder direkt darin oder in Unterordnern (eine Ebene; **jeder Unterordner = eine
   Filmrolle**). Gelesen werden JPEG, TIFF, PNG und RAWs (NEF, CR2, CR3, ARW, RAF, ORF, RW2, DNG, …). Liegen RAW und
   JPEG gleichen Namens nebeneinander (RAW+JPEG der Kamera), zählt nur das RAW.
2. RAWs werden mit einem **RAW-Konverter** in voller Auflösung entwickelt, ohne einen vorhandenen Crop; dann läuft die
   Erkennung und die Web-UI öffnet sich im Browser.
3. Oben in der Web-UI wählst du das **Ziel** – wofür „Fertig“ den Crop schreibt (siehe [Ziele](#ziele)). Ein
   Vorschlag ist schon markiert; jede Option erklärt ausführlich, was sie tut, ob sie Schräglage geradestellen kann und
   ob es in dieser Sitzung Bilder gibt, für die sie nichts schreibt (z. B. JPEGs bei `xmp`). Das Ziel lässt sich
   jederzeit vor „Fertig“ wechseln, danach über „Zurück zur Prüfung“ erneut.
4. Prüfen und korrigieren wie gewohnt (siehe [Web-UI](#web-ui)).
5. **Fertig**: die Ansicht wird gesperrt und der Plan **sofort** auf das gewählte Ziel angewendet. Das Ergebnis (OK /
   übersprungen / Fehler, Hinweise je Bild) steht oben in der Web-UI.
6. Nicht zufrieden? **Zurück zur Prüfung**, ändern, erneut **Fertig**: geschrieben wird nur, was sich geändert hat.
   Übersprungene Bilder, die vorher zugeschnitten waren, werden zurückgenommen (Kopie gelöscht, Crop im Sidecar aus).
   Wechselst du dabei das Ziel, bleiben die Dateien des vorherigen Ziels unangetastet liegen (die Web-UI weist darauf hin).

Erneutes Starten mit demselben Ordner und Ziel **setzt die letzte Sitzung fort**, auch wenn der Ordner inzwischen
gewachsen ist: neue Bilder werden ergänzt und analysiert, bereits getroffene Entscheidungen an den alten bleiben
unangetastet. Fehlt der alten Sitzung dagegen ein Bild, das jetzt nicht mehr da ist (gelöscht), passt sie nicht mehr und
es entsteht eine neue. `--new` erzwingt in jedem Fall eine komplette Neuanalyse. Läuft für eine Sitzung schon ein Server
(z. B. aus einem zweiten Terminal gestartet), meldet ein erneuter Aufruf nur dessen URL, statt einen zweiten Server auf
denselben Dateien laufen zu lassen.

```bash
kader check ~/Scans/2026-09      # verfügbare Konverter, vorgeschlagenes Ziel
kader open ~/Scans --target xmp --films 33 34 --converter rawpy --out ~/Export --no-browser
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
| `--tui` | Terminal-Statusanzeige statt nur der URL (siehe unten) | aus |
| `--bind` | Lauschadresse; im Netzwerk erreichbar machen (siehe unten, Sicherheitshinweis beachten) | `127.0.0.1` |

### Ziele

| Ziel | Schreibt | Geradestellen | Automatisch gewählt, wenn |
|------|----------|---------------|---------------------------|
| `copies` | zugeschnittene Kopien `OUT/<Rolle>/<Name>` + `crops.json`/`crops.csv` | ja, beim Schneiden | nur JPEG/TIFF/PNG im Ordner |
| `json` | `OUT/crops.json` und `OUT/crops.csv` (Crop normiert und in Pixeln, Winkel, Gruppe) | Winkel in der Datei | RAWs ohne bekannte Sidecars |
| `xmp` | Adobe-Sidecar `<Name>.xmp`: `crs:HasCrop`, `crs:Crop*`, Farblabel `xmp:Label` | nein | eine Adobe-XMP (`crs:`) liegt neben einem RAW |
| `rawtherapee` | Profil `<Datei>.pp3`: `[Crop]`, `[General] ColorLabel` | nein | ein `.pp3` liegt im Ordner |
| `darktable` | über das Lua-Plugin, siehe [Nutzung mit darktable](#nutzung-mit-darktable) | ja | – (nur aus darktable) |
| `reviews` | Kalibrierung, siehe [Kalibrierung](#kalibrierung-und-ground-truth) | Winkel als Referenz | – |

`--target auto` (Standard) schlägt eines vor, entscheidet aber nicht endgültig: die Web-UI zeigt **alle** Ziele
gleichberechtigt mit ausführlicher Erklärung und lässt dich jederzeit wechseln (siehe [Web-UI](#web-ui)). Ohne irgendein
Sidecar im Ordner – der häufigste Fall bei frisch digitalisierten Rollen – schlägt `auto` `json`/`copies` vor, auch wenn
du eigentlich `xmp` oder `rawtherapee` willst; wähle dann in der Web-UI bewusst um.

Die Originale werden nie verändert. Sidecars werden **ergänzt**: vorhandene Einstellungen bleiben stehen, nur Crop und
Farblabel werden gesetzt. Ausgabeordner tragen die Markierung `.autocrop-output` und werden bei der nächsten Suche übersprungen.

`xmp` schreibt nur für proprietäre RAWs. Enthält eine Sitzung auch JPEG/TIFF/PNG/DNG, werden diese von `xmp` nicht
geschrieben – die Web-UI markiert sie mit einer Kachel „nicht geschrieben“ und erklärt es im Crop-Editor, **bevor** du
auf Fertig klickst, nicht erst danach im Ergebnis. Für eine gemischte Rolle bleibt nur: `copies`/`json` wählen (nimmt
jedes Bild), oder das Werkzeug für den betroffenen Unterordner separat mit einem anderen Ziel laufen lassen.

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

### Terminal-Statusanzeige (`--tui`)

Ohne `--tui` gibt `open` die URL einmal aus und der Prozess läuft still im Hintergrund weiter – auf einem NAS oder per
SSH sieht man danach nichts mehr vom Fortschritt, außer man öffnet die Web-UI selbst. `--tui` zeigt stattdessen eine
laufende Statusanzeige direkt im Terminal: Sitzung und Ziel, Zähler (grün/gelb/rot/anwenden), einen Fortschrittsbalken
während Export/Erkennung, danach das Ergebnis nach „Fertig“, und die URL groß in der Mitte. Zwei Tasten:

| Taste | Wirkung |
|-------|---------|
| `O` | versucht, die URL im Browser zu öffnen; ohne lokalen Browser (der NAS-Regelfall) erscheint ein Hinweis, die URL auf einem anderen Gerät zu öffnen |
| `Q` | beendet den Server (wie „Server beenden“ in der Web-UI) |

Kein Ersatz für die Web-UI: Prüfen, Korrigieren und Fertig laufen weiterhin nur dort. `--tui` ist reine Statusanzeige,
läuft im selben Prozess (kein zusätzlicher Netzwerk-Umweg) und braucht das Paket `rich`
(`pip install "kader[tui]"`, bei `--standalone` von `install.sh` mitinstalliert) sowie ein echtes Terminal
(funktioniert über SSH; nicht unter Windows, nicht in einer Pipe/einem Skript). Fehlt eine Voraussetzung, meldet
`--tui` das sofort und klar (`kader check` zeigt den Stand auch ohne `--tui` an). Nur für diesen
eigenständigen Weg – über darktable oder `serve --folder` bleibt es wie bisher ganz ohne Terminal-Oberfläche.

Die Sitzung selbst endet nicht mit `--tui`: Solange der Server läuft (auch während `--tui` aktiv ist), ist die normale
Web-UI unter derselben URL parallel nutzbar – beide arbeiten auf genau derselben Sitzung, Änderungen im Browser
erscheinen mit der nächsten Aktualisierung auch in der TUI. Beendest du die TUI mit `Q`, bleibt die Sitzung auf der
Platte erhalten; ein erneutes `kader ORDNER` (auch ohne `--tui`) setzt sie fort und öffnet die Web-UI wie
gewohnt.

### Im Netzwerk erreichbar (`--bind`)

Die URL aus `--tui` (und auch ohne sie) ist standardmäßig nur auf derselben Maschine erreichbar – der Server bindet an
`127.0.0.1`. Sitzt du per SSH auf einem NAS, hilft das allein nicht: ein Browser auf deinem eigenen Rechner kommt ohne
Weiteres nicht an `127.0.0.1` des NAS heran. `--bind ADRESSE` öffnet den Server fürs Netzwerk:

```bash
kader open ~/Scans/Film-12 --tui --bind 0.0.0.0    # auf allen Netzwerkschnittstellen lauschen
kader open ~/Scans/Film-12 --tui --bind 192.168.1.50   # nur auf dieser einen Adresse
kader open ~/Scans/Film-12 --bind :: --port 8080       # IPv6 (URL dann mit [Adresse])
```

Lässt sich die Adresse nicht binden (gehört der Maschine nicht, Port belegt, kein IPv6), endet der Aufruf mit einer
klaren Fehlermeldung. Mit `--port 80` funktioniert auch der Aufruf ohne Portangabe in der URL.
Ist der mit `--port` gewählte Port belegt, nimmt der Server den nächsten freien (bis zu 20 weiter, sonst einen
beliebigen) und meldet das; die angezeigte URL stimmt immer.

Danach zeigt `--tui` (bzw. die normale Ausgabe ohne `--tui`) die URL mit einer im Netzwerk erreichbaren Adresse statt
`127.0.0.1` – bei `--bind 0.0.0.0` wird sie erraten (`guess_lan_ip()`): eine echte, physische Netzwerkschnittstelle mit
privater LAN-Adresse wird bevorzugt, Docker-/Brücken-/VPN-Interfaces (`docker0`, `br-…`, `veth…`, `tun…` usw.) und
deren üblicher Adressbereich (172.16–31.0.0/12, Dockers Standard-Spielwiese) werden bewusst nach hinten gestellt, nicht
ausgeschlossen – auf einer reinen Docker-Netzwerkumgebung ohne echtes LAN ist eine wahrscheinlich falsche Adresse
besser als gar keine. Stimmt die geratene Adresse dennoch nicht mit der, unter der du die Maschine tatsächlich
erreichst, überein, ersetze sie in der URL von Hand.

**Sicherheit:** Der **Token** in der URL (`?t=...`) ist ab dann die einzige Zugriffskontrolle – er wird bei **jedem
Start neu ausgewürfelt** (`secrets.token_urlsafe`, 128 Bit Zufall) und nirgends gespeichert außer in der angezeigten
URL. Ohne `--bind` (Standard `127.0.0.1`) prüft der Server zusätzlich, dass die Anfrage tatsächlich an `127.0.0.1`
oder `localhost` gerichtet war (Schutz gegen DNS-Rebinding durch eine fremde Webseite im selben Browser); mit
`--bind` entfällt diese enge Prüfung zwangsläufig (der Hostname variiert je nach Netzwerkschnittstelle), es bleibt nur
noch die Portprüfung. `--tui` zeigt deshalb dauerhaft eine rote Warnzeile, solange nicht auf `127.0.0.1` gebunden ist
(auch ohne `--tui` erscheint die Warnung einmal beim Start). Folgen:
- Nicht in unsicheren oder fremden Netzen (offenes WLAN, Firmennetz mit anderen Nutzern) binden.
- Die URL (mit Token) ist ein Geheimnis wie ein Passwort – nicht in Chatverläufe, Tickets o. Ä. kopieren.
- Abgewiesene Anfragen (falscher Token, fremder Host) zählen nicht als Aktivität: ein Scanner im Netz verhindert das
  automatische Beenden nach 30 Minuten Leerlauf nicht.
- Nur für den eigenständigen `open`-Weg; `serve --job`/`serve --folder` (darktable, Kalibrierung) binden weiterhin
  ausschließlich an `127.0.0.1`, das Flag existiert dort nicht.

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
Unter **Voreinstellungen → Lua → Kader**:

| Einstellung | Beschreibung | Standard |
|-------------|-------------|----------|
| Pfad zum Python-Skript | Pfad zu `kader.py` | `kader.py` |
| Konfidenz-Schwelle | Schwelle für Markierung (0.0–1.0) | `0.7` |
| Filmformat | 35mm, 6x6, 6x4.5, 6x7, 6x9 | `35mm` |

## Web-UI

Gilt für alle Ziele. Kacheln nach 🟢 Grün / 🟡 Gelb / 🔴 Rot, Umsortieren per Drag-and-drop, Crop-Editor mit Ziehgriffen
und Kandidaten, „Größe auf Rolle“, „Auswahl neu erkennen“ mit anderen Einstellungen, Akzeptieren (`A`) und Überspringen (`S`).
Kopf und Ablaufleiste zeigen das aktive Ziel. Der Server beendet sich nach 30 Minuten ohne Aktivität; die Sitzung bleibt
erhalten (ohne darktable: denselben Befehl erneut starten).

**Ziel-Auswahl (eigenständiger Modus):** direkt unter dem Kopf zeigt ein Panel „Ziel: was passiert bei „Fertig“?“ alle
eigenständigen Ziele als Karten – jede mit Kurzbeschreibung, ausführlichem Absatz, was sie tut und wo Dateien landen,
Aufzählung ihrer Grenzen (Geradestellen, Ungetestetes, RAW-only, …) und, falls zutreffend, wie viele Bilder dieser
Sitzung sie nicht schreiben würde und warum. Ein Vorschlag ist markiert, die aktuell gewählte Karte ebenfalls. Ein Klick
auf eine andere Karte wechselt sofort (solange die Sitzung nicht gesperrt ist); war die Sitzung vorher schon einmal mit
einem anderen Ziel fertig, weist ein Hinweis darauf hin, dass dessen Dateien liegen bleiben.

Sitzungen liegen in `~/.cache/kader/` und werden nach 14 Tagen aufgeräumt
(`kader cleanup`). Details und Entwurf: [`companion-ui-plan.md`](companion-ui-plan.md).

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
python3 kader.py --debug -o /tmp/debug_crop.jpg '/path/to/ablichtung.jpg'   # Erkennung allein, JSON-Ausgabe
.venv/bin/python -m unittest discover -s tests                                             # Unit-/Integrationstests
```

Die JSON-Ausgabe enthält x, y, width, height, confidence, needs_review. Browser-Test: `tests/ui_smoke.py` (benötigt
Playwright und `Testphotos/`). `tests/test_standalone.py` deckt Ziele, Konverter-Wahl, Bildsuche und CLI ab; der
rawpy-Test erzeugt dafür eine DNG (benötigt `rawpy` und `tifffile`, sonst übersprungen). `tests/test_tui.py` prüft die
Statusanzeige, darunter ein echter Durchlauf in einem Pseudo-Terminal (`pty`): startet `--tui`, drückt `Q`, prüft das
saubere Ende (benötigt `rich`, sonst übersprungen).

## Dateien

| Datei | Beschreibung |
|-------|-------------|
| `kader.py` | Erkennung: Bildanalyse und Crop-Erkennung (Kern) |
| `film_scale.py` | Maßstab einer Rolle aus der Perforation |
| `companion/` | Companion: lokaler Server, Sitzungen, Web-Oberfläche (`static/`), CLI (`__main__.py`) |
| `companion/converters.py` | RAW-Konverter (Eingabe-Adapter): darktable, RawTherapee, rawpy |
| `companion/targets/` | Ziele (Ausgabe-Adapter): darktable, reviews, json, copies, xmp, rawtherapee |
| `companion/tui.py` | Terminal-Statusanzeige (`--tui`), nur eigenständige Nutzung |
| `kader.lua` | darktable-Integration (Lua-Plugin) |
| `pyproject.toml` | Python-Paket, Befehl `kader` |
| `tests/` | Unit-/Integrationstests, Lua-Stub, Browser-Smoke-Test |
| `install.sh` | Installation (`--standalone`: ohne darktable) |
| `roadmap-standalone.md` | Roadmap zur Unabhängigkeit von darktable, Stand und Namensvorschläge |
| `scan-howto.md` / `scan-howto.en.md` | Wie man Negative scannt, damit die Erkennung gut funktioniert (deutsch/englisch) |

## Name

Das Projekt heißt „Kader“; Paket, Befehl, Cache-/Konfigurationsordner, darktable-Plugin und Repository
(`git.arianw.de/shrippen/kader`) tragen diesen Namen. Begründung der Wahl:
[`roadmap-standalone.md`, Phase 4](roadmap-standalone.md#namensvorschläge).

## License

[MIT](LICENSE)
