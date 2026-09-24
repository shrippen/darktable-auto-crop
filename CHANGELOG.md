# Changelog

## Unreleased

### Korrekturen aus dem Code-Review (Companion-Server und CLI)
- **Laufende Sitzung verlor neue Bilder**: ein zweiter `open`-Aufruf mit gewachsenem Ordner schrieb die neuen Bilder
  in `state.json`, bevor er die Sitzungssperre prüfte; der laufende Server überschrieb sie beim nächsten Speichern.
  Jetzt erst sperren, dann ergänzen (`_find_standalone` ändert nichts, `add_images` erst unter der Sperre).
- **`--bind ::`/IPv6 stürzte beim Start ab** (`gaierror`, der Server war fest IPv4): IPv6-Adressen bekommen jetzt einen
  IPv6-Server, die URL setzt die Adresse in Klammern. Nicht bindbare Adressen (fremd, Port belegt, kein IPv6) enden mit
  klarer Meldung statt Traceback.
- **Abgewiesene Anfragen hielten den Server am Leben**: der Leerlauf-Zeitstempel wurde vor Host- und Token-Prüfung
  gesetzt; bei `--bind` genügte ein Scanner, um das Beenden nach 30 Minuten zu verhindern. Jetzt zählen nur
  angenommene Anfragen.
- **Retry konnte die Sitzung in "analyzing" festsetzen**: lief schon eine Neu-Erkennung, blieb nach dem Busy-Fehler die
  Phase stehen und Fertig blockiert. Jetzt wird Busy vorher geprüft, die Änderung läuft unter der Sitzungssperre
  (`Session.retry`), und bei einem Rennen geht die Phase zurück.
- **`--port 80` gab immer 403**: Browser lassen `:80` im Host-Header weg. `split_host` versteht jetzt fehlende Ports
  und IPv6-Klammern.
- **`guess_lan_ip()`**: stürzte unter Windows ab (`fcntl`-Import außerhalb der Fehlerbehandlung) und zog eine
  VPN-Schnittstelle einer physischen vor, wenn das echte LAN in 172.16.0.0/12 liegt. Eine physische Schnittstelle
  gewinnt jetzt immer; die Route des Betriebssystems wird mit dem Namen ihrer Schnittstelle bewertet (eine Route über
  `docker0` gilt nicht mehr als physisch).

### Im Netzwerk erreichbar machen (`--bind`)
`auto-crop-negative open ORDNER --bind 0.0.0.0` (oder eine konkrete LAN-Adresse) lässt den Server auf mehr als nur
`127.0.0.1` lauschen, damit man z. B. von seinem eigenen Rechner aus auf die Web-UI eines per SSH betriebenen NAS
zugreifen kann, ohne einen SSH-Tunnel aufzusetzen. Der **Token** (bei jedem Start neu ausgewürfelt, `secrets.token_urlsafe`)
bleibt dabei die Zugriffskontrolle; die engere Host-Header-Prüfung (Schutz gegen DNS-Rebinding), die nur bei
`127.0.0.1`/`localhost` greift, entfällt zwangsläufig, sobald die Adresse variieren kann (`App.loopback_only`,
`companion/server.py`). `--tui` und die normale Ausgabe zeigen dann dauerhaft eine deutliche Warnung samt Adresse; bei
`--bind 0.0.0.0` wird die angezeigte URL mit einer echten LAN-Adresse statt `0.0.0.0` gefüllt (`guess_lan_ip()`):
alle Netzwerkschnittstellen mit IPv4-Adresse werden bewertet, physische Interfaces mit privater LAN-Adresse
(192.168.0.0/16, 10.0.0.0/8) gewinnen gegen Docker-/Brücken-/VPN-Interfaces (`docker0`, `br-…`, `veth…`, `tun…`, `wg…`
usw., an Namen erkannt) und deren üblichen Adressbereich (172.16–31.0.0/12); die vom Betriebssystem für eine
ausgehende Verbindung gewählte Route zählt als zusätzlicher Kandidat. Nur für den eigenständigen `open`-Weg.

### Terminal-Statusanzeige für NAS/SSH (`--tui`)
`auto-crop-negative open ORDNER --tui` zeigt eine laufende Statusanzeige im Terminal statt nur der einmal ausgegebenen
URL: Sitzung, Ziel, Zähler (grün/gelb/rot/anwenden), Fortschrittsbalken bei Export/Erkennung, Ergebnis nach „Fertig“,
die URL groß in der Mitte. Taste `O` versucht, den Browser zu öffnen (mit Hinweis, wenn keiner da ist – der NAS-
Regelfall), `Q` beendet den Server. Läuft im selben Prozess wie der HTTP-Server (der wandert dafür in einen
Hintergrund-Thread, `companion/server.py` `serve(..., tui=True)`), liest Sitzung/Fortschritt direkt statt über HTTP.
Neue optionale Abhängigkeit `rich` (Extra `[tui]`, `install.sh --standalone` installiert sie mit); ohne `rich`, ohne
POSIX-Terminal (`termios`/`tty`, kein Windows) oder ohne TTY an stdin/stdout meldet `--tui` das sofort und klar
(`companion/tui.py` `unavailable_reason()`), statt mittendrin zu scheitern. Nur für den eigenständigen `open`-Weg;
`serve --job`/`serve --folder` (darktable, Kalibrierung) bleiben unverändert ohne Terminal-Oberfläche – das Flag
existiert dort argparse-seitig gar nicht erst.

### Ziel-Auswahl in der Web-UI, Lücken aus der Journey-Analyse geschlossen
Eine Durchsicht typischer Abläufe der eigenständigen Nutzung (Erstnutzer, Lightroom-Digitalisierer, mehrtägige Sitzungen,
gemischte Ordner, …) fand sechs konkrete Lücken; alle behoben.
- **Ziel-Panel in der Web-UI**: direkt unter dem Kopf zeigt „Ziel: was passiert bei „Fertig“?“ alle eigenständigen Ziele
  als Karten mit ausführlicher Erklärung (was passiert, wohin geschrieben wird, Geradestellen ja/nein, bekannte Grenzen),
  Vorschlags- und Auswahl-Markierung, und – falls zutreffend – wie viele Bilder dieser Sitzung das Ziel nicht schreiben
  würde und warum. Jederzeit änderbar, solange die Sitzung nicht gesperrt ist. Löst, dass ein automatisch gewähltes Ziel
  (z. B. `json` mangels vorhandener Sidecars) bisher nur im README stand, nicht in der Oberfläche selbst.
- **Bilder, die ein Ziel nicht schreibt, sind jetzt sichtbar, bevor man auf Fertig klickt**: Kachel-Badge „nicht
  geschrieben“ in der Galerie, ausführlicher Hinweis im Crop-Editor. Bisher zählte so ein Bild ganz normal zu „Anwenden“
  und tauchte erst nach Fertig im Ergebnis als übersprungen auf.
- **Ordner wachsen lassen funktioniert jetzt**: `auto-crop-negative ORDNER` erkennt beim erneuten Aufruf, wenn die
  bekannten Bilder einer bestehenden Sitzung eine Teilmenge der jetzt gefundenen sind, und ergänzt nur die neuen
  (Analyse nur für sie, bestehende Entscheidungen bleiben). Bisher erzwang schon ein einziges neues Bild eine komplette
  neue Sitzung mit Neuanalyse aller Bilder.
- **Kein zweiter Server mehr auf derselben Sitzung**: eine Dateisperre (`server.lock`, `flock`) verhindert, dass ein
  zweiter `auto-crop-negative`-Aufruf (oder ein zweites „Prüfung öffnen“) denselben Sitzungsordner gleichzeitig bedient;
  der zweite Aufruf meldet stattdessen die URL des laufenden Servers. Ohne das konnten zwei Prozesse unbemerkt
  gegenseitig Korrekturen überschreiben.
- **`--converter NAME` scheitert jetzt sofort**, wenn das Programm fehlt, statt erst beim Export mitten in der Sitzung.
- **Zielwechsel-Hinweis**: War eine Sitzung schon einmal mit einem anderen Ziel fertig, weist die Web-UI beim Wechsel
  darauf hin, dass dessen Dateien liegen bleiben (kein automatisches Aufräumen).

### Eigenständig nutzbar, darktable optional (`roadmap-standalone.md`, Phase 1–4)
Erkennung und Web-UI waren schon werkzeugneutral; nur Ein- und Ausgang hingen an darktable. Beide Enden sind jetzt
austauschbare Adapter.
- **Neuer Befehl** `auto-crop-negative ORDNER` (`python -m companion ORDNER`): durchsucht den Ordner (Unterordner = Rollen,
  auch RAWs), analysiert, öffnet die Web-UI; **Fertig** wendet den Plan sofort an. Erneuter Start setzt die Sitzung fort
  (`--new` für Neuanalyse). `auto-crop-negative check ORDNER` zeigt Konverter und vorgeschlagenes Ziel.
- **Ziele** (`companion/targets/`): `copies` (zugeschnittene, ggf. geradegestellte Kopien; ICC/EXIF bleiben, 16 Bit bleibt),
  `json` (`crops.json` + `crops.csv`), `xmp` (Adobe-Sidecar für Lightroom/Camera Raw), `rawtherapee` (`.pp3`), dazu die
  bisherigen Wege als `darktable` (unverändert über Lua) und `reviews` (Kalibrierung). Das Ziel wird aus vorhandenen
  Sidecars vorgeschlagen. Differenz-Anwenden gilt für alle: nur Geändertes wird neu geschrieben, zurückgenommene Crops
  werden entfernt. Farblabel rot/gelb/grün gehen an darktable, RawTherapee und Lightroom.
- **RAW-Konverter** (`companion/converters.py`): `darktable` (wie bisher), `rawtherapee` (`rawtherapee-cli`) und `rawpy`
  (LibRaw, kein externes Programm). Gewählt nach den Sidecars neben den RAWs, sonst der erste verfügbare.
- **Web-UI**: Kopf und Ablaufleiste zeigen das Ziel; Fertig-Dialog, Ergebnis (mit Hinweisen je Bild) und Server-Ende-
  Meldung ohne darktable-Bezug, wenn eigenständig. Der Schräglagen-Editor weist darauf hin, wenn ein Ziel nicht drehen kann.
- **Installation ohne darktable**: `pyproject.toml` (`pip install ".[raw]"`) und `./install.sh --standalone`.
- **README** umgebaut: Kern zuerst, darktable als eine Integration. Namensvorschläge in `roadmap-standalone.md`.
- Ausgabeordner tragen `.autocrop-output` und werden von der Bildsuche übersprungen (auch im Kalibriermodus).
- Ungeprüft gegen die echten Programme: RawTherapee (Export und `.pp3`) und Lightroom (Crop-Koordinaten in Sensorlage).

### Die Rolle bestätigt ihren Maßstab selbst (`agree`)
Ob der gemessene Perforations-Takt stimmt, entschied bisher die Höhe des Autokorrelations-Peaks. Sie taugt dafür nicht:
*Film 8* hat Score 0,97 bei 2 % Fehler, *07.07.2014 – Leipzig* Score 0,22 bei 1,3 %. `measure_roll_pitch` teilt die Bilder
einer Rolle jetzt zusätzlich abwechselnd in zwei Hälften und misst beide getrennt; ihr relativer Abstand (`agree`) ist die
Wiederholbarkeit der Messung an genau dieser Rolle. Auf 62 Rollen liegt sie im Median bei **0,13 %**, während der Fehler
gegen die Referenz-Crops 1,03 % beträgt — die Messung ist rund achtmal genauer, als ihr Fehler vermuten lässt. Der Rest ist
nicht Messrauschen, sondern die Crop-Gewohnheit der jeweiligen Digitalisier-Sitzung (Korrelation 0,63 mit dem gemessenen
Bildausschnitt; 36,5 mm gegen 35,8 mm in den zwei Stapeln).
- Der Takt gilt jetzt, wenn die Hälften sich bestätigen (`agree` < 0,002), und gilt nicht, wenn sie sich widersprechen
  (> 0,02) — dazwischen entscheidet weiter der Score. Kosten: ein zweiter Akkumulator je Rolle, kein zusätzliches Bildladen.
- **Rollen ohne bestätigten Maßstab bekommen die Konfidenz halbiert** (`no_scale_conf`). Ihre Größe kommt aus dem Pool der
  übrigen Rollen und kann für eine ganze Rolle gleichmäßig danebenliegen, ohne dass Streuung oder Kantenschärfe das zeigen:
  41,3 % Treffer gegen 92,8 %, und nur 61,5 % ihrer Grünen sind richtig gegen 96,0 %. *Leipzig* allein lieferte 23 grüne
  Ergebnisse, von denen **keines** stimmte — genau das „die Konfidenz lügt“ aus der Roadmap.
- Wirkung auf den 62 darktable-Rollen: Treffer unverändert (1454 → 1453), aber **AUC 0,749 → 0,835**, Anteil richtiger
  Grüner **93,4 % → 96,0 %**, falsche Grüne **80 → 47**. Auf den 209 Handcrops Bit für Bit unverändert (197 Treffer,
  136 Grüne, keine falsch): dort bestätigt jede Rolle ihren Takt, es gibt nichts zu deckeln.
- Wichtiger als die Trefferquote ist die Kalibrierung (Schwellen nie auf denselben Bildern gewählt): bei 95 % Ziel-Präzision
  steigt die Abdeckung von 42,4 % auf **86,0 %**, und **98 % Präzision wird zum ersten Mal erreichbar** (t = 0,76,
  Abdeckung 48,8 %, 2-fach out-of-sample 97,5 %) — vorher bei keiner Schwelle. Erst damit lässt sich „grün“ automatisch anwenden.
- Der Konventions-Lerner der Web-UI (`learned_convention`) nutzt dasselbe Kriterium: eine Fehlmessung mit hohem Peak hätte die
  gelernte mm-Konvention sonst um ihren eigenen Fehler verschoben.
- Messungen und verworfene Ideen dazu: `bericht-erkennung-2026-09-22.md`.

### Crop-Größe aus der Perforation (`film_scale.py`)
Der Lochabstand des 35-mm-Films (4,7625 mm) ist eine Konstante; `film_scale.py` misst ihn über die Autokorrelation der
Randstreifen, aufsummiert über die Bilder einer Rolle. Daraus ergeben sich Maßstab (px/mm) und die Rahmengröße (36,2 × 24,1 mm,
die Konvention wird aus Handcrops der Web-UI gelernt). Rollen mit Takt-Score ≥ 0,30 bekommen ihre Größe daraus statt aus
Rollen-Median und Cross-Film-Pool; bei unklarem Seitenverhältnis entscheidet die rohe Rollengröße oder die Kantenschärfe
zwischen 3:2 und quadratisch. Ohne `film_scale.py` oder ohne Takt bleibt die Erkennung wie zuvor.
- Auf den 62 darktable-Rollen (1662 Referenzen): **62,6 % → 87,4 % Treffer**, Anteil richtiger Grüner 80,4 % → 93,5 %. Auf den
  209 Handcrops 194 → 196. Ganze Rollen kamen zurück (Forst 0/37 → 36/37, Weimar 07.07.2014 0/38 → 35/38, Zwiebelmarkt 0/40 → 30/40).
- **Takt-Messung mit Konsens:** Der Takt kommt nicht mehr aus der einen stärksten Streifenlage, sondern aus dem Peak-Bündel mit
  der größten Score-Summe über alle Streifenlagen. Vorher entschied ein einzelner Fremdpeak am äußersten Bildrand (0,129 statt
  0,118 der Bildbreite) und ergab einen 10 % zu großen Maßstab (Altona Sept 89 und Film 1 auf unentwickelten Bildern: 0 Treffer).
  Auf den entwickelten Bildern unverändert (1452 → 1454 Treffer), auf unentwickelten 1397 → 1453.
- Neu: Button „Größe auf Rolle“ im Crop-Editor überträgt die Größe eines korrigierten Crops auf die übrigen Bilder der Rolle
  (ein Undo-Schritt, keine Feedback-Einträge). `install.sh` kopiert `film_scale.py` mit.
- Nachführung der Rollengröße (`SCALE_CFG["adapt"]`) eingebaut, aber aus (0): 8 px und 16 px brachten keinen Gewinn.
- `tools/convert_testphotos.py --unedited`: Export ohne Negadoctor/Filmic/Belichtung (nicht invertiert, nur Weißabgleich bleibt),
  für Messungen mit unentwickelten Bildern. Nur für Auswertungen, kein Bestandteil der Erkennung.

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
