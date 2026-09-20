# Roadmap: Trefferrate & Konfidenz-Kalibrierung

Stand 2026-09: die darktable-Integration (Erkennung -> Style-Anwendung,
Undo) funktioniert technisch zuverlässig. Das eigentliche Problem ist
jetzt fachlich, nicht technisch: die Trefferrate ist noch nicht gut genug,
und die Konfidenz lügt teilweise - mehrere als **grün** (== "sicher, wende
automatisch an") markierte Ergebnisse waren laut Nutzer stark falsch. Ein
Konfidenzwert, dem man nicht vertrauen kann, ist schlimmer als gar keiner,
weil er falsches Vertrauen erzeugt. Diese Roadmap sortiert die Ursachen
und schlägt eine Reihenfolge vor, in der man das angeht.

## Stand der Umsetzung (2026-09-20)

| Phase | Erledigt | Offen |
| --- | --- | --- |
| 0 Fehlerfälle einsammeln | Werkzeug (Web-UI, `feedback_report.py`), Muster der 5 falschen Grünen | Ground Truth nach `review_data/`, Film 33/34 labeln, Baseline neu messen |
| 1 Ground Truth verbreitern | – | mehr Rollen/Formate, Tuning/Holdout, Leave-One-Film-Out |
| 2 Zweites Signal | Teilansatz (Helligkeit innen/außen) | Varianz/Histogramm, Perforation, unabhängige zweite Kantenmethode |
| 3 `film_trust` | – | Plausibilität des Konsens, Position im Cross-Film-Clamp |
| 4 Neu kalibrieren | – | LR mit Leave-One-Film-Out, Schwellen aus Holdout-ROC |
| 5 Feedback-Schleife | Gegenmelden (Web-UI), Faktoren sichtbar, Auswertung | Feedback in `eval.py`/`calibrate.py` einspeisen |

Priorität bleibt: Phase 0 abschließen (Daten), dann Phase 2.

## Praxistest der Companion-UI (2026-09-20)

Erster Durchlauf in einer echten darktable-Bibliothek: **36 Raw-Fotos** wurden
importiert, analysiert, in der Web-UI eingestellt und korrigiert und dann an darktable
übergeben, **Crops und Farblabels**. Auch das **nochmalige Korrigieren** (Zurück zur
Prüfung, zweite Revision, erneutes Anwenden) hat funktioniert.

Bestätigt damit (vorher offene Integrationsrisiken, siehe `companion-ui-plan.md`
Abschnitt 13): Export aus darktable, Übergabe per `plan.json`, Anwenden über
`dt.styles` in der GUI, Differenz-Anwenden nach Revision 2, Stopp des Servers
(Knopf und automatisch beim Schließen von darktable).

Folgen für diese Roadmap:
- Die **technische** Integration ist kein Engpass mehr; die fachlichen Phasen unten
  (Trefferrate, Konfidenz) bleiben der Kern.
- Erstmals gibt es **Ground Truth aus echter Nutzung** auf einer bisher unbekannten Rolle
  (Phase 0/1/5): Die Sitzung schreibt jede Korrektur nach `feedback.jsonl`
  (Crop-Korrekturen, Gruppenwechsel, mit erkanntem Crop und Konfidenz). Beim Test
  fielen je Sitzung etwa fünf manuelle Crops und rund zwanzig Gruppenwechsel an.
  **Achtung:** Sitzungen werden nach 14 Tagen aus `~/.cache/auto-crop-negative/`
  gelöscht. Vorher die wertvollen `feedback.jsonl`/`state.json` in
  `review_data/` sichern oder nach `reviews.json` übernehmen.
- Phase 5 (Feedback-Schleife): das Werkzeug steht, es fehlt die **Auswertung**
  (Feedback → `tools/eval.py`/`tools/calibrate.py`).

## Ausgangslage: warum "grün" trotzdem falsch sein kann

Die aktuelle Konfidenzformel (`apply_film_consensus()` in
`auto_crop_negative.py`):

```
conf = 0.45 * size_agree + 0.35 * edge_score + 0.20 * film_trust
conf *= exposure_factor
```

- **`size_agree`**: wie gut die Einzeldetektion zur Film-Konsens-Größe
  (Median über die Rolle) passt.
- **`film_trust`**: wie eng die Größen *innerhalb der Rolle* streuen, plus
  ob genug Bilder (>= 8) vorliegen.
- **`edge_score`**: wie steil/eindeutig die Kante war, die die lokale
  Konsens-Verfeinerung gefunden hat.
- **`exposure_factor`**: Deckel für unterbelichtete/kontrastarme Bilder.

Das Problem: **alle vier Faktoren sind Selbstbezug auf die eigene
Erkennung, kein Abgleich mit der Realität.**

- `size_agree`/`film_trust` messen nur, ob eine Rolle *in sich konsistent*
  ist. Wenn die Erkennung auf einer ganzen Rolle systematisch denselben
  Fehler macht (z. B. weil Lichtverhältnisse/Filmhalter/Nachbarframe auf
  dieser Rolle immer ähnlich aussehen), ist das Ergebnis maximal
  "einträchtig falsch" - genau das Muster, das hohe Konfidenz erzeugt.
- `edge_score` misst nur "war da eine scharfe Kante", nicht "war es die
  *richtige* Kante". Eine Perforation, ein Klebestreifen, eine
  Lichthof-Grenze oder der Rand des Filmhalters erzeugen ebenfalls scharfe
  Kanten.
- Es gibt aktuell **keine zweite, unabhängige Methode**, die zum Vergleich
  herangezogen wird. Pass A liefert nur das Seitenverhältnis, keine eigene
  Bounding-Box; Pass B und die Konsens-Verfeinerung sind im Kern dieselbe
  Kantensuche, nur mit unterschiedlichem Startpunkt.
- Die Schwellen (grün >= 0.50, gelb >= 0.30) und die Formel-Gewichte
  (0.45/0.35/0.20) wurden ausschließlich auf `review_data/reviews.json`
  kalibriert - **98 Bilder aus 6 Filmen (27-32)**. Die 100 % grün-Präzision
  in `tools/eval.py` gilt nur auf genau dieser Menge. Reale Rollen wie die
  vom Nutzer getestete "Film 35" sind darin nicht enthalten - die Formel
  wurde nie an unbekannten Rollen gegengeprüft.

Kurz: die Konfidenz ist aktuell ein Maß für "wie sehr stimmt die Rolle mit
sich selbst überein", nicht für "wie sehr stimmt der Crop mit der Realität
überein". Das erklärt das beobachtete Muster ziemlich präzise.

## Phase 0 (sofort, Voraussetzung für alles Weitere): Fehlerfälle einsammeln

Ohne die konkreten falschen grünen Ergebnisse zu kennen, ist jede weitere
Maßnahme Rätselraten.

- [ ] Die vom Nutzer beobachteten falschen grünen Crops identifizieren
      (Filmordner + Dateiname) und mit `start_review_gui.sh` den
      *tatsächlich richtigen* Crop von Hand eintragen - genau wie die
      bestehenden 98 Referenzbilder.
      **Stand (teilweise):** Auf der bisher unbekannten Rolle „Film 35“ sind über die Web-UI 5 falsche Grüne samt richtigem Crop erfasst (`tools/feedback_report.py`; Muster: 2× Position bei richtiger Größe, 2× Größe, 1× beides). Noch nicht nach `review_data/` übernommen (`--export-gt`); die ursprünglich beobachteten falschen Grünen sind nicht einzeln zugeordnet.
- [ ] Dabei **auch Film 33 und 34** vervollständigen (liegen schon als
      Testfotos vor, aber ohne Review-Daten - aktuell komplett ungenutzt
      für Kalibrierung/Auswertung).
      **Stand: offen.** Film 33 (37 Bilder) und 34 (28) haben weiter keine Review-Daten, Film 32 nur 1 von 9. Erfassbar mit `python -m companion serve --folder Testphotos …`.
- [ ] Für jeden neuen falschen Fall kurz notieren, *welches* der vier
      Symptome zutraf (Nachbarframe erwischt? Filmhalterkante statt
      Bildrand? Falsche Seite/Position bei richtiger Größe? Rolle mit
      wenigen Bildern?) - das entscheidet, welcher Punkt unten zuerst
      etwas bringt.
      **Stand (teilweise):** `feedback_report.py` unterscheidet automatisch Größe, Position und beides; Nachbarframe und Filmhalterkante bleiben Handarbeit am Bild.
- [ ] `tools/eval.py` und `tools/eval_baseline.json` mit den erweiterten
      Referenzdaten neu laufen lassen, um eine ehrliche (niedrigere)
      Ausgangs-Precision/Recall-Zahl zu bekommen, statt sich weiter auf
      die alten, zu optimistischen 93/98 zu verlassen.
      **Stand: offen.** `tools/eval_baseline.json` steht unverändert bei 93/98 (6 Filme).

## Phase 1: Ground Truth breiter aufstellen

Kalibrierung auf 6 Filmen von einer Kamera/einem Aufbau ist zu schmal, um
zu wissen, ob die Formel generalisiert.

- [ ] Ground Truth auf **mehr, unterschiedliche Rollen** ausweiten -
      idealerweise verschiedene Kameras, Belichtungssituationen,
      Filmformate (nicht nur Kleinbild), Farb- und S/W-Negative.
      **Stand: offen.** Bisher Kleinbild und ein Aufbau; Film 35 liegt nur als Feedback aus Sitzungen vor, nicht in der Kalibrierbasis.
- [ ] Datensatz in **Tuning-Set** und **Holdout-Set** aufteilen (z. B. pro
      Film, nicht zufällig gemischt - sonst leckt Wissen über eine Rolle
      zwischen Tuning und Test). Schwellen/Gewichte nur auf dem
      Tuning-Set anpassen, Erfolg nur auf dem Holdout-Set berichten.
      **Stand: offen.** Film 33/34 wären Holdout-Kandidaten, haben aber keine Labels; `eval.py --holdout` erzeugt nur einen Kontaktabzug.
- [ ] `tools/eval.py` um eine **Leave-One-Film-Out-Auswertung** erweitern:
      für jeden Film einmal so tun, als wäre er unbekannt (nicht Teil der
      film_trust/Cross-Film-Pools), und Precision/Recall nur auf diesem
      Film messen. Das deckt genau die Art von Überanpassung auf, die
      hier vermutlich vorliegt.
      **Stand: offen.** `tools/eval.py` hat keine Leave-One-Film-Out-Auswertung.

## Phase 2: Eine echte zweite Signalquelle einbauen

Der Kern des Problems ist der fehlende unabhängige Gegencheck. Ohne den
bleibt jede Gewichts-Feinjustierung Kosmetik auf derselben fehleranfälligen
Grundlage.

Kandidaten (nicht exklusiv, nach vermutetem Aufwand/Nutzen-Verhältnis
sortiert):

- [ ] **Inhalts-Plausibilität der Crop-Region.** Ein echtes Foto hat
      innerhalb des Rahmens typischerweise eine andere
      Helligkeits-/Varianzverteilung als Filmbasis, Sprocket-Löcher oder
      der Tisch drumherum (oft sehr gleichmäßig hell/dunkel oder mit
      regelmäßigem Muster). Ein einfacher Vergleich "Varianz/Histogramm
      innerhalb vs. direkt außerhalb des vorgeschlagenen Crops" ist
      billig zu berechnen und bestraft z. B. einen Crop, der zur Hälfte
      auf dem Filmträger sitzt, unabhängig davon, wie "einträchtig" die
      restliche Rolle ist.
      **Stand (teilweise):** `measure_crop_evidence()` vergleicht Helligkeit innen/außen. Kein Varianz-/Histogramm-Vergleich, und die Konsens-Konfidenz hat dafür keinen eigenen Faktor.
- [ ] **Perforation/Sprocket-Löcher als physische Referenz**, sofern auf
      den Aufnahmen sichtbar (je nach Digitalisier-Rig). Perforationen
      haben einen bekannten, extrem konstanten Rasterabstand - wenn
      erkennbar, ist das eine vom Bildinhalt komplett unabhängige
      Positionsreferenz und würde die "einträchtig falsch"-Schwäche
      strukturell auflösen. Aufwand deutlich höher, aber potenziell die
      robusteste Lösung.
      **Stand: offen.** Sprossenlöcher dienen nur zum Ausschließen in der Verfeinerung, nicht als Positionsreferenz.
- [ ] **Zweite, andersartige Kantendetektion** (z. B. ein anderer
      Gradient-/Schwellwert-Ansatz oder ein auf Kontrast statt Gradient
      basierendes Verfahren) und deren *Übereinstimmung* mit der
      bestehenden Methode als Signal nutzen, statt nur die Sicherheit der
      einen Methode zu betrachten. Zwei unabhängige Verfahren, die sich
      einig sind, sind ein deutlich stärkeres Signal als eine Methode, die
      sich selbst sehr sicher ist.
      **Stand: offen.** Die vier Detektoren werden zusammengeführt („n Strategien stimmen überein“), aber ein unabhängiges Zweitverfahren als Konfidenzfaktor fehlt.

## Phase 3: film_trust von "einträchtig" zu "einträchtig UND plausibel" machen

- [ ] `film_trust` sollte nicht nur geringe Streuung *innerhalb* der Rolle
      belohnen, sondern zusätzlich prüfen, ob der Rollen-Konsens selbst
      plausibel ist (z. B. via Phase-2-Signal, oder indem auffällt, wie
      viele Bilder der Rolle `size_ok=false` waren - viele Ausreißer
      deuten eher auf einen falschen Konsens als auf viele falsche
      Einzelbilder hin).
      **Stand: offen.** `film_trust` unverändert.
- [ ] Cross-Film-Clamp (aktuell nur "nach oben" gedeckelt, siehe
      `MIN_POOL_SUPPORT`/`CLAMP_TOL` in `apply_film_consensus()`) auf
      Plausibilität für *Position*, nicht nur Größe, prüfen - aktuell wird
      nur eine zu große Konsens-Größe korrigiert, eine falsche Position
      (z. B. konstant zu weit links) auf der ganzen Rolle würde nicht
      auffallen.
      **Stand: offen.** Der Cross-Film-Clamp (`CLAMP_TOL`) korrigiert weiter nur zu große Größen.

## Phase 4: Erst danach neu kalibrieren

`tools/calibrate.py` (logistische Regression) wurde bisher verworfen,
weil zu wenige Fehltreffer und zu schmale Datenbasis unzuverlässige,
teils falsch vorzeichnete Gewichte lieferten. Mit einem breiteren,
mehrere Filme/Kameras umfassenden Datensatz (Phase 1) und einem echten
zweiten Signal als zusätzlichem Feature (Phase 2) wird ein Neuversuch
sinnvoll:

- [ ] Logistische Regression erneut versuchen, diesmal mit
      Leave-One-Film-Out-Kreuzvalidierung statt einer einzigen
      Train/Test-Zahl, um Überanpassung sofort sichtbar zu machen.
      **Stand: offen** (setzt Phase 1 und 2 voraus).
- [ ] Schwellen (grün/gelb) danach separat aus der ROC-Kurve auf dem
      Holdout-Set ableiten, mit einer bewusst konservativen
      Ziel-Precision für grün (z. B. >= 98 % auf dem Holdout, nicht nur
      auf den Trainingsdaten).
      **Stand: offen** (setzt Phase 1 und 4a voraus).

## Phase 5: Feedback-Schleife aus echter Nutzung

Damit sich das Problem "grün war falsch" nicht wiederholt, ohne dass es
auffällt:

- [x] Einen leichten Weg schaffen, ein falsches automatisches Crop direkt
      aus darktable heraus gegenzumelden (z. B. ein Button "als falsch
      markieren", der Dateiname + berechnete Werte in eine Log-/Review-
      Datei schreibt) statt den Umweg über manuelles Wiederfinden im
      Review-GUI.
      **Erledigt (anders gelöst):** In der Web-UI genügt eine Korrektur oder die Rot-Markierung; jede Änderung geht mit erkanntem Crop und Konfidenz in `feedback.jsonl`, der Ordnermodus schreibt `reviews.json`. Kein Knopf in darktable nötig.
- [x] `_conf_parts` (bereits im Python-Code vorhanden) im Lua-Log oder in
      der GUI sichtbar machen, damit bei einem falschen grünen Ergebnis
      sofort erkennbar ist, welcher der vier Faktoren die Fehleinschätzung
      verursacht hat, ohne erst manuell nachrechnen zu müssen.
      **Erledigt:** Der Crop-Editor zeigt die vier Faktoren mit Balken und markiert den schwächsten; sie stehen auch im `feedback.jsonl`. Gilt für Analysen ab diesem Stand (ältere Sitzungen haben keine Faktoren gespeichert).

- [x] Auswertung des gesammelten Feedbacks: `tools/feedback_report.py` (Korrekturrate je
      Gruppe, falsches Grün mit Symptom, schwächster Faktor, Gruppenwechsel, Export als
      Ground Truth). Erster Lauf auf den 4 Testsitzungen (36 Bilder): 22 grün, davon
      5 korrigiert und alle 5 über der Toleranz, 6 gelb und 8 rot ohne Korrektur.
- [ ] Feedback in `tools/eval.py` und `tools/calibrate.py` einspeisen (Ground Truth aus
      `--export-gt` zusammen mit `reviews.json` auswerten; die Bilder liegen als Raw
      außerhalb von `Testphotos/`).

## Priorisierung

Phase 0 ist zwingend zuerst (ohne Daten kein Fortschritt). Danach hat
**Phase 2 (echtes zweites Signal)** den größten erwarteten Hebel, weil sie
die strukturelle Ursache angeht statt an Symptomen (Gewichte, Schwellen)
zu drehen - Phase 1, 3 und 4 sind ohne Phase 2 nur Feintuning auf einer
weiterhin fehleranfälligen Grundlage.
