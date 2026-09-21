# Changelog

## Unreleased

### Konfidenz: Rollen-Verlässlichkeit (`size+edge*exposure*roll-v4`)
Neue Testdaten: 4106 weitere Raw-Fotos wurden mit `tools/convert_testphotos.py` in ungecroppte JPEGs umgewandelt (Crop und
Drehung in der Sidecar nur im Export abgeschaltet, Originale unverändert). In 1662 Sidecars steckt ein früherer darktable-Crop
von dir; er liegt in `review_data/darktable_crops.json` und dient mit `tools/eval_darktable_crops.py` als (ältere, weniger
verlässliche) Referenz für 62 weitere Rollen. Auf diesen Rollen zeigte sich ein Problem, das die 209 Handcrops nicht zeigten:
Die Konfidenz war dort hoch, obwohl **ganze Rollen** falsch lagen (Pass A misst bei 12 von 71 Rollen ein falsches
Seitenverhältnis, Trefferquote dort 21 % gegen 84 %). 18 % der grünen Bilder waren falsch (Präzision 79.7 %).
- Neuer Faktor `roll_factor` (0.06–1), der alle Bilder einer Rolle gemeinsam senkt, wenn (a) die Roh-Größen der Rolle stark
  streuen (`film_trust` < 0.85), (b) der Abgleich mit den anderen Rollen die Größe um mehr als 3 % nach unten ziehen musste
  oder (c) das Seitenverhältnis der Konsens-Box um mehr als 3 % vom gemessenen abweicht. Die Rampen sind bewusst grob; ein
  Sweep aller Stützstellen ändert AUC und Präzision kaum.
- Wirkung auf den darktable-Rollen: AUC 0.74 → 0.85, Präzision der Grünen 79.7 % → 91.6 % (auf den ungesehenen Rollen der
  Prüfhälfte 81.7 % → 95.6 %, AUC 0.77 → 0.91). Auf den 209 Handcrops unverändert: 194 Treffer, Grüne 99.3 % richtig,
  Leave-One-Film-Out-Präzision 96.2 % → 97.9 %.
- Der Crop selbst ändert sich nicht, nur die Einstufung grün/gelb/rot. Die Web-UI zeigt den Faktor unter „Woraus sich die
  Konfidenz ergibt“ und nennt in den Hinweisen den Grund („Rolle unsicher (x0.xx): …“).
- Verworfen nach Messung (Details in `roadmap.md`): statische Rollenmaske, Kantenpaar-/Peak-Schätzung der Rollengröße, 3:2
  für alle Rollen erzwingen, Hypothesen-Auswahl je Rolle, Pool je Rolle statt je Bild, zweistufiges Einpassen, globales
  Schrumpfen der Box.

### Algorithmus: geschärft an allen 209 handgecroppten Testfotos
Referenzen: alle Bilder in `Testphotos/` (9 Filme) wurden im Web-UI von Hand gecroppt (`review_data/reviews.json`).
Baseline vorher/nachher auf denselben 209 Bildern: **186 → 194 Treffer** (|dW|,|dH| < 60 px bei 2000 px langer Kante).
- **Orientierung aus dem Bild:** Die Einzeldetektion lieferte in 12 Fällen die falsche Orientierung (Querformat-Box in einem
  Hochformatbild, dy≈+300, dh≈−600). Auf den Referenzen stimmt die Crop-Orientierung in 208 von 209 Fällen mit der des
  Bildes überein, deshalb bestimmt jetzt die Bildorientierung die Konsens-Box (nur bei fast quadratischen Bildern
  entscheidet weiter die Detektion). Behebt 6 der 8 schwersten Fehlschläge.
- **Engeres lokales Einpassen:** Die Größenabweichung beim Einpassen (`REFINE_SIZE_SLACK`) ist 10 statt 45 px. Die Referenzen
  einer Rolle streuen nur um 3–12 px. Sweep 10/15/30/45: 194/192/191/192 Treffer; keine Rolle wird schlechter.
- **Konfidenz: Belichtungsdeckel zurück** (`size+edge*exposure-v3`). Mit 98 Referenzen hatte er nichts gebracht und war
  entfernt worden; mit 209 Bildern kehrt sich das um. Bei den Produktionsschwellen (grün ≥ 0.5) sinken falsche Grüne von 11
  auf 1 (grün 139 statt 183, Präzision 94.0 % → 99.3 %, AUC 0.813 → 0.848; ohne Film 34 0.802 → 0.871). Der Preis: mehr Bilder
  landen in gelb/rot (70 statt 26 zu prüfen). Leave-One-Film-Out: Präzision 93.9 % → 96.2 %, Abdeckung 79.7 % → 77.8 %.
- `tools/eval.py` rechnet die 3-Wege-Tabelle jetzt mit den Produktionsschwellen (0.5 / 0.3).
- Bekannte Grenze: In Film 34 liegt die Referenz bei 9 Bildern 40–70 px innerhalb der sichtbaren Rahmenkante, die die
  Erkennung trifft (Oberkante); das sind vermutlich bewusst engere Crops und keine Erkennungsfehler. Sie bleiben Fehltreffer.

### Neu: Schräglage
- **Pipeline:** Erkennung → **Tilt-Erkennung** → **korrigierte Erkennung**. Ist das Bild merklich schief (≥ 0.3°), wird es
  geradegestellt und der umgerechnete Crop dort lokal an die Kanten eingepasst; diese „korrigierte Erkennung“ ist der
  Start-Crop, sobald der Tilt angewendet wird.
- **Tilt-Messung sucht nur nahe am Crop:** je Seite werden nur Geraden geprüft, die in Reichweite der Crop-Kante liegen
  (3 % der kurzen Seite), Winkel frei bis ±9°. Filmhalter und andere weiter entfernte Kanten kommen nicht mehr in Frage;
  uneinige Seiten werden verworfen (dann „nicht messbar“). Mediane Abweichung an künstlich gedrehten Bildern 0.03°,
  größter Fehler 0.5° (vorher 0.1° / 2.3°), dafür in ~20 % der künstlich gedrehten Fälle keine Messung.
- **Tilt ist standardmäßig angewendet:** Bilder mit verlässlich gemessenem Tilt (≥ 0.3°, Sicherheit ≥ 0.4) werden in Galerie und
  Editor geradegestellt gezeigt, auch beim Vorladen der zwei Nachbarbilder; der Plan für darktable enthält dann die Drehung.
  Abschalten geht je Bild mit dem Schalter. Manuelle Crops merken sich den Winkel, in dem sie gesetzt wurden, und werden beim
  Umschalten umgerechnet.
- **Schalter „Tilt anwenden“ (Taste T)** in der Editor-Leiste, auch im Ordnermodus: zeigt das Bild mit angewendetem Tilt,
  der Crop wird auf dem geraden Bild gesetzt. Referenzen im Ordnermodus werden im Originalrahmen gespeichert, dazu
  `tilt_deg` und `manual_crop_straight`.
- Die Schräglage des Filmrahmens wird gemessen (Geradenanpassung an den vier Crop-Kanten, Suchbereich ±9°, Ergebnis
  in Grad plus Sicherheit und Winkel je Seite) und in der Web-UI gezeigt: Kachelmarke „Schräg +1.2°“, Abschnitt im
  Editor, Sortierung „Schräglage, größte zuerst“. Auf den 98 Referenzbildern liegt der Betrag bei höchstens 0.53°
  (Median 0.19°); an künstlich gedrehten Bildern (±2 bis 7°) beträgt der mediane Fehler 0.1°.
- **Geradestellen in darktable (optional, je Bild oder für die Auswahl):** setzt das Modul „Drehen und Perspektive“
  (`ashift`, Rotation = Messwert, ohne automatischen Zuschnitt) und danach den Crop auf das gedrehte Bild. Editor und
  Kacheln zeigen dann das geradegestellte Bild; der Winkel ist von Hand änderbar (±10°). Nur im darktable-Modus, der
  Ordnermodus zeigt den Messwert nur an. „Zurücksetzen“ und ein erneuter Plan schalten die Drehung wieder aus.
- Die gemessenen Rahmenkanten werden als Linien über dem Bild im Editor gezeichnet (Schalter „Rahmenlinien“); nach dem
  Geradestellen liegen sie waagerecht bzw. senkrecht und zeigen so, ob die Korrektur passt.
- Plan (`plan.json`) hat pro Bild `angle`, `was_angle`; der Ordner-Speichern-Dialog nennt jetzt `reviews.json`.

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
