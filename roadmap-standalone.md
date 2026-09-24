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

## Ist-Zustand: was schon unabhängig ist, was nicht

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

- [ ] Eigener Einstiegspunkt/Kurzanleitung für den reinen Ordnermodus
      (`python -m companion folder <pfad>`), unabhängig vom
      darktable-Installationsabschnitt in README.
- [ ] Prüfen, ob der Ordnermodus mit **bereits entwickelten/exportierten**
      Bildern (nicht nur RAW-Testfotos) aus beliebiger Quelle sauber läuft
      (Lightroom/Capture-One-JPEG-Export, Scanner-Software-Export).
- [ ] Anwenden im Ordnermodus: aktuell schreibt "Fertig" nur `reviews.json`/
      Referenzdaten für die Kalibrierung, aber keinen nutzbaren Crop auf ein
      reales Bild. Einen einfachen Output-Weg ergänzen (z. B. zugeschnittene
      Kopien schreiben, oder Crop-Koordinaten als generisches JSON/CSV
      neben den Bildern ablegen).
- [ ] README-Abschnitt "Ohne darktable nutzen" ergänzen.

## Phase 2: Output-Adapter abstrahieren

Der riskantere, aber wertvollere Schritt: das "Anwenden" von einer
darktable-Operation zu einer austauschbaren Aktion machen.

- [ ] Gemeinsame Schnittstelle definieren: `apply(image, crop, straighten_deg,
      group) -> ok/error`, die heutige darktable-Lua-Logik dahinter kapseln
      (kein Verhaltenswechsel für bestehende Nutzer).
- [ ] Adapter **"Sidecar generisch"**: schreibt Crop (+ optional Rotation)
      als eigenes, werkzeugneutrales XMP/JSON neben das Bild, ohne dt.styles.
      Deckt Nutzer ab, die ihre eigene Pipeline haben.
- [ ] Adapter **"Direkt zuschneiden"**: schreibt sofort zugeschnittene
      Ausgabedateien (für Nutzer ganz ohne RAW-Entwickler, z. B. reine
      Scan-Workflows mit JPEG/TIFF).
- [ ] Farblabel-Konzept von darktable lösen: intern bleibt es
      rot/gelb/grün (Konfidenzstufen), aber die *Anwendung* dieser Stufen
      wird adapterabhängig (darktable: Farblabel; generisch: z. B.
      Ordner-Sortierung `review/`, `ok/`, oder Suffix im Dateinamen).

## Phase 3: Input-Adapter für andere RAW-Entwickler

Erst nach Phase 2 sinnvoll, weil sonst nur die Erkennung, nicht der
Rückweg, an einem zweiten Werkzeug hinge.

- [ ] **RawTherapee**: RAW-Export via `rawtherapee-cli` (Analogon zu
      `darktable-cli`), Sidecar `.pp3` statt `.xmp` lesen/schreiben
      (Crop-Sektion `[Crop]`).
- [ ] **Lightroom / generisches XMP**: viele RAW-Entwickler benutzen
      dieselbe Adobe-XMP-Crop-Konvention (`crs:CropTop` etc.) – ein
      gemeinsamer XMP-Adapter deckt potenziell mehrere Werkzeuge ab, bevor
      werkzeugspezifische CLI-Exporte gebaut werden.
- [ ] **Capture One** (falls Nachfrage): eigenes Sidecar-Format, höherer
      Aufwand, niedrigere Priorität ohne konkreten Bedarf.
- [ ] Adapter-Erkennung: Companion soll aus dem Ordnerinhalt ableiten
      können, welcher Input-Adapter zuständig ist (vorhandene Sidecar-Typen),
      statt dass der Modus von Hand gewählt werden muss.

## Phase 4: Companion als eigenständiges Produkt

Erst wenn Phase 1–3 stehen, macht es Sinn, das Projekt auch *so zu
präsentieren* und zu benennen.

- [ ] Namensfrage klären: "Auto Crop Negative – Darktable-Plugin" impliziert
      im Titel bereits die Abhängigkeit. Entweder zwei Pakete (Kern +
      darktable-Plugin als eines von mehreren Frontends) oder ein
      Produktname, der die Erkennung in den Vordergrund stellt.
- [ ] README/Architektur-Doku umstrukturieren: Kernstück zuerst
      beschreiben (Erkennung + Companion), darktable als "Integration",
      gleichrangig neben Ordnermodus und ggf. RawTherapee.
      `auto_crop_negative.lua` bleibt bestehen, wird aber als *ein*
      Frontend dokumentiert statt als das Projekt selbst.
- [ ] Installationsweg ohne darktable: `install.sh` derzeit auf
      `~/.config/darktable/lua/` fixiert – ein zweiter, einfacherer Pfad nur
      für Companion (pip-Paket/venv, kein Lua-Schritt).
- [ ] Erwägen: Companion-Server dauerhaft im Hintergrund statt aus dem
      darktable-Panel gestartet (Watch-Ordner statt Import-Trigger) – nur
      falls das reine Ordner-Szenario genug Nutzung bekommt, um den Aufwand
      zu rechtfertigen.

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
