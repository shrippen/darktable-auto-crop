# Roadmap: Projekt-Unabhängigkeit von darktable

Stand 2026-09. Diese Roadmap ist von `roadmap.md` (Trefferquote/Konfidenz)
getrennt, weil sie eine andere Frage behandelt: nicht *wie gut* erkannt wird,
sondern *wofür das Projekt gebaut ist*. Auslöser ist die Companion-UI: sie
erkennt, sortiert, editiert und verwaltet Sitzungen bereits komplett ohne
darktable-Import (Modus `"folder"` in `companion/session.py`, siehe
`companion-ui-plan.md`). Nur der letzte Schritt – Crop und Farblabel
*anwenden* – hängt noch an darktables Lua-API und `dt.styles`. Die Idee: das
Projekt als eigenständiges Werkzeug positionieren, das eine Bildmenge nimmt,
Filmrahmen erkennt und Crops liefert – mit darktable als einer von mehreren
optionalen Andockstellen, nicht als Voraussetzung.

## Stand der Umsetzung (2026-09-24)

| Phase | Erledigt | Offen / bewusst zurückgestellt |
| --- | --- | --- |
| 1 Ordner-Unabhängigkeit sichtbar | Befehl `auto-crop-negative ORDNER`, Ausgabe im Ordnermodus (Ziele `json`, `copies`), README-Abschnitt | – |
| 2 Output-Adapter | `companion/targets/` mit gemeinsamer Schnittstelle; darktable dahinter ohne Verhaltensänderung; `json`, `copies`; Farblabel je Ziel | – |
| 3 Input-Adapter | RAW-Konverter `darktable`/`rawtherapee`/`rawpy`; Ziele `xmp` (Lightroom/ACR) und `rawtherapee` (.pp3); automatische Wahl von Ziel und Konverter | Capture One (kein Bedarf belegt); RawTherapee und Lightroom nicht gegen die echten Programme geprüft |
| 4 Eigenständiges Produkt | `pyproject.toml` (Befehl `auto-crop-negative`), `install.sh --standalone`, README umgebaut, Namensvorschläge | Umbenennung selbst (Entscheidung offen); Watch-Ordner-Dienst (zurückgestellt) |

Messung: 26 neue Tests in `tests/test_standalone.py`, darunter ein echter Durchlauf (Erkennung auf `test.jpg`, Kopie) und
ein RAW-Durchlauf mit einer erzeugten DNG über rawpy. Die Web-UI wurde für alle drei Modi (eigenständig, Kalibrierung,
darktable-Job) im Browser durchgeklickt.

## Ist-Zustand vor der Umsetzung: was schon unabhängig war, was nicht

**Bereits darktable-unabhängig:**
- Erkennung (`auto_crop_negative.py`, `film_analysis.py`, `film_scale.py`) –
  reines OpenCV/NumPy auf Bilddateien, kein darktable-Bezug.
- Companion-Server, Review-UI, Sitzungsverwaltung, Crop-Editor,
  Referenz-Test, Feedback-Auswertung (`companion/`, `tools/`) – arbeiten im
  Ordnermodus direkt auf JPEGs/TIFFs, ohne darktable.
- `review_gui.py`/`start_review_gui.sh` – Alt-Werkzeug, ebenfalls ordnerbasiert.

**Noch darktable-spezifisch:**
- **Eingabe (RAW-Export):** `companion/export.py` exportiert nur über
  `darktable-cli`, liest nur darktable-XMP-Sidecars (Crop-Historieneintrag
  deaktivieren, siehe `export.py`-Kopfkommentar).
- **Ausgabe (Anwenden):** Plan-Übergabe an darktable per `plan.json`/
  `result.json`, Anwendung über `dt.styles` und Lua (`auto_crop_negative.lua`).
  Farblabels sind ein darktable-Konzept (rot/gelb/grün).
- **Start/Discovery:** Server wird aus dem darktable-Panel gestartet
  (Lua-Button), die Ordnermodus-CLI (`python -m companion folder ...`)
  existiert, ist aber nicht das beworbene Einstiegsszenario (README, `install.sh`
  sind auf darktable ausgerichtet).
- **Koordinatensystem/Konventionen:** Crop-Geometrie und Schräglage
  (`straight_size`, `crop_to_straight` in `session.py`) sind explizit an
  darktables `ashift`+`crop`-Module angelehnt.

Kurz: Das Kernstück (Erkennung + Review-UI) ist heute schon werkzeugneutral.
Es fehlt eine **generische Export/Import-Schicht** an beiden Enden statt der
festen darktable-Kopplung.

## Leitidee

darktable wird zu **einem Adapter unter mehreren**, nicht zum Fundament:

```
                      ┌─────────────────────────────┐
                      │   Erkennung (Kernlogik)      │
                      │   auto_crop_negative.py      │
                      │   film_analysis / film_scale │
                      └───────────────┬───────────────┘
                                      │ Crop + Konfidenz (JSON)
                      ┌───────────────┴───────────────┐
                      │   Companion (Server + Web-UI)  │
                      │   Sitzungen, Review, Editor    │
                      └───┬───────────────────────┬───┘
              Input-Adapter                 Output-Adapter
    ┌──────────┬──────────┼──────────┐   ┌────────┼──────────┬─────────────┐
    │ Ordner   │ darktable│ RawTherapee│  │ Ordner │ darktable│ RawTherapee │
    │ (JPEG/   │ (Export  │ (Export/   │  │ (XMP/  │ (Style/  │ (.pp3)      │
    │  TIFF)   │  per CLI)│  Sidecar)  │  │  Sidecar)│ dt.styles)│           │
    └──────────┴──────────┴────────────┘  └────────┴──────────┴─────────────┘
```

Die Erkennung bleibt unverändert Kernstück. Companion bleibt die zentrale
Steuerung (Sitzung, Review, Konfidenz-Anzeige). Neu ist, dass Input und
Output über austauschbare Adapter laufen statt über festverdrahteten
darktable-Code in `export.py`/`session.py`/`server.py`.

## Phase 1: Bestehende Ordner-Unabhängigkeit sichtbar machen

Kein Architektur-Umbau, nur Framing und Lücken schließen – macht das
Projekt *heute schon* für Nutzer ohne darktable brauchbar.

- [x] Eigener Einstiegspunkt/Kurzanleitung für den reinen Ordnermodus,
      unabhängig vom darktable-Installationsabschnitt in README.
      **Umgesetzt:** `auto-crop-negative ORDNER` (Kurzform von `open`), `auto-crop-negative check [ORDNER]`;
      erneuter Start mit demselben Ordner setzt die Sitzung fort. `serve --folder` bleibt der Kalibriermodus.
- [x] Prüfen, ob der Ordnermodus mit **bereits entwickelten/exportierten**
      Bildern aus beliebiger Quelle sauber läuft.
      **Geprüft:** JPEG/TIFF/PNG beliebiger Herkunft, EXIF-Orientierung (Anzeige und Kopie im selben Rahmen),
      16-Bit-TIFF (Bittiefe bleibt; Pillow hätte stillschweigend auf 8 Bit gekürzt, deshalb liest der Kopien-Adapter
      mit OpenCV). RAW+JPEG gleichen Namens zählen einmal (als RAW).
- [x] Anwenden im Ordnermodus: einen einfachen Output-Weg ergänzen.
      **Umgesetzt:** Ziele `copies` (zugeschnittene Kopien) und `json` (`crops.json` + `crops.csv`).
- [x] README-Abschnitt "Ohne darktable nutzen" ergänzen.

## Phase 2: Output-Adapter abstrahieren

- [x] Gemeinsame Schnittstelle definieren, die heutige darktable-Lua-Logik dahinter kapseln
      (kein Verhaltenswechsel für bestehende Nutzer).
      **Umgesetzt:** `Target.apply(session, plan) -> {id: {status, message}}` in `companion/targets/`. Die Schnittstelle
      arbeitet auf dem ganzen Plan statt je Bild, weil Adapter wie `json` eine Datei für alle Bilder schreiben.
      darktable ist ein *externes* Ziel (Lua liest `plan.json`, schreibt `result.json`, unverändert); lokale Ziele wendet
      `Session.finish()` sofort an und führt `result.json`/`applied.json` genauso, sodass Differenz-Anwenden nach
      „Zurück zur Prüfung“ für alle Ziele gilt. Der bisherige Kalibriermodus ist jetzt das Ziel `reviews`.
- [x] Adapter **"Sidecar generisch"**. **Umgesetzt als `json`:** eine Datei je Lauf im Ausgabeordner statt einer Datei je
      Bild – leichter weiterzuverarbeiten und ohne fremde Dateien neben den Originalen.
- [x] Adapter **"Direkt zuschneiden"**. **Umgesetzt als `copies`**, inklusive Geradestellen (gleiche Drehung wie die
      Vorschau). ICC-Profil und EXIF bleiben bei 8 Bit erhalten.
- [x] Farblabel-Konzept von darktable lösen. **Umgesetzt:** Die Gruppe geht als `label` in jeden Plan; darktable,
      RawTherapee (`ColorLabel`) und Lightroom (`xmp:Label`) setzen ein Farblabel, `json`/`copies` schreiben die Spalte
      `label`. Eine Ordner-Sortierung nach Gruppe wurde verworfen: rote Bilder werden ohnehin nicht kopiert.

## Phase 3: Input-Adapter für andere RAW-Entwickler

- [x] **RawTherapee**: RAW-Export via `rawtherapee-cli`, Sidecar `.pp3` lesen/schreiben (`[Crop]`, `ColorLabel`).
      **Umgesetzt**, aber **nicht gegen RawTherapee getestet** (nicht in der Testumgebung); auch der Aufruf
      `rawtherapee-cli -d -p <profil> -o <datei> -j95 -Y -c <raw>` ist ungeprüft.
- [x] **Lightroom / generisches XMP**: Ziel `xmp` schreibt `crs:HasCrop`/`crs:Crop*`/`xmp:Label` in `<Name>.xmp` und lässt
      vorhandene Werte, Präfixe und das xpacket stehen. Annahme: Adobe speichert den Crop in Sensorlage; die Orientierung
      kommt aus dem RAW-Kopf (TIFF-basierte RAWs) oder über rawpy. **Nicht gegen Lightroom geprüft.** Kein Winkel.
- [x] Zusätzlich (nicht in der ursprünglichen Liste): **RAW ohne externes Programm** über `rawpy` (LibRaw). Damit läuft
      das Werkzeug mit RAWs, ohne dass darktable oder RawTherapee installiert sind. Getestet mit einer erzeugten DNG.
- [ ] **Capture One**: **zurückgestellt.** Eigenes Sidecar-Format (`.cos` in `CaptureOne/Settings*`), ohne Nutzer mit
      Bedarf nicht zu rechtfertigen – wie in der Roadmap vorgesehen.
- [x] Adapter-Erkennung: **Umgesetzt:** Ziel aus dem Ordnerinhalt (`.pp3` → `rawtherapee`, Adobe-XMP → `xmp`, sonst RAWs →
      `json`, nur JPEG/TIFF → `copies`); Konverter nach den Sidecars neben den RAWs (darktable-XMP oder `.pp3`), sonst der
      erste verfügbare. `auto-crop-negative check ORDNER` zeigt beide Vorschläge.

## Phase 4: Companion als eigenständiges Produkt

- [x] Namensfrage klären. **Vorschläge unten**; entschieden ist noch nichts, umbenannt wurde nichts außer dem
      Untertitel „– Darktable-Plugin“ im README.
- [x] README/Architektur-Doku umstrukturieren: Kernstück zuerst, darktable als Integration gleichrangig neben den
      anderen Zielen. `auto_crop_negative.lua` bleibt unverändert.
- [x] Installationsweg ohne darktable: `pyproject.toml` (Befehl `auto-crop-negative`, Extra `[raw]` für rawpy) und
      `./install.sh --standalone` (eigene venv unter `~/.local/share/auto-crop-negative/`, Link nach `~/.local/bin`).
- [ ] Companion-Server dauerhaft im Hintergrund / Watch-Ordner: **zurückgestellt.** Die Roadmap macht das von
      Nutzungsdaten des Ordner-Szenarios abhängig; die gibt es noch nicht. Ein erneuter Start mit demselben Ordner setzt die
      Sitzung fort, das deckt den häufigsten Fall („weiter prüfen“) ab.

### Namensvorschläge

Ausgangslage: „Auto Crop Negative“ selbst ist bereits werkzeugneutral und steckt in Paketname, Befehl
(`auto-crop-negative`), Cache (`~/.cache/auto-crop-negative`), Konfiguration (`~/.config/auto-crop-negative`), Web-UI und
Script-Manager-Eintrag. Abhängig von darktable ist nur der Repository-Name `darktable-auto-crop`.

| Vorschlag | Für | Gegen |
| --- | --- | --- |
| **Auto Crop Negative** behalten, Repo → `auto-crop-negative` (**Empfehlung**) | keine Migration von Cache, Konfiguration, gelernten Konventionen oder darktable-Installationen; Name sagt, was es tut | beschreibend statt einprägsam |
| **negcrop** | kurz, gut als Befehl | Abkürzung; „neg“ allein ist mehrdeutig |
| **Framefinder** / **Rahmenfinder** | beschreibt den Kern (Rahmen finden), nicht nur das Schneiden | generisch, Namenskollisionen wahrscheinlich |
| **Filmframe** | kurz, international | sagt nicht, dass geschnitten wird |
| **Negative Frame** | eindeutig Film-bezogen | lang, als Befehl unhandlich |

Empfehlung: Namen behalten und nur das Repository umbenennen (GitHub leitet alte URLs weiter). Vor einer Wahl mit neuem
Namen prüfen, ob der Name auf PyPI und GitHub frei ist; ein neuer Name hieße auch Cache- und Konfigurationsordner
umzuziehen (gelernte Crop-Konvention in `~/.config/auto-crop-negative/convention.json`).

## Was explizit nicht angetastet wird

- Die Erkennungslogik selbst (`auto_crop_negative.py` und die laufende
  Kalibrierarbeit aus `roadmap.md`) ändert sich durch diese Roadmap nicht.
- Der darktable-Adapter wird nicht schlechter oder umständlicher – er ist
  weiterhin der am besten getestete, "Erstklassige" Weg; die anderen sind
  Ergänzung, kein Ersatz.
- Keine Big-Bang-Umstellung: jede Phase liefert für sich nutzbaren Fortschritt
  und lässt sich unabhängig stoppen, falls kein Bedarf an weiteren
  Adaptern entsteht.

## Priorisierung

Phase 1 zuerst: kostet fast nichts (Dokumentation + kleine Lücken), macht
sofort sichtbar, dass der Ordnermodus real nutzbar ist, und ist der Test,
ob die Idee überhaupt Nachfrage hat, bevor in Adapter-Architektur (Phase 2)
investiert wird. Phase 3/4 nur bei konkretem Bedarf (eigene Nutzung mit
einem anderen RAW-Entwickler, oder Nutzer, die danach fragen) – sonst bleibt
es spekulative Arbeit an Werkzeugen, die niemand nutzt.

Nachtrag 2026-09-24: Auf Wunsch wurden Phase 1 bis 4 zusammen umgesetzt, nicht nacheinander mit Nachfragetest. Der Test
bleibt trotzdem sinnvoll: Die ungeprüften Teile (RawTherapee, Lightroom) sollten erst mit einem echten Nutzer dieses
Werkzeugs geprüft werden, bevor sie als verlässlich gelten.

## Nachtrag: User-Journey-Analyse und Behebung (2026-09-24)

Nach Phase 1–4 wurden konkrete Nutzungsabläufe (Journeys) durchgespielt statt nur die Ziele/Konverter einzeln zu prüfen.
Das deckte sechs Lücken auf, die einzeln funktionierende Bausteine im Zusammenspiel unbrauchbar machten. Alle behoben,
siehe `CHANGELOG.md`:

1. **Zielwahl war "richtig, aber unsichtbar"** (traf v. a. die Lightroom-Journey): `targets.suggest()` schlägt ohne
   vorhandene Sidecars `json`/`copies` vor, obwohl gerade der Ordner ohne Sidecars der Normalfall bei frisch
   digitalisierten Rollen ist. Der Vorschlag stand nur im README, nicht in der Oberfläche.
   **Behoben:** Ziel-Panel in der Web-UI zeigt alle Optionen gleichberechtigt mit ausführlicher Erklärung, jederzeit
   wechselbar (`Session.set_target`, `POST /api/target`, `companion/static/app.js` `renderTargetPanel`).
2. **Gemischte Ordner (RAW + JPEG) mit Ziel `xmp`**: JPEGs wurden lautlos übersprungen, die Oberfläche zeigte das erst
   nach Fertig im Ergebnis.
   **Behoben:** `Target.compatible()` je Ziel, in `will_apply()`/`public_image()` eingebaut (`target_ok`/`target_reason`),
   Badge in der Galerie und Hinweis im Crop-Editor **vor** Fertig, dazu die Zählung "N von M Bildern werden nicht
   geschrieben" direkt an der jeweiligen Ziel-Karte (`targets.options_for`).
3. **Mehrtägige Sitzungen (Ordner wächst)**: ein einziges neues Bild ließ `_latest_standalone` (jetzt
   `_resume_standalone`) nicht mehr matchen → komplette Neuanalyse aller Bilder, alte Entscheidungen verloren.
   **Behoben:** Teilmengen-Vergleich statt Gleichheit, `Session.add_images()` ergänzt nur die neuen Bilder und setzt die
   Sitzung zurück auf "analyzing", ohne bestehende Bilder anzutasten.
4. **Zwei Server auf derselben Sitzung** (Doppelstart, vergessenes Terminal): unbemerkter Datenverlust durch zwei
   unabhängige In-Memory-Zustände auf demselben Sitzungsordner.
   **Behoben:** `companion/server.py` `acquire_lock()` (`flock`, prozessgebunden, kein Aufräum-Code nötig); ein zweiter
   Aufruf meldet die URL des laufenden Servers statt selbst zu bedienen. Schützt nebenbei auch "Prüfung öffnen" in
   darktable, falls zweimal geklickt.
5. **`--converter NAME` explizit, aber nicht installiert**: scheiterte bisher erst beim Export, mit einer Fehlermeldung
   pro Bild, nicht vorab auf der Kommandozeile.
   **Behoben:** Verfügbarkeitsprüfung in `_open()` gilt jetzt unabhängig davon, ob der Konverter automatisch oder
   explizit gewählt wurde.
6. **Zielwechsel im selben Ausgabeordner**: Dateien des vorherigen Ziels blieben kommentarlos liegen.
   **Entschärft, nicht automatisiert:** Automatisches Löschen fremder Dateien wäre selbst riskant; stattdessen weist die
   Web-UI beim Wechsel auf ein zuvor angewendetes anderes Ziel hin (`applied_target` in `public_state()`).

**Bewusst nicht angegangen:** Ziel-Wahl bleibt pro Sitzung global, nicht pro Bild/Unterordner – ein Sammelordner mit
Rollen unterschiedlicher Herkunft (Journey 7) braucht weiterhin mehrere Aufrufe auf Unterordnern, wenn die Rollen
verschiedene Ziele brauchen. Das wird jetzt aber vor Fertig sichtbar gemacht statt stillschweigend falsch zu laufen.

## Nachtrag: Terminal-Statusanzeige für NAS/SSH-Nutzung (2026-09-24)

Journey 5 („reiner Kommandozeilen-Nutzer ohne GUI-Programme, NAS/Server") war zwar nicht *kaputt*, aber dünn: nach dem
Start gab `open` nur einmal die URL aus, danach lief der Prozess still weiter – ohne die Web-UI im Browser (der
Normalfall bei SSH auf ein NAS) sah man nichts vom Fortschritt.

- **Umgesetzt:** `--tui` (nur beim eigenständigen `open`-Weg, siehe README-Abschnitt „Terminal-Statusanzeige").
  Läuft im selben Prozess wie der Server (kein HTTP-Umweg), zeigt Sitzung/Ziel/Zähler/Fortschritt/Ergebnis/URL, zwei
  Tasten (`O` Browser, `Q` beenden). Optionale Abhängigkeit `rich` (Extra `[tui]`), sonst kein neuer Bedarf.
- **Entscheidung dokumentiert** (siehe Gespräch): `rich`/`textual` statt reinem `curses`, weil deutlich weniger
  eigener Layout-Code für den Preis einer zusätzlichen, gut gepflegten reinen Python-Abhängigkeit – das Projekt hat mit
  OpenCV/NumPy/Pillow/rawpy für die eigenständige Nutzung ohnehin schon nicht-stdlib-Abhängigkeiten, „nur Stdlib" gilt
  explizit nur für `companion/server.py` selbst. Start nur mit explizitem `--tui`, kein automatisches Erkennen eines
  Terminals, um bestehende Skripte/Automatisierung nicht durch ein plötzliches Vollbild-UI zu überraschen.
- **Getestet:** `tests/test_tui.py`, darunter ein echter Durchlauf in einem Pseudo-Terminal (stdlib `pty`) – Prozess
  starten, Ausgabe bis zum ersten Frame abwarten, `Q` senden, sauberes Ende samt Abbau der Terminal-Sperre prüfen. Dabei
  zwei echte Bugs gefunden und behoben: `Live.update()` aktualisiert ohne `refresh=True` gar nichts (nur der
  Konstruktor-Frame wäre je sichtbar geworden), und lange Ausgabepfade brachen die Kopfzeile hässlich um (jetzt
  `no_wrap`/`overflow="ellipsis"`).

## Nachtrag: Server im Netzwerk erreichbar machen, `--bind` (2026-09-24)

Direkte Nachfrage: TUI in der SSH-Sitzung auf dem NAS starten, dann die Web-UI vom eigenen Rechner aus weiterbenutzen.
Geprüft (siehe Gespräch): Solange der Server läuft, ist die Web-UI ohnehin parallel zur TUI nutzbar (beide lesen/schreiben
dieselbe `Session` im selben Prozess) – das ging schon vorher. Der eigentliche Engpass war der harte `127.0.0.1`-Bind:
ohne SSH-Tunnel kam ein Browser auf einem anderen Rechner gar nicht erst bis zum Server.

- **Umgesetzt:** `--bind ADRESSE` (nur `open`). Token-Schutz bleibt (neu bei jedem Start, siehe `App.token`); die
  Host-Header-Prüfung (`Handler._host_ok`) bleibt eng, solange auf `127.0.0.1`/`localhost` gebunden wird, und wird nur
  bei einer anderen Bind-Adresse auf eine Portprüfung gelockert (ein fester Hostname-Abgleich wäre bei `0.0.0.0`
  ohnehin nicht eindeutig – die Maschine kann mehrere Adressen haben). `--tui` und die normale Ausgabe zeigen dann
  dauerhaft/beim Start eine unübersehbare Warnung.
  **Bewusste Entscheidung, keine engere Zuordnung** (z. B. eine feste erlaubte Host-Liste aus allen Interface-Adressen):
  mehr Komplexität für einen Schutz, der bei `0.0.0.0` ohnehin nur einen Teil der Fälle abdecken könnte und leicht
  falsch-negativ ausfallen kann (falsche Schnittstelle geraten); der Token bleibt die tragende Kontrolle, transparent
  dokumentiert statt stillschweigend geschwächt.
- **Für die Anzeige:** `guess_lan_ip()` füllt bei `--bind 0.0.0.0` eine echte Adresse in die angezeigte URL statt der
  nutzlosen `0.0.0.0`.
  **Nachtrag (auf Wunsch verbessert):** zunaechst nur ein einzelner UDP-Verbindungsversuch (welche Route das
  Betriebssystem waehlen wuerde) - in einer Docker-Umgebung waere das oft die Docker-Bruecke, nicht das echte LAN.
  Jetzt werden alle Netzwerkschnittstellen (`socket.if_nameindex` + `SIOCGIFADDR`, nur Linux) bewertet: physische
  Interfaces mit privater LAN-Adresse (192.168.0.0/16, 10.0.0.0/8) gewinnen gegen Docker-/Bruecken-/VPN-Interfaces
  (am Namen erkannt: `docker`, `br-`, `veth`, `tun`, `wg`, ...) und deren ueblichen Adressbereich (172.16-31.0.0/12,
  Dockers Standard-Spielwiese); die Routenwahl bleibt als zusaetzlicher, leicht bevorzugter Kandidat, falls die
  Interface-Aufzaehlung nichts liefert (z. B. nicht Linux) oder mehrdeutig ist. Im Sandbox-Testnetz bestaetigt
  (`eth0` schlaegt simulierte `docker0`/`br-*`/`veth*`-Kandidaten klar). Bleibt ein Raten - bei mehreren echten
  LAN-Schnittstellen oder ungewoehnlicher Netzwerktopologie kann die Adresse danebenliegen, dann muss sie von Hand
  ersetzt werden.
- **Getestet:** `tests/test_companion.py` `BindTest` (Standard bleibt eng; `0.0.0.0` fuellt eine erratene Adresse;
  die Bewertungsfunktion bevorzugt echte Interfaces gegenueber Docker/VPN, auch wenn die Routenwahl selbst auf ein
  Docker-Netz zeigt; ein echter Server auf `127.0.0.2` beweist per HTTP-Anfragen mit unterschiedlichen `Host`-Headern,
  dass der Hostname jetzt egal ist, der Port aber weiterhin geprüft wird und ein falscher Token weiterhin 403 gibt).
