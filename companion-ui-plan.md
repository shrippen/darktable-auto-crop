# Plan: Companion-UI (Review vor Übergabe an darktable)

Stand: Entwurf. Ergänzt `roadmap.md` (Trefferrate/Kalibrierung); die UI liefert
dafür nebenbei die Ground-Truth-Daten (Phase 0/5).

## 1. Ziel und Nicht-Ziele

**Ziel:** Zwischen "Analyse" und "Crop anwenden" eine Prüfstation einschieben.
Der Nutzer sieht alle Kandidaten mit Vorschau, sortiert nach Grün/Gelb/Rot,
korrigiert per Drag-and-drop und Crop-Editor, erkennt einzelne Bilder mit
anderen Einstellungen neu und übergibt am Ende den bestätigten Plan an darktable.

**Nicht-Ziele:**
- Kein Fork von darktable, keine Änderung von darktable selbst.
- Kein Ersatz für darktables Crop-Modul (Feinarbeit bleibt dort).
- Kein Cloud-Dienst: alles läuft lokal auf `127.0.0.1`.
- Crop-Anwendung bleibt in darktables eigenen APIs (`dt.styles`), keine
  XMP-Edits von außen.

## 2. Architektur

```
darktable (Lua)                 Companion (Python, lokal)          Browser
────────────────                ─────────────────────────          ───────
[Review starten]
  Pfade/IDs der Auswahl ──────▶ Export je Raw via darktable-cli
                                  (volle Auflösung, JPEG, sRGB)
                                Analyse auf dem Export
                                Thumbnails aus dem Export (Pillow)
                                HTTP-Server 127.0.0.1:<port> ────▶ SPA (Prüfen)
                                ◀── Edits, Neu-Erkennung ─────────
                                                                   [Fertig] → gesperrt
                                plan.json (Phase=locked, revision n) ◀┘
[Plan anwenden]
  nur wenn locked:  set_crop() via dt.styles, Farblabels
                    ───────────▶ result.json (revision n) ─▶ UI: "angewendet"

Nicht zufrieden in darktable?
[Prüfung öffnen] ─────────────▶ Server (neu) starten, Sitzung laden ─▶ Browser
                                [Zurück zur Prüfung] → Phase=reviewing, revision n+1
```

- **Alles Raw, Export durch darktable:** Es wird davon ausgegangen, dass im
  Zweifel alle Bilder Raws sind. Python liest **nie** Raws selbst (kein
  rawpy/libraw). Der bestehende Export `export_raws_via_darktable()` in
  `auto_crop_negative.py` wird wiederverwendet: `darktable-cli` mit eigener
  temporärer Config und `write_sidecar_files=never`, also ohne die laufende
  darktable-Instanz zu stören und ohne XMP zu überschreiben. Lua übergibt nur
  Pfade/IDs. Das eine Export-Bild dient für Erkennung, Thumbnails,
  Detailansicht und Neu-Erkennung; die Neu-Erkennung braucht darktable nicht.
- **Exportgröße:** volle Auflösung, weil die Erkennung sie braucht: sie arbeitet
  heute auf einer Kopie mit höchstens `DETECT_MAX_PIXELS` (2,6 MP) und
  verfeinert die Kanten anschließend auf dem Vollbild. Regel: **nie kleiner als
  die Arbeitsauflösung der Erkennung** (mindestens 2,6 MP, sonst würde
  hochskaliert), Standard ist volle Sensorauflösung, JPEG hoher Qualität,
  sRGB. Vorschauen und Thumbnails werden daraus mit Pillow abgeleitet und
  gecacht. Kosten: grob 10–15 MB je 24-MP-Export, also rund 1,5–2 GB je Film
  mit ~150 Bildern im Sitzungs-Cache (siehe Aufräumen in Abschnitt 9).
- **Server:** Python-Stdlib (`http.server` + `ThreadingHTTPServer`), kein
  Framework. Grund: keine neue Abhängigkeit, das Projekt hat bereits Pillow/OpenCV.
  Falls WebSocket/SSE für Fortschritt zu umständlich wird: SSE über einen
  einfachen Streaming-Endpoint (`text/event-stream`), kein WebSocket nötig.
- **Frontend:** Statische Dateien, kein Build-Schritt (Vanilla JS + ES-Module,
  Web Components optional). Drag-and-drop über natives HTML5-DnD oder
  Pointer-Events (letzteres besser für Touch/Tablet und für den Crop-Editor).
- **Lauf-Kontrolle:** Lua startet den Companion als Hintergrundprozess
  (PID-Handling ist in `auto_crop_negative.lua` bereits vorhanden) und öffnet
  die URL per `xdg-open`. Lua blockiert nicht und pollt nicht. Das
  darktable-Modul hat drei Knöpfe: **Review starten**, **Plan anwenden**,
  **Prüfung öffnen** (startet den Server für die letzte Sitzung neu, falls er
  beendet wurde, und öffnet den Browser).
- **Phasen einer Sitzung:** `analyzing` → `reviewing` → `locked` → `applied`
  (bzw. `apply_failed`). `reviewing → locked` gibt es nur durch "Fertig" in der
  Web-UI. Der Sinn von "Fertig" ist ein bewusster Übergabepunkt: Man versteht
  explizit, dass es jetzt in darktable weitergeht. **Es ist umkehrbar:** aus
  `locked`, `applied` und `apply_failed` führt "Zurück zur Prüfung" (nur in
  der Web-UI, nie automatisch) nach `reviewing` und erhöht die `revision`.
  In `locked` lehnt der Server Änderungen mit `409` ab. Der Server bleibt als
  Ansicht am Leben, solange die Sitzung offen ist; wurde er beendet, holt
  ihn "Prüfung öffnen" in darktable zurück.
- **Sicherheit:** Bindung nur an Loopback, zufälliger Token in der URL, der bei
  jeder API-Anfrage geprüft wird (schützt vor fremden Webseiten, die
  `localhost` ansprechen). Kein CORS. Nur Pfade aus dem aktuellen Job sind lesbar.

## 3. Datenmodell (`job.json` → `plan.json`)

Eine Sitzung = ein Verzeichnis `~/.cache/auto-crop-negative/<session-id>/`.

```jsonc
{
  "session": "2026-09-20T14-03-11",
  "settings": { "format": "6x6", "aspect_ratio": null,
                "confidence_threshold": 0.7, "film_border_level": null },
  "images": [
    {
      "id": 4711,                      // darktable-Bild-ID
      "path": "/pfad/20250414_0164.jpg",
      "fingerprint": "…",              // Raw-Pfad + Größe + mtime; Konsistenzprüfung beim Anwenden
      "size": [4000, 3000],
      "detected": { "crop": [l,t,r,b], "confidence": 0.82, "strategy": "…" },
      "candidates": [ { "crop": […], "confidence": 0.82, "strategy": "…" } ],
      "state": "green|yellow|red",     // Gruppe (initial aus Konfidenz)
      "manual": { "crop": […] },       // nur wenn vom Nutzer geändert
      "decision": "accept|skip|null"   // skip = Bild in darktable unverändert
    }
  ]
}
```

- Crop-Koordinaten immer **normalisiert (0–1)**, relativ zum ungedrehten
  Originalbild. So sind Thumbnail, Vollbild und darktable-Crop deckungsgleich.
- Das Bild-Objekt trägt zusätzlich `export`: Pfad des JPEG und dessen Pixelmaße.
  Alle Crops beziehen sich auf den **Export**; die Umrechnung auf darktables
  Crop-Koordinaten macht Lua beim Anwenden (siehe Risiken: Orientierung/Rotation).
- `plan.json` enthält `phase`, `revision`, `locked_at`, eine Prüfsumme über den
  Inhalt (`plan_sha256`) und je Bild `id`, `fingerprint`, endgültigen Crop, Farblabel,
  `decision`. Es wird atomar geschrieben (temporäre Datei + Umbenennen), damit
  Lua nie eine halbe Datei liest. Bei geändertem Fingerprint überspringt Lua
  das Bild und meldet es. (Ein SHA-1 über Raw-Dateien wäre unnötig langsam.)
- Das Format ist mit `review_data/results.json`/`reviews.json` **kompatibel
  ableitbar**, damit die Altdaten importierbar sind und `tools/eval.py`
  weiterläuft.

## 4. Funktionsumfang

### 4.1 Galerie (Stufe 1)
- Drei Spalten/Bänder: **Grün / Gelb / Rot**, Zähler im Kopf, Schwellenwerte
  sichtbar und verschiebbar (Regler ändert nur die Gruppierung, keine Neuberechnung).
- Kachel: Thumbnail mit **eingezeichnetem Crop-Overlay**, Konfidenz als Zahl
  (nicht nur Farbe), Dateiname, Strategie-Kürzel.
- Umschalter pro Kachel: "Original" / "Nach Crop" / "Overlay".
- Filter/Sortierung: nach Konfidenz, nach Film, nach Strategie-Uneinigkeit.
- Tastatur: Pfeile, `A` akzeptieren, `S` überspringen, `E` Editor, `Z` Undo.

### 4.2 Detailansicht und Crop-Editor (Stufe 2)
- Großansicht mit Ziehgriffen (Ecken/Kanten), Seitenverhältnis-Sperre
  (frei / Format / Original), Nachbarn-Filmstreifen zum schnellen Wechseln.
- Alle Kandidaten der Strategien als umschaltbare Overlays, mit "Warum
  unsicher?" (z. B. Strategien uneinig, Rand zu dunkel, Aspekt-Penalty).
- Zwei Undo-Ebenen wie im Tk-GUI: pro Auswahl und pro Sitzung.

### 4.3 Umsortieren per Drag-and-drop (Stufe 2)
- Kachel in eine andere Gruppe ziehen = Nutzerentscheidung. Grün heißt "so
  übernehmen", Rot heißt "in darktable von Hand prüfen" (Label bleibt rot).
- Mehrfachauswahl (Shift/Ctrl, Lasso) und Gruppenaktionen ("alle Gelben
  akzeptieren").
- Jede Verschiebung wird protokolliert: das ist Feedback für die Kalibrierung.

### 4.4 Neu erkennen mit anderen Einstellungen (Stufe 3)
- Einstellungsleiste: Format, Seitenverhältnis, `--confidence-threshold`,
  `--film-border-level`, `--no-aspect-penalty`, `--skip-refine`.
- "Neu erkennen" wirkt nur auf die aktuell **markierte Auswahl**; Ergebnis
  erscheint als Vergleich vorher/nachher, erst "Übernehmen" überschreibt.
- Läuft als Hintergrundjob (Prozesspool), Fortschritt pro Bild per SSE.

### 4.5 Prozess beobachten (ab Stufe 1)
- Fortschrittsanzeige der Analyse, live einlaufende Kacheln.
- Log-Panel (einklappbar) mit Ausgabe von `auto_crop_negative.py --debug`.
- Fehlerkacheln mit Grund, "Erneut versuchen"-Knopf.

### 4.6 Fertig, Plan anwenden, Zurück zur Prüfung (Stufe 4)
Ablauf: **Alles in der Web-UI fertigstellen → "Fertig" → in darktable weitermachen.
Nicht zufrieden → zurück in die Web-UI.** Fertig ist ein bewusster Wechsel des
Werkzeugs, keine Einbahnstraße.

1. **Fertig** (Web-UI, `.btn-accent`): Bestätigung (`.dialog`) mit Zusammenfassung
   (n anwenden, n rot markiert, n übersprungen, Warnungen als `.callout`) und dem
   Hinweis, dass es in darktable weitergeht und man von dort zurückkehren kann.
2. Nach Bestätigung schreibt der Server `plan.json` (`phase: "locked"`,
   `revision: n`) und sperrt das Interface (`.is-locked`): Kacheln, Editor, DnD,
   Einstellungen und Neu-Erkennung sind deaktiviert, Schreibzugriffe der API
   antworten `409`. Oben steht ein `.callout-ok` ("Gesperrt. In darktable auf
   *Plan anwenden* klicken.") mit dem Knopf **Zurück zur Prüfung**.
3. **Plan anwenden** (darktable-Lua-Modul):
   - Nicht gesperrt (Phase `reviewing`, keine oder unvollständige `plan.json`,
     Prüfsumme falsch): tut nichts, zeigt per `dt.print` "Web-UI noch nicht auf
     *Fertig* gestellt". Der Knopf ist immer klickbar.
   - Gesperrt: prüft je Bild den Fingerprint, wendet den Crop über den
     bestehenden `set_crop()`-Pfad (`dt.styles`) an, setzt Farblabels und
     schreibt `result.json` (Revision, pro Bild ok/übersprungen/Fehler, zuletzt
     angewendeter Crop).
   - Dieselbe Revision wird nicht zweimal angewendet.
4. Die UI zeigt das Ergebnis (Tabelle, Fehlerzeilen als `.callout-danger`) und
   bleibt gesperrt (`applied`). Dort steht: "Nicht zufrieden? **Zurück zur
   Prüfung**".
5. **Zurück zur Prüfung** (aus `locked`, `applied`, `apply_failed`): Bestätigung
   (`.dialog`), dann `phase: "reviewing"`, `revision: n+1`, Interface entsperrt,
   alle bisherigen Korrekturen bleiben erhalten. Wer in darktable unzufrieden ist,
   klickt dort **Prüfung öffnen** und landet direkt in der Web-UI.
6. **Erneutes Anwenden nach Rückkehr (Revision n+1):** Der Server berechnet die
   **Differenz zur zuletzt angewendeten Revision**. Lua fasst nur Bilder an, deren
   Crop oder Label sich geändert hat. Damit bleiben Anpassungen, die der Nutzer
   in darktable an anderen Bildern von Hand gemacht hat, unberührt. Betroffene
   Bilder werden im Fertig-Dialog vorab genannt ("12 Bilder werden neu
   zugeschnitten"), weil manuelle darktable-Änderungen an genau diesen Bildern
   überschrieben würden.
7. Wird der Browser vor "Fertig" geschlossen, bleibt die Sitzung auf der Platte
   (`reviewing`); "Prüfung öffnen" startet den Server neu.

## 5. Look & Feel (Designsystem "shrippen landing-page design system")

Quelle: Claude-Design-Projekt `claude.ai/design/p/be2a9fed-46be-4e18-b2ae-4f9b8781f422`
(Gruvbox-abgeleitet, dunkel, ohne JS-Bibliothek und ohne CSS-Framework). Die
Landingpage in `docs/` nutzt es bereits; die Companion-UI soll sich wie die
gleiche Produktfamilie anfühlen.

### 5.1 Was direkt übernommen wird
- **Tokens:** ausschließlich `var(--*)` aus `tokens/variables.css`, keine
  eigenen Hex-Werte (Regel des Systems): Flächen `--bg-void` (Seite),
  `--bg-panel` (Kacheln/Panels), `--bg-hard` (Code, Log), `--bg1`/`--bg2`
  (Ränder); Text `--fg0`–`--fg3`; Akzent `--yellow`, `--accent`.
- **Typografie:** Überschriften Rajdhani 700, groß und uppercase; Labels und
  Zahlen (Konfidenz, Dateinamen, Log) JetBrains Mono, klein, uppercase mit
  Letter-Spacing; Fließtext System-Sans.
- **Form:** Boxen haben nur die **abgeschnittene Ecke oben rechts**
  (`--chamfer`, 16 px), keine Rundungen, Buttons eckig.
- **Dunkel als Standard**, helles Theme ("Leinen") optional über `data-theme="light"`.
  Das lokale Designsystem (`shrippen.github.io`) hat es inzwischen, mit
  Rollen-Tokens (`--field`, `--score`, `--hl`, `--scrim`). Die Bildbühnen
  (Kachelvorschau, Crop-Editor) bleiben in **beiden** Themes dunkel, weil
  Farbbeurteilung auf beigem Grund täuscht.
- **Icons:** inline SVG, 24×24, `stroke="currentColor"`, keine Emojis.
  Logo: `docs/icon.svg` (Nav/Footer) und `docs/icon-mono.svg` (Wasserzeichen).
- **Zweisprachig:** Jeder sichtbare Text existiert als Paar
  `lang="en"`/`lang="de"`, Umschalter `.lang` in der Nav, Wahl in
  `localStorage`; Standard Englisch. Keine einsprachigen Texte.
- **Nav:** `.nav` mit Marke links und Aktionen rechts (`.nav-hl` gelb).
  In der UI ist die Nav dauerhaft sichtbar (das Einblenden nach dem Hero entfällt).

### 5.2 Zuordnung der UI-Elemente zu vorhandenen Komponenten

| UI-Element | Komponente/Klasse des Systems |
| --- | --- |
| Kopfleiste mit Sitzung, Sprachwahl | `.nav`, `.lang` |
| Kennzahlen (n grün/gelb/rot, n bearbeitet) | `.facts` / `.fact` |
| Gruppenbänder Grün/Gelb/Rot | `.feat[data-tier]` (Farbbalken oben) als Muster für Gruppenkopf und Kachel |
| Pipeline-Anzeige Analyse → Prüfen → Übergabe | `.flow` mit `.flow-node.hl` auf dem aktiven Schritt |
| Einstellungen (Format, Schwellen) | `.table`/`.tokens`-Muster für Parameter, Buttons wie unten |
| Hinweise, Fehler, Erfolg | `.callout` (`-warn`, `-danger`, `-ok`) |
| Log-Panel | `.codeblock` (`.c`, `.k`) |
| Tastaturhilfe | `<kbd>` |
| Hilfe, Erklärungen ("Warum unsicher?") | `.faq` (details/summary) |
| Fertig-Knopf | `.btn.btn-accent` (gelb auf dunkler Seite); sekundär `.btn` ghost-Muster |
| Fertig-Bestätigung / Zusammenfassung | `.install-card` (gelbe Box) mit `.install-links` |

### 5.3 Neue Bausteine (abgenickt, im Designsystem umgesetzt)
Das System war auf Landingpages zugeschnitten. Für die App sind sechs
Bausteine ergänzt worden (Repo `/home/arian/Hacking/eigene/shrippen.github.io`,
`css/components.css`, Referenzkarten in `ds-bundle/components/App/`):

| Baustein | Klassen |
| --- | --- |
| Bildkachel | `.tile[data-tier]`, `.tile-img`, `.tile-crop`, `.tile-check`, `.tile-meta`, `.tile-conf`, `.tile-sm`, `.tiles` |
| Crop-Editor | `.toolbar`, `.stage`, `.cropbox`, `.handle[data-h]`, `.cand`, `.cand-tag`, `.readout`, `.strip` |
| Umsortieren | `.band[data-tier]`, `.band-head`, `.is-over`, `.is-dragging`, `.drag-badge` |
| Steuerelemente | `.field`, `.input`, `.select`, `.seg`, `.range` (`--zones`), `.switch` |
| Status | `.pill[data-state]`, `.progress`, `.progress-bar > i`, `.toast` |
| Dialog & Sperre | `.scrim`, `.dialog`, `.dialog-facts`, `.dialog-actions`, `.is-locked`, `[data-lockable]` |

Dazu: Token `--scrim` (hell/dunkel), `button.btn` ohne Extra-Reset und
`.btn-outline` als Zweitknopf auf dunklem Grund (`.btn-ghost` gilt nur in der
gelben Box). Verhalten (Ziehen, Griffe, Sperren) ist Sache der App.
`companion/static/app.css` enthält daher nur noch Layout der Ansichten, keine
neuen Bausteine.

### 5.4 Entscheidungen für die Gruppenfarben (bestätigt)
- **Rot** `--red`, **Gelb** `--yellow`, **Grün** `--aqua` (nicht `--green`; das ist das Grün von `.callout-ok`, bestätigt):
  `--green` (#b8bb26) liegt zu nah an `--yellow` (#fabd2f) und wäre für die
  Unterscheidung zweier Stufen unglücklich; `--aqua` ist im System als
  "Erfolg/bestätigt" definiert.
- Status **nie allein über Farbe**: Farbbalken oben (wie `.feat`) plus Icon
  und Zahlenwert der Konfidenz in Mono.
- **Konflikt Gelb:** Gelb ist im System zugleich Akzent (aktive Zustände,
  primärer Button). Trennung über Form: Status = Balken/Tag an der Kachel,
  Aktion = gefüllter Button. Auswahl und Fokus einer Kachel: Fokusring
  `2px solid var(--yellow)` wie im System; die *Mehrfachauswahl* zusätzlich
  mit `--orange` markieren, damit sie nicht mit dem Gelb-Status verwechselt wird.
- **Bildbühne:** Hinter Vorschau und Editor `--bg0` (#282828, nahezu neutral)
  statt der wärmeren `--bg-panel`, damit der Farbeindruck des Negativs nicht
  verfälscht wird; kein farbiger Rahmen um das Bild selbst.
- **Chamfer und Editor:** `clip-path` schneidet Fokusringe und Overlays an der
  Ecke ab. Kacheln behalten die Ecke; der **Crop-Editor-Canvas bleibt
  rechteckig**, weil Ziehgriffe an allen vier Ecken erreichbar sein müssen.
  Der Fokusring auf Kacheln liegt deshalb innen (`outline-offset` negativ oder
  Innenrahmen).
- Kontrast (Text mindestens 4,5:1, Fokus/Icons 3:1) für die Kombinationen
  `--fg2`/`--fg3` auf `--bg-panel` und `--bg-void` einmal messen; das System
  gibt dazu selbst keine Werte an.

### 5.5 Einbindung technisch
- Die UI läuft lokal, **kein Laden von Fremd-Hosts**: `styles.css` (aus `ds-bundle/`, enthält die App-Bausteine)
  (Tokens + Komponenten), `shrippen.js` und die Fonts (`Rajdhani-500/600/700`,
  `JetBrainsMono-400/500` als `.ttf`) werden in `companion/static/vendor/`
  gelegt (offline, keine Google-Fonts-Anfrage, reproduzierbar).
  Die Landingpage lädt dagegen von `shrippen.github.io/DesignDefault/v1/`;
  die Companion-UI soll eine feste Version pinnen und eine Notiz mit
  Herkunft und Stand (`v1`) im Ordner tragen.
- Die vendorte Datei wird **nicht editiert**; Anpassungen nur in `app.css`.
  `shrippen.js` im `<head>` synchron laden (Sprachumschalter, Copy-Button).
- Kein Framework nötig: passt zur Vorgabe des Systems (schlichtes HTML plus
  benannte Klassen) und zum Plan (Vanilla-JS).

### 5.6 Skizze der Hauptansicht (Desktop)

```
┌ NAV ─ [icon] AUTO CROP NEGATIVE ─── Sitzung 2026-09-20 ── EN|DE ── GITHUB ┐
│ FACTS:  148 bilder │ 112 grün │ 24 gelb │ 12 rot                          │
│ FLOW:   [analyse ✓] → [prüfen ●] → [fertig] → [in darktable anwenden]                             │
├───────────────────────────────────────────────────────────────────────────┤
│ ▍GRÜN (112)         ▍GELB (24)           ▍ROT (12)                        │
│ ┌────┐┌────┐┌────┐  ┌────┐┌────┐         ┌────┐┌────┐                    │
│ │ bild ││    │      │ Kachel: Bild+Overlay, Konfidenz 0.82 (Mono)        │
│ └────┘└────┘└────┘  └────┘└────┘         └────┘└────┘                    │
├──────────────────────────────┬────────────────────────────────────────────┤
│ EINSTELLUNGEN (Tabelle)      │ LOG (.codeblock, einklappbar)              │
└──────────────────────────────┴────────────────────────────────────────────┘
        [ AUSWAHL NEU ERKENNEN ]            [ FERTIG ]  (btn-accent, sperrt die UI)
```
Auf Tablet/Phone (Breakpoints 900/640 px des Systems) stapeln sich die drei
Bänder zu Tabs.

## 6. Repo-Struktur

```
companion/
  server.py          # HTTP-Server, API, SSE
  session.py         # Sitzung, job/plan lesen und schreiben
  thumbs.py          # Thumbnails aus den darktable-Exporten (Cache je fingerprint)
  folder_source.py   # Dev-/Kalibriermodus: Ordner mit JPGs statt darktable-Sitzung
  redetect.py        # Neu-Erkennung als Subprozess/Pool
  static/
    index.html
    app.js           # ES-Module: gallery.js, editor.js, dnd.js, api.js
    vendor/          # shrippen-Designsystem v1: styles.css, shrippen.js, fonts/ (unverändert)
    app.css          # nur App-Bausteine, ausschließlich mit Tokens
    i18n.js          # EN/DE-Textpaare, Umschalter
auto_crop_negative.lua   # + Export, Sitzung starten, Knopf "Plan anwenden", result.json
```
**Entscheidung:** Die Web-UI ersetzt `review_gui.py` langfristig. Bis zur
Parität (Stufe 6) bleibt das Tk-GUI bestehen. Damit der Ersatz vollständig ist,
muss die Web-UI auch **ohne darktable** laufen können: `folder_source.py`
liest wie heute `Testphotos/` und `review_data/` (Kalibrierung, `tools/eval.py`),
die Oberfläche ist dieselbe. Danach entfallen `review_gui.py` und
`start_review_gui.sh` (dessen Logik "Daten neu erzeugen: voll/inkrementell/behalten"
wandert in die Web-UI bzw. ein kleines CLI `python -m companion --folder …`).

## 7. API-Skizze

| Methode | Pfad | Zweck |
| --- | --- | --- |
| GET | `/api/session` | Einstellungen, Bilderliste, Gruppen |
| GET | `/api/thumb/<id>?w=…` | Thumbnail bzw. Export-Ausschnitt (Cache) |
| GET | `/api/events` | SSE: Fortschritt, neue Ergebnisse |
| PATCH | `/api/images` | Crop/Gruppe/Entscheidung für mehrere Bilder als ein Undo-Schritt ändern (`ids` im Body) |
| POST | `/api/redetect` | Neu-Erkennung für IDs mit Settings |
| POST | `/api/undo` | Undo (Auswahl/Sitzung) |
| POST | `/api/finish` | Phase `locked`, `plan.json` atomar schreiben; danach `409` auf alle Schreibzugriffe |
| POST | `/api/reopen` | Zurück zur Prüfung: Phase `reviewing`, `revision + 1`, entsperrt (nur aus `locked`/`applied`/`apply_failed`) |
| GET | `/api/result` | `result.json` (nach Anwenden in darktable) |

## 8. Umsetzung in Stufen

| Stufe | Inhalt | Ergebnis |
| --- | --- | --- |
| 0 | Datenformat, Sitzungsverzeichnis, `results.json`-Import, Designsystem vendoren, Export ohne vorhandenen Crop (Abschnitt 12) | Grundlage |
| 1 | Lua-Export (Raw → JPEG), Server + read-only Galerie mit Gruppen, Overlay, Fortschritt | Sichtbarkeit; schon nützlich |
| 2 | Detailansicht, Crop-Editor, DnD, Undo, Mehrfachauswahl | Korrigieren |
| 3 | Neu erkennen mit Settings, Vorher/Nachher | Iterieren |
| 4 | Fertig/Sperre, Zurück zur Prüfung (Revisionen, Differenz-Anwenden), `plan.json` (atomar, Prüfsumme), Knöpfe "Plan anwenden" und "Prüfung öffnen", `result.json` | Ende-zu-Ende |
| 5 | Feedback in Ground Truth (`reviews.json`) einspeisen, `tools/eval.py` erweitern | Kalibrierung |
| 6 | Parität mit `review_gui.py` (OK/Problem/Reset, Sprung, manueller Referenz-Crop, Ordnermodus), dann `review_gui.py` und `start_review_gui.sh` entfernen | Ersatz |

Stufe 1 und 4 sollten früh durchgängig getestet werden (dünner Durchstich
zuerst), damit die Übergabe an darktable, das riskanteste Stück, nicht am Ende
überrascht.

## 9. Risiken und Gegenmaßnahmen

| Risiko | Gegenmaßnahme |
| --- | --- |
| Lua kann Hintergrundprozess/Browser nicht zuverlässig starten | `io.popen`/`os.execute` mit `&`, PID-Datei, `kill -0`-Prüfung (Muster existiert) |
| Bild ändert sich zwischen Analyse und Anwenden | `fingerprint` im Plan, Lua prüft je Bild und meldet Abweichung |
| Live-Darkroom-Sitzung offen beim Anwenden | Anwendung nur über darktable-API, nie über XMP; Plan erst nach Bestätigung (vgl. Memory zu XMP-Snapshots) |
| Crop-Koordinaten Export ↔ darktable stimmen nicht (Orientierung, vorhandener Crop) | Getestet (Abschnitt 12): Crop-Anteile beziehen sich auf den **gedrehten** Rahmen, also exakt auf den Export. Lua muss die normalisierten Anteile direkt setzen und darf **nicht** mit `image.width/height` multiplizieren (bei gedrehten Raws möglicherweise unrotierte Maße; im Lua-Kontext noch ungeprüft). Ein vorhandener Crop wird beim Export per XMP-Kopie deaktiviert (Abschnitt 12) |
| Erkennung läuft künftig auf Raw-Exporten, nicht auf den `Testphotos/`-JPGs; Trefferrate kann abweichen | Ein Satz Raw-Exporte in `tools/eval.py` aufnehmen und Baseline neu messen (deckt sich mit Roadmap Phase 0/1) |
| Export in voller Auflösung ist langsam und groß | Vorhandener Export mit 4 parallelen `darktable-cli`-Läufen, Kacheln laufen live ein; Wiederverwendung über Cache je `fingerprint`; Speicherbedarf vorab schätzen und im UI zeigen; Sitzungs-Cache nach 14 Tagen oder per Knopf aufräumen. Verkleinern nur, wenn der Export über der Arbeitsauflösung der Erkennung (2,6 MP) bleibt |
| Erneutes Anwenden nach "Zurück zur Prüfung" stapelt Crops oder überschreibt manuelle darktable-Arbeit | Differenz zur zuletzt angewendeten Revision; nur geänderte Bilder anfassen; im Dialog vorab nennen. Auf History-Ebene getestet: ein späterer Crop-Eintrag derselben Instanz ersetzt den früheren (Abschnitt 12). Offen bleibt nur, ob `dt.styles.apply()` in der GUI den Eintrag ebenso erzeugt |
| Server nach "Fertig"/Anwenden beendet, Nutzer will zurück | "Prüfung öffnen" in darktable startet ihn aus dem Sitzungsverzeichnis neu; Zustand liegt komplett auf der Platte |
| Nutzer ändert in darktable Bilder zwischen Export und "Plan anwenden" | Fingerprint-Prüfung; betroffene Bilder werden übersprungen und im Ergebnis rot gelistet |
| Lua-Knopf wird zu früh gedrückt | Ohne `locked` und gültige Prüfsumme geschieht nichts, nur eine Meldung; nach Rückkehr in die Prüfung ist "Plan anwenden" wieder wirkungslos, bis erneut "Fertig" gedrückt wurde |
| Sitzung bleibt hängen (Browser zu, Rechner neu gestartet) | Zustand liegt auf der Platte; "Sitzung fortsetzen"; alte Sitzungen werden nach 14 Tagen aufgeräumt |
| Große Filme (>100 Bilder) träge | Virtualisierte Liste, Thumbnails in 2 Größen (aus dem Export abgeleitet), Analyse im Pool |
| Fremde Webseiten sprechen den lokalen Server an | Loopback + Token + kein CORS |
| Scope wächst | Stufen 1–4 als MVP, alles andere erst danach |

## 10. Teststrategie
- Server/Session/Plan: `pytest` gegen `Testphotos/`, inklusive Fingerprint-Abweichung, 409 nach `locked` und abgebrochenem/halbem `plan.json`.
- Koordinaten: Property-Test, dass normalisierte Crops nach Round-Trip
  (Thumbnail ↔ Original) pixelgenau bleiben.
- Frontend: Playwright-Smoke (Galerie lädt, DnD ändert Gruppe, Editor speichert).
- Anwenden: manueller Test in darktable auf einer Kopie der Bibliothek mit Raws, inklusive "Plan anwenden" vor "Fertig" (muss folgenlos bleiben) doppeltem Anwenden derselben Revision sowie Fertig → Anwenden → Zurück → Ändern → Fertig → Anwenden (nur Differenz betroffen, manuelle darktable-Änderung an einem unberührten Bild bleibt erhalten),
  danach Vergleich `plan.json` ↔ tatsächliche History/Labels.

## 11. Entscheidungen

Entschieden:
- Die Web-UI ersetzt `review_gui.py` langfristig (Stufe 6, Ordnermodus bleibt).
- Übergabe über einen Knopf "Plan anwenden" in darktable, kein Polling. Erst
  "Fertig" in der Web-UI sperrt das Interface und schaltet das Anwenden frei.
- **"Fertig" ist umkehrbar:** "Zurück zur Prüfung" (in der Web-UI, auch aus dem
  Zustand `applied`) entsperrt und erhöht die Revision; erneutes Anwenden
  wirkt nur auf die Differenz. In darktable holt "Prüfung öffnen" die Web-UI
  zurück. Der Sinn von "Fertig" bleibt der bewusste Wechsel nach darktable.
- Thumbnails und Erkennungsbasis sind darktable-Exporte; Annahme: alles Raws.
- **Exportgröße:** so groß, dass die Erkennung gut arbeitet: volle Auflösung
  (mindestens die Arbeitsauflösung von 2,6 MP), JPEG hoher Qualität, sRGB;
  Vorschauen daraus abgeleitet.
- Die sechs neuen Bausteine sind abgenickt und im Designsystem umgesetzt (5.3).

- Gruppenfarben bestätigt: Grün = `--aqua` (wie `.callout-ok`), Auswahl = `--hl` (orange).
- Sitzungs-Cache: 14 Tage, dazu ein Knopf "Aufräumen".
- Spikes durchgeführt (Abschnitt 12).

Noch offen:
1. Prüfen in der darktable-GUI (Lua): erzeugt `dt.styles.apply()` denselben History-Eintrag wie im Test (Instanz 0), und liefert `image.width/height` bei gedrehten Raws die gedrehten oder die gespeicherten Maße?
2. Test mit echten Raws statt des JPEG-Ersatzes (auf dieser Maschine liegt keines).

## 12. Spike-Ergebnisse (2026-09-20, darktable 5.6.1)

Aufbau: `darktable-cli` mit isolierter Config (`XDG_CONFIG_HOME` im Temp-Ordner),
`write_sidecar_files=never`, Kopie eines Testbilds (1333×2000). Die History wurde
als XMP-Kopie geschrieben und explizit übergeben. Die echte Bibliothek und die
echten Sidecars wurden nicht angefasst. Als Ersatz für Raws dienten JPEGs;
die History-Semantik ist dieselbe, ein Raw-spezifischer Test fehlt aber.

| Test | Erwartung | Ergebnis |
| --- | --- | --- |
| Crop A (10–90 %) | 1066×1600 | 1066×1599 (±1 px Rundung) |
| Crop A, danach Crop B, gleiche Instanz (`multi_priority` 0) | nur B: 800×1000 | **799×1000: B ersetzt A, kein Stapeln** |
| Crop A, danach Crop B als Instanz 1 | (Gegenprobe) | 1066×1599: die zweite Instanz wird ignoriert, A bleibt |
| Crop A mit `enabled=0` | volles Bild | 1333×2000 |
| Crop A, danach derselbe Crop mit `enabled=0` | volles Bild | 1333×2000 |
| Bild mit EXIF-Orientierung 6 (gespeichert 2000×1333, angezeigt 1333×2000), Crop 0,1/0,1/0,9/0,5 | angezeigter Rahmen: 1066×800, gespeicherter: 1600×533 | **1066×800: Anteile beziehen sich auf den gedrehten Rahmen** |

Folgerungen:
1. **Erneutes Anwenden ist unkritisch:** Der Lua-Style nutzt `multi_priority` 0
   (dieselbe Instanz), ein neuer Crop ersetzt den alten. Die Differenz-Logik
   (4.6, Punkt 6) bleibt trotzdem richtig, weil sie manuelle Arbeit an
   anderen Bildern schützt. Ein Style mit `multi_priority` 1 würde dagegen
   still ignoriert; der Code darf das nie erzeugen.
2. **Export ohne vorhandenen Crop:** `darktable-cli` liest laut bestehendem Code die XMP neben dem
   Raw (hier nur mit explizit übergebener XMP getestet, dort wirkt der Crop wie erwartet) und exportiert damit einen bereits gecroppten Frame. Lösung: die XMP
   in den Sitzungsordner kopieren, alle `operation="crop"`-Einträge auf
   `enabled="0"` setzen und die Kopie als zweites Argument übergeben
   (`darktable-cli raw kopie.xmp out.jpg`). Das Original bleibt unberührt.
   Das gilt auch für das ältere Modul `clipping` (Crop und Drehen), falls
   eine XMP es enthält. Fehlt die XMP, ändert sich nichts.
3. **Koordinaten:** Erkennung arbeitet auf dem Export (gedrehter Rahmen). Die
   normalisierten Anteile aus dem Plan sind deshalb direkt die Crop-Anteile
   von darktable. Kein Umrechnen über `image.width/height`.

## 13. Umsetzungsstand (2026-09-20)

Umgesetzt und getestet (34 Unit-/Integrationstests, dazu ein Browser-Test):

| Stufe | Stand |
| --- | --- |
| 0 | Sitzungsformat, atomare Dateien, Ordner-Import (`results.json`/`reviews.json`), Designsystem vendort (`static/vendor/`), Export ohne vorhandenen Crop (`companion/export.py`, mit darktable-cli getestet) |
| 1 | Server (`companion/server.py`), Export + Analyse im Hintergrund, Galerie mit Gruppen, Overlay, Fortschritt (SSE), Log |
| 2 | Detailansicht, Crop-Editor (Griffe, Seitenverhältnis-Sperre, Kandidaten, Filmstreifen), Drag-and-drop, Mehrfachauswahl, zwei Undo-Ebenen, Tastenkürzel |
| 3 | Neu-Erkennung für die Auswahl mit eigenen Parametern; Ergebnis als Vorschlag, erst „Übernehmen“ überschreibt |
| 4 | Fertig/Sperre (409), `plan.json` atomar mit Prüfsumme, „Zurück zur Prüfung“ (Revisionen), Differenz-Anwenden, Lua-Knöpfe „Review starten“, „Plan anwenden“, „Prüfung öffnen“, `result.json` |
| 5 | Jede Korrektur landet in `feedback.jsonl`; im Ordnermodus schreibt „Fertig“ `reviews.json` |
| 6 | Ordnermodus und Funktionsparität weitgehend da; `review_gui.py` bleibt bis zur praktischen Erprobung bestehen (nicht gelöscht) |

Abweichungen vom Entwurf:
- **Fingerprint:** Lua kann keine mtime lesen. Python prüft Größe und mtime beim „Fertig“, Lua prüft zusätzlich Pfad und Dateigröße.
- **Prüfsumme:** `plan.json` trägt `plan_sha256`; Lua prüft stattdessen `complete`/`phase` (atomares Schreiben schließt halbe Dateien aus), da es kein SHA-256 gibt.
- **Export in Python:** Die Sitzung exportiert selbst per `darktable-cli` (bestehende Isolation, kein Schreiben der XMP); Lua übergibt nur Pfade.
- **Ordnermodus:** „Fertig“ gilt dort sofort als angewendet (kein darktable dahinter).
- **Lua-Statuszeile:** Der bisherige Zugriff `lt_widget.children[10]` traf in Wahrheit den Undo-Knopf; die Statuszeile ist jetzt ein benanntes Widget.
- Neu im Plan: `was_applied` je Plan-Eintrag, damit ein nach der Rückkehr nicht mehr gewollter Crop wieder ausgeschaltet wird.

Praxistest 2026-09-20 (36 Raw-Fotos, echte darktable-Bibliothek): Import, Analyse, Korrigieren,
Übergabe von Crops **und** Farblabels und erneutes Korrigieren (Revision 2, Differenz-Anwenden)
funktionieren. Damit bestätigt: `dt.styles.apply()` in der GUI, „Plan anwenden“, Rückkehr in die Prüfung.

Weiterhin nicht gezielt geprüft:
1. Was `image.width/height` bei gedrehten Raws liefert (das Anwenden nutzt sie nicht mehr).
2. Ob das `exit`-Ereignis beim Schließen von darktable den Server stoppt (PID-Überwachung ist das zweite Netz).
3. Der Knopf „Crop & Farben zurücksetzen“ und „Server stoppen“ in der GUI.
4. Der Lua-Test läuft gegen einen Stub der darktable-API, nicht gegen darktable selbst.

### Nachtrag: Serverlebensdauer und Anzeige in darktable
- Im Plugin-Bereich: Statuszeile als `section_label` (`◐ SERVER STARTET …` / `▶ SERVER LÄUFT` / `■ Server gestoppt`; Label-Widgets in darktables Lua-GUI kennen kein Markup, also keine Farben oder Größen), URL als Knopf, Stop-Knopf, Hinweis auf die Auto-Stopp-Regeln.
- Automatisches Ende: darktable geschlossen (`--watch-pid`, geprüft jede Sekunde), 30 Minuten ohne Aktivität in der Web-UI, SIGTERM (Stop-Knopf), „Server beenden“ in der UI.
- Aktivität = Nutzeraktionen (Klicks, Tasten, Mausbewegung, alle schreibenden Aufrufe; die UI sendet höchstens einmal pro Minute ein Lebenszeichen). Der Ereignisstrom und das serverausgelöste Neuladen zählen nicht. Eine laufende Analyse verhindert das Beenden.
- Fünf Minuten vorher erscheint eine Warnung in der Web-UI; beim Ende zeigt die UI sofort einen Hinweis („Prüfung öffnen“ startet neu).
- Die Statusanzeige in darktable aktualisiert sich bei häufigen Ereignissen (Maus über Bild, Auswahl, Ansichtswechsel), ohne Prozess zu starten (nur /proc-Abfrage).
